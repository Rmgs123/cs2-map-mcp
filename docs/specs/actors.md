# Actors: animated characters as props

An actor is an own skinned model with idle, walk and turn clips, placed as a `prop_dynamic` and moved along a route by a generated `cs_script`. Tools: `actor_build` (model) and the `map_edit` operation `add_actor` (route). Code: `actors.py`, `ext/actors.py`, `data/actors/`.

## How it works

**Model.** `actor_build` runs `data/actors/export.py` in the shared headless Blender (`blender_run`, 180 s). Author space is inches, +Z up, front +X, feet at z 0. The default walker has 7 bones with rigid part weights, is 64 units tall and has `idle` (60 frames), `walk` and `turn` (24 frames each) at 30 fps; the repeated last frame closes each loop. In every pose the lowest point of the skinned mesh is measured through Blender's depsgraph and the root is lifted, so boots never sink into a flat floor. An own script must give one armature, every mesh skinned with weights on every vertex, and the actions `idle`, `walk`, `turn`; other actions are exported too.

The ModelDoc has `RenderMeshFile`, a `DefaultMaterialGroup` and one `AnimFile` per clip (`looping`, `is_default_idle_anim` on idle). The skeleton comes from the skinned render FBX; the animgraph stays empty and root motion is not extracted: clips are in place and the script moves the prop.

**Route.** `add_actor` takes the model and 2..64 world or room points, `speed` 1..300, `loop`, `idle_at_points` 0..60 s, `stop_near_player` 0..256. Each leg is planned with the layout waypoints and an A* over floor triangles; every sample (≤ 12 units apart) needs floor support under the centre and four corners, steps ≤ 18 and torso clearance. A straight leg without A* is still checked against real triangles: the actor never walks through walls.

**Runtime.** `data/actors/runtime.js` is embedded whole in `maps/scripts/mcp/n8_<name>.js` (CS2 has no relative imports). Each tick (dt capped at 0.1 s) the actor turns toward the next point at 180°/s (standing, `turn` clip), then moves with `Move` (client interpolation stays smooth; `Teleport` only on reset); the walk clip plays at `speed / walk_speed`. A `TraceLine` to the floor and a `TraceBox` ahead stop it at a missing floor or a new obstacle. Inputs `Start`, `Stop`, `Reset`, `State`; `OnActivate` and `OnRoundStart` reset it.

## Why it is built this way

- Props instead of player models: CS2 does not let an addon add player characters or retarget clips to the player skeleton; a prop with its own clips is fully under our control.
- In-place clips and script motion: the route, the pauses and stopping near the player stay in one place (the script), and a route change needs no new animation.
- A `map_edit` wrapper runs the whole batch as a dry run first, so a late failure never leaves a new script behind.

## Pitfalls we hit

- **Animation scale.** `RenderMeshFile` honours `import_scale = 0.0254`, `AnimFile` ignores it and reads FBX translations at scale 1: without compensation the body was at z 1259.8 instead of 32. Render FBX are exported at scale 1, animation FBX with `global_scale = 0.0254`, both with the same −90° Z / 1/2.54 pre-matrix as environment models. `resourceinfo` confirmed bounds `[-6,-13,0]..[9,13,64]`; the compile alone reported success either way.
- Blender actions need `use_fake_user`, or the `.blend` loses every clip that is not active.
- Diffuse colours are converted from linear to sRGB; the FBX keeps short stable material tokens that the ModelDoc remaps.
- `GetFbxMaterialPath Failed` lines are normal with explicit remaps; they stay in the log and are not counted as errors.

## Limits

The static A* does not plan around props or other actors: the runtime stops in front of a solid prop and a new waypoint is needed. No lifts, ladders, IK, bone-scale animation or dynamic replanning.

## Files and tests

| What | Where |
| --- | --- |
| Model sources | `models/mcp_n/<name>/sources/<id>/` (`actor.blend`, `mesh.fbx`, clip FBX, `source.json`); the previous ModelDoc in `history/` |
| Metadata | `actor.json` (bounds, bones, clips, walk speed, preview) |
| Preview | four poses on one labelled sheet with +X/+Y axes |
| Tests | `tests/test_actors.py`; in game `n8_walker` (full loop, clips, `script_test` 9/9) and `n8_levels` (up and down a level, Stop/State, stop near the player) |
