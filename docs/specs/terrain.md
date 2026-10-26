# Terrain, streets and scatter

Height-field terrain with blended material layers, scattered props, and streets that follow the ground. Code: `terrain.py`, `ext/terrain.py`; operations `add_terrain`, `scatter`, `add_street`; tool `route_test` for walking checks.

## Terrain plan

`terrain.plan()` normalises `bounds`, picks a grid of `ceil(size/cell)` and keeps `heights[i][j]` in world XY. Chunk border vertices come from the same table. The mesh is split into blocks of at most 64×64 cells; each block is closed with side walls down to `bottom`, so the compiler gets ordinary `CMapMesh` nodes, not an open sheet. The group `terrain:<name>` carries the JSON attribute `cs2map_terrain` (version 1): bounds, cells, heights, operations, materials and `steep_fraction`.

Operations apply to every vertex in order: hill/valley (radial smoothstep), ridge/flatten (distance to a polyline), plateau (polygon with falloff), noise (deterministic value noise), heightmap (PIL greyscale, bilinear resize). `terrain.sample` interpolates over the same `p00-p11` diagonal and returns Z, the normal and the slope angle. A preprocessor turns `terrain/at/z=ground` into a world point for later operations. Moving the group is translation only: the plan shifts bounds and heights; rotation and scale are refused.

## Material layers

Valve's resources (`hr_grass_dirt_blend_001`, `blendgrassdirt002a`, `train_zoo.vmap`, `ancient_zoo.vmap`) confirm `csgo_lightmappedgeneric.vfx` with `F_LAYERS=1`, `g_tLayer2Color`/`g_tBlendModulation` and a second vertex buffer (`vertexBufferLocation=1`). The `VertexPaintBlendParams:0` stream holds RGBA weights; official meshes mostly use `.x`, sometimes `.y` and `.w`. Our terrain writes `.x` with a smooth height/slope weight and a white `VertexPaintTintColor`; with `blend: false` each face gets a fallback material by slope and height. A correct stream is not a good look: blending is accepted from in-game frames.

## Scatter

`scatter` makes a deterministic candidate sequence, target = floor(area × density / 1e6), at most 2048. It reads kit JSON (schema 1) directly and supports `kit/key`. Points are dropped outside the area, by slope or height, inside road/room/building polygons, around spawns and on story routes (`go`) or `route`/`keep_clear`. The model's measured radius counts in exclusions and spacing. Props are grouped `prop_static`; the origin is shifted by the bottom of the rotated render box. Small items (< 160 units at the largest size) are non-solid by default, trees solid; rocks turn their local +Z to the normal. `fademindist`/`fademaxdist` go to keyvalues.

## Streets

`add_street` cuts source segments into pieces of at most 128 units and takes the grade from `terrain.sample` + 2 or from an XYZ path. Miter turns share cross sections at the joint; a miter over 3 is refused. Top polygons are triangulated and closed 8 units below, including non-planar pieces. Slope is checked on every road triangle, crossfall included. Sidewalks rise by a curb of 4..8 units. Roads of different streets may cross only when Z matches within 0.5; convex clipping cuts overlaps and existing sidewalks at T and X junctions. A new street updates the mesh children of older street groups; group ids stay stable.

The group `street:<name>` / `cs2map_street` (version 1) keeps the path, surfaces (triangle XY/Z), road and side materials, width, exclusions and lots. A core move recomputes surfaces and lots; rotation and scale are refused. Lots hold a rectangle (polygon, origin, yaw, size) and a world box with one Z; buildings take axis-aligned footprints, so a rotated lot should be simplified to an inscribed rectangle and levelled with a plateau. Furniture, overlay decals and nav seeds are children of the group, so move and delete apply to the whole recipe.

Furniture is found by tags/key in the kits (`italy_street`, `street_common`), otherwise stock fallbacks; spacing per category, `models` and `scales` overrides; measured render bounds keep bases on the grade. `details` are manholes and grates, `markings` stock overlays projected along +Z. Nav seeds sit on every centreline point. Unknown fields and assets are refused before saving.

## Layers

`add_terrain` with `layer: true` writes the chunk meshes into `maps/prefabs/e_<map>_<terrain>.vmap` (a prefab document, `isprefab`) and puts a `CMapPrefab` in the terrain group instead (`targetMapPath`, the fields Hammer saves; the compiler pulls the file into the world, checked on `t11_layer`: the same vpk as inline). The plan keeps `layer` and empty `mesh_ids`. A dry run writes no file. `check_map` and `visual_check` load the layer prefabs of the addon into the map in memory (`terrain.expand_layers`), never saved.

## Junctions

`add_junction` (`streets.py`) triangulates the outline (ear clipping) and stores the triangles as convex cuts in the group `junction:<name>` / `cs2map_junction`. Every street is rebuilt by the same piece pass as `add_street` with those cuts added for roads, sidewalks and curbs; a later street whose road meets a cut is refused. The plaza is one closed solid: grid cells (`cell`) clipped by the triangles, each fanned around its centre; points where a road edge crosses the outline are added on the outline edges, so the road's corner vertices lie on plaza vertices. Heights: the mean of the road surface planes covering the point, else inverse-distance weights of road-surface samples (every 32 units along the road pieces inside the outline), raised to the ground + 2. The bottom is the top moved down by `thickness`; side faces close the edges used once.

## Fences, railings, banks, trees

`builders.py` (`add_fence`, `add_railing`, `add_bank`, `add_tree`). Points take z from the terrain under them, else the path. Stations are spaced along the path (`post_spacing`) and refined where the ground leaves the straight line between two stations by more than 4 units (down to 16-unit spans); each span's bottom goes down by the largest gap under it. Neighbouring spans touch at a post, so even and odd spans are separate meshes (no shared half-edges); a wall cap is its own mesh for the same reason. See-through fences and railings are `physicsType none` with a `toolsplayerclip` slab.

The terrain op `bank` cuts vertices on the low side of a path to the face plane (`top − sink − (d − verge)·tan(angle)`) and not below the lower level (the lowest ground within `width` along the normal, every 32 units); vertices within one cell before the toe drop to the level at once, so no grid triangle rises through the face. Past each end the plane climbs away at the same angle and the lowered yard at about 27°, so the bank's end section stays buried. The cut keeps its tops per 32 units (`bank_cuts` in the plan); `add_bank` along the same path and side takes them, so its face lies `sink` above the cut, and measures its end sections one cell inside. The bank mesh carries `cs2map_bank` (angle, footprint grown five cells past the ends); `check_map` skips `steep_ramp` within two cells of a footprint.

## Walking checks

`route_test` on terrain runs under the shared game lock: a setup frame loads the map, sets `mp_freezetime 0` and kicks bots with a readback; a unique frame name allows repeated runs. The first point is an allowed coordinate start; the rest are physical `go` steps with noclip off.

## Limits

World coordinates ±32768; heightmaps up to 16M pixels; cells up to 512×512, chunks up to 64×64; up to 128 operations. A `full` build and `route_test` in the real game are needed for nav and walking; frames judge blending, detail scale and seams.

## Tests

`tests/test_terrain*.py`, `tests/test_builders.py`; in game `t_valley`, `t_streets`, `o_harbor`; builders compiled and checked on `t11_builders` (not yet in game).
