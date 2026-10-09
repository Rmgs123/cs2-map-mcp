"""Controllable objects: a registry of kinds (drone here; a car or a turret register their own) on the shared
mechanics fragments. Validated map recipe, scoped assets, the script assembled by ``mechanics.build_script``."""
from __future__ import annotations

import copy
import json
import math
import re
import shutil
import tempfile
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from . import blender_run, checks, compiler, kv3, layout, materials, mechanics, models, scripting, sound, visual
from .config import PROJECT_ROOT, ensure_inside
from .ext.models import asset_path

DATA = Path(__file__).parent / 'data' / 'control'
MARKER = '// CS2MAP_CONTROL '
PREVIEW = ContextVar('control_preview', default=False)
# Shared helpers live in mechanics; the old names stay importable (tests patch them here).
keys, number, vector = mechanics.keys, mechanics.number, mechanics.vector
write_asset = mechanics.write_asset
EFFECTS, NATIVE_HEALTH, PILOT_MODEL = mechanics.EFFECTS, mechanics.NATIVE_HEALTH, mechanics.PILOT_MODEL
# Fields every kind accepts; a kind adds its own (`fields`).
COMMON_FIELDS = {'op', 'name', 'kind', 'model', 'origin', 'yaw', 'camera', 'sound', 'health', 'respawn', 'effects',
                 'reach', 'hud', 'ref'}
DRONE_FIELDS = frozenset({'controller', 'speed', 'climb', 'accel', 'tilt', 'land', 'bounds', 'hull', 'battery', 'rotors',
                          'physics', 'mass', 'on_exit'})


@dataclass
class Kind:
    name: str
    validate: object          # (spec, m) -> spec: the kind's own fields, after the common ones were checked
    fragments: list           # mechanics fragments in order (the motion adapter is added last)
    entities: object          # (spec, names) -> list of add_entity operations
    motion: Path              # JS adapter of the kind: header "// @fragment <kind>_motion"
    fields: frozenset
    prefix: str               # targetname prefix of the entities (d8)
    script_prefix: str        # script name prefix (d8_control)
    hud_prefix: str           # HUD layout name prefix (mcp_d_control)
    names: object             # (spec) -> extra targetnames, or None
    prepare: object           # (project, spec) -> None: compiled models, hull... raises ValueError
    camera: dict
    hud_files: tuple          # (xml, css) templates: HUDNAME = layout name, HUDTITLE / HUDHINT = title and key hints
    tag: str
    title: str = 'CONTROL'
    hint: str = 'E: exit'


KINDS: dict[str, Kind] = {}


def register_kind(kind, validate, fragments, entities, motion, *, fields=(), prefix, script_prefix=None, hud_prefix=None,
                  names=None, prepare=None, camera=None, hud=None, tag=None, title=None, hint=None):
    """Add a kind of controllable object (a car, a turret) without touching this module.

    validate(spec, m): validate the kind's own fields (spec already holds the checked common fields and a camera),
        return the spec; relative points go through `layout.point(m, ...)`.
    fragments: shared fragments of data/mechanics (['core','hud','camera','seat','health', ...]).
    entities(spec, names): the add_entity operations (see mechanics.entity/seat_entities/effect_entities).
    motion: the JS adapter (`// @fragment <kind>_motion`, `// @requires ...`): the body of the script after the fragments.
    title, hint: HUD heading and key hints of the shared template (data/control/hud.xml); hud={'title': ...} overrides the title.
    names(spec): extra targetnames; the standard ones (script, hud, sound, pilot, filter, pawn, effects) are always there.
    """
    if not mechanics._NAME.fullmatch(kind):
        raise ValueError('kind: lowercase letter + up to 39 letters/digits/underscores')
    mechanics.register_fragment(f'{kind}_motion', Path(motion))
    KINDS[kind] = Kind(kind, validate, list(fragments), entities, Path(motion), frozenset(fields), prefix,
                       script_prefix or f'{prefix}_control', hud_prefix or f'mcp_{prefix}_control', names, prepare,
                       camera or {'forward': 22, 'up': 10}, tuple(hud or (DATA / 'hud.xml', DATA / 'hud.css')),
                       tag or kind.upper(), title or kind.upper(), hint or 'E: exit')
    return KINDS[kind]


