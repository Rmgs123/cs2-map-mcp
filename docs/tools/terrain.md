# Terrain, dirt roads, streets and scatter

| Task | Take |
| --- | --- |
| Hills, valleys, ridges, bumps, a flat bed for a road | `add_terrain` with `ops` |
| A dirt road or trail across the land | `add_terrain` `paint` tracks (+ `flatten` op under it) |
| Rocks, bushes, trees on the land | `scatter` (grounded on the footprint, rocks sunk in) |
| A paved street with sidewalks, lamps, lots | `add_street` |
| One seamless paved square where streets meet | `add_junction` (after every street) |
| Fences, gates, handrails, a steep bank between two levels, bare trees, poles with wires | `add_fence`, `add_railing`, `add_bank` (+ `bank` cut), `add_tree`, `add_utility_line` |
| Ground height, slope at points | `map_info(name, about="terrain", key=terrain, points=[[x,y],...])` |
| Walk waypoints physically | `route_test(map, route=[[x,y],...], terrain=name)` |

## `add_terrain` (verified on `p9_offroad`, `t_village`)

```json
{"op":"add_terrain","name":"land","bounds":[[-2048,-2048],[2048,2048]],"cell":64,"base_z":-64,
 "ops":[{"op":"hill","center":[950,950],"radius":950,"height":300},
        {"op":"ridge","path":[[-2000,-900],[2000,-1300]],"width":300,"height":34},
        {"op":"noise","amplitude":10,"scale":150,"seed":3},
        {"op":"flatten","path":[[-1700,-1700],[-300,-1000],[1150,950]],"width":300,"z":"follow","falloff":96},
        {"op":"noise","amplitude":5,"scale":90,"seed":8}],
 "blend":true,"paint":[{"path":[[-1700,-1700],[-300,-1000],[1150,950]],"width":190,"falloff":80}]}
```

- `layer: true` (big terrains): the terrain meshes go to their own file `maps/prefabs/e_<map>_<terrain>.vmap`, pulled into the map by a prefab node, so saving the map no longer rewrites them (a 257×291 terrain is ~48 MB). Ground sampling, streets, banks and the checks work as before; the build compiles the layer in. Rebuild the terrain by replaying its `add_terrain` (the file is overwritten).
- `bounds` XY (or XYZ: `mins.z` gives `base_z`); `cell` 32..256 (64 for detailed ground); grid ≤ 512×512; chunks of ≤ 64×64 cells share edge heights (no seams).
- `ops` in order: `hill`/`valley` (`center`, `radius`, `height`), `ridge` (`path`, `width`, `height`), `plateau` (`polygon`, `z`, `falloff`), `noise` (`amplitude`, `scale`, `seed`), `flatten` (`path`, `width`, `z` or `"follow"` = grade between sampled ends, `falloff`). A small `noise` after `flatten` makes a bumpy dirt road. `bank` (`path`, `side`, `angle`, `verge`, `sink`, `width`): cuts the lower side under a steep face down to the lowest ground within `width` (default 512) — put it after the ops that make the two levels, then dress it with `add_bank` (below).
- Or a heightmap: local grayscale PNG + `height_range:[min,max]`; 16-bit PNGs keep 65536 levels. Pixel column i is the i-th x step and row j the j-th y step from `bounds` min (row 0 = lowest y: do not flip an image made with y up).
- `blend:true` (default): Valve's grass/dirt blend shader; dirt in hollows (`dirt_height`, default min+64), rock on slopes over `rock_slope` (42°). `blend:false`: one material per face. `materials`: `grass`, `dirt`, `rock`, `sides`, `blend`.
- `paint`: up to 32 dirt tracks `{path, width, falloff}` — a soft dirt band in blend mode (faces in the band get `dirt` with `blend:false`). Roads, trails, tyre tracks.

Points on the terrain anywhere in `map_edit`: `{"terrain":"land","at":[x,y],"z":"ground","offset":8}` (spawns +16, nav seeds +8).

## `scatter`

```json
{"op":"scatter","name":"stones","terrain":"land","category":"rocks","density":6,"slope_max":35,
 "min_distance":260,"seed":5,"route":[[-1700,-1700],[1150,950]],"scale":[0.6,1.0]}
```

`models` (paths or `kit/key`) or `category` `vegetation_med` / `rocks`; `density` per 1000×1000; `area` (`terrain`, `{box}`, `{polygon}`); `height`, `slope_max`, `min_distance`, `seed`, `random_yaw`, `scale`, `align_normal` (rocks: true), `solid`, `fademaxdist`, `route` and `keep_clear` (kept free). Each prop stands on the **lowest ground under its whole footprint** and is sunk by `sink` units (default: rocks 22 % of their height, others 3), so nothing hangs over a slope. Roads, rooms, buildings, spawns, story routes and existing props are avoided.

## `add_street`

```json
{"op":"add_street","name":"main","terrain":"land","path":[[-1600,-500],[0,0],[1600,500]],"width":256,
 "sidewalk":{"width":64,"curb":6},"max_slope":12,"furniture":{"lamps":480,"benches":600}}
```

Paved road with curbs and sidewalks following the terrain (+2) or `z:"path"`; width 48..4096; every triangle checked against `max_slope`; mitred bends; equal-height crossings cut sidewalks; `lots:{depth,length,gap}` returns building plots; `furniture` (lamps, benches, bins, bollards: spacing in units), `details` (manholes, grates), `markings`, `road_material`. A dirt road without curbs is `paint`, not `add_street`.

