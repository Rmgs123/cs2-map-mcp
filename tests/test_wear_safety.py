"""Whole-area safety, reproducibility, serialization and layer deletion."""
import json
import random
import unittest
from unittest.mock import patch

from cs2map import checks, dmx, mesh, wear
from cs2map.project import OpError, Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


def rect_tri(a,b,c,d):
    return [[(a,c),(b,c),(b,d)],[(a,c),(b,d),(a,d)]]


class WearCoverage(unittest.TestCase):
    def test_internal_hole_and_duplicate_triangles_cannot_pass(self):
        # The centre and all corners have support. A tiny internal hole remains.
        tris=rect_tri(0,4,0,10)+rect_tri(5,10,0,10)+rect_tri(4,5,0,3)+rect_tri(4,5,4,10)
        self.assertFalse(wear._covered((1,9,1,9),tris*2))
        self.assertTrue(wear._covered((.1,3.9,.1,9.9),tris))

    def test_window_sign_house_edge_and_plinth_transition(self):
        face={'axis':1,'plane':0,'normal':[0,-1,0],'z0':0,'top':200}
        house={'from':0,'to':200,'top':200}
        planes={0:rect_tri(0,200,0,200),-2.5:rect_tri(0,200,0,24)}
        safe=lambda u,z,size,props=[]:wear._wall_patch(face,house,planes,props,u,z,size)
        self.assertIsNotNone(safe(60,12,(80,18)))
        self.assertIsNone(safe(60,26,(40,20)))
        self.assertIsNone(safe(5,80,(20,20)))
        sign=[{'rect':(40,80,80,100),'kind':'prop'}]
        self.assertIsNone(safe(90,90,(30,20),sign))
        self.assertIsNotNone(safe(125,90,(30,20),sign))

    def test_niche_and_arcade_back_wall_are_not_front_support(self):
        tris=[((0,0,0),(0,0,180),(80,0,180)),((0,0,0),(80,0,180),(80,0,0)),
              ((0,24,0),(0,24,180),(200,24,180))]
        planes=wear._planes([tuple(reversed(t)) for t in tris],1,-1,(0,2),-10,10)
        self.assertNotIn(24,planes)
        self.assertFalse(wear._covered((70,100,50,70),planes[0]))

    def test_floor_is_street_level_and_whole_quad_cannot_cross_a_step(self):
        # Roof 400 is excluded before support selection.
        tris=[((0,0,z),(100,0,z),(100,100,z)) for z in (0,6,400)]
        planes=wear._planes(tris,2,1,(0,1),-8,18)
        self.assertNotIn(400,planes)
        face={'axis':1,'plane':0,'normal':[0,1,0],'z0':0}
        floors={0:rect_tri(0,100,0,100),6:rect_tri(0,100,0,24)}
        self.assertIsNone(wear._ground_patch(face,50,20,(30,20),floors))
        self.assertIsNone(wear._ground_patch(face,50,28,(30,20),floors))
        self.assertEqual(wear._ground_patch(face,50,12,(30,18),floors)[2],6)
        self.assertIsNone(wear._ground_patch(face,50,12,(30,18),{}))
        self.assertIsNone(wear._ground_patch(face,50,12,(30,18),floors,rect_tri(0,20,0,24)))

    def test_strict_numeric_input(self):
        for value in (True,1.5,'7',2**63,float('nan')):
            with self.subTest(value=value),self.assertRaises(OpError):
                wear._integer(value,'seed',-(2**63),2**63-1)
        for value in (True,'1',float('inf'),-1,3):
            with self.subTest(value=value),self.assertRaises(OpError):wear._num(value,'density',0,2)


