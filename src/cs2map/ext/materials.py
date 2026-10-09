"""Reference-material and UV tools owned by agent B."""
from __future__ import annotations

import time
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

from .. import compiler, materials, textures, uv
from ..config import check_name
from ..project import OpError
from .. import vtex


def _compile(project, vmat: Path) -> dict:
    job = compiler.compile_assets(project.s, project.jobs, [vmat], force=True)
    state = project.job_status(job.id, 50, 0)
    return {'job': job.id, 'state': state['state'], 'log': state['log'], 'result': state['result']}


def _wait_build(project, job: str) -> dict:
    state = project.job_status(job, 50, 0)
    for _ in range(5):
        if state['state'] != 'running':
            break
        state = project.job_status(job, 50, 0)
    return state


def _reference_build(project, name: str, image: str, region=None, quad=None, real_size=None,
                     surface='concrete', roughness=None, metalness=0.0, alpha='none') -> dict:
    check_name(name, 'material name')
    source = textures.load(image, region, quad)
    source = textures.delight(source)
    analysis = textures.tile_analysis(source)
    source = textures.make_tileable(source, method='auto')
    scratch = project.s.logs.parent / '.local' / 'scratch' / 'B' / 'references'
    scratch.mkdir(parents=True, exist_ok=True)
    color_path = scratch / f'{name}_color.png'
    normal_path = scratch / f'{name}_normal.png'
    rough_path = scratch / f'{name}_rough.png'
    ao_path = scratch / f'{name}_ao.png'
    source.save(color_path)
    height = textures.height_from(source)
    textures.normal_from(height).save(normal_path)
    textures.roughness_from(source, roughness if roughness is not None else 0.7).save(rough_path)
    textures.ao_from(height).save(ao_path)
    made = materials.create(project.s, name, str(color_path), roughness=roughness if roughness is not None else 0.7,
                            metalness=metalness, surface=surface, normal_image=str(normal_path),
                            roughness_image=str(rough_path), ao_image=str(ao_path), alpha=alpha,
                            real_size=real_size)
    project._tex_cache.pop(made['material'], None)
    compile_result = _compile(project, Path(made['vmat']))
    out = {**made, 'maps': {'color': f'materials/mcp/{name}/{name}_color.png',
                            'normal': f'materials/mcp/{name}/{name}_normal.png',
                            'roughness': f'materials/mcp/{name}/{name}_rough.png',
                            'ao': f'materials/mcp/{name}/{name}_ao.png'},
           'seam_error': textures.seam_error(source),
           'mirror_score': textures.mirror_score(source),
           'repeat_score': textures.repeat_score(source),
           **textures.band_analysis(source),
           'tile_mode': analysis['mode'], 'period': analysis['period'],
           'compile': compile_result}
    if compile_result.get('result'):
        out['compile_result'] = compile_result['result']
    return out


def _build_preview_map(project, name: str, material: str) -> dict:
    """Create a small, self-contained map used by material_preview/gallery."""
    project.map_create(name, 'basic', overwrite=True)
    ops = [
        {'op': 'add_mesh', 'ref': 'floor', 'shape': {'shape': 'box', 'mins': [-512, -512, 0], 'maxs': [512, 512, 16]},
         'material': material},
        {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [-512, 480, 16], 'maxs': [512, 496, 320]},
         'material': material},
        {'op': 'add_entity', 'classname': 'info_player_terrorist', 'origin': [-256, 0, 24], 'angles': [0, 0, 0]},
        {'op': 'add_entity', 'classname': 'info_player_counterterrorist', 'origin': [256, 0, 24], 'angles': [0, 180, 0]},
        {'op': 'add_entity', 'classname': 'point_nav_walkable', 'origin': [0, 0, 20]},
        {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [-480, -480, 16], 'maxs': [-320, -320, 144]},
         'material': material},
        {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [-160, -480, 16], 'maxs': [0, -320, 144]},
         'material': material},
        {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [160, -480, 16], 'maxs': [320, -320, 144]},
         'material': material},
        {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [-512, -512, 0], 'maxs': [512, 512, 24]},
         'material': 'materials/tools/toolslightmapres.vmat'},
    ]
    return project.map_edit(name, ops)


