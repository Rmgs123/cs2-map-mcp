# cs2map — guide for AI models

Everything a model needs to build a CS2 map with this MCP server (resource `cs2map://guide`, tool `read_guide()`, prompt `cs2map_guide`). Feature areas are sections (index at the end): read one with `read_guide(section="<name>")` before use.

## What to take for which task

| Task | Take | Section |
| --- | --- | --- |
| Start, check CS2/Blender/addon | `status` | — |
| Publish a map to the Steam Workshop | `map_build(name, "release")`, then `game_screenshot(map="<map>/<map>")` | `publish` |
| Remember lessons, own styles, recipes; the user's files | `my_notes`, `read_guide(section="my")`, `asset_search(kind="mine")` | `styles` |
| New map, edit, build | `map_create` → `map_edit` (batches) → `check_map` → `map_build` → `job_status` | this core |
| Look at the result | `game_screenshot` (views, `"auto"`, `"rooms"`) | this core |
| Rooms and floors of a level | `set_map_type` | `type` (table above) |
| Competitive map | `add_comp_plan`, `playtest(kind="competitive"/"timings")` | `competitive` |
| Bots; a big map's nav | `point_nav_walkable`, `set_play_area`, `add_nav_markup`, `map_build` `bots` | `nav` |
| Story / single player | `add_story`, `script_test(steps="auto")`, `route_test` | `story` |
| Streets in the Inferno look, fast | `add_paving`, `add_facade`, `add_wear`, `dress_street`, `add_backdrop` | `buildings`, `wear`, `streetlife`, `outdoor` |
| Any other look (own style) | primitives + own materials/models, saved as a style in `my/` | `styles` |
| A huge map's town, cheap | `add_district` | `buildings` |
| Hills, dirt roads, rocks, trees | `add_terrain` (`paint` tracks), `scatter`, `add_street`, `add_junction` | `terrain` |
| Fences, gates, handrails, steep banks, bare trees, poles with wires | `add_fence`, `add_railing`, `add_bank`, `add_tree`, `add_utility_line` | `terrain` |
| Any shape; bevel, inset, bend, twist, noise a mesh | shapes `mesh`/`extrude`/`revolve`, `cut_mesh`, `edit_mesh`, `texture_faces`, `paint_blend` | `geometry` |
| Stock props | `asset_search(kind="prop")` → `prop_preview` → `add_model` | `atlas` |
| Own props, characters | `model_build`, `actor_build` + `add_actor` | `models`, `actors` |
| Materials, decals, signs | `material_create`, `material_from_reference`, `decal_create` | `materials` |
| Doors, lifts, physics, zones | `add_door`, `add_mover`, `add_physics_prop`, `add_zone` | `physics` |
| Light, fog, cubemaps | `lighting_preset`, `add_light`, `lighting_build` | `light`, `quality` |
| Sound | `sound_create` → `sound_event` → `add_sound` | `sound` |
| Rain, fire, particles | `add_fx`, `assets_prepare(kind="weather")` | `fx` |
| Game logic | `script_write` + `add_script`, `script_api`, `script_test` | `scripting` |
| Drone, car, turret | `add_controllable` (`kind`: `drone`, `car`, `turret`) | `control`, `vehicles`, `turrets` |
| Guns, launchers, gadgets (own model in the hands) | `add_weapon` (`fire`, `projectile`), `model_build` `weapon` | `weapons` |
| Double jump, dash, flying, low gravity, invisibility, wallhack, jump pads | `add_ability` | `abilities` |
| Shooting range, race, collect | `add_minigame` | `minigames` |
| HUD looks, own HUDs, clickable menus | `hud` style keys, `set_ui_theme`, `add_hud` | `ui` |
| Game modes (DM, surf...) | `mode_setup`, `playtest(kind="mode"/"course")` | `modes` |
| Hide and seek, prop hunt; recipes of map kinds | `add_hideseek` | `types` |

## Your local layer `my/`

`my/` belongs to the user of this installation (never in git, never replaced by updates): how you get better from chat to chat.

