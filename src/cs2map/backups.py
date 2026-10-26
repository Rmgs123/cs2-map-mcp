"""Backups: compressed copies of every map kept in tiers, plus deduplicated snapshots of the sources that build
the maps, all in one folder (settings "backups_dir"; best on another physical disk or a synced folder).

Layout of the backups folder:
  maps/<map>/<stamp>.vmap.zst      the map as it was at <stamp> (zstd); <stamp>.json: kind, sizes, sources, pin
  maps/<map>/<stamp>.vmap          uncompressed copies made by 1.0 (listed, restored, compressed by the next pass)
  maps/prefabs/<prefab>/...        prefabs saved by prefab_save
  sources/objects/<h2>/<sha256>.zst   the content of a source file, stored once whatever the number of snapshots
  sources/snapshots/<stamp>.json.zst  {roots: {label: folder}, files: {"label/path": [sha256, size, mtime_ns]}}
  <kind>/...                       assets backed up before overwriting (scripts, sound, materials, story...)

Sources are my/ (notes, styles, recipes, generators in my/maps/<map>/, photos), the addon's content folder
without its map files (own materials, models, sounds, scripts) and the folders in settings "backup_sources".

Saving a map: the old file gets a second name (an NTFS hard link, instant) in a pending folder next to it, the
new file is written, and a background worker compresses the old one into the store, links it to a sources
snapshot and trims the tiers. A process that dies first leaves the pending file; the next start finishes it.
The periodic pass (server start, then every 30 minutes) also keeps a copy of every map changed since its newest
backup (a Hammer save, the end of a session), takes a sources snapshot and trims everything.

Tiers per map: the newest `backups_keep_last` copies, the oldest copy of each hour of the last `backups_hourly`
hours (the map before the work of that hour: complete, not half a rebuild), of each day of `backups_daily` days
and of each week of `backups_weekly` weeks, and pinned milestones (map_backups pin) forever; `backups_map_mb`
caps one map. A sources snapshot stays while it is among the newest five, the newest of a tier bucket, pinned or
referenced by a kept map copy.
"""
from __future__ import annotations

import atexit
import datetime as _dt
import fnmatch
import hashlib
import json
import os
import queue
import re
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from compression import zstd

from .config import PROJECT_ROOT, Settings

LEVEL_MAP = 9            # 144 MB map -> 16 MB in ~1 s, in the background (zstd releases the GIL)
LEVEL_SOURCE = 3
STAMP_FORMAT = '%Y%m%d-%H%M%S-%f'
STAMP = re.compile(r'^\d{8}-\d{6}-\d{6}$')
PENDING = '.cs2map_pending'
DEFAULTS = {'backups_keep_last': 3, 'backups_hourly': 24, 'backups_daily': 14, 'backups_weekly': 8,
            'backups_map_mb': 1000}
KEEP_MIN = 3              # the newest copies of a map always stay, whatever their size
SOURCE_KEEP_LAST = 5
SOURCE_MAX_MB = 256       # larger files are listed as skipped in the snapshot
SKIP_DIRS = {'__pycache__', '.git', '.svn', 'node_modules', PENDING}
SKIP_SUFFIXES = ('.pyc', '.tmp', '.pending')
PERIOD_S = 30 * 60
SETTLE_S = 120            # a map changed less than this ago may still be written: copy it next time
RESTORED = PROJECT_ROOT / 'restored'
MY_ROOT: Path | None = None   # tests set it; None = mylayer.root() (settings "my_dir")
LEGACY_ROOT = PROJECT_ROOT / 'backups'     # where 1.0 kept every backup


class BackupError(RuntimeError):
    pass


class Busy(BackupError):
    """Another process holds the lock."""


# -- settings -------------------------------------------------------------------------------------------
def limits(s: Settings | None) -> dict:
    out = dict(DEFAULTS)
    if s is not None:
        out = {k: s.limit(k, v) for k, v in DEFAULTS.items()}
        legacy = s.limit('backups_per_map', None)             # the 1.0 name of keep_last
        if legacy is not None and s.limit('backups_keep_last', None) is None:
            out['backups_keep_last'] = int(legacy)
    out['backups_keep_last'] = max(KEEP_MIN, int(out['backups_keep_last']))
    return out