def kind_of(spec) -> Kind:
    kind = spec.get('kind', 'drone')
    if kind not in KINDS:
        raise ValueError(f'unknown kind {kind!r}; registered kinds: {sorted(KINDS)}')
    return KINDS[kind]


def validate(op, m=None):
    if not isinstance(op, dict):
        raise ValueError('add_controllable: expected an object')
    k = kind_of(op)
    keys(op, COMMON_FIELDS | k.fields, 'add_controllable')
    spec = copy.deepcopy(op)
    name = spec.get('name')
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', name):
        raise ValueError('name: lowercase letter followed by at most 39 letters/digits/underscores')
    spec['kind'] = k.name
    # Default FPV eye of the drone: just ahead of the nose lens (+X 16.7, z 10) of the stock D quadcopter.
    spec['camera'] = mechanics.validate_camera(spec.get('camera'), k.camera)
    mechanics.validate_seat(spec)
    if 'hud' in spec:
        spec['hud'] = mechanics.validate_hud(spec['hud'])
    if 'sound' in spec and (not isinstance(spec['sound'], str) or
                            not re.fullmatch(r'[a-zA-Z0-9_.]{1,128}', spec['sound'])):
        raise ValueError('sound must be an existing sound event name')
    return k.validate(spec, m)


DRONE_MODEL = 'models/mcp_d/drone/drone.vmdl'
REMOTE_MODEL = 'models/mcp_d/remote/remote.vmdl'
DRONE_ROTORS = [{'model': 'models/mcp_d/rotor/rotor.vmdl', 'offset': [x, y, 20], 'speed': s}
                for x, y, s in ((-26, -26, 1440), (-26, 26, -1440), (26, -26, -1440), (26, 26, 1440))]


def validate_drone(spec, m=None):
    spec['speed'] = number(spec.get('speed', 400), 'speed', 1, 1000)
    spec['battery'] = number(spec.get('battery', 0), 'battery', 0, 3600)
    spec['physics'] = spec.get('physics', True)
    if not isinstance(spec['physics'], bool):
        raise ValueError('physics must be true (simulated body: inertia, collisions, damage) or false (kinematic)')
    spec['climb'] = number(spec.get('climb', spec['speed'] * .6), 'climb', 1, 1000)
    spec['accel'] = number(spec.get('accel', 900), 'accel', 50, 5000)
    spec['tilt'] = number(spec.get('tilt', 18), 'tilt', 0, 45)
    spec['land'] = number(spec.get('land', 90), 'land', 10, 400)
    spec['yaw'] = number(spec.get('yaw', 0), 'yaw', -360, 360)
    spec['mass'] = number(spec.get('mass', 8), 'mass', .5, 500)
    mechanics.validate_health(spec, 50, native=spec['physics'])
    spec['on_exit'] = spec.get('on_exit', 'hover' if spec['physics'] else 'home')
    if spec['on_exit'] not in (('hover', 'land') if spec['physics'] else ('home',)):
        raise ValueError('on_exit: hover|land for a physics body; a kinematic body always returns home')
    if spec['health'] and not spec['physics']:
        raise ValueError('health needs physics:true; a kinematic body is not solid and cannot be hit')
    keys(spec.get('controller'), {'model', 'at', 'angles'}, 'controller')
    controller = spec['controller']
    at = controller.get('at')
    if isinstance(at, dict):
        if m is None:
            raise ValueError('relative controller.at needs map layout')
        at = layout.point(m, at)[0]
    controller['at'] = vector(at, 'controller.at')
    controller['angles'] = vector(controller.get('angles', [0, 0, 0]), 'controller.angles')
    bounds = spec.get('bounds')
    if isinstance(bounds, dict) and 'room' in bounds:
        keys(bounds, {'room', 'layout'}, 'bounds')
        if m is None:
            raise ValueError('room bounds need map layout')
        bounds = copy.deepcopy(layout._room_ref(m, bounds)['interior'])
    keys(bounds, {'mins', 'maxs'}, 'bounds')
    bounds = {k: vector(bounds.get(k), 'bounds.' + k) for k in ('mins', 'maxs')}
    spec['bounds'] = bounds
    hull = spec.setdefault('hull', {'mins': [-55, -55, -1], 'maxs': [55, 55, 26]})
    keys(hull, {'mins', 'maxs'}, 'hull')
    spec['hull'] = hull = {k: vector(hull.get(k), 'hull.' + k) for k in ('mins', 'maxs')}
    for i in range(3):
        if not hull['mins'][i] < hull['maxs'][i] or not bounds['mins'][i] < bounds['maxs'][i]:
            raise ValueError('bounds and hull must have positive volume')
        if bounds['maxs'][i] - bounds['mins'][i] <= hull['maxs'][i] - hull['mins'][i] + 2:
            raise ValueError('bounds must be larger than the hull with 1 unit clearance')
    origin = spec.get('origin', [(bounds['mins'][i] + bounds['maxs'][i])/2 for i in range(2)] +
                      [bounds['mins'][2] - hull['mins'][2] + 1])
    if isinstance(origin, dict):
        origin = layout.point(m, origin)[0]
    spec['origin'] = vector(origin, 'origin')
    if any(not bounds['mins'][i]+1 <= spec['origin'][i]+hull['mins'][i] or
           not spec['origin'][i]+hull['maxs'][i] <= bounds['maxs'][i]-1 for i in range(3)):
        raise ValueError('origin and its entire hull must fit inside bounds with 1 unit clearance')
    # The stock drone's blades are separate models: without them it flies with bare motors (05-06.10 m9_games).
    spec.setdefault('model', DRONE_MODEL)
    controller.setdefault('model', REMOTE_MODEL)
    rotors = spec.setdefault('rotors', [dict(r) for r in DRONE_ROTORS] if spec['model'] == DRONE_MODEL else [])
    if not isinstance(rotors, list) or len(rotors) > 8:
        raise ValueError('rotors must be a list of at most 8 {model,offset,speed?}')
    for rotor in rotors:
        keys(rotor, {'model', 'offset', 'speed'}, 'rotor')
        rotor['offset'] = vector(rotor.get('offset'), 'rotor.offset')
        rotor['speed'] = number(rotor.get('speed', 1440), 'rotor.speed', -10000, 10000)
    for value in [spec.get('model'), controller.get('model')] + [r.get('model') for r in rotors]:
        mechanics.model_path(value)
    return spec


