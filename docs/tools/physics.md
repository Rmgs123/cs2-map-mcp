# Physics and interactive map recipes

| Task | Take |
| --- | --- |
| Doors (slide, swing, locked) | `add_door` |
| Buttons, lifts, fans | `add_button`, `add_mover`, `add_rotator` |
| Physics props, breakables | `add_physics_prop`, `add_breakable` |
| Push, teleport, hurt, gravity zones; ladders | `add_zone`, `add_ladder` |
| Watch them in game | `physics_probe` |

These `map_edit` operations use CS2 units (inches), world-space `mins/maxs`,
and `[pitch,yaw,roll]` angles in degrees. Each dynamic recipe returns `id`,
`targetname` and `classname`; pass `targetname` to `connect` or a script.
`targetname` is optional: a unique name is generated. `ref` supports later
`@ref` operations in the same transaction. Map backups and Hammer protection
are inherited from `map_edit`.

## Doors

```json
{"op":"add_door","targetname":"p_slide","mins":[-380,-8,0],"maxs":[-252,8,112],"open_by":"button","stay_open":true,"button":{"mins":[-416,-24,44],"maxs":[-400,-12,60]}}
```

Verified in `p_interactive`: the generated `p_slide_button` opens the door.
The response includes `button` and `button_id` (or `trigger`/`trigger_id`).

| Field | Default / meaning |
|---|---|
| `kind` | `slide` or `rotate`; default `slide` |
| `direction` | Slide movement: `up` (default), `down`, `+x/-x/+y/-y/+z/-z`, yaw number, or `[pitch,yaw,roll]` |
| `hinge` | Rotate pivot: `-x/+x/-y/+y` edge or world `[x,y,z]`; defaults to the low edge along the wider horizontal dimension |
| `axis` | Rotation axis `x/y/z`, default `z` |
| `rotation` | Signed opening angle, default 90; nonzero, -360..360 |
| `speed` | Slide units/s or rotate degrees/s; default 100 |
| `open_by` | `use` (E, default), `touch`, `button`, `trigger`, `script` |
| `button` | Existing button targetname or `{mins,maxs}`; omitted creates a button near the door |
| `trigger` | Optional `{mins,maxs}` for `open_by="trigger"`; omitted expands 48 units around the door |
| `locked` | Boolean, default false; E/touch requires an `Unlock` input first |
| `stay_open` | Boolean, default false; true sets `wait=-1` |
| `wait` | Seconds before auto-close, default 4; used when `stay_open=false` |
| `lip` | Slide overlap left in the doorway, units; default 0 |
| `texture` | Brush VMAT path; default dev reflectivity 50 |
| `sounds` | Optional `{move,stop,locked,unlocked}` sound event names; custom sound combinations are not yet game-verified |

A slide travels its projected brush extent minus `lip`; use `add_mover`
when an exact displacement is required. A rotating brush's mesh remains
in world coordinates while the entity origin sits on the hinge.

```json
{"op":"add_door","targetname":"p_rotate","kind":"rotate","mins":[116,-64,0],"maxs":[124,48,112],"hinge":"-y","open_by":"use","stay_open":true}
```

## Buttons, lifts and fans

`add_button`: `mins/maxs`, `targets=[{name,input,param?,delay?}]`, optional
`once=false`, `wait=1`, `texture`. At least one target is required; delayed
I/O uses seconds. A once button does not reset and outputs fire once.

```json
{"op":"add_button","targetname":"p_lift_down","mins":[348,220,236],"maxs":[360,236,252],"targets":[{"name":"p_lift","input":"Close"}]}
```

`add_mover`: `mins/maxs`, `to=[dx,dy,dz]` (displacement, not destination),
`speed=100` units/s, `call="button"|"trigger"|"loop"|"script"`. Button and
trigger fields work as for doors. `loop` alternates Open/Close, pausing
`wait=2` seconds at each end; `script` only creates the named mover.
Inputs are `Open`, `Close`, `SetPosition` (0..1) and `SetSpeed`.

```json
{"op":"add_mover","targetname":"p_lift","mins":[200,220,0],"maxs":[328,348,12],"to":[0,0,188],"speed":80,"call":"button","button":{"mins":[180,220,44],"maxs":[192,236,60]}}
```

`add_rotator`: `mins/maxs`, `axis="z"`, signed `speed=90` degrees/s, optional
world `origin` as pivot and `texture`. Starts immediately. Inputs include
`Start`, `Stop`, `Toggle`, `Reverse`, `SetSpeed`. Both `add_button` and
`add_rotator` also accept `model` plus `origin` instead of bounds; those
model-backed variants must be treated as experimental until their live
acceptance is recorded in the report. The brush recipes are game-verified.

## Physical and breakable objects

`add_physics_prop`: `model` (short own name or exact stock `.vmdl` path),
`origin=[0,0,0]`, `angles=[0,0,0]`, optional `mass` kilograms and positive
`health`. The compiled model must contain a `PHYS` block or a resolvable
external `.vphys`; source collision alone is insufficient. Missing bodies
are rejected. A `health` override uses `prop_physics_override`; otherwise
the model's `prop_data` determines health. `mass` emits a map-start `SetMass`
connection instead of guessing a `massScale` factor.

```json
{"op":"add_physics_prop","targetname":"p_own","model":"p_crate","origin":[-290,248,80],"mass":20,"health":100}
```

