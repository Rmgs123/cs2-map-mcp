"""Unit coverage for the competitive extension (no CS2 lock required)."""
import json
import unittest
from pathlib import Path
from unittest import mock

from cs2map import competitive, layout
from cs2map.project import OpError, Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


class CompetitivePure(unittest.TestCase):
    def test_spawn_grid_keeps_player_spacing(self):
        room = {"id": "t", "floor_z": 0, "interior": {"mins": [0, 0, 0], "maxs": [640, 640, 256]}}
        points = competitive._spawn_grid([320, 160, 8], room, 10, "T spawn")
        self.assertEqual(len(points), 10)
        self.assertGreaterEqual(min(__import__('math').dist(a, b) for i, a in enumerate(points) for b in points[i + 1:]), 48)
        self.assertTrue(all(32 <= p[0] <= 608 and 32 <= p[1] <= 608 for p in points))

    def test_timing_hint_has_three_to_eight_second_contract(self):
        rows = [
            {"team": "T", "objective": "A", "seconds": 20, "reachable": True},
            {"team": "CT", "objective": "A", "seconds": 15, "reachable": True},
        ]
        self.assertIn("within", competitive._suggest(rows, "A"))

    def test_grid_in_long_y_room(self):
        room = {"floor_z": 0, "interior": {"mins": [0, 0, 0], "maxs": [192, 640, 256]}}
        points = competitive._spawn_grid([96, 160, 8], room, 10, "T")
        self.assertTrue(all(32 <= p[0] <= 160 and 32 <= p[1] <= 608 for p in points))

    def test_physical_seconds_exclude_setup_and_failure_has_no_time(self):
        details = json.dumps({'samples': [{'seconds': 12, 'pos': [0,0,0]},
                             {'seconds': 17, 'pos': [1000,0,0]}], 'completed': [{'seconds': 17}]})
        with mock.patch('cs2map.competitive.Path.read_text', return_value=details):
            result = competitive._physical_measurement({'ok': True, 'seconds': 30, 'log': 'unused.json'})
            self.assertEqual(result['seconds'], 5)
            self.assertEqual(result['distance_m'], 25.4)
            self.assertIsNone(competitive._physical_measurement({'ok': False})['seconds'])

    def test_invalid_mode_is_refused_before_map_write(self):
        with self.assertRaises(OpError):
            competitive.add_comp_plan(Project(), VMap.from_template(SETTINGS.cs2_path, "empty"),
                                      {"op": "add_comp_plan", "name": "bad", "mode": "deathmatch", "layout": {"rooms": []}}, {})

    def test_round_clock_values_are_parsed_or_refused(self):
        values = competitive._cvar_values(['"mp_roundtime" = "5.0"', 'mp_ignore_round_win_conditions false'],
                                          ['mp_roundtime','mp_ignore_round_win_conditions'])
        self.assertEqual(values, {'mp_roundtime': '5.0', 'mp_ignore_round_win_conditions': '0'})
        with self.assertRaises(OpError):
            competitive._cvar_values(['Unknown command: mp_roundtime'], ['mp_roundtime'])

    def test_round_clock_restored_on_runner_failure(self):
        project = mock.Mock()
        lines = [f'{name} = {value}' for name, value in (
            ('mp_roundtime', 5), ('mp_roundtime_defuse', 2), ('mp_roundtime_hostage', 3), ('mp_ignore_round_win_conditions', 1))]
        with mock.patch('cs2map.competitive.game.exclusive') as lock, \
             mock.patch('cs2map.competitive.game.ensure_map'), \
             mock.patch('cs2map.competitive.game.query', return_value=lines), \
             mock.patch('cs2map.competitive.game.command') as command, \
             mock.patch('cs2map.competitive.playtest.playtest_run', side_effect=RuntimeError('probe failed')):
            with self.assertRaisesRegex(RuntimeError, 'probe failed'):
                competitive.comp_playtest(project, 'c_test', round_seconds=30)
            restored = command.call_args.args[1]
            self.assertIn('mp_roundtime_defuse 2', restored)
            self.assertIn('mp_ignore_round_win_conditions 1', restored)


