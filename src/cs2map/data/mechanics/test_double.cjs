// Engine double for Node tests of assembled mechanic scripts (mechanics.run_harness prepends it to the script,
// then the script, then the test body, all in one scope).
// Globals are only the names the script itself imports: Instance, CSInputs, CSMoveType, CustomCameraMode, CSGearSlot,
// CSDamageTypes, CSPlayerPawn, CSWeaponBase, CustomHudLayout, Entity. Everything else is under `D`:
//   D.pawn (the human, keys via tick), D.entities (FindEntityByName), D.H (engine callbacks by event name: D.H.OnActivate(),
//   D.H.think()), D.commands (cheat commands), D.inputs (script inputs), D.events (Instance.Msg lines), D.fired (EntFire
//   inputs), D.T (set D.T.traceLine/traceBox/traceSphere/traceBullet to script traces), D.tick(keys, dt), D.advance(seconds, keys),
//   D.byClass (FindEntitiesByClass: class name -> entities; given weapons land here too),
//   D.last(word) / D.count(word) (log lines by event), D.state(command), D.now(), D.assert.
const CSInputs = {FORWARD: 1, BACK: 2, LEFT: 4, RIGHT: 8, WALK: 16, DUCK: 32, JUMP: 64, USE: 128, ATTACK: 256, ATTACK2: 512, RELOAD: 1024,
                  SHOW_SCORES: 2048, LOOK_AT_WEAPON: 4096};
