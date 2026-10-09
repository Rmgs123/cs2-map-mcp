# Mini-games

`add_minigame` adds a start button, its props, sounds, a script and a HUD (score, timer, best result of the session). **E** on the button starts a run for one player; others see BUSY. Death, the player's reset and a round restart clear a run; the best result stays until the map reloads.

| Task | Take |
| --- | --- |
| Shooting gallery: targets rise and fall, points for hits | `kind:"gallery"`, `targets` |
| Race through rings, on foot or on a drone/car | `kind:"race"`, ordered `targets`, `vehicle` |
| Collect items before the time ends | `kind:"collect"`, `items` |
| Check it in the game | `game_reset` → `script_test` (E, `aim`, `hold` attack, `go`, `m9_<name>_state`) |

## Example (verified on `m9_games`)

```json
{"op":"add_minigame","name":"range","kind":"gallery",
 "origin":{"room":"yard","at":[0.15,0.5],"z":0},
 "targets":[{"room":"yard","at":[0.8,0.2],"z":0},{"room":"yard","at":[0.8,0.5],"z":0},
            {"room":"yard","at":[0.8,0.8],"z":0}],
 "time":30,"popup":[1,2.5],"ref":"range"}
```

```json
{"op":"add_minigame","name":"airrace","kind":"race","vehicle":"drone1",
 "origin":{"room":"yard","at":[0.2,0.2],"z":0},
 "targets":[{"room":"yard","at":[0.4,0.5],"z":120},{"room":"yard","at":[0.7,0.5],"z":160}],
 "time":60,"radius":64}
```

## Fields

| Field | Default; range, units |
| --- | --- |
| `name` | required: lowercase letter + up to 39 letters, digits, `_`; entities `m9_<name>_*` |
| `kind` | `gallery`, `race` or `collect` |
| `origin` | required: the start button's base (room point or `[x,y,z]`); put it on a floor |
| `targets` | gallery: stand bases (the shield is 42 higher and faces the button); race: ring bases in order (centre +64) |
| `items` | collect: item bases |
| `time` | 30 s (1..600) |
| `popup` | gallery: `[min, max]` seconds a target stays up/down (default `[1, 2.5]`, 0.1..60) |
| `radius` | 56 units (8..256): race and collect pickup distance |
| `vehicle` | race: a controllable's name (`drone1`) or a body targetname; the rings are taken by the vehicle |
| `hud` | `{"title": "..."}`, up to 24 characters (default by kind) |
| `ref` | names the script node |

Up to 64 targets or items. The gallery scores 10/5/2 for the centre, ring and edge; stock bullets, scripted shots and explosions (`add_weapon` `fire`) all count. In a race the next ring is yellow, passed green; skipping one adds 5 s once.

## Test it

```json
[{"walk":[160,512,8]},{"aim":"m9_range_start"},{"use":true},{"expect":"M_MINIGAME range start","within":3},
 {"aim":[820,204,42]},{"hold":{"key":"attack","seconds":0.2}},{"expect":"M_MINIGAME range score_hit","within":3},
 {"cmd":"m9_range_state after"},{"expect":"\"token\":\"after\"","within":3},{"shot":"range_score"}]
```

## By hand

Button: a solid model + the E ray (`core.eyeRay`). Targets: `prop_physics_override` shields with convex hulls and `prop_data` on a `phys_hinge` along their lower edge, moved by `SetMotorTargetAngle` (never teleported). Rings and items: `prop_dynamic`, rotated with `Move`. Score from `OnBulletImpact` (hit entity, position) and native damage (`Health`); HUD per player through `custom_hud_layout`; timers and records in the script (`core`, `hud`, `health` fragments, see `docs/specs/mechanics.md`).
