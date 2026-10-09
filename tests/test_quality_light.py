import unittest
from types import SimpleNamespace

from cs2map import lighting, mesh
from cs2map.playtest import CameraGeometry, find_rooms, _auto_views
from cs2map.project import Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


@unittest.skipUnless(HAVE_CS2,'CS2 fixture template needed')
class IndoorQuality(unittest.TestCase):
    def setUp(self):
        self.p=Project()
        self.m=VMap.from_template(SETTINGS.cs2_path,'empty')

    def box(self,lo,hi):
        self.m.attach(self.m.new_mesh(mesh.box(lo,hi)))

    def room(self,x=0):
        self.box([x,0,-16],[x+512,512,0])
        self.box([x,0,256],[x+512,512,272])
        self.box([x-16,0,0],[x,512,256])
        self.box([x+512,0,0],[x+528,512,256])
        self.box([x,-16,0],[x+512,0,256])
        self.box([x,512,0],[x+512,528,256])

    def test_split_rooms_under_single_roof_and_no_roof_overviews(self):
        self.room()
        self.room(544)
        rooms=find_rooms(self.m)
        self.assertEqual(len(rooms),2)
        scene=CameraGeometry(self.m)
        for r in rooms:
            self.assertTrue(scene.clear(r['eye']))
            self.assertTrue(scene.visible(r['eye'],r['centre']))
            self.assertEqual(r['floor'],0)
            self.assertEqual(r['ceiling'],256)
        views=_auto_views(SimpleNamespace(load=lambda _:self.m),'l_test')
        self.assertFalse(any('overview' in v['name'] for v in views))
        self.assertEqual(len(views),2)

    def test_open_space_not_a_room_and_helper_volumes_do_not_make_roof(self):
        self.box([0,0,-16],[512,512,0])
        self.assertEqual(find_rooms(self.m),[])
        helper=self.m.new_mesh(mesh.box([0,0,256],[512,512,272],'materials/tools/toolslightmapres.vmat'))
        self.m.attach(helper)
        self.assertEqual(find_rooms(self.m),[])

    def test_lamps_fgd_units_temperature_visible_shape_and_idempotent_rooms(self):
        self.room()
        refs={}
        r=lighting.light_rooms(self.p,self.m,{},refs)
        self.assertEqual(len(r['ids']),1)
        e=self.m.node(r['ids'][0]);kv=e['entity_properties']
        self.assertEqual(kv['brightness_units'],'1')
        self.assertEqual(kv['colormode'],'1')
        self.assertEqual(kv['showlight'],'1')
        self.assertEqual(lighting.light_rooms(self.p,self.m,{},refs)['ids'],[])
        f=lighting.add_light(self.p,self.m,{'kind':'fire','origin':[64,64,64],'ref':'fire'},refs)
        self.assertEqual(self.m.node(f['id'])['entity_properties']['light_style'],'candle_1')
        self.assertEqual(refs['fire'],f['id'])

    def test_invalid_inputs_and_explicit_fog_idempotence(self):
        for op in [{'origin':[0,0,float('nan')]},{'origin':[0,0,0],'brightness':'bad'},
                   {'origin':[0,0,0],'color':[256,0,0]}, {'origin':[0,0,0],'temperature':999},
                   {'origin':[0,0,0],'temperature':3000,'color':[1,2,3]}]:
            with self.assertRaises(ValueError):lighting.add_light(self.p,self.m,op,{})
        self.room()
        lighting.apply_preset(self.p,self.m,'sunset','volumetric')
        lighting.apply_preset(self.p,self.m,'night','light')
        self.assertEqual(len(self.m.entities('env_volumetric_fog_controller')),1)
        self.assertEqual(len(self.m.entities('env_volumetric_fog_volume')),1)
        lighting.apply_preset(self.p,self.m,'night','none')
        self.assertEqual(self.m.entities('env_volumetric_fog_controller')[0]['entity_properties']['StartDisabled'],'1')
        self.assertEqual(self.m.entities('env_volumetric_fog_volume')[0]['entity_properties']['StartDisabled'],'1')

    def test_rotated_mesh_room_and_floor_ids(self):
        self.room()
        floor=next(n for n in self.m.iter_nodes() if n.type=='CMapMesh')
        self.assertEqual(len(find_rooms(self.m,[floor['nodeID']])),1)
        # Apply a common rigid transform to every authored mesh, preserving
        # the room while making the wall-ray intersections no longer box axes.
        import math
        for n in self.m.iter_nodes():
            if n.type=='CMapMesh':
                x,y,z=n['origin'];a=math.radians(30)
                n['origin']=(x*math.cos(a)-y*math.sin(a),x*math.sin(a)+y*math.cos(a),z)
                n['angles']=(0,30,0)
        rooms=find_rooms(self.m)
        self.assertGreaterEqual(len(rooms),1)
        scene=CameraGeometry(self.m)
        self.assertTrue(all(scene.clear(r['eye']) and scene.visible(r['eye'],r['centre']) for r in rooms))

    def test_spot_has_visible_disk_and_explicit_color(self):
        result=lighting.add_light(self.p,self.m,{'kind':'spot','origin':[128,128,224],'color':[200,220,255]}, {})
        self.assertEqual(len(result['ids']),2)
        lamp=self.m.node(result['id'])
        self.assertEqual(self.m.classname(lamp),'light_barn')
        self.assertEqual(lamp['entity_properties']['colormode'],'0')

    def test_shaft_meshes_are_noncolliding_and_fog_bounds_are_explicit(self):
        result=lighting.add_light(self.p,self.m,{'kind':'shaft','origin':[0,0,256],
            'angles':[40,0,0],'length':320,'width':64,'model':False},{})
        self.assertEqual(self.m.classname(self.m.node(result['id'])),'light_barn')
        self.assertEqual(len(result['ids']),9)        # lamp + 4 planes x 2 sides ("star")
        for node_id in result['ids'][1:]:
            self.assertEqual(self.m.node(node_id)['physicsType'],'none')
        lighting.apply_preset(self.p,self.m,'night',{'mode':'volumetric','density':.2,
            'draw_distance':1800,'mins':[-32,-32,0],'maxs':[512,512,256]})
        fog=self.m.entities('env_volumetric_fog_controller')[0]
        self.assertEqual(fog['entity_properties']['FogStrength'],'0.2')
        before=len(list(self.m.iter_nodes()))
        for invalid in ({'density':float('nan')},{'mode':None},{'mode':'unsupported'}):
            with self.assertRaises(ValueError):
                lighting.apply_preset(self.p,self.m,'night',invalid)
            self.assertEqual(before,len(list(self.m.iter_nodes())))

    def test_gradient_fog_uses_csgo_keys_and_reuses_owned_entity(self):
        self.room()
        lighting.apply_preset(self.p,self.m,'overcast',{'mode':'gradient','density':.55,'draw_distance':2200})
        fog=self.m.entities('env_gradient_fog')[0]
        self.assertEqual(fog['entity_properties']['fogmaxopacity'],'0.55')
        self.assertEqual(float(fog['entity_properties']['fogend']),2200)
        self.assertEqual(fog['entity_properties']['heightfog'],'0')
        self.assertFalse(self.m.entities('env_volumetric_fog_controller'))
        lighting.apply_preset(self.p,self.m,'night','heavy')
        self.assertEqual(len(self.m.entities('env_gradient_fog')),1)
        self.assertEqual(fog['entity_properties']['fogmaxopacity'],'0.8')
        lighting.apply_preset(self.p,self.m,'night','none')
        self.assertEqual(fog['entity_properties']['StartDisabled'],'1')
