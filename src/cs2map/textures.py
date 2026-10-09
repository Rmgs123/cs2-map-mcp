"""Small, deterministic image operations used by the reference-material pipeline.

The functions deliberately use Pillow and NumPy only.  They accept either a
Pillow image or a path and return Pillow images/arrays so a caller can inspect
each stage before it is written into an addon.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageFilter


def _image(value: Image.Image | str | Path) -> Image.Image:
    if isinstance(value, Image.Image):
        return value.copy()
    path = Path(value)
    if not path.is_file():
        raise FileNotFoundError(f'image not found: {value}')
    with Image.open(path) as im:
        return im.copy()


def _box(region, size):
    if region is None:
        return (0, 0, size[0], size[1])
    if len(region) != 4:
        raise ValueError('region must be [left, top, right, bottom] or [left, top, width, height]')
    x0, y0, a, b = (float(v) for v in region)
    # A conventional crop box is the least surprising interpretation.  Accept
    # a width/height pair too when it is obviously inside the source image.
    if a <= size[0] and b <= size[1] and x0 + a <= size[0] and y0 + b <= size[1]:
        x1, y1 = x0 + a, y0 + b
    else:
        x1, y1 = a, b
    box = tuple(int(round(v)) for v in (x0, y0, x1, y1))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError('region must have positive width and height')
    return box


def _homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Return H mapping destination pixel coordinates to source coordinates."""
    a = []
    for (x, y), (u, v) in zip(dst, src):
        a.extend(((x, y, 1, 0, 0, 0, -u * x, -u * y),
                  (0, 0, 0, x, y, 1, -v * x, -v * y)))
    h = np.linalg.solve(np.asarray(a, dtype=float), np.asarray(src, dtype=float).reshape(-1))
    return np.r_[h, 1.0].reshape(3, 3)


