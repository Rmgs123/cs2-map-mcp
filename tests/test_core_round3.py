"""Core changes of the round-3 integration: view order and freshness, switchable lamps, story checks."""
import unittest
from unittest import mock

from cs2map import game, lighting, mesh
from cs2map.project import OpError, Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


class FakeGame:
    """Records console commands; every capture returns the signature queued in `frames`."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.log = []
        self.pos = [0.0, 0.0, 64.0, 0.0, 0.0]

    def command(self, s, cmd, wait=1.0):
        self.log.append(cmd)
        for line in cmd.split('\n'):
            parts = line.split()
            if parts and parts[0] == 'setpos':
                self.pos[:3] = [float(parts[1]), float(parts[2]), float(parts[3]) + 64]
            if parts and parts[0] == 'setang':
                self.pos[3:] = [float(parts[1]), float(parts[2])]
        return ['printed by ' + cmd.split('\n')[0], 'Unable to determine cubemap texture for env_cubemap_fog']

    def getpos(self, s):
        x, y, z, p, yaw = self.pos
        return [f'setpos {x} {y} {z};setang {p} {yaw} 0']

    def capture(self, s, w, name, captured_at=None, previous_signature=None, folder=None):
        sig = self.frames.pop(0)
        self.log.append(f'capture {name} {sig}')
        return {'name': name, 'path': name + '.png', 'frame_check': {'ok': True, 'reasons': [], 'signature': sig}}

    def run(self, shots):
        player = {'slot': 0, 'team': 2, 'alive': True, 'observing': False, 'health': 100, 'noclip': True,
                  'angles': {'pitch': 0, 'yaw': 0, 'roll': 0}}
        with mock.patch.multiple(game, command=self.command, getpos=self.getpos, _capture=self.capture,
                                 _window=lambda title: None, wait_for_player=lambda s, timeout=0: None,
                                 player_state=lambda s: {'players': [player]}), \
                mock.patch.object(game.time, 'sleep', lambda x: self.log.append(f'sleep {x}')):
            return game._views_locked(None, None, shots, True, 0.01)


class ViewOrder(unittest.TestCase):
    def test_cmd_runs_after_the_camera_is_in_place(self):
        fake = FakeGame(['challenge', 'shot'])
        out = fake.run([{'name': 'v', 'pos': [100, 0, 0], 'ang': [0, 90, 0], 'cmd': 'ent_fire door Open',
                         'wait': 2, 'grep': 'printed'}])
        setpos = next(i for i, c in enumerate(fake.log) if 'setpos 100.0' in c)
        self.assertGreater(fake.log.index('ent_fire door Open'), setpos)
        self.assertEqual(out['shots'][0]['console'], ['printed by ent_fire door Open'])

    def test_wait_alone_sleeps_before_the_shot(self):
        fake = FakeGame(['challenge', 'shot'])
        fake.run([{'name': 'v', 'ang': [0, 0, 0], 'wait': 3}])
        self.assertIn('sleep 3.0', fake.log)

    def test_stale_frame_equal_to_the_challenge_is_retried_then_rejected(self):
        fake = FakeGame(['same', 'same', 'same', 'same'])
        with self.assertRaisesRegex(game.GameError, 'did not change'):
            fake.run([{'name': 'v', 'ang': [0, 0, 0]}])
        fake = FakeGame(['challenge', 'challenge', 'fresh'])
        out = fake.run([{'name': 'v', 'ang': [0, 0, 0]}])
        self.assertEqual(len(out['shots']), 1)

    def test_same_view_twice_may_give_the_same_picture(self):
        fake = FakeGame(['c1', 'scene', 'c2', 'scene'])
        out = fake.run([{'name': 'a', 'ang': [0, 0, 0]}, {'name': 'b', 'ang': [0, 0, 0], 'cmd': 'snd_showstart 1'}])
        self.assertEqual(len(out['shots']), 2)

    def test_console_digest_drops_noise(self):
        lines = ['Unable to determine cubemap texture for env_cubemap_fog', 'a', 'b sounds\\x.vsnd']
        self.assertEqual(game._console_digest(lines), ['a', 'b sounds\\x.vsnd'])
        self.assertEqual(game._console_digest(lines, 'vsnd'), ['b sounds\\x.vsnd'])


@unittest.skipUnless(HAVE_CS2, 'CS2 templates needed')
class Round3Ops(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(SETTINGS.cs2_path, 'empty')

    def test_switchable_lamp_is_parented_to_a_parked_mover(self):
        out = lighting.add_light(self.p, self.m, {'kind': 'ceiling', 'origin': [0, 0, 150], 'start_off': True,
                                                  'name': 'lamp'}, {})
        self.assertEqual(out['switch'], {'on': 'lamp Open', 'off': 'lamp Close'})
        mover = self.m.entities(targetname='lamp')[0]
        light = self.m.entities(targetname='lamp_light')[0]
        self.assertEqual(self.m.classname(mover), 'func_movelinear')
        props = light['entity_properties']
        self.assertEqual((props.get('parentname'), props.get('directlight')), ('lamp', '2'))
        self.assertLess(light['origin'][2], -5000)

    def test_light_style_and_bad_style(self):
        lighting.add_light(self.p, self.m, {'kind': 'ceiling', 'origin': [0, 0, 150], 'style': 'flicker_1',
                                            'name': 'fl'}, {})
        props = self.m.entities(targetname='fl')[0]['entity_properties']
        self.assertEqual((props.get('light_style'), props.get('directlight')), ('flicker_1', '3'))
        with self.assertRaisesRegex(ValueError, 'style must be one of'):
            lighting.add_light(self.p, self.m, {'kind': 'ceiling', 'origin': [0, 0, 1], 'style': 'disco'}, {})

    def test_rain_box_needs_no_origin(self):
        from cs2map import fx
        out = fx.add_fx(self.p, self.m, {'effect': 'rain', 'mins': [0, 0, 0], 'maxs': [256, 256, 256]}, {})
        self.assertTrue(out['area'])

    def test_add_mesh_rejects_misplaced_shape_fields_and_takes_materials(self):
        with self.assertRaisesRegex(OpError, 'inside "shape"'):
            self.p._apply(self.m, {'op': 'add_mesh', 'shape': {'shape': 'box'}, 'mins': [0, 0, 0]}, {})
        out = self.p._apply(self.m, {'op': 'add_mesh', 'shape': {'shape': 'room', 'mins': [0, 0, 0],
                                                                  'maxs': [128, 128, 128]},
                                     'materials': {'floor': 'materials/dev/dev_measuregeneric01b.vmat'}}, {})
        floor = self.m.node(out['ids'][0])
        self.assertIn('materials/dev/dev_measuregeneric01b.vmat', floor['meshData']['materials'])


@unittest.skipUnless(HAVE_CS2, 'CS2 templates needed')
class Preprocessor(unittest.TestCase):
    def test_preprocessor_rewrites_ops_before_they_run(self):
        p = Project()
        m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        p.preprocessors = [lambda proj, vm, op, refs: {**op, 'origin': [1, 2, 3]} if op.get('origin') == 'here' else op]
        out = p._apply(m, {'op': 'add_entity', 'classname': 'info_target', 'origin': 'here'}, {})
        self.assertEqual(tuple(m.node(out['id'])['origin']), (1, 2, 3))


class StoryChecks(unittest.TestCase):
    def test_story_maps_skip_competitive_errors(self):
        from cs2map import checks
        p = mock.Mock()
        with mock.patch('cs2map.story.map_stories', return_value=[('node', {})]):
            self.assertTrue(checks._is_story_map(p, object()))
        with mock.patch('cs2map.story.map_stories', side_effect=RuntimeError('broken')):
            self.assertFalse(checks._is_story_map(p, object()))


if __name__ == '__main__':
    unittest.main()
