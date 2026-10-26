"""Backups without an MCP client (the same store map_backups uses; see REPAIR.md "Backups").

    python tools/backups.py status                     folder, sizes, newest sources snapshot, warnings
    python tools/backups.py list <map>                 copies of a map, newest first
    python tools/backups.py restore <map> <id> [--sources] [--force]
                                                       put a copy back (the current file is backed up first);
                                                       --sources also writes that moment's sources to restored/<id>/
    python tools/backups.py pin <map> "<label>"         keep the map and the sources as they are now, forever
    python tools/backups.py sources [<stamp>]          list sources snapshots, or write one to restored/<stamp>/
    python tools/backups.py now                        copy changed maps, snapshot sources and trim, right now
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from cs2map import backups  # noqa: E402
from cs2map.config import load_settings  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    s = load_settings()
    cmd, args = argv[0], [a for a in argv[1:] if not a.startswith('--')]
    flags = {a for a in argv[1:] if a.startswith('--')}
    if cmd == 'status':
        out = backups.summary(s)
    elif cmd == 'list' and len(args) == 1:
        out = backups.listing(s, args[0], limit=200)
    elif cmd in ('restore', 'pin') and len(args) == 2:
        from cs2map.project import Project
        project = Project(s)
        out = (project.map_restore(args[0], args[1], '--force' in flags, '--sources' in flags) if cmd == 'restore'
               else project.map_backups(args[0], pin=args[1]))
    elif cmd == 'sources' and len(args) <= 1:
        out = backups.restore_sources(s, args[0]) if args else {'snapshots': backups.snapshots(s)[:50]}
    elif cmd == 'now':
        out = backups.periodic(s, force=True)
    else:
        print(__doc__)
        return 2
    backups.flush(120)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
