"""add_weapon runtime against the engine double: give at spawn, an own model held in the hands, projectiles with
explosions (falloff, cover, knockback, self damage, homing, life, pool) and the fire modes (single, burst)."""
import unittest

from cs2map import mechanics, weapons

SETUP = r'''
for (const [key, name] of Object.entries(E)) {
    if (typeof name === "string" && !key.match(/^(proj|trail|boom|boom_sound|beam)$/)) D.entities[name] = new D.Entity(name);
    if (Array.isArray(name)) for (const n of name) D.entities[n] = new D.Entity(n);
}
if (E.hud) D.entities[E.hud] = new D.Hud("hud");
for (const [prefix, count] of [[E.proj, E.proj_count], [E.trail, E.proj_count], [E.boom, E.boom_count], [E.boom_sound, E.boom_count]])
    if (prefix) for (let i = 0; i < count; i++) D.entities[prefix + i] = new D.Entity(prefix + i);
const gun = new D.Weapon("gun", C.base); gun.owner = D.pawn;
if (!C.give) { D.entities[C.targetname] = gun; D.pawn.weapon = gun; }
D.pawn.pos = {x: 100, y: 100, z: 0};
const victim = new D.Pawn(); victim.name = "victim"; victim.pos = {x: 560, y: 100, z: 0}; victim.team = 3;
const victimCtl = {IsValid: () => true, IsBot: () => true, GetPlayerSlot: () => 1, GetPlayerPawn: () => victim};
Instance.GetAllPlayerControllers = () => [D.controller, victimCtl];
const world = new D.Entity("world"); world.world = true;
const state = () => D.state("w9_" + C.name + "_state");
'''


