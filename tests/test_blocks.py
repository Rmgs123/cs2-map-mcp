"""add_block_house (blocks.py): closed walls with real openings, texture axes that never look along a face, a stock
entrance door fitted into its opening (also on a turned house), and the stair window kept clear of the canopy."""
import math
import unittest

from cs2map import blocks, checks, mesh
from cs2map.project import OpError, Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, check_topology


def front_hit(tris, x, z):
    """A ray from in front of the -y wall (face at y -224, 16 thick) hits the wall itself, not the house behind."""
    for t in tris:
        d = checks._ray((x, -240, z), (0, 1, 0), t)
        if d is not None and 0 <= d <= 40:
            return True
    return False


class SlabTexture(unittest.TestCase):
    def test_reveal_and_end_faces_take_texture_across_the_wall(self):
        # 16.11 in CS2: the reveal sides took the wall's own u axis, which looks along their normal: the texture
        # collapsed into streaks. Every face with its own axes must see them across itself.
        m = mesh.Mesh()
        fr = blocks._Frame([0.0, 0.0, 0.0], 30)
        blocks._slab(m, fr, (-200, -100), (1, 0), (0, -1), 400, 0, 300, [(100, 160, 80, 150), (240, 300, 0, 110)],
                     16, lambda a, b: 'materials/x.vmat', (0.25, 0.25), rows=[32])
        checked = 0
        for f in m.faces:
            if not f.u_axis:
                continue
            n = m.normal(f)
            for axis in (f.u_axis, f.v_axis):
                self.assertLess(abs(sum(a * b for a, b in zip(axis, n))), 0.01, (axis, n))
            checked += 1
        self.assertGreater(checked, 40)

    def test_ring_is_one_closed_solid_without_overlaps(self):
        # 16.11: end walls sunk 1 unit into the long walls left a strip of coplanar faces down every corner
        m = mesh.Mesh()
        fr = blocks._Frame([0.0, 0.0, 0.0], 15)
        corners = [(-300, -200), (300, -200), (300, 200), (-300, 200)]
        holes = [[(100, 160, 80, 150), (240, 300, 0, 110)], [(150, 200, 80, 150)], [], [(30, 90, 60, 120)]]
        blocks._ring(m, fr, corners, holes, 0, 300, 16, lambda a, b: 'materials/x.vmat', (0.25, 0.25), rows=[32])
        dest, face_of, *_ = m.halfedges()                   # raises on open or non-manifold edges
        self.assertNotIn(-1, face_of)
        for f in m.faces:
            if f.u_axis:
                n = m.normal(f)
                self.assertLess(abs(sum(a * b for a, b in zip(f.u_axis, n))), 0.01)
        # every outer face lies on the outline; no face pokes out of it
        a = math.radians(15)
        for p in m.positions:
            x, y = p[0] * math.cos(a) + p[1] * math.sin(a), -p[0] * math.sin(a) + p[1] * math.cos(a)
            self.assertLessEqual(abs(x), 300.01)
            self.assertLessEqual(abs(y), 200.01)
        with self.assertRaises(OpError):
            blocks._ring(mesh.Mesh(), fr, corners, [[(5, 60, 80, 150)], [], [], []], 0, 300, 16,
                         lambda a, b: 'materials/x.vmat', (0.25, 0.25))


