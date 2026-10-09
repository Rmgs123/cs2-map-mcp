# Repair guide

What to do when something in CS2 Map MCP breaks: where to look, the usual causes, and how an AI agent can fix a part of the MCP and prove the fix.

## 1. Where to look

| Source | What it tells |
| --- | --- |
| `status` (MCP tool) | CS2 path, Workshop Tools, whether CS2 runs, the addon, Blender version, Node, running and recent jobs |
| `job_status(job)` | a build or bake: `state` (running, succeeded, failed, timeout, cancelled, lost), `result.errors`, `missing_assets`, `nav_areas`, the log path |
| `logs/build-*`, `logs/compile-*` | full `resourcecompiler` output and the parsed JSON of each build |
| `logs/blender-*` | Blender runs (models, previews, weapon rigs) |
| `logs/script-test-<map>-*.json`, `logs/route-test-*` | every step, console line and frame of a play test (the tool answer is a summary) |
| `game_crash_report` | the newest CS2 minidump: exception code, module, the console tail from the dump |
| `check_map`, `visual_check` | static problems with a machine code, a node id and a suggested fix |
| `map_backups(name)` | copies made before every save; `restore=<id>` rolls back |

## 2. Common failures

| Symptom | Usual cause | Fix |
| --- | --- | --- |
| `map_edit` refuses to write | the map is open in Hammer with unsaved changes (asterisk in the title) | save or close it in Hammer, then retry |
| `map_edit` refuses a field or operation | unknown field, wrong range, or a stale guide example | read the section (`read_guide(section=...)`); the error names the field and the allowed values |
| A build fails, `missing_assets` | a model, material or sound path that does not exist in the game or the addon | `asset_search` for the right path; own resources must be built first (`model_build`, `material_create`, `sound_create`) |
| `Failed to find compiler` for an image | a bare image is not compiled on its own | reference it from a material or a layout (the radar and preview code does this) |
| `Too many charts` while baking light | the lightmap atlas is too small | the build retries with a larger atlas; lower `toolslightmapres` on huge flat meshes |
| The build cannot replace the `.vpk` | the map is loaded in CS2 | the build unloads it first; if CS2 holds it anyway, load another map and rebuild |
| `job_status` says `lost` | the server restarted during the job | run the build again |
| "one console client at a time" | a VConsole2 window or another tool holds the CS2 console | the server closes VConsole2 itself; close other console tools |
| CS2 "Not responding" for 30 s | the game hung (often during a load or a cubemap build) | ask the user to restart CS2 Workshop Tools; CS2 processes are never killed by the server |
| CS2 crashes on a weapon or `SetModel` | a model not in the map's manifest, or a weapon model without the base's clips | use `add_weapon` (it adds the preload prop) and weapon models built with `model_build(weapon=...)`; read `game_crash_report` |
| A script does nothing, the console says "Relative values are not supported" | `import "./x.js"` in a `cs_script` | no relative imports: use `// @requires` fragments or one file |
| A trigger, button or story stage never fires | wrong targetname, spawnflags, or the script lost its hooks after a round restart | `script_test` with `expect` lines; runtimes re-subscribe on round start; see `docs/tools/scripting.md` |
| Players walk through a floor or wall | a render-only mesh shows a surface without collision | `visual_check` reports `visual_phantom_floor`/`visual_phantom_wall`; put the material on the real floor or make the mesh solid |
| A prop is invisible in game | spawned outside the world, or a physics-only stock model used as `prop_dynamic` | keep parked entities inside the map; use `prop_dynamic_override` for physics-only models |
| The release addon misses a file in the game | the file name is built at run time, so the release could not find it | name the asset in the map or script literally, run `map_build(name, "release")` again and check `game_screenshot(map="<map>/<map>")` |
| Bots stand still | no nav mesh (`nav_areas` 0) | build with `full`; check that spawns stand on walkable floor |
| Blender not found or times out | Blender missing or an untested version; a heavy script | `setup.ps1` shows what it found; split heavy models; the timeout is 180 s |
| Prop previews fail | Source2Viewer CLI missing | `setup.ps1 -Apply` offers the download |

## 3. After a CS2 update

Updates can rename models, change the FGD or the scripting API. Run the quick self-check:

```powershell
.\tools\python-env\Scripts\python.exe tools\selfcheck.py              # about 10 minutes, CS2 Workshop Tools running
.\tools\python-env\Scripts\python.exe tools\selfcheck.py --no-game    # files and the compiler only
.\tools\python-env\Scripts\python.exe tools\selfcheck.py car story    # only some scenarios
```

It builds `mcp_selfcheck` (a layout with a door and a button, an `edit_mesh` detail, a story, a drone, a car, a turret, a shooting gallery, a railgun on its AWP base, an ability zone, a HUD theme), runs `check_map` and a full build, plays one short scenario per mechanic with real input and takes overview frames. The report `logs/selfcheck-<time>.json` (with a contact sheet) lists every step and scenario and what changed since the previous report. Do not use the game window while it plays. If an area fails, read its spec in `docs/specs/` ("Pitfalls") and fix it as below. The map and scenarios live in `src/cs2map/data/selfcheck/`.

## 4. Fixing a part of the MCP (for AI agents)

1. **Find the area.** The guide section names the operation; `docs/ARCHITECTURE.md` maps operations to modules (`src/cs2map/<area>.py`, `src/cs2map/ext/<area>.py`); the spec `docs/specs/<area>.md` explains how it works and what was learned the hard way.
2. **Reproduce cheaply.** A minimal batch with `dry_run: true`, then a small test map. Use `tools/mcp_call.py <tool> @args.json` to run the new code on a fresh server (your chat's MCP server runs the code loaded at its start).
3. **Fix in the code, not in one map.** A defect found on a map is fixed in the tool, its checks or the guide, so no map gets it again; then rebuild the map from its batch.
4. **Test.** Add a unit test next to the area's tests (`tests/test_<area>*.py`), run `python -m tests.run -k <Area>`, then the full suite.
5. **Verify in the game.** Build (`full` when bots or nav matter), load, `script_test` with real input or `game_screenshot`; open the frames and look at them. A fix is not done before this.
6. **Update the docs.** The guide section (examples must pass dry run), the spec's "Pitfalls" if you learned something, and one line in `CHANGELOG.md`.
