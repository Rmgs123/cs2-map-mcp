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
| `visual_wall_gap` | A wall part (kit/atlas `mount: wall`, an AC unit, a `wall_mounted` model): five rays from each side of its oriented box find the wall behind it; its back stands over 1.5 units off the wall, is sunk over 4 units into it, one side stands 4+ units off (turned), or some rays find no wall (it hangs past the wall's end). | Move it along its back axis onto the wall (gap ≤ 1), turn it parallel to the wall, or move it where the wall continues. |
| `visual_unit_over_window` | Such a wall part covers a window pane (a vertical face whose material names glass or window) of the same wall by 6×6 units or more. | Move it beside or under the window. |
| `visual_cable_in_air` | An `add_cables` end has no wall, pole, pipe, mesh or model within 6 units (cables meeting in the air count too). | Move the end onto a wall, pole or insulator; add a pole where cables meet. |
| `visual_door_over_window` | A door panel (a face whose material names a door, 60–130 tall, 20–110 wide) lies over a window pane of the same facade within 16 units, overlapping 0.03 m²+. A pane inside the door's own outline and plane (a glazed door) is fine. | Move the door to a plain stretch of wall or remove the window behind it. |
| `visual_prop_floats_on_slope` | A static prop's base hangs over the ground under its footprint (8 units, plants 16) on a terrain map. The ground is the terrain, a mesh top or a non-plant prop no more than 8 units above the base (a box on a table, grass on a bank mesh); on a structure, corners over a 64+ unit ledge are overhangs. | Lower it, or use scatter. |
| `visual_paved_slab` | The free edge of a paving mesh (ground material or add_mesh `tags: ["paved"]`, 48+ units wide, at most 96 tall, lying on the terrain) stands 7+ units over bare terrain 12 units outside, over 64+ units of length: its side shows as a dark band. Edges continued by another mesh (a kerb, a road, a wall) are not counted. | Raise the terrain along the edge to 2–4 units under the top or lower the paving; a kerb or a chamfer mesh also hides it. |
| `visual_bare_clip` | A player clip (`toolsplayerclip`/`toolsclip`) stops players standing on paving, a floor or the terrain beside it (not roofs or wall tops) with no steep visible face or model within 28 units, over 64+ units: an invisible wall. Outside the play area it is not reported. | Put a visible barrier there (`add_fence`, a wall, a railing) or move the clip onto one. |
| `visual_fence_no_collision` | A render-only fence-like mesh (fence, railing, grate, wire, picket... material; 32–200 tall, thin, 64+ long) in the play area has no solid face or clip along it: players walk through it. | Give it collision or a clip box along it; `add_fence` does both. |
| `visual_worldtext_unbacked` | No visible triangle behind the text plane within 32 units. | Add a backing or correct the angle; rotating story hints are excluded. |
| `visual_phantom_floor` (error) | A render-only (no collision) floor in a playable room lies 2–64 units above the solid floor: players walk and shoot through it. Outside the play area (`set_play_area`) it is background and not reported. Faces narrower than 24 units seen from above (sills, cornices, frames, curtains, cables — also when turned) are skipped. | Pave the floor itself (`add_paving`, `set_material`) or make the slab solid. |
| `visual_phantom_wall` (error) | A render-only wall at player height in a room with no collision within 8 units behind it. | Make it solid or put it on a real wall. |
| `visual_paved_lip` | Two ground meshes (asphalt, concrete, tiles, cobbles, gravel, dirt... by face material) meet with a step: one lies 1–4 units over the other and the lower one comes out from under its edge, or they meet side by side 1–2 units apart. Seen in frames only as a thin dark line. Terrain, buildings, roofs, background, render-only and small meshes (under ~120×120 units or 48 wide) are left out; 4+ unit steps over another mesh and 2+ side by side are kerbs. | Make the joint flush, or where they overlap lay the joining surface 1 unit under the other's top and blend it back within a few metres (`add_street`/`add_junction` do this themselves). |
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
