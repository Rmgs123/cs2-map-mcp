// @fragment drone_motion
// @requires core, seat, camera, hud, health
// @imports Instance, CSInputs
// Motion adapter of kind "drone" on the shared mechanics fragments: WASD/SPACE/CTRL flight of a physics body
// (inertia, collisions, hover/land after the pilot leaves, battery) or a kinematic swept-box mover.
const E = config.entities;
const core = Core({tag: "D_CONTROL", name: config.name, state: "d8_control_" + config.name + "_state"});
const {valid, vec, zero, rad, clamp, basis, anglesOf} = core;
const GRAVITY = 800;
let body, remote, rotors = [], decoy, home, lastTime = 0, entered = 0;
let speed = 0, blocked = false, rotorAngle = 0;
// idle: undefined (parked, pure physics) | 'hover' (holds `hold`) | 'land' (descends until grounded)
let idle, idleSince = 0, hold, charge = config.battery, tilt = {pitch: 0, roll: 0}, seated = null;

const hud = Hud({core, name: E.hud, panel: "control", variable: "telemetry"});
const camera = Camera({core, follow: () => body, views: config.camera});
// Native prop health is kept far from zero so the engine never breaks/removes the body;
// the script counts real damage (bullets, grenades, knife) against config.health.
const health = Health({core, body: () => body, max: config.health, native: config.physics, parts: () => rotors,
    effects: {smoke: E.smoke, fire: E.fire, boom: E.boom, boom_sound: E.boom_sound},
    respawn: config.respawn, spawn: {origin: config.origin, yaw: config.yaw},
    guard: w => !!seat.player && w?.GetOwner() === seat.player,
    onDestroy: () => { idle = undefined; if (seat.player) seat.exit("destroyed"); motor(false); },
    onRestore: () => {
        idle = undefined; charge = config.battery; tilt = {pitch: 0, roll: 0};
        if (config.physics && config.mass) Instance.EntFireAtTarget({target: body, input: "SetMass", value: config.mass});
        seated = null; placeRotors(false);
    }});
const seat = Seat({core, camera, hud, target: () => remote, pawn: E.pawn, decoy: () => decoy, reach: config.reach,
    allow(p) {
        if (!valid(body)) return false;
        if (health.dead) return {reason: "destroyed", respawn: health.respawnIn(Instance.GetGameTime())};
        if (config.battery && charge < 1) return {reason: "battery", charge};
        if (!config.physics) {
            const t = Instance.TraceBox({start: body.GetAbsOrigin(), end: body.GetAbsOrigin(), mins: vec(config.hull.mins),
                      maxs: vec(config.hull.maxs), ignoreEntity: [body, ...rotors]});
            if (t.startedInSolid) return {reason: "drone_in_solid"};
        }
    },
    onEnter() {
        home = body.GetAbsOrigin(); entered = Instance.GetGameTime(); idle = undefined;
        camera.start(seat.player);
        if (valid(remote)) Instance.EntFireAtTarget({target: remote, input: "Disable"});
        hud.show(seat.player); motor(true);
    },
    enterData: () => ({mode: camera.mode(), position: body.GetAbsOrigin(), hp: health.hp}),
    onExit() {
        if (valid(remote)) Instance.EntFireAtTarget({target: remote, input: "Enable"});
        if (config.physics) {
            // Without a pilot the drone hovers in place (GPS hold) or lands; a destroyed one just falls.
            idle = health.dead || !valid(body) ? undefined : grounded() ? undefined : config.on_exit;
            idleSince = Instance.GetGameTime(); hold = valid(body) ? body.GetAbsOrigin() : undefined;
            if (!idle) motor(false);
        } else {
            motor(false);
            if (valid(body) && home) body.Teleport({position: home, angles: {pitch: 0, yaw: 0, roll: 0}, velocity: zero});
        }
        speed = 0;
    },
    exitData: () => ({hp: health.hp})});

