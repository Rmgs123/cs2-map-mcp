"""Ground vehicles on the shared controllable registry and mechanics fragments."""
from __future__ import annotations

import json
import math
import shutil
import tempfile
from pathlib import Path

from . import blender_run, compiler, control, kv3, layout, materials, mechanics, models, sound
from .config import PROJECT_ROOT, ensure_inside
from .ext.models import asset_path

DATA = Path(__file__).parent / 'data' / 'vehicles'
BODY = 'models/mcp_v/car/car.vmdl'
WHEEL = 'models/mcp_v/wheel/wheel.vmdl'
# Wheel axle centres at rest (body frame; the body origin is the ground under the car). car.js has the same table.
WHEELS = [[62, 36, 16], [62, -36, 16], [-60, 36, 16], [-60, -36, 16]]
ASSET_VERSION = 3
MATERIALS = [  # name, colour, metalness, roughness, emissive glow
    ('paint', [58, 74, 50], .35, .42, 0), ('roof', [196, 190, 168], .1, .5, 0), ('trim', [22, 23, 25], .1, .7, 0),
    ('rim', [168, 172, 178], .9, .28, 0), ('window', [14, 20, 24], .4, .06, 0), ('tyre', [26, 26, 28], 0, .92, 0),
    ('lamp', [255, 236, 190], 0, .3, 4), ('tail', [200, 20, 18], 0, .3, 2.5), ('amber', [255, 130, 20], 0, .3, 2.5)]
FIELDS = {'speed', 'reverse', 'accel', 'brake', 'grip', 'steer', 'mass', 'wheel_model', 'engine', 'crash_damage',
          'wheels', 'suspension', 'seat', 'door', 'exits', 'size', 'passengers', 'pilot', 'scale'}
STEER = {'max': 32, 'fast': 9, 'rate': 120, 'return': 200}
SUSPENSION = {'up': 9, 'down': 12, 'stiffness': 16, 'rebound': 7, 'damping': 14, 'grip': .85}


def _object(value, field, defaults, ranges):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise ValueError(f'{field} must be an object with fields {sorted(defaults)}')
    return {k: mechanics.number(value.get(k, d), f'{field}.{k}', *ranges[k]) for k, d in defaults.items()}


def _steer(value):
    """steer: the old number (max angle) or {max, fast, rate, return}: the wheels turn less and slower at speed."""
    if not isinstance(value, dict):
        value = {'max': value, 'fast': min(STEER['fast'], mechanics.number(value, 'steer', 5, 45))}
    s = _object(value, 'steer', STEER, {'max': (5, 45), 'fast': (1, 45), 'rate': (10, 720), 'return': (10, 1440)})
    if s['fast'] > s['max']:
        raise ValueError('steer.fast (the angle at top speed) must not exceed steer.max')
    return s


def _wheels(value, wheel_model):
    """wheels: {positions: 4 axle centres [x,y] (front left, front right, rear left, rear right), radius, z}."""
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {'positions', 'radius', 'z', 'scale'}:
        raise ValueError('wheels must be {positions: [[x,y] x4], radius, z}')
    positions = value.get('positions', [w[:2] for w in WHEELS])
    if (not isinstance(positions, list) or len(positions) != 4 or
            any(not isinstance(w, (list, tuple)) or len(w) != 2 for w in positions)):
        raise ValueError('wheels.positions must be four [x,y] (front left, front right, rear left, rear right)')
    positions = [[mechanics.number(c, 'wheels.positions', -400, 400) for c in w] for w in positions]
    if not (positions[0][0] > positions[2][0] and positions[1][0] > positions[3][0]):
        raise ValueError('wheels.positions: the first two are the front axle (larger x, the car faces +x)')
    radius = mechanics.number(value.get('radius', 16), 'wheels.radius', 4, 64)
    out = {'positions': positions, 'radius': radius, 'z': mechanics.number(value.get('z', radius), 'wheels.z', 2, 160)}
    # The default wheel model is 16 in radius: it is scaled to another radius.
    out['scale'] = round(radius / 16, 4) if wheel_model == WHEEL else 1
    return out


