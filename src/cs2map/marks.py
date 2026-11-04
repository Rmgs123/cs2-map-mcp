"""Player marks: a tester presses one key in CS2 and the AI later gets, per press, the frame the tester saw,
the map, the position and view (setpos/setang), the time and an optional chat note.

How it works (measured in CS2 Workshop Tools 1.41.9.5, 03.11):
- `install` writes `<game addon>/cfg/mcp_mark.cfg` and binds the key (settings `marks_key`, default F8) to
  `exec mcp_mark`. CS2 keeps binds in the user's key config, so the key also works in later Workshop Tools
  sessions of this addon that the user starts without the MCP; in the normal game (no addon) the exec finds
  no file and does nothing. A key the user bound to something of their own is never taken.
- the cfg prints a marker, `getpos` (eye position), `status` (map) and runs `jpeg_screenshot mcp_mark`
  (`<game addon>/screenshots/mcp_mark_NNNN.jpg`; CS2 prints "Screenshot written to: ..." a frame later).
- nothing holds the console meanwhile: CS2 replays its console buffer (every line stamped with milliseconds
  since start) to each new VConsole client, and live lines reach an open one. `tap` keeps the relevant lines
  of every connection (vconsole.TAPS); `harvest` turns them into marks, moves the frame to
  `screenshots/marks/` and keeps the record in `.local/state/marks.json`.
- the note: every chat line (Y, text, Enter) the tester sends after the mark until the next mark, each within
  NOTE_SECONDS of the previous one (or of the mark) and all within NOTE_MAX; joined with " / ", kept as `notes`.
- `prune` owns screenshots/marks/ (housekeeping skips it): marks older than settings `limits.marks_days` (14),
  beyond `limits.marks_max` (300) or `limits.marks_mb` (200) of frames go first-oldest with their frames; CS2
  frames left unmatched in `<game addon>/screenshots/` go after marks_days. A frame is ~150-250 KB.
"""
from __future__ import annotations

import datetime as _dt
import itertools
import json
import os
import re
import shutil
import threading
import time
from collections import deque
from pathlib import Path

from . import vconsole
from .config import PROJECT_ROOT, Settings, ensure_inside

CFG = 'mcp_mark'
BEGIN = 'MCP_MARK_BEGIN'
BIND = f'exec {CFG}'
SHOT = 'mcp_mark'
CFG_TEXT = ('// cs2map player mark (written by the MCP, bound to settings marks_key): marker, eye position, map,\n'
            '// then a frame in screenshots/. The MCP reads the lines from the console later.\n'
            f'echo {BEGIN}\ngetpos\nstatus\njpeg_screenshot {SHOT} 90\n')
STATE = PROJECT_ROOT / '.local' / 'state' / 'marks.json'
NOTE_SECONDS = 120          # a note line belongs to the mark if it follows the mark or the previous line this soon
NOTE_MAX = 300              # ... and comes at most this long after the mark
NOTE_LINES = 10
BLOCK_MS = 5000              # getpos, status and the screenshot line follow the marker within a few frames
DAYS = 14
MAX_MARKS = 300
MAX_MB = 200
EYE = 64.0                   # getpos gives the eyes; views take the feet
KEEP_LINES = 20000           # relevant lines kept per console connection
KEEP_CONNECTIONS = 6

_SETPOS = re.compile(r'setpos\s+([-+\d.e]+)\s+([-+\d.e]+)\s+([-+\d.e]+)\s*;\s*setang\s+([-+\d.e]+)\s+'
                     r'([-+\d.e]+)\s+([-+\d.e]+)', re.I)
_MAP = re.compile(r'loaded spawngroup\(\s*\d+\)\s*:\s*SV:\s*\[\d+:\s*([^|\]]+?)\s*\|[^\]]*\bmapload\]', re.I)
_SHOT = re.compile(r'Screenshot written to:\s*\S*?\b(' + SHOT + r'_\d+\.jpg)', re.I)
# A chat line: "[ALL] name‎: text" (the channel is localised; CS2 puts U+200E after the name; a team chat
# line may add "@ place"; a dead player's line starts with "*DEAD*").
_CHAT = re.compile(r'^\s*(?:\*[^*\s]{1,16}\*\s*)?\[[^\]]{1,32}\]\s.{0,96}?‎(?:\s*@\s*[^:]{0,64})?:\s(?P<text>.+)$')
_BIND = re.compile(r'"(?P<key>[^"]+)"\s*=\s*"(?P<value>.*)"\s*$')

