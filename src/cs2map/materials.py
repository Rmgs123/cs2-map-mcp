"""Custom materials: an image (or a procedural pattern) -> PNG textures + .vmat in the addon."""
from __future__ import annotations

import math
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageDraw, ImageFont

from .config import Settings, check_name, ensure_inside

SURFACES = ('concrete', 'brick', 'metal', 'wood', 'dirt', 'grass', 'gravel', 'sand', 'tile', 'glass', 'plaster',
            'rock', 'carpet', 'rubber', 'plastic', 'cardboard', 'mud', 'snow', 'water', 'default')
PATTERNS = ('solid', 'noise', 'bricks', 'tiles', 'planks', 'checker', 'stripes', 'plaster')


def _rgb(c, default=(128, 128, 128)) -> np.ndarray:
    if c is None:
        c = default
    if isinstance(c, str):
        c = c.lstrip('#')
        if not re.fullmatch(r'[0-9a-fA-F]{6}', c):
            raise ValueError('colour must be "#rrggbb" or [r, g, b]')
        c = [int(c[i:i + 2], 16) for i in (0, 2, 4)]
    if len(c) != 3 or not all(0 <= float(v) <= 255 for v in c):
        raise ValueError('colour must be [r, g, b] with 0..255')
    return np.array([float(v) for v in c])


def _noise(size: int, scale: float, seed: int, octaves: int = 4) -> np.ndarray:
    """Tileable fractal value noise in [0, 1]."""
    rng = np.random.default_rng(seed)
    out = np.zeros((size, size))
    amp, total = 1.0, 0.0
    cells = max(1, int(size / scale))
    for _ in range(octaves):
        grid = rng.random((cells, cells))
        x = np.linspace(0, cells, size, endpoint=False)
        xi = np.floor(x).astype(int)
        xf = x - xi
        xf = xf * xf * (3 - 2 * xf)
        x0, x1 = xi % cells, (xi + 1) % cells
        a = grid[np.ix_(x0, x0)]
        b = grid[np.ix_(x0, x1)]
        c = grid[np.ix_(x1, x0)]
        d = grid[np.ix_(x1, x1)]
        fy = xf[:, None]
        fx = xf[None, :]
        layer = (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy
        out += amp * layer
        total += amp
        amp *= 0.5
        cells = min(cells * 2, size)
    return out / total


def generate(pattern: str, size: int = 512, color=None, color2=None, mortar=None, seed: int = 1,
             count: int | None = None, variation: float = 0.15) -> tuple[Image.Image, np.ndarray]:
    """Return (colour image, height map 0..1) for a procedural pattern. All patterns tile."""
    if pattern not in PATTERNS:
        raise ValueError(f'pattern must be one of {PATTERNS}')
    if size not in (64, 128, 256, 512, 1024, 2048):
        raise ValueError('size must be a power of two between 64 and 2048')
    c1 = _rgb(color)
    c2 = _rgb(color2, tuple(np.clip(c1 * 0.75, 0, 255)))
    cm = _rgb(mortar, (170, 165, 155))
    n = _noise(size, size / 8, seed)
    fine = _noise(size, size / 64, seed + 7, 3)
    yy, xx = np.mgrid[0:size, 0:size] / size
    height = np.ones((size, size))
    rng = np.random.default_rng(seed)
    if pattern == 'solid':
        img = np.broadcast_to(c1, (size, size, 3)).copy()
    elif pattern in ('noise', 'plaster'):
        t = n if pattern == 'noise' else 0.6 * n + 0.4 * fine
        img = c2 + (c1 - c2) * t[..., None]
        height = t
    elif pattern == 'checker':
        k = count or 8
        mask = ((np.floor(xx * k) + np.floor(yy * k)) % 2)[..., None]
        img = c1 * mask + c2 * (1 - mask)
    elif pattern == 'stripes':
        k = count or 8
        mask = (np.floor(xx * k) % 2)[..., None]
        img = c1 * mask + c2 * (1 - mask)
    elif pattern == 'bricks':
        rows = count or 8
        cols = max(1, rows // 2)
        gap = 0.06
        ry = yy * rows
        row = np.floor(ry)
        rx = xx * cols + 0.5 * (row % 2)
        col = np.floor(rx)
        fy, fx = ry - row, rx - col
        joint = (fy < gap) | (fx < gap / 2 * cols / rows * 2)
        tone = rng.uniform(-1, 1, (rows + 1, cols + 2))
        t = tone[row.astype(int) % (rows + 1), col.astype(int) % (cols + 2)]
        brick = c1 * (1 + variation * t)[..., None]
        img = np.where(joint[..., None], cm, brick)
        height = np.where(joint, 0.0, 1.0)
    elif pattern == 'tiles':
        k = count or 4
        gap = 0.03
        fx, fy = (xx * k) % 1, (yy * k) % 1
        joint = (fx < gap) | (fy < gap)
        tone = rng.uniform(-1, 1, (k, k))
        t = tone[np.floor(yy * k).astype(int) % k, np.floor(xx * k).astype(int) % k]
        img = np.where(joint[..., None], cm, c1 * (1 + variation * t)[..., None])
        height = np.where(joint, 0.0, 1.0)
    elif pattern == 'planks':
        k = count or 6
        gap = 0.02
        fy = (yy * k) % 1
        plank = np.floor(yy * k)
        grain = np.sin((xx * 40 + _noise(size, size / 4, seed + 3) * 6) * math.pi) * 0.5 + 0.5
        tone = rng.uniform(-1, 1, k + 1)[plank.astype(int) % (k + 1)]
        base = c2 + (c1 - c2) * grain[..., None]
        img = np.where((fy < gap)[..., None], c2 * 0.5, base * (1 + variation * tone)[..., None])
        height = np.where(fy < gap, 0.0, 0.8 + 0.2 * grain)
    img = img * (0.9 + 0.2 * fine)[..., None] if pattern not in ('solid',) else img
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), 'RGB'), height


