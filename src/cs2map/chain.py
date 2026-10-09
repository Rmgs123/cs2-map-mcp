"""Run several commands one after another as one job; stop at the first failure (exit code of that command).

    python -m cs2map.chain '[["cmd", "arg"], ["cmd2"]]'
"""
from __future__ import annotations

import json
import subprocess
import sys


def main() -> int:
    steps = json.loads(sys.argv[1])
    for i, argv in enumerate(steps, 1):
        print(f'== step {i}/{len(steps)}: {subprocess.list2cmdline(argv)[:300]}', flush=True)
        code = subprocess.call(argv, stdin=subprocess.DEVNULL)
        if code:
            print(f'== step {i} failed with exit code {code}', flush=True)
            return code
    return 0


if __name__ == '__main__':
    sys.exit(main())