def _door(value):
    """door: where the driver's feet must be to take the wheel: {at: [x,y,z] body frame, radius}."""
    if not isinstance(value, dict) or set(value) - {'at', 'radius'} or 'at' not in value:
        raise ValueError('door must be {at: [x,y,z] in the body frame, radius}')
    return {'at': mechanics.vector(value['at'], 'door.at'),
            'radius': mechanics.number(value.get('radius', 62), 'door.radius', 16, 160)}


def _exits(value):
    """exits: 1..8 body-frame points tried in order when the driver leaves (inside a hollow body too)."""
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise ValueError('exits must be a list of 1..8 [x,y,z] points in the body frame')
    return [mechanics.vector(e, 'exits') for e in value]


def _size(value):
    """size: [length, width] of the body, for run-over hits, crash probes and passengers."""
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError('size must be [length, width] of the body')
    return [mechanics.number(value[0], 'size.length', 48, 1200), mechanics.number(value[1], 'size.width', 32, 400)]


HINTS = {'board': 'E: get in', 'ride': 'E: get out', 'drive': 'E: drive', 'blocked': 'No room to get out'}


def _passengers(ride):
    """passengers: a hollow body players ride in. floor/roof: body-frame heights; doors: [{at, radius, spots}] where
    `at` is the doorway (body frame) and `spots` the standing places (feet) of the passengers who got in there. E near a
    door (or inside) puts a player on a random free spot of that door's group, E again lets them off; anyone standing
    inside when the body starts moving is put on a spot. Riders keep their weapons and look around. hint: the prompt
    texts {board, ride, drive}."""
    if not isinstance(ride, dict) or set(ride) - {'floor', 'roof', 'doors', 'hint'}:
        raise ValueError('passengers must be {floor, roof, doors: [{at, radius, spots}], hint}')
    doors = ride.get('doors', [])
    if not isinstance(doors, list) or not 1 <= len(doors) <= 8:
        raise ValueError('passengers.doors must list 1..8 doors {at: [x,y,z], radius, spots: [[x,y,z], ...]}')
    out, total = [], 0
    for d in doors:
        if not isinstance(d, dict) or set(d) - {'at', 'radius', 'spots'} or 'at' not in d:
            raise ValueError('a passenger door is {at: [x,y,z] body frame, radius, spots: [[x,y,z], ...]}')
        spots = d.get('spots', [])
        if not isinstance(spots, list) or not spots:
            raise ValueError('every passenger door needs its standing spots')
        total += len(spots)
        out.append({'at': mechanics.vector(d['at'], 'passengers.doors.at'),
                    'radius': mechanics.number(d.get('radius', 64), 'passengers.doors.radius', 16, 160),
                    'spots': [mechanics.vector(x, 'passengers.doors.spots') for x in spots]})
    if total > 32:
        raise ValueError('at most 32 passenger spots')
    hint = ride.get('hint', {})
    if not isinstance(hint, dict) or set(hint) - set(HINTS) or any(not isinstance(v, str) or len(v) > 64 for v in hint.values()):
        raise ValueError('passengers.hint is {board, ride, drive, blocked}: texts up to 64 characters')
    return {'floor': mechanics.number(ride.get('floor', 24), 'passengers.floor', 0, 200),
            'roof': mechanics.number(ride.get('roof', 140), 'passengers.roof', 40, 400),
            'doors': out, 'hint': {**HINTS, **hint}}


