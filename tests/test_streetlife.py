"""Unit coverage for the S street-life contract and deterministic placement."""
import json
import unittest
from unittest.mock import patch

from cs2map import dressing, layout, streetlife
from cs2map.project import Project, OpError
from cs2map.vmap import VMap


class StreetLife(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        self.map = VMap.from_template(self.project.s.cs2_path, 'empty')
        layout.add_layout(self.project, self.map, {
            'op': 'add_layout', 'name': 'q', 'wall': 16,
            'rooms': [{'id': 'street', 'origin': [0, 0, 0], 'size': [512, 384, 256], 'ceiling': False}],
            'materials': {'floor': 'materials/de_inferno/ground/inferno_stone_floor_01.vmat',
                          'walls': 'materials/de_inferno/plaster/inferno_plaster_07.vmat'}}, {})

    def fake_index(self, project=None):
        return {model: {'crc': 'test', 'phys': True, 'lo': [-12, -12, -1], 'hi': [12, 12, 36]}
                for role in streetlife._load_set('inferno')['roles'].values() for model in role['models']}

    def fake_measure(self, project, paths):
        data = self.fake_index(project)
        return {p: data[p] for p in paths}

    def fake_notes(self):
        return {}

    def test_set_has_roles_and_atlas_sized_models(self):
        data = streetlife._load_set('inferno')
        self.assertEqual(data['schema'], 1)
        self.assertTrue({'door_plant', 'wall_clutter', 'bike', 'bench', 'cafe', 'laundry', 'wires'} <= set(data['roles']))
        models = [m for role in data['roles'].values() for m in role['models']]
        self.assertGreaterEqual(len(set(models)), 40)
        self.assertLessEqual(len(set(models)), 50)
        for role in data['roles'].values():
            self.assertIn('front', role); self.assertIn('mount', role)
            self.assertIn('wall_offset', role); self.assertIn('needs_passage', role)
            self.assertTrue(role['surfaces'])

    def test_density_zero_is_noop_but_records_group(self):
        before = self.map.to_bytes()
        result = streetlife.dress_street(self.project, self.map,
                                         {'op': 'dress_street', 'name': 'empty', 'rooms': 'all',
                                          'style': 'inferno', 'density': 0, 'seed': 5,
                                          'clear_width': 112}, {})
        self.assertEqual(result['placed'], 0)
        self.assertEqual(result['counts']['door_plant'], 0)
        group = self.map.node(result['id'])
        self.assertEqual(group.name, 'street:empty')
        self.assertEqual(json.loads(group['cs2map_streetlife'])['seed'], 5)
        self.assertNotEqual(before, self.map.to_bytes())
        VMap(__import__('cs2map.dmx', fromlist=['loads']).loads(self.map.to_bytes()))

    def test_deterministic_placement_and_metadata(self):
        patches = [patch.object(streetlife.atlas, 'index', side_effect=self.fake_index),
                   patch.object(streetlife.atlas, 'measure', side_effect=self.fake_measure),
                   patch.object(streetlife.atlas, 'notes_all', side_effect=self.fake_notes)]
        for ctx in patches: ctx.start()
        try:
            first = streetlife.dress_street(self.project, self.map,
                                            {'op': 'dress_street', 'name': 'main', 'rooms': 'street',
                                             'style': 'inferno', 'density': 1.0, 'seed': 12,
                                             'clear_width': 112, 'laundry': True, 'wires': True}, {})
            ids = first['id']
            self.assertGreater(first['placed'], 0)
            self.assertGreater(first['counts']['wires'] + first['counts']['laundry'], 0)
            meta = json.loads(self.map.node(ids)['cs2map_streetlife'])
            self.assertEqual(meta['seed'], 12)
            self.assertEqual(len(meta['ids']), first['placed'])
            nodes = list(self.map.iter_nodes(self.map.node(ids)))
            self.assertTrue(all(n.get('cs2map_street_role') for n in nodes if n.type == 'CMapEntity'))
            # A second clean VMAP with the same seed yields the same authored origins.
            other = VMap(__import__('cs2map.dmx', fromlist=['loads']).loads(self.map.to_bytes()))
            # The operation name changes to avoid the duplicate guard; compare the first pass exactly.
            # Removing the existing group also exercises the regular map remove path.
            other.remove(other.node(ids))
            second = streetlife.dress_street(self.project, other,
                                             {'op': 'dress_street', 'name': 'main2', 'rooms': 'street',
                                              'style': 'inferno', 'density': 1.0, 'seed': 12,
                                              'clear_width': 112, 'laundry': True, 'wires': True}, {})
            a = [(n.get('cs2map_street_role'), tuple(round(x, 3) for x in n['origin']))
                 for n in self.map.iter_nodes(self.map.node(ids)) if n.type == 'CMapEntity']
            b = [(n.get('cs2map_street_role'), tuple(round(x, 3) for x in n['origin']))
                 for n in other.iter_nodes(other.node(second['id'])) if n.type == 'CMapEntity']
            self.assertEqual(a, b)
        finally:
            for ctx in reversed(patches): ctx.stop()

    def test_bad_input_does_not_mutate_map(self):
        for field, value in [('density', 1.1), ('seed', True), ('clear_width', 32), ('laundry', 1), ('typo', 1)]:
            before = self.map.to_bytes()
            with self.assertRaises(OpError):
                streetlife.dress_street(self.project, self.map,
                                        {'op': 'dress_street', 'name': 'bad', 'rooms': 'street', field: value}, {})
            self.assertEqual(before, self.map.to_bytes())

    def test_hanging_spans_touch_opposite_walls_and_leave_player_route(self):
        # These are measured asset-axis fixtures: Inferno cloth is long along Y,
        # while the straight electric wire is long along Z in its source file.
        data = self.fake_index()
        cloth = streetlife._load_set('inferno')['roles']['laundry']['models'][0]
        data[cloth] = {'phys': False, 'lo': [-2.05, -128.05, -82.14], 'hi': [1.42, 128.05, .32]}
        for model in streetlife._load_set('inferno')['roles']['wires']['models']:
            data[model] = {'phys': False, 'lo': [-.5, -1, 0], 'hi': [.5, 1, 130]}
        for width in (192, 384):
            with self.subTest(width=width):
                m = VMap.from_template(self.project.s.cs2_path, 'empty')
                layout.add_layout(self.project, m, {'op': 'add_layout', 'name': 'span', 'wall': 16,
                    'rooms': [{'id': 'lane', 'origin': [0, 0, 0], 'size': [800, width, 512], 'ceiling': False}]}, {})
                with patch.object(streetlife.atlas, 'index', return_value=data), \
                     patch.object(streetlife.atlas, 'measure', side_effect=lambda p, paths: {x: data[x] for x in paths}), \
                     patch.object(streetlife.atlas, 'notes_all', return_value={}):
                    result = streetlife.dress_street(self.project, m, {'op': 'dress_street', 'name': 'hanging',
                        'rooms': 'all', 'density': 1, 'seed': 5, 'laundry': True, 'wires': True}, {})
                self.assertEqual(result['counts']['laundry'], 1)
                group = m.node(result['id'])
                meta = json.loads(group[streetlife.STREET_KEY])
                cloth_node = next(n for n in m.iter_nodes(group) if n.get('cs2map_street_role') == 'laundry')
                bound = dressing.transformed([data[cloth]['lo'], data[cloth]['hi']],
                    cloth_node['origin'], cloth_node['angles'], cloth_node['scales'])
                self.assertGreater(bound[0][2], 112)
                self.assertGreaterEqual(cloth_node['scales'][0], .75)
                self.assertLessEqual(cloth_node['scales'][0], 1.25)
                ties = [n for n in m.iter_nodes(group) if n.get('cs2map_street_role') == 'laundry_tie']
                if width == 384:
                    self.assertEqual(len(ties), 2)
                    self.assertEqual(len(meta['support_ids']), 2)
                else:
                    self.assertLessEqual(abs(bound[0][1]), 1)
                    self.assertLessEqual(abs(bound[1][1] - width), 1)
                # Every prop stays above or outside the player clearance boxes.
                clear = streetlife._clear_boxes(streetlife._room_map(m, 'all'), 112)
                for n in m.iter_nodes(group):
                    if n.type != 'CMapEntity' or not n.get('cs2map_street_model'):
                        continue
                    info = data[n['cs2map_street_model']]
                    b = dressing.transformed([info['lo'], info['hi']], n['origin'], n['angles'], n['scales'])
                    self.assertFalse(any(dressing.overlaps(b, corridor) for corridor in clear))

    def test_wall_mounts_require_real_triangle_backing_at_openings(self):
        m = VMap.from_template(self.project.s.cs2_path, 'empty')
        layout.add_layout(self.project, m, {'op': 'add_layout', 'name': 'open', 'wall': 16,
            'rooms': [{'id': 'street', 'origin': [0, 0, 0], 'size': [512, 384, 512], 'ceiling': False}],
            'openings': [{'room': 'street', 'wall': '+x', 'at': .5, 'width': 256, 'height': 256}]}, {})
        data = self.fake_index()
        with patch.object(streetlife.atlas, 'index', return_value=data), \
             patch.object(streetlife.atlas, 'measure', side_effect=lambda p, paths: {x: data[x] for x in paths}), \
             patch.object(streetlife.atlas, 'notes_all', return_value={}):
            result = streetlife.dress_street(self.project, m, {'op': 'dress_street', 'name': 'mounted',
                'rooms': 'all', 'density': 1, 'seed': 5}, {})
        wall_props = [n for n in m.iter_nodes(m.node(result['id']))
                      if n.get('cs2map_street_role') in {'wall_lamp', 'street_sign'}
                      and n.get('cs2map_street_model')]
        self.assertTrue(wall_props)
        # The +x opening is the edge x=512, y=64..320, below z=256.
        for n in wall_props:
            info = data[n['cs2map_street_model']]
            b = dressing.transformed([info['lo'], info['hi']], n['origin'], n['angles'], n['scales'])
            centre = [(b[0][i] + b[1][i]) / 2 for i in range(3)]
            self.assertFalse(centre[0] > 480 and 80 < centre[1] < 304 and centre[2] < 240)


if __name__ == '__main__':
    unittest.main()
