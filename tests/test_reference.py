import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageDraw

from cs2map import reference
from cs2map.reference import reference_facade, reference_compare

ROOT=Path(__file__).resolve().parents[1]
REF=ROOT/'screenshots/ref_inferno_spawn-20261003-213340-310.png'


class ReferenceFacade(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Fixtures and outputs live in a temp folder inside the project (ensure_inside) and are removed.
        (ROOT/'.local'/'tmp').mkdir(parents=True,exist_ok=True)
        cls.folder=Path(tempfile.mkdtemp(prefix='ref_',dir=ROOT/'.local'/'tmp'))
        cls.out=patch.object(reference,'OUTPUT',cls.folder/'out');cls.out.start()
        cls.fixture=cls.folder/'facade.png'
        im=Image.new('RGB',(600,600),(210,180,140));draw=ImageDraw.Draw(im)
        for x in (100,270,440):
            for y in (80,260,440):draw.rectangle((x,y,x+50,y+80),fill=(20,30,35))
        im.save(cls.fixture)

    @classmethod
    def tearDownClass(cls):
        cls.out.stop();shutil.rmtree(cls.folder,ignore_errors=True)

    def test_rectifies_and_returns_reviewable_suggestion(self):
        result=reference_facade(str(self.fixture), real_width=512)
        self.assertTrue(result['ok']); self.assertTrue(result['needs_visual_review'])
        self.assertTrue(Path(result['rectified']).is_file()); self.assertTrue(Path(result['wall_crop']).is_file())
        self.assertIn('-y',result['facades']); self.assertIn('palette',result)
        self.assertEqual(result['estimated_floors'],3)
        self.assertEqual(result['estimated_columns'],3)

    def test_invalid_quad_rejected(self):
        for quad in ([[0,0],[1,0],[1,1],[0,1]],[[0,0],[600,600],[600,0],[0,600]],[[0,0],[900,0],[900,600],[0,600]]):
            with self.subTest(quad=quad),self.assertRaises(ValueError):reference_facade(str(self.fixture),quad)

    def test_compare_equalizes_height(self):
        small=self.folder/'small.png';Image.new('RGB',(200,100),'red').save(small)
        result=reference_compare(str(self.fixture),str(small)); self.assertTrue(Path(result['comparison']).is_file())
        with Image.open(result['comparison']) as im:self.assertEqual(im.size,(1808,632))

    def test_flat_wall_does_not_invent_detected_floors(self):
        path=self.folder/'blank.png';Image.new('RGB',(200,200),'beige').save(path)
        r=reference_facade(str(path));self.assertIsNone(r['estimated_floors']);self.assertIsNone(r['estimated_columns'])
        self.assertTrue(r['needs_visual_review'])

    def test_quad_keeps_rectified_coordinates_and_width_scale(self):
        r=reference_facade(str(self.fixture),[[100,50],[500,50],[500,550],[100,550]],400)
        self.assertEqual(r['size'],[400,500]);self.assertEqual(r['estimated_height'],500)
