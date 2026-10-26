"""Fixes for the 1.1 feedback journal: material region has one meaning, surface aliases, two-number pattern
counts, readable add_street errors, roads without sidewalks, smoothed traced axes."""
import unittest
from unittest.mock import patch

from cs2map import materials, mesh, streets, terrain, textures
from cs2map.project import OpError, Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


class Materials(unittest.TestCase):
    def test_region_is_left_top_right_bottom(self):
        # 23.10: [330,345,560,398] on a 960x1280 photo was read as x, y, width, height.
        self.assertEqual(textures._box([330, 345, 560, 398], (960, 1280)), (330, 345, 560, 398))
        with self.assertRaises(ValueError):
            textures._box([330, 345, 230, 53], (960, 1280))            # a width/height box is refused, not guessed
        with self.assertRaises(ValueError):
            textures._box([0, 0, 2000, 10], (960, 1280))

    def test_surface_aliases(self):
        self.assertEqual(materials.surface_name('asphalt'), 'concrete')
        self.assertEqual(materials.surface_name('brick'), 'brick')
        with self.assertRaises(ValueError):
            materials.surface_name('lava')

    def test_pattern_counts_across_and_down(self):
        def courses(height):          # rows that are mortar all the way across, counted as runs
            rows = (height == 0).all(axis=1)
            return int(rows[0]) + int((rows[1:] & ~rows[:-1]).sum())
        im, height = materials.generate('bricks', 256, (150, 60, 45), count=[12, 28])
        self.assertEqual(im.size, (256, 256))
        self.assertEqual(courses(height), 28)
        self.assertEqual(courses(materials.generate('bricks', 256, (150, 60, 45), count=8)[1]), 8)
        with self.assertRaises(ValueError):
            materials.generate('tiles', 256, count=[1, 2, 3])


class Shells(unittest.TestCase):
    def ring(self):
        """A window frame: a 40x40 ring with a 30x30 opening, 4 deep (extruded along +z)."""
        from cs2map import meshops
        return meshops.extrude_shape({'profile': [[0, 0], [40, 0], [40, 40], [0, 40]],
                                      'holes': [[[5, 5], [35, 5], [35, 35], [5, 35]]],
                                      'start': [0, 0, 0], 'end': [0, 0, 4]}, mesh.DEFAULT_MATERIAL)[0]

    def test_mullion_inside_the_opening_is_fine(self):
        from cs2map import meshops
        m = self.ring()
        m.extend(mesh.box((18, 6, 0.5), (22, 34, 3.5)))           # inside the opening, touching nothing
        meshops.validate(m)

    def test_rails_on_a_diagonal_are_fine(self):
        from cs2map import meshops
        rails = mesh.Mesh()
        for k in range(3):                                         # parallel rails 7 units apart along 45 degrees
            r = mesh.box((0, 7 * k, 0), (200, 7 * k + 3, 3))
            r.positions = [(x * 0.7071 - y * 0.7071, x * 0.7071 + y * 0.7071, z) for x, y, z in r.positions]
            rails.extend(r)
        meshops.validate(rails)

    def test_box_inside_box_is_refused_with_both_bounds(self):
        from cs2map import meshops
        m = mesh.box((0, 0, 0), (100, 100, 100))
        m.extend(mesh.box((40, 40, 40), (60, 60, 60)))
        with self.assertRaisesRegex(mesh.MeshError, 'corners inside shell 0'):
            meshops.validate(m)

    def test_many_aligned_windows_triangulate(self):
        from cs2map import meshops
        # 25.10 (TC Mayak): an L elevation whose windows share a column bridged two holes to one corner.
        outer = [[0, 0], [900, 0], [900, 300], [500, 300], [500, 700], [0, 700]]
        holes = [[[250, 375], [370, 375], [370, 495], [250, 495]], [[350, 100], [430, 100], [430, 180], [350, 180]],
                 [[125, 25], [185, 25], [185, 105], [125, 105]], [[125, 125], [225, 125], [225, 205], [125, 205]]]
        tris = meshops.triangulate(outer, holes)
        self.assertEqual(len(tris), len(outer) + 4 * len(holes) + 2 * len(holes) - 2)


