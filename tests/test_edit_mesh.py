"""edit_mesh: piece-by-piece edits keep the mesh closed, valid and textured; a failing step changes nothing."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cs2map import mesh, meshops as g
from cs2map.dmx import Document, dumps
from cs2map.ext import geometry
from tests.helpers import check_topology
from tests.test_geometry import MAT, node


def bounds(n):
    pts = [v.p for p in g.decode_node(n) for v in p.corners]
    return ([round(min(p[k] for p in pts), 3) for k in range(3)], [round(max(p[k] for p in pts), 3) for k in range(3)])


class EditMesh(unittest.TestCase):
    def setUp(self):
        self.refs = {}

    def make(self, m):
        self.n = node(m)
        self.p = SimpleNamespace(resolve=lambda *a: self.n, texture_size=lambda _: (512, 512))
        return self.n

    def edit(self, steps, **extra):
        with patch.object(geometry.checks, '_asset_exists', return_value=True):
            out = geometry.edit_mesh(self.p, None, {'op': 'edit_mesh', 'id': 42, 'steps': steps, **extra}, self.refs)
        check_topology(self, self.n['meshData'])
        self.assertNotIn(-1, self.n['meshData']['edgeFaceIndices'])          # still closed
        return out

    def test_bevel_box_edges_chamfer_and_round(self):
        self.make(mesh.box((0, 0, 0), (64, 64, 64)))
        out = self.edit([{'do': 'bevel', 'edges': 'sharp', 'width': 4}])
        self.assertEqual(out['faces'], 18)                     # 6 faces + 12 chamfers meeting at the corners
        self.assertEqual(bounds(self.n), ([0, 0, 0], [64, 64, 64]))
        self.make(mesh.box((0, 0, 0), (64, 64, 32)))
        out = self.edit([{'do': 'bevel', 'edges': 'top', 'width': 6, 'segments': 3}])
        self.assertEqual(out['steps'][0]['edges'], 4)
        top = [p for p in g.decode_node(self.n) if p.n[2] > 0.999]
        self.assertEqual(len(top), 1)
        zs = sorted({round(v.p[0], 2) for v in top[0].corners})
        self.assertEqual((zs[0], zs[-1]), (6.0, 58.0))          # the top shrank by the bevel width

    def test_inset_with_depth_makes_a_panel(self):
        self.make(mesh.box((0, 0, 0), (128, 16, 128)))
        out = self.edit([{'do': 'inset', 'faces': '-y', 'width': 16, 'depth': -4}])
        self.assertEqual(out['steps'][0]['frames'], 4)
        self.assertEqual(out['faces'], 6 + 4 + 4)
        inner = [p for p in g.decode_node(self.n) if p.n[1] < -0.999 and abs(p.corners[0].p[1] - 4) < 1e-3]
        self.assertEqual(len(inner), 1)                        # pushed 4 units in
        self.assertEqual(bounds(self.n), ([0, 0, 0], [128, 16, 128]))

    def test_extrude_and_last_selection(self):
        self.make(mesh.box((0, 0, 0), (64, 64, 64)))
        self.edit([{'do': 'extrude', 'faces': '+x', 'distance': 32},
                   {'do': 'move', 'faces': 'last', 'offset': [0, 0, 16]}])
        self.assertEqual(bounds(self.n), ([0, 0, 0], [96, 64, 80]))

    def test_column_subdivide_twist_bend_taper(self):
        self.make(mesh.box((-16, -16, 0), (16, 16, 256)))
        out = self.edit([{'do': 'subdivide', 'axes': 'z', 'cell': 32},
                         {'do': 'twist', 'axis': 'z', 'angle': 90},
                         {'do': 'taper', 'axis': 'z', 'scale': 0.5}])
        self.assertGreater(out['faces'], 30)
        lo, hi = bounds(self.n)
        self.assertEqual((lo[2], hi[2]), (0, 256))
        top = [v.p for p in g.decode_node(self.n) for v in p.corners if abs(v.p[2] - 256) < 1e-3]
        self.assertTrue(all(abs(abs(x) - 8) < 0.01 for p in top for x in p[:2]))   # tapered to half, twisted 90
        self.make(mesh.box((-16, -16, 0), (16, 16, 256)))
        out = self.edit([{'do': 'subdivide', 'axes': 'z', 'cell': 16},
                         {'do': 'bend', 'axis': 'z', 'toward': '+x', 'angle': 90}])
        lo, hi = bounds(self.n)
        self.assertGreater(hi[0], 120)                         # the top bent over toward +x
        self.assertLess(hi[2], 200)

    def test_noise_and_smooth_on_a_rock(self):
        rock = g.revolve_shape({'shape': 'revolve', 'origin': [0, 0, 0], 'segments': 12,
                                'profile': [[0, -40], [30, -30], [40, 0], [30, 30], [0, 40]], 'profile_closed': False},
                               MAT)[0]
        self.make(rock)
        out = self.edit([{'do': 'noise', 'amount': 6, 'radius': 24, 'seed': 4},
                         {'do': 'smooth', 'iterations': 1, 'strength': 0.3}])
        self.assertEqual(out['steps'][0]['verts'], out['steps'][1]['verts'])
        lo, hi = bounds(self.n)
        self.assertNotEqual(lo, [-40, -40, -40])

    def test_mirror_keeps_one_half_and_makes_it_symmetric(self):
        self.make(mesh.box((-32, 0, 0), (64, 32, 32)))
        self.edit([{'do': 'move', 'verts': {'mins': [60, -1, 30], 'maxs': [65, 33, 33]}, 'offset': [0, 0, 40]},
                   {'do': 'mirror', 'axis': 'x', 'at': 0, 'keep': '+'}])
        lo, hi = bounds(self.n)
        self.assertEqual((lo[0], hi[0]), (-64, 64))
        self.assertEqual(hi[2], 72)
        zs_left = max(v.p[2] for p in g.decode_node(self.n) for v in p.corners if v.p[0] < -63)
        self.assertEqual(round(zs_left, 3), 72)

    def test_weld_merges_close_vertices(self):
        self.make(mesh.box((0, 0, 0), (64, 64, 64)))
        out = self.edit([{'do': 'move', 'verts': [[64, 64, 64]], 'offset': [-63.8, 0, 0]},
                         {'do': 'weld', 'distance': 0.5}])
        self.assertEqual(out['steps'][1]['merged'], 1)

    def test_world_uv_follows_hammer_and_keep_follows_vertices(self):
        self.make(mesh.box((0, 0, 0), (64, 64, 64)))
        self.edit([{'do': 'move', 'faces': 'top', 'offset': [32, 0, 0]}])
        top = next(p for p in g.decode_node(self.n) if p.n[2] > 0.99)
        u, v = mesh.world_axes(top.n)
        for c in top.corners:
            self.assertAlmostEqual(c.uv[0], (c.p[0] / 0.25) / 512, 4)
        self.make(mesh.box((0, 0, 0), (64, 64, 64)))
        before = {tuple(round(x, 2) for x in c.p): c.uv for p in g.decode_node(self.n) if p.n[2] > 0.99 for c in p.corners}
        self.edit([{'do': 'move', 'faces': 'top', 'offset': [32, 0, 0]}], uv='keep')
        top = next(p for p in g.decode_node(self.n) if p.n[2] > 0.99)
        for c in top.corners:
            self.assertEqual(c.uv, before[(round(c.p[0] - 32, 2), round(c.p[1], 2), round(c.p[2], 2))])

    def test_failures_change_nothing(self):
        self.make(mesh.box((0, 0, 0), (64, 16, 64)))
        before = dumps(Document(self.n))
        cases = [([{'do': 'inset', 'faces': '-y', 'width': 40}], 'too large'),
                 ([{'do': 'bevel', 'width': 20}], 'wider than a face'),
                 ([{'do': 'spin'}], 'do'),
                 ([{'do': 'move', 'offset': [1, 0, 0], 'typo': 1}], 'unknown fields'),
                 ([{'do': 'extrude', 'faces': 'top', 'distance': -64}], 'not a valid solid'),
                 ([{'do': 'mirror', 'axis': 'x', 'at': 500}], 'cross the mesh')]
        for steps, msg in cases:
            with self.subTest(msg=msg), self.assertRaisesRegex(ValueError, msg):
                self.edit(steps)
            self.assertEqual(dumps(Document(self.n)), before)

    def test_concave_edge_is_refused(self):
        ell = [[0, 0], [100, 0], [100, 20], [20, 20], [20, 100], [0, 100]]
        self.make(g.extrude_shape({'shape': 'extrude', 'profile': ell, 'start': [0, 0, 0], 'end': [0, 0, 50],
                                   'caps': True}, MAT)[0])
        with self.assertRaisesRegex(ValueError, 'concave'):
            self.edit([{'do': 'bevel', 'edges': {'faces': 'all'}, 'width': 2}])
        out = self.edit([{'do': 'bevel', 'edges': 'sharp', 'width': 2}])      # "sharp" takes convex edges only
        self.assertGreater(out['steps'][0]['edges'], 10)

    def test_big_bevel_is_fast_enough(self):
        self.make(g.revolve_shape({'shape': 'revolve', 'origin': [0, 0, 0], 'segments': 16,
                                   'profile': [[24, 0], [24, 128], [0, 128], [0, 0]]}, MAT)[0])
        t = time.time()
        self.edit([{'do': 'bevel', 'edges': 'top', 'width': 3, 'segments': 2, 'angle': 60}])
        self.assertLess(time.time() - t, 20)


if __name__ == '__main__':
    unittest.main()
