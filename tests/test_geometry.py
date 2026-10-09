"""Modelling invariants, negative inputs and DMX attribute preservation."""
import math
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from tests.helpers import check_topology, signed_volume, HAVE_CS2, SETTINGS
from cs2map import mesh, meshops as g, shapes
from cs2map.dmx import Element
from cs2map.ext import geometry

MAT=mesh.DEFAULT_MATERIAL
RECT=[[0,0],[16,0],[16,16],[0,16]]


def node(m):
    n=Element('CMapMesh');n.set('nodeID','int',42);n.set('origin','vector3',(0.,0.,0.))
    n.set('physicsType','string','default');n.set('meshData','element',m.to_dmx())
    n.set('tintColor','color',(255,255,255,255))
    return n


class GeometryShapes(unittest.TestCase):
    def checked(self,m,closed=True):
        d=m.to_dmx();check_topology(self,d,closed)
        if closed:self.assertGreater(signed_volume(d),0)
        for s in g.stream_map(d['faceVertexData']).values():
            self.assertTrue(all(math.isfinite(x) for v in s['data'] for x in v))
        return m

    def test_concave_profile(self):
        p=[[0,0],[32,0],[32,8],[8,8],[8,32],[0,32]]
        m=g.extrude_shape({'profile':p,'start':[0,0,0],'end':[0,0,20]},MAT)[0]
        self.checked(m);self.assertAlmostEqual(g.volume(m),448*20)

    def test_hole_profile(self):
        m=g.extrude_shape({'profile':RECT,'holes':[[[4,4],[12,4],[12,12],[4,12]]],'end':[0,0,20]},MAT)[0]
        self.checked(m);self.assertAlmostEqual(g.volume(m),(256-64)*20)

    def test_multiple_holes_and_area(self):
        outer=[[0,0],[100,0],[100,100],[0,100]]
        holes=[[[10,10],[20,10],[20,20],[10,20]],[[60,60],[90,60],[90,90],[60,90]]]
        ts=g.triangulate(outer,holes);self.assertAlmostEqual(sum(g.area(t) for t in ts),9000)
        self.assertTrue(all(not any(g.inside(tuple(sum(p[k] for p in t)/3 for k in (0,1)),h) for h in holes) for t in ts))

    def test_path_bends_and_arc(self):
        p=[[-3,-3],[3,-3],[3,3],[-3,3]]
        for path in ([[0,0,0],[40,0,0],[40,40,0]], {'center':[0,0,64],'radius':100,'start':0,'angle':180,'axis':[0,1,0],'segments':12}):
            self.checked(g.extrude_shape({'profile':p,'path':path,'up':[0,1,0] if isinstance(path,dict) else [0,0,1]},MAT)[0])

    def test_closed_path_and_uncapped_render(self):
        p=[[-2,-2],[2,-2],[2,2],[-2,2]]
        self.checked(g.extrude_shape({'profile':p,'path':[[0,0,0],[40,0,0],[40,40,0],[0,40,0]],'closed':True},MAT)[0])
        m=g.extrude_shape({'profile':RECT,'end':[0,0,20],'caps':False,'solid':False},MAT)[0]
        self.checked(m,False);self.assertIn(-1,m.halfedges()[1])

    def test_spiral_stair_recipe_and_helical_rail(self):
        segments=24;inner,outer=64,144
        for i in range(segments):
            a,b=2*math.pi*i/segments,2*math.pi*(i+1)/segments
            poly=[(inner*math.cos(a),inner*math.sin(a)),(outer*math.cos(a),outer*math.sin(a)),
                  (outer*math.cos(b),outer*math.sin(b)),(inner*math.cos(b),inner*math.sin(b))]
            self.checked(g.validate(mesh.prism(poly,0,(i+1)*8)))
        path=[[outer*math.cos(2*math.pi*i/segments),outer*math.sin(2*math.pi*i/segments),i*8+56] for i in range(segments+1)]
        self.checked(g.extrude_shape({'profile':[[-2,-2],[2,-2],[2,2],[-2,2]],'path':path},MAT)[0])

    def test_revolve_full_partial_axis_and_poles(self):
        for axis in ('x','y','z',[1,1,0]):
            for angle in (360,180,70):
                m=g.revolve_shape({'profile':[[0,0],[16,0],[16,20],[0,20]],'angle':angle,'axis':axis},MAT)[0]
                self.checked(m)
        dome=[[0,0],[32,0]]+[[32*math.cos(t),32*math.sin(t)] for t in [math.pi*i/16 for i in range(1,9)]]
        self.checked(g.revolve_shape({'profile':dome},MAT)[0])

    def test_hollow_revolve(self):
        m=g.revolve_shape({'profile':[[10,0],[16,0],[16,64],[10,64]],'segments':32},MAT)[0]
        self.checked(m);self.assertAlmostEqual(g.volume(m),32/2*math.sin(2*math.pi/32)*(256-100)*64,delta=.2)

    def test_mesh_concave_face_hole_uv_material(self):
        vertices=[[0,0,0],[32,0,0],[32,32,0],[0,32,0],[8,8,0],[24,8,0],[24,24,0],[8,24,0]]
        m=g.mesh_shape({'vertices':vertices,'faces':[{'vertices':[0,1,2,3],'holes':[[4,5,6,7]],'material':'second',
                          'uv':{'mode':'planar','scale':[.5,1],'rotate':20,'offset':[3,4]}}],'solid':False},MAT)[0]
        self.checked(m,False);self.assertEqual(len(m.faces),8)
        self.assertTrue(all(m.normal(f)[2]>.999 and f.material=='second' and f.scale==(.5,1.) for f in m.faces))

    def test_closed_mesh_preserves_winding(self):
        cube=mesh.box((0,0,0),(10,10,10))
        s={'vertices':cube.positions,'faces':[f.verts for f in cube.faces]}
        self.checked(g.mesh_shape(s,MAT)[0])
        for f in s['faces']:f.reverse()
        with self.assertRaisesRegex(ValueError,'inward'):g.mesh_shape(s,MAT)

    def test_invalid_polygons_profiles_and_holes(self):
        cases=[([],[]), ([[0,0],[16,16],[0,16],[16,0]],[]), (RECT,[[[0,4],[8,4],[8,8],[0,8]]]),
               (RECT,[[[20,20],[21,20],[21,21]]]),(RECT,[[[2,2],[8,2],[8,8],[2,8]],[[4,4],[10,4],[10,10],[4,10]]])]
        for p,h in cases:
            with self.subTest(p=p),self.assertRaises(ValueError):g.triangulate(p,h)

    def test_invalid_mesh(self):
        for s in ({'vertices':[[0,0,0],[1,0,0],[0,1,0]],'faces':[[0,1,2]]},
                  {'vertices':[[0,0,0],[1,0,0],[0,1,0]],'faces':[[0,1,99]],'solid':False},
                  {'vertices':[[0,0,0],[1,0,0],[1,1,1],[0,1,0]],'faces':[[0,1,2,3]],'solid':False},
                  {'vertices':[[0,0,0],[1,0,0],[0,1,0]],'faces':[[0,1,True]],'solid':False}):
            with self.assertRaises(ValueError):g.mesh_shape(s,MAT)

    def test_invalid_sweep(self):
        for extra in ({'caps':False},{'path':[[0,0,0],[0,0,0]]},{'path':[[0,0,0],[10,0,0],[0,0,0]]},
                      {'up':[0,0,0]},{'end':[0,0,float('nan')]},{'twist':30}):
            with self.assertRaises(ValueError):g.extrude_shape({'profile':RECT,'end':[0,0,20],**extra},MAT)
        for extra in ({'segments':2},{'segments':4.5},{'angle':0},{'angle':361},{'axis':'q'},{'profile':[[-1,0],[1,0],[1,1]]}):
            with self.assertRaises(ValueError):g.revolve_shape({'profile':RECT,**extra},MAT)

    def test_face_budgets_fail_before_large_grid_allocation(self):
        profile=[[10*math.cos(2*math.pi*i/40),10*math.sin(2*math.pi*i/40)] for i in range(40)]
        with self.assertRaisesRegex(ValueError,'budget'):
            g.extrude_shape({'profile':profile,'path':[[i*40,0,0] for i in range(200)]},MAT)
        with self.assertRaisesRegex(ValueError,'holes'):
            g.rings(RECT,[RECT]*33)
        p=g.mesh_polygons(mesh.box((0,0,0),(256,256,256)))
        with self.assertRaisesRegex(ValueError,'budget'):
            g.paint(p,set(range(6)),{'cell':4})

    def test_paint_after_cuts_on_curved_tower_stays_manifold(self):
        # p8_mechanics regression: fan triangles of cut faces made zero-area slivers.
        m=g.revolve_shape({'origin':[230,930,0],'profile':[[80,0],[96,0],[96,336],[80,336]],'segments':24},MAT)[0]
        n=node(m);polys=g.decode_node(n)
        for lo,hi in [((300,900,150),(330,960,240)),((200,830,150),(260,860,240)),((194,830,-1),(266,860,120))]:
            polys=g.subtract(polys,g.mesh_polygons(mesh.box(lo,hi,'inside')))
        n['meshData']=g.encode_polygons(n,polys,lambda _: (512,512))
        polys=g.decode_node(n);sel=g.select_faces(polys,'sides')
        out=g.paint(polys,sel,{'cell':48,'radius':72,'rule':['bottom']})
        n['meshData']=g.encode_polygons(n,out,lambda _: (512,512))
        check_topology(self,n['meshData'])
        weights=[v.blend[0] for p in g.decode_node(n) for v in p.corners]
        self.assertEqual((round(min(weights),3),round(max(weights),3)),(0,1))


