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
// Own bodies (vehicles.py door/exits/size/passengers); absent fields keep the stock SUV's numbers.
const DOOR = config.door ?? {at: [-8, 66, 0], radius: 62};
const EXITS = config.exits ?? [[-8, 80, 90], [-8, -80, 90], [-20, 0, 170], [-135, 0, 90], [135, 0, 90]];
const HALF = config.size ? [config.size[0] / 2, config.size[1] / 2] : [118, 58];
const RIDE = config.passengers;          // {floor, roof, spots}: players inside the body ride with it
const DOORS = RIDE?.doors ?? [];
const PILOT = config.pilot ?? {scale: .5, at: config.seat};
// Turning radius follows the wheelbase (the stock SUV's 122 units gave the original 120).
const PROBE = config.size ? Math.max(125, HALF[0] + 30) : 125;    // crash probe ahead of the body centre
// Status line words (hud.labels translates them).
const LABEL = {kmh: 'km/h', crawl: 'CRAWL', exit: 'E: exit', blocked: 'Exit blocked', air: 'AIR', ...(config.hud?.labels ?? {})};
const WHEELBASE = (WHEELS[0][0] - WHEELS[2][0]) * 120 / 122;
let body, wheels = [], decoy, lastTime = 0, steerAngle = 0, spin = 0, lastPose;
let contacts = 0, grounded = false, speed = 0, longitudinal = 0, lateral = 0, motorMode = null, crawl = false;
let lastVelocity = zero, lastCrash = -10, lastExit = null, lastSafe = null, entrySafe = null, seatPose, pilotParent;
let controls = '', upsideSince = 0, impactWindow = [];
let travel = [0, 0, 0, 0], touch = [false, false, false, false];
// Peaks since the last state report: how hard the suspension worked (tests read them instead of guessing from frames).
let peak = {bump: 0, droop: 0, pitch: 0, roll: 0, air: 0, speed: 0};
const hits = new Map();
const hud = Hud({core, name: E.hud, panel: 'control', variable: 'telemetry'});
const camera = Camera({core, follow: () => body, views: config.camera, pilot: () => seat.player});

