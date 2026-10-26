# CS2 scripts

| Task | Take |
| --- | --- |
| Write and compile a script | `script_write` + `map_edit` `add_script` |
| Find the API | `script_api` |
| Play a scenario with real input | `script_test` |
| A whole chapter without code | `add_story` (section `story`) |
| Own mechanic with the tested pieces (HUD, E to control, cameras, HP, the shot engine) | `// @requires ...` in `script_write` (below) |

`script_write(name, code)` first checks the JavaScript syntax with Node.js (an ES module, as CS2 loads it). A syntax error is refused with the line, the source line and the message before writing addon assets. Diagnostic `.mjs` sources are retained under the project's `.local/scratch/<agent>/`, without using or deleting system temp files. Then it writes `maps/scripts/mcp/<name>.js` (the previous source is backed up), compiles it with `resourcecompiler` and returns `{name, ok, cs_script, source, syntax_checked, compile_state, log}` plus `errors` on failure. `ok` is true only for a succeeded job, no compiler failures and an existing `.vjs_c`. Names contain letters, digits and underscores only. The syntax check does not know the CS2 API: a misspelled method still fails only at runtime, so check the console after loading the map (below).

`script_api(query="", limit=20)` searches the installed Valve declaration file `point_script.d.ts` from the `cs_script_demo` addon and returns matching declarations with their nearest comments, preserving multiline signatures. An exact class/interface/enum name (e.g. `CustomHudLayout`, `CSPlayerPawn`, `CameraFollowConfig`, `Domain`) returns its **entire body with comments**, `complete:true`, without truncating it at `limit`. With an empty query it lists common `Instance.On*` events and core methods. The result includes the exact installed file path, so the answer is version-specific.

For a short JSON campaign, use `map_edit` / `add_story`; see [Story maps](story.md). `script_test(map,steps="auto",timeout=90)` tests that campaign, or explicit manual steps test any custom script. It continuously collects console errors during screenshots, movement and waits, returns a compact summary and a full JSON log. A real JS exception makes `ok=false` even with no expectation step.


Attach a compiled script with `map_edit`:

```json
{"op":"add_script","name":"round_log","origin":[0,0,8],"ref":"script"}
```

The operation creates `point_script` with `cs_script = maps/scripts/mcp/round_log.vjs` and `targetname` `mcp_script_round_log` (or your `targetname`). The source must already have been compiled. `script_write(name, code, reload=true)` on a map loaded in CS2 restarts its named `point_script` entities with the new code, without reloading the map (script state starts over). A minimal script is:

```js
import { Instance } from "cs_script/point_script";
Instance.Msg("round_log: loaded");
Instance.OnRoundStart(() => Instance.Msg("round_log: round started"));
```

The full API with comments is in the CS2 install: `content/csgo_addons/cs_script_demo/maps/scripts/point_script.d.ts` (events `Instance.On*`, `EntFireAtName({name, input, value, delay})`, `ServerCommand`, `GiveNamedItem`, traces, `SetThink`). Useful `point_script.d.ts` APIs include `Instance.Msg`, `Instance.OnRoundStart`, `Instance.OnPlayerReset`, `Instance.OnWeaponPickup`, `Instance.RegisterCheatCommand`, entity validity checks, and player/pawn methods such as `GetAbsOrigin`, `GetHealth`, `IsAlive`, `IsNoclipping`, and `FindWeaponBySlot`. Keep callbacks short and check `IsValid()` before using an entity. Verify in VConsole by looking for `C_ROUND_EVENT round_start` or a token-matched `C_MCP_STATE` line after loading the map. The quality playtest installs the latter diagnostic script as a point_script entity on its owned fixtures.

## `script_test`: play a scenario like a player

`script_test(map, steps, timeout=90)` runs under one game lock with the game window in front, collects every console line and JS error and returns `ok`, `checks`, `errors`, `shots`, `log` (a JS exception makes `ok=false`). Call `game_reset(map)` before and after. Do not touch the mouse or keyboard while it runs. `steps="auto"` plays the map's `add_story`.

