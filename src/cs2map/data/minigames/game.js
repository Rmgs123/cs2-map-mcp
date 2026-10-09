// @fragment minigame_game
// @requires core, hud, health
// @imports Instance, CSInputs
function MiniGame(cfg) {
    const core = Core({tag:"M_MINIGAME", name:cfg.name, state:cfg.state});
    const E = cfg.entities, kind = cfg.kind;
    const hud = Hud({core, name:E.hud, panel:E.hud+"_panel", variable:E.hud+"_status"});
    const useHeld = new Map(), visible = new Map(), switchAt = new Map(), native = new Map();
    let start, things = [], active, started = 0, score = 0, next = 0, record = 0;
    let penalty = 0, skipZone = -1, finishedSlot, finishedUntil = 0, lastTime = 0, seed = 9;
    const now = () => Instance.GetGameTime();
    const random = () => {seed = (seed*1664525+1013904223)>>>0; return seed/4294967296;};
    const delay = () => cfg.popup[0]+random()*(cfg.popup[1]-cfg.popup[0]);
    const elapsed = t => Math.max(0,t-started)+penalty;
    function vehicle() {
        if (!cfg.vehicle) return undefined;
        for (const n of [cfg.vehicle, cfg.vehicle+"_body", cfg.vehicle+"_vehicle", "d8_"+cfg.vehicle+"_body", "v9_"+cfg.vehicle+"_body"]) {
            const e = Instance.FindEntityByName(n); if (core.valid(e)) return e;
        }
    }
    function point(e, height=0) {return core.add(e.GetAbsOrigin(),{x:0,y:0,z:height});}
    function audio(key, at) {core.sound(E.sounds[key],at);}
    function tilt(i, up) {core.fx(E.hinges[i],"SetMotorTargetAngle",up ? 0 : 90);}
    function ringColors() {
        for (let i=0;i<things.length;i++) if(core.valid(things[i])) things[i].SetColor(i<next ? {r:70,g:255,b:100,a:255} : i===next ? {r:255,g:225,b:60,a:255} : {r:70,g:100,b:150,a:255});
    }
    function reset(reason) {
        // The per-player Hidden=false override outranks hideAll's global value.
        hud.reset(); hud.hideAll(); active=undefined; next=0; score=0; penalty=0; skipZone=-1; finishedSlot=undefined;
        visible.clear(); switchAt.clear();
        for (let i=0;i<things.length;i++) {
            const e=things[i]; if (!core.valid(e)) continue;
            e.SetColor({r:255,g:255,b:255,a:255});
            if (kind==="gallery") {native.get(e)?.restore(); tilt(i,false);}
        }
        if (kind==="race") ringColors();
        core.log("reset",{reason});
    }
    function finish(reason,t) {
        if (!active) return;
        const seconds=elapsed(t), result=kind==="race" ? seconds : score;
        const success=kind!=="race" || reason==="finish";
        if (success && (record===0 || (kind==="race" ? result<record : result>record))) record=result;
        const label=kind==="race" ? (success ? `FINISH ${seconds.toFixed(2)}s | BEST ${record.toFixed(2)}s` : `TIME OUT | CP ${next}/${things.length}`) : `DONE ${score} | BEST ${record}`;
        hud.put(active,label); finishedSlot=core.slot(active); finishedUntil=t+8;
        audio("finish",active.GetAbsOrigin()); core.log("finish",{reason,score,seconds:Number(seconds.toFixed(2)),record});
        active=undefined;
    }
    function startGame(p,t) {
        if (active) {if (active!==p) {hud.put(p,"BUSY | one player at a time"); core.log("busy",{slot:core.slot(p)});} return;}
        if (cfg.vehicle && !vehicle()) {hud.put(p,"VEHICLE MISSING"); core.log("refused",{reason:"vehicle_missing"}); return;}
        if (things.length !== cfg.points.length) {core.log("refused",{reason:"entities_missing"});return;}
        if (finishedSlot!==undefined) hud.hideSlot(finishedSlot);
        finishedSlot=undefined; active=p; started=t; score=0; next=0; penalty=0; skipZone=-1;
        for (let i=0;i<things.length;i++) {
            const e=things[i]; visible.set(e,true); e.SetColor({r:255,g:255,b:255,a:255});
            if (kind==="gallery") {native.get(e).restore(); tilt(i,true); switchAt.set(e,t+delay());}
        }
        if (kind==="race") ringColors();
        audio("start",start.GetAbsOrigin()); hud.show(p); core.log("start",{slot:core.slot(p),kind});
    }
    function hitTarget(e,position,t) {
        if (!active || kind!=="gallery" || !visible.get(e)) return;
        const centre=core.add(e.GetAbsOrigin(),core.basis(e.GetAbsAngles()).u,32), distance=position ? core.dist(position,centre) : 0;
        const points=distance<=14 ? 10 : distance<=26 ? 5 : 2;
        score+=points; visible.set(e,false); switchAt.set(e,t+delay());
        tilt(things.indexOf(e),false); native.get(e).restore(); audio("hit",e.GetAbsOrigin());
        core.log("score_hit",{target:e.GetEntityName(),points,score});
    }
    function gallery(t) {
        for (let i=0;i<things.length;i++) {
            const e=things[i], health=native.get(e), before=health.hp; health.update(t);
            if (health.hp<before) {hitTarget(e,undefined,t); health.restore();}
            if (t >= switchAt.get(e)) {
                const up=!visible.get(e); visible.set(e,up); tilt(i,up); health.restore(); switchAt.set(e,t+delay());
                core.log(up ? "popup" : "down",{target:e.GetEntityName()});
            }
        }
    }
    function race(t) {
        const body=cfg.vehicle ? vehicle() : active; if (!core.valid(body)) {finish("vehicle_missing",t);return;}
        const at=point(body,cfg.vehicle ? 12 : 32);
        if (next<things.length && core.dist(at,point(things[next],64))<=cfg.radius) {
            core.log("checkpoint",{index:next}); audio("checkpoint",things[next].GetAbsOrigin()); next++; ringColors();
            if (next===things.length) {score=next;finish("finish",t);return;}
        }
        let zone=-1;
        for (let i=next+1;i<things.length;i++) if(core.dist(at,point(things[i],64))<=cfg.radius){zone=i;break;}
        if (zone!==-1 && zone!==skipZone) {penalty+=5;core.log("skip",{index:zone,penalty});}
        skipZone=zone;
    }
    function collect(t,dt) {
        const at=active.GetAbsOrigin();
        for (const e of things) {
            if (!visible.get(e)) continue;
            const a=e.GetAbsAngles(); e.Move({angles:{pitch:0,yaw:(a.yaw+90*dt)%360,roll:0}});
            if (core.dist(at,point(e))<=cfg.radius) {visible.set(e,false);e.SetColor({r:255,g:255,b:255,a:0});score++;audio("hit",e.GetAbsOrigin());core.log("collect",{item:e.GetEntityName(),score});}
        }
        if(score===things.length) finish("all_collected",t);
    }
    function tick() {
        const t=now(),dt=Math.min(.1,Math.max(0,t-lastTime)); lastTime=t;
        for(const p of core.pawns()) {
            const s=core.slot(p), pressed=p.IsInputPressed(CSInputs.USE), edge=pressed&&!useHeld.get(s);useHeld.set(s,pressed);
            if(edge && core.eyeRay(p,96)?.hitEntity===start) startGame(p,t);
        }
        if(active && (!core.valid(active)||!active.IsAlive())) reset("death");
        if(active) {
            if(elapsed(t)>=cfg.time) finish("time",t);
            else if(kind==="gallery") gallery(t); else if(kind==="race") race(t);else collect(t,dt);
        }
        if(active && hud.due("state",t,.1)) {
            const best=kind==="race" ? record.toFixed(2)+"s" : record;
            hud.put(active,kind==="race" ? `RACE ${elapsed(t).toFixed(2)}s | CP ${next}/${things.length} | +${penalty}s | BEST ${best}` : `${kind==="gallery" ? "SCORE" : "FOUND"} ${score} | TIME ${Math.max(0,cfg.time-elapsed(t)).toFixed(1)} | BEST ${best}`);
        }
        if(finishedSlot!==undefined && t>=finishedUntil){hud.hideSlot(finishedSlot);finishedSlot=undefined;}
        Instance.SetNextThink(t+.05);
    }
    function setup() {
        start=Instance.FindEntityByName(E.start);things=(kind==="collect"?E.items:E.targets).map(n=>Instance.FindEntityByName(n)).filter(core.valid);
        hud.find();hud.resetDue();
        if(kind==="gallery") {
            const adapters=[...native.values()];native.clear();
            for(let i=0;i<things.length;i++) {
                const h=adapters[i] || Health({core,body:()=>things[i],max:100000,native:true});native.set(things[i],h);
            }
        }
        useHeld.clear();reset("activate");core.fx(E.start,"DisableMotion");lastTime=now();Instance.SetNextThink(now()+.05);
    }
    core.on("OnBulletImpact",e=>{if(active && e.weapon?.GetOwner()===active && things.includes(e.hitEntity)) hitTarget(e.hitEntity,e.position,now());});
    core.on("OnBeginRoundRestart",()=>reset("round"));
    core.on("OnPlayerReset",e=>{if(active===e.player)reset("reset");});
    core.on("OnPlayerKill",e=>{if(active===e.player)reset("death");});
    core.state(()=>({kind,active:!!active,score,next,collected:kind==="collect"?score:0,penalty,record,time:active?Math.max(0,cfg.time-elapsed(now())):0,
        vehicle:core.valid(vehicle())?vehicle().GetEntityName():null,targets:things.map(e=>({name:e.GetEntityName(),up:!!visible.get(e),angles:e.GetAbsAngles()}))}));
    core.on("OnActivate",setup);
    core.on("OnRoundStart",setup);
    Instance.SetThink(tick);
}
const minigame=MiniGame(config);
