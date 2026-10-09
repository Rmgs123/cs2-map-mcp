"""Stationary, player-operated turrets on the shared controllable registry."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from . import blender_run, control, fire as fire_engine, kv3, layout, materials, mechanics, models, sound
from .config import PROJECT_ROOT, ensure_inside
from .ext.models import asset_path

DATA = Path(__file__).parent / 'data' / 'turrets'
BASE = 'models/mcp_t/turret_base/turret_base.vmdl'
PLATFORM = 'models/mcp_t/turret_platform/turret_platform.vmdl'
BARREL = 'models/mcp_t/turret_barrel/turret_barrel.vmdl'
ASSET_VERSION = 1
FIELDS = frozenset({'limits', 'turn_rate', 'base_model', 'platform_model', 'barrel_model', 'fire', 'heat'})


def _fire(value):
    """Translate the turret-friendly rate/sound spelling to the shared fire schema."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError('fire must be an object')
    allowed = {'rate', 'damage', 'spread', 'sound', 'magazine', 'reload', 'range', 'penetration', 'thickness',
               'wall_loss', 'beam', 'flash', 'recoil', 'knockback', 'impact', 'charge', 'cooldown', 'pellets',
               'charge_sound', 'charge_effect', 'empty_sound', 'reload_sound', 'shake', 'kickback'}
    if set(value) - allowed:
        raise ValueError(f'fire: unknown fields {sorted(set(value) - allowed)}')
    raw = dict(value)
    if 'rate' in raw:
        if 'cooldown' in raw:
            raise ValueError('fire.rate and fire.cooldown are mutually exclusive')
        raw['cooldown'] = raw.pop('rate')
    raw.setdefault('charge', 0)
    raw.setdefault('cooldown', .1)
    raw.setdefault('damage', 18)
    raw.setdefault('spread', 1.5)
    raw.setdefault('recoil', 0)
    raw.setdefault('magazine', 250)
    raw.setdefault('reload', .1)
    raw.setdefault('sound', 'mcp.t9_shot')
    raw.setdefault('impact', True)
    raw.setdefault('beam', {'color': [255, 150, 55], 'width': 1, 'life': .15})
    # fire.validate serializes the absent charge effect as null internally, while its
    # input contract uses false. Normalize a recorded definition before check_map revalidates it.
    if raw.get('charge_effect') is None:
        raw.pop('charge_effect', None)
    if isinstance(raw.get('beam'), dict):
        raw['beam'] = {k: v for k, v in raw['beam'].items() if k not in ('scale', 'segment')}
    if isinstance(raw.get('flash'), dict) and raw['flash'].get('effect') is None:
        raw['flash'] = {**raw['flash'], 'effect': False}
    return fire_engine.validate(raw)


def validate(spec, m=None):
    origin = spec.get('origin', [0, 0, 8])
    if isinstance(origin, dict):
        if m is None:
            raise ValueError('relative origin needs map layout')
        origin = layout.point(m, origin)[0]
    spec['origin'] = mechanics.vector(origin, 'origin')
    spec['yaw'] = mechanics.number(spec.get('yaw', 0), 'yaw', -360, 360)
    spec['reach'] = mechanics.number(spec.get('reach', 96), 'reach', 16, 160)
    spec['turn_rate'] = mechanics.number(spec.get('turn_rate', 120), 'turn_rate', 1, 720)
    limits = spec.get('limits', {'yaw': [-60, 60], 'pitch': [-10, 30]})
    mechanics.keys(limits, {'yaw', 'pitch'}, 'limits')
    out_limits = {}
    for key, lo, hi in (('yaw', -180, 180), ('pitch', -89, 89)):
        pair = limits.get(key)
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(f'limits.{key} must be [min,max]')
        pair = [mechanics.number(v, f'limits.{key}', lo, hi) for v in pair]
        if pair[0] >= pair[1]:
            raise ValueError(f'limits.{key} must be increasing')
        out_limits[key] = pair
    spec['limits'] = out_limits
    spec['base_model'] = mechanics.model_path(spec.get('base_model', BASE), 'base_model')
    spec['platform_model'] = mechanics.model_path(spec.get('platform_model', PLATFORM), 'platform_model')
    spec['barrel_model'] = mechanics.model_path(spec.get('barrel_model', BARREL), 'barrel_model')
    fire = spec.get('fire', {})
    if not isinstance(fire, dict):
        raise ValueError('fire must be an object')
    fire = dict(fire)
    inline = {k: fire.pop(k) for k in ('heat', 'cool') if k in fire}
    if inline and 'heat' in spec:
        raise ValueError('use fire.heat/fire.cool or heat, not both')
    heat = spec.get('heat', {'gain': inline.get('heat', .06), 'cool': inline.get('cool', .35)})
    mechanics.keys(heat, {'gain', 'cool'}, 'heat')
    spec['heat'] = {'gain': mechanics.number(heat.get('gain', .06), 'heat.gain', .001, 1),
                    'cool': mechanics.number(heat.get('cool', .35), 'heat.cool', .001, 10)}
    spec['fire'] = _fire(fire)
    return mechanics.validate_health(spec, 200, native=True)


