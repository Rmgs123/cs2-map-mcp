"""Prop atlas: the AI sees stock models cheaply (docs/specs/atlas.md).

Index of facts (path, materials, physics) for every stock model, sizes measured
lazily, offline previews (Source2Viewer-CLI -> Blender) cached by file version,
and short structured notes: personal ones in .local, the shared catalog in git.
Previews are in Source model axes: red arrow = +X (yaw 0), blue box = player.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import time
import uuid
import zlib
from contextlib import contextmanager
from pathlib import Path

from . import blender_run, resource
from .config import PROJECT_ROOT, ensure_inside

CACHE = PROJECT_ROOT / '.local' / 'cache' / 'props'
INDEX = CACHE / 'index.json'
IMAGES = CACHE / 'img'
SHEETS = CACHE / 'sheets'
TMP = PROJECT_ROOT / '.local' / 'tmp' / 'props'
NOTES = PROJECT_ROOT / '.local' / 'props' / 'notes.jsonl'
CATALOG = PROJECT_ROOT / 'src' / 'cs2map' / 'data' / 'props' / 'catalog.jsonl'
VRF = PROJECT_ROOT / 'tools' / 'vrf-cli-20.0' / 'Source2Viewer-CLI.exe'
SCRIPT = PROJECT_ROOT / 'src' / 'cs2map' / 'blender' / 'prop_atlas.py'

INDEX_VERSION = 1
RENDER_VERSION = 4
IMAGE_LIMIT = 1_000_000_000      # bytes of cached tiles; least recently used go first
SHEETS_KEEP = 60
M_TO_INCH = 1 / 0.0254
EXCLUDE = ('models/weapons/', 'models/inventory_items/', 'models/player/', 'models/chicken/',
           'models/hostage/', 'models/characters/', 'models/editor/', 'models/dev/')
CATEGORIES = ('architecture', 'door_window', 'stairs_railing', 'furniture', 'lighting', 'sign', 'street',
              'vegetation', 'rock_terrain', 'vehicle', 'container', 'industrial', 'debris', 'decor',
              'appliance', 'tool', 'other')
MOUNTS = ('floor', 'wall', 'ceiling', 'any')
FRONTS = ('+x', '-x', '+y', '-y', 'none')
_WORD = re.compile(r'^[a-z0-9][a-z0-9_-]{0,19}$')
_CREATE_NO_WINDOW = 0x08000000


class AtlasError(ValueError):
    pass


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'{path.name}.{uuid.uuid4().hex[:8]}.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    os.replace(tmp, path)


@contextmanager
def _file_lock(path: Path, timeout: float = 150):
    """Cross-process lock on a small file; waits while another process renders or writes."""
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a+b') as fh:
        deadline = time.monotonic() + timeout
        while True:
            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise AtlasError(f'{path.stem} is busy in another process; repeat the call') from None
                time.sleep(0.25)
        try:
            yield
        finally:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def _scratch():
    """A private temp folder that is always removed."""
    folder = ensure_inside(TMP / uuid.uuid4().hex[:12], PROJECT_ROOT / '.local' / 'tmp')
    folder.mkdir(parents=True)
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# -- index of facts ----------------------------------------------------------------

def _vpk_key(project) -> list:
    return [[mod, v.path.stat().st_size, int(v.path.stat().st_mtime)] for mod, v in project.game.vpks]


def index(project) -> dict:
    """{model path: {crc, vpk, mats, phys, lo?, hi?}}; rebuilt when the game files change."""
    key = _vpk_key(project)
    try:
        data = json.loads(INDEX.read_text(encoding='utf-8'))
        if data.get('version') == INDEX_VERSION and data.get('key') == key:
            return data['models']
    except (OSError, ValueError):
        pass
    models = {}
    for mod, v in project.game.vpks:
        for name in v.entries:
            if not (name.startswith('models/') and name.endswith('.vmdl_c')) or name.startswith(EXCLUDE):
                continue
            path = name[:-2]
            if path in models:
                continue
            raw = v.read(name)
            refs = resource.external_references(raw)
            mats = sorted({r.rsplit('/', 1)[-1][:-5] for r in refs if r.endswith('.vmat')})
            models[path] = {'crc': f'{zlib.crc32(raw):08x}', 'vpk': mod, 'mats': mats[:6],
                            'phys': 'PHYS' in resource.blocks(raw) or any(r.endswith('.vphys') for r in refs)}
    _write_json(INDEX, {'version': INDEX_VERSION, 'key': key, 'models': models})
    return models


def _save_index(project, models: dict) -> None:
    _write_json(INDEX, {'version': INDEX_VERSION, 'key': _vpk_key(project), 'models': models})


def _model(project, models: dict, value) -> str:
    if not isinstance(value, str):
        raise AtlasError('model must be a string like models/props/x/y.vmdl')
    path = value.strip().replace('\\', '/').lower()
    if path.endswith('.vmdl_c'):
        path = path[:-2]
    if path not in models:
        raise AtlasError(f'unknown stock model {value!r}; find one with prop_search')
    return path


# -- Source2Viewer-CLI exports --------------------------------------------------------

def _vrf(project, paths: list[str], out: Path, textures: bool) -> dict[str, Path]:
    if not VRF.is_file():
        raise AtlasError(f'Source2Viewer-CLI not found: {VRF} (see config/tool-sources.json)')
    models = index(project)
    vpks = dict((mod, v.path) for mod, v in project.game.vpks)
    groups: dict[str, list[str]] = {}
    for p in paths:
        groups.setdefault(models[p]['vpk'], []).append(p)
    for mod, names in groups.items():
        # With exactly one matching file Source2Viewer-CLI treats -o as the output file, not a folder.
        target = out / (names[0][:-5] + '.glb') if len(names) == 1 else out
        target.parent.mkdir(parents=True, exist_ok=True)
        argv = [str(VRF), '-i', str(vpks[mod]), '-d', '-f', ','.join(n + '_c' for n in names),
                '-o', str(target), '--gltf_export_format', 'glb', '--threads', '4']
        if textures:
            argv += ['--gltf_export_materials', '--gltf_textures_adapt',
                     '--game', str(project.s.cs2_path / 'game' / 'csgo' / 'gameinfo.gi')]
        try:
            # stdin is the MCP pipe inside the server: never let a child inherit it.
            subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, timeout=30 + 3 * len(names),
                           creationflags=_CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            pass  # missing glb files are reported per model below
    return {p: out / (p[:-5] + '.glb') for p in paths}


def _mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _node_matrix(node) -> list:
    if 'matrix' in node:
        m = node['matrix']
        return [[m[c * 4 + r] for c in range(4)] for r in range(4)]  # glTF is column-major
    t = node.get('translation', [0, 0, 0]); s = node.get('scale', [1, 1, 1])
    x, y, z, w = node.get('rotation', [0, 0, 0, 1])
    r = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return [[r[i][0] * s[0], r[i][1] * s[1], r[i][2] * s[2], t[i]] for i in range(3)] + [[0, 0, 0, 1]]


def glb_bounds(path: Path):
    """(lo, hi) in Source inches from a Source2Viewer glb: Source (x, y, z) = glTF (z, x, y)."""
    data = path.read_bytes()
    length, = struct.unpack_from('<I', data, 12)
    doc = json.loads(data[20:20 + length])
    pts = []

    def walk(i, parent):
        node = doc['nodes'][i]
        m = _mat_mul(parent, _node_matrix(node))
        if node.get('mesh') is not None:
            for prim in doc['meshes'][node['mesh']]['primitives']:
                acc = doc['accessors'][prim['attributes']['POSITION']]
                for corner in [(a, b, c) for a in (0, 1) for b in (0, 1) for c in (0, 1)]:
                    v = [(acc['min'], acc['max'])[corner[k]][k] for k in range(3)] + [1]
                    pts.append([sum(m[r][k] * v[k] for k in range(4)) for r in range(3)])
        for child in node.get('children', []):
            walk(child, m)

    identity = [[float(i == j) for j in range(4)] for i in range(4)]
    for scene in doc.get('scenes', [{'nodes': range(len(doc.get('nodes', [])))}]):
        for i in scene.get('nodes', []):
            walk(i, identity)
    if not pts:
        raise AtlasError(f'no geometry in {path.name}')
    src = [[p[2] * M_TO_INCH, p[0] * M_TO_INCH, p[1] * M_TO_INCH] for p in pts]
    return ([round(min(p[i] for p in src), 2) for i in range(3)],
            [round(max(p[i] for p in src), 2) for i in range(3)])


def measure(project, paths: list[str]) -> dict:
    """Fill lo/hi for models that have no size yet (geometry-only export, ~50 ms each)."""
    models = index(project)
    todo = [p for p in paths if 'lo' not in models[p]]
    if not todo:
        return models
    with _scratch() as tmp:
        for p, glb in _vrf(project, todo, tmp, textures=False).items():
            try:
                models[p]['lo'], models[p]['hi'] = glb_bounds(glb)
            except (OSError, ValueError, KeyError, struct.error):
                models[p]['lo'] = models[p]['hi'] = None
    _save_index(project, models)
    return models


# -- notes: personal journal + shared catalog ------------------------------------------

def _read_jsonl(path: Path) -> list[dict]:
    out = []
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        return out
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and isinstance(rec.get('model'), str):
            out.append(rec)
    return out


def notes_all() -> dict:
    """Latest record per model: personal notes override the shared catalog."""
    out = {r['model']: {**r, 'origin': 'catalog'} for r in _read_jsonl(CATALOG)}
    for r in _read_jsonl(NOTES):
        out[r['model']] = {**r, 'origin': 'local'}
    return out


def validate_note(rec: dict) -> dict:
    fields = ('model', 'category', 'tags', 'style', 'front', 'mount', 'note', 'by', 'date', 'source', 'crc', 'rev')
    if set(rec) - set(fields) - {'origin'}:
        raise AtlasError(f'unknown note fields: {sorted(set(rec) - set(fields) - {"origin"})}')
    if rec.get('category') not in CATEGORIES:
        raise AtlasError(f'category must be one of {", ".join(CATEGORIES)}')
    tags = rec.get('tags')
    if not isinstance(tags, list) or not 3 <= len(tags) <= 6 or len(set(tags)) != len(tags) \
            or not all(isinstance(t, str) and _WORD.match(t) for t in tags):
        raise AtlasError('tags must be 3..6 distinct lowercase words (a-z, 0-9, _ or -, up to 20 chars)')
    if not isinstance(rec.get('style'), str) or not _WORD.match(rec['style']):
        raise AtlasError('style must be one lowercase word, e.g. inferno, italy, mirage, generic, modern')
    if rec.get('front') not in FRONTS:
        raise AtlasError('front must be +x, -x, +y, -y or none (round or symmetric models)')
    if rec.get('mount') not in MOUNTS:
        raise AtlasError(f'mount must be one of {", ".join(MOUNTS)}')
    note = rec.get('note', '')
    if not isinstance(note, str) or len(note) > 80 or '\n' in note:
        raise AtlasError('note must be one line of at most 80 characters')
    if not isinstance(rec.get('by'), str) or not 1 <= len(rec['by']) <= 40:
        raise AtlasError('by must name the AI model that looked at the preview (1..40 chars)')
    if rec.get('source') not in ('offline', 'game', 'kit'):
        raise AtlasError('source must be offline, game or kit')
    return rec


def note(project, model: str, category: str, tags: list, style: str, front: str, mount: str,
         note: str = '', by: str = '', source: str = 'offline') -> dict:
    models = index(project)
    path = _model(project, models, model)
    rec = validate_note({'model': path, 'category': category, 'tags': tags, 'style': style, 'front': front,
                         'mount': mount, 'note': note, 'by': by, 'source': source})
    if front != 'none' and source == 'offline':
        seen = IMAGES / f'{_key(path, models[path]["crc"], "detail")}.json'
        try:
            ok = json.loads(seen.read_text(encoding='utf-8')).get('status') == 'ok'
        except (OSError, ValueError):
            ok = False
        if not ok:
            raise AtlasError('a front is recorded only after both sides were seen: '
                             f'prop_preview(models=["{path}"], detail=true), then repeat prop_note')
    with _file_lock(NOTES.with_suffix('.lock')):
        previous = notes_all().get(path)
        rec.update(date=datetime.date.today().isoformat(), crc=models[path]['crc'],
                   rev=int(previous.get('rev', 0)) + 1 if previous else 1)
        NOTES.parent.mkdir(parents=True, exist_ok=True)
        with open(NOTES, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(',', ':')) + '\n')
    return {'ok': True, 'model': path, 'rev': rec['rev']}


# -- search ----------------------------------------------------------------------------

def _size(info) -> str:
    if not info.get('lo'):
        return '?'
    return 'x'.join(f'{b - a:.0f}' for a, b in zip(info['lo'], info['hi']))


def _line(path: str, info: dict, rec: dict | None) -> str:
    head = f'{path} | {_size(info)}'
    if not rec:
        return head + ' | not described'
    stale = ' (model changed since)' if rec.get('crc') != info['crc'] else ''
    text = f' — {rec["note"]}' if rec.get('note') else ''
    return (f'{head} | front {rec["front"]}, {rec["mount"]} | {rec["category"]}/{rec["style"]}: '
            f'{", ".join(rec["tags"])}{text}{stale}')


def search(project, query: str = '', limit: int = 12) -> dict:
    if not isinstance(query, str) or not query.strip():
        raise AtlasError('query must name what you look for, e.g. "bench", "inferno window", "palm"')
    if type(limit) is not int or not 1 <= limit <= 40:
        raise AtlasError('limit must be 1..40')
    models = index(project)
    notes = notes_all()
    words = [w for w in re.split(r'[\s,;]+', query.lower()) if w]
    ranked = []
    for path, info in models.items():
        rec = notes.get(path)
        name = path.rsplit('/', 1)[-1]
        noted = ' '.join([rec['category'], rec['style'], rec.get('note', '').lower(), *rec['tags']]) if rec else ''
        hit = score = 0
        for w in words:
            s = 3 if (w in name or w in noted) else 2 if w in path else 1 if any(w in m for m in info['mats']) else 0
            hit += s > 0
            score += s
        if hit:
            ranked.append((-hit, -score, rec is None, len(path), path))
    ranked.sort()
    chosen = [r[-1] for r in ranked[:limit]]
    models = measure(project, chosen)
    return {'query': query, 'found': len(ranked),
            'results': [_line(p, models[p], notes.get(p)) for p in chosen],
            'size': 'XxYxZ inches in model axes; 72 is player height',
            'next': 'prop_preview(models=[...]) to look; prop_note(...) to record what you saw'}


# -- previews ------------------------------------------------------------------------

def _evict() -> None:
    files = [p for p in IMAGES.glob('*.webp')]
    total = sum(p.stat().st_size for p in files)
    if total > IMAGE_LIMIT:
        for p in sorted(files, key=lambda p: p.stat().st_mtime):
            total -= p.stat().st_size
            p.unlink(missing_ok=True)
            p.with_suffix('.json').unlink(missing_ok=True)
            if total < IMAGE_LIMIT * 0.9:
                break
    sheets = sorted(SHEETS.glob('sheet-*.jpg'), key=lambda p: p.stat().st_mtime)
    for p in sheets[:-SHEETS_KEEP]:
        p.unlink(missing_ok=True)


def _font(size: int):
    from PIL import ImageFont
    for name in ('arial.ttf', 'segoeui.ttf', 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _magenta(image) -> float:
    """Share of pixels in Blender's missing-texture magenta."""
    raw = image.convert('RGB').resize((64, 64)).tobytes()
    return sum(raw[i] > 190 and raw[i + 2] > 190 and raw[i + 1] < 90 for i in range(0, len(raw), 3)) / (len(raw) // 3)


def _render(project, paths: list[str], keys: dict, detail: bool) -> dict:
    """Render missing previews into the cache. Returns {model: reason} for tool failures (not cached)."""
    from PIL import Image
    failed = {}
    models = index(project)
    views = ['front', 'back'] if detail else ['front']
    size = 384 if detail else 256
    with _scratch() as tmp:
        glbs = _vrf(project, paths, tmp / 'glb', textures=True)
        jobs, owners = [], []
        for p in paths:
            meta = {'model': p, 'crc': models[p]['crc'], 'status': 'broken'}
            if not glbs[p].is_file():
                failed[p] = 'export failed (Source2Viewer-CLI)'
                continue
            jobs.append({'glb': str(glbs[p]), 'out': str(tmp / keys[p]), 'views': views, 'size': size})
            owners.append(p)
        if not jobs:
            return failed
        (tmp / 'jobs.json').write_text(json.dumps(jobs), encoding='utf-8')
        argv = blender_run.argv(project.s, SCRIPT, [tmp / 'jobs.json'])
        env = {**os.environ, **blender_run.env()}
        try:
            proc = subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, timeout=40 + 6 * len(jobs),
                                  env=env, creationflags=_CREATE_NO_WINDOW)
            out = proc.stdout.decode('utf-8', 'replace')
        except subprocess.TimeoutExpired:
            out = ''
        results = blender_run.parse_json_line(out).get('results') or []
        if len(results) != len(owners):
            log = project.s.logs / f'atlas-{time.strftime("%Y%m%d-%H%M%S")}.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(out[-20000:], encoding='utf-8')
            failed.update({p: f'Blender failed; see logs/{log.name}' for p in owners})
            results = []
        for p, r in zip(owners, results):
            meta = {'model': p, 'crc': models[p]['crc'], 'status': 'ok' if r.get('ok') else 'broken'}
            if r.get('reason'):
                meta['reason'] = r['reason']
            if r.get('lo'):
                meta['lo'] = [round(x, 2) for x in r['lo']]
                meta['hi'] = [round(x, 2) for x in r['hi']]
                models[p]['lo'], models[p]['hi'] = meta['lo'], meta['hi']
            images = [r.get('images', {}).get(v) for v in views]
            if all(images) and all(Path(i).is_file() for i in images):
                tiles = [Image.open(i).convert('RGB') for i in images]
                if meta['status'] == 'ok' and _magenta(tiles[0]) > 0.01:
                    meta.update(status='broken', reason='missing textures (magenta)')
                tile = Image.new('RGB', (size * len(tiles), size))
                for n, t in enumerate(tiles):
                    tile.paste(t, (n * size, 0))
                tile.save(IMAGES / f'{keys[p]}.webp', quality=84, method=4)
            elif meta['status'] == 'ok':
                meta.update(status='broken', reason='no image rendered')
            _write_json(IMAGES / f'{keys[p]}.json', meta)
    _save_index(project, models)
    return failed


