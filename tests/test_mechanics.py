"""Shared mechanics: the fragment builder, the schemas, the kind registry and every fragment against the engine double."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from cs2map import control, fire, mechanics
from cs2map.config import PROJECT_ROOT

NODE = shutil.which('node') or mechanics.node_path()


class Builder(unittest.TestCase):
    def test_order_dependencies_imports_and_config_literal(self):
        code = mechanics.build_script('t', ['seat', 'camera'], {'name': 'x"; throw 1; //'})
        names = [f.name for f in mechanics.resolve(['seat', 'camera'])]
        self.assertEqual(names, ['core', 'seat', 'camera'])               # dependency first, then the given order
        self.assertLess(code.index('function Core('), code.index('function Seat('))
        self.assertLess(code.index('function Seat('), code.index('function Camera('))
        lines = code.splitlines()
        self.assertTrue(lines[0].startswith(mechanics.MARKER))
        self.assertEqual(mechanics.read_marker(code), {'name': 'x"; throw 1; //'})
        imports = [x for x in lines if x.startswith('import ')]
        self.assertEqual(len(imports), 1)                                 # all imports merged into one line
        self.assertEqual(len(set(imports[0].split('{')[1].split('}')[0].replace(' ', '').split(','))), imports[0].count(',') + 1)
        self.assertIn('"x\\"; throw 1; //"', code)                        # user text stays inside a JSON string

    def test_unknown_cycle_and_bad_headers(self):
        with self.assertRaisesRegex(ValueError, 'unknown fragment'):
            mechanics.resolve(['nope'])
        with tempfile.TemporaryDirectory(prefix='frag-', dir=PROJECT_ROOT / '.local' / 'tmp') as tmp:
            a, b, c = (Path(tmp) / f'{n}.js' for n in ('cyc_a', 'cyc_b', 'bad'))
            a.write_text('// @fragment cyc_a\n// @requires cyc_b\n', encoding='utf-8')
            b.write_text('// @fragment cyc_b\n// @requires cyc_a\n', encoding='utf-8')
            c.write_text('// @fragment bad\n// @imports NotAnApi\n', encoding='utf-8')
            try:
                mechanics.register_fragment('cyc_a', a); mechanics.register_fragment('cyc_b', b)
                with self.assertRaisesRegex(ValueError, 'cycle'):
                    mechanics.resolve(['cyc_a'])
                with self.assertRaisesRegex(ValueError, 'unknown cs_script imports'):
                    mechanics.register_fragment('bad', c)
                with self.assertRaisesRegex(ValueError, 'header says'):
                    mechanics.register_fragment('other', a)
            finally:
                mechanics.FRAGMENTS.pop('cyc_a', None); mechanics.FRAGMENTS.pop('cyc_b', None)

    def test_shared_schemas(self):
        self.assertEqual(mechanics.validate_camera(None), {'forward': 22.0, 'up': 10.0, 'chase': {'forward': -120.0, 'up': 36.0}})
        self.assertFalse(mechanics.validate_camera({'chase': False})['chase'])
        self.assertEqual(mechanics.validate_camera({'start': 'chase'})['start'], 'chase')
        for bad in ({'forward': 500}, {'chase': {'up': -1}}, {'x': 1}, {'start': 'chase', 'chase': False}, {'start': 'top'}):
            with self.assertRaises(ValueError):
                mechanics.validate_camera(bad)
        spec = mechanics.validate_health({'health': 80}, native=True)
        self.assertEqual((spec['health'], spec['respawn'], spec['effects']), (80.0, 10.0, True))
        self.assertEqual(mechanics.validate_health({}, native=False)['health'], 0)
        with self.assertRaises(ValueError):
            mechanics.validate_health({'effects': 'yes'})
        self.assertEqual(mechanics.validate_seat({}), {})                  # absent stays absent
        with self.assertRaises(ValueError):
            mechanics.validate_seat({'reach': 5000})
        self.assertEqual(mechanics.validate_hud({'title': 'CAR'}), {'title': 'CAR'})
        with self.assertRaises(ValueError):
            mechanics.validate_hud({'title': '<b>'})

    def test_fire_schema_lives_in_its_own_module(self):
        f = fire.validate({'charge': .8, 'beam': {'width': 4.8}, 'penetration': 3})
        self.assertEqual((f['beam']['scale'], f['beam']['segment']), (2.0, 256.0))
        self.assertEqual(f['charge_effect'], fire.CHARGE_GLOW)
        e = fire.names('t9_gun', f)
        self.assertEqual((e['hud'], e['beam'], e['beam_count']), ('t9_gun_hud', 't9_gun_beam_', 32))
        defs = fire.entity_defs(e, f, [0, 0, 10])
        self.assertEqual(defs[0][0], 'custom_hud_layout')
        self.assertEqual(sum(d[0] == 'prop_dynamic' for d in defs), 32)
        self.assertTrue(all(d[3][2] < -4000 for d in defs[1:]))            # glow and rods are parked
        with self.assertRaises(Exception):
            fire.validate({'charge': 99})


class Registry(unittest.TestCase):
    def test_kind_registered_without_touching_control(self):
        with tempfile.TemporaryDirectory(prefix='kind-', dir=PROJECT_ROOT / '.local' / 'tmp') as tmp:
            motion = Path(tmp) / 'toy.js'
            motion.write_text('// @fragment toy_motion\n// @requires core, seat, camera, hud, health\n// @imports Instance\n'
                              'const core = Core({tag: "TOY", name: config.name, state: "toy_state"});\n', encoding='utf-8')

            def validate(spec, m=None):
                spec['origin'] = mechanics.vector(spec.get('origin', [0, 0, 0]), 'origin')
                return mechanics.validate_health(spec, 100, native=True)

            def entities(spec, n):
                return [mechanics.entity('prop_dynamic', n['toy'], spec['origin'], {'model': 'models/x.vmdl'})]

            try:
                control.register_kind('toy', validate, ['core', 'hud', 'camera', 'seat', 'health'], entities, motion,
                                      fields={'wheels'}, prefix='z9', names=lambda s: {'toy': f'z9_{s["name"]}_body'}, title='TOY')
                spec = control.validate({'op': 'add_controllable', 'name': 'a', 'kind': 'toy', 'wheels': 4})
                self.assertEqual((spec['kind'], spec['health'], spec['camera']['up']), ('toy', 100.0, 10.0))
                code, xml, css, ops = control.generate(spec)
                self.assertTrue(code.startswith(control.MARKER))
                self.assertIn('toy_motion', code.splitlines()[2])
                self.assertIn('TOY', xml); self.assertNotIn('HUDNAME', xml)
                self.assertEqual(ops[0]['keyvalues']['targetname'], 'z9_a_body')
                self.assertEqual(control.names(spec)['script'], 'z9_control_a')
                with self.assertRaises(ValueError):                         # a drone field is not a toy field
                    control.validate({'op': 'add_controllable', 'name': 'a', 'kind': 'toy', 'speed': 5})
            finally:
                control.KINDS.pop('toy', None); mechanics.FRAGMENTS.pop('toy_motion', None)
        with self.assertRaisesRegex(ValueError, 'unknown kind'):
            control.validate({'op': 'add_controllable', 'name': 'a', 'kind': 'toy'})


TOY = r'''
const E = {};
const core = Core({tag: "TOY", name: "t", state: "toy_state"});
const hud = Hud({core, name: "toy_hud", panel: "control", variable: "telemetry"});
const body = new Entity("toy_body"); body.pos = {x: 0, y: 0, z: 0}; body.health = 100000;
const remote = new Entity("toy_remote"); const decoy = new Entity("toy_pilot"); decoy.enabled = false;
const camera = Camera({core, follow: () => body, views: {forward: 22, up: 10, chase: {forward: -120, up: 36}}});
let refused = null;
const health = Health({core, body: () => body, max: 100, native: true, effects: {smoke: "sm", fire: "fi", boom: "bo", boom_sound: "bs"},
    respawn: 5, spawn: {origin: [0, 0, 0], yaw: 0}, guard: w => !!seat.player && w.GetOwner() === seat.player});
const seat = Seat({core, camera, hud, target: () => remote, pawn: "toy_pawn", decoy: () => decoy,
    allow: p => health.dead ? {reason: "destroyed"} : true, onEnter: p => { camera.start(p); hud.show(p); }, onExit: () => {}});
D.entities.toy_hud = D.hudEntity = new CustomHudLayout("toy_hud");
Instance.SetThink(() => { const now = Instance.GetGameTime(); health.update(now); seat.update(core.pawns()); if (seat.player) camera.update(seat.player); });
hud.find(); health.restore();
core.state(() => ({hp: health.hp, dead: health.dead, active: !!seat.player}));
'''


@unittest.skipUnless(NODE, 'Node unavailable')
class Fragments(unittest.TestCase):
    def run_js(self, test, fragments=('core', 'hud', 'camera', 'seat', 'health'), code=None):
        code = code or mechanics.build_script('toy', fragments, {'name': 't'}) + TOY
        return mechanics.run_harness(code, test)

    def test_core_dispatch_modify_damage_and_state(self):
        out = self.run_js(r'''
        const seen = [];
        core.on("OnPlayerKill", e => seen.push("a")); core.on("OnPlayerKill", e => seen.push("b"));
        D.H.OnPlayerKill({player: null}); D.assert.deepEqual(seen.slice(-2), ["a", "b"]);   // one callback, many listeners
        core.on("OnModifyPlayerDamage", () => ({damage: 5}));
        D.assert.deepEqual(D.H.OnModifyPlayerDamage({attacker: null}), {damage: 5});
        D.assert.equal(typeof core.basis({pitch: 0, yaw: 90, roll: 0}).f.y, "number");
        D.assert.ok(Math.abs(core.basis({pitch: 0, yaw: 90, roll: 0}).f.y - 1) < 1e-9);
        core.log("ev", {a: 1}); for (let i = 0; i < 12; i++) core.log("n" + i);
        const s = D.state("toy_state", "tok"); D.assert.equal(s.token, "tok"); D.assert.equal(s.recent.length, 8);
        D.assert.ok(Array.isArray(s.humans) && s.humans[0].slot === 0);
        core.fx(null, "Start"); D.assert.ok(!D.fired.some(x => x.startsWith("null")));
        console.log("OK");''')
        self.assertIn('OK', out)

    def test_seat_enter_exit_and_hud_camera(self):
        out = self.run_js(r'''
        const p = D.pawn; p.keys = 0;
        D.T.traceLine = c => ({hitEntity: remote, didHit: true});          // the eye ray hits the seat target
        D.tick(CSInputs.USE);                                               // E rising edge: enter
        D.assert.equal(seat.player, p); D.assert.equal(p.mode, 0); D.assert.equal(p.cam.GetMode(), 3);
        D.assert.equal(p.name, "toy_pawn"); D.assert.equal(p.GetColor().a, 0); D.assert.equal(decoy.enabled, true);
        D.assert.equal(D.hudEntity.classes["0controlHidden"], false);       // HUD shown for this player
        D.tick(0); p.weapon = p.pistol; D.tick(0); D.assert.equal(p.weapon, p.knife);   // only the knife
        D.tick(CSInputs.RELOAD); D.assert.equal(camera.chase, true); D.assert.equal(p.cam.follow.cameraOffset.x, -120);
        D.assert.deepEqual(D.H.OnModifyPlayerDamage({attacker: p}), {abort: true});
        p.ang = {pitch: 20, yaw: 40, roll: 0};
        D.tick(0); D.tick(CSInputs.USE);                                     // E again: exit
        D.assert.equal(seat.player, undefined); D.assert.equal(p.mode, 1); D.assert.equal(p.cam.GetMode(), 0);
        D.assert.equal(p.name, "human"); D.assert.equal(p.GetColor().a, 255); D.assert.equal(decoy.enabled, false);
        D.assert.equal(p.weapon, p.pistol, "the weapon comes back"); D.assert.equal(D.hudEntity.classes["0controlHidden"], true);
        D.assert.equal(D.H.OnModifyPlayerDamage({attacker: p}), undefined);
        // exit by death, round restart, input and reset: all release the player
        for (const how of ["kill", "round", "input", "reset"]) {
            D.tick(0); D.tick(CSInputs.USE); D.assert.equal(seat.player, p, how);
            if (how === "kill") D.H.OnPlayerKill({player: p});
            if (how === "round") D.H.OnBeginRoundRestart();
            if (how === "input") D.inputs.Exit({});
            if (how === "reset") D.H.OnPlayerReset({player: p});
            D.assert.equal(seat.player, undefined, how); D.assert.equal(p.cam.GetMode(), 0, how);
        }
        console.log("OK");''')
        self.assertIn('OK', out)

    def test_seat_refusal_and_camera_owned_elsewhere(self):
        out = self.run_js(r'''
        D.T.traceLine = c => ({hitEntity: remote});
        health.destroy(); D.tick(0); D.tick(CSInputs.USE);
        D.assert.equal(seat.player, undefined); D.assert.ok(D.last("refused").includes("destroyed"));
        health.restore(); D.pawn.cam.SetMode(1);                             // a story camera owns the player's camera
        D.tick(0); D.tick(CSInputs.USE); D.assert.equal(seat.player, undefined);
        D.pawn.cam.SetMode(0); D.T.traceLine = c => ({hitEntity: new Entity("wall")});
        D.tick(0); D.tick(CSInputs.USE); D.assert.equal(seat.player, undefined, "the eye ray must hit the target");
        console.log("OK");''')
        self.assertIn('OK', out)

    def test_health_native_damage_destroy_respawn_and_knife_refund(self):
        out = self.run_js(r'''
        D.tick(0); D.assert.equal(health.hp, 100);
        body.health -= 30; D.tick(0);
        D.assert.equal(health.hp, 70); D.assert.equal(body.health, 100000 - 30);
        body.health -= 30; D.tick(0); D.assert.equal(health.hp, 40); D.assert.ok(D.fired.includes("sm:Start"), "smoke at half");
        body.health -= 45; D.tick(0);
        D.assert.equal(health.dead, true); D.assert.ok(D.last("destroyed")); D.assert.ok(D.fired.includes("bs:StartSound"));
        D.assert.equal(body.color.r, 60); D.assert.ok(body.angVel, "the wreck tumbles");
        D.advance(5.2); D.assert.equal(health.dead, false); D.assert.equal(health.hp, 100);
        D.assert.equal(body.color.r, 255); D.assert.equal(body.health, 100000);
        // scripted damage
        health.hurt(40, "crash"); D.assert.equal(health.hp, 60); health.hurt(100); D.assert.equal(health.dead, true);
        console.log("OK");''')
        self.assertIn('OK', out)

    def test_pilot_knife_swing_is_refunded(self):
        out = self.run_js(r'''
        D.T.traceLine = c => ({hitEntity: remote});
        D.tick(0); D.tick(CSInputs.USE); D.assert.equal(seat.player, D.pawn);
        D.H.OnKnifeAttack({weapon: D.pawn.knife}); body.health -= 65; D.tick(0);
        D.assert.equal(body.health, 100000); D.assert.equal(health.hp, 100); D.assert.ok(D.last("refund"));
        console.log("OK");''')
        self.assertIn('OK', out)

    def test_camera_starts_in_the_chase_view(self):
        out = mechanics.run_harness(mechanics.build_script('toy', ['core', 'camera'], {}) + r'''
        const core = Core({tag: "T", name: "t", state: "s"});
        const cam = Camera({core, follow: () => body, views: {forward: 22, up: 10, chase: {forward: -200, up: 80}, start: "chase"}});
        const body = new Entity("b");
        D.pawn.keys = 0; cam.start(D.pawn);
        D.assert.equal(D.pawn.cam.follow.cameraOffset.x, -200); D.assert.equal(cam.chase, true);
        D.pawn.keys = CSInputs.RELOAD; cam.update(D.pawn); D.assert.equal(D.pawn.cam.follow.cameraOffset.x, 22);   // R: first person
        console.log("OK");''', '')
        self.assertIn('OK', out)

    def test_wreck_kept_for_good(self):
        out = mechanics.run_harness(mechanics.build_script('toy', ['core', 'health'], {}) + r'''
        const core = Core({tag: "T", name: "t", state: "s"});
        const body = new Entity("b"); body.pos = {x: 5, y: 6, z: 7};
        const health = Health({core, body: () => body, max: 10, native: false, respawn: -1, spawn: {origin: [0, 0, 0], yaw: 0}});
        Instance.SetThink(() => {}); health.restore(); health.hurt(10); D.assert.equal(health.dead, true);
        D.advance(1, 0, .1); health.update(D.now()); D.assert.equal(health.respawnIn(D.now()), null);
        body.pos = {x: 0, y: 0, z: 0}; body.color = {r: 255, g: 255, b: 255, a: 255};
        health.restore();                                   // a round restart: the wreck comes back, dark
        D.assert.equal(health.dead, true); D.assert.equal(body.pos.x, 5); D.assert.equal(body.color.r, 60);
        console.log("OK");''', '')
        self.assertIn('OK', out)

    def test_hud_throttle_and_put(self):
        out = self.run_js(r'''
        D.assert.equal(hud.due("k", 0, .1), true); D.assert.equal(hud.due("k", .05, .1), false); D.assert.equal(hud.due("k", .11, .1), true);
        hud.put(D.pawn, "hello"); D.assert.equal(D.hudEntity.text, "hello"); D.assert.equal(D.hudEntity.classes["0controlHidden"], false);
        console.log("OK");''')
        self.assertIn('OK', out)


FIRE_AT_TURRET = r'''
const core = Core({tag: "T", name: "gun", state: "gun_state"});
const F = {charge: 0, cooldown: .1, magazine: 5, reload: 1, damage: 30, penetration: 0, thickness: 32, wall_loss: .1, range: 2000,
           pellets: 1, spread: 0, recoil: 4, beam: null, charge_sound: null, charge_effect: null};
const fire = Fire({core, fire: F, entities: {fire: "t_fire"}, knockback: 0, title: "GUN"});
const target = new Entity("dummy"); target.health = 100;
D.T.traceBullet = c => [{hitEntity: target, position: {x: 500, y: 0, z: 40}}];
const st = fire.newState(), kicks = [];
const src = {shooter: D.pawn, origin: {x: 0, y: 0, z: 40}, angles: {pitch: 0, yaw: 0, roll: 0}, inflictor: new Entity("turret"),
             kick: (dp, dy) => kicks.push(dp)};
function step(trigger, dt = .016) { D.tick(0, dt); fire.step(st, {trigger, reload: false, src}, D.now(), dt); }
'''


@unittest.skipUnless(NODE, 'Node unavailable')
class FireEngineOutsideWeapons(unittest.TestCase):
    def test_a_turret_style_shooter_fires_without_a_weapon(self):
        code = mechanics.build_script('gun', fire.FRAGMENTS, {}) + FIRE_AT_TURRET
        out = mechanics.run_harness(code, r'''
        D.H.think = () => {};
        step(true); D.assert.equal(target.health, 70, "fires at once (no charge)");
        D.assert.equal(target.lastDamage.attacker, D.pawn); D.assert.equal(target.lastDamage.inflictor, src.inflictor);
        step(true); D.assert.equal(target.health, 70, "cooldown");
        for (let i = 0; i < 10; i++) step(true);
        D.assert.ok(target.health < 70, "fires again after the cooldown");
        D.assert.ok(kicks.length > 0 && kicks.some(k => k > 0), "recoil goes to src.kick, not to the player's view");
        D.assert.equal(D.pawn.ang.pitch, 0);
        const before = st.cells; D.assert.ok(before < 5);
        D.assert.ok(/GUN  \|+\.+/.test(fire.text(st, D.now())));
        console.log("OK");''')
        self.assertIn('OK', out)


if __name__ == '__main__':
    unittest.main()
