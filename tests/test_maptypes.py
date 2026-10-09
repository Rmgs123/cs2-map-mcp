"""Map types pick checks and end tests; they never limit what can be built."""
import unittest

from cs2map import checks, maptypes
from cs2map.project import Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class MapTypes(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(SETTINGS.cs2_path, 'basic')
        self.p.load = lambda name: self.m

    def codes(self):
        r = checks.check_map(self.p, 'x')
        return r, {q['code']: q['severity'] for q in r['problems']}

    def test_untyped_maps_keep_competitive_rules(self):
        r, codes = self.codes()
        self.assertEqual(r['type'], 'competitive')
        self.assertEqual(codes.get('buyzone_missing'), 'error')

    def test_sandbox_and_custom_skip_competitive_rules(self):
        for kind in ('sandbox', 'custom'):
            with self.subTest(kind=kind):
                maptypes.store(self.m, kind)
                r, codes = self.codes()
                self.assertEqual(r['type'], kind)
                self.assertNotIn('buyzone_missing', codes)
                self.assertNotIn('bombsite_missing', codes)
                self.assertEqual(codes.get('nav_seed_missing'), 'warning')
                self.assertIn('visual_check', r['tests_at_the_end'])

    def test_set_map_type_operation_and_bad_type(self):
        out = maptypes.set_type_op(self.p, self.m, {'op': 'set_map_type', 'type': 'hideseek'}, {})
        self.assertEqual(out['type'], 'hideseek')
        self.assertEqual(maptypes.stored(self.m), 'hideseek')
        with self.assertRaisesRegex(ValueError, 'must be one of'):
            maptypes.set_type_op(self.p, self.m, {'op': 'set_map_type', 'type': 'arena'}, {})
