# Fast loop on big maps: packages, regions, offline preview

A big map (tens of thousands of nodes, hundreds of MB) costs minutes per full replay, build and check. These ways answer a small change in seconds; none of them changes what a full build gives.

| Task | Take |
| --- | --- |
| Apply only the packages that changed | `map_edit(name, packages=["my/maps/town/args_terrain.json", ...])` → `job_status(job, wait_seconds=25)` |
| Run only the generators that are stale, then apply | `map_edit(name, packages="my/maps/town/pipeline.json")` |
| Prove the map equals a clean replay (nothing written) | `map_edit(name, packages=..., dry_run=true)` |
| Drop a package on purpose | `map_edit(name, operations=[{"op":"replace_batch","remove":["yard"]}])` |
| Look at the saved map without CS2 | `game_screenshot(map, views, window="offline")` |
| Build and load one zone only | `map_build(name, "fast", region=[[x0,y0],[x1,y1]])` → `game_screenshot(map="<name>_region", views)` |
| Zones around the tester's F8 marks | `map_build(name, "fast", region="marks")` |
| Check one zone | `visual_check(name, box=[[x0,y0],[x1,y1]])` |

Measured on a town map of 24 500 nodes, 270 MB, 40 packages, 20 500 operations: all packages into a fresh map 112 s (2.4 s when nothing changed, 37 s after a change in one late package); offline preview of 3 views 21 s the first time after a change and 3–7 s then (the scene is cached until the map changes); a region of 2400 × 2400 units: cut 18 s (9 of them reading the big map), `fast` build 14 s, loaded and photographed in 27 s — about a minute from the saved map to frames instead of a full build; `visual_check` 110 s for the whole map, at once when the map has not changed, 25 s for one zone.

## Packages

A package is a JSON file with one part of the map: the terrain, a street, a yard. Three forms are read:

```json
{"package": "yard", "needs": ["terrain"], "operations": [
  {"op": "add_mesh", "shape": {"shape": "box", "mins": [0, 0, 0], "maxs": [256, 256, 8]},
   "material": "materials/dev/reflectivity_30.vmat"}]}
```

- the arguments of a `map_edit` call, `{"name": "town", "operations": [...]}` (a leading `replace_batch` gives the name);
- a bare list of operations (the name is the file name without `args_` and `.json`).

`map_edit(name, packages=[...])` takes the **full ordered list** of the map's package files. The map remembers every package (content hash, what it needs, whose nodes it used), so one job with one load and one save replays only:

- packages whose content changed, that are new, or whose nodes were added or removed by hand;
- every package applied before a game update that changed the entity definitions (the same operations then write other keyvalues; `why`: `the game was updated`);
- packages that `needs` a replayed one, or used its nodes last time;
- with no `needs` declared: every package after the first replayed one. Declare `"needs": [...]` (`[]` = depends on nothing) to replay less.

A package that uses or changes a node of another package must name it in `needs`: the call fails with the names otherwise. Nodes that belong to no package (made by plain `map_edit`) stay; a package that changes them gets a note (a replay cannot undo it). A map with more than 40 such nodes and no package record is refused (a map built earlier from the same files would be doubled): rebuild it with `map_create(overwrite=true)`, or keep those nodes on purpose with `map_edit(name, operations=[{"op":"replace_batch","tag":"base"}])` first. A broken operation names its package and index; nothing is saved then. A list that leaves out a package the map holds is refused (a forgotten file must not delete a zone).

Result (`job_status`): `replayed`, `skipped`, `removed`, `why` (per package), `operations`, `seconds`, `warnings`.

**Clean replay.** `dry_run=true` replays every package into a fresh map in memory and compares it with the map, package by package: `ok` with the note "the map equals a clean replay", or `differs_from_clean_replay: [packages]`. `map_build(name, "release")` refuses a map updated package by package until this proof (or a rebuild: `map_create(overwrite=true)`, then the packages) has passed.

## Manifest: generators and packages

`pipeline.json` next to the generators (inside the project folder):

