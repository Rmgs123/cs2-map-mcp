"""Own weapon models on the skeleton of a stock CS2 weapon: held in the hands, seen by others, lying on the floor.

CS2 draws ONE model per weapon in first person, third person and on the ground (vmdl game data
`weapon_metadata.is_unified_model`); the hands' animation moves it by its bones `weapon` -> `weapon_offset`.
A model built on the same skeleton is held exactly like the stock gun (verified 07.10 on an AWP base: first
person, third person, dropped). So an own model is rigged here, not in Blender:

    the base's body mesh DMX (its skeleton, joint list, axis system) with our triangles in place of its mesh,
    every vertex on `weapon_offset`, placed so that our grip point sits on the base's right hand bone;
    the base's vmdl (attachments, game data, hitbox, clips, NmSkeleton) with our mesh, our hull and the muzzle
    attachment moved to our muzzle; the base's own clips (dropped pose, shoot, reload) copied next to it.

Everything of the base is read from the user's own CS2 install (Source2Viewer-CLI, cached in
.local/cache/weapon_bases, never in the repository). The map must also reference the model (a parked prop):
a model missing from the map's manifest shows ERROR and crashes the client when a weapon wears it.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import time
from pathlib import Path

from . import dmx, kv3
from .config import PROJECT_ROOT
from .project import OpError

CACHE = PROJECT_ROOT / '.local' / 'cache' / 'weapon_bases'
KEEP = 12                                 # cached bases (~2-6 MB each); the oldest are removed
VRF = PROJECT_ROOT / 'tools' / 'vrf-cli-20.0' / 'Source2Viewer-CLI.exe'
# Class names whose model file does not end with the class name.
ALIASES = {'glock': 'glock18', 'm4a1': 'm4a4', 'knife': 'knife_default_ct', 'knife_t': 'knife_default_t'}
HAND_BONES = ('ag1_hand_r', 'weapon_hand_r', 'hand_r')
CLIP_FIELDS = ('source_filename',)
_NO_WINDOW = 0x08000000


def _base_name(base: str) -> str:
    name = str(base or '').lower().removeprefix('weapon_')
    if not name or not name.replace('_', '').isalnum():
        raise OpError('weapon.base must be a stock weapon class such as "awp", "ak47", "deagle", "taser", "knife"')
    return name


def stock_models(project) -> dict[str, str]:
    """weapon base -> its stock model (source path), from the installed game's VPKs."""
    found = {}
    for _, v in project.game.vpks:
        for path in v.entries:
            if path.startswith('weapons/models/') and path.endswith('.vmdl_c') and '/weapon_' in path:
                stem = path.rsplit('/', 1)[1][len('weapon_'):-len('.vmdl_c')]
                if stem.endswith('_mag') or '_ag2' in stem or 'broken' in stem or stem == 'arms':
                    continue
                found[stem] = path[:-2]
    out = {}
    for stem, path in found.items():
        out[stem] = path                                     # rif_ak47, snip_awp, knife_default_ct, ...
        out.setdefault(stem.split('_', 1)[1] if '_' in stem and stem.split('_', 1)[0] in
                       ('rif', 'snip', 'pist', 'smg', 'shot', 'mach', 'eq') else stem, path)
    for alias, target in ALIASES.items():
        if target in out:
            out[alias] = out[target]
    return out


def model_of(project, base: str) -> str:
    name = _base_name(base)
    models = stock_models(project)
    if name not in models:
        guns = sorted(k for k in models if '_' not in k or k.startswith(('knife', 'usp', 'm4a1', 'mp5')))
        raise OpError(f'no stock model for weapon_{name}; bases: {", ".join(guns)}')
    return models[name]


# -- transforms ------------------------------------------------------------------------------------------------
def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def _rot(q, v):
    x, y, z, w = q
    t = (2 * (y * v[2] - z * v[1]), 2 * (z * v[0] - x * v[2]), 2 * (x * v[1] - y * v[0]))
    return (v[0] + w * t[0] + y * t[2] - z * t[1], v[1] + w * t[1] + z * t[0] - x * t[2],
            v[2] + w * t[2] + x * t[1] - y * t[0])


def _inv(q):
    return (-q[0], -q[1], -q[2], q[3])


