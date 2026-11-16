# Recipes: whole pipelines that worked, and what is not done yet

| Task | Take |
| --- | --- |
| A street quarter in the Inferno look | the first pipeline below |
| A competitive, story or mechanics map | the pipeline of that kind below |
| Anything no pipeline covers | primitives (`geometry`), own models (`models`), scripts (`scripting`) |

Each recipe was built through MCP calls and checked in CS2. Take the one closest to the task, then change what the task needs: every step is an ordinary tool or operation, so any step can be replaced by primitives (`geometry`), own models, materials and scripts.

## Pipelines that worked

- **Inferno-like streets** (`p7_quartiere`): `add_layout`/`add_comp_plan` (with `levels`) → `add_paving` → `add_facade` on every visible wall side → `add_wear` → `dress_street` → `lighting_preset "inferno"` → `add_backdrop style:"italy"` → `visual_check`, `check_map`, `map_build full` → frames next to `game_screenshot(map="de_inferno")`; name three differences and fix them.
- **Competitive** (`c_defuse`, `f3_port`): `add_comp_plan` → cover, `dress_room`, light → outdoor (`add_water_body`, `add_backdrop`, sky) → `playtest(kind="timings", live=true)` (CT reaches sites 3–8 s earlier) and `playtest(kind="competitive")`.
- **Story** (`mcp_story`, `f2_port`): `add_layout` → interactive parts (`physics`) → atmosphere (`light`, `fx`, `sound`) → `dress_room`, signs → **one** `add_story` last → `script_test(steps="auto")` and `route_test(map)` (a real walk through every stage).
- **Mechanics** (`p8_mechanics`, `p9_playground`): every section: a tested example, `script_test` steps, "By hand" recipes.
- **Housing estate** (post-war blocks of flats; `demo_estate`): ground (`add_terrain`; a `flatten` across a garage row on a slope, so its open boxes have no step) → `add_street` (`sidewalk:false` on flat ground: a sidewalk's side shows as a band until edges bevel) → a `panel` fence round the play area (closed path, `clip`) → `add_block_house` for the near houses (finishes `panel`, `silicate`, `brick`, `plaster`; turn them with `yaw`) → `add_garage_row` in the backs (`terrain` on slopes, a few `open`) → `add_fence` with `to_wall` between houses and garages → `add_yard` in every yard (`paths`, drawn as trodden tracks with the terrain's `paint`; `playground` in the biggest) → `add_district` `"style":"blocks"`, `collision:false` beyond the play area → `lighting_preset` `day` or `overcast` → `visual_check` → `full` build → frames from the street, a yard, the roofs and above. The look (save it with `my_notes(kind="style")` and change it): walls `concrete_panel_001`, `brick_soviet_white_01a`, `plaster_soviet_01a`, `ch2_brick_01_base`; plinths and trims `hr_concrete_wall_004/005`; gates `hr_metal_corrugated_001_*` and the tinted `001b_tint`; asphalt `ch2_asphalt_001`; walks `pavement_soviet_tiles_01a`; doors `ch2_door_001`.
- **Anything else**: primitives (`geometry`), any entity + `connect`, any logic (`scripting`), own models, materials and sounds. A missing primitive: `my_notes` feedback with the workaround.

## A big map from photos (a whole town, checked over weeks)

The way a town of about 24 500 nodes was built from phone photos, an outline and elevation data. It works for any real place.

1. **Photos are the truth.** Sort them into folders by place, each with a short note (what it shows, where it was taken from, which way it looks) and one numbered index. Old photos that disagree with new ones lose. Ask the person about anything the photos leave open before you build it (levels, which areas are playable, which are background).
2. **Ground first.** Terrain from elevation, road centrelines and building outlines from map data, one planning frame per street (distance along, offset across). Fix the levels of plazas, lots and stairs from the photos before anything stands on them.
3. **Stages and packages.** Split the work into stages, each into packages of one area or one feature. Every package is one generator in `my/maps/<map>/` writing its operations to a JSON file; `pipeline.json` lists them in order (section `pipeline`). Never edit the map by hand: fix the generator.
4. **The loop per package:** generate → zone checks offline (`visual_check(box=)`: no z-fighting, nothing floating, paving joins) → `map_build(name, "fast", region=...)` → frames in fullbright from the place and direction of each photo of the package (`setpos`/`setang`) → compare side by side → fix → again. A full light build only when the package changed looks (colour, materials, lamps, roofs) and at the end of a stage.
5. **Never worse:** keep reference views of the best places and of every fix, and compare their sheet after each package (section `pitfalls`, "Big maps").
6. **Testers:** give them the marks key (F8 at each problem, then a note); read `game_screenshot(views="marks")`, fix by generator, rebuild the marked zones with `region="marks"`.
7. **Release:** `map_edit(..., dry_run=true)` proves the map equals a clean replay; bots only with nav marked down to the routes (section `nav`); `map_build(name, "release")`; check the radar and the upload size.

## Not done yet

What maps needed and the tools do not do yet (each has a workaround in `pitfalls`). Write a feedback note when you meet one of them.

- A frame-time probe in the game (repeatable numbers despite other programs) and distance LODs for stock models.
- One damage filter shared by all vehicles of a map that ignores every seated player (each vehicle's filter refuses only its own pilot).
- Mount points and hang axes of stock lamps, lamps and poles checked against walls, a prop standing in a doorway narrower than the player.
- A street on a slope that bevels its edge down to the ground and caps the terrain under itself as the last step (today: `flatten` wider than the road before `add_street`).
- `visual_check` finds overlapping coplanar faces of different meshes and materials only: two faces of one mesh, or of one material with crossed texture axes, also flicker (keep solids of one mesh apart, as `add_block_house` does).

## By hand

No pipeline fits: plan the rooms or the ground first (`add_layout`, `add_terrain`), build with base shapes and `edit_mesh`, place stock props found with the atlas, light it, and look at frames after every batch. Write the steps that worked as your own recipe with `my_notes(kind="recipe")`, so the next chat starts from them.
