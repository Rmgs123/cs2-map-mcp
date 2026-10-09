"""Persisted level plans, shared wall solids and relative map coordinates."""
from __future__ import annotations

import copy
import json
import math
import re
import time

from . import checks, game, mesh, physics
from .project import OpError

SIDES = ('-x', '+x', '-y', '+y')
PLAN_KEY = 'cs2map_layout'
LIFT_KEY = 'cs2map_lift'
EPS = 1e-5


def _num(v, field, low=-1e6, high=1e6):
    return physics.number(v, field, low, high)


def _vec(v, field, n=3):
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise OpError(f'{field} must contain {n} numbers')
    return [_num(x, field) for x in v]


def _id(v, field):
    if not isinstance(v, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', v):
        raise OpError(f'{field} must be 1..64 letters/digits/underscores, starting with a letter/_')
    return v


def _keys(obj, allowed, field):
    if not isinstance(obj, dict):
        raise OpError(f'{field} must be an object')
    extra = set(obj) - set(allowed)
    if extra:
        raise OpError(f'unknown {field} fields: {sorted(extra)}')


def _box(lo, hi):
    return {'mins': list(lo), 'maxs': list(hi)}


def _overlap(a, b, epsilon=EPS):
    return all(min(a['maxs'][i], b['maxs'][i]) - max(a['mins'][i], b['mins'][i]) > epsilon
               for i in range(3))


def _subtract(a, b):
    """Disjoint partition of A minus B, at most six axis-aligned solids."""
    if not _overlap(a, b):
        return [a]
    lo, hi = list(a['mins']), list(a['maxs'])
    out = []
    for i in range(3):
        cutlo, cuthi = max(lo[i], b['mins'][i]), min(hi[i], b['maxs'][i])
        if cutlo > lo[i] + EPS:
            end = hi.copy(); end[i] = cutlo
            out.append({**a, **_box(lo, end)})
        if cuthi < hi[i] - EPS:
            start = lo.copy(); start[i] = cuthi
            out.append({**a, **_box(start, hi)})
        lo[i], hi[i] = cutlo, cuthi
    return out


def _union(pieces):
    out = []
    for piece in pieces:
        todo = [piece]
        for used in out:
            todo = [p for q in todo for p in _subtract(q, used)]
            if not todo:
                break
        out.extend(todo)
        if len(out) > 4096:
            raise OpError('layout exceeds 4096 solid pieces; split it into smaller layouts')
    return out


def _wall(room, side, thickness):
    lo, hi = room['interior']['mins'], room['interior']['maxs']
    axis = 0 if side[1] == 'x' else 1
    a, b = lo.copy(), hi.copy()
    if side[0] == '+':
        a[axis], b[axis] = hi[axis], hi[axis] + thickness
    else:
        a[axis], b[axis] = lo[axis] - thickness, lo[axis]
    # X walls own the corner columns; Y walls end at the interior edges.
    if axis == 0:
        a[1] -= thickness; b[1] += thickness
    return _box(a, b)


def _shared(a, b, thickness):
    al, ah = a['interior']['mins'], a['interior']['maxs']
    bl, bh = b['interior']['mins'], b['interior']['maxs']
    for side in SIDES:
        axis = 0 if side[1] == 'x' else 1
        other = 1 - axis
        face = ah[axis] if side[0] == '+' else al[axis]
        remote = bl[axis] if side[0] == '+' else bh[axis]
        gap = remote - face if side[0] == '+' else face - remote
        if abs(gap - thickness) > EPS:
            continue
        low, high = max(al[other], bl[other]), min(ah[other], bh[other])
        bottom, top = max(al[2], bl[2]), min(ah[2], bh[2])
        if high > low and top > bottom:
            return side, axis, other, face, remote, low, high, bottom, top
    raise OpError(f'rooms {a["id"]} and {b["id"]} do not share a wall; use from/side or fix origins')


def plan(op):
    """Validate the whole plan before creating a VMAP node or calling a recipe."""
    _keys(op, {'op', 'name', 'origin', 'wall', 'materials', 'rooms', 'openings', 'levels', 'ref'}, 'layout')
    name = _id(op.get('name'), 'layout name')
    origin = _vec(op.get('origin', [0, 0, 0]), 'layout origin')
    thickness = _num(op.get('wall', 16), 'wall', 1, 128)
    materials = op.get('materials', {})
    _keys(materials, {'floor', 'walls', 'ceiling'}, 'materials')
    if any(not isinstance(v, str) or not v.endswith('.vmat') for v in materials.values()):
        raise OpError('materials must be VMAT paths')
    levels = op.get('levels', {'ground': 0})
    if not isinstance(levels, dict) or len(levels) > 64:
        raise OpError('levels must be an object of at most 64 name:floor_z entries')
    levels = {_id(k, 'level'): _num(v, 'level floor_z') for k, v in levels.items()}
    source = op.get('rooms')
    if not isinstance(source, list) or not 1 <= len(source) <= 128:
        raise OpError('rooms must contain 1..128 rooms in dependency order')
    rooms = {}
    for spec in source:
        _keys(spec, {'id', 'size', 'from', 'side', 'align', 'origin', 'floor_z', 'level',
                     'outdoor', 'in', 'at', 'ceiling'}, 'room')
        rid = _id(spec.get('id'), 'room id')
        if rid in rooms:
            raise OpError(f'duplicate room id {rid}')
        outdoor = physics.flag(spec, 'outdoor')
        size = spec.get('size')
        if outdoor and isinstance(size, (list, tuple)) and len(size) == 2:
            size = [*size, 512]
        size = _vec(size, f'room {rid} size')
        if any(x <= 0 or x > 32768 for x in size):
            raise OpError(f'room {rid} size must be positive, at most 32768')
        modes = sum(k in spec for k in ('from', 'in', 'origin'))
        if modes > 1:
            raise OpError(f'room {rid}: choose one of from/in/origin')
        if 'floor_z' in spec and 'level' in spec:
            raise OpError(f'room {rid}: choose floor_z or level')
        if 'level' in spec and spec['level'] not in levels:
            raise OpError(f'unknown level {spec["level"]}; available: {", ".join(levels)}')
        base = _vec(spec.get('origin', origin), f'room {rid} origin')
        anchor = None
        if 'from' in spec or 'in' in spec:
            anchor_id = spec.get('from', spec.get('in'))
            if anchor_id not in rooms:
                raise OpError(f'room {rid}: anchor {anchor_id!r} must appear earlier in rooms')
            anchor = rooms[anchor_id]
            al, ah = anchor['interior']['mins'], anchor['interior']['maxs']
            base = al.copy()
            if 'from' in spec:
                side = spec.get('side', '+x')
                if side not in SIDES:
                    raise OpError('side must be -x/+x/-y/+y')
                axis = 0 if side[1] == 'x' else 1; other = 1-axis
                align = spec.get('align', 'center')
                fraction = {'start': 0, 'center': .5, 'end': 1}.get(align) if isinstance(align, str) else align
                fraction = _num(fraction, 'align (start/center/end or fraction)', 0, 1)
                base[axis] = ah[axis]+thickness if side[0] == '+' else al[axis]-thickness-size[axis]
                base[other] = al[other]+fraction*(ah[other]-al[other]-size[other])
            else:
                if not outdoor:
                    raise OpError('room in another room must be outdoor:true (an open platform)')
                at = _vec(spec.get('at', [0, 0]), 'in-room at', 2)
                if any(not 0 <= f <= 1 for f in at):
                    raise OpError('in-room at must contain fractions 0..1')
                base[:2] = [al[i]+at[i]*(ah[i]-al[i]) for i in range(2)]
                if any(base[i]+size[i] > ah[i]+EPS for i in range(2)):
                    raise OpError(f'room {rid} floor exceeds parent {anchor_id}; reduce size or at')
        if 'level' in spec:
            base[2] = origin[2] + levels[spec['level']]
        elif 'floor_z' in spec:
            base[2] = origin[2] + _num(spec['floor_z'], 'floor_z')
        end = [base[i]+size[i] for i in range(3)]
        if 'in' in spec and (base[2] <= anchor['floor_z'] or end[2] > anchor['interior']['maxs'][2]+EPS):
            raise OpError(f'room {rid} platform must be above the parent floor and below its ceiling')
        room = {'id': rid, 'interior': _box(base, end), 'floor_z': base[2], 'outdoor': outdoor,
                'ceiling': physics.flag(spec, 'ceiling', not outdoor), 'level': spec.get('level'),
                'in': spec.get('in')}
        for previous in rooms.values():
            if _overlap(room['interior'], previous['interior']):
                # Only explicitly contained open platforms may overlap a room's empty volume.
                parent_id = room.get('in')
                if parent_id != previous['id']:
                    raise OpError(f'room interiors {rid} and {previous["id"]} overlap')
        rooms[rid] = room
    source_openings = op.get('openings', [])
    if not isinstance(source_openings, list) or len(source_openings) > 256:
        raise OpError('openings must be a list of at most 256 entries')
    openings = []
    for spec in source_openings:
        _keys(spec, {'between', 'room', 'wall', 'at', 'bottom', 'width', 'height', 'door'}, 'opening')
        between = spec.get('between')
        if between is not None:
            if 'room' in spec or 'wall' in spec or not isinstance(between, list) or len(between) != 2:
                raise OpError('opening: use between:[a,b] or room/wall')
            if between[0] == between[1] or any(r not in rooms for r in between):
                raise OpError(f'unknown/duplicate room in between {between}; available: {", ".join(rooms)}')
            room = rooms[between[0]]
            if room['outdoor'] and rooms[between[1]]['outdoor']:
                raise OpError('two outdoor rooms have no wall to cut')
            side, axis, other, face, remote, low, high, bottom, top = _shared(
                room, rooms[between[1]], thickness)
            across = sorted([face, remote])
        else:
            if spec.get('room') not in rooms:
                raise OpError(f'unknown opening room {spec.get("room")}; available: {", ".join(rooms)}')
            room = rooms[spec['room']]; side = spec.get('wall')
            if side not in SIDES or room['outdoor']:
                raise OpError('opening needs an indoor room and wall -x/+x/-y/+y')
            axis = 0 if side[1] == 'x' else 1; other = 1-axis
            wall = _wall(room, side, thickness)
            low, high = room['interior']['mins'][other], room['interior']['maxs'][other]
            bottom, top = room['floor_z'], room['interior']['maxs'][2]
            across = [wall['mins'][axis], wall['maxs'][axis]]
            # A single-sided opening still cuts the entire shared wall. Infer its graph neighbour.
            neighbours = []
            for candidate in rooms.values():
                if candidate['id'] == room['id']:
                    continue
                try:
                    shared = _shared(room, candidate, thickness)
                    if shared[0] == side:
                        neighbours.append((candidate, shared))
                except OpError:
                    pass
        width = _num(spec.get('width', 96), 'opening width', .1, 32768)
        height = _num(spec.get('height', 112), 'opening height', .1, 32768)
        center = low + _num(spec.get('at', .5), 'opening at', 0, 1)*(high-low)
        start = bottom + _num(spec.get('bottom', 0), 'opening bottom', 0, 32768)
        if center-width/2 < low-EPS or center+width/2 > high+EPS or start+height > top+EPS:
            span = high-low
            fit = (f'; with width {width:g} use at {width/2/span:.3f}..{1-width/2/span:.3f}'
                   if width < span and start+height <= top+EPS else '')
            raise OpError(f'opening on {room["id"]} {side} exceeds shared wall/ceiling '
                          f'(at is the centre as a fraction of the {span:g}-unit wall, height {top-bottom:g}){fit}; '
                          'reduce width/height or move at')
        lo, hi = [0., 0., start], [0., 0., start+height]
        lo[axis], hi[axis] = across
        lo[other], hi[other] = center-width/2, center+width/2
        if between is None:
            for candidate, shared in neighbours:
                if lo[other] < shared[6] and hi[other] > shared[5]:
                    if lo[other] < shared[5]-EPS or hi[other] > shared[6]+EPS or start < shared[7]-EPS or hi[2] > shared[8]+EPS:
                        raise OpError(f'opening exceeds neighbour {candidate["id"]}; use between and smaller dimensions')
                    between = [room['id'], candidate['id']]
                    break
        hole = {'index': len(openings), 'between': between, 'room': room['id'], 'wall': side,
                'bounds': _box(lo, hi), 'width': width, 'height': height, 'bottom': start,
                'door': copy.deepcopy(spec.get('door'))}
        if hole['door'] is not None:
            allowed = {'targetname', 'kind', 'direction', 'hinge', 'axis', 'rotation', 'speed', 'open_by',
                       'button', 'trigger', 'locked', 'stay_open', 'wait', 'lip', 'texture', 'sounds'}
            _keys(hole['door'], allowed, 'opening door')
        if any(_overlap(hole['bounds'], h['bounds']) for h in openings):
            raise OpError('openings overlap; combine them or move at/bottom')
        openings.append(hole)
    return {'version': 1, 'name': name, 'origin': origin, 'wall': thickness, 'materials': materials,
            'levels': levels, 'rooms': rooms, 'openings': openings}


def solids(data):
    """Rooms share wall volumes; carve openings once and remove all overlapping solids."""
    thickness = data['wall']; pieces = []
    for r in data['rooms'].values():
        lo, hi = r['interior']['mins'], r['interior']['maxs']
        # Contained open platforms stop exactly at their footprint. Full rooms include thresholds.
        pad = 0 if r['in'] or r['outdoor'] else thickness
        pieces.append({**_box([lo[0]-pad, lo[1]-pad, lo[2]-thickness],
                             [hi[0]+pad, hi[1]+pad, lo[2]]), 'part': 'floor', 'room': r['id']})
        if r['outdoor']:
            continue
        for side in SIDES:
            todo = [{**_wall(r, side, thickness), 'part': 'walls', 'room': r['id']}]
            for hole in data['openings']:
                todo = [p for q in todo for p in _subtract(q, hole['bounds'])]
            for empty in data['rooms'].values():
                todo = [p for q in todo for p in _subtract(q, empty['interior'])]
            pieces.extend(todo)
        if r['ceiling']:
            pieces.append({**_box([lo[0]-thickness, lo[1]-thickness, hi[2]],
                                 [hi[0]+thickness, hi[1]+thickness, hi[2]+thickness]),
                           'part': 'ceiling', 'room': r['id']})
    return _union(pieces)


def add_layout(p, m, op, refs):
    data = plan(op)
    if any(n.get(PLAN_KEY) and json.loads(n[PLAN_KEY]).get('name') == data['name'] for n in m.iter_nodes()):
        raise OpError(f'layout {data["name"]} already exists; delete its group or use a different name')
    pieces = solids(data)
    group = m.new_group('layout:'+data['name'], data['origin'])
    m.attach(group)
    mesh_ids = []
    for solid in pieces:
        material = data['materials'].get(solid['part'], data['materials'].get('walls', mesh.DEFAULT_MATERIAL))
        result = p._apply(m, {'op': 'add_mesh', 'shape': {'shape': 'box', **_box(solid['mins'], solid['maxs'])},
                              'material': material, 'parent': group['nodeID']}, refs)
        mesh_ids.extend(result['ids'])
    data['mesh_ids'] = mesh_ids
    group.set(PLAN_KEY, 'string', json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    for hole in data['openings']:
        if hole['door'] is not None:
            dop = preprocess(p, m, {**hole['door'], 'op': 'add_door', **hole['bounds']}, refs)
            result = physics.add_door(p, m, dop, refs)
            # Group all recipe entities, including a generated button or trigger.
            for key in ('id', 'button_id', 'trigger_id'):
                if result.get(key):
                    node = m.node(result[key]); m.remove(node); m.attach(node, group)
            hole['door_id'] = result['id']; hole['door_name'] = result['targetname']
    data['mesh_ids'] = mesh_ids
    group.set(PLAN_KEY, 'string', json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    if op.get('ref'):
        refs[op['ref']] = group['nodeID']
    return {'op': 'add_layout', 'id': group['nodeID'], 'name': data['name'], 'rooms': data['rooms'],
            'openings': [{k: v for k, v in h.items() if k != 'door'} for h in data['openings']], 'mesh_ids': mesh_ids}


def plans(m):
    result = []
    for node in m.iter_nodes():
        if not node.get(PLAN_KEY):
            continue
        try:
            data = json.loads(node[PLAN_KEY])
            if data.get('version') != 1:
                raise ValueError('unsupported version')
            if any(abs(x) > EPS for x in node.get('angles', (0, 0, 0))) or any(
                    abs(x-1) > EPS for x in node.get('scales', (1, 1, 1))):
                raise ValueError('rotated/scaled layout group; rebuild the plan')
            delta = [node['origin'][i]-data['origin'][i] for i in range(3)]
            data['origin'] = list(node['origin'])
            for r in data['rooms'].values():
                for k in ('mins', 'maxs'):
                    r['interior'][k] = [r['interior'][k][i]+delta[i] for i in range(3)]
                r['floor_z'] += delta[2]
            for h in data['openings']:
                for k in ('mins', 'maxs'):
                    h['bounds'][k] = [h['bounds'][k][i]+delta[i] for i in range(3)]
                h['bottom'] += delta[2]
            data['id'] = node['nodeID']; result.append(data)
        except (ValueError, KeyError, TypeError) as e:
            raise OpError(f'invalid layout metadata on group {node["nodeID"]}: {e}') from None
    return result


def _room_ref(m, spec):
    found = []; available = []
    for data in plans(m):
        if spec.get('layout') and spec['layout'] != data['name']:
            continue
        available.extend(data['rooms'])
        if spec.get('room') in data['rooms']:
            found.append(data['rooms'][spec['room']])
    if not found:
        raise OpError(f'no room {spec.get("room")!r}; available: {", ".join(sorted(set(available))) or "none (add_layout first)"}')
    if len(found) != 1:
        raise OpError(f'ambiguous room {spec["room"]!r}; add layout:<name> to the coordinate')
    return found[0]


def point(m, spec):
    _keys(spec, {'room', 'layout', 'at', 'z', 'wall', 'u', 'h', 'offset'}, 'relative point')
    r = _room_ref(m, spec); lo, hi = r['interior']['mins'], r['interior']['maxs']
    normal = None
    if 'wall' in spec:
        side = spec['wall']
        if side not in SIDES or 'at' in spec or 'z' in spec:
            raise OpError('wall coordinate needs wall -x/+x/-y/+y, u, h, offset; do not combine at/z')
        axis = 0 if side[1] == 'x' else 1; other = 1-axis
        sign = -1 if side[0] == '+' else 1
        pos = [0., 0., lo[2]+_num(spec.get('h', 0), 'wall h')]
        pos[axis] = (hi[axis] if sign == -1 else lo[axis])+sign*_num(spec.get('offset', 1), 'wall offset')
        pos[other] = lo[other]+_num(spec.get('u', .5), 'wall u', 0, 1)*(hi[other]-lo[other])
        normal = [0, 0, 0]; normal[axis] = sign
    else:
        if any(k in spec for k in ('u', 'h', 'offset')):
            raise OpError('u/h/offset require wall')
        at = _vec(spec.get('at', [.5, .5]), 'room at', 2)
        if any(not 0 <= x <= 1 for x in at):
            raise OpError('room at must contain fractions 0..1')
        z = spec.get('z', 'floor')
        height = 0 if z == 'floor' else hi[2]-lo[2] if z == 'ceiling' else _num(z, 'room z')
        pos = [lo[i]+at[i]*(hi[i]-lo[i]) for i in range(2)] + [lo[2]+height]
    return pos, normal


def relative_box(m, spec):
    r = _room_ref(m, spec); lo, hi = r['interior']['mins'], r['interior']['maxs']
    raw = spec['box']
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise OpError('room box must be [[u0,v0,z0],[u1,v1,z1]]')
    a, b = _vec(raw[0], 'room box mins'), _vec(raw[1], 'room box maxs')
    if any(not 0 <= x <= 1 for x in (*a[:2], *b[:2])) or any(a[i] >= b[i] for i in range(3)):
        raise OpError('room box XY must be fractions 0..1, mins strictly below maxs; Z is floor-relative units')
    return _box([lo[i]+a[i]*(hi[i]-lo[i]) if i < 2 else lo[2]+a[i] for i in range(3)],
                [lo[i]+b[i]*(hi[i]-lo[i]) if i < 2 else lo[2]+b[i] for i in range(3)])


def preprocess(p, m, op, refs):
    # add_layout declares new rooms; add_lift's room is a selector, not a coordinate.
    if op.get('op') == 'add_layout':
        resolved = copy.deepcopy(op)
        if isinstance(op.get('origin'), dict):
            resolved['origin'] = point(m, op['origin'])[0]
        for r in resolved.get('rooms', []):
            if isinstance(r, dict) and isinstance(r.get('origin'), dict):
                r['origin'] = point(m, r['origin'])[0]
        return resolved
    def visit(value, key=None):
        if isinstance(value, list):
            return [visit(v) for v in value]
        if not isinstance(value, dict):
            return value
        if 'room' in value and 'box' in value:
            box = relative_box(m, value)
            if key in ('mins', 'maxs'):
                return box[key]
            extra = {k: visit(v, k) for k, v in value.items() if k not in ('room', 'layout', 'box')}
            if 'mins' in extra or 'maxs' in extra:
                raise OpError('use relative box or mins/maxs, not both')
            return {**extra, **box}
        if 'room' in value and ('at' in value or 'wall' in value) and 'op' not in value:
            return point(m, value)[0]
        result = {k: visit(v, k) for k, v in value.items()}
        if value.get('op') == 'add_mover' and isinstance(value.get('to'), dict):
            raise OpError('add_mover to is a displacement, not a point; use numeric [dx,dy,dz] or add_lift')
        origin = value.get('origin')
        if value.get('op') in ('add_sign', 'add_decal') and isinstance(origin, dict) and 'wall' in origin:
            _, normal = point(m, origin)
            if 'normal' not in result and 'angles' not in result:
                result['normal'] = normal
        return result
    return visit(op)


def world_solids(m):
    """World meshes with world-space triangles: group transforms (house rows of add_facade,
    prefabs) are applied, so planners and checks see facades where the game does."""
    def visit(parent, entity=False, groups=()):
        for n in parent.get('children', []):
            is_entity = entity or n.type == 'CMapEntity'
            if n.type == 'CMapMesh' and not is_entity and n.get('physicsType') != 'none' and checks._is_solid_mesh(n):
                triangles = list(checks._triangles(n))
                if groups:
                    for g in reversed(groups):
                        triangles = [tuple(checks._transform(g, t)) for t in triangles]
                    points = [q for t in triangles for q in t]
                    bounds = (tuple(min(q[i] for q in points) for i in range(3)),
                              tuple(max(q[i] for q in points) for i in range(3))) if points else checks._mesh_bounds(n)
                else:
                    bounds = checks._mesh_bounds(n)
                yield n, bounds, triangles
            inner = groups + (n,) if n.type == 'CMapGroup' and _moved(n) else groups
            yield from visit(n, is_entity, inner)
    return list(visit(m.world))


def _moved(group):
    return (any(abs(float(v)) > .001 for v in group.get('origin', [0, 0, 0]))
            or any(abs(float(v)) > .001 for v in group.get('angles', [0, 0, 0]))
            or any(abs(float(v) - 1) > .001 for v in group.get('scales', [1, 1, 1])))


def swept_triangle(box, delta, triangle):
    """Continuous SAT: translating AABB versus triangle, including oblique world faces."""
    center = [(a+b)/2 for a, b in zip(box['mins'], box['maxs'])]
    radius = [(b-a)/2 for a, b in zip(box['mins'], box['maxs'])]
    edges = [checks._sub(triangle[(i+1)%3], triangle[i]) for i in range(3)]
    units = [(1,0,0), (0,1,0), (0,0,1)]
    axes = [*units, checks._cross(edges[0], edges[1]),
            *(checks._cross(e, u) for e in edges for u in units)]
    enter, leave = 0., 1.
    for axis in axes:
        length = math.sqrt(checks._dot(axis, axis))
        if length < EPS:
            continue
        axis = [a/length for a in axis]
        projection = [checks._dot(v, axis) for v in triangle]
        c = checks._dot(center, axis); speed = checks._dot(delta, axis)
        extent = sum(abs(axis[i])*radius[i] for i in range(3))
        # Permit contact with floors and docking edges, refuse penetration.
        low, high = min(projection)-extent+.02, max(projection)+extent-.02
        if abs(speed) < EPS:
            if c <= low or c >= high:
                return False
        else:
            a, b = sorted([(low-c)/speed, (high-c)/speed])
            enter, leave = max(enter, a), min(leave, b)
            if enter >= leave:
                return False
    return enter < leave


def collisions(m, box, delta, solids_list=None):
    swept = _box([min(box['mins'][i], box['mins'][i]+delta[i]) for i in range(3)],
                 [max(box['maxs'][i], box['maxs'][i]+delta[i]) for i in range(3)])
    out = []
    center = [(a+b)/2 for a, b in zip(box['mins'], box['maxs'])]
    for node, bounds, triangles in solids_list if solids_list is not None else world_solids(m):
        if bounds and _overlap(swept, _box(*bounds)) and (
                any(swept_triangle(box, delta, t) for t in triangles) or checks._inside_mesh(center, triangles)):
            out.append(node['nodeID'])
    return out


def add_lift(p, m, op, refs):
    _keys(op, {'op', 'room', 'upper', 'layout', 'side', 'at', 'size', 'gap', 'thickness', 'targetname',
               'speed', 'texture', 'ref', 'buttons', 'call_buttons'}, 'lift')
    lower = _room_ref(m, op)
    upper = _room_ref(m, {'room': op.get('upper'), 'layout': op.get('layout')})
    side = op.get('side', '-x')
    if side not in SIDES:
        raise OpError('lift side must be -x/+x/-y/+y (edge of the upper floor)')
    if upper.get('in') != lower['id'] or not upper['outdoor']:
        raise OpError('lift upper must be an open platform with in:<lower room>')
    size = _vec(op.get('size', [104, 96]), 'lift size', 2)
    if any(x < 32 or x > 1024 for x in size):
        raise OpError('lift size must be 32..1024 units on each axis')
    gap = _num(op.get('gap', 8), 'lift gap', 1, 18)
    thickness = _num(op.get('thickness', 8), 'lift thickness', 1, 32)
    if upper['floor_z']-lower['floor_z'] < 72:
        raise OpError('lift upper floor must be at least 72 units above lower floor')
    axis = 0 if side[1] == 'x' else 1; other = 1-axis
    ul, uh = upper['interior']['mins'], upper['interior']['maxs']
    center = ul[other]+_num(op.get('at', .5), 'lift at', 0, 1)*(uh[other]-ul[other])
    lo = [0., 0., lower['floor_z']]; hi = [0., 0., lower['floor_z']+thickness]
    if side[0] == '-':
        hi[axis] = ul[axis]-gap; lo[axis] = hi[axis]-size[axis]
    else:
        lo[axis] = uh[axis]+gap; hi[axis] = lo[axis]+size[axis]
    lo[other], hi[other] = center-size[other]/2, center+size[other]/2
    ll, lh = lower['interior']['mins'], lower['interior']['maxs']
    if any(lo[i] < ll[i] or hi[i] > lh[i] for i in range(2)) or lo[other] < ul[other] or hi[other] > uh[other]:
        raise OpError('lift does not fit next to upper edge inside the lower room; move at/side or enlarge room')
    delta = [0, 0, upper['floor_z']-hi[2]]
    box = _box(lo, hi)
    rider = _box([lo[0], lo[1], hi[2]], [hi[0], hi[1], hi[2]+72])
    solids_list = world_solids(m)
    hits = sorted(set(collisions(m, box, delta, solids_list)+collisions(m, rider, delta, solids_list)))
    if hits:
        raise OpError(f'lift platform/player sweep intersects world meshes {hits[:8]}; move the upper edge or lift')
    target = physics.target_name(m, op, 'lift')
    result = physics.add_mover(p, m, {'op': 'add_mover', **box, 'to': delta, 'call': 'script',
        'targetname': target, 'speed': op.get('speed', 80), 'texture': op.get('texture', mesh.DEFAULT_MATERIAL),
        **({'ref': op['ref']} if op.get('ref') else {})}, refs)
    node = m.node(result['id'])
    node.set(LIFT_KEY, 'string', json.dumps({'room': lower['id'], 'upper': upper['id'], 'layout': op.get('layout'),
                                           'gap': gap, 'height': 72}))
    buttons = []
    overrides = op.get('buttons', {})
    _keys(overrides, {'lower', 'upper', 'call_lower', 'call_upper'}, 'lift buttons')
    calls = op.get('call_buttons', True)
    if not isinstance(calls, bool):
        raise OpError('call_buttons must be true or false')
    # Ride buttons send the platform away from their floor; call buttons (on the other side of the
    # platform) bring it to their floor, so a player is never stuck with the platform elsewhere.
    plan = [('lower', lower['floor_z'], 'Open', 1), ('upper', upper['floor_z'], 'Close', 1)]
    if calls:
        plan += [('call_lower', lower['floor_z'], 'Close', -1), ('call_upper', upper['floor_z'], 'Open', -1)]
    ride_high = hi[other]+32 < lh[other]
    for where, floor, input_name, mirror in plan:
        if where in overrides:
            spec = preprocess(p, m, {'op': 'add_button', **overrides[where]}, refs)
        else:
            bcenter = [(lo[i]+hi[i])/2 for i in range(3)]
            if where.endswith('upper'):
                bcenter[axis] = ul[axis]+24 if side[0] == '-' else uh[axis]-24
            high = ride_high if mirror > 0 else not ride_high
            bcenter[other] = hi[other]+24 if high else lo[other]-24
            spec = _box([bcenter[0]-8, bcenter[1]-8, floor+44],
                        [bcenter[0]+8, bcenter[1]+8, floor+60])
        blo, bhi, _ = physics.bounds(spec)
        if collisions(m, _box(blo, bhi), [0, 0, 0], solids_list):
            raise OpError(f'lift {where} button intersects the world; supply buttons.{where} mins/maxs'
                          + (' or call_buttons:false' if where.startswith('call') else ''))
        buttons.append(physics.add_button(p, m, {**spec, 'op': 'add_button', 'targetname': target+'_'+where,
                          'targets': [{'name': target, 'input': input_name}]}, refs))
    return {**result, 'op': 'add_lift', 'bounds': box, 'to': delta, 'upper_floor_z': upper['floor_z'],
            'gap': gap, 'buttons': [{'id': b['id'], 'targetname': b['targetname']} for b in buttons]}


def _entity_bounds(node):
    bounds = [checks._mesh_bounds(n) for n in node.get('children', []) if n.type == 'CMapMesh']
    bounds = [b for b in bounds if b]
    if not bounds:
        return None
    return _box([min(b[0][i] for b in bounds) for i in range(3)],
                [max(b[1][i] for b in bounds) for i in range(3)])


def _light_carrier(m, node, box):
    """Existing start_off recipe: a tiny nodraw carrier intentionally traverses floors."""
    name = node['entity_properties'].get('targetname')
    return (name and all(box['maxs'][i]-box['mins'][i] <= 4.1 for i in range(3))
            and all(n['meshData']['materials'] == ['materials/tools/toolsnodraw.vmat']
                    for n in node.get('children', []) if n.type == 'CMapMesh')
            and any(e['entity_properties'].get('parentname') == name
                    for cls in ('light_omni2','light_barn','light_rect') for e in m.entities(classname=cls)))


def _stair_meshes(m):
    """Solid world meshes at least 36 tall whose upward faces rise from the bottom in steps of <= 18."""
    for node in m.iter_nodes():
        if node.type != 'CMapMesh' or node.get('physicsType') == 'none' or not checks._is_solid_mesh(node):
            continue
        bounds = checks._mesh_bounds(node)
        if not bounds or bounds[1][2] - bounds[0][2] < 36:
            continue
        lo, hi = bounds
        levels = {round(lo[2], 1)}
        for a, b, c in checks._triangles(node):
            n = checks._cross(checks._sub(b, a), checks._sub(c, a))
            length = math.sqrt(sum(x*x for x in n)) or 1
            if n[2]/length > .7:
                levels.add(round(max(a[2], b[2], c[2]), 1))
        levels = sorted(levels)
        if len(levels) >= 3 and levels[-1] >= hi[2] - 1 and all(b - a <= 18.01 for a, b in zip(levels, levels[1:])):
            yield node, bounds


def check_layout(p, m):
    found = plans(m)
    if not found:
        return []
    out = []; world = world_solids(m)
    def issue(code, nid, message, fix, severity='error'):
        out.append(checks.problem(severity, code, nid, message, fix))
    rooms = {(d['id'], rid): r for d in found for rid, r in d['rooms'].items()}
    graph = {k: set() for k in rooms}
    for data in found:
        for h in data['openings']:
            if h['width'] < 32 or h['height'] < 72:
                issue('layout_opening_small', data['id'], f'opening {h["index"]} is smaller than 32x72', 'Enlarge width/height.')
                continue
            if collisions(m, h['bounds'], [0,0,0], world):
                issue('layout_opening_blocked', data['id'], f'opening {h["index"]} intersects world geometry', 'Remove the obstruction.')
                continue
            if h.get('door_id'):
                try:
                    door = m.node(h['door_id']); actual = _entity_bounds(door)
                    if m.classname(door) not in ('func_door', 'func_door_rotating') or not actual or any(
                            abs(actual[k][i]-h['bounds'][k][i]) > .1 for k in ('mins','maxs') for i in range(3)):
                        raise ValueError('bounds mismatch')
                except (ValueError, RuntimeError):
                    issue('layout_door_outside_opening', h['door_id'], 'door is missing or no longer matches its opening', 'Recreate the door from the layout.')
            between = h.get('between')
            if between:
                a, b = (rooms[(data['id'], r)] for r in between)
                if all(h['bottom']-r['floor_z'] <= 18 for r in (a, b)):
                    ka, kb = (data['id'], between[0]), (data['id'], between[1])
                    graph[ka].add(kb); graph[kb].add(ka)
    # A ladder surface (add_ladder) joins the room at its foot with a room whose floor it reaches.
    for node in m.iter_nodes():
        if node.type != 'CMapMesh' or not any('toolsinvisibleladder' in str(x)
                                               for x in node.get('meshData', {}).get('materials', [])):
            continue
        bounds = checks._mesh_bounds(node)
        if not bounds:
            continue
        lo, hi = bounds
        near = [k for k, r in rooms.items() if all(lo[i] - 32 <= r['interior']['maxs'][i]
                and hi[i] + 32 >= r['interior']['mins'][i] for i in (0, 1))]
        feet = [k for k in near if abs(lo[2] - rooms[k]['floor_z']) <= 18]
        tops = [k for k in near if rooms[k]['floor_z'] - 18 > lo[2] and hi[2] >= rooms[k]['floor_z'] - 24]
        for ka in feet:
            for kb in tops:
                graph[ka].add(kb); graph[kb].add(ka)
    # Stairs (shape "stairs" or any mesh whose top faces climb in steps of <= 18) join the room at
    # their foot with the room whose floor their top reaches.
    for node, (lo, hi) in _stair_meshes(m):
        near = [k for k, r in rooms.items() if all(lo[i] - 32 <= r['interior']['maxs'][i]
                and hi[i] + 32 >= r['interior']['mins'][i] for i in (0, 1))]
        feet = [k for k in near if abs(lo[2] - rooms[k]['floor_z']) <= 18]
        tops = [k for k in near if rooms[k]['floor_z'] - 18 > lo[2] and abs(hi[2] - rooms[k]['floor_z']) <= 18]
        for ka in feet:
            for kb in tops:
                graph[ka].add(kb); graph[kb].add(ka)
    for n in m.entities():
        cls = m.classname(n); box = _entity_bounds(n)
        if cls in ('func_door', 'func_door_rotating') and box:
            holes = [h for d in found for h in d['openings']]
            if not any(all(abs(box[k][i]-h['bounds'][k][i]) < .1
                           for k in ('mins','maxs') for i in range(3)) for h in holes):
                if not any(x['code'] == 'layout_door_outside_opening' and x['id'] == n['nodeID'] for x in out):
                    issue('layout_door_outside_opening', n['nodeID'], 'door does not match any saved opening',
                          'Create doors through opening.door or match an opening exactly.')
        if cls == 'func_movelinear' and box:
            if _light_carrier(m, n, box) or int(n['entity_properties'].get('spawnflags') or 0) & 8:
                continue
            kv = n['entity_properties']
            try:
                _, direction = physics.direction(checks._vec(kv.get('movedir')))
                delta = [v*float(kv.get('movedistance', 0)) for v in direction]
                hits = collisions(m, box, delta, world)
                if n.get(LIFT_KEY):
                    rider = _box([box['mins'][0],box['mins'][1],box['maxs'][2]],
                                 [box['maxs'][0],box['maxs'][1],box['maxs'][2]+72])
                    hits += collisions(m, rider, delta, world)
                if hits:
                    issue('layout_mover_world', n['nodeID'], f'mover/player path intersects world meshes {sorted(set(hits))[:8]}', 'Place platform beside the upper floor; provide headroom.')
                elif n.get(LIFT_KEY):
                    meta = json.loads(n[LIFT_KEY])
                    for data in found:
                        if meta.get('layout') and meta['layout'] != data['name']:
                            continue
                        ka, kb = (data['id'], meta['room']), (data['id'], meta['upper'])
                        if ka in graph and kb in graph:
                            lower, upper = rooms[ka], rooms[kb]
                            end = {k:[box[k][i]+delta[i] for i in range(3)] for k in ('mins','maxs')}
                            ul, uh = upper['interior']['mins'], upper['interior']['maxs']
                            adjacent = any(abs(end['maxs'][a]+meta['gap']-ul[a]) < .1
                                or abs(end['mins'][a]-meta['gap']-uh[a]) < .1 for a in (0,1))
                            if not adjacent or abs(end['maxs'][2]-upper['floor_z']) > .1 or abs(
                                    box['mins'][2]-lower['floor_z']) > .1:
                                issue('layout_lift_docking', n['nodeID'], 'lift no longer docks at the saved upper floor',
                                      'Recreate add_lift after changing its bounds/travel.')
                            else:
                                graph[ka].add(kb); graph[kb].add(ka)
            except (ValueError, TypeError):
                issue('layout_mover_invalid', n['nodeID'], 'invalid mover direction/distance', 'Recreate the mover.')
        if cls in ('func_button', 'func_physical_button') and box and collisions(m, box, [0,0,0], world):
            issue('layout_button_in_wall', n['nodeID'], 'button intersects world geometry', 'Move its bounds inward from the wall.')
    for n in m.iter_nodes():
        if n.type == 'CMapGroup' and n.name.startswith('sign_'):
            for child in n.get('children', []):
                bounds = checks._mesh_bounds(child)
                if bounds and any(all(wb[0][i]+.02 < v[i] < wb[1][i]-.02 for i in range(3))
                        and checks._inside_mesh(v, tri) for v in checks._vertices(child) for _, wb, tri in world if wb):
                    issue('layout_sign_in_wall', n['nodeID'], 'sign is embedded in world geometry', 'Use a wall point with offset >= 1.'); break
    seeds = set()
    for spawn in [*m.entities(classname='info_player_terrorist'), *m.entities(classname='info_player_counterterrorist')]:
        options = [(k,r) for k,r in rooms.items() if checks._inside(spawn['origin'],
                   (r['interior']['mins'],r['interior']['maxs']))]
        if options:
            seeds.add(min(options, key=lambda item: math.prod(b-a for a,b in zip(
                item[1]['interior']['mins'], item[1]['interior']['maxs'])))[0])
    if seeds:
        visited = set(seeds); pending = list(seeds)
        while pending:
            for neighbour in graph[pending.pop()] - visited:
                visited.add(neighbour); pending.append(neighbour)
        for key in rooms.keys()-visited:
            issue('layout_room_unreachable', key[0], f'room {rooms[key]["id"]} has no opening/lift path from a spawn',
                  'Add a floor-level opening or add_lift; script teleportation is not a walking connection.', 'warning')
    else:
        issue('layout_spawn_missing', None, 'no spawn lies inside a layout room', 'Add a relative spawn inside a room.', 'warning')
    return out


def layout_info(p, name, layout=None):
    found = plans(p.load(name))
    if layout is not None:
        found = [d for d in found if d['name'] == layout]
        if not found:
            raise OpError(f'no layout {layout!r} in map {name}')
    return {'ok': True, 'map': name, 'layouts': [{k: v for k, v in d.items()
            if k in ('name','id','origin','wall','levels','rooms','mesh_ids')} | {
                'openings': [{k:v for k,v in h.items() if k != 'door'} for h in d['openings']]} for d in found]}


def layout_views(p, name, layout=None, offset=0, limit=6):
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 6:
        raise OpError('offset must be a nonnegative integer; limit must be 1..6')
    info = layout_info(p, name, layout); views = []
    for data in info['layouts']:
        for r in data['rooms'].values():
            lo, hi = r['interior']['mins'], r['interior']['maxs']
            pos = [lo[0]+.2*(hi[0]-lo[0]), lo[1]+.3*(hi[1]-lo[1]), r['floor_z']+8]
            views.append({'name': f'k_{data["name"]}_{r["id"]}', 'pos': pos, 'ang': [0, 15, 0]})
    selected = views[offset:offset+limit]
    if not selected:
        return {'ok': True, 'map': name, 'total_views':len(views), 'offset':offset, 'shots':[]}
    with game.exclusive(wait=110):
        shots = p.game_screenshot(map=name, views=selected, deadline=time.monotonic()+110)
    return {**shots, 'total_views':len(views), 'offset':offset}
