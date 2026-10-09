"""add_weapon with the native shot (damage, magazine, colour, model, sound, sparks, knockback) and the railgun shot
engine, against small engine doubles."""
import json
import subprocess
import unittest
from unittest.mock import patch

from cs2map import weapons
from cs2map.project import OpError
from cs2map.scripting import _node

HARNESS = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=JSON.parse(require('node:fs').readFileSync(0,'utf8')).replace(/^import[^\n]*$/m,'');
let modify,damaged,fire,reload,pickup,think,activate,now=0;const fired=[];
class Data{GetDamage(){return 53;} GetName(){return 'weapon_deagle';}}
class Weapon{constructor(){this.clip=7;this.reserve=35;this.model='w_deagle';}IsValid(){return true;}GetData(){return new Data();}
 GetClipAmmo(){return this.clip;}SetClipAmmo(v){this.clip=v;}GetReserveAmmo(){return this.reserve;}SetReserveAmmo(v){this.reserve=v;}
 SetModel(m){this.model=m;}SetColor(c){this.color=c;}GetOwner(){return this.owner;}GetModelName(){return this.model;}GetAbsOrigin(){return {x:0,y:0,z:0};}}
class Pawn{constructor(){this.pos={x:100,y:0,z:0};this.vel={x:0,y:0,z:0};this.hp=100;}IsValid(){return true;}IsAlive(){return true;}
 GetAbsOrigin(){return {...this.pos};}GetEyePosition(){return {x:0,y:0,z:64};}GetAbsVelocity(){return {...this.vel};}GetHealth(){return this.hp;}
 Teleport(v){if(v.velocity)this.vel=v.velocity;if(v.position)this.pos=v.position;}GetPlayerController(){return {GetPlayerSlot:()=>0};}SwitchToWeapon(w){this.weapon=w;}}
const gun=new Weapon(),stock=new Weapon(),shooter=new Pawn(),victim=new Pawn(),fx={IsValid:()=>true,Teleport(){}};
const Instance={Msg(){},FindEntitiesByName:n=>n==='w9_cannon_weapon'?[gun]:[],FindEntityByName:()=>fx,GetGameTime:()=>now,
 GetAllPlayerControllers:()=>[],FindEntitiesByClass:()=>[],SetNextThink(){},SetThink:f=>think=f,OnActivate:f=>activate=f,
 OnRoundStart(){},OnPlayerReset(){},RegisterCheatCommand(){},
 OnModifyPlayerDamage:f=>modify=f,OnPlayerDamage:f=>damaged=f,OnGunFire:f=>fire=f,OnBulletImpact(){},OnWeaponPickup:f=>pickup=f,
 EntFireAtName:c=>fired.push(c.name+':'+c.input)};