## `add_junction`

```json
{"op":"add_street","name":"lane_a","terrain":"land","path":[[700,-1500],[1300,-1500]],"width":192}
{"op":"add_street","name":"lane_b","terrain":"land","path":[[1500,-700],[1500,-1300]],"width":192,"sidewalk":false}
{"op":"add_junction","name":"fork","polygon":[[1250,-1750],[1750,-1750],[1750,-1350],[1550,-1250],[1250,-1250]]}
```

One paved plaza where streets meet, also at different heights: inside the `polygon` (any simple outline over the road ends) the streets give way (road, sidewalks, curbs); the plaza takes the road surface's own height wherever a road lies under it and a smooth blend of the roads' heights elsewhere, never less than 2 units over the ground, so every mouth meets its road without a step or a gap. `streets` (names; default every street that reaches the outline), `material` (default the first street's road), `cell` (16..512, default 64: finer follows the blend closer), `thickness`. Add every street first: a street added later into a plaza is refused. Sidewalks end at the outline in a curb. `"streets": []` makes a paved area (a parking lot, a yard) that follows the ground 2 units over it.

## Fences, railings, banks, bare trees

All four follow the ground: a point's z is the terrain under it (or `"terrain"` names one), else the path's z, else 0. Posts are added where the ground bends between two posts, and a span's bottom goes down where the ground falls under it, so nothing floats or sinks into a slope.

```json
{"op":"add_fence","terrain":"land","kind":"sheet","path":[[-1800,-600],[-1200,-600],[-900,-300]],"gates":[{"at":300,"width":120}]}
{"op":"add_fence","terrain":"land","kind":"chainlink","path":[[-1800,-400],[-1200,-400]],"height":96}
{"op":"add_railing","path":[[-1800,200,-360],[-1400,200,-360]],"kick":true}
{"op":"add_tree","terrain":"land","origin":[-1500,600],"height":420,"seed":4}
{"op":"add_fence","terrain":"land","kind":"rubble","path":[[-1800,900],[-1100,900]],"gates":[{"at":350,"width":110,"kind":"closed"}]}
{"op":"add_utility_line","terrain":"land","poles":[[-1800,1200],[-1400,1250],[-1000,1200]]}
```

- `add_fence` `kind`: `sheet` (corrugated on posts with rails), `planks` (boards with `gap`), `chainlink`, `mesh3d` (welded mesh), `panel` (concrete), `wall` (masonry with a cap: `cap` false or a material), `rubble` (rubble stone with a concrete cap), `gabion` (stone baskets in a wire cage). `height`, `post_spacing`, `thickness`, `sink`, `material`, `post_material`. `gates` [{`at`: distance along the path, `width`, `kind`: `open` (a gap), `closed` (two leaves), `wicket` (one leaf)}] get their own posts; leaves are solid, `gate_material`. See-through kinds (`chainlink`, `mesh3d`) are render-only with a player clip; `clip:true` (or a height) adds a player clip behind any fence, e.g. the play-area edge.
- `add_railing`: round posts and rails (render-only) along a walking line plus a player clip of rail height (`clip:false` to drop it), `kick` plate; `height` 24..72, `rails`, `radius`, `post_spacing`. Pass z in the path on stairs and landings.
- `add_utility_line`: `poles` [[x,y],...] on the ground, `kind` `wood`/`concrete`, `height` (320), `cables` 1..6 on a crossarm, `sag` (24), `arm` width: poles are solid, crossarms and wires render-only.
- `add_tree`: a leafless tree — solid trunk (stops players and bullets), render-only branches; `height`, `radius`, `branches`, `lean`, `seed`, `material`.
- `add_bank`: a steep earth bank under an upper edge (`side` = where the lower ground is, seen along the path): players jump down, nobody walks up (over 45°). `angle` 46..85, `verge` (flat strip on top), `sink`, `material`, `verge_material`, `bottom` (else the lower ground). The result lists `climbable` places (drop under the 56-unit jump: lower the ground or add a clip). On a terrain cut the ground first with the `bank` op along the same path and side, else the gentle slope covers the face (the op refuses or reports `buried`):

```json
{"op":"add_terrain","name":"lanes","bounds":[[4096,-1024],[6144,1024]],"cell":64,"ops":[{"op":"plateau","polygon":[[5200,-1024],[6144,-1024],[6144,1024],[5200,1024]],"z":160,"falloff":400},{"op":"bank","path":[[5210,-900],[5210,900]],"side":"left"}]}
{"op":"add_bank","terrain":"lanes","path":[[5210,-900],[5210,900]],"side":"left"}
```

`check_map` warns `bank_walkable_around` when ground under 45° leads from a bank's foot to its top beside it (past an end, as a rule): extend the bank or close the way with a fence or a clip, then walk it with `route_test`. It does not report a bank or its cut as a `steep_ramp`; `visual_check` does not report fence rails and wall caps as floating.

## Check

`visual_check` reports props hanging over the ground (`visual_prop_floats_on_slope`); build `full` (nav over the land); frames from above and from the side. Bots and the car need `point_nav_walkable` seeds and real slopes under ~35° on routes.

## By hand

A terrain is a closed mesh (`add_mesh` shape `mesh` or `terrain` for small patches); `paint_blend` and `texture_faces` paint selected faces of ordinary meshes (up to 8192 faces).
