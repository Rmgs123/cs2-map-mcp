"""FGD-backed interactive recipes and runtime physics observations for CS2."""
from __future__ import annotations

import json
import math
import re
import time
import uuid
import hashlib
import subprocess
from pathlib import Path

from . import checks, compiler, game, kv3, mesh, resource
from .config import ensure_inside, PROJECT_ROOT
from .project import OpError
from .ext.models import _model_path


def number(value, label, minimum=0, maximum=100000):
    if isinstance(value, bool):
        raise OpError(f'{label} must be a number')
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise OpError(f'{label} must be a finite number') from None
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise OpError(f'{label} must be between {minimum} and {maximum}')
    return value


def vector(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise OpError(f'{label} must be [x,y,z]')
    return [number(x, label, -1e7, 1e7) for x in value]


def flag(op, key, default=False):
    value = op.get(key, default)
    if not isinstance(value, bool):
        raise OpError(f'{key} must be true or false')
    return value


def bounds(op):
    lo, hi = vector(op.get('mins'), 'mins'), vector(op.get('maxs'), 'maxs')
    if any(a >= b for a, b in zip(lo, hi)):
        raise OpError('mins must be strictly below maxs on each axis')
    return lo, hi, [(a+b)/2 for a, b in zip(lo, hi)]


def name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}', value):
        raise OpError('targetname must contain letters, digits, _, . or - (1..64 characters)')
    return value


def target_name(m, op, prefix):
    value = name(op.get('targetname') or f'{prefix}_{m.next_node_id()}')
    if m.entities(targetname=value):
        raise OpError(f'duplicate targetname: {value}')
    return value


def entity(p, m, cls, kv, origin, angles=None):
    p.catalog.get(cls)
    e = m.new_entity(cls, kv, origin, angles, defaults=p.catalog.defaults(cls))
    m.attach(e)
    return e


def brush(p, m, cls, op, kv, origin=None, material=None):
    lo, hi, centre = bounds(op)
    e = entity(p, m, cls, kv, origin if origin is not None else centre)
    material = material or op.get('texture') or mesh.DEFAULT_MATERIAL
    for piece in p._new_meshes(m, {'shape': 'box', 'mins': lo, 'maxs': hi}, material,
                               {'renderwithdynamic': True, 'bakelighting': False}):
        m.attach(piece, e)
    return e


def model_brush(p, m, cls, op, kv):
    """An invisible brush supplies I/O/collision; a parented prop draws a model."""
    rel, _ = _model_path(p, op['model'])
    origin = vector(op.get('origin', [0, 0, 0]), 'origin')
    metadata = p.s.content_addon / Path(rel).with_name('model.json')
    if 'mins' in op or 'maxs' in op:
        lo, hi, _ = bounds(op)
    else:
        box = json.loads(metadata.read_text(encoding='utf-8')).get('bounds') if metadata.is_file() else None
        box = box or model_physics(p, rel).get('bounds')
        if not box:
            raise OpError('model bounds unavailable; supply world mins/maxs or use an own model')
        low, high = vector(box[0], 'model bounds'), vector(box[1], 'model bounds')
        lo = [origin[i]+low[i] for i in range(3)]
        hi = [origin[i]+high[i] for i in range(3)]
    e = brush(p, m, cls, {**op, 'mins':lo, 'maxs':hi}, kv, origin,
              material=mesh.TOOL_MATERIALS['invisible'])
    prop = entity(p, m, 'prop_dynamic', {'targetname': kv['targetname']+'_model',
        'model':rel, 'solid':0, 'parentname':kv['targetname']}, origin)
    return e, prop


def result(m, e, op, refs, **extra):
    if op.get('ref'):
        refs[op['ref']] = e['nodeID']
    return {'op': op['op'], 'id': e['nodeID'],
            'targetname': e['entity_properties'].get('targetname'),
            'classname': m.classname(e), **extra}


