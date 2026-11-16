"""Can a vehicle drive a route? An offline check on the saved map, before any build or game test.

route_test(map, route=[[x,y],...], vehicle=...) drives a kinematic bicycle (rear axle on the route, steering towards
a point ahead, the steering lock of the vehicle) over the map's triangles and reports, along the route:
grades and steps under the wheels, the nose or the tail touching the ground at a change of grade, walls, clips and
props the body would hit (at three heights of the body), the tightest free margin, and turns the vehicle cannot make
without reversing. Measured on a town map (10.2026): a bus needed one 15-minute rebuild per such problem when it was
found only by driving in the game.
"""
from __future__ import annotations

import json
import math

import numpy as np

from . import faceindex

UP_NZ = 0.5          # a face whose normal is more upward than this carries wheels
WALL_NZ = 0.7        # a face whose normal is flatter than this can stop a body
STEP_STEP = 16.0     # route resampling for the follower (units)
REPORT_EVERY = 32.0  # one pose per this much travel


class DriveError(ValueError):
    pass


# -- the vehicle -------------------------------------------------------------------------------------------------------
def vehicle_of(project, m, value) -> dict:
    """{length, width, height, wheelbase, rear_x, front_x, back_x, half, radius, clearance, steer, name}: from a
    controllable of the map (its recipe and body model) or from a dict {length, width, height?, wheelbase?,
    clearance?, steer?}."""
    if isinstance(value, str):
        for node in m.entities(classname='point_script'):
            raw = node.get('cs2map_control')
            if not raw:
                continue
            spec = json.loads(raw)
            if spec.get('name') != value:
                continue
            wheels = spec.get('wheels') or {}
            pos = wheels.get('positions') or [[62, 36], [62, -36], [-60, 36], [-60, -36]]
            front, rear = (pos[0][0] + pos[1][0]) / 2, (pos[2][0] + pos[3][0]) / 2
            radius = float(wheels.get('radius', 16))
            lo = hi = None
            if spec.get('size'):
                length, width = spec['size']
                lo, hi = [-length / 2, -width / 2], [length / 2, width / 2]
            if spec.get('model'):
                from . import propfacts, visual
                rec = propfacts.get(spec['model'])
                b = (rec['lo'], rec['hi']) if rec and rec.get('lo') else visual.model_bounds(project, spec['model'])
                if b:
                    lo, hi = [b[0][0], b[0][1], b[0][2]], [b[1][0], b[1][1], b[1][2]]
            if lo is None:
                lo, hi = [rear - 40, -max(abs(p[1]) for p in pos) - 8], [front + 40, max(abs(p[1]) for p in pos) + 8]
            steer = spec.get('steer') or {}
            steer = steer.get('max', 32) if isinstance(steer, dict) else steer
            height = (hi[2] - max(lo[2], 0)) if len(hi) > 2 else 90.0
            return _vehicle(value, lo[0], hi[0], (hi[1] - lo[1]) / 2, height, front, rear, radius,
                            max(4.0, radius * 0.5), float(steer))
        raise DriveError(f'no controllable named {value!r} on this map; give vehicle={{"length","width",...}} instead')
    if not isinstance(value, dict) or not {'length', 'width'} <= set(value):
        raise DriveError('vehicle is the name of a controllable on the map or {"length", "width", "height",'
                         ' "wheelbase", "clearance", "steer"} in units and degrees')
    length, width = float(value['length']), float(value['width'])
    if not (48 <= length <= 1200 and 32 <= width <= 400):
        raise DriveError('vehicle length 48..1200 and width 32..400 units')
    wheelbase = float(value.get('wheelbase', length * 0.6))
    half = length / 2
    return _vehicle(value.get('name', 'vehicle'), -half, half, width / 2, float(value.get('height', 90)),
                    wheelbase / 2, -wheelbase / 2, float(value.get('radius', 16)), float(value.get('clearance', 8)),
                    float(value.get('steer', 32)))


def _vehicle(name, back, front_end, half, height, front, rear, radius, clearance, steer):
    wb = max(front - rear, 16.0)
    return {'name': name, 'back_x': back, 'front_x': front_end, 'half': half, 'height': height,
            'front_axle': front, 'rear_axle': rear, 'wheelbase': wb, 'radius': radius, 'clearance': clearance,
            'steer': max(5.0, min(steer, 60.0)),
            'min_radius': round(wb / math.tan(math.radians(max(5.0, min(steer, 60.0)))), 1)}


