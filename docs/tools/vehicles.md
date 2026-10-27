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
| `reach` | 80 (16..160); feet must also be at the driver's door (`door`) |
| `camera` | chase `{forward:-270, up:105}` first; R: driver camera `{forward:0, up:72}` (in the cabin; `forward` moves along the view, so keep it 0 for a seat); `pilot:true` puts the driver camera at the driver's eyes (a seat far from the body origin); chase up to `{forward:-1024, up:512}` |
| `engine` | `{idle, drive}` loop events (stock ones by default) or `false`; `sound` is not used |
| `model`, `wheel_model` | stock SUV and wheel; an own body gives its `wheels` |
| `wheels` | `{positions:[[62,36],[62,-36],[-60,36],[-60,-36]], radius:16, z:16}`: axle centres (front left, front right, rear left, rear right; the car faces +X), tyre radius, axle height at rest; the stock wheel is scaled to the radius |
| `suspension` | `{up:9, down:12, stiffness:16, rebound:7, damping:14, grip:.85}`: wheel travel (units), spring in bump and rebound, damper, tyre friction (× weight) |
| `seat` | `[-6,17,40]`: the driver's hips in the body frame (the stand-in sits there) |
| `pilot` | `{scale:.5, at:seat}`: the driver's stand-in (its size, its feet in the body frame) |
| `door` | `{at:[-8,66,0], radius:62}`: where the driver's feet must be for E (body frame) |
| `exits` | the stock car's five points: body-frame points tried in order when the driver leaves |
| `size` | `[236,116]`: body length and width (run-over hits, crash probe, passengers) |
| `scale` | 1: a bigger or smaller body — every body-frame length (wheels, seat, door, exits, size, passengers, stand-in place, cameras, suspension travel) times it, once; pair it with a scaled copy of the body `model_build(name="van_xl", file="models/mcp/van/van.vmdl", scale=1.2)` |
| `passengers` | none: a hollow body players ride in, `{floor, roof, doors:[{at, radius, spots}], hint:{board, ride, drive}}` (below) |

## Own bodies: a bus

A long hollow body (verified with a 590-unit bus on a test field and on a town map): the driver walks in and presses E at the wheel (`door` inside, `reach` 140, `pilot:{scale:1}`, `camera.pilot` with `forward` 7 so the view is just in front of the stand-in's face; `chase:false` for first person only). `passengers.doors` lists the doorways with their groups of standing spots (feet, body frame, 2 units above the floor): E near a door, or anywhere inside, puts a player on a random free spot of that door's group (the nearest other group when it is full); E again lets them off. A prompt shows the texts of `hint` at a door, at the wheel and while riding. Whoever stands inside when the body has a driver or moves is put on a spot: players merely standing on a physics body weigh it down and it does not move. Riders stay normal standing players (not parented: a parented pawn turns and leans with the body; not frozen: a frozen pawn shoots as if in the air and cannot stand up after crouching); every tick they stand on the floor at their spot where it will be after the physics step, at rest, so their fire is as accurate as standing on the ground (measured: 0.17-0.24° first-shot error parked and riding). A driver leaving at speed becomes a front passenger; standing still, they stay on the salon floor (an exit point with someone in it is skipped; then a spot; then out of a door). A rider getting off while the body has a driver or moves leaves through the nearest door with room outside, or stays on (`exit_blocked`, the `hint.blocked` prompt for 1.5 s); a parked bus lets them off inside. A rider of a still body is not teleported while at the spot, and a crouched rider of a moving one is lifted by the stand-up drop when letting go of crouch: CS2 treats a just-teleported player as airborne and stands them up by lowering the feet, into the floor. A player standing on the moving body from outside (roof, outer step) is boarded at a door or put down beside one. When the body is destroyed its riders get off (and take the blast like anyone); when it is gone, respawns or the round restarts, riders and every player's prompt are cleared (prompts are hidden per player: a shown per-player prompt overrides an all-player hide). A spot with no room (the body pressed on a wall, a player in it) moves its rider to another free spot, else out of a door; players with no spot left are put out of a door, never left loose in a moving body. Keep spots at least 12 units from anything inside (wheel housings): a few units of roll or lag otherwise make the body crush the rider. Inside the bus only weapons hurt the driver and riders (crush, falls and the body's pushes are cancelled; `hurt` and `killed` lines name what hit them).

