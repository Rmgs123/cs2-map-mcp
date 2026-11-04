# Visual check

`visual.py` (loaded by `ext/visual.py`) never changes the VMAP. `visual_check(name)` loads the map and returns a compact list of problems; `checks.register_check("visual", ...)` adds the same problems to `check_map` without a second load. Guide: `docs/tools/visual.md`.

## Model bounds

For `prop_static`, `prop_dynamic`, `prop_physics` and `prop_physics_override` the compiled `models/...vmdl_c` is read from the addon or a VPK (a temporary copy only under `.local/scratch/Q/model_bounds/`). `resourceinfo.exe -b MDAT -baremode` gives `m_sceneObjects[].m_vMinBounds/m_vMaxBounds`; without scene objects, DATA `m_modelInfo` (`m_vHull*`/`m_vView*`) and then PHYS hull/mesh bounds are used; zero bounds are dropped. Own models fall back to `models/mcp/<name>/model.json`. Results are cached per `Project` by relative path. The local box is transformed by its eight corners (scale, then `structure._rotate`, then `origin`): a conservative world box, not a promise of the exact collision hull.

## Checks

Thresholds give warnings, not errors (a model can be unusual, a support can be invisible), except phantom floors and walls, which break play.

| Code | What it catches |
| --- | --- |
| `visual_floating_prop` | five rays down find no solid mesh within 16 units, no wall, ceiling or prop contact; the map bottom is the median top of ordinary `*floor*` materials, so a deep pool is not "the floor" |
| `visual_prop_floats_on_slope` | a static prop on terrain whose base hangs more than 8 units (plants 16) above the lowest ground under its footprint; the ground at a sample is the highest of the terrain, a mesh top and a non-plant prop no more than 8 units over the base; on a structure, samples 64+ units under the base are an overhang |
| `visual_floating_platform` | a raised concrete slab (≤ 32 thick, ≥ 4096 sq units, > 32 up) with solid support under less than 2 % of its area (ceilings, metal, water and tool materials excluded) |
| `visual_prop_intersection`, `visual_prop_overlap` | a prop deeper than 12 units into a wall on every axis; two props nested deeper than 24 or one centre inside the other (water/boats, lamps, pipes and `cs2map_inset` parts excluded) |
| `visual_z_fighting` | faces of different meshes overlapping in one plane (exactly or nearly coplanar), unless both are pressed against an opposite face |
| `visual_flat_model_on_surface` | a thin model lying in the plane of a mesh (a panel at the bottom of a niche) |
| `visual_wall_part_off_wall` | a wall-mounted part with no wall within 16 units |
| `visual_layout_obstruction` | a prop of 64+ units in a room's central cross corridor (±32) from `cs2map_layout` |
| `visual_worldtext_unbacked` | `point_worldtext` with no visible triangle within 32 units behind its plane (a hole in a wall is not a backing; rotating story hints excluded) |
| `visual_exposed_foundation` | an outdoor ground slab whose underside is visible from gameplay anchors (advisory; backdrop parts, water and closing ground count as finished) |
| `visual_lamp_unmounted` | a light farther than 32 units from any solid mesh or measured prop (shaft companions, lights outside authored geometry and parked carriers skipped) |
| `visual_open_mesh` | open half-edges on a collidable mesh; identical boxes of different materials |
| `visual_phantom_floor`, `visual_phantom_wall` (errors) | a render-only mesh (`physicsType none`) showing a floor more than a few units above the real floor, or a wall at player height with no collision behind it: players walk and shoot through it |
| `visual_wall_gap`, `visual_unit_over_window` (`visual_scene.py`) | wall parts (mount `wall`, AC units, `wall_mounted`): five rays from each side of the oriented box (Möller–Trumbore over a numpy cell index of steep visible faces, 32 units ahead, 8 behind); the side with most hits and the smallest gap is the back; gap > 1.5, sunk > 4, uneven > 4, or 1–2 rays without a wall. A glass/window face parallel within 30 units overlapping 6×6+ |
| `visual_cable_in_air` | `add_cables` meshes carry `cs2map_cable` (both ends); an end with no visible non-cable face within 6 units (exact point–triangle distance) and no prop box within 6 |
| `visual_door_over_window` | door faces (material "door", 60–130 × 20–110) against parallel glass faces within 16 units, overlap ≥ 0.03 m²; a pane inside the door's outline within 3 units is a glazed door |
| `visual_paved_slab` | free edges of paving tops (edges used by one top triangle; ground material or tag `paved`; 48+ wide, ≤ 96 tall, 70 % of the top within 48 of the terrain, not the terrain's own surface): every 32 units, 12 outside, terrain under the top by 7+ and no other mesh over the terrain there; 64+ units per mesh |
| `visual_bare_clip` | vertical sides of clip meshes every 32 units; ground 24 units outside = terrain or paving/floor faces (roofs, facades, plaster, wall tops excluded); the clip spans ground + 48; no steep visible face or prop within 28 units between ground + 8 and + 96; 64+ units per clip; play area only |
| `visual_fence_no_collision` | render-only meshes of fence-like materials, 32–200 tall, ≤ 24 thick, ≥ 64 long, with no steep solid face or clip within 8 units |
| `visual_paved_lip` | two ground meshes (by face material) meeting with a step: 1–4 units where one overlaps the other and the lower comes out from under its edge, 1–2 units side by side (planes compared on the shared cell edge); rasterised on an 8-unit grid; one finding per mesh pair; terrain, buildings, roofs, background and small meshes skipped |

Sky, water, decals, triggers, precipitation and non-collidable meshes take no part in support and intrusion tests. The same penetration test is used by `dress_street` and the kit placement.

## Pitfalls we hit

- Paving laid as render-only slabs above the floor made a "phantom floor" (players' feet sank, bullets passed): now an error, and paving writes the material to the floor itself.
- Wall checks by nearest box called a window hole a backing; the world-text check now traces along the text normal.

## Limits

Box-based heuristics; Hammer frames, `resourcecompiler` and in-game frames remain the acceptance.

## Tests

`tests/test_visual.py`, `test_visual_penetration.py`, `test_visual_polish.py`, `test_visual_scene.py`. On a 830 m town (04.11) the new checks add ~25 s; their first runs there and on `p7_quartiere` tuned the thresholds (1-unit lamp gaps, round gutters, boundary clips over roofs, `dirt` inside `dirty` facade materials, boxes on tables).
