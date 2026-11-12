"""Stand-in players for readability frames (figures.py): the operation, its views and the check_map reminder."""
import json
import math
import unittest

from cs2map import figures
from cs2map.project import OpError, Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


class Views(unittest.TestCase):
    """The pure part: who looks at whom."""

    def fig(self, team, x, y, z=0, yaw=0):
        return {'team': team, 'origin': [float(x), float(y), float(z)], 'yaw': float(yaw)}

    def test_every_figure_is_seen_by_its_nearest_opponent(self):
        figs = [self.fig('t', 0, 0), self.fig('ct', 1000, 0), self.fig('ct', 0, 400)]
        views = figures.views(figs)
        self.assertEqual([v['name'] for v in views], ['ct3_sees_t1', 't1_sees_ct2', 't1_sees_ct3'])
        # t1 looks along +X at ct2, from just in front of itself and level with the chest (eye 64 over a chest at 48)
        t1_at_ct2 = views[1]
        self.assertEqual(t1_at_ct2['pos'], [figures.OUT_OF_FIGURE, 0.0, 0.0])
        self.assertEqual(t1_at_ct2['ang'][1], 0.0)
        self.assertGreater(t1_at_ct2['ang'][0], 0)                  # pitch down: positive in CS2
        self.assertLess(t1_at_ct2['ang'][0], 2)
        self.assertEqual(views[0]['ang'][1], -90.0)                 # ct3 at y 400 looks along -Y at t1

    def test_a_higher_target_is_looked_up_at(self):
        views = figures.views([self.fig('t', 0, 0), self.fig('ct', 500, 0, z=300)])
        self.assertLess(views[1]['ang'][0], -25)                    # t1 looks up at ct2
        self.assertGreater(views[0]['ang'][0], 25)                  # ct2 looks down at t1

    def test_one_team_alone_is_seen_from_in_front(self):
        views = figures.views([self.fig('t', 100, 100, yaw=90)])
        self.assertEqual(views[0]['name'], 'front_of_t1')
        self.assertEqual(views[0]['pos'], [100.0, 100.0 + figures.ALONE_VIEW, 0.0])
        self.assertEqual(views[0]['ang'][1], -90.0)

    def test_close_opponents_keep_the_camera_out_of_the_target(self):
        views = figures.views([self.fig('t', 0, 0), self.fig('ct', 60, 0)])
        self.assertEqual(views[1]['pos'], [0.0, 0.0, 0.0])          # too close to step forward


@unittest.skipUnless(HAVE_CS2, 'needs the game files (stock agent models, FGD)')
class Operation(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(SETTINGS.cs2_path, 'empty')

    def add(self, **fields):
        return figures.add_figures(self.p, self.m, {'op': 'add_figures', **fields}, {})

    def test_figures_are_non_solid_props_in_one_group(self):
        out = self.add(figures=[{'team': 't', 'origin': [0, 0, 0]}, {'team': 'ct', 'origin': [800, 0, 0], 'yaw': 180}])
        self.assertEqual(out['figures'], 2)
        self.assertEqual(len(out['views']), 2)
        props = self.m.entities(classname='prop_dynamic')
        self.assertEqual(len(props), 2)
        kv = [e['entity_properties'] for e in props]
        self.assertEqual({k['model'] for k in kv}, set(figures.MODELS.values()))
        self.assertEqual({str(k['solid']) for k in kv}, {'0'})
        self.assertEqual(sorted(k['targetname'] for k in kv), ['readability_ct2', 'readability_t1'])
        self.assertTrue(math.isclose(float(props[1]['angles'][1]), 180.0))
        group = self.m.parent_of(props[0])
        self.assertEqual(group['nodeID'], out['id'])
        self.assertEqual(json.loads(group.get(figures.KEY)), {'name': 'readability', 'count': 2})

    def test_the_same_name_replaces_and_another_name_adds(self):
        self.add(figures=[{'team': 't', 'origin': [0, 0, 0]}])
        again = self.add(figures=[{'team': 't', 'origin': [64, 0, 0]}, {'team': 'ct', 'origin': [640, 0, 0]}])
        self.assertEqual(again['replaced'], 1)
        self.assertEqual(len(self.m.entities(classname='prop_dynamic')), 2)
        self.add(name='site_b', figures=[{'team': 'ct', 'origin': [0, 640, 0]}])
        self.assertEqual(len(self.m.entities(classname='prop_dynamic')), 3)

    def test_check_map_reminds_to_remove_them(self):
        self.assertEqual(figures.check_figures(self.p, self.m), [])
        out = self.add(figures=[{'team': 't', 'origin': [0, 0, 0]}])
        found = figures.check_figures(self.p, self.m)
        self.assertEqual([(f['code'], f['severity'], f['id']) for f in found],
                         [('figures_in_map', 'warning', out['id'])])
        self.assertIn(f'"ids":[{out["id"]}]', found[0]['fix'])

    def test_bad_input_is_refused_with_the_field_named(self):
        for fields, word in [
            ({'figures': []}, 'figures'),
            ({'figures': [{'team': 'spectator', 'origin': [0, 0, 0]}]}, 'team'),
            ({'figures': [{'team': 't', 'origin': [0, 0]}]}, 'origin'),
            ({'figures': [{'team': 't', 'origin': [0, 0, 0], 'yaw': 'north'}]}, 'yaw'),
            ({'figures': [{'team': 't', 'origin': [0, 0, 0], 'model': 'models/none/none.vmdl'}]}, 'model'),
            ({'figures': [{'team': 't', 'origin': [0, 0, 0], 'pose': 'crouch'}]}, 'figures[0]'),
            ({'figures': [{'team': 't', 'origin': [0, 0, 0]}], 'views': True}, 'views'),
            ({'figures': [{'team': 't', 'origin': [0, 0, 0]}], 'name': 'Site B'}, 'name'),
        ]:
            with self.subTest(fields=fields), self.assertRaises(OpError) as ctx:
                self.add(**fields)
            self.assertIn(word, str(ctx.exception))
        self.assertEqual(self.m.entities(classname='prop_dynamic'), [])


if __name__ == '__main__':
    unittest.main()
