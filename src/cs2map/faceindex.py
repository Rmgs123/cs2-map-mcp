"""The faces of a map as arrays: one fast extraction shared by the offline preview, region builds and scene checks.

A mesh node's half-edge loops are read with numpy (no Python loop over faces), so a map of several hundred thousand
faces is turned into triangles in seconds. The whole scene of a map is kept in a cache file next to nothing the
user owns (.local/cache/faces/<addon>/<map>.npz); it is used again while the map file has the same size and
modification time, so a second preview or check of an unchanged map does not even parse the map.
Specification: docs/specs/pipeline.md ("The face index").
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np

from .config import PROJECT_ROOT
from .dmx import Element

VERSION = 3                              # bump when the stored arrays change
CACHE = PROJECT_ROOT / '.local' / 'cache' / 'faces'
CACHE_LIMIT = 600 * 1024 * 1024          # bytes kept for all maps together; the oldest files go first
MAX_DEGREE = 4096                        # a face loop longer than this is a corrupt mesh

# flags of a triangle's node
SOLID, RENDER_ONLY, TOOL, SCENERY = 1, 2, 4, 8
_TOOL_WORDS = ('tools', 'toolsskybox', 'toolstrigger', 'toolslightmapres', 'postprocess_volume')


def _rotation(angles) -> np.ndarray:
    """Hammer angles (pitch, yaw, roll) as the matrix checks._transform applies: roll, then pitch, then yaw."""
    pitch, yaw, roll = (math.radians(float(a)) for a in angles)
    cp, sp, cy, sy, cr, sr = math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw), math.cos(roll), math.sin(roll)
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return rz @ ry @ rx


def _vec(value, default) -> tuple:
    try:
        v = tuple(float(x) for x in value)
        return v if len(v) == 3 else default
    except (TypeError, ValueError):
        return default


def world_vertices(node: Element) -> np.ndarray | None:
    """(V, 3) vertex positions of a mesh node in its parent's space (as checks._vertices)."""
    md = node.get('meshData')
    if not isinstance(md, Element):
        return None
    try:
        streams = md['vertexData']['streams']
    except (KeyError, TypeError):
        return None
    data = next((st['data'] for st in streams if st.get('semanticName') == 'position'), None)
    if data is None:
        data = streams[0]['data'] if streams else None
    if not data:
        return None
    v = np.asarray(data, dtype=np.float64).reshape(-1, 3)
    scales = _vec(node.get('scales'), (1.0, 1.0, 1.0))
    angles = _vec(node.get('angles'), (0.0, 0.0, 0.0))
    if scales != (1.0, 1.0, 1.0):
        v = v * np.asarray(scales)
    if any(angles):
        v = v @ _rotation(angles).T
    return v + np.asarray(_vec(node.get('origin'), (0.0, 0.0, 0.0)))


def face_loops(md: Element) -> np.ndarray:
    """(F, D) vertex indices of every face in loop order, padded with -1 (D = the longest loop)."""
    dest = np.asarray(md['edgeVertexIndices'], dtype=np.int64)
    nxt = np.asarray(md['edgeNextIndices'], dtype=np.int64)
    start = np.asarray(md['faceEdgeIndices'], dtype=np.int64)
    if not len(start) or not len(dest):
        return np.zeros((0, 3), dtype=np.int64)
    ok = (start >= 0) & (start < len(dest))
    cur = np.where(ok, start, 0)
    active = ok.copy()
    cols = []
    for _ in range(MAX_DEGREE):
        cols.append(np.where(active, dest[cur], -1))
        cur = nxt[cur]
        cur = np.where((cur >= 0) & (cur < len(dest)), cur, start)
        active &= cur != start
        if not active.any():
            break
    return np.stack(cols, axis=1)


def face_materials(md: Element) -> tuple[np.ndarray, list[str]]:
    """(F,) index into the mesh's materials of every face, and those materials."""
    mats = [str(x) for x in md.get('materials', [])]
    count = len(md['faceEdgeIndices'])
    index = None
    face_data = md.get('faceData')
    for st in (face_data.get('streams', []) if isinstance(face_data, Element) else []):
        if str(st.get('standardAttributeName') or st.get('name') or '').startswith('materialindex'):
            index = np.asarray(st['data'], dtype=np.int64)
    if index is None or not len(index):
        return np.zeros(count, dtype=np.int64), mats
    data_of = np.asarray(md.get('faceDataIndices') or [], dtype=np.int64)
    if len(data_of) == count:
        index = index[np.clip(data_of, 0, len(index) - 1)]
    elif len(index) != count:
        index = np.resize(index, count)
    return np.clip(index, 0, max(len(mats) - 1, 0)), mats


