// @fragment core
// @imports Instance, CSPlayerPawn
// core: the base every mechanic stands on. One instance per script:
//   const core = Core({tag: "D_CONTROL", name: config.name, state: "d8_control_x_state"});
// It owns the log (with a ring of the last events that rides in every state report), helpers
// (valid, vectors, angle basis), named effects and sounds, ONE dispatcher per engine event
// (fragments and the mechanic share it through core.on) and the telemetry command <state>.
function Core(opts) {
    const rad = Math.PI / 180, zero = {x: 0, y: 0, z: 0};
    const valid = e => !!e && e.IsValid();
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    const vec = a => ({x: a[0], y: a[1], z: a[2]});
    const add = (a, b, k = 1) => ({x: a.x + b.x * k, y: a.y + b.y * k, z: a.z + b.z * k});
    const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
    // The last events ride along in every state report: console lines printed between commands are lost.
    const recent = [];
    const log = (event, data = {}) => {
        recent.push((event + " " + JSON.stringify(data)).replace(/"/g, "'").slice(0, 160));
        if (recent.length > 8) recent.shift();
        Instance.Msg(opts.tag + " " + opts.name + " " + event + " " + JSON.stringify(data));
    };
    const slot = p => p.GetPlayerController()?.GetPlayerSlot();
    // Source angle convention: forward/left/up basis of a QAngle (r = -l for convenience).
    function basis(a) {
        const sp = Math.sin(a.pitch * rad), cp = Math.cos(a.pitch * rad), sy = Math.sin(a.yaw * rad), cy = Math.cos(a.yaw * rad);
        const sr = Math.sin((a.roll || 0) * rad), cr = Math.cos((a.roll || 0) * rad);
        const l = {x: sr * sp * cy - cr * sy, y: sr * sp * sy + cr * cy, z: sr * cp};
        return {f: {x: cp * cy, y: cp * sy, z: -sp}, l, r: {x: -l.x, y: -l.y, z: -l.z},
                u: {x: cr * sp * cy + sr * sy, y: cr * sp * sy - sr * cy, z: cr * cp}};
    }
    function anglesOf(f, l, u) {
        const xy = Math.hypot(f.x, f.y);
        return {pitch: Math.atan2(-f.z, xy) / rad, yaw: Math.atan2(f.y, f.x) / rad, roll: Math.atan2(l.z, u.z) / rad};
    }
    // Pawns of the connected players. Defaults: humans only, alive only.
    function pawns(o = {}) {
        const out = [];
        for (const c of Instance.GetAllPlayerControllers()) {
            if (!c.IsValid() || (c.IsBot() && !o.bots)) continue;
            const p = c.GetPlayerPawn();
            if (!(p instanceof CSPlayerPawn) || !p.IsValid() || (o.dead !== true && !p.IsAlive())) continue;
            out.push(p);
        }
        return out;
    }
    // Where the player is looking: a trace along the view (E-to-use rays, aim checks).
    function eyeRay(p, distance = 96) {
        const eye = p.GetEyePosition(), a = p.GetEyeAngles(), cp = Math.cos(a.pitch * rad);
        const end = {x: eye.x + Math.cos(a.yaw * rad) * cp * distance, y: eye.y + Math.sin(a.yaw * rad) * cp * distance,
                     z: eye.z - Math.sin(a.pitch * rad) * distance};
        return Instance.TraceLine({start: eye, end, ignoreEntity: p});
    }
    // Named entity inputs: fx("smoke_name", "Start"). A null name is a no-op (optional effect).
    function fx(name, input, value, delay) {
        if (!name) return;
        const c = {name, input};
        if (value !== undefined) c.value = value;
        if (delay) c.delay = delay;
        Instance.EntFireAtName(c);
    }
    // Sound by event entity name(s): optionally moved to `at` first, then started.
    function sound(names, at) {
        for (const name of [].concat(names || [])) {
            const s = Instance.FindEntityByName(name);
            if (valid(s) && at) s.Teleport({position: at});
            Instance.EntFireAtName({name, input: "StartSound"});
        }
    }
    // One engine callback per event, many listeners. OnModifyPlayerDamage results are merged.
    const handlers = {};
    function on(event, fn) {
        if (!handlers[event]) {
            const list = handlers[event] = [];
            if (event === "OnModifyPlayerDamage") {
                Instance[event](e => { let out; for (const f of list) { const r = f(e); if (r) out = out ? Object.assign(out, r) : r; } return out; });
            } else Instance[event](e => { for (const f of list) f(e); });
        }
        handlers[event].push(fn);
    }
    // Cheat command <name> <token>; the handler gets the trimmed token.
    function command(name, fn) { Instance.RegisterCheatCommand(name, token => fn((token || "").trim())); }
    function humans() {
        return Instance.GetAllPlayerControllers().filter(c => c.IsValid() && !c.IsBot()).map(c => c.GetPlayerPawn()).filter(valid).map(h => ({
            slot: slot(h), origin: h.GetAbsOrigin(), eye: h.GetEyePosition(), body: h.GetAbsAngles(), view: h.GetEyeAngles(),
            alpha: h.GetColor().a, move: h.GetMoveType(), camera: h.GetCustomCamera().GetMode(), weapon: h.GetActiveWeapon()?.GetClassName()}));
    }
    // Telemetry: `<opts.state> <token>` prints one "state" line: your fields + recent events + human pawns.
    function state(extra) {
        command(opts.state, token => log("state", {token, recent: recent.filter(x => !x.startsWith("state ")), ...extra(token), humans: humans()}));
    }
    return {rad, zero, valid, clamp, vec, add, dist, log, recent, slot, basis, anglesOf, pawns, eyeRay, fx, sound, on, command, humans, state,
            input: (name, fn) => Instance.OnScriptInput(name, fn)};
}
