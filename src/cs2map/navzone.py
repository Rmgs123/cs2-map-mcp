"""Where bots may go: the play area and nav markup (map_edit set_play_area, add_nav_markup; check_map "nav").

func_nav_markup is Valve's own tool (de_inferno has it): a volume with the keyvalue navproperty_navgen = NONAV
removes the nav inside (NAVGEN stage RemovePolysMarkedNoNav), WALKABLESEED seeds it. set_play_area keeps the nav
inside an outline (or along route corridors in it) by covering everything else with NONAV boxes, and remembers the
outline: checks treat what lies outside as background (no phantom floors there), estimates count only the inside.
"""
from __future__ import annotations

import json
import math

from . import checks, mesh, navcost
from .project import OpError

# Measured with the compiler (27.10): a func_nav_markup counts only its FIRST mesh, and a mesh counts as one convex
# volume (one mesh of two boxes also covered the gap between them). So every box is its own entity with one mesh.
CELL_RANGE = (16, 512)


def _points(value, what: str, dims=2, at_least=2) -> list[list[float]]:
    if not isinstance(value, list) or len(value) < at_least:
        raise OpError(f'{what} needs at least {at_least} points [x, y]')
    out = []
    for p in value:
        if not isinstance(p, (list, tuple)) or len(p) < dims or not all(isinstance(c, (int, float)) for c in p[:dims]):
            raise OpError(f'{what}: every point is [x, y] in units, got {p!r}')
        out.append([float(c) for c in p[:dims]])
    return out


def _kind(op) -> str:
    kind = op.get('kind', 'nonav')
    if kind not in navcost.MARKUP:
        raise OpError(f'add_nav_markup kind must be one of {sorted(navcost.MARKUP)}')
    return kind


def _markup_entities(project, m, tag: str, pieces: list, keyvalues=None, auto=False, parent=None):
    """One func_nav_markup per piece (Valve's keyvalue and tool material); returns [(entity, mesh)]."""
    out = []
    for piece in pieces:
        el = m.new_mesh(piece, texture_size=project.texture_size)
        # the FGD key exactly: the FGD default fills navProperty_NavGen, and a second key in another case is ignored
        kv = {'navProperty_NavGen': tag, **(keyvalues or {})}
        ent = m.new_entity('func_nav_markup', kv, list(el['origin']),
                           defaults=project.catalog.defaults('func_nav_markup'))
        m.attach(el, ent)
        if auto:
            ent.set(navcost.AUTO_KEY, 'string', 'set_play_area')
        m.attach(ent, parent)
        out.append((ent, el))
    return out


def add_nav_markup(project, m, op, refs):
    """{"op":"add_nav_markup","kind":"nonav"|"nonav_nonsolid"|"nonav_space"|"seed","box":[[x,y,z],[x,y,z]]} or
    "shape":{...} (any add_mesh shape) or "outline":[[x,y],...] with "z":[z0,z1]."""
    unknown = set(op) - {'op', 'kind', 'box', 'shape', 'outline', 'z', 'ref', 'name'}
    if unknown:
        raise OpError(f'add_nav_markup: unknown fields {sorted(unknown)}')
    tag = navcost.MARKUP[_kind(op)]
    given = [k for k in ('box', 'shape', 'outline') if op.get(k) is not None]
    if len(given) != 1:
        raise OpError('add_nav_markup needs exactly one of "box", "shape" or "outline" (+ "z")')
    if op.get('box') is not None:
        lo, hi = (_points(op['box'], 'box', 3, 2) + [[0, 0, 0]])[:2]
        pieces = [mesh.box([min(a, b) for a, b in zip(lo, hi)], [max(a, b) for a, b in zip(lo, hi)],
                           navcost.MARKUP_MATERIAL)]
    elif op.get('shape') is not None:
        from . import shapes
        pieces = [p.set_material(navcost.MARKUP_MATERIAL) for p in shapes.build(op['shape'], navcost.MARKUP_MATERIAL)]
    else:
        poly = _points(op['outline'], 'outline', 2, 3)
        if not _convex(poly):
            raise OpError('an outline markup must be convex (the compiler takes each markup mesh as one convex volume): '
                          'split it into convex outlines, one add_nav_markup each, or use set_play_area')
        z = op.get('z')
        if not (isinstance(z, list) and len(z) == 2 and z[1] > z[0]):
            raise OpError('an outline markup needs "z": [bottom, top]')
        pieces = [mesh.prism([tuple(p) for p in _ccw(poly)], float(z[0]), float(z[1]), 'z', navcost.MARKUP_MATERIAL)]
    kv = {'targetname': op['name']} if op.get('name') else {}
    made = _markup_entities(project, m, tag, pieces, kv)
    if op.get('ref'):
        refs[op['ref']] = made[0][0]['nodeID']
    return {'op': 'add_nav_markup', 'id': made[0][0]['nodeID'], 'kind': op.get('kind', 'nonav'), 'tag': tag,
            'ids': [e['nodeID'] for e, _ in made], **({'ref': op['ref']} if op.get('ref') else {})}


