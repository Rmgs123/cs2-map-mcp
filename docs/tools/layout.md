# Layout plans and relative coordinates

| Task | Take |
| --- | --- |
| Rooms by a graph, shared walls, openings, doors, levels | `add_layout` |
| A lift between floors with call buttons | `add_lift` |
| Read rooms (interiors, floor z, openings) | `map_info(about="layout")` |
| One frame per room | `game_screenshot(map, views="rooms")` |

Implemented and tested through fresh MCP stdio and Valve's compiler on
`k_warehouse`: 40 edit operations, five rooms/levels, five story stages. The
accepted `full` build has exit 0, no errors/warnings/missing assets, and 232 nav
areas. **Game acceptance is pending:** the user's running Workshop Tools
currently returns no console output, so the map could not be loaded for images
or the story auto test. These examples are file/compiler verified, not yet
visually accepted.

`add_layout` is a `map_edit` operation. Units are CS2 units; room sizes describe
the empty interior. Rooms are listed in dependency order. The first room starts
at `origin` (its southwest floor corner). A room with `from` is attached across
one shared wall of thickness `wall`, on `side` (`+x/-x/+y/-y`). `align` is
`center` (default), `start`, `end`, or a fraction 0..1 of the available alignment
range. Rooms may also supply a world `origin`. `outdoor:true` builds only a floor;
size may have two components (reference ceiling defaults to 512 units).

`levels` is an object of named floor heights relative to the layout origin,
e.g. `{"ground":0,"upper":256}`. Rooms select `level` or `floor_z` (also relative
to layout origin). An open mezzanine uses `in:"hall"`, `at:[0.7,0]`,
`size:[256,960,192]`, `floor_z:256`, `outdoor:true`; `at` is the normalized lower
corner within the parent room. Its floor is independent, with no walls/ceiling.

Openings: either `between:["back","corr"]`, or `room:"hall",wall:"+y"`.
`at` (default .5) is the opening centre as a fraction of the shared wall length
(for a 640 wall and width 192 the valid range is .15..0.85; the error names it).
`width`, `height` are units; `bottom` is units above the higher floor (between)
or the named room floor. Optional `door` accepts the existing `add_door` recipe
options; bounds are supplied by the opening. The operation returns room interior
bounds, floor heights, opening bounds, mesh IDs and group ID. Plans persist in VMAP.

Any point field can use `{"room":"hall","at":[.5,.2],"z":"floor"}`.
`z` is `floor`, `ceiling`, or units above the floor (default 0).
Wall points: `{"room":"hall","wall":"+x","u":.3,"h":330,"offset":1}`;
positive offset moves inward; sign/decal normal is inferred when omitted.
Boxes: `{"room":"hall","box":[[.1,.1,0],[.3,.3,96]]}` expands to
`mins/maxs`; XY are fractions, Z is units above the floor. Use this directly
as `until.zone`, or as a shape/recipe object together with its other fields.
Individual `mins` and `maxs` may instead be relative points.
Use `layout:"wh"` to disambiguate room IDs shared by multiple plans.

`to` on `add_mover` is a numeric displacement, not a room-relative point. Use
`add_lift` for an automatically aligned elevator. Unknown rooms report available
IDs; ambiguous IDs require `layout`. Numerical coordinate arrays keep working.

## Compiler-verified minimal example

Use `map_create(name="k_example",template="basic")`, then this `map_edit` batch.
This exact batch passed a fresh MCP `fast` build (15 compiled/0 failed,
errors/warnings/missing assets empty). Add a story or
the normal competitive entities before checking/building a playable map.

```json
[
 {"op":"add_layout","name":"wh","wall":16,
  "materials":{"floor":"materials/concrete/concrete_floor_04.vmat","walls":"materials/concrete/hr_c/hr_concrete_wall_004.vmat"},
  "levels":{"ground":0,"upper":256},
  "rooms":[{"id":"back","size":[320,320,176]},
   {"id":"corr","size":[560,192,160],"from":"back","side":"+x"},
   {"id":"hall","size":[1024,960,448],"from":"corr","side":"+x"},
   {"id":"yard","size":[640,1280],"from":"hall","side":"+x","outdoor":true},
   {"id":"mezz","size":[256,960,192],"in":"hall","at":[0.75,0],"level":"upper","outdoor":true}],
  "openings":[{"between":["back","corr"],"width":96,"height":112,"door":{"targetname":"k_door1","open_by":"use","stay_open":true}},
   {"between":["corr","hall"],"width":128,"height":128},
   {"between":["hall","yard"],"width":128,"height":128}]},
 {"op":"add_lift","room":"hall","upper":"mezz","targetname":"k_lift"},
 {"op":"add_entity","classname":"info_player_terrorist","origin":{"room":"back","at":[0.2,0.4],"z":8}},
 {"op":"add_entity","classname":"info_player_counterterrorist","origin":{"room":"back","at":[0.2,0.7],"z":8}}
]
```

Full replayable scenario: `.local/scratch/K/k_edit.json`; it reuses the
already installed `ms_exit/ms_note/ms_glass/ms_river` artwork/materials and
`mcp.ms_hum/ms_rain/ms_signal` events from `mcp_story`. Its `until.zone` values
are room boxes, for example:

