# Publishing a map to the Steam Workshop

The project addon (`mcp_lab` by default) holds every map you ever made; the Workshop Manager uploads a whole addon. `map_build(name, "release")` puts **one map alone in its own addon**, named like the map, with only the files it uses.

| Task | Take |
| --- | --- |
| Make the Workshop addon | `map_build(name, "release")` → `job_status(job, wait_seconds=50)` until it is not running (10+ minutes: final light; `progress` shows the stage) |
| Without bots / without visibility | `map_build(name, "release", bots=false)`, `vis=false` (both are decided automatically when left out) |
| Check the release itself, not the project copy | `game_screenshot(map="<map>/<map>")`, `script_test(map="<map>/<map>", steps)` |
| A good preview picture | `game_screenshot(map, views=[{..., "preview": true}])` before the release |
| Tell the user how to upload | the `PUBLISH.md` path in the answer: read it and give the user its steps |
| Reflections (glass, metal, water) | needs Workshop Tools started with the release addon (CS2 writes cubemaps into that addon): ask the user, then `lighting_build(name="<map>/<map>", cubemaps_only=true)` |
| Update after changes | change the project map, run `map_build(name, "release")` again (the addon is rebuilt) |

## What the release does

1. Collects the map's files: every own material, texture, model (with its sources and clips), sound, particle, script, HUD layout, style sheet and image the map, its scripts and its models refer to, followed recursively; the radar, the loading picture and the icon.
2. Copies them into `content/csgo_addons/<map>/` (an addon made from Valve's template), writes a sound event file with only the used `mcp.*` events, compiles every copied resource there (a missing file fails here, not in the game), builds the map with the `final` preset and draws its radar.
3. Decides before the long build: **VIS** is left out on big open maps (wider than ~310 m: there it costs hours and gives nothing, frustum culling keeps the FPS); **bots** are left out when the nav estimate is over the limit, and when the estimate is high the nav is measured first (minutes, before the light bake) — over the limit the release goes on without bots. The answer says what was decided and why (`bots`, `vis`, `nav`, `time_limit_minutes`).
4. Removes the light bake's work files (`_vrad3`, hundreds of MB, made again by every bake) from the folder that gets uploaded and reports the upload size (`result.upload`, also in PUBLISH.md).
5. Writes `content/csgo_addons/<map>/PUBLISH.md`: where the files are and the Workshop Manager steps with a title, description, preview and tags to fill in.

The release addon is generated: the next release replaces its files. It refuses an existing addon it did not make. Stock CS2 assets are not copied (every player has them).

## Before the release

- `check_map` and `visual_check` clean (errors fixed, warnings understood); a `full` build tested with `script_test` / `route_test` / `playtest` as the map type needs.
- A preview frame: one beautiful `game_screenshot` view with `"preview": true` (16:9; it becomes the loading picture and the Workshop preview).
- Spawns for the players it is meant for, a `point_nav_walkable` if bots play it.
- Bots on a big map: limit the nav first (`set_play_area`, section `nav`) and measure it with `map_build(name, "nav")`; otherwise the release goes without bots (it says so).

## Upload (the user does it: it is their Steam account)

In running CS2 Workshop Tools: Asset Browser → **Tools → Tools → Counter-Strike 2 Workshop Manager** → **New**: title, description, preview image (16:9, resized to 555×312), **addon folder = the release addon** (never the project addon: it holds every test map), game mode tags (Classic, Deathmatch, Wingman, Arms Race, Custom), visibility (Hidden first) → **Submit**. Updates: select the map → **Re-Upload**. Give the user the `PUBLISH.md` steps; never claim it was uploaded.

## Limits

One map per release addon. A map that loads files by names built at run time (not written anywhere in the map or its scripts) may miss them: check the release in the game with real input before the user uploads it.

## By hand

An addon is two folders: `content/csgo_addons/<addon>` (sources: `maps/*.vmap`, materials, models, sounds, `soundevents/soundevents_addon.vsndevts`, `panorama/`, `maps/scripts/`) and `game/csgo_addons/<addon>` (what the compiler writes; this is uploaded). Copy what the map uses, compile with `resourcecompiler -i <file>` for each resource, build the map, open the addon in the Workshop Tools launcher and upload it with the Workshop Manager.
