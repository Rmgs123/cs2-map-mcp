# Map structure: groups, prefabs, decals

| Task | Take |
| --- | --- |
| Group nodes to move/copy them as one | `map_edit` `group` / `ungroup` |
| Reusable pieces | `prefab_save`, `prefab_info`, `add_prefab` |
| Decals and overlays | `asset_search(kind="decal")`, `add_decal` |
| Signs and runtime text | `decal_create(text=...)`, `add_sign`, `add_world_text` |

The structure extension adds reusable scene organization without changing the
core VMAP codec. All coordinates are Source 2 map units (1 unit is about one
inch); origins are world positions and angles are `[pitch, yaw, roll]` degrees.

## Groups

Groups are applied through `map_edit`:

```json
{"op":"group", "ids":[101,102], "name":"crate_corner", "ref":"corner"}
{"op":"ungroup", "id":"@corner"}
```

`group` requires non-empty node ids that are direct children of the same
parent. It creates a `CMapGroup`, moves those children under it, and records a
name, world-space bounds centre, transform pin, visibility group, random seed,
and deformation mode. `ungroup` puts the children back at the group's former
position in the parent's child list. The core `move`, `duplicate`, `delete`,
and `set_material` operations already recurse through a group subtree, so a
group can be edited as one unit. Add `ref` to a group operation to use its id
later in the same batch.

Use it through `map_edit` (`{"op":"group","ids":[...],"name":"..."}`; `ungroup` dissolves).

## Prefabs

Save a reusable selection with:

```json
prefab_save(
  map="e_structure_test",
  ids=[101, 102],
  name="crate_corner"
)
```

The result is `maps/prefabs/e_crate_corner.vmap` inside the configured addon.
The selected nodes must be direct children of one parent. Their bounding-box
centre becomes the prefab origin and all node origins are translated relative
to that centre. Saving an existing prefab keeps a backup under
`<backups>/maps/prefabs/`.

Place copies with `map_edit`:

```json
{"op":"add_prefab", "prefab":"crate_corner", "origin":[512,0,0],
 "angles":[0,90,0], "scale":1.0, "ref":"copy1"}
```

`prefab` accepts a simple name (`crate_corner`, `e_crate_corner.vmap`) or the
addon-relative path `maps/prefabs/e_crate_corner.vmap`. `origin` is the placed
prefab origin; `angles` rotates it; `scale` is a positive scalar or `[x,y,z]`
(up to 100). The default creates a Valve-style `CMapInstance` and its local
`CMapGroup` target. Set `inline=true` to place the cloned group directly when
a compiler or an older editor does not accept an instance target.

Inline placement currently supports translation only: `angles` must be zero
and `scale` must be 1. Other inline transforms are refused. Instance rotations
and scales are written to the observed Valve transform fields, but their
rendered placement is still awaiting visual acceptance. Instances contain a
copied local target graph; they do not retain a live link to the prefab file.

`prefab_info()` lists available files and `prefab_info(name)` reports the
`isprefab` flag, root group and node count. A prefab file is a VMAP asset, not
a model VPK; it must remain under `content/csgo_addons/<addon>/maps/prefabs/`.

## Decals and overlays

Projected decals are added as a `CMapStaticOverlay` through `map_edit`:

```json
{"op":"add_decal",
 "material":"materials/decals/sitea.vmat",
 "origin":[128,64,96], "normal":[0,0,1], "size":[96,64],
 "projection_far":32, "ref":"site_a"}
```

Give either `normal` or `angles`, never both. `normal` is normalized; with
`angles`, the local +Z axis is used as the projection normal. `size` is one
positive number (square) or `[width,height]`, in world units. A tiny normal
offset avoids z-fighting. Optional fields are `offset`, `projection_far`,
`project_on_back_faces`, `back_facing_angle`, `projection_mode`,
`render_order`, `disabled_in_low_quality`, `name`, and a
`material_adjustments` object matching Valve's
`MaterialAdjustmentParamsStruct` fields (`ColorBrightness`, `ColorContrast`,
`ColorAlpha`, `RoughnessBrightness`, `RoughnessContrast`, `ShadingAlpha`,
`NormalIntensity`, `RoughnessMetalnessOverride`, `NormalBlendOverride`).

The material must be an existing addon or stock `materials/...vmat`; use
`asset_search("graffiti", kind="decal")` to find
stock choices. Useful verified stock families include `materials/decals/*`,
`materials/overlays/*`, graffiti, stains, `materials/decals/sitea.vmat`, and
`materials/decals/siteb.vmat`.

The generated node uses the same attributes found in Valve maps:
`meshData` (`CDmePolygonMesh`), `projectionTargets`, `projectionFar`,
`projectOnBackFaces`, `backFacingAngle`, `projectionMode`, and optional
`MaterialAdjustmentParamsStruct`. The mesh is a single planar quad and carries
the selected material. This is a projected overlay rather than a point entity,
so it is visible to Hammer and is compiled with the map geometry.

The quad covers UV 0–1 once, regardless of texture resolution and placement.
Its normal is baked into the vertices; node angles stay zero. Verified in the
game and in Hammer on `e_prefabs` (site letters A/B on a floor and a wall).

## Limits and troubleshooting

- A group/prefab selection cannot include the world node or nodes from
  different parents.
- Ungrouping an instance's source target is refused; `ungroup` cannot define
  a `ref` because the group is removed. Prefab selections with map-node
  references outside their child hierarchy are refused.
- Prefab placement is local to the saved VMAP and does not copy entities from
  another addon. Keep source materials/models available to the destination
  addon or the compiler reports missing assets.
- Selection bounds use mesh vertices and node origins. Model extents and
  rotated/scaled mesh bounds are not yet included in the centre calculation.
- Decals do not create collision or nav geometry. `projection_far` must reach
  the receiving surface; use a small positive offset and avoid coplanar
  surfaces.
- The extension only writes the configured addon and project backup folder.
  Hammer must be closed or called with `force=true`; unsaved Hammer edits are
  still refused by the core safety check.
- `prefab_save` refuses a prefab open in Hammer, including unsaved edits.

## B3 signs, world text and custom overlays

`add_sign` is a `map_edit` operation for artwork made by `decal_create(text=...)`:

```json
{"op":"add_sign","name":"warehouse","origin":[320,299,217],
 "normal":[0,-1,0],"size":[130,32],"frame":2}
```

`normal` or `angles` selects the facing plane, never both. Plate and neon use a
framed planar mesh, paper uses a planar card, and stencil uses the projected
overlay path. `size` is world `[width,height]`; `frame` is world units.

`add_world_text` creates a `point_worldtext` entity:

```json
{"op":"add_world_text","text":"Open: 4","origin":[180,290,42],
 "angles":[0,0,90],"size":18,"color":"#55ffff",
 "targetname":"live_text"}
```

`size` is glyph height in world units. `SetMessage` can update the target at
runtime. In the verified B3 stand `[0,0,90]` faces -Y.

`decal_create` (materials guide) writes a keyed or alpha-preserving material
under `materials/mcp_decals`; place it with the existing `add_decal` operation.
The overlay is visual only and does not add collision or nav geometry.

## By hand

Groups are `CMapGroup` nodes (`group`/`ungroup`); a prefab is a separate VMAP placed by a `CMapInstance`. World text is `point_worldtext` (`message`, `font_size`, `font_name`, `world_units_per_pixel`, `color`, `justify_horizontal`, `justify_vertical`, `fullbright`), changed at run time with the input `SetMessage`. A decal is a projected overlay (`add_decal`); a sign is a planar mesh 0.5 unit in front of its plate.
