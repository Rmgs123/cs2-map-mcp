// @fragment ability_motion
// @requires core, hud
// @imports Instance, CSInputs, CSPlayerPawn, CSDamageTypes
// Player abilities (abilities.py, map_edit add_ability): one script per ability set and source.
// Every ability is a small rule run each tick for the players who hold the set (velocity impulses through
// Teleport, colour, glow, damage filters). Sources: always, zone (trigger), item (walk over it), chat command,
// script input (I/O). Measured 08.10 (b10_lab): jump 56 -> double jump 107, dash 900 u/s, jetpack 530 in 2 s,
// gravity 0.35 -> jump 155, speed x1.6 -> 400 u/s; glow shows through walls on props, not on players.
const A = config.abilities, SRC = config.source, E = config.entities;
const core = Core({tag: "A10_ABILITY", name: config.name, state: config.state});
const hud = Hud({core, name: E.hud, panel: "ability", variable: "status"});
const KEYS = {inspect: CSInputs.LOOK_AT_WEAPON, attack2: CSInputs.ATTACK2, reload: CSInputs.RELOAD, use: CSInputs.USE,
              walk: CSInputs.WALK, duck: CSInputs.DUCK};
const KEY_LABEL = {inspect: "F", attack2: "RMB", reload: "R", use: "E", walk: "SHIFT", duck: "CTRL"};
const MOVE = CSInputs.FORWARD | CSInputs.BACK | CSInputs.LEFT | CSInputs.RIGHT;
const players = new Map();
let last = 0, itemReadyAt = 0, itemOn = true;

function S(p) {
    const slot = core.slot(p) ?? -1;
    let x = players.get(slot);
    if (!x) {
        x = {granted: false, until: 0, jumps: 0, released: false, air: 0, dashAt: -99, blinkAt: -99, hurtAt: -99,
             fuel: A.jetpack ? A.jetpack.fuel : 0, heal: 0, alphaAt: 0};
        players.set(slot, x);
    }
    return x;
}
function eligible(p) {
    if (!core.valid(p) || !p.IsAlive()) return false;
    const c = p.GetPlayerController();
    if (!c || (c.IsBot() && !config.bots)) return false;
    return !config.team || p.GetTeamNumber() === config.team;
}
function alpha(p, a) {
    const c = p.GetColor();
    p.SetColor({r: c.r, g: c.g, b: c.b, a});
    for (const slot of [0, 1, 2, 3, 4]) {
        const w = p.FindWeaponBySlot(slot);
        if (core.valid(w)) { const k = w.GetColor(); w.SetColor({r: k.r, g: k.g, b: k.b, a}); }
    }
}
function launch(p) {
    const L = A.launch;
    let v = L.velocity ? core.vec(L.velocity) : null;
    if (!v) {                                   // ballistic: reach `to` with an apex `height` above the higher end
        const o = p.GetAbsOrigin(), t = core.vec(L.to), g = 800;
        const top = Math.max(o.z, t.z) + L.height, up = Math.sqrt(2 * g * (top - o.z));
        const time = up / g + Math.sqrt(2 * Math.max(1, top - t.z) / g);
        v = {x: (t.x - o.x) / time, y: (t.y - o.y) / time, z: up};
    }
    p.Teleport({velocity: v});
    core.log("launch", {v: {x: Math.round(v.x), y: Math.round(v.y), z: Math.round(v.z)}});
}
function grant(p, why) {
    const x = S(p), now = Instance.GetGameTime();
    if (A.launch) launch(p);
    if (!config.lasting) return;               // a launch pad has nothing that lasts
    const fresh = !x.granted;
    x.granted = true;
    x.until = config.duration ? now + config.duration : 0;
    if (fresh) {
        if (A.armor) { p.SetArmor(A.armor.value); if (A.armor.helmet) p.SetHasHelmet(true); }
        if (A.health) { p.SetMaxHealth(A.health.max); p.SetHealth(A.health.max); }
        if (A.invisible) alpha(p, A.invisible.alpha);
        if (A.jetpack) x.fuel = A.jetpack.fuel;
        hud.show(p);
    }
    core.log("grant", {slot: core.slot(p), why, until: x.until ? Math.round(x.until - now) : 0});
}
function revoke(p, why) {
    const x = S(p);
    if (!x.granted) return;
    x.granted = false;
    if (core.valid(p)) {
        if (A.invisible) alpha(p, 255);
        if (A.health && p.IsAlive()) { p.SetMaxHealth(100); p.SetHealth(Math.min(p.GetHealth(), 100)); }
        hud.hide(p);
    }
    core.log("revoke", {slot: core.slot(p), why});
}
function pressed(p, key) { return key && p.WasInputJustPressed(KEYS[key]); }

