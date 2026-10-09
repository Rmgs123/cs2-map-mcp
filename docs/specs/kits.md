# Kits: measured stock building parts

CS2 has thousands of stock parts Valve builds houses and streets from: Inferno windows and shutters, Italy balconies and awnings, gutter and chimney kits, lamps, benches, cars, trees, rocks. A file name says nothing about size, facing or look; a kit says it all. Code: `kits.py`, `ext/kits.py`, `data/kits/<kit>.json`; tools `kit_info` (part, list, gallery) and the operation `add_kit_part`.

## The format

```json
{"schema": 1, "kit": "inferno_house", "title": "Inferno/Italy house parts", "style": ["mediterranean", "italy"],
 "parts": [{"key": "window_shutters_green", "model": "models/de_inferno/bk01/....vmdl",
            "category": "window", "mount": "wall", "front": "+x",
            "bounds": [[-4, -30, -2], [6, 30, 82]], "size": [10, 60, 84],
            "opening": [48, 72], "opening_bottom": 4,
            "tags": ["shutters", "green", "wooden"], "solid": false, "verified": "game",
            "thumb": "screenshots/kits/inferno_house/window_shutters_green.png"}]}
```

| Field | Meaning |
| --- | --- |
| `key` | unique in the kit, `[a-z0-9_]` |
| `category` | window, door, shutter, balcony, awning, roof, chimney, gutter, trim, sign, lamp, street, vehicle, tree, bush, plant, rock, fence, prop |
| `mount` | wall (against a facade), floor, roof, ceiling, hang |
| `front` | the local axis that points away from the wall (wall parts) or toward a viewer |
| `bounds`, `size` | render bounds from the `vmdl_c` (`visual.model_bounds`), units |
| `opening`, `opening_bottom` | windows and doors: the wall hole [width, height] and its bottom above the model origin; no field = a surface part |
| `solid` | has collision |
| `verified` | `game` after an opened frame confirmed the facing; `bounds` = measured only |
| `thumb` | a game frame the AI chooses by |

## How it works

`kits.place` stands a part with its front along the wall's outward normal, using `front` and `bounds`; a wall part's back touches the wall plane. `add_kit_part` exposes it in batches, `kit_info(gallery=true)` photographs a kit or category on a service map and returns one labelled sheet. Kits are named by style (`inferno_house`, `mirage_house`, `street_common`, `vegetation_med`, `rocks`, `vehicles`, ...).

## Rules

Only stock models or addon models (`models/mcp/**`), paths exactly as `asset_search` lists them. `verified: game` only after a frame in the game was opened and judged. The schema changes only through the integrator: buildings, facades, streets and the atlas all read it.

## Tests

`tests/test_kits.py`, `tests/test_kits_placement.py`; in game `a_kit_accept` (175 parts in 9 kits).
