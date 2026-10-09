# Prop atlas: see stock models before you place them

| Task | Take |
| --- | --- |
| Find a stock prop with its size and notes | `asset_search(query, kind="prop")` |
| Look at it before placing | `prop_preview(models=[...])` |
| Record what you saw for later | `prop_note` |
| Place it | `map_edit` `add_model` |

Stock CS2 has about 12 000 models. Their names say little about their look, size or front, so never place a stock model you have not seen. Each step below is cheap; do them in this order.

1. `asset_search(query, kind="prop", limit=12)`: one line per model, `path | XxYxZ | front, mount | category/style: tags — note`. Size is in inches in model axes (72 = player height). `not described` means nobody has looked at that model yet. Search words match the file name, the path, the material names and the notes.
2. `prop_preview(models=[...])`: up to 16 models on one labelled sheet. Open the sheet image. A model takes about 1 s the first time and is free from the cache after that. The red arrow on the floor is the model's +X (yaw 0); the blue box is the player (32×32×72), left out for parts under 24 units so they stay visible (read their size in the label). The view looks from +X/−Y. `detail=true` adds the opposite view (from −X/+Y) for up to 6 models: use it when you need the front or the back.
3. `prop_note(model, category, tags, style, front, mount, by, note)`: record what you saw, so the next search shows it in one line. `front` is the local axis the model's face looks along (`+x`, `-x`, `+y`, `-y`, or `none` for round or symmetric models). `by` is your model name. A front other than `none` is accepted only after you looked at the model with `detail=true`. Notes go into your personal notes; the integrator merges good ones into the shared catalog in git.
4. Place it with `add_model` (`yaw = yaw(D) − yaw(front)`, see "Axes and facing") or with `add_kit_part` for kit parts.

`status: "broken"` means the offline preview failed: empty, invisible, missing textures or failed export. **Only then** may you call `prop_preview(models=[...broken], game=true)`, at most 6 models. It builds a gallery map and takes frames in CS2, holds the game lock for minutes and competes with other agents. If it answers `pending: true`, repeat the identical call. A healthy offline preview is never sent to the game. The player judges lighting, glass and foliage in the finished map.

Kit fronts were recorded before the atlas existed and are being re-checked against detail previews. If a kit front and the preview disagree, trust the preview and tell the integrator.

## By hand

Without the atlas: `asset_search(kind="model", query=...)` lists model files; `asset_search(kind="prop")` adds measured sizes and notes; place with `add_model` and check the front on a frame from above (yaw 0 looks along +X). Save what you learned about a model with `prop_note`, so the next search knows its front and mount.
