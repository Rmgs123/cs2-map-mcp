import copy
import math
import time
import unittest
from unittest.mock import patch

from cs2map import route


class RouteValidation(unittest.TestCase):
    def test_rejects_invalid_plan_before_live_commands(self):
        for plan in ([], 'teleport', [{'go':[0,0,float('nan')]}], [{'go':[1,2,3], 'wait':1}],
                     [{'go':[1,2,3], 'jump':'yes'}], [{'ride':'r_lift'}], [{'use':'x;kill'}],
                     [{'go':[1,2,3], 'cmd':'noclip 1'}], [{'wait':1,'jump':True}]):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                route.validate(plan)
        plan=[{'go':[10,20,0],'jump':True,'duck':False},{'ride':'r_lift','to_z':144}]
        self.assertEqual(route.validate(plan),plan)
        self.assertIsNot(route.validate(plan),plan)
        for plan in ([{'climb_down':[1,2,0],'top':[1,2,10]}],
                     [{'climb_down':{'to':[0,0,0],'bounds':{'mins':[0,0,0],'maxs':[0,2,3]}}}],
                     [{'break':'crate','allow_damage':1}], [{'push':'crate','to':[1,2,float('inf')]}]):
            with self.subTest(plan=plan),self.assertRaises(ValueError):route.validate(plan)

    def test_round5_steps_and_start_are_validated(self):
        plan = route.validate([
            {'climb_down': [10, 20, 0], 'allow_damage': True},
            {'break': 'r_window', 'within': 8},
            {'push': 'r_crate', 'to': [100, 20, 0]},
        ])
        self.assertEqual(plan[0]['climb_down'], {'to': [10, 20, 0]})
        self.assertTrue(plan[0]['allow_damage'])
        self.assertEqual(route.validate([{'go': [0, 0, 0]}]), [{'go': [0, 0, 0]}])

    def test_damage_can_be_explicitly_allowed(self):
        state = {'alive': True, 'noclip': False, 'health': 60,
                 'pos': [0, 0, -120], 'velocity': [0, 0, -200]}
        self.assertIsNone(route.failure(state, [100, 0, 0], 100,
                                        allow_damage=True))

    def test_failure_uses_engine_evidence(self):
        state={'alive':True,'noclip':False,'health':100,'pos':[0,0,0],'velocity':[0,0,0]}
        self.assertIsNone(route.failure(state,[100,0,0],100))
        self.assertEqual(route.failure({**state,'noclip':True},[100,0,0],100),'noclip_enabled')
        self.assertEqual(route.failure({**state,'alive':False},None,100),'death')
        self.assertEqual(route.failure({**state,'health':80},None,100),'damage')
        self.assertEqual(route.failure({**state,'pos':[0,0,-120],'velocity':[0,0,-200]},[100,0,0],100),'fall')
        self.assertEqual(route.failure({**state,'obstacle':{'hit':True,'normal':[-1,0,0]}},[100,0,0],100,True),'wall')
        self.assertEqual(route.failure(state,[100,0,144],100,True),'height')

    def test_aim_is_from_eye_to_target_in_all_quadrants(self):
        self.assertEqual(route.angles([0,0,64],[0,100,64]),[0,90,0])
        self.assertEqual(route.angles([0,0,64],[-100,0,64]),[0,180,0])
        self.assertAlmostEqual(route.angles([0,0,64],[100,0,164])[0],-45)

    def test_stop_inputs_even_when_live_motion_raises(self):
        d=route.Driver(None,time.monotonic()+30,[])
        d.timeout=30
        d.probe=lambda goal: {'alive':True,'noclip':False,'health':100,'pos':[0,0,0]}
        commands=[]
        d.command=commands.append
        d.pause=lambda seconds: (_ for _ in ()).throw(RuntimeError('lost console'))
        with self.assertRaisesRegex(RuntimeError,'lost console'):
            d.go({'go':[100,0,0]})
        self.assertIn('-forward\n-jump\n-duck',commands)
        self.assertFalse(any('setpos' in c or 'noclip 1' in c for c in commands))

    def test_use_releases_key_after_probe_failure(self):
        from types import SimpleNamespace
        d=route.Driver(SimpleNamespace(load=lambda name: None),time.monotonic()+30,[])
        d.map='r_fixture'
        state={'pos':[0,0,0],'alive':True,'noclip':False}
        calls=iter([state,RuntimeError('lost console')])
        def probe():
            value=next(calls)
            if isinstance(value,Exception):raise value
            return value
        d.probe=probe
        commands=[];d.command=commands.append;d.pause=lambda seconds:None
        with patch.object(route,'target_position',return_value=[48,0,64]),self.assertRaises(RuntimeError):
            d.use('r_button')
        self.assertEqual(commands[-1],'-use')

    def test_held_inputs_are_not_pressed_twice(self):
        d=route.Driver(None,time.monotonic()+30,[])
        sent=[]
        def acknowledge(commands,token,**kwargs):
            sent.extend(commands);return True,[token]
        with patch.object(route.vconsole,'run_until',side_effect=acknowledge):
            d.command('+forward');d.command('setang 80 0 0\n+forward')
            d.command('-forward');d.command('+forward');d.command('-forward')
        self.assertEqual(sent.count('+forward'),2)
        self.assertEqual(sent.count('-forward'),2)

    def test_generic_zero_health_is_not_proof_of_destruction(self):
        from types import SimpleNamespace
        node={'origin':[48,0,64]}
        m=SimpleNamespace(entities=lambda **kwargs:[node],classname=lambda n:'func_breakable')
        d=route.Driver(SimpleNamespace(load=lambda name:m),time.monotonic()+30,[]);d.map='r_fixture'
        d.entity=lambda name:{'found':True,'origin':[48,0,64],'health':0,'alive':False}
        d.probe=lambda:{'alive':True,'noclip':False,'health':100,'pos':[0,0,0]}
        commands=[];d.command=commands.append;d.pause=lambda seconds:None
        ticks=iter(i/10 for i in range(100))
        with patch.object(route,'target_position',return_value=[48,0,64]),patch.object(route.time,'monotonic',side_effect=lambda:next(ticks)):
            self.assertEqual(d.break_target({'break':'r_glass','within':.5}),'break_timeout')
        self.assertEqual(commands[-1],'-attack')


