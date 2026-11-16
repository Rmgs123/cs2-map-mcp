---
description: Make an existing map look finished and cosy, with before and after frames
arguments: map | Map name ; reference | Optional: an official map or photos to match (e.g. de_inferno)
---
Make the map `{map}` look like a finished map by Valve. Reference: {reference}.

1. Frames of the current state: `game_screenshot(map="{map}", views="auto")` plus eye-height views of the main places. If a reference map is named, take the same kind of frames of it (`game_screenshot(map="<reference>")`).
2. Name the three biggest differences in look (depth of fronts, wear, light, clutter, materials, sky, bare spots) and fix them in order; then the next three. Use primitives and own models where the quick builders have no part (`read_guide(section="styles")`, `pitfalls`).
3. Keep the scene clean: `visual_check` after every round, no floating or overlapping objects, decals for text and stains.
4. Never worse: compare the same views before and after every round; light changes need a `full` build and cubemaps.
5. Report with before and after frames side by side and what is still missing.