def names(spec):
    k = kind_of(spec)
    n = mechanics.standard_names(k.prefix, spec['name'], script_prefix=k.script_prefix, hud_prefix=k.hud_prefix,
                                 with_effects=bool(spec.get('physics', True) and spec.get('health') and spec.get('effects')))
    if k.names:
        n.update(k.names(spec))
    return n


def drone_names(spec):
    name = spec['name']
    return {'drone': f'd8_{name}_body', 'remote': f'd8_{name}_remote',
            'rotors': [f'd8_{name}_rotor_{i}' for i in range(len(spec['rotors']))]}


def check_hull(project, spec):
    """Conservative envelope for all body yaw and independently spinning rotors."""
    required = [0., 0., 0.]
    zmin, zmax = 0., 0.
    parts = [(spec['model'], [0, 0, 0])] + [(r['model'], r['offset']) for r in spec['rotors']]
    for model, offset in parts:
        measured = visual.model_bounds(project, model)
        if measured is None:
            raise ValueError(f'cannot measure compiled model bounds: {model}; control hull cannot be verified')
        lo, hi = measured[:2]
        radius = math.hypot(max(abs(lo[0]), abs(hi[0])), max(abs(lo[1]), abs(hi[1]))) + math.hypot(*offset[:2])
        required[0] = required[1] = max(required[0], radius)
        zmin, zmax = min(zmin, offset[2]+lo[2]), max(zmax, offset[2]+hi[2])
    if any(spec['hull']['mins'][i] > -required[i] + .01 or spec['hull']['maxs'][i] < required[i] - .01 for i in range(2)) or \
            spec['hull']['mins'][2] > zmin + .01 or spec['hull']['maxs'][2] < zmax - .01:
        raise ValueError(f'hull must contain all rotations: XY radius {required[0]:.2f}, Z {zmin:.2f}..{zmax:.2f}')


def prepare_drone(project, spec):
    for model in [spec['model'], spec['controller']['model']] + [r['model'] for r in spec['rotors']]:
        asset_path(project, model)
        # A source file alone does not prove that the prop can load.
        if not (project.s.game_addon / (model + '_c')).is_file() and not project.game.exists(model + '_c'):
            raise ValueError(f'compiled model is missing: {model}')
    check_hull(project, spec)


