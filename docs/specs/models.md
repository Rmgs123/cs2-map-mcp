# Own models: Blender to ModelDoc

`model_build` makes a prop from a Blender script, a template or an imported FBX/OBJ/GLB and compiles it into `models/mcp/<name>/<name>.vmdl` with collision, materials and optional physics. Code: `models.py`, `blender_run.py`, `blender/driver.py`, `blender/common.py`, `blender/templates/*.py`, `ext/models.py` (also the operation `add_model`); weapons on stock skeletons: `weapons.md`.

## How it works

**Blender** runs headless through `blender_run` (`--python-exit-code 1`, a background job, profile `.local/blender`, logs `logs/blender-*`). The driver clears the scene, runs the script or imports the file, bakes transforms and UVs, saves an author-space `.blend`, exports render meshes to one FBX and each collision mesh to its own FBX, and prints one line `JSON>{...}` (bounds, triangles, materials, collision objects). Without `_col` objects the render mesh is a fallback hull.

**ModelDoc** (written with `cs2map.kv3`): `RenderMeshFile` (the FBX under `sources/<id>/`), a `DefaultMaterialGroup` remapping Blender material tokens to addon or stock `.vmat`, `PhysicsHullFile` or `PhysicsMeshFile` per collision FBX. `physics={mass, breakable}` adds `PhysicsBodyMarkup` (mass in kg), `prop_data` and, for breakables, up to eight closed clips of the render mesh by octants (`bisect_plane` + `holes_fill`, UVs kept on outer faces) as break pieces. Units: authored in inches, `import_scale = 0.0254` because FBX writes metres; +Z up, front +X, base at z 0.

**Materials**: a material named `materials/...vmat` (or `models/...`, `gloves/...`) is remapped directly; long paths live in the custom property `source_vmat` (Blender truncates names to 63 chars); other materials become generated PNG + `.vmat` from their image texture or colour under `materials/mcp_models/`. Templates use verified stock materials and size-proportional UVs (`project_uv`, 128 units per tile by default; barrels get one cylindrical turn; crates 0..1 per face).

**Templates**: crate, barrel, fence, door, window_frame, pipe, lamp, table, shelf, sandbags, jersey_barrier, pallet, railing, dumpster, electrical_box, each with separate convex collision copies; asymmetric ones face +X. `add_model` places a model by name or path as `prop_static` or `prop_dynamic` with origin, angles and uniform scale.

## Pitfalls we hit

- Collision objects need a material: otherwise the Source 2 FBX importer creates a `cube.vmat` section and the build fails. The driver gives them the first render material.
- Blender's `.001` name suffixes break paths: materials travel as short `a_mat_<hex>` tokens.
- Built-in `prop_data` names must come from the installed `scripts/propdata.vdata` (`Wooden.Medium`, `Glass.Window`, `Metal.break`); the old `Wood.Crate` is refused by CS2.
- A plain `prop_physics` deleted an own breakable prop at run time; `prop_physics_override` works.
- `GetFbxMaterialPath Failed!` is normal with remaps: kept in the log, not reported as an error.
- The Blender preview cannot decode stock materials: their colour and texel density are judged on game frames.

## Limits

No LODs or automatic optimisation (a warning above 50 000 triangles). `scale="auto"`: FBX 2.54, glTF 39.37, OBJ 1.

## Tests

`tests/test_models.py`, `tests/test_models_physics.py` (one real Blender run checks all 15 templates: density, normals, base, collision, overrides); in game `p_physics`, `mcp_accept`.
