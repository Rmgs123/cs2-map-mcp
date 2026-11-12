"""The status page: what the tools are doing, shown to the person at the PC.  Run:  python tools/panel.py

A small local web server (standard library only) on 127.0.0.1. It reads what the server processes already write
(job records and logs, the activity journal, frames, player marks, the permits file) and samples the load of the
PC; it never talks to CS2's console, so it cannot disturb a running test. Its buttons change config/settings.json
(the standing choices), the permits file (one-off answers) and may stop a job or run a deferred test.
One instance per port; actions need the page's token and a local Host header. Specification: docs/specs/panel.md.
"""
from __future__ import annotations

import collections
import ctypes
import ctypes.wintypes as wt
import datetime as _dt
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psutil

from . import __version__, activity, config, permits
from .config import PROJECT_ROOT

PAGE = Path(__file__).parent / 'data' / 'panel' / 'index.html'
THUMBS = PROJECT_ROOT / '.local' / 'tmp' / 'panel-thumbs'
THUMB_WIDTH, THUMB_KEEP = 480, 150
FRAMES_SHOWN = 12
FEED_SHOWN = 14
HISTORY, HISTORY_STEP = 120, 5.0           # 10 minutes of load
IDLE_EXIT_SECONDS = 10 * 60                # nobody looks, no server, no job: the panel ends
GROUPS = {'resourcecompiler.exe': 'compiler', 'vrad3.exe': 'compiler', 'cs2.exe': 'cs2', 'blender.exe': 'blender'}
PARTS = ('compiler', 'cs2', 'blender')

# A map build as its log shows it. The stage of the last mark seen is the current one.
STAGES = [('prepare', 'Prepare', 6), ('light', 'Light', 62), ('world', 'World', 9), ('vis', 'Visibility', 30),
          ('nav', 'Navigation', 4), ('bots', 'Bot data', 6), ('write', 'Write', 2)]
_MARKS = [(re.compile(r'==== Baking \d+ x \d+ lightmap|VRAD3 - Distributed Lighting Tool'), 'light'),
          (re.compile(r"\.\.\. Building '(?:world|phys)'"), 'world'),
          (re.compile(r"\.\.\. Building 'vis'|VIS3: Build partitioned PVS"), 'vis'),
          (re.compile(r"\.\.\. Building 'nav'|>>NAVGEN"), 'nav'),
          (re.compile(r'CSGO Compute AI Data'), 'bots'),
          (re.compile(r'Nav mesh \([^)]*\) loaded with'), 'write')]
_ROUND = re.compile(r'Round (\d+) cumulative runtime: ([\dms ]+)')
_STEP = re.compile(r'^== step (\d+)/(\d+): (.*)$', re.M)
_TITLE = re.compile(r'^(build|release) (\S+)(?: \((\w+)\))?')
CALL_NAMES = {'script_test': 'Script test', 'route_test': 'Route test', 'playtest': 'Play test',
              'game_screenshot': 'Frames', 'lighting_build': 'Light bake', 'game_launch': 'Starting CS2',
              'map_edit': 'Map edit', 'visual_check': 'Scene check', 'check_map': 'Map check',
              'model_build': 'Model build', 'physics_probe': 'Physics probe', 'game_reset': 'Game reset',
              'material_preview': 'Material preview', 'prop_preview': 'Prop preview', 'kit_info': 'Kit gallery',
              'assets_prepare': 'Preparing assets', 'map_build': 'Starting a build'}
QUIET_CALLS = ('job_status', 'status', 'read_guide')


# -- GPU share by process: the Windows counter "GPU Engine", read through PDH -----------------------------------------
class _PdhValue(ctypes.Structure):
    _fields_ = [('status', wt.DWORD), ('value', ctypes.c_double)]


class _PdhItem(ctypes.Structure):
    _fields_ = [('name', wt.LPWSTR), ('fmt', _PdhValue)]