def _euler_q(pitch, yaw, roll):
    """Source QAngle (degrees) -> quaternion (x, y, z, w)."""
    p, y, r = (math.radians(a) / 2 for a in (pitch, yaw, roll))
    sp, cp, sy, cy, sr, cr = math.sin(p), math.cos(p), math.sin(y), math.cos(y), math.sin(r), math.cos(r)
    return (sr * cp * cy - cr * sp * sy, cr * sp * cy + sr * cp * sy, cr * cp * sy - sr * sp * cy,
            cr * cp * cy + sr * sp * sy)


def _skeleton(model_el) -> dict[str, dict]:
    """Joint name -> {pos, q, parent} in model space, from a DmeModel."""
    out = {}

    def walk(el, ppos, pq, parent):
        for child in el.get('children') or []:
            if child is None or child.type != 'DmeJoint':
                continue
            t = child['transform']
            pos = tuple(float(x) for x in t['position'])
            q = tuple(float(x) for x in t['orientation'])
            wpos = tuple(a + b for a, b in zip(ppos, _rot(pq, pos)))
            wq = _qmul(pq, q)
            out[child.name] = {'pos': wpos, 'q': wq, 'parent': parent}
            walk(child, wpos, wq, child.name)
    walk(model_el, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), None)
    return out


# -- the base, from the installed game --------------------------------------------------------------------------
def _children(doc, cls):
    return [c for c in doc['rootNode']['children'] if c.get('_class') == cls]


def _prune(keep: Path):
    folders = sorted((p for p in CACHE.iterdir() if p.is_dir() and p != keep), key=lambda p: p.stat().st_mtime)
    for old in folders[:max(0, len(folders) - (KEEP - 1))]:
        shutil.rmtree(old, ignore_errors=True)


def base_info(project, base: str) -> dict:
    """Skeleton, hand, muzzle, size and files of a stock weapon (decompiled once into the cache)."""
    name = _base_name(base)
    model = model_of(project, name)
    vpk = next(v for _, v in project.game.vpks if model + '_c' in v.entries)
    stamp = int(Path(vpk.path).stat().st_mtime)
    folder = CACHE / f'{name}-{stamp}'
    info_file = folder / 'base.json'
    if info_file.is_file():
        folder.touch()
        return json.loads(info_file.read_text(encoding='utf-8'))
    if not VRF.is_file():
        raise OpError(f'Source2Viewer-CLI not found: {VRF}; run setup.ps1 (it installs it)')
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = CACHE / f'.{name}-{time.time_ns()}'
    tmp.mkdir()                     # an existing folder: with one file Source2Viewer-CLI treats -o as a file name
    try:
        argv = [str(VRF), '-i', str(vpk.path), '-d', '-f', model + '_c', '-o', str(tmp)]
        subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, timeout=120, creationflags=_NO_WINDOW)
        vmdl = tmp / model
        if not vmdl.is_file():
            raise OpError(f'could not read the stock model {model} (Source2Viewer-CLI)')
        _, doc = kv3.loads(vmdl.read_text(encoding='utf-8'))
        meshes = [m for lst in _children(doc, 'RenderMeshList') for m in lst.get('children', [])]
        body = next((m for m in meshes if 'legacy' in m.get('name', '')), meshes[0] if meshes else None)
        if body is None:
            raise OpError(f'stock model {model} has no render mesh')
        body_file = tmp / body['filename']
        skel = _skeleton(dmx.load(body_file).root['skeleton'])
        if 'weapon' not in skel:
            raise OpError(f'stock model {model} has no "weapon" bone; it cannot carry an own model')
        hand = next((skel[b]['pos'] for b in HAND_BONES if b in skel), skel['weapon']['pos'])
        muzzle = None
        for lst in _children(doc, 'AttachmentList'):
            for a in lst.get('children', []):
                if a.get('name') == 'muzzle_flash' and a.get('parent_bone') in skel:
                    bone = skel[a['parent_bone']]
                    muzzle = [a + b for a, b in zip(bone['pos'], _rot(bone['q'], a.get('relative_origin', [0, 0, 0])))]
        pos = dmx.load(body_file)
        verts = next(e for e in pos.elements() if e.type == 'DmeVertexData')['position$0']
        lo = [min(v[i] for v in verts) for i in range(3)]
        hi = [max(v[i] for v in verts) for i in range(3)]
        folder.mkdir(parents=True, exist_ok=True)
        keep = {body['filename']}
        for lst in _children(doc, 'AnimationList'):
            for clip in lst.get('children', []):
                if clip.get('source_filename'):
                    keep.add(clip['source_filename'])
        for rel in keep:
            src = tmp / rel
            if src.is_file():
                (folder / Path(rel).name).write_bytes(src.read_bytes())
        (folder / 'base.vmdl').write_text(vmdl.read_text(encoding='utf-8'), encoding='utf-8')
        info = {'base': name, 'model': model, 'body': Path(body['filename']).name, 'hand': [round(x, 4) for x in hand],
                'muzzle': [round(x, 4) for x in muzzle] if muzzle else None,
                'bounds': [[round(x, 3) for x in lo], [round(x, 3) for x in hi]],
                'bones': {k: {'pos': [round(x, 4) for x in v['pos']], 'q': [round(x, 6) for x in v['q']],
                              'parent': v['parent']} for k, v in skel.items()}}
        info_file.write_text(json.dumps(info, indent=1), encoding='utf-8')
        _prune(folder)
        return info
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def summary(info: dict) -> dict:
    """What a model author needs: where the hand and the muzzle are, how big the stock gun is."""
    lo, hi = info['bounds']
    return {'base': info['base'], 'hand': info['hand'], 'muzzle': info['muzzle'],
            'length': round(hi[0] - lo[0], 1), 'height': round(hi[2] - lo[2], 1), 'width': round(hi[1] - lo[1], 1),
            'front': '+X', 'up': '+Z'}