@unittest.skipUnless(HAVE_CS2, "CS2 templates needed")
class CompetitiveAuthoring(unittest.TestCase):
    def test_volume_detects_corridor_between_yards_with_safe_spawn_points(self):
        root = Path(__file__).resolve().parents[1]
        fixture = root / '.local/scratch/F3/port_replay_v2.json'
        if not fixture.exists():
            self.skipTest('F3 replay fixture unavailable')
        p = Project()
        for version, exposed in [('v2',True),('v3',False)]:
            ops = json.loads((fixture.parent/f'port_replay_{version}.json').read_text(encoding='utf-8'))['operations']
            op = next(x for x in ops if x['op']=='add_comp_plan')
            m = VMap.from_template(SETTINGS.cs2_path,'empty')
            competitive.add_comp_plan(p,m,op,{})
            codes = {x['code'] for x in competitive.check_competitive(p,m)}
            self.assertNotIn('comp_spawn_line_of_sight',codes)
            self.assertEqual('comp_spawn_area_line_of_sight' in codes,exposed)

    def test_default_dogleg_protects_whole_spawn_yards(self):
        p, m = Project(), VMap.from_template(SETTINGS.cs2_path, 'empty')
        competitive.add_comp_plan(p,m,{'op':'add_comp_plan','name':'unit_dogleg'}, {})
        self.assertNotIn('comp_spawn_area_line_of_sight',{x['code'] for x in competitive.check_competitive(p,m)})

    def test_add_comp_plan_creates_required_entities_and_metadata(self):
        p = Project()
        m = VMap.from_template(SETTINGS.cs2_path, "empty")
        op = {
            "op": "add_comp_plan", "name": "unit_comp", "mode": "defuse",
            "layout": {"name": "unit_comp_layout", "wall": 16,
                "rooms": [{"id": "t", "size": [640, 640, 256]},
                          {"id": "mid", "size": [960, 512, 256], "from": "t", "side": "+x"},
                          {"id": "ct", "size": [640, 640, 256], "from": "mid", "side": "+x"},
                          {"id": "a", "size": [512, 512, 256], "from": "mid", "side": "+y"},
                          {"id": "b", "size": [512, 512, 256], "from": "mid", "side": "-y"}],
                "openings": [{"between": ["t", "mid"], "width": 128, "height": 128},
                             {"between": ["mid", "ct"], "width": 128, "height": 128},
                             {"between": ["mid", "a"], "width": 128, "height": 128},
                             {"between": ["mid", "b"], "width": 128, "height": 128}]}}
        out = competitive.add_comp_plan(p, m, op, {})
        self.assertEqual(out["spawns"], {"t": 10, "ct": 10})
        self.assertEqual(len(m.entities(classname="func_bomb_target")), 2)
        self.assertEqual(len(m.entities(classname="func_buyzone")), 2)
        self.assertEqual(len(competitive._competitive_meta(m)), 1)
        warnings = competitive.check_competitive(p, m)
        self.assertEqual([x['code'] for x in warnings],
                         ['comp_spawn_area_line_of_sight', 'comp_site_exposed', 'comp_site_exposed'])
        meta = competitive._competitive_meta(m)[0][1]
        self.assertEqual([s['room'] for s in meta['sites']], ['a', 'b'])

    def test_all_modes_and_deferred_relative_coordinates(self):
        for mode, bombsites, hostages in [('defuse', 2, 0), ('wingman', 1, 0), ('hostage', 0, 1)]:
            with self.subTest(mode=mode):
                p = Project()
                p.register_preprocessor(competitive.preprocess)
                p.register_preprocessor(layout.preprocess)
                p.register_op('add_comp_plan', competitive.add_comp_plan)
                m = VMap.from_template(SETTINGS.cs2_path, 'empty')
                out = p._apply(m, {'op': 'add_comp_plan', 'name': 'unit_' + mode, 'mode': mode,
                                  't_spawn': {'room': 't', 'at': [.5,.25]},
                                  'ct_spawn': {'room': 'ct', 'at': [.5,.75]}}, {})
                self.assertEqual(out['spawns'], {'t': 10, 'ct': 10})
                self.assertEqual(len(m.entities(classname='func_bomb_target')), bombsites)
                self.assertEqual(len(m.entities(classname='hostage_entity')), hostages)

    def test_checks_use_moved_spawns_and_reject_wall_spacing_los(self):
        p, m = Project(), VMap.from_template(SETTINGS.cs2_path, 'empty')
        competitive.add_comp_plan(p, m, {'op': 'add_comp_plan', 'name': 'unit_mutation'}, {})
        t = m.entities(classname='info_player_terrorist')
        t[0].set('origin', 'vector3', [-8,320,8])
        t[1].set('origin', 'vector3', [-8,320,8])
        codes = {x['code'] for x in competitive.check_competitive(p, m)}
        self.assertIn('comp_spawn_in_solid', codes)
        self.assertIn('comp_spawn_spacing', codes)
        t[0].set('origin', 'vector3', [1400,320,8])
        m.entities(classname='info_player_counterterrorist')[0].set('origin', 'vector3', [1850,320,8])
        self.assertIn('comp_spawn_line_of_sight', {x['code'] for x in competitive.check_competitive(p, m)})

    def test_sampled_bot_movement_deaths_and_real_round_turnover(self):
        p, m = Project(), VMap.from_template(SETTINGS.cs2_path, 'empty')
        competitive.add_comp_plan(p, m, {'op': 'add_comp_plan', 'name': 'unit_bots'}, {})
        def sample(round_number, x, alive=True):
            return {'round': round_number, 'elapsed': x/10, 'players': [
                {'team': 2, 'slot': 1, 'bot': True, 'alive': alive, 'pos': {'x': x,'y': 160,'z': 0}}]}
        out = competitive._bot_observation(m, [sample(0,100),sample(1,100),sample(1,200),sample(1,200,False),sample(2,100)])
        self.assertEqual(out['moving_bots_by_team']['2'], 1)
        self.assertEqual(out['rounds_completed'], 1)
        self.assertEqual(out['death_location_count'], 1)

    def test_graph_rejects_blocked_door_even_with_metadata_edge(self):
        p, m = Project(), VMap.from_template(SETTINGS.cs2_path, 'empty')
        competitive.add_comp_plan(p, m, {'op': 'add_comp_plan', 'name': 'unit_blocked'}, {})
        p._apply(m, {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [632,56,0],
                      'maxs': [664,264,180]}, 'material': 'materials/concrete/concrete_floor_04.vmat'}, {})
        self.assertIn('comp_no_path', {x['code'] for x in competitive.check_competitive(p, m)})

    def test_removed_objective_is_error_not_silent_pass(self):
        p, m = Project(), VMap.from_template(SETTINGS.cs2_path, 'empty')
        competitive.add_comp_plan(p, m, {'op': 'add_comp_plan', 'name': 'unit_removed'}, {})
        entity = m.entities(classname='func_bomb_target')[0]
        entity['entity_properties'].set('targetname', 'string', 'renamed')
        self.assertEqual(competitive.check_competitive(p, m)[0]['code'], 'comp_anchor_missing')

    def test_world_spawn_anchor_is_not_clamped_to_default_spawn_room(self):
        p, m = Project(), VMap.from_template(SETTINGS.cs2_path, 'empty')
        competitive.add_comp_plan(p, m, {'op': 'add_comp_plan', 'name': 'unit_world',
                                       't_spawn': [800,200,8]}, {})
        points = competitive._competitive_meta(m)[0][1]['t_spawn']
        self.assertAlmostEqual(sum(p[0] for p in points)/len(points), 800)
        self.assertAlmostEqual(sum(p[1] for p in points)/len(points), 200)


if __name__ == "__main__":
    unittest.main()
