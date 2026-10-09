"""K layout: geometric invariants, persistence, preprocessing and lift regression."""
import copy
import json
import unittest
from unittest.mock import patch

from tests.helpers import HAVE_CS2, check_topology
from cs2map import checks, dmx, layout, physics
from cs2map.project import OpError, Project
from cs2map.vmap import VMap


def warehouse():
    return {'op':'add_layout', 'name':'wh', 'wall':16, 'levels':{'ground':0,'upper':256}, 'rooms':[
        {'id':'back','size':[320,320,176]},
        {'id':'corr','size':[560,192,160],'from':'back','side':'+x'},
        {'id':'hall','size':[1024,960,448],'from':'corr','side':'+x'},
        {'id':'yard','size':[640,1280],'from':'hall','side':'+x','outdoor':True},
        {'id':'mezz','size':[256,960,192],'in':'hall','at':[.75,0], 'level':'upper','outdoor':True}],
        'openings':[{'between':['back','corr'],'width':96,'height':112,
                     'door':{'targetname':'k_door','open_by':'script'}},
                    {'between':['corr','hall'],'width':128,'height':128},
                    {'between':['hall','yard'],'width':128,'height':128},
                    {'room':'hall','wall':'+y','at':.3,'bottom':300,'width':112,'height':96}]}


class LayoutPlan(unittest.TestCase):
    def test_union_has_one_shared_wall_and_no_interpenetration(self):
        data = layout.plan(warehouse()); solids = layout.solids(data)
        for i, a in enumerate(solids):
            for b in solids[i+1:]:
                self.assertFalse(layout._overlap(a,b), (a,b))
        hall = data['rooms']['hall']; corr = data['rooms']['corr']
        self.assertEqual(hall['interior']['mins'][0]-corr['interior']['maxs'][0],16)
        # Through opening: no wall block; elsewhere: exactly one 16-unit layer.
        hole = data['openings'][1]['bounds']
        self.assertFalse(any(layout._overlap(s,hole) for s in solids))
        sample = [(hole['mins'][0]+hole['maxs'][0])/2,hole['maxs'][1]+8,40]
        self.assertEqual(sum(checks._inside(sample,(s['mins'],s['maxs'])) for s in solids),1)
        mezz = data['rooms']['mezz']
        self.assertEqual(mezz['floor_z'],256)
        self.assertEqual({s['part'] for s in solids if s['room']=='mezz'}, {'floor'})

    def test_all_attachment_sides_and_align(self):
        for side in layout.SIDES:
            for align in ('start','center','end',.25):
                spec={'op':'add_layout','name':'a','rooms':[{'id':'a','size':[320,320,160]},
                     {'id':'b','size':[128,192,128],'from':'a','side':side,'align':align}],
                     'openings':[{'between':['a','b'],'width':64,'height':100}]}
                with self.subTest(side=side,align=align):
                    p=layout.plan(spec)
                    self.assertEqual(p['openings'][0]['wall'],side)
                    self.assertFalse(any(layout._overlap(s,p['openings'][0]['bounds']) for s in layout.solids(p)))

    def test_invalid_opening_or_plan_refused_before_nodes(self):
        cases=[]
        bad=warehouse();bad['openings'][0]['width']=300;cases.append(bad)
        bad=warehouse();bad['openings'][1]['height']=180;cases.append(bad)
        bad=warehouse();bad['openings'].append(copy.deepcopy(bad['openings'][0]));cases.append(bad)
        bad=warehouse();bad['rooms'][1]['from']='later';cases.append(bad)
        bad=warehouse();bad['rooms'][0]['size'][0]=float('nan');cases.append(bad)
        bad=warehouse();bad['rooms'][1]['origin']=[0,0,0];cases.append(bad)
        bad=warehouse();bad['rooms'][0]['outdoor']='false';cases.append(bad)
        for spec in cases:
            with self.subTest(spec=spec),self.assertRaises(OpError):layout.plan(spec)

    def test_stacked_windows_and_external_shared_opening(self):
        spec=warehouse();spec['openings'][0]={'room':'back','wall':'+x','width':96,'height':112}
        spec['openings'].append({'room':'hall','wall':'+y','at':.3,'bottom':180,'width':112,'height':80})
        data=layout.plan(spec)
        self.assertEqual(data['openings'][0]['between'],['back','corr'])
        solids=layout.solids(data)
        self.assertFalse(any(layout._overlap(s,h['bounds']) for s in solids for h in data['openings']))

    def test_swept_triangle_avoids_wedge_aabb_false_positive(self):
        tri=[(0,0,0),(128,0,128),(128,128,128)]
        self.assertFalse(layout.swept_triangle(layout._box([0,80,70],[16,96,80]),[0,0,5],tri))
        self.assertTrue(layout.swept_triangle(layout._box([50,20,0],[60,30,8]),[0,0,100],tri))
        floor=[(0,0,0),(256,0,0),(256,256,0)]
        self.assertFalse(layout.swept_triangle(layout._box([10,10,0],[30,30,8]),[0,0,100],floor))

    def test_enclosed_second_floor_has_one_slab(self):
        data=layout.plan({'op':'add_layout','name':'stack','levels':{'lower':0,'upper':192},'rooms':[
            {'id':'lower','size':[320,320,176],'level':'lower'},
            {'id':'upper','size':[320,320,176],'level':'upper'}]})
        solids=layout.solids(data)
        point=[160,160,184]
        self.assertEqual(sum(checks._inside(point,(s['mins'],s['maxs'])) for s in solids),1)
        self.assertFalse(any(layout._overlap(a,b) for i,a in enumerate(solids) for b in solids[i+1:]))


