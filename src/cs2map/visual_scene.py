"""visual_check findings for what hangs on, stands on and stops at something (journal 26.10-03.11: a big town map
needed its own scripts for all of them; these are their map-independent versions).

- visual_wall_gap: a wall part (atlas/kit mount "wall", an AC unit, a "wall_mounted" model) whose back stands
  off the wall, hangs past the wall's end or is sunk deep into it (28 AC units hung 1-23 units off; the 16-unit
  floating test passed them all).
- visual_unit_over_window: a wall part over a window pane of the same wall.
- visual_cable_in_air: an add_cables end with no wall, pole, pipe or model within 6 units.
- visual_door_over_window: a door panel over a window pane of the same facade.
- visual_paved_slab: the free edge of a paved mesh standing over the terrain beside it (its side shows as a
  dark band; 73 % of 3.5 km of road edges stood 7-14 units up).
- visual_bare_clip: a player clip with nothing visible near it (an invisible wall).
- visual_fence_no_collision: a visible fence without collision (players walk through it).
"""
from __future__ import annotations

import json
import math

import numpy as np

from . import checks

STEEP = 0.35              # |normal z| below this: a wall face
WALL_GAP = 1.5            # a wall part's back further than this from its wall hangs off it (1 unit is the rule)
WALL_SUNK = 4.0           # ... and deeper than this in it is sunk (render bounds are a little loose)
WALL_REACH = 32.0         # the wall is searched this far behind each side
PANE_NEAR = 30.0          # a pane this close to a wall part's back plane is in the same wall
CABLE_REACH = 6.0
DOOR_PANE_NEAR = 16.0
DOOR_OVERLAP = 46.5       # square units (0.03 m2)
SLAB_STEP = 32.0          # samples along a paved edge
SLAB_OUT = 12.0           # the ground is read this far outside the edge
SLAB_SHOW = 7.0           # an edge this high over the ground shows its side (a kerb is up to ~6)
SLAB_REPORT = 64.0        # ... over this much length is reported
GAP_STEP = 16.0           # samples along a free paved edge for gaps and patches
GAP_REACH = 96.0          # another paving this close across lower ground makes a gap
GAP_LEVEL = 24.0          # ... when its top is within this of the edge's height
GAP_DEEP = 2.0            # ... and everything between them lies this much under both
GAP_HEAD = 72.0           # what hangs higher than this over the strip (a canopy, a roof) does not fill it
GAP_REPORT = 48.0         # units of edge per pair of meshes
PATCH_AREA = 93000.0      # 60 m2 of top: a smaller paving piece that joins nothing is a patch
PATCH_MIN = 3100.0        # 2 m2: smaller pieces are objects (a step, a slab under a post)
PATCH_FREE = 0.9          # the share of its outline with nothing beside it
CLIP_STEP = 32.0
CLIP_NEAR = 28.0          # something visible this close to a clip makes it a real barrier
CLIP_REPORT = 64.0

_WALL_TOKENS = ('wall_mounted', 'wallmount', 'wall_mount', 'ac_unit', 'air_conditioner', 'aircon')
_NOT_UNIT = ('window', 'shutter', 'glass', 'blind', 'curtain', 'frame', 'grate', 'bars', 'awning')
_GLASS = ('glass', 'window', 'pane')
_FENCE = ('fence', 'railing', 'rail_', 'grate', 'grille', 'chainlink', 'chain_link', 'wire', 'picket',
          'lattice', 'plank', 'palisade', 'netting')
_CLIP = ('toolsplayerclip', 'toolsclip')
_NOT_GROUND = ('roof', 'facade', 'plaster', 'wall', 'trim', 'parapet')     # tops nobody walks to a clip on


class Zone:
    """One zone of a map under audit (visual_check box=...). Only the meshes within `context` units of the box
    are loaded, so a check that adds up samples along a long mesh (a road, a clip, a joint) counts those in the
    zone only: further out its neighbours are missing and bare ground would be read where a kerb stands."""

    def __init__(self, box, report: float, context: float):
        (self.x0, self.y0), (self.x1, self.y1) = box
        self.report, self.context = report, context

    def reports(self, x: float, y: float) -> bool:
        """Is the place in the zone (findings there are the zone's)?"""
        r = self.report
        return self.x0 - r <= x <= self.x1 + r and self.y0 - r <= y <= self.y1 + r

    def known(self, x: float, y: float, reach: float) -> bool:
        """Is everything within `reach` units of the place loaded?"""
        r = self.context - reach
        return self.x0 - r <= x <= self.x1 + r and self.y0 - r <= y <= self.y1 + r