function run(p, x, now, dt) {
    const ground = p.GetGroundEntity() !== undefined, v = p.GetAbsVelocity(), jumpHeld = p.IsInputPressed(CSInputs.JUMP);
    let nv = null;
    if (ground) { x.jumps = 0; x.released = false; x.air = 0; } else { x.air += dt; if (!jumpHeld) x.released = true; }
    if (A.jetpack && ground) x.fuel = Math.min(A.jetpack.fuel, x.fuel + dt * A.jetpack.fuel / A.jetpack.refill);
    const flying = A.fly && (!ground || jumpHeld);
    if (flying) {
        const a = p.GetEyeAngles(), b = core.basis({pitch: a.pitch, yaw: a.yaw, roll: 0}), f = A.fly.speed;
        let d = {x: 0, y: 0, z: 0};
        if (p.IsInputPressed(CSInputs.FORWARD)) d = core.add(d, b.f);
        if (p.IsInputPressed(CSInputs.BACK)) d = core.add(d, b.f, -1);
        if (p.IsInputPressed(CSInputs.LEFT)) d = core.add(d, b.l);
        if (p.IsInputPressed(CSInputs.RIGHT)) d = core.add(d, b.l, -1);
        if (jumpHeld) d.z += 1;
        if (p.IsInputPressed(CSInputs.DUCK)) d.z -= 1;
        const n = Math.hypot(d.x, d.y, d.z) || 1;
        nv = {x: d.x / n * f, y: d.y / n * f, z: d.z / n * f};
    } else if (A.double_jump && !ground && x.released && p.WasInputJustPressed(CSInputs.JUMP) && x.jumps < A.double_jump.jumps) {
        x.jumps++;
        nv = {x: v.x, y: v.y, z: A.double_jump.power};
        core.log("double_jump", {n: x.jumps});
    } else if (A.jetpack && !ground && jumpHeld && x.fuel > 0 && (x.released || x.air > 0.3)) {
        x.fuel = Math.max(0, x.fuel - dt);
        nv = {x: v.x, y: v.y, z: Math.min(A.jetpack.max_up, v.z + A.jetpack.thrust * dt)};
    } else if (A.autobhop && ground && jumpHeld && !p.WasInputJustPressed(CSInputs.JUMP)) {
        nv = {x: v.x, y: v.y, z: 301};
    } else if (A.gravity && !ground) {
        nv = {x: v.x, y: v.y, z: v.z + 800 * (1 - A.gravity.scale) * dt};
    }
    if (A.dash && pressed(p, A.dash.key) && now - x.dashAt >= A.dash.cooldown) {
        x.dashAt = now;
        const yaw = p.GetEyeAngles().yaw * core.rad;
        nv = {x: Math.cos(yaw) * A.dash.speed, y: Math.sin(yaw) * A.dash.speed, z: Math.max((nv || v).z, A.dash.up)};
        core.log("dash", {});
    }
    if (A.blink && pressed(p, A.blink.key) && now - x.blinkAt >= A.blink.cooldown) blink(p, x, now);
    if (A.speed && ground && !nv && p.IsInputPressed(MOVE)) {
        const h = Math.hypot(v.x, v.y), want = 250 * A.speed.scale;
        if (h > 40 && h < want) { const k = Math.min(want / h, 1.15); nv = {x: v.x * k, y: v.y * k, z: v.z}; }
    }
    if (nv) p.Teleport({velocity: nv});
    if (A.regen && now - x.hurtAt >= A.regen.delay && p.GetHealth() < A.regen.max) {
        x.heal += A.regen.per_second * dt;
        if (x.heal >= 1) { const n = Math.floor(x.heal); x.heal -= n; p.SetHealth(Math.min(A.regen.max, p.GetHealth() + n)); }
    }
    if (A.invisible && now > x.alphaAt) { x.alphaAt = now + 0.5; alpha(p, A.invisible.alpha); }   // a new weapon in hand
}
function blink(p, x, now) {
    const ray = core.eyeRay(p, A.blink.distance), a = p.GetEyeAngles(), yaw = a.yaw * core.rad;
    const back = ray.didHit ? 32 : 0, eye = ray.end;
    let feet = {x: eye.x - Math.cos(yaw) * back, y: eye.y - Math.sin(yaw) * back, z: eye.z - 64};
    for (const lift of [0, 18, 36, 64]) {
        const at = {x: feet.x, y: feet.y, z: feet.z + lift};
        const t = Instance.TraceBox({mins: {x: -16, y: -16, z: 0}, maxs: {x: 16, y: 16, z: 72}, start: at, end: at, ignorePlayers: true});
        if (!t.startedInSolid) {
            x.blinkAt = now;
            p.Teleport({position: at, velocity: {x: 0, y: 0, z: 0}});
            core.log("blink", {to: {x: Math.round(at.x), y: Math.round(at.y), z: Math.round(at.z)}});
            return;
        }
    }
    core.log("blink_blocked", {});
}

