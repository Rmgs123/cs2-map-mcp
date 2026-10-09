"""Deterministic mesh recipes for water, distant scenery and physical supports."""
from __future__ import annotations

import json
import math
import random
import re

from . import checks, lighting, mesh
from .project import OpError

# Same-camera full builds showed much less shoreline grain with Nuke water.
# ``water_style=coast`` keeps the previous Anubis look when explicitly wanted.
WATER_MATERIAL = "materials/liquids/nuke_water.vmat"
COAST_WATER_MATERIAL = "materials/anubis/anubis_water_coast.vmat"
SKYBOX_WATER_MATERIAL = "materials/anubis/anubis_water_coast_skybox.vmat"
WATER_BED_MATERIAL = "materials/concrete/concrete_floor_04.vmat"
SHORE_MATERIAL = "materials/concrete/concrete_floor_04.vmat"
SUPPORT_MATERIAL = "materials/concrete/hr_c/hr_concrete_wall_004.vmat"
METAL_SUPPORT_MATERIAL = "materials/metal/metalwall031a.vmat"
SKYBOXES = {
    "day": "materials/skybox/sky_de_dust2.vmat",
    "sunset": "materials/skybox/cs_italy_s2_skybox_2.vmat",
    "night": "materials/skybox/sky_rain_night_01.vmat",
    "overcast": "materials/skybox/sky_overcast_01.vmat",
    "fog": "materials/skybox/sky_csgo_cloudy01.vmat",
}
BACKDROP_MATERIALS = {
    "port": "materials/concrete/hr_c/hr_concrete_wall_004.vmat",
    "industrial": "materials/metal/metalwall031a.vmat",
    "rocky": "materials/concrete/concrete_floor_04.vmat",
    "city": "materials/brick/brick_ext_04.vmat",
    "italy": "materials/de_inferno/plaster/inferno_plaster_07.vmat",
}
BACKDROP_MODELS = {
    'port': [
        'models/props/de_nuke/hr_nuke/nuke_skybox_buildings/nuke_skybox_warehouse01.vmdl',
        'models/props/de_overpass/crane.vmdl',
        'models/props/de_nuke/hr_nuke/nuke_skybox_buildings/nuke_skybox_warehouse02.vmdl',
    ],
    'city': ['models/props/de_dust/skybox_dust_hotel01.vmdl',
             'models/props/de_dust/skybox_dust_hotel02.vmdl'],
    'industrial': ['models/props/de_nuke/hr_nuke/nuke_skybox_buildings/nuke_skybox_warehouse01.vmdl',
                   'models/props/de_nuke/hr_nuke/nuke_skybox_buildings/nuke_skybox_silo.vmdl'],
    'rocky': ['models/props/dust_massive/rocks/cliff_rock002.vmdl',
              'models/props_foliage/tree_deciduous_card_01_skybox.vmdl'],
}
# The Italy skyline uses measured Inferno scenery rather than the coarse
# boxes used by the older backdrop styles.  Keep the list small: every model
# is a real prop_dynamic and is measured once before it is attached.
ITALY_BUILDINGS = [
    'models/de_inferno/inferno_background_buildings_01/inferno_background_building_tall_01.vmdl',
    'models/de_inferno/inferno_background_buildings_01/inferno_background_building_tall_02.vmdl',
    'models/de_inferno/inferno_background_buildings_01/inferno_background_building_small_02.vmdl',
    'models/de_inferno/inferno_background_buildings_01/inferno_background_building_small_03.vmdl',
    'models/de_inferno/inferno_background_buildings_01/inferno_background_building_medium_01.vmdl',
    'models/de_inferno/inferno_background_buildings_01/inferno_background_building_tall_03.vmdl',
]
ITALY_BELL_TOWER = 'models/props/cs_italy/italy_skybox_tower.vmdl'
ITALY_TREES = ['models/props/de_inferno/hr_i/cypress_a/cypress_a.vmdl',
               'models/foliage/trees/cypress_02/cypress02_large_b.vmdl']
ITALY_GROUND_MATERIAL = 'materials/nature/hr_c/hr_grass_dirt_blend_001.vmat'
# The background houses are white plaster meant to be tinted (de_inferno bakes
# tints into them); untinted they turn sky-blue in shade.  Warm facade tints:
ITALY_TINTS = [(243, 219, 179), (225, 172, 108), (213, 164, 92), (254, 235, 204), (206, 144, 79),
               (187, 110, 80), (245, 210, 150), (230, 190, 140)]
VISUAL = {"physicsType": "none", "bakelighting": False, "disableShadows": 1,
          "renderToCubemaps": False}
# Italy houses are closed meshes, not the stock background cards: those are thin
# one-sided shells with see-through windows that break when seen from above or
# from the side.  Footprint and height still follow the measured stock model.
ITALY_PLASTER = 'materials/de_inferno/plaster/inferno_plaster_facade_01_dirty.vmat'
ITALY_ROOF = 'models/de_inferno/roof/roof_shingles_01/roofkit01_shingles_01.vmat'
ITALY_GLASS = 'materials/de_inferno/glass/inferno_glass_01_opaque.vmat'
ITALY_STONE = 'materials/de_inferno/stone/inferno_stone_wall_09.vmat'
HOUSE_BAKED = {"physicsType": "none", "bakelighting": True, "renderToCubemaps": False}