def validate(spec, m=None):
    spec['camera'].setdefault('start', 'chase' if spec['camera']['chase'] is not False else 'fpv')
    origin = spec.get('origin', [0, 0, 8])
    if isinstance(origin, dict):
        if m is None:
            raise ValueError('relative origin needs map layout')
        origin = layout.point(m, origin)[0]
    spec['origin'] = mechanics.vector(origin, 'origin')
    for key, default, lo, hi in [
        ('yaw', 0, -360, 360), ('speed', 700, 50, 1000), ('reverse', 180, 10, 400),
        ('accel', 300, 50, 1500), ('brake', 700, 100, 2500), ('grip', 9, 1, 25),
        ('mass', 1500, 100, 5000), ('crash_damage', .12, 0, 1)]:
        spec[key] = mechanics.number(spec.get(key, default), key, lo, hi)
    spec['steer'] = _steer(spec.get('steer', STEER))
    spec['suspension'] = _object(spec.get('suspension'), 'suspension', SUSPENSION,
                                 {'up': (1, 48), 'down': (1, 64), 'stiffness': (1, 60), 'rebound': (1, 60),
                                  'damping': (1, 60), 'grip': (.1, 2)})
    # Where the driver sits (body frame, hips): the dummy is drawn there, the hidden player 14 units lower.
    spec['seat'] = mechanics.vector(spec.get('seat', [-6, 17, 40]), 'seat')
    spec['reach'] = mechanics.number(spec.get('reach', 80), 'reach', 16, 160)     # long bodies: the wheel is far inside
    spec['model'] = mechanics.model_path(spec.get('model', BODY))
    spec['wheel_model'] = mechanics.model_path(spec.get('wheel_model', WHEEL), 'wheel_model')
    spec['wheels'] = _wheels(spec.get('wheels'), spec['wheel_model'])
    # Own bodies (a bus, a van): the driver's door, the exit points, the footprint and riding passengers. Absent
    # fields stay absent, so stock cars keep their recorded configuration (and script file).
    if 'door' in spec:
        spec['door'] = _door(spec['door'])
    if 'exits' in spec:
        spec['exits'] = _exits(spec['exits'])
    if 'size' in spec:
        spec['size'] = _size(spec['size'])
    if 'passengers' in spec:
        spec['passengers'] = _passengers(spec['passengers'])
        if 'size' not in spec:
            raise ValueError('passengers need size: [length, width] of the body')
    if 'pilot' in spec:
        # The driver's stand-in: its scale and where its feet are (body frame); default half size at the seat.
        if not isinstance(spec['pilot'], dict) or set(spec['pilot']) - {'scale', 'at'}:
            raise ValueError('pilot must be {scale, at: [x,y,z] feet of the stand-in in the body frame}')
        spec['pilot'] = {'scale': mechanics.number(spec['pilot'].get('scale', .5), 'pilot.scale', .2, 1.5),
                         'at': mechanics.vector(spec['pilot'].get('at', spec['seat']), 'pilot.at')}
    engine = spec.get('engine', {'idle': 'mcp.v9_engine_idle', 'drive': 'mcp.v9_engine_drive'})
    if engine is not False:
        mechanics.keys(engine, {'idle', 'drive'}, 'engine')
        import re
        if any(not isinstance(engine.get(k), str) or not re.fullmatch(r'[A-Za-z0-9_.]{1,128}', engine[k]) for k in ('idle', 'drive')):
            raise ValueError('engine.idle/drive must be existing sound event names')
    spec['engine'] = engine
    if spec.get('sound'):
        raise ValueError('car uses engine:{idle,drive}, not sound')
    _scale(spec)
    # These are internal constants, not recipe fields. Do not add to spec: validation runs twice.
    return mechanics.validate_health(spec, 150, native=True)