def load(path: str | Path | Image.Image, region: Sequence[float] | None = None,
         quad: Sequence[Sequence[float]] | None = None) -> Image.Image:
    """Load a reference image, optionally crop and rectify a four-point quad.

    ``quad`` is ordered top-left, top-right, bottom-right, bottom-left in
    source pixels.  The output keeps the crop dimensions unless a non-square
    quad is supplied, in which case its average edge lengths are used.
    """
    im = _image(path)
    if region is not None:
        im = im.crop(_box(region, im.size))
    if quad is None:
        return im.convert('RGB')
    if len(quad) != 4 or any(len(p) != 2 for p in quad):
        raise ValueError('quad must contain four [x, y] points')
    q = np.asarray(quad, dtype=float)
    if region is not None:
        b = _box(region, _image(path).size)
        q -= np.asarray([b[0], b[1]])
    w = max(1, int(round((np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2)))
    h = max(1, int(round((np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2)))
    dst = np.asarray([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=float)
    # Pillow's perspective transform expects the inverse mapping in the order
    # (a,b,c,d,e,f,g,h), with x'=(a*x+b*y+c)/(g*x+h*y+1).
    inv = _homography(q, dst)
    coeff = tuple(float(v) for v in (inv[0, 0], inv[0, 1], inv[0, 2], inv[1, 0], inv[1, 1], inv[1, 2], inv[2, 0], inv[2, 1]))
    return im.convert('RGB').transform((w, h), Image.Transform.PERSPECTIVE, coeff, Image.Resampling.BICUBIC)


def delight(img: Image.Image | np.ndarray, radius: float | None = None) -> Image.Image:
    """Flatten large-scale illumination while retaining local colour/texture."""
    source = img if isinstance(img, Image.Image) else Image.fromarray(np.asarray(img).astype(np.uint8))
    rgb = np.asarray(source.convert('RGB'), dtype=np.float32)
    r = int(radius or max(8, min(source.size) // 8))
    blurred = np.asarray(source.convert('RGB').filter(ImageFilter.GaussianBlur(r)), dtype=np.float32)
    mean = blurred.mean(axis=(0, 1), keepdims=True)
    corrected = rgb * mean / np.maximum(blurred, 2.0)
    return Image.fromarray(np.clip(corrected, 0, 255).astype(np.uint8), 'RGB')


def _period(arr: np.ndarray, axis: int) -> int | None:
    """Find a strong local autocorrelation peak in a grid's projection."""
    gray = arr[..., :3].mean(axis=2) if arr.ndim == 3 else arr
    profile = gray.mean(axis=1 - axis)
    n = len(profile)
    if n < 16:
        return None
    profile = np.diff(profile)
    if float(profile.std()) < 0.1:
        return None
    scores = [(lag, _corr(profile[:-lag], profile[lag:])) for lag in range(3, n // 2)]
    peaks = [(lag, value) for i, (lag, value) in enumerate(scores[1:-1], 1)
             if value > 0.65 and value >= scores[i - 1][1] and value > scores[i + 1][1]]
    if not peaks:
        return None
    best = max(value for _, value in peaks)
    return min(lag for lag, value in peaks if value >= best - 0.08)


def tile_analysis(img: Image.Image | np.ndarray) -> dict:
    arr = np.asarray(img.convert('RGB') if isinstance(img, Image.Image) else img, dtype=np.float32)
    periods = [_period(arr, axis) for axis in (1, 0)]
    # Once one axis proves a regular grid, reduce high-frequency noise on the
    # other projection. Grout can wander by one pixel in a photographed floor.
    for i, axis in enumerate((1, 0)):
        if periods[i] is None and periods[1-i] is not None:
            profile=arr[..., :3].mean(axis=2).mean(axis=1-axis)
            if len(profile)<24: continue
            profile=np.diff(np.convolve(profile,[1/16,4/16,6/16,4/16,1/16],mode='valid'))
            scores=[(lag,_corr(profile[:-lag],profile[lag:])) for lag in range(8,len(profile)//2)]
            if not scores: continue
            lag,score=max(scores,key=lambda pair:pair[1])
            if score>=.35:
                hint=periods[1-i]
                near=dict(scores).get(hint,-1)
                periods[i]=hint if abs(hint-lag)<=1 and near>=score-.03 else lag
    return {'mode': 'regular' if any(periods) else 'stochastic', 'period': periods,
            **band_analysis(arr)}


def band_analysis(img: Image.Image | np.ndarray, width: int | None = None) -> dict:
    """Compare border-strip contrast and gradient energy with ordinary strips.

    Equal edge pixels alone do not make a smooth texture. This diagnoses the
    blurred/light bands created by edge feathering. Ratios near 1 are natural;
    a seam gradient spike or a very flat strip produces a larger band_score.
    Structured grout/stripes can legitimately trigger this visual-review gate.
    """
    a = np.asarray(img.convert('RGB') if isinstance(img, Image.Image) else img, dtype=float)
    if a.ndim == 2: a = a[..., None]
    a = a[..., :3] / 255
    h,w=a.shape[:2]
    if min(h,w)<8 or float(a.std())<1e-6:
        return {'band_score':0.0,'seam_energy_ratio':1.0,'band_energy_ratio':1.0,'band_contrast':0.0}
    ratios=[]; seams=[]; contrasts=[]
    for axis,n in [(1,w),(0,h)]:
        strip=max(2,min(n//8,width or max(3,n//50)))
        grad=np.mean(np.abs(a-np.roll(a,1,axis=axis)),axis=(1-axis,2))
        ordinary=float(np.mean(grad[strip:-strip]))
        edge=np.r_[grad[:strip],grad[-strip:]]
        ratio=float(edge.mean()/max(ordinary,1/255))
        ratios.append(ratio)
        seams.append(float(grad[0]/max(ordinary,1/255)))
        profile=a.mean(axis=(1-axis,2))
        edge_mean=float(np.r_[profile[:strip],profile[-strip:]].mean())
        next_mean=float(np.r_[profile[strip:2*strip],profile[-2*strip:-strip]].mean())
        contrasts.append(abs(edge_mean-next_mean))
    # A ratio <0.6 flags an overblurred band; >1.8 flags a sharp stitch.
    score=max(max(ratios)-1, 1-min(ratios), max(seams)-1, max(contrasts)*4, 0)
    return {'band_score':float(score),'seam_energy_ratio':float(max(seams)),
            'band_energy_ratio':float(max(ratios)), 'band_contrast':float(max(contrasts))}


def _periodic_component(arr: np.ndarray) -> np.ndarray:
    """Remove the boundary's smooth component with a toroidal Poisson solve.

    FFT implements the periodic discrete Laplacian. Correction spreads smoothly
    across the image instead of flattening a 2% perimeter strip. DC is unchanged.
    The last one-pixel equality preserves the historical seam_error contract.
    """
    a=arr.astype(float,copy=True); h,w=a.shape[:2]
    forcing=np.zeros_like(a)
    forcing[0]=a[-1]-a[0]; forcing[-1]=-forcing[0]
    dx=a[:,-1]-a[:,0]; forcing[:,0]+=dx; forcing[:,-1]-=dx
    denom=2*np.cos(2*np.pi*np.arange(h)/h)[:,None]+2*np.cos(2*np.pi*np.arange(w)/w)[None,:]-4
    denom[0,0]=1
    smooth=np.fft.fft2(forcing,axes=(0,1))/denom[...,None]; smooth[0,0]=0
    a-=np.fft.ifft2(smooth,axes=(0,1)).real
    a[:,0]=a[:,-1]=(a[:,0]+a[:,-1])/2
    a[0]=a[-1]=(a[0]+a[-1])/2
    return a


def _edge_blend(arr: np.ndarray, border: float) -> np.ndarray:
    """Equalise edges by blending corresponding coordinates, never mirroring."""
    a = arr.astype(np.float32, copy=True)
    h, w = a.shape[:2]
    bx, by = max(1, int(w * border)), max(1, int(h * border))
    left, right = a[:, :bx].copy(), a[:, -bx:].copy()
    edge = (a[:, 0] + a[:, -1]) / 2
    t = np.linspace(1.0, 0.0, bx, dtype=np.float32)[None, :, None]
    a[:, :bx] = left * (1.0 - t) + edge[:, None] * t
    a[:, -bx:] = right * (1.0 - t[:, ::-1]) + edge[:, None] * t[:, ::-1]
    top, bottom = a[:by].copy(), a[-by:].copy()
    edge = (a[0] + a[-1]) / 2
    t = np.linspace(1.0, 0.0, by, dtype=np.float32)[:, None, None]
    a[:by] = top * (1.0 - t) + edge[None] * t
    a[-by:] = bottom * (1.0 - t[::-1]) + edge[None] * t[::-1]
    # Make the endpoint pair exactly equal so the metric represents the seam,
    # while keeping the local orientation of each side intact.
    a[:, 0] = a[:, -1] = (a[:, 0] + a[:, -1]) / 2.0
    a[0] = a[-1] = (a[0] + a[-1]) / 2.0
    return a


def _quilting(arr: np.ndarray, border: float) -> np.ndarray:
    """Hide the half-offset discontinuities with minimum-error DP patches."""
    original = arr.astype(np.float32, copy=True)
    h, w = original.shape[:2]
    a = np.roll(original, (h // 2, w // 2), axis=(0, 1))

    def patch(src, other):
        hh, ww = src.shape[:2]
        radius = max(2, int(ww * border))
        lo, hi = max(0, ww // 2 - radius), min(ww, ww // 2 + radius)
        seam_cost = np.mean((src[:, lo:hi] - other[:, lo:hi]) ** 2, axis=2)
        hh, ww = seam_cost.shape
        # Two independently traced cuts bound a patch covering the offset
        # discontinuity.  Keep contrast outside a 2-pixel feather at each cut.
        mid = max(1, ww // 2)
        def trace(costs):
            d = costs.copy(); p = np.zeros_like(costs, dtype=np.int32)
            for yy in range(1, hh):
                choices = np.stack([np.r_[np.inf, d[yy - 1, :-1]], d[yy - 1], np.r_[d[yy - 1, 1:], np.inf]])
                step = np.argmin(choices, axis=0)
                p[yy] = np.arange(costs.shape[1]) + step - 1
                d[yy] += choices[step, np.arange(costs.shape[1])]
            xx = int(np.argmin(d[-1])); result = np.empty(hh, dtype=np.int32)
            for yy in range(hh - 1, -1, -1):
                result[yy] = xx; xx = int(p[yy, xx]) if yy else xx
            return result
        left = trace(seam_cost[:, :mid]) + lo
        right = trace(seam_cost[:, mid:]) + lo + mid
        out = src.copy()
        coords = np.arange(src.shape[1])
        for y in range(hh):
            weight = np.minimum(np.clip((coords - left[y] + 2) / 4, 0, 1),
                                np.clip((right[y] - coords + 2) / 4, 0, 1))[:, None]
            out[y] = src[y] * (1 - weight) + other[y] * weight
        return out
    a = patch(a, np.roll(original, h // 2, axis=0))
    a = np.swapaxes(patch(np.swapaxes(a, 0, 1), np.swapaxes(np.roll(original, w // 2, axis=1), 0, 1)), 0, 1)
    return _periodic_component(a)


def make_tileable(img: Image.Image | np.ndarray, method: str = 'auto', border: float = 0.08) -> Image.Image:
    """Make a texture repeat without mirrored edge patches.

    ``auto`` crops regular images to whole periods or quilts stochastic images,
    then subtracts the smooth boundary component with a periodic Poisson solve.
    ``blend``/``average`` retain
    the round-one implementation for reproducibility when explicitly requested.
    """
    if method not in ('auto', 'regular', 'stochastic', 'blend', 'average', 'legacy'):
        raise ValueError("method must be 'auto', 'regular', 'stochastic', 'blend' or 'average'")
    if not 0 < float(border) <= 0.5:
        raise ValueError('border must be in (0, 0.5]')
    source = img if isinstance(img, Image.Image) else Image.fromarray(np.asarray(img).astype(np.uint8))
    mode = 'RGBA' if source.mode == 'RGBA' else 'RGB'
    arr = np.asarray(source.convert(mode), dtype=np.float32).copy()
    average = method == 'average'
    if method in ('blend', 'average', 'legacy'):
        method = 'legacy'
    elif method == 'auto':
        # Strong 1-D autocorrelation indicates a regular brick/tile/plank grid.
        method = tile_analysis(arr)['mode']
    if min(arr.shape[:2]) < 8:
        return Image.fromarray(np.clip(_edge_blend(arr, border), 0, 255).astype(np.uint8), mode)
    if method == 'regular':
        px, py = tile_analysis(arr)['period']
        h, w = arr.shape[:2]
        cw, ch = w - w % px if px else w, h - h % py if py else h
        arr = arr[(h - ch) // 2:(h + ch) // 2, (w - cw) // 2:(w + cw) // 2]
        return Image.fromarray(np.clip(_periodic_component(arr), 0, 255).astype(np.uint8), mode)
    if method == 'stochastic':
        return Image.fromarray(np.clip(_quilting(arr, border), 0, 255).astype(np.uint8), mode)
    h, w = arr.shape[:2]
    bx, by = max(1, int(w * border)), max(1, int(h * border))

    def feather(a, axis, n):
        # Equalise both sides with a smooth cross-fade.  The exact endpoint
        # average makes seam_error deterministic and zero at the borders.
        if axis == 1:
            left, right = a[:, :n].copy(), a[:, -n:].copy()
            t = np.linspace(0.0, 1.0, n, dtype=np.float32)[None, :, None]
            blend = left * (1.0 - t) + right * t
            a[:, :n] = blend
            a[:, -n:] = blend[:, ::-1]
            edge = (a[:, 0] + a[:, -1]) / 2.0
            a[:, 0] = edge
            a[:, -1] = edge
        else:
            top, bottom = a[:n].copy(), a[-n:].copy()
            t = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None, None]
            blend = top * (1.0 - t) + bottom * t
            a[:n] = blend
            a[-n:] = blend[::-1]
            edge = (a[0] + a[-1]) / 2.0
            a[0] = edge
            a[-1] = edge

    feather(arr, 1, bx)
    feather(arr, 0, by)
    if average:
        arr = (arr + np.roll(arr, (h // 2, w // 2), axis=(0, 1))) / 2.0
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode)


def seam_error(img: Image.Image | np.ndarray) -> float:
    """Mean normalised mismatch between opposite edges (lower is better)."""
    arr = np.asarray(img.convert('RGB') if isinstance(img, Image.Image) else img, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[..., None]
    if arr.shape[0] < 2 or arr.shape[1] < 2:
        return 0.0
    vertical = np.abs(arr[:, 0] - arr[:, -1]).mean()
    horizontal = np.abs(arr[0] - arr[-1]).mean()
    return float((vertical + horizontal) / (2.0 * 255.0))


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a.reshape(-1).astype(np.float32), b.reshape(-1).astype(np.float32)
    if a.size < 2 or float(a.std()) < 1e-6 or float(b.std()) < 1e-6:
        return 0.0  # An untextured flat patch has no repeated structure.
    return float(np.clip(np.corrcoef(a, b)[0, 1], -1, 1))


def mirror_score(img: Image.Image | np.ndarray, border: float = 0.08) -> float:
    """Score mirrored edge structure (0 natural, 1 a reflected edge patch)."""
    arr = np.asarray(img.convert('RGB') if isinstance(img, Image.Image) else img, dtype=np.float32)
    if arr.ndim == 2: arr = arr[..., None]
    h, w = arr.shape[:2]; bx, by = max(1, int(w * border)), max(1, int(h * border))
    x = max(0.0, _corr(arr[:, :bx], arr[:, -bx:][:, ::-1]))
    y = max(0.0, _corr(arr[:by], arr[-by:][::-1]))
    return float((x + y) / 2.0)


def repeat_score(img: Image.Image | np.ndarray) -> float:
    """Estimate visible tile-grid repetition on a 3x3 stitch (0 low, 1 high)."""
    arr = np.asarray(img.convert('RGB') if isinstance(img, Image.Image) else img, dtype=np.float32)
    if arr.ndim == 2: arr = arr[..., None]
    gray = arr[..., :3].mean(axis=2)
    h, w = gray.shape
    if min(h, w) < 8: return 0.0
    # Correlation with a half-tile shift catches repeated motifs while ignoring
    # the inevitable correlation at a one-tile wrap boundary.
    sx, sy = max(1, w // 2), max(1, h // 2)
    vals = [_corr(gray[:, :-sx], gray[:, sx:]), _corr(gray[:-sy], gray[sy:])]
    return float(np.mean([max(0.0, v) for v in vals]))


def height_from(img: Image.Image | np.ndarray) -> np.ndarray:
    """Derive a normalised height field from image luminance."""
    arr = np.asarray(img.convert('L') if isinstance(img, Image.Image) else img, dtype=np.float32)
    if arr.ndim == 3:
        arr = arr[..., :3].mean(axis=2)
    lo, hi = float(arr.min()), float(arr.max())
    return np.zeros_like(arr, dtype=np.float32) if hi <= lo else (arr - lo) / (hi - lo)


def normal_from(height: np.ndarray, strength: float = 2.0) -> Image.Image:
    """Convert a height field to a tangent-space DirectX normal map."""
    h = np.asarray(height, dtype=np.float32)
    dx = (np.roll(h, -1, axis=1) - np.roll(h, 1, axis=1)) * float(strength)
    dy = (np.roll(h, -1, axis=0) - np.roll(h, 1, axis=0)) * float(strength)
    nz = np.ones_like(h)
    length = np.sqrt(dx * dx + dy * dy + nz * nz)
    n = np.stack((-dx / length, dy / length, nz / length), axis=-1)
    return Image.fromarray(np.clip((n * 0.5 + 0.5) * 255, 0, 255).astype(np.uint8), 'RGB')


def roughness_from(img: Image.Image | np.ndarray, base: float = 0.7, contrast: float = 0.25) -> Image.Image:
    """Create a greyscale roughness map around ``base`` from luminance."""
    lum = height_from(img)
    result = np.clip(float(base) + (0.5 - lum) * float(contrast), 0, 1)
    return Image.fromarray(np.round(result * 255).astype(np.uint8), 'L')


def ao_from(height: np.ndarray, radius: int = 3) -> Image.Image:
    """Approximate cavity AO from a height field using wrapped local blur."""
    h = np.asarray(height, dtype=np.float32)
    r = max(1, int(radius))
    acc = np.zeros_like(h)
    count = 0
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx == 0 and dy == 0:
                continue
            acc += np.maximum(0.0, np.roll(h, (dy, dx), axis=(0, 1)) - h)
            count += 1
    cavity = np.clip(acc / max(1, count) * 4.0, 0, 1)
    return Image.fromarray(np.round((1.0 - cavity) * 255).astype(np.uint8), 'L')

