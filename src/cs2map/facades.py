"""add_facade: dress an existing wall face (a layout wall or a plain wall) as a row of house fronts.

Inferno-style streets are corridors whose walls are house fronts. The street side of the wall
stays where the layout put it, so routes and timings do not change.

Where the space behind the wall is free (nothing but void up to `depth`), every house becomes a
real block: the layout wall is replaced by a solid front slab with niches (windows, doors and
shop fronts set into the wall), a solid mass behind it and a tiled gable roof with an eave over
the street and chimneys; neighbours differ in floors and cornice height. Where the wall is shared
with another room, the house is a thin plaster skin with wall-mounted details (no niches).
"""
from __future__ import annotations

import json
import zlib
import math
import random
import re
from types import SimpleNamespace

from . import buildings, checks, layout, mesh, structure
from .project import OpError
from .dressing import number, vector

_FACADE = 'materials/de_inferno/plaster/inferno_plaster_facade_01_'
# de_inferno paints one plaster (facade_01) with mesh tintColor: these are the
# tints its facade meshes use most (read from the shipped map, 04.10.2026).
PALETTES = {
    'building_inferno': [{'material': _FACADE + 'dirty.vmat', 'tint': [243, 219, 179]},
                         {'material': _FACADE + 'basic.vmat', 'tint': [213, 164, 92]},
                         {'material': _FACADE + 'dirty.vmat', 'tint': [187, 79, 52]},
                         {'material': _FACADE + 'basic.vmat', 'tint': [254, 235, 204]},
                         {'material': _FACADE + 'dirty.vmat', 'tint': [206, 144, 79]},
                         {'material': _FACADE + 'dirty.vmat', 'tint': [245, 196, 100]},
                         {'material': _FACADE + 'basic.vmat', 'tint': [190, 86, 86]},
                         {'material': _FACADE + 'dirty.vmat', 'tint': [254, 254, 254]},
                         {'material': _FACADE + 'basic.vmat', 'tint': [225, 172, 108]},
                         {'material': _FACADE + 'dirty.vmat', 'tint': [182, 101, 85]}],
    'building_mirage': ['materials/de_dust/hr_dust/hr_dust_plaster_03.vmat',
                        'materials/shared_base/stucco/stucco_003.vmat'],
}
WINDOWS = {'building_inferno': ['window_shutters_green', 'window_shutters_blue', 'window_stone_40',
                                'window_shutters_red', 'window_italy_01', 'window_shutters_yellow'],
           'building_mirage': ['window_88x80']}
KEYS = ('op name on from to normal wall_height thickness floors floor_height house_width houses style materials '
        'plinth bands cornice eave windows doors balconies awnings lamps gutters seed ref depth roof shops arcade loggia '
        'roof_details')
SKIN = 1.25          # plaster skin in front of a shared wall (players cannot notice it)
EPS = 0.25           # gap between stacked surfaces: never coplanar
GAP = 1.0            # a model set into a niche stays this far off its back (glass with curtains flickered)
CORNER = 24.0        # a row along Y stops its cornice/lean-to this far from a room corner (eave 20 + 4)
FRONT = 24           # front slab of a deep house: niches are cut into it
MIN_DEPTH = 64       # a house needs this much free space behind its face to get a real body
ROOF_MAT = 'models/de_inferno/roof/roof_shingles_01/roofkit01_shingles_01.vmat'   # Inferno terracotta
EAVE_MAT = 'materials/de_inferno/wood/inferno_wood_floor_01.vmat'
STONE_MAT = 'materials/de_inferno/stone/inferno_stone_wall_09.vmat'     # arcade piers and arch rings
GALLERY_FLOOR = 'materials/de_inferno/ground/inferno_tile_floor_03.vmat'
CEILING_MAT = 'materials/de_inferno/plaster/inferno_plaster_ceiling_01.vmat'
DARK_MAT = 'materials/dev/reflectivity_10.vmat'

# Stock pieces of deep houses: (model, front). Fronts were checked with detail previews.
P = {
    'glass': ('models/de_inferno/bk01/bk01_w01_glass01_a.vmdl', '+x'),            # 44x80 window, curtains
    'frame': ('models/de_inferno/bk01/bk01_w01_frame04_a.vmdl', '+x'),            # stone surround + sill
    'french': ('models/cs_italy/italy_windowkit_01/italy_windowkit01_window01_a.vmdl', '+x'),   # 38x84
    'shutter_l': ('models/cs_italy/italy_windowkit_01/italy_windowkit01_shutterleft_a.vmdl', '+x'),
    'shutter_r': ('models/cs_italy/italy_windowkit_01/italy_windowkit01_shutterright_a.vmdl', '+x'),
    'closed': ('models/props/de_inferno/hr_i/inferno_folding_shutters/inferno_folding_shutters.vmdl', '+x'),
    'hood': ('models/props/de_inferno/hr_i/inferno_window_overhang/inferno_window_overhang_01.vmdl', '+x'),
    'balcony_door': ('models/props/de_inferno/hr_i/inverno_balcony_door/inferno_balcony_door_01.vmdl', '+x'),
    'door_shop': ('models/props/de_inferno/hr_i/store_front/store_front_door.vmdl', '+x'),        # 58x128
    'door_wood': ('models/props/de_inferno/hr_i/door_c/door_c.vmdl', '+x'),                       # 46x128
    'shop_128': ('models/de_inferno/inferno_shop_front_01/inferno_shop_front_128_160_01.vmdl', '+x'),
    'shop_128_glass': ('models/de_inferno/inferno_shop_front_01/inferno_shop_front_128_160_glass_01.vmdl', '+x'),
    'shop_152': ('models/de_inferno/inferno_shop_front_01/inferno_shop_front_152_160_01.vmdl', '+x'),
    'shop_152_glass': ('models/de_inferno/inferno_shop_front_01/inferno_shop_front_152_160_glass_01.vmdl', '+x'),
    'sign_monaco': ('models/de_inferno/inferno_signs_01/inferno_signs_monaco_01.vmdl', '+x'),
    'sign_gelateria': ('models/de_inferno/inferno_signs_01/inferno_signs_shop_gelateria_01.vmdl', '+x'),
    'awning': ('models/cs_italy/props/awning/awning_cloth_128_01a.vmdl', '+x'),
    'lamp': ('models/props/de_inferno/hr_i/ornate_lamp/ornate_lamp.vmdl', '+x'),
    'balcony': ('models/cs_italy/props/balcony/italy_balcony_128_01.vmdl', '+x'),
    'pipe': ('models/generic/gutter_kit_01/gk01_pipe_01_128_a.vmdl', '+x'),
    'chimney_pot': ('models/props/de_inferno/hr_i/inferno_chimney/inferno_chimney_01.vmdl', '+x'),
    'chimney_top': ('models/generic/chimney_kit_01/chimney_01a.vmdl', '+x'),                 # terracotta pot 32x32x46
    'antenna': ('models/props/de_dust/du_antenna_a.vmdl', '+x'),                             # TV antenna on a mast
}
SHUTTER_WINDOWS = ['window_shutters_green', 'window_shutters_blue', 'window_shutters_red', 'window_shutters_yellow']
_BOUNDS = {}


def _model_bounds(project, model):
    """Local render bounds of a stock model (atlas index, else the compiled model)."""
    if model not in _BOUNDS:
        lo = hi = None
        try:
            from . import atlas
            info = atlas.measure(project, [model]).get(model) or {}
            lo, hi = info.get('lo'), info.get('hi')
        except Exception:
            pass
        if not lo or not hi:
            from . import visual
            got = visual.model_bounds(project, model)
            if not got:
                raise OpError(f'cannot measure {model}')
            lo, hi = got[0], got[1]
        _BOUNDS[model] = (tuple(lo), tuple(hi))
    return _BOUNDS[model]


