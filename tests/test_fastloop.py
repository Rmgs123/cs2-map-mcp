"""The fast loop without the game: the face index, region cuts and the offline preview. Scratch maps in the addon."""
import unittest

import numpy as np

from tests.helpers import HAVE_CS2, agent_suffix

MAP = 'test_fastloop' + agent_suffix()
DEV = 'materials/dev/dev_measuregeneric01.vmat'


def box(x, y, size=64, z=0, material=DEV):
    return {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [x, y, z], 'maxs': [x + size, y + size, z + size]},
            'material': material}


@unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
class FastLoop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cs2map import server                       # the project with every extension's operations
        cls.p = server.project
        cls.p.map_create(MAP, 'basic', overwrite=True)
        # Ground of 32 x 32 cells over 4096 x 4096 units, crates on a 512 grid, one spawn far from the test zone.
        cls.p.map_edit(MAP, [
            {'op': 'add_mesh', 'shape': {'shape': 'terrain', 'mins': [0, 0, -32], 'maxs': [4096, 4096, 0],
                                         'cells': [32, 32], 'noise': {'amplitude': 12, 'seed': 3}},
             'material': DEV, 'ref': 'ground'},
            *[box(x, y, 64, 16) for x in range(128, 4096, 512) for y in range(128, 4096, 512)],
            {'op': 'add_entity', 'classname': 'info_player_terrorist', 'origin': [3900, 3900, 40]},
            {'op': 'add_entity', 'classname': 'prop_static', 'origin': [700, 700, 20],
             'keyvalues': {'model': 'models/props/de_nuke/crate_large.vmdl'}},
            {'op': 'add_entity', 'classname': 'prop_static', 'origin': [3500, 700, 20],
             'keyvalues': {'model': 'models/props/de_nuke/crate_large.vmdl'}},
        ])

    # -- the face index -------------------------------------------------------------------------------------------
    def test_triangles_equal_the_slow_reader(self):
        from cs2map import checks, faceindex
        m = self.p.load(MAP)
        scene = faceindex.extract(self.p, m)
        seen = 0
        for node in m.iter_nodes():
            if node.type != 'CMapMesh':
                continue
            old = np.asarray(list(checks._triangles(node)), dtype=np.float32).reshape(-1, 3, 3)
            new = scene.tris[scene.node == node['nodeID']]
            self.assertEqual(old.shape, new.shape)
            self.assertTrue(np.allclose(old, new, atol=1e-3), node['nodeID'])
            seen += 1
        self.assertGreaterEqual(seen, 65)
        self.assertEqual(len(scene.props), 2)

    def test_scene_is_cached_until_the_map_changes(self):
        from cs2map import faceindex
        first = faceindex.scene(self.p, MAP)
        cache = faceindex._cache_file(self.p.s, MAP)
        self.assertTrue(cache.is_file())
        stamp = cache.stat().st_mtime_ns
        again = faceindex.scene(self.p, MAP)
        self.assertEqual(again.tris.shape, first.tris.shape)
        self.p.map_edit(MAP, [box(2000, 2000, 32, 200)])
        changed = faceindex.scene(self.p, MAP)
        self.assertEqual(len(changed.tris), len(first.tris) + 12)
        self.assertNotEqual(cache.stat().st_mtime_ns, stamp, 'the cache was written again for the changed map')
        ids = [n['id'] for n in self.p.map_info(MAP, kind='mesh')['nodes']]
        self.p.map_edit(MAP, [{'op': 'delete', 'ids': [max(ids)]}])

    def test_mesh_with_many_sided_faces(self):
        from cs2map import checks, faceindex
        self.p.map_create(MAP + '_c', 'empty', overwrite=True)
        self.p.map_edit(MAP + '_c', [{'op': 'add_mesh', 'material': DEV, 'shape': {
            'shape': 'cylinder', 'center': [0, 0], 'radius': 64, 'z0': 0, 'z1': 96, 'sides': 24}}])
        m = self.p.load(MAP + '_c')
        node = next(n for n in m.iter_nodes() if n.type == 'CMapMesh')
        old = np.asarray(list(checks._triangles(node)), dtype=np.float32)
        new = faceindex.extract(self.p, m).tris
        self.assertEqual(old.shape, new.shape)
        self.assertTrue(np.allclose(old, new, atol=1e-3))

    # -- regions ----------------------------------------------------------------------------------------------------
    def test_region_keeps_the_zone_and_cuts_the_ground(self):
        from cs2map import faceindex, region
        m = self.p.load(MAP)
        before = sum(1 for _ in m.iter_nodes())
        stats = region.cut(self.p, m, region.parse([[512, 512], [1024, 1024]]))
        self.assertEqual(stats['cut'], 1, 'the ground is cut by faces')
        self.assertLess(stats['kept'], before // 4)
        scene = faceindex.extract(self.p, m)
        ground = self.p.map_info(MAP, ids=None, kind='mesh')['nodes'][0]['id']
        tris = scene.tris[scene.node == ground]
        top = tris[(np.ptp(tris[:, :, 0], axis=1) < 200) & (np.ptp(tris[:, :, 1], axis=1) < 200)]   # cells (the big
        xy = top[:, :, :2]                                        # bottom and sides of the slab touch the zone)
        self.assertGreaterEqual(xy.min(), 512 - region.MARGIN - 128.01)
        self.assertLessEqual(xy.max(), 1024 + region.MARGIN + 128.01)
        self.assertGreater(len(xy), 100)
        crates = [n for n in m.iter_nodes() if n.type == 'CMapMesh' and n['nodeID'] != ground]
        self.assertTrue(crates)
        for n in crates:
            lo = faceindex.world_vertices(n).min(axis=0)
            self.assertTrue(512 - region.MARGIN - 64 <= lo[0] <= 1024 + region.MARGIN, lo)
        models = m.entities('prop_static')
        self.assertEqual([tuple(e['origin'])[:2] for e in models], [(700.0, 700.0)])
        self.assertEqual(stats['spawn'], 'added at the centre of the first box')
        spawns = m.entities('info_player_terrorist')
        self.assertEqual(len(spawns), 1)
        self.assertAlmostEqual(spawns[0]['origin'][1], 768.0)
        self.assertTrue(m.entities('light_environment'), 'the sun of the whole map stays')
        m.to_bytes()                                    # the cut map encodes and parses

    def test_region_arguments(self):
        from cs2map import region
        from cs2map.vmap import MapError
        self.assertEqual(region.parse([[100, 900], [600, 100]]), [[[100.0, 100.0], [600.0, 900.0]]])
        self.assertEqual(len(region.parse([[[0, 0], [100, 100]], [[500, 500], [900, 900]]])), 2)
        for bad in ([[0, 0], [10, 10]], 'nope', [[0, 0]], [[[0, 0], [100, 100]]] * 9):
            with self.assertRaises(MapError):
                region.parse(bad)
        self.assertEqual(region._merge([[[0, 0], [10, 10]], [[5, 5], [20, 20]], [[100, 100], [110, 110]]]),
                         [[[0, 0], [20, 20]], [[100, 100], [110, 110]]])
        with self.assertRaises(MapError):
            region.cut(self.p, self.p.load(MAP), region.parse([[90000, 90000], [91000, 91000]]))
        self.assertIsNone(region.info(MAP))
        # 12.11: marks all over a town gave a "region" of 58 % of the map: said in the answer
        self.assertIsNone(region.wide_note({'kept': 40, 'dropped': 60}, [[0, 0], [100, 100]]))
        self.assertIn('58 %', region.wide_note({'kept': 58, 'dropped': 42}, [[0, 0], [100, 100]]))
        self.assertIn('every stored mark', region.wide_note({'kept': 58, 'dropped': 42}, 'marks'))

    # -- the offline preview -------------------------------------------------------------------------------------------
    def test_offline_preview_draws_the_map(self):
        from PIL import Image
        out = self.p.map_preview(MAP, [
            {'name': 'Top', 'top': [[0, 0], [1024, 1024]]},
            {'name': 'crate', 'pos': [400, 160, 30], 'ang': [10, 180, 0]},
            {'name': 'side', 'side': [[0, 0], [1024, 1024]], 'yaw': 90}])
        self.assertEqual([s['name'] for s in out['shots']], ['top', 'crate', 'side'])
        top = np.asarray(Image.open(out['shots'][0]['path']).convert('RGB')).astype(int)
        self.assertEqual(out['shots'][0]['x'], [-398, 1422])
        drawn = (np.abs(top - np.asarray([36, 38, 44])).sum(axis=2) > 12).mean()
        self.assertGreater(drawn, 0.7, 'the ground fills the picture where the map has ground (x >= 0)')
        crate = np.asarray(Image.open(out['shots'][1]['path']).convert('RGB')).astype(int)
        sky = np.abs(crate[:40] - np.asarray([118, 160, 214])).sum(axis=2).mean()
        self.assertLess(sky, 60, 'sky at the top of a level view')
        self.assertGreater(np.abs(crate[300:] - crate[:40].mean(axis=(0, 1))).sum(axis=2).mean(), 60, 'ground below')
        self.assertTrue(Image.open(out['sheet']).size[0] >= 960)
        default = self.p.map_preview(MAP)
        self.assertEqual(len(default['shots']), 3)

    def test_offline_preview_arguments(self):
        from cs2map.vmap import MapError
        for bad in ([{'name': 'x'}], [{'pos': [1, 2]}], [{'top': [[0, 0], [0, 0]]}], [{'pos': [0, 0, 0]}] * 13):
            with self.assertRaises(MapError):
                self.p.map_preview(MAP, bad)
        with self.assertRaises(MapError):
            self.p.map_preview(None)

    def test_near_plane_clip_keeps_the_winding(self):
        from cs2map import preview3d
        cam = np.asarray([[[0, 0, 10.0], [10, 0, 10], [0, 10, -10]],       # one vertex behind
                          [[0, 0, -10.0], [10, 0, -10], [0, 10, 10]],      # two behind
                          [[0, 0, -5.0], [1, 0, -5], [0, 1, -5]]])         # all behind
        out, ids = preview3d._clip_near(cam, np.arange(3))
        self.assertEqual(sorted(ids.tolist()), [0, 0, 1])
        self.assertTrue((out[:, :, 2] >= preview3d.NEAR - 1e-6).all())
        for tri, i in zip(out, ids):
            n = np.cross(tri[1] - tri[0], tri[2] - tri[0])
            ref = np.cross(cam[i][1] - cam[i][0], cam[i][2] - cam[i][0])
            self.assertGreater(float(n @ ref), 0)

    # -- the scene checks on a big map ------------------------------------------------------------------------------
    def test_findings_are_kept_until_the_map_changes(self):
        from cs2map import visual
        first = self.p_visual(MAP)
        self.assertNotIn('cached', first['summary'])
        again = self.p_visual(MAP, limit=1)
        self.assertIn('cached', again['summary'])
        self.assertEqual(again['summary']['by_code'], first['summary']['by_code'])
        self.assertFalse('cached' in self.p_visual(MAP, box=[[0, 0], [1024, 1024]])['summary'], 'a zone is its own check')
        self.assertTrue(visual._check_file(self.p, MAP, None).is_file())
        self.p.map_edit(MAP, [{'op': 'add_entity', 'classname': 'prop_static', 'origin': [1500, 1500, 400],
                               'keyvalues': {'model': 'models/props/de_nuke/crate_large.vmdl'}, 'ref': 'air'}])
        changed = self.p_visual(MAP)
        self.assertNotIn('cached', changed['summary'])
        self.assertEqual(changed['summary']['by_code'].get('visual_floating_prop', 0),
                         first['summary']['by_code'].get('visual_floating_prop', 0) + 1)
        from cs2map import checks
        checks.check_map(self.p, MAP)                              # shares the findings of the same file
        self.assertIn('cached', self.p_visual(MAP)['summary'])
        ids = [e['id'] for e in self.p.map_info(MAP, classname='prop_static')['nodes']]
        self.p.map_edit(MAP, [{'op': 'delete', 'ids': [max(ids)]}])

    def p_visual(self, name, limit=200, box=None):
        from cs2map import visual
        return visual.visual_check(self.p, name, limit, box)

    def test_a_long_answer_shows_every_kind_and_the_busiest_zones(self):
        from cs2map import visual
        problems = ([{'code': 'visual_phantom_floor', 'severity': 'error', 'id': i, 'at': [100, 100, 0]} for i in range(500)]
                    + [{'code': 'visual_floating_prop', 'severity': 'warning', 'id': 1000 + i, 'at': [5000 + i, 100, 0]}
                       for i in range(30)]
                    + [{'code': 'visual_z_fighting', 'severity': 'warning', 'id': 2000, 'at': [-3000, -3000, 0]}])
        shown = visual._fair_share(problems, 50)
        self.assertEqual(len(shown), 50)
        kinds = [p['code'] for p in shown]
        self.assertEqual(kinds.count('visual_z_fighting'), 1)
        self.assertEqual(kinds.count('visual_floating_prop'), 24)
        self.assertEqual(shown[0]['severity'], 'error')
        zones = visual._zones(problems)
        self.assertEqual(zones[0], {'box': [[0.0, 0.0], [1024.0, 1024.0]], 'problems': 500, 'errors': 500})
        self.assertEqual(len(zones), 3)

    def test_fast_tests_answer_as_the_slow_ones(self):
        import random
        from cs2map import checks, mesh, visual
        from cs2map.vmap import VMap
        m = VMap.from_template(self.p.s.cs2_path, 'empty')
        m.attach(m.new_mesh(mesh.box([0, 0, 0], [128, 96, 64])))
        m.attach(m.new_mesh(mesh.box([40, 40, 10], [300, 60, 30])))
        rnd = random.Random(7)
        fast = visual._InsideTest()
        for _, _, tris in visual._solid_meshes(m):
            for _ in range(300):
                point = (rnd.uniform(-40, 340), rnd.uniform(-40, 140), rnd.uniform(-40, 100))
                self.assertEqual(fast.inside(point, tris), checks._inside_mesh(point, tris), point)
        # the side contact of a prop with the boxes around it
        def boxed():
            lo = [rnd.uniform(0, 400) for _ in range(3)]
            return (tuple(lo), tuple(x + rnd.uniform(4, 80) for x in lo))
        meshes = [(None, boxed(), []) for _ in range(60)]
        props = [{'bounds': boxed(), 'model': 'models/x.vmdl'} for _ in range(60)]
        boxes, grid, near = visual._Boxes(meshes, props), visual._FloorGrid([]), visual._NearGrid([])
        got = [visual._has_support(e, meshes, props, grid, near, boxes) for e in props]
        self.assertEqual(got, [visual._has_support(e, meshes, props, grid, near) for e in props])
        self.assertTrue(any(ok for ok, _ in got) and not all(ok for ok, _ in got))

    def test_pipes_take_mesh_options(self):
        r = self.p.map_edit(MAP, [{'op': 'add_pipes', 'points': [[0, 0, 300], [128, 0, 300]],
                                   'options': {'disableShadows': 1}, 'ref': 'pipe'},
                                  {'op': 'add_cables', 'start': [0, 64, 300], 'end': [128, 64, 300],
                                   'options': {'renderToCubemaps': False}}], dry_run=True)
        self.assertEqual(r['applied'], 2)
        from cs2map.project import OpError
        with self.assertRaises(OpError):
            self.p.map_edit(MAP, [{'op': 'add_pipes', 'points': [[0, 0, 300], [128, 0, 300]],
                                   'options': {'no_such_option': 1}}], dry_run=True)


if __name__ == '__main__':
    unittest.main()
