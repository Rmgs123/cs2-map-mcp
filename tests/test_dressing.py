"""V: conservative placement against real authored meshes, not mocked rays."""
import json
import math
import unittest
from unittest.mock import patch

from cs2map import dressing, dmx, mesh, shapes
from cs2map.project import OpError, Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, check_topology


class DressingGeometry(unittest.TestCase):
    def test_closed_tubes_at_arbitrary_angles(self):
        for end in [(0,0,100),(100,0,0),(10,50,75)]:
            tube=dressing.tube([0,0,0],end,3,'mat',12)
            check_topology(self,tube.to_dmx())
            direction=dressing.structure._unit(end,'end')
            self.assertLess(sum(a*b for a,b in zip(tube.normal(tube.faces[0]),direction)),-.99)
            self.assertGreater(sum(a*b for a,b in zip(tube.normal(tube.faces[1]),direction)),.99)

    def test_rotated_asymmetric_bounds(self):
        b=dressing.transformed([[-2,-10,-3],[8,20,15]],[50,70,90],[0,90,0])
        for actual,expected in zip(b[0]+b[1],[30,68,87,60,78,105]):self.assertAlmostEqual(actual,expected)

    def test_bad_bounds_and_theme_fail_closed(self):
        for value in ({'mins':[0,0,0],'maxs':[0,1,1]},[[0,0,0],[1,2,math.nan]],[[False,0,0],[1,1,1]]):
            with self.assertRaises(OpError):dressing.box_bounds(value)
        for theme in ('../warehouse','unknown',None):
            with self.assertRaises(OpError):dressing.theme_info(theme)

    def test_trim_interval_subtraction(self):
        self.assertEqual(dressing._segments(0,100,[[-10,10],[40,60],[90,110]]),[(10,40),(60,90)])

    def test_seven_palettes_have_measured_extents_and_no_dev_history(self):
        info=dressing.theme_info('all')
        self.assertEqual(len(info['themes']),7)
        for data in info['themes']:
            self.assertEqual(set(data['materials']),{'floor','walls','ceiling','trim','metal','wood'})
            for path in data['materials'].values():self.assertNotIn('/dev/',path)
            for prop in data['props']:
                self.assertNotIn('/history/',prop['model'])
                self.assertTrue(all(x>0 for x in prop['size']))


