"""One weapon type, `add_weapon`: a stock CS2 weapon under the hood with an own look and an own shot.

The base (any gun, the taser or the knife) keeps everything native: slot, buying/pickup/drop, ammo, the hands and
their animations. On top of it the script gives
  - an own model in the hands, in third person and on the ground: a model rigged on the base's skeleton
    (model_build weapon={base,...}, cs2map.weapon_rig); any other model shows in the world only;
  - a colour, damage, magazine, layered sound, sparks and knockback for the native shot; or
  - the shot engine of fire.py instead of the native shot: hitscan, pellets, charge, wall penetration, glowing
    beams, projectiles (rockets, grenades) with explosions, single/burst/auto, spring recoil, shake, flash;
  - weapons lying in the map (origin) and/or given to players at spawn (give).
The old two ways (add_custom_weapon, add_scripted_weapon) are merged here (decision of the user, 07.10).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import time
import uuid
from pathlib import Path

from . import blender_run, compiler, kv3, materials, mechanics, models, scripting, sound
from . import fire as fire_engine
from .config import PROJECT_ROOT, ensure_inside
from .project import OpError

DATA = Path(__file__).with_name('data') / 'weapons'
mechanics.register_fragment('weapon', DATA / 'weapon.js')
fire_engine.register_user('add_weapon', 'weapon')
CUSTOM_HUD = fire_engine.CUSTOM_HUD
IMPACT = 'particles/ambient_fx/env_sparks_omni.vpcf'
# Stock weapon classes that can carry an own model and shot (grenades and C4 are thrown or planted: not bases).
BASES = {'ak47', 'aug', 'awp', 'bizon', 'cz75a', 'deagle', 'elite', 'famas', 'fiveseven', 'g3sg1', 'galilar',
         'glock', 'hkp2000', 'm249', 'm4a1', 'm4a1_silencer', 'mac10', 'mag7', 'mp5sd', 'mp7', 'mp9', 'negev',
         'nova', 'p250', 'p90', 'revolver', 'sawedoff', 'scar20', 'sg556', 'ssg08', 'tec9', 'ump45',
         'usp_silencer', 'xm1014', 'taser', 'knife', 'knife_t'}
PISTOLS = {'cz75a', 'deagle', 'elite', 'fiveseven', 'glock', 'hkp2000', 'p250', 'revolver', 'tec9', 'usp_silencer'}
RAILGUN_MODEL = 'models/mcp_w/railgun/railgun.vmdl'
ZAPPER_MODEL = 'models/mcp_x/zapper/zapper.vmdl'
FIELDS = {'op', 'name', 'base', 'title', 'origin', 'angles', 'give', 'shop', 'model', 'color', 'fire', 'damage', 'clip',
          'reserve', 'sound', 'impact', 'knockback', 'hud', 'ref'}


def _number(value, field, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OpError(f'{field} must be a number')
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise OpError(f'{field} must be {low}..{high}')
    return value


def _angles(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise OpError('angles must be [pitch,yaw,roll]')
    return [_number(v, 'angles', -360, 360) for v in value]


def _sound_list(value):
    if value is None:
        raise OpError('sound must be an event name or a list of up to 4 event names')
    return fire_engine._events(value, 'sound')


def validate(op) -> dict:
    extra = set(op) - FIELDS
    if extra:
        raise OpError(f'unknown add_weapon fields: {sorted(extra)}')
    name = op.get('name')
    if not isinstance(name, str) or not re.fullmatch('[a-z][a-z0-9_]{0,31}', name):
        raise OpError('name must start with a-z and contain 1..32 lowercase letters, digits, _')
    base = str(op.get('base', '')).lower().removeprefix('weapon_')
    if base not in BASES:
        raise OpError(f'base must be a stock weapon: {", ".join(sorted(BASES))}')
    give = op.get('give')
    if give is not None and give not in ('all', 't', 'ct'):
        raise OpError('give must be "all", "t" or "ct" (players of that team get it at spawn)')
    shop = op.get('shop', False)
    if not isinstance(shop, bool):
        raise OpError('shop is true/false: every base weapon bought (or picked up) becomes this weapon')
    if op.get('origin') is None and give is None and not shop:
        raise OpError('add_weapon needs origin (where it lies), give (who gets it at spawn) and/or shop:true')
    p = f'w9_{name}'
    # The gear slot a gift frees (CSGearSlot): pistols, knives, the taser (none) and primaries.
    slot = 1 if base in PISTOLS else 2 if base.startswith('knife') else -1 if base == 'taser' else 0
    spec = {'name': name, 'base': 'weapon_' + base, 'targetname': f'{p}_weapon', 'give': give, 'shop': shop, 'slot': slot,
            'damage': _number(op.get('damage', 0) or 0, 'damage', 0, 10000) or None,
            'clip': int(_number(op.get('clip', 0), 'clip', 0, 250)) or None,
            'reserve': int(_number(op['reserve'], 'reserve', 0, 1000)) if 'reserve' in op else None,
            'knockback': _number(op.get('knockback', 0), 'knockback', 0, 2000),
            'impact': bool(op.get('impact', False)), 'model': op.get('model') or None,
            'sound': _sound_list(op.get('sound', [])), 'color': None, 'rigged': False,
            'title': str(op.get('title', name.upper()))[:24],
            'fire': fire_engine.validate(op['fire']) if op.get('fire') is not None else None,
            'entities': {'fire': [], 'impact': f'{p}_hit' if op.get('impact') else None}}
    if not isinstance(op.get('impact', False), bool):
        raise OpError('impact must be boolean')
    if spec['damage'] is None and 'damage' in op:
        raise OpError('damage must be > 0 (omit it to keep the stock damage)')
    if 'color' in op:
        color = op['color']
        if not isinstance(color, (list, tuple)) or len(color) != 3:
            raise OpError('color must be [r,g,b] 0..255')
        spec['color'] = [int(_number(c, 'color', 0, 255)) for c in color]
    if spec['model'] is not None and (not isinstance(spec['model'], str) or '..' in spec['model'] or
                                      not re.fullmatch(r'models/[A-Za-z0-9_./-]+\.vmdl', spec['model'])):
        raise OpError('model must be a models/.../*.vmdl path')
    f = spec['fire']
    if f:
        spec['sound'] = f.get('sound', spec['sound'])
        spec['impact'] = f.get('impact', spec['impact'])
        spec['entities']['impact'] = f'{p}_hit' if spec['impact'] else None
        spec['entities'].update(fire_engine.names(p, f))
    if len(spec['sound']) > 4:
        raise OpError('sound must be an event name or a list of up to 4 event names')
    spec['entities']['fire'] = [f'{p}_fire{i}' for i in range(len(spec['sound']))]
    return spec


def _check_assets(project, spec):
    f = spec.get('fire') or {}
    pr = f.get('projectile') or {}
    boom = pr.get('explode') or {}
    events = (*spec['sound'], *f.get('empty_sound', []), *f.get('reload_sound', []), f.get('charge_sound'), boom.get('sound'))
    for event in events:
        if event and not sound.event_exists(project, event):
            raise OpError('unknown sound event: ' + event)
    for particle in (f.get('charge_effect'), (f.get('flash') or {}).get('effect'), pr.get('trail'), boom.get('effect')):
        if particle and not (project.game.exists(particle + '_c') or (project.s.game_addon / (particle + '_c')).is_file()):
            raise OpError('particle resource not found: ' + particle)
    if pr and pr['model'] != fire_engine.ROCKET_MODEL:
        from .ext.models import asset_path
        asset_path(project, pr['model'])


def _rigged(project, model: str, base: str) -> tuple[bool, list[str]]:
    """Is the model built on a weapon skeleton (held in the hands)? A model rigged for another base is placed by
    that base's hand: it works, but sits differently, so it is reported."""
    source = project.s.content_addon / model
    text = source.read_text(encoding='utf-8', errors='replace') if source.is_file() else ''
    if 'is_unified_model = true' not in text:
        return False, [f'{model} is not on a weapon skeleton: it shows on the ground and in others\' hands only; '
                       f'the holder sees the stock {base}. Build it with model_build(weapon={{"base": "{base[7:]}"}})']
    meta = source.with_name('model.json')
    rigged_for = None
    if meta.is_file():
        try:
            rigged_for = (json.loads(meta.read_text(encoding='utf-8')).get('weapon') or {}).get('base')
        except ValueError:
            pass
    if rigged_for and 'weapon_' + rigged_for != base:
        return True, [f'{model} was rigged for weapon_{rigged_for}, not {base}: the hands hold it at the other grip']
    return True, []


