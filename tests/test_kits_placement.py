"""Real VMAP/triangle placement and extension preprocessing, with native props."""
import copy
import itertools
import math
import unittest
from unittest.mock import patch
from cs2map import kits, mesh, structure, visual, layout
from cs2map.project import Project, OpError
from cs2map.vmap import VMap


class KitPlacement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=Project()
        cls.fixture=kits.part(cls.p,'street_common/bench_urban')
        cls.fixture['bounds']=[[-7,-11,-3],[19,31,53]]
        cls.fixture['size']=[26,42,56]

    def setUp(self):
        self.m=VMap.from_template(self.p.s.cs2_path,'empty')
        self.m.attach(self.m.new_mesh(mesh.box([-500,-500,-16],[500,500,24])))

    def localpart(self,mount='wall',front='+x',**kwargs):
        return {**copy.deepcopy(self.fixture),'mount':mount,'front':front,**kwargs}

    def place(self,item,**kwargs):
        with patch.object(kits,'part',return_value=item):
            return kits.place(self.p,self.m,'fixture/test',**kwargs)

    def test_wall_rear_and_observed_front_all_local_axes(self):
        for front, normal in itertools.product(kits.FRONTS,[[1,0,0],[-1,0,0],[0,1,0],[0,-1,0]]):
            item=self.localpart(front=front)
            node=self.place(item,at=[100,150,70],normal=normal,scale=1.5,offset=2)
            face=structure._rotate(kits.FRONTS[front],node['angles'])
            self.assertGreater(kits._dot(face,normal),.9999)
            world=[tuple(v[i]+node['origin'][i] for i in range(3)) for v in kits._corners(item['bounds'],node['angles'],1.5)]
            rear=min(kits._dot(v,normal) for v in world)
            self.assertAlmostEqual(rear,kits._dot([100,150,70],normal)+2)

    def test_tilted_wall_and_up(self):
        item=self.localpart(front='-y')
        normal=kits._unit([1,1,.5],'normal')
        node=self.place(item,at=[10,20,30],normal=normal,up=[0,0,1])
        self.assertGreater(kits._dot(structure._rotate(kits.FRONTS['-y'],node['angles']),normal),.9999)
        points=kits._corners(item['bounds'],node['angles'],1)
        self.assertAlmostEqual(min(kits._dot([v[i]+node['origin'][i] for i in range(3)],normal) for v in points),kits._dot([10,20,30],normal))

    def test_floor_ray_not_requested_altitude(self):
        item=self.localpart(mount='floor')
        node=self.place(item,at=[0,0,200],normal=[0,1,0],scale=2,offset=0)
        bounds=visual._transformed(item['bounds'],node['origin'],node['angles'],node['scales'])
        self.assertAlmostEqual(bounds[0][2],24)
        self.assertAlmostEqual((bounds[0][0]+bounds[1][0])/2,0)
        self.assertAlmostEqual((bounds[0][1]+bounds[1][1])/2,0)

    def test_floor_absent_and_visual_mesh_not_support(self):
        item=self.localpart(mount='floor')
        with self.assertRaises(OpError):self.place(item,at=[1000,1000,0])
        self.m.attach(self.m.new_mesh(mesh.box([900,900,-16],[1100,1100,0]), options={'physicsType':'none'}))
        with self.assertRaises(OpError):self.place(item,at=[1000,1000,0])
        node=self.place(item,at=[1000,1000,0],snap_floor=False)
        self.assertAlmostEqual(node['origin'][2],3)

    def test_ceiling_top_and_hang_origin(self):
        item=self.localpart(mount='ceiling')
        node=self.place(item,at=[10,20,160],offset=2)
        self.assertAlmostEqual(visual._transformed(item['bounds'],node['origin'],node['angles'])[1][2],158)
        node=self.place(self.localpart(mount='hang'),at=[10,20,160],normal=[0,1,0],offset=2)
        self.assertEqual(list(node['origin']),[10,22,160])

    def test_jitter_is_repeatable_and_refused_on_walls(self):
        item=self.localpart(mount='floor')
        first=self.place(item,at=[0,0,100],yaw_jitter=45,seed=30)
        second=self.place(item,at=[0,0,100],yaw_jitter=45,seed=30)
        self.assertEqual(first['angles'],second['angles'])
        with self.assertRaises(OpError):self.place(self.localpart(),at=[0,0,100],yaw_jitter=1)

    def test_nonfinite_scale_zero_normal_and_collinear_up(self):
        for kwargs in [{'scale':False},{'scale':math.nan},{'normal':[0,0,0]},{'normal':[0,0,1]}, {'at':[0,0,math.inf]}]:
            with self.assertRaises(OpError):self.place(self.localpart(),**({'at':[0,0,0]}|kwargs))

    def test_layout_preprocessor_keeps_inward_wall_normal(self):
        # The first resolver must execute before the generic layout resolver.
        spec={'room':'hall','wall':'+y','u':.5,'h':0}
        with patch.object(layout,'point',return_value=([0,500,24],[0,-1,0])):
            op=kits.preprocess(self.p,self.m,{'op':'add_kit_part','part':'street_common/bench_urban','at':spec},{})
        self.assertEqual(op['facing'],[0,-1,0])
        self.assertTrue(op['_kit_wall_anchor'])
        item=self.localpart(mount='floor',front='+y')
        with patch.object(kits,'part',return_value=item):
            out=kits.add_kit_part(self.p,self.m,{**op,'ref':'bench'},{})
        node=self.m.node(out['id']);bounds=visual._transformed(item['bounds'],node['origin'],node['angles'])
        self.assertAlmostEqual(bounds[1][1],500)
        self.assertAlmostEqual(bounds[0][2],24)

    def test_unknown_op_fields_and_refs(self):
        with self.assertRaises(OpError):kits.add_kit_part(self.p,self.m,{'op':'add_kit_part','part':'street_common/bench_urban','at':[0,0,0],'typo':1},{})
        refs={}
        out=kits.add_kit_part(self.p,self.m,{'op':'add_kit_part','part':'street_common/bench_urban','at':[0,0,24],'ref':'b'},refs)
        self.assertEqual(out['id'],refs['b'])


if __name__=='__main__':unittest.main()
