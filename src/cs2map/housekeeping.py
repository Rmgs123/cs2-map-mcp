"""Disk limits for what the tools produce: screenshots/, logs/, backups/maps/ and .local/tmp.

Screenshots, logs and old map backups are regenerable artefacts: beyond the limits (config/settings.json
"limits") the oldest ones are deleted, except files that README, docs/, reports/, STATUS.md or CHANGELOG.md
refer to. Map backups keep the newest `backups_per_map` copies of every map. Stale temporary folders in
.local/tmp (older than a day) are removed. Runs at server start at most once an hour (a background thread).
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path

from .config import PROJECT_ROOT, Settings

DEFAULTS = {'screenshots_mb': 800, 'logs_mb': 200, 'logs_files': 3000, 'backups_mb': 1500, 'backups_per_map': 15,
            'backups_map_mb': 800, 'backups_other_mb': 300, 'tmp_hours': 24}
KEEP_MIN_BACKUPS = 3      # the newest copies of a map always stay, whatever their size
STAMP = PROJECT_ROOT / '.local' / 'housekeeping.json'
REFERENCE_SOURCES = ('README.md', 'STATUS.md', 'CHANGELOG.md', 'docs', 'reports', 'src/cs2map/data')
# Curated images that data files point to (kit thumbnails verified in the game): never trimmed.
KEEP_DIRS = ('kits',)
_NAME = re.compile(r'[\w.\-]+\.(?:png|jpe?g|json|log|txt)', re.I)


def limits(s: Settings) -> dict:
    return {k: s.limit(k, v) for k, v in DEFAULTS.items()}


def referenced_names() -> set[str]:
    """File names mentioned in README, STATUS, CHANGELOG, docs/ and reports/ (kept whatever their age)."""
    names: set[str] = set()
    for source in REFERENCE_SOURCES:
        path = PROJECT_ROOT / source
        files = [path] if path.is_file() else [f for ext in ('*.md', '*.json') for f in path.rglob(ext)] if path.is_dir() else []
        for f in files:
            try:
                names.update(m.lower() for m in _NAME.findall(f.read_text(encoding='utf-8', errors='replace')))
            except OSError:
                continue
    return names


def _files(folder: Path, pattern: str = '*') -> list[Path]:
    out = []
    for p in folder.rglob(pattern) if folder.is_dir() else []:
        try:
            if p.is_file():
                out.append(p)
        except OSError:
            continue
    return out


def _trim(files: list[Path], limit_bytes: int, keep: set[str], dry_run: bool, max_files: int | None = None) -> dict:
    """Delete the oldest files until the total (and the count) fits the limit; files named in `keep` stay."""
    stats = []
    for p in files:
        try:
            st = p.stat()
            stats.append((st.st_mtime, st.st_size, p))
        except OSError:
            continue
    total = sum(s for _, s, _ in stats)
    removed = freed = 0
    count = len(stats)
    for mtime, size, p in sorted(stats):
        if total <= limit_bytes and (max_files is None or count <= max_files):
            break
        if p.name.lower() in keep:
            continue
        if not dry_run:
            try:
                p.unlink()
            except OSError:
                continue
        total -= size
        count -= 1
        removed += 1
        freed += size
    return {'files': len(stats) - removed, 'mb': round(total / 2**20), 'removed': removed, 'freed_mb': round(freed / 2**20)}


def _backups(root: Path, per_map: int, limit_bytes: int, dry_run: bool) -> dict:
    """backups/maps/<map>/<stamp>.vmap: keep the newest `per_map` of each map, then the size cap."""
    groups: dict[str, list[Path]] = {}
    for p in _files(root):
        groups.setdefault(p.parent.name, []).append(p)
    removed = freed = 0
    survivors = []
    for key, items in groups.items():
        items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        survivors += items[:per_map]
        for p in items[per_map:]:
            size = p.stat().st_size
            if not dry_run:
                try:
                    p.unlink()
                except OSError:
                    continue
            removed += 1
            freed += size
    rest = _trim(survivors, limit_bytes, set(), dry_run)
    return {'files': rest['files'], 'mb': rest['mb'], 'removed': removed + rest['removed'],
            'freed_mb': round(freed / 2**20) + rest['freed_mb']}


def trim_map_backups(s: Settings, folder: Path) -> int:
    """After a save: keep the newest `backups_per_map` copies of this map within `backups_map_mb` (the newest
    KEEP_MIN_BACKUPS always stay). A big map saved by many map_edit calls no longer fills the disk between the
    hourly cleanups. Returns the number of copies removed."""
    lim = limits(s)
    stats = []
    for p in folder.glob('*.vmap'):
        try:
            st = p.stat()
            stats.append((st.st_mtime, st.st_size, p))
        except OSError:
            continue
    stats.sort(reverse=True)
    total = removed = 0
    full = False
    for i, (_, size, p) in enumerate(stats):
        full = full or not (i < int(lim['backups_per_map']) and total + size <= lim['backups_map_mb'] * 2**20)
        if i < KEEP_MIN_BACKUPS or not full:
            total += size
            continue
        try:
            p.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _tmp(root: Path, hours: float, dry_run: bool) -> dict:
    removed = 0
    cutoff = time.time() - hours * 3600
    for p in root.iterdir() if root.is_dir() else []:
        try:
            if p.stat().st_mtime > cutoff:
                continue
            if not dry_run:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
            removed += 1
        except OSError:
            continue
    return {'removed': removed}


def cleanup(s: Settings, dry_run: bool = False) -> dict:
    """Apply the limits now. Returns what stayed and what was (or would be) removed per folder."""
    lim = limits(s)
    keep = referenced_names()
    shots = [f for f in _files(s.screenshots) if f.relative_to(s.screenshots).parts[0] not in KEEP_DIRS]
    out = {'screenshots': _trim(shots, lim['screenshots_mb'] * 2**20, keep, dry_run),
           'logs': _trim(_files(s.logs), lim['logs_mb'] * 2**20, keep, dry_run, int(lim['logs_files'])),
           'backups': _backups(s.backups / 'maps', int(lim['backups_per_map']), lim['backups_mb'] * 2**20, dry_run),
           # assets backed up before overwriting (control, materials, scripts, sound, story, weapons)
           'backups_other': _trim([f for d in s.backups.iterdir() if d.is_dir() and d.name != 'maps' for f in _files(d)]
                                  if s.backups.is_dir() else [], lim['backups_other_mb'] * 2**20, set(), dry_run),
           'tmp': _tmp(PROJECT_ROOT / '.local' / 'tmp', lim['tmp_hours'], dry_run),
           'limits': lim, 'dry_run': dry_run, 'time': time.strftime('%Y-%m-%d %H:%M:%S')}
    if not dry_run:
        STAMP.parent.mkdir(parents=True, exist_ok=True)
        STAMP.write_text(json.dumps(out, indent=1), encoding='utf-8')
    return out


def last() -> dict | None:
    try:
        return json.loads(STAMP.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def start_background(s: Settings, every_hours: float = 1.0) -> threading.Thread | None:
    """Clean in a daemon thread at server start, at most once per `every_hours` (never blocks a tool call)."""
    previous = last()
    if previous:
        try:
            if time.time() - time.mktime(time.strptime(previous['time'], '%Y-%m-%d %H:%M:%S')) < every_hours * 3600:
                return None
        except (KeyError, ValueError):
            pass
    thread = threading.Thread(target=lambda: cleanup(s), name='cs2map-housekeeping', daemon=True)
    thread.start()
    # A short session (one tool call) may end first: give the cleanup a few seconds at exit to finish.
    import atexit
    atexit.register(thread.join, 8)
    return thread
