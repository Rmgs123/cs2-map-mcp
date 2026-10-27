"""End-to-end: start the MCP server over stdio with the official SDK client,
create a map, edit it, compile it with resourcecompiler.  Needs CS2 installed."""
import asyncio
import json
import os
import sys
import unittest

from tests.helpers import HAVE_COMPILER, ROOT, agent_suffix

MAP = 'test_mcp_e2e' + agent_suffix()


async def _session_run(steps):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable, args=['-m', 'cs2map.server'], cwd=str(ROOT),
                                   env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            return await steps(session, init)


def _data(result):
    if result.isError:
        raise AssertionError(result.content[0].text)
    if result.structuredContent is not None:
        sc = result.structuredContent
        return sc.get('result', sc) if isinstance(sc, dict) and set(sc) == {'result'} else sc
    return json.loads(result.content[0].text)


@unittest.skipUnless(HAVE_COMPILER, 'CS2 install not found or CS2MAP_OFFLINE set')
class ServerEndToEnd(unittest.TestCase):
    def test_create_edit_build(self):
        async def steps(session, init):
            self.assertIn('cs2map://guide', init.instructions)
            tools = {t.name for t in (await session.list_tools()).tools}
            for name in ('status', 'map_create', 'map_edit', 'map_build', 'job_status', 'asset_search', 'entity_info',
                         # tools from several extensions: models, materials, quality, scripting, kits
                         'model_build', 'material_from_reference', 'check_map', 'playtest', 'script_write',
                         'kit_info'):
                self.assertIn(name, tools)
            guide = await session.read_resource('cs2map://guide')
            self.assertIn('map_edit operations', guide.contents[0].text)
            self.assertFalse(any(name.startswith('character_') for name in tools))
            created = _data(await session.call_tool('map_create', {'name': MAP, 'overwrite': True}))
            self.assertEqual(created['map'], MAP)
            ops = [
                {'op': 'add_mesh', 'shape': {'shape': 'room', 'mins': [-512, -384, 0], 'maxs': [512, 384, 256],
                                             'openings': [{'wall': '+x', 'center': 0, 'width': 128, 'height': 128}]},
                 'material': 'materials/dev/reflectivity_30.vmat', 'ref': 'room'},
                {'op': 'add_entity', 'classname': 'info_player_counterterrorist', 'origin': [-400, 0, 8],
                 'angles': [0, 0, 0]},
                {'op': 'add_entity', 'classname': 'info_player_terrorist', 'origin': [400, 0, 8],
                 'angles': [0, 180, 0]},
                {'op': 'add_brush_entity', 'classname': 'func_buyzone', 'keyvalues': {'TeamNum': 3},
                 'shape': {'shape': 'box', 'mins': [-480, -128, 0], 'maxs': [-320, 128, 128]}},
                {'op': 'add_entity', 'classname': 'light_omni2', 'origin': [0, 0, 200],
                 'keyvalues': {'color': '255 220 180', 'range': 600, 'targetname': 'lamp'}, 'ref': 'lamp'},
                {'op': 'add_entity', 'classname': 'logic_auto', 'origin': [0, 0, 32], 'ref': 'auto'},
                {'op': 'connect', 'from': '@auto', 'output': 'OnMapSpawn', 'target': 'lamp', 'input': 'TurnOn'},
            ]
            edited = _data(await session.call_tool('map_edit', {'name': MAP, 'operations': ops}))
            self.assertEqual(edited['applied'], len(ops))
            self.assertIn('room', edited['refs'])
            bad = await session.call_tool('map_edit', {'name': MAP, 'operations': [
                {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [0, 0, 0], 'maxs': [0, 1, 1]}}]})
            self.assertTrue(bad.isError)
            info = _data(await session.call_tool('map_info', {'name': MAP, 'classname': 'light_omni2', 'full': True}))
            self.assertEqual(info['total_matching'], 1)
            self.assertEqual(info['nodes'][0]['keyvalues']['color'], '255 220 180')
            job = _data(await session.call_tool('map_build', {'name': MAP, 'preset': 'fast'}))['job']
            for _ in range(40):
                st = _data(await session.call_tool('job_status', {'job': job, 'wait_seconds': 15}))
                if st['state'] != 'running':
                    break
            self.assertEqual(st['state'], 'succeeded', st)
            self.assertTrue(st['result']['ok'], st['result'])
            return st

        st = asyncio.run(_session_run(steps))
        print('\nbuild result:', json.dumps(st['result'], indent=1)[:800])


if __name__ == '__main__':
    unittest.main()
