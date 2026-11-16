---
description: Fix what testers marked in the game with the F8 key
arguments: map | Map name
---
Testers marked problems on the map `{map}` in CS2 (F8, then a note). Fix every mark with the cs2map tools.

1. `game_screenshot(map="{map}", views="marks")`: open every frame, read its position, view and note. Group marks that are the same problem.
2. For each group, find the cause in what builds the map (the generator in `my/maps/{map}/` or the batch that made it), not only at the marked spot: fix the cause for every place it occurs. Ask me only when a note can mean two different fixes.
3. Rebuild only the marked zones: `map_build("{map}", "fast", region="marks")`, then frames from each mark's own position and view, before and after side by side.
4. Run `visual_check` on the changed zones, then a `full` build when looks changed.
5. Report per mark: what it was, what you changed, the before and after frames. Marks you could not fix: why.
