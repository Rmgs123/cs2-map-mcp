# Buildings: free-standing houses from measured kits

`add_building` builds one closed, measured house in a single `map_edit` operation: walls with real openings, kit details (windows, doors, balconies, awnings, lamps), floor, ceiling and a closed roof. `reference_facade` turns a facade photo into a starting suggestion; `reference_compare` puts a photo next to a game frame. Code: `buildings.py`, `reference.py`, `ext/buildings.py`, kits `data/kits/building_*.json`. Playable streets use `add_facade` instead (`docs/specs/quarter.md`).

## How it works

**Kits.** A building kit (`schema: 1`, `kit`, `style`, `materials`, 30+ parts) lists stock parts with bounds measured from their MDAT/PHYS (`visual.model_bounds`), `solid` = has physics, a candidate `front` axis and, for windows and doors, `opening` and `opening_bottom`. `verified: "bounds"` means measured only, never "seen in the game".

**The operation.**
1. Name, footprint, floors (1..6), floor height (112..256), materials, style, facades and roof are validated.
2. Every model and material is checked before the map changes. Each part gets the rotation from its `front` to the wall normal and an origin that puts its back on the facade.
3. Each wall is built as closed `region_solid` rectangles around the openings, so openings are real and never overlap; a closed window gets a dark inset plate behind it.
4. Groups `building:<name>` and `facade:<name>:<side>` hold walls, plinth and floor bands, kit parts, floor, ceiling and the roof. All five roof types (flat, parapet, gable, hip, shed) are closed meshes; only visual bands and signs are `physicsType: none`.
5. The root group keeps `cs2map_building` (footprint, facades, opening bounds) for later checks; the result lists groups, mesh and model ids and the model count (max 60).

**Photos.** `reference_facade` rectifies a four-corner quad (`textures.load` homography), profiles brightness and edges into a floor/window grid, crops a low-contrast wall patch for `material_from_reference` and always says `needs_visual_review: true`. `reference_compare` composites two local images at the same height, `pairs` stacks numbered photo | frame rows, and `plan` draws the map from above offline (`reference.reference_plan`: mesh triangles facing up, painted lowest first and shaded by height, layer prefabs included, grid every power of ten that gives about four lines, marks with yaw arrows, an optional picture blended under the box).

## Limits

Rectangular houses with cardinal walls; corner and overlapping openings are refused; a window must fit its floor. Groups are not proof: `full` build, `check_map`, `visual_check` and opened frames are.

## Extending

A new style is one `building_<style>.json` with the same contract. A new roof profile returns a watertight `Mesh` that passes `halfedges()`. Own looks without code: `docs/tools/styles.md`.

## Tests

`tests/test_buildings.py`, `tests/test_reference.py`; in game `h_street`.