class RouteLiftGeometry(unittest.TestCase):
    def test_closed_boxes_and_lift_floor_conflict_without_aabb_hallucination(self):
        from tests.helpers import HAVE_CS2,SETTINGS
        if not HAVE_CS2:self.skipTest('installed template unavailable')
        from cs2map import mesh
        from cs2map.project import Project
        from cs2map.vmap import VMap
        p=Project();m=VMap.from_template(SETTINGS.cs2_path,'empty')
        lift=m.new_entity('func_movelinear',{'targetname':'r_lift','movedir':'-90 0 0','movedistance':256})
        m.attach(lift)
        platform=m.new_mesh(mesh.box([0,0,0],[128,128,12]));m.attach(platform,lift)
        slab=m.new_mesh(mesh.box([-10,-10,256],[140,140,272]));m.attach(slab)
        self.assertIsNotNone(route._closed_box(slab))
        self.assertEqual([c['code'] for c in route.check_route(p,m)],['route_lift_obstruction'])
        m.remove(slab)
        beside=m.new_mesh(mesh.box([144,-10,256],[300,140,272]));m.attach(beside)
        self.assertEqual(route.check_route(p,m),[])
        # Same AABB with an open side must not be accepted as a solid box.
        open_mesh=mesh.box([-10,-10,256],[140,140,272]);open_mesh.faces.pop()
        open_node=m.new_mesh(open_mesh);m.attach(open_node)
        self.assertIsNone(route._closed_box(open_node))
        self.assertEqual(route.check_route(p,m),[])


