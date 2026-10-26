"""UV projection helpers for existing CMapMesh elements.

The map format stores one texture axis/scale tuple per polygon and one UV per
half-edge corner.  ``set_uv`` edits only those fields; topology and positions
remain untouched.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .dmx import Element
from .mesh import world_axes
from .project import OpError


def _dot(a, b):
    return sum(float(x) * float(y) for x, y in zip(a, b))


def _norm(a):
    n = math.sqrt(_dot(a, a))
    if n < 1e-9:
        raise OpError('UV axis has zero length')
    return tuple(float(x) / n for x in a)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _face_loops(md: Element):
    edges = md['edgeVertexIndices']
    nxt = md['edgeNextIndices']
    loops = []
    for last in md['faceEdgeIndices']:
        loop = []
        h = int(last)
        while True:
            loop.append(h)
            h = int(nxt[h])
            if h == int(last):
                break
            if len(loop) > len(edges):
                raise OpError('mesh face loop is corrupt')
        loops.append(loop)
    return loops


def _normal(points):
    n = [0.0, 0.0, 0.0]
    for a, b in zip(points, points[1:] + points[:1]):
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    return _norm(n)


STOCK_SIZES = Path(__file__).parent / 'data' / 'material_sizes.json'


def _material_real_size(project, material: str):
    try:
        stock = json.loads(STOCK_SIZES.read_text(encoding='utf-8'))['sizes'].get(material)
        if stock:
            return float(stock[0]), float(stock[1])
    except (OSError, ValueError, KeyError):
        pass
    try:
        p = project.s.content_addon / material
        meta = p.with_suffix('.json')
        if meta.exists():
            value = json.loads(meta.read_text(encoding='utf-8')).get('real_size')
            if isinstance(value, list) and len(value) == 2 and all(float(v) > 0 for v in value):
                return float(value[0]), float(value[1])
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return None


def units_per_texel(project, material: str):
    """(u, v) world units per texel that show the material at its real size, or None when it is unknown."""
    real = _material_real_size(project, material)
    if not real:
        return None
    tw, th = project.texture_size(material)
    return real[0] / max(1, tw), real[1] / max(1, th)


def wall_axes(n):
    """u along the face's level edge (left to right seen from outside), v straight down the face; None for a
    floor or ceiling."""
    u = _cross((0.0, 0.0, 1.0), n)
    if math.sqrt(_dot(u, u)) < 0.1:
        return None
    u = _norm(u)
    return u, _norm(_cross(u, n))


def _selected_faces(points, faces: str | list[int], normal_by_face):
    count = len(points)
    if faces == 'all' or faces is None:
        return set(range(count))
    if isinstance(faces, str):
        if faces == 'top':
            return {i for i, n in enumerate(normal_by_face) if n[2] > 0.707}
        if faces == 'bottom':
            return {i for i, n in enumerate(normal_by_face) if n[2] < -0.707}
        if faces == 'sides':
            return {i for i, n in enumerate(normal_by_face) if abs(n[2]) <= 0.707}
        raise OpError('faces must be all, top, bottom, sides or a list of indices')
    if not isinstance(faces, list) or any(not isinstance(i, int) or i < 0 or i >= count for i in faces):
        raise OpError('faces list contains an invalid face index')
    return set(faces)


def apply_set_uv(project, node: Element, op: dict[str, Any]) -> dict:
    """Apply one set_uv operation to a CMapMesh and return a compact result."""
    if node.type != 'CMapMesh':
        raise OpError('set_uv target must be a mesh or an entity containing meshes')
    md = node.get('meshData')
    if not isinstance(md, Element):
        raise OpError('mesh has no meshData')
    loops = _face_loops(md)
    positions = md['vertexData']['streams'][0]['data']
    origin = tuple(node.get('origin', (0, 0, 0)))
    world = [tuple(float(p[i]) + origin[i] for i in range(3)) for p in positions]
    points_by_face = [[world[int(md['edgeVertexIndices'][h])] for h in loop] for loop in loops]
    normals = [_normal(p) for p in points_by_face]
    selected = _selected_faces(points_by_face, op.get('faces', 'all'), normals)
    mode = op.get('mode', 'world')
    if mode not in ('world', 'fit', 'wall'):
        raise OpError('mode must be world, fit or wall')
    rotate = float(op.get('rotate', 0.0))
    offset = op.get('offset', [0.0, 0.0])
    if len(offset) != 2:
        raise OpError('offset must be [u, v] in texels')
    offset = (float(offset[0]), float(offset[1]))
    scale_arg = op.get('scale', 'auto')
    if scale_arg != 'auto':
        if not isinstance(scale_arg, (list, tuple)) or len(scale_arg) != 2 or any(float(v) <= 0 for v in scale_arg):
            raise OpError('scale must be "auto" or [world_units_per_texel_u, world_units_per_texel_v]')
        explicit_scale = (float(scale_arg[0]), float(scale_arg[1]))
    else:
        explicit_scale = None
    fds = md['faceData']['streams']
    streams = {s['standardAttributeName']: s for s in fds}
    scale_data = streams['textureScale']['data']
    axis_u = streams['textureAxisU']['data']
    axis_v = streams['textureAxisV']['data']
    material_indices = streams['materialindex']['data']
    materials = md['materials']
    tex_stream = next(s for s in md['faceVertexData']['streams'] if s['standardAttributeName'] == 'texcoord')
    texcoords = tex_stream['data']
    changed = 0
    for fi in sorted(selected):
        n = normals[fi]
        u0, v0 = (wall_axes(n) if mode == 'wall' else None) or world_axes(n)
        c, s = math.cos(math.radians(rotate)), math.sin(math.radians(rotate))
        u = _norm(tuple(c * u0[i] + s * v0[i] for i in range(3)))
        v = _norm(tuple(-s * u0[i] + c * v0[i] for i in range(3)))
        material = materials[int(material_indices[fi])]
        tw, th = project.texture_size(material)
        if scale_arg == 'auto':
            real = units_per_texel(project, material)
            if real:
                scale = real
            elif mode == 'fit':
                scale = None
            else:
                scale = tuple(float(x) for x in scale_data[fi])
        else:
            scale = explicit_scale
        if mode == 'fit' or scale is None:
            us = [_dot(p, u) for p in points_by_face[fi]]
            vs = [_dot(p, v) for p in points_by_face[fi]]
            scale = (max((max(us) - min(us)) / max(1, tw), 1e-6),
                     max((max(vs) - min(vs)) / max(1, th), 1e-6))
            face_offset = (-min(us) / scale[0], -min(vs) / scale[1])
        else:
            face_offset = offset
        scale_data[fi] = tuple(scale)
        axis_u[fi] = (*u, face_offset[0])
        axis_v[fi] = (*v, face_offset[1])
        for h in loops[fi]:
            p = world[int(md['edgeVertexIndices'][h])]
            texcoords[h] = ((_dot(p, u) / scale[0] + face_offset[0]) / max(1, tw),
                            (_dot(p, v) / scale[1] + face_offset[1]) / max(1, th))
        changed += 1
    return {'op': 'set_uv', 'id': node['nodeID'], 'faces': changed, 'mode': mode}


def apply_to_target(project, vmap, target, op):
    meshes = [n for n in [target, *vmap.iter_nodes(target)] if n.type == 'CMapMesh']
    if not meshes:
        raise OpError('set_uv target contains no meshes')
    out = [apply_set_uv(project, n, op) for n in meshes]
    return {'op': 'set_uv', 'id': target['nodeID'], 'mesh_ids': [n['nodeID'] for n in meshes],
            'faces': sum(x['faces'] for x in out), 'mode': op.get('mode', 'world')}