def direction(value):
    """Return Source pitch/yaw/roll and a normalized world direction."""
    aliases = {'up': [-90, 0, 0], 'down': [90, 0, 0], '+z': [-90, 0, 0],
               '-z': [90, 0, 0], '+x': [0, 0, 0], '-x': [0, 180, 0],
               '+y': [0, 90, 0], '-y': [0, 270, 0]}
    if isinstance(value, str):
        if value not in aliases:
            raise OpError('direction must be up/down/+x/-x/+y/-y/+z/-z or [pitch,yaw,roll]')
        ang = aliases[value]
    elif isinstance(value, (int, float)):
        ang = [0, number(value, 'direction', -36000, 36000), 0]
    else:
        ang = vector(value, 'direction')
    pitch, yaw = map(math.radians, ang[:2])
    return ang, [math.cos(pitch)*math.cos(yaw), math.cos(pitch)*math.sin(yaw), -math.sin(pitch)]


def connection(m, e, output, target, input, param='', delay=0, once=False):
    m.add_connection(e, output, name(target), input, str(param), number(delay, 'delay', 0, 3600),
                     1 if once else -1)


def add_button(p, m, op, refs):
    targets = op.get('targets')
    if not isinstance(targets, list) or not targets or len(targets) > 128:
        raise OpError('button targets must be a nonempty list of {name,input,param?,delay?}')
    for t in targets:
        if not isinstance(t, dict):
            raise OpError('each target must be an object')
        name(t.get('name'))
        if not isinstance(t.get('input'), str) or not re.fullmatch(r'\w{1,64}', t['input']):
            raise OpError('target input must be an entity input name')
        number(t.get('delay', 0), 'delay', 0, 3600)
    once = flag(op, 'once')
    kv = {'targetname': target_name(m, op, 'button'), 'spawnflags': 1025,
          'wait': -1 if once else number(op.get('wait', 1), 'wait', .01, 3600)}
    if op.get('model'):
        e, prop = model_brush(p, m, 'func_button', op, kv)
    else:
        e = brush(p, m, 'func_button', op, kv, material=op.get('texture') or 'materials/dev/primary_red.vmat')
    for t in targets:
        connection(m, e, 'OnPressed', t['name'], t['input'], t.get('param', ''), t.get('delay', 0), once)
    return result(m, e, op, refs)


def _activation(p, m, op, refs, e, mode):
    target = e['entity_properties']['targetname']
    if mode == 'button':
        button = op.get('button')
        if isinstance(button, str):
            buttons = m.entities(targetname=name(button))
            if len(buttons) != 1 or m.classname(buttons[0]) not in ('func_button', 'func_physical_button'):
                raise OpError('button name must identify one existing button')
            connection(m, buttons[0], 'OnPressed', target, 'Open')
            return {'button': button, 'button_id': buttons[0]['nodeID']}
        if button is None:
            lo, hi, _ = bounds(op)
            button = {'mins': [lo[0]-24, lo[1]-8, lo[2]+40],
                      'maxs': [lo[0]-12, lo[1]+4, lo[2]+56]}
        if not isinstance(button, dict):
            raise OpError('button must be a name or {mins,maxs}')
        b = add_button(p, m, {**button, 'op': 'add_button', 'targetname': target+'_button',
                             'targets': [{'name': target, 'input': 'Open'}]}, refs)
        return {'button': b['targetname'], 'button_id': b['id']}
    if mode == 'trigger':
        lo, hi, _ = bounds(op)
        spec = op.get('trigger') or {'mins': [lo[0]-48, lo[1]-48, lo[2]],
                                   'maxs': [hi[0]+48, hi[1]+48, max(hi[2], lo[2]+80)]}
        tr = brush(p, m, 'trigger_multiple', spec,
                   {'targetname': target+'_trigger', 'spawnflags': 1, 'wait': 1},
                   material=mesh.TOOL_MATERIALS['trigger'])
        connection(m, tr, 'OnStartTouch', target, 'Open')
        return {'trigger': target+'_trigger', 'trigger_id': tr['nodeID']}
    return {}


