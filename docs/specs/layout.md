# Level skeleton: rooms from a graph

`add_layout` turns a list of rooms (sizes, neighbours, levels) and openings into closed walls, floors and ceilings with real holes; `add_lift` adds lifts between levels; a preprocessor lets any operation use points relative to rooms. Code: `layout.py`, `ext/layout.py`; tools `map_info(about="layout")`, `game_screenshot(views="rooms")`.

## How it works

1. **Validation** of the whole plan: names, sizes, dependency order, levels, interior overlaps, openings, door parameters. Limits: 128 rooms, 256 openings, 64 levels, 4096 result boxes.
2. **Placement**: the first room starts at the layout origin; `from`/`side` leaves exactly `wall` units between interiors; cross alignment `start|center|end` or a fraction. `floor_z` and levels are relative to the layout origin, not to the anchor room. An open platform (`in`, `at`, `outdoor`) sits inside its parent with headroom and may not leave it.
3. **Openings**: `between` uses the shared wall, the overlap of both rooms along it and their common height range (`at` a fraction of that, `bottom` above the higher floor); `room`/`wall` cuts a wall and finds the neighbour itself.
4. **Solids**: walls are axis boxes outside the interiors (X walls own the corners, Y walls stop at them); floors and ceilings of normal rooms extend by `wall` (thresholds included). Openings and room interiors are subtracted (A − B gives up to six boxes) and earlier bodies are subtracted from new ones, so no two result solids overlap: a shared wall is built once with one hole, a slab between floors is not doubled. The material of a shared wall comes from the room described first.
5. Meshes go under the group `layout:<name>`; doors are made with `add_door` at the exact opening bounds. Saving is the usual atomic `map_edit`.

**Storage**: the group keeps `cs2map_layout` (JSON v1: name, origin, wall, materials, levels, rooms with interiors, floor heights and flags, openings with bounds and doors, mesh ids). Moving the group shifts the stored plan by the same delta; rotation and scale are refused. Moving single meshes does not rebuild the plan; `check_layout` catches blocked openings, displaced doors and broken lifts.

**Relative points**: the preprocessor walks a copy of every operation and resolves `{room, at, z}`, walls and boxes in any field (origins, mins/maxs, nested shapes, story zones, teleports). XY are fractions, Z units above the room floor (`"ceiling"` = the interior top); wall normals point into the room. Unknown rooms return the list of rooms; duplicates need `layout:<name>`.

**Lifts**: `add_lift` builds a `func_movelinear` next to the chosen edge of an open upper platform (start at the lower floor, travel to the upper one, gap 1..18, size 32..1024, at least 72 between levels) and two buttons, `<target>_lower` (Open = up) and `<target>_upper` (Close = down), plus call buttons on both floors. The whole travel and 72 units above a standing player are checked against real triangles with a continuous SAT test (13 axes, contact tolerance 0.02); triggers, sky, helpers and render-only meshes are ignored.

**Checks** (codes): `layout_opening_small` (< 32×72), `layout_opening_blocked`, `layout_door_outside_opening`, `layout_mover_world`, `layout_mover_invalid`, `layout_lift_docking`, `layout_button_in_wall`, `layout_sign_in_wall`, `layout_room_unreachable` and `layout_spawn_missing` (warnings). The graph connects rooms through openings whose threshold is ≤ 18 above both floors, working lifts, ladder surfaces touching both rooms and stair meshes rising ≤ 18 per step.

## Why it is built this way

- Non-overlapping solids: overlapping walls flicker and confuse every later check.
- A plan stored in the map lets later operations (paving, facades, routes, radar) reason about rooms instead of raw meshes.

## Pitfalls we hit

- A graph is not proof of walkability: ladders, scripted teleports, dynamic props and `+use` need a live `route_test`.
- Room views follow the plan, not visibility: a furnished room sometimes needs another camera.

## Tests

`tests/test_layout.py`; in game `k_warehouse` (40 relative operations), `f2_port` (21/21 route checks), `r2_*` lifts and ladders.
