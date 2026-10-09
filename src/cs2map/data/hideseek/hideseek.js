// @fragment hideseek_motion
// @requires core, hud
// @imports Instance, CSPlayerPawn, CSMoveType, CSGearSlot, CSInputs
// Hide and seek (hideseek.py, map_edit add_hideseek). Hiders are CT, seekers T: CS2 itself ends the round
// (all hiders dead -> T win; time out -> CT win, mp_default_team_winner_no_objective 3).
// Round: hide phase (seekers frozen and blind), then the hunt; optional reveal pulses and prop disguises.
const C = config, E = config.entities;
const core = Core({tag: "H10_HIDESEEK", name: C.name, state: C.state});
const hud = Hud({core, name: E.hud, panel: "hideseek", variable: "status"});
const HIDER = 3, SEEKER = 2;
const KEYS = {inspect: CSInputs.LOOK_AT_WEAPON, attack2: CSInputs.ATTACK2, reload: CSInputs.RELOAD, use: CSInputs.USE};
let started = 0, phase = "idle", last = 0, revealUntil = 0, nextReveal = 0, nextBlind = 0;
const disguise = new Map();          // slot -> {prop index, model index}

function team(p) { return p.GetTeamNumber(); }
function all() { return core.pawns({bots: true}); }
// Blind = a short black fade held 1.2 s, renewed every 0.5 s while the seekers wait: it ends by itself when the
// renewals stop. (A "stay out" fade for one player cannot be lifted in CS2: a player-only "fade from" does nothing.)
function blind(p) { if (E.fade) Instance.EntFireAtName({name: E.fade, input: "Fade", activator: p}); }
function freeze(p, on) { p.SetMoveType(on ? CSMoveType.NONE : CSMoveType.WALK); }

function setup() {
    started = Instance.GetGameTime();
    phase = "hide";
    disguise.clear();
    hud.find(); hud.hideAll();
    Instance.ServerCommand("mp_default_team_winner_no_objective 3");
    Instance.SetRoundRemainingTime(C.hide + C.seek);
    nextReveal = started + C.hide + (C.reveal || 0);
    park();
    for (const p of all()) arm(p);
    last = started;
    Instance.SetNextThink(started);
    core.log("round", {hide: C.hide, seek: C.seek});
}
function arm(p) {
    if (team(p) === SEEKER) { freeze(p, true); blind(p); }
    if (team(p) === HIDER && C.hider_weapons === "knife") {
        for (const slot of [CSGearSlot.RIFLE, CSGearSlot.PISTOL]) { const w = p.FindWeaponBySlot(slot); if (core.valid(w)) p.DestroyWeapon(w); }
        const k = p.FindWeaponBySlot(CSGearSlot.KNIFE); if (core.valid(k)) p.SwitchToWeapon(k);
    }
    if (team(p) === HIDER && C.disguise) wear(p, 0);
}
function release() {
    phase = "seek";
    for (const p of all()) if (team(p) === SEEKER) freeze(p, false);
    core.log("seek", {hiders: hiders().length});
}
function hiders() { return all().filter(p => team(p) === HIDER); }

// Prop disguise: the hider's body fades, a prop from the list follows it; F switches the prop.
function wear(p, model) {
    const slot = core.slot(p);
    let d = disguise.get(slot);
    if (!d) {
        const used = new Set([...disguise.values()].map(x => x.prop));
        const free = E.props.findIndex((_, i) => !used.has(i));
        if (free < 0) return;
        d = {prop: free, model: 0};
        disguise.set(slot, d);
    }
    d.model = model % C.disguise.models.length;
    const prop = Instance.FindEntityByName(E.props[d.prop]);
    if (!core.valid(prop)) return;
    prop.SetModel(C.disguise.models[d.model]);
    alpha(prop, 255);
    alpha(p, 0);
    for (const slot of [CSGearSlot.KNIFE, CSGearSlot.PISTOL, CSGearSlot.RIFLE]) { const w = p.FindWeaponBySlot(slot); if (core.valid(w)) alpha(w, 0); }
    core.log("disguise", {slot, model: C.disguise.models[d.model]});
}
function alpha(e, a) { const c = e.GetColor(); e.SetColor({r: c.r, g: c.g, b: c.b, a}); }
// Parked helpers wait inside the map, transparent (CS2 never draws entities that spawned outside the world).
function stow(e) { if (!core.valid(e)) return; if (e.IsGlowing()) e.Unglow(); alpha(e, 0); e.Teleport({position: core.vec(E.park)}); }
function park() { for (const n of [...E.props, ...E.markers]) stow(Instance.FindEntityByName(n)); }