```json
{"id":"hall","objective":"Reach the mezzanine",
 "until":{"zone":{"room":"mezz","box":[[0,0.1,0],[0.9,0.9,128]]}}}
```

For a button mounted inside the +Y wall, specify thickness using two offsets:

```json
{"op":"add_button","targetname":"k_switch",
 "mins":{"room":"back","wall":"+y","u":0.25,"h":64,"offset":14},
 "maxs":{"room":"back","wall":"+y","u":0.35,"h":96,"offset":1},
 "targets":[{"name":"k_door1","input":"Open"}]}
```

## `add_lift`

Required: `room` (lower room), `upper` (an open platform with `in:<room>`).
Optional `layout` disambiguates both. It places the platform beside the selected
upper-floor edge and rejects intersection with world geometry or the standing
player's 72-unit headroom along the entire path.

| Field | Default / units |
| --- | --- |
| `side` | `-x`; upper edge `-x/+x/-y/+y` |
| `at` | .5; fraction along that edge |
| `size` | [104,96]; X/Y platform dimensions, 32..1024 |
| `gap` | 8; docking gap, 1..18 |
| `thickness` | 8; platform thickness, 1..32 |
| `targetname` | Generated if omitted |
| `speed` | 80 units/s |
| `texture` | Default dev VMAT |
| `buttons` | Optional `{lower,upper,call_lower,call_upper:{mins,maxs}}`, including relative boxes/points |
| `call_buttons` | true; false skips the call buttons |
| `ref` | Ref to the created mover |

Returns `id,targetname,bounds,to,upper_floor_z,gap,buttons`. The platform starts
with bottom at the lower floor and ends with top exactly at the upper floor.
Ride buttons `<targetname>_lower` send `Open` (up), `_upper` send `Close` (down).
Call buttons on the other side of the platform bring it to their floor:
`_call_lower` sends `Close`, `_call_upper` sends `Open` (verified: platform
parked upstairs, a walk pressed `_call_lower`, rode up, 7/7 on `int_lift`).

## Read, check and capture

- `map_info(name, about="layout", key=layout)` reads room bounds, openings, levels and mesh
  IDs without touching the game. A saved plan is available to later MCP sessions.
- `game_screenshot(map, views="rooms", offset=0, limit=6)` captures one view inside each
  room/platform, feet at floor+8. `limit` is 1..6; page using `offset`. Names
  start `k_`. The game lock protects the complete load/capture; view execution
  has a 110-second deadline. Open every returned PNG. Cameras are based on the
  plan and may need adjustment after dense dressing.
- `check_map(name)` adds layout findings: narrow/blocked opening, displaced
  door, mover path through the world, lift no longer docking, embedded button
  or sign, and rooms without a graph path from spawns. Unreachable rooms are
  warnings; player clearance is 32x72, and a doorway threshold above 18 is not
  a walking graph edge. Add normal floor-level openings or `add_lift` links.
  `add_ladder` surfaces and staircases (shape `stairs` or any mesh whose top
  faces rise in steps of ≤ 18 from one room's floor to another's) also link rooms.

## Limits and errors

- Rooms are axis aligned; there are no arbitrary polygon footprints. Indoor
  overlapping interiors are rejected. Only explicit `in/outdoor` platforms can
  overlap their parent room. Stack enclosed floors with `level`/`floor_z` and
  an explicit origin; their empty interiors must remain disjoint.
- An opening exceeding either neighbour's width/ceiling is refused before the
  VMAP save. Multiple openings may stack vertically, but their volumes cannot
  overlap. `door` cannot override bounds; all other documented door recipe
  settings are accepted.
- The first room owns the material of a shared wall. Separate materials on
  its two faces are not yet supported. Outdoor `ceiling` means a reference
  coordinate only; no roof is created.
- Core `move` on the whole layout group is supported. Group rotation/scale
  is refused. Editing individual solids does not rewrite the saved plan;
  rebuild/replay the layout when changing dimensions or openings. Reusing an
  existing layout name is refused; delete the old group through `map_edit` or
  choose a new name. Duplicate groups leave ambiguous room IDs.
- Static sweeps use actual world triangles, not props or dynamic entities.
  Known tiny light carriers (`start_off`) and non-solid movers are excluded
  from player-platform checks. Signs touching a wall are allowed; penetration
  is reported. An `add_ladder` surface whose foot is on one room's floor and whose top
  reaches another room's floor joins them; the graph does not infer scripted
  teleports or arbitrary stairs. Static checks and the story auto test do not prove manual traversal.
- Limits: 128 rooms, 256 openings, 64 levels, 4096 resulting solid boxes.

## By hand

Rooms are meshes: shape `room` (inward faces) or boxes for floors and walls, openings with `cut_mesh`, levels as raised floors with stairs (steps of 18 or less). Layout metadata only feeds the helpers (room points, checks, paving, facades); a map without it is fine. A lift by hand: `func_movelinear` + `func_button` on both floors (section `physics`).