def add_door(p, m, op, refs):
    kind, mode = op.get('kind', 'slide'), op.get('open_by', 'use')
    if kind not in ('slide', 'rotate') or mode not in ('use', 'touch', 'button', 'trigger', 'script'):
        raise OpError('kind: slide/rotate; open_by: use/touch/button/trigger/script')
    lo, hi, centre = bounds(op)
    flags = (256 if mode == 'use' else 1024 if mode == 'touch' else 0)
    flags |= 2048 if flag(op, 'locked') else 0
    kv = {'targetname': target_name(m, op, 'door'), 'speed': number(op.get('speed', 100), 'speed', .1),
          'spawnflags': flags, 'wait': -1 if flag(op, 'stay_open') else number(op.get('wait', 4), 'wait', .01, 3600),
          'lip': number(op.get('lip', 0), 'lip', -10000, 10000)}
    origin = centre
    if kind == 'slide':
        kv['movedir'], _ = direction(op.get('direction', 'up'))
    else:
        axis = op.get('axis', 'z')
        if axis not in ('x', 'y', 'z'):
            raise OpError('axis must be x/y/z')
        kv['spawnflags'] |= {'x': 64, 'y': 128, 'z': 0}[axis]
        rotation = number(op.get('rotation', 90), 'rotation', -360, 360)
        if rotation == 0:
            raise OpError('rotation must be nonzero')
        kv['distance'] = abs(rotation)
        kv['spawnflags'] |= 2 if rotation < 0 else 0
        hinge = op.get('hinge', '-y' if hi[1]-lo[1] > hi[0]-lo[0] else '-x')
        if isinstance(hinge, str):
            if hinge not in ('-x', '+x', '-y', '+y'):
                raise OpError('hinge must be -x/+x/-y/+y or [x,y,z]')
            idx = 'xy'.index(hinge[1]); origin = centre.copy()
            origin[idx] = lo[idx] if hinge[0] == '-' else hi[idx]
        else:
            origin = vector(hinge, 'hinge')
    sounds = op.get('sounds', {})
    if not isinstance(sounds, dict) or set(sounds)-{'move', 'stop', 'locked', 'unlocked'}:
        raise OpError('sounds must be {move?,stop?,locked?,unlocked?} with sound event names')
    kv.update({{'move': 'noise1', 'stop': 'noise2', 'locked': 'locked_sound', 'unlocked': 'unlocked_sound'}[k]: v
               for k, v in sounds.items()})
    e = brush(p, m, 'func_door' if kind == 'slide' else 'func_door_rotating', op, kv, origin)
    return result(m, e, op, refs, **_activation(p, m, op, refs, e, mode))


def add_mover(p, m, op, refs):
    delta = vector(op.get('to'), 'to')
    distance = math.sqrt(sum(x*x for x in delta))
    if not 0 < distance <= 100000:
        raise OpError('to must be a nonzero displacement of at most 100000 units')
    ang = [math.degrees(math.atan2(-delta[2], math.hypot(*delta[:2]))),
           math.degrees(math.atan2(delta[1], delta[0])), 0]
    mode = op.get('call', 'button')
    if mode not in ('button', 'trigger', 'loop', 'script'):
        raise OpError('call must be button/trigger/loop/script')
    e = brush(p, m, 'func_movelinear', op,
              {'targetname': target_name(m, op, 'mover'), 'movedir': ang, 'movedistance': distance,
               'speed': number(op.get('speed', 100), 'speed', .1), 'spawnflags': 0,
               'startposition': 0, 'authoredposition': 0})
    extra = _activation(p, m, op, refs, e, mode)
    if mode == 'loop':
        target = e['entity_properties']['targetname']
        delay = number(op.get('wait', 2), 'wait', .1, 3600)
        connection(m, e, 'OnFullyOpen', target, 'Close', delay=delay)
        connection(m, e, 'OnFullyClosed', target, 'Open', delay=delay)
        auto = entity(p, m, 'logic_auto', {'targetname': target+'_start'}, [0, 0, 0])
        connection(m, auto, 'OnMapSpawn', target, 'Open', delay=1, once=True)
    return result(m, e, op, refs, to=delta, **extra)


def add_rotator(p, m, op, refs):
    axis = op.get('axis', 'z')
    if axis not in ('x', 'y', 'z'):
        raise OpError('axis must be x/y/z')
    speed = number(op.get('speed', 90), 'speed', -36000, 36000)
    if speed == 0:
        raise OpError('speed must be nonzero')
    flags = {'x': 4, 'y': 8, 'z': 0}[axis] | 1 | (2 if speed < 0 else 0)
    kv = {'targetname': target_name(m, op, 'rotator'), 'spawnflags': flags, 'maxspeed': abs(speed),
          'fanfriction': 0, 'dmg': 0}
    if op.get('model'):
        e, prop = model_brush(p, m, 'func_rotating', op, kv)
    else:
        e = brush(p, m, 'func_rotating', op, kv,
                  vector(op['origin'], 'origin') if 'origin' in op else None)
    return result(m, e, op, refs)


