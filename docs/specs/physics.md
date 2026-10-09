# Physics and interactive entities

Doors, buttons, movers, rotators, physics props, breakables, zones and ladders as `map_edit` operations, checks for their wiring, a live probe and physics for own models. Code: `physics.py`, `ext/physics.py`; tools `physics_probe`, `model_info(physics=true)`; guide `docs/tools/physics.md`.

## How it works

Keys and I/O come from the installed FGD, never from HL2 class names; the shipped `cs_script_demo` (12 `func_movelinear`, 6 `func_button`) and other example maps showed working setups.

| Recipe | CS2 facts used |
| --- | --- |
| Sliding door (`func_door`) | `movedir` is pitch/yaw/roll (`-90 0 0` = up), `speed` units/s, `lip` shortens travel, `wait -1` stays open; spawnflags 256 E, 1024 touch, 2048 locked (0 for button/trigger/script) |
| Rotating door (`func_door_rotating`) | `distance` = angle; 2 reverse, 64 X axis, 128 Y axis, else Z; the entity origin is the hinge, the child mesh stays in world coordinates |
| Button (`func_button`) | 1024 E + 1 don't move; once → `wait -1`, `timesToFire 1` |
| Mover (`func_movelinear`) | `movedistance` = length of `to`, `movedir` from atan2; inputs Open, Close, SetPosition, SetSpeed; `loop` links the end outputs through a `logic_auto` |
| Rotator (`func_rotating`) | `maxspeed`; 1 start on; 4 X axis, 8 Y axis; 2 reverse |
| Breakable (`func_breakable`) | `material` 0 glass, 1 wood, 2 metal; health > 0 |
| Zones | `trigger_push` (`pushdir`, `speed`), `trigger_teleport` (target → `info_teleport_destination`), `trigger_hurt` (`damage`, `damagetype`); gravity zones are scripted abilities (`trigger_gravity` does nothing for players in CS2) |
| Ladder | a world mesh with the invisible ladder material plus stacked stock Nuke ladder modules on the wall |

Inputs are validated (finite, ranges, positive sizes, names, enums); generated names and `ref` connect entities. A physics prop needs a compiled collision body (`PHYS` block or a resolved `.vphys`, checked with `resource.py`); `mass` becomes a map-start `SetMass` (not `massScale`); `health` uses `prop_physics_override`. `check_map` adds `door_no_activation` (warning), `button_no_targets`, `physics_io_target_missing`, `physics_model_no_body` (errors); a `point_script` only counts as a possible activator.

**Own models with physics**: `PhysicsBodyMarkup` (mass in kg, inertia scale), `prop_data` (health, damage multipliers, `allowstatic`, `physicsmode 1`) based on the installed `propdata.vdata` names; breakables get up to eight octant pieces as `BreakPieceEmbedded` (render mesh + hull, `prop_physics_override`, debris collision, 15 s fade). Generic fragmentation, not hand-authored damage.

**`physics_probe`** prints one short JSON line per entity through a runtime helper script (`FindEntitiesByName`, origin, angles, velocity, health, alive) and a token-matched end line: VConsole cuts a single long line at about 2 KB. No answer, a partial answer or another map is an error, never empty success. It also takes a context frame, so it lags a few seconds.

## Pitfalls we hit

- A class present in Valve's example maps is not proof it works for players: `trigger_gravity` exists and does nothing.
- A static concave mesh cannot be a dynamic body: physics models need a hull.

## Tests

`tests/test_physics.py`, `tests/test_models_physics.py`; in game `p_interactive` (doors, buttons, movers, rotators, props, breakables), `a10_raw` (logic chains, hinge, track train, templates).
