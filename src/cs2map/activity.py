"""What the tools are doing, written for people: one journal shared by every server process.

Each public tool call appends two lines to .local/state/activity.jsonl (start, end): the tool, what it works on,
which client asked, one line of outcome, the seconds and the job it started. The panel (panel.py) reads it; so can
anyone with a text editor. A journal problem never fails a tool. Specification: docs/specs/panel.md.
"""
from __future__ import annotations

import functools
import json
import os
import time
import uuid
from pathlib import Path

from .config import PROJECT_ROOT

STATE_DIR = PROJECT_ROOT / '.local' / 'state'
_DEFAULT_LOG = STATE_DIR / 'activity.jsonl'
LOG = _DEFAULT_LOG
MAX_BYTES = 1_000_000          # then the older half is dropped
KEEP_LINES = 2500
SUBJECT_KEYS = ('map', 'name', 'job', 'command', 'query', 'model', 'path', 'section', 'image', 'key', 'classname')
COUNT_KEYS = ('problems', 'findings', 'shots', 'checks', 'errors', 'maps', 'results', 'entities', 'backups')
_CLIENTS = {'claude-code': 'Claude Code', 'claude-ai': 'Claude Desktop', 'claude-desktop': 'Claude Desktop',
            'codex-mcp-client': 'Codex', 'codex': 'Codex'}
_client: str | None = None


def enabled() -> bool:
    """Offline test runs do not write into the user's journal (a test points LOG at its own file)."""
    return not (os.environ.get('CS2MAP_OFFLINE') and LOG == _DEFAULT_LOG)


def write(entry: dict) -> None:
    if not enabled():
        return
    line = (json.dumps(entry, ensure_ascii=False, separators=(',', ':'), default=str) + '\n').encode('utf-8')
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        # One write of one short line in append mode: lines of several processes do not mix.
        fd = os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_BINARY', 0))
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        if LOG.stat().st_size > MAX_BYTES:
            _rotate()
    except OSError:
        pass


def _rotate() -> None:
    try:
        lines = LOG.read_bytes().splitlines(keepends=True)[-KEEP_LINES:]
        tmp = LOG.with_suffix('.tmp')
        tmp.write_bytes(b''.join(lines))
        os.replace(tmp, LOG)
    except OSError:                        # another process holds the file: the next write tries again
        pass


def client(mcp=None) -> str:
    """Who is calling: the MCP client's own name (Claude Code, Codex...), else the program that started us."""
    global _client
    if _client:
        return _client
    name = None
    try:
        name = mcp.get_context().session.client_params.clientInfo.name
    except Exception:                      # no request in progress, or an older SDK
        pass
    if not name or name == 'mcp':          # the bare SDK client: tools/mcp_call.py and scripts
        try:
            import psutil
            for parent in psutil.Process().parents()[:3]:
                cmd = ' '.join(parent.cmdline()).lower()
                if 'mcp_call' in cmd:
                    name = 'mcp_call'
                elif 'selfcheck' in cmd:
                    name = 'selfcheck'
                if name and name != 'mcp':
                    break
        except Exception:
            pass
    if name and name != 'mcp':
        _client = _CLIENTS.get(name.lower(), name)[:24]
    return _client or 'script'


def subject(kwargs: dict) -> str:
    for key in SUBJECT_KEYS:
        value = kwargs.get(key)
        if isinstance(value, str) and value:
            text = value.splitlines()[0]
            extra = kwargs.get('preset') if key == 'name' and isinstance(kwargs.get('preset'), str) else None
            return (text[:48] + ('...' if len(text) > 48 else '')) + (f' · {extra}' if extra else '')
    return ''


def summarize(tool: str, result) -> tuple[str, str]:
    """(state, one line) of a tool result: state is ok | warn | bad."""
    if not isinstance(result, dict):
        return 'ok', ''
    if result.get('refused'):
        cost = result.get('estimate')
        if isinstance(cost, dict) and 'minutes' in cost:       # a heavy build the user has not allowed yet
            declined = 'not now' in str(result['refused'])
            return 'warn', (f'{"the user said not now" if declined else "waits for the user"}: about '
                            f'{cost["minutes"]} min, up to {cost.get("memory_gb", "?")} GB')
        return 'warn', str(result['refused'])[:160]
    if result.get('deferred'):
        return 'warn', 'put off: ' + str(result['deferred']).split(' and settings')[0][:140]
    state = 'bad' if result.get('ok') is False or result.get('error') else 'ok'
    parts: list[str] = []
    if state == 'bad':
        why = result.get('reason') or result.get('error') or result.get('stuck')
        if why:
            parts.append(str(why).splitlines()[0][:140])
    if result.get('state') and (result.get('job') or tool == 'job_status'):
        progress = result.get('progress') or {}
        text = str(result['state'])
        if text == 'running' and progress.get('stage'):
            text = f'{progress["stage"].split(" (")[0]}, {progress.get("minutes", 0)} min'
        elif text == 'running' and tool != 'job_status':
            text = 'started in the background'
        elif isinstance(result.get('seconds'), (int, float)):
            text += f' in {_seconds(result["seconds"])}'
        if result['state'] in ('failed', 'timeout', 'lost'):
            state = 'bad'
            errors = (result.get('result') or {}).get('errors')
            if isinstance(errors, list) and errors:
                text += f': {str(errors[0])[:100]}'
        parts.append(text)
    if isinstance(result.get('applied'), int):
        parts.append(f'{result["applied"]} operations' + (' (dry run)' if result.get('dry_run') else ' applied'))
    if isinstance(result.get('reached'), int) and isinstance(result.get('point_count'), int):
        parts.append(f'reached {result["reached"]} of {result["point_count"]} points')
    for key in COUNT_KEYS:
        value = result.get(key)
        if isinstance(value, list) and (value or key in ('problems', 'findings')):
            parts.append(f'{len(value)} {key[:-1] if len(value) == 1 else key}')
            if key in ('problems', 'findings', 'errors') and value and state == 'ok':
                state = 'warn' if key != 'errors' else 'bad'
    if not parts and result.get('ok') is True:
        parts.append('ok')
    if not parts:
        for key in ('hint', 'note', 'loaded', 'png', 'sheet', 'path'):
            if isinstance(result.get(key), (str, bool)) and result.get(key) not in ('', False):
                value = result[key]
                parts.append(f'{key}: {Path(value).name if key in ("png", "sheet", "path") else str(value)[:100]}'
                             if not isinstance(value, bool) else key)
                break
    return state, ' · '.join(parts[:3])[:200]