def model_physics(p, model):
    """Inspect compiled PHYS (or referenced vphys); source only is insufficient."""
    rel, source = _model_path(p, model)
    compiled = ensure_inside(p.s.game_addon / (rel+'_c'), p.s.game_addon)
    try:
        data = compiled.read_bytes() if compiled.exists() else p.game.read(rel+'_c')
        block = resource.blocks(data).get('PHYS')
        refs = resource.external_references(data)
        external = [x for x in refs if x.endswith('.vphys')]
        found = bool(block and block[1] > 32) or any(
            (p.s.game_addon/(x+'_c')).is_file() or p.game.exists(x+'_c') for x in external)
        info = {'model': rel, 'has_physics': found, 'source': 'compiled PHYS' if block else 'external vphys',
                'physics_bytes': block[1] if block else 0}
        native = p.s.game_bin / 'resourceinfo.exe'
        if block and native.is_file():
            digest = hashlib.sha256(data).hexdigest()
            cache = getattr(p, '_physics_cache', {})
            if digest not in cache:
                path = compiled
                if not path.is_file():
                    path = PROJECT_ROOT/'.local/scratch/P/physics_inspect'/f'{digest}.vmdl_c'
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if not path.is_file():
                        path.write_bytes(data)
                process = subprocess.run([str(native), '-game', str(p.s.cs2_path/'game/csgo'),
                    '-i', str(path), '-b', 'PHYS', '-baremode'],
                    cwd=PROJECT_ROOT, capture_output=True, timeout=15)
                if process.returncode:
                    raise OpError('resourceinfo could not inspect the compiled physics body')
                text = process.stdout.decode('utf-8-sig', errors='replace')
                # Native KV3 dump's explicitly delimited byte arrays contain
                # geometry buffers; omit only those blobs before structural parsing.
                text = re.sub(r'#\[\s*(?:[0-9A-Fa-f]{2}\s*)*\]', '[]', text)
                _, document = kv3.loads(text)
                parts = document['m_parts']; points = []; shapes = 0
                for part in parts:
                    shape = part['m_rnShape']
                    shapes += sum(len(shape.get(k, [])) for k in ('m_spheres','m_capsules','m_hulls','m_meshes'))
                    for hull in shape.get('m_hulls', []):
                        box = hull['m_Hull']['m_Bounds']
                        points.extend([box['m_vMinBounds'],box['m_vMaxBounds']])
                detail = {'has_physics': bool(parts and shapes), 'parts':len(parts), 'shapes':shapes,
                          'mass':sum(part['m_flMass'] for part in parts)}
                if points:
                    detail['bounds'] = [[min(pt[i] for pt in points) for i in range(3)],
                                        [max(pt[i] for pt in points) for i in range(3)]]
                cache[digest] = detail; p._physics_cache = cache
            info.update(cache[digest])
        return info
    except (FileNotFoundError, ValueError, IndexError, KeyError, subprocess.SubprocessError) as exc:
        return {'model': rel, 'has_physics': False, 'source': 'unavailable', 'reason': str(exc)}


def add_physics_prop(p, m, op, refs):
    info = model_physics(p, op.get('model'))
    if not info['has_physics']:
        raise OpError(f"model has no compiled physics body: {info['model']}; build with collision='hull'")
    health = number(op['health'], 'health', 1, 100000) if 'health' in op else None
    if health is None and info['model'].startswith('models/mcp/'):
        metadata = p.s.content_addon / Path(info['model']).with_name('model.json')
        if metadata.is_file():
            kind = (json.loads(metadata.read_text(encoding='utf-8')).get('physics') or {}).get('breakable')
            health = {'wood': 40, 'glass': 10, 'metal': 80}.get(kind)
    mass = number(op['mass'], 'mass', .01, 100000) if 'mass' in op else None
    kv = {'targetname': target_name(m, op, 'physics_prop'), 'model': info['model'],
          'spawnflags': 0, 'skipPreSettle': 1}
    if health is not None:
        kv['health'] = health
    # CS2 may discard a custom prop_physics despite valid PHYS and prop_data.
    # The override class reliably accepts the same compiled body.
    e = entity(p, m, 'prop_physics_override', kv,
               vector(op.get('origin', [0, 0, 0]), 'origin'), vector(op.get('angles', [0, 0, 0]), 'angles'))
    if mass is not None:
        auto = entity(p, m, 'logic_auto', {'targetname': kv['targetname']+'_mass'}, [0, 0, 0])
        connection(m, auto, 'OnMapSpawn', kv['targetname'], 'SetMass', mass, once=True)
    return result(m, e, op, refs, model=info['model'], **({'mass': mass} if mass else {}))


