"""Bot navigation cost: how many nav areas a map gets and how long CS2's "Compute AI Data" takes for them.

The compiler makes the nav mesh in two parts. NAVGEN (Recast, then merging and splitting polygons) is fast: under a
minute even for 400 000 input triangles. Then "CSGO Compute AI Data" runs on ONE core; its last stage, "Approach
areas", grows about with the square of the area count and prints nothing until it ends. Measured (logs of 55 builds):

    areas    Approach areas
      800    2-7 s
     2000    15-56 s
    66000    not finished after 3 h 40 min (a 41 000 m2 terrain map, 0.62 m2 per area)

so SECONDS_PER_AREA2 * n^2 is the middle estimate, within a factor of three either way. Areas multiply where
walkable ground is not flat: every terrain triangle is its own plane and is never merged with its neighbours.

The levers (verified in CS2's files, see docs/specs/nav.md): func_nav_markup volumes (navproperty_navgen NONAV:
no nav inside; WALKABLESEED: a seed) as Valve uses them in de_inferno, and nav clips (toolsnavclip) that stop the
flood where player clips do not. NavGen parameters exist only in gameinfo.gi for the whole game; there are no per-map
settings ("Nav settings not found, using default settings").
"""
from __future__ import annotations

import json
import math
import re

from . import checks

WARN_AREAS = 8000            # above this: AI data takes many minutes; say so before the build
STOP_AREAS = 20000           # default limit (settings limits.nav_max_areas): the build stops after NAVGEN
SECONDS_PER_AREA2 = 8e-6
MARKUP_MATERIAL = 'materials/tools/toolsnavattribute.vmat'
NAVCLIP_MATERIAL = 'materials/tools/toolsnavclip.vmat'
MARKUP = {'nonav': 'NONAV', 'nonav_nonsolid': 'NONAVNONSOLID', 'nonav_space': 'NONAVSPACE', 'seed': 'WALKABLESEED'}
PLAY_AREA_KEY = 'cs2map_play_area'
AUTO_KEY = 'cs2map_nav_auto'       # nodes made by set_play_area (replaced when the play area changes)
WALKABLE_NZ = 0.7                  # CS2 stands on surfaces with a normal z of 0.7 and more (about 45 degrees)
# Faces navgen does not build on: tool volumes, clips (they do NOT stop the nav flood), sky, markup.
NOT_GROUND = ('toolsplayerclip', 'toolsclip', 'toolsnavclip', 'toolsnavspaceclip', 'toolstrigger', 'toolsskybox',
              'toolslightmapres', 'postprocess', 'toolsnavattribute', 'toolsmarkup', 'toolsinvisibleladder',
              'toolsnpcclip', 'toolsblocklight', 'toolsfog', 'toolsblockbullets')
ISLAND = re.compile(r'stage 01 / 01: IslandRemoval took [\d.]+ sec, produced (\d+) polys')
NO_SEEDS = 'no walkable seeds present'


def nav_twin(piece):
    """The same mesh in toolsnavclip: player clips do not stop the nav flood, so every player clip gets this twin
    (bots then never plan a path through a clip they cannot pass)."""
    from . import mesh
    return mesh.Mesh().extend(piece).set_material(NAVCLIP_MATERIAL)


def max_areas(s) -> int:
    return int(s.limit('nav_max_areas', STOP_AREAS))


def ai_data_seconds(areas: int) -> tuple[float, float, float]:
    """(low, middle, high) seconds of "CSGO Compute AI Data" for this many nav areas."""
    mid = SECONDS_PER_AREA2 * areas * areas
    return mid / 3, mid, mid * 3


def duration(seconds: float) -> str:
    if seconds < 60:
        return 'under a minute'
    if seconds < 3600:
        return f'{seconds / 60:.0f} min'
    if seconds < 48 * 3600:
        return f'{seconds / 3600:.1f} h'
    return f'{seconds / 86400:.0f} days'


def eta(areas: int) -> str:
    low, mid, high = ai_data_seconds(areas)
    if high < 60:
        return 'under a minute'
    return f'about {duration(mid)} ({duration(low)} to {duration(high)})'


