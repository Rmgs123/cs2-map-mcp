"""Offline drive check (drivecheck.py, route_test vehicle=...): a flat test field with a step, a wall and a corner."""
import unittest

from tests.helpers import HAVE_CS2, agent_suffix

MAP = 'test_drivecheck' + agent_suffix()
DEV = 'materials/dev/dev_measuregeneric01.vmat'


def box(mins, maxs):
    return {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': mins, 'maxs': maxs}, 'material': DEV}


@unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
class DriveCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cs2map import server
        cls.p = server.project
        cls.p.map_create(MAP, 'empty', overwrite=True)
        cls.p.map_edit(MAP, [
            box([-512, -512, -16], [4096, 2048, 0]),         # the field
            box([800, -512, 0], [1100, 120, 40]),            # a 40-unit ledge across the south lane (y < 120)
            box([2000, 400, 0], [2016, 2048, 200]),          # a wall across the north lane (y > 400)
        ])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.p.map_delete(MAP)
        except Exception:                               # noqa: BLE001 - scratch map, removed if possible
            pass

    def drive(self, route, **vehicle):
        from cs2map import drivecheck
        return drivecheck.check(self.p, MAP, route, {'length': 200, 'width': 80, **vehicle})

    def test_a_clear_lane_is_ok(self):
        r = self.drive([[0, 1000], [1800, 1000]])
        self.assertTrue(r['ok'], r)
        self.assertTrue(r['reached_end'])

    def test_a_ledge_is_a_step(self):
        r = self.drive([[0, 0], [1600, 0]])
        self.assertFalse(r['ok'])
        self.assertTrue(r.get('steps'), r)

    def test_a_wall_is_a_hit(self):
        r = self.drive([[1000, 1000], [3000, 1000]])
        self.assertTrue(r.get('hits'), r)

    def test_a_corner_tighter_than_the_turning_circle(self):
        r = self.drive([[0, 1400], [600, 1400], [600, 1300], [0, 1300]], steer=30)
        self.assertTrue(r.get('turns'), r)
        self.assertFalse(r['reached_end'])

    def test_bad_input_is_named(self):
        from cs2map import drivecheck
        with self.assertRaises(drivecheck.DriveError):
            drivecheck.check(self.p, MAP, [[0, 0]], {'length': 200, 'width': 80})
        with self.assertRaises(drivecheck.DriveError):
            drivecheck.check(self.p, MAP, [[0, 0], [10, 0]], 'no_such_car')


if __name__ == '__main__':
    unittest.main()
