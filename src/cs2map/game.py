"""Run CS2 Workshop Tools with the addon, send console commands, open maps in Hammer,
take screenshots of the game / Hammer windows.

Commands go through the VConsole port (29000) that every Workshop Tools session
opens, so this works both for a CS2 we started and for one the user started from
Steam (as long as it runs our addon).
"""
from __future__ import annotations

import ctypes
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import psutil

from . import system, vconsole
from .config import PROJECT_ROOT, Settings

STATE = PROJECT_ROOT / '.local' / 'state' / 'launched.json'
LOADED = PROJECT_ROOT / '.local' / 'state' / 'loaded_map.json'
FRAMES = PROJECT_ROOT / '.local' / 'frames'
LOCK = PROJECT_ROOT / '.local' / 'locks' / 'cs2.lock'
LOCK_STALE_SECONDS = 15 * 60
SW_RESTORE = 9
_LOCK_LOCAL = threading.local()
_PROCESS_LOCK = threading.RLock()


class GameError(RuntimeError):
    pass


def _status_has_map(lines: list[str], map_name: str) -> bool:
    """Return true only when CS2 status names *map_name* as the loaded map.

    ``map_workshop`` prints a successful state before the actual server map has
    switched.  The status line is the authoritative signal and is deliberately
    matched in a few formats used by Workshop Tools builds.
    """
    wanted = map_name.casefold()
    for line in lines:
        low = line.casefold()
        if 'loaded spawngroup' not in low and 'mapload' not in low:
            continue
        if re.search(rf"(?:\[|\b)\s*(?:\d+\s*:\s*)?{re.escape(wanted)}\s*(?:\||\]|\s|$)", low):
            return True
        # Some builds print ``map : foo`` without the spawngroup decoration.
        if re.search(rf"\bmap\s*[:=]\s*{re.escape(wanted)}\b", low):
            return True
    return False


def _looks_like_player(lines: list[str]) -> bool:
    """Conservative check that getpos answered and the team menu is absent."""
    if not any(ln.lstrip().lower().startswith('setpos') for ln in lines):
        return False
    joined = '\n'.join(lines).casefold()
    menu_markers = ('choose a team', 'select a team', 'team select', 'выберите команду',
                    'press 1 for', 'press 2 for', 'all teams are full')
    return not any(marker in joined for marker in menu_markers)


def _live_player(state: dict) -> dict | None:
    return next((p for p in state.get('players', []) if not p.get('bot')
                 and p.get('slot') == 0 and p.get('team') in (2, 3)
                 and p.get('alive') and not p.get('observing') and p.get('health', 0) > 0), None)


def _getpos_values(lines):
    for line in lines:
        m = re.search(r'setpos\s+([-+\d.e]+)\s+([-+\d.e]+)\s+([-+\d.e]+);setang\s+([-+\d.e]+)\s+([-+\d.e]+)', line, re.I)
        if m:
            return tuple(float(m.group(i)) for i in range(1,6))
    return None


def player_state(s: Settings, install: bool = True) -> dict:
    """Read a token-matched server snapshot, including team and live pawn.

    The helper is spawned in the running map, without writing that map. This
    avoids trusting getpos, which also answers for a dead observer.
    """
    token = uuid.uuid4().hex[:12]
    prefix = f'C_MCP_STATE {token} '
    lines = query(s, f'c_mcp_quality_probe {token}', (prefix, 'Unknown command'), timeout=2.0)
    for line in lines:
        if line.startswith(prefix):
            state = json.loads(line[len(prefix):])
            if 'players' not in state:          # current runtime: one C_MCP_STATEP line per player
                part = f'C_MCP_STATEP {token} '
                state['players'] = [json.loads(x[len(part):]) for x in lines if x.startswith(part)]
                if len(state['players']) != state.get('count', len(state['players'])):
                    raise GameError('player diagnostics were cut off; retry')
            return state
    if install:
        # No answer: the map has no diagnostics entity (CS2 does not always print
        # "unknown command"). Spawn it at runtime; the VMAP is not changed.
        from .scripting import runtime_script
        rel = runtime_script(s)
        command(s, f'sv_cheats 1\nent_create point_script {{ cs_script {rel} targetname c_mcp_runtime }}', wait=0.6)
        return player_state(s, install=False)
    # Maps made before the diagnostics entity was added cannot expose pawn
    # state. Return an explicit uninstrumented state so map transitions still
    # work; views refuse this state and ask for a rebuilt map.
    lines_status = status(s)
    pos = getpos(s)
    if any('active' in x.casefold() for x in lines_status):
        return {'instrumented': False, 'players': [], 'getpos': pos[-1:]}
    raise GameError('CS2 did not return live player diagnostics: ' + ' | '.join(lines[-6:]))