class GpuCounter:
    """\\GPU Engine(pid_N_..._engtype_X)\\Utilization Percentage for every process and engine. A process group's
    share is its busiest engine (what Task Manager shows); the PC's share is the busiest engine of all."""
    PATH = r'\GPU Engine(*)\Utilization Percentage'
    _NAME = re.compile(r'pid_(\d+)_.*?(luid_\w+_phys_\d+_eng_\d+_engtype_\w+)')
    _Item = _PdhItem

    def __init__(self):
        self.ok = False
        self.query, self.counter = wt.HANDLE(), wt.HANDLE()
        try:
            self.pdh = ctypes.WinDLL('pdh')
            self.pdh.PdhOpenQueryW.argtypes = [wt.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wt.HANDLE)]
            self.pdh.PdhAddEnglishCounterW.argtypes = [wt.HANDLE, wt.LPCWSTR, ctypes.c_size_t,
                                                       ctypes.POINTER(wt.HANDLE)]
            self.pdh.PdhCollectQueryData.argtypes = [wt.HANDLE]
            self.pdh.PdhGetFormattedCounterArrayW.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.DWORD),
                                                              ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
            self.pdh.PdhCloseQuery.argtypes = [wt.HANDLE]
            if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)) & 0xFFFFFFFF:
                return
            if self.pdh.PdhAddEnglishCounterW(self.query, self.PATH, 0, ctypes.byref(self.counter)) & 0xFFFFFFFF:
                return
            self.pdh.PdhCollectQueryData(self.query)       # a rate needs two samples: this is the first
            self.ok = True
        except (OSError, AttributeError):
            pass

    def sample(self) -> dict | None:
        """{pid: {engine: percent}} since the previous sample, or None when the counter is not there."""
        if not self.ok:
            return None
        if self.pdh.PdhCollectQueryData(self.query) & 0xFFFFFFFF:
            return None
        size, count = wt.DWORD(0), wt.DWORD(0)
        self.pdh.PdhGetFormattedCounterArrayW(self.counter, 0x00000200, ctypes.byref(size), ctypes.byref(count), None)
        if not size.value:
            return {}
        buf = ctypes.create_string_buffer(size.value)
        if self.pdh.PdhGetFormattedCounterArrayW(self.counter, 0x00000200, ctypes.byref(size), ctypes.byref(count),
                                                 buf) & 0xFFFFFFFF:
            return {}
        items = ctypes.cast(buf, ctypes.POINTER(self._Item))
        out: dict[int, dict[str, float]] = {}
        for i in range(count.value):
            m = self._NAME.search(items[i].name or '')
            value = items[i].fmt.value
            if m and value > 0.05:
                engines = out.setdefault(int(m.group(1)), {})
                engines[m.group(2)] = engines.get(m.group(2), 0.0) + value
        return out

    def close(self) -> None:
        if self.ok:
            self.pdh.PdhCloseQuery(self.query)
            self.ok = False


def gpu_shares(sample: dict, groups: dict[int, str]) -> dict:
    """{compiler, cs2, blender, other, total} in percent from a GpuCounter sample and {pid: group}."""
    engines: dict[str, float] = {}
    by_group: dict[str, dict[str, float]] = {p: {} for p in PARTS}
    for pid, used in sample.items():
        group = groups.get(pid)
        for engine, value in used.items():
            engines[engine] = engines.get(engine, 0.0) + value
            if group:
                by_group[group][engine] = by_group[group].get(engine, 0.0) + value
    out = {p: min(100.0, max(by_group[p].values(), default=0.0)) for p in PARTS}
    ours = sum(out.values())
    if ours > 100.0:                         # groups on different engines: keep the picture within 100 %
        out = {p: v * 100.0 / ours for p, v in out.items()}
        ours = 100.0
    total = min(100.0, max(max(engines.values(), default=0.0), ours))
    out['other'] = max(0.0, total - ours)
    out['total'] = total
    return out


