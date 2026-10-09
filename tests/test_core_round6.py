"""Integrator changes of round 6: mode-aware check_map, equip item keys."""
import unittest

from cs2map import checks
from cs2map.project import Project
from tests.helpers import HAVE_CS2


@unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
class ModeAwareCheckMap(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def check(self, name):
        if not self.p.s.map_path(name).is_file():
            self.skipTest(f'acceptance map {name} not present')
        return checks.check_map(self.p, name)

    def test_deathmatch_skips_only_defuse_rules(self):
        r = self.check('c2_dm')
        self.assertEqual(r['mode'], 'deathmatch')
        self.assertTrue(r['ok'])
        self.assertIn('buyzone_missing', r['inapplicable_core_checks'])
        self.assertFalse({x['code'] for x in r['problems']} & checks.DEFUSE_ONLY)

    def test_surf_course_skips_movement_rules_and_aim_equip_keys_are_known(self):
        surf = self.check('c2_surf')
        self.assertEqual(surf['mode'], 'surf')
        self.assertIn('steep_ramp', surf['inapplicable_core_checks'])   # visual codes join when extensions load
        aim = self.check('c2_aim')
        self.assertNotIn('unknown_key', {x['code'] for x in aim['problems']})

    def test_defuse_maps_keep_every_rule(self):
        r = self.check('c2_defuse')
        self.assertNotIn('inapplicable_core_checks', r)


@unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
class PersistentRefs(unittest.TestCase):
    def test_ref_from_an_earlier_call_resolves_and_must_be_unique(self):
        from cs2map.vmap import VMap
        from tests.helpers import SETTINGS
        p = Project()
        m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        refs = {}
        op = {'op': 'add_entity', 'classname': 'info_target', 'origin': [0, 0, 0], 'ref': 'marker'}
        result = p._apply(m, op, refs)
        p._keep_ref(m, op, result)
        later = p._resolve(m, '@marker', {})              # a new call: empty refs
        self.assertEqual(later['nodeID'], result['id'])
        again = p._apply(m, dict(op), {})
        with self.assertRaises(Exception):
            p._keep_ref(m, op, again)


class EngineNoise(unittest.TestCase):
    def test_stock_agent_animgraph_lines_are_not_map_errors(self):
        from cs2map import playtest
        errors, engine = playtest._split_engine(['Failed to set animgraph param e_body_idle of type unknown',
                                                 'Graph Warning: animation/graphs/agent.vanmgrph',
                                                 'Error: entity references missing model'])
        self.assertEqual(errors, ['Error: entity references missing model'])
        self.assertEqual(len(engine), 2)


if __name__ == '__main__':
    unittest.main()