LIMIT_FIX = ('limit the nav to where bots play: map_edit set_play_area (outline, nav "inside" or "routes") or '
             'add_nav_markup kind "nonav" boxes over slopes, lawns, yards, roofs and the background; flatten what '
             'bots walk on (terrain cells make one nav area each); or build without bots (map_build bots=false)')


def verdict(areas: int, limit: int) -> dict:
    """What a nav area count means for the build: level ok | slow | too_slow and one sentence."""
    level = 'too_slow' if areas > limit else 'slow' if areas > min(WARN_AREAS, limit) else 'ok'
    text = f'{areas} nav areas: bot data ("Compute AI Data", one CPU core) takes {eta(areas)}'
    if level == 'slow':
        text += '; bots on this map are expensive: build them last'
    elif level == 'too_slow':
        text += f'; over the limit of {limit} areas (settings limits.nav_max_areas)'
    return {'areas': areas, 'ai_data': eta(areas), 'level': level, 'message': text}


# ---- what the map says ---------------------------------------------------------------------------------------------
def _children(node):
    stack = list(node.get('children', []) or [])
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.get('children', []) or [])


def markups(m) -> dict[str, list]:
    """Boxes of func_nav_markup volumes by tag (NONAV, NONAVNONSOLID, NONAVSPACE, WALKABLESEED)."""
    out: dict[str, list] = {}
    for node in m.iter_nodes():
        if node.type != 'CMapEntity' or m.classname(node) != 'func_nav_markup':
            continue
        props = checks._props(node)
        tags = ' '.join(str(v) for k, v in props.items() if k.lower() == 'navproperty_navgen').upper().split()
        for ch in _children(node):
            if ch.type == 'CMapMesh':
                for b in _piece_boxes(ch):
                    for tag in tags:
                        out.setdefault(tag, []).append(b)
    return out


