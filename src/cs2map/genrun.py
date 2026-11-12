"""Runs one generator script for the pipeline and notes every file it read.

    python -s -P genrun.py <deps.json> <script.py> [args...]      (PYTHONHASHSEED=0, no other PYTHON* variables)

Started by pipeline.run_generator with an isolated interpreter, so this file imports nothing from cs2map. The
script runs as __main__ with its folder first on sys.path. Every file opened for reading and every module loaded
from a file is written to <deps.json>: the pipeline hashes those inside the generator's folder and runs the
generator again only when one of them (or the script, or its arguments) has changed.
"""
import json
import os
import runpy
import sys


def main() -> None:
    deps_file, script = sys.argv[1], os.path.abspath(sys.argv[2])
    read: set[str] = set()

    def audit(event: str, args) -> None:
        if event != 'open':
            return
        path, mode = args[0], args[1]
        if not isinstance(path, (str, bytes, os.PathLike)):
            return
        if mode is None:                               # os.open: read unless a write flag is set
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC):
                return
        elif any(c in str(mode) for c in 'wax+'):
            return
        try:
            read.add(os.path.abspath(os.fsdecode(path)))
        except (TypeError, ValueError):
            pass

    sys.addaudithook(audit)
    sys.argv = [script, *sys.argv[3:]]
    sys.path.insert(0, os.path.dirname(script))
    code = 0
    try:
        runpy.run_path(script, run_name='__main__')
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        if not isinstance(e.code, int) and e.code is not None:
            print(e.code, file=sys.stderr)
    finally:
        for module in list(sys.modules.values()):
            name = getattr(module, '__file__', None)
            if name:
                read.add(os.path.abspath(name))
        read.discard(script)
        try:
            with open(deps_file, 'w', encoding='utf-8') as f:
                json.dump(sorted(read), f)
        except OSError:
            pass
    sys.exit(code)


if __name__ == '__main__':
    main()