def _keys(op, allowed):
    extra = set(op) - set(allowed) - {"op", "ref", "name"}
    if extra:
        raise OpError("unknown outdoor fields: " + ", ".join(sorted(extra)))


def _count(value, field, low, high):
    number = lighting._number(value, field, low, high)
    if number != int(number):
        raise OpError(field + " must be an integer")
    return int(number)


def _bounds(value, field="bounds"):
    if isinstance(value, dict) and set(value) == {"mins", "maxs"}:
        lo, hi = value["mins"], value["maxs"]
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        lo, hi = value
    else:
        raise OpError(field + " must be {mins:[x,y,z],maxs:[x,y,z]}")
    lo, hi = lighting._vector(lo, field + ".mins"), lighting._vector(hi, field + ".maxs")
    if any(lo[i] >= hi[i] for i in range(3)) or any(abs(x) > 32768 for x in lo + hi):
        raise OpError(field + " needs maxs > mins, inside +/-32768 units")
    return lo, hi


def _material(project, path, field="material"):
    if not isinstance(path, str) or not path.startswith("materials/") or not path.endswith(".vmat") or ".." in path.split("/"):
        raise OpError(field + " must be an installed materials/...vmat path")
    if not checks._asset_exists(project, path):
        raise OpError(f"{field} {path!r} is missing; use asset_search")
    return path