def source_roots(s: Settings) -> list[tuple[str, Path, tuple[str, ...]]]:
    """(label, folder, excluded top-level glob patterns) of everything a sources snapshot covers."""
    from . import mylayer
    roots = [('my', MY_ROOT or mylayer.root(), ()), ('addon', s.content_addon, ('maps/*.vmap',))]
    seen = {'my', 'addon'}
    for raw in s.backup_sources:
        path = Path(raw) if Path(raw).is_absolute() else PROJECT_ROOT / raw
        label = re.sub(r'[^\w.-]', '_', path.name) or 'source'
        while label in seen:
            label += '_'
        seen.add(label)
        roots.append((label, path, ()))
    return roots


def _now() -> _dt.datetime:
    """UTC: copies are ordered by their names, and a local clock jumps (25.10: Windows changed the time zone and
    new copies sorted before older ones, so "the newest three" kept the wrong ones)."""
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def stamp_now() -> str:
    return _now().strftime(STAMP_FORMAT)


def _when(stamp: str) -> _dt.datetime:
    return _dt.datetime.strptime(stamp, STAMP_FORMAT)


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    with open(tmp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


@contextmanager
def _file_lock(path: Path, timeout: float = 120):
    """Cross-process lock (sources store: a snapshot never meets a cleanup of its objects)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a+b') as fh:
        try:
            import msvcrt
        except ImportError:                       # not Windows: no lock (the project runs on Windows)
            yield
            return
        deadline = time.monotonic() + timeout
        while True:
            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise Busy(f'{path} is busy in another process; repeat the call') from None
                time.sleep(0.2)
        try:
            yield
        finally:
            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass


# -- saving a map: stage the old file, finish in the background ----------------------------------------------
def stage(target: Path, group: Path) -> dict | None:
    """Before `target` is overwritten: give its current content a second name next to it (a hard link: instant
    and on the same drive; a copy where links are not supported). Returns the job for submit()."""
    target = Path(target)
    if not target.is_file():
        return None
    stamp = stamp_now()
    pending = target.parent / PENDING
    pending.mkdir(exist_ok=True)
    held = pending / f'{target.stem}.{stamp}.pending'
    try:
        os.link(target, held)
    except OSError:
        shutil.copy2(target, held)
    job = {'held': str(held), 'map': target.stem, 'stamp': stamp, 'group': str(group), 'kind': 'save',
           'map_mtime_ns': target.stat().st_mtime_ns}
    _write_atomic(held.with_suffix('.json'), json.dumps(job).encode())
    return job


def destination(job: dict) -> Path:
    return Path(job['group']) / job['map'] / f'{job["stamp"]}.vmap.zst'


_queue: queue.Queue = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_errors: list[str] = []
_inflight: set[str] = set()
RECOVER_AFTER_S = 600     # a held file older than this belongs to a process that ended before its worker


def submit(job: dict | None, s: Settings | None = None) -> None:
    """Finish a staged backup in the background worker (one thread per process, jobs in order)."""
    global _worker
    if job is None:
        return
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name='cs2map-backups', daemon=True)
            _worker.start()
    _inflight.add(job['held'])
    _queue.put((job, s))


def _run() -> None:
    while True:
        job, s = _queue.get()
        try:
            finish(job, s)
        except Exception as e:                    # the pending file stays: the next pass retries it
            _errors.append(f'{job.get("map")} {job.get("stamp")}: {type(e).__name__}: {e}')
            del _errors[:-20]
        finally:
            _inflight.discard(job['held'])
            _queue.task_done()


def flush(timeout: float = 60) -> bool:
    """Wait until every staged backup of this process is in the store. False on timeout."""
    deadline = time.monotonic() + timeout
    while _queue.unfinished_tasks:
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


atexit.register(flush, 60)


_recent_snapshot: tuple[float, str, str] | None = None
SNAPSHOT_REUSE_S = 30     # saves of one rebuild (seconds apart) share a sources snapshot


def _sources_now(s: Settings) -> str | None:
    """The sources snapshot for a copy being finished: a fresh one, or this process's snapshot of the last
    SNAPSHOT_REUSE_S seconds (a rebuild saves a map 20 times a minute; its generators do not change meanwhile)."""
    global _recent_snapshot
    store = str(s.backups)
    if (_recent_snapshot and _recent_snapshot[2] == store and time.monotonic() - _recent_snapshot[0] < SNAPSHOT_REUSE_S
            and (_sources(s) / 'snapshots' / f'{_recent_snapshot[1]}.json.zst').is_file()):
        return _recent_snapshot[1]
    stamp = snapshot_sources(s)
    _recent_snapshot = (time.monotonic(), stamp, store)
    return stamp


def finish(job: dict, s: Settings | None = None, sources: str | None = None) -> Path:
    """Compress a held map into the store, link it to a sources snapshot (`sources`, or a current one), remove
    the held file, trim."""
    held = Path(job['held'])
    dest = destination(job)
    try:
        data = held.read_bytes()
    except FileNotFoundError:                     # another process finished it
        return dest
    meta = {'kind': job.get('kind', 'save'), 'bytes_raw': len(data), 'map_mtime_ns': job.get('map_mtime_ns')}
    if job.get('pin'):
        meta['pin'] = job['pin']
    if sources:
        meta['sources'] = sources
    elif s is not None:
        try:
            meta['sources'] = _sources_now(s)
        except Exception as e:                    # a broken source file must not lose the map copy
            meta['sources_error'] = f'{type(e).__name__}: {e}'
    packed = zstd.compress(data, LEVEL_MAP)
    meta['bytes'] = len(packed)
    _write_atomic(dest, packed)
    _write_atomic(dest.with_name(f'{job["stamp"]}.json'), json.dumps(meta).encode())
    for p in (held, held.with_suffix('.json')):
        try:
            p.unlink()
        except OSError:
            pass
    trim_map(dest.parent, s)
    return dest


def recover(s: Settings) -> int:
    """Finish the held files a process left (it ended before its worker). Returns how many."""
    done = 0
    for folder in [s.content_addon / 'maps' / PENDING, s.content_addon / 'maps' / 'prefabs' / PENDING]:
        for side in sorted(folder.glob('*.json')) if folder.is_dir() else []:
            job = _read_json(side)
            if not job or job.get('held') in _inflight or not Path(job.get('held', '')).is_file():
                continue
            try:
                if time.time() - side.stat().st_mtime < RECOVER_AFTER_S:
                    continue                      # its process may still be finishing it
            except OSError:
                continue
            finish(job, s)
            done += 1
    return done


# -- the store ---------------------------------------------------------------------------------------------
def entries(folder: Path) -> list[dict]:
    """Copies of one map, newest first: {id, path, bytes, meta}."""
    out = []
    for p in folder.iterdir() if folder.is_dir() else []:
        name = p.name
        stamp = name[:-len('.vmap.zst')] if name.endswith('.vmap.zst') else name[:-5] if name.endswith('.vmap') else ''
        if not STAMP.match(stamp):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        out.append({'id': stamp, 'path': p, 'bytes': size, 'meta': _read_json(folder / f'{stamp}.json')})
    out.sort(key=lambda e: e['id'], reverse=True)
    return out


def read_copy(entry: dict) -> bytes:
    data = Path(entry['path']).read_bytes()
    return zstd.decompress(data) if str(entry['path']).endswith('.zst') else data


def _tier_keep(stamps: list[str], lim: dict, now: _dt.datetime, pick) -> set[str]:
    """Stamps the tiers keep: pick(stamps of one bucket) for each hour / day / week bucket inside its window."""
    keep: set[str] = set()
    tiers = ((lim['backups_hourly'] * 3600, lambda d: d.strftime('%Y%m%d%H')),
             (lim['backups_daily'] * 86400, lambda d: d.strftime('%Y%m%d')),
             (lim['backups_weekly'] * 7 * 86400, lambda d: '%d-%02d' % d.isocalendar()[:2]))
    for window, bucket in tiers:
        buckets: dict[str, list[str]] = {}
        for st in stamps:
            when = _when(st)
            if (now - when).total_seconds() <= window:
                buckets.setdefault(bucket(when), []).append(st)
        keep |= {pick(group) for group in buckets.values()}
    return keep


def _remove_copy(entry: dict) -> int:
    freed = 0
    folder = Path(entry['path']).parent
    for p in (Path(entry['path']), folder / f'{entry["id"]}.json'):
        try:
            freed += p.stat().st_size
            p.unlink()
        except OSError:
            pass
    return freed


def trim_map(folder: Path, s: Settings | None = None, now: _dt.datetime | None = None) -> int:
    """Apply the tiers and the size cap to one map's copies. Returns the number removed."""
    lim = limits(s)
    now = now or _now()
    items = entries(folder)
    if not items:
        return 0
    stamps = [e['id'] for e in items]
    ends = {e['id'] for e in items if e['meta'].get('kind') in ('live', 'pin')}

    def pick(group: list[str]) -> str:
        # A copy taken outside a save holds a finished session; a copy before a save may be half a rebuild,
        # except the first one of a bucket (the map before that bucket's work).
        done = [st for st in group if st in ends]
        return max(done) if done else min(group)

    keep = set(stamps[:lim['backups_keep_last']]) | _tier_keep(stamps, lim, now, pick)
    keep |= {e['id'] for e in items if e['meta'].get('pin')}
    removed = 0
    for e in items:
        if e['id'] not in keep:
            _remove_copy(e)
            removed += 1
    kept = [e for e in items if e['id'] in keep]
    cap = lim['backups_map_mb'] * 2**20
    total = sum(e['bytes'] for e in kept)
    for e in reversed(kept[KEEP_MIN:]):                          # oldest first, the newest three stay
        if total <= cap:
            break
        if e['meta'].get('pin'):
            continue
        total -= e['bytes']
        _remove_copy(e)
        removed += 1
    return removed


def trim_all(s: Settings, cap_bytes: int, dry_run: bool = False) -> dict:
    """Tiers for every map, then one cap for all maps together: the oldest copies go first, never a pinned
    one nor the newest three of a map."""
    removed = 0 if dry_run else sum(trim_map(d, s) for d in all_map_dirs(s))
    pool, total = [], 0
    for d in all_map_dirs(s):
        items = entries(d)
        total += sum(e['bytes'] for e in items)
        pool += [e for e in items[KEEP_MIN:] if not e['meta'].get('pin')]
    freed = 0
    for e in sorted(pool, key=lambda e: e['id']):
        if total <= cap_bytes:
            break
        total -= e['bytes']
        freed += e['bytes'] if dry_run else _remove_copy(e)
        removed += 1
    return {'mb': round(total / 2**20), 'removed': removed, 'freed_mb': round(freed / 2**20)}


def all_map_dirs(s: Settings) -> list[Path]:
    root = s.backups / 'maps'
    out = [d for d in root.iterdir() if d.is_dir() and d.name != 'prefabs'] if root.is_dir() else []
    pre = root / 'prefabs'
    out += [d for d in pre.iterdir() if d.is_dir()] if pre.is_dir() else []
    return out


# -- sources -------------------------------------------------------------------------------------------------
def _sources(s: Settings) -> Path:
    return s.backups / 'sources'


def _object(s: Settings, sha: str) -> Path:
    return _sources(s) / 'objects' / sha[:2] / f'{sha}.zst'


def snapshots(s: Settings) -> list[str]:
    folder = _sources(s) / 'snapshots'
    out = [p.name[:-len('.json.zst')] for p in folder.glob('*.json.zst')] if folder.is_dir() else []
    return sorted((x for x in out if STAMP.match(x)), reverse=True)


def load_snapshot(s: Settings, stamp: str) -> dict:
    path = _sources(s) / 'snapshots' / f'{stamp}.json.zst'
    try:
        return json.loads(zstd.decompress(path.read_bytes()))
    except (OSError, ValueError, zstd.ZstdError) as e:
        raise BackupError(f'sources snapshot {stamp} cannot be read: {e}') from None


def _excluded(rel: str, patterns: tuple[str, ...]) -> bool:
    """'maps/*.vmap' matches maps/a.vmap, not maps/prefabs/b.vmap ('*' stays inside one folder)."""
    parts = rel.split('/')
    return any(len(parts) == len(pat.split('/')) and all(map(fnmatch.fnmatchcase, parts, pat.split('/')))
               for pat in patterns)


def _walk(root: Path, exclude: tuple[str, ...]):
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        base = Path(folder)
        for f in files:
            if f.endswith(SKIP_SUFFIXES):
                continue
            path = base / f
            rel = path.relative_to(root).as_posix()
            if not _excluded(rel, exclude):
                yield rel, path


def snapshot_sources(s: Settings, pin: str | None = None) -> str | None:
    """Store every changed source file once and write a snapshot (none when nothing changed: the newest one is
    returned). Unchanged files (same size and modification time as in the newest snapshot) are not read."""
    with _file_lock(_sources(s) / '.lock'):
        names = snapshots(s)
        previous = load_snapshot(s, names[0]) if names else {}
        known = previous.get('files', {})
        files: dict[str, list] = {}
        skipped = []
        roots = {}
        for label, root, exclude in source_roots(s):
            if not root.is_dir():
                continue
            roots[label] = str(root)
            for rel, path in _walk(root, exclude):
                key = f'{label}/{rel}'
                try:
                    st = path.stat()
                except OSError:
                    continue
                if st.st_size > SOURCE_MAX_MB * 2**20:
                    skipped.append(key)
                    continue
                old = known.get(key)
                if old and old[1] == st.st_size and old[2] == st.st_mtime_ns and _object(s, old[0]).is_file():
                    files[key] = old
                    continue
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                sha = hashlib.sha256(data).hexdigest()
                obj = _object(s, sha)
                if not obj.is_file():
                    _write_atomic(obj, zstd.compress(data, LEVEL_SOURCE))
                files[key] = [sha, len(data), st.st_mtime_ns]
        if names and files == known and not pin:
            return names[0]
        stamp = stamp_now()
        doc = {'stamp': stamp, 'roots': roots, 'files': files, 'skipped': skipped}
        if pin:
            doc['pin'] = pin
        _write_atomic(_sources(s) / 'snapshots' / f'{stamp}.json.zst',
                      zstd.compress(json.dumps(doc, separators=(',', ':')).encode(), 9))
    return stamp


def trim_sources(s: Settings, now: _dt.datetime | None = None) -> dict:
    """Snapshots by tier (the newest of each bucket), the ones kept map copies point to, pinned ones; then the
    objects no kept snapshot uses are removed."""
    lim = limits(s)
    now = now or _now()
    with _file_lock(_sources(s) / '.lock'):
        names = snapshots(s)
        if not names:
            return {'snapshots': 0, 'removed': 0, 'objects_removed': 0}
        used = set()
        for d in all_map_dirs(s):
            used |= {e['meta'].get('sources') for e in entries(d)}
        used.discard(None)
        keep = set(names[:SOURCE_KEEP_LAST]) | _tier_keep(names, lim, now, max) | used
        docs = {}
        for st in names:
            try:
                docs[st] = load_snapshot(s, st)
            except BackupError:
                continue
            if docs[st].get('pin'):
                keep.add(st)
        removed = 0
        for st in names:
            if st not in keep:
                try:
                    (_sources(s) / 'snapshots' / f'{st}.json.zst').unlink()
                    removed += 1
                except OSError:
                    pass
        live = {v[0] for st in keep if st in docs for v in docs[st].get('files', {}).values()}
        gone = 0
        objects = _sources(s) / 'objects'
        unreadable = [st for st in keep if st not in docs]
        if unreadable:                            # never guess what an unreadable snapshot needs
            return {'snapshots': len(names) - removed, 'removed': removed, 'objects_removed': 0,
                    'unreadable': unreadable[:5]}
        for p in objects.rglob('*') if objects.is_dir() else []:
            if p.is_file() and (p.name.endswith('.tmp') or p.name[:-4] not in live):
                if p.name.endswith('.tmp') and time.time() - p.stat().st_mtime < 86400:
                    continue
                try:
                    p.unlink()
                    gone += 1
                except OSError:
                    pass
    return {'snapshots': len(names) - removed, 'removed': removed, 'objects_removed': gone}


def restore_sources(s: Settings, stamp: str, dest: Path | None = None) -> dict:
    """Write a snapshot's files into restored/<stamp>/<label>/... (never over the live folders)."""
    doc = load_snapshot(s, stamp)
    dest = Path(dest or RESTORED / stamp)
    count = missing = 0
    for key, (sha, size, _) in doc.get('files', {}).items():
        obj = _object(s, sha)
        target = dest / key
        try:
            data = zstd.decompress(obj.read_bytes())
        except (OSError, zstd.ZstdError):
            missing += 1
            continue
        _write_atomic(target, data)
        count += 1
    return {'folder': str(dest), 'files': count, 'missing': missing, 'roots': doc.get('roots', {})}


# -- whole-map copies outside a save ------------------------------------------------------------------------
def copy_live(s: Settings, path: Path, group: Path, kind: str = 'live', pin: str | None = None,
              sources: str | None = None) -> Path:
    """Copy a map file as it is now into the store (a pin, or the periodic copy of a changed map)."""
    path = Path(path)
    stamp = stamp_now()
    pending = path.parent / PENDING
    pending.mkdir(exist_ok=True)
    held = pending / f'{path.stem}.{stamp}.pending'
    shutil.copy2(path, held)
    job = {'held': str(held), 'map': path.stem, 'stamp': stamp, 'group': str(group), 'kind': kind,
           'map_mtime_ns': path.stat().st_mtime_ns}
    if pin:
        job['pin'] = pin
    _write_atomic(held.with_suffix('.json'), json.dumps(job).encode())
    if pin:
        sources = snapshot_sources(s, pin=pin)
    return finish(job, s, sources)


def changed_maps(s: Settings, now: float | None = None) -> list[Path]:
    """Map files whose content is in no backup yet and that were not written in the last two minutes."""
    now = now or time.time()
    out = []
    for path in sorted((s.content_addon / 'maps').glob('*.vmap')):
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            continue
        if now - mtime / 1e9 < SETTLE_S:
            continue
        if any(e['meta'].get('map_mtime_ns') == mtime for e in entries(s.backups / 'maps' / path.stem)[:5]):
            continue
        out.append(path)
    return out


def migrate_legacy(s: Settings) -> dict:
    """Bring 1.0 backups under the store: uncompressed map copies are compressed (same stamp), and with a
    backups_dir outside the project the old project/backups folder moves there."""
    out = {'compressed': 0, 'moved': 0}
    old = LEGACY_ROOT
    if old.resolve() != s.backups.resolve() and old.is_dir():
        for p in sorted(old.rglob('*')):
            if not p.is_file():
                continue
            rel = p.relative_to(old)
            target = s.backups / rel
            if target.exists() or (rel.parts[0] == 'maps' and (target.parent / (target.name + '.zst')).exists()):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            if target.stat().st_size == p.stat().st_size:
                p.unlink()
                out['moved'] += 1
        for folder, _, _ in sorted(os.walk(old), key=lambda w: -len(w[0])):     # the emptied folders, deepest first
            try:
                os.rmdir(folder)
            except OSError:
                pass
    for d in all_map_dirs(s):
        for e in entries(d):
            path = Path(e['path'])
            if path.suffix != '.vmap':
                continue
            data = path.read_bytes()
            packed = zstd.compress(data, LEVEL_MAP)
            dest = path.with_name(f'{e["id"]}.vmap.zst')
            _write_atomic(dest, packed)
            if zstd.decompress(dest.read_bytes()) == data:
                meta = e['meta'] or {'kind': 'legacy', 'bytes_raw': len(data)}
                meta['bytes'] = len(packed)
                _write_atomic(d / f'{e["id"]}.json', json.dumps(meta).encode())
                path.unlink()
                out['compressed'] += 1
    return out


# -- the periodic pass ---------------------------------------------------------------------------------------
def _state(s: Settings) -> Path:
    return s.backups / 'state.json'


def periodic(s: Settings, force: bool = False) -> dict | None:
    """Recover held files, snapshot sources, copy changed maps, bring 1.0 copies in, trim. One process at a
    time; at most once per PERIOD_S after a completed pass unless forced. Every step can be repeated, so a
    process that ends half way leaves nothing broken: the next pass does the rest."""
    if not force and time.time() - _read_json(_state(s)).get('time', 0) < PERIOD_S:
        return None
    try:
        with _file_lock(s.backups / '.periodic.lock', timeout=0):
            out: dict = {'recovered': recover(s), 'sources': snapshot_sources(s)}
            copied = 0
            for path in changed_maps(s):
                try:
                    copy_live(s, path, s.backups / 'maps', sources=out['sources'])
                    copied += 1
                except OSError:
                    continue
            out['copied'] = copied
            out.update(migrate_legacy(s))
            out['trimmed'] = sum(trim_map(d, s) for d in all_map_dirs(s))
            out['sources_trim'] = trim_sources(s)
            out['time'] = time.strftime('%Y-%m-%d %H:%M:%S')
            _write_atomic(_state(s), json.dumps({'time': time.time(), 'last': out}).encode())
            return out
    except Busy:                                  # another process runs the pass right now
        return None


_periodic_thread: threading.Thread | None = None


def start_periodic(s: Settings) -> threading.Thread:
    """Run the periodic pass now and then every PERIOD_S in a daemon thread (never blocks a tool call)."""
    global _periodic_thread

    def loop():
        while True:
            try:
                periodic(s)
            except Exception as e:
                _errors.append(f'periodic: {type(e).__name__}: {e}')
                del _errors[:-20]
            time.sleep(PERIOD_S)

    if _periodic_thread is None or not _periodic_thread.is_alive():
        _periodic_thread = threading.Thread(target=loop, name='cs2map-backups-periodic', daemon=True)
        _periodic_thread.start()
    return _periodic_thread


# -- reports -------------------------------------------------------------------------------------------------
def _size(folder: Path) -> int:
    total = 0
    for p in folder.rglob('*') if folder.is_dir() else []:
        try:
            total += p.stat().st_size if p.is_file() else 0
        except OSError:
            pass
    return total


def _drive(path: Path) -> str:
    return os.path.splitdrive(os.path.realpath(path))[0].upper() or '/'


def where(s: Settings) -> dict:
    """The folder and whether it shares a drive with the project or the CS2 addon (one failure loses both)."""
    folder = s.backups
    drive = _drive(folder)
    same = [label for label, p in (('project', PROJECT_ROOT), ('addon', s.content_addon)) if _drive(p) == drive]
    out = {'folder': str(folder)}
    if same:
        out['warning'] = (f'backups are on the same drive as the {" and ".join(same)}: set "backups_dir" in '
                          'config/settings.json to another disk or a synced folder (REPAIR.md "Backups")')
    return out


def summary(s: Settings) -> dict:
    names = snapshots(s)
    out = {**where(s), 'maps_mb': round(_size(s.backups / 'maps') / 2**20),
           'sources_mb': round(_size(_sources(s)) / 2**20), 'snapshots': len(names)}
    if names:
        out['newest_sources_min'] = round((_now() - _when(names[0])).total_seconds() / 60)
    if _errors:
        out['errors'] = _errors[-3:]
    return out


def listing(s: Settings, name: str, limit: int = 40) -> dict:
    flush(30)
    items = entries(s.backups / 'maps' / name)
    now = _now()
    rows = []
    for e in items[:limit]:
        age = (now - _when(e['id'])).total_seconds()
        row = {'id': e['id'], 'mb': round(e['bytes'] / 2**20, 1),
               'age': f'{age / 60:.0f} min' if age < 7200 else f'{age / 3600:.0f} h' if age < 172800 else f'{age / 86400:.0f} d',
               'kind': e['meta'].get('kind', 'legacy')}
        for k in ('pin', 'sources'):
            if e['meta'].get(k):
                row[k] = e['meta'][k]
        rows.append(row)
    return {'map': name, **where(s), 'count': len(items), 'backups': rows}


def find(s: Settings, name: str, backup_id: str) -> dict:
    if not STAMP.match(backup_id or ''):
        raise BackupError(f'backup id looks like 20261025-141554-534906, got {backup_id!r}; see map_backups')
    flush(30)
    for e in entries(s.backups / 'maps' / name):
        if e['id'] == backup_id:
            return e
    raise BackupError(f'no backup {backup_id} for map {name}; see map_backups')
