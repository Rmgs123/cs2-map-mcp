import tempfile
import unittest
from pathlib import Path

from PIL import Image

from cs2map import checks, lighting
from cs2map.project import Project
from tests.helpers import HAVE_CS2


class QualityPure(unittest.TestCase):
    def test_lighting_measure_reports_clipping(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'frame.png'
            image = Image.new('RGB', (32, 32), (255, 255, 255))
            image.paste((0, 0, 0), (0, 0, 4, 4))
            image.save(path)
            result = lighting.lighting_measure(path)
            self.assertGreater(result['clipped_high'], 0.8)
            self.assertGreater(result['clipped_low'], 0.0)

    def test_lighting_measure_handles_large_images_without_getdata_warning(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'frame.png'
            Image.new('RGB', (1400, 900), (80, 90, 100)).save(path)
            result = lighting.lighting_measure(path)
            self.assertAlmostEqual(result['mean'], (80 + 90 + 100) / 3 / 255, places=3)


@unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
class QualityMap(unittest.TestCase):
    def test_existing_map_returns_contract(self):
        result = checks.check_map(Project(), 'mcp_showcase')
        self.assertIn('ok', result)
        self.assertIsInstance(result['problems'], list)
        if result['problems']:
            self.assertTrue({'severity', 'code', 'id', 'message', 'fix'} <= set(result['problems'][0]))

    def test_quality_fixture_is_clean_and_broken_fixture_is_actionable(self):
        project = Project()
        good = checks.check_map(project, 'c_quality')
        bad = checks.check_map(project, 'c_broken')
        self.assertTrue(good['ok'], good['problems'])
        self.assertFalse(bad['ok'])
        codes = {p['code'] for p in bad['problems']}
        self.assertTrue({'unknown_key','missing_asset','io_target_missing','spawn_inside_geometry',
                         'spawn_clearance','spawn_too_close','nav_seed_missing'} <= codes)

    def test_owned_model_spawn_overlap_is_reported_as_warning(self):
        result = checks.check_map(Project(), 'mcp_accept')
        self.assertNotIn('prop_over_spawn', {p['code'] for p in result['problems']})

    def test_registered_area_checks_run_and_failures_are_reported(self):
        def area_check(project, m):
            return [checks.problem('error', 'area_test', None, 'area check ran', 'nothing')]

        def broken_check(project, m):
            raise RuntimeError('boom')
        checks.register_check('t_area', area_check)
        checks.register_check('t_broken', broken_check)
        try:
            result = checks.check_map(Project(), 'mcp_showcase')
        finally:
            checks._EXTRA_CHECKS.pop('t_area', None)
            checks._EXTRA_CHECKS.pop('t_broken', None)
        codes = {p['code'] for p in result['problems']}
        self.assertIn('area_test', codes)
        self.assertIn('check_failed', codes)
        self.assertFalse(result['ok'])

