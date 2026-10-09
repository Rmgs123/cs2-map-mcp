"""add_ability: validation, generated entities and the runtime rules on the Node engine double."""
import unittest

from cs2map import abilities, mechanics
from cs2map.project import OpError
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


def script(op):
    spec = abilities.validate({'op': 'add_ability', 'name': 'a', **op})
    code, xml, css, n = abilities.generate(spec)
    return code, n


SETUP = r'''
D.entities[E.hud] = new D.Hud(E.hud);
for (const m of E.markers) D.entities[m] = new Entity(m);
if (E.zone) D.entities[E.zone] = new Entity(E.zone);
if (E.item) { D.entities[E.item] = new Entity(E.item); D.entities[E.item].pos = {x: 300, y: 100, z: 0}; }
D.H.OnActivate();
const p = D.pawn;
'''


class Rules(unittest.TestCase):
    def run_js(self, op, test):
        code, n = script(op)
        return mechanics.run_harness(code, SETUP + test + '\nconsole.log("OK");')

    def test_double_jump_needs_a_release_and_counts(self):
        out = self.run_js({'abilities': {'double_jump': {'power': 320}}}, r'''
        D.tick(0); D.assert.ok(D.last("grant"));                         // source always: granted at once
        p.ground = false; D.tick(CSInputs.JUMP);                         // the take-off press is not a double jump
        D.assert.equal(D.count("double_jump"), 0);
        D.tick(0); D.tick(CSInputs.JUMP); D.assert.equal(p.vel.z, 320);  // released, pressed again in the air
        D.tick(0); D.tick(CSInputs.JUMP); D.assert.equal(D.count("double_jump"), 1);   // one extra jump only
        p.ground = true; D.tick(0); p.ground = false; D.tick(0); D.tick(CSInputs.JUMP); D.assert.equal(D.count("double_jump"), 2);''')
        self.assertIn('OK', out)

    def test_dash_cooldown_and_jetpack_fuel(self):
        out = self.run_js({'abilities': {'dash': {'speed': 900, 'cooldown': 1}, 'jetpack': {'fuel': 1, 'refill': 1}}}, r'''
        D.tick(0); p.ang = {pitch: 0, yaw: 90, roll: 0};
        D.tick(CSInputs.LOOK_AT_WEAPON); D.assert.ok(Math.abs(p.vel.y - 900) < 1e-6);
        D.tick(0); D.tick(CSInputs.LOOK_AT_WEAPON); D.assert.equal(D.count("dash"), 1);    // cooling down
        D.advance(1.1, 0); D.tick(CSInputs.LOOK_AT_WEAPON); D.assert.equal(D.count("dash"), 2);
        p.ground = false; p.vel = {x: 0, y: 0, z: 0}; D.tick(0);
        D.advance(0.5, CSInputs.JUMP); D.assert.ok(p.vel.z > 200);                         // thrust
        D.advance(1, CSInputs.JUMP); const s = D.state(config.state); D.assert.ok(s.holders[0].fuel < 0.01);''')
        self.assertIn('OK', out)

    def test_zone_grants_and_leaving_revokes(self):
        out = self.run_js({'abilities': {'gravity': {'scale': 0.3}},
                           'source': {'zone': {'mins': [0, 0, 0], 'maxs': [64, 64, 64]}}}, r'''
        D.tick(0); D.assert.equal(D.count("grant"), 0);
        D.output(E.zone, "OnStartTouch", p); D.assert.equal(D.count("grant"), 1);
        p.ground = false; p.vel = {x: 0, y: 0, z: 0}; D.tick(0, 0.1); D.assert.ok(Math.abs(p.vel.z - 56) < 1e-6);   // 800*0.7*0.1
        D.output(E.zone, "OnEndTouch", p); D.assert.equal(D.count("revoke"), 1);''')
        self.assertIn('OK', out)

    def test_command_with_duration_and_shield(self):
        out = self.run_js({'abilities': {'shield': {'reduce': 0.75}, 'invisible': {'alpha': 20}},
                           'source': {'command': '!ghost'}, 'duration': 2}, r'''
        D.tick(0); D.H.OnPlayerChat({player: D.controller, text: "!ghost", team: 2});
        D.assert.equal(p.color.a, 20); D.assert.equal(p.knife.color.a, 20);               // body and weapons
        D.assert.deepEqual(D.H.OnModifyPlayerDamage({player: p, damage: 40, damageTypes: CSDamageTypes.BULLET}), {damage: 10});
        D.advance(2.1, 0); D.assert.ok(D.last("revoke").includes("time")); D.assert.equal(p.color.a, 255);''')
        self.assertIn('OK', out)

    def test_wallhack_markers_follow_enemies_and_park(self):
        out = self.run_js({'abilities': {'wallhack': {'color': [0, 255, 0]}}}, r'''
        const bot = new D.Pawn(); bot.team = 3; bot.pos = {x: 900, y: 50, z: 0};
        const ctrl = {IsValid: () => true, IsBot: () => true, IsConnected: () => true, GetPlayerSlot: () => 1, GetPlayerPawn: () => bot};
        bot.GetPlayerController = () => ctrl;
        const all = Instance.GetAllPlayerControllers; Instance.GetAllPlayerControllers = () => [D.controller, ctrl];
        D.tick(0);
        const m = D.entities[E.markers[0]]; D.assert.equal(m.pos.x, 900); D.assert.deepEqual(m.glow, {r: 0, g: 255, b: 0});
        D.assert.equal(m.color.a, 0);
        bot.alive = false; D.tick(0); D.assert.equal(m.glow, null); D.assert.deepEqual(m.pos, core.vec(E.park));''')
        self.assertIn('OK', out)

    def test_launch_pad_reaches_its_target(self):
        out = self.run_js({'abilities': {'launch': {'to': [612, 100, 0], 'height': 128}},
                           'source': {'zone': {'mins': [0, 0, 0], 'maxs': [64, 64, 64]}}}, r'''
        D.tick(0); p.pos = {x: 100, y: 100, z: 0}; D.output(E.zone, "OnStartTouch", p);
        // integrate the jump: lands near x 612
        let pos = {...p.pos}, v = {...p.vel}; for (let t = 0; t < 5 && (t < 0.1 || pos.z > 0); t += 0.005) { pos.x += v.x*0.005; pos.z += v.z*0.005; v.z -= 800*0.005; }
        D.assert.ok(Math.abs(pos.x - 612) < 16, "landed at " + pos.x);''')
        self.assertIn('OK', out)


