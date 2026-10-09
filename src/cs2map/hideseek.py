"""add_hideseek: hide and seek on the shared mechanics fragments (core, hud) + data/hideseek/hideseek.js.

Hiders are CT, seekers T, so CS2's own rules finish the round (hiders eliminated -> T; time out -> CT with
mp_default_team_winner_no_objective 3). The script runs the phases (seekers frozen and blind while hiders hide),
the HUD, reveal pulses (glowing silhouettes, the wallhack marker of add_ability) and prop disguises (prop hunt).
Secret walls are visible, never solid func_brush; teleports are trigger_teleport zones.
"""
from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from . import abilities, mechanics, scripting, ui
from .project import DRY_RUN, OpError

DATA = Path(__file__).parent / 'data' / 'hideseek'
FIELDS = {'op', 'name', 'hide', 'seek', 'hider_weapons', 'blind', 'reveal', 'disguise', 'secret_walls', 'teleports',
          'hud', 'ref'}
MAX_HIDERS = 8
HUD_XML = '''<root><styles><include src="file://{resources}/styles/custom_game/HUDNAME.css" /></styles>
<Panel class="HideRoot" hittest="false"><Panel id="hideseek" class="Hidden" hittest="false">
<Label id="hideseek_status" text="{s:status}" hittest="false" /></Panel></Panel></root>'''
HUD_CSS = '''.HideRoot { width: 100%; height: 100%; }
#hideseek { horizontal-align: center; vertical-align: top; margin-top: 110px; padding: 10px 26px; border-radius: 8px;
 background-color: #120d1ce8; border-top: 3px solid #ffcf4aff; box-shadow: #000000aa 0px 6px 18px 0px; }
#hideseek Label { color: #fff6dcff; font-size: 24px; font-weight: bold; }
.Hidden { visibility: collapse; }
'''


