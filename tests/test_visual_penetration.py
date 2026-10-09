"""Prop-in-mesh test on real triangles: slopes and set-in rocks are fine, walls and burial are not."""
import unittest

from cs2map import visual


def _slope(rise_per_unit=0.5, size=2000):
    """Two triangles of a plane z = rise * x over [-size, size]^2 (a 26.6 degree slope)."""
    a = (-size, -size, -size * rise_per_unit); b = (size, -size, size * rise_per_unit)
    c = (size, size, size * rise_per_unit); d = (-size, size, -size * rise_per_unit)
    return [(a, b, c), (a, c, d)]


def _wall_x(x=0.0, size=500):
    """A vertical wall in the plane x = const, facing -x."""
    return [((x, -size, 0), (x, size, 0), (x, size, size)), ((x, -size, 0), (x, size, size), (x, -size, size))]


class Penetration(unittest.TestCase):
    def test_tri_box(self):
        box = ([-10, -10, -10], [10, 10, 10])
        self.assertTrue(visual._tri_box(((-50, 0, 0), (50, 0, 0), (0, 50, 0)), box))      # crosses the box
        self.assertFalse(visual._tri_box(((-50, 0, 20), (50, 0, 20), (0, 50, 20)), box))   # above it
        self.assertTrue(visual._tri_box(((-1, -1, -1), (1, -1, -1), (0, 1, 1)), box))      # inside it
        self.assertFalse(visual._tri_box(((30, 30, -50), (60, 30, 50), (30, 60, 50)), box))  # beside it

    def test_prop_on_a_slope_is_fine(self):
        # A bench 76 wide resting on a 26.6 degree slope: its base corner dips 19 units into the ground.
        bench = ([-38, -15, -19], [38, 15, 46])
        self.assertIsNone(visual._penetration(bench, _slope()))

    def test_rock_set_into_the_ground_is_fine_but_buried_is_not(self):
        flat = _slope(0)
        self.assertIsNone(visual._penetration(([-20, -20, -12], [20, 20, 28]), flat))   # 30% sunk
        self.assertEqual(visual._penetration(([-20, -20, -30], [20, 20, 10]), flat), 'buried 30 units')

    def test_prop_through_a_wall_is_reported_but_touching_is_not(self):
        wall = _wall_x(0)
        self.assertEqual(visual._penetration(([-30, -30, 0], [30, 30, 60]), wall), 'wall')  # straddles it
        self.assertIsNone(visual._penetration(([-60, -30, 0], [8, 30, 60]), wall))          # 8-unit mounting contact


class ZFighting(unittest.TestCase):
    def setUp(self):
        from cs2map.project import Project
        from cs2map.vmap import VMap
        from cs2map import mesh
        from tests.helpers import HAVE_CS2, SETTINGS
        if not HAVE_CS2:
            self.skipTest('CS2 template needed')
        self.p, self.mesh = Project(), mesh
        self.m = VMap.from_template(SETTINGS.cs2_path, 'empty')

    def slab(self, lo, hi, mat):
        node = self.m.new_mesh(self.mesh.box(lo, hi, mat), texture_size=self.p.texture_size)
        self.m.attach(node)
        return node

    def codes(self):
        return [x for x in visual._zfight_checks(self.m, visual._visible_meshes(self.m))]

    def test_coplanar_and_near_coplanar_faces(self):
        self.slab([0, 0, -16], [256, 256, 0], 'materials/concrete/concrete_floor_04.vmat')
        self.slab([128, 0, -8], [384, 256, 0], 'materials/dev/reflectivity_30.vmat')        # same top plane
        found = self.codes()
        self.assertEqual(len(found), 1)
        self.assertIn('exactly coplanar', found[0]['message'])
        self.assertIn('(192.0, 128.0, 0.0)', found[0]['message'])   # centre of the overlap, not of a face

    def test_near_gap_flickers_one_unit_is_fine_same_material_is_ignored(self):
        self.slab([0, 0, -16], [256, 256, 0], 'materials/concrete/concrete_floor_04.vmat')
        self.slab([0, 0, -8], [64, 64, 0.1], 'materials/dev/reflectivity_30.vmat')         # 0.1 above: flickers
        self.slab([100, 100, -8], [164, 164, 1], 'materials/dev/reflectivity_30.vmat')     # 1 unit above: fine
        self.slab([200, 0, -8], [256, 56, 0], 'materials/concrete/concrete_floor_04.vmat')  # same material
        found = self.codes()
        self.assertEqual(len(found), 1)
        self.assertIn('0.10 units apart', found[0]['message'])


    def test_slab_set_into_ground_is_supported_but_hovering_slab_is_not(self):
        floor = 'materials/concrete/concrete_floor_04.vmat'
        for x in (0, 600, 1200, 1800, 2400, 3000):                 # the scene floor at z = 0 (median)
            self.slab([x, 0, -16], [x + 512, 512, 0], floor)
        self.slab([0, 1000, 0], [512, 1512, 210], floor)          # a hill block, top at z = 210
        set_in = self.slab([64, 1064, 200], [448, 1448, 216], floor)   # plaza sunk 10 into it
        hover = self.slab([600, 1064, 200], [984, 1448, 216], floor)   # nothing under it
        found = {x['id'] for x in visual._audit(self.p, self.m) if x['code'] == 'visual_floating_platform'}
        self.assertNotIn(set_in.get('nodeID'), found)
        self.assertIn(hover.get('nodeID'), found)

    def test_wall_part_needs_a_wall(self):
        from cs2map import kits
        self.slab([-512, -512, -16], [512, 512, 0], 'materials/concrete/concrete_floor_04.vmat')
        self.slab([200, -128, 0], [216, 128, 160], 'materials/concrete/concrete_floor_04.vmat')   # wall, face at x=200
        on_wall = kits.place(self.p, self.m, 'street_common/sign_italy', [200, 0, 96], (-1, 0, 0))
        on_floor = kits.place(self.p, self.m, 'street_common/sign_italy', [-200, 0, 96], (-1, 0, 0))
        props = visual._prop_entries(self.p, self.m)
        flagged = {x['id'] for x in visual._wall_mount_checks(self.p, props, visual._visible_meshes(self.m))}
        self.assertEqual(flagged, {on_floor.get('nodeID')})
        self.assertNotIn(on_wall.get('nodeID'), flagged)


if __name__ == '__main__':
    unittest.main()
