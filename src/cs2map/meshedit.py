"""edit_mesh: change an existing mesh piece by piece, keeping it closed and textured.

The mesh is decoded into polygons with per-corner attributes (meshops.decode_node), edited by a list of steps
and encoded back once (meshops.encode_polygons validates closure, manifold edges, self-intersection and
winding), so a failing step changes nothing. Vertices are identified by position (0.001 grid), which is what
the encoder welds, so moving "a vertex" moves every corner that shares it.

Steps ({"do": <name>, ...}):
  move       verts|faces, offset, scale, rotate {axis, angle}, pivot
  extrude    faces, distance (along the region normal) or direction [x,y,z]
  inset      faces, width, depth (each face gets a frame; depth pushes the inner face out/in)
  bevel      edges, width, segments (convex edges; cut by local prisms, ends mitre like Hammer's chamfer)
  subdivide  faces, cell, axes ("xyz" subset): world-grid split so bends and noise have vertices to move
  bend       axis, toward, angle, from/to, center      twist  axis, angle, center, from/to
  taper      axis, scale (number or [a, b]), center, from/to
  noise      amount, radius, seed, along ("normal" | "xyz")
  mirror     axis, at, keep ("+" | "-"): keep one half and mirror it onto the other
  weld       distance            smooth  iterations, strength
Selections: faces = all|top|bottom|sides|±x|±y|±z, indices, {mins,maxs} (centroids), {plane:{point,normal}},
{facing:[x,y,z], within:deg}, "last" (what the previous step made); verts = all, {mins,maxs},
{plane:{...}}, {faces: <faces>}, [[x,y,z], ...] (nearest vertices); edges = sharp | a face selector |
{mins,maxs} (edge midpoints) | {faces: <faces>} | [[[x,y,z],[x,y,z]], ...].
"""
from __future__ import annotations

import math

from . import meshops as g
from .mesh import Mesh, MeshError, _add, _cross, _dot, _norm, _scale, _sub, world_axes

GRID = 3                      # decimals of the vertex identity grid (the encoder welds at 0.001)
PLANAR_TOL = 0.02             # units a deformed face may bend before it is split into triangles
MAX_STEPS = 32
MAX_CUTS = 160                # bevel prisms per operation (edges x segments)
STEP_KEYS = {
    'move': {'verts', 'faces', 'offset', 'scale', 'rotate', 'pivot'},
    'extrude': {'faces', 'distance', 'direction'},
    'inset': {'faces', 'width', 'depth'},
    'bevel': {'edges', 'width', 'segments', 'angle', 'material'},
    'subdivide': {'faces', 'cell', 'axes'},
    'bend': {'verts', 'axis', 'toward', 'angle', 'from', 'to', 'center'},
    'twist': {'verts', 'axis', 'angle', 'from', 'to', 'center'},
    'taper': {'verts', 'axis', 'scale', 'from', 'to', 'center'},
    'noise': {'verts', 'amount', 'radius', 'seed', 'along'},
    'mirror': {'axis', 'at', 'keep'},
    'weld': {'distance'},
    'smooth': {'verts', 'iterations', 'strength'},
}
AXES = {'x': (1., 0., 0.), 'y': (0., 1., 0.), 'z': (0., 0., 1.)}
DIRS = {'+x': (1., 0., 0.), '-x': (-1., 0., 0.), '+y': (0., 1., 0.), '-y': (0., -1., 0.),
        '+z': (0., 0., 1.), '-z': (0., 0., -1.)}


def _keys(spec, allowed, what):
    if not isinstance(spec, dict):
        raise MeshError(f'{what} must be an object')
    extra = set(spec) - set(allowed)
    if extra:
        raise MeshError(f'{what}: unknown fields {sorted(extra)}; allowed: {", ".join(sorted(allowed))}')


def key(p) -> tuple:
    return tuple(round(c, GRID) + 0.0 for c in p)


def centroid(poly) -> tuple:
    n = len(poly.corners)
    return tuple(sum(v.p[k] for v in poly.corners) / n for k in range(3))


def _axis(value, label='axis'):
    if isinstance(value, str) and value in AXES:
        return AXES[value]
    if isinstance(value, str) and value in DIRS:
        return DIRS[value]
    v = g.vector(value) if isinstance(value, (list, tuple)) else None
    if v is None or _dot(v, v) < 1e-12:
        raise MeshError(f'{label} must be x|y|z, +x|-y|..., or a vector [x,y,z]')
    return _norm(v)


# ---- selection -----------------------------------------------------------------------------------------
def select_faces(polys, sel, last):
    if sel == 'last':
        if not last:
            raise MeshError('faces:"last" needs a previous step that made faces (inset, extrude, subdivide)')
        return set(last)
    if isinstance(sel, dict) and 'plane' in sel:
        _keys(sel, {'plane'}, 'selection')
        point, normal = _plane(sel['plane'])
        out = {i for i, p in enumerate(polys) if _dot(_sub(centroid(p), point), normal) >= -g.EPS}
    elif isinstance(sel, dict) and 'facing' in sel:
        _keys(sel, {'facing', 'within'}, 'faces')
        d = _axis(sel['facing'], 'facing')
        limit = math.cos(math.radians(g.number(sel.get('within', 30), 'within')))
        out = {i for i, p in enumerate(polys) if _dot(p.n, d) >= limit - 1e-9}
    else:
        return g.select_faces(polys, 'all' if sel is None else sel)
    if not out:
        raise MeshError('face selection is empty')
    return out


