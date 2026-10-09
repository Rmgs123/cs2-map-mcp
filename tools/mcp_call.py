"""Call cs2map tools through a fresh MCP server over stdio (the same path an AI client uses).

    python tools/mcp_call.py <tool> '<json args>' [<tool> '<json args>' ...]
    python tools/mcp_call.py <tool> @args.json
    python tools/mcp_call.py --list
    python tools/mcp_call.py --ascii <tool> ...   (escape non-ASCII: for consoles that are not UTF-8,
                                                    e.g. Windows PowerShell 5.1 shows Cyrillic as U+FFFD)

Each run starts a new server, so it always runs the current code.  Results are printed as JSON.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


ASCII = False


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
                res = await session.call_tool(tool, json.loads(args or '{}'))
                if res.isError:
                    code = 1
                    print(json.dumps({'tool': tool, 'error': res.content[0].text}, ensure_ascii=ASCII, indent=1))
                    break
                sc = res.structuredContent
                data = sc.get('result', sc) if isinstance(sc, dict) and set(sc) == {'result'} else sc
                if data is None and res.content:
                    try:
                        data = json.loads(res.content[0].text)
                    except ValueError:
                        data = res.content[0].text
                print(json.dumps({'tool': tool, 'result': data}, ensure_ascii=ASCII, indent=1), flush=True)
                if isinstance(data, dict) and isinstance(data.get('job'), str):
                    last_job = data['job']
    return code


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    args = sys.argv[1:]
    if args[:1] == ['--ascii']:
        ASCII, args = True, args[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(args)))
