---
description: Prepare a finished map for the Steam Workshop
arguments: map | Map name
---
Prepare the map `{map}` for the Steam Workshop with the cs2map tools. Read `read_guide(section="publish")` first.

1. `check_map("{map}")` and `visual_check` must be clean or every remaining finding explained. If the map is built from packages, `map_edit(name="{map}", packages=..., dry_run=true)` must say it equals a clean replay.
2. Decide bots with me: on a big open map they cost hours of compute (section `nav`).
3. `map_build("{map}", "release")`, follow it with `job_status`; then `game_screenshot(map="{map}/{map}")` of the released map: spawns, the radar, the main places.
4. Tell me the upload size and the steps to upload it in the Workshop Manager, and prepare a title, a description and a cover frame.
