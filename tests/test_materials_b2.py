"""B2 regressions: real VTEX layouts/codecs and non-mirrored tiling."""
import struct
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from cs2map import textures, vtex
from cs2map.config import PROJECT_ROOT, load_settings
from cs2map.vpk import GameFiles


def compiled(fmt, width, height, payload, mips=1):
    header = struct.pack('<IHHII', 68, 12, 1, 8, 1)
    block = b'DATA' + struct.pack('<II', 8, 40)
    data = struct.pack('<HH4fHHHBBfII', 1, 0, 0, 0, 0, 0, width, height, 1, fmt, mips, 0, 0, 0)
    return header + block + data + payload


class VTexCodecs(unittest.TestCase):
    def image(self, fmt, payload, width=4, height=4):
        data = compiled(fmt, width, height, payload)
        return vtex.decode(data, vtex.parse(data))

    def test_bc1_bc3_bc4_bc5_channels(self):
        red = struct.pack('<HHI', 0xf800, 0, 0)
        alpha = bytes([255, 0]) + bytes(6)
        green = bytes([128, 0]) + bytes(6)
        self.assertEqual(self.image(1, red).getpixel((3, 2)), (255, 0, 0, 255))
        self.assertEqual(self.image(2, alpha + red).getpixel((1, 1)), (255, 0, 0, 255))
        self.assertEqual(self.image(27, alpha).getpixel((1, 1)), (255, 255, 255, 255))
        self.assertEqual(self.image(21, alpha + green).getpixel((1, 1)), (255, 128, 255, 255))

    def test_bc7_pillow_known_mode5(self):
        # Stock default-normal BC7 block; all pixels have the same normal.
        im = self.image(20, bytes.fromhex('20a17fe88ff7ffffafaaaaaa00000000'))
        self.assertEqual(im.size, (4, 4))
        self.assertGreater(im.getpixel((0, 0))[2], 240)

    def test_rgba_bgra_and_smallest_first_mips(self):
        self.assertEqual(self.image(4, bytes([1, 2, 3, 4]), 1, 1).getpixel((0, 0)), (1, 2, 3, 4))
        self.assertEqual(self.image(28, bytes([1, 2, 3, 4]), 1, 1).getpixel((0, 0)), (3, 2, 1, 4))
        data = compiled(4, 2, 2, bytes([8, 9, 10, 255]) + bytes([20, 30, 40, 255]) * 4, mips=2)
        info = vtex.parse(data)
        self.assertEqual(vtex.decode(data, info, 1).getpixel((0, 0)), (8, 9, 10, 255))
        self.assertEqual(vtex.decode(data, info).getpixel((1, 1)), (20, 30, 40, 255))

    def test_lz4_overlap_and_refusal(self):
        self.assertEqual(vtex._lz4_block(b'\x40abcd', 4), b'abcd')
        self.assertEqual(vtex._lz4_block(b'\x40abcd\x04\x00', 8), b'abcdabcd')
        with self.assertRaises(vtex.VTexError):
            vtex._lz4_block(b'\x00\x00\x00', 4)
        with self.assertRaises(vtex.VTexError):
            vtex.parse(b'bad')
        with self.assertRaises(vtex.VTexError):
            self.image(1, bytes(7))

    def test_five_installed_stock_materials(self):
        settings = load_settings()
        if not (settings.cs2_path / 'game/csgo/pak01_dir.vpk').is_file():
            self.skipTest('CS2 VPK is unavailable')
        game = GameFiles(settings.cs2_path)
        for name in ['brick/brick_ext_04', 'concrete/concrete_floor_04', 'metal/ibeam',
                     'wood/wood_ext_05', 'tile/tilefloor005a']:
            with self.subTest(material=name):
                out = vtex.export(game, f'materials/{name}.vmat', PROJECT_ROOT / '.local/textures/test_B', mip=1)
                self.assertTrue(out['ok'])
                self.assertFalse(out['errors'])
                self.assertIn('color', out['maps'])
                self.assertIn('normal', out['maps'])
                for path in out['maps'].values():
                    with Image.open(path) as im:
                        self.assertGreater(im.width, 0)


class NonMirroredTiling(unittest.TestCase):
    def test_old_algorithm_fails_mirror_gate(self):
        rng = np.random.default_rng(4)
        source = Image.fromarray(rng.integers(0, 256, (128, 160, 3), dtype=np.uint8))
        old = textures.make_tileable(source, 'legacy')
        new = textures.make_tileable(source, 'stochastic')
        self.assertGreater(textures.mirror_score(old), .95)
        self.assertLess(textures.mirror_score(new), .35)
        self.assertLess(textures.seam_error(new), .01)
        self.assertGreater(np.asarray(new).std(), np.asarray(source).std() * .8)

    def test_regular_grid_crops_whole_periods(self):
        y, x = np.mgrid[:140, :180]
        arr = np.repeat(((x % 32 < 3) | (y % 24 < 3))[..., None], 3, axis=2) * 140 + 40
        self.assertEqual(textures.tile_analysis(arr)['period'], [32, 24])
        result = textures.make_tileable(arr)
        self.assertEqual(result.size, (160, 120))
        self.assertEqual(textures.seam_error(result), 0)


if __name__ == '__main__':
    unittest.main()
