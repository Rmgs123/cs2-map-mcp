"""add_yard: life in a yard between blocks of flats, with stock models only, on free ground.

Taken from a big town map built from photos (11.2026) and rewritten without its places:
- only on free ground: every mesh more than 12 above the ground, every model (by its measured footprint) and the spawns are taken
  first, paved surfaces are paths (a yard paved all over is a courtyard: everything may stand on it);
- bushes along the house walls (every ~3 m, never into the wall); flower beds of three painted tyres lying half in the
  ground with a low plant in each, by the paths; benches on the lawn at a path's edge facing the path (in a courtyard
  facing the middle), never facing a wall within 4 m, each with a bin at its end; washing lines on two T posts 260 apart;
  young trees on open lawn, 3 m off paths and walls, collision only in the trunk; a playground where there is room;
- decor is not solid (bushes, beds, lines); benches, bins, playground things and trunks are.

`area` is the yard outline; the ground is `z` or the `terrain` under each spot.
"""
from __future__ import annotations

import json
import math
import random
import re

import numpy as np

from . import structure
from .project import OpError

KEYS = {'op', 'name', 'area', 'z', 'terrain', 'paths', 'benches', 'beds', 'lines', 'trees', 'bushes', 'playground',
        'seed', 'ref'}
G = 16.0                                                     # raster cell
BUSHES = ['models/generic/plants/spindle_bush_kit/spindle_06_a.vmdl',
          'models/generic/plants/spindle_bush_kit/spindle_07_a.vmdl',
          'models/de_inferno/imported/imported_viburnum_davidii_bush_01.vmdl']
LOW_PLANT = 'models/de_overpass/foliage/eg_huckleberry/eg_huckleberry_bush_01.vmdl'
TREES = ['models/foliage/trees/urban/urban_tree_small_1.vmdl']
TYRE = 'models/props_urban/tire001.vmdl'
TYRE_PAINT = ['255 255 255', '90 140 220', '220 70 60', '240 200 60', '90 170 90']
BENCH = 'models/de_overpass/street/street_bench_1/street_bench_1.vmdl'       # seat faces +X
BIN = 'models/props/de_vostok/trashcan.vmdl'
LINE = 'models/props/de_inferno/hr_i/clothes_a/clothes_b.vmdl'             # the line along Y, origin on it
POST = 'materials/metal/hr_metal/hr_metal_corrugated_001_grey.vmat'
PLAY = [  # model, offset from the playground middle (x, y), yaw, solid
    ('models/props/de_overpass/overpass_swingset.vmdl', (-120, 110), 0, True),
    ('models/de_overpass/playground/playground_slide_01.fbm/playground_slide_01.vmdl', (130, 100), 90, True),
    ('models/de_overpass/playground/playground_carousel_01.vmdl', (-110, -130), 0, True),
    ('models/de_overpass/playground/playground_horse.vmdl', (120, -120), 30, True),
    ('models/de_overpass/playground/playground_horse.vmdl', (175, -60), 75, True),
]
PAVED = re.compile(r'asphalt|sidewalk|gravel|conc|tile|granite|paving|pavement|slab|curb|stone|brick|plank|wood')


def _pt(v, label):
    if (not isinstance(v, (list, tuple)) or len(v) != 2
            or any(isinstance(c, bool) or not isinstance(c, (int, float)) for c in v)):
        raise OpError(f'add_yard.{label}: points are [x, y]')
    return float(v[0]), float(v[1])


def _count(op, key, default, hi):
    v = op.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= hi:
        raise OpError(f'add_yard.{key}: a count 0..{hi}')
    return v


