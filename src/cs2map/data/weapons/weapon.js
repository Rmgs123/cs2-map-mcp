// @fragment weapon
// @requires core, hud, fire
// @imports Instance, CSPlayerPawn, CSInputs
// add_weapon: a real CS2 weapon (hands, animations, slots, pickup and drop stay native) with an own model and colour,
// its own damage, magazine, extra shot sound, sparks and knockback; with C.fire the native shot is disabled (empty
// magazine) and the script fires through the shot engine (fragment fire): charge, beam, penetration, projectiles.
// A model on the base's skeleton (C.rigged, model_build weapon=...) is worn everywhere, in the hands too; any other
// model only in the world (table, floor, others' hands), the holder then sees the stock gun.
const C = config;
const F = C.fire;
const E = C.entities;
const core = Core({tag: "W_WEAPON", name: C.name});
const {valid} = core;
const hud = E.hud ? Hud({core, name: E.hud, panel: "weapon", variable: "status"}) : undefined;
const engine = F ? Fire({core, fire: F, entities: E, knockback: C.knockback, title: C.title}) : undefined;
let weapons = [], lastClip = new Map(), pilots = new Map(), stock;
const ours = w => valid(w) && weapons.includes(w);
function wanted(w) { return !C.model ? null : C.rigged || !valid(w.GetOwner()) ? C.model : stock; }
function dress(w) {
    const model = wanted(w);
    if (model && w.GetModelName() !== model) w.SetModel(model);
    if (C.color) w.SetColor({r: C.color[0], g: C.color[1], b: C.color[2], a: 255});
    if (F) { w.SetClipAmmo(0); w.SetReserveAmmo(0); }
}
function adopt(w) {
    if (!stock && w.GetModelName() !== C.model) stock = w.GetModelName();   // the base's model, read before dressing
    weapons.push(w);
    dress(w);
    if (!F && C.clip) w.SetClipAmmo(C.clip);
    if (!F && C.reserve !== null) w.SetReserveAmmo(C.reserve);
    lastClip.set(w, w.GetClipAmmo());
}
// give: players of the team get the weapon at spawn (once: not if they already carry one of ours). The engine hands
// out the default loadout after the spawn event, so the gift comes .3 s later and replaces the stock weapon of the
// same slot (C.slot: 0 primary, 1 pistol, 2 knife; -1 = no slot to free, e.g. the taser).
const pending = new Map();
function giveTo(p) {
    if (!C.give || !valid(p) || !p.IsAlive()) return;
    const team = p.GetTeamNumber();
    if ((C.give === "t" && team !== 2) || (C.give === "ct" && team !== 3) || team < 2) return;
    pending.set(p, {at: Instance.GetGameTime() + .3, tries: 0});
}
function givePending(now) {
    for (const [p, g] of pending) {
        if (now < g.at) continue;
        if (!valid(p) || !p.IsAlive() || weapons.some(w => valid(w) && w.GetOwner() === p)) { pending.delete(p); continue; }
        if (!g.given) {
            const old = C.slot >= 0 ? p.FindWeaponBySlot(C.slot) : undefined;
            if (valid(old) && !ours(old)) {
                if (old.GetData().GetName() === C.base) { g.adopt = old; }
                else p.DestroyWeapon(old);
            }
            if (!g.adopt) p.GiveNamedItem(C.base, true);
            g.given = true;
        }
        const w = g.adopt || Instance.FindEntitiesByClass(C.base).find(x => valid(x) && x.GetOwner() === p && !weapons.includes(x));
        if (w) {
            pending.delete(p);
            w.SetEntityName(C.targetname); adopt(w); p.SwitchToWeapon(w);
            core.log("give", {slot: core.slot(p)});
        } else if (++g.tries > 20) { pending.delete(p); core.log("give_failed", {slot: core.slot(p)}); }
    }
}
function setup() {
    weapons = []; lastClip = new Map(); pilots = new Map(); pending.clear();
    if (hud) { hud.find(); hud.reset(); }
    if (engine) engine.setup();
    for (const w of Instance.FindEntitiesByName(C.targetname).filter(valid)) adopt(w);
    if (C.give) for (const c of Instance.GetAllPlayerControllers()) giveTo(c.GetPlayerPawn());
    const d = weapons[0]?.GetData();
    core.log("ready", {count: weapons.length, base: d?.GetName() ?? C.base, stock_damage: d?.GetDamage(), scripted_fire: !!F,
                       rigged: !!C.rigged});
    Instance.SetNextThink(Instance.GetGameTime() + .02);
}
core.on("OnPlayerReset", ({player}) => giveTo(player));
// ---- native shots: scale the engine's damage (head/limb multipliers and falloff stay) ----
core.on("OnModifyPlayerDamage", e => {
    if (F || !C.damage || !ours(e.weapon)) return;
    return {damage: e.damage * C.damage / e.weapon.GetData().GetDamage()};
});
function push(player, from, force) {
    if (!force || !valid(player) || !player.IsAlive()) return;
    const p = player.GetAbsOrigin(), v = player.GetAbsVelocity(), dx = p.x - from.x, dy = p.y - from.y, l = Math.hypot(dx, dy) || 1;
    player.Teleport({velocity: {x: v.x + dx / l * force, y: v.y + dy / l * force, z: Math.max(v.z, 0) + force * .35}});
}
core.on("OnPlayerDamage", ({player, attacker, weapon, damage}) => {
    if (F || !ours(weapon)) return;
    core.log("hit", {damage, health: player.GetHealth()});
    if (valid(attacker)) push(player, attacker.GetEyePosition(), C.knockback);
});
function spark(position) {
    if (!E.impact) return;
    const fx = Instance.FindEntityByName(E.impact);
    if (!valid(fx)) return;
    fx.Teleport({position});
    Instance.EntFireAtName({name: E.impact, input: "Stop"});
    Instance.EntFireAtName({name: E.impact, input: "Start", delay: .04});
    Instance.EntFireAtName({name: E.impact, input: "Stop", delay: .3});
}
core.on("OnGunFire", ({weapon}) => {
    if (F || !ours(weapon)) return;
    const owner = weapon.GetOwner();
    if (valid(owner)) core.sound(E.fire, owner.GetEyePosition());
    core.log("fire", {clip: weapon.GetClipAmmo()});
});
core.on("OnBulletImpact", ({weapon, position}) => { if (!F && ours(weapon) && C.impact) spark(position); });
// ---- scripted fire: every player holding one of our weapons shoots through the engine ----
function scripted(now, dt) {
    for (const c of Instance.GetAllPlayerControllers()) {
        const p = c.GetPlayerPawn();
        if (!(p instanceof CSPlayerPawn) || !p.IsValid()) continue;
        const slot = c.GetPlayerSlot(), w = p.IsAlive() ? p.GetActiveWeapon() : undefined;
        let st = pilots.get(slot);
        if (!ours(w)) {
            if (st?.shown) { hud.hide(p); st.shown = false; }
            if (st) engine.cancel(st, {shooter: p}); continue;
        }
        if (!st) pilots.set(slot, st = Object.assign(engine.newState(), {held: false, shown: false}));
        w.SetClipAmmo(0); w.SetReserveAmmo(0);
        engine.step(st, {trigger: p.IsInputPressed(CSInputs.ATTACK), reload: p.IsInputPressed(CSInputs.RELOAD),
                         src: {shooter: p, origin: p.GetEyePosition(), angles: p.GetEyeAngles(), inflictor: w}}, now, dt);
        hud.put(p, engine.text(st, now));
        st.shown = true;
    }
}
let lastTime = 0;
Instance.SetThink(() => {
    const now = Instance.GetGameTime(), dt = Math.min(.05, Math.max(0, now - lastTime)); lastTime = now;
    try {
        if (pending.size) givePending(now);
        if (F) scripted(now, dt);
        else for (const w of weapons) {
            // The engine reloads to the stock magazine; our magazine size is restored from the reserve.
            if (!valid(w)) continue;
            const clip = w.GetClipAmmo(), before = lastClip.get(w) ?? clip;
            if (C.clip && clip > before && clip !== C.clip) {
                const reserve = w.GetReserveAmmo(), want = Math.min(C.clip, clip + reserve);
                w.SetClipAmmo(want); w.SetReserveAmmo(Math.max(0, reserve - (want - clip)));
                core.log("reloaded", {clip: want, reserve: w.GetReserveAmmo()});
            }
            lastClip.set(w, w.GetClipAmmo());
        }
        // Pickup, drop and native resets change the model: dress it again.
        if (C.model) for (const w of weapons) if (valid(w) && w.GetModelName() !== wanted(w)) dress(w);
        if (engine) engine.update(now);
    } catch (e) { throw Error("W_WEAPON_ERROR " + C.name + ": " + String(e)); }
    Instance.SetNextThink(now + (F ? .016 : .05));
});
core.on("OnWeaponPickup", ({weapon}) => {
    // shop: a base weapon bought in the buy menu (or picked up from anywhere) becomes this weapon
    if (C.shop && valid(weapon) && !ours(weapon) && weapon.GetData().GetName() === C.base) { adopt(weapon); core.log("bought", {}); }
    if (!ours(weapon)) return;
    dress(weapon);
    // A picked-up special weapon goes straight into the hands (CS2 keeps the old gun otherwise).
    const owner = weapon.GetOwner();
    if (valid(owner)) owner.SwitchToWeapon(weapon);
    core.log("pickup", {owner: owner?.GetPlayerController()?.GetPlayerSlot()});
});
core.on("OnActivate", setup);
core.on("OnRoundStart", setup);
core.command("w9_" + C.name + "_state", () => core.log("state", {weapons: weapons.filter(valid).map(w => ({
    owner: w.GetOwner()?.GetPlayerController()?.GetPlayerSlot() ?? null, clip: w.GetClipAmmo(), reserve: w.GetReserveAmmo(),
    model: w.GetModelName(), stock, color: w.GetColor(), pos: w.GetAbsOrigin()})), pilots: [...pilots.entries()],
    shots: engine ? engine.shots : [], flying: engine ? engine.projectiles.length : 0}));