@unittest.skipUnless(HAVE_CS2,'Valve template needed')
class WearScene(unittest.TestCase):
    def setUp(self):
        self.p=Project(SETTINGS)   # deletes rely on the core DMX fix, no W preprocessor

    def scene(self,reverse=False,props=False,paving=True):
        m=VMap.from_template(SETTINGS.cs2_path,'empty')
        for index,name in enumerate(('b','a') if reverse else ('a','b')):
            plane=0 if name=='a' else 500
            h=m.new_group('house:'+name); m.attach(h)
            wall=m.new_mesh(mesh.box([0,plane,0],[400,plane+16,300],'materials/dev/reflectivity_50.vmat'))
            m.attach(wall,h)
            meta={'schema':2,'name':name,'axis':1,'plane':plane,'normal':[0,-1,0],'z0':0,'top':300,
                  'houses':[{'id':h['nodeID'],'from':0,'to':400,'top':300,'doors':[],'shops':[],'depth':100}]}
            row=m.new_group('facade_row:'+name); m.attach(row)
            row.set(wear.FACADE_KEY,'string',json.dumps(meta))
            if props:
                for model,origin in ((wear.facades.P['frame'][0],[120,plane,100]),
                                     (wear.facades.P['pipe'][0],[360,plane,120]),
                                     (wear.facades.P['sign_monaco'][0],[230,plane,130])):
                    n=m.new_entity('prop_static',{'model':model},origin=origin)
                    m.attach(n,h)
            floor=m.new_mesh(mesh.box([0,plane-100,-10],[400,plane,0],'materials/dev/reflectivity_50.vmat'))
            m.attach(floor)
        if paving:
            group=m.new_group('paving:test'); m.attach(group)
            group.set(wear.PAVING_KEY,'string',json.dumps({'schema':1,'name':'test','rooms':[],
                'sidewalks':[{'mins':[0,plane-100,-10],'maxs':[400,plane,0]} for plane in (0,500)]}))
        return m

    def apply(self,m,**kwargs):
        return wear.add_wear(self.p,m,{'op':'add_wear','name':'test','seed':7,**kwargs},{})

    def records(self,m):
        return sorted((json.loads(n.get('cs2map_wear_decal'))['role'],tuple(n['origin']),
                       tuple(n['meshData']['materials'])) for n in m.iter_nodes() if n.get('cs2map_wear_decal'))

    def test_same_seed_independent_of_row_order_and_global_random(self):
        a,b=self.scene(),self.scene(True)
        random.seed(999); self.apply(a)
        random.seed(123); self.apply(b)
        self.assertEqual(self.records(a),self.records(b))
        c=self.scene(); self.apply(c,seed=8)
        self.assertNotEqual(self.records(a),self.records(c))

    def test_row_subset_preserves_selected_house_seed(self):
        a,b=self.scene(),self.scene()
        self.apply(a); self.apply(b,rows=['a'])
        self.assertEqual([r for r in self.records(a) if r[1][1]<250],self.records(b))

    def test_density_zero_empty_group_and_increasing_density(self):
        m=self.scene(); result=self.apply(m,density=0)
        self.assertEqual(result['decals'],0)
        self.assertEqual(m.node(result['id']).get(Project.REF_KEY),'wear_test')
        signatures=[]
        for density in (.5,1,2):
            m=self.scene(); self.apply(m,density=density); signatures.append(set(self.records(m)))
        self.assertTrue(signatures[0]<=signatures[1]<=signatures[2])

    def test_ground_off_and_missing_paving(self):
        for m,kw in ((self.scene(),{'ground':False}),(self.scene(paving=False),{})):
            result=self.apply(m,**kw)
            self.assertFalse(result['ground'])
            self.assertFalse(any(r[0].startswith('ground_') for r in self.records(m)))

    def test_roundtrip_persistent_ref_and_delete_entire_layer(self):
        m=self.scene(); result=self.apply(m)
        saved=VMap(dmx.loads(m.to_bytes()))
        before,after=self.records(m),self.records(saved)
        self.assertEqual(len(before),len(after))
        for a,b in zip(before,after):
            self.assertEqual((a[0],a[2]),(b[0],b[2]))
            for x,y in zip(a[1],b[1]):self.assertAlmostEqual(x,y,delta=.0001)
        self.p._apply(saved,{'op':'delete','id':'@wear_test'},{})
        self.assertEqual(self.records(saved),[])
        self.assertFalse(any(n.name=='wear:test' for n in saved.iter_nodes()))
        serialized=dmx.loads(saved.to_bytes())
        self.assertFalse(any(e.type=='CMapStaticOverlay' or e.get(wear.WEAR_KEY)
                             for e in serialized.element_order))
        self.assertGreater(result['decals'],0)

    def test_delete_and_replay_in_same_batch_reuses_ref(self):
        m=self.scene(); refs={}
        wear.add_wear(self.p,m,{'op':'add_wear','name':'test'},refs)
        self.p._apply(m,{'op':'delete','id':'@wear_test'},refs)
        result=wear.add_wear(self.p,m,{'op':'add_wear','name':'test'},refs)
        self.assertGreater(result['decals'],0)
        self.assertEqual(len([n for n in m.iter_nodes() if n.name=='wear:test']),1)

    def test_collision_and_missing_row_do_not_mutate(self):
        m=self.scene(); before=len(list(m.iter_nodes()))
        node=m.new_group('existing'); m.attach(node); node.set(Project.REF_KEY,'string','wear_test')
        for args in ({},{'name':'other','rows':['absent']},{'name':'other','seed':1.5}):
            with self.subTest(args=args),self.assertRaises(OpError):self.apply(m,**args)
        self.assertEqual(len(list(m.iter_nodes())),before+1)

    def test_all_prop_bounds_protected_and_actual_anchor_roles(self):
        m=self.scene(props=True)
        def bounds(p,model):
            return ((-2,-18,0),(2,18,64))
        with patch.object(wear.facades,'_model_bounds',side_effect=bounds):
            self.apply(m,density=2)
            metadata=wear._facades(m,None)
            for row in metadata:
                h=row['houses'][0]; face,_,_,props,_,_=wear._house_plan(self.p,m,row,h,[])
                for n in m.iter_nodes():
                    if not n.get('cs2map_wear_decal'):continue
                    item=json.loads(n.get('cs2map_wear_decal'))
                    if item['role'].startswith('ground_') or abs(item['surface'][1]-face['plane'])>1:continue
                    u,z=item['surface'][0],item['surface'][2]; w,ht=item['size']
                    r=(u-w/2,u+w/2,z-ht/2,z+ht/2)
                    self.assertFalse(any(wear._intersects(r,p['rect']) for p in props))
                    if item['role']=='leak':self.assertIn(item['anchor'],('sill','downpipe'))

    def test_unknown_bounds_fail_before_group_creation(self):
        m=self.scene(props=True); before=len(list(m.iter_nodes()))
        with patch.object(wear.facades,'_model_bounds',side_effect=OpError('unmeasured')):
            with self.assertRaises(OpError):self.apply(m)
        self.assertEqual(len(list(m.iter_nodes())),before)

    def test_recessed_plane_cannot_substitute_for_front(self):
        m=self.scene()
        row=wear._facades(m,{'a'})[0]
        house=m.node(row['houses'][0]['id'])
        node=next(n for n in m.iter_nodes(house) if n.type=='CMapMesh')
        node['origin']=(node['origin'][0],node['origin'][1]+8,node['origin'][2])
        before=len(list(m.iter_nodes()))
        with self.assertRaises(OpError):self.apply(m)
        self.assertEqual(len(list(m.iter_nodes())),before)

    def test_leak_alpha_fades_sides_and_bottom_and_atlas_uv_is_cropped(self):
        from cs2map import structure
        m=self.scene()
        res=structure.add_decal(self.p,m,{'op':'add_decal','material':'materials/decals/infer_dirt.vmat',
              'origin':[60,0,100],'normal':[0,-1,0],'size':[20,58]}, {})
        node=m.node(res['id'])
        wear._feather_leak(node,[0,-1,0],(20,58),'materials/decals/infer_dirt.vmat',(256,64))
        wear._uv(node,[0,0,1,.49])
        streams=node['meshData']['faceVertexData']['streams']
        alpha=next(s['data'] for s in streams if s.name=='VertexPaintTintColor:0')
        self.assertEqual({p[3] for p in alpha},{0.,1.})
        tex=next(s['data'] for s in streams if s.get('semanticName')=='texcoord')
        self.assertTrue(all(0<=u<=1 and 0<=v<=.49 for u,v in tex))


if __name__=='__main__': unittest.main()
