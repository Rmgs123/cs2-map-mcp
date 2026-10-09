// @fragment car_motion
// @requires core, seat, camera, hud, health
// @imports Instance, CSInputs, CSDamageTypes
// A rigid body on four ray-cast wheels with suspension. The body collision has no tyres: every tick each wheel
// casts three rays (a round tyre: centre, ahead, behind) along the body's down axis, the wheel sits where the
// tyre touches the ground (travel UP/DOWN), and the body is steered toward the plane of its wheels with a
// spring-damper (height, pitch, roll). Traction exists only where tyres touch; friction is limited (MU), so the
// car slides on steep slopes and climbs what real tyres climb. In the air the engine's physics is untouched.
const E = config.entities;
const core = Core({tag: 'V_CAR', name: config.name, state: 'v9_control_' + config.name + '_state'});
const {valid, zero, rad, clamp, basis, anglesOf} = core;
// Geometry (vehicles.py `wheels`, `suspension`): axle centres in the body frame, the body origin is the ground under
// the car at rest; travel UP/DOWN of each wheel; spring (bump), rebound and damping of the body; tyre grip (MU).
const W = config.wheels, S = config.suspension, ST = config.steer;
const WHEELS = W.positions, R = W.radius, WZ = W.z, UP = S.up, DOWN = S.down;
// An unsupported corner falls (the body's weight on that wheel): its target lift in the plane fit, and its weight.
const FALL = -(DOWN + 6), FALL_WEIGHT = .7;
const SAMPLES = [-.55, 0, .55], TOP = WZ + UP + R + 2, REACH = 2 * R + UP + DOWN + 6;
const G = 800, MU = S.grip, ROLLING = 70;
let body, wheels = [], decoy, lastTime = 0, steerAngle = 0, spin = 0, lastPose;
let contacts = 0, grounded = false, speed = 0, longitudinal = 0, lateral = 0, motorMode = null, crawl = false;
let lastVelocity = zero, lastCrash = -10, lastExit = null, lastSafe = null, entrySafe = null, seatPose, pilotParent;
let controls = '', upsideSince = 0, impactWindow = [];
let travel = [0, 0, 0, 0], touch = [false, false, false, false];
// Peaks since the last state report: how hard the suspension worked (tests read them instead of guessing from frames).
let peak = {bump: 0, droop: 0, pitch: 0, roll: 0, air: 0, speed: 0};
const hits = new Map();
const hud = Hud({core, name: E.hud, panel: 'control', variable: 'telemetry'});
const camera = Camera({core, follow: () => body, views: config.camera});