def normal_from_height(height: np.ndarray, strength: float = 2.0) -> Image.Image:
    h = np.asarray(height, dtype=float)
    dx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) * strength
    dy = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) * strength
    nz = np.ones_like(h)
    length = np.sqrt(dx * dx + dy * dy + nz * nz)
    n = np.stack([-dx / length, dy / length, nz / length], -1)   # DirectX-style green (Source 2)
    return Image.fromarray(((n * 0.5 + 0.5) * 255).astype(np.uint8), 'RGB')


def _pow2(img: Image.Image, max_size: int = 2048) -> Image.Image:
    w, h = img.size
    tw = min(max_size, 1 << max(6, round(math.log2(max(w, 1)))))
    th = min(max_size, 1 << max(6, round(math.log2(max(h, 1)))))
    return img if (w, h) == (tw, th) else img.resize((tw, th), Image.LANCZOS)


VMAT = '''// Generated by cs2map

Layer0
{{
	shader "csgo_complex.vfx"

	//---- Color ----
	g_flModelTintAmount "1.000"
	g_vColorTint "[1.000000 1.000000 1.000000 0.000000]"
	TextureColor "{color}"

	//---- Fog ----
	g_bFogEnabled "1"

	//---- Metalness ----
	F_METALNESS_TEXTURE 0
	g_flMetalness "{metalness:.3f}"

	//---- Normal ----
	TextureNormal "{normal}"

	//---- Roughness ----
	TextureRoughness "{roughness}"

	//---- Texture Address Mode ----
	g_nTextureAddressModeU "0"
	g_nTextureAddressModeV "0"

	SystemAttributes
	{{
		PhysicsSurfaceProperties "{surface}"
	}}
}}
'''


def _copy_map(path: str | Path, size: tuple[int, int], out: Path, mode: str = 'L') -> None:
    """Copy a user supplied PBR map to ``out`` with deterministic dimensions."""
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f'image not found: {path}')
    with Image.open(src) as im:
        if mode == 'L':
            result = im.convert('L')
        elif mode == 'RGB':
            result = im.convert('RGB')
        else:
            result = im.convert('RGBA')
        if result.size != size:
            result = result.resize(size, Image.Resampling.LANCZOS)
        result.save(out)