vm.runInNewContext(source,{Instance,Math,JSON,Map});
activate();
assert.equal(gun.model,'models/mcp_x/zapper/zapper.vmdl');assert.equal(gun.clip,12);assert.equal(gun.reserve,24);
assert.equal(modify({weapon:gun,damage:53*4}).damage,90*4,'headshot multiplier kept');
assert.equal(modify({weapon:stock,damage:53}),undefined,'other guns untouched');
damaged({player:victim,attacker:shooter,weapon:gun,damage:90});assert.ok(victim.vel.x>299&&victim.vel.z>0,'knockback');
gun.owner=shooter;fire({weapon:gun});assert.ok(fired.includes('w9_cannon_fire0:StartSound'));
gun.clip=3;think();gun.clip=7;gun.reserve=20;think();assert.equal(gun.clip,12);assert.equal(gun.reserve,15,'magazine refilled from reserve');
gun.model='w_deagle';pickup({weapon:gun});assert.equal(gun.model,'w_deagle','held: stock model (a model off the skeleton floats in first person)');
assert.equal(shooter.weapon,gun,'switched to the picked gun');
gun.owner=undefined;think();assert.equal(gun.model,'models/mcp_x/zapper/zapper.vmdl','dropped: own model again');
console.log('CUSTOM_WEAPON_OK');
'''


def spec():
    return {'op': 'add_weapon', 'name': 'cannon', 'base': 'weapon_deagle', 'origin': [0, 0, 40], 'damage': 90,
            'clip': 12, 'reserve': 24, 'model': 'models/mcp_x/zapper/zapper.vmdl', 'color': [255, 140, 40],
            'sound': 'mcp.x8_zap', 'impact': True, 'knockback': 300}


class NativeShot(unittest.TestCase):
    def test_validation(self):
        s = weapons.validate(spec())
        self.assertEqual((s['base'], s['targetname'], s['clip']), ('weapon_deagle', 'w9_cannon_weapon', 12))
        self.assertIsNone(weapons.validate({'name': 'x', 'base': 'ak47', 'origin': [0, 0, 0]})['damage'])
        for bad in ({'base': 'weapon_hegrenade'}, {'damage': 0}, {'clip': 999},
                    {'color': [1, 2]}, {'model': '../x.vmdl'}, {'name': 'Bad'}, {'speed': 3}):
            with self.subTest(bad=bad), self.assertRaises(OpError):
                weapons.validate({**spec(), **bad})

    def test_runtime(self):
        node = _node()
        if node is None:
            self.skipTest('Node.js unavailable')
        code = weapons.render(weapons.validate(spec()))
        result = subprocess.run([str(node), '-e', HARNESS], input=json.dumps(code), capture_output=True,
                                text=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('CUSTOM_WEAPON_OK', result.stdout)

    def test_entities(self):
        from types import SimpleNamespace
        from cs2map.config import load_settings
        from cs2map.vmap import VMap
        m = VMap.from_template(load_settings().cs2_path, 'empty')
        p = SimpleNamespace(catalog=SimpleNamespace(defaults=lambda cls: {}))
        with patch('cs2map.ext.models.asset_path'), patch('cs2map.sound.event_exists', return_value=True), \
             patch('cs2map.scripting.script_write', return_value={'ok': True, 'cs_script': 'maps/scripts/mcp/x.vjs'}):
            out = weapons.add_weapon(p, m, {**spec(), 'model': None}, {})
            classes = sorted(m.classname(n) for n in m.entities())
            self.assertEqual(classes, ['info_particle_system', 'point_script', 'point_soundevent', 'weapon_deagle'])
            self.assertEqual(out['base'], 'weapon_deagle')
            with self.assertRaises(OpError):
                weapons.add_weapon(p, m, {**spec(), 'model': None}, {})


FIRE_HARNESS = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=JSON.parse(require('node:fs').readFileSync(0,'utf8')).replace(/^import[^\n]*$/m,'');
let think,activate,now=0,keys=0;const fired=[],msgs=[];
class CSPlayerPawn{constructor(){this.hp=100;this.vel={x:0,y:0,z:0};}IsValid(){return true;}IsAlive(){return true;}
 GetEyePosition(){return {x:0,y:0,z:64};}GetEyeAngles(){return {...(this.ang||{pitch:0,yaw:0,roll:0})};}SetEyeAngles(a){this.ang=a;}
 GetAbsOrigin(){return {x:500,y:0,z:0};}GetAbsVelocity(){return this.vel;}Teleport(v){if(v.velocity)this.vel=v.velocity;}
 GetHealth(){return this.hp;}TakeDamage(d){this.hp-=d.damage;this.last=d;return d.damage;}GetClassName(){return 'player';}GetEntityName(){return '';}
 IsInputPressed(i){return !!(keys&i);}GetActiveWeapon(){return this.weapon;}GetPlayerController(){return this.ctl;}IsWorld(){return false;}}
const world={IsValid:()=>true,IsWorld:()=>true};
class Weapon{constructor(){this.clip=10;this.reserve=30;}IsValid(){return true;}GetData(){return {GetDamage:()=>115,GetName:()=>'weapon_awp'};}
 GetClipAmmo(){return this.clip;}SetClipAmmo(v){this.clip=v;}GetReserveAmmo(){return this.reserve;}SetReserveAmmo(v){this.reserve=v;}
 SetModel(){}SetColor(c){this.color=c;}GetOwner(){return shooter;}GetModelName(){return 'awp';}GetColor(){return this.color;}GetAbsOrigin(){return {x:0,y:0,z:0};}}
const gun=new Weapon(),shooter=new CSPlayerPawn(),victim=new CSPlayerPawn();
shooter.weapon=gun;shooter.ctl={GetPlayerSlot:()=>0,GetPlayerPawn:()=>shooter};
const ent={IsValid:()=>true,Teleport(v){this.pos=v.position;},SetModelScale(v){this.scale=v;},SetColor(c){this.color=c;}};
const hud={IsValid:()=>true,Reset(){},SetDialogVariableStringForPlayer(s,p,k,v){this.text=v;},SetHasClassForPlayer(){}};
const Instance={Msg:m=>msgs.push(m),FindEntitiesByName:n=>n==='w9_rail_weapon'?[gun]:[],FindEntityByName:n=>n==='w9_rail_hud'?hud:ent,
 GetGameTime:()=>now,SetNextThink(){},SetThink:f=>think=f,OnActivate:f=>activate=f,OnRoundStart(){},OnPlayerReset(){},RegisterCheatCommand(){},
 OnModifyPlayerDamage(){},OnPlayerDamage(){},OnGunFire(){},OnBulletImpact(){},OnWeaponPickup(){},FindEntitiesByClass:()=>[],
 GetAllPlayerControllers:()=>[shooter.ctl],EntFireAtName:c=>fired.push(c.name+':'+c.input),EntFireAtTarget:c=>fired.push('rod:'+c.input),
 // A 16-unit wall at x=300..316 with the victim behind it at x=500.
 TraceBullet:({start})=>start.x<300?[{hitEntity:world,position:{x:300,y:0,z:64},damage:900}]:[{hitEntity:victim,position:{x:500,y:0,z:64},damage:800}],
 TraceLine:({start,end})=>start.x<316&&start.x>300?{startedInSolid:true}:{didHit:true,startedInSolid:false,end:{x:316,y:0,z:64}}};
vm.runInNewContext(source,{Instance,CSPlayerPawn,CSInputs:{ATTACK:256,RELOAD:1024},CSDamageTypes:{SHOCK:256},Math,JSON,Map,Error,String});
activate();assert.equal(gun.clip,0,'native shot disabled');assert.deepEqual(Object.values(gun.color),[40,120,255,255]);
const tick=(k,dt=.016)=>{keys=k;now+=dt;think();};
for(let i=0;i<30;i++)tick(256);assert.equal(victim.hp,100,'still charging at .48 s');assert.match(hud.text,/CHARGE \[#{5}/);
for(let i=0;i<25;i++)tick(256);assert.ok(victim.hp<0,'fired through the wall');assert.ok(fired.includes('rod:Enable'),'beam rods shown');assert.ok(!fired.includes('rod:Disable'),'beam still lit');
assert.ok(fired.includes('w9_rail_charge:StartSound'));assert.ok(shooter.ang.pitch<0,'recoil');assert.match(hud.text,/RAIL  \|\|\./);
const hp=victim.hp;for(let i=0;i<20;i++)tick(256);assert.equal(victim.hp,hp,'cooldown');
tick(0);tick(1024);tick(0,3);tick(0);assert.match(hud.text,/RAIL  \|\|\|/,'reloaded');assert.ok(fired.includes('rod:Disable'),'beam faded out and hidden');
assert.ok(ent.color.a<255,'rods dimmed while fading');
console.log('RAILGUN_OK');
'''


class Railgun(unittest.TestCase):
    def test_charge_penetrating_shot_beam_cooldown_reload(self):
        node = _node()
        if node is None:
            self.skipTest('Node.js unavailable')
        op = {'name': 'rail', 'base': 'awp', 'origin': [0, 0, 0], 'color': [40, 120, 255], 'title': 'RAIL',
              'fire': {'charge': .8, 'cooldown': 1, 'magazine': 3, 'reload': 2, 'damage': 900, 'penetration': 20, 'thickness': 64,
                       'recoil': 5, 'charge_sound': 'mcp.x', 'beam': {'color': [60, 140, 255], 'width': 6}}}
        code = weapons.render(weapons.validate(op))
        result = subprocess.run([str(node), '-e', FIRE_HARNESS], input=json.dumps(code), capture_output=True,
                                text=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        self.assertIn('RAILGUN_OK', result.stdout)
