# Cars

`add_controllable` with `kind:"car"`: an off-road SUV the player drives. It is a physics body on four ray-cast wheels with suspension: each tyre finds the ground with three rays, travels up 9 and down 12 units, and the body follows the plane of its wheels (a wheel that loses the ground drops the body's corner until it touches again). Traction exists only where tyres touch and is limited by friction, so it climbs about 35° and slides on steeper banks. Logs, kerbs and rocks up to ~14 units, ruts and humps are taken; walls stop it with crash damage.

| Task | Take |
| --- | --- |
| A drivable car | `add_controllable`, `kind:"car"` |
| Dirt roads, hills, rocks to drive on | `add_terrain` with `paint` tracks, `scatter` (section `terrain`) |
| Build the car assets in advance | `assets_prepare(kind="car")` |
| Check driving in the game | `game_reset` → `script_test` (E, `hold` forward/left/jump, `v9_control_<name>_state`) |
| Other geometry or driving rules | see **By hand** |

## Example (verified on `v9_car`, `p9_offroad`)

```json
{"op":"add_controllable","name":"jeep","kind":"car",
 "origin":{"room":"yard","at":[0.5,0.3],"z":10},"yaw":90,"health":300,"respawn":8,"ref":"jeep"}
```

A heavier rally car with a soft long-travel suspension and a quick, light wheel:

```json
{"op":"add_controllable","name":"rally","kind":"car","origin":{"room":"yard","at":[0.3,0.3],"z":10},
 "speed":900,"accel":420,"steer":{"max":36,"fast":7,"rate":180},"suspension":{"up":14,"down":18,"stiffness":12,"damping":10,"grip":1},
 "camera":{"forward":0,"up":72,"chase":{"forward":-320,"up":120},"start":"chase"}}
```

Put `origin` on the ground (+10 is fine: it settles on its wheels). The first real edit builds the car, the wheel, materials and two engine loops (`models/mcp_v`, `sounds/mcp_v`); dry runs build nothing.

## Fields (plus the shared ones in section `control`)

| Field | Default; range, units |
| --- | --- |
| `speed`, `reverse` | 700 (50..1000), 180 (10..400) u/s; 700 u/s = 64 km/h |
| `accel`, `brake` | 300 (50..1500), 700 (100..2500) u/s² |
| `grip` | 9 (1..25): how fast sideways sliding dies |
| `steer` | `{max:32, fast:9, rate:120, return:200}`: front-wheel angle standing and at top speed (degrees), how fast the wheel turns (°/s; half as fast at top speed, so it feels heavier) and comes back; a number = `max` |
| `mass` | 1500 kg (100..5000) |
| `health`, `respawn`, `effects` | 150 HP, 10 s, true |
| `crash_damage` | .12 (0..1): HP per u/s lost in a fast wall hit (from 240 u/s) |
| `reach` | 80 (16..80); feet must also be beside the driver's door (+Y side) |
| `camera` | chase `{forward:-270, up:105}` first; R: driver camera `{forward:0, up:72}` (in the cabin; `forward` moves along the view, so keep it 0 for a seat) |
| `engine` | `{idle, drive}` loop events (stock ones by default) or `false`; `sound` is not used |
| `model`, `wheel_model` | stock SUV and wheel; an own body gives its `wheels` |
| `wheels` | `{positions:[[62,36],[62,-36],[-60,36],[-60,-36]], radius:16, z:16}`: axle centres (front left, front right, rear left, rear right; the car faces +X), tyre radius, axle height at rest; the stock wheel is scaled to the radius |
| `suspension` | `{up:9, down:12, stiffness:16, rebound:7, damping:14, grip:.85}`: wheel travel (units), spring in bump and rebound, damper, tyre friction (× weight) |
| `seat` | `[-6,17,40]`: the driver's hips in the body frame (the stand-in sits there) |

## In game

E at the driver's door. **W** gas, **S** brake then reverse, **A/D** steer (less and slower at speed), **SPACE** handbrake (also holds on slopes), **SHIFT** crawl (slow, strong: steep climbs and obstacles), mouse looks around, **R** driver camera (in the cabin), **E** exit by a free door (else the other door, the roof, an end, or the nearest free spot around). HUD: km/h, HP, `CRAWL`, `AIR`. Without gas it rolls with the slope. Bullets and explosions damage the car; at 0 HP it explodes and the wreck burns on its wheels until respawn. A car on its roof or side for 2.5 s is put back on its wheels. Players hit at speed are hurt (never the driver).

## Test it

```json
[{"walk":[256,330,8]},{"aim":[256,256,40]},{"use":true},{"expect":"V_CAR jeep enter","within":3},
 {"cmd":"v9_control_jeep_state start\n+sprint"},{"hold":{"key":"forward","seconds":2}},{"shot":"crawl"},
 {"cmd":"-sprint\nv9_control_jeep_state go"},{"expect":"\"token\":\"go\"","within":3},
 {"hold":{"key":"jump","seconds":1}},{"use":true},{"expect":"V_CAR jeep exit","within":3}]
```

`v9_control_<name>_state <token>`: position, speed, angles, `contacts`, wheel `travel`, and `peak` since the last report (`bump`, `droop`, `pitch`, `roll`, `air` seconds, `speed`) — the numbers that prove the suspension worked. Damage by I/O: `RemoveHealth` on `v9_<name>_body`.

## Limits

One driver; network play is not tested. Wheels have no collision (the suspension carries the body; the body's own collision starts 24 units above the ground). The driver stand-in is half size inside the dark cabin.

## By hand

`prop_physics_override` with convex hulls (no tyres), `PhysicsBodyMarkup` mass and `prop_data` damage scales; non-solid `prop_dynamic` wheels parented to it; HUD, damage filter and stand-in like the drone. In the script, every tick: cast rays down along the body from each wheel (centre, ±0.55 radius) and take the highest required wheel centre; fit a plane to the wheels' lifts (an unsupported wheel asks for −18 at lower weight); set `Teleport({angles, velocity, angularVelocity:0})` toward it, adding `800*dt*support` to z (the engine applies gravity after the script); add the slope's gravity along the ground yourself and limit the tyre force to `0.85 × g × cos(slope)`. Move wheels with `Move` only when their pose changes.
