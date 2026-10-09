"""Small, validated JSON stories compiled to a self-contained CS2 script and HUD."""
from __future__ import annotations

import copy
import json
import math
import re
import shutil
import time
from contextvars import ContextVar
from pathlib import Path

from . import checks, compiler, scripting
from .config import ensure_inside

CONDITIONS = {'button', 'zone', 'chat', 'time', 'input', 'kills', 'pickup', 'use'}
ACTIONS = {'fire', 'sound', 'text', 'say', 'fade', 'shake', 'teleport', 'spawn', 'save', 'end_text', 'camera', 'light'}
MARKER = '// CS2MAP_STORY '
_PREVIEW = ContextVar('cs2map_story_preview', default=False)


def _number(value, label, low=0, high=86400):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label} must be a finite number in {low}..{high}')
    return value


def _text(value, label, maximum=2048):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f'{label} must be a non-empty string (up to {maximum} characters)')
    return value


def entity_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,95}', value) or value.startswith('mcp_'):
        raise ValueError('entity name must be literal letters/digits/_.-, begin with a letter/_ and not mcp_')
    return value


def _save_key(value):
    if value in {'__proto__', 'prototype', 'constructor'}:
        raise ValueError('reserved JavaScript object key')
    return value


def _vector(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f'{label} needs three coordinates')
    return [_number(x, label, -1e6, 1e6) for x in value]


def _keys(obj, allowed, label):
    if not isinstance(obj, dict):
        raise ValueError(f'{label} must be an object')
    extra = set(obj) - set(allowed)
    if extra:
        raise ValueError(f'unknown {label} fields: {sorted(extra)}')