def _plane(spec):
    if not isinstance(spec, dict):
        raise MeshError('plane is {point:[x,y,z], normal:[x,y,z]} (the side the normal points to is selected)')
    _keys(spec, {'point', 'normal'}, 'plane')
    return g.vector(spec.get('point')), _axis(spec.get('normal'), 'plane.normal')


def select_verts(polys, sel, last):
    keys = {key(v.p) for p in polys for v in p.corners}
    if sel is None or sel == 'all':
        return keys
    if isinstance(sel, dict) and set(sel) == {'faces'}:
        faces = select_faces(polys, sel['faces'], last)
        return {key(v.p) for i in faces for v in polys[i].corners}
    if isinstance(sel, dict) and 'plane' in sel:
        _keys(sel, {'plane'}, 'selection')
        point, normal = _plane(sel['plane'])
        out = {k for k in keys if _dot(_sub(k, point), normal) >= -g.EPS}
    elif isinstance(sel, dict):
        _keys(sel, {'mins', 'maxs'}, 'verts')
        lo, hi = g.vector(sel.get('mins')), g.vector(sel.get('maxs'))
        out = {k for k in keys if all(lo[i] - g.EPS <= k[i] <= hi[i] + g.EPS for i in range(3))}
    elif isinstance(sel, list) and sel and all(isinstance(x, (list, tuple)) for x in sel):
        out = set()
        for q in sel:
            q = g.vector(q)
            best = min(keys, key=lambda k: _dot(_sub(k, q), _sub(k, q)))
            if _dot(_sub(best, q), _sub(best, q)) > 1.0:
                raise MeshError(f'no vertex within 1 unit of {list(q)}; map_info(about="geometry") shows bounds')
            out.add(best)
    else:
        raise MeshError('verts: "all", {mins,maxs}, {plane:{point,normal}}, {faces:<faces>} or [[x,y,z],...]')
    if not out:
        raise MeshError('vertex selection is empty')
    return out


def edge_table(polys):
    """Undirected edges with their two faces: {(ka,kb) sorted: [(face, a_point, b_point), ...]}."""
    table = {}
    for fi, p in enumerate(polys):
        n = len(p.corners)
        for i in range(n):
            a, b = p.corners[i].p, p.corners[(i + 1) % n].p
            ka, kb = key(a), key(b)
            if ka == kb:
                continue
            table.setdefault((min(ka, kb), max(ka, kb)), []).append((fi, a, b))
    return table


def select_edges(polys, sel, angle, last):
    """Convex edges sharper than `angle` degrees, filtered by the selection: [(a, b, f1, f2)]."""
    limit = math.cos(math.radians(angle))
    found = []
    for (ka, kb), uses in edge_table(polys).items():
        if len(uses) != 2:
            continue
        (f1, a, b), (f2, _, _) = uses
        n1, n2 = polys[f1].n, polys[f2].n
        if _dot(n1, n2) > limit:
            continue
        convex = _dot(n1, _sub(centroid(polys[f2]), a)) < -1e-3
        found.append({'a': a, 'b': b, 'f1': f1, 'f2': f2, 'convex': convex})
    if sel == 'sharp' or sel is None:
        chosen = [e for e in found if e['convex']]
    elif isinstance(sel, dict) and set(sel) <= {'mins', 'maxs'} and sel:
        lo, hi = g.vector(sel.get('mins')), g.vector(sel.get('maxs'))
        chosen = [e for e in found if all(lo[i] - g.EPS <= (e['a'][i] + e['b'][i]) / 2 <= hi[i] + g.EPS
                                          for i in range(3))]
    elif isinstance(sel, list) and sel and all(isinstance(x, (list, tuple)) and len(x) == 2
                                               and all(isinstance(y, (list, tuple)) for y in x) for x in sel):
        chosen = []
        for pa, pb in sel:
            pa, pb = g.vector(pa), g.vector(pb)
            mid = tuple((x + y) / 2 for x, y in zip(pa, pb))
            best = min(found, key=lambda e: _dist2(mid, _mid(e)), default=None)
            if best is None or _dist2(mid, _mid(best)) > 1.0:
                raise MeshError(f'no sharp edge between {list(pa)} and {list(pb)}')
            chosen.append(best)
    else:
        faces = select_faces(polys, sel['faces'] if isinstance(sel, dict) and set(sel) == {'faces'} else sel, last)
        chosen = [e for e in found if e['f1'] in faces or e['f2'] in faces]
    concave = [e for e in chosen if not e['convex']]
    if concave:
        e = concave[0]
        raise MeshError(f'bevel works on convex edges; the edge {list(e["a"])} -> {list(e["b"])} is concave '
                        '(fill inner corners with an extrude or a mesh piece instead)')
    if not chosen:
        raise MeshError(f'no convex edge sharper than {angle}° in the selection')
    return chosen


def _mid(e):
    return tuple((x + y) / 2 for x, y in zip(e['a'], e['b']))


