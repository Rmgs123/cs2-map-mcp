"""Headless Blender for every pipeline (environment models, buildings, previews).

One executable (config.find_blender: settings, Steam libraries, tools/, Program Files), one profile (.local/blender), one protocol:
the script prints a line ``JSON>{...}``; the last such line becomes the job result.
start() runs Blender as a background job (job_status works, the log is in logs/),
run() starts it and waits.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .config import PROJECT_ROOT, Settings
from .jobs import Job, JobManager, register_recovery

PROFILE = PROJECT_ROOT / '.local' / 'blender'


class BlenderError(RuntimeError):
    pass


def parse_json_line(text: str) -> dict:
    found = None
    for line in text.splitlines():
        if line.startswith('JSON>'):
            try:
                found = json.loads(line[5:])
            except json.JSONDecodeError:
                continue
    return found or {}


def _result(job: Job, text: str) -> dict:
    result = parse_json_line(text)
    result.setdefault('ok', job.exit_code == 0)
    if job.exit_code != 0:
        errors = [ln.strip() for ln in text.splitlines()
                  if ln.strip().startswith(('ERROR>', 'Error', 'Traceback'))]
        if errors:
            result['errors'] = errors[-20:]
    return result


register_recovery('blender', _result)


def argv(s: Settings, script: Path, args: list | None = None) -> list[str]:
    from .config import BLENDER_SUPPORTED, blender_version
    if not s.blender_exe.exists():
        raise BlenderError(f'Blender not found: {s.blender_exe}. Install Blender 4.5+ (Steam or blender.org) '
                           'or set "blender" in config/settings.json; setup.ps1 finds it.')
    version = blender_version(s.blender_exe)
    lo, hi = BLENDER_SUPPORTED
    if version is not None and not lo <= version <= hi:
        raise BlenderError(f'Blender {version[0]}.{version[1]} at {s.blender_exe} is not supported: the pipelines '
                           f'are tested with {lo[0]}.{lo[1]}..{hi[0]}.x; set "blender" in config/settings.json')
    return [str(s.blender_exe), '-b', '--factory-startup', '--python-exit-code', '1',
            '--python', str(script), '--', *(str(x) for x in (args or []))]


def env() -> dict:
    for folder in ('config', 'scripts', 'extensions', 'datafiles'):
        (PROFILE / folder).mkdir(parents=True, exist_ok=True)
    return {'BLENDER_USER_CONFIG': str(PROFILE / 'config'),
            'BLENDER_USER_SCRIPTS': str(PROFILE / 'scripts'),
            'BLENDER_USER_EXTENSIONS': str(PROFILE / 'extensions'),
            'BLENDER_USER_DATAFILES': str(PROFILE / 'datafiles'),
            'PYTHONUTF8': '1'}


def start(s: Settings, jobs: JobManager, script: Path, args: list | None = None,
          title: str | None = None, timeout: float = 600) -> Job:
    """Run a Blender Python script in the background; job.result = its JSON line (+ ok, errors)."""
    if not math.isfinite(float(timeout)) or timeout <= 0:
        raise BlenderError('timeout must be a positive finite number')
    return jobs.start('blender', title or f'Blender {Path(script).name}', argv(s, Path(script), args),
                      PROJECT_ROOT, timeout=timeout, on_finish=_result, env=env())


def run(s: Settings, jobs: JobManager, script: Path, args: list | None = None,
        title: str | None = None, timeout: float = 600) -> Job:
    """start() and wait until Blender exits (or the timeout kills it)."""
    job = start(s, jobs, script, args, title, timeout)
    return jobs.wait(job.id, timeout + 30)