```json
{"map": "town", "create": {"template": "basic", "type": "competitive"},
 "steps": [
  {"run": "gen_terrain.py", "packages": ["args_terrain.json"], "needs": []},
  {"run": "gen_streets.py", "args": ["--blocks", "3"], "inputs": ["plan/*.json"],
   "packages": ["args_street_*.json"], "needs": ["terrain"]},
  {"tool": "material_create", "args": "material_plaster.json"},
  {"packages": ["args_hand_made.json"]}]}
```

- `run`: a Python script started in its folder. It runs again only when the script, its `args`, a file matching `inputs`, a file it read last time (any file it opened or module it imported in that folder), or the tools' version changed, or a package file is missing. A non-zero exit stops the job with the end of its output. A generator must write the same file for the same inputs: seed its random numbers and keep a fixed order (the pipeline fixes Python's hash seed, so sets of strings iterate alike in every run; outside the pipeline they do not). A step may rewrite package files of earlier steps (a post-process): it reads them, so it runs again whenever they change. A step that reads a file a later step writes is run again in the same job once that file changed (up to 3 passes; `unsettled` names generators that keep changing each other's input).
- `tool`: an MCP tool call (not `map_edit`, `map_build`, `game_launch`, `game_stop`), made again when its arguments change.
- `packages`: files or patterns the step writes, in order; `needs` applies to all of them.
- Packages the manifest no longer lists are removed from the map. A missing map is created from `create`.

Result adds `generators_run` (with the reason for each) and `steps_up_to_date`.

## Offline preview

`game_screenshot(map, views, window="offline")` draws the saved map from its source: meshes in a colour per material with a simple sun and outlines, props as boxes, plants as see-through boxes. Views: `{"name","pos":[x,y,z],"ang":[pitch,yaw,0]}` as in the game (the same `pos`/`ang` give the same framing), `{"top": [[x0,y0],[x1,y1]]}` or `{"top": true}` from above (north up), `{"side": box, "yaw": 90}` from the side; `views="marks"` uses the tester's marks; no views = from above and two bird's-eye views. Returns `sheet` (one labelled picture) and `shots`.

Use it for shape and place: gaps, slabs standing over the ground, buried or floating pieces, overlaps, a road that misses its junction. Light, textures and the shapes of models are the game's: judge beauty there.

## Region builds

`map_build(name, preset, region=...)` cuts a zone out of the saved map into the map `<name>_region` and builds it. `region`: one box `[[x0,y0],[x1,y1]]`, up to 8 boxes, or `"marks"` (640 units around every mark). The region map keeps:

- every object that stands in a box or within 64 units of it;
- big meshes (ground, long roads) cut by faces, with 384 units around the box;
- sun, sky, fog and post-processing of the whole map; a spawn in the middle of the first box when none is inside.

Coordinates are those of the full map: views and marks apply as they are. Frames of a `fast` or `nav` region are fullbright (no baked light). `"marks"` takes every stored mark of the map: with marks all over it the region is most of the map and saves nothing (the answer says so in `wide`) — give the box of the zone you work on. The region map is cut again from the saved map every time and never feeds back into it; while CS2 holds `<name>_region`, the next build goes to `<name>_region2`. Not for `release`.

## Checks

`visual_check(name)` keeps its findings while the map file, its layer files, its own models, the model notes and the tools are unchanged (`summary.cached`). `check_map` shares them. A long answer shows a share of every kind and `summary.zones`: the busiest squares as boxes for `visual_check(name, box=...)`. A zone check audits the zone with 256 units around it and reports what stands in it; a long mesh (a road, a clip, a joint) is judged by its part in the zone, so its lengths are the zone's.

## By hand

Without packages: keep each part's operations in a file and replay one part with `{"op":"replace_batch","tag":"yard"}` as the first operation of a `map_edit` call (the nodes of the earlier call with this tag are removed, the new ones get it). Without regions: copy the zone's operations into a scratch map with `map_create` + `map_edit` and build that.
