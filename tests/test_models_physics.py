"""Optional physical ModelDoc data must be validated before invoking Blender."""
import tempfile
import unittest
from pathlib import Path
from cs2map import models, kv3
from cs2map.config import Settings
from cs2map.project import Project
from tests.helpers import HAVE_BLENDER


class ModelsPhysics(unittest.TestCase):
    @unittest.skipUnless(HAVE_BLENDER, 'Blender required')
    def test_real_fragments_are_closed_and_preserve_volume(self):
        script = '''
import bpy, bmesh, json, sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'src/cs2map/blender'))
from common import box
from driver import export_break_fragments, evaluated_meshes
bpy.ops.wm.read_factory_settings(use_empty=True)
box('fragment_test',(40,40,40),(0,0,20),bevel=0)
bpy.context.view_layer.update()
obj=evaluated_meshes()[0][0]
out=Path.cwd()/'.local/scratch/P/fragment_test/fragment_test.fbx'
out.parent.mkdir(parents=True,exist_ok=True)
files=export_break_fragments([obj],out)
assert len(files)==8,files
volumes=[]
for piece in bpy.context.scene.objects:
 if not piece.name.startswith(tuple('fragment_'+str(i)+'_' for i in range(8))): continue
 bm=bmesh.new();bm.from_mesh(piece.data)
 assert all(e.is_manifold for e in bm.edges),piece.name
 volume=bm.calc_volume(signed=True);bm.free()
 assert abs(volume-8000)<.01,(piece.name,volume)
 volumes.append(volume)
assert len(volumes)==8 and abs(sum(volumes)-64000)<.01,volumes
print('JSON>'+json.dumps({'fragments':len(files),'volume':sum(volumes),'closed':True}))
'''
        p=Project();job=models.run_blender(p,script,timeout=60)
        st=p.jobs.wait(job.id,60)
        self.assertEqual(st.state,'succeeded',st.log)
        self.assertEqual(st.result['fragments'],8)
        self.assertAlmostEqual(st.result['volume'],64000,places=2)

    def test_validation(self):
        for opts,col in [({'mass':0},'hull'),({'mass':float('nan')},'hull'),
                         ({'mass':True},'hull'),({'breakable':'stone'},'hull'),
                         ({'mass':20},'none'),({'mass':20},'mesh'),({'typo':1},'hull')]:
            with self.subTest(opts=opts,col=col), self.assertRaises(models.ModelError):
                models._physics_options(opts,col)
        self.assertIsNone(models._physics_options(None,'none'))
        self.assertEqual(models._physics_options({},'hull'), {'mass':20.,'breakable':None})

    def test_modeldoc_mass_propdata_and_fragments(self):
        with tempfile.TemporaryDirectory() as td:
            p=Project(Settings(Path(td),'p_test'))
            doc,_,_=models._make_vmdl(p,'p_unit',{'materials':[],
                'collision_files':['models/mcp/p_unit/col0.fbx'],
                'break_files':['models/mcp/p_unit/break0.fbx','models/mcp/p_unit/break1.fbx']},
                'hull','wood',{'mass':20.,'breakable':'wood'})
            _,data=kv3.loads(doc.read_text(encoding='utf-8'))
            nodes={x['_class']:x for x in data['rootNode']['children']}
            body=nodes['PhysicsBodyMarkupList']['children'][0]
            self.assertEqual(body['mass_override'],20)
            self.assertIsInstance(body['inertia_scale'],float)
            prop=nodes['GameDataList']['children'][0]
            self.assertEqual(prop['game_class'],'prop_data')
            self.assertEqual(prop['game_keys']['base'],'Wooden.Medium')
            pieces=nodes['BreakPieceList']['children']
            self.assertEqual(len(pieces),2)
            self.assertTrue(all(x['spawn_entity']=='prop_physics_override' for x in pieces))
            self.assertTrue(all(x['children'][1]['_class']=='PhysicsHullFile' for x in pieces))