def triangles(loops: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fan triangles of face loops: (T, 3) vertex indices and (T,) the face of each (as checks._triangles)."""
    if loops.shape[1] < 3:
        return np.zeros((0, 3), dtype=np.int64), np.zeros(0, dtype=np.int64)
    tris, faces = [], []
    first = loops[:, 0]
    for i in range(1, loops.shape[1] - 1):
        has = loops[:, i + 1] >= 0
        if not has.any():
            break
        idx = np.nonzero(has)[0]
        tris.append(np.stack([first[idx], loops[idx, i], loops[idx, i + 1]], axis=1))
        faces.append(idx)
    if not tris:
        return np.zeros((0, 3), dtype=np.int64), np.zeros(0, dtype=np.int64)
    tri, face = np.concatenate(tris), np.concatenate(faces)
    order = np.argsort(face, kind='stable')          # face by face, each fan in order
    return tri[order], face[order]


def node_flags(node: Element, mats: list[str]) -> int:
    lower = [m.lower() for m in mats]
    flags = 0
    if lower and all(any(w in m for w in _TOOL_WORDS) for m in lower):
        flags |= TOOL
    if node.get('physicsType') == 'none':
        flags |= RENDER_ONLY
    elif not flags & TOOL or any('clip' in m or 'nodraw' in m or 'invisible' in m for m in lower):
        flags |= SOLID
    return flags


class Scene:
    """Every triangle of a map. tris (T, 3, 3) float32 world space; node (T,) node id; mat (T,) index into
    materials; flags (T,) SOLID | RENDER_ONLY | TOOL | SCENERY; props: [{id, model, class, lo, hi, origin, angles}]."""

    def __init__(self, tris, node, mat, flags, materials, props, sun=None, bounds=None):
        self.tris, self.node, self.mat, self.flags = tris, node, mat, flags
        self.materials, self.props, self.sun = materials, props, sun
        self._bounds = bounds

    @property
    def bounds(self):
        """((x0, y0, z0), (x1, y1, z1)) of what is drawn and stands in the playable part (no tool faces)."""
        if self._bounds is None and len(self.tris):
            keep = (self.flags & (TOOL | SCENERY)) == 0
            pts = self.tris[keep] if keep.any() else self.tris
            self._bounds = (tuple(float(x) for x in pts.reshape(-1, 3).min(axis=0)),
                            tuple(float(x) for x in pts.reshape(-1, 3).max(axis=0)))
        return self._bounds

    @property
    def core(self):
        """The bounds of where most of the map stands: a far backdrop or a sky box does not stretch them (the
        middle 96 % of the solid triangles, with a margin)."""
        if not len(self.tris):
            return None
        keep = ((self.flags & (TOOL | SCENERY)) == 0) & ((self.flags & SOLID) > 0)
        pts = self.tris[keep] if keep.sum() > 50 else self.tris
        centre = pts.mean(axis=1)
        lo, hi = np.percentile(centre, 2, axis=0), np.percentile(centre, 98, axis=0)
        pad = (hi - lo) * 0.08 + 64
        full = self.bounds
        return (tuple(float(max(a - p, f)) for a, p, f in zip(lo, pad, full[0])),
                tuple(float(min(b + p, f)) for b, p, f in zip(hi, pad, full[1])))

    def in_box(self, box, margin: float = 0.0) -> np.ndarray:
        """Mask of the triangles that touch the zone [[x0, y0], [x1, y1]] seen from above."""
        lo, hi = self.tris[:, :, :2].min(axis=1), self.tris[:, :, :2].max(axis=1)
        return ((lo[:, 0] <= box[1][0] + margin) & (hi[:, 0] >= box[0][0] - margin) &
                (lo[:, 1] <= box[1][1] + margin) & (hi[:, 1] >= box[0][1] - margin))


def extract(project, m) -> Scene:
    """Read every mesh of a loaded map (layers expanded by the caller if it wants them)."""
    from . import visual
    scenery = visual.scenery_ids(m)
    materials: dict[str, int] = {}
    tris, nodes, mats, flags = [], [], [], []
    for node in m.iter_nodes():
        if node.type != 'CMapMesh':
            continue
        md = node.get('meshData')
        verts = world_vertices(node)
        if verts is None or not isinstance(md, Element):
            continue
        try:
            loops = face_loops(md)
            tri, face = triangles(loops)
            index, names = face_materials(md)
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if not len(tri) or tri.max() >= len(verts):
            continue
        table = np.asarray([materials.setdefault(n.lower(), len(materials)) for n in names] or
                           [materials.setdefault('', len(materials))], dtype=np.int32)
        flag = node_flags(node, names) | (SCENERY if node.get('nodeID') in scenery else 0)
        tris.append(verts[tri].astype(np.float32))
        nodes.append(np.full(len(tri), int(node.get('nodeID') or 0), dtype=np.int32))
        mats.append(table[index[face]] if len(names) else np.full(len(tri), table[0], dtype=np.int32))
        flags.append(np.full(len(tri), flag, dtype=np.uint8))
    props = []
    for entry in visual._prop_entries(project, m):
        lo, hi = entry['bounds']
        local = entry.get('local_bounds') or (lo, hi)
        props.append({'id': entry['id'], 'model': entry['model'], 'class': entry['class'],
                      'lo': [round(float(x), 2) for x in lo], 'hi': [round(float(x), 2) for x in hi],
                      'local': [[round(float(x), 2) for x in local[0]], [round(float(x), 2) for x in local[1]]],
                      'origin': list(_vec(entry['node'].get('origin'), (0.0, 0.0, 0.0))),
                      'angles': list(_vec(entry['node'].get('angles'), (0.0, 0.0, 0.0))),
                      'scales': list(_vec(entry['node'].get('scales'), (1.0, 1.0, 1.0)))})
    sun = None
    for ent in m.entities('light_environment'):
        sun = list(_vec(ent.get('angles'), (45.0, 45.0, 0.0)))
        break
    if tris:
        return Scene(np.concatenate(tris), np.concatenate(nodes), np.concatenate(mats), np.concatenate(flags),
                     list(materials), props, sun)
    empty = np.zeros((0, 3, 3), dtype=np.float32)
    return Scene(empty, np.zeros(0, np.int32), np.zeros(0, np.int32), np.zeros(0, np.uint8), list(materials), props, sun)


# -- the cache ---------------------------------------------------------------------------------------------------------
def _cache_file(s, name: str) -> Path:
    return CACHE / s.addon / f'{name}.npz'


def _stamp(path: Path) -> list | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return [st.st_mtime_ns, st.st_size, VERSION]


def _layers(project, m) -> dict:
    """{layer prefab path: its stamp}: a map's layered terrain lives in other files that may change on their own."""
    out = {}
    for node in m.iter_nodes():
        rel = str(node.get('targetMapPath') or '') if node.type == 'CMapPrefab' else ''
        if rel.startswith('maps/prefabs/'):
            out[rel] = _stamp(project.s.content_addon / rel)
    return out


def _trim() -> None:
    """Keep the cache under its limit: the files not used for the longest time go first."""
    try:
        files = sorted((p for p in CACHE.rglob('*.npz')), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        for p in files:
            if total <= CACHE_LIMIT:
                break
            total -= p.stat().st_size
            p.unlink()
    except OSError:
        pass


def scene(project, name: str, m=None) -> Scene:
    """The scene of map `name`: from the cache while the map file is unchanged, else read from the map (given as
    `m` when the caller has it loaded) and stored for the next call."""
    path = project.s.map_path(name)
    cache = _cache_file(project.s, name)
    stamp = _stamp(path)
    if stamp is not None and cache.is_file():
        try:
            with np.load(cache, allow_pickle=False) as data:
                meta = json.loads(str(data['meta']))
                layers = meta.get('layers') or {}
                if meta.get('stamp') == stamp and all(_stamp(project.s.content_addon / rel) == st
                                                      for rel, st in layers.items()):
                    os.utime(cache)
                    return Scene(data['tris'], data['node'], data['mat'], data['flags'], meta['materials'],
                                 meta['props'], meta.get('sun'))
        except (OSError, ValueError, KeyError):
            pass
    if m is None:
        from .terrain import expand_layers
        m = expand_layers(project, project.load(name))
    sc = extract(project, m)
    if stamp is not None and _stamp(path) == stamp:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_name(cache.name + f'.{os.getpid()}.tmp')
            meta = json.dumps({'stamp': stamp, 'materials': sc.materials, 'props': sc.props, 'sun': sc.sun,
                               'layers': _layers(project, m)})
            with open(tmp, 'wb') as f:
                np.savez(f, tris=sc.tris, node=sc.node, mat=sc.mat, flags=sc.flags, meta=np.asarray(meta))
            os.replace(tmp, cache)
            _trim()
        except OSError:
            pass
    return sc