| Step (one action each) | Meaning |
| --- | --- |
| `{"cmd":"console lines","wait":1}` | console commands (several: one per line), optional seconds to wait after |
| `{"walk":[x,y,z]}` / `{"go":[x,y,z]}` | teleport (feet) / walk there with real keys |
| `{"use":true}` | E for 0.15 s (enter a vehicle, take a gun, press a button in view) |
| `{"hold":{"key":"forward","seconds":2}}` | hold `attack attack2 use forward back left right jump duck reload walk turnleft turnright` (`walk` = SHIFT; `left/right` strafe) |
| `{"aim":[x,y,z]}` / `{"aim":"entity"}` | turn the view there (waits 1 s for the client) |
| `{"mouse":{"dx":40,"dy":-10}}` | real mouse motion in pixels (cameras that read the mouse) |
| `{"click":[0.43,0.52]}` | a real left click at that point of the game window (fractions): HUD buttons in cursor mode (section `ui`) |
| `{"expect":"regex","within":3}` | a console line after the previous action |
| `{"shot":"name"}` | a frame of what the player sees now (no noclip; vehicle and drone cameras stay) |
| `{"spread":{"shots":5,"interval":0.5,"max_deg":1}}` | fire that many single shots where the view points and pair each with its first impact by game tick (an impact can come before its gun-fire event): the result's `spread` gives `deg_mean/max` and `miss_mean/max` (units off the view line), every shot in the log; `max_deg` makes it a check. Riders of a moving vehicle: measured 0.2° parked, ~0.75° at 330 u/s (the ~1.4-tick lag) |
| `{"press":"button"}`, `{"fire":"name input"}`, `{"say":"text"}`, `{"wait":2}`, `{"focus":true}` | button Press, entity input, chat, pause, window focus |

Mechanics print telemetry on a cheat command `<name>_state <token>` (e.g. `d8_control_drone1_state t1`, `v9_control_jeep_state t1`, `t9_turret_gun1_state t1`, `m9_range_state t1`, `w9_rail_state`): position, HP, camera, the player's pawn and the last 8 events — `expect` the token. Several keys at once: `{"cmd":"+sprint"}` then `hold` another key, `{"cmd":"-sprint"}` after.

## Own mechanic on the shared pieces

`cs_script` has no imports, but `script_write` puts the project's tested pieces in front of your code: begin the source with `// @requires core, hud, seat, camera, health, fire` (any of them; their dependencies come along, imports are merged with yours). `// @fire <prefix> {config}` (the `fire` object of `add_weapon`: damage, beam, projectile, ...) adds `FIRE.<prefix> = {fire, entities}`; `add_script` then creates those entities (HUD, beam rods, rockets, sounds) next to the script. The result's `pieces` lists what was included.

| Piece | Make it | Gives |
| --- | --- | --- |
| core | `Core({tag:"TOWER", name:"tower", state:"tower_state"})` | `log(event, data)` → console `TOWER tower event {...}`; `on(event, fn)` (many listeners per engine event); `command(name, fn)`; `pawns({bots:true})`; `valid`, `add`, `dist`, `basis(angles)` → `{f,l,r,u}`; `eyeRay(p, distance)`; `sound(names, at)`; `fx(name, input, value, delay)`; `state(extra)` (a telemetry command) |
| hud | `Hud({core, name, panel, variable})` on a `custom_hud_layout` | `find()`, `put(p, text)`, `hide(p)`, `flag(p, cls, on)` |
| camera | `Camera({core, follow: () => body, views: {forward, up, right, chase}})` | `start(p)`, `stop()`, `update(p)` (R switches views) |
| seat | `Seat({core, camera, hud, target: () => body, reach, onEnter, onExit})` | E takes control and gives it back (frozen player, stand-in, safe exit spot), `update(humans)`, `player`, `exit(reason)` |
| health | `Health({core, body: () => body, max, native, effects, respawn, spawn})` | hit points, wreck, respawn (docs/specs/mechanics.md) |
| fire | `Fire({core, fire: FIRE.<p>.fire, entities: FIRE.<p>.entities})` | `setup()`, `newState()`, `step(st, {trigger, reload, src:{shooter?, origin, angles, inflictor, muzzle?}}, now, dt)`, `text(st, now)`; without a player `shooter` it traces lines (kills then count as suicides) |

A sentry tower that fires at the nearest visible player (verified on `w9_tower`):

