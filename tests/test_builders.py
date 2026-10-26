"""Fences, railings, banks and bare trees: they follow the terrain, every mesh is a valid closed solid, see-through
fences and railings get a player clip, gates are openings, low banks are reported as climbable."""
import unittest
from unittest.mock import patch

from cs2map import builders, meshops, terrain
from cs2map.project import OpError, Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS, agent_suffix


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class Builders(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        patcher = patch('cs2map.checks._asset_exists', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        terrain.add_terrain(self.p, self.m, {'name': 'hill', 'bounds': [[-2048, -2048], [2048, 2048]], 'cell': 128,
                                             'ops': [{'op': 'hill', 'center': [600, 0], 'radius': 900, 'height': 300}]}, {})

    def closed(self, ids):
        for nid in ids:
            node = self.m.node(nid)
            meshops.encode_polygons(node, meshops.decode_node(node), self.p.texture_size, True)

    def test_fence_kinds_follow_the_ground(self):
        for kind in builders.FENCE_KINDS:
            out = builders.add_fence(self.p, self.m, {'op': 'add_fence', 'kind': kind,
                                                      'path': [[-400, -300], [400, -300], [700, 200]]}, {})
            self.closed(out['ids'])
            if kind == 'wall':
                wall = out
        out = wall
        ground = builders._ground(self.m, {})
        self.assertGreater(ground(700, 200) - ground(-400, -300), 100)
        pts = [c.p for i in out['ids'] for poly in meshops.decode_node(self.m.node(i)) for c in poly.corners]
        for x, y in ((-400, -300), (700, 200)):
            near = [p[2] for p in pts if abs(p[0] - x) < 16 and abs(p[1] - y) < 16]
            self.assertAlmostEqual(min(near), ground(x, y) - 4, delta=1)      # the wall is sunk 4 units

    def test_gate_is_an_opening(self):
        full = builders.add_fence(self.p, self.m, {'op': 'add_fence', 'path': [[-500, 0], [500, 0]]}, {})
        gated = builders.add_fence(self.p, self.m, {'op': 'add_fence', 'path': [[-500, 400], [500, 400]],
                                                    'gates': [{'at': 500, 'width': 120}]}, {})
        def faces(ids):
            return sum(len(meshops.decode_node(self.m.node(i))) for i in ids)
        self.assertLess(faces(gated['ids']), faces(full['ids']))

    def test_gates_get_their_own_posts_and_leaves(self):
        out = builders.add_fence(self.p, self.m, {'op': 'add_fence', 'kind': 'chainlink', 'path': [[-500, -900], [500, -900]],
                                                  'gates': [{'at': 300, 'width': 120, 'kind': 'closed'},
                                                            {'at': 700, 'width': 40, 'kind': 'wicket'}]}, {})
        self.closed(out['ids'])
        leaves = [i for i in out['ids'] if 'materials/metal/hr_metal/hr_metal_corrugated_001.vmat'
                  in self.m.node(i)['meshData']['materials']]
        self.assertEqual(len(leaves), 1)
        xs = sorted({round(c.p[0], 1) for poly in meshops.decode_node(self.m.node(leaves[0])) for c in poly.corners})
        self.assertAlmostEqual(xs[0], -500 + 240 + 1.2 + 1, delta=.2)     # 1 unit off the gate post (2.4 wide)
        self.assertAlmostEqual(xs[-1], -500 + 720 - 1.2 - 1, delta=.2)
        with self.assertRaises(OpError):
            builders.add_fence(self.p, self.m, {'op': 'add_fence', 'path': [[0, 0], [100, 0]],
                                                'gates': [{'at': 50, 'kind': 'swing'}]}, {})

    def test_utility_line(self):
        out = builders.add_utility_line(self.p, self.m, {'op': 'add_utility_line', 'kind': 'concrete', 'cables': 2,
                                                         'poles': [[-1800, 1200], [-1400, 1300], [-1000, 1200]]}, {})
        self.closed(out['ids'])
        self.assertNotEqual(self.m.node(out['ids'][0]).get('physicsType'), 'none')          # poles stop players
        self.assertTrue(all(self.m.node(i).get('physicsType') == 'none' for i in out['ids'][1:]))

    def test_see_through_fence_and_railing_get_a_clip(self):
        for op in ({'op': 'add_fence', 'kind': 'chainlink', 'path': [[-300, 800], [300, 800]]},
                   {'op': 'add_railing', 'path': [[-300, 900, 64], [300, 900, 64]]}):
            out = getattr(builders, op['op'])(self.p, self.m, op, {})
            mats = {m for i in out['ids'] for m in self.m.node(i)['meshData']['materials']}
            self.assertIn(builders.CLIP, mats)

    def test_bank_is_steep_and_reports_low_places(self):
        out = builders.add_bank(self.p, self.m, {'op': 'add_bank', 'path': [[300, -600, 330], [300, 600, 330]],
                                                 'side': 'left', 'angle': 60}, {})
        self.closed(out['ids'])
        low = builders.add_bank(self.p, self.m, {'op': 'add_bank', 'path': [[-1500, -600, 40], [-1500, 600, 40]],
                                                 'side': 'left', 'bottom': 0}, {})
        self.assertIn('climbable', low)
        with self.assertRaises(OpError):
            builders.add_bank(self.p, self.m, {'op': 'add_bank', 'path': [[0, 0], [100, 0]], 'side': 'up'}, {})

    def test_terrain_bank_cut_uncovers_the_face(self):
        """Two levels joined by a gentle slope: the bank face lies under the ground until add_terrain cuts it."""
        def land(cut):
            m = VMap.from_template(SETTINGS.cs2_path, 'empty')
            ops = [{'op': 'plateau', 'polygon': [[200, -1024], [1024, -1024], [1024, 1024], [200, 1024]], 'z': 200,
                    'falloff': 400}]
            if cut:
                ops.append({'op': 'bank', 'path': [[210, -600], [210, 600]], 'side': 'left'})
            terrain.add_terrain(self.p, m, {'name': 'two', 'bounds': [[-1024, -1024], [1024, 1024]], 'cell': 64,
                                            'ops': ops}, {})
            return m, builders.add_bank(self.p, m, {'op': 'add_bank', 'path': [[210, -600], [210, 600]],
                                                    'side': 'left'}, {})
        with self.assertRaisesRegex(OpError, 'cut it first'):
            land(False)                                                  # the 60-degree face meets the gentle slope
        m, cut = land(True)
        self.assertNotIn('buried', cut)
        self.assertNotIn('climbable', cut)
        ground = builders._ground(m, {})
        self.assertGreater(ground(300, 0) - ground(0, 0), 150)          # the upper level is untouched
        self.assertAlmostEqual(ground(300, 1000), 200, delta=1)          # past the path's end: not cut

    def test_a_bank_with_gentle_ground_around_it_is_reported(self):
        def bank(falloff):
            m = VMap.from_template(SETTINGS.cs2_path, 'empty')
            terrain.add_terrain(self.p, m, {'name': 'two', 'bounds': [[-1024, -1024], [1024, 1024]], 'cell': 64, 'ops': [
                {'op': 'plateau', 'polygon': [[200, -1024], [1024, -1024], [1024, 1024], [200, 1024]], 'z': 200,
                 'falloff': falloff}, {'op': 'bank', 'path': [[210, -600], [210, 600]], 'side': 'left'}]}, {})
            builders.add_bank(self.p, m, {'op': 'add_bank', 'path': [[210, -600], [210, 600]], 'side': 'left'}, {})
            return builders.bank_bypass(m)
        found = bank(400)                      # past the ends the old 30-degree slope still leads up
        self.assertEqual(len(found), 1)
        self.assertGreater(abs(found[0]['at'][1]), 500)            # up near an end, not across the middle
        self.assertEqual(bank(100), [])        # a cliff-like edge everywhere: no way round

    def test_rails_and_banks_are_not_reported(self):
        """t11_builders (25.10): low fence rails 'hung over the terrain' and a bank was a 'steep ramp'; both are meant."""
        from cs2map import checks, server, visual
        p = server.project
        name = 'test_builders' + agent_suffix()
        p.map_create(name, 'empty', overwrite=True)
        p.map_edit(name, [
            {'op': 'add_terrain', 'name': 'land', 'bounds': [[-1024, -1024], [1024, 1024]], 'cell': 64,
             'ops': [{'op': 'hill', 'center': [600, 0], 'radius': 700, 'height': 300}]},
            {'op': 'add_fence', 'kind': 'sheet', 'path': [[-900, -600], [-100, -600], [500, -200]]},
            {'op': 'add_fence', 'kind': 'planks', 'path': [[-900, -400], [-300, -400]]},
            {'op': 'add_bank', 'path': [[250, -500, 330], [250, 500, 330]], 'side': 'left'},
            {'op': 'add_entity', 'classname': 'info_player_terrorist',
             'origin': {'terrain': 'land', 'at': [-800, 0], 'z': 'ground', 'offset': 16}}])
        codes = [x['code'] for x in visual.visual_check(p, name)['problems']]
        self.assertNotIn('visual_floats_over_terrain', codes)
        self.assertNotIn('steep_ramp', [x['code'] for x in checks.check_map(p, name)['problems']])

    def test_slab_on_a_block_is_carried_a_free_one_hangs(self):
        """A town map (25.10): garage roofs on walls and a landing on a block 'hung over the terrain'."""
        from cs2map import server, visual
        p = server.project
        name = 'test_carried' + agent_suffix()
        p.map_create(name, 'empty', overwrite=True)
        p.map_edit(name, [
            {'op': 'add_terrain', 'name': 'land', 'bounds': [[-1024, -1024], [1024, 1024]], 'cell': 64, 'ops': []},
            {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [-300, -100, 30], 'maxs': [-100, 100, 36]}},
            {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [100, -100, 30], 'maxs': [300, 100, 36]}},
            {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [80, -100, -8], 'maxs': [160, 100, 30]}},
            {'op': 'add_entity', 'classname': 'info_player_terrorist', 'origin': [-800, 0, 16]}])
        hung = [x for x in visual.visual_check(p, name)['problems'] if x['code'] == 'visual_floats_over_terrain']
        self.assertEqual(len(hung), 1, hung)
        self.assertLess(float(hung[0]['message'].split('near [')[1].split(',')[0]), 0)    # the free slab at x<0

    def test_tree_trunk_solid_branches_render_only(self):
        out = builders.add_tree(self.p, self.m, {'op': 'add_tree', 'origin': [-1000, -1000], 'seed': 3}, {})
        self.closed(out['ids'][:1])
        self.assertEqual(self.m.node(out['ids'][1]).get('physicsType'), 'none')
        self.assertNotEqual(self.m.node(out['ids'][0]).get('physicsType'), 'none')


if __name__ == '__main__':
    unittest.main()