def _dist2(a, b):
    d = _sub(a, b)
    return _dot(d, d)


# ---- texture -------------------------------------------------------------------------------------------
def reproject(poly, texture_size):
    """World-aligned UV like Hammer's, keeping the face's texel scale and offset."""
    attrs = poly.attributes or {}
    scale = tuple(attrs.get('textureScale', (0.25, 0.25)))
    off = (attrs.get('textureAxisU', (0, 0, 0, 0))[3], attrs.get('textureAxisV', (0, 0, 0, 0))[3])
    try:
        n = poly.n
    except MeshError:
        return
    u, v = world_axes(n)
    tw, th = texture_size(poly.material)
    for c in poly.corners:
        c.uv = ((_dot(c.p, u) / scale[0] + off[0]) / tw, (_dot(c.p, v) / scale[1] + off[1]) / th)
    poly.attributes = {**attrs, 'textureScale': scale, 'textureAxisU': (*u, off[0]), 'textureAxisV': (*v, off[1])}


def _new_poly(points, like, uv_hint=None):
    corners = [g.Corner(tuple(p), (0., 0.), like.corners[0].tint, like.corners[0].blend) for p in points]
    attrs = {k: v for k, v in (like.attributes or {}).items() if k in ('textureScale', 'flags', 'lightmapScaleBias')}
    poly = g.Polygon(corners, like.material, attrs)
    poly.dirty = 'new'
    return poly


