"""Run a command while holding a named cross-process lock:

    python -m cs2map.lockrun <lock-name | -> <timeout-seconds> [--nav-max N] [--nav-probe] -- <argv...>

Used for map builds that bake lighting: VRAD3 keeps its work files in one
`_vrad3` folder per addon, so two lighting bakes at once break each other.
The lock file holds the pid of this wrapper; if the wrapper dies, the lock is stale. "-" runs without a lock.

Nav guard (map builds with -nav): right after NAVGEN the compiler prints the nav area count, before the bot data
step that can run for hours on one core. The guard prints that count with a time estimate; over --nav-max it stops
the compiler (exit NAV_STOPPED). --nav-probe stops it as soon as the count is known (exit 0): a cheap measurement.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

from .config import PROJECT_ROOT

LOCKS = PROJECT_ROOT / '.local' / 'locks'


def acquire(name: str, timeout: float) -> Path:
    LOCKS.mkdir(parents=True, exist_ok=True)
    path = LOCKS / f'{name}.lock'
    deadline = time.monotonic() + timeout
    announced = False
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump({'pid': os.getpid(), 'agent': os.environ.get('CS2MAP_AGENT', 'default'),
                           'time': time.time()}, f)
            return path
        except FileExistsError:
            try:
                info = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                info = {}
            if not psutil.pid_exists(int(info.get('pid', -1))):
                path.unlink(missing_ok=True)
                continue
            if not announced:
                print(f'[lockrun] waiting for "{name}" held by agent {info.get("agent")} (pid {info.get("pid")})',
                      flush=True)
                announced = True
            if time.monotonic() > deadline:
                print(f'[lockrun] gave up waiting for "{name}" after {timeout:.0f}s', flush=True)
                sys.exit(75)
            time.sleep(2)


def main() -> int:
    if '--' not in sys.argv:
        print(__doc__)
        return 2
    i = sys.argv.index('--')
    name, timeout = sys.argv[1], float(sys.argv[2])
    opts = sys.argv[3:i]
    nav_max = int(opts[opts.index('--nav-max') + 1]) if '--nav-max' in opts else None
    probe = '--nav-probe' in opts
    argv = sys.argv[i + 1:]
    path = acquire(name, timeout) if name != '-' else None
    try:
        if path:
            print(f'[lockrun] acquired "{name}"', flush=True)
        while True:
            code, too_many = _run(argv, nav_max, probe)
            res = _lightmap_res(argv)
            if code == 0 or not too_many or res is None or res >= MAX_LIGHTMAP:
                return code
            # A big map has more UV charts than the atlas holds: retry with a larger atlas.
            argv = _with_lightmap_res(argv, res * 2)
            print(f'{RETRY_MARK} lightmap atlas too small: retrying with -lightmapMaxResolution {res * 2}',
                  flush=True)
    finally:
        if path:
            path.unlink(missing_ok=True)


RETRY_MARK = '[lockrun] retry:'
MAX_LIGHTMAP = 2048
TOO_MANY = 'Too many charts for the lightmap'


NAV_MARK = '[navguard]'
NAV_STOPPED = 76


def _run(argv, nav_max: int | None = None, probe: bool = False) -> tuple[int, bool]:
    from . import navcost
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    too_many = False
    for raw in proc.stdout:
        line = raw.decode('utf-8', 'replace')
        too_many = too_many or TOO_MANY in line
        sys.stdout.write(line)
        sys.stdout.flush()
        found = navcost.ISLAND.search(line)
        if found and (nav_max is not None or probe):
            areas = int(found.group(1))
            v = navcost.verdict(areas, nav_max if nav_max is not None else navcost.STOP_AREAS)
            print(f'{NAV_MARK} areas={areas} level={v["level"]}: {v["message"]}', flush=True)
            if probe:
                print(f'{NAV_MARK} probe done: the compiler is stopped after counting', flush=True)
                _kill(proc)
                return 0, False
            if nav_max is not None and areas > nav_max:
                print(f'{NAV_MARK} stopped: {areas} nav areas is over the limit of {nav_max}; the bot data would '
                      f'take {v["ai_data"]}. The compiled map was not replaced. {navcost.LIMIT_FIX}', flush=True)
                _kill(proc)
                return NAV_STOPPED, False
    return proc.wait(), too_many


def _kill(proc) -> None:
    try:
        parent = psutil.Process(proc.pid)
        procs = parent.children(recursive=True) + [parent]
    except psutil.NoSuchProcess:
        return
    for p in procs:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(procs, timeout=10)


def _lightmap_res(argv):
    if '-lightmapMaxResolution' in argv:
        try:
            return int(argv[argv.index('-lightmapMaxResolution') + 1])
        except (IndexError, ValueError):
            return None
    return None


def _with_lightmap_res(argv, res):
    out = list(argv)
    out[out.index('-lightmapMaxResolution') + 1] = str(res)
    return out


if __name__ == '__main__':
    sys.exit(main())
