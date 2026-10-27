"""Linear builders that maps of real places need again and again (fences, railings, banks, bare trees), each one
operation instead of a page of generator code (a big town map, 24-25.10: ~250 lines per generator).

All of them follow the ground: a point's z is the add_terrain surface under it when the map has one (or the
terrain named in "terrain"), else the z given in the path, else 0. Parts that only look solid (wire, rails, leaves)
are render-only; what must stop a player gets a player clip (bullets still pass), what must stop bullets is solid.
"""
from __future__ import annotations

import json
import math
import random

from . import mesh, navcost, structure, terrain
from .dressing import tube
from .project import OpError

CLIP = 'materials/tools/toolsplayerclip.vmat'
BANK_KEY = 'cs2map_bank'   # marks a bank mesh: its steep face is meant (check_map steep_ramp skips it)
FENCE_KINDS = {
    # panel material, post material, post size, panel thickness, default height, rails, solid panel
    'sheet': ('materials/metal/hr_metal/hr_metal_corrugated_001.vmat', 'materials/trim/trim_metal_painted_rusty_01.vmat',
              3, 1.5, 78, True, True),
    'planks': ('materials/hr_massive/wood_planks_1.vmat', 'materials/hr_massive/wood_planks_1.vmat', 4, 1.0, 60, True, True),
    'chainlink': ('materials/de_nuke/hr_nuke/chainlink_fence_001_card.vmat', 'materials/trim/trim_metal_painted_rusty_01.vmat',
                  2.4, 0.5, 72, True, False),
    'mesh3d': ('materials/metal/metalfence004a.vmat', 'materials/trim/trim_metal_painted_rusty_01.vmat', 2.4, 0.5, 66, False,
               False),
    'panel': ('materials/shared_base/concrete/concrete_panel_001.vmat', 'materials/concrete/hr_c/hr_conc_d.vmat', 6, 4.0, 96,
              False, True),
    'wall': ('materials/cs_italy/hpe_stone_wall_02.vmat', 'materials/concrete/hr_c/hr_conc_d.vmat', 0, 16.0, 48, False, True),
    'rubble': ('materials/de_overpass/brick/cobblestone_wall_1.vmat', 'materials/concrete/hr_c/hr_conc_d.vmat', 0, 20.0,
               48, False, True),
    'gabion': ('materials/de_overpass/brick/cobblestone_wall_2.vmat', 'materials/metal/metalfence004a.vmat', 0, 36.0, 40,
               False, True),
}
MASONRY = ('wall', 'rubble', 'gabion')       # solid walls without posts; a cap (wall, rubble) or a wire cage (gabion)
GATES = ('open', 'closed', 'wicket')
JUMP_UP = 56.0          # a standing jump reaches about this high: a lower bank can be climbed


