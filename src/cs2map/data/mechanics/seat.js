// @fragment seat
// @requires core
// @imports Instance, CSMoveType, CSInputs, CSGearSlot
// seat: a player takes control of an object (drone, car, turret) and gives it back.
//   const seat = Seat({core, camera, hud, target: () => remote, pawn: E.pawn, decoy: () => decoy, reach: 96,
//                      allow: p => ..., onEnter: (p, saved) => ..., onExit: (p, reason, saved) => ...});
// Enter: E (rising edge) while the eye ray hits `target` (or a part parented to it) within `reach`. The player is saved
// (MoveType, position, yaw/pitch, weapon, name), frozen (MoveType NONE), holds only the knife, is renamed
// (the object's damage filter refuses that name: the pilot cannot hurt their own object), hidden and
// replaced by a still decoy of the same model. CS2 turns a pawn's body to its view in every camera mode and
// Teleport of the body's angles snaps the view, so the real body stays parked and hidden.
// Exit: E, death, respawn, round restart, disconnect, `Exit` script input, or exit() from the mechanic.
// Restore: MoveType, position, ONLY yaw in Teleport + SetEyeAngles for the pitch (a pitched pawn tilts for good).
function Seat(cfg) {
    const {core} = cfg, useHeld = new Map(), zero = core.zero;
    let player, saved, hidden;
    const targets = () => [].concat(cfg.target ? cfg.target() : []).filter(core.valid);
    // Hide the real body and its weapons; a decoy keeps the pilot visible, still and in place.
    function showPilot(p, show) {
        const decoy = cfg.decoy ? cfg.decoy() : undefined;
        if (show) {
            if (core.valid(decoy)) Instance.EntFireAtTarget({target: decoy, input: "Disable"});
            for (const e of hidden || []) if (core.valid(e)) e.SetColor({...e.__color, a: e.__color.a ?? 255});
            hidden = undefined;
            return;
        }
        if (!core.valid(p)) return;
        hidden = [p, ...[0, 1, 2, 3, 4, 5].map(n => p.FindWeaponBySlot(n))].filter(core.valid);
        for (const e of hidden) { e.__color = e.GetColor(); e.SetColor({...e.__color, a: 0}); }
        if (core.valid(decoy)) {
            decoy.SetModel(p.GetModelName());
            decoy.Teleport({position: saved.position, angles: {pitch: 0, yaw: saved.angles.yaw, roll: 0}});
            Instance.EntFireAtTarget({target: decoy, input: "Enable"});
        }
    }
    // The way out: the saved spot, or the nearest free spot around it and the object (a turret that turned, a car
    // that moved may stand where the player stood). A 32x32x72 hull with a floor under it; the object is solid.
    const HULL = {mins: {x: -16, y: -16, z: 0}, maxs: {x: 16, y: 16, z: 72}};
    // Only the player, and only while not parented: ignoring an entity parented to a vehicle (the seated pilot, the
    // decoy) makes CS2 skip the whole hierarchy, so a spot inside the vehicle's body read as free. Decoys are never solid.
    const ignore = p => [p].filter(e => core.valid(e) && !core.valid(e.GetParent?.()));
    function free(at, p) {
        const t = core.boxTrace({start: at, end: at, ...HULL, ignoreEntity: ignore(p)});
        return !t.startedInSolid && !t.didHit;
    }
    function ground(at, p) {
        const t = core.boxTrace({start: {...at, z: at.z + 48}, end: {...at, z: at.z - 96}, ...HULL, ignoreEntity: ignore(p)});
        if (t.startedInSolid || !t.didHit || (t.normal && t.normal.z < .65)) return null;
        const spot = {...t.end, z: t.end.z + 1};
        return free(spot, p) ? spot : null;
    }
    function exitSpot(p) {
        const home = saved.position;
        if (free(home, p)) return home;
        let best = null, bestD = Infinity;
        for (const c of [home, ...targets().map(t => t.GetAbsOrigin())])
            for (const r of [40, 64, 96, 128, 176]) for (let i = 0; i < 16; i++) {
                const at = {x: c.x + Math.cos(i * Math.PI / 8) * r, y: c.y + Math.sin(i * Math.PI / 8) * r, z: home.z};
                if (core.dist(at, home) >= bestD) continue;
                const spot = ground(at, p);
                if (spot) { best = spot; bestD = core.dist(spot, home); }
            }
        return best;
    }
    // The pilot holds only the knife (if any): no other weapon can be drawn or fired while seated.
    function holster() {
        if (!core.valid(player)) return;
        const knife = player.FindWeaponBySlot(CSGearSlot.KNIFE), active = player.GetActiveWeapon();
        if (core.valid(knife) && active !== knife) player.SwitchToWeapon(knife);
    }
    function enter(p) {
        if (player || !p.IsAlive() || p.IsNoclipping() || !targets().length) return;
        // allow(): undefined/true = yes; false = silently no; string or {reason, ...} = refused with a log line.
        const verdict = cfg.allow ? cfg.allow(p) : true;
        if (verdict === false) return;
        if (verdict !== undefined && verdict !== true) { core.log("refused", typeof verdict === "string" ? {reason: verdict} : verdict); return; }
        if (cfg.camera && !cfg.camera.free(p)) return;
        saved = {position: {...p.GetAbsOrigin()}, angles: {...p.GetEyeAngles(), roll: 0}, moveType: p.GetMoveType(), slot: core.slot(p),
                 weapon: p.GetActiveWeapon(), name: p.GetEntityName()};
        if (cfg.pawn) p.SetEntityName(cfg.pawn);
        player = p;
        try {
            p.SetMoveType(CSMoveType.NONE);
            p.Teleport({velocity: zero});
            holster();
            showPilot(p, false);
            if (cfg.onEnter) cfg.onEnter(p, saved);
            core.log("enter", {slot: saved.slot, anchor: saved.position, ...(cfg.enterData ? cfg.enterData(p) : {})});
        } catch (e) { exit("error"); throw e; }
    }
    function exit(reason) {
        const p = player;
        player = undefined;
        try {
            if (cfg.camera) cfg.camera.stop();
            if (cfg.hud && saved) cfg.hud.hideSlot(saved.slot);
            showPilot(p, true);
            if (core.valid(p) && saved && cfg.pawn) p.SetEntityName(saved.name || "");
            if (core.valid(p) && p.IsAlive()) {
                // A dead pilot keeps the engine's death camera: only a living pawn is restored.
                // A pawn a mechanic parented (a car's pilot) takes the world angles below only once unparented:
                // SetEyeAngles is relative to the parent. onExit may parent it back (a lift it stood on).
                if (p.GetParent?.()) p.SetParent(undefined);
                p.SetMoveType(saved.moveType);
                if (!["reset", "round", "disconnect"].includes(reason)) {
                    // A pawn's own angles carry yaw only: a pitch here tilts the body (and its eye
                    // offset (0,0,64)) for good, so the view floats off the body. Pitch goes to the eyes.
                    const spot = exitSpot(p);
                    if (!spot) core.log("exit_blocked", {at: saved.position});
                    else if (spot !== saved.position) core.log("exit_moved", {from: saved.position, to: spot});
                    p.Teleport({position: spot || saved.position, angles: {pitch: 0, yaw: saved.angles.yaw, roll: 0}, velocity: zero});
                    p.SetEyeAngles({pitch: saved.angles.pitch, yaw: saved.angles.yaw, roll: 0});
                }
                if (core.valid(saved.weapon) && saved.weapon.GetOwner() === p) p.SwitchToWeapon(saved.weapon);
            }
        } finally {
            if (cfg.onExit) cfg.onExit(p, reason, saved);
            core.log("exit", {reason, slot: core.valid(p) ? core.slot(p) : null, mode: core.valid(p) ? p.GetMoveType() : null,
                              ...(cfg.exitData ? cfg.exitData() : {})});
        }
    }
    // The use key. Call every tick with the human pawns; also notices the pilot's death.
    function update(humans) {
        if (player && (!core.valid(player) || !player.IsAlive())) exit("death");
        for (const p of humans) {
            const s = core.slot(p), pressed = p.IsInputPressed(CSInputs.USE), edge = pressed && !useHeld.get(s);
            useHeld.set(s, pressed);
            if (!edge) continue;
            if (p === player) { exit("use"); continue; }
            if (player) continue;
            const t = targets(), hit = core.eyeRay(p, cfg.reach || 96).hitEntity;
            // Parts parented to the object count too (a bus window, a turret shield).
            if (t.length && core.valid(hit) && (t.includes(hit) || t.includes(hit.GetParent?.()))) enter(p);
        }
        if (player) holster();
    }
    core.on("OnBeginRoundRestart", () => { if (player) exit("round"); });
    core.on("OnPlayerReset", ({player: p}) => { if (p === player) exit("reset"); });
    core.on("OnPlayerKill", ({player: p}) => { if (p === player) exit("death"); });
    core.on("OnPlayerDisconnect", ({playerSlot}) => { if (player && core.slot(player) === playerSlot) exit("disconnect"); });
    core.input("Exit", () => { if (player) exit("input"); });
    // Nothing the pilot's parked body does (a knife swing, a grenade already in the air) hurts anyone.
    core.on("OnModifyPlayerDamage", ({attacker}) => { if (player && attacker === player) return {abort: true}; });
    return {
        get player() { return player; },
        get saved() { return saved; },
        enter, exit, update, holster,
        clearEdges() { useHeld.clear(); },
    };
}