```js
// @requires core, fire
// @fire tower {"damage": 15, "cooldown": 0.7, "magazine": 250, "reload": 0.5, "range": 1400, "recoil": 0, "beam": {"color": [255, 70, 40], "width": 2.5, "life": 0.12, "fade": 0.35}}
import { Instance } from "cs_script/point_script";
const core = Core({tag: "TOWER", name: "tower"});
const fire = Fire({core, fire: FIRE.tower.fire, entities: FIRE.tower.entities});
const st = fire.newState();
let head;
function target(eye) {
    let best = null, bestD = 1200;
    for (const p of core.pawns({bots: true})) {
        const at = core.add(p.GetAbsOrigin(), {x: 0, y: 0, z: 1}, 48), d = core.dist(at, eye);
        if (d < bestD && !Instance.TraceLine({start: eye, end: at, ignoreEntity: head, ignorePlayers: true}).didHit) { best = at; bestD = d; }
    }
    return best;
}
// Find the entities on activation AND on every round start: a round restart re-creates them (old handles die).
function setup() {
    fire.setup(); head = Instance.FindEntityByName("tower_head");
    core.log("ready", {head: core.valid(head)});
    Instance.SetNextThink(Instance.GetGameTime() + .1);   // start the loop after activation
}
core.on("OnActivate", setup);
core.on("OnRoundStart", setup);
Instance.SetThink(() => {
    const now = Instance.GetGameTime();
    if (core.valid(head)) {
        const eye = head.GetAbsOrigin(), at = target(eye), d = at ? core.add(at, eye, -1) : {x: 1, y: 0, z: 0};
        const angles = {pitch: -Math.atan2(d.z, Math.hypot(d.x, d.y)) / core.rad, yaw: Math.atan2(d.y, d.x) / core.rad, roll: 0};
        fire.step(st, {trigger: !!at, reload: false, src: {origin: eye, angles, inflictor: head, muzzle: eye}}, now, .05);
    }
    Instance.SetNextThink(now + .05);
});
```

Then `map_edit`: a `prop_dynamic` named `tower_head` and `{"op":"add_script","name":"<the script name>","origin":[700,500,0]}`.

## Short recipes

Each source below is a complete `script_write` payload. Attach it with `map_edit`/`add_script` after a successful compile.

Recipes 1, 5 and 6 were verified in round 2 (`C_RECIPE round_start`, `C_RECIPE hello`, `C_RECIPE tick`). Round 3 verified recipes 2–4 on `s_probe`: actual button → RunScriptInput → door Open → OnFullyOpen (`S_RECIPE door_opened`), MP5 given after a real round restart (`S_RECIPE weapon_name weapon_mp5sd`), and actual C4 planting (`S_RECIPE bomb_planted`). The door recipe needs a button output connected to the script entity's `RunScriptInput` with parameter `OpenDoor` and a door with `spawnflags:0`, `movedir:"-90 0 0"` for upward movement.

Check a script at runtime: build the map, load it with `game_screenshot`, then `game_command("mp_restartgame 1", wait=6)` returns everything printed during six seconds, including your `Instance.Msg` lines and JavaScript errors. Fire inputs with `game_command("ent_fire <name> <input>", wait=3)` and chat commands with `say !text`.

```js
// 1. Message at round start
import { Instance } from "cs_script/point_script";
Instance.OnRoundStart(() => Instance.Msg("C_RECIPE round_start"));
```

```js
// 2. A button input opens a named door
import { Instance } from "cs_script/point_script";
Instance.OnScriptInput("OpenDoor", ({ activator }) => {
  Instance.EntFireAtName({name: "recipe_door", input: "Open", activator});
});
```

```js
// 3. Give a weapon after player reset/spawn
import { Instance } from "cs_script/point_script";
Instance.OnPlayerReset(({ player }) => {
  if (player && player.IsValid()) {
    player.GiveNamedItem("weapon_mp5sd", true);
    Instance.Msg("weapon: " + player.FindWeapon("weapon_mp5sd")?.GetData().GetName());
  }
});
```

```js
// 4. Announce a bomb plant
import { Instance } from "cs_script/point_script";
Instance.OnBombPlant(() => Instance.Msg("C_RECIPE bomb_planted"));
```