// Wallhack: invisible glowing silhouettes follow every enemy of a holder (glow renders through walls on props).
function wallhack(holders) {
    const markers = E.markers.map(n => Instance.FindEntityByName(n)).filter(core.valid);
    let used = 0;
    if (holders.length) {
        const teams = new Set(holders.map(h => h.GetTeamNumber()));
        for (const e of core.pawns({bots: true})) {
            if (used >= markers.length) break;
            if (holders.includes(e) || (A.wallhack.enemies && teams.has(e.GetTeamNumber()) && teams.size === 1)) continue;
            const m = markers[used++];
            m.Teleport({position: e.GetAbsOrigin(), angles: {pitch: 0, yaw: e.GetAbsAngles().yaw, roll: 0}});
            if (!m.IsGlowing()) {
                const c = m.GetColor(), g = A.wallhack.color;
                m.SetColor({r: c.r, g: c.g, b: c.b, a: 0});
                m.Glow({r: g[0], g: g[1], b: g[2]});
            }
        }
    }
    for (let i = used; i < markers.length; i++) {
        if (markers[i].IsGlowing()) { markers[i].Unglow(); markers[i].Teleport({position: core.vec(E.park)}); }
    }
}

function hudLine(p, x, now) {
    if (!config.hud || !hud.due(core.slot(p), now, 0.2)) return;
    const out = [config.title];
    const cd = (at, c) => now - at >= c ? "ready" : (c - (now - at)).toFixed(1) + " s";
    if (A.dash) out.push(`DASH (${KEY_LABEL[A.dash.key]})  ${cd(x.dashAt, A.dash.cooldown)}`);
    if (A.blink) out.push(`BLINK (${KEY_LABEL[A.blink.key]})  ${cd(x.blinkAt, A.blink.cooldown)}`);
    if (A.jetpack) out.push(`JETPACK  ${Math.round(100 * x.fuel / A.jetpack.fuel)}%`);
    if (x.until) out.push(`TIME  ${Math.max(0, Math.ceil(x.until - now))} s`);
    hud.text(p, out.join("   "));
}

function item(now) {
    const it = Instance.FindEntityByName(E.item);
    if (!core.valid(it)) return;
    if (!itemOn && config.source.respawn > 0 && now >= itemReadyAt) {
        itemOn = true;
        const c = it.GetColor(); it.SetColor({r: c.r, g: c.g, b: c.b, a: 255});
        core.log("item_back", {});
    }
    if (!itemOn) return;
    const at = it.GetAbsOrigin();
    for (const p of core.pawns({bots: config.bots})) {
        if (!eligible(p) || core.dist(p.GetAbsOrigin(), at) > SRC.radius) continue;
        grant(p, "item");
        itemOn = false;
        itemReadyAt = now + SRC.respawn;
        const c = it.GetColor(); it.SetColor({r: c.r, g: c.g, b: c.b, a: 0});
        break;
    }
}

