"""Test helpers around the running game: the crash report and the one-call reset between scenarios."""
from __future__ import annotations

import json
import time
from pathlib import Path

import psutil

from . import crashdump, game

DUMP_PATTERNS = ('cs2_*_crash.mdmp', 'cs2_*_error.mdmp', 'cs2_*_accessviolation.mdmp')
# One line of advice per known failure, shown beside the facts.
HINTS = {
    ('0xC00000FD', 'server.dll'): 'stack overflow in the game server: a script or entity recursed (05.10: env_beam did exactly this)',
    ('0xC0000005', 'server.dll'): 'access violation in the game server: a script call or entity input the server cannot handle',
    ('0x80000003', None): 'the engine stopped on a fatal error: read the FATAL ERROR line below (a missing or misplaced resource)',
}


def dumps(s) -> list[Path]:
    """Minidumps of CS2 (crash / error / access violation), newest first."""
    found = {p for pattern in DUMP_PATTERNS for p in s.game_bin.glob(pattern)}
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def recent_dump(s, minutes: float = 30) -> dict | None:
    found = dumps(s)
    if not found:
        return None
    age = (time.time() - found[0].stat().st_mtime) / 60
    return {'file': found[0].name, 'age_minutes': round(age, 1)} if age <= minutes else None


def _launch_time(s):
    cur = game.session(s)
    try:
        return psutil.Process(cur['pid']).create_time() if cur else None
    except psutil.Error:
        return None


def crash_report(project, dump: str | None = None) -> dict:
    """The newest CS2 minidump: exception, module, console tail. Short answer; the full text goes to logs/.

    `crashed_since_launch` compares the dump time with the start of the CS2 that runs now (None if none runs)."""
    s = project.s
    found = dumps(s)
    if dump:
        path = next((p for p in found if p.name == dump), None)
        if path is None:
            raise ValueError(f'no such dump; newest are {[p.name for p in found[:3]]}')
    elif found:
        path = found[0]
    else:
        return {'crash': False, 'note': 'no CS2 minidump found in ' + str(s.game_bin)}
    launched = _launch_time(s)
    mtime = path.stat().st_mtime
    info = crashdump.parse(path)
    tail = crashdump.console_tail(path, 40)
    exc = info['exception'] or {}
    module = (info['module'] or {}).get('name')
    hint = HINTS.get((exc.get('code'), module)) or HINTS.get((exc.get('code'), None))
    log = s.logs / f'crash-{path.stem}.json'
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({**info, 'time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime)),
                               'console_tail': tail, 'hint': hint}, ensure_ascii=False, indent=1), encoding='utf-8')
    out = {'crash': True, 'dump': path.name, 'time': time.strftime('%H:%M:%S', time.localtime(mtime)),
           'age_minutes': round((time.time() - mtime) / 60, 1),
           'crashed_since_launch': None if launched is None else mtime >= launched,
           'exception': f'{exc.get("name")} {exc.get("code")}', 'module': module and f'{module}+{info["module"]["offset"]}',
           'last_lines': [x[:160] for x in tail[-8:]], 'log': str(log)}
    if hint:
        out['hint'] = hint
    if launched is not None and mtime < launched:
        out['note'] = 'this dump is older than the running CS2: no crash since it was started'
    return out


# What a scenario may leave behind and how to undo it. Defaults are CS2's own (weapon_recoil_scale 2).
RELEASE_KEYS = ('-attack', '-attack2', '-use', '-forward', '-back', '-left', '-right', '-turnleft', '-turnright',
                '-jump', '-duck', '-reload', '-sprint')
RESET_CVARS = ('weapon_accuracy_nospread 0', 'weapon_recoil_scale 2', 'bot_stop 0', 'bot_dont_shoot 0', 'cl_drawhud 1',
               'r_drawviewmodel 1')


def control_scripts(project, map: str | None) -> list[str]:
    """Targetnames of the point_scripts of controllable objects on a map (they all take the `Exit` input)."""
    if not map:
        return []
    m = project.load(map)
    return [n['entity_properties'].get('targetname') for n in m.entities(classname='point_script')
            if n.get('cs2map_control') and n['entity_properties'].get('targetname')]


def reset(project, map: str | None = None) -> dict:
    """Undo what scenarios change, in one call: leave every controllable object, noclip off, HUD and viewmodel on,
    default accuracy/recoil, no bots, held keys released, `mp_restartgame`, wait for a live player."""
    s = project.s
    done = []
    with game.exclusive():
        game.ensure_alive(s)
        if map is not None:
            # A restarted CS2 (or another map) has nothing to reset: load the requested map first.
            if game.ensure_map(s, map):
                done.append(f'loaded {map}')
        if map is None:
            try:
                map = json.loads(game.LOADED.read_text(encoding='utf-8')).get('map')
            except (OSError, ValueError):
                map = None
        scripts = control_scripts(project, map)
        game.command(s, '\n'.join(['sv_cheats 1', *RELEASE_KEYS]), 0.2)
        for name in scripts:
            game.command(s, f'ent_fire {name} RunScriptInput Exit', 0.1)
        if scripts:
            done.append(f'exit {len(scripts)} controllable scripts')
        state = game.player_state(s)
        player = game._live_player(state)
        if player and player.get('noclip'):
            game.command(s, 'noclip', 0.2)
            done.append('noclip off')
        game.command(s, '\n'.join(['bot_kick', 'bot_quota 0', *RESET_CVARS, 'mp_restartgame 1']), 0.5)
        done += ['bots removed', 'accuracy/recoil/HUD/viewmodel default', 'inputs released', 'mp_restartgame 1']
        time.sleep(3.5)
        game.wait_for_player(s, timeout=40)
        after = game._live_player(game.player_state(s))
    return {'ok': bool(after), 'map': map, 'done': done,
            'player': after and {'health': after.get('health'), 'noclip': after.get('noclip')}}