```js
// 5. Echo a player chat command
import { Instance } from "cs_script/point_script";
Instance.OnPlayerChat(({ text }) => {
  if (text.trim() === "!hello") Instance.Msg("C_RECIPE hello");
});
```

```js
// 6. One-second repeating timer
import { Instance } from "cs_script/point_script";
Instance.SetThink(() => { Instance.Msg("C_RECIPE tick"); Instance.SetNextThink(Instance.GetGameTime() + 1.0); });
Instance.SetNextThink(Instance.GetGameTime() + 1.0);
```

## Story scripting recipes (verified round 3)

The entity/resource names below are prerequisites: create the named entities with `map_edit`, or use an `add_story` HUD/effects. These snippets demonstrate the APIs exercised by `s_probe`, `s_story`, and `s_extended`; they do not create HUD XML or template contents on their own.

```js
// 7. Update a HUD string. s_probe_hud uses panel id="story", text="{s:objective}".
import { Instance, CustomHudLayout } from "cs_script/point_script";
Instance.OnActivate(() => {
  const hud = Instance.FindEntityByName("s_probe_hud");
  if (hud instanceof CustomHudLayout) {
    hud.SetDialogVariableString("story", "objective", "Find the switch");
  }
});
```

```js
// 8. Trigger output without VMAP I/O. Reinstall after round cleanup.
import { Instance, CSPlayerPawn } from "cs_script/point_script";
let connection;
function install() {
  if (connection !== undefined) Instance.DisconnectOutput(connection);
  const zone = Instance.FindEntityByName("s_probe_zone");
  if (zone) connection = Instance.ConnectOutput(zone, "OnStartTouch", ({ activator }) => {
    if (activator instanceof CSPlayerPawn) Instance.Msg("Entered the story zone");
  });
}
Instance.OnActivate(install);
Instance.OnRoundStart(install);
```

```js
// 9. Save one flag, preserving the rest of the addon JSON (shared by scripts).
import { Instance } from "cs_script/point_script";
Instance.OnScriptInput("Save", () => {
  const raw = Instance.GetSaveData();
  const data = raw ? JSON.parse(raw) : {};
  if (!data || typeof data !== "object" || Array.isArray(data)) throw Error("Invalid addon save");
  data.my_chapter_done = true;
  Instance.SetSaveData(JSON.stringify(data));
  Instance.Msg("Saved chapter");
});
```

```js
// 10. Spawn a named point_template with Template01 pointing at an existing entity.
import { Instance, PointTemplate } from "cs_script/point_script";
Instance.OnScriptInput("Spawn", () => {
  const template = Instance.FindEntityByName("s_spawn_template");
  if (template instanceof PointTemplate) {
    const spawned = template.ForceSpawn();
    Instance.Msg("Spawned " + (spawned?.length ?? 0));
  }
});
```

```js
// 11. Fade out/in. Two env_fade entities: out flags=8, in flags=1, duration=1.
import { Instance } from "cs_script/point_script";
Instance.OnScriptInput("Fade", async () => {
  Instance.EntFireAtName({ name: "s_story_flow_fade_3_0", input: "Fade" });
  await Instance.Delay(1);
  Instance.EntFireAtName({ name: "s_story_flow_fade_3_1", input: "Fade" });
});
```

CS2 rejects relative module imports such as `import "./helper.js"` at runtime even though resourcecompiler packages them. Embed your helper library in the same source, or take the shared pieces with `// @requires`. Weapon item identity is `weapon.GetData().GetName()`; MP5-SD shares the engine class `weapon_mp7`. `ConnectOutput` triggers also fire in noclip; manual zone tests should explicitly turn it off. `ServerCommand` can apply story round/respawn settings without cheats; console `ent_fire`, movement commands and `RegisterCheatCommand` need cheats.

## By hand

Logic without scripts: `logic_relay`, `logic_timer`, `math_counter`, `logic_case`, `logic_branch`, `filter_activator_name` wired with `connect` (`delay`, `times`, `param`); recipes in section `lowlevel`. A script is needed when logic depends on positions, aiming, input or saved data: `script_write` + `add_script`, checked with `script_test`.
