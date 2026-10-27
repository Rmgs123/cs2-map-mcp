"""The steps of map_build(name, "release") as one background job:

    python -m cs2map.releaserun <plan.json>

1. compile every copied resource of the release addon;
2. (big maps) measure the nav: the map compiler with -nav and no light, stopped right after NAVGEN by the nav guard;
   over the limit the release goes on WITHOUT bots instead of spending hours on bot data after the light bake;
3. build the map (final light, VIS unless the map is open, nav when it fits), under the guard;
4. drop the light bake's work files (_vrad3, hundreds of MB, made again by every bake) from the uploaded folder and
   print the upload size.

Lines starting with "[release]" carry the decisions; the job's result reads them.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

MARK = '[release]'


def _run(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    seen = []
    for raw in proc.stdout:
        line = raw.decode('utf-8', 'replace')
        seen.append(line)
        sys.stdout.write(line)
        sys.stdout.flush()
    return proc.wait(), ''.join(seen)


def folder_size(path: Path) -> tuple[int, int]:
    total = files = 0
    for p in path.rglob('*'):
        if p.is_file():
            total += p.stat().st_size
            files += 1
    return total, files


def drop_bake_files(game_addon: Path) -> int:
    """Remove <game addon>/_vrad3 (VRAD3 work files; every bake makes them again). Returns the bytes freed."""
    work = game_addon / '_vrad3'
    if not work.is_dir() or work.parent.resolve() != game_addon.resolve():
        return 0
    size = folder_size(work)[0]
    shutil.rmtree(work, ignore_errors=True)
    return size


def main(plan_file: str) -> int:
    from . import navcost
    plan = json.loads(Path(plan_file).read_text(encoding='utf-8'))
    steps = [s for s in ('compile', 'probe', 'build') if plan.get(s)]
    n = len(steps)
    final = plan['build']
    for i, step in enumerate(steps, 1):
        title = {'compile': 'compile the copied resources',
                 'probe': 'measure the nav (the compiler stops right after NAVGEN)',
                 'build': 'build the map'}[step]
        argv = final if step == 'build' else plan[step]
        print(f'== step {i}/{n}: {title}', flush=True)
        code, text = _run(argv)
        if step == 'probe':
            areas = navcost.from_log(text)
            if code != 0 or areas is None:
                reason = ('no walkable seeds' if navcost.NO_SEEDS in text else
                          f'the measurement failed (exit {code})')
                print(f'{MARK} bots off: {reason}', flush=True)
                final = plan['build_without_bots']
            elif areas > plan['nav_max']:
                print(f'{MARK} bots off: {navcost.verdict(areas, plan["nav_max"])["message"]}. '
                      f'{navcost.LIMIT_FIX}', flush=True)
                final = plan['build_without_bots']
            else:
                print(f'{MARK} bots on: {navcost.verdict(areas, plan["nav_max"])["message"]}', flush=True)
            continue
        if code:
            print(f'== step {i} failed with exit code {code}', flush=True)
            return code
    game = Path(plan['game_addon'])
    freed = drop_bake_files(game)
    if freed:
        print(f'{MARK} removed the light bake work files _vrad3 ({freed / 2**20:.0f} MB) from the upload folder',
              flush=True)
    size, files = folder_size(game)
    print(f'{MARK} upload size: {size / 2**20:.0f} MB in {files} files ({game})', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