# -- rig ---------------------------------------------------------------------------------------------------------
def validate(weapon) -> dict:
    """model_build's `weapon` field: {base, grip?, muzzle?, scale?, magazine?}."""
    if isinstance(weapon, str):
        weapon = {'base': weapon}
    if not isinstance(weapon, dict) or set(weapon) - {'base', 'grip', 'muzzle', 'scale', 'magazine'}:
        raise OpError('weapon must be {base, grip:[x,y,z], muzzle:[x,y,z], scale, magazine}')
    out = {'base': _base_name(weapon.get('base')), 'scale': weapon.get('scale', 1), 'magazine': weapon.get('magazine', False)}
    if isinstance(out['scale'], bool) or not isinstance(out['scale'], (int, float)) or not .05 <= out['scale'] <= 20:
        raise OpError('weapon.scale must be .05..20 (the model is built in inches)')
    if not isinstance(out['magazine'], bool):
        raise OpError('weapon.magazine must be boolean (true keeps the stock magazine in reload clips)')
    for key in ('grip', 'muzzle'):
        if weapon.get(key) is not None:
            v = weapon[key]
            if not isinstance(v, (list, tuple)) or len(v) != 3 or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in v):
                raise OpError(f'weapon.{key} must be [x,y,z] in the model\'s inches')
            out[key] = [float(x) for x in v]
    return out


