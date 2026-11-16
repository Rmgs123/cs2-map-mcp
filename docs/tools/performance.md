# Performance: what a map costs, and what nobody sees

| Task | Take |
| --- | --- |
| Triangles by model, mesh and zone; objects nobody can see | `map_info(name, about="cost")` |
| Prove a removal changed nothing in view | the same `game_screenshot` views before and after, `reference_compare(pairs=...)` → `diffs` |
| Find the views | reference views of the best places, testers' marks (`views="marks"`), `views="auto"` |

Offer this pass to the map's owner when a map is big or open (a town, a valley): open maps have no visibility culling, so a house behind a hill is drawn every frame. Numbers first, then changes, then proof. Never trade looks for speed without asking.

## The report: `map_info(name, about="cost")`

Offline and deterministic (frame times in the game depend on everything else running on the PC):

- `triangles`: drawn faces of meshes, triangles of stock props from the shipped facts (LOD0, the worst case), and how many props have no measured size (own models).
- `heaviest_props`: model, count, triangles each and in all. A town map: one bush model, 149 copies × 7 500 triangles = 1.1 million, more than all its meshes. Fewer copies or a lighter model away from where players stand is the cheapest win.
- `heaviest_meshes`, `zones` (squares of 2048 units with the most triangles).
- `never_seen`: objects outside the places players reach (and outside `set_play_area`) that no eye sees. Eyes stand on the floor players reach from the spawns, 64 units up, one per 384-unit square. Occluders are a height map of opaque faces (glass, water, fences, foliage, decals hide nothing), each cell a solid column up to its highest face, so something seen only under an arcade or a canopy can be listed. A town map of 24 500 nodes: about 2 000 candidates, 140 000 triangles, the report in about 110 s. The map needs spawns.

## Remove, then prove

1. Change what builds the map (its generator or batch): delete the never-seen objects, use fewer or lighter heavy props far from players, give far meshes a draw distance (mesh `options` `fademaxdist`, `add_district` `fade`).
2. Build, then take the same views as before the change (same `pos`, `ang`, light).
3. `reference_compare(pairs=[[before_1, before_2], [before_1, after]])`: the first pair is the noise of an unchanged view, the second the change. Each pair of equal-size frames gets `diffs`: `changed` (share of pixels where a colour moved more than 24 of 255), `mean`, and `box` (where). Measured in CS2: two frames of one unchanged view with trees differ in 3.9 % of pixels, all in leaves and their shadows (wind); everything else is equal. A removal is invisible when the second pair is no bigger than the first and its box lies in foliage. Anything else: open both frames and look.
4. Keep what failed the proof and say why.

## By hand

Without the report: count props per model with `map_info(kind="entity")`, look at the map from above (`game_screenshot(window="offline")`) and from the play area's edge, and delete only what the frames prove unseen.

Not done yet: a frame-time probe in the game and distance LODs for stock models (section `recipes`).
