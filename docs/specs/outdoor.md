# Outdoors: water, skylines, supports, skies

Three `map_edit` operations for what lies around the play area: `add_water_body` (sea, river, lake), `add_backdrop` (port, city, industrial, rocky silhouettes, and the `italy` skyline), `add_supports` (piles, posts, trusses); and the list of verified skies (`style_info(kind="sky")`). Code: `outdoor.py`, `ext/outdoor.py`.

## How it works

Every operation validates bounds (ordered, within ±32768), checks assets in the installed game or addon and creates a group `outdoor:<name>` with `cs2map_outdoor` metadata (v1). Re-authoring = delete the group and create it again in the same batch.

**Water**: one upward rectangle, a thin bottom at `level − depth` and shore strips; render-only (no swimming or drowning), so `check_map` never treats it as floor. `water_style: "calm"` uses `materials/liquids/nuke_water.vmat`, which removed the grain the old Anubis water showed at the edge on same-camera frames. The shore top sits at `level + 2` to cover the water edge. An ocean ends where the author sets bounds beyond the camera's horizon.

**Backdrops** (`port`, `city`, `industrial`, `rocky`): measured stock silhouettes (`silhouettes: "stock"`, default) or legacy meshes, extra stock props through `models` (non-solid `prop_dynamic`, no nav growth), render-only beyond the bounds, with player clips on the chosen edges.

**Italy skyline** (`style: "italy"`): the boundary is the union of layout rooms with walls, facade blocks (plane to plane − normal·depth, overhangs 24, roof margin 128) and optional bounds, so houses never land inside the quarter. A seeded RNG places three rings (near, near + 0.48·(far − near), far; defaults 320/2400) with density 1/0.72/0.30 and height 1/0.8/0.45; boxes 16 apart; a bell tower first; cypress groups near houses. Houses are closed `_italy_house` meshes sized from measured stock models: tinted Inferno plaster, a hip roof, a stone plinth, dark windows. A continuous ground slab (256 thick, 512 margin, top one unit below the lowest floor) carries everything; it is render-only and outside playable bounds and nav. Props are marked `cs2map_scenery=backdrop` and left out of gameplay checks.

**Supports**: solid cylindrical piles or posts with top beams; trusses add non-solid cross beams and X braces (never ramps); positions on the footprint perimeter; tops from the layout's floor heights.

**Skies**: day `sky_de_dust2`, sunset `cs_italy_s2_skybox_2`, night `sky_rain_night_01`, overcast `sky_overcast_01`, fog `sky_csgo_cloudy01` (`materials/skybox/`). A "fog" sky is a picture; atmospheric haze is separate.

## Pitfalls we hit

- **Stock background houses are thin one-sided shells with transparent windows**: seen from above or the side they showed through and turned blue. The skyline uses closed meshes; the stock models remain as `houses: "models"`.
- The Inferno skybox bell tower previewed fine offline but had a missing roof texture (magenta) in a real build: always confirm in the game.
- Deeper water bottoms and disabling fog did not fix the water grain; only the Nuke water did.

## Tests

`tests/test_outdoor.py` (determinism, rings, contacts, exposed foundations, atomic refusal); in game `o_harbor` / `p_harbor` (water to the horizon, piles, a truss tower, port backdrop) and the phase 7 quarter (30 houses, 37 cypresses, a tower; full build +7 % time, same nav).
