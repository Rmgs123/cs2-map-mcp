# Changelog

## Unreleased

- **Big maps are fast**: a 4000-mesh town map with a 257×291 terrain rebuilds from its batches in about 1.5 min instead of 4 and `visual_check` takes 30 s instead of 18 min, with the same results: grid lookups instead of whole-map scans in the phantom-floor check, cheaper z-fighting pairs, hole bridges in triangulation tried nearest first, faster self-intersection checks and DMX encoding.
- `mesh` shapes are cached by content; `map_edit` reuses the map it has just written while the file is unchanged; map backups are trimmed after every save (`backups_map_mb`, newest three always kept).
- **Buses and other hollow vehicles**: cars take `door`, `exits`, `size`, `pilot` (stand-in size/place), `camera.pilot` (driver's eyes) and `passengers` (doors with groups of standing spots, E prompts, riders who keep their weapons and stand upright while the body drives); `reach` up to 160; parts parented to a vehicle (windows) count for E; hull checks ignore carried or dropped weapons (a C4 blocked entering).
- Riders can stand up after crouching (no teleport at a still spot; lift on stand-up while moving); prompts are cleared per player on round restart, death and a removed body; `hint.blocked`; players on the roof of a moving bus are put down.
- Vehicles with passengers park (physics motion off) without a driver once they stand still on their wheels; riders are never held inside anything solid (another spot or out of a door), extra players are put out instead of standing in a moving body, getting off at speed uses a door with room or is refused, and only weapons hurt the driver and riders; `hurt`/`killed` log lines.
- Riders are standing players held on their spots (accurate fire, crouch); hull traces never ignore entities parented to a vehicle (a driver could be put inside the body and crushed); a failed vehicle tick no longer stops its script; `hud.hint`/`hud.labels` translate the car panel; route test command `v9_control_<name>_route`; `script_test` releases held keys at the start.
- `visual_check(limit)` and `summary.by_code`; `visual_phantom_floor` no longer flags narrow turned faces (sills, frames, curtains, cables).

## 1.0.0 — 2026-10-09 (phase 10)

First release: any CS2 map and mechanic through MCP, with docs in English.

- **Steam Workshop releases**: `map_build(name, "release")` copies one map and only the files it uses into its own addon, compiles everything there, builds it with final light and a radar, and writes `PUBLISH.md` with the Workshop Manager steps; game tools check it with `map="<addon>/<map>"`.
- **Feedback for the author**: `my_notes(kind="feedback")` keeps short entries about tool errors, wrong guides and missing features in `my/feedback.md`; `status` shows the count.
- **Release**: MIT license; `status` reports the version; `tools/selfcheck.py` checks everything after a CS2 update (a small map with every mechanic, a full build, one real-input scenario each, a report with a contact sheet); acceptance map `p10_showcase` from one batch.
- **Docs**: all specs, ARCHITECTURE, AGENTS.md and this changelog in English; REPAIR.md, CONTRIBUTING.md, issue templates; every guide section has a task table and a "By hand" part (size limits tested).
- **Safety**: `model_build` takes `.blend` files without running their scripts; `script_test` never clicks when another window is in front; a download that fails its SHA-256 check is removed; no personal paths in shipped configs.
- **Found by the acceptance map, fixed for every map**: hide-and-seek seekers stayed blind after the hide phase (a one-player "stay out" fade cannot be lifted in CS2; now a held fade renewed during the hide phase); `check_map` warns when two mechanics share a key (`input_key_conflict`); window hoods no longer hit the eave with a low `floor_height`; parked invisible helpers, coplanar faces buried in a third body, bevels and moving cars no longer raise false warnings; every lighting preset adds light-probe volumes over the existing geometry.

### Abilities, UI, hide and seek, the lobby

- **Player abilities `add_ability`**: double jump, super jump, dash, blink, speed, gravity, jetpack, flight, auto bunny hop, invisibility, wallhack (glowing silhouettes through walls), regeneration, shield, armor, health, no fall damage, jump pads; sources always, zone, item, chat command, I/O input; durations and a HUD.
- **HUD themes and own UI**: six themes, position, size, animation and colours for every mechanic and story HUD, a map default (`set_ui_theme`), own layouts with images and clickable buttons (`add_hud`), a nicer default story HUD; `script_test` step `click`.
- **Hide and seek `add_hideseek`**: phases, frozen and blind seekers, reveal pulses, prop disguises (prop hunt), secret walls, teleports; guide section `types` with recipes of map kinds.
- **`mcp_idle`**: a cozy rooftop terrace at sunset over a town, shown when CS2 starts without a map; built from a shipped batch.
- **Radar, loading-screen picture and icon**: radars drawn from the floors on full builds; `game_screenshot` view `preview:true`.
- **Smaller things**: `respawn:"never"` keeps wrecks; `add_weapon shop:true` (bought base weapons become the custom one); `add_light size`; the user's inbox recipe; gravity zones work (scripted).
- **Fixed**: the dry run that `map_edit` wrappers make before mechanics write files never ran through MCP; helpers parked outside the world were never drawn.

### Depth for models

- **Local layer `my/`** (never in git): notes, recipes, own styles and the user's inbox; tool `my_notes`, `read_guide(section="my")`, the guide index lists what earlier chats saved, `asset_search(kind="mine")`, `style_info(kind="mine")`; `setup.ps1` creates it.
- **Own styles in batches:** `"style":"my:<name>"` in `add_facade`, `add_building`, `add_paving`, `add_district`, `"preset":"my:<name>"` in `lighting_preset`, `"$<style>.materials.<role>"` in any field.
- **`edit_mesh`:** move, extrude, inset, bevel (chamfer and round), subdivide, bend, twist, taper, noise, smooth, mirror, weld on an existing mesh; atomic, stays closed, world UV or `uv:"keep"`.
- **Smoothed normals like Hammer** (`smoothingAngle`, 40°) on every generated mesh: round shapes no longer look faceted.
- **Map types** (`map_create(type)`, `set_map_type`): competitive, story, mode, sandbox, hideseek, custom pick the checks and end tests.
- **`add_district`:** a cheap town for huge maps (blocks, streets, houses with windows and roofs, fade distance).
- **Base commands by area** (guide `lowlevel`) with in-game recipes: logic chains, `phys_hinge`, `func_tracktrain`, `point_template`; `texture_faces` UV mode `fit`.


## 2026-10-07 — phase 9: vehicles, turrets, mini-games, one weapon type

- **One weapon operation `add_weapon`** (replaces `add_custom_weapon` and `add_scripted_weapon`): any stock base (gun, taser, knife) keeps its slot, pickup, drop, ammo and hand animations; your own model shows in the hands, in third person and on the ground — `model_build(weapon={base, scale})` builds it on the base's skeleton read from the installed game. `give` on spawn; ready assets `railgun` (AWP), `launcher` (Nova), `zapper` (taser).
- **Shot engine**: projectiles (rockets, grenades, homing by view or on enemies), explosions (radius, falloff, push, self damage, cover by walls), `auto`/`burst`, `muzzle`, beams that fade; non-player shooters (towers).
- **Own mechanics from shared parts**: `// @requires core, hud, seat, camera, health, fire` in `script_write`, `// @fire <prefix> {...}` for shot-engine entities.
- **Car**: an off-road SUV on ray-cast suspension (kerbs, logs, rocks, slopes), speed-sensitive steering, configurable wheels, suspension, seat and cameras; a cabin view. **Turret** with yaw/pitch limits and overheating. **Mini-games**: shooting gallery, drone race through rings, collect games.
- **Shared mechanics core**: scripts bundled from fragments (`core`, `hud`, `camera`, `seat`, `health`, `fire`), `control.register_kind`; a Node engine double for offline tests. Exit from any seat finds the nearest free spot.
- **Real input in tests**: `script_test` steps `use`, `aim`, `hold`, `mouse`, `focus`; `game_reset`; `game_crash_report` reads CS2 minidumps; clear errors when CS2 hangs (nothing is killed).
- **Order for models**: 88 → 48 tools, descriptions and schemas half as long (12.6k → 6.6k tokens), a task table in every guide section, all guide JSON examples dry-run tested.
- **Disk and setup**: automatic trimming by limits (frames, logs, backups); one-command `setup.ps1` (plan, then `-Apply`: environment and registration in Claude Code, Claude Desktop, Codex with config backups); Blender from Steam.
- **Fixed**: a weapon model missing from the map's manifest crashed the client; unchanged mechanics scripts are not recompiled (a running CS2 holds the file); `map_edit` with actors, room lights and routes about twice as fast.

## 2026-10-05 — phase 8: base primitives and mechanics

- **Base primitives**: `mesh`/`extrude`/`revolve` shapes, `cut_mesh`, `texture_faces`, `paint_blend` (two-material vertex blend), `geometry_info`.
- **Drone** (`add_controllable`): real physics, 50 HP from bullets, grenades and knives, an exact 20-hull hitbox, explosion, a falling burning wreck, respawn, FPV and chase cameras; the pilot's body is hidden behind a still stand-in.
- **Animated characters** (`actor_build`, `add_actor`): a skeletal character walking routes. **Scripted weapons**: the railgun (charge, wall penetration across the map, beam, magazine, recoil).
- `env_beam` crashed the CS2 server (found from a minidump): beams are pools of glowing rods. VConsole2 windows that steal the console close themselves (`WM_CLOSE`). `keep:true` frames for script cameras.
- Phase 7 finishing: models in niches stand 1 unit off the surface (no flicker), check `visual_flat_model_on_surface`; roof chimneys and antennas; a solid ring of houses on the Italy skyline; clean facade row corners.

## 2026-10-04 — phase 7: maps that look like Valve's (`p7_quartiere`)

- **Floors and facade depth**: `add_paving` (real floors by area, sidewalks with kerbs, gutters, stairs between levels), `add_facade` (real houses where space allows: niches, arcades, loggias, shutters, Inferno tile roofs; thin facades on shared walls), checks `visual_phantom_floor`/`visual_phantom_wall`.
- **Like Valve's Inferno**: tinted `inferno_plaster_facade_01`, the `terrace_set_01` café and `planter_kit_01` pots, Inferno wear decals (`add_wear`), street life from the atlas (`dress_street`), the `inferno` light preset, an Italy skyline (`add_backdrop`).
- **Prop atlas**: `prop_search` over ~12 000 stock models, offline `prop_preview` (~1 s per model, player box and +X arrow), `prop_note`.
- Core: DMX deletion leaves no orphans; light baking retries the lightmap atlas on "Too many charts"; fast start (`mp_freezetime 0`, `mp_team_intro_time 0`): 6 s from load to a live player instead of 26; `@ref` names stored in the map; the guide split into a core and sections; `check_map` knows game modes.
- Private GitHub repository with a whitelist `.gitignore`; `config/settings.json` optional.

## 2026-10-03 — rounds 3–5: stories, sound, physics, outdoor, competitive

- **Stories** (`add_story`, HUD, `script_test`), **sound** (stock index, own sounds, events, ambience), **physics** (doors, buttons, lifts, ladders, breakables), **light** (presets, fog, shafts, switchable and flickering lamps), **glass, neon, water, signs**.
- **Outdoor and competitive**: water to the horizon, backdrops, supports, sky; `add_comp_plan`, physical T/CT timings, 5v5 bot rounds; `visual_check`; `route_test` with a planner (openings, lifts, ladders, A*).
- The "silent console" cause: CS2 accepts one console client; the server now frees the port after 5 s idle and names the holder. Frames detect stale images with a control frame. Visible ladders (stock modules over an invisible surface).

## 2026-10-01 — rounds 1–2: layouts, models, materials, modes

- Layouts, models from Blender, materials from references, deathmatch/aim/surf/bhop/kz modes, terrain and streets; the acceptance map `mcp_accept` built only through MCP.
- Persistent console connection (about 3 s per frame instead of 20), automatic views, short answers, JS syntax checks through Node.js; job results survive the server process (`jobexit.py`).

## 2026-09-30 — the new core (1.0.0.dev0)

- Pure-Python DMX codec, half-edge mesh generator and shapes, FGD catalogue, VPK search, materials, the compiler with background jobs, the FastMCP server, game control through VConsole; the `mcp_lab` addon; FBX scale for `.vmdl` (`import_scale 0.0254`); bot navigation; light baking queue.
- An earlier approach (protective infrastructure without running the real programs) was archived; the rule since then: verify in the real programs.