Make an own physical model first:

```json
{"name":"p_crate","template":"crate","params":{"width":40,"depth":40,"height":40},"surface":"wood","physics":{"mass":20,"breakable":"wood"}}
```

`model_build` (script, template or file) accepts `physics={mass:20,breakable:null}`;
`breakable` may be `wood`, `glass`, `metal` or null. Dynamic models require
`collision="hull"`. Generated break pieces are up to eight closed clips of
the render geometry, with their own convex hulls, lasting 15 seconds.
This is a generic fragmentation recipe, not hand-authored realistic damage.

`add_breakable`: `mins/maxs`, `material="glass"|"wood"|"metal"`, `health=20`,
optional `texture` to override the matching stock VMAT. It creates
`func_breakable` with bullet damage enabled. `Break`, `RemoveHealth`,
`SetHealth` are available. Visible shards are not guaranteed by this legacy
brush class; use own ModelDoc break pieces when persistent debris matters.

## Push, teleport, damage, gravity and ladders

`add_zone`: `kind`, `mins/maxs`, optional `params`. Affects players.

| Kind | `params` |
|---|---|
| `push` | `direction="up"`, `speed=600` units/s |
| `teleport` | Required `destination=[x,y,z]` feet position; optional `angles=[0,0,0]`; creates and returns a named `info_teleport_destination` |
| `hurt` | `damage=10`, optional integer `damage_type=0` (Source damage bitmask) |
| `gravity` | `gravity=0.3` multiplier, 0.05..4: a scripted zone (`add_ability` `gravity`; `trigger_gravity` does nothing in CS2), verified in game |

`add_ladder`: `mins/maxs` (the climbable volume, 8–16 deep against a wall);
creates the invisible climb surface (`toolsinvisibleladder`) **and a visible
stock metal ladder** (Nuke `metal_ladder_001` modules stacked to the height,
not solid, 2 units off the wall). The wall side is found by rays; give
`wall:"+x"|"-x"|"+y"|"-y"` to force it, `visual:"none"` for the surface only.
Without a wall within 24 units the ladder stands free in the middle (`note`);
a volume deeper than 24 gets a `warning` (the player stops at its face, far
from the rungs). Returns the surface `id`, `visual {wall, segments, ids, height}`,
`targetname=null` (no I/O). Verified in game: `int_ladder`, `f2_port` 21/21,
`k_warehouse` 16/16, `r2_ladder_auto` 17/17 (up and down). Do not use the
legacy HL2 `func_useableladder`.

## Connecting scripts and observing results

VMAP I/O uses `connect` with exact targetnames, for example a button
`OnPressed` → `p_slide` / `Open`. Script-side code uses Valve's API:

```js
import { Instance } from "cs_script/point_script";
Instance.EntFireAtName({name:"p_slide",input:"Unlock"});
Instance.EntFireAtName({name:"p_slide",input:"Open",delay:0.1});
const door = Instance.FindEntityByName("p_slide");
if (door) Instance.ConnectOutput(door,"OnFullyOpen",()=>Instance.Msg("door open"));
```

`physics_probe(map,names)` returns exact-match counts, existence, origins,
angles, velocity, health and alive state. It loads/verifies the requested
compiled map using the screenshot lifecycle, captures `context_frame`, and
installs a runtime-only `point_script` under the game lock. It does not edit
the VMAP. Names must be 1..64 exact targetnames; at most 16 matches per name
are expanded (the complete count is still returned). Unknown names return
`exists=false`. `debris` counts runtime `prop_physics`, including ordinary
named props; compare counts and entity records, not this number alone.

```json
{"map":"p_interactive","names":["p_slide","p_rotate","p_lift","p_own"]}
```

Take before/after frames with `game_screenshot` view `cmd`/`wait` fields.
For a multistep console experiment hold `game.exclusive()` throughout;
another agent may load a map between separate MCP calls. A missing entity
does not distinguish `Break` from `Kill` or a round reset. Use damage/event
console traces plus immediate debris observations and images. `setpos`
alone does not prove collision: walk with noclip off.

`model_info(model, physics=true)` reports the compiled-body gate.
`check_map` also reports doors without an opening route, empty buttons,
missing I/O targets and physics models without compiled bodies. A script
presence only makes door activation plausible; it does not prove behavior.

Visible buttons: `add_button` alone is a red brush. For a button on a wall use the `map_edit` operation `add_control_button` (same fields as `add_button` plus `visual={face,model?,scale?}`; `face` is the outward wall side `-x|+x|-y|+y`), or dress an existing button with `add_button_visual`. Both keep the brush and its I/O and hide it with an invisible material.

## By hand

Brush entities through `add_brush_entity`: `func_door` (`movedir`, `speed`, `lip`, `wait`; outputs `OnFullyOpen`/`OnFullyClosed`), `func_button` (`OnPressed`), `func_movelinear` (`movedir`, `movedistance`, `speed`; inputs `Open`/`Close`), `func_rotating`, `func_breakable`, `trigger_push` (`pushdir`, `speed`), `trigger_teleport` (`target` = an `info_teleport_destination`), `trigger_hurt`; wire them with `connect`. Physics props need a model with collision (`prop_physics_override`). Constraints and trains: section `lowlevel`.
