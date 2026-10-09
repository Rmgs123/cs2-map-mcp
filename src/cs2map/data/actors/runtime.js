import { Instance, CSPlayerPawn } from "cs_script/point_script";
const spec = __SPEC__;
const vec = p => ({x:p[0], y:p[1], z:p[2]});
let actor, index=1, paused=false, finished=false, hold=0, previousTime=0, yaw=0, clip="";
let diagnostic=0;
let playback=-1;
const deltaAngle = (a,b) => ((b-a+540)%360)-180;
function animation(next, rate=1) {
    if (next !== clip) {
        Instance.EntFireAtTarget({target:actor, input:"SetAnimationNoResetLooping", value:next});
        clip=next;
        Instance.Msg("N_ACTOR "+spec.name+" clip "+next);
    }
    if (rate!==playback) {
        Instance.EntFireAtTarget({target:actor, input:"SetPlaybackRate", value:rate}); playback=rate;
    }
}
function report(reason="state") {
    if (!actor?.IsValid()) return;
    const p=actor.GetAbsOrigin();
    Instance.Msg("N_ACTOR "+spec.name+" "+reason+" "+JSON.stringify({index, clip, paused, finished,
        pos:[p.x,p.y,p.z], yaw}));
}
function reset() {
    actor=Instance.FindEntityByName(spec.name);
    if (!actor?.IsValid()) { Instance.Msg("N_ACTOR ERROR missing "+spec.name); return; }
    index=1; paused=false; finished=false; hold=Instance.GetGameTime()+spec.idle_at_points;
    const a=spec.path[0].pos, b=spec.path[1].pos;
    yaw=Math.atan2(b[1]-a[1],b[0]-a[0])*180/Math.PI;
    actor.Teleport({position:vec(a), angles:{pitch:0,yaw,roll:0}, velocity:vec([0,0,0])});
    clip=""; playback=-1; animation("idle"); previousTime=Instance.GetGameTime(); diagnostic=previousTime;
    Instance.SetNextThink(previousTime+.015625);
    report("reset");
}
function advance(now) {
    if (spec.path[index].stop) { hold=now+spec.idle_at_points; animation("idle"); report("arrival"); }
    index++;
    if (index>=spec.path.length) {
        if (spec.loop) index=1;
        else { finished=true; animation("idle"); report("complete"); }
    }
}
function tick() {
    const now=Instance.GetGameTime(), dt=Math.max(0,Math.min(.1,now-previousTime));
    previousTime=now;
    try {
        if (!actor?.IsValid()) return;
        const p=actor.GetAbsOrigin();
        let nearby=false;
        if (spec.stop_near_player>0) {
            for (const controller of Instance.GetAllPlayerControllers()) {
                const pawn=controller.GetPlayerPawn();
                if (pawn instanceof CSPlayerPawn && pawn.IsValid() && pawn.IsAlive()) {
                    const q=pawn.GetAbsOrigin();
                    if (Math.hypot(q.x-p.x,q.y-p.y,q.z-p.z)<spec.stop_near_player) nearby=true;
                }
            }
        }
        if (paused || finished || nearby || now<hold) { animation("idle"); return; }
        const goal=spec.path[index].pos;
        const dx=goal[0]-p.x,dy=goal[1]-p.y,dz=goal[2]-p.z;
        const distance=Math.hypot(dx,dy);
        if (distance<.1) {
            actor.Move({position:vec(goal)});
            advance(now);
            return;
        }
        const facing=Math.atan2(dy,dx)*180/Math.PI;
        const turn=deltaAngle(yaw,facing);
        yaw+=Math.max(-180*dt,Math.min(180*dt,turn));
        if (Math.abs(turn)>8) {
            actor.Move({angles:{pitch:0,yaw,roll:0}}); animation("turn"); return;
        }
        const fraction=Math.min(1,spec.speed*dt/distance);
        const q={x:p.x+dx*fraction,y:p.y+dy*fraction,z:p.z+dz*fraction};
        const ground=Instance.TraceLine({start:{...q,z:q.z+18},end:{...q,z:q.z-18},ignoreEntity:actor,ignorePlayers:true});
        if (!ground.didHit || ground.startedInSolid || ground.normal.z<.7) {
            animation("idle"); if(now>=diagnostic) report("no_floor"); return;
        }
        q.z=ground.end.z;
        const clearance=Instance.TraceBox({start:{...p,z:Math.max(p.z,q.z)+18.1},
            end:{...q,z:Math.max(p.z,q.z)+18.1}, mins:{x:-16,y:-16,z:0},maxs:{x:16,y:16,z:53.9},
            ignoreEntity:actor,ignorePlayers:true});
        if (clearance.startedInSolid || (clearance.didHit && clearance.fraction<.999)) {
            animation("idle"); if(now>=diagnostic) report("blocked"); return;
        }
        actor.Move({position:q,angles:{pitch:0,yaw,roll:0},velocity:vec([0,0,0])});
        animation("walk",spec.speed/spec.walk_speed);
        if (fraction===1) advance(now);
    } finally {
        if (now>=diagnostic) { report(); diagnostic=now+2; }
        Instance.SetNextThink(now+.015625);
    }
}
Instance.SetThink(tick);
Instance.OnActivate(reset);
Instance.OnRoundStart(reset);
Instance.OnScriptInput("Start",()=>{paused=false;previousTime=Instance.GetGameTime();});
Instance.OnScriptInput("Stop",()=>{paused=true;animation("idle");report("stop");});
Instance.OnScriptInput("Reset",reset);
Instance.OnScriptInput("State",()=>report());