def _convex(poly) -> bool:
    signs = set()
    for a, b, c in zip(poly, poly[1:] + poly[:1], poly[2:] + poly[:2]):
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) > 1e-6:
            signs.add(cross > 0)
    return len(signs) == 1


def _ccw(poly):
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))
    return poly if area > 0 else list(reversed(poly))


def _rectangles(blocked: list[list[bool]]) -> list[tuple[int, int, int, int]]:
    """Cover the True cells with few rectangles (i0, j0, i1, j1 inclusive): row runs, grown down while identical."""
    rows, cols = len(blocked), len(blocked[0]) if blocked else 0
    used = [[False] * cols for _ in range(rows)]
    out = []
    for i in range(rows):
        j = 0
        while j < cols:
            if not blocked[i][j] or used[i][j]:
                j += 1
                continue
            j1 = j
            while j1 + 1 < cols and blocked[i][j1 + 1] and not used[i][j1 + 1]:
                j1 += 1
            i1 = i
            while i1 + 1 < rows and all(blocked[i1 + 1][k] and not used[i1 + 1][k] for k in range(j, j1 + 1)):
                i1 += 1
            for a in range(i, i1 + 1):
                for k in range(j, j1 + 1):
                    used[a][k] = True
            out.append((i, j, i1, j1))
            j = j1 + 1
    return out


def _world_box(m):
    b = checks._map_bounds([(n, bb) for n, bb in checks._all_meshes(m)
                            if not all('toolsnavattribute' in str(x) for x in n['meshData'].get('materials', []))])
    if not b:
        raise OpError('set_play_area needs geometry in the map first (it covers the rest of the world with NONAV)')
    return b


