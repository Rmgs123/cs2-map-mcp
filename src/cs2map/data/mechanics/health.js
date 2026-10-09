// @fragment health
// @requires core
// @imports Instance
// health: hit points, wreck and respawn of a body (a prop, a vehicle, a turret).
//   const health = Health({core, body: () => body, max: config.health, native: config.physics,
//       parts: () => rotors, effects: {smoke, fire, boom, boom_sound}, respawn: config.respawn,
//       spawn: {origin: config.origin, yaw: config.yaw}, guard: w => ..., onDestroy, onRestore});
// native:true — the body is a prop with HUGE engine health (never breaks by itself); every tick the drop of
//   GetHealth() is counted against `max` (bullets, grenades, knife, RemoveHealth). Props need prop_data
//   damage scales, otherwise bullets do nothing at all.
// native:false — no engine health; hurt(n) takes points (collisions, scripted damage).
// At half the points smoke starts, at 0 the body explodes, goes dark, tumbles under physics and burns until `respawn`.
function Health(cfg) {
    const {core} = cfg, NATIVE = 100000, BOOM_DELAY = .04;
    let hp = cfg.max, native = NATIVE, dead = false, deadAt = 0, knifeGuard = 0, wreckedForever = null;
    const FOREVER = cfg.respawn < 0;           // respawn "never": the wreck stays, even after a round restart
    const stats = {hits: 0, damage: 0, last: 0};
    const parts = () => (cfg.parts ? cfg.parts() : []).filter(core.valid);
    const E = cfg.effects || {};
    const tint = c => { const body = cfg.body(); if (core.valid(body)) body.SetColor(c); for (const r of parts()) r.SetColor(c); };
    function restore() {
        if (FOREVER && wreckedForever) { rewreck(); return; }
        hp = cfg.max; dead = false;
        stats.hits = 0; stats.damage = 0; stats.last = 0;
        const body = cfg.body();
        if (cfg.native && core.valid(body)) {
            if (cfg.max) { body.SetMaxHealth(NATIVE); body.SetHealth(NATIVE); }
            native = NATIVE;
            tint({r: 255, g: 255, b: 255, a: 255});
        }
        // The explosion is stopped too: a particle left "active" would ignore the next Start.
        core.fx(E.smoke, "Stop"); core.fx(E.fire, "Stop"); core.fx(E.boom, "Stop");
        if (cfg.onRestore) cfg.onRestore();
    }
    function destroy() {
        const body = cfg.body();
        dead = true; deadAt = Instance.GetGameTime(); hp = 0;
        if (cfg.onDestroy) cfg.onDestroy();
        const o = body.GetAbsOrigin();
        if (E.boom) {
            // Stop and Start in one tick never reach the client (the networked "active" flag does not change):
            // every explosion after the first one was lost. Stop now, move, Start two ticks later.
            const boom = Instance.FindEntityByName(E.boom);
            core.fx(E.boom, "Stop");
            if (core.valid(boom)) boom.Teleport({position: o});
            core.fx(E.boom, "Start", undefined, BOOM_DELAY);
        }
        core.fx(E.boom_sound, "StartSound"); core.fx(E.smoke, "Start"); core.fx(E.fire, "Start");
        tint({r: 60, g: 60, b: 60, a: 255});
        // The wreck keeps its momentum, tumbles and falls under real physics.
        if (cfg.native) {
            const v = body.GetAbsVelocity(), rnd = () => (Math.random() * 2 - 1);
            body.Teleport({velocity: {x: v.x, y: v.y, z: Math.max(v.z, 60)}, angularVelocity: {x: rnd() * 240, y: rnd() * 240, z: rnd() * 480}});
        }
        if (FOREVER) wreckedForever = {position: o, angles: body.GetAbsAngles()};
        core.log("destroyed", {position: o});
    }
    // A round restart re-creates the map's entities: put the wreck back where it lay, dark, without effects.
    function rewreck() {
        const body = cfg.body();
        dead = true; hp = 0;
        if (core.valid(body)) {
            body.Teleport({position: wreckedForever.position, angles: wreckedForever.angles, velocity: core.zero});
            tint({r: 60, g: 60, b: 60, a: 255});
        }
        if (cfg.onDestroy) cfg.onDestroy();
        core.log("wreck_kept", {position: wreckedForever.position});
    }
    function respawn() {
        const body = cfg.body(), s = cfg.spawn;
        body.Teleport({position: core.vec(s.origin), angles: {pitch: 0, yaw: s.yaw, roll: 0}, velocity: core.zero, angularVelocity: core.zero});
        restore();
        core.log("respawn", {position: core.vec(s.origin), hp});
    }
    // Scripted damage (a crash, a turret overheating its own gun): points straight off `hp`.
    function hurt(amount, why) {
        if (dead || !cfg.max) return;
        hp = Math.max(0, hp - amount); stats.damage += amount; stats.last = amount;
        core.log("damage", {damage: amount, hp, ...(why ? {why} : {})});
        if (hp <= 0) destroy();
        else if (hp <= cfg.max / 2) core.fx(E.smoke, "Start");
    }
    // Every tick: engine-side damage and the respawn timer.
    function update(now) {
        const body = cfg.body();
        if (cfg.native && cfg.max && !dead && core.valid(body)) {
            const h = body.GetHealth();
            if (h < native && now < knifeGuard) {
                // A swing of the pilot's own knife (traced from the camera at the object) is refunded.
                body.SetHealth(native); core.log("refund", {damage: native - h});
            } else {
                if (h < native) {
                    const damage = native - h;
                    native = h; hp = Math.max(0, hp - damage); stats.damage += damage; stats.last = damage;
                    core.log("damage", {damage, hp});
                    if (hp <= 0) destroy();
                    else if (hp <= cfg.max / 2) core.fx(E.smoke, "Start");
                }
                if (!dead && native < NATIVE / 2) { body.SetHealth(NATIVE); native = NATIVE; }
            }
        }
        if (dead && cfg.respawn > 0 && now - deadAt >= cfg.respawn) respawn();
    }
    // A swing of the seated player's own knife is traced from the camera at the object: refund that damage.
    core.on("OnKnifeAttack", ({weapon}) => { if (cfg.guard && cfg.guard(weapon)) knifeGuard = Instance.GetGameTime() + .4; });
    core.on("OnBulletImpact", ({position, hitEntity}) => {
        const body = cfg.body();
        if (core.valid(body) && hitEntity === body) { stats.hits++; core.log("hit", {position}); }
    });
    return {
        get hp() { return hp; }, get dead() { return dead; }, get deadAt() { return deadAt; }, stats,
        restore, destroy, respawn, hurt, update,
        // seconds until the wreck respawns (null = never)
        respawnIn(now) { return cfg.respawn > 0 ? Math.max(0, cfg.respawn - (now - deadAt)) : null; },
    };
}
