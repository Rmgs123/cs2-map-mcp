# Architecture

For people who change the code. The tools for models are described in [TOOLS.md](TOOLS.md).

## Data path

```text
MCP client (Claude Code / Claude Desktop / Codex)
   │ stdio, the standard MCP SDK (FastMCP)
src/cs2map/server.py        core functions → RAW; loads ext/*.py (their tools go to RAW too); serves the guide
src/cs2map/surface.py       PUBLIC MCP tools (49): merged wrappers over RAW with short descriptions
                            "what it does → parameters → example → answer"; schemas compacted (compact_schema);
                            the description budget is tested (≤ 7000 tokens). A new tool = a RAW function + a line here
src/cs2map/housekeeping.py  disk limits (config/settings.json → limits): frames, logs, map backups, .local/tmp;
                            files referenced by README/docs/data are kept; a background pass at most once an hour
src/cs2map/backups.py       map copies (zstd, tiers, pins) + deduplicated sources snapshots in backups_dir; a save
                            hard-links the old file and a worker thread finishes it; periodic pass every 30 min
src/cs2map/project.py       core operations: maps, batch editing, builds, assets, materials, game; idle map
   ├─ vmap.py       map model: nodes, entities, meshes, I/O, saving with a backup (backups.stage/submit)
   │   └─ dmx.py    lossless DMX binary v9 codec (the .vmap format), pure Python
   ├─ mesh.py       polygon meshes → half-edge CDmePolygonMesh; primitives; smoothed normals
   ├─ meshops.py    polygons with holes, extrude along paths, revolve, closure/self-intersection checks, BSP CSG
   ├─ meshedit.py   edit_mesh: move, extrude, inset, bevel, subdivide, bend, twist, taper, noise, smooth, mirror, weld
   ├─ shapes.py     JSON shape descriptions → Mesh (box, room, arch, stairs, mesh, extrude, revolve...)
   ├─ fgd.py        entity classes from the game's FGD: keys, defaults, I/O
   ├─ vpk.py, vtex.py, resource.py   reading game VPKs, compiled textures and resources (_c): references, sizes
   ├─ kv3.py        KeyValues3 text: parse and write (.vmdl, .vpost, anim graphs)
   ├─ compiler.py   resourcecompiler: build presets, asset compiles, log parsing, cubemaps
   ├─ jobs.py       background processes: log, timeout, cancel; jobexit.py writes the exit code,
   │                so a result survives a server restart
   ├─ lockrun.py    a command under a cross-process lock (VRAD3 light baking queue); retries with a larger
   │                lightmap atlas on "Too many charts"
   ├─ blender_run.py headless Blender for every pipeline: one profile, a JSON> protocol, background jobs
   ├─ game.py       CS2 Workshop Tools: launch, map_workshop, live player (a diagnostic point_script created at
   │                run time), camera, frames with staleness checks, the CS2 lock, fast start, idle map
   ├─ vconsole.py   VConsole2 protocol (TCP 29000): commands and output; CS2 accepts one client, so the
   │                connection closes after 5 s idle and on leaving game.exclusive
   ├─ system.py     Windows processes and windows (CS2/Hammer), focus, window capture, SendInput, Hammer state
   ├─ gametest.py, crashdump.py   script_test with real input, game_reset, game_crash_report (minidumps)
   ├─ addon.py, config.py   the addon from Valve's template; paths and settings (config/settings.json)
   ├─ mylayer.py    local layer my/: notes, recipes, own styles, inbox (never in git)
   ├─ maptypes.py   map types (competitive, sandbox, story, minigame...) deciding which checks apply
   │
   │  feature areas (the core does not import them; ext/<area>.py registers their tools and operations)
   ├─ models.py + blender/   environment models: templates/scripts/imports → FBX → ModelDoc → .vmdl;
   │                weapon={base}: a weapon model on a stock base's skeleton (weapon_rig.py)
   ├─ materials.py, textures.py, uv.py   materials, reference → seamless PBR maps, UV edits
   ├─ checks.py, lighting.py, playtest.py   check_map, lights/presets/fog/shafts, bot play tests, auto views
   ├─ scripting.py, story.py   cs_script writing and checks, script_test; stories from JSON → JS + HUD + entities
   ├─ route.py      route_test: real walking (+forward/+use) by stages; planner over openings, lifts, ladders, A*
   ├─ visual.py     visual_check: model bounds from vmdl_c → floating, intrusion, z-fighting, phantom floors
   ├─ outdoor.py    water to the horizon, backdrops, supports, outdoor sky
   ├─ competitive.py add_comp_plan (defuse/hostage/wingman layouts), timings, bot rounds, spawn sightlines
   ├─ layout.py     add_layout: rooms by graph, shared walls, openings, levels, add_lift, relative coordinates
   ├─ dressing.py + data/themes/   theme palettes, dress_room, trims, pipes, cables
   ├─ physics.py    doors, buttons, lifts, rotation, physics props, breakables, zones, ladders
   ├─ sound.py      stock event index, WAV import/synthesis → vsnd, events in soundevents_addon.vsndevts
   ├─ fx.py         particles, weather, lightning
   ├─ structure.py  groups, prefabs, decals, signs, world text
   ├─ kits.py, atlas.py, atlas_game.py   stock part kits; the prop atlas (index, offline previews, notes)
   ├─ buildings.py, reference.py, facades.py, paving.py, districts.py   houses, photo hints, facade rows,
   │                floors and sidewalks, quick districts
   ├─ wear.py, streetlife.py   facade wear decals, street dressing
   ├─ terrain.py, streets.py   terrain (heightmap, blending, bank cuts), scatter, add_street
   ├─ builders.py   fences, railings, earth banks, bare trees along paths over the terrain
   ├─ modes.py      DM, Arms Race, Wingman, aim, surf/bhop/kz courses
   ├─ mechanics.py, fire.py + data/mechanics/   the shared mechanics core (JS fragments) and the shot engine
   ├─ control.py, vehicles.py, turrets.py   controllable objects: drone, car, turret
   ├─ minigames.py, weapons.py, actors.py   mini-games, add_weapon, animated characters
   ├─ ui.py         UI themes, add_hud, style overrides for every generated HUD
   ├─ abilities.py, hideseek.py   player abilities, hide and seek
   ├─ radar.py, preview.py   radar overview and loading screen/icon on full builds
   ├─ release.py, chain.py   map_build(name, "release"): one map and the files it uses in its own addon
   │                (compile all, final build, PUBLISH.md); game tools take map="<addon>/<map>" (Project.released)
   └─ experimental/ not part of MCP (unsupported player-model draft)
src/cs2map/ext/*.py         register(mcp, project): tools, map_edit operations, shapes, preprocessors
docs/tools/*.md             guide sections (read_guide(section)); docs/TOOLS.md is the core
docs/specs/*.md             technical specs per area (pipeline, formats, decisions, pitfalls)
```

