"""add_block_house: a multi-storey block of flats (concrete panels, white silicate brick, red brick or plaster).

Built the way a big town map from photos showed works (11.2026): the outer walls are one closed solid with mitred
corners whose faces are grids of quads with the openings left out (no boolean cuts: many windows cut into one body
failed; walls sunk into each other left coplanar strips at the corners), openings have reveals, window frames sink 1 unit into the reveal, glass stands 1 unit behind the frame, a dark solid core
stands behind the walls, the flat roof has a parapet with a coping along the outer outline and a stair-house over
every entrance. Entrances are doors at the ground with a canopy and the stair windows half a floor lower than the
flats' windows above them. Stock materials only, so it looks the same on every PC.

Frame: at yaw 0 the house is `length` long along +X and `depth` deep along +Y, centred on `origin` (its ground);
the front (entrances) faces -Y. yaw turns it like any entity.
"""
from __future__ import annotations

import json
import math
import random

from . import mesh, structure
from .project import OpError

KEYS = {'op', 'name', 'origin', 'yaw', 'length', 'depth', 'floors', 'floor_height', 'plinth', 'finish', 'entrances',
        'window', 'step', 'balconies', 'end_windows', 'materials', 'parapet', 'seed', 'collision', 'door', 'ref'}
DOOR = 'models/de_cache_s2/doors/ch2_door_001.vmdl'   # a worn painted metal leaf, 57 x 112
DOOR_AT = 8.0        # the door leaf's middle plane, from the wall face
FINISHES = {
    'panel': {'walls': 'materials/shared_base/concrete/concrete_panel_001.vmat',
              'plinth': 'materials/concrete/hr_c/hr_concrete_wall_004.vmat'},
    'silicate': {'walls': 'materials/shared_base/brick/brick_soviet_white_01a.vmat',
                 'plinth': 'materials/concrete/hr_c/hr_concrete_wall_004.vmat'},
    'brick': {'walls': 'materials/de_cache/brick/ch2_brick_01_base.vmat',
              'plinth': 'materials/concrete/hr_c/hr_concrete_wall_004.vmat'},
    'plaster': {'walls': 'materials/shared_base/plaster/plaster_soviet_01a.vmat',
                'plinth': 'materials/concrete/hr_c/hr_concrete_wall_004.vmat'},
}
COMMON = {'frame': 'materials/de_overpass/concrete/painted_concrete_1_white.vmat',
          'glass': 'materials/glass/window_opaque_1b.vmat',
          'core': 'materials/tools/toolsblack.vmat',
          'trim': 'materials/concrete/hr_c/hr_concrete_wall_005.vmat',
          'roof': 'materials/concrete/hr_c/hr_concrete_wall_001.vmat'}
WALL = 16.0          # wall thickness
# a group's ground that must stay free (entrances, gates): polygons [[x, y], ...] as JSON; dressers keep off it
KEEP_CLEAR = 'cs2map_keepclear'
FRAME_AT = 5.0       # frame depth into the reveal
FRAME_T = 3.0
FRAME_W = 3.0


