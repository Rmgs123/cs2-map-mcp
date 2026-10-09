"""Actor routes must prove support/clearance; dry runs never write scripts."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.helpers import ROOT, HAVE_BLENDER, HAVE_CS2, SETTINGS
from cs2map import actors, layout
from cs2map.project import Project, OpError
from cs2map.vmap import VMap
from cs2map.config import Settings


@unittest.skipUnless(HAVE_CS2, 'CS2 template fixture required')
class Actors(unittest.TestCase):
    def setUp(self):
        tmp=ROOT/'.local/tmp';tmp.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='n8-actors-test-',dir=tmp)
        self.addCleanup(self.temp.cleanup)
        self.p=Project(Settings(Path(self.temp.name),'mcp_n_test'))
        self.p.catalog=Project().catalog
        self.p.register_op('add_actor',actors.add_actor)
        self.m=VMap.from_template(SETTINGS.cs2_path,'empty')
        self.m.path=self.p.s.map_path('n8_test')
        self.m.path.parent.mkdir(parents=True,exist_ok=True)
        self.p._apply(self.m,{'op':'add_mesh','shape':{'shape':'box','mins':[-64,-64,-16],
            'maxs':[512,512,0]},'material':'materials/dev/dev_measuregeneric01.vmat'},{})

    def model(self):
        rel='models/mcp_n/test/test.vmdl'
        path=self.p.s.content_addon/rel;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('model fixture',encoding='utf-8')
        output=self.p.s.game_addon/(rel+'_c');output.parent.mkdir(parents=True,exist_ok=True)
        output.write_bytes(b'fixture')
        (path.parent/'actor.json').write_text(json.dumps({'ok':True,'model':rel,
            'bounds':[[-6,-13,0],[9,13,64]],'clips':{'idle':{},'walk':{},'turn':{}},'walk_speed':62}),encoding='utf-8')
        return rel

    def op(self):
        return {'op':'add_actor','name':'n8_test','model':self.model(),'route':[[0,0,0],[400,400,0]],'loop':False}

    def test_modeldoc_has_explicit_mesh_scale_and_clip_sequences(self):
        doc=actors.model_document('test','models/mcp_n/test/sources/one',[],
            {name:{'fps':30} for name in ('idle','walk','turn')})
        children=doc['rootNode']['children']
        scale=children[0]['children'][0]['import_scale']
        self.assertEqual(scale,.0254)
        clips=children[2]['children']
        self.assertEqual([c['name'] for c in clips],['idle','walk','turn'])
        self.assertTrue(all('import_scale' not in c for c in clips))
        self.assertTrue(all(c['_class']=='AnimFile' and c['looping'] for c in clips))

    def test_routes_go_around_wall_and_refuse_sealed_wall(self):
        self.p._apply(self.m,{'op':'add_mesh','shape':{'shape':'box','mins':[190,110,0],
            'maxs':[230,330,100]},'material':'materials/dev/dev_measuregeneric01.vmat'},{})
        path=actors.plan(self.m,[[0,210,0],[400,210,0]],False)
        self.assertTrue(any(abs(q['pos'][1]-210)>110 for q in path))
        self.assertTrue(all(q['pos'][2]==0 for q in path))
        self.assertEqual(sum(q['stop'] for q in path),2)
        self.p._apply(self.m,{'op':'add_mesh','shape':{'shape':'box','mins':[190,-64,0],
            'maxs':[230,512,100]},'material':'materials/dev/dev_measuregeneric01.vmat'},{})
        with self.assertRaises(OpError):actors.plan(self.m,[[0,210,0],[400,210,0]],False)

    def test_unsupported_floor_and_nonfinite_are_refused(self):
        with self.assertRaises(OpError):actors.plan(self.m,[[0,0,0],[600,0,0]],False)
        op=self.op();op['speed']=float('nan')
        with self.assertRaisesRegex(OpError,'finite'):actors.add_actor(self.p,self.m,op,{})
        op=self.op();op['loop']=1
        with self.assertRaisesRegex(OpError,'true or false'):actors.add_actor(self.p,self.m,op,{})
        op=self.op();op['extra']=1
        with self.assertRaisesRegex(OpError,'unknown'):actors.add_actor(self.p,self.m,op,{})

    def test_dry_run_and_later_failure_write_no_script(self):
        op=self.op()
        edit=actors.map_edit_wrapper(self.p,self.p.map_edit)
        self.m.save(self.m.path)
        with patch.object(self.p,'_check_hammer'), patch.object(actors.scripting,'script_write') as write:
            result=edit('n8_test',[op],dry_run=True)
            self.assertTrue(result['dry_run']);write.assert_not_called()
            before=self.m.path.read_bytes()
            with self.assertRaises(OpError):edit('n8_test',[op,{'op':'unknown'}])
            write.assert_not_called();self.assertEqual(before,self.m.path.read_bytes())

    def test_actor_entity_and_io_script_are_wired(self):
        token=actors.PREVIEW.set(True)
        try:result=actors.add_actor(self.p,self.m,self.op(),{})
        finally:actors.PREVIEW.reset(token)
        actor=self.m.node(result['id']);props=actor['entity_properties']
        self.assertEqual(props['classname'],'prop_dynamic')
        self.assertEqual(props['solid'],'0');self.assertEqual(props['use_animgraph'],'0')
        self.assertEqual(props['IdleAnim'],'idle')
        script=self.m.node(result['script_id'])['entity_properties']
        self.assertEqual(script['targetname'],'n8_test_script')
        self.assertEqual(script['cs_script'],'maps/scripts/mcp/n8_n8_test.vjs')

    def test_layout_openings_are_used(self):
        m=VMap.from_template(SETTINGS.cs2_path,'empty')
        layout.add_layout(self.p,m,{'op':'add_layout','name':'rooms','rooms':[
            {'id':'a','origin':[0,0,0],'size':[256,256,128]},
            {'id':'b','from':'a','side':'+x','size':[256,256,128]}],
            'openings':[{'between':['a','b'],'width':96,'height':96}]},{})
        a=layout.point(m,{'room':'a','at':[.3,.3]})[0]
        b=layout.point(m,{'room':'b','at':[.7,.7]})[0]
        expected=actors.route.layout_waypoints(m,a,b)
        path=actors.plan(m,[a,b],True)
        self.assertTrue(all(any(q['pos']==e for q in path) for e in expected))

    def test_stairs_supported_and_large_steps_refused(self):
        self.p._apply(self.m,{'op':'add_mesh','shape':{'shape':'stairs','mins':[120,-40,0],
            'maxs':[240,100,48],'steps':4,'rise':'+x'},'material':'materials/dev/dev_measuregeneric01.vmat'},{})
        self.p._apply(self.m,{'op':'add_mesh','shape':{'shape':'box','mins':[240,-40,0],
            'maxs':[500,100,48]},'material':'materials/dev/dev_measuregeneric01.vmat'},{})
        path=actors.plan(self.m,[[60,30,0],[400,30,48]],False)
        heights={q['pos'][2] for q in path}
        self.assertTrue({0,12,24,36,48}<=heights)
        self.assertTrue(all(abs(a['pos'][2]-b['pos'][2])<=18 for a,b in zip(path,path[1:])))
        with self.assertRaises(OpError):actors.plan(self.m,[[400,30,48],[400,200,0]],False)

    def test_check_detects_missing_script_and_cross_map_name_collision(self):
        op=self.op();token=actors.PREVIEW.set(True)
        try:actors.add_actor(self.p,self.m,op,{})
        finally:actors.PREVIEW.reset(token)
        self.assertEqual(actors.check_actors(self.p,self.m)[0]['code'],'actor_resource')
        self.m.save(self.m.path)
        script=self.p.s.content_addon/'maps/scripts/mcp/n8_n8_test.js'
        script.parent.mkdir(parents=True,exist_ok=True);script.write_text('different',encoding='utf-8')
        other=VMap.from_template(SETTINGS.cs2_path,'empty');other.path=self.p.s.map_path('n8_other')
        self.p._apply(other,{'op':'add_mesh','shape':{'shape':'box','mins':[-64,-64,-16],
            'maxs':[512,512,0]},'material':'materials/dev/dev_measuregeneric01.vmat'},{})
        with self.assertRaisesRegex(OpError,'another map'):
            actors.add_actor(self.p,other,op,{})


class ActorsBlender(unittest.TestCase):
    @unittest.skipUnless(HAVE_BLENDER, 'Blender required')
    def test_real_skinning_pose_support_and_saved_actions(self):
        from cs2map import blender_run
        tmp=ROOT/'.local/tmp';tmp.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='n8-blender-test-',dir=tmp) as temp:
            out=Path(temp)/'source'
            p=Project()
            job=blender_run.run(p.s,p.jobs,actors.DATA/'export.py',[out],timeout=90)
            self.assertEqual(job.state,'succeeded',job.log)
            check=Path(temp)/'verify.py'
            check.write_text('''import bpy,json,sys
from pathlib import Path
bpy.ops.wm.open_mainfile(filepath=sys.argv[-1])
rig=next(o for o in bpy.context.scene.objects if o.type=='ARMATURE')
assert {'idle','walk','turn'}<=set(a.name for a in bpy.data.actions)
positions=[]
for clip in ('idle','walk','turn'):
 rig.animation_data.action=bpy.data.actions[clip]
 a,b=map(int,rig.animation_data.action.frame_range)
 for frame in range(a,b+1):
  bpy.context.scene.frame_set(frame)
  deps=bpy.context.evaluated_depsgraph_get()
  points=[o.matrix_world@v.co for o in bpy.context.scene.objects if o.type=='MESH' for v in o.evaluated_get(deps).data.vertices]
  minimum=min(v.z for v in points)
  assert abs(minimum)<.01,(clip,frame,minimum)
  if clip=='walk' and frame in (7,19):positions.append([(v.x,v.y,v.z) for v in points])
assert positions[0]!=positions[1]
assert bpy.data.objects['nose_forward_X'].location.x==0
mesh=bpy.data.objects['nose_forward_X'].data
assert min(v.co.x for v in mesh.vertices)>0
print('JSON>'+json.dumps({'ok':True,'poses_grounded':True,'opposite_walk_poses':True}))
''',encoding='utf-8')
            verified=blender_run.run(p.s,p.jobs,check,[out/'actor.blend'],timeout=60)
            self.assertEqual(verified.state,'succeeded',verified.log)
            self.assertTrue(verified.result['poses_grounded'])


if __name__=='__main__':unittest.main()
