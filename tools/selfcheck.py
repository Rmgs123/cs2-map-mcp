"""Quick check after a CS2 update: one command builds a small map with every mechanic, loads it in CS2, plays short
scenarios with real input and writes a report.

    python tools/selfcheck.py              # everything: files, compiler, game (about 10 minutes)
    python tools/selfcheck.py --no-game    # files and the compiler only (CS2 not needed)
    python tools/selfcheck.py car story    # only these scenarios (after the build)

CS2 Workshop Tools must be running with the project's addon, or startable by the server; do not use the game
window while the scenarios play. Everything goes through a real MCP session (the path AI clients use). The report
is logs/selfcheck-<time>.json with a contact sheet next to it; the summary compares with the previous report.
Map and scenarios: src/cs2map/data/selfcheck/.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'src' / 'cs2map' / 'data' / 'selfcheck'
MAP = 'mcp_selfcheck'
VIEWS = [{'name': 'sc_overview', 'pos': [180, 120, 420], 'ang': [28, 40, 0]},
         {'name': 'sc_mechanics', 'pos': [2300, 160, 300], 'ang': [22, 150, 0]}]


class Failed(Exception):
    pass


async def run(args: list[str]) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    no_game = '--no-game' in args
    only = [a for a in args if not a.startswith('--')]
    scenarios = json.loads((DATA / 'scenarios.json').read_text(encoding='utf-8'))
    unknown = sorted(set(only) - set(scenarios))
    if unknown:
        raise SystemExit(f'unknown scenarios {unknown}; known: {", ".join(scenarios)}')
    report = {'map': MAP, 'started': time.strftime('%Y-%m-%d %H:%M:%S'), 'steps': {}, 'scenarios': {}, 'frames': []}
    params = StdioServerParameters(command=sys.executable, args=['-m', 'cs2map.server'], cwd=str(ROOT),
                                   env={**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONUTF8': '1'})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async def call(tool: str, arguments: dict):
                res = await session.call_tool(tool, arguments)
                if res.isError:
                    raise Failed(f'{tool}: {res.content[0].text[:600]}')
                sc = res.structuredContent
                data = sc.get('result', sc) if isinstance(sc, dict) and set(sc) == {'result'} else sc
                if data is None and res.content:
                    data = json.loads(res.content[0].text)
                return data

            async def vrf_reads_own_model():
                """Source2Viewer-CLI must read a model compiled by this CS2 (23.10: VRF 20.0 failed on KV3 type 26)."""
                sys.path.insert(0, str(ROOT / 'src'))
                from cs2map.config import load_settings, vrf_cli
                models = sorted((load_settings().game_addon / 'models' / 'mcp').glob('*/*.vmdl_c'),
                                key=lambda f: f.stat().st_mtime)
                if not vrf_cli().is_file() or not models:
                    raise Failed('Source2Viewer-CLI or a compiled own model is missing (setup.ps1 -Apply)')
                proc = subprocess.run([str(vrf_cli()), '-i', str(models[-1]), '-b', 'DATA'], capture_output=True,
                                      text=True, timeout=120)
                if proc.returncode or 'Exception' in proc.stdout + proc.stderr:
                    raise Failed(f'Source2Viewer-CLI cannot read {models[-1].name}: update it (config/tool-sources.json)')

            async def step(name: str, coro):
                t = time.monotonic()
                print(f'{name} ...', flush=True)
                try:
                    value = await coro
                    report['steps'][name] = {'ok': True, 'seconds': round(time.monotonic() - t, 1)}
                    return value
                except Failed as exc:
                    report['steps'][name] = {'ok': False, 'seconds': round(time.monotonic() - t, 1), 'error': str(exc)}
                    raise

            try:
                if not only:
                    st = await step('status', call('status', {}))
                    cs2 = st.get('cs2', {}) if isinstance(st, dict) else {}
                    report['cs2'] = {k: cs2.get(k) for k in ('path', 'found', 'workshop_tools', 'running')}
                    report['blender'] = (st.get('blender') or {}).get('version') if isinstance(st, dict) else None
                    if not cs2.get('found') or not cs2.get('workshop_tools'):
                        raise Failed('CS2 with Workshop Tools not found (status)')
                    await step('assets', call('assets_prepare', {'kind': 'railgun'}))
                    await step('vrf', vrf_reads_own_model())
                    await step('map_create', call('map_create', {'name': MAP, 'template': 'empty', 'overwrite': True,
                                                                 'type': 'sandbox'}))
                    ops = json.loads((DATA / 'map.json').read_text(encoding='utf-8'))
                    await step('map_edit', call('map_edit', {'name': MAP, 'operations': ops}))
                    audit = await step('check_map', call('check_map', {'name': MAP}))
                    errors = [p for p in audit.get('problems', []) if p.get('severity') == 'error']
                    report['steps']['check_map']['errors'] = [f"{p.get('code')}: {p.get('message', '')[:120]}"
                                                              for p in errors[:8]]
                    if errors:
                        report['steps']['check_map']['ok'] = False
                    build = await step('map_build', call('map_build', {'name': MAP, 'preset': 'full'}))
                    deadline = time.monotonic() + 1800
                    while True:
                        js = await call('job_status', {'job': build['job'], 'wait_seconds': 50, 'log_lines': 5})
                        if js.get('state') != 'running' or time.monotonic() > deadline:
                            break
                    result = js.get('result') or {}
                    report['steps']['map_build'].update({'ok': bool(result.get('ok')), 'state': js.get('state'),
                                                         'errors': (result.get('errors') or [])[:5],
                                                         'missing_assets': (result.get('missing_assets') or [])[:5],
                                                         'nav_areas': result.get('nav_areas')})
                    if not result.get('ok'):
                        raise Failed(f"build {js.get('state')}: {(result.get('errors') or [''])[:2]}")
                if not no_game:
                    for name, steps in scenarios.items():
                        if only and name not in only:
                            continue
                        print(f'scenario {name} ...', flush=True)
                        await call('game_reset', {'map': MAP})
                        try:
                            r = await call('script_test', {'map': MAP, 'steps': steps, 'timeout': 90})
                        except Failed as exc:
                            r = {'ok': False, 'reason': str(exc)[:300]}
                        report['scenarios'][name] = {
                            'ok': bool(r.get('ok')), 'passed': r.get('passed'), 'checks': r.get('check_count'),
                            'reason': r.get('reason'), 'errors': r.get('errors', [])[:3], 'log': r.get('log'),
                            'failed': [c.get('expect') for c in r.get('checks', []) if not c.get('ok')][:4]}
                        log = r.get('log')
                        if log and Path(log).is_file():
                            details = json.loads(Path(log).read_text(encoding='utf-8'))
                            report['frames'] += [s['path'] for s in details.get('shots', [])
                                                 if not s.get('name', '').endswith('_test_start')]
                    await call('game_reset', {'map': MAP})
                    if not only:
                        shot = await step('frames', call('game_screenshot', {'map': MAP, 'views': VIEWS}))
                        report['frames'] += [v.get('path') for v in shot.get('views', shot.get('shots', []))
                                             if isinstance(v, dict) and v.get('path')]
            except Failed as exc:
                report['stopped'] = str(exc)
    return report


def finish(report: dict) -> int:
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    previous = sorted(logs.glob('selfcheck-*.json'))
    frames = [f for f in report['frames'] if f and Path(f).is_file()]
    if frames:
        sheet = logs / f'selfcheck-{stamp}.jpg'
        subprocess.run([sys.executable, str(ROOT / 'tools' / 'contact_sheet.py'), str(sheet), '--cols', '3',
                        '--width', '480', *frames], cwd=ROOT, capture_output=True, timeout=120)
        if sheet.is_file():
            report['sheet'] = str(sheet)
    items = {**{f'step {k}': v['ok'] for k, v in report['steps'].items()},
             **{f'scenario {k}': v['ok'] for k, v in report['scenarios'].items()}}
    report['ok'] = bool(items) and all(items.values()) and 'stopped' not in report
    if previous:
        old = json.loads(previous[-1].read_text(encoding='utf-8'))
        before = {**{f'step {k}': v['ok'] for k, v in old.get('steps', {}).items()},
                  **{f'scenario {k}': v['ok'] for k, v in old.get('scenarios', {}).items()}}
        report['changed_since'] = {'report': previous[-1].name,
                                   'items': {k: [before.get(k), v] for k, v in items.items() if before.get(k) != v}}
    path = logs / f'selfcheck-{stamp}.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print()
    for k, v in items.items():
        extra = ''
        if k.startswith('scenario '):
            s = report['scenarios'][k[9:]]
            extra = f"  {s.get('passed')}/{s.get('checks')}" + (f"  {s['failed']}" if s.get('failed') else '') + \
                    (f"  {s['reason'][:120]}" if s.get('reason') else '')
        elif report['steps'][k[5:]].get('error'):
            extra = '  ' + report['steps'][k[5:]]['error'][:160]
        print(f"{'OK  ' if v else 'FAIL'} {k}{extra}")
    if report.get('stopped'):
        print('stopped:', report['stopped'][:300])
    if report.get('changed_since', {}).get('items'):
        print('changed since', report['changed_since']['report'], report['changed_since']['items'])
    print('report:', path, '| sheet:', report.get('sheet', '-'))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(finish(asyncio.run(run(sys.argv[1:]))))