def _face_from_layout(m, spec):
    buildings._keys(spec, 'room layout side from to', 'facade.on')
    room = layout._room_ref(m, spec)
    side = spec.get('side')
    if side not in buildings.SIDES:
        raise OpError('facade.on.side must be -x, +x, -y or +y (the room wall to dress)')
    lo, hi = room['interior']['mins'], room['interior']['maxs']
    axis = 0 if side[1] == 'x' else 1
    other = 1 - axis
    plane = hi[axis] if side[0] == '+' else lo[axis]
    inward = [0, 0, 0]
    inward[axis] = -1 if side[0] == '+' else 1
    u0 = number(spec.get('from', 0), 'facade.on.from', 0, 1)
    u1 = number(spec.get('to', 1), 'facade.on.to', 0, 1)
    if u1 - u0 < 0.05:
        raise OpError('facade.on from/to must span at least 5% of the wall')
    a = lo[other] + u0 * (hi[other] - lo[other])
    b = lo[other] + u1 * (hi[other] - lo[other])
    thickness = None
    for data in layout.plans(m):
        if any(r is room or r.get('id') == room.get('id') for r in data['rooms'].values()):
            thickness = data['wall']
    return {'axis': axis, 'plane': plane, 'normal': tuple(inward), 'start': min(a, b), 'end': max(a, b),
            'z0': lo[2], 'wall_top': hi[2], 'thickness': thickness or 16, 'ceiling': bool(room.get('ceiling'))}


def _face_explicit(op):
    a, b = vector(op.get('from'), 'facade.from'), vector(op.get('to'), 'facade.to')
    n = vector(op.get('normal'), 'facade.normal')
    if abs(a[2] - b[2]) > 1e-3:
        raise OpError('facade from/to must be the bottom edge at one height')
    normal = tuple(round(x) for x in n)
    if normal not in buildings.SIDES.values():
        raise OpError('facade.normal must be a cardinal axis pointing out of the wall, e.g. [0,-1,0]')
    axis = 0 if normal[0] else 1
    other = 1 - axis
    if abs(a[axis] - b[axis]) > 1e-3:
        raise OpError('facade from/to must lie on one wall plane (same coordinate along the normal)')
    height = number(op.get('wall_height', 0), 'facade.wall_height', 0, 4096) if 'wall_height' in op else 0
    return {'axis': axis, 'plane': a[axis], 'normal': normal, 'start': min(a[other], b[other]),
            'end': max(a[other], b[other]), 'z0': a[2], 'wall_top': a[2] + height,
            'thickness': number(op.get('thickness', 16), 'facade.thickness', 4, 128)}


def _openings(m, face):
    """Layout openings that cut this face: [(u0, u1, z0, z1)] along the face."""
    axis, other = face['axis'], 1 - face['axis']
    stairs = []             # add_paving stairs climb to a raised opening: the wall under it is hidden
    for node in m.iter_nodes():
        raw = node.get('cs2map_paving')
        if raw:
            try:
                stairs += [(s['mins'], s['maxs']) for s in json.loads(raw).get('steps', [])]
            except (ValueError, TypeError, KeyError):
                pass
    out = []
    for data in layout.plans(m):
        for h in data['openings']:
            lo, hi = h['bounds']['mins'], h['bounds']['maxs']
            if lo[axis] - 1 <= face['plane'] <= hi[axis] + 1 and hi[other] > face['start'] and lo[other] < face['end']:
                bottom = min([lo[2]] + [a[2] for a, b in stairs if a[axis] - 1 <= face['plane'] <= b[axis] + 1
                                        and a[other] < hi[other] and b[other] > lo[other]])
                out.append((max(lo[other], face['start']), min(hi[other], face['end']), bottom, hi[2]))
    return out


def _paint(project, entry):
    """Palette entry -> (material, tint or None); tint is an RGB list like de_inferno's mesh tintColor."""
    if isinstance(entry, dict):
        if set(entry) - {'material', 'tint'} or 'material' not in entry:
            raise OpError('materials entries are paths or {"material": path, "tint": [r, g, b]}')
        tint = entry.get('tint')
        if tint is not None:
            tint = [int(v) for v in vector(tint, 'materials.tint')]
            if any(not 0 <= v <= 255 for v in tint):
                raise OpError('materials.tint channels must be 0..255')
        return buildings._material(project, entry['material']), tint
    return buildings._material(project, entry), None


def _tint(m, group, material, tint):
    """Tint the house's plaster meshes (and only them) like de_inferno does."""
    if not tint:
        return
    for node in m.iter_nodes(group):
        if node.type != 'CMapMesh':
            continue
        mats = {str(x) for x in node['meshData'].get('materials', [])}
        if material in mats and all(x == material or 'plaster' in x or 'dev/' in x for x in mats):
            node.set('tintColor', 'color', (*tint, 255))


