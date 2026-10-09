"""Unit and in-memory checks for the round-7 W wear extension."""
from __future__ import annotations

import unittest

from cs2map import checks, wear
from cs2map.project import OpError


class WearPure(unittest.TestCase):
    def test_profile_has_all_roles_and_stock_paths(self):
        profile = wear._profile('inferno')
        self.assertEqual(profile['schema'], 1)
        self.assertEqual(set(profile['roles']), {
            'base_dirt', 'leak', 'eave_grime', 'crack', 'plaster_loss', 'poster', 'graffiti',
            'ground_stain', 'ground_dirt'})
        for role in profile['roles'].values():
            self.assertTrue(all(x.startswith(('materials/', 'models/')) and x.endswith('.vmat')
                                for x in role['materials']))

    def test_safe_intervals_split_door_and_shop(self):
        house = {'doors': [[100, 64, 0]], 'shops': [[260, 128, 0]]}
        pieces = wear._intervals(0, 400, wear._blocked(house))
        self.assertEqual(len(pieces), 3)
        self.assertTrue(all(b > a for a, b in pieces))
        self.assertFalse(any(a < 100 < b for a, b in pieces))

    def test_world_face_coordinates(self):
        face = {'axis': 1, 'plane': 384, 'normal': [0, -1, 0], 'z0': 6, 'top': 400}
        self.assertEqual(wear._world(face, 128, 80, 12), [128, 372, 80])

    def test_surface_ray_hits_wall_and_misses_window(self):
        from cs2map import mesh
        from cs2map.vmap import VMap
        from tests.helpers import HAVE_CS2, SETTINGS
        if not HAVE_CS2:
            self.skipTest('CS2 fixture needed to construct VMap')
        m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        node = m.new_mesh(mesh.box([0, 0, 0], [256, 16, 200], 'materials/dev/dev_measuregeneric01.vmat'))
        m.attach(node)
        tris = list(checks._triangles(node))
        face = {'axis': 1, 'plane': 0, 'normal': [0, -1, 0], 'z0': 0, 'top': 200}
        house = {'from': 0, 'to': 256, 'top': 200}
        planes = wear._planes(tris, 1, -1, (0, 2), -1, 1)
        self.assertIsNotNone(wear._wall_patch(face, house, planes, [], 100, 100, (30, 40)))
        m.remove(node)
        left = m.new_mesh(mesh.box([0, 0, 0], [80, 16, 200], 'materials/dev/dev_measuregeneric01.vmat'))
        right = m.new_mesh(mesh.box([176, 0, 0], [256, 16, 200], 'materials/dev/dev_measuregeneric01.vmat'))
        m.attach(left)
        m.attach(right)
        tris = [t for n in (left, right) for t in checks._triangles(n)]
        planes = wear._planes(tris, 1, -1, (0, 2), -1, 1)
        self.assertIsNone(wear._wall_patch(face, house, planes, [], 128, 100, (30, 40)))

    def test_bad_input_is_closed(self):
        with self.assertRaises(OpError):
            wear._profile('../bad')
        with self.assertRaises(OpError):
            wear._num(True, 'density', 0, 2)


if __name__ == '__main__':
    unittest.main()