function setup() {
    for (const [slot] of players) if (slot !== undefined) hud.hideSlot(slot);
    players.clear();
    hud.find(); hud.hideAll();
    itemOn = true; itemReadyAt = 0;
    if (E.item) { const it = Instance.FindEntityByName(E.item); if (core.valid(it)) { const c = it.GetColor(); it.SetColor({r: c.r, g: c.g, b: c.b, a: 255}); } }
    if (E.zone) {
        const z = Instance.FindEntityByName(E.zone);
        if (core.valid(z)) {
            Instance.ConnectOutput(z, "OnStartTouch", d => { if (d.activator instanceof CSPlayerPawn && eligible(d.activator)) grant(d.activator, "zone"); });
            if (config.lasting && !config.duration)
                Instance.ConnectOutput(z, "OnEndTouch", d => { if (d.activator instanceof CSPlayerPawn) revoke(d.activator, "left zone"); });
        }
    }
    for (const n of E.markers) {                // parked inside the map, transparent until they follow someone
        const mk = Instance.FindEntityByName(n);
        if (core.valid(mk)) { const c = mk.GetColor(); mk.SetColor({r: c.r, g: c.g, b: c.b, a: 0}); }
    }
    wallhack([]);
    last = Instance.GetGameTime();
    Instance.SetNextThink(last);
    core.log("ready", {abilities: Object.keys(A), source: SRC.kind});
}

Instance.SetThink(() => {
    const now = Instance.GetGameTime(), dt = Math.min(0.1, Math.max(0, now - last));
    last = now;
    if (SRC.kind === "item") item(now);
    const holders = [];
    for (const p of core.pawns({bots: config.bots})) {
        if (!eligible(p)) continue;
        const x = S(p);
        if (SRC.kind === "always" && !x.granted) grant(p, "always");
        if (x.granted && x.until && now >= x.until) revoke(p, "time");
        if (!x.granted) continue;
        run(p, x, now, dt);
        hudLine(p, x, now);
        if (A.wallhack) holders.push(p);
    }
    if (A.wallhack) wallhack(holders);
    Instance.SetNextThink(now);
});

core.on("OnPlayerJump", e => {
    const p = e.player;
    if (A.super_jump && eligible(p) && S(p).granted) { const v = p.GetAbsVelocity(); p.Teleport({velocity: {x: v.x, y: v.y, z: A.super_jump.power}}); }
});
core.on("OnModifyPlayerDamage", e => {
    const p = e.player;
    if (!core.valid(p) || !S(p).granted) return;
    if (A.no_fall_damage && (e.damageTypes & CSDamageTypes.FALL)) return {abort: true};
    if (A.shield) return {damage: e.damage * (1 - A.shield.reduce)};
});
core.on("OnPlayerDamage", e => { if (core.valid(e.player)) S(e.player).hurtAt = Instance.GetGameTime(); });
core.on("OnPlayerKill", e => { if (core.valid(e.player)) revoke(e.player, "death"); });
core.on("OnPlayerReset", e => { if (core.valid(e.player) && SRC.kind !== "always") revoke(e.player, "respawn"); });
core.on("OnPlayerChat", e => {
    if (SRC.kind !== "command" || !e.player || e.text.trim().toLowerCase() !== SRC.text) return;
    const p = e.player.GetPlayerPawn();
    if (!eligible(p)) return;
    if (S(p).granted && !config.duration) revoke(p, "command"); else grant(p, "command");
});
core.input(config.name + "_grant", d => { if (d.activator instanceof CSPlayerPawn && eligible(d.activator)) grant(d.activator, "input"); });
core.input(config.name + "_revoke", d => { if (d.activator instanceof CSPlayerPawn) revoke(d.activator, "input"); });
core.command(config.name + "_grant_all", () => { for (const p of core.pawns({bots: config.bots})) if (eligible(p)) grant(p, "command"); });
core.on("OnActivate", setup);
core.on("OnRoundStart", setup);
core.state(() => ({abilities: Object.keys(A), source: SRC.kind,
                   holders: [...players].filter(([, x]) => x.granted).map(([slot, x]) => ({slot, fuel: x.fuel, until: x.until}))}));