def _scale(spec):
    """scale: every body-frame length of the recipe (wheels, seat, door, exits, size, passengers, the stand-in's
    place, cameras, suspension travel) times k; reach is the player's, it stays, for a bigger or smaller body - the model itself comes from
    model_build(file=<its .vmdl>, scale=k) (23.10: a 1.2x SUV needed every field by hand). Applied once: the
    spec keeps the scaled values with scale 1, so a second validation leaves it alone."""
    k = mechanics.number(spec.get('scale', 1), 'scale', .25, 4)
    if k == 1:
        return
    def vec(v):
        return [round(c * k, 4) for c in v]

    def one(v):
        return round(v * k, 4)
    w = spec['wheels']
    spec['wheels'] = _wheels({'positions': [vec(p) for p in w['positions']], 'radius': one(w['radius']),
                              'z': one(w['z'])}, spec['wheel_model'])
    spec['seat'] = vec(spec['seat'])
    if 'door' in spec:
        spec['door'] = _door({'at': vec(spec['door']['at']), 'radius': one(spec['door']['radius'])})
    if 'exits' in spec:
        spec['exits'] = [vec(e) for e in spec['exits']]
    if 'size' in spec:
        spec['size'] = _size(vec(spec['size']))
    if 'passengers' in spec:
        ride = spec['passengers']
        spec['passengers'] = _passengers({'floor': one(ride['floor']), 'roof': one(ride['roof']), 'hint': ride['hint'],
                                          'doors': [{'at': vec(d['at']), 'radius': one(d['radius']),
                                                     'spots': [vec(s) for s in d['spots']]} for d in ride['doors']]})
    if 'pilot' in spec:
        spec['pilot'] = {**spec['pilot'], 'at': vec(spec['pilot']['at'])}
    cam = spec['camera']
    for key in ('forward', 'up', 'right'):
        if key in cam:
            cam[key] = one(cam[key])
    if cam.get('chase'):
        cam['chase'] = {key: one(v) for key, v in cam['chase'].items()}
    spec['camera'] = mechanics.validate_camera(cam)
    for key in ('up', 'down'):
        spec['suspension'][key] = mechanics.number(one(spec['suspension'][key]), f'suspension.{key} (scaled)', 1, 64)
    spec['scale'] = 1


def names(spec):
    p = 'v9_' + spec['name']
    return {'body': p + '_body', 'wheels': [p + '_wheel_' + str(i) for i in range(4)],
            'idle_sound': p + '_idle', 'drive_sound': p + '_drive'}


def world(origin, yaw, off):
    a = math.radians(yaw)
    return [origin[0] + off[0] * math.cos(a) - off[1] * math.sin(a),
            origin[1] + off[0] * math.sin(a) + off[1] * math.cos(a), origin[2] + off[2]]


def entities(spec, n):
    seats = mechanics.seat_entities(n, spec['origin'])
    ops = [seats['filter'], mechanics.entity('prop_physics_override', n['body'], spec['origin'],
        {'model': spec['model'], 'health': mechanics.NATIVE_HEALTH, 'spawnflags': 0,
         'skipPreSettle': 1, 'damagefilter': n['filter']}, [0, spec['yaw'], 0]), seats['decoy']]
    w = spec['wheels']
    for i, off in enumerate([x, y, w['z']] for x, y in w['positions']):
        ops.append(mechanics.entity('prop_dynamic', n['wheels'][i], world(spec['origin'], spec['yaw'], off),
            {'model': spec['wheel_model'], 'solid': 0, 'parentname': n['body']}, [0, spec['yaw'], 0]))
    ops.append(mechanics.entity('custom_hud_layout', n['hud'], spec['origin'],
        {'layout': f'panorama/layout/custom_game/{n["hud"]}.vxml'}))
    if spec['engine']:
        for key in ('idle', 'drive'):
            ops.append(mechanics.entity('point_soundevent', n[key + '_sound'], spec['origin'],
                {'soundName': spec['engine'][key], 'sourceEntityName': n['body'], 'parentname': n['body'], 'startOnSpawn': 0}))
    return ops + mechanics.effect_entities(n, spec['origin'], n['body'])


def prepare(project, spec):
    defaults = spec['model'] == BODY or spec['wheel_model'] == WHEEL
    # Dry-run never creates assets. Stock defaults are built by the real pass only.
    if defaults and not control.PREVIEW.get():
        ensure_assets(project)
    for model in (spec['model'], spec['wheel_model']):
        if control.PREVIEW.get() and model in (BODY, WHEEL):
            continue
        asset_path(project, model)
        if not (project.s.game_addon / (model + '_c')).is_file() and not project.game.exists(model + '_c'):
            raise ValueError('compiled car model missing: ' + model)
    if spec['engine']:
        for event in spec['engine'].values():
            if control.PREVIEW.get() and event in ('mcp.v9_engine_idle', 'mcp.v9_engine_drive'):
                continue
            if not sound.event_exists(project, event):
                raise ValueError('unknown car engine event: ' + event)
    fit(project, spec)


