"""add_district: a cheap town for huge maps — streets, sidewalks and houses as light meshes, faded at distance.

A district is many simple houses (a box body, a gable or flat roof, window and door boxes on the street sides)
on a grid of blocks and streets. No props and no CSG: a few hundred faces per block, so a 6000 x 6000 town
builds in seconds and renders cheaply; `fade` sets fademaxdist on every mesh, `collision:false` makes a
background town without physics. Close-up streets where players fight still deserve add_facade or by-hand
houses; a district fills what is seen from them and the space of open-world maps.
"""
from __future__ import annotations

import math
import random

from . import mesh, structure
from .project import OpError

KEYS = {'op', 'name', 'area', 'z', 'block', 'street', 'sidewalk', 'floors', 'floor_height', 'house_width', 'roofs',
        'windows', 'doors', 'fade', 'collision', 'seed', 'style', 'materials', 'ref'}
INFERNO = {
    'walls': [{'material': 'materials/de_inferno/plaster/inferno_plaster_facade_01_dirty.vmat', 'tint': [243, 219, 179]},
              {'material': 'materials/de_inferno/plaster/inferno_plaster_facade_01_basic.vmat', 'tint': [213, 164, 92]},
              {'material': 'materials/de_inferno/plaster/inferno_plaster_facade_01_dirty.vmat', 'tint': [187, 79, 52]},
              {'material': 'materials/de_inferno/plaster/inferno_plaster_facade_01_basic.vmat', 'tint': [254, 235, 204]},
              {'material': 'materials/de_inferno/plaster/inferno_plaster_facade_01_dirty.vmat', 'tint': [206, 144, 79]}],
    'roof': 'models/de_inferno/roof/roof_shingles_01/roofkit01_shingles_01.vmat',
    'street': 'materials/de_inferno/ground/inferno_stone_floor_08.vmat',
    'sidewalk': 'materials/de_inferno/ground/inferno_stone_floor_07.vmat',
    'trim': 'materials/de_inferno/stone/inferno_stone_wall_09.vmat',
    'window': 'materials/glass/window_opaque_1.vmat',
    'door': 'materials/dev/reflectivity_10.vmat',
}
MAX_HOUSES = 900
WINDOW = (40.0, 60.0)          # width, height
WINDOW_STEP = 96.0             # one window per this much wall