def _num(v, label, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise OpError(f'add_block_house.{label} must be a number {lo}..{hi}')
    return float(v)


class _Frame:
    """Local house coordinates -> world: x along the length, y across, z up; rotated by yaw about origin."""

    def __init__(self, origin, yaw):
        self.o = origin
        a = math.radians(yaw)
        self.c, self.s = math.cos(a), math.sin(a)

    def p(self, x, y, z):
        return (round(self.o[0] + x * self.c - y * self.s, 3), round(self.o[1] + x * self.s + y * self.c, 3),
                round(self.o[2] + z, 3))

    def d(self, x, y):
        return (x * self.c - y * self.s, x * self.s + y * self.c, 0.0)


def _slab(m: mesh.Mesh, fr: _Frame, start, u, n, length, z0, z1, holes, thick, materials, scale, rows=None):
    """A closed wall slab: outer face on the line start + u*s (s in 0..length), thick inwards (-n), z0..z1, with
    rectangular openings holes [(s0, s1, v0, v1)]. materials(v_low, v_high) picks the material of a row of quads."""
    us = sorted({0.0, length, *[h[0] for h in holes], *[h[1] for h in holes]})
    vs = sorted({z0, z1, *[h[2] for h in holes], *[h[3] for h in holes], *(rows or [])})
    vs = [v for v in vs if z0 <= v <= z1]

    def P(s, v, depth):
        x = start[0] + u[0] * s - n[0] * depth
        y = start[1] + u[1] * s - n[1] * depth
        return fr.p(x, y, v)

    nu, nn = fr.d(*u), fr.d(*n)
    wall_uv = {'u_axis': nu, 'v_axis': (0.0, 0.0, -1.0), 'scale': scale}
    # faces that look along the wall (reveal sides, slab ends): the texture runs across the wall's depth. With the
    # wall's own u axis the projection collapses and the texture streaks (seen in CS2 16.11)
    side_uv = {'u_axis': nn, 'v_axis': (0.0, 0.0, -1.0), 'scale': scale}
    # soffits and sills: along the wall and across its depth, so brick courses follow the wall
    flat_uv = {'u_axis': nu, 'v_axis': nn, 'scale': scale}

    def hole_at(s, v):
        return any(h[0] <= s <= h[1] and h[2] <= v <= h[3] for h in holes)

    for i in range(len(us) - 1):
        for j in range(len(vs) - 1):
            sm, vm = (us[i] + us[i + 1]) / 2, (vs[j] + vs[j + 1]) / 2
            if hole_at(sm, vm):
                continue
            mat = materials(vs[j], vs[j + 1])
            m.face_out([P(us[i], vs[j], 0), P(us[i + 1], vs[j], 0), P(us[i + 1], vs[j + 1], 0), P(us[i], vs[j + 1], 0)],
                       nn, mat, **wall_uv)
            m.face_out([P(us[i], vs[j], thick), P(us[i + 1], vs[j], thick), P(us[i + 1], vs[j + 1], thick),
                        P(us[i], vs[j + 1], thick)], (-nn[0], -nn[1], 0.0), mat, **wall_uv)
    # outer border: bottom and top per u interval, the two ends per v interval
    for i in range(len(us) - 1):
        for v, out in ((vs[0], -1.0), (vs[-1], 1.0)):
            if hole_at((us[i] + us[i + 1]) / 2, v):
                continue
            mat = materials(v - 1, v) if out < 0 else materials(v, v + 1)
            m.face_out([P(us[i], v, 0), P(us[i + 1], v, 0), P(us[i + 1], v, thick), P(us[i], v, thick)],
                       (0.0, 0.0, out), mat, **flat_uv)
    for j in range(len(vs) - 1):
        for s, sign in ((us[0], -1.0), (us[-1], 1.0)):
            if hole_at(s, (vs[j] + vs[j + 1]) / 2):
                continue
            mat = materials(vs[j], vs[j + 1])
            m.face_out([P(s, vs[j], 0), P(s, vs[j + 1], 0), P(s, vs[j + 1], thick), P(s, vs[j], thick)],
                       (nu[0] * sign, nu[1] * sign, 0.0), mat, **side_uv)
    # reveals of every opening, split on the grid lines inside it
    for s0, s1, v0, v1 in holes:
        inner_v = [v for v in vs if v0 <= v <= v1]
        inner_u = [s for s in us if s0 <= s <= s1]
        for a, b in zip(inner_v, inner_v[1:]):
            mat = materials(a, b)
            if s0 > 0:
                m.face_out([P(s0, a, 0), P(s0, b, 0), P(s0, b, thick), P(s0, a, thick)], nu, mat, **side_uv)
            if s1 < length:
                m.face_out([P(s1, a, 0), P(s1, b, 0), P(s1, b, thick), P(s1, a, thick)],
                           (-nu[0], -nu[1], 0.0), mat, **side_uv)
        for a, b in zip(inner_u, inner_u[1:]):
            if v0 > z0:
                m.face_out([P(a, v0, 0), P(b, v0, 0), P(b, v0, thick), P(a, v0, thick)], (0.0, 0.0, 1.0),
                           materials(v0, v0 + 1), **flat_uv)
            if v1 < z1:
                m.face_out([P(a, v1, 0), P(b, v1, 0), P(b, v1, thick), P(a, v1, thick)], (0.0, 0.0, -1.0),
                           materials(v1 - 1, v1), **flat_uv)


def _ring(m: mesh.Mesh, fr: _Frame, corners, holes, z0, z1, thick, materials, scale, rows=()):
    """The outer walls as ONE closed solid round a rectangle: corners counter-clockwise seen from above (house
    coordinates), holes[k] = openings [(s0, s1, v0, v1)] of the side from corners[k] (s from that corner, at least
    thick + 1 from both ends). The corners are mitred, so no wall overlaps another: two walls sunk 1 unit into each
    other left a strip of coplanar faces with crossed textures down every corner (16.11). Every side is split on the
    same rows, so the corner edges match and the solid is closed."""
    vs = sorted({z0, z1, *rows, *[h[2] for hs in holes for h in hs], *[h[3] for hs in holes for h in hs]})
    vs = [v for v in vs if z0 <= v <= z1]
    for k, A in enumerate(corners):
        B = corners[(k + 1) % len(corners)]
        length = math.hypot(B[0] - A[0], B[1] - A[1])
        u = ((B[0] - A[0]) / length, (B[1] - A[1]) / length)
        n = (u[1], -u[0])                                   # outward for a counter-clockwise outline
        hs = holes[k]
        if any(h[0] < thick + 1 or h[1] > length - thick - 1 for h in hs):
            raise OpError('an opening reaches into a corner of the walls')
        us = sorted({0.0, length, *[h[0] for h in hs], *[h[1] for h in hs]})
        ui = sorted({thick, length - thick, *[s for s in us if thick < s < length - thick]})

        def P(s, v, depth, A=A, u=u, n=n):
            return fr.p(A[0] + u[0] * s - n[0] * depth, A[1] + u[1] * s - n[1] * depth, v)

        nu, nn = fr.d(*u), fr.d(*n)
        wall_uv = {'u_axis': nu, 'v_axis': (0.0, 0.0, -1.0), 'scale': scale}
        side_uv = {'u_axis': nn, 'v_axis': (0.0, 0.0, -1.0), 'scale': scale}
        flat_uv = {'u_axis': nu, 'v_axis': nn, 'scale': scale}

        def hole_at(s, v, hs=hs):
            return any(h[0] <= s <= h[1] and h[2] <= v <= h[3] for h in hs)

        for line, depth, out in ((us, 0.0, nn), (ui, thick, (-nn[0], -nn[1], 0.0))):
            for i in range(len(line) - 1):
                for j in range(len(vs) - 1):
                    if hole_at((line[i] + line[i + 1]) / 2, (vs[j] + vs[j + 1]) / 2):
                        continue
                    m.face_out([P(line[i], vs[j], depth), P(line[i + 1], vs[j], depth), P(line[i + 1], vs[j + 1], depth),
                                P(line[i], vs[j + 1], depth)], out, materials(vs[j], vs[j + 1]), **wall_uv)
        # bottom and top: the strip between the outer and the inner line, mitred at the corners
        for i in range(len(us) - 1):
            a, b = us[i], us[i + 1]
            for v, out in ((vs[0], -1.0), (vs[-1], 1.0)):
                if hole_at((a + b) / 2, v):
                    continue
                mat = materials(v - 1, v) if out < 0 else materials(v, v + 1)
                m.face_out([P(a, v, 0), P(b, v, 0), P(min(b, length - thick), v, thick), P(max(a, thick), v, thick)],
                           (0.0, 0.0, out), mat, **flat_uv)
        for s0, s1, v0, v1 in hs:                           # reveals, split on the grid lines inside them
            inner_v = [v for v in vs if v0 <= v <= v1]
            inner_u = [s for s in us if s0 <= s <= s1]
            for a, b in zip(inner_v, inner_v[1:]):
                mat = materials(a, b)
                m.face_out([P(s0, a, 0), P(s0, b, 0), P(s0, b, thick), P(s0, a, thick)], nu, mat, **side_uv)
                m.face_out([P(s1, a, 0), P(s1, b, 0), P(s1, b, thick), P(s1, a, thick)],
                           (-nu[0], -nu[1], 0.0), mat, **side_uv)
            for a, b in zip(inner_u, inner_u[1:]):
                if v0 > z0:
                    m.face_out([P(a, v0, 0), P(b, v0, 0), P(b, v0, thick), P(a, v0, thick)], (0.0, 0.0, 1.0),
                               materials(v0, v0 + 1), **flat_uv)
                if v1 < z1:
                    m.face_out([P(a, v1, 0), P(b, v1, 0), P(b, v1, thick), P(a, v1, thick)], (0.0, 0.0, -1.0),
                               materials(v1 - 1, v1), **flat_uv)


def _door_fit(project, model, max_h, max_w):
    """Measure a stock door model and fit it to the entrance: (scale, width axis, lo, hi, opening w, opening h).
    The opening is 1 unit narrower than the leaf on each side and 2 lower, so the leaf sinks into the reveals."""
    from . import propfacts
    rec = propfacts.get(model)
    if rec and rec.get('lo'):
        lo, hi = rec['lo'], rec['hi']
    else:
        from .facades import _model_bounds
        lo, hi = _model_bounds(project, model)
    wx, wy, h = hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]
    axis = 0 if wx >= wy else 1
    width, thick = max(wx, wy), min(wx, wy)
    if width < 24 or h < 48:
        raise OpError(f'add_block_house.door: {model} is {width:.0f} x {h:.0f}, not a door')
    sc = min(1.0, max_h / h, max_w / width)
    if thick * sc > 2 * (WALL - DOOR_AT) - 2:
        raise OpError(f'add_block_house.door: {model} is {thick * sc:.0f} units thick, the wall {WALL:g}')
    return sc, axis, lo, hi, width * sc - 2, h * sc - 2