const dot = (a, b) => a.x * b.x + a.y * b.y + a.z * b.z;
function local(o, b, x, y, z) {
    return {x: o.x + b.f.x * x + b.l.x * y + b.u.x * z, y: o.y + b.f.y * x + b.l.y * y + b.u.y * z,
            z: o.z + b.f.z * x + b.l.z * y + b.u.z * z};
}
function transform(off) { return local(body.GetAbsOrigin(), basis(body.GetAbsAngles()), off[0], off[1], off[2]); }
function ignored(p) { return [body, ...wheels, decoy, p].filter(valid); }
function clearance(point, p) {
    const t = Instance.TraceBox({start: point, end: point, mins: {x: -16, y: -16, z: 0}, maxs: {x: 16, y: 16, z: 72},
        ignoreEntity: [p, decoy].filter(valid)});
    return !t.startedInSolid && !t.didHit;
}
function safeExit(p) {
    // Keep the car in the trace: a point inside its body is never considered free.
    for (const off of [[-8, 80, 90], [-8, -80, 90], [-20, 0, 170], [-135, 0, 90], [135, 0, 90]]) {
        const start = transform(off), t = Instance.TraceBox({start, end: {...start, z: start.z - 190},
            mins: {x: -16, y: -16, z: 0}, maxs: {x: 16, y: 16, z: 72}, ignoreEntity: [p, decoy].filter(valid)});
        if (!t.startedInSolid && t.didHit && t.normal?.z >= .65) {
            const at = {...t.end, z: t.end.z + 2};
            if (clearance(at, p)) return at;
        }
    }
    // The validated entry/last exit remains a fallback if both doors and roof are enclosed.
    if (lastSafe && clearance(lastSafe, p)) return {...lastSafe};
    if (entrySafe && clearance(entrySafe, p)) return {...entrySafe};
    return null;
}
function refreshExit() {
    if (!seat.player) return true;
    const at = safeExit(seat.player);
    if (at) { seat.saved.position = at; lastSafe = {...at}; }
    const a = seat.player.GetEyeAngles();
    seat.saved.angles = {pitch: a.pitch, yaw: a.yaw, roll: 0};
    return !!at;
}
function motor(mode) {
    if (mode === motorMode) return;
    for (const key of ['idle', 'drive']) core.fx(E[key + '_sound'], 'StopSound');
    motorMode = mode;
    if (mode && config.engine) core.fx(E[mode + '_sound'], 'StartSound', undefined, .05);
}
const health = Health({core, body: () => body, max: config.health, native: true, parts: () => wheels,
    effects: {smoke: E.smoke, fire: E.fire, boom: E.boom, boom_sound: E.boom_sound}, respawn: config.respawn,
    spawn: {origin: config.origin, yaw: config.yaw}, guard: w => !!seat.player && w?.GetOwner() === seat.player,
    onDestroy() { if (seat.player) seat.exit('destroyed'); motor(null); },
    onRestore() {
        Instance.EntFireAtTarget({target: body, input: 'SetMass', value: config.mass});
        steerAngle = spin = speed = longitudinal = lateral = 0; lastPose = null; lastVelocity = zero;
        travel = [0, 0, 0, 0]; hits.clear(); placeWheels(); impactWindow = []; upsideSince = 0;
        if (seat.player) seat.exit('respawn');
    }});
const seatCore = {...core, input: (name, fn) => core.input(name, e => {
    if (name === 'Exit' && seat.player && !refreshExit()) { core.log('exit_blocked'); return; }
    fn(e);
})};
const seat = Seat({core: seatCore, camera, hud, target: () => body, pawn: E.pawn, decoy: () => decoy, reach: config.reach,
    allow(p) {
        if (!valid(body)) return false;
        if (health.dead) return {reason: 'destroyed', respawn: health.respawnIn(Instance.GetGameTime())};
        // The eye trace must hit the car, AND feet must be beside the driver's door (+local Y).
        const at = p.GetAbsOrigin(), door = transform([-8, 66, 0]);
        if (Math.hypot(at.x - door.x, at.y - door.y) > 62 || Math.abs(at.z - door.z) > 40) return {reason: 'driver_door'};
        if (!clearance(at, p)) return {reason: 'entry_in_solid'};
    },
    onEnter(p, saved) {
        entrySafe = lastSafe = {...saved.position};
        pilotParent = p.GetParent(); p.SetParent(body);
        p.SetEyeAngles({pitch: 12, yaw: body.GetAbsAngles().yaw, roll: 0});
        Instance.EntFireAtTarget({target: body, input: 'DisableAutoSleeping'});
        camera.start(p); hud.show(p); seatPose = null; placeDriver(); motor('idle'); refreshExit();
    },
    onExit(p, reason) {
        motor(null); steerAngle = 0; crawl = false;
        lastExit = {reason, position: valid(p) ? p.GetAbsOrigin() : null};
        if (valid(p)) p.SetParent(valid(pilotParent) ? pilotParent : undefined);
        pilotParent = undefined;
        seatPose = null;
    },
    enterData: () => ({hp: health.hp, position: body.GetAbsOrigin(), camera: camera.mode()}),
    exitData: () => ({hp: health.hp, exit: lastExit})});
const exitSeat = seat.exit;
seat.exit = reason => {
    if (valid(seat.player) && seat.player.IsAlive() && !['reset', 'round', 'disconnect', 'death'].includes(reason) && !refreshExit()) {
        core.log('exit_blocked', {reason}); return false;
    }
    exitSeat(reason); return true;
};

