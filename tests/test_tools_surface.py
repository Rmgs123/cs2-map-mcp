"""The public tool surface (surface.py): size, style and compact schemas — what every AI client reads first."""
import asyncio
import json
import unittest

from cs2map import server, surface


def tools():
    return asyncio.run(server.mcp.list_tools())


class Surface(unittest.TestCase):
    def test_count_and_token_budget(self):
        ts = tools()
        self.assertLessEqual(len(ts), 60, 'merge tools into parameters/operations instead of adding new ones')
        chars = sum(len(json.dumps({'name': t.name, 'description': t.description, 'inputSchema': t.inputSchema},
                                   ensure_ascii=False, separators=(',', ':'))) for t in ts)
        self.assertLessEqual(chars // 4, 7000, f'tool descriptions+schemas ~{chars // 4} tokens')

    def test_descriptions_one_style(self):
        for t in tools():
            with self.subTest(tool=t.name):
                d = t.description or ''
                self.assertTrue(40 <= len(d) <= 1000, len(d))
                self.assertTrue(d[0].isupper(), 'starts with what it does')
                self.assertNotIn('  ', d.replace('\n', ' ').strip().replace('  ', ' ', 0)[:0] + '')

    def test_compact_schemas(self):
        for t in tools():
            s = json.dumps(t.inputSchema)
            with self.subTest(tool=t.name):
                self.assertNotIn('"title"', s)
                self.assertNotIn('{"type": "null"}', s)

    def test_every_published_tool_has_a_raw_function_and_old_names_are_gone(self):
        names = {t.name for t in tools()}
        for old in ('map_restore', 'job_cancel', 'hammer_open', 'building_views', 'layout_views', 'mode_views',
                    'playtest_views', 'kit_list', 'kit_gallery', 'prop_search', 'sound_search', 'fx_search',
                    'model_import', 'model_list', 'sound_import', 'sound_generate', 'playtest_run', 'comp_playtest',
                    'terrain_info', 'layout_info', 'weapon_prepare', 'control_build_assets', 'group_nodes'):
            self.assertNotIn(old, names)
        self.assertIn('status', surface.RAW)

    def test_compact_schema_keeps_optional_types(self):
        s = surface.compact_schema({'title': 'X', 'properties': {'a': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                                                                        'default': None, 'title': 'A'}}})
        self.assertEqual(s, {'properties': {'a': {'type': 'string'}}})


if __name__ == '__main__':
    unittest.main()