class GeometryEdits(unittest.TestCase):
    def test_cut_box_through_hole_preserves_uv_tint_blend(self):
        n=node(mesh.box((0,0,0),(100,20,100)))
        polys=g.decode_node(n)
        for p in polys:
            for v in p.corners:v.tint=(.8,.7,.6,1);v.blend=(v.p[2]/100,0,0,0)
        cutter=g.mesh_polygons(mesh.box((30,-10,20),(70,30,80),'inside'))
        result=g.subtract(polys,cutter);n['meshData']=g.encode_polygons(n,result,lambda _: (512,512))
        check_topology(self,n['meshData']);self.assertAlmostEqual(signed_volume(n['meshData']),200000-48000)
        restored=g.decode_node(n);self.assertIn('inside',{p.material for p in restored})
        for p in restored:
            if p.material!=MAT:continue
            for v in p.corners:
                self.assertEqual(v.tint,(.8,.7,.6,1));self.assertAlmostEqual(v.blend[0],v.p[2]/100)

    def test_cut_concave_solid_convex_prism_and_disconnected_result(self):
        m=g.extrude_shape({'profile':[[0,0],[100,0],[100,20],[20,20],[20,100],[0,100]],'end':[0,0,100]},MAT)[0]
        n=node(m);c=mesh.prism([[10,-10],[50,-10],[10,50]],30,70)
        n['meshData']=g.encode_polygons(n,g.subtract(g.decode_node(n),g.mesh_polygons(c)),lambda _: (512,512))
        check_topology(self,n['meshData']);self.assertLess(signed_volume(n['meshData']),360000)
        n=node(mesh.box((0,0,0),(100,20,20)));c=mesh.box((40,-1,-1),(60,21,21))
        n['meshData']=g.encode_polygons(n,g.subtract(g.decode_node(n),g.mesh_polygons(c)),lambda _: (512,512))
        check_topology(self,n['meshData']);self.assertAlmostEqual(signed_volume(n['meshData']),32000)

    def test_repeated_cuts_on_curved_hollow_tower(self):
        m=g.revolve_shape({'origin':[280,690,0],'profile':[[94,0],[110,0],[110,288],[94,288]],'segments':24},MAT)[0]
        n=node(m);vol=signed_volume(n['meshData'])
        for lo,hi in [([166,658,136],[183,722,220]),([248,576,136],[312,593,220]),
                      ([248,787,136],[312,806,220]),([240,570,-1],[320,608,112])]:
            result=g.subtract(g.decode_node(n),g.mesh_polygons(mesh.box(lo,hi)))
            n['meshData']=g.encode_polygons(n,result,lambda _: (512,512))
            check_topology(self,n['meshData']);new=signed_volume(n['meshData'])
            self.assertLess(new,vol);vol=new

    def test_full_cut_refused_and_no_overlap_unchanged(self):
        n=node(mesh.box((0,0,0),(10,10,10)));p=g.decode_node(n)
        with self.assertRaisesRegex(ValueError,'empty'):g.encode_polygons(n,g.subtract(p,g.mesh_polygons(mesh.box((-1,-1,-1),(11,11,11)))),lambda _: (512,512))
        result=g.subtract(p,g.mesh_polygons(mesh.box((20,20,20),(30,30,30))))
        self.assertAlmostEqual(signed_volume(g.encode_polygons(n,result,lambda _: (512,512))),1000)

    def test_selection_and_uv(self):
        n=node(mesh.box((0,0,0),(100,100,100)));p=g.decode_node(n)
        for name,count in [('top',1),('bottom',1),('sides',4),('+x',1),('-y',1),('all',6)]:self.assertEqual(len(g.select_faces(p,name)),count)
        self.assertEqual(len(g.select_faces(p,{'mins':[0,0,99],'maxs':[100,100,101]})),1)
        for mode in ('world','box','planar'):
            g.project_uv(p,g.select_faces(p,'top'),{'mode':mode,'scale':[1,2],'rotate':30,'offset':[20,10]},lambda _: (512,256))
            d=g.encode_polygons(n,p,lambda _: (512,256));check_topology(self,d)
        top=p[next(iter(g.select_faces(p,'top')))];self.assertGreater(max(v.uv[0] for v in top.corners)-min(v.uv[0] for v in top.corners),.1)
        for bad in ('missing',[999],[True],{'mins':[1,1,1],'maxs':[0,0,0]}):
            with self.assertRaises(ValueError):g.select_faces(p,bad)

    def test_cylinder_uv_seam_is_short_and_finite(self):
        n=node(mesh.cylinder((0,0),32,0,64,24));p=g.decode_node(n)
        g.project_uv(p,g.select_faces(p,'sides'),{'mode':'cylinder','scale':[1,1]},lambda _: (512,512))
        for i in g.select_faces(p,'sides'):
            self.assertLess(max(v.uv[0] for v in p[i].corners)-min(v.uv[0] for v in p[i].corners),.03)
        check_topology(self,g.encode_polygons(n,p,lambda _: (512,512)))

    def test_paint_grid_uv_tint_and_determinism(self):
        n=node(mesh.box((0,0,0),(128,16,128)));p=g.decode_node(n);selected=g.select_faces(p,'-y')
        for poly in p:
            for v in poly.corners:v.tint=(.5,.7,1,1)
        result=g.paint(p,selected,{'cell':32,'radius':64,'rule':['bottom','noise'],'seed':5})
        md=g.encode_polygons(n,result,lambda _: (512,512));check_topology(self,md)
        self.assertGreater(len(md['faceEdgeIndices']),6);self.assertAlmostEqual(signed_volume(md),128*16*128)
        for poly in result:
            for v in poly.corners:
                for got,want in zip(v.tint,(.5,.7,1,1)):self.assertAlmostEqual(got,want)
        n['meshData']=md
        for poly in g.decode_node(n):
            for v in poly.corners:self.assertTrue(0<=v.blend[0]<=1)
        self.assertEqual(g.noise((10,20,30),5,64),g.noise((10,20,30),5,64))
        self.assertNotEqual(g.noise((10,20,30),5,64),g.noise((10,20,30),6,64))

    def test_bottom_and_corner_rules_and_invalid_paint(self):
        p=g.mesh_polygons(mesh.box((0,0,0),(128,16,128)));selected=g.select_faces(p,'-y')
        out=g.paint(p,selected,{'cell':32,'radius':32,'rule':'bottom'})
        for poly in out:
            if poly.n[1]>-.99:continue
            for v in poly.corners:self.assertAlmostEqual(v.blend[0],max(0,1-v.p[2]/32))
        out=g.paint(p,selected,{'cell':32,'radius':32,'rule':'corners'})
        self.assertTrue(any(v.blend[0]==1 for poly in out for v in poly.corners))
        for extra in ({'cell':0},{'radius':0},{'rule':'random'},{'seed':-1},{'strength':2}):
            with self.assertRaises(ValueError):g.paint(p,selected,extra)

    def test_transforms_and_unknown_streams_refused(self):
        n=node(mesh.box((0,0,0),(10,10,10)));n.set('angles','qangle',(0,30,0))
        with self.assertRaisesRegex(ValueError,'transformed'):g.decode_node(n)
        n['angles']=(0,0,0);n['meshData']['faceVertexData']['streams'].append(mesh._paint_stream('Unknown',[(0,0,0,0)]*24))
        with self.assertRaisesRegex(ValueError,'streams'):g.decode_node(n)

    def test_intersecting_closed_shells_and_vertex_pinch_refused(self):
        a=mesh.box((0,0,0),(10,10,10));a.extend(mesh.box((5,5,5),(15,15,15)))
        with self.assertRaisesRegex(ValueError,'self-intersecting'):g.validate(a)
        a=mesh.box((0,0,0),(10,10,10));a.extend(mesh.box((10,10,10),(20,20,20)))
        with self.assertRaisesRegex(ValueError,'non-manifold vertex'):g.validate(a)

    def test_extension_render_only_and_registry(self):
        fake=SimpleNamespace(extra_ops={},preprocessors=[],register_op=lambda n,f:None,register_preprocessor=lambda f:None)
        mcp=SimpleNamespace(tool=lambda:lambda f:f)
        geometry.register(mcp,fake);geometry.register(mcp,fake)
        self.assertTrue({'mesh','extrude','revolve'}<=shapes.EXTRA_SHAPES.keys())
        op=geometry.preprocess(None,None,{'op':'add_mesh','shape':{'shape':'mesh','solid':False}}, {})
        self.assertEqual(op['options']['physicsType'],'none');self.assertFalse(op['options']['bakelighting'])


@unittest.skipUnless(HAVE_CS2,'requires installed Valve material data')
class GeometryMaterialIntegration(unittest.TestCase):
    def test_stock_blend_operation_and_nonblend_refusal(self):
        from cs2map.project import Project
        p=Project();n=node(mesh.box((0,0,0),(128,16,128)))
        p.resolve=lambda *args:n
        op={'op':'paint_blend','id':'@wall','material':'materials/de_inferno/stone/inferno_stone_wall04_plaster03_blend.vmat',
            'faces':'-y','cell':64,'radius':80}
        out=geometry.paint_blend(p,None,op,{})
        self.assertGreater(out['faces'],6);check_topology(self,n['meshData'])
        with self.assertRaisesRegex(ValueError,'second color layer'):
            geometry.paint_blend(p,None,{**op,'material':MAT},{})