def _inside(poly, x, y):
    c = False
    for k in range(len(poly)):
        (x1, y1), (x2, y2) = poly[k], poly[(k + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


class _Raster:
    def __init__(self, lo, hi):
        self.x0, self.y0 = lo[0] - 256, lo[1] - 256
        self.nx, self.ny = int((hi[0] + 256 - self.x0) / G) + 1, int((hi[1] + 256 - self.y0) / G) + 1
        self.blocked = np.zeros((self.nx, self.ny), bool)       # anything standing there
        self.wall = np.zeros((self.nx, self.ny), bool)          # tall things: walls of houses
        self.paved = np.zeros((self.nx, self.ny), bool)
        self.area = np.zeros((self.nx, self.ny), bool)

    def ij(self, x, y):
        return int((x - self.x0) / G), int((y - self.y0) / G)

    def xy(self, i, j):
        return self.x0 + (i + 0.5) * G, self.y0 + (j + 0.5) * G

    def ok(self, i, j):
        return 0 <= i < self.nx and 0 <= j < self.ny

    def tri(self, grid, a, b, c):
        """Mark the cells a triangle covers seen from above (thin vertical faces: their edge line)."""
        pts = np.array([a[:2], b[:2], c[:2]], float)
        lo, hi = pts.min(0), pts.max(0)
        i0, j0 = self.ij(*lo)
        i1, j1 = self.ij(*hi)
        i0, j0, i1, j1 = max(0, i0), max(0, j0), min(self.nx - 1, i1), min(self.ny - 1, j1)
        if i0 > i1 or j0 > j1:
            return
        gi, gj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1), indexing='ij')
        px, py = self.x0 + (gi + 0.5) * G, self.y0 + (gj + 0.5) * G
        (ax, ay), (bx, by), (cx, cy) = pts
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(d) < 1e-6:                                      # a vertical face: mark along its longest edge
            e = max(((pts[0], pts[1]), (pts[1], pts[2]), (pts[0], pts[2])), key=lambda q: np.hypot(*(q[1] - q[0])))
            n = int(np.hypot(*(e[1] - e[0])) / (G / 2)) + 2
            for t in np.linspace(0, 1, n):
                i, j = self.ij(*(e[0] + (e[1] - e[0]) * t))
                if self.ok(i, j):
                    grid[i, j] = True
            return
        tol = G * 0.5 / max(1e-6, math.sqrt(abs(d)))
        l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
        l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
        grid[i0:i1 + 1, j0:j1 + 1] |= (l1 >= -tol) & (l2 >= -tol) & (1 - l1 - l2 >= -tol)

    def disc(self, grid, x, y, r):
        i, j = self.ij(x, y)
        k = int(math.ceil(r / G))
        for a in range(max(0, i - k), min(self.nx, i + k + 1)):
            for b in range(max(0, j - k), min(self.ny, j + k + 1)):
                cx, cy = self.xy(a, b)
                if (cx - x) ** 2 + (cy - y) ** 2 <= r * r:
                    grid[a, b] = True

    def free(self, x, y, r, lawn_only=False):
        """No obstacle (and, for lawn_only, no paving) within r of the point, inside the yard."""
        i, j = self.ij(x, y)
        if not self.ok(i, j) or not self.area[i, j]:
            return False
        k = int(math.ceil(r / G))
        a0, a1, b0, b1 = max(0, i - k), min(self.nx, i + k + 1), max(0, j - k), min(self.ny, j + k + 1)
        grid = self.blocked[a0:a1, b0:b1] | (self.paved[a0:a1, b0:b1] if lawn_only else False)
        if not grid.any():
            return True
        gi, gj = np.nonzero(grid)
        cx, cy = self.x0 + (gi + a0 + 0.5) * G, self.y0 + (gj + b0 + 0.5) * G
        return bool(((cx - x) ** 2 + (cy - y) ** 2 > r * r).all())

    def nearest(self, grid, x, y, r):
        """(distance, angle) to the nearest marked cell within r, or None."""
        i, j = self.ij(x, y)
        k = int(math.ceil(r / G))
        a0, a1, b0, b1 = max(0, i - k), min(self.nx, i + k + 1), max(0, j - k), min(self.ny, j + k + 1)
        gi, gj = np.nonzero(grid[a0:a1, b0:b1])
        if not len(gi):
            return None
        cx, cy = self.x0 + (gi + a0 + 0.5) * G, self.y0 + (gj + b0 + 0.5) * G
        d = np.hypot(cx - x, cy - y)
        n = int(d.argmin())
        if d[n] > r:
            return None
        return float(d[n]), math.atan2(cy[n] - y, cx[n] - x)


def _footprint(rec, origin, yaw, scale):
    """Corners of a model's footprint (physics near the ground, else render bounds) in world xy."""
    phys = (rec or {}).get('phys') or {}
    foot = (phys.get('bands') or {}).get('foot')
    if foot:
        lo, hi = (foot[0], foot[1]), (foot[2], foot[3])
    elif rec and rec.get('lo'):
        lo, hi = rec['lo'][:2], rec['hi'][:2]
    else:
        lo, hi = (-24, -24), (24, 24)
    a = math.radians(yaw)
    c, s = math.cos(a), math.sin(a)
    return [(origin[0] + (x * c - y * s) * scale, origin[1] + (x * s + y * c) * scale)
            for x, y in ((lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]), (lo[0], hi[1]))]


