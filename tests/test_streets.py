import unittest
from unittest.mock import patch
from cs2map import terrain, streets, mesh
from cs2map.project import Project,OpError
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2,SETTINGS

@unittest.skipUnless(HAVE_CS2,'CS2 fixture needed')
class StreetRecipes(unittest.TestCase):
    def setUp(self):
        self.p=Project();self.m=VMap.from_template(SETTINGS.cs2_path,'empty')
        patcher=patch('cs2map.checks._asset_exists',return_value=True);patcher.start();self.addCleanup(patcher.stop)
        terrain.add_terrain(self.p,self.m,{'name':'flat','bounds':[[-1500,-1500],[1500,1500]],'cell':128},{})
    def add(self,nm,path,**kw):return streets.add_street(self.p,self.m,{'name':nm,'terrain':'flat','path':path,'width':192,**kw},{})
    def test_grade_solid_and_lots(self):
        out=self.add('up',[[-900,0,16],[900,0,128]],z='path',sidewalk={'width':64,'curb':6})
        self.assertGreater(out['max_slope'],0);self.assertGreater(len(out['lots']),0)
        self.assertEqual(self.m.node(out['id'])['children'][-1]['entity_properties']['classname'],'point_nav_walkable')
        for nid in out['ids']:self.assertNotIn(-1,self.m.node(nid)['meshData']['edgeFaceIndices'])
        lots=out['lots'];self.assertTrue(all(v['mins'][0]<v['maxs'][0] and v['mins'][1]<v['maxs'][1] for v in lots))
    def test_x_and_t_junctions_remove_sidewalk_across_road(self):
        self.add('east',[[-900,0],[900,0]])
        out=self.add('north',[[0,-900],[0,900]])
        self.assertTrue(out['junctions_open'])
        from cs2map.checks import _triangles
        # At crossing centre every upward face is at road Z=2, never a curb at Z=8.
        from cs2map.playtest import CameraGeometry
        hit=CameraGeometry(self.m).nearest([0,0,100],[0,0,-1],floors=True)
        self.assertIsNotNone(hit)
        before=len(list(self.m.iter_nodes()))
        with self.assertRaises(OpError):self.add('bad',[[-900,-100,32],[900,100,32]],z='path')
        self.assertEqual(before,len(list(self.m.iter_nodes())))
    def test_polygon_difference_area(self):
        outer=[[0,0],[10,0],[10,10],[0,10]];cut=[[3,-1],[7,-1],[7,11],[3,11]]
        pieces=streets.subtract(outer,cut)
        self.assertAlmostEqual(sum(abs(mesh._polygon_area(p)) for p in pieces),60)
    def test_bad_slope_or_unknown_fields_do_not_attach(self):
        before=len(list(self.m.iter_nodes()))
        with self.assertRaises(OpError):self.add('bad',[[-100,0,16],[100,0,100]],z='path',max_slope=12)
        with self.assertRaises(OpError):self.add('bad',[[-900,0],[900,0]],furniture={'wat':True})
        self.assertEqual(before,len(list(self.m.iter_nodes())))
