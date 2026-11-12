"""Region builds: one zone of a big map as a throwaway map that compiles and loads in seconds.

map_build(name, region=[[x0, y0], [x1, y1]]) copies what stands in the box (or boxes) into the map
<name>_region: objects that touch it whole, the ground and other big meshes cut by faces with a margin around it.
Coordinates stay those of the full map, so views and player marks apply as they are. The region map is cut from
the saved map by the same code every time: it never feeds anything back into the full map.
Specification: docs/specs/pipeline.md ("Region builds").
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from . import faceindex, mesh as meshlib, meshops, system
from .config import PROJECT_ROOT, check_name
from .dmx import Element
from .mesh import Mesh, MeshError
from .vmap import MapError, VMap

MARGIN = 384.0            # big meshes (ground, long walls) keep their faces this far around the region
TOUCH = 64.0              # an object this close to the region belongs to it
WHOLE_FACES = 64          # meshes with up to this many faces are never cut
MAX_BOXES = 8
MARK_HALF = 640.0         # region="marks": a box of this half-size around every player mark
SLOTS = ('_region', '_region2')
STATE = PROJECT_ROOT / '.local' / 'state' / 'regions.json'
# What sets the look of the whole map is kept wherever it stands.
_GLOBAL = ('light_environment', 'env_sky', 'env_gradient_fog', 'env_cubemap_fog', 'env_volumetric_fog',
           'env_tonemap_controller', 'post_processing_volume', 'env_wind', 'skybox_reference', 'sky_camera',
           'env_fog_controller', 'env_time_of_day', 'info_map_parameters')
_SPAWNS = ('info_player_terrorist', 'info_player_counterterrorist', 'info_player_start', 'info_deathmatch_spawn')


def parse(region, project=None, name: str | None = None) -> list[list[list[float]]]:
    """[[x0, y0], [x1, y1]], a list of such boxes, or "marks" (boxes around the player marks of this map)."""
    if region == 'marks':
        from . import marks
        boxes = []
        for shot in marks.listing(project.s, name, 0, 50)['shots']:
            pos = shot.get('pos')
            if pos:
                boxes.append([[pos[0] - MARK_HALF, pos[1] - MARK_HALF], [pos[0] + MARK_HALF, pos[1] + MARK_HALF]])
        if not boxes:
            raise MapError(f'region="marks": no player marks with a position for map {name}')
        return _merge(boxes)[:MAX_BOXES]
    try:
        if region and isinstance(region[0][0], (int, float)):
            region = [region]
        boxes = []
        for (x0, y0), (x1, y1) in region:
            x0, y0, x1, y1 = float(x0), float(y0), float(x1), float(y1)
            boxes.append([[min(x0, x1), min(y0, y1)], [max(x0, x1), max(y0, y1)]])
    except (TypeError, ValueError, IndexError):
        raise MapError('region: [[x0, y0], [x1, y1]] (a zone seen from above), a list of up to '
                       f'{MAX_BOXES} such boxes, or "marks"') from None
    if not boxes or len(boxes) > MAX_BOXES:
        raise MapError(f'region: 1..{MAX_BOXES} boxes')
    for (x0, y0), (x1, y1) in boxes:
        if x1 - x0 < 64 or y1 - y0 < 64:
            raise MapError('region: a box is at least 64 x 64 units')
    return boxes


def _merge(boxes: list) -> list:
    """Boxes that overlap become one (marks made next to each other)."""
    out: list = []
    for box in sorted(boxes):
        for other in out:
            if (box[0][0] <= other[1][0] and box[1][0] >= other[0][0] and
                    box[0][1] <= other[1][1] and box[1][1] >= other[0][1]):
                other[0] = [min(other[0][0], box[0][0]), min(other[0][1], box[0][1])]
                other[1] = [max(other[1][0], box[1][0]), max(other[1][1], box[1][1])]
                break
        else:
            out.append([list(box[0]), list(box[1])])
    return out


def _touches(lo, hi, boxes, margin: float) -> bool:
    return any(lo[0] <= b[1][0] + margin and hi[0] >= b[0][0] - margin and
               lo[1] <= b[1][1] + margin and hi[1] >= b[0][1] - margin for b in boxes)


def _within(lo, hi, boxes, margin: float) -> bool:
    return any(lo[0] >= b[0][0] - margin and hi[0] <= b[1][0] + margin and
               lo[1] >= b[0][1] - margin and hi[1] <= b[1][1] + margin for b in boxes)


def _subset(node: Element, keep: np.ndarray, texture_size) -> bool:
    """Rewrite a mesh node with only the faces of `keep`. False = this mesh cannot be cut (it is kept whole)."""
    try:
        polys = meshops.decode_node(node)
    except (MeshError, KeyError, TypeError, ValueError, IndexError):
        return False
    if len(polys) != len(keep):
        return False
    polys = [p for p, k in zip(polys, keep) if k]
    origin = meshops.vector(node['origin'])
    m = Mesh()
    try:
        for p in polys:
            opts = {}
            if p.attributes:
                opts = {'u_axis': p.attributes['textureAxisU'][:3], 'v_axis': p.attributes['textureAxisV'][:3],
                        'scale': tuple(p.attributes['textureScale']),
                        'offset': (p.attributes['textureAxisU'][3], p.attributes['textureAxisV'][3])}
            m.face([tuple(a - b for a, b in zip(v.p, origin)) for v in p.corners], p.material, **opts)
        if len(m.faces) != len(polys):
            return False
        md = m.to_dmx(origin=origin, texture_size=texture_size, smoothing=float(node.get('smoothingAngle', 40.0) or 0))
    except (MeshError, KeyError, TypeError, ValueError, IndexError):
        return False                               # a cut that leaves a non-manifold corner: keep the mesh whole
    fv, fd = meshops.stream_map(md['faceVertexData']), meshops.stream_map(md['faceData'])
    dest, faces = md['edgeVertexIndices'], md['edgeFaceIndices']
    lookups = [{i: v for i, v in zip(f.verts, p.corners)} for f, p in zip(m.faces, polys)]
    tint, blend = [], []
    for h, fi in enumerate(faces):
        v = lookups[fi].get(dest[h]) if fi >= 0 else None
        if v is None:
            tint.append((1.0, 1.0, 1.0, 1.0))
            blend.append((0.0, 0.0, 0.0, 0.0))
            continue
        fv['texcoord']['data'][h] = v.uv
        tint.append(v.tint)
        blend.append(v.blend)
    for fi, p in enumerate(polys):
        for key, stream in fd.items():
            if key in p.attributes and key != 'materialindex':
                stream['data'][fi] = p.attributes[key]
    md['faceVertexData']['streams'] += [meshlib._paint_stream('VertexPaintTintColor', tint),
                                        meshlib._paint_stream('VertexPaintBlendParams', blend)]
    node['meshData'] = md
    return True


def cut(project, m: VMap, boxes: list) -> dict:
    """Reduce a loaded map (in memory) to the region. Returns {kept, dropped, cut, whole_big, spawn}."""
    stats = {'kept': 0, 'dropped': 0, 'cut': 0, 'kept_whole': 0}
    spawns_inside = 0

    def mesh_state(node: Element) -> str:
        verts = faceindex.world_vertices(node)
        md = node.get('meshData')
        if verts is None or not isinstance(md, Element) or not len(verts):
            return 'keep'
        lo, hi = verts.min(axis=0), verts.max(axis=0)
        if not _touches(lo, hi, boxes, MARGIN):
            return 'drop'
        if _within(lo, hi, boxes, MARGIN):
            return 'keep'
        faces = len(md['faceEdgeIndices'])
        if faces <= WHOLE_FACES:
            return 'keep' if _touches(lo, hi, boxes, TOUCH) else 'drop'
        try:
            loops = faceindex.face_loops(md)
        except (KeyError, TypeError, ValueError, IndexError):
            return 'keep'
        pad = np.where(loops >= 0, loops, loops[:, :1])
        xy = verts[pad][:, :, :2]
        flo, fhi = xy.min(axis=1), xy.max(axis=1)
        keep = np.zeros(len(loops), dtype=bool)
        for b in boxes:
            keep |= ((flo[:, 0] <= b[1][0] + MARGIN) & (fhi[:, 0] >= b[0][0] - MARGIN) &
                     (flo[:, 1] <= b[1][1] + MARGIN) & (fhi[:, 1] >= b[0][1] - MARGIN))
        if keep.all():
            return 'keep'
        if not keep.any():
            return 'drop'
        if _subset(node, keep, project.texture_size):
            stats['cut'] += 1
            return 'keep'
        stats['kept_whole'] += 1
        return 'keep'

    def keep_node(node: Element) -> bool:
        nonlocal spawns_inside
        if node.type == 'CMapMesh':
            return mesh_state(node) == 'keep'
        kids = [c for c in node.get('children', []) if isinstance(c, Element)]
        if node.type == 'CMapEntity':
            cls = VMap.classname(node)
            if kids:                                   # a brush entity lives where its meshes are
                bounds = [faceindex.world_vertices(c) for c in kids if c.type == 'CMapMesh']
                bounds = [v for v in bounds if v is not None and len(v)]
                if not bounds:
                    return True
                lo = np.min([v.min(axis=0) for v in bounds], axis=0)
                hi = np.max([v.max(axis=0) for v in bounds], axis=0)
                return _touches(lo, hi, boxes, TOUCH)
            if any(cls.startswith(g) for g in _GLOBAL):
                return True
            origin = node.get('origin')
            if origin is None:
                return True
            inside = _touches(origin, origin, boxes, TOUCH)
            if cls in _SPAWNS:
                inside = _touches(origin, origin, boxes, 0.0)
                spawns_inside += inside
            return inside
        if kids or node.type == 'CMapGroup':
            node['children'] = [c for c in node.get('children', []) if not isinstance(c, Element) or walk(c)]
            return bool(node['children'])
        origin = node.get('origin')
        return origin is None or _touches(origin, origin, boxes, TOUCH)

    def walk(node: Element) -> bool:
        ok = keep_node(node)
        stats['kept' if ok else 'dropped'] += 1
        return ok

    m.world['children'] = [c for c in m.world.get('children', []) if not isinstance(c, Element) or walk(c)]
    m._node_ids = m._ref_ids = None
    if not any(n.type == 'CMapMesh' for n in m.iter_nodes()):
        raise MapError('region: nothing of the map stands in this zone (check the coordinates: x, y of the box '
                       'corners in units)')
    # A spawn inside the region: a player who spawns in the void falls for ever and the frames are black.
    b = boxes[0]
    cx, cy = (b[0][0] + b[1][0]) / 2, (b[0][1] + b[1][1]) / 2
    top = _ground(project, m, cx, cy)
    stats['center'] = [round(cx, 1), round(cy, 1), round(top, 1)]
    if not spawns_inside:
        for i, cls in enumerate(('info_player_terrorist', 'info_player_counterterrorist')):
            ent = m.new_entity(cls, {}, (cx + (i * 2 - 1) * 24, cy, top + 16), (0, 0, 0), None,
                               defaults=project.catalog.defaults(cls))
            m.attach(ent)
        stats['spawn'] = 'added at the centre of the first box'
    return stats


def _ground(project, m: VMap, x: float, y: float) -> float:
    """The highest solid surface under (x, y), or the middle height of the map there."""
    sc = faceindex.extract(project, m)
    solid = sc.tris[(sc.flags & faceindex.SOLID) > 0]
    if not len(solid):
        return 0.0
    a, b, c = solid[:, 0], solid[:, 1], solid[:, 2]
    near = ((np.minimum(np.minimum(a[:, 0], b[:, 0]), c[:, 0]) <= x) & (np.maximum(np.maximum(a[:, 0], b[:, 0]), c[:, 0]) >= x) &
            (np.minimum(np.minimum(a[:, 1], b[:, 1]), c[:, 1]) <= y) & (np.maximum(np.maximum(a[:, 1], b[:, 1]), c[:, 1]) >= y))
    a, b, c = a[near].astype(np.float64), b[near].astype(np.float64), c[near].astype(np.float64)
    if len(a):
        d = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
        ok = np.abs(d) > 1e-9
        d = np.where(ok, d, 1.0)
        w0 = ((b[:, 1] - c[:, 1]) * (x - c[:, 0]) + (c[:, 0] - b[:, 0]) * (y - c[:, 1])) / d
        w1 = ((c[:, 1] - a[:, 1]) * (x - c[:, 0]) + (a[:, 0] - c[:, 0]) * (y - c[:, 1])) / d
        w2 = 1 - w0 - w1
        hit = ok & (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if hit.any():
            return float((w0 * a[:, 2] + w1 * b[:, 2] + w2 * c[:, 2])[hit].max())
    return float(solid[:, :, 2].mean())


# -- the build ---------------------------------------------------------------------------------------------------------
def _state() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def info(name: str) -> dict | None:
    """What a region map was cut from: {source, boxes, preset, time}, or None for an ordinary map."""
    if not name.endswith(SLOTS):
        return None
    return _state().get(name)


def _slot(project, name: str) -> str:
    """The region map to write: the one CS2 does not hold open, so the game needs no disconnect."""
    for suffix in SLOTS:
        target = name + suffix
        vpk = project.s.compiled_map(target)
        try:
            if not vpk.exists() or not system.file_in_use(vpk):
                return target
        except OSError:
            continue
    return name + SLOTS[0]


def wide_note(stats: dict, region) -> str | None:
    """A region that keeps most of the map saves nothing. 12.11: 50 marks all over a town gave 8 zones with 58 % of
    the nodes; the cut and the build took as long as the whole map."""
    total = stats.get('kept', 0) + stats.get('dropped', 0)
    if not total or stats['kept'] <= total / 2:
        return None
    return (f'the region keeps {round(100 * stats["kept"] / total)} % of the map: it costs about what the whole map '
            'costs. Give the box of the zone you work on'
            + (' (region="marks" takes every stored mark of the map)' if region == 'marks' else ''))


def build(project, name: str, region, preset: str = 'fast') -> dict:
    """Cut the region out of map `name`, save it as a region map and start its build. Returns the job."""
    from . import compiler
    if preset not in compiler.PRESETS:
        raise MapError(f'a region is built with one of {sorted(compiler.PRESETS)} (fast is the point: seconds)')
    if name.endswith(SLOTS):
        raise MapError(f'{name} is itself a region map: give the full map')
    boxes = parse(region, project, name)
    started = time.monotonic()
    target = _slot(project, name)
    check_name(target, 'map name')
    m = project.load(name)                           # a private copy in memory: the full map is not written
    stats = cut(project, m, boxes)
    m.world.set('cs2map_region', 'string', json.dumps({'source': name, 'boxes': boxes}))
    path = project.s.map_path(target)
    project._saved_map = None
    saved = m.save(path)                             # a throwaway map: no backup copies
    state = _state()
    state[target] = {'source': name, 'boxes': boxes, 'preset': preset, 'time': time.time()}
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state), encoding='utf-8')
    except OSError:
        pass
    plan = project.build_plan(target, preset, bots=False if preset == 'fast' else None)
    released = project._release_compiled_map(target)
    job = compiler.build_map(project.s, project.jobs, target, preset, timeout=plan['timeout'], map_flags=plan['flags'])
    x, y, z = stats.pop('center')
    out = {'job': job.id, 'state': job.state, 'log': job.log, 'map': target, 'region_of': name, 'boxes': boxes,
           'nodes': stats, 'bytes': saved['bytes'], 'cut_seconds': round(time.monotonic() - started, 1),
           'hint': f'poll job_status(job), then game_screenshot(map="{target}", views=[{{"name": "centre", "pos": '
                   f'[{x}, {y}, {z}], "ang": [10, 0, 0]}}]): coordinates are those of {name}'
                   + ('; frames are fullbright (a fast build has no light)' if preset in ('fast', 'nav') else '')}
    if released:
        out['note'] = 'both region maps were in use by CS2; the game was switched to the idle map'
    wide = wide_note(stats, region)
    if wide:
        out['wide'] = wide
    return out