def _seconds(value: float) -> str:
    return f'{value:.1f} s' if value < 60 else f'{int(value // 60)}:{int(value % 60):02d}'


def begin(tool: str, kwargs: dict, who: str) -> str:
    ident = uuid.uuid4().hex[:10]
    write({'ev': 'start', 'id': ident, 't': round(time.time(), 2), 'tool': tool, 'what': subject(kwargs),
           'who': who, 'pid': os.getpid()})
    return ident


def end(ident: str, state: str, text: str, seconds: float, job: str | None = None) -> None:
    entry = {'ev': 'end', 'id': ident, 't': round(time.time(), 2), 'state': state, 'text': text,
             'seconds': round(seconds, 1)}
    if job:
        entry['job'] = job
    write(entry)


def note(kind: str, **fields) -> None:
    """A fact that is not a tool call: the game window took the focus ("focus"), the panel did something ("panel")."""
    write({'ev': kind, 't': round(time.time(), 2), 'pid': os.getpid(), **fields})


def logged(tool: str, fn, mcp=None, who: str | None = None):
    """The tool function with its call written to the journal (`who` names the caller when it is not an MCP client)."""
    @functools.wraps(fn)
    def call(*args, **kwargs):
        if not enabled():
            return fn(*args, **kwargs)
        ident = begin(tool, kwargs, who or client(mcp))
        started = time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:
            text = (str(exc).splitlines() or [type(exc).__name__])[0][:200]
            end(ident, 'bad', text, time.monotonic() - started)
            raise
        try:
            state, text = summarize(tool, result)
            job = result.get('job') if isinstance(result, dict) and isinstance(result.get('job'), str) else None
        except Exception:                  # a summary bug must not lose the result
            state, text, job = 'ok', '', None
        end(ident, state, text, time.monotonic() - started, job)
        return result
    return call


def read(limit: int = 60, since: float = 0.0) -> dict:
    """The journal merged for display: {calls: newest first, focus: [{t, seconds, what}] since `since`}."""
    import psutil
    try:
        raw = LOG.read_bytes()[-400_000:].decode('utf-8', errors='replace').splitlines()
    except OSError:
        raw = []
    calls: dict[str, dict] = {}
    order: list[str] = []
    focus, notes = [], []
    for line in raw:
        try:
            e = json.loads(line)
        except ValueError:                 # a cut first line
            continue
        ev = e.get('ev')
        if ev == 'start':
            calls[e['id']] = {**e, 'state': 'running'}
            order.append(e['id'])
        elif ev == 'end' and e.get('id') in calls:
            calls[e['id']].update({k: e[k] for k in ('state', 'text', 'seconds', 'job') if k in e}, ended=e['t'])
        elif ev == 'focus' and e.get('t', 0) >= since:
            focus.append({k: e.get(k) for k in ('t', 'seconds', 'what')})
        elif ev == 'panel':
            notes.append(e)
    out = []
    for ident in reversed(order[-limit * 2:]):
        c = calls[ident]
        if c['state'] == 'running' and not psutil.pid_exists(c.get('pid') or 0):
            c['state'], c['text'] = 'lost', 'the server process ended before the call returned'
        out.append({k: c.get(k) for k in ('id', 't', 'tool', 'what', 'who', 'state', 'text', 'seconds', 'job', 'ended')})
    for e in notes[-limit:]:
        out.append({'id': None, 't': e['t'], 'tool': e.get('tool', 'panel'), 'what': e.get('what', ''),
                    'who': 'panel', 'state': e.get('state', 'ok'), 'text': e.get('text', ''), 'seconds': None})
    out.sort(key=lambda c: c['t'], reverse=True)
    return {'calls': out[:limit], 'focus': focus}