def frame_check(path: str | Path, captured_at: float | None = None,
                previous_signature: str | None = None) -> dict:
    """Inspect a captured frame for a loading/blank image and stale output.

    This is intentionally a conservative heuristic: dark in-game rooms are
    valid, while a fully uniform or almost entirely black PrintWindow result is
    rejected.  The returned signature lets callers detect an unchanged frame
    after moving the camera without pretending that image analysis is OCR.
    """
    import numpy as np
    from PIL import Image

    p = Path(path)
    result = {'ok': False, 'reasons': [], 'fresh': False, 'signature': None}
    if not p.exists() or p.stat().st_size <= 0:
        result['reasons'].append('capture file is missing or empty')
        return result
    if captured_at is None:
        captured_at = time.time() - 1e-3
    result['fresh'] = p.stat().st_mtime >= captured_at - 0.25
    if not result['fresh']:
        result['reasons'].append('capture file predates this request')
    try:
        with Image.open(p) as image:
            image = image.convert('RGB')
            # A small sample keeps this check cheap for 4K captures.
            sample = image.copy()
            sample.thumbnail((320, 180))
            pixels = np.asarray(sample, dtype=np.float32)
            mean = float(pixels.mean())
            variance = float(pixels.var(axis=(0, 1)).mean())
            black_fraction = float(np.all(pixels <= 4, axis=2).mean())
            # Share of pixels near the median colour: the fast build's "Preview Lighting" line and the vignette
            # lift the variance of a blank frame (25.10: a camera inside the floor passed; 0.99 there, real
            # views of 500 frames at most 0.85).
            median = np.median(pixels.reshape(-1, 3), axis=0)
            flat = float(np.all(np.abs(pixels - median) <= 16, axis=2).mean())
            digest = hashlib.sha256(sample.tobytes()).hexdigest()[:16]
            result.update({'size': list(image.size), 'mean': round(mean, 3),
                           'variance': round(variance, 3),
                           'black_fraction': round(black_fraction, 4), 'flat': round(flat, 4),
                           'signature': digest})
    except Exception as exc:
        result['reasons'].append(f'cannot inspect capture: {exc}')
        return result
    if result.get('size', [0, 0])[0] < 320 or result.get('size', [0, 0])[1] < 180:
        result['reasons'].append('capture is smaller than a game frame')
    if result.get('variance', 0) < 0.5:
        result['reasons'].append('frame is uniform (likely loading/blank)')
    elif result.get('flat', 0) > 0.95:
        result['reasons'].append('frame is uniform (the camera is inside geometry, or loading)')
    elif result.get('black_fraction', 0) > 0.985 and result.get('mean', 0) < 5:
        result['reasons'].append('frame is almost entirely black (likely loading)')
    if previous_signature and result.get('signature') == previous_signature:
        result['reasons'].append('frame did not change after the camera move')
    result['ok'] = result['fresh'] and not result['reasons']
    return result


@contextmanager
def exclusive(owner: str | None = None, wait: float = 600):
    """Cross-process lock around using the single CS2 instance (several agents may share it).
    The lock file holds pid/owner/time; it is stale if the pid is dead or it is older than 15 min."""
    with _PROCESS_LOCK:
        if getattr(_LOCK_LOCAL, 'depth', 0):
            _LOCK_LOCAL.depth += 1
            try:
                yield
            finally:
                _LOCK_LOCAL.depth -= 1
            return
        with _exclusive_file(owner, wait):
            _LOCK_LOCAL.depth = 1
            try:
                yield
            finally:
                _LOCK_LOCAL.depth = 0
                vconsole.release()    # the next lock holder may be another process


@contextmanager
def _exclusive_file(owner, wait):
    owner = owner or os.environ.get('CS2MAP_AGENT', 'default')
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump({'pid': os.getpid(), 'owner': owner, 'time': time.time()}, f)
            break
        except FileExistsError:
            try:
                info = json.loads(LOCK.read_text(encoding='utf-8'))
                stale = (not psutil.pid_exists(info.get('pid', -1))
                         or time.time() - info.get('time', 0) > LOCK_STALE_SECONDS)
            except (OSError, ValueError):
                # A concurrent creator may not have written its JSON yet.
                # Never steal a newly created lock in that short interval.
                try:
                    stale = time.time() - LOCK.stat().st_mtime > 5
                except FileNotFoundError:
                    continue
                info = {}
            if stale:
                LOCK.unlink(missing_ok=True)
                continue
            if time.monotonic() > deadline:
                raise GameError(f'CS2 is busy (used by {info.get("owner")}); try again later') from None
            time.sleep(1)
    try:
        yield
    finally:
        LOCK.unlink(missing_ok=True)