function motor(on) { if (config.sound) core.fx(E.sound, on ? "StartSound" : "StopSound"); }
// Rotors are parented to the body, so the engine carries them; the script only sets their spin
// and re-seats them after the body moved. Move keeps the client's interpolation (a Teleport every
// tick reset it and left the rotors invisible after a map load).
function placeRotors(spin) {
    if (!valid(body)) return;
    const o = body.GetAbsOrigin(), a = body.GetAbsAngles(), b = basis(a);
    const pose = [o.x, o.y, o.z, a.pitch, a.yaw, a.roll];
    if (!spin && seated && pose.every((v, i) => Math.abs(v - seated[i]) < .05)) return;
    seated = pose;
    for (let i = 0; i < rotors.length; i++) if (valid(rotors[i])) {
        const off = config.rotors[i].offset, s = (spin ? rotorAngle * config.rotors[i].speed : 0) * rad;
        const c = Math.cos(s), n = Math.sin(s);
        const f = {x: b.f.x * c + b.l.x * n, y: b.f.y * c + b.l.y * n, z: b.f.z * c + b.l.z * n};
        const l = {x: b.l.x * c - b.f.x * n, y: b.l.y * c - b.f.y * n, z: b.l.z * c - b.f.z * n};
        rotors[i].Move({position: {x: o.x + b.f.x * off[0] + b.l.x * off[1] + b.u.x * off[2],
            y: o.y + b.f.y * off[0] + b.l.y * off[1] + b.u.y * off[2], z: o.z + b.f.z * off[0] + b.l.z * off[1] + b.u.z * off[2]},
            angles: anglesOf(f, l, b.u)});
    }
}
function setup() {
    if (seat.player) seat.exit("round");
    body = Instance.FindEntityByName(E.drone); remote = Instance.FindEntityByName(E.remote);
    decoy = E.pilot ? Instance.FindEntityByName(E.pilot) : undefined;
    rotors = E.rotors.map(n => Instance.FindEntityByName(n)).filter(valid);
    if (!valid(body) || !valid(remote) || !hud.find() || rotors.length !== E.rotors.length)
        throw Error("missing controllable entities");
    for (const r of rotors) r.SetParent(body);
    health.restore();
    hud.hideAll();
    seat.clearEdges(); lastTime = Instance.GetGameTime(); home = body.GetAbsOrigin();
    core.log("ready", {position: home, hp: health.hp, physics: config.physics});
    Instance.SetNextThink(lastTime + .016);
}
function grounded() {
    const o = body.GetAbsOrigin();
    const t = Instance.TraceLine({start: {x: o.x, y: o.y, z: o.z + 4}, end: {x: o.x, y: o.y, z: o.z - 6}, ignoreEntity: [body, ...rotors]});
    return t.didHit && !t.startedInSolid;
}
function steer(desired, dt, yaw) {
    const v = body.GetAbsVelocity();
    let dv = {x: desired.x - v.x, y: desired.y - v.y, z: desired.z - v.z};
    const len = Math.hypot(dv.x, dv.y, dv.z), max = config.accel * dt;
    if (len > max) for (const k of ["x", "y", "z"]) dv[k] *= max / len;
    const nv = {x: v.x + dv.x, y: v.y + dv.y, z: v.z + dv.z};
    // Visual attitude follows the requested horizontal acceleration: nose down to speed up.
    const ax = dt ? dv.x / dt : 0, ay = dt ? dv.y / dt : 0, cy = Math.cos(yaw * rad), sy = Math.sin(yaw * rad);
    const fwd = clamp((ax * cy + ay * sy) / config.accel, -1, 1), left = clamp((-ax * sy + ay * cy) / config.accel, -1, 1);
    const k = Math.min(1, dt * 8);
    tilt.pitch += (fwd * config.tilt - tilt.pitch) * k; tilt.roll += (-left * config.tilt - tilt.roll) * k;
    // Rotor thrust: pre-compensate the gravity the engine adds during the next step, so the
    // effective velocity is nv; the acceleration limit (inertia) applies to commands only.
    body.Teleport({angles: {pitch: tilt.pitch, yaw, roll: tilt.roll}, velocity: {x: nv.x, y: nv.y, z: nv.z + GRAVITY * dt}, angularVelocity: zero});
    speed = Math.hypot(nv.x, nv.y, nv.z);
}
function inputs(p) {
    const a = p.GetEyeAngles(), r = a.yaw * rad;
    const f = Number(p.IsInputPressed(CSInputs.FORWARD)) - Number(p.IsInputPressed(CSInputs.BACK));
    const right = Number(p.IsInputPressed(CSInputs.RIGHT)) - Number(p.IsInputPressed(CSInputs.LEFT));
    const up = Number(p.IsInputPressed(CSInputs.JUMP)) - Number(p.IsInputPressed(CSInputs.DUCK));
    return {yaw: a.yaw, slow: p.IsInputPressed(CSInputs.WALK), v: {x: f * Math.cos(r) + right * Math.sin(r), y: f * Math.sin(r) - right * Math.cos(r), z: up}};
}
function flyPhysics(p, dt) {
    const i = inputs(p), o = body.GetAbsOrigin(), scale = i.slow ? .4 : 1;
    const h = Math.hypot(i.v.x, i.v.y);
    const desired = {x: h ? i.v.x / h * config.speed * scale : 0, y: h ? i.v.y / h * config.speed * scale : 0, z: i.v.z * config.climb * scale};
    blocked = false;
    for (let n = 0; n < 3; n++) {
        const k = ["x", "y", "z"][n];
        const lo = config.bounds.mins[n] - config.hull.mins[n] + 1, hi = config.bounds.maxs[n] - config.hull.maxs[n] - 1;
        // Soft walls of the flight area: brake before the edge and push back from outside.
        if (o[k] < lo + 24 && desired[k] < 0) { desired[k] = Math.max(desired[k], (lo - o[k]) * 6); blocked = true; }
        if (o[k] > hi - 24 && desired[k] > 0) { desired[k] = Math.min(desired[k], (hi - o[k]) * 6); blocked = true; }
        if (o[k] < lo) desired[k] = Math.max(desired[k], (lo - o[k]) * 6);
        if (o[k] > hi) desired[k] = Math.min(desired[k], (hi - o[k]) * 6);
    }
    steer(desired, dt, i.yaw);
    p.Teleport({position: seat.saved.position, velocity: zero});
}
function flyKinematic(p, dt) {
    const origin = body.GetAbsOrigin(), i = inputs(p);
    let v = i.v, length = Math.hypot(v.x, v.y, v.z);
    if (length) for (const k of ["x", "y", "z"]) v[k] *= config.speed * dt / length;
    let end = {x: origin.x + v.x, y: origin.y + v.y, z: origin.z + v.z};
    blocked = false;
    for (let n = 0; n < 3; n++) {
        const k = ["x", "y", "z"][n];
        const bounded = Math.max(config.bounds.mins[n] - config.hull.mins[n] + 1,
                       Math.min(config.bounds.maxs[n] - config.hull.maxs[n] - 1, end[k]));
        if (bounded !== end[k]) blocked = true;
        end[k] = bounded;
    }
    const t = Instance.TraceBox({start: origin, end, mins: vec(config.hull.mins), maxs: vec(config.hull.maxs),
        ignoreEntity: [body, ...rotors]});
    if (t.startedInSolid) { end = origin; blocked = true; }
    else if (t.didHit) {
        const distance = Math.hypot(end.x - origin.x, end.y - origin.y, end.z - origin.z);
        const fraction = Math.max(0, t.fraction - (distance ? 1 / distance : 0));
        end = {x: origin.x + (end.x - origin.x) * fraction, y: origin.y + (end.y - origin.y) * fraction, z: origin.z + (end.z - origin.z) * fraction};
        blocked = true;
    }
    speed = dt ? Math.hypot(end.x - origin.x, end.y - origin.y, end.z - origin.z) / dt : 0;
    // Small game-time steps; Teleport with zero velocity cannot tunnel through swept TraceBox.
    body.Teleport({position: end, angles: {pitch: 0, yaw: i.yaw, roll: 0}, velocity: zero});
    p.Teleport({position: seat.saved.position, velocity: zero});
}
Instance.SetThink(() => {
    const now = Instance.GetGameTime(), dt = Math.min(.05, Math.max(0, now - lastTime)); lastTime = now;
    try {
        if (!valid(body)) { if (seat.player) seat.exit("death"); Instance.SetNextThink(now + .1); return; }
        health.update(now);
        if (config.battery) {
            charge = seat.player || idle === "hover" ? charge - dt : Math.min(config.battery, charge + dt * config.battery / 30);
            if (seat.player && charge <= 0) { charge = 0; seat.exit("battery"); }
        }
        seat.update(core.pawns());
        const player = seat.player;
        if (player) {
            camera.update(player);
            if (config.physics) flyPhysics(player, dt); else flyKinematic(player, dt);
            if (hud.due("telemetry", now, .1)) {
                hud.text(player, (config.health ? "HP " + Math.ceil(health.hp) + "/" + config.health + "   " : "") +
                    "ALT " + Math.round(body.GetAbsOrigin().z - config.bounds.mins[2]) + " u   SPEED " + Math.round(speed) + " u/s" +
                    (config.battery ? "   BAT " + Math.ceil(charge) + " s" : "") + (blocked ? "   EDGE" : ""));
                hud.flag(player, "Low", !!config.health && health.hp <= config.health / 3);
            }
        } else if (idle && !health.dead) {
            const yaw = body.GetAbsAngles().yaw, o = body.GetAbsOrigin();
            if (idle === "hover" && config.battery && charge <= 1) { idle = "land"; idleSince = now; }
            if (idle === "hover") {
                // Position hold: knocked by a shot or a push, it flies back to the hold point.
                const d = {x: (hold.x - o.x) * 2.5, y: (hold.y - o.y) * 2.5, z: (hold.z - o.z) * 2.5}, l = Math.hypot(d.x, d.y, d.z), max = config.speed * .5;
                if (l > max) for (const k of ["x", "y", "z"]) d[k] *= max / l;
                steer(d, dt, yaw);
            } else {
                steer({x: 0, y: 0, z: -config.land}, dt, yaw);
                if ((now - idleSince > .3 && grounded()) || now - idleSince > 20) {
                    idle = undefined; motor(false); core.log("landed", {position: body.GetAbsOrigin()});
                }
            }
        }
        const powered = !!player || !!idle;
        if (powered) rotorAngle += dt;
        if (rotors.length && (powered || config.physics)) placeRotors(powered);
    } catch (e) { if (seat.player) seat.exit("error"); throw Error("D_CONTROL_ERROR " + config.name + ": " + String(e)); }
    Instance.SetNextThink(now + .016);
});
core.on("OnActivate", setup);
core.on("OnRoundStart", setup);
core.state(() => ({active: !!seat.player, position: valid(body) ? body.GetAbsOrigin() : null,
    velocity: valid(body) ? body.GetAbsVelocity() : null, bodyAngles: valid(body) ? body.GetAbsAngles() : null,
    player: valid(seat.player) ? seat.player.GetAbsOrigin() : null, anchor: seat.saved?.position, speed, blocked,
    hp: health.hp, dead: health.dead, idle: idle || null, chase: camera.chase, stats: health.stats,
    camera: camera.mode(), playerMode: valid(seat.player) ? seat.player.GetMoveType() : null,
    angles: valid(seat.player) ? seat.player.GetEyeAngles() : null, rotors: rotors.filter(valid).map(r => r.GetAbsOrigin()),
    rotorAngles: rotors.filter(valid).map(r => r.GetAbsAngles().yaw), battery: config.battery ? charge : null,
    forward: valid(seat.player) ? seat.player.IsInputPressed(CSInputs.FORWARD) : false}));
