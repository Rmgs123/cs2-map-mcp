"""Integrator fixes found while accepting phase 7 (round 7)."""
import unittest

from cs2map import compiler, lockrun


class LightmapRetry(unittest.TestCase):
    def test_retry_doubles_the_atlas_until_2048(self):
        argv = ['rc.exe', '-bakelighting', '-lightmapMaxResolution', '512', '-retail']
        self.assertEqual(lockrun._lightmap_res(argv), 512)
        bigger = lockrun._with_lightmap_res(argv, 1024)
        self.assertEqual(bigger[3], '1024')
        self.assertEqual(argv[3], '512')
        self.assertIsNone(lockrun._lightmap_res(['rc.exe', '-nolightmaps']))

    def test_log_keeps_only_the_last_attempt(self):
        first = 'Packing 104969 UV charts onto atlas...\nToo many charts for the lightmap: use a higher resolution\n'
        ok = lockrun.RETRY_MARK + ' lightmap atlas too small\n 573 compiled, 0 failed, 0 skipped\n'
        self.assertEqual(compiler.analyze_log(first + ok)['errors'], [])
        failed = compiler.analyze_log(first)
        self.assertTrue(failed['errors'])
        self.assertTrue(failed.get('hints'))


class StreetOpenings(unittest.TestCase):
    def test_shared_opening_is_kept_clear_on_both_sides(self):
        from cs2map import streetlife
        room = lambda rid, lo, hi: {'id': rid, 'interior': {'mins': lo, 'maxs': hi}, 'floor_z': 0}
        a, b = room('a', [0, 0, 0], [640, 640, 320]), room('b', [656, 0, 0], [1296, 640, 320])
        plan = {'name': 'q', 'openings': [{'between': ['a', 'b'], 'wall': '+x',
                                           'bounds': {'mins': [640, 256, 0], 'maxs': [656, 384, 224]}}]}
        boxes = streetlife._opening_clear(None, [(plan, 'a', a), (plan, 'b', b)], 112)
        self.assertEqual(sorted((lo[0], hi[0]) for lo, hi in boxes), [(528, 656), (640, 768)])


class FlatModels(unittest.TestCase):
    def test_thin_model_flat_on_a_surface_is_reported(self):
        from cs2map import checks, mesh, visual
        from cs2map.vmap import VMap
        from tests.helpers import HAVE_CS2, SETTINGS
        if not HAVE_CS2:
            self.skipTest('CS2 fixture needed')
        m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        wall = m.attach(m.new_mesh(mesh.box((-16, 0, 0), (0, 128, 128))))     # face x=0 looks to +x
        visible = [(wall, checks._mesh_bounds(wall), list(checks._triangles(wall)))]
        pane = lambda x0: {'id': 1, 'model': 'pane.vmdl', 'bounds': ((x0, 40, 20), (x0 + 3.8, 84, 100))}
        self.assertEqual(len(visual._flat_model_checks([pane(0.0)], visible)), 1)
        self.assertEqual(visual._flat_model_checks([pane(1.0)], visible), [])            # 1 unit off: fine
        thick = {'id': 2, 'model': 'frame.vmdl', 'bounds': ((0.0, 40, 20), (13.0, 84, 100))}
        self.assertEqual(visual._flat_model_checks([thick], visible), [])                # a frame's back is hidden