def fit(project, spec):
    """The recipe's body-frame places (size, seat, doors, passenger spots) must lie in the body model. 25.10: the bus
    recipe of the docs on the stock car model (204x84) gave riders standing in the air beside a small car facing the
    wrong way, and their shots went 10 degrees wide; nothing refused it."""
    from . import visual
    got = visual.model_bounds(project, spec['model']) if project is not None else None
    if not got:
        return                      # not compiled yet (a dry run of a stock body): checked on the real pass
    lo, hi = got[0], got[1]
    length, width = hi[0] - lo[0], hi[1] - lo[1]
    problems = []

    def inside(p, label, margin=8.0, head=0.0):
        if not (lo[0] - margin <= p[0] <= hi[0] + margin and lo[1] - margin <= p[1] <= hi[1] + margin
                and lo[2] - margin <= p[2] <= hi[2] - head + margin):
            problems.append(f'{label} {[round(c) for c in p]}')
    if 'size' in spec and (spec['size'][0] > length * 1.25 + 8 or spec['size'][1] > width * 1.25 + 8):
        problems.append(f'size {spec["size"]}')
    inside(spec['seat'], 'seat')
    if 'door' in spec:
        inside(spec['door']['at'], 'door.at', 64 + spec['door']['radius'])     # where a player stands by the door
    for d in (spec.get('passengers') or {}).get('doors', []):
        inside(d['at'], 'passengers door', 64 + d['radius'])
        for s in d['spots']:
            inside(s, 'passenger spot', head=40)       # feet with room for a body above them, under the roof
    if problems:
        raise ValueError(
            f'the recipe does not fit the body model {spec["model"]} ({round(length)}x{round(width)}, height '
            f'{round(lo[2])}..{round(hi[2])} in the body frame, +x forward): {", ".join(problems[:6])} lie outside it. '
            'These fields describe another body (a bus recipe on a car model leaves riders in the air): give "model" '
            'a body of that size (model_build, or scale with a scaled copy) or fit the fields to this model (model_info)')


