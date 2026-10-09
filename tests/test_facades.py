"""add_facade: house fronts on an existing layout wall, no collision in the street, openings kept clear."""
import unittest

from cs2map import checks, facades, layout, visual
from cs2map.project import OpError, Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class Facades(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        self.refs = {}
        layout.add_layout(self.p, self.m, {'op': 'add_layout', 'name': 'st', 'wall': 16,
                               'rooms': [{'id': 'street', 'size': [1024, 320, 224], 'ceiling': False},
                                         {'id': 'yard', 'size': [384, 384, 224], 'from': 'street', 'side': '+y',
                                          'ceiling': False},
                                         {'id': 'hall', 'size': [256, 256, 224], 'from': 'street', 'side': '-y'}],
                               'openings': [{'between': ['street', 'yard'], 'width': 128, 'height': 160}]}, self.refs)

    def add(self, **op):
        return facades.add_facade(self.p, self.m, {'op': 'add_facade', **op}, self.refs)

    def test_fronts_raise_the_wall_and_keep_the_street_and_opening_clear(self):
        r = self.add(name='north', on={'room': 'street', 'side': '+y'}, floors=3, house_width=256)
        self.assertEqual((r['houses'], r['raised_wall'], r['kept_clear']), (4, True, 1))
        room = layout._room_ref(self.m, {'room': 'street'})
        lo, hi = room['interior']['mins'], room['interior']['maxs']
        opening = [h for d in layout.plans(self.m) for h in d['openings']][0]['bounds']
        row = self.m.node(r['id'])
        for node in self.m.iter_nodes(row):
            if node.type == 'CMapMesh':
                b = checks._mesh_bounds(node)
                # Collision stays behind the face, except roof eaves high above the street.
                if node.get('physicsType') != 'none' and b[0][1] < hi[1] - 1e-3:
                    self.assertGreaterEqual(b[0][2], lo[2] + 3 * 144 - 40)
            elif node.get('entity_properties'):
                props = node['entity_properties']
                self.assertEqual(props.get('solid'), '0')
                o = node['origin']
                in_opening = (opening['mins'][0] - 8 < o[0] < opening['maxs'][0] + 8 and o[2] < opening['maxs'][2])
                self.assertFalse(in_opening, f'{props.get("model")} placed in the opening at {o}')

    def test_no_z_fighting_or_floating_parts(self):
        self.add(name='south', on={'room': 'street', 'side': '-y'}, floors=2)
        visible = visual._visible_meshes(self.m)
        self.assertEqual(visual._zfight_checks(self.m, visible), [])

    def test_shared_wall_dressed_on_both_sides_is_raised_once(self):
        self.add(name='front', on={'room': 'street', 'side': '+y', 'from': 0, 'to': 0.3}, floors=3)
        r = self.add(name='back', on={'room': 'yard', 'side': '-y'}, floors=3)
        raised = __import__('json').loads(self.m.node(r['id'])['cs2map_facade'])['raised']
        found = visual._zfight_checks(self.m, visual._visible_meshes(self.m))
        self.assertEqual(found, [], found[:2])
        self.assertLessEqual(len(raised), 1)          # the stretch raised by the front row is skipped

    def test_free_space_behind_makes_real_houses_with_niches_and_roofs(self):
        r = self.add(name='deep', on={'room': 'street', 'side': '-y', 'from': 0.66, 'to': 1}, floors=[2, 4],
                     house_width=170, seed=5)
        self.assertGreaterEqual(r['deep'], 2)
        meta = __import__('json').loads(self.m.node(r['id'])['cs2map_facade'])
        deep = [h for h in meta['houses'] if h['depth']]
        self.assertTrue(all(h['depth'] >= facades.MIN_DEPTH for h in deep))
        room = layout._room_ref(self.m, {'room': 'street'})
        lo = room['interior']['mins']
        mats = {str(x) for n in self.m.iter_nodes(self.m.node(r['id'])) if n.type == 'CMapMesh'
                for x in n['meshData'].get('materials', [])}
        self.assertIn(facades.ROOF_MAT, mats)
        # The layout wall behind deep houses is gone: nothing solid of the layout is left in front of a niche.
        for n in self.m.iter_nodes(self.m.node(layout.plans(self.m)[0]['id'])):
            b = checks._mesh_bounds(n) if n.type == 'CMapMesh' else None
            if b and b[1][2] > lo[2] + 1 and b[1][1] <= lo[1] + 1e-3 and b[0][0] >= deep[0]['from'] + 1                     and b[1][0] <= deep[-1]['to'] - 1:
                self.fail(f'layout wall piece left behind a deep house: {b}')
        found = visual._zfight_checks(self.m, visual._visible_meshes(self.m))
        self.assertEqual(found, [], found[:2])
        self.assertEqual([p for p in visual._phantom_checks(self.m, visual._solid_meshes(self.m))], [])

    def test_arcade_and_loggia_are_open_galleries(self):
        r = self.add(name='gal', on={'room': 'street', 'side': '-y', 'from': 0.62, 'to': 1}, floors=3,
                     house_width=190, seed=2, arcade=True, loggia=True)
        self.assertGreaterEqual(r['arcades'], 1)
        self.assertGreaterEqual(r['loggias'], 1)
        meta = __import__('json').loads(self.m.node(r['id'])['cs2map_facade'])
        house = next(h for h in meta['houses'] if h['arcade'])
        room = layout._room_ref(self.m, {'room': 'street'})
        lo = room['interior']['mins']
        # A player walks into the arcade: nothing solid at chest height just behind the face between the piers.
        solids = visual._solid_meshes(self.m)
        mid_u = (house['from'] + house['to']) / 2
        probe = ([mid_u - 16, lo[1] - 60, lo[2] + 40], [mid_u + 16, lo[1] - 30, lo[2] + 70])
        blocked = [n for n, b, _ in solids if all(b[0][i] < probe[1][i] and b[1][i] > probe[0][i] for i in range(3))]
        self.assertEqual(blocked, [], 'arcade interior is blocked')
        found = visual._zfight_checks(self.m, visual._visible_meshes(self.m))
        self.assertEqual(found, [], found[:2])
        self.assertEqual(visual._phantom_checks(self.m, solids), [])

    def test_bad_input(self):
        with self.assertRaises(OpError):
            self.add(name='x', on={'room': 'street', 'side': 'up'})
        with self.assertRaises(OpError):
            self.add(name='x', on={'room': 'nope', 'side': '+y'})
        with self.assertRaisesRegex(OpError, 'ceiling'):
            self.add(name='x', on={'room': 'hall', 'side': '+x'}, floors=3)
        self.add(name='dup', on={'room': 'street', 'side': '+y'})
        with self.assertRaises(OpError):
            self.add(name='dup', on={'room': 'street', 'side': '-y'})


if __name__ == '__main__':
    unittest.main()
