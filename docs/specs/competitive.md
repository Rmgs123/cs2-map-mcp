# Competitive maps

A competitive skeleton in one operation (`add_comp_plan`: defuse, hostage, wingman), static checks of what decides fair play, physical route timings and bot matches. Code: `competitive.py`, `ext/competitive.py`; tools `playtest(kind="competitive"|"timings")`.

## How it works

**The plan.** `add_comp_plan` calls `layout.add_layout` (12 rooms in three lanes by default, openings 128/192 wide, main openings alternate `at` 0.25/0.75 for dog-legs) and then adds through `Project._apply`: 10–16 spawns per team 64 apart, team buy zones (`TeamNum` 2/3), bomb sites A/B (`func_bomb_target`, designation 0/1; wingman: one) or hostages with a rescue zone, nav seeds in every room, `info_map_parameters`, a light-probe volume and a `toolslightmapres` box. The default scale follows the verified `mcp_accept` (3072×2048): T reach A/B in about 2656 units, CT in about 2082 with an extra CT link. Cover is the author's job; the checks report its absence.

An extension preprocessor packs `add_comp_plan` into an opaque string before the shared layout preprocessor (its room points refer to rooms that do not exist yet), then unpacks it for strict validation. The group `competitive:<name>` keeps `cs2map_competitive` (mode, layout, entity ids, mid, sites, anchors); checks and timings re-read the real origins and brush bounds, so later moves never leave stale coordinates.

**Checks** (`check_map` for type `competitive`): the player hull (32×32×72) inside world geometry, spawns closer than 48, line of sight between spawn areas (a bounded XY grid of eye points over both spawn rooms and buy zones, rays against solid triangles with an AABB broad phase; direct sight, not wall-bang), cover within 96 units and at least 40 high, opening widths (< 48 error, < 128 or > 256 warning), connectivity through openings (a swept hull test against static geometry).

**Timings** (`playtest(kind="timings")`): estimates from layout waypoints at 250 u/s (knife), and with `live=true` one physical route per call (`offset`) walked with real input from the real spawn, noclip off, bots kicked, under the game lock. The time is the last completed sample minus the first; a failed walk has no seconds. Results are cached in `logs/comp-timings-<map>.json`, keyed by the map file's mtime.

**Bot matches** (`playtest(kind="competitive")`): the shared playtest samples bots; a bot moves if it shifts 64+ units between live samples of one round; deaths are alive→dead changes with the layout room. `round_seconds` (20..120) sets the round cvars, reading the real values first and restoring them in `finally`. `competitive_ok` needs both teams moving, nav > 0, a finished round and no errors; a short test may honestly return false.

## Why it is built this way

- One route per call keeps every tool call inside the 120 s budget (lock ≤ 20 s, map load ≤ 25 s, cleanup reserved).
- Checks re-read the map instead of trusting the plan metadata: authors move things.
- Rounds end by the game's own rules, never by a forced restart during observation.

## Pitfalls we hit

- Stock prop collision and run-time doors are invisible to static checks: only `route_test` proves a path.
- The first round-counter change comes from bots joining, not from a finished round; it is ignored.

## Tests

`tests/test_competitive.py`; in game `c_defuse` (full build, nav 435, six physical routes, CT reaches A/B 3.2/4.0 s earlier, a 60 s bot match with named deaths), hostage and wingman skeletons with frames.