function placeDriver() {
    if (!seat.player || !valid(body)) return;
    // The driver is parented to its own body, so vehicle/child collisions are excluded.
    const [sx, sy, sz] = config.seat;
    seat.player.Teleport({position: transform([sx, sy, sz - 14]), velocity: zero});
    if (valid(decoy)) {
        const a = body.GetAbsAngles(), p = transform([sx, sy, sz]);
        const pose = [p.x, p.y, p.z, a.pitch, a.yaw, a.roll];
        if (!seatPose || pose.some((v, i) => Math.abs(v - seatPose[i]) > .05)) {
            decoy.SetModelScale(.5); decoy.SetParent(body);
            decoy.Move({position: p, angles: a}); seatPose = pose;
        }
    }
}
// Wheels follow their own travel (suspension), the front ones turn with the steering, all spin with the distance.
function placeWheels() {
    if (!valid(body)) return;
    const a = body.GetAbsAngles(), o = body.GetAbsOrigin();
    const pose = [o.x, o.y, o.z, a.pitch, a.yaw, a.roll, spin, steerAngle, ...travel];
    if (lastPose && pose.every((v, i) => Math.abs(v - lastPose[i]) < .08)) return;
    lastPose = pose;
    const b = basis(a);
    for (let i = 0; i < wheels.length; i++) {
        const s = (i < 2 ? steerAngle : 0) * rad, c = Math.cos(s), n = Math.sin(s), r = spin * rad;
        const f = {x: b.f.x * c + b.l.x * n, y: b.f.y * c + b.l.y * n, z: b.f.z * c + b.l.z * n};
        const l = {x: b.l.x * c - b.f.x * n, y: b.l.y * c - b.f.y * n, z: b.l.z * c - b.f.z * n};
        const ff = {x: f.x * Math.cos(r) - b.u.x * Math.sin(r), y: f.y * Math.cos(r) - b.u.y * Math.sin(r), z: f.z * Math.cos(r) - b.u.z * Math.sin(r)};
        const uu = {x: f.x * Math.sin(r) + b.u.x * Math.cos(r), y: f.y * Math.sin(r) + b.u.y * Math.cos(r), z: f.z * Math.sin(r) + b.u.z * Math.cos(r)};
        wheels[i].Move({position: local(o, b, WHEELS[i][0], WHEELS[i][1], WZ + travel[i]), angles: anglesOf(ff, l, uu)});
    }
}
// Suspension sensing: where each tyre touches. travel[i] > 0 = the wheel is pushed up into its arch.
function sense() {
    const o = body.GetAbsOrigin(), b = basis(body.GetAbsAngles()), ign = ignored(seat.player);
    const fit = [];
    contacts = 0;
    for (let i = 0; i < 4; i++) {
        let best = -Infinity;
        for (const k of SAMPLES) {
            const dx = k * R, start = local(o, b, WHEELS[i][0] + dx, WHEELS[i][1], TOP);
            const t = Instance.TraceLine({start, end: core.add(start, b.u, -REACH), ignoreEntity: ign, ignorePlayers: true});
            if (!t.didHit || t.startedInSolid || (t.normal && dot(t.normal, b.u) < .3)) continue;
            const ground = TOP - core.dist(start, t.end);
            best = Math.max(best, ground + Math.sqrt(R * R - dx * dx) - WZ);
        }
        touch[i] = best >= -DOWN;
        travel[i] = touch[i] ? Math.min(UP, best) : -DOWN;
        if (touch[i]) { contacts++; fit.push([WHEELS[i][0], WHEELS[i][1], Math.min(best, UP + 8), 1]); }
        // No ground under this wheel: gravity pulls this corner down until the tyre touches again
        // (without it a car that lost its front wheels on a crest kept its nose up for good).
        else fit.push([WHEELS[i][0], WHEELS[i][1], FALL, FALL_WEIGHT]);
    }
    grounded = contacts >= 2;
    return fit;
}
// Least-squares plane d = h + p*x + q*y through the wheels' required lifts (lightly regularised, so two or three
// contacts still give a pose). h: lift of the body origin, p/q: slopes (nose up / left side up).
function plane(fit) {
    const lam = 400;
    let a11 = 0, a12 = 0, a13 = 0, a22 = lam, a23 = 0, a33 = lam, b1 = 0, b2 = 0, b3 = 0;
    for (const [x, y, d, w] of fit) {
        a11 += w; a12 += w * x; a13 += w * y; a22 += w * x * x; a23 += w * x * y; a33 += w * y * y;
        b1 += w * d; b2 += w * x * d; b3 += w * y * d;
    }
    const det = a11 * (a22 * a33 - a23 * a23) - a12 * (a12 * a33 - a23 * a13) + a13 * (a12 * a23 - a22 * a13);
    if (Math.abs(det) < 1e-6) return {h: 0, p: 0, q: 0};
    const h = (b1 * (a22 * a33 - a23 * a23) - a12 * (b2 * a33 - a23 * b3) + a13 * (b2 * a23 - a22 * b3)) / det;
    const p = (a11 * (b2 * a33 - a23 * b3) - b1 * (a12 * a33 - a23 * a13) + a13 * (a12 * b3 - b2 * a13)) / det;
    const q = (a11 * (a22 * b3 - b2 * a23) - a12 * (a12 * b3 - b2 * a13) + b1 * (a12 * a23 - a22 * a13)) / det;
    return {h, p, q};
}
function crash(v, now) {
    impactWindow.push({time: now, velocity: lastVelocity, speed: Math.hypot(lastVelocity.x, lastVelocity.y)});
    impactWindow = impactWindow.filter(x => now - x.time <= .18);
    const peak = impactWindow.reduce((a, b) => b.speed > a.speed ? b : a, {speed: 0, velocity: zero});
    const prev = peak.speed, cur = Math.hypot(v.x, v.y), loss = prev - cur;
    if (prev > 240 && loss > 100 && now - lastCrash > .6 && !health.dead) {
        const p = body.GetAbsOrigin(), t = Instance.TraceLine({start: {...p, z: p.z + 40},
            end: {x: p.x + peak.velocity.x / prev * 125, y: p.y + peak.velocity.y / prev * 125, z: p.z + 40}, ignoreEntity: ignored(seat.player), ignorePlayers: true});
        if (t.didHit && t.normal?.z < .4) {
            lastCrash = now; impactWindow = []; const damage = Math.min(100, (loss - 60) * config.crash_damage);
            health.hurt(damage, 'crash'); core.log('crash', {speed: prev, loss, damage});
        }
    }
}
function runover(now, dt) {
    if (speed < 180 || health.dead) return;
    const b = basis(body.GetAbsAngles()), o = body.GetAbsOrigin();
    // Sweep a torso-sized box over the real previous-to-current car path, including fast crossings.
    for (const p of core.pawns({bots: true})) {
        if (p === seat.player || now - (hits.get(p) ?? -10) < .8) continue;
        const q = p.GetAbsOrigin(), dx = q.x - o.x, dy = q.y - o.y;
        const x = dx * b.f.x + dy * b.f.y, y = dx * b.l.x + dy * b.l.y;
        if (Math.abs(x) > 118 + speed * dt || Math.abs(y) > 58 || q.z + 72 < o.z + 5 || q.z > o.z + 80) continue;
        const t = Instance.TraceLine({start: {x: o.x, y: o.y, z: o.z + 40}, end: {x: q.x, y: q.y, z: q.z + 36}, ignoreEntity: ignored(seat.player)});
        if (t.didHit && t.hitEntity !== p) continue; // Do not hit through a wall.
        const damage = clamp((speed - 120) * .15, 10, 90);
        // No pilot attacker: Seat cancels every damage event attributed to the seated pilot.
        p.TakeDamage({damage, damageTypes: CSDamageTypes.CRUSH ?? 1, inflictor: body, attacker: body});
        hits.set(p, now); core.log('runover', {slot: core.slot(p), damage, speed});
    }
}
function drive(dt, fit) {
    const a = body.GetAbsAngles(), v = body.GetAbsVelocity(), p = health.dead ? undefined : seat.player, b = basis(a);
    const key = k => !!p?.IsInputPressed(k);
    const left = key(CSInputs.LEFT) ? 1 : 0, right = key(CSInputs.RIGHT) ? 1 : 0;
    const gas = key(CSInputs.FORWARD) ? 1 : 0, back = key(CSInputs.BACK) ? 1 : 0, hand = key(CSInputs.JUMP);
    crawl = key(CSInputs.WALK);
    const ctl = JSON.stringify({gas, back, left, right, hand, crawl});
    if (p && ctl !== controls) { controls = ctl; core.log('controls', JSON.parse(ctl)); }
    // Speed-sensitive steering: the angle shrinks from ST.max to ST.fast at top speed, the wheel turns at ST.rate
    // (half as fast at top speed: it feels heavier) and comes back to the centre at ST.return degrees per second.
    const fast = clamp(Math.abs(longitudinal) / config.speed, 0, 1), aimSteer = (left - right) * (ST.max + (ST.fast - ST.max) * fast);
    const centring = aimSteer === 0 || (steerAngle !== 0 && Math.sign(aimSteer) !== Math.sign(steerAngle));
    const rate = (centring ? ST.return : ST.rate * (1 - .5 * fast)) * dt;
    steerAngle += clamp(aimSteer - steerAngle, -rate, rate);
    if (!contacts) return; // In the air: the engine's gravity, rotation and momentum, untouched.
    const support = Math.min(1, contacts / 3);
    // Tangent frame of the body (it follows the wheels' plane): forward/left along the ground, up along the body.
    const vu = dot(v, b.u);
    longitudinal = dot(v, b.f); lateral = dot(v, b.l);
    // Gravity split along the ground frame. The engine's own gravity is cancelled below (pre-added) while
    // the tyres carry the car, so the slope part is applied here and the tyres fight it within friction.
    const gf = -G * b.f.z, gl = -G * b.l.z, load = Math.max(0, G * b.u.z);
    const toward0 = (x, d) => Math.sign(x) * Math.max(0, Math.abs(x) - d);
    const top = config.speed * (crawl ? .35 : 1), accel = config.accel * (crawl ? 1.4 : 1);
    // What the driver (or the parking brake) wants the tyres to do along the car.
    let tf;
    if (hand || !p) tf = toward0(longitudinal, (hand ? config.brake : 380) * dt);
    else if (back && longitudinal > 30) tf = Math.max(0, longitudinal - config.brake * dt);
    else if (gas && longitudinal < -30) tf = Math.min(0, longitudinal + config.brake * dt);
    else if (gas || back) tf = clamp(longitudinal + (gas - back) * accel * dt, -config.reverse * (crawl ? .6 : 1), top);
    else tf = toward0(longitudinal, ROLLING * dt) + gf * dt;       // coasting rolls with the slope
    // Sideways the tyres grip (the handbrake lets the rear go).
    const ts = lateral * Math.max(0, 1 - dt * config.grip * (hand ? .22 : 1));
    // Friction circle: the tyre force (beyond gravity) is at most MU * load, shared by the contacts.
    let df = tf - (longitudinal + gf * dt), ds = ts - (lateral + gl * dt);
    const limit = MU * load * support * dt, want = Math.hypot(df, ds);
    if (want > limit && want > 0) { df *= limit / want; ds *= limit / want; }
    const fwd = longitudinal + gf * dt + df, side = lateral + gl * dt + ds;
    // Steering (bicycle model) needs the front tyres on the ground.
    const front = (touch[0] ? 1 : 0) + (touch[1] ? 1 : 0);
    const yawRate = front ? clamp(fwd / 120, -4.5, 4.5) * Math.tan(steerAngle * rad) * Math.min(1, Math.abs(fwd) / 55) * (front / 2) : 0;
    // Suspension: spring-damper toward the plane of the wheels (stiffer in bump than in rebound).
    const s = plane(fit);
    const lift = s.h >= 0 ? s.h * S.stiffness : s.h * S.rebound;
    const nu = vu + (clamp(lift, -420, 520) - vu) * Math.min(1, dt * S.damping) * support;
    const pitchRate = clamp(-Math.atan(s.p) / rad * 9, -140, 140) * support;
    const rollRate = clamp(Math.atan(s.q) / rad * 9, -140, 140) * support;
    const angles = {pitch: clamp(a.pitch + pitchRate * dt, -80, 80), yaw: a.yaw + yawRate / rad * dt,
                    roll: clamp(a.roll + rollRate * dt, -80, 80)};
    const nb = basis(angles);
    // Velocity in the new frame; the engine adds gravity during its step, so the carried share is pre-added.
    const vel = core.add(core.add(core.add(zero, nb.f, fwd), nb.l, side), nb.u, nu);
    vel.z += G * dt * support;
    body.Teleport({angles, velocity: vel, angularVelocity: zero});
    longitudinal = fwd; lateral = side;
}
// The chase camera orbits with the mouse; when the mouse rests for a moment while the car moves, the view swings
// back behind the car (as in driving games). Our own nudges are told apart from the player's mouse by the last yaw set.
let viewYawSet = null, mouseIdleSince = 0;
const angleDiff = (a, b) => ((a - b + 540) % 360) - 180;
function followView(p, now, dt) {
    if (!camera.chase) { viewYawSet = null; return; }
    const eye = p.GetEyeAngles();
    if (viewYawSet === null || Math.abs(angleDiff(eye.yaw, viewYawSet)) > .3) mouseIdleSince = now;
    viewYawSet = eye.yaw;
    if (now - mouseIdleSince < 1.2 || speed < 80) return;
    const d = angleDiff(body.GetAbsAngles().yaw, eye.yaw);
    if (Math.abs(d) < .5) return;
    const yaw = eye.yaw + d * Math.min(1, dt * 2.5);
    p.SetEyeAngles({pitch: eye.pitch, yaw, roll: 0});
    viewYawSet = yaw;
}
// Upside down or on its side and still for 2.5 s: put the car back on its wheels.
function selfRight(now) {
    const a = body.GetAbsAngles();
    const tipped = Math.abs(a.roll) > 70 || Math.abs(a.pitch) > 70;
    if (!tipped || speed > 60 || health.dead) { upsideSince = 0; return; }
    if (!upsideSince) { upsideSince = now; return; }
    if (now - upsideSince < 2.5) return;
    upsideSince = 0;
    const o = body.GetAbsOrigin();
    body.Teleport({position: {x: o.x, y: o.y, z: o.z + 48}, angles: {pitch: 0, yaw: a.yaw, roll: 0}, velocity: zero, angularVelocity: zero});
    core.log('righted', {position: o});
}
function setup() {
    if (seat.player) seat.exit('round');
    body = Instance.FindEntityByName(E.body); wheels = E.wheels.map(n => Instance.FindEntityByName(n)); decoy = Instance.FindEntityByName(E.pilot);
    if (W.scale !== 1) for (const w of wheels) if (valid(w)) w.SetModelScale(W.scale);
    if (!valid(body) || wheels.some(w => !valid(w)) || !hud.find()) throw Error('missing car entities');
    for (const w of wheels) w.SetParent(body);
    // Round restart restores the home position even on engines that keep physical props.
    body.Teleport({position: core.vec(config.origin), angles: {pitch: 0, yaw: config.yaw, roll: 0}, velocity: zero, angularVelocity: zero});
    // The suspension holds the body every tick: it must never fall asleep in mid-air.
    Instance.EntFireAtTarget({target: body, input: 'DisableAutoSleeping'});
    health.restore(); hud.hideAll(); seat.clearEdges(); lastTime = Instance.GetGameTime(); lastCrash = -10;
    contacts = 0; grounded = false; lastExit = lastSafe = entrySafe = null; motor(null);
    core.log('ready', {position: body.GetAbsOrigin(), hp: health.hp}); Instance.SetNextThink(lastTime + .016);
}
Instance.SetThink(() => {
    const now = Instance.GetGameTime(), dt = Math.min(.05, Math.max(0, now - lastTime)); lastTime = now;
    try {
        if (!valid(body)) { if (seat.player) seat.exit('death'); Instance.SetNextThink(now + .1); return; }
        health.update(now); const canExit = refreshExit();
        if (health.dead && seat.player && canExit) seat.exit('destroyed');
        // Fully enclosed: keep the pilot seated until a tested point becomes free.
        // Death and round callbacks still use the core's restoration without moving a live pawn.
        const humans = core.pawns();
        seat.update(canExit ? humans : humans.filter(p => p !== seat.player));
        const v = body.GetAbsVelocity(); speed = Math.hypot(v.x, v.y);
        // A wreck stays on its wheels too (the body has no tyre collision): it is a parked car without a driver.
        const fit = sense();
        crash(v, now); drive(dt, fit); selfRight(now);
        if (!health.dead) runover(now, dt);
        lastVelocity = body.GetAbsVelocity();
        spin = (spin + longitudinal / R / rad * dt) % 360; placeWheels();
        const ang = body.GetAbsAngles();
        peak = {bump: Math.max(peak.bump, ...travel), droop: Math.min(peak.droop, ...travel),
                pitch: Math.max(peak.pitch, Math.abs(ang.pitch)), roll: Math.max(peak.roll, Math.abs(ang.roll)),
                air: peak.air + (contacts ? 0 : dt), speed: Math.max(peak.speed, speed)};
        if (seat.player) {
            camera.update(seat.player); followView(seat.player, now, dt); placeDriver();
            motor(speed > 40 ? 'drive' : 'idle');
            if (hud.due('speed', now, .1)) {
                hud.text(seat.player, Math.round(speed * .09144) + ' km/h   HP ' + Math.ceil(health.hp) + '/' + config.health +
                    (crawl ? '   CRAWL' : '') + (canExit ? '   E: exit' : '   Exit blocked') + (!contacts ? '   AIR' : ''));
                hud.flag(seat.player, 'Low', !!config.health && health.hp <= config.health / 3);
            }
        }
    } catch (e) { if (seat.player) seat.exit('error'); throw Error('V_CAR_ERROR ' + config.name + ': ' + String(e)); }
    Instance.SetNextThink(now + .016);
});
core.on('OnActivate', setup); core.on('OnRoundStart', setup);
const rounded = v => Math.round(v * 100) / 100;
const compact = o => o ? Object.fromEntries(Object.entries(o).map(([k, v]) => [k, rounded(v)])) : null;
core.state(() => { const p = peak; peak = {bump: 0, droop: 0, pitch: 0, roll: 0, air: 0, speed: 0}; return {peak: Object.fromEntries(Object.entries(p).map(([k, v]) => [k, rounded(v)])), recent: core.recent.filter(x => !x.startsWith('state ')).slice(-8).map(x => x.slice(0, 75)),
    active: !!seat.player, hp: rounded(health.hp), dead: health.dead, position: valid(body) ? compact(body.GetAbsOrigin()) : null,
    velocity: valid(body) ? compact(body.GetAbsVelocity()) : null, bodyAngles: valid(body) ? compact(body.GetAbsAngles()) : null,
    speed: rounded(speed), kph: rounded(speed * .09144), contacts, grounded, travel: travel.map(rounded), crawl,
    steer: rounded(steerAngle), spin: rounded(spin), camera: camera.mode(), chase: camera.chase,
    engine: motorMode, exit: lastExit, stats: health.stats,
    wheels: wheels.filter(valid).map(w => ({position: compact(w.GetAbsOrigin()), angles: compact(w.GetAbsAngles())}))}; });