def render(spec):
    """The weapon script: core, hud and the shot engine, plus the adapter (data/weapons/weapon.js)."""
    return fire_engine.build('add_weapon', spec['targetname'][:-7], spec)


def add_weapon(project, m, op, refs):
    from . import layout
    if isinstance(op.get('origin'), dict):              # a room point, as everywhere
        op = {**op, 'origin': layout.point(m, op['origin'])[0]}
    spec = validate(op)
    p = spec['targetname'][:-7]
    if m.entities(targetname=spec['targetname']) or m.entities(targetname=f'{p}_script'):
        raise OpError(f'weapon {spec["name"]!r} already exists in this map')
    warnings = []
    if spec['model']:
        from .ext.models import asset_path
        asset_path(project, spec['model'])
        spec['rigged'], warnings = _rigged(project, spec['model'], spec['base'])
    _check_assets(project, spec)
    if spec['fire']:
        ensure_hud(project)
        fire_engine.ensure_assets(project, spec['fire'])
    origin = op.get('origin')
    if origin is not None:
        if not isinstance(origin, (list, tuple)) or len(origin) != 3:
            raise OpError('origin must be [x,y,z] or a layout room point (where the weapon lies)')
        origin = [_number(v, 'origin', -1e6, 1e6) for v in origin]
    anchor = origin or [0.0, 0.0, 0.0]
    angles = _angles(op.get('angles', [0, 0, 90]))
    code = render(spec)
    digest = hashlib.sha256(code.encode('utf-8')).hexdigest()[:16]
    result = scripting.script_write(project, f'{p}_{digest}', code)
    if not result.get('ok'):
        raise OpError(f'weapon script compilation failed: {result}')
    e, f, parked = spec['entities'], spec['fire'], [anchor[0], anchor[1], anchor[2] - 4096]
    defs = []
    if origin is not None:
        defs.append((spec['base'], 'weapon', {'targetname': spec['targetname'], 'CanBePickedUp': 1}, origin, angles))
    defs.append(('point_script', 'script', {'targetname': f'{p}_script', 'cs_script': result['cs_script']}, anchor, None))
    for i, event in enumerate(spec['sound']):
        defs.append(('point_soundevent', f'sound{i}', {'targetname': e['fire'][i], 'soundName': event, 'startOnSpawn': 0},
                     anchor, None))
    if spec['impact']:
        defs.append(('info_particle_system', 'impact', {'targetname': e['impact'], 'effect_name': IMPACT,
                                                        'start_active': 0}, parked, None))
    if spec['model']:
        # The map must reference the model or CS2 never loads it: SetModel on a model missing from the map's
        # manifest shows ERROR and crashes the client (07.10). A parked, non-solid copy keeps it resident.
        defs.append(('prop_dynamic', 'precache', {'targetname': f'{p}_precache', 'model': spec['model'], 'solid': 0,
                                                  'disableshadows': 1}, parked, None))
    if f:
        layout = themed_hud(project, op.get('hud'))
        for cls, key, kv, pos in fire_engine.entity_defs(e, f, anchor):
            if cls == 'custom_hud_layout' and layout:
                kv = {**kv, 'layout': f'panorama/layout/custom_game/{layout}.vxml'}
            defs.append((cls, key, kv, pos, None))
    for _, _, kv, _, _ in defs:
        if m.entities(targetname=kv['targetname']):
            raise OpError('duplicate weapon entity name: ' + kv['targetname'])
    ids = {}
    for cls, key, kv, pos, ang in defs:
        node = m.new_entity(cls, kv, pos, list(ang or (0, 0, 0)), defaults=project.catalog.defaults(cls))
        m.attach(node)
        ids[key] = node['nodeID']
    main = ids.get('weapon', ids['script'])
    if op.get('ref'):
        refs[op['ref']] = main
    out = {'op': 'add_weapon', 'id': main, 'entities': ids, 'name': spec['name'], 'base': spec['base'],
           'script': result['cs_script'], 'state': f'w9_{spec["name"]}_state', 'log': result.get('log'),
           'model_in_hands': bool(spec['rigged'])}
    if warnings:
        out['warnings'] = warnings
    return out