def drone_entities(spec, n):
    ops = []

    def add(cls, name, origin, kv=None, angles=None):
        ops.append(mechanics.entity(cls, name, origin, kv, angles))

    seats = mechanics.seat_entities(n, spec['origin'], spec['controller']['at'])
    if spec['physics']:
        # A real rigid body: the engine handles collisions, bullet impulses and gravity;
        # native health stays huge and the script counts damage against spec['health'].
        # In a script camera CS2 traces the pilot's knife from the camera at the drone: the pilot
        # must not hurt their own drone. The pawn is renamed while flying; this filter drops its damage.
        ops.append(seats['filter'])
        add('prop_physics_override', n['drone'], spec['origin'],
            {'model': spec['model'], 'health': NATIVE_HEALTH if spec['health'] else 0,
             'spawnflags': 0, 'skipPreSettle': 1, 'damagefilter': n['filter']}, [0, spec['yaw'], 0])
    else:
        add('prop_dynamic', n['drone'], spec['origin'], {'model': spec['model'], 'solid': 0}, [0, spec['yaw'], 0])
    add('prop_dynamic', n['remote'], spec['controller']['at'], {'model': spec['controller']['model'], 'solid': 6},
        spec['controller']['angles'])
    # Still stand-in of the pilot (the script sets the pilot's own model); see seat.js showPilot.
    # Parked below the map until a pilot enters, so it never overlaps the remote or the table.
    ops.append(seats['decoy'])
    for i, r in enumerate(spec['rotors']):
        add('prop_dynamic', n['rotors'][i], [a+b for a, b in zip(spec['origin'], r['offset'])],
            {'model': r['model'], 'solid': 0, 'parentname': n['drone']})
    add('custom_hud_layout', n['hud'], spec['controller']['at'], {'layout': f'panorama/layout/custom_game/{n["hud"]}.vxml'})
    if spec.get('sound'):
        add('point_soundevent', n['sound'], spec['origin'],
            {'soundName': spec['sound'], 'sourceEntityName': n['drone'], 'parentname': n['drone'], 'startOnSpawn': 0})
    ops += mechanics.effect_entities(n, spec['origin'], n['drone'])
    return ops


register_kind('drone', validate_drone, ['core', 'hud', 'camera', 'seat', 'health'], drone_entities, DATA / 'drone.js',
              fields=DRONE_FIELDS, prefix='d8', script_prefix='d8_control', hud_prefix='mcp_d_control',
              names=drone_names, prepare=prepare_drone, camera={'forward': 22, 'up': 10}, tag='D_CONTROL',
              title='QUADCOPTER / FLIGHT CONTROL',
              hint='WASD: fly | SPACE/CTRL: up/down | SHIFT: slow | R: camera | E: exit')


def generate(spec):
    """(script code, HUD xml, HUD css, add_entity operations) of any registered kind."""
    k = kind_of(spec)
    n = names(spec)
    code = mechanics.build_script(n['script'], [*k.fragments, f'{k.name}_motion'], {**spec, 'entities': n},
                                  marker=MARKER, marker_config=spec)
    title = (spec.get('hud') or {}).get('title') or k.title
    xml = (k.hud_files[0].read_text(encoding='utf-8').replace('HUDNAME', n['hud'])
           .replace('HUDTITLE', title).replace('HUDHINT', k.hint))
    from . import ui
    css = k.hud_files[1].read_text(encoding='utf-8') + ui.override(HUD_ROLES, spec.get('hud') or {}, 'bottom')
    return code, xml, css, k.entities(spec, n)


# The shared control HUD (drone, turret) and the car's: one panel #control with a title, a status line and hints.
HUD_ROLES = {'panel': '#control', 'title': '#title, #control .Title', 'hint': '#control .Hint',
             'warn': ['#control.Low #telemetry', '#control.Low .Status', '#control.Overheat #telemetry']}