# -- load of the PC -------------------------------------------------------------------------------------------------
class Load:
    def __init__(self):
        self.gpu = GpuCounter()
        self.procs: dict[int, psutil.Process] = {}
        self.cores = psutil.cpu_count() or 1
        self.now: dict = {}
        self.history = {r: {'mcp': collections.deque(maxlen=HISTORY), 'sys': collections.deque(maxlen=HISTORY)}
                        for r in ('cpu', 'mem', 'gpu')}
        self._last_history = 0.0
        psutil.cpu_percent(None)

    def sample(self) -> None:
        groups: dict[int, str] = {}
        cpu = {p: 0.0 for p in PARTS}
        mem = {p: 0.0 for p in PARTS}
        total_mem = psutil.virtual_memory()
        for proc in psutil.process_iter(['name']):
            group = GROUPS.get((proc.info['name'] or '').lower())
            if not group:
                continue
            groups[proc.pid] = group
            known = self.procs.get(proc.pid)
            if known is None:
                known = self.procs[proc.pid] = proc
                try:
                    known.cpu_percent(None)                  # the first call only starts the measurement
                except psutil.Error:
                    pass
                continue
            try:
                cpu[group] += known.cpu_percent(None) / self.cores
                mem[group] += known.memory_info().rss / total_mem.total * 100
            except psutil.Error:
                pass
        for pid in [p for p in self.procs if p not in groups]:
            del self.procs[pid]
        cpu_total = psutil.cpu_percent(None)
        mem_total = (total_mem.total - total_mem.available) / total_mem.total * 100
        now = {'cpu': self._with_other(cpu, cpu_total), 'mem': self._with_other(mem, mem_total),
               'mem_total_gb': round(total_mem.total / 2**30, 1), 'gpu': None}
        raw = self.gpu.sample()
        if raw is not None:
            now['gpu'] = {k: round(v, 1) for k, v in gpu_shares(raw, groups).items()}
        self.now = now
        if time.monotonic() - self._last_history >= HISTORY_STEP - 0.2:
            self._last_history = time.monotonic()
            for res in ('cpu', 'mem', 'gpu'):
                part = now[res]
                if part is None:
                    continue
                self.history[res]['mcp'].append(round(sum(part[p] for p in PARTS), 1))
                self.history[res]['sys'].append(part['total'])

    @staticmethod
    def _with_other(parts: dict, total: float) -> dict:
        ours = sum(parts.values())
        total = max(min(100.0, total), min(100.0, ours))
        return {**{k: round(v, 1) for k, v in parts.items()}, 'other': round(max(0.0, total - ours), 1),
                'total': round(total, 1)}

    def state(self) -> dict:
        return {**self.now, 'step': HISTORY_STEP, 'points': HISTORY,
                'history': {r: {k: list(v) for k, v in h.items()} for r, h in self.history.items()}}


