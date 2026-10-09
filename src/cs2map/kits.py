"""Stock parts, measured placement and resumable CS2 contact sheets.

Schema 1 is shared with buildings/terrain (docs/specs/kits.md). No catalogue
entry becomes game-verified merely because a screenshot was captured.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import itertools
import json
import math
import os
from pathlib import Path, PurePosixPath
import random
import re
import subprocess
import time

from . import checks, game, resource, structure, vconsole, visual
from .config import PROJECT_ROOT, ensure_inside
from .project import OpError

KITS = Path(__file__).parent / 'data' / 'kits'
CATEGORIES = {'window', 'door', 'shutter', 'balcony', 'awning', 'roof', 'chimney',
              'gutter', 'trim', 'sign', 'lamp', 'street', 'vehicle', 'tree',
              'bush', 'plant', 'rock', 'fence', 'prop'}
MOUNTS = {'wall', 'floor', 'roof', 'ceiling', 'hang'}
FRONTS = {'+x': (1, 0, 0), '-x': (-1, 0, 0), '+y': (0, 1, 0), '-y': (0, -1, 0)}
SCRATCH = PROJECT_ROOT / '.local' / 'scratch' / 'A'


def _num(v, name, low=-32768, high=32768):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not low <= v <= high:
        raise OpError(f'{name} must be a finite number in {low}..{high}')
    return float(v)


def _vec(v, name, n=3):
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise OpError(f'{name} must contain {n} numbers')
    return [_num(x, name) for x in v]


def _name(v, name):
    if not isinstance(v, str) or not re.fullmatch(r'[a-z0-9_]{1,64}', v):
        raise OpError(f'{name} must contain 1..64 lowercase letters, digits or underscores')
    return v


def _strings(v, name):
    if not isinstance(v, list) or any(not isinstance(x, str) or not x.strip() for x in v) or len(set(v)) != len(v):
        raise OpError(f'{name} must be a list of distinct nonempty strings')
    return v


def _model(project, value):
    if not isinstance(value, str) or '\\' in value:
        raise OpError('model must be a models/.../*.vmdl path with forward slashes')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or ':' in value or not value.startswith('models/') or path.suffix != '.vmdl':
        raise OpError('model must be a safe models/.../*.vmdl path')
    stock = project.game.exists(value + '_c')
    if not stock and not (value.startswith('models/mcp/') and
                          ensure_inside(project.s.game_addon / (value + '_c'), project.s.game_addon).is_file()):
        raise OpError(f'compiled stock/owned model does not exist: {value}')
    return value


def validate(project, data, filename=None):
    """Validate every schema field and compiled model path, without mutating it."""
    if not isinstance(data, dict) or type(data.get('schema')) is not int or data['schema'] != 1:
        raise OpError('kit must have schema:1')
    name = _name(data.get('kit'), 'kit')
    if filename is not None and name != Path(filename).stem:
        raise OpError(f'kit {name} does not match its filename')
    if not isinstance(data.get('title'), str) or not data['title'].strip():
        raise OpError('kit needs a nonempty title')
    _strings(data.get('style'), 'style')
    parts = data.get('parts')
    if not isinstance(parts, list) or not parts:
        raise OpError('kit needs a nonempty parts list')
    seen = set()
    for p in parts:
        if not isinstance(p, dict):
            raise OpError('each part must be an object')
        key = _name(p.get('key'), 'part key')
        if key in seen:
            raise OpError(f'duplicate part {name}/{key}')
        seen.add(key)
        _model(project, p.get('model'))
        if p.get('category') not in CATEGORIES or p.get('mount') not in MOUNTS or p.get('front') not in FRONTS:
            raise OpError(f'{name}/{key}: invalid category, mount or front')
        b = p.get('bounds')
        if not isinstance(b, (list, tuple)) or len(b) != 2:
            raise OpError(f'{name}/{key}: bounds must be [mins,maxs]')
        lo, hi = _vec(b[0], 'bounds mins'), _vec(b[1], 'bounds maxs')
        size = _vec(p.get('size'), 'size')
        if any(b <= a or s <= 0 or abs(s - (b-a)) > .15 for a, b, s in zip(lo, hi, size)):
            raise OpError(f'{name}/{key}: positive size must agree with bounds within .15 units')
        _strings(p.get('tags'), 'tags')
        if type(p.get('solid')) is not bool or p.get('verified') not in {'bounds', 'game'}:
            raise OpError(f'{name}/{key}: solid must be boolean; verified must be bounds or game')
        if 'opening' in p:
            opening = _vec(p['opening'], 'opening', 2)
            if p['category'] not in {'window', 'door'} or min(opening) <= 0:
                raise OpError('opening is a positive [width,height] for windows/doors')
        if 'opening_bottom' in p:
            if 'opening' not in p:
                raise OpError('opening_bottom requires opening')
            _num(p['opening_bottom'], 'opening_bottom')
        thumb = p.get('thumb')
        if thumb not in (None, ''):
            if not isinstance(thumb, str) or not thumb.startswith('screenshots/kits/') or not thumb.endswith('.png') or '\\' in thumb:
                raise OpError('thumb must be screenshots/kits/.../*.png')
            ensure_inside(PROJECT_ROOT / thumb, PROJECT_ROOT / 'screenshots' / 'kits')
        if p['verified'] == 'game' and (not thumb or not (PROJECT_ROOT / thumb).is_file()):
            raise OpError(f'{name}/{key}: game verification needs an existing game thumbnail')
    return data


_LOADED = {}


def load(project):
    """Read and validate all kits (cached until a kit file changes; treat the result as read-only)."""
    stamp = tuple((p.name, p.stat().st_mtime_ns) for p in sorted(KITS.glob('*.json')))
    if _LOADED.get('stamp') == stamp:
        return _LOADED['data']
    out = {}
    for path in sorted(KITS.glob('*.json')):
        try:
            data = validate(project, json.loads(path.read_text(encoding='utf-8')), path)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise OpError(f'kit {path.name}: {exc}') from None
        if data['kit'] in out:
            raise OpError(f'duplicate kit {data["kit"]}')
        out[data['kit']] = data
    _LOADED.update(stamp=stamp, data=out)
    return out


def _part(data, key):
    if not isinstance(key, str) or not re.fullmatch(r'[a-z0-9_]{1,64}/[a-z0-9_]{1,64}', key):
        raise OpError('part must be kit/key; use kit_list')
    name, local = key.split('/')
    if name in data:
        for p in data[name]['parts']:
            if p['key'] == local:
                return {**copy.deepcopy(p), 'kit': name, 'full_key': key, 'style': data[name]['style'][:]}
    raise OpError(f'unknown part {key}; use kit_list')


def part(project, key):
    return _part(load(project), key)


def find(project, category=None, mount=None, style=None, tags=None):
    if category is not None and category not in CATEGORIES:
        raise OpError('unknown category; use kit_list without filters')
    if mount is not None and mount not in MOUNTS:
        raise OpError('mount must be wall, floor, roof, ceiling or hang')
    if style is not None and (not isinstance(style, str) or not style):
        raise OpError('style must be a nonempty string')
    tags = _strings([] if tags is None else tags, 'tags')
    data = load(project)
    return [_part(data, f'{name}/{p["key"]}') for name, d in data.items() for p in d['parts']
            if (category is None or p['category'] == category) and (mount is None or p['mount'] == mount)
            and (style is None or style in d['style']) and set(tags).issubset(p['tags'])]


def kit_list(project, category=None, style=None, mount=None, tags=None):
    rows = find(project, category, mount, style, tags)
    return {'ok': True, 'count': len(rows), 'kits': sorted({p['kit'] for p in rows}),
            'parts': [{k: p[k] for k in ('full_key', 'category', 'mount', 'front', 'size', 'verified', 'thumb') if k in p}
                      for p in rows]}


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def _unit(v, name):
    v = _vec(v, name)
    length = math.sqrt(_dot(v, v))
    if length < 1e-7:
        raise OpError(name + ' must be nonzero')
    return [x/length for x in v]


def _angles(front, normal, up):
    """Rz Ry Rx rotation aligning the observed local front and local +Z."""
    n = _unit(normal, 'facing')
    u = _unit(up, 'up')
    dot = _dot(n, u)
    z = [u[i] - dot*n[i] for i in range(3)]
    z = _unit(z, 'up perpendicular to facing')
    side = structure._cross(z, n)
    f = FRONTS[front]
    # columns are images of local +X/+Y/+Z
    x = [f[0]*n[i] - f[1]*side[i] for i in range(3)]
    y = [f[1]*n[i] + f[0]*side[i] for i in range(3)]
    pitch = math.asin(max(-1, min(1, -x[2])))
    if abs(math.cos(pitch)) > 1e-7:
        yaw, roll = math.atan2(x[1], x[0]), math.atan2(y[2], z[2])
    else:
        yaw, roll = math.atan2(-y[0], y[1]), 0
    return [math.degrees(a) for a in (pitch, yaw, roll)]


def _corners(bounds, angles, scale):
    return [structure._rotate(tuple(x*scale for x in v), angles) for v in itertools.product(*zip(*bounds))]


def _floor(m, at):
    """Nearest world-solid top below a probe, excluding triggers and entities."""
    probe = [at[0], at[1], at[2] + 2]
    hits = []
    for node in m.iter_nodes():
        if node.type != 'CMapMesh' or node.get('physicsType') == 'none' or not checks._is_solid_mesh(node):
            continue
        parent = m.parent_of(node)
        while parent is not None and parent.type != 'CMapEntity':
            parent = m.parent_of(parent)
        if parent is not None:
            continue
        for tri in checks._triangles(node):
            n = checks._cross(checks._sub(tri[1], tri[0]), checks._sub(tri[2], tri[0]))
            length = math.sqrt(_dot(n, n)) or 1
            if n[2]/length < .5:
                continue
            distance = checks._ray(probe, (0, 0, -1), tri)
            if distance is not None and distance <= 4096:
                hits.append(probe[2]-distance)
    if not hits:
        raise OpError('no solid world floor below at (4096-unit ray); add a floor or use snap_floor:false')
    return max(hits)


def place(project, m, key, at, normal=None, up=(0, 0, 1), offset=0,
          scale=1, yaw_jitter=0, seed=0, snap_floor=True, wall_anchor=False):
    """Place a part facing normal; wall back touches at, floor base hits a ray.

    Floor ``at`` denotes base centre. With a wall coordinate its back touches
    the wall and its base is separately snapped to the floor.
    """
    p = key if isinstance(key, dict) else part(project, key)   # a dict: a measured stock model (atlas)
    key = p['full_key']
    if isinstance(at, dict):
        from .layout import point
        spec = {**at}
        if 'wall' in spec:
            spec.setdefault('offset', 0)
        at, inferred = point(m, spec)
        wall_anchor = inferred is not None
        if normal is None:
            normal = inferred
    at = _vec(at, 'at')
    normal = normal if normal is not None else (1, 0, 0)
    n = _unit(normal, 'facing')
    up = _unit(up, 'up')
    scale = _num(scale, 'scale', .001, 100)
    offset = _num(offset, 'offset', -1024, 1024)
    jitter = _num(yaw_jitter, 'yaw_jitter', 0, 180)
    if type(seed) is not int or not -2147483648 <= seed <= 2147483647:
        raise OpError('seed must be a 32-bit integer')
    if type(snap_floor) is not bool or type(wall_anchor) is not bool:
        raise OpError('snap_floor and wall_anchor must be boolean')
    if jitter:
        if p['mount'] not in {'floor', 'roof'} or wall_anchor:
            raise OpError('yaw_jitter is only supported for free floor/roof parts')
        n = structure._rotate(n, (0, random.Random(seed).uniform(-jitter, jitter), 0))
    angles = _angles(p['front'], n, up)
    pts = _corners(p['bounds'], angles, scale)
    centre = [(min(v[i] for v in pts) + max(v[i] for v in pts))/2 for i in range(3)]
    origin = [at[i] - centre[i] for i in range(3)]
    mount = p['mount']
    if mount == 'wall' or wall_anchor:
        rear = min(_dot(v, n) for v in pts)
        shift = _dot(centre, n) - rear + offset
        origin = [origin[i] + n[i]*shift for i in range(3)]
        # Wall heights refer to model origin, matching opening_bottom metadata.
        origin[2] = at[2] if abs(n[2]) < 1e-6 else origin[2]
    if mount in {'floor', 'roof'}:
        bottom = min(v[2] for v in pts)
        floor_z = _floor(m, [origin[0]+centre[0], origin[1]+centre[1], at[2]]) if snap_floor else at[2]
        origin[2] = floor_z - bottom + (0 if wall_anchor else offset)
    elif mount == 'ceiling':
        origin[2] = at[2] - max(v[2] for v in pts) - offset
    elif mount == 'hang':
        origin = [at[i]+n[i]*offset for i in range(3)]
    classname = 'prop_static'
    kv = {'model': p['model'], 'solid': 6 if p['solid'] else 0}
    node = m.new_entity(classname, kv, origin, angles, [scale]*3, defaults=project.catalog.defaults(classname))
    node.set('cs2map_kit_part', 'string', key)
    m.attach(node)
    return node


def preprocess(project, m, op, refs):
    """Preserve inferred wall facing before layout's recursive point resolver."""
    if op.get('op') != 'add_kit_part' or not isinstance(op.get('at'), dict):
        return op
    from .layout import point
    spec = {**op['at']}
    if 'wall' in spec:
        spec.setdefault('offset', 0)
    pos, normal = point(m, spec)
    out = {**op, 'at': pos}
    if normal is not None:
        out['_kit_wall_anchor'] = True
        if 'facing' not in out:
            out['facing'] = normal
    return out