def old_op(name):
    """The two former weapon operations, merged into add_weapon (07.10)."""
    def refuse(project, m, op, refs):
        raise OpError(f'{name} was merged into add_weapon: base (any stock weapon, "taser" for a gadget), origin '
                      'and/or give, model (model_build weapon=... to hold it in the hands), color, fire '
                      '(read_guide section weapons)')
    return refuse


def _backup(path, project):
    if path.exists():
        backup = project.s.backups / 'weapons' / f'{time.time_ns()}-{path.name}'
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)


def _rigged_asset(project, folder: str, name: str, script: Path, params: dict, weapon: dict, surface='metal') -> dict:
    """A prepared weapon model (railgun, zapper) through the same rig as model_build(weapon=...)."""
    from . import weapon_rig
    root = ensure_inside(project.s.content_addon / folder, project.s.content_addon)
    sources = root / 'sources'
    sources.mkdir(parents=True, exist_ok=True)
    scratch = ensure_inside(PROJECT_ROOT / '.local/tmp', PROJECT_ROOT)
    scratch.mkdir(parents=True, exist_ok=True)
    param_file = scratch / f'{name}_{uuid.uuid4().hex}.json'
    param_file.write_text(json.dumps({**params, '__cs2map_weapon': True}), encoding='utf-8')
    fbx = sources / f'{name}.fbx'
    _backup(root / f'{name}.vmdl', project)
    try:
        job = blender_run.run(project.s, project.jobs, models.BLENDER_DIR / 'driver.py',
                              ['build', str(script), str(fbx), 'none', str(param_file)], timeout=180)
    finally:
        param_file.unlink(missing_ok=True)
    if job.state != 'succeeded' or not job.result.get('weapon_tris'):
        raise OpError(f'{name} Blender build failed; log: {job.log}')
    vmats, remaps = models._materials_for_model(project, job.result, surface, name)
    tris = json.loads((sources / job.result['weapon_tris']).read_text(encoding='utf-8'))
    vmdl, notes = weapon_rig.rig(project, name, root, tris, weapon_rig.validate(weapon), remaps, surface)
    compiled = models._compile_model(project, vmdl, vmats)
    if not compiled.get('ok'):
        raise OpError(f'{name} model compilation failed; log: {compiled.get("log")}')
    (root / 'model.json').write_text(json.dumps({'name': name, 'weapon': notes, 'bounds': job.result.get('bounds')},
                                                indent=1), encoding='utf-8')
    return {'model': project.s.addon_relative(vmdl), 'weapon': notes, 'logs': [job.log, compiled.get('log')]}


