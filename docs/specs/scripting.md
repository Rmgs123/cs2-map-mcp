# Scripts, stories and play tests

`cs_script` scripts (`script_write`, `add_script`, `script_api`), stories compiled from JSON (`add_story`), scenario tests with real input (`script_test`) and physical walks (`route_test`). Code: `scripting.py`, `story.py`, `route.py`, `gametest.py`, `ext/scripting.py`, `ext/story.py`, `ext/route.py`; guides `docs/tools/scripting.md`, `docs/tools/story.md`.

## What the installed CS2 allows

- The API reference is the installed `cs_script_demo/maps/scripts/point_script.d.ts` (`script_api` returns a class's full commented body; brace matching on a copy with strings and comments masked).
- **No relative imports**: `import "./x.js"` compiles, but the game says "Relative values are not supported". Every generated runtime is embedded whole; shared mechanics are bundled from fragments (`mechanics.md`).
- HUDs: `custom_hud_layout` pointing at a `.vxml` from `panorama/layout/custom_game/`, CSS through `<include src="file://{resources}/styles/custom_game/NAME.css" />`, `SetDialogVariableString` fills `{s:var}` (Cyrillic renders). See `ui.md`.
- `ConnectOutput(trigger, "OnStartTouch", cb)` works with noclip on and off; after a round cleanup old hooks are invalid, so runtimes re-subscribe.
- From `OnActivate`/`OnRoundStart`, `ServerCommand` sets round and team cvars without cheats; `SetRoundRemainingTime` sets the running round. Console `ent_fire`, `noclip`, `setpos` and `RegisterCheatCommand` commands need `sv_cheats` (JS `EntFireAtName` does not).
- `GetSaveData`/`SetSaveData` survive map changes and rebuilds; the data is shared by the addon, so writes merge JSON and keep other keys.
- Weapons report their data name: the MP5-SD's class is `weapon_mp7` but `GetData().GetName()` is `weapon_mp5sd`.
- `CustomPlayerCamera` CONTROLLED/DISABLED work; `getpos` then reports the custom eye.

## Stories (`add_story`)

1. `validate` deep-copies and strictly checks the short schema: one `until` per stage, actions, unique ids, no unknown fields, no `mcp_` names, safe save keys.
2. `generate` gives one JS, one XML, one CSS and standard operations: a `trigger_multiple` per zone (spawnflags 4097), a `point_soundevent` per sound, `env_fade`/`env_shake` per action, the HUD, hint `point_worldtext` markers (off until their stage). The first JS line is `// CS2MAP_STORY <canonical JSON>`, read by `check_map` and the auto test; the runtime gets the JSON as a literal.
3. A `map_edit` wrapper runs the whole batch as a dry run first (in-memory entities, a placeholder script, a syntax check), then for real: HUD and script are written, backed up and compiled; a compiler error saves no map.
4. **Runtime**: one active stage; a think every 0.1 s updates timer and HUD; buttons and zones through `ConnectOutput`, re-subscribed per stage; `until.use` traces 96 units from the real eye on a rising E (no `OnPlayerUse` exists); checkpoints teleport a respawned player (`QueueAfterThinks`, velocity 0); a run epoch cancels actions of an old run after a reset. Log lines: `S_STORY <name> stage <id> start|done`, `S_STORY <name> complete`, `S_STORY_ERROR`; `<name>_state <token>` prints the state. The human joins T (a single-player map). Saves: `cs2map_stories[name] = {next, flags}`; `resume` restores the stage and replays fire/spawn of earlier stages once.

## `script_test`

Steps and regexes are validated before CS2 is touched (up to 128 steps, timeout 1..90 s). One game lock covers the test; a continuous console listener (attached after draining old output) catches errors during loads, waits and frames too. JS errors (`Uncaught`, `TypeError`, bad imports, `S_STORY_ERROR`) make `ok=false`. `expect` reads only lines after the previous action and consumes its match. Real input: `use`, `hold`, `aim` (`setang`, the window must be in front), `mouse` (SendInput), `click` (a real left click at a window point), `focus`. `steps="auto"` sends `TestReset`, walks every story stage (buttons pressed, zones entered with setpos, chat and inputs sent, pickups given, kills done with a real bot and `TakeDamage`). The answer stays under 2 KB; the full log goes to `logs/script-test-<map>-<ns>.json`.

## `route_test`

A real walk with no teleports while walking:
1. Steps are validated before the game; a runtime probe script is created live with `ent_create` (the map is not changed).
2. One lock covers load, test, frames and cleanup; noclip off; `start` is the spawn (round restart), the current position, or one setpos before measuring.
3. A driver gives `+forward` impulses toward each waypoint with `setang` and reads a token-matched state (feet, eyes, move type, velocity, health, duck, keys, ground entity, a player trace 40 ahead). Impulse length follows speed (crouched about 85 u/s, standing 250).
4. `go` succeeds within the radius and |dz| ≤ 18; no progress for 1.8 s is diagnosed (wall, height, inside solid, damage, death, fall): a diagnosis of this plan, not proof that no way exists. `jump` presses once near an obstacle; `duck` holds for a segment.
5. `use` needs the eye trace on the named target before pressing and `IsInputPressed(USE)` after (a lift may carry the view away); `ride` checks the ground entity and the target height. Activation is always a real `+use`.
6. `"auto"` plans story stages: layout openings, lifts (call button, board, ride, leave), ladders (up, or down with move type LADDER), and flat legs by A* on a 24-unit grid over real floor triangles with a 32×32×72 hull, 8 units of margin and swept tests. Breakables are knifed (success = the entity is gone); pushing props is experimental.
7. The walk waits for the end of the freeze period (`IsFreezePeriod`): after a restart or a bot joining, input is ignored for about 6 s.

## Pitfalls we hit

- A `func_brush` with `Solidity` 1 (the CS2 default) lets the eye trace pass: `until.use` refuses it.
- Without `TestReset` before the first frame, the spawn frame after an earlier run was black with the final text.
- Multi-line console commands must be split (`game.query`).
- Nav files exist, but a nav area count is not a path: physical walking works even with 0 nav areas.

## Tests

`tests/test_scripting.py`, `test_story*.py`, `test_route.py`, `test_gametest.py`; in game `mcp_story` (five stages), `s_extended` (chat, input, pickup, kill, template, teleport, camera), `f2_port` (21/21 route steps), `k_warehouse`, `r2_*` (ladders down, lifts, breakables).