def names(spec):
    p = 't9_' + spec['name']
    f = fire_engine.names(p, spec['fire'])
    f['shot'] = [p + '_shot' + str(i) for i in range(len(spec['fire'].get('sound', [])))]
    if spec['fire'].get('impact'):
        f['impact'] = p + '_impact'
    return {'body': p + '_body', 'platform': p + '_platform', 'barrel': p + '_barrel', **f}


def entities(spec, n):
    o = spec['origin']
    seats = mechanics.seat_entities(n, o)
    ops = [seats['filter'], mechanics.entity('prop_physics_override', n['body'], o,
        {'model': spec['base_model'], 'health': mechanics.NATIVE_HEALTH, 'spawnflags': 0,
         'skipPreSettle': 1, 'damagefilter': n['filter']}, [0, spec['yaw'], 0]),
        seats['decoy'],
        mechanics.entity('prop_dynamic', n['platform'], [o[0], o[1], o[2] + 20],
            {'model': spec['platform_model'], 'solid': 6, 'parentname': n['body']}, [0, spec['yaw'], 0]),
        mechanics.entity('prop_dynamic', n['barrel'], [o[0], o[1], o[2] + 34],
            {'model': spec['barrel_model'], 'solid': 6, 'parentname': n['platform']}, [0, spec['yaw'], 0]),
        mechanics.entity('custom_hud_layout', n['hud'], o,
            {'layout': f'panorama/layout/custom_game/{n["hud"]}.vxml'})]
    for cls, key, kv, at in fire_engine.entity_defs(n, spec['fire'], o):
        if key == 'hud':
            # The shared control HUD carries the heat strip; the fire HUD is only an engine entity.
            continue
        ops.append(mechanics.entity(cls, kv.pop('targetname', n.get(key, key)), at, kv))
    for i, event in enumerate(spec['fire'].get('sound', [])):
        ops.append(mechanics.entity('point_soundevent', n['shot'][i], o,
            {'soundName': event, 'sourceEntityName': n['body'], 'startOnSpawn': 0}))
    if spec['fire'].get('impact'):
        ops.append(mechanics.entity('info_particle_system', n['impact'], [o[0], o[1], o[2] - 4096],
            {'effect_name': fire_engine.FLASH_EFFECT, 'start_active': 0}))
    ops += mechanics.effect_entities(n, o, n['body'])
    return ops


def _build_part(project, part, signature):
    folder = ensure_inside(project.s.content_addon / 'models/mcp_t' / f'turret_{part}', project.s.content_addon)
    source = folder / 'sources' / signature[:12]; source.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DATA / 'models.py', source / 'script.py')
    fbx = source / f'turret_{part}.fbx'
    with tempfile.TemporaryDirectory(prefix='t9-model-', dir=PROJECT_ROOT / '.local' / 'tmp') as tmp:
        params = Path(tmp) / 'params.json'; params.write_text(json.dumps({'part': part}), encoding='utf-8')
        job = blender_run.start(project.s, project.jobs, models.BLENDER_DIR / 'driver.py',
            ['build', str(source / 'script.py'), str(fbx), 'hull', str(params)], timeout=180)
        state = project.job_status(job.id, 50, 0)
        while state['state'] == 'running': state = project.job_status(job.id, 50, 0)
    meta = state.get('result') or {}
    if state['state'] != 'succeeded' or not meta.get('ok') or not fbx.is_file():
        raise ValueError('turret Blender build failed: ' + state.get('log', ''))
    remaps = [{'from': x['token'] + '.vmat', 'to': asset_path(project, x['material'], '.vmat', 'materials')}
              for x in meta.get('materials', [])]
    children = [
        {'_class': 'RenderMeshList', 'children': [{'_class': 'RenderMeshFile', 'name': part,
            'filename': project.s.addon_relative(fbx), 'import_scale': .0254,
            'import_filter': {'exclude_by_default': False, 'exception_list': []}}]},
        {'_class': 'MaterialGroupList', 'children': [{'_class': 'DefaultMaterialGroup', 'remaps': remaps,
            'use_global_default': False, 'global_default_material': ''}]},
        {'_class': 'PhysicsShapeList', 'children': [{'_class': 'PhysicsHullFile',
            'filename': project.s.addon_relative(source / f), 'import_scale': .0254,
            'surface_prop': 'metal', 'collision_prop': 'default',
            'import_filter': {'exclude_by_default': False, 'exception_list': []}} for f in meta.get('collision_files', [])]}]
    if part == 'base':
        children += [
            {'_class': 'PhysicsBodyMarkupList', 'children': [{'_class': 'PhysicsBodyMarkup',
                'target_body': '', 'mass_override': 500.0, 'inertia_scale': 1.0}]},
            {'_class': 'GameDataList', 'children': [{'_class': 'GenericGameData', 'game_class': 'prop_data',
                'game_keys': {'allowstatic': True, 'spawn_motion_disabled': True, 'physicsmode': 1,
                    'health': mechanics.NATIVE_HEALTH, 'dmg.bullets': 1.0, 'dmg.club': 1.0,
                    'dmg.explosive': 1.0}}]}]
    vmdl = folder / f'turret_{part}.vmdl'
    mechanics.write_asset(project, vmdl, kv3.dumps({'rootNode': {'_class': 'RootNode', 'children': children,
        'model_archetype': '', 'primary_associated_entity': '', 'anim_graph_name': ''}}, header=kv3.HEADER_MODELDOC))
    compiled = models._compile_model(project, vmdl, [])
    if not compiled.get('ok'):
        raise ValueError('turret model compile failed: ' + compiled.get('log', ''))
    mechanics.write_asset(project, folder / 'model.json', json.dumps({**meta, 'compile': compiled}, indent=2))