def _num(value, label, lo, hi, default=None):
    if value is None:
        if default is None:
            raise OpError(f'{label} is required')
        return float(default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise OpError(f'{label} must be a number in {lo}..{hi}')
    return float(value)


def _fields(op, allowed, label):
    extra = set(op) - set(allowed) - {'op', 'ref'}
    if extra:
        raise OpError(f'unknown {label} fields: {", ".join(sorted(extra))}')


def _material(project, value, default):
    return terrain.material(project, value) if value is not None else default


def _ground(m, op):
    """f(x, y, z_given) -> ground z: the terrain under the point, else the given z, else 0."""
    plans = terrain.plans(m)
    if op.get('terrain') is not None:
        plans = [terrain.select(m, op['terrain'])]

    def at(x, y, z=None):
        for data in plans:
            (lo, hi) = data['bounds']
            if lo[0] <= x <= hi[0] and lo[1] <= y <= hi[1]:
                return terrain.sample(data, x, y)['z']
        return 0.0 if z is None else z
    return at


def _path(value, label='path', least=2):
    if not isinstance(value, list) or not least <= len(value) <= 256:
        raise OpError(f'{label} needs {least}..256 points [x, y] or [x, y, z]')
    out = []
    for p in value:
        if not isinstance(p, (list, tuple)) or len(p) not in (2, 3):
            raise OpError(f'{label} points are [x, y] or [x, y, z]')
        out.append([_num(c, label, -32768, 32768) for c in p])
    for a, b in zip(out, out[1:]):
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 1:
            raise OpError(f'{label}: consecutive points closer than 1 unit')
    return out


def slab(a, b, za0, za1, zb0, zb1, half, material, offset=0.0):
    """A box along the segment a -> b seen from above, `half` wide each side of the line (shifted `offset` to
    its left); bottom/top at za0/za1 over a and zb0/zb1 over b (it follows a slope)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln = math.hypot(dx, dy)
    nx, ny = -dy / ln, dx / ln
    def side(p, s):
        return (p[0] + nx * (offset + s), p[1] + ny * (offset + s))
    al, ar, bl, br = side(a, half), side(a, -half), side(b, half), side(b, -half)
    pts = {'al0': (*al, za0), 'ar0': (*ar, za0), 'bl0': (*bl, zb0), 'br0': (*br, zb0),
           'al1': (*al, za1), 'ar1': (*ar, za1), 'bl1': (*bl, zb1), 'br1': (*br, zb1)}
    m = mesh.Mesh()
    up, down = (0, 0, 1), (0, 0, -1)
    left, right, back, front = (nx, ny, 0), (-nx, -ny, 0), (-dx / ln, -dy / ln, 0), (dx / ln, dy / ln, 0)
    for corners, out in ((('al1', 'bl1', 'br1', 'ar1'), up), (('al0', 'ar0', 'br0', 'bl0'), down),
                         (('al0', 'bl0', 'bl1', 'al1'), left), (('ar0', 'ar1', 'br1', 'br0'), right),
                         (('al0', 'al1', 'ar1', 'ar0'), back), (('bl0', 'br0', 'br1', 'bl1'), front)):
        m.face_out([pts[c] for c in corners], out, material)
    return m


def _post(x, y, z0, z1, size, material):
    h = size / 2
    return mesh.box((x - h, y - h, z0), (x + h, y + h, z1), material)


def _attach(project, m, piece, options=None):
    node = m.new_mesh(piece, texture_size=project.texture_size, options=options)
    m.attach(node)
    return node['nodeID']


def _stations(path, step):
    """Points every `step` along the path (its corners kept), with their running distance."""
    out = [(0.0, path[0])]
    run = 0.0
    for a, b in zip(path, path[1:]):
        ln = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, math.ceil(ln / step - 1e-6))
        for i in range(1, n + 1):
            t = i / n
            out.append((run + ln * t, [a[k] + (b[k] - a[k]) * t for k in range(len(a))]))
        run += ln
    return out


def _follow(stations, height, tol=4.0, least=16.0):
    """More stations where the ground bends between two of them by over `tol` (a post at the break), so spans
    neither float over a hollow nor sink into a bank (t11_builders 25.10: a fence across a bank was buried)."""
    out = [stations[0]]

    def split(s0, s1, depth):
        (d0, a), (d1, b) = s0, s1
        za, zb = height(a), height(b)
        worst, at = tol, None
        for k in range(1, 8):
            t = k / 8
            p = [a[j] + (b[j] - a[j]) * t for j in range(len(a))]
            dev = abs(height(p) - (za + (zb - za) * t))
            if dev > worst and least <= (d1 - d0) * t <= (d1 - d0) - least:
                worst, at = dev, (d0 + (d1 - d0) * t, p)
        if at is None or depth > 6:
            out.append(s1)
            return
        split(s0, at, depth + 1)
        split(at, s1, depth + 1)
    for s0, s1 in zip(stations, stations[1:]):
        split(s0, s1, 0)
    return out


def _gaps(op, total):
    """(start, end, kind) of each gate: open (a gap), closed (two leaves) or wicket (one leaf)."""
    gates = op.get('gates') or []
    if not isinstance(gates, list) or len(gates) > 32:
        raise OpError('gates: a list of {"at": distance along the path, "width": units, "kind": open|closed|wicket}')
    out = []
    for g in gates:
        if not isinstance(g, dict) or set(g) - {'at', 'width', 'kind'} or g.get('kind', 'open') not in GATES:
            raise OpError('each gate is {"at": distance along the path, "width": units, "kind": open|closed|wicket}')
        at, w = _num(g.get('at'), 'gate at', 0, total), _num(g.get('width'), 'gate width', 24, 1024, 96)
        out.append((max(0.0, at - w / 2), min(total, at + w / 2), g.get('kind', 'open')))
    return out


def _with_stops(stations, stops):
    """The stations plus a station at each distance in `stops` (a gate's edge gets its own post)."""
    out = list(stations)
    for d in stops:
        for i in range(len(out) - 1):
            (d0, a), (d1, b) = out[i], out[i + 1]
            if d0 + 1 < d < d1 - 1:
                t = (d - d0) / (d1 - d0)
                out.insert(i + 1, (d, [a[k] + (b[k] - a[k]) * t for k in range(len(a))]))
                break
    return out


def add_fence(project, m, op, refs):
    """Posts, rails and panels of one kind along a path, following the ground; gates are openings."""
    _fields(op, {'path', 'kind', 'height', 'post_spacing', 'material', 'post_material', 'thickness', 'sink', 'gates',
                 'clip', 'cap', 'terrain', 'gap', 'gate_material'}, 'add_fence')
    kind = op.get('kind', 'sheet')
    if kind not in FENCE_KINDS:
        raise OpError(f'kind must be one of {", ".join(FENCE_KINDS)}')
    panel_mat, post_mat, post_size, thick, height0, rails, solid = FENCE_KINDS[kind]
    path = _path(op.get('path'))
    height = _num(op.get('height'), 'height', 8, 512, height0)
    spacing = _num(op.get('post_spacing'), 'post_spacing', 24, 512, 96 if kind != 'panel' else 80)
    thick = _num(op.get('thickness'), 'thickness', 0.25, 64, thick)
    sink = _num(op.get('sink'), 'sink', 0, 64, 4)
    panel_mat = _material(project, op.get('material'), panel_mat)
    post_mat = _material(project, op.get('post_material'), post_mat)
    ground = _ground(m, op)
    height_at = lambda p: ground(p[0], p[1], p[2] if len(p) == 3 else None)
    stations = _stations(path, spacing)
    total = stations[-1][0]
    gaps = _gaps(op, total)
    stations = _follow(_with_stops(stations, [e for g in gaps for e in g[:2]]), height_at)
    def open_at(d0, d1):
        return any(d0 < g1 - .5 and d1 > g0 + .5 for g0, g1, _ in gaps)
    z = [ground(p[0], p[1], p[2] if len(p) == 3 else None) for _, p in stations]
    # Neighbouring spans meet at a post: in one mesh their touching ends would share edges, so even and odd
    # spans go to two meshes (posts cover the joint).
    posts = mesh.Mesh()
    panels_eo, rails_eo, clips_eo = [mesh.Mesh(), mesh.Mesh()], [mesh.Mesh(), mesh.Mesh()], [mesh.Mesh(), mesh.Mesh()]
    caps_eo, cages_eo = [mesh.Mesh(), mesh.Mesh()], [mesh.Mesh(), mesh.Mesh()]
    planks = kind == 'planks'

    def dip(i):
        """How far the ground falls below the straight line between two posts: bottoms go down by that."""
        (_, a), (_, b) = stations[i], stations[i + 1]
        return max([0.0] + [z[i] + (z[i + 1] - z[i]) * k / 8 - ground(*[a[j] + (b[j] - a[j]) * k / 8 for j in range(len(a))])
                            for k in range(1, 8)])
    dips = [dip(i) for i in range(len(stations) - 1)]
    for i in range(len(stations) - 1):
        (d0, a), (d1, b) = stations[i], stations[i + 1]
        if open_at(d0, d1):
            continue
        za, zb = z[i], z[i + 1]
        low = sink + dips[i]
        panels, rails_m, clips = panels_eo[i % 2], rails_eo[i % 2], clips_eo[i % 2]
        if kind in MASONRY:
            panels.extend(slab(a, b, za - low, za + height, zb - low, zb + height, thick / 2, panel_mat))
            if kind == 'gabion':
                # The wire basket round the stones, a unit off them also at the fence's two ends (render-only, the
                # stones are solid).
                ln = math.hypot(b[0] - a[0], b[1] - a[1])
                ca = [a[k] - (b[k] - a[k]) / ln for k in (0, 1)] if i == 0 else a
                cb = [b[k] + (b[k] - a[k]) / ln for k in (0, 1)] if i == len(stations) - 2 else b
                cages_eo[i % 2].extend(slab(ca, cb, za - 1, za + height + 1, zb - 1, zb + height + 1, thick / 2 + 1,
                                            post_mat))
            elif op.get('cap', True):
                # Its own mesh: a cap sitting on the wall in the same mesh would share the wall's top edges.
                cap = _material(project, op['cap'], post_mat) if isinstance(op.get('cap'), str) else post_mat
                caps_eo[i % 2].extend(slab(a, b, za + height, za + height + 3, zb + height, zb + height + 3,
                                           thick / 2 + 1, cap))
            continue
        bottom = 4 if kind in ('sheet', 'planks', 'chainlink') else 0
        if planks:
            gap = _num(op.get('gap'), 'gap', 0, 12, 1.5)
            ln = math.hypot(b[0] - a[0], b[1] - a[1])
            board = 6.0
            count = max(1, int((ln - post_size) // (board + gap)))
            for k in range(count):
                t0 = (post_size / 2 + k * (board + gap)) / ln
                t1 = min(1.0, t0 + board / ln)
                pa = [a[j] + (b[j] - a[j]) * t0 for j in (0, 1)]
                pb = [a[j] + (b[j] - a[j]) * t1 for j in (0, 1)]
                zz = [za + (zb - za) * t0, za + (zb - za) * t1]
                panels.extend(slab(pa, pb, zz[0] + bottom - dips[i], zz[0] + height, zz[1] + bottom - dips[i], zz[1] + height,
                                   thick / 2, panel_mat, offset=post_size / 2 + thick / 2 + 1))
        else:
            under = dips[i] + (sink if kind == 'panel' else 0)
            panels.extend(slab(a, b, za + bottom - under, za + height, zb + bottom - under, zb + height, thick / 2,
                               panel_mat))
        if rails:
            for level in (bottom + 8, height - 8):
                rails_m.extend(slab(a, b, za + level - 1, za + level + 1, zb + level - 1, zb + level + 1, 1, post_mat,
                                    offset=-(thick / 2 + 1.5)))
        if op.get('clip'):
            ch = height + 8 if op.get('clip') is True else _num(op.get('clip'), 'clip', 8, 4096)
            clips.extend(slab(a, b, za - dips[i], za + ch, zb - dips[i], zb + ch, 1, CLIP,
                              offset=-(thick / 2 + post_size / 2 + 3)))
    if post_size:
        for i, (d, p) in enumerate(stations):
            touches = [open_at(stations[max(0, i - 1)][0], d), open_at(d, stations[min(len(stations) - 1, i + 1)][0])]
            if all(touches) and 0 < i < len(stations) - 1:
                continue                      # inside a gate
            posts.extend(_post(p[0], p[1], z[i] - sink, z[i] + height + 2, post_size, post_mat))
    if not solid and not op.get('clip'):
        # A see-through panel has no collision: without a clip players walk through the wire.
        for i in range(len(stations) - 1):
            (d0, a), (d1, b) = stations[i], stations[i + 1]
            if not open_at(d0, d1):
                clips_eo[i % 2].extend(slab(a, b, z[i] - dips[i], z[i] + height, z[i + 1] - dips[i], z[i + 1] + height,
                                            1, CLIP))
    leaves = mesh.Mesh()
    gate_mat = _material(project, op.get('gate_material'), panel_mat if solid and kind not in MASONRY
                         else 'materials/metal/hr_metal/hr_metal_corrugated_001.vmat')
    for g0, g1, gkind in gaps:
        if gkind == 'open':
            continue
        # Leaves hang between the gate posts, 2 units over the ground, 1 unit off each post and each other.
        p0 = next(p for d, p in stations if abs(d - g0) < .01)
        p1 = next(p for d, p in stations if abs(d - g1) < .01)
        z0, z1 = height_at(p0), height_at(p1)
        width = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        clear = width - post_size - 2
        parts = [(0, clear / 2 - .5), (clear / 2 + .5, clear)] if gkind == 'closed' else [(0, clear)]
        for s0, s1 in parts:
            t0, t1 = (post_size / 2 + 1 + s0) / width, (post_size / 2 + 1 + s1) / width
            qa = [p0[k] + (p1[k] - p0[k]) * t0 for k in (0, 1)]
            qb = [p0[k] + (p1[k] - p0[k]) * t1 for k in (0, 1)]
            za, zb = z0 + (z1 - z0) * t0, z0 + (z1 - z0) * t1
            top = min(height, 84) if gkind == 'wicket' else height
            leaves.extend(slab(qa, qb, za + 2, za + top, zb + 2, zb + top, 1, gate_mat))
    ids = []
    for part in panels_eo:
        if part.faces:
            ids.append(_attach(project, m, part, None if solid else {'physicsType': 'none'}))
    for part in cages_eo:
        if part.faces:
            ids.append(_attach(project, m, part, {'physicsType': 'none'}))
    if leaves.faces:
        ids.append(_attach(project, m, leaves))
    for part in [posts, *caps_eo, *rails_eo, *clips_eo, *(navcost.nav_twin(c) for c in clips_eo if c.faces)]:
        if part.faces:
            ids.append(_attach(project, m, part))
    if not ids:
        raise OpError('add_fence made nothing (every span is inside a gate)')
    out = {'op': 'add_fence', 'id': ids[0], 'ids': ids, 'kind': kind, 'length': round(total, 1),
           'posts': len(stations) if post_size else 0, 'gates': len(gaps)}
    if op.get('ref'):
        refs[op['ref']] = ids[0]
        out['ref'] = op['ref']
    return out


def add_railing(project, m, op, refs):
    """A handrail along a walking line: round posts and rails (render-only) plus a player clip of rail height,
    so nobody walks through it and falls (23.10: rails made of add_cables had no collision)."""
    _fields(op, {'path', 'height', 'post_spacing', 'rails', 'radius', 'material', 'kick', 'clip', 'terrain'},
            'add_railing')
    path = _path(op.get('path'))
    height = _num(op.get('height'), 'height', 24, 72, 40)
    spacing = _num(op.get('post_spacing'), 'post_spacing', 16, 256, 48)
    count = int(_num(op.get('rails'), 'rails', 1, 6, 2))
    radius = _num(op.get('radius'), 'radius', 0.4, 4, 1.0)
    material = _material(project, op.get('material'), 'materials/trim/trim_metal_painted_rusty_01.vmat')
    ground = _ground(m, op)
    stations = _follow(_stations(path, spacing), lambda p: p[2] if len(p) == 3 else ground(p[0], p[1]))
    z = [p[2] if len(p) == 3 else ground(p[0], p[1]) for _, p in stations]
    posts = mesh.Mesh()
    for (d, p), zz in zip(stations, z):
        posts.extend(tube([p[0], p[1], zz - 1], [p[0], p[1], zz + height], radius * 1.2, material, 8))
    rails = [mesh.Mesh(), mesh.Mesh()]           # consecutive rail pieces share their end rings: two meshes
    for k in range(count):
        level = height - k * (height - 8) / count
        for i in range(len(stations) - 1):
            a, b = stations[i][1], stations[i + 1][1]
            rails[i % 2].extend(tube([a[0], a[1], z[i] + level], [b[0], b[1], z[i + 1] + level], radius, material, 8))
    ids = [_attach(project, m, part, {'physicsType': 'none'}) for part in (posts, *rails) if part.faces]
    pieces = []
    if op.get('kick'):
        pieces.append((material, 4, 0.5))
    if op.get('clip', True):
        pieces.append((CLIP, height + 8, 1))
    for mat, h, half in pieces:
        both = [mesh.Mesh(), mesh.Mesh()]
        for i in range(len(stations) - 1):
            a, b = stations[i][1], stations[i + 1][1]
            both[i % 2].extend(slab(a, b, z[i], z[i] + h, z[i + 1], z[i + 1] + h, half, mat))
        ids += [_attach(project, m, part) for part in both if part.faces]
        if mat == CLIP:
            ids += [_attach(project, m, navcost.nav_twin(part)) for part in both if part.faces]
    out = {'op': 'add_railing', 'id': ids[0], 'ids': ids, 'posts': len(stations), 'length': round(stations[-1][0], 1)}
    if op.get('ref'):
        refs[op['ref']] = ids[0]
        out['ref'] = op['ref']
    return out


def _bank_cut(m, op, path, side):
    """The add_terrain "bank" cut along the same path and side, if any: its tops, angle and verge."""
    plans = [terrain.select(m, op['terrain'])] if op.get('terrain') is not None else terrain.plans(m)
    for data in plans:
        for cut in data.get('bank_cuts', []):
            if cut['side'] == side and len(cut['path']) == len(path) and all(
                    math.dist(a, b[:2]) <= 1 for a, b in zip(cut['path'], path)):
                def top(p, cut=cut):
                    _, i, t, _ = terrain.nearest_path(p[0], p[1], cut['path'])
                    row = cut['tops'][i]
                    k = t * (len(row) - 1)
                    k0 = min(len(row) - 2, int(k))
                    return row[k0] + (k - k0) * (row[k0 + 1] - row[k0])
                cell = max((data['bounds'][1][k] - data['bounds'][0][k]) / data['cells'][k] for k in (0, 1))
                return {**cut, 'top': top, 'cell': cell}
    return None


def add_bank(project, m, op, refs):
    """A steep earth bank below an upper edge: players jump down it but cannot walk up (slopes over 45 degrees
    are not walkable). Reports where the drop is low enough to jump up (a town map 25.10, a lane over a yard)."""
    _fields(op, {'path', 'side', 'angle', 'bottom', 'verge', 'sink', 'material', 'verge_material', 'terrain'}, 'add_bank')
    path = _path(op.get('path'))
    side = op.get('side', 'right')
    if side not in ('left', 'right'):
        raise OpError('side is where the lower ground is, seen along the path: left or right')
    cut = _bank_cut(m, op, path, side)
    angle = _num(op.get('angle'), 'angle', 46, 85, cut['angle'] if cut else 60)
    verge = _num(op.get('verge'), 'verge', 0, 256, cut['verge'] if cut else 24)
    sink = _num(op.get('sink'), 'sink', 2, 64, 8)
    face_mat = _material(project, op.get('material'), 'materials/shared_base/grass/grass_001.vmat')
    verge_mat = _material(project, op.get('verge_material'), face_mat)
    ground = _ground(m, op)
    sign = -1.0 if side == 'right' else 1.0
    run_per_unit = 1 / math.tan(math.radians(angle))
    stations = _stations(path, 64)
    sections, low, buried = [], [], []
    for i, (d, p) in enumerate(stations):
        a, b = stations[max(0, i - 1)][1], stations[min(len(stations) - 1, i + 1)][1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy) or 1
        nx, ny = -dy / ln * sign, dx / ln * sign
        top = p[2] if len(p) == 3 else cut['top'](p) if cut else ground(p[0], p[1])
        q = p
        if cut and i in (0, len(stations) - 1):
            # The cut climbs away past the path's ends: the end sections measure the ground a cell inside.
            inward = cut['cell'] * (1 if i == 0 else -1) / ln
            q = [p[0] + dx * inward, p[1] + dy * inward]
        if isinstance(op.get('bottom'), (int, float)):
            bottom = float(op['bottom'])
        else:
            # The toe is where the face first meets the lower ground: walk down the face, then halve the last step.
            def gap(r):
                return top - (r - verge) / run_per_unit - ground(q[0] + nx * r, q[1] + ny * r, top - 64)
            r0, r1 = verge, verge
            while gap(r1) > 0 and r1 < verge + 4096 * run_per_unit:
                r0, r1 = r1, r1 + 8
            for _ in range(8):
                rm = (r0 + r1) / 2
                (r0, r1) = (rm, r1) if gap(rm) > 0 else (r0, rm)
            bottom = top - (r1 - verge) / run_per_unit
        drop = top - bottom
        if drop < 8:
            raise OpError(f'add_bank: at path distance {d:.0f} the lower ground is only {drop:.0f} units down; '
                          'where the ground falls gently, cut it first: {"op": "bank"} with the same path and side in the '
                          'add_terrain ops; else shorten the path or pass "bottom"')
        if drop < JUMP_UP:
            low.append([round(p[0]), round(p[1]), round(drop)])
        for t in (0.3, 0.6):          # ground above the face hides it: the terrain needs a "bank" cut
            r = verge + drop * t * run_per_unit
            if ground(q[0] + nx * r, q[1] + ny * r, -1e9) > top - drop * t + 2:
                buried.append([round(p[0]), round(p[1])])
                break
        reach = verge + (drop + sink) * run_per_unit
        sections.append([(p[0], p[1], top), (p[0] + nx * verge, p[1] + ny * verge, top),
                         (p[0] + nx * reach, p[1] + ny * reach, bottom - sink), (p[0], p[1], bottom - sink)])
    body = mesh.Mesh()
    mats = (verge_mat, face_mat, face_mat, face_mat)     # top strip, slope, bottom, back (buried)
    def centre(pts):
        return [sum(q[k] for q in pts) / len(pts) for k in range(3)]
    for s0, s1 in zip(sections, sections[1:]):
        mid = [(u + v) / 2 for u, v in zip(centre(s0), centre(s1))]
        for k in range(4):
            q = [s0[k], s1[k], s1[(k + 1) % 4], s0[(k + 1) % 4]]
            out = [c - m0 for c, m0 in zip(centre(q), mid)]      # the cross-section is convex: away from its centre
            for tri in (q[:3], [q[0], q[2], q[3]]):
                try:
                    body.face_out(tri, out, mats[k])
                except mesh.MeshError:
                    pass                          # a degenerate sliver at a zero-width verge
    for s, other in ((sections[0], sections[1]), (sections[-1], sections[-2])):
        body.face_out(s, [a - b for a, b in zip(centre(s), centre(other))], face_mat)
    ids = [_attach(project, m, body)]
    tops, toes = [s[0][:2] for s in sections], [s[2][:2] for s in sections]
    if cut:
        # The terrain cut fades out over five cells past each end: that ground is part of the bank too.
        for line in (tops, toes):
            for end, inner in ((0, 1), (-1, -2)):
                dx, dy = line[end][0] - line[inner][0], line[end][1] - line[inner][1]
                ln = math.hypot(dx, dy) or 1
                far = [line[end][0] + dx / ln * 5 * cut['cell'], line[end][1] + dy / ln * 5 * cut['cell']]
                line.insert(0 if end == 0 else len(line), far)
    footprint = tops + toes[::-1]
    m.node(ids[0]).set(BANK_KEY, 'string', json.dumps({
        'angle': angle, 'footprint': [[round(c, 1) for c in q] for q in footprint],
        'top': [[round(c, 1) for c in s[0]] for s in sections],
        'toe': [[round(c, 1) for c in (*s[2][:2], s[2][2] + sink)] for s in sections]}))
    out = {'op': 'add_bank', 'id': ids[0], 'ids': ids, 'length': round(stations[-1][0], 1)}
    if low:
        out['climbable'] = low[:20]
        out['warning'] = (f'{len(low)} places drop less than {JUMP_UP:g} units: a player can jump up there; '
                          'lower the ground below or add a player clip')
    if buried:
        out['buried'] = buried[:20]
        out['warning'] = ' '.join(filter(None, [out.get('warning'), (
            f'the ground covers the face at {len(buried)} places: add {{"op": "bank"}} with the same path and side to '
            'the add_terrain ops (it cuts the ground under the face), then rebuild the terrain')]))
    if op.get('ref'):
        refs[op['ref']] = ids[0]
        out['ref'] = op['ref']
    return out


POLES = {
    # pole material, radius, crossarm material
    'wood': ('materials/models/props/de_inferno/hr_i/wood_pole_a/wood_pole_a.vmat', 5.0,
             'materials/hr_massive/wood_planks_1.vmat'),
    'concrete': ('materials/concrete/hr_c/hr_conc_d.vmat', 6.0, 'materials/trim/trim_metal_painted_rusty_01.vmat'),
}


def add_utility_line(project, m, op, refs):
    """Poles along a street with sagging cables between them (a town map 24-25.10: custom code in every generator).
    Poles are solid (players and bullets stop), crossarms and cables render-only."""
    _fields(op, {'poles', 'kind', 'height', 'cables', 'sag', 'radius', 'material', 'terrain', 'arm'}, 'add_utility_line')
    poles = _path(op.get('poles'), 'poles')
    kind = op.get('kind', 'wood')
    if kind not in POLES:
        raise OpError(f'kind must be one of {", ".join(POLES)}')
    pole_mat, radius0, arm_mat = POLES[kind]
    pole_mat = _material(project, op.get('material'), pole_mat)
    height = _num(op.get('height'), 'height', 96, 1024, 320)
    count = int(_num(op.get('cables'), 'cables', 1, 6, 3))
    sag = _num(op.get('sag'), 'sag', 0, 256, 24)
    radius = _num(op.get('radius'), 'radius', 2, 24, radius0)
    arm = _num(op.get('arm'), 'arm', 16, 160, 60)
    ground = _ground(m, op)
    base = [ground(p[0], p[1], p[2] if len(p) == 3 else None) for p in poles]
    body, arms = mesh.Mesh(), mesh.Mesh()
    ends = []                               # per pole: the cable points along its crossarm
    for i, p in enumerate(poles):
        a, b = poles[max(0, i - 1)], poles[min(len(poles) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy) or 1
        nx, ny = -dy / ln, dx / ln               # the arm lies across the line (the bisector at a corner)
        top = base[i] + height
        body.extend(tube([p[0], p[1], base[i] - 8], [p[0], p[1], top], radius, pole_mat, 10))
        az = top - 14
        off = radius + 1                         # the arm's middle line: sunk 1 unit into the pole
        arms.extend(slab([p[0] - nx * arm / 2, p[1] - ny * arm / 2], [p[0] + nx * arm / 2, p[1] + ny * arm / 2],
                         az - 2, az + 2, az - 2, az + 2, 2, arm_mat, offset=off))
        spread = [(k - (count - 1) / 2) * (arm - 8) / max(1, count - 1) for k in range(count)]
        # Cables lie on the arm's top along its middle line (the arm is offset back along the line by `off`).
        back = (-dx / ln, -dy / ln)
        ends.append([[p[0] + nx * s + back[0] * off, p[1] + ny * s + back[1] * off, az + 2.6] for s in spread])
    cables = [mesh.Mesh(), mesh.Mesh()]
    for i in range(len(poles) - 1):
        for p0, p1 in zip(ends[i], ends[i + 1]):
            pts = []
            for k in range(13):
                t = k / 12
                pts.append([p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t,
                            p0[2] + (p1[2] - p0[2]) * t - 4 * sag * t * (1 - t)])
            for k, (q0, q1) in enumerate(zip(pts, pts[1:])):
                cables[k % 2].extend(tube(q0, q1, .6, 'materials/trim/trim_metal_painted_rusty_01.vmat', 6))
    ids = [_attach(project, m, body)]
    ids += [_attach(project, m, part, {'physicsType': 'none'}) for part in (arms, *cables) if part.faces]
    out = {'op': 'add_utility_line', 'id': ids[0], 'ids': ids, 'poles': len(poles), 'cables': count}
    if op.get('ref'):
        refs[op['ref']] = ids[0]
        out['ref'] = op['ref']
    return out


def bank_bypass(m):
    """check_map: a bank nobody may walk up is pointless when gentle ground beside it leads from its foot to its top
    (a town map 25.10: 'a walkable slope between levels'). Walks the terrain grid around each bank over steps under
    45 degrees, the bank's own footprint blocked; walls, fences and clips are not counted."""
    out = []
    plans = terrain.plans(m)
    for node in m.iter_nodes():
        if BANK_KEY not in node:
            continue
        data = json.loads(node[BANK_KEY])
        if not data.get('top') or not data.get('toe'):
            continue
        fp = data['footprint']
        for plan in plans:
            (lo, hi), (nx, ny) = plan['bounds'], plan['cells']
            dx, dy = (hi[0] - lo[0]) / nx, (hi[1] - lo[1]) / ny
            xs = [p[0] for p in fp]
            ys = [p[1] for p in fp]
            if min(xs) < lo[0] or max(xs) > hi[0] or min(ys) < lo[1] or max(ys) > hi[1]:
                continue
            margin = 768
            i0, i1 = max(0, int((min(xs) - margin - lo[0]) / dx)), min(nx, int((max(xs) + margin - lo[0]) / dx) + 1)
            j0, j1 = max(0, int((min(ys) - margin - lo[1]) / dy)), min(ny, int((max(ys) + margin - lo[1]) / dy) + 1)
            h = plan['heights']

            def at(i, j):
                return (lo[0] + dx * i, lo[1] + dy * j, h[i][j])
            free = {(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)
                    if not terrain.inside(at(i, j)[0], at(i, j)[1], fp)}
            near = 1.5 * max(dx, dy)

            def close(p, line, below):
                return any(math.hypot(p[0] - q[0], p[1] - q[1]) <= near and (p[2] <= q[2] + 16 if below else p[2] >= q[2] - 16)
                           for q in line)
            start = [v for v in free if close(at(*v), data['toe'], True)]
            goal = {v for v in free if close(at(*v), data['top'], False)}
            seen, todo, found = set(start), list(start), None
            while todo and found is None:
                i, j = todo.pop()
                a = at(i, j)
                for di in (-1, 0, 1):
                    for dj in (-1, 0, 1):
                        v = (i + di, j + dj)
                        if v in seen or v not in free:
                            continue
                        b = at(*v)
                        if abs(b[2] - a[2]) > .98 * math.hypot(b[0] - a[0], b[1] - a[1]):
                            continue                         # 45 degrees or steeper: not walkable
                        seen.add(v)
                        if v in goal:
                            found = b
                            break
                        todo.append(v)
                    if found:
                        break
            if found:
                out.append({'id': node['nodeID'], 'at': [round(found[0]), round(found[1]), round(found[2])]})
            break
    return out


def add_tree(project, m, op, refs):
    """A leafless tree: a solid trunk (it stops players and bullets) and render-only branches in two levels."""
    _fields(op, {'origin', 'height', 'radius', 'branches', 'seed', 'material', 'lean', 'terrain'}, 'add_tree')
    origin = op.get('origin')
    if not isinstance(origin, (list, tuple)) or len(origin) not in (2, 3):
        raise OpError('origin is [x, y] or [x, y, z]')
    x, y = _num(origin[0], 'origin', -32768, 32768), _num(origin[1], 'origin', -32768, 32768)
    z = _num(origin[2], 'origin', -32768, 32768) if len(origin) == 3 else _ground(m, op)(x, y)
    height = _num(op.get('height'), 'height', 64, 1200, 360)
    radius = _num(op.get('radius'), 'radius', 2, 48, height / 45)
    count = int(_num(op.get('branches'), 'branches', 0, 24, 7))
    lean = _num(op.get('lean'), 'lean', 0, 30, 4)
    material = _material(project, op.get('material'), 'materials/foliage/trees/bark/foliage_bark_pine_01.vmat')
    rng = random.Random(int(_num(op.get('seed'), 'seed', 0, 1e9, 1)))
    tilt = rng.uniform(0, 2 * math.pi)
    top = (x + math.cos(tilt) * height * math.sin(math.radians(lean)), y + math.sin(tilt) * height * math.sin(math.radians(lean)),
           z + height * 0.72)
    trunk = mesh.Mesh()
    trunk.extend(tube([x, y, z - 8], list(top), radius, material, 10))
    branches = mesh.Mesh()
    for k in range(count):
        t = 0.35 + 0.6 * (k + rng.random()) / max(1, count)
        base = [x + (top[0] - x) * t, y + (top[1] - y) * t, z + (top[2] - z) * t]
        yaw = 2 * math.pi * k / max(1, count) + rng.uniform(-0.4, 0.4)
        length = height * rng.uniform(0.22, 0.38) * (1.1 - t * 0.5)
        rise = rng.uniform(0.45, 0.9)
        tip = [base[0] + math.cos(yaw) * length, base[1] + math.sin(yaw) * length, base[2] + length * rise]
        r = max(0.6, radius * (0.45 - 0.2 * t))
        branches.extend(tube(base, tip, r, material, 6))
        for j in range(2):                         # twigs off the branch tip
            ty = yaw + rng.uniform(-0.9, 0.9)
            tl = length * rng.uniform(0.3, 0.5)
            twig = [tip[0] + math.cos(ty) * tl, tip[1] + math.sin(ty) * tl, tip[2] + tl * rng.uniform(0.2, 0.8)]
            branches.extend(tube(tip, twig, max(0.4, r * 0.5), material, 5))
    ids = [_attach(project, m, trunk)]
    if branches.faces:
        ids.append(_attach(project, m, branches, {'physicsType': 'none'}))
    out = {'op': 'add_tree', 'id': ids[0], 'ids': ids, 'height': height}
    if op.get('ref'):
        refs[op['ref']] = ids[0]
        out['ref'] = op['ref']
    return out
