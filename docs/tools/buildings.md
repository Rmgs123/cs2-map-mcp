# Buildings and facade references

The builders here are **quick fill in the Inferno look** (Mirage parts are partial). They accept an own style (`"style":"my:<name>"`: materials, tints, sizes) but their details stay Inferno; any other look is built by hand — section `styles`.

| Task | Take |
| --- | --- |
| Floors, sidewalks, steps between levels | `add_paving` |
| House fronts along playable streets | `add_facade` |
| A free-standing house | `add_building` |
| A whole town for a huge map, cheap, faded at distance | `add_district` |
| Parts, openings, palettes of a style | `style_info(kind="building")` |
| From a photo of a facade | `reference_facade` → `material_from_reference` |
| Compare with a reference | `reference_compare` |

The H extension builds a measured, closed house in one `map_edit` operation. It uses stock CS2 props for facade details and VMAP meshes for walls, openings, floor, ceiling, bands and the roof. Units are CS2 inches.

## Floors first: `add_paving`

```json
{"op":"add_paving","name":"quarter","rooms":"all","areas":{"yard":{"surface":"plaza"},"lane":{"surface":"alley"}}}
```

Paves the real floor of layout rooms (open rooms by default): the floor slabs are split into boxes with area materials, their top stays exactly on the room floor, so collision, spawns and nav do not move. Surfaces (`style` `inferno` or `mirage`): `street` (cobble, sidewalks with curbs and a gutter), `plaza` (red brick, sidewalks, stone border), `alley` (flagstones with a centre band), `courtyard` (hex stone). Default: rooms with a bomb site or rescue zone, or named plaza/piazza/square, become plazas, rooms at least 288 wide streets, narrower ones alleys. Per area override `floor`, `sidewalk` (width, `false` or `{width,height,curb,sides,material,curb_material}`; height 6 by default), `gutter`, `band`, `border`. Sidewalks are solid steps along the walls, cut at passages; props standing there are lifted onto them. A passage between two floor levels gets solid stairs in the lower room (`steps:false` to skip); route checks and `playtest(kind="timings")` treat them as a path. Pave before `add_facade`: fronts stand on the sidewalk. Never lay render-only slabs on a floor: `visual_check` reports them as `visual_phantom_floor`.

## Streets of house fronts: `add_facade` (use this for playable streets)

`add_building` makes a closed, solid house. A playable street (a competitive corridor, a story alley) already has walls from `add_layout` or `add_comp_plan`; do not put houses into the corridor. Dress its walls instead:

```json
{"op":"add_facade","name":"main_south","on":{"room":"street","side":"-y","from":0,"to":1},
 "floors":[2,4],"floor_height":144,"house_width":320,"style":"building_inferno","seed":3,"arcade":0.3,"loggia":0.3}
```

`on` names a layout room and the wall to dress (`-x|+x|-y|+y`, `from`/`to` are fractions along it); or give `from`/`to` world points of the bottom edge plus `normal` (out of the wall, into the street), `thickness` and `wall_height` for a plain wall. The row is split into houses of about `house_width` (or explicit `houses:[widths]`), each with a plaster from the style palette — for Inferno de_inferno's own `inferno_plaster_facade_01` painted with the mesh tints the shipped map uses (cream, ochre, terracotta); override with `materials:[path | {"material":path,"tint":[r,g,b]}]`. Every house gets windows on each floor (`windows:{parts,per_floor,bottom}`), a door every `doors.every` houses, and, by default, balconies, awnings over short doors, lamps, downpipes, a plinth, floor bands, a cornice and a roof eave (`balconies|awnings|lamps|gutters|bands|cornice|roof_details:false`, `plinth|eave` in units to change). Roofs get chimney pots and, on some ridges, TV antennas (`roof_details`). Windows, shutters and doors in niches stand 1 unit off the niche back (no flicker). Layout walls may be lower than the fronts (320 is fine): fronts raise them where needed.

`floors` may be a range `[2,4]`: neighbours then differ in height and cornice line. Where the space behind the wall is free (`depth`: `auto` up to 288, a number, or `0`), each house becomes a real block: the layout wall is replaced by a solid front with niches (windows with stone frames and glass, doors with stone surrounds, shop fronts with signs and awnings, balcony doors), a solid body and a tiled gable roof (`roof:{type:gable|shed|flat|none,pitch,overhang,material,chimneys}`) with an eave over the street; `shops` is the share of ground floors with a shop front. `arcade` (`true`, a share like `0.3`, house indices, or `{houses,depth:112,pier:28}`) turns the ground floor into a walkable arcade: stone piers with segmental arches, a raised tiled floor, shops and doors in the back wall (the storey is 48 units taller); `loggia` (same forms, `depth:56`) opens the top floor of houses with 3+ floors into a gallery with posts, a parapet and balcony doors. Rows along X fill the empty square behind a room corner, so roofs close from above. On a shared wall the house stays a thin skin: glazed windows in stone frames or kit shutters on the skin and a tiled lean-to over the wall (both rows meet in a ridge); it grows to the wall top. The street face stays where the layout put it, so routes keep their width; arcades and niches only add space. Dress both sides of shared walls: corners keep one downpipe, rows along X own room corners (rows along Y end cornice and lean-to 24 short, so eaves never cross), a wall raised by both sides is raised once, and a body backed by a taller wall of the next room grows storeys (up to the `floors` maximum) so no bare wall shows above its roof.

