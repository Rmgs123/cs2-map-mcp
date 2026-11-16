"""Where players can stand (reach.py) and what visual_check makes of it: escapes, invisible walls only where players
walk up to them, doorways of rotated houses (the player box does not turn)."""
import math
import unittest

from tests.helpers import HAVE_CS2, agent_suffix

MAP = 'test_reach' + agent_suffix()
DEV = 'materials/dev/dev_measuregeneric01.vmat'
CLIP = 'materials/tools/toolsplayerclip.vmat'


def box(mins, maxs, material=DEV):
    return {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': mins, 'maxs': maxs}, 'material': material}


def wall(cx, cy, length, yaw, thick=16, height=128):
    """A wall of `length` centred at (cx, cy) along direction yaw (a prism, so it can turn)."""
    a = math.radians(yaw)
    ux, uy, nx, ny = math.cos(a), math.sin(a), -math.sin(a), math.cos(a)
    h, t = length / 2, thick / 2
    pts = [[cx + ux * sx * h + nx * sy * t, cy + uy * sx * h + ny * sy * t] for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    return {'op': 'add_mesh', 'shape': {'shape': 'prism', 'polygon': pts, 'z0': 0, 'z1': height}, 'material': DEV}


@unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
class Reach(unittest.TestCase):
    def build(self, extra, area=None):
        from cs2map import server
        p = server.project
        p.map_create(MAP, 'empty', overwrite=True)
        ops = [{**box([-2048, -2048, -16], [2048, 2048, 0]), 'tags': ['paved']},          # ground the checks know
               {'op': 'add_entity', 'classname': 'info_player_terrorist', 'origin': [0, 0, 8]},
               # a closed yard 1024 x 1024 round the spawn: walls on four sides
               box([-528, -528, 0], [528, -512, 160]), box([-528, 512, 0], [528, 528, 160]),
               box([-528, -512, 0], [-512, 512, 160]), *extra]
        if area:
            ops.append({'op': 'set_play_area', 'outline': area, 'nav': 'all'})
        p.map_edit(MAP, ops)
        return p

    def flood(self, p):
        from cs2map import reach
        return reach.flood(p, MAP)

    def reached_at(self, r, x, y):
        from cs2map import reach
        return reach.lookup(r)(x, y, 0)

    def test_a_gap_in_the_yard_is_an_escape(self):
        from cs2map import visual
        yard = [[-512, -512], [512, -512], [512, 512], [-512, 512]]
        # the east wall with a 64-unit gap in it
        p = self.build([box([512, -512, 0], [528, -32, 160]), box([512, 32, 0], [528, 512, 160])], yard)
        found = visual.visual_check(p, MAP, 50)['summary']['by_code']
        self.assertGreaterEqual(found.get('visual_escape', 0), 1, found)
        p = self.build([box([512, -512, 0], [528, 512, 160])], yard)
        found = visual.visual_check(p, MAP, 50)['summary']['by_code']
        self.assertNotIn('visual_escape', found)

    def test_a_doorway_of_a_turned_house_is_measured_with_the_square_player(self):
        # the east wall closed; a 45-degree wall across the yard's north-east corner with a doorway in it
        def corner(door):
            seg = 600.0
            parts = []
            for side in (-1, 1):
                length = (seg - door) / 2
                off = side * (door / 2 + length / 2)
                cx, cy = 256 + off * math.cos(math.radians(135)), 256 + off * math.sin(math.radians(135))
                parts.append(wall(cx, cy, length, 135))
            return parts
        for door, passes in ((36, False), (52, True)):
            with self.subTest(door=door):
                p = self.build([box([512, -512, 0], [528, 512, 160]), *corner(door)])
                r = self.flood(p)
                self.assertEqual(self.reached_at(r, 460, 460), passes)

    def test_invisible_walls_only_where_players_walk_up_to_them(self):
        from cs2map import visual
        inside = box([-200, 300, 0], [200, 316, 128], CLIP)            # in the yard: players meet it
        outside = box([700, -200, 0], [716, 200, 128], CLIP)           # out of the closed yard: nobody does
        p = self.build([box([512, -512, 0], [528, 512, 160]), inside, outside])
        problems = [x for x in visual.visual_check(p, MAP, 100)['problems'] if x['code'] == 'visual_bare_clip']
        self.assertEqual(len(problems), 1, problems)
        self.assertLess(abs(problems[0]['at'][1] - 308), 40)


if __name__ == '__main__':
    unittest.main()
