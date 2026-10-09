"""Mini-games on the shared mechanics fragments.

The operation deliberately stays in map_edit: a model can author a gallery, race,
or collection game in one batch, while the generated point_script owns the small
amount of runtime state (score, timer and session record).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from contextvars import ContextVar
from pathlib import Path

from . import checks, kv3, layout, mechanics, models, scripting, materials, blender_run, sound
from .config import PROJECT_ROOT, ensure_inside
from .project import OpError

DATA = Path(__file__).parent / 'data' / 'minigames'
MARKER = '// CS2MAP_MINIGAME '
PREVIEW = ContextVar('minigames_preview', default=False)
NAME = re.compile(r'[a-z][a-z0-9_]{0,39}$')
MODEL_TARGET = 'models/mcp_m/m9_target/m9_target.vmdl'
MODEL_RING = 'models/mcp_m/m9_ring/m9_ring.vmdl'
MODEL_COLLECT = 'models/mcp_m/m9_collect/m9_collect.vmdl'
MODEL_BUTTON = 'models/mcp_m/m9_button/m9_button.vmdl'
MODEL_STAND = 'models/mcp_m/m9_stand/m9_stand.vmdl'
KINDS = {'gallery', 'race', 'collect'}


def _keys(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise OpError(f'{label} must be an object with fields {sorted(allowed)}')


def _num(value, label, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise OpError(f'{label} must be a finite number in {lo}..{hi}')
    return float(value)


def _point(m, value, label):
    # The shared layout preprocessor resolves room-relative points before an
    # extension sees the operation.  Accept that already-world-space form.
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return [_num(x, label, -32768, 32768) for x in value]
    if not isinstance(value, dict):
        raise OpError(f'{label} must be a room-relative point {{room, at, z}}')
    try:
        return layout.point(m, value)[0]
    except (ValueError, KeyError, TypeError, AttributeError, OpError) as exc:
        raise OpError(f'{label}: {exc}') from None


def validate(op, m=None):
    if not isinstance(op, dict):
        raise OpError('add_minigame must be an object')
    if 'start' in op and 'origin' not in op:            # the old name of the start button point
        op = {**{k: v for k, v in op.items() if k != 'start'}, 'origin': op['start']}
    allowed = {'op', 'name', 'kind', 'origin', 'targets', 'items', 'time', 'popup', 'vehicle', 'radius', 'hud', 'ref'}
    _keys(op, allowed, 'add_minigame')
    name = op.get('name')
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise OpError('name must be lowercase letters, digits and underscores (1..40 chars)')
    kind = op.get('kind')
    if kind not in KINDS:
        raise OpError('kind must be gallery, race or collect')
    start = op.get('origin')
    if start is None:
        raise OpError('origin (the start button point) is required')
    start_world = _point(m, start, 'origin') if m is not None else None
    try:
        hud = mechanics.validate_hud(op.get('hud'))
    except ValueError as e:
        raise OpError(str(e)) from None
    if kind == 'gallery':
        raw = op.get('targets')
        field = 'targets'
    elif kind == 'race':
        raw = op.get('targets')
        field = 'targets'
    else:
        raw = op.get('items')
        field = 'items'
    if not isinstance(raw, list) or not 1 <= len(raw) <= 64:
        raise OpError(f'{field} must contain 1..64 points')
    points = []
    for i, value in enumerate(raw):
        points.append(_point(m, value, f'{field}[{i}]') if m is not None else None)
    if kind == 'collect' and 'targets' in op or kind != 'collect' and 'items' in op:
        if op.get('targets' if kind == 'collect' else 'items'):
            raise OpError('use items for collect and targets for gallery/race')
    seconds = _num(op.get('time', 30), 'time', 1, 600)
    popup = op.get('popup', [1.0, 2.5])
    if kind == 'gallery':
        if not isinstance(popup, (list, tuple)) or len(popup) != 2:
            raise OpError('popup must be [minimum, maximum] seconds')
        popup = [_num(popup[0], 'popup[0]', .1, 60), _num(popup[1], 'popup[1]', .1, 60)]
        if popup[0] > popup[1]:
            raise OpError('popup minimum must not exceed maximum')
    else:
        if op.get('popup') is not None:
            raise OpError('popup is only valid for gallery')
        popup = None
    vehicle = op.get('vehicle')
    if kind != 'race' and vehicle is not None:
        raise OpError('vehicle is only valid for race')
    if vehicle is not None and (not isinstance(vehicle, str) or not re.fullmatch(r'[A-Za-z0-9_]{1,64}', vehicle)):
        raise OpError('vehicle must be an entity name')
    radius = _num(op.get('radius', 56), 'radius', 8, 256)
    return {'op': 'add_minigame', 'name': name, 'kind': kind, 'start': start, 'hud': hud,
            'start_world': start_world, 'targets': raw if kind != 'collect' else [],
            'items': raw if kind == 'collect' else [], 'points': points, 'time': seconds,
            'popup': popup, 'vehicle': vehicle, 'radius': radius, **({'ref': op['ref']} if op.get('ref') else {})}


def _names(spec):
    p = 'm9_' + spec['name']
    count = len(spec['points'])
    return {'start': p + '_start', 'script': p, 'hud': 'mcp_m_' + spec['name'],
            'targets': [p + ('_target_' if spec['kind'] == 'gallery' else '_ring_') + str(i) for i in range(count)],
            'items': [p + '_item_' + str(i) for i in range(count)],
            'hinges': [p + '_hinge_' + str(i) for i in range(count)],
            'state': p + '_state', 'sounds': {k:p+'_'+k+'_sound' for k in ('start','hit','checkpoint','finish')}}


def _entities(spec, names):
    origin = spec['start_world']
    # A physics override consumes the authored hull reliably in server traces;
    # the point_script still treats it as a static button on the floor.
    out = [mechanics.entity('prop_physics_override', names['start'], origin,
                            {'model': MODEL_BUTTON, 'health': 100000,
                             'spawnflags': 0, 'skipPreSettle': 1}, [0, 0, 0])]
    if spec['kind'] == 'gallery':
        for i, (n, at) in enumerate(zip(names['targets'], spec['points'])):
            yaw = math.degrees(math.atan2(origin[1]-at[1], origin[0]-at[0])) + 90
            pivot = [at[0], at[1], at[2]+10]
            out.append(mechanics.entity('prop_dynamic', n+'_stand', at, {'model': MODEL_STAND, 'solid':6}, [0,yaw,0]))
            out.append(mechanics.entity('prop_physics_override', n, pivot,
                                        {'model': MODEL_TARGET, 'health': 100000, 'spawnflags': 0,
                                         'skipPreSettle': 1}, [0, yaw, 0]))
            axis = [pivot[0]+32*math.cos(math.radians(yaw)), pivot[1]+32*math.sin(math.radians(yaw)), pivot[2]]
            out.append(mechanics.entity('phys_hinge', names['hinges'][i], pivot,
                {'attach1':n, 'hingeaxis':' '.join(map(str,axis)), 'min_rotation':0, 'max_rotation':90,
                 'hingefriction':1, 'motorfrequency':10, 'motordampingratio':1}))
    elif spec['kind'] == 'race':
        for n, at in zip(names['targets'], spec['points']):
            out.append(mechanics.entity('prop_dynamic', n, at,
                                        {'model': MODEL_RING, 'solid': 0, 'disableshadows': 1}, [0, 0, 0]))
    else:
        for n, at in zip(names['items'], spec['points']):
            out.append(mechanics.entity('prop_dynamic', n, at,
                                        {'model': MODEL_COLLECT, 'solid': 0, 'disableshadows': 1}, [0, 0, 0]))
    out.append(mechanics.entity('custom_hud_layout', names['hud'], origin,
                                {'layout': f'panorama/layout/custom_game/{names["hud"]}.vxml'}))
    for key, n in names['sounds'].items():
        out.append(mechanics.entity('point_soundevent', n, origin,
            {'soundName':'mcp.m9_'+key, 'startOnSpawn':0}))
    return out


def _config(spec, names):
    return {'name': spec['name'], 'kind': spec['kind'], 'time': spec['time'], 'popup': spec['popup'],
            'vehicle': spec['vehicle'], 'radius': spec['radius'], 'points': spec['points'],
            'entities': names, 'state': names['state']}


def generate(spec):
    """Return assembled script, HUD files, and entity operations for a validated spec."""
    names = _names(spec)
    config = _config(spec, names)
    marker = {key: spec[key] for key in ('op', 'name', 'kind', 'start', 'targets', 'items', 'time',
                                         'popup', 'vehicle', 'radius', 'ref') if key in spec}
    code = mechanics.build_script(names['script'], ['core', 'hud', 'health', 'minigame_game'], config,
                                  marker=MARKER, marker_config=marker)
    xml = (DATA / 'hud.xml').read_text(encoding='utf-8').replace('HUDNAME', names['hud'])
    title = (spec.get('hud') or {}).get('title') or {'gallery': 'SHOOTING GALLERY', 'race': 'CHECKPOINT RACE',
                                                       'collect': 'COLLECT RUN'}[spec['kind']]
    xml = xml.replace('HUDTITLE', title.replace('&', '&amp;').replace('<', '&lt;').replace('"', '&quot;'))
    from . import ui
    css = (DATA / 'hud.css').read_text(encoding='utf-8').replace('HUDNAME', names['hud'])
    css += ui.override({'panel': f'#{names["hud"]}_panel', 'title': f'#{names["hud"]}_title',
                        'hint': f'#{names["hud"]}_panel .Hint'}, spec.get('hud') or {}, 'top')
    return code, xml, css, _entities(spec, names), names


def _ensure_part(project, part, script, material_specs, physics=False):
    folder = ensure_inside(project.s.content_addon / 'models' / 'mcp_m' / ('m9_' + part), project.s.content_addon)
    vmdl = folder / f'm9_{part}.vmdl'
    compiled = project.s.game_addon / (project.s.addon_relative(vmdl) + '_c')
    signature = hashlib.sha256((DATA/'models.py').read_bytes()+json.dumps([part,material_specs,physics,2]).encode()).hexdigest()
    if compiled.is_file():
        # A button needs an authored hull so the E trace can actually hit the
        # visible model.  Rebuild an older cached asset when its metadata says
        # it was emitted without physics; other matching assets stay cached.
        meta = folder / 'model.json'
        try:
            cached = json.loads(meta.read_text(encoding='utf-8'))
            cached_physics = bool(cached.get('physics'))
        except (OSError, ValueError, TypeError):
            cached_physics = False
            cached = {}
        if cached_physics == physics and cached.get('signature') == signature:
            return {'model': project.s.addon_relative(vmdl), 'compiled': str(compiled), 'skipped': True}
    folder.mkdir(parents=True, exist_ok=True)
    vmats = []
    for name, color, rough, metal in material_specs:
        made = materials.create(project.s, 'm9_' + name, pattern={'pattern': 'solid', 'color': color, 'size': 64},
                                roughness=rough, metalness=metal, surface='metal', folder='mcp_m',
                                **({'kind':'emissive','glow':2} if name in ('ring_blue','collect_gold') else {}))
        vmats.append(made.get('material', f'materials/mcp_m/m9_{name}/m9_{name}.vmat'))
    source = folder / 'sources'
    source.mkdir(exist_ok=True)
    script_path = source / 'models.py'
    shutil.copy2(DATA / 'models.py', script_path)
    params = PROJECT_ROOT / '.local' / 'tmp' / f'm9_{part}_params.json'
    params.parent.mkdir(parents=True, exist_ok=True)
    params.write_text(json.dumps({'part': part}), encoding='utf-8')
    fbx = source / f'm9_{part}.fbx'
    try:
        job = blender_run.start(project.s, project.jobs, DATA.parent.parent / 'blender' / 'driver.py',
                                ['build', str(script_path), str(fbx), 'hull' if physics else 'none', str(params)], timeout=180)
        state = project.jobs.wait(job.id, 190)
    finally:
        params.unlink(missing_ok=True)
    if state.state != 'succeeded' or not state.result.get('ok') or not fbx.is_file():
        raise OpError(f'minigame {part} model build failed; see {state.log}')
    result = dict(state.result)
    fbx_rel = project.s.addon_relative(fbx)
    coll = [project.s.addon_relative(source / x) for x in result.get('collision_files', [])]
    children = [{'_class': 'RenderMeshList', 'children': [{'_class': 'RenderMeshFile', 'name': f'm9_{part}',
        'filename': fbx_rel, 'import_scale': .0254,
        'import_filter': {'exclude_by_default': False, 'exception_list': []}}]}]
    remaps = []
    for rec in result.get('materials', []):
        target = rec.get('material') or next((x for x in vmats if Path(x).stem == rec.get('name')), vmats[0])
        remaps.append({'from': f"{rec['token']}.vmat", 'to': target})
    children.append({'_class': 'MaterialGroupList', 'children': [{'_class': 'DefaultMaterialGroup', 'remaps': remaps,
        'use_global_default': False, 'global_default_material': ''}]})
    if coll:
        children.append({'_class': 'PhysicsShapeList', 'children': [{'_class': 'PhysicsHullFile', 'name': f'm9_{part}_col_{i}',
            'filename': x, 'surface_prop': 'metal', 'collision_prop': 'default', 'import_scale': .0254,
            'import_filter': {'exclude_by_default': False, 'exception_list': []}} for i, x in enumerate(coll)]})
    if physics:
        children += [{'_class': 'PhysicsBodyMarkupList', 'children': [{'_class': 'PhysicsBodyMarkup', 'target_body': '',
            'mass_override': 8.0, 'inertia_scale': 1.0}]},
            {'_class': 'GameDataList', 'children': [{'_class': 'GenericGameData', 'game_class': 'prop_data',
                'game_keys': {'allowstatic': True, 'spawn_motion_disabled': False, 'physicsmode': 1,
                              'health': 100000, 'dmg.bullets': 1.0, 'dmg.club': 1.0, 'dmg.explosive': 1.0}}]}]
    doc = {'rootNode': {'_class': 'RootNode', 'children': children, 'model_archetype': '',
                        'primary_associated_entity': '', 'anim_graph_name': ''}}
    mechanics.write_asset(project, vmdl, kv3.dumps(doc, header=kv3.HEADER_MODELDOC))
    compiled_result = models._compile_model(project, vmdl, [])
    if not compiled_result.get('ok'):
        raise OpError(f'minigame {part} resource compile failed: {compiled_result.get("log")}')
    (folder / 'model.json').write_text(json.dumps({**result, 'model': project.s.addon_relative(vmdl),
                                                    'physics': physics, 'signature':signature}, indent=2), encoding='utf-8')
    return {'model': project.s.addon_relative(vmdl), 'compiled': compiled_result.get('output')}


def ensure_assets(project):
    made = []
    # Parts share the accent materials used by the authored Blender source.
    # Keep them in the M-owned folder even when only one kind is requested.
    made.append(_ensure_part(project, 'button', 'models.py', [('button', [32, 110, 210], .45, .25),
                                                              ('collect_gold', [250, 185, 35], .25, .55)], True))
    made.append(_ensure_part(project, 'target', 'models.py', [('target_red', [175, 38, 32], .5, .1),
                                                              ('target_white', [235, 230, 218], .7, .0)], True))
    made.append(_ensure_part(project, 'stand', 'models.py', [('target_red', [175, 38, 32], .5, .1)], True))
    made.append(_ensure_part(project, 'ring', 'models.py', [('ring_blue', [255, 255, 255], .3, .35)]))
    made.append(_ensure_part(project, 'collect', 'models.py', [('collect_gold', [250, 185, 35], .25, .55),
                                                               ('ring_blue', [255, 255, 255], .3, .35)]))
    for key, preset, length, frequency in [('start','beep',.18,700),('hit','click',.08,1100),
                                          ('checkpoint','beep',.14,950),('finish','whoosh',.45,700)]:
        rel = f'sounds/mcp_m/m9_{key}.vsnd'
        if not (project.s.game_addon/(rel+'_c')).is_file():
            samples, rate, loop = sound.synthesize(preset,length,{'gain':.35,'frequency':frequency,'loop':False})
            wav = project.s.content_addon / 'sounds/mcp_m' / f'm9_{key}.wav'
            if wav.exists(): sound._backup(project,wav)
            wav.parent.mkdir(parents=True,exist_ok=True)
            wav.write_bytes(sound.wav_bytes(samples,rate,loop))
            sound._compile(project,wav)
        if not sound.event_exists(project,'mcp.m9_'+key):
            sound.sound_event(project,'m9_'+key,'oneshot',sound=rel,volume=.65,distance=1200)
    return made


def add_minigame(project, m, op, refs):
    spec = validate(op, m)
    names = _names(spec)
    generated = [x['keyvalues']['targetname'] for x in _entities(spec, names)] + [names['script'] + '_script']
    if any(m.entities(targetname=x) for x in generated):
        raise OpError('minigame name or generated targetname is already used in this map')
    if not PREVIEW.get():
        ensure_assets(project)
    code, xml, css, operations, names = generate(spec)
    # The script file is per definition (name + digest): another map's mini-game of the same name keeps its own.
    import hashlib
    script = names['script'] + '_' + hashlib.sha1(code.encode()).hexdigest()[:8]
    ids = []
    for entity_op in operations:
        ids.append(project._apply(m, entity_op, refs)['id'])
    logs = []
    if not PREVIEW.get():
        logs.append(mechanics.ensure_hud(project, names['hud'], xml, css))
        result = scripting.script_write(project, script, code)
        if not result.get('ok'):
            raise OpError(f'minigame script compilation failed: {result.get("log")}')
        logs.append(result.get('log'))
        attached = scripting.add_script(project, m, {'name': script, 'targetname': names['script'] + '_script',
                                                     'origin': spec['start_world'], 'ref': op.get('ref')}, refs)
    else:
        ent = m.new_entity('point_script', {'targetname': names['script'] + '_script',
                                             'cs_script': f'maps/scripts/mcp/{script}.vjs'},
                            spec['start_world'], defaults=project.catalog.defaults('point_script'))
        m.attach(ent)
        if op.get('ref'):
            refs[op['ref']] = ent['nodeID']
        attached = {'id': ent['nodeID'], 'op': 'add_script', 'name': names['script']}
    return {'op': 'add_minigame', 'id': attached['id'], 'name': spec['name'], 'kind': spec['kind'],
            'entities': len(ids), 'script': names['script'], 'hud': names['hud'], 'logs': logs,
            **({'ref': op['ref']} if op.get('ref') else {})}


def map_edit_wrapper(project, original):
    def edit(name, operations, dry_run=False, force=False):
        has = isinstance(operations, list) and any(isinstance(x, dict) and x.get('op') == 'add_minigame' for x in operations)
        if not has:
            return original(name, operations, dry_run, force)
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


def _script_spec(path):
    try:
        first = path.read_text(encoding='utf-8').split('\n', 1)[0]
        if first.startswith(MARKER):
            return validate(json.loads(first[len(MARKER):]), None)
    except (OSError, ValueError, TypeError, OpError):
        return None
    return None


def check_minigames(project, m):
    problems = []
    for node in m.entities(classname='point_script'):
        rel = node['entity_properties'].get('cs_script', '')
        if not re.fullmatch(r'maps/scripts/mcp/m9_[A-Za-z0-9_]+\.vjs', rel):
            continue
        src = project.s.content_addon / Path(rel).with_suffix('.js')
        spec = _script_spec(src) if src.exists() else None
        if not spec:
            problems.append(checks.problem('error', 'minigame_script_missing', node['nodeID'],
                                          f'{rel}: generated mini-game script or marker is missing', 'Recreate the map batch.'))
            continue
        # The marker stores the author's room points; generated entities remain the source of truth for checks.
        name = spec['name']; prefix = 'm9_' + name
        expected = [prefix + '_start']
        suffix = '_target_' if spec['kind'] == 'gallery' else '_ring_' if spec['kind'] == 'race' else '_item_'
        count = len(spec.get('targets') or spec.get('items') or [])
        expected += [prefix + suffix + str(i) for i in range(count)]
        expected += ['mcp_m_' + name]
        for target in expected:
            if len(m.entities(targetname=target)) != 1:
                problems.append(checks.problem('error', 'minigame_entity_missing', node['nodeID'],
                                              f'{name}: expected one entity named {target}', 'Recreate the map batch.'))
    return problems


def register_check():
    checks.register_check('minigames', check_minigames)


# Importing the area module is enough for unit tests and for callers that use
# the Python operation directly; the extension repeats this idempotently.
mechanics.register_fragment('minigame_game', DATA / 'game.js')
