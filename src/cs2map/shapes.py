"""Shape specs (plain JSON-like dicts) -> Mesh objects.

Every shape is in world coordinates.  Used by map_edit's add_mesh and
add_brush_entity operations, so the same vocabulary works for visible
geometry and for trigger volumes.
"""
from __future__ import annotations

import math
import random
from typing import Any

from . import mesh
from .mesh import Mesh, MeshError

SHAPES = ('box', 'wedge', 'cylinder', 'stairs', 'prism', 'terrain', 'polyhedron', 'room', 'arch')
# shapes added by extensions: name -> fn(spec: dict, material: str) -> list[Mesh]
EXTRA_SHAPES: dict[str, Any] = {}


def register_shape(name: str, fn) -> None:
    if name in SHAPES or name in EXTRA_SHAPES:
        raise ValueError(f'shape {name!r} already exists')
    EXTRA_SHAPES[name] = fn


def _num(spec: dict, key: str, default=None) -> float:
    v = spec.get(key, default)
    if v is None:
        raise MeshError(f'shape needs "{key}"')
    return float(v)


def _vec(spec: dict, key: str, n: int = 3, default=None) -> list[float]:
    v = spec.get(key, default)
    if v is None or len(v) != n:
        raise MeshError(f'shape needs "{key}" with {n} numbers')
    return [float(c) for c in v]


def _check_keys(spec: dict, allowed: set[str]) -> None:
    extra = set(spec) - allowed - {'shape', 'material', 'materials'}
    if extra:
        raise MeshError(f'unknown keys for shape {spec.get("shape")!r}: {sorted(extra)}')


def build(spec: dict[str, Any], default_material: str = mesh.DEFAULT_MATERIAL) -> list[Mesh]:
    """Return one or more meshes for a shape spec."""
    kind = spec.get('shape', 'box')
    mat = spec.get('material') or default_material
    mats = spec.get('materials') or {}
    if kind == 'box':
        _check_keys(spec, {'mins', 'maxs'})
        return [mesh.box(_vec(spec, 'mins'), _vec(spec, 'maxs'), mat, mats)]
    if kind == 'wedge':
        _check_keys(spec, {'mins', 'maxs', 'rise'})
        return [mesh.wedge(_vec(spec, 'mins'), _vec(spec, 'maxs'), spec.get('rise', '+x'), mat)]
    if kind == 'cylinder':
        _check_keys(spec, {'center', 'radius', 'z0', 'z1', 'sides'})
        return [mesh.cylinder(_vec(spec, 'center', 2), _num(spec, 'radius'), _num(spec, 'z0'), _num(spec, 'z1'),
                              int(spec.get('sides', 16)), mat)]
    if kind == 'stairs':
        _check_keys(spec, {'mins', 'maxs', 'steps', 'rise'})
        return [mesh.stairs(_vec(spec, 'mins'), _vec(spec, 'maxs'), int(spec.get('steps', 8)),
                            spec.get('rise', '+x'), mat)]
    if kind == 'prism':
        _check_keys(spec, {'polygon', 'z0', 'z1', 'axis'})
        poly = spec.get('polygon') or []
        return [mesh.prism([(float(p[0]), float(p[1])) for p in poly], _num(spec, 'z0'), _num(spec, 'z1'),
                           spec.get('axis', 'z'), mat, {'start': mats.get('bottom', mat), 'end': mats.get('top', mat),
                                                        'sides': mats.get('sides', mat)})]
    if kind == 'terrain':
        return [_terrain(spec, mat, mats)]
    if kind == 'polyhedron':
        _check_keys(spec, {'vertices', 'faces', 'face_materials'})
        return [_polyhedron(spec, mat)]
    if kind == 'room':
        return _room(spec, mat, mats)
    if kind == 'arch':
        return [_arch(spec, mat)]
    if kind in EXTRA_SHAPES:
        return list(EXTRA_SHAPES[kind](spec, mat))
    raise MeshError(f'unknown shape {kind!r}; shapes: {", ".join((*SHAPES, *EXTRA_SHAPES))}')


# -- terrain -------------------------------------------------------------------

