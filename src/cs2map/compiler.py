"""resourcecompiler.exe: map builds and asset compiles, with log analysis."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from .config import Settings
from .jobs import Job, JobManager, register_recovery

PRESETS = {
    # fast iteration: geometry + collision + entities, no baked lighting
    'fast': ['-world', '-phys', '-nolightmaps'],
    # bots without baked light: navigation only (a GPU without hardware ray tracing cannot bake light)
    'nav': ['-world', '-phys', '-nav', '-gridnav', '-nolightmaps'],
    # playable test: baked lighting (low res) and bot navigation; no visibility (vis is slow on open maps)
    'full': ['-world', '-phys', '-nav', '-gridnav', '-bakelighting', '-lightmapMaxResolution', '512',
             '-lightmapVRadQuality', '1'],
    # release: high quality lighting + visibility (can take many minutes)
    'final': ['-world', '-phys', '-vis', '-nav', '-gridnav', '-bakelighting', '-lightmapMaxResolution', '2048',
              '-lightmapVRadQuality', '2'],
}
BASE = ['-fshallow', '-maxtextureres', '256', '-dxlevel', '110', '-quiet', '-unbufferedio', '-noassert']
TAIL = ['-retail', '-breakpad', '-nop4']

_ERROR = re.compile(r'(\berror\b|\bfailed\b|\bfatal\b|access violation|0xc0000005|unable to|could not|cannot|'
                    r'too many charts)',
                    re.IGNORECASE)
_WARNING = re.compile(r'\bwarning\b', re.IGNORECASE)
_SUMMARY = re.compile(r'(\d+)\s+compiled,\s+(\d+)\s+failed(?:,\s+(\d+)\s+skipped)?', re.IGNORECASE)
_MISSING = re.compile(r'(?:Failed loading resource "|missing[^\n"]*?|not found[^\n"]*?|can\'t find[^\n"]*?|'
                      r'cannot find[^\n"]*?|couldn\'t find[^\n"]*?|failed to (?:load|open)[^\n"]*?)'
                      r'([\w/\\.-]+\.(?:vmat|vtex|vmdl|vpcf|vsnd|png|tga|fbx|obj|dmx|vmap|vsmart|vdata|vjs|vts)(?:_c)?)',
                      re.IGNORECASE)
# lines that contain "error"/"failed" but are not problems
_NOISE = re.compile(r'(0 failed|error\(s\): 0|errors: 0|no errors|failed: 0|_failed\b|ErrorModel|error\.vmdl|'
                    r'scripts/detail_prop_types\.vdata_c|Fossilize|'
                    r'GetFbxMaterialPath Failed)', re.IGNORECASE)
HINTS = {
    'No lightmap resolution volumes': 'add a mesh with material materials/tools/toolslightmapres.vmat covering the '
                                      'playable area (op add_mesh, shape box) for correct baked-light resolution',
    '.vpk Failed!': 'the compiled map file is locked, usually because CS2 has this map loaded; map_build and '
                    'lighting_build switch the game to the idle map first - rebuild; if it repeats, load another '
                    'map in CS2 (game_command "map mcp_idle") and rebuild',
    'Unable to remove': 'another lighting bake of this addon was running; rebuild (bakes are queued now)',
    'Too many charts for the lightmap': 'the map has more lightmap UV charts than even a 2048 atlas holds: merge '
                                        'or remove small render-only pieces, or split the map',
    'no walkable seeds present': 'the log says ">>NAVGEN: Skipped... no walkable seeds present": the bot '
                                 'navigation mesh is EMPTY (bots will not join or move). Add point_nav_walkable '
                                 'entities standing on the floor (z = floor + 1..8) near spawns and in each area, '
                                 'then rebuild with preset nav or full; check_map warns about missing seeds',
    '[navguard] stopped': 'the build was stopped after NAVGEN: the nav mesh has too many areas and the bot data '
                          'would take hours (see nav in the result). Limit the nav (set_play_area, add_nav_markup '
                          'nonav) or build without bots (map_build bots=false)',
}
# Baked light (VRAD3, presets full/final) runs on the GPU with hardware ray tracing; on a card without it the bake
# fails (a user's friend, 1.0 on a PC without an RTX card). Any of these in a failed build's log gets the hint.
GPU_HINT = ('baked light (presets full and final) needs a GPU with hardware ray tracing (for example NVIDIA RTX 20 '
            'series or newer) and a current driver; without one build with preset nav (bots) or fast: the map works, '
            'without baked light (flatter, darker shadows)')
GPU_KEYS = ('gpu hang', 'device_lost', 'ray tracing', 'raytracing', 'vk_error', 'no suitable gpu')
_NAV = re.compile(r'Nav mesh \([^)]*\) loaded with (\d+) nav areas')
NAV_FLAGS = ('-nav', '-gridnav')
# A world wider than this (square units, about 310 x 310 m) is an open map: VIS costs hours there (a 830 m terrain
# map: 3368 s of clustering alone, then 2+ h of merging) and gives nothing (215-240 FPS with frustum culling only).
VIS_AUTO_MAX_FOOTPRINT = 1.5e8


def flags(preset: str, bots: bool | None = None, vis: bool | None = None) -> list[str]:
    """Compiler flags of a preset; bots=False drops the nav, bots=True adds it; vis=False drops visibility."""
    out = list(PRESETS[preset])
    if bots is False:
        out = [f for f in out if f not in NAV_FLAGS]
    elif bots and '-nav' not in out:
        at = out.index('-phys') + 1
        out[at:at] = list(NAV_FLAGS)
    if vis is False:
        out = [f for f in out if f != '-vis']
    elif vis and '-vis' not in out:
        out.insert(out.index('-phys') + 1, '-vis')
    return out


def open_map(m) -> dict:
    """Footprint of the solid world (square units) and whether VIS is worth it (no on big open maps)."""
    from . import checks
    b = checks._map_bounds([(n, bb) for n, bb in checks._all_meshes(m) if checks._is_solid_mesh(n)])
    if not b:
        return {'footprint': 0, 'vis': True}
    lo, hi = b
    foot = (hi[0] - lo[0]) * (hi[1] - lo[1])
    return {'footprint': round(foot), 'size_m': [round((hi[0] - lo[0]) * 0.0254), round((hi[1] - lo[1]) * 0.0254)],
            'vis': foot <= VIS_AUTO_MAX_FOOTPRINT}


# Stages of a map build as its log shows them (the last one seen is where it is now).
_STAGES = [
    (re.compile(r'VRAD3 - Distributed Lighting Tool'), 'baking light (VRAD3, GPU)'),
    (re.compile(r'VIS3: Build partitioned PVS'), 'visibility (VIS: voxelize, cluster, merge; slow on open maps)'),
    (re.compile(r'Generated clusters for \d+ regions'), 'visibility: merging clusters'),
    (re.compile(r'>>NAVGEN: pass'), 'nav mesh (NAVGEN, usually under a minute)'),
    (re.compile(r'CSGO Compute AI Data'), 'bot data (CSGO Compute AI Data: one CPU core, no progress lines)'),
    (re.compile(r'Compute Early Occupy Times \[.*Done'), 'bot data: hiding spots'),
    (re.compile(r'Compute Hiding spots \[.*Done'), 'bot data: encounter spots'),
    (re.compile(r'Compute Encounter spots \[.*Done'), 'bot data: approach areas (the long one)'),
    (re.compile(r'Nav mesh \([^)]*\) loaded with'), 'writing the map'),
]
_STEP = re.compile(r'^== step (\d+)/(\d+): (.*)$', re.M)


def progress(text: str) -> dict:
    """Where a running map build is: the last stage seen in its log, the nav area count and the bot data estimate."""
    from . import navcost
    steps = list(_STEP.finditer(text))
    if steps:                                   # a chain (release): only the current step's own log counts
        text = text[steps[-1].start():]
    best, stage = -1, None
    for rx, label in _STAGES:
        for m in rx.finditer(text):
            if m.start() > best:
                best, stage = m.start(), label
    out = {'stage': stage or 'compiling (world, physics, assets)'}
    if steps:
        out['step'] = f'{steps[-1].group(1)}/{steps[-1].group(2)}: {steps[-1].group(3)[:80]}'
    areas = navcost.from_log(text)
    if areas is not None:
        out['nav_areas'] = areas
        out['ai_data_estimate'] = navcost.eta(areas)
    return out


def analyze_log(text: str) -> dict:
    # lockrun retried with a larger lightmap atlas: only the last attempt counts.
    text = text[text.rfind('[lockrun] retry:'):] if '[lockrun] retry:' in text else text
    lines = [ln.rstrip() for ln in text.splitlines()]
    errors = [ln.strip() for ln in lines if _ERROR.search(ln) and not _NOISE.search(ln)]
    warnings = [ln.strip() for ln in lines if _WARNING.search(ln) and not _NOISE.search(ln)]
    summary = None
    for m in _SUMMARY.finditer(text):
        summary = {'compiled': int(m.group(1)), 'failed': int(m.group(2)),
                   'skipped': int(m.group(3) or 0)}
    missing = sorted({m.group(1).replace('\\', '/').removesuffix('_c') for m in _MISSING.finditer(text)}
                     - {'scripts/detail_prop_types.vdata'})

    def dedupe(items, limit):
        seen, out = set(), []
        for x in items:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out[:limit], len(seen)

    errors, n_err = dedupe(errors, 40)
    warnings, n_warn = dedupe(warnings, 40)
    low = text.lower()
    hints = [h for key, h in HINTS.items() if key.lower() in low]
    if (errors or (summary or {}).get('failed')) and any(k in low for k in GPU_KEYS):
        hints.append(GPU_HINT)
    nav = _NAV.findall(text)
    out = {'summary': summary, 'errors': errors, 'error_count': n_err, 'warnings': warnings,
           'warning_count': n_warn, 'missing_assets': missing[:50],
           **({'nav_areas': int(nav[-1])} if nav else {}), **({'hints': hints} if hints else {})}
    from . import navcost
    counted = navcost.from_log(text)
    if counted is not None:
        out['nav_areas'] = int(nav[-1]) if nav else counted
        out['nav'] = navcost.verdict(out['nav_areas'], navcost.STOP_AREAS)['message']
    if '[navguard] stopped' in text:
        out['nav_stopped'] = True
        out['nav'] = next(ln.split('] ', 1)[1] for ln in reversed(lines) if ln.startswith('[navguard] stopped'))
    elif navcost.NO_SEEDS in text:
        out['nav'] = 'empty: ">>NAVGEN: Skipped... no walkable seeds present" (no point_nav_walkable)'
    return out


def _threads(s: Settings) -> str:
    return str(s.compile_threads or os.cpu_count() or 4)


def build_argv(s: Settings, name: str, map_flags: list[str], timeout: float, nav_max: int | None = None,
               probe: bool = False) -> list[str]:
    """resourcecompiler for one map; under lockrun when it bakes light (one VRAD3 at a time) or builds nav (guard)."""
    argv = [str(s.resourcecompiler), '-threads', _threads(s), *BASE, '-i', str(s.map_path(name)), *map_flags, *TAIL]
    # VRAD3 uses one _vrad3 work folder per addon: serialize lighting bakes across processes/agents
    lock = f'vrad3-{s.addon}' if '-bakelighting' in map_flags else '-'
    guard = []
    if '-nav' in map_flags:
        from . import navcost
        guard = ['--nav-max', str(nav_max if nav_max is not None else navcost.max_areas(s))]
        guard += ['--nav-probe'] if probe else []
    if lock != '-' or guard:
        argv = [sys.executable, '-m', 'cs2map.lockrun', lock, str(timeout), *guard, '--', *argv]
    return argv


def build_map(s: Settings, jobs: JobManager, name: str, preset: str = 'fast', timeout: float = 1800,
              map_flags: list[str] | None = None) -> Job:
    if preset not in PRESETS:
        raise ValueError(f'preset must be one of {sorted(PRESETS)}')
    vmap = s.map_path(name)
    if not vmap.exists():
        raise FileNotFoundError(f'map not found: {vmap}')
    argv = build_argv(s, name, list(map_flags if map_flags is not None else PRESETS[preset]), timeout)
    vpk = s.compiled_map(name)
    before = vpk.stat().st_mtime if vpk.exists() else None

    def finish(job: Job, text: str) -> dict:
        return _build_result(job, text, vpk, before)

    env = {'PYTHONPATH': str(Path(__file__).resolve().parents[1]), 'PYTHONUTF8': '1'}
    return jobs.start('build', f'build {name} ({preset})', argv, s.game_bin, timeout, finish, env=env,
                      meta={'output': str(vpk), 'vpk_mtime_before': before})


def _build_result(job: Job, text: str, vpk: Path, before: float | None) -> dict:
    info = analyze_log(text)
    fresh = vpk.exists() and (before is None or vpk.stat().st_mtime > before)
    info['output'] = str(vpk) if fresh else None
    crashed = any('.mdmp' in e.lower() or 'access violation' in e.lower() for e in info['errors'])
    failed = (info['summary'] or {}).get('failed', 0)
    info['ok'] = job.exit_code == 0 and fresh and not failed and not crashed
    if job.exit_code == 0 and not fresh:
        info['note'] = 'compiler exited 0 but did not write a new .vpk'
    return info


def _compile_result(job: Job, text: str) -> dict:
    info = analyze_log(text)
    failed = (info['summary'] or {}).get('failed', 0)
    info['ok'] = job.exit_code == 0 and not failed
    return info


register_recovery('build', lambda job, text: _build_result(
    job, text, Path(job.meta.get('output', '')), job.meta.get('vpk_mtime_before')))
register_recovery('compile', _compile_result)


def compile_assets(s: Settings, jobs: JobManager, paths: list[Path], force: bool = False,
                   timeout: float = 600) -> Job:
    """Compile content files (.vmat, .vmdl, .vtex, .vsmart, .vsndevts ...)."""
    if not paths:
        raise ValueError('no assets to compile')
    argv = [str(s.resourcecompiler), '-threads', _threads(s), '-nop4', '-retail']
    for p in paths:
        argv += ['-i', str(p)]
    if force:
        argv.append('-f')

    return jobs.start('compile', f'compile {len(paths)} asset(s)', argv, s.game_bin, timeout, _compile_result)


def build_cubemaps(s: Settings, name: str, timeout: float = 600) -> dict:
    """Bake runtime cubemaps for a loaded addon map through VConsole.

    Source 2's ``resourcecompiler`` does not bake ``env_cubemap_array``.  The
    supported path is the in-game ``buildcubemaps`` command with all windows
    rendered.  A Workshop Tools process is reused and never stopped here.
    """
    from . import game, vconsole

    sess = game.session(s)
    if sess is None:
        raise game.GameError(f'no CS2 Workshop Tools session with addon {s.addon} is running')
    if sess.get('addon') != s.addon:
        # buildcubemaps writes into the session's own addon, never into a map mounted with map_workshop (09.10)
        raise game.GameError(f'reflections of addon {s.addon} need CS2 Workshop Tools started with that addon '
                             f'(it runs with {sess.get("addon")}): ask the user to restart Workshop Tools with addon '
                             f'{s.addon}, then call lighting_build again')
    vpk = s.compiled_map(name)
    before = vpk.stat().st_mtime if vpk.exists() else 0
    with game.exclusive('C-cubemap'):
        game.load_map(s, name)
        query = game.command(s, 'r_always_render_all_windows', wait=0.05)
        original = 'false'
        for line in reversed(query):
            low = line.casefold()
            if 'true' in low:
                original = 'true'
                break
            if 'false' in low:
                original = 'false'
                break
        game.command(s, 'r_always_render_all_windows true', wait=0.5)
        output: list[str] = []
        sentinel = False
        try:
            try:
                sentinel, output = vconsole.run_until(['buildcubemaps'], 'Re-loading map', timeout)
            except vconsole.VConsoleError as exc:
                output.append(str(exc))
            # CS2 may drop the TCP connection while rendering; availability
            # plus a fresh status is the completion signal in that case.
            if not sentinel:
                deadline = __import__('time').monotonic() + min(timeout, 60)
                while __import__('time').monotonic() < deadline and not vconsole.available():
                    __import__('time').sleep(1)
                if vconsole.available():
                    status = game.command(s, 'status', wait=1.0)
                    output.extend(status)
                    sentinel = any('loaded spawngroup' in ln.casefold() and name.casefold() in ln.casefold()
                                   for ln in status)
        finally:
            try:
                game.command(s, f'r_always_render_all_windows {original}', wait=0.5)
            except Exception as exc:
                output.append(f'could not restore r_always_render_all_windows: {exc}')
        flagged = [ln for ln in output if ('error' in ln.casefold() or 'failed' in ln.casefold())
                   and '0 error' not in ln.casefold() and 'unable to remove' not in ln.casefold()]
        # Renderer chatter while the map reloads (a texture still streaming in) says nothing about the bake: a fresh
        # VPK with its cubemap array is the proof (15.10: three "returning error texture" lines failed a good bake).
        noise = ('error texture', 'gettextureresource')
        errors = [ln for ln in flagged if not any(x in ln.casefold() for x in noise)]
        warnings = [ln for ln in flagged if ln not in errors]
        from .vpk import VPK
        fresh = vpk.exists() and vpk.stat().st_mtime >= before
        cubemap_entry = False
        if fresh:
            try: cubemap_entry = any('env_cubemap_array' in n for n in VPK(vpk).entries)
            except Exception: pass
        return {'map': name, 'ok': bool(sentinel and fresh and cubemap_entry and not errors), 'sentinel': sentinel,
                'vpk_fresh': fresh, 'cubemap_entry': cubemap_entry,
                'restored_r_always_render_all_windows': original, 'errors': errors[-20:],
                'warnings': warnings[-5:], 'output_tail': output[-80:]}
