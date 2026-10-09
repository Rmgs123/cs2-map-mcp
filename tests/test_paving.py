"""add_paving: real floors (materials by area, solid sidewalks and steps) and the phantom-floor check."""
import json
import unittest

from cs2map import checks, layout, mesh, paving, visual
from cs2map.project import OpError, Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class Paving(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        self.refs = {}
        layout.add_layout(self.p, self.m, {
            'op': 'add_layout', 'name': 'q', 'wall': 16,
            'rooms': [{'id': 'street', 'size': [1024, 384, 256], 'ceiling': False},
                      {'id': 'alley', 'size': [640, 192, 256], 'from': 'street', 'side': '+y', 'ceiling': False},
                      {'id': 'upper', 'size': [384, 384, 256], 'from': 'street', 'side': '+x', 'ceiling': False,
                       'floor_z': 48}],
            'openings': [{'between': ['street', 'alley'], 'width': 128, 'height': 160},
                         {'between': ['street', 'upper'], 'width': 128, 'height': 160}]}, self.refs)

    def pave(self, **op):
        return paving.add_paving(self.p, self.m, {'op': 'add_paving', 'name': 'p', **op}, self.refs)

    def test_floor_keeps_its_plane_and_gets_area_materials(self):
        r = self.pave()
        self.assertEqual(r['rooms'], {'street': 'street', 'alley': 'alley', 'upper': 'street'})
        group = self.m.node(layout.plans(self.m)[0]['id'])
        tops = {}
        for n in group['children']:
            b = checks._mesh_bounds(n)
            if n.type == 'CMapMesh' and abs(b[1][2]) < 1e-3:
                self.assertNotEqual(n.get('physicsType'), 'none')          # floors stay solid
                tops.update({str(x): True for x in n['meshData']['materials']})
        self.assertIn(paving.STYLES['inferno']['surfaces']['street']['floor'], tops)
        self.assertIn(paving.STYLES['inferno']['surfaces']['alley']['floor'], tops)

    def test_sidewalks_are_solid_steps_cut_at_passages(self):
        r = self.pave()
        self.assertGreater(r['sidewalks'], 0)
        meta = json.loads(self.m.node(r['id'])[paving.PAVING_KEY])
        opening = [h for d in layout.plans(self.m) for h in d['openings'] if h['between'] == ['street', 'alley']][0]
        for s in meta['sidewalks']:
            self.assertAlmostEqual(s['maxs'][2] - s['mins'][2], 7)     # 6 high, sunk 1 into the floor
            if s['room'] == 'street' and s['side'] == '+y':
                ob = opening['bounds']
                self.assertTrue(s['maxs'][0] <= ob['mins'][0] + 1e-3 or s['mins'][0] >= ob['maxs'][0] - 1e-3)
        for n in self.m.iter_nodes(self.m.node(r['id'])):
            if n.type == 'CMapMesh':
                self.assertNotEqual(n.get('physicsType'), 'none')

    def test_steps_join_two_levels(self):
        r = self.pave(rooms=['street', 'upper'])
        self.assertEqual(r['steps'], 1)
        meta = json.loads(self.m.node(r['id'])[paving.PAVING_KEY])
        step = meta['steps'][0]
        self.assertEqual((step['from'], step['to']), ('street', 'upper'))
        self.assertAlmostEqual(step['maxs'][2], 48)
        self.assertLessEqual(48 / step['steps'], 18)

    def test_route_graph_climbs_paved_steps(self):
        from cs2map import route
        key = lambda rid: (layout.plans(self.m)[0]['id'], rid)
        _, edges = route._layout_rooms(self.m)
        self.assertNotIn(key('upper'), [k for k, _ in edges[key('street')]])     # 48 up, no stairs yet
        self.pave(rooms=['street', 'upper'])
        _, edges = route._layout_rooms(self.m)
        self.assertIn(key('upper'), [k for k, _ in edges[key('street')]])

    def test_facade_stands_on_the_sidewalk(self):
        self.pave()
        from cs2map import facades
        r = facades.add_facade(self.p, self.m, {'op': 'add_facade', 'name': 'f', 'on': {'room': 'street', 'side': '-y'},
                                                'floors': 2}, self.refs)
        meta = json.loads(self.m.node(r['id'])['cs2map_facade'])
        self.assertAlmostEqual(meta['z0'], 6)

    def test_no_double_paving_and_bad_input(self):
        self.pave(rooms=['street'])
        with self.assertRaisesRegex(OpError, 'already paved'):
            paving.add_paving(self.p, self.m, {'op': 'add_paving', 'name': 'other', 'rooms': ['street']}, self.refs)
        with self.assertRaises(OpError):
            self.pave(name='x', rooms=['nope'])
        with self.assertRaises(OpError):
            self.pave(name='y', rooms=['alley'], areas={'alley': {'surface': 'lava'}})


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class Phantoms(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        layout.add_layout(self.p, self.m, {'op': 'add_layout', 'name': 'q', 'wall': 16, 'rooms': [
            {'id': 'street', 'size': [1024, 384, 256], 'ceiling': False}]}, {})

    def add(self, lo, hi, render_only=True):
        node = self.m.new_mesh(mesh.box(lo, hi, 'materials/dev/dev_measuregeneric01.vmat'),
                               options={'physicsType': 'none'} if render_only else None)
        self.m.attach(node)
        return node['nodeID']

    def codes(self):
        return [(p['code'], p['id']) for p in visual._phantom_checks(self.m, visual._solid_meshes(self.m))]

    def test_raised_render_only_strip_is_a_phantom_floor(self):
        nid = self.add([100, 100, 1], [600, 300, 9])
        self.assertEqual(self.codes(), [('visual_phantom_floor', nid)])

    def test_flush_or_solid_or_high_slabs_are_fine(self):
        self.add([100, 100, -2], [600, 300, 1])           # within 2 units of the floor (decal-like)
        self.add([100, 100, 0], [600, 300, 8], render_only=False)
        self.add([100, 100, 200], [600, 300, 206])        # a canopy above head height
        self.assertEqual(self.codes(), [])

    def test_free_standing_render_only_wall_is_a_phantom_wall(self):
        nid = self.add([400, 150, 0], [600, 160, 120])
        self.assertEqual(self.codes(), [('visual_phantom_wall', nid)])

    def test_skin_on_a_wall_is_fine(self):
        self.add([0, 0.25, 0], [1024, 1.25, 200])          # plaster skin on the -y wall
        self.assertEqual(self.codes(), [])


if __name__ == '__main__':
    unittest.main()