def script_file(spec, n) -> str:
    """The addon-wide script file of this definition: its name plus a digest of the definition, so two maps may both
    have a `drone1` with different settings (each gets its own file; identical definitions share one)."""
    import hashlib
    digest = hashlib.sha1(json.dumps(spec, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:8]
    return f'{n["script"]}_{digest}'


def add_controllable(project, m, op, refs):
    spec = validate(op, m)
    k = kind_of(spec)
    if k.prepare:
        k.prepare(project, spec)
    if spec.get('sound') and not sound.event_exists(project, spec['sound']):
        raise ValueError('unknown sound event: ' + spec['sound'])
    code, xml, css, operations = generate(spec)
    n = names(spec)
    targets = [o['keyvalues']['targetname'] for o in operations] + [n['script'] + '_script']
    if any(m.entities(targetname=t) for t in targets):
        raise ValueError('controllable name is already attached or a generated targetname is occupied')
    script = script_file(spec, n)
    ids = [project._apply(m, x, refs)['id'] for x in operations]
    logs = []
    if not PREVIEW.get():
        logs.append(mechanics.ensure_hud(project, n['hud'], xml, css, write=lambda *a: write_asset(*a)))
        result = scripting.script_write(project, script, code)
        if not result['ok']:
            raise ValueError(f'control script compilation failed: {result.get("log")}')
        logs.append(result['log'])
    node = m.new_entity('point_script', {'targetname': n['script'] + '_script',
                      'cs_script': f'maps/scripts/mcp/{script}.vjs'}, spec['origin'],
                      defaults=project.catalog.defaults('point_script'))
    node.set('cs2map_control', 'string', json.dumps(spec, separators=(',', ':')))
    m.attach(node)
    if op.get('ref'):
        refs[op['ref']] = node['nodeID']
    return {'op': 'add_controllable', 'id': node['nodeID'], 'name': spec['name'], 'kind': spec['kind'], 'entities': n,
            'entity_ids': ids, 'origin': spec['origin'], 'bounds': spec.get('bounds'),
            'assets_written': not PREVIEW.get(), 'logs': logs}


def map_edit_wrapper(project, original):
    def edit(name, operations, dry_run=False, force=False):
        if not isinstance(operations, list) or not any(isinstance(o, dict) and o.get('op') == 'add_controllable' for o in operations):
            return original(name, operations, dry_run, force)
        with project._lock:
            if not dry_run:
                project._check_hammer(name, force)
            token = PREVIEW.set(True)
            try:
                preview = original(name, operations, True, force)
            finally:
                PREVIEW.reset(token)
            if dry_run:
                return preview
            return original(name, operations, False, force)
    return edit


def check_control(project, m):
    problems = []
    for node in m.entities(classname='point_script'):
        if not node.get('cs2map_control'):
            continue
        try:
            spec = validate(json.loads(node['cs2map_control']))
            _, _, _, ops = generate(spec)
            for op in ops:
                found = m.entities(targetname=op['keyvalues']['targetname'])
                if len(found) != 1 or m.classname(found[0]) != op['classname']:
                    raise ValueError('missing/duplicate controllable entity: ' + op['keyvalues']['targetname'])
            n = names(spec)
            # The script file the entity names (maps before 07.10: the plain name; newer: name + digest).
            rel_script = str(node['entity_properties'].get('cs_script') or f'maps/scripts/mcp/{n["script"]}.vjs')
            for rel in [rel_script + '_c', f'panorama/layout/custom_game/{n["hud"]}.vxml_c']:
                if not (project.s.game_addon / rel).is_file():
                    raise ValueError('compiled control resource missing: ' + rel)
            source = project.s.content_addon / rel_script.replace('.vjs', '.js')
            recorded = mechanics.read_marker(source.read_text(encoding='utf-8'), MARKER) if source.is_file() else None
            if recorded is not None and recorded != spec:
                problems.append(checks.problem('warning', 'control_script_differs', node['nodeID'],
                    f'the script {n["script"]} was built from another definition (another map reused the name)',
                    'Rebuild this map from its batch (map_create overwrite + map_edit) or use a new name.'))
        except (ValueError, KeyError, TypeError) as exc:
            problems.append(checks.problem('error', 'control_resource', node['nodeID'], str(exc), 'Recreate the controllable and compile its resources.'))
    return problems


def build_assets(project):
    """Same Blender model_build driver, with D-owned destination (core has no namespace option)."""
    output = []
    for name, color, metal in [('shell', [28, 44, 60], .6), ('accent', [235, 155, 38], .25),
                               ('rubber', [18, 20, 24], 0), ('screen', [37, 174, 205], .15)]:
        made = materials.create(project.s, 'd8_' + name, pattern={'pattern': 'solid', 'color': color, 'size': 64},
                                roughness=.4, metalness=metal, surface='metal' if metal else 'plastic', folder='mcp_d')
        if not made.get('ok', True):
            raise ValueError(f'control material build failed: {made}')
    for name in ('drone', 'remote', 'rotor'):
        folder = ensure_inside(project.s.content_addon / 'models/mcp_d' / name, project.s.content_addon)
        folder.mkdir(parents=True, exist_ok=True)
        # Sources are retained, while driver configuration is disposable.
        source = folder / 'sources' / str(time.time_ns())
        source.mkdir(parents=True)
        shutil.copy2(DATA / 'models.py', source / 'script.py')
        fbx = source / (name + '.fbx')
        tmp_root = PROJECT_ROOT / '.local/tmp'
        tmp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='d8-model-', dir=tmp_root) as tmp:
            params = Path(tmp) / 'params.json'
            params.write_text(json.dumps({'part': name}), encoding='utf-8')
            job = blender_run.start(project.s, project.jobs, models.BLENDER_DIR / 'driver.py',
                                    ['build', str(source / 'script.py'), str(fbx), 'hull', str(params)], timeout=180)
            state = project.job_status(job.id, 50, 0)
            while state['state'] == 'running':
                state = project.job_status(job.id, 50, 0)
        meta = state.get('result', {})
        if state['state'] != 'succeeded' or not meta.get('ok') or not fbx.is_file():
            raise ValueError(f'control model Blender failed: {state["log"]}')
        remaps = [{'from': x['token'] + '.vmat', 'to': asset_path(project, x['material'], '.vmat', 'materials')}
                  for x in meta['materials']]
        children = [{'_class': 'RenderMeshList', 'children': [{'_class': 'RenderMeshFile', 'name': name,
                    'filename': project.s.addon_relative(fbx), 'import_scale': .0254,
                    'import_filter': {'exclude_by_default': False, 'exception_list': []}}]},
                    {'_class': 'MaterialGroupList', 'children': [{'_class': 'DefaultMaterialGroup', 'remaps': remaps,
                    'use_global_default': False, 'global_default_material': ''}]},
                    {'_class': 'PhysicsShapeList', 'children': [{'_class': 'PhysicsHullFile',
                    'filename': project.s.addon_relative(source / f), 'import_scale': .0254,
                    'surface_prop': 'metal', 'collision_prop': 'default',
                    'import_filter': {'exclude_by_default': False, 'exception_list': []}} for f in meta['collision_files']]}]
        if name == 'drone':
            # Without an explicit mass the compiled body reports 0 kg; add_controllable sets the real mass at spawn.
            children.append({'_class': 'PhysicsBodyMarkupList', 'children': [{'_class': 'PhysicsBodyMarkup',
                             'target_body': '', 'mass_override': 8.0, 'inertia_scale': 1.0}]})
            # Checked in CS2: without prop_data damage scales a prop takes no bullet damage at all
            # (RemoveHealth still works). Native health stays huge; the script counts damage.
            children.append({'_class': 'GameDataList', 'children': [{'_class': 'GenericGameData', 'game_class': 'prop_data',
                             'game_keys': {'allowstatic': True, 'spawn_motion_disabled': False, 'physicsmode': 1,
                                           'health': NATIVE_HEALTH, 'dmg.bullets': 1.0, 'dmg.club': 1.0,
                                           'dmg.explosive': 1.0}}]})
        doc = {'rootNode': {'_class': 'RootNode', 'children': children, 'model_archetype': '',
                           'primary_associated_entity': '', 'anim_graph_name': ''}}
        vmdl = folder / (name + '.vmdl')
        write_asset(project, vmdl, kv3.dumps(doc, header=kv3.HEADER_MODELDOC))
        compiled = models._compile_model(project, vmdl, [])
        meta['model'] = project.s.addon_relative(vmdl)
        write_asset(project, folder / 'model.json', json.dumps(meta, indent=2))
        output.append({'model': meta['model'], 'bounds': meta['bounds'], 'tris': meta['tris'],
                       'ok': compiled['ok'], 'log': compiled['log']})
        if not compiled['ok']:
            raise ValueError(f'control model compilation failed: {compiled}')
    return {'ok': True, 'models': output}