# -- jobs -----------------------------------------------------------------------------------------------------------
class JobView:
    """A running build read from its record and log: stage, percent against the last build of the same kind."""

    def __init__(self, s):
        from .jobs import JobManager
        self.s = s
        self.jobs = JobManager(s.logs)
        self._logs: dict[str, dict] = {}       # job id -> {offset, stage, detail, step}
        self._history: dict[str, dict] = {}

    def recent(self, limit: int = 40) -> list:
        try:
            metas = sorted(self.s.logs.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        except OSError:
            return []
        out = []
        for meta in metas:
            if not re.match(r'(build|release|compile|blender)-', meta.name):
                continue
            try:
                out.append(self.jobs.get(meta.stem))
            except (KeyError, OSError, ValueError):
                continue
        return out

    def _follow(self, job) -> dict:
        info = self._logs.setdefault(job.id, {'offset': 0, 'stage': 'prepare', 'detail': '', 'step': None})
        try:
            with open(job.log, 'rb') as fh:
                fh.seek(info['offset'])
                chunk = fh.read(2_000_000)
        except OSError:
            return info
        if not chunk:
            return info
        info['offset'] += len(chunk)
        text = chunk.decode('utf-8', errors='replace')
        steps = list(_STEP.finditer(text))
        if steps:                                # a release chain: the current step's own log
            info['step'] = [int(steps[-1].group(1)), int(steps[-1].group(2)), steps[-1].group(3)[:60]]
            info['stage'] = 'prepare'
            text = text[steps[-1].start():]
        best = -1
        for rx, stage in _MARKS:
            for m in rx.finditer(text):
                if m.start() > best:
                    best, info['stage'] = m.start(), stage
        rounds = _ROUND.findall(text)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith(('$ ', '[lockrun]'))]
        if info['stage'] == 'light' and rounds:
            info['detail'] = f'VRAD3 · round {rounds[-1][0]}, {rounds[-1][1].strip()}'
        elif lines:
            last = re.sub(r'\s+', ' ', lines[-1])
            info['detail'] = last[:70] + ('...' if len(last) > 70 else '')
        return info

    def view(self, job, who: str | None) -> dict:
        m = _TITLE.match(job.title or '')
        kind, map_name, preset = (m.group(1), m.group(2), m.group(3)) if m else (job.kind, '', None)
        flags = job.argv
        light = '-bakelighting' in flags or kind == 'release'
        name = {'release': 'Release build', 'compile': 'Compiling assets', 'blender': 'Blender'}.get(
            job.kind, 'Light bake' if light else 'Map build')
        elapsed = max(0.0, time.time() - _dt.datetime.fromisoformat(job.started).timestamp())
        out = {'kind': 'job', 'id': job.id, 'short': job.id.rsplit('-', 1)[-1], 'name': name, 'map': map_name,
               'preset': 'release' if kind == 'release' else preset, 'elapsed': round(elapsed), 'by': who,
               'title': job.title, 'low': bool(self.s.build_priority == 'low'), 'pct': None, 'left_min': None,
               'stages': None, 'detail': ''}
        if job.kind in ('build', 'release'):
            info = self._follow(job)
            present = {'prepare', 'world', 'write'}
            present |= {'light'} if light else set()
            present |= {'vis'} if '-vis' in flags or kind == 'release' else set()
            present |= {'nav', 'bots'} if '-nav' in flags or kind == 'release' else set()
            present.add(info['stage'])
            stages = [(k, label, w) for k, label, w in STAGES if k in present]
            index = next(i for i, st in enumerate(stages) if st[0] == info['stage'])
            if job.id not in self._history:
                title = permits.build_title(self.s, map_name, 'release') if kind == 'release' else job.title
                self._history[job.id] = permits.build_history(self.s.logs, title)
            last = self._history[job.id]['seconds']
            total = sum(w for _, _, w in stages)
            before = sum(w for _, _, w in stages[:index])
            if last:
                # The share of time gone, never behind the stage the log has reached nor past its end.
                pct = min(99.0, elapsed / last * 100)
                pct = max(pct, before / total * 100)
                pct = min(pct, max(before + stages[index][2], 1) / total * 100 if index < len(stages) - 1 else 99.0)
                out['pct'] = round(pct, 1)
                out['left_min'] = max(1, round((last - elapsed) / 60)) if last > elapsed else None
                frac = (pct / 100 * total - before) / stages[index][2]
            else:
                frac = 0.5
            out['stages'] = [[label, w] for _, label, w in stages]
            out['stage'] = [index, round(min(0.97, max(0.04, frac)), 3)]
            out['detail'] = info['detail']
            if info['step']:
                out['step'] = info['step']
        return out


# -- frames ---------------------------------------------------------------------------------------------------------
_STAMP = re.compile(r'-\d{8}-\d{6}(?:-\d{3})?$')


def frames(s) -> dict:
    """The newest frames of the tools and the player marks (F8), newest first."""
    items = []
    folder = s.screenshots
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if entry.is_file() and entry.name.lower().endswith(('.png', '.jpg')):
                    items.append((entry.stat().st_mtime, entry.name))
    except OSError:
        pass
    items.sort(reverse=True)
    out = [{'p': name, 'name': _STAMP.sub('', Path(name).stem), 't': t} for t, name in items[:FRAMES_SHOWN]]
    marks_total = 0
    try:
        data = json.loads((PROJECT_ROOT / '.local' / 'state' / 'marks.json').read_text(encoding='utf-8'))
        marks = data.get('marks') or []
    except (OSError, ValueError, AttributeError):
        marks = []
    for n, mark in enumerate(marks, 1):
        path = Path(mark.get('path') or '')
        try:
            rel = path.resolve().relative_to(folder.resolve()).as_posix()
        except (ValueError, OSError):
            continue
        if not path.is_file():
            continue
        marks_total += 1
        out.append({'p': rel, 'name': f'mark {n}', 't': mark.get('epoch') or path.stat().st_mtime, 'mark': True,
                    'map': mark.get('map'), 'note': ' / '.join(mark.get('notes') or [mark.get('note') or ''])[:160],
                    'seen': bool(mark.get('seen'))})
    out.sort(key=lambda f: f['t'], reverse=True)
    return {'items': out[:FRAMES_SHOWN], 'total': len(items), 'marks': marks_total}