def add_breakable(p, m, op, refs):
    material = op.get('material', 'glass')
    if material not in ('glass', 'wood', 'metal'):
        raise OpError('material must be glass/wood/metal')
    texture = op.get('texture') or {'glass': 'materials/glass/glass01.vmat',
                                  'wood': 'materials/wood/milbeams003.vmat',
                                  'metal': 'materials/metal/metalwall031a.vmat'}[material]
    target = target_name(m, op, 'breakable')
    e = brush(p, m, 'func_breakable', op,
              {'targetname': target, 'material': {'glass': 0, 'wood': 1, 'metal': 2}[material],
               'health': number(op.get('health', 20), 'health', 1), 'spawnflags': 0}, material=texture)
    out = result(m, e, op, refs)
    if flag(op, 'bulletproof'):
        # Spawnflag 2048 ("Don't allow bullet penetration") lets every bullet through in CS2 (measured 11.10 and
        # 25.10 with AK-47). A toolsblockbullets layer inside the pane stops them; it goes away when the pane
        # breaks, so shots then fly through the hole. The layer is a func_breakable that only breaks on input:
        # a killed func_brush is not re-created by a round restart (the pane is, so it came back without it).
        lo, hi, _ = bounds(op)
        axis = min(range(3), key=lambda k: hi[k] - lo[k])
        mid, half = (lo[axis] + hi[axis]) / 2, min(.5, (hi[axis] - lo[axis]) / 4)
        llo, lhi = list(lo), list(hi)
        llo[axis], lhi[axis] = mid - half, mid + half
        brush(p, m, 'func_breakable', {'mins': llo, 'maxs': lhi},
              {'targetname': f'{target}_bulletproof', 'spawnflags': 1, 'health': 1, 'material': 0},
              material='materials/tools/toolsblockbullets_cs.vmat')
        connection(m, e, 'OnBreak', f'{target}_bulletproof', 'Kill')
        out['bulletproof'] = f'{target}_bulletproof'
    return out


def add_zone(p, m, op, refs):
    kind = op.get('kind')
    if kind not in ('push', 'teleport', 'hurt', 'gravity'):
        raise OpError('zone kind must be push/teleport/hurt/gravity')
    params = op.get('params') or {}
    if not isinstance(params, dict):
        raise OpError('params must be an object')
    if kind == 'gravity':
        # trigger_gravity has no effect in CS2 (measured 08.10): the zone is a scripted ability instead.
        from . import abilities
        lo, hi = vector(op.get('mins'), 'mins'), vector(op.get('maxs'), 'maxs')
        name = re.sub(r'[^a-z0-9_]', '_', (op.get('targetname') or f'gravity_{len(m.entities(classname="point_script"))}').lower())[:32]
        if not name[0].isalpha():
            name = 'g' + name[:31]
        out = abilities.add_ability(p, m, {'op': 'add_ability', 'name': name, 'hud': False, 'abilities': {
            'gravity': {'scale': number(params.get('gravity', .3), 'gravity', .05, 4)}},
            'source': {'zone': {'mins': list(lo), 'maxs': list(hi)}}, **({'ref': op['ref']} if op.get('ref') else {})}, refs)
        return {**out, 'op': 'add_zone', 'kind': 'gravity', 'note': 'a scripted ability zone (trigger_gravity does nothing in CS2)'}
    target = target_name(m, op, kind)
    kv = {'targetname': target, 'spawnflags': 1, 'StartDisabled': 0}
    extra = {}
    if kind == 'push':
        kv['pushdir'], _ = direction(params.get('direction', 'up'))
        kv['speed'] = number(params.get('speed', 600), 'speed', .1)
    elif kind == 'teleport':
        origin = vector(params.get('destination'), 'params.destination')
        dest = entity(p, m, 'info_teleport_destination', {'targetname': target+'_destination'},
                      origin, vector(params.get('angles', [0, 0, 0]), 'angles'))
        kv['target'] = dest['entity_properties']['targetname']
        extra = {'destination': kv['target'], 'destination_id': dest['nodeID']}
    elif kind == 'hurt':
        kv['damage'] = number(params.get('damage', 10), 'damage', .01)
        kv['damagetype'] = int(number(params.get('damage_type', 0), 'damage_type', 0, 2147483647))
    else:
        kv['gravity'] = number(params.get('gravity', .3), 'gravity', .01, 10)
    e = brush(p, m, 'trigger_'+kind, op, kv, material=mesh.TOOL_MATERIALS['trigger'])
    return result(m, e, op, refs, **extra)


