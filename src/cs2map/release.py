"""map_build(name, "release"): one map in its own clean addon, ready for the Steam Workshop.

The project addon (mcp_lab) holds every test map; the Workshop Manager uploads a whole addon. So a release copies
the map and only the files it needs into a new addon named after the map (or `addon`), writes a sound event file
with just the used events, compiles every copied resource there, builds the map with the final preset, makes the
radar, and writes PUBLISH.md with the steps and texts for the Workshop Manager. One background job
(cs2map.releaserun) does the compiling, a nav measurement on big maps and the build; job_status reports the stage.
Bots and VIS are decided before the build (Project.build_plan): no VIS on big open maps, no bots when the nav would
take hours; the light bake's work files are dropped from the uploaded folder.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import re
import shutil
import sys
import time
from pathlib import Path

from . import addon as addon_mod, compiler, kv3
from .config import check_name, ensure_inside

MARKER = 'cs2map_release.json'
# addon-relative resource paths inside map, script, model, material, layout and particle files
PATH = re.compile(r'(?<![{}/A-Za-z0-9_])((?:materials|models|sounds|particles|panorama|maps/scripts|'
                  r'scripts|soundevents)/[A-Za-z0-9_./\-]+?\.(?:vmat|vmdl|vsnd|vpcf|vjs|vxml|vcss|vtex|vsvg|png|tga|'
                  r'jpg|jpeg|psd|exr|fbx|dmx|obj|wav|mp3|js|xml|css|svg|vanmgrph|vsndevts|txt))(?![A-Za-z0-9_])')
STYLE_PATH = re.compile(r'file://\{(resources|images)\}/([A-Za-z0-9_./\-]+\.[a-z]+)')
EVENT = re.compile(r'\bmcp\.[A-Za-z0-9_.]+[A-Za-z0-9_]')
LEAF = {'.png', '.tga', '.jpg', '.jpeg', '.psd', '.exr', '.wav', '.mp3', '.svg', '.blend', '.blend1'}
SOURCE_OF = {'.vjs': ['.js'], '.vxml': ['.xml'], '.vcss': ['.css'], '.vsvg': ['.svg'], '.vsnd': ['.wav', '.mp3'],
             '.vtex': ['.png', '.tga', '.psd', '.jpg', '.exr', '.vtex']}
COMPILE = {'.vmat', '.vmdl', '.vpcf', '.js', '.xml', '.css', '.wav', '.mp3', '.vsndevts'}


class ReleaseError(ValueError):
    pass


def _source(content: Path, rel: str) -> str | None:
    """The content file behind a referenced path (x.vjs -> x.js, x.vsnd -> x.wav ...), or None."""
    p = Path(rel)
    if (content / rel).is_file():
        return rel
    for ext in SOURCE_OF.get(p.suffix.lower(), []):
        cand = p.with_suffix(ext).as_posix()
        if (content / cand).is_file():
            return cand
    return None


def _refs(text: str) -> set[str]:
    out = set()
    for m in PATH.finditer(text):
        out.add(m.group(1))
    # panorama: {resources} is the panorama folder, {images} its images folder
    for kind, rel in STYLE_PATH.findall(text):
        root = 'panorama/images/' if kind == 'images' else 'panorama/'
        out.add(rel if rel.startswith('panorama/') else root + rel)
    return out


def _map_strings(vmap_path: Path) -> str:
    from .vmap import VMap
    m = VMap.load(vmap_path)
    parts = []
    for el in m.doc.elements():
        for attr in el.attrs.values():
            v = attr.value
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, (list, tuple)):
                parts.extend(x for x in v if isinstance(x, str))
    return '\n'.join(parts)


def collect(s, name: str) -> dict:
    """Content files (addon-relative), compiled-only files and sound events the map needs, followed recursively."""
    content, game = s.content_addon, s.game_addon
    seeds = _refs(_map_strings(s.map_path(name)))
    text = _map_strings(s.map_path(name))
    events = set(EVENT.findall(text))
    # loading picture, icon, radar and their hidden layouts (made by game_screenshot preview / full builds)
    for extra in (f'panorama/images/map_icons/screenshots/1080p/{name}.png', f'panorama/images/map_icons/map_icon_{name}.svg',
                  f'panorama/layout/custom_game/mcp_preview_{name}.xml', f'panorama/styles/custom_game/mcp_preview_{name}.css'):
        seeds.add(extra)
    files, compiled_only, todo, seen = set(), set(), list(seeds), set()
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        src = _source(content, rel)
        if src is None:
            if (game / (rel + '_c')).is_file():
                compiled_only.add(rel + '_c')
            continue
        files.add(src)
        if Path(src).suffix.lower() in LEAF:
            continue
        try:
            body = (content / src).read_bytes().decode('latin-1')
        except OSError:
            continue
        events |= set(EVENT.findall(body))
        todo.extend(r for r in _refs(body) if r not in seen)
    return {'files': sorted(files), 'compiled_only': sorted(compiled_only), 'events': sorted(events)}


def _events(s, wanted: set[str]) -> tuple[str | None, list[str], set[str]]:
    """A sound event file with only the wanted events (and the addon events they derive from); their sounds."""
    path = s.content_addon / 'soundevents' / 'soundevents_addon.vsndevts'
    if not wanted or not path.is_file():
        return None, [], set()
    header, tree = kv3.loads(path.read_text(encoding='utf-8-sig'))
    keep, todo = {}, sorted(wanted)
    while todo:
        name = todo.pop()
        body = tree.get(name) if isinstance(tree, dict) else None
        if body is None or name in keep:
            continue
        keep[name] = body
        base = body.get('base') if isinstance(body, dict) else None
        if isinstance(base, str) and base.startswith('mcp.'):
            todo.append(base)
    sounds = set()
    for body in keep.values():
        sounds |= {m.group(1) for m in PATH.finditer(json.dumps(body, default=str))}
    return kv3.dumps(keep, header) if keep else None, sorted(keep), sounds


def _owned(rs) -> dict | None:
    marker = rs.content_addon / MARKER
    if marker.is_file():
        return json.loads(marker.read_text(encoding='utf-8'))
    if rs.content_addon.exists() or rs.game_addon.exists():
        raise ReleaseError(f'addon {rs.addon!r} exists and was not made by a release: choose another addon name')
    return None


def prepare(project, name: str, addon: str | None = None) -> dict:
    """Copy the map and what it needs into the release addon; returns the plan for the build job."""
    s = project.s
    target = check_name(addon or name, 'release addon')
    if target == s.addon:
        raise ReleaseError('the release addon must differ from the project addon')
    if not s.map_path(name).is_file():
        raise ReleaseError(f'map {name!r} does not exist in addon {s.addon}')
    rs = dataclasses.replace(s, addon=target)
    old = _owned(rs)
    found = collect(s, name)
    vsndevts, events, sounds = _events(s, set(found['events']))
    for rel in sorted(sounds):
        src = _source(s.content_addon, rel)
        if src:
            found['files'].append(src)
    files = sorted(set(found['files']) | {f'maps/{name}.vmap'})
    addon_mod.ensure(rs)
    roots = (rs.content_addon, rs.game_addon)
    for rel in files:
        dst = ensure_inside(rs.content_addon / rel, *roots)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s.content_addon / rel, dst)
    for rel in found['compiled_only']:
        dst = ensure_inside(rs.game_addon / rel, *roots)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s.game_addon / rel, dst)
    if vsndevts:
        ev = ensure_inside(rs.content_addon / 'soundevents' / 'soundevents_addon.vsndevts', *roots)
        ev.parent.mkdir(parents=True, exist_ok=True)
        ev.write_text(vsndevts, encoding='utf-8')
        files.append('soundevents/soundevents_addon.vsndevts')
    # files of an earlier release of this addon that the map no longer needs (all generated by us)
    stale = sorted(set((old or {}).get('files', [])) - set(files))
    for rel in stale:
        p = rs.content_addon / rel
        if p.is_file():
            ensure_inside(p, rs.content_addon).unlink()
    manifest = {'map': name, 'from_addon': s.addon, 'files': files, 'compiled_only': found['compiled_only'],
                'events': events, 'made': time.strftime('%Y-%m-%d %H:%M:%S')}
    (rs.content_addon / MARKER).write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    return {'settings': rs, 'files': files, 'events': events, 'compiled_only': found['compiled_only']}


def start(project, name: str, addon: str | None = None, bots: bool | None = None, vis: bool | None = None):
    """Prepare the addon, make its radar, then one job (cs2map.releaserun): compile every copied resource, measure
    the nav on big maps, build the map (final), drop the light bake work files, report the upload size."""
    from . import navcost
    plan = prepare(project, name, addon)
    rs = plan['settings']
    rp = copy.copy(project)
    rp.s = rs
    notes = {}
    try:                                         # the radar is a bonus: it never fails a release
        from . import radar
        made = radar.write(rp, name)
        notes['radar'] = made.get('radar') or made.get('skipped')
    except Exception as e:                       # noqa: BLE001
        notes['radar'] = f'not made: {e}'
    build = project.build_plan(name, 'final', bots, vis, s=rs)
    for key in ('bots', 'vis', 'nav', 'memory'):
        if key in build:
            notes[key] = build[key]
    sources = [rs.content_addon / rel for rel in plan['files'] if Path(rel).suffix.lower() in COMPILE]
    compile_argv = [str(rs.resourcecompiler), '-threads', compiler._threads(rs), '-nop4', '-retail', '-f']
    for p in sources:
        compile_argv += ['-i', str(p)]
    nav_max = navcost.max_areas(rs)
    flags = build['flags']
    timeout = build['timeout'] + (900 if sources else 0)
    steps = {'compile': compile_argv if sources else None, 'nav_max': nav_max, 'game_addon': str(rs.game_addon),
             'build': compiler.build_argv(rs, name, flags, build['timeout'], nav_max),
             'build_without_bots': compiler.build_argv(rs, name, compiler.flags('final', False, '-vis' in flags),
                                                       build['timeout'])}
    # Bots asked for (or allowed) on a map whose nav may be big: count the areas first (world + NAVGEN, minutes)
    # before the light bake, so a too big nav costs minutes, not the bake plus hours of bot data.
    if '-nav' in flags and (build.get('nav') or {}).get('level') in ('slow', 'too_slow'):
        steps['probe'] = compiler.build_argv(rs, name, compiler.flags('nav'), 1800, nav_max, probe=True)
        timeout += 1800
    plan_file = rs.content_addon / 'cs2map_release_job.json'
    plan_file.write_text(json.dumps(steps, indent=1), encoding='utf-8')
    argv = [sys.executable, '-m', 'cs2map.releaserun', str(plan_file)]
    vpk = rs.compiled_map(name)
    before = vpk.stat().st_mtime if vpk.exists() else None
    guide = write_guide(project, rs, name, plan)

    def finish(job, text: str) -> dict:
        return _result(job, text)

    env = {'PYTHONUTF8': '1'}
    job = project.jobs.start('release', f'release {name} -> addon {rs.addon}', argv, rs.game_bin, timeout, finish,
                             env=env, meta={'output': str(vpk), 'vpk_mtime_before': before, 'guide': str(guide),
                                            'addon': rs.addon})
    return {'job': job.id, 'state': job.state, 'log': job.log, 'addon': rs.addon,
            'content': str(rs.content_addon), 'game': str(rs.game_addon), 'files': len(plan['files']),
            'sound_events': len(plan['events']), **notes, 'publish_guide': str(guide),
            'time_limit_minutes': round(timeout / 60),
            'hint': ('poll job_status(job, wait_seconds=50) until it is not running (progress shows the stage); then '
                     f'check the release in the game: game_screenshot(map="{rs.addon}/{name}") and '
                     f'script_test(map="{rs.addon}/{name}")')}


def _result(job, text: str) -> dict:
    """The release job's result: the build's, the decisions of the run ([release] lines), PUBLISH.md updated."""
    from . import releaserun
    info = compiler._build_result(job, text, Path(job.meta.get('output', '')), job.meta.get('vpk_mtime_before'))
    info['addon'] = job.meta.get('addon')
    info['publish_guide'] = job.meta.get('guide')
    for line in text.splitlines():
        if line.startswith(releaserun.MARK + ' bots '):
            info['bots'] = line[len(releaserun.MARK) + 6:]
        elif line.startswith(releaserun.MARK + ' upload size: '):
            info['upload'] = line[len(releaserun.MARK) + 14:]
    guide = Path(job.meta.get('guide') or '')
    if info.get('upload') and guide.is_file():
        body = guide.read_text(encoding='utf-8')
        body = re.sub(r'\n- (?:Upload size|Bots): .*', '', body)
        body = body.replace('the loading picture.\n', f'the loading picture.\n- Upload size: {info["upload"]}'
                            f'{_bots_line(info)}\n', 1)
        guide.write_text(body, encoding='utf-8')
    return info


def _bots_line(info: dict) -> str:
    bots = info.get('bots')
    return f'\n- Bots: {bots}' if bots else ''


def write_guide(project, rs, name: str, plan: dict) -> Path:
    text = f"""# Publish `{name}` to the Steam Workshop