def prepare(project, kind='railgun'):
    """Ready weapon assets: railgun (AWP base, six layered sounds), launcher (Nova base, rocket model) or zapper
    (taser base, a small gadget)."""
    if kind == 'railgun':
        return prepare_railgun(project)
    if kind == 'launcher':
        return prepare_launcher(project)
    if kind != 'zapper':
        raise OpError('kind must be railgun, launcher or zapper')
    paths = {}
    for name, color, kind, metal in (('dark', [14, 15, 18], 'opaque', .4), ('metal', [118, 124, 132], 'opaque', .85),
                                     ('copper', [170, 78, 30], 'opaque', .8), ('glow', [60, 200, 255], 'emissive', 0)):
        out = materials.create(project.s, 'x8_zap_' + name, pattern={'pattern': 'solid', 'color': color, 'size': 64},
                               kind=kind, color=color, glow=5, metalness=metal, roughness=.35, folder='mcp_x')
        if not out.get('ok', True):
            raise OpError('zapper material failed: ' + str(out))
        paths[name] = out['material']
    made = _rigged_asset(project, 'models/mcp_x/zapper', 'zapper', DATA / 'zapper.py', paths, {'base': 'taser'})
    samples, rate, loop = sound.synthesize('beep', .18, {'frequency': 950, 'gain': .45})
    wav = ensure_inside(project.s.content_addon / 'sounds/mcp_x/x8_zap.wav', project.s.content_addon)
    wav.parent.mkdir(parents=True, exist_ok=True)
    _backup(wav, project)
    wav.write_bytes(sound.wav_bytes(samples, rate, loop))
    snd = sound._compile(project, wav)
    event = sound.sound_event(project, 'x8_zap', 'oneshot', sound='sounds/mcp_x/x8_zap.vsnd', volume=.7, distance=1500)
    return {'ok': True, 'model': made['model'], 'base': 'taser', 'sound': event['event'], 'weapon': made['weapon'],
            'logs': [*made['logs'], snd['log'], event['log'], ensure_hud(project)]}


