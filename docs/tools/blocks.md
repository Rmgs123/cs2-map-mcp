# Housing estates: blocks of flats, garage rows, yards

| Task | Take |
| --- | --- |
| A block of flats: concrete panel, white silicate brick, brick or plaster; entrances, balconies, flat roof | `add_block_house` |
| A row of lock-up garages: brick boxes, painted sheet gates, some open to walk in; flat or on a slope | `add_garage_row` |
| Life in the yard: bushes by the walls, benches with bins, tyre flower beds, washing lines, young trees, a playground | `add_yard` |
| A whole estate of blocks as a cheap backdrop of a big map | `add_district` `"style":"blocks"` (`buildings`) |
| Old Mediterranean houses instead | `add_facade`, `add_building` (`buildings`) |
| Any other house | primitives (`geometry`), stock props (`atlas`), "By hand" below |

## `add_block_house`

```json
{"op":"add_block_house","name":"block_a","origin":[0,-3000,0],"yaw":0,"finish":"panel","length":1344,
 "depth":448,"floors":5,"entrances":3,"balconies":0.3}
```

At yaw 0 the house is `length` long along +X and `depth` deep along +Y, centred on `origin` (its ground); the entrances face -Y. `yaw` turns it like any entity.

- `finish`: `panel` (default), `silicate`, `brick`, `plaster`; `materials` overrides any of `walls`, `plinth`, `frame`, `glass`, `core`, `trim`, `roof` (stock materials by default, so it looks the same on every PC).
- `floors` 1–16, `floor_height` 112 (96–192), `plinth` 32 (0–96), `step` 112: one window slot; `length` is cut into slots and the rest is split between the ends. `window` [width, height] (default 56 × 60, smaller on low floors).
- `entrances`: a count spread along the front, or fractions 0..1 (`[0.2, 0.8]`); each takes one slot: a stock door leaf, a canopy, landing windows half a floor under the flats' windows and a stair-house on the roof. `door`: another stock door model; it is measured from the stock index, turned with its width along the wall and scaled down to fit under the first floor and inside the slot.
- `balconies` 0..1: the share of window columns with balconies (front and back, from the second floor); `seed` picks them.
- `end_windows`: windows on the two ends too. `parapet` 24 (0–96) with a coping. `collision` false: nothing is solid.

How it is built (keep these when you change it or build one by hand): the four walls are one closed solid with mitred corners whose faces are grids of quads with the openings left out (many boolean cuts in one body fail; walls sunk into each other leave a strip of coplanar faces with crossed textures down every corner); reveal sides take the texture across the wall's depth (the wall's own axis looks along them and streaks); frames sink 1 unit into the reveal, glass stands 1 unit behind the frame; a dark solid core 1 unit into the walls closes every opening; the door leaf sinks 1 unit into the side reveals and 2 into the top, and `visual_check` knows it is set in on purpose; the coping laps 2 units over both faces of the parapet.

Result: `id` (the group; one `delete` removes the house), `floors`, `height` (roof deck), `entrances`, `doors`, `windows`, `meshes`.

Limits: no rooms inside (doors are closed, the core is solid; a playable entrance is `add_layout` behind a cut wall); balconies are closed screens in front of windows; flat roofs only. Check (both operations): `visual_check` (props, z-fighting), frames from the street, from the side and from above with `fullbright` on a `fast` build, then light (`full`).

## `add_garage_row`

```json
{"op":"add_garage_row","name":"garages_a","origin":[0,-3800,0],"yaw":0,"count":8,"open":[5]}
```

At yaw 0 the row runs along +X, centred on `origin`, the gates facing -Y. `count` boxes 1–60, `box` [width, depth, height] (default 136 × 240 × 104: a car fits). `open`: box numbers (from 0 at -X) that stand open: hollow, one leaf hanging out, the other shut and solid, a concrete floor — players walk in. `terrain`: the name of an `add_terrain` to stand on: every box takes the ground at its gates + 2 (at most 12 under its highest point) and the row steps with the slope; where the ground falls away the base shows as concrete; an open box's floor stays over the ground everywhere. `gates`: the looks to pick from per box, each a stock material or `{"material", "tint": [r, g, b]}` (a tint shows only on materials made for it, such as `hr_metal_corrugated_001b_tint`); default: blue, grey, green, brown, red and rusty sheet. `materials`: `walls`, `base`, `roof`, `hardware`. `seed`, `collision`.

How it is built: the closed boxes of a run are ONE stepped solid and the roof is another (boxes sunk into each other leave coplanar strips; solids that share corners in one mesh break it), the walls stop flush under the roof (sunk in, the steps doubled the roof's end faces); leaves sink 1 unit into the box with a gap between them, hinges and a handle sink 1 unit into the leaves; the sheet pattern starts at the top of each leaf; leaves of open boxes are solid (a leaf without collision hid a player and let bullets through).

Result: `id`, `boxes`, `open`, `length`, `floors` (world z per box), `meshes`; `thresholds` + `hint` when an open box's floor stands over 16 units above the ground at its gates (flatten the terrain there).

## `add_yard`

```json
{"op":"add_yard","name":"yard_a","area":[[-800,-2400],[800,-2400],[800,-1600],[-800,-1600]],
 "paths":[{"path":[[-800,-2000],[800,-2000]],"width":96}],"playground":true}
```

`area`: the yard outline (or `{"mins","maxs"}`); the ground is `z` (default 0) or the `terrain` under each spot. Only free ground is used: every mesh standing more than 12 units above the ground (curbs and raised walks are walked on), every model (its measured footprint), the spawns and the ground kept clear in front of `add_block_house` entrances and `add_garage_row` gates are taken first. `paths` (and paved meshes found in the area) are where people walk: beds go by them, benches stand on the lawn at their edge facing them; a yard paved all over is a courtyard (`courtyard` in the result): benches face its middle. Counts (defaults grow with the area): `bushes` (along house walls, never into them), `beds` (three tyres lying half in the ground, painted, a low plant in each), `benches` (each with a bin at its end; never facing a wall within 4 m), `lines` (washing lines on two T posts 260 apart), `trees` (young trees 3 m off paths and walls, solid only in the trunk); `playground` true: swings, slide, carousel and rocking horses where there are 300 units of room. `seed`.

Result: `id` (the group), the counts made, `courtyard`; `short` + `hint` when there was no free ground for some. Decor is not solid (bushes, beds, lines); benches, bins, playground things and trunks are.

## By hand

A wall with openings: one `add_mesh` with shape `mesh` per side, the sides meeting edge to edge at the corners (never sunk into each other) — the face split into quads on every opening edge, the quads inside openings left out, the reveal faces added on the opening edges, the back face the same. Frames: thin rings (four boxes) 1 unit into the reveals; glass: one quad 1 unit behind. Doors: `asset_search(kind="prop", query="door")` → `prop_preview` → `add_model` with `anchor:"bottom"`, its width along the wall. Give the faces `uv` `{"mode":"wall"}` (u along each face's own level edge): reveal sides then never streak.
