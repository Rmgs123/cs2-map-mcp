# Animated actors

| Task | Take |
| --- | --- |
| An own animated character (idle/walk/turn) | `actor_build` |
| It walks a route among the props | `map_edit` `add_actor` |
| Other clips or a rig | a Blender script for `actor_build`; see By hand |

Own skeletal scenery props, controlled by `cs_script`. Units are inches, +Z up,
feet z=0, face +X. These are standalone scenery props.

## Build a model

`actor_build(name="walker", script=null, walk_speed=62)` runs headless Blender,
exports a skinned mesh and single-take idle/walk/turn FBXs, writes ModelDoc,
and compiles the materials/model. It returns `ok`, model path, bounds, bone count,
clips, authored walk speed, source directory, labelled pose preview and compiler
log. Open the preview; game verification remains necessary.

```json
{"name":"walker"}
```

Result: `models/mcp_n/walker/walker.vmdl`; sources/history stay there.
Materials use `materials/mcp_n`. Identifiers use ASCII letters/digits/underscores,
start with a letter/underscore, and have at most 56 characters.

For your own creature, `script` is an existing project-local Blender `.py`.
It creates exactly one armature, meshes with an Armature modifier referencing
that rig, and Actions **idle**, **walk**, **turn** (ASCII identifiers). Weight every
vertex; give the root bone the name `root`; set scene FPS; apply object transforms
and author in inches with the front +X. Use in-place clips, including a closing
pose for loops. Bone scaling animation is unsupported. Materials use their
diffuse color, or `material["source_vmat"]="materials/...vmat"` for an existing
stock/addon material.

To add/change a clip, edit the Blender Action (e.g. keyframe an arm/leg rotation),
rebuild, then rebuild the map. Additional named Actions become additional
ModelDoc sequences; the movement operation uses idle/walk/turn. `walk_speed`
is the distance per cycle divided by its duration (units/second, 1..400).
The default cycle has no foot IK.

## `map_edit`: `add_actor`

```json
{"op":"add_actor","name":"walker1","model":"models/mcp_n/walker/walker.vmdl",
 "route":[{"room":"yard","at":[0.2,0.2]},
          {"room":"gallery","at":[0.5,0.5]},
          {"room":"lane","at":[0.5,0.8]}],
 "speed":90,"loop":true,"idle_at_points":2,"ref":"walker1"}
```

`route`: 2..64 world `[x,y,z]` points or layout-relative room points (optional
`layout`). `speed`: 1..300, default 90, must yield playback rate .1..4 relative
to the authored walk speed. `loop`: boolean, default true. `idle_at_points`:
0..60 seconds, default 2. `stop_near_player`: 0..256 units, default 0 (disabled).
`ref` names the prop. Generated script targetname is `<name>_script`.

The planner uses core layout openings and triangle A*, then samples support
and swept torso clearance every <=12 units. Flat world-mesh obstacles are
routed around. Direct supported ramps/steps <=18 units are accepted. Unsupported
floors, sealed paths, lifts and ladders are refused. Grouped meshes (house rows
of `add_facade`, prefabs) are planned in world space (fixed 05.10 on `p8_mechanics`).
The model must fit a 32x32x72 hull, feet at z=0. Props are non-solid scenery.
Runtime ground rays keep feet on floors; live obstacles pause movement.
Moving props are excluded from offline A*.

Idle plays at requested stops; turn plays while facing the next leg; walk rate
is `speed/walk_speed`. `Move` preserves client interpolation; `Teleport` resets.
I/O: `RunScriptInput Start`, `Stop`, `Reset`, `State`. Start resumes; Reset
returns to the first point. Console `N_ACTOR` lines report clips, arrivals,
positions and blocked/no_floor states. A round restart resets the route.

`dry_run` and a later invalid operation produce no actor scripts. Reusing a
name with a different definition on another map is refused. Recreate/replay
the map to revise its actor. Build the map and inspect timed game screenshots.
`check_map` checks actor model/script references; `visual_check` checks initial
placement. Neither proves the complete moving route.
CS2 acceptance: `n8_walker` full loop and clips passed. `n8_levels` passed
6/6 after a CS2 restart, including stair ascent/return, Stop/State, and
`stop_near_player`; open the three `n8_steps-*.png` frames for the visual check.

## By hand

Add a raw `prop_dynamic` with `model`, `targetname`, `solid:0`, `use_animgraph:0`,
`IdleAnim:"idle"`, `IdleAnimationLoopMode:"ANIM_LOOP_MODE_LOOPING"`.
CS2 inputs are `SetAnimationLooping`,
`SetAnimationNoResetLooping`, `SetAnimationNotLooping`,
`SetIdleAnimationLooping`, **SetPlaybackRate**; check `entity_info` for your version.
Wire `connect` outputs to these inputs, e.g. a button to SetAnimationLooping
with `param:"walk"`. `OnAnimationLoopCycleDone` reports completed cycles.

For custom movement, `script_write` a self-contained ES module importing
`Instance` from `cs_script/point_script`, attach with `add_script`, find the prop
by name, and schedule `SetThink`/`SetNextThink`. Ground with `TraceLine`, test
clearance with `TraceBox`, then `actor.Move({position,angles:{pitch:0,yaw,roll:0}})`.
Change clips through `Instance.EntFireAtTarget({target:actor,
input:"SetAnimationNoResetLooping",value:"walk"})`; scale playback through
SetPlaybackRate. Use `OnScriptInput` to expose your own I/O. See
`data/actors/runtime.js`; query `script_api` for engine methods.
