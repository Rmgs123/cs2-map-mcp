"""Call cs2map tools through a fresh MCP server over stdio (the same path an AI client uses).

    python tools/mcp_call.py <tool> '<json args>' [<tool> '<json args>' ...]
    python tools/mcp_call.py <tool> @args.json
    python tools/mcp_call.py --list
    python tools/mcp_call.py --ascii <tool> ...   (escape non-ASCII: for consoles that are not UTF-8,
                                                    e.g. Windows PowerShell 5.1 shows Cyrillic as U+FFFD)

Each run starts a new server, so it always runs the current code.  Results are printed as JSON.
A map_edit with more than 2000 operations is sent in parts of 2000 (each part atomic; "ref" names carry over).
A tool that starts a background job (map_build, ...) is followed until the job ends (progress lines every 50 s):
the SDK client kills the server's process tree when this script exits, the job with it. --no-wait skips that.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


ASCII = False
WAIT_JOBS = True
MAP_EDIT_MAX = 2000


async def main(argv: list[str]) -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable, args=['-m', 'cs2map.server'], cwd=str(ROOT),
                                   env={**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONUTF8': '1'})
    code = 0
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if argv[:1] == ['--list']:
                for t in (await session.list_tools()).tools:
                    print(t.name)
                return 0
            pairs = list(zip(argv[0::2], argv[1::2]))
            last_job = None
            for tool, args in pairs:
                if args.startswith('@'):                      # @file.json: arguments from a file
                    args = Path(args[1:]).read_text(encoding='utf-8')
                if last_job:                                  # "$job" = job id from the previous result
                    args = args.replace('$job', last_job)
                call = json.loads(args or '{}')
                if tool == 'map_edit' and len(call.get('operations') or []) > MAP_EDIT_MAX:
                    # map_edit takes 2000 operations per call: send the batch in parts, in order ("ref"s carry
                    # over); each part is atomic on its own. A dry run cannot see refs of the earlier parts.
                    ops = call['operations']
                    parts = [ops[i:i + MAP_EDIT_MAX] for i in range(0, len(ops), MAP_EDIT_MAX)]
                    print(json.dumps({'tool': tool, 'split': f'{len(ops)} operations in {len(parts)} calls'}),
                          flush=True)
                    for k, part in enumerate(parts[:-1], 1):
                        r = await session.call_tool(tool, {**call, 'operations': part})
                        if r.isError:
                            print(json.dumps({'tool': tool, 'part': k, 'error': r.content[0].text}, ensure_ascii=ASCII,
                                             indent=1))
                            return 1
                        print(json.dumps({'tool': tool, 'part': k, 'ok': True}), flush=True)
                    call = {**call, 'operations': parts[-1]}
                res = await session.call_tool(tool, call)
                if res.isError:
                    code = 1
                    print(json.dumps({'tool': tool, 'error': res.content[0].text}, ensure_ascii=ASCII, indent=1))
                    break
                data = _data(res)
                print(json.dumps({'tool': tool, 'result': data}, ensure_ascii=ASCII, indent=1), flush=True)
                if isinstance(data, dict) and isinstance(data.get('job'), str):
                    last_job = data['job']
                    if data.get('state') == 'running' and WAIT_JOBS:
                        # The SDK client runs the server in a Windows job object that is killed with it (no
                        # breakaway): a build started here dies when this script ends. Wait for it.
                        await _wait(session, last_job)
    return code


def _data(res):
    sc = res.structuredContent
    data = sc.get('result', sc) if isinstance(sc, dict) and set(sc) == {'result'} else sc
    if data is None and res.content:
        try:
            data = json.loads(res.content[0].text)
        except ValueError:
            data = res.content[0].text
    return data


async def _wait(session, job: str) -> None:
    while True:
        res = await session.call_tool('job_status', {'job': job, 'wait_seconds': 50, 'log_lines': 0})
        data = _data(res)
        if res.isError or not isinstance(data, dict) or data.get('state') != 'running':
            print(json.dumps({'tool': 'job_status', 'result': data}, ensure_ascii=ASCII, indent=1), flush=True)
            return
        print(json.dumps({'job': job, 'progress': data.get('progress')}, ensure_ascii=ASCII), flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    args = sys.argv[1:]
    if args[:1] == ['--ascii']:
        ASCII, args = True, args[1:]
    if args[:1] == ['--no-wait']:
        WAIT_JOBS, args = False, args[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(args)))
