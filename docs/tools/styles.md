# Own styles: any look, not only Inferno

The quick builders (`add_paving`, `add_facade`, `add_building`, `add_wear`, `dress_street`, `add_backdrop style:"italy"`) fill streets fast **in the Inferno look**. Every other look — a nordic harbour, a desert fort, a concrete bunker, a neon city — is built from base commands, and you keep what you learned as an **own style** in the local layer `my/`, so the next chat starts where you stopped.

| Task | Take |
| --- | --- |
| Streets in the Inferno look, fast | the quick builders (sections `buildings`, `wear`, `streetlife`, `outdoor`) |
| Any other look | primitives (`geometry`), own materials (`materials`) and models (`models`), then save the style |
| Save, list, read an own style | `my_notes(action="write", kind="style", name, data)`, `style_info(kind="mine", name?)` |
| Use it in a batch | `"style":"my:<name>"` (`add_facade`, `add_building`, `add_paving`), `"preset":"my:<name>"` (`lighting_preset`), `"$<name>.materials.<role>"` in any field |
| Remember how you built it | `my_notes(kind="recipe", name, text, data=<batch>)` |

## What a style holds

```json
{"description": "Nordic harbour: dark tarred timber, white trim, slate roofs, grey sky",
 "based_on": "building_inferno",
 "materials": {"wall": ["materials/mcp/tarred_planks.vmat", "materials/mcp/red_planks.vmat"],
               "trim": "materials/mcp/white_trim.vmat", "roof": "materials/mcp/slate.vmat",
               "floor": "materials/mcp/wet_cobble.vmat", "sidewalk": "materials/mcp/granite_slab.vmat"},
 "tints": {"wall": [[70, 52, 40], [150, 50, 40]]},
 "models": {"lamp": "models/mcp/harbour_lamp.vmdl", "door": "models/mcp/plank_door.vmdl"},
 "sizes": {"floor_height": 128, "house_width": 224, "roof_pitch": 45},
 "light": {"preset": "overcast", "fog": "gradient"},
 "rules": ["steep roofs, 45°", "white window frames 4 units proud of the planks", "no plaster anywhere"],
 "notes": "Houses: room + cut_mesh windows + extrude trims; roofs: prism with 16 overhang."}
```

Only `description` is required. Roles are your own words (`wall`, `trim`, `roof`, `window`, `pier`...); a role may hold one path or a list (houses cycle through it). Materials end in `.vmat`, models in `.vmdl`, tints are `[r,g,b]` or `"#rrggbb"`. A saved style is validated; unknown keys are refused.

## What the quick builders take from an own style

| Operation | From your style | Stays as shipped |
| --- | --- | --- |
| `add_facade` | wall materials + tints (one per house), roof material, `floor_height`, `house_width` | windows, shutters, doors, cornices: parts of the `based_on` kit (Inferno by default). Balconies, awnings, lamps and shop fronts (Italian signs) are off unless the batch turns them on; place your own models with `add_model` |
| `add_building` | wall, floor and roof materials, `floor_height`, `wall_thickness` | facade parts of the kit |
| `add_paving` | floors per surface (`street`, `plaza`, `alley`, `courtyard`, or `floor` for all), `sidewalk`, `curb`, `gutter`, `band`, `border`, `steps` | widths and heights of the Inferno preset |
| `lighting_preset` | `light.preset`, `light.fog` | — |
| `add_wear`, `dress_street`, `add_backdrop` | — (Inferno only) | build by hand: `add_decal`, `add_model`, `scatter`, own backdrop meshes |

`"$nordic.materials.trim"`, `"$nordic.tints.wall.1"`, `"$nordic.sizes.floor_height"` work in **any** field of any operation (meshes, entities, `texture_faces`, `edit_mesh`), so a by-hand batch follows the style too: change the style, replay the batch, the whole map changes.

## By hand: a look that no builder knows

1. **Materials.** Photos from the user (`asset_search(kind="mine")`) → `material_from_reference` (tileable, `real_size`); patterns → `material_create`; stock surfaces tinted → `material_variant`. Look at them with `material_preview`.
2. **One house first.** Walls: `room` or `box` meshes; windows and doors: `cut_mesh`; trims, cornices, gutters: `extrude` along a path; columns, domes: `revolve`; roofs: `prism` (`axis:"x"`) or `extrude` of the roof profile; bevels, insets, bends: `edit_mesh`; materials per face: `texture_faces`; plaster to stone at the base: `paint_blend`. Details that are hard as meshes: `model_build`.
3. **Check it close up** (`visual_check`, frames from the street, from above and from the side), fix, then `prefab_save` it.
4. **Repeat with variety.** `add_prefab` copies with other `"$style"` materials and sizes; `duplicate` with an offset for rows.
5. **Save** the style (`kind:"style"`) and the batch (`kind:"recipe"`, `data` = the operations) so the next chat replays it instead of reinventing it. Write a note about what went wrong and how you fixed it.

## The user's own files (`my/inbox`)

The user drops photos, models and sounds into `my/inbox/`; `asset_search(kind="mine")` lists them with the tool for each kind. Verified end to end (`b10_inbox`): a street photo became a tiling wall material and a poster decal, an OBJ stool a prop, a WAV a sound event that plays.

| File | Turn it into | Then |
| --- | --- | --- |
| photo of a surface (`.png`, `.jpg`) | `material_from_reference(name, image, real_size=[w,h])` (a crop or `quad` for a skewed photo) | `material` / `texture_faces` |
| photo of a facade | `reference_facade(image)` → floors, windows, palette, wall crop | own style or `add_building` |
| picture, logo, poster (alpha or a plain background) | `decal_create(name, image, size, background)` | `add_decal` |
| model (`.fbx`, `.obj`, `.glb`, `.blend`) | `model_build(name, file, scale="auto")`; look at it with `model_preview` | `add_model` |
| sound (`.wav`, `.mp3`, `.ogg`) | `sound_create(name, file, loop)` → `sound_event(name, kind)` | `add_sound` |

Ask the user what a file is for when it is not obvious, and save the result as part of a style or a recipe so the next chat finds it.