class Views(unittest.TestCase):
    def test_a_rejected_frame_keeps_the_other_views(self):
        from tests.test_core_round3 import FakeGame
        fake = FakeGame(['same', 'same', 'same', 'same', 'c2', 'fresh'])
        out = fake.run([{'name': 'a', 'ang': [0, 0, 0]}, {'name': 'b', 'ang': [0, 40, 0]}])
        self.assertEqual([s['name'] for s in out['shots']], ['b'])
        self.assertEqual(out['rejected'][0]['name'], 'a')
        self.assertIn('did not change', out['rejected'][0]['reasons'])


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class Streets(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        patcher = patch('cs2map.checks._asset_exists', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        terrain.add_terrain(self.p, self.m, {'name': 'flat', 'bounds': [[-1500, -1500], [1500, 1500]], 'cell': 128}, {})

    def add(self, name, path, **kw):
        return streets.add_street(self.p, self.m, {'name': name, 'terrain': 'flat', 'path': path, 'width': 192, **kw}, {})

    def test_folded_edge_names_the_segment(self):
        zigzag = [[-900, 0], [0, 0], [40, 90], [80, 0], [900, 0]]       # short legs between sharp turns
        with self.assertRaises(OpError) as e:
            self.add('zig', zigzag)
        self.assertIn('segment', str(e.exception))
        self.assertIn('smooth', str(e.exception))

    def test_road_without_sidewalks(self):
        out = self.add('lane', [[-900, 0], [900, 0]], sidewalk=False)
        roles = {s['role'] for d in streets.streets(self.m) for s in d['surfaces']}
        self.assertEqual(roles, {'road'})
        self.assertTrue(out['ids'])

    def test_smooth_axis(self):
        pts = streets.smooth_path([[0, 0], [100, 0], [100, 100]])
        self.assertEqual(pts[0], [0, 0])
        self.assertEqual(pts[-1], [100, 100])
        self.assertGreater(len(pts), 3)


if __name__ == '__main__':
    unittest.main()


class Compiler(unittest.TestCase):
    def test_gpu_hint_only_on_a_failed_bake(self):
        from cs2map import compiler
        bad = compiler.analyze_log('vrad3: error: GPU Hang detected\n10 compiled, 1 failed')
        self.assertIn(compiler.GPU_HINT, bad.get('hints', []))
        good = compiler.analyze_log('ray tracing enabled\n10 compiled, 0 failed')
        self.assertNotIn(compiler.GPU_HINT, good.get('hints', []))

    def test_ray_tracing_guess(self):
        from cs2map import system
        self.assertTrue(system.ray_tracing('NVIDIA GeForce RTX 3060'))
        self.assertTrue(system.ray_tracing('AMD Radeon RX 6600 XT'))
        self.assertFalse(system.ray_tracing('NVIDIA GeForce GTX 1660 SUPER'))
        self.assertFalse(system.ray_tracing('Intel(R) UHD Graphics 630'))
        self.assertFalse(system.ray_tracing('AMD Radeon RX 580 Series'))


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class Batches(unittest.TestCase):
    def setUp(self):
        import tempfile
        from cs2map.config import load_settings
        import dataclasses
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = Project()
        self.m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        self.p.load = lambda name: self.m
        self.p.save = lambda m, name, force=False: {'saved': True}

    def box(self, x):
        return {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [x, 0, 0], 'maxs': [x + 32, 32, 32]}}

    def test_replace_batch_redoes_one_part(self):
        self.p.map_edit('t', [{'op': 'replace_batch', 'tag': 'market'}, self.box(0), self.box(100)])
        self.p.map_edit('t', [self.box(500)])                         # another part, untagged
        before = len(list(self.m.iter_nodes()))
        out = self.p.map_edit('t', [{'op': 'replace_batch', 'tag': 'market'}, self.box(0)])
        self.assertEqual(out['results'][0]['removed'], 2)
        self.assertEqual(len(list(self.m.iter_nodes())), before - 1)

    def test_all_failed_operations_are_named(self):
        with self.assertRaises(OpError) as e:
            self.p.map_edit('t', [self.box(0), {'op': 'add_mesh', 'shape': {'shape': 'wat'}}, self.box(64),
                                  {'op': 'nope'}])
        msg = str(e.exception)
        self.assertIn('2 operations failed', msg)
        self.assertIn('#1', msg)
        self.assertIn('#3', msg)


class Planar(unittest.TestCase):
    def test_rounded_big_face_with_holes_is_planar_a_warped_one_is_not(self):
        """10.10: a wall turned 37 degrees with vertices rounded to 0.01 was 'not planar'."""
        import math
        from cs2map import meshops
        a = math.radians(37)

        def at(x, z):
            return (round(x * math.cos(a), 2), round(x * math.sin(a), 2), round(z, 2))
        outer = [at(0, 0), at(1200.003, 0), at(1200.003, 400.004), at(0, 400.004)]
        holes = [[at(50 + i * 190, 100), at(50 + i * 190, 300.007), at(150 + i * 190, 300.007), at(150 + i * 190, 100)]
                 for i in range(6)]
        meshops.planar_parts(outer, holes)
        with self.assertRaisesRegex(meshops.MeshError, 'not planar'):
            meshops.planar_parts([(0, 0, 0), (100, 0, 0), (100, 100, 1), (0, 100, 0)])


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class RealSizeUV(unittest.TestCase):
    BRICK = 'materials/shared_base/brick/brick_soviet_white_01a.vmat'

    def test_wall_mode_and_auto_scale(self):
        """18.10: Valve's 2048 px brick at 0.25 units per texel made every course 4x too big; a 45-degree wall
        stretched it."""
        import math
        from cs2map import meshops
        p = Project()
        self.assertAlmostEqual(meshops.UNITS_PER_TEXEL(self.BRICK)[0], 123 / p.texture_size(self.BRICK)[0])
        c = math.sqrt(.5)
        wall = [[0, 0, 0], [100 * c, 100 * c, 0], [100 * c, 100 * c, 120], [0, 0, 120]]     # faces (+x, -y) / sqrt 2
        uv = meshops.uv_options(wall, {'mode': 'wall', 'scale': 'auto'}, self.BRICK)
        self.assertAlmostEqual(uv['scale'][0], 123 / p.texture_size(self.BRICK)[0])
        for got, want in ((uv['u_axis'], (c, c, 0)), (uv['v_axis'], (0, 0, -1))):
            for a, b in zip(got, want):
                self.assertAlmostEqual(a, b, places=6)
        self.assertEqual(meshops.uv_options(wall, {'scale': 'auto'}, 'materials/dev/reflectivity_30.vmat')['scale'],
                         [.25, .25])                                   # unknown real size: the default


class Stones(unittest.TestCase):
    def test_rubble_and_cobbles_tile_and_take_a_palette(self):
        import numpy as np
        for pattern in ('rubble', 'cobbles'):
            img, height = materials.generate(pattern, 256, palette=[[120, 110, 100], [90, 85, 80]], seed=4)
            a = np.asarray(img, dtype=float)
            # Seamless: the last column continues into the first as smoothly as any two neighbouring columns.
            inner = np.abs(np.diff(a, axis=1)).mean()
            self.assertLess(np.abs(a[:, -1] - a[:, 0]).mean(), inner * 3)
            self.assertLess(np.abs(a[-1] - a[0]).mean(), inner * 3)
            self.assertGreater((height == 0).mean(), .03)                     # there is mortar
        with self.assertRaises(ValueError):
            materials.generate('rubble', 256, joint=0.9)


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class ReferenceSheets(unittest.TestCase):
    def test_pairs_and_plan(self):
        """24.10: the plan of a place with photo cameras and the photo | game sheet were local scripts."""
        from PIL import Image
        from cs2map import reference, server
        from tests.helpers import ROOT, agent_suffix
        tmp = ROOT / '.local' / 'tmp'
        tmp.mkdir(parents=True, exist_ok=True)
        a, b = tmp / 'ref_a.png', tmp / 'ref_b.png'
        Image.new('RGB', (320, 200), (200, 50, 50)).save(a)
        Image.new('RGB', (300, 300), (50, 50, 200)).save(b)
        self.addCleanup(a.unlink, True)
        self.addCleanup(b.unlink, True)
        out = reference.reference_pairs([[str(a), str(b)], [str(b), str(a)]])
        self.assertEqual(out['pairs'], 2)
        p = server.project
        name = 'test_reference_plan' + agent_suffix()
        p.map_create(name, 'empty', overwrite=True)
        p.map_edit(name, [{'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [0, 0, 0], 'maxs': [256, 256, 32]}}])
        plan = reference.reference_plan(p, {'map': name, 'box': [[-128, -128], [384, 384]], 'size': 512,
                                            'marks': [['b1-01', 0, 0, 90]], 'image': str(a)})
        with Image.open(plan['plan']) as im:
            self.assertEqual(im.size, (512, 512))
            r, g, bl = im.getpixel((256, 256))[:3]                   # the box top, lighter than the background
            self.assertGreater(r + g + bl, 3 * 60)
        self.assertEqual(plan['units_per_px'], 1.0)
        with self.assertRaises(OpError):
            reference.reference_plan(p, {'map': name, 'box': [[0, 0], [10, 10]]})