_LOCK = threading.Lock()          # the tap buffers (reader threads)
_HARVEST = threading.RLock()      # the state file
_CONNS: dict[int, dict] = {}
_SEQ = itertools.count(1)


def _relevant(line: str) -> bool:
    return (BEGIN in line or 'setpos' in line or 'mapload' in line or 'Screenshot written' in line
            or '‎' in line)


def tap(conn, batch: list[tuple[int, str]]) -> None:
    """vconsole tap: keep the lines marks need, per connection, with the wall-clock offset of its stamps."""
    if not batch:
        return
    now = time.time()
    keep = [(ms, ln) for ms, ln in batch if _relevant(ln)]
    newest = max(ms for ms, _ in batch)
    with _LOCK:
        seq = getattr(conn, 'marks_seq', None)
        if seq is None or seq not in _CONNS:
            seq = next(_SEQ)
            try:
                conn.marks_seq = seq
            except AttributeError:
                pass
            _CONNS[seq] = {'lines': deque(maxlen=KEEP_LINES), 'offset': None}
            for old in sorted(_CONNS)[:-KEEP_CONNECTIONS]:
                _CONNS.pop(old, None)
        c = _CONNS[seq]
        # The newest line of a batch was printed at most "now": the smallest offset seen is the closest one.
        offset = now - newest / 1000.0
        if c['offset'] is None or offset < c['offset']:
            c['offset'] = offset
        c['lines'].extend(keep)


def enable() -> None:
    if tap not in vconsole.TAPS:
        vconsole.TAPS.append(tap)


enable()


# ---- parsing ---------------------------------------------------------------------------------------------------
def parse(lines: list[tuple[int, str]], offset: float | None) -> list[dict]:
    """Marks in one connection's lines: {ms, wall, eye [x,y,z], ang, setpos, map, shot, notes, note, complete,
    note_open}. `complete`: the block is over (its screenshot line arrived, a newer mark started or BLOCK_MS
    passed); `note_open`: more note lines may still come."""
    begins = [i for i, (_, ln) in enumerate(lines) if ln.strip() == BEGIN]
    newest = lines[-1][0] if lines else 0
    out = []
    for k, i in enumerate(begins):
        t = lines[i][0]
        end = begins[k + 1] if k + 1 < len(begins) else len(lines)
        m = {'ms': t, 'wall': (offset + t / 1000.0) if offset is not None else None, 'eye': None, 'ang': None,
             'setpos': None, 'map': None, 'shot': None, 'notes': [], 'note': None}
        last_note = t
        for ms, ln in lines[i + 1:end]:
            if ms - t > NOTE_MAX * 1000 or ms - last_note > NOTE_SECONDS * 1000:
                break
            if ms - t <= BLOCK_MS:
                if m['eye'] is None and (hit := _SETPOS.search(ln)):
                    v = [float(x) for x in hit.groups()]
                    m['eye'], m['ang'] = v[:3], v[3:]
                    m['setpos'] = hit.group(0)
                    continue
                if m['map'] is None and (hit := _MAP.search(ln)):
                    m['map'] = None if hit.group(1).strip() == '<empty>' else hit.group(1).strip()
                    continue
                if m['shot'] is None and (hit := _SHOT.search(ln)):
                    m['shot'] = hit.group(1)
                    continue
            if m['eye'] is not None and len(m['notes']) < NOTE_LINES and (hit := _CHAT.match(ln)):
                m['notes'].append(hit.group('text').strip()[:200])
                last_note = ms
        m['note'] = ' / '.join(m['notes']) or None
        last = end < len(lines)
        m['complete'] = m['eye'] is not None and (m['shot'] is not None or last or newest - t > BLOCK_MS)
        m['note_open'] = (not last and newest - t < NOTE_MAX * 1000 and newest - last_note < NOTE_SECONDS * 1000
                          and len(m['notes']) < NOTE_LINES)
        out.append(m)
    return out


# ---- state -----------------------------------------------------------------------------------------------------
def _load() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding='utf-8'))
        if isinstance(data, dict) and isinstance(data.get('marks'), list):
            return data
    except (OSError, ValueError):
        pass
    return {'install': None, 'marks': []}


def _save(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, STATE)


