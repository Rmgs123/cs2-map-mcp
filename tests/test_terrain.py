import json, math, unittest
from pathlib import Path
from unittest.mock import patch
from cs2map import terrain, mesh, dmx
from cs2map.project import Project, OpError
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS

@unittest.skipUnless(HAVE_CS2,'CS2 fixture needed')
class TerrainRecipes(unittest.TestCase):
    def setUp(self):
        self.p=Project();self.m=VMap.from_template(SETTINGS.cs2_path,'empty')
        patcher=patch('cs2map.checks._asset_exists',return_value=True);patcher.start();self.addCleanup(patcher.stop)
    def test_chunks_share_exact_edges_and_are_closed(self):
        out=terrain.add_terrain(self.p,self.m,{'name':'hills','bounds':[[0,0],[4200,4200]],'cell':64,
           'ops':[{'op':'noise','amplitude':32,'scale':400,'seed':4}]},{})
        self.assertEqual(out['chunks'],4)
        chunks=[self.m.node(i) for i in out['ids']]
        def vertices(n):return {tuple(round(v[k]+n['origin'][k],3) for k in range(3)) for v in n['meshData']['vertexData']['streams'][0]['data']}
        self.assertGreaterEqual(len(vertices(chunks[0])&vertices(chunks[2])),65)
        for c in chunks:self.assertNotIn(-1,c['meshData']['edgeFaceIndices'])
        loaded=VMap(dmx.loads(self.m.to_bytes()));self.assertEqual(terrain.select(loaded,'hills')['cells'],[66,66])
    def test_sample_matches_triangle_not_bilinear(self):
        d={'bounds':[[0,0],[32,32]],'cells':[1,1],'heights':[[0,0],[0,32]]}
        self.assertAlmostEqual(terrain.sample(d,24,8)['z'],8)
        self.assertAlmostEqual(terrain.sample(d,8,24)['z'],8)
        self.assertAlmostEqual(terrain.sample(d,8,8)['z'],8)
    def test_ops_plateau_valley_ridge_flatten_and_seed(self):
        op={'name':'h','bounds':[[-512,-512],[512,512]],'cell':32,'ops':[
            {'op':'hill','center':[0,0],'radius':500,'height':160},
            {'op':'valley','center':[-300,-300],'radius':100,'height':20},
            {'op':'ridge','path':[[-400,300],[400,300]],'width':200,'height':40},
            {'op':'noise','amplitude':8,'scale':400,'seed':4},
            {'op':'plateau','polygon':[[-96,-96],[96,-96],[96,96],[-96,96]],'z':64,'falloff':64},
            {'op':'flatten','path':[[-400,-300],[400,-300]],'width':128,'z':8,'falloff':32}]}
        a=terrain.plan(self.p,op);self.assertEqual(a,terrain.plan(self.p,op));self.assertEqual(terrain.sample(a,0,0)['z'],64)
        self.assertEqual(terrain.sample(a,0,-300)['z'],8)
    def test_validation_is_atomic_and_strict(self):
        for spec in [{'cell':True},{'cell':1},{'cell':float('nan')},{'ops':[{'op':'unknown'}]},{'ops':[{'op':'noise','seed':.1}]},
                     {'ops':[{'op':'plateau','polygon':[[0,0],[128,128],[0,128],[128,0]],'z':0}]}]:
            with self.assertRaises(OpError):terrain.add_terrain(self.p,self.m,{'name':'bad','bounds':[[0,0],[128,128]],**spec},{})
            self.assertEqual(len(list(self.m.iter_nodes())),0)
    def test_blend_stream_and_area_weighted_steep_fraction(self):
        out=terrain.add_terrain(self.p,self.m,{'name':'hill','bounds':[[0,0],[512,512]],'cell':64,'ops':[
            {'op':'hill','center':[256,256],'radius':240,'height':600}]},{})
        self.assertGreater(out['steep_fraction'],.1)
        md=self.m.node(out['ids'][0])['meshData'];s=next(s for s in md['faceVertexData']['streams'] if s.name=='VertexPaintBlendParams:0')
        self.assertEqual(s['vertexBufferLocation'],1);self.assertEqual(len(s['data']),len(md['edgeVertexIndices']))
        self.assertTrue(all(0<=v[0]<=1 and v[1:]==(0.,0.,0.) for v in s['data']))
    def test_heightmap_and_ground_preprocessor(self):
        from PIL import Image
        file=Path('.local/scratch/T/test_heightmap.png');file.parent.mkdir(parents=True,exist_ok=True)
        image=Image.new('L',(2,2));image.putdata([0,255,0,255]);image.save(file)
        terrain.add_terrain(self.p,self.m,{'name':'hills','bounds':[[0,0],[64,64]],'cell':32,'heightmap':str(file.resolve()),'height_range':[10,74]},{})
        self.assertEqual(terrain.sample(terrain.select(self.m,'hills'),64,0)['z'],74)
        op=terrain.preprocess(self.p,self.m,{'op':'add_entity','origin':{'terrain':'hills','at':[0,0],'z':'ground','offset':8}},{})
        self.assertEqual(op['origin'],[0,0,18])
    def test_moved_group_and_duplicates_fail_closed(self):
        out=terrain.add_terrain(self.p,self.m,{'name':'hills','bounds':[[0,0],[128,128]],'cell':64},{})
        self.p._apply(self.m,{'op':'move','id':out['id'],'offset':[100,200,20]}, {})
        self.assertEqual(terrain.sample(terrain.select(self.m,'hills'),164,264)['z'],20)
        self.m.attach(self.m.clone(self.m.node(out['id'])))
        with self.assertRaises(OpError):terrain.select(self.m,'hills')
    def test_scatter_density_zero_and_protected_corridors(self):
        terrain.add_terrain(self.p,self.m,{'name':'flat','bounds':[[-1000,-1000],[1000,1000]],'cell':128},{})
        self.m.attach(self.m.new_entity('info_player_terrorist',{},[0,0,8]))
        model='models/props/de_aztec/hr_aztec/aztec_stairs/aztec_river_rock_01.vmdl'
        result=terrain.scatter(self.p,self.m,{'name':'none','terrain':'flat','models':[model],'density':0},{})
        self.assertEqual(result['placed'],0)
        out=terrain.scatter(self.p,self.m,{'name':'rocks','terrain':'flat','models':[model],'density':20,'seed':7,
             'route':[[-900,400],[900,400]],'keep_clear':[[[-100,-100],[100,100]]],'min_distance':64},{})
        self.assertEqual(out['target'],80);self.assertEqual(out['placed'],80)
        nodes=[self.m.node(i) for i in out['ids']]
        for n in nodes:
            x,y=n['origin'][:2];self.assertGreater(math.hypot(x,y),128);self.assertGreater(abs(y-400),64)
            self.assertEqual(n['entity_properties']['solid'],'0')
        self.assertEqual(len(self.m.node(out['id'])['children']),80)