Instance.SetThink(() => {
    const now = Instance.GetGameTime();
    last = now;
    if (phase === "idle") { Instance.SetNextThink(now); return; }
    if (phase === "hide" && now - started >= C.hide) release();
    const left = Math.max(0, Math.ceil(C.hide + C.seek - (now - started)));
    const hide = Math.max(0, Math.ceil(C.hide - (now - started)));
    const hs = hiders();
    // Spawns and round restarts come in any order: the rules are enforced every tick, not only at round start.
    for (const p of all()) {
        if (phase === "hide" && team(p) === SEEKER && p.GetMoveType() !== CSMoveType.NONE) freeze(p, true);
        if (phase === "hide" && team(p) === SEEKER && C.blind && now >= nextBlind) blind(p);
        if (team(p) !== HIDER) continue;
        if (C.hider_weapons === "knife")
            for (const slot of [CSGearSlot.RIFLE, CSGearSlot.PISTOL]) { const w = p.FindWeaponBySlot(slot); if (core.valid(w)) p.DestroyWeapon(w); }
        if (C.disguise && !disguise.has(core.slot(p))) wear(p, 0);
        else if (C.disguise && p.GetColor().a !== 0) alpha(p, 0);
    }
    if (phase === "hide" && now >= nextBlind) nextBlind = now + 0.5;
    // disguises follow their hiders; F changes the prop
    for (const p of hs) {
        const d = disguise.get(core.slot(p));
        if (!d) continue;
        const prop = Instance.FindEntityByName(E.props[d.prop]);
        if (core.valid(prop)) prop.Teleport({position: p.GetAbsOrigin(), angles: {pitch: 0, yaw: p.GetAbsAngles().yaw, roll: 0}});
        if (C.disguise && p.WasInputJustPressed(KEYS[C.disguise.key])) wear(p, d.model + 1);
    }
    // reveal pulses: every C.reveal seconds of the hunt the hiders glow for 1.5 s
    if (C.reveal && phase === "seek" && now >= nextReveal) { revealUntil = now + 1.5; nextReveal = now + C.reveal; core.log("reveal", {hiders: hs.length}); }
    const marks = E.markers.map(n => Instance.FindEntityByName(n)).filter(core.valid);
    marks.forEach((m, i) => {
        const h = now < revealUntil ? hs[i] : null;
        if (h) {
            m.Teleport({position: h.GetAbsOrigin(), angles: {pitch: 0, yaw: h.GetAbsAngles().yaw, roll: 0}});
            if (!m.IsGlowing()) { const c = m.GetColor(); m.SetColor({r: c.r, g: c.g, b: c.b, a: 0}); m.Glow({r: 255, g: 210, b: 60}); }
        } else if (m.IsGlowing()) stow(m);
    });
    for (const p of core.pawns()) {
        if (!hud.due(core.slot(p), now, 0.25)) continue;
        const role = team(p) === HIDER ? "HIDE" : team(p) === SEEKER ? "SEEK" : "";
        const text = phase === "hide"
            ? (team(p) === SEEKER ? `WAIT  ${hide} s  ·  hiders are hiding` : `HIDE!  ${hide} s`)
            : `${role}  ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}  ·  hiders left ${hs.length}`;
        hud.put(p, C.title + "   " + text);
    }
    Instance.SetNextThink(now);
});

core.on("OnRoundStart", setup);
core.on("OnActivate", setup);
core.on("OnPlayerReset", e => { if (phase !== "idle" && core.valid(e.player)) arm(e.player); });
core.on("OnPlayerKill", e => {
    const p = e.player;
    if (!core.valid(p)) return;
    const d = disguise.get(core.slot(p));
    if (d) { stow(Instance.FindEntityByName(E.props[d.prop])); disguise.delete(core.slot(p)); }
    if (team(p) === HIDER) core.log("found", {left: hiders().length});
});
core.state(() => ({phase, hide: C.hide, seek: C.seek, elapsed: Math.round(Instance.GetGameTime() - started),
                   hiders: hiders().length, disguised: disguise.size}));
