"""Conservative visual authoring checks for VMAP maps.

The compiler and the game remain the authority for visibility and collision.
This module catches the high-cost mistakes an AI author cannot see in a text
map: floating props, props buried in walls/other props, unattached lamps,
open render meshes and coincident faces.  Unknown stock model bounds are
skipped rather than guessed.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

from . import checks, kv3, resource, structure
from .project import OpError
from .config import PROJECT_ROOT
from .dmx import Element


# These tolerances are deliberately conservative.  A small floor penetration
# is common after compiling an authored prop; a gap over 16 units is visible.
SUPPORT_GAP = 16.0
CONTACT_TOLERANCE = 12.0
INTERSECTION_DEPTH = 12.0
LAMP_CONTACT = 32.0
ROOM_CLEARANCE = 32.0

_HELPER_MATERIALS = (
    "toolsskybox", "toolstrigger", "toolslightmapres", "toolsinvisible",
    "toolsprecipitation", "toolsblockbullets", "postprocess", "nodraw", "water", "sea", "skybox",
)
_DECORATIVE_MODEL = (
    "/decal", "decal/", "lightshaft", "/light", "_glow", "/fx/",
    "particle", "water", "boat", "boat01", "gaspipes", "/pipe",
    "mcp_m/m9_ring/",            # race rings hang in the air on purpose (a drone flies through them)
)
_LAMP_MODEL = ("lamp", "light", "lantern", "fixture", "fluorescent")


def _props(node: Element) -> dict[str, Any]:
    p = node.get("entity_properties")
    return {k: a.value for k, a in p.attrs.items()} if isinstance(p, Element) else {}


def _vec(value: Any, default=(0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    if isinstance(value, str):
        try:
            parts = [float(x) for x in value.replace(",", " ").split()]
            return tuple((parts + list(default))[:3])  # type: ignore[return-value]
        except (TypeError, ValueError):
            return default
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return tuple(float(x) for x in value[:3])
        except (TypeError, ValueError):
            return default
    return default


def _bounds_points(points: list[tuple[float, float, float]]):
    if not points:
        return None
    return (tuple(min(p[i] for p in points) for i in range(3)),
            tuple(max(p[i] for p in points) for i in range(3)))


def _transformed(bounds, origin=(0, 0, 0), angles=(0, 0, 0), scales=(1, 1, 1)):
    """Transform a local AABB to a world-space AABB (all eight corners)."""
    origin = _vec(origin)
    angles = _vec(angles)
    scales = _vec(scales, (1.0, 1.0, 1.0))
    points = []
    for local in itertools.product(*zip(*bounds)):
        q = structure._rotate(tuple(float(local[i]) * scales[i] for i in range(3)), angles)
        points.append(tuple(q[i] + origin[i] for i in range(3)))
    return _bounds_points(points)


def _parse_points(value: Any) -> list[tuple[float, float, float]]:
    out = []
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            out.append(tuple(float(x) for x in value[:3]))
        except (TypeError, ValueError):
            pass
    return out


def _resourceinfo_bounds(text: str, block: str) -> list[tuple[float, float, float]]:
    """Extract bounds from a native resourceinfo KV3 dump."""
    # resourceinfo sometimes places a byte array between the opening brace and
    # the KV3 data.  kv3.loads accepts the usual ``#[..]`` spelling; normalize
    # the rare line-wrapped form emitted by old Workshop Tools builds.
    text = re.sub(r"#\[\s*(?:[0-9A-Fa-f]{2}\s*)*\]", "[]", text)
    try:
        _, doc = kv3.loads(text)
    except Exception:
        return []
    points: list[tuple[float, float, float]] = []
    if block == "MDAT":
        for scene in doc.get("m_sceneObjects", []) or []:
            if not isinstance(scene, dict):
                continue
            points.extend(_parse_points(scene.get("m_vMinBounds")))
            points.extend(_parse_points(scene.get("m_vMaxBounds")))
    elif block == "DATA":
        info = doc.get("m_modelInfo", {}) or {}
        for key in ("m_vHullMin", "m_vHullMax", "m_vViewMin", "m_vViewMax"):
            points.extend(_parse_points(info.get(key)))
    elif block == "PHYS":
        for part in doc.get("m_parts", []) or []:
            shape = part.get("m_rnShape", {}) if isinstance(part, dict) else {}
            for hull in shape.get("m_hulls", []) or []:
                box = (hull.get("m_Hull", {}) if isinstance(hull, dict) else {}).get("m_Bounds", {})
                points.extend(_parse_points(box.get("m_vMinBounds")))
                points.extend(_parse_points(box.get("m_vMaxBounds")))
            for mesh in shape.get("m_meshes", []) or []:
                box = (mesh.get("m_Mesh", {}) if isinstance(mesh, dict) else {})
                points.extend(_parse_points(box.get("m_vMin")))
                points.extend(_parse_points(box.get("m_vMax")))
    return points


def _compiled_model(project, model: str) -> tuple[bytes, str] | None:
    rel = str(model or "").replace("\\", "/")
    if not rel.lower().endswith(".vmdl"):
        return None
    compiled = project.s.game_addon / (rel + "_c")
    if compiled.is_file():
        return compiled.read_bytes(), str(compiled)
    try:
        if project.game.exists(rel + "_c"):
            return project.game.read(rel + "_c"), "vpk:" + rel + "_c"
    except Exception:
        pass
    return None


def model_bounds(project, model: str) -> tuple[tuple[float, float, float], tuple[float, float, float], str] | None:
    """Read render bounds from an installed/owned ``vmdl_c``.

    MDAT is preferred because it describes rendered geometry.  DATA is used
    for models whose MDAT has no scene objects; PHYS is a last resort.  The
    native utility is cached by content digest under ``.local/scratch/Q``.
    """
    rel = str(model or "").replace("\\", "/")
    if not rel.lower().endswith(".vmdl"):
        return None
    cache = getattr(project, "_visual_model_bounds", {})
    if rel in cache:
        return cache[rel]
    got = _compiled_model(project, rel)
    if not got:
        return None
    data, source_file = got
    try:
        blocks = resource.blocks(data)
    except Exception:
        return None
    scratch = PROJECT_ROOT / ".local" / "scratch" / "Q" / "model_bounds"
    scratch.mkdir(parents=True, exist_ok=True)
    tmp = scratch / (hashlib.sha256(data).hexdigest()[:24] + ".vmdl_c")
    if not tmp.exists():
        tmp.write_bytes(data)
    native = project.s.game_bin / "resourceinfo.exe"
    points: list[tuple[float, float, float]] = []
    used = ""
    if native.is_file():
        for block in ("MDAT", "DATA", "PHYS"):
            if block not in blocks:
                continue
            try:
                proc = subprocess.run(
                    [str(native), "-game", str(project.s.cs2_path / "game/csgo"),
                     "-i", str(tmp), "-b", block, "-baremode"],
                    capture_output=True, timeout=20, check=False,
                )
                if proc.returncode:
                    continue
                out = proc.stdout.decode("utf-8-sig", errors="replace")
                got_points = _resourceinfo_bounds(out, block)
            except (OSError, subprocess.SubprocessError):
                got_points = []
            # A zero DATA hull/view is the normal ModelDoc default; do not let
            # it hide useful MDAT/PHYS bounds.
            if got_points and any(abs(x) > 1e-5 for p in got_points for x in p):
                points, used = got_points, block
                break
    if not points:
        # Own models carry the exact authoring bounds in model.json.  This is a
        # safe fallback when resourceinfo is unavailable in a headless test.
        try:
            meta = project.s.content_addon / Path(rel).with_name("model.json")
            payload = json.loads(meta.read_text(encoding="utf-8"))
            b = payload.get("bounds")
            if isinstance(b, list) and len(b) == 2:
                result = (tuple(float(x) for x in b[0]), tuple(float(x) for x in b[1]), "model.json")
                cache[rel] = result
                project._visual_model_bounds = cache
                return result
        except (OSError, ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError):
            pass
        return None
    raw = _bounds_points(points)
    if not raw:
        return None
    # Keep a tiny margin for float rounding at the render hull boundary.
    result = (tuple(v - .05 for v in raw[0]), tuple(v + .05 for v in raw[1]), used)
    cache[rel] = result
    project._visual_model_bounds = cache
    return result


def _model_bounds(project, model: str, origin=None, angles=None, scales=None):
    """Compatibility-shaped helper returning a transformed world AABB.

    ``checks._model_bounds`` historically returned only owned ``model.json``
    bounds.  Q keeps the same call shape for area checks while measuring stock
    resources through :func:`model_bounds` above.
    """
    info = model_bounds(project, model)
    if not info:
        return None
    return _transformed(info[:2], origin, angles, scales)


def _is_helper(node: Element) -> bool:
    if node.get("physicsType") == "none":
        return True
    mats = node.get("meshData", {}).get("materials", [])
    return bool(mats) and all(any(x in str(mat).lower() for x in _HELPER_MATERIALS) for mat in mats)


def _mesh_data(node: Element):
    try:
        return node["meshData"]
    except (KeyError, TypeError):
        return None


def _solid_meshes(m):
    result = []
    for node in m.iter_nodes():
        if node.type != "CMapMesh" or _is_helper(node):
            continue
        bounds = checks._mesh_bounds(node)
        if bounds:
            result.append((node, bounds, list(checks._triangles(node))))
    return result


def _tri_box(tri, box) -> bool:
    """Separating-axis test: does a triangle intersect an axis-aligned box?"""
    c = [(box[0][i] + box[1][i]) / 2 for i in range(3)]
    h = [(box[1][i] - box[0][i]) / 2 for i in range(3)]
    if min(h) <= 0:
        return False
    v = [[p[i] - c[i] for i in range(3)] for p in tri]
    for i in range(3):
        if min(p[i] for p in v) > h[i] or max(p[i] for p in v) < -h[i]:
            return False
    e = [[v[(k + 1) % 3][i] - v[k][i] for i in range(3)] for k in range(3)]
    n = checks._cross(e[0], e[1])
    r = sum(h[i] * abs(n[i]) for i in range(3))
    if abs(checks._dot(n, v[0])) > r:
        return False
    for edge in e:
        for i in range(3):
            axis = checks._cross([float(i == 0), float(i == 1), float(i == 2)], edge)
            if not any(abs(x) > 1e-12 for x in axis):
                continue
            proj = [checks._dot(axis, p) for p in v]
            r = sum(h[k] * abs(axis[k]) for k in range(3))
            if min(proj) > r or max(proj) < -r:
                return False
    return True


def _penetration(entry_bounds, triangles) -> str | None:
    """Why a prop really cuts into a mesh, or None.

    Walls (steep faces) must not cross the prop box shrunk by INTERSECTION_DEPTH.
    Floors and terrain may hold the base: a prop may sink up to max(12, 40% of its
    height) below the surface under its centre (rocks and bushes are set in).
    """
    lo, hi = entry_bounds
    height = hi[2] - lo[2]
    m = INTERSECTION_DEPTH
    inner = ([lo[0] + m, lo[1] + m, lo[2] + m], [hi[0] - m, hi[1] - m, hi[2] - m])
    centre = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, hi[2] + 1]
    floor = []
    for tri in triangles:
        if (min(p[0] for p in tri) > hi[0] or max(p[0] for p in tri) < lo[0] or
                min(p[1] for p in tri) > hi[1] or max(p[1] for p in tri) < lo[1] or
                min(p[2] for p in tri) > hi[2] or max(p[2] for p in tri) < lo[2]):
            continue
        n = checks._cross(checks._sub(tri[1], tri[0]), checks._sub(tri[2], tri[0]))
        length = math.sqrt(checks._dot(n, n)) or 1.0
        if abs(n[2]) / length > 0.6:
            floor.append(tri)
        elif _tri_box(tri, inner):
            return 'wall'
    surface = checks._floor(centre, floor, centre[2] - lo[2] + 4096) if floor else None
    if surface is not None and surface > lo[2] + max(m, 0.4 * height):
        return f'buried {surface - lo[2]:.0f} units'
    return None


def _overlap(a, b, gap=0.0) -> bool:
    return all(a[0][i] < b[1][i] + gap and a[1][i] > b[0][i] - gap for i in range(3))


def _terrain_float_checks(m, props) -> list:
    """Props on a terrain whose base hangs over the slope: the bounding-box bottom is checked against the lowest
    ground under the whole footprint, not only under the origin (rocks on hills floated with their downhill side)."""
    from . import terrain
    out = []
    plans = terrain.plans(m)
    if not plans:
        return out
    for entry in props:
        # Static props only: physics bodies settle, and parented parts (wheels, rotors) move with their body.
        if entry.get("class") != "prop_static" or entry.get("props", {}).get("parentname"):
            continue
        (lo, hi) = entry["bounds"]
        for data in plans:
            (blo, bhi) = data["bounds"]
            if not (blo[0] <= lo[0] and hi[0] <= bhi[0] and blo[1] <= lo[1] and hi[1] <= bhi[1]):
                continue
            pts = [(x, y) for x in (lo[0], (lo[0] + hi[0]) / 2, hi[0]) for y in (lo[1], (lo[1] + hi[1]) / 2, hi[1])]
            ground = [terrain.sample(data, x, y)["z"] for x, y in pts]
            gap = lo[2] - min(ground)
            # Only props that stand on this terrain (their base is near it) and hang more than 8 units somewhere.
            if lo[2] < max(ground) + 24 and gap > 8:
                out.append(checks.problem("warning", "visual_prop_floats_on_slope", entry["id"],
                    f"{entry['model']} at {_centre(entry['bounds'])}: its base hangs {gap:.0f} units above the lowest ground under it",
                    f"Lower it by at least {gap:.0f} units (rocks: also sink ~20% of their height), or use scatter, which grounds props on the footprint."))
            break
    return out


def _depth(a, b) -> tuple[float, float, float]:
    return tuple(min(a[1][i], b[1][i]) - max(a[0][i], b[0][i]) for i in range(3))


def _point_box_distance(point, bounds) -> float:
    d2 = 0.0
    for i, x in enumerate(point):
        if x < bounds[0][i]:
            d2 += (bounds[0][i] - x) ** 2
        elif x > bounds[1][i]:
            d2 += (x - bounds[1][i]) ** 2
    return math.sqrt(d2)


def _centre(bounds):
    return tuple(round((bounds[0][i] + bounds[1][i]) / 2, 1) for i in range(3))


def _ray_floor(point, triangles, max_drop=4096):
    return checks._floor(point, triangles, max_drop)


def _prop_entries(project, m):
    entries = []
    scenery = scenery_ids(m)
    for ent in m.entities():
        if ent.get('nodeID') in scenery:
            continue
        props = _props(ent)
        cls = props.get("classname", "")
        if cls not in {"prop_static", "prop_dynamic", "prop_physics", "prop_physics_override"}                 or checks.hidden_helper(props):
            continue
        model = str(props.get("model", ""))
        if not model:
            continue
        info = model_bounds(project, model)
        if not info:
            continue
        local, source = (info[0], info[1]), info[2]
        world = _transformed(local, ent.get("origin"), ent.get("angles"), ent.get("scales", (1, 1, 1)))
        if world:
            entries.append({"node": ent, "id": ent.get("nodeID"), "model": model,
                            "bounds": world, "local_bounds": local, "source": source,
                            "class": cls, "props": props})
    return entries


def scenery_ids(m):
    """Explicit outdoor backdrop intent; never infer it from a model name."""
    out = set()
    for node in m.iter_nodes():
        if node.get('cs2map_scenery') == 'backdrop':
            out.add(node.get('nodeID'))
        raw = node.get('cs2map_outdoor')
        if not raw:
            continue
        try:
            if json.loads(raw).get('kind') == 'add_backdrop':
                out.update(n.get('nodeID') for n in m.iter_nodes(node))
        except (ValueError, TypeError):
            continue
    return out


def playable_bounds(m):
    """Core integration helper: visible solids, excluding distant clip/scenery."""
    scenery = scenery_ids(m)
    return checks._map_bounds([(n,b) for n,b in checks._all_meshes(m)
        if n.get('nodeID') not in scenery and checks._is_solid_mesh(n)
        and not all('tools' in str(mat).lower() for mat in n['meshData'].get('materials',[]))])


def _visible_meshes(m):
    """Render geometry includes non-solid sign plates, but excludes tool faces."""
    out = []
    scenery = scenery_ids(m)
    for n,b in checks._all_meshes(m):
        mats = [str(x).lower() for x in n['meshData'].get('materials',[])]
        if n.get('nodeID') in scenery or all('tools' in x or any(h in x for h in _HELPER_MATERIALS) for x in mats):
            continue
        out.append((n,b,list(checks._triangles(n))))
    return out


ZFIGHT_GAP = 0.25        # same-facing faces closer than this flicker against each other
ZFIGHT_AREA = 16.0       # square units of overlap worth reporting
ZFIGHT_LIMIT = 20


def _faces(node):
    """(polygon points, material) of every face of a mesh in world space."""
    md = node['meshData']
    vertices = checks._vertices(node)
    dest, nxt = md['edgeVertexIndices'], md['edgeNextIndices']
    mats = [str(x).lower() for x in md.get('materials', [])]
    index = []
    face_data = md.get('faceData')
    for st in (face_data.get('streams', []) if face_data is not None else []):
        if str(st.get('name', '')).startswith('materialindex'):
            index = st['data']
    data_of = md.get('faceDataIndices') or []
    for fi, start in enumerate(md['faceEdgeIndices']):
        points, edge, seen = [], start, set()
        while edge not in seen and 0 <= edge < len(dest):
            seen.add(edge)
            points.append(vertices[dest[edge]])
            edge = nxt[edge]
        k = data_of[fi] if fi < len(data_of) else fi
        mat = mats[index[k]] if k < len(index) and 0 <= index[k] < len(mats) else (mats[0] if mats else '')
        if len(points) >= 3:
            yield points, mat


def _plane(points):
    """Newell normal (unit) and plane distance, or None for degenerate faces."""
    n = [0.0, 0.0, 0.0]
    for a, b in zip(points, points[1:] + points[:1]):
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    length = math.sqrt(sum(x * x for x in n))
    if length < 1e-6:
        return None
    n = [x / length for x in n]
    return n, sum(n[i] * points[0][i] for i in range(3))


def _clip(subject, clipper):
    """Sutherland-Hodgman clip of a 2D polygon by a convex 2D polygon (both counter-clockwise)."""
    out = subject
    for (ax, ay), (bx, by) in zip(clipper, clipper[1:] + clipper[:1]):
        if not out:
            break
        inp, out = out, []

        def side(pt):
            return (bx - ax) * (pt[1] - ay) - (by - ay) * (pt[0] - ax)
        for a, b in zip(inp, inp[1:] + inp[:1]):
            sa, sb = side(a), side(b)
            if sa >= 0:
                out.append(a)
            if (sa >= 0) != (sb >= 0) and sa != sb:
                f = sa / (sa - sb)
                out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    return out


def _area(poly):
    return 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))


def _inside2(point, poly):
    """Point in a convex 2D polygon of either winding."""
    sign = 0
    for a, b in zip(poly, poly[1:] + poly[:1]):
        c = (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
        if abs(c) < 1e-9:
            continue
        if sign and (c > 0) != (sign > 0):
            return False
        sign = c
    return True


def _pressed(buckets, n, d, d2, u, v, shared):
    """Is the overlap covered by an opposite-facing face lying on (or just in front of) both faces?

    Such faces (a skin's back against its wall, an end cap against a neighbour) are never visible.
    """
    key = tuple(round(-x, 2) for x in n)
    cu = sum(q[0] for q in shared) / len(shared)
    cv = sum(q[1] for q in shared) / len(shared)
    for od, _, pts, _, _ in buckets.get(key, []):
        # the opposite face's plane along n is -od; it must sit within 0.3 in front of the nearer face
        if not d2 - 0.05 <= -od <= d2 + 0.3:
            continue
        poly = [(sum(q[k] * u[k] for k in range(3)), sum(q[k] * v[k] for k in range(3))) for q in pts]
        if _inside2((cu, cv), poly):
            return True
    return False


def _buried(point, visible, skip):
    """Is a point inside a closed mesh other than the ``skip`` nodes?"""
    for node, bounds, tris in visible:
        if node.get('nodeID') in skip or not bounds or not checks._inside(point, bounds):
            continue
        if checks._inside_mesh(point, tris):
            return True
    return False


def _zfight_checks(m, visible):
    """Faces of different meshes that lie in one plane, face the same way and overlap."""
    buckets = {}
    for node, _, _ in visible:
        for points, mat in _faces(node):
            if 'tools' in mat or 'water' in mat or any(h in mat for h in _HELPER_MATERIALS):
                continue
            plane = _plane(points)
            if plane is None:
                continue
            n, d = plane
            buckets.setdefault(tuple(round(x, 2) for x in n), []).append((d, node.get('nodeID'), points, n, mat))
    found, pairs = [], set()
    for faces in buckets.values():
        faces.sort(key=lambda f: f[0])
        for i, (d, nid, pts, n, mat) in enumerate(faces):
            for d2, nid2, pts2, _, mat2 in faces[i + 1:]:
                if d2 - d > ZFIGHT_GAP:
                    break
                pair = (min(nid, nid2), max(nid, nid2))
                # Same material with our world-projected UVs draws the same pixels: no visible flicker.
                if nid2 == nid or pair in pairs or mat2 == mat:
                    continue
                u = structure._cross(n, (0, 0, 1) if abs(n[2]) < 0.9 else (1, 0, 0))
                ul = math.sqrt(sum(x * x for x in u))
                u = [x / ul for x in u]
                v = structure._cross(n, u)
                a = [(sum(q[k] * u[k] for k in range(3)), sum(q[k] * v[k] for k in range(3))) for q in pts]
                b = [(sum(q[k] * u[k] for k in range(3)), sum(q[k] * v[k] for k in range(3))) for q in pts2]
                if _area(a) < 0:
                    a = a[::-1]
                if _area(b) < 0:
                    b = b[::-1]
                shared = _clip(a, b)
                overlap = abs(_area(shared)) if len(shared) >= 3 else 0.0
                if overlap >= ZFIGHT_AREA and _pressed(buckets, n, d, d2, u, v, shared):
                    continue          # both faces are pressed against an opposite face: never seen
                if overlap >= ZFIGHT_AREA:
                    su = sum(q[0] for q in shared) / len(shared)
                    sv = sum(q[1] for q in shared) / len(shared)
                    c = tuple(round(n[k] * d + u[k] * su + v[k] * sv, 1) for k in range(3))
                    if _buried(tuple(c[k] + n[k] * 0.5 for k in range(3)), visible, {nid, nid2}):
                        continue      # both faces are inside a third body (a post's foot in the floor): never seen
                    pairs.add(pair)
                    gap = d2 - d
                    effect = (f'{gap:.2f} units apart: they flicker' if gap > 1e-3
                              else 'exactly coplanar: one hides the other or they flicker')
                    found.append((gap > 1e-3, overlap, checks.problem('warning', 'visual_z_fighting', nid,
                        f'meshes {nid} and {nid2} overlap in one plane near {c} '
                        f'({mat.rsplit("/", 1)[-1]} vs {mat2.rsplit("/", 1)[-1]}, {overlap:.0f} sq units, {effect})',
                        'Join them edge to edge, sink one at least 1 unit behind the other, or remove the duplicate; '
                        'use decals or sign_create for labels and stains.')))
    found.sort(key=lambda f: (not f[0], -f[1]))       # near-coplanar first, then the largest
    return [f[2] for f in found[:ZFIGHT_LIMIT]]


FLAT_MODEL = 4.0          # a model this thin is a pane, panel or sheet
FLAT_GAP = 0.3            # ...and lying this close to a surface it flickers against it


def _flat_model_checks(props, visible, limit=ZFIGHT_LIMIT):
    """Thin models (glass panes, shutters, sheets) lying in the plane of a mesh face.

    Their visible side is in that plane, so they z-fight with the surface behind
    (white curtains flickering in dark window niches).  Keep them >= 1 unit off.
    """
    found = []
    for entry in props:
        lo, hi = entry["bounds"]
        size = [hi[i] - lo[i] for i in range(3)]
        axis = min((0, 1, 2), key=lambda i: size[i])
        if size[axis] > FLAT_MODEL or axis == 2:
            continue        # walls and niches; floor covers and drains have their back face down
        u, v = [i for i in range(3) if i != axis]
        rect = [(lo[u], lo[v]), (hi[u], lo[v]), (hi[u], hi[v]), (lo[u], hi[v])]
        area = size[u] * size[v]
        if area < 16:
            continue
        hit = None
        for node, b, tris in visible:
            if any(b[0][i] > hi[i] + 1 or b[1][i] < lo[i] - 1 for i in range(3)):
                continue
            for tri in tris:
                n = structure._cross(checks._sub(tri[1], tri[0]), checks._sub(tri[2], tri[0]))
                ln = math.sqrt(sum(x * x for x in n))
                if not ln or abs(n[axis]) / ln < 0.99:
                    continue
                plane = tri[0][axis]
                # The surface must face the model: a sheet on top of it, not behind its back.
                if n[axis] > 0 and abs(plane - lo[axis]) <= FLAT_GAP:
                    pass
                elif n[axis] < 0 and abs(plane - hi[axis]) <= FLAT_GAP:
                    pass
                else:
                    continue
                poly = [(q[u], q[v]) for q in tri]
                if _area(poly) < 0:
                    poly = poly[::-1]
                shared = _clip(poly, rect)
                if len(shared) >= 3 and abs(_area(shared)) >= 0.25 * area:
                    hit = node
                    break
            if hit is not None:
                break
        if hit is not None:
            found.append(checks.problem("warning", "visual_flat_model_on_surface", entry["id"],
                f"{entry['model']} at {_centre(entry['bounds'])} lies in the plane of mesh {hit.get('nodeID')} "
                f"(it is {size[axis]:.1f} units thin): the two flicker",
                "Move the model at least 1 unit off the surface (deepen the niche or offset the model)."))
            if len(found) >= limit:
                break
    return found


def _mount_of(project, entry):
    """Recorded mount of a placed model: kit part first, then prop atlas notes."""
    node = entry.get('node')
    key = node.get('cs2map_kit_part') if node is not None else None
    if key:
        try:
            from . import kits
            return kits.part(project, key).get('mount')
        except Exception:
            pass
    try:
        from . import atlas
        rec = atlas.notes_all().get(entry['model'].lower())
        return rec.get('mount') if rec else None
    except Exception:
        return None


def _wall_mount_checks(project, props, visible):
    """Wall parts (signs, wall lamps, shutters) must have a steep surface right behind them."""
    problems = []
    for entry in props:
        if _mount_of(project, entry) != 'wall':
            continue
        lo, hi = entry['bounds']
        # Windows sit in openings wider than the frame: look 16 units around for the wall.
        probe = ([lo[0] - 16, lo[1] - 16, lo[2] - 8], [hi[0] + 16, hi[1] + 16, hi[2] + 8])
        backed = False
        for _, bound, tris in visible:
            if not _overlap(probe, bound):
                continue
            for tri in tris:
                n = checks._cross(checks._sub(tri[1], tri[0]), checks._sub(tri[2], tri[0]))
                length = math.sqrt(checks._dot(n, n)) or 1.0
                if abs(n[2]) / length < 0.35 and _tri_box(tri, probe):
                    backed = True
                    break
            if backed:
                break
        if not backed:
            problems.append(checks.problem('warning', 'visual_wall_part_off_wall', entry['id'],
                f"{entry['model']} at {_centre(entry['bounds'])} is a wall part with no wall within 16 units",
                'Put it on a wall (add_kit_part with a layout wall point or facing), or use a floor part instead.'))
    return problems


def _worldtext_checks(m, visible):
    problems=[]
    for ent in m.entities('point_worldtext'):
        ep=_props(ent)
        if str(ep.get('reorient_mode','0')) != '0':
            continue  # intentional camera-facing story hints
        point=_vec(ent.get('origin')); angles=_vec(ent.get('angles'))
        normal=structure._rotate((0,0,1), angles)
        behind=tuple(-x for x in normal)
        candidates=[ts for _,b,ts in visible if _point_box_distance(point,b)<=33]
        backed=any((t:=checks._ray(point,behind,tri)) is not None and t<=32
                   for ts in candidates for tri in ts)
        if not backed:
            problems.append(checks.problem('warning','visual_worldtext_unbacked',ent.get('nodeID'),
                f"point_worldtext {ent.get('nodeID')} at {tuple(round(x,1) for x in point)} has no visible backing within 32 units behind its text plane",
                'Place a sign plate or wall 0.1..32 units behind the text; check its angles. Camera-facing story hints are excluded.'))
    return problems


def _foundation_checks(m, visible, solids):
    """Bounded visibility samples under layout ground slabs in outdoor scenes.

    An empty region is actionable only if its underside is visible from a
    gameplay anchor, there is no lower ground/water there, and the slab is
    not an interior upper storey.  This is a warning, not a renderer claim.
    """
    rooms = _layout_rooms(m)
    if not any(meta.get('outdoor') and not meta.get('in') for _,_,meta in rooms):
        return []
    waters=[]
    for n in m.iter_nodes():
        try:
            d=json.loads(n.get('cs2map_outdoor','{}'))
            if d.get('kind') in {'sea','river','lake'}:
                waters.append(d)
        except (TypeError,ValueError):
            pass
    anchors=[_vec(e.get('origin')) for e in m.entities()
             if _props(e).get('classname') in {'info_player_terrorist','info_player_counterterrorist','point_nav_walkable'}]
    anchors += [( (b[0][0]+b[1][0])/2, (b[0][1]+b[1][1])/2, b[0][2])
                for _,b,meta in rooms if meta.get('outdoor') and not meta.get('in')]
    triangles=[tri for _,_,ts in visible for tri in ts]
    problems=[]
    for n,b,ts in solids:
        sx,sy,sz=(b[1][i]-b[0][i] for i in range(3))
        if sz>32 or sx*sy<4096:
            continue
        if not any(not meta.get('in') and abs(b[1][2]-rb[0][2])<=1
                   and _overlap((b[0][:2]+(-1e6,), b[1][:2]+(1e6,)),
                                (rb[0][:2]+(-1e6,),rb[1][:2]+(1e6,))) for _,rb,meta in rooms):
            continue
        samples=[((b[0][0]+b[1][0])/2,b[0][1]+8),((b[0][0]+b[1][0])/2,b[1][1]-8),
                 (b[0][0]+8,(b[0][1]+b[1][1])/2),(b[1][0]-8,(b[0][1]+b[1][1])/2)]
        for x,y in samples:
            target=(x,y,b[0][2]-32)
            if any(d['bounds'][0][0]<=x<=d['bounds'][1][0] and d['bounds'][0][1]<=y<=d['bounds'][1][1]
                   and d['water_level']<b[0][2] for d in waters):
                continue
            other_tris=[tri for other,_,ots in visible if other is not n for tri in ots]
            if checks._floor((x,y,b[0][2]-.5),other_tris,1024) is not None:
                continue
            for a in anchors:
                eye=(a[0],a[1],a[2]+64)
                distance=math.dist(eye,target)
                if not 32<distance<1536:
                    continue
                direction=tuple((target[i]-eye[i])/distance for i in range(3))
                if not any((hit:=checks._ray(eye,direction,tri)) is not None and hit<distance-1 for tri in triangles):
                    problems.append(checks.problem('warning','visual_exposed_foundation',n.get('nodeID'),
                        f"layout ground slab {n.get('nodeID')} has an exposed empty underside at {tuple(round(v,1) for v in target)}, visible from feet {tuple(round(v,1) for v in a)}",
                        'Add a ground mass/skirt below the land, or water and visible supports under a pier; inspect this view in CS2.'))
                    break
            if problems and problems[-1]['id']==n.get('nodeID'):
                break
    return problems


PHANTOM_GAP = 2.0          # a render-only floor this far above the solid floor is walked through
PHANTOM_REACH = 64.0       # ... up to where a player stands on it or steps over it
PHANTOM_BACKING = 8.0      # a render-only wall face needs a solid surface this close behind it
_PHANTOM_SKIP = _HELPER_MATERIALS + ("decal", "lightshaft", "shaft", "overlay", "glow", "fog", "cloud", "tools")


def _render_only_meshes(m):
    """World meshes without collision (physicsType none), excluding helpers, decals and backdrops."""
    scenery = scenery_ids(m)
    out = []

    def visit(parent):
        for n in parent.get("children", []):
            if n.type == "CMapEntity":
                continue          # brush entities: triggers, doors, func_* own their collision
            if n.type == "CMapMesh" and n.get("physicsType") == "none" and n.get("nodeID") not in scenery:
                mats = [str(x).lower() for x in n["meshData"].get("materials", [])]
                if mats and not all(any(h in mat for h in _PHANTOM_SKIP) for mat in mats):
                    out.append(n)
            visit(n)
    visit(m.world)
    return out


def _phantom_checks(m, meshes):
    """Visible floors and walls without collision in the playable rooms (seen, but walked and shot through)."""
    rooms = [b for _, b, meta in _layout_rooms(m)]
    if not rooms:
        bounds = playable_bounds(m)
        rooms = [bounds] if bounds else []
    if not rooms:
        return []
    solid = []
    for _, bound, tris in meshes:
        for tri in tris:
            lo = [min(p[i] for p in tri) for i in range(3)]
            hi = [max(p[i] for p in tri) for i in range(3)]
            solid.append((tri, lo, hi))

    def room_at(point, below=8.0):
        return next((r for r in rooms if r[0][0] <= point[0] <= r[1][0] and r[0][1] <= point[1] <= r[1][1]
                     and r[0][2] - below <= point[2] <= r[1][2]), None)

    problems = []
    for node in _render_only_meshes(m):
        floors = walls = 0
        where = None
        for points, mat in _faces(node):
            if any(h in mat for h in _PHANTOM_SKIP):
                continue
            plane = _plane(points)
            if plane is None:
                continue
            n, _ = plane
            lo = [min(p[i] for p in points) for i in range(3)]
            hi = [max(p[i] for p in points) for i in range(3)]
            c = [sum(p[i] for p in points) / len(points) for i in range(3)]
            if n[2] > 0.7:
                if min(hi[0] - lo[0], hi[1] - lo[1]) < 24:
                    continue          # ledges, sills, plinth tops: nobody stands there
                near = [t for t, a, b in solid if a[0] <= hi[0] and b[0] >= lo[0] and a[1] <= hi[1] and b[1] >= lo[1]
                        and a[2] <= hi[2] + 1]
                samples = [c] + [(lo[0] + (hi[0] - lo[0]) * fx, lo[1] + (hi[1] - lo[1]) * fy,
                                  lo[2] + (hi[2] - lo[2]) * 0.5) for fx in (0.25, 0.75) for fy in (0.25, 0.75)]
                bad = 0
                for x, y, z in samples:
                    if room_at((x, y, z)) is None:
                        continue
                    floor = checks._floor((x, y, z - 0.01), near, 4096)
                    if floor is None or PHANTOM_GAP < z - floor <= PHANTOM_REACH:
                        bad += 1
                if bad >= 3:
                    floors += 1
                    where = where or tuple(round(v, 1) for v in c)
            elif abs(n[2]) < 0.3:
                width = math.hypot(hi[0] - lo[0], hi[1] - lo[1])
                if width < 32 or room_at(c) is None:
                    continue
                near = [t for t, a, b in solid if a[0] <= hi[0] + 64 and b[0] >= lo[0] - 64
                        and a[1] <= hi[1] + 64 and b[1] >= lo[1] - 64]
                floor = checks._floor((c[0], c[1], lo[2] + 1), near, 512)
                base = floor if floor is not None else lo[2]
                if min(hi[2], base + 72) - max(lo[2], base) < 48:
                    continue          # above head height or too low to block anyone
                # Backed on either side: a skin in front of a wall, or the back face of one pressed against it.
                probe = (c[0], c[1], max(lo[2], base) + 24)
                if not any((hit := checks._ray(probe, d, t)) is not None and hit <= PHANTOM_BACKING
                           for d in (tuple(-x for x in n), tuple(n)) for t in near):
                    walls += 1
                    where = where or tuple(round(v, 1) for v in c)
        if floors:
            problems.append(checks.problem('error', 'visual_phantom_floor', node.get('nodeID'),
                f"render-only mesh {node.get('nodeID')} shows a floor near {where} that is more than "
                f"{PHANTOM_GAP:g} units above the solid floor: players walk and shoot through it",
                'Give the floor itself the material (add_paving, set_material) or make the slab solid; '
                'never lay render-only slabs on a floor.'))
        elif walls:
            problems.append(checks.problem('error', 'visual_phantom_wall', node.get('nodeID'),
                f"render-only mesh {node.get('nodeID')} shows a wall at player height near {where} with no "
                f"collision within {PHANTOM_BACKING:g} units behind it: players walk and shoot through it",
                'Make the mesh solid (remove physicsType none) or put it on a real wall.'))
    return problems


BUTTON_MODEL='models/props/de_nuke/hr_nuke/rollup_door_001/rollup_door_001_button.vmdl'
AWNING_MODELS={
    'italy': 'models/cs_italy/props/awning/awning_cloth_128_01a.vmdl',
    'dust': 'models/props/de_dust/awning01.vmdl',
}


def add_button_visual(project, m, op, refs):
    """Dress an existing brush button, keeping its original OnPressed I/O."""
    from . import lighting
    from .ext.models import asset_path
    extra=set(op)-{'op','ref','button','face','model','scale'}
    if extra:
        raise OpError('unknown add_button_visual fields: '+', '.join(sorted(extra)))
    ent=project.resolve(m,op.get('button'),refs)
    if _props(ent).get('classname') != 'func_button':
        raise OpError('button must identify an existing func_button')
    if any(n.get('cs2map_button_visual')==str(ent['nodeID']) for n in m.iter_nodes()):
        raise OpError('button already has a visual model')
    model=asset_path(project,op.get('model',BUTTON_MODEL))
    info=model_bounds(project,model)
    if not info:
        raise OpError('button model bounds unavailable')
    scale=lighting._number(op.get('scale',.5),'scale',.01,4)
    children=[n for n in m.iter_nodes(ent) if n.type=='CMapMesh']
    bounds=checks._map_bounds([(n,checks._mesh_bounds(n)) for n in children if checks._mesh_bounds(n)])
    if not bounds:
        raise OpError('button has no brush bounds')
    face=op.get('face','-x')
    yaws={'-x':0,'+x':180,'-y':90,'+y':270}
    if face not in yaws:
        raise OpError('face must be -x,+x,-y,+y (outward from the wall); model front is local -X')
    angles=[0,yaws[face],0]; axis=0 if face[1]=='x' else 1; sign=-1 if face[0]=='-' else 1
    rb=_transformed(info[:2],angles=angles,scales=[scale]*3)
    centre=_centre(bounds); origin=[centre[i]-(rb[0][i]+rb[1][i])/2 for i in range(3)]
    plane=bounds[0][axis] if sign<0 else bounds[1][axis]
    origin[axis]=plane-(rb[1][axis] if sign<0 else rb[0][axis])+sign*.25
    measured=_transformed(info[:2],origin,angles,[scale]*3)
    if any(abs(v)>32768 for p in measured for v in p):
        raise OpError('button visual exceeds world bounds')
    target=str(_props(ent).get('targetname',''))
    if not target:
        raise OpError('button needs a targetname to parent its visual model')
    prop=m.new_entity('prop_dynamic',{'model':model,'solid':'0','parentname':target,
                                     'modelscale':scale,'targetname':target+'_visual'},
                     origin,angles,[scale]*3,defaults=project.catalog.defaults('prop_dynamic'))
    prop.set('cs2map_button_visual','string',str(ent['nodeID']))
    invisible='materials/tools/toolsinvisible.vmat'
    if not checks._asset_exists(project,invisible):
        raise OpError('button invisible material missing')
    for child in children:
        child['meshData']['materials']=[invisible]*len(child['meshData']['materials'])
    m.attach(prop)
    if op.get('ref'): refs[op['ref']]=prop['nodeID']
    return {'op':'add_button_visual','id':prop['nodeID'],'button_id':ent['nodeID'],
            'targetname':target,'model':model,'face':face,'origin':origin,'scale':scale}


def add_control_button(project,m,op,refs):
    """Wrapper proposed for physics.add_button integration; no core mutation."""
    from . import physics
    spec=dict(op); spec['op']='add_button'; style=spec.pop('visual',{})
    if not isinstance(style,dict) or set(style)-{'face','model','scale'}:
        raise OpError('visual must be {face?,model?,scale?}')
    # Check the model before attaching the logical button.
    from .ext.models import asset_path
    asset_path(project,style.get('model',BUTTON_MODEL))
    out=physics.add_button(project,m,spec,refs)
    dressed=add_button_visual(project,m,{'button':out['id'],**style},refs)
    return {**out,'op':'add_control_button','visual':dressed}


def add_cloth_awning(project,m,op,refs):
    """Measured stock fabric canopy stretched to an authored top footprint."""
    from . import lighting
    from .outdoor import _bounds, _material
    style=op.get('style','italy')
    if set(op)-{'op','ref','bounds','style','posts','post_width','material'} or style not in AWNING_MODELS:
        raise OpError('add_cloth_awning fields: bounds, style=italy|dust, posts=true, post_width=6, material?')
    lo,hi=_bounds(op.get('bounds')); model=AWNING_MODELS[style]; info=model_bounds(project,model)
    if not info: raise OpError('stock awning render bounds unavailable')
    posts=op.get('posts',True)
    if not isinstance(posts,bool): raise OpError('posts must be true/false')
    width=lighting._number(op.get('post_width',6),'post_width',2,16)
    from . import mesh
    material=_material(project,op.get('material','materials/wood/milbeams003.vmat'))
    local=info[:2]; sizes=[local[1][i]-local[0][i] for i in range(3)]
    scales=[(hi[0]-lo[0])/sizes[0],(hi[1]-lo[1])/sizes[1],1.]
    if max(scales)>8: raise OpError('awning XY scale exceeds 8; use several canopy modules')
    origin=[lo[0]-local[0][0]*scales[0],lo[1]-local[0][1]*scales[1],hi[2]-local[1][2]]
    if hi[2]-lo[2]<sizes[2]+72: raise OpError('awning hanging edge must leave at least 72 units clearance')
    prop=m.new_entity('prop_static',{'model':model,'solid':'0'},origin,[0,0,0],scales,
                      defaults=project.catalog.defaults('prop_static'))
    nodes=[]
    if posts:
        for x in (lo[0]+width/2,hi[0]-width/2):
            for y in (lo[1]+width/2,hi[1]-width/2):
                nodes.append(m.new_mesh(mesh.box([x-width/2,y-width/2,lo[2]],[x+width/2,y+width/2,hi[2]],material),texture_size=project.texture_size))
    group=m.new_group('visual:cloth_awning'); m.attach(prop,group)
    for n in nodes: m.attach(n,group)
    m.attach(group)
    if op.get('ref'): refs[op['ref']]=group['nodeID']
    return {'op':'add_cloth_awning','id':group['nodeID'],'model_id':prop['nodeID'],
            'ids':[n['nodeID'] for n in nodes], 'model':model,'bounds':[lo,hi],'scales':scales,'posts':posts}


class _FloorGrid:
    """Upward-facing triangles bucketed by 128-unit XY cells: floor rays test only their cell."""
    CELL = 128.0

    def __init__(self, triangles):
        self.cells = {}
        c = self.CELL
        for tri in triangles:
            n = checks._cross(checks._sub(tri[1], tri[0]), checks._sub(tri[2], tri[0]))
            if n[2] <= 1e-6:
                continue
            xs, ys = [p[0] for p in tri], [p[1] for p in tri]
            for i in range(int(math.floor(min(xs) / c)), int(math.floor(max(xs) / c)) + 1):
                for j in range(int(math.floor(min(ys) / c)), int(math.floor(max(ys) / c)) + 1):
                    self.cells.setdefault((i, j), []).append(tri)

    def floor(self, point, max_drop=4096):
        key = (int(math.floor(point[0] / self.CELL)), int(math.floor(point[1] / self.CELL)))
        return checks._floor(point, self.cells.get(key, []), max_drop)


def _has_support(entry, meshes, props, grid=None):
    lo, hi = entry["bounds"]
    grid = grid or _FloorGrid([t for _, _, ts in meshes for t in ts])
    # Five samples make a narrow crate on a broad floor reliable, while still
    # rejecting a prop whose centre happens to touch an unrelated wall.
    samples = [(lo[0], lo[1]), (lo[0], hi[1]), (hi[0], lo[1]), (hi[0], hi[1]),
               ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2)]
    floor = []
    for x, y in samples:
        z = grid.floor((x, y, lo[2] + SUPPORT_GAP), SUPPORT_GAP + 8)
        if z is not None:
            floor.append(z)
    if floor and max(abs(lo[2] - z) for z in floor) <= SUPPORT_GAP:
        return True, "floor"
    for other in [*meshes, *[(None, p["bounds"], []) for p in props if p is not entry]]:
        bound = other[1]
        # Side/ceiling contact: overlap on the two transverse axes and a
        # small gap on the third.  This covers wall lamps and shelf brackets.
        for axis in range(3):
            other_axes = [j for j in range(3) if j != axis]
            if not all(lo[j] < bound[1][j] and hi[j] > bound[0][j] for j in other_axes):
                continue
            if abs(lo[axis] - bound[1][axis]) <= CONTACT_TOLERANCE or abs(hi[axis] - bound[0][axis]) <= CONTACT_TOLERANCE:
                return True, "wall_or_ceiling"
    return False, ""


def _layout_rooms(m):
    rooms = []
    for node in m.iter_nodes():
        raw = node.get("cs2map_layout")
        if not raw:
            continue
        try:
            data = json.loads(raw)
            base = _vec(node.get("origin"))
            origin = _vec(data.get("origin"))
            for name, room in (data.get("rooms") or {}).items():
                inside = room.get("interior")
                if not inside:
                    continue
                lo = _vec(inside.get("mins"), (0, 0, 0)); hi = _vec(inside.get("maxs"), (0, 0, 0))
                delta = tuple(base[i] - origin[i] for i in range(3))
                rooms.append((name, (tuple(lo[i] + delta[i] for i in range(3)),
                                    tuple(hi[i] + delta[i] for i in range(3))), room))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return rooms


def _inside(bounds, room, margin=0):
    return all(bounds[0][i] >= room[0][i] + margin and bounds[1][i] <= room[1][i] - margin for i in range(3))


def _audit(project, m) -> list[dict]:
    problems: list[dict] = []
    meshes = _solid_meshes(m)
    triangles = [tri for _, _, ts in meshes for tri in ts]
    props = _prop_entries(project, m)
    map_bounds = checks._map_bounds([(n, b) for n, b, _ in meshes])
    map_floor = map_bounds[0][2] if map_bounds else -4096
    floor_tops = []
    for mesh_node, mesh_bound, _ in meshes:
        md = _mesh_data(mesh_node)
        mats = [str(x).lower() for x in (md.get("materials", []) if isinstance(md, Element) else [])]
        if any("floor" in mat for mat in mats) and not any(x in mat for mat in mats for x in ("metal", "river", "water")):
            floor_tops.append(mesh_bound[1][2])
    if floor_tops:
        # Water/shore basins may have a deeper concrete floor.  The median of
        # authored floor tops follows the playable floor while ignoring one
        # or two deep scenery slabs.
        ordered = sorted(floor_tops)
        map_floor = ordered[len(ordered) // 2]
    # Switchable light carriers and other helper props are intentionally
    # parked hundreds of units below the authored scene.
    props = [p for p in props if p["bounds"][1][2] >= map_floor - 256]

    visible = _visible_meshes(m)
    problems.extend(_worldtext_checks(m, visible))
    problems.extend(_foundation_checks(m, visible, meshes))
    problems.extend(_zfight_checks(m, visible))
    problems.extend(_flat_model_checks(props, visible))
    problems.extend(_phantom_checks(m, meshes))
    problems.extend(_wall_mount_checks(project, props, visible))
    problems.extend(_terrain_float_checks(m, props))

    # Large authored floors high above the scene need a visible support.  Use
    # the material name as a narrow intent signal so ceilings, sky, water and
    # metal roof plates are not mistaken for floating play platforms.
    for node, bound, _ in meshes:
        md = _mesh_data(node)
        materials = [str(x).lower() for x in (md.get("materials", []) if isinstance(md, Element) else [])]
        sx, sy, sz = (bound[1][i] - bound[0][i] for i in range(3))
        if (sz > 32 or sx * sy < 4096 or bound[0][2] <= map_floor + 32 or
                not any("floor" in mat for mat in materials) or any("metal" in mat for mat in materials)):
            continue
        supported = False
        area = sx * sy
        for other, ob, _ in meshes:
            if other is node or ob[1][2] > bound[0][2] + SUPPORT_GAP:
                continue
            ox = max(0.0, min(bound[1][0], ob[1][0]) - max(bound[0][0], ob[0][0]))
            oy = max(0.0, min(bound[1][1], ob[1][1]) - max(bound[0][1], ob[0][1]))
            if ox * oy / max(area, 1.0) >= 0.02 and bound[0][2] - ob[1][2] <= SUPPORT_GAP:
                supported = True
                break
        if not supported:
            # Ground right under the slab (terrain, a hillside street) also holds it: most of five
            # downward rays must hit another surface no deeper than SUPPORT_GAP under the slab.
            lo, hi = bound
            inset = [(lo[i] + (hi[i] - lo[i]) * f) for i in range(2) for f in (0.15, 0.85)]
            samples = [((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2)] + [(x, y) for x in inset[:2] for y in inset[2:]]
            below = [tri for other, ob, tris in meshes if other is not node and _overlap(ob, bound, SUPPORT_GAP * 2)
                     for tri in tris]
            hits = 0
            for x, y in samples:
                # From the top: ground may also pass through the slab (a plaza set into a slope).
                top = checks._floor((x, y, hi[2] + 0.5), below, hi[2] - lo[2] + SUPPORT_GAP + 0.5)
                hits += top is not None
            supported = hits >= 3
        if not supported:
            problems.append(checks.problem("warning", "visual_floating_platform", node.get("nodeID"),
                f"floor mesh {node.get('nodeID')} at {_centre(bound)} is {bound[0][2] - map_floor:.1f} units above the scene floor without a visible support",
                "Add columns/walls/beams reaching the platform underside, or lower it onto an authored solid floor."))

    # Props must have a nearby floor, wall, ceiling or existing prop support.
    grid = _FloorGrid(triangles)
    for entry in props:
        model_lower = entry["model"].lower()
        if any(token in model_lower for token in _DECORATIVE_MODEL):
            continue
        if entry["bounds"][1][2] < map_floor - 256:
            continue  # switchable-light carriers parked far below the map
        ok, support = _has_support(entry, meshes, props, grid)
        if not ok and entry["bounds"][0][2] > map_floor + SUPPORT_GAP:
            problems.append(checks.problem("warning", "visual_floating_prop", entry["id"],
                f"{entry['model']} at {_centre(entry['bounds'])} has no floor/wall support; bottom is {entry['bounds'][0][2]:.1f} units above the map floor",
                "Move the origin down until the measured model bounds touch a floor, wall or ceiling (gap <= 16 units), or add a real support mesh."))

    # Prop/brush and prop/prop deep intersections.  Base contact with a floor
    # is intentionally accepted; only penetration on two axes is reported.
    for i, entry in enumerate(props):
        for node, bound, tris in meshes:
            if any(token in entry["model"].lower() for token in ("boat", "lamp", "lantern", "gaspipes", "/pipe")):
                continue
            if not _overlap(entry["bounds"], bound):
                continue
            d = _depth(entry["bounds"], bound)
            if min(d) > INTERSECTION_DEPTH:
                why = _penetration(entry["bounds"], tris)
                if why is None:
                    continue
                problems.append(checks.problem("warning", "visual_prop_intersection", entry["id"],
                    f"{entry['model']} at {_centre(entry['bounds'])} cuts into solid mesh {node.get('nodeID')} ({why})",
                    "Move the model out of the wall or up onto the surface; leave only a small mounting contact (<= 12 units)."))
                break
        for other in props[i + 1:]:
            if not _overlap(entry["bounds"], other["bounds"]):
                continue
            # A model set into another on purpose (glass in its frame) carries the host's id.
            if (str(entry["node"].get("cs2map_inset", "")) == str(other["id"])
                    or str(other["node"].get("cs2map_inset", "")) == str(entry["id"])):
                continue
            d = _depth(entry["bounds"], other["bounds"])
            a_center = tuple((entry["bounds"][0][k] + entry["bounds"][1][k]) / 2 for k in range(3))
            b_center = tuple((other["bounds"][0][k] + other["bounds"][1][k]) / 2 for k in range(3))
            nested = (_inside((a_center, a_center), other["bounds"]) or
                      _inside((b_center, b_center), entry["bounds"]))
            if min(d) > 24.0 or nested:
                problems.append(checks.problem("warning", "visual_prop_overlap", entry["id"],
                    f"{entry['model']} at {_centre(entry['bounds'])} is nested in {other['model']} (overlap {tuple(round(x,1) for x in d)} units; ids {entry['id']},{other['id']})",
                    "Separate the two model AABBs or remove the duplicate prop; do not hide one model inside another."))

    # Layout interiors provide a useful semantic signal: a large prop in the
    # centre of a room blocks the AI's intended route, while wall furniture is
    # fine.  Require a central 32-unit corridor overlap and a sizeable prop.
    for name, room, meta in _layout_rooms(m):
        rlo, rhi = room
        cx, cy = (rlo[0] + rhi[0]) / 2, (rlo[1] + rhi[1]) / 2
        for entry in props:
            b = entry["bounds"]
            if not _inside(b, room, 0):
                continue
            central_x = b[0][0] < cx + ROOM_CLEARANCE and b[1][0] > cx - ROOM_CLEARANCE
            central_y = b[0][1] < cy + ROOM_CLEARANCE and b[1][1] > cy - ROOM_CLEARANCE
            size_x, size_y = b[1][0] - b[0][0], b[1][1] - b[0][1]
            if entry["class"].startswith("prop_physics"):
                continue    # bodies that move (cars, crates) are not furniture blocking the route
            if central_x and central_y and max(size_x, size_y) >= 64 and not any(x in entry["model"].lower() for x in _DECORATIVE_MODEL):
                problems.append(checks.problem("warning", "visual_layout_obstruction", entry["id"],
                    f"{entry['model']} at {_centre(entry['bounds'])} occupies the centre of layout room {name!r} ({round(size_x)}×{round(size_y)} units)",
                    "Move the prop against a wall or outside the 64-unit centre corridor; keep a 96-unit route through the room."))

    # Lamps need either a nearby lamp model or a geometric mount.  Light
    # entities are client-side and may have no render model of their own.
    for ent in m.entities():
        cls = _props(ent).get("classname", "")
        if not cls.startswith("light_") or cls in {"light_environment", "light_directional"}:
            continue
        point = _vec(ent.get("origin"))
        target = str(_props(ent).get("targetname", "")).lower()
        if cls in {"light_barn", "light_rect"} and "shaft" in target:
            continue  # add_light(kind=shaft) is intentionally out in the sky
        if cls == "light_rect":
            continue  # tiny disk companion for spot/shaft lights
        if map_bounds and (point[2] < map_floor - 256 or
                           any(point[i] < map_bounds[0][i] - 32 or point[i] > map_bounds[1][i] + 32 for i in range(3))):
            continue
        attached = False
        for entry in props:
            # A light may be mounted on any nearby prop (barrel fire, lamp,
            # switchable fixture), so use proximity rather than naming.
            centre = ((entry["bounds"][0][0] + entry["bounds"][1][0]) / 2,
                      (entry["bounds"][0][1] + entry["bounds"][1][1]) / 2,
                      (entry["bounds"][0][2] + entry["bounds"][1][2]) / 2)
            if math.dist(point[:2], centre[:2]) <= 64 and abs(point[2] - centre[2]) <= 256:
                attached = True
                break
        near_surface = any(_point_box_distance(point, bound) <= LAMP_CONTACT for _, bound, _ in meshes)
        if not near_surface and meshes and min(_point_box_distance(point, bound) for _, bound, _ in meshes) > 128:
            # An outdoor light can be far from walls but still belongs to a
            # playable floor.  Skip only helpers with no floor ray at all.
            if _ray_floor(point, triangles, 2048) is None:
                continue
        if not attached and not near_surface:
            problems.append(checks.problem("warning", "visual_lamp_unmounted", ent.get("nodeID"),
                f"{cls} at {tuple(round(x,1) for x in point)} is more than {LAMP_CONTACT:g} units from a wall/ceiling and has no nearby lamp model",
                "Place the light within 32 units of a solid wall/ceiling or add a lamp model at the same origin."))

    # Open edges are only actionable for visible, collidable authored meshes;
    # water/decals/sky/FX meshes are intentionally open and were filtered.
    for node, bound, _ in meshes:
        md = _mesh_data(node)
        faces = md.get("edgeFaceIndices", []) if isinstance(md, Element) else []
        if any(int(x) < 0 for x in faces):
            mats = [str(x).lower() for x in md.get("materials", [])]
            if not any(any(h in mat for h in _HELPER_MATERIALS) for mat in mats):
                problems.append(checks.problem("warning", "visual_open_mesh", node.get("nodeID"),
                    f"solid mesh {node.get('nodeID')} at {_centre(bound)} has an open boundary edge visible in its authored topology",
                    "Close the mesh with a face, or mark intentional non-solid scenery physicsType=none and ensure the sky/void is visible."))

    # Coplanar duplicates (z-fighting) are found face by face in _zfight_checks.
    return problems


def visual_check(project, name: str) -> dict:
    """Audit a map for conservative visual authoring problems."""
    m = project.load(name)
    problems = _audit(project, m)
    return {"map": name, "ok": not any(p["severity"] == "error" for p in problems),
            "problems": problems,
            "summary": {"errors": sum(p["severity"] == "error" for p in problems),
                        "warnings": sum(p["severity"] == "warning" for p in problems),
                        "models_measured": len(getattr(project, "_visual_model_bounds", {}))}}


def visual_check_loaded(project, m) -> list[dict]:
    """Adapter used by checks.register_check (map is already loaded)."""
    return _audit(project, m)
