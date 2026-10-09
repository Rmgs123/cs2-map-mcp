"""Register the cs2map MCP server in AI clients (used by setup.ps1).

    python tools/setup_clients.py [--apply] [--clients claude-code,claude-desktop,codex]

Without --apply it only prints what would change. With --apply each user config file is copied next to itself
(<file>.before-cs2map-<time>.bak) before it is written. Clients:
  claude-code     <project>/.mcp.json (project scope: the server is offered when Claude Code runs in this folder)
  claude-desktop  %APPDATA%/Claude/claude_desktop_config.json  (mcpServers.cs2map)
  codex           ~/.codex/config.toml [mcp_servers.cs2map]  (Codex CLI and the Codex desktop app share it)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / 'tools' / 'python-env' / 'Scripts' / 'python.exe'
ENV = {'PYTHONPATH': str(ROOT / 'src'), 'PYTHONUTF8': '1'}
CLIENTS = ('claude-code', 'claude-desktop', 'codex')


def server_entry() -> dict:
    return {'command': str(PYTHON), 'args': ['-m', 'cs2map.server'], 'env': dict(ENV)}


def toml_block() -> str:
    q = lambda s: "'" + s + "'"
    return '\n'.join(['[mcp_servers.cs2map]', f'command = {q(str(PYTHON))}', 'args = ["-m", "cs2map.server"]',
                      f'cwd = {q(str(ROOT))}', 'startup_timeout_sec = 60', 'tool_timeout_sec = 300', '',
                      '[mcp_servers.cs2map.env]', f'PYTHONPATH = {q(ENV["PYTHONPATH"])}', 'PYTHONUTF8 = "1"', ''])


def merged_json(path: Path) -> tuple[str, str]:
    old = path.read_text(encoding='utf-8') if path.is_file() else ''
    data = json.loads(old) if old.strip() else {}
    if not isinstance(data, dict):
        raise ValueError(f'{path} is not a JSON object')
    data.setdefault('mcpServers', {})['cs2map'] = server_entry()
    return old, json.dumps(data, indent=2, ensure_ascii=False) + '\n'


def merged_toml(path: Path) -> tuple[str, str]:
    old = path.read_text(encoding='utf-8') if path.is_file() else ''
    kept, skip = [], False
    for line in old.splitlines():
        header = re.match(r'\s*\[([^\]]+)\]\s*$', line)
        if header:
            skip = header.group(1).strip() in ('mcp_servers.cs2map', 'mcp_servers.cs2map.env')
        if not skip:
            kept.append(line)
    text = '\n'.join(kept).rstrip() + ('\n\n' if kept else '') + toml_block()
    return old, text


def targets(clients) -> list[tuple[str, Path, callable]]:
    appdata = Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming'))
    table = {'claude-code': (ROOT / '.mcp.json', merged_json),
             'claude-desktop': (appdata / 'Claude' / 'claude_desktop_config.json', merged_json),
             'codex': (Path.home() / '.codex' / 'config.toml', merged_toml)}
    return [(c, *table[c]) for c in clients]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--clients', default=','.join(CLIENTS))
    a = ap.parse_args(argv)
    clients = [c.strip() for c in a.clients.split(',') if c.strip()]
    bad = [c for c in clients if c not in CLIENTS]
    if bad:
        print(f'unknown client(s) {bad}; known: {", ".join(CLIENTS)}')
        return 2
    for client, path, merge in targets(clients):
        old, new = merge(path)
        same = old == new
        if not same and merge is merged_json and old.strip():
            same = (json.loads(old).get('mcpServers') or {}).get('cs2map') == server_entry()
        if same:
            print(f'[{client}] {path}: already registered')
            continue
        if merge is merged_json:
            entry = (json.loads(old).get('mcpServers') or {}).get('cs2map') if old.strip() else None
            diff = [k for k, v in server_entry().items() if entry and entry.get(k) != v]
        else:
            entry = '[mcp_servers.cs2map]' in old
            diff = [x for x in toml_block().splitlines() if entry and x.strip() and x not in old.splitlines()]
        action = 'create the file with' if not old else 'update' if entry else 'add'
        print(f'[{client}] {path}: {action} the cs2map entry' + (f' ({"; ".join(diff)})' if diff else ''))
        if not a.apply:
            print('    (plan only; run with -Apply to write)')
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if old:
            backup = path.with_name(path.name + f'.before-cs2map-{time.strftime("%Y%m%d-%H%M%S")}.bak')
            shutil.copy2(path, backup)
            print(f'    backup: {backup}')
        path.write_text(new, encoding='utf-8')
        print('    written; restart the client to load the server')
    return 0


if __name__ == '__main__':
    sys.exit(main())