- **Before work:** the index at the end of this guide lists your notes, recipes and styles; read the ones that touch the task (`read_guide(section="my")` prints every note).
- **After a finding** (a fix that worked, a rule, a pitfall, the user's taste, a batch worth replaying): `my_notes(action="write", kind="note"|"recipe", name, text, data)`. One topic per note, short lines, the *why* included. Update a note instead of adding a near-duplicate.
- **Own looks** (anything that is not Inferno): save materials, tints, models, sizes, light and rules as a style (`kind="style"`, section `styles`) and use it with `"style":"my:<name>"` or `"$<name>.materials.wall"` in any operation.
- **The user's files** (photos, models, sounds) are in `my/inbox/`: `asset_search(kind="mine")`.
- **Problems with this MCP** (a tool error you did not cause, a wrong guide, a missing feature you built by hand): one entry `my_notes(action="write", kind="feedback", name=<tool>, text)` → `my/feedback.md`; the user sends it to the author.
- Write to `my/` only through `my_notes`; never edit the shipped guides or code.

## How it works

Maps are `.vmap` files in the addon (`content/csgo_addons/<addon>/maps/`); the tools edit them directly, Hammer is not needed. `map_build` runs Valve's compiler and the game loads the compiled map.

1. `status` — CS2 and Workshop Tools found and responding? Blender, Node?
2. `map_create(name, type)` — sky, sun, fog, nothing else; `type` (below) picks the checks and the end tests.
3. Assets the map needs: stock (`asset_search`, props through `kind="prop"` + `prop_preview`), own (`model_build`, `material_create`, `sound_create`).
4. `map_edit(name, operations=[...])` — many operations per call; keep the batch in a file with `ref` names and replay it after `map_create(overwrite=true)`.
5. `check_map` — fix every `error`; `visual_check` — nothing floating, buried or flickering.
6. `map_build(name, "full")` → `job_status(job, wait_seconds=50)` until it is not `running`: `result.ok`, `errors`, `missing_assets`, `nav_areas` (> 0 for bots).
7. `game_screenshot(map, views=...)`: open the PNGs and judge them; mechanics with `script_test`; fix and rebuild.

**Beauty first, gameplay at the end.** Shape, look and light come first (fast builds, frames); timings, bot matches and long scenario runs come once, at the end, and only where the map type needs them.

| `type` | for | checks skip | end tests |
| --- | --- | --- | --- |
| `competitive` | defuse, hostage, wingman | — | `playtest` `timings`, `competitive` |
| `story` | single player stories | buy zones, objectives, spawn count | `script_test(steps="auto")`, `route_test` |
| `mode` | deathmatch, surf, bhop, kz, aim... (`mode_setup`) | rules the mode does not use | `playtest` `mode` / `course` |
| `sandbox` | playgrounds, showcases of mechanics | buy zones, objectives, spawn count | `script_test` per mechanic |
| `hideseek` | hide and seek, prop hunt (`add_hideseek`, section `types`) | buy zones, objectives | `script_test` of the round |
| `custom` (default) | a new kind of map | as sandbox | what your rules need |

Types never limit what you build; change one with the operation `set_map_type`. A request for a kind of map nobody has made is `custom`: invent its rules, test them and save them in `my/`.

## Tools

| Group | Tools |
| --- | --- |
| Start | `status`, `read_guide` |
| Maps | `map_create`, `map_list`, `map_info` (`about`: nodes, layout, geometry, terrain + `points`, street), `map_edit`, `map_backups` (`restore`), `map_build`, `job_status` (`cancel`), `check_map`, `visual_check` |
| References | `asset_search` (`kind`: material, model, prop, texture, sound, particle, decal, wear, smartprop), `entity_info`, `script_api` |
| Materials | `material_create`, `material_variant`, `material_from_reference`, `material_preview` (list = gallery, `texture=true` = PNG export), `decal_create` (`image` or `text` sign) |
| Models | `model_build` (`script`, `template` or `file` import, `lods`), `model_preview`, `model_info` (no name = list, `physics`), `prop_preview`, `prop_note`, `actor_build`, `assets_prepare` (drone, car, zapper, railgun, weather, light_shaft) |
| Styles | `kit_info` (part, list, `gallery`), `style_info` (building, theme, street_set, sky), `reference_facade`, `reference_compare`, `prefab_save`, `prefab_info` |
| Sound, scripts, light | `sound_create` (`file` or `preset`), `sound_event`, `script_write`, `script_test`, `lighting_build` (`cubemaps_only`), `lighting_measure` |
| Game | `game_launch` (`open_in_hammer`), `game_command`, `game_screenshot`, `game_reset`, `game_crash_report`, `game_stop` |
| Tests | `route_test` (`terrain` waypoints), `playtest` (`kind`: bots, competitive, mode, course, timings), `physics_probe`, `mode_setup` |

## Pipelines that worked

- **Inferno-like streets** (`p7_quartiere`): `add_layout`/`add_comp_plan` (with `levels`) → `add_paving` → `add_facade` on every visible wall side → `add_wear` → `dress_street` → `lighting_preset "inferno"` → `add_backdrop style:"italy"` → `visual_check`, `check_map`, `map_build full` → frames next to `game_screenshot(map="de_inferno")`; name three differences and fix them.
- **Competitive** (`c_defuse`, `f3_port`): `add_comp_plan` → cover, `dress_room`, light → outdoor (`add_water_body`, `add_backdrop`, sky) → `playtest(kind="timings", live=true)` (CT reaches sites 3–8 s earlier) and `playtest(kind="competitive")`.
- **Story** (`mcp_story`, `f2_port`): `add_layout` → interactive parts (`physics`) → atmosphere (`light`, `fx`, `sound`) → `dress_room`, signs → **one** `add_story` last → `script_test(steps="auto")` and `route_test(map)` (a real walk through every stage).
- **Mechanics** (`p8_mechanics`, `p9_playground`): operations with sane defaults; each section has a tested example, `script_test` steps and "By hand" recipes.
- **Anything else**: primitives (`geometry`), any entity + `connect`, any logic (`scripting`), own models, materials and sounds. A missing primitive: describe the workaround in `my_notes` feedback.

## Units, axes, facing

- 1 unit ≈ 1 inch; +Z up; `angles` = `[pitch, yaw, roll]` degrees; yaw 0 looks along +X, 90 along +Y; positive pitch tilts the nose down.
- Player: 32×32×72 (crouched 54), eye 64; step 18; jump ~55 (crouch-jump 64); slopes up to ~45°. Doorways ≥ 64×112 (main routes 96–128 wide); corridors 128–256; competitive maps 4000–8000 across; walls 8–16 thick.
- A model's **front** is the local axis it faces at angles `[0,0,0]` (`+x`, `-x`, `+y`, `-y`), never guessed from its name: take it from `kit_info` or a preview with the +X arrow. To face world direction D: `yaw = yaw(D) − yaw(front)` (`+x` 0, `+y` 90, `-x` 180, `-y` 270). `facing` and wall normals point out of the wall into the room. Own models are authored front +X, base at z 0.
- Points relative to rooms anywhere in `map_edit`: `{"room":"hall","at":[0.5,0.5],"z":8}`; on terrain: `{"terrain":"hills","at":[x,y],"z":"ground","offset":8}`.

## Clean scene

- Objects do not pass through each other unless intended; chairs stand beside a table, plants grow from soil or a pot. Respect `mount`: wall parts on walls at their height, floor parts on floors. Leave 40+ units between furniture groups on routes.
- Nothing floats: rocks and boulders sink 15–25 % of their height into the ground; on slopes the lowest ground under the whole footprint counts, not the ground under the origin. `scatter` does this; `visual_check` reports `visual_prop_floats_on_slope`.
- No z-fighting: faces of different objects never share a plane — join them edge to edge or set one back 1+ unit (glass, shutters, panels in a niche stay 1+ unit off its back). Labels and stains are decals or signs, never thin boxes.
- After dressing run `visual_check` and look at frames close up, from above, from the side and from outside the play area.

## map_edit operations

`map_edit(name, operations, dry_run=false, force=false)` applies up to 2000 operations in order and saves once (split bigger ones; `ref`s carry over); a failure saves nothing and names the operation. A mesh holds ≤ 8192 faces. Results carry the created ids. `"ref": "x"` names a result; later operations (in this or any later call) use `"@x"` wherever an id is expected. Unknown fields are refused.

| op | fields |
| --- | --- |
| `add_mesh` | `shape` (below), `material` or `materials`, optional `options` (`physicsType`, `disableShadows`, `smoothingAngle`, `tintColor`, `fademaxdist`, ...), `parent` |
| `add_entity` | `classname`, `origin`, `angles`, `keyvalues`; FGD defaults fill the rest |
| `add_brush_entity` | `classname` (triggers, buyzones, bombsites, `func_*`), `shape`, `keyvalues`, `material` |
| `update` / `move` / `delete` / `duplicate` | `id` + `keyvalues`/`origin`/`angles`; `ids`/`id` + `offset`; `ids`; `id`, `offset`, `count` |
| `connect` | `from`, `output`, `target`, `input`, `param`, `delay`, `times` |
| `set_material`, `set_uv` | `id`, `material`, `replace`; `id`, `faces`, `mode`, `scale`, `rotate`, `offset` |
| `group`, `ungroup` | `ids` (direct children of one parent), `name`: move/duplicate/delete the group as one; `id` to dissolve |
| `add_model` | stock or own prop: `model`, `origin`, `angles`, `scale`, `dynamic`, `skin`, `tint` (`models`) |
| `add_script` | a compiled `script_write` script, `origin` (`scripting`) |
| `lighting_preset`, `add_light`, `light_rooms`, `add_fx` | `light`, `quality`, `fx` |
| `add_sound`, `add_soundscape`, `add_ambience` | `sound` |
| `add_sign`, `add_world_text`, `add_decal` | `materials` |
| `add_door`, `add_button`, `add_mover`, `add_rotator`, `add_physics_prop`, `add_breakable`, `add_zone`, `add_ladder` | `physics` |
| `add_button_visual`, `add_control_button`, `add_cloth_awning` | a visible button body, a control panel button, a cloth awning (`visual`) |
| `add_mode`, `add_arena`, `add_surf_ramp`, `add_bhop_platforms`, `add_movement_course` | game modes and movement maps (`modes`) |
| `set_map_type` | `type` (table above) |
| `add_layout`, `add_lift` | `layout` |
| `add_comp_plan` | `competitive` |
| `add_story` | `story` |
| `add_paving`, `add_facade`, `add_building`, `add_district`, `add_kit_part` (quick fill, Inferno look) | `buildings`, `kits` |
| `add_wear`, `dress_street` (Inferno look), `dress_room`, `add_trim`, `add_pipes`, `add_cables` | `wear`, `streetlife`, `dressing` |
| `add_terrain`, `scatter`, `add_street`, `add_junction`, `add_fence`, `add_railing`, `add_bank`, `add_tree`, `add_utility_line` | `terrain` |
| `add_water_body`, `add_backdrop`, `add_supports` | `outdoor` |
| `cut_mesh`, `texture_faces`, `paint_blend`, `edit_mesh` | `geometry` |
| `add_prefab` | `structure` |
| `set_play_area`, `add_nav_markup` | `nav` |
| `add_controllable`, `add_actor`, `add_weapon`, `add_minigame` | `control`/`vehicles`/`turrets`, `actors`, `weapons`, `minigames` |
| `add_ability` | `abilities` |
| `add_hideseek` | `types` |
| `set_ui_theme`, `add_hud` | `ui` |

## Shapes (world coordinates)

| shape | fields |
| --- | --- |
| `box` | `mins`, `maxs`; `materials` {top, bottom, sides} |
| `wedge` | `mins`, `maxs`, `rise` (`+x`/`-x`/`+y`/`-y`): a ramp, highest at `rise` |
| `stairs` | `mins`, `maxs`, `steps`, `rise` |
| `cylinder` | `center` [x,y], `radius`, `z0`, `z1`, `sides` (3–128) |
| `prism` | `polygon` (convex), `z0`, `z1`, `axis` (`z`: polygon in x,y; `x`: in y,z; `y`: in z,x) |
| `room` | `mins`/`maxs` = inside space, `thickness`, `floor`/`ceiling`, `walls`, `openings` [{`wall`, `center`, `width`, `height`, `bottom`}], `materials` {floor, ceiling, walls} |
| `arch` | `mins`, `maxs` (the wall), `axis`, `opening_width`, `opening_height`, `segments` |
| `terrain` | small heightfield: `mins`, `maxs`, `cells` (≤96), `heights` or `noise` (added to `maxs.z`, or `absolute:true`); big landscapes: op `add_terrain` |
| `polyhedron` | `vertices`, `faces` (counter-clockwise from outside) |
| `mesh`, `extrude`, `revolve` | any polygons, sweeps, lathe forms (`geometry`) |

Shapes are checked before anything is written; solids are watertight; textures are world-aligned at 0.25 units per texel.

## Materials

- Find: `asset_search(query, kind="material")` (`"concrete floor"`, `"plaster wall"`); use paths exactly as listed.
- Blockout: `materials/dev/dev_measuregeneric01.vmat`, `materials/dev/reflectivity_30.vmat`…`_90`. Tools: `materials/tools/toolsnodraw.vmat` (invisible solid), `toolsclip`, `toolsplayerclip`, `toolsblockbullets`, `toolstrigger`, `toolsskybox`, `toolslightmapres` (one box over the play area, or baked light is coarse).
- Own: `material_create(name, pattern={...})` or `image=`; kinds glass, emissive, water, decal (section `materials`).

## Entity recipes (all keys: `entity_info(classname)`)

- Spawns `info_player_terrorist` / `info_player_counterterrorist`: **8 units above the floor** (on the floor plane CS2 rejects them and nobody joins — `check_map` `spawn_in_floor`), `angles` [0,yaw,0], 48+ apart, competitive 10+ per team.
- Buyzones `func_buyzone` brush (`TeamNum` 2 = T, 3 = CT) over spawns, 128+ tall; bombsites `func_bomb_target` (`bomb_site_designation` 0 = A, 1 = B); hostages `hostage_entity` + `func_hostage_rescue`.
- **Bots need `point_nav_walkable`** on the floor in every area (nav grows from these seeds; `map_build` reports `nav_areas`). Big maps: section `nav`.
- Sun and sky: `light_environment` + `env_sky` (the `basic` template has both). Lights exist only on the client: I/O and scripts cannot switch them — use `add_light` with `start_off` / `style`.
- `env_combined_light_probe_volume` over the play area for reflections; `prop_static` / `prop_dynamic` / `prop_physics_multiplayer` props; `point_script` (`cs_script` = `scripts/<name>.vjs`).

## Building

- `fast`: geometry, collision, entities; no baked light; seconds — iterate on layout.
- `full`: + baked light (512 px, retried at 1024/2048) + bots' nav + the radar (an own `resource/overviews/<map>.txt` is kept); 0.5–3 minutes.
- `final`: + visibility and 2048 px quality-2 light; 10+ minutes.
- `release`: the map alone in its own addon for the Steam Workshop (section `publish`).
- Bots cost: bot data grows with the square of the nav areas (66 000: hours). `map_build` answers with a `nav` estimate; over the limit it skips bots or stops after NAVGEN. Big maps: bots last.

Light builds are queued (one VRAD3 folder per addon); a map loaded in CS2 is unloaded first. `job_status`: `state` running/succeeded/failed/timeout/cancelled/lost; `result.ok` only with exit 0, a new `.vpk` and no failures; `lost` = rebuild.

## Testing in game

- One CS2 Workshop Tools session (addon from settings) serves everything through its console port; **one console client at a time** (the error names a holder). CS2 is never killed: a window "Not responding" for 30 s → ask the user to restart Workshop Tools; a crash → `game_crash_report`.
- `game_screenshot(map, views=[{"name","pos":[x,y,z],"ang":[pitch,yaw,0]}])`: loads the map if needed, moves a noclip camera (pos = feet, eye +64), checks it and returns PNG paths. Per view: `cmd` (console in place, e.g. `"ent_fire door1 Open"`), `wait`, `grep` (console lines, e.g. `"vsnd"` with `snd_showstart 1`), `keep:true` (no noclip: the view as the player or a script camera sees it). `"preview":true` on a view also makes that frame the map's loading-screen picture (and an icon). `views="auto"`: overviews, spawns, objectives, rooms (`offset`/`limit` page them); `views="rooms"`: one per layout room. ~3 s per view.
- `script_test(map, steps)`: real input and checks — steps in section `scripting` (`use`, `hold`, `aim`, `mouse`, `expect`, `shot`, ...). Call `game_reset(map)` before and after: it leaves vehicles, turrets and drones, turns noclip off, restores HUD, accuracy and recoil, kicks bots, releases keys and restarts the round.
- Rounds start live (`mp_freezetime 0`, no intro). After a map load the mode's bots may join and the game restarts the round once; the tools wait for the respawn.
- `game_command("...", wait)` returns what the commands and the next `wait` seconds printed (script `Instance.Msg`, events). Aimed input (`setang` + `+attack`) needs `focus=true`. Avoid `maps *`.
- `game_launch(map)` reuses the running Workshop Tools session or starts one (30–90 s), never the user's own game; `open_in_hammer=<map>` shows a map in Hammer (map_edit refuses to write it until it is closed there).

## Safety

- Backups are automatic (tiers, with a snapshot of the sources): `map_backups(name)` lists, `restore=<id>` rolls back (`sources=true`: also `restored/<id>/`), `pin="label"` keeps a milestone. Keep what builds a map (generators, batches) in `my/maps/<map>/`, never in temp folders.
- Unsaved Hammer edits are never overwritten, not even with `force=true`.
- Only the configured addon is written; other addons and game files are never touched.
