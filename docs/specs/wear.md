# Facade wear and decals

`add_wear` (registered by `ext/wear.py`, code `wear.py`) lays dirt, leaks, cracks, plaster loss, posters and ground stains as decals on facades and paving. Input: `cs2map_facade` schema 2, `cs2map_paving` schema 1 and the real meshes and models.

```json
{"op":"add_wear","name":"quarter","rows":"all","style":"inferno","density":1.0,"seed":7,"ground":true}
```

`name`: 1..59 ASCII letters, digits, `_` or `-`; `rows`: `all` or a list of row names; `density` 0..2; `seed` a signed 64-bit integer (strings, fractions and bools refused); `ground` bool. Unknown fields, rows or styles, broken metadata and a repeated ref or name are refused.

## Placement and protection

A house is found by `houses[].id`. Only front faces with the row's normal within 0..10 units on the street side of the nominal plane are used; the back of a niche is never accepted. The nearest plane at the candidate centre is chosen and the union of its triangles is subtracted from the decal rectangle: what is left must be below 0.00001 sq units (duplicate triangles do not hide holes). Front projections are excluded too: a decal never crosses a plinth, belt or step, and house bounds, ground and eave limit the whole rectangle, not just its centre.

Nearby props are protected by world boxes from measured bounds (scale and rotation included) with a 2-unit margin: frames, glass, shutters, doors, shop fronts, signs, awnings, pipes. A failed measurement refuses before the map changes. Wide openings split the dirt strip by `doors`/`shops`.

- **Dirt**: irregular segments at the foot of each reachable plinth, split by openings and low props; smaller segments of the same aspect are tried when a big one does not fit.
- **Leaks**: under real windows and sills (up to +300) and at downpipe joints; shortened when a belt or plinth is in the way; a six-quad overlay uses vertex alpha to fade sides and bottom. **Eave grime**: a leak strip under the cornice.
- **Cracks, brick chips**: lower corners and the band above measured doors. **Plaster loss**: Inferno damaged-plaster strips above the plinth near corners, searched inward from the corner.
- **Posters, rare graffiti**: centre 65..105 above `z0` on free plaster; skipped in open arcades.
- **Ground**: elongated uneven stains near wall corners, within about 48 units of the wall, wholly inside the rooms and sidewalks listed in paving, only on horizontal solid faces at `z0−8..z0+18` (no roofs, tool helpers or missing floors; never across a step). Without paving the ground layer is off and the answer says so.

The candidate sequence comes from SHA-256 of the seed, row name and house interval: row order and `rows` do not change a house's picks; no global random. Density thins the maximum set and pattern choice has its own RNG, so a denser layer keeps existing positions. Protection can lower the count.

## Materials and group

`data/wear/inferno.json`: roles with materials, world sizes, jitter, densities, alpha, orientation and optional UV rectangles. Of 32 stock candidates the opaque plaster overlays and round stamps were dropped; taken: `infer_dirt`, a rust UV strip, `concrete_crack`, a transparent brick chip, Italy posters, Venice graffiti, stains, `infer_plaster_damaged*` and `inferno_trim_decal_leakage_01` (the most frequent decal in the real Inferno, 85 times). Text is never mirrored; leaks point down.

Decals are `structure.add_decal` overlays (offset 0.08, `projectionFar` 1.0) with a confirmed receiving plane behind the whole quad, children of `wear:<name>`; `cs2map_wear` (schema 1) keeps seed, density, rows, roles and ground. Default ref `wear_<name>`. Even density 0 makes an empty group. `delete @wear_<name>` removes the layer (unreachable source elements are dropped by `dmx.Document.source_reachable`); an exception during generation removes the unfinished layer.

## Limits

After moving or rebuilding facades, delete and replay the layer. Axis-aligned facades and horizontal paving only; sloped floors are skipped. Geometry, collision and nav do not change. Large-scale vertex blending on facades is not implemented (terrain has it: `terrain.md`). `visual_check` does not measure overlay coverage, so the wear tests and frames are required.

## Tests

`tests/test_wear.py`, `tests/test_wear_safety.py` (holes inside a quad, duplicate faces, corners/belts/signs, arcade back walls, roofs and steps, types, row order and subsets, seed/density, refs and layer removal with orphan elements, delete and replay in one batch, UV/alpha streams, unknown bounds). In game `w7_street` and the phase 7 quarter, near and far frames.
