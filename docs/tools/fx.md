# Particle and atmospheric effects

| Task | Take |
| --- | --- |
| Find a particle (fire, smoke, sparks) | `asset_search(kind="particle")` |
| Place an effect | `add_fx` |
| Rain and snow volumes | `assets_prepare(kind="weather")` → `add_fx` weather |
| Lightning | `add_fx` lightning |

The L extension uses only installed particle assets. Search first, then author the effect through `map_edit`. Build the map before judging runtime visuals.

## Search

Call `asset_search(kind="particle")`:

```json
{"query":"fire","limit":20}
```

`query` is a text substring and `limit` is 1..100. The compact result contains `aliases` and installed `asset_search` matches with kind `particle`. The checked aliases are:

| Alias | Installed VPCF |
| --- | --- |
| `fire_small` | `particles/burning_fx/env_fire_small.vpcf` |
| `fire_large` | `particles/burning_fx/env_fire_large.vpcf` |
| `smoke` | `particles/ambient_fx/ambient_smokestack.vpcf` |
| `steam` | `particles/maps/de_train/train_steam_leak.vpcf` |
| `sparks` | `particles/ambient_fx/env_sparks_omni.vpcf` |
| `dust` | `particles/ambient_fx/dustmotes_64.vpcf` |
| `rain` | `particles/rain_fx/rain.vpcf` |
| `snow` | `particles/rain_fx/snow.vpcf` |
| `ash` | `particles/rain_fx/ash.vpcf` |

Paths are validated against the installed game/addon. An absent path, a path outside `particles/`, or a non-`.vpcf` file is rejected; do not invent a path.

## Point effects

Use `map_edit` with operation `add_fx`:

```json
{"op":"add_fx","effect":"fire_large","origin":[256,256,16],
 "name":"hearth_fire","start":true,"light":true}
```

`effect` is an alias or an installed `.vpcf` path. `origin` is three world units (not needed for a rain/snow box); point effects also accept optional `angles` in degrees. `start` defaults to true. `name` is optional and must be a unique 1..64 character entity name. `light` is a boolean; fire aliases default to true and receive a stationary companion light named `<name>_light` with the `candle_1` style. Set `light:false` when a separate authored lamp should provide the illumination.

Point effects create `info_particle_system` with `effect_name` and `start_active`. The returned `inputs` mapping is `{start:"Start",stop:"Stop"}`. In the game use `ent_fire <name> Stop` and `ent_fire <name> Start`; capture a frame after the particle has had time to accumulate. The `refs` argument of `map_edit` can refer to the returned point entity in later operations.

## Rain and snow volumes

Rain and snow can be authored over an absolute world-space box:

```json
{"op":"add_fx","effect":"rain","mins":[-512,-512,0],
 "maxs":[512,512,512],"name":"yard_rain","start":true}
```

Both `mins` and `maxs` are required together, must be strictly ordered, and are accepted only for `rain` or `snow`; `origin` is then optional. The default `backend:"native"` preserves `func_precipitation` and `{start:"Enable",stop:"Disable"}`. **Native precipitation is not visible in the checked CS2 frames**, including the integrator's map; do not treat compiler success as rain acceptance.

Round 4 adds explicit point weather. Run `assets_prepare(kind="weather")` once: it compiles `particles/mcp/m_rain.vpcf` and `m_snow.vpcf` using installed Valve renderer/initializer fields and textures, without downloading assets. Then:

```json
{"op":"add_fx","effect":"rain","backend":"particle",
 "mins":[-512,-384,0],"maxs":[96,384,640],"name":"m_rain_fx"}
```

The rain layer must be exactly 640 units tall, snow exactly 540. A grid with at most 224-unit spacing covers XY, up to 64 emitters; `name` is at most 48 characters. The emitter radius is 160 units, so particles extend beyond XY bounds; keep the emitters away from walls/interiors. This fixed outdoor layer has **no roof occlusion** and does not follow the camera. Snow uses the same fields but falls slower. Keep snow active for 7 seconds before inspection. `start` defaults true; the returned main target is a relay, with `{start:"FireUser1",stop:"FireUser2"}` controlling all tiles. Wait 3 seconds for rain/7 for snow after stopping to clear old particles.

This explicit backend is compiled on `m_storm` and visually accepted in CS2. `m_rain_ground2-20261003-124742-309.png` and `m_rain_angle2-20261003-125002-494.png` show rain streaks from ground and upward views; `m_snow_off2-20261003-125632-902.png` has no flakes, while `m_snow_on2-20261003-125644-026.png` and `m_snow_angle2-20261003-125651-098.png` show dense falling flakes after `FireUser1`. Native `func_precipitation` remains build-only; the particle backend is the runtime path.

## Lightning

```json
{"op":"add_fx","effect":"lightning","name":"m_lightning",
 "origin":[0,0,128],"duration":0.4,"interval":12,"start":true}
```

Uses an `env_fade` screen flash (blue-white, opacity 180), driven by `logic_timer`. This does not change sky/world illumination. `duration` is .05..2 seconds, `interval` 3..300 seconds; origin defaults [0,0,128]. Return inputs are `{start:"Enable",stop:"Disable",flash:"FireTimer"}` on the timer. `start:false` allows deterministic manual flashes. `m_lightning_timer_before-20261003-131110-446.png`, `m_lightning_timer_flash-20261003-131113-670.png`, and `m_lightning_timer_after-20261003-131118-103.png` show the enabled timer flash and recovery in CS2.

## Validation and evidence

`add_fx` and `add_light` are atomic map-edit operations; a duplicate name or invalid bounds fails before writing. Fire is verified in the game; particle rain and snow are verified with the particle backend (section above). Pair rain with a rain sound (`sound_create(preset="rain")`).

Fire turns on and off with Start/Stop (verified in the game). The companion baked/dynamic light may remain visible briefly after a stop, so use a separate focused light test when validating illumination state.

Fog is authored by `lighting_preset` (quality.md). Use `mode:"gradient"` for the game-accepted haze; `volumetric` remains experimental after O’s paired int_shafts-copy tests. Rain/snow/lightning behavior is unchanged.

## By hand

`info_particle_system` with `effect_name` (a `.vpcf` from `asset_search(kind="particle")`) and `start_active`, inputs `Start`/`Stop` (stop and start in different ticks, at least 0.04 s apart, or the client never restarts it). Lightning: an `env_fade` flash, lamps switched by I/O and a thunder `point_soundevent`, chained with `logic_relay` delays or a `logic_timer`.
