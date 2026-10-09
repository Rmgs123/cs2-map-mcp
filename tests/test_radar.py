"""Radar images and overview files drawn from a map's geometry (radar.py)."""
import unittest

from cs2map import layout, radar
from cs2map.project import Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class Radar(unittest.TestCase):
    def test_floors_by_height_and_walls(self):
        p, m, refs = Project(), VMap.from_template(SETTINGS.cs2_path, 'empty'), {}
        layout.add_layout(p, m, {'op': 'add_layout', 'name': 'q', 'wall': 16,
                                 'rooms': [{'id': 'low', 'size': [1024, 512, 256], 'ceiling': False},
                                           {'id': 'high', 'size': [512, 512, 256], 'from': 'low', 'side': '+x',
                                            'ceiling': False, 'floor_z': 96}],
                                 'openings': [{'between': ['low', 'high'], 'width': 128, 'height': 160}]}, refs)
        img, pos_x, pos_y, scale = radar.draw(m)
        self.assertEqual(img.size, (radar.SIZE, radar.SIZE))
        self.assertLess(scale, 2.0)                                     # the play area, not the void around it

        def at(x, y):
            return img.getpixel((int((x - pos_x) / scale), int((pos_y - y) / scale)))
        low, high, outside = at(512, 256), at(1300, 256), at(pos_x + 4, pos_y - 4)
        self.assertGreater(sum(high[:3]), sum(low[:3]))                 # higher floor is lighter
        self.assertEqual(outside, radar.BG)


if __name__ == '__main__':
    unittest.main()