# -- the ground and the walls near the route ------------------------------------------------------------------------
class Ground:
    def __init__(self, tris: np.ndarray, cell: float = 128.0):
        self.tris, self.cell = tris, cell
        lo, hi = tris[:, :, :2].min(axis=1), tris[:, :, :2].max(axis=1)
        self.grid: dict = {}
        for i in range(len(tris)):
            for cx in range(int(lo[i, 0] // cell), int(hi[i, 0] // cell) + 1):
                for cy in range(int(lo[i, 1] // cell), int(hi[i, 1] // cell) + 1):
                    self.grid.setdefault((cx, cy), []).append(i)

    def tops(self, x: float, y: float) -> list[float]:
        """Heights of every carrying face over (x, y)."""
        out = []
        for i in self.grid.get((int(x // self.cell), int(y // self.cell)), ()):
            a, b, c = self.tris[i]
            d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(d) < 1e-9:
                continue
            l1 = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / d
            l2 = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / d
            l3 = 1 - l1 - l2
            if min(l1, l2, l3) >= -1e-6:
                out.append(float(l1 * a[2] + l2 * b[2] + l3 * c[2]))
        return out

    def under(self, x: float, y: float, ref: float, climb: float) -> float | None:
        """The highest carrying face a wheel at height ref can reach (no more than `climb` up); else the lowest."""
        tops = self.tops(x, y)
        if not tops:
            return None
        up = [z for z in tops if z <= ref + climb]
        return max(up) if up else min(tops)


class Walls:
    def __init__(self, tris: np.ndarray, labels: list[str], cell: float = 128.0):
        self.tris, self.labels, self.cell = tris, labels, cell
        self.zlo, self.zhi = tris[:, :, 2].min(axis=1), tris[:, :, 2].max(axis=1)
        lo, hi = tris[:, :, :2].min(axis=1), tris[:, :, :2].max(axis=1)
        self.grid: dict = {}
        for i in range(len(tris)):
            for cx in range(int(lo[i, 0] // cell), int(hi[i, 0] // cell) + 1):
                for cy in range(int(lo[i, 1] // cell), int(hi[i, 1] // cell) + 1):
                    self.grid.setdefault((cx, cy), []).append(i)

    def near(self, x0, y0, x1, y1) -> set:
        out = set()
        for cx in range(int(x0 // self.cell), int(x1 // self.cell) + 1):
            for cy in range(int(y0 // self.cell), int(y1 // self.cell) + 1):
                out.update(self.grid.get((cx, cy), ()))
        return out

    def slice(self, i: int, z: float):
        """The segment where triangle i cuts the plane at height z, or None."""
        if not self.zlo[i] < z < self.zhi[i]:
            return None
        pts = []
        t = self.tris[i]
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            if (a[2] - z) * (b[2] - z) < 0:
                k = (z - a[2]) / (b[2] - a[2])
                pts.append((a[0] + k * (b[0] - a[0]), a[1] + k * (b[1] - a[1])))
        return (pts[0], pts[1]) if len(pts) == 2 else None


def _seg_rect(p, q, centre, ax, ay, hx, hy) -> float:
    """Distance from segment pq to the rectangle (centre, unit axes ax/ay, half sizes hx/hy); 0 when they cross."""
    def local(v):
        d = (v[0] - centre[0], v[1] - centre[1])
        return (d[0] * ax[0] + d[1] * ax[1], d[0] * ay[0] + d[1] * ay[1])
    a, b = local(p), local(q)
    best = math.inf
    for k in range(9):                                   # sample the segment: exact enough at 1/8 of its length
        t = k / 8
        x, y = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
        dx, dy = max(abs(x) - hx, 0.0), max(abs(y) - hy, 0.0)
        best = min(best, math.hypot(dx, dy))
    # a segment crossing the rectangle between samples
    for e0, e1 in (((-hx, -hy), (hx, -hy)), ((hx, -hy), (hx, hy)), ((hx, hy), (-hx, hy)), ((-hx, hy), (-hx, -hy))):
        d1 = (b[0] - a[0]) * (e0[1] - a[1]) - (b[1] - a[1]) * (e0[0] - a[0])
        d2 = (b[0] - a[0]) * (e1[1] - a[1]) - (b[1] - a[1]) * (e1[0] - a[0])
        d3 = (e1[0] - e0[0]) * (a[1] - e0[1]) - (e1[1] - e0[1]) * (a[0] - e0[0])
        d4 = (e1[0] - e0[0]) * (b[1] - e0[1]) - (e1[1] - e0[1]) * (b[0] - e0[0])
        if d1 * d2 < 0 and d3 * d4 < 0:
            return 0.0
    return best


def _prop_box(pr, z0, z1):
    """World [lo, hi] (xy) of what of a prop collides between heights z0 and z1: the physics bands of the shipped
    facts (a tree crown at roof height counts, its render leaves do not), else the render bounds. None: no overlap."""
    from . import propfacts
    rec = propfacts.get(pr['model'])
    s = pr.get('scales') or [1, 1, 1]
    oz = pr['origin'][2]
    if rec is None or 'phys' not in rec:
        lo, hi = pr['lo'], pr['hi']
        return None if hi[2] < z0 or lo[2] > z1 else ([lo[0], lo[1]], [hi[0], hi[1]])
    pick = [b for name, b0, b1 in propfacts.BANDS for key, b in rec['phys']['bands'].items()
            if key == name and oz + b0 * s[2] < z1 and oz + min(b1, rec['phys']['hi'][2]) * s[2] > z0]
    if not pick:
        return None
    x0, y0 = min(b[0] for b in pick) * s[0], min(b[1] for b in pick) * s[1]
    x1, y1 = max(b[2] for b in pick) * s[0], max(b[3] for b in pick) * s[1]
    yaw = math.radians(pr.get('angles', [0, 0, 0])[1])
    c, si = math.cos(yaw), math.sin(yaw)
    pts = [(pr['origin'][0] + px * c - py * si, pr['origin'][1] + px * si + py * c)
           for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    return ([min(p[0] for p in pts), min(p[1] for p in pts)], [max(p[0] for p in pts), max(p[1] for p in pts)])


# -- the drive ---------------------------------------------------------------------------------------------------------
def _resample(points, step):
    out = [tuple(points[0][:2])]
    for a, b in zip(points, points[1:]):
        n = max(1, int(math.dist(a[:2], b[:2]) // step))
        out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(1, n + 1)]
    return out


def check(project, name: str, route, vehicle) -> dict:
    if not isinstance(route, list) or len(route) < 2 or not all(
            isinstance(p, (list, tuple)) and len(p) in (2, 3) and all(isinstance(v, (int, float)) for v in p)
            for p in route):
        raise DriveError('route is a list of [x, y] (or [x, y, z]) points in units, at least two')
    m = project.load(name)
    v = vehicle_of(project, m, vehicle)
    sc = faceindex.scene(project, name)
    path = _resample(route, STEP_STEP)
    margin = max(v['front_x'], -v['back_x'], v['half']) + 256
    xs, ys = [p[0] for p in path], [p[1] for p in path]
    box = [[min(xs) - margin, min(ys) - margin], [max(xs) + margin, max(ys) + margin]]
    zone = sc.in_box(box)
    flags = sc.flags
    tris = sc.tris
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    length = np.linalg.norm(n, axis=1)
    nz = np.divide(n[:, 2], length, out=np.zeros(len(n)), where=length > 1e-9)
    names = [sc.materials[i] for i in range(len(sc.materials))]
    playerclip = np.array(['playerclip' in s for s in names] or [False])[sc.mat] if len(sc.mat) else np.zeros(0, bool)
    solid = ((flags & faceindex.SOLID) > 0) & ~playerclip & zone
    carry = solid & (np.abs(nz) > UP_NZ) & (nz > 0)
    wall = solid & (np.abs(nz) < WALL_NZ)
    if not carry.any():
        raise DriveError('no solid ground under the route on this map')
    ground = Ground(tris[carry])
    wall_idx = np.where(wall)[0]
    labels = ['clip' if (flags[i] & faceindex.TOOL) else f'mesh {int(sc.node[i])}' for i in wall_idx]
    walls = Walls(tris[wall_idx], labels)
    props = []
    from . import propfacts
    for pr in sc.props:
        rec = propfacts.get(pr['model'])
        if rec is not None and 'phys' not in rec:
            continue                                     # no collision at all
        lo, hi = pr['lo'], pr['hi']
        if hi[0] < box[0][0] or lo[0] > box[1][0] or hi[1] < box[0][1] or lo[1] > box[1][1]:
            continue
        props.append(pr)

    # start: rear axle on the route, heading along it
    x, y = path[0]
    yaw = math.atan2(path[1][1] - y, path[1][0] - x)
    first = route[0][2] if len(route[0]) == 3 else None
    tops = ground.tops(x, y)
    if not tops:
        raise DriveError(f'nothing to drive on at the start {route[0]}')
    if first is None:                                    # the highest top with room for the body above it
        first = max(z for z in tops if not any(z < t < z + v['height'] for t in tops))
    lookahead = max(v['wheelbase'], 96.0)
    max_steer = math.radians(v['steer'])
    ds = 8.0
    progress, travelled, last_report, stall = 0, 0.0, -1e9, 0
    z_rear = z_front = first
    poses, problems = [], {'grades': [], 'steps': [], 'scrapes': [], 'hits': [], 'props': [], 'turns': []}
    tightest = []
    climb = max(v['radius'] * 0.6, 8.0)
    end = path[-1]
    best_d = math.inf
    while True:
        # nearest route point ahead of the current progress
        window = range(progress, min(len(path), progress + 40))
        j = min(window, key=lambda k: math.dist(path[k], (x, y)))
        if j > progress:
            progress, stall = j, 0
        else:
            stall += 1
        off = math.dist(path[progress], (x, y))
        if progress >= len(path) - 3 and math.dist(end, (x, y)) < 48:
            break
        if off > max(2 * v['half'], 96) or stall > 400:
            problems['turns'].append({'at': [round(x), round(y)], 'travelled': round(travelled),
                                      'message': f'the vehicle cannot follow the route here without reversing: its '
                                                 f'tightest turn is a radius of {v["min_radius"]} units (wheelbase '
                                                 f'{v["wheelbase"]:.0f}, steering {v["steer"]:.0f}°)'})
            break
        target = path[min(len(path) - 1, progress)]
        k = progress
        while k < len(path) - 1 and math.dist(path[k], (x, y)) < lookahead:
            k += 1
        target = path[k]
        alpha = math.atan2(target[1] - y, target[0] - x) - yaw
        alpha = math.atan2(math.sin(alpha), math.cos(alpha))
        steer = max(-max_steer, min(max_steer, math.atan2(2 * v['wheelbase'] * math.sin(alpha), lookahead)))
        x += ds * math.cos(yaw)
        y += ds * math.sin(yaw)
        yaw += ds / v['wheelbase'] * math.tan(steer)
        travelled += ds
        if travelled - last_report < REPORT_EVERY:
            continue
        last_report = travelled
        fx, fy = math.cos(yaw), math.sin(yaw)
        lx, ly = -fy, fx
        # wheels: rear axle at (x, y), front axle wheelbase ahead
        rz = ground.under(x, y, z_rear, climb)
        fxp, fyp = x + fx * v['wheelbase'], y + fy * v['wheelbase']
        fz = ground.under(fxp, fyp, z_front, climb)
        at = [round(x), round(y)]
        if rz is None or fz is None:
            problems['steps'].append({'at': at, 'message': 'no ground under a wheel (off the paving or a hole)'})
            break
        for wx, wy, z_old, which in ((x, y, z_rear, 'rear'), (fxp, fyp, z_front, 'front')):
            # a top just over the wheel that it cannot climb: the wheel runs into a ledge (a kerb, a slab edge)
            ledge = [t_ for t_ in ground.tops(wx, wy) if z_old + climb < t_ <= z_old + max(60.0, 2 * v['radius'])]
            if ledge:
                problems['steps'].append({'at': at, 'dz': round(min(ledge) - z_old, 1),
                                          'message': f'the {which} wheels meet a {min(ledge) - z_old:.0f}-unit '
                                                     'step up they cannot climb'})
        for z_old, z_new, which in ((z_rear, rz, 'rear'), (z_front, fz, 'front')):
            if abs(z_new - z_old) > climb and travelled > REPORT_EVERY:
                problems['steps'].append({'at': at, 'dz': round(z_new - z_old, 1),
                                          'message': f'the {which} wheels meet a {abs(z_new - z_old):.0f}-unit '
                                                     f'{"step up" if z_new > z_old else "drop"}'})
        grade = (fz - rz) / v['wheelbase'] * 100
        problems['grades'].append({'at': at, 'grade': round(grade, 1)})
        z_rear, z_front = rz, fz
        # nose and tail over the ground: the underside follows the line through the axles
        rear_x, front_axle_x = v['rear_axle'], v['front_axle']

        def underside(bx):
            t = (bx - rear_x) / (front_axle_x - rear_x)
            return rz + t * (fz - rz) + v['clearance']

        for bx, which in ((v['front_x'], 'nose'), (v['back_x'], 'tail')):
            d = bx - rear_x
            px, py = x + fx * d, y + fy * d
            gz = ground.under(px, py, underside(bx) - v['clearance'], climb)
            if gz is not None and gz > underside(bx) + 1:
                problems['scrapes'].append({'at': [round(px), round(py)], 'by': round(gz - underside(bx), 1),
                                            'message': f'the {which} touches the ground ({gz - underside(bx):.0f} '
                                                       'units into it) at a change of grade'})
        # the body: a rectangle around the body frame's origin, at three heights
        ox, oy = x - fx * rear_x, y - fy * rear_x
        cx = (v['front_x'] + v['back_x']) / 2
        centre = (ox + fx * cx, oy + fy * cx)
        hx, hy = (v['front_x'] - v['back_x']) / 2, v['half']
        reach = math.hypot(hx, hy) + 128
        base = (rz + fz) / 2
        near = walls.near(centre[0] - reach, centre[1] - reach, centre[0] + reach, centre[1] + reach)
        free, hit = math.inf, None
        for h in (v['clearance'] + 6, v['height'] * 0.5, v['height'] - 6):
            zc = base + h
            for i in near:
                seg = walls.slice(i, zc)
                if seg is None:
                    continue
                d = _seg_rect(seg[0], seg[1], centre, (fx, fy), (lx, ly), hx, hy)
                if d < free:
                    free, hit = d, (walls.labels[i], round(h))
        if hit is not None and free < 1.0:
            problems['hits'].append({'at': at, 'what': hit[0], 'height': hit[1],
                                     'message': f'the body hits {hit[0]} at {hit[1]} units over the road'})
        elif free < math.inf:
            tightest.append({'at': at, 'margin': round(free, 1)})
        for pr in props:
            box2 = _prop_box(pr, base + v['clearance'], base + v['height'])
            if box2 is None:
                continue
            lo, hi = box2
            seg_d = min(_seg_rect((lo[0], lo[1]), (hi[0], lo[1]), centre, (fx, fy), (lx, ly), hx, hy),
                        _seg_rect((hi[0], lo[1]), (hi[0], hi[1]), centre, (fx, fy), (lx, ly), hx, hy),
                        _seg_rect((hi[0], hi[1]), (lo[0], hi[1]), centre, (fx, fy), (lx, ly), hx, hy),
                        _seg_rect((lo[0], hi[1]), (lo[0], lo[1]), centre, (fx, fy), (lx, ly), hx, hy))
            inside = abs((((lo[0] + hi[0]) / 2 - centre[0]) * fx + ((lo[1] + hi[1]) / 2 - centre[1]) * fy)) < hx and \
                abs((((lo[0] + hi[0]) / 2 - centre[0]) * lx + ((lo[1] + hi[1]) / 2 - centre[1]) * ly)) < hy
            if seg_d < 1.0 or inside:
                problems['props'].append({'at': at, 'id': pr['id'], 'model': pr['model'].rsplit('/', 1)[-1],
                                          'message': f'the body touches the prop {pr["model"].rsplit("/", 1)[-1]} '
                                                     f'(id {pr["id"]})'})
        poses.append(at)
        if len(poses) > 4000:
            break

    def first_of(items, key=None, n=8):
        seen, out = set(), []
        for it in items:
            k = key(it) if key else tuple(it['at'])
            if k in seen:
                continue
            seen.add(k)
            out.append(it)
            if len(out) >= n:
                break
        return out

    grades = sorted(problems['grades'], key=lambda g: -abs(g['grade']))[:5]
    hits = first_of(problems['hits'], key=lambda h: (h['what'],))
    found = {'steps': first_of(problems['steps']), 'scrapes': first_of(problems['scrapes']), 'hits': hits,
             'props': first_of(problems['props'], key=lambda p: p['id']), 'turns': problems['turns'][:1]}
    ok = not any(found.values())
    return {'ok': ok, 'vehicle': {k: v[k] for k in ('name', 'wheelbase', 'min_radius', 'height')},
            'driven': round(travelled), 'route_length': round(sum(math.dist(a[:2], b[:2])
                                                                   for a, b in zip(route, route[1:]))),
            'reached_end': not problems['turns'] and progress >= len(path) - 3 and math.dist(end, (x, y)) < 64,
            **{k: v_ for k, v_ in found.items() if v_},
            'steepest': grades, 'tightest': sorted(tightest, key=lambda t: t['margin'])[:5],
            'note': ('offline, on the saved map: the body as a box, wheels on the highest reachable face. '
                     'Confirm a clean result once in the game (route_test with the vehicle\'s route command).')}
