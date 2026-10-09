# Lamps, effects and weather

Lamps (`add_light`, `light_rooms`), stock particle effects (`add_fx`, `asset_search(kind="particle")`), rain and snow, lightning. Code: `lighting.py`, `fx.py`, `ext/quality.py`, `ext/fx.py`. Fog and presets are in `quality.md` and `light.md`.

## How it works

**Lamps.** `add_light` creates a FGD-default `light_omni2` (ceiling, wall, fire, neon) or `light_barn` (spot), checks installed model paths and normalises colour (RGB or temperature) and brightness (lumens: dim 300, normal 1000, bright 2400 or a number). FGD facts used: `brightness_units = 1` is lumens; `colormode = 1` uses `colortemperature`; `directlight` 1 static, 3 stationary, 2 dynamic; `castshadows` 0 off, 2 baked, 1 baked + dynamic; `showlight = 1` draws the source of an omni or rect light. `light_barn` has no `showlight`, so a spot gets a tiny `light_rect` for the visible disc. Fire adds a stationary companion with the `candle_1` light style. Styles come from the game's `scripts/light_styles.vdata_c` (`candle_1..3`, `flicker_1/2`, `fluorescent_flicker`, pulses). Switchable lamps are dynamic lights parented to a parked `func_movelinear` (lights exist only on the client, so I/O cannot switch them).

`light_rooms` finds rooms from mesh triangles (wall, floor and ceiling rays, bounded voting, five probes) and lays lamps out in each room's local axes, also for rotated rooms.

**Effects.** `asset_search(kind="particle")` lists short aliases first (`fire_small`, `fire_large`, `smoke`, `steam`, `sparks`, `dust`, `rain`, `snow`, `ash`), each an installed `.vpcf`, then the full search. `add_fx` refuses missing paths and paths outside `particles/`. A point effect is an `info_particle_system` with `Start/Stop`.

**Weather.** Native `func_precipitation` (a `toolsprecipitation` brush, `Enable/Disable`) stays the default for compatibility, but only builds; the accepted path is `backend: "particle"`: `assets_prepare(kind="weather")` writes `particles/mcp/m_rain.vpcf` and `m_snow.vpcf`, and the operation lays a grid of particle systems (rain layer 640, snow 540, ≤ 224 apart, ≤ 64 tiles) started and stopped through one `logic_relay` (`FireUser1/FireUser2`). The definitions follow the structure decoded from the stock rain/snow (trails or sprites, continuous emitter, movement, decay, fade) with stock textures, but drop the stock precipitation culling (control point 1, roof blockers): an outdoor effect at fixed height, not attached to the player, no roof occlusion.

**Lightning**: an `env_fade` flash (blue-white, alpha 180, 0.05..2 s) driven by a `logic_timer` (3..300 s, start disabled, `Enable/Disable/FireTimer`). It is a screen flash; it does not change the sky or the lamps.

## Pitfalls we hit

- The volumetric fog controller and volume exist at run time but are excluded from `csgo.fgd`; same-camera frames never showed scattering, so it stays experimental and gradient fog is the accepted haze.
- A "fog off" frame once matched the "fog on" frame byte for byte (a stale capture): toggles are only accepted with distinct frames.
- Steam, sparks, ash and the native rain/snow compiled but gave no clear signal in wide frames: they stay "build only" until a dedicated angle shows them.

## Tests

`tests/test_fx.py`, `tests/test_core_round3.py`; in game `l_light` (dim/normal/bright distinguishable), `l_atmos` (fire on/off), `l_fx` (large fire), `m_storm` (particle rain and snow, lightning before/flash/after).