def _box(m: mesh.Mesh, fr: _Frame, lo, hi, material):
    """An axis-aligned box in house coordinates, turned with the house."""
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    c = [fr.p(x, y, z) for x, y, z in ((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                                         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))]
    ax, ay = fr.d(1, 0), fr.d(0, 1)
    for idx, out in (((0, 1, 2, 3), (0, 0, -1)), ((4, 5, 6, 7), (0, 0, 1)), ((0, 1, 5, 4), (-ay[0], -ay[1], 0)),
                     ((2, 3, 7, 6), (ay[0], ay[1], 0)), ((1, 2, 6, 5), (ax[0], ax[1], 0)),
                     ((3, 0, 4, 7), (-ax[0], -ax[1], 0))):
        m.face_out([c[i] for i in idx], out, material)


def add_block_house(project, m, op, refs):
    extra = set(op) - KEYS
    if extra:
        raise OpError(f'unknown add_block_house fields {sorted(extra)}; allowed: {", ".join(sorted(KEYS - {"op"}))}')
    name = op.get('name', 'block')
    if not isinstance(name, str) or not name.replace('_', '').isalnum() or len(name) > 48:
        raise OpError('add_block_house.name: 1..48 chars a-z0-9_')
    origin = op.get('origin')
    if (not isinstance(origin, list) or len(origin) != 3
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in origin)):
        raise OpError('add_block_house.origin is [x, y, z]: the middle of its footprint on the ground')
    yaw = _num(op.get('yaw', 0), 'yaw', -360, 360)
    floors = int(_num(op.get('floors', 5), 'floors', 1, 16))
    fh = _num(op.get('floor_height', 112), 'floor_height', 96, 192)
    plinth = _num(op.get('plinth', 32), 'plinth', 0, 96)
    step = _num(op.get('step', 112), 'step', 72, 256)
    win = op.get('window', [min(56.0, step - 24), min(60.0, fh - 40)])     # the default fits low floors too
    if not isinstance(win, list) or len(win) != 2:
        raise OpError('add_block_house.window is [width, height]')
    ww, wh = _num(win[0], 'window width', 24, step - 24), _num(win[1], 'window height', 24, fh - 40)
    length = _num(op.get('length', step * 12), 'length', step * 2, 8192)
    depth = _num(op.get('depth', 448), 'depth', 192, 2048)
    finish = op.get('finish', 'panel')
    if finish not in FINISHES:
        raise OpError(f'add_block_house.finish: {", ".join(FINISHES)}')
    mats = {**COMMON, **FINISHES[finish]}
    own = op.get('materials') or {}
    if not isinstance(own, dict) or set(own) - set(mats):
        raise OpError(f'add_block_house.materials keys: {", ".join(sorted(mats))}')
    mats.update(own)
    mats = {k: structure._material_path(project, v) for k, v in mats.items()}
    parapet = _num(op.get('parapet', 24), 'parapet', 0, 96)
    balconies = _num(op.get('balconies', 0.0), 'balconies', 0, 1)
    end_windows = bool(op.get('end_windows', False))
    collision = bool(op.get('collision', True))
    rng = random.Random(op.get('seed', 1))
    slots = int(length // step)
    pad = (length - slots * step) / 2
    entrances = op.get('entrances', max(1, slots // 6))
    if isinstance(entrances, (int, float)) and not isinstance(entrances, bool):
        k = int(entrances)
        if not 0 <= k <= slots:
            raise OpError(f'add_block_house.entrances: 0..{slots} (one slot of {step:g} units each)')
        ent_slots = sorted({int((i + 0.5) * slots / k) for i in range(k)}) if k else []
    elif isinstance(entrances, list) and all(isinstance(f, (int, float)) and 0 <= f <= 1 for f in entrances):
        ent_slots = sorted({min(slots - 1, int(f * slots)) for f in entrances})
    else:
        raise OpError('add_block_house.entrances: a count, or fractions 0..1 along the front')
    z_top = plinth + floors * fh
    z_wall = z_top + parapet
    L2, D2 = length / 2, depth / 2
    fr = _Frame([float(v) for v in origin], yaw)
    scale = (0.25, 0.25)

    def wall_mats(a, b):
        return mats['plinth'] if b <= plinth + 0.5 else mats['walls']

    walls, details, glass = mesh.Mesh(), mesh.Mesh(), mesh.Mesh()
    door_model = op.get('door', DOOR)
    if not isinstance(door_model, str) or not door_model.startswith('models/') or not door_model.endswith('.vmdl'):
        raise OpError('add_block_house.door is a model path models/....vmdl (a stock door leaf)')
    # the door stays under the first floor's ceiling and inside its slot
    d_sc, d_axis, d_lo, d_hi, door_w, door_h = _door_fit(project, door_model, plinth + fh - 16, step - 16)
    canopy = door_h + 10                                # its underside; 8 thick
    doors = []
    keep = []                                           # ground to keep clear in front of the entrances (yards read it)
    windows = 0

    def clear_of_corners(c, half, side_len):            # openings keep out of the mitred corners of the walls
        return c - half >= WALL + 4 and c + half <= side_len - WALL - 4

    ent_slots = [k for k in ent_slots if clear_of_corners(pad + (k + 0.5) * step, door_w / 2, length)]

    def window_holes(side_len, front_back, is_front):
        nonlocal windows
        holes, items = [], []
        if front_back:
            n_slots, off = slots, pad
        else:
            n_slots = int(side_len // step) if end_windows else 0
            off = (side_len - n_slots * step) / 2
        for k in range(n_slots):
            c = off + (k + 0.5) * step
            entrance = is_front and k in ent_slots
            if not entrance and not clear_of_corners(c, ww / 2, side_len):
                continue
            if entrance:
                holes.append((c - door_w / 2, c + door_w / 2, 0.0, door_h))
                items.append(('door', c, 0.0, door_h, door_w))
            for f in range(floors):
                zf = plinth + f * fh
                if entrance:
                    v0 = zf - fh / 2 + 24                        # stair landing: half a floor lower
                    if f == 0 or v0 - 5 < canopy + 8 + 16:       # its sill clears the canopy
                        continue
                    holes.append((c - 20, c + 20, v0, v0 + 44))
                    items.append(('stair', c, v0, v0 + 44, 40.0))
                    continue
                v0 = zf + 34
                holes.append((c - ww / 2, c + ww / 2, v0, v0 + wh))
                items.append(('window', c, v0, v0 + wh, ww, f))
                windows += 1
        return holes, items

    sides = [  # start corner, u, n (outward), length, front/back, is_front: counter-clockwise from above
        ((-L2, -D2), (1, 0), (0, -1), length, True, True),
        ((L2, -D2), (0, 1), (1, 0), depth, False, False),
        ((L2, D2), (-1, 0), (0, 1), length, True, False),
        ((-L2, D2), (0, -1), (-1, 0), depth, False, False),
    ]
    balcony_cols = set()
    for k in range(slots):
        if k not in ent_slots and rng.random() < balconies:
            balcony_cols.add(k)
    side_holes = [window_holes(side_len, fb, is_front) for _, _, _, side_len, fb, is_front in sides]
    _ring(walls, fr, [sd[0] for sd in sides], [h for h, _ in side_holes], 0.0, z_wall, WALL, wall_mats, scale,
          rows=[plinth])
    for (start, u, n, side_len, fb, is_front), (holes, items) in zip(sides, side_holes):
        def P(s, v, dep):
            return (start[0] + u[0] * s - n[0] * dep, start[1] + u[1] * s - n[1] * dep, v)

        for it in items:
            kind, c, v0, v1, w = it[:5]
            if kind == 'door':
                # the stock leaf (placed after the meshes) sinks 1 unit into the side reveals and 2 into the top;
                # the canopy sinks 1 unit into the wall
                doors.append((P(c, 0.0, DOOR_AT)[:2], u))
                keep.append([fr.p(*P(s_, 0.0, d_)[:2], 0.0)[:2] for s_, d_ in
                             ((c - w / 2 - 48, 0.0), (c + w / 2 + 48, 0.0), (c + w / 2 + 48, -140.0), (c - w / 2 - 48, -140.0))])
                a, b = P(c - w / 2 - 32, canopy, -56), P(c + w / 2 + 32, canopy, 1)
                _box(details, fr, (min(a[0], b[0]), min(a[1], b[1]), canopy),
                     (max(a[0], b[0]), max(a[1], b[1]), canopy + 8), mats['trim'])
                continue
            # frame: a ring 1 unit into the reveal on every side, FRAME_T deep at FRAME_AT
            fs = (start[0] + u[0] * (c - w / 2 - 1) - n[0] * FRAME_AT, start[1] + u[1] * (c - w / 2 - 1) - n[1] * FRAME_AT)
            fw = w + 2
            inner = [(FRAME_W + 1, fw - FRAME_W - 1, v0 - 1 + FRAME_W + 1, v1 + 1 - FRAME_W - 1)]
            _slab(details, fr, fs, u, n, fw, v0 - 1, v1 + 1, inner, FRAME_T, lambda a, b: mats['frame'], scale)
            if w >= 48:                                           # a mullion, its ends inside the frame
                a = P(c - 1.5, v0, FRAME_AT)
                b = P(c + 1.5, v1, FRAME_AT + FRAME_T)
                _box(details, fr, (min(a[0], b[0]), min(a[1], b[1]), v0 + FRAME_W), (max(a[0], b[0]), max(a[1], b[1]),
                     v1 - FRAME_W), mats['frame'])
            # glass 1 unit behind the frame, tucked 1 unit behind its inner edge
            g0, g1 = c - w / 2 + FRAME_W, c + w / 2 - FRAME_W
            dep = FRAME_AT + FRAME_T + 1
            glass.face_out([fr.p(*P(g0, v0 + FRAME_W, dep)[:2], v0 + FRAME_W), fr.p(*P(g1, v0 + FRAME_W, dep)[:2], v0 + FRAME_W),
                            fr.p(*P(g1, v1 - FRAME_W, dep)[:2], v1 - FRAME_W), fr.p(*P(g0, v1 - FRAME_W, dep)[:2], v1 - FRAME_W)],
                           fr.d(*n), mats['glass'])
            # a sill under the opening, 1 unit into the wall, its top 1 unit under the reveal
            a, b = P(c - w / 2 - 4, v0 - 5, -4), P(c + w / 2 + 4, v0 - 1, 1)
            _box(details, fr, (min(a[0], b[0]), min(a[1], b[1]), v0 - 5), (max(a[0], b[0]), max(a[1], b[1]), v0 - 1),
                 mats['trim'])
            if kind == 'window' and fb and len(it) > 5 and it[5] >= 1 and int((c - pad) // step) in balcony_cols:
                zf = plinth + it[5] * fh
                a, b = P(c - 44, zf - 8, -52), P(c + 44, zf, 1)
                _box(details, fr, (min(a[0], b[0]), min(a[1], b[1]), zf - 8), (max(a[0], b[0]), max(a[1], b[1]), zf),
                     mats['trim'])
                a, b = P(c - 45, zf - 1, -53), P(c + 45, zf + 40, -48)     # the front screen sinks into the slab
                _box(details, fr, (min(a[0], b[0]), min(a[1], b[1]), zf - 1), (max(a[0], b[0]), max(a[1], b[1]), zf + 40),
                     mats['walls'])
                for s_ in (c - 43, c + 39):                                  # side screens sink into both
                    a, b = P(s_, zf - 1, -49), P(s_ + 4, zf + 39, 1)
                    _box(details, fr, (min(a[0], b[0]), min(a[1], b[1]), zf - 1), (max(a[0], b[0]), max(a[1], b[1]), zf + 39),
                         mats['walls'])
    # the core: a dark solid 1 unit into the walls, up to the roof; the roof deck on it
    core = mesh.Mesh()
    _box(core, fr, (-L2 + WALL - 1, -D2 + WALL - 1, 0), (L2 - WALL + 1, D2 - WALL + 1, z_top - 8), mats['core'])
    roof = mesh.Mesh()
    _box(roof, fr, (-L2 + WALL - 1, -D2 + WALL - 1, z_top - 8), (L2 - WALL + 1, D2 - WALL + 1, z_top), mats['roof'])
    if parapet:                                                    # coping: 2 units over each face of the parapet
        cz0, cz1 = z_wall - 1, z_wall + 3
        _box(roof, fr, (-L2 - 2, -D2 - 2, cz0), (L2 + 2, -D2 + WALL + 2, cz1), mats['trim'])
        _box(roof, fr, (-L2 - 2, D2 - WALL - 2, cz0), (L2 + 2, D2 + 2, cz1), mats['trim'])
        _box(roof, fr, (-L2 - 1, -D2 + WALL + 1, cz0 + 0.5), (-L2 + WALL + 1, D2 - WALL - 1, cz1 - 0.5), mats['trim'])
        _box(roof, fr, (L2 - WALL - 1, -D2 + WALL + 1, cz0 + 0.5), (L2 + 1, D2 - WALL - 1, cz1 - 0.5), mats['trim'])
    for k in ent_slots:                                            # a stair-house over every entrance
        c = -L2 + pad + (k + 0.5) * step
        _box(roof, fr, (c - 64, -D2 + WALL + 24, z_top - 1), (c + 64, -D2 + WALL + 120, z_top + 72), mats['trim'])

    group = m.new_group('block_house:' + name)
    structure._set_group_defaults(group)
    if keep:
        group.set(KEEP_CLEAR, 'string', json.dumps([[list(map(lambda v: round(v, 1), q)) for q in k] for k in keep]))
    m.attach(group)
    solid = {'physicsType': 'default' if collision else 'none'}
    flat = {'physicsType': 'none', 'disableShadows': 0}
    nodes = [m.new_mesh(walls, texture_size=project.texture_size, options=solid),
             m.new_mesh(core, texture_size=project.texture_size, options=solid),
             m.new_mesh(roof, texture_size=project.texture_size, options=solid)]
    if details.faces:
        nodes.append(m.new_mesh(details, texture_size=project.texture_size, options=flat))
    if glass.faces:
        nodes.append(m.new_mesh(glass, texture_size=project.texture_size, options={'physicsType': 'none',
                                                                               'disableShadows': 1}))
    group['children'] = list(group['children']) + nodes
    for (lx, ly), u_dir in doors:                   # stock leaves: width along the wall, bounds centred in the slot
        wu = fr.d(*u_dir)
        yaw_d = math.degrees(math.atan2(wu[1], wu[0])) - (90.0 if d_axis else 0.0)
        a = math.radians(yaw_d)
        cx, cy = (d_lo[0] + d_hi[0]) / 2 * d_sc, (d_lo[1] + d_hi[1]) / 2 * d_sc
        t = fr.p(lx, ly, 0.0)
        at = [round(t[0] - (cx * math.cos(a) - cy * math.sin(a)), 3),
              round(t[1] - (cx * math.sin(a) + cy * math.cos(a)), 3), round(t[2] - d_lo[2] * d_sc, 3)]
        kv = {'model': door_model} if collision else {'model': door_model, 'solid': '0'}
        ent = m.new_entity('prop_static', kv, at, (0.0, round(yaw_d % 360, 3), 0.0), (round(d_sc, 4),) * 3,
                           defaults=project.catalog.defaults('prop_static'))
        ent.set('cs2map_inset', 'string', str(nodes[0]['nodeID']))     # set into the wall's reveals on purpose
        m.attach(ent, group)
    if op.get('ref'):
        refs[op['ref']] = group['nodeID']
    return {'op': 'add_block_house', 'id': group['nodeID'], 'floors': floors, 'height': z_top,
            'entrances': len(ent_slots), 'windows': windows, 'meshes': len(nodes), 'doors': len(doors),
            **({'ref': op['ref']} if op.get('ref') else {})}
