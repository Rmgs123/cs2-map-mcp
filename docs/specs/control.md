# Controllable objects: the drone and the kind registry

`add_controllable` places an object a player takes over with E: a `drone` here, a `car` (`vehicles.md`) and a `turret` (`turrets.md`) in their own modules. All kinds share entry and exit, the hidden pilot with a stand-in, the camera, damage and telemetry (`mechanics.md`). Code: `control.py`, `ext/control.py`, `data/control/` (`drone.js`, Blender models, HUD); guide `docs/tools/control.md`.

## How it works

1. **Validation**: finite numbers, names, compiled models, a flight area and hull of positive volume with the start inside (1 unit clearance); room points from `add_layout`; `health` needs `physics: true`.
2. **Dry run first**: a `map_edit` wrapper runs the whole batch without writing resources (HUD, script); only a clean pass writes them. Hammer's unsaved edits are checked before.
3. **Entities**: the body is a `prop_physics_override` with engine health 100000 (or a kinematic `prop_dynamic`), the remote a solid `prop_dynamic`, the pilot's stand-in a parked `prop_dynamic`, rotors parented to the body, the HUD `custom_hud_layout`, sounds and effects (smoke, fire, explosion).
4. `check_control` verifies the entities, their classes and the compiled HUD and script.

**The drone model** (`data/control/models.py`): 20 uniquely named `*_col` objects (Blender's `.001` suffix would hide the marker) become 20 convex hulls: body, camera, four arms, motors and rotor discs, legs, skids; mass 8 kg; `prop_data` with `dmg.bullets/club/explosive = 1`.

**Flight** (every tick): the wanted velocity from WASD/SPACE/CTRL relative to the view yaw (SHIFT slows to 0.4), change limited by `accel·dt`, applied with `Teleport({angles, velocity})` without a position so the engine handles movement and collisions. `800·dt` is added to z: the engine subtracts gravity in the step, so the effective velocity is the wanted one (otherwise hovering sinks about 5 units). Pitch and roll follow the requested acceleration (up to `tilt`, smoothed `8·dt`). Soft walls fade the velocity toward the edge of the area. Rotors are posed from the body basis plus their own spin. After the pilot leaves: `hover` holds the point (P gain 2.5, at most speed/2) or `land` descends to the ground. The battery drains in flight and recharges on the ground.

**Damage**: the body's engine health stays huge; each tick the drop of `GetHealth()` is taken from the script's HP (bullets, grenades, knife, `RemoveHealth`). Half HP smokes; 0 explodes, darkens, tumbles under physics and burns until `respawn` (or forever with `"never"`).

**Pilot**: E (rising edge) with an eye ray of 96 units into the remote. Position, angles, move type and weapon are saved; the player is frozen on an anchor, holds only the knife, his own damage to the drone is cancelled, and the camera follows the body (`FOLLOW_POSITION`, first person at the lens or chase, R switches).

## Why it is built this way

- Velocity-only `Teleport` keeps real physics: the drone bumps into walls and can be shot and pushed.
- A huge engine health with script HP means the engine never deletes the body and every damage source counts.

## Pitfalls we hit

- **Props without `prop_data` take no bullet damage at all** (while `RemoveHealth` still works).
- **CS2 always turns the player's body with the view.** In `CONTROLLED` camera mode the camera angles are copied into the eye angles (the script cannot read the mouse) and teleporting the body's angles resets the view. So the pilot's body and weapons are hidden (`SetColor` with alpha 0) and a stand-in of the same model stands at the anchor, facing where the player looked.
- **Leave with yaw only.** `Teleport` with a pitch tilted the body entity, and the eye offset (0,0,64) stayed rotated for good: the view "came loose" from the body. Pitch goes through `SetEyeAngles`.
- `SetColor` without `a` makes a prop invisible.
- Leaving because the pilot died must not touch the dead body (the engine's death camera).

## Tests

Node test bed `data/control/test_runtime.cjs`, `tests/test_control.py`; in game: entry/exit, flight, walls, hover, pilot killed in flight, round restart in flight, destruction in flight, re-entry after each, real mouse, the stand-in, hitbox accuracy with real bullets (a ray inside the bounds but between parts passes, the edge of a rotor disc hits) on `d8_drone` and `p8_mechanics`.