def validate(op):
    """Fail before writing any asset on a typo or ambiguous condition/action."""
    _keys(op, {'op', 'name', 'settings', 'hud', 'stages', 'ref', 'origin'}, 'story')
    spec = copy.deepcopy(op)
    spec.pop('op', None)
    spec.pop('ref', None)
    spec['name'] = scripting._safe_name(spec.get('name', ''))
    if spec['name'].startswith('mcp_'):
        raise ValueError('story name cannot start with mcp_')
    _save_key(spec['name'])
    spec['origin'] = _vector(spec.get('origin', [0, 0, 8]), 'origin')
    settings = spec.setdefault('settings', {})
    _keys(settings, {'respawn', 'round_minutes', 'bots', 'resume'}, 'settings')
    for key, default in [('respawn', True), ('resume', False)]:
        if not isinstance(settings.setdefault(key, default), bool):
            raise ValueError(f'settings.{key} must be boolean')
    _number(settings.setdefault('round_minutes', 30), 'round_minutes', 1, 60)
    bots = settings.setdefault('bots', 0)
    if isinstance(bots, bool) or not isinstance(bots, int) or not 0 <= bots <= 16:
        raise ValueError('settings.bots must be integer 0..16')
    hud = spec.setdefault('hud', {})
    from . import ui
    _keys(hud, {'objective', 'subtitles', 'timer'} | ui.STYLE_KEYS, 'hud')
    ui.validate({k: v for k, v in hud.items() if k in ui.STYLE_KEYS})
    for key in ('objective', 'subtitles', 'timer'):
        if not isinstance(hud.setdefault(key, True), bool):
            raise ValueError(f'hud.{key} must be boolean')
    stages = spec.get('stages')
    if not isinstance(stages, list) or not 1 <= len(stages) <= 64:
        raise ValueError('stages must have 1..64 entries')
    ids = set()
    for stage in stages:
        _keys(stage, {'id', 'objective', 'say', 'until', 'then', 'hint', 'checkpoint', 'route'}, 'stage')
        ident = scripting._safe_name(stage.get('id', ''))
        if ident in ids:
            raise ValueError(f'duplicate stage id {ident}')
        ids.add(ident)
        _text(stage.get('objective'), 'objective')
        if 'hint' in stage and not isinstance(stage['hint'], bool):
            raise ValueError('stage.hint must be boolean')
        if 'checkpoint' in stage:
            stage['checkpoint'] = _vector(stage['checkpoint'], 'checkpoint')
        if 'route' in stage:
            from .route import validate as validate_route
            if stage['route'] == 'auto':
                raise ValueError('stage.route needs explicit physical steps')
            stage['route'] = validate_route(stage['route'])
        say = stage.setdefault('say', [])
        if isinstance(say, str):
            say = stage['say'] = [say]
        if not isinstance(say, list) or len(say) > 20:
            raise ValueError('stage.say must be a string or up to 20 strings')
        for text in say:
            _text(text, 'say')
        until = stage.get('until')
        _keys(until, CONDITIONS, 'until')
        if len(until) != 1:
            raise ValueError('until must contain exactly one condition')
        key, value = next(iter(until.items()))
        if key in ('button', 'input', 'use'):
            entity_name(value)
            if key == 'input' and value == 'TestReset':
                raise ValueError('TestReset is reserved for script_test')
        elif key == 'zone':
            _keys(value, {'mins', 'maxs'}, 'zone')
            lo, hi = _vector(value.get('mins'), 'zone.mins'), _vector(value.get('maxs'), 'zone.maxs')
            if any(a >= b for a, b in zip(lo, hi)):
                raise ValueError('zone.mins must be below zone.maxs on every axis')
        elif key == 'chat':
            _text(value, 'chat', 128)
            if any(c in value for c in '\n\r;"'):
                raise ValueError('chat must be a single literal command without newline/semicolon/quote')
        elif key == 'time':
            _number(value, 'time', 0.1)
        elif key == 'kills':
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
                raise ValueError('kills must be an integer 1..100')
        elif key == 'pickup':
            if not isinstance(value, str) or not re.fullmatch(r'weapon_[A-Za-z0-9_]+', value):
                raise ValueError('pickup currently supports weapon_<classname> only')
        then = stage.setdefault('then', [])
        if not isinstance(then, list) or len(then) > 64:
            raise ValueError('then must be a list of up to 64 actions')
        for action in then:
            _keys(action, ACTIONS | {'input', 'value', 'delay', 'seconds', 'amplitude', 'ang', 'on'}, 'action')
            kinds = set(action) & ACTIONS
            if len(kinds) != 1:
                raise ValueError('each then action must contain exactly one action kind')
            kind = next(iter(kinds))
            allowed = {kind} | ({'input', 'value', 'delay'} if kind == 'fire' else
                               {'seconds', 'amplitude'} if kind == 'shake' else
                               {'seconds', 'ang'} if kind == 'camera' else
                               {'seconds'} if kind in {'text', 'say', 'fade'} else
                               {'on'} if kind == 'light' else set())
            _keys(action, allowed, f'{kind} action')
            if kind in {'fire', 'spawn', 'save', 'light'}:
                entity_name(action[kind])
                if kind == 'save':
                    _save_key(action[kind])
                if kind == 'light' and not isinstance(action.get('on'), bool):
                    raise ValueError('light.on must be boolean')
            elif kind in {'text', 'say', 'end_text', 'sound'}:
                _text(action[kind], kind, 256 if kind == 'sound' else 2048)
            elif kind == 'fade':
                if action[kind] not in ('in', 'out'):
                    raise ValueError('fade must be in or out')
            elif kind in {'teleport', 'camera'}:
                action[kind] = _vector(action[kind], kind)
                if kind == 'camera':
                    action['ang'] = _vector(action.get('ang', [0, 0, 0]), 'camera.ang')
            elif kind == 'shake':
                if action[kind] is not True:
                    raise ValueError('shake must be true; use amplitude and seconds')
                _number(action.setdefault('amplitude', 4), 'amplitude', 0, 16)
            if kind == 'fire':
                entity_name(action.get('input'))
                if 'value' in action and (isinstance(action['value'], (dict, list, bool)) or action['value'] is None):
                    raise ValueError('fire.value must be string or number')
                _number(action.get('delay', 0), 'delay')
            if 'seconds' in action:
                _number(action['seconds'], 'seconds', 0.1, 600)
    return spec