# ---- steps ---------------------------------------------------------------------------------------------
class Editor:
    def __init__(self, polys, texture_size, keep_uv=False):
        self.polys = polys
        self.texture_size = texture_size
        self.keep_uv = keep_uv
        self.last: set[int] = set()
        for p in self.polys:
            p.dirty = False

    def _settle(self):
        """Bring corner UVs of changed faces up to date before a CSG step makes new polygons from them."""
        for p in self.polys:
            state = getattr(p, 'dirty', False)
            if state == 'new' or (state and not self.keep_uv):
                reproject(p, self.texture_size)
            p.dirty = False

    # vertex maps
    def _apply(self, fn, selected):
        """Move every corner whose vertex is selected: fn(point) -> new point."""
        moved = {}
        for k in selected:
            moved[k] = tuple(fn(k))
        for p in self.polys:
            hit = False
            for c in p.corners:
                k = key(c.p)
                if k in moved:
                    c.p = moved[k]
                    hit = True
            if hit:
                p.dirty = True
        return len(moved)

    def _bounds(self, keys):
        pts = list(keys)
        return (tuple(min(p[i] for p in pts) for i in range(3)), tuple(max(p[i] for p in pts) for i in range(3)))

    def move(self, s):
        sel = select_verts(self.polys, {'faces': s['faces']} if 'faces' in s else s.get('verts'), self.last)
        offset = g.vector(s.get('offset', [0, 0, 0]))
        sc = s.get('scale', 1)
        sc = (g.number(sc, 'scale'),) * 3 if isinstance(sc, (int, float)) else g.vector(sc)
        if any(x <= 0 for x in sc):
            raise MeshError('scale must be positive (mirror makes mirrored halves)')
        lo, hi = self._bounds(sel)
        pivot = g.vector(s['pivot']) if 'pivot' in s else tuple((a + b) / 2 for a, b in zip(lo, hi))
        rot = None
        if 'rotate' in s:
            r = s['rotate']
            if not isinstance(r, dict):
                raise MeshError('rotate is {axis: x|y|z|[x,y,z], angle: degrees}')
            _keys(r, {'axis', 'angle'}, 'rotate')
            rot = (_axis(r.get('axis', 'z')), math.radians(g.number(r.get('angle', 0), 'rotate.angle')))

        def fn(p):
            d = tuple((p[i] - pivot[i]) * sc[i] for i in range(3))
            if rot:
                d = _rotate(d, *rot)
            return _add(_add(pivot, d), offset)
        count = self._apply(fn, sel)
        self.last = set()
        return {'verts': count}

    def extrude(self, s):
        faces = select_faces(self.polys, s.get('faces'), self.last)
        if 'direction' in s:
            direction = g.vector(s['direction'])
            dist = math.sqrt(_dot(direction, direction))
            if dist < 1e-6:
                raise MeshError('direction must be a non-zero vector')
            fixed = _scale(direction, 1 / dist)
        else:
            dist = g.number(s.get('distance', 0), 'distance')
            fixed = None
        if abs(dist) < 1e-3 or abs(dist) > 4096:
            raise MeshError('extrude distance must be 0.001..4096 units (negative pushes in)')
        normals = {}
        for i in faces:
            n = self.polys[i].n
            for c in self.polys[i].corners:
                normals.setdefault(key(c.p), []).append(n)
        offset = {}
        for k, ns in normals.items():
            if fixed:
                offset[k] = _scale(fixed, dist)
                continue
            avg = _norm(tuple(sum(n[i] for n in ns) for i in range(3)))
            # keep the walls of a flat region parallel: scale so the projection on each face normal is dist
            cos = min(_dot(avg, n) for n in ns)
            if cos < 0.3:
                raise MeshError('extrude region folds over itself (faces more than ~70° apart); extrude them in '
                                'separate steps or give a direction')
            offset[k] = _scale(avg, dist / cos)
        # walls along the region boundary: directed edge a->b of a selected face whose twin is outside
        owner = {}
        for fi, p in enumerate(self.polys):
            n = len(p.corners)
            for j in range(n):
                owner[(key(p.corners[j].p), key(p.corners[(j + 1) % n].p))] = fi
        walls = []
        for fi in faces:
            p = self.polys[fi]
            n = len(p.corners)
            for j in range(n):
                a, b = p.corners[j].p, p.corners[(j + 1) % n].p
                twin = owner.get((key(b), key(a)))
                if twin is None or twin not in faces:
                    a2, b2 = _add(a, offset[key(a)]), _add(b, offset[key(b)])
                    walls.append(_new_poly([a, b, b2, a2], p))
        for fi in faces:
            p = self.polys[fi]
            for c in p.corners:
                c.p = _add(c.p, offset[key(c.p)])
            p.dirty = True
        self.polys.extend(walls)
        self.last = set(faces)
        return {'faces': len(faces), 'walls': len(walls)}

    def inset(self, s):
        faces = select_faces(self.polys, s.get('faces'), self.last)
        width = g.number(s.get('width', 0), 'width')
        if not 0.05 <= width <= 2048:
            raise MeshError('inset width must be 0.05..2048 units')
        inner, rings = [], []
        for fi in sorted(faces):
            p = self.polys[fi]
            n = p.n
            pts = [c.p for c in p.corners]
            m = len(pts)
            new = []
            for j in range(m):
                prev, cur, nxt = pts[j - 1], pts[j], pts[(j + 1) % m]
                e1, e2 = _norm(_sub(cur, prev)), _norm(_sub(nxt, cur))
                n1, n2 = _cross(n, e1), _cross(n, e2)          # inward (left of a CCW edge)
                bis = _add(n1, n2)
                denom = 1 + _dot(n1, n2)
                if denom < 1e-3:
                    raise MeshError(f'face {fi} has a spike too sharp to inset')
                new.append(_add(cur, _scale(bis, width / denom)))
            # the inner face must keep its orientation and stay inside (no flipped or crossing edges)
            try:
                same = _dot(g.normal(new), n) > 0.99
            except MeshError:
                same = False
            if not same or any(_dot(_sub(new[(j + 1) % m], new[j]), _sub(pts[(j + 1) % m], pts[j])) <= 0
                               for j in range(m)):
                raise MeshError(f'inset width {width} is too large for face {fi}')
            for j in range(m):
                k = (j + 1) % m
                rings.append(_new_poly([pts[j], pts[k], new[k], new[j]], p))
            for c, q in zip(p.corners, new):
                c.p = q
            p.dirty = True
            inner.append(fi)
        self.polys.extend(rings)
        self.last = set(inner)
        out = {'faces': len(inner), 'frames': len(rings)}
        if 'depth' in s and abs(g.number(s['depth'], 'depth')) >= 1e-3:
            pushed = Editor.extrude(self, {'faces': 'last', 'distance': s['depth']})
            out['depth_walls'] = pushed['walls']
        return out

    def bevel(self, s, material_check):
        width = g.number(s.get('width', 0), 'width')
        if not 0.1 <= width <= 512:
            raise MeshError('bevel width must be 0.1..512 units')
        segments = g.integer(s.get('segments', 1), 1, 8, 'segments')
        angle = g.number(s.get('angle', 30), 'angle')
        if not 1 <= angle <= 179:
            raise MeshError('angle (minimum sharpness of an edge) must be 1..179 degrees')
        edges = select_edges(self.polys, s.get('edges', 'sharp'), angle, self.last)
        if len(edges) * segments > MAX_CUTS:
            raise MeshError(f'{len(edges)} edges x {segments} segments > {MAX_CUTS} cuts; bevel fewer edges')
        material = material_check(s['material']) if 'material' in s else None
        self._settle()
        cutters = []
        for e in edges:
            p1, p2 = self.polys[e['f1']], self.polys[e['f2']]
            n1, n2 = p1.n, p2.n
            a, b = e['a'], e['b']
            length = math.sqrt(_dist2(a, b))
            d = _scale(_sub(b, a), 1 / length)
            t1, t2 = _norm(_cross(n1, d)), _norm(_cross(n2, d))
            if _dot(t1, _sub(centroid(p1), a)) < 0:
                t1 = _scale(t1, -1)
            if _dot(t2, _sub(centroid(p2), a)) < 0:
                t2 = _scale(t2, -1)
            # the bevel may not eat a whole face: width < the face's extent away from the edge
            for p, t in ((p1, t1), (p2, t2)):
                reach = max(_dot(_sub(c.p, a), t) for c in p.corners)
                if width >= reach - 0.05:
                    raise MeshError(f'bevel width {width} is wider than a face next to the edge '
                                    f'{[round(x, 2) for x in a]} -> {[round(x, 2) for x in b]} ({reach:.1f})')
            P1, P2 = _add(a, _scale(t1, width)), _add(a, _scale(t2, width))
            if segments == 1:
                arc = [P1, P2]
            else:
                diff_n = math.sqrt(_dist2(n1, n2))
                r = width * math.sqrt(_dist2(t1, t2)) / diff_n
                centre = _sub(P1, _scale(n1, r))
                theta = math.acos(max(-1., min(1., _dot(n1, n2))))
                arc = []
                for k in range(segments + 1):
                    f = k / segments
                    w1 = math.sin((1 - f) * theta) / math.sin(theta)
                    w2 = math.sin(f * theta) / math.sin(theta)
                    arc.append(_add(centre, _scale(_add(_scale(n1, w1), _scale(n2, w2)), r)))
            apex = _add(a, _scale(_norm(_add(n1, n2)), width))
            mat = material or p1.material
            margin = width * 1.5 + 1
            for k in range(segments):
                q0, q1 = arc[k], arc[k + 1]
                cd = _norm(_sub(q1, q0))
                ext = width * 0.02 + 0.01
                tri = [apex, _sub(q0, _scale(cd, ext)), _add(q1, _scale(cd, ext))]
                cutters.append(_prism(tri, d, -margin, length + margin, mat))
        polys = self.polys
        for cutter in cutters:
            polys = merge_coplanar(local_subtract(polys, g.mesh_polygons(cutter, self.texture_size)))
            if len(polys) > g.MAX_FACES:
                raise MeshError('bevel face budget exceeded')
        for p in polys:
            if not hasattr(p, 'dirty'):
                p.dirty = False
        self.polys = polys
        self.last = set()
        return {'edges': len(edges), 'segments': segments}

    def subdivide(self, s):
        faces = select_faces(self.polys, s.get('faces'), self.last)
        cell = g.number(s.get('cell', 32), 'cell')
        if not 2 <= cell <= 1024:
            raise MeshError('cell must be 2..1024 units')
        axes = s.get('axes', 'xyz')
        if not isinstance(axes, str) or not axes or set(axes) - set('xyz'):
            raise MeshError('axes: any of "x", "y", "z" (e.g. "z" splits a column into rings)')
        out, last = [], set()
        for fi, p in enumerate(self.polys):
            parts = _grid_split(p, cell, ['xyz'.index(a) for a in sorted(set(axes))]) if fi in faces else [p]
            for part in parts:
                if fi in faces:
                    last.add(len(out))
                    part.dirty = getattr(p, 'dirty', False)
                out.append(part)
            if len(out) > g.MAX_FACES:
                raise MeshError('subdivide face budget exceeded; use a bigger cell or fewer faces')
        self.polys = out
        self.last = last
        return {'faces': len(faces), 'pieces': len(last)}

    def _range(self, s, sel, axis):
        """Selection extent along an axis and the deformation centre."""
        lo, hi = self._bounds(sel)
        centre = g.vector(s['center']) if 'center' in s else tuple((a + b) / 2 for a, b in zip(lo, hi))
        proj = [_dot(k, axis) for k in sel]
        start = g.number(s['from'], 'from') if 'from' in s else min(proj)
        end = g.number(s['to'], 'to') if 'to' in s else max(proj)
        if end - start < 1e-3:
            raise MeshError('the selection has no extent along the axis (give from/to)')
        return centre, start, end

    def bend(self, s):
        sel = select_verts(self.polys, s.get('verts'), self.last)
        axis = _axis(s.get('axis', 'z'))
        toward = _axis(s.get('toward', '+x'), 'toward')
        if abs(_dot(axis, toward)) > 1e-6:
            raise MeshError('toward must be perpendicular to axis (bend a z column toward +x)')
        angle = math.radians(g.number(s.get('angle', 0), 'angle'))
        if not 1e-4 < abs(angle) <= 2 * math.pi:
            raise MeshError('bend angle must be 0..360 degrees (negative bends the other way)')
        centre, start, end = self._range(s, sel, axis)
        length = end - start
        radius = length / angle
        base = _add(_sub(centre, _scale(axis, _dot(centre, axis))), _scale(axis, start))

        def fn(p):
            rel = _sub(p, base)
            t, y = _dot(rel, axis), _dot(rel, toward)
            rest = _sub(rel, _add(_scale(axis, t), _scale(toward, y)))
            tt = min(max(t, 0.), length)
            th = tt / radius
            pos = _add(_scale(toward, radius - (radius - y) * math.cos(th)), _scale(axis, (radius - y) * math.sin(th)))
            if t != tt:       # beyond the bent span: straight on along the rotated axis
                pos = _add(pos, _scale(_add(_scale(axis, math.cos(th)), _scale(toward, math.sin(th))), t - tt))
            return _add(_add(base, pos), rest)
        return {'verts': self._apply(fn, sel), 'radius': round(radius, 2)}

    def twist(self, s):
        sel = select_verts(self.polys, s.get('verts'), self.last)
        axis = _axis(s.get('axis', 'z'))
        angle = math.radians(g.number(s.get('angle', 0), 'angle'))
        centre, start, end = self._range(s, sel, axis)

        def fn(p):
            t = min(max((_dot(p, axis) - start) / (end - start), 0.), 1.)
            return _add(centre, _rotate(_sub(p, centre), axis, angle * t))
        return {'verts': self._apply(fn, sel)}

    def taper(self, s):
        sel = select_verts(self.polys, s.get('verts'), self.last)
        axis = _axis(s.get('axis', 'z'))
        sc = s.get('scale', 1)
        sa, sb = (g.number(sc, 'scale'),) * 2 if isinstance(sc, (int, float)) else g.vector(sc, 2)
        if min(sa, sb) <= 0:
            raise MeshError('taper scale must be positive')
        centre, start, end = self._range(s, sel, axis)
        u, v = g.basis(axis)

        def fn(p):
            t = min(max((_dot(p, axis) - start) / (end - start), 0.), 1.)
            rel = _sub(p, centre)
            ca, cb = 1 + (sa - 1) * t, 1 + (sb - 1) * t
            return _add(centre, _add(_scale(axis, _dot(rel, axis)),
                                     _add(_scale(u, _dot(rel, u) * ca), _scale(v, _dot(rel, v) * cb))))
        return {'verts': self._apply(fn, sel)}

    def noise(self, s):
        sel = select_verts(self.polys, s.get('verts'), self.last)
        amount = g.number(s.get('amount', 4), 'amount')
        radius = g.number(s.get('radius', 32), 'radius')
        if not 0 < amount <= 256 or not 1 <= radius <= 8192:
            raise MeshError('noise amount 0..256 units, radius 1..8192 (feature size)')
        seed = g.integer(s.get('seed', 1), 0, 2147483647, 'seed')
        along = s.get('along', 'normal')
        if along not in ('normal', 'xyz'):
            raise MeshError('along: normal (bulges in and out) or xyz (wobble in every direction)')
        normals = self._vertex_normals()

        def fn(p):
            if along == 'normal':
                return _add(p, _scale(normals[p], (g.noise(p, seed, radius) * 2 - 1) * amount))
            return _add(p, tuple((g.noise(p, seed + i * 7919, radius) * 2 - 1) * amount for i in range(3)))
        return {'verts': self._apply(fn, sel)}

    def _vertex_normals(self):
        acc = {}
        for p in self.polys:
            n = p.n
            for c in p.corners:
                k = key(c.p)
                acc[k] = _add(acc.get(k, (0., 0., 0.)), n)
        return {k: _norm(v) if _dot(v, v) > 1e-12 else (0., 0., 1.) for k, v in acc.items()}

    def smooth(self, s):
        sel = select_verts(self.polys, s.get('verts'), self.last)
        iterations = g.integer(s.get('iterations', 1), 1, 20, 'iterations')
        strength = g.number(s.get('strength', 0.5), 'strength')
        if not 0 < strength <= 1:
            raise MeshError('strength must be 0..1')
        for _ in range(iterations):
            nbr = {}
            for p in self.polys:
                n = len(p.corners)
                for j in range(n):
                    a, b = key(p.corners[j].p), key(p.corners[(j + 1) % n].p)
                    nbr.setdefault(a, set()).add(b)
                    nbr.setdefault(b, set()).add(a)
            targets = {k: tuple(sum(q[i] for q in nbr[k]) / len(nbr[k]) for i in range(3)) for k in sel if k in nbr}
            moved = self._apply(lambda p: _add(p, _scale(_sub(targets[p], p), strength)), set(targets))
            sel = {key(_add(k, _scale(_sub(targets[k], k), strength))) for k in targets}
        return {'verts': moved, 'iterations': iterations}

    def weld(self, s):
        dist = g.number(s.get('distance', 0.5), 'distance')
        if not 0.002 <= dist <= 64:
            raise MeshError('weld distance must be 0.002..64 units')
        keys = sorted({key(v.p) for p in self.polys for v in p.corners})
        parent = {k: k for k in keys}

        def find(k):
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k
        cells = {}
        for k in keys:
            cells.setdefault(tuple(math.floor(c / dist) for c in k), []).append(k)
        for cell, members in cells.items():
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        for q in cells.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), ()):
                            for k in members:
                                if k < q and _dist2(k, q) <= dist * dist:
                                    parent[find(q)] = find(k)
        groups = {}
        for k in keys:
            groups.setdefault(find(k), []).append(k)
        target = {}
        for members in groups.values():
            mean = tuple(sum(m[i] for m in members) / len(members) for i in range(3))
            for m in members:
                target[m] = mean
        merged = sum(len(m) - 1 for m in groups.values())
        out = []
        for p in self.polys:
            moved = False
            for c in p.corners:
                t = target[key(c.p)]
                moved = moved or _dist2(t, c.p) > 1e-8
                c.p = t
            part = p.part(p.corners)
            if part is not None:
                part.dirty = 'new' if getattr(p, 'dirty', False) == 'new' else (moved or getattr(p, 'dirty', False))
                out.append(part)
        self.polys = out
        self.last = set()
        return {'merged': merged, 'faces': len(out)}

    def mirror(self, s):
        axis_name = s.get('axis', 'x')
        if axis_name not in AXES:
            raise MeshError('mirror axis must be x, y or z')
        ai = 'xyz'.index(axis_name)
        pts = [v.p for p in self.polys for v in p.corners]
        lo = [min(p[i] for p in pts) for i in range(3)]
        hi = [max(p[i] for p in pts) for i in range(3)]
        at = g.number(s['at'], 'at') if 'at' in s else (lo[ai] + hi[ai]) / 2
        keep = s.get('keep', '+')
        if keep not in ('+', '-'):
            raise MeshError('keep: "+" keeps the half above `at` and mirrors it, "-" the half below')
        if not lo[ai] + 1e-3 < at < hi[ai] - 1e-3:
            raise MeshError(f'mirror plane {axis_name}={at} must cross the mesh ({lo[ai]}..{hi[ai]})')
        self._settle()
        pad = 16
        box_lo = [x - pad for x in lo]
        box_hi = [x + pad for x in hi]
        if keep == '+':
            box_hi[ai] = at
        else:
            box_lo[ai] = at
        from . import mesh as _mesh
        cutter = _mesh.box(box_lo, box_hi, self.polys[0].material)
        half = merge_coplanar(g.subtract(self.polys, g.mesh_polygons(cutter, self.texture_size)))
        seam = [p for p in half if all(abs(v.p[ai] - at) < 1e-3 for v in p.corners)]
        half = [p for p in half if p not in seam]
        if not half:
            raise MeshError('nothing left on the kept side')
        mirrored = []
        for p in half:
            corners = [g.Corner(tuple(2 * at - c if i == ai else c for i, c in enumerate(v.p)), v.uv, v.tint, v.blend)
                       for v in reversed(p.corners)]
            q = g.Polygon(corners, p.material, dict(p.attributes or {}))
            q.dirty = True
            mirrored.append(q)
        for p in half:
            if not hasattr(p, 'dirty'):
                p.dirty = False
        self.polys = half + mirrored
        self.last = set()
        return {'kept': len(half), 'faces': len(self.polys)}

    # final pass
    def finish(self):
        out = []
        for p in self.polys:
            parts = [p]
            if getattr(p, 'dirty', False) and len(p.corners) > 3 and _warp(p) > PLANAR_TOL:
                parts = _triangulate(p)
            for q in parts:
                state = getattr(q, 'dirty', False)
                if state == 'new' or (state and not self.keep_uv):
                    reproject(q, self.texture_size)
                elif state and q.attributes:
                    q.attributes = {**q.attributes, **_axes_for(q)}
                out.append(q)
        return out


