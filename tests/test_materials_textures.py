import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from cs2map import materials, textures
from cs2map.config import Settings


class TexturePipeline(unittest.TestCase):
    def test_tileable_has_lower_seam_error(self):
        rng = np.random.default_rng(42)
        image = Image.fromarray(rng.integers(0, 256, (64, 80, 3), dtype=np.uint8))
        result = textures.make_tileable(image)
        self.assertLess(textures.seam_error(result), textures.seam_error(image))

    def test_load_quad(self):
        image = Image.new('RGB', (100, 100), 'red')
        out = textures.load(image, quad=[[10, 10], [90, 20], [80, 90], [20, 80]])
        self.assertGreater(out.width, 0)
        self.assertGreater(out.height, 0)
        self.assertEqual(out.getpixel((out.width // 2, out.height // 2)), (255, 0, 0))

    def test_pbr_maps_and_metadata_are_optional(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            s = Settings(root / 'cs2', 'test_addon')
            source = root / 'source.png'
            Image.new('RGB', (31, 17), (120, 100, 80)).save(source)
            normal = root / 'normal.png'
            Image.new('RGB', (8, 8), (128, 128, 255)).save(normal)
            made = materials.create(s, 'ref', str(source), normal_image=str(normal), alpha='test', self_illum=True,
                                    real_size=[64, 32], folder='mcp')
            out = s.content_addon / 'materials/mcp/ref'
            self.assertEqual(made['size'], [64, 64])
            self.assertTrue((out / 'ref_normal.png').exists())
            self.assertEqual(json.loads((out / 'ref.json').read_text())['real_size'], [64.0, 32.0])
            text = (out / 'ref.vmat').read_text()
            self.assertIn('F_ALPHA_TEST 1', text)
            self.assertIn('F_SELF_ILLUM 1', text)


if __name__ == '__main__':
    unittest.main()