HUD_XML = '''<root><styles><include src="file://{resources}/styles/custom_game/NAME.css" /></styles>
<Panel class="Root"><Panel id="story"><Label id="chapter" text="{s:chapter}" />
<Label id="objective" text="{s:objective}" /><Label id="timer" text="{s:timer}" /></Panel>
<Panel id="subtitle"><Label id="subtitle_text" text="{s:subtitle}" /></Panel></Panel></root>'''
HUD_CSS = '''.Root { width: 100%; height: 100%; }
#story { width: 720px; margin-top: 110px; horizontal-align: center; flow-children: down; padding: 16px 26px 18px 26px;
 border-radius: 8px; border-left: 4px solid #e2b866ff; box-shadow: #000000aa 0px 6px 22px 0px;
 background-color: gradient( linear, 0% 0%, 100% 0%, from( #0b131ef2 ), to( #16222ed9 ) ); }
Label { color: #f4f1e8ff; font-size: 25px; }
#chapter { color: #e2b866ff; font-size: 15px; font-weight: bold; text-transform: uppercase; letter-spacing: 2px;
 margin-bottom: 6px; }
#timer { color: #9fd3e0ff; font-size: 19px; margin-top: 8px; }
#subtitle { max-width: 1000px; vertical-align: bottom; horizontal-align: center; margin-bottom: 150px; padding: 12px 26px;
 border-radius: 6px; background-color: #0b131ee6; box-shadow: #00000099 0px 4px 16px 0px;
 transition-property: opacity; transition-duration: 0.3s; }
#subtitle_text { font-size: 24px; text-align: center; color: #ffffffff; }
.Hidden { visibility: collapse; }
#subtitle.Hidden { visibility: visible; opacity: 0; }
'''

