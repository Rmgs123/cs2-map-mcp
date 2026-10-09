"""Map-local game modes and movement courses; native mode tests restore CS2."""
from __future__ import annotations

import contextvars
import copy
import json
import math
import re
import time
from pathlib import Path

from . import checks, competitive as comp, game, layout, playtest, scripting, vconsole
from .project import OpError
from .config import check_name

MODE_KEY = "cs2map_mode"
COURSE_KEY = "cs2map_course"
NATIVE = {"casual": (0,0), "wingman": (0,2), "deathmatch": (1,2),
          "armsrace": (1,0), "aim": (3,0), "surf": (3,0), "bhop": (3,0), "kz": (3,0)}
FLOOR = "materials/concrete/concrete_floor_04.vmat"
WALL = "materials/concrete/hr_c/hr_concrete_wall_004.vmat"
PREVIEW = contextvars.ContextVar("c2_mode_preview", default=False)


def _name(value):
    name = comp._id(value, "mode/course name")
    if not name.startswith("c2_"):
        raise OpError("mode/course script names must start c2_ (owned addon script namespace)")
    if len(name) > 48:
        raise OpError("mode/course name must be at most 48 characters")
    return name


def _int(value, field, low, high):
    if isinstance(value,bool) or not isinstance(value,int) or not low <= value <= high:
        raise OpError(f"{field} must be an integer {low}..{high}")
    return value


def _box(value, field):
    if not isinstance(value,dict) or set(value) != {"mins","maxs"}:
        raise OpError(f"{field} needs mins/maxs")
    return comp._bounds(comp._vec(value["mins"],field), comp._vec(value["maxs"],field))


def _records(m, key):
    return [(n,json.loads(n[key])) for n in m.iter_nodes() if n.get(key)]


def _group(m, name, key, data, refs, ref=None):
    n = m.new_group(name,[0,0,0])
    n.set(key,"string",json.dumps(data,separators=(",",":")))
    m.attach(n)
    if ref:
        refs[ref] = n["nodeID"]
    return n["nodeID"]


def _script(p,m,name,code,refs):
    target = name+"_script"
    if m.entities(targetname=target):
        raise OpError(f"{target} already exists")
    path = p.s.content_addon / f"maps/scripts/mcp/{name}.js"
    if path.exists() and path.read_text(encoding="utf-8") != code:
        needle = f"maps/scripts/mcp/{name}.vjs".encode()
        users = [x.stem for x in (p.s.content_addon/"maps").glob("*.vmap") if needle in x.read_bytes()]
        if users:
            raise OpError(f"script name already used by {users}; use a new c2_ name")
    if PREVIEW.get():
        node = m.new_entity("point_script", {"targetname":target,"cs_script":f"maps/scripts/mcp/{name}.vjs"},
                            [0,0,8], defaults=p.catalog.defaults("point_script"))
        m.attach(node)
        return node["nodeID"]
    result = scripting.script_write(p,name,code)
    if not result.get("ok"):
        raise OpError(f"mode script compilation failed: {result.get('log')}")
    return scripting.add_script(p,m,{"name":name,"targetname":target},refs)["id"]


def map_edit_wrapper(p, original):
    """Preflight the full batch before writing scripts, including dry runs."""
    asset_ops = {"add_mode","add_movement_course","add_arena"}
    def edit(name,operations,dry_run=False,force=False):
        if not isinstance(operations,list) or not any(isinstance(x,dict) and x.get("op") in asset_ops for x in operations):
            return original(name,operations,dry_run,force)
        with p._lock:
            if not dry_run:
                p._check_hammer(name,force)
            token = PREVIEW.set(True)
            try:
                result = original(name,operations,True,force)
            finally:
                PREVIEW.reset(token)
            return result if dry_run else original(name,operations,False,force)
    return edit


def preprocess(p,m,op,refs):
    if op.get("op") in {"add_mode","add_movement_course","add_arena"} and "_mode_request" not in op:
        return {"op":op["op"],"_mode_request":json.dumps(op,allow_nan=False)}
    return op


def _unpack(op):
    return json.loads(op["_mode_request"]) if "_mode_request" in op else copy.deepcopy(op)


