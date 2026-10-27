# Bots and navigation

| Task | Take |
| --- | --- |
| Bots on a map | `point_nav_walkable` on the floor in every area → `map_build(name, "full")` → `result.nav_areas` > 0 |
| How long will bots take to build? | `check_map` (`nav_too_big`) or the `nav` estimate in any `map_build` answer |
| Keep the nav where players go | `set_play_area` (outline; `nav`: `inside` or `routes`; `paved: true`) |
| No nav in one place, or an extra seed | `add_nav_markup` (`kind`: `nonav`, `seed`, ...) |
| Measure the real nav cheaply | `map_build(name, "nav")`: no light; the result has `nav_areas`; over the limit it stops right after NAVGEN |
| Build without bots | `map_build(name, preset, bots=false)` |

## How the nav is built and why big maps are slow

The compiler first makes the nav mesh (NAVGEN: seconds, under a minute even on huge maps), then "CSGO Compute AI Data" — hiding spots, encounter spots and approach areas — on **one CPU core with no progress lines**. Its time grows with the square of the nav area count:

| nav areas | bot data |
| --- | --- |
| 800 | seconds |
| 2 000 | 15–60 s |
| 8 000 | about 10 min |
| 20 000 (default limit) | about an hour |
| 66 000 (an 830 m terrain map) | many hours: stopped after 3 h 40 min |

Flat floors merge into big areas; every sloped plane (each terrain triangle, a ramp) becomes about two areas that never merge. A terrain map with a 41 000 m² play area got 66 000 areas.

**The nav floods everything walkable that is connected to a seed, player clips included**: a player clip does not stop it, the whole terrain behind the play area's edge gets nav. Clips made by `add_fence`, `add_railing` and `add_backdrop` get a twin in `materials/tools/toolsnavclip.vmat` automatically; give your own boundary clips such a twin (the same mesh with that material).

## The order on a big map

1. Look, light and gameplay first, with `fast`/`full` builds **without bots** (`bots=false`, or no seeds yet).
2. `set_play_area` with the outline players can reach. An outline alone is rarely enough on terrain: the play area itself holds most of the nav (a town map: 69 000 areas without it, 60 600 with it). Add `paved: true` (only roads, sidewalks, floors) and `nav: "routes"` (corridors along the routes bots use): the same map got 4 755 areas with 8 m corridors between its nav seeds, and bot data took under a minute.
3. `check_map`: the `nav_too_big` estimate (right within a factor of three; stock props are not counted).
4. `map_build(name, "nav")` → `job_status`: `result.nav_areas` is the true count (`progress` shows it while the build runs). Under ~8 000 the bot data takes minutes.
5. Then `full` or `release` with bots.

Over the limit (20 000 areas; `limits.nav_max_areas` in `config/settings.json`) every build with nav stops right after NAVGEN and says so (`result.nav_stopped`); light builds skip bots beforehand when the estimate is over it (`bots` in the answer says why; `bots=true` tries anyway, still under the guard). A release measures the nav before its light bake and goes on without bots when it does not fit.

## set_play_area

```json
{"op": "set_play_area", "outline": [[-1500, -1500], [1500, -1500], [1500, 1500], [-1500, 1500]], "nav": "inside"}
```

- `outline`: [x, y] corners of the area players can reach (any simple polygon, units).
- `nav`: `inside` (default) covers the world outside the outline with NONAV markup; `routes` also everything farther than `width`/2 from `routes` `[{"points": [[x, y], ...], "width": 192}]`; `all` adds no markup.
- `paved: true`: nav only where solid walkable faces have paved materials (asphalt, concrete, tiles, cobbles, gravel, stone, floors, planks; not grass, dirt, sand, roofs), one cell around them; with `routes` both apply. Spawns and nav seeds always keep nav around them (2–3 cells).
- `cell` (16–512, default 64): the markup follows the outline in steps of this size; `z` [bottom, top] of the markup (default: the world ± 64); `margin`: units kept free of nav inside the edge.
- Running it again replaces the earlier play area and its markup. The answer gives `nav_m2` (the area left for the nav) and the markup ids.
- The outline is remembered: `visual_check` and `check_map` treat meshes outside it as background (no phantom floors there), the estimate counts only the nav inside.

```json
{"op": "set_play_area", "outline": [[-1500, -1500], [1500, -1500], [1500, 1500], [-1500, 1500]], "nav": "routes",
 "paved": true, "routes": [{"points": [[-1400, 0], [0, 0], [0, 1400]], "width": 256}]}
```

## add_nav_markup

```json
{"op": "add_nav_markup", "kind": "nonav", "box": [[512, 512, -64], [1024, 1024, 256]]}
```

- `kind`: `nonav` (no nav inside), `nonav_nonsolid`, `nonav_space` (Valve's other NONAV tags), `seed` (a walkable seed volume, like `point_nav_walkable`).
- One of `box` [[x,y,z],[x,y,z]], `shape` (any `add_mesh` shape) or a convex `outline` [[x,y],...] with `z` [bottom, top]. Every piece becomes its own `func_nav_markup` (the compiler reads only the first mesh of one, and a mesh counts as one convex volume).
- Cover slopes, lawns, closed yards, roofs and far ground no bot should walk on. `check_map` warns about a seed inside NONAV (`nav_seed_in_nonav`).

## Limits

- NavGen parameters (cell size, merge size) exist only for the whole game (`gameinfo.gi`); there are no per-map nav settings ("Nav settings not found, using default settings" in every log). The cvar `nav_approach_points_area_size_threshold` has no effect on the compiler command line.
- The estimate does not see stock prop collision and roofs nobody reaches; the build's count is the truth.

## By hand

A `func_nav_markup` brush entity (`add_brush_entity`, material `materials/tools/toolsnavattribute.vmat`) with the keyvalue `navProperty_NavGen` = `NONAV` (or `WALKABLESEED`; exactly this key: the FGD default fills it, a second key in another case is ignored) — as in Valve's de_inferno. Measured with the compiler: **one convex mesh per entity** (only the first mesh counts; a mesh of two boxes also covers the gap between them). Triangles inside a NONAV volume never reach NAVGEN. A nav clip is any mesh in `materials/tools/toolsnavclip.vmat`.
