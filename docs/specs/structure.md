# Map structure: groups, prefabs, decals, signs and world text

Groups that move as one, reusable prefabs, projected decals, signs and live world text. Code: `structure.py`, `ext/structure.py`; tools `prefab_save`, `prefab_info`, `decal_create`; operations `group`, `ungroup`, `add_prefab`, `add_decal`, `add_sign`, `add_world_text`.

## What Valve's maps contain

Checked in the `.vmap` files shipped with CS2 (`content/csgo/maps/editor`, example addons), binary DMX 9:

- `CMapGroup`: a normal node (`nodeID`, `referenceID`, `children`, `origin`, `angles`, `scales`, `transformLocked`, `force_hidden`, `editorOnly`; in Valve's maps also `customVisGroup`, `randomSeed`, `deformationMode`, `transformPin`).
- `CMapInstance`: empty `children`, a transform and `target` (an element, usually a `CMapGroup`): `cs_script_demo.vmap` places one group three times this way. There is no separate prefab file path attribute.
- `CMapStaticOverlay`: a projected decal with `meshData` (`CDmePolygonMesh`), `projectionTargets`, `projectionFar`, `projectOnBackFaces`, `backFacingAngle`, `projectionMode`, `renderOrder`, `disabledInLowQuality` (e.g. one quad with a Dust sign material).
- `CMapSmartProp` is a procedural node with its own format: not a prefab, not supported here. `CMapPrefab` did not appear in any shipped map.

## How it works

1. `group` takes direct children of one parent, centres a `CMapGroup` on their bounds and moves them under it; `ungroup` puts them back.
2. `prefab_save` clones the chosen nodes with new ids, centres them and writes a separate binary VMAP `maps/prefabs/e_<name>.vmap` (`isprefab = true`, one root group); prefab files get their own backups.
3. `add_prefab` clones that group into the map and adds a local `CMapInstance` targeting it (`inline: true` keeps only the placed group); only translation for now.
4. `add_decal` builds a quad at `origin` facing `normal` as a `CMapStaticOverlay` (UV, normal, tangent through the same `Mesh.to_dmx` as geometry); `size` in world units, `projectionFar` limits the depth; no collision, no nav.
5. `add_sign` reads the `decal_create(text=...)` metadata and builds a planar mesh with 0..1 UV (plate and neon get a six-faceted frame; stencil reuses `add_decal`); `add_world_text` writes the FGD keys of `point_worldtext` (`message`, `font_size`, `font_name`, `color`, `world_units_per_pixel`, `justify_*`, `enabled`, `fullbright`); `SetMessage` changes the text at run time.

No change to the codec was needed: the core's move, clone, delete and material operations already walk children recursively. A prefab open in Hammer is never overwritten; ungrouping an instance's target group and prefabs referencing outside nodes are refused.

## Tests

`tests/test_structure.py`, `tests/test_structure_b3.py`; in game `b_kinds` (Cyrillic plate, paper and stencil signs, a keyed poster, live text before/after `SetMessage`), `e_prefabs` (build without errors).