**Parking**: without a driver or a route test, a body on its wheels and nearly still switches its physics motion off (`parked`/`unparked` in the log): players walking in it, pushing or standing on it cannot slide, sink or shake it. Gravity stays: in the air or rolling it keeps its physics until it settles; a driver turns it back on. E is one action per half second per player. Spot and exit checks ignore weapons and items (a carried C4), and never ignore an entity parented to the body (CS2 then skips the whole hierarchy, the body included).

`hud`: `title`, `hint` (the key line) and `labels` (`kmh`, `crawl`, `exit`, `blocked`, `air`: the words of the status line) translate the driver's panel.

```json
{"op":"add_controllable","name":"bus","kind":"car","model":"models/mcp/bus/bus.vmdl",
 "origin":[0,0,8],"health":1000,"mass":5000,"speed":560,"accel":180,"steer":{"max":34,"fast":10,"rate":70,"return":120},
 "wheels":{"positions":[[144,61],[144,-61],[-144,61],[-144,-61]],"radius":27,"z":27},"suspension":{"stiffness":30,"damping":16},
 "seat":[238,37,50],"pilot":{"scale":1,"at":[236,37,24]},"door":{"at":[226,-24,24],"radius":40},"reach":140,
 "exits":[[150,-20,50],[100,-20,50]],"size":[590,140],
 "passengers":{"floor":24,"roof":140,"doors":[{"at":[220,-78,14],"radius":70,"spots":[[165,-24,26],[165,26,26],[118,26,26]]},
   {"at":[-218,-78,14],"radius":70,"spots":[[-160,-24,26],[-160,26,26],[-245,0,26]]}],"hint":{"board":"E: get in"}},
 "camera":{"forward":0,"up":72,"pilot":true,"chase":{"forward":-620,"up":230},"start":"chase"}}
```

`models/mcp/bus/bus.vmdl` is your own hollow body, 590×140, +x forward, the floor at 24: `model_build(name="bus", template="bus_body")` makes exactly this one (doors at x 225 and −218 on −Y, open windows; `params` for another size, doors or colours), or a script or file of your own; give it its own `wheel_model` too. The recipe must fit the model: `add_controllable` refuses a `size`, `seat`, door or passenger spot outside the body model's bounds — this recipe on the stock car (204×84) put the riders in the air beside a small car and their shots went 10° wide. Breakable windows are `func_breakable` boxes parented to the body (`parentname` `v9_<name>_body`), authored at the body's spawn pose; E on them counts as E on the body.

## In game

E at the driver's door. **W** gas, **S** brake then reverse, **A/D** steer (less and slower at speed), **SPACE** handbrake (also holds on slopes), **SHIFT** crawl (slow, strong: steep climbs and obstacles), mouse looks around, **R** driver camera (in the cabin), **E** exit by a free door (else the other door, the roof, an end, or the nearest free spot around). HUD: km/h, HP, `CRAWL`, `AIR`. Without gas it rolls with the slope. Bullets and explosions damage the car; at 0 HP it explodes and the wreck burns on its wheels until respawn. A car on its roof or side for 2.5 s is put back on its wheels. Players hit at speed are hurt (never the driver).

## Test it

A route without a driver (cheat): `v9_control_<name>_route x1,y1,x2,y2,...[,speed]` drives through the points with proportional steering and logs `route_point`, `route_tick` (each second), `route_done`, or `route_stuck` with the place where it stopped; `..._route off` ends it. Keep the points farther apart than the turning radius (a 288-unit wheelbase turns in ~15 m at walking pace) — it finds narrow passages and scraping humps along a road.

```json
[{"walk":[256,330,8]},{"aim":[256,256,40]},{"use":true},{"expect":"V_CAR jeep enter","within":3},
 {"cmd":"v9_control_jeep_state start\n+sprint"},{"hold":{"key":"forward","seconds":2}},{"shot":"crawl"},
 {"cmd":"-sprint\nv9_control_jeep_state go"},{"expect":"\"token\":\"go\"","within":3},
 {"hold":{"key":"jump","seconds":1}},{"use":true},{"expect":"V_CAR jeep exit","within":3}]
```