def _spawn_points(p,m,op):
    provided = op.get("spawns")
    if provided is not None:
        if not isinstance(provided,list) or not 2 <= len(provided) <= 128:
            raise OpError("spawns must contain 2..128 feet positions (8 above floor)")
        return [comp._point(m,x,"spawn",8) for x in provided]
    result = []
    solids = layout.world_solids(m)
    for plan in layout.plans(m):
        for room in plan["rooms"].values():
            lo,hi = room["interior"]["mins"], room["interior"]["maxs"]
            nx,ny = min(4,max(1,int((hi[0]-lo[0])/256))), min(4,max(1,int((hi[1]-lo[1])/256)))
            for i in range(nx):
                for j in range(ny):
                    pos = [lo[0]+(hi[0]-lo[0])*(i+.5)/nx,lo[1]+(hi[1]-lo[1])*(j+.5)/ny,room["floor_z"]+8]
                    box = {"mins":[pos[0]-16,pos[1]-16,pos[2]],"maxs":[pos[0]+16,pos[1]+16,pos[2]+72]}
                    if not layout.collisions(m,box,[0,0,0],solids):
                        result.append(pos)
    if not result:
        result = [list(e.get("origin")) for cls in ("info_player_terrorist","info_player_counterterrorist")
                  for e in m.entities(classname=cls)]
    if not 2 <= len(result) <= 128:
        raise OpError("provide 2..128 spawns or an add_layout with clear floors")
    return result


def _mode_code(spec):
    # Native game_type/game_mode are deliberately NOT applied after map activation:
    # their game-rules objects must be selected before map_workshop loads the map.
    settings = ["mp_freezetime 0"]
    if spec["mode"] in {"aim","surf","bhop","kz"}:
        settings += ["mp_respawn_on_death_t 1","mp_respawn_on_death_ct 1","mp_ignore_round_win_conditions 1",
                     "mp_freezetime 0","mp_warmuptime 0","mp_limitteams 0","mp_autoteambalance 0"]
    if spec["mode"] == "surf":
        settings += ["sv_airaccelerate 150"]
    elif spec["mode"] == "bhop":
        settings += ["sv_airaccelerate 100","sv_autobunnyhopping 1","sv_enablebunnyhopping 1"]
    return '''import { Instance } from "cs_script/point_script";
const cfg=SPEC;
const deaths=new Map(),resets=new Map(),pending=new Set();
const slot=p=>p?.GetOriginalPlayerController()?.GetPlayerSlot();
function configure(){for(const c of cfg.commands)Instance.ServerCommand(c);Instance.Msg("C2_MODE ready "+cfg.mode);}
Instance.OnActivate(configure);Instance.OnRoundStart(configure);
Instance.OnBeginRoundRestart(()=>pending.clear());
Instance.OnPlayerKill(({player})=>{const s=slot(player);deaths.set(s,(deaths.get(s)||0)+1);pending.add(s);Instance.Msg("C2_MODE death "+s);});
Instance.OnPlayerReset(({player})=>{const s=slot(player);if(pending.delete(s))resets.set(s,(resets.get(s)||0)+1);
 if(cfg.equip){Instance.EntFireAtName({name:cfg.equip,input:"TriggerForActivatedPlayer",value:cfg.weapon,activator:player,delay:0.1});
  // game_player_equip is the authored path; GiveNamedItem is a bounded
  // fallback for Workshop Tools builds where that input is not dispatched to
  // a freshly reset bot. The result is still checked on the actual pawn.
  Instance.Delay(0.2).then(()=>{if(player?.IsValid()&&!player.FindWeapon(cfg.weapon))player.GiveNamedItem(cfg.weapon,true);});
  Instance.Delay(0.45).then(()=>{if(player?.IsValid())Instance.Msg("C2_MODE equip "+s+" "+(player.FindWeapon(cfg.weapon)?.GetData().GetName()||"MISSING"));});}});
Instance.OnScriptInput("Report",()=>Instance.Msg("C2_MODE stats "+JSON.stringify({mode:cfg.mode,deaths:Object.fromEntries(deaths),respawns:Object.fromEntries(resets)})));
'''.replace("const cfg=SPEC;","const cfg="+json.dumps({**spec,"commands":settings})+";")


