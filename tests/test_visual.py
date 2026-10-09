import unittest
from unittest import mock

from cs2map import visual
from cs2map import checks
from cs2map.ext import visual as visual_ext
from cs2map.project import Project
from tests.helpers import HAVE_CS2


class VisualPure(unittest.TestCase):
    def test_extension_registers_tool_and_check(self):
        class MCP:
            def tool(self):
                return lambda fn: fn

        before = checks._EXTRA_CHECKS.get('visual')
        visual_ext.register(MCP(), mock.Mock())
        try:
            self.assertIs(checks._EXTRA_CHECKS.get('visual'), visual.visual_check_loaded)
        finally:
            if before is None:
                checks._EXTRA_CHECKS.pop('visual', None)
            else:
                checks._EXTRA_CHECKS['visual'] = before

    def test_resourceinfo_mdat_bounds(self):
        text = '''{ m_sceneObjects = [ { m_vMinBounds = [ -2, -3, 0 ] m_vMaxBounds = [ 4, 5, 8 ] } ] }'''
        points = visual._resourceinfo_bounds(text, 'MDAT')
        self.assertEqual(points, [(-2.0, -3.0, 0.0), (4.0, 5.0, 8.0)])

    def test_resourceinfo_data_zero_bounds_are_available_to_caller(self):
        text = '''{ m_modelInfo = { m_vHullMin = [ 0, 0, 0 ] m_vHullMax = [ 0, 0, 0 ] m_vViewMin = [ -1, -2, -3 ] m_vViewMax = [ 1, 2, 3 ] } }'''
        self.assertEqual(visual._resourceinfo_bounds(text, 'DATA')[-2:], [(-1.0, -2.0, -3.0), (1.0, 2.0, 3.0)])

    def test_transformed_bounds_apply_rotation_and_scale(self):
        result = visual._transformed(((-1, -2, 0), (1, 2, 4)), (10, 20, 30), (0, 90, 0), (2, 1, .5))
        self.assertEqual(result, ((8.0, 18.0, 30.0), (12.0, 22.0, 32.0)))

    @unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
    def test_world_bounds_compatibility_helper(self):
        project = Project()
        result = visual._model_bounds(project, 'models/props/de_nuke/crate_small.vmdl', [10, 20, 30], [0, 0, 0], [1, 1, 1])
        self.assertAlmostEqual(result[0][0], -23.15151, places=3)
        self.assertAlmostEqual(result[1][2], 95.345746, places=3)

    def test_unknown_model_is_skipped_without_guessing(self):
        project = mock.Mock()
        project._visual_model_bounds = {}
        project.s.game_addon = mock.Mock()
        project.s.game_addon.__truediv__ = mock.Mock(return_value=mock.Mock(is_file=lambda: False))
        project.game.exists.return_value = False
        self.assertIsNone(visual.model_bounds(project, 'models/unknown/not_installed.vmdl'))


@unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
class VisualMap(unittest.TestCase):
    def test_original_f2_has_actionable_visual_findings(self):
        # q_f2_orig keeps the round-4 f2_port; f2_port itself was fixed from these findings.
        result = visual.visual_check(Project(), 'q_f2_orig')
        codes = {item['code'] for item in result['problems']}
        self.assertTrue({'visual_floating_prop', 'visual_floating_platform'} <= codes, result)
        fixed = {item['code'] for item in visual.visual_check(Project(), 'f2_port')['problems']}
        self.assertFalse({'visual_floating_prop', 'visual_floating_platform'} & fixed)

    def test_accepted_story_and_dressed_maps_have_no_visual_warnings(self):
        project = Project()
        # mcp_story (round 3) keeps coplanar duplicates found by the z-fighting check (04.10): sign 0.08 in
        # front of its plate, floor and wall slabs; everything else stays clean.
        story = visual.visual_check(project, 'mcp_story')['problems']
        self.assertEqual({x['code'] for x in story}, {'visual_z_fighting'})
        dressed = visual.visual_check(project, 'v_warehouse')['problems']
        self.assertEqual({x['code'] for x in dressed} - {'visual_z_fighting'}, set())
