"""Public modelling operation contract, including no mutation on refusal."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_geometry import node, MAT
from tests.helpers import check_topology
from cs2map import mesh, meshops as g
from cs2map.ext import geometry
from cs2map.project import OpError
from cs2map.dmx import Document, dumps


class GeometryOperationContract(unittest.TestCase):
    def setUp(self):
        self.n=node(mesh.box((0,0,0),(128,16,128)))
        self.p=SimpleNamespace(resolve=lambda *args:self.n, texture_size=lambda _: (512,512))
        self.refs={}

    def data(self):return dumps(Document(self.n))

    def test_selected_material_face_and_mesh_tint_keep_other_faces(self):
        with patch.object(geometry.checks,'_asset_exists',return_value=True):
            out=geometry.texture_faces(self.p,None,{'op':'texture_faces','id':'@wall','ref':'wall','faces':'-y',
                'material':'materials/mcp_g/check.vmat','tint':[.5,.7,.8],'mesh_tint':[1,.5,0],
                'uv':{'mode':'world','scale':[1,1],'rotate':35,'offset':[20,10]}},self.refs)
        self.assertEqual(out['faces'],1);self.assertEqual(self.refs['wall'],42)
        self.assertEqual(self.n['tintColor'],(255,128,0,255));check_topology(self,self.n['meshData'])
        for poly in g.decode_node(self.n):
            self.assertEqual(poly.material,'materials/mcp_g/check.vmat' if poly.n[1]<-.9 else MAT)
            for v in poly.corners:self.assertEqual(v.tint,(.5,.7,.8,1.) if poly.n[1]<-.9 else (1.,1.,1.,1.))

    def test_bad_projection_after_material_does_not_mutate_target(self):
        before=self.data()
        with patch.object(geometry.checks,'_asset_exists',return_value=True),self.assertRaises(ValueError):
            geometry.texture_faces(self.p,None,{'op':'texture_faces','id':42,'material':'materials/mcp_g/check.vmat',
                'uv':{'mode':'world','scale':[0,1]}},self.refs)
        self.assertEqual(self.data(),before)

    def test_cut_preserves_node_and_mesh_options(self):
        self.n.set('bakelighting','bool',False);self.n.set('physicsGroup','string','example')
        with patch.object(geometry.checks,'_asset_exists',return_value=True):
            out=geometry.cut_mesh(self.p,None,{'op':'cut_mesh','id':42,'ref':'wall',
                'cutter':{'shape':'box','mins':[32,-1,32],'maxs':[64,17,96]},'material':MAT},self.refs)
        self.assertEqual(out['faces'],len(self.n['meshData']['faceEdgeIndices']));self.assertEqual(out['id'],42)
        self.assertFalse(self.n['bakelighting']);self.assertEqual(self.n['physicsGroup'],'example')
        check_topology(self,self.n['meshData'])

    def test_nonconvex_cutter_wrong_target_unknown_fields_and_missing_material(self):
        before=self.data()
        with patch.object(geometry.checks,'_asset_exists',return_value=True):
            with self.assertRaisesRegex(ValueError,'convex'):
                geometry.cut_mesh(self.p,None,{'op':'cut_mesh','id':42,'cutter':{'shape':'prism',
                    'polygon':[[0,0],[100,0],[100,10],[10,10],[10,100],[0,100]],'z0':0,'z1':100}},self.refs)
            with self.assertRaisesRegex(ValueError,'unknown'):
                geometry.texture_faces(self.p,None,{'op':'texture_faces','id':42,'typo':'world'},self.refs)
        with patch.object(geometry.checks,'_asset_exists',return_value=False),self.assertRaises(OpError):
            geometry.texture_faces(self.p,None,{'op':'texture_faces','id':42,'material':'materials/absent.vmat'},self.refs)
        self.assertEqual(self.data(),before)
        self.n.type='CMapGroup'
        with self.assertRaisesRegex(ValueError,'one CMapMesh'):
            geometry.texture_faces(self.p,None,{'op':'texture_faces','id':42,'tint':[1,1,1]},self.refs)

    def test_full_cut_refused_without_mutation(self):
        before=self.data()
        with patch.object(geometry.checks,'_asset_exists',return_value=True),self.assertRaises(ValueError):
            geometry.cut_mesh(self.p,None,{'op':'cut_mesh','id':42,'cutter':{'shape':'box','mins':[-1,-1,-1],'maxs':[129,17,129]}},self.refs)
        self.assertEqual(self.data(),before)
