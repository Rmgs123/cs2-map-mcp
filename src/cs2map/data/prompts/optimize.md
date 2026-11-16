---
description: Find what a map draws for nothing and remove it without any visible change
arguments: map | Map name
---
Make the map `{map}` cheaper to draw without changing what players see. Read `read_guide(section="performance")` first.

1. `map_info(name="{map}", about="cost")`: report the triangles, the heaviest props and meshes, the busiest zones and the objects nobody sees. Tell me the numbers and what you propose before changing anything.
2. Pick the views for the proof: the best-looking places, every tester mark (`game_screenshot(map="{map}", views="marks")`) and views from the edge of the play area towards the removed objects. Take each view twice before the change.
3. Change what builds the map (its generator or batch), never the map by hand; rebuild; take the same views after.
4. `reference_compare(pairs=[[before_1, before_2], [before_1, after], ...])`: a change passes only when it is no bigger than the noise of the unchanged view and lies in foliage. Put back whatever fails and say why.
5. Report: triangles before and after, what was removed or lightened, the proof per view.