# Embedded rather than relying on CS2 resolving arbitrary addon ES modules.
RUNTIME = r'''
import { Instance, CSPlayerPawn, CSInputs, CustomHudLayout, PointTemplate, CustomCameraMode } from "cs_script/point_script";
const story=SPEC;
let index=-1, started=0, finishing=false, complete=false, run=0, kills=0, subtitleUntil=0;
let hud, hooks=[], events=[], data={}, initialized=false, worldRestored=false, checkpoint=null, useHeld=false;
const name=story.name;
const ent=(n)=>Instance.FindEntityByName(n);
const human=(p)=>p instanceof CSPlayerPawn && p.IsValid() && !p.GetOriginalPlayerController().IsBot();
function pawn(){return Instance.GetAllPlayerControllers().find(p=>p.IsConnected()&&!p.IsBot())?.GetPlayerPawn();}
function log(text){events.push(text);if(events.length>256)events.shift();Instance.Msg(text);}
function variable(key,value){if(hud?.IsValid())hud.SetDialogVariableString(key==='subtitle'?'subtitle':'story',key,String(value));}
function subtitle(text,seconds=4){variable('subtitle',text);subtitleUntil=Instance.GetGameTime()+seconds;if(hud?.IsValid())hud.SetHasClass('subtitle','Hidden',!text||!story.hud.subtitles);}
function settings(){
 const s=story.settings;
 for(const c of ['mp_ignore_round_win_conditions 1','mp_roundtime '+s.round_minutes,
 'mp_roundtime_defuse '+s.round_minutes,'mp_roundtime_hostage '+s.round_minutes,
 'mp_freezetime 0','mp_warmup_end','mp_limitteams 0','mp_autoteambalance 0',
 'mp_respawn_on_death_t '+Number(s.respawn),'mp_respawn_on_death_ct '+Number(s.respawn),
 'bot_quota '+s.bots]) Instance.ServerCommand(c);
 Instance.SetRoundRemainingTime(s.round_minutes*60);
}
function readSave(){const raw=Instance.GetSaveData();const d=raw?JSON.parse(raw):{};
 if(!d||typeof d!=='object'||Array.isArray(d))throw Error('addon save is not a JSON object');return d;}
function save(key){
 data=readSave();data.cs2map_stories??={};
 const old=data.cs2map_stories[name]??{};
 const flags={...(old.flags??{})};flags[key]=true;
 data.cs2map_stories[name]={next:index+1,flags};
 Instance.SetSaveData(JSON.stringify(data));log('S_STORY '+name+' saved '+key);
}
function fire(n,input,value,delay=0){Instance.EntFireAtName({name:n,input,value,delay,activator:pawn()});}
async function actions(list,epoch){
 for(const a of list){if(epoch!==run)return;
  if(a.fire)fire(a.fire,a.input,a.value,a.delay??0);
  else if(a.light){fire(a.light,a.on?'Open':'Close');log('S_STORY '+name+' light '+a.light+' '+a.on);}
  else if(a.sound){fire(a._entity,'StartSound');log('S_STORY '+name+' sound '+a.sound);}
  else if(a.text||a.say)subtitle(a.text??a.say,a.seconds??4);
  else if(a.fade){fire(a._entity,'Fade');await Instance.Delay(a.seconds??1);}
  else if(a.shake)fire(a._entity,'StartShake');
  else if(a.teleport){const p=pawn();if(!p?.IsValid())throw Error('no live human for teleport');p.Teleport({position:{x:a.teleport[0],y:a.teleport[1],z:a.teleport[2]},velocity:{x:0,y:0,z:0}});}
  else if(a.spawn){const t=ent(a.spawn);if(!(t instanceof PointTemplate))throw Error('missing point_template '+a.spawn);const spawned=t.ForceSpawn();log('S_STORY '+name+' spawn '+a.spawn+' '+(spawned?.length??0));}
  else if(a.save)save(a.save);
  else if(a.end_text){variable('objective',a.end_text);subtitle(a.end_text,86400);}
  else if(a.camera){const p=pawn();if(!p?.IsValid())throw Error('no human for camera');const c=p.GetCustomCamera();c.Teleport({position:{x:a.camera[0],y:a.camera[1],z:a.camera[2]},angles:{pitch:a.ang[0],yaw:a.ang[1],roll:a.ang[2]}});c.SetMode(CustomCameraMode.CONTROLLED);log('S_STORY '+name+' camera controlled mode='+c.GetMode());await Instance.Delay(a.seconds??2);c.SetMode(CustomCameraMode.DISABLED);log('S_STORY '+name+' camera restored mode='+c.GetMode());}
 }
}
function disconnect(){for(const h of hooks)Instance.DisconnectOutput(h);hooks=[];}
function hook(n,output,cb){const target=ent(n);if(!target)throw Error('missing entity '+n);const id=Instance.ConnectOutput(target,output,cb);if(id===undefined)throw Error('cannot connect '+n+' '+output);hooks.push(id);}
function enter(i){
 disconnect();index=i;started=Instance.GetGameTime();kills=0;finishing=false;
 for(const s of story.stages)if(s._hint)fire(s._hint,'Disable');
 if(i>=story.stages.length){complete=true;variable('timer','');variable('chapter','COMPLETE');log('S_STORY '+name+' complete');return;}
 complete=false;const s=story.stages[i];variable('chapter',(i+1)+' / '+story.stages.length+' · '+s.id);
 const enteringPawn=pawn();useHeld=!!enteringPawn?.IsAlive()&&enteringPawn.IsInputPressed(CSInputs.USE);
 if(s.checkpoint)checkpoint=s.checkpoint;
 if(s._hint){fire(s._hint,'Enable');log('S_STORY '+name+' hint '+s.id+' enabled');}
 variable('objective',s.objective);variable('timer','');
 log('S_STORY '+s.id+' start');log('S_STORY '+name+' stage '+s.id+' start');
 if(s.say.length)subtitle(s.say.join('\n'),6);
 if(s.until.button)hook(s.until.button,'OnPressed',()=>done());
 if(s.until.zone)hook(s.until._zone,'OnStartTouch',({activator})=>{if(human(activator)){log('S_STORY '+name+' touch noclip='+activator.IsNoclipping());done();}});
}
async function done(){if(index<0||finishing||complete)return;finishing=true;
 const epoch=run,s=story.stages[index];
 if(s._hint){fire(s._hint,'Disable');log('S_STORY '+name+' hint '+s.id+' disabled');}
 try{await actions(s.then,epoch);if(epoch!==run)return;log('S_STORY '+s.id+' done');log('S_STORY '+name+' stage '+s.id+' done');enter(index+1);}
 catch(e){log('S_STORY_ERROR '+name+' '+s.id+' '+e);}
}
function reset(resume){
 run++;initialized=true;checkpoint=null;useHeld=false;settings();hud=ent(name+'_hud');
 if(!(hud instanceof CustomHudLayout))throw Error('missing CustomHudLayout '+name+'_hud');
 hud.SetHasClass('objective','Hidden',!story.hud.objective);hud.SetHasClass('timer','Hidden',!story.hud.timer);
 subtitle('',1);let next=0;if(ent(name+'_fade_clear'))fire(name+'_fade_clear','Fade');
 if(resume){try{data=readSave();next=data.cs2map_stories?.[name]?.next??0;}catch(e){log('S_STORY_ERROR '+name+' save '+e);}}
 next=Math.min(Math.max(0,next),story.stages.length);
 // Restore persistent world changes after a map reload, without replaying transient effects.
 for(const s of story.stages.slice(0,next))if(s.checkpoint)checkpoint=s.checkpoint;
 if(!worldRestored){for(const s of story.stages.slice(0,next))for(const a of s.then){if(a.fire)fire(a.fire,a.input,a.value,a.delay??0);if(a.light)fire(a.light,a.on?'Open':'Close');if(a.spawn)ent(a.spawn)?.ForceSpawn();}worldRestored=true;}
 enter(next);
 if(complete){const end=story.stages.flatMap(s=>s.then).findLast(a=>a.end_text);if(end)variable('objective',end.end_text);}
 Instance.SetNextThink(Instance.GetGameTime()+0.1);
}
Instance.OnActivate(()=>{settings();});
Instance.OnPlayerActivate(({player})=>{if(!player.IsBot())player.JoinTeam(2);});
Instance.OnPlayerReset(({player})=>{if(!human(player))return;if(!initialized){reset(story.settings.resume);return;}
 const epoch=run;if(checkpoint)Instance.QueueAfterThinks(()=>{if(epoch!==run||!player.IsValid()||!player.IsAlive()||!checkpoint)return;
 player.Teleport({position:{x:checkpoint[0],y:checkpoint[1],z:checkpoint[2]},velocity:{x:0,y:0,z:0}});log('S_STORY '+name+' checkpoint '+JSON.stringify(checkpoint));});});
Instance.OnRoundStart(()=>reset(story.settings.resume));
Instance.OnBeginRoundRestart(()=>{worldRestored=false;initialized=false;disconnect();});
Instance.OnPlayerChat(({player,text})=>{if(player&&!player.IsBot()&&story.stages[index]?.until.chat===text.trim())done();});
Instance.OnPlayerKill(({player,attacker})=>{if(human(attacker)&&player!==attacker&&player.GetTeamNumber()!==attacker.GetTeamNumber()&&story.stages[index]?.until.kills){kills++;if(kills>=story.stages[index].until.kills)done();}});
Instance.OnWeaponPickup(({weapon})=>{Instance.QueueAfterThinks(()=>{
 if(!weapon.IsValid()||!human(weapon.GetOwner()))return;
 const requested=story.stages[index]?.until.pickup;
 // Silenced variants share engine classes (MP5 -> weapon_mp7), but data names retain item identity.
 if(requested&&weapon.GetData().GetName().replace(/^weapon_/,'')===requested.replace(/^weapon_/,''))done();
});});
for(const s of story.stages)if(s.until.input)Instance.OnScriptInput(s.until.input,()=>{if(story.stages[index]===s)done();});
Instance.OnScriptInput('TestReset',()=>reset(false));
Instance.RegisterCheatCommand(name+'_state',(token)=>Instance.Msg('S_STORY_STATE '+token.trim()+' '+JSON.stringify({name,index,id:story.stages[index]?.id,complete,finishing,events})));
// Exercise actual engine events; these helpers cannot call done() directly.
Instance.RegisterCheatCommand(name+'_test_kill',()=>{const p=pawn();const victim=Instance.GetAllPlayerControllers().find(c=>c.IsBot()&&c.GetPlayerPawn()?.IsAlive())?.GetPlayerPawn();if(!victim||!p)throw Error('auto kills needs a live bot');victim.TakeDamage({damage:1000,attacker:p,inflictor:p});});
Instance.RegisterCheatCommand(name+'_test_pickup',(weapon)=>{weapon=weapon.trim();if(!/^weapon_[a-z0-9_]+$/i.test(weapon))throw Error('invalid test weapon');const p=pawn();if(!p?.IsAlive())throw Error('no live human');const old=p.FindWeapon(weapon);if(old)p.DestroyWeapon(old);p.GiveNamedItem(weapon,true);});
Instance.SetThink(()=>{try{
 if(!initialized&&pawn()?.IsAlive())reset(story.settings.resume);
 const s=story.stages[index];if(s&&!finishing&&s.until.time){const remaining=Math.max(0,s.until.time-(Instance.GetGameTime()-started));if(story.hud.timer)variable('timer',Math.ceil(remaining)+' s');if(!remaining)done();}
 const p=pawn(),pressed=!!p?.IsAlive()&&p.IsInputPressed(CSInputs.USE);
 if(s&&!finishing&&s.until.use&&pressed&&!useHeld){const target=ent(s.until.use);if(!target?.IsValid())throw Error('missing use target '+s.until.use);
  const eye=p.GetEyePosition(),a=p.GetEyeAngles(),r=Math.PI/180,cp=Math.cos(a.pitch*r);
  const hit=Instance.TraceLine({start:eye,end:{x:eye.x+Math.cos(a.yaw*r)*cp*96,y:eye.y+Math.sin(a.yaw*r)*cp*96,z:eye.z-Math.sin(a.pitch*r)*96},ignoreEntity:p});
  if(hit.hitEntity?.GetEntityName()===s.until.use){log('S_STORY '+name+' use '+s.until.use);done();}}
 useHeld=pressed;
 if(subtitleUntil&&Instance.GetGameTime()>subtitleUntil){subtitleUntil=0;subtitle('',0);}
}catch(e){log('S_STORY_ERROR '+name+' '+e);}Instance.SetNextThink(Instance.GetGameTime()+(story.stages[index]?.until.use?0.016:0.1));});
'''