def add_yard(project, m, op, refs):
    from . import checks, propfacts, terrain
    from .builders import _ground
    from .ext.models import _trunk_core
    extra = set(op) - KEYS
    if extra:
        raise OpError(f'unknown add_yard fields {sorted(extra)}; allowed: {", ".join(sorted(KEYS - {"op"}))}')
    name = op.get('name', 'yard')
    if not isinstance(name, str) or not name.replace('_', '').isalnum() or len(name) > 48:
        raise OpError('add_yard.name: 1..48 chars a-z0-9_')
    area = op.get('area')
    if isinstance(area, dict) and set(area) == {'mins', 'maxs'}:
        (x0, y0), (x1, y1) = _pt(area['mins'][:2], 'area'), _pt(area['maxs'][:2], 'area')
        area = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    if not isinstance(area, list) or not 3 <= len(area) <= 64:
        raise OpError('add_yard.area: the yard outline, 3..64 points [x, y] (or {"mins", "maxs"})')
    poly = [_pt(p, 'area') for p in area]
    z_flat = op.get('z', 0)
    if isinstance(z_flat, bool) or not isinstance(z_flat, (int, float)):
        raise OpError('add_yard.z: the ground height where there is no terrain')
    if op.get('terrain') is not None:
        sample = _ground(m, {'terrain': op['terrain']})

        def gz(x, y):
            return sample(x, y, float(z_flat))
    else:
        def gz(x, y):
            return float(z_flat)
    paths = op.get('paths') or []
    if not isinstance(paths, list) or any(not isinstance(p, dict) or set(p) - {'path', 'width'} for p in paths):
        raise OpError('add_yard.paths: [{"path": [[x, y], ...], "width": 64}] where people walk (benches face them)')
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    size = (max(xs) - min(xs)) * (max(ys) - min(ys))
    per = max(1, round(size / 1.0e6))                         # ~25 x 25 m
    want = {'benches': _count(op, 'benches', min(8, 2 * per), 32), 'beds': _count(op, 'beds', min(6, per + 1), 32),
            'lines': _count(op, 'lines', min(2, per), 8), 'trees': _count(op, 'trees', min(10, 3 * per), 48),
            'bushes': _count(op, 'bushes', min(30, 8 * per), 96)}
    playground = bool(op.get('playground', False))
    rng = random.Random(op.get('seed', 1))
    R = _Raster((min(xs), min(ys)), (max(xs), max(ys)))
    for i in range(R.nx):
        for j in range(R.ny):
            R.area[i, j] = _inside(poly, *R.xy(i, j))
    zmid = gz((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)

    # what is there already: meshes above the ground, paved tops, models, spawns
    skip = set()
    for g in m.iter_nodes():
        if terrain.PLAN_KEY in g:
            skip |= {n.get('nodeID') for n in m.iter_nodes(g)}
    from .visual import _faces
    for node in m.iter_nodes():
        if node.type != 'CMapMesh' or node.get('nodeID') in skip:
            continue
        b = checks._mesh_bounds(node)
        if b is None or b[1][0] < R.x0 or b[0][0] > R.x0 + R.nx * G or b[1][1] < R.y0 or b[0][1] > R.y0 + R.ny * G:
            continue
        for pts, mat in _faces(node):
            if 'trigger' in mat or 'skybox' in mat:
                continue
            for k in range(1, len(pts) - 1):
                a, b_, c = pts[0], pts[k], pts[k + 1]
                zs = (a[2], b_[2], c[2])
                ground = gz((a[0] + b_[0] + c[0]) / 3, (a[1] + b_[1] + c[1]) / 3)
                if max(zs) < ground - 2:
                    continue                                    # buried under the terrain
                if max(zs) > ground + 12:                      # curbs and raised walks (up to 12) are walked on
                    R.tri(R.blocked, a, b_, c)
                    if max(zs) > ground + 96 and min(zs) < ground + 48:
                        R.tri(R.wall, a, b_, c)
                elif PAVED.search(mat):
                    n = np.cross(np.subtract(b_, a), np.subtract(c, a))
                    if n[2] > 0.7 * np.linalg.norm(n):
                        R.tri(R.paved, a, b_, c)
    from .blocks import KEEP_CLEAR
    for g in m.iter_nodes():                                    # in front of entrances and gates
        if KEEP_CLEAR in g:
            for q in json.loads(g[KEEP_CLEAR]):
                for k in range(1, len(q) - 1):
                    R.tri(R.blocked, (*q[0], 0), (*q[k], 0), (*q[k + 1], 0))
    for ent in m.entities():
        cls = m.classname(ent)
        props = ent['entity_properties']
        o = ent.get('origin') or (0, 0, 0)
        if cls.startswith('info_player'):
            R.disc(R.blocked, o[0], o[1], 48)
        model = str(props.get('model', '') or '')
        if model.endswith('.vmdl'):
            fp = _footprint(propfacts.get(model), o, float((ent.get('angles') or (0, 0, 0))[1]),
                            float((ent.get('scales') or (1, 1, 1))[0]))
            R.tri(R.blocked, (*fp[0], 0), (*fp[1], 0), (*fp[2], 0))
            R.tri(R.blocked, (*fp[0], 0), (*fp[2], 0), (*fp[3], 0))
    for p in paths:
        pts = [_pt(q, 'paths') for q in p.get('path') or []]
        w = p.get('width', 64)
        if len(pts) < 2 or isinstance(w, bool) or not isinstance(w, (int, float)) or not 16 <= w <= 1024:
            raise OpError('add_yard.paths: each {"path": 2+ points, "width": 16..1024}')
        for a, b in zip(pts, pts[1:]):
            n = int(math.dist(a, b) / (G / 2)) + 2
            for t in np.linspace(0, 1, n):
                R.disc(R.paved, a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, w / 2)
    in_area = R.area.sum()
    courtyard = in_area and (R.paved & R.area).sum() > 0.6 * in_area
    if courtyard:
        R.paved[:] = False                                      # paved all over: it is the yard itself
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)

    group = m.new_group('yard:' + name)
    structure._set_group_defaults(group)
    m.attach(group)
    defaults = project.catalog.defaults('prop_static')
    made = {k: 0 for k in ('bushes', 'beds', 'benches', 'bins', 'lines', 'trees', 'playground')}

    def put(model, x, y, z, yaw, scale=1.0, solid=True, kv=None, pitch=0.0):
        props = {'model': model, **({} if solid else {'solid': '0'}), **(kv or {})}
        ent = m.new_entity('prop_static', props, [round(x, 2), round(y, 2), round(z, 2)],
                           [pitch, round(yaw % 360, 2), 0.0], [round(scale, 3)] * 3, defaults=defaults)
        m.attach(ent, group)
        return ent

    def take(x, y, r):
        R.disc(R.blocked, x, y, r)

    def spots(n_try, r, lawn_only=True):
        cells = np.argwhere(R.area & ~R.blocked & (~R.paved if lawn_only else True))
        if not len(cells):
            return
        for _ in range(n_try):
            i, j = cells[rng.randrange(len(cells))]
            x, y = R.xy(int(i), int(j))
            x, y = x + rng.uniform(-G / 2, G / 2), y + rng.uniform(-G / 2, G / 2)
            if R.free(x, y, r, lawn_only):
                yield x, y

    # 1. a playground first: it needs the most room
    if playground:
        for x, y in spots(6000, 300):
            a = rng.uniform(0, 360)
            ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
            for model, (ox, oy), yaw, solid in PLAY:
                px, py = x + ox * ca - oy * sa, y + ox * sa + oy * ca
                put(model, px, py, gz(px, py) - 1, yaw + a, solid=solid)
                made['playground'] += 1
            take(x, y, 300)
            break
    # 2. bushes along the house walls
    for x, y in spots(20000, 40):
        if made['bushes'] >= want['bushes']:
            break
        near = R.nearest(R.wall, x, y, 110)
        if near is None or rng.random() > 0.55:
            continue
        model = rng.choice(BUSHES)
        rec = propfacts.get(model) or {}
        lo, hi = rec.get('lo', [-40, -40]), rec.get('hi', [40, 40])
        reach = max(abs(lo[0]), abs(lo[1]), abs(hi[0]), abs(hi[1]))
        scale = rng.uniform(0.7, 1.0)
        if near[0] < reach * scale * 0.6 + 8:                  # the crown may brush the wall, never go into it
            continue
        put(model, x, y, gz(x, y) - 3, rng.uniform(0, 360), scale, solid=False)
        take(x, y, 120 * scale)
        made['bushes'] += 1
    # 3. flower beds of painted tyres by the paths (anywhere in a courtyard)
    for x, y in spots(20000, 64):
        if made['beds'] >= want['beds']:
            break
        if not courtyard and R.nearest(R.paved, x, y, 120) is None:
            continue
        a = rng.uniform(0, 360)
        paint = rng.choice(TYRE_PAINT)
        for k in (-1, 0, 1):
            qx, qy = x + math.cos(math.radians(a)) * 33 * k, y + math.sin(math.radians(a)) * 33 * k
            tyre = put(TYRE, qx, qy, gz(qx, qy) + 1, rng.uniform(0, 360), solid=False, kv={'rendercolor': paint},
                       pitch=90)
            plant = put(LOW_PLANT, qx, qy, gz(qx, qy) + 2, rng.uniform(0, 360), 0.45, solid=False)
            plant.set('cs2map_inset', 'string', str(tyre['nodeID']))     # planted in the tyre on purpose
        take(x, y, 90)
        made['beds'] += 1
    # 4. benches facing the path (in a courtyard: the middle), never a wall; a bin at one end
    for x, y in spots(20000, 40):
        if made['benches'] >= want['benches']:
            break
        if courtyard:
            if R.nearest(R.wall, x, y, 64) is not None or math.dist((x, y), (cx, cy)) < 160:
                continue
            facing = math.atan2(cy - y, cx - x)
        else:
            near = R.nearest(R.paved, x, y, 64)
            if near is None or near[0] < 24:
                continue
            facing = near[1]
        ahead = [(x + math.cos(facing) * d, y + math.sin(facing) * d) for d in (60, 100, 140, 160)]
        if any(R.ok(*R.ij(*q)) and R.wall[R.ij(*q)] for q in ahead):
            continue                                            # a bench looking at a wall
        side = (-math.sin(facing), math.cos(facing))
        bx, by = x + side[0] * 62, y + side[1] * 62
        if not R.free(bx, by, 20, lawn_only=not courtyard):
            continue
        put(BENCH, x, y, gz(x, y) - 1, math.degrees(facing))
        put(BIN, bx, by, gz(bx, by) - 1, rng.uniform(0, 360))
        take(x, y, 90)
        take(bx, by, 30)
        made['benches'] += 1
        made['bins'] += 1
    # 5. washing lines: two T posts 260 apart on open lawn, the line with sheets between them
    posts = []
    for x, y in spots(20000, 60):
        if made['lines'] >= want['lines']:
            break
        a = rng.uniform(0, math.pi)
        t = (math.cos(a), math.sin(a))
        ends = [(x + t[0] * 130 * s, y + t[1] * 130 * s) for s in (-1, 1)]
        if not all(R.free(ex, ey, 40, lawn_only=not courtyard) for ex, ey in ends):
            continue
        if not all(R.free(x + t[0] * 130 * s, y + t[1] * 130 * s, 40, lawn_only=not courtyard) for s in (-0.5, 0.5)):
            continue
        top = max(gz(*e) for e in ends) + 102
        for ex, ey in ends:
            posts.append((ex, ey, gz(ex, ey) - 20, top + 2, t))
        put(LINE, x, y, top - 2, math.degrees(a) - 90, 260 / 256, solid=False)
        for ex, ey in ends:
            take(ex, ey, 60)
        take(x, y, 120)
        made['lines'] += 1
    # 6. young trees on open lawn, 3 m off paths and walls; collision in the trunk only
    cores = []
    for x, y in spots(20000, 118):
        if made['trees'] >= want['trees']:
            break
        model = rng.choice(TREES)
        scale = rng.uniform(0.8, 1.0)
        yaw = rng.uniform(0, 360)
        z = gz(x, y) - 5
        put(model, x, y, z, yaw, scale, solid=False)
        cores.append(_trunk_core(model, [x, y, z], [0, yaw, 0], scale))
        take(x, y, 236)
        made['trees'] += 1
    nodes = []
    if posts:                                                   # T posts: a pipe and a cross bar each, not solid
        from . import mesh
        from .blocks import _Frame, _box
        pm = mesh.Mesh()
        mat = structure._material_path(project, POST)
        for ex, ey, z0, z1, t in posts:
            fr = _Frame([ex, ey, 0.0], math.degrees(math.atan2(t[1], t[0])))
            _box(pm, fr, (-1.2, -1.2, z0), (1.2, 1.2, z1), mat)
            _box(pm, fr, (-1.0, -14, z1 - 2), (1.0, 14, z1 - 0.5), mat)  # the bar sinks into the post's top
        nodes.append(m.new_mesh(pm, texture_size=project.texture_size, options={'physicsType': 'none'}))
    group['children'] = list(group['children']) + nodes
    for core in cores:
        got = project._apply(m, core, {})
        for nid in got.get('ids') or []:
            node = m.node(nid)
            m.remove(node)
            m.attach(node, group)
    if op.get('ref'):
        refs[op['ref']] = group['nodeID']
    short = {k: (made[k], want[k]) for k in want if made[k] < want[k]}
    return {'op': 'add_yard', 'id': group['nodeID'], **made, 'courtyard': bool(courtyard),
            **({'short': short, 'hint': 'no free ground left for these (made, wanted): a bigger area, fewer items or '
                'paths for the benches'} if short else {}),
            **({'ref': op['ref']} if op.get('ref') else {})}
