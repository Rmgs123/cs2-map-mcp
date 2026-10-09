"""Execute generated JS against an API double; live engine acceptance is separate."""
import json
import subprocess
import time
import unittest

from cs2map import scripting, story
from cs2map.config import PROJECT_ROOT


class StoryRuntimeRound4(unittest.TestCase):
    def test_use_edges_hints_light_and_checkpoint_epoch(self):
        node=scripting._node()
        if node is None:
            self.skipTest('bundled/local Node unavailable')
        spec=story.validate({'name':'r_runtime_unit','stages':[
            {'id':'use_note','objective':'Read','until':{'use':'r_note'},'hint':True,
             'checkpoint':[10,20,8],'then':[{'light':'r_lamp','on':True}]},
            {'id':'wait_end','objective':'Wait','until':{'zone':{'mins':[90,-20,0],'maxs':[120,20,128]}},
             'hint':True,'checkpoint':[100,0,8]}]})
        code=story.generate(spec)[0]
        code='\n'.join(line for line in code.splitlines() if not line.startswith('import '))
        mock=r'''
import assert from 'node:assert/strict';
class CSPlayerPawn {}
class CustomHudLayout {IsValid(){return true;} SetDialogVariableString(){} SetHasClass(){}}
class PointTemplate {}
const CustomCameraMode={};
let pressed=false,hitName='r_other',gameTime=1,queued=[];
const fired=[],teleported=[],callbacks={},inputs={};
const testPawn=new CSPlayerPawn();
const controller={IsBot:()=>false,IsConnected:()=>true,GetPlayerPawn:()=>testPawn};
Object.assign(testPawn,{IsValid:()=>true,IsAlive:()=>true,GetOriginalPlayerController:()=>controller,
 IsInputPressed:()=>pressed,GetEyePosition:()=>({x:0,y:0,z:64}),GetEyeAngles:()=>({pitch:0,yaw:0,roll:0}),
 Teleport:(values)=>teleported.push(values)});
const testHud=new CustomHudLayout(),CSInputs={USE:128};
const Instance={
 GetGameTime:()=>gameTime,GetAllPlayerControllers:()=>[controller],
 FindEntityByName:(n)=>n.endsWith('_hud')?testHud:{IsValid:()=>true},
 Msg:()=>{},ServerCommand:()=>{},SetRoundRemainingTime:()=>{},
 EntFireAtName:(e)=>fired.push(e),ConnectOutput:()=>1,DisconnectOutput:()=>{},
 GetSaveData:()=>'',SetSaveData:()=>{},QueueAfterThinks:(fn)=>queued.push(fn),
 TraceLine:()=>({hitEntity:{GetEntityName:()=>hitName}}),
 OnActivate:(fn)=>callbacks.activate=fn,OnPlayerActivate:(fn)=>callbacks.activatePlayer=fn,
 OnPlayerReset:(fn)=>callbacks.resetPlayer=fn,OnRoundStart:(fn)=>callbacks.round=fn,
 OnBeginRoundRestart:(fn)=>callbacks.begin=fn,OnPlayerChat:()=>{},OnPlayerKill:()=>{},OnWeaponPickup:()=>{},
 OnScriptInput:(name,fn)=>inputs[name]=fn,RegisterCheatCommand:()=>{},
 SetThink:(fn)=>callbacks.think=fn,SetNextThink:()=>{}
};
'''
        assertions=r'''
callbacks.round();
assert.equal(index,0);
assert(fired.some(e=>e.name==='r_runtime_unit_hint_0'&&e.input==='Enable'));
pressed=true;callbacks.think();await Promise.resolve();assert.equal(index,0,'wrong target');
hitName='r_note';callbacks.think();await Promise.resolve();assert.equal(index,0,'held key cannot advance');
pressed=false;callbacks.think();pressed=true;callbacks.think();await Promise.resolve();await Promise.resolve();
assert.equal(index,1,'new use press advances');
assert(fired.some(e=>e.name==='r_lamp'&&e.input==='Open'));
assert(fired.some(e=>e.name==='r_runtime_unit_hint_0'&&e.input==='Disable'));
assert(fired.some(e=>e.name==='r_runtime_unit_hint_1'&&e.input==='Enable'));
callbacks.resetPlayer({player:testPawn});queued.shift()();
assert.deepEqual(teleported[0].position,{x:100,y:0,z:8});
assert.deepEqual(teleported[0].velocity,{x:0,y:0,z:0});
callbacks.resetPlayer({player:testPawn});inputs.TestReset();queued.shift()();
assert.equal(teleported.length,1,'reset cancels old checkpoint callback');
assert.equal(index,0);assert.deepEqual(checkpoint,[10,20,8]);
console.log('R_RUNTIME_OK');
'''
        folder=PROJECT_ROOT/'.local/scratch/R'
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/f'runtime-test-{time.time_ns()}.mjs'
        path.write_text(mock+code+assertions,encoding='utf-8')
        result=subprocess.run([str(node),str(path)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('R_RUNTIME_OK',result.stdout)
