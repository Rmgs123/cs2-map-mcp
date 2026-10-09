# Controllable objects and the drone

`add_controllable` puts an object a player takes over with **E**: a `drone` (this section), a `car` (section `vehicles`) or a `turret` (section `turrets`). All kinds share entry and exit, the hidden pilot with a still stand-in, the camera, HP with smoke, explosion, burning wreck and respawn, the HUD and test telemetry.

| Task | Take |
| --- | --- |
| A drone flown from a remote | `add_controllable`, `kind:"drone"` (default) |
| A car, a mounted gun | `kind:"car"` (`vehicles`), `kind:"turret"` (`turrets`) |
| Build the stock drone assets in advance | `assets_prepare(kind="drone")` (the operation builds them on first use otherwise) |
| Check it in the game | `game_reset` → `script_test` (E, keys, mouse, `<state> <token>`) → `game_reset` |
| Another kind of object | section `scripting` + `docs/specs/mechanics.md` (shared fragments) |

## Example (verified on `p8_mechanics`, `m9_games`)

```json
{"op":"add_controllable","name":"drone1","origin":[512,512,2],"bounds":{"room":"yard"},
 "controller":{"at":{"room":"yard","at":[0.3,0.5],"z":40}},
 "health":50,"respawn":10,"ref":"drone1"}
```

The stock drone (`models/mcp_d/drone`) gets its remote and its four propellers by default. The remote's `at` is its base: put a table under it (the operation makes none).

## Fields shared by every kind

| Field | Default; range, units |
| --- | --- |
| `name` | required: lowercase letter + up to 39 letters, digits, `_`; entities are `<prefix>_<name>_*` |
| `kind` | `drone` (default), `car`, `turret` |
| `model` | the kind's stock model; own: a compiled `models/.../*.vmdl` with the same layout |
| `origin`, `yaw` | world `[x,y,z]` or room point; heading degrees (0 = +X) |
| `health`, `respawn`, `effects` | kind default HP (0 = indestructible); seconds until the wreck respawns at `origin` (0 = next round, `"never"` = the wreck stays, also after round restarts); smoke, explosion and fire |
| `camera` | first person `{forward, up, right}` and `chase:{forward, up}` or `false`, `start` (`fpv` or `chase`); R switches; `up` = height above the body origin, `right` = sideways in the body frame (+ right), `forward` = along the view (0 keeps the eye fixed, e.g. a seat) |
| `reach` | units the eye ray may be from the object when E is pressed |
| `sound` | a loop event played while powered (car: `engine`) |
| `hud` | `{"title": "..."}`, up to 24 plain characters |
| `ref` | names the script node for later `@ref` |

## Drone fields

| Field | Default; range, units |
| --- | --- |
| `controller` | `{at, angles, model}`: the remote (stock model by default) |
| `bounds` | room `{room}` or `{mins,maxs}`: the flight area with soft walls; a box above the rooms lets it fly over roofs |
| `physics` | `true`: rigid body (inertia, collisions, bullet pushes, falling wreck); `false`: kinematic mover, no health |
| `speed`, `climb`, `accel`, `tilt`, `mass` | 400 u/s, 0.6×speed, 900 u/s², 18°, 8 kg |
| `on_exit` | `hover` (holds its point, returns after a push) or `land` |
| `battery` | seconds of flight (0 = unlimited); recharges parked, lands when empty |
| `rotors` | stock drone: 4 propellers; own model: up to 8 `{model, offset, speed}` |
| `hull` | clearance box (±55, z −1..26), checked against the models |

Defaults: health 50, respawn 10, camera `{forward:22, up:10}` + chase `{forward:-120, up:36}`, reach 96.

## In game

Aim at the remote within `reach` and press **E**: WASD fly relative to the view, mouse turns, SPACE/CTRL up/down, SHIFT slow, R camera, E exit. HUD: HP, height, speed, battery. The pilot's body is hidden; a still stand-in of the same model stays where E was pressed; the pilot holds only the knife and cannot hurt their own drone. Death of the pilot, round restart, disconnect or destruction of the drone end the flight at once and restore the body, weapon and view.

Bullets, grenades and knives hurt and push the drone. At half HP it smokes; at 0 it explodes (every time), the dark wreck falls and burns until `respawn`, the remote refuses meanwhile.

## Test it

```json
[{"walk":[440,420,8]},{"aim":[420,420,40]},{"use":true},{"expect":"D_CONTROL drone1 enter","within":3},
 {"hold":{"key":"jump","seconds":1}},{"hold":{"key":"forward","seconds":1}},{"shot":"drone_fly"},
 {"cmd":"d8_control_drone1_state up"},{"expect":"\"token\":\"up\"","within":3},{"use":true}]
```

`d8_control_<name>_state <token>` prints position, velocity, angles, hp, stats, the pilot's eye and camera and the last 8 events. Damage by I/O: `RemoveHealth N` on `d8_<name>_body`; release: `RunScriptInput Exit` on the script.

## Limits

One pilot per object; network play is not tested. CS2 turns a pawn's body to its view: the hidden body's hitboxes follow the camera. Change a definition by replaying the map batch.

## By hand

Body `prop_physics_override` whose model has convex hulls, mass and `prop_data` (`dmg.bullets` etc.; without them bullets do nothing); remote: solid `prop_dynamic`; HUD: `custom_hud_layout` with its XML in `panorama/layout/custom_game/` only. `point_script`: on E traced at the remote save the pawn, MoveType NONE, `GetCustomCamera()` FOLLOW_POSITION on the body; steer with `Teleport({velocity, angles})` adding `800*dt` to z; count `GetHealth()` drops against a huge native health. Restore the pawn with yaw-only `Teleport` + `SetEyeAngles` (a pitched Teleport tilts it for good); `SetColor` needs `a:255`. Stop and start a particle in different ticks (delay ≥ 0.04 s), or the client never restarts it.
