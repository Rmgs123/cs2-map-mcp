// Behavioral harness for the engine-independent lifecycle, swept (kinematic) and simulated (physics) motion.
const assert = require('node:assert/strict');
const CSInputs={FORWARD:1,BACK:2,LEFT:4,RIGHT:8,WALK:16,DUCK:32,JUMP:64,USE:128,RELOAD:1024};
const CSMoveType={NONE:0,WALK:1}, CustomCameraMode={DISABLED:0,CONTROLLED:1,FOLLOW_POSITION:3}, CSGearSlot={KNIFE:2};
let now=0,think,activate,kill,reset,round,impact,modify,knifeAttack,wall=330;
class Entity {
 constructor(){this.pos={x:200,y:200,z:1};this.ang={pitch:0,yaw:0,roll:0};this.vel={x:0,y:0,z:0};this.valid=true;this.mode=1;this.health=0;}
 IsValid(){return this.valid;} IsAlive(){return this.alive!==false;} GetAbsOrigin(){return {...this.pos};}
 GetAbsAngles(){return {...this.ang};} GetAbsVelocity(){return {...this.vel};}
 GetEyeAngles(){return {...this.ang};} GetEyePosition(){return {...this.pos,z:this.pos.z+64};}
 Teleport(v){if(v.position)this.pos={...v.position};if(v.angles)this.ang={...v.angles};if(v.velocity)this.vel={...v.velocity};}
 Move(v){this.Teleport(v);} GetOwner(){return this.owner;}
 GetMoveType(){return this.mode;} SetMoveType(v){this.mode=v;} SetParent(){} GetEntityName(){return this.name;} SetEntityName(n){this.name=n;}
 GetHealth(){return this.health;} SetHealth(v){this.health=v;} SetMaxHealth(){} SetColor(c){this.color=c;}
 GetColor(){return this.color||{r:255,g:255,b:255,a:255};} SetModel(m){this.model=m;} GetModelName(){return this.model||'agents/models/x.vmdl';}
}
class CustomHudLayout extends Entity {SetHasClass(){} SetHasClassForPlayer(){} SetDialogVariableStringForPlayer(){} }
class CamEntity extends Entity {SetMode(v){this.cm=v;} GetMode(){return this.cm||0;} SetFollowConfig(c){this.follow=c;} }
const knife=new Entity(), pistol=new Entity();
// The client applies a server SetEyeAngles one tick late; meanwhile the mouse keeps turning the view.
class CSPlayerPawn extends Entity {
 constructor(){super();this.cam=new CamEntity();this.keys=0;this.pos={x:100,y:100,z:0};this.weapon=pistol;this.snap=null;}
 SetEyeAngles(a){this.snap={...a};}
 GetPlayerController(){return controller;} GetCustomCamera(){return this.cam;} IsNoclipping(){return false;}
 IsInputPressed(v){return !!(this.keys&v);} GetActiveWeapon(){return this.weapon;}
 FindWeaponBySlot(s){return s===CSGearSlot.KNIFE?knife:undefined;} SwitchToWeapon(w){this.weapon=w;}
}
const pawn=new CSPlayerPawn(), controller={IsValid:()=>true,IsBot:()=>false,GetPlayerSlot:()=>0,GetPlayerPawn:()=>pawn};
const entities={'d8_unit_body':new Entity(),'d8_unit_remote':new Entity(),'mcp_d_control_unit':new CustomHudLayout()};
for (const k of ['smoke','fire','boom','pilot']) entities['d8_unit_'+k]=new Entity();
const events=[], fired=[];
const drone=()=>entities.d8_unit_body;
const Instance={
 Msg:v=>events.push(v),GetGameTime:()=>now,FindEntityByName:n=>entities[n],GetAllPlayerControllers:()=>[controller],
 EntFireAtName:c=>fired.push(c.name+':'+c.input),EntFireAtTarget:c=>{c.target.enabled=c.input==='Enable'?true:c.input==='Disable'?false:c.target.enabled;},
 SetThink:f=>think=f,SetNextThink(){},
 // Eye traces hit the remote; short downward traces are the ground probe.
 TraceLine:({start,end})=>start.x===end.x&&start.y===end.y?{didHit:end.z<=0,startedInSolid:false,fraction:1}:
   {hitEntity:entities.d8_unit_remote},
 TraceBox:({start,end,maxs})=>{
   const crossing=end.x+maxs.x>wall && end.x>start.x;
   return {startedInSolid:false,didHit:crossing,fraction:crossing?(wall-maxs.x-start.x)/(end.x-start.x):1};
 },
 OnActivate:f=>activate=f,OnRoundStart(){},OnBeginRoundRestart:f=>round=f,OnBulletImpact:f=>impact=f,
 OnPlayerReset:f=>reset=f,OnPlayerKill:f=>kill=f,OnPlayerDisconnect(){},RegisterCheatCommand(){},OnScriptInput(){},
 OnModifyPlayerDamage:f=>modify=f,OnKnifeAttack:f=>knifeAttack=f
};
// Physics stand-in: the engine integrates velocity and gravity between thinks and stops at the floor.
function tick(keys=0,dt=.016,mouse=0){
 pawn.keys=keys;now+=dt;
 if(pawn.pendingSnap){pawn.ang={...pawn.pendingSnap};pawn.pendingSnap=null;}
 if(pawn.snap){pawn.pendingSnap=pawn.snap;pawn.snap=null;}
 pawn.ang.yaw+=mouse;
 if(config.physics){const b=drone();b.vel.z-=800*dt;for(const k of ['x','y','z'])b.pos[k]+=b.vel[k]*dt;
  if(b.pos.z<0){b.pos.z=0;b.vel.z=0;}}
 think();
}
const last=word=>events.filter(e=>e.includes(' '+word+' ')).pop();
function runAcceptance(){
 activate();tick(CSInputs.USE);assert.equal(pawn.mode,0);assert.equal(pawn.cam.GetMode(),3);
 tick(); // release entrance E
 const start={...drone().pos};tick(CSInputs.FORWARD|CSInputs.RIGHT,.05);
 assert.ok(Math.abs(Math.hypot(drone().pos.x-start.x,drone().pos.y-start.y)-20)<.01);
 for(let i=0;i<40;i++)tick(CSInputs.FORWARD,.05);
 assert.ok(drone().pos.x<=wall-55);assert.deepEqual(pawn.pos,{x:100,y:100,z:0});
 tick(CSInputs.USE);assert.equal(pawn.mode,1);assert.equal(pawn.cam.GetMode(),0);
 assert.deepEqual(drone().pos,{x:200,y:200,z:1});
 tick();tick(CSInputs.USE);kill({player:pawn});assert.equal(pawn.mode,1);assert.equal(pawn.cam.GetMode(),0);
 tick(CSInputs.USE); // held E is not a new edge after death
 assert.equal(pawn.cam.GetMode(),0);tick();tick(CSInputs.USE);tick();reset({player:pawn});assert.equal(pawn.mode,1);
 tick();tick(CSInputs.USE);tick();round();assert.equal(pawn.cam.GetMode(),0);
 console.log('CONTROL_RUNTIME_OK');
}
function knife_swing(){
 // The pilot's knife traced from the camera hits the drone: no damage counts.
 knifeAttack({weapon:knife});drone().health-=65;tick();assert.equal(drone().health,100000);assert.ok(!events.some(e=>e.includes(' damage ')));
 now+=.5;
}
function runPhysics(){
 activate();assert.equal(drone().health,100000);
 knife.owner=pawn;pistol.owner=pawn;
 tick(CSInputs.USE);assert.equal(pawn.cam.GetMode(),3);assert.equal(pawn.weapon,knife);tick();
 pawn.weapon=pistol;tick();assert.equal(pawn.weapon,knife,'no gun can be drawn while flying');
 assert.deepEqual(modify({attacker:pawn}),{abort:true});assert.equal(modify({attacker:knife}),undefined);
 assert.equal(pawn.name,'d8_unit_pilot_pawn','damage filter name while flying');
 knife.owner=pawn;knife_swing();
 // The real body and its knife are hidden; a still decoy of the same model stands at the anchor.
 assert.equal(pawn.GetColor().a,0);assert.equal(knife.GetColor().a,0);
 assert.equal(entities.d8_unit_pilot.enabled,true);assert.deepEqual(entities.d8_unit_pilot.pos,{x:100,y:100,z:0});
 for(let i=0;i<30;i++)tick(CSInputs.JUMP);
 assert.ok(drone().pos.z>60,'climbs against gravity');
 for(let i=0;i<20;i++)tick(CSInputs.FORWARD);
 assert.ok(drone().vel.x>200&&drone().vel.x<=400.01,'accelerates with inertia');
 assert.ok(drone().ang.pitch>3,'nose down while accelerating');
 for(let i=0;i<6;i++)tick();
 assert.ok(drone().vel.x>50,'keeps momentum after release');
 for(let i=0;i<60;i++)tick();
 assert.ok(Math.abs(drone().vel.x)<1,'brakes to a hover');
 const z=drone().pos.z;tick(CSInputs.USE);assert.equal(pawn.mode,1);assert.equal(pawn.weapon,pistol);tick();
 assert.equal(pawn.GetColor().a,255);assert.equal(knife.GetColor().a,255);assert.equal(entities.d8_unit_pilot.enabled,false);
 assert.equal(pawn.name,undefined===pawn.origName?'':pawn.origName,'name restored');
 drone().vel.x=300; // a hit knocks it away
 for(let i=0;i<120;i++)tick();
 assert.ok(Math.abs(drone().pos.z-z)<3,'hovers at the exit height');
 assert.ok(Math.abs(drone().vel.x)<5,'position hold recovers from a knock');
 drone().health-=30;tick();assert.ok(last('damage').includes('"hp":20'));assert.ok(fired.includes('d8_unit_smoke:Start'));
 drone().health-=36;tick();assert.ok(last('destroyed'));
 for(let i=0;i<60;i++)tick();
 assert.ok(drone().pos.z<1,'the wreck falls');
 tick(CSInputs.USE);tick();assert.ok(last('refused').includes('destroyed'));
 tick(0,5);tick(0,5.1);assert.ok(last('respawn').includes('"hp":50'));assert.equal(drone().health,100000);
 console.log('CONTROL_PHYSICS_OK');
}