@unittest.skipUnless(HAVE_CS2,'CS2 install required for template/catalog')
class DressingPlacement(unittest.TestCase):
    def setUp(self):
        self.p=Project(); self.m=VMap.from_template(self.p.s.cs2_path,'empty')
        self.room={'mins':[-256,-256,0],'maxs':[256,256,192]}
        self.base={'op':'dress_room','room':self.room,'density':.8,'seed':3,'lights':False,'decals':False,'details':False}
        self.add_room()

    def add_room(self,floor=True):
        spec={'shape':'room',**self.room,'floor':floor,'thickness':8,
              'openings':[{'wall':'+x','center':128,'width':96,'height':112,'bottom':0}]}
        for piece in shapes.build(spec):self.m.attach(self.m.new_mesh(piece))

    def test_repeatable_clearance_openings_spawns_and_floor(self):
        self.m.attach(self.m.new_entity('info_player_terrorist',{},[-160,-160,8]))
        self.base['keep_clear']=[[[100,-250,0],[250,-100,120]]]
        a=dressing.dress_room(self.p,self.m,self.base,{})
        self.assertGreater(a['props'],0)
        props=[p for p in a['placed'] if p['kind']=='prop']
        self.assertLessEqual(sum(p['key']=='trash' for p in props),2)
        self.assertTrue(any(p['wall']=='+x' for p in a['portals']))
        for prop in props:
            self.assertTrue(dressing.inside(prop['bounds'],dressing.box_bounds(self.room)))
            self.assertAlmostEqual(prop['bounds'][0][2],.1,places=3)
            self.assertFalse(any(dressing.overlaps(prop['bounds'],z) for z in a['keep_clear']))
        for i,prop in enumerate(props):
            self.assertFalse(any(dressing.overlaps(prop['bounds'],p['bounds']) for p in props[i+1:]))
        m2=VMap(dmx.loads(self.m.to_bytes()))
        for p in props:m2.remove(m2.node(p['ids'][0]))
        b=dressing.dress_room(self.p,m2,self.base,{})
        self.assertEqual([(p['model'],p['origin']) for p in props],[(p['model'],p['origin']) for p in b['placed']])

    def test_no_floor_no_placement_and_zero_density_is_noop(self):
        self.m=VMap.from_template(self.p.s.cs2_path,'empty');self.add_room(False)
        res=dressing.dress_room(self.p,self.m,self.base,{})
        self.assertEqual(res['props'],0);self.assertGreater(res['skipped'].get('no_floor',0),0)
        before=len(list(self.m.iter_nodes()))
        res=dressing.dress_room(self.p,self.m,{**self.base,'density':0,'lights':True,'decals':True},{})
        self.assertEqual(res['count'],0);self.assertEqual(before,len(list(self.m.iter_nodes())))

    def test_existing_solids_excluded(self):
        self.m.attach(self.m.new_mesh(mesh.box([-256,-256,0],[-64,-64,100])))
        result=dressing.dress_room(self.p,self.m,self.base,{})
        for prop in result['placed']:
            self.assertFalse(dressing.overlaps(prop['bounds'],[[-256,-256,0],[-64,-64,100]]))

    def test_trim_preserves_doorway_and_roundtrip(self):
        door=self.m.new_entity('func_door',{},[256,128,56]);self.m.attach(door)
        self.m.attach(self.m.new_mesh(mesh.box([252,80,0],[260,176,112])),door)
        result=dressing.add_trim(self.p,self.m,{'op':'add_trim','room':self.room,'walls':['+x']},{})
        self.assertEqual(result['count'],2)
        for item in result['placed']:
            check_topology(self,self.m.node(item['ids'][0])['meshData'])
            b=dressing.checks._mesh_bounds(self.m.node(item['ids'][0]))
            self.assertFalse(b[0][1]<128<b[1][1])
        VMap(dmx.loads(self.m.to_bytes()))

    def test_bad_input_does_not_mutate_map(self):
        for field,value in [('density',1.1),('seed',True),('lights',1),('keep_clear','box'),('max_props',1.5),('typo',0)]:
            before=self.m.to_bytes()
            with self.assertRaises(OpError):dressing.dress_room(self.p,self.m,{**self.base,field:value},{})
            self.assertEqual(before,self.m.to_bytes())

    def test_cable_sag_and_refs(self):
        refs={}
        result=dressing.add_cables(self.p,self.m,{'op':'add_cables','start':[-100,0,160],
             'end':[100,0,160],'sag':20,'segments':12,'ref':'wire'},refs)
        self.assertEqual(result['count'],12);self.assertEqual(refs['wire'],result['ids'][0])
        for item in result['placed']:check_topology(self,self.m.node(item['ids'][0])['meshData'])
        self.assertLess(min(dressing.checks._mesh_bounds(self.m.node(i))[0][2] for i in result['ids']),141)
        VMap(dmx.loads(self.m.to_bytes()))

    def test_named_layout_rooms_move_and_disambiguate(self):
        data={'version':1,'name':'wh','origin':[0,0,0],'rooms':{'hall':{'interior':self.room}}}
        group=self.m.new_group('layout',[10,20,30]);group.set('cs2map_layout','string',json.dumps(data));self.m.attach(group)
        self.assertEqual(dressing.room_bounds(self.m,'hall'),([-246,-236,30],[266,276,222]))
        VMap(dmx.loads(self.m.to_bytes()))
        group2=self.m.new_group('second');group2.set('cs2map_layout','string',json.dumps({**data,'name':'other'}));self.m.attach(group2)
        with self.assertRaisesRegex(OpError,'ambiguous'):dressing.room_bounds(self.m,'hall')
        self.assertEqual(dressing.room_bounds(self.m,'hall','wh')[0],[-246,-236,30])
        group['angles']=(0,90,0)
        with self.assertRaisesRegex(OpError,'rotated'):dressing.room_bounds(self.m,'hall','wh')