def _launched_pids() -> set[int]:
    try:
        return set(json.loads(STATE.read_text(encoding='utf-8')))
    except (OSError, ValueError):
        return set()


def _remember(pid: int) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    pids = {p for p in _launched_pids() if psutil.pid_exists(p)} | {pid}
    STATE.write_text(json.dumps(sorted(pids)), encoding='utf-8')


_SESSION_CACHE: dict = {}


def session(s: Settings) -> dict | None:
    """The running CS2 Workshop Tools session with our addon, if any.

    Scanning every process's command line takes ~0.5 s, so a found session is
    remembered and later only re-checked by pid and start time."""
    cached = _SESSION_CACHE.get(s.addon)
    if cached:
        try:
            proc = psutil.Process(cached['pid'])
            if proc.is_running() and proc.create_time() == cached['create_time']:
                return {**cached['info'], 'launched_by_tool': cached['pid'] in _launched_pids()}
        except psutil.Error:
            pass
        _SESSION_CACHE.pop(s.addon, None)
    for p in system.cs2_processes():
        if p['mode'] == 'tools' and p['addon'] == s.addon:
            try:
                _SESSION_CACHE[s.addon] = {'pid': p['pid'], 'info': p,
                                           'create_time': psutil.Process(p['pid']).create_time()}
            except psutil.Error:
                pass
            return {**p, 'launched_by_tool': p['pid'] in _launched_pids()}
    # A release addon (map_build "release") is played from any Workshop Tools session: `map_workshop <addon> <map>`
    # mounts that addon for the map (verified 09.10).
    if (s.content_addon / 'cs2map_release.json').is_file():
        for p in system.cs2_processes():
            if p['mode'] == 'tools':
                return {**p, 'launched_by_tool': p['pid'] in _launched_pids()}
    return None


