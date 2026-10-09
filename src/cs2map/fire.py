"""The shot engine's schema and entities: owned by the weapons zone (agent W), used as is by turrets and gadgets.

JS side: ``data/mechanics/fire.js`` (fragment ``fire``). A user of the engine (``add_weapon``, a turret) calls

    f = fire.validate(op['fire'])                 # the one schema (charge, pierce, beam, pellets, magazine, recoil, ...)
    names = fire.names(prefix, f)                 # targetnames of the HUD, charge sound, glow and beam rods
    defs = fire.entity_defs(names, f, origin)     # (classname, key, keyvalues, origin) for the map
    fire.ensure_assets(project, f)                # HUD layout + beam model, built once
    code = mechanics.build_script(..., fragments=fire.FRAGMENTS + [...], config={..., 'fire': f, 'entities': {...names}})

Only compatible changes: new fields optional, defaults and behaviour of existing fields unchanged.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from . import blender_run, kv3, materials, mechanics, models
from .config import PROJECT_ROOT, ensure_inside
from .project import OpError

FRAGMENTS = ['core', 'hud', 'fire']
DATA = Path(__file__).with_name('data') / 'weapons'
# env_beam crashed the CS2 server (stack overflow in server.dll, 05.10): beams are a pool of
# emissive rods instead, tinted per weapon, laid along the shot and hidden after `life`.
# Additive core + halo (csgo_complex F_TRANSLUCENT + F_ADDITIVE_BLEND, as Valve's cs_italy unlit windows): the rods only
# add light, so the script fades them by dimming their colour. The old opaque rod (beam_core) dithered when faded.
BEAM_MODEL = 'models/mcp_x/beam/beam_glow.vmdl'
BEAM_MATERIALS = {'core': ('x8_beam_glow', [255, 255, 255], 6.0), 'halo': ('x8_beam_halo', [110, 110, 110], 2.0)}
BEAM_POOL = 48
CHARGE_GLOW = 'particles/weapons/cs_weapon_fx/weapon_taser_glow.vpcf'
FLASH_EFFECT = 'particles/ambient_fx/env_sparks_omni.vpcf'
CUSTOM_HUD = 'panorama/layout/custom_game/mcp_x_custom.vxml'     # own place: custom guns and gadgets show together
# Projectiles (rockets, grenades, slugs): a pool of models moved by the script and swept as spheres.
ROCKET_MODEL = 'models/mcp_x/rocket/rocket.vmdl'
TRAIL_EFFECT = 'particles/burning_fx/gas_canister_trail.vpcf'
BOOM_EFFECT = 'particles/explosions_fx/explosion_hegrenade.vpcf'
BOOM_SOUND = 'BaseGrenade.Explode'
BOOM_SLOTS = 3                      # explosions that can play at once (effect + sound each)
_PARTICLE = r'particles/[A-Za-z0-9_./-]+\.vpcf'
_MODEL = r'models/[A-Za-z0-9_./-]+\.vmdl'


# Registry of the users of the engine: operation or kind -> its adapter fragment. A new user (a turret, the gadget
# of 9.7) registers itself and builds its script with `build`; the engine itself stays one copy.
USERS: dict[str, str] = {}


def register_user(user: str, adapter: str) -> None:
    """`adapter`: the name of a registered mechanics fragment that creates `Fire(...)` and feeds it shooters."""
    USERS[user] = adapter


def build(user: str, name: str, config: dict) -> str:
    """core + hud + fire + the user's adapter, assembled by mechanics.build_script."""
    if user not in USERS:
        raise ValueError(f'unknown fire user {user!r}; registered: {sorted(USERS)}')
    return mechanics.build_script(name, [*FRAGMENTS, USERS[user]], config)


