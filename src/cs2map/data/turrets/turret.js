// @fragment turret_motion
// @requires core, hud, camera, seat, health, fire, turret_heat
// @imports Instance, CSInputs
// Stationary turret adapter. Angles are local to the authored base yaw: yaw is horizontal degrees,
// pitch is positive upward. Fire remains the shared engine; this adapter supplies its muzzle and view.
const C = config, E = C.entities, core = Core({tag: 'T_TURRET', name: C.name,
    state: 't9_turret_' + C.name + '_state'});
const {valid, clamp, basis} = core;
let body, platform, barrel, decoy, lastPose, lastTime = 0;
let turretYaw = 0, turretPitch = 0, muzzle = {x: 0, y: 0, z: 0};
const hud = Hud({core, name: E.hud, panel: 'control', variable: 'telemetry'});
const camera = Camera({core, follow: () => barrel, views: C.camera});
const heat = Heat({core, cool: C.heat.cool});
const engine = Fire({core, fire: C.fire, entities: {...E, fire: E.shot}, title: 'TURRET'});
let gunState = engine.newState();

function norm(a) { while (a > 180) a -= 360; while (a < -180) a += 360; return a; }
function pose() {
    if (!valid(body)) return;
    const a = body.GetAbsAngles(), worldYaw = a.yaw + turretYaw;
    const p = body.GetAbsOrigin();
    const pivot = {x: p.x, y: p.y, z: p.z + 34};
    const pb = basis({pitch: 0, yaw: worldYaw, roll: 0});
    const qb = basis({pitch: -turretPitch, yaw: worldYaw, roll: 0});
    const muzzleNow = {x: pivot.x + qb.f.x * 77 + pb.r.x * 0 + qb.u.x * 0,
                      y: pivot.y + qb.f.y * 77 + pb.r.y * 0 + qb.u.y * 0,
                      z: pivot.z + qb.f.z * 77 + qb.u.z * 0};
    const key = [p.x, p.y, p.z, a.yaw, turretYaw, turretPitch, muzzleNow.x, muzzleNow.y, muzzleNow.z];
    if (lastPose && key.every((v, i) => Math.abs(v - lastPose[i]) < .05)) return;
    lastPose = key; muzzle = muzzleNow;
    platform.Move({position: {x: p.x, y: p.y, z: p.z + 20}, angles: {pitch: 0, yaw: worldYaw, roll: 0}});
    barrel.Move({position: pivot, angles: {pitch: -turretPitch, yaw: worldYaw, roll: 0}});
    if (seat.player) {
        // The gunner stands at the grips behind the gun and walks round the base with it: the stand-in never
        // blocks the sight (where E was pressed may be in front of the barrel). The hidden pawn keeps it company;
        // leaving puts the player back where E was pressed.
        const grips = {x: p.x - pb.f.x * 52, y: p.y - pb.f.y * 52, z: p.z};
        if (valid(decoy)) decoy.Move({position: grips, angles: {pitch: 0, yaw: worldYaw, roll: 0}});
        seat.player.Teleport({position: grips, velocity: {x: 0, y: 0, z: 0}});
    }
    // Barrel is the hinge anchor; Camera applies the sight offset.
}
function aim(p, dt) {
    const eye = p.GetEyeAngles(), base = body.GetAbsAngles().yaw;
    const targetYaw = clamp(norm(eye.yaw - base), C.limits.yaw[0], C.limits.yaw[1]);
    const targetPitch = clamp(-eye.pitch, C.limits.pitch[0], C.limits.pitch[1]);
    const step = C.turn_rate * dt;
    turretYaw += clamp(targetYaw - turretYaw, -step, step);
    turretPitch += clamp(targetPitch - turretPitch, -step, step);
}
function setup() {
    body = Instance.FindEntityByName(E.body); platform = Instance.FindEntityByName(E.platform);
    barrel = Instance.FindEntityByName(E.barrel); decoy = Instance.FindEntityByName(E.pilot);
    if (!valid(body) || !valid(platform) || !valid(barrel) || !hud.find()) throw Error('missing turret entities');
    platform.SetParent(body); barrel.SetParent(platform); engine.setup(); health.restore(); heat.reset(); hud.hideAll();
    turretYaw = 0; turretPitch = 0; lastPose = null; gunState = engine.newState(); lastTime = Instance.GetGameTime(); pose();
    seat.clearEdges(); core.log('ready', {position: body.GetAbsOrigin(), hp: health.hp}); Instance.SetNextThink(lastTime + .016);
}
const health = Health({core, body: () => body, max: C.health, native: true, parts: () => [platform, barrel],
    effects: {smoke: E.smoke, fire: E.fire, boom: E.boom, boom_sound: E.boom_sound}, respawn: C.respawn,
    spawn: {origin: C.origin, yaw: C.yaw}, guard: w => !!seat.player && w?.GetOwner() === seat.player,
    onDestroy() { engine.cancel(gunState, seat.player ? source(seat.player) : undefined); if (seat.player) seat.exit('destroyed'); },
    onRestore() { turretYaw = 0; turretPitch = 0; lastPose = null; gunState = engine.newState(); pose(); }});
