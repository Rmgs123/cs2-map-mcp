# Car

`vehicles.py` registers `car` in the shared `control.register_kind`; `ext/vehicles.py` loads automatically and adds no tools. The shared `add_controllable` keeps batch checks, the VMAP backup, the dirty-Hammer refusal, the definition marker and the asset check. Seating, camera, HUD, health, respawn and telemetry are not copied: the car runtime (`data/vehicles/car.js`) is bundled with the `core`, `seat`, `camera`, `hud` and `health` fragments. Guide: `docs/tools/vehicles.md`.

## Assets

`data/vehicles/models.py` (Blender): +X front, +Y the driver's side, inches, +Z up. An off-road SUV body with chassis, bonnet, boot, clear glass, seats, lamps and mirrors; the wheel is a separate model with its origin on the axle, scaled to the tyre radius. The body has convex hulls without tyres, `PhysicsBodyMarkup` mass and `prop_data` damage scales; a huge native HP keeps the engine from deleting the prop. Wheels are non-solid. Models, materials and two engine loops go to `models/mcp_v`, `materials/mcp_v`, `sounds/mcp_v`; the manifest has a version and the Blender source's fingerprint, Blender has a 180 s timeout, compiles go through the shared job manager. Dry runs build nothing; `assets_prepare(kind="car")` builds in advance. Wheels sit in empty arches, so each gets `cs2map_inset` with the body id: the box overlap check would otherwise call them buried in the body.

An own body (`model`) brings its own `wheels` (axle centres, radius, height at rest) and `seat`.

## Driving model

Every tick (dt capped at 0.05): health, seat, wheels, motion, child parts, HUD.

- **Suspension**: each wheel casts three rays down along the body (centre, ±0.55 radius) ignoring the car, its wheels, the stand-in, the driver and players, and takes the highest required wheel centre within `up`/`down` travel. A plane is fitted to the wheels' lifts (a wheel without ground asks for −18 at a lower weight), and the body is steered toward it with `Teleport({angles, velocity, angularVelocity: 0})`, adding `800 × dt × support` to Z because the engine applies gravity after the script. Spring (bump and rebound), damper and travel come from `suspension`.
- **Traction** exists only where tyres touch and is limited to `grip × g × cos(slope)`; the slope's gravity along the ground is added by the script, so the car climbs about 35° and slides on steeper banks. Velocity is split along and across the car: gas changes the longitudinal part within `accel`, S brakes then reverses, sideways speed dies by `grip`, the handbrake brakes hard and holds on slopes, SHIFT crawls (slow and strong). Steering is smoothed: `max` standing, `fast` at top speed, turning half as fast at speed; yaw rate is the bicycle model `v / wheelbase × tan(steer)`.
- **Air**: with no contacts the script writes no velocity: gravity and momentum act alone (`AIR` on the HUD). A car on its roof or side for 2.5 s is put back on its wheels.
- **Collisions** are real physics; the body is never teleported in position while driving. A wall hit losing more than 100 u/s within 0.18 s from above 240 u/s takes `(loss − 60) × crash_damage` HP (max 100, cooldown 0.6 s); the window matters because the engine stops a body over several physics steps. Players hit at speed take 10..90 HP (box with a dt margin, a ray against hits through walls, 0.8 s per pawn); the driver is excluded and the attacker is the body, since `Seat` cancels damage attributed to the pilot.
- Wheels spin by `distance / radius`, front wheels also steer; child parts `Move` only when their pose changes, so client interpolation is not reset.

## Driver

E needs the eye ray on the body within `reach` and feet beside the driver's door. The shared `Seat` hides the player and weapons and shows a stand-in; the car adapter puts the half-size stand-in in the cabin and parents the hidden pawn to the body, restoring the old parent on exit (an unparented pawn in the cabin flipped the car and died from crush in the first live run). Exit checks both doors, the roof and both ends with a supported 32×32×72 hull, then the last free spot and the entry point; when nothing is free, exit waits and the HUD shows `Exit blocked`. The chase camera reads the mouse; R switches to the driver camera. Parent, alpha, HUD and camera are restored on death, round restart, disconnect and destruction.

## Limits

One driver; network play is not tested. Wheels have no collision (the body's own collision starts 24 units above the ground). The stand-in does not play a seated animation. Another body shape needs its own `wheels`/`seat`; a different drive model needs its own JS adapter.

## Tests

`tests/test_vehicles.py` (schema, driving maths without the engine, transitions, damage, telemetry, Node double). In game `v9_car` (8-unit kerb, 64-unit ramp, wall, free yard) and `p9_offroad` (hills, ruts, logs, rocks); telemetry `v9_control_<name>_state` reports contacts, wheel travel and peaks (bump, droop, pitch, roll, air time, speed).
