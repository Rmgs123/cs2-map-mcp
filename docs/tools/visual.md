# Visual authoring checks

| Task | Take |
| --- | --- |
| Floating, buried, flickering, unmounted things | `visual_check` |
| A visible button body, a control button, an awning | `add_button_visual`, `add_control_button`, `add_cloth_awning` |

Use `visual_check(name)` after a `map_edit` batch and before a build. It audits
the authored VMAP in seconds; it does not load the map or replace a real CS2
capture. The response is `{map, ok, problems, summary}`. Each problem has
`severity`, `code`, `id`, `message`, and a numeric `fix`. `problems` lists the
first `limit` (default 200, errors first); `summary.by_code` counts every
problem, so a town of thousands of meshes answers in a few kilobytes. A map
with ~4000 meshes and a 257×291 terrain is checked in about 30 seconds.

```json
{"name":"q_f2_orig"}
```

The check reads render bounds from installed `vmdl_c` resources (`MDAT`, then
`DATA`, then `PHYS`) and applies the node `origin`, `angles` (pitch/yaw/roll in
degrees), and `scales`. A model whose bounds cannot be measured is skipped;
the checker never invents a size. Own MCP models may use their `model.json`
bounds when the native utility is unavailable.

Codes:

| Code | Meaning | Fix |
| --- | --- | --- |
| `visual_floating_prop` | Model bottom is over 16 units from every floor, wall, ceiling, or prop. | Move the origin or add a support. |
| `visual_floating_platform` | Large concrete floor high above the scene has no visible support below. | Add columns/walls/beams or lower it. |
| `visual_prop_intersection` | Model cuts into a wall (real triangles, beyond 12 units), or is buried in a floor or terrain deeper than max(12, 40% of its height). Props resting on slopes and rocks set into the ground are fine. | Move it out of the wall or up onto the surface. |
| `visual_prop_overlap` | One prop is nested in another. | Separate the AABBs or remove the duplicate. |
| `visual_layout_obstruction` | A large prop occupies a layout room's centre route. | Keep a 96-unit path or move it to a wall. |
| `visual_lamp_unmounted` | Light is over 32 units from geometry and has no nearby prop. | Mount it to a wall/ceiling or add a fixture. |
| `visual_open_mesh` | Collidable mesh has an open boundary edge. | Close it, or make intentional scenery non-solid. |
| `visual_z_fighting` | Faces of two meshes with different materials lie in one plane (gap ≤ 0.25), face the same way and overlap. Near-coplanar faces (gap > 0) flicker; exactly coplanar ones hide each other. Up to 20, worst first. | Join edge to edge, sink one 1+ unit, or delete the duplicate; labels and stains are decals or `decal_create(text=...)` signs. |
| `visual_flat_model_on_surface` | A model at most 4 units thin (glass pane, shutter, door panel) lies within 0.3 of a parallel wall or niche face that faces it: its visible side is in that plane and flickers (white curtains in dark window niches). Floor covers and drains are skipped (their back faces down). | Move it 1+ unit off the surface: deepen the niche or offset the model. |
| `visual_wall_part_off_wall` | A part whose kit or atlas `mount` is `wall` has no wall within 16 units (a sign or shutter left on the floor or in the air). | Put it on a wall with a layout wall point or `facing`. |
| `visual_worldtext_unbacked` | No visible triangle behind the text plane within 32 units. | Add a backing or correct the angle; rotating story hints are excluded. |
| `visual_phantom_floor` (error) | A render-only (no collision) floor in a playable room lies 2–64 units above the solid floor: players walk and shoot through it. Faces narrower than 24 units seen from above (sills, cornices, frames, curtains, cables — also when turned) are skipped. | Pave the floor itself (`add_paving`, `set_material`) or make the slab solid. |
| `visual_phantom_wall` (error) | A render-only wall at player height in a room with no collision within 8 units behind it. | Make it solid or put it on a real wall. |
| `visual_exposed_foundation` | Empty underside of a ground slab in an outdoor layout is visible from a gameplay point. | Add a ground mass/skirt, or water and supports for a pier. |

Water, sky, precipitation, decals, light-shaft helpers, explicitly marked
outdoor backdrop groups, and props parked far below the scene (switchable light
carriers) are excluded. Warnings are advisory;
`ok` becomes false only for an error produced by an area check. Finish the
acceptance with `check_map`, a `resourcecompiler` build, and opened
`game_screenshot` frames.

Polish operations (world units):

```json
{"op":"add_button_visual","button":"my_lift_lower","face":"-x","scale":0.5}
{"op":"add_control_button","targetname":"panel","mins":[0,0,44],"maxs":[12,16,60],"targets":[{"name":"door","input":"Open"}],"visual":{"face":"-x"}}
{"op":"add_cloth_awning","bounds":[[1376,48,0],[1520,128,176]],"style":"italy","posts":true}
```

`add_button_visual` retains an existing `func_button` and its I/O, hides the
brush and adds a parented Nuke control model (`solid=0`). `button` is an id,
targetname or `@ref`; `face=-x|+x|-y|+y` points out from its mount. Optional
`model` must have measured bounds, `scale=.5` (.01..4). It refuses duplicate
visuals. `add_control_button` combines the normal button recipe and this
visual; `visual` accepts those same face/model/scale fields. `add_lift` and
`add_button` defaults await core integration.

`add_cloth_awning`: `bounds` footprint and ground/top z; `style=italy|dust`,
`posts=true`, `post_width=6` (2..16), optional installed post `material`.
Measured stock fabric fits XY, keeps its original vertical sag, and leaves
72 units of headroom. Posts have collision; the canopy is non-solid.

## By hand

What the check looks for, you can see yourself: `game_screenshot` from above, from the side and from outside the play area; `asset_search(kind="prop")` for a stock model's size; `map_info(about="geometry", key=<mesh>)` for a mesh's faces and closure. Fix by moving (`move`), sinking 1 unit, joining edge to edge, or replacing thin boxes by decals.
