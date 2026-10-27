# Environment models (Blender → CS2)

| Task | Take |
| --- | --- |
| Own prop from a template, script or file | `model_build` |
| Look at it | `model_preview` |
| List, inspect, physics body | `model_info` |
| Place it | `map_edit` `add_model` |

`model_build` creates and compiles a Source 2 ModelDoc41 prop in the configured addon. Templates include stock materials, UVs, bevels and separate convex collision pieces. Units are CS2 inches; +Z is up, asymmetric fronts face +X, and the base is z=0.

## `model_build`

```text
model_build(name, script=None, template=None, params=None,
            collision="hull", surface="default", physics=None)
```

Use either a Blender Python script (path or source) or a template. The default template is `crate`. `name` must be a simple lowercase asset name. `collision` is `hull`, `mesh` or `none`; `surface` selects a supported CS2 physics surface.

Optional `physics={"mass":20,"breakable":"wood"}` adds dynamic ModelDoc
data. Mass is kilograms (0.01..100000, default 20); `breakable` is `wood`,
`glass`, `metal` or null. This requires `collision="hull"`. `physics=None`
preserves the previous static model pipeline. The result reports `physics`
with `mass`, `breakable` and `fragments` (0 or up to 8).

```json
{"name":"p_crate","template":"crate","params":{"width":40,"depth":40,"height":40},"surface":"wood","physics":{"mass":20,"breakable":"wood"}}
```

Verified through fresh MCP, compiler and CS2. `model_info("p_crate", physics=true)`
reads a body mass of 20 kg. Add it using `add_physics_prop`, not `add_model`.
The own prop falls and responds to shots/explosions. Break pieces are generic
closed clips of render geometry with separate convex hulls, lasting 15 s;
see [Physics](physics.md) and the current acceptance report for debris limits.
The generated prop_data uses the installed CS2 names `Wooden.Medium`,
`Glass.Window` and `Metal.break`; `Wood.Crate` is a legacy name and is
rejected by the current `propdata.vdata`.
Physical surface and visual material are separate: `surface="glass"` does
not replace a wooden render material. Provide a glass VMAT as `params.material`.

The compact response contains `ok`, `model`, `bounds`, `tris`, final `materials` paths, `preview`, `collision`, `compile` and `warnings`. `compile` includes `ok`, `exit_code`, `log` and `output`; errors or missing assets appear when present. `ok=false` when compilation fails or is unavailable (`compile.skipped`). Full compiler output is at `compile.log`, including benign FBX remap messages omitted from the response.

```json
{
  "ok": true,
  "model": "models/mcp/a_props2_crate/a_props2_crate.vmdl",
  "bounds": [[-32,-32,0],[32,32,64]],
  "tris": 108,
  "materials": ["materials/cs_italy/picrate1.vmat"],
  "preview": ".../screenshots/a_model_....png",
  "preview_note": "Blender preview shows shape and colors; stock VMAT textures are visible in CS2.",
  "collision": "hull",
  "compile": {"ok":true,"exit_code":0,"log":".../logs/compile-....log","output":"...vmdl_c"},
  "warnings": []
}
```

Blender previews show the shape and base colors. They do not decode Valve VMAT textures; `preview_note` makes this explicit. Inspect stock textures in the game after a map build.

## Templates and parameters

All templates accept `material`, `color` (RGB/RGBA, 0..1 or 0..255, or hex), `image`, `roughness=0.7`, `metallic=0`, `z=0`, and `uv_tile_size=128` (units per tile). `material` replaces all default part materials; `color` or `image` selects a generated material when `material` is absent. A part-specific material overrides the global material on that part.

The size column is the measured axis-aligned default bounds `[X,Y,Z]`, including trim and wheels. For models whose front matters (door, dumpster, electrical box), width runs along Y and depth along X.