def _box(points, pad=0.):
    return (tuple(min(p[i] for p in points) - pad for i in range(3)), tuple(max(p[i] for p in points) + pad for i in range(3)))


def local_subtract(polys, cutter):
    """CSG subtract on the polygons that reach the cutter's box only: every face of the solid that crosses that
    box takes part, so inside/outside is decided by the real local surface; far faces pass through untouched
    (a bevel of 32 edges is seconds instead of minutes)."""
    lo, hi = _box([v.p for c in cutter for v in c.corners], 0.01)
    near, far = [], []
    for p in polys:
        plo, phi = _box([v.p for v in p.corners])
        (near if all(plo[i] <= hi[i] and phi[i] >= lo[i] for i in range(3)) else far).append(p)
    if not near:
        return polys
    return far + g.subtract(near, cutter)


def _plane_key(poly):
    n = poly.n
    d = _dot(n, poly.corners[0].p)
    attrs = poly.attributes or {}
    tex = tuple(round(x, 4) for k in ('textureAxisU', 'textureAxisV', 'textureScale') for x in attrs.get(k, ()))
    return (poly.material, tuple(round(x, 4) for x in n), round(d, 3), tex)


def _uniform(poly):
    c0 = poly.corners[0]
    return all(c.tint == c0.tint and c.blend == c0.blend for c in poly.corners)