def register(mcp, project):
    # Typed kinds are part of the core material_create(kind=..., options=...); this stays for scripts.
    def material_create_kind(name: str, kind: str = 'opaque', image: str | None = None,
                             pattern: dict | None = None, roughness: float = 0.7,
                             tint=None, opacity: float = 0.08, dirt: float = 0.0, color=None,
                             glow: float = 3.0, mask_image: str | None = None,
                             murk: float = 0.35, flow: float = 0.5) -> dict:
        """Create and compile opaque/glass/emissive/water/decal material.

        Tint/color: #rrggbb or RGB 0..255. Opacity/dirt/roughness/murk: 0..1.
        Glow: 0..100, flow: wave speed 0..10. mask_image: local grayscale PNG.
        Example: name=b_window, kind=glass, tint=#cfeef3, roughness=0.08.
        """
        made = materials.create(project.s, name, image, pattern, roughness=roughness, kind=kind,
                                tint=tint, opacity=opacity, dirt=dirt, color=color, glow=glow,
                                mask_image=mask_image, murk=murk, flow=flow,
                                folder='mcp_decals' if kind == 'decal' else 'mcp')
        project._tex_cache.pop(made['material'], None)
        return {**made, 'compile': _compile(project, Path(made['vmat']))}

    @mcp.tool()
    def sign_create(name: str, text: str, style: str = 'plate', color=None,
                    background=None, glow: float = 3.0, font: str = 'arialbd.ttf') -> dict:
        """Render Cyrillic/Latin text using Windows fonts and compile a sign.

        Styles: plate (frame via add_sign), stencil (overlay), neon (masked glow),
        paper. Place with map_edit add_sign {name,origin,normal,size:[w,h]}.
        """
        made = materials.sign_create(project.s, name, text, style, color=color, background=background,
                                     glow=glow, font=font)
        project._tex_cache.pop(made['material'], None)
        return {**made, 'compile': _compile(project, Path(made['vmat']))}

    @mcp.tool()
    def decal_create(name: str, image: str, size: list[float] | float | None = None,
                     background=None, tolerance: float = 20, softness: float = 20) -> dict:
        """Compile local PNG alpha artwork as an overlay material in mcp_decals.

        Optional background=#ffffff keys out that RGB color with a soft edge;
        tolerance/softness are RGB distance 0..441. size is world [width,height].
        Place with map_edit add_decal using the returned material path.
        """
        made = materials.decal_create(project.s, name, image, size, background, tolerance, softness)
        project._tex_cache.pop(made['material'], None)
        return {**made, 'compile': _compile(project, Path(made['vmat']))}

    def _stock_or_local_maps(base: str) -> dict:
        """Return exported/local maps for a material path."""
        norm = str(base).replace('\\', '/').removesuffix('_c')
        if norm.startswith('materials/') and project.game.exists(norm + '_c'):
            folder = project.s.logs.parent / '.local' / 'textures'
            return vtex.export(project.game, norm, folder)['maps']
        p = Path(norm)
        if not p.is_absolute():
            p = project.s.content_addon / norm
        if p.suffix.lower() != '.vmat':
            p = p.with_suffix('.vmat')
        if not p.is_file():
            raise FileNotFoundError(f'base material not found: {base}')
        text = p.read_text(encoding='utf-8', errors='replace')
        slots = {}
        for key, pattern in {'color': r'TextureColor\s+"([^"]+)"', 'normal': r'TextureNormal\s+"([^"]+)"',
                             'roughness': r'TextureRoughness\s+"([^"]+)"', 'ao': r'TextureAmbientOcclusion\s+"([^"]+)"'}.items():
            hit = re.search(pattern, text, re.I)
            if hit:
                q = hit.group(1).replace('\\', '/')
                q = project.s.content_addon / q if not Path(q).is_absolute() else Path(q)
                if q.is_file(): slots[key] = str(q)
        if 'color' not in slots:
            raise ValueError(f'base material has no local TextureColor: {base}')
        return slots

    def _tint_color(path: str, tint, saturation: float, brightness: float, grime: float, ao: str | None = None) -> Image.Image:
        with Image.open(path) as im:
            image = im.convert('RGB')
        arr = np.asarray(image, dtype=np.float32) / 255.0
        if tint is not None:
            if isinstance(tint, str):
                value = tint.lstrip('#')
                if len(value) != 6: raise ValueError('tint must be #rrggbb or [r,g,b]')
                tint = [int(value[i:i + 2], 16) for i in (0, 2, 4)]
            if len(tint) != 3 or any(float(x) < 0 or float(x) > 255 for x in tint):
                raise ValueError('tint must be #rrggbb or [r,g,b]')
            arr *= np.asarray(tint, dtype=np.float32)[None, None, :] / 255.0
        image = Image.fromarray(np.clip(arr * 255, 0, 255).astype(np.uint8), 'RGB')
        image = ImageEnhance.Color(image).enhance(float(saturation))
        image = ImageEnhance.Brightness(image).enhance(float(brightness))
        if grime:
            if ao:
                with Image.open(ao) as mask:
                    gray = np.asarray(mask.convert('L').resize(image.size), dtype=np.float32) / 255
                # Flat stock AO maps carry no cavities: fall back to luminance.
                if float(gray.std()) < .01:
                    gray = np.asarray(image.convert('L'), dtype=np.float32) / 255
            else:
                gray = np.asarray(image.convert('L').filter(ImageFilter.GaussianBlur(max(1, image.width // 64))), dtype=np.float32) / 255
            strength = np.clip(float(grime), 0, 1)
            image = Image.fromarray(np.clip(np.asarray(image, dtype=np.float32) * (1 - strength * (1 - gray[..., None]) * .45), 0, 255).astype(np.uint8), 'RGB')
        return image

    def _variant(project_, name: str, base: str, tint=None, saturation: float = 1, brightness: float = 1,
                 roughness_offset: float = 0, grime: float = 0, scale: float = 1) -> dict:
        check_name(name, 'material name')
        if saturation < 0 or brightness < 0 or scale <= 0 or abs(roughness_offset) > 1 or not 0 <= grime <= 1:
            raise ValueError('saturation, brightness, scale, roughness_offset or grime is out of range')
        maps = _stock_or_local_maps(base)
        scratch = project_.s.logs.parent / '.local' / 'scratch' / 'B' / 'variants'
        scratch.mkdir(parents=True, exist_ok=True)
        color = _tint_color(maps['color'], tint, saturation, brightness, grime, maps.get('ao'))
        color_path = scratch / f'{name}_color.png'; color.save(color_path)
        kwargs = {'roughness': float(np.clip(0.7 + roughness_offset, 0, 1)),
                  'surface': next((x for x in materials.SURFACES if f'/{x}/' in base), 'concrete'),
                  'real_size': [color.width * scale, color.height * scale]}
        if 'roughness' in maps:
            with Image.open(maps['roughness']) as rough:
                r = np.asarray(rough.convert('L'), dtype=np.float32) / 255 + float(roughness_offset)
                r = Image.fromarray(np.clip(r * 255, 0, 255).astype(np.uint8), 'L')
                rough_path = scratch / f'{name}_rough.png'; r.save(rough_path); kwargs['roughness_image'] = str(rough_path)
        if 'normal' in maps: kwargs['normal_image'] = maps['normal']
        if 'ao' in maps: kwargs['ao_image'] = maps['ao']
        if 'metalness' in maps: kwargs['metalness_image'] = maps['metalness']
        made = materials.create(project_.s, name, str(color_path), folder='mcp', **kwargs)
        project_._tex_cache.pop(made['material'], None)
        job = compiler.compile_assets(project_.s, project_.jobs, [Path(made['vmat'])], force=True)
        state = project_.job_status(job.id, 50, 0)
        return {**made, 'base': base, 'compile': {'job': job.id, 'state': state['state'], 'log': state['log'], 'result': state['result']},
                'variant': {'tint': tint, 'saturation': saturation, 'brightness': brightness,
                            'roughness_offset': roughness_offset, 'grime': grime, 'scale': scale}}

    @mcp.tool()
    def texture_export(material_or_vtex: str, mip: int = 0) -> dict:
        """Export a stock material or compiled texture from the game VPK to PNG."""
        if not isinstance(material_or_vtex, str) or not material_or_vtex.strip():
            raise ValueError('material_or_vtex must be a materials/... .vmat or .vtex path')
        out = project.s.logs.parent / '.local' / 'textures'
        return vtex.export(project.game, material_or_vtex, out, mip)

    @mcp.tool()
    def material_variant(name: str, base: str, tint=None, saturation: float = 1, brightness: float = 1,
                         roughness_offset: float = 0, grime: float = 0, scale: float = 1) -> dict:
        """Create and compile a tinted/aged variant of a stock or local material."""
        return _variant(project, name, base, tint, saturation, brightness, roughness_offset, grime, scale)

    @mcp.tool()
    def material_from_reference(name: str, image: str, region: list[float] | None = None,
                                quad: list[list[float]] | None = None,
                                real_size: list[float] | None = None, surface: str = 'concrete',
                                roughness: float | None = None, metalness: float = 0.0,
                                alpha: str = 'none') -> dict:
        """Create a tileable PBR material from a local reference image.

        The image is cropped/rectified, illumination-flattened and seam-tested;
        normal, roughness and AO maps are generated beside the colour map.
        ``real_size`` is the physical [width, height] in Source 2 units.
        """
        return _reference_build(project, name, image, region, quad, real_size, surface, roughness, metalness, alpha)

    @mcp.tool()
    def material_preview(material: str, map_name: str = 'b_preview') -> dict:
        """Put one material on a lit service map, compile it and return game screenshots."""
        if not material.startswith('materials/'):
            raise ValueError('material must be an addon-relative materials/... path')
        made = _build_preview_map(project, map_name, material)
        build = project.map_build(map_name, 'full')
        state = _wait_build(project, build['job'])
        result = {'map': map_name, 'material': material, 'build': state}
        if state.get('result', {}).get('ok'):
            try:
                result['screenshots'] = project.game_screenshot(map_name, [
                    {'name': f'b_{map_name}_overview', 'pos': [0, -720, 500], 'ang': [28, 90, 0]},
                    {'name': f'b_{map_name}_eye', 'pos': [0, -260, 100], 'ang': [4, 90, 0]},
                ])
            except Exception as exc:
                result['screenshot_error'] = str(exc)
        return result

    @mcp.tool()
    def material_gallery(materials_list: list[str], map_name: str = 'b_gallery') -> dict:
        """Build up to six 512x512 material stands and return game screenshots."""
        if not 1 <= len(materials_list) <= 6:
            raise ValueError('materials_list must contain 1..6 materials')
        if any(not str(m).startswith('materials/') for m in materials_list):
            raise ValueError('each material must be an addon-relative materials/... path')
        project.map_create(map_name, 'basic', overwrite=True)
        extent = max(1280, len(materials_list) * 320 + 128)
        ops = [
            {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [-extent, -1024, 0], 'maxs': [extent, 1024, 16]},
             'material': 'materials/dev/dev_measuregeneric01.vmat'},
            {'op': 'add_entity', 'classname': 'info_player_terrorist', 'origin': [0, -800, 24]},
            {'op': 'add_entity', 'classname': 'info_player_counterterrorist', 'origin': [0, 800, 24], 'angles': [0, 180, 0]},
            {'op': 'add_entity', 'classname': 'point_nav_walkable', 'origin': [0, 0, 20]},
            {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [-extent, -1024, 0], 'maxs': [extent, 1024, 24]},
             'material': 'materials/tools/toolslightmapres.vmat'},
        ]
        centers = [(int((i - (len(materials_list) - 1) / 2) * 640), 0) for i in range(len(materials_list))]
        for i, mat in enumerate(materials_list):
            x, y = centers[i]
            ops.append({'op': 'add_mesh', 'ref': f'stand_{i}', 'shape': {'shape': 'box', 'mins': [x - 256, y - 16, 16],
                                                     'maxs': [x + 256, y + 16, 528]}, 'material': mat})
            ops.append({'op': 'set_uv', 'id': f'@stand_{i}', 'mode': 'world', 'scale': [1, 1]})
        project.map_edit(map_name, ops)
        build = project.map_build(map_name, 'full')
        state = _wait_build(project, build['job'])
        out = {'map': map_name, 'materials': materials_list, 'build': state}
        if state.get('result', {}).get('ok'):
            try:
                shots = [{'name': f'{map_name}_stand_{i}', 'pos': [x, y - 650, 208], 'ang': [0, 90, 0]}
                         for i, (x, y) in enumerate(centers)]
                out['screenshots'] = project.game_screenshot(map_name, shots)
            except Exception as exc:
                out['screenshot_error'] = str(exc)
        return out

    def set_uv_op(project_, vmap, op, refs):
        target = project_.resolve(vmap, op.get('id'), refs)
        result = uv.apply_to_target(project_, vmap, target, op)
        if op.get('ref'):
            refs[op['ref']] = target['nodeID']
            result['ref'] = op['ref']
        return result

    project.register_op('set_uv', set_uv_op)

