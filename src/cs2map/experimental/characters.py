"""Character import and player-model build pipeline.

The character manifest is the source of truth.  Blender is an optional build
step: manifests, bone maps, ModelDoc files and scripts remain useful on a
machine without Blender, while a present Blender installation performs the
FBX conversion and writes a machine-readable report.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Iterable

from ..config import PROJECT_ROOT, check_name, ensure_inside, load_settings
from .. import blender_run, materials
from ..jobs import JobManager
from ..kv3 import HEADER_MODELDOC, dumps as kv3_dumps

CHARACTERS = PROJECT_ROOT / "resources" / "characters"
BONEMAPS = PROJECT_ROOT / "src" / "cs2map" / "experimental" / "blender_chars" / "bonemaps"
SKELETON = PROJECT_ROOT / "src" / "cs2map" / "experimental" / "blender_chars" / "cs2_skeleton.json"
AGENT_MODELDOC_NODES = PROJECT_ROOT / "src" / "cs2map" / "experimental" / "blender_chars" / "modeldoc_agent_nodes.json"
DEFAULT_ANIM_GRAPH = "animation/graphs/worldmodel/worldmodel.vnmgraph"

_NAME = re.compile(r"^[a-z0-9_]{1,64}$")


class CharacterError(ValueError):
    """Invalid manifest, source or character build request."""


def _json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_path(name: str) -> Path:
    check_name(name, "character name")
    return ensure_inside(CHARACTERS / name / "character.json", CHARACTERS)


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def available_bone_maps() -> list[str]:
    return sorted(p.stem for p in BONEMAPS.glob("*.json")) if BONEMAPS.is_dir() else []


def bone_map(name: str) -> dict[str, Any]:
    check_name(name, "bone map")
    data = _json(BONEMAPS / f"{name}.json")
    if not isinstance(data, dict):
        raise CharacterError(f"unknown bone map {name!r}; available: {available_bone_maps()}")
    return data


def detect_bone_map(bones: Iterable[str]) -> dict[str, Any]:
    from .blender_chars.mapping import resolve
    result = resolve(list(bones))
    return {k:result[k] for k in ('preset','scores','roles','unmapped_bones')}


def _validate_manifest(data: dict[str, Any], name: str | None = None) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise CharacterError("character manifest must be a JSON object")
    n = data.get("name", name)
    check_name(n, "character name")
    if name and n != name:
        raise CharacterError(f"manifest name {n!r} does not match directory {name!r}")
    source = data.get("source")
    if not source or not isinstance(source, str):
        raise CharacterError("manifest needs a source FBX path")
    h = data.get("height", 72)
    if not isinstance(h, (int, float)) or not 1 <= float(h) <= 512:
        raise CharacterError("height must be between 1 and 512 Source units")
    model_scale = data.get("model_scale", 1.0)
    if not isinstance(model_scale, (int, float)) or not 0.25 <= float(model_scale) <= 2.0:
        raise CharacterError("model_scale must be between 0.25 and 2.0")
    preset = data.get("bone_map", "auto")
    if preset != "auto" and preset not in available_bone_maps():
        raise CharacterError(f"unknown bone_map {preset!r}; available: {available_bone_maps()}")
    if data.get('forward','auto') not in ('auto','+x','-x','+y','-y'):
        raise CharacterError('forward must be auto, +x, -x, +y or -y')
    for key, spec in data.get('materials',{}).items():
        check_name(key,'material key')
        check_name(f'{n}_{key}','generated material name')
        if not isinstance(spec,dict): raise CharacterError('material entries must be objects')
        if spec.get('image') and not isinstance(spec['image'],str): raise CharacterError('material image must be a path')
        if spec.get('alpha','none') not in ('none','test','translucent'):
            raise CharacterError('material alpha must be none, test or translucent')
    extra_roles = data.get('extra_bone_roles', {})
    from .blender_chars.mapping import TARGETS
    if not isinstance(extra_roles, dict) or any(not isinstance(b, str) or not isinstance(r, str) or r not in TARGETS for b,r in extra_roles.items()):
        raise CharacterError('extra_bone_roles must map source bone names to humanoid roles')
    out = dict(data)
    out.update({"name": n, "height": float(h), "model_scale": float(model_scale), "bone_map": preset,
                "bone_map_overrides": dict(data.get("bone_map_overrides") or {}),
                "remove_objects": list(data.get("remove_objects") or []),
                "materials": dict(data.get("materials") or {})})
    return out


def load_manifest(name: str) -> dict[str, Any]:
    p = _manifest_path(name)
    if not p.exists():
        raise CharacterError(f"character {name!r} is not imported")
    return _validate_manifest(_json(p), name)


def list_characters() -> list[dict[str, Any]]:
    result = []
    if not CHARACTERS.is_dir():
        return result
    for p in sorted(CHARACTERS.iterdir()):
        if not p.is_dir() or not (p / "character.json").exists():
            continue
        try:
            m = load_manifest(p.name)
            report = _json(p / "build_report.json", {})
            result.append({"name": m["name"], "bone_map": m["bone_map"], "height": m["height"],
                           "built": bool(report.get("compile",{}).get("ok")), "report": report.get("status")})
        except (ValueError, OSError) as exc:
            result.append({"name": p.name, "error": str(exc)})
    return result


def _write_json(path: Path, value: Any) -> None:
    ensure_inside(path, CHARACTERS)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _model_doc(name, model_rel, material_remaps, anim_graph=DEFAULT_ANIM_GRAPH):
    import copy
    ref = _json(SKELETON)
    children = [
        {"_class":"RenderMeshList","children":[{"_class":"RenderMeshFile","name":name,
          "filename":model_rel,"import_scale":1.0,
          "import_filter":{"exclude_by_default":False,"exception_list":[]}}]},
        {"_class":"MaterialGroupList","children":[{"_class":"DefaultMaterialGroup",
          "remaps":material_remaps,"use_global_default":False,"global_default_material":""}]}]
    # Actual ModelDoc nodes extracted from the installed CS2 agent.  The
    # skeleton fixture contains the portable animation/hitbox nodes; the
    # physics/constraint nodes live in a separate fixture because they are
    # large and source-specific.  Keeping the extraction beside the skeleton
    # makes the source and the generated VMDL auditable without reading files
    # from the user's CS2 installation at build time.
    nodes = copy.deepcopy(ref['modeldoc_nodes'])
    extra = _json(AGENT_MODELDOC_NODES, {}).get('nodes', [])
    present = {node.get('_class') for node in nodes}
    nodes.extend(copy.deepcopy(node) for node in extra if node.get('_class') not in present)
    scale = float(_json(CHARACTERS / name / "character.json", {}).get("model_scale", 1.0))
    if scale != 1.0:
        def scale_hitboxes(value):
            if isinstance(value, dict):
                for key, item in list(value.items()):
                    if key in ("radius",) and isinstance(item, (int, float)):
                        value[key] = item * scale
                    elif key in ("point0", "point1", "center", "relative_origin") and isinstance(item, list):
                        value[key] = [x * scale for x in item]
                    else:
                        scale_hitboxes(item)
            elif isinstance(value, list):
                for item in value: scale_hitboxes(item)
        scale_hitboxes(nodes)
    children += nodes
    return {"rootNode":{"_class":"RootNode","children":children,
            "model_archetype":"","primary_associated_entity":"","anim_graph_name":""}}


def _hitboxes():
    node = next(n for n in _json(SKELETON)['modeldoc_nodes'] if n['_class']=='HitboxSetList')
    return node['children'][0]['children']


def _material_remaps(manifest: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for source_name, spec in manifest.get("materials", {}).items():
        key = str(source_name)
        target = spec.get("material") if isinstance(spec, dict) else None
        target = target or f"materials/mcp_characters/{manifest['name']}_{key}/{manifest['name']}_{key}.vmat"
        out.append({"from": key if key.endswith(".vmat") else f"{key}.vmat", "to": target})
    return out


def _blender_script() -> Path:
    return PROJECT_ROOT / "src" / "cs2map" / "experimental" / "blender_chars" / "prepare.py"


def _run_blender(settings, source: Path, output: Path, manifest: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if not settings.blender_exe.exists():
        return {"status": "skipped", "reason": "Blender 4.5 not found"}, ""
    ensure_inside(output, settings.content_addon / "models/mcp_characters")
    output.parent.mkdir(parents=True, exist_ok=True)
    jobs = JobManager(settings.logs)
    job = blender_run.run(settings, jobs, _blender_script(),
                          [source, output, manifest["height"], json.dumps(manifest)],
                          title=f"Blender character {manifest.get('name', output.stem)}", timeout=600)
    tail = "\n".join(jobs.tail(job.id, 120))
    if job.state != "succeeded":
        return {"status": "failed", "returncode": job.exit_code, "job": job.id, "log": job.log}, tail
    result = {k: v for k, v in job.result.items() if k not in ("ok", "errors")}
    if not result:
        return {"status": "failed", "reason": "Blender did not emit JSON", "job": job.id}, tail
    # Keep the large edge table and heatmap in the character resource folder;
    # the compact build report then points to stable project-relative files.
    distortion = result.get("distortion")
    if isinstance(distortion, dict):
        resource_dir = CHARACTERS / manifest["name"]
        resource_dir.mkdir(parents=True, exist_ok=True)
        for key, filename in (("metrics_path", "distortion_metrics.json"),
                              ("heatmap_path", "distortion_heatmap.png")):
            source_path = Path(str(distortion.get(key, "")))
            if source_path.is_file():
                target_path = resource_dir / filename
                shutil.copy2(source_path, target_path)
                distortion[key] = _relative(target_path)
    return {"status": "ok", **result}, tail


def import_character(name: str, source: str, bone_map_name: str = "auto", height: float = 72,
                     textures_dir: str | None = None, overrides: dict[str, str] | None = None,
                     license_note: str = "") -> dict[str, Any]:
    check_name(name, "character name")
    src = Path(source).expanduser().resolve()
    if not src.is_file() or src.suffix.lower() != ".fbx":
        raise CharacterError(f"source FBX not found: {source}")
    if bone_map_name != "auto" and bone_map_name not in available_bone_maps():
        raise CharacterError(f"unknown bone map {bone_map_name!r}")
    manifest = _validate_manifest({"name": name, "source": _relative(src), "textures_dir": _relative(Path(textures_dir).resolve()) if textures_dir else "",
                                  "bone_map": bone_map_name, "bone_map_overrides": overrides or {}, "height": height,
                                  "forward": "auto", "remove_objects": [], "materials": {}, "license_note": license_note}, name)
    if _manifest_path(name).exists():
        raise CharacterError("Character exists; edit its manifest and use character_rebuild")
    folder = ensure_inside(CHARACTERS / name, CHARACTERS)
    folder.mkdir(parents=True, exist_ok=True)
    _write_json(folder / "character.json", manifest)
    return rebuild_character(name)


def rebuild_character(name: str) -> dict[str, Any]:
    manifest = load_manifest(name)
    settings = load_settings()
    source = Path(manifest["source"])
    if not source.is_absolute():
        source = PROJECT_ROOT / source
    if not source.is_file():
        raise CharacterError(f"manifest source not found: {source}")
    out_dir = ensure_inside(settings.content_addon / "models" / "mcp_characters" / name,
                            settings.content_addon / "models/mcp_characters")
    model_rel = f"models/mcp_characters/{name}/{name}.dmx"
    report: dict[str, Any] = {"status": "unverified", "name": name, "source": _relative(source), "warnings": [],
                              "bone_map": manifest["bone_map"], "overrides": manifest.get("bone_map_overrides", {}),
                              "retarget": {"status": "planned", "unmapped_bones": [], "weight_transfer": "nearest canonical bone"},
                              "animation": {"anim_graph": DEFAULT_ANIM_GRAPH, "status": "configured"},
                              "hitboxes": {"status": "unverified", "count": len(_hitboxes()), "groups": sorted({b["group_id"] for b in _hitboxes()})}}
    blender, log = _run_blender(settings, source, out_dir / f"{name}.fbx", manifest)
    report["blender"] = blender
    report["retarget"] = blender.get("retarget", {"status":"failed"})
    report["distortion"] = blender.get("distortion", {"status": "unavailable"})
    if log:
        (CHARACTERS / name / "blender.log").write_text(log, encoding="utf-8")
    if blender.get("status") == "skipped":
        report["warnings"].append(blender["reason"])
    if blender.get("status") == "failed":
        report["status"] = "partial"
        report["warnings"].append("Blender conversion failed; no new ModelDoc generated")
    if blender.get("status") != "ok":
        report["status"] = "failed"
        _write_json(CHARACTERS / name / "build_report.json", report)
        return report
    out_dir.mkdir(parents=True, exist_ok=True)
    material_outputs = []
    for mat_name, spec in manifest.get("materials", {}).items():
        image = spec.get("image") if isinstance(spec, dict) else None
        if not image:
            continue
        image_path = Path(manifest.get("textures_dir", "")) / image
        if not image_path.is_absolute():
            image_path = PROJECT_ROOT / image_path
        if not image_path.exists():
            report["warnings"].append(f"material image not found: {image_path}")
            continue
        made_name = f"{name}_{mat_name}"
        made = materials.create(settings, made_name, image=str(image_path), roughness=0.85,
                                surface="default", folder="mcp_characters", normal_strength=0.0,
                                alpha=spec.get('alpha','none'))
        material_outputs.append(made["material"])
    if material_outputs:
        report["materials"] = material_outputs
    vmdl = out_dir / f"{name}.vmdl"
    vmdl.write_text(kv3_dumps(_model_doc(name, model_rel, _material_remaps(manifest)), HEADER_MODELDOC), encoding="utf-8")
    report["outputs"] = {"vmdl": _relative(vmdl), "fbx": _relative(out_dir / f"{name}.fbx"), "dmx": _relative(out_dir / f"{name}.dmx"),
                         "report": _relative(CHARACTERS / name / "build_report.json")}
    _write_json(CHARACTERS / name / "build_report.json", report)
    return report


def character_info(name: str) -> dict[str, Any]:
    manifest = load_manifest(name)
    report = _json(CHARACTERS / name / "build_report.json", {})
    return {"manifest": manifest, "report": report, "bone_maps": available_bone_maps(),
            "skeleton": _json(SKELETON, {})}


def compile_character_assets(project, name: str) -> dict[str, Any]:
    """Compile the generated VMDL and character materials through resourcecompiler."""
    from . import compiler
    load_manifest(name)
    report = _json(CHARACTERS / name / "build_report.json", {})
    if report.get("blender",{}).get("status") != "ok":
        return report
    settings = project.s
    paths = []
    vmdl = settings.content_addon / "models" / "mcp_characters" / name / f"{name}.vmdl"
    if vmdl.exists():
        paths.append(vmdl)
    for rel in report.get("materials", []):
        p = settings.content_addon / rel
        if p.exists():
            paths.append(p)
    if not paths:
        report["compile"] = {"status": "skipped", "reason": "no generated assets"}
        _write_json(CHARACTERS / name / "build_report.json", report)
        return report
    job = compiler.compile_assets(settings, project.jobs, paths, force=True)
    state = project.job_status(job.id, 30, 0)
    while state['state']=='running': state=project.job_status(job.id,30,0)
    ok = (state['state']=='succeeded' and state['exit_code']==0
          and state['result'].get('ok') and not state['result'].get('errors')
          and not state['result'].get('missing_assets'))
    report["compile"] = {"job": job.id, "state": state["state"], "exit_code": state["exit_code"],
                         "result": state["result"], "ok": bool(ok), "log": state["log"]}
    report["status"] = "compiled_unverified" if ok else "failed"
    _write_json(CHARACTERS / name / "build_report.json", report)
    return report


def compact_build_result(report: dict[str, Any]) -> dict[str, Any]:
    """Return the small MCP response; full retarget data stays in build_report."""
    blender = report.get("blender") or {}
    compile_info = report.get("compile") or {}
    compile_result = compile_info.get("result") or {}
    distortion = report.get("distortion") or blender.get("distortion") or {}
    return {
        "ok": bool(compile_info.get("ok")),
        "name": report.get("name"),
        "status": report.get("status"),
        "blender": {"status": blender.get("status"), "bones": blender.get("bones"),
                    "meshes": blender.get("meshes"), "bounds": blender.get("bounds")},
        "compile": {"ok": bool(compile_info.get("ok")), "state": compile_info.get("state"),
                    "exit_code": compile_info.get("exit_code"), "summary": compile_result.get("summary"),
                    "log": compile_info.get("log")},
        "distortion": {"edge_count": distortion.get("edge_count"), "summary": distortion.get("summary"),
                       "zones": distortion.get("zones"), "metrics_path": distortion.get("metrics_path"),
                       "heatmap_path": distortion.get("heatmap_path")},
        "outputs": report.get("outputs", {}),
        "report": f"resources/characters/{report.get('name')}/build_report.json",
        "warnings": report.get("warnings", []),
    }


def render_character_preview(name: str, poses: list[str] | None = None) -> dict[str, Any]:
    """Render deterministic Blender previews of the rebuilt character."""
    manifest = load_manifest(name)
    report = _json(CHARACTERS / name / "build_report.json", {})
    settings = load_settings()
    blend = settings.content_addon / "models" / "mcp_characters" / name / f"{name}.blend"
    destination = ensure_inside(CHARACTERS / name / "preview", CHARACTERS)
    if not blend.is_file():
        return {"status": "missing", "poses": poses or ["rest", "run"], "reason": "rebuilt .blend not found"}
    if not settings.blender_exe.exists():
        return {"status": "skipped", "poses": poses or ["rest", "run"], "reason": "Blender 4.5 not found"}
    destination.mkdir(parents=True, exist_ok=True)
    requested = poses or ["rest", "run"]
    if any(p not in {"rest", "run"} for p in requested):
        raise CharacterError("preview poses must be 'rest' or 'run'")
    job = blender_run.run(settings, JobManager(settings.logs), PROJECT_ROOT / "src/cs2map/experimental/blender_chars/preview.py",
                          [blend, destination, json.dumps(requested), _manifest_path(name)],
                          title=f"Blender character preview {name}", timeout=300)
    payload = {k: v for k, v in job.result.items() if k not in ("ok", "errors")}
    if job.state != "succeeded" or not payload:
        result = {"status": "failed", "returncode": job.exit_code, "job": job.id, "log": job.log,
                  "errors": job.result.get("errors", [])}
    else:
        result = {"status": "ok", **payload}
    report["preview"] = result
    _write_json(CHARACTERS / name / "build_report.json", report)
    return result


def generate_script(name: str, t_names: list[str], ct_names: list[str], mode: str = "random") -> str:
    if mode not in ("random", "by_slot"):
        raise CharacterError("mode must be 'random' or 'by_slot'")
    for n in [*t_names, *ct_names]:
        check_name(n, "character name")
    models = {n: f"models/mcp_characters/{n}/{n}.vmdl" for n in [*t_names, *ct_names]}
    payload = json.dumps(models, ensure_ascii=False, separators=(",", ":"))
    t = json.dumps([models[n] for n in t_names], ensure_ascii=False)
    ct = json.dumps([models[n] for n in ct_names], ensure_ascii=False)
    chooser = ('function pick(list, pawn) { const slot = pawn.GetPlayerController()?.GetPlayerSlot() ?? 0; return list[slot % list.length] || ""; }'
               if mode == 'by_slot' else 'function pick(list, pawn) { return list[Math.floor(Math.random() * list.length)] || ""; }')
    return f'''import {{ Instance }} from "cs_script/point_script";

// Generated by cs2map experimental player models (not an MCP tool).  Models: {payload}
const T_MODELS = {t};
const CT_MODELS = {ct};
{chooser}
Instance.OnPlayerReset((event) => {{
  const pawn = event.player;
  if (!pawn || !pawn.IsValid()) return;
  const team = pawn.GetTeamNumber();
  if (team !== 2 && team !== 3) return;
  const list = team === 3 ? CT_MODELS : T_MODELS;
  const model = pick(list, pawn);
  if (model) pawn.SetModel(model);
}});
Instance.OnPlayerDamage((event) => {{
  Instance.Msg("mcp_characters_damage " + JSON.stringify({{damage:event.damage,hitGroup:event.hitGroup,
    model:event.player.GetModelName(),slot:event.player.GetPlayerController()?.GetPlayerSlot()}}));
}});
Instance.Msg("mcp_characters: loaded mode={mode}");
'''


def assign_characters(project, map_name: str, t_names: list[str], ct_names: list[str], mode: str = "random") -> dict[str, Any]:
    check_name(map_name, "map name")
    project._check_hammer(map_name, False)
    project.load(map_name)
    all_names = list(dict.fromkeys([*t_names, *ct_names]))
    for n in all_names:
        load_manifest(n)
        report = _json(CHARACTERS / n / 'build_report.json',{})
        if not report.get('compile',{}).get('ok'):
            raise CharacterError(f'Character {n} has no clean compilation; rebuild first')
    settings = project.s
    script_dir = settings.content_addon / "maps" / "scripts" / "mcp_characters"
    script_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{map_name}_characters"
    js = script_dir / f"{stem}.js"
    js.write_text(generate_script(stem, t_names, ct_names, mode), encoding="utf-8")
    from . import compiler
    job = compiler.compile_assets(settings, project.jobs, [js], force=True)
    compile_state = project.job_status(job.id, 50, 0)
    if compile_state['state'] != 'succeeded' or not compile_state['result'].get('ok'):
        raise CharacterError(f'Script compile failed: {compile_state}')
    from .ext.characters import _add_character_script
    if 'add_character_script' not in project.extra_ops:
        project.register_op('add_character_script',_add_character_script)
    result = project.map_edit(map_name,[{'op':'add_character_script',
        'script':f'mcp_characters/{stem}','targetname':'mcp_character_assign','models':all_names}])
    return {"map": map_name, "mode": mode, "t": t_names, "ct": ct_names,
            "script": f"maps/scripts/mcp_characters/{stem}.vjs", "source": str(js),
            "compile": {"job": job.id, "state": compile_state["state"], "exit_code": compile_state["exit_code"],
                        "result": compile_state["result"]}, **result}


def test_character(project, map_name: str) -> dict[str, Any]:
    """Run a non-destructive character smoke test when the game session permits it."""
    compiled = project.s.compiled_map(map_name)
    out = {"map": map_name, "compiled": compiled.exists(), "screenshots": [], "status": "blocked"}
    if not compiled.exists():
        out["reason"] = "map is not compiled; run map_build with preset full"
        return out
    try:
        shots = project.game_screenshot(map_name, [{"name": f"d_{map_name}_characters", "pos": [0, -512, 128], "ang": [8, 90, 0]}])
        out.update({"status": "ok", "screenshots": shots.get("screenshots", shots)})
    except Exception as exc:
        out["reason"] = str(exc)
    return out
