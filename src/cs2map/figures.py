"""Stand-in players for readability frames: stock T and CT agents as props at given places.

A map is readable when a player is seen against what stands behind him from where the other team looks. Bots need
a nav and do not stand where asked, so the figures are plain props: `add_figures` places them, returns the views
"one team sees the other" for game_screenshot, and check_map reminds to remove them before the release."""
from __future__ import annotations

import json
import math
import re

from . import checks
from .project import OpError

KEY = 'cs2map_figures'
MODELS = {'t': 'agents/models/tm_phoenix/tm_phoenix.vmdl', 'ct': 'agents/models/ctm_sas/ctm_sas.vmdl'}
MAX_FIGURES = 64
EYE = 64.0                 # the camera of game_screenshot stands this far over `pos`
CHEST = 48.0               # the views aim at this height of a figure
ALONE_VIEW = 512.0         # a figure with no opponent is looked at from this far in front of it
OUT_OF_FIGURE = 48.0       # the camera stands this far in front of the figure whose view it shows (not in its head)


def _groups(vmap):
    return [n for n in vmap.iter_nodes() if n.type == 'CMapGroup' and n.get(KEY)]


def _vec(value, label):
    if (not isinstance(value, (list, tuple)) or len(value) != 3
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value)):
        raise OpError(f'{label} is [x, y, z]: where the feet stand')
    return [float(v) for v in value]


def _view(name, eye_feet, target_feet, step: float = 0.0):
    """A game_screenshot view from over `eye_feet` at the chest of the figure at `target_feet`; `step` moves the
    camera that far towards the target (out of the figure that stands at eye_feet)."""
    dx, dy = target_feet[0] - eye_feet[0], target_feet[1] - eye_feet[1]
    dist = math.hypot(dx, dy)
    if step and dist > 2 * step:
        eye_feet = [eye_feet[0] + dx / dist * step, eye_feet[1] + dy / dist * step, eye_feet[2]]
        dx, dy, dist = dx * (1 - step / dist), dy * (1 - step / dist), dist - step
    dz = (target_feet[2] + CHEST) - (eye_feet[2] + EYE)
    return {'name': name, 'pos': [round(v, 1) for v in eye_feet],
            'ang': [round(-math.degrees(math.atan2(dz, max(dist, 1e-6))), 1),
                    round(math.degrees(math.atan2(dy, dx)), 1), 0]}


def views(figures: list[dict]) -> list[dict]:
    """For every figure: the view of its nearest opponent (from his eyes at its chest). A figure without an
    opponent is looked at from in front."""
    out = []
    for i, f in enumerate(figures):
        others = [(math.dist(f['origin'], g['origin']), j, g) for j, g in enumerate(figures) if g['team'] != f['team']]
        if others:
            _, j, g = min(others)
            out.append(_view(f"{g['team']}{j + 1}_sees_{f['team']}{i + 1}", g['origin'], f['origin'], OUT_OF_FIGURE))
        else:
            rad = math.radians(f['yaw'])
            eye = [f['origin'][0] + math.cos(rad) * ALONE_VIEW, f['origin'][1] + math.sin(rad) * ALONE_VIEW,
                   f['origin'][2]]
            out.append(_view(f"front_of_{f['team']}{i + 1}", eye, f['origin']))
    return out


def add_figures(p, vmap, op, refs):
    allowed = {'op', 'figures', 'name', 'ref'}
    if set(op) - allowed:
        raise OpError('unknown add_figures field(s): ' + ', '.join(sorted(set(op) - allowed))
                      + '; fields: figures [{team, origin, yaw, model}], name')
    name = op.get('name', 'readability')
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', name):
        raise OpError('name is a lowercase identifier of at most 32 characters')
    raw = op.get('figures')
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_FIGURES:
        raise OpError(f'figures is a list of 1..{MAX_FIGURES} of {{"team": "t" | "ct", "origin": [x, y, z], "yaw": 0}}')
    figures = []
    for i, f in enumerate(raw):
        label = f'figures[{i}]'
        if not isinstance(f, dict) or set(f) - {'team', 'origin', 'yaw', 'model'}:
            raise OpError(f'{label} is {{"team": "t" | "ct", "origin": [x, y, z], "yaw": 0, "model": optional}}')
        team = f.get('team')
        if team not in MODELS:
            raise OpError(f'{label}.team is "t" or "ct"')
        yaw = f.get('yaw', 0)
        if isinstance(yaw, bool) or not isinstance(yaw, (int, float)) or not math.isfinite(yaw):
            raise OpError(f'{label}.yaw is a number of degrees (0 looks along +X, 90 along +Y)')
        model = f.get('model', MODELS[team])
        if not isinstance(model, str) or not model.endswith('.vmdl') or not p.game.exists(model + '_c'):
            raise OpError(f'{label}.model must be a stock player model (asset_search(kind="model", "agents")); '
                          f'the default is {MODELS[team]}')
        figures.append({'team': team, 'origin': _vec(f.get('origin'), f'{label}.origin'), 'yaw': float(yaw),
                        'model': model})
    replaced = 0
    for group in _groups(vmap):
        try:
            same = json.loads(group.get(KEY)).get('name') == name
        except (ValueError, AttributeError):
            same = False
        if same:
            vmap.remove(group)
            replaced += 1
    centre = [sum(f['origin'][k] for f in figures) / len(figures) for k in range(3)]
    group = vmap.new_group(f'figures:{name}', centre)
    vmap.attach(group)
    defaults = p.catalog.defaults('prop_dynamic')
    ids = []
    for i, f in enumerate(figures):
        ent = vmap.new_entity('prop_dynamic', {'model': f['model'], 'solid': 0,
                                               'targetname': f"{name}_{f['team']}{i + 1}"},
                              f['origin'], [0, f['yaw'], 0], defaults=defaults)
        vmap.attach(ent, group)
        ids.append(ent['nodeID'])
    group.set(KEY, 'string', json.dumps({'name': name, 'count': len(figures)}, separators=(',', ':')))
    ref = op.get('ref')
    if ref:
        refs[ref] = group['nodeID']
    return {'op': 'add_figures', 'id': group['nodeID'], 'ids': ids, 'figures': len(figures),
            **({'replaced': replaced} if replaced else {}), **({'ref': ref} if ref else {}),
            'views': views(figures),
            'note': 'build with light (full) and pass views to game_screenshot; remove the figures before the '
                    f'release: {{"op":"delete","ids":[{group["nodeID"]}]}}'}


def check_figures(project, m) -> list[dict]:
    out = []
    for group in _groups(m):
        try:
            meta = json.loads(group.get(KEY))
        except ValueError:
            meta = {}
        out.append(checks.problem('warning', 'figures_in_map', group['nodeID'],
            f"{meta.get('count', '?')} stand-in players ({meta.get('name', 'figures')}) stand in the map: props "
            "for readability frames, not for players to meet",
            f'Remove them before the release: {{"op":"delete","ids":[{group["nodeID"]}]}}'))
    return out