const seat = Seat({core, camera, hud, target: () => [body, platform], pawn: E.pawn, decoy: () => decoy, reach: C.reach,
    allow() { if (!valid(body)) return false; if (health.dead) return {reason: 'destroyed', respawn: health.respawnIn(Instance.GetGameTime())}; },
    onEnter(p) { camera.start(p); hud.show(p); pose(); },
    onExit(p, reason) { engine.cancel(gunState, p ? source(p) : undefined); hud.reset(); lastPose = null; pose(); },
    enterData: () => ({hp: health.hp, yaw: turretYaw, pitch: turretPitch, camera: camera.mode()}),
    exitData: () => ({hp: health.hp, yaw: turretYaw, pitch: turretPitch})});
function source(p) {
    const a = body.GetAbsAngles(), b = basis({pitch: -turretPitch, yaw: a.yaw + turretYaw, roll: 0});
    return {shooter: p, origin: muzzle, muzzle, angles: {pitch: -turretPitch, yaw: a.yaw + turretYaw, roll: 0}, inflictor: body, kick: (dp, dy) => {}};
}
// Seat protects the pilot's knife and grenades by returning abort:true. A Fire shot
// is deliberately tagged with this turret as inflictor, so merge a later abort:false
// only for that path; the pilot's other damage remains filtered.
core.on('OnModifyPlayerDamage', e => {
    if (seat.player && e.attacker === seat.player && e.inflictor === body) return {abort: false};
});
function update(now, dt) {
    health.update(now); seat.update(core.pawns()); heat.update(now, dt);
    if (!valid(body)) return;
    const p = seat.player;
    if (p) {
        camera.update(p); aim(p, dt); pose();
        const cells = gunState.cells;
        const ready = gunState.ready;
        const trigger = p.IsInputPressed(CSInputs.ATTACK) && !heat.locked;
        engine.step(gunState, {trigger, reload: false, src: source(p)}, now, dt);
        // Fire decrements cells exactly when a shot is emitted; this remains reliable
        // after the shared four-shot telemetry ring wraps.
        const fired = gunState.cells < cells || gunState.ready !== ready;
        if (fired) heat.add(C.heat.gain, now);
        if (hud.due('telemetry', now, .08)) {
            hud.text(p, 'HP ' + Math.ceil(health.hp) + '/' + C.health + '   YAW ' + Math.round(turretYaw) + '°   PITCH ' + Math.round(turretPitch) + '°   ' + heat.text());
            hud.flag(p, 'Low', health.hp <= C.health / 3); hud.flag(p, 'Overheat', heat.locked);
        }
    } else engine.cancel(gunState, undefined);
    engine.update(now); pose();
}
Instance.SetThink(() => { const now = Instance.GetGameTime(), dt = Math.min(.05, Math.max(0, now - lastTime)); lastTime = now;
    try { update(now, dt); } catch (e) { if (seat.player) seat.exit('error'); throw Error('T_TURRET_ERROR ' + C.name + ': ' + String(e)); }
    Instance.SetNextThink(now + .016); });
core.on('OnActivate', setup); core.on('OnRoundStart', setup);
core.state(() => ({active: !!seat.player, position: valid(body) ? body.GetAbsOrigin() : null, hp: health.hp,
    dead: health.dead, yaw: turretYaw, pitch: turretPitch, heat: heat.value, overheated: heat.locked,
    muzzle, player: valid(seat.player) ? seat.player.GetAbsOrigin() : null, anchor: seat.saved?.position,
    camera: camera.mode(), stats: health.stats, shots: engine.shots}));