class _Row:
    """One facade row: geometry helpers in face coordinates (u along the wall, d out of it)."""

    def __init__(self, project, m, face, group):
        self.p, self.m, self.face, self.group = project, m, face, group
        self.axis = face['axis']
        self.other = 1 - self.axis
        self.plane = face['plane']
        self.normal = face['normal']
        self.out = self.normal[self.axis]
        self.props = []

    def at(self, u, z, d=0.0):
        p = [0.0, 0.0, z]
        p[self.axis] = self.plane + self.out * d
        p[self.other] = u
        return p

    def node(self, piece, parent, render_only=False):
        node = self.m.new_mesh(piece, texture_size=self.p.texture_size,
                               options={'physicsType': 'none'} if render_only else None)
        self.m.attach(node, parent)
        return node

    def box(self, u0, u1, z0, z1, d0, d1, material, parent, render_only=True, materials=None):
        a, b = self.at(u0, z0, d0), self.at(u1, z1, d1)
        lo = [min(a[i], b[i]) for i in range(3)]
        hi = [max(a[i], b[i]) for i in range(3)]
        return self.node(mesh.box(lo, hi, material, materials), parent, render_only)

    def slab(self, rects, d0, d1, material, parent, render_only=True):
        if not rects:
            return None
        span = tuple(sorted((self.plane + self.out * d0, self.plane + self.out * d1)))
        return self.node(mesh.region_solid(rects, span, self.axis == 1, material), parent, render_only)

    def panel(self, u0, u1, z0, z1, d_back, d_face, face_material, side_material, parent, render_only=False):
        """Box between depths d_back < d_face whose street-facing face has its own material."""
        lo_c, hi_c = sorted((self.plane + self.out * d_back, self.plane + self.out * d_face))
        if self.axis == 0:
            piece = mesh.prism([(u0, z0), (u1, z0), (u1, z1), (u0, z1)], lo_c, hi_c, 'x', side_material,
                               {('end' if self.out > 0 else 'start'): face_material})
        else:
            piece = mesh.prism([(z0, u0), (z0, u1), (z1, u1), (z1, u0)], lo_c, hi_c, 'y', side_material,
                               {('end' if self.out > 0 else 'start'): face_material})
        return self.node(piece, parent, render_only)

    def profile(self, points, u0, u1, material, parent, render_only=False):
        """Solid from an outline in (d, z) extruded along the wall from u0 to u1."""
        prof = [(self.plane + self.out * d, z) for d, z in points]
        return mesh.profile_solid(prof, (u0, u1), self.axis == 0, material)

    def put(self, key, u, zb, d_rear, parent, scale=1.0, fade=(4200, 5200), model=None, front=None):
        """Stock model facing the street: rear at depth d_rear, centred at u, render bottom at zb."""
        model, front = (model, front) if model else P[key]
        lo, hi = _model_bounds(self.p, model)
        yaw = math.degrees(math.atan2(self.normal[1], self.normal[0])) - buildings.FRONT_YAW[front]
        corners = [structure._rotate((x * scale, y * scale, z * scale), (0, yaw, 0))
                   for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        rear = min(c[self.axis] * self.out for c in corners)
        cu = (min(c[self.other] for c in corners) + max(c[self.other] for c in corners)) / 2
        origin = [0.0, 0.0, zb - lo[2] * scale]
        origin[self.axis] = self.plane + self.out * (d_rear - rear)
        origin[self.other] = u - cu
        kv = {'model': model, 'solid': '0', 'fademindist': str(fade[0]), 'fademaxdist': str(fade[1])}
        ent = self.m.new_entity('prop_static', kv, origin, (0.0, yaw, 0.0), (scale,) * 3,
                                defaults=self.p.catalog.defaults('prop_static'))
        self.m.attach(ent, parent)
        self.props.append(ent['nodeID'])
        return ent

    def pipe_taken(self, u, radius=20.0):
        """A downpipe of another row already runs down this corner (two rows meet there)."""
        p = self.at(u, 0.0, 0.5)
        for ent in self.m.entities('prop_static'):
            o = ent.get('origin') or (0, 0, 0)
            if 'gutter_kit' in str(ent['entity_properties'].get('model', ''))                     and abs(o[0] - p[0]) < radius and abs(o[1] - p[1]) < radius:
                return True
        return False

    def inset(self, inner, host):
        """Mark a model set into another on purpose (glass in its frame): visual_check skips the pair."""
        inner.set('cs2map_inset', 'string', str(host['nodeID']))

    def size(self, key):
        lo, hi = _model_bounds(self.p, P[key][0])
        return [hi[i] - lo[i] for i in range(3)]


def _free_depth(m, row, u0, u1, z_lo, z_hi, want, wall):
    """Free distance behind the face (from the face plane) over u0..u1, up to `want`."""
    a, o, plane, out = row.axis, row.other, row.plane, row.out
    best = want

    def behind(lo, hi, margin):
        nonlocal best
        if hi[o] <= u0 + 0.5 or lo[o] >= u1 - 0.5 or hi[2] <= z_lo or lo[2] >= z_hi:
            return
        near = (plane - hi[a]) if out > 0 else (lo[a] - plane)       # distance behind the face
        far = (plane - lo[a]) if out > 0 else (hi[a] - plane)
        if far <= wall + 0.5:
            return              # in front of the face or inside this wall
        best = min(best, max(0.0, near) - margin)
    for data in layout.plans(m):
        for r in data['rooms'].values():
            behind(r['interior']['mins'], r['interior']['maxs'], 16)
    for node in m.iter_nodes():
        if node.type != 'CMapMesh' or node.get('physicsType') == 'none' or not checks._is_solid_mesh(node):
            continue
        b = checks._mesh_bounds(node)
        if b:
            behind(b[0], b[1], 0)
    return best


def _back_top(m, row, u0, u1, depth, wall, zf):
    """Top of the ground-standing solid right behind a house body (another room's wall), or None."""
    a, o, plane, out = row.axis, row.other, row.plane, row.out
    best = None
    for node in m.iter_nodes():
        if node.type != 'CMapMesh' or node.get('physicsType') == 'none' or not checks._is_solid_mesh(node):
            continue
        b = checks._mesh_bounds(node)
        if not b or b[1][o] <= u0 + 0.5 or b[0][o] >= u1 - 0.5 or b[0][2] > zf + 64:
            continue
        near = (plane - b[1][a]) if out > 0 else (b[0][a] - plane)
        if depth - 4 <= near <= depth + 40:
            best = max(best or b[1][2], b[1][2])
    return best


def _clear_wall(m, row, u0, u1, z_from, wall):
    """Remove the layout wall volume behind the face over u0..u1 (the house front replaces it)."""
    a, o = row.axis, row.other
    cut = {'mins': [0.0, 0.0, z_from], 'maxs': [0.0, 0.0, 1e6]}
    cut['mins'][a], cut['maxs'][a] = sorted((row.plane, row.plane - row.out * wall))
    cut['mins'][o], cut['maxs'][o] = u0, u1
    for data in layout.plans(m):
        group = m.node(data['id'])
        for node in list(group.get('children', [])):
            if node.type != 'CMapMesh' or len(checks._vertices(node)) != 8:
                continue
            b = checks._mesh_bounds(node)
            piece = {'mins': list(b[0]), 'maxs': list(b[1])}
            if not layout._overlap(piece, cut, 0.01):
                continue
            mats = [str(x) for x in node['meshData'].get('materials', [])]
            m.remove(node)
            for rest in layout._subtract(piece, cut):
                if min(rest['maxs'][i] - rest['mins'][i] for i in range(3)) < 0.05:
                    continue          # rounding slivers
                new = m.new_mesh(mesh.box(rest['mins'], rest['maxs'], mats[0] if mats else mesh.DEFAULT_MATERIAL),
                                 texture_size=row.p.texture_size)
                m.attach(new, group)


def _roof_uv(piece, along):
    """Tile rows along the eave: u along the wall, v down the slope (world UVs would turn them)."""
    for f in piece.faces:
        n = piece.normal(f)
        if abs(n[2]) < 0.3:
            continue
        v = structure._cross(n, along)
        length = math.sqrt(sum(x * x for x in v)) or 1.0
        f.u_axis, f.v_axis = tuple(along), tuple(x / length for x in v)


def _pick(spec, index, rng, label):
    """Does house `index` get the feature? spec: true/"all", [indices] or a share 0..1 (seeded)."""
    houses = spec.get('houses', 'all') if isinstance(spec, dict) else spec
    if houses is True or houses == 'all':
        return True
    if isinstance(houses, list):
        return index in houses
    return rng.random() < number(houses, label, 0, 1)


def _feature(op, key, keys, label):
    spec = op.get(key, False)
    if spec is False or spec is None:
        return None
    if spec is True or spec == 'all' or isinstance(spec, (list, int, float)):
        return {'houses': spec}
    buildings._keys(spec, keys, label)
    return spec


def add_facade(project, m, op, refs):
    buildings._keys(op, KEYS, 'facade')
    name = op.get('name')
    if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9_]{1,48}', name):
        raise OpError('facade.name must be 1..48 chars a-z0-9_')
    if any(n.name == 'facade_row:' + name for n in m.iter_nodes()):
        raise OpError(f'facade {name} already exists')
    # 'on' (not 'wall'): layout points {room, wall} are resolved to coordinates before operations run.
    face = _face_from_layout(m, op['on']) if 'on' in op else _face_explicit(op)
    data = buildings.kit(op.get('style', 'building_inferno'))
    inferno = data['kit'] == 'building_inferno'
    raw_floors = op.get('floors', 2)
    if isinstance(raw_floors, list):
        if len(raw_floors) != 2:
            raise OpError('floors must be a number or [min, max]')
        f_lo, f_hi = (buildings._int(x, 'floors', 1, 6) for x in raw_floors)
        if f_lo > f_hi:
            raise OpError('floors [min, max] needs min <= max')
    else:
        f_lo = f_hi = buildings._int(raw_floors, 'floors', 1, 6)
    fh = number(op.get('floor_height', 144), 'floor_height', 112, 256)
    rng = random.Random(op.get('seed', 1))
    palette = op.get('materials') or PALETTES.get(data['kit'], [data['materials']['walls']])
    if not isinstance(palette, list) or not palette:
        raise OpError('materials must be a non-empty list of plaster materials (one per house, cycled)')
    palette = [_paint(project, p) for p in palette]
    roof_spec = op.get('roof', {})
    if roof_spec is False:
        roof_spec = {'type': 'none'}
    buildings._keys(roof_spec, 'type pitch overhang material chimneys', 'roof')
    c = SimpleNamespace(
        op=op, data=data, rng=rng, fh=fh, inferno=inferno, face=face,
        trim=buildings._material(project, data['materials']['trim']),
        plinth_mat=buildings._material(project, data['materials']['plinth']),
        inner=buildings._material(project, data['materials']['inner']),
        stone=buildings._material(project, STONE_MAT if inferno else data['materials']['trim']),
        gallery_floor=buildings._material(project, GALLERY_FLOOR if inferno else data['materials']['floor']),
        ceiling=buildings._material(project, CEILING_MAT if inferno else data['materials']['stucco']),
        roof_type=roof_spec.get('type', 'gable'),
        roof_mat=buildings._material(project, roof_spec.get('material',
                                                            ROOF_MAT if inferno else data['materials']['roof'])),
        pitch=number(roof_spec.get('pitch', 24), 'roof.pitch', 8, 45),
        overhang=number(roof_spec.get('overhang', 24), 'roof.overhang', 0, 64),
        chimneys=roof_spec.get('chimneys', True),
        shop_share=number(op.get('shops', 0.35 if inferno else 0), 'shops', 0, 1))
    if c.roof_type not in ('gable', 'shed', 'flat', 'none'):
        raise OpError('roof.type must be gable, shed, flat or none')
    c.eave_mat = buildings._material(project, EAVE_MAT) if inferno else c.trim
    arcade = _feature(op, 'arcade', 'houses depth pier', 'arcade')
    loggia = _feature(op, 'loggia', 'houses depth', 'loggia')
    c.arcade_depth = number((arcade or {}).get('depth', 112), 'arcade.depth', 64, 256)
    c.pier = number((arcade or {}).get('pier', 28), 'arcade.pier', 12, 64)
    c.loggia_depth = number((loggia or {}).get('depth', 56), 'loggia.depth', 32, 160)
    want_depth = op.get('depth', 'auto')
    if want_depth != 'auto':
        want_depth = number(want_depth, 'depth', 0, 1024)
    axis, normal, plane = face['axis'], face['normal'], face['plane']
    out = normal[axis]                       # +1 or -1: direction from the wall into the street
    c.zf = zf = face['z0']                   # floor in front of the face
    # A sidewalk along the face (add_paving) lifts doors, plinth and floors onto its top.
    from . import paving
    c.z0 = z0 = zf + (paving.sidewalk_top(m, axis, plane, out, face['start'], face['end'], zf) or 0)
    if face.get('ceiling') and z0 + f_hi * fh > face['wall_top'] + 1e-3:
        raise OpError(f'room has a ceiling at {face["wall_top"] - zf:.0f} units: the fronts must fit under it '
                      f'(floors x floor_height); dress open streets and courtyards (ceiling:false)')
    length = face['end'] - face['start']
    if length < 96:
        raise OpError('facade must be at least 96 units long')
    c.holes = holes = _openings(m, face)
    if 'houses' in op:
        widths = op['houses']
        if not isinstance(widths, list) or not widths or sum(widths) > length + 1e-3:
            raise OpError('houses must be widths (units) summing to at most the facade length')
    else:
        target = number(op.get('house_width', 320), 'house_width', 160, 1024)
        count = max(1, round(length / target))
        widths = [length / count] * count
    widths = [number(w, 'house width', 96, 2048) for w in widths]
    c.t = t = face['thickness']

    row_group = m.new_group('facade_row:' + name, origin=tuple(face_pt(face, (face['start'] + face['end']) / 2, zf)))
    structure._set_group_defaults(row_group)
    m.attach(row_group)
    row = _Row(project, m, face, row_group)

    def clear(u0, u1, z_lo, z_hi, margin=4):
        return not any(h[0] - margin < u1 and u0 < h[1] + margin and h[2] - margin < z_hi and z_lo < h[3] + margin
                       for h in holes)
    c.clear = clear

    # Houses: span, floors, storeys and whether there is room for a real body behind the face.
    plan = []
    # Rows start at different colours; a hash (not rng) keeps the row's geometry unchanged.
    start = (zlib.crc32(f"{op.get('seed', 1)}|{name}".encode()) % len(palette)
             if len(palette) > 1 and not op.get('materials') else 0)
    u = face['start'] + (length - sum(widths)) / 2
    previous = None
    for index, w in enumerate(widths):
        floors = rng.randint(f_lo, f_hi)
        if previous is not None and f_hi > f_lo and floors == previous and rng.random() < 0.6:
            floors = f_lo if floors != f_lo else f_hi          # neighbours rarely share a cornice line
        previous = floors
        extra = rng.choice((0, 0, 8, 16)) if inferno else 0
        depth = 0.0
        if want_depth != 0 and not face.get('ceiling') and not any(h[0] < u + w and u < h[1] for h in holes):
            depth = _free_depth(m, row, u, u + w, zf + 1, z0 + floors * fh + 128,
                                288 if want_depth == 'auto' else want_depth, t)
            depth = depth if depth >= max(MIN_DEPTH, t + 32) else 0.0
            depth = min(depth, max(224, round(w * 0.9)), 288) if want_depth == 'auto' else depth
        if depth and floors < f_hi:
            # A body backed by a taller wall of the next room: grow storeys so no bare wall shows above it.
            back = _back_top(m, row, u, u + w, depth, t, zf)
            while back is not None and floors < f_hi and z0 + floors * fh + extra < back - 40:
                floors += 1
        has_arcade = bool(arcade and _pick(arcade, index, rng, 'arcade.houses') and w >= 160
                          and depth >= 2 * FRONT + c.arcade_depth + 32)
        has_loggia = bool(loggia and floors >= 3 and _pick(loggia, index, rng, 'loggia.houses')
                          and depth >= 2 * FRONT + c.loggia_depth + 32)
        ground = fh + (48 if has_arcade else 0)
        bases = [z0] + [z0 + ground + i * fh for i in range(floors - 1)]
        top = bases[-1] + (ground if floors == 1 else fh) + extra
        plan.append({'index': index, 'u0': u, 'u1': u + w, 'w': w, 'floors': floors, 'top': top, 'depth': depth,
                     'bases': bases, 'ground': ground, 'arcade': has_arcade, 'loggia': has_loggia,
                     'material': palette[(index + start) % len(palette)][0],
                     'tint': palette[(index + start) % len(palette)][1]})
        u += w
    for h in plan:
        if h['depth']:
            _clear_wall(m, row, h['u0'], h['u1'], zf, t)
    # Corner blocks: rows along X fill the empty square behind a room corner, so roofs close from above.
    if axis == 1:
        for h, edge, sign in ((plan[0], plan[0]['u0'], -1), (plan[-1], plan[-1]['u1'], 1)):
            if h['depth'] and abs(edge - (face['start'] if sign < 0 else face['end'])) < 1:
                free = _free_side(m, row, edge, sign, -h['depth'], 0, zf + 1, h['top'] + 64, h['depth'] + t)
                if free >= t + 32:
                    h['ext'] = (edge, edge + sign * min(free, h['depth'] + t))

    ids, raised = [], []
    houses = []
    for h in plan:
        g = m.new_group(f'facade:{name}:{h["index"]}', origin=tuple(face_pt(face, (h['u0'] + h['u1']) / 2, zf)))
        structure._set_group_defaults(g)
        m.attach(g, row_group)
        if h['depth']:
            _deep_house(row, h, g, c)
        else:
            raised += _shallow_house(row, h, g, c, ids)
        _tint(m, g, h['material'], h['tint'])
        houses.append({'id': g['nodeID'], 'from': round(h['u0'], 1), 'to': round(h['u1'], 1),
                       'material': h['material'], **({'tint': h['tint']} if h['tint'] else {}),
                       'floors': h['floors'], 'top': round(h['top'], 1),
                       'depth': round(h['depth'], 1), 'arcade': h['arcade'], 'loggia': h['loggia'],
                       'arcade_depth': c.arcade_depth if h['arcade'] else 0,
                       'doors': h.get('doors', []), 'shops': h.get('shops', [])})
    top = max(h['top'] for h in plan)
    meta = {'schema': 2, 'name': name, 'axis': axis, 'plane': plane, 'normal': list(normal), 'z0': z0,
            'top': top, 'houses': houses, 'raised': raised}
    row_group.set('cs2map_facade', 'string', json.dumps(meta, separators=(',', ':')))
    if op.get('ref'):
        refs[op['ref']] = row_group['nodeID']
    return {'op': 'add_facade', 'id': row_group['nodeID'], 'houses': len(houses),
            'deep': sum(1 for h in plan if h['depth']), 'arcades': sum(1 for h in plan if h['arcade']),
            'loggias': sum(1 for h in plan if h['loggia']), 'models': len(row.props), 'top': top,
            'raised_wall': bool(ids), 'kept_clear': len(holes)}


