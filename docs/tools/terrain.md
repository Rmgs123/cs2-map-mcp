# Terrain, dirt roads, streets and scatter

| Task | Take |
| --- | --- |
| Hills, valleys, ridges, bumps, a flat bed for a road | `add_terrain` with `ops` |
| A dirt road or trail across the land | `add_terrain` `paint` tracks (+ `flatten` op under it) |
| Rocks, bushes, trees on the land | `scatter` (grounded on the footprint, rocks sunk in) |
| A paved street with sidewalks, lamps, lots | `add_street` |
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

- `bounds` XY (or XYZ: `mins.z` gives `base_z`); `cell` 32..256 (64 for detailed ground); grid ≤ 512×512; chunks of ≤ 64×64 cells share edge heights (no seams).
- `ops` in order: `hill`/`valley` (`center`, `radius`, `height`), `ridge` (`path`, `width`, `height`), `plateau` (`polygon`, `z`, `falloff`), `noise` (`amplitude`, `scale`, `seed`), `flatten` (`path`, `width`, `z` or `"follow"` = grade between sampled ends, `falloff`). A small `noise` after `flatten` makes a bumpy dirt road.
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

## Check

`visual_check` reports props hanging over the ground (`visual_prop_floats_on_slope`); build `full` (nav over the land); frames from above and from the side. Bots and the car need `point_nav_walkable` seeds and real slopes under ~35° on routes.

## By hand

A terrain is a closed mesh (`add_mesh` shape `mesh` or `terrain` for small patches); `paint_blend` and `texture_faces` paint selected faces of ordinary meshes (up to 8192 faces).