The map and everything it needs are in their own addon **{rs.addon}** (made by `map_build("{name}", "release")`):

- source (open it in Hammer): `{rs.content_addon}`
- compiled (this is what gets uploaded): `{rs.game_addon}`
- {len(plan['files'])} files copied from the project addon, {len(plan['events'])} sound events, the radar and the loading picture.

## 1. Check it once more (your AI does this)

`game_screenshot(map="{rs.addon}/{name}")` loads the release itself (not the project copy); play it for a minute.
After changes in the project map, run `map_build("{name}", "release")` again: the addon is rebuilt from scratch.

Optional, for shiny surfaces (glass, metal, water): reflections. CS2 writes them only into the addon Workshop Tools
were started with, so start Workshop Tools with the addon **{rs.addon}** and ask your AI for
`lighting_build(name="{rs.addon}/{name}", cubemaps_only=true)`. Plaster and stone maps look right without it.

## 2. Upload with the Workshop Manager

1. CS2 Workshop Tools must be running (Steam → Counter-Strike 2 → **Launch CS2 Workshop Tools**, any addon).
2. In the **Asset Browser**: menu **Tools → Tools → Counter-Strike 2 Workshop Manager**.
3. **New** opens the new submission form:
   - **Title**: a short name, e.g. `{name.replace('_', ' ').title()}`.
   - **Description**: what the map is and how to play it (team, objective, the mechanics' keys: E to enter, R camera...).
   - **Preview image** (Browse): `{rs.content_addon / 'panorama' / 'images' / 'map_icons' / 'screenshots' / '1080p' / (name + '.png')}`
     (16:9; it is resized to 555×312; ask your AI for a better frame with `game_screenshot`).
   - **Addon folder**: choose **{rs.addon}** (not `{project.s.addon}`: that one holds every test map). Contents shows
     the files and the size that will be uploaded.
   - **Game mode tags**: Classic, Deathmatch, Wingman, Arms Race or Custom.
   - **Visibility**: Hidden (default) first, then Friends only to test with friends, then Public.
4. **Submit**. The first upload asks to accept the Steam Workshop agreement (once per account).

## 3. Play it from the Workshop

Open the map's Workshop page (Workshop Manager → View), subscribe; in CS2 it appears under the Workshop maps after
the download. A lobby or a server can start it with `host_workshop_map <published id>`.

## Updating

Change the map in the project, run `map_build("{name}", "release")` again, then in the Workshop Manager select the
map and press **Re-Upload** (Edit changes only the texts and the picture).
"""
    path = ensure_inside(rs.content_addon / 'PUBLISH.md', rs.content_addon)
    path.write_text(text, encoding='utf-8')
    return path


compiler.register_recovery('release', _result)