@unittest.skipUnless(HAVE_CS2,'Valve FGD and map template required')
class LayoutMap(unittest.TestCase):
    def setUp(self):
        self.p=Project();self.m=VMap.from_template(self.p.s.cs2_path,'basic');self.refs={}
        self.p.register_op('add_layout',layout.add_layout)
        self.p.register_op('add_lift',layout.add_lift)
        for name,fn in physics.OPERATIONS.items():self.p.register_op(name,fn)
        self.p.register_preprocessor(layout.preprocess)

    def apply(self,op):return self.p._apply(self.m,op,self.refs)

    def test_persisted_plan_and_exact_door_opening(self):
        r=self.apply(warehouse());self.assertEqual(len(r['rooms']),5)
        for nid in r['mesh_ids']:check_topology(self,self.m.node(nid)['meshData'])
        self.m=VMap(dmx.loads(self.m.to_bytes()))
        data=layout.plans(self.m)[0];hole=data['openings'][0]
        self.assertEqual(layout._entity_bounds(self.m.node(hole['door_id'])),hole['bounds'])
        self.assertEqual(layout.point(self.m,{'room':'mezz','at':[.5,.5],'z':'floor'})[0][2],256)
        with self.assertRaisesRegex(OpError,'already exists'):self.apply(warehouse())

    def test_recursive_points_story_boxes_wall_normals_and_numeric_compatibility(self):
        self.apply(warehouse())
        story={'op':'add_story','name':'test','origin':{'room':'back','at':[.2,.3],'z':8},'stages':[
             {'until':{'zone':{'room':'hall','box':[[.1,.2,0],[.3,.4,96]]}},
              'then':[{'teleport':{'room':'mezz','at':[.5,.5],'z':8}}]}]}
        original=copy.deepcopy(story)
        actual=layout.preprocess(self.p,self.m,story,{})
        self.assertEqual(story,original)
        self.assertEqual(actual['origin'],[64.,96.,8.])
        zone=actual['stages'][0]['until']['zone'];self.assertEqual(zone['maxs'][2],96)
        self.assertEqual(actual['stages'][0]['then'][0]['teleport'][2],264)
        sign=layout.preprocess(self.p,self.m,{'op':'add_sign','origin':{
             'room':'hall','wall':'+x','u':.3,'h':330,'offset':1}}, {})
        self.assertEqual(sign['normal'],[-1,0,0]);self.assertEqual(sign['origin'][2],330)
        numeric={'op':'add_entity','origin':[1,2,3],'keyvalues':{'text':'room hall'}}
        self.assertEqual(layout.preprocess(self.p,self.m,numeric,{}),numeric)
        box={'op':'add_mesh','shape':{'shape':'box','room':'hall','box':[[0,0,0],[.1,.1,64]]}}
        self.assertNotIn('room',layout.preprocess(self.p,self.m,box,{})['shape'])
        with self.assertRaisesRegex(OpError,'available: back, corr, hall, mezz, yard'):
            layout.point(self.m,{'room':'hal','at':[.5,.5]})

    def test_translation_is_followed_and_ambiguous_ids_fail_closed(self):
        result=self.apply(warehouse())
        self.apply({'op':'move','id':result['id'],'offset':[100,200,300]})
        self.assertEqual(layout.point(self.m,{'room':'back','at':[0,0],'z':'floor'})[0],[100,200,300])
        second=warehouse();second['name']='other';second['origin']=[5000,0,0]
        second['openings'][0]['door']=None
        self.apply(second)
        with self.assertRaisesRegex(OpError,'ambiguous'):layout.point(self.m,{'room':'hall','at':[.5,.5]})
        self.assertEqual(layout.point(self.m,{'layout':'other','room':'back','at':[0,0]})[0],[5000,0,0])

    def test_lift_docks_beside_slab_with_ride_and_call_buttons_and_graph(self):
        self.apply(warehouse())
        r=self.apply({'op':'add_lift','room':'hall','upper':'mezz','targetname':'k_lift','ref':'lift'})
        self.assertEqual(r['bounds']['maxs'][0]+r['gap'],layout.plans(self.m)[0]['rooms']['mezz']['interior']['mins'][0])
        self.assertEqual(r['bounds']['maxs'][2]+r['to'][2],256)
        self.assertEqual(self.refs['lift'],r['id'])
        self.assertEqual([self.m.node(b['id'])['connectionsData'][0]['inputName'] for b in r['buttons']],['Open','Close','Close','Open'])
        self.assertEqual([b['targetname'] for b in r['buttons']][2:],['k_lift_call_lower','k_lift_call_upper'])
        self.apply({'op':'add_entity','classname':'info_player_terrorist','origin':{'room':'back','at':[.5,.5],'z':8}})
        self.assertEqual(layout.check_layout(self.p,self.m),[])

    def test_regression_original_f_lift_slab_and_embedded_button(self):
        self.apply(warehouse());upper=layout.plans(self.m)[0]['rooms']['mezz'];lo=upper['interior']['mins']
        self.apply({'op':'add_mover','targetname':'k_bad','mins':[lo[0]+10,lo[1]+10,0],
                    'maxs':[lo[0]+110,lo[1]+110,8],'to':[0,0,256],'call':'script'})
        self.apply({'op':'add_button','mins':[330,70,40],'maxs':[335,80,56],
                    'targets':[{'name':'k_bad','input':'Open'}]})
        codes={x['code'] for x in layout.check_layout(self.p,self.m)}
        self.assertIn('layout_mover_world',codes)
        self.assertIn('layout_button_in_wall',codes)

    def test_lift_preflight_refuses_rider_ceiling_obstruction(self):
        self.apply(warehouse());r=layout.plans(self.m)[0]['rooms']['mezz'];x=r['interior']['mins'][0]
        self.apply({'op':'add_mesh','shape':{'shape':'box','mins':[x-200,-100,280],'maxs':[x-1,400,296]}})
        with self.assertRaisesRegex(OpError,'platform/player sweep'):
            self.apply({'op':'add_lift','room':'hall','upper':'mezz','targetname':'k_blocked'})
        self.assertEqual(self.m.entities(targetname='k_blocked'),[])

    def test_door_displacement_and_small_opening_checks(self):
        spec=warehouse();spec['openings'][1]['width']=24
        r=self.apply(spec)
        door=r['openings'][0]['door_id'];self.apply({'op':'move','id':door,'offset':[10,0,0]})
        codes={x['code'] for x in layout.check_layout(self.p,self.m)}
        self.assertIn('layout_opening_small',codes);self.assertIn('layout_door_outside_opening',codes)

    def test_atomic_batch_relative_failure_and_dry_run(self):
        with patch.object(self.p,'load',return_value=self.m),patch.object(self.p,'save') as save:
            with self.assertRaisesRegex(OpError,'Nothing was saved'):
                self.p.map_edit('k_unit',[warehouse(),{'op':'add_entity','classname':'info_player_terrorist',
                    'origin':{'room':'missing','at':[.5,.5]}}])
            save.assert_not_called()

    def test_known_light_carrier_is_not_a_player_lift(self):
        from cs2map import lighting
        self.apply(warehouse())
        lighting.add_light(self.p,self.m,{'kind':'ceiling','origin':[500,160,154],
                                          'name':'k_switch_light','start_off':True},self.refs)
        self.assertNotIn('layout_mover_world',{x['code'] for x in layout.check_layout(self.p,self.m)})

    def test_sign_wall_contact_permitted_penetration_detected(self):
        from cs2map import mesh
        self.apply(warehouse())
        group=self.m.new_group('sign_k_fixture');self.m.attach(group)
        sign=self.m.new_mesh(mesh.box([895,130,135],[896,190,155]),options={'physicsType':'none'})
        self.m.attach(sign,group)
        self.assertNotIn('layout_sign_in_wall',{x['code'] for x in layout.check_layout(self.p,self.m)})
        self.apply({'op':'move','id':group['nodeID'],'offset':[2,0,0]})
        self.assertIn('layout_sign_in_wall',{x['code'] for x in layout.check_layout(self.p,self.m)})

    def test_lift_docking_change_and_standalone_door_are_reported(self):
        self.apply(warehouse());lift=self.apply({'op':'add_lift','room':'hall','upper':'mezz'})
        self.m.set_keyvalues(self.m.node(lift['id']),{'movedistance':200})
        self.apply({'op':'add_door','mins':[200,200,0],'maxs':[208,260,112]})
        codes={x['code'] for x in layout.check_layout(self.p,self.m)}
        self.assertIn('layout_lift_docking',codes);self.assertIn('layout_door_outside_opening',codes)

    def test_nested_door_button_can_reference_the_new_plan(self):
        spec=warehouse();spec['openings'][0]['door']={'targetname':'k_relative_door','open_by':'button',
            'button':{'mins':{'room':'back','wall':'+y','u':.25,'h':64,'offset':14},
                      'maxs':{'room':'back','wall':'+y','u':.35,'h':96,'offset':1}}}
        self.apply(spec)
        button=self.m.entities(targetname='k_relative_door_button')[0]
        self.assertEqual(layout._entity_bounds(button),{'mins':[80,306,64],'maxs':[112,319,96]})

    def test_graph_warns_disconnected_room_not_script_teleport(self):
        spec=warehouse();spec['openings']=[]
        self.apply(spec)
        self.apply({'op':'add_entity','classname':'info_player_terrorist','origin':{'room':'back','at':[.5,.5],'z':8}})
        problems=layout.check_layout(self.p,self.m)
        self.assertEqual(sum(x['code']=='layout_room_unreachable' for x in problems),4)
