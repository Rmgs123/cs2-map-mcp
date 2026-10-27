"""Background process jobs (map builds, asset compiles, Blender runs) with logs.

A job is a child process whose combined stdout/stderr goes to logs/<job>.log.
State is kept in memory and mirrored to logs/<job>.json so it can still be
inspected after the MCP server restarts.  The command runs under cs2map.jobexit,
which writes logs/<job>.exit; if the process that started the job is gone,
get() recovers the result from that file and the log (see register_recovery).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import psutil

CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
SRC_ROOT = Path(__file__).resolve().parents[1]
# CS2MAP_OFFLINE=1 (tests while the game is in use, machines without CS2): these programs never start as jobs.
OFFLINE_BLOCKED = {'resourcecompiler.exe', 'cs2.exe', 'vrad3.exe'}
# kind -> fn(job, log_text) -> result dict; used when the watcher thread was lost
RECOVERY: dict[str, Callable[['Job', str], dict]] = {}


def register_recovery(kind: str, fn: Callable[['Job', str], dict]) -> None:
    RECOVERY[kind] = fn


@dataclass
class Job:
    id: str
    kind: str
    title: str
    argv: list[str]
    cwd: str
    log: str
    started: str
    state: str = 'running'           # running | succeeded | failed | cancelled | timeout | lost
    exit_code: int | None = None
    finished: str | None = None
    seconds: float | None = None
    timeout: float | None = None
    result: dict = field(default_factory=dict)
    pid: int | None = None
    meta: dict = field(default_factory=dict)


class JobManager:
    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    def start(self, kind: str, title: str, argv: list[str], cwd: Path, timeout: float | None = None,
              on_finish: Callable[[Job, str], dict] | None = None, env: dict | None = None,
              meta: dict | None = None) -> Job:
        if os.environ.get('CS2MAP_OFFLINE') and any(Path(str(a)).name.lower() in OFFLINE_BLOCKED for a in argv):
            raise RuntimeError('CS2MAP_OFFLINE is set: no compiler or game process may start (offline tests)')
        stamp = _dt.datetime.now().strftime('%Y%m%d-%H%M%S')
        job_id = f'{kind}-{stamp}-{uuid.uuid4().hex[:6]}'
        log = self.log_dir / f'{job_id}.log'
        job = Job(id=job_id, kind=kind, title=title, argv=[str(a) for a in argv], cwd=str(cwd), log=str(log),
                  started=_dt.datetime.now().isoformat(timespec='seconds'), timeout=timeout, meta=meta or {})
        fh = open(log, 'wb')
        fh.write(('$ ' + subprocess.list2cmdline(job.argv) + '\n\n').encode('utf-8'))
        fh.flush()
        run_env = {**os.environ, **(env or {})}
        run_env['PYTHONPATH'] = os.pathsep.join(x for x in (str(SRC_ROOT), run_env.get('PYTHONPATH')) if x)
        wrapped = [sys.executable, '-m', 'cs2map.jobexit', str(self._exit_file(job_id)), '--', *job.argv]
        # MCP clients put the server into a Windows job object that is killed with it; break away
        # so a build survives a server restart (falls back if the job object forbids breakaway).
        try:
            proc = subprocess.Popen(wrapped, cwd=str(cwd), stdout=fh, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=run_env,
                                    creationflags=CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB)
        except OSError:
            proc = subprocess.Popen(wrapped, cwd=str(cwd), stdout=fh, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=run_env, creationflags=CREATE_NO_WINDOW)
        job.pid = proc.pid
        with self._lock:
            self.jobs[job_id] = job
            self._procs[job_id] = proc
        self._save(job)
        threading.Thread(target=self._watch, args=(job, proc, fh, on_finish), daemon=True).start()
        return job

    def _watch(self, job: Job, proc: subprocess.Popen, fh, on_finish) -> None:
        t0 = time.monotonic()
        state = None
        try:
            proc.wait(timeout=job.timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc.pid)
            proc.wait()
            state = 'timeout'
        fh.close()
        job.exit_code = proc.returncode
        job.seconds = round(time.monotonic() - t0, 1)
        job.finished = _dt.datetime.now().isoformat(timespec='seconds')
        if job.state == 'cancelled':
            state = 'cancelled'
        state = state or ('succeeded' if proc.returncode == 0 else 'failed')
        if on_finish:
            try:
                text = Path(job.log).read_text(encoding='utf-8', errors='replace')
                job.result = on_finish(job, text) or {}
                if state == 'succeeded' and job.result.get('ok') is False:
                    state = 'failed'
            except Exception as e:  # never lose the job because a parser broke
                job.result = {'parse_error': repr(e)}
        job.state = state          # published last, so waiters always see the result too
        with self._lock:
            self._procs.pop(job.id, None)
        self._save(job)

    def _save(self, job: Job) -> None:
        tmp = self.log_dir / f'{job.id}.json.tmp'
        tmp.write_text(json.dumps(asdict(job), indent=1), encoding='utf-8')
        os.replace(tmp, self.log_dir / f'{job.id}.json')

    def _exit_file(self, job_id: str) -> Path:
        return self.log_dir / f'{Path(job_id).name}.exit'

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            meta = self.log_dir / f'{Path(job_id).name}.json'
            if not meta.exists():
                raise KeyError(f'unknown job {job_id}')
            job = _load_job(meta)
            if job is None:
                raise KeyError(f'{job_id} is not a job record')
            if job.state == 'running' and not (job.pid and psutil.pid_exists(job.pid)):
                self._recover(job)   # the process that watched this job is gone
        return job

    def _recover(self, job: Job) -> None:
        """Finish a job whose watcher died, from its .exit file and log; 'lost' if there is none."""
        exit_file = self._exit_file(job.id)
        try:
            job.exit_code = int(exit_file.read_text(encoding='ascii').strip())
        except (OSError, ValueError):
            job.state = 'lost'
            return
        job.finished = _dt.datetime.fromtimestamp(exit_file.stat().st_mtime).isoformat(timespec='seconds')
        job.seconds = round(exit_file.stat().st_mtime - _dt.datetime.fromisoformat(job.started).timestamp(), 1)
        state = 'succeeded' if job.exit_code == 0 else 'failed'
        fn = RECOVERY.get(job.kind)
        if fn:
            try:
                job.result = fn(job, Path(job.log).read_text(encoding='utf-8', errors='replace')) or {}
                if state == 'succeeded' and job.result.get('ok') is False:
                    state = 'failed'
            except Exception as e:
                job.result = {'parse_error': repr(e)}
        job.result['recovered'] = True
        job.state = state
        self._save(job)

    def wait(self, job_id: str, seconds: float) -> Job:
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            job = self.get(job_id)
            if job.state != 'running':
                return job
            time.sleep(0.25)
        return self.get(job_id)

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        proc = self._procs.get(job_id)
        if job.state == 'running' and proc is not None:
            job.state = 'cancelled'
            _kill_tree(proc.pid)
        return job

    def tail(self, job_id: str, lines: int = 40) -> list[str]:
        job = self.get(job_id)
        try:
            text = Path(job.log).read_text(encoding='utf-8', errors='replace')
        except OSError:
            return []
        return [ln for ln in text.splitlines() if ln.strip()][-lines:]

    def recent(self, limit: int = 10) -> list[dict]:
        metas = sorted(self.log_dir.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:limit * 4]
        out = []
        for m in metas:
            try:
                j = self.get(m.stem)
            except KeyError:     # other tools keep their own JSON logs here (script tests, cubemaps)
                continue
            out.append({'id': j.id, 'title': j.title, 'state': j.state, 'started': j.started, 'seconds': j.seconds})
            if len(out) >= limit:
                break
        return out

    def running(self) -> list[str]:
        with self._lock:
            return [j for j, p in self._procs.items() if p.poll() is None]


_JOB_FIELDS = set(Job.__dataclass_fields__)


def _load_job(path: Path) -> Job | None:
    """A job record from its JSON file, or None for a foreign JSON log; unknown keys go to meta."""
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not {'id', 'kind', 'argv', 'log', 'started'} <= data.keys():
        return None
    extra = {k: v for k, v in data.items() if k not in _JOB_FIELDS}
    job = Job(**{k: v for k, v in data.items() if k in _JOB_FIELDS})
    job.meta = {**extra, **(job.meta or {})}
    return job


def _kill_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = parent.children(recursive=True) + [parent]
    for p in procs:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(procs, timeout=10)
