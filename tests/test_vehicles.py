"""Car schema, registry and complete assembled runtime on the shared engine double."""
import copy
import json
import unittest
from unittest.mock import patch

from cs2map import control, mechanics, vehicles


class Schema(unittest.TestCase):
    def test_defaults_stable_and_own_names(self):
        s = control.validate({'name': 'car1', 'kind': 'car'})
        self.assertEqual(control.validate(s), s)
        self.assertEqual((s['speed'], s['health'], s['mass'], s['reach']), (700, 150, 1500, 80))
        self.assertEqual(s['camera']['start'], 'chase')
        self.assertEqual(control.validate({'name':'car1','kind':'car','camera':{'chase':{'forward':-220,'up':90}}})['camera']['start'],'chase')
        self.assertEqual(control.validate({'name':'car1','kind':'car','camera':{'chase':False}})['camera']['start'],'fpv')
        code, xml, css, ops = control.generate(s)
        self.assertEqual(mechanics.read_marker(code, control.MARKER), s)
        self.assertEqual(code.count('function Seat('), 1)
        self.assertEqual(code.count('function Health('), 1)
        self.assertTrue(all(o['keyvalues']['targetname'].startswith(('v9_', 'mcp_v_')) for o in ops))
        body = next(o for o in ops if o['classname'] == 'prop_physics_override')
        self.assertEqual(body['keyvalues']['health'], 100000)
        self.assertEqual(len([o for o in ops if '_wheel_' in o['keyvalues']['targetname']]), 4)
        self.assertIn('E: exit', xml)

    def test_bad_numbers_fields_and_sound(self):
        for key, value in [('speed', float('nan')), ('mass', True), ('steer', 46), ('grip', 0),
                ('reach', 81), ('origin', [0, 0]), ('wheel_model', '../x.vmdl'), ('engine', {}),
                ('engine', {'idle': 'x', 'drive': 'x;quit'}), ('sound', 'motor'), ('physics', False), ('rotors', [])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                control.validate({'name': 'car1', 'kind': 'car', key: value})

    def test_relative_origin_and_yaw_wheel_positions(self):
        with patch.object(vehicles.layout, 'point', return_value=([100, 200, 8], None)):
            s = control.validate({'name': 'car1', 'kind': 'car', 'origin': {'room': 'yard'}, 'yaw': 90}, object())
        ops = vehicles.entities(s, control.names(s))
        front = next(o for o in ops if o['keyvalues']['targetname'] == 'v9_car1_wheel_0')
        self.assertAlmostEqual(front['origin'][0], 64)
        self.assertAlmostEqual(front['origin'][1], 262)
        with self.assertRaisesRegex(ValueError, 'map layout'):
            control.validate({'name': 'car1', 'kind': 'car', 'origin': {'room': 'yard'}})

    def test_wheels_steering_suspension_and_seat_are_settings(self):
        spec = control.validate({'name': 'car1', 'kind': 'car'})
        self.assertEqual(spec['wheels'], {'positions': [w[:2] for w in vehicles.WHEELS], 'radius': 16, 'z': 16, 'scale': 1})
        self.assertEqual(spec['steer'], vehicles.STEER)
        self.assertEqual(spec['suspension'], vehicles.SUSPENSION)
        self.assertEqual(spec['seat'], [-6, 17, 40])
        js = (vehicles.DATA / 'car.js').read_text(encoding='utf-8')
        self.assertIn('const W = config.wheels, S = config.suspension, ST = config.steer;', js)
        old = control.validate({'name': 'car1', 'kind': 'car', 'steer': 25})['steer']
        self.assertEqual((old['max'], old['fast']), (25, 9), 'the old number is the low-speed angle')
        own = control.validate({'name': 'car1', 'kind': 'car', 'steer': {'max': 40, 'fast': 6, 'rate': 90},
                                'wheels': {'positions': [[80, 40], [80, -40], [-70, 40], [-70, -40]], 'radius': 20},
                                'suspension': {'up': 14, 'down': 20, 'grip': 1.1}, 'seat': [0, 18, 46]})
        self.assertEqual((own['steer']['return'], own['wheels']['z'], own['wheels']['scale']), (200, 20, 1.25))
        self.assertEqual((own['suspension']['down'], own['suspension']['stiffness']), (20, 16))
        again = control.validate(dict(own))
        self.assertEqual(again['wheels'], own['wheels'], 'validation is idempotent (it runs twice)')
        for bad in ({'steer': {'max': 10, 'fast': 20}}, {'steer': {'speed': 1}}, {'wheels': {'positions': [[1, 2]]}},
                    {'wheels': {'positions': [[-80, 40], [-80, -40], [70, 40], [70, -40]]}}, {'wheels': {'radius': 200}},
                    {'suspension': {'grip': 9}}, {'suspension': {'spring': 1}}, {'seat': [0, 0]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                control.validate({'name': 'car1', 'kind': 'car', **bad})

    def test_preview_does_not_build_assets(self):
        spec = control.validate({'name': 'car1', 'kind': 'car'})
        with patch.object(vehicles, 'ensure_assets') as build:
            token = control.PREVIEW.set(True)
            try:
                vehicles.prepare(None, spec)
            finally:
                control.PREVIEW.reset(token)
            build.assert_not_called()


SETUP = r'''
D.Entity.prototype.GetParent=function(){return this.parent;};
for (const op of OPS) {
    const name=op.keyvalues.targetname;
    const ent=op.classname==='custom_hud_layout'?new D.Hud(name):new D.Entity(name);
    ent.pos=core.vec(op.origin); ent.ang={pitch:op.angles[0],yaw:op.angles[1],roll:op.angles[2]};
    D.entities[name]=ent;
}
D.pawn.pos={x:8,y:66,z:8};
D.T.traceLine=c=>c.ignorePlayers ? {didHit:true,startedInSolid:false,fraction:.8,end:{...c.start,z:0},normal:{x:0,y:0,z:1}}
    : {didHit:true,startedInSolid:false,hitEntity:D.entities[E.body],end:c.end,normal:{x:0,y:1,z:0}};
D.H.OnActivate();
'''


@unittest.skipUnless(mechanics.node_path(), 'Node unavailable')
class Runtime(unittest.TestCase):
    def run_js(self, test, **kw):
        spec = control.validate({'name': 'car1', 'kind': 'car', 'origin': [0,0,0], **kw})
        code, _, _, ops = control.generate(spec)
        return mechanics.run_harness(code, 'const OPS='+json.dumps(ops)+';\n'+SETUP+test+'\nconsole.log("OK");')

    def test_steering_is_lighter_when_slow_and_heavier_when_fast(self):
        self.run_js(r'''
        D.tick(CSInputs.USE);
        D.advance(.15,CSInputs.LEFT); const slowRate=steerAngle;          // standing: 120 deg/s
        D.advance(1,CSInputs.LEFT); D.assert.ok(Math.abs(steerAngle-32)<.01,'full lock at rest: '+steerAngle);
        D.advance(1,0); D.assert.equal(steerAngle,0,'back to the centre');
        D.advance(3,CSInputs.FORWARD); const v=Math.abs(longitudinal); D.assert.ok(v>550,'fast: '+v);
        D.advance(.15,CSInputs.FORWARD|CSInputs.LEFT); const fastRate=steerAngle;
        D.assert.ok(fastRate<slowRate*.75,'the wheel turns slower at speed: '+fastRate+' vs '+slowRate);
        D.advance(1,CSInputs.FORWARD|CSInputs.LEFT);
        const lock=32+(9-32)*Math.min(1,Math.abs(longitudinal)/700);
        D.assert.ok(steerAngle<15 && Math.abs(steerAngle-lock)<2,'small angle at speed: '+steerAngle);
        ''')

    def test_enter_camera_speed_steering_brake_reverse_handbrake_exit(self):
        self.run_js(r'''
        D.tick(CSInputs.USE); D.assert.equal(seat.player,D.pawn); D.assert.equal(camera.chase,true);
        D.assert.equal(D.pawn.color.a,0); D.assert.equal(decoy.enabled,true);
        D.advance(1,CSInputs.FORWARD); const before=body.GetAbsVelocity().x;
        D.assert.ok(before>250 && before<320, before); D.assert.equal(grounded,true); D.assert.equal(contacts,4);
        D.assert.deepEqual(travel,[0,0,0,0],'level ground: wheels at rest');
        D.advance(.3,CSInputs.FORWARD|CSInputs.LEFT); D.assert.ok(body.ang.yaw>0); D.assert.ok(steerAngle>0);
        D.advance(.1,CSInputs.RELOAD); D.assert.equal(camera.chase,false); D.assert.ok(D.pawn.cam.follow.followOffset.z===72 && D.pawn.cam.follow.cameraOffset.x===0);   // the eye stays in the cabin whatever the view pitch
        D.advance(.7,CSInputs.JUMP); D.assert.ok(Math.hypot(body.vel.x,body.vel.y)<40);
        D.advance(1,CSInputs.BACK); D.assert.ok(longitudinal<0);
        D.tick(0); D.tick(CSInputs.USE); D.assert.equal(seat.player,undefined);
        D.assert.equal(D.pawn.mode,1); D.assert.equal(D.pawn.color.a,255); D.assert.equal(D.pawn.cam.GetMode(),0);
        D.assert.equal(decoy.enabled,false); D.assert.equal(D.pawn.weapon,D.pawn.pistol);
        ''')

    def test_no_thrust_gravity_cancellation_or_air_steering(self):
        self.run_js(r'''
        D.tick(CSInputs.USE); D.tick(0); body.vel={x:120,y:35,z:-80};
        D.T.traceLine=c=>({didHit:false,startedInSolid:false,end:c.end,normal:{x:0,y:0,z:1}});
        const before=body.teleports, angle=body.ang.yaw;
        D.advance(.5,CSInputs.FORWARD|CSInputs.LEFT|CSInputs.JUMP);
        D.assert.equal(grounded,false); D.assert.deepEqual(body.vel,{x:120,y:35,z:-80});
        D.assert.equal(body.teleports,before); D.assert.equal(body.ang.yaw,angle);
        ''')

    SLOPE = r'''
    // Ground plane z = k*x (rising toward +x): rays along the body's down axis hit it exactly.
    const slope=k=>c=>{
        const dx=c.end.x-c.start.x, dz=c.end.z-c.start.z, t=(k*c.start.x-c.start.z)/(dz-k*dx);
        if(!(t>=0&&t<=1)) return {didHit:false,startedInSolid:false,end:c.end};
        const n=Math.hypot(k,1);
        return {didHit:true,startedInSolid:false,end:{x:c.start.x+t*dx,y:c.end.y,z:c.start.z+t*dz},normal:{x:-k/n,y:0,z:1/n}};
    };
    // The engine applies gravity in its own step after the script's velocity (the script pre-adds it).
    const tp=body.Teleport.bind(body); body.Teleport=v=>{tp(v); if(v.velocity) body.vel.z-=800*.016;};
    '''

    def test_slopes_hold_within_friction_and_slide_beyond(self):
        self.run_js(self.SLOPE + r'''
        const flat=D.T.traceLine; D.tick(CSInputs.USE); D.tick(0); D.assert.equal(seat.player,D.pawn);
        D.T.traceLine=slope(Math.tan(30*Math.PI/180)); body.ang={pitch:-30,yaw:0,roll:0}; body.vel=zero;
        D.advance(1,CSInputs.JUMP); D.assert.ok(Math.abs(longitudinal)<1,'handbrake on 30 deg: held');
        D.assert.ok(Math.abs(body.ang.pitch+30)<1.5,'pose follows the slope'); D.assert.equal(contacts,4);
        D.advance(.5); D.assert.ok(longitudinal<-20,'coasting rolls back down');
        body.vel=zero; D.advance(1,CSInputs.FORWARD); D.assert.ok(longitudinal>40,'climbs 30 deg');
        D.assert.ok(longitudinal<300*1.05,'uphill accel is limited by friction');
        D.T.traceLine=slope(Math.tan(45*Math.PI/180)); body.ang={pitch:-45,yaw:0,roll:0}; body.vel=zero;
        D.advance(1,CSInputs.JUMP); D.assert.ok(longitudinal<-60,'45 deg: tyres cannot hold, it slides back');
        ''')

    def test_step_lifts_the_front_wheels_and_the_nose(self):
        self.run_js(r'''
        D.tick(CSInputs.USE); D.tick(0);
        // A 12-unit step under the front axle (x=62; the double does not move the body, so it stays there).
        D.T.traceLine=c=>c.ignorePlayers?{didHit:true,startedInSolid:false,end:{...c.start,z:c.start.x>=45?12:0},normal:{x:0,y:0,z:1}}
            :{didHit:false,startedInSolid:false,end:c.end};
        D.tick(CSInputs.FORWARD);
        D.assert.equal(travel[0],9,'front wheels pushed up to the bump stop'); D.assert.ok(travel[2]<1);
        D.advance(.2,CSInputs.FORWARD); D.assert.ok(body.ang.pitch<-1,'nose rises over the step');
        D.assert.ok(body.vel.z>12.8,'body lifted');
        ''')

    def test_unsupported_front_falls_back_to_the_ground(self):
        self.run_js(r'''
        D.tick(CSInputs.USE); D.tick(0);
        // Nose up 15 deg on flat ground (left over from a slope): the front tyres hang in the air.
        body.ang={pitch:-15,yaw:0,roll:0};
        D.T.traceLine=c=>{ if(!c.ignorePlayers) return {didHit:false,startedInSolid:false,end:c.end};
            const t=(0-c.start.z)/(c.end.z-c.start.z); if(!(t>=0&&t<=1)) return {didHit:false,startedInSolid:false,end:c.end};
            return {didHit:true,startedInSolid:false,end:{x:c.start.x+t*(c.end.x-c.start.x),y:c.start.y+t*(c.end.y-c.start.y),z:0},normal:{x:0,y:0,z:1}}; };
        D.tick(0); D.assert.equal(touch[0]||touch[1],false,'front in the air');
        D.advance(1.5); D.assert.ok(body.ang.pitch>-3,'gravity brought the nose down: '+body.ang.pitch);
        D.assert.equal(contacts,4);
        ''')

    def test_chase_view_swings_behind_the_moving_car(self):
        self.run_js(r'''
        D.tick(CSInputs.USE); D.tick(0); D.assert.equal(camera.chase,true);
        D.pawn.ang={pitch:10,yaw:90,roll:0};
        D.advance(2.5,CSInputs.FORWARD);
        D.assert.ok(Math.abs(D.pawn.GetEyeAngles().yaw)<15,'view behind the car: '+D.pawn.GetEyeAngles().yaw);
        D.assert.equal(D.pawn.GetEyeAngles().pitch,10,'the pitch stays the player one');
        ''')

    def test_wreck_stays_on_its_wheels(self):
        self.run_js(r'''
        D.tick(0); body.health-=1000; D.tick(0); D.assert.equal(health.dead,true);
        D.advance(.5); D.assert.equal(contacts,4); D.assert.deepEqual(travel,[0,0,0,0]);
        ''')

    def test_exit_alternative_wall_and_correct_eye_angles(self):
        self.run_js(r'''
        D.tick(CSInputs.USE); D.tick(0); D.pawn.ang={pitch:23,yaw:117,roll:0};
        D.T.traceBox=c=>c.start===c.end?{didHit:false,startedInSolid:false}:
            c.start.y>0?{startedInSolid:true,didHit:true}:{startedInSolid:false,didHit:true,end:{x:8,y:-76,z:0},normal:{z:1}};
        D.tick(CSInputs.USE); D.assert.equal(D.pawn.pos.y,-76); D.assert.equal(D.pawn.pos.z,2);
        D.assert.equal(D.pawn.GetEyeAngles().pitch,23); D.assert.equal(D.pawn.GetEyeAngles().yaw,117);
        ''')

    def test_driver_door_refusal_and_no_stationary_yaw(self):
        self.run_js(r'''
        D.pawn.pos={x:8,y:-66,z:8}; D.tick(CSInputs.USE); D.assert.equal(seat.player,undefined);
        D.assert.ok(D.last('refused').includes('driver_door'));
        D.pawn.pos={x:8,y:66,z:8}; D.tick(0); D.tick(CSInputs.USE); D.advance(.8,CSInputs.LEFT);
        D.assert.equal(body.ang.yaw,0); D.assert.ok(steerAngle>20);
        ''')

    def test_roof_exit_and_refusal_when_every_free_space_disappears(self):
        self.run_js(r'''
        D.tick(CSInputs.USE); D.tick(0);
        D.T.traceBox=c=>c.start===c.end ? {didHit:false,startedInSolid:false}:
            c.start.z>120?{didHit:true,startedInSolid:false,end:{x:0,y:0,z:68},normal:{z:1}}:{didHit:true,startedInSolid:true};
        D.tick(CSInputs.USE); D.assert.equal(D.pawn.pos.z,70); D.assert.equal(seat.player,undefined);
        D.pawn.pos={x:8,y:66,z:8}; D.tick(0); D.tick(CSInputs.USE); D.tick(0);
        D.T.traceBox=c=>({startedInSolid:true,didHit:true});
        D.tick(CSInputs.USE); D.assert.equal(seat.player,D.pawn,'E cannot teleport into a solid');
        D.assert.equal(seat.exit('input'),false); D.assert.equal(seat.player,D.pawn);
        D.assert.ok(D.last('exit_blocked'));
        ''')

    def test_health_native_crash_destroy_respawn_round_death(self):
        self.run_js(r'''
        D.tick(CSInputs.USE); D.tick(0); body.health-=35; D.tick(0); D.assert.equal(health.hp,115);
        lastVelocity={x:500,y:0,z:0}; body.vel=zero;
        D.T.traceLine=c=>c.start.z===40?{didHit:true,normal:{z:0},end:c.end}:
            {didHit:true,startedInSolid:false,end:{...c.start,z:0},normal:{x:0,y:0,z:1},hitEntity:body};
        D.tick(0); D.assert.ok(health.hp<115); D.assert.ok(D.last('crash'));
        body.health-=1000; D.tick(0); D.assert.equal(health.dead,true); D.assert.equal(seat.player,undefined);
        D.assert.equal(D.pawn.cam.GetMode(),0); D.assert.equal(D.pawn.color.a,255);
        D.advance(10.2); D.assert.equal(health.dead,false); D.assert.equal(health.hp,150);
        D.pawn.pos={x:8,y:66,z:8}; D.tick(0); D.tick(CSInputs.USE); D.H.OnBeginRoundRestart();
        D.assert.equal(seat.player,undefined); D.assert.equal(D.pawn.cam.GetMode(),0);
        D.tick(0); D.tick(CSInputs.USE); D.pawn.alive=false; D.H.OnPlayerKill({player:D.pawn});
        D.assert.equal(seat.player,undefined); D.assert.equal(D.pawn.color.a,255);
        ''')

    def test_runover_hits_bot_not_driver_or_through_wall(self):
        self.run_js(r'''
        D.tick(CSInputs.USE); const bot=new D.Pawn(); bot.pos={x:104,y:0,z:0};
        const bots={IsValid:()=>true,IsBot:()=>true,GetPlayerPawn:()=>bot};
        Instance.GetAllPlayerControllers=()=>[D.controller,bots];
        D.T.traceLine=c=>({didHit:false,startedInSolid:false,end:c.end,normal:{z:1}});
        body.vel={x:400,y:0,z:0}; D.tick(0); D.assert.ok(bot.health<100); D.assert.equal(D.pawn.health,100);
        const hp=bot.health; D.tick(0); D.assert.equal(bot.health,hp,'cooldown');
        D.advance(1); bot.health=100;
        D.T.traceLine=c=>({didHit:true,startedInSolid:false,end:c.end,hitEntity:new D.Entity('wall'),normal:{z:0}});
        D.advance(1); D.assert.equal(bot.health,100,'wall blocks damage');
        ''')

    def test_parts_stop_updating_when_stationary_and_state_is_bounded(self):
        self.run_js(r'''
        D.tick(0); const before=wheels.map(w=>w.teleports); D.advance(1);
        D.assert.deepEqual(wheels.map(w=>w.teleports),before);
        for(let i=0;i<20;i++) core.log('probe',{i});
        const s=D.state('v9_control_car1_state','tok'); D.assert.equal(s.token,'tok');
        D.assert.equal(s.recent.length,8); D.assert.equal(s.wheels.length,4); D.assert.equal(s.active,false);
        ''')


if __name__ == '__main__':
    unittest.main()
