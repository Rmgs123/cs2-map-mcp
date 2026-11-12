"""Offline preview: the map drawn from its source in seconds, without CS2.

game_screenshot(map, views, window="offline") rasterises the meshes of the saved map (a colour per material, a
simple sun, outlines between objects; props as boxes, plants as see-through boxes) from the same views as the game
frames, from above and from the side. It shows gaps, slabs, buried or floating pieces and overlaps before any
build; light, textures and models are the game's job. Only numpy and Pillow. Specification: docs/specs/pipeline.md
("Offline preview").
"""
from __future__ import annotations

import colorsys
import hashlib
import json
import math
import re
import time
from pathlib import Path

import numpy as np

from . import faceindex
from .config import PROJECT_ROOT
from .vmap import MapError

SIZE = (960, 540)
MAX_VIEWS = 12
NEAR = 4.0
HFOV_4_3 = 90.0                       # the game's field of view (at 4:3; wider screens see more to the sides)
SKY_TOP, SKY_LOW = (118, 160, 214), (198, 216, 232)
CHUNK = 3_000_000                     # pixel candidates rasterised at once (memory)
COLORS = PROJECT_ROOT / '.local' / 'cache' / 'material_colors.json'

# The look of a material by a word in its name (first match wins): enough to tell grass from asphalt from brick.
_WORDS = (
    (('toolsskybox', 'sky'), (150, 185, 225)),
    (('clip', 'nodraw', 'trigger', 'tools'), (235, 110, 200)),
    (('water', 'river', 'sea', 'pond'), (70, 120, 150)),
    (('glass', 'window', 'pane'), (150, 190, 205)),
    (('grass', 'lawn', 'foliage', 'moss', 'hedge'), (96, 128, 64)),
    (('dirt', 'soil', 'mud', 'earth', 'ground'), (122, 100, 74)),
    (('sand', 'beach', 'dust'), (196, 178, 136)),
    (('snow', 'ice'), (228, 234, 240)),
    (('asphalt', 'tarmac', 'road'), (72, 72, 76)),
    (('gravel', 'pebble', 'cobble'), (128, 122, 112)),
    (('sidewalk', 'pavement', 'paving', 'tile'), (158, 154, 146)),
    (('brick',), (150, 84, 64)),
    (('roof', 'terracotta', 'shingle'), (140, 74, 58)),
    (('wood', 'plank', 'timber', 'crate', 'fence_wood'), (136, 100, 64)),
    (('rust',), (128, 78, 52)),
    (('metal', 'steel', 'iron', 'chrome', 'pipe', 'corrug'), (118, 126, 134)),
    (('plaster', 'stucco', 'facade'), (212, 190, 150)),
    (('concrete', 'cement', 'conc_'), (150, 148, 142)),
    (('rock', 'stone', 'cliff'), (126, 120, 110)),
    (('paint', 'wall'), (196, 186, 168)),
    (('dev_measure', 'dev/'), (170, 170, 170)),
)
_PLANT = ('tree', 'bush', 'hedge', 'shrub', 'plant', 'foliage', 'vegetation', 'palm', 'pine', 'fern', 'ivy', 'grass')


# -- colours -----------------------------------------------------------------------------------------------------------
def _name_color(material: str) -> tuple[int, int, int]:
    low = material.lower()
    for words, color in _WORDS:
        if any(w in low for w in words):
            base = color
            break
    else:
        base = (168, 160, 150)
    # A small steady shift per material, so two plasters side by side are told apart.
    h = int(hashlib.sha1(low.encode()).hexdigest()[:6], 16)
    hue, light, sat = colorsys.rgb_to_hls(*(c / 255 for c in base))
    hue = (hue + ((h & 0xff) / 255 - 0.5) * 0.03) % 1.0
    light = min(0.92, max(0.08, light + (((h >> 8) & 0xff) / 255 - 0.5) * 0.08))
    return tuple(int(round(c * 255)) for c in colorsys.hls_to_rgb(hue, light, sat))