def _inside(root: Path, rel: str) -> Path | None:
    try:
        path = (root / rel).resolve()
        path.relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return path if path.is_file() and path.suffix.lower() in ('.png', '.jpg', '.jpeg') else None


def thumb(path: Path) -> Path:
    from PIL import Image
    stat = path.stat()
    key = hashlib.sha1(f'{path}|{stat.st_mtime_ns}|{stat.st_size}'.encode('utf-8')).hexdigest()[:20]
    out = THUMBS / f'{key}.jpg'
    if out.is_file():
        return out
    THUMBS.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as im:
        im = im.convert('RGB')
        im.thumbnail((THUMB_WIDTH, THUMB_WIDTH))
        tmp = out.with_suffix('.tmp')
        im.save(tmp, 'JPEG', quality=82)
    os.replace(tmp, out)
    old = sorted(THUMBS.glob('*.jpg'), key=lambda p: p.stat().st_mtime, reverse=True)[THUMB_KEEP:]
    for stale in old:
        try:
            stale.unlink()
        except OSError:
            pass
    return out


# -- the panel ------------------------------------------------------------------------------------------------------
class Panel:
    def __init__(self, s=None):
        self.s = s or config.load_settings()
        self.token = secrets.token_urlsafe(18)
        self.load = Load()
        self.jobview = JobView(self.s)
        self.game: dict = {'running': False}
        self.seen = time.monotonic()            # the last time a page asked for the state
        self.started = time.monotonic()
        self._state: tuple[float, dict] | None = None
        self._lock = threading.Lock()
        self._tests_running = False
        self._settings_mtime = None
        self.stop = threading.Event()

    # .. settings follow the file (the user may edit it by hand while the panel runs)
    def settings(self):
        try:
            mtime = os.stat(config.SETTINGS_FILE).st_mtime_ns
        except OSError:
            return self.s
        if mtime != self._settings_mtime:
            try:
                self.s = config.load_settings(config.SETTINGS_FILE)
                self.jobview.s = self.s
                self._settings_mtime = mtime
            except Exception:                    # noqa: BLE001 - keep the last good ones
                pass
        return self.s

    # .. sampling thread: the load and what is slow to ask (is CS2 responding?)
    def sampler(self) -> None:
        tick = 0
        while not self.stop.is_set():
            watched = time.monotonic() - self.seen < 30
            try:
                self.load.sample()
                if watched or tick % 6 == 0:
                    self.game = self._game()
            except Exception:                    # noqa: BLE001 - one bad sample never ends the panel
                pass
            tick += 1
            self.stop.wait(2.0 if watched else HISTORY_STEP)

    def _game(self) -> dict:
        from . import game, system
        s = self.settings()
        cur = game.session(s)
        playing = [p for p in system.cs2_processes() if p['mode'] != 'tools']
        if cur is None:
            return {'running': False, 'playing': bool(playing)}
        out = {'running': True, 'pid': cur['pid'], 'started': cur.get('started'),
               'by_tools': bool(cur.get('launched_by_tool')), 'addon': cur.get('addon'), 'playing': bool(playing)}
        try:
            out['responding'] = not game.hung(s)
        except Exception:                        # noqa: BLE001
            out['responding'] = None
        try:
            loaded = json.loads(game.LOADED.read_text(encoding='utf-8'))
            if loaded.get('pid') == cur['pid']:
                out['map'] = loaded.get('map')
                out['loaded'] = game.LOADED.stat().st_mtime
        except (OSError, ValueError):
            pass
        try:
            wins = [w for w in system.windows(('cs2.exe',)) if w.pid == cur['pid']]
            view = next((w for w in wins if w.title.startswith('Counter-Strike 2')), None)
            front = system.foreground()
            out['window'] = ('front' if any(w.hwnd == front for w in wins) else
                             'minimized' if view is not None and view.minimized else 'behind')
        except Exception:                        # noqa: BLE001
            out['window'] = None
        return out

    # .. the state the page draws
    def state(self) -> dict:
        self.seen = time.monotonic()
        with self._lock:
            if self._state and time.monotonic() - self._state[0] < 1.0:
                return self._state[1]
            state = self._build()
            self._state = (time.monotonic(), state)
            return state

    def _build(self) -> dict:
        s = self.settings()
        midnight = _dt.datetime.combine(_dt.date.today(), _dt.time()).timestamp()
        journal = activity.read(limit=60, since=midnight)
        calls = journal['calls']
        who_started = {c['job']: c['who'] for c in calls if c.get('job')}
        jobs = self.jobview.recent()
        running = [j for j in jobs if j.state == 'running']
        now: dict
        if running:
            job = running[0]
            now = self.jobview.view(job, who_started.get(job.id))
            now['more'] = len(running) - 1
        else:
            call = next((c for c in calls if c['state'] == 'running' and c['tool'] not in QUIET_CALLS), None)
            if call:
                now = {'kind': 'call', 'name': CALL_NAMES.get(call['tool'], call['tool'].replace('_', ' ').capitalize()),
                       'tool': call['tool'], 'what': call['what'], 'by': call['who'], 'since': call['t']}
            else:
                now = {'kind': 'idle'}
                done = next((j for j in jobs if j.state != 'running'), None)
                last_call = next((c for c in calls if c['state'] != 'running' and c['tool'] not in QUIET_CALLS
                                  and c['who'] != 'panel'), None)
                if done and done.finished:
                    ended = _dt.datetime.fromisoformat(done.finished).timestamp()
                    if not last_call or ended >= (last_call.get('ended') or last_call['t']):
                        now['last'] = {'title': done.title, 'state': done.state, 'seconds': done.seconds, 't': ended}
                if 'last' not in now and last_call:
                    now['last'] = {'title': f'{last_call["tool"]} {last_call["what"]}'.strip(),
                                   'state': last_call['state'], 'seconds': last_call.get('seconds'),
                                   't': last_call.get('ended') or last_call['t'], 'text': last_call.get('text')}
        focus = journal['focus']
        game = dict(self.game)
        game['focus_count'] = len(focus)
        game['focus_seconds'] = round(sum(f.get('seconds') or 0 for f in focus), 1)
        waiting = permits.waiting()
        until = permits.focus_until()
        return {
            'version': __version__, 'time': time.time(), 'port': s.panel_port,
            'now': now,
            'frames': frames(s),
            'feed': calls[:FEED_SHOWN],
            'asks': {'builds': [b for b in waiting['builds'] if b.get('state') == 'waiting'],
                     'tests': [{k: t.get(k) for k in ('id', 'map', 'who', 'asked', 'state', 'needs', 'result',
                                                      'finished')} for t in waiting['tests']],
                     'tests_running': self._tests_running},
            'perms': {'focus': s.game_focus != 'never' or bool(until), 'focus_until': until,
                      'game_focus': s.game_focus, 'heavy': s.heavy_builds == 'allow',
                      'low': s.build_priority == 'low', 'restart': bool(s.restart_hung_cs2),
                      'restart_seconds': s.restart_hung_cs2_seconds, 'restart_user': bool(s.restart_hung_cs2_user)},
            'game': game,
            'load': self.load.state(),
        }

    # .. actions
    def act(self, body: dict) -> dict:
        do = body.get('do')
        s = self.settings()
        if do == 'toggle':
            key, on = body.get('key'), bool(body.get('on'))
            if key == 'focus':
                permits.allow_focus(None)
                values = {'game_focus': ('always' if s.game_focus == 'always' else 'input_only') if on else 'never'}
            elif key == 'heavy':
                values = {'heavy_builds': 'allow' if on else 'ask'}
                if on:
                    permits.allow_waiting_builds()
            elif key == 'low':
                values = {'build_priority': 'low' if on else 'normal'}
            elif key == 'restart':
                values = {'restart_hung_cs2': on}
            else:
                return {'error': 'unknown toggle'}
            config.update_settings(values, config.SETTINGS_FILE)
            self._note('settings', ', '.join(f'{k} = {json.dumps(v)}' for k, v in values.items()))
        elif do == 'focus15':
            permits.allow_focus(permits.FOCUS_MINUTES)
            self._note('focus', f'allowed for {permits.FOCUS_MINUTES} min')
        elif do == 'build':
            done = permits.answer_build(str(body.get('id')), bool(body.get('allow')))
            if done is None:
                return {'error': 'that question is gone'}
            self._note('map_build', 'allowed once' if body.get('allow') else 'not now',
                       f'{done["map"]} · {done["preset"]}', 'ok' if body.get('allow') else 'warn')
        elif do == 'run_tests':
            if self._tests_running:
                return {'error': 'the tests are already running'}
            ids = [t['id'] for t in permits.waiting()['tests'] if t.get('state') == 'waiting']
            if not ids:
                return {'error': 'no test is waiting'}
            self._tests_running = True
            threading.Thread(target=self._run_tests, args=(ids,), daemon=True).start()
        elif do == 'drop_test':
            permits.drop_test(str(body.get('id')))
        elif do == 'stop_job':
            job_id = Path(str(body.get('id'))).name
            try:
                job = self.jobview.jobs.cancel(job_id)
            except KeyError:
                return {'error': 'no such job'}
            self._note('job_stop', 'stopped from the panel', job.title, 'warn')
        else:
            return {'error': 'unknown action'}
        with self._lock:
            self._state = None
        return {'ok': True}

    def _note(self, tool: str, text: str, what: str = '', state: str = 'ok') -> None:
        activity.note('panel', tool=tool, what=what, text=text, state=state)

    def _run_tests(self, ids: list[str]) -> None:
        """Deferred script tests, one after another, each in its own process (the CS2 lock is theirs to take)."""
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join(
            x for x in (str(Path(__file__).resolve().parents[1]), os.environ.get('PYTHONPATH')) if x)}
        try:
            for ident in ids:
                try:
                    subprocess.run([sys.executable, '-m', 'cs2map.permits', 'run', ident], cwd=str(PROJECT_ROOT),
                                   env=env, timeout=240, creationflags=0x08000000, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    permits.finish_test(ident, {'ok': False, 'reason': f'the test did not finish: {exc}'[:200]})
        finally:
            self._tests_running = False

    # .. when to end
    def wanted(self) -> bool:
        if time.monotonic() - self.seen < IDLE_EXIT_SECONDS or time.monotonic() - self.started < 120:
            return True
        if self._tests_running or any(j.state == 'running' for j in self.jobview.recent(10)):
            return True
        for proc in psutil.process_iter(['cmdline']):
            try:
                if 'cs2map.server' in ' '.join(proc.info['cmdline'] or []):
                    return True
            except psutil.Error:
                pass
        return False


def log_text(limit: int = 400) -> str:
    """The journal as text, newest first (the page's "Open the full log")."""
    lines = []
    for c in activity.read(limit=limit)['calls']:
        when = _dt.datetime.fromtimestamp(c['t']).strftime('%Y-%m-%d %H:%M:%S')
        took = f'{c["seconds"]:.1f} s' if isinstance(c.get('seconds'), (int, float)) else ''
        lines.append(f'{when}  {c["state"]:8} {c["tool"]:18} {c["what"] or "":32} {c["who"] or "":14} {took:>9}  '
                     f'{c.get("text") or ""}')
    return '\n'.join(lines) + '\n'


def make_handler(panel: Panel):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'cs2map-panel'
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):          # quiet: a page polls every two seconds
            pass

        def _local(self) -> bool:
            """Only a page opened as 127.0.0.1 or localhost on our port (a foreign site cannot rebind its name)."""
            port = self.server.server_address[1]
            return self.headers.get('Host', '') in (f'127.0.0.1:{port}', f'localhost:{port}')

        def _send(self, code: int, body: bytes, kind: str, cache: str = 'no-store') -> None:
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', cache)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
                       'application/json; charset=utf-8')

        def do_GET(self):                      # noqa: N802
            if not self._local():
                return self._send(403, b'local only', 'text/plain')
            url = urlparse(self.path)
            try:
                if url.path in ('/', '/index.html'):
                    page = PAGE.read_text(encoding='utf-8').replace('__TOKEN__', panel.token)
                    return self._send(200, page.encode('utf-8'), 'text/html; charset=utf-8')
                if url.path == '/api/ping':
                    return self._json({'cs2map_panel': __version__})
                if url.path == '/api/state':
                    return self._json(panel.state())
                if url.path == '/log':
                    return self._send(200, log_text().encode('utf-8'), 'text/plain; charset=utf-8')
                if url.path in ('/thumb', '/frame'):
                    path = _inside(panel.s.screenshots, (parse_qs(url.query).get('p') or [''])[0])
                    if path is None:
                        return self._send(404, b'no such frame', 'text/plain')
                    if url.path == '/thumb':
                        path = thumb(path)
                    kind = 'image/png' if path.suffix.lower() == '.png' else 'image/jpeg'
                    return self._send(200, path.read_bytes(), kind, 'private, max-age=86400')
                return self._send(404, b'not found', 'text/plain')
            except (BrokenPipeError, ConnectionError):
                pass
            except Exception as exc:               # noqa: BLE001 - the page shows it and asks again
                try:
                    self._json({'error': f'{type(exc).__name__}: {exc}'[:300]}, 500)
                except OSError:
                    pass

        def do_POST(self):                     # noqa: N802
            port = self.server.server_address[1]
            origin = self.headers.get('Origin')
            if (not self._local() or self.headers.get('X-Panel-Token') != panel.token
                    or (origin and origin not in (f'http://127.0.0.1:{port}', f'http://localhost:{port}'))):
                return self._json({'error': 'reload the page'}, 403)
            if urlparse(self.path).path != '/api/action':
                return self._json({'error': 'not found'}, 404)
            try:
                size = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(min(size, 65536)) or b'{}')
                result = panel.act(body if isinstance(body, dict) else {})
            except Exception as exc:               # noqa: BLE001
                result = {'error': f'{type(exc).__name__}: {exc}'[:300]}
            self._json(result, 200 if result.get('ok') else 400)

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False                # with it Windows lets a second panel bind the same port

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def running(port: int, timeout: float = 0.4) -> bool:
    """Is a panel answering on this port?"""
    import urllib.request
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/ping', timeout=timeout) as response:
            return 'cs2map_panel' in json.loads(response.read() or b'{}')
    except Exception:                            # noqa: BLE001 - closed port, another program, a timeout
        return False