def _name(m, requested, kind):
    existing = {n.name.removeprefix("outdoor:") for n in m.iter_nodes() if n.type == "CMapGroup" and n.name.startswith("outdoor:")}
    if requested is None:
        requested = kind
        index = 1
        while requested in existing:
            requested = f"{kind}_{index}"; index += 1
    if not isinstance(requested, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", requested):
        raise OpError("name must be 1..64 letters/digits/underscores, starting with a letter/_")
    if requested in existing:
        raise OpError(f"outdoor name {requested!r} already exists")
    return requested


def _commit(project, m, op, name, pieces, result, models=()):
    # Validate and construct every mesh before attaching anything to the map.
    if any(abs(value) > 32768 for piece, _ in pieces for pos in piece.positions for value in pos):
        raise OpError('generated outdoor geometry exceeds +/-32768 units; reduce bounds/height/depth')
    nodes = [m.new_mesh(piece, texture_size=project.texture_size,
                        options={k: v for k, v in (options or {}).items() if k != '_scenery'} or options)
             for piece, options in pieces]
    for (piece, options), node in zip(pieces, nodes):
        if (options or {}).get('_scenery'):
            node.set('cs2map_scenery', 'string', 'backdrop')
    model_nodes = []
    for spec in models:
        if spec.get('mesh'):
            continue
        properties = {
            'model': spec['model'], 'solid': '0', 'disableshadows': '1',
            'targetname': f'outdoor_{name}_{len(model_nodes)}',
        }
        if result.get('style') != 'italy':
            properties['modelscale'] = spec['scale']  # preserve the round-5 recipes
        if spec.get('tint'):
            properties['rendercolor'] = ' '.join(str(v) for v in spec['tint'])
        ent = m.new_entity('prop_dynamic', properties,
            spec['origin'], spec['angles'], [spec['scale']] * 3,
            defaults=project.catalog.defaults('prop_dynamic'))
        ent.set('cs2map_scenery', 'string', 'backdrop')
        model_nodes.append(ent)
    group = m.new_group("outdoor:" + name)
    for node in nodes + model_nodes:
        m.attach(node, group)
    group.set("cs2map_outdoor", "string", json.dumps({"version": 1, "kind": op["op"],
              **{k:v for k,v in result.items() if k != "_refs"}}))
    m.attach(group)
    if op.get("ref"):
        refs = result.pop("_refs")
        refs[op["ref"]] = group["nodeID"]
    else:
        result.pop("_refs", None)
    return {"op": op["op"], "id": group["nodeID"], "ids": [n["nodeID"] for n in nodes], "name": name,
            **({'model_ids': [n['nodeID'] for n in model_nodes]} if model_nodes else {}), **result}


def add_water_body(project, m, op, refs):
    _keys(op, {"kind", "bounds", "mins", "maxs", "water_level", "depth", "material", "water_style",
               "bed_material", "shore_material", "shore_axis", "shore_side", "shore_width"})
    name = _name(m, op.get("name"), "water")
    kind = op.get("kind", "sea")
    if kind not in {"sea", "river", "lake"}:
        raise OpError("kind must be sea, river or lake")
    lo, hi = _bounds(op.get("bounds", {"mins": op.get("mins"), "maxs": op.get("maxs")}))
    level = lighting._number(op.get("water_level", hi[2]), "water_level", -32760, 32760)
    depth = lighting._number(op.get("depth", 256), "depth", 16, 4096)
    if level - depth - 8 < -32768:
        raise OpError("water bed exceeds map coordinates")
    style = op.get("water_style", "calm")
    styles = {"calm": WATER_MATERIAL, "coast": COAST_WATER_MATERIAL,
              "skybox": SKYBOX_WATER_MATERIAL}
    if style not in styles:
        raise OpError("water_style must be calm, coast or skybox")
    water = _material(project, op.get("material", styles[style]))
    bed = _material(project, op.get("bed_material", WATER_BED_MATERIAL), "bed_material")
    shore = _material(project, op.get("shore_material", SHORE_MATERIAL), "shore_material")
    width = lighting._number(op.get("shore_width", 48), "shore_width", 4, 512)
    axis = op.get("shore_axis")
    side = op.get("shore_side", -1)
    if axis not in {None, "x", "y"} or isinstance(side, bool) or side not in {-1, 1}:
        raise OpError("shore_axis must be x/y or omitted; shore_side must be -1/1")
    # A single upward water face avoids six refractive faces on a thin solid.
    plane = mesh.Mesh()
    plane.face([(lo[0], lo[1], level), (hi[0], lo[1], level),
                (hi[0], hi[1], level), (lo[0], hi[1], level)], water)
    pieces = [(plane, {**VISUAL, "renderToCubemaps": True}),
              (mesh.box([lo[0], lo[1], level - depth - 8], [hi[0], hi[1], level - depth], bed), VISUAL)]
    edges = [axis] if axis else ["x", "y"]
    for edge in edges:
        k = 0 if edge == "x" else 1
        for sign in ([side] if axis else [-1, 1]):
            a, b = [lo[0], lo[1], level - 4], [hi[0], hi[1], level + 2]
            a[k], b[k] = (lo[k] - width, lo[k]) if sign < 0 else (hi[k], hi[k] + width)
            pieces.append((mesh.box(a, b, shore), VISUAL))
    result = {"kind": kind, "bounds": [lo, hi], "water_level": level, "depth": depth,
              "material": water, "water_style": style, "shore_axis": axis, "shore_side": side,
              "note": "visual water/bed/shore; no nav floor, swimming or drowning", "_refs": refs}
    return _commit(project, m, {**op, "op": "add_water_body"}, name, pieces, result)


def _quarter_bounds(m, supplied=None, layout_name=None):
    """Read layout/facade metadata; never put scenery into a deep facade."""
    from .layout import plans
    records = plans(m)
    if layout_name is not None:
        records = [r for r in records if r["name"] == layout_name]
        if len(records) != 1:
            raise OpError(f"layout {layout_name!r}: expected one layout")
    elif len(records) > 1 and supplied is None:
        raise OpError("multiple layouts; supply layout or enclosing bounds")
    boxes, floors = [], []
    for data in records:
        wall = data["wall"]
        for room in data["rooms"].values():
            lo, hi = room["interior"]["mins"], room["interior"]["maxs"]
            boxes.append(([lo[0]-wall, lo[1]-wall, lo[2]],
                          [hi[0]+wall, hi[1]+wall, hi[2]]))
            floors.append(room["floor_z"])
    for node in m.iter_nodes():
        raw = node.get("cs2map_facade")
        if not raw:
            continue
        try:
            data = json.loads(raw)
            if data.get("schema") != 2:
                raise ValueError("requires facade schema 2")
            k = data["axis"]
            if isinstance(k, bool) or k not in (0, 1) or data["normal"][k] not in (-1, 1):
                raise ValueError("requires an XY axis and unit facade normal")
            for house in data["houses"]:
                # Roof eaves extend 24 units, including outside the back wall.
                a, b = [0., 0., data["z0"]], [0., 0., house["top"]+128]
                a[1-k], b[1-k] = house["from"]-24, house["to"]+24
                back = data["plane"]-data["normal"][k]*house["depth"]
                a[k], b[k] = min(back, data["plane"])-24, max(back, data["plane"])+24
                boxes.append(_bounds([a, b], "facade bounds"))
        except (ValueError, KeyError, TypeError, IndexError) as e:
            raise OpError(f"invalid skyline facade metadata on {node.get('nodeID')}: {e}") from None
    if supplied is not None:
        boxes.append(_bounds(supplied))
    if not boxes:
        raise OpError("italy needs bounds or an add_layout quarter first")
    lo = [min(a[k] for a, _ in boxes) for k in range(3)]
    hi = [max(b[k] for _, b in boxes) for k in range(3)]
    return *_bounds([lo, hi]), min(floors) if floors else lo[2]


def _italy_house(box, tint):
    """A closed render-only house: tinted plaster body, hip roof, stone plinth, dark windows."""
    from . import buildings
    (x0, y0, z0), (x1, y1, z1) = box
    top = z0 + (z1 - z0) * 0.84
    body = mesh.box([x0, y0, z0], [x1, y1, top], ITALY_PLASTER)
    roofs, _ = buildings.roof_meshes([x0, y0, top], [x1, y1, top], top,
                                     {'type': 'hip', 'pitch': 22, 'overhang': 14, 'thickness': 6,
                                      'ridge': 'x' if x1 - x0 >= y1 - y0 else 'y'}, ITALY_ROOF, ITALY_ROOF)
    roof = mesh.Mesh()
    for piece in roofs:
        roof.extend(piece)
    details = mesh.box([x0 - 2, y0 - 2, z0], [x1 + 2, y1 + 2, z0 + 40], ITALY_STONE)
    floors = max(1, int((top - z0 - 70) // 128))
    for axis in (0, 1):
        a0, a1 = (y0, y1) if axis == 0 else (x0, x1)
        count = max(1, int((a1 - a0 - 48) // 112))
        step = (a1 - a0) / count
        for side in ((x0, x1) if axis == 0 else (y0, y1)):
            out = -1 if side in (x0, y0) else 1
            for f in range(floors):
                zb = z0 + 64 + f * 128
                for i in range(count):
                    c = a0 + step * (i + 0.5)
                    lo3, hi3 = [0.0, 0.0, zb], [0.0, 0.0, zb + 62]
                    lo3[1 - axis], hi3[1 - axis] = c - 16, c + 16
                    lo3[axis], hi3[axis] = sorted((side - out * 1.0, side + out * 1.5))
                    details.extend(mesh.box(lo3, hi3, ITALY_GLASS))
    return [(body, {**HOUSE_BAKED, "tintColor": (*tint, 255)}), (roof, HOUSE_BAKED), (details, VISUAL)]


def _italy_backdrop(project, m, op, refs, name, lo, hi, floor_z, sky):
    """Three separated scenery rows and a continuous earth mass, no nav floor."""
    from . import visual
    seed = _count(op.get("seed", 7), "seed", -2147483648, 2147483647)
    ring = op.get("ring", [op.get("ring_min", 320), op.get("ring_max", 2400)])
    if not isinstance(ring, (list, tuple)) or len(ring) != 2:
        raise OpError("ring must be [near,far] in world units")
    near = lighting._number(ring[0], "ring_min", 300, 2500)
    far = lighting._number(ring[1], "ring_max", 600, 4096)
    if far-near < 300:
        raise OpError("ring_max must be at least 300 greater than ring_min")
    count = _count(op.get("density", op.get("segments", 6)), "density", 1, 12)
    trees = _count(op.get("trees", 2), "trees", 0, 3)
    height = lighting._number(op.get("height", 1400), "height", 640, 1600)
    ground_z = lighting._number(op.get("ground_z", floor_z-1), "ground_z", -32700, min(32700, floor_z-1))
    sides = op.get("sides", ["-x", "+x", "-y", "+y"])
    if not isinstance(sides, list) or not sides or any(x not in {"-x", "+x", "-y", "+y"} for x in sides):
        raise OpError("sides must be a nonempty subset of -x,+x,-y,+y")
    sides = list(dict.fromkeys(sides))
    for flag in ("clip", "ground", "bell_tower"):
        if not isinstance(op.get(flag, True), bool):
            raise OpError(flag+" must be true or false")
    if not op.get("ground", True):
        raise OpError("italy needs its ground mass; use another backdrop style for independent placements")
    if op.get("silhouettes", "stock") != "stock":
        raise OpError('italy uses stock models; legacy silhouettes="mesh" is available in the other styles')
    if op.get("models") is not None:
        raise OpError("italy selects its verified models; use a legacy backdrop style for explicit models")
    tower_side = op.get("tower_side", "+x" if "+x" in sides else sides[0])
    if tower_side not in sides:
        raise OpError("tower_side must be one of the selected sides")
    ground_mat = _material(project, op.get("ground_material", ITALY_GROUND_MATERIAL), "ground_material")
    if op.get("clip", True):
        _material(project, "materials/tools/toolsplayerclip.vmat")
    rng = random.Random(seed)
    specs, occupied = [], []
    measured = {}
    role_counts = {"building": 0, "cypress": 0, "bell_tower": 0}

    def bounds(model):
        if model not in measured:
            if not checks._asset_exists(project, model):
                raise OpError(f"italy skyline model {model!r} is missing")
            info = visual.model_bounds(project, model)
            if not info:
                raise OpError(f"cannot measure italy skyline model {model!r}")
            measured[model] = info[:2]
        return measured[model]

    def place(model, edge, distance, along, scale, role, row):
        k, t = (0, 1) if edge[1] == "x" else (1, 0)
        yaw = {"-x": 0, "+x": 180, "-y": 90, "+y": 270}[edge]
        # Fronts in ITALY_BUILDINGS were inspected in detail previews (+X).
        if role == "cypress":
            yaw = rng.uniform(0, 360)
        local = visual._transformed(bounds(model), angles=[0, yaw, 0], scales=[scale]*3)
        origin = [0., 0., ground_z-local[0][2]]
        origin[t] = along-(local[0][t]+local[1][t])/2
        origin[k] = (lo[k]-distance-local[1][k] if edge[0] == "-"
                     else hi[k]+distance-local[0][k])
        bound = visual._transformed(bounds(model), origin, [0, yaw, 0], [scale]*3)
        if any(abs(v) > 32768 for p in bound for v in p):
            raise OpError("italy skyline model exceeds +/-32768 units")
        if any(all(bound[0][j] < b[1][j]+16 and bound[1][j] > b[0][j]-16
                   for j in (0, 1)) for b in occupied):
            return False
        occupied.append(bound)
        specs.append(dict(model=model, origin=origin, angles=[0, yaw, 0], scale=scale,
                          role=role, row=row, **({'tint': list(rng.choice(ITALY_TINTS))}
                                                 if role == 'building' else {})))
        role_counts[role] += 1
        return True

    # Landmark first reserves a clear slot, including its roof footprint.
    if op.get("bell_tower", True):
        t = 1 if tower_side[1] == "x" else 0
        target = max(height*1.85, hi[2]-ground_z+700)
        raw = bounds(ITALY_BELL_TOWER)
        tower_scale = target/(raw[1][2]-raw[0][2])
        place(ITALY_BELL_TOWER, tower_side, near+200, (lo[t]+hi[t])/2,
              tower_scale, "bell_tower", 0)
    houses = op.get("houses", "mesh")
    if houses not in ("mesh", "models"):
        raise OpError('houses must be "mesh" (closed houses) or "models" (stock background cards)')
    ring_houses = []
    if houses == "mesh":
        # A continuous ring of attached houses right behind the quarter: from the
        # roofs and from above it reads as more town, not grass between rows.
        q_height = hi[2]-ground_z
        span_out = 420
        for edge in sides:
            k, t = (0, 1) if edge[1] == "x" else (1, 0)
            a = lo[t]-(near+span_out if k == 0 else near)
            b = hi[t]+(near+span_out if k == 0 else near)
            u = a
            while u < b-60:
                w = min(rng.uniform(180, 340), b-u)
                depth = rng.uniform(260, span_out)
                box_lo, box_hi = [0., 0., ground_z], [0., 0., ground_z+rng.uniform(.55, .85)*q_height]
                box_lo[t], box_hi[t] = u, u+w
                if edge[0] == "-":
                    box_lo[k], box_hi[k] = lo[k]-near-depth, lo[k]-near
                else:
                    box_lo[k], box_hi[k] = hi[k]+near, hi[k]+near+depth
                if not any(all(box_lo[j] < o[1][j] and box_hi[j] > o[0][j] for j in (0, 1)) for o in occupied):
                    ring_houses.append(((box_lo, box_hi), list(rng.choice(ITALY_TINTS))))
                u += w
        occupied.extend(box for box, _ in ring_houses)
        role_counts["ring_house"] = len(ring_houses)
    shift = 520 if ring_houses else 0
    distances = [near+shift, near+shift+(far-near-shift)*.48, far]
    for row, distance in enumerate(distances):
        target_count = max(1, math.ceil(count*(1, .72, .30)[row]))
        for edge in sides:
            k, t = (0, 1) if edge[1] == "x" else (1, 0)
            n = min(target_count, max(1, int((hi[t]-lo[t])/400)))
            step = (hi[t]-lo[t])/n
            for i in range(n):
                centre = lo[t]+step*(i+.5+rng.uniform(-.07, .07))
                model = (ITALY_BUILDINGS[-1] if row == 0 and step < 500 else
                         rng.choice(ITALY_BUILDINGS[:2] if row == 0 else ITALY_BUILDINGS))
                raw = bounds(model)
                factor = (1., .80, .45)[row]*rng.uniform(.91, 1.09)
                scale = height*factor/(raw[1][2]-raw[0][2])
                # Fit the measured tangential span into its slot; keep a gap
                # for trees, and reject all remaining prop/prop intersections.
                yaw = {"-x": 0, "+x": 180, "-y": 90, "+y": 270}[edge]
                rotated = visual._transformed(raw, angles=[0, yaw, 0])
                scale = min(scale, step*.80/(rotated[1][t]-rotated[0][t]), 2.2)
                d = distance+rng.uniform(0, 90)
                for attempt in range(4):
                    if place(model, edge, d+attempt*160, centre, scale, "building", row):
                        break
                for j in range(trees if row < 2 else min(trees, 1)):
                    tree = ITALY_TREES[(i+j+row)%len(ITALY_TREES)]
                    tree_raw = bounds(tree)
                    tree_scale = height*(.87, .75, .54)[row]*rng.uniform(.9, 1.1)/(tree_raw[1][2]-tree_raw[0][2])
                    tree_along = centre+step*(.42 if j == 0 else -.42)
                    for attempt in range(5):
                        if place(tree, edge, d+attempt*160, tree_along, tree_scale, "cypress", row):
                            break

    # Cover the quarter's slab bottoms and every model's actual footprint.
    # An earth skirt extends beyond all rows, so its outer edge is never a
    # visible floating plate from the gameplay anchors.
    outer_lo = [min([lo[k]-far-700]+[b[0][k]-512 for b in occupied]) for k in (0, 1)]
    outer_hi = [max([hi[k]+far+700]+[b[1][k]+512 for b in occupied]) for k in (0, 1)]
    pieces = [(mesh.box(outer_lo+[ground_z-256], outer_hi+[ground_z], ground_mat), VISUAL)]
    for box, tint in ring_houses:
        for piece, options in _italy_house(box, tint):
            pieces.append((piece, {**options, "_scenery": True}))
    if houses == "mesh":
        for spec in specs:
            if spec["role"] == "building":
                box = visual._transformed(bounds(spec["model"]), spec["origin"], spec["angles"], [spec["scale"]]*3)
                for piece, options in _italy_house(box, spec.get("tint") or (230, 200, 160)):
                    pieces.append((piece, {**options, "_scenery": True}))
                spec["mesh"] = True
    if op.get("clip", True):
        top = max(hi[2]+256, ground_z+height*1.6)
        # Place clips just outside the envelope, behind facade bodies. They
        # never cut through the plaza or roof eaves when bounds were supplied
        # too tightly: _quarter_bounds has expanded them using schema 2.
        for edge in sides:
            k = 0 if edge[1] == "x" else 1
            a, b = lo.copy(), hi.copy()
            a[2], b[2] = ground_z-256, top
            a[k], b[k] = ((lo[k]-8, lo[k]) if edge[0] == "-" else (hi[k], hi[k]+8))
            pieces.append((mesh.box(a, b, "materials/tools/toolsplayerclip.vmat"), {"disableShadows": 1}))
    result = dict(kind="add_skyline", style="italy", bounds=[lo, hi], seed=seed,
                  ring_min=near, ring_max=far, density=count, trees=trees,
                  ground_z=ground_z, ground_bounds=[outer_lo, outer_hi],
                  clip=op.get("clip", True), sides=sides, skybox=sky,
                  models=specs, model_roles=role_counts, _refs=refs)
    out = _commit(project, m, {**op, "op": "add_backdrop"}, name, pieces, result, specs)
    if sky is not None:
        ent = next(iter(m.entities("env_sky")), None)
        if ent is None:
            ent = m.new_entity("env_sky", {"targetname": "mcp_sky", "skyname": sky},
                               defaults=project.catalog.defaults("env_sky")); m.attach(ent)
        else:
            ent["entity_properties"].set("skyname", "string", sky)
    return {k: out[k] for k in ("op", "id", "ids", "name", "style", "bounds", "seed", "model_roles")} | {
        "model_count": len(specs), "ground_z": ground_z}


def add_backdrop(project, m, op, refs):
    allowed = {"style", "bounds", "material", "sky", "skybox", "height", "depth", "segments", "sides", "clip", "models", "silhouettes"}
    if op.get("style") == "italy":
        allowed |= {"layout", "seed", "ring", "ring_min", "ring_max", "density", "trees", "bell_tower", "tower_side", "ground", "ground_z", "ground_material", "houses"}
        if "material" in op or "depth" in op:
            raise OpError("italy uses model materials and measured depth; use ground_material for earth")
    _keys(op, allowed)
    name = _name(m, op.get("name"), "backdrop")
    style = op.get("style", "port")
    if style not in BACKDROP_MATERIALS:
        raise OpError("style must be port, industrial, rocky, city or italy")
    if style == 'italy':
        lo, hi, floor_z = _quarter_bounds(m, op.get('bounds'), op.get('layout'))
    else:
        lo, hi = _bounds(op.get("bounds"))
    sky_style = op.get("sky")
    if sky_style is not None and sky_style not in SKYBOXES:
        raise OpError("sky must be day, sunset, night, overcast or fog")
    sky = op.get("skybox", SKYBOXES.get(sky_style))
    if sky is not None:
        _material(project, sky, "skybox")
    if style == "italy":
        return _italy_backdrop(project, m, op, refs, name, lo, hi, floor_z, sky)
    material = _material(project, op.get("material", BACKDROP_MATERIALS[style]))
    metal = _material(project, METAL_SUPPORT_MATERIAL)
    height = lighting._number(op.get("height", hi[2] - lo[2]), "height", 64, 4096)
    depth = lighting._number(op.get("depth", 192), "depth", 32, 1024)
    count = _count(op.get("segments", 6), "segments", 2, 16)
    sides = op.get("sides", ["-x", "+x", "-y", "+y"])
    if not isinstance(sides, list) or not sides or any(x not in {"-x", "+x", "-y", "+y"} for x in sides):
        raise OpError("sides must be a nonempty subset of -x,+x,-y,+y")
    clip = op.get("clip", True)
    if not isinstance(clip, bool):
        raise OpError("clip must be true or false")
    if clip:
        _material(project, "materials/tools/toolsplayerclip.vmat")
    pieces = []
    silhouettes = op.get('silhouettes', 'stock')
    if silhouettes not in {'stock', 'mesh'}:
        raise OpError('silhouettes must be stock or mesh (legacy round-5 shapes)')
    model_specs = op.get("models", [])
    if not isinstance(model_specs, list) or len(model_specs) > 64:
        raise OpError("models must be a list of at most 64 {model,origin,angles?,scale?} entries")
    validated_models = []
    from . import visual
    for item in model_specs:
        if not isinstance(item, dict) or not isinstance(item.get("model"), str):
            raise OpError("each backdrop model must contain a models/...vmdl path")
        model = item["model"].replace("\\", "/")
        if (set(item) - {'model','origin','angles','scale'} or '..' in model.split('/') or ':' in model
                or not model.startswith("models/") or not model.endswith(".vmdl") or not checks._asset_exists(project, model)):
            raise OpError(f"backdrop model {model!r} is not an installed models/...vmdl")
        origin = lighting._vector(item.get("origin"), "models.origin")
        angles = lighting._vector(item.get("angles", [0, 0, 0]), "models.angles")
        scale = lighting._number(item.get("scale", 1), "models.scale", .01, 100)
        info = visual.model_bounds(project, model)
        if not info:
            raise OpError(f'cannot measure backdrop model {model!r}; use a model with compiled render bounds')
        bound = visual._transformed(info[:2], origin, angles, [scale]*3)
        if any(abs(v) > 32768 for p in bound for v in p):
            raise OpError('backdrop model exceeds +/-32768 units')
        if not any(bound[1][k] <= lo[k] or bound[0][k] >= hi[k] for k in (0,1)):
            raise OpError('backdrop models must be entirely outside the playable XY bounds')
        validated_models.append(dict(model=model, origin=origin, angles=angles, scale=scale))
    def box(a, b, mat=material):
        pieces.append((mesh.box(a, b, mat), VISUAL))
    for edge in dict.fromkeys(sides):
        k, t = (0, 1) if edge[1] == "x" else (1, 0)
        sign = -1 if edge[0] == "-" else 1
        fixed = lo[k] - depth if sign < 0 else hi[k] + depth
        step = (hi[t] - lo[t]) / count
        for i in range(count):
            centre = [0., 0., lo[2]]
            centre[k], centre[t] = fixed, lo[t] + (i + .5) * step
            h = height * (.4 + .5 * ((i * 37 + k * 3) % 11) / 10)
            w = min(step * .88, depth * 2)
            a, b = centre.copy(), centre.copy()
            a[k] -= depth/2; b[k] += depth/2
            a[t] -= w/2; b[t] += w/2; b[2] += h
            if silhouettes == 'stock':
                if not model_specs:
                    model = BACKDROP_MODELS[style][i % len(BACKDROP_MODELS[style])]
                    if not checks._asset_exists(project, model):
                        raise OpError(f'backdrop model {model!r} is missing; use silhouettes="mesh" explicitly')
                    info = visual.model_bounds(project, model)
                    if not info:
                        raise OpError(f'cannot measure stock backdrop {model!r}')
                    angles = [0, 0 if k == 0 else 90, 0]
                    if model.endswith('/crane.vmdl'):
                        angles[1] = 90 if k == 0 else 0
                    raw = visual._transformed(info[:2], angles=angles)
                    scale = min(h/(raw[1][2]-raw[0][2]), step*.88/(raw[1][t]-raw[0][t]), 32.)
                    origin = [0.,0.,lo[2]-raw[0][2]*scale]
                    origin[t] = centre[t] - (raw[0][t]+raw[1][t])/2*scale
                    origin[k] = (lo[k]-depth-raw[1][k]*scale if sign < 0
                                 else hi[k]+depth-raw[0][k]*scale)
                    bound = visual._transformed(info[:2], origin, angles, [scale]*3)
                    if any(abs(v) > 32768 for p in bound for v in p):
                        raise OpError('generated backdrop model exceeds +/-32768 units')
                    validated_models.append(dict(model=model,origin=origin,angles=angles,scale=scale))
                continue
            if style == "rocky":
                # Irregular roofline, sloping rock face rather than towers.
                pieces.append((mesh.wedge(a, b, "+x" if i % 2 else "-x", material), VISUAL))
            elif style == "port" and i % 3 == 1:
                # Recognisable distant crane: legs, tower, jib and a hanging cable.
                cw = min(w, 180); z = lo[2] + h
                for dx in (-cw/2, cw/2):
                    ca, cb = [centre[0]-12, centre[1]-12, lo[2]], [centre[0]+12, centre[1]+12, z]
                    ca[t] += dx; cb[t] += dx; box(ca, cb, metal)
                ca, cb = [centre[0]-20, centre[1]-20, z-24], [centre[0]+20, centre[1]+20, z+24]
                ca[t] -= cw; cb[t] += cw * 1.8; box(ca, cb, metal)
                ca, cb = [centre[0]-6, centre[1]-6, z-h*.55], [centre[0]+6, centre[1]+6, z]
                ca[t] += cw*1.65; cb[t] += cw*1.65; box(ca, cb, metal)
            else:
                box(a, b)
                # Roof ledge makes the block read as a warehouse/building.
                box([a[0]-8, a[1]-8, b[2]], [b[0]+8, b[1]+8, b[2]+12], metal)
        if clip:
            ca, cb = lo.copy(), hi.copy()
            ca[k], cb[k] = (lo[k]-8, lo[k]) if sign < 0 else (hi[k], hi[k]+8)
            pieces.append((mesh.box(ca, cb, "materials/tools/toolsplayerclip.vmat"), {"disableShadows": 1}))
    result = {"style": style, "bounds": [lo, hi], "segments": count, "sides": sides, "clip": clip,
              'silhouettes': silhouettes, 'models': validated_models,
              "skybox": sky, "_refs": refs}
    out = _commit(project, m, {**op, "op": "add_backdrop"}, name, pieces, result, validated_models)
    out.pop('models', None)  # complete placements stay in the group metadata
    out['model_count'] = len(validated_models)
    if sky is not None:
        ent = next(iter(m.entities("env_sky")), None)
        if ent is None:
            ent = m.new_entity("env_sky", {"targetname": "mcp_sky", "skyname": sky}, defaults=project.catalog.defaults("env_sky"));m.attach(ent)
        else:
            ent["entity_properties"].set("skyname", "string", sky)
        out["sky_id"] = ent["nodeID"]
    return out


def _room_bounds(m, room, layout=None):
    from .layout import plans
    matches = [(data, data["rooms"][room]) for data in plans(m)
               if (not layout or data["name"] == layout) and room in data["rooms"]]
    if len(matches) != 1:
        raise OpError(f"room {room!r}: found {len(matches)}; supply layout or add_layout first")
    data, item = matches[0]
    lo, hi = item["interior"]["mins"], item["interior"]["maxs"]
    return lo, hi, item["floor_z"] - data["wall"]


def add_supports(project, m, op, refs):
    _keys(op, {"room", "layout", "bounds", "kind", "theme", "material", "base_z", "top_z",
               "width", "depth", "count_x", "count_y"})
    name = _name(m, op.get("name"), "supports")
    if op.get("room"):
        if op.get("bounds") is not None:
            raise OpError("choose room or bounds")
        lo, hi, top_default = _room_bounds(m, op["room"], op.get("layout"))
    else:
        lo, hi = _bounds(op.get("bounds")); top_default = hi[2]
    top = lighting._number(op.get("top_z", top_default), "top_z", -32760, 32760)
    base = lighting._number(op.get("base_z", min(lo[2], 0)), "base_z", -32760, top-8)
    kind = op.get("kind", "columns")
    if kind not in {"columns", "piles", "truss"}:
        raise OpError("kind must be columns, piles or truss")
    from .dressing import palette, tube
    theme = palette(op.get("theme", "industrial"))
    material = _material(project, op.get("material", theme["materials"]["wood" if kind == "piles" else "metal"]))
    nx, ny = _count(op.get("count_x", 2), "count_x", 1, 12), _count(op.get("count_y", 2), "count_y", 1, 12)
    width = lighting._number(op.get("width", 32), "width", 4, 128)
    depth = lighting._number(op.get("depth", width), "depth", 4, 128)
    if width * nx > hi[0]-lo[0] or depth * ny > hi[1]-lo[1]:
        raise OpError("supports too wide or too numerous for footprint")
    def centres(k, count, size):
        return [(lo[k]+hi[k])/2] if count == 1 else [lo[k]+size/2+i*(hi[k]-lo[k]-size)/(count-1) for i in range(count)]
    xs, ys = centres(0, nx, width), centres(1, ny, depth)
    pieces = []
    girder = min(width, depth, 24, (top-base)/3)
    for x in xs:
        for y in ys:
            shape = mesh.cylinder([x, y], min(width,depth)/2, base, top-girder, 12, material) if kind == "piles" else mesh.box(
                [x-width/2,y-depth/2,base],[x+width/2,y+depth/2,top-girder],material)
            pieces.append((shape, None))
    # Girders spread load into all posts and visibly contact the slab underside.
    for y in ys:
        pieces.append((mesh.box([lo[0],y-depth/2,top-girder],[hi[0],y+depth/2,top],material), None))
    if kind == "truss":
        for x in xs:
            pieces.append((mesh.box([x-width/2,lo[1],top-girder],[x+width/2,hi[1],top],material), None))
        for y in (ys[0], ys[-1]):
            for x0, x1 in zip(xs, xs[1:]):
                for a, b in (([x0,y,base+girder],[x1,y,top-girder]),([x0,y,top-girder],[x1,y,base+girder])):
                    # Decorative braces do not turn into steep walkable ramps.
                    # Posts and horizontal load girders retain collision.
                    pieces.append((tube(a,b,girder/4,material,4), VISUAL))
    return _commit(project, m, {**op,"op":"add_supports"}, name, pieces,
                   {"kind":kind,"bounds":[lo,hi],"base_z":base,"top_z":top,"count":[nx,ny],
                    "material":material,"theme":theme["theme"],"_refs":refs})


def backdrop_sky_info(style=None):
    if style is not None and style not in SKYBOXES:
        raise OpError("sky style must be day, sunset, night, overcast or fog")
    return {"ok":True,"skyboxes":{style:SKYBOXES[style]} if style else dict(SKYBOXES),
            "note":"sky materials only; for haze use lighting_preset fog mode=gradient before selecting sky"}