def _vmat_text(rel: str, name: str, color: str, normal: str, roughness: str, metalness: float,
               surface: str, metalness_image: str | None = None, ao_image: str | None = None,
               alpha: str = 'none', self_illum: bool = False) -> str:
    """Generate a csgo_complex material while keeping optional features opt-in."""
    alpha_block = {
        'none': 'F_ALPHA_TEST 0\n\tF_TRANSLUCENT 0',
        'test': 'F_ALPHA_TEST 1\n\tF_TRANSLUCENT 0\n\tg_flAlphaTestReference "0.500"',
        'translucent': 'F_ALPHA_TEST 0\n\tF_TRANSLUCENT 1',
    }[alpha]
    metal_block = f'F_METALNESS_TEXTURE 1\n\tTextureMetalness "{metalness_image}"' if metalness_image else \
        f'F_METALNESS_TEXTURE 0\n\tg_flMetalness "{metalness:.3f}"'
    ao_block = f'\n\tTextureAmbientOcclusion "{ao_image}"' if ao_image else ''
    illum_block = '\n\tF_SELF_ILLUM 1\n\tg_flSelfIllumScale "1.000"' if self_illum else ''
    return f'''// Generated by cs2map

Layer0
{{
\tshader "csgo_complex.vfx"

\t//---- Color ----
\tg_flModelTintAmount "1.000"
\tg_vColorTint "[1.000000 1.000000 1.000000 0.000000]"
\tTextureColor "{color}"

\t//---- Fog ----
\tg_bFogEnabled "1"

\t//---- Metalness ----
\t{metal_block}

\t//---- Normal ----
\tTextureNormal "{normal}"

\t//---- Roughness ----
\tTextureRoughness "{roughness}"{ao_block}

\t//---- Optional surface features ----
\t{alpha_block}{illum_block}

\t//---- Texture Address Mode ----
\tg_nTextureAddressModeU "0"
\tg_nTextureAddressModeV "0"

\tSystemAttributes
\t{{
\t\tPhysicsSurfaceProperties "{surface}"
\t}}
}}
'''


