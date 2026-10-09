# Turrets

`add_controllable` with `kind:"turret"`: a stationary gun the player mounts with **E**. The base stays, the platform turns (yaw) and the barrel tilts (pitch) inside limits at a turn rate; the shot is the shared fire engine of section `weapons` (bursts, spread, tracers that fade, sparks, sound) with overheating.

| Task | Take |
| --- | --- |
| A mounted gun | `add_controllable`, `kind:"turret"` |
| Its shot: damage, rate, tracer, sound | `fire` (same fields as guns, section `weapons`) |
| Overheating | `heat:{gain, cool}` |
| Own body | `model_build` three parts with the same pivots → `base_model`, `platform_model`, `barrel_model` |
| Check it in the game | `game_reset` → `script_test` (E, `aim`/`mouse`, `hold` attack, `t9_turret_<name>_state`) |

## Example (verified on `t9_turret`)

```json
{"op":"add_controllable","name":"gun1","kind":"turret",
 "origin":{"room":"yard","at":[0.5,0.85],"z":0},"yaw":270,
 "limits":{"yaw":[-60,60],"pitch":[-10,30]},"turn_rate":120,"health":200,
 "fire":{"rate":0.1,"damage":18,"spread":1.5},"heat":{"gain":0.06,"cool":0.35},"ref":"gun1"}
```

`origin` is the base on the floor (a roof works too). `yaw` is where the barrel looks at rest.

## Fields (plus the shared ones in section `control`)

| Field | Default; range, units |
| --- | --- |
| `limits` | `yaw:[-60,60]`, `pitch:[-10,30]` local degrees (yaw −180..180, pitch −89..89, increasing pairs; pitch up is positive) |
| `turn_rate` | 120 °/s (1..720) |
| `fire` | `rate` 0.1 s between shots (or `cooldown`), `damage` 18, `spread` 1.5°, `magazine` 250, `reload` 0.1, `recoil` 0, `sound` stock burst, `beam` orange tracer `{width:1, life:.15, fade:.3}`, `impact` true; every other field as in section `weapons`; `beam:null` / `impact:false` turn them off |
| `heat` | `{gain:.06 per shot, cool:.35 per second}`; at 1 the gun locks until cold (`fire.heat`/`fire.cool` are the same, not both) |
| `health`, `respawn`, `effects` | 200, 10 s, true |
| `reach` | 96 (16..160): E on the base or the grips |
| `camera` | sight `{forward:44, up:16}`, no chase |
| `base_model`, `platform_model`, `barrel_model` | stock parts; own parts keep the hinge origins |

## In game

E at the grips: the player is held behind the mount (a stand-in shows them), the mouse aims, the turret follows at `turn_rate` within the limits, ATTACK fires. HUD: HP, yaw/pitch and a heat bar (`OVERHEATED`). The pilot cannot hurt the turret; only its own shots count. E, death or round restart give back the place, the view and the weapon.

## Test it

```json
[{"walk":[260,512,8]},{"aim":[358,512,70]},{"use":true},{"cmd":"t9_turret_gun1_state seated"},
 {"expect":"\"token\":\"seated\".*\"active\":true","within":3},{"aim":[820,512,40]},
 {"hold":{"key":"attack","seconds":2}},{"expect":"T_TURRET gun1 hit","within":3},{"shot":"turret_fire"},{"use":true}]
```

`t9_turret_<name>_state <token>` reports `yaw`, `pitch`, `heat`, `overheated`, `muzzle`, hits and the last events.

## Limits

No autonomous mode (a player is needed); network play is not tested.

## By hand

A fixed `prop_physics_override` base with `prop_data`; platform and barrel `prop_dynamic` parented to it; damage filter, stand-in, HUD, `point_script`. Convert the eye angles to local yaw/pitch, clamp, rate-limit, `Move` the parts only when the pose changes; call `Fire.step(state, {trigger, src:{shooter, origin: muzzle, angles, inflictor: base}})` every tick and add heat per shot.