def _value_noise(seed: int):
    rnd = random.Random(seed)
    perm = list(range(256))
    rnd.shuffle(perm)
    vals = [rnd.uniform(-1, 1) for _ in range(256)]

    def lattice(ix, iy):
        return vals[perm[(perm[ix & 255] + iy) & 255]]

    def noise(x, y):
        ix, iy = math.floor(x), math.floor(y)
        fx, fy = x - ix, y - iy
        sx, sy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        a = lattice(ix, iy) + sx * (lattice(ix + 1, iy) - lattice(ix, iy))
        b = lattice(ix, iy + 1) + sx * (lattice(ix + 1, iy + 1) - lattice(ix, iy + 1))
        return a + sy * (b - a)
    return noise


def _terrain(spec, mat, mats) -> Mesh:
    _check_keys(spec, {'mins', 'maxs', 'cells', 'heights', 'noise', 'thickness', 'absolute'})
    mins, maxs = _vec(spec, 'mins'), _vec(spec, 'maxs')
    absolute = spec.get('absolute', False)
    if not isinstance(absolute, bool):
        raise MeshError('absolute must be true (heights are world z) or false (heights are added to maxs.z)')
    if absolute and spec.get('heights') is None:
        raise MeshError('absolute:true needs "heights" (world z of every grid point)')
    cells = spec.get('cells', [16, 16])
    nx, ny = int(cells[0]), int(cells[1])
    heights = spec.get('heights')
    if heights is not None:
        if len(heights) != nx + 1 or any(len(r) != ny + 1 for r in heights):
            raise MeshError(f'heights must be a ({nx + 1} x {ny + 1}) grid: heights[i][j] for x index i, y index j')
        dx, dy = (maxs[0] - mins[0]) / nx, (maxs[1] - mins[1]) / ny

        # top z = maxs.z + height; with absolute:true the heights are world z (a common mistake doubled them)
        lift = -maxs[2] if absolute else 0.0

        def height(x, y):
            return float(heights[round((x - mins[0]) / dx)][round((y - mins[1]) / dy)]) + lift
    else:
        n = spec.get('noise') or {}
        amp = float(n.get('amplitude', 64))
        scale = float(n.get('scale', 512))
        octaves = int(n.get('octaves', 3))
        base = _value_noise(int(n.get('seed', 1)))

        def height(x, y):
            total, a, f, norm = 0.0, 1.0, 1.0, 0.0
            for _ in range(octaves):
                total += a * base(x / scale * f, y / scale * f)
                norm += a
                a *= 0.5
                f *= 2.0
            return amp * total / norm
    return mesh.heightfield(mins, maxs, (nx, ny), height, mat, mats.get('sides'), float(spec.get('thickness', 16)))


# -- polyhedron ------------------------------------------------------------------

def _polyhedron(spec, mat) -> Mesh:
    verts = [[float(c) for c in v] for v in spec.get('vertices') or []]
    faces = spec.get('faces') or []
    fmats = spec.get('face_materials') or []
    if len(verts) < 3 or not faces:
        raise MeshError('polyhedron needs vertices and faces')
    m = Mesh()
    for i, f in enumerate(faces):
        if any(not 0 <= int(k) < len(verts) for k in f):
            raise MeshError(f'face {i} uses a vertex index out of range')
        m.face([verts[int(k)] for k in f], fmats[i] if i < len(fmats) and fmats[i] else mat)
    return m


# -- room ---------------------------------------------------------------------------

