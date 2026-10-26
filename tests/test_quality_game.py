import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from cs2map import game
from cs2map.playtest import _auto_views, _look_at, playtest_views
from cs2map.project import Project
from tests.helpers import HAVE_CS2


class FrameChecks(unittest.TestCase):
    def test_status_requires_requested_map(self):
        lines = ["loaded spawngroup(  1)  : SV:  [1: c_light | main lump | mapload]"]
        self.assertTrue(game._status_has_map(lines, "c_light"))
        self.assertFalse(game._status_has_map(lines, "c_other"))

    def test_player_check_rejects_team_menu(self):
        self.assertTrue(game._looks_like_player(["setpos 1 2 3"]))
        self.assertFalse(game._looks_like_player(["setpos 1 2 3", "Choose a team"]))
        self.assertFalse(game._looks_like_player(["No player"]))

    def test_frame_check_rejects_uniform_and_accepts_scene(self):
        with tempfile.TemporaryDirectory() as td:
            blank = Path(td) / "blank.png"
            Image.new("RGB", (640, 360), (0, 0, 0)).save(blank)
            self.assertFalse(game.frame_check(blank, time.time() - 1)['ok'])
            inside = Path(td) / "inside.png"      # a camera inside the floor: flat colour + the fast-build line
            image = Image.new("RGB", (640, 360), (197, 190, 175))
            for x in range(140, 500):
                for y in range(10, 22):
                    image.putpixel((x, y), (255, 255, 0) if (x // 3 + y) % 2 else (0, 0, 0))
            image.save(inside)
            result = game.frame_check(inside, time.time() - 1)
            self.assertFalse(result['ok'], result)
            self.assertIn('inside geometry', ' '.join(result['reasons']))
            scene = Path(td) / "scene.png"
            image = Image.new("RGB", (640, 360), (24, 28, 32))
            for x in range(640):
                for y in range(180, 360):
                    image.putpixel((x, y), ((x * 3) % 255, (y * 2) % 255, 80))
            image.save(scene)
            result = game.frame_check(scene, time.time() - 1)
            self.assertTrue(result['ok'], result)
            self.assertTrue(result['fresh'])

    def test_frame_check_detects_same_frame(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "scene.png"
            Image.new("RGB", (640, 360), (40, 50, 60)).save(path)
            first = game.frame_check(path, time.time() - 1)
            second = game.frame_check(path, time.time() - 1, first['signature'])
            self.assertFalse(second['ok'])
            self.assertIn('frame did not change', ' '.join(second['reasons']))

    def test_runtime_player_requires_live_team_member(self):
        state={'players':[{'slot':0,'bot':False,'team':1,'alive':True,'observing':False,'health':100}]}
        self.assertIsNone(game._live_player(state))
        state['players'][0].update(team=2)
        self.assertIs(game._live_player(state),state['players'][0])

    def test_getpos_parser(self):
        self.assertEqual(game._getpos_values(['setpos 1 -2 3;setang 4 5 0']), (1.0,-2.0,3.0,4.0,5.0))

    def test_playtest_views_batches_explicit_requests(self):
        requests = [
            {'name': 'l_one', 'pos': [0, 0, 0], 'ang': [0, 0, 0]},
            {'name': 'l_two', 'pos': [1, 0, 0], 'ang': [0, 90, 0]},
            {'name': 'l_three', 'pos': [2, 0, 0], 'ang': [0, 180, 0]},
        ]
        returned = {'map': 'l_batch', 'reloaded': True,
                    'shots': [{'name': r['name'], 'path': f"{r['name']}.png"} for r in requests[1:2]]}
        with mock.patch('cs2map.playtest.game.views', return_value=returned) as views:
            result = playtest_views(mock.Mock(s=object()), 'l_batch', requests, offset=1, limit=1)
        views.assert_called_once()
        self.assertEqual(result['total_views'], 3)
        self.assertEqual(result['offset'], 1)
        self.assertEqual(result['shots'][0]['name'], 'l_two')

    @unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
    def test_auto_views_are_named_and_point_into_map(self):
        views = _auto_views(Project(), 'mcp_accept')
        self.assertGreaterEqual(len(views), 4)
        self.assertTrue(all(v['name'].startswith('mcp_accept_') for v in views))
        self.assertTrue(any('bomb_target' in v['name'] for v in views))
        self.assertTrue(any('spawns_' in v['name'] for v in views))
        self.assertEqual(len(_look_at([0, 0, 100], [100, 0, 0])), 3)


if __name__ == '__main__':
    unittest.main()