Extensions load automatically (`server.load_extensions`). New `map_edit` operations register with `project.register_op(name, fn)`, shapes with `shapes.register_shape(name, fn)`, `check_map` checks with `checks.register_check(name, fn)`, batch preprocessors with `project.preprocessors`, so the core does not change.

## Files and folders

- Project folder: code, tools, docs, `logs/` (log and JSON of each build). Local only: `my/` (notes, styles, `maps/<map>/` generators), `.local/` (caches, temp, scratch), `logs/`, `restored/`.
- Backups folder (settings `backups_dir`, default `<project>/backups`; best on another disk): `maps/<map>/<stamp>.vmap.zst` + `<stamp>.json` (kind, sizes, sources snapshot, pin), `sources/objects/` (file contents by SHA-256, zstd), `sources/snapshots/` (file lists), asset backups by kind. Tiers and the recovery path: [REPAIR.md](../REPAIR.md#3-backups).
- CS2 addon (`config/settings.json` → `addon`, default `mcp_lab`):
  - `…/content/csgo_addons/<addon>/`: sources (`maps/*.vmap`, `materials/mcp/<name>/`, models, sounds, panorama);
  - `…/game/csgo_addons/<addon>/`: compiler output (`maps/*.vpk`, `*_c`, lighting caches).
- Writing is allowed only there and in the backups folder: `config.ensure_inside` checks the real path, junctions and `..` included.

## VMAP format

A VMAP is a DMX document in binary encoding 9. The codec (`dmx.py`) keeps element order, the string table, prefix, stubs and orphans. An unchanged file re-encodes byte for byte (checked on all 22 `.vmap` files shipped with CS2, up to 280 MB).

Root `CMapRootElement` → `world` (`CMapWorld`) → `children`: `CMapEntity`, `CMapMesh`, `CMapGroup`... Every node has `nodeID` (int, unique in the map) and `referenceID` (uint64), issued by `VMap` and used outside as stable ids. Entity keys live in `entity_properties` (`EditGameClassProps`), all stored as strings. `origin/angles/scales` are node attributes, not keys. A brush entity's mesh is its child node; a child's `origin` is in world coordinates.

## Meshes

`CDmePolygonMesh` is a half-edge structure:

- half-edges come in pairs `(2k, 2k+1)`;
- `edgeVertexIndices[h]` is the vertex `h` points to;
- `edgeNextIndices[h]` is the next half-edge around the face (around the hole for boundary edges, `face = -1`);
- `vertexEdgeIndices[v]` is an outgoing half-edge;
- face-corner data (texcoord, normal, tangent) is stored on the half-edge, for its destination vertex.

Faces wind counter-clockwise seen from outside. `Face.colors` (RGBA per corner) writes the `VertexPaintTintColor` stream (faceVertexData, second buffer, as Valve does). UVs are projected on world axes like Hammer: axes by the dominant normal axis, scale 0.25, divided by the texture size from `vtex_c`. `MatchesValve` compares our cube with one made by Hammer: normals, tangents and UVs match at every corner. Normals are smoothed like Hammer (`smoothingAngle` 40) so curved shapes are not faceted. `weld_t_junctions` and `region_solid` make compound bodies (stairs, arches) watertight.

## Scale and axes (shared by every pipeline)

- 1 unit = 1 inch. +Z up; angles `[pitch, yaw, roll]`, yaw 0 = +X. Player 32×32×72, eyes at 64.
- Models are authored in Blender directly in CS2 units, base at z = 0, front along +X.
- FBX (environment models): the exporter writes metres, so `.vmdl` uses `import_scale = 0.0254`.
- `game_screenshot` `views[].pos` are the player's feet; `getpos` returns the eyes (+64).

## Builds

`compiler.build_map` runs `resourcecompiler.exe -i <vmap>` with a preset (`fast`, `full`, `final`; flags as in Hammer5Tools' Map Builder) in the background through `JobManager`. Success needs exit 0, a fresh `.vpk` and 0 `failed` in the log; errors, warnings, missing assets and hints are parsed out. `full`/`final` also write the radar overview (`radar.py`, a PSD the game requires as `<map>_radar_psd`) and keep the loading screen and icon (`preview.py`); bare images compile only through a hidden layout that references them.

## Batch editing

`map_edit` takes a list of operations and applies it atomically: preprocessors expand references (`@ref`, room points, `$style.key` values, adapters for own styles and UI overrides), the operations run on an in-memory map, wrappers of areas that write resources (stories, mechanics, weapons) first run the whole batch as a dry run (`project.DRY_RUN`), and only a fully successful batch is saved (with a backup; a map open in Hammer with unsaved changes is refused).

## Mechanics core

`cs_script` has no imports, so a mechanic's script is bundled by `mechanics.build_script` from fragments in `data/mechanics/*.js` (`core` log/events/telemetry, `hud`, `camera`, `seat` entry by E with a stand-in and pilot damage filter, `health` HP/explosion/respawn, `fire` shot engine) plus the mechanic's adapter; imports merge into one line, the config is a JSON literal, the first line is a marker with the config. Script names carry a config hash (`<name>_<sha1[:8]>`), so an unchanged compiled script is not rebuilt (a running CS2 holds its file). `// @requires core, hud, ...` in `script_write` gives own scripts the same fragments. The Node bench (`test_double.cjs` + `mechanics.run_harness`) runs adapters on an engine double. Controllable kinds register with `control.register_kind` (drone in `control.py`, car in `vehicles.py`, turret in `turrets.py`).

## Game layer

- **Fast start**: after each map load `mp_freezetime 0` and `mp_team_intro_time 0` (the mode config resets them, so they are set after): about 6 s from load to a live player instead of 26.
- **Frames**: a control frame at +17° precedes each frame; a matching signature means a stale frame (up to 3 retries, then a refusal). A frame's `cmd` runs after the camera moves; after `cmd`/`wait` the position is re-checked. `keep: true` frames use only `setang` (script cameras, frozen players).
- **Health**: a hung window (`IsHungAppWindow`) gets 30 s, then an error asking to restart Workshop Tools; CS2 processes are never killed. VConsole2 windows that steal the single console client are closed with `WM_CLOSE`.
- **Idle map**: when the game starts without a map, `mcp_idle` (a cozy terrace built from `data/idle/idle.json`) is built once and loaded; it is unloaded through an empty map before its own rebuild (the VPK is locked while loaded).

## Guide

`server.py` serves `docs/TOOLS.md` (≤ 20 KB: a "what to take for which task" table and the section index), `docs/tools/<name>.md` through `read_guide(section)`, all with `section="all"`, and `my` from the local layer. Every JSON example in the guides passes `map_edit` dry run in `tests/test_doc_examples.py`; `script_test` steps pass step validation.

## Setup

`setup.ps1`, `tools/setup_clients.py`, [INSTALL.md](INSTALL.md): Python environment from the lock file, CS2/Workshop Tools, Blender (Steam → `tools/` → Program Files), Source2Viewer, `my/` folders, registration in Claude Code/Desktop and Codex with a config backup; without `-Apply` only the plan is printed.

## Tests

```powershell
.\tools\python-env\Scripts\python.exe -m tests.run          # all
.\tools\python-env\Scripts\python.exe -m tests.run -k Mesh  # some
```

`tests/test_server_stdio.py` starts the server with a real MCP client over stdio, creates, edits and compiles a map (needs CS2). `.local/scratch/` holds one-off research scripts, not part of the product. Unit tests are necessary but not enough: a feature is done only after the end-to-end path (MCP → files → compiler → CS2 → frames), see [CONTRIBUTING.md](../CONTRIBUTING.md).

## Areas (details in docs/specs/)

- **Geometry** ([geometry](specs/geometry.md)): `meshops.py`, `meshedit.py`; shapes `mesh/extrude/revolve`, operations `cut_mesh`, `edit_mesh`, `texture_faces`, `paint_blend`.
- **Models** ([models](specs/models.md)): Blender script or template → render FBX + collision FBX per `_col` object → ModelDoc → compile; Blender materials named `materials/...vmat` remap to stock ones.
- **Materials and UV** ([materials](specs/materials.md)): reference → crop/homography → delight → seamless → normal/roughness/AO → `.vmat`.
- **Quality** ([quality](specs/quality.md), [visual](specs/visual.md)): `check_map`, presets, cubemaps, views, play tests, fog, shafts; `visual_check`.
- **Scripts and stories** ([scripting](specs/scripting.md)), **sound** ([sound](specs/sound.md)), **physics** ([physics](specs/physics.md)), **effects** ([fx](specs/fx.md)), **light** ([light](specs/light.md)).
- **Layout and buildings** ([layout](specs/layout.md), [buildings](specs/buildings.md), [quarter](specs/quarter.md), [wear](specs/wear.md), [streetlife](specs/streetlife.md), [dressing](specs/dressing.md), [kits](specs/kits.md), [atlas](specs/atlas.md), [structure](specs/structure.md)).
- **Outdoor** ([outdoor](specs/outdoor.md), [terrain](specs/terrain.md)), **competitive** ([competitive](specs/competitive.md)), **modes** ([modes](specs/modes.md)).
- **Mechanics** ([mechanics](specs/mechanics.md), [control](specs/control.md), [vehicles](specs/vehicles.md), [turrets](specs/turrets.md), [minigames](specs/minigames.md), [weapons](specs/weapons.md), [actors](specs/actors.md)).
- **Phase 10**: UI themes and HUD overrides (`ui.py`, guide `ui`), abilities (`abilities.py`, guide `abilities`), hide and seek (`hideseek.py`), map types (`maptypes.py`, guide `types`), own styles and notes (`mylayer.py`, guide `styles`), radar and preview.

Player models for CT/T are not provided: stock agents are used; own models are for the environment and props.
