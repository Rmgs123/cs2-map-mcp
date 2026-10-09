import json
import unittest
from unittest.mock import patch

from cs2map import checks, mesh, physics, visual
from cs2map.project import Project, OpError
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class VisualPolish(unittest.TestCase):
    def setUp(self):
        self.p=Project(); self.m=VMap.from_template(SETTINGS.cs2_path,'empty')

    def text(self,pos=(0,0,80),angles=(0,0,90),reorient=0):
        n=self.m.new_entity('point_worldtext',{'message':'TEST','reorient_mode':str(reorient)},pos,angles)
        self.m.attach(n); return n

    def test_worldtext_requires_backing_direction_including_render_only_plate(self):
        text=self.text()
        self.m.attach(self.m.new_mesh(mesh.box([-64,1,40],[64,3,120]),options={'physicsType':'none'}))
        self.assertEqual(visual._worldtext_checks(self.m,visual._visible_meshes(self.m)),[])
        text['angles']=(0,180,90)
        self.assertEqual(visual._worldtext_checks(self.m,visual._visible_meshes(self.m))[0]['code'],
                         'visual_worldtext_unbacked')

    def test_worldtext_floor_contact_and_story_hint_are_not_backing(self):
        self.m.attach(self.m.new_mesh(mesh.box([-64,-64,0],[64,64,16])))
        self.text(pos=(0,0,24))
        self.text(pos=(0,0,160),reorient=1)
        findings=visual._worldtext_checks(self.m,visual._visible_meshes(self.m))
        self.assertEqual(len(findings),1)

    def test_worldtext_aabb_does_not_accept_a_hole(self):
        self.text()
        # Disconnected quads sharing one broad AABB, with no face behind text.
        shape=mesh.Mesh()
        shape.face([(-64,2,40),(-8,2,40),(-8,2,120),(-64,2,120)])
        shape.face([(8,2,40),(64,2,40),(64,2,120),(8,2,120)])
        self.m.attach(self.m.new_mesh(shape,options={'physicsType':'none'}))
        self.assertEqual(len(visual._worldtext_checks(self.m,visual._visible_meshes(self.m))),1)

    def test_button_visual_keeps_brush_io_and_material_is_invisible(self):
        self.m.attach(self.m.new_entity('logic_relay',{'targetname':'relay'}))
        out=physics.add_button(self.p,self.m,{'op':'add_button','targetname':'button',
            'mins':[-8,-8,44],'maxs':[8,8,60], 'targets':[{'name':'relay','input':'Trigger'}]}, {})
        button=self.m.node(out['id']); before=list(button['connectionsData'])
        refs={}
        dressed=visual.add_button_visual(self.p,self.m,{'button':'button','face':'-x','ref':'skin'},refs)
        self.assertEqual(button['connectionsData'],before)
        self.assertEqual(self.m.node(dressed['id'])['entity_properties']['parentname'],'button')
        self.assertEqual(refs['skin'],dressed['id'])
        self.assertTrue(all('toolsinvisible' in mat for n in self.m.iter_nodes(button)
                            if n.type=='CMapMesh' for mat in n['meshData']['materials']))
        with self.assertRaisesRegex(OpError,'already has'):
            visual.add_button_visual(self.p,self.m,{'button':'button'}, {})

    def test_cloth_canopy_fits_footprint_and_leaves_headroom(self):
        out=visual.add_cloth_awning(self.p,self.m,{'bounds':[[0,0,0],[80,144,176]]},{})
        n=self.m.node(out['model_id'])
        measured=visual._model_bounds(self.p,out['model'],n['origin'],n['angles'],n['scales'])
        self.assertAlmostEqual(measured[0][0],0)
        self.assertAlmostEqual(measured[1][1],144)
        self.assertAlmostEqual(measured[1][2],176)
        self.assertEqual(len(out['ids']),4)
        with self.assertRaisesRegex(OpError,'72 units'):
            visual.add_cloth_awning(self.p,self.m,{'bounds':[[0,0,0],[80,144,80]]},{})

    def test_scenery_playable_bounds_excludes_clip_but_keeps_supports(self):
        floor=self.m.new_mesh(mesh.box([0,0,-16],[512,512,0])); self.m.attach(floor)
        backdrop=self.m.new_group('outdoor:bg')
        backdrop.set('cs2map_outdoor','string',json.dumps({'kind':'add_backdrop'}))
        self.m.attach(self.m.new_mesh(mesh.box([1000,0,-100],[1016,512,2048],
             'materials/tools/toolsplayerclip.vmat')),backdrop)
        self.m.attach(backdrop)
        self.assertEqual(visual.playable_bounds(self.m),((0.,0.,-16.),(512.,512.,0.)))

    def test_void_visible_from_gameplay_point_is_reported_but_water_closes_it(self):
        slab=self.m.new_mesh(mesh.box([0,0,-16],[512,512,0]))
        self.m.attach(slab)
        g=self.m.new_group('layout')
        g.set('cs2map_layout','string',json.dumps({'origin':[0,0,0], 'rooms':{
             'pier':{'interior':{'mins':[0,0,0],'maxs':[512,512,256]},'outdoor':True}}}))
        self.m.attach(g)
        self.m.attach(self.m.new_entity('point_nav_walkable',{},[-80,256,-128]))
        result=visual._foundation_checks(self.m,visual._visible_meshes(self.m),visual._solid_meshes(self.m))
        self.assertEqual(result[0]['code'],'visual_exposed_foundation')
        water=self.m.new_group('outdoor:water')
        water.set('cs2map_outdoor','string',json.dumps({'kind':'sea','bounds':[[-128,-128,-256],[1024,1024,-32]],'water_level':-32}))
        self.m.attach(water)
        self.assertEqual(visual._foundation_checks(self.m,visual._visible_meshes(self.m),visual._solid_meshes(self.m)),[])

    def test_five_control_maps_keep_original_findings_without_new_false_alarms(self):
        for name in ('f2_port','f3_port','mcp_story','v_warehouse'):
            found=visual.visual_check(self.p,name)['problems']
            self.assertFalse([f for f in found if f['code'] in {'visual_worldtext_unbacked','visual_exposed_foundation'}],
                             (name,found))
        codes={f['code'] for f in visual.visual_check(self.p,'q_f2_orig')['problems']}
        self.assertTrue({'visual_floating_prop','visual_floating_platform'}<=codes)
