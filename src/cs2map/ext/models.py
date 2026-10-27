"""MCP tools and map_edit operation for environment models."""
from __future__ import annotations

from pathlib import Path
from pathlib import PurePosixPath
import math
from typing import Any, Literal

from .. import models
from ..project import OpError
from ..config import check_name, ensure_inside


def asset_path(project, value: str, extension: str = '.vmdl', prefix: str = 'models') -> str:
    value = value.replace('\\', '/')
    path = PurePosixPath(value)
    if (not path.parts or path.is_absolute() or '..' in path.parts or ':' in value or path.parts[0] != prefix
            or path.suffix != extension):
        raise OpError(f'asset must be a relative {prefix}/.../{extension} path')
    source = ensure_inside(project.s.content_addon / value, project.s.content_addon)
    compiled = ensure_inside(project.s.game_addon / (value + '_c'), project.s.game_addon)
    if not source.is_file() and not compiled.is_file() and not project.game.exists(value + '_c'):
        raise OpError(f'asset does not exist: {value}')
    return value


def _model_path(project, value: str) -> tuple[str, Path]:
    if not isinstance(value, str) or not value.strip():
        raise OpError('"model" must be a model name or a models/.../.vmdl path')
    value = value.replace("\\", "/")
    if "/" in value and value.endswith(".vmdl"):
        # any stock or addon path: models/..., but also agents/models/... and characters/models/... (player agents)
        prefix = value.split("/", 1)[0]
        try:
            rel = asset_path(project, value, prefix=prefix)
        except OpError as e:
            raise OpError(f'{e}{_similar_models(project, value)}') from None
        path = project.s.content_addon / Path(value)
    else:
        try:
            name = check_name(value.removesuffix(".vmdl"), 'model name')
            if "/" in name:
                raise ValueError
            rel = f"models/mcp/{name}/{name}.vmdl"
            path = project.s.content_addon / rel
        except ValueError:
            raise OpError('model name must be simple, or use models/.../<name>.vmdl') from None
    if not path.is_file() and not project.game.exists(rel + '_c'):
        raise OpError(f"model asset does not exist: {rel}")
    return rel, path


def _similar_models(project, value: str) -> str:
    """'; real paths: ...' for a model path that does not exist (a guessed folder: models/tm_phoenix/... is really
    agents/models/tm_phoenix/... or characters/models/...)."""
    stem = PurePosixPath(value).stem
    found = [x for x in project.game.search(stem, 'vmdl', 40) if PurePosixPath(x).stem == stem][:4]
    return f'; existing paths with this name: {", ".join(found)}' if found else ''


def register(mcp, project):
    @mcp.tool()
    def model_build(name: str, script: str | None = None, template: str | None = None,
                    params: dict[str, Any] | None = None,
                    collision: Literal['hull', 'mesh', 'none'] = 'hull',
                    surface: str = 'default', physics: dict[str, Any] | None = None,
                    weapon: dict[str, Any] | str | None = None, lods: list[dict[str, Any]] | None = None) -> dict:
        """Build an environment model with Blender and compile its .vmdl.

        script is a Blender Python file path or source text.  A stock template
        (crate, barrel, fence, door, window_frame, pipe, lamp, table, shelf,
        sandbags, jersey_barrier, pallet, railing, dumpster, electrical_box)
        can be selected instead.  Units are CS2 units, +Z is up, and objects
        whose names end in ``_col`` are used only for collision.
        """
        return models.model_build(project, name, script, template, params, collision, surface, physics, weapon, lods)

    @mcp.tool()
    def model_import(name: str, file: str, scale: str | float = 'auto',
                     collision: Literal['hull', 'mesh', 'none'] = 'hull',
                     surface: str = 'default', physics: dict[str, Any] | None = None,
                     weapon: dict[str, Any] | str | None = None, lods: list[dict[str, Any]] | None = None) -> dict:
        """Import an FBX, OBJ, or GLB model, normalize it in Blender, and compile it."""
        return models.model_import(project, name, file, scale, collision, surface, physics, weapon, lods)

    @mcp.tool()
    def model_preview(name: str, views: list[str] | None = None, size: int = 512,
                      reference: str | None = None) -> dict:
        """Render a model preview PNG in Blender; reference creates a side-by-side image."""
        return models.model_preview(project, name, views, size, reference)

    @mcp.tool()
    def model_list() -> dict:
        """List environment models built in this addon's models/mcp directory."""
        return models.model_list(project)

    @mcp.tool()
    def model_info(name: str) -> dict:
        """Return the ModelDoc41 document and source-file status for one model."""
        return models.model_info(project, name)

    def add_model(p, vmap, op, refs):
        rel, _ = _model_path(p, op.get('model'))
        dynamic = op.get('dynamic', False)
        if not isinstance(dynamic, bool):
            raise OpError('dynamic must be true or false')
        classname = 'prop_dynamic' if dynamic else 'prop_static'
        origin = op.get('origin') or [0, 0, 0]
        angles = op.get('angles') or [0, 0, 0]
        scale = float(op.get('scale', 1.0))
        if not math.isfinite(scale) or scale <= 0 or scale > 100:
            raise OpError('scale must be between 0 and 100')
        kv = dict(op.get('keyvalues') or {})
        kv['model'] = rel
        tint = op.get('tint')
        if tint is not None:
            if (not isinstance(tint, list) or len(tint) != 3
                    or any(isinstance(c, bool) or not isinstance(c, (int, float)) or not 0 <= c <= 255 for c in tint)):
                raise OpError('tint is [r, g, b], each 0..255 (multiplies the whole model: leaves and bark alike)')
            kv['rendercolor'] = ' '.join(str(int(round(c))) for c in tint)
        skin = op.get('skin')
        if skin is not None:
            if not isinstance(skin, str) or not skin.strip():
                raise OpError('skin is the name of one of the model\'s material groups')
            from .. import visual
            groups = visual.model_material_groups(p, rel)
            if groups is not None and skin not in groups:
                raise OpError(f'{rel} has no material group {skin!r}; it has: {", ".join(groups) or "none (one look only)"}')
            kv['skin'] = skin
        defaults = p.catalog.defaults(classname)
        if dynamic:
            kv.setdefault('solid', 6)
            kv['modelscale'] = scale
        ent = vmap.new_entity(classname, kv, origin, angles, [scale, scale, scale], defaults=defaults)
        vmap.attach(ent)
        ref = op.get('ref')
        if ref:
            refs[ref] = ent['nodeID']
        return {'op': 'add_model', 'id': ent['nodeID'], 'model': rel, 'classname': classname,
                **({'ref': ref} if ref else {}), **({'skin': kv['skin']} if skin else {})}

    project.register_op('add_model', add_model)