const CSMoveType = {NONE: 0, WALK: 1}, CustomCameraMode = {DISABLED: 0, CONTROLLED: 1, FOLLOW_POSITION: 3};
const CSGearSlot = {RIFLE: 0, PISTOL: 1, KNIFE: 2};
const CSDamageTypes = {GENERIC: 0, CRUSH: 1, BULLET: 2, SLASH: 4, BURN: 8, VEHICLE: 16, FALL: 32, BLAST: 64, CLUB: 128, SHOCK: 256};
const D = (() => {
    let now = 0;
    const d = {assert: require('node:assert/strict'), events: [], fired: [], H: {}, commands: {}, inputs: {}, entities: {},
               T: {traceLine: null, traceBox: null, traceSphere: null, traceBullet: null}, byClass: {}, now: () => now};
    d.Entity = class {
        constructor(name) { this.name = name; this.pos = {x: 0, y: 0, z: 0}; this.ang = {pitch: 0, yaw: 0, roll: 0}; this.vel = {x: 0, y: 0, z: 0};
            this.valid = true; this.mode = 1; this.health = 100; this.color = {r: 255, g: 255, b: 255, a: 255}; this.enabled = true; }
        IsValid() { return this.valid; } IsAlive() { return this.alive !== false; } IsWorld() { return !!this.world; }
        GetAbsOrigin() { return {...this.pos}; } GetAbsAngles() { return {...this.ang}; } GetAbsVelocity() { return {...this.vel}; }
        GetEyeAngles() { return {...this.ang}; } GetEyePosition() { return {...this.pos, z: this.pos.z + 64}; }
        Teleport(v) { if (v.position) this.pos = {...v.position}; if (v.angles) this.ang = {...v.angles}; if (v.velocity) this.vel = {...v.velocity};
            if (v.angularVelocity) this.angVel = {...v.angularVelocity}; this.teleports = (this.teleports || 0) + 1; }
        Move(v) { this.Teleport(v); } SetParent(p) { this.parent = p; } GetOwner() { return this.owner; } SetOwner(o) { this.owner = o; }
        GetMoveType() { return this.mode; } SetMoveType(v) { this.mode = v; }
        GetEntityName() { return this.name; } SetEntityName(n) { this.name = n; }
        GetHealth() { return this.health; } SetHealth(v) { this.health = v; } SetMaxHealth() {}
        TakeDamage(v) { this.health -= v.damage; this.lastDamage = v; return v.damage; }
        SetColor(c) { this.color = c; } GetColor() { return this.color; } SetModel(m) { this.model = m; } GetModelName() { return this.model || 'agents/models/x.vmdl'; }
        SetModelScale(s) { this.scale = s; } GetClassName() { return this.cls || 'prop_dynamic'; }
        GetTeamNumber() { return this.team ?? 2; }
        Glow(c) { this.glow = c || {r: 255, g: 255, b: 255}; } Unglow() { this.glow = null; } IsGlowing() { return !!this.glow; }
    };
    d.Weapon = class extends d.Entity {
        constructor(name, cls = 'weapon_awp') { super(name); this.cls = cls; this.clip = 10; this.reserve = 30; }
        GetClipAmmo() { return this.clip; } SetClipAmmo(n) { this.clip = n; } GetReserveAmmo() { return this.reserve; } SetReserveAmmo(n) { this.reserve = n; }
        GetData() { return {GetName: () => this.cls, GetDamage: () => 115}; }
    };
    d.Hud = class extends d.Entity {
        constructor(n) { super(n); this.texts = {}; this.classes = {}; }
        SetHasClass(panel, cls, on) { this.classes['*' + panel + cls] = on; }
        SetHasClassForPlayer(slot, panel, cls, on) { this.classes[slot + panel + cls] = on; }
        SetDialogVariableStringForPlayer(slot, panel, key, v) { this.texts[slot + panel + key] = v; this.text = v; }
        Reset() { this.texts = {}; this.classes = {}; }
    };
    d.Camera = class extends d.Entity { SetMode(v) { this.cm = v; } GetMode() { return this.cm || 0; } SetFollowConfig(c) { this.follow = c; } };
    d.Pawn = class extends d.Entity {
        constructor() { super('human'); this.cam = new d.Camera(); this.keys = 0; this.pos = {x: 100, y: 100, z: 0}; this.slots = {};
            this.knife = new d.Entity('knife'); this.pistol = new d.Entity('pistol'); this.knife.owner = this.pistol.owner = this; this.weapon = this.pistol;
            this.slots[CSGearSlot.KNIFE] = this.knife; this.slots[CSGearSlot.PISTOL] = this.pistol; }
        SetEyeAngles(a) { this.ang = {...a}; }
        GetGroundEntity() { return this.ground === false ? undefined : d.world; }
        SetArmor(v) { this.armor = v; } GetArmor() { return this.armor || 0; } SetHasHelmet(v) { this.helmet = v; }
        GetPlayerController() { return d.controller; } GetCustomCamera() { return this.cam; } IsNoclipping() { return false; }
        IsInputPressed(v) { return !!(this.keys & v); } WasInputJustPressed(v) { return !!(this.justKeys & v); }
        GetActiveWeapon() { return this.weapon; } FindWeaponBySlot(s) { return this.slots[s]; } SwitchToWeapon(w) { this.weapon = w; }
        DestroyWeapon(w) { for (const k in this.slots) if (this.slots[k] === w) delete this.slots[k]; w.valid = false; if (this.weapon === w) this.weapon = undefined; }
        GiveNamedItem(cls, deploy) { const w = new d.Weapon('', cls); w.owner = this; (d.byClass[cls] = d.byClass[cls] || []).push(w); if (deploy) this.weapon = w; }
    };
    d.world = new d.Entity('world'); d.world.world = true;
    d.pawn = new d.Pawn();
    d.outputs = {};
    d.controller = {IsValid: () => true, IsBot: () => false, IsConnected: () => true, GetPlayerSlot: () => 0, GetPlayerPawn: () => d.pawn};
    d.Instance = {
        Msg: v => d.events.push(v), GetGameTime: () => now, FindEntityByName: n => d.entities[n], FindEntitiesByName: n => d.entities[n] ? [d.entities[n]] : [],
        GetAllPlayerControllers: () => [d.controller], GetMapName: () => 'double', IsFreezePeriod: () => false,
        EntFireAtName: c => d.fired.push(c.name + ':' + c.input),
        EntFireAtTarget: c => { if (c.input === 'Enable') c.target.enabled = true; else if (c.input === 'Disable') c.target.enabled = false; d.fired.push('@' + c.input); },
        SetThink: f => { d.H.think = f; }, SetNextThink() {},
        TraceLine: c => d.T.traceLine ? d.T.traceLine(c) : {didHit: false, startedInSolid: false, fraction: 1, end: c.end},
        TraceBox: c => d.T.traceBox ? d.T.traceBox(c) : {didHit: false, startedInSolid: false, fraction: 1},
        TraceSphere: c => d.T.traceSphere ? d.T.traceSphere(c) : {didHit: false, startedInSolid: false, fraction: 1, end: c.end},
        TraceBullet: c => d.T.traceBullet ? d.T.traceBullet(c) : [],
        FindEntitiesByClass: n => [...(d.byClass[n] || [])],
        RegisterCheatCommand: (n, f) => { d.commands[n] = f; }, OnScriptInput: (n, f) => { d.inputs[n] = f; },
        ServerCommand: c => { (d.server = d.server || []).push(c); }, SetRoundRemainingTime: t => { d.roundTime = t; },
        GetRoundRemainingTime: () => d.roundTime ?? 0,
        ConnectOutput: (target, output, f) => { (d.outputs[target.name + ':' + output] = d.outputs[target.name + ':' + output] || []).push(f); return 1; },
    };
    for (const e of ['OnActivate', 'OnRoundStart', 'OnBeginRoundRestart', 'OnPlayerReset', 'OnPlayerKill', 'OnPlayerDisconnect', 'OnPlayerDamage',
        'OnModifyPlayerDamage', 'OnBulletImpact', 'OnKnifeAttack', 'OnGunFire', 'OnWeaponPickup', 'OnPlayerJump', 'OnPlayerLand',
        'OnPlayerChat', 'OnCustomHudClicked']) d.Instance[e] = f => { d.H[e] = f; };
    // Fire a connected entity output (ConnectOutput) with an activator: D.output('zone1', 'OnStartTouch', D.pawn).
    d.output = (name, output, activator) => { for (const f of d.outputs[name + ':' + output] || []) f({activator, caller: d.entities[name]}); };
    // One game tick: keys held by the human, time advances by dt, then the script's think runs.
    d.tick = (keys = 0, dt = .016) => { d.pawn.keys = keys; d.pawn.justKeys = keys & ~(d.tick.prev || 0); d.tick.prev = keys; now += dt; d.H.think(); };
    d.advance = (seconds, keys = 0, dt = .016) => { for (let t = 0; t < seconds; t += dt) d.tick(keys, dt); };
    d.last = word => d.events.filter(e => e.includes(' ' + word + ' ')).pop();
    d.count = word => d.events.filter(e => e.includes(' ' + word + ' ')).length;
    d.state = (cmd, token = 't') => { d.commands[cmd](token); return JSON.parse(d.last('state').split(' state ')[1]); };
    return d;
})();
const Instance = D.Instance, Entity = D.Entity, CustomHudLayout = D.Hud, CSPlayerPawn = D.Pawn, CSWeaponBase = D.Weapon;
