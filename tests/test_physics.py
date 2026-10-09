"""Interactive recipes: FGD flags, pivots, I/O, validation and compiled bodies."""
import math
import unittest
from unittest.mock import patch

from tests.helpers import HAVE_CS2
from cs2map import physics
from cs2map.project import Project, OpError
from cs2map.vmap import VMap


@unittest.skipUnless(HAVE_CS2, 'Valve FGD and template required')
class Physics(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(self.p.s.cs2_path, 'basic')
        self.refs = {}

    def apply(self, op):
        return physics.OPERATIONS[op['op']](self.p, self.m, op, self.refs)

    def test_slide_button_and_rotating_pivot_world_mesh(self):
        r = self.apply({'op': 'add_door', 'targetname': 'p_test_door', 'mins': [0,0,0],
                        'maxs': [8,96,112], 'open_by': 'button', 'ref': 'door'})
        door = self.m.node(r['id'])
        self.assertEqual(door['entity_properties']['spawnflags'], '0')
        self.assertEqual(door['entity_properties']['movedir'], '-90 0 0')
        b = self.m.node(r['button_id'])
        self.assertEqual(b['entity_properties']['spawnflags'], '1025')
        self.assertEqual(b['connectionsData'][0]['targetName'], 'p_test_door')
        self.assertEqual(self.refs['door'], r['id'])
        r = self.apply({'op': 'add_door', 'mins': [200,0,0], 'maxs': [208,96,112],
                        'kind': 'rotate', 'hinge': '-y', 'locked': True, 'stay_open': True})
        door = self.m.node(r['id'])
        self.assertEqual(door['origin'], (204.,0.,56.))
        self.assertEqual(door['children'][0]['origin'], (204.,48.,56.))
        self.assertEqual(int(door['entity_properties']['spawnflags']), 256|2048)
        self.assertEqual(door['entity_properties']['wait'], '-1')

    def test_mover_arbitrary_vector_and_loop(self):
        r = self.apply({'op': 'add_mover', 'mins': [0,0,0], 'maxs': [64,64,8],
                        'to': [30,40,120], 'call': 'loop'})
        e = self.m.node(r['id']);kv=e['entity_properties']
        self.assertAlmostEqual(float(kv['movedistance']), 130)
        ang, v = physics.direction([float(x) for x in kv['movedir'].split()])
        for actual, expected in zip(v, [30/130,40/130,120/130]):
            self.assertAlmostEqual(actual, expected, places=5)
        self.assertEqual([x['inputName'] for x in e['connectionsData']], ['Close','Open'])
        self.assertTrue(self.m.entities(classname='logic_auto'))

    def test_button_once_and_trigger_door(self):
        r = self.apply({'op':'add_button','mins':[0,0,0],'maxs':[8,8,8], 'once':True,
                        'targets':[{'name':'later','input':'Open','delay':2}]})
        b=self.m.node(r['id'])
        self.assertEqual(b['entity_properties']['wait'],'-1')
        self.assertEqual(b['connectionsData'][0]['timesToFire'],1)
        r=self.apply({'op':'add_door','mins':[100,0,0],'maxs':[108,96,112],'open_by':'trigger'})
        tr=self.m.node(r['trigger_id'])
        self.assertEqual(tr['connectionsData'][0]['outputName'],'OnStartTouch')

    def test_rotator_and_breakables(self):
        r=self.apply({'op':'add_rotator','mins':[0,0,0],'maxs':[100,8,8],'axis':'y','speed':-80})
        self.assertEqual(int(self.m.node(r['id'])['entity_properties']['spawnflags']), 8|1|2)
        for kind, value in [('glass','0'),('wood','1'),('metal','2')]:
            r=self.apply({'op':'add_breakable','mins':[200,0,0],'maxs':[208,64,64],
                          'material':kind,'health':25})
            self.assertEqual(self.m.node(r['id'])['entity_properties']['material'], value)

    def test_zones_and_ladder(self):
        for kind,params in [('push',{'speed':700}),('teleport',{'destination':[100,200,8]}),
                            ('hurt',{'damage':20}),('gravity',{'gravity':.25})]:
            r=self.apply({'op':'add_zone','kind':kind,'mins':[0,0,0],'maxs':[64,64,64],'params':params})
            if kind=='gravity':        # trigger_gravity does nothing in CS2: a scripted ability zone instead
                self.assertEqual(r['abilities'],['gravity'])
                self.assertTrue(self.m.entities(classname='trigger_multiple'))
                continue
            self.assertEqual(r['classname'],'trigger_'+kind)
            if kind=='teleport':
                self.assertEqual(self.m.node(r['destination_id'])['origin'],(100.,200.,8.))
        r=self.apply({'op':'add_ladder','mins':[0,0,0],'maxs':[8,64,128]})
        self.assertIsNone(r['targetname'])
        self.assertIn('visual', r)

    def test_invalid_inputs(self):
        for op in [
            {'op':'add_door','mins':[0,0,0],'maxs':[0,4,5]},
            {'op':'add_mover','mins':[0,0,0],'maxs':[8,8,8],'to':[0,0,0]},
            {'op':'add_button','mins':[0,0,0],'maxs':[8,8,8],'targets':[]},
            {'op':'add_zone','kind':'teleport','mins':[0,0,0],'maxs':[8,8,8],'params':{'destination':[1,2]}},
            {'op':'add_rotator','mins':[0,0,0],'maxs':[8,8,8],'speed':float('nan')},
            {'op':'add_door','mins':[0,0,0],'maxs':[8,8,8],'locked':'yes'}]:
            with self.subTest(op=op), self.assertRaises(OpError): self.apply(op)

    def test_checks_detect_missing_targets_and_body(self):
        self.apply({'op':'add_door','mins':[0,0,0],'maxs':[8,96,112], 'open_by':'script'})
        self.apply({'op':'add_button','mins':[0,0,0],'maxs':[8,8,8],
                    'targets':[{'name':'missing','input':'Open'}]})
        physics.entity(self.p,self.m,'prop_physics',{'model':'models/absent.vmdl'},[0,0,0])
        codes={x['code'] for x in physics.check_physics(self.p,self.m)}
        self.assertTrue({'door_no_activation','physics_io_target_missing','physics_model_no_body'} <= codes)

    def test_compiled_body_gate(self):
        info=physics.model_physics(self.p,'models/props/de_nuke/crate_small.vmdl')
        self.assertTrue(info['has_physics'])
        info=physics.model_physics(self.p,'models/warehouse/warehouse_crate_001.vmdl')
        self.assertFalse(info['has_physics'])
        with self.assertRaises(OpError):
            self.apply({'op':'add_physics_prop','model':'models/warehouse/warehouse_crate_001.vmdl'})


class PhysicsInput(unittest.TestCase):
    def test_names_and_directions(self):
        for bad in ['x;quit','a b','*','../map','x\nquit']:
            with self.assertRaises(OpError): physics.name(bad)
        for alias,expected in [('up',[0,0,1]),('-x',[-1,0,0]),('+y',[0,1,0])]:
            _, actual=physics.direction(alias)
            for a,b in zip(actual,expected): self.assertAlmostEqual(a,b)