def _sheet(entries: list[dict], detail: bool) -> Path:
    from PIL import Image, ImageDraw
    cols = 2 if detail else 4
    tw = 768 if detail else 256
    th = 384 if detail else 256
    label = 34
    rows = (len(entries) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * tw, rows * (th + label)), (24, 26, 30))
    draw = ImageDraw.Draw(sheet)
    font, small = _font(15), _font(13)
    for i, e in enumerate(entries):
        x, y = (i % cols) * tw, (i // cols) * (th + label)
        img = IMAGES / f'{e["key"]}.webp'
        if img.is_file():
            with Image.open(img) as tile:
                sheet.paste(tile.convert('RGB'), (x, y + label))
        else:
            draw.text((x + 12, y + label + th // 2), 'no preview: ' + e.get('reason', ''), fill=(255, 120, 90), font=small)
        name = e['model'].rsplit('/', 1)[-1][:-5]
        draw.text((x + 6, y + 2), f'{e["n"]}. {name}'[:34], fill=(255, 255, 255), font=font)
        sub = e['size'] + ('' if e['status'] == 'ok' else '  ' + e['status'].upper())
        draw.text((x + 6, y + 18), sub, fill=(170, 200, 255) if e['status'] == 'ok' else (255, 120, 90), font=small)
    SHEETS.mkdir(parents=True, exist_ok=True)
    path = SHEETS / f'sheet-{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:4]}.jpg'
    sheet.save(path, quality=86)
    return path


def _cached(key: str) -> dict | None:
    """Cached preview meta; tool failures from older versions count as missing."""
    try:
        meta = json.loads((IMAGES / f'{key}.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if str(meta.get('reason', '')).startswith(('Blender failed', 'export failed')):
        return None
    return meta


def _key(path: str, crc: str, mode: str) -> str:
    return hashlib.sha1(f'{path}|{crc}|{RENDER_VERSION}|{mode}'.encode()).hexdigest()[:20]


def preview(project, models: list, detail: bool = False, game: bool = False) -> dict:
    limit = 6 if detail else 16
    if not isinstance(models, list) or not 1 <= len(models) <= limit:
        raise AtlasError(f'models must be a list of 1..{limit} stock model paths')
    facts = index(project)
    paths = [_model(project, facts, m) for m in models]
    if len(set(paths)) != len(paths):
        raise AtlasError('models must be distinct')
    mode = 'detail' if detail else 'tile'
    keys = {p: _key(p, facts[p]['crc'], mode) for p in paths}
    IMAGES.mkdir(parents=True, exist_ok=True)
    todo = [p for p in paths if not _cached(keys[p])]
    failed = {}
    with _file_lock(CACHE / 'render.lock'):
        todo = [p for p in todo if not _cached(keys[p])]
        if todo:
            failed = _render(project, todo, keys, detail)
    # A cached preview may outlive the index entry's size (index rebuilt): measure what lacks it.
    facts = measure(project, [p for p in paths if p not in failed])
    entries = []
    now = time.time()
    for n, p in enumerate(paths, 1):
        if p in failed:
            entries.append({'n': n, 'model': p, 'key': keys[p], 'status': 'error', 'size': _size(facts[p]),
                            'reason': failed[p] + '; repeat the call'})
            continue
        meta = json.loads((IMAGES / f'{keys[p]}.json').read_text(encoding='utf-8'))
        for f in (IMAGES / f'{keys[p]}.webp', IMAGES / f'{keys[p]}.json'):
            if f.exists():
                os.utime(f, (now, now))
        entries.append({'n': n, 'model': p, 'key': keys[p], 'status': meta['status'],
                        'size': _size(facts[p]), **({'reason': meta['reason']} if meta.get('reason') else {})})
    if game:
        broken = [e['model'] for e in entries if e['status'] == 'broken']
        if not broken:
            raise AtlasError('game view is only for models whose offline preview is broken; these previews are fine')
        if len(broken) > 6:
            raise AtlasError('game view takes at most 6 broken models per call')
        from . import atlas_game
        return {**atlas_game.capture(project, broken), 'offline_ok': [e['model'] for e in entries if e['status'] == 'ok']}
    sheet = _sheet(entries, detail)
    _evict()
    out = {'sheet': str(sheet), 'rendered': len(todo), 'cached': len(paths) - len(todo),
           'models': [{k: e[k] for k in ('n', 'model', 'size', 'status', 'reason') if k in e} for e in entries],
           'legend': 'red arrow = model +X (yaw 0); blue box = player 32x32x72 (hidden for parts under 24 units); ' +
                     ('left view from +X/-Y, right view from -X/+Y' if detail else 'view from +X/-Y'),
           'next': 'open the sheet; record each model you judged with prop_note'}
    if any(e['status'] == 'broken' for e in entries):
        out['broken_hint'] = 'only broken models may be viewed in the game: prop_preview(models=[...], game=true)'
    if failed:
        out['error_hint'] = 'a tool failed (not cached); repeat the identical call'
    return out
