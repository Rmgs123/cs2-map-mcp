# Shared mechanics core

`cs_script` has no imports, so mechanics used to copy entry, camera, health, HUD and logging from each other. Now every mechanic script is **assembled from tested fragments**; a new mechanic is a short adapter on the same pieces. Drone, car, turret, weapons, mini-games, abilities and hide-and-seek all stand on it. Code: `mechanics.py` (assembler, schemas, standard entities, Node test bed), `data/mechanics/*.js` (fragments), `fire.py` (the shot engine's schema and entities).

## The assembler

```python
code = mechanics.build_script('v9_car1', ['core', 'hud', 'camera', 'seat', 'health', 'car_motion'], config)
```

- A fragment is a JS file with a header: `// @fragment name`, `// @requires a, b`, `// @imports Instance, CSInputs` (names of `cs_script/point_script` exports; an unknown name fails at registration). Modules register their own: `mechanics.register_fragment('car_motion', path)`.
- `build_script` orders fragments by dependencies (a cycle or an unknown name is a `ValueError`), merges imports into **one line**, inserts `const config = <JSON>;` and writes a first-line marker (`// CS2MAP_MECH {config}`, or a module's own marker) by which operations and `check_map` recognise the script and read its configuration. Configuration enters only as a JSON literal.
- The adapter is the last "fragment"; inside a fragment everything is in a closure (`function Seat(cfg) {...}`), so names never clash.
- Script files carry a digest of their definition (`<name>_<sha1[:8]>`): the same name on two maps never collides, and an unchanged compiled script is not recompiled (a running CS2 keeps its file open).

## Fragments

**`core`** — `Core({tag, name, state})`. `core.log(event, data)` prints `"<tag> <name> <event> {json}"` and keeps the last 8 events (`recent`). Helpers: `valid`, `clamp`, `vec`, `add`, `dist`, `basis(angles)` → `{f, l, r, u}`, `anglesOf`, `pawns({bots, dead})`, `eyeRay(p, distance)`, `fx(name, input, value, delay)`, `sound(names, at)`. **One engine subscription per event, many listeners**: `core.on("OnPlayerKill", fn)` (results of `OnModifyPlayerDamage` are merged), `core.input(name, fn)`, `core.command(name, fn)`. `core.state(() => ({...}))` registers `<state> <token>`, which prints your fields + `recent` + every human (position, eyes, body and view angles, alpha, move type, camera mode, weapon): the answer tests read.

**`hud`** — `Hud({core, name, panel, variable})`: one `custom_hud_layout` per mechanic. `find`, `hideAll`, `show`, `hide`, `hideSlot`, `text`, `put` (text + show), `flag(p, class, on)`, `due(key, now, period)` (throttle).

**`camera`** — `Camera({core, follow, views: {forward, up, chase, start}, key})`. `FOLLOW_POSITION` lets the script read the mouse (`CONTROLLED` does not). `free`, `start`, `stop`, `update` (R toggles first person and chase), `mode()`.

**`seat`** — `Seat({core, camera, hud, target, reach, pawn, decoy, allow, onEnter, onExit})`. Entry: a rising E with the eye ray within `reach` (96) of `target()`. The player is saved (position, yaw/pitch, move type, weapon, name), frozen, given only the knife, renamed to `pawn` (a damage filter on the body ignores the pilot), hidden (alpha 0) and replaced by a still stand-in. Exit: E, death, respawn, round restart, disconnect, the script input `Exit` or `seat.exit(reason)`. Restore: **yaw in `Teleport`, pitch through `SetEyeAngles`**. If the player does not fit at the entry point (a turned turret, a moved car), rings around the entry point and the object are searched for the nearest free place with a floor.

**`health`** — `Health({core, body, max, native, parts, effects, respawn, spawn, guard, onDestroy, onRestore})`. `native: true`: the body has huge engine health and each tick's drop of `GetHealth()` comes off the script's HP (bullets, grenades, knife, `RemoveHealth`). Half HP smokes; 0 explodes (`boom`, sound), darkens, tumbles and burns until `respawn` seconds, then returns to `spawn`; `respawn: -1` ("never") keeps the wreck, also after round restarts. `guard(weapon)` refunds the pilot's own knife swing.

**`fire`** — the shot engine: charge with glow, `TraceBullet` to the wall and `TraceLine` back through it for penetration, beams from a pool of glowing rods (instead of `env_beam`), pellets and spread, magazine, reload, cooldown, recoil kick, sounds, projectiles with homing and explosions, single/burst/auto. `Fire({core, fire, entities, knockback, title})`; per shooter `st = fire.newState()`; each tick `fire.step(st, {trigger, reload, src}, now, dt)` with `src = {shooter, origin, angles, inflictor, muzzle?, kick?}` (a player's eyes, or a turret's muzzle and barrel angles with the seated gunner credited). Schema and entities: `fire.validate`, `fire.names`, `fire.entity_defs`, `fire.ensure_assets`; users register with `fire.register_user`.

## A mechanic of a model's own

`script_write` bundles a hand-written script that starts with `// @requires core, hud, ...`: the shared fragments go in front of the code under one merged import. `// @fire <prefix> {json}` adds `FIRE.<prefix> = {fire, entities}` (validated by `fire.validate`), and `add_script` creates those entities. Fragments never read the global `config`, so they work in any script. Example: the tower guard in `docs/tools/scripting.md`.

## Adding a kind of controllable

`control.register_kind(kind, validate, fragments, entities, motion, *, fields, prefix, names, prepare, camera, hud, title, hint)` — no change to `control.py`; the operation stays `add_controllable`. The adapter follows `drone.js`: body variables, `Health`/`Seat`/`Camera`/`Hud`, a `setup()` (find entities → `health.restore()` → `hud.hideAll()` → `SetNextThink`), a think with `health.update(now)` → `seat.update(core.pawns())` → motion → HUD, `core.on("OnActivate", setup)`, `core.on("OnRoundStart", setup)`, `core.state(...)`.

## Testing

- **Node test bed** (no game): `mechanics.run_harness(code, test_js)`. The engine double `test_double.cjs` provides `Instance`, `D.pawn` (keys through `D.tick(keys, dt)`), entities, events, traces, glow, chat, outputs (`D.output`), the round clock and `D.state(command)`. Examples: `tests/test_mechanics.py`, `test_control.py`, `test_weapons_*.py`, `test_abilities.py`, `test_hideseek.py`.
- **In the game**: `script_test` with real input (`use`, `hold`, `aim`, `mouse`, `click`, `focus`, `shot`), telemetry `<state> <token>`, `game_reset` before and after, `game_crash_report` after a crash.
- **What Node cannot prove**: physics, hitboxes, `FOLLOW_POSITION` cameras, model visibility, sound — only the game and opened frames.

## CS2 facts the core stands on

The player's body always turns with the view (hence the stand-in); `Teleport` of a player with pitch tilts it for good; `SetColor` without alpha makes a prop invisible; `env_beam` crashed the server; props without `prop_data` take no bullet damage; triggers do not see a body the script moves; HUD layouts only load from `panorama/layout/custom_game/`; the game window must be in front for `setang` and the mouse to reach the client; glow renders on props (through walls) but not on player pawns; `trigger_gravity` does nothing for players; **an entity spawned outside the world (e.g. parked at z −15000) is never drawn, even after a script moves it in** — helpers wait inside the map, transparent; physics-only stock props (crates, barrels) are deleted at spawn as `prop_dynamic`: use `prop_dynamic_override`; the script VM survives a round restart (variables persist), the map's entities are re-created.