class Validation(unittest.TestCase):
    def test_refusals(self):
        for op, msg in (({'abilities': {'teleport': {}}}, 'unknown ability'),
                        ({'abilities': {'dash': {'key': 'mouse3'}}}, 'key'),
                        ({'abilities': {'dash': {}, 'blink': {'key': 'inspect'}}}, 'different keys'),
                        ({'abilities': {'fly': {}, 'jetpack': {}}}, 'fly already'),
                        ({'abilities': {'speed': {'scale': 9}}}, 'scale'),
                        ({'abilities': {'launch': {'velocity': [0, 0, 600]}}}, 'jump pad'),
                        ({'abilities': {'speed': {}}, 'duration': 5}, 'duration'),
                        ({'abilities': {'speed': {}}, 'source': {'portal': 1}}, 'unknown source')):
            with self.subTest(msg=msg), self.assertRaisesRegex(OpError, msg):
                abilities.validate({'op': 'add_ability', 'name': 'a', **op})

    @unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
    def test_dry_run_entities(self):
        from cs2map.project import DRY_RUN, Project
        m = VMap.from_template(SETTINGS.cs2_path, 'basic')
        token = DRY_RUN.set(True)
        try:
            r = abilities.add_ability(Project(), m, {'op': 'add_ability', 'name': 'pad', 'abilities': {
                'launch': {'velocity': [0, 0, 700]}, 'wallhack': {}}, 'source': {'zone': {
                    'mins': [0, 0, 0], 'maxs': [64, 64, 64]}}}, {})
        finally:
            DRY_RUN.reset(token)
        self.assertEqual(r['entities'], 1 + 1 + abilities.MARKERS + 1)   # hud, zone, markers, script
        self.assertEqual(len(m.entities(classname='trigger_multiple')), 1)


if __name__ == '__main__':
    unittest.main()
