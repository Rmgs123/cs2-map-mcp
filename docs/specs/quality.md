# Quality: checks, light presets, cubemaps, camera views, fog and light shafts

The static audit (`check_map`), lighting presets with post-processing, runtime cubemaps, automatic camera views, bot play tests, distance fog and light shafts. Code: `checks.py`, `lighting.py`, `playtest.py`, `compiler.py` (cubemaps), `game.py` (views), `maptypes.py`; tools `check_map`, `lighting_build`, `lighting_measure`, `game_screenshot`, `playtest`.

## How it works

**`check_map`** loads the map read-only and returns `{map, ok, type, problems, tests_at_the_end}`; a problem has `severity`, a machine `code`, a node id, a message and a fix. Floors are tested with rays against real triangles, solid containment and a free 32×32×72 hull, spawns 48 apart; box coverage of buy zones stays a fast heuristic. Steep faces, step jumps and gaps between gameplay anchors are flagged conservatively. Also checked: FGD classes and keys, asset references, I/O targets, teams, objectives, `point_nav_walkable`, sun, sky, light-probe coverage, `toolslightmapres`, own props over spawn hulls (`prop_over_spawn`, from `model.json` bounds). The **map type** decides which rules apply (`maptypes.py`); feature areas add checks through `checks.register_check`.

**Presets** (`lighting.apply_preset`): sun, sky and a master `post_processing_volume`; `inferno` has its own numbers (`light.md`). `fog` is a string (`none`, `light`, `heavy`) or an object (`mode`, `density` 0.01..3, `draw_distance` up to 16384, `start_distance`, height fog). The accepted haze is `env_gradient_fog` (`mcp_atmos_gradient`: colour, strength 1, max opacity = density, start, end = draw distance); older fog entities are disabled. Paired frames on `o_harbor` show distant warehouses losing contrast only with it on.

**Cubemaps** are not a compiler step: after the map loads, `buildcubemaps` goes through VConsole; the tool rides out the dropped connection, waits for the reload, checks a fresh VPK and `env_cubemap_array`, and restores `r_always_render_all_windows`. `lighting_build` chains build and bake. Valve's maps use `env_combined_light_probe_volume` (probes and cubemaps together) over the play area, sometimes smaller ones around objects; there are no separate `env_cubemap` in them.

**Views.** `game.views` runs under the CS2 lock: checks the loaded map, waits for a live player through a tokenised snapshot of a diagnostic `point_script` (created at run time with `ent_create` if the map has none; the map file is not touched), moves a noclip camera (`pos` = feet, `getpos` = eyes +64; repeated up to three times until position and angles match within 2), and checks every frame for staleness and emptiness. Automatic views (`views="auto"`, six per call, `offset` continues) come from spawns, objectives, map bounds and rooms found on triangles (a bounded grid, rays down to floors and up/sideways at floor+72, voting on wall/floor/ceiling hits; local axes for rotated meshes). Views that would land above a roof are dropped or moved to the facade below.

**Play tests** (`playtest(kind="bots")`): samples go to `logs/playtest-<map>-<time>.json`; the answer has durations, team totals, a few live positions, deaths, weapon pickups, unique errors, the nav count of the last build and the samples path. Deaths are counted between samples and can be missed.

**Light shafts** (`add_light kind:"shaft"`): Valve makes god rays as planes with `effects/smoke/lightshaft_00x.vmat` (a soft rectangle mask, bright near V≈0.1 fading to V=1, with noise; the shader fades planes seen edge-on). The rectangle of an opening (`window`, its thin axis the wall normal) or `width × height` around `origin` is swept along the lamp or sun direction; each corner's length is a ray against solid triangles. Four planes through the axis (two diagonals, two middles), each as a front and a back mesh (the material is one-sided; a two-sided strip is not manifold); three rows of vertices with alpha 0 at the opening, 1 at 20 % and at the end, 0 at the edges; UV across 0..1 and along 0.1..0.92; `tintColor` = colour × intensity. `beam: "additive"` (`assets_prepare(kind="light_shaft")`) uses the same shader and stock masks with fog disabled, so rays stay visible in dense gradient fog.

## Pitfalls we hit

- The first shafts fanned out from a point with UV from world coordinates after rotating the node: a clipped "spot" that never reached the floor. Rewritten as sweeps of the opening.
- The installed `csgo.fgd` removes the fog keys from lamps; they are not restored by guessing. Lamps do not make volumetric rays by themselves.
- Volumetric fog: the controller and volume exist at run time (`ent_find` sees them) but bounds, master, indirect, draw distance, density and refresh gave no visible scattering on unchanged light: experimental only.
- In dense fog a shaft plane takes fog at its own depth and can look darker than the fog behind it.
- Auto views are a heuristic: narrow rooms, odd shapes and props with outer collision can be missed.

## Tests

`tests/test_quality*.py`, `tests/test_maptypes.py`; in game `mcp_accept`, `mcp_showcase`, `l_atmos` (auto views), `int_shafts`, `m_storm` (shafts), `o_harbor` (gradient fog on/off).
