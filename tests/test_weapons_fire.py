"""Compatible fire schema and runtime: spring/shake recovery, flash cleanup and audio headroom."""
import importlib.util
import unittest
from cs2map import fire, mechanics, weapons
from cs2map.project import OpError


class WeaponFire(unittest.TestCase):
    def test_new_fields_and_validation(self):
        f = fire.validate({'recoil': {'kick': 6, 'side': 1, 'return': .7},
                          'shake': {'amplitude': .4}, 'flash': {}, 'sound': ['mcp.crack', 'mcp.body'],
                          'reload_sound': 'mcp.reload', 'empty_sound': 'mcp.empty', 'kickback': 80})
        self.assertEqual(f['recoil']['return'], .7)
        self.assertEqual(f['empty_sound'], ['mcp.empty'])
        e = fire.names('test', f)
        definitions = fire.entity_defs(e, f, [0, 0, 40])
        light = next(kv for cls, key, kv, origin in definitions if key == 'flash_light')
        self.assertEqual(light['parentname'], e['flash_anchor'])
        self.assertEqual(light['directlight'], 2)
        old = fire.validate({})
        self.assertEqual(old['recoil'], 1)
        self.assertNotIn('flash_anchor', fire.names('old', old))
        self.assertEqual(len(fire.entity_defs(fire.names('old', old), old, [0, 0, 0])), 1)
        for bad in ({'recoil': {'kick': True}}, {'recoil': {'return': 0}}, {'recoil': {'side': 100}},
                    {'recoil': {'typo': 1}}, {'shake': True}, {'shake': 0}, {'flash': 0}, {'shake': {'frequency': 100}},
                    {'flash': {'effect': '../foo'}}, {'flash': {'light': 'yes'}},
                    {'empty_sound': [None]}, {'reload_sound': 7}, {'sound': ['x'] * 5},
                    {'kickback': float('inf')}, {'impact': 'true'}, {'charge_effect': 'particles/../a.vpcf'}):
            with self.subTest(bad=bad), self.assertRaises(OpError):
                fire.validate(bad)

    def test_spring_overshoot_returns_preserving_mouse_and_flash_cleanup(self):
        code = weapons.render(weapons.validate({'origin': [0, 0, 0], 'name': 'spring', 'base': 'awp',
            'fire': {'magazine': 2, 'cooldown': 2, 'damage': 10, 'recoil': {'kick': 6, 'side': 0, 'return': .6},
                     'flash': {'life': .1}, 'shake': {'amplitude': .2, 'duration': .15}, 'kickback': 80}}))
        result = mechanics.run_harness(code, r'''
const gun = new D.Entity("gun"); gun.clip = 0; gun.reserve = 0;
gun.GetClipAmmo = () => gun.clip; gun.SetClipAmmo = n => gun.clip = n;
gun.GetReserveAmmo = () => gun.reserve; gun.SetReserveAmmo = n => gun.reserve = n;
gun.GetData = () => ({GetName: () => "awp", GetDamage: () => 115});
D.entities.w9_spring_weapon = gun; D.entities[E.hud] = new D.Hud("hud");
for (const name of [E.flash, E.flash_anchor]) D.entities[name] = new D.Entity(name);
D.pawn.weapon = gun; D.H.OnActivate();
D.tick(CSInputs.ATTACK);
D.assert.equal(gun.clip, 0, "native shot stays disabled");
D.assert.equal(D.pawn.vel.x, -80, "shooter kickback");
D.assert.notEqual(D.entities[E.flash_anchor].pos.z, -8192, "light parent at muzzle");
let low = 0, high = 0;
for (let i = 0; i < 44; i++) {
    if (i === 8) { D.pawn.ang.yaw += 12; D.pawn.ang.pitch += 4; }
    D.tick();
    const offset = D.pawn.ang.pitch - (i >= 8 ? 4 : 0);
    low = Math.min(low, offset); high = Math.max(high, offset);
}
D.assert.ok(low < -3, "initial kick"); D.assert.ok(high > .1, "spring crosses rest");
D.assert.ok(Math.abs(D.pawn.ang.pitch - 4) < .000001, "returns to mouse-modified aim");
D.assert.ok(Math.abs(D.pawn.ang.yaw - 12) < .000001);
D.assert.equal(D.entities[E.flash_anchor].pos.z, -8192, "light parked after life");
D.assert.ok(D.fired.includes(E.flash + ":Start")); D.assert.ok(D.fired.includes(E.flash + ":Stop"));
console.log("W_SPRING_OK");
''')
        self.assertIn('W_SPRING_OK', result)

    def test_sound_generator_has_headroom_and_six_distinct_resources(self):
        module = importlib.util.spec_from_file_location('w9_audio_test', weapons.DATA / 'rail_audio.py')
        audio = importlib.util.module_from_spec(module); module.loader.exec_module(audio)
        sounds, metrics = audio.generate()
        self.assertEqual(set(sounds), {'crack', 'body', 'tail', 'charge', 'empty', 'reload'})
        self.assertLess(metrics['shot_peak'], .65)
        for name, wave in sounds.items():
            self.assertLess(float(abs(wave).max()), .36)
            self.assertEqual(wave.shape[1], 1)

    def test_rapid_shots_and_holster_remove_shake_offset(self):
        code = weapons.render(weapons.validate({'origin': [0, 0, 0], 'name': 'shake', 'base': 'awp',
            'fire': {'magazine': 30, 'cooldown': .05, 'recoil': 0,
                     'shake': {'amplitude': .5, 'frequency': 13, 'duration': .3}}}))
        result = mechanics.run_harness(code, r'''
const gun = new D.Entity("gun"); gun.GetClipAmmo = () => 0; gun.SetClipAmmo = () => {};
gun.GetReserveAmmo = () => 0; gun.SetReserveAmmo = () => {};
gun.GetData = () => ({GetName: () => "awp", GetDamage: () => 115});
D.entities.w9_shake_weapon = gun; D.entities[E.hud] = new D.Hud("hud");
D.pawn.weapon = gun; D.H.OnActivate();
for (let i = 0; i < 22; i++) D.tick(CSInputs.ATTACK);
for (let i = 0; i < 24; i++) D.tick();
D.assert.ok(D.events.filter(m => typeof m === "string" && m.includes("shot ")).length > 3, "overlapping shake envelopes");
D.assert.ok(Math.abs(D.pawn.ang.pitch) < 1e-9, "rapid shots settle without drift");
D.assert.ok(Math.abs(D.pawn.ang.yaw) < 1e-9);
for (let i = 0; i < 5; i++) D.tick(CSInputs.ATTACK);
D.pawn.ang.pitch += 4; D.pawn.ang.yaw += 12;
D.pawn.weapon = undefined; D.tick();
D.assert.ok(Math.abs(D.pawn.ang.pitch - 4) < 1e-9, "holster removes only shake");
D.assert.ok(Math.abs(D.pawn.ang.yaw - 12) < 1e-9);
console.log("W_SHAKE_OK");
''')
        self.assertIn('W_SHAKE_OK', result)

    def test_back_face_without_hit_is_not_counted_as_a_second_wall(self):
        code = weapons.render(weapons.validate({'origin': [0, 0, 0], 'name': 'boundary', 'base': 'awp',
            'fire': {'damage': 20, 'penetration': 3, 'thickness': 16, 'wall_loss': .1, 'recoil': 0}}))
        result = mechanics.run_harness(code, r'''
const gun = new D.Entity("gun"); gun.GetClipAmmo = () => 0; gun.SetClipAmmo = () => {};
gun.GetReserveAmmo = () => 0; gun.SetReserveAmmo = () => {};
gun.GetData = () => ({GetName: () => "awp", GetDamage: () => 115});
D.entities.w9_boundary_weapon = gun; D.entities[E.hud] = new D.Hud("hud");
const world = new D.Entity("world"); world.IsWorld = () => true;
const victim = new D.Pawn("victim");
Instance.TraceBullet = ({start}) => start.x < 316 ?
    [{hitEntity: world, position: {x: start.x < 300 ? 300 : 316, y: 0, z: 64}}] :
    [{hitEntity: victim, position: {x: 500, y: 0, z: 64}}];
Instance.TraceLine = ({start}) => start.x === 316 ? {didHit:false, startedInSolid:false} :
    {didHit:true, startedInSolid:false, end:{x:316,y:0,z:64}};
D.pawn.weapon = gun; D.H.OnActivate(); D.tick(CSInputs.ATTACK);
D.assert.equal(victim.GetHealth(), 82, "one physical wall, one 10% loss");
D.assert.ok(D.events.some(m => typeof m === "string" && m.includes('"walls":1')));
console.log("W_BOUNDARY_OK");
''')
        self.assertIn('W_BOUNDARY_OK', result)

    def test_inflictor_in_front_of_muzzle_is_ignored(self):
        code = weapons.render(weapons.validate({'origin': [0, 0, 0], 'name': 'selfhit', 'base': 'awp',
            'fire': {'damage': 20, 'recoil': 0, 'penetration': 0}}))
        result = mechanics.run_harness(code, r'''
const gun = new D.Entity("gun"); gun.GetClipAmmo = () => 0; gun.SetClipAmmo = () => {};
gun.GetReserveAmmo = () => 0; gun.SetReserveAmmo = () => {};
gun.GetData = () => ({GetName: () => "awp", GetDamage: () => 115});
D.entities.w9_selfhit_weapon = gun; D.entities[E.hud] = new D.Hud("hud");
const victim = new D.Pawn("victim");
Instance.TraceBullet = () => [{hitEntity: gun, position: {x: 40, y: 0, z: 64}},
                              {hitEntity: victim, position: {x: 500, y: 0, z: 64}}];
D.pawn.weapon = gun; D.H.OnActivate(); D.tick(CSInputs.ATTACK);
D.assert.equal(victim.GetHealth(), 80, "inflictor is not damaged");
D.assert.ok(D.events.some(m => typeof m === "string" && m.includes('"hits":1')));
console.log("W_SELFHIT_OK");
''')
        self.assertIn('W_SELFHIT_OK', result)

    def test_projectile_burst_auto_and_muzzle_schema(self):
        f = fire.validate({'projectile': {'speed': 2000, 'gravity': 600, 'homing': {'target': 'enemy', 'turn': 200},
                                          'explode': {'radius': 300, 'damage': 150, 'self': .25}},
                           'burst': {'count': 3, 'interval': .1}, 'auto': False, 'muzzle': {'forward': 30, 'down': 8}})
        pr = f['projectile']
        self.assertEqual((pr['model'], pr['trail'], pr['explode']['effect'], pr['explode']['sound']),
                         (fire.ROCKET_MODEL, fire.TRAIL_EFFECT, fire.BOOM_EFFECT, fire.BOOM_SOUND))
        self.assertEqual((pr['homing']['target'], pr['homing']['turn'], pr['explode']['self']), ('enemy', 200, .25))
        self.assertEqual((f['burst']['count'], f['auto'], f['muzzle']['right']), (3, False, 5))
        slug = fire.validate({'projectile': {'explode': False, 'trail': False, 'hit': 80}})['projectile']
        self.assertIsNone(slug['explode'])
        e = fire.names('w9_rl', f)
        self.assertEqual((e['proj'], e['proj_count'], e['boom_count']), ('w9_rl_proj_', 6, fire.BOOM_SLOTS))
        defs = fire.entity_defs(e, f, [0, 0, 40])
        self.assertEqual(sum(1 for d in defs if d[1].startswith('proj')), 6)
        self.assertEqual(sum(1 for d in defs if d[1].startswith('boom')), 2 * fire.BOOM_SLOTS)
        no_boom = fire.names('w9_s', fire.validate({'projectile': {'explode': False, 'trail': False}}))
        self.assertIsNone(no_boom['boom'])
        self.assertIsNone(no_boom['trail'])
        for bad in ({'projectile': {'speed': 1}}, {'projectile': {'homing': {'target': 'nearest'}}},
                    {'projectile': {'model': '../x.vmdl'}}, {'projectile': {'explode': {'radius': -1}}},
                    {'projectile': {'trail': 'x.vpcf'}}, {'projectile': {}, 'beam': {'width': 4}}, {'burst': {'count': 1}},
                    {'auto': 'yes'}, {'muzzle': {'up': 3}}):
            with self.subTest(bad=bad), self.assertRaises(OpError):
                fire.validate(bad)
