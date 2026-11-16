---
description: Test every mechanic of a map with real input in CS2 and fix what fails
arguments: map | Map name
---
Test every mechanic on the map `{map}` in CS2 with real input.

1. List the mechanics on the map (`map_info`, `check_map`): vehicles, weapons, abilities, doors, story steps, mini-games, modes.
2. For each one: `game_reset("{map}")`, a `script_test` with real input (use, hold, aim, mouse, expect, shot) that proves it works and one that tries to break it (wrong order, leaving early, dying, round restart), `game_reset` again. Routes and vehicles: `route_test`. Competitive layouts: `playtest(kind="timings")`.
3. Open every frame. A failure: find the cause, fix it, test again.
4. Report a table: mechanic, tests run, result, frames, what was fixed.