def prepare_launcher(project):
    """A shoulder rocket launcher on a Nova (rigged, hands as for the shotgun) and the default rocket."""
    paths = {}
    for name, color, metal in (('olive', [52, 62, 36], .2), ('dark', [16, 17, 18], .6), ('band', [205, 150, 20], .1)):
        out = materials.create(project.s, 'w9_launcher_' + name, pattern={'pattern': 'solid', 'color': color, 'size': 64},
                               color=color, metalness=metal, roughness=.55, folder='mcp_w')
        if not out.get('ok', True):
            raise OpError('launcher material failed: ' + str(out))
        paths[name] = out['material']
    made = _rigged_asset(project, 'models/mcp_w/launcher', 'launcher', DATA / 'launcher.py', paths,
                         {'base': 'nova', 'scale': .75})
    rocket = fire_engine.ensure_rocket(project)
    return {'ok': True, 'model': made['model'], 'base': 'nova', 'weapon': made['weapon'],
            'rocket': fire_engine.ROCKET_MODEL, 'logs': [x for x in (*made['logs'], rocket, ensure_hud(project)) if x]}


def prepare_railgun(project):
    """The railgun on an AWP (rigged, scale .55) and its six sounds (crack, body, tail, charge, reload, empty)."""
    import importlib.util
    logs, paths = [], {}
    for name, color, metalness, roughness in (
            ('dark', [20, 29, 39], .7, .35), ('steel', [120, 150, 173], .85, .25),
            ('copper', [180, 80, 30], .8, .28), ('glow', [24, 130, 255], 0, .3)):
        out = materials.create(project.s, 'w9_rail_' + name, pattern={'pattern': 'solid', 'color': color, 'size': 64},
                               kind='emissive' if name == 'glow' else 'opaque', color=color, glow=4,
                               metalness=metalness, roughness=roughness, folder='mcp_w')
        if not out.get('ok', True):
            raise OpError('railgun material failed: ' + str(out))
        paths[name] = out['material']
        logs.append(out.get('log'))
    made = _rigged_asset(project, 'models/mcp_w/railgun', 'railgun', DATA / 'railgun.py', paths,
                         {'base': 'awp', 'scale': .55})
    logs.extend(made['logs'])
    module = importlib.util.spec_from_file_location('w9_rail_audio', DATA / 'rail_audio.py')
    audio = importlib.util.module_from_spec(module)
    module.loader.exec_module(audio)
    sounds, mix = audio.generate()
    events, metrics = {}, {}
    for name, samples in sounds.items():
        wav = ensure_inside(project.s.content_addon / f'sounds/mcp_w/w9_rail_{name}.wav', project.s.content_addon)
        wav.parent.mkdir(parents=True, exist_ok=True)
        _backup(wav, project); wav.write_bytes(sound.wav_bytes(samples, audio.RATE, False))
        data, rate = sound.read_wav(wav)
        metrics[name] = sound.metrics(data)
        snd = sound._compile(project, wav)
        event = sound.sound_event(project, 'w9_rail_' + name, 'oneshot',
                                  sound=f'sounds/mcp_w/w9_rail_{name}.vsnd', volume=.85, distance=1800)
        events[name] = event['event']; logs.extend([snd['log'], event['log']])
    logs.append(ensure_hud(project))
    return {'ok': True, 'model': RAILGUN_MODEL, 'base': 'awp', 'weapon': made['weapon'], 'sounds': events,
            'shot_sound': [events[n] for n in ('crack', 'body', 'tail')], 'audio': {'mix': mix, 'files': metrics},
            'logs': [x for x in logs if x]}