def _convex(points, n):
    m = len(points)
    for i in range(m):
        a, b, c = points[i - 1], points[i], points[(i + 1) % m]
        if _dot(_cross(_sub(b, a), _sub(c, b)), n) < -1e-6:
            return False
    return True


def _drop_collinear(corners):
    out = list(corners)
    changed = True
    while changed and len(out) > 3:
        changed = False
        for i in range(len(out)):
            a, b, c = out[i - 1].p, out[i].p, out[(i + 1) % len(out)].p
            cr = _cross(_sub(b, a), _sub(c, b))
            if _dot(cr, cr) < 1e-10 and _dot(_sub(b, a), _sub(c, b)) > 0:
                out.pop(i)
                changed = True
                break
    return out


def merge_coplanar(polys):
    """Join coplanar fragments a CSG step leaves behind (same material, plane and texture axes, unpainted) into
    convex faces again, so bevels stay light and look like Hammer's."""
    groups = {}
    for i, p in enumerate(polys):
        try:
            groups.setdefault(_plane_key(p), []).append(i)
        except MeshError:
            continue
    alive = {i: p for i, p in enumerate(polys)}
    for members in groups.values():
        if len(members) < 2:
            continue
        pool = [i for i in members if _uniform(polys[i])]
        merged = True
        while merged:
            merged = False
            edges = {}
            for i in pool:
                cs = alive[i].corners
                for j in range(len(cs)):
                    edges[(key(cs[j].p), key(cs[(j + 1) % len(cs)].p))] = (i, j)
            for (a, b), (i, j) in list(edges.items()):
                other = edges.get((b, a))
                if other is None or other[0] == i:
                    continue
                k, l = other
                pa, pb = alive[i], alive[k]
                ca, cb = pa.corners, pb.corners
                if ca[0].tint != cb[0].tint or ca[0].blend != cb[0].blend:
                    continue
                # walk pa from b round to a, then pb from a round to b (both skip the shared edge)
                ring = [ca[(j + 1 + t) % len(ca)] for t in range(len(ca))]
                ring += [cb[(l + 2 + t) % len(cb)] for t in range(len(cb) - 2)]
                ring = _drop_collinear(ring)
                n = pa.n
                if len(ring) < 3 or not _convex([c.p for c in ring], n):
                    continue
                if len({key(c.p) for c in ring}) != len(ring):
                    continue
                joined = g.Polygon(ring, pa.material, dict(pa.attributes or {}))
                joined.dirty = getattr(pa, 'dirty', False) or getattr(pb, 'dirty', False)
                alive[i] = joined
                del alive[k]
                pool.remove(k)
                merged = True
                break
    return [alive[i] for i in sorted(alive)]


