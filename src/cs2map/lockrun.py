"""Run a command while holding a named cross-process lock:

    python -m cs2map.lockrun <lock-name> <timeout-seconds> -- <argv...>

Used for map builds that bake lighting: VRAD3 keeps its work files in one
`_vrad3` folder per addon, so two lighting bakes at once break each other.
The lock file holds the pid of this wrapper; if the wrapper dies, the lock is stale.
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
    argv = sys.argv[i + 1:]
    path = acquire(name, timeout)
    try:
        print(f'[lockrun] acquired "{name}"', flush=True)
        while True:
            code, too_many = _run(argv)
            res = _lightmap_res(argv)
            if code == 0 or not too_many or res is None or res >= MAX_LIGHTMAP:
                return code
            # A big map has more UV charts than the atlas holds: retry with a larger atlas.
            argv = _with_lightmap_res(argv, res * 2)
            print(f'{RETRY_MARK} lightmap atlas too small: retrying with -lightmapMaxResolution {res * 2}',
                  flush=True)
    finally:
        path.unlink(missing_ok=True)


RETRY_MARK = '[lockrun] retry:'
MAX_LIGHTMAP = 2048
TOO_MANY = 'Too many charts for the lightmap'


def _run(argv) -> tuple[int, bool]:
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    too_many = False
    for raw in proc.stdout:
        line = raw.decode('utf-8', 'replace')
        too_many = too_many or TOO_MANY in line
        sys.stdout.write(line)
        sys.stdout.flush()
    return proc.wait(), too_many


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
