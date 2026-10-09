"""The self-check after a CS2 update (tools/selfcheck.py): its map batch and scenarios stay valid without the game."""
import json
import unittest

from tests.helpers import HAVE_CS2, ROOT, agent_suffix

DATA = ROOT / 'src' / 'cs2map' / 'data' / 'selfcheck'
MAP = 'test_selfcheck' + agent_suffix()


class SelfCheck(unittest.TestCase):
    def test_scenarios_are_valid_script_test_steps(self):
        from cs2map import scripting
        scenarios = json.loads((DATA / 'scenarios.json').read_text(encoding='utf-8'))
        self.assertGreaterEqual(set(scenarios), {'drone', 'car', 'turret', 'gallery', 'weapon', 'ability', 'story'})
        for name, steps in scenarios.items():
            with self.subTest(scenario=name):
                scripting._test_steps(steps)
                self.assertTrue(any('expect' in s for s in steps), 'a scenario must expect at least one log line')

    @unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
    def test_map_batch_passes_dry_run(self):
        from cs2map import server
        from cs2map.surface import RAW
        project = server.project
        if not (project.s.game_addon / 'models/mcp_w/railgun/railgun.vmdl_c').exists():
            RAW['assets_prepare']('railgun')
        project.map_create(MAP, 'empty', overwrite=True, type='sandbox')
        ops = json.loads((DATA / 'map.json').read_text(encoding='utf-8'))
        project.map_edit(MAP, ops, dry_run=True)


if __name__ == '__main__':
    unittest.main()