LADDER_MODEL = 'models/props/de_nuke/hr_nuke/metal_ladder_001/metal_ladder_001_{}.vmdl'
LADDER_SEGMENTS = (256, 128, 64, 32, 16)       # stock Nuke modules: 26 wide, 3 thick, origin at the foot
SIDES = {'+x': (0, 1), '-x': (0, -1), '+y': (1, 1), '-y': (1, -1)}


def _ladder_wall(m, lo, hi):
    """Side of the ladder volume that touches a wall: (side, distance from the volume face) or None."""
    centre = [(a+b)/2 for a, b in zip(lo, hi)]
    near = ([a-48 for a in lo], [b+48 for b in hi])
    triangles = []
    for node in m.iter_nodes():
        if node.type != 'CMapMesh' or not checks._is_solid_mesh(node):
            continue
        mats = [str(x) for x in node.get('meshData', {}).get('materials', [])]
        if mats and all('materials/tools/' in x for x in mats):
            continue                     # clips, triggers, the ladder surface itself
        b = checks._mesh_bounds(node)
        if b and all(b[0][i] <= near[1][i] and b[1][i] >= near[0][i] for i in range(3)):
            triangles.extend(checks._triangles(node))
    best = None
    for side, (axis, sign) in SIDES.items():
        half = (hi[axis]-lo[axis])/2
        hits = []
        for f in (.2, .5, .8):
            point = list(centre)
            point[2] = lo[2]+(hi[2]-lo[2])*f
            direction = [0.0, 0.0, 0.0]
            direction[axis] = sign
            # Only surfaces at or beyond the volume face count; rails inside the volume are not a wall.
            t = min((t for tri in triangles if (t := checks._ray(point, direction, tri)) is not None
                     and t >= half-.5), default=None)
            if t is not None and t <= half+24:
                hits.append(t-half)
        if hits:
            score = (len(hits), -sum(hits)/len(hits))
            if best is None or score > best[0]:
                best = (score, side, max(0.0, min(hits)))
    return (best[1], best[2]) if best else None


def _ladder_visual(p, m, lo, hi, wall):
    """Stack stock metal ladder modules against the wall; props are not solid (the surface is climbed)."""
    side, gap = wall
    axis, sign = SIDES[side]
    centre = [(a+b)/2 for a, b in zip(lo, hi)]
    origin = list(centre)
    # The 3-unit panel stands 2 units off the wall face.
    origin[axis] = (hi[axis] if sign > 0 else lo[axis])+sign*gap-sign*3.5
    # Model +X faces the climber (its cage sits on +X): turn it away from the wall.
    yaw = {'+x': 180, '-x': 0, '+y': 270, '-y': 90}[side]
    height, z, ids = hi[2]-lo[2], lo[2], []
    for size in LADDER_SEGMENTS:
        while height-(z-lo[2]) >= size:
            model = LADDER_MODEL.format(size)
            if not p.game.exists(model+'_c'):
                raise OpError(f'installed CS2 has no {model}; use visual="none" and add rails yourself')
            e = entity(p, m, 'prop_static', {'model': model, 'solid': 0},
                       [origin[0], origin[1], z], [0, yaw, 0])
            ids.append(e['nodeID'])
            z += size
    return {'wall': side, 'segments': len(ids), 'ids': ids, 'height': z-lo[2]}


