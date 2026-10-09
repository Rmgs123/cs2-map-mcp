"""B3 shader, multilingual artwork and alpha regressions."""
import json
import unittest
import uuid
from pathlib import Path
import numpy as np
from PIL import Image
from cs2map import materials, textures
from cs2map.config import Settings, PROJECT_ROOT


class MaterialKinds(unittest.TestCase):
    def setUp(self):
        self.root = PROJECT_ROOT / '.local/scratch/B/tests' / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.s = Settings(self.root / 'cs2', 'b_test')

    def test_legacy_signature_and_core_pattern_bridge(self):
        old = materials.create(self.s, 'old', None, {'pattern': 'solid', 'size': 64}, .5, .2, 'metal', 2, 'mcp')
        self.assertEqual(old['size'], [64,64])
        self.assertIn('csgo_complex', Path(old['vmat']).read_text())
        new = materials.create(self.s, 'window', pattern={'kind': 'glass', 'options': {'opacity': .2}, 'size': 64})
        self.assertEqual(new['kind'], 'glass')
        self.assertIn('csgo_glass', Path(new['vmat']).read_text())
        with self.assertRaises(ValueError):
            materials.create(self.s, 'bad', pattern={'kind':'glass','options':{'made_up':1}})

    def test_shader_keys_and_water_attributes(self):
        for kind, shader in [('glass','csgo_glass'),('water','csgo_water_fancy'),('emissive','csgo_complex'),('decal','csgo_lightmappedgeneric')]:
            made = materials.create(self.s, kind, pattern={'pattern':'solid','size':64}, kind=kind)
            text = Path(made['vmat']).read_text()
            self.assertIn(shader + '.vfx', text)
            if kind == 'water':
                self.assertIn('mapbuilder.water',text)
                self.assertIn('TextureWavesNormal', text)
                self.assertIn('TextureWavesHeight', text)
            elif kind == 'decal':
                self.assertIn('F_OVERLAY',text)
                self.assertIn('F_TRANSLUCENT',text)
        for options in [{'opacity':float('nan')}, {'glow':-1}, {'flow':float('inf')}, {'kind':'invalid'},
                        {'real_size':[float('nan'),64]}]:
            with self.assertRaises(ValueError):
                materials.create(self.s,'bad',**options)

    def test_cyrillic_neon_has_ink_only_emission(self):
        made=materials.sign_create(self.s,'sign','Склад 3 →','neon')
        folder=Path(made['vmat']).parent
        mask=np.asarray(Image.open(folder/'sign_illum.png'))
        self.assertEqual(mask[0,0],0)
        self.assertGreater(np.count_nonzero(mask),100)
        self.assertLess(np.count_nonzero(mask),mask.size*.7)
        meta=json.loads((folder/'sign.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['sign']['text'],'Склад 3 →')
        self.assertEqual(meta['sign']['aspect'],4)

    def test_emissive_rgba_uses_alpha_and_tints_ink(self):
        src=self.root/'ink.png'
        im=Image.new('RGBA',(64,64),(255,255,255,0))
        im.putpixel((32,32),(255,255,255,255)); im.save(src)
        made=materials.create(self.s,'ink',str(src),kind='emissive',color='#ff0000')
        folder=Path(made['vmat']).parent
        mask=Image.open(folder/'ink_illum.png')
        self.assertEqual(mask.getpixel((0,0)),0)
        self.assertEqual(mask.getpixel((32,32)),255)
        color=Image.open(folder/'ink_color.png')
        self.assertEqual(color.getpixel((0,0)),(0,0,0))
        self.assertEqual(color.getpixel((32,32)),(255,0,0))

    def test_decal_preserves_alpha_and_color_key_is_soft(self):
        src=self.root/'rgba.png'
        im=Image.new('RGBA',(64,64),(200,50,20,0)); im.putpixel((32,32),(200,50,20,128)); im.save(src)
        made=materials.decal_create(self.s,'alpha',str(src),size=[96,48])
        out=Image.open(Path(made['vmat']).parent/'alpha_color.png')
        self.assertEqual(out.getpixel((0,0))[3],0)
        self.assertEqual(out.getpixel((32,32))[3],128)
        im=Image.new('RGB',(64,64),'white'); im.putpixel((32,32),(225,225,225)); im.save(src)
        made=materials.decal_create(self.s,'key',str(src),background='#ffffff',tolerance=20,softness=60)
        out=Image.open(Path(made['vmat']).parent/'key_color.png')
        self.assertEqual(out.getpixel((0,0))[3],0)
        self.assertTrue(0<out.getpixel((32,32))[3]<255)


class ToroidalSeams(unittest.TestCase):
    def test_bands_detected_even_with_equal_edge_pixels(self):
        rng=np.random.default_rng(31)
        arr=rng.integers(80,180,(128,128,3),dtype=np.uint8)
        clean=Image.fromarray(arr)
        bad=arr.copy(); bad[:,:8]=240; bad[:,-8:]=240; bad[:8]=240; bad[-8:]=240
        self.assertEqual(textures.seam_error(bad),0)
        self.assertGreater(textures.tile_analysis(bad)['band_score'],textures.tile_analysis(clean)['band_score']+.2)
        self.assertEqual(textures.tile_analysis(Image.new('RGB',(64,64),'gray'))['band_score'],0)

    def test_periodic_solve_preserves_detail_and_removes_wide_feather(self):
        rng=np.random.default_rng(14)
        arr=rng.integers(20,240,(128,160,3),dtype=np.uint8)
        new=textures.make_tileable(arr,'stochastic')
        self.assertEqual(textures.seam_error(new),0)
        self.assertLess(textures.mirror_score(new),.4)
        self.assertGreater(np.asarray(new).std(),arr.std()*.8)
        self.assertEqual(new.size,(160,128))


if __name__ == '__main__': unittest.main()
