"""add_paving: real floors for a layout — paving by area, sidewalks with curbs, gutters, steps.

The layout floor keeps its collision plane. The floor slabs of the paved rooms are split into
boxes with the area materials (their top faces stay exactly at the room floor), and sidewalks,
curbs and steps are solid meshes standing on that floor. Nothing render-only lies on a floor,
so a player never sees a floor he walks or shoots through.
"""
from __future__ import annotations

import json
import math
import re

from . import checks, layout, mesh, structure
from .dressing import number
from .project import OpError

G = 'materials/de_inferno/ground/'
STONE = 'materials/de_inferno/stone/'
STYLES = {
    'inferno': {
        'surfaces': {
            'street': {'floor': G + 'inferno_stone_floor_08.vmat', 'sidewalk': 40, 'gutter': 12},
            'plaza': {'floor': G + 'inferno_stone_floor_05.vmat', 'sidewalk': 48, 'border': 32},
            'alley': {'floor': G + 'inferno_stone_floor_01.vmat', 'band': 24},
            'courtyard': {'floor': G + 'inferno_stone_floor_hex_01.vmat', 'border': 24},
        },
        'materials': {'sidewalk': G + 'inferno_stone_floor_07.vmat', 'curb': STONE + 'inferno_stone_wall_09.vmat',
                      'gutter': G + 'inferno_stone_floor_04.vmat', 'band': G + 'inferno_stone_floor_08.vmat',
                      'border': G + 'inferno_stone_floor_07.vmat', 'steps': G + 'inferno_stone_floor_07.vmat'},
    },
    'mirage': {
        'surfaces': {
            'street': {'floor': 'materials/de_dust/hr_dust/hr_dust_brick_ground_02.vmat', 'sidewalk': 40, 'gutter': 12},
            'plaza': {'floor': 'materials/de_dust/hr_dust/hr_dust_tile_01.vmat', 'sidewalk': 48, 'border': 32},
            'alley': {'floor': 'materials/de_dust/hr_dust/hr_dust_ground_stone_01.vmat', 'band': 24},
            'courtyard': {'floor': 'materials/de_dust/hr_dust/hr_dust_tile_02.vmat', 'border': 24},
        },
        'materials': {'sidewalk': 'materials/de_dust/hr_dust/hr_dust_tile_03.vmat', 'curb': 'materials/de_dust/hr_dust/hr_dust_plaster_03.vmat',
                      'gutter': 'materials/de_dust/hr_dust/hr_dust_ground_dirt_rough_01.vmat', 'band': 'materials/de_dust/hr_dust/hr_dust_brick_ground_04.vmat',
                      'border': 'materials/de_dust/hr_dust/hr_dust_tile_03.vmat', 'steps': 'materials/de_dust/hr_dust/hr_dust_tile_03.vmat'},
    },
}
KEYS = 'op name rooms layout style areas sidewalk steps ref'
AREA_KEYS = 'surface floor sidewalk gutter band border'
SIDEWALK_KEYS = 'width height curb sides material curb_material'
PAVING_KEY = 'cs2map_paving'
STEP_RUN = 16          # tread depth of generated stairs
STEP_RISE = 12         # target riser height (CS2 climbs up to 18)


def _strip_keys(obj, allowed, label):
    if not isinstance(obj, dict):
        raise OpError(f'{label} must be an object')
    extra = set(obj) - set(allowed.split())
    if extra:
        raise OpError(f'unknown {label} fields: {sorted(extra)}; allowed: {allowed}')


def _rect_sub(a, b):
    """2D rect A minus B -> disjoint rects ((x0, y0, x1, y1))."""
    if b[0] >= a[2] or b[2] <= a[0] or b[1] >= a[3] or b[3] <= a[1]:
        return [a]
    out = []
    x0, y0, x1, y1 = a
    if b[0] > x0:
        out.append((x0, y0, b[0], y1))
    if b[2] < x1:
        out.append((b[2], y0, x1, y1))
    cx0, cx1 = max(x0, b[0]), min(x1, b[2])
    if b[1] > y0:
        out.append((cx0, y0, cx1, b[1]))
    if b[3] < y1:
        out.append((cx0, b[3], cx1, y1))
    return [r for r in out if r[2] - r[0] > 1e-3 and r[3] - r[1] > 1e-3]


