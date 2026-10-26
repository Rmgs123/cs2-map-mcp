"""Environment model pipeline: Blender scripts/imports to CS2 ModelDoc41 assets.

The module owns all filesystem work for models.  It deliberately keeps the
Blender process asynchronous through :class:`JobManager`, while the public
``model_build``/``model_import`` helpers wait for the short build and compile
steps so their return value is immediately useful to an MCP caller.
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
import math
import os
from pathlib import Path, PurePosixPath
from typing import Any

from . import blender_run, compiler, kv3, materials
from .config import PROJECT_ROOT, Settings, check_name, ensure_inside
from .jobs import Job, JobManager


class ModelError(ValueError):
    """Invalid model input or an unavailable model toolchain."""


BLENDER_DIR = PROJECT_ROOT / "src" / "cs2map" / "blender"
TEMPLATES_DIR = BLENDER_DIR / "templates"
TEMPLATES = ("crate", "barrel", "fence", "door", "window_frame", "pipe", "lamp", "table", "shelf",
             "sandbags", "jersey_barrier", "pallet", "railing", "dumpster", "electrical_box")


def _agent():
    return re.sub(r'[^A-Za-z0-9]', '', os.environ.get('CS2MAP_AGENT', 'A')) or 'A'


def _physics_options(physics, collision):
    if physics is None:
        return None
    if not isinstance(physics, dict) or set(physics)-{'mass', 'breakable'}:
        raise ModelError('physics must be {mass?: kilograms, breakable?: wood/glass/metal/null}')
    if collision != 'hull':
        raise ModelError("dynamic physics requires collision='hull'; mesh is for static environment")
    mass = physics.get('mass', 20)
    if isinstance(mass, bool):
        raise ModelError('physics.mass must be a positive number in kilograms')
    try:
        mass = float(mass)
    except (ValueError, TypeError):
        raise ModelError('physics.mass must be a positive number in kilograms') from None
    if not math.isfinite(mass) or not .01 <= mass <= 100000:
        raise ModelError('physics.mass must be between 0.01 and 100000 kilograms')
    breakable = physics.get('breakable')
    if breakable not in (None, 'wood', 'glass', 'metal'):
        raise ModelError('physics.breakable must be wood/glass/metal/null')
    return {'mass': mass, 'breakable': breakable}


def run_blender(project, script: str | Path, args: list[str] | None = None,
                timeout: float = 600) -> Job:
    """Run a Blender Python script as a background job (see cs2map.blender_run).

    ``script`` may be a file path or Python source text.  Scripts should print
    one ``JSON>...`` line; the decoded object is copied to ``job.result``.
    """
    script_path = _script_file(script)
    if script_path is None:
        scratch = PROJECT_ROOT / ".local" / "scratch" / _agent()
        scratch.mkdir(parents=True, exist_ok=True)
        script_path = scratch / f"blender_{uuid.uuid4().hex[:10]}.py"
        script_path.write_text(str(script), encoding="utf-8")
    try:
        return blender_run.start(project.s, project.jobs, script_path, args, timeout=timeout)
    except blender_run.BlenderError as e:
        raise ModelError(str(e)) from None


def _model_dir(s: Settings, name: str) -> Path:
    check_name(name, "model name")
    root = ensure_inside(s.content_addon / 'models' / 'mcp', s.content_addon)
    return ensure_inside(root / name, root)


def _writable_model_dir(s: Settings, name: str) -> Path:
    """Folder model_build/model_import may write: new, or holding only what they make."""
    folder = _model_dir(s, name)
    if folder.is_dir() and not (folder / 'model.json').is_file():
        foreign = [p.name for p in folder.iterdir() if p.name != 'sources' and p.stem != name]
        if foreign:
            raise ModelError(f'models/mcp/{name} holds files not made by model_build '
                             f'({", ".join(sorted(foreign)[:3])}); choose another name')
    return folder


def _script_file(script) -> Path | None:
    if isinstance(script, str) and ('\n' in script or '\r' in script):
        return None
    try:
        path = Path(script)
        return path.resolve() if path.is_file() else None
    except (OSError, ValueError, TypeError):
        return None


def _write_params(params: dict[str, Any] | None) -> Path:
    path = PROJECT_ROOT / ".local" / "scratch" / _agent() / f"params_{uuid.uuid4().hex[:10]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(params or {}, ensure_ascii=False), encoding="utf-8")
    return path


def _lod_request(project, name: str, lods) -> list[dict]:
    """Checked LOD levels: [{switch, reduce} | {switch, model}]. None keeps what the model had (a rebuild must not
    drop hand-set LODs, 14.10); [] removes them."""
    if lods is None:
        try:
            return json.loads((_model_dir(project.s, name) / 'model.json').read_text(encoding='utf-8')).get('lod_request', [])
        except (OSError, ValueError):
            return []
    if not isinstance(lods, list) or len(lods) > 4:
        raise ModelError('lods: up to 4 levels [{"switch": distance, "reduce": 0.5} or {"switch": d, "model": name}]')
    out, last = [], 0.0
    for lod in lods:
        if not isinstance(lod, dict) or set(lod) - {'switch', 'reduce', 'model'} or ('reduce' in lod) == ('model' in lod):
            raise ModelError('each LOD is {"switch": distance, "reduce": 0.02..0.95} or {"switch": distance, "model": '
                             'an own model built before (hand-made level)}')
        switch = lod.get('switch')
        if isinstance(switch, bool) or not isinstance(switch, (int, float)) or not last < switch <= 32768:
            raise ModelError('LOD switch distances must grow, each above the last and at most 32768 units')
        last = float(switch)
        if 'reduce' in lod:
            r = lod['reduce']
            if isinstance(r, bool) or not isinstance(r, (int, float)) or not .02 <= r <= .95:
                raise ModelError('LOD reduce is the share of triangles kept, 0.02..0.95')
            out.append({'switch': last, 'reduce': float(r)})
        else:
            check_name(lod['model'], 'LOD model')
            meta = _model_dir(project.s, lod['model']) / 'model.json'
            if lod['model'] == name or not meta.is_file():
                raise ModelError(f'LOD model {lod["model"]!r}: build it first with model_build (not this model itself)')
            out.append({'switch': last, 'model': lod['model']})
    return out


def _lod_parts(project, name: str, request: list[dict], metadata: dict) -> list[dict]:
    """Each level's mesh file, triangles and remaps (an own model's level brings its materials)."""
    made = {f['level']: f for f in metadata.get('lod_files', [])}
    base = metadata.get('render_file', f'models/mcp/{name}/{name}.fbx')
    parts = []
    for level, lod in enumerate(request, 1):
        if 'reduce' in lod:
            f = made[level]
            parts.append({'switch': lod['switch'], 'file': str(PurePosixPath(base).with_name(f['file'])),
                          'tris': f['tris'], 'remaps': []})
        else:
            other = json.loads((_model_dir(project.s, lod['model']) / 'model.json').read_text(encoding='utf-8'))
            parts.append({'switch': lod['switch'], 'file': other['render_file'], 'tris': other.get('tris', 0),
                          'remaps': other.get('remaps', []), 'model': lod['model']})
    return parts


def _run_blender_model(project, mode: str, name: str, script_or_source: str | Path,
                       collision: str, params: dict[str, Any] | None, physics=None,
                       weapon: dict | None = None, lods: list[dict] | None = None) -> tuple[Job, Path, dict]:
    if collision not in ("hull", "mesh", "none"):
        raise ModelError("collision must be 'hull', 'mesh', or 'none'")
    out_dir = _model_dir(project.s, name) / 'sources' / uuid.uuid4().hex[:12]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_fbx = out_dir / f"{name}.fbx"
    values = dict(params or {})
    values['__cs2map_physics'] = physics or {}
    values['__cs2map_lods'] = [[i, lod['reduce']] for i, lod in enumerate(lods or [], 1) if 'reduce' in lod]
    if weapon:
        values['__cs2map_weapon'] = True
    param_file = _write_params(values)
    if mode == "build":
        script_path = _script_file(script_or_source)
        if script_path is None:
            script_path = out_dir / 'script.py'
            script_path.write_text(str(script_or_source), encoding="utf-8")
        else:
            target = out_dir / 'script.py'
            shutil.copy2(script_path, target)
        args = ["build", str(script_path), str(out_fbx), collision, str(param_file)]
    else:
        args = ["import", str(script_or_source), str(out_fbx), collision, str(param_file)]
    job = run_blender(project, BLENDER_DIR / "driver.py", args)
    while True:
        state = project.jobs.wait(job.id, 1.0)
        if state.state != "running":
            break
    if state.state != "succeeded" or not state.result.get("ok", False) or not out_fbx.exists():
        raise ModelError(f"Blender {mode} failed; see {state.log}: {state.result}")
    state.result['render_file'] = project.s.addon_relative(out_fbx)
    state.result['collision_files'] = [project.s.addon_relative(out_dir / f)
                                       for f in state.result.get('collision_files', [])]
    state.result['break_files'] = [project.s.addon_relative(out_dir / f)
                                  for f in state.result.get('break_files', [])]
    if weapon:
        state.result['weapon_tris'] = str(out_dir / state.result['weapon_tris'])
    return state, out_fbx, state.result


def _safe_material_name(raw: str, used: set[str]) -> str:
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", raw).strip("_").lower() or "model_material"
    base = base[:48]
    name = base
    i = 2
    while name in used:
        name = f"{base}_{i}"
        i += 1
    used.add(name)
    return name


def _materials_for_model(project, metadata: dict, surface: str, name: str = 'unit') -> tuple[list[Path], list[dict]]:
    vmats: list[Path] = []
    remaps: list[dict] = []
    used: set[str] = set()
    for item in metadata.get("materials", []):
        if isinstance(item, str):
            item = {"name": item, "image": ""}
        raw = str(item.get("name") or "material")
        # An explicit Source 2 path is kept verbatim.
        target = item.get('material')
        token = item.get('token', raw)
        normalized = raw.replace("\\", "/")
        # Stock VPK materials may live under ``materials/``, ``models/`` or
        # ``gloves/``.  Validate the relative path using its first segment;
        # generated add-on materials still go through the normal branch below.
        if target or (normalized.lower().endswith(".vmat") and "/" in normalized):
            target = target or raw.replace("\\", "/")
            from .ext.models import asset_path
            prefix = PurePosixPath(target).parts[0] if PurePosixPath(target).parts else "materials"
            target = asset_path(project, target, '.vmat', prefix)
            remaps.append({"from": f'{token}.vmat', "to": target})
            continue
        image = str(item.get("image") or "")
        if image and not Path(image).is_file():
            raise ModelError(f'missing material image: {image}')
        # Prefix and hash prevent separate models from overwriting one another.
        import hashlib
        prefix = name[:36]
        mat_name = f'{prefix}_{hashlib.sha256(raw.encode()).hexdigest()[:12]}'
        color = item.get('color', [.5, .5, .5])
        def to_srgb(v):
            v = max(0.0, min(1.0, float(v)))
            return round(255 * (12.92*v if v <= .0031308 else 1.055*v**(1/2.4)-.055))
        made = materials.create(project.s, mat_name, image=image or None,
                                pattern={"pattern": "solid", "color": [to_srgb(v) for v in color[:3]], 'size': 64},
                                roughness=float(item.get('roughness', .7)), metalness=float(item.get('metalness', 0)),
                                normal_strength=0, surface=surface, folder="mcp_models")
        vmat = project.s.content_addon / made["material"]
        vmats.append(vmat)
        remaps.append({"from": f"{token}.vmat", "to": made["material"]})
    return vmats, remaps


def _make_vmdl(project, name: str, metadata: dict, collision: str, surface: str,
               physics: dict | None = None) -> tuple[Path, list[Path], list[dict]]:
    out_dir = _model_dir(project.s, name)
    out_dir.mkdir(parents=True, exist_ok=True)
    fbx_rel = metadata.get('render_file', f"models/mcp/{name}/{name}.fbx")
    vmats, remaps = _materials_for_model(project, metadata, surface, name)
    lods = metadata.get('lods', [])
    meshes = [(name, fbx_rel)] + [(f'{name}_lod{i}', lod['file']) for i, lod in enumerate(lods, 1)]
    children: list[dict] = [{"_class": "RenderMeshList", "children": [{
        "_class": "RenderMeshFile", "name": mesh_name, "filename": file,
        "import_scale": 0.0254, "import_filter": {"exclude_by_default": False, "exception_list": []}
    } for mesh_name, file in meshes]}]
    if lods:
        # One group per level, switched by distance (the layout resourcecompiler accepted for an own SUV, 14.10).
        children.append({'_class': 'LODGroupList', 'children': [
            {'_class': 'LODGroup', 'name': f'lod{i}', 'switch_threshold': float(switch),
             'mesh_references': [{'mesh_name': mesh_name, 'copy_and_simplify': False}]}
            for i, ((mesh_name, _), switch) in enumerate(zip(meshes, [0.0] + [lod['switch'] for lod in lods]))]})
        seen = {r['from'] for r in remaps}
        remaps = remaps + [r for lod in lods for r in lod['remaps'] if r['from'] not in seen and not seen.add(r['from'])]
    if remaps:
        children.append({"_class": "MaterialGroupList", "children": [{
            "_class": "DefaultMaterialGroup", "remaps": remaps,
            "use_global_default": False, "global_default_material": ""
        }]})
    if collision != "none":
        physics_class = "PhysicsHullFile" if collision == "hull" else "PhysicsMeshFile"
        collision_files = metadata.get('collision_files', [f'models/mcp/{name}/{name}_col_0.fbx'])
        children.append({"_class": "PhysicsShapeList", "children": [{
            "_class": physics_class, "name": f"{name}_col_{i}", "filename": file,
            "surface_prop": surface, "collision_prop": "default", "import_scale": 0.0254,
            "import_filter": {"exclude_by_default": False, "exception_list": []}
        } for i, file in enumerate(collision_files)]})
    if physics is not None:
        # ModelDoc editor's installed schema: PhysicsBodyMarkupList, mass_override.
        children.append({'_class': 'PhysicsBodyMarkupList', 'children': [{
            '_class': 'PhysicsBodyMarkup', 'target_body': '', 'mass_override': physics['mass'],
            'inertia_scale': 1.0}]})
        kind = physics['breakable']
        keys = {'allowstatic': True, 'spawn_motion_disabled': False, 'physicsmode': 1,
                'health': 40 if kind == 'wood' else 10 if kind == 'glass' else 80 if kind == 'metal' else 0,
                'dmg.bullets': 1.0, 'dmg.club': 1.0, 'dmg.explosive': 1.0}
        if kind:
            # CS2's propdata.vdata uses Wooden.* (the legacy Wood.Crate name
            # is absent and produces a runtime warning for custom models).
            keys['base'] = {'wood': 'Wooden.Medium', 'glass': 'Glass.Window', 'metal': 'Metal.break'}[kind]
        children.append({'_class': 'GameDataList', 'children': [{
            '_class': 'GenericGameData', 'game_class': 'prop_data', 'game_keys': keys}]})
        if kind:
            # Blender clips actual render geometry; each fragment gets a hull.
            # FBX keeps world placement, avoiding guessed offsets.
            pieces = []
            for i, file in enumerate(metadata.get('break_files', [])):
                pieces.append({'_class': 'BreakPieceEmbedded', 'name': f'fragment_{i}',
                    'children': [
                        {'_class': 'RenderMeshFile', 'name': f'fragment_{i}', 'filename': file,
                         'import_scale': .0254, 'import_filter': {'exclude_by_default': False, 'exception_list': []}},
                        {'_class': 'PhysicsHullFile', 'filename': file, 'import_scale': .0254,
                         'surface_prop': kind, 'collision_prop': 'default',
                         'import_filter': {'exclude_by_default': False, 'exception_list': []}}],
                    'placement_mode': 'default', 'spawn_entity': 'prop_physics_override',
                    'collision_group': 'debris', 'fadetime': 15.0, 'random_spawn_chance': 1.0,
                    'piece_motion_disabled': False, 'piece_health': 0, 'surface_prop_override': kind,
                    'render_color_mode': 'inherit_owner', 'material_group_mode': 'inherit_owner_index'})
            if not pieces:
                raise ModelError('breakable model requires exported convex collision fragments')
            children.append({'_class': 'BreakPieceList', 'children': pieces})
    doc = {"rootNode": {"_class": "RootNode", "children": children,
                         "model_archetype": "", "primary_associated_entity": "", "anim_graph_name": ""}}
    vmdl = out_dir / f"{name}.vmdl"
    if vmdl.exists():
        history = out_dir / 'history'
        history.mkdir(exist_ok=True)
        shutil.copy2(vmdl, history / f'{uuid.uuid4().hex[:12]}.vmdl')
    vmdl.write_text(kv3.dumps(doc, header=kv3.HEADER_MODELDOC), encoding="utf-8")
    return vmdl, vmats, remaps


def _make_weapon_vmdl(project, name: str, metadata: dict, surface: str, weapon: dict) -> tuple[Path, list[Path], list[dict]]:
    """A weapon model on the skeleton of a stock gun (cs2map.weapon_rig): held in the hands like the base."""
    from . import weapon_rig
    out_dir = _model_dir(project.s, name)
    out_dir.mkdir(parents=True, exist_ok=True)
    vmats, remaps = _materials_for_model(project, metadata, surface, name)
    old = out_dir / f"{name}.vmdl"
    if old.exists():
        history = out_dir / 'history'
        history.mkdir(exist_ok=True)
        shutil.copy2(old, history / f'{uuid.uuid4().hex[:12]}.vmdl')
    tris = json.loads(Path(metadata['weapon_tris']).read_text(encoding='utf-8'))
    vmdl, notes = weapon_rig.rig(project, name, out_dir, tris, weapon, remaps, surface)
    metadata['weapon'] = notes
    return vmdl, vmats, remaps


def _compile_model(project, vmdl: Path, vmats: list[Path]) -> dict:
    if not project.s.resourcecompiler.exists():
        return {"ok": False, "skipped": True, "reason": f"resourcecompiler not found: {project.s.resourcecompiler}"}
    compiled_file = project.s.game_addon / (project.s.addon_relative(vmdl) + '_c')
    before = compiled_file.stat().st_mtime_ns if compiled_file.exists() else None
    job = compiler.compile_assets(project.s, project.jobs, [*vmats, vmdl], force=True)
    st = project.job_status(job.id, 50, 0)
    while st["state"] == "running":
        st = project.job_status(job.id, 50, 0)
    info = dict(st.get('result') or {})
    # Valve prints this benign diagnostic when remaps supply the material.
    benign = [ln for ln in info.get('errors', []) if ln.startswith('GetFbxMaterialPath Failed!')]
    info['errors'] = [ln for ln in info.get('errors', []) if ln not in benign]
    info['error_count'] = len(info['errors'])
    fresh = compiled_file.exists() and compiled_file.stat().st_mtime_ns != before
    info['ok'] = bool(info.get('ok') and st['state'] == 'succeeded' and fresh and not info['errors']
                      and not info.get('missing_assets'))
    return {"job": job.id, "state": st["state"], 'exit_code': st['exit_code'], 'log': st['log'],
            'output': str(compiled_file) if fresh else None, **info}


def _result(project, name: str, metadata: dict, collision: str, surface: str, vmdl: Path,
            compile_result: dict, preview: str | None = None) -> dict:
    # Keep the MCP response useful to a model caller.  The complete compiler
    # output remains in ``compile_result.log``; only its path and outcome are
    # returned here.
    compile_public = {"ok": bool(compile_result.get("ok")),
                      "exit_code": compile_result.get("exit_code"),
                      "log": compile_result.get("log"),
                      "output": compile_result.get("output")}
    if compile_result.get("skipped"):
        compile_public.update({"skipped": True, "reason": compile_result.get("reason")})
    if compile_result.get("errors"):
        compile_public["errors"] = compile_result["errors"][:8]
    materials_out = [str(item.get("to")) for item in metadata.get("remaps", [])
                     if isinstance(item, dict) and item.get("to")]
    result = {"ok": bool(compile_result.get("ok")),
              "model": f"models/mcp/{name}/{name}.vmdl", "bounds": metadata.get("bounds"),
              "tris": int(metadata.get("tris", 0)), "materials": materials_out,
              "collision": collision, "compile": compile_public, "preview": preview}
    result['warnings'] = []
    if metadata.get('physics') is not None:
        result['physics'] = {**metadata['physics'], 'fragments': len(metadata.get('break_files', []))}
    if metadata.get('weapon'):
        result['weapon'] = metadata['weapon']
    if any(item.get('material') for item in metadata.get('materials', []) if isinstance(item, dict)):
        result['preview_note'] = 'Blender preview shows shape and colors; stock VMAT textures are visible in CS2.'
    if compile_result.get('warnings'):
        result['warnings'].extend(compile_result['warnings'][:4])
    if compile_result.get('missing_assets'):
        compile_public['missing_assets'] = compile_result['missing_assets'][:8]
    if metadata.get('implicit_collision') and not metadata.get('weapon'):
        result['warnings'].append('collision uses render geometry; provide simpler *_col meshes for large props')
    if metadata.get('lods'):
        result['lods'] = [{'switch': lod['switch'], 'tris': lod['tris'], **({'model': lod['model']} if 'model' in lod
                                                                            else {})} for lod in metadata['lods']]
        # Measured 25.10: CS2 draws level 0 at every distance (prop_static and prop_dynamic, 500-3300 units).
        result["warnings"].append("CS2 does not switch LOD levels by distance now (measured 25.10): the levels "
                                  "save nothing in today's game; keep the full model's triangles low")
    if result["tris"] > 50000:
        result["warnings"].append("more than 50,000 triangles: simplify before filling a map with this model "
                                  "(CS2 does not switch LOD levels by distance)")
    data = {**metadata, **result, 'surface': surface, 'name': name}
    data['blender_materials'] = metadata.get('materials', [])
    (_model_dir(project.s, name) / 'model.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
    return result


def model_build(project, name: str, script: str | None = None, template: str | None = None,
                params: dict[str, Any] | None = None, collision: str = "hull", surface: str = "default",
                physics: dict | None = None, weapon: dict | str | None = None, lods: list | None = None) -> dict:
    """Build a model from an inline/path Blender script or a stock template (weapon: on a stock gun's skeleton)."""
    if weapon is not None:
        from . import weapon_rig
        weapon = weapon_rig.validate(weapon)
        if physics is not None:
            raise ModelError('a weapon model takes no physics: the stock weapon entity carries it')
    physics = _physics_options(physics, collision)
    _writable_model_dir(project.s, name)
    try:
        surface = materials.surface_name(surface)
    except ValueError as e:
        raise ModelError(str(e)) from None
    if params is not None and not isinstance(params, dict):
        raise ModelError('params must be a JSON object')
    if script and template:
        raise ModelError("pass either script or template, not both")
    if template:
        check_name(template, "template")
        if template not in TEMPLATES:
            raise ModelError(f"unknown template {template!r}; choose one of {TEMPLATES}")
        script = str(TEMPLATES_DIR / f"{template}.py")
    if not script:
        template = "crate"
        script = str(TEMPLATES_DIR / "crate.py")
    request = [] if weapon else _lod_request(project, name, lods)
    state, _, metadata = _run_blender_model(project, "build", name, script, collision, params, physics, weapon, request)
    metadata['lod_request'] = request
    metadata['lods'] = _lod_parts(project, name, request, metadata)
    if physics is not None:
        metadata['physics'] = physics
    if weapon:
        vmdl, vmats, metadata['remaps'] = _make_weapon_vmdl(project, name, metadata, surface, weapon)
    else:
        vmdl, vmats, metadata['remaps'] = _make_vmdl(project, name, metadata, collision, surface, physics)
    compiled = _compile_model(project, vmdl, vmats)
    _result(project, name, metadata, collision, surface, vmdl, compiled)
    preview = None
    try:
        preview = model_preview(project, name, ["iso"], 512).get("preview")
    except Exception as exc:
        preview = None
        compiled.setdefault("warnings", []).append(f"preview failed: {exc}")
    return _result(project, name, metadata, collision, surface, vmdl, compiled, preview)


def model_import(project, name: str, file: str, scale: str | float = "auto",
                 collision: str = "hull", surface: str = "default", physics: dict | None = None,
                 weapon: dict | str | None = None, lods: list | None = None) -> dict:
    """Normalize an FBX/OBJ/GLB import and build its CS2 model asset (weapon: on a stock gun's skeleton)."""
    if weapon is not None:
        from . import weapon_rig
        weapon = weapon_rig.validate(weapon)
        if not weapon.get('grip'):
            raise ModelError('an imported weapon needs weapon.grip [x,y,z] (inches after import; see model_preview)')
    physics = _physics_options(physics, collision)
    _writable_model_dir(project.s, name)
    try:
        surface = materials.surface_name(surface)
    except ValueError as e:
        raise ModelError(str(e)) from None
    own = re.fullmatch(r'models/(mcp[a-z0-9_]*)/([a-z0-9_]+)/\2\.vmdl', str(file).replace('\\', '/'))
    if own:
        if physics is not None or weapon is not None or lods is not None or collision != 'hull':
            raise ModelError('a scaled copy keeps the model\'s own collision, physics and LODs: pass only scale')
        return _scaled_copy(project, name, own.group(0), scale)
    source = Path(file)
    if not source.is_file():
        raise ModelError(f"model file not found: {file}")
    if source.suffix.lower() not in (".fbx", ".obj", ".glb", ".gltf", ".blend"):
        raise ModelError("file must be .fbx, .obj, .glb, .gltf or .blend")
    if scale != "auto":
        try:
            scale = float(scale)
        except (TypeError, ValueError):
            raise ModelError("scale must be 'auto' or a positive number") from None
        if not math.isfinite(scale) or scale <= 0 or scale > 1e6:
            raise ModelError("scale must be between 0 and 1000")
    request = [] if weapon else _lod_request(project, name, lods)
    state, _, metadata = _run_blender_model(project, "import", name, source, collision, {"scale": scale}, physics,
                                            weapon, request)
    metadata['lod_request'] = request
    metadata['lods'] = _lod_parts(project, name, request, metadata)
    if physics is not None:
        metadata['physics'] = physics
    if weapon:
        vmdl, vmats, metadata['remaps'] = _make_weapon_vmdl(project, name, metadata, surface, weapon)
    else:
        vmdl, vmats, metadata['remaps'] = _make_vmdl(project, name, metadata, collision, surface, physics)
    compiled = _compile_model(project, vmdl, vmats)
    _result(project, name, metadata, collision, surface, vmdl, compiled)
    preview = model_preview(project, name, ['iso'], 512).get('preview')
    return _result(project, name, metadata, collision, surface, vmdl, compiled, preview)


def _scaled_copy(project, name: str, rel: str, scale) -> dict:
    """An own model `scale` times bigger under a new name: the same FBX sources with every import_scale (render
    meshes, LOD levels, collision, fragments) and LOD distance multiplied, nothing re-exported (23.10: the
    a 1.2x SUV was a hand-edited copy). rel: models/mcp*/<name>/<name>.vmdl in the addon (also the
    generated vehicle bodies of models/mcp_v)."""
    source = PurePosixPath(rel).stem
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or not .05 <= scale <= 20:
        raise ModelError('a scaled copy needs scale 0.05..20 (a number, not "auto")')
    if source == name:
        raise ModelError('a scaled copy needs a new name')
    folder = ensure_inside(project.s.content_addon / PurePosixPath(rel).parent, project.s.content_addon)
    try:
        header, doc = kv3.loads((folder / f'{source}.vmdl').read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise ModelError(f'own model {rel} not found: build it first ({e})') from None
    try:
        meta = json.loads((folder / 'model.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        meta = {}                       # generated vehicle bodies (models/mcp_v) keep no model.json
    if meta.get('weapon'):
        raise ModelError('weapon models are not scaled copies: rebuild with weapon.scale')

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get('import_scale'), (int, float)):
                node['import_scale'] = node['import_scale'] * scale
            if node.get('_class') == 'LODGroup':
                node['switch_threshold'] = float(node['switch_threshold']) * scale
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(doc)
    out_dir = _writable_model_dir(project.s, name)
    out_dir.mkdir(parents=True, exist_ok=True)
    vmdl = out_dir / f'{name}.vmdl'
    if vmdl.exists():
        (out_dir / 'history').mkdir(exist_ok=True)
        shutil.copy2(vmdl, out_dir / 'history' / f'{uuid.uuid4().hex[:12]}.vmdl')
    vmdl.write_text(kv3.dumps(doc, header=header), encoding='utf-8')
    compiled = _compile_model(project, vmdl, [])
    data = {**meta, 'name': name, 'scaled_from': source, 'scale': scale, 'lod_request': [],
            'bounds': [[c * scale for c in corner] for corner in meta.get('bounds') or [[0, 0, 0], [0, 0, 0]]],
            'lods': [{**lod, 'switch': lod['switch'] * scale} for lod in meta.get('lods', [])]}
    result = {'ok': bool(compiled.get('ok')), 'model': f'models/mcp/{name}/{name}.vmdl', 'scaled_from': source,
              'scale': scale, 'bounds': data['bounds'], 'tris': meta.get('tris', 0),
              'compile': {k: compiled.get(k) for k in ('ok', 'exit_code', 'log', 'output')}, 'warnings': []}
    if compiled.get('errors'):
        result['compile']['errors'] = compiled['errors'][:8]
    if meta.get('physics'):
        result['warnings'].append('mass is kept from the source model: pass a physics mass in the entity if it '
                                  'should change')
    data.update({k: v for k, v in result.items() if k not in ('warnings',)})
    (out_dir / 'model.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
    return result


def _model_source(project, name: str) -> Path:
    check_name(name, "model name")
    folder = _model_dir(project.s, name)
    metadata_path = folder / 'model.json'
    metadata = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else {}
    fbx = Path(metadata.get('blend', folder / f"{name}.fbx"))
    ensure_inside(fbx, folder)
    if not fbx.exists():
        raise ModelError(f"model {name!r} has no source FBX; build or import it first")
    return fbx


def model_preview(project, name: str, views: list[str] | None = None, size: int = 512,
                  reference: str | None = None) -> dict:
    """Render a model source FBX to a PNG; optionally put a reference beside it."""
    if not 64 <= int(size) <= 2048:
        raise ModelError("size must be between 64 and 2048")
    source = _model_source(project, name)
    views = views if views is not None else ['front', 'side', 'top', 'iso']
    if not isinstance(views, list) or not views or len(views) > 4 or len(set(views)) != len(views) or any(v not in ('front','side','top','iso') for v in views):
        raise ModelError('views must be 1..4 unique names: front, side, top, iso')
    if reference and not Path(reference).is_file():
        raise ModelError(f'reference image not found: {reference}')
    out = PROJECT_ROOT / "screenshots" / f"{_agent().lower()}_model_{name}-{uuid.uuid4().hex[:8]}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    job = run_blender(project, BLENDER_DIR / "preview.py", [str(source), str(out), str(int(size)), json.dumps(views)], timeout=180)
    while True:
        state = project.jobs.wait(job.id, 1)
        if state.state != 'running':
            break
    previews = state.result.get('previews', {})
    if state.state != "succeeded" or set(previews) != set(views) or not all(Path(p).is_file() for p in previews.values()):
        raise ModelError(f"preview failed; see {state.log}")
    from PIL import Image, ImageOps
    if len(views) == 1:
        final = Path(previews[views[0]])
    else:
        columns = min(len(views), 2)
        rows = (len(views) + columns-1)//columns
        canvas = Image.new('RGB', (int(size)*columns, int(size)*rows), (30,30,30))
        for i, view in enumerate(views):
            with Image.open(previews[view]) as tile:
                canvas.paste(tile.convert('RGB'), (int(size)*(i%columns), int(size)*(i//columns)))
        canvas.save(out)
        final = out
    if reference:
        ref = Path(reference)
        if not ref.is_file():
            raise ModelError(f"reference image not found: {reference}")
        with Image.open(ref).convert("RGBA") as a, Image.open(final).convert("RGBA") as b:
            w, h = int(size), int(size)
            a = ImageOps.contain(a, (w, h)); b = ImageOps.contain(b, (w, h))
            canvas = Image.new("RGBA", (w * 2, h), (25, 25, 25, 255))
            canvas.alpha_composite(a, ((w - a.width)//2, (h - a.height)//2))
            canvas.alpha_composite(b, (w + (w - b.width)//2, (h - b.height)//2))
            final = out.with_name(out.stem + "_reference.png")
            canvas.save(final)
    return {"model": f"models/mcp/{name}/{name}.vmdl", "preview": str(final),
            'previews': previews, "views": views, "size": int(size), "reference": reference}


def model_list(project) -> dict:
    root = project.s.content_addon / "models" / "mcp"
    models = []
    if root.is_dir():
        for p in sorted(root.glob("*/*.vmdl")):
            models.append({"name": p.parent.name, "model": project.s.addon_relative(p)})
    return {"models": models, "count": len(models)}


def model_info(project, name: str) -> dict:
    vmdl = _model_dir(project.s, name) / f"{name}.vmdl"
    if not vmdl.exists():
        raise ModelError(f"model {name!r} does not exist")
    header, data = kv3.loads(vmdl.read_text(encoding="utf-8"))
    metadata_file = _model_dir(project.s, name) / 'model.json'
    metadata = json.loads(metadata_file.read_text(encoding='utf-8')) if metadata_file.exists() else {}
    fbx = project.s.content_addon / metadata.get('render_file', f'models/mcp/{name}/{name}.fbx')
    return {**metadata, "name": name, "model": project.s.addon_relative(vmdl), "source": str(fbx),
            "source_exists": fbx.exists(), "vmdl_header": header, "document": data}