class WeaponRuntime(unittest.TestCase):
    def run_weapon(self, body, rigged=False, **fields):
        spec = weapons.validate({'name': 'gun', 'base': 'awp', 'origin': [0, 0, 40], **fields})
        spec['rigged'] = rigged
        result = mechanics.run_harness(weapons.render(spec), SETUP + '{\n' + body + '\n}\nconsole.log("W_RUNTIME_OK");')
        self.assertIn('W_RUNTIME_OK', result)

    def test_give_at_spawn_once_and_only_to_the_team(self):
        self.run_weapon(r'''
D.pawn.team = 3; D.H.OnActivate();
D.assert.equal((D.byClass.weapon_awp || []).length, 0, "not yet: the engine hands out the default loadout first");
D.advance(.4);
const given = D.byClass.weapon_awp.filter(w => w.owner === D.pawn);
D.assert.equal(given.length, 1, "given .3 s after the spawn");
D.assert.equal(given[0].name, C.targetname, "named as ours");
D.assert.equal(D.pawn.weapon, given[0], "deployed");
D.assert.equal(given[0].model, C.model, "own model at once");
const count = () => D.byClass.weapon_awp.length;
const before = count(); D.H.OnPlayerReset({player: D.pawn}); D.advance(.4); D.assert.equal(count(), before, "not twice");
const t = new D.Pawn(); t.team = 2; D.H.OnPlayerReset({player: t}); D.advance(.4); D.assert.equal(count(), before, "CT only");
D.assert.equal(state().weapons[0].owner, 0);
''', rigged=True, origin=None, give='ct', model='models/mcp/rail/rail.vmdl')

    def test_a_given_pistol_replaces_the_default_one(self):
        spec = weapons.validate({'name': 'gun', 'base': 'deagle', 'give': 'all'})
        self.assertEqual(spec['slot'], 1)
        self.assertEqual(weapons.validate({'name': 'z', 'base': 'taser', 'give': 'all'})['slot'], -1)
        result = mechanics.run_harness(weapons.render(spec), r'''
const glock = D.pawn.pistol; glock.cls = "weapon_glock"; glock.GetData = () => ({GetName: () => "weapon_glock", GetDamage: () => 30});
D.H.OnActivate(); D.advance(.4);
D.assert.equal(glock.valid, false, "the stock pistol made room");
D.assert.equal(D.pawn.weapon.GetClassName(), "weapon_deagle");
D.assert.equal(D.pawn.weapon.name, C.targetname);
console.log("W_GIVE_OK");''')
        self.assertIn('W_GIVE_OK', result)

    def test_a_model_on_the_skeleton_stays_in_the_hands(self):
        for rigged, held in ((True, 'models/mcp/rail/rail.vmdl'), (False, 'stock.vmdl')):
            with self.subTest(rigged=rigged):
                self.run_weapon(r'''
gun.model = "stock.vmdl"; gun.owner = undefined; D.H.OnActivate();
D.assert.equal(gun.model, C.model, "lying: own model");
gun.owner = D.pawn; D.H.OnWeaponPickup({weapon: gun}); D.tick();
D.assert.equal(gun.model, "''' + held + r'''");
''', rigged=rigged, model='models/mcp/rail/rail.vmdl')

    def rocket(self, **extra):
        return {'magazine': 4, 'cooldown': .5, 'recoil': 0,
                'projectile': {'speed': 1000, **extra, 'explode': {'radius': 200, 'damage': 100, 'knockback': 300, 'self': .5}}}

    def test_rocket_flies_explodes_with_falloff_knockback_and_cover(self):
        self.run_weapon(r'''
// A wall at x=600: the sphere sweep stops 4 units before it.
D.T.traceSphere = c => c.end.x >= 596 ? {didHit: true, startedInSolid: false, end: {x: 596, y: c.end.y, z: c.end.z}, hitEntity: world}
                                      : {didHit: false, startedInSolid: false, end: c.end};
D.H.OnActivate(); D.tick(CSInputs.ATTACK);
D.assert.equal(engine.projectiles.length, 1, "launched");
D.assert.equal(D.entities[E.proj + 0].enabled, true, "rocket shown");
D.assert.ok(D.fired.includes(E.trail + "0:Start"), "trail lit");
D.tick(); const x0 = engine.projectiles[0].pos.x; D.tick();
D.assert.ok(Math.abs(engine.projectiles[0].pos.x - x0 - 16) < .01, "1000 u/s, 16 units a tick");
D.advance(.6);
D.assert.equal(engine.projectiles.length, 0, "exploded at the wall");
D.assert.equal(D.entities[E.proj + 0].enabled, false, "rocket hidden");
const at = state().shots.at(-1).end, d = Math.hypot(at.x - 560, at.y - 100, at.z - 36), want = 100 * (1 - d / 200);
D.assert.ok(Math.abs((100 - victim.GetHealth()) - want) < .6, "falloff by distance: " + victim.GetHealth());
D.assert.equal(victim.lastDamage.damageTypes, CSDamageTypes.BLAST);
D.assert.ok(victim.vel.x < -100, "pushed away from the blast");
D.assert.equal(D.pawn.GetHealth(), 100, "the shooter is far away");
D.assert.ok(D.fired.includes(E.boom + "0:Start") && D.fired.includes(E.boom_sound + "0:StartSound"), "effect and sound");
// Cover: the line from the blast to the victim stops at a wall.
victim.health = 100; victim.vel = {x: 0, y: 0, z: 0};
D.T.traceLine = c => c.ignorePlayers ? {didHit: true, hitEntity: world, end: {x: 580, y: 100, z: 50}} : {didHit: false, end: c.end};
D.advance(.6); D.tick(CSInputs.ATTACK); D.advance(.7);
D.assert.equal(victim.GetHealth(), 100, "a wall shields");
D.assert.equal(state().shots.at(-1).explode, true);
''', fire=self.rocket())

    def test_self_damage_life_pool_and_homing(self):
        self.run_weapon(r'''
// Point-blank: the blast is at the muzzle, the shooter takes half.
D.T.traceSphere = c => ({didHit: true, startedInSolid: false, end: c.start, hitEntity: world});
D.H.OnActivate(); D.tick(CSInputs.ATTACK); D.tick();
D.assert.ok(D.pawn.GetHealth() < 100 && D.pawn.GetHealth() > 50, "self damage scaled by .5: " + D.pawn.GetHealth());
// Open sky: rockets die of old age (life 1 s), and the pool recycles the oldest.
D.T.traceSphere = null; D.pawn.health = 100;
D.advance(.6); D.tick(CSInputs.ATTACK); D.tick();
D.assert.equal(engine.projectiles.length, 1);
D.advance(1.1); D.assert.equal(engine.projectiles.length, 0, "exploded after its life");
''', fire={**self.rocket(life=1, pool=2), 'magazine': 9})
        self.run_weapon(r'''
victim.pos = {x: 900, y: 420, z: 0};       // off the line of fire, inside the cone
D.H.OnActivate(); D.tick(CSInputs.ATTACK); D.advance(.4);
const r = engine.projectiles[0];
D.assert.ok(r.pos.y > 140, "turned toward the enemy: y=" + r.pos.y);
D.assert.ok(r.vel.y > 0);
D.assert.ok(Math.abs(Math.hypot(r.vel.x, r.vel.y, r.vel.z) - 1000) < .5, "speed kept while turning");
''', fire=self.rocket(homing={'target': 'enemy', 'turn': 180, 'cone': 60, 'delay': 0}))

    def test_single_shot_and_burst(self):
        self.run_weapon(r'''
D.T.traceBullet = () => [{hitEntity: victim, position: {x: 560, y: 100, z: 64}}];
D.H.OnActivate();
D.advance(1, CSInputs.ATTACK);
D.assert.equal(victim.GetHealth(), 90, "auto:false holds fire until the trigger is released");
D.tick(); D.tick(CSInputs.ATTACK); D.assert.equal(victim.GetHealth(), 80);
''', fire={'damage': 10, 'recoil': 0, 'cooldown': .1, 'auto': False})
        self.run_weapon(r'''
D.T.traceBullet = () => [{hitEntity: victim, position: {x: 560, y: 100, z: 64}}];
D.H.OnActivate();
D.tick(CSInputs.ATTACK); D.assert.equal(victim.GetHealth(), 90);
D.advance(.35);
D.assert.equal(victim.GetHealth(), 70, "the burst finishes by itself");
D.assert.equal(state().shots.length, 3);
D.advance(.2, CSInputs.ATTACK); D.assert.equal(victim.GetHealth(), 70, "cooldown after the burst");
''', fire={'damage': 10, 'recoil': 0, 'cooldown': .8, 'auto': False, 'burst': {'count': 3, 'interval': .1}})


if __name__ == '__main__':
    unittest.main()