def create(s: Settings, name: str, image: str | None = None, pattern: dict | None = None, roughness: float = 0.7,
           metalness: float = 0.0, surface: str = 'concrete', normal_strength: float = 2.0,
           folder: str = 'mcp', *, normal_image: str | None = None, roughness_image: str | None = None,
           metalness_image: str | None = None, ao_image: str | None = None,
           alpha: str = 'none', self_illum: bool = False, real_size: list[float] | tuple[float, float] | None = None,
           kind: str = 'opaque', tint=None, opacity: float = 0.08, dirt: float = 0.0,
           color=None, glow: float = 3.0, mask_image: str | None = None,
           murk: float = 0.35, flow: float = 0.5) -> dict:
    """Write textures and a .vmat into content/<addon>/materials/<folder>/<name>/; return paths."""
    check_name(name, 'material name')
    check_name(folder, 'folder')
    # The core MCP tool forwards pattern unchanged. Until its signature grows,
    # pattern.kind exposes the same functionality without replacing a core tool.
    p = dict(pattern or {})
    if 'kind' in p:
        kind = p.pop('kind')
        options = p.pop('options', {})
        if not isinstance(options, dict):
            raise ValueError('pattern.options must be an object')
        allowed = {'tint', 'opacity', 'dirt', 'color', 'glow', 'mask_image', 'murk', 'flow'}
        if set(options) - allowed:
            raise ValueError(f'unknown kind options: {sorted(set(options) - allowed)}')
        return create(s, name, image, p or None, roughness, metalness, surface, normal_strength, folder,
                      normal_image=normal_image, roughness_image=roughness_image, metalness_image=metalness_image,
                      ao_image=ao_image, alpha=alpha, self_illum=self_illum, real_size=real_size, kind=kind,
                      **{**dict(tint=tint, opacity=opacity, dirt=dirt, color=color, glow=glow,
                               mask_image=mask_image, murk=murk, flow=flow), **options})
    if kind not in ('opaque', 'glass', 'emissive', 'water', 'decal'):
        raise ValueError('kind must be opaque, glass, emissive, water or decal')
    for key, value in dict(opacity=opacity, dirt=dirt, murk=murk).items():
        if not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
            raise ValueError(f'{key} must be in 0..1')
    if not math.isfinite(float(glow)) or not 0 <= glow <= 100:
        raise ValueError('glow must be in 0..100')
    if not math.isfinite(float(flow)) or not 0 <= flow <= 10:
        raise ValueError('flow must be in 0..10')
    if kind == 'decal':
        alpha = 'translucent'
    if kind == 'emissive':
        self_illum = True
    if surface not in SURFACES:
        raise ValueError(f'surface must be one of {SURFACES}')
    if not 0 <= roughness <= 1 or not 0 <= metalness <= 1:
        raise ValueError('roughness and metalness must be in 0..1')
    if alpha not in ('none', 'test', 'translucent'):
        raise ValueError("alpha must be 'none', 'test' or 'translucent'")
    if real_size is not None and (len(real_size) != 2 or any(not math.isfinite(float(v)) or float(v) <= 0 for v in real_size)):
        raise ValueError('real_size must be [width, height] in positive world units')
    rel = f'materials/{folder}/{name}'
    out_dir = ensure_inside(s.content_addon / rel, s.content_addon)
    out_dir.mkdir(parents=True, exist_ok=True)
    height = None
    source_alpha = None
    if image:
        src = Path(image)
        if not src.is_file():
            raise FileNotFoundError(f'image not found: {image}')
        with Image.open(src) as im:
            color_img = _pow2(im.convert('RGBA' if alpha != 'none' or kind == 'emissive' and 'A' in im.getbands() else 'RGB'))
        if kind == 'emissive' and color_img.mode == 'RGBA':
            source_alpha = color_img.getchannel('A')
            if alpha == 'none':
                flat = Image.new('RGB', color_img.size, (0, 0, 0))
                flat.paste(color_img, mask=source_alpha)
                color_img = flat
    else:
        p = dict(pattern or {'pattern': 'noise'})
        if color is not None:
            p['color'] = color
        color_img, height = generate(p.pop('pattern', 'noise'), **p)
    image_tint = color if image and kind == 'emissive' and color is not None else tint
    if image_tint is not None and kind != 'glass':
        arr = np.asarray(color_img).copy()
        arr[..., :3] = np.clip(arr[..., :3] * (_rgb(image_tint) / 255), 0, 255).astype(np.uint8)
        color_img = Image.fromarray(arr)
    color_img.save(out_dir / f'{name}_color.png')
    size = color_img.size
    if height is None:
        gray = np.asarray(color_img.convert('L').filter(ImageFilter.GaussianBlur(1)), dtype=float) / 255.0
        height = gray
    normal_path = out_dir / f'{name}_normal.png'
    if normal_image:
        _copy_map(normal_image, size, normal_path, 'RGB')
    elif kind in ('glass', 'emissive', 'decal'):
        Image.new('RGB', size, (128, 128, 255)).save(normal_path)
    else:
        normal_from_height(height, normal_strength).resize(size).save(normal_path)
    rough_path = out_dir / f'{name}_rough.png'
    if roughness_image:
        _copy_map(roughness_image, size, rough_path, 'L')
    else:
        rough = np.full((size[1], size[0]), round(roughness * 255), dtype=np.uint8)
        Image.fromarray(rough, 'L').save(rough_path)
    metal_path = None
    if metalness_image:
        metal_path = out_dir / f'{name}_metal.png'
        _copy_map(metalness_image, size, metal_path, 'L')
    ao_path = None
    if ao_image:
        ao_path = out_dir / f'{name}_ao.png'
        _copy_map(ao_image, size, ao_path, 'L')
    vmat = out_dir / f'{name}.vmat'
    text = _vmat_text(rel, name, f'{rel}/{name}_color.png', f'{rel}/{name}_normal.png',
                               f'{rel}/{name}_rough.png', metalness, surface,
                               f'{rel}/{name}_metal.png' if metal_path else None,
                               f'{rel}/{name}_ao.png' if ao_path else None, alpha, self_illum)
    path = lambda suffix: f'{rel}/{name}_{suffix}.png'
    vec = lambda rgb: '[' + ' '.join(f'{x / 255:.6f}' for x in _rgb(rgb)) + ' 0]'
    if kind == 'glass':
        glass_tint = tint if tint is not None else '#d8eeec'
        Image.new('RGB', size, tuple(int(v) for v in _rgb(glass_tint))).save(out_dir / f'{name}_tint.png')
        dust = _noise(size[0], max(1, size[0] / 12), 7)
        Image.fromarray(np.round(dust * dirt * 255).astype(np.uint8)).resize(size).save(out_dir / f'{name}_dust.png')
        text = _layer('csgo_glass', {'F_RENDER_BACKFACES': 1, 'F_DO_NOT_CAST_SHADOWS': 1,
                      'GlassMaskTranslucency': f'[{opacity} {opacity} {opacity} 0]',
                      'GlassMaskTransmission': '[255 255 255 0]', 'GlassTintColor': path('tint'),
                      'GlassMaskColor': path('dust'),
                      'TextureNormal': path('normal'), 'TextureRoughness': path('rough')}, 'glass')
    elif kind == 'emissive':
        if mask_image:
            _copy_map(mask_image, size, out_dir / f'{name}_illum.png')
        else:
            # RGBA artwork illuminates only nontransparent ink. RGB patterns
            # use luminance so a black screen background stays dark.
            mask = source_alpha if source_alpha is not None else color_img.getchannel('A') if color_img.mode == 'RGBA' else color_img.convert('L')
            mask.save(out_dir / f'{name}_illum.png')
        text = text.replace('g_flSelfIllumScale "1.000"',
                            f'g_flSelfIllumScale "1.000"\n\tg_flSelfIllumBrightness "{glow:.6f}"\n'
                            f'\tg_flSelfIllumAlbedoFactor "1"\n\tg_vSelfIllumTint "[1 1 1 0]"\n'
                            f'\tTextureSelfIllumMask "{path("illum")}"')
    elif kind == 'water':
        water_color = color if color is not None else '#3a8d9a'
        waves = _noise(256, 32, 23)
        rgba = normal_from_height(waves, 18).convert('RGBA')
        rgba.putalpha(Image.fromarray(np.round(waves * 255).astype(np.uint8)))
        rgba.save(out_dir / f'{name}_waves.png')
        rgba.getchannel('A').save(out_dir / f'{name}_waveheight.png')
        text = _layer('csgo_water_fancy', {'F_REFLECTION_TYPE': 2, 'F_REFRACTION': 1,
                      'g_flWaterFogStrength': murk, 'g_flWaterDecayStrength': murk,
                      'g_vWaterFogColor': vec(water_color), 'g_vWaterDecayColor': vec(water_color),
                      'g_flWavesSpeed': flow, 'g_vWaveScale': '[15 15 0 0]', 'g_nWaveIterations': 3,
                      'g_flWavesNormalStrength': 1, 'g_flWavesHeightOffset': 0.1,
                      'g_flWaterMaxDepth': 128, 'g_flWaterPlaneOffset': 0,
                      'g_flHighFreqWeight': 0.15, 'g_flMedFreqWeight': 0.25, 'g_flLowFreqWeight': 0.15,
                      'g_flEnvironmentMapBrightness': 1, 'g_flGlossiness': 1,
                      'g_flFresnelExponent': 7, 'g_flRefractionLimit': 0.4,
                      'g_flWaterRoughnessMin': 0.1, 'g_flWaterRoughnessMax': 1,
                      'TextureWavesNormal': path('waves'), 'TextureWavesHeight': path('waveheight')}, 'water', water=True)
    elif kind == 'decal':
        color_img.convert('RGBA').getchannel('A').save(out_dir / f'{name}_alpha.png')
        text = _layer('csgo_lightmappedgeneric', {'F_OVERLAY': 1, 'F_TRANSLUCENT': 1,
                      'F_LAYERS': 0, 'F_VERTEX_COLOR': 1, 'g_flAlphaTestReference': 0,
                      'TextureLayer1Color': path('color'), 'TextureLayer1Translucency': path('alpha'),
                      'TextureLayer1Normal': path('normal'), 'TextureLayer1Roughness': path('rough'),
                      'g_vLayer1Tint': '[1 1 1 0]'}, surface)
    vmat.write_text(text, encoding='utf-8')
    meta = {'kind': kind}
    if real_size is not None:
        meta['real_size'] = [float(v) for v in real_size]
    (out_dir / f'{name}.json').write_text(json.dumps(meta, indent=2) + '\n', encoding='utf-8')
    return {'material': f'{rel}/{name}.vmat', 'vmat': str(vmat), 'size': list(size), 'kind': kind}


