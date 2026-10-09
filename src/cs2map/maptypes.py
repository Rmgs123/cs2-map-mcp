"""Map types: what kind of map this is decides which checks and game tests apply (never what may be built).

The type is stored on the world node (`cs2map_type`). Maps without one are inferred as before: an add_story
script -> story, an add_mode -> mode, otherwise competitive. New maps get the type map_create was given
(`custom` by default: only checks that hold for every map).
"""
from __future__ import annotations

KEY = 'cs2map_type'
DEFUSE_ONLY = {'buyzone_missing', 'buyzone_not_over_spawn', 'bombsite_missing'}
TYPES = {
    'competitive': {'about': 'defuse, hostage or wingman: buy zones, objectives, 5+ spawns per team, bot navigation',
                    'skip': set(),
                    'tests': ['visual_check', 'playtest(kind="timings")', 'playtest(kind="competitive")']},
    'story': {'about': 'single player story: stages, triggers, HUD; competitive rules do not apply',
              'skip': DEFUSE_ONLY | {'spawn_count'},
              'tests': ['visual_check', 'script_test(steps="auto")', 'route_test(route="auto")']},
    'mode': {'about': 'a native game mode (deathmatch, arms race, surf, bhop, kz, aim, casual, wingman)',
             'skip': set(),
             'tests': ['visual_check', 'playtest(kind="mode")', 'playtest(kind="course") for movement maps']},
    'sandbox': {'about': 'a playground or showcase of mechanics, no rounds to win',
                'skip': DEFUSE_ONLY | {'spawn_count'},
                'tests': ['visual_check', 'script_test per mechanic (game_reset before and after)']},
    'hideseek': {'about': 'hide and seek: hiders (T) and seekers (CT), a hide timer, then the hunt',
                 'skip': DEFUSE_ONLY,
                 'tests': ['visual_check', 'script_test of the round flow', 'route_test to hiding spots']},
    'custom': {'about': 'anything else (a new kind of map): only checks that hold for every map',
               'skip': DEFUSE_ONLY | {'spawn_count'},
               'tests': ['visual_check', 'the tests your own rules need (script_test, route_test, playtest)']},
}
NAV_OPTIONAL = {'story', 'sandbox', 'custom', 'hideseek'}


def check_type(value) -> str:
    if value not in TYPES:
        raise ValueError(f'map type must be one of {", ".join(TYPES)}')
    return value


def stored(m) -> str | None:
    value = m.world.get(KEY) if hasattr(m, 'world') else None
    return value if value in TYPES else None


def store(m, value: str) -> None:
    m.world.set(KEY, 'string', check_type(value))


def effective(m, story: bool, mode: str | None) -> tuple[str, str]:
    """(type, how it was decided): stored > story script > add_mode > competitive."""
    value = stored(m)
    if value:
        return value, 'set'
    if story:
        return 'story', 'inferred from add_story'
    if mode:
        return 'mode', 'inferred from add_mode'
    return 'competitive', 'default for maps without a type'


def apply(problems: list[dict], kind: str) -> tuple[list[dict], list[str]]:
    """Drop the checks the type does not need; bot navigation is advice where bots are optional."""
    rules = TYPES[kind]['skip']
    skipped = sorted({p['code'] for p in problems if p['code'] in rules})
    out = []
    for p in problems:
        if p['code'] in rules:
            continue
        if p['code'] == 'nav_seed_missing' and kind in NAV_OPTIONAL and p['severity'] == 'error':
            p = {**p, 'severity': 'warning', 'message': 'No point_nav_walkable: bots cannot move',
                 'fix': 'Only needed if the map uses bots; add a seed on the floor and build full.'}
        out.append(p)
    return out, skipped


def set_type_op(project, m, op, refs):
    extra = set(op) - {'op', 'type'}
    if extra:
        raise ValueError(f'set_map_type takes only type; unknown {sorted(extra)}')
    store(m, op.get('type'))
    return {'op': 'set_map_type', 'type': op['type'], 'checks': TYPES[op['type']]['about']}
