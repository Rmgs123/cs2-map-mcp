"""Defects the p10_showcase acceptance map found in the tools (fixed for every map, not in one map)."""
import unittest

from cs2map import checks, facades, layout, mesh, visual
from cs2map.project import Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS, agent_suffix

MAP = 'test_showcase_fixes' + agent_suffix()
MARKER = 'models/mcp/a10_marker/a10_marker.vmdl'


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class ShowcaseFixes(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(SETTINGS.cs2_path, 'empty')

    def test_parked_hidden_helpers_are_not_scene_props(self):
        """Disguise props and glow markers wait at a spawn, invisible and non-solid: no spawn or overlap warnings."""
        self.assertTrue(checks.hidden_helper({'solid': '0', 'renderamt': '0'}))
        self.assertFalse(checks.hidden_helper({'solid': '0'}))
        helper = {'op': 'add_entity', 'classname': 'prop_dynamic', 'origin': [120, 120, 8],
                  'keyvalues': {'model': MARKER, 'solid': 0, 'renderamt': 0}}
        self.p.map_create(MAP, 'basic', overwrite=True)
        self.p.map_edit(MAP, [{'op': 'add_entity', 'classname': 'info_player_counterterrorist',
                               'origin': [120, 120, 8]}, helper, helper])
        codes = [x['code'] for x in checks.check_map(self.p, MAP)['problems']]
        self.assertNotIn('prop_over_spawn', codes)
        self.assertNotIn('visual_prop_overlap', codes)
        self.assertEqual(visual._prop_entries(self.p, self.p.load(MAP)), [])

    def test_coplanar_faces_inside_a_third_body_do_not_flicker(self):
        """A post through a sidewalk: both bottoms at z -1 sit inside the floor, where nobody sees them."""
        slab = self.m.new_mesh(mesh.box([-128, -40, -1], [128, 40, 6], material='materials/dev/reflectivity_30.vmat'))
        post = self.m.new_mesh(mesh.box([-8, -8, -1], [8, 8, 110], material='materials/dev/reflectivity_70.vmat'))
        self.m.attach(slab)
        self.m.attach(post)
        self.assertTrue(visual._zfight_checks(self.m, visual._visible_meshes(self.m)))    # in the open they flicker
        self.m.attach(self.m.new_mesh(mesh.box([-256, -256, -16], [256, 256, 0])))
        self.assertEqual(visual._zfight_checks(self.m, visual._visible_meshes(self.m)), [])

    def test_bevels_are_not_ramps_but_a_steep_ramp_is(self):
        base = [{'op': 'add_entity', 'classname': 'info_player_terrorist', 'origin': [0, 0, 8]},
                {'op': 'add_mesh', 'ref': 'seat', 'shape': {'shape': 'box', 'mins': [100, 0, 17], 'maxs': [196, 24, 21]}},
                {'op': 'edit_mesh', 'id': '@seat', 'steps': [{'do': 'bevel', 'edges': 'top', 'width': 1.5, 'segments': 2}]}]
        from cs2map import server                      # the project with every extension's operations
        p = server.project
        p.map_create(MAP, 'basic', overwrite=True)
        p.map_edit(MAP, base)
        self.assertNotIn('steep_ramp', [x['code'] for x in checks.check_map(p, MAP)['problems']])
        p.map_edit(MAP, [{'op': 'add_mesh', 'shape': {'shape': 'wedge', 'mins': [-300, -64, 0],
                                                       'maxs': [-200, 64, 160], 'rise': '+x'}}])
        self.assertIn('steep_ramp', [x['code'] for x in checks.check_map(p, MAP)['problems']])

    def test_a_parked_car_is_not_furniture_blocking_the_lane(self):
        refs = {}
        layout.add_layout(self.p, self.m, {'op': 'add_layout', 'name': 'st', 'wall': 16, 'rooms': [
            {'id': 'lane', 'size': [448, 1280, 320], 'ceiling': False}]}, refs)
        room = layout._room_ref(self.m, {'room': 'lane'})['interior']
        centre = [(room['mins'][i] + room['maxs'][i]) / 2 for i in range(2)] + [12]
        self.m.attach(self.m.new_entity('prop_physics_override', {'model': 'models/mcp_v/car/car.vmdl'}, centre, (0, 90, 0)))
        codes = [x['code'] for x in visual.visual_check_loaded(self.p, self.m)]
        self.assertNotIn('visual_layout_obstruction', codes)

    def test_two_mechanics_on_one_key_are_reported(self):
        from unittest.mock import patch
        from cs2map import abilities
        ents = [self.m.new_entity('point_script', {'cs_script': f'maps/scripts/mcp/x{i}.vjs'}, (0, 0, 0)) for i in range(2)]
        for e in ents:
            self.m.attach(e)
        configs = {id(ents[0]): ({'name': 'moves', 'abilities': {'dash': {'key': 'inspect'}}}, '// CS2MAP_ABILITY {}'),
                   id(ents[1]): ({'name': 'hs', 'disguise': {'models': ['a'], 'key': 'inspect'}}, '// CS2MAP_HIDESEEK {}')}
        with patch.object(abilities, '_script_config', lambda project, e: configs[id(e)]):
            found = abilities.check_keys(self.p, self.m)
        self.assertEqual([x['code'] for x in found], ['input_key_conflict'])
        configs[id(ents[1])][0]['disguise']['key'] = 'reload'
        with patch.object(abilities, '_script_config', lambda project, e: configs[id(e)]):
            self.assertEqual(abilities.check_keys(self.p, self.m), [])

    def test_window_hoods_clear_the_eave_with_a_low_floor_height(self):
        for seed in (40, 41, 42, 43):
            m = VMap.from_template(SETTINGS.cs2_path, 'empty')
            refs = {}
            layout.add_layout(self.p, m, {'op': 'add_layout', 'name': 'st', 'wall': 16, 'rooms': [
                {'id': 'street', 'size': [1408, 448, 448], 'ceiling': False}]}, refs)
            facades.add_facade(self.p, m, {'op': 'add_facade', 'name': 'f', 'seed': seed, 'floors': [2, 3],
                                           'on': {'room': 'street', 'side': '+y'}, 'floor_height': 136,
                                           'house_width': 288}, refs)
            bad = [x['message'] for x in visual.visual_check_loaded(self.p, m)
                   if x['code'] == 'visual_prop_intersection' and 'window_overhang' in x['message']]
            self.assertEqual(bad, [], f'seed {seed}')


if __name__ == '__main__':
    unittest.main()