class RouteRound5Planner(unittest.TestCase):
    def _warehouse(self, with_lift=False, with_ladder=False):
        from tests.helpers import SETTINGS
        from tests.test_layout import warehouse
        from cs2map import layout, physics
        from cs2map.project import Project
        from cs2map.vmap import VMap
        p = Project(); m = VMap.from_template(SETTINGS.cs2_path, 'basic'); refs = {}
        p.register_op('add_layout', layout.add_layout)
        p.register_op('add_lift', layout.add_lift)
        for name, fn in physics.OPERATIONS.items(): p.register_op(name, fn)
        p.register_preprocessor(layout.preprocess)
        p._apply(m, warehouse(), refs)
        if with_lift: p._apply(m, {'op': 'add_lift', 'room': 'hall', 'upper': 'mezz',
                                  'targetname': 'r_lift'}, refs)
        if with_ladder:
            r = layout.plans(m)[0]['rooms']['mezz']; y = (r['interior']['mins'][1] + r['interior']['maxs'][1]) / 2
            p._apply(m, {'op': 'add_ladder', 'mins': [r['interior']['mins'][0] - 16, y - 16, 0],
                         'maxs': [r['interior']['mins'][0], y + 16, r['floor_z']]}, refs)
        return m, layout.plans(m)[0]['rooms']

    def test_auto_plans_lift_and_ladder_descent_from_metadata(self):
        m, rooms = self._warehouse(with_lift=True, with_ladder=False)
        centre = lambda name: [sum(rooms[name]['interior'][k][i] for k in ('mins', 'maxs')) / 2
                               for i in range(2)] + [rooms[name]['floor_z']]
        lift = route.plan_between(m, centre('back'), centre('mezz'))
        self.assertEqual([x.get('ride') for x in lift if 'ride' in x], ['r_lift'])
        m, rooms = self._warehouse(with_lift=False, with_ladder=True)
        centre = lambda name: [sum(rooms[name]['interior'][k][i] for k in ('mins', 'maxs')) / 2
                               for i in range(2)] + [rooms[name]['floor_z']]
        ladder = route.plan_between(m, centre('mezz'), centre('hall'))
        self.assertTrue(any('climb_down' in x for x in ladder))

    def test_grid_fallback_keeps_direct_goal_when_no_layout(self):
        from tests.helpers import SETTINGS
        from cs2map import mesh
        from cs2map.project import Project
        from cs2map.vmap import VMap
        p = Project(); m = VMap.from_template(SETTINGS.cs2_path, 'basic')
        floor = m.new_mesh(mesh.box([0, 0, -16], [512, 256, 0])); m.attach(floor)
        self.assertTrue(route.plan_between(m, [32, 32, 0], [400, 32, 0]))

    def test_triangle_grid_routes_around_walls_and_rejects_floor_gaps(self):
        from tests.helpers import SETTINGS
        from cs2map import mesh
        from cs2map.vmap import VMap
        m=VMap.from_template(SETTINGS.cs2_path,'empty')
        floor=m.new_mesh(mesh.box([0,0,-16],[512,256,0]));m.attach(floor)
        wall=m.new_mesh(mesh.box([240,0,0],[272,160,128]));m.attach(wall)
        points=route._triangle_grid_waypoints(m,[48,48,0],[448,48,0])
        self.assertGreater(len(points),1)
        self.assertTrue(any(p[1]>=176 for p in points))
        m.remove(floor);m.remove(wall)
        for a,b in (([0,0,-16],[200,256,0]),([300,0,-16],[512,256,0])):
            m.attach(m.new_mesh(mesh.box(a,b)))
        self.assertEqual(route._triangle_grid_waypoints(m,[48,48,0],[448,48,0]),[])

    def test_room_interior_is_walkable_and_does_not_duplicate_floor_goal(self):
        from tests.helpers import SETTINGS
        from cs2map import mesh,shapes
        from cs2map.vmap import VMap
        m=VMap.from_template(SETTINGS.cs2_path,'empty')
        for part in shapes.build({'shape':'room','mins':[0,0,0],'maxs':[512,256,128],'thickness':16}):
            m.attach(m.new_mesh(part))
        self.assertEqual(route._triangle_grid_waypoints(m,[48,48,.03125],[448,48,8]),[[448,48,0]])
        self.assertEqual(len(route.plan_between(m,[48,48,.03125],[448,48,8])),1)

    def test_references_reject_invalid_interactions(self):
        from tests.helpers import SETTINGS
        from cs2map.vmap import VMap
        m=VMap.from_template(SETTINGS.cs2_path,'empty')
        e=m.new_entity('prop_dynamic',{'targetname':'r_decor'});m.attach(e)
        for step in ({'break':'r_missing'},{'push':'r_decor','to':[0,0,0]},{'break':'r_decor'}):
            with self.subTest(step=step),self.assertRaises(ValueError):route.validate_references(m,[step])
