# Competitive maps

| Task | Take |
| --- | --- |
| Spawns, buyzones, bombsites/hostages, mid, nav seeds | `add_comp_plan` |
| Route times T/CT to the sites | `playtest(kind="timings")` |
| Bots play rounds | `playtest(kind="competitive")` |

`map_edit` operation `add_comp_plan` composes `layout.add_layout` with 10–16 spawns per team (default 10, pitch 64u), buyzones, objectives, mid and room nav seeds, map parameters, a light probe and a render-only lightmap volume.

```json
{"name":"c_demo","operations":[{"op":"add_comp_plan","name":"c_demo","mode":"defuse"}]}
```

Modes: `defuse` = A+B bombsites, `wingman` = one bombsite, `hostage` = hostages plus one CT rescue zone. The default three-lane skeleton has 128–192u openings, 2656u T and 2082u CT approaches, open ceilings and no tactical cover. Add cover, `dress_room` and lighting, then `check_map` and `map_build(name, preset="full")`. Mode describes objective entities; it does not configure the server's player limit or game mode.

Supply a nested `layout` (the `add_layout` fields), or top-level `rooms`, `openings`, `materials`, `wall`, `origin`, `levels`, to replace the default geometry. Room IDs `t`, `ct`, `mid`, `a`, `b` select defaults. Custom layouts without these IDs require explicit objective rooms. One competitive plan per map.

Optional anchors: `t_spawn`, `ct_spawn`, `mid`, world `[x,y,z]` or `{ "room":"t", "at":[0.5,0.15], "z":"floor" }`. Floor points receive an 8u spawn offset. `spawn_count` is an integer 10–16. Explicit `spawns:{"t":[points...],"ct":[points...]}` must have equal counts in that range.

`sites` is an array: two sites for defuse, one for wingman/rescue. Bomb IDs are A/B. Example: `{ "id":"A", "room":"a", "at":[0.5,0.5], "size":[256,256], "height":128 }`; alternatively use world `mins`/`maxs`. Hostage points: `hostages:[{"room":"a","at":[0.4,0.5]}]` (1–8 points, or an integer count).

`playtest(map, kind="timings", timeout=60, speed=250, live=true, offset=0)` returns all layout distance estimates and walks **one physical route per call**, keeping prior measurements for the same source VMAP. Queue/load share a 112s budget; the requested route timeout can be reduced to the remaining budget. Repeat the returned `next_offset` (defuse: 0–5: T→A/B/mid, CT→A/B/mid). Use `live=false` for estimates. `ok` for a live call requires all routes completed; inspect the selected `physical` row while progressing. Failed routes have null seconds/metres. Changing VMAP invalidates measurements.

The runner locks CS2, removes bots, restarts the round, selects `slot3`, positions the player at the actual first spawn and delegates to R2 `route_test`; no teleporting during measured movement. Physical seconds exclude setup/freeze/screenshots but include controller turns and pauses; metres sum sampled XY movement. Estimates use 250u/s and 0.0254m/u. Weapon selection is requested, not independently inspected. Comparisons use physical times once both teams completed that objective; otherwise `basis=estimate`. A 3–8s CT advantage is a design target for bombsites, not an official map guarantee. Mid/rescue balance needs human judgement.

`playtest(map, kind="competitive", bots=10, seconds=60, round_seconds=null, spectate=true)` runs for 1–90s and runs bounded rounds (you spectate; bots play 5 v 5 alone — verified on `c_defuse`: 5+5 moving, a round completed), reports moving bots by team, observed completed rounds and sampled death positions/rooms. `competitive_ok` requires both teams moving, nav > 0, a later round turnover and no console errors. Initial bot-join restart is excluded; sparse samples can miss deaths and give last observed positions. Use `round_seconds=30` (20–120s) for a short native round-clock test; timer/win-condition convars are read first and restored even on failure. Queue/load time is bounded; retry a busy/budget error. Detailed logs are linked in responses.

`check_map` runs spawn hull/spacing, triangle LOS and bounded eye-height samples of whole T/CT spawn rooms and actual buyzones. `comp_spawn_area_line_of_sight` includes one exposed eye pair; offset consecutive openings (dog-leg) or add a wall. Grid/doorway samples can miss small gaps, and LOS does not simulate bullet penetration. It also checks cover, passage widths and obstructed opening graphs. Static checks exclude stock prop collision and cannot prove nav, runtime doors, objective gameplay or ranked balance. New default main-corridor openings alternate `at=.25/.75` to break the straight LOS; custom openings keep their explicit positions.

Verified in CS2 on `c_defuse` (layout, physical timings, bot rounds).




Accepted c_defuse: full build exit0/nav435, all six routes completed, physical CT advantage A3.19s/B3.96s. Short clock test (bots10, seconds60, round_seconds30): both teams walking, one completed round, sampled named death locations, console errors []. c_hostage and c_wingman shells also compiled full and their game frames were opened. See the report for logs, frames and remaining gameplay/balance limits.

## By hand

A defuse map is: 10+ `info_player_terrorist` and `info_player_counterterrorist` 48 apart on the floor, `func_buyzone` brushes (`TeamNum` 2 for T, 3 for CT) over each spawn, `func_bomb_target` brushes at sites A and B; hostage maps use `hostage_entity` and `func_hostage_rescue`. Routes: `add_layout` or meshes; check timings with `playtest(kind="timings")` and sightlines with frames from each spawn.