@unittest.skipUnless(HAVE_CS2, 'Valve template and FGD required')
class BlockHouse(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(self.p.s.cs2_path, 'basic')

    def house(self, **kw):
        op = {'op': 'add_block_house', 'name': 'b', 'origin': [0, 0, 0], 'length': 672, 'floors': 4, **kw}
        return blocks.add_block_house(self.p, self.m, op, {})

    def parts(self, r):
        group = self.m.node(r['id'])
        meshes = [c for c in group['children'] if 'meshData' in c]
        doors = [c for c in group['children'] if c.type == 'CMapEntity' and self.m.classname(c) == 'prop_static']
        return meshes, doors

    def door_box(self, ent):
        """World bounds of a placed door leaf (model bounds from the stock facts, turned by yaw, scaled)."""
        from cs2map import propfacts
        props = ent['entity_properties']
        rec = propfacts.get(props['model'])
        lo, hi = rec['lo'], rec['hi']
        yaw = math.radians(float(ent['angles'][1]))
        s = float(ent['scales'][0])
        o = ent['origin']
        pts = []
        for x in (lo[0], hi[0]):
            for y in (lo[1], hi[1]):
                pts.append((o[0] + (x * math.cos(yaw) - y * math.sin(yaw)) * s,
                            o[1] + (x * math.sin(yaw) + y * math.cos(yaw)) * s))
        return ([min(p[0] for p in pts), min(p[1] for p in pts), o[2] + lo[2] * s],
                [max(p[0] for p in pts), max(p[1] for p in pts), o[2] + hi[2] * s])

    def test_walls_are_closed_and_openings_go_through(self):
        r = self.house(entrances=1)
        meshes, doors = self.parts(r)
        for node in meshes[:3]:                          # walls, core, roof: closed solids
            check_topology(self, node['meshData'])
        tris = list(checks._triangles(meshes[0]))
        # a window on the first floor: plinth 32 + 34 above the floor, 56 wide, centred in a slot of 112
        x = -336 + 0.5 * 112
        self.assertFalse(front_hit(tris, x, 32 + 34 + 30))
        self.assertTrue(front_hit(tris, x, 20))
        self.assertEqual(r['doors'], 1)
        self.assertEqual(len(doors), 1)

    def test_door_fills_its_opening_and_stays_in_the_wall(self):
        r = self.house(entrances=1)
        _, (door,) = self.parts(r)
        lo, hi = self.door_box(door)
        # slot 3 of 6 (centre 0); the leaf sinks into the reveals: wider than the opening by ~2, top over it
        self.assertAlmostEqual((lo[0] + hi[0]) / 2, -336 + 3.5 * 112, delta=0.5)
        self.assertAlmostEqual(lo[2], 0.0, delta=0.2)
        self.assertGreater(hi[0] - lo[0], 50)
        # thickness inside the wall: the face is y = -224, the dark core starts 15 in
        self.assertGreater(lo[1], -224.0)
        self.assertLess(hi[1], -224.0 + 15)
        self.assertEqual(door.get('cs2map_inset'), str(self.parts(r)[0][0]['nodeID']))

    def test_turned_house_keeps_the_door_in_place(self):
        r = self.house(entrances=1, yaw=30, origin=[100, 50, 8])
        _, (door,) = self.parts(r)
        lo, hi = self.door_box(door)
        a = math.radians(30)
        # local (x 56, y -224 + 8): middle of the opening, the leaf's mid-plane
        lx, ly = -336 + 3.5 * 112, -224 + blocks.DOOR_AT
        want = (100 + lx * math.cos(a) - ly * math.sin(a), 50 + lx * math.sin(a) + ly * math.cos(a))
        self.assertAlmostEqual((lo[0] + hi[0]) / 2, want[0], delta=0.6)
        self.assertAlmostEqual((lo[1] + hi[1]) / 2, want[1], delta=0.6)
        self.assertAlmostEqual(lo[2], 8.0, delta=0.2)

    def test_stair_window_clears_the_canopy_and_low_floors_shrink_the_door(self):
        r = self.house(entrances=1)
        walls = self.parts(r)[0][0]
        tris = list(checks._triangles(walls))
        x = -336 + 3.5 * 112
        # over the door (top ~110) and its canopy there is wall: the first landing window is gone
        self.assertTrue(front_hit(tris, x, 150))
        # the next landing window, half a floor under floor 2: 32 + 224 - 56 + 24 = 224 .. 268
        self.assertFalse(front_hit(tris, x, 246))
        low = self.house(name='low', floor_height=96, plinth=0, origin=[0, 2000, 0], entrances=1)
        _, (door,) = self.parts(low)
        self.assertLess(float(door['scales'][0]), 0.75)
        self.assertLessEqual(self.door_box(door)[1][2], 96 - 16 + 0.1)

    def test_no_collision_makes_the_door_not_solid(self):
        r = self.house(entrances=1, collision=False)
        _, (door,) = self.parts(r)
        self.assertEqual(str(door['entity_properties']['solid']), '0')

    def test_bad_fields_refused(self):
        with self.assertRaises(OpError):
            self.house(entrance=1)
        with self.assertRaises(OpError):
            self.house(door='materials/metal/metaldoor034a.vmat')
        with self.assertRaises(OpError):
            self.house(materials={'door': 'materials/metal/metaldoor034a.vmat'})
        with self.assertRaises(OpError):
            self.house(entrances=99)


if __name__ == '__main__':
    unittest.main()


class StepBar(unittest.TestCase):
    def test_stepped_bar_is_closed_for_any_steps(self):
        from cs2map import garages
        for tops in ([100, 100, 100], [100, 140, 90, 90, 130], [50]):
            m = mesh.Mesh()
            n = len(tops)
            xs = [i * 120.0 for i in range(n + 1)]
            bottoms = [-20 - 7 * (i % 3) for i in range(n)]
            garages._bar(m, blocks._Frame([0.0, 0.0, 0.0], 25), xs, bottoms, tops, -100, 100,
                         lambda a, b, i: 'materials/x.vmat', (0.25, 0.25), rows=[2, 30])
            dest, face_of, *_ = m.halfedges()
            self.assertNotIn(-1, face_of, tops)


@unittest.skipUnless(HAVE_CS2, 'Valve template and FGD required')
class GarageRow(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(self.p.s.cs2_path, 'basic')

    def row(self, **kw):
        from cs2map import garages
        op = {'op': 'add_garage_row', 'name': 'g', 'origin': [0, 0, 0], 'count': 5, **kw}
        return garages.add_garage_row(self.p, self.m, op, {})

    def meshes(self, r):
        return [c for c in self.m.node(r['id'])['children'] if 'meshData' in c]

    def test_flat_row_closed_bodies_solid_open_leaves_and_tinted_gates(self):
        from cs2map import garages
        r = self.row(open=[2], gates=[{'material': garages.SHEET, 'tint': [90, 140, 80]},
                                      'materials/metal/hr_metal/hr_metal_corrugated_001_blue.vmat'])
        self.assertEqual(r['floors'], [2.0] * 5)
        nodes = self.meshes(r)
        solid = [n for n in nodes if n['physicsType'] == 'default']
        # walls (two runs in one mesh), the open box's walls, the roof, its floor, its leaves
        self.assertEqual(len(solid), 5)
        for n in solid[:3]:
            check_topology(self, n['meshData'])
        gates = [n for n in nodes if n['physicsType'] == 'none' and tuple(n['tintColor'])[:3] != (255, 255, 255)]
        self.assertTrue(gates)
        # the open box is hollow: a ray through its gate reaches far inside; a closed box stops it at the gate
        tris_walls = [t for n in solid[:2] for t in checks._triangles(n)]
        x_open, x_closed = -340 + 2.5 * 136, -340 + 0.5 * 136

        def first(x):
            ds = [d for t in tris_walls for d in [checks._ray((x, -200, 40), (0, 1, 0), t)] if d is not None]
            return min(ds) if ds else None
        self.assertAlmostEqual(first(x_closed), 80, delta=0.1)          # the box face at y -120
        self.assertGreater(first(x_open), 300)                          # the back wall's inner face

    def test_row_on_a_slope_steps_and_open_floors_stay_over_the_ground(self):
        from cs2map import terrain
        terrain.add_terrain(self.p, self.m, {'op': 'add_terrain', 'name': 'land', 'bounds': [[-1200, -800], [1200, 800]],
                                             'base_z': 0, 'cell': 64,
                                             'ops': [{'op': 'hill', 'center': [900, 0], 'radius': 1400, 'height': 160}]}, {})
        r = self.row(count=8, terrain='land', open=[6])
        f = r['floors']
        self.assertTrue(all(b > a for a, b in zip(f, f[1:])), f)         # climbs with the hill to +x
        data = terrain.select(self.m, 'land')
        x0 = -544 + 6 * 136
        highest = max(terrain.sample(data, x, y)['z'] for x in (x0, x0 + 68, x0 + 136) for y in (-120, 0, 120))
        self.assertGreaterEqual(f[6], highest + 2 - 0.01)
        for n in self.meshes(r)[:3]:
            check_topology(self, n['meshData'])

    def test_bad_fields_refused(self):
        with self.assertRaises(OpError):
            self.row(open=[5])
        with self.assertRaises(OpError):
            self.row(gates=[{'material': 'materials/x.vmat', 'tint': [300, 0, 0]}])
        with self.assertRaises(OpError):
            self.row(box=[136, 240, 60])
        with self.assertRaises(OpError):
            self.row(colors=1)


@unittest.skipUnless(HAVE_CS2, 'Valve template and FGD required')
class Yard(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(self.p.s.cs2_path, 'basic')
        # a block of flats; the yard lies in front of it (its entrances face -y)
        blocks.add_block_house(self.p, self.m, {'op': 'add_block_house', 'name': 'h', 'origin': [0, 0, 0],
                                                'length': 1344, 'floors': 3}, {})

    def yard(self, **kw):
        from cs2map import yards
        op = {'op': 'add_yard', 'name': 'y', 'area': [[-800, -1100], [800, -1100], [800, -224], [-800, -224]], **kw}
        return yards.add_yard(self.p, self.m, op, {})

    def props(self, r):
        group = self.m.node(r['id'])
        return [(self.m.classname(c), c['entity_properties']['model'], c['origin'], c['angles'])
                for c in group['children'] if c.type == 'CMapEntity']

    def test_lawn_yard_benches_face_the_path_and_nothing_stands_in_the_house_or_on_the_path(self):
        from cs2map import yards
        r = self.yard(paths=[{'path': [[-800, -600], [800, -600]], 'width': 96}], benches=4, trees=3, bushes=6,
                      playground=True)
        self.assertFalse(r['courtyard'])
        self.assertEqual((r['benches'], r['bins']), (4, 4))
        self.assertEqual(r['playground'], len(yards.PLAY))
        for cls, model, o, a in self.props(r):
            self.assertLess(o[1], -224 + 1, model)                      # never in the house (its face is y -224)
            self.assertFalse(abs(o[1] + 600) < 48 and model != yards.LINE, (model, o))   # never on the path
            if model == yards.BENCH:                                    # seat (+x of the model) towards the path
                facing = math.radians(a[1])
                self.assertGreater(math.sin(facing) * (1 if o[1] < -600 else -1), 0.7, o)
        # bushes stand along the house wall
        bushes = [o for _, model, o, _ in self.props(r) if model in yards.BUSHES]
        self.assertTrue(bushes)
        self.assertTrue(all(-224 - 130 < o[1] < -224 - 20 for o in bushes), bushes)

    def test_paved_yard_is_a_courtyard_and_the_same_seed_gives_the_same_yard(self):
        self.m2 = self.m
        from cs2map import mesh as meshmod
        slab = meshmod.Mesh()
        fr = blocks._Frame([0.0, 0.0, 0.0], 0)
        blocks._box(slab, fr, (-900, -1200, -8), (900, -224, 0), 'materials/concrete/hr_c/hr_concrete_floor_05.vmat')
        self.m.attach(self.m.new_mesh(slab, texture_size=self.p.texture_size))
        a = self.yard(seed=7)
        self.assertTrue(a['courtyard'])
        first = self.props(a)
        self.m.remove(self.m.node(a['id']))
        b = self.yard(seed=7)
        self.assertEqual(first, self.props(b))


@unittest.skipUnless(HAVE_CS2, 'Valve template and FGD required')
class KeepClear(unittest.TestCase):
    def test_yard_keeps_off_entrances_and_garage_drives(self):
        from cs2map import garages, yards
        p = Project()
        m = VMap.from_template(p.s.cs2_path, 'basic')
        blocks.add_block_house(p, m, {'op': 'add_block_house', 'name': 'h', 'origin': [0, 0, 0], 'length': 1344,
                                      'floors': 3, 'entrances': 3}, {})
        garages.add_garage_row(p, m, {'op': 'add_garage_row', 'name': 'g', 'origin': [0, -1500, 0], 'yaw': 180,
                                      'count': 8}, {})
        r = yards.add_yard(p, m, {'op': 'add_yard', 'name': 'y', 'area': [[-800, -1380], [800, -1380], [800, -224],
                                                                          [-800, -224]],
                                  'bushes': 30, 'benches': 6, 'trees': 6, 'beds': 4}, {})
        doors = [c['origin'] for c in m.entities('prop_static') if 'door' in c['entity_properties']['model']]
        self.assertEqual(len(doors), 3)
        for c in m.node(r['id'])['children']:
            if c.type != 'CMapEntity':
                continue
            o = c['origin']
            for d in doors:                                     # nothing right in front of a door
                self.assertFalse(abs(o[0] - d[0]) < 60 and -224 - 130 < o[1] < -224, (c['entity_properties']['model'], o))
            # the drive: 200 in front of the gates (y -1380), along the row (x within 544 + 24)
            self.assertFalse(o[1] < -1380 + 190 and abs(o[0]) < 568 - 30, (c['entity_properties']['model'], o))


@unittest.skipUnless(HAVE_CS2, 'Valve template and FGD required')
class FenceToWall(unittest.TestCase):
    def test_fence_ends_go_onto_the_real_wall_face(self):
        from cs2map import builders
        p = Project()
        m = VMap.from_template(p.s.cs2_path, 'basic')
        blocks.add_block_house(p, m, {'op': 'add_block_house', 'name': 'h', 'origin': [0, 0, 0], 'length': 1344,
                                      'floors': 2}, {})
        # drawn 36 short of the front wall (y -224) at one end and 40 into it at the other ... of a second fence
        r = builders.add_fence(p, m, {'op': 'add_fence', 'kind': 'sheet', 'path': [[200, -900], [200, -260]],
                                      'to_wall': 'end'}, {})
        self.assertAlmostEqual(r['to_wall']['end'], 37, delta=0.5)
        r = builders.add_fence(p, m, {'op': 'add_fence', 'kind': 'planks', 'path': [[-200, -900], [-200, -184]],
                                      'to_wall': 'end'}, {})
        self.assertAlmostEqual(r['to_wall']['end'], -39, delta=0.5)
        with self.assertRaises(OpError):                       # nothing in front of the start
            builders.add_fence(p, m, {'op': 'add_fence', 'path': [[0, -900], [0, -500]], 'to_wall': True}, {})
        plain = builders.add_fence(p, m, {'op': 'add_fence', 'path': [[400, -900], [400, -500]]}, {})
        self.assertNotIn('to_wall', plain)


@unittest.skipUnless(HAVE_CS2, 'Valve template and FGD required')
class EstateDistrict(unittest.TestCase):
    def test_blocks_style_builds_free_standing_slabs_with_closed_bodies(self):
        from cs2map import districts
        p = Project()
        m = VMap.from_template(p.s.cs2_path, 'basic')
        r = districts.add_district(p, m, {'op': 'add_district', 'name': 'e', 'style': 'blocks', 'seed': 2,
                                          'area': {'mins': [-3000, -3000], 'maxs': [3000, 3000]}, 'block': [1600, 1200]}, {})
        self.assertGreaterEqual(r['houses'], r['blocks'])
        nodes = [c for c in m.node(r['id'])['children'] if 'meshData' in c]
        for n in nodes:
            check_topology(self, n['meshData'])
        tall = [n for n in nodes if checks._mesh_bounds(n)[1][2] >= 5 * 112]
        self.assertTrue(tall)
        with self.assertRaises(OpError):
            districts.add_district(p, m, {'op': 'add_district', 'name': 'x', 'style': 'nope',
                                          'area': {'mins': [0, 0], 'maxs': [1000, 1000]}}, {})


class GlassWords(unittest.TestCase):
    def test_panel_materials_are_not_window_panes(self):
        # 16.11: 'pane' matched concrete_panel_001: a whole panel wall counted as glass and every door "covered" it
        from cs2map.visual_scene import _is_glass
        self.assertFalse(_is_glass('materials/shared_base/concrete/concrete_panel_001.vmat'))
        self.assertTrue(_is_glass('materials/glass/window_opaque_1b.vmat'))
        self.assertTrue(_is_glass('materials/mcp/old_pane/old_pane.vmat'))


@unittest.skipUnless(HAVE_CS2, 'Valve template and FGD required')
class ClosedFence(unittest.TestCase):
    def test_a_fence_round_an_area_closes(self):
        # 16.11: a fence whose last point is its first failed (a post twice, two even spans in one mesh)
        from cs2map import builders
        p = Project()
        for kind, ring in (('panel', [[0, 0], [800, 0], [800, 600], [0, 600], [0, 0]]),
                           ('sheet', [[0, 0], [800, 0], [800, 600], [0, 0]]),
                           ('wall', [[0, 0], [800, 0], [800, 600], [0, 600], [0, 0]])):
            m = VMap.from_template(p.s.cs2_path, 'basic')
            r = builders.add_fence(p, m, {'op': 'add_fence', 'kind': kind, 'path': ring, 'clip': True}, {})
            for nid in r['ids']:
                check_topology(self, m.node(nid)['meshData'])