def _layer(shader: str, params: dict, surface: str, water=False) -> str:
    lines = ['// Generated by cs2map; parameters verified against installed Valve materials',
             'Layer0', '{', f'\tshader "{shader}.vfx"', '\tg_bFogEnabled "1"']
    lines.extend(f'\t{k} "{v}"' for k, v in params.items())
    if water:
        lines += ['\tAttributes { "mapbuilder.water" "1" "mapbuilder.nonsolid" "1" }']
    lines += ['\tSystemAttributes', '\t{', f'\t\tPhysicsSurfaceProperties "{surface}"', '\t}', '}', '']
    return '\n'.join(lines)


def sign_create(s: Settings, name: str, text: str, style: str = 'plate', *, color=None,
                background=None, glow: float = 3.0, font: str = 'arialbd.ttf') -> dict:
    """Render static multilingual artwork with an installed Windows font."""
    check_name(name, 'sign name')
    if style not in ('plate', 'stencil', 'neon', 'paper'):
        raise ValueError('style must be plate, stencil, neon or paper')
    if not isinstance(text, str) or not text.strip() or len(text) > 512:
        raise ValueError('text must contain 1..512 characters')
    font_path = ensure_inside(Path('C:/Windows/Fonts') / font, Path('C:/Windows/Fonts'))
    if not font_path.is_file():
        raise ValueError(f'Windows font not found: {font}')
    fg = tuple(int(x) for x in _rgb(color, (255, 95, 125) if style == 'neon' else (240, 242, 234)))
    bg = tuple(int(x) for x in _rgb(background, (240, 227, 195) if style == 'paper' else (18, 40, 36)))
    if style == 'paper' and color is None:
        fg = (25, 25, 23)
    size = (1024, 512) if '\n' in text else (1024, 256)
    im = Image.new('RGBA', size, (*bg, 0 if style in ('stencil', 'neon') else 255))
    draw = ImageDraw.Draw(im)
    for px in range(180, 11, -2):
        face = ImageFont.truetype(str(font_path), px)
        bounds = draw.multiline_textbbox((0, 0), text, font=face, align='center', spacing=10)
        if bounds[2] - bounds[0] <= size[0] - 96 and bounds[3] - bounds[1] <= size[1] - 60:
            break
    else:
        raise ValueError('text is too long; shorten it or add line breaks')
    x = (size[0] - (bounds[2] - bounds[0])) / 2 - bounds[0]
    y = (size[1] - (bounds[3] - bounds[1])) / 2 - bounds[1]
    draw.multiline_text((x, y), text, font=face, fill=(*fg, 255), align='center', spacing=10)
    folder = 'mcp_decals' if style == 'stencil' else 'mcp'
    out = ensure_inside(s.content_addon / f'materials/{folder}/{name}', s.content_addon)
    out.mkdir(parents=True, exist_ok=True)
    artwork = out / f'{name}_artwork.png'; im.save(artwork)
    ink_mask = out / f'{name}_ink.png'; im.getchannel('A').save(ink_mask)
    result = create(s, name, str(artwork), roughness=0.85, surface='metal' if style == 'plate' else 'default',
                    folder=folder, kind='decal' if style == 'stencil' else 'emissive' if style == 'neon' else 'opaque',
                    glow=glow, mask_image=str(ink_mask) if style == 'neon' else None)
    # A neon sign has an opaque dark backing; alpha is used solely as the glow mask.
    if style == 'neon':
        flat = Image.new('RGB', size, (5, 7, 10)); flat.paste(im, mask=im.getchannel('A'))
        flat.save(out / f'{name}_color.png')
    meta_path = out / f'{name}.json'
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    meta.update(sign={'text': text, 'style': style, 'aspect': size[0] / size[1], 'font': font})
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {**result, 'name': name, 'style': style, 'text': text}


