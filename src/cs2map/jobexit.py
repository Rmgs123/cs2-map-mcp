"""Run a command and record its exit code in a file:  python -m cs2map.jobexit <exit-file> -- <command...>

JobManager starts every job through this wrapper, so the result of a build survives
the process that started it (an MCP server restart, a short script that exits)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != '--':
        print('usage: python -m cs2map.jobexit <exit-file> -- <command...>', file=sys.stderr)
        return 2
    exit_file = Path(argv[0])
    try:
        code = subprocess.call(argv[2:], stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    except OSError as e:
        print(f'cannot start {argv[2]}: {e}', flush=True)
        code = 127
    tmp = exit_file.with_suffix('.tmp')
    tmp.write_text(str(code), encoding='ascii')
    tmp.replace(exit_file)
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
