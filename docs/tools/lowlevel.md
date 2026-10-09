# Base commands: everything by hand

The quick builders are accelerators, not the ceiling. Every idea can be built from the base commands below; this table says where each area lives and what to do where no command exists. Found a gap? Build it by hand, then save the recipe in `my/` (`my_notes(kind="recipe")`).

| Area | Base commands | By hand, where no command exists |
| --- | --- | --- |
| Geometry | shapes `box` … `polyhedron`, `mesh`, `extrude`, `revolve`; `cut_mesh`; `edit_mesh` (bevel, inset, extrude, bend, twist, taper, noise, mirror) | sculpted ground: `subdivide` + `move`/`noise`; a union of solids: separate meshes overlapping 1+ unit |
| Surfaces | `material_create`, `material_variant`, `material_from_reference`; `texture_faces` (material, UV `world`/`box`/`planar`/`cylinder`/`fit`, corner tint), `paint_blend`, mesh `tintColor`; `decal_create` + `add_decal`, `add_sign` | a picture once on a face: `uv:{"mode":"fit"}`; shader keys we do not expose: write the `.vmat` next to a made one |
| Entities and logic | `add_entity` (any FGD class), `update`, `move`, `delete`, `duplicate`, `connect` (`param`, `delay`, `times`), `entity_info` | logic chains below; `logic_case`, `logic_compare`, `math_remap` the same way |
| Physics | `add_physics_prop`, `add_breakable`, `add_door`, `add_mover`, `add_rotator`, `add_zone`, `add_ladder` | constraints (`phys_hinge`, `phys_ballsocket`, `phys_lengthconstraint`, `phys_motor`) and trains on paths: recipes below |
| Spawning at run time | `point_template` + `ForceSpawn` (I/O or a script) | recipe below |
| Scripts | `script_write` (`// @requires` shared parts), `add_script`, `script_api`, `script_test` | anything the I/O system cannot do: section `scripting` |
| Sound | `sound_create`, `sound_event`, `add_sound`, `add_soundscape`, `add_ambience` | — |
| Light | `lighting_preset`, `add_light` (point, spot, shaft, switchable, flicker), `light_rooms`, `lighting_build` | — |
| Effects | `add_fx` (stock particles, rain, snow, lightning), `env_fade`, `env_shake` | own particle systems are not made yet: stock particles, glowing materials and scripted props |
| HUD and UI | per mechanic HUDs, themes and own layouts (section `ui`) | — |
| Models and animation | `model_build` (script, template, file), `actor_build` + `add_actor`, `add_model`, `prop_preview` | — |
| Players | spawns, `add_ability`, `add_weapon`, `mode_setup`; `game_player_equip` | anything else a player can do: a script on `core` (section `scripting`) |
| Bots | `point_nav_walkable`, `playtest` | — |

## Logic chain: three switches open a gate, it closes after 5 s

```json
{"op":"add_door","targetname":"gate","mins":[700,600,0],"maxs":[828,616,128],"open_by":"script","stay_open":true}
{"op":"add_button","targetname":"sw1","mins":[600,560,48],"maxs":[616,576,64],"once":true,"targets":[{"name":"cnt","input":"Add","param":"1"}]}
{"op":"add_button","targetname":"sw2","mins":[640,560,48],"maxs":[656,576,64],"once":true,"targets":[{"name":"cnt","input":"Add","param":"1"}]}
{"op":"add_button","targetname":"sw3","mins":[680,560,48],"maxs":[696,576,64],"once":true,"targets":[{"name":"cnt","input":"Add","param":"1"}]}
{"op":"add_entity","ref":"cnt","classname":"math_counter","origin":[640,520,16],"keyvalues":{"targetname":"cnt","min":"0","max":"3"}}
{"op":"add_entity","ref":"relay","classname":"logic_relay","origin":[660,520,16],"keyvalues":{"targetname":"open_relay"}}
{"op":"connect","from":"@cnt","output":"OnHitMax","target":"open_relay","input":"Trigger"}
{"op":"connect","from":"@relay","output":"OnTrigger","target":"gate","input":"Open"}
{"op":"connect","from":"@relay","output":"OnTrigger","target":"gate","input":"Close","delay":5}
```

Verified in CS2 (`a10_raw`: two presses leave the gate shut, the third opens it). One-off timed steps are simplest as delayed outputs (`delay`); `logic_timer` is for repeating ones. Check a chain with `game_command("ent_fire sw1 Press")` and `physics_probe(names=["gate"])` (each probe also takes a frame, so it lags a few seconds: do not time a chain with it).

## A hanging sign that swings: `phys_hinge`

```json
{"op":"add_physics_prop","targetname":"sign1","model":"models/props/cs_italy/streetsign01.vmdl","origin":[300,600,170]}
{"op":"add_entity","classname":"phys_hinge","origin":[300,600,170],"keyvalues":{"targetname":"sign1_hinge","attach1":"sign1","hingeaxis":"300 664 170","hingefriction":"0.2"}}
```

The hinge's `origin` and `hingeaxis` are two points of the axis; `attach2` empty = the world. Without the hinge the prop falls; with it, it hangs and swings when shot (verified, `a10_raw`). The same pattern: `phys_ballsocket` (a lamp on a chain), `phys_lengthconstraint` (a rope length), `phys_motor` (a turning wheel).

## A platform on a path: `func_tracktrain` + `path_track`

```json
{"op":"add_brush_entity","classname":"func_tracktrain","shape":{"shape":"box","mins":[1000,300,8],"maxs":[1128,428,24]},"material":"materials/dev/reflectivity_50.vmat","keyvalues":{"targetname":"train","target":"tr1","speed":"80","startspeed":"120","spawnflags":"2","orientationtype":"0","height":"8"}}
{"op":"add_entity","classname":"path_track","origin":[1064,364,0],"keyvalues":{"targetname":"tr1","target":"tr2"}}
{"op":"add_entity","classname":"path_track","origin":[1364,364,0],"keyvalues":{"targetname":"tr2","target":"tr3"}}
{"op":"add_entity","classname":"path_track","origin":[1364,764,0],"keyvalues":{"targetname":"tr3","target":"tr4"}}
{"op":"add_entity","classname":"path_track","origin":[1064,764,0],"keyvalues":{"targetname":"tr4","target":"tr1"}}
```

The train moves to `tr1` and loops at `speed` (verified, `a10_raw`); `height` lifts it above the track points, `spawnflags` 2 = no player control, inputs `Stop`, `StartForward`, `SetSpeed`, `TeleportToPathNode`. A cable car, a moving target, a lift that is not straight.

## Spawn copies at run time: `point_template`

```json
{"op":"add_physics_prop","targetname":"spare_crate","model":"models/props_junk/plasticcrate01a.vmdl","origin":[450,900,40]}
{"op":"add_entity","classname":"point_template","origin":[450,900,40],"keyvalues":{"targetname":"maker","Template01":"spare_crate","spawnflags":"2"}}
{"op":"add_button","targetname":"spawn_btn","mins":[400,980,48],"maxs":[416,996,64],"targets":[{"name":"maker","input":"ForceSpawn"}]}
```

At map start CS2 takes the named entities into the template (they disappear); every `ForceSpawn` makes a new copy at their place (verified: two presses, two crates). Keep `spawnflags` 2: with 0 nothing was spawned. A script spawns at any point with `PointTemplate.ForceSpawn(origin, angles)` and gets the new entities back.