def face_pt(face, u, z, d=0.0):
    p = [0.0, 0.0, z]
    p[face['axis']] = face['plane'] + face['normal'][face['axis']] * d
    p[1 - face['axis']] = u
    return p


def _free_side(m, row, edge, sign, d_lo, d_hi, z_lo, z_hi, want):
    """Free length along the wall beyond a row end (sign -1: before the start), within depths d_lo..d_hi."""
    a, o, plane, out = row.axis, row.other, row.plane, row.out
    best = want
    lo_a, hi_a = sorted((plane + out * d_lo, plane + out * d_hi))

    def hit(lo, hi, margin):
        nonlocal best
        if hi[a] <= lo_a + 0.5 or lo[a] >= hi_a - 0.5 or hi[2] <= z_lo or lo[2] >= z_hi:
            return
        if (hi[o] - edge if sign > 0 else edge - lo[o]) <= 0.5:
            return              # on the other side of the edge
        near = (lo[o] - edge) if sign > 0 else (edge - hi[o])
        best = min(best, max(0.0, near) - margin)
    for data in layout.plans(m):
        for r in data['rooms'].values():
            hit(r['interior']['mins'], r['interior']['maxs'], 16)
    for node in m.iter_nodes():
        if node.type != 'CMapMesh' or node.get('physicsType') == 'none' or not checks._is_solid_mesh(node):
            continue
        b = checks._mesh_bounds(node)
        if b:
            hit(b[0], b[1], 0)
    return best


def _segment_arc(a0, a1, crown, rise, segments=10):
    """Points of a segmental arch over the opening a0..a1 (left to right) with its crown height and rise."""
    half = (a1 - a0) / 2
    if rise < 6:
        return [(a0, crown), (a1, crown)]
    radius = (half * half + rise * rise) / (2 * rise)
    cz, cx = crown - radius, (a0 + a1) / 2
    phi = math.asin(min(1.0, half / radius))
    pts = [(cx + radius * math.cos(math.pi / 2 + phi - 2 * phi * i / segments),
            cz + radius * math.sin(math.pi / 2 + phi - 2 * phi * i / segments)) for i in range(segments + 1)]
    pts[0], pts[-1] = (a0, pts[0][1]), (a1, pts[-1][1])
    return pts