def add_mode(p,m,op,refs):
    op = _unpack(op)
    comp._keys(op,{"op","name","mode","spawns","ref","equip","weapon"},"mode")
    name,mode = _name(op.get("name")),op.get("mode")
    if mode not in NATIVE:
        raise OpError("mode must be "+", ".join(NATIVE))
    if _records(m,MODE_KEY):
        raise OpError("map already has add_mode; recreate it to change its mode")
    if mode == "wingman" and len(m.entities(classname="func_bomb_target")) != 1:
        raise OpError("wingman needs exactly one bombsite; first add_comp_plan(mode=wingman)")
    points = _spawn_points(p,m,op)
    ids = []
    if mode == "deathmatch":
        for i,pos in enumerate(points):
            ids.append(comp._add_entity(p,m,refs,"info_deathmatch_spawn",pos,{"targetname":f"{name}_dm_{i}"})["id"])
        params = m.entities(classname="info_map_parameters")
        if params:
            for e in params:
                m.set_keyvalues(e,{"disableautogenerateddmspawns":1})
        else:
            ids.append(comp._add_entity(p,m,refs,"info_map_parameters",points[0],{"disableautogenerateddmspawns":1})["id"])
    elif mode == "armsrace":
        for team,cls in (("t","info_armsrace_terrorist"),("ct","info_armsrace_counterterrorist")):
            source = m.entities(classname="info_player_terrorist" if team=="t" else "info_player_counterterrorist")
            ordered = sorted(points,key=lambda x:(x[0],x[1],x[2]))
            candidates = [list(e.get("origin")) for e in source] or (
                ordered[:len(ordered)//2] if team=="t" else ordered[len(ordered)//2:])
            selected = []
            for pos in candidates:
                if all(math.dist(pos,q)>=128 for q in selected):
                    selected.append(pos)
                    ids.append(comp._add_entity(p,m,refs,cls,pos,{"targetname":f"{name}_{team}_{len(selected)}"})["id"])
    # Preserve existing normal team spawns (native DM still needs teams to join).
    for team,cls in (("t","info_player_terrorist"),("ct","info_player_counterterrorist")):
        if not m.entities(classname=cls):
            for pos in points[0::2] if team=="t" else points[1::2]:
                ids.append(comp._add_entity(p,m,refs,cls,pos,{})["id"])
    for i,pos in enumerate(points):
        ids.append(comp._add_nav(p,m,refs,pos,f"{name}_nav_{i}")["id"])
    spec = {"name":name,"mode":mode,"equip":op.get("equip"),"weapon":op.get("weapon","weapon_ak47")}
    if spec["equip"]:
        comp._id(spec["equip"],"equip")
        if not re.fullmatch(r"weapon_[a-z0-9_]+",spec["weapon"]) or not m.entities(targetname=spec["equip"]):
            raise OpError("equip needs an existing game_player_equip and weapon_* name")
    script_id = _script(p,m,name,_mode_code(spec),refs)
    ident = _group(m,"mode:"+name,MODE_KEY,{**spec,"entity_ids":ids,"script_id":script_id},refs,op.get("ref"))
    return {"op":"add_mode","id":ident,"mode":mode,"spawns":len(points),"script":name,
            "game_type":NATIVE[mode][0],"game_mode":NATIVE[mode][1],"requires_native_reload":True}


def mode_setup(p,map_name,mode):
    check_name(map_name,"map")
    result = p.map_edit(map_name,[{"op":"add_mode","name":_name("c2_"+map_name+"_mode"),"mode":mode}])
    return {"ok":True,"map":map_name,**result["results"][0],
            "launch_command":f"game_type {NATIVE[mode][0]}\ngame_mode {NATIVE[mode][1]}\nmap_workshop {p.s.addon} {map_name}",
            "note":"Authoring only. Native rules require a map reload; mode_playtest loads/tests/restores under one lock."}


def add_surf_ramp(p,m,op,refs):
    comp._keys(op,{"op","origin","length","width","angle","side","material","ref"},"surf ramp")
    origin = comp._vec(op.get("origin",[0,0,0]),"origin")
    length = comp._number(op.get("length",1024),"length",64,8192)
    width = comp._number(op.get("width",256),"width",32,1024)
    angle = comp._number(op.get("angle",55),"angle",45,60)
    side = op.get("side","+y")
    if side not in {"+x","-x","+y","-y"}:
        raise OpError("side must be +x/-x/+y/-y")
    height = width*math.tan(math.radians(angle))
    # Surf incline is CROSSWISE to travel, not an ordinary ascending floor ramp.
    size = [length,width,height] if side[-1]=="y" else [width,length,height]
    out = p._apply(m,{"op":"add_mesh","shape":{"shape":"wedge","mins":origin,
                    "maxs":[origin[i]+size[i] for i in range(3)],"rise":side},
                    "material":op.get("material",FLOOR),"ref":op.get("ref")},refs)
    return {**out,"op":"add_surf_ramp","angle":angle,"length":length,"width":width,"height":round(height,3)}


def add_bhop_platforms(p,m,op,refs):
    comp._keys(op,{"op","origin","count","size","gap","rise","material","ref"},"bhop platforms")
    origin = comp._vec(op.get("origin",[0,0,0]),"origin")
    count = _int(op.get("count",6),"count",1,64)
    size = comp._size(op.get("size",[96,96,16]),"size",3)
    gap = comp._number(op.get("gap",64),"gap",16,256)
    rise = comp._number(op.get("rise",0),"rise",-48,48)
    ids = []
    for i in range(count):
        lo = [origin[0]+i*(size[0]+gap),origin[1],origin[2]+i*rise]
        ids += p._apply(m,{"op":"add_mesh","shape":{"shape":"box","mins":lo,"maxs":[lo[j]+size[j] for j in range(3)]},
                         "material":op.get("material",FLOOR)},refs)["ids"]
    if op.get("ref"):
        refs[op["ref"]] = ids[0]
    return {"op":"add_bhop_platforms","ids":ids,"count":count,"gap":gap}


COURSE_RUNTIME = r'''import { Instance, CSPlayerPawn, CustomHudLayout } from "cs_script/point_script";
const cfg=SPEC;
let hooks=[],states=new Map();
const valid=p=>p instanceof CSPlayerPawn&&p.IsValid()&&p.IsAlive();
const slot=p=>p.GetOriginalPlayerController().GetPlayerSlot();
function state(p){let s=states.get(slot(p));if(!s){s={start:null,checkpoint:cfg.spawn,pending:false};states.set(slot(p),s);}return s;}
function msg(p,text){Instance.Msg("C2_TIMER "+cfg.name+" slot="+slot(p)+" "+text);}
function hud(p,text){const h=Instance.FindEntityByName(cfg.name+"_hud");if(h instanceof CustomHudLayout){h.SetDialogVariableStringForPlayer(slot(p),"story","chapter",cfg.mode.toUpperCase());h.SetDialogVariableStringForPlayer(slot(p),"story","objective","Start / checkpoints / finish");h.SetDialogVariableStringForPlayer(slot(p),"story","timer",text);}}
function connect(name,fn){const ent=Instance.FindEntityByName(name);if(!ent)throw Error("missing zone "+name);hooks.push(Instance.ConnectOutput(ent,"OnStartTouch",({activator})=>{if(valid(activator))fn(activator);}));}
function install(){for(const id of hooks)if(id!==undefined)Instance.DisconnectOutput(id);hooks=[];states.clear();
 connect(cfg.name+"_start",p=>{const s=state(p);s.start=Instance.GetGameTime();s.checkpoint=cfg.spawn;msg(p,"start");});
 connect(cfg.name+"_finish",p=>{const s=state(p);if(s.start!==null){const elapsed=Instance.GetGameTime()-s.start;s.start=null;hud(p,"Finished: "+elapsed.toFixed(3)+" s");msg(p,"finish seconds="+elapsed.toFixed(3));}});
 cfg.checkpoints.forEach((c,i)=>connect(cfg.name+"_cp_"+i,p=>{state(p).checkpoint=c.destination;msg(p,"checkpoint="+i);}));
 connect(cfg.name+"_fall",p=>{const s=state(p);if(s.pending)return;s.pending=true;const cp=s.checkpoint;
  // Native trigger_teleport sends to start immediately; the next engine tick
  // returns this individual pawn to its checkpoint, with zero velocity.
  Instance.Delay(0.03).then(()=>{if(valid(p)){p.Teleport({position:{x:cp[0],y:cp[1],z:cp[2]},velocity:{x:0,y:0,z:0}});msg(p,"return "+JSON.stringify(cp)+" actual="+JSON.stringify(p.GetAbsOrigin()));}s.pending=false;});});
 Instance.Msg("C2_TIMER "+cfg.name+" ready");}
Instance.OnActivate(install);Instance.OnRoundStart(install);
Instance.OnPlayerDisconnect(({playerSlot})=>states.delete(playerSlot));
Instance.SetThink(()=>{for(const c of Instance.GetAllPlayerControllers()){const p=c.GetPlayerPawn();if(valid(p)){const s=state(p);if(s.start!==null)hud(p,(Instance.GetGameTime()-s.start).toFixed(2)+" s");}}Instance.SetNextThink(Instance.GetGameTime()+0.1);});
Instance.SetNextThink(Instance.GetGameTime()+0.1);
'''


def add_movement_course(p,m,op,refs):
    op = _unpack(op)
    comp._keys(op,{"op","name","mode","start","finish","spawn","checkpoints","fall","hud_layout","ref"},"movement course")
    name = _name(op.get("name"))
    mode = op.get("mode","surf")
    if mode not in {"surf","bhop","kz"}:
        raise OpError("movement mode must be surf/bhop/kz")
    start,finish,fall = [_box(op.get(k),k) for k in ("start","finish","fall")]
    spawn = comp._point(m,op.get("spawn"),"spawn",8)
    checkpoints = op.get("checkpoints",[])
    if not isinstance(checkpoints,list) or len(checkpoints)>32:
        raise OpError("checkpoints must be a list of up to 32 zones")
    cps = []
    for c in checkpoints:
        comp._keys(c,{"mins","maxs","destination"},"checkpoint")
        cps.append({**_box({k:c[k] for k in ("mins","maxs")},"checkpoint"),
                    "destination":comp._point(m,c.get("destination"),"checkpoint destination",8)})
    # Reuse the established story HUD; C2 is allowed to write scripts only.
    hud = op.get("hud_layout","panorama/layout/custom_game/ms_chapter1.vxml")
    if hud is not None:
        if not isinstance(hud,str) or not re.fullmatch(r"panorama/layout/custom_game/[a-zA-Z0-9_]+\.vxml",hud):
            raise OpError("hud_layout must be an existing panorama/layout/custom_game/*.vxml or null")
        if not (p.s.game_addon/(hud+"_c")).exists():
            raise OpError("HUD resource not compiled; pass an existing story HUD or hud_layout:null for console only")
    ids = []
    zones = [(name+"_start",start,"trigger_multiple",{}),(name+"_finish",finish,"trigger_multiple",{})]
    zones += [(f"{name}_cp_{i}",{k:c[k] for k in ("mins","maxs")},"trigger_multiple",{}) for i,c in enumerate(cps)]
    dest = name+"_return"
    ids.append(comp._add_entity(p,m,refs,"info_teleport_destination",spawn,{"targetname":dest})["id"])
    zones.append((name+"_fall",fall,"trigger_teleport",{"target":dest}))
    for target,bounds,cls,kv in zones:
        ids.append(comp._add_brush(p,m,refs,cls,bounds,{"targetname":target,"spawnflags":4097,**kv})["id"])
    if hud:
        ids.append(comp._add_entity(p,m,refs,"custom_hud_layout",spawn,{"targetname":name+"_hud","layout":hud})["id"])
    spec = {"name":name,"mode":mode,"spawn":spawn,"checkpoints":cps}
    script_id = _script(p,m,name,COURSE_RUNTIME.replace("const cfg=SPEC;","const cfg="+json.dumps(spec)+";"),refs)
    ident = _group(m,"course:"+name,COURSE_KEY,{**spec,"entity_ids":ids,"script_id":script_id,"hud_layout":hud},refs,op.get("ref"))
    return {"op":"add_movement_course","id":ident,"script":name,"checkpoints":len(cps),"hud_layout":hud,
            "timer":"engine game time; per player; physical traversal needs separate verification"}


def add_arena(p,m,op,refs):
    op = _unpack(op)
    comp._keys(op,{"op","name","origin","size","weapon","covers","ref"},"arena")
    name = _name(op.get("name"))
    origin = comp._vec(op.get("origin",[0,0,0]),"origin")
    size = comp._size(op.get("size",[1536,1024,256]),"size",3)
    if size[0]<768 or size[1]<512 or size[2]<128:
        raise OpError("arena minimum size is 768x512x128")
    weapon = op.get("weapon","weapon_ak47")
    if not isinstance(weapon,str) or not re.fullmatch(r"weapon_[a-z0-9_]+",weapon):
        raise OpError("weapon must be a weapon_* classname")
    count = _int(op.get("covers",4),"covers",0,12)
    if count % 2:
        raise OpError("covers must be even for a mirrored arena")
    shell = layout.add_layout(p,m,{"op":"add_layout","name":name+"_layout","rooms":[
            {"id":"arena","origin":origin,"size":size,"ceiling":False}],"wall":16,
            "materials":{"floor":FLOOR,"walls":WALL}},refs)
    for i in range(count):
        # Mirrored pairs around both central axes, leaving an open central lane.
        x = origin[0]+size[0]*(.25+.5*(i%2))
        pairs = max(1,count//2)
        y = origin[1]+size[1]*(i//2+1)/(pairs+1)
        z = origin[2]
        p._apply(m,{"op":"add_mesh","shape":{"shape":"box","mins":[x-48,y-64,z],"maxs":[x+48,y+64,z+72]},"material":WALL},refs)
    for team,cls,x,yaw in (("t","info_player_terrorist",.12,0),("ct","info_player_counterterrorist",.88,180)):
        for i in range(10):
            pos = [origin[0]+size[0]*x,origin[1]+size[1]/2+(i-4.5)*48,origin[2]+8]
            comp._add_entity(p,m,refs,cls,pos,{},[0,yaw,0])
    equip = name+"_equip"
    comp._add_entity(p,m,refs,"game_player_equip",origin,{"targetname":equip,"spawnflags":3,weapon:1,"weapon_knife":1})
    mode = add_mode(p,m,{"op":"add_mode","name":name+"_mode","mode":"aim","equip":equip,"weapon":weapon},refs)
    # Probes/lightmap keep full compilation free of quality setup warnings.
    comp._add_entity(p,m,refs,"env_combined_light_probe_volume",[origin[i]+size[i]/2 for i in range(3)],
                     {"box_mins":[-size[i]/2-32 for i in range(3)],"box_maxs":[size[i]/2+32 for i in range(3)]})
    p._apply(m,{"op":"add_mesh","shape":{"shape":"box","mins":[origin[0]-32,origin[1]-32,origin[2]-32],
              "maxs":[origin[i]+size[i]+32 for i in range(3)]},"material":"materials/tools/toolslightmapres.vmat","options":{"physicsType":"none"}},refs)
    if op.get("ref"):
        refs[op["ref"]] = shell["id"]
    return {"op":"add_arena","id":shell["id"],"mode_id":mode["id"],"weapon":weapon,"covers":count,"spawns_per_team":10}


def _stats(lines):
    found = [json.loads(x.split("C2_MODE stats ",1)[1]) for x in lines if "C2_MODE stats " in x]
    return found[-1] if found else {"deaths":{},"respawns":{}}


def _restore_settings(p,previous):
    game.command(p.s,"\n".join(f"{k} {v}" for k,v in previous.items()),wait=.2)
    restored = comp._cvar_values(game.query(p.s,"\n".join(previous)+"\necho C2_SETTINGS_RESTORED",
                                          "C2_SETTINGS_RESTORED",timeout=3),list(previous))
    changed = {k:{"expected":v,"actual":restored[k]} for k,v in previous.items() if float(v)!=float(restored[k])}
    if changed:
        raise OpError("server settings restoration did not verify: "+json.dumps(changed))


def mode_playtest(p,map_name,bots=10,seconds=30):
    check_name(map_name,"map")
    bots,seconds = _int(bots,"bots",2,20),_int(seconds,"seconds",5,60)
    m = p.load(map_name)
    records = _records(m,MODE_KEY)
    if len(records)!=1:
        raise OpError("map needs exactly one mode_setup/add_mode")
    spec = records[0][1]
    mode = spec["mode"]
    deadline = time.monotonic()+112
    previous = {}
    with game.exclusive("C2-mode-playtest",wait=20):
        keys = ["game_type","game_mode","sv_airaccelerate","sv_autobunnyhopping","sv_enablebunnyhopping",
                "mp_respawn_on_death_t","mp_respawn_on_death_ct","mp_ignore_round_win_conditions",
                "mp_freezetime","mp_limitteams","mp_autoteambalance","mp_warmuptime","bot_quota"]
        previous = comp._cvar_values(game.query(p.s,"\n".join(keys)+"\necho C2_MODE_READ","C2_MODE_READ",timeout=3),keys)
        try:
            game.command(p.s,f"mp_freezetime 0\ngame_type {NATIVE[mode][0]}\ngame_mode {NATIVE[mode][1]}",wait=.2)
            # A native mode change must rebuild game rules, even if this map was loaded.
            game.load_map(p.s,map_name,timeout=max(3,min(30,deadline-time.monotonic()-seconds-10)))
            actual = comp._cvar_values(game.query(p.s,"game_type\ngame_mode\necho C2_MODE_ACTUAL",
                                                   "C2_MODE_ACTUAL",timeout=3),["game_type","game_mode"])
            if tuple(int(float(actual[k])) for k in ("game_type","game_mode")) != NATIVE[mode]:
                raise OpError("CS2 did not keep the requested native mode: "+json.dumps(actual))
            game.command(p.s,"mp_freezetime 0",wait=.1)
            zero_freeze = comp._cvar_values(game.query(p.s,"mp_freezetime\necho C2_ZERO_FREEZE",
                                                      "C2_ZERO_FREEZE",timeout=3),["mp_freezetime"])
            if float(zero_freeze["mp_freezetime"]) != 0:
                raise OpError("mp_freezetime optimization was not accepted by CS2")
            if time.monotonic()+seconds+12>deadline:
                raise OpError("queue/load used the playtest budget; retry or use fewer seconds")
            trace = scripting._ConsoleTrace()
            connection = vconsole._connection(vconsole.PORT)
            with connection.cv:
                connection.listeners.append(trace)
            try:
                result = playtest.playtest_run(p,map_name,bots,seconds,True)
                lines = game.command(p.s,f"ent_fire {spec['name']}_script RunScriptInput Report",wait=.5)
                stats = _stats(lines)
            finally:
                with connection.cv:
                    connection.listeners.remove(trace)
            console_path = p.s.logs / f"c2-mode-{map_name}-{time.time_ns()}.json"
            console_path.write_text(json.dumps({"map":map_name,"mode":mode,"console":trace},indent=2),encoding="utf-8")
            result["console_log"] = str(console_path)
            result["mp_freezetime_during_test"] = zero_freeze["mp_freezetime"]
            result["errors"] = list(dict.fromkeys(result.get("errors",[])+scripting._script_errors(trace)))
            if spec.get("equip"):
                # The local spectator is slot 0 and has no pawn inventory.
                equip_lines = [x for x in trace if "C2_MODE equip " in x and
                               not re.search(r"C2_MODE equip 0(?: |$)", x)]
                result["equipment_observed"] = equip_lines[:4]
                result["equipment_ok"] = bool(equip_lines) and all("MISSING" not in x for x in equip_lines)
            # The report counts real kill -> OnPlayerReset transitions, not forced round restarts.
            respawns = sum(stats.get("respawns",{}).values())
            observed = comp._bot_observation(m,json.loads(Path(result["samples_path"]).read_text(encoding="utf-8"))["samples"])
            result.update({"mode":mode,"native":{"game_type":NATIVE[mode][0],"game_mode":NATIVE[mode][1]},
                           "respawns_after_death":respawns,"mode_events":stats,"moving_bots_by_team":observed["moving_bots_by_team"]})
            stock_warnings = [x for x in result.get("errors",[]) if
                              x.startswith("Graph Warning: animation/graphs/") or
                              re.match(r"Failed to set animgraph param e_(?:face|body)_idle of type unknown", x)]
            result["errors"] = [x for x in result.get("errors",[]) if x not in stock_warnings]
            result["engine_warnings"] = stock_warnings
            result["error_count"] = len(result["errors"])
            result["mode_ok"] = bool(result.get("nav_areas") and not result.get("errors") and stats.get("mode")==mode and
                all(result.get("teams",{}).get(k) for k in ("2","3")) and
                all(observed["moving_bots_by_team"].get(k) for k in ("2","3")) and
                (respawns>0 if mode in {"deathmatch","armsrace"} else True))
            if spec.get("equip"):
                result["mode_ok"] = result["mode_ok"] and result["equipment_ok"]
            result["shots"] = [{k:x.get(k) for k in ("name","path")} for x in
                game.views(p.s,map_name,[{"name":f"c2_{map_name}_mode","ang":[15,0,0]}],deadline=deadline).get("shots",[])]
            result["ok"] = result["mode_ok"]
        finally:
            # Unload this map's OnRoundStart policy before restoring global convars.
            try:
                game.ensure_map(p.s,"mcp_idle",deadline=time.monotonic()+15)
            finally:
                _restore_settings(p,previous)
        result["settings_restored"] = previous
        result["load_optimization"] = "mp_freezetime 0 before and after map load; original value restored"
    return result


def mode_course_test(p,map_name,timeout=60):
    """Exercise real trigger events with setup teleports; does not prove surfing."""
    check_name(map_name,"map")
    timeout = _int(timeout,"timeout",15,90)
    m = p.load(map_name)
    records = _records(m,COURSE_KEY)
    if len(records)!=1:
        raise OpError("map needs exactly one add_movement_course")
    spec = records[0][1]
    def centre(target):
        entities = m.entities(targetname=target)
        if len(entities)!=1:
            raise OpError("course target is missing: "+target)
        lo,hi = playtest._entity_bounds(m,entities[0])
        return [(lo[0]+hi[0])/2,(lo[1]+hi[1])/2,lo[2]+8]
    name = spec["name"]
    steps = [{"cmd":"bot_quota 0\nbot_kick\nsv_cheats 1\nmp_warmup_end\nnoclip 0"},
             {"walk":centre(name+"_start")},{"expect":f"C2_TIMER {name} .* start","within":3}]
    if spec["checkpoints"]:
        steps += [{"walk":centre(name+"_cp_0")},{"expect":f"C2_TIMER {name} .* checkpoint=0","within":3}]
    # Keep the fall touch apart from the standing platform: cmd is a setup
    # teleport, followed by engine gravity/trigger teleport with noclip off.
    fall = centre(name+"_fall")
    steps += [{"walk":fall},{"expect":f"C2_TIMER {name} .* return","within":3},
              {"shot":"c2_course_checkpoint_return"},{"walk":centre(name+"_finish")},
              {"expect":f"C2_TIMER {name} .* finish seconds=","within":3},
              {"shot":"c2_course_finish"}]
    keys = ["game_type","game_mode","sv_airaccelerate","sv_autobunnyhopping","sv_enablebunnyhopping",
            "mp_respawn_on_death_t","mp_respawn_on_death_ct","mp_ignore_round_win_conditions",
            "mp_freezetime","mp_warmuptime","mp_limitteams","mp_autoteambalance","bot_quota"]
    with game.exclusive("C2-course-test",wait=20):
        previous = comp._cvar_values(game.query(p.s,"\n".join(keys)+"\necho C2_COURSE_READ","C2_COURSE_READ",timeout=3),keys)
        try:
            game.command(p.s,"mp_freezetime 0",wait=.1)
            result = scripting.script_test(p,map_name,steps,timeout)
        finally:
            try:
                game.ensure_map(p.s,"mcp_idle",deadline=time.monotonic()+15)
            finally:
                _restore_settings(p,previous)
        result["scope"] = "real timer/checkpoint/fall trigger events with setup teleports; not a physical surf or bhop run"
        result["settings_restored"] = previous
    return result


def mode_views(p,map_name,views,hide_hud=True):
    """Capture C2 acceptance frames with a temporary zero freeze time."""
    check_name(map_name,"map")
    if not isinstance(views,list) or not 1<=len(views)<=6:
        raise OpError("views must contain 1..6 camera views")
    deadline = time.monotonic()+110
    keys = ["mp_freezetime","sv_airaccelerate","sv_autobunnyhopping","sv_enablebunnyhopping",
            "mp_respawn_on_death_t","mp_respawn_on_death_ct","mp_ignore_round_win_conditions",
            "mp_warmuptime","mp_limitteams","mp_autoteambalance","bot_quota"]
    with game.exclusive("C2-mode-views",wait=20):
        previous = comp._cvar_values(game.query(p.s,"\n".join(keys)+"\necho C2_VIEWS_READ","C2_VIEWS_READ",timeout=3),keys)
        try:
            game.command(p.s,"mp_freezetime 0",wait=.1)
            game.ensure_map(p.s,map_name,deadline=min(deadline,time.monotonic()+30))
            game.command(p.s,"mp_freezetime 0",wait=.1)
            result = game.views(p.s,map_name,views,hide_hud=hide_hud,deadline=deadline)
        finally:
            try:
                game.ensure_map(p.s,"mcp_idle",deadline=time.monotonic()+15)
            finally:
                _restore_settings(p,previous)
    return {"map":map_name,"shots":[{k:x.get(k) for k in ("name","path","console")} for x in result.get("shots",[])],
            "mp_freezetime_restored":previous["mp_freezetime"]}


def check_modes(p,m):
    result = []
    for _,spec in _records(m,MODE_KEY)+_records(m,COURSE_KEY):
        for ident in [spec.get("script_id"),*spec.get("entity_ids",[])]:
            try:
                m.node(ident)
            except Exception:
                result.append(checks.problem("error","mode_entity_missing",ident,"Mode/course entity was removed","Recreate the mode/course."))
    return result