def _source_color(s, material: str) -> tuple[int, int, int] | None:
    """The mean colour of an addon material's own colour texture (its .vmat names the picture)."""
    vmat = s.content_addon / material
    try:
        text = vmat.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    found = re.search(r'TextureColor\w*"?\s+"([^"]+\.(?:png|jpg|jpeg|tga|bmp))"', text, re.I)
    if not found:
        return None
    picture = s.content_addon / found.group(1)
    try:
        from PIL import Image
        with Image.open(picture) as im:
            r, g, b = im.convert('RGB').resize((8, 8)).resize((1, 1)).getpixel((0, 0))
    except Exception:                                  # noqa: BLE001 - any unreadable picture: the name decides
        return None
    tint = re.search(r'g_vColorTint"?\s+"\[([\d.\s]+)\]"', text)
    if tint:
        try:
            t = [float(x) for x in tint.group(1).split()[:3]]
            r, g, b = (int(min(255, c * k)) for c, k in zip((r, g, b), t))
        except ValueError:
            pass
    return r, g, b


def material_colors(s, materials: list[str]) -> np.ndarray:
    """(M, 3) uint8: one colour per material; own materials from their picture (cached), stock ones by name."""
    try:
        cache = json.loads(COLORS.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        cache = {}
    out, dirty = [], False
    for material in materials:
        key = f'{s.addon}/{material}'
        entry = cache.get(key)
        vmat = s.content_addon / material
        try:
            stamp = vmat.stat().st_mtime_ns
        except OSError:
            stamp = 0
        if not entry or entry[0] != stamp:
            color = (_source_color(s, material) if stamp else None) or _name_color(material)
            entry = [stamp, list(color)]
            cache[key] = entry
            dirty = True
        out.append(entry[1])
    if dirty:
        try:
            COLORS.parent.mkdir(parents=True, exist_ok=True)
            COLORS.write_text(json.dumps(cache), encoding='utf-8')
        except OSError:
            pass
    return np.asarray(out or [[168, 160, 150]], dtype=np.float32)


# -- geometry ----------------------------------------------------------------------------------------------------------
_BOX_TRIS = np.asarray([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                        [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
# A crown: a double pyramid over a hexagon (top, bottom, six around the middle).
_CROWN = np.asarray([[math.cos(i * math.pi / 3), math.sin(i * math.pi / 3), 0.0] for i in range(6)]
                    + [[0, 0, 1.0], [0, 0, -1.0]])
_CROWN_TRIS = np.asarray([[i, (i + 1) % 6, 6] for i in range(6)] + [[(i + 1) % 6, i, 7] for i in range(6)])


def _box_corners(lo, hi) -> np.ndarray:
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    return np.asarray([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                       [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], dtype=np.float64)


def _prop_boxes(props: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Props as simple solids: (T, 3, 3) triangles, (T,) prop index, (T,) see-through, (T,) a plant's green.
    A prop is the box of its model in its own frame; a plant is a thin trunk under a see-through crown."""
    empty = np.zeros(0, bool)
    if not props:
        return np.zeros((0, 3, 3), np.float32), np.zeros(0, np.int32), empty, empty
    tris, owner, thin, green = [], [], [], []
    for i, p in enumerate(props):
        local = p.get('local')
        lo, hi = local if local else (p['lo'], p['hi'])
        plant = any(w in p['model'].lower() for w in _PLANT)
        tall = hi[2] - lo[2]
        parts = []
        if plant and tall > 72:
            cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
            half = max(2.0, min(hi[0] - lo[0], hi[1] - lo[1]) * 0.04)
            parts.append((_box_corners((cx - half, cy - half, lo[2]), (cx + half, cy + half, lo[2] + tall * 0.5))
                          [_BOX_TRIS], False))
            radius = np.asarray([(hi[0] - lo[0]) * 0.42, (hi[1] - lo[1]) * 0.42, tall * 0.3])
            crown = _CROWN * radius + np.asarray([cx, cy, lo[2] + tall * 0.68])
            parts.append((crown[_CROWN_TRIS], True))
        else:
            parts.append((_box_corners(lo, hi)[_BOX_TRIS], plant))
        for part, see_through in parts:
            if local:
                part = part * np.asarray(p.get('scales') or (1, 1, 1))
                part = part @ faceindex._rotation(p.get('angles') or (0, 0, 0)).T + np.asarray(p['origin'])
            tris.append(part)
            owner.append(np.full(len(part), i, dtype=np.int32))
            thin.append(np.full(len(part), see_through))
            green.append(np.full(len(part), plant))
    return (np.concatenate(tris).astype(np.float32), np.concatenate(owner), np.concatenate(thin),
            np.concatenate(green))


def _axes(ang) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """forward, right, up of Source angles (pitch down is positive, yaw 0 = +X, 90 = +Y)."""
    pitch, yaw = math.radians(float(ang[0])), math.radians(float(ang[1]))
    cp, sp, cy, sy = math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw)
    return (np.asarray([cp * cy, cp * sy, -sp]), np.asarray([sy, -cy, 0.0]), np.asarray([sp * cy, sp * sy, cp]))


def _clip_near(cam: np.ndarray, ident: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Cut camera-space triangles (T, 3, 3; z = depth) at the near plane; keeps the winding."""
    d = cam[:, :, 2] - NEAR
    inside = d > 0
    count = inside.sum(axis=1)
    out, ids = [cam[count == 3]], [ident[count == 3]]
    for case in (1, 2):
        sel = count == case
        if not sel.any():
            continue
        tri, dd, ins = cam[sel], d[sel], inside[sel]
        odd = np.argmax(ins if case == 1 else ~ins, axis=1)          # the lone vertex goes first
        idx = (odd[:, None] + np.arange(3)[None, :]) % 3
        rows = np.arange(len(tri))[:, None]
        tri, dd = tri[rows, idx], dd[rows, idx]
        v0, v1, v2 = tri[:, 0], tri[:, 1], tri[:, 2]
        t1 = (dd[:, 0] / (dd[:, 0] - dd[:, 1]))[:, None]
        t2 = (dd[:, 0] / (dd[:, 0] - dd[:, 2]))[:, None]
        p1, p2 = v0 + (v1 - v0) * t1, v0 + (v2 - v0) * t2
        if case == 1:
            out.append(np.stack([v0, p1, p2], axis=1))
            ids.append(ident[sel])
        else:
            out += [np.stack([p1, v1, v2], axis=1), np.stack([p1, v2, p2], axis=1)]
            ids += [ident[sel], ident[sel]]
    return np.concatenate(out), np.concatenate(ids)


def _raster(screen: np.ndarray, ident: np.ndarray, stipple: np.ndarray, width: int, height: int):
    """screen (T, 3, 3): x, y in pixels and a depth key (bigger = nearer); ident (T,): the number each triangle
    is reported by. Returns (H*W,) key and triangle number buffers (-1 = nothing). stipple (T,) bool: drawn on
    every other pixel (see-through)."""
    zbuf = np.full(width * height, -np.inf, dtype=np.float32)
    tbuf = np.full(width * height, -1, dtype=np.int64)
    if not len(screen):
        return zbuf, tbuf
    x, y, k = screen[:, :, 0].astype(np.float64), screen[:, :, 1].astype(np.float64), screen[:, :, 2].astype(np.float64)
    x0 = np.clip(np.floor(x.min(axis=1)), 0, width - 1).astype(np.int64)
    x1 = np.clip(np.ceil(x.max(axis=1)), 0, width - 1).astype(np.int64)
    y0 = np.clip(np.floor(y.min(axis=1)), 0, height - 1).astype(np.int64)
    y1 = np.clip(np.ceil(y.max(axis=1)), 0, height - 1).astype(np.int64)
    on = (x.max(axis=1) >= 0) & (x.min(axis=1) < width) & (y.max(axis=1) >= 0) & (y.min(axis=1) < height)
    area = (x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0]) - (x[:, 2] - x[:, 0]) * (y[:, 1] - y[:, 0])
    on &= np.abs(area) > 1e-9
    span = np.maximum(x1 - x0, y1 - y0) + 1
    order = np.nonzero(on)[0]
    if not len(order):
        return zbuf, tbuf
    cls = np.ceil(np.log2(np.maximum(span[order], 1))).astype(np.int64)
    k32 = k.astype(np.float32)
    for c in np.unique(cls):
        size = 1 << int(c)
        group = order[cls == c]
        step = max(1, CHUNK // (size * size))
        centre = np.arange(size, dtype=np.float32) + 0.5
        cell = np.arange(size, dtype=np.int64)
        for start in range(0, len(group), step):
            g = group[start:start + step]
            # Barycentric weights as planes over the triangle's own pixel box (small numbers: float32 is enough).
            ox, oy = x0[g].astype(np.float64), y0[g].astype(np.float64)
            ax, ay, bx, by = x[g, 0] - ox, y[g, 0] - oy, x[g, 1] - ox, y[g, 1] - oy
            qx, qy = x[g, 2] - ox, y[g, 2] - oy
            inv = 1.0 / area[g]
            a0, b0 = -(qy - by) * inv, (qx - bx) * inv
            c0 = -b0 * by - a0 * bx
            a1, b1 = -(ay - qy) * inv, (ax - qx) * inv
            c1 = -b1 * qy - a1 * qx

            def plane(aa, bb, cc):
                return (aa.astype(np.float32)[:, None, None] * centre[None, None, :]
                        + (bb.astype(np.float32)[:, None, None] * centre[None, :, None]
                           + cc.astype(np.float32)[:, None, None]))
            w0, w1 = plane(a0, b0, c0), plane(a1, b1, c1)
            hit = (w0 >= 0) & (w1 >= 0) & (w0 + w1 <= 1)
            hit &= cell[None, None, :] <= (x1[g] - x0[g])[:, None, None]
            hit &= cell[None, :, None] <= (y1[g] - y0[g])[:, None, None]
            ti, yi, xi = np.nonzero(hit)
            if not len(ti):
                continue
            px, py = x0[g][ti] + xi, y0[g][ti] + yi
            tri = g[ti]
            if stipple[g].any():
                keep = ~stipple[tri] | (((px + py) & 1) == 0)
                ti, yi, xi, px, py, tri = ti[keep], yi[keep], xi[keep], px[keep], py[keep], tri[keep]
            u, v = w0[ti, yi, xi], w1[ti, yi, xi]
            key = u * k32[tri, 0] + v * k32[tri, 1] + (1 - u - v) * k32[tri, 2]
            pix = py * width + px
            best = zbuf.copy()
            np.maximum.at(best, pix, key)
            win = (key >= best[pix]) & (key > zbuf[pix])
            tbuf[pix[win]] = tri[win]
            zbuf = best
    return zbuf, np.where(tbuf >= 0, ident[np.maximum(tbuf, 0)], -1)       # the caller's own triangle numbers


class _Geometry:
    """What one map draws: triangles with a colour, an object id and a see-through flag."""

    def __init__(self, s, scene: faceindex.Scene):
        keep = (scene.flags & faceindex.TOOL) == 0
        tris = scene.tris[keep]
        colors = material_colors(s, scene.materials)[scene.mat[keep]]
        ident = scene.node[keep].astype(np.int64)
        boxes, owner, thin, green = _prop_boxes(scene.props)
        if len(boxes):
            pcolor = np.where(green[:, None], np.asarray([[78, 118, 60]], np.float32),
                              np.asarray([[150, 140, 128]], np.float32))
            pcolor[green & ~thin] = (96, 78, 58)                       # trunks
            pid = np.asarray([int(p['id'] or 0) for p in scene.props], dtype=np.int64)[owner] + (1 << 40)
            tris = np.concatenate([tris, boxes])
            colors = np.concatenate([colors, pcolor.astype(np.float32)])
            ident = np.concatenate([ident, pid])
            self.stipple = np.concatenate([np.zeros(int(keep.sum()), bool), thin])
        else:
            self.stipple = np.zeros(len(tris), bool)
        self.tris, self.ident = tris.astype(np.float64), ident
        a, b, c = self.tris[:, 0], self.tris[:, 1], self.tris[:, 2]
        n = np.cross(b - a, c - a)
        length = np.linalg.norm(n, axis=1)
        self.normal = n / np.where(length > 1e-12, length, 1.0)[:, None]
        sun = scene.sun or (50.0, 135.0, 0.0)
        to_sun = -_axes(sun)[0]
        if to_sun[2] < 0.15:                               # a night map or a sun under the horizon: light from above
            to_sun = np.asarray([0.35, 0.25, 0.9])
            to_sun /= np.linalg.norm(to_sun)
        lambert = np.clip(self.normal @ to_sun, 0.0, 1.0)
        sky = np.clip(self.normal[:, 2] * 0.5 + 0.5, 0.0, 1.0)
        self.color = colors * (0.38 + 0.47 * lambert + 0.15 * sky)[:, None]
        self.bounds = scene.core or scene.bounds


def _shade(geo: _Geometry, tbuf: np.ndarray, depth: np.ndarray | None, width: int, height: int, sky: bool) -> np.ndarray:
    img = np.zeros((height * width, 3), dtype=np.float32)
    hit = tbuf >= 0
    if sky:
        rows = np.repeat(np.linspace(0.0, 1.0, height, dtype=np.float32), width)[:, None]
        img[:] = np.asarray(SKY_TOP, np.float32) * (1 - rows) + np.asarray(SKY_LOW, np.float32) * rows
    else:
        img[:] = (36, 38, 44)
    img[hit] = geo.color[tbuf[hit]]
    if depth is not None:                                  # a little haze with distance: depth without light
        haze = np.clip(depth[hit] / 24000.0, 0.0, 0.55)[:, None]
        img[hit] = img[hit] * (1 - haze) + np.asarray(SKY_LOW, np.float32) * haze
    ident = np.where(hit, geo.ident[np.where(hit, tbuf, 0)], -1).reshape(height, width)
    edge = np.zeros((height, width), bool)
    edge[:, :-1] |= ident[:, :-1] != ident[:, 1:]
    edge[:-1, :] |= ident[:-1, :] != ident[1:, :]
    img = img.reshape(height, width, 3)
    img[edge & hit.reshape(height, width)] *= 0.62
    return np.clip(img, 0, 255).astype(np.uint8)


def _perspective(geo: _Geometry, eye, ang, width: int, height: int) -> np.ndarray:
    forward, right, up = _axes(ang)
    eye = np.asarray(eye, dtype=np.float64)
    front = np.einsum('ij,ij->i', geo.normal, eye[None, :] - geo.tris[:, 0]) > 0       # faces are one-sided
    idx = np.nonzero(front)[0]
    rel = geo.tris[idx] - eye
    cam = np.stack([rel @ right, rel @ up, rel @ forward], axis=2)
    ok = (cam[:, :, 2] > NEAR).any(axis=1)
    cam, idx = _clip_near(cam[ok], idx[ok])
    tan_h = math.tan(math.radians(HFOV_4_3) / 2) * (width / height) / (4 / 3)
    tan_v = tan_h * height / width
    z = cam[:, :, 2]
    sx = (cam[:, :, 0] / (z * tan_h) + 1) * 0.5 * width
    sy = (1 - cam[:, :, 1] / (z * tan_v)) * 0.5 * height
    screen = np.stack([sx, sy, 1.0 / z], axis=2)
    zbuf, tbuf = _raster(screen, idx, geo.stipple[idx], width, height)
    depth = np.where(tbuf >= 0, 1.0 / np.maximum(zbuf, 1e-9), 0.0)
    return _shade(geo, tbuf, depth, width, height, sky=True)


def _ortho(geo: _Geometry, box, yaw: float | None, width: int, height: int) -> tuple[np.ndarray, dict]:
    """From above (yaw None; north = +Y up) or from the side, looking along `yaw` at the box. Returns the picture
    and its scale {units_per_pixel, x, y | along, z}."""
    (x0, y0), (x1, y1) = box

    def touching(ax0, ay0, ax1, ay1):
        return ((geo.tris[:, :, 0].max(axis=1) >= ax0) & (geo.tris[:, :, 0].min(axis=1) <= ax1) &
                (geo.tris[:, :, 1].max(axis=1) >= ay0) & (geo.tris[:, :, 1].min(axis=1) <= ay1))
    if yaw is None:
        scale = max((x1 - x0) / width, (y1 - y0) / height)
        ox = (x0 + x1) / 2 - scale * width / 2
        oy = (y0 + y1) / 2 + scale * height / 2
        # Everything the picture shows, also beside the box (the picture is wider than a square zone).
        idx = np.nonzero(touching(ox, oy - scale * height, ox + scale * width, oy) & (geo.normal[:, 2] > 0))[0]
        t = geo.tris[idx]
        screen = np.stack([(t[:, :, 0] - ox) / scale, (oy - t[:, :, 1]) / scale, t[:, :, 2]], axis=2)
        info = {'units_per_pixel': round(scale, 2), 'x': [round(ox), round(ox + scale * width)],
                'y': [round(oy - scale * height), round(oy)]}
    else:
        rad = math.radians(float(yaw))
        look = np.asarray([math.cos(rad), math.sin(rad), 0.0])
        right = np.asarray([math.sin(rad), -math.cos(rad), 0.0])
        idx = np.nonzero(touching(x0, y0, x1, y1) & (geo.normal @ look < 0))[0]
        t = geo.tris[idx]
        if not len(t):
            return _shade(geo, np.full(width * height, -1, np.int64), None, width, height, sky=False), {}
        along, zs = t @ right, t[:, :, 2]
        corners = np.asarray([[x0, y0, 0], [x1, y0, 0], [x1, y1, 0], [x0, y1, 0]]) @ right
        a0, a1 = corners.min(), corners.max()
        within = ((t[:, :, 0] >= x0) & (t[:, :, 0] <= x1) & (t[:, :, 1] >= y0) & (t[:, :, 1] <= y1))
        zin = zs[within] if within.any() else zs.ravel()
        # The heights most of the zone lies at, plus as much again each way: a skirt 4000 units deep or a far
        # backdrop over it would shrink the street to a line.
        low, high = np.percentile(zin, [5, 95])
        span = max(float(high - low), 256.0)
        z0, z1 = max(float(zin.min()), low - span), min(float(zin.max()), high + span)
        scale = max((a1 - a0) / width, (z1 - z0) / height) * 1.04
        oa = (a0 + a1) / 2 - scale * width / 2
        oz = (z0 + z1) / 2 + scale * height / 2
        screen = np.stack([(along - oa) / scale, (oz - zs) / scale, -(t @ look)], axis=2)
        info = {'units_per_pixel': round(float(scale), 2), 'z': [round(float(oz - scale * height)), round(float(oz))],
                'looking_along_yaw': float(yaw)}
    _, tbuf = _raster(screen, idx, geo.stipple[idx], width, height)
    return _shade(geo, tbuf, None, width, height, sky=False), info


# -- the tool ----------------------------------------------------------------------------------------------------------
def _box(value, bounds):
    if value in (None, True, 'all'):
        if not bounds:
            raise MapError('the map has no geometry to look at')
        return [[bounds[0][0], bounds[0][1]], [bounds[1][0], bounds[1][1]]]
    try:
        (x0, y0), (x1, y1) = ((float(v) for v in corner) for corner in value)
    except (TypeError, ValueError):
        raise MapError('a box is [[x0, y0], [x1, y1]] (a zone seen from above)') from None
    if abs(x1 - x0) < 1 or abs(y1 - y0) < 1:
        raise MapError('a box needs a size')
    return [[min(x0, x1), min(y0, y1)], [max(x0, x1), max(y0, y1)]]


def default_views(bounds) -> list[dict]:
    (x0, y0, z0), (x1, y1, z1) = bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    reach = max(x1 - x0, y1 - y0)
    high = z1 + reach * 0.45
    out = [{'name': 'top', 'top': None}]
    for name, dx, dy in (('from_south_west', -1, -1), ('from_north_east', 1, 1)):
        ex, ey = cx + dx * reach * 0.55, cy + dy * reach * 0.55
        yaw = math.degrees(math.atan2(cy - ey, cx - ex))
        pitch = math.degrees(math.atan2(high - (z0 + z1) / 2, math.hypot(cx - ex, cy - ey)))
        out.append({'name': name, 'pos': [ex, ey, high - 64], 'ang': [pitch, yaw, 0]})
    return out


def render(project, name: str, views: list[dict] | None = None, size=SIZE) -> dict:
    """Draw map `name` from `views` (default: from above and two bird's-eye views) on one labelled sheet.
    A view: {"name", "pos": [x, y, z] feet, "ang": [pitch, yaw, 0]} as in the game; {"top": [[x0, y0], [x1, y1]]}
    (or true: the whole map) from above; {"side": box or true, "yaw": 0} from the side, looking along yaw.
    Returns {sheet, shots: [{name, path, ...}], seconds, triangles}."""
    from PIL import Image, ImageDraw
    started = time.monotonic()
    scene = faceindex.scene(project, name)
    geo = _Geometry(project.s, scene)
    if not len(geo.tris):
        raise MapError(f'map {name} has no geometry to draw')
    views = views or default_views(geo.bounds)
    if not isinstance(views, list) or len(views) > MAX_VIEWS:
        raise MapError(f'offline views: a list of 1..{MAX_VIEWS} views')
    width, height = size
    stamp = time.strftime('%Y%m%d-%H%M%S')
    folder = project.s.screenshots if hasattr(project.s, 'screenshots') else PROJECT_ROOT / 'screenshots'
    folder.mkdir(parents=True, exist_ok=True)
    shots, pictures = [], []
    for i, view in enumerate(views):
        if not isinstance(view, dict):
            raise MapError('each view is an object: {"name", "pos", "ang"} | {"top": box} | {"side": box, "yaw"}')
        label = re.sub(r'[^a-z0-9_]+', '_', str(view.get('name') or f'view{i + 1}').lower()).strip('_')[:48] or 'view'
        shot: dict = {'name': label}
        if 'top' in view:
            box = _box(view['top'], geo.bounds)
            picture, info = _ortho(geo, box, None, width, height)
            shot.update(info, view='top (north up)')
        elif 'side' in view:
            box = _box(view['side'], geo.bounds)
            picture, info = _ortho(geo, box, float(view.get('yaw') or 0), width, height)
            shot.update(info, view='side')
        elif view.get('pos') is not None:
            try:
                pos = [float(v) for v in view['pos']][:3]
                ang = [float(v) for v in (view.get('ang') or (0, 0, 0))][:3] + [0.0, 0.0]
            except (TypeError, ValueError):
                raise MapError(f'view {label}: pos [x, y, z] and ang [pitch, yaw, 0] are numbers') from None
            if len(pos) != 3:
                raise MapError(f'view {label}: pos is [x, y, z] (feet; the eye is 64 higher)')
            eye = [pos[0], pos[1], pos[2] + 64.0]
            picture = _perspective(geo, eye, ang, width, height)
            shot.update(eye=[round(v, 1) for v in eye], ang=[round(ang[0], 1), round(ang[1], 1)])
        else:
            raise MapError(f'view {label}: give pos (+ ang), "top" or "side"')
        path = folder / f'offline_{label}-{stamp}-{i:02d}.png'
        Image.fromarray(picture).save(path)
        shot['path'] = str(path)
        shots.append(shot)
        pictures.append((label, shot, picture))
    cols = 1 if len(pictures) == 1 else 2
    cell_w, cell_h = (width, height) if cols == 1 else (width * 2 // 3, height * 2 // 3)
    rows = (len(pictures) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * cell_w, rows * (cell_h + 22)), (24, 24, 28))
    draw = ImageDraw.Draw(sheet)
    for i, (label, shot, picture) in enumerate(pictures):
        x, y = (i % cols) * cell_w, (i // cols) * (cell_h + 22)
        sheet.paste(Image.fromarray(picture).resize((cell_w, cell_h), Image.LANCZOS), (x, y + 22))
        if 'units_per_pixel' in shot:
            extra = ' '.join(f'{k} {v[0]}..{v[1]}' for k, v in shot.items() if k in ('x', 'y', 'z'))
            caption = f'{label}  -  {shot["view"]}  {extra}'
        else:
            caption = f'{label}  -  eye {shot["eye"]}  ang {shot["ang"]}'
        draw.text((x + 6, y + 5), caption[:150], fill=(230, 230, 230))
    sheet_path = folder / f'offline_{name}-{stamp}.jpg'
    sheet.save(sheet_path, quality=88)
    return {'map': name, 'sheet': str(sheet_path), 'shots': shots, 'triangles': int(len(geo.tris)),
            'seconds': round(time.monotonic() - started, 1),
            'note': 'offline preview from the map source: shapes, places and material colours only (no light, '
                    'textures or model shapes; props are boxes, plants see-through). The game shows the final look'}
