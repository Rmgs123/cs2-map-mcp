"""add_garage_row: a row of lock-up garages (brick boxes, painted metal gates, flat roofs), on flat ground or a terrain.

Taken from a big town map built from photos (11.2026) and rewritten without its places:
- each box has its own floor: the ground at its gates + 2 (never sunk more than 12 under the highest point there);
  on a slope the row steps, and where the ground falls away the base shows as concrete;
- the closed boxes of a run are ONE stepped solid and the roof is another, so no two bodies overlap or share a face
  (boxes overlapping by a little left coplanar strips; shared corners of solids in one mesh break it);
- gate leaves sink 1 unit into the box, with a gap between them, hinges and a handle; colours vary from box to box;
- an open box (`open`) is hollow and can be walked into: its leaves are solid (a thin leaf without collision hid a
  player and let bullets through), one hangs open.

Frame: at yaw 0 the row runs along +X, `depth` along +Y, centred on `origin`; the gates face -Y.
"""
from __future__ import annotations

import json
import math
import random
import statistics

from . import mesh, structure
from .blocks import KEEP_CLEAR, _Frame, _box, _ring
from .project import OpError

KEYS = {'op', 'name', 'origin', 'yaw', 'count', 'box', 'open', 'gates', 'materials', 'terrain', 'seed', 'collision',
        'ref'}
MATERIALS = {'walls': 'materials/shared_base/brick/brick_soviet_white_01a.vmat',
             'base': 'materials/concrete/hr_c/hr_concrete_wall_004.vmat',
             'roof': 'materials/de_nuke/nukroofa.vmat',
             'hardware': 'materials/de_overpass/metal/rusty_metal_1.vmat'}
# gate sheets: stock profiled sheet in its own colours, the white one tinted (mesh tintColor works only on materials
# made for it: a blend material stayed grey, 16.11), and bare rust
SHEET = 'materials/metal/hr_metal/hr_metal_corrugated_001b_tint.vmat'
GATES = ['materials/metal/hr_metal/hr_metal_corrugated_001_blue.vmat',
         'materials/metal/hr_metal/hr_metal_corrugated_001_grey.vmat',
         {'material': SHEET, 'tint': [104, 132, 88]}, {'material': SHEET, 'tint': [136, 96, 72]},
         {'material': SHEET, 'tint': [150, 76, 62]}, 'materials/de_overpass/metal/rusty_metal_1.vmat']
WALL = 8.0
PILLAR = 10.0
GATE_LOW, GATE_HIGH = 4.0, 88.0