def _num(v, label, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise OpError(f'district.{label} must be a number {lo}..{hi}')
    return float(v)


def _pair(v, label, lo, hi):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = [v, v]
    if not isinstance(v, list) or len(v) != 2:
        raise OpError(f'district.{label} must be a number or [min, max]')
    a, b = (_num(x, label, lo, hi) for x in v)
    if a > b:
        raise OpError(f'district.{label}: min > max')
    return a, b


def _materials(project, op):
    mats = dict(INFERNO)
    own = op.get('materials') or {}
    if not isinstance(own, dict) or set(own) - set(mats):
        raise OpError(f'district.materials keys: {", ".join(sorted(mats))}')
    mats.update(own)
    walls = mats['walls'] if isinstance(mats['walls'], list) else [mats['walls']]
    out = {'walls': []}
    for w in walls:
        entry = w if isinstance(w, dict) else {'material': w}
        if set(entry) - {'material', 'tint'}:
            raise OpError('district.materials.walls entries are paths or {"material", "tint"}')
        tint = entry.get('tint')
        if tint is not None and (not isinstance(tint, list) or len(tint) != 3
                                 or not all(isinstance(c, (int, float)) and 0 <= c <= 255 for c in tint)):
            raise OpError('district wall tints are [r,g,b] 0..255')
        out['walls'].append((structure._material_path(project, entry['material']), tint))
    for key in ('roof', 'street', 'sidewalk', 'trim', 'window', 'door'):
        out[key] = structure._material_path(project, mats[key])
    return out


def add_district(project, m, op, refs):
    extra = set(op) - KEYS
    if extra:
        raise OpError(f'unknown add_district fields {sorted(extra)}; allowed: {", ".join(sorted(KEYS - {"op"}))}')
    name = op.get('name', 'district')
    if not isinstance(name, str) or not name.replace('_', '').isalnum() or not name.islower() or len(name) > 48:
        raise OpError('district.name: 1..48 chars a-z0-9_')
    if any(n.name == 'district:' + name for n in m.iter_nodes()):
        raise OpError(f'district {name} already exists')
    if op.get('style') not in (None, 'inferno'):
        raise OpError('district.style: "inferno" or "my:<own style>" (own styles map their materials)')
    area = op.get('area')
    if not isinstance(area, dict) or set(area) != {'mins', 'maxs'}:
        raise OpError('district.area is {"mins":[x,y], "maxs":[x,y]}')
    (x0, y0), (x1, y1) = ([_num(c, 'area', -32768, 32768) for c in area[k][:2]] for k in ('mins', 'maxs'))
    if x1 - x0 < 512 or y1 - y0 < 512:
        raise OpError('district.area must be at least 512 x 512')
    z = _num(op.get('z', 0), 'z', -16384, 16384)
    block = op.get('block', [768, 640])
    block = [block, block] if isinstance(block, (int, float)) else block
    if not isinstance(block, list) or len(block) != 2:
        raise OpError('district.block is [width, depth] of a block between streets (or one number)')
    bw, bd = (_num(v, 'block', 256, 4096) for v in block)
    street = _num(op.get('street', 192), 'street', 64, 1024)
    walk = _num(op.get('sidewalk', 32), 'sidewalk', 0, 256)
    f_lo, f_hi = (int(v) for v in _pair(op.get('floors', [2, 4]), 'floors', 1, 12))
    fh = _num(op.get('floor_height', 128), 'floor_height', 96, 256)
    hw = _num(op.get('house_width', 256), 'house_width', 128, 1024)
    roofs = op.get('roofs', ['gable', 'gable', 'flat'])
    if not isinstance(roofs, list) or not roofs or set(roofs) - {'gable', 'flat'}:
        raise OpError('district.roofs: a list of "gable" / "flat" (picked at random per house)')
    windows, doors = bool(op.get('windows', True)), bool(op.get('doors', True))
    fade = _num(op.get('fade', 0), 'fade', 0, 100000)
    collision = bool(op.get('collision', True))
    rng = random.Random(op.get('seed', 1))
    mats = _materials(project, op)
    mats['tex'] = project.texture_size

    group = m.new_group('district:' + name)
    structure._set_group_defaults(group)
    m.attach(group)
    nodes = []
    base = {'fademaxdist': fade} if fade else {}
    solid = {**base, 'physicsType': 'default' if collision else 'none'}
    flat_opts = {**base, 'physicsType': 'none', 'disableShadows': 1}

    def add(shape_mesh, options, tint=None):
        opts = dict(options)
        if tint:
            opts['tintColor'] = (int(tint[0]), int(tint[1]), int(tint[2]), 255)
        nodes.append(m.new_mesh(shape_mesh, texture_size=project.texture_size, options=opts))

    add(mesh.box((x0, y0, z - 16), (x1, y1, z), mats['street']), solid)
    cols = max(1, int((x1 - x0 + 1e-6) // (bw + street)))
    rows = max(1, int((y1 - y0 + 1e-6) // (bd + street)))
    houses = 0
    for i in range(cols):
        for j in range(rows):
            bx0 = x0 + (x1 - x0) * i / cols + street / 2
            bx1 = x0 + (x1 - x0) * (i + 1) / cols - street / 2
            by0 = y0 + (y1 - y0) * j / rows + street / 2
            by1 = y0 + (y1 - y0) * (j + 1) / rows - street / 2
            top = z + (6 if walk else 0)
            if walk:
                add(mesh.box((bx0, by0, z - 1), (bx1, by1, top), mats['sidewalk']), solid)
            hx0, hx1, hy0, hy1 = bx0 + walk, bx1 - walk, by0 + walk, by1 - walk
            if hx1 - hx0 < 128 or hy1 - hy0 < 128:
                continue
            mid = (hy0 + hy1) / 2
            for facing, (ry0, ry1) in ((-1, (hy0, mid)), (1, (mid, hy1))):
                cuts, x = [hx0], hx0
                while hx1 - x > hw * 1.5:
                    x += hw * rng.uniform(0.75, 1.25)
                    cuts.append(round(x))
                cuts.append(hx1)
                for k in range(len(cuts) - 1):
                    houses += 1
                    if houses > MAX_HOUSES:
                        raise OpError(f'district has more than {MAX_HOUSES} houses: a larger house_width/block '
                                      'or several districts')
                    end = {'-x': k == 0, '+x': k == len(cuts) - 2}
                    _house(add, mats, rng, (cuts[k], ry0, top), (cuts[k + 1], ry1), facing, end,
                           rng.randint(f_lo, f_hi), fh, rng.choice(roofs), windows, doors, solid, flat_opts)
    group['children'] = list(group['children']) + nodes
    if op.get('ref'):
        refs[op['ref']] = group['nodeID']
    return {'op': 'add_district', 'id': group['nodeID'], 'blocks': cols * rows, 'houses': houses,
            'meshes': len(nodes), **({'ref': op['ref']} if op.get('ref') else {})}


def _house(add, mats, rng, lo, hi, facing, end, floors, fh, roof, windows, doors, solid, flat_opts):
    """One house: body, roof, windows/door boxes on the street side (facing -1 = -y, +1 = +y) and row ends."""
    x0, y0, z0 = lo
    x1, y1 = hi
    h = floors * fh
    material, tint = mats['walls'][rng.randrange(len(mats['walls']))]
    add(mesh.box((x0, y0, z0), (x1, y1, z0 + h), material), solid, tint)
    top = z0 + h
    depth = y1 - y0
    if roof == 'gable':
        rise = depth / 2 * math.tan(math.radians(rng.choice((28, 32, 36))))
        poly = [(y0 - 8, top - 1), (y1 + 8, top - 1), ((y0 + y1) / 2, top + rise)]
        add(mesh.prism(poly, x0 - 8, x1 + 8, axis='x', material=mats['roof']), solid)
    else:
        rim = 4
        add(mesh.box((x0 - rim, y0 - rim, top - 1), (x1 + rim, y1 + rim, top + 10), mats['trim']), solid)
    pieces = mesh.Mesh()
    face_y = y0 if facing < 0 else y1
    out = -1 if facing < 0 else 1

    def window(u0, u1, zb, axis, plane, normal):
        w, wh = WINDOW
        lo_u, hi_u = (u0 + u1) / 2 - w / 2, (u0 + u1) / 2 + w / 2
        a, b = (plane - 1, plane + 2) if normal > 0 else (plane - 2, plane + 1)
        if axis == 'y':
            box = mesh.box((lo_u, a, zb), (hi_u, b, zb + wh), mats['trim'])
        else:
            box = mesh.box((a, lo_u, zb), (b, hi_u, zb + wh), mats['trim'])
        for f in box.faces:
            n = box.normal(f)
            if (axis == 'y' and n[1] * normal > 0.9) or (axis == 'x' and n[0] * normal > 0.9):
                # the window image once over the front, right-reading for a viewer facing the wall
                f.material = mats['window']
                tw, th = mats['tex'](f.material)
                right = (-normal, 0.0, 0.0) if axis == 'y' else (0.0, normal, 0.0)
                pts = [box.positions[i] for i in f.verts]
                us = [p[0] * right[0] + p[1] * right[1] for p in pts]
                zs = [p[2] for p in pts]
                su, sv = (max(us) - min(us)) / tw, (max(zs) - min(zs)) / th
                f.u_axis, f.v_axis, f.scale = right, (0.0, 0.0, -1.0), (su, sv)
                f.offset = (-min(us) / su, max(zs) / sv)
        pieces.extend(box)

    if windows:
        count = max(1, int((x1 - x0) // WINDOW_STEP))
        for fl in range(floors):
            zb = z0 + fl * fh + fh * 0.32
            for c in range(count):
                if doors and fl == 0 and c == count // 2:
                    continue
                window(x0 + (x1 - x0) * c / count, x0 + (x1 - x0) * (c + 1) / count, zb, 'y', face_y, out)
            for side, x in (('-x', x0), ('+x', x1)):
                if end[side] and depth >= 160:
                    window(y0 + 24, y1 - 24, zb, 'x', x, -1 if side == '-x' else 1)
    if doors:
        count = max(1, int((x1 - x0) // WINDOW_STEP))
        c = count // 2
        u = x0 + (x1 - x0) * (c + 0.5) / count
        a, b = (face_y - 1, face_y + 3) if out > 0 else (face_y - 3, face_y + 1)
        pieces.extend(mesh.box((u - 26, a, z0), (u + 26, b, z0 + 96), mats['door']))
    if pieces.faces:
        add(pieces, flat_opts)