def ensure_started(s) -> str | None:
    """Start the panel process when settings "panel" is on and none answers. Returns its address."""
    if not getattr(s, 'panel', False) or os.environ.get('CS2MAP_OFFLINE'):
        return None
    if not running(s.panel_port):
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join(
            x for x in (str(Path(__file__).resolve().parents[1]), os.environ.get('PYTHONPATH')) if x)}
        flags = 0x08000000 | 0x00000008          # CREATE_NO_WINDOW | DETACHED_PROCESS
        argv = [sys.executable, '-m', 'cs2map.panel']
        kwargs = dict(cwd=str(PROJECT_ROOT), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, close_fds=True)
        try:                                     # outside the MCP client's job object: the page outlives a chat
            subprocess.Popen(argv, creationflags=flags | 0x01000000, **kwargs)
        except OSError:
            try:
                subprocess.Popen(argv, creationflags=flags, **kwargs)
            except OSError:
                return None
    return config.panel_url(s)


def serve(port: int | None = None, s=None) -> int:
    panel = Panel(s)
    port = port or panel.s.panel_port
    try:
        httpd = _Server(('127.0.0.1', port), make_handler(panel))
    except OSError:
        if running(port):
            print(f'the panel already runs: http://127.0.0.1:{port}/')
            return 0
        print(f'port {port} is taken by another program: set "panel_port" in config/settings.json')
        return 1
    threading.Thread(target=panel.sampler, daemon=True).start()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f'cs2map panel: http://127.0.0.1:{port}/', flush=True)
    try:
        while not panel.stop.wait(60):
            if not panel.wanted():
                break
    except KeyboardInterrupt:
        pass
    panel.stop.set()
    httpd.shutdown()
    panel.load.gpu.close()
    return 0


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description='cs2map status page on 127.0.0.1')
    parser.add_argument('--port', type=int, default=None, help='default: settings "panel_port" (8717)')
    args = parser.parse_args()
    if sys.stdout is not None and hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(serve(args.port))


if __name__ == '__main__':
    main()