def add_ladder(p, m, op, refs):
    # CS2 uses the ladder surface, unlike the legacy HL2 point entity.
    lo, hi, _ = bounds(op)
    material = 'materials/tools/toolsinvisibleladder.vmat'
    if not p.game.exists(material+'_c'):
        raise OpError('installed CS2 has no toolsinvisibleladder material')
    visual = op.get('visual', 'metal')
    if visual not in ('metal', 'none'):
        raise OpError('visual must be "metal" (stock rails and rungs) or "none" (invisible surface only)')
    wall = op.get('wall')
    if wall is not None and wall not in SIDES:
        raise OpError('wall must be +x, -x, +y or -y (side of the volume that touches the wall)')
    if hi[2]-lo[2] < 16:
        raise OpError('ladder must be at least 16 units tall')
    out = {'op': 'add_ladder'}
    if visual == 'metal':
        found = (wall, 0.0) if wall else _ladder_wall(m, lo, hi)
        if found is None:
            # Free-standing: the panel goes in the middle of the thin axis.
            axis = 0 if hi[0]-lo[0] <= hi[1]-lo[1] else 1
            side = '+x' if axis == 0 else '+y'
            found = (side, 3.5-(hi[axis]-lo[axis])/2)
            out['note'] = 'no wall within 24 units; ladder panel stands free in the middle of the volume'
        out['visual'] = _ladder_visual(p, m, lo, hi, found)
        depth = hi[SIDES[found[0]][0]]-lo[SIDES[found[0]][0]]
        if depth > 24:
            out['warning'] = f'volume is {depth:g} deep: the player stops at its front face, far from the rungs; use 8-16'
    nodes = p._new_meshes(m, {'shape': 'box', 'mins': lo, 'maxs': hi}, material, None)
    m.attach(nodes[0])
    if op.get('ref'):
        refs[op['ref']] = nodes[0]['nodeID']
    return {**out, 'id': nodes[0]['nodeID'], 'targetname': None}


def check_physics(p, m):
    problems = []
    names = {e['entity_properties'].get('targetname') for e in m.entities()}
    incoming = {c.get('targetName') for e in m.entities() for c in e.get('connectionsData', [])}
    has_script = bool(m.entities(classname='point_script'))
    for e in m.entities():
        cls, kv, nid = m.classname(e), e['entity_properties'], e['nodeID']
        if cls in ('func_door', 'func_door_rotating'):
            flags = int(kv.get('spawnflags') or 0)
            if not flags & (256 | 1024) and kv.get('targetname') not in incoming and not has_script:
                problems.append(checks.problem('warning', 'door_no_activation', nid,
                    'Door has no use/touch flag, incoming I/O or point_script.', 'Connect a button/trigger or add a script.'))
        if cls in ('func_button', 'func_physical_button') and not e.get('connectionsData'):
            if has_script:   # a script may listen to it (add_story ConnectOutput, cs_script)
                problems.append(checks.problem('warning', 'button_no_targets', nid,
                    'Button has no outputs; fine if a script listens to it (add_story until.button).', 'Add targets otherwise.'))
            else:
                problems.append(checks.problem('error', 'button_no_targets', nid, 'Button has no outputs.', 'Add targets.'))
        for c in e.get('connectionsData', []):
            t = c.get('targetName', '')
            if t and not t.startswith('!') and '*' not in t and t not in names:
                problems.append(checks.problem('error', 'physics_io_target_missing', nid,
                    f'I/O target {t!r} does not exist.', 'Create the target or correct its name.'))
        if cls in ('prop_physics', 'prop_physics_override', 'prop_physics_multiplayer'):
            try:
                valid = model_physics(p, kv.get('model', ''))['has_physics']
            except (ValueError, OSError):
                valid = False
            if not valid:
                problems.append(checks.problem('error', 'physics_model_no_body', nid,
                    'Physics prop has no compiled physics body.', "Build the model with collision='hull'."))
    return problems