def _number(value, field, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OpError(f'{field} must be a number')
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise OpError(f'{field} must be {low}..{high}')
    return value


def _events(value, field):
    if value is None:
        return []
    events = [value] if isinstance(value, str) else value
    if not isinstance(events, list) or len(events) > 4 or any(not isinstance(e, str) or not e for e in events):
        raise OpError(f'{field} must be an event name or a list of up to 4 event names')
    return events.copy()


def _object(value, field, keys):
    if not isinstance(value, dict) or set(value) - keys:
        raise OpError(f'{field} must be an object with fields {sorted(keys)}')
    return value


def _particle(value, field, default):
    effect = default if value is None else value
    if effect is False or effect is None:
        return None
    if not isinstance(effect, str) or '..' in effect or not re.fullmatch(_PARTICLE, effect):
        raise OpError(f'{field} must be a particles/...vpcf path or false')
    return effect


def _projectile(value):
    """fire.projectile: what flies instead of a hitscan shot, and how it explodes."""
    p = _object(value, 'fire.projectile', {'speed', 'gravity', 'life', 'radius', 'model', 'scale', 'trail', 'homing',
                                           'explode', 'hit', 'pool'})
    model = p.get('model', ROCKET_MODEL)
    if not isinstance(model, str) or '..' in model or not re.fullmatch(_MODEL, model):
        raise OpError('fire.projectile.model must be a models/...vmdl path')
    out = {'speed': _number(p.get('speed', 1500), 'fire.projectile.speed', 50, 20000),
           'gravity': _number(p.get('gravity', 0), 'fire.projectile.gravity', 0, 4000),
           'life': _number(p.get('life', 4), 'fire.projectile.life', .1, 30),
           'radius': _number(p.get('radius', 4), 'fire.projectile.radius', 0, 64),
           'scale': _number(p.get('scale', 1), 'fire.projectile.scale', .05, 20),
           'hit': _number(p.get('hit', 0), 'fire.projectile.hit', 0, 10000),
           'pool': int(_number(p.get('pool', 6), 'fire.projectile.pool', 1, 32)),
           'model': model, 'trail': _particle(p.get('trail'), 'fire.projectile.trail', TRAIL_EFFECT),
           'homing': None, 'explode': None}
    homing = p.get('homing')
    if homing:
        h = _object(homing if isinstance(homing, dict) else {}, 'fire.projectile.homing', {'turn', 'cone', 'target', 'delay'})
        target = h.get('target', 'aim')
        if target not in ('aim', 'enemy'):
            raise OpError('fire.projectile.homing.target must be "aim" (where the shooter looks) or "enemy"')
        out['homing'] = {'target': target, 'turn': _number(h.get('turn', 120), 'fire.projectile.homing.turn', 1, 1440),
                         'cone': _number(h.get('cone', 45), 'fire.projectile.homing.cone', 1, 180),
                         'delay': _number(h.get('delay', .15), 'fire.projectile.homing.delay', 0, 5)}
    explode = p.get('explode', {})
    if explode is not False:
        x = _object(explode, 'fire.projectile.explode', {'radius', 'damage', 'falloff', 'knockback', 'self', 'effect', 'sound'})
        sound = x.get('sound', BOOM_SOUND)
        if sound is not None and sound is not False and (not isinstance(sound, str) or not sound):
            raise OpError('fire.projectile.explode.sound must be a sound event name or false')
        out['explode'] = {'radius': _number(x.get('radius', 200), 'fire.projectile.explode.radius', 0, 2048),
                          'damage': _number(x.get('damage', 120), 'fire.projectile.explode.damage', 0, 10000),
                          'falloff': _number(x.get('falloff', 1), 'fire.projectile.explode.falloff', 0, 1),
                          'knockback': _number(x.get('knockback', 400), 'fire.projectile.explode.knockback', 0, 4000),
                          'self': _number(x.get('self', .5), 'fire.projectile.explode.self', 0, 1),
                          'effect': _particle(x.get('effect'), 'fire.projectile.explode.effect', BOOM_EFFECT),
                          'sound': sound or None}
    return out


def validate(value):
    """Scripted fire: the native shot is disabled and the script charges, traces and draws."""
    keys = {'charge', 'cooldown', 'magazine', 'reload', 'damage', 'penetration', 'thickness', 'wall_loss', 'range',
            'pellets', 'spread', 'recoil', 'beam', 'charge_sound', 'charge_effect',
            'sound', 'empty_sound', 'reload_sound', 'shake', 'flash', 'kickback', 'impact', 'knockback',
            'auto', 'burst', 'muzzle', 'projectile'}
    if not isinstance(value, dict) or set(value) - keys:
        raise OpError(f'fire must be an object with fields {sorted(keys)}')
    f = {'charge': _number(value.get('charge', 0), 'fire.charge', 0, 10),
         'cooldown': _number(value.get('cooldown', .3), 'fire.cooldown', 0, 30),
         'magazine': int(_number(value.get('magazine', 10), 'fire.magazine', 1, 250)),
         'reload': _number(value.get('reload', 2), 'fire.reload', .1, 30),
         'damage': _number(value.get('damage', 50), 'fire.damage', .1, 10000),
         'penetration': int(_number(value.get('penetration', 0), 'fire.penetration', 0, 100)),
         'thickness': _number(value.get('thickness', 32), 'fire.thickness', 1, 1024),
         'wall_loss': _number(value.get('wall_loss', .1), 'fire.wall_loss', 0, 1),
         'range': _number(value.get('range', 8192), 'fire.range', 16, 65536),
         'pellets': int(_number(value.get('pellets', 1), 'fire.pellets', 1, 32)),
         'spread': _number(value.get('spread', 0), 'fire.spread', 0, 45),
         'recoil': 0,
         'charge_sound': value.get('charge_sound') or None, 'beam': None}
    recoil = value.get('recoil', 1)
    if isinstance(recoil, dict):
        r = _object(recoil, 'fire.recoil', {'kick', 'side', 'return'})
        f['recoil'] = {'kick': _number(r.get('kick', 1), 'fire.recoil.kick', 0, 30),
                       'side': _number(r.get('side', .25), 'fire.recoil.side', 0, 10),
                       'return': _number(r.get('return', .55), 'fire.recoil.return', .1, 3)}
    else:
        f['recoil'] = _number(recoil, 'fire.recoil', 0, 30)
    # New features are absent from old configs, keeping old users and entity counts unchanged.
    for field in ('sound', 'empty_sound', 'reload_sound'):
        if field in value:
            f[field] = _events(value[field], 'fire.' + field)
    for field, high in (('kickback', 1000), ('knockback', 2000)):
        if field in value:
            f[field] = _number(value[field], 'fire.' + field, 0, high)
    if 'impact' in value:
        if not isinstance(value['impact'], bool):
            raise OpError('fire.impact must be boolean')
        f['impact'] = value['impact']
    if value.get('shake') is not None and value.get('shake') is not False:
        sh = _object(value['shake'], 'fire.shake', {'amplitude', 'frequency', 'duration'})
        f['shake'] = {'amplitude': _number(sh.get('amplitude', .3), 'fire.shake.amplitude', 0, 3),
                      'frequency': _number(sh.get('frequency', 16), 'fire.shake.frequency', 1, 40),
                      'duration': _number(sh.get('duration', .2), 'fire.shake.duration', .02, 1)}
    if value.get('flash') is not None and value.get('flash') is not False:
        fl = _object(value['flash'], 'fire.flash', {'effect', 'life', 'light', 'color', 'lumens', 'range'})
        effect = fl.get('effect', FLASH_EFFECT)
        if effect is not False and (not isinstance(effect, str) or '..' in effect or
                not re.fullmatch(r'particles/[A-Za-z0-9_./-]+\.vpcf', effect)):
            raise OpError('fire.flash.effect must be particles/...vpcf or false')
        color = fl.get('color', [100, 180, 255])
        if not isinstance(color, (list, tuple)) or len(color) != 3:
            raise OpError('fire.flash.color must be [r,g,b]')
        light = fl.get('light', True)
        if not isinstance(light, bool):
            raise OpError('fire.flash.light must be boolean')
        f['flash'] = {'effect': effect or None, 'light': light,
                      'life': _number(fl.get('life', .12), 'fire.flash.life', .02, 1),
                      'color': [int(_number(c, 'fire.flash.color', 0, 255)) for c in color],
                      'lumens': _number(fl.get('lumens', 1800), 'fire.flash.lumens', 0, 20000),
                      'range': _number(fl.get('range', 180), 'fire.flash.range', 16, 512)}
    if 'auto' in value:
        if not isinstance(value['auto'], bool):
            raise OpError('fire.auto must be boolean (true: holding the trigger keeps firing)')
        f['auto'] = value['auto']
    if value.get('burst'):
        b = _object(value['burst'], 'fire.burst', {'count', 'interval'})
        f['burst'] = {'count': int(_number(b.get('count', 3), 'fire.burst.count', 2, 20)),
                      'interval': _number(b.get('interval', .08), 'fire.burst.interval', .02, 2)}
    if value.get('muzzle') is not None:
        mz = _object(value['muzzle'], 'fire.muzzle', {'forward', 'right', 'down'})
        f['muzzle'] = {'forward': _number(mz.get('forward', 40), 'fire.muzzle.forward', 0, 200),
                       'right': _number(mz.get('right', 5), 'fire.muzzle.right', -64, 64),
                       'down': _number(mz.get('down', 5), 'fire.muzzle.down', -64, 64)}
    if value.get('projectile') is not None:
        f['projectile'] = _projectile(value['projectile'])
        if value.get('beam'):
            raise OpError('fire.beam and fire.projectile exclude each other (a projectile has its own trail)')
    if f['charge_sound'] is not None and not isinstance(f['charge_sound'], str):
        raise OpError('fire.charge_sound must be a sound event name')
    # Glow in front of the barrel while charging (stock zeus glow unless another particle or false).
    effect = value.get('charge_effect', CHARGE_GLOW if f['charge'] else False)
    if effect is not False and (not isinstance(effect, str) or '..' in effect or not re.fullmatch(r'particles/[A-Za-z0-9_./-]+\.vpcf', effect)):
        raise OpError('fire.charge_effect must be a particles/...vpcf path or false')
    f['charge_effect'] = effect or None
    beam = value.get('beam')
    if beam:
        if not isinstance(beam, dict) or set(beam) - {'color', 'width', 'life', 'fade'}:
            raise OpError('fire.beam must be {color:[r,g,b], width, life, fade}')
        color = beam.get('color', [255, 255, 255])
        if not isinstance(color, (list, tuple)) or len(color) != 3:
            raise OpError('fire.beam.color must be [r,g,b]')
        f['beam'] = {'color': [int(_number(c, 'fire.beam.color', 0, 255)) for c in color],
                     'width': _number(beam.get('width', 4), 'fire.beam.width', .5, 64),
                     'life': _number(beam.get('life', .4), 'fire.beam.life', .05, 5),
                     # seconds of fading out after `life` (0 = vanish at once)
                     'fade': _number(beam.get('fade', .3), 'fire.beam.fade', 0, 5)}
        # Rod length/thickness scale together: width 2.4 units = scale 1 = 128-unit segments.
        scale = f['beam']['width'] / 2.4
        f['beam']['scale'] = scale
        f['beam']['segment'] = 128 * scale
    return f


def names(prefix: str, f: dict) -> dict:
    """Targetnames the engine's script looks up (``config.entities`` of the fire fragment)."""
    e = {'hud': f'{prefix}_hud', 'charge': f'{prefix}_charge' if f['charge_sound'] else None,
         'glow': f'{prefix}_glow' if f['charge_effect'] else None}
    if f['beam']:
        e.update(beam=f'{prefix}_beam_', beam_count=min(BEAM_POOL, math.ceil(f['range'] / f['beam']['segment'])))
    for field in ('empty_sound', 'reload_sound'):
        if f.get(field):
            e[field] = [f'{prefix}_{field}{i}' for i in range(len(f[field]))]
    if f.get('projectile'):
        pr = f['projectile']
        e.update(proj=f'{prefix}_proj_', proj_count=pr['pool'], trail=f'{prefix}_trail_' if pr['trail'] else None)
        x = pr['explode'] or {}
        e.update(boom=f'{prefix}_boom_' if x.get('effect') else None,
                 boom_sound=f'{prefix}_boomsnd_' if x.get('sound') else None, boom_count=BOOM_SLOTS)
    if f.get('flash'):
        if f['flash']['effect']:
            e['flash'] = f'{prefix}_flash'
        if f['flash']['light']:
            e['flash_anchor'] = f'{prefix}_flash_anchor'
            e['flash_light'] = f'{prefix}_flash_light'
    return e


def entity_defs(e: dict, f: dict, origin) -> list:
    """(classname, key, keyvalues, origin) of the HUD, glow, charge sound and the rod pool; rods and glow are parked."""
    parked = [origin[0], origin[1], origin[2] - 4096]
    defs = [('custom_hud_layout', 'hud', {'targetname': e['hud'], 'layout': CUSTOM_HUD}, list(origin))]
    if f['charge_effect']:
        defs.append(('info_particle_system', 'glow', {'targetname': e['glow'], 'effect_name': f['charge_effect'],
                                                      'start_active': 0}, parked))
    if f['charge_sound']:
        defs.append(('point_soundevent', 'charge', {'targetname': e['charge'], 'soundName': f['charge_sound'],
                                                    'startOnSpawn': 0}, list(origin)))
    for field in ('empty_sound', 'reload_sound'):
        for name, event in zip(e.get(field, []), f.get(field, [])):
            defs.append(('point_soundevent', name, {'targetname': name, 'soundName': event,
                                                   'startOnSpawn': 0}, list(origin)))
    if f.get('flash'):
        flash = f['flash']
        if flash['effect']:
            defs.append(('info_particle_system', 'flash', {'targetname': e['flash'],
                         'effect_name': flash['effect'], 'start_active': 0}, parked))
        if flash['light']:
            # Lights exist only on the client. A networked, invisible model carries the dynamic light;
            # the engine parks that parent below the scene after the flash, with no light I/O.
            defs.append(('prop_dynamic', 'flash_anchor', {'targetname': e['flash_anchor'], 'model': BEAM_MODEL,
                         'solid': 0, 'renderamt': 0, 'rendercolor': '0 0 0', 'disableshadows': 1}, parked))
            defs.append(('light_omni2', 'flash_light', {'targetname': e['flash_light'],
                         'parentname': e['flash_anchor'], 'brightness_units': 1,
                         'brightness_lumens': flash['lumens'], 'range': flash['range'],
                         'color': ' '.join(map(str, flash['color'])), 'colormode': 0,
                         'directlight': 2, 'bouncelight': 0, 'castshadows': 0, 'showlight': 0}, parked))
    if f.get('projectile'):
        pr = f['projectile']
        for i in range(e['proj_count']):
            name = e['proj'] + str(i)
            defs.append(('prop_dynamic', f'proj{i}', {'targetname': name, 'model': pr['model'], 'solid': 0,
                                                     'StartDisabled': 1}, parked))
            if e.get('trail'):
                # The trail rides on its projectile (parented), a little behind the model's origin.
                defs.append(('info_particle_system', f'trail{i}', {'targetname': e['trail'] + str(i), 'parentname': name,
                             'effect_name': pr['trail'], 'start_active': 0},
                             [parked[0] - 8 * pr['scale'], parked[1], parked[2]]))
        for i in range(e['boom_count'] if (e.get('boom') or e.get('boom_sound')) else 0):
            if e.get('boom'):
                defs.append(('info_particle_system', f'boom{i}', {'targetname': e['boom'] + str(i),
                             'effect_name': pr['explode']['effect'], 'start_active': 0}, parked))
            if e.get('boom_sound'):
                defs.append(('point_soundevent', f'boomsnd{i}', {'targetname': e['boom_sound'] + str(i),
                             'soundName': pr['explode']['sound'], 'startOnSpawn': 0}, parked))
    if f['beam']:
        for i in range(e['beam_count']):
            defs.append(('prop_dynamic', f'beam{i}', {'targetname': e['beam'] + str(i), 'model': BEAM_MODEL, 'solid': 0,
                                                      'StartDisabled': 1, 'disableshadows': 1,
                                                      'rendercolor': ' '.join(str(c) for c in f['beam']['color'])}, parked))
    return defs


def ensure_assets(project, f: dict, hud=None):
    """The fire HUD layout and (if `f` has a beam) the rod model; both built once. Returns logs."""
    logs = []
    if hud is not None and not (project.s.game_addon / (CUSTOM_HUD.replace('.vxml', '.vxml_c'))).is_file():
        logs.append(hud(project))
    if f['beam'] or (f.get('flash') and f['flash']['light']):
        logs.append(ensure_beam(project))
    if f.get('projectile') and f['projectile']['model'] == ROCKET_MODEL:
        logs.append(ensure_rocket(project))
    return [x for x in logs if x]


def ensure_rocket(project):
    """The default projectile: a small rocket (olive body, fins, glowing nozzle), front +X, built once."""
    if (project.s.game_addon / (ROCKET_MODEL + '_c')).is_file():
        return None
    paths = {}
    for name, color, kind, metal in (('body', [70, 82, 60], 'opaque', .3), ('dark', [30, 32, 34], 'opaque', .6),
                                     ('flame', [255, 150, 60], 'emissive', 0)):
        made = materials.create(project.s, 'x8_rocket_' + name, pattern={'pattern': 'solid', 'color': color, 'size': 64},
                                kind=kind, color=color, glow=6, metalness=metal, roughness=.45, folder='mcp_x')
        if not made.get('ok', True):
            raise OpError(f'rocket material failed: {made}')
        paths[name] = made['material']
    root = ensure_inside(project.s.content_addon / 'models/mcp_x/rocket', project.s.content_addon)
    root.mkdir(parents=True, exist_ok=True)
    scratch = ensure_inside(PROJECT_ROOT / '.local/tmp', PROJECT_ROOT)
    scratch.mkdir(parents=True, exist_ok=True)
    params = scratch / 'x8_rocket_params.json'
    params.write_text(json.dumps(paths), encoding='utf-8')
    fbx = root / 'rocket.fbx'
    try:
        job = blender_run.run(project.s, project.jobs, models.BLENDER_DIR / 'driver.py',
                              ['build', str(DATA / 'rocket.py'), str(fbx), 'none', str(params)], timeout=120)
    finally:
        params.unlink(missing_ok=True)
    if job.state != 'succeeded' or not fbx.is_file():
        raise OpError(f'rocket model Blender build failed; log: {job.log}')
    _, remaps = models._materials_for_model(project, job.result, 'metal', 'x8_rocket')
    doc = {'rootNode': {'_class': 'RootNode', 'model_archetype': '', 'primary_associated_entity': '',
                        'anim_graph_name': '', 'children': [
        {'_class': 'RenderMeshList', 'children': [{'_class': 'RenderMeshFile', 'name': 'rocket',
         'filename': project.s.addon_relative(fbx), 'import_scale': .0254,
         'import_filter': {'exclude_by_default': False, 'exception_list': []}}]},
        {'_class': 'MaterialGroupList', 'children': [{'_class': 'DefaultMaterialGroup', 'remaps': remaps,
         'use_global_default': False, 'global_default_material': ''}]}]}}
    vmdl = root / 'rocket.vmdl'
    vmdl.write_text(kv3.dumps(doc, header=kv3.HEADER_MODELDOC), encoding='utf-8')
    compiled = models._compile_model(project, vmdl, [])
    if not compiled.get('ok'):
        raise OpError(f'rocket model compilation failed; log: {compiled.get("log")}')
    return compiled.get('log')


def _additive(project, name, color, glow) -> str:
    """An emissive material made additive (adds light; fades with the entity colour), compiled."""
    from . import compiler
    made = materials.create(project.s, name, pattern={'pattern': 'solid', 'color': color, 'size': 64},
                            kind='emissive', color=color, glow=glow, folder='mcp_x')
    if not made.get('ok', True):
        raise OpError(f'beam material failed: {made}')
    vmat = project.s.content_addon / f'materials/mcp_x/{name}/{name}.vmat'
    text = vmat.read_text(encoding='utf-8')
    additive = 'F_TRANSLUCENT 1' + chr(10) + chr(9) + 'F_ADDITIVE_BLEND 1'
    text = text.replace('F_TRANSLUCENT 0', additive).replace('g_bFogEnabled "1"', 'g_bFogEnabled "0"')
    vmat.write_text(text, encoding='utf-8')
    job = compiler.compile_assets(project.s, project.jobs, [vmat], force=True, timeout=60)
    done = project.jobs.wait(job.id, 60)
    if done.state != 'succeeded' or not done.result.get('ok'):
        raise OpError(f'beam material compile failed; log: {job.log}')
    return f'materials/mcp_x/{name}/{name}.vmat'


def ensure_beam(project):
    """Additive white rod (core + halo; tinted per weapon by rendercolor), built once with Blender."""
    if (project.s.game_addon / (BEAM_MODEL + '_c')).is_file():
        return None
    paths = {key: _additive(project, *spec) for key, spec in BEAM_MATERIALS.items()}
    root = ensure_inside(project.s.content_addon / 'models/mcp_x/beam', project.s.content_addon)
    root.mkdir(parents=True, exist_ok=True)
    scratch = ensure_inside(PROJECT_ROOT / '.local/tmp', PROJECT_ROOT)
    scratch.mkdir(parents=True, exist_ok=True)
    params = scratch / 'x8_beam_params.json'
    params.write_text(json.dumps(paths), encoding='utf-8')
    fbx = root / 'beam_glow.fbx'
    try:
        job = blender_run.run(project.s, project.jobs, models.BLENDER_DIR / 'driver.py',
                              ['build', str(DATA / 'beam.py'), str(fbx), 'none', str(params)], timeout=120)
    finally:
        params.unlink(missing_ok=True)
    if job.state != 'succeeded' or not fbx.is_file():
        raise OpError(f'beam model Blender build failed; log: {job.log}')
    _, remaps = models._materials_for_model(project, job.result, 'metal', 'x8_beam')
    doc = {'rootNode': {'_class': 'RootNode', 'model_archetype': '', 'primary_associated_entity': '',
                        'anim_graph_name': '', 'children': [
        {'_class': 'RenderMeshList', 'children': [{'_class': 'RenderMeshFile', 'name': 'beam',
         'filename': project.s.addon_relative(fbx), 'import_scale': .0254,
         'import_filter': {'exclude_by_default': False, 'exception_list': []}}]},
        {'_class': 'MaterialGroupList', 'children': [{'_class': 'DefaultMaterialGroup', 'remaps': remaps,
         'use_global_default': False, 'global_default_material': ''}]}]}}
    vmdl = root / 'beam_glow.vmdl'
    vmdl.write_text(kv3.dumps(doc, header=kv3.HEADER_MODELDOC), encoding='utf-8')
    compiled = models._compile_model(project, vmdl, [])
    if not compiled.get('ok'):
        raise OpError(f'beam model compilation failed; log: {compiled.get("log")}')
    return compiled.get('log')
