# Turret

A turret is a fixed controllable object in the shared `control` registry: the operation is `add_controllable` with `kind: "turret"`. Its runtime is built from the shared fragments `core`, `hud`, `camera`, `seat`, `health` and `fire` plus its own `turret_heat`; seating, camera, health and shot tracing are not copied. Code: `control.py`, `data/turrets/`, `data/mechanics/`; guide `docs/tools/control.md`.

## Geometry and resources

`data/turrets/models.py` builds three models: a fixed base, a platform around the yaw hinge and a twin-barrel block around the pitch hinge (+X barrel, +Z up, labelled in the source). Models and materials go only to `models/mcp_t` / `materials/mcp_t`; the first real `add_controllable` also creates the short `mcp.t9_shot` sound. Model previews are checked from the side and in isometric view: platform and block must neither float nor cut into the base.

The base is a `prop_physics_override` with a huge native HP and `prop_data`, so bullets reduce a value that `Health` reads. Platform and barrels are child `prop_dynamic`; their `Move` is called only when the pose changes, otherwise the client hides child parts. A pilot-name filter blocks damage from the turret's own shots.

## Seating and aiming

`Seat` needs the eye ray on the base and E. The player's position, angles, parent, move type and weapons are saved; the player is hidden and holds the knife; a still decoy stays visible behind the turret for others. A `FOLLOW_POSITION` camera follows the sight block, so the mouse is read. `aim()` turns the world view into a local angle relative to `yaw`, clamps it to `limits.yaw/pitch` and moves at most `turn_rate × dt` per tick. Contract and telemetry pitch is positive up; `eye.pitch` and the QAngle given to `Fire` have the opposite sign. Hinges: yaw `[0,0,20]`, pitch `[0,0,34]` from the base; default camera `[44,0,16]` from the pitch hinge.

## Firing and heat

The adapter gives `Fire` the world muzzle point, local QAngles, the player as `shooter` and the base as `inflictor`. The contract's `rate` is `fire.cooldown`; other fields pass the weapon validator. `fire.heat`/`fire.cool` are accepted and normalised to `heat.gain`/`heat.cool`; giving both forms is refused. Glowing rod pool, sparks, sound, spread and damage are one `Fire` for turrets and weapons. The tracer uses the shared `models/mcp_x/beam/beam_core.vmdl`; if it is missing the operation refuses before saving and suggests preparing weapon assets.

`Seat` suppresses all pilot damage by default; after seating, a local handler allows only damage whose attacker is this pilot and whose inflictor is this turret's base, so kills are credited to the player while the pilot's knife and grenades stay filtered.

`turret_heat` keeps a number 0..1. A shot is detected by `Fire` lowering ammo or moving the next shot time (automatic reload ticks included); each shot adds `heat.gain`; at 1 the turret locks out; each tick subtracts `heat.cool × dt`, and the lockout ends at zero. The HUD shows ten segments and `OVERHEATED`.

## Life cycle and limits

`Health` handles smoke, fire, explosion, native damage, respawn and exit on death. `t9_turret_<name>_state <token>` prints HP, local angles, heat, muzzle, the seated pilot, statistics and recent events. Automatic target picking is not implemented; `auto: true` is refused before writing.

## Tests

Unit and Node tests (`tests/test_turrets.py`) cover the schema, rate mapping, limits, entering and leaving, turn rate, bursts, heat and native damage on the engine double. In game `t9_turret`: real E, aim and mouse, a 2-second held attack, yaw/pitch limits, damage to a bot, overheating, leaving, pilot death, round restart, destruction and respawn. The stress stand uses heat 0.2 per shot and cool 0.1/s; defaults 0.06/0.35 overheat only on longer bursts.