def days(s: Settings) -> float:
    try:
        return max(0.1, float(s.limit('marks_days', DAYS)))
    except (TypeError, ValueError):
        return float(DAYS)


def store(s: Settings) -> Path:
    return s.screenshots / 'marks'


def _incoming(s: Settings) -> Path:
    return s.game_addon / 'screenshots'


def _move_frame(s: Settings, src: Path, when: float) -> str | None:
    """Move a CS2 frame into screenshots/marks/ (CS2 then reuses its number); None if it cannot be moved."""
    folder = store(s)
    folder.mkdir(parents=True, exist_ok=True)
    stem = 'mark-' + _dt.datetime.fromtimestamp(when).strftime('%Y%m%d-%H%M%S')
    dst = folder / f'{stem}.jpg'
    n = 1
    while dst.exists():
        n += 1
        dst = folder / f'{stem}-{n}.jpg'
    try:
        ensure_inside(src, _incoming(s))
        ensure_inside(dst, folder)
        shutil.move(str(src), str(dst))
    except (OSError, RuntimeError):
        return None
    return str(dst)


def _record(s: Settings, key: str, when: float, path: str | None, eye, ang, setpos, map_name, notes) -> dict:
    stamp = _dt.datetime.fromtimestamp(when)
    name = Path(path).stem if path and Path(path).stem.startswith('mark-') else None
    name = name or 'mark-' + stamp.strftime('%Y%m%d-%H%M%S')
    rec = {'id': name, 'time': stamp.strftime('%Y-%m-%d %H:%M:%S'),
           'epoch': round(when, 3), 'map': map_name, 'pos': None, 'ang': None, 'setpos': setpos, 'path': path,
           'note': ' / '.join(notes or []) or None, 'notes': list(notes or []), 'key': key, 'seen': False}
    if eye is not None:
        rec['pos'] = [round(eye[0], 1), round(eye[1], 1), round(eye[2] - EYE, 1)]
        rec['ang'] = [round(a, 1) for a in ang]
    return rec


def _cap(s: Settings, key: str, default: float) -> float:
    try:
        return max(1.0, float(s.limit(key, default)))
    except (TypeError, ValueError):
        return float(default)


def _size(path: str | None) -> int:
    try:
        return Path(path).stat().st_size if path else 0
    except OSError:
        return 0


def _clean(s: Settings, data: dict, now: float) -> int:
    """Keep marks within marks_days, marks_max and marks_mb (oldest dropped first, with their frames); remove
    frames nobody references in screenshots/marks/ and stale unmatched CS2 frames. Returns marks dropped."""
    limit = now - days(s) * 86400
    folder, incoming = store(s), _incoming(s)
    newest_first = sorted(data['marks'], key=lambda m: m.get('epoch', 0), reverse=True)
    max_marks, max_bytes = int(_cap(s, 'marks_max', MAX_MARKS)), _cap(s, 'marks_mb', MAX_MB) * 2**20
    kept, used = [], 0
    for m in newest_first:
        size = _size(m.get('path'))
        if m.get('epoch', 0) >= limit and len(kept) < max_marks and used + size <= max_bytes:
            kept.append(m)
            used += size
    dropped = len(data['marks']) - len(kept)
    keep_ids = {id(m) for m in kept}
    for m in data['marks']:
        p = Path(m['path']) if m.get('path') else None
        if id(m) not in keep_ids and p and p.parent in (folder, incoming):
            p.unlink(missing_ok=True)
    data['marks'] = [m for m in data['marks'] if id(m) in keep_ids]     # stored order stays
    referenced = {Path(m['path']).name for m in kept if m.get('path')}
    stale = [(folder, 'mark-*.jpg', lambda f, t: f.name not in referenced),
             (incoming, f'{SHOT}_*.jpg', lambda f, t: t < limit and f.name not in referenced)]
    for where, pattern, gone in stale:
        for f in where.glob(pattern) if where.is_dir() else []:
            try:
                if gone(f, f.stat().st_mtime) and (where != folder or f.stat().st_mtime < now - 60):
                    f.unlink()
            except OSError:
                pass
    return dropped


def prune(s: Settings, now: float | None = None) -> dict:
    """The limits alone (housekeeping calls it hourly): {marks, dropped, mb}."""
    now = time.time() if now is None else now
    with _HARVEST:
        data = _load()
        before = len(data['marks'])
        dropped = _clean(s, data, now)
        if dropped or len(data['marks']) != before:
            _save(data)
        mb = sum(_size(m.get('path')) for m in data['marks']) / 2**20
    return {'marks': len(data['marks']), 'dropped': dropped, 'mb': round(mb, 1)}