def _rect_and(a, b):
    r = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return r if r[2] - r[0] > 1e-3 and r[3] - r[1] > 1e-3 else None


def _paint(regions, rect, material):
    """Painter's algorithm on disjoint rect regions: rect replaces whatever lies under it."""
    out = [(piece, mat) for r, mat in regions for piece in _rect_sub(r, rect)]
    out.append((rect, material))
    return out


def _site_rooms(m, rooms):
    """Rooms holding a bomb target or rescue zone: these are paved as plazas by default."""
    found = set()
    for ent in m.entities():
        cls = checks._props(ent).get('classname', '')
        if cls not in ('func_bomb_target', 'func_hostage_rescue'):
            continue
        b = structure._bounds([ent])
        if not b:
            continue
        c = [(b[0][i] + b[1][i]) / 2 for i in range(3)]
        for rid, room in rooms.items():
            lo, hi = room['interior']['mins'], room['interior']['maxs']
            if lo[0] <= c[0] <= hi[0] and lo[1] <= c[1] <= hi[1] and lo[2] - 32 <= c[2] <= hi[2]:
                found.add(rid)
    return found


def _material(project, path):
    return structure._material_path(project, path)


def _sidewalk_cfg(raw, preset_width, base, label):
    """Defaults <- surface preset width <- op-level sidewalk <- area sidewalk (raw)."""
    if raw is False or raw == 0 or (raw is None and not preset_width):
        return None
    cfg = {'width': preset_width or 40, 'height': 6, 'curb': 8, 'sides': 'auto', 'material': None,
           'curb_material': None, **base}
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        cfg['width'] = raw
    elif isinstance(raw, dict):
        _strip_keys(raw, SIDEWALK_KEYS, label)
        cfg.update(raw)
    elif raw is not None:
        raise OpError(f'{label} must be a width, an object or false')
    cfg['width'] = number(cfg['width'], f'{label}.width', 16, 256)
    cfg['height'] = number(cfg['height'], f'{label}.height', 2, 16)
    cfg['curb'] = number(cfg['curb'], f'{label}.curb', 0, cfg['width'] - 8)
    sides = cfg['sides']
    if sides != 'auto' and (not isinstance(sides, list) or any(s not in layout.SIDES for s in sides)):
        raise OpError(f'{label}.sides must be "auto" or a list of -x/+x/-y/+y')
    return cfg


def _strip_cfg(raw, default_width, default_material, label):
    if raw is False or raw == 0 or (raw is None and not default_width):
        return None
    cfg = {'width': default_width, 'material': default_material}
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        cfg['width'] = raw
    elif isinstance(raw, dict):
        _strip_keys(raw, 'width material', label)
        cfg.update(raw)
    elif raw is not None:
        raise OpError(f'{label} must be a width, an object or false')
    cfg['width'] = number(cfg['width'], f'{label}.width', 4, 512)
    return cfg


def _openings_on(data, room, side):
    """Openings that cut this room's wall `side`: [(u0, u1, bottom)] along the wall."""
    lo, hi = room['interior']['mins'], room['interior']['maxs']
    axis = 0 if side[1] == 'x' else 1
    other = 1 - axis
    plane = hi[axis] if side[0] == '+' else lo[axis]
    out = []
    for h in data['openings']:
        b = h['bounds']
        if b['mins'][axis] - 1 <= plane <= b['maxs'][axis] + 1 and b['maxs'][other] > lo[other] and b['mins'][other] < hi[other]:
            out.append((b['mins'][other], b['maxs'][other], b['mins'][2], h))
    return out