def _axes_for(poly):
    attrs = poly.attributes or {}
    u, v = world_axes(poly.n)
    off = (attrs.get('textureAxisU', (0, 0, 0, 0))[3], attrs.get('textureAxisV', (0, 0, 0, 0))[3])
    return {'textureAxisU': (*u, off[0]), 'textureAxisV': (*v, off[1])}


def _rotate(d, axis, angle):
    """Rodrigues rotation of vector d around a unit axis."""
    c, s = math.cos(angle), math.sin(angle)
    return _add(_add(_scale(d, c), _scale(_cross(axis, d), s)), _scale(axis, _dot(axis, d) * (1 - c)))


def _warp(poly):
    n = poly.n
    o = poly.corners[0].p
    return max(abs(_dot(_sub(c.p, o), n)) for c in poly.corners)


def _triangulate(poly):
    """Split a bent polygon into triangles (ear clipping in its best-fit plane), attributes kept."""
    n = poly.n
    u, v = g.basis(n)
    o = poly.corners[0].p
    flat = [(_dot(_sub(c.p, o), u), _dot(_sub(c.p, o), v)) for c in poly.corners]
    try:
        tris = g.triangulate(flat)
    except MeshError:
        tris = [[flat[0], flat[i], flat[i + 1]] for i in range(1, len(flat) - 1)]
    lookup = {q: c for q, c in zip(flat, poly.corners)}
    out = []
    for t in tris:
        q = g.Polygon([g.Corner(lookup[x].p, lookup[x].uv, lookup[x].tint, lookup[x].blend) for x in t],
                      poly.material, dict(poly.attributes or {}))
        q.dirty = poly.dirty
        out.append(q)
    return out