def themed_hud(project, hud):
    """A themed copy of the weapon HUD (layout mcp_x_<digest>) when hud carries style keys; None = the shared one."""
    from . import ui
    try:
        style = ui.validate(hud or {})
    except ui.UIError as e:
        raise OpError(str(e)) from None
    if not ui.has_style(style):
        return None
    css = (DATA / 'custom_hud.css').read_text(encoding='utf-8') + ui.override({'panel': '#weapon'}, style, 'bottom-right')
    layout = 'mcp_x_' + hashlib.sha1(css.encode()).hexdigest()[:8]
    from .project import DRY_RUN
    if not DRY_RUN.get() and not (project.s.game_addon / f'panorama/layout/custom_game/{layout}.vxml_c').is_file():
        xml = (DATA / 'custom_hud.xml').read_text(encoding='utf-8').replace('mcp_x_custom.css', layout + '.css')
        from . import mechanics
        mechanics.ensure_hud(project, layout, xml, css)
    return layout


def ensure_hud(project, force=False):
    """The weapon HUD layout (panorama/layout/custom_game/mcp_x_custom: the only allowed folder), compiled once."""
    layout = 'mcp_x_custom'
    if not force and (project.s.game_addon / f'panorama/layout/custom_game/{layout}.vxml_c').is_file():
        return None
    sources = []
    for ext, folder in (('xml', 'panorama/layout/custom_game'), ('css', 'panorama/styles/custom_game')):
        path = ensure_inside(project.s.content_addon / folder / (layout + '.' + ext), project.s.content_addon)
        path.parent.mkdir(parents=True, exist_ok=True)
        _backup(path, project)
        path.write_text((DATA / ('custom_hud.' + ext)).read_text(encoding='utf-8'), encoding='utf-8')
        sources.append(path)
    job = compiler.compile_assets(project.s, project.jobs, sources, force=True, timeout=60)
    state = project.jobs.wait(job.id, 65)
    if state.state != 'succeeded' or not state.result.get('ok') or state.result.get('errors'):
        raise OpError(f'weapon HUD compilation failed; log: {state.log}')
    return state.log


BEAM_MODEL, BEAM_POOL, CHARGE_GLOW = fire_engine.BEAM_MODEL, fire_engine.BEAM_POOL, fire_engine.CHARGE_GLOW