def launch(s: Settings, map_name: str | None = None, width: int = 1600, height: int = 900,
           hammer_map: str | None = None, wait_seconds: float = 150) -> dict:
    """Start CS2 Workshop Tools with our addon (or reuse a running one).
    map_name: load it in the game view; hammer_map: open that map in Hammer."""
    current = session(s)
    if current is None:
        others = system.cs2_processes()
        if others:
            o = others[0]
            raise GameError(f'CS2 is running in {o["mode"]} mode' + (f' with addon {o["addon"]}' if o['addon'] else '')
                            + f'. Close it first (or start Workshop Tools with addon {s.addon}); '
                              'this tool never closes a CS2 it did not start.')
        argv = [str(s.cs2_exe), '-addon', s.addon, '-tools', '-steam', '-retail', '-gpuraytracing', '-novid',
                '+sv_steamauth_enforce', '0']
        if hammer_map:
            argv += ['-tool', 'hammer', '-asset', f'maps/{hammer_map}.vmap']
        proc = subprocess.Popen(argv, cwd=str(s.game_bin), creationflags=subprocess.DETACHED_PROCESS,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        _remember(proc.pid)
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline and not vconsole.available():
            if proc.poll() is not None:
                raise GameError(f'CS2 exited during startup (code {proc.returncode})')
            time.sleep(2)
        if not vconsole.available():
            return {'state': 'started, console not reachable yet', 'pid': proc.pid}
        time.sleep(5)   # let the tools finish initialising before the first command
        state = 'started'
    else:
        state = 'already running'
        if hammer_map:
            hammer_open(s, hammer_map)
    out = {'state': state, 'addon': s.addon}
    if map_name:
        out['console'] = load_map(s, map_name)
    return out


HANG_SECONDS = 30.0
RESTART_HINT = ('CS2 does not respond: ask the user to restart CS2 Workshop Tools with addon {addon}. '
                'Nothing was killed.')


def not_running_message(s: Settings) -> str:
    """Why there is no session: never started, or crashed a moment ago (then the dump is named)."""
    text = f'CS2 Workshop Tools with addon {s.addon} is not running: start it with game_launch'
    try:
        from . import gametest
        recent = gametest.recent_dump(s, minutes=30)
    except Exception:
        recent = None
    if recent:
        text += (f'. It seems to have crashed {recent["age_minutes"]} min ago ({recent["file"]}): '
                 'call game_crash_report for the module and exception before relaunching')
    return text


_HWNDS: dict = {'pid': None, 'at': 0.0, 'list': []}


def hung(s: Settings) -> bool:
    """True when a window of our CS2 process is flagged "Not responding" right now.

    Window handles are cached for 10 s (listing windows walks all processes); a vanished handle refreshes the cache."""
    cur = session(s)
    if not cur:
        return False
    now = time.monotonic()
    if _HWNDS['pid'] != cur['pid'] or now - _HWNDS['at'] > 10 or not all(system.user32.IsWindow(h) for h in _HWNDS['list']):
        _HWNDS.update(pid=cur['pid'], at=now,
                      list=[w.hwnd for w in system.windows(('cs2.exe',)) if w.pid == cur['pid']])
    return any(system.is_hung(h) for h in _HWNDS['list'])


def ensure_alive(s: Settings, wait: float = HANG_SECONDS) -> dict:
    """A live, responding CS2 session or a clear error. A hung window gets `wait` seconds to recover
    (map loads freeze it for a while); if it does not, the user must restart CS2: we never kill it."""
    cur = session(s)
    if cur is None:
        raise GameError(not_running_message(s))
    deadline = time.monotonic() + wait
    while hung(s):
        if time.monotonic() >= deadline:
            raise GameError(f'CS2 is not responding ({int(wait)} s). ' + RESTART_HINT.format(addon=s.addon))
        time.sleep(1.0)
    return cur


def command(s: Settings, cmd: str, wait: float = 1.5) -> list[str]:
    ensure_alive(s)
    lines = [c.strip() for c in cmd.splitlines() if c.strip()]
    return vconsole.run(lines, wait)


_LAST_QUERY: dict[str, float] = {}


def query(s: Settings, cmd: str, pattern: str | tuple[str, ...], timeout: float = 5.0,
          retries: int = 0) -> list[str]:
    """Run a command and wait for its own output (`pattern`), e.g. 'status' -> '#end'.

    CS2 silently drops a server command repeated within a few milliseconds, so the same
    command is spaced by at least 0.25 s and, with `retries`, re-sent after a timeout."""
    ensure_alive(s)
    key = cmd.split()[0] if cmd.split() else cmd
    lines: list[str] = []
    for _ in range(retries + 1):
        gap = time.monotonic() - _LAST_QUERY.get(key, 0.0)
        if gap < 0.25:
            time.sleep(0.25 - gap)
        ok, lines = vconsole.run_until([c.strip() for c in cmd.splitlines() if c.strip()], pattern, timeout)
        _LAST_QUERY[key] = time.monotonic()
        if ok:
            return lines
    ensure_alive(s, wait=0)          # no answer: a hung window explains it better than an empty list
    return lines


def status(s: Settings) -> list[str]:
    return query(s, 'status', '#end', timeout=3.0, retries=1)


def getpos(s: Settings) -> list[str]:
    return query(s, 'getpos', 'setpos', timeout=3.0, retries=1)


def load_map(s: Settings, name: str, timeout: float = 120) -> list[str]:
    """Load (or reload) a compiled addon map in the game view and wait until it is in game.

    `map <name>` loads without addons ("invalid map name" for addon maps); Hammer's
    Run uses `map_workshop <addon> <map>` ("Loading custom game ... with map ...")."""
    ensure_alive(s)
    started = time.monotonic()
    official = s.map_vpk(name) != s.compiled_map(name)
    # Official maps (references for "build like this" work) load with plain `map`.
    load = f'map {name}' if official else f'map_workshop {s.addon} {name}'
    ok, out = vconsole.run_until([load], '-> CSGO_GAME_UI_STATE_INGAME', timeout)
    bad = [ln for ln in out if 'invalid map' in ln.lower() or "can't find" in ln.lower()]
    if bad or not ok:
        raise GameError(f'CS2 could not load {name}: ' + ' | '.join((bad or out)[-5:]))
    # The UI transition is not enough: a stale map can still report INGAME.
    status_deadline = time.monotonic() + max(5.0, timeout - (time.monotonic() - started))
    status_lines: list[str] = []
    while time.monotonic() < status_deadline:
        status_lines = status(s)
        if _status_has_map(status_lines, name):
            break
        time.sleep(0.5)
    else:
        raise GameError(f'CS2 entered the game UI but status did not confirm map {name!r}; '
                        f'last status: {" | ".join(status_lines[-5:])}')
    if official:
        command(s, 'bot_kick\nbot_quota 0', wait=0.3)      # official modes add bots that shoot the camera
    fast_start(s)
    vpk = s.map_vpk(name)
    LOADED.parent.mkdir(parents=True, exist_ok=True)
    LOADED.write_text(json.dumps({'pid': session(s)['pid'], 'map': name,
                                  'vpk_mtime': vpk.stat().st_mtime if vpk.exists() else None}), encoding='utf-8')
    wait_for_player(s, timeout=max(5.0, timeout - (time.monotonic() - started)))
    return [ln for ln in out if 'error' in ln.lower() and 'annotation' not in ln.lower()][:20]


# Test sessions start live: the mode config sets mp_freezetime 15 on every load and the first round
# plays a team intro.  Measured on a_kit_accept (04.10): load -> unfrozen player 25.8 s by default,
# 10.8 s with mp_freezetime 0 after the load, 6.0 s with the team intro off too.  Setting the cvar
# before the load does nothing (the config overrides it).
FAST_START = ('mp_freezetime 0', 'mp_team_intro_time 0')
LAST_FAST_START: dict = {}


def fast_start(s: Settings) -> str | None:
    """No freeze period and no team intro on the loaded map; returns mp_freezetime read back."""
    value = None
    for _ in range(2):
        lines = query(s, '\n'.join(FAST_START) + '\nmp_freezetime', 'mp_freezetime =', timeout=3.0)
        found = [ln.split('=', 1)[1].strip().split()[0] for ln in lines if 'mp_freezetime =' in ln]
        value = found[-1] if found else None
        if value == '0':
            break
    LAST_FAST_START.update(time=time.time(), mp_freezetime=value)
    return value


def _live_after_restart(s: Settings, seconds: float = 8.0) -> dict | None:
    """The live local player, waiting out a round restart. After a load the mode's bots join the other team and
    the game restarts the round once ("Game commencing"): for ~1-3 s the player is dead or an observer."""
    deadline = time.monotonic() + seconds
    while True:
        player = _live_player(player_state(s))
        if player or time.monotonic() >= deadline:
            return player
        time.sleep(0.5)


def wait_for_player(s: Settings, timeout: float = 45) -> None:
    """The UI reports 'in game' before the loading screen is gone; wait until a player exists
    (getpos answers with 'setpos ...'), joining a team if needed."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = player_state(s)
        if _live_player(state) or (not state.get('instrumented', True)
                                   and any('active' in x.casefold() for x in status(s))):
            return
        command(s, 'engine_no_focus_sleep 0\nsv_cheats 1\njointeam 2 1\nrespawn_player', wait=0.4)
        time.sleep(1.0)
    raise GameError('map loaded but no live player in a team appeared in time; '
                    'the team menu may be blocked or spawns may be missing')


def ensure_map(s: Settings, name: str, deadline: float | None = None) -> bool:
    """Load `name` unless exactly this compiled version is already loaded. Returns True if (re)loaded."""
    cur = session(s)
    vpk = s.map_vpk(name)
    try:
        state = json.loads(LOADED.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    if (cur and state.get('pid') == cur['pid'] and state.get('map') == name
            and vpk.exists() and state.get('vpk_mtime') == vpk.stat().st_mtime
            and _status_has_map(status(s), name)):
        return False
    timeout = 120.0 if deadline is None else deadline - time.monotonic()
    if timeout <= 1:
        raise GameError(f'no time left to load map {name!r}')
    load_map(s, name, timeout=min(120.0, timeout))
    return True


def hammer_open(s: Settings, name: str) -> list[str]:
    return command(s, f'open_asset maps/{name}.vmap', wait=3)


def _window(title_prefix: str) -> system.Window:
    wins = [w for w in system.windows(('cs2.exe',)) if w.title.startswith(title_prefix)]
    if not wins:
        raise GameError(f'no CS2 window titled "{title_prefix}..."')
    w = wins[0]
    if w.minimized:
        ctypes.windll.user32.ShowWindow(w.hwnd, SW_RESTORE)
        time.sleep(0.8)
        w = [x for x in system.windows(('cs2.exe',)) if x.hwnd == w.hwnd][0]
    return w


def _capture(s: Settings, w: system.Window, name: str, captured_at: float | None = None,
             previous_signature: str | None = None, folder: Path | None = None) -> dict:
    captured_at = captured_at or time.time()
    stamp = _dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]
    folder = folder or s.screenshots
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{name}-{stamp}.png'
    info = system.capture(w.hwnd, path)
    if info['blank']:
        info = system.capture_screen_region(w.rect, path)
        info['method'] = 'screen region (the window must be visible and not covered)'
    info['frame_check'] = frame_check(path, captured_at, previous_signature)
    return {'name': name, **info}


def views(s: Settings, map_name: str | None, shots: list[dict], hide_hud: bool = True,
          settle: float = 1.5, deadline: float | None = None) -> dict:
    """Atomically (under the CS2 lock): make sure `map_name` is loaded, then for each
    shot {name, pos:[x,y,z], ang:[pitch,yaw,roll], cmd?, wait?, keep?} move the camera, run `cmd`,
    wait and capture the game view. `keep:true` leaves the player as is (no noclip, no setpos;
    only `ang` is applied): use it while a script owns the camera or the player's movement.
    `deadline` (time.monotonic()) bounds loading and shots."""
    if not shots:
        raise GameError('give at least one view: {"name": "...", "pos": [x,y,z], "ang": [pitch,yaw,0]}')
    with exclusive():
        # CS2 does not finish loading nor render reliably in the background: focus the game window
        # for the duration of the shots (the previously focused window gets focus back afterwards).
        previous = system.foreground()
        system.focus(_window('Counter-Strike 2').hwnd)
        try:
            return _views_locked(s, map_name, shots, hide_hud, settle, deadline)
        finally:
            if previous:
                system.focus(previous)


_CONSOLE_NOISE = re.compile(r'Unable to determine cubemap texture|^Annotation:|CQ: Server set re-sync')


@contextmanager
def focused():
    """Keep the game window in front for real-input actions. In the background the client
    ignores a server setang for its own user commands: bullets keep flying the old way."""
    previous = system.foreground()
    system.focus(_window('Counter-Strike 2').hwnd)
    try:
        yield
    finally:
        if previous:
            system.focus(previous)


def _console_digest(lines: list[str], grep: str | None = None) -> list[str]:
    """Console lines printed for a view: the ones matching `grep` (up to 20), else the last 8 non-noise."""
    lines = [ln for ln in lines if ln.strip() and not _CONSOLE_NOISE.search(ln)]
    if grep:
        rx = re.compile(grep, re.I)
        return [ln for ln in lines if rx.search(ln)][:20]
    return lines[-8:]


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise GameError('game view deadline exceeded')


def _at_view(actual, expected, angle) -> bool:
    pos_ok = expected is None or all(abs(actual[j] - expected[j]) <= 2 for j in range(3))
    return pos_ok and all(abs((actual[3 + j] - float(angle[j]) + 180) % 360 - 180) <= 2 for j in range(2))


def _place_camera(s: Settings, v: dict, angle, expected, player: dict, settle: float):
    """noclip + setpos/setang until getpos confirms the view; returns (player, getpos values)."""
    cmds = ['noclip 1']
    if expected is not None:
        cmds.append('setpos {} {} {}'.format(*[float(c) for c in v['pos']]))
    cmds.append('setang {} {} {}'.format(*[float(c) for c in angle]))
    actual = None
    # Right after a (re)load or respawn noclip may not be active yet and the
    # camera falls to the floor; repeat the move until getpos confirms it.
    for attempt in range(3):
        if not player.get('noclip'):
            # Enabling noclip in the same tick as setpos lets the camera fall.
            command(s, 'sv_cheats 1\nnoclip 1', wait=0.1)
            time.sleep(0.4)
        command(s, '\n'.join(cmds), wait=0.3)
        time.sleep(settle)
        player = _live_after_restart(s)
        if not player:
            raise GameError('camera movement failed: the local player died or became an observer')
        actual = _getpos_values(getpos(s))
        if not actual:
            raise GameError('camera movement failed: getpos returned no position after setpos/setang')
        if _at_view(actual, expected, angle) and player.get('noclip'):
            return player, actual
    raise GameError(f'camera did not reach position {v.get("pos")} / angles {angle}: '
                    f'eye at {actual[:3]}, angles {actual[3:]}')


def _aim_kept(s: Settings, angle, settle: float):
    """Only turn the view: the player's movement mode and any script camera stay untouched."""
    actual = None
    for attempt in range(3):
        command(s, 'setang {} {} {}'.format(*[float(c) for c in angle]), wait=0.3)
        time.sleep(settle)
        actual = _getpos_values(getpos(s))
        if not actual:
            raise GameError('camera turn failed: getpos returned nothing after setang')
        if _at_view(actual, None, angle):
            return actual
    raise GameError(f'view did not reach angles {angle}: angles {actual[3:]}')


def _views_locked(s: Settings, map_name: str | None, shots: list[dict], hide_hud: bool, settle: float,
                  deadline: float | None = None) -> dict:
    done: list[dict] = []
    # A free camera is the player's body: looking down it shows the player's own legs (10.10). The body is
    # hidden for the frames and shown again afterwards, also when a view fails.
    body = hide_hud and any(not v.get('keep') for v in shots)
    try:
        return _views_run(s, map_name, shots, hide_hud, settle, deadline, done)
    except GameError as e:
        if done:      # keep the frames already taken usable for the caller
            raise GameError(f'{e}; frames captured before the error: '
                            + ', '.join(f'{x["name"]}={x["path"]}' for x in done)) from None
        raise
    finally:
        if body:
            try:
                command(s, 'ent_fire !self alpha 255\nent_fire weapon_* EnableShadow', wait=0.1)
            except GameError:
                pass


def _views_run(s: Settings, map_name: str | None, shots: list[dict], hide_hud: bool, settle: float,
               deadline: float | None, out: list[dict]) -> dict:
    reloaded = ensure_map(s, map_name, deadline=deadline) if map_name else False
    # Verify the map even when ensure_map reused the state file; it may be stale
    # after another process or a manual map change.
    if map_name:
        lines = status(s)
        if not _status_has_map(lines, map_name):
            raise GameError(f'CS2 status is not on requested map {map_name!r}; '
                            f'last status: {" | ".join(lines[-5:])}')
    wait_for_player(s, timeout=20)
    base = ['engine_no_focus_sleep 0', 'sv_cheats 1'] + (['noclip 1'] if any(not v.get('keep') for v in shots) else [])
    # Untouched test frames (keep) show what the player sees: a scripted gadget may hide the viewmodel.
    keep_all = all(v.get('keep') for v in shots)
    base += ['cl_drawhud 0', 'r_drawviewmodel 0'] if hide_hud else ['cl_drawhud 1'] + ([] if keep_all else ['r_drawviewmodel 1'])
    if hide_hud and not keep_all:
        # The weapon in the hidden body's hands still cast a stick-like shadow into low views (25.10); weapons
        # have no selector for "mine", so all of them lose the shadow for the frames.
        base += ['ent_fire !self alpha 0', 'ent_fire weapon_* DisableShadow']    # shown again in _views_locked
    command(s, '\n'.join(base), wait=0.3)
    ready = player_state(s)
    # During the freeze period (after a load or round restart) noclip cannot hold the
    # camera in the air: wait until the round is live.
    freeze_deadline = time.monotonic() + 25
    froze = bool(ready.get('freeze'))
    while ready.get('freeze') and time.monotonic() < freeze_deadline:
        time.sleep(1.0)
        ready = player_state(s)
    if reloaded or froze:
        # The "match started" banner stays on screen for a few seconds even with cl_drawhud 0.
        time.sleep(3.0)
        ready = player_state(s)
    if not _live_player(ready) and ready.get('instrumented') is not False:
        wait_for_player(s, timeout=15)
        ready = player_state(s)
    if not _live_player(ready):
        if ready.get('instrumented') is False:
            raise GameError('cannot capture views: map needs a point_script diagnostics entity; rebuild it first')
        raise GameError('cannot capture views: no live player in a team (team menu/loading screen is active)')
    previous = None          # (signature, view) of the previous shot
    rejected: list[dict] = []
    for i, v in enumerate(shots):
        _check_deadline(deadline)
        player = _live_after_restart(s)
        if not player:
            raise GameError('cannot capture: the local player died or became an observer')
        angle = v.get('ang')
        if v.get('keep') and angle is None:
            # Untouched view: a script camera reads every setang as mouse input, so neither
            # the freshness challenge nor any turn is sent; the frame is only checked for blank.
            if v.get('cmd'):
                printed = command(s, str(v['cmd']), wait=float(v.get('wait', 1.0)))
            else:
                printed = None
                time.sleep(float(v.get('wait', 0.3)))
            actual = _getpos_values(getpos(s)) or (0, 0, 0, 0, 0)
            shot = _capture(s, _window('Counter-Strike 2'), str(v.get('name') or f'view{i + 1}'), time.time())
            if not shot['frame_check']['ok']:
                rejected.append({'name': shot['name'], 'reasons': ', '.join(shot['frame_check']['reasons']),
                                 'path': shot['path']})
                continue
            shot['player_check'] = {'ok': True, 'team': player['team'], 'alive': player['alive'],
                                    'position': actual[:3], 'angles': actual[3:], 'method': 'keep, untouched view'}
            if printed is not None:
                shot['console'] = _console_digest(printed, v.get('grep'))
            out.append(shot)
            continue
        if angle is None:
            here = player.get('angles')
            if here:
                angle = [here[k] for k in ('pitch', 'yaw', 'roll')]
            else:
                cur = _getpos_values(getpos(s))
                angle = [cur[3], cur[4], 0.0] if cur else [0.0, 0.0, 0.0]
        # Render a deliberately different angle first, then the requested
        # angle. Identical frames prove a stale capture even for a single shot.
        command(s, f'setang {float(angle[0])} {float(angle[1])+17} {float(angle[2])}', wait=0.25)
        time.sleep(max(0.2, settle / 2))
        # The challenge frame only proves freshness; keep it out of screenshots/.
        challenge = _capture(s, _window('Counter-Strike 2'), f'{v.get("name") or "view"}_freshness',
                             folder=FRAMES)
        # views[].pos is the player origin (feet); getpos reports the eye, 64 units higher.
        keep = bool(v.get('keep'))
        if keep and v.get('pos') is not None:
            raise GameError(f'view {v.get("name")!r}: keep:true cannot move the player; drop pos')
        expected = [float(v['pos'][0]), float(v['pos'][1]), float(v['pos'][2]) + 64] if v.get('pos') is not None else None
        if keep:
            actual = _aim_kept(s, angle, settle)
        else:
            player, actual = _place_camera(s, v, angle, expected, player, settle)
        printed = None
        # Commands run with the camera already in place, so triggers, sounds and
        # effects happen where the view is; then `wait` seconds for the effect.
        if v.get('cmd'):
            printed = command(s, str(v['cmd']), wait=float(v.get('wait', 1.0)))
        elif v.get('wait'):
            time.sleep(float(v['wait']))
        _check_deadline(deadline)
        # The command or the wait may have moved the camera (respawn, round end,
        # mp_restartgame): check again right before the capture and put it back.
        player = _live_after_restart(s)
        if not player:
            raise GameError(f'view {v.get("name")!r}: the local player died or became an observer after cmd/wait')
        now = _getpos_values(getpos(s))
        if keep:
            actual = now if now and _at_view(now, None, angle) else _aim_kept(s, angle, settle)
        elif not now or not _at_view(now, expected, angle) or not player.get('noclip'):
            frozen_until = time.monotonic() + 25
            while player_state(s).get('freeze') and time.monotonic() < frozen_until:
                time.sleep(1.0)
            player, actual = _place_camera(s, v, angle, expected, _live_player(player_state(s)) or player, settle)
        else:
            actual = now
        view = [round(x) for x in actual[:5]]
        stale = {challenge['frame_check'].get('signature')}
        if previous and previous[1] != view:
            stale.add(previous[0])     # a different view must not give the same picture
        attempts = 3
        for attempt in range(8):
            captured_at = time.time()
            shot = _capture(s, _window('Counter-Strike 2'), str(v.get('name') or f'view{i + 1}'), captured_at)
            check = shot['frame_check']
            if check.get('signature') in stale:
                check['reasons'].append('frame did not change after the camera move')
                check['ok'] = False
            # A black frame right after a build or load is the map still streaming in: give it longer.
            if any('black' in r or 'uniform' in r for r in check['reasons']):
                attempts = 8
            if check['ok'] or attempt >= attempts - 1:
                break
            time.sleep(1.0)        # the game had not rendered the new view yet: capture again
        if not check['ok']:
            # The other views still run (10.10: one black frame threw away a whole batch); the answer lists it.
            reasons = ', '.join(check['reasons']) or 'unknown frame error'
            rejected.append({'name': shot['name'], 'reasons': reasons, 'path': shot['path']})
            continue
        shot['player_check'] = {'ok': True, 'team': player['team'], 'alive': player['alive'],
                                'position': actual[:3], 'angles': actual[3:],
                                'method': 'server point_script token snapshot'}
        if printed is not None:
            shot['console'] = _console_digest(printed, v.get('grep'))
        previous = (check['signature'], view)
        out.append(shot)
    if rejected and not out:
        first = rejected[0]
        raise GameError(f'capture {first["name"]!r} was rejected: {first["reasons"]}; see {first["path"]}'
                        + (f' (and {len(rejected) - 1} more views)' if len(rejected) > 1 else ''))
    result = {'map': map_name, 'reloaded': reloaded, 'shots': out}
    if rejected:
        result['rejected'] = rejected
    return result


def screenshot(s: Settings, name: str | None = None, pos: list[float] | None = None,
               ang: list[float] | None = None, hide_hud: bool = True, window: str = 'game',
               map_name: str | None = None) -> dict:
    """One capture of the game view (optionally after moving the camera) or of the Hammer window.

    For Hammer, `map_name` must match the map in the window title: a capture of
    another map must not pass as evidence."""
    if window == 'hammer':
        w = _window('Hammer')
        if map_name:
            title = w.title.casefold()
            stem = map_name.casefold()
            if f'{stem}.vmap' not in title and not title.rstrip(' *').endswith(stem):
                raise GameError(f'Hammer shows {w.title!r}, not map {map_name!r}; '
                                f'open it with hammer_open({map_name!r}) and retry')
        shot = _capture(s, w, name or 'hammer')
        shot['window'] = w.title
        return shot
    if window != 'game':
        raise GameError("window must be 'game' or 'hammer'")
    return views(s, None, [{'name': name or 'game', 'pos': pos, 'ang': ang}], hide_hud)['shots'][0]


def stop(s: Settings) -> dict:
    cur = session(s)
    if cur is None:
        return {'state': 'not running'}
    if not cur['launched_by_tool']:
        return {'state': 'left running: this CS2 was started by the user, close it yourself'}
    psutil.Process(cur['pid']).kill()
    return {'state': 'stopped', 'pid': cur['pid']}
