"""What the user allowed and what waits for them: one small file shared by every server process and the panel.

.local/state/panel_state.json holds
- ``focus_until``: the game window may take the focus for real mouse steps until this moment (the panel's
  "Allow for 15 min"), whatever settings ``game_focus`` says;
- ``builds``: heavy builds the AI asked for while settings ``heavy_builds`` is "ask" (waiting, allowed, declined);
- ``tests``: script tests put off because a step needs the real mouse and the focus is not allowed.
The standing choices themselves (game_focus, heavy_builds, build_priority, restart_hung_cs2) live in
config/settings.json; running servers read them again when the file changes. Specification: docs/specs/panel.md.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .config import PROJECT_ROOT

_DEFAULT_STATE = PROJECT_ROOT / '.local' / 'state' / 'panel_state.json'
# CS2MAP_PANEL_STATE: another file for a test instance of the panel and the processes it starts.
STATE = Path(os.environ['CS2MAP_PANEL_STATE']) if os.environ.get('CS2MAP_PANEL_STATE') else _DEFAULT_STATE
FOCUS_MINUTES = 15
HEAVY_PRESETS = ('full', 'final', 'release')
HEAVY_SECONDS = 120            # a build of this map and preset that took this long last time is heavy
HEAVY_VMAP_MB = 20             # ... and so is a first light bake of a map this big
ALLOW_SECONDS = 30 * 60        # "Allow once" waits this long for the AI to start the build
DECLINE_SECONDS = 10 * 60      # "Not now" answers the same request for this long, then the AI may ask again
KEEP_SECONDS = 24 * 3600       # answered questions and finished tests stay visible this long
KEEP_ITEMS = 12


def enabled() -> bool:
    """Offline test runs neither read nor write the user's file (a test points STATE at its own)."""
    return not (os.environ.get('CS2MAP_OFFLINE') and STATE == _DEFAULT_STATE)


def load() -> dict:
    if not enabled():
        return {}
    try:
        data = json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@contextmanager