| Template | Parameters and defaults | Default bounds size | Stock materials |
|---|---|---|---|
| `crate` | `width=64`, `depth=64`, `height=64`, `bevel=0.75` | 64×64×64 | C |
| `barrel` | `radius=24`, `height=64`, `sides=32`, `body_material`, `ring_material` | 50×50×64 | B, M |
| `fence` | `width=160`, `height=96`, `slats=9`, `thickness=5` | 160×5×96 | W |
| `door` | `width=72`, `height=112`, `thickness=6`, `bevel=1.5`, `wood_material`, `trim_material` | 10×82×117 | W, M |
| `window_frame` | `width=128`, `height=96`, `thickness=6`, `verticals=1` | 128×6×96 | M |
| `pipe` | `radius=8`, `length=192`, `sides=24` | 192×16×16 | M |
| `lamp` | `height=128`, `radius=16`, `metal_material`, `shade_material` | 32×32×128 | M, P |
| `table` | `width=128`, `depth=64`, `height=72`, `top=6`, `leg=7` | 128×64×72 | W |
| `shelf` | `width=128`, `depth=32`, `height=160`, `levels=4`, `thickness=5` | 128×32×160 | W |
| `sandbags` | `width=128`, `rows=3`, `height=44`, `bag_width=32`, `bag_depth=20`, `bag_height=44/3` | 127×22×44 | F |
| `jersey_barrier` | `length=128` (`width` alias), `depth=30`, `height=44`, `top_width=8`, `bevel=0.75` | 128×29×44 | K |
| `pallet` | `width=64`, `depth=48`, `height=8`, `slats=5`, `thickness=2.5` | 64×48×8 | W |
| `railing` | `width=128`, `height=40`, `posts=4`, `radius=2.5`, `mid_rail=true` | 133×5×40 | M |
| `dumpster` | `width=80`, `depth=48`, `height=64`, `wall=4`, `wheel_radius=7`, `body_material`, `wheel_material` | 58×82×68 | M, R |
| `electrical_box` | `width=32`, `depth=20`, `height=48`, `wall=2`, `body_material`, `label_material` | 24×32×48 | M, P |
| `bus_body` | hollow bus/van body, front +X: `length=590`, `width=140`, `height=150`, `floor=24` (floor top), `wall=4`, `roof=4`, `window_bottom=44`/`window_top=104` (over the floor), `door_top`, `windows`, `pillar=8`, `doors=[{x,width,side}]` (default two on −Y), a step well inside each door when the floor is over 18 (`step_depth=16`; 0 → 12 → 24: walked in CS2), `windshield=true`, `body_material`, `trim_material`, `floor_material`; every piece its own collision hull, so players walk in and stand inside | 590×140×150 | M, R |

Verified stock paths (also useful in your scripts):

| Key | Surface | Exact VMAT path |
|---|---|---|
| C | Crate, per-face 0..1 texture | `materials/cs_italy/picrate1.vmat` |
| B | Barrel, cylindrical object texture | `materials/models/props/de_train/barrel0003.vmat` |
| W | Tiling wood | `materials/wood/milbeams003.vmat` |
| M | Tiling metal | `materials/metal/metalwall031a.vmat` |
| K | Tiling concrete | `materials/concrete/concrete_ext_07.vmat` |
| P | Plastic | `materials/pbr_defaults/default_white_plastic001.vmat` |
| F | Cloth | `materials/pbr_defaults/default_cloth001.vmat` |
| R | Rubber | `materials/vehicle/rubbertrainfloor001a.vmat` |

Crate faces use 0..1 UVs. Barrel sides wrap once around the cylinder. Tiling surfaces use positions in inches divided by `uv_tile_size`, normally 128–256. `uv_scale` on internal helper calls can reduce density for tiny parts; it is not a public template parameter.

```json
{"name":"a_barrier","template":"jersey_barrier","params":{"length":192,"uv_tile_size":256}}
```

## Custom Blender scripts

A script receives `PARAMS` and `params`, imports `bpy`, and creates mesh objects. Names ending in `_col` are collision only. With no collision objects, render geometry is used and a warning is returned. Existing image textures become generated VMATs in `materials/mcp_models/`.

Use exact paths from `asset_search(kind="material")` as material names. For long paths, keep the full path in the custom property `source_vmat`; Blender truncates datablock names:

