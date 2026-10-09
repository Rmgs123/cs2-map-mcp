"""add_district: a cheap town on a grid — closed light meshes, fade distance, own materials, budgets."""
import unittest

from cs2map import districts, meshops as g
from cs2map.project import OpError, Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class District(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(SETTINGS.cs2_path, 'basic')

    def run_op(self, **op):
        return districts.add_district(self.p, self.m, {'op': 'add_district', 'name': 't', **op}, {})

    def test_town_blocks_houses_and_fade(self):
        r = self.run_op(area={'mins': [0, 0], 'maxs': [2048, 2048]}, fade=5000, collision=False, seed=2)
        self.assertEqual(r['blocks'], 4)
        self.assertGreater(r['houses'], 8)
        meshes = [n for n in self.m.iter_nodes(self.m.node(r['id'])) if n.type == 'CMapMesh']
        self.assertEqual(len(meshes), r['meshes'])
        for n in meshes:
            self.assertEqual(n['fademaxdist'], 5000)
            self.assertEqual(n['physicsType'], 'none')
            self.assertNotIn(-1, n['meshData']['edgeFaceIndices'])             # every piece is closed
        windows = [n for n in meshes if 'materials/glass/window_opaque_1.vmat' in n['meshData']['materials']]
        self.assertTrue(windows)
        poly = next(p for p in g.decode_node(windows[0]) if p.material.endswith('window_opaque_1.vmat'))
        us = sorted(round(c.uv[0], 3) for c in poly.corners)
        self.assertEqual((us[0], us[-1]), (0.0, 1.0))                         # the image once per window

    def test_refusals(self):
        for op, msg in (({'area': {'mins': [0, 0], 'maxs': [100, 100]}}, 'at least 512'),
                        ({'area': {'mins': [0, 0], 'maxs': [2048, 2048]}, 'roofs': ['dome']}, 'roofs'),
                        ({'area': {'mins': [0, 0], 'maxs': [2048, 2048]}, 'typo': 1}, 'unknown'),
                        ({'area': {'mins': [-30000, -30000], 'maxs': [30000, 30000]}, 'house_width': 128,
                          'block': 256, 'street': 64}, 'more than')):
            with self.subTest(msg=msg), self.assertRaisesRegex(OpError, msg):
                self.run_op(**op)


if __name__ == '__main__':
    unittest.main()