def _deep_house(row, h, g, c):
    """A real house block: front slab with niches, mass, roof; galleries; details set into the niches."""
    op, rng, data, fh, zf, z0 = c.op, c.rng, c.data, c.fh, c.zf, c.z0
    clear, inferno, trim, inner = c.clear, c.inferno, c.trim, c.inner
    u0, u1, w, top, depth, mat = h['u0'], h['u1'], h['w'], h['top'], h['depth'], h['material']
    floors, bases, ground = h['floors'], h['bases'], h['ground']
    mid = (u0 + u1) / 2
    walls = {0.0: {'niches': [], 'fills': [], 'range': (zf, top)}}   # wall faces by depth (0 = street face)
    surrounds = []         # stone door surrounds: trims stop at them
    details = []           # deferred stock props
    blocked = []           # awnings and shop fronts: no window or balcony may overlap them
    hoods = []             # window hoods (u0, u1, z0, z1): the edge lamp must not hit one

    def free(a, b, lo, hi):
        return clear(a, b, lo, hi) and not any(x[0] - 4 < b and a < x[1] + 4 and x[2] - 4 < hi and lo < x[3] + 4
                                               for x in walls[0.0]['niches'] + blocked)

    def niche(a, b, lo, hi, deep, back=inner, face=0.0):
        wall = walls[face]
        wall['niches'].append((a, b, lo, hi))
        if deep < FRONT:
            wall['fills'].append((a, b, lo, hi, deep, back))
    niches = walls[0.0]['niches']

    def door(du, zb, face=0.0):
        dkey = 'door_shop' if rng.random() < 0.5 else 'door_wood'
        size = row.size(dkey)
        dw, dh = size[1], size[2]
        niche(du - dw / 2, du + dw / 2, zb, zb + dh, 12 + GAP, face=face)
        details.append(lambda: row.put(dkey, du, zb, face - 12, g))
        # Stone surround: jambs and lintel, prouder than the plinth (never coplanar with it).
        for a, b, lo, hi in ((du - dw / 2 - 8, du - dw / 2, zb, zb + dh + 10),
                             (du + dw / 2, du + dw / 2 + 8, zb, zb + dh + 10),
                             (du - dw / 2, du + dw / 2, zb + dh, zb + dh + 10)):
            row.box(a, b, lo, hi, face, face + 3.5, trim, g)
        if face == 0:
            surrounds.append((du - dw / 2 - 8, du + dw / 2 + 8, zf, zb + dh + 10))
        h.setdefault('doors', []).append([round(du, 1), round(dw, 1), round(face, 1)])
        return dw, dh

    def shop(su, zb, wide, face=0.0):
        key, sw = ('shop_152', 152) if wide else ('shop_128', 128)
        h.setdefault('shops', []).append([round(su, 1), sw, round(face, 1)])
        niche(su - sw / 2, su + sw / 2, zb, zb + 160, FRONT, face=face)
        walls[face]['fills'].append((su - sw / 2, su + sw / 2, zb, zb + 160, FRONT - 0.5, inner))
        details.append(lambda: row.inset(_shop_glass(row, key, su, zb, g, face - 12),
                                         row.put(key, su, zb, face - 12, g)))
        sign = rng.choice(('sign_monaco', 'sign_gelateria', None))
        if sign:
            details.append(lambda: row.put(sign, su, zb + 134, face + 0.5, g))
        if face == 0:
            blocked.append((su - sw / 2, su + sw / 2, zb, zb + 160))
        if face == 0 and op.get('awnings', True) and rng.random() < 0.7:
            details.append(lambda: row.put('awning', su, zb + 164, 0.5, g, scale=sw / 128))
            blocked.append((su - sw / 2, su + sw / 2, zb + 160, zb + 210))

    # Ground floor: a shop front or a door with windows beside it (or an arcade, built below).
    is_shop = (not h['arcade'] and inferno and w >= 200 and rng.random() < c.shop_share
               and clear(mid - 80, mid + 80, z0, z0 + 168))
    door_cfg = op.get('doors', {})
    buildings._keys(door_cfg, 'parts every', 'doors')
    every = buildings._int(door_cfg.get('every', 2), 'doors.every', 1, 8)
    has_door = not is_shop and not h['arcade'] and (h['index'] + rng.randrange(every)) % every == 0
    door_u = u0 + w * rng.uniform(0.3, 0.7)
    win_cfg = op.get('windows', {})
    buildings._keys(win_cfg, 'parts per_floor bottom', 'windows')
    per_floor = buildings._int(win_cfg.get('per_floor', max(1, min(4, int(w // 104)))), 'windows.per_floor', 1, 6)
    bottom = number(win_cfg.get('bottom', 30), 'windows.bottom', 8, fh - 40)
    style = rng.choice(('stone', 'stone', 'shutter', 'french')) if inferno and not win_cfg.get('parts') else 'kit'
    shutter_key = rng.choice(SHUTTER_WINDOWS)
    closed_share = rng.choice((0.0, 0.15, 0.3))
    shutters = style == 'stone' and w / per_floor >= 150 and rng.random() < 0.4   # room for both leaves
    balcony_floor = 1 if floors > 1 and op.get('balconies', True) and w >= 224 and rng.random() < 0.6 else None
    if balcony_floor is not None and h['loggia'] and balcony_floor == floors - 1:
        balcony_floor = None
    if is_shop:
        shop(mid, z0 + 0.5, w >= 288)
    elif has_door:
        if clear(door_u - 50, door_u + 50, z0, z0 + 152):
            dw, dh = door(door_u, z0 + 0.5)
            if op.get('awnings', True) and h['index'] % 3 == 0 and floors > 1:
                details.append(lambda: row.put('awning', door_u, z0 + dh + 14, 0.5, g, scale=0.75))
                blocked.append((door_u - 50, door_u + 50, z0 + dh + 10, z0 + dh + 50))
        else:
            has_door = False

    def window(u, zb, f):
        """One window set into the front slab at centre u, opening bottom zb."""
        if style == 'kit':
            return
        if style == 'shutter':
            part = buildings._part(data, shutter_key, 'window')
            ow, oh = part['opening']
            if free(u - 24, u + 24, zb, zb + 52):
                niche(u - ow / 2, u + ow / 2, zb, zb + oh, 8)
                ob = part['opening_bottom'] - part['bounds'][0][2]
                details.append(lambda: row.put(None, u, zb - ob, 0, g, model=part['model'], front=part['front']))
            return
        if f == balcony_floor and abs(u - mid) < 64:
            size = row.size('balcony_door')
            bw, bh = size[1], size[2]
            zd = bases[f] + 4
            if free(u - bw / 2 - 4, u + bw / 2 + 4, zd, zd + bh):
                niche(u - bw / 2, u + bw / 2, zd, zd + bh, 10 + GAP)
                details.append(lambda: row.put('balcony_door', u, zd, -10, g))
            return
        # stone: 40x76 hole, glazed window inside, stone surround with sill on the face
        if not free(u - 36, u + 36, zb - 20, zb + 94):
            return
        if f > 0 and rng.random() < closed_share:
            niche(u - 24.5, u + 24.5, zb - 6, zb + 82, 6 + GAP)
            details.append(lambda: row.put('closed', u, zb - 6, -6, g))
            details.append(lambda: row.put('frame', u, zb - 18, 0, g))
        else:
            niche(u - 20, u + 20, zb, zb + 76, 8 + GAP)
            details.append(lambda: row.inset(row.put('glass', u, zb - 2, -8, g), row.put('frame', u, zb - 18, 0, g)))
        if shutters and f > 0:          # open louvred shutters folded against the wall beside the frame
            details.append(lambda: row.put('shutter_l', u - 33.5 - 15, zb - 1, 0.5, g, scale=1.42))
            details.append(lambda: row.put('shutter_r', u + 33.5 + 15, zb - 1, 0.5, g, scale=1.42))
        # a hood over the window, only where it clears the eave (lower floors with an own floor_height)
        if (style == 'french' or (f > 0 and rng.random() < 0.25)) and zb + 92 + row.size('hood')[2] <= top - 18:
            details.append(lambda: row.put('hood', u, zb + 92, 0, g))
            hw = max(row.size('hood')[:2])
            hoods.append((u - hw / 2, u + hw / 2, zb + 92, zb + 92 + row.size('hood')[2]))

    for f in range(floors):
        if (f == 0 and h['arcade']) or (f == floors - 1 and h['loggia']):
            continue
        base = bases[f]
        for k in range(per_floor):
            wu = u0 + (k + 0.5) * w / per_floor
            if f == 0 and (is_shop or (has_door and abs(wu - door_u) < 64)):
                continue
            window(wu, base + bottom, f)
        if f == balcony_floor and style not in ('kit', 'shutter') and not any(
                x[0] < mid + 64 and mid - 64 < x[1] and x[2] < base + 52 and base - 8 < x[3] for x in blocked)                 and clear(mid - 64, mid + 64, base, base + 52):
            details.append(lambda base=base: row.put('balcony', mid, base + 2, 0, g))

    # Galleries: an arcade on the ground floor, a loggia on the top floor.
    cut = []
    if h['arcade']:
        cut.append((u0, u1, zf, z0 + ground))
        _gallery(row, c, h, g, zf, z0 + ground, c.arcade_depth, 'arcade', walls, niche, door, shop, details)
    if h['loggia']:
        cut.append((u0, u1, bases[-1], top))
        _gallery(row, c, h, g, bases[-1], top, c.loggia_depth, 'loggia', walls, niche, door, shop, details)

    # Wall slabs with their niches (solid), fills at the back of shallow niches, the mass behind.
    for face, wall in walls.items():
        lo, hi = wall['range']
        row.slab(buildings._wall_rects(u0, u1, lo, hi, wall['niches'] + (cut if face == 0 else [])),
                 face - FRONT, face, mat, g, render_only=False)
        for a, b, n_lo, n_hi, deep, back in wall['fills']:
            row.panel(a, b, n_lo, n_hi, face - FRONT, face - deep, back, mat, g)
    z_low = zf
    if h['arcade']:
        row.box(u0, u1, zf, z0 + ground, -depth, -(2 * FRONT + c.arcade_depth), mat, g, render_only=False)
        z_low = z0 + ground
    z_high = bases[-1] if h['loggia'] else top
    if z_high > z_low:
        # Over an arcade the underside of the house is its whitewashed ceiling.
        row.box(u0, u1, z_low, z_high, -depth, -FRONT, mat, g, render_only=False,
                materials={'bottom': c.ceiling} if h['arcade'] else None)
    if h['loggia']:
        row.box(u0, u1, bases[-1], top, -depth, -(2 * FRONT + c.loggia_depth), mat, g, render_only=False)
    ext = h.get('ext')
    if ext:
        lo_u, hi_u = sorted(ext)
        row.box(lo_u, hi_u, zf, top, -depth, -c.t, mat, g, render_only=False)
        corner = (lo_u, hi_u - c.t) if ext[1] < ext[0] else (lo_u + c.t, hi_u)
        if corner[1] - corner[0] > 1:
            row.box(corner[0], corner[1], zf, top, -c.t, 0, mat, g, render_only=False)

    # Trim: plinth, floor bands and cornice (render-only, proud of the face, split at niches).
    def proud(a, b, lo, hi, dd, material, extra=()):
        # Niches that start on the street (doors, shops) cut the trim down to the floor: no ledge in them.
        spans = [(n[0], n[1], zf if n[2] <= z0 + 1 else n[2], n[3]) for n in niches + surrounds + list(extra)]
        holes = [[max(a, n[0]), min(b, n[1]), max(lo, n[2]), min(hi, n[3])] for n in spans
                 if n[0] < b and a < n[1] and n[2] < hi and lo < n[3]]
        row.slab(buildings._wall_rects(a, b, lo, hi, holes), 0, dd, material, g)
    plinth = number(op.get('plinth', 28), 'plinth', 0, 64)
    if plinth:
        proud(u0, u1, zf, z0 + plinth, 2.5, c.plinth_mat, cut)
    if op.get('bands', True):
        for zb in bases[1:]:
            proud(u0, u1, zb - 7, zb, 3, trim)
    if op.get('cornice', True):
        row.box(u0, u1, top - 14, top - 4, 0, 6, trim, g)
        row.box(u0, u1, top - 4, top, 0, 9, trim, g)
    # Quoins up the corners of the row ends; pilasters at the edges of some palazzi.
    lift = z0 + ground if h['arcade'] else z0 + plinth
    for edge, sign in ((u0, 1), (u1, -1)):
        if abs(edge - c.face['start']) < 1 or abs(edge - c.face['end']) < 1:
            z, k = lift, 0
            while z + 16 <= top - 16:
                wq = 26 if k % 2 == 0 else 16
                a, b = (edge, edge + wq) if sign > 0 else (edge - wq, edge)
                row.box(a, b, z, z + 15, 0, 1.5, trim, g)
                z += 17
                k += 1
        elif inferno and rng.random() < 0.3 and floors > 1:
            a, b = (edge, edge + 18) if sign > 0 else (edge - 18, edge)
            proud(a, b, lift, top - 14, 2, trim)

    # Roof: attic (solid) and a tiled slab with the eave over the street.
    ru0, ru1 = (min(u0, *ext), max(u1, *ext)) if ext else (u0, u1)
    if c.roof_type in ('gable', 'shed'):
        k = math.tan(math.radians(c.pitch))
        if c.roof_type == 'gable':
            ridge_d, rise = depth / 2, depth / 2 * k
            attic = [(0, top), (-depth, top), (-ridge_d, top + rise)]
        else:
            ridge_d, rise = depth, depth * k
            attic = [(0, top), (-depth, top), (-depth, top + rise)]
        row.node(row.profile(attic, ru0, ru1, mat, g), g)
        th, back = 6.0, 12.0
        if c.roof_type == 'gable':
            lo = [(c.overhang, top - k * c.overhang), (-ridge_d, top + rise), (-depth - back, top - k * back)]
        else:
            lo = [(c.overhang, top - k * c.overhang), (-depth - back, top + rise + k * back)]
        ra = ru0 - 8 if ext or ru0 > c.face['start'] + 1 else ru0 + 1     # see the lean-to note
        rb = ru1 + 8 if ext or ru1 < c.face['end'] - 1 else ru1 - 1
        _roof_piece(row, lo + [(d, z + th) for d, z in lo][::-1], ra, rb, c, g)
        if c.chimneys and rng.random() < 0.75:
            cu = rng.uniform(u0 + 40, u1 - 40)
            cd = -ridge_d - 18 if c.roof_type == 'gable' else -depth * 0.6
            ztop = top + rise + 42
            row.box(cu - 12, cu + 12, top - 8, ztop, cd - 14, cd + 14, mat, g, render_only=False)
            row.box(cu - 16, cu + 16, ztop, ztop + 5, cd - 18, cd + 18, trim, g, render_only=False)
            if rng.random() < 0.6:          # a terracotta pot on the chimney, like de_inferno's roofs
                row.put('chimney_top', cu, ztop + 5, cd - 16, g, fade=(2600, 3400))
        else:
            cu = None
        if c.roof_type == 'gable' and op.get('roof_details', True) and rng.random() < 0.35:
            au = rng.uniform(u0 + 50, u1 - 50)
            if cu is None or abs(au - cu) > 70:   # a TV antenna on the ridge
                row.put('antenna', au, top + rise + th, -ridge_d - 17.5, g, fade=(2600, 3400))
    elif c.roof_type == 'flat':
        row.box(ru0, ru1, top, top + 4, -depth, c.overhang * 0.25, trim, g, render_only=False)

    # Lamps and downpipes at the house edges, then the deferred stock details.
    if op.get('lamps', True) and h['index'] % 2 == 0 and floors > 1 and not h['arcade'] \
            and clear(u1 - 34, u1 - 14, z0 + 100, z0 + 140) \
            and not any(a < u1 - 10 and u1 - 38 < b and lo < z0 + fh + 10 and z0 + fh - 40 < hi
                        for a, b, lo, hi in hoods):
        row.put('lamp', u1 - 24, z0 + fh - 34, 0, g, fade=(2600, 3400))
    if op.get('gutters', True) and clear(u1 - 10, u1 - 2, zf, top) and not row.pipe_taken(u1 - 6):
        pipe_h = row.size('pipe')[2]
        z_pipe = z0 + ground if h['arcade'] else zf
        count = math.ceil((top - z_pipe) / pipe_h)
        scale = (top - z_pipe) / (count * pipe_h)
        for i in range(count):
            row.put('pipe', u1 - 6, z_pipe + i * pipe_h * scale, 0.5, g, scale=scale, fade=(2600, 3400))
    for make in details:
        make()


def _roof_piece(row, outline, ru0, ru1, c, g):
    """Tiled roof slab from an outline in (d, z); the underside of the eave gets the eave material."""
    along = [0.0, 0.0, 0.0]
    along[row.other] = 1.0
    piece = row.profile(outline, ru0, ru1, c.roof_mat, g)
    _roof_uv(piece, along)
    for f in piece.faces:
        if piece.normal(f)[2] < -0.3:
            f.material = c.eave_mat
    row.node(piece, g)


def _gallery(row, c, h, g, z_lo, z_hi, deep, kind, walls, niche, door, shop, details):
    """An open storey across the house: stone piers with segmental arches (arcade) or posts with a parapet
    (loggia) in the street plane, and a recessed back wall with shop fronts, doors or balcony doors."""
    u0, u1, w, mat = h['u0'], h['u1'], h['w'], h['material']
    rng, z0, zf = c.rng, c.z0, c.zf
    pier = c.pier if kind == 'arcade' else 16
    target = 128 if kind == 'arcade' else 88
    n = max(1, round((w - pier) / (target + pier)))
    span = (w - (n + 1) * pier) / n
    while span < 56 and n > 1:
        n -= 1
        span = (w - (n + 1) * pier) / n
    floor_z = z0 + 2 if kind == 'arcade' else z_lo           # the arcade floor is one small step up
    crown = z_hi - (22 if kind == 'arcade' else 16)
    rise = max(0.0, min(span / 2, crown - (floor_z + 96))) if kind == 'arcade' else 0.0
    piers, spandrels, rings, openings = [], [], [], []
    for i in range(n + 1):
        a = u0 + i * (span + pier)
        piers.append([(a, z_lo), (a + pier, z_lo), (a + pier, z_hi), (a, z_hi)])
        if i < n:
            openings.append((a + pier, a + pier + span))
    for a0, a1 in openings:
        pts = _segment_arc(a0, a1, crown, rise)
        for p, q in zip(pts, pts[1:]):
            spandrels.append([p, q, (q[0], z_hi), (p[0], z_hi)])
        if rise >= 6:
            cx = (a0 + a1) / 2
            radius = ((a1 - a0) ** 2 / 4 + rise * rise) / (2 * rise)
            cz = crown - radius
            for p, q in zip(pts, pts[1:]):
                np_ = ((p[0] - cx) / radius, (p[1] - cz) / radius)
                nq = ((q[0] - cx) / radius, (q[1] - cz) / radius)
                rings.append([p, q, (q[0] + nq[0] * 7, q[1] + nq[1] * 7), (p[0] + np_[0] * 7, p[1] + np_[1] * 7)])
    row.slab(piers, -FRONT, 0, c.stone, g, render_only=False)
    span_d = tuple(sorted((row.plane - row.out * FRONT, row.plane)))
    piece = mesh.region_solid(spandrels, span_d, row.axis == 1, mat)
    for f in piece.faces:              # the soffit of each arch is dressed stone, like the piers
        if piece.normal(f)[2] < -0.2:
            f.material = c.stone
    row.node(piece, g)
    if rings:
        row.slab(rings, 0, 1.5, c.stone, g)
    spring = crown - rise
    for a0, a1 in openings:      # imposts: small stone blocks where the arch springs from the piers
        row.box(a0 - 2, a0 + 3, spring - 8, spring, 0, 2.5, c.stone, g)
        row.box(a1 - 3, a1 + 2, spring - 8, spring, 0, 2.5, c.stone, g)
    back = -(FRONT + deep)
    walls[back] = {'niches': [], 'fills': [], 'range': (z_lo, z_hi)}
    if kind == 'arcade':
        # The floor under the arcade and the step in front of it (solid).
        row.box(u0, u1, zf - 15, floor_z, back, -0.5, mat, g, render_only=False,
                materials={'top': c.gallery_floor, 'sides': c.stone})
        for a0, a1 in openings:
            cu = (a0 + a1) / 2
            if a1 - a0 >= 136 and rng.random() < 0.55:
                shop(cu, floor_z, False, face=back)
            elif rng.random() < 0.8:
                door(cu, floor_z, face=back)
    else:
        size = row.size('balcony_door')
        for a0, a1 in openings:
            row.box(a0, a1, z_lo, z_lo + 34, -FRONT + 6, -6, c.stone, g, render_only=False)   # parapet
            if a1 - a0 >= size[1] + 8:
                cu = (a0 + a1) / 2
                niche(cu - size[1] / 2, cu + size[1] / 2, z_lo + 2, z_lo + 2 + size[2], 10 + GAP, face=back)
                details.append(lambda cu=cu: row.put('balcony_door', cu, z_lo + 2, back - 10, g))


def _shop_glass(row, key, mid, z0, g, d_rear=-12.0):
    """The glass of a stock shop front shares the frame's origin: place it with the frame's transform."""
    frame_model, front = P[key]
    glass_model = P[key + '_glass'][0]
    lo, hi = _model_bounds(row.p, frame_model)
    yaw = math.degrees(math.atan2(row.normal[1], row.normal[0])) - buildings.FRONT_YAW[front]
    corners = [structure._rotate((x, y, z), (0, yaw, 0)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
               for z in (lo[2], hi[2])]
    rear = min(cc[row.axis] * row.out for cc in corners)
    cu = (min(cc[row.other] for cc in corners) + max(cc[row.other] for cc in corners)) / 2
    origin = [0.0, 0.0, z0 - lo[2]]
    origin[row.axis] = row.plane + row.out * (d_rear - rear)
    origin[row.other] = mid - cu
    kv = {'model': glass_model, 'solid': '0', 'fademindist': '4200', 'fademaxdist': '5200'}
    ent = row.m.new_entity('prop_static', kv, origin, (0.0, yaw, 0.0), (1.0, 1.0, 1.0),
                           defaults=row.p.catalog.defaults('prop_static'))
    row.m.attach(ent, g)
    row.props.append(ent['nodeID'])
    return ent


def _shallow_house(row, h, g, c, ids):
    """A thin plaster skin on a shared wall: no space for niches, so windows sit on the skin."""
    m, project = row.m, row.p
    op, data, rng, zf, z0, fh, trim, inner, holes, clear, face = (
        c.op, c.data, c.rng, c.zf, c.z0, c.fh, c.trim, c.inner, c.holes, c.clear, c.face)
    axis, other, plane, out = row.axis, row.other, row.plane, row.out
    u0, u1, w, top, material, floors = h['u0'], h['u1'], h['w'], h['top'], h['material'], h['floors']
    index = h['index']
    raised = []
    # The wall stays: a front lower than it would leave bare wall above the roof, so it grows to the top.
    if not face.get('ceiling') and face['wall_top'] > top:
        top = h['top'] = face['wall_top']

    def skin_rects(a, b, lo, hi):
        cut = [[max(a, x[0]), min(b, x[1]), max(lo, x[2]), min(hi, x[3])] for x in holes
               if x[0] < b and a < x[1] and x[2] < hi and lo < x[3]]
        return buildings._wall_rects(a, b, lo, hi, cut)

    # Raise the wall behind the face when the fronts are taller than it. A shared wall dressed on
    # both sides is raised once: stretches already raised by another facade row are skipped.
    mine = sorted((plane - out * face['thickness'], plane))
    base = face['wall_top']
    for node in m.iter_nodes():
        if node.type != 'CMapMesh' or node.get('physicsType') == 'none':
            continue
        b = checks._mesh_bounds(node)
        # Only a piece through the whole wall seals it: a lean-to cap of the other side's row
        # covers half the thickness and would leave a slit above the wall top.
        if (b and b[0][axis] <= mine[0] + 0.5 and b[1][axis] >= mine[1] - 0.5 and b[0][other] < u1
                and b[1][other] > u0 and b[0][2] <= base + 1 and base - 1 < b[1][2] <= base + 64):
            base = max(base, b[1][2])
    if base < top - 1:
        # Stretches already raised by the row on the other side start our raise at their top
        # (two overlapping raises of one wall had coplanar faces).
        spans = [(u0, u1, base)]
        for node in m.iter_nodes():
            other_meta = node.get('cs2map_facade')
            for box_ in (json.loads(other_meta).get('raised') or []) if other_meta else []:
                thick = (box_[0][axis], box_[1][axis])
                if not (thick[0] < mine[1] - 0.5 and thick[1] > mine[0] + 0.5 and box_[0][2] <= base + 1):
                    continue
                cut, btop = (box_[0][other], box_[1][other]), box_[1][2]
                out_spans = []
                for a_, b_, z_ in spans:
                    if b_ <= cut[0] or a_ >= cut[1]:
                        out_spans.append((a_, b_, z_))
                        continue
                    if a_ < cut[0]:
                        out_spans.append((a_, cut[0], z_))
                    out_spans.append((max(a_, cut[0]), min(b_, cut[1]), max(z_, btop)))
                    if b_ > cut[1]:
                        out_spans.append((cut[1], b_, z_))
                spans = out_spans
        for a_, b_, z_ in spans:
            if b_ - a_ <= 1 or z_ >= top - 1:
                continue
            node = row.slab(skin_rects(a_, b_, z_, top), -face['thickness'], 0, material, g, render_only=False)
            if node is not None:
                ids.append(node['nodeID'])
                lo_, hi_ = row.at(a_, z_, -face['thickness']), row.at(b_, top, 0)
                raised.append([[min(lo_[i], hi_[i]) for i in range(3)], [max(lo_[i], hi_[i]) for i in range(3)]])

    def wall_part(key, category, u, bottom, scale=1.0, depth=SKIN + GAP):
        part = buildings._part(data, key, category)
        part = {**part, 'full_key': f"{part['kit']}/{part['key']}", 'solid': bool(part.get('solid'))}
        anchor = row.at(u, bottom - part['bounds'][0][2] * scale, depth)   # >= 1 unit off what is behind
        from . import kits
        ent = kits.place(project, m, part, anchor, row.normal, offset=0, scale=scale, snap_floor=False,
                         wall_anchor=True)
        for k, v in {'solid': '0', 'fademindist': '2600', 'fademaxdist': '3400'}.items():
            ent['entity_properties'].set(k, 'string', v)
        m.remove(ent)
        m.attach(ent, g)
        row.props.append(ent['nodeID'])
        return part, ent

    # Plaster skin (render-only, 1.25 units): the wall keeps its collision.
    row.slab(skin_rects(u0, u1, z0 if z0 > zf else zf, top), EPS, SKIN, material, g)
    plinth = number(op.get('plinth', 24), 'plinth', 0, 64)
    if plinth:
        row.slab(skin_rects(u0 + 0.5, u1 - 0.5, zf, z0 + plinth), SKIN, SKIN + 2.5, c.plinth_mat, g)
    if op.get('bands', True):
        for f in range(1, floors):
            zb = z0 + f * fh
            row.slab(skin_rects(u0 + 0.5, u1 - 0.5, zb - 6, zb), SKIN, SKIN + 3, trim, g)
    # Room corners belong to rows along X (they also get corner blocks); a row along Y ends its
    # cornice and lean-to CORNER short of a corner, so the two never cross or meet in one plane.
    corner_x = axis == 0
    cu0 = u0 + CORNER if corner_x and u0 <= face['start'] + 1 else u0
    cu1 = u1 - CORNER if corner_x and u1 >= face['end'] - 1 else u1
    if op.get('cornice', True):
        row.slab([[(cu0, top - 12), (cu1, top - 12), (cu1, top), (cu0, top)]], SKIN, SKIN + 8, trim, g)
    eave = number(op.get('eave', 20), 'eave', 0, 64)
    if eave and c.roof_type != 'none':
        # A tiled lean-to over the wall: two rows on a shared wall meet in a ridge above its middle.
        k = math.tan(math.radians(c.pitch))
        half = face['thickness'] / 2
        lo = [(eave, top - k * eave), (-half, top + k * half)]
        ra = u0 - 4 if cu0 == u0 else cu0 + 1         # never flush with the cornice end
        rb = u1 + 4 if cu1 == u1 else cu1 - 1
        _roof_piece(row, lo + [(d, z + 6) for d, z in lo][::-1], ra, rb, c, g)
        # Fill the wall up to the ridge under the tiles (the far half belongs to the row on the other side).
        row.node(row.profile([(0, top), (-half, top), (-half, top + k * half)], cu0, cu1, material, g), g)
    win_cfg = op.get('windows', {})
    buildings._keys(win_cfg, 'parts per_floor bottom', 'windows')
    choices = win_cfg.get('parts') or WINDOWS.get(data['kit'], ['window_stone_40'])
    per_floor = buildings._int(win_cfg.get('per_floor', max(1, min(4, int(w // 112)))), 'windows.per_floor', 1, 6)
    bottom = number(win_cfg.get('bottom', 28), 'windows.bottom', 8, fh - 40)
    key = choices[(index + rng.randrange(len(choices))) % len(choices)]
    # Inferno houses without a body still get glazed windows in stone frames on most fronts.
    framed = c.inferno and not win_cfg.get('parts') and rng.random() < 0.6
    door_cfg = op.get('doors', {})
    buildings._keys(door_cfg, 'parts every', 'doors')
    every = buildings._int(door_cfg.get('every', 2), 'doors.every', 1, 8)
    has_door = (index + rng.randrange(every)) % every == 0
    door_u = u0 + w * rng.uniform(0.3, 0.7)          # the seed varies doors between rows
    for f in range(floors):
        for k in range(per_floor):
            wu = u0 + (k + 0.5) * w / per_floor
            if f == 0 and has_door and abs(wu - door_u) < 56:
                continue
            wz = z0 + f * fh + bottom
            if framed:
                if wz + 90 > z0 + (f + 1) * fh - 8 or not clear(wu - 36, wu + 36, wz - 20, wz + 90):
                    continue
                row.inset(row.put('glass', wu, wz - 2, SKIN + GAP, g), row.put('frame', wu, wz - 18, SKIN + EPS, g))
                continue
            part = buildings._part(data, key, 'window')
            size = part['size'][2]
            half_w = max(part['size'][0], part['size'][1]) / 2
            if wz + size > z0 + (f + 1) * fh - 8 or not clear(wu - half_w, wu + half_w, wz, wz + size):
                continue
            if part.get('opening'):       # dark glass behind the frame
                ow, oh = part['opening']
                ob = wz + (part['opening_bottom'] - part['bounds'][0][2])
                # 0.25..0.5 in front of the plaster; the window stands a full GAP in front of it.
                row.slab([[(wu - ow / 2, ob), (wu + ow / 2, ob), (wu + ow / 2, ob + oh), (wu - ow / 2, ob + oh)]],
                         SKIN + 0.25, SKIN + 0.5, inner, g)
                wall_part(key, 'window', wu, wz, depth=SKIN + 0.5 + GAP)
                continue
            wall_part(key, 'window', wu, wz)
    if has_door:
        doors = door_cfg.get('parts') or ['door_wood_double', 'door_wood']
        dkey = doors[rng.randrange(len(doors))]
        part = buildings._part(data, dkey, 'door')
        half_w = max(part['size'][0], part['size'][1]) / 2
        if clear(door_u - half_w, door_u + half_w, z0, z0 + part['size'][2]):
            wall_part(dkey, 'door', door_u, z0 + 0.5)
            h.setdefault('doors', []).append([round(door_u, 1), round(2 * half_w, 1), 0.0])
            awning_z = z0 + part['size'][2] + 8            # above the door frame, never across it
            if op.get('awnings', True) and floors > 1 and index % 3 == 0 and awning_z + 40 <= z0 + fh - 6:
                wall_part('awning_128' if w >= 200 else 'awning_64', 'awning', door_u, awning_z)
    mid = (u0 + u1) / 2
    if (op.get('balconies', True) and floors > 1 and w >= 224 and index % 2 == 1
            and clear(mid - 64, mid + 64, z0 + fh, z0 + fh + 52)):
        wall_part('balcony_128', 'balcony', mid, z0 + fh + 2)
    if op.get('lamps', True) and index % 2 == 0 and floors > 1 and clear(u1 - 34, u1 - 14, z0 + fh - 30, z0 + fh + 6):
        wall_part('lamp_ornate', 'lamp', u1 - 24, z0 + fh - 30)
    if op.get('gutters', True) and clear(u1 - 10, u1 - 2, zf, top) and not row.pipe_taken(u1 - 6):
        pipe = buildings._part(data, 'downpipe_128', 'gutter')
        count = math.ceil((top - zf) / 128)
        scale = (top - zf) / (count * pipe['size'][2])
        for i in range(count):
            wall_part('downpipe_128', 'gutter', u1 - 6, zf + i * pipe['size'][2] * scale, scale)
    return raised