def _locked(wait: float = 3.0):
    """A short cross-process lock for read-modify-write. A lock left by a dead process is taken after a second."""
    lock = STATE.with_suffix('.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait
    held = False
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            held = True
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 1.0:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                continue
            if time.monotonic() > deadline:
                break                        # better a rare lost update than a stuck tool
            time.sleep(0.02)
        except OSError:
            break
    try:
        yield
    finally:
        if held:
            try:
                lock.unlink(missing_ok=True)
            except OSError:
                pass


def change(fn):
    """Read, let ``fn(data)`` change the state, write it back. Returns what fn returned."""
    if not enabled():
        return fn({})
    with _locked():
        data = load()
        out = fn(data)
        _trim(data)
        tmp = STATE.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(tmp, STATE)
        return out


def _trim(data: dict) -> None:
    now = time.time()
    for key, open_states in (('builds', ('waiting',)), ('tests', ('waiting', 'running'))):
        items = [x for x in data.get(key) or [] if x.get('state') in open_states
                 or now - (x.get('answered') or x.get('finished') or x.get('asked') or 0) < KEEP_SECONDS]
        closed = [x for x in items if x.get('state') not in open_states][-KEEP_ITEMS:]
        data[key] = [x for x in items if x.get('state') in open_states or x in closed]
    if (data.get('focus_until') or 0) < now:
        data.pop('focus_until', None)


def _clock(epoch: float) -> str:
    return _dt.datetime.fromtimestamp(epoch).strftime('%H:%M')


# -- focus ---------------------------------------------------------------------------------------------------------
def focus_until() -> float:
    """The moment until which the user allowed the focus for mouse steps (0 = no such permit)."""
    until = load().get('focus_until') or 0
    return until if until > time.time() else 0


def allow_focus(minutes: float | None = FOCUS_MINUTES) -> float:
    """Allow the focus for `minutes` (None or 0 takes the permit back). Returns the moment it ends."""
    until = time.time() + minutes * 60 if minutes else 0

    def apply(data):
        if until:
            data['focus_until'] = until
        else:
            data.pop('focus_until', None)
        return until
    return change(apply)


# -- heavy builds --------------------------------------------------------------------------------------------------
def build_history(logs: Path, title: str) -> dict:
    """How the builds titled `title` went before: {seconds (the last success), count}. Reads logs/<kind>-*.json."""
    kind = title.split(' ', 1)[0]
    seconds, count = None, 0
    try:
        files = sorted(Path(logs).glob(f'{kind}-*.json'), key=lambda p: p.name, reverse=True)[:400]
    except OSError:
        files = []
    for path in files:
        try:
            job = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(job, dict) or job.get('state') != 'succeeded':
            continue
        if not (str(job.get('title', '')).startswith(title) if title.endswith('->') else job.get('title') == title):
            continue
        count += 1
        if seconds is None and isinstance(job.get('seconds'), (int, float)):
            seconds = float(job['seconds'])
        if count >= 5:
            break
    return {'seconds': seconds, 'count': count}


def build_title(s, name: str, preset: str) -> str:
    """The job title of a build; a release title ends with "->" and matches any target addon."""
    return f'release {name} ->' if preset == 'release' else f'build {name} ({preset})'


def estimate(s, name: str, preset: str) -> dict | None:
    """The cost of a heavy build, or None when this build is not heavy (a small map, a quick preset)."""
    if preset not in HEAVY_PRESETS:
        return None
    try:
        size_mb = s.map_path(name).stat().st_size / 2**20
    except OSError:
        return None                          # no such map: the build itself says so
    history = build_history(s.logs, build_title(s, name, preset))
    last = history['seconds']
    if preset != 'release' and (last or 0) < HEAVY_SECONDS and size_mb < HEAVY_VMAP_MB:
        return None
    if last:
        minutes, basis = last / 60, 'the last build of this map and preset'
    else:                                    # no history: light bakes measured on maps of 1..270 MB
        minutes = (1.5 + size_mb * 0.025) * {'full': 1, 'final': 3, 'release': 4}[preset]
        basis = 'the map size (no earlier build of this kind)'
    return {'minutes': max(1, round(minutes)), 'memory_gb': round(2.0 + size_mb * 0.06), 'basis': basis,
            'map_mb': round(size_mb)}


def heavy_gate(s, name: str, preset: str, who: str = '', panel: str | None = None) -> dict | None:
    """None = build; else the refusal a tool returns while settings heavy_builds is "ask" and the user has not
    allowed this build in the panel. An allowance is used up by the build it lets through."""
    if getattr(s, 'heavy_builds', 'allow') != 'ask' or not enabled():
        return None
    cost = estimate(s, name, preset)
    if cost is None:
        return None
    now = time.time()
    where = (f'in the panel ({panel})' if panel else
             'in the panel (python tools/panel.py) or with "heavy_builds": "allow" in config/settings.json')
    text = f'about {cost["minutes"]} min and up to {cost["memory_gb"]} GB of memory'

    def apply(data):
        builds = data.setdefault('builds', [])
        mine = [b for b in builds if b.get('map') == name and b.get('preset') == preset]
        for b in mine:
            if b.get('state') == 'allowed' and now - b.get('answered', 0) < ALLOW_SECONDS:
                b.update(state='used', finished=now)
                return None
        for b in mine:
            if b.get('state') == 'declined' and now - b.get('answered', 0) < DECLINE_SECONDS:
                return {'refused': f'the user answered "not now" to this build at {_clock(b["answered"])}: go on '
                                   f'without it (a fast or nav build needs no permission) and tell the user what '
                                   f'is left unbaked', 'estimate': cost}
        waiting = next((b for b in mine if b.get('state') == 'waiting'), None)
        if waiting is None:
            builds.append({'id': uuid.uuid4().hex[:8], 'map': name, 'preset': preset, 'who': who, 'asked': now,
                           'state': 'waiting', **cost})
        else:
            waiting.update(cost, asked_again=now)
        return {'refused': f'a heavy build ({text}; settings heavy_builds is "ask"): the user allows it {where}. '
                           f'Tell them the cost and call again once they have allowed it; meanwhile work with '
                           f'fast builds', 'estimate': cost}
    return change(apply)


def answer_build(ident: str, allow: bool) -> dict | None:
    def apply(data):
        for b in data.get('builds') or []:
            if b.get('id') == ident and b.get('state') == 'waiting':
                b.update(state='allowed' if allow else 'declined', answered=time.time())
                return dict(b)
        return None
    return change(apply)


def allow_waiting_builds() -> int:
    """The user turned "heavy builds without asking" on: every waiting question is answered yes."""
    def apply(data):
        n = 0
        for b in data.get('builds') or []:
            if b.get('state') == 'waiting':
                b.update(state='allowed', answered=time.time())
                n += 1
        return n
    return change(apply)


# -- tests put off until the focus is allowed ----------------------------------------------------------------------
def needs_focus(steps) -> list[str]:
    """The steps of a script test that cannot run behind the user's windows (a real click, a held focus)."""
    out = []
    if isinstance(steps, list):
        for i, step in enumerate(steps):
            if isinstance(step, dict) and 'click' in step:
                out.append(f'step {i}: click {step["click"]}')
            elif isinstance(step, dict) and step.get('focus') is True:
                out.append(f'step {i}: focus')
    return out


def defer_test(s, map_name: str, steps, timeout: float, who: str = '', panel: str | None = None) -> dict | None:
    """None = run now; else the answer of a script test put off because a step needs the CS2 window in front while
    settings game_focus is "never" and the user has not allowed the focus. The panel runs it on the user's word."""
    if getattr(s, 'game_focus', 'input_only') != 'never' or not enabled() or focus_until():
        return None
    blocked = needs_focus(steps)
    if not blocked:
        return None
    ident = uuid.uuid4().hex[:8]

    def apply(data):
        tests = data.setdefault('tests', [])
        for t in tests:                      # the same scenario asked again: one entry, the newest steps
            if t.get('state') == 'waiting' and t.get('map') == map_name and t.get('steps') == steps:
                return t['id']
        tests.append({'id': ident, 'map': map_name, 'steps': steps, 'timeout': timeout, 'who': who,
                      'asked': time.time(), 'state': 'waiting', 'needs': blocked[:6]})
        return ident
    ident = change(apply)
    where = f'in the panel ({panel})' if panel else 'in the panel (python tools/panel.py)'
    return {'deferred': f'{len(blocked)} step(s) need the real mouse ({"; ".join(blocked[:3])}) and settings '
                        f'game_focus is "never": the whole test waits until the user runs it {where}. Its result '
                        f'then appears in status (deferred_tests). Go on with other work', 'id': ident}


def take_test(ident: str) -> dict | None:
    """Mark a waiting test as running and return it (None when it is gone or already taken)."""
    def apply(data):
        for t in data.get('tests') or []:
            if t.get('id') == ident and t.get('state') == 'waiting':
                t.update(state='running', started=time.time(), pid=os.getpid())
                return dict(t)
        return None
    return change(apply)


def finish_test(ident: str, result: dict) -> None:
    keep = {k: result.get(k) for k in ('ok', 'reason', 'passed', 'check_count', 'error_count', 'seconds', 'log')
            if result.get(k) is not None}

    def apply(data):
        for t in data.get('tests') or []:
            if t.get('id') == ident:
                t.update(state='done', finished=time.time(), result=keep)
    change(apply)


def drop_test(ident: str) -> bool:
    def apply(data):
        before = len(data.get('tests') or [])
        data['tests'] = [t for t in data.get('tests') or [] if not (t.get('id') == ident and t.get('state') == 'waiting')]
        return len(data['tests']) < before
    return change(apply)


def waiting() -> dict:
    """What waits for the user, for the panel and for status: {builds, tests} (open ones and fresh results)."""
    import psutil
    data = load()
    tests = []
    for t in data.get('tests') or []:
        t = dict(t)
        if t.get('state') == 'running' and not psutil.pid_exists(t.get('pid') or 0):
            t.update(state='done', result={'ok': False, 'reason': 'the test process ended without a result'})
        tests.append(t)
    return {'builds': list(data.get('builds') or []), 'tests': tests}


def summary(s, panel: str | None = None) -> dict:
    """The standing choices and what waits, compact, for the status tool."""
    until = focus_until()
    policy = getattr(s, 'game_focus', 'input_only')
    out = {'game_focus': policy + (f' (focus allowed until {_clock(until)})' if until and policy == 'never' else ''),
           'heavy_builds': getattr(s, 'heavy_builds', 'allow'),
           'build_priority': getattr(s, 'build_priority', 'low')}
    state = waiting()
    builds = [f'{b["map"]} ({b["preset"]}): {b["state"]}' for b in state['builds'] if b.get('state') in
              ('waiting', 'allowed', 'declined')]
    if builds:
        out['heavy_build_requests'] = builds[-4:]
    tests = []
    for t in state['tests'][-4:]:
        item = {'id': t['id'], 'map': t['map'], 'state': t['state']}
        if t.get('result'):
            item.update({k: v for k, v in t['result'].items() if k in ('ok', 'reason', 'passed', 'check_count', 'log')})
        tests.append(item)
    if tests:
        out['deferred_tests'] = tests
    if panel:
        out['panel'] = panel
    return out


# -- the deferred test runner (started by the panel) ------------------------------------------------------------------
def run_deferred(ident: str) -> dict:
    """Run one deferred script test now, with the focus allowed for its mouse steps, and record the result."""
    import dataclasses
    from . import activity, scripting
    from .project import Project
    test = take_test(ident)
    if test is None:
        return {'ok': False, 'reason': f'no waiting test {ident}'}
    project = Project()
    # The user pressed "Run": for this one run a mouse step may bring the game to the front.
    project.s = dataclasses.replace(project.s, game_focus='input_only')
    run = activity.logged('script_test', lambda **kw: scripting.script_test(project, kw['map'], kw['steps'],
                                                                           kw['timeout']), who='panel')
    token = None
    try:
        token, name = project.released(test['map'])          # "<addon>/<map>": a release build
        result = run(map=name, steps=test['steps'], timeout=test.get('timeout', 90))
    except Exception as exc:                 # noqa: BLE001 - the panel shows the reason
        result = {'ok': False, 'reason': str(exc)[:200]}
    finally:
        if token is not None:
            from .project import ADDON_SETTINGS
            ADDON_SETTINGS.reset(token)
    finish_test(ident, result)
    return result


if __name__ == '__main__':
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == 'run':
        out = run_deferred(sys.argv[2])
        print(json.dumps({k: out.get(k) for k in ('ok', 'reason', 'passed', 'check_count', 'log')}))
        sys.exit(0 if out.get('ok') else 1)
    print('usage: python -m cs2map.permits run <test id>')
    sys.exit(2)
