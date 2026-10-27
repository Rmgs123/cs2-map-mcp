import unittest

from tests.helpers import check_topology, signed_volume

from cs2map import shapes
from cs2map.mesh import MeshError


class Shapes(unittest.TestCase):
    def closed(self, spec, count=None):
        meshes = shapes.build(spec)
        if count is not None:
            self.assertEqual(len(meshes), count)
        for m in meshes:
            el = m.to_dmx()
            check_topology(self, el, closed=True)
            self.assertGreater(signed_volume(el), 0)
        return meshes

    def test_room_plain(self):
        self.closed({'shape': 'room', 'mins': [0, 0, 0], 'maxs': [512, 256, 192]}, 6)

    def test_room_openings(self):
        meshes = self.closed({'shape': 'room', 'mins': [0, 0, 0], 'maxs': [512, 256, 192], 'thickness': 16,
                              'openings': [{'wall': '-x', 'center': 128, 'width': 96, 'height': 112},
                                           {'wall': '+y', 'center': 100, 'width': 64, 'height': 64, 'bottom': 64},
                                           {'wall': '+y', 'center': 300, 'width': 64, 'height': 64, 'bottom': 64}]})
        # floor, ceiling, -x (3 pieces), +x (1), -y (1), +y (2 windows -> 3 columns + 2x(below+above))
        self.assertEqual(len(meshes), 2 + 3 + 1 + 1 + 7)

    def test_room_bad_opening(self):
        with self.assertRaises(MeshError):
            shapes.build({'shape': 'room', 'mins': [0, 0, 0], 'maxs': [100, 100, 100],
                          'openings': [{'wall': '-x', 'center': 50, 'width': 200, 'height': 50}]})

    def test_arch(self):
        for axis in ('x', 'y'):
            with self.subTest(axis=axis):
                self.closed({'shape': 'arch', 'mins': [0, 0, 0], 'maxs': [256, 16, 192] if axis == 'x' else [16, 256, 192],
                             'axis': axis, 'opening_width': 96, 'opening_height': 144, 'segments': 8}, 1)

    def test_terrain_noise_and_heights(self):
        self.closed({'shape': 'terrain', 'mins': [0, 0, 0], 'maxs': [1024, 512, 0], 'cells': [8, 4],
                     'noise': {'amplitude': 48, 'scale': 300, 'seed': 3}}, 1)
        grid = [[(i * j) % 5 * 8 for j in range(3)] for i in range(4)]
        self.closed({'shape': 'terrain', 'mins': [0, 0, 0], 'maxs': [300, 200, 10], 'cells': [3, 2],
                     'heights': grid}, 1)

    def test_terrain_absolute_heights_are_world_z(self):
        grid = [[200 + 10 * i + j for j in range(3)] for i in range(4)]
        spec = {'shape': 'terrain', 'mins': [0, 0, 0], 'maxs': [300, 200, 230], 'cells': [3, 2], 'heights': grid}
        top = max(p[2] for p in shapes.build({**spec, 'absolute': True})[0].positions)
        self.assertEqual(top, 232)                               # the highest height itself
        doubled = max(p[2] for p in shapes.build(spec)[0].positions)
        self.assertEqual(doubled, 462)                           # default: added to maxs.z
        with self.assertRaises(MeshError):
            shapes.build({**spec, 'heights': None, 'absolute': True})

    def test_polyhedron_tetra(self):
        self.closed({'shape': 'polyhedron', 'vertices': [[0, 0, 0], [100, 0, 0], [0, 100, 0], [0, 0, 100]],
                     'faces': [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]}, 1)

    def test_unknown_key_rejected(self):
        with self.assertRaises(MeshError):
            shapes.build({'shape': 'box', 'mins': [0, 0, 0], 'maxs': [1, 1, 1], 'colour': 'red'})


if __name__ == '__main__':
    unittest.main()