def _room(spec, mat, mats) -> list[Mesh]:
    """Hollow box around an interior volume mins..maxs, with optional door/window openings.

    openings: [{"wall": "-x"|"+x"|"-y"|"+y", "center": <world coord along the wall>,
                "width": w, "bottom": <world z, default floor>, "height": h}]
    """
    _check_keys(spec, {'mins', 'maxs', 'thickness', 'openings', 'floor', 'ceiling', 'walls'})
    (x0, y0, z0), (x1, y1, z1) = _vec(spec, 'mins'), _vec(spec, 'maxs')
    t = float(spec.get('thickness', 16))
    if not (x1 > x0 and y1 > y0 and z1 > z0 and t > 0):
        raise MeshError('room needs maxs > mins and thickness > 0')
    wall_mat = mats.get('walls', mat)
    out: list[Mesh] = []
    if spec.get('floor', True):
        out.append(mesh.box((x0 - t, y0 - t, z0 - t), (x1 + t, y1 + t, z0), mats.get('floor', mat)))
    if spec.get('ceiling', True):
        out.append(mesh.box((x0 - t, y0 - t, z1), (x1 + t, y1 + t, z1 + t), mats.get('ceiling', mat)))
    walls = {
        # wall: (axis along the wall, along range, fixed range across the wall)
        '-x': ('y', (y0 - t, y1 + t), (x0 - t, x0)),
        '+x': ('y', (y0 - t, y1 + t), (x1, x1 + t)),
        '-y': ('x', (x0, x1), (y0 - t, y0)),
        '+y': ('x', (x0, x1), (y1, y1 + t)),
    }
    wanted = spec.get('walls', ['-x', '+x', '-y', '+y'])
    openings = spec.get('openings') or []
    for o in openings:
        if o.get('wall') not in walls:
            raise MeshError('opening "wall" must be one of -x, +x, -y, +y')
    for name, (along, (a0, a1), (c0, c1)) in walls.items():
        if name not in wanted:
            continue
        holes = []
        for o in openings:
            if o['wall'] != name:
                continue
            w, h = float(o.get('width', 96)), float(o.get('height', 112))
            c = float(o['center'])
            b = float(o.get('bottom', z0))
            lo, hi = c - w / 2, c + w / 2
            if lo < a0 + 1e-3 or hi > a1 - 1e-3 or b < z0 - 1e-3 or b + h > z1 + 1e-3 or w <= 0 or h <= 0:
                raise MeshError(f'opening on wall {name} does not fit inside the wall')
            holes.append((lo, hi, b, b + h))
        holes.sort()
        for (la, ha, _, _), (lb, _, _, _) in zip(holes, holes[1:]):
            if lb < ha:
                raise MeshError(f'openings on wall {name} overlap')
        pieces = []   # (along0, along1, z0, z1)
        cursor = a0
        for lo, hi, b, top in holes:
            if lo > cursor:
                pieces.append((cursor, lo, z0, z1))
            if b > z0:
                pieces.append((lo, hi, z0, b))
            if top < z1:
                pieces.append((lo, hi, top, z1))
            cursor = hi
        if cursor < a1:
            pieces.append((cursor, a1, z0, z1))
        for p0, p1, q0, q1 in pieces:
            if along == 'y':
                out.append(mesh.box((c0, p0, q0), (c1, p1, q1), wall_mat))
            else:
                out.append(mesh.box((p0, c0, q0), (p1, c1, q1), wall_mat))
    return out


# -- arch --------------------------------------------------------------------------------

def _arch(spec, mat) -> Mesh:
    """Wall piece with a round-topped opening: {"mins","maxs","axis":"x"|"y","opening_width",
    "opening_height" (to the top of the arch), "segments"}.  The wall spans mins..maxs; the
    opening is centred along `axis` (the wall's long direction) and starts at the floor."""
    _check_keys(spec, {'mins', 'maxs', 'axis', 'opening_width', 'opening_height', 'segments'})
    (x0, y0, z0), (x1, y1, z1) = _vec(spec, 'mins'), _vec(spec, 'maxs')
    axis = spec.get('axis', 'x')
    a0, a1 = (x0, x1) if axis == 'x' else (y0, y1)
    s0, s1 = (y0, y1) if axis == 'x' else (x0, x1)
    w = _num(spec, 'opening_width')
    h = _num(spec, 'opening_height')
    seg = int(spec.get('segments', 8))
    r = w / 2
    c = (a0 + a1) / 2
    spring = z0 + h - r
    if not (w < a1 - a0 and h < z1 - z0 and spring >= z0 and 2 <= seg <= 32):
        raise MeshError('arch opening must fit inside the wall (width < length, height < wall height)')
    # the wall face as convex pieces: two jambs plus crown strips between the arc and the top
    pieces = [[(a0, z0), (c - r, z0), (c - r, z1), (a0, z1)],
              [(c + r, z0), (a1, z0), (a1, z1), (c + r, z1)]]
    for i in range(seg):
        t0, t1 = math.pi - math.pi * i / seg, math.pi - math.pi * (i + 1) / seg
        p0 = (c + r * math.cos(t0), spring + r * math.sin(t0))
        p1 = (c + r * math.cos(t1), spring + r * math.sin(t1))
        if z1 - max(p0[1], p1[1]) < 1e-3:
            raise MeshError('arch top touches the wall top; make the wall taller than opening_height')
        pieces.append([p0, p1, (p1[0], z1), (p0[0], z1)])
    return mesh.region_solid(pieces, (s0, s1), axis == 'x', mat)