def _prepare(spec, m=None):
    """Add generated targetnames; each action gets its own effect entity."""
    spec = copy.deepcopy(spec)
    operations = []
    base = spec['name']
    for i, stage in enumerate(spec['stages']):
        if m is not None and 'use' in stage['until']:
            matches=m.entities(targetname=stage['until']['use'])
            if len(matches)!=1 or not m.classname(matches[0]).startswith(('prop_','func_','weapon_')):
                raise ValueError('until.use needs one solid prop/brush/weapon target')
            # CS2's FGD makes func_brush "Never Solid" by default: the eye trace passes through it.
            if (m.classname(matches[0])=='func_brush'
                    and str(matches[0]['entity_properties'].get('Solidity','1')).strip() not in ('0','2')):
                raise ValueError(f'until.use target {stage["until"]["use"]} is a func_brush that is never solid '
                                 '(CS2 default Solidity=1), so the player cannot aim at it; add keyvalues {"Solidity": 2}')
        if stage.get('hint'):
            from .route import stage_target
            if not any(k in stage['until'] for k in ('button','use','zone')):
                raise ValueError('hint needs a spatial button/use/zone target')
            pos = stage_target(m, stage) if m is not None or 'zone' in stage['until'] else spec['origin']
            stage['_hint'] = f'{base}_hint_{i}'
            operations.append({'op':'add_entity','classname':'point_worldtext','origin':[pos[0],pos[1],pos[2]+48],
                               'keyvalues':{'targetname':stage['_hint'],'message':'◆ '+stage['objective'][:128],
                                            'enabled':0,'fullbright':1,'color':'255 215 100 255','font_name':'Arial',
                                            'font_size':64,'world_units_per_pixel':0.14,'justify_horizontal':1,
                                            'justify_vertical':1,'reorient_mode':1}})
        if 'zone' in stage['until']:
            stage['until']['_zone'] = f'{base}_zone_{i}'
            operations.append({'op': 'add_brush_entity', 'classname': 'trigger_multiple',
                               'keyvalues': {'targetname': stage['until']['_zone'], 'spawnflags': 4097},
                               'shape': {'shape': 'box', **stage['until']['zone']}})
        for j, action in enumerate(stage['then']):
            if m is not None and 'light' in action:
                matches=m.entities(targetname=action['light'])
                if len(matches)!=1 or m.classname(matches[0])!='func_movelinear':
                    raise ValueError('light needs an add_light start_off switch (func_movelinear)')
            for kind in ('sound', 'fade', 'shake'):
                if kind not in action:
                    continue
                target = f'{base}_{kind}_{i}_{j}'
                action['_entity'] = target
                if kind == 'sound':
                    cls, kv = 'point_soundevent', {'soundName': action[kind], 'toLocalPlayer': 1}
                elif kind == 'fade':
                    cls, kv = 'env_fade', {'duration': action.get('seconds', 1), 'spawnflags': 8 if action[kind] == 'out' else 1,
                                          'holdtime': 0, 'renderamt': 255, 'rendercolor': '0 0 0'}
                else:
                    cls, kv = 'env_shake', {'duration': action.get('seconds', 1), 'amplitude': action.get('amplitude', 4),
                                           'spawnflags': 5, 'frequency': 2.5}
                operations.append({'op': 'add_entity', 'classname': cls, 'origin': spec['origin'],
                                   'keyvalues': {'targetname': target, **kv}})
    if any('fade' in a for st in spec['stages'] for a in st['then']):
        # A stay-out fade survives TestReset/round restarts; reset() clears it with this entity.
        operations.append({'op': 'add_entity', 'classname': 'env_fade', 'origin': spec['origin'],
                           'keyvalues': {'targetname': base + '_fade_clear', 'duration': 0.1, 'spawnflags': 1,
                                         'holdtime': 0, 'renderamt': 255, 'rendercolor': '0 0 0'}})
    operations.append({'op': 'add_entity', 'classname': 'custom_hud_layout', 'origin': spec['origin'],
                       'keyvalues': {'targetname': base + '_hud', 'layout': f'panorama/layout/custom_game/{base}.vxml'}})
    return spec, operations


