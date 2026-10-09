"""Schema and Node-engine checks for the T9 stationary turret adapter."""
import json
import unittest

from cs2map import control, mechanics, turrets


def spec(**extra):
    value = {'name': 'gun1', 'kind': 'turret', 'origin': [0, 0, 0], **extra}
    return control.validate(value)


class Schema(unittest.TestCase):
    def test_defaults_and_rate_translation(self):
        s = spec(fire={'rate': .1, 'damage': 18, 'spread': 1.5})
        self.assertEqual((s['health'], s['limits'], s['turn_rate']), (200, {'yaw': [-60, 60], 'pitch': [-10, 30]}, 120))
        self.assertEqual((s['fire']['cooldown'], s['fire']['damage'], s['fire']['spread']), (.1, 18, 1.5))
        self.assertEqual(s['fire']['sound'], ['mcp.t9_shot'])
        code, xml, css, ops = control.generate(s)
        self.assertIn('Heat({core', code)
        self.assertIn('Fire({core', code)
        self.assertIn('HUDHINT', (turrets.DATA / 'hud.xml').read_text(encoding='utf-8'))
        self.assertTrue(any(o['keyvalues'].get('targetname') == 't9_gun1_barrel' for o in ops))
        self.assertTrue(any(o['keyvalues'].get('targetname') == 't9_gun1_beam_0' for o in ops))
        self.assertTrue(any(o['keyvalues'].get('targetname') == 't9_gun1_shot0' for o in ops))
        self.assertNotIn('targetname": [', json.dumps(ops))

    def test_prompt_heat_spelling_and_recorded_roundtrip(self):
        s = spec(fire={'rate': .1, 'heat': .2, 'cool': .35})
        self.assertEqual(s['heat'], {'gain': .2, 'cool': .35})
        self.assertEqual(control.validate(s), s)
        with self.assertRaises(ValueError):
            spec(fire={'heat': .2}, heat={'gain': .1, 'cool': .3})

    def test_reject_bad_limits_and_heat(self):
        for bad in ({'limits': {'yaw': [1, 1], 'pitch': [-10, 30]}},
                    {'limits': {'yaw': [-181, 60], 'pitch': [-10, 30]}},
                    {'heat': {'gain': 0, 'cool': .3}},
                    {'turn_rate': 0}, {'auto': True}, {'fire': {'rate': .1, 'cooldown': .2}},
                    {'fire': {'unknown': 1}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                spec(**bad)


SETUP = r'''
const OPS=OPS_JSON;
for (const op of OPS) { const n=op.keyvalues.targetname; const e=op.classname==='custom_hud_layout'?new D.Hud(n):new D.Entity(n); e.pos=core.vec(op.origin); e.ang={pitch:op.angles[0],yaw:op.angles[1],roll:op.angles[2]}; D.entities[n]=e; }
D.pawn.pos={x:0,y:40,z:0}; D.pawn.ang={pitch:0,yaw:0,roll:0};
D.T.traceLine=c=>({didHit:true,startedInSolid:false,hitEntity:D.entities['t9_gun1_body'],end:c.end});
D.T.traceBullet=c=>[];
D.H.OnActivate();
'''


@unittest.skipUnless(mechanics.node_path(), 'Node unavailable')
class Runtime(unittest.TestCase):
    def run_js(self, test, **extra):
        s = spec(**extra)
        code, _, _, ops = control.generate(s)
        setup = SETUP.replace('OPS_JSON', json.dumps(ops))
        return mechanics.run_harness(code, setup + '\n' + test + '\nconsole.log("TURRET_OK");')

    def test_enter_aim_rate_fire_and_heat(self):
        self.run_js(r'''
D.tick(CSInputs.USE); D.tick(0); D.assert.equal(seat.player,D.pawn); D.assert.equal(camera.mode(),3);
D.pawn.ang={pitch:0,yaw:110,roll:0}; D.advance(.4,0); D.assert.equal(turretYaw,20);
// The gunner stands 52 units behind the gun (world yaw 110): never in front of the barrel.
const r=110*Math.PI/180, base=body.GetAbsOrigin();
D.assert.ok(Math.abs(decoy.pos.x-(base.x-Math.cos(r)*52))<.01 && Math.abs(decoy.pos.y-(base.y-Math.sin(r)*52))<.01);
D.assert.equal(decoy.ang.yaw,110); D.assert.deepEqual(D.pawn.pos,decoy.pos);
D.pawn.ang={pitch:-40,yaw:170,roll:0}; D.advance(.25,0); D.assert.ok(turretYaw<=30 && turretYaw>=0); D.assert.ok(turretPitch>=0 && turretPitch<=30);
D.advance(.65,CSInputs.ATTACK); D.assert.ok(D.count('shot')>4); D.assert.ok(heat.value>.2);
D.tick(0); D.tick(CSInputs.USE); D.assert.equal(seat.player,undefined); D.assert.equal(D.pawn.color.a,255);
''', yaw=90, fire={'rate': .05, 'damage': 18}, limits={'yaw': [-30, 30], 'pitch': [-10, 30]})

    def test_overheat_locks_until_cool_and_health_exit(self):
        self.run_js(r'''
D.tick(CSInputs.USE); D.tick(0); D.advance(.12,CSInputs.ATTACK); D.assert.equal(heat.locked,true); const shots=D.count('shot');
D.advance(.2,CSInputs.ATTACK); D.assert.equal(D.count('shot'),shots);
D.advance(1.1,0); D.assert.equal(heat.locked,false);
body.health-=40; D.tick(0); D.assert.equal(health.hp,160); D.pawn.alive=false; D.H.OnPlayerKill({player:D.pawn}); D.assert.equal(seat.player,undefined);
''', fire={'rate': .01}, heat={'gain': 1, 'cool': 1})

    def test_exit_after_turning_finds_the_nearest_free_spot(self):
        self.run_js(r'''
// The turret turned onto the spot where the player stood (0,40): that hull is now solid.
const blocked = p => Math.abs(p.x) < 30 && Math.abs(p.y - 40) < 30;
D.T.traceBox = c => c.start.z === c.end.z ? {didHit: blocked(c.start), startedInSolid: blocked(c.start)}
    : {didHit: true, startedInSolid: false, end: {...c.end, z: 0}, normal: {x: 0, y: 0, z: 1}};
D.tick(CSInputs.USE); D.tick(0); D.pawn.ang = {pitch: 0, yaw: 150, roll: 0}; D.advance(.5, 0);
D.tick(0); D.tick(CSInputs.USE); D.assert.equal(seat.player, undefined);
D.assert.ok(!blocked(D.pawn.pos), "out of the turret: " + JSON.stringify(D.pawn.pos));
const d = Math.hypot(D.pawn.pos.x, D.pawn.pos.y - 40);
D.assert.ok(d >= 39.9 && d <= 64.1, "the nearest free spot, not a far one: " + d);
D.assert.ok(D.count('exit_moved') === 1);
D.assert.equal(D.pawn.GetMoveType(), 1, "walking again");
''', yaw=90, limits={'yaw': [-90, 90], 'pitch': [-10, 30]})

    def test_heat_counts_reload_tick_and_wrapped_queue(self):
        self.run_js(r'''
D.tick(CSInputs.USE); D.tick(0); D.advance(.8,CSInputs.ATTACK);
D.assert.ok(D.count('shot')>4); D.assert.ok(heat.value>.5);
''', fire={'rate': .01, 'magazine': 1, 'reload': .1}, heat={'gain': .1, 'cool': .001})


if __name__ == '__main__':
    unittest.main()
