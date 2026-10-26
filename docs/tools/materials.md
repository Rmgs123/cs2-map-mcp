# Materials, signs, decals and tileable references

| Task | Take |
| --- | --- |
| Own material from a pattern or picture | `material_create` |
| Glass, glow, water, decal | `material_create(kind=...)` |
| Tinted/aged copy of a stock material | `material_variant` |
| Tileable material from a photo | `material_from_reference` |
| A text sign or an overlay picture | `decal_create(text=...)` / `decal_create(image=...)` |
| Look at materials in game | `material_preview` |

All paths are local project or addon paths. The tools never download images.

`material_preview` builds the service map `b_gallery` and loads it in CS2 (a map shown there is replaced); when the light bake fails it shows the stands from a fast build and says so.
Coordinates and sizes are Source 2 world units.

## Material kinds

`material_create(name, kind, options, image?, pattern?, roughness?)` creates
and compiles one material. `options` holds the kind-specific fields: `tint`,
`opacity`, `dirt` (glass), `color`, `glow`, `mask_image` (emissive), `color`,
`murk`, `flow` (water). Unknown option names are refused.

```json
{"name":"window","kind":"glass","roughness":0.18,
 "options":{"tint":"#cfeef3","opacity":0.22,"dirt":0.35}}
```

Kinds are `opaque`, `glass`, `emissive`, `water`, and `decal`. `roughness`,
`opacity`, `dirt`, and `murk` are 0..1; `glow` is 0..100; `flow` is 0..10.
`image` is a local PNG/JPEG/TGA. `mask_image` is a local grayscale emissive
mask. The result contains the addon `.vmat`, pixel `size`, `kind`, and the
compiler result. Decals are written to `materials/mcp_decals`. Verified in
CS2 on `b_kinds` (glass, neon, water, poster) and `mcp_story` (river water,
window glass).

Verified installed shader inputs:

| kind | shader and significant inputs |
| --- | --- |
| glass | `csgo_glass.vfx`: `GlassTintColor`, `GlassMaskColor`, `GlassMaskTranslucency`, `GlassMaskTransmission`, `TextureNormal`, `TextureRoughness` |
| emissive | `csgo_complex.vfx`: `F_SELF_ILLUM`, `TextureSelfIllumMask`, `g_flSelfIllumBrightness`, `g_vSelfIllumTint` |
| water | `csgo_water_fancy.vfx`: `TextureWavesNormal`, `TextureWavesHeight`, `F_REFRACTION`, `F_REFLECTION_TYPE`, wave/fog scalars |
| decal | `csgo_lightmappedgeneric.vfx`: `F_OVERLAY`, `F_TRANSLUCENT`, `TextureLayer1Color`, `TextureLayer1Translucency`, `TextureLayer1Normal`, `TextureLayer1Roughness` |

Water also carries `mapbuilder.water=1` and `mapbuilder.nonsolid=1`. A water
surface is a mesh; use `func_water` only when its entity behaviour is needed.

## Patterns

`pattern` draws a tiling texture: `bricks`, `tiles`, `planks`, `rubble` (irregular stones), `cobbles` (small
rounded stones), `noise`, `plaster`, `checker`, `stripes`, `solid`; `color`, `color2`, `mortar`, `count` (rows, or
`[across, down]`; for stones: stones across), `seed`, `size`, `variation`. Stones also take `joint` (mortar width as
a share of a stone) and `palette` (1..16 colours) or `palette_from` `{image, region, colors}`: the main colours of a
photo region, so a small or blurry photo still gives a sharp wall in its own colours.

```json
{"name":"lane_rubble","pattern":{"pattern":"rubble","count":6,"joint":0.12,"palette":[[120,112,100],[150,140,122],[98,92,86]],"mortar":[185,180,168],"size":1024},"surface":"stone"}
```

## Text signs

`decal_create(name, text=..., style, color?, background?, glow?, font?)` renders
Windows font text, including Cyrillic. Styles are `plate`, `stencil`, `neon`,
and `paper`.

```json
{"name":"exit","text":"EXIT","style":"neon","color":"#8fffb8","glow":5}
```

Place it using `map_edit`:

```json
{"op":"add_sign","name":"exit","origin":[0,128,128],
 "normal":[0,-1,0],"size":[128,32],"frame":2}
```

`normal` and `angles` are mutually exclusive. Plate and neon are framed
planar meshes, stencil is an overlay, and paper is an opaque card.

For runtime text use:

```json
{"op":"add_world_text","text":"Open: 4","origin":[180,290,42],
 "angles":[0,0,90],"size":18,"color":"#55ffff","targetname":"live_text"}
```

`size` is glyph height in world units; `color` is RGB; Arial and fullbright
are defaults. The entity accepts `SetMessage` through `ent_fire`. In the B3
stand `[0,0,90]` faces the -Y wall.

## Custom decals

`decal_create(name, image, size?, background?, tolerance?, softness?)` keeps
PNG alpha or keys an RGB background with a soft edge. Background is `#rrggbb`;
distances are 0..441 and softness is positive.

```json
{"name":"graffiti","image":"C:/art/wave.png","size":[200,100]}
```

Use the returned material with `add_decal`:

```json
{"op":"add_decal","material":"materials/mcp_decals/graffiti/graffiti.vmat",
 "origin":[64,300,80],"normal":[0,-1,0],"size":[200,100]}
```

The overlay adds no collision or navigation geometry.

## Reference textures and UVs

`material_from_reference` reads a local image, crops/rectifies it, removes
broad illumination, chooses regular or stochastic tiling, derives PBR maps,
and compiles the result. `real_size:[w,h]` stores physical world size.
`set_uv` (and a face's `uv` in `add_mesh`) with `scale:"auto"` reads that metadata, or the measured
size of a stock material (`data/material_sizes.json`, e.g. `brick_soviet_white_01a` = 123 units a tile);
unknown sizes keep 0.25 units per texel. `set_uv` `mode:"wall"` lays courses level on any wall.

`tile_analysis` returns `mode` and `period`; `band_analysis` returns
`band_score`, `seam_energy_ratio`, `band_energy_ratio`, and `band_contrast`.
Scores near zero are the target; values above 0.4 require visual review. A
metric never replaces opening a 3x3 image and a game view. Regular references
are cropped to whole periods. Stochastic references use a torus-aware
correction and retain detail without mirrored edge strips; repeated motifs can
remain visible by design.

## Verification

Build with `map_build` (`full` for lighting), then capture related views in one
`game_screenshot` call and open every PNG. Accepted B3 map `b_kinds` contains
clear/frost glass, a dark neon room, plate/paper/stencil signs, a keyed poster,
graffiti and dirt overlays, reflective water, 512x512 3x3 reference walls,
and world text before/after `SetMessage`.

## By hand

A material is a text `.vmat` (shader, textures, parameters). `material_create` writes `materials/mcp/<name>/<name>.vmat`; for shader keys we do not expose, copy a made one next to it and edit. Stock materials: `asset_search(kind="material")`; apply with `set_material` or per face with `texture_faces`; check with `material_preview` and a frame in the game.
