// @fragment fire
// @requires core
// @imports Instance, CSPlayerPawn, CSWeaponBase, CSDamageTypes
// fire: the shot engine (any weapon, turret). The native shot is not used: the script charges, traces,
// pierces walls, draws a beam of glowing rods or launches projectiles that explode, and applies the damage
// itself. Config: fire.py `validate`.
//   const fire = Fire({core, fire: config.fire, entities: config.entities, knockback: 0, title: "RAIL"});
//   fire.setup();                         // on map/round start: finds the rod and projectile pools
//   const st = fire.newState();           // one per shooter (player, turret)
//   fire.step(st, {trigger, reload, src}, now, dt);   // every tick; src says who shoots and from where
//   hud text: fire.text(st, now)
// src = {shooter: pawn (credit, ignored by traces), origin: vec, angles: QAngle, inflictor: entity,
//        muzzle?: vec (beam/projectile start, default: F.muzzle in front of origin), kick?: (dPitch, dYaw) => void,
//        aim?: () => {origin, angles} (homing "aim" target; default: the shooter's eyes)}
function Fire(cfg) {
    const {core} = cfg, F = cfg.fire, E = cfg.entities, P = F.projectile;
    const {valid, add, basis, rad, dist} = core;
    const shots = [];
    let rods = [], flashUntil = 0, flashAnchor, nextRod = 0;
    // Live beams: {rods: [indices], born}. A rod belongs to the newest beam that took it (ring over the pool).
    let beams = [], rodOwner = [];
    // Projectiles: a pool of models moved every tick and swept as spheres; `own` = our pool entities (never targets).
    let projs = [], lastFly = 0, booms = 0, own = new Set();
    const parked = {x: 0, y: 0, z: -8192};
    function setup() {
        flashUntil = 0;
        flashAnchor = E.flash_anchor ? Instance.FindEntityByName(E.flash_anchor) : undefined;
        if (valid(flashAnchor)) { flashAnchor.SetColor({r: 0, g: 0, b: 0, a: 0}); flashAnchor.Teleport({position: parked}); }
        rods = []; beams = []; rodOwner = []; nextRod = 0; own = new Set([flashAnchor].filter(valid));
        for (let i = 0; i < (E.beam_count || 0); i++) {
            const r = Instance.FindEntityByName(E.beam + i);
            if (valid(r)) { r.SetModelScale(F.beam.scale); tintRod(r, 1); rods.push(r); own.add(r); }
        }
        projs = []; lastFly = 0;
        for (let i = 0; i < (E.proj_count || 0); i++) {
            const m = Instance.FindEntityByName(E.proj + i);
            if (!valid(m)) continue;
            m.SetModelScale(P.scale); m.Teleport({position: parked});
            Instance.EntFireAtTarget({target: m, input: "Disable"});
            if (E.trail) core.fx(E.trail + i, "Stop");
            projs.push({ent: m, trail: E.trail ? E.trail + i : null, live: false, born: 0}); own.add(m);
        }
    }
    // Brightness k (0..1): colour and alpha go down together, so the rod fades on any material (additive:
    // colour; translucent: alpha) instead of vanishing at once.
    function tintRod(r, k) {
        const c = F.beam.color;
        r.SetColor({r: Math.round(c[0] * k), g: Math.round(c[1] * k), b: Math.round(c[2] * k), a: Math.round(255 * k)});
    }
    function fadeBeams(now) {
        if (!beams.length) return;
        const life = F.beam.life, fade = F.beam.fade || 0;
        beams = beams.filter(b => {
            const t = now - b.born, own = b.rods.filter(i => rodOwner[i] === b);
            if (t >= life + fade || !own.length) {
                for (const i of own) { Instance.EntFireAtTarget({target: rods[i], input: "Disable"}); rodOwner[i] = undefined; }
                return false;
            }
            if (t > life) {
                const k = 1 - (t - life) / fade, e = Math.pow(k, 1.25);   // additive: a near-linear dim reads as a fade
                if (Math.abs(e - b.k) > .03) { b.k = e; for (const i of own) tintRod(rods[i], Math.max(.02, e)); }
            }
            return true;
        });
    }
    function update(now) {
        fadeBeams(now);
        if (projs.length) fly(now);
        if (flashUntil && now >= flashUntil) {
            flashUntil = 0;
            if (valid(flashAnchor)) flashAnchor.Teleport({position: parked});
            if (E.flash) Instance.EntFireAtName({name: E.flash, input: "Stop"});
        }
    }
    function flash(src, b, now) {
        if (!F.flash) return;
        const position = src.muzzle || ahead(src, b);
        if (valid(flashAnchor)) flashAnchor.Teleport({position});
        if (E.flash) {
            const fx = Instance.FindEntityByName(E.flash);
            if (valid(fx)) fx.Teleport({position, angles: src.angles});
            Instance.EntFireAtName({name: E.flash, input: "Start"});
        }
        flashUntil = now + F.flash.life;
    }
    function push(player, from, force) {
        if (!force || !valid(player) || !player.IsAlive()) return;
        const p = player.GetAbsOrigin(), v = player.GetAbsVelocity(), dx = p.x - from.x, dy = p.y - from.y, l = Math.hypot(dx, dy) || 1;
        player.Teleport({velocity: {x: v.x + dx / l * force, y: v.y + dy / l * force, z: Math.max(v.z, 0) + force * .35}});
    }
    function spark(position) {
        if (!E.impact) return;
        const e = Instance.FindEntityByName(E.impact);
        if (!valid(e)) return;
        e.Teleport({position});
        Instance.EntFireAtName({name: E.impact, input: "Stop"});
        // Stop and Start must land in different ticks (1/64 s), or the client keeps the old effect.
        Instance.EntFireAtName({name: E.impact, input: "Start", delay: .04});
        Instance.EntFireAtName({name: E.impact, input: "Stop", delay: .3});
    }
    // The beam is a pool of glowing rods laid end to end along the shot (env_beam crashed CS2).
    function beam(start, end) {
        if (!rods.length) return;
        const dx = end.x - start.x, dy = end.y - start.y, dz = end.z - start.z, L = Math.hypot(dx, dy, dz);
        if (L < 1) return;
        const d = {x: dx / L, y: dy / L, z: dz / L}, seg = F.beam.segment;
        const angles = {pitch: -Math.atan2(d.z, Math.hypot(d.x, d.y)) / rad, yaw: Math.atan2(d.y, d.x) / rad, roll: 0};
        const n = Math.min(rods.length, Math.ceil(L / seg));
        const b = {rods: [], born: Instance.GetGameTime(), k: 1};
        for (let i = 0; i < n; i++) {
            // The last rod ends exactly at the hit point instead of poking through the wall.
            const at = i < n - 1 || L <= seg ? i * seg : L - seg;
            const j = (nextRod + i) % rods.length, r = rods[j];
            r.Teleport({position: add(start, d, at), angles});
            tintRod(r, 1);
            Instance.EntFireAtTarget({target: r, input: "Enable"});
            rodOwner[j] = b; b.rods.push(j);
        }
        nextRod = (nextRod + n) % rods.length;
        beams.push(b);
    }
    // Through the map: each segment is a TraceBullet up to the first wall (players and props on the
    // way are hit), then a TraceLine back from behind the wall finds where it ends and the shot goes on.
    function exitPoint(hit, dir, p) {
        for (const depth of [...new Set([16, 48, 128, F.thickness])].filter(d => d <= F.thickness).sort((a, b) => a - b)) {
            const from = add(hit, dir, depth);
            const back = Instance.TraceLine({start: from, end: add(hit, dir, .5), ignoreEntity: p, ignorePlayers: true});
            if (back.startedInSolid) continue;                 // still inside: the wall is thicker, look further
            // Exactly on the back face TraceLine can report neither a hit nor solid.
            // That sample is already outside; returning hit+1 would start inside the same wall again.
            return back.didHit ? back.end : from;
        }
        return null;                                           // thicker than F.thickness: the shot stops here
    }
    // Default muzzle for a shooter at the eye: ahead, right and down (F.muzzle; a rod at the eye floods the view).
    const M = F.muzzle || {forward: 40, right: 5, down: 5};
    const ahead = (src, b, k) => add(add(add(src.origin, b.f, k ?? M.forward), b.r, M.right), b.u, -M.down);
    const unit = v => { const l = Math.hypot(v.x, v.y, v.z) || 1; return {x: v.x / l, y: v.y / l, z: v.z / l}; };
    const look = d => ({pitch: -Math.atan2(d.z, Math.hypot(d.x, d.y)) / rad, yaw: Math.atan2(d.y, d.x) / rad, roll: 0});
    // Spread: a random direction inside the cone.
    function spreadDir(b) {
        const s = F.spread * rad * Math.sqrt(Math.random()), t = Math.random() * 2 * Math.PI;
        return unit(add(add(b.f, b.r, Math.tan(s) * Math.cos(t)), b.u, Math.tan(s) * Math.sin(t)));
    }
    // ---- projectiles: rockets, grenades, slugs ----
    function launch(src, dir, now) {
        if (!projs.length) return;
        // A free one, or the oldest in flight explodes early (the pool is the limit of rockets in the air).
        let free = projs.find(x => !x.live);
        if (!free) { free = projs.reduce((a, b) => a.born <= b.born ? a : b); explode(free, free.pos, now); }
        const p = src.shooter, start = src.muzzle || ahead(src, basis(src.angles));
        const aim = src.aim || (() => valid(p) && p.IsAlive() ? {origin: p.GetEyePosition(), angles: p.GetEyeAngles()} : null);
        Object.assign(free, {live: true, born: now, pos: start, vel: {x: dir.x * P.speed, y: dir.y * P.speed, z: dir.z * P.speed},
                             shooter: p, inflictor: src.inflictor, aim});
        free.ent.Teleport({position: start, angles: look(dir)});
        Instance.EntFireAtTarget({target: free.ent, input: "Enable"});
        if (free.trail) { core.fx(free.trail, "Stop"); core.fx(free.trail, "Start", undefined, .04); }
    }
    function homingTarget(x) {
        const H = P.homing;
        if (H.target === "aim") {
            const a = x.aim();
            if (!a) return null;
            const f = basis(a.angles).f, end = add(a.origin, f, 16384);
            const t = Instance.TraceLine({start: a.origin, end, ignoreEntity: [x.shooter, x.ent].filter(valid)});
            return t.didHit ? t.end : end;
        }
        // "enemy": the nearest living player of another team inside the cone ahead of the projectile.
        const speed = Math.hypot(x.vel.x, x.vel.y, x.vel.z) || 1, cone = Math.cos(H.cone * rad);
        let best = null, bestD = Infinity;
        for (const c of Instance.GetAllPlayerControllers()) {
            const pawn = c.GetPlayerPawn();
            if (!valid(pawn) || !pawn.IsAlive() || pawn === x.shooter) continue;
            if (valid(x.shooter) && x.shooter.GetTeamNumber() > 1 && pawn.GetTeamNumber() === x.shooter.GetTeamNumber()) continue;
            const at = add(pawn.GetAbsOrigin(), {x: 0, y: 0, z: 1}, 40), d = dist(at, x.pos);
            const cos = ((at.x - x.pos.x) * x.vel.x + (at.y - x.pos.y) * x.vel.y + (at.z - x.pos.z) * x.vel.z) / (d * speed || 1);
            if (cos >= cone && d < bestD) { best = at; bestD = d; }
        }
        return best;
    }
    // Turn the velocity toward the target by at most `turn` degrees per second (the speed stays).
    function steer(x, dt) {
        const target = homingTarget(x);
        if (!target) return;
        const speed = Math.hypot(x.vel.x, x.vel.y, x.vel.z), cur = unit(x.vel), want = unit(add(target, x.pos, -1));
        const angle = Math.acos(Math.max(-1, Math.min(1, cur.x * want.x + cur.y * want.y + cur.z * want.z)));
        if (angle < 1e-4) return;
        const k = Math.min(1, P.homing.turn * rad * dt / angle);
        const d = unit(add(cur, add(want, cur, -1), k));
        x.vel = {x: d.x * speed, y: d.y * speed, z: d.z * speed};
    }
    function fly(now) {
        const dt = Math.min(.05, Math.max(0, now - (lastFly || now))); lastFly = now;
        for (const x of projs) {
            if (!x.live || !dt) continue;
            if (now - x.born >= P.life) { explode(x, x.pos, now); continue; }
            if (P.homing && now - x.born >= P.homing.delay) steer(x, dt);
            const v = x.vel, g = P.gravity;
            const next = {x: x.pos.x + v.x * dt, y: x.pos.y + v.y * dt, z: x.pos.z + v.z * dt - .5 * g * dt * dt};
            v.z -= g * dt;
            const ignoreEntity = [x.ent, x.shooter, x.inflictor].filter(valid);
            const t = P.radius > 0 ? Instance.TraceSphere({start: x.pos, end: next, radius: P.radius, ignoreEntity})
                                   : Instance.TraceLine({start: x.pos, end: next, ignoreEntity});
            if (t.didHit || t.startedInSolid) {
                const at = t.didHit ? t.end : x.pos, e = t.hitEntity;
                if (P.hit && valid(e) && !e.IsWorld() && !own.has(e)) damage(x, e, P.hit, CSDamageTypes.BULLET, at, 0);
                explode(x, at, now);
                continue;
            }
            x.pos = next;
            x.ent.Teleport({position: next, angles: look(v)});
        }
    }
    function damage(x, e, amount, types, from, force) {
        const before = e.GetHealth(), w = x.inflictor;
        e.TakeDamage({damage: amount, damageTypes: types, attacker: valid(x.shooter) ? x.shooter : undefined,
                      inflictor: valid(x.ent) ? x.ent : undefined, weapon: w instanceof CSWeaponBase ? w : undefined});
        if (force) {
            if (e instanceof CSPlayerPawn) push(e, from, force);
            else { const v = e.GetAbsVelocity(), d = unit(add(e.GetAbsOrigin(), from, -1)); e.Teleport({velocity: add(v, d, force)}); }
        }
        const hit = {target: e.GetClassName(), name: e.GetEntityName(), damage: Math.round(amount), before, after: e.GetHealth()};
        core.log("hit", hit);
        return hit;
    }
    // Damageable things near a point: players (bots too) and props (physics, breakables, mechanics' bodies).
    const PROPS = ["prop_physics", "prop_physics_override", "prop_physics_multiplayer", "func_breakable", "func_physbox", "prop_dynamic"];
    function near(at, radius) {
        const out = [];
        for (const c of Instance.GetAllPlayerControllers()) {
            const pawn = c.GetPlayerPawn();
            if (valid(pawn) && pawn.IsAlive()) out.push({e: pawn, at: add(pawn.GetAbsOrigin(), {x: 0, y: 0, z: 1}, 36), reach: 16});
        }
        for (const cls of PROPS) for (const e of Instance.FindEntitiesByClass(cls)) {
            // Physics props are pushed even without health; a prop_dynamic counts only with health (targets, bodies).
            if (!valid(e) || own.has(e) || (cls === "prop_dynamic" && e.GetHealth() <= 0)) continue;
            out.push({e, at: e.GetAbsOrigin(), reach: 48});
        }
        return out.filter(t => dist(t.at, at) <= radius + t.reach);
    }
    function explode(x, at, now) {
        x.live = false;
        Instance.EntFireAtTarget({target: x.ent, input: "Disable"});
        x.ent.Teleport({position: parked});
        if (x.trail) core.fx(x.trail, "Stop");
        const X = P.explode, hits = [];
        if (X) {
            const k = booms++ % (E.boom_count || 1);
            if (E.boom) {
                const fx = Instance.FindEntityByName(E.boom + k);
                if (valid(fx)) { fx.Teleport({position: at}); core.fx(E.boom + k, "Stop"); core.fx(E.boom + k, "Start", undefined, .04); }
            }
            if (E.boom_sound) core.sound(E.boom_sound + k, at);
            for (const t of near(at, X.radius)) {
                const d = dist(t.at, at), f = Math.max(0, 1 - X.falloff * Math.min(1, d / Math.max(1, X.radius)));
                if (!f) continue;
                // Walls shield (the map's geometry); props and players in between do not.
                const line = Instance.TraceLine({start: at, end: t.at, ignoreEntity: [x.ent].filter(valid), ignorePlayers: true});
                const wall = line.didHit && (!valid(line.hitEntity) || line.hitEntity.IsWorld());
                if (wall && dist(line.end, t.at) > 24) continue;
                const amount = X.damage * f * (t.e === x.shooter ? X.self : 1);
                if (amount > .5 || X.knockback) hits.push(damage(x, t.e, Math.max(0, amount), CSDamageTypes.BLAST, at, X.knockback * f));
            }
        }
        shots.push({hits, walls: 0, end: at, explode: !!X}); if (shots.length > 4) shots.shift();
        core.log("explode", {at, hits: hits.length, age: Math.round((now - x.born) * 100) / 100});
    }
    function shoot(src, st, now) {
        const p = src.shooter, eye = src.origin, b = basis(src.angles), w = src.inflictor;
        if (P) {
            for (let pellet = 0; pellet < F.pellets; pellet++) launch(src, spreadDir(b), now);
            core.log("launch", {count: F.pellets});
            aftermath(src, st, now, b);
            return;
        }
        const totals = new Map();
        // TraceBullet needs a player as the shooter: a tower or a machine (no pawn) shoots with TraceLine.
        const lineTrace = F.trace_mode === "line" || !(p instanceof CSPlayerPawn);
        let last = add(eye, b.f, F.range), walls = 0;
        for (let pellet = 0; pellet < F.pellets; pellet++) {
            const dir = spreadDir(b);
            let start = eye, left = F.range, damage = F.damage, pierced = 0;
            const once = new Set();
            while (left > 1 && damage > .5) {
                const end = add(start, dir, left);
                // A gadget without an explicit fire config keeps its old single-hit line trace.
                // Both trace sources feed the same damage/effects/magazine engine.
                let res;
                if (lineTrace) {
                    const hit = Instance.TraceLine({start, end, ignoreEntity: [p, w].filter(valid), traceHitboxes: true});
                    res = hit.didHit ? [{hitEntity: hit.hitEntity, position: hit.end}] : [];
                    if (hit.didHit) last = hit.end;
                } else res = Instance.TraceBullet({start, end, shooter: p, damage, rangeModifier: 1, penetration: 1});
                let wall = null;
                for (const h of res) {
                    const e = h.hitEntity;
                    if (!valid(e) || e.IsWorld()) { wall = h.position; break; }
                    // The stock/custom inflictor is in front of a picked-up weapon and can be
                    // returned by TraceBullet as a world entity. It is never a valid target.
                    if (e === p || e === w || once.has(e)) continue;
                    once.add(e);
                    totals.set(e, (totals.get(e) || 0) + damage);
                    spark(h.position);
                }
                if (!wall) { if (!lineTrace || !res.length) last = end; break; }
                spark(wall);
                last = wall;
                if (pierced >= F.penetration) break;
                const out = exitPoint(wall, dir, p);
                if (!out) break;
                pierced++; walls++;
                damage *= (1 - F.wall_loss);
                left -= Math.hypot(out.x - start.x, out.y - start.y, out.z - start.z) + 1;
                start = add(out, dir, 1);
            }
        }
        const hits = [];
        for (const [e, damage] of totals) {
            if (!valid(e)) continue;
            const before = e.GetHealth();
            e.TakeDamage({damage, attacker: p, inflictor: w, weapon: w, damageTypes: CSDamageTypes.SHOCK});
            if (e instanceof CSPlayerPawn) push(e, eye, F.knockback ?? cfg.knockback ?? 0);
            const hit = {target: e.GetClassName(), name: e.GetEntityName(), damage: Math.round(damage), before, after: e.GetHealth()};
            hits.push(hit);
            core.log("hit", hit);
        }
        beam(src.muzzle || ahead(src, b), last);
        shots.push({hits, walls, end: last}); if (shots.length > 4) shots.shift();
        core.log("shot", {hits: hits.length, walls, end: last});
        aftermath(src, st, now, b);
    }
    // What every shot does at the shooter: sound, muzzle flash, kickback, recoil and shake.
    function aftermath(src, st, now, b) {
        const p = src.shooter, eye = src.origin;
        core.sound(E.fire, eye);
        flash(src, b, now);
        if (F.kickback && valid(p) && p.IsAlive()) {
            const v = p.GetAbsVelocity();
            p.Teleport({velocity: add(v, b.f, -F.kickback)});
        }
        if (typeof F.recoil === "object") {
            st.springs.push({at: now, pitch: F.recoil.kick, yaw: (Math.random() * 2 - 1) * F.recoil.side, done: 0});
        } else if (F.recoil) st.kick = {at: now, done: 0, pitch: F.recoil, yaw: (Math.random() * 2 - 1) * F.recoil * .25};
        // A fast second shot restarts the envelope while retaining the previous applied offset.
        // The next delta then removes it; replacing it with zero would leave permanent view drift.
        if (F.shake) st.shake = {at: now, pitch: st.shake?.pitch || 0, yaw: st.shake?.yaw || 0};
    }
    // Recoil as a view punch: a fast kick up (and a little sideways) that settles back to 20 %.
    // Deltas are added to the current view, so the player's own mouse movement is kept.
    function kickOffset(t, k) {
        if (t <= 0) return 0;
        return t < .05 ? k * t / .05 : k * (.2 + .8 * Math.exp(-(t - .05) / .09));
    }
    function recoil(src, st, now) {
        let dp = 0, dy = 0;
        // A damped spring has a fast first kick, crosses rest, and returns completely.
        // Accumulated deltas preserve the mouse input between ticks and successive shots.
        if (typeof F.recoil === "object") {
            st.springs = (st.springs || []).filter(k => {
                const t = now - k.at, end = F.recoil.return;
                const u = Math.max(0, t - .035);
                const cur = t >= end ? 0 : t < .035 ? t / .035 :
                    Math.exp(-u / (end * .18)) * Math.cos(u * 2 * Math.PI / (end * .65));
                dp += (cur - k.done) * k.pitch; dy += (cur - k.done) * k.yaw; k.done = cur;
                return t < end;
            });
        }
        if (st.shake) {
            const s = st.shake, t = now - s.at, f = F.shake, left = Math.max(0, 1 - t / f.duration);
            const a = f.amplitude * left * left, phase = t * f.frequency * Math.PI * 2;
            const pitch = a * Math.sin(phase), yaw = a * .7 * Math.sin(phase * .83);
            dp += pitch - s.pitch; dy += yaw - s.yaw; s.pitch = pitch; s.yaw = yaw;
            if (!left) st.shake = null;
        }
        if (dp || dy) applyKick(src, dp, dy);
        if (!st.kick) return;
        const t = now - st.kick.at, prev = st.kick.done, cur = t > .6 ? kickOffset(.6, 1) : kickOffset(t, 1);
        const d = cur - prev;
        st.kick.done = cur;
        applyKick(src, d * st.kick.pitch, d * st.kick.yaw);
        if (t > .6) st.kick = null;
    }
    function applyKick(src, dp, dy) {
        if (src.kick) src.kick(dp, dy);
        else if (valid(src.shooter)) {
            const a = src.shooter.GetEyeAngles();
            src.shooter.SetEyeAngles({pitch: core.clamp(a.pitch - dp, -89, 89), yaw: a.yaw + dy, roll: 0});
        }
    }
    // Charging glow in front of the barrel: the only part of the shot the holder sees on the stock hands.
    function glow(on) { if (E.glow) Instance.EntFireAtName({name: E.glow, input: on ? "Start" : "Stop"}); }
    function glowAt(src) {
        if (!E.glow) return;
        const g = Instance.FindEntityByName(E.glow);
        if (valid(g)) g.Teleport({position: src.muzzle || ahead(src, basis(src.angles), M.forward - 6)});
    }
    const newState = () => ({cells: F.magazine, charge: 0, ready: 0, reload: 0, rheld: false, theld: false, springs: [],
                             fired: false, burst: 0});
    // One shot from the magazine; an empty magazine reloads by itself.
    function fireOne(src, st, now) {
        shoot(src, st, now); st.cells--;
        if (!st.cells) { st.burst = 0; reload(st, src, now); }
    }
    function stopCharge(st) {
        if (!st.charge) return;
        st.charge = 0; glow(false);
        if (E.charge) Instance.EntFireAtName({name: E.charge, input: "StopSound"});
    }
    function reload(st, src, now) {
        stopCharge(st); st.reload = now + F.reload;
        core.sound(E.reload_sound, src.origin); core.log("reload", {});
    }
    function step(st, c, now, dt) {
        const src = c.src;
        update(now);
        if (st.reload && now >= st.reload) { st.reload = 0; st.cells = F.magazine; core.log("reloaded", {cells: st.cells}); }
        if (c.reload && !st.rheld && !st.reload && st.cells < F.magazine) reload(st, src, now);
        st.rheld = !!c.reload;
        if (c.trigger && !st.theld && (st.cells <= 0 || st.reload)) core.sound(E.empty_sound, src.origin);
        st.theld = !!c.trigger;
        recoil(src, st, now);
        if (!c.trigger) st.fired = false;
        // The rest of a burst fires by itself, one shot every burst.interval.
        if (st.burst > 0) {
            if (st.reload || st.cells <= 0) st.burst = 0;
            else if (now >= st.ready) { fireOne(src, st, now); st.burst--; st.ready = now + (st.burst > 0 ? F.burst.interval : F.cooldown); }
            return;
        }
        // auto: holding the trigger keeps firing; otherwise one shot (or burst) per press.
        if (c.trigger && (F.auto !== false || !st.fired) && !st.reload && st.cells > 0 && now >= st.ready) {
            if (!st.charge) { core.sound(E.charge, src.origin); glow(true); core.log("charge", {}); }
            st.charge += dt;
            glowAt(src);
            if (st.charge >= F.charge) {
                stopCharge(st); st.fired = true;
                st.burst = F.burst ? F.burst.count - 1 : 0;
                fireOne(src, st, now);
                st.ready = now + (st.burst > 0 ? F.burst.interval : F.cooldown);
            }
        } else if (!c.trigger && st.charge) {
            stopCharge(st);
        }
    }
    // Idle shooter (weapon holstered, turret empty): cancel a charge in progress.
    function cancel(st, src) {
        stopCharge(st); update(Instance.GetGameTime());
        if (src && (st.springs?.length || st.shake)) {
            let dp = 0, dy = 0;
            for (const k of st.springs || []) { dp -= k.done * k.pitch; dy -= k.done * k.yaw; }
            if (st.shake) { dp -= st.shake.pitch; dy -= st.shake.yaw; }
            applyKick(src, dp, dy);
        }
        st.springs = []; st.shake = null; st.theld = false;
    }
    function text(st, now, title) {
        const n = Math.round((F.charge ? Math.min(1, st.charge / F.charge) : 0) * 10);
        return (title || cfg.title || "") + "  " + "|".repeat(st.cells) + ".".repeat(F.magazine - st.cells) +
            (st.reload ? "   RELOADING" : now < st.ready ? "   COOLING" : F.charge ? "   CHARGE [" + "#".repeat(n) + "-".repeat(10 - n) + "]" : "");
    }
    return {setup, shoot, step, cancel, update, newState, text, push, spark, beam, shots, get rods() { return rods; },
            get beams() { return beams; }, get projectiles() { return projs.filter(x => x.live); }};
}