const dot = (a, b) => a.x * b.x + a.y * b.y + a.z * b.z;
function local(o, b, x, y, z) {
    return {x: o.x + b.f.x * x + b.l.x * y + b.u.x * z, y: o.y + b.f.y * x + b.l.y * y + b.u.y * z,
            z: o.z + b.f.z * x + b.l.z * y + b.u.z * z};
}
function transform(off) { return local(body.GetAbsOrigin(), basis(body.GetAbsAngles()), off[0], off[1], off[2]); }
function ignored(p) { return [body, ...wheels, decoy, p].filter(valid); }
// Hull traces never ignore an entity parented to the body (the seated pilot, the decoy): CS2 then skips the whole
// hierarchy, the body included, and a point inside the bus reads as free. The decoy is never solid anyway.
const loose = p => [p].filter(e => valid(e) && !valid(e.GetParent?.()));
// What the last failed clearance touched: refusals and exit_blocked lines name it.
let clearWhy = null;
function clearance(point, p) {
    // From 2 units up (a pawn settled a fraction of a unit into a slope touches it; a step is 18 high).
    const s = {x: point.x, y: point.y, z: point.z + 2};
    const t = core.boxTrace({start: s, end: s, mins: {x: -16, y: -16, z: 0}, maxs: {x: 16, y: 16, z: 70},
        ignoreEntity: loose(p)});
    const ok = !t.startedInSolid && !t.didHit;
    clearWhy = ok ? null : {solid: !!t.startedInSolid,
        blocker: valid(t.hitEntity) ? (t.hitEntity.GetEntityName() || t.hitEntity.GetClassName()) : 'world'};
    return ok;
}
function safeExit(p) {
    // Keep the car in the trace: a point inside its body is never considered free.
    for (const off of EXITS) {
        const start = transform(off), t = core.boxTrace({start, end: {...start, z: start.z - 190},
            mins: {x: -16, y: -16, z: 0}, maxs: {x: 16, y: 16, z: 72}, ignoreEntity: loose(p)});
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
const riders = new Map();     // pawn -> {door, spot}
const health = Health({core, body: () => body, max: config.health, native: true, parts: () => wheels,
    effects: {smoke: E.smoke, fire: E.fire, boom: E.boom, boom_sound: E.boom_sound}, respawn: config.respawn,
    spawn: {origin: config.origin, yaw: config.yaw}, guard: w => !!seat.player && w?.GetOwner() === seat.player,
    onDestroy() { if (seat.player) seat.exit('destroyed'); leaveAll('destroyed'); motor(null); },
    onRestore() {
        if (parked) { parked = false; parkSince = 0; Instance.EntFireAtTarget({target: body, input: 'EnableMotion'}); }
        Instance.EntFireAtTarget({target: body, input: 'SetMass', value: config.mass});
        steerAngle = spin = speed = longitudinal = lateral = 0; lastPose = null; lastVelocity = zero;
        travel = [0, 0, 0, 0]; hits.clear(); placeWheels(); impactWindow = []; upsideSince = 0;
        if (seat.player) seat.exit('respawn');
        leaveAll('respawn');
    }});
const seatCore = {...core, input: (name, fn) => core.input(name, e => {
    if (name === 'Exit' && seat.player && !refreshExit()) { core.log('exit_blocked', clearWhy || {}); return; }
    fn(e);
})};
const seat = Seat({core: seatCore, camera, hud, target: () => body, pawn: E.pawn, decoy: () => decoy, reach: config.reach,
    allow(p) {
        if (!valid(body) || riders.has(p)) return false;
        if (health.dead) return {reason: 'destroyed', respawn: health.respawnIn(Instance.GetGameTime())};
        // The eye trace must hit the car, AND feet must be beside the driver's door (+local Y).
        const at = p.GetAbsOrigin(), door = transform(DOOR.at);
        // With passengers, E elsewhere boards instead (passengers()): no refusal line then.
        if (Math.hypot(at.x - door.x, at.y - door.y) > DOOR.radius || Math.abs(at.z - door.z) > 40) return RIDE ? false : {reason: 'driver_door'};
        // 1 unit up: a box resting exactly on a floor (a bus salon) touches it and would read as solid.
        if (!clearance(at, p)) return {reason: 'entry_in_solid', ...clearWhy};
    },
    onEnter(p, saved) {
        entrySafe = lastSafe = {...saved.position};
        pilotParent = p.GetParent(); p.SetParent(body);
        // A parented pawn: GetEyeAngles is in world space but SetEyeAngles is relative to the parent (measured: car
        // at yaw 90, SetEyeAngles yaw 90 -> view 180). 0 = along the car whatever its heading.
        p.SetEyeAngles({pitch: 12, yaw: 0, roll: 0});
        Instance.EntFireAtTarget({target: body, input: 'DisableAutoSleeping'});
        camera.start(p); hud.show(p); seatPose = null; placeDriver(); camera.apply(); motor('idle'); refreshExit();
    },
    onExit(p, reason) {
        motor(null); steerAngle = 0; crawl = false;
        // A body with passengers: the driver stays inside (the generic search may pick a spot in the hull).
        if (DOORS.length && valid(p) && p.IsAlive() && !['reset', 'round', 'disconnect', 'death'].includes(reason)) aisle(p);
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
        const a = body.GetAbsAngles(), p = transform(PILOT.at);
        const pose = [p.x, p.y, p.z, a.pitch, a.yaw, a.roll];
        if (!seatPose || pose.some((v, i) => Math.abs(v - seatPose[i]) > .05)) {
            decoy.SetModelScale(PILOT.scale); decoy.SetParent(body);
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
            end: {x: p.x + peak.velocity.x / prev * PROBE, y: p.y + peak.velocity.y / prev * PROBE, z: p.z + 40}, ignoreEntity: ignored(seat.player), ignorePlayers: true});
        if (t.didHit && t.normal?.z < .4) {
            lastCrash = now; impactWindow = []; const damage = Math.min(100, (loss - 60) * config.crash_damage);
            health.hurt(damage, 'crash'); core.log('crash', {speed: prev, loss, damage});
        }
    }
}
// A passenger: feet on the floor inside the body (body frame), not the driver.
function aboard(p) {
    if (!RIDE || p === seat.player) return false;
    const q = p.GetAbsOrigin(), o = body.GetAbsOrigin(), b = basis(body.GetAbsAngles());
    const d = {x: q.x - o.x, y: q.y - o.y, z: q.z - o.z};
    const x = dot(d, b.f), y = dot(d, b.l), z = dot(d, b.u);
    return Math.abs(x) < HALF[0] && Math.abs(y) < HALF[1] && z > RIDE.floor - 12 && z < RIDE.roof;
}
function runover(now, dt) {
    if (speed < 180 || health.dead) return;
    const b = basis(body.GetAbsAngles()), o = body.GetAbsOrigin();
    // Sweep a torso-sized box over the real previous-to-current car path, including fast crossings.
    for (const p of core.pawns({bots: true})) {
        if (p === seat.player || now - (hits.get(p) ?? -10) < .8 || riders.has(p) || aboard(p)) continue;
        const q = p.GetAbsOrigin(), dx = q.x - o.x, dy = q.y - o.y;
        const x = dx * b.f.x + dy * b.f.y, y = dx * b.l.x + dy * b.l.y;
        if (Math.abs(x) > HALF[0] + speed * dt || Math.abs(y) > HALF[1] || q.z + 72 < o.z + 5 || q.z > o.z + 80) continue;
        const t = Instance.TraceLine({start: {x: o.x, y: o.y, z: o.z + 40}, end: {x: q.x, y: q.y, z: q.z + 36}, ignoreEntity: ignored(seat.player)});
        if (t.didHit && t.hitEntity !== p) continue; // Do not hit through a wall.
        const damage = clamp((speed - 120) * .15, 10, 90);
        // No pilot attacker: Seat cancels every damage event attributed to the seated pilot.
        p.TakeDamage({damage, damageTypes: CSDamageTypes.CRUSH ?? 1, inflictor: body, attacker: body});
        hits.set(p, now); core.log('runover', {slot: core.slot(p), damage, speed});
    }
}
// Passengers (vehicles.py passengers): each door has a group of standing spots. E near a door (or inside) puts a
// player on a random free spot of that group, E again lets them off; whoever stands inside when the body starts to
// move is put on a spot (a walking player inside a moving physics body blocks it). Riders stay normal standing
// players on the floor (accurate fire, crouch and stand up), not parented (a parented pawn turns and leans with the
// body): every tick they stand on their spot where the floor will be after the physics step, at rest. Physics crush
// never hurts the driver or riders.
const prompt = Hud({core, name: E.hud, panel: 'prompt', variable: 'prompt'});
const useHeld = new Map(), shown = new Map(), toggled = new Map();   // toggled: slot -> time of the last E action
const E_GAP = .5;
const blocked = new Map();   // slot -> until when the "no room to get out" prompt shows            // one E action per half second: a held or bouncing key never boards and leaves at once
let lastPilot;           // the driver of the previous tick: their E that left the wheel must not board too
function bodyFrame(q) {
    const o = body.GetAbsOrigin(), b = basis(body.GetAbsAngles()), d = {x: q.x - o.x, y: q.y - o.y, z: q.z - o.z};
    return [dot(d, b.f), dot(d, b.l), dot(d, b.u)];
}
function doorAt(p) {
    const q = bodyFrame(p.GetAbsOrigin());
    let best = -1, bestD = Infinity;
    DOORS.forEach((d, i) => {
        const r = Math.hypot(q[0] - d.at[0], q[1] - d.at[1]);
        if (r < d.radius && Math.abs(q[2] - d.at[2]) < 60 && r < bestD) { best = i; bestD = r; }
    });
    return best;
}
function nearestDoor(p) {
    const q = bodyFrame(p.GetAbsOrigin());
    let best = 0, bestD = Infinity;
    DOORS.forEach((d, i) => { const r = Math.hypot(q[0] - d.at[0], q[1] - d.at[1]); if (r < bestD) { best = i; bestD = r; } });
    return best;
}
function atDriverDoor(p) {
    const q = bodyFrame(p.GetAbsOrigin());
    return Math.hypot(q[0] - DOOR.at[0], q[1] - DOOR.at[1]) < DOOR.radius && Math.abs(q[2] - DOOR.at[2]) < 40;
}
// Room for a standing player at a spot of the body as it is now: nothing solid (the body, a wall it scrapes, another
// player) in the hull, 1 unit above the feet.
function roomAt(at, p) {
    const s = {...at, z: at.z + 1}, t = core.boxTrace({start: s, end: s, mins: {x: -16, y: -16, z: 0}, maxs: {x: 16, y: 16, z: 72},
        ignoreEntity: loose(p)});
    return !t.startedInSolid && !t.didHit;
}
function freeSpot(door, p) {
    const used = new Set([...riders.entries()].filter(([q, r]) => q !== p && r.door === door).map(([, r]) => r.spot));
    const free = DOORS[door].spots.map((_, i) => i).filter(i => !used.has(i) && (!p || roomAt(transform(DOORS[door].spots[i]), p)));
    return free.length ? free[Math.floor(Math.random() * free.length)] : -1;
}
const UNDUCK = 18;           // CS2 stands an airborne crouched player up by lowering the feet this much
function placeRider(p, r, dt) {
    // Feet on the floor where it will be after this tick's physics step (the body moves after the script). Zero
    // velocity: a rider counts as standing still on the ground, so their shots are as accurate as standing ones.
    const at = transform(DOORS[r.door].spots[r.spot]), v = body.GetAbsVelocity(), moving = Math.hypot(v.x, v.y, v.z) > 1;
    // A still body (parked): a rider already at the spot is left alone. A teleport drops the ground state, and an
    // "airborne" crouched player stands up by lowering the feet into the floor: they could not stand up.
    if (!moving && core.dist(p.GetAbsOrigin(), at) < 1.5) return;
    let target = core.add(at, v, dt);
    // Moving: the teleport every tick cannot be avoided, so a crouched rider letting go of crouch gets the feet raised
    // by the stand-up drop for that tick: they stand up onto the floor instead of into it.
    if ((p.IsDucked() || p.IsDucking()) && !p.IsInputPressed(CSInputs.DUCK)) target = {...target, z: target.z + UNDUCK};
    p.Teleport({position: target, velocity: zero});
}
// Every tick: a spot that has no room (the body pressed on a wall, a player in it) moves its rider to another free
// spot, else out of the nearest door; a rider is never held inside anything solid (that fight shook the body and
// crushed the rider).
function holdRider(p, r, dt) {
    if (roomAt(transform(DOORS[r.door].spots[r.spot]), p)) { placeRider(p, r, dt); return; }
    for (const d of doorOrder(r.door)) {
        const s = freeSpot(d, p);
        if (s >= 0) { r.door = d; r.spot = s; placeRider(p, r, dt); core.log('respot', {slot: core.slot(p), door: d, spot: s}); return; }
    }
    leave(p, 'no_room', true);
}
// The door's own group first, then the groups of the nearest other doors.
function doorOrder(door) {
    return DOORS.map((_, i) => i).sort((a, b) =>
        Math.abs(DOORS[a].at[0] - DOORS[door].at[0]) - Math.abs(DOORS[b].at[0] - DOORS[door].at[0]));
}
const fullLogged = new Map();
function board(p, door) {
    for (const d of doorOrder(door)) {
        const s = freeSpot(d, p);
        if (s < 0) continue;
        const r = {door: d, spot: s};
        riders.set(p, r);
        placeRider(p, r, 0);
        // Face the nearest side window (spots on the centre line face the rear).
        const y = DOORS[d].spots[s][1], face = Math.abs(y) < 6 ? 180 : (y < 0 ? -90 : 90);
        p.SetEyeAngles({pitch: 0, yaw: body.GetAbsAngles().yaw + face, roll: 0});
        core.log('board', {slot: core.slot(p), door: d, spot: s});
        return true;
    }
    const now = Instance.GetGameTime();
    if (now - (fullLogged.get(core.slot(p)) ?? -10) > 2) { fullLogged.set(core.slot(p), now); core.log('full', {slot: core.slot(p)}); }
    return false;
}
function doorOutside(i, p) {
    const d = DOORS[i].at, side = d[1] < 0 ? -1 : 1;
    for (const dx of [0, -50, 50]) {
        const start = transform([d[0] + dx, d[1] + side * 48, d[2] + 40]), t = core.boxTrace({start,
            end: {...start, z: start.z - 160}, mins: {x: -16, y: -16, z: 0}, maxs: {x: 16, y: 16, z: 72},
            ignoreEntity: loose(p)});
        if (t.startedInSolid || !t.didHit || !(t.normal?.z >= .65) || t.hitEntity === body) continue;
        if (valid(t.hitEntity) && t.hitEntity.GetClassName() === 'player') continue;     // not on someone's head
        const at = {...t.end, z: t.end.z + 2};
        if (clearance(at, p)) return at;
    }
    return null;
}
// Off the bus. Standing still: stay on the floor at the spot. Moving (or `out`): through the nearest door with room
// outside; a rider who asked to get off at speed with no room outside keeps riding (returns false).
function leave(p, reason, out = false) {
    const r = riders.get(p);
    if (!r) return true;
    if (!valid(p) || !p.IsAlive()) { riders.delete(p); core.log('leave', {slot: valid(p) ? core.slot(p) : null, reason}); return true; }
    let spot = null;
    // With a driver (or a route test) or at speed nobody stands loose inside: off through a door, or stay on.
    if (speed >= 60 || out || seat.player || auto) {
        for (const d of doorOrder(r.door)) if ((spot = doorOutside(d, p))) break;
        if (!spot && reason === 'use') { core.log('exit_blocked', {slot: core.slot(p)}); blocked.set(core.slot(p), Instance.GetGameTime() + 1.5); return false; }
    }
    riders.delete(p);
    p.Teleport(spot ? {position: spot, velocity: body.GetAbsVelocity()} : {velocity: zero});
    core.log('leave', {slot: core.slot(p), reason, outside: !!spot});
    return true;
}
// The driver leaves the wheel: moving -> a front spot as a passenger; standing -> on the salon floor at the first free
// exit point (a short trace that can only find the body's own floor), else a spot as well.
function aisle(p) {
    if (speed <= 25) {
        for (const off of EXITS) {
            const start = transform([off[0], off[1], RIDE.floor + 40]), t = core.boxTrace({start,
                end: transform([off[0], off[1], RIDE.floor - 6]), mins: {x: -16, y: -16, z: 0}, maxs: {x: 16, y: 16, z: 72},
                ignoreEntity: loose(p)});
            if (t.startedInSolid || !t.didHit || t.hitEntity !== body) continue;
            const at = {...t.end, z: t.end.z + 1};
            if (!clearance(at, p)) continue;
            p.Teleport({position: at, velocity: zero});
            core.log('aisle', {slot: core.slot(p)});
            return;
        }
    }
    if (board(p, 0)) return;
    for (const d of doorOrder(0)) {
        const at = doorOutside(d, p);
        if (at) { p.Teleport({position: at, velocity: body.GetAbsVelocity()}); core.log('aisle_outside', {slot: core.slot(p)}); return; }
    }
    core.log('aisle_blocked', {slot: core.slot(p)});
}
function leaveAll(reason) { for (const p of [...riders.keys()]) leave(p, reason); }
function hint(p, text) {
    const s = core.slot(p), now = Instance.GetGameTime(), last = shown.get(s);
    if (last && last.text === text && now - last.at < 2) return;
    if (!last && text === null) return;
    shown.set(s, {text, at: now});
    if (text) prompt.put(p, text); else prompt.hide(p);
    if (last?.text !== text) core.log('prompt', {slot: s, text});
}
function passengers(dt) {
    // Riders are held every tick: players merely standing on a physics body weigh it down and it does not move.
    for (const [p, r] of [...riders]) {
        if (!valid(p) || !p.IsAlive() || p === seat.player) { riders.delete(p); continue; }
        holdRider(p, r, dt);
        const q = riders.get(p);
        if (q && core.dist(p.GetAbsOrigin(), transform(DOORS[q.door].spots[q.spot])) > 64) leave(p, 'lost', true);
    }
    // Whoever stands inside while the body moves or has a driver goes to a spot: a walking player in a physics body
    // holds it down (the body would not start at all).
    const ride = (speed > 25 || !!seat.player || !!auto) && !health.dead;
    const humans = new Set(core.pawns()), seen = new Set();
    for (const p of core.pawns({bots: true})) {
        if (humans.has(p)) seen.add(core.slot(p));
        if (p === seat.player || p === lastPilot) {
            if (humans.has(p)) {
                const pressed = p.IsInputPressed(CSInputs.USE);
                hint(p, null); useHeld.set(core.slot(p), pressed);
                if (pressed) toggled.set(core.slot(p), Instance.GetGameTime());   // the wheel's E is not a boarding E too
            }
            continue;
        }
        const riding = riders.has(p), door = riding ? -1 : doorAt(p), inside = !riding && aboard(p);
        const driverDoor = !seat.player && !riding && atDriverDoor(p);
        if (humans.has(p)) {
            const s = core.slot(p), pressed = p.IsInputPressed(CSInputs.USE), now = Instance.GetGameTime();
            const edge = pressed && !useHeld.get(s) && now - (toggled.get(s) ?? -10) > E_GAP;
            useHeld.set(s, pressed);
            if (edge) toggled.set(s, now);
            // At the driver's place E takes the wheel (Seat); anywhere else at a door or inside it boards.
            if (edge && !health.dead && !driverDoor) {
                if (riding) leave(p, 'use');
                else if (door >= 0 || inside) board(p, door >= 0 ? door : nearestDoor(p));
            }
            hint(p, health.dead ? null : riders.has(p) ? ((blocked.get(s) ?? 0) > now ? RIDE.hint.blocked : RIDE.hint.ride) :
                 driverDoor ? RIDE.hint.drive : (door >= 0 || inside) ? RIDE.hint.board : null);
        }
        if (ride && !riders.has(p) && !aboard(p) && p.GetGroundEntity?.() === body) {
            const d = doorAt(p);
            if (!(d >= 0 && board(p, d))) {
                for (const k of doorOrder(nearestDoor(p))) {
                    const at = doorOutside(k, p);
                    if (at) { p.Teleport({position: at, velocity: body.GetAbsVelocity()}); core.log('off_roof', {slot: core.slot(p)}); break; }
                }
            }
        }
        if (ride && !riders.has(p) && aboard(p) && !board(p, nearestDoor(p))) {
            // No spot left: nobody stands loose in a moving bus; out of the nearest door with room.
            for (const d of doorOrder(nearestDoor(p))) {
                const at = doorOutside(d, p);
                if (at) { p.Teleport({position: at, velocity: body.GetAbsVelocity()}); core.log('overflow_out', {slot: core.slot(p)}); break; }
            }
        }
    }
    // A player who is gone (dead, spectating, disconnected) keeps no prompt.
    for (const [s, v] of [...shown]) if (!seen.has(s)) { if (v.text) prompt.hideSlot(s); shown.delete(s); }
    lastPilot = seat.player;
}
// What hurts the driver or a rider, and what kills them: one line each (tests and online reports read them).
const who = e => valid(e) ? e.GetClassName() + (e === body ? ':body' : '') : null;
core.on('OnPlayerDamage', e => {
    if (e.player === seat.player || riders.has(e.player))
        core.log('hurt', {slot: core.slot(e.player), damage: e.damage, types: e.damageTypes, flags: e.damageFlags,
                          attacker: who(e.attacker), inflictor: who(e.inflictor), rider: riders.has(e.player)});
});
core.on('OnPlayerKill', e => {
    if (e.player === seat.player || riders.has(e.player))
        core.log('killed', {slot: core.slot(e.player), attacker: who(e.attacker), inflictor: who(e.inflictor), rider: riders.has(e.player)});
});
const crushLogged = new Map();
core.on('OnModifyPlayerDamage', e => {
    if (!RIDE || (e.player !== seat.player && !riders.has(e.player))) return;
    // Inside the bus only weapons hurt the driver and the riders (bullets, knives, grenades thrown by players): the
    // body's own pushes, crush, falls and "stuck" damage never do.
    const byPlayer = valid(e.attacker) && e.attacker.GetClassName() === 'player' && e.attacker !== e.player;
    if (valid(e.weapon) || byPlayer) return;
    const now = Instance.GetGameTime(), s = core.slot(e.player);
    if (now - (crushLogged.get(s) ?? -10) > 1) { crushLogged.set(s, now); core.log('crush_blocked', {slot: s, damage: e.damage, types: e.damageTypes}); }
    return {abort: true};
});
// Route test (cheat command <name>_route x1,y1,x2,y2,...[,speed]): without a driver the body drives itself through
// the points with virtual keys and logs route_point / route_done, or route_stuck with the place where it stopped.
let auto = null;
core.command('v9_control_' + config.name + '_route', token => {
    const n = token.split(/[ ,]+/).map(Number).filter(Number.isFinite);
    if (n.length < 2) { auto = null; core.log('route_off'); return; }
    const speed = n.length % 2 ? n.pop() : 300;
    const pts = []; for (let i = 0; i + 1 < n.length; i += 2) pts.push({x: n[i], y: n[i + 1]});
    auto = {pts, i: 0, speed, slow: null}; core.log('route', {points: pts.length, speed});
});
function autoKeys(now) {
    const o = body.GetAbsOrigin(), t = auto.pts[auto.i];
    if (Math.hypot(t.x - o.x, t.y - o.y) < 160) {
        core.log('route_point', {i: auto.i, at: o}); auto.i++;
        if (auto.i >= auto.pts.length) { core.log('route_done', {at: o, hp: health.hp}); auto = null; return null; }
    }
    const tgt = auto.pts[auto.i], want = Math.atan2(tgt.y - o.y, tgt.x - o.x) / rad;
    const err = ((want - body.GetAbsAngles().yaw + 540) % 360) - 180;
    if (now - (auto.logged ?? 0) >= 1) { auto.logged = now; core.log('route_tick', {i: auto.i, at: {x: Math.round(o.x), y: Math.round(o.y)}, yaw: Math.round(body.GetAbsAngles().yaw), err: Math.round(err), speed: Math.round(speed), steer: Math.round(steerAngle)}); }
    if (speed < 25) { auto.slow ??= now; if (now - auto.slow > 2.5) { core.log('route_stuck', {at: o, i: auto.i, hp: health.hp}); auto = null; return null; } }
    else auto.slow = null;
    // Proportional steering (a test driver, not keys): the wheels point at the next point, gas below the route speed.
    return {gas: speed < auto.speed ? 1 : 0, back: 0, left: 0, right: 0, hand: false, steer: err * 1.5};
}
// Parking (fundamental for a hollow body): without a driver or a route test, a body standing on its wheels and nearly
// still has its physics motion switched off: players walking in it, pushing or standing on it cannot slide, sink or
// shake it. Gravity stays: a body in the air or rolling keeps its physics until it settles. A driver turns it back on.
let parked = false, parkSince = 0;
function park(now) {
    const want = !seat.player && !auto && !health.dead && contacts >= 3 && speed < 15;
    if (!want) {
        parkSince = 0;
        if (parked) { parked = false; Instance.EntFireAtTarget({target: body, input: 'EnableMotion'}); core.log('unparked'); }
        return;
    }
    parkSince ||= now;
    if (!parked && now - parkSince > .4) {
        parked = true; longitudinal = lateral = 0;
        body.Teleport({velocity: zero, angularVelocity: zero});
        Instance.EntFireAtTarget({target: body, input: 'DisableMotion'}); core.log('parked');
    }
}
function drive(dt, fit) {
    const a = body.GetAbsAngles(), v = body.GetAbsVelocity(), p = health.dead ? undefined : seat.player, b = basis(a);
    const A = !p && auto && !health.dead ? autoKeys(Instance.GetGameTime()) : null;
    const key = k => !!p?.IsInputPressed(k);
    const left = A ? A.left : key(CSInputs.LEFT) ? 1 : 0, right = A ? A.right : key(CSInputs.RIGHT) ? 1 : 0;
    const gas = A ? A.gas : key(CSInputs.FORWARD) ? 1 : 0, back = A ? A.back : key(CSInputs.BACK) ? 1 : 0, hand = A ? false : key(CSInputs.JUMP);
    crawl = A ? false : key(CSInputs.WALK);
    const ctl = JSON.stringify({gas, back, left, right, hand, crawl});
    if (p && ctl !== controls) { controls = ctl; core.log('controls', JSON.parse(ctl)); }
    // Speed-sensitive steering: the angle shrinks from ST.max to ST.fast at top speed, the wheel turns at ST.rate
    // (half as fast at top speed: it feels heavier) and comes back to the centre at ST.return degrees per second.
    const fast = clamp(Math.abs(longitudinal) / config.speed, 0, 1), lock = ST.max + (ST.fast - ST.max) * fast;
    const aimSteer = A ? clamp(A.steer, -lock, lock) : (left - right) * lock;
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
    if (hand || (!p && !A)) tf = toward0(longitudinal, (hand ? config.brake : 380) * dt);
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
    const yawRate = front ? clamp(fwd / WHEELBASE, -4.5, 4.5) * Math.tan(steerAngle * rad) * Math.min(1, Math.abs(fwd) / 55) * (front / 2) : 0;
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
    // Without a driver a slow body is parked: it holds its place on the ground (no creeping on slopes or
    // under passengers). The suspension still works vertically.
    if (!p && !A && support >= 1 && Math.hypot(fwd, side) < 25) {
        const keep = dot(vel, nb.u);
        vel.x = nb.u.x * keep; vel.y = nb.u.y * keep; vel.z = nb.u.z * keep;
        longitudinal = lateral = 0;
        body.Teleport({angles, velocity: vel, angularVelocity: zero});
        return;
    }
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
    // The pilot is parented: the new view goes in the car's frame (pitch too, or the view creeps on a slope).
    const b = basis(body.GetAbsAngles()), cp = Math.cos(eye.pitch * rad);
    const v = {x: Math.cos(yaw * rad) * cp, y: Math.sin(yaw * rad) * cp, z: -Math.sin(eye.pitch * rad)};
    p.SetEyeAngles({pitch: -Math.asin(clamp(dot(v, b.u), -1, 1)) / rad, yaw: Math.atan2(dot(v, b.l), dot(v, b.f)) / rad, roll: 0});
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
    if (parked && valid(body)) Instance.EntFireAtTarget({target: body, input: 'EnableMotion'});
    parked = false; parkSince = 0;
    if (seat.player) seat.exit('round');
    body = Instance.FindEntityByName(E.body); wheels = E.wheels.map(n => Instance.FindEntityByName(n)); decoy = Instance.FindEntityByName(E.pilot);
    if (W.scale !== 1) for (const w of wheels) if (valid(w)) w.SetModelScale(W.scale);
    if (!valid(body) || wheels.some(w => !valid(w)) || !hud.find()) throw Error('missing car entities');
    for (const w of wheels) w.SetParent(body);
    // Round restart restores the home position even on engines that keep physical props.
    body.Teleport({position: core.vec(config.origin), angles: {pitch: 0, yaw: config.yaw, roll: 0}, velocity: zero, angularVelocity: zero});
    // The suspension holds the body every tick: it must never fall asleep in mid-air.
    Instance.EntFireAtTarget({target: body, input: 'DisableAutoSleeping'});
    leaveAll('round'); prompt.find();
    for (const s of shown.keys()) prompt.hideSlot(s);      // per player: hideAll does not override a shown prompt
    health.restore(); hud.hideAll(); prompt.hideAll(); shown.clear(); useHeld.clear(); toggled.clear(); seat.clearEdges(); lastTime = Instance.GetGameTime(); lastCrash = -10;
    contacts = 0; grounded = false; lastExit = lastSafe = entrySafe = null; motor(null);
    core.log('ready', {position: body.GetAbsOrigin(), hp: health.hp}); Instance.SetNextThink(lastTime + .016);
}
Instance.SetThink(() => {
    const now = Instance.GetGameTime(), dt = Math.min(.05, Math.max(0, now - lastTime)); lastTime = now;
    let next = now + .016;
    try {
        if (!valid(body)) {
            // The body is gone (removed by the engine or the map): nobody rides it and nobody keeps its prompts.
            if (seat.player) seat.exit('death');
            if (riders.size) { core.log('body_gone', {riders: riders.size}); riders.clear(); }
            for (const [s, v] of [...shown]) { if (v.text) prompt.hideSlot(s); shown.delete(s); }
            next = now + .1; return;
        }
        health.update(now); const canExit = refreshExit();
        if (health.dead && seat.player && canExit) seat.exit('destroyed');
        // Fully enclosed: keep the pilot seated until a tested point becomes free.
        // Death and round callbacks still use the core's restoration without moving a live pawn.
        const humans = core.pawns();
        seat.update(canExit ? humans : humans.filter(p => p !== seat.player));
        const v = body.GetAbsVelocity(); speed = Math.hypot(v.x, v.y);
        // A wreck stays on its wheels too (the body has no tyre collision): it is a parked car without a driver.
        const fit = sense();
        park(now);
        if (!parked) { crash(v, now); drive(dt, fit); selfRight(now); }
        if (!health.dead) runover(now, dt);
        lastVelocity = body.GetAbsVelocity();
        spin = (spin + longitudinal / R / rad * dt) % 360; placeWheels();
        if (DOORS.length) passengers(dt);
        const ang = body.GetAbsAngles();
        peak = {bump: Math.max(peak.bump, ...travel), droop: Math.min(peak.droop, ...travel),
                pitch: Math.max(peak.pitch, Math.abs(ang.pitch)), roll: Math.max(peak.roll, Math.abs(ang.roll)),
                air: peak.air + (contacts ? 0 : dt), speed: Math.max(peak.speed, speed)};
        if (seat.player) {
            camera.update(seat.player); followView(seat.player, now, dt); placeDriver();
            motor(speed > 40 ? 'drive' : 'idle');
            if (hud.due('speed', now, .1)) {
                hud.text(seat.player, Math.round(speed * .09144) + ' ' + LABEL.kmh + '   HP ' + Math.ceil(health.hp) + '/' + config.health +
                    (crawl ? '   ' + LABEL.crawl : '') + '   ' + (canExit ? LABEL.exit : LABEL.blocked) + (!contacts ? '   ' + LABEL.air : ''));
                hud.flag(seat.player, 'Low', !!config.health && health.hp <= config.health / 3);
            }
        }
    } catch (e) {
        // Report (tests fail on it) but keep the vehicle alive: a stopped think froze prompts and riders for good.
        if (seat.player) seat.exit('error');
        throw Error('V_CAR_ERROR ' + config.name + ': ' + String(e) + (e?.stack ? ' ' + String(e.stack).slice(0, 300) : ''));
    } finally {
        Instance.SetNextThink(next);
    }
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
