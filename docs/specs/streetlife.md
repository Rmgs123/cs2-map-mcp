# Street life: dressing streets from the atlas

`dress_street` furnishes Inferno-style streets with stock props: cafés at shop fronts, potted trees at doors, benches, bins, bikes, laundry and wires between houses, lamps. It reads the quarter metadata (layout, facades, paving) and adds `prop_static` in a group `street:<name>`; it changes no architecture. Code: `streetlife.py`, `ext/streetlife.py`, set `data/street_sets/inferno.json`.

## How it works

**Input**: `name`, `rooms` (`all`, an id or a list), `style: "inferno"`, `density` 0..1, `seed`, `clear_width` 96..512, `laundry` and `wires` (off by default). `density: 0` makes an empty group with metadata. The set (schema 1) has roles with `models`, `front`, `mount`, `wall_offset`, `needs_passage`, allowed `surfaces` (street, alley, plaza, courtyard), optional collision; a planter role combines a pot (`base_model`) and a tree on its measured top (`fill_model`); 48 models in all. Sizes come from `atlas.measure`, fronts from atlas notes; an unknown or unmeasured model stops the operation before writing.

**Placement**: door plants and cafés follow the facades' `doors` and `shops`; other roles take points along walls (plane and normal from `cs2map_facade`, sidewalk height from `paving.sidewalk_top`; room bounds without facades). Each room keeps two central lanes of `clear_width` up to 112 high, the same lanes to openings and in front of doors and shop fronts. A candidate's full rotated box must stay in the room and off other props and lanes; floor parts land on the nearest solid floor by a ray, wall parts need a real wall behind them (a room edge may be an opening); intrusion into solid meshes uses the same penetration test as `visual_check`, allowing a small mounting contact. Rejections are counted in `skipped`.

**Cafés**: a table, two matching chairs facing it, a stand and a separate awning; the shop entrance stays free. **Laundry** crosses the short axis of narrow streets (≤ 400 wide) at the second or third floor, scaled 0.75..1.25, with small visible ties to the walls (sunk 1 unit). **Wires** turn vertical electric segments horizontal and chain them over the full span (or roll back). Lamps get a warm `add_light` (2800 K, dim, range 320).

The group keeps `cs2map_streetlife` (settings, counts, skipped, ids, support ids); children carry `cs2map_street_role` and `cs2map_street_model`.

## Pitfalls we hit

- The set was checked against the models in the real de_inferno (names from the extracted map): the `terrace_set_01` café (chairs face +X by detail previews), `planter_kit_01` pots with `spindle_bush` trees; an empty pot is never placed.
- The lane at a shared opening must exist on both sides: the first room's wall used to flip the lane of the second, and a barrel blocked the CT exit.

## Limits

Inferno only (another look is dressed by hand, `styles.md`); safe lanes matter more than filling every role on every seed; stock physics is not proven walkable. Acceptance needs a full build, frames at eye level and from above, `visual_check` and a route or bot test through the lanes.

## Tests

`tests/test_streetlife.py`; in game the phase 7 quarter.