def _floor_pieces(m, data, room):
    """Layout floor slabs of `room`: boxes under the layout group whose top is the room floor."""
    group = m.node(data['id'])
    lo, hi = room['interior']['mins'], room['interior']['maxs']
    out = []
    for node in group.get('children', []):
        if node.type != 'CMapMesh':
            continue
        b = checks._mesh_bounds(node)
        if not b or abs(b[1][2] - room['floor_z']) > 0.01:
            continue
        # The room's slab covers its interior and the pads under its walls.
        pad = data['wall'] + 0.01
        if not (b[0][0] >= lo[0] - pad and b[1][0] <= hi[0] + pad and b[0][1] >= lo[1] - pad
                and b[1][1] <= hi[1] + pad):
            continue
        if len(checks._vertices(node)) != 8:
            continue
        mats = [str(x) for x in node['meshData'].get('materials', [])]
        out.append((node, b, mats[0] if mats else mesh.DEFAULT_MATERIAL))
    return out


def paved_rooms(m):
    """{(layout name, room id): paving metadata} of existing add_paving groups."""
    out = {}
    for node in m.iter_nodes():
        raw = node.get(PAVING_KEY)
        if not raw:
            continue
        data = json.loads(raw)
        for rid in data.get('rooms', []):
            out[(data.get('layout'), rid)] = {**data, 'id': node['nodeID']}
    return out


def sidewalk_top(m, axis, plane, normal_sign, start, end, z0):
    """Height of a sidewalk running along a wall face (plane on `axis`), or None."""
    best = 0.0
    covered = 0.0
    for node in m.iter_nodes():
        raw = node.get(PAVING_KEY)
        if not raw:
            continue
        for s in json.loads(raw).get('sidewalks', []):
            lo, hi = s['mins'], s['maxs']
            near = lo[axis] - 2 <= plane <= hi[axis] + 2
            if not near or abs(lo[2] + 1 - z0) > 1.5:
                continue
            other = 1 - axis
            overlap = min(hi[other], end) - max(lo[other], start)
            if overlap > 0:
                covered += overlap
                best = max(best, hi[2] - z0)
    return best if covered >= 0.5 * (end - start) else None


def _stair_plan(data, rooms, wanted):
    """Staircases (in the lower room) for passages that join two floor levels."""
    out = []
    for h in data['openings']:
        pair = h.get('between')
        if not pair or not any(r in wanted for r in pair):
            continue
        ra, rb = rooms[pair[0]], rooms[pair[1]]
        low, high = (ra, rb) if ra['floor_z'] < rb['floor_z'] else (rb, ra)
        dz = high['floor_z'] - low['floor_z']
        if dz < 2:
            continue
        side = next((s for s in layout.SIDES if _openings_on(data, low, s) and
                     any(o[3] is h for o in _openings_on(data, low, s))), None)
        if side is None:
            continue
        axis = 0 if side[1] == 'x' else 1
        other = 1 - axis
        llo, lhi = low['interior']['mins'], low['interior']['maxs']
        plane = lhi[axis] if side[0] == '+' else llo[axis]
        inward = -1 if side[0] == '+' else 1
        count = max(1, math.ceil(dz / STEP_RISE))
        run = count * STEP_RUN
        if run > (lhi[axis] - llo[axis]) - 64:
            raise OpError(f'steps from {low["id"]} up to {high["id"]} need {run:.0f} units of floor in '
                          f'{low["id"]}; make the room deeper or add a ramp/lift yourself')
        b = h['bounds']
        u0, u1 = max(b['mins'][other], llo[other]), min(b['maxs'][other], lhi[other])
        lo3, hi3 = [0.0, 0.0, low['floor_z'] - 1], [0.0, 0.0, high['floor_z']]
        lo3[axis], hi3[axis] = sorted((plane, plane + inward * run))
        lo3[other], hi3[other] = u0, u1
        out.append({'from': low['id'], 'to': high['id'], 'room': low['id'], 'mins': lo3, 'maxs': hi3,
                    'steps': count, 'rise': ('+' if inward < 0 else '-') + 'xy'[axis]})
    return out


