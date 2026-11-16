"""What a map costs and what nobody sees (perfcheck.py, map_info about="cost"); frame diffs of reference_compare."""
import unittest

from tests.helpers import HAVE_CS2, agent_suffix

MAP = 'test_perfcheck' + agent_suffix()
DEV = 'materials/dev/dev_measuregeneric01.vmat'


def box(mins, maxs, name=None):
    op = {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': mins, 'maxs': maxs}, 'material': DEV}
    if name:
        op['name'] = name
    return op


@unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
class Cost(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cs2map import server
        cls.p = server.project
        cls.p.map_create(MAP, 'empty', overwrite=True)
        res = cls.p.map_edit(MAP, [
            box([-1024, -1024, -16], [4096, 4096, 0]),
            {'op': 'add_entity', 'classname': 'info_player_terrorist', 'origin': [0, 0, 8]},
            # the play yard: walls 400 high on its east and north sides (players inside cannot see over them)
            box([512, -1024, 0], [528, 512, 400]), box([-1024, 512, 0], [528, 528, 400]),
            box([-1040, -1024, 0], [-1024, 528, 400]), box([-1024, -1040, 0], [528, -1024, 400]),
            {'op': 'set_play_area', 'outline': [[-1024, -1024], [512, -1024], [512, 512], [-1024, 512]],
             'nav': 'all'},
            box([1200, 0, 0], [1264, 64, 64], 'hidden_crate'),            # low, behind the east wall
            box([1200, 1500, 0], [1300, 1600, 1200], 'tall_tower'),      # over the walls: seen
        ])
        cls.ids = {r.get('ref') or r['op']: r for r in res.get('results', [])}

    def test_a_low_box_behind_a_wall_is_never_seen_and_a_tower_is(self):
        from cs2map import perfcheck
        r = perfcheck.cost(self.p, MAP)
        self.assertGreater(r['triangles']['meshes'], 0)
        hidden = r['never_seen']
        self.assertIsInstance(hidden, dict, hidden)
        at = [tuple(o['at'][:2]) for o in hidden['objects']]
        self.assertIn((1232, 32), at)                                  # the low crate behind the east wall
        self.assertNotIn((1250, 1550), at)                             # the tower over the walls is seen
        self.assertEqual(hidden['count'], 1)


class FrameDiff(unittest.TestCase):
    def test_equal_frames_and_a_changed_spot(self):
        from PIL import Image
        from cs2map import reference
        a = Image.new('RGB', (100, 80), (90, 120, 150))
        b = a.copy()
        self.assertEqual(reference.frame_diff(a, b)['changed'], 0.0)
        for x in range(10, 20):
            for y in range(30, 40):
                b.putpixel((x, y), (255, 0, 0))
        d = reference.frame_diff(a, b)
        self.assertAlmostEqual(d['changed'], 100 / 8000, places=4)
        self.assertEqual(d['box'], [10, 30, 19, 39])


if __name__ == '__main__':
    unittest.main()