def add_kit_part(project, m, op, refs):
    extra = set(op) - {'op', 'part', 'at', 'facing', 'up', 'offset', 'scale', 'yaw_jitter', 'seed', 'snap_floor', 'ref', '_kit_wall_anchor'}
    if extra:
        raise OpError('unknown add_kit_part fields: ' + ', '.join(sorted(extra)))
    node = place(project, m, op.get('part'), op.get('at'), op.get('facing'), op.get('up', [0, 0, 1]),
                 op.get('offset', 0), op.get('scale', 1), op.get('yaw_jitter', 0), op.get('seed', 0),
                 op.get('snap_floor', True), op.get('_kit_wall_anchor', False))
    if op.get('ref'):
        refs[op['ref']] = node['nodeID']
    return {'op': 'add_kit_part', 'id': node['nodeID'], 'part': op['part'],
            'origin': [round(x, 4) for x in node['origin']], 'angles': [round(x, 4) for x in node['angles']],
            'scale': node['scales'][0], **({'ref': op['ref']} if op.get('ref') else {})}


def measure(project, model):
    """Read native render bounds and PHYS into A's cache; no guessing."""
    model = _model(project, model)
    own = project.s.game_addon / (model + '_c')
    data = own.read_bytes() if own.is_file() else project.game.read(model + '_c')
    blocks = resource.blocks(data)
    path = ensure_inside(SCRATCH / 'models' / (hashlib.sha256(data).hexdigest()[:24] + '.vmdl_c'), SCRATCH)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)
    def dump(block):
        proc = subprocess.run([str(project.s.game_bin / 'resourceinfo.exe'), '-game', str(project.s.cs2_path / 'game/csgo'),
                               '-i', str(path), '-b', block, '-baremode'], capture_output=True, timeout=20)
        if proc.returncode:
            raise OpError(f'resourceinfo failed on {model} {block}')
        return proc.stdout.decode('utf-8-sig', errors='replace')
    points = []
    for block in ('MDAT', 'DATA'):
        if block in blocks:
            points = visual._resourceinfo_bounds(dump(block), block)
            if points and any(abs(x) > 1e-5 for p in points for x in p):
                break
    if not points or not any(abs(x) > 1e-5 for p in points for x in p):
        raise OpError(f'no native render bounds for {model}')
    lo = [round(min(p[i] for p in points)-.05, 4) for i in range(3)]
    hi = [round(max(p[i] for p in points)+.05, 4) for i in range(3)]
    solid = False
    if 'PHYS' in blocks:
        from .kv3 import loads
        _, physics = loads(re.sub(r'#\[\s*(?:[0-9A-Fa-f]{2}\s*)*\]', '[]', dump('PHYS')))
        solid = any(any(part.get('m_rnShape', {}).get(k) for k in ('m_spheres', 'm_capsules', 'm_hulls', 'm_meshes'))
                    for part in physics.get('m_parts', []))
    if not solid:
        solid = any(x.endswith('.vphys') and ((project.s.game_addon / (x+'_c')).is_file() or project.game.exists(x+'_c'))
                    for x in resource.external_references(data))
    return {'bounds': [lo, hi], 'size': [round(b-a, 4) for a, b in zip(lo, hi)], 'solid': bool(solid)}


