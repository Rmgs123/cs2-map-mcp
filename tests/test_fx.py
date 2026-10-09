import unittest
from cs2map import fx
from cs2map.project import Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2,SETTINGS

@unittest.skipUnless(HAVE_CS2,'Installed FGD/assets needed')
class Effects(unittest.TestCase):
    def setUp(self):
        self.p=Project()
        self.m=VMap.from_template(SETTINGS.cs2_path,'empty')

    def test_all_aliases_exist_and_named_particle_has_stop_start(self):
        for effect,path in fx.EFFECTS.items():
            r=fx.add_fx(self.p,self.m,{'effect':effect,'origin':[0,0,32],'start':False},{})
            self.assertEqual(r['effect'],path)
            self.assertEqual(r['inputs'],{'start':'Start','stop':'Stop'})
            self.assertEqual(self.m.node(r['id'])['entity_properties']['start_active'],'0')

    def test_precipitation_world_bounds_native_subclasses(self):
        for effect in ['rain','snow']:
            r=fx.add_fx(self.p,self.m,{'effect':effect,'origin':[0,0,0],'backend':'native',
                                      'mins':[-128,-128,0],'maxs':[128,128,512]}, {})
            ent=self.m.node(r['id'])
            self.assertEqual(self.m.classname(ent),'func_precipitation')
            self.assertEqual(ent['entity_properties']['subclass_name'],'precipitation_'+effect)
            self.assertEqual(r['inputs']['stop'],'Disable')
            self.assertEqual(len(ent['children']),1)

    def test_invalid_effect_missing_bounds_duplicate_name(self):
        for op in [{'effect':'../../bad.vpcf','origin':[0,0,0]},
                   {'effect':'rain','origin':[0,0,0],'mins':[0,0,0]},
                   {'effect':'smoke','origin':[0,0,0],'start':'false'}]:
            with self.assertRaises(ValueError):fx.add_fx(self.p,self.m,op,{})
        op={'effect':'smoke','origin':[0,0,0],'name':'l_smoke'}
        fx.add_fx(self.p,self.m,op,{})
        with self.assertRaises(ValueError):fx.add_fx(self.p,self.m,op,{})

    def test_weather_tiles_controls_limits_and_lightning(self):
        from unittest.mock import patch
        with patch('cs2map.checks._asset_exists',return_value=True):
            result=fx.add_fx(self.p,self.m,{'effect':'rain','backend':'particle','mins':[0,0,0],
                'maxs':[448,448,640],'name':'m_rain','start':False},{})
        self.assertEqual(result['tiles'],4)
        self.assertEqual(result['inputs'],{'start':'FireUser1','stop':'FireUser2'})
        relay=self.m.node(result['id'])
        self.assertEqual(len(relay['connectionsData']),8)
        for node_id in result['ids'][1:]:
            self.assertEqual(self.m.node(node_id)['entity_properties']['start_active'],'0')
        before=len(list(self.m.iter_nodes()))
        with patch('cs2map.checks._asset_exists',return_value=True):
            for maxs in ([448,448,200],[10000,10000,640]):
                with self.assertRaises(ValueError):
                    fx.add_fx(self.p,self.m,{'effect':'rain','backend':'particle','mins':[0,0,0],'maxs':maxs},{})
        self.assertEqual(before,len(list(self.m.iter_nodes())))
        flash=fx.add_fx(self.p,self.m,{'effect':'lightning','name':'m_flash','start':False},{})
        self.assertEqual(flash['inputs']['flash'],'FireTimer')
        self.assertEqual(self.m.node(flash['id'])['entity_properties']['StartDisabled'],'1')