def set_play_area(project, m, op, refs):
    """{"op":"set_play_area","outline":[[x,y],...],"nav":"inside"|"routes"|"all","routes":[{"points":[[x,y],...],
    "width":192}],"cell":64,"z":[z0,z1]}: remember the play area; nav "inside" covers the world outside it with
    NONAV markup, "routes" also everything farther than width/2 from the routes, "all" adds no markup.
    Running it again replaces the earlier play area and its markup."""
    unknown = set(op) - {'op', 'outline', 'nav', 'routes', 'cell', 'z', 'margin', 'paved'}
    if unknown:
        raise OpError(f'set_play_area: unknown fields {sorted(unknown)}')
    poly = _points(op.get('outline'), 'outline', 2, 3)
    nav = op.get('nav', 'inside')
    if nav not in ('inside', 'routes', 'all'):
        raise OpError('nav must be "inside", "routes" or "all"')
    paved = op.get('paved', False)
    if not isinstance(paved, bool):
        raise OpError('paved is true (nav only on paved ground: roads, sidewalks, floors) or false')
    routes = []
    if nav == 'routes':
        if not isinstance(op.get('routes'), list) or not op['routes']:
            raise OpError('nav "routes" needs "routes": [{"points": [[x,y],...], "width": 192}, ...]')
        for r in op['routes']:
            if not isinstance(r, dict):
                raise OpError('every route is {"points": [[x,y],...], "width": units}')
            width = r.get('width', 192)
            if not isinstance(width, (int, float)) or not 48 <= width <= 4096:
                raise OpError('route width must be 48..4096 units (a player is 32 wide)')
            routes.append({'points': _points(r.get('points'), 'route points', 2, 2), 'width': float(width)})
    cell = op.get('cell', 64)
    if not isinstance(cell, (int, float)) or not CELL_RANGE[0] <= cell <= CELL_RANGE[1]:
        raise OpError(f'cell must be {CELL_RANGE[0]}..{CELL_RANGE[1]} units (smaller follows the outline closer)')
    margin = float(op.get('margin', 0))
    removed = []
    stack = [m.world]
    while stack:                              # top-most markup nodes of an earlier play area (a group goes whole)
        parent = stack.pop()
        keep = []
        for child in parent.get('children', []):
            if child.get(navcost.AUTO_KEY):
                removed += [n['nodeID'] for n in [child, *m.iter_nodes(child)] if n.type == 'CMapEntity']
            else:
                keep.append(child)
                stack.append(child)
        if len(keep) != len(parent.get('children', [])):
            parent['children'] = keep
    m.world.set(navcost.PLAY_AREA_KEY, 'string', json.dumps({'outline': poly, 'nav': nav, 'routes': routes,
                                                              'cell': cell, 'paved': paved}))
    out = {'op': 'set_play_area', 'nav': nav, 'outline_points': len(poly), 'replaced_markup': len(removed)}
    if nav == 'all':
        return out
    (wx0, wy0, wz0), (wx1, wy1, wz1) = _world_box(m)
    z = op.get('z') or [wz0 - 64, wz1 + 64]
    if not (isinstance(z, list) and len(z) == 2 and z[1] > z[0]):
        raise OpError('"z" is [bottom, top] of the NONAV volumes')
    x0, y0 = min(wx0, *(p[0] for p in poly)) - cell, min(wy0, *(p[1] for p in poly)) - cell
    nx = math.ceil((max(wx1, *(p[0] for p in poly)) + cell - x0) / cell)
    ny = math.ceil((max(wy1, *(p[1] for p in poly)) + cell - y0) / cell)
    if nx * ny > 4_000_000:
        raise OpError(f'the world is {nx} x {ny} cells of {cell} units: use a larger cell')

    import numpy as np
    xs = x0 + (np.arange(nx) + 0.5) * cell
    ys = y0 + (np.arange(ny) + 0.5) * cell
    gx, gy = np.meshgrid(xs, ys)
    # cells on the outline count as inside when any corner is inside: the nav reaches the edge
    inside = _inside(gx, gy, poly)
    for dx in (-cell / 2, cell / 2):
        for dy in (-cell / 2, cell / 2):
            inside |= _inside(gx + dx, gy + dy, poly)
    if margin:
        inside &= _distance(gx, gy, poly + poly[:1]) >= margin
    if routes:
        near = np.zeros_like(inside)
        for r in routes:
            near |= _distance(gx, gy, r['points']) <= r['width'] / 2 + cell * 0.5
        inside &= near
    if paved:
        inside &= _paved_cells(m, x0, y0, cell, nx, ny)
    if routes or paved:
        inside |= _anchor_cells(m, x0, y0, cell, nx, ny)   # spawns and seeds always keep nav around them
    blocked = (~inside).tolist()
    rects = _rectangles(blocked)
    # Each box grows by half a unit: neighbours overlap, so no strip between two markup volumes keeps nav.
    boxes = [mesh.box([x0 + j0 * cell - .5, y0 + i0 * cell - .5, z[0]], [x0 + (j1 + 1) * cell + .5,
                      y0 + (i1 + 1) * cell + .5, z[1]], navcost.MARKUP_MATERIAL) for i0, j0, i1, j1 in rects]
    ids = []
    # all in one group, so the outliner (and a move or delete) handles the play area's markup as one thing
    group = m.new_group('play_area_nonav')
    group.set(navcost.AUTO_KEY, 'string', 'set_play_area')
    m.attach(group)
    made = _markup_entities(project, m, 'NONAV', boxes, {'targetname': 'mcp_play_area_nonav'}, auto=True,
                            parent=group)
    ids = [e['nodeID'] for e, _ in made]
    kept = sum(1 for row in blocked for b in row if not b) * (cell * 0.0254) ** 2
    out.update({'nonav_boxes': len(boxes), 'ids': ids, 'nav_m2': round(kept), 'cell': cell})
    return out


# Ground bots walk on, by face material; natural ground (grass, dirt, a terrain's slopes) is left without nav.
PAVED = ('asphalt', 'sidewalk', 'cobble', 'gravel', 'pav', 'concrete', 'conc_', 'tile', 'stone', 'road', 'street',
         'plaza', 'brick_floor', 'floor', 'ruts', 'metal', 'wood', 'planks', 'stairs', 'step')
NATURAL = ('grass', 'dirt', 'mud', 'sand', 'forest', 'soil', 'moss', 'leaves', 'roof', 'tools', 'water')


def _paved(material: str) -> bool:
    return any(h in material for h in PAVED) and not any(h in material for h in NATURAL)