def rig(project, name: str, out_dir: Path, tris: dict, weapon: dict, remaps: list[dict], surface: str) -> tuple[Path, dict]:
    """Write <out_dir>/<name>.dmx + <name>.vmdl on the base's skeleton. Returns (vmdl, notes)."""
    info = base_info(project, weapon['base'])
    folder = next(CACHE.glob(f"{info['base']}-*"))
    grip = weapon.get('grip') or tris.get('markers', {}).get('grip')
    if grip is None:
        raise OpError('a weapon model needs its grip point: an Empty named "grip" in the script (where the right '
                      'hand holds it) or weapon.grip [x,y,z]')
    muzzle = weapon.get('muzzle') or tris.get('markers', {}).get('muzzle')
    s, hand = float(weapon['scale']), info['hand']
    off = [hand[i] - s * grip[i] for i in range(3)]
    place = lambda p: (p[0] * s + off[0], p[1] * s + off[1], p[2] * s + off[2])
    material = {r['from'][:-5]: r['to'] for r in remaps}

    doc = dmx.load(folder / info['body'])
    els = doc.elements()
    vd = next(e for e in els if e.type == 'DmeVertexData')
    mesh_el = next(e for e in els if e.type == 'DmeMesh')
    joints = [j.name for j in doc.root['skeleton']['jointList']]
    bone = 'weapon_offset' if 'weapon_offset' in joints else 'weapon'
    index = joints.index(bone)
    verts = tris['verts']
    for key in list(vd.attrs):
        if key not in ('vertexFormat', 'jointCount', 'flipVCoordinates'):
            del vd.attrs[key]
    vd['vertexFormat'] = ['position$0', 'texcoord$0', 'normal$0', 'blendindices$0', 'blendweights$0']
    vd['jointCount'] = 1
    ids = list(range(len(verts)))
    vd.set('position$0', 'vector3_array', [place(v) for v in verts])
    vd.set('position$0Indices', 'int_array', ids)
    vd.set('texcoord$0', 'vector2_array', [(v[6], v[7]) for v in verts])
    vd.set('texcoord$0Indices', 'int_array', ids)
    vd.set('normal$0', 'vector3_array', [(v[3], v[4], v[5]) for v in verts])
    vd.set('normal$0Indices', 'int_array', ids)
    vd.set('blendindices$0', 'int_array', [index] * len(verts))
    vd.set('blendweights$0', 'float_array', [1.0] * len(verts))
    sets = []
    for mi, token in enumerate(tris['materials']):
        faces = []
        for a, b, c, m in tris['tris']:
            if m == mi:
                faces += [a, b, c, -1]
        if not faces:
            continue
        mat = dmx.Element('DmeMaterial', 'material')
        mat.set('mtlName', 'string', material.get(token, 'materials/dev/dev_measuregeneric01.vmat'))
        fs = dmx.Element('DmeFaceSet', f'faces{mi}')
        fs.set('faces', 'int_array', faces)
        fs.set('material', 'element', mat)
        sets.append(fs)
    mesh_el['faceSets'] = sets
    mesh_file = out_dir / f'{name}.dmx'
    mesh_file.write_bytes(dmx.dumps(doc))

    _, vm = kv3.loads((folder / 'base.vmdl').read_text(encoding='utf-8'))
    rel = project.s.addon_relative(mesh_file)
    clips = out_dir / 'clips'
    clips.mkdir(exist_ok=True)
    for node in vm['rootNode']['children']:
        cls = node.get('_class')
        if cls == 'RenderMeshList':
            for m in node.get('children', []):
                m['filename'] = rel
        elif cls == 'PhysicsShapeList':
            for h in node.get('children', []):
                h['filename'] = rel
                h['surface_prop'] = surface if surface != 'default' else h.get('surface_prop', 'metal')
        elif cls == 'AnimationList':
            for clip in node.get('children', []):
                src = folder / Path(clip.get('source_filename', '')).name
                if src.is_file():
                    shutil.copy2(src, clips / src.name)
                    clip['source_filename'] = project.s.addon_relative(clips / src.name)
        elif cls == 'AttachmentList' and muzzle is not None:
            for a in node.get('children', []):
                if a.get('name') == 'muzzle_flash' and a.get('parent_bone') in info['bones']:
                    b = info['bones'][a['parent_bone']]
                    m = place(muzzle)
                    local = _rot(_inv(b['q']), [m[i] - b['pos'][i] for i in range(3)])
                    a['relative_origin'] = [round(x, 4) for x in local]
        elif cls == 'GameDataList' and not weapon['magazine']:
            for g in node.get('children', []):
                keys = g.get('game_keys') or {}
                if 'magazine_model' in keys:
                    keys['magazine_model'] = ''
    vmdl = out_dir / f'{name}.vmdl'
    vmdl.write_text(kv3.dumps(vm, header=kv3.HEADER_MODELDOC), encoding='utf-8')
    placed = [place(v) for v in verts]
    lo = [min(p[i] for p in placed) for i in range(3)]
    hi = [max(p[i] for p in placed) for i in range(3)]
    notes = {'base': info['base'], 'scale': s, 'grip': grip, 'muzzle': muzzle, 'stock': summary(info),
             'size': [round(hi[i] - lo[i], 1) for i in range(3)]}
    return vmdl, notes