def generate(spec, m=None):
    prepared, operations = _prepare(spec, m)
    code = MARKER + json.dumps(spec, ensure_ascii=True, separators=(',', ':')) + '\n'
    code += RUNTIME.replace('const story=SPEC;', 'const story=' + json.dumps(prepared, ensure_ascii=True) + ';')
    from . import ui
    style = {k: v for k, v in (spec.get('hud') or {}).items() if k in ui.STYLE_KEYS}
    css = HUD_CSS + ui.override({'panel': '#story', 'title': '#chapter', 'hint': '#timer'}, style, 'top')
    if style:
        sub = {k: v for k, v in style.items() if k not in ('position', 'offset', 'width')}
        css += ui.override({'panel': '#subtitle'}, {**sub, 'position': 'bottom'}, 'bottom').replace(
            'margin: 0px 0px 110px 0px', 'margin: 0px 0px 150px 0px')
    return code, HUD_XML.replace('NAME', spec['name']), css, operations


def _write(project, path, text):
    ensure_inside(path, project.s.content_addon)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        rel = path.relative_to(project.s.content_addon)
        backup = project.s.backups / 'story' / str(time.time_ns()) / rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
    path.write_text(text, encoding='utf-8', newline='\n')


def add_story(project, m, op, refs):
    spec = validate(op)
    base = spec['name']
    code, xml, css, operations = generate(spec, m)
    targets = [x['keyvalues']['targetname'] for x in operations] + [base + '_script']
    if any(m.entities(targetname=x) for x in targets):
        raise ValueError(f'story {base} is already attached or a generated name is occupied; use a new name')
    source = project.s.content_addon / f'maps/scripts/mcp/{base}.js'
    ensure_inside(source, project.s.content_addon)
    if source.exists():
        first = source.read_text(encoding='utf-8').split('\n', 1)[0]
        if first != code.split('\n', 1)[0]:
            # Another map may still use the old story: replacing it would change that map silently.
            needle = f'maps/scripts/mcp/{base}.vjs'.encode()
            users = [f.stem for f in (project.s.content_addon / 'maps').glob('*.vmap') if needle in f.read_bytes()]
            if users:
                raise ValueError(f'story name {base} is used with different content by map(s) {users}; '
                                 'use a unique name or remove it from those maps')
    # Validate shape/entity operations on this unsaved VMAP before touching assets.
    ids = [project._apply(m, x, refs)['id'] for x in operations]
    syntax = scripting.syntax_check(code)
    if not syntax['ok']:
        raise ValueError(f'generated JavaScript failed syntax check: {syntax}')
    if _PREVIEW.get():
        # A placeholder with the final resource path allows the normal DMX
        # encoder and following operations to validate without touching assets.
        node = m.new_entity('point_script', {'targetname': base + '_script',
                                            'cs_script': f'maps/scripts/mcp/{base}.vjs'},
                            spec['origin'], defaults=project.catalog.defaults('point_script'))
        m.attach(node)
        if op.get('ref'):
            refs[op['ref']] = node['nodeID']
        return {'op': 'add_story', 'name': base, 'id': node['nodeID'], 'entity_ids': ids,
                'stages': len(spec['stages']), 'assets_written': False}
    xmlpath = project.s.content_addon / f'panorama/layout/custom_game/{base}.xml'
    csspath = project.s.content_addon / f'panorama/styles/custom_game/{base}.css'
    _write(project, xmlpath, xml)
    _write(project, csspath, css)
    job = compiler.compile_assets(project.s, project.jobs, [xmlpath, csspath], force=True, timeout=40)
    state = project.job_status(job.id, 45, 0)
    if state['state'] != 'succeeded' or not state.get('result', {}).get('ok'):
        raise ValueError(f'HUD compilation failed; see {state.get("log")}')
    result = scripting.script_write(project, base, code)
    if not result['ok']:
        raise ValueError(f'story script compilation failed; see {result.get("log")}')
    attached = scripting.add_script(project, m, {'name': base, 'targetname': base + '_script',
                                                'origin': spec['origin'], 'ref': op.get('ref')}, refs)
    return {'op': 'add_story', 'name': base, 'id': attached['id'], 'entity_ids': ids,
            'stages': len(spec['stages']), 'cs_script': result['cs_script'],
            'hud': f'panorama/layout/custom_game/{base}.vxml', 'logs': [state['log'], result['log']]}


