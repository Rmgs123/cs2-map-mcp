"""add_junction: one plaza where streets at different heights meet - the streets give way inside the outline, every
mouth meets its road at the road's own height, the plaza is one closed solid, later streets cannot run into it."""
import unittest
from unittest.mock import patch

from cs2map import meshops, streets, terrain
from cs2map.project import OpError, Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS

PLAZA = [[-300, -300], [300, -300], [300, 300], [-300, 300]]


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class Junction(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        patcher = patch('cs2map.checks._asset_exists', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        terrain.add_terrain(self.p, self.m, {'name': 'flat', 'bounds': [[-2048, -2048], [2048, 2048]], 'cell': 128,
                                             'base_z': -64}, {})
        for name, path in (('west', [[-1500, 0, 0], [-200, 0, 0]]), ('north', [[0, 1500, 30], [0, 200, 30]]),
                           ('east', [[1500, 0, -10], [200, 0, -10]])):
            streets.add_street(self.p, self.m, {'op': 'add_street', 'name': name, 'terrain': 'flat', 'path': path,
                                                'z': 'path', 'width': 256}, {})

    def polys(self, nid):
        return meshops.decode_node(self.m.node(nid))

    def test_plaza_meets_every_road_at_its_height(self):
        out = streets.add_junction(self.p, self.m, {'op': 'add_junction', 'name': 'fork', 'polygon': PLAZA}, {})
        self.assertEqual(out['streets'], ['east', 'north', 'west'])
        node = self.m.node(out['mesh'])
        meshops.encode_polygons(node, self.polys(out['mesh']), self.p.texture_size, True)      # one closed solid
        tops = {}
        for poly in self.polys(out['mesh']):
            for c in poly.corners:
                k = (round(c.p[0], 2), round(c.p[1], 2))
                tops[k] = max(tops.get(k, -1e9), c.p[2])
        mouths = 0
        for (x, y), z in tops.items():
            for edge, want in ((x == -300 and abs(y) < 120, 0), (y == 300 and abs(x) < 120, 30),
                               (x == 300 and abs(y) < 120, -10)):
                if edge:
                    mouths += 1
                    self.assertAlmostEqual(z, want, delta=.01, msg=(x, y))
        self.assertGreaterEqual(mouths, 9)
        for data in streets.streets(self.m):          # nothing of a street is left inside the plaza
            for nid in data['mesh_ids']:
                for poly in self.polys(nid):
                    for c in poly.corners:
                        self.assertFalse(-299 < c.p[0] < 299 and -299 < c.p[1] < 299, (data['name'], c.p))

    def test_streets_after_a_junction_are_refused(self):
        streets.add_junction(self.p, self.m, {'op': 'add_junction', 'polygon': PLAZA}, {})
        with self.assertRaisesRegex(OpError, 'add every street first'):
            streets.add_street(self.p, self.m, {'op': 'add_street', 'name': 'south', 'terrain': 'flat', 'z': 'path',
                                                'path': [[0, -1500, 0], [0, -100, 0]]}, {})
        with self.assertRaisesRegex(OpError, 'no street road'):
            streets.add_junction(self.p, self.m, {'op': 'add_junction', 'name': 'far',
                                                  'polygon': [[1700, 1700], [1900, 1700], [1900, 1900]]}, {})


    def test_paved_area_without_streets_follows_the_ground(self):
        out = streets.add_junction(self.p, self.m, {'op': 'add_junction', 'name': 'yard', 'streets': [],
                                                    'polygon': [[1000, -1500], [1600, -1500], [1600, -900]]}, {})
        zs = {c.p[2] for poly in self.polys(out['mesh']) for c in poly.corners}
        self.assertAlmostEqual(max(zs), -62, delta=.01)                   # flat ground at -64, plus 2
        self.assertAlmostEqual(min(zs), -70, delta=.01)                   # 8 thick


if __name__ == '__main__':
    unittest.main()
