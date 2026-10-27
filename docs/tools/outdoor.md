# Outdoor water, scenery and supports

| Task | Take |
| --- | --- |
| Sea, lake, river to the horizon | `add_water_body` |
| Distant roofs, trees, towers | `add_backdrop` |
| Columns and piles under raised floors | `add_supports` |
| Sky | `style_info(kind="sky")` |

These `map_edit` recipes use CS2 units. `id`/`ref` addresses the whole `outdoor:<name>` group; `ids` lists meshes. Omit `name` for an automatic name. Delete the group to replace it. Backups and dirty-Hammer guards apply. Inspect a full build in CS2.

```json
{"op":"add_water_body","name":"sea","kind":"sea","bounds":{"mins":[480,-10000,-352],"maxs":[16000,10000,-96]},"water_level":-96,"depth":256,"shore_axis":"x","shore_side":-1}
{"op":"add_supports","room":"pier","layout":"port","kind":"piles","base_z":-352,"count_x":6,"count_y":3,"width":48}
{"op":"add_backdrop","style":"port","bounds":{"mins":[-64,-1300,-350],"maxs":[3100,1300,1400]},"sky":"sunset","height":1100,"segments":8}
```

`add_water_body`: `kind=sea|river|lake`, rectangular `bounds` (or `mins/maxs`); `water_level` defaults to maxs.z, `depth=256` (16..4096). `water_style=calm|coast|skybox` selects Nuke/Anubis/Anubis skybox water (see-through: from a hill or from afar only the bed shows); `far` is an opaque sea (own material `mcp_far_sea`, made once) for distant bays and a sea seen from above; `material` overrides it. `bed_material`/`shore_material` override concrete. A bed lies at level-depth. `shore_axis=x|y`, `shore_side=-1|1`, `shore_width=48` (4..512); omit axis for four banks. Water, bed and shore are render-only, without collision/nav. Add playable shores, supports and hazards separately; size bounds to the visible horizon.

`add_backdrop`, legacy `style=port|industrial|rocky|city`: enclosing `bounds`; `height` (64..4096), `depth=192` (32..1024), `segments=6` per edge (2..16), `material`, `sides=["-x","+x","-y","+y"]`, `clip=true`. Default `silhouettes=stock` uses measured warehouses/cranes, silos, rocks/trees or hotels; `silhouettes=mesh` keeps legacy shapes. `models` replaces stock placements with up to 64 `{model,origin,angles?,scale?}` entries, measured outside XY bounds and within +/-32768. Set bounds.min.z to visible ground. Props have `solid=0`; `model_ids`/`model_count` identify them. Clips enclose selected edges vertically. Optional `sky`/`skybox` changes the sky; omitting both preserves it. Apply `lighting_preset` before choosing sky.

`style=italy` adds three rows of tiled houses, cypress groups and an Italy bell tower:
```json
{"op":"add_backdrop","name":"k7_skyline","style":"italy","layout":"port","seed":76,"ring":[320,2400],"density":6,"trees":2,"ref":"skyline"}
```
It encloses layout rooms AND schema-2 facade depths/eaves; optional `bounds` enlarges this envelope. With several layouts, choose `layout` or supply bounds. Integer `seed`; `ring=[near,far]` (or `ring_min/max`) defaults to 320/2400: near 300..2500, far 600..4096, gap >=300. `density=6` (1..12) is the near-row target per side, reduced for short edges; outer rows are smaller/sparser. `trees=2` (0..3) places cypresses near houses. `height=1400` (640..1600), `bell_tower=true`, `tower_side` (default +x if selected, else first side), `sides` and `clip` control composition. Right behind the quarter runs a continuous ring of attached houses of varied height (from the roofs and from above it reads as town, not grass); further rows stand behind it. Houses are closed render-only meshes by default (`houses:"mesh"`): tinted Inferno plaster, hip tile roof, stone plinth, dark windows, footprint and height of the measured stock background house. The stock cards (`houses:"models"`) are thin shells with see-through windows that break when seen from above or from the side. Model bottoms touch render-only earth; `ground_z` defaults to lowest floor minus 1 and cannot exceed it. `ground_material` overrides earth. `ground:false`, explicit `models`, mesh silhouettes, `material` and `depth` are rejected for Italy. Short response: group/mesh ids, bounds, seed and role counts; placements stay in metadata. One `delete` removes all rows, earth and clips.

`add_supports`: `room`/optional `layout`, or `bounds`. Room top contacts floor_z minus slab thickness; bounds top defaults to maxs.z. `base_z` defaults to min(mins.z,0), optional `top_z`; `kind=columns|piles|truss`, `theme=industrial`, `material`, `width=32`, `depth=width` (4..128), `count_x/count_y=2` (1..12). Posts/girders collide; truss braces are decorative.

`style_info(kind="sky", name?)`: stock `day|sunset|night|overcast|fog` materials. Sky mood and atmospheric haze are separate; use gradient fog in `lighting_preset`. Volumetric scattering remains experimental; see `quality.md`.

## By hand

Water: a flat non-solid mesh (`physicsType none`) with a stock water material (`asset_search(kind="material", query="water")`) below the walk floor. Backdrops: non-solid meshes and `prop_static` with `fademaxdist` outside the play area, behind `materials/tools/toolsplayerclip.vmat` walls. Supports: boxes or `extrude` posts from the underside down to the ground. Sky and fog: section `light`.