def harvest(s: Settings, orphans: bool = False, now: float | None = None) -> dict:
    """Turn buffered console lines into stored marks. `orphans`: also take frames that no console block names
    (CS2 was closed before the MCP read its console): only safe right after a refresh or with CS2 gone.
    Returns {new, pending}."""
    now = time.time() if now is None else now
    with _HARVEST:
        data = _load()
        known = {m['key']: m for m in data['marks']}
        with _LOCK:
            snapshot = [(c['offset'], list(c['lines'])) for _, c in sorted(_CONNS.items())]
        incoming = _incoming(s)
        new = pending = 0
        changed = False
        waiting: set[str] = set()
        for offset, lines in snapshot:
            for p in parse(lines, offset):
                if not p['complete']:
                    pending += 1
                    if p['shot']:
                        waiting.add(p['shot'])
                    continue
                key = f'{p["ms"]}|{p["shot"]}|{p["setpos"]}'
                if key in known:
                    m = known[key]
                    if len(p['notes']) > len(m.get('notes') or ([m['note']] if m.get('note') else [])):
                        m['notes'], m['note'] = p['notes'], p['note']
                        m['seen'] = False             # the tester added lines after the last read
                        changed = True
                    continue
                when = p['wall'] or now
                path = None
                src = incoming / p['shot'] if p['shot'] else None
                if src is not None and src.is_file():
                    mtime = src.stat().st_mtime
                    if p['wall'] is None or abs(mtime - p['wall']) < 120:   # a reused number from an older session
                        if now - mtime < 1.0:
                            time.sleep(1.0)                                   # let CS2 finish the file
                        path = _move_frame(s, src, mtime) or str(src)
                        when = mtime
                rec = _record(s, key, when, path, p['eye'], p['ang'], p['setpos'], p['map'], p['notes'])
                data['marks'].append(rec)
                known[key] = rec
                new += 1
                changed = True
        frames = sorted(incoming.glob(f'{SHOT}_*.jpg')) if incoming.is_dir() else []
        left = {Path(m['path']).name for m in data['marks'] if m.get('path') and Path(m['path']).parent == incoming}
        for f in frames:
            if f.name in left:              # a mark whose frame could not be moved: already recorded
                continue
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            if f.name in waiting or now - mtime < BLOCK_MS / 1000.0 * 2:
                pending += 1
                continue
            if not orphans:
                pending += 1
                continue
            path = _move_frame(s, f, mtime)
            if path:
                rec = _record(s, f'file|{f.name}|{mtime}', mtime, path, None, None, None, None, None)
                data['marks'].append(rec)
                new += 1
                changed = True
        if _clean(s, data, now):
            changed = True
        if changed:
            _save(data)
        return {'new': new, 'pending': pending}


def _refresh(s: Settings) -> str | None:
    """Make the running CS2 replay its console through the tap (a fresh connection). None = done, else why not."""
    from . import game
    if game.session(s) is None:
        return 'no session'
    try:
        with game.exclusive('player marks', wait=30):
            game.command(s, 'echo mcp_marks_sync', wait=0.3)
    except Exception as e:                 # busy, hung or closed: the buffered lines still count
        return str(e)
    return None


def summary(s: Settings) -> str | None:
    """One status line when unread marks wait (no console access)."""
    try:
        h = harvest(s)
        data = _load()
    except Exception:
        return None
    unread = sum(1 for m in data['marks'] if not m.get('seen'))
    if not unread and not h['pending']:
        return None
    text = f'{unread} new player marks' if unread else 'player marks waiting in CS2'
    return text + ': game_screenshot(views="marks")'


