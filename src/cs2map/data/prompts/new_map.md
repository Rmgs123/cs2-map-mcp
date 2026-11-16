---
description: Build a new CS2 map from an idea, checked in the game step by step
arguments: idea | What the map is: kind, place, mood, size, gameplay (one or two sentences)
---
Build a CS2 map with the cs2map tools: {idea}

Work like a level designer who checks everything in the real game.

1. Call `status`. If something is missing for map work, say what and stop.
2. Read the core guide (`read_guide`), your notes from earlier chats (`read_guide(section="my")`) and the guide sections of the features you will use (`recipes` lists whole pipelines that worked; `pitfalls` lists what real maps taught).
3. Before building, write a short plan: layout with sizes in units (player 32×32×72, eye 64), the look (materials, light, a reference map if there is one), gameplay and tests. Ask me only what changes the plan; take sensible defaults for the rest.
4. Build in batches with `map_edit`, check each batch with `check_map`, build with `map_build` (`fast` while shaping) and look at frames with `game_screenshot`: from above, from the side, at eye height and from outside the play area. Open every frame you take.
5. Quality bar: no overlapping or floating objects unless intended, no faces of different objects in one plane, text and stains as decals, furniture by its real footprint, nothing that looks unfinished from any side players can see. Run `visual_check` before you call a part done.
6. Mechanics are tested with real input: `game_reset`, `script_test`, `route_test` or `playtest`, `game_reset` again.
7. Finish with a `full` build, final frames and a short report: what was built, what was tested and how, what is left. Save new lessons with `my_notes`; if a tool failed without your fault, write `my_notes(kind="feedback")`.