def map_edit_wrapper(project, original):
    """Area-local preflight adapter while core extra_ops have no transaction context."""
    def edit(name, operations, dry_run=False, force=False):
        has_story = isinstance(operations, list) and any(isinstance(op, dict) and op.get('op') == 'add_story'
                                                       for op in operations)
        if not has_story:
            return original(name, operations, dry_run, force)
        with project._lock:
            if not dry_run:
                project._check_hammer(name, force)
            token = _PREVIEW.set(True)
            try:
                preview = original(name, operations, True, force)
            finally:
                _PREVIEW.reset(token)
            if dry_run:
                return preview
            return original(name, operations, False, force)
    return edit


def map_stories(project, m):
    """Read metadata only from safe script paths referenced by this map."""
    out = []
    for node in m.entities(classname='point_script'):
        rel = node['entity_properties'].get('cs_script', '')
        if not re.fullmatch(r'maps/scripts/mcp/[A-Za-z0-9_]+\.vjs', rel):
            continue
        path = project.s.content_addon / Path(rel).with_suffix('.js')
        ensure_inside(path, project.s.content_addon)
        if not path.exists():
            continue
        first = path.read_text(encoding='utf-8').split('\n', 1)[0]
        if first.startswith(MARKER):
            spec = validate(json.loads(first[len(MARKER):]))
            out.append((node, spec))
    return out


