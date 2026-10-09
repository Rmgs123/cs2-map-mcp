# Quality, lighting, atmosphere, and playtesting

| Task | Take |
| --- | --- |
| Audit before building | `check_map` |
| Light presets and fog | `lighting_preset` |
| Lamps (flicker, switchable, shafts) | `add_light`, `light_rooms` |
| Bots play a round | `playtest` |
| Automatic frames | `game_screenshot(views="auto")` |

These tools author VMAP entities and inspect real game captures. A lighting change is only meaningful after a `full` build; `fast` does not bake lightmaps. `game_screenshot` loads the requested map while holding the shared CS2 lock. A response from a headless check or a successful compiler run is not a visual acceptance.

## Map audit and bot run

`check_map(name)` audits the VMAP without changing it and returns `{map, ok, problems}`. Problems include severity, code, node id, message, and fix; resolve every `error` before compiling. The checks cover FGD classes/keys, installed assets, I/O targets, solid floor rays, spawn spacing/clearance, gameplay volumes, sky/sun, probe/lightmap volumes, and navigation seeds.

`playtest(map, bots=10, seconds=60, spectate=true)` loads the map, runs a bounded round (seconds is capped at 120), and returns compact team/alive/death/pickup/error samples. With `spectate` (default) you watch from the spectator team, so bots play 5 v 5 alone (`human:"spectator"`). Long samples are kept under `logs/`; bots require compiled navigation areas.

## Lighting presets and fog

Use `map_edit` with an operation such as:

```json
{"op":"lighting_preset","preset":"sunset","fog":{"mode":"gradient","density":0.55,"draw_distance":2200}}
```

`preset=day|sunset|night|overcast|indoor` updates sky, sun and post-processing. Every preset (and `inferno`) also covers the geometry that exists with light-probe volumes (indirect light, reflections), so apply it **after** the geometry. Overcast uses `sky_overcast_01`; the other preset skies remain unchanged. To select one of five outdoor skies separately, use `add_backdrop sky=...` after the preset (see outdoor.md).

`fog=none|light|heavy|gradient|volumetric` is optional. `none` disables existing gradient/cubemap/volumetric fog. `light`, `heavy` and `gradient` use the CS2-supported `env_gradient_fog`, reusing `mcp_atmos_gradient`. Their opacity defaults are .12, .8 and .3. Custom options: `fog:{mode:"gradient",density:0.55,draw_distance:2200}`; density .01..3 (gradient opacity capped at 1), distance 128..8192 units. Gradient fog ignores height and fades from 8 units to draw_distance. Enable/Disable works in the opened o_harbor fog pair.

`volumetric` retains the legacy controller/volume recipe, with IsMaster=1, IndirectEnabled=1, optional ordered `mins/maxs` (this mode only), relative controller bounds plus64 and volume bounds plus32. It is **experimental**: both entities exist in CS2, but same-camera full-build frames on copies of int_shafts did not show scattering after bounds, DrawDistance1800, Enable/SetFogStrength3/ForceRefresh. The operation returns a fog_note; use gradient for accepted visible haze. Invalid options fail before mutation. `light`/`heavy` now use gradient rather than the unconfirmed volumetric backend.

## Authoring lamps

`map_edit` operation `add_light` accepts:

```json
{"op":"add_light","kind":"ceiling","origin":[256,256,224],"temperature":3000,
 "brightness":"normal","range":768,"shadows":true,"name":"hall_lamp"}
```

`kind` is `ceiling`, `wall`, `spot`, `shaft`, `fire`, or `neon`. `origin` and optional `angles` are world units/degrees. Choose either `color` as three 0..255 channels or `temperature` in kelvin (1000..20000). `brightness` is `dim` (300 lm), `normal` (1000 lm), `bright` (2400 lm), or a numeric lumen value from 1..100000. `range` is 32..8192 units. `shadows` defaults to true. `size` (0.5..64, default 10) is the radius of the glowing source sphere: 1.5–3 for bulbs on a string. `model` may be `false` to suppress the visible source or an installed `.vmdl` path returned by `asset_search` (the model is then the visible lamp, without the sphere); an invalid path is rejected.

Optional `style` animates the lamp: `flicker_1`, `flicker_2`, `fluorescent_flicker`, `dimmer`, `candle_1..3`, `gentle_pulse`, `slow_strong_pulse`, `slow_pulse_nofade`, `fast_strobe`, `slow_strobe` (stationary light; `fluorescent_flicker` verified in CS2 by frame brightness 17→22→17).

**Switchable lamps.** CS2 light entities exist only on the client, so `ent_fire`, I/O and scripts cannot turn them on or off. `start_off: true` creates a fully dynamic lamp parented to an invisible `func_movelinear` named `name`, parked 6000 units below its place; `<name> Open` brings the lamp in (on), `<name> Close` parks it (off). The result has `switch: {on, off}`. In a story: `{"fire":"corr_lamp","input":"Open"}`. Verified on `mcp_story`: dark corridor, switch pressed, corridor lit. Dynamic lights are expensive; use a few.

`ceiling`, `wall`, `fire`, and `neon` use FGD `light_omni2`; `spot` uses `light_barn`. Visible omni sources use `showlight`; spot lights receive a tiny `light_rect` disk because `light_barn` has no `showlight` key. Fire adds a stationary `candle_1` companion light. The operation returns the main entity id and all created ids.

