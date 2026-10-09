"""add_ability: player abilities on the shared mechanics fragments (core, hud) + data/abilities/ability.js.

One operation = one set of abilities with one source (always, a zone, an item, a chat command, an I/O input) and an
optional duration. Each ability is a short rule in the script; the operation writes the script, the HUD layout,
the zone trigger / item prop and, for the wallhack, a pool of invisible glowing silhouettes that follow enemies
(CS2 draws glow through walls on props but not on player pawns: measured 08.10).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from . import checks, mechanics, scripting, ui
from .project import DRY_RUN, OpError

DATA = Path(__file__).parent / 'data' / 'abilities'
NAME = re.compile(r'^[a-z][a-z0-9_]{0,31}$')
KEYS = ('inspect', 'attack2', 'reload', 'use', 'walk', 'duck')
MARKER_MODEL = 'models/mcp/a10_marker/a10_marker.vmdl'
ITEM_MODEL = 'models/mcp_m/m9_collect/m9_collect.vmdl'
MARKERS = 8
FIELDS = {'op', 'name', 'abilities', 'source', 'duration', 'team', 'bots', 'hud', 'ref'}
# defaults and allowed fields of each ability
SPECS = {
    'double_jump': {'jumps': 1, 'power': 300},
    'super_jump': {'power': 480},
    'dash': {'key': 'inspect', 'speed': 900, 'cooldown': 1.5, 'up': 120},
    'blink': {'key': 'attack2', 'distance': 420, 'cooldown': 3},
    'speed': {'scale': 1.4},
    'gravity': {'scale': 0.45},
    'jetpack': {'thrust': 1600, 'max_up': 260, 'fuel': 3, 'refill': 2},
    'fly': {'speed': 360},
    'autobhop': {},
    'invisible': {'alpha': 0},
    'wallhack': {'color': [255, 70, 40], 'enemies': True},
    'regen': {'per_second': 6, 'max': 100, 'delay': 3},
    'shield': {'reduce': 0.5},
    'armor': {'value': 100, 'helmet': True},
    'health': {'max': 200},
    'no_fall_damage': {},
    'launch': {'velocity': None, 'to': None, 'height': 96},
}
RANGES = {'jumps': (1, 5), 'power': (100, 2000), 'speed': (50, 3000), 'cooldown': (0, 60), 'up': (0, 1000),
          'distance': (64, 4096), 'scale': (0.05, 4), 'thrust': (200, 6000), 'max_up': (50, 2000), 'fuel': (0.2, 60),
          'refill': (0.2, 120), 'alpha': (0, 255), 'per_second': (0.5, 100), 'max': (1, 1000), 'delay': (0, 60),
          'reduce': (0, 1), 'value': (0, 100), 'height': (16, 2048)}
HUD_XML = '''<root><styles><include src="file://{resources}/styles/custom_game/HUDNAME.css" /></styles>
<Panel class="AbilityRoot" hittest="false"><Panel id="ability" class="Hidden" hittest="false">
<Label id="ability_status" text="{s:status}" hittest="false" /></Panel></Panel></root>'''
HUD_CSS = '''.AbilityRoot { width: 100%; height: 100%; }
#ability { horizontal-align: left; vertical-align: bottom; margin-left: 40px; margin-bottom: 150px; padding: 10px 20px;
 background-color: #0d1622e6; border-left: 4px solid #6fd3ffff; border-radius: 6px; }
#ability Label { color: #e9f6ffff; font-size: 19px; }
.Hidden { visibility: collapse; }
'''


def _num(v, label, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise OpError(f'{label} must be a number {lo}..{hi}')
    return float(v)


def _point(m, value, label):
    from . import layout
    if isinstance(value, dict) and m is not None:
        return list(layout.point(m, value)[0])
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise OpError(f'{label} is [x, y, z] or a room point')
    return [_num(c, label, -32768, 32768) for c in value]


def validate(op, m=None) -> dict:
    extra = set(op) - FIELDS
    if extra:
        raise OpError(f'unknown add_ability fields {sorted(extra)}; allowed: {", ".join(sorted(FIELDS - {"op"}))}')
    name = op.get('name')
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise OpError('name: 1..32 chars a-z0-9_, starts with a letter')
    raw = op.get('abilities')
    if not isinstance(raw, dict) or not raw:
        raise OpError(f'abilities: {{name: settings}} with names {", ".join(SPECS)}')
    abilities = {}
    for kind, settings in raw.items():
        if kind not in SPECS:
            raise OpError(f'unknown ability {kind!r}; known: {", ".join(SPECS)}')
        if settings is True or settings is None:
            settings = {}
        if not isinstance(settings, dict):
            raise OpError(f'abilities.{kind} is an object of settings (or true for the defaults)')
        unknown = set(settings) - set(SPECS[kind])
        if unknown:
            raise OpError(f'abilities.{kind}: unknown {sorted(unknown)}; settings: {", ".join(SPECS[kind]) or "none"}')
        out = {**SPECS[kind], **settings}
        for k, v in out.items():
            if k == 'key':
                if v not in KEYS:
                    raise OpError(f'abilities.{kind}.key: one of {", ".join(KEYS)} (inspect = F)')
            elif k == 'color':
                if not (isinstance(v, list) and len(v) == 3 and all(isinstance(c, (int, float)) and 0 <= c <= 255 for c in v)):
                    raise OpError(f'abilities.{kind}.color is [r,g,b] 0..255')
            elif k in ('helmet', 'enemies'):
                if not isinstance(v, bool):
                    raise OpError(f'abilities.{kind}.{k} is true/false')
            elif k in ('velocity', 'to'):
                if v is not None:
                    out[k] = _point(m, v, f'abilities.{kind}.{k}') if k == 'to' else \
                        [_num(c, f'abilities.{kind}.velocity', -5000, 5000) for c in (v if isinstance(v, list) and len(v) == 3 else [None])]
            else:
                lo, hi = RANGES[k]
                out[k] = _num(v, f'abilities.{kind}.{k}', lo, hi)
        if kind == 'launch' and (out['velocity'] is None) == (out['to'] is None):
            raise OpError('abilities.launch needs exactly one of velocity [x,y,z] or to [x,y,z] (a ballistic jump there)')
        if kind == 'double_jump':
            out['jumps'] = int(out['jumps'])
        abilities[kind] = out
    keys = [a['key'] for a in abilities.values() if 'key' in a]
    if len(keys) != len(set(keys)):
        raise OpError('dash and blink need different keys')
    if 'fly' in abilities and {'jetpack', 'double_jump', 'gravity'} & set(abilities):
        raise OpError('fly already ignores gravity: do not combine it with jetpack, double_jump or gravity')
    source = op.get('source', 'always')
    src = _source(source, m)
    duration = _num(op.get('duration', 0), 'duration', 0, 3600)
    lasting = any(k != 'launch' for k in abilities)
    if src['kind'] == 'always' and duration:
        raise OpError('duration needs a source that grants (zone, item, command, input); "always" lasts forever')
    if not lasting and src['kind'] not in ('zone', 'item', 'input'):
        raise OpError('launch alone is a jump pad: give source zone, item or input')
    team = {'any': 0, 't': 2, 'ct': 3}.get(op.get('team', 'any'))
    if team is None:
        raise OpError('team: any, t or ct')
    bots = op.get('bots', False)
    if not isinstance(bots, bool):
        raise OpError('bots is true/false (bots get the abilities too)')
    hud = op.get('hud', {})
    if hud is not False:
        try:
            hud = ui.validate(hud or {}, extra={'title'})
        except ui.UIError as e:
            raise OpError(str(e)) from None
        title = hud.get('title', 'ABILITIES')
        if not isinstance(title, str) or len(title) > 24 or any(c in title for c in '<>"\\\n'):
            raise OpError('hud.title: plain text up to 24 characters')
        hud['title'] = title
    return {'name': name, 'abilities': abilities, 'source': src, 'duration': duration, 'team': team, 'bots': bots,
            'hud': hud, 'lasting': lasting, **({'ref': op['ref']} if op.get('ref') else {})}


def _source(source, m):
    if source == 'always':
        return {'kind': 'always'}
    if source == 'input':
        return {'kind': 'input'}
    if not isinstance(source, dict) or len(source) != 1:
        raise OpError('source: "always", "input", {"zone":{mins,maxs}}, {"item":{origin, radius, respawn}} or '
                      '{"command":"!fly"}')
    (kind, value), = source.items()
    if kind == 'zone':
        if not isinstance(value, dict) or set(value) != {'mins', 'maxs'}:
            raise OpError('source.zone is {mins:[x,y,z], maxs:[x,y,z]}')
        lo, hi = _point(m, value['mins'], 'zone.mins'), _point(m, value['maxs'], 'zone.maxs')
        if any(a >= b for a, b in zip(lo, hi)):
            raise OpError('source.zone: maxs must be above mins on every axis')
        return {'kind': 'zone', 'mins': lo, 'maxs': hi}
    if kind == 'item':
        if not isinstance(value, dict) or 'origin' not in value or set(value) - {'origin', 'radius', 'respawn', 'model'}:
            raise OpError('source.item is {origin, radius (48), respawn (seconds, 0 = never), model}')
        model = value.get('model', ITEM_MODEL)
        mechanics.model_path(model, 'source.item.model')
        return {'kind': 'item', 'origin': _point(m, value['origin'], 'item.origin'),
                'radius': _num(value.get('radius', 48), 'item.radius', 16, 512),
                'respawn': _num(value.get('respawn', 15), 'item.respawn', 0, 3600), 'model': model}
    if kind == 'command':
        if not isinstance(value, str) or not re.fullmatch(r'!?[a-z0-9_]{1,20}', value.lower()):
            raise OpError('source.command is chat text like "!fly"')
        return {'kind': 'command', 'text': value.lower()}
    raise OpError(f'unknown source {kind!r}')


def names(spec) -> dict:
    p = 'a10_' + spec['name']
    # parked markers stay inside the map (entities spawned outside the world are never drawn, 08.10)
    out = {'script': p, 'hud': 'mcp_a_' + spec['name'], 'park': spec.get('park', [0.0, 0.0, 16.0])}
    out['zone'] = p + '_zone' if spec['source']['kind'] == 'zone' else None
    out['item'] = p + '_item' if spec['source']['kind'] == 'item' else None
    out['markers'] = [f'{p}_mark_{i}' for i in range(MARKERS)] if 'wallhack' in spec['abilities'] else []
    return out


def generate(spec):
    n = names(spec)
    config = {'name': spec['name'], 'state': n['script'] + '_state', 'abilities': spec['abilities'],
              'source': spec['source'], 'duration': spec['duration'], 'team': spec['team'], 'bots': spec['bots'],
              'lasting': spec['lasting'], 'hud': spec['hud'] is not False,
              'title': (spec['hud'] or {}).get('title', 'ABILITIES') if spec['hud'] is not False else '',
              'entities': n}
    code = mechanics.build_script(n['script'], ['core', 'hud', 'ability_motion'], config,
                                  marker='// CS2MAP_ABILITY ', marker_config={k: spec[k] for k in
                                                                              ('name', 'abilities', 'source', 'duration')})
    xml = HUD_XML.replace('HUDNAME', n['hud'])
    css = HUD_CSS + ui.override({'panel': '#ability'}, spec['hud'] or {}, 'bottom-left')
    return code, xml, css, n


def ensure_assets(project, spec):
    """The wallhack silhouette and the item token (built once, kept in the addon)."""
    out = []
    if 'wallhack' in spec['abilities'] and not (project.s.game_addon / (MARKER_MODEL + '_c')).is_file():
        from . import models
        made = models.model_build(project, 'a10_marker', script=(DATA / 'marker.py').read_text(encoding='utf-8'),
                                  collision='none')
        if not made.get('ok', True) or made.get('error'):
            raise OpError(f'wallhack marker model failed: {made}')
        out.append(MARKER_MODEL)
    if spec['source']['kind'] == 'item' and spec['source']['model'] == ITEM_MODEL \
            and not (project.s.game_addon / (ITEM_MODEL + '_c')).is_file():
        from . import minigames
        minigames.ensure_assets(project)
        out.append(ITEM_MODEL)
    return out


def add_ability(project, m, op, refs):
    spec = validate(op, m)
    src = spec['source']
    spec['park'] = src.get('origin') or ([(a + b) / 2 for a, b in zip(src['mins'], src['maxs'])]
                                         if src['kind'] == 'zone' else _inside_point(m))
    code, xml, css, n = generate(spec)
    taken = [x for x in [n['script'] + '_script', n['hud'], n['zone'], n['item'], *n['markers']] if x]
    if any(m.entities(targetname=t) for t in taken):
        raise OpError(f'ability set {spec["name"]!r} already exists in this map (or a generated name is taken)')
    dry = DRY_RUN.get()
    script = n['script'] + '_' + hashlib.sha1(code.encode()).hexdigest()[:8]
    logs = []
    if not dry:
        logs += ensure_assets(project, spec)
        if spec['hud'] is not False:
            logs.append(mechanics.ensure_hud(project, n['hud'], xml, css))
        result = scripting.script_write(project, script, code)
        if not result.get('ok'):
            raise OpError(f'ability script compilation failed: {result}')
    anchor = list(spec['park'])
    ops = []
    if spec['hud'] is not False:
        ops.append({'op': 'add_entity', 'classname': 'custom_hud_layout', 'origin': anchor,
                    'keyvalues': {'targetname': n['hud'], 'layout': f'panorama/layout/custom_game/{n["hud"]}.vxml'}})
    if n['zone']:
        ops.append({'op': 'add_brush_entity', 'classname': 'trigger_multiple', 'material': 'materials/tools/toolstrigger.vmat',
                    'keyvalues': {'targetname': n['zone'], 'spawnflags': 4097, 'wait': 0},
                    'shape': {'shape': 'box', 'mins': src['mins'], 'maxs': src['maxs']}})
    if n['item']:
        ops.append({'op': 'add_entity', 'classname': 'prop_dynamic_override', 'origin': src['origin'],
                    'keyvalues': {'targetname': n['item'], 'model': src['model'], 'solid': 0, 'disableshadows': 1}})
    for marker in n['markers']:
        ops.append({'op': 'add_entity', 'classname': 'prop_dynamic', 'origin': n['park'],
                    'keyvalues': {'targetname': marker, 'model': MARKER_MODEL, 'solid': 0, 'disableshadows': 1,
                                  'renderamt': 0}})
    for o in ops:
        project._apply(m, o, refs)
    attached = scripting.add_script(project, m, {'name': script if not dry else n['script'],
                                                 'targetname': n['script'] + '_script', 'origin': anchor,
                                                 **({'ref': op['ref']} if op.get('ref') else {})}, refs) \
        if not dry else _placeholder(project, m, n, anchor, op, refs)
    return {'op': 'add_ability', 'id': attached['id'], 'name': spec['name'], 'abilities': list(spec['abilities']),
            'source': src['kind'], 'script': n['script'], 'entities': len(ops) + 1,
            'test': f'{n["script"]}_state t  (console telemetry); input {spec["name"]}_grant / _revoke',
            **({'ref': op['ref']} if op.get('ref') else {}), **({'logs': [x for x in logs if x]} if logs else {})}


def _inside_point(m):
    """A point inside the playable map (a spawn), where parked helpers are still drawn by CS2."""
    for cls in ('info_player_terrorist', 'info_player_counterterrorist', 'info_player_start'):
        ents = m.entities(classname=cls)
        if ents:
            return [float(c) for c in ents[0].get('origin')]
    return [0.0, 0.0, 16.0]


def _placeholder(project, m, n, anchor, op, refs):
    """A dry run checks the batch without writing scripts: the point_script stands in with the planned name."""
    ent = m.new_entity('point_script', {'targetname': n['script'] + '_script',
                                         'cs_script': f'maps/scripts/mcp/{n["script"]}.vjs'},
                       anchor, defaults=project.catalog.defaults('point_script'))
    m.attach(ent)
    if op.get('ref'):
        refs[op['ref']] = ent['nodeID']
    return {'id': ent['nodeID']}


mechanics.register_fragment('ability_motion', DATA / 'ability.js')


# ---- check_map: one key, one action ---------------------------------------------------------------------------
KEY_NAMES = {'inspect': 'F', 'attack2': 'right mouse', 'reload': 'R', 'use': 'E', 'walk': 'SHIFT', 'duck': 'CTRL'}


def _script_config(project, ent):
    """The full config of an assembled mechanics script (its ``const config = {...};`` line), or None."""
    rel = str(ent['entity_properties'].get('cs_script') or '')
    source = project.s.content_addon / rel.replace('.vjs', '.js')
    if not rel.startswith('maps/scripts/mcp/') or not source.is_file():
        return None, ''
    text = source.read_text(encoding='utf-8')
    head = text.splitlines()[:6]
    for line in head:
        if line.startswith('const config = ') and line.endswith(';'):
            try:
                return json.loads(line[len('const config = '):-1]), head[0]
            except ValueError:
                return None, ''
    return None, ''


def check_keys(project, m):
    """Abilities and hide and seek listen to keys all the time: two of them on one key fire together."""
    users = {}
    for ent in m.entities(classname='point_script'):
        config, first = _script_config(project, ent)
        if not config:
            continue
        if first.startswith('// CS2MAP_ABILITY '):
            for kind, a in (config.get('abilities') or {}).items():
                if isinstance(a, dict) and a.get('key'):
                    users.setdefault(a['key'], []).append((ent, f'ability {config.get("name")!r} ({kind})'))
        elif first.startswith('// CS2MAP_HIDESEEK ') and (config.get('disguise') or {}).get('key'):
            users.setdefault(config['disguise']['key'], []).append((ent, 'hide and seek (disguise switch)'))
    problems = []
    for key, found in users.items():
        if len(found) > 1:
            problems.append(checks.problem('warning', 'input_key_conflict', found[0][0]['nodeID'],
                f'{KEY_NAMES.get(key, key)} ({key}) is used by ' + ' and '.join(x[1] for x in found) +
                ': one press triggers all of them',
                'Give one of them another key (abilities.<kind>.key, disguise.key: inspect, attack2, reload, use).'))
    return problems
