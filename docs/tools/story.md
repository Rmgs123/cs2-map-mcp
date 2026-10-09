# Story maps

A single-player chapter: stages with objectives, conditions, actions, HUD, subtitles, fades, saves, checkpoints. `add_story` generates the script, HUD and helper entities; rooms, buttons, doors and enemies are made first with normal operations.

| Task | Take |
| --- | --- |
| The chapter logic | `map_edit` `add_story` (last in the batch) |
| Rooms, lifts, ladders it walks through | `add_layout`, `add_lift`, `add_ladder` (`layout`, `physics`) |
| Test the logic (teleports, events) | `script_test(map, steps="auto")` (steps: section `scripting`) |
| Test that a player can really walk it | `route_test(map)` (`route="auto"` or explicit steps) |
| Switchable lamps in a story | `add_light(start_off=true)` + action `{"light": name, "on": true}` |

Verified: `mcp_story` (five stages, switch → lamps, lift, window break, river, fade, save), `s_story`, `s_extended` (chat, input, pickup, kill, template, teleport, camera), `f2_port`, `k_warehouse`, `r2_*`.

## `add_story`

```json
{"op":"add_story","name":"my_chapter","ref":"story_script",
 "settings":{"respawn":true,"round_minutes":30,"bots":0,"resume":false},
 "stages":[
  {"id":"wake","objective":"Find the switch","say":["Where am I?"],"until":{"button":"switch1"},
   "then":[{"fire":"door1","input":"Open"},{"sound":"tr.Countdown"}]},
  {"id":"hall","objective":"Enter the hall","until":{"zone":{"mins":[440,-80,0],"maxs":[640,80,128]}},
   "checkpoint":[540,0,8],"hint":true},
  {"id":"timer","objective":"Wait for the door","until":{"time":10},"then":[{"fire":"door2","input":"Open"}]},
  {"id":"exit","objective":"Leave","until":{"zone":{"mins":[860,-80,0],"maxs":[1080,80,128]}},
   "then":[{"fade":"out","seconds":1},{"save":"chapter1_done"},{"end_text":"Chapter 1 complete"}]}]}
```

| Field | Contract |
| --- | --- |
| `name` | unique script name (1–64, letters/digits/`_`, not `mcp_`); script targetname `<name>_script` |
| `origin`, `ref` | helper position (default `[0,0,8]`); reference to the script |
| `settings` | `respawn` true, `round_minutes` 30 (1–60), `bots` 0 (0–16), `resume` false |
| `hud` | `objective`, `subtitles`, `timer` booleans (all true) |
| `stages` | 1–64: unique `id`, `objective`, exactly one `until`, optional `say` (≤20 lines), `then` (≤64 actions), `hint`, `checkpoint`, `route` |

Stage extras: `hint:true` — a glowing marker above the stage's button/zone/use target; `checkpoint:[x,y,z]` — respawn here (feet) once this stage is entered (`respawn:true`); `route:[...]` — physical steps of this stage for `route_test` auto.

Conditions (`until`, exactly one):

| Condition | Meaning |
| --- | --- |
| `{"button":"switch1"}` | the real `func_button` OnPressed (the button must exist) |
| `{"zone":{"mins","maxs"}}` | a human enters the box (a generated trigger) |
| `{"use":"note"}` | a fresh E while looking at that solid prop/brush within 96 (a `func_brush` needs `"Solidity":2`) |
| `{"chat":"!ready"}` | exact chat text |
| `{"time":10}` | seconds in this stage (HUD timer) |
| `{"input":"Ready"}` | `RunScriptInput Ready` on the script (I/O) |
| `{"kills":1}` | enemy kills by a human in this stage (needs bots and nav) |
| `{"pickup":"weapon_mp5sd"}` | a human receives that weapon in this stage |

Actions (`then`, in order; fades and camera wait for their duration):

| Action | Meaning |
| --- | --- |
| `{"fire":"door1","input":"Open","value":"","delay":0}` | any entity input |
| `{"sound":"tr.Countdown"}` | play a sound **event** |
| `{"text":"...","seconds":3}`, `{"say":"..."}` | subtitle (later text replaces it) |
| `{"fade":"out","seconds":1}` (or `"in"`) | black fade; out stays until in |
| `{"shake":true,"seconds":1,"amplitude":4}` | screen shake |
| `{"teleport":[x,y,z]}` | move the player (feet) |
| `{"spawn":"template"}` | `ForceSpawn` an existing `point_template` |
| `{"light":"lamp","on":true}` | switch an `add_light(start_off)` lamp |
| `{"camera":[x,y,z],"ang":[p,y,r],"seconds":2}` | a fixed camera shot, then back |
| `{"save":"flag"}`, `{"end_text":"..."}` | save progress; final text |

The story sets a long round, no warmup, no win conditions, team T for humans, the bot quota and respawn. `resume:true` continues from the last `save` and replays completed `fire`/`spawn` actions. `check_map` checks targets and inputs and switches to `mode: "story"` (no competitive rules). To change a story, replay the map batch after `map_create(overwrite=true)`; a changed definition used by another map is refused.

## `route_test(map, route="auto", timeout=90, start="spawn")`

Walks with real keys (no teleports while walking), noclip off; positions are feet. `start`: `spawn` (restarts the round), `current`, or `[x,y,z]` (placed once before walking).

```json
{"map":"r_route_lab","timeout":90,"route":[
  {"go":[190,0,0]},{"go":[356,0,0],"jump":true},{"go":[470,0,0],"duck":true},
  {"use":"r_lift_button"},{"ride":"r_lift","to_z":144,"within":12},{"go":[688,0,144]}]}
```

Steps: `go:[x,y,z]` (+`jump`, `duck`, `radius` 2..48), `use:"name"` (eye ray checked), `wait`, `ride:"lift"` + `to_z`, `climb_down:[x,y,z]` (or `{to,top}`), `break:"name"` (knife until gone), `push:"name"` + `to` (experimental). `within` seconds per step (15); `allow_damage:true` per step. `route="auto"` follows the story: doorways, lifts (call buttons), ladders and A* over real triangles; other conditions need `stage.route`. On terrain: `route_test(map, route=[[x,y],...], terrain="hills")`.

Returns `ok`, `reached`, `stuck` (point, position, reason), `stage_seconds`, `movement_seconds`, frames and the log. Open the frames; a busy console is not a pass.

## By hand

A `point_script` with a stage index, subscribed to outputs (`ConnectOutput`), trigger touches, chat and `OnPlayerKill`; HUD through `custom_hud_layout` (XML in `panorama/layout/custom_game/`); fades `env_fade`, shakes `env_shake` (story only), saves with `Instance.SetSaveData`. See section `scripting`.