def _num(v, label, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise OpError(f'add_garage_row.{label} must be a number {lo}..{hi}')
    return float(v)


def _bar(m: mesh.Mesh, fr: _Frame, xs, bottoms, tops, y0, y1, material, scale, rows=()):
    """ONE closed solid over the intervals xs[i]..xs[i+1] (house coordinates), each from bottoms[i] to tops[i],
    y0..y1 deep: a stepped bar. Every face is split on the same heights, so the steps close without T-junctions.
    material(z_low, z_high, i) picks the material of a row of quads."""
    zs = sorted({*bottoms, *tops, *rows})
    n = len(xs) - 1
    ax, ay = fr.d(1, 0), fr.d(0, 1)
    along = {'u_axis': ax, 'v_axis': (0.0, 0.0, -1.0), 'scale': scale}
    across = {'u_axis': ay, 'v_axis': (0.0, 0.0, -1.0), 'scale': scale}
    flat = {'u_axis': ax, 'v_axis': ay, 'scale': scale}

    def P(x, y, z):
        return fr.p(x, y, z)

    def rows_of(i):
        return [(a, b) for a, b in zip(zs, zs[1:]) if bottoms[i] <= a and b <= tops[i]]

    for i in range(n):
        x0, x1 = xs[i], xs[i + 1]
        for a, b in rows_of(i):
            mat = material(a, b, i)
            m.face_out([P(x0, y0, a), P(x1, y0, a), P(x1, y0, b), P(x0, y0, b)], (-ay[0], -ay[1], 0.0), mat, **along)
            m.face_out([P(x0, y1, a), P(x1, y1, a), P(x1, y1, b), P(x0, y1, b)], (ay[0], ay[1], 0.0), mat, **along)
        m.face_out([P(x0, y0, tops[i]), P(x1, y0, tops[i]), P(x1, y1, tops[i]), P(x0, y1, tops[i])], (0.0, 0.0, 1.0),
                   material(tops[i], tops[i] + 1, i), **flat)
        m.face_out([P(x0, y0, bottoms[i]), P(x1, y0, bottoms[i]), P(x1, y1, bottoms[i]), P(x0, y1, bottoms[i])],
                   (0.0, 0.0, -1.0), material(bottoms[i] - 1, bottoms[i], i), **flat)
    # ends and risers: on each boundary the heights covered by one side only
    for k in range(n + 1):
        left = (bottoms[k - 1], tops[k - 1]) if k > 0 else None
        right = (bottoms[k], tops[k]) if k < n else None
        for a, b in zip(zs, zs[1:]):
            in_l = left is not None and left[0] <= a and b <= left[1]
            in_r = right is not None and right[0] <= a and b <= right[1]
            if in_l == in_r:
                continue
            side = k - 1 if in_l else k
            out = (ax[0], ax[1], 0.0) if in_l else (-ax[0], -ax[1], 0.0)
            m.face_out([P(xs[k], y0, a), P(xs[k], y1, a), P(xs[k], y1, b), P(xs[k], y0, b)], out,
                       material(a, b, side), **across)


def _sheet_uv(fr: _Frame, along, z_top, scale=(0.25, 0.25)):
    """Texture axes of a gate face: u along the leaf, v down, the pattern's top edge on the leaf's top (stock
    profiled sheets carry their rivet line along the top of the texture; world-aligned it fell mid-gate)."""
    return {'u_axis': along, 'v_axis': (0.0, 0.0, -1.0), 'scale': scale,
            'offset': (0.0, round((fr.o[2] + z_top) / scale[1], 3))}


def _gate(m: mesh.Mesh, fr: _Frame, lo, hi, material):
    """An axis-aligned leaf (house coordinates) with the sheet pattern from its top edge."""
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    c = [fr.p(x, y, z) for x, y, z in ((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                                         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))]
    ax, ay = fr.d(1, 0), fr.d(0, 1)
    m.face_out([c[i] for i in (0, 1, 2, 3)], (0, 0, -1), material)
    m.face_out([c[i] for i in (4, 5, 6, 7)], (0, 0, 1), material)
    for idx, out, along in (((0, 1, 5, 4), (-ay[0], -ay[1], 0), ax), ((2, 3, 7, 6), (ay[0], ay[1], 0), ax),
                            ((1, 2, 6, 5), (ax[0], ax[1], 0), ay), ((3, 0, 4, 7), (-ax[0], -ax[1], 0), ay)):
        m.face_out([c[i] for i in idx], out, material, **_sheet_uv(fr, along, z1))


def _leaf(m: mesh.Mesh, fr: _Frame, hinge, angle, width, thick, z0, z1, material):
    """A gate leaf turned `angle` degrees about its hinge (house coordinates; 0 = along +X, closed)."""
    a = math.radians(angle)
    u, v = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))
    base = [(hinge[0] + u[0] * s + v[0] * t, hinge[1] + u[1] * s + v[1] * t)
            for s, t in ((0, 0), (width, 0), (width, thick), (0, thick))]
    lo = [fr.p(x, y, z0) for x, y in base]
    hi = [fr.p(x, y, z1) for x, y in base]
    wu, wv = fr.d(*u), fr.d(*v)
    m.face_out(lo, (0.0, 0.0, -1.0), material)
    m.face_out(hi, (0.0, 0.0, 1.0), material)
    for i, out, along in ((0, (-wv[0], -wv[1], 0.0), wu), (1, (wu[0], wu[1], 0.0), wv), (2, (wv[0], wv[1], 0.0), wu),
                          (3, (-wu[0], -wu[1], 0.0), wv)):
        j = (i + 1) % 4
        m.face_out([lo[i], lo[j], hi[j], hi[i]], out, material, **_sheet_uv(fr, along, z1))