`shaft` is a visible light beam (god ray) made the way Valve builds them in de_dust2/cs_italy: translucent planes with a stock lightshaft material, UV-mapped 0..1 across and along the beam, edges faded by vertex alpha. Verified in CS2 on `int_shafts` and `m_storm` (integrator, 03.10).

- **Sunlight through a window** (best look): `{"op":"add_light","kind":"shaft","sun":true,"window":{"mins":[x0,y0,z0],"maxs":[x1,y1,z1]}}`. `window` is the opening box (thin along the wall normal, e.g. the wall thickness); the beam is that rectangle swept along the `light_environment` direction until it hits a solid surface, so it ends exactly on the sunlit patch of the floor. No lamp is created (the sun lights the patch). The sun must shine through the opening (the tool refuses light parallel to the wall or below the horizon); aim the sun with `update` of `mcp_sun` angles `[pitch, yaw, 0]` (pitch down, yaw = direction the light travels).
- **Lamp beam**: without `sun`, `angles` give the direction (pitch 90 = down) and a `light_barn` spotlight is added; the beam starts at `window` or at a `width` x `height` (default = width, 8..1024) rectangle centred on `origin`.
- Optional: `length` 32..4096 (default: traced to the first solid surface, corners separately), `beam` `soft` (default) / `smoky` / `dusty` (stock material), `shape` `star` (4 planes, default; some plane always faces the camera) or `cross` (2 planes, cheaper, a dark streak at some angles), `intensity` 0.05..1 (default 0.75, scales the tint), `color` [r,g,b] tints the beam (and the lamp). The beam fades in over its first 20% so the opening itself stays clean. Meshes are `physicsType:none` (not walls for `check_map` either). `start_off` is refused (static planes).
- Beams read best in darker rooms. Run `assets_prepare(kind="light_shaft")` once, then optional `beam:"additive"` selects `materials/mcp_o/lightshaft_additive.vmat`. Native DATA shows stock soft already has F_ADDITIVE_BLEND=1. The copy preserves csgo_effects and all stock masks, with material fog disabled. Paired full-build frames on o_shaft_stock/o_shaft_additive show brighter beams in gradient fog; stock defaults are unchanged. Dense haze can still hide scene detail. The tool exports masks from the installed VPK under materials/mcp_o, keeps material backups and returns ok/log/errors. Rebuild maps after preparing it.

```json
{"op":"add_light","kind":"shaft","sun":true,"window":{"mins":[244,512,150],"maxs":[356,528,294]}}
{"op":"add_light","kind":"shaft","name":"m_shaft_550","window":{"mins":[494,-400,176],"maxs":[606,-384,280]},
 "angles":[35,90,0],"brightness":4500,"color":[160,195,255],"range":640,"model":false}
```

`light_rooms` places one `normal` ceiling lamp per 512-unit tile in each enclosed room found from solid triangles:

```json
{"op":"light_rooms","brightness":"normal","temperature":3000}
```

Optional `ids` restricts floor candidates to mesh/group ids or refs. Existing lamps within 96 units are skipped. This is a bounded geometry heuristic; small or unusual rooms can be missed. Run `check_map` and a `full` build after authoring.

## Image measurements

`lighting_measure(path)` reads a local PNG/JPEG and returns normalized `mean`, `clipped_high`, and `clipped_low`. For the checked 512x512 room, the `normal` frame measured mean `.25373` and clipped-high `.00097`; this is a repeatable fixture target, not a universal exposure rule. Compare frames from the same interior camera and keep sky/window pixels out of the decision when possible.

`lighting_build(name,preset="full",cubemaps=true)` performs a tracked build and optionally a runtime cubemap bake. `lighting_build(cubemaps_only=true)` bakes only cubemaps. Both return compact status and retain long logs under `logs/`.

## Automatic camera views

`game_screenshot(map, views="auto", offset=0, limit=6)` derives bounded views from solid triangles, spawns, objective/buyzone volumes, and roofed rooms. `limit` is 1..6; use `offset=0`, then `6`, and so on for a large result. The response includes `total_views`, `offset`, and each PNG path. Explicit `views` remains a list of `{name,pos,ang}` dictionaries. Names are prefixed with `CS2MAP_AGENT` (for L, `l_`) when the environment variable is set.

Room detection samples floors, ceilings, and four wall rays, separates rooms that share a roof, and verifies five interior probes. Cameras are placed at a clear eye position with a line of sight to the room centre. If an overview ray hits a roofed room, that overview is omitted or moved below the roof so it does not show only a ceiling.

The automatic captures were opened and reviewed for `mcp_accept`, `mcp_showcase`, and `l_atmos`. Interior/spawn/objective frames were useful; one `mcp_showcase` exterior was bright. This remains an authoring heuristic: open every PNG and inspect it for the actual map.

If CS2 has no visible Workshop Tools window, capture tools fail with a diagnostic. Do not use `maps *`; restore the tools window and retry.

## By hand

Bots: `bot_add_t`, `bot_add_ct` through `game_command` after a `full` build (nav needs it; spawns on walkable floor). Reflections: `env_combined_light_probe_volume` boxes over the play area, baked by `lighting_build`. Views: `game_screenshot(views=[{"pos":[x,y,z],"angles":[pitch,yaw,0]}])` with `pos` at the feet. Fog: `env_gradient_fog`. Lamps: `light_omni2`, `light_rect`, `light_barn` through `add_light` or `add_entity`.