Nothing is added in front of the face that a player can touch: details are non-solid and protrude at most a few units at head height or above, so routes and timings do not change. When the fronts are taller than the wall, the wall is raised behind the face. Layout openings on the face stay clear (no window, door, pipe or lamp in them). Check with `visual_check` and frames from the street.

## A town for huge maps: `add_district`

```json
{"op":"add_district","name":"old_town","area":{"mins":[-3000,-3000],"maxs":[3000,3000]},"block":[768,640],
 "street":192,"floors":[2,4],"fade":6000,"seed":3}
```

Fills `area` with blocks (`block` [w,d]) and streets (`street` wide, paved), sidewalks (`sidewalk` 32, 6 high) and rows of simple houses: a tinted box body, a gable or flat roof (`roofs` list), window boxes with a window image and a door on every street side. No props and no cutting: 6000×6000 is about 250 houses, 16 000 faces, two seconds. `fade` sets the draw distance of every mesh (0 = always), `collision:false` makes a background town without physics, `materials` {walls, roof, street, sidewalk, trim, window, door} or `"style":"my:<name>"` change the look, `z` the ground height. Use it for open-world towns and the view beyond the play area; the streets players fight in still deserve `add_facade` or by-hand houses.

## Styles

`style_info(kind="building", name?)` lists the installed `building_inferno` and `building_mirage` kits. Each part includes its measured render bounds, category, mount, candidate facing, optional `[width,height]` opening, stock model and material palette. `verified:"bounds"` is measurement evidence; a part becomes `verified:"game"` only after an opened test-wall frame.

## `add_building`

Use this as a `map_edit` operation:

```json
{"op":"add_building","name":"house1",
 "footprint":{"mins":[0,0,0],"maxs":[512,384,0]},
 "floors":3,"floor_height":144,"style":"building_inferno",
 "wall_material":"materials/cs_italy/plasterwall04.vmat",
 "facades":{"-y":{"windows":{"part":"window_stone_40","per_floor":3,"floors":[1,2]},
   "door":{"part":"door_wood_double","at":0.5},
   "balconies":{"part":"balcony_128","floors":[1]},
   "sign":{"text":"TRATTORIA","floor":0}},
   "+x":{"windows":{"part":"window_shutters_green","per_floor":2,"floors":[1,2]}}},
 "roof":{"type":"gable","pitch":25,"overhang":16,"material":"materials/cs_italy/tileroof01.vmat","ridge":"x"},
 "extras":{"chimneys":1,"gutters":true},"enterable":false}
```

`footprint` is an XY rectangle with a single floor Z. `floors` is 1..6 and `floor_height` is 112..256. Facade keys are `-x`, `+x`, `-y`, `+y`; windows, doors, balconies, awnings and lamps take a kit `part`, floor list, normalized `at` and optional `scale`. A detail with `opening` cuts a real wall hole; every other window gets a dark inset backing so the room is never an accidental void. A non-enterable door is closed decoration. `enterable:true` leaves the first-floor door open, adds a first-floor room ceiling and a warm light, and requires a door on one facade.

Roofs are `flat`, `parapet`, `gable`, `hip` or `shed`; `pitch` and `overhang` are in degrees and units. `extras.chimneys` is 0..4 and `extras.gutters` adds measured gutter/downpipe props. A building is rejected above 60 model props or when openings overlap, cross a corner, or exceed the selected floor. The result returns the group ID, one facade group per side, mesh/prop IDs, opening bounds and model count. `visual_check` and a full build remain the acceptance gates.

## Frames

`game_screenshot(map, views=[...])` (names like `h_front`; `pos` = feet, `ang` = pitch/yaw/roll); it waits for the round to be live.

Window `infill` may name a second window part such as `window_bars` or `window_glass`; it is fitted behind the measured opening. `mount_transform` treats `at` as render-bottom and tangent centre, so bounds and opening bottoms remain explicit.

## `reference_facade`

`reference_facade(image, quad?, real_width?)` reads an existing local PNG/JPG. `quad` is four source pixel corners in TL, TR, BR, BL order. It writes a rectified PNG and a conservative wall crop, estimates floor rows/window columns from dark and edge profiles, and returns a palette plus a `facades` suggestion ready to edit into `add_building`. `real_width` is the known facade width in units and is used only for an estimated height. Open both images and correct the suggestion: shadows, plants, arches and signs can fool the profiles. No network or image download is used.

## `reference_compare`

`reference_compare(reference, shot)` creates a same-height `REFERENCE | OUR SHOT` PNG under `.local/scratch/H/references/`. It is a visual review aid; name three visible differences before changing the map.

## By hand

A house by hand: walls as boxes or a `room` shape, openings with `cut_mesh`, a roof as an `extrude` of a triangle profile, frames and shutters from kits (`kit_info`), glass (`material_create(kind="glass")`) 1 unit behind its frame, belts and cornices with `add_trim`, details shaped with `edit_mesh` (bevel, inset). Another look for the quick builders: an own style (`styles`).