def ensure_assets(project):
    """Scoped model_build driver: shared pipeline, V-owned destinations, no new MCP tool."""
    manifest = project.s.content_addon / 'models/mcp_v/assets.json'
    signature = __import__('hashlib').sha256((DATA / 'models.py').read_bytes()).hexdigest()
    current = {'version': ASSET_VERSION, 'source': signature}
    try:
        cached = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        cached = None
    required = [BODY, WHEEL, 'sounds/mcp_v/v9_engine_idle.vsnd', 'sounds/mcp_v/v9_engine_drive.vsnd']
    required += [f'materials/mcp_v/v9_{m[0]}/v9_{m[0]}.vmat' for m in MATERIALS]
    if cached == current and all(
            (project.s.game_addon / (p + '_c')).is_file() for p in required) and all(
            sound.event_exists(project, e) for e in ('mcp.v9_engine_idle', 'mcp.v9_engine_drive')):
        return
    for name, color, metal, rough, glow in MATERIALS:
        solid = {'pattern': 'solid', 'color': color, 'size': 64}
        if glow:
            materials.create(project.s, 'v9_' + name, pattern=solid, kind='emissive', color=color, glow=glow, folder='mcp_v')
        else:
            materials.create(project.s, 'v9_' + name, pattern=solid, roughness=rough, metalness=metal,
                             surface='rubber' if name == 'tyre' else 'metal', folder='mcp_v')
    for part in ('car', 'wheel'):
        folder = ensure_inside(project.s.content_addon / 'models/mcp_v' / part, project.s.content_addon)
        source = folder / 'sources' / signature[:12]
        source.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DATA / 'models.py', source / 'script.py')
        fbx = source / (part + '.fbx')
        with tempfile.TemporaryDirectory(prefix='v9-model-', dir=PROJECT_ROOT / '.local/tmp') as tmp:
            params = Path(tmp) / 'params.json'
            params.write_text(json.dumps({'part': part}), encoding='utf-8')
            job = blender_run.start(project.s, project.jobs, models.BLENDER_DIR / 'driver.py',
                ['build', str(source / 'script.py'), str(fbx), 'hull', str(params)], timeout=180)
            state = project.job_status(job.id, 50, 0)
            while state['state'] == 'running':
                state = project.job_status(job.id, 50, 0)
        meta = state.get('result') or {}
        if state['state'] != 'succeeded' or not meta.get('ok') or not fbx.is_file():
            raise ValueError('car Blender build failed: ' + state['log'])
        children = [
            {'_class': 'RenderMeshList', 'children': [{'_class': 'RenderMeshFile', 'name': part,
                'filename': project.s.addon_relative(fbx), 'import_scale': .0254,
                'import_filter': {'exclude_by_default': False, 'exception_list': []}}]},
            {'_class': 'MaterialGroupList', 'children': [{'_class': 'DefaultMaterialGroup',
                'remaps': [{'from': x['token'] + '.vmat', 'to': asset_path(project, x['material'], '.vmat', 'materials')} for x in meta['materials']],
                'use_global_default': False, 'global_default_material': ''}]},
            {'_class': 'PhysicsShapeList', 'children': [{'_class': 'PhysicsHullFile',
                'filename': project.s.addon_relative(source / f), 'import_scale': .0254, 'surface_prop': 'rubber',
                'collision_prop': 'default', 'import_filter': {'exclude_by_default': False, 'exception_list': []}} for f in meta['collision_files']]}]
        if part == 'car':
            children += [
                {'_class': 'PhysicsBodyMarkupList', 'children': [{'_class': 'PhysicsBodyMarkup', 'target_body': '', 'mass_override': 1500.0, 'inertia_scale': 1.0}]},
                {'_class': 'GameDataList', 'children': [{'_class': 'GenericGameData', 'game_class': 'prop_data',
                    'game_keys': {'allowstatic': True, 'spawn_motion_disabled': False, 'physicsmode': 1,
                        'health': mechanics.NATIVE_HEALTH, 'dmg.bullets': 1.0, 'dmg.club': 1.0, 'dmg.explosive': 1.0}}]}]
        doc = {'rootNode': {'_class': 'RootNode', 'children': children, 'model_archetype': '', 'primary_associated_entity': '', 'anim_graph_name': ''}}
        vmdl = folder / (part + '.vmdl')
        mechanics.write_asset(project, vmdl, kv3.dumps(doc, header=kv3.HEADER_MODELDOC))
        compiled = models._compile_model(project, vmdl, [])
        if not compiled['ok']:
            raise ValueError('car compilation failed: ' + compiled['log'])
        mechanics.write_asset(project, folder / 'model.json', json.dumps({**meta, 'compile': compiled}, indent=2))
    # sound_generate's synthesizer, with scoped WAV destinations (the public tool currently fixes sounds/mcp).
    for key, frequency in [('idle', 32), ('drive', 72)]:
        samples, rate, loop = sound.synthesize('drone', 2, {'frequency': frequency, 'gain': .3, 'loop': True, 'seed': 9})
        path = project.s.content_addon / f'sounds/mcp_v/v9_engine_{key}.wav'
        ensure_inside(path, project.s.content_addon)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            sound._backup(project, path)
        path.write_bytes(sound.wav_bytes(samples, rate, loop))
        compiled = sound._compile(project, path)
        if not compiled.get('ok'):
            raise ValueError('car sound compilation failed: ' + str(compiled))
        sound.sound_event(project, 'v9_engine_' + key, 'loop', sound=f'sounds/mcp_v/v9_engine_{key}.vsnd', volume=.45, distance=900)
    mechanics.write_asset(project, manifest, json.dumps(current))


def register():
    return control.register_kind('car', validate, ['core', 'hud', 'camera', 'seat', 'health'], entities, DATA / 'car.js',
        fields=FIELDS, prefix='v9', script_prefix='v9_control', hud_prefix='mcp_v_control', names=names, prepare=prepare,
        camera={'forward': 0, 'up': 72, 'chase': {'forward': -270, 'up': 105}, 'start': 'chase'},
        hud=(DATA / 'hud.xml', DATA / 'hud.css'), tag='V_CAR', title='CAR / DRIVE',
        hint='W/S: gas/brake | A/D: steer | SPACE: handbrake | SHIFT: crawl | R: camera | E: exit')


register()