def add_paving(project, m, op, refs):
    _strip_keys(op, KEYS, 'paving')
    name = op.get('name', 'paving')
    if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9_]{1,48}', name):
        raise OpError('paving.name must be 1..48 chars a-z0-9_')
    if any(n.name == 'paving:' + name for n in m.iter_nodes()):
        raise OpError(f'paving {name} already exists; delete its group or use another name')
    style_name = op.get('style', 'inferno')
    if isinstance(style_name, dict) and {'my', 'surfaces', 'materials'} <= set(style_name):
        style, style_name = style_name, 'my:' + style_name['my']      # an own style (ext/my.py made it)
    elif not isinstance(style_name, str) or style_name not in STYLES:
        raise OpError(f'paving.style must be one of {sorted(STYLES)} or "my:<own style>"')
    else:
        style = STYLES[style_name]
    plans = [d for d in layout.plans(m) if not op.get('layout') or d['name'] == op['layout']]
    if not plans:
        raise OpError('add_paving needs an add_layout/add_comp_plan skeleton (no layout found)')
    if len(plans) > 1 and not op.get('layout'):
        raise OpError(f'several layouts: give layout (one of {[d["name"] for d in plans]})')
    data = plans[0]
    rooms = data['rooms']
    wanted = op.get('rooms', 'all')
    if wanted == 'all':
        wanted = [rid for rid, r in rooms.items() if not r.get('ceiling') and not r.get('in')]
    if not isinstance(wanted, list) or not wanted or any(r not in rooms for r in wanted):
        raise OpError(f'paving.rooms must be "all" or a list of room ids; available: {", ".join(rooms)}')
    already = paved_rooms(m)
    for rid in wanted:
        if (data['name'], rid) in already:
            raise OpError(f'room {rid} is already paved by paving group {already[(data["name"], rid)]["id"]}; '
                          'delete that group first or leave the room out')
    areas = op.get('areas', {})
    if not isinstance(areas, dict) or any(r not in rooms for r in areas):
        raise OpError('paving.areas must be {room id: {surface, floor, sidewalk, gutter, band, border}}')
    base_walk = op.get('sidewalk', {})
    if base_walk is not False:
        _strip_keys(base_walk, SIDEWALK_KEYS, 'paving.sidewalk')
    mats = {k: _material(project, v) for k, v in style['materials'].items()}
    sites = _site_rooms(m, rooms)
    t = data['wall']

    group = m.new_group('paving:' + name)
    structure._set_group_defaults(group)
    m.attach(group)
    layout_group = m.node(data['id'])

    def box(lo, hi, material, materials=None, parent=None):
        node = m.new_mesh(mesh.box(lo, hi, material, materials), texture_size=project.texture_size)
        m.attach(node, parent or group)
        return node

    surfaces, walks, steps_meta, lifted, floor_boxes = {}, [], [], 0, 0
    walk_rects = {}
    stairs = _stair_plan(data, rooms, wanted) if op.get('steps', True) else []
    for rid in wanted:
        room = rooms[rid]
        spec = areas.get(rid, {})
        _strip_keys(spec, AREA_KEYS, f'paving.areas.{rid}')
        lo, hi = room['interior']['mins'], room['interior']['maxs']
        w, d = hi[0] - lo[0], hi[1] - lo[1]
        named_square = any(k in rid.lower() for k in ('plaza', 'piazza', 'square'))
        surface = spec.get('surface') or ('plaza' if rid in sites or named_square
                                          else 'street' if min(w, d) >= 288 else 'alley')
        if surface not in style['surfaces']:
            raise OpError(f'areas.{rid}.surface must be one of {sorted(style["surfaces"])}')
        preset = style['surfaces'][surface]
        surfaces[rid] = surface
        floor_mat = _material(project, spec.get('floor', preset['floor']))
        preset_walk = 0 if room.get('outdoor') else preset.get('sidewalk', 0)
        walk = None
        if base_walk is not False or 'sidewalk' in spec:
            walk = _sidewalk_cfg(spec.get('sidewalk'), preset_walk, base_walk or {}, f'areas.{rid}.sidewalk')
        if walk and min(w, d) - 2 * walk['width'] < 128 and 'sidewalk' not in spec:
            walk = None
        gutter = _strip_cfg(spec.get('gutter'), preset.get('gutter', 0) if walk else 0, mats['gutter'],
                            f'areas.{rid}.gutter')
        band = _strip_cfg(spec.get('band'), preset.get('band', 0), mats['band'], f'areas.{rid}.band')
        border = _strip_cfg(spec.get('border'), preset.get('border', 0), mats['border'], f'areas.{rid}.border')
        z = room['floor_z']
        inner = (lo[0], lo[1], hi[0], hi[1])

        # Sidewalk strips along the walls, cut at passages; X walls own the corners.
        pieces = []
        if walk:
            sides = layout.SIDES if walk['sides'] == 'auto' else walk['sides']
            ww = walk['width']
            for side in sorted(sides, key=lambda s: s[1] != 'x'):
                axis = 0 if side[1] == 'x' else 1
                other = 1 - axis
                plane = hi[axis] if side[0] == '+' else lo[axis]
                inward = -1 if side[0] == '+' else 1
                a0, a1 = lo[other], hi[other]
                if axis == 1:        # Y walls stop at the X sidewalks
                    if '-x' in sides:
                        a0 += ww
                    if '+x' in sides:
                        a1 -= ww
                spans = [(a0, a1)]
                cuts = [(u0, u1) for u0, u1, bottom, _ in _openings_on(data, room, side) if bottom <= z + 128]
                strip = sorted((plane, plane + inward * ww))
                cuts += [(st['mins'][other], st['maxs'][other]) for st in stairs   # a sidewalk ends at stairs
                         if st['room'] == rid and st['mins'][axis] < strip[1] and st['maxs'][axis] > strip[0]]
                for u0, u1 in cuts:
                    spans = [s for a, b in spans for s in ((a, min(b, u0)), (max(a, u1), b)) if s[1] - s[0] > 1e-3]
                for a, b in spans:
                    if b - a < 48:
                        continue
                    pieces.append((side, axis, other, plane, inward, a, b))
        # Floor regions: base, then border (sides without sidewalk), band, gutters in front of curbs.
        regions = [(inner, floor_mat)]
        for h in data['openings']:
            # The threshold under a passage belongs to the floor slab of one of the rooms: pave it too.
            b = h['bounds']
            if abs(b['mins'][2] - z) < 1:
                r = _rect_and((b['mins'][0], b['mins'][1], b['maxs'][0], b['maxs'][1]),
                              (lo[0] - t, lo[1] - t, hi[0] + t, hi[1] + t))
                if r:
                    regions.append((r, floor_mat))
        walled = {p[0] for p in pieces}
        if border:
            bw = border['width']
            mat = _material(project, border['material'])
            for side in layout.SIDES:
                if side in walled:
                    continue
                rect = {'-x': (lo[0], lo[1], lo[0] + bw, hi[1]), '+x': (hi[0] - bw, lo[1], hi[0], hi[1]),
                        '-y': (lo[0], lo[1], hi[0], lo[1] + bw), '+y': (lo[0], hi[1] - bw, hi[0], hi[1])}[side]
                regions = _paint(regions, rect, mat)
        if band:
            bw = band['width'] / 2
            mat = _material(project, band['material'])
            if w >= d:
                cy = (lo[1] + hi[1]) / 2
                regions = _paint(regions, (lo[0], cy - bw, hi[0], cy + bw), mat)
            else:
                cx = (lo[0] + hi[0]) / 2
                regions = _paint(regions, (cx - bw, lo[1], cx + bw, hi[1]), mat)
        if gutter:
            mat = _material(project, gutter['material'])
            for side, axis, other, plane, inward, a, b in pieces:
                e0 = plane + inward * walk['width']
                e1 = e0 + inward * gutter['width']
                rect = [0, 0, 0, 0]
                rect[axis], rect[axis + 2] = min(e0, e1), max(e0, e1)
                rect[other], rect[other + 2] = a, b
                regions = _paint(regions, tuple(rect), mat)

        # Rebuild this room's floor slabs: same boxes, area materials on top.
        for node, b, orig in _floor_pieces(m, data, room):
            prect = (b[0][0], b[0][1], b[1][0], b[1][1])
            parts = []
            rest = [prect]
            for rect, mat in regions:
                cut = _rect_and(prect, rect)
                if cut:
                    parts.append((cut, mat))
                    rest = [p for q in rest for p in _rect_sub(q, rect)]
            parts += [(r, orig) for r in rest]
            m.remove(node)
            for r, mat in parts:
                box((r[0], r[1], b[0][2]), (r[2], r[3], b[1][2]), orig, {'top': mat}, parent=layout_group)
                floor_boxes += 1

        # Sidewalks and curbs: solid, sunk 1 unit into the floor and 1 unit into the wall.
        if walk:
            top = z + walk['height']
            smat = _material(project, walk.get('material') or mats['sidewalk'])
            cmat = _material(project, walk.get('curb_material') or mats['curb'])
            for side, axis, other, plane, inward, a, b in pieces:
                edge = plane + inward * walk['width']
                curb_in = edge - inward * walk['curb']
                spans = [(plane - inward * 1, curb_in, smat)]
                if walk['curb']:
                    spans.append((curb_in, edge, cmat))
                for c0, c1, mat in spans:
                    lo3, hi3 = [0.0, 0.0, z - 1], [0.0, 0.0, top]
                    lo3[axis], hi3[axis] = min(c0, c1), max(c0, c1)
                    lo3[other], hi3[other] = a, b
                    box(lo3, hi3, cmat, {'top': mat})
                lo3, hi3 = [0.0, 0.0, z - 1], [0.0, 0.0, top]
                lo3[axis], hi3[axis] = sorted((plane - inward, edge))
                lo3[other], hi3[other] = a, b
                walks.append({'room': rid, 'side': side, 'mins': lo3, 'maxs': hi3})
                walk_rects.setdefault(rid, []).append((lo3, hi3))

    # Steps where a passage joins two floor levels: a staircase in the lower room.
    if stairs:
        smat = _material(project, mats['steps'])
        cmat = _material(project, mats['curb'])
        for st in stairs:
            stair = mesh.stairs(st['mins'], st['maxs'], st['steps'], st['rise'], cmat)
            for f in stair.faces:
                if stair.normal(f)[2] > 0.9:
                    f.material = smat
            node = m.new_mesh(stair, texture_size=project.texture_size)
            m.attach(node, group)
            steps_meta.append({k: st[k] for k in ('from', 'to', 'mins', 'maxs', 'steps')})

    # Props and nav seeds standing on the old floor where a sidewalk now is: lift them onto it.
    for ent in m.entities():
        cls = checks._props(ent).get('classname', '')
        if not (cls.startswith('prop_') or cls == 'point_nav_walkable'):
            continue
        o = list(ent.get('origin', (0, 0, 0)))
        for rects in walk_rects.values():
            for lo3, hi3 in rects:
                if (lo3[0] - 4 <= o[0] <= hi3[0] + 4 and lo3[1] - 4 <= o[1] <= hi3[1] + 4
                        and lo3[2] <= o[2] <= lo3[2] + 3 + (8 if cls == 'point_nav_walkable' else 0)):
                    ent.set('origin', 'vector3', (o[0], o[1], o[2] + hi3[2] - lo3[2] - 1))
                    lifted += 1
                    break
            else:
                continue
            break

    meta = {'schema': 1, 'name': name, 'layout': data['name'], 'style': style_name, 'rooms': wanted,
            'surfaces': surfaces, 'sidewalks': walks, 'steps': steps_meta}
    group.set(PAVING_KEY, 'string', json.dumps(meta, separators=(',', ':')))
    if op.get('ref'):
        refs[op['ref']] = group['nodeID']
    return {'op': 'add_paving', 'id': group['nodeID'], 'rooms': surfaces, 'floor_boxes': floor_boxes,
            'sidewalks': len(walks), 'steps': len(steps_meta), 'lifted': lifted}