```python
mat = bpy.data.materials.new("concrete")
mat["source_vmat"] = "models/generic/barrier_concrete_kit_01/barrier_concrete_01_bare.vmat"
obj.data.materials.append(mat)
```

The driver keeps a unique `a_mat_<hex>` FBX token and remaps it to the checked VMAT. Each build stores author-space `.blend`, render FBX, separate collision FBXs, copied script and metadata under `models/mcp/<name>/sources/<id>/`. Previous ModelDocs remain in `history/`. ModelDoc uses `import_scale=0.0254` and a measured FBX axis/unit compensation so author-space units remain CS2 inches.

## Levels of detail (`lods`)

**CS2 does not switch these levels by distance now** (measured: `prop_static` and `prop_dynamic` at 500–3300 units always draw level 0; `sc_force_lod_level 1|2` shows the levels exist; stock CS2 models have no LOD groups), so `lods` saves nothing in today's game — keep triangle counts low instead. `model_build(..., lods=[{"switch":600,"reduce":0.4},{"switch":1500,"reduce":0.1}])` (also with `file`): from `switch` units away the model asks for a simpler copy. `reduce` keeps that share of the triangles (Blender Decimate, same materials); `"model":"<own model>"` takes a hand-made level built before with `model_build`. Up to 4 levels, distances growing. The result lists each level's triangles. A rebuild without `lods` keeps the levels the model had (`lods=[]` removes them). Collision always comes from the full model. Meant for big props repeated over a map (vehicles, trees, houses), should CS2 start using them.

## Other model tools

- `model_build(name, file=..., scale="auto", collision="hull", surface="default", physics=None)`: FBX/OBJ/GLB/GLTF/BLEND through the same pipeline, including the optional physical body and fragmentation. Auto scale is 2.54 for FBX, 39.37 for glTF and .blend metres and 1 for OBJ; a positive number overrides it. A `.blend` is appended object by object; its scripts never run.
- `model_build(name, file="models/mcp/<own>/<own>.vmdl", scale=1.2)`: a scaled copy of an own model under a new name — the same sources with render meshes, LOD levels, collision and LOD distances times `scale` (mass kept); nothing is exported again.
- `model_preview(name, views=["front","side","top","iso"], size=512, reference=None)`: 1–4 unique views, size 64–2048; reference PNG/JPG creates a side-by-side composite. Output PNGs are under `screenshots/a_*`.
- `model_info()`: addon models under `models/mcp`.
- `model_info(name)`: saved metadata, parsed ModelDoc and source-file availability.

## Putting a prop on a map

```json
{"op":"add_model","model":"a_barrier","origin":[256,128,0],"angles":[0,90,0],"scale":1,"dynamic":false}
```

`model` also accepts a full own/stock `.vmdl` path from any folder — player agents are `characters/models/tm_phoenix/tm_phoenix.vmdl` or `agents/models/ctm_sas/ctm_sas.vmdl` (a wrong folder lists the real paths); use them with `dynamic=true` for readability frames. `dynamic=true` creates `prop_dynamic`; otherwise it creates `prop_static`.

Looks: `skin` picks one of the model's material groups by name (a wrong name lists the real ones, e.g. `generic_tree_large_1` has `default` and `no_motion`); `tint` `[r,g,b]` multiplies the whole model's colour (leaves and bark alike: an orange tint makes an autumn-ish tree, but a stock tree has no autumn group to pick).

```json
{"op":"add_model","model":"models/cs_italy/foliage/tree/generic_tree_large_1.vmdl","origin":[600,128,0],"skin":"no_motion","tint":[255,190,120]}
```
 Build assets first, add the prop, build the map with `full`, then call `game_screenshot` and open the images. Check collision by walking with `noclip 0`; `setpos` alone does not prove that movement is blocked.

## By hand

A `.vmdl` is a ModelDoc KV3 file: `RenderMeshFile` (FBX), `PhysicsHullFile`/`PhysicsMeshFile` for collision, material remaps, `import_scale 0.0254` for FBX in metres. `model_build` with your own Blender script covers almost everything: author in CS2 inches, base at z 0, front +X, collision objects named `*_col`. Check every model with `model_preview` before placing it.
