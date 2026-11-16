"""What a map costs to draw, and what nobody ever sees (map_info about="cost"; guide section performance).

Offline and deterministic (no frame times, which other programs on the PC disturb): triangles of meshes and of
stock props (from the shipped facts), the heaviest models and meshes, the busiest zones, and objects outside the
places players reach that no eye can see. Open maps have no visibility culling (VIS is off over open ground), so a
background house behind a hill is drawn every frame (a town map, 11.2026: 24 houses, 2 city blocks and 205 of
1 900 vista trees were never visible).

Eyes: floor players reach from the spawns (reach.py), 64 units up, one per square of EYE_GRID, plus the spawns.
Occluders: a height map of opaque geometry (glass, water, fences, foliage and decals do not hide anything) where
each cell is a solid column up to its highest face. Arcades and canopies are columns too, so an object seen only
under them can be reported: remove a candidate only after the same views before and after compare equal.
"""
from __future__ import annotations

import math

import numpy as np

from . import faceindex

EYE_GRID = 384.0
OCC_CELL = 32.0
VIEW_CELL = 32.0
SEE_THROUGH = ('glass', 'water', 'fence', 'grate', 'chainlink', 'foliage', 'leaf', 'leaves', 'decal', 'overlay',
               'wire', 'cable', 'tools', 'sky')