def decal_create(s: Settings, name: str, image: str, size=None, background=None,
                 tolerance: float = 20, softness: float = 20) -> dict:
    """Keep source alpha or smoothly key out a specified RGB background."""
    check_name(name, 'decal name')
    if not math.isfinite(tolerance) or not 0 <= tolerance <= 441 or not math.isfinite(softness) or not 0 < softness <= 441:
        raise ValueError('tolerance must be 0..441 and softness must be >0..441')
    with Image.open(image) as source:
        im = source.convert('RGBA')
    if background is not None:
        arr = np.asarray(im).copy()
        distance = np.linalg.norm(arr[..., :3].astype(float) - _rgb(background), axis=2)
        arr[..., 3] = np.round(arr[..., 3] * np.clip((distance - tolerance) / softness, 0, 1)).astype(np.uint8)
        # Remove the keyed background from partial alpha pixels (unmatte) to
        # avoid white/black fringes against a differently coloured receiving wall.
        alpha = arr[..., 3:4].astype(float) / 255
        arr[..., :3] = np.clip((np.asarray(im)[..., :3] - (1-alpha) * _rgb(background)) / np.maximum(alpha, 1/255), 0, 255).astype(np.uint8)
        im = Image.fromarray(arr)
    out = ensure_inside(s.content_addon / f'materials/mcp_decals/{name}', s.content_addon)
    out.mkdir(parents=True, exist_ok=True)
    artwork = out / f'{name}_artwork.png'; im.save(artwork)
    if isinstance(size, (int, float)):
        size = [size, size]
    return create(s, name, str(artwork), folder='mcp_decals', kind='decal', real_size=size, surface='default')