PROBE_CODE = '''import { Instance } from "cs_script/point_script";
Instance.RegisterCheatCommand("p_mcp_physics_probe", args => {
 const [token, ...names] = args.trim().split(/\\s+/);
 for (const name of names) {
  const matches = Instance.FindEntitiesByName(name).filter(e => e.IsValid());
  if (!matches.length) Instance.Msg("P_PHYSICS "+token+" "+JSON.stringify({name,count:0,entity:null}));
  for (const e of matches.slice(0,16)) Instance.Msg("P_PHYSICS "+token+" "+JSON.stringify({name,count:matches.length,
   entity:{classname:e.GetClassName(), origin:e.GetAbsOrigin(), angles:e.GetAbsAngles(),
   velocity:e.GetAbsVelocity(), health:e.GetHealth(), alive:e.IsAlive()}}));
 }
 const debris = ["prop_physics","prop_physics_override","prop_physics_multiplayer"]
  .reduce((n,cls)=>n+Instance.FindEntitiesByClass(cls).filter(e=>e.IsValid()).length,0);
 Instance.Msg("P_PHYSICS_END "+token+" "+JSON.stringify({map:Instance.GetMapName(),debris}));
});
'''


def _probe_asset(p):
    # P owns models/mcp, while the maps/scripts/mcp zone belongs to S.
    rel = 'models/mcp/p_runtime/p_probe'
    source = ensure_inside(p.s.content_addon/(rel+'.js'), p.s.content_addon)
    compiled = ensure_inside(p.s.game_addon/(rel+'.vjs_c'), p.s.game_addon)
    if (source.exists() and source.read_text(encoding='utf-8') == PROBE_CODE and compiled.exists()
            and compiled.stat().st_mtime >= source.stat().st_mtime):
        return rel+'.vjs'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(PROBE_CODE, encoding='utf-8')
    job = compiler.compile_assets(p.s, p.jobs, [source], force=True, timeout=40)
    state = p.jobs.wait(job.id, 45)
    if state.state != 'succeeded' or not state.result.get('ok') or not compiled.exists():
        raise game.GameError(f'physics probe compilation failed: {state.log}')
    return rel+'.vjs'


def physics_probe(p, map, names):
    if not isinstance(names, list) or not names or len(names) > 64:
        raise OpError('names must contain 1..64 exact targetnames')
    names = [name(n) for n in names]
    with game.exclusive():
        # Public views owns loading/focusing/verification; a probe cannot switch
        # maps outside that lifecycle. The frame is also useful to the caller.
        frame = game.views(p.s, map, [{'name': 'p_probe_context', 'ang': [0, 0, 0]}])
        rel = _probe_asset(p)
        token = uuid.uuid4().hex[:12]
        prefix = 'P_PHYSICS '+token+' '
        end = 'P_PHYSICS_END '+token+' '
        cmd = 'p_mcp_physics_probe '+token+' '+' '.join(names)
        lines = game.query(p.s, cmd, end, timeout=1, retries=0)
        if not any(x.startswith(end) for x in lines):
            game.command(p.s, 'ent_create point_script { cs_script '+rel+' targetname p_mcp_probe_runtime }', wait=.5)
            lines = game.query(p.s, cmd, end, timeout=4, retries=2)
        for ln in lines:
            if ln.startswith(end):
                data = json.loads(ln[len(end):])
                if str(data.get('map', '')).split('/')[-1] != map:
                    raise game.GameError('probe answered for a different map')
                observations = {n: {'name': n, 'count': 0, 'exists': False, 'entities': []} for n in names}
                for row in lines:
                    if row.startswith(prefix):
                        record = json.loads(row[len(prefix):])
                        observed = observations[record['name']]
                        observed.update(count=record['count'], exists=bool(record['count']))
                        if record['entity'] is not None:
                            observed['entities'].append(record['entity'])
                if any(not any(row.startswith(prefix) and json.loads(row[len(prefix):])['name'] == n
                               for row in lines) for n in names):
                    raise game.GameError('physics probe snapshot is incomplete')
                return {'ok': True, **data, 'entities': list(observations.values()),
                        'context_frame': frame['shots'][0]['path']}
        raise game.GameError('physics probe did not answer: '+' | '.join(lines[-6:]))


OPERATIONS = {fn.__name__: fn for fn in (add_door, add_button, add_mover, add_rotator,
                                      add_physics_prop, add_breakable, add_zone, add_ladder)}