`v9_control_<name>_state <token>`: position, speed, angles, `contacts`, wheel `travel`, and `peak` since the last report (`bump`, `droop`, `pitch`, `roll`, `air` seconds, `speed`) — the numbers that prove the suspension worked. Damage by I/O: `RemoveHealth` on `v9_<name>_body`.

## Limits

One driver; network play is not tested. Wheels have no collision (the suspension carries the body; the body's own collision starts 24 units above the ground). The driver stand-in is half size inside the dark cabin. Window panes on a body: one face each with a glass material (a closed glass box seen from inside stacks four tinted layers and looks black).

## By hand

`prop_physics_override` with convex hulls (no tyres), `PhysicsBodyMarkup` mass and `prop_data` damage scales; non-solid `prop_dynamic` wheels parented to it; HUD, damage filter and stand-in like the drone. In the script, every tick: cast rays down along the body from each wheel (centre, ±0.55 radius) and take the highest required wheel centre; fit a plane to the wheels' lifts (an unsupported wheel asks for −18 at lower weight); set `Teleport({angles, velocity, angularVelocity:0})` toward it, adding `800*dt*support` to z (the engine applies gravity after the script); add the slope's gravity along the ground yourself and limit the tyre force to `0.85 × g × cos(slope)`. Move wheels with `Move` only when their pose changes.

### Riders in a low cabin (a car, not a bus)

`passengers` needs standing room (a cabin over 72 units). For a small car there is no option yet; these rules were measured in CS2 for a 4-seat SUV:

- **Riders cannot stand in it.** The engine never crouches a player for a script: a standing player put into a lower cabin is stuck (crush damage every tick, a light car rolls over). Board with a crouch (Ctrl+E: `script_test` step `{"use":true,"with":"duck"}`); under a roof lower than a standing hull the engine keeps the crouch by itself, also at 80 km/h.
- **Player hulls are world-axis boxes.** In a body turned by θ a 32×32 hull spans 16·(|cos θ|+|sin θ|): at 45° keep a circle of r = 22.6 around each seat free of body collision, plus the one-tick lead (speed/64, 13.7 units at 875 u/s) and ~3 units above heads and below feet. Two riders closer than 45.3 units overlap at some headings (side by side 32.6 apart: 11°–79°) and stall the car, even with `mp_solid_teammates 0`: in a small car only diagonal seats are safe for two free riders.
- **Their floor is not the body.** A plate in the body's collision put the car down on it. Use a player-only clip `func_brush` (`toolsplayerclip`, `Solidity` 2) parented to the body; a parented `func_brush` dies with the body at a round restart and is not re-created: unparent it in `OnBeginRoundRestart` and parent it again in the new round. Hold riders one tick ahead (spot + body velocity·dt, velocity 0).
- **Visible occupants who do not shoot** (what worked): keep each occupant the real player, crouched, parented to the body, `MoveType` NONE: their own hitboxes sit in the windows, so shots through a window hurt them. Give the feet a non-parented player clip teleported with the body each tick (foot IK skips the parent). Keep the view within ±50° of the heading (the body turns to the view). Empty every gun while seated (`SetClipAmmo(0)`, `SetReserveAmmo(0)`, give it back on exit) and remove grenades thrown from a seat (`OnGrenadeThrow`).
- **Eye angles of a parented pawn:** `GetEyeAngles` is world space, `SetEyeAngles` is relative to the parent. Set relative yaws (forward = 0) and unparent before restoring world angles; test every vehicle parked at a non-zero heading (at yaw 0 both agree).
- **Breakable windows on a moving body:** take the panes out of the model, build each as a `func_brush` from the glass shell at the car's spawn pose, parent them (and unparent at a round restart as above); break by script (`OnBulletImpact` hits, then `Disable`, a glass particle and `Breakable.Glass`), `Enable` on restart and respawn. A scripted pane can come back; a `func_breakable` cannot.
