"""add_hideseek: phases, HUD, reveal pulses and prop disguises on the Node engine double; validation."""
import unittest

from cs2map import hideseek, mechanics


def code(op):
    spec = hideseek.validate({'op': 'add_hideseek', **op})
    c, xml, css, n = hideseek.generate(spec)
    return c


SETUP = r'''
D.entities[E.hud] = new D.Hud(E.hud);
for (const n of [...E.props, ...E.markers]) D.entities[n] = new Entity(n);
const hider = D.pawn; hider.team = 3;
const seeker = new D.Pawn(); seeker.team = 2; seeker.pos = {x: 500, y: 0, z: 0};
const sc = {IsValid: () => true, IsBot: () => true, IsConnected: () => true, GetPlayerSlot: () => 1, GetPlayerPawn: () => seeker};
seeker.GetPlayerController = () => sc;
Instance.GetAllPlayerControllers = () => [D.controller, sc];
D.H.OnRoundStart();
'''


class Flow(unittest.TestCase):
    def run_js(self, op, test):
        return mechanics.run_harness(code(op), SETUP + test + '\nconsole.log("OK");')

    def test_hide_phase_freezes_seekers_then_the_hunt(self):
        out = self.run_js({'hide': 10, 'seek': 60}, r'''
        D.tick(0);
        D.assert.equal(seeker.mode, CSMoveType.NONE); D.assert.ok(D.fired.includes(E.fade + ":Fade"));
        D.assert.equal(hider.slots[CSGearSlot.PISTOL], undefined);          // hiders keep only the knife
        D.assert.equal(D.roundTime, 70); D.assert.ok(D.server.some(c => c.includes("winner_no_objective 3")));
        D.assert.ok(D.entities[E.hud].text.includes("HIDE!"));
        const blinds = () => D.fired.filter(x => x === E.fade + ":Fade").length;
        D.advance(2, 0);
        D.assert.ok(blinds() >= 4);                        // renewed every 0.5 s while the seekers wait
        D.advance(8.1, 0);
        D.assert.equal(seeker.mode, CSMoveType.WALK);
        const after = blinds(); D.advance(3, 0); D.assert.equal(blinds(), after);   // the hunt: no more blinding
        D.assert.ok(D.entities[E.hud].text.includes("hiders left 1"));
        const s = D.state(config.state); D.assert.equal(s.phase, "seek");''')
        self.assertIn('OK', out)

    def test_reveal_pulse_and_disguise(self):
        out = self.run_js({'hide': 5, 'seek': 60, 'reveal': 10,
                           'disguise': {'models': ['models/a.vmdl', 'models/b.vmdl']}}, r'''
        D.tick(0);
        const prop = D.entities[E.props[0]]; D.assert.equal(prop.model, "models/a.vmdl"); D.assert.equal(hider.color.a, 0);
        hider.pos = {x: 40, y: 50, z: 0}; D.tick(0); D.assert.equal(prop.pos.x, 40);       // the prop follows
        D.tick(CSInputs.LOOK_AT_WEAPON); D.assert.equal(prop.model, "models/b.vmdl");      // F: next prop
        D.advance(15.1, 0); const m = D.entities[E.markers[0]];
        D.assert.ok(m.glow); D.assert.equal(m.pos.x, 40);                                   // reveal pulse
        D.advance(1.6, 0); D.assert.equal(m.glow, null); D.assert.equal(m.color.a, 0);''')
        self.assertIn('OK', out)


class Validation(unittest.TestCase):
    def test_refusals(self):
        for op, msg in (({'hide': 1}, 'hide'), ({'hider_weapons': 'rifle'}, 'hider_weapons'),
                        ({'disguise': {'models': []}}, 'disguise'), ({'disguise': {'models': ['x.vmdl']}}, 'models/'),
                        ({'teleports': [{'from': {'mins': [0, 0, 0], 'maxs': [1, 1, 1]}}]}, 'teleports'),
                        ({'colour': 1}, 'unknown')):
            with self.subTest(msg=msg), self.assertRaisesRegex(ValueError, msg):     # OpError is a ValueError
                hideseek.validate({'op': 'add_hideseek', **op})


if __name__ == '__main__':
    unittest.main()