def _grid_split(poly, cell, axes):
    if g.polygon_area([v.p for v in poly.corners]) < 1e-4:
        return [poly]
    pieces = [poly]
    for axis in axes:
        values = [v.p[axis] for v in poly.corners]
        n = tuple(1. if k == axis else 0. for k in range(3))
        for k in range(math.floor(min(values) / cell) + 1, math.ceil(max(values) / cell)):
            nxt = []
            for piece in pieces:
                _, front, back = g.split_polygon(piece, n, k * cell)
                nxt.extend(x for x in (front, back) if x is not None)
            pieces = nxt
    return pieces


def _prism(tri, d, s0, s1, material):
    """Convex triangular prism: cross-section `tri` (3 points) swept along unit d from s0 to s1."""
    m = Mesh()
    lo = [_add(p, _scale(d, s0)) for p in tri]
    hi = [_add(p, _scale(d, s1)) for p in tri]
    centre = tuple(sum(p[i] for p in lo + hi) / 6 for i in range(3))

    def face(points):
        c = tuple(sum(p[i] for p in points) / len(points) for i in range(3))
        m.face_out(points, _sub(c, centre), material)
    face(lo)
    face(hi)
    for i in range(3):
        j = (i + 1) % 3
        face([lo[i], lo[j], hi[j], hi[i]])
    return m


def run(polys, steps, texture_size, material_check, keep_uv=False):
    """Apply the steps; returns (polygons, per-step reports). Raises MeshError naming the failing step."""
    if not isinstance(steps, list) or not steps:
        raise MeshError('steps must be a non-empty list of {"do": ..., ...}')
    if len(steps) > MAX_STEPS:
        raise MeshError(f'at most {MAX_STEPS} steps per edit_mesh')
    ed = Editor(polys, texture_size, keep_uv)
    reports = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict) or s.get('do') not in STEP_KEYS:
            raise MeshError(f'step #{i}: "do" must be one of {", ".join(STEP_KEYS)}')
        extra = set(s) - STEP_KEYS[s['do']] - {'do'}
        if extra:
            raise MeshError(f'step #{i} ({s["do"]}): unknown fields {sorted(extra)}; allowed: '
                            f'{", ".join(sorted(STEP_KEYS[s["do"]]))}')
        try:
            fn = getattr(ed, s['do'])
            report = fn(s, material_check) if s['do'] == 'bevel' else fn(s)
        except MeshError as e:
            raise MeshError(f'step #{i} ({s["do"]}): {e}') from None
        reports.append({'do': s['do'], **report})
    return ed.finish(), reports
