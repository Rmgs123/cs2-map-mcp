# Facade wear and decals

`add_wear` is **quick fill in the Inferno look** (its overlays are Inferno decals). For another look place decals by hand: `asset_search(kind="decal")`, own `decal_create`, `add_decal` (section `styles`).

| Task | Take |
| --- | --- |
| Dirt, drips, leaks, broken plaster, posters on facades | `add_wear` |
| Find wear overlays | `asset_search(kind="wear")` |

`add_wear` adds deterministic projected `CMapStaticOverlay` decals after
`add_facade` (metadata schema 2) and `add_paving` (schema 1).

```json
{"op":"add_wear","name":"quarter","rows":"all","style":"inferno",
 "density":1.0,"seed":7,"ground":true}
```

Use it as a `map_edit` operation after the facade rows exist. `rows` is `"all"`
or a non-empty list of facade row names. `density` is `0..2`; `0` creates an
empty, removable group. `name` is 1..59 ASCII letters, digits, `_` or `-`.
`seed` is a signed 64-bit integer. `ground` controls marks on paving near a
wall; without paving metadata, ground marks are skipped (`ground:false` in
the result). The result reports group `id`, persistent `ref`, decal count,
selected row count and counts by role:
`base_dirt`, `leak`, `eave_grime`, `crack`, `plaster_loss`, `poster`,
`graffiti`, `ground_stain`, and `ground_dirt`.

The group is named `wear:<name>` and receives a persistent ref
`wear_<name>` (hyphens become underscores), or an explicit `ref` of 1..64
letters, digits or underscores. Duplicate names/refs are refused before
placement. Remove every generated overlay in one edit:

```json
{"op":"delete","id":"@wear_quarter"}
```

The entire decal quad must be covered by the union of receiving triangles on
one plane. Holes, corners and plinth/step transitions are rejected. Measured
rotated/scaled model bounds protect windows, doors, shops, signs, awnings and
pipes. Missing model bounds refuse the operation. Leaks (de_inferno's own
leakage decal) start below measured sills or beside downpipes; a dark drip band
runs under the cornice (`eave_grime`); dirty plinth segments split at entrances;
broken plaster with brick showing (`plaster_loss`, Inferno's strips) sits just
above the plinth near corners; cracks favour lower corners and lintels. Posters/graffiti stay
65..105 units above the sidewalk, and open arcades omit them.

Ground marks stay at wall corners within 48 units of the wall centre,
inside paved room/sidewalk footprints. Only solid horizontal surfaces between
`z0-8` and `z0+18` can receive them; roofs and absent floors cannot. Every
overlay uses offset 0.08 and projection depth 1, adds no collision/nav geometry,
and is skipped if no safe placement exists. Counts are not density targets.

Material paths, world sizes, opacity, orientation and the selected atlas UV
strip are in `src/cs2map/data/wear/inferno.json`. Vertex alpha softens the sides
and bottom of the streaks. Wall text stays upright and
unmirrored; grime/ground patches vary. Seed is stable across row order and
selection. Delete/replay after moving or rebuilding the source rows.

`asset_search("leak", kind="wear")` searches stock and addon materials.
Use `visual_check` and a `full` build before treating the result as accepted in
the game. The operation needs at least one valid facade row. Checked in CS2 on
`w7_street` and on the acceptance map `p7_quartiere` (40 rows, ~370 decals).

## By hand

`add_decal` with a stock decal material (`asset_search(kind="decal")` or `kind="wear"`; Inferno: `infer_dirt*`, `infer_plaster_damaged*`, `inferno_trim_decal_leakage_01`) at a wall point, `normal` out of the wall, `size` in units; own pictures through `decal_create`. One decal per plane: never across corners, windows, doors or trims; leaks hang down from sills and eaves, dirt sits on plinths.
