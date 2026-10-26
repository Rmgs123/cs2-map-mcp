# Build anything

| Task | Take |
| --- | --- |
| Any polygon solid, holes, concave faces | shape `mesh` |
| Profile along a path (cornice, rail, pipe, arch) | shape `extrude` |
| Columns, vases, domes | shape `revolve` |
| Doors and windows in a mesh | `cut_mesh` |
| Bevel, inset panels, extrude, bend, twist, taper, noise, mirror an existing mesh | `edit_mesh` |
| Material, UV, tint on chosen faces; two-material blend | `texture_faces`, `paint_blend` |
| Inspect a mesh | `map_info(about="geometry", key=id)` |

Use `map_edit`; units are inches, +Z is up.
`map_info(name, about="geometry", key=id)` inspects a mesh by ID or `@ref`.

## Shapes

All fields below go inside `add_mesh.shape`. `solid:true` is the default:
closed, outward shells are required. `solid:false` permits an open surface,
sets collision to `none` and defaults baking off. Brush entities need solids.

`mesh`: `vertices` XYZ, `faces` lists of indices or objects with `vertices`,
`holes` (lists of index rings), `material`, `uv`. Planar concave faces and
holes are triangulated; convex faces remain polygons. Face UV accepts
`mode:world|box|planar|wall`, `scale:[u,v]` units/texel (default .25) or `"auto"`
(the material's real size), `rotate` degrees, `offset:[u,v]` texels, optional `u_axis` and
`v_axis`. `wall`: u along the face's level edge, v straight down — bricks stay level on a wall
turned to any angle.

```json
{"op":"add_mesh","shape":{"shape":"mesh","solid":false,
 "vertices":[[0,0,8],[128,0,8],[128,128,8],[0,128,8],
 [32,32,8],[96,32,8],[96,96,8],[32,96,8]],
 "faces":[{"vertices":[0,1,2,3],"holes":[[4,5,6,7]],
 "uv":{"mode":"world","scale":[0.5,0.5]}}]}}
```

`extrude`: `profile` XY ring, optional `holes`; `start/end` XYZ or `path`
XYZ points. A path arc is `{center:[x,y,z],radius,start:0,angle:180,
segments:12,axis:[0,-1,0]}`; angles are degrees. `closed:false` closes the
path when true; `caps:true` closes open ends. `up` fixes the profile's
vertical direction. Default +Z falls back to +Y for a vertical first leg.
Profile X follows cross(up,tangent); Y is projected up. Singular frames,
reversals and intersecting bodies fail.
`materials:{start,end,sides}` overrides parts.

```json
{"op":"add_mesh","shape":{"shape":"extrude",
 "profile":[[-4,-4],[4,-4],[4,4],[-4,4]],
 "path":[[0,0,64],[128,0,64],[128,128,64]]}}
```

`revolve`: `profile` radius/height points, `origin` XYZ, `axis:"x|y|z"` or
unit vector, `segments:24` (3..128), `angle:360` (0<angle<=360), `start:0`,
`profile_closed:true`, `caps:true` for partial turns. Full open profiles can
be solid if both endpoints lie on the axis. Radii must be nonnegative.

```json
{"op":"add_mesh","ref":"tower","shape":{"shape":"revolve",
 "origin":[280,690,0],"profile":[[94,0],[110,0],[110,288],[94,288]],
 "segments":24}}
```

## Editing

Operations target ONE mesh through `id`. Add `ref` on an edit to persist the
mesh name for later calls (core `add_mesh` alone stores only a batch ref).

```json
{"op":"cut_mesh","id":"@tower","ref":"tower",
 "cutter":{"shape":"box","mins":[166,658,136],"maxs":[183,722,220]},
 "material":"materials/de_inferno/stone/inferno_stone_wall_09.vmat"}
```

`cut_mesh` subtracts a world-coordinate `box` or convex `prism`. New cut
surfaces use `material`; original UV/tint/blend interpolate at intersections.
Empty results, invalid topology and unsupported streams fail atomically.

`texture_faces`: `faces` = all/top/bottom/sides/+x/-x/+y/-y/+z/-z, index
list, or `{mins,maxs}` selecting face centroids. Optional `material`, `uv`,
`tint` RGB(A) 0..1, `mesh_tint` RGB(A) 0..1 (writes Valve mesh tintColor).
UV modes: world/box (dominant axes), planar (one shared basis), cylinder
(`origin`, `axis` vector; select sides). Scale/rotate/offset as above.

```json
{"op":"texture_faces","id":"@tower","faces":"sides",
 "mesh_tint":[0.85,0.70,0.48],
 "uv":{"mode":"cylinder","origin":[280,690,0],"scale":[0.5,0.5]}}
```

`paint_blend`: `faces` default sides, optional blend `material`, `cell:64`
(4..1024), `rule:"bottom"|"corners"|"noise"` or list, `radius:96`, `seed:1`,
`strength:1` (0..1), `base_z` (selected minimum by default). Splits the
selected faces by the world grid x/y/z = k·cell (also after `cut_mesh`); writes layer-2 weight in VertexPaintBlendParams.x. Rules
combine by maximum. Bottom fades upward; corners fade from horizontal
bounds; noise is seeded. Shader: csgo_environment_blend.vfx; tint is preserved.

```json
{"op":"add_mesh","ref":"wall","shape":{"shape":"box","mins":[0,0,0],"maxs":[512,16,256]},
 "material":"materials/de_inferno/stone/inferno_stone_wall_09.vmat"}
{"op":"paint_blend","id":"@wall","faces":"-y","cell":64,
 "material":"materials/de_inferno/stone/inferno_stone_wall04_plaster03_blend.vmat",
 "rule":["bottom","corners"],"radius":96,"seed":8}
```

## `edit_mesh`: change a mesh piece by piece

`steps` run in order on ONE mesh (`id`); the result must stay a valid closed solid or nothing changes. Faces that move or appear get world-aligned UV like Hammer's (`"uv":"keep"`: textures follow the vertices instead). Bent faces are split into triangles; the compiler smooths normals up to `smoothingAngle` (40°).

| `do` | fields | for |
| --- | --- | --- |
| `move` | `verts` or `faces`, `offset`, `scale`, `rotate:{axis,angle}`, `pivot` | push corners, lean a wall, raise a roof ridge |
| `extrude` | `faces`, `distance` (negative = in) or `direction` | plinths, steps, a beam out of a wall |
| `inset` | `faces`, `width`, `depth` | panels, window frames, recessed doors, table tops |
| `bevel` | `edges`, `width`, `segments` (1 = chamfer, 2..8 round), `angle` (min sharpness, 30), `material` | soft stone edges, bench seats, column caps |
| `subdivide` | `faces`, `cell`, `axes` (`"z"` = rings) | vertices for bend, twist, taper, noise |
| `bend` | `axis`, `toward`, `angle`, `from`/`to`, `center` | arches from beams, curved walls |
| `twist` / `taper` | `axis`, `angle` / `scale` (number or [a,b]), `from`/`to`, `center` | twisted columns, obelisks, tapering towers |
| `noise` / `smooth` | `amount`, `radius`, `seed`, `along` (`normal`/`xyz`) / `iterations`, `strength` | rocks, worn stone, organic shapes |
| `mirror` | `axis`, `at`, `keep` (`+`/`-`) | model one half, mirror it |
| `weld` | `distance` | merge corners moved together |

Selections: `faces` as in `texture_faces` plus `{plane:{point,normal}}`, `{facing:[x,y,z],within:30}` and `"last"` (the faces the previous step made: inset inner faces, extruded caps, subdivided pieces); `verts` = `all` (default), `{mins,maxs}`, `{plane:{...}}`, `{faces:<faces>}`, `[[x,y,z],...]` (nearest corners); `edges` = `sharp` (every convex edge, default), a face selector (edges of those faces), `{mins,maxs}` (edge midpoints), `[[[x,y,z],[x,y,z]],...]`. Bevels cut convex edges only; their ends meet like a mitre.

```json
{"op":"add_mesh","ref":"seat","shape":{"shape":"box","mins":[0,0,16],"maxs":[96,24,20]},
 "material":"materials/dev/dev_measuregeneric01.vmat"}
{"op":"edit_mesh","id":"@seat","ref":"seat","steps":[
 {"do":"bevel","edges":"top","width":1.5,"segments":2},
 {"do":"inset","faces":"bottom","width":4},{"do":"extrude","faces":"last","distance":-2}]}
```

A rock: `revolve` a closed profile (or a `box`), then `subdivide` (`cell` 12–24), `noise` (`amount` 10–20 % of its size, `radius` about half of it), `smooth` once. A twisted column: a square `box`, `subdivide` `axes:"z"`, `twist` 90–180, `bevel` the base and the cap.

## Recipes and By hand

Tower: hollow revolve, box niches/cuts, glass 1 unit off niche backs, closed
dome profile touching/embedded 1 unit into the rim. Spiral stair: convex
sector prisms, rising <=16 per step, width >=64; extrude a square rail along
sampled helix points with up +Z. Bridge: extrude an arch along a vertical
semicircle (up perpendicular to its plane), support its endpoints with
columns and carry a deck above. Fountain: revolve a bowl with inner wall
and bottom; add a render-only water polygon inside the rim.

In Hammer use polygon/edge tools and extrude; cut a
wall with convex cutters and close the new faces, then select faces for UV
and vertex paint. If a sweep frame is singular, split the sweep into
supported paths or submit explicit planar triangles through `mesh`. For
sculpture import a Blender model with explicit collision. Keep >=1 unit
between layered surfaces. Limit faces (8192), profile points (512), path
stations (256); use coarse solids and render-only details. Validate with
check_map/visual_check, full build, opened game frames and physical routes.

## Big maps: speed

- `mesh` shapes are cached by content (in memory and in `.local/cache/meshes.sqlite`, 256 MB): replaying a
  batch of unchanged buildings skips their triangulation and checks.
- `map_edit` reuses the map it has just written while the file is unchanged, so a series of calls on a
  100 MB map reads it once. Every save still keeps a backup (compressed in the background: a 144 MB map
  becomes ~16 MB and the save does not wait for it).
- Builds: `fast` (~20 s on a town map) for shape and placement frames; `full` and cubemaps once the look matters.
- Keep source data of a map (generators, OSM extracts, drawn plans, heightmaps) in `my/maps/<map>/`: it is
  backed up with every save; `.local/tmp` is emptied after a day.
