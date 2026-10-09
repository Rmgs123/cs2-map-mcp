# Light presets: Inferno by the numbers

The `inferno` preset of `lighting_preset` reproduces de_inferno's lighting from values read out of the installed map; the other presets (`day`, `sunset`, `night`, `overcast`, `indoor`) keep their simpler fields. Code: `lighting.py` (`PRESETS`, `apply_preset`, probes, fog); guide `docs/tools/light.md`.

## Where the numbers come from

`game/csgo/maps/de_inferno.vpk` → `maps/de_inferno.vmap_c` → Source2Viewer CLI MapExtract → DMX through our codec. Valve's files are only read; the extraction lives in `.local/scratch/` and never in git.

- Sun (`light_environment`): angles `63 218 0`, colour `255 247 235`, brightness 3, sky colour `136 199 255`, sky intensity 0.875, bounce scale 1.75, sky bounce 1.5, angular diameter 0.3, direct light and shadows on.
- Visible sky (`env_sky`): `materials/skybox/test/s2_de_inferno_sky01.vmat`, yaw 245, brightness 0.5. A separate disabled light sky (`skymodel_hosekwilkie_..._elevation_65_0.vmat`, yaw 40, brightness 2) is what the sun's `skytexture` points to.
- Post-processing: `de_inferno_prefab.vpost`, exposure 0.8..1.1, fade 1.5, adaptation speeds 1.
- Gradient fog: colour `128 172 212`, start 500, end 15000, height 0..10000. Cubemap fog: from the visible sky, start 800, height −2000..35000.
- 115 local combined light-probe volumes, mostly voxel size 24, some 48.

## How it works

The preset applies these values, reuses its own entities and keeps the name of an existing visible sky. Valve's local probe volumes cannot be transplanted (coordinates and baked resources belong to their map), so two volumes are made like Valve's general setup: `mcp_inferno_probe` (voxel 24) over the playable solids (`visual.playable_bounds`) and, when the map has a skyline, `mcp_inferno_probe_far` (voxel 48, `indoor_outdoor_level` −2) over it, so distant houses get bounce light instead of plain blue. Cubemaps are baked after a full build (`lighting_build`). `fog` is optional: `"none"` or an object (`start_distance`, `draw_distance` up to 16384, `heightfog`, `start_height`, `end_height`).

## Why and what we calibrated

- The visible sky is 0.3 instead of Valve's 0.5: on full-build frames of a mini quarter 0.5 clipped 30 % of the top frame to white (Inferno's own frame: 11 %); 0.3 gives 4 %. Only the visible sky changed; the light sky, sun and bounce stay at Valve's values.
- Fog ends at 15000 like Valve's: at 8192 the horizon turned twice as blue.
- A single probe volume that also covered the lightmap helper box gave flat light; splitting play area and skyline fixed it.

## Limits

Volumetric scattering is not confirmed and not enabled. `lighting_measure` measures the whole frame, so sky share and different geometry affect comparisons: numbers guide, frames decide.

## Tests

`tests/test_light_inferno.py`, `tests/test_quality_light.py`; in game the phase 7 quarter next to Inferno frames (full build, cubemaps, opened frames).