def listing(s: Settings, map_name: str | None = None, offset: int = 0, limit: int | None = None) -> dict:
    """Marks newest first: {shots: [{name, path, map, pos (feet), ang, time, note}], total, offset}."""
    why = _refresh(s)
    h = harvest(s, orphans=why is None or why == 'no session')
    with _HARVEST:
        data = _load()
        marks = sorted(data['marks'], key=lambda m: m.get('epoch', 0), reverse=True)
        if map_name:
            marks = [m for m in marks if m.get('map') in (map_name, None)]
        limit = 10 if limit is None else max(1, min(int(limit), 50))
        offset = max(0, int(offset))
        page = marks[offset:offset + limit]
        for m in page:
            m['seen'] = True
        if page:
            _save(data)
    shots = []
    for m in page:
        shot = {'name': m['id'], 'path': m.get('path'), 'map': m.get('map'), 'pos': m.get('pos'),
                'ang': m.get('ang'), 'time': m.get('time')}
        if m.get('note'):
            shot['note'] = m['note']
        if len(m.get('notes') or []) > 1:
            shot['notes'] = m['notes']
        if m.get('pos') is None:
            shot['unknown'] = 'position lost: CS2 was closed before the MCP read its console'
        shots.append(shot)
    out = {'shots': shots, 'total': len(marks), 'offset': offset}
    key = s.marks_key or 'off'
    if not marks:
        out['hint'] = (f'no player marks: ask the tester to press {key} in CS2 at each problem, then Y + text + '
                       'Enter (several lines are fine)' if s.marks_key else
                       'player marks are off (settings marks_key "")')
    if why and why != 'no session':
        out['warning'] = f'CS2 console not read ({why}); marks made since the last read may be missing'
    if h['pending']:
        out['pending'] = h['pending']
    return out


# ---- the key -----------------------------------------------------------------------------------------------------
def _defaults(s: Settings, key: str) -> set[str]:
    """Stock bindings of `key` (retail and Workshop Tools defaults): rebinding those takes nothing from the user."""
    values = {''}
    for name in ('user_keys_default.vcfg', 'user_keys_dev_default.vcfg'):
        try:
            text = (s.cs2_path / 'game' / 'csgo' / 'cfg' / name).read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for line in text.splitlines():
            hit = re.match(r'\s*"([^"]+)"\s+"([^"]*)"', line)
            if hit and hit.group(1).casefold() == key.casefold():
                values.add(hit.group(2))
    return values


def _bound(s: Settings, key: str) -> str | None:
    from . import game
    for line in game.query(s, f'bind {key}', ('" = "', "isn't a valid key"), timeout=3.0):
        if "isn't a valid key" in line:
            raise ValueError(f'settings marks_key {key!r} is not a CS2 key name (e.g. F8, INS, KP_END)')
        hit = _BIND.search(line)
        if hit and hit.group('key').casefold() == key.casefold():
            return hit.group('value')
    return None


def _write_cfg(s: Settings) -> None:
    cfg = s.game_addon / 'cfg' / f'{CFG}.cfg'
    ensure_inside(cfg, s.game_addon)
    try:
        if cfg.read_text(encoding='utf-8') == CFG_TEXT:
            return
    except OSError:
        pass
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(CFG_TEXT, encoding='utf-8')


def install(s: Settings) -> str:
    """Write the cfg and bind settings marks_key in the running CS2 (call under game.exclusive). A key moved or
    turned off gets its earlier binding back. Returns the key, 'off' or why it was not bound."""
    from . import game
    _write_cfg(s)
    key = s.marks_key
    with _HARVEST:
        data = _load()
        inst = data.get('install') or {}
        if inst.get('key') and inst['key'].casefold() != key.casefold():
            if _bound(s, inst['key']) == BIND:
                prev = inst.get('previous') or ''
                game.command(s, f'bind {inst["key"]} "{prev}"' if prev else f'unbind {inst["key"]}', wait=0.2)
            data['install'] = None
            _save(data)
        if not key:
            return 'off'
        current = _bound(s, key)
        if current is None:
            return f'{key} not bound: the console gave no answer'
        if current != BIND:
            if current not in _defaults(s, key):
                return f'{key} not bound: it is bound to "{current}"; set settings marks_key to a free key'
            game.command(s, f'bind {key} "{BIND}"', wait=0.2)
            if _bound(s, key) != BIND:
                return f'{key} not bound: CS2 did not accept the bind'
        if (data.get('install') or {}).get('key') != key:
            data['install'] = {'key': key, 'previous': current if current != BIND else (inst.get('previous') or '')}
            _save(data)
    return key


if __name__ == '__main__':          # python -m cs2map.marks install: bind the key in the running CS2, no map reload
    import sys
    from . import config, game
    if sys.argv[1:] != ['install']:
        sys.exit('usage: python -m cs2map.marks install')
    with game.exclusive('player marks install'):
        print(install(config.load_settings()))