def check_story(project, m):
    problems = []
    for node, spec in map_stories(project, m):
        _, ops = _prepare(spec)
        required = [(x['keyvalues']['targetname'], x['classname']) for x in ops
                    if not x['keyvalues']['targetname'].endswith('_fade_clear')]   # absent on older maps
        for stage in spec['stages']:
            if 'button' in stage['until']:
                required.append((stage['until']['button'], 'func_button'))
            if 'use' in stage['until']:
                required.append((stage['until']['use'], None))
            for action in stage['then']:
                if 'fire' in action:
                    required.append((action['fire'], None))
                if 'spawn' in action:
                    required.append((action['spawn'], 'point_template'))
                if 'light' in action:
                    required.append((action['light'], 'func_movelinear'))
        for target, cls in sorted(set(required), key=lambda x: x[0]):
            matches = m.entities(targetname=target)
            if len(matches) != 1 or (cls and m.classname(matches[0]) != cls):
                problems.append(checks.problem('error', 'story_reference', node['nodeID'],
                                  f'{spec["name"]}: expected one {cls or "entity"} named {target}',
                                  'Create the named entity or correct the story target.'))
        for expected in ops:
            props = expected['keyvalues']
            targets = m.entities(targetname=props['targetname'])
            if len(targets) != 1:
                continue
            for key in ('layout', 'soundName'):
                if key in props and targets[0]['entity_properties'].get(key) != str(props[key]):
                    problems.append(checks.problem('error', 'story_resource', node['nodeID'],
                        f'{props["targetname"]}: {key} differs from the story definition', 'Restore the generated resource name.'))
        hudpath = project.s.game_addon / f'panorama/layout/custom_game/{spec["name"]}.vxml_c'
        if not hudpath.exists():
            problems.append(checks.problem('error', 'story_hud_missing', node['nodeID'],
                f'{spec["name"]}: compiled HUD is missing', 'Compile the generated XML/CSS before building.'))
        for stage in spec['stages']:
            for action in stage['then']:
                if 'fire' in action:
                    targets = m.entities(targetname=action['fire'])
                    if len(targets) == 1 and action['input'] not in project.catalog.io(m.classname(targets[0]))[0]:
                        problems.append(checks.problem('warning', 'story_input', node['nodeID'],
                            f'{action["fire"]}: input {action["input"]} is absent from installed FGD', 'Check entity_info.'))
    return problems
