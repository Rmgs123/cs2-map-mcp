"""Where can players stand? A flood fill from the spawns over the map's triangles (visual_check uses it).

The player is a world-axis box 32x32x72 (it does not turn with a rotated house, so a doorway of a 45-degree house
must be 45 units wide, not 32), climbs 18 units, jumps 56 units up and may drop any height. Cells of CELL units:
a node is a floor height in a cell where the box fits (no wall within 16 units between 18.5 and 72 over the floor,
no ceiling under 72). Two nodes of neighbouring cells connect when the step between them can be walked or jumped.

Used for: invisible-wall findings only where players can get (a boundary clip over roofs nobody reaches is no
problem), `visual_escape` (a player can get out of the area set with set_play_area: 10.2026 a 1 m gap between a
fence and a house let a player out of a town map), and a top view of the reachable area.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from . import faceindex

CELL = 16.0
HALF = 16.0          # half the player box
STEP = 18.5
JUMP = 56.0
TALL = 72.0
MAX_CELLS = 3_000_000
FLOOR_NZ = 0.7       # walkable up to ~45 degrees
SPAWNS = ('info_player_terrorist', 'info_player_counterterrorist', 'info_deathmatch_spawn', 'info_player_start')


def _raster(tris: np.ndarray, x0: float, y0: float, cell: float, nx: int, ny: int):
    """(cell index, z) of every cell centre inside a triangle's footprint, with the triangle's height there."""
    out_i, out_z = [], []
    lo = np.floor((tris[:, :, :2].min(axis=1) - [x0, y0]) / cell - 0.5).astype(int)
    hi = np.ceil((tris[:, :, :2].max(axis=1) - [x0, y0]) / cell - 0.5).astype(int)
    span = np.maximum(hi - lo + 1, 1).max(axis=1)
    for k in sorted(set(int(2 ** math.ceil(math.log2(max(s, 1)))) for s in span)):
        sel = np.where((span <= k) & (span > k // 2) if k > 1 else span <= 1)[0]
        if not len(sel):
            continue
        for part in np.array_split(sel, max(1, len(sel) * k * k // 2_000_000 + 1)):
            t = tris[part]
            gx = lo[part, 0][:, None, None] + np.arange(k)[None, :, None]
            gy = lo[part, 1][:, None, None] + np.arange(k)[None, None, :]
            gx, gy = np.broadcast_arrays(gx, gy)
            px, py = x0 + (gx + 0.5) * cell, y0 + (gy + 0.5) * cell
            a, b, c = t[:, 0], t[:, 1], t[:, 2]
            d = ((b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1]))[:, None, None]
            ok_d = np.abs(d) > 1e-9
            d = np.where(ok_d, d, 1)
            l1 = ((b[:, 1] - c[:, 1])[:, None, None] * (px - c[:, 0][:, None, None]) +
                  (c[:, 0] - b[:, 0])[:, None, None] * (py - c[:, 1][:, None, None])) / d
            l2 = ((c[:, 1] - a[:, 1])[:, None, None] * (px - c[:, 0][:, None, None]) +
                  (a[:, 0] - c[:, 0])[:, None, None] * (py - c[:, 1][:, None, None])) / d
            l3 = 1 - l1 - l2
            inside = ok_d & (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6) & (gx >= 0) & (gy >= 0) & (gx < nx) & (gy < ny)
            z = l1 * a[:, 2][:, None, None] + l2 * b[:, 2][:, None, None] + l3 * c[:, 2][:, None, None]
            out_i.append((gx * ny + gy)[inside])
            out_z.append(z[inside])
    if not out_i:
        return np.zeros(0, int), np.zeros(0, np.float32)
    return np.concatenate(out_i), np.concatenate(out_z).astype(np.float32)


def _walls(tris: np.ndarray, x0, y0, cell, nx, ny):
    """(cell index, zlo, zhi): cells whose player box (centre +-HALF) a steep face crosses, with its height span."""
    out_i, out_lo, out_hi = [], [], []
    a, b, c = tris[:, 0, :2], tris[:, 1, :2], tris[:, 2, :2]
    # the face seen from above: the segment between its two farthest corners (a steep face is nearly a line)
    d_ab, d_bc, d_ca = (np.linalg.norm(a - b, axis=1), np.linalg.norm(b - c, axis=1), np.linalg.norm(c - a, axis=1))
    p = np.where((d_ab >= d_bc) & (d_ab >= d_ca), 0, np.where(d_bc >= d_ca, 1, 2))
    s0 = np.where(p[:, None] == 0, a, np.where(p[:, None] == 1, b, c))
    s1 = np.where(p[:, None] == 0, b, np.where(p[:, None] == 1, c, a))
    zlo, zhi = tris[:, :, 2].min(axis=1), tris[:, :, 2].max(axis=1)
    length = np.linalg.norm(s1 - s0, axis=1)
    steps = np.maximum(1, np.ceil(length / min(cell * 0.25, 4.0))).astype(int)
    reach = int(math.ceil(HALF / cell))
    for n in sorted(set(steps.tolist())):
        sel = np.where(steps == n)[0]
        f = (np.arange(n + 1) / n)[None, :, None]
        pts = s0[sel][:, None, :] + (s1[sel] - s0[sel])[:, None, :] * f          # (T, n+1, 2)
        ci = np.floor((pts - [x0, y0]) / cell).astype(int)
        for dx in range(-reach - 1, reach + 2):
            for dy in range(-reach - 1, reach + 2):
                gx, gy = ci[..., 0] + dx, ci[..., 1] + dy
                cx, cy = x0 + (gx + 0.5) * cell, y0 + (gy + 0.5) * cell
                hit = (np.abs(cx - pts[..., 0]) <= HALF) & (np.abs(cy - pts[..., 1]) <= HALF) & \
                      (gx >= 0) & (gy >= 0) & (gx < nx) & (gy < ny)
                if not hit.any():
                    continue
                t_idx = np.broadcast_to(sel[:, None], hit.shape)[hit]
                out_i.append((gx * ny + gy)[hit])
                out_lo.append(zlo[t_idx])
                out_hi.append(zhi[t_idx])
    if not out_i:
        return np.zeros(0, int), np.zeros(0, np.float32), np.zeros(0, np.float32)
    i = np.concatenate(out_i)
    lo, hi = np.concatenate(out_lo), np.concatenate(out_hi)
    # one int64 key per (cell, low, high): a 1-D unique is many times faster than unique rows
    lo_i = np.clip(np.round(lo), -32768, 32767).astype(np.int64) + 32768
    hi_i = np.clip(np.round(hi), -32768, 32767).astype(np.int64) + 32768
    key = np.unique((i.astype(np.int64) << 32) | (lo_i << 16) | hi_i)
    return ((key >> 32).astype(int), (((key >> 16) & 0xFFFF) - 32768).astype(np.float32),
            ((key & 0xFFFF) - 32768).astype(np.float32))


def flood(project, name: str, m=None, box=None, props=True) -> dict | None:
    """{cell, origin, shape, nodes: (N, 3) [i, j, z], reached: bool (N,), seeds} or None (no spawns or no ground).
    box [[x0,y0],[x1,y1]] limits the grid (the spawns still seed when they stand in it)."""
    sc = faceindex.scene(project, name, m)
    if m is None:
        m = project.load(name)
    spawns = [tuple(float(v) for v in e.get('origin')) for cls in SPAWNS for e in m.entities(classname=cls)
              if e.get('origin') is not None]
    if not spawns or not len(sc.tris):
        return None
    solid = (sc.flags & faceindex.SOLID) > 0
    names = list(sc.materials)
    if box is None:
        lo3, hi3 = sc.bounds
        if (hi3[0] - lo3[0]) * (hi3[1] - lo3[1]) / CELL ** 2 > MAX_CELLS and sc.core:
            lo3, hi3 = sc.core                    # a far backdrop does not stretch the grid
        box = [[min([lo3[0]] + [s[0] - 256 for s in spawns]), min([lo3[1]] + [s[1] - 256 for s in spawns])],
               [max([hi3[0]] + [s[0] + 256 for s in spawns]), max([hi3[1]] + [s[1] + 256 for s in spawns])]]
    cell = CELL
    while (box[1][0] - box[0][0]) * (box[1][1] - box[0][1]) / cell ** 2 > MAX_CELLS:
        cell *= 2
    x0, y0 = box[0][0] - 2 * cell, box[0][1] - 2 * cell
    nx = int((box[1][0] - x0) / cell) + 3
    ny = int((box[1][1] - y0) / cell) + 3
    zone = sc.in_box([[x0, y0], [x0 + nx * cell, y0 + ny * cell]], HALF)
    tris = sc.tris[solid & zone]
    if not len(tris):
        return None
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    ln = np.linalg.norm(n, axis=1)
    nz = np.divide(n[:, 2], ln, out=np.zeros(len(n)), where=ln > 1e-9)
    floors = tris[nz > FLOOR_NZ]
    others = tris[(np.abs(nz) >= 0.05) & ~(nz > FLOOR_NZ)]       # ceilings, steep slopes: anything else a box meets
    steep = tris[np.abs(nz) < FLOOR_NZ]
    if props:                                                  # stock props with collision: their physics footprint
        from . import propfacts
        extra = []
        for pr in sc.props:
            rec = propfacts.get(pr['model'])
            if rec is not None and 'phys' not in rec:
                continue
            lo, hi = pr['lo'], pr['hi']
            if hi[0] < x0 or lo[0] > x0 + nx * cell or hi[1] < y0 or lo[1] > y0 + ny * cell:
                continue
            (bx0, by0), (bx1, by1) = ([lo[0], lo[1]], [hi[0], hi[1]])
            if rec is not None and rec['phys']['bands'].get('foot'):
                from .drivecheck import _prop_box
                got = _prop_box(pr, lo[2], lo[2] + TALL)
                if got is None:
                    continue
                (bx0, by0), (bx1, by1) = got
            z0, z1 = lo[2], hi[2]
            for (ax, ay), (bx, by) in (((bx0, by0), (bx1, by0)), ((bx1, by0), (bx1, by1)),
                                       ((bx1, by1), (bx0, by1)), ((bx0, by1), (bx0, by0))):
                extra.append([[ax, ay, z0], [bx, by, z0], [bx, by, z1]])
        if extra:
            steep = np.concatenate([steep, np.asarray(extra, dtype=np.float32)])
    fi, fz = _raster(floors, x0, y0, cell, nx, ny)
    oi, oz = _raster(others, x0, y0, cell, nx, ny)
    ai, az = np.concatenate([fi, oi]), np.concatenate([fz, oz])
    wi, wlo, whi = _walls(steep, x0, y0, cell, nx, ny)
    # candidate floor heights per cell (merged within 2 units)
    order = np.lexsort((fz, fi))
    fi, fz = fi[order], fz[order]
    keep = np.ones(len(fi), bool)
    keep[1:] = ~((fi[1:] == fi[:-1]) & (fz[1:] - fz[:-1] < 2))
    fi, fz = fi[keep], fz[keep]
    # a box fits: no face of any kind between 2 and 72 over the floor, no wall between 18.5 and 72 (vectorised:
    # keys cell * BIG + height, sorted; a running maximum of wall tops within each cell)
    from collections import defaultdict
    BIG, SHIFT = 1e6, 4e5
    fkey = fi.astype(np.float64) * BIG + fz + SHIFT
    akey = np.sort(ai.astype(np.float64) * BIG + az + SHIFT)
    ceiling = (np.searchsorted(akey, fkey + TALL, 'left') - np.searchsorted(akey, fkey + 2, 'right')) > 0
    order = np.lexsort((wlo, wi))
    wkey = wi[order].astype(np.float64) * BIG + wlo[order] + SHIFT
    wtop = np.maximum.accumulate(wi[order].astype(np.float64) * BIG + whi[order] + SHIFT) if len(wkey) else wkey
    last = np.searchsorted(wkey, fkey + TALL, 'left') - 1          # the last wall of the cell starting under 72
    walled = np.zeros(len(fkey), bool)
    has = last >= 0
    walled[has] = wtop[last[has]] > fkey[has] + STEP                  # its cell's highest top reaches over 18.5
    ok = ~ceiling & ~walled
    nodes = list(zip(fi[ok].tolist(), fz[ok].tolist()))
    by_cell = defaultdict(list)
    for k, (i, _) in enumerate(nodes):
        by_cell[i].append(k)
    if not nodes:
        return None
    zs = np.array([z for _, z in nodes], dtype=np.float32)
    # seeds: the highest node at or under each spawn
    seeds = []
    for sx, sy, sz in spawns:
        gx, gy = int((sx - x0) // cell), int((sy - y0) // cell)
        best = None
        for dx in (0, -1, 1):
            for dy in (0, -1, 1):
                for k in by_cell.get((gx + dx) * ny + gy + dy, ()):
                    if zs[k] <= sz + 8 and (best is None or zs[k] > zs[best]):
                        best = k
        if best is not None:
            seeds.append(best)
    reached = np.zeros(len(nodes), bool)
    queue = deque(seeds)
    for s in seeds:
        reached[s] = True
    while queue:
        k = queue.popleft()
        i, z = nodes[k]
        gx, gy = divmod(i, ny)
        # diagonal moves too: the free band for the box's centre through a doorway of a turned house is narrower
        # than a cell and runs along the diagonal
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            for j in by_cell.get((gx + dx) * ny + gy + dy, ()):
                if not reached[j] and zs[j] - z <= (JUMP if dx == 0 or dy == 0 else STEP):
                    reached[j] = True
                    queue.append(j)
    arr = np.array([[i // ny, i % ny, z] for i, z in nodes], dtype=np.float32)
    return {'cell': cell, 'origin': (x0, y0), 'shape': (nx, ny), 'nodes': arr, 'reached': reached,
            'seeds': len(seeds), 'spawns': len(spawns)}


def world(r) -> np.ndarray:
    """(N, 3) world positions of the nodes (cell centres, floor heights)."""
    x0, y0 = r['origin']
    return np.stack([x0 + (r['nodes'][:, 0] + 0.5) * r['cell'], y0 + (r['nodes'][:, 1] + 0.5) * r['cell'],
                     r['nodes'][:, 2]], axis=1)


def escapes(r, m) -> list[dict]:
    """Groups of reached floor outside the play area (set_play_area), farther out than one cell: where players get out."""
    from . import navcost
    area = navcost.play_area(m)
    if not area or r is None:
        return []
    poly = area['outline']
    pts = world(r)[r['reached']]
    out_mask = np.array([not navcost.in_polygon(x, y, poly) for x, y, _ in pts.tolist()])
    if not out_mask.any():
        return []
    outside = pts[out_mask]
    # one finding per 512-unit square of escaped floor
    groups: dict = {}
    for x, y, z in outside.tolist():
        groups.setdefault((int(x // 512), int(y // 512)), []).append((x, y, z))
    found = []
    for pts_ in sorted(groups.values(), key=len, reverse=True)[:12]:
        x, y, z = pts_[len(pts_) // 2]
        area_m2 = len(pts_) * (r['cell'] / 39.37) ** 2
        found.append({'at': [round(x, 1), round(y, 1), round(z, 1)], 'area_m2': round(area_m2, 1)})
    return found


def picture(r, path) -> str:
    """A top view: reached floor green, floor nobody reaches grey, one pixel per cell."""
    from PIL import Image
    nx, ny = r['shape']
    img = np.full((ny, nx, 3), 18, np.uint8)
    nodes = r['nodes'].astype(int)
    img[ny - 1 - nodes[:, 1], nodes[:, 0]] = (90, 90, 90)
    hit = nodes[r['reached']]
    img[ny - 1 - hit[:, 1], hit[:, 0]] = (70, 200, 90)
    im = Image.fromarray(img)
    scale = max(1, int(1024 / max(nx, ny)))
    if scale > 1:
        im = im.resize((nx * scale, ny * scale), Image.NEAREST)
    im.save(path)
    return str(path)


def lookup(r):
    """reached(x, y, z) -> bool: is there reached floor within one cell and 40 units of height of that point?"""
    from collections import defaultdict
    x0, y0 = r['origin']
    cell = r['cell']
    zs: dict = defaultdict(list)
    for (i, j, z), ok in zip(r['nodes'].tolist(), r['reached'].tolist()):
        if ok:
            zs[(int(i), int(j))].append(z)

    def reached(x, y, z):
        gx, gy = int((x - x0) // cell), int((y - y0) // cell)
        return any(abs(h - z) < 40 for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2)
                   for h in zs.get((gx + dx, gy + dy), ()))
    return reached
