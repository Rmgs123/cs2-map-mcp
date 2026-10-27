# Bot navigation

How many nav areas a map gets, how long the compiler's bot data step takes for them, how to limit the nav, and how builds protect themselves from hours of it. Code: `navcost.py` (estimate, time model, log parsing), `navzone.py` (`set_play_area`, `add_nav_markup`, the `check_map` nav check), `lockrun.py` (the nav guard), `releaserun.py` (release steps), `Project.build_plan`; operations in `ext/nav.py`; guide section `nav`.

## How it works

**The compiler.** With `-nav` the map compiler runs NAVGEN (Recast on the collision triangles, then 31 stages: merging, splitting by markup, hull clearance, connections, island removal) and then "CSGO Compute AI Data" (early occupy times, hiding spots, encounter spots, approach areas) in `server.dll` on one core. NAVGEN prints the final count right away: `>>NAVGEN: stage 01 / 01: IslandRemoval took ... produced N polys`; the AI data prints nothing until each stage ends.

**The time model.** From every logged nav build (55 builds, 16 maps, 2 to 2400 areas, and a 66 000-area terrain map that did not finish in 3 h 40 min): approach areas ≈ 8e-6 · N² seconds, within a factor of three either way (open flat ground is faster, cut-up geometry slower). Levels: ok up to 8 000 areas (minutes), slow up to the limit (20 000 by default, `limits.nav_max_areas`), too slow above it.

**The estimate.** Walkable faces (normal z ≥ 0.7, solid, not tools or clips, not inside NONAV markup, big faces sampled on a grid against the markup): 150 + 0.4 per flat plane + 2 per sloped plane + 1 per 100 m². Flat planes merge; sloped ones (terrain triangles, ramps) are cut by the 128-unit nav tiles and never merge. Right within a factor of three on the logged maps; stock prop collision and unreachable roofs are not seen.

**The guard.** Every build with `-nav` runs under `lockrun` with `--nav-max N`: it reads the compiler's output line by line, prints `[navguard] areas=N level=...` with the time estimate, and above N kills the compiler's process tree and exits 76 (`result.nav_stopped`, a hint). `--nav-probe` kills it right after counting and exits 0: a measurement costing a world compile and NAVGEN.

**Build plans.** `Project.build_plan` decides before a build: the nav estimate goes into the answer; a light build whose estimate is over the limit drops `-nav -gridnav` (unless `bots=true`); a nav-only build keeps it (the guard measures cheaply); VIS is dropped on solid worlds wider than 1.5e8 square units (about 310 × 310 m); the time limit grows with the estimate. The release runs `releaserun`: resources, a nav probe when the estimate is not ok, the final build with or without nav, then it deletes `_vrad3` from the uploaded folder and prints the upload size.

**Limiting the nav.** `func_nav_markup` with `navProperty_NavGen` = `NONAV` / `NONAVNONSOLID` / `NONAVSPACE` / `WALKABLESEED` and the tool material `toolsnavattribute` (as in de_inferno). Measured: the compiler reads only the first mesh of a markup entity and takes it as one convex volume; triangles inside NONAV are dropped before NAVGEN (`orig tris`). `set_play_area` stores the outline in the world (`cs2map_play_area`) and covers the rest of the world with NONAV boxes: cells of `cell` units outside the outline (or farther than width/2 from routes), merged into rectangles (row runs grown down), each box grown by half a unit so neighbours overlap, each box its own entity, all in one group `play_area_nonav`. `paved: true` keeps only cells with paved walkable faces (by face material, grown by a cell); spawns and seeds always keep a few cells. The group carries `cs2map_nav_auto` and is replaced on the next call. Player clips do not stop the nav flood: clips made by `add_fence`, `add_railing` and `add_backdrop` get a `toolsnavclip` twin.

## Why it is built this way

- Only the build knows the true count; the estimate decides what is cheap to try, the guard stops what would take hours. The count is printed before the slow step, so stopping there costs minutes.
- A light build is not stopped mid-way by default: it drops bots beforehand, so the light bake is never thrown away because of the nav.
- NONAV markup is Valve's own mechanism and needs no game files changed; the per-game NavGen parameters in `gameinfo.gi` are not ours to edit.

## Pitfalls we hit

- No point_nav_walkable: the nav is empty (`Skipped... no walkable seeds present`) and bots do not join; `check_map` warns.
- `nav_approach_points_area_size_threshold` on the compiler command line (`+` or `-`) changes nothing (tested); there are no per-map nav settings ("Nav settings not found").
- A heightfield terrain gives about two areas per triangle: a 41 000 m² play area of 1.6 m cells made 66 000 areas.

## Tests

`tests/test_nav.py` (time model, log parsing, progress, flags, the guard and the release steps with a fake compiler, play area and markup, build plans, phantom floors outside the play area). In game: not yet (1.2 acceptance: a copy of a big terrain map with a limited nav).
