# Materials: from photos, patterns and special kinds

Own materials from pictures or procedural patterns, tileable PBR from photos, glass, glowing, water and decal kinds, signs and world text. Code: `materials.py`, `textures.py`, `uv.py`, `structure.py` (signs, decals); tools `material_create`, `material_variant`, `material_from_reference`, `material_preview`, `decal_create`.

## How it works

**Shader parameters** were checked against the VCS72 DATA blocks of the installed `shaders_vulkan_dir.vpk` (ValveResourceFormat); no `.vmat` field is guessed:

| Kind | Shader | Parameters |
| --- | --- | --- |
| glass | `csgo_glass.vfx` | `GlassTintColor` (tint), `GlassMaskColor` (dirt), `GlassMaskTranslucency` (opacity), `GlassMaskTransmission` `[255 255 255 0]`, normal and roughness, back faces, no shadows |
| emissive | `csgo_complex.vfx` | `F_SELF_ILLUM=1`, `TextureSelfIllumMask` (an RGBA alpha becomes the mask), `g_flSelfIllumBrightness`, `g_flSelfIllumAlbedoFactor`, `g_vSelfIllumTint` |
| water | `csgo_water_fancy.vfx` | `TextureWavesNormal`, `TextureWavesHeight`, `F_REFRACTION=1`, `F_REFLECTION_TYPE=2`, waves, fog, speed, `mapbuilder.water=1`, `mapbuilder.nonsolid=1` |
| decal | `csgo_lightmappedgeneric.vfx` | overlay/translucent features, `TextureLayer1Color/Translucency/Normal/Roughness` |

Materials are written only to the addon (`materials/mcp/`, `materials/mcp_decals/`) with a JSON sidecar (`kind`, optional `real_size`) and compiled at once.

**From a photo** (`material_from_reference`): crop or rectify (four corners), flatten the light, make it seamless, derive normal, roughness and AO. Seamless: `tile_analysis` finds the period by the derivative of the autocorrelation; regular textures are cropped to whole periods; others use stochastic DP seams and a periodic Poisson solve in FFT, so the first and last rows and columns match (`seam_error = 0`) without a wide band. `band_analysis` compares edge gradient energy with inner bands (brick 0.072, concrete 0.099, tile 0.092 in the final check): a diagnostic, not a guarantee.

**Signs and decals**: `decal_create(text=...)` renders a power-of-two artwork and ink mask with a Windows font (plate, stencil, neon, paper); `add_sign` builds a planar mesh with 0..1 UV and, for plate and neon, a frame; stencil uses a static overlay. `decal_create(image=...)` keeps RGBA or keys out a colour by RGB distance and removes the matte colour from half-transparent pixels. Overlays make no physics or nav. `add_world_text` writes the FGD keys of `point_worldtext`; `SetMessage` changes the text at run time.

## Pitfalls we hit

- The old VCS ≤ 71 provider gave wrong parameter names; only the installed VCS72 data is trusted.
- White matte fringes on posters came from half-transparent pixels keeping the background colour; removing the matte colour fixed them.

## Tests

`tests/test_materials_*.py`, `tests/test_uv_materials.py`; in game `b_kinds_*` frames: glass shows coloured boxes behind it, neon spills pink and green, water ripples and reflects, posters have no white frame, world text changes at run time.