def _num(v, label, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise OpError(f'{label} must be a number {lo}..{hi}')
    return float(v)


def _box(m, value, label):
    if not isinstance(value, dict) or set(value) - {'mins', 'maxs', 'material'} or not {'mins', 'maxs'} <= set(value):
        raise OpError(f'{label} is {{mins:[x,y,z], maxs:[x,y,z]}}')
    lo = abilities._point(m, value['mins'], label + '.mins')
    hi = abilities._point(m, value['maxs'], label + '.maxs')
    if any(a >= b for a, b in zip(lo, hi)):
        raise OpError(f'{label}: maxs must be above mins')
    return lo, hi


def validate(op, m=None) -> dict:
    extra = set(op) - FIELDS
    if extra:
        raise OpError(f'unknown add_hideseek fields {sorted(extra)}; allowed: {", ".join(sorted(FIELDS - {"op"}))}')
    name = op.get('name', 'hideseek')
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', name):
        raise OpError('name: 1..32 chars a-z0-9_')
    weapons = op.get('hider_weapons', 'knife')
    if weapons not in ('knife', 'default'):
        raise OpError('hider_weapons: knife (default) or default (whatever they spawn with)')
    blind = op.get('blind', True)
    if not isinstance(blind, bool):
        raise OpError('blind is true/false (seekers see black while hiders hide)')
    disguise = op.get('disguise')
    if disguise is not None:
        if not isinstance(disguise, dict) or set(disguise) - {'models', 'key'} or not disguise.get('models'):
            raise OpError('disguise is {models: [stock or own .vmdl props], key: inspect|attack2|reload|use}')
        models = disguise['models']
        if not isinstance(models, list) or not 1 <= len(models) <= 16:
            raise OpError('disguise.models: 1..16 model paths')
        for mdl in models:
            mechanics.model_path(mdl, 'disguise.models')
        key = disguise.get('key', 'inspect')
        if key not in ('inspect', 'attack2', 'reload', 'use'):
            raise OpError('disguise.key: inspect (F), attack2, reload or use')
        disguise = {'models': models, 'key': key}
    walls = []
    for i, w in enumerate(op.get('secret_walls') or []):
        lo, hi = _box(m, w, f'secret_walls[{i}]')
        walls.append({'mins': lo, 'maxs': hi, 'material': w.get('material', 'materials/dev/reflectivity_50.vmat')})
    teleports = []
    for i, t in enumerate(op.get('teleports') or []):
        if not isinstance(t, dict) or set(t) - {'from', 'to', 'angles'} or not {'from', 'to'} <= set(t):
            raise OpError(f'teleports[{i}] is {{from: {{mins,maxs}}, to: [x,y,z], angles?}}')
        lo, hi = _box(m, t['from'], f'teleports[{i}].from')
        teleports.append({'mins': lo, 'maxs': hi, 'to': abilities._point(m, t['to'], f'teleports[{i}].to'),
                          'angles': t.get('angles', [0, 0, 0])})
    hud = op.get('hud', {})
    try:
        hud = ui.validate(hud or {}, extra={'title'})
    except ui.UIError as e:
        raise OpError(str(e)) from None
    title = hud.get('title', 'HIDE & SEEK')
    if not isinstance(title, str) or len(title) > 24 or any(c in title for c in '<>"\\\n'):
        raise OpError('hud.title: plain text up to 24 characters')
    hud['title'] = title
    return {'name': name, 'hide': _num(op.get('hide', 40), 'hide', 5, 600),
            'seek': _num(op.get('seek', 180), 'seek', 20, 3600), 'hider_weapons': weapons, 'blind': blind,
            'reveal': _num(op.get('reveal', 0), 'reveal', 0, 600), 'disguise': disguise, 'secret_walls': walls,
            'teleports': teleports, 'hud': hud, **({'ref': op['ref']} if op.get('ref') else {})}


def names(spec) -> dict:
    p = 'h10_' + spec['name']
    # Parked props stay inside the map, transparent: entities spawned outside the world are never drawn by CS2,
    # not even after they are moved in (08.10).
    return {'script': p, 'hud': 'mcp_h_' + spec['name'], 'park': spec.get('park', [0.0, 0.0, 16.0]),
            'fade': p + '_blind' if spec['blind'] else None,
            'props': [f'{p}_prop_{i}' for i in range(MAX_HIDERS)] if spec['disguise'] else [],
            'markers': [f'{p}_mark_{i}' for i in range(MAX_HIDERS)] if spec['reveal'] else []}


def generate(spec):
    n = names(spec)
    config = {'name': spec['name'], 'state': n['script'] + '_state', 'hide': spec['hide'], 'seek': spec['seek'],
              'hider_weapons': spec['hider_weapons'], 'reveal': spec['reveal'], 'disguise': spec['disguise'],
              'blind': spec['blind'],
              'title': spec['hud']['title'], 'entities': n}
    code = mechanics.build_script(n['script'], ['core', 'hud', 'hideseek_motion'], config,
                                  marker='// CS2MAP_HIDESEEK ',
                                  marker_config={k: spec[k] for k in ('name', 'hide', 'seek', 'reveal')})
    css = HUD_CSS + ui.override({'panel': '#hideseek'}, spec['hud'], 'top')
    return code, HUD_XML.replace('HUDNAME', n['hud']), css, n


def add_hideseek(project, m, op, refs):
    spec = validate(op, m)
    spawns = m.entities(classname='info_player_counterterrorist') or m.entities(classname='info_player_terrorist')
    spec['park'] = [float(c) for c in spawns[0].get('origin')] if spawns else [0.0, 0.0, 16.0]
    code, xml, css, n = generate(spec)
    taken = [x for x in [n['script'] + '_script', n['hud'], n['fade'], *n['props'], *n['markers']] if x]
    if any(m.entities(targetname=t) for t in taken):
        raise OpError(f'hide and seek {spec["name"]!r} already exists in this map')
    if any(str(e['entity_properties'].get('cs_script', '')).startswith('maps/scripts/mcp/h10_')
           for e in m.entities(classname='point_script')):
        raise OpError('one add_hideseek per map')
    dry = DRY_RUN.get()
    script = n['script'] + '_' + hashlib.sha1(code.encode()).hexdigest()[:8]
    if not dry:
        if spec['reveal']:
            abilities.ensure_assets(project, {'abilities': {'wallhack': {}}, 'source': {'kind': 'always'}})
        mechanics.ensure_hud(project, n['hud'], xml, css)
        result = scripting.script_write(project, script, code)
        if not result.get('ok'):
            raise OpError(f'hide and seek script compilation failed: {result}')
    anchor = [0.0, 0.0, 16.0]
    ops = [{'op': 'add_entity', 'classname': 'custom_hud_layout', 'origin': anchor,
            'keyvalues': {'targetname': n['hud'], 'layout': f'panorama/layout/custom_game/{n["hud"]}.vxml'}}]
    if spec['blind']:
        # spawnflags 4 = only the activator; held 1.2 s and renewed by the script every 0.5 s of the hide phase.
        # Never "stay out" (8): CS2 cannot lift it for one player (a player-only "fade from" does nothing, 09.10).
        ops.append({'op': 'add_entity', 'classname': 'env_fade', 'origin': anchor,
                    'keyvalues': {'targetname': n['fade'], 'duration': 0.4, 'holdtime': 1.2, 'renderamt': 255,
                                  'rendercolor': '0 0 0', 'spawnflags': 4}})
    # prop_dynamic_override: physics-only stock props (crates, barrels) are deleted at spawn as plain prop_dynamic
    for prop in n['props']:
        ops.append({'op': 'add_entity', 'classname': 'prop_dynamic_override', 'origin': n['park'],
                    'keyvalues': {'targetname': prop, 'model': spec['disguise']['models'][0], 'solid': 0,
                                  'disableshadows': 1, 'renderamt': 0}})
    for marker in n['markers']:
        ops.append({'op': 'add_entity', 'classname': 'prop_dynamic', 'origin': n['park'],
                    'keyvalues': {'targetname': marker, 'model': abilities.MARKER_MODEL, 'solid': 0,
                                  'disableshadows': 1, 'renderamt': 0}})
    # every disguise model must be in the map's manifest (SetModel on an unloaded model crashes the client)
    for i, mdl in enumerate((spec['disguise'] or {}).get('models', [])[1:]):
        ops.append({'op': 'add_entity', 'classname': 'prop_dynamic_override', 'origin': n['park'],
                    'keyvalues': {'targetname': f'{n["script"]}_precache_{i}', 'model': mdl, 'solid': 0,
                                  'disableshadows': 1, 'renderamt': 0}})
    for i, w in enumerate(spec['secret_walls']):
        ops.append({'op': 'add_brush_entity', 'classname': 'func_brush', 'material': w['material'],
                    'keyvalues': {'targetname': f'{n["script"]}_secret_{i}', 'Solidity': 1},
                    'shape': {'shape': 'box', 'mins': w['mins'], 'maxs': w['maxs']}})
    for i, t in enumerate(spec['teleports']):
        ops.append({'op': 'add_zone', 'kind': 'teleport', 'targetname': f'{n["script"]}_tp_{i}', 'mins': t['mins'],
                    'maxs': t['maxs'], 'params': {'destination': t['to'], 'angles': t['angles']}})
    for o in ops:
        project._apply(m, o, refs)
    from . import maptypes
    if maptypes.stored(m) in (None, 'custom'):
        maptypes.store(m, 'hideseek')
    if dry:
        attached = abilities._placeholder(project, m, n, anchor, op, refs)
    else:
        attached = scripting.add_script(project, m, {'name': script, 'targetname': n['script'] + '_script',
                                                     'origin': anchor, **({'ref': op['ref']} if op.get('ref') else {})},
                                        refs)
    return {'op': 'add_hideseek', 'id': attached['id'], 'name': spec['name'], 'hiders': 'CT', 'seekers': 'T',
            'entities': len(ops) + 1, 'test': f'{n["script"]}_state t (phase, hiders left)',
            **({'ref': op['ref']} if op.get('ref') else {})}


mechanics.register_fragment('hideseek_motion', DATA / 'hideseek.js')
