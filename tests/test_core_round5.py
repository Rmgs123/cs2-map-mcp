"""Core changes of the round-5 integration: visible ladders, spawn on the floor plane, stairs in the
layout graph, hostage maps, opening hints and views that re-check the camera after `cmd`."""
import unittest
from unittest import mock

from cs2map import checks, game, layout, physics
from cs2map.project import OpError, Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2
from tests.test_core_round3 import FakeGame


@unittest.skipUnless(HAVE_CS2, 'Valve FGD, template and stock ladder models required')
class Ladders(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(self.p.s.cs2_path, 'basic')
        self.refs = {}
        self.p._apply(self.m, {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [512, -256, 0], 'maxs': [528, 256, 320]}},
                      self.refs)

    def ladder(self, **op):
        return physics.add_ladder(self.p, self.m, {'op': 'add_ladder', **op}, self.refs)

    def props(self):
        return [e for e in self.m.entities() if 'metal_ladder_001' in str(e['entity_properties'].get('model'))]

    def test_wall_is_found_and_modules_cover_the_height(self):
        r = self.ladder(mins=[496, -16, 0], maxs=[512, 16, 240])
        self.assertEqual(r['visual']['wall'], '+x')
        self.assertEqual(r['visual']['height'], 240)          # 128 + 64 + 32 + 16
        props = self.props()
        self.assertEqual(len(props), 4)
        for e in props:
            self.assertEqual(str(e['entity_properties']['solid']), '0')   # the surface is climbed, not the model
            self.assertAlmostEqual(e['origin'][0], 508.5)                 # 3.5 in front of the wall face
            self.assertAlmostEqual(e['angles'][1], 180)                   # rungs face the climber (-x)
        surface = self.m.node(r['id'])
        self.assertTrue(any('toolsinvisibleladder' in str(x) for x in surface['meshData']['materials']))

    def test_free_standing_none_and_bad_input(self):
        r = self.ladder(mins=[0, 0, 0], maxs=[16, 32, 128])
        self.assertIn('stands free', r['note'])
        self.assertAlmostEqual(self.props()[0]['origin'][0], 8)
        r = self.ladder(mins=[100, 0, 0], maxs=[116, 32, 128], visual='none')
        self.assertNotIn('visual', r)
        self.assertEqual(len(self.props()), 1)
        r = self.ladder(mins=[200, 0, 0], maxs=[240, 32, 128], wall='-x')
        self.assertIn('warning', r)                           # 40 deep: the player stops far from the rungs
        for bad in ({'visual': 'gold'}, {'wall': 'up'}):
            with self.subTest(bad=bad), self.assertRaises(OpError):
                self.ladder(mins=[0, 0, 0], maxs=[16, 32, 128], **bad)

    def test_rails_inside_the_volume_are_not_a_wall(self):
        # mcp_story/v_warehouse: the author's metal strips straddle the ladder volume face.
        self.p._apply(self.m, {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [-8, 22, 0], 'maxs': [-2, 28, 300]}}, self.refs)
        self.p._apply(self.m, {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [-32, -64, 0], 'maxs': [-16, 64, 300]}}, self.refs)
        lo, hi = [-16, -24, 0], [0, 24, 300]
        self.assertEqual(physics._ladder_wall(self.m, lo, hi)[0], '-x')


@unittest.skipUnless(HAVE_CS2, 'Valve FGD and template required')
class MapChecks(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(self.p.s.cs2_path, 'basic')
        self.refs = {}
        self.p._apply(self.m, {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [-512, -512, -16], 'maxs': [512, 512, 0]}},
                      self.refs)

    def codes(self):
        with mock.patch.object(self.p, 'load', return_value=self.m):
            return [x['code'] for x in checks.check_map(self.p, 'unused')['problems']]

    def test_spawn_on_the_floor_plane_is_an_error(self):
        self.p._apply(self.m, {'op': 'add_entity', 'classname': 'info_player_terrorist', 'origin': [0, 0, 0]}, self.refs)
        codes = self.codes()
        self.assertIn('spawn_in_floor', codes)
        self.assertNotIn('spawn_not_on_floor', codes)
        self.p._apply(self.m, {'op': 'update', 'id': self.refs.get('x') or max(
            e['nodeID'] for e in self.m.entities(classname='info_player_terrorist')), 'origin': [0, 0, 8]}, self.refs)
        self.assertNotIn('spawn_in_floor', self.codes())

    def test_hostage_map_needs_no_bombsite(self):
        self.assertIn('bombsite_missing', self.codes())
        self.p._apply(self.m, {'op': 'add_entity', 'classname': 'hostage_entity', 'origin': [0, 0, 8]}, self.refs)
        self.assertNotIn('bombsite_missing', self.codes())

    def test_stairs_mesh_is_recognised(self):
        self.p._apply(self.m, {'op': 'add_mesh', 'shape': {'shape': 'stairs', 'mins': [0, 0, 0], 'maxs': [128, 256, 192],
                                                            'steps': 16, 'rise': '+y'}}, self.refs)
        self.p._apply(self.m, {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [300, 0, 0], 'maxs': [400, 100, 192]}},
                      self.refs)
        found = [b for _, b in layout._stair_meshes(self.m)]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][1][2], 192)


@unittest.skipUnless(HAVE_CS2, 'installed FGD required')
class HelperKeys(unittest.TestCase):
    def test_fog_controller_bounds_are_known_keys(self):
        keys = Project().catalog.helper_keys('env_volumetric_fog_controller')
        self.assertTrue({'box_mins', 'box_maxs'} <= keys)


class Openings(unittest.TestCase):
    def test_at_error_names_the_valid_range(self):
        spec = {'op': 'add_layout', 'name': 'o', 'rooms': [{'id': 'a', 'size': [640, 640, 256]},
                {'id': 'b', 'size': [640, 640, 256], 'from': 'a', 'side': '+x'}],
                'openings': [{'between': ['a', 'b'], 'at': .1, 'width': 192}]}
        with self.assertRaisesRegex(OpError, r'use at 0\.\d+\.\.0\.\d+'):
            layout.plan(spec)


class ViewsAfterCommand(unittest.TestCase):
    def test_camera_is_put_back_when_cmd_moves_the_player(self):
        fake = FakeGame(['challenge', 'shot'])
        command = fake.command

        def respawning(s, cmd, wait=1.0):
            out = command(s, cmd, wait)
            if 'mp_restartgame' in cmd:
                fake.pos = [0.0, 0.0, 72.0, 0.0, 0.0]        # back at the spawn
            return out
        fake.command = respawning
        out = fake.run([{'name': 'v', 'pos': [300, 0, 0], 'ang': [0, 90, 0], 'cmd': 'mp_restartgame 1', 'wait': 1}])
        shot = out['shots'][0]
        self.assertEqual(list(shot['player_check']['position'][:2]), [300.0, 0.0])
        restart = fake.log.index('mp_restartgame 1')
        self.assertTrue(any('setpos 300.0' in c for c in fake.log[restart:]))

    def test_frames_taken_before_a_rejected_one_are_kept(self):
        fake = FakeGame(['c1', 'a', 'c2', 'c2', 'c2', 'c2'])
        out = fake.run([{'name': 'first', 'ang': [0, 0, 0]}, {'name': 'second', 'ang': [0, 45, 0]}])
        self.assertEqual([s['name'] for s in out['shots']], ['first'])
        self.assertEqual(out['rejected'][0]['name'], 'second')


if __name__ == '__main__':
    unittest.main()
