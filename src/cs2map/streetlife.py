"""Deterministic Inferno street dressing driven by quarter metadata.

The operation intentionally stays in this extension: layout, paving and facade
metadata are read as contracts and the resulting static props are grouped under
``street:<name>``.  Stock bounds and facing come from the local prop atlas.
"""
from __future__ import annotations

import itertools
import json
import math
import random
from pathlib import Path

from . import atlas, checks, dressing, kits, layout, lighting, mesh, structure, visual
from .project import OpError

DATA = Path(__file__).parent / 'data' / 'street_sets' / 'inferno.json'
STREET_KEY = 'cs2map_streetlife'
FACADE_KEY = 'cs2map_facade'
PAVING_KEY = 'cs2map_paving'
ROLES = ('door_plant', 'planter', 'wall_clutter', 'bike', 'bench', 'bin', 'cafe', 'market',
         'lamp_post', 'wall_lamp', 'street_sign', 'drain')
SOLID_ROLES = {'wall_clutter', 'bike', 'bench', 'bin', 'cafe', 'market', 'lamp_post'}


def _number(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not low <= value <= high:
        raise OpError(f'{label} must be a finite number in {low}..{high}')
    return float(value)


def _seed(value):
    if isinstance(value, bool) or not isinstance(value, int) or not -2147483648 <= value <= 2147483647:
        raise OpError('seed must be a 32-bit integer')
    return value


def _unit(v, label):
    length = math.sqrt(sum(float(x) * float(x) for x in v))
    if length < 1e-7:
        raise OpError(f'{label} must be nonzero')
    return [float(x) / length for x in v]


def _load_set(style):
    try:
        data = json.loads(DATA.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise OpError(f'cannot read street set {DATA}: {exc}') from None
    if data.get('schema') != 1 or data.get('style') != style:
        raise OpError(f'invalid street set for style {style!r}')
    roles = data.get('roles')
    if not isinstance(roles, dict):
        raise OpError('street set roles must be an object')
    return data


def _room_map(m, requested):
    plans = layout.plans(m)
    if not plans:
        raise OpError('dress_street needs an add_layout plan')
    if requested == 'all' or requested is None:
        selected = [(p, rid, room) for p in plans for rid, room in p['rooms'].items()]
    elif isinstance(requested, str):
        selected = [(p, requested, p['rooms'][requested]) for p in plans if requested in p['rooms']]
    elif isinstance(requested, list) and requested and all(isinstance(x, str) for x in requested):
        selected = [(p, rid, p['rooms'][rid]) for p in plans for rid in requested if rid in p['rooms']]
        if len({rid for _, rid, _ in selected}) != len(set(requested)):
            missing = sorted(set(requested) - {rid for _, rid, _ in selected})
            raise OpError(f'rooms not found: {", ".join(missing)}')
    else:
        raise OpError('rooms must be "all", a room id, or a non-empty list of room ids')
    if not selected:
        raise OpError('no rooms selected')
    return selected


def _paving(m, plan_name):
    for node in m.iter_nodes():
        raw = node.get(PAVING_KEY)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if data.get('layout') == plan_name:
            return data
    return None


def _faces(m, room_rows):
    """Return facade faces matched to rooms by their saved world plane/range."""
    faces = []
    for node in m.iter_nodes():
        raw = node.get(FACADE_KEY)
        if not raw:
            continue
        try:
            data = json.loads(raw)
            axis, plane = int(data['axis']), float(data['plane'])
            normal = _unit(data['normal'], 'facade.normal')
            start, end = float(data.get('start', 0)), float(data.get('end', 0))
            # In schema 2 house coordinates are absolute u coordinates.
            if data.get('houses'):
                start = min(float(h['from']) for h in data['houses'])
                end = max(float(h['to']) for h in data['houses'])
        except (KeyError, TypeError, ValueError):
            continue
        tangent = 1 - axis
        for plan, rid, room in room_rows:
            lo, hi = room['interior']['mins'], room['interior']['maxs']
            edge = lo[axis] if normal[axis] > 0 else hi[axis]
            if abs(edge - plane) > 32:
                continue
            if end <= lo[tangent] or start >= hi[tangent]:
                continue
            faces.append({'room': rid, 'layout': plan['name'], 'axis': axis, 'plane': plane,
                          'normal': normal, 'start': max(start, lo[tangent]),
                          'end': min(end, hi[tangent]), 'z0': room['floor_z'],
                          'houses': data.get('houses', []), 'node': node})
    # A facade is optional for authoring previews; fallback faces keep the
    # operation useful on a paved layout while still using actual room bounds.
    for plan, rid, room in room_rows:
        lo, hi = room['interior']['mins'], room['interior']['maxs']
        for axis in (0, 1):
            for sign in (-1, 1):
                normal = [0., 0., 0.]; normal[axis] = -float(sign)
                plane = lo[axis] if sign < 0 else hi[axis]
                tangent = 1 - axis
                if any(f['layout'] == plan['name'] and f['room'] == rid and f['axis'] == axis
                       and abs(f['plane'] - plane) < 32 for f in faces):
                    continue
                faces.append({'room': rid, 'layout': plan['name'], 'axis': axis, 'plane': plane,
                              'normal': normal, 'start': lo[tangent], 'end': hi[tangent],
                              'z0': room['floor_z'], 'houses': [], 'node': None})
    return faces


def _normal_side(room, side):
    axis = 0 if side.endswith('x') else 1
    normal = [0., 0., 0.]
    normal[axis] = 1. if side[0] == '-' else -1.
    return axis, normal


def _clear_boxes(room_rows, clear_width):
    out = []
    half = clear_width / 2
    for _, _, room in room_rows:
        lo, hi = room['interior']['mins'], room['interior']['maxs']
        z0 = room['floor_z']
        cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
        out.extend([
            ([lo[0], cy - half, z0], [hi[0], cy + half, z0 + 112]),
            ([cx - half, lo[1], z0], [cx + half, hi[1], z0 + 112])
        ])
    return out


def _opening_clear(m, room_rows, clear_width):
    out = []
    plans = {p['name']: p for p, _, _ in room_rows}
    selected = {(p['name'], rid): room for p, rid, room in room_rows}
    for (plan_name, room_id), room in selected.items():
        plan = plans[plan_name]
        for h in plan['openings']:
            if h.get('room') != room['id'] and room['id'] not in (h.get('between') or []):
                continue
            lo, hi = h['bounds']['mins'][:], h['bounds']['maxs'][:]
            # 'wall' names the side of the opening's own room; a shared opening is seen from both rooms.
            side = h.get('wall') if h.get('room') == room['id'] else None
            if not side:
                # Shared openings use the side of the selected room closest to the hole.
                rlo, rhi = room['interior']['mins'], room['interior']['maxs']
                axis = 0 if abs((lo[0] + hi[0]) / 2 - rlo[0]) < 20 or abs((lo[0] + hi[0]) / 2 - rhi[0]) < 20 else 1
                side = ('-' if abs((lo[axis] + hi[axis]) / 2 - rlo[axis]) < 20 else '+') + ('x' if axis == 0 else 'y')
            axis, normal = _normal_side(room, side)
            if normal[axis] > 0:
                hi[axis] = min(hi[axis] + clear_width, room['interior']['maxs'][axis])
            else:
                lo[axis] = max(lo[axis] - clear_width, room['interior']['mins'][axis])
            lo[2] = room['floor_z']; hi[2] = room['floor_z'] + 112
            out.append((lo, hi))
    return out


def _record(project, model, cfg):
    models = atlas.index(project)
    if model not in models:
        raise OpError(f'street set model is not installed: {model}')
    info = atlas.measure(project, [model])[model]
    if not info.get('lo') or not info.get('hi'):
        raise OpError(f'cannot measure street model: {model}')
    note = atlas.notes_all().get(model) or {}
    front = note.get('front') or cfg.get('front', 'none')
    if front not in kits.FRONTS and front != 'none':
        front = cfg.get('front', 'none')
    mount = note.get('mount', cfg.get('mount', 'floor'))
    if mount == 'any':
        mount = cfg.get('mount', 'floor')
    return {'model': model, 'bounds': [list(info['lo']), list(info['hi'])],
            'front': front, 'mount': mount, 'solid': bool(info.get('phys'))}


def _rotated_bounds(bounds, angles, scale=1.0):
    pts = [structure._rotate(tuple(v * scale for v in p), angles)
           for p in itertools.product(*zip(*bounds))]
    return [[min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]], pts


def _place_geometry(rec, centre, floor_z, face=None, wall_offset=0, wall_height=96,
                    yaw=0.0, scale=1.0, angles_override=None):
    if face is None:
        angles = angles_override or [0., yaw, 0.]
        local, _ = _rotated_bounds(rec['bounds'], angles, scale)
        origin = [centre[0] - (local[0][0] + local[1][0]) / 2,
                  centre[1] - (local[0][1] + local[1][1]) / 2,
                  floor_z - local[0][2]]
    else:
        normal = face['normal']; axis = face['axis']; tangent = 1 - axis
        angles = kits._angles(rec['front'] if rec['front'] in kits.FRONTS else '+x', normal, [0, 0, 1])
        local, pts = _rotated_bounds(rec['bounds'], angles, scale)
        rear = min(sum(p[i] * normal[i] for i in range(3)) for p in pts)
        target = sum((face['plane'] + normal[axis] * wall_offset if i == axis else centre[i]) * normal[i]
                     for i in range(3))
        shift = target - rear
        origin = [0., 0., 0.]
        origin[tangent] = centre[tangent] - (local[0][tangent] + local[1][tangent]) / 2
        origin[axis] = normal[axis] * shift
        origin[2] = floor_z + wall_height - local[0][2]
    world = [[local[0][i] + origin[i], local[1][i] + origin[i]] for i in range(3)]
    return origin, angles, [[world[i][0] for i in range(3)], [world[i][1] for i in range(3)]]


def _add_prop(project, m, group, rec, role, origin, angles, scale, solid):
    kv = {'model': rec['model'], 'solid': 6 if solid else 0}
    node = m.new_entity('prop_static', kv, origin, angles, [scale, scale, scale],
                        defaults=project.catalog.defaults('prop_static'))
    node.set('cs2map_street_role', 'string', role)
    node.set('cs2map_street_model', 'string', rec['model'])
    m.attach(node, group)
    return node


def _room_floor(m, face, paving_data):
    z0 = face['z0']
    if paving_data:
        top = __import__('cs2map.paving', fromlist=['sidewalk_top']).sidewalk_top(
            m, face['axis'], face['plane'], face['normal'][face['axis']], face['start'], face['end'], z0)
        if top is not None:
            z0 += top
    return z0


def _face_openings(face):
    out = []
    for house in face.get('houses', []):
        for key in ('doors', 'shops'):
            for entry in house.get(key, []):
                try:
                    u, width, depth = float(entry[0]), float(entry[1]), float(entry[2])
                except (TypeError, ValueError, IndexError):
                    continue
                out.append((u, width, depth, key))
    return out


def dress_street(project, m, op, refs):
    allowed = {'op', 'name', 'rooms', 'style', 'density', 'seed', 'clear_width', 'laundry', 'wires', 'ref'}
    extra = set(op) - allowed
    if extra:
        raise OpError('unknown dress_street fields: ' + ', '.join(sorted(extra)))
    name = op.get('name')
    if not isinstance(name, str) or not __import__('re').fullmatch(r'[a-z0-9_]{1,48}', name):
        raise OpError('dress_street.name must be 1..48 lowercase letters, digits or underscores')
    if any(node.name == 'street:' + name for node in m.iter_nodes()):
        raise OpError(f'street dressing {name} already exists')
    style = op.get('style', 'inferno')
    if style != 'inferno':
        raise OpError('dress_street currently supports style="inferno" only')
    density = _number(op.get('density', 1.0), 'density', 0, 1)
    seed = _seed(op.get('seed', 0))
    clear_width = _number(op.get('clear_width', 112), 'clear_width', 96, 512)
    for flag in ('laundry', 'wires'):
        if flag in op and not isinstance(op[flag], bool):
            raise OpError(flag + ' must be true or false')
    room_rows = _room_map(m, op.get('rooms', 'all'))
    data = _load_set(style)
    faces = _faces(m, room_rows)
    paving_by_layout = {p['name']: _paving(m, p['name']) for p, _, _ in room_rows}
    clear = _clear_boxes(room_rows, clear_width) + _opening_clear(m, room_rows, clear_width)
    for face in faces:
        for u, width, _, _ in _face_openings(face):
            axis, tangent = face['axis'], 1 - face['axis']
            lo, hi = [0., 0., face['z0']], [0., 0., face['z0'] + 112]
            lo[tangent], hi[tangent] = u - width / 2 - 8, u + width / 2 + 8
            a, b = face['plane'], face['plane'] + face['normal'][axis] * clear_width
            lo[axis], hi[axis] = sorted((a, b))
            clear.append((lo, hi))
    # Include facade props as well as the actual solid triangles. A frame or
    # gutter is not a valid place to hide a sign, wire or planter.
    obstacle_bounds = [entry['bounds'] for entry in visual._prop_entries(project, m)]
    world_meshes = visual._solid_meshes(m)
    visible_meshes = visual._visible_meshes(m)
    floor_grid = visual._FloorGrid([tri for _, _, tris in world_meshes for tri in tris])
    group = m.new_group('street:' + name)
    structure._set_group_defaults(group); m.attach(group)
    rng = random.Random(seed)
    placed = []
    counts = {role: 0 for role in (*ROLES, 'laundry', 'wires')}
    skipped = {}
    attempts = 0
    records = {}
    support_ids = []

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    def getrec(role, model=None):
        # The set is a real palette: seed chooses its variants. A compact
        # anchor is tried separately if a large randomly selected prop fails.
        if model is None:
            cfg = data['roles'][role]
            base = cfg.get('base_model')
            model = base if base and rng.random() < cfg.get('base_share', 1.0) else rng.choice(cfg['models'])
        key = (role, model)
        if key not in records:
            records[key] = _record(project, model, data['roles'][role])
        return records[key]

    def room_for(face):
        for plan, rid, room in room_rows:
            if plan['name'] == face['layout'] and rid == face['room']:
                return plan, rid, room
        return None

    def try_prop(role, face, u, wall_height=96, offset=None, floor=True, yaw=None,
                 model=None, scale=1.0, z_override=None, reserve=True, angles=None, gap=2, base=None):
        nonlocal attempts
        attempts += 1
        cfg = data['roles'][role]
        rec = getrec(role, model)
        if z_override is None and rec['mount'] in {'floor', 'wall'}:
            floor = rec['mount'] == 'floor'
        plan, rid, room = room_for(face)
        if not plan:
            skip('room'); return None
        surface = (paving_by_layout.get(plan['name']) or {}).get('surfaces', {}).get(rid, 'street')
        plaza_like = rid in {'plaza', 'courtyard'}
        if surface not in cfg.get('surfaces', []) and not (plaza_like and role in {'bench', 'planter', 'cafe', 'market', 'lamp_post'}):
            skip('surface'); return None
        floor_z = _room_floor(m, face, paving_by_layout.get(plan['name']))
        axis, normal, tangent = face['axis'], face['normal'], 1 - face['axis']
        if offset is None:
            offset = cfg.get('wall_offset', 32)
        point = [0., 0., floor_z]
        point[axis] = face['plane'] + normal[axis] * offset
        point[tangent] = u
        if z_override is not None:
            point[2] = z_override
        support_z = z_override if (floor and z_override is not None) else floor_z
        if floor:
            if z_override is None:
                hit = floor_grid.floor((point[0], point[1], floor_z + 16), 64)
                if hit is None:
                    skip('floor'); return None
                support_z = hit
            if angles is None and yaw is None:
                if role == 'bike':
                    long_axis = max(range(2), key=lambda i: rec['bounds'][1][i] - rec['bounds'][0][i])
                    yaw = 90.0 if long_axis != tangent else 0.0
                elif rec['front'] in kits.FRONTS:
                    angles = kits._angles(rec['front'], normal, [0, 0, 1])
                else:
                    yaw = 0.0
            origin, angles, bounds = _place_geometry(rec, point, support_z, None,
                                                     yaw=0.0 if yaw is None else yaw,
                                                     scale=scale, angles_override=angles)
        else:
            origin, angles, bounds = _place_geometry(rec, point, floor_z, face, offset, wall_height,
                                                     scale=scale)
        lo, hi = room['interior']['mins'], room['interior']['maxs']
        room_box = [lo, hi]
        if not dressing.inside(bounds, room_box, -1 if z_override is not None else 0):
            skip('bounds'); return None
        if reserve and any(dressing.overlaps(bounds, box, 1) for box in clear):
            skip('passage'); return None
        if any(dressing.overlaps(bounds, old['bounds'], gap) for old in placed if old is not base):
            skip('overlap'); return None
        if any(dressing.overlaps(bounds, b, 0) for b in obstacle_bounds):
            skip('geometry'); return None
        for _, b, triangles in world_meshes:
            if dressing.overlaps(bounds, b, 0) and min(visual._depth(bounds, b)) > 2 \
                    and visual._penetration(bounds, triangles) is not None:
                skip('geometry'); return None
        if not floor:
            # A fallback room edge can be a doorway, not a wall. Verify the
            # actual mount with a horizontal ray from the model's rear face.
            rear = [(bounds[0][i] + bounds[1][i]) / 2 for i in range(3)]
            rear[axis] = bounds[0 if normal[axis] > 0 else 1][axis] + normal[axis] * .5
            behind = [-x for x in normal]
            backed = any((distance := checks._ray(rear, behind, tri)) is not None and distance <= 16
                         for _, b, tris in visible_meshes if dressing.overlaps(bounds, b, 16)
                         for tri in tris)
            if not backed:
                skip('wall'); return None
        solid = bool(cfg.get('collision', role in SOLID_ROLES) and rec['solid'])
        node = _add_prop(project, m, group, rec, role, origin, angles, scale, solid)
        item = {'id': node['nodeID'], 'role': role, 'model': rec['model'],
                'room': rid, 'origin': [round(x, 3) for x in origin],
                'angles': [round(x, 3) for x in angles], 'bounds': bounds,
                'solid': solid}
        placed.append(item); counts[role] += 1
        fills = cfg.get('fill_models') or ([cfg['fill_model']] if cfg.get('fill_model') else [])
        if fills and rec['model'] == cfg.get('base_model'):
            # A plant grows from the soil, a few units under the pot rim.
            for fill in rng.sample(fills, len(fills)):
                if try_prop(role, face, u, offset=offset, yaw=rng.choice((0, 90, 180, 270)), model=fill,
                            z_override=bounds[1][2] - cfg.get('fill_sink', 0), gap=0, base=item):
                    break
            else:
                # An empty pot reads as a mistake: no plant fits, so no pot either.
                m.remove(node); placed.remove(item); counts[role] -= 1
                skip('empty_pot'); return None
        if role in {'lamp_post', 'wall_lamp'}:
            light_origin = [sum(bounds[j][i] for j in range(2)) / 2 for i in range(3)]
            light_origin[2] = (bounds[1][2] - 16 if role == 'lamp_post'
                               else max(light_origin[2] - 10, floor_z + 72))
            light = lighting.add_light(project, m, {'op': 'add_light', 'kind': 'wall',
                'origin': light_origin, 'temperature': 2800, 'brightness': 'dim', 'range': 320,
                'model': False}, refs)
            for lid in light.get('ids', [light.get('id')]):
                if lid:
                    light_node = m.node(lid)
                    light_node.set('cs2map_street_role', 'string', role)
                    m.remove(light_node); m.attach(light_node, group)
            item['light_id'] = light.get('id')
        return item

    # Plants sit in pairs at saved doors and single planters at shop fronts.
    for face in faces:
        openings = _face_openings(face)
        for u, width, _, kind in openings:
            if kind == 'shops':
                continue
            if density <= 0 or rng.random() > density:
                skip('density'); continue
            for side in (-1, 1):
                try_prop('door_plant', face, u + side * (width / 2 + 38), offset=52)

    # Cafe furniture is authored as one compact cluster per shop on a plaza wall.
    for face in faces:
        plan, rid, room = room_for(face)
        surface = (paving_by_layout.get(face['layout']) or {}).get('surfaces', {}).get(rid, 'street')
        if surface not in {'plaza', 'courtyard'} and rid not in {'plaza', 'courtyard'}:
            continue
        for u, width, _, kind in _face_openings(face):
            if kind != 'shops' or density <= 0 or rng.random() > density:
                continue
            cfg = data['roles']['cafe']
            tables, chairs = cfg.get('table_models', cfg['models'][:1]), cfg.get('chair_models', cfg['models'][1:2])
            stand_model = cfg.get('stand_model', cfg['models'][4] if len(cfg['models']) > 4 else None)
            canopies = cfg.get('canopy_models', cfg['models'][5:])
            # The near-wall strip is the shop approach. Put the terrace beyond
            # that strip; each candidate is still tested against both routes.
            side = -1 if rng.random() < .5 else 1
            table_model, chair_model = rng.choice(tables), rng.choice(chairs)
            terrace = []
            for table_u in (u, u + side * 56, u - side * 56, u + side * 132, u - side * 132):
                if len(terrace) >= cfg.get('tables_per_shop', 2):
                    break
                if any(abs(table_u - t[0]) < 104 for t in terrace):
                    continue
                for table_offset in (176, 224):
                    table = try_prop('cafe', face, table_u, offset=table_offset, model=table_model, yaw=0)
                    if table:
                        terrace.append((table_u, table_offset, table)); break
            for table_u, table_offset, table in terrace:
                # Chairs face the table (their front is +X); each is independently checked.
                half = (table['bounds'][1][1 - face['axis']] - table['bounds'][0][1 - face['axis']]) / 2
                for du in (-(half + 16), half + 16):
                    tangent = [0.0, 0.0]; tangent[1 - face['axis']] = -1.0 if du > 0 else 1.0
                    yaw = math.degrees(math.atan2(tangent[1], tangent[0]))
                    try_prop('cafe', face, table_u + du, offset=table_offset, model=chair_model, yaw=yaw)
            if terrace and stand_model:
                table_u, table_offset, _ = terrace[0]
                parasol_u = table_u + side * 76
                stand = try_prop('cafe', face, parasol_u, offset=table_offset + 32, model=stand_model, yaw=0)
                if stand and canopies:
                    try_prop('cafe', face, parasol_u, offset=table_offset + 32,
                             model=rng.choice(canopies), yaw=0,
                             z_override=stand['bounds'][1][2] + 2, reserve=False)

    # General wall-side dressing. Keep the candidate cadence sparse enough for an Inferno street.
    generic = ('wall_clutter', 'bike', 'bench', 'planter', 'bin', 'market', 'lamp_post', 'wall_lamp', 'street_sign', 'drain')
    for face in faces:
        plan, rid, room = room_for(face)
        surface = (paving_by_layout.get(face['layout']) or {}).get('surfaces', {}).get(rid, 'street')
        step = max(96, min(240, (face['end'] - face['start']) / 4))
        us = [face['start'] + step * .5 + i * step for i in range(max(1, int((face['end'] - face['start']) / step)))]
        rng.shuffle(us)
        for index, u in enumerate(us):
            if density <= 0 or rng.random() > density:
                continue
            eligible = [r for r in generic if surface in data['roles'][r]['surfaces']
                        or (rid in {'plaza', 'courtyard'} and r in {'bench', 'planter', 'market', 'lamp_post'})]
            if not eligible:
                continue
            role = eligible[(index + seed) % len(eligible)]
            # Do not put wall clutter directly in a saved door/shop opening.
            if any(abs(u - ou) < ow / 2 + 38 for ou, ow, _, _ in _face_openings(face)):
                skip('opening'); continue
            height = 148 if role in {'wall_lamp', 'street_sign'} else 0
            try_prop(role, face, u, floor=role not in {'wall_lamp', 'street_sign'},
                     wall_height=height, offset=data['roles'][role].get('wall_offset', 42))

    # Guarantee one recognisable anchor for the plaza/street vignette when a
    # long random prop was rejected at a central corridor or an opening. The
    # edge-biased points leave the centre line available for route tests.
    for role, wanted_room, edge_offset in (
            ('bike', 'street', 74), ('bench', 'plaza', 104),
            ('market', 'plaza', 156), ('lamp_post', 'plaza', 112), ('planter', 'plaza', 88),
            ('wall_lamp', 'street', 2), ('street_sign', 'street', 2)):
        if counts[role] or density <= 0:
            continue
        candidates = [f for f in faces if f['room'] == wanted_room]
        for face in candidates:
            span = face['end'] - face['start']
            # Keep the anchor away from the end caps: long props such as bikes
            # otherwise fail the room bounds check even though the wall-side
            # lane itself is clear.
            margin = min(220.0, max(160.0, span * .2))
            if span <= margin * 2:
                margin = max(24.0, span * .25)
            us = [face['start'] + margin, face['end'] - margin]
            us.extend(face['start'] + span * f for f in (.25, .38, .62, .75))
            us.extend((face['start'] + 40, face['end'] - 40))
            for u in us:
                if try_prop(role, face, u, offset=edge_offset,
                            floor=role not in {'wall_lamp', 'street_sign'}, wall_height=148,
                            model=data['roles'][role].get('base_model') or data['roles'][role]['models'][0]):
                    break
            if counts[role]:
                break

    # Laundry and electric wires span opposite walls of narrow streets. The
    # long axis is measured, not inferred from the asset's file name.
    if op.get('laundry', False) and density > 0:
        for plan, rid, room in room_rows:
            surface = (paving_by_layout.get(plan['name']) or {}).get('surfaces', {}).get(rid, 'street')
            lo, hi = room['interior']['mins'], room['interior']['maxs']
            cross_axis = min(range(2), key=lambda i: hi[i] - lo[i])
            along_axis = 1 - cross_axis
            width = hi[cross_axis] - lo[cross_axis]
            if rid in {'plaza', 'courtyard'} or surface not in {'street', 'alley'} or width > 400:
                continue
            model = data['roles']['laundry']['models'][0]
            rec = getrec('laundry', model)
            long_axis = max(range(2), key=lambda i: rec['bounds'][1][i] - rec['bounds'][0][i])
            local_len = rec['bounds'][1][long_axis] - rec['bounds'][0][long_axis]
            if width / local_len < .745:
                skip('span'); continue
            scale = max(.75, min(1.25, width / local_len))
            top = room['floor_z'] + min(320, (hi[2] - lo[2]) * .7)
            bottom = top - (rec['bounds'][1][2] - rec['bounds'][0][2]) * scale
            for fraction in (.28, .68, .17, .82):
                at = lo[along_axis] + (hi[along_axis] - lo[along_axis]) * fraction
                normal = [0, 0, 0]; normal[along_axis] = 1
                face = {'room': rid, 'layout': plan['name'], 'axis': along_axis, 'plane': at,
                        'normal': normal, 'start': lo[cross_axis], 'end': hi[cross_axis], 'z0': room['floor_z']}
                item = try_prop('laundry', face, (lo[cross_axis] + hi[cross_axis]) / 2, offset=0, floor=True,
                                model=model, scale=scale, z_override=bottom, reserve=False,
                                angles=[0, 90 if long_axis != cross_axis else 0, 0])
                if not item:
                    continue
                # Straight, visible end ties connect a short garment model to
                # both walls. They touch the model and sink one unit in the wall.
                b = item['bounds']
                for a, end in ((lo[cross_axis] - 1, b[0][cross_axis]),
                               (b[1][cross_axis], hi[cross_axis] + 1)):
                    if end - a < .5:
                        continue
                    low, high = [0., 0., top - .4], [0., 0., top + .4]
                    low[along_axis], high[along_axis] = at - .4, at + .4
                    low[cross_axis], high[cross_axis] = a, end
                    node = m.new_mesh(mesh.box(low, high,
                                               'materials/dev/reflectivity_30.vmat'),
                                      texture_size=project.texture_size)
                    node.set('cs2map_street_role', 'string', 'laundry_tie')
                    m.attach(node, group); support_ids.append(node['nodeID'])
                break

    if op.get('wires', False) and density > 0:
        for plan, rid, room in room_rows:
            surface = (paving_by_layout.get(plan['name']) or {}).get('surfaces', {}).get(rid, 'street')
            lo, hi = room['interior']['mins'], room['interior']['maxs']
            cross_axis = min(range(2), key=lambda i: hi[i] - lo[i])
            along_axis = 1 - cross_axis
            width = hi[cross_axis] - lo[cross_axis]
            if rid in {'plaza', 'courtyard'} or surface not in {'street', 'alley'} or width > 400:
                continue
            choices = []
            for model in data['roles']['wires']['models']:
                rec = getrec('wires', model)
                length = rec['bounds'][1][2] - rec['bounds'][0][2]
                for n in range(1, 9):
                    scale = width / (length * n)
                    if .75 <= scale <= 1.25:
                        choices.append((n, abs(1 - scale), model, scale)); break
            if not choices:
                skip('span'); continue
            n, _, model, scale = min(choices)
            z = room['floor_z'] + min(356, (hi[2] - lo[2]) * .78)
            for fraction in (.72, .22, .83):
                normal = [0, 0, 0]; normal[along_axis] = 1
                face = {'room': rid, 'layout': plan['name'], 'axis': along_axis,
                        'plane': lo[along_axis] + (hi[along_axis] - lo[along_axis]) * fraction, 'normal': normal,
                        'start': lo[cross_axis], 'end': hi[cross_axis], 'z0': room['floor_z']}
                start = len(placed)
                for i in range(n):
                    item = try_prop('wires', face, lo[cross_axis] + width * (i + .5) / n,
                                    floor=True, z_override=z, offset=0, model=model,
                                    scale=scale, angles=[90, 90 * cross_axis, 0], reserve=False, gap=0)
                    if not item:
                        break
                if len(placed) - start == n:
                    break
                # Never leave a dangling half-span after one segment fails.
                for item in placed[start:]:
                    m.remove(m.node(item['id'])); counts['wires'] -= 1
                del placed[start:]

    metadata = {'schema': 1, 'name': name, 'style': style, 'rooms': sorted({rid for _, rid, _ in room_rows}),
                'layout': sorted({p['name'] for p, _, _ in room_rows}), 'density': density, 'seed': seed,
                'clear_width': clear_width, 'laundry': bool(op.get('laundry', False)),
                'wires': bool(op.get('wires', False)), 'counts': counts, 'skipped': skipped,
                'ids': [i['id'] for i in placed], 'support_ids': support_ids}
    group.set(STREET_KEY, 'string', json.dumps(metadata, separators=(',', ':')))
    if op.get('ref'):
        refs[op['ref']] = group['nodeID']
    return {'op': 'dress_street', 'id': group['nodeID'], 'group': 'street:' + name,
            'rooms': metadata['rooms'], 'counts': counts, 'placed': len(placed),
            'attempts': attempts, 'skipped': skipped, 'clear_width': clear_width,
            **({'ref': op['ref']} if op.get('ref') else {})}
