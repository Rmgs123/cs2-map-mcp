# Stock detail kits

| Task | Take |
| --- | --- |
| Measured stock parts (windows, doors, benches...) | `kit_info(category=..., tags=[...])` |
| One part: bounds, front, mount | `kit_info(key="kit/part")` |
| See them | `kit_info(gallery=true, kit=...)` |
| Place one facing a wall | `map_edit` `add_kit_part` |

Kits expose measured stock models for buildings, streets and dressing. Units are
CS2 inches. `verified: "bounds"` means native `vmdl_c` bounds were measured;
`verified: "game"` is reserved for a reviewed CS2 thumbnail.

`kit_info(category?, style?, mount?, tags?)` returns matching `full_key` values,
categories, local front axes, sizes and thumbnail paths. Filters are exact and
all requested tags must match. Typical calls are
`kit_info(category="street", tags=["bench"])` and
`kit_info(category="tree", style="mediterranean")`.

`kit_info(key="street_common/bench_urban")` returns the complete schema record:
model path, native `bounds`, `size`, `front`, `mount`, tags, collision flag,
opening metadata where present, and verification state. Open a gallery image to
choose a part by appearance; do not infer a front from its filename.

`kit_info(gallery=true, kit="street_common", offset=0, limit=12)` builds `a_gallery` with a
full compile, captures the selected parts in CS2 and writes a labelled contact
sheet under `screenshots/kits/<kit>/sheet_000.png`, per-part square thumbnails,
and a JSON manifest. Use exactly one of `kit`, `category`, or `keys=[...]`.
`limit` is 1..12; repeat the identical call while `pending:true` to resume a
compiler or capture batch. The sheet includes an overview plus oblique frames;
large trees, vehicles and props keep their measured full scale.

Place details with the `map_edit` operation:

```json
{"op":"add_kit_part","part":"street_common/bench_urban",
 "at":{"room":"street","wall":"-x","u":0.35,"h":0},
 "scale":1,"offset":1,"ref":"bench"}
```

`at` is a world `[x,y,z]`, a layout wall point `{room,wall,u,h,offset}`, or a
layout floor point `{room,at:[u,v],z:"floor"}`. `facing` is an explicit outward
normal; layout wall points infer it. `up` defaults to `[0,0,1]`; `scale` is
0.001..100; `offset` is world units; `yaw_jitter` (degrees) is for free floor
parts only and is deterministic with integer `seed`. Floor/roof parts snap to a
downward authored-world ray by default (`snap_floor:true`); use `false` only in
a display map or when the base is intentionally floating. Wall parts align the
back side to the wall plane, and a solid physics flag is metadata only—game
collision and route checks remain the authority.

The operation returns the node id, final origin/angles, scale and part key. It
uses the normal VMAP backup and Hammer-dirty refusal path. Unknown fields,
missing models, non-finite vectors, invalid kits, zero normals and absent floor
geometry fail closed.

Current kits: `street_common`, `vegetation_med`, `rocks`, `vehicles`, `market`,
`harbor`, and `interior_common`. `building_*` files are owned by the buildings
agent; compatible records are read by the same API.

## By hand

A kit part is a stock model with a known front and mount: `add_model` with the yaw from the front rule, wall parts touching the wall (sunk at most 1 unit), floor parts on the floor, 1+ unit off any coplanar surface. Find candidates with `asset_search(kind="prop")` and `prop_preview` (section `atlas`). Own kits for contributors: a JSON file in `src/cs2map/data/kits/` following an existing one (schema in `docs/specs/kits.md`).