def _piece_boxes(node) -> list[tuple[tuple, tuple]]:
    """Bounds of every separate piece of a mesh (set_play_area puts up to 1000 boxes into one mesh)."""
    md = node['meshData']
    verts = checks._vertices(node)
    dest, nxt = md['edgeVertexIndices'], md['edgeNextIndices']
    parent = list(range(len(verts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for start in md['faceEdgeIndices']:
        edge, seen, first = start, set(), None
        while edge not in seen and 0 <= edge < len(dest):
            seen.add(edge)
            v = dest[edge]
            if first is None:
                first = v
            else:
                parent[find(v)] = find(first)
            edge = nxt[edge]
    groups: dict[int, list] = {}
    for i, p in enumerate(verts):
        groups.setdefault(find(i), []).append(p)
    return [(tuple(min(p[k] for p in pts) for k in range(3)), tuple(max(p[k] for p in pts) for k in range(3)))
            for pts in groups.values()]


def play_area(m) -> dict | None:
    raw = m.world.get(PLAY_AREA_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get('outline') else None


def in_polygon(x: float, y: float, poly) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def outside_play_area(m, point) -> bool:
    """True when the play area is set and this XY point lies outside its outline (scenery, far background)."""
    area = play_area(m)
    return bool(area) and not in_polygon(point[0], point[1], area['outline'])


class _BoxIndex:
    CELL = 512.0

    def __init__(self, boxes):
        self.boxes = boxes
        self.cells: dict[tuple, list] = {}
        for i, (lo, hi) in enumerate(boxes):
            for gx in range(math.floor(lo[0] / self.CELL), math.floor(hi[0] / self.CELL) + 1):
                for gy in range(math.floor(lo[1] / self.CELL), math.floor(hi[1] / self.CELL) + 1):
                    self.cells.setdefault((gx, gy), []).append(i)

    def contains(self, p) -> bool:
        for i in self.cells.get((math.floor(p[0] / self.CELL), math.floor(p[1] / self.CELL)), ()):
            lo, hi = self.boxes[i]
            if all(lo[k] <= p[k] <= hi[k] for k in range(3)):
                return True
        return False


def estimate(m, limit: int = STOP_AREAS) -> dict:
    """Nav areas this map will get, from its geometry, before any build (props are not counted).

    Walkable faces (normal z >= 0.7, solid, not tools, not inside a NONAV markup): flat planes mostly merge, every
    sloped plane (a terrain triangle, a ramp) becomes about two areas (nav tiles of 128 units cut them and they never
    merge), big flat floors are split about every 100 m2. Fitted on the logged builds (16 maps, 150..2400 areas, and
    a 66 000-area terrain map): right within a factor of three (unreachable roofs make it high, floors cut by many
    walls low); the build's own count after NAVGEN is the truth and the build stops there over the limit."""
    from . import visual
    marks = markups(m)
    seeds = [e for e in m.iter_nodes() if e.type == 'CMapEntity' and m.classname(e) == 'point_nav_walkable']
    if not seeds and not marks.get('WALKABLESEED'):
        return {'areas': 0, 'level': 'none', 'seeds': 0,
                'message': 'no point_nav_walkable and no WALKABLESEED markup: the compiler skips the nav mesh '
                           '(">>NAVGEN: Skipped... no walkable seeds present"); bots will not move'}
    nonav = _BoxIndex(marks.get('NONAV', []) + marks.get('NONAVNONSOLID', []) + marks.get('NONAVSPACE', []))
    area = 0.0
    planes = set()
    rough = 0
    for node in m.iter_nodes():
        if node.type != 'CMapMesh' or node.get('physicsType') == 'none':
            continue
        for pts, mat in visual._faces(node):
            if any(k in mat for k in NOT_GROUND):
                continue
            plane = visual._plane(pts)
            if plane is None or plane[0][2] < WALKABLE_NZ:
                continue
            a = 0.0
            kept = 0.0
            for i in range(1, len(pts) - 1):
                t = (pts[0], pts[i], pts[i + 1])
                u = [t[1][k] - t[0][k] for k in range(3)]
                v = [t[2][k] - t[0][k] for k in range(3)]
                ta = 0.5 * math.sqrt((u[1] * v[2] - u[2] * v[1]) ** 2 + (u[2] * v[0] - u[0] * v[2]) ** 2
                                     + (u[0] * v[1] - u[1] * v[0]) ** 2)
                a += ta
                kept += ta * _free_share(t, nonav) if nonav.boxes else ta
            if kept < a * 0.5:
                continue                      # mostly under NONAV markup
            area += kept
            n, d = plane
            key = (node.get('nodeID'), round(n[0], 2), round(n[1], 2), round(n[2], 2), round(d / 4))
            if key not in planes:
                planes.add(key)
                if n[2] < 0.999:
                    rough += 1
    m2 = area * 0.0254 ** 2
    areas = int(150 + 0.4 * (len(planes) - rough) + 2.0 * rough + m2 / 100)
    out = verdict(areas, limit)
    out.update({'estimated': True, 'walkable_m2': round(m2), 'planes': len(planes), 'sloped_planes': rough,
                'seeds': len(seeds), 'nonav_volumes': len(nonav.boxes)})
    out['message'] = 'estimate from geometry (x0.3 to x3; the build counts exactly): ' + out['message']
    if out['level'] != 'ok':
        out['fix'] = LIMIT_FIX
    return out


def _free_share(tri, nonav: '_BoxIndex') -> float:
    """Share of a triangle outside the NONAV volumes: its centre for small ones, a grid of samples for big ones."""
    a, b, c = tri
    size = max(abs(a[k] - b[k]) + abs(a[k] - c[k]) for k in (0, 1))
    n = 1 if size < 256 else min(24, int(size / 128))
    free = total = 0
    for i in range(n):
        for j in range(n - i):
            wa, wb = (i + 1 / 3) / n, (j + 1 / 3) / n
            wc = 1 - wa - wb
            p = [wa * a[k] + wb * b[k] + wc * c[k] for k in range(3)]
            total += 1
            free += not nonav.contains((p[0], p[1], p[2] + 1))
    return free / total


def from_log(text: str) -> int | None:
    """The nav area count after NAVGEN (before the slow AI data), or None."""
    found = ISLAND.findall(text)
    return int(found[-1]) if found else None
