"""Polygon meshes for Hammer (CMapMesh / CDmePolygonMesh).

A Mesh is a list of vertices and polygonal faces.  Faces are wound
counter-clockwise when seen from outside (right-hand rule gives the outward
normal), which is how Hammer stores them.  `to_dmx()` turns the polygons into
Hammer's half-edge structure:

- half-edges come in opposite pairs (2k, 2k+1), one pair per undirected edge;
- `edgeVertexIndices[h]` is the vertex h points *to*; `edgeNextIndices[h]` is
  the next half-edge around the same face (or around the hole, for boundary
  half-edges whose face is -1);
- per-corner data (texcoord/normal/tangent) is stored per half-edge, for the
  corner at the half-edge's destination vertex.

The layout was taken from meshes in Valve's own maps and is checked by tests
(topology invariants) and by compiling maps with resourcecompiler.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from .dmx import Element

Vec3 = tuple[float, float, float]

DEFAULT_MATERIAL = 'materials/dev/reflectivity_50.vmat'
TOOL_MATERIALS = {
    'nodraw': 'materials/tools/toolsnodraw.vmat',
    'clip': 'materials/tools/toolsclip.vmat',
    'playerclip': 'materials/tools/toolsplayerclip.vmat',
    'trigger': 'materials/tools/toolstrigger.vmat',
    'skybox': 'materials/tools/toolsskybox.vmat',
    'invisible': 'materials/tools/toolsinvisible.vmat',
    'lightmapres': 'materials/tools/toolslightmapres.vmat',
}


class MeshError(ValueError):
    pass


def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _scale(a, s): return (a[0] * s, a[1] * s, a[2] * s)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    length = math.sqrt(_dot(a, a))
    if length < 1e-12:
        raise MeshError('degenerate face (zero area)')
    return _scale(a, 1.0 / length)


def _clean(v: float) -> float:
    """Snap tiny float noise so generated files are tidy (0.1 unit grid is not forced)."""
    r = round(v, 4)
    return 0.0 if r == 0 else r


@dataclass
class Face:
    verts: list[int]
    material: str
    u_axis: Vec3 | None = None     # texture axes; None = Hammer-style world alignment
    v_axis: Vec3 | None = None
    scale: tuple[float, float] = (0.25, 0.25)   # world units per texel
    offset: tuple[float, float] = (0.0, 0.0)    # texels
    colors: list | None = None     # vertex paint RGBA (0..1) per corner, in `verts` order


def world_axes(normal: Vec3) -> tuple[Vec3, Vec3]:
    """Hammer's default texture axes for a face normal (same as Valve's maps)."""
    ax, ay, az = (abs(c) for c in normal)
    if az >= ax and az >= ay:
        return (1.0, 0.0, 0.0), (0.0, -1.0, 0.0)
    if ax >= ay:
        return (0.0, 1.0, 0.0), (0.0, 0.0, -1.0)
    return (1.0, 0.0, 0.0), (0.0, 0.0, -1.0)


@dataclass
class Mesh:
    positions: list[Vec3] = field(default_factory=list)
    faces: list[Face] = field(default_factory=list)
    _lookup: dict = field(default_factory=dict, repr=False)

    # -- construction --------------------------------------------------------
    def vertex(self, p: Sequence[float]) -> int:
        """Index of a vertex at p (coincident points are shared)."""
        key = tuple(round(float(c), 3) for c in p)
        idx = self._lookup.get(key)
        if idx is None:
            idx = len(self.positions)
            self.positions.append(tuple(_clean(float(c)) for c in p))
            self._lookup[key] = idx
        return idx

    def face(self, points: Iterable[Sequence[float]], material: str = DEFAULT_MATERIAL, **uv) -> Face:
        """Add a polygon given its corner points, counter-clockwise from outside."""
        idx = []
        for p in points:
            i = self.vertex(p)
            if not idx or idx[-1] != i:
                idx.append(i)
        if len(idx) > 1 and idx[0] == idx[-1]:
            idx.pop()
        if len(idx) < 3 or len(set(idx)) != len(idx):
            raise MeshError(f'face needs >= 3 distinct corners, got {idx}')
        if uv.get('colors') is not None and len(uv['colors']) != len(idx):
            raise MeshError('face colors need one RGBA per corner')
        f = Face(idx, material, **uv)
        self.normal(f)   # raises on degenerate faces
        self.faces.append(f)
        return f

    def face_out(self, points: Sequence[Sequence[float]], outward: Sequence[float],
                 material: str = DEFAULT_MATERIAL, **uv) -> Face:
        """Add a planar polygon, choosing the winding so its normal points along `outward`."""
        pts = [tuple(float(c) for c in p) for p in points]
        n = [0.0, 0.0, 0.0]
        for i, a in enumerate(pts):
            b = pts[(i + 1) % len(pts)]
            n[0] += (a[1] - b[1]) * (a[2] + b[2])
            n[1] += (a[2] - b[2]) * (a[0] + b[0])
            n[2] += (a[0] - b[0]) * (a[1] + b[1])
        if _dot(tuple(n), tuple(outward)) < 0:
            pts.reverse()
            if uv.get('colors'):
                uv = {**uv, 'colors': list(reversed(uv['colors']))}
        return self.face(pts, material, **uv)

    def weld_t_junctions(self, eps: float = 1e-3) -> int:
        """Insert vertices lying on a face edge into that edge (removes cracks). Returns inserts."""
        count = 0
        pos = self.positions
        for f in self.faces:
            out = []
            n = len(f.verts)
            for i in range(n):
                a, b = f.verts[i], f.verts[(i + 1) % n]
                out.append(a)
                pa, d = pos[a], _sub(pos[b], pos[a])
                length2 = _dot(d, d)
                found = []
                for vi, p in enumerate(pos):
                    if vi == a or vi == b:
                        continue
                    t = _dot(_sub(p, pa), d) / length2
                    if 1e-6 < t < 1 - 1e-6:
                        q = _add(pa, _scale(d, t))
                        if _dot(_sub(p, q), _sub(p, q)) < eps * eps:
                            found.append((t, vi))
                out.extend(v for _, v in sorted(found))
                count += len(found)
            f.verts = out
        return count

    def normal(self, f: Face) -> Vec3:
        """Newell normal of a polygon (robust for non-triangles)."""
        n = [0.0, 0.0, 0.0]
        pts = [self.positions[i] for i in f.verts]
        for i, a in enumerate(pts):
            b = pts[(i + 1) % len(pts)]
            n[0] += (a[1] - b[1]) * (a[2] + b[2])
            n[1] += (a[2] - b[2]) * (a[0] + b[0])
            n[2] += (a[0] - b[0]) * (a[1] + b[1])
        return _norm(tuple(n))

    def extend(self, other: 'Mesh', offset: Vec3 = (0, 0, 0)) -> 'Mesh':
        for f in other.faces:
            self.face([_add(other.positions[i], offset) for i in f.verts], f.material, u_axis=f.u_axis,
                      v_axis=f.v_axis, scale=f.scale, offset=f.offset, colors=f.colors)
        return self

    def _paint_streams(self, dest: list[int], face_of: list[int]) -> list[Element]:
        """VertexPaintTintColor per half-edge when any face carries corner colors (alpha fades translucent
        materials such as light shafts); white elsewhere. Nothing is written for unpainted meshes."""
        if not any(f.colors for f in self.faces):
            return []
        tint = []
        for h, fi in enumerate(face_of):
            f = self.faces[fi] if fi != -1 else None
            if f is None or not f.colors:
                tint.append((1.0, 1.0, 1.0, 1.0))
            else:
                tint.append(tuple(_clean(c) for c in f.colors[f.verts.index(dest[h])]))
        return [_paint_stream('VertexPaintBlendParams', [(0.0, 0.0, 0.0, 0.0)] * len(dest)),
                _paint_stream('VertexPaintTintColor', tint)]

    def set_material(self, material: str) -> 'Mesh':
        for f in self.faces:
            f.material = material
        return self

    def bounds(self) -> tuple[Vec3, Vec3]:
        xs, ys, zs = zip(*self.positions)
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def centered(self) -> tuple['Mesh', Vec3]:
        """Copy of the mesh moved so its bounding-box centre is the origin; returns (mesh, centre)."""
        lo, hi = self.bounds()
        c = tuple(_clean((a + b) / 2) for a, b in zip(lo, hi))
        out = Mesh()
        out.extend(self, _scale(c, -1))
        return out, c

    # -- topology ------------------------------------------------------------
    def halfedges(self):
        """Build the half-edge arrays; raises MeshError on non-manifold input."""
        he_of: dict[tuple[int, int], int] = {}
        dest: list[int] = []
        face_of: list[int] = []
        nxt: list[int] = []
        face_edge: list[int] = []
        for fi, f in enumerate(self.faces):
            n = len(f.verts)
            loop = []
            for i in range(n):
                a, b = f.verts[i], f.verts[(i + 1) % n]
                h = he_of.get((a, b))
                if h is None:
                    k = len(dest) // 2
                    he_of[(a, b)], he_of[(b, a)] = 2 * k, 2 * k + 1
                    dest += [b, a]
                    face_of += [-1, -1]
                    nxt += [-1, -1]
                    h = 2 * k
                if face_of[h] != -1:
                    raise MeshError(f'edge {a}->{b} is used twice in the same direction '
                                    '(flipped face or more than two faces on one edge)')
                face_of[h] = fi
                loop.append(h)
            for i, h in enumerate(loop):
                nxt[h] = loop[(i + 1) % n]
            face_edge.append(loop[-1])
        # boundary half-edges (face -1) are chained around each hole
        start = [0] * len(dest)
        for h in range(len(dest)):
            start[h] = dest[h ^ 1]
        boundary_from: dict[int, int] = {}
        for h in range(len(dest)):
            if face_of[h] == -1:
                if start[h] in boundary_from:
                    raise MeshError(f'vertex {start[h]} touches two separate holes (non-manifold vertex)')
                boundary_from[start[h]] = h
        for h, v in ((h, dest[h]) for h in range(len(dest)) if face_of[h] == -1):
            nxt[h] = boundary_from[v]
        vertex_edge = [-1] * len(self.positions)
        for h in range(len(dest)):
            if vertex_edge[start[h]] == -1:
                vertex_edge[start[h]] = h
        if -1 in vertex_edge:
            raise MeshError('mesh has vertices that are not used by any face')
        return dest, face_of, nxt, face_edge, vertex_edge

    # -- DMX -----------------------------------------------------------------
    def to_dmx(self, origin: Vec3 = (0.0, 0.0, 0.0),
               texture_size: tuple[int, int] | Callable[[str], tuple[int, int]] = (512, 512),
               smoothing: float = 0.0) -> Element:
        """Return a CDmePolygonMesh element.

        `origin` is the CMapMesh origin (UVs are world-aligned like Hammer's).
        `texture_size` is (w, h) in pixels or a function material -> (w, h).
        `smoothing` (degrees, the node's smoothingAngle): corners shared by faces less than this apart get one
        averaged normal, like Hammer; 0 keeps flat face normals.
        """
        if not self.faces:
            raise MeshError('mesh has no faces')
        dest, face_of, nxt, face_edge, vertex_edge = self.halfedges()
        materials: list[str] = []
        for f in self.faces:
            if f.material not in materials:
                materials.append(f.material)
        size_of = texture_size if callable(texture_size) else (lambda _m: texture_size)
        sizes = {m: size_of(m) for m in materials}
        normals, tangents, axes = [], [], []
        for f in self.faces:
            n = self.normal(f)
            u, v = (f.u_axis, f.v_axis) if f.u_axis and f.v_axis else world_axes(n)
            u, v = _norm(u), _norm(v)
            t = _sub(u, _scale(n, _dot(u, n)))
            t = _norm(t) if _dot(t, t) > 1e-12 else _norm(_cross(v, n))
            w = -1.0 if _dot(_cross(n, t), v) < 0 else 1.0
            normals.append(n)
            tangents.append((t[0], t[1], t[2], w))
            axes.append((u, v))
        texcoord, fv_normal, fv_tangent = [], [], []
        for h in range(len(dest)):
            fi = face_of[h]
            if fi == -1:
                texcoord.append((0.0, 0.0))
                fv_normal.append((0.0, 0.0, 1.0))
                fv_tangent.append((1.0, 0.0, 0.0, 1.0))
                continue
            f = self.faces[fi]
            u, v = axes[fi]
            tw, th = sizes[f.material]
            p = _add(self.positions[dest[h]], origin)
            texcoord.append((_clean((_dot(p, u) / f.scale[0] + f.offset[0]) / tw),
                             _clean((_dot(p, v) / f.scale[1] + f.offset[1]) / th)))
            fv_normal.append(tuple(_clean(c) for c in normals[fi]))
            fv_tangent.append(tuple(_clean(c) for c in tangents[fi]))
        n_he, n_edges, n_faces = len(dest), len(dest) // 2, len(self.faces)

        mesh = Element('CDmePolygonMesh', 'meshData')
        mesh.set('vertexEdgeIndices', 'int_array', vertex_edge)
        mesh.set('vertexDataIndices', 'int_array', list(range(len(self.positions))))
        mesh.set('edgeVertexIndices', 'int_array', dest)
        mesh.set('edgeOppositeIndices', 'int_array', [h ^ 1 for h in range(n_he)])
        mesh.set('edgeNextIndices', 'int_array', nxt)
        mesh.set('edgeFaceIndices', 'int_array', face_of)
        mesh.set('edgeDataIndices', 'int_array', [h // 2 for h in range(n_he)])
        mesh.set('edgeVertexDataIndices', 'int_array', list(range(n_he)))
        mesh.set('faceEdgeIndices', 'int_array', face_edge)
        mesh.set('faceDataIndices', 'int_array', list(range(n_faces)))
        mesh.set('materials', 'string_array', materials)
        mesh.set('vertexData', 'element', _data_array(len(self.positions), [
            _stream('position', 3, 'vector3_array', list(self.positions))]))
        mesh.set('faceVertexData', 'element', _data_array(n_he, [
            _stream('texcoord', 1, 'vector2_array', texcoord),
            _stream('normal', 1, 'vector3_array', fv_normal),
            _stream('tangent', 1, 'vector4_array', fv_tangent), *self._paint_streams(dest, face_of)]))
        mesh.set('edgeData', 'element', _data_array(n_edges, [_stream('flags', 3, 'int_array', [0] * n_edges)]))
        mesh.set('faceData', 'element', _data_array(n_faces, [
            _stream('textureScale', 0, 'vector2_array', [tuple(f.scale) for f in self.faces]),
            _stream('textureAxisU', 0, 'vector4_array', [(*a[0], f.offset[0]) for a, f in zip(axes, self.faces)]),
            _stream('textureAxisV', 0, 'vector4_array', [(*a[1], f.offset[1]) for a, f in zip(axes, self.faces)]),
            _stream('materialindex', 8, 'int_array', [materials.index(f.material) for f in self.faces]),
            _stream('flags', 3, 'int_array', [0] * n_faces),
            _stream('lightmapScaleBias', 1, 'int_array', [0] * n_faces)]))
        if smoothing > 0:
            smooth_normals(mesh, smoothing)
        sub = Element('CDmePolygonMeshSubdivisionData', '')
        sub.set('subdivisionLevels', 'int_array', [0] * n_he)
        sub.set('streams', 'element_array', [])
        mesh.set('subdivisionData', 'element', sub)
        return mesh


def _streams(array: Element) -> dict:
    return {st.get('semanticName', st.get('standardAttributeName')): st for st in array['streams']}


def smooth_normals(md: Element, angle: float) -> int:
    """Hammer-style smoothing of a CDmePolygonMesh in place: each corner's normal becomes the angle-weighted mean
    of the normals of faces around its vertex that are within `angle` degrees of the corner's own face; tangents
    are re-orthogonalised. Hard edges (sharper than `angle`) stay crisp. Returns the corners changed."""
    dest, nxt, face = md['edgeVertexIndices'], md['edgeNextIndices'], md['edgeFaceIndices']
    vdata = md['vertexDataIndices']
    pos = _streams(md['vertexData'])['position']['data']
    fv = _streams(md['faceVertexData'])
    if 'normal' not in fv:
        return 0
    normals, tangents = fv['normal']['data'], fv.get('tangent', {}).get('data') if fv.get('tangent') else None
    corner = md['edgeVertexDataIndices']
    p = [pos[vdata[v]] for v in range(len(md['vertexEdgeIndices']))]
    prev = [0] * len(dest)
    for h, n in enumerate(nxt):
        prev[n] = h
    face_n = {}
    for f, h0 in enumerate(md['faceEdgeIndices']):
        acc = [0.0, 0.0, 0.0]
        h = h0
        while True:
            a, b = p[dest[prev[h]]], p[dest[h]]
            acc[0] += (a[1] - b[1]) * (a[2] + b[2])
            acc[1] += (a[2] - b[2]) * (a[0] + b[0])
            acc[2] += (a[0] - b[0]) * (a[1] + b[1])
            h = nxt[h]
            if h == h0:
                break
        length = math.sqrt(acc[0] ** 2 + acc[1] ** 2 + acc[2] ** 2)
        face_n[f] = tuple(c / length for c in acc) if length > 1e-12 else (0.0, 0.0, 1.0)
    around: dict[int, list] = {}
    for h, f in enumerate(face):
        if f < 0:
            continue
        v = dest[h]
        a, b = _sub(p[dest[prev[h]]], p[v]), _sub(p[dest[nxt[h]]], p[v])
        la, lb = math.sqrt(_dot(a, a)), math.sqrt(_dot(b, b))
        w = math.acos(max(-1.0, min(1.0, _dot(a, b) / (la * lb)))) if la > 1e-9 and lb > 1e-9 else 0.0
        around.setdefault(v, []).append((h, f, w))
    limit = math.cos(math.radians(angle)) - 1e-6
    changed = 0
    for v, uses in around.items():
        for h, f, _ in uses:
            nf = face_n[f]
            acc = (0.0, 0.0, 0.0)
            for _, g, w in uses:
                if _dot(face_n[g], nf) >= limit:
                    acc = _add(acc, _scale(face_n[g], max(w, 1e-4)))
            n = _norm(acc) if _dot(acc, acc) > 1e-18 else nf
            if _dot(n, nf) > 1 - 1e-9:
                continue
            ci = corner[h]
            normals[ci] = tuple(_clean(c) for c in n)
            if tangents is not None:
                t = tangents[ci]
                tt = _sub(t[:3], _scale(n, _dot(t[:3], n)))
                if _dot(tt, tt) > 1e-12:
                    tangents[ci] = (*(_clean(c) for c in _norm(tt)), t[3])
            changed += 1
    return changed


def _paint_stream(name: str, data: list) -> Element:
    """Hammer's vertex paint stream (faceVertexData, second vertex buffer), as in Valve's maps."""
    s = _stream(name, 1, 'vector4_array', data)
    s.set('standardAttributeName', 'string', '')
    s.set('vertexBufferLocation', 'int', 1)
    return s


def _stream(name: str, flags: int, type_: str, data: list) -> Element:
    s = Element('CDmePolygonMeshDataStream', f'{name}:0')
    s.set('standardAttributeName', 'string', name)
    s.set('semanticName', 'string', name)
    s.set('semanticIndex', 'int', 0)
    s.set('vertexBufferLocation', 'int', 0)
    s.set('dataStateFlags', 'int', flags)
    s.set('subdivisionBinding', 'element', None)
    s.set('data', type_, data)
    return s


def _data_array(size: int, streams: list[Element]) -> Element:
    a = Element('CDmePolygonMeshDataArray', '')
    a.set('size', 'int', size)
    a.set('streams', 'element_array', streams)
    return a




# ---------------------------------------------------------------------------
# primitives (all coordinates are local to the mesh origin)

_AXES = {'z': (0, 1, 2), 'x': (1, 2, 0), 'y': (2, 0, 1)}   # (u, v, extrude) right-handed


def _polygon_area(poly: Sequence[Sequence[float]]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, list(poly[1:]) + [poly[0]])) / 2


def prism(polygon: Sequence[Sequence[float]], start: float, end: float, axis: str = 'z',
          material: str = DEFAULT_MATERIAL, materials: dict[str, str] | None = None) -> Mesh:
    """Extrude a convex 2D polygon along an axis (x, y or z) from `start` to `end`.

    For axis 'z' the polygon is in (x, y); for 'x' it is in (y, z); for 'y' in (z, x).
    `materials` may override 'start', 'end' or 'sides'.  Either winding is accepted.
    """
    if axis not in _AXES:
        raise MeshError("axis must be 'x', 'y' or 'z'")
    if end <= start:
        raise MeshError('prism needs end > start')
    poly = [(float(p[0]), float(p[1])) for p in polygon]
    if len(poly) < 3:
        raise MeshError('polygon needs at least 3 points')
    if abs(_polygon_area(poly)) < 1e-6:
        raise MeshError('polygon has zero area')
    if _polygon_area(poly) < 0:
        poly.reverse()
    ui, vi, wi = _AXES[axis]
    mats = {'start': material, 'end': material, 'sides': material, **(materials or {})}

    def point(p, w):
        out = [0.0, 0.0, 0.0]
        out[ui], out[vi], out[wi] = p[0], p[1], w
        return tuple(out)

    m = Mesh()
    m.face([point(p, end) for p in poly], mats['end'])
    m.face([point(p, start) for p in reversed(poly)], mats['start'])
    for i, a in enumerate(poly):
        b = poly[(i + 1) % len(poly)]
        m.face([point(b, end), point(a, end), point(a, start), point(b, start)], mats['sides'])
    return m


def box(mins: Sequence[float], maxs: Sequence[float], material: str = DEFAULT_MATERIAL,
        materials: dict[str, str] | None = None) -> Mesh:
    """Axis-aligned box.  `materials` may override 'top', 'bottom' or 'sides'."""
    (x0, y0, z0), (x1, y1, z1) = mins, maxs
    if not (x1 > x0 and y1 > y0 and z1 > z0):
        raise MeshError(f'box needs maxs > mins on every axis, got {list(mins)} {list(maxs)}')
    mats = materials or {}
    return prism([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], z0, z1, 'z', material,
                 {'end': mats.get('top', material), 'start': mats.get('bottom', material),
                  'sides': mats.get('sides', material)})


def wedge(mins: Sequence[float], maxs: Sequence[float], rise: str = '+x', material: str = DEFAULT_MATERIAL) -> Mesh:
    """Ramp filling the box mins..maxs, rising towards `rise` (+x, -x, +y, -y)."""
    (x0, y0, z0), (x1, y1, z1) = mins, maxs
    if rise in ('+x', '-x'):          # triangle in (z, x), extruded along y
        tri = [(z0, x0), (z0, x1), (z1, x1)] if rise == '+x' else [(z0, x0), (z0, x1), (z1, x0)]
        return prism(tri, y0, y1, 'y', material)
    if rise in ('+y', '-y'):          # triangle in (y, z), extruded along x
        tri = [(y0, z0), (y1, z0), (y1, z1)] if rise == '+y' else [(y0, z0), (y1, z0), (y0, z1)]
        return prism(tri, x0, x1, 'x', material)
    raise MeshError("rise must be '+x', '-x', '+y' or '-y'")


def cylinder(center: Sequence[float], radius: float, z0: float, z1: float, sides: int = 16,
             material: str = DEFAULT_MATERIAL) -> Mesh:
    if not 3 <= sides <= 128:
        raise MeshError('sides must be 3..128')
    if radius <= 0:
        raise MeshError('radius must be > 0')
    cx, cy = center[0], center[1]
    poly = [(cx + radius * math.cos(2 * math.pi * i / sides), cy + radius * math.sin(2 * math.pi * i / sides))
            for i in range(sides)]
    return prism(poly, z0, z1, 'z', material)


def stairs(mins: Sequence[float], maxs: Sequence[float], steps: int, rise: str = '+x',
           material: str = DEFAULT_MATERIAL) -> Mesh:
    """Solid staircase filling mins..maxs, climbing towards `rise`; one closed mesh."""
    if not 1 <= steps <= 64:
        raise MeshError('steps must be 1..64')
    (x0, y0, z0), (x1, y1, z1) = mins, maxs
    if rise not in ('+x', '-x', '+y', '-y'):
        raise MeshError("rise must be '+x', '-x', '+y' or '-y'")
    along_x = rise in ('+x', '-x')
    a0, a1 = (x0, x1) if along_x else (y0, y1)
    run, height = (a1 - a0) / steps, (z1 - z0) / steps
    # staircase outline in (along, z), climbing towards +along
    profile = [(a0, z0), (a1, z0)]
    for i in range(steps, 0, -1):
        profile += [(a0 + i * run, z0 + i * height), (a0 + (i - 1) * run, z0 + i * height)]
    if rise in ('-x', '-y'):
        profile = [(a0 + a1 - a, z) for a, z in profile]
    return profile_solid(profile, (y0, y1) if along_x else (x0, x1), along_x, material)


def profile_solid(profile: Sequence[Sequence[float]], span: tuple[float, float], along_x: bool,
                  material: str = DEFAULT_MATERIAL) -> Mesh:
    """Extrude an outline given in (along, z) across `span` (y range if along_x else x range).

    The outline may be non-convex as long as every vertical line crosses it at
    most twice (stairs, arches cut from below are not supported).  The two big
    side caps are split into convex vertical strips, and T-junctions are welded
    so the result is a closed, watertight mesh.
    """
    prof = [(float(a), float(z)) for a, z in profile]
    if _polygon_area(prof) < 0:
        prof.reverse()
    s0, s1 = span
    if s1 <= s0:
        raise MeshError('span must be increasing')

    def pt(a, z, s):
        return (a, s, z) if along_x else (s, a, z)

    def side_normal(da, dz):   # outward normal (right of a CCW edge) in 3D
        na, nz = dz, -da
        return (na, 0.0, nz) if along_x else (0.0, na, nz)

    m = Mesh()
    n = len(prof)
    for i in range(n):
        (a0, z0), (a1, z1) = prof[i], prof[(i + 1) % n]
        m.face_out([pt(a0, z0, s0), pt(a1, z1, s0), pt(a1, z1, s1), pt(a0, z0, s1)],
                   side_normal(a1 - a0, z1 - z0), material)
    cuts = sorted({round(a, 4) for a, _ in prof})
    for left, right in zip(cuts, cuts[1:]):
        mid = (left + right) / 2
        edges = []
        for i in range(n):
            (a0, z0), (a1, z1) = prof[i], prof[(i + 1) % n]
            if (a0 - mid) * (a1 - mid) < 0:
                edges.append(((a0, z0), (a1, z1)))
        if len(edges) != 2:
            raise MeshError('outline must be crossed exactly twice by every vertical line')

        def z_at(edge, a):
            (a0, z0), (a1, z1) = edge
            return z0 + (a - a0) / (a1 - a0) * (z1 - z0)

        lo, hi = sorted(edges, key=lambda e: z_at(e, mid))
        strip = [(left, z_at(lo, left)), (right, z_at(lo, right)), (right, z_at(hi, right)), (left, z_at(hi, left))]
        outward_s0 = (0.0, -1.0, 0.0) if along_x else (-1.0, 0.0, 0.0)
        outward_s1 = (0.0, 1.0, 0.0) if along_x else (1.0, 0.0, 0.0)
        m.face_out([pt(a, z, s0) for a, z in strip], outward_s0, material)
        m.face_out([pt(a, z, s1) for a, z in strip], outward_s1, material)
    m.weld_t_junctions()
    return m


def region_solid(polygons: Sequence[Sequence[Sequence[float]]], span: tuple[float, float], along_x: bool,
                 material: str = DEFAULT_MATERIAL) -> Mesh:
    """Extrude a flat region made of convex 2D pieces (in (along, z)) across `span`.

    The pieces must not overlap; touching pieces merge into one closed solid
    (the region may have holes, e.g. a wall with an arched doorway).
    """
    def key(p):
        return (round(p[0], 3), round(p[1], 3))

    polys = []
    for poly in polygons:
        pts = [(float(a), float(z)) for a, z in poly]
        if abs(_polygon_area(pts)) < 1e-6:
            raise MeshError('region piece has zero area')
        polys.append(pts if _polygon_area(pts) > 0 else pts[::-1])
    points = {key(p): p for poly in polys for p in poly}
    split = []
    for poly in polys:   # insert other pieces' corners lying on this piece's edges
        out = []
        for i, a in enumerate(poly):
            b = poly[(i + 1) % len(poly)]
            out.append(a)
            da, dz = b[0] - a[0], b[1] - a[1]
            length2 = da * da + dz * dz
            on_edge = []
            for p in points.values():
                t = ((p[0] - a[0]) * da + (p[1] - a[1]) * dz) / length2
                if 1e-6 < t < 1 - 1e-6:
                    qa, qz = a[0] + t * da, a[1] + t * dz
                    if (p[0] - qa) ** 2 + (p[1] - qz) ** 2 < 1e-6:
                        on_edge.append((t, p))
            out += [p for _, p in sorted(on_edge)]
        split.append(out)
    directed = set()
    for poly in split:
        for i, a in enumerate(poly):
            directed.add((key(a), key(poly[(i + 1) % len(poly)])))
    s0, s1 = span

    def pt(a, z, s):
        return (a, s, z) if along_x else (s, a, z)

    m = Mesh()
    neg = (0.0, -1.0, 0.0) if along_x else (-1.0, 0.0, 0.0)
    pos = (0.0, 1.0, 0.0) if along_x else (1.0, 0.0, 0.0)
    for poly in split:
        m.face_out([pt(a, z, s0) for a, z in poly], neg, material)
        m.face_out([pt(a, z, s1) for a, z in poly], pos, material)
        for i, a in enumerate(poly):
            b = poly[(i + 1) % len(poly)]
            if (key(b), key(a)) in directed:
                continue   # interior edge shared with a neighbouring piece
            na, nz = b[1] - a[1], -(b[0] - a[0])
            outward = (na, 0.0, nz) if along_x else (0.0, na, nz)
            m.face_out([pt(a[0], a[1], s0), pt(b[0], b[1], s0), pt(b[0], b[1], s1), pt(a[0], a[1], s1)],
                       outward, material)
    m.weld_t_junctions()
    return m


def heightfield(mins: Sequence[float], maxs: Sequence[float], cells: tuple[int, int],
                height: Callable[[float, float], float], material: str = DEFAULT_MATERIAL,
                side_material: str | None = None, thickness: float = 16.0) -> Mesh:
    """Terrain slab over mins..maxs (x, y).  Top surface z = maxs.z + height(x, y) on a
    grid of cells; flat bottom `thickness` below the lowest point; closed sides."""
    nx, ny = cells
    if not (1 <= nx <= 96 and 1 <= ny <= 96):
        raise MeshError('cells must be 1..96 each')
    (x0, y0, _), (x1, y1, ztop) = mins, maxs
    if not (x1 > x0 and y1 > y0):
        raise MeshError('heightfield needs maxs > mins in x and y')
    side = side_material or material
    gx = [x0 + (x1 - x0) * i / nx for i in range(nx + 1)]
    gy = [y0 + (y1 - y0) * j / ny for j in range(ny + 1)]
    z = [[ztop + float(height(x, y)) for y in gy] for x in gx]
    zb = min(min(col) for col in z) - thickness
    m = Mesh()
    for i in range(nx):
        for j in range(ny):
            p00, p10 = (gx[i], gy[j], z[i][j]), (gx[i + 1], gy[j], z[i + 1][j])
            p11, p01 = (gx[i + 1], gy[j + 1], z[i + 1][j + 1]), (gx[i], gy[j + 1], z[i][j + 1])
            if abs(p00[2] + p11[2] - p10[2] - p01[2]) < 1e-3:
                m.face([p00, p10, p11, p01], material)
            else:
                m.face([p00, p10, p11], material)
                m.face([p00, p11, p01], material)
    for i in range(nx):   # -y and +y walls
        m.face_out([(gx[i], y0, z[i][0]), (gx[i + 1], y0, z[i + 1][0]), (gx[i + 1], y0, zb), (gx[i], y0, zb)],
                   (0, -1, 0), side)
        m.face_out([(gx[i], y1, z[i][ny]), (gx[i + 1], y1, z[i + 1][ny]), (gx[i + 1], y1, zb), (gx[i], y1, zb)],
                   (0, 1, 0), side)
    for j in range(ny):   # -x and +x walls
        m.face_out([(x0, gy[j], z[0][j]), (x0, gy[j + 1], z[0][j + 1]), (x0, gy[j + 1], zb), (x0, gy[j], zb)],
                   (-1, 0, 0), side)
        m.face_out([(x1, gy[j], z[nx][j]), (x1, gy[j + 1], z[nx][j + 1]), (x1, gy[j + 1], zb), (x1, gy[j], zb)],
                   (1, 0, 0), side)
    bottom = ([(x, y0, zb) for x in gx] + [(x1, y, zb) for y in gy[1:]] +
              [(x, y1, zb) for x in reversed(gx[:-1])] + [(x0, y, zb) for y in reversed(gy[1:-1])])
    m.face_out(bottom, (0, 0, -1), side)
    return m
