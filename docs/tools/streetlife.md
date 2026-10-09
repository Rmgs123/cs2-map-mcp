# Street life

`dress_street` is **quick fill in the Inferno look** (Inferno street sets). For another look place props by hand from the atlas (`asset_search(kind="prop")` → `prop_preview` → `add_model`, or `scatter`) and save the set in `my/` (section `styles`).

| Task | Take |
| --- | --- |
| Plants, cafe terraces, bikes, laundry, wires on a street | `dress_street` |
| Roles of a street set | `style_info(kind="street_set")` |

`dress_street` fills a layout quarter with measured Inferno street props. It reads the room, paving and facade metadata written by `add_layout`, `add_paving` and `add_facade`; each run is deterministic and writes one group named `street:<name>`.

```json
{"op":"dress_street","name":"quarter","rooms":"all","style":"inferno",
 "density":1.0,"seed":5,"clear_width":112,"laundry":true,"wires":true}
```

Use `rooms:"all"` or a room id/list. `density` is 0..1, `seed` is a signed 32-bit integer, and `clear_width` is the reserved player corridor (96..512 units). Both hanging flags default to false. They span the shorter room axis only in street/alley rooms up to 400 units wide, above the player. Cloth scale stays within 0.75..1.25; short end ties reach the walls. Electric segments rotate from their measured Z axis and form a complete wall-to-wall span; failed partial spans are removed.

The response reports the group id, counts by role, attempts and skip reasons (`passage`, `opening`, `overlap`, `geometry`, `floor`, `wall`, `bounds`, `span`). Large stock props use `solid 6` when the measured model has physics; wires, signs, drains and small door plants stay nonblocking. Atlas bounds and recorded facing orient the props. Floor rays, real wall backing, facade props and solid mesh triangles are checked; unknown or unmeasurable models fail closed. Density 0 creates an empty metadata group; duplicate names are rejected.

The set follows what de_inferno itself uses (read from the shipped map): cafe terraces of up to two `terrace_set_01` tables per plaza shop, two matching chairs facing each table (wood, rattan or cushioned), a vintage parasol; terracotta pots `planter_kit_01` with small `spindle_bush` trees at doors and on squares. A pot whose tree does not fit is not placed (`empty_pot` in skips). Shared layout openings are kept clear on both sides. Warm 2800 K lights accompany the wall lamp and lamp post. `style_info(kind="street_set", name="inferno")` returns compact role counts and the set path (about 50 selected models).

Read `atlas`, `kits` and `quarter` before authoring. Reproduce the checked quarter with `.local/scratch/S/{create,edit,dress}.json`, build with `full`, then inspect `.local/scratch/S/views.json`, run `visual_check` and walk `.local/scratch/S/route.json`. The placement pass does not replace a physical game route test.

## By hand

Another look or a hand-picked street: `add_model` (static) per prop with the front rule (`yaw = yaw(D) − yaw(front)`), floor props on the floor (origin at floor height for models with the base at z 0; sizes from `asset_search(kind="prop")`), wall props touching the wall, lamps with a warm `add_light` (about 2800 K, dim). Keep two central lanes 96+ wide and every doorway free; then `visual_check` and frames at eye level and from above. Save the set as an own style (`styles`).