def cost(project, name: str, m=None, limit: int = 12) -> dict:
    from . import propfacts, reach
    sc = faceindex.scene(project, name, m)
    if m is None:
        m = project.load(name)
    drawn = (sc.flags & faceindex.TOOL) == 0
    mesh_tris = int(drawn.sum())
    nodes, counts = np.unique(sc.node[drawn], return_counts=True)
    prop_rows, unknown = {}, 0
    prop_tris = 0
    for pr in sc.props:
        rec = propfacts.get(pr['model'])
        t = (rec or {}).get('tris')
        if t is None:
            unknown += 1
            continue
        prop_tris += t
        row = prop_rows.setdefault(pr['model'], [0, t])
        row[0] += 1
    heavy_props = sorted(({'model': k, 'count': v[0], 'tris_each': v[1], 'tris': v[0] * v[1]}
                          for k, v in prop_rows.items()), key=lambda r: -r['tris'])[:limit]
    order = np.argsort(-counts)[:limit]
    heavy_meshes = [{'id': int(nodes[i]), 'tris': int(counts[i])} for i in order]
    # zones of 2048 units: where the drawn triangles stand
    centre = sc.tris[drawn].mean(axis=1)
    zone = np.floor(centre[:, :2] / 2048).astype(int)
    keys, zc = np.unique(zone, axis=0, return_counts=True) if len(zone) else (np.zeros((0, 2), int), np.zeros(0, int))
    for pr in sc.props:                                          # props count in their zone too
        rec = propfacts.get(pr['model'])
        if rec and rec.get('tris'):
            k = (int(pr['origin'][0] // 2048), int(pr['origin'][1] // 2048))
            hit = np.where((keys[:, 0] == k[0]) & (keys[:, 1] == k[1]))[0] if len(keys) else []
            if len(hit):
                zc[hit[0]] += rec['tris']
    zones = [{'box': [[int(keys[i][0]) * 2048, int(keys[i][1]) * 2048],
                      [int(keys[i][0]) * 2048 + 2048, int(keys[i][1]) * 2048 + 2048]], 'tris': int(zc[i])}
             for i in np.argsort(-zc)[:5]]
    out = {'map': name, 'triangles': {'meshes': mesh_tris, 'props': prop_tris, 'props_unmeasured': unknown,
                                      'total': mesh_tris + prop_tris},
           'props': len(sc.props), 'heaviest_props': heavy_props, 'heaviest_meshes': heavy_meshes, 'zones': zones}
    flood = reach.flood(project, name, m)
    if flood is None or not flood['seeds']:
        out['never_seen'] = 'no spawns or no floor: add spawns to find what nobody sees'
        return out
    out['never_seen'] = never_seen(sc, m, flood, limit)
    return out


def _occluders(sc) -> tuple:
    """Height map (max z of opaque faces per cell) over the scene."""
    names = [str(s).lower() for s in sc.materials]
    clear = np.array([any(w in s for w in SEE_THROUGH) for s in names] or [False])
    opaque = ((sc.flags & faceindex.TOOL) == 0) & ~clear[sc.mat] if len(sc.mat) else np.zeros(0, bool)
    tris = sc.tris[opaque]
    lo3, hi3 = sc.bounds
    x0, y0 = lo3[0] - OCC_CELL, lo3[1] - OCC_CELL
    nx, ny = int((hi3[0] - x0) / OCC_CELL) + 2, int((hi3[1] - y0) / OCC_CELL) + 2
    from .reach import _raster
    height = np.full(nx * ny, -1e9, np.float32)
    if len(tris):
        i, z = _raster(tris, x0, y0, OCC_CELL, nx, ny)
        np.maximum.at(height, i, z)
        # thin vertical faces (walls) leave no top inside a cell: their highest corners count too
        pts = tris.reshape(-1, 3)
        gx = np.clip(((pts[:, 0] - x0) // OCC_CELL).astype(int), 0, nx - 1)
        gy = np.clip(((pts[:, 1] - y0) // OCC_CELL).astype(int), 0, ny - 1)
        np.maximum.at(height, gx * ny + gy, pts[:, 2])
    return height.reshape(nx, ny), x0, y0


def lowest_seen(height, x0, y0, eye) -> np.ndarray:
    """Per cell of VIEW_CELL: the lowest height any eye sees there over the occluder height map (a viewshed per eye:
    the steepest occluder between the eye and a cell sets the line of sight; cells are filled in bands of half a
    cell of distance, each taking its predecessor 1.5 cells nearer the eye, so the predecessor is always done)."""
    k = int(VIEW_CELL // OCC_CELL)
    nx, ny = height.shape[0] // k, height.shape[1] // k
    occ = height[:nx * k, :ny * k].reshape(nx, k, ny, k).max(axis=(1, 3)).ravel()
    gx, gy = np.meshgrid(np.arange(nx), np.arange(ny), indexing='ij')
    gx, gy = gx.ravel().astype(np.float64), gy.ravel().astype(np.float64)
    low = np.full(nx * ny, np.inf)
    for ex, ey, ez in eye.tolist():
        fx, fy = (ex - x0) / VIEW_CELL - 0.5, (ey - y0) / VIEW_CELL - 0.5      # the eye in cell coordinates
        dx, dy = gx - fx, gy - fy
        d = np.hypot(dx, dy)
        back = np.where(d > 1.5, (d - 1.5) / np.maximum(d, 1e-9), 0.0)
        px = np.clip(np.rint(fx + dx * back), 0, nx - 1).astype(np.int64)
        py = np.clip(np.rint(fy + dy * back), 0, ny - 1).astype(np.int64)
        pred = px * ny + py
        dw = np.maximum(d, 0.5) * VIEW_CELL
        own = (occ - ez) / dw                                     # the slope to the top of each cell's occluder
        slope = np.full(nx * ny, -np.inf)
        band = np.floor(d * 2).astype(np.int64)
        order = np.argsort(band, kind='stable')
        edges = np.searchsorted(band[order], np.arange(band.max() + 2))
        first = d <= 1.5
        for b in range(len(edges) - 1):
            idx = order[edges[b]:edges[b + 1]]
            if not len(idx):
                continue
            idx = idx[~first[idx]]
            if len(idx):
                pr = pred[idx]
                slope[idx] = np.maximum(slope[pr], own[pr])
        low = np.minimum(low, np.where(np.isfinite(slope), ez + slope * dw, -np.inf))
    return low.reshape(nx, ny)


def never_seen(sc, m, flood, limit: int) -> dict:
    from . import navcost, propfacts, reach
    height, x0, y0 = _occluders(sc)
    nx, ny = height.shape
    pts = reach.world(flood)[flood['reached']]
    eyes = {}
    for x, y, z in pts.tolist():                                  # one eye per square: the highest floor there
        k = (int(x // EYE_GRID), int(y // EYE_GRID))
        if k not in eyes or z > eyes[k][2]:
            eyes[k] = (x, y, z)
    eye = np.array([[x, y, z + 64] for x, y, z in eyes.values()], dtype=np.float64)
    area = navcost.play_area(m)

    near = {(int(x // 256) + dx, int(y // 256) + dy) for x, y, _ in pts.tolist() for dx in (-1, 0, 1) for dy in (-1, 0, 1)}

    def outside(x, y):
        if area:
            return not navcost.in_polygon(x, y, area['outline'])
        return (int(x // 256), int(y // 256)) not in near

    # targets: props and meshes that are not where players are; points = top corners and the top middle
    targets = []
    for pr in sc.props:
        lo, hi = pr['lo'], pr['hi']
        cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
        if outside(cx, cy):
            rec = propfacts.get(pr['model'])
            targets.append(('prop', pr['id'], pr['model'].rsplit('/', 1)[-1], (rec or {}).get('tris') or 0, lo, hi))
    drawn = np.where((sc.flags & faceindex.TOOL) == 0)[0]
    if len(drawn):
        order = drawn[np.argsort(sc.node[drawn], kind='stable')]
        ids, start, count = np.unique(sc.node[order], return_index=True, return_counts=True)
        tlo = sc.tris[order].min(axis=1)                          # per triangle, then per node with reduceat
        thi = sc.tris[order].max(axis=1)
        nlo, nhi = np.minimum.reduceat(tlo, start), np.maximum.reduceat(thi, start)
        for nid, lo, hi, c in zip(ids.tolist(), nlo.tolist(), nhi.tolist(), count.tolist()):
            if (hi[0] - lo[0]) * (hi[1] - lo[1]) > 4096 ** 2:
                continue                                          # ground and huge shells: always somewhere in view
            if outside((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2):
                targets.append(('mesh', int(nid), '', int(c), lo, hi))

    low = lowest_seen(height, x0, y0, eye)

    def visible(lo, hi) -> bool:
        cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
        tz = hi[2] - 1
        for ax, ay, az in ((cx, cy, tz), (lo[0], lo[1], tz), (hi[0], lo[1], tz), (hi[0], hi[1], tz),
                           (lo[0], hi[1], tz), (cx, cy, (lo[2] + tz) / 2)):
            gx = min(max(int((ax - x0) // VIEW_CELL), 0), low.shape[0] - 1)
            gy = min(max(int((ay - y0) // VIEW_CELL), 0), low.shape[1] - 1)
            if az >= low[gx, gy] - 1:
                return True
        return False

    hidden = [tg for tg in targets if not visible(tg[4], tg[5])]
    hidden.sort(key=lambda t: -t[3])
    return {'eyes': len(eye), 'checked': len(targets), 'count': len(hidden), 'tris': int(sum(t[3] for t in hidden)),
            'objects': [{'kind': k, 'id': i, **({'model': mdl} if mdl else {}), 'tris': tr,
                         'at': [round((lo[0] + hi[0]) / 2), round((lo[1] + hi[1]) / 2), round(lo[2])]}
                        for k, i, mdl, tr, lo, hi in hidden[:limit]],
            'note': 'candidates: canopies and arcades count as solid; delete only after the same views before and '
                    'after compare equal (reference_compare)'}