class TriIndex:
    """Triangles in numpy arrays bucketed by XY cells: vertical hits, rays and point distances near a place."""

    def __init__(self, tris, cell: float = 128.0):
        self.cell = cell
        arr = np.asarray(tris, dtype=float).reshape(-1, 3, 3)
        self.a, self.b, self.c = arr[:, 0], arr[:, 1], arr[:, 2]
        self.lo, self.hi = arr.min(axis=1), arr.max(axis=1)
        self.cells: dict = {}
        if not len(arr):
            return
        g0 = np.floor(self.lo[:, :2] / cell).astype(np.int64)
        g1 = np.floor(self.hi[:, :2] / cell).astype(np.int64)
        single = np.all(g0 == g1, axis=1)
        one = np.nonzero(single)[0]
        if len(one):
            keys = g0[one, 0] * 4_000_003 + g0[one, 1]
            order = np.argsort(keys, kind='stable')
            for group in np.split(one[order], np.nonzero(np.diff(keys[order]))[0] + 1):
                self.cells[(int(g0[group[0], 0]), int(g0[group[0], 1]))] = list(group)
        for i in np.nonzero(~single)[0].tolist():
            for gx in range(g0[i, 0], g1[i, 0] + 1):
                for gy in range(g0[i, 1], g1[i, 1] + 1):
                    self.cells.setdefault((gx, gy), []).append(i)

    def near(self, x0, x1, y0, y1) -> np.ndarray:
        c = self.cell
        found = []
        for gx in range(math.floor(x0 / c), math.floor(x1 / c) + 1):
            for gy in range(math.floor(y0 / c), math.floor(y1 / c) + 1):
                found.extend(self.cells.get((gx, gy), ()))
        if not found:
            return np.zeros(0, dtype=np.int64)
        idx = np.unique(np.asarray(found, dtype=np.int64))
        keep = (self.lo[idx, 0] <= x1) & (self.hi[idx, 0] >= x0) & (self.lo[idx, 1] <= y1) & (self.hi[idx, 1] >= y0)
        return idx[keep]

    def vhits(self, x: float, y: float) -> np.ndarray:
        """z of every triangle a vertical line at (x, y) crosses."""
        idx = self.near(x, x, y, y)
        if not len(idx):
            return np.zeros(0)
        a, b, c = self.a[idx], self.b[idx], self.c[idx]
        d = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
        ok = np.abs(d) > 1e-9
        d = np.where(ok, d, 1.0)
        u = ((x - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (y - a[:, 1])) / d
        v = ((b[:, 0] - a[:, 0]) * (y - a[:, 1]) - (x - a[:, 0]) * (b[:, 1] - a[:, 1])) / d
        ok &= (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6)
        z = a[:, 2] + u * (b[:, 2] - a[:, 2]) + v * (c[:, 2] - a[:, 2])
        return z[ok]

    def heights(self, xs, ys, idx) -> np.ndarray:
        """(points, len(idx)) z of triangles `idx` on vertical lines at the points (xs, ys); nan where a line
        misses the triangle."""
        a, b, c = self.a[idx], self.b[idx], self.c[idx]
        x, y = np.asarray(xs, dtype=float)[:, None], np.asarray(ys, dtype=float)[:, None]
        d = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
        ok = np.abs(d) > 1e-9
        d = np.where(ok, d, 1.0)
        u = ((x - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (y - a[:, 1])) / d
        v = ((b[:, 0] - a[:, 0]) * (y - a[:, 1]) - (x - a[:, 0]) * (b[:, 1] - a[:, 1])) / d
        z = a[:, 2] + u * (b[:, 2] - a[:, 2]) + v * (c[:, 2] - a[:, 2])
        return np.where(ok & (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6), z, np.nan)

    def ray(self, origin, direction, tmax: float, tmin: float = 0.0) -> float | None:
        """Nearest t in [tmin, tmax] where the ray meets a triangle (either side)."""
        o = np.asarray(origin, dtype=float)
        d = np.asarray(direction, dtype=float)
        e = o + d * tmax
        s = o + d * tmin
        idx = self.near(min(s[0], e[0]), max(s[0], e[0]), min(s[1], e[1]), max(s[1], e[1]))
        if not len(idx):
            return None
        v0 = self.a[idx]
        e1, e2 = self.b[idx] - v0, self.c[idx] - v0
        h = np.cross(d, e2)
        det = np.einsum('ij,ij->i', e1, h)
        ok = np.abs(det) > 1e-9
        f = 1.0 / np.where(ok, det, 1.0)
        q = o - v0
        u = f * np.einsum('ij,ij->i', q, h)
        r = np.cross(q, e1)
        v = f * (r @ d)
        t = f * np.einsum('ij,ij->i', e2, r)
        ok &= (u >= -1e-6) & (u <= 1 + 1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6) & (t >= tmin) & (t <= tmax)
        return float(t[ok].min()) if ok.any() else None

    def distance(self, p, reach: float) -> float | None:
        """Distance from point p to the nearest triangle within `reach` (None: none that close)."""
        p = np.asarray(p, dtype=float)
        idx = self.near(p[0] - reach, p[0] + reach, p[1] - reach, p[1] + reach)
        if len(idx):
            idx = idx[(self.lo[idx, 2] <= p[2] + reach) & (self.hi[idx, 2] >= p[2] - reach)]
        if not len(idx):
            return None
        best = _point_tri_distance(p, self.a[idx], self.b[idx], self.c[idx]).min()
        return float(best) if best <= reach else None


def _point_tri_distance(p, a, b, c) -> np.ndarray:
    """Exact distances from p to many triangles: the plane when p projects inside, else the nearest edge."""
    n = np.cross(b - a, c - a)
    ln = np.linalg.norm(n, axis=1)
    nn = n / np.where(ln > 1e-12, ln, 1.0)[:, None]
    dist_plane = np.einsum('ij,ij->i', p - a, nn)
    proj = p - nn * dist_plane[:, None]

    def side(x, y):
        return np.einsum('ij,ij->i', np.cross(y - x, proj - x), n)
    inside = (side(a, b) >= 0) & (side(b, c) >= 0) & (side(c, a) >= 0) & (ln > 1e-12)

    def seg(x, y):
        d = y - x
        t = np.clip(np.einsum('ij,ij->i', p - x, d) / np.maximum(np.einsum('ij,ij->i', d, d), 1e-12), 0, 1)
        return np.linalg.norm(p - (x + d * t[:, None]), axis=1)
    edges = np.minimum(np.minimum(seg(a, b), seg(b, c)), seg(c, a))
    return np.where(inside, np.abs(dist_plane), edges)


def unit_nz(arr: np.ndarray) -> np.ndarray:
    """z of the unit normal of every triangle of an (n, 3, 3) array: _normal(t)[2] for all at once, in the same
    arithmetic, so a threshold picks the same triangles."""
    e1, e2 = arr[:, 1] - arr[:, 0], arr[:, 2] - arr[:, 0]
    nx = e1[:, 1] * e2[:, 2] - e1[:, 2] * e2[:, 1]
    ny = e1[:, 2] * e2[:, 0] - e1[:, 0] * e2[:, 2]
    nz = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
    ln = np.sqrt(nx * nx + ny * ny + nz * nz)
    return nz / np.where(ln == 0, 1.0, ln)


def tris_of(groups, keep=None) -> np.ndarray:
    """The triangles of (node, box, triangles) groups as one array, in their order; keep(unit normal z) picks
    some (walls, tops). The arrays are made once per mesh inside checks.scene_cache()."""
    arr = checks.tri_arrays([tris for _, _, tris in groups])
    return arr if keep is None or not len(arr) else arr[keep(unit_nz(arr))]


def _unit(v):
    ln = math.sqrt(sum(x * x for x in v)) or 1.0
    return tuple(x / ln for x in v)


def _normal(tri):
    return _unit(checks._cross(checks._sub(tri[1], tri[0]), checks._sub(tri[2], tri[0])))


# ---- wall parts ----------------------------------------------------------------------------------------------
def _wall_part(project, entry, mount_of) -> bool:
    name = entry['model'].lower()
    if any(t in name for t in _NOT_UNIT):
        return False
    return mount_of(project, entry) == 'wall' or any(t in name for t in _WALL_TOKENS)


def _glass_panes(visible):
    """Vertical window panes: (centre, unit normal, horizontal unit axis, (u0, u1), (z0, z1))."""
    from .visual import _faces
    panes = []
    for node, _, _ in visible:
        mats = [str(x).lower() for x in node['meshData'].get('materials', [])]
        if not any(g in mat for mat in mats for g in _GLASS):
            continue
        for points, mat in _faces(node):
            if not any(g in mat for g in _GLASS):
                continue
            n = _normal(points[:3])
            if abs(n[2]) > STEEP:
                continue
            zs = [p[2] for p in points]
            axis = _unit((-n[1], n[0], 0.0))
            us = [p[0] * axis[0] + p[1] * axis[1] for p in points]
            if max(zs) - min(zs) < 12 or max(us) - min(us) < 8:
                continue
            centre = tuple(sum(p[k] for p in points) / len(points) for k in range(3))
            panes.append((centre, n, axis, (min(us), max(us)), (min(zs), max(zs))))
    return panes


def wall_checks(project, props, visible, mount_of, frame_of) -> list:
    """visual_wall_gap and visual_unit_over_window for wall parts."""
    cands = [e for e in props if _wall_part(project, e, mount_of)]
    if not cands:
        return []
    walls = TriIndex(tris_of(visible, lambda nz: np.abs(nz) < STEEP))
    panes = None
    out = []
    for entry in cands:
        origin, axes, (lo, hi) = frame_of(entry)
        best = None
        for k in (0, 1):
            other = 1 - k
            for sign in (1, -1):
                d = tuple(axes[k][i] * sign for i in range(3))
                face = hi[k] if sign > 0 else lo[k]
                gaps = []
                for fu, fz in ((0.5, 0.5), (0.15, 0.15), (0.85, 0.15), (0.15, 0.85), (0.85, 0.85)):
                    local = [0.0, 0.0, 0.0]
                    local[k] = face
                    local[other] = lo[other] + (hi[other] - lo[other]) * fu
                    local[2] = lo[2] + (hi[2] - lo[2]) * fz
                    p = [origin[i] + sum(axes[j][i] * local[j] for j in range(3)) for i in range(3)]
                    # The wall in front of the back face (a gap), or just behind it (sunk; a short reach, so the
                    # rays of the front side do not find the wall through the part itself).
                    ahead = walls.ray(p, d, WALL_REACH)
                    behind = walls.ray(p, tuple(-x for x in d), WALL_SUNK * 2)
                    choices = [x for x in (ahead, None if behind is None else -behind) if x is not None]
                    gaps.append(min(choices, key=abs) if choices else None)
                hits = [g for g in gaps if g is not None]
                score = (len(hits), -min((abs(g) for g in hits), default=1e9))
                if best is None or score > best[0]:
                    best = (score, gaps, d, k, other, sign)
        (count, _), gaps, d, k, other, sign = best
        if count < 3:
            continue          # no wall behind any side: visual_wall_part_off_wall (mount "wall") covers it
        hits = [g for g in gaps if g is not None]
        centre = [round(x) for x in (np.add(entry['bounds'][0], entry['bounds'][1]) / 2)]
        why = None
        if min(hits) > WALL_GAP:
            why = f'its back stands {min(hits):.1f}-{max(hits):.1f} units off the wall'
        elif max(hits) < -WALL_SUNK:
            why = f'it is sunk {-max(hits):.1f} units into the wall'
        elif count < 5:
            why = f'{5 - count} of 5 rays from its back find no wall: it hangs past the end of the wall'
        elif max(hits) > 4.0 and max(hits) - min(hits) > 3.0:
            why = f'one side stands {max(hits):.1f} units off the wall (turned, or the wall is not flat there)'
        if why:
            out.append(checks.problem('warning', 'visual_wall_gap', entry['id'], f"{entry['model']} at {centre}: {why}",
                                      'Move it along its back axis onto the wall (gap <= 1 unit), turn it parallel '
                                      'to the wall, or move it where the wall continues'))
        # Over a window: its rectangle on the wall plane meets a pane of the same wall.
        if panes is None:
            panes = _glass_panes(visible)
        back = [origin[i] + sum(axes[j][i] * ((hi[j] if sign > 0 else lo[j]) if j == k else 0.0)
                                for j in range(3)) for i in range(3)]
        lo_w, hi_w = entry['bounds']
        for pc, pn, pa, (u0, u1), (z0, z1) in panes:
            if abs(pn[0] * d[0] + pn[1] * d[1]) < 0.9:
                continue
            if abs((pc[0] - back[0]) * d[0] + (pc[1] - back[1]) * d[1]) > PANE_NEAR:
                continue
            corners = [(x, y) for x in (lo_w[0], hi_w[0]) for y in (lo_w[1], hi_w[1])]
            us = [x * pa[0] + y * pa[1] for x, y in corners]
            w = min(max(us), u1) - max(min(us), u0)
            h = min(hi_w[2], z1) - max(lo_w[2], z0)
            if w >= 6 and h >= 6:               # render bounds are a little loose: a real cover, not a touch
                out.append(checks.problem('warning', 'visual_unit_over_window', entry['id'],
                    f"{entry['model']} at {centre} covers a window pane by {w:.0f} x {h:.0f} units",
                    'Move it beside or under the window onto the plain wall'))
                break
    return out


# ---- cables --------------------------------------------------------------------------------------------------
def cable_checks(m, props, visible) -> list:
    cables = []
    for node, _, _ in visible:
        raw = node.get('cs2map_cable')
        if raw:
            try:
                cables.append((node, [tuple(map(float, p)) for p in json.loads(raw)]))
            except (ValueError, TypeError):
                pass
    if not cables:
        return []
    cable_ids = {id(n) for n, _ in cables}
    anchors = TriIndex(tris_of([g for g in visible if id(g[0]) not in cable_ids]))
    box_lo = np.array([e['bounds'][0] for e in props], dtype=float).reshape(-1, 3) - CABLE_REACH
    box_hi = np.array([e['bounds'][1] for e in props], dtype=float).reshape(-1, 3) + CABLE_REACH
    out, seen = [], set()
    for node, ends in cables:
        for p in ends:
            key = tuple(round(x / 8) for x in p)
            if key in seen:
                continue
            if anchors.distance(p, CABLE_REACH) is not None:
                continue
            if len(box_lo) and bool(np.any(np.all((box_lo <= p) & (p <= box_hi), axis=1))):
                continue
            seen.add(key)
            out.append(checks.problem('warning', 'visual_cable_in_air', node.get('nodeID'),
                f"a cable end at {[round(x) for x in p]} is fixed to nothing (no wall, pole, pipe or model within "
                f"{CABLE_REACH:g} units)", 'Move the end onto a wall, a pole or an insulator model; cables meeting '
                'in the air need a pole there'))
            out[-1]['at'] = [round(x, 1) for x in p]
    return out


# ---- doors over windows --------------------------------------------------------------------------------------
def door_window_checks(visible) -> list:
    from .visual import _faces
    doors = []
    for node, _, _ in visible:
        mats = [str(x).lower() for x in node['meshData'].get('materials', [])]
        if not any('door' in mat for mat in mats):
            continue
        for points, mat in _faces(node):
            if 'door' not in mat:
                continue
            n = _normal(points[:3])
            if abs(n[2]) > STEEP:
                continue
            axis = _unit((-n[1], n[0], 0.0))
            zs = [p[2] for p in points]
            us = [p[0] * axis[0] + p[1] * axis[1] for p in points]
            if not (60 <= max(zs) - min(zs) <= 130 and 20 <= max(us) - min(us) <= 110):
                continue
            plane = points[0][0] * n[0] + points[0][1] * n[1]
            doors.append((node, n, axis, plane, (min(us), max(us)), (min(zs), max(zs))))
    if not doors:
        return []
    panes = _glass_panes(visible)
    out, seen = [], set()
    for node, n, axis, plane, (u0, u1), (z0, z1) in doors:
        if node.get('nodeID') in seen:
            continue
        for pc, pn, pa, (pu0, pu1), (pz0, pz1) in panes:
            if abs(pn[0] * n[0] + pn[1] * n[1]) < 0.9:
                continue
            dist = abs(pc[0] * n[0] + pc[1] * n[1] - plane)
            if dist > DOOR_PANE_NEAR:
                continue
            us = sorted((pu0 * (pa[0] * axis[0] + pa[1] * axis[1]), pu1 * (pa[0] * axis[0] + pa[1] * axis[1])))
            w = min(u1, us[1]) - max(u0, us[0])
            h = min(z1, pz1) - max(z0, pz0)
            if w <= 0 or h <= 0 or w * h < DOOR_OVERLAP:
                continue
            # A glazed door: the pane lies inside the door's own outline and plane.
            if dist <= 3 and us[0] >= u0 - 2 and us[1] <= u1 + 2 and pz0 >= z0 - 2 and pz1 <= z1 + 2:
                continue
            seen.add(node.get('nodeID'))
            out.append(checks.problem('warning', 'visual_door_over_window', node.get('nodeID'),
                f"door panel of mesh {node.get('nodeID')} lies over a window pane ({w:.0f} x {h:.0f} units, "
                f"{dist:.0f} units apart) near {[round(x) for x in pc]}",
                'Move the door to a plain stretch of the wall or remove the window behind it'))
            break
    return out


# ---- paved slabs over the ground -----------------------------------------------------------------------------
def paved_slab_checks(m, meshes, paved, skip=frozenset()) -> list:
    """Free edges of paved tops standing over the terrain beside them. `paved(node)` says which meshes are
    paving."""
    return paved_checks(m, meshes, paved, skip, ('slab',))


def paved_checks(m, meshes, paved, skip=frozenset(), kinds=('slab', 'gap', 'patch'), zone: Zone | None = None) -> list:
    """What is wrong at the edges of paving that lies on the terrain: visual_paved_slab (an edge standing over
    the ground), visual_paved_gap (soil between two pavings), visual_paved_patch (a piece that joins nothing).
    zone: the audit of one zone, where an edge is judged by its part in the zone."""
    from . import terrain
    plans = terrain.plans(m)
    if not plans:
        return []

    def ground(x, y):
        for data in plans:
            lo, hi = data['bounds']
            if lo[0] <= x <= hi[0] and lo[1] <= y <= hi[1]:
                try:
                    return terrain.sample(data, x, y)['z']
                except Exception:
                    return None
        return None

    paving = _paving(meshes, paved, skip, ground)
    if not paving:
        return []
    every = TriIndex(tris_of(meshes))
    out = []
    for node, tops, own, free in (paving if 'slab' in kinds else ()):
        length = worst = 0.0
        where = None
        for a, b, nx, ny, ln in free:
            dx, dy = b[0] - a[0], b[1] - a[1]
            steps = max(1, int(ln // SLAB_STEP))
            for s in range(steps):
                f = (s + 0.5) / steps
                q = (a[0] + dx * f, a[1] + dy * f, a[2] + (b[2] - a[2]) * f)
                if zone and not zone.reports(q[0], q[1]):
                    continue
                x, y = q[0] + nx * SLAB_OUT, q[1] + ny * SLAB_OUT
                if len(own.vhits(x, y)):
                    continue                             # its own top goes on (a notch, a bend)
                g = ground(x, y)
                if g is None or q[2] - g < SLAB_SHOW:
                    continue
                # Anything of another mesh over the terrain there (a top, a lower road beside a kerb, a wall) means
                # the edge is not standing over bare ground: steps between meshes are visual_paved_lip's.
                if any(z > g + 0.5 for z in every.vhits(x, y)):
                    continue
                length += ln / steps
                if q[2] - g > worst:
                    worst, where = q[2] - g, q
        if length >= SLAB_REPORT:
            out.append(checks.problem('warning', 'visual_paved_slab', node.get('nodeID'),
                f"{length / 39.37:.1f} m of the edge of paved mesh {node.get('nodeID')} stand {SLAB_SHOW:g}+ units "
                f"over the ground (up to {worst:.0f}, near {[round(x) for x in where]}): its side shows as a band",
                'Sink the edge into the ground: raise the terrain along it to 2-4 units under the top, or lower '
                'the paving; a kerb or a chamfer mesh along the edge also hides it'))
            out[-1]['at'] = [round(x, 1) for x in where]
    if 'gap' in kinds:
        out.extend(_paved_gap_checks(paving, every, ground, zone))
    if 'patch' in kinds:
        out.extend(_paved_patch_checks(paving, every, zone))
    return out


def _paving(meshes, paved, skip, ground) -> list:
    """The paving meshes lying on the terrain: [(node, top triangles, TriIndex of the tops, free edges)]. A free
    edge is (a, b, nx, ny, length): an edge used by one top triangle, with its normal pointing out of the top."""
    out = []
    for node, bound, tris in meshes:
        if node.get('nodeID') in skip or not paved(node):
            continue
        if min(bound[1][0] - bound[0][0], bound[1][1] - bound[0][1]) < 48 or bound[1][2] - bound[0][2] > 96:
            continue                                     # kerbs, posts and walls are not ground paving
        tops = [t for t in tris if _normal(t)[2] > 0.7]
        if not tops:
            continue
        # The terrain's own surface is not a slab; a raised floor (a terrace, a roof) is not on the ground.
        on = near = 0
        sample = tops[:: max(1, len(tops) // 200)]
        for t in sample:
            g = ground(sum(p[0] for p in t) / 3, sum(p[1] for p in t) / 3)
            z = sum(p[2] for p in t) / 3
            if g is not None and abs(z - g) < 0.05:
                on += 1
            elif g is not None and -48 <= z - g <= 48:
                near += 1
        if on > 0.8 * len(sample) or near < 0.7 * len(sample):
            continue
        edges: dict = {}
        for t in tops:
            for i in range(3):
                a, b = t[i], t[(i + 1) % 3]
                key = tuple(sorted((tuple(round(x, 1) for x in a), tuple(round(x, 1) for x in b))))
                edges.setdefault(key, []).append((a, b, t))
        free = []
        for items in edges.values():
            if len(items) != 1:
                continue
            a, b, t = items[0]
            dx, dy = b[0] - a[0], b[1] - a[1]
            ln = math.hypot(dx, dy)
            if ln < 1.0:
                continue
            nx, ny = dy / ln, -dx / ln
            cx, cy = sum(p[0] for p in t) / 3, sum(p[1] for p in t) / 3
            if (cx - a[0]) * nx + (cy - a[1]) * ny > 0:
                nx, ny = -nx, -ny                        # outward: away from its own triangle
            free.append((a, b, nx, ny, ln))
        out.append((node, tops, TriIndex(tops), free))
    return out


def _edge_samples(free, step: float):
    """Points along free edges every `step` units: (point on the edge, outward nx, ny, the length it stands for)."""
    for a, b, nx, ny, ln in free:
        steps = max(1, int(ln // step))
        for s in range(steps):
            f = (s + 0.5) / steps
            yield ((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f), nx, ny, ln / steps)


def _paved_gap_checks(paving, every: TriIndex, ground, zone: Zone | None = None) -> list:
    """visual_paved_gap: soil showing in a strip between two pavings (27.10, a town map: a footway broken by a
    driveway with grass between the pieces). From a free paved edge another paving's top lies 12..96 units
    straight out at about the same height, and everything between them up to head height is lower than both
    (a kerb or a wall standing in the strip fills it; a canopy over it does not)."""
    if len(paving) < 2:
        return []
    tris, owner = [], []
    for k, (_, tops, _, _) in enumerate(paving):
        tris.extend(tops)
        owner.extend([k] * len(tops))
    index = TriIndex(tris)
    owner = np.asarray(owner, dtype=np.int64)
    reach = np.arange(SLAB_OUT, GAP_REACH + 0.1, 6.0)
    found: dict = {}                    # (node id a, node id b) -> {side k: [length, depth, width, where]}
    for k, (node, _, own, free) in enumerate(paving):
        for q, nx, ny, part in _edge_samples(free, GAP_STEP):
            if zone and not zone.reports(q[0], q[1]):
                continue
            if len(own.vhits(q[0] + nx * SLAB_OUT, q[1] + ny * SLAB_OUT)):
                continue                                 # its own top goes on (a notch, a bend)
            xs, ys = q[0] + nx * reach, q[1] + ny * reach
            idx = index.near(float(xs.min()), float(xs.max()), float(ys.min()), float(ys.max()))
            if not len(idx):
                continue
            z = index.heights(xs, ys, idx)               # (points, triangles), nan where the point is off it
            level = np.abs(z - q[2])
            mine = (owner[idx] == k)[None, :] & (level < 8)
            other = (owner[idx] != k)[None, :] & (level <= GAP_LEVEL)
            hit = None
            for j in range(len(reach)):
                if mine[j].any():
                    break                                # back on its own paving: this way is not out
                if other[j].any():
                    col = np.where(other[j], z[j], -np.inf)
                    best = int(np.argmax(col))
                    hit = (float(reach[j]), float(col[best]), int(owner[idx[best]]))
                    break
            if hit is None:
                continue
            width, zo, ko = hit
            mx, my = q[0] + nx * width / 2, q[1] + ny * width / 2
            low = min(q[2], zo)
            between = [float(v) for v in every.vhits(mx, my) if v <= low + GAP_HEAD]
            g = ground(mx, my)
            if g is not None:
                between.append(g)
            if not between or max(between) >= low - GAP_DEEP:
                continue                                 # filled (a kerb, a filler, the ground at their level)
            other_id = paving[ko][0].get('nodeID')
            pair = tuple(sorted((node.get('nodeID'), other_id)))
            side = found.setdefault(pair, {}).setdefault(k, [0.0, 0.0, 0.0, q])
            side[0] += part
            side[2] = max(side[2], width)
            if low - max(between) > side[1]:
                side[1], side[3] = low - max(between), q
    out = []
    for (a, b), sides in found.items():
        length, depth, width, where = max(sides.values(), key=lambda s: s[0])     # seen from both: the longer side
        if length < GAP_REPORT:
            continue
        out.append(checks.problem('warning', 'visual_paved_gap', a,
            f"paved meshes {a} and {b} leave a strip up to {width:.0f} units wide along {length / 39.37:.1f} m near "
            f"{[round(x) for x in where]} where the ground lies {depth:.0f} units lower: soil shows between them",
            'Join them: extend one paving to the other or add a paving mesh between them; a kerb or the terrain '
            'raised to their level also closes the strip'))
        out[-1].update({'ids': [a, b], 'at': [round(x, 1) for x in where]})
    return out


def _paved_patch_checks(paving, every: TriIndex, zone: Zone | None = None) -> list:
    """visual_paved_patch: a small piece of paving that joins nothing (asphalt rectangles lying alone in a
    yard). Under 60 m2 of top, and along 90 % of its outline nothing of another mesh stands 12 units out at its
    level: no paving, kerb or wall. In a zone audit a piece is judged only when its whole outline is loaded."""
    out = []
    for node, tops, own, free in paving:
        if zone and not all(zone.known(p[0], p[1], SLAB_OUT + 4) for a, b, *_ in free for p in (a, b)):
            continue
        arr = np.asarray(tops, dtype=float).reshape(-1, 3, 3)
        e1, e2 = arr[:, 1] - arr[:, 0], arr[:, 2] - arr[:, 0]
        area = float(np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]).sum() / 2)
        if not PATCH_MIN <= area < PATCH_AREA:
            continue
        total = alone = 0
        for q, nx, ny, _ in _edge_samples(free, GAP_STEP):
            x, y = q[0] + nx * SLAB_OUT, q[1] + ny * SLAB_OUT
            if len(own.vhits(x, y)):
                continue
            total += 1
            alone += not any(v >= q[2] - 8 for v in every.vhits(x, y))
        if total >= 4 and alone >= PATCH_FREE * total:
            centre = [round(float(v), 1) for v in arr.reshape(-1, 3).mean(axis=0)]
            out.append(checks.problem('warning', 'visual_paved_patch', node.get('nodeID'),
                f"paved mesh {node.get('nodeID')} ({area / 1550:.1f} m2) near {[round(v) for v in centre]} joins no "
                "other paving, kerb or wall: a stray piece of paving on the ground",
                'Join it to the paving it belongs to or remove it; a pad meant to stand alone needs a border '
                '(a kerb, a frame) to read as one'))
            out[-1]['at'] = centre
    return out


# ---- player clips and fences ---------------------------------------------------------------------------------
def _is_clip(node) -> bool:
    mats = [str(x).lower() for x in node['meshData'].get('materials', [])]
    return bool(mats) and all(any(c in mat for c in _CLIP) for mat in mats)


def clip_checks(m, meshes, visible, props, skip=frozenset(), zone: Zone | None = None) -> list:
    """visual_bare_clip: a player clip with nothing visible within CLIP_NEAR where a player meets it."""
    clips = [(n, b, tris) for n, b, tris in meshes if _is_clip(n) and n.get('nodeID') not in skip]
    if not clips:
        return []
    # Ground a player walks up to the clip on: paving and floors (ground materials or tags "paved"), never roofs or
    # wall tops (a map's boundary clips stand over roofs nobody reaches), plus the terrain.
    from .visual import _faces, _paved, _lip_ground
    tops = []
    for n, _, _ in meshes:
        if _is_clip(n):
            continue
        tagged = 'paved' in str(n.get('cs2map_tags') or '').split(',')
        for points, mat in _faces(n):
            if any(w in mat for w in _NOT_GROUND) or not (tagged or _lip_ground(mat)) or _normal(points[:3])[2] <= 0.5:
                continue
            tops.extend((points[0], points[i], points[i + 1]) for i in range(1, len(points) - 1))
    floors = TriIndex(tops)
    # Walls, fence boards, railing posts: steep visible faces (a sloped ground beside the clip is no barrier).
    seen_tris = TriIndex(tris_of(visible, lambda nz: np.abs(nz) < 0.7))
    from . import terrain
    plans = terrain.plans(m)

    def ground(x, y, top):
        zs = [z for z in floors.vhits(x, y) if z <= top]
        for data in plans:
            lo, hi = data['bounds']
            if lo[0] <= x <= hi[0] and lo[1] <= y <= hi[1]:
                try:
                    g = terrain.sample(data, x, y)['z']
                    if g <= top:
                        zs.append(g)
                except Exception:
                    pass
                break
        return max(zs, default=None)

    out = []
    for node, bound, tris in clips:
        bare = 0.0
        where = None
        sides = [t for t in tris if abs(_normal(t)[2]) < 0.3]
        done = set()
        for t in sides:
            n = _normal(t)
            for i in range(3):
                a, b = t[i], t[(i + 1) % 3]
                if abs(a[2] - b[2]) > 1:
                    continue                             # only the horizontal edges of a side
                key = (round(a[0]), round(a[1]), round(b[0]), round(b[1]), round(n[0], 1), round(n[1], 1))
                rkey = (round(b[0]), round(b[1]), round(a[0]), round(a[1]), round(n[0], 1), round(n[1], 1))
                if key in done or rkey in done:
                    continue
                done.add(key)
                ln = math.dist(a[:2], b[:2])
                if ln < 8:
                    continue
                steps = max(1, int(ln // CLIP_STEP))
                z_lo = min(p[2] for p in t)
                z_hi = max(p[2] for p in t)
                for s in range(steps):
                    f = (s + 0.5) / steps
                    x, y = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
                    if zone and not zone.reports(x, y):
                        continue
                    ox, oy = x + n[0] * 24, y + n[1] * 24       # where a player walks up to it
                    g = ground(ox, oy, z_hi)
                    if g is None or z_lo > g + 48 or z_hi < g + 48:
                        continue                             # it does not stop a player standing there
                    box = ((x - CLIP_NEAR, y - CLIP_NEAR, g + 8), (x + CLIP_NEAR, y + CLIP_NEAR, g + 96))
                    idx = seen_tris.near(box[0][0], box[1][0], box[0][1], box[1][1])
                    idx = idx[(seen_tris.lo[idx, 2] <= box[1][2]) & (seen_tris.hi[idx, 2] >= box[0][2])]
                    if len(idx):
                        continue
                    if any(all(lo[k] <= box[1][k] and hi[k] >= box[0][k] for k in range(3)) for lo, hi in
                           (e['bounds'] for e in props)):
                        continue
                    bare += ln / steps
                    where = (x, y, g)
        if bare >= CLIP_REPORT:
            out.append(checks.problem('warning', 'visual_bare_clip', node.get('nodeID'),
                f"{bare / 39.37:.1f} m of player clip {node.get('nodeID')} stand with nothing visible within "
                f"{CLIP_NEAR:g} units (an invisible wall) near {[round(v) for v in where]}",
                'Put a visible barrier there (add_fence, a wall, a railing) or move the clip onto one'))
            out[-1]['at'] = [round(v, 1) for v in where]
    return out


def fence_checks(m, meshes, render_only, skip=frozenset()) -> list:
    """visual_fence_no_collision: a visible fence-like mesh without collision and no solid or clip at it."""
    out = []
    if not render_only:
        return out
    solid = TriIndex(tris_of(meshes, lambda nz: np.abs(nz) < 0.7))
    for node in render_only:
        if node.get('nodeID') in skip:
            continue
        mats = [str(x).lower() for x in node['meshData'].get('materials', [])]
        if not any(f in mat for mat in mats for f in _FENCE):
            continue
        bound = checks._mesh_bounds(node)
        if not bound:
            continue
        lo, hi = bound
        dx, dy, dz = (hi[i] - lo[i] for i in range(3))
        if not (32 <= dz <= 200 and min(dx, dy) <= 24 and max(dx, dy) >= 64):
            continue
        idx = solid.near(lo[0] - 8, hi[0] + 8, lo[1] - 8, hi[1] + 8)
        idx = idx[(solid.lo[idx, 2] <= lo[2] + 64) & (solid.hi[idx, 2] >= lo[2] + 8)]
        if len(idx):
            continue                                     # a wall, a solid twin or a clip stands along it
        out.append(checks.problem('warning', 'visual_fence_no_collision', node.get('nodeID'),
            f"fence-like mesh {node.get('nodeID')} ({', '.join(sorted(set(mats)))[:80]}) has no collision and "
            f"nothing solid along it: players walk through it",
            'Give it collision (physicsType default) or a toolsplayerclip box along it; add_fence does both'))
    return out