def add_garage_row(project, m, op, refs):
    extra = set(op) - KEYS
    if extra:
        raise OpError(f'unknown add_garage_row fields {sorted(extra)}; allowed: {", ".join(sorted(KEYS - {"op"}))}')
    name = op.get('name', 'garages')
    if not isinstance(name, str) or not name.replace('_', '').isalnum() or len(name) > 48:
        raise OpError('add_garage_row.name: 1..48 chars a-z0-9_')
    origin = op.get('origin')
    if (not isinstance(origin, list) or len(origin) != 3
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in origin)):
        raise OpError('add_garage_row.origin is [x, y, z]: the middle of the row on the ground')
    yaw = _num(op.get('yaw', 0), 'yaw', -360, 360)
    count = int(_num(op.get('count', 6), 'count', 1, 60))
    size = op.get('box', [136, 240, 104])
    if not isinstance(size, list) or len(size) != 3:
        raise OpError('add_garage_row.box is [width, depth, height] of one box')
    bw, bd, bh = (_num(size[0], 'box width', 96, 240), _num(size[1], 'box depth', 160, 480),
                  _num(size[2], 'box height', GATE_HIGH + 12, 192))
    opened = op.get('open', [])
    if not isinstance(opened, list) or not all(isinstance(i, int) and not isinstance(i, bool) and 0 <= i < count
                                               for i in opened):
        raise OpError(f'add_garage_row.open: box numbers 0..{count - 1} that stand open (hollow, walk-in)')
    opened = set(opened)
    palette = []
    raw = op.get('gates', GATES)
    if not isinstance(raw, list) or not 1 <= len(raw) <= 16:
        raise OpError('add_garage_row.gates: 1..16 gate looks, each a material or {"material", "tint": [r, g, b]}')
    for g in raw:
        tint = None
        if isinstance(g, dict):
            if set(g) - {'material', 'tint'} or 'material' not in g:
                raise OpError('add_garage_row.gates: {"material": ..., "tint": [r, g, b] 0..255}')
            tint = g.get('tint')
            if tint is not None and (not isinstance(tint, list) or len(tint) != 3 or not all(
                    isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 255 for x in tint)):
                raise OpError('add_garage_row.gates tint is [r, g, b] 0..255 (works on materials made for tint)')
            g = g['material']
        if not isinstance(g, str):
            raise OpError('add_garage_row.gates: each a material path or {"material", "tint"}')
        palette.append((structure._material_path(project, g), tuple(int(x) for x in tint) if tint else None))
    mats = dict(MATERIALS)
    own = op.get('materials') or {}
    if not isinstance(own, dict) or set(own) - set(mats):
        raise OpError(f'add_garage_row.materials keys: {", ".join(sorted(mats))}')
    mats.update(own)
    mats = {k: structure._material_path(project, v) for k, v in mats.items()}
    collision = bool(op.get('collision', True))
    rng = random.Random(op.get('seed', 1))
    fr = _Frame([float(v) for v in origin], yaw)
    L = count * bw
    xs = [-L / 2 + i * bw for i in range(count + 1)]
    y0, y1 = -bd / 2, bd / 2

    # floors: flat, or the terrain under each box (relative to origin z)
    if op.get('terrain') is not None:
        from .builders import _ground
        ground = _ground(m, {'terrain': op['terrain']})

        def gz(x, y):
            w = fr.p(x, y, 0.0)
            return ground(w[0], w[1], None) - origin[2]
    else:
        def gz(x, y):
            return 0.0
    floors, lows, thresholds = [], [], {}
    for i in range(count):
        gates = [gz(x, y0) for x in (xs[i] + 8, (xs[i] + xs[i + 1]) / 2, xs[i + 1] - 8)]
        under = [gz(x, y) for x in (xs[i], (xs[i] + xs[i + 1]) / 2, xs[i + 1]) for y in (y0, y0 / 2, 0.0, y1 / 2, y1)]
        f = max(statistics.median(gates) + 2, max(gates) - 12)
        if i in opened:                                     # a floor people walk on: over the ground everywhere
            f = max(f, max(under) + 2)
            if f - min(gates) > 16:
                thresholds[i] = round(f - min(gates), 1)
        floors.append(round(f, 2))
        lows.append(min(under))
    bottoms = [round(min(f - 6, lo - 20), 2) for f, lo in zip(floors, lows)]
    tops = [f + bh for f in floors]                         # flush under the roof: sunk in, the steps doubled faces
    scale = (0.25, 0.25)

    def wall_mat(a, b, i):
        return mats['base'] if b <= floors[i] + 0.01 else mats['walls']

    walls, details, roof = mesh.Mesh(), mesh.Mesh(), mesh.Mesh()
    gates = {}                                              # tint -> mesh
    solid_leaves, hardware, floors_mesh = mesh.Mesh(), mesh.Mesh(), mesh.Mesh()
    rings = []
    i = 0
    while i < count:                                        # closed runs: one stepped solid each
        if i in opened:
            i += 1
            continue
        j = i
        while j < count and j not in opened:
            j += 1
        _bar(walls, fr, xs[i:j + 1], bottoms[i:j], tops[i:j], y0, y1,
             lambda a, b, k, i=i: wall_mat(a, b, i + k), scale, rows=floors[i:j])
        i = j
    # the roof: one stepped slab over the whole row, 14 over the gates, 4 at the back and the ends; every other
    # box 1 unit thicker, like separate slabs
    rx = [xs[0] - 4, *xs[1:-1], xs[-1] + 4]
    _bar(roof, fr, rx, [f + bh for f in floors], [f + bh + 6 + (k % 2) for k, f in enumerate(floors)],
         y0 - 14, y1 + 4, lambda a, b, k: mats['roof'], scale)
    plain = [g for g in palette if g[1] is None]
    for k in range(count):
        f = floors[k]
        x0, x1 = xs[k], xs[k + 1]
        mid = (x0 + x1) / 2
        look = palette[rng.randrange(len(palette))]
        if k in opened and look[1] is not None and plain:   # walk-in leaves share one solid mesh: no tint there
            look = plain[rng.randrange(len(plain))]
        if k in opened:
            ring = mesh.Mesh()
            # the front side runs from the left corner: the gate opening between the pillars, from the floor
            _ring(ring, fr, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                  [[(PILLAR, bw - PILLAR, f, f + GATE_HIGH)], [], [], []], bottoms[k], tops[k], WALL,
                  lambda a, b, k=k: wall_mat(a, b, k), scale, rows=[f])
            rings.append(ring)
            # the floor fills the ring down to its bottom: no air under it on a slope
            _box(floors_mesh, fr, (x0 + WALL, y0 + WALL, bottoms[k]), (x1 - WALL, y1 - WALL, f), mats['base'])
            leaf_w = (bw - 2 * PILLAR) / 2 - 0.75
            mat = look[0]
            # the left leaf hangs open outwards, the right one is shut in the opening (1 unit into reveal and lintel)
            _leaf(solid_leaves, fr, (x0 + PILLAR + 0.5, y0 + 0.5), -100 - rng.uniform(0, 25), leaf_w, 2.5,
                  f + GATE_LOW, f + GATE_HIGH - 4, mat)
            _gate(solid_leaves, fr, (mid + 0.75, y0 + 2, f + GATE_LOW), (x1 - PILLAR + 1, y0 + 4.5, f + GATE_HIGH + 1),
                  mat)
            continue
        target = gates.setdefault(look, mesh.Mesh())
        mat = look[0]
        for a, b in ((x0 + PILLAR, mid - 0.75), (mid + 0.75, x1 - PILLAR)):
            _gate(target, fr, (a, y0 - 2, f + GATE_LOW), (b, y0 + 1, f + GATE_HIGH), mat)
        for hx in (x0 + PILLAR + 1.5, x1 - PILLAR - 1.5):  # hinges at the outer edges, 1 unit into the leaves
            for hz in (f + 16, f + GATE_HIGH - 18):
                _box(hardware, fr, (hx - 1.2, y0 - 3.8, hz), (hx + 1.2, y0 - 1, hz + 6), mats['hardware'])
        _box(hardware, fr, (mid + 4, y0 - 3.5, f + 38), (mid + 5.2, y0 - 1, f + 48), mats['hardware'])  # handle

    group = m.new_group('garage_row:' + name)
    structure._set_group_defaults(group)
    drive = [fr.p(x, y, 0.0)[:2] for x, y in ((xs[0] - 24, y0), (xs[-1] + 24, y0), (xs[-1] + 24, y0 - 200),
                                              (xs[0] - 24, y0 - 200))]
    group.set(KEEP_CLEAR, 'string', json.dumps([[[round(v, 1) for v in q] for q in drive]]))   # cars drive up to the gates
    m.attach(group)
    solid = {'physicsType': 'default' if collision else 'none'}
    flat = {'physicsType': 'none'}
    ts = project.texture_size
    nodes = []
    if walls.faces:
        nodes.append(m.new_mesh(walls, texture_size=ts, options=solid))
    nodes += [m.new_mesh(r, texture_size=ts, options=solid) for r in rings]
    nodes.append(m.new_mesh(roof, texture_size=ts, options=solid))
    if floors_mesh.faces:
        nodes.append(m.new_mesh(floors_mesh, texture_size=ts, options=solid))
    if solid_leaves.faces:
        nodes.append(m.new_mesh(solid_leaves, texture_size=ts, options=solid))
    for (_, tint), gm in gates.items():
        opts = dict(flat)
        if tint:
            opts['tintColor'] = (*tint, 255)
        nodes.append(m.new_mesh(gm, texture_size=ts, options=opts))
    if hardware.faces:
        nodes.append(m.new_mesh(hardware, texture_size=ts, options=flat))
    group['children'] = list(group['children']) + nodes
    if op.get('ref'):
        refs[op['ref']] = group['nodeID']
    return {'op': 'add_garage_row', 'id': group['nodeID'], 'boxes': count, 'open': sorted(opened),
            'length': L, 'floors': [round(f + origin[2], 1) for f in floors], 'meshes': len(nodes),
            **({'thresholds': thresholds, 'hint': 'open boxes whose floor stands over 16 units above the ground at '
                'their gates: flatten the terrain there (add_terrain flatten) or players cannot step in'}
               if thresholds else {}),
            **({'ref': op['ref']} if op.get('ref') else {})}
