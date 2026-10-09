# Themes and room dressing

| Task | Take |
| --- | --- |
| Interior palettes (warehouse, office, ...) | `style_info(kind="theme")` |
| Furnish a room keeping routes clear | `dress_room` |
| Trim, pipes, cables on walls | `add_trim`, `add_pipes`, `add_cables` |

`style_info(kind="theme")` returns installed stock palettes for `warehouse`,
`industrial`, `office`, `dust`, `harbor`, `sewer`, and `night_street`.
Each palette has six material roles (`floor`, `walls`, `ceiling`, `trim`,
`metal`, `wood`), props with measured render/physics bounds and dimensions,
wall decals, and indexed sound-event suggestions. Units are CS2 inches.
`material_verified` and each prop's `verified` flag refer to opened material/prop
frames; the top-level `verified` remains false while any required element lacks
an accepted live frame. `has_physics` describes a compiled body; dressing
creates static props.
Sound suggestions are not added automatically and V has not verified their playback.

Palette `verified` flags refer to game screenshots that were opened and
inspected.

## dress_room (map_edit operation)

```json
{"op":"dress_room","room":{"mins":[-256,-256,0],"maxs":[256,256,192]},
 "theme":"warehouse","density":0.5,"seed":3,
 "keep_clear":[[[160,-64,0],[256,64,128]]],"max_props":24}
```

`room` is the empty interior, with `mins.z` at floor height. The room must
be at least 96 x 96 x 112 units. Optional `density` is 0..1 (default .5),
`seed` is a 32-bit integer (default 0), `max_props` is an integer 0..64
(default 24). Density 0 creates nothing. `keep_clear` is a list of up to
128 world-space `[mins,maxs]` boxes; reserve buttons, ladders, story objects,
and any route or installation that needs more room.

With K's `add_layout` metadata present, `room:"hall"` selects a named room.
Optional `layout:"wh"` disambiguates room names in different plans.
Moved layout groups are supported; rotated/scaled plans are refused.
Explicit bounds work independently of the layout extension.

The operation places measured props along walls, adjusts their origin so
their base rests on the floor, and probes the centre and four corners
downwards. It skips holes, uneven support (>1 unit), walls, existing solids
and props, new-prop overlaps, spawns and supplied clear boxes. It reserves
a 96-unit central cross, wall openings detected at player heights, routes
from each opening to the centre, and door/button/ladder/mover space.
The requested density is a target: crowded rooms may get fewer props or none.

`lights:true`, `decals:true`, and `details:true` are defaults. Lights use
the existing `add_light` recipe (up to three ceiling lamps). Set `lights:false`
to preserve a dark/scripted room. Decals go on solid walls away from openings.
Details add a high pipe run where space permits and a +Y-wall world-text label.
`label` is up to 48 characters (default theme name; `""` suppresses text).
Set `details:false` to suppress both details. These flags accept booleans only.
No sounds or interactive physics props are spawned.

The result lists created `ids`, `placed` items (`kind`, prop model/origin/bounds),
`props`, `target_props`, `attempts`, skip reason counts, detected `portals`,
and effective `keep_clear`. `ref` points to the first created node when any
exists. Repeating the operation adds another pass; it does not replace the first.

## add_trim (map_edit operation)

```json
{"op":"add_trim","room":{"mins":[-256,-256,0],"maxs":[256,256,192]},
 "theme":"warehouse","height":8,"depth":1.5}
```

Builds a visual baseboard/stripe around existing walls, splitting at detected
doorway gaps and supplied clear boxes. `room`/`layout` are as above. `material`
overrides the theme trim. `height` is .25..64 units (default 8), `depth` is
.1..8 (default 1.5), `z` is an absolute world height (default room floor).
`walls` selects `-x`, `+x`, `-y`, `+y` (all by default). `keep_clear` and `ref`
are supported. Trim has no collision and returns all mesh IDs.

## add_pipes and add_cables (map_edit operations)

```json
{"op":"add_pipes","points":[[-80,-460,400],[300,-460,400],[600,-460,400]],
 "theme":"warehouse","radius":4,"fittings":true}
```

Pipes connect 2..33 world points with closed cylinders. `radius` .25..32
(default 4), `sides` integer 6..32 (default 12), `theme` default industrial,
optional exact `material`. `fittings:false` by default; true adds the measured
stock gas-pipe fitting at intermediate points in themes that include it.
The fitting is an upright decorative pipe assembly, not a generated elbow.
Meshes and fittings have no collision. Keep the entire run above routes or
against walls; this explicit operation does not infer room boundaries.

```json
{"op":"add_cables","start":[-80,-390,410],"end":[580,-390,410],
 "sag":24,"radius":0.75,"segments":12,"theme":"warehouse"}
```

Cable endpoints are exact world points. `sag` 0..256 (default 12) is the
maximum vertical drop at the midpoint; `radius` .1..4 (default .75),
`segments` integer 2..32 (default 12). The curve is a parabola discretized
into six-sided closed tubes. Optional `material`, `theme`, and `ref` work
as for pipes. Both operations return `ids` and `visual_only:true`.

## Verification and limits

Build with `full`, call `game_screenshot` with explicit room views, and open
the PNGs. `script_test(steps="auto")` checks story logic by teleporting; it
does not prove walking clearance. Placement is conservative authoring geometry
analysis; stock collision behaviour and performance still need game testing.
Decorative tubes are nonblocking; dynamic/rotating obstacles and irregular
terrain should have explicit `keep_clear` boxes.

Unknown parameters, nonfinite vectors, invalid themes/materials, unsupported
model bounds, ambiguous named rooms, and transformed layout plans fail with
an actionable error. The surrounding `map_edit` retains atomic save, backups,
and refusal for unsaved Hammer changes.

## By hand

A room by hand: stock props with `add_model` (front rule, 40+ units between groups on routes), materials per face with `texture_faces`, a trim as a thin `add_mesh` box sunk 1 unit into the wall, pipes as `extrude` of a circle along a path with a metal material, cables as `extrude` of a small circle along a sagging path. Save a palette you like as an own style (`styles`).