@contextmanager
def _gallery_lock():
    """A's map is shared across callers; keep selection/build/capture serialized."""
    import msvcrt
    SCRATCH.mkdir(parents=True, exist_ok=True)
    with open(SCRATCH / 'gallery.lock', 'a+b') as fh:
        fh.seek(0)
        if not fh.read(1):
            fh.write(b'0'); fh.flush()
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise OpError('kit_gallery is busy in another process; repeat this call later') from None
        try:
            yield
        finally:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def _build_gallery(project, deadline):
    from . import game
    # Releasing a loaded VPK may load mcp_idle. Bound that lock wait too.
    with game.exclusive(wait=min(30,max(1,deadline-time.monotonic()-20))):
        game.command(project.s,'mp_freezetime 0',wait=.1)
        return project.map_build('a_gallery','full')


def _gallery_scene(project, parts):
    """Full-scale parts on a wide grid, and individual oblique-front views."""
    from .vmap import VMap
    from . import lighting, mesh
    m = VMap.from_template(project.s.cs2_path, 'basic')
    maximum = max(max(p['size']) for p in parts)
    cell = max(320, maximum*2.2 + 96)
    cols = min(4, len(parts)); rows = math.ceil(len(parts)/cols)
    width, depth = cols*cell, rows*cell
    ground = mesh.box([-cell/2, -cell/2, -16], [width-cell/2, depth-cell/2, 0],
                      'materials/concrete/concrete_floor_04.vmat')
    m.attach(m.new_mesh(ground, texture_size=project.texture_size))
    for cls, origin in [('info_player_terrorist', [-cell*.35, -cell*.35, 8]),
                        ('info_player_counterterrorist', [-cell*.2, -cell*.35, 8])]:
        m.attach(m.new_entity(cls, {}, origin, defaults=project.catalog.defaults(cls)))
    lighting.apply_preset(project, m, 'overcast')
    # Bake resolution box and dynamic reflection volume, both stock FGD.
    project._apply(m, {'op': 'add_mesh', 'shape': {'shape':'box', 'mins':[-cell/2,-cell/2,-24],
                    'maxs':[width-cell/2,depth-cell/2,maximum+300]},
                    'material':'materials/tools/toolslightmapres.vmat'}, {})
    project._apply(m, {'op':'add_entity','classname':'env_combined_light_probe_volume',
                    'origin':[(width-cell)/2,(depth-cell)/2,maximum/2],
                    'keyvalues':{'box_mins':f'{-width/2} {-depth/2} {-maximum/2-32}',
                                 'box_maxs':f'{width/2} {depth/2} {maximum/2+128}'}}, {})
    views = []
    for index, p in enumerate(parts):
        at = [(index%cols)*cell, (index//cols)*cell, 0]
        if p['mount'] == 'wall':
            at[2] = max(64, -p['bounds'][0][2]+16)
            node = place(project, m, p, at, (0,-1,0))
            # Visible neutral backing shows the mounting plane and alpha cards.
            transformed = visual._transformed(p['bounds'], node['origin'], node['angles'])
            low, high = transformed
            wall = mesh.box([low[0]-12, at[1], max(0,low[2]-12)],
                            [high[0]+12,at[1]+8,high[2]+12], 'materials/concrete/hr_c/hr_concrete_wall_004.vmat')
            m.attach(m.new_mesh(wall, texture_size=project.texture_size))
        else:
            node = place(project, m, p, at, (0,-1,0), snap_floor=False)
        bounds = visual._transformed(p['bounds'], node['origin'], node['angles'])
        centre = [(a+b)/2 for a,b in zip(*bounds)]
        extent = max(b-a for a,b in zip(*bounds))
        distance = max(30, extent*1.65)
        eye = [centre[0]+distance*.22, centre[1]-distance, centre[2]+distance*.68]
        direction = [centre[i]-eye[i] for i in range(3)]
        ang = [math.degrees(math.atan2(-direction[2],math.hypot(*direction[:2]))),
               math.degrees(math.atan2(direction[1],direction[0])),0]
        views.append({'name':f'a_kit_{p["kit"]}_{p["key"]}', 'pos':[eye[0],eye[1],eye[2]-64], 'ang':ang})
        structure.add_world_text(project, m, {'text':f'{index+1:02d} {p["key"]}',
                    'origin':[centre[0],bounds[0][1]-10,bounds[1][2]+14], 'angles':[0,0,90],
                    'size':min(18,max(8,extent/14)), 'color':'#eff6ff'}, {})
    eye = [(width-cell)/2, -depth*.8-cell, maximum+depth*.75]
    target = [(width-cell)/2,(depth-cell)/2, maximum*.25]
    d = [target[i]-eye[i] for i in range(3)]
    overview = {'name':'a_kit_grid','pos':[eye[0],eye[1],eye[2]-64],
                'ang':[math.degrees(math.atan2(-d[2],math.hypot(*d[:2]))),90,0]}
    project.save(m, 'a_gallery')
    return [overview, *views]


def gallery(project, kit=None, category=None, keys=None, offset=0, limit=12):
    """Prepare/full-build/capture; repeat identical args while pending.

    Each game batch has <=6 views. A 105-second budget yields build/capture
    progress instead of killing a long-running MCP call. Thumbnails remain
    captured-but-unreviewed until an agent actually opens and assesses them.
    """
    if sum(x is not None for x in (kit, category, keys)) != 1:
        raise OpError('supply exactly one of kit, category or keys')
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 12:
        raise OpError('offset must be >=0 and limit must be 1..12 integers')
    data = load(project)
    if kit is not None:
        if kit not in data:
            raise OpError('unknown kit; use kit_list')
        all_parts = [_part(data,f'{kit}/{p["key"]}') for p in data[kit]['parts']]
        label = kit
    elif category is not None:
        all_parts = find(project,category=category); label = category
    else:
        if not isinstance(keys,list) or not keys or len(keys)>200 or any(not isinstance(k,str) for k in keys) or len(set(keys)) != len(keys):
            raise OpError('keys must be 1..200 distinct kit/key strings')
        all_parts = [_part(data,k) for k in keys]; label = 'selection'
    requested = {'kit':kit,'category':category,'keys':keys,'offset':offset,'limit':limit}
    return gallery_parts(project, all_parts, label, requested, offset, limit)


def gallery_parts(project, all_parts, label, requested, offset=0, limit=12):
    """Build/capture a_gallery for part dicts (kit parts or measured stock models)."""
    selected = all_parts[offset:offset+limit]
    if not selected:
        raise OpError(f'offset {offset} is outside {len(all_parts)} matching parts')
    # Verification/thumb fields can change after review without changing a scene.
    geometry = [{k:v for k,v in p.items() if k not in {'verified','thumb'}} for p in selected]
    signature = hashlib.sha256(json.dumps([requested,geometry],sort_keys=True).encode()).hexdigest()
    root = ensure_inside(project.s.screenshots / 'kits', PROJECT_ROOT / 'screenshots' / 'kits')
    folder = ensure_inside(root / label, root); folder.mkdir(parents=True,exist_ok=True)
    state_path = ensure_inside(root / 'a_gallery_state.json',root)
    deadline = time.monotonic()+105
    with _gallery_lock(), project._lock:
        try:
            state = json.loads(state_path.read_text(encoding='utf-8'))
        except (FileNotFoundError,ValueError):
            state = {}
        if state.get('job'):
            try:
                project.jobs.get(state['job'])
            except KeyError:                       # its log was trimmed by the disk limits: build again
                state = {}
        if state.get('signature') != signature:
            # Never replace a VMAP while the previous compiler is reading it.
            if state.get('job') and project.jobs.get(state['job']).state == 'running':
                raise OpError('a_gallery is still compiling another selection; finish/retry that gallery first')
            views = _gallery_scene(project,selected)
            try:
                build = _build_gallery(project,deadline)
            except (game.GameError, vconsole.VConsoleError) as exc:
                return {'ok':False,'pending':True,'stage':'prepare_wait','reason':str(exc),
                        'repeat':{k:v for k,v in requested.items() if v is not None}}
            state = {'signature':signature,'requested':requested,'views':views,'shots':[], 'job':build['job'],
                     'parts':[p['full_key'] for p in selected]}
            _write_json(state_path,state)
        elif state.get('failed'):
            try:
                build = _build_gallery(project,deadline)
            except (game.GameError, vconsole.VConsoleError) as exc:
                return {'ok':False,'pending':True,'stage':'prepare_wait','reason':str(exc),
                        'repeat':{k:v for k,v in requested.items() if v is not None}}
            state.update(job=build['job'],failed=False,shots=[])
            _write_json(state_path,state)
        job = project.job_status(state['job'],wait_seconds=50,log_lines=0)
        base = {'ok':False,'map':'a_gallery','kit':label,'total':len(all_parts),'offset':offset,'count':len(selected),
                'job':state['job'],'log':job['log'],'repeat':{k:v for k,v in requested.items() if v is not None}}
        if job['state'] == 'running':
            return {**base,'pending':True,'stage':'build','captured':len(state['shots'])}
        if job['state'] != 'succeeded' or not job['result'].get('ok') or job['exit_code'] != 0 or job['result'].get('errors'):
            state['failed'] = True
            _write_json(state_path,state)
            return {**base,'pending':False,'stage':'build_failed','compile':job['result']}
        while len(state['shots']) < len(state['views']) and time.monotonic() < deadline-25:
            start = len(state['shots']); batch = state['views'][start:start+6]
            try:
                with game.exclusive(wait=min(40,max(1,deadline-time.monotonic()-20))):
                    # User-requested optimisation before ensure_map, under the
                    # same lock as capture. Preserve each completed frame.
                    game.command(project.s, 'mp_freezetime 0\nmp_warmup_pausetimer 0\nmp_warmup_end', wait=.2)
                    for v in batch:
                        if time.monotonic() > deadline-15:
                            break
                        view = {**v, 'cmd': 'mp_freezetime 0', 'wait': .2}
                        try:
                            shots = project.game_screenshot(map='a_gallery',views=[view],deadline=deadline)
                        except (game.GameError, vconsole.VConsoleError) as first:
                            # Tiny props can leave the same sampled background as
                            # the preceding tile. Keep frame_check strict, but
                            # give the camera one small, visible alternate before
                            # reporting capture_wait to the caller.
                            if 'frame did not change' not in str(first):
                                raise
                            alt = dict(view)
                            pos = list(alt['pos'])
                            pos[0] += 8.0; pos[1] += 5.0; pos[2] += 2.0
                            alt['pos'] = pos
                            alt['ang'] = [float(alt['ang'][0]), float(alt['ang'][1]) + 3.0,
                                          float(alt['ang'][2])]
                            shots = project.game_screenshot(map='a_gallery',views=[alt],deadline=deadline)
                        state['shots'].extend(shots['shots']); _write_json(state_path,state)
            except (game.GameError, vconsole.VConsoleError) as exc:
                return {**base,'pending':True,'stage':'capture_wait','captured':len(state['shots']),
                        'reason':str(exc)[:700]}
        if len(state['shots']) < len(state['views']):
            return {**base,'pending':True,'stage':'capture','captured':len(state['shots']), 'views':len(state['views'])}
        from PIL import Image, ImageDraw, ImageFont
        try:
            font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf',18)
        except OSError:
            font = ImageFont.load_default()
        thumbs = []; cols = min(4,len(selected)); cell_w, cell_h = 400, 290
        sheet = Image.new('RGB',(cols*cell_w,math.ceil(len(selected)/cols)*cell_h+44),'#17212b')
        draw = ImageDraw.Draw(sheet); draw.text((12,10),f'{label} | {offset+1}..{offset+len(selected)} | CS2 full',font=font,fill='white')
        for i, (p, shot) in enumerate(zip(selected,state['shots'][1:])):
            thumb_dir = ensure_inside(root / p['kit'],root); thumb_dir.mkdir(parents=True,exist_ok=True)
            thumb = thumb_dir / (p['key']+'.png')
            with Image.open(shot['path']) as source:
                # A centre square retains the full model at our measured framing.
                im = source.convert('RGB'); side = min(im.size)
                im = im.crop(((im.width-side)//2,(im.height-side)//2,(im.width+side)//2,(im.height+side)//2))
                im.thumbnail((512,512)); im.save(thumb)
                tile = im.copy(); tile.thumbnail((cell_w,cell_h-55))
            x=(i%cols)*cell_w; y=44+(i//cols)*cell_h
            sheet.paste(tile,(x+(cell_w-tile.width)//2,y))
            draw.text((x+8,y+cell_h-50),p['full_key'],font=font,fill='white')
            draw.text((x+8,y+cell_h-26),f'{p["front"]} | '+ ' x '.join(f'{x:g}' for x in p['size']),font=font,fill='#afc9db')
            thumbs.append({'part':p['full_key'],'thumb':str(thumb),'frame':shot['path']})
        path = folder / f'sheet_{offset:03d}.png'; sheet.save(path)
        manifest = folder / f'sheet_{offset:03d}.json'
        _write_json(manifest,{'requested':requested,'compile':job,'parts':thumbs,'shots':state['shots']})
        return {**base,'ok':True,'pending':False,'sheet':str(path),'overview':state['shots'][0]['path'],
                'thumbs':thumbs,'manifest':str(manifest),'compile':{'ok':True,'exit_code':0},
                'review':'Open the sheet and frames; captured images do not change verified flags.'}
