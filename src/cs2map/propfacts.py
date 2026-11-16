"""Facts of every stock model, shipped in git (docs/specs/atlas.md, "Facts of every model").

data/props/facts.jsonl holds one sorted line per stock model: its file CRC, the render bounds, the triangle count,
the physics bounds with the collision footprint per height band, and companion models (doors, glass, details
shipped as separate files). Numbers only: no images, no Valve data beyond sizes. Built once by the integrator with
`python tools/prop_catalog.py facts` (Source2Viewer-CLI geometry exports, which also write `<model>_physics.glb`);
after a game update only models whose CRC changed are measured again. Search, previews and add_model read it
instead of measuring on every PC.

Bands are the physics footprint (x0, y0, x1, y1, model axes) of the triangles that reach into a height range over
the model origin: `foot` 0-72 (a player), `roof` 72-160 (a car or bus body), `top` over 160. A tree whose crown
collides at 72-160 stops a bus that clears its trunk (a town map, 31.10).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
import time
import uuid
from pathlib import Path

from .config import PROJECT_ROOT

FACTS = PROJECT_ROOT / 'src' / 'cs2map' / 'data' / 'props' / 'facts.jsonl'
FACTS_VERSION = 1
M_TO_INCH = 1 / 0.0254
BANDS = (('foot', 0.0, 72.0), ('roof', 72.0, 160.0), ('top', 160.0, 1e9))
COMPANION = re.compile(r'_(details?|glass|door\w*|lid|top|bottom|base|frame|cap|light|lamp|broken|gibs?)\d*$')

_cache: dict | None = None
_cache_stamp = None


def load() -> dict:
    """{model path: facts}; empty when the file is missing (an old checkout)."""
    global _cache, _cache_stamp
    try:
        st = FACTS.stat()
    except OSError:
        return {}
    stamp = (st.st_mtime_ns, st.st_size)
    if _cache is None or _cache_stamp != stamp:
        out = {}
        for line in FACTS.read_text(encoding='utf-8').splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and isinstance(rec.get('model'), str):
                out[rec['model']] = rec
        _cache, _cache_stamp = out, stamp
    return _cache


def get(model: str, crc: str | None = None) -> dict | None:
    """Facts of one model; None when unknown or (with crc) measured from another version of the file."""
    rec = load().get(model)
    if rec is None or (crc is not None and rec.get('crc') != crc):
        return None
    return rec


def summary(model: str, crc: str | None = None) -> str:
    """One short clause for search results: where the origin sits, what collides, which parts come separately."""
    rec = get(model, crc)
    if rec is None or not rec.get('lo'):
        return ''
    lo, hi = rec['lo'], rec['hi']
    out = []
    size = [b - a for a, b in zip(lo, hi)]
    if size[2] > 4 and lo[2] < -0.25 * size[2]:
        out.append(f'origin {(-lo[2] / size[2]):.0%} up (z {lo[2]:.0f}..{hi[2]:.0f})')
    off = [k for k in (0, 1) if size[k] > 8 and not (lo[k] + 0.3 * size[k] <= 0 <= hi[k] - 0.3 * size[k])]
    if off:
        out.append('origin off centre (' + ', '.join(f'{"xy"[k]} {lo[k]:.0f}..{hi[k]:.0f}' for k in off) + ')')
    phys = rec.get('phys')
    if not phys:
        out.append('no collision')
    else:
        bands = phys['bands']
        area = {k: (b[2] - b[0]) * (b[3] - b[1]) for k, b in bands.items()}
        foot = area.get('foot', 0)
        wide = [k for k in ('roof', 'top') if area.get(k, 0) > 2 * foot + 100]
        if wide:
            b = bands[wide[0]]
            out.append(f'collides wider above 72 (x {b[0]:.0f}..{b[2]:.0f}, y {b[1]:.0f}..{b[3]:.0f}, up to '
                       f'z {phys["hi"][2]:.0f}): vehicles hit it')
    if rec.get('kin'):
        out.append('parts: ' + ', '.join(k.rsplit('/', 1)[-1][:-5] for k in rec['kin'][:4]))
    return '; '.join(out)


# -- glb geometry ----------------------------------------------------------------------------------------------
_COMPONENT = {5120: 'b', 5121: 'B', 5122: 'h', 5123: 'H', 5125: 'I', 5126: 'f'}
_WIDTH = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}


def _read_glb(path: Path):
    raw = path.read_bytes()
    if raw[:4] != b'glTF':
        raise ValueError(f'{path.name} is not a glb')
    n = struct.unpack_from('<I', raw, 12)[0]
    doc = json.loads(raw[20:20 + n])
    pos = 20 + n
    binary = b''
    if pos + 8 <= len(raw):
        size, kind = struct.unpack_from('<I4s', raw, pos)
        if kind == b'BIN\x00':
            binary = raw[pos + 8:pos + 8 + size]
    return doc, binary


def _accessor(doc, binary, index):
    acc = doc['accessors'][index]
    view = doc['bufferViews'][acc['bufferView']]
    fmt = _COMPONENT[acc['componentType']]
    width = _WIDTH[acc['type']]
    item = struct.calcsize('<' + fmt) * width
    stride = view.get('byteStride') or item
    start = view.get('byteOffset', 0) + acc.get('byteOffset', 0)
    count = acc['count']
    if stride == item:
        flat = struct.unpack_from(f'<{count * width}{fmt}', binary, start)
        return [flat[i * width:(i + 1) * width] for i in range(count)]
    return [struct.unpack_from(f'<{width}{fmt}', binary, start + i * stride) for i in range(count)]


def triangles(path: Path) -> list:
    """Every triangle of a Source2Viewer glb in Source inches and model axes: [(a, b, c), ...]."""
    from .atlas import _mat_mul, _node_matrix
    doc, binary = _read_glb(path)
    out = []

    def walk(i, parent):
        node = doc['nodes'][i]
        m = _mat_mul(parent, _node_matrix(node))
        if 'mesh' in node:
            for prim in doc['meshes'][node['mesh']]['primitives']:
                if prim.get('mode', 4) != 4 or 'POSITION' not in prim['attributes']:
                    continue
                pts = []
                for x, y, z in _accessor(doc, binary, prim['attributes']['POSITION']):
                    gx = m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3]
                    gy = m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3]
                    gz = m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3]
                    pts.append((gz * M_TO_INCH, gx * M_TO_INCH, gy * M_TO_INCH))   # Source = glTF (z, x, y)
                if 'indices' in prim:
                    idx = [v[0] for v in _accessor(doc, binary, prim['indices'])]
                else:
                    idx = list(range(len(pts)))
                out.extend((pts[idx[k]], pts[idx[k + 1]], pts[idx[k + 2]]) for k in range(0, len(idx) - 2, 3))
        for child in node.get('children', []):
            walk(child, m)

    identity = [[float(i == j) for j in range(4)] for i in range(4)]
    for scene in doc.get('scenes') or [{'nodes': range(len(doc.get('nodes', [])))}]:
        for i in scene.get('nodes', []):
            walk(i, identity)
    return out


def _box(points) -> tuple[list, list]:
    return ([round(min(p[k] for p in points), 1) + 0.0 for k in range(3)],
            [round(max(p[k] for p in points), 1) + 0.0 for k in range(3)])


def measure(render: Path | None, physics: Path | None) -> dict:
    """Facts from the two exports of one model (either may be missing)."""
    out: dict = {}
    if render is not None and render.is_file():
        tris = triangles(render)
        if tris:
            out['lo'], out['hi'] = _box([p for t in tris for p in t])
            out['tris'] = len(tris)
    if physics is not None and physics.is_file():
        tris = triangles(physics)
        if tris:
            lo, hi = _box([p for t in tris for p in t])
            bands = {}
            for name, z0, z1 in BANDS:
                inside = [t for t in tris if max(p[2] for p in t) > z0 and min(p[2] for p in t) < z1]
                if inside:
                    xs = [p[0] for t in inside for p in t]
                    ys = [p[1] for t in inside for p in t]
                    bands[name] = [round(v, 1) + 0.0 for v in (min(xs), min(ys), max(xs), max(ys))]
            out['phys'] = {'lo': lo, 'hi': hi, 'bands': bands}
    return out


def companions(paths) -> dict[str, list[str]]:
    """{model: [models shipped as its parts]}: same folder, name = model name + a part suffix."""
    by_folder: dict[str, set] = {}
    for p in paths:
        folder, name = p.rsplit('/', 1)
        by_folder.setdefault(folder, set()).add(name[:-5])
    out = {}
    for folder, names in by_folder.items():
        for name in names:
            parts = sorted(f'{folder}/{n}.vmdl' for n in names
                           if n != name and n.startswith(name + '_') and COMPANION.search(n[len(name):]))
            if parts:
                out[f'{folder}/{name}.vmdl'] = parts[:8]
    return out


# -- building the shipped file (integrator) --------------------------------------------------------------------
def build(project, batch: int = 120, limit: int | None = None, say=print) -> dict:
    """Measure every stock model whose CRC is not in facts.jsonl yet; write the file sorted. Resumable: progress is
    written after every batch."""
    from . import atlas
    models = atlas.index(project)
    have = {k: v for k, v in load().items() if k in models and v.get('crc') == models[k]['crc']}
    kin = companions(models)
    todo = sorted(p for p in models if p not in have)
    if limit is not None:
        todo = todo[:limit]
    started, done = time.monotonic(), 0
    say(f'{len(models)} models, {len(have)} current, {len(todo)} to measure')
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        tmp = PROJECT_ROOT / '.local' / 'tmp' / 'propfacts' / uuid.uuid4().hex[:10]
        tmp.mkdir(parents=True)
        try:
            glbs = atlas._vrf(project, chunk, tmp, textures=False)
            for p in chunk:
                render = glbs[p]
                physics = render.with_name(render.stem + '_physics.glb')
                try:
                    rec = measure(render, physics)
                except (OSError, ValueError, KeyError, IndexError, struct.error) as exc:
                    rec = {'error': str(exc)[:80]}
                if not rec.get('lo') and 'error' not in rec:
                    rec['empty'] = True
                have[p] = {'model': p, 'crc': models[p]['crc'], **rec}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        done += len(chunk)
        _write(have, kin)
        rate = done / max(time.monotonic() - started, 1e-3)
        say(f'{done}/{len(todo)} measured, ~{(len(todo) - done) / rate / 60:.1f} min left')
    _write(have, kin)
    return {'models': len(have), 'measured': done, 'seconds': round(time.monotonic() - started, 1)}


def _write(have: dict, kin: dict) -> None:
    lines = []
    for p in sorted(have):
        rec = dict(have[p])
        rec.pop('kin', None)
        if kin.get(p):
            rec['kin'] = kin[p]
        lines.append(json.dumps(rec, sort_keys=True, separators=(',', ':')))
    FACTS.parent.mkdir(parents=True, exist_ok=True)
    tmp = FACTS.with_name(f'{FACTS.name}.{uuid.uuid4().hex[:8]}.tmp')
    tmp.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    os.replace(tmp, FACTS)