def ensure_assets(project):
    manifest = project.s.content_addon / 'models/mcp_t/assets.json'
    signature = hashlib.sha256((DATA / 'models.py').read_bytes()).hexdigest()
    current = {'version': ASSET_VERSION, 'source': signature}
    try: cached = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError): cached = None
    required = [BASE, PLATFORM, BARREL, 'sounds/mcp_t/t9_shot.vsnd']
    required += [f'materials/mcp_t/t9_{k}/t9_{k}.vmat' for k in ('dark', 'metal', 'accent', 'glass')]
    if cached == current and all((project.s.game_addon / (p + '_c')).is_file() for p in required) and sound.event_exists(project, 'mcp.t9_shot'):
        return
    for k, color, metal in [('dark', [12, 20, 24], .7), ('metal', [95, 115, 120], .85),
                             ('accent', [220, 65, 18], .25)]:
        made = materials.create(project.s, 't9_' + k, pattern={'pattern': 'solid', 'color': color, 'size': 64},
                                roughness=.35, metalness=metal, surface='metal', folder='mcp_t')
        if not made.get('ok', True): raise ValueError('turret material failed: ' + str(made))
    materials.create(project.s, 't9_glass', kind='glass', tint=[30, 130, 170], opacity=.38, folder='mcp_t')
    for part in ('base', 'platform', 'barrel'):
        _build_part(project, part, signature)
    samples, rate, loop = sound.synthesize('zap', .22, {'gain': .45, 'frequency': 900, 'seed': 9, 'loop': False})
    wav = project.s.content_addon / 'sounds/mcp_t/t9_shot.wav'; wav.parent.mkdir(parents=True, exist_ok=True)
    if wav.exists(): sound._backup(project, wav)
    wav.write_bytes(sound.wav_bytes(samples, rate, loop)); compiled = sound._compile(project, wav)
    if not compiled.get('ok'): raise ValueError('turret sound compile failed: ' + str(compiled))
    sound.sound_event(project, 't9_shot', 'oneshot', sound='sounds/mcp_t/t9_shot.vsnd', volume=.65, distance=1200)
    mechanics.write_asset(project, manifest, json.dumps(current))


def prepare(project, spec):
    if control.PREVIEW.get(): return
    ensure_assets(project)
    # The shared tracer is a core resource. Never generate it in another agent's
    # asset folder; the accepted mechanics/weapon preparation supplies it.
    if spec['fire']['beam'] or spec['fire'].get('flash', {}).get('light'):
        beam = fire_engine.BEAM_MODEL + '_c'
        if not (project.s.game_addon / beam).is_file() and not project.game.exists(beam):
            raise ValueError('shared tracer model missing; run weapon_prepare before adding a turret')
    for model in (spec['base_model'], spec['platform_model'], spec['barrel_model']):
        asset_path(project, model)
        if not (project.s.game_addon / (model + '_c')).is_file() and not project.game.exists(model + '_c'):
            raise ValueError('compiled turret model missing: ' + model)
    for event in spec['fire'].get('sound', []):
        if not sound.event_exists(project, event): raise ValueError('unknown turret fire event: ' + event)


def register():
    mechanics.register_fragment('turret_heat', DATA / 'heat.js')
    return control.register_kind('turret', validate, ['core', 'hud', 'camera', 'seat', 'health', 'fire', 'turret_heat'],
        entities, DATA / 'turret.js', fields=FIELDS, prefix='t9', script_prefix='t9_turret',
        hud_prefix='mcp_t_turret', names=names, prepare=prepare,
        camera={'forward': 44, 'up': 16, 'chase': False}, hud=(DATA / 'hud.xml', DATA / 'hud.css'),
        tag='T_TURRET', title='AUTOCANNON / TURRET', hint='Mouse: aim | Attack: fire | E: exit')


register()
