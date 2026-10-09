# Interior palettes and room dressing

Themed stock palettes (`theme_info`) and operations that furnish a room or add trims, pipes and cables without changing collision or routes: `dress_room`, `add_trim`, `add_pipes`, `add_cables`. Code: `dressing.py`, `ext/dressing.py`, palettes `data/themes/<theme>.json`. Only installed stock resources are used; nothing is written besides the map.

## How it works

**Palettes** (`schema: 1`): `materials` {floor, walls, ceiling, trim, metal, wood} with a per-role verified flag, `props` [{key, model, bounds, size, has_physics, mass, tris, role, verified}], `decals`, `sounds` (found in the index; playback is not claimed), `lighting`, `notes`. Prop sizes come from `resourceinfo`: MDAT scene-object bounds merged with PHYS hull bounds. Static models keep their own origin, so support is computed from the bottom of the measured box, never by assuming `origin.z = 0`. Bounds of other existing models are read the same way and cached locally.

**`dress_room`**:
1. Validate everything first (bounds, density, seed, flags, `max_props`, `keep_clear`); the room is an explicit interior or a name from the layout metadata.
2. Collect world and brush solids, their triangles and existing prop boxes (helper volumes, triggers and `physicsType: none` excluded).
3. Scan the walls with outward rays at floor+36 and floor+96 every 16 units; reserve 96-deep strips at openings, 96-wide paths to the centre and a central cross; spawns get a 64 radius, doors, buttons, ladders and movers their bounds plus a margin (linear movers along their travel).
4. A seeded shuffle of a fixed grid along the walls chooses positions and props (balanced by use; bins only near corners, two at most); yaw follows the wall; the eight rotated corners keep 4 units off the wall and inside the room.
5. Each box is checked against clear zones, the new props (8 units apart) and existing solids/props; five rays down (centre and corners) need a flat floor within 1 unit and ±18 of the floor height, which also fixes `origin.z`.
6. Add `prop_static`, wall decals, up to three lights and free tall details. `density: 0` does nothing. The result lists ids, placed vs target props, skipped places, portals and clear zones; the target density is not a guarantee.

**Trims, pipes, cables**: `add_trim` cuts openings out of the wall intervals and adds non-solid boxes; `add_pipes` builds closed n-gon cylinders along any direction (orthonormal basis), optional stock fittings as non-solid props (a decorative gas assembly, not an elbow); `add_cables` follows `z(t) = lerp(z0, z1) − 4·sag·t·(1−t)` with six-sided cylinders. Topology is checked (closed half-edges, outward normals).

## Limits

Box checks are conservative: a free spot may be rejected. Model boxes cover render + hull, not animated skins or physics motion; complex doors, rotation and terrain need `keep_clear`. The 96 corridor is a guarantee for the props placed, not a fix for existing obstacles. `script_test(steps="auto")` teleports and does not prove walking.

## Tests

`tests/test_dressing.py`; in game the round 4 maps (`f2_port`, `k_warehouse`).
