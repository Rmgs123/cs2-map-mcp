# Inferno lighting

| Task | Take |
| --- | --- |
| Inferno daylight | `lighting_preset` `inferno` |
| Bake light and cubemaps | `map_build` full, `lighting_build` |
| Measure a frame | `lighting_measure` |

Use the `inferno` lighting preset after the quarter geometry, paving and facades are authored. It is an additive preset: the existing `day`, `sunset`, `night`, `overcast` and `indoor` names keep their old fields and behavior.

```json
{"op":"lighting_preset","preset":"inferno"}
```

The operation writes a warm angled sun, the shipped Inferno sky material, a disabled procedural light sky for the sun texture link, the Inferno post-processing volume, a gradient haze and combined light-probe volumes like Valve's: a fine one (voxel 24) over the playable solids and, when the map has a skyline, a coarse low-priority one (voxel 48, level -2) over it, so distant houses get bounce light instead of plain blue sky. The measured Valve values are: sun angles `[63,218,0]`, color `255 247 235`, brightness `3`, sky color `136 199 255`, sky intensity `.875`, visible sky yaw `245` and brightness `.5`, light sky yaw `40`, post-process exposure range `.8..1.1`, gradient fog color `128 172 212`, start `500`, height range `0..10000`, and cubemap fog start `800`.

Generated visible sky brightness is `.3`, calibrated down from Valve's `.5` to preserve color in the quarter's open horizon. The separate lighting sky stays at `2`, so this tuning keeps the sun and bounce settings.

`fog` remains optional. Pass `"fog":"none"` to disable the owned gradient and cubemap fog, or pass the normal object form to tune it:

```json
{"op":"lighting_preset","preset":"inferno",
 "fog":{"mode":"gradient","density":0.55,"start_distance":500,"draw_distance":2200}}
```

Gradient fog ends at 15000 units like the source map (`draw_distance` accepts 128..16384). `heightfog`, `start_height` and `end_height` are optional boolean/number fields. Volumetric fog remains experimental and is not enabled by this preset.

Build real lighting before judging frames. `lighting_build(name, preset="full", cubemaps=true)` runs the full compile and cubemap bake. `lighting_build(name, cubemaps_only=true)` bakes them after a separate full build. Use `lighting_measure(path)` on same-camera PNGs; compare sky-free regions and keep the unclipped mean, `clipped_high` and `clipped_low` together.

## By hand

Any other look is a few entities: `light_environment` (sun: `angles`, `color`, `brightness`, `castshadows`, sky colour and intensity), `env_sky` with a sky material, one master `post_processing_volume` (brush; exposure range and a `.vpost`), `env_gradient_fog` (`fogcolor`, `fogstart`, `fogend`, `fogmaxopacity`) and `env_combined_light_probe_volume` boxes (`box_mins`, `box_maxs`, `voxel_size`) over the play area. Lamps: `add_light` (section `quality`). Judge only after `lighting_build`, on same-camera frames.