def _paved_cells(m, x0, y0, cell, nx, ny):
    """Cells (ny x nx) holding solid walkable faces of paved materials, grown by one cell (kerbs, doorways)."""
    import numpy as np
    from . import visual
    out = np.zeros((ny, nx), dtype=bool)
    for node in m.iter_nodes():
        if node.type != 'CMapMesh' or node.get('physicsType') == 'none':
            continue
        for pts, mat in visual._faces(node):
            if not _paved(mat):
                continue
            plane = visual._plane(pts)
            if plane is None or plane[0][2] < navcost.WALKABLE_NZ:
                continue
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            j0, j1 = int((min(xs) - x0) // cell), int((max(xs) - x0) // cell)
            i0, i1 = int((min(ys) - y0) // cell), int((max(ys) - y0) // cell)
            j0, i0, j1, i1 = max(j0, 0), max(i0, 0), min(j1, nx - 1), min(i1, ny - 1)
            if j1 < j0 or i1 < i0:
                continue
            if (j1 - j0 + 1) * (i1 - i0 + 1) <= 4:
                out[i0:i1 + 1, j0:j1 + 1] = True
                continue
            cx = x0 + (np.arange(j0, j1 + 1) + 0.5) * cell
            cy = y0 + (np.arange(i0, i1 + 1) + 0.5) * cell
            gx, gy = np.meshgrid(cx, cy)
            hit = np.zeros(gx.shape, dtype=bool)
            for dx in (-cell / 2, 0, cell / 2):                 # a cell the face touches, not only its centre
                for dy in (-cell / 2, 0, cell / 2):
                    hit |= _inside(gx + dx, gy + dy, [p[:2] for p in pts])
            out[i0:i1 + 1, j0:j1 + 1] |= hit
    grown = out.copy()
    grown[1:, :] |= out[:-1, :]
    grown[:-1, :] |= out[1:, :]
    grown[:, 1:] |= out[:, :-1]
    grown[:, :-1] |= out[:, 1:]
    return grown


def _anchor_cells(m, x0, y0, cell, nx, ny):
    """Cells within about 2.5 cells of spawns and nav seeds (bots start there and must reach the routes)."""
    import numpy as np
    out = np.zeros((ny, nx), dtype=bool)
    for e in m.iter_nodes():
        if e.type == 'CMapEntity' and m.classname(e) in ('point_nav_walkable', 'info_player_terrorist',
                                                         'info_player_counterterrorist'):
            o = e['origin']
            j, i = int((o[0] - x0) // cell), int((o[1] - y0) // cell)
            out[max(i - 2, 0):max(i + 3, 0), max(j - 2, 0):max(j + 3, 0)] = True
    return out


def _inside(gx, gy, poly):
    """Point in polygon (even-odd) for arrays of points."""
    import numpy as np
    out = np.zeros(gx.shape, dtype=bool)
    for (xi, yi), (xj, yj) in zip(poly, poly[-1:] + poly[:-1]):
        if yi == yj:
            continue
        cross = (yi > gy) != (yj > gy)
        out ^= cross & (gx < (xj - xi) * (gy - yi) / (yj - yi) + xi)
    return out


def _distance(gx, gy, line):
    """Distance from arrays of points to a polyline."""
    import numpy as np
    best = np.full(gx.shape, np.inf)
    for (ax, ay), (bx, by) in zip(line, line[1:]):
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        t = np.zeros(gx.shape) if length2 == 0 else np.clip(((gx - ax) * dx + (gy - ay) * dy) / length2, 0, 1)
        best = np.minimum(best, np.hypot(gx - ax - t * dx, gy - ay - t * dy))
    return best


def check_nav(project, m) -> list[dict]:
    """check_map: the nav outlook (areas, bot data time), seeds outside the nav, a big map without limits."""
    est = navcost.estimate(m, navcost.max_areas(project.s))
    problems = []
    if est['level'] in ('slow', 'too_slow'):
        problems.append(checks.problem('warning' if est['level'] == 'slow' else 'error', 'nav_too_big', None,
                                       est['message'], navcost.LIMIT_FIX + '; build bots as the last step'))
    marks = navcost.markups(m)
    nonav = navcost._BoxIndex(marks.get('NONAV', []) + marks.get('NONAVNONSOLID', []))
    for e in m.iter_nodes():
        if e.type == 'CMapEntity' and m.classname(e) == 'point_nav_walkable':
            o = checks._vec(e.get('origin'))
            if nonav.boxes and nonav.contains((o[0], o[1], o[2])):
                problems.append(checks.problem('warning', 'nav_seed_in_nonav', e.get('nodeID'),
                                               f'point_nav_walkable at {[round(c) for c in o]} is inside a NONAV '
                                               'markup: it seeds nothing', 'Move it into the play area.'))
    if est['level'] not in ('none', 'ok') and navcost.play_area(m) is None and not nonav.boxes:
        problems.append(checks.problem('warning', 'nav_unbounded', None,
                                       'no play area and no NONAV markup: the nav floods every walkable surface '
                                       'reachable from the seeds (player clips do not stop it)',
                                       'map_edit set_play_area with the outline players can reach.'))
    return problems
