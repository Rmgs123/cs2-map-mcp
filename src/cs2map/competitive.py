"""Competitive map authoring, static acceptance checks and route timing.

The extension deliberately keeps the competitive contract in its own module.  It
uses the shared layout and route APIs but does not change them: a plan is saved
as metadata on a small group so later checks/timing calls can recover the
anchors without replaying the authoring request.
"""
from __future__ import annotations

import copy
import json
import math
import re
import time
from typing import Any
from pathlib import Path

from . import checks, game, layout, playtest, route
from .project import OpError

COMP_KEY = "cs2map_competitive"
_COMP_MODES = {"defuse", "hostage", "wingman"}
_TEAM_KEYS = {"t", "terrorist", "ct", "counterterrorist", "counter_terrorist"}
_DEFAULT_MATERIALS = {
    "floor": "materials/concrete/concrete_floor_04.vmat",
    "walls": "materials/concrete/hr_c/hr_concrete_wall_004.vmat",
    "ceiling": "materials/concrete/hr_c/hr_concrete_wall_004.vmat",
}


def _number(value: Any, field: str, low: float = -1e7, high: float = 1e7) -> float:
    if isinstance(value, bool):
        raise OpError(f"{field} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise OpError(f"{field} must be a number") from None
    if not math.isfinite(number) or not low <= number <= high:
        raise OpError(f"{field} must be between {low} and {high}")
    return number


def _vec(value: Any, field: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise OpError(f"{field} must be [x,y,z]")
    return [_number(x, field) for x in value]


def _size(value: Any, field: str, n: int = 2) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != n:
        raise OpError(f"{field} must contain {n} numbers")
    return [_number(x, field, 1, 32768) for x in value]


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", value):
        raise OpError(f"{field} must be 1..64 letters/digits/underscores, starting with a letter/_")
    return value


def _keys(value: dict, allowed: set[str], field: str) -> None:
    extra = set(value) - allowed
    if extra:
        raise OpError(f"unknown {field} fields: {sorted(extra)}")


def _centre(bounds: dict[str, list[float]]) -> list[float]:
    return [(bounds["mins"][i] + bounds["maxs"][i]) / 2 for i in range(3)]


def _bounds(lo: list[float], hi: list[float]) -> dict[str, list[float]]:
    if any(a >= b for a, b in zip(lo, hi)):
        raise OpError("mins must be strictly below maxs on each axis")
    return {"mins": list(lo), "maxs": list(hi)}


def _plan_for(m, name: str | None = None) -> dict:
    plans = layout.plans(m)
    if name:
        plans = [p for p in plans if p.get("name") == name]
    if len(plans) != 1:
        if not plans:
            raise OpError("competitive plan needs one add_layout plan")
        raise OpError("layout name is ambiguous; pass layout:<name>")
    return plans[0]


def _room(m, spec: dict) -> dict:
    try:
        return layout._room_ref(m, spec)  # shared resolver, kept read-only
    except AttributeError as exc:  # defensive if the layout extension changes
        raise OpError("layout room resolver is unavailable") from exc


def _point(m, value: Any, field: str, floor_offset: float = 0.0) -> list[float]:
    if isinstance(value, dict):
        if "room" not in value:
            raise OpError(f"{field} relative point needs room")
        pos, _ = layout.point(m, value)
        pos = list(pos)
        if value.get("z", "floor") == "floor":
            pos[2] += floor_offset
        return pos
    return _vec(value, field)


def _room_point(m, spec: dict, field: str, floor_offset: float = 0.0) -> tuple[list[float], dict | None]:
    point = _point(m, spec, field, floor_offset)
    room = _room(m, spec) if isinstance(spec, dict) and spec.get("room") else None
    return point, room


def _anchor(value: Any, field: str, default_room: dict | None, m) -> tuple[list[float], dict | None]:
    if value is None:
        if default_room is None:
            raise OpError(f"{field} is required (provide a room-relative point or world position)")
        lo, hi = default_room["interior"]["mins"], default_room["interior"]["maxs"]
        # Keep the generated spawn pocket away from the central doorway axis.
        frac_y = 0.15 if field.casefold().startswith("t") else 0.85
        return [(lo[0] + hi[0]) / 2, lo[1] + frac_y * (hi[1] - lo[1]), default_room["floor_z"] + 8], default_room
    if isinstance(value, list) and value and isinstance(value[0], (list, tuple, dict)):
        points = [_point(m, p, field, 8) for p in value]
        return points[0], None
    return _room_point(m, value, field, 8)


def _find_room(plan: dict, names: list[str], used: set[str] | None = None) -> dict | None:
    used = used or set()
    for name in names:
        for rid, room in plan.get("rooms", {}).items():
            if rid.casefold() == name.casefold() and rid not in used:
                return room
    return None


def _rooms_sorted(plan: dict) -> list[dict]:
    return list(plan.get("rooms", {}).values())


def _spawn_grid(anchor: list[float], room: dict | None, count: int, field: str) -> list[list[float]]:
    if count < 10 or count > 16:
        raise OpError("spawn_count must be between 10 and 16")
    if room is None:
        return [[anchor[0] + (i % 5) * 64 - 128, anchor[1] + (i // 5) * 64 - 32, anchor[2]]
                for i in range(count)]
    lo, hi = room["interior"]["mins"], room["interior"]["maxs"]
    cols = (count + 1) // 2
    # Prefer the wider axis; the grid stays inside the room and keeps the 48u
    # player-origin spacing contract with a 64u pitch.
    axis_x = (hi[0] - lo[0]) >= (hi[1] - lo[1])
    if axis_x and hi[0] - lo[0] < cols * 64 + 64:
        axis_x = False
    if not axis_x and hi[1] - lo[1] < cols * 64 + 64:
        axis_x = True
    span = cols * 64 - 64
    if (hi[0] - lo[0] < span + 64) if axis_x else (hi[1] - lo[1] < span + 64):
        raise OpError(f"{field} room is too small for {count} spawns at 64-unit spacing")
    cx, cy = anchor[0], anchor[1]
    # Use an anchor clamped to a safe interior, retaining room-relative intent.
    margin_x, margin_y = (span / 2 + 32, 64) if axis_x else (64, span / 2 + 32)
    if any(hi[i] - lo[i] < 2 * margin for i, margin in enumerate((margin_x, margin_y))):
        raise OpError(f"{field} room cannot fit the spawn grid")
    cx = min(max(cx, lo[0] + margin_x), hi[0] - margin_x)
    cy = min(max(cy, lo[1] + margin_y), hi[1] - margin_y)
    points = []
    for i in range(count):
        j, row = divmod(i, 2)
        if axis_x:
            x = cx + (j - (cols - 1) / 2) * 64
            y = cy + (row - .5) * 64
        else:
            x = cx + (row - .5) * 64
            y = cy + (j - (cols - 1) / 2) * 64
        points.append([round(x, 4), round(y, 4), room["floor_z"] + 8])
    return points


def _site_bounds(m, spec: dict, default_room: dict | None, field: str) -> tuple[dict, list[float], dict | None]:
    if not isinstance(spec, dict):
        raise OpError(f"{field} must be an object")
    _keys(spec, {"id", "room", "layout", "at", "size", "height", "mins", "maxs"}, field)
    room = _room(m, spec) if spec.get("room") else default_room
    if "mins" in spec or "maxs" in spec:
        if "mins" not in spec or "maxs" not in spec:
            raise OpError(f"{field} needs both mins and maxs")
        lo, hi = _vec(spec["mins"], f"{field}.mins"), _vec(spec["maxs"], f"{field}.maxs")
        return _bounds(lo, hi), [(lo[i] + hi[i]) / 2 for i in range(3)], room
    if room is None:
        raise OpError(f"{field} needs room or world mins/maxs")
    at = spec.get("at", [.5, .5])
    if not isinstance(at, (list, tuple)) or len(at) != 2:
        raise OpError(f"{field}.at must be [u,v] fractions 0..1")
    at = [_number(x, f"{field}.at", 0, 1) for x in at]
    size = _size(spec.get("size", [256, 256]), f"{field}.size", 2)
    height = _number(spec.get("height", 128), f"{field}.height", 16, 512)
    rlo, rhi = room["interior"]["mins"], room["interior"]["maxs"]
    centre = [rlo[0] + float(at[0]) * (rhi[0] - rlo[0]), rlo[1] + float(at[1]) * (rhi[1] - rlo[1]), room["floor_z"]]
    lo = [centre[0] - size[0] / 2, centre[1] - size[1] / 2, room["floor_z"]]
    hi = [centre[0] + size[0] / 2, centre[1] + size[1] / 2, room["floor_z"] + height]
    if any(lo[i] < rlo[i] or hi[i] > rhi[i] for i in (0, 1)):
        raise OpError(f"{field} exceeds its room; reduce size or move at")
    return _bounds(lo, hi), centre, room


def _add_entity(p, m, refs, classname: str, origin: list[float], keyvalues: dict | None = None,
                angles: list[float] | None = None, ref: str | None = None) -> dict:
    op = {"op": "add_entity", "classname": classname, "origin": origin, "keyvalues": keyvalues or {}}
    if angles is not None:
        op["angles"] = angles
    if ref:
        op["ref"] = ref
    return p._apply(m, op, refs)


def _add_brush(p, m, refs, classname: str, bounds: dict, keyvalues: dict | None = None,
               material: str | None = None, ref: str | None = None) -> dict:
    op = {"op": "add_brush_entity", "classname": classname, "shape": {"shape": "box", **bounds},
          "keyvalues": keyvalues or {}}
    if material:
        op["material"] = material
    if ref:
        op["ref"] = ref
    return p._apply(m, op, refs)


def _add_nav(p, m, refs, point: list[float], name: str) -> dict:
    return _add_entity(p, m, refs, "point_nav_walkable", point, {"targetname": name})


def _default_layout() -> dict:
    """Three lanes, 192u doors, and ~2.5–3.5ku T approaches (design defaults)."""
    rooms = [{"id": rid, "origin": origin, "size": size, "ceiling": False}
             for rid, origin, size in (
                 ("t", [0, 0, 0], [640, 640, 256]),
                 ("t_hub", [656, 0, 0], [640, 640, 256]),
                 ("mid", [1312, 0, 0], [640, 640, 256]),
                 ("ct_hub", [1968, 0, 0], [640, 640, 256]),
                 ("ct_link", [2624, 0, 0], [960, 640, 256]),
                 ("ct", [3600, 0, 0], [640, 640, 256]),
                 ("t_a", [656, 656, 0], [640, 192, 256]),
                 ("a_long", [1312, 656, 0], [640, 192, 256]),
                 ("a", [1968, 656, 0], [640, 640, 256]),
                 ("t_b", [656, -208, 0], [640, 192, 256]),
                 ("b_long", [1312, -208, 0], [640, 192, 256]),
                 ("b", [1968, -656, 0], [640, 640, 256]))]
    pairs = [("t", "t_hub"), ("t_hub", "mid"), ("mid", "ct_hub"), ("ct_hub", "ct_link"), ("ct_link", "ct"),
             ("t_hub", "t_a"), ("t_a", "a_long"), ("a_long", "a"), ("ct_hub", "a"),
             ("t_hub", "t_b"), ("t_b", "b_long"), ("b_long", "b"), ("ct_hub", "b")]
    # Narrow lane rooms need side margins around their end openings.
    doglegs = {("t", "t_hub"): .25, ("t_hub", "mid"): .75,
               ("mid", "ct_hub"): .25, ("ct_hub", "ct_link"): .75,
               ("ct_link", "ct"): .25}
    return {"wall": 16, "rooms": rooms,
            "openings": [{"between": list(pair), "width": 128 if "long" in " ".join(pair) else 192,
                          "height": 160, **({"at": doglegs[pair]} if pair in doglegs else {})}
                         for pair in pairs]}


def preprocess(p, m, op, refs):
    # Other extensions recursively resolve room-relative points. Our rooms do
    # not exist until add_layout runs, so preserve this request as an opaque
    # JSON string during that preprocessing pass; validate it after unpacking.
    if op.get("op") == "add_comp_plan" and "_competitive_request" not in op:
        return {"op": "add_comp_plan", "_competitive_request": json.dumps(op, allow_nan=False)}
    return op


def _layout_op(op: dict) -> dict:
    raw = op.get("layout")
    if isinstance(raw, dict):
        value = copy.deepcopy(raw)
    elif raw is None:
        value = {}
    else:
        raise OpError("layout must be an object with rooms/openings")
    for key in ("origin", "wall", "materials", "rooms", "openings", "levels"):
        if key in op and key not in value:
            value[key] = copy.deepcopy(op[key])
    value.setdefault("name", f"{op['name']}_layout")
    if "rooms" not in value:
        if value.get("origin") is not None:
            raise OpError("default skeleton is at [0,0,0]; provide layout.rooms to place it elsewhere")
        value = {**_default_layout(), **value}
    value["op"] = "add_layout"
    value.setdefault("materials", copy.deepcopy(_DEFAULT_MATERIALS))
    return value


def _room_choices(plan: dict, mode: str) -> tuple[dict | None, dict | None, dict | None]:
    rooms = _rooms_sorted(plan)
    t = _find_room(plan, ["t", "t_spawn", "terrorist", "terrorist_spawn", "ts"], set())
    ct = _find_room(plan, ["ct", "ct_spawn", "counterterrorist", "counter_terrorist", "cts"], {t.get("id")} if t else set())
    mid = _find_room(plan, ["mid", "middle", "mids"], {x.get("id") for x in (t, ct) if x})
    if t is None and rooms:
        t = rooms[0]
    if ct is None and rooms:
        ct = next((r for r in reversed(rooms) if not t or r["id"] != t["id"]), rooms[-1])
    if mid is None:
        mid = next((r for r in rooms if r["id"] not in {x["id"] for x in (t, ct) if x}), None)
    return t, ct, mid


def add_comp_plan(p, m, op, refs):
    if not isinstance(op, dict):
        raise OpError("add_comp_plan must be an object")
    if set(op) == {"op", "_competitive_request"}:
        op = json.loads(op["_competitive_request"])
    _keys(op, {"op", "name", "mode", "type", "layout", "origin", "wall", "materials", "rooms", "openings",
               "levels", "spawns", "t_spawn", "ct_spawn", "mid", "sites", "hostages", "spawn_count", "ref"},
          "competitive plan")
    name = _id(op.get("name"), "competitive plan name")
    mode = op.get("mode", op.get("type", "defuse"))
    if mode not in _COMP_MODES:
        raise OpError("mode/type must be defuse, hostage or wingman")
    count = int(_number(op.get("spawn_count", 10), "spawn_count", 10, 16))
    if count != _number(op.get("spawn_count", count), "spawn_count", 10, 16):
        raise OpError("spawn_count must be an integer")
    if any(n.get(COMP_KEY) for n in m.iter_nodes() if n.type == "CMapGroup"):
        raise OpError("map already contains a competitive plan; recreate the map before adding another")

    layout_request = _layout_op(op)
    layout_result = layout.add_layout(p, m, layout_request, refs)
    plan = _plan_for(m, layout_request.get("name"))
    t_room, ct_room, mid_room = _room_choices(plan, mode)
    spawns_spec = op.get("spawns") or {}
    if not isinstance(spawns_spec, dict):
        raise OpError("spawns must be an object with t/ct (or terrorist/counterterrorist) anchors")
    _keys(spawns_spec, _TEAM_KEYS, "spawns")
    t_spec = op.get("t_spawn", spawns_spec.get("t", spawns_spec.get("terrorist")))
    ct_spec = op.get("ct_spawn", spawns_spec.get("ct", spawns_spec.get("counterterrorist", spawns_spec.get("counter_terrorist"))))
    t_anchor, t_room_override = _anchor(t_spec, "t_spawn", t_room, m)
    ct_anchor, ct_room_override = _anchor(ct_spec, "ct_spawn", ct_room, m)
    t_room = t_room_override or t_room
    ct_room = ct_room_override or ct_room
    # Explicit arrays are useful for a hand-tuned map; anchors generate a safe grid.
    t_points = [_point(m, x, "spawns.t", 8) for x in t_spec] if isinstance(t_spec, list) and t_spec and isinstance(t_spec[0], (list, tuple, dict)) else _spawn_grid(t_anchor, t_room_override if t_spec is not None else t_room, count, "T spawn")
    ct_points = [_point(m, x, "spawns.ct", 8) for x in ct_spec] if isinstance(ct_spec, list) and ct_spec and isinstance(ct_spec[0], (list, tuple, dict)) else _spawn_grid(ct_anchor, ct_room_override if ct_spec is not None else ct_room, count, "CT spawn")
    if not 10 <= len(t_points) <= 16 or not 10 <= len(ct_points) <= 16:
        raise OpError("each team needs 10..16 spawn points")
    if len(t_points) != len(ct_points):
        raise OpError("T and CT spawn arrays must have equal length")

    prefix = name
    entity_ids: dict[str, list[int]] = {"t_spawns": [], "ct_spawns": [], "buyzones": [], "nav": [], "sites": [], "hostages": []}
    # Spawn placement and buy zones.
    for team, points, team_room, label in ((2, t_points, t_room, "t"), (3, ct_points, ct_room, "ct")):
        for i, point in enumerate(points, 1):
            angle = [0, 0 if team == 2 else 180, 0]
            result = _add_entity(p, m, refs, "info_player_terrorist" if team == 2 else "info_player_counterterrorist", point, {}, angle)
            entity_ids[f"{label}_spawns"].append(result["id"])
        lo = [min(x[0] for x in points) - 96, min(x[1] for x in points) - 96, points[0][2] - 8]
        hi = [max(x[0] for x in points) + 96, max(x[1] for x in points) + 96, points[0][2] + 128]
        zone = _add_brush(p, m, refs, "func_buyzone", _bounds(lo, hi), {"TeamNum": team, "targetname": f"{prefix}_buy_{label}"})
        entity_ids["buyzones"].append(zone["id"])
        _add_nav(p, m, refs, [points[0][0], points[0][1], points[0][2]], f"{prefix}_nav_{label}")
        entity_ids["nav"].append(m.entities(targetname=f"{prefix}_nav_{label}")[0]["nodeID"])

    # Objective rooms/sites.
    raw_sites = op.get("sites")
    sites: list[dict] = []
    if mode in ("defuse", "wingman"):
        if raw_sites is None:
            rooms = _rooms_sorted(plan)
            used = {r["id"] for r in (t_room, ct_room, mid_room) if r}
            a = _find_room(plan, ["a", "site_a", "bombsite_a"], used)
            b = _find_room(plan, ["b", "site_b", "bombsite_b"], used | ({a["id"]} if a else set()))
            candidates = [r for r in rooms if r["id"] not in used]
            chosen = [a or (candidates[0] if candidates else None)]
            if mode == "defuse":
                chosen.append(b or next((r for r in candidates if r != chosen[0]), None))
            if any(r is None for r in chosen):
                raise OpError("plan needs objective rooms or explicit sites:[...]")
            raw_sites = [{"id": chr(65 + i), "room": r["id"], "layout": plan["name"], "at": [.5, .5]} for i, r in enumerate(chosen)]
        if not isinstance(raw_sites, list) or len(raw_sites) != (2 if mode == "defuse" else 1):
            raise OpError("defuse needs two sites; wingman needs one site")
        seen_site = set()
        for i, spec in enumerate(raw_sites):
            if not isinstance(spec, dict):
                raise OpError("each site must be an object")
            sid = _id(spec.get("id", chr(65 + i)), "site id").upper()
            if sid not in {"A", "B"}:
                raise OpError("bomb site id must be A or B")
            if sid in seen_site:
                raise OpError(f"duplicate site id {sid}")
            seen_site.add(sid)
            box, centre, room = _site_bounds(m, spec, None, f"site {sid}")
            ent = _add_brush(p, m, refs, "func_bomb_target", box,
                             {"targetname": f"{prefix}_site_{sid.lower()}", "bomb_site_designation": 0 if sid == "A" else 1})
            entity_ids["sites"].append(ent["id"])
            nav = _add_nav(p, m, refs, [centre[0], centre[1], box["mins"][2] + 8], f"{prefix}_nav_site_{sid.lower()}")
            entity_ids["nav"].append(nav["id"])
            sites.append({"id": sid, "entity_id": ent["id"], "targetname": f"{prefix}_site_{sid.lower()}", "bounds": box, "point": [centre[0], centre[1], box["mins"][2] + 8], "room": room.get("id") if room else None})
    else:
        # Hostage mode has a rescue zone and one or more hostage points.
        rescue_spec = op.get("sites") or [{"id": "rescue", "room": (ct_room or t_room or {}).get("id"), "layout": plan["name"], "at": [.5, .5], "size": [256, 256], "height": 128}]
        if not isinstance(rescue_spec, list) or len(rescue_spec) != 1:
            raise OpError("hostage mode needs sites:[{room, ...}] as the rescue zone")
        box, centre, room = _site_bounds(m, rescue_spec[0], ct_room, "rescue zone")
        ent = _add_brush(p, m, refs, "func_hostage_rescue", box, {"targetname": f"{prefix}_rescue"})
        entity_ids["sites"].append(ent["id"])
        sites.append({"id": "rescue", "entity_id": ent["id"], "targetname": f"{prefix}_rescue", "bounds": box, "point": [centre[0], centre[1], box["mins"][2] + 8], "room": room.get("id") if room else None})
        h_specs = op.get("hostages")
        if h_specs is None:
            h_specs = [{"room": (mid_room or t_room or ct_room or {}).get("id"), "layout": plan["name"], "at": [.5, .5]}]
        if isinstance(h_specs, int) and not isinstance(h_specs, bool) and 1 <= h_specs <= 8:
            h_specs = [{"room": (mid_room or t_room or ct_room or {}).get("id"), "layout": plan["name"], "at": [.5 + (i - h_specs / 2) * .08, .5]} for i in range(h_specs)]
        if not isinstance(h_specs, list) or not 1 <= len(h_specs) <= 8:
            raise OpError("hostages must contain 1..8 point objects")
        for i, hs in enumerate(h_specs, 1):
            hp = _point(m, hs, "hostage point", 8)
            he = _add_entity(p, m, refs, "hostage_entity", hp, {"targetname": f"{prefix}_hostage_{i}"})
            entity_ids["hostages"].append(he["id"])
            sites.append({"id": f"hostage_{i}", "entity_id": he["id"], "targetname": f"{prefix}_hostage_{i}",
                          "point": hp, "room": hs.get("room") if isinstance(hs, dict) else None})
            nv = _add_nav(p, m, refs, hp, f"{prefix}_nav_hostage_{i}")
            entity_ids["nav"].append(nv["id"])

    # Mid is both a semantic anchor for timings and a nav seed.
    if op.get("mid") is not None:
        mid_point, _ = _room_point(m, op["mid"], "mid", 8)
    elif mid_room:
        mid_point = [(mid_room["interior"]["mins"][i] + mid_room["interior"]["maxs"][i]) / 2 for i in (0, 1)] + [mid_room["floor_z"] + 8]
    else:
        mid_point = [(t_anchor[i] + ct_anchor[i]) / 2 for i in range(3)]
    mid_ent = _add_nav(p, m, refs, mid_point, f"{prefix}_nav_mid")
    entity_ids["nav"].append(mid_ent["id"])

    # One seed in every room makes disconnected objective branches discoverable.
    for rid, room in plan.get("rooms", {}).items():
        point = [(room["interior"]["mins"][i] + room["interior"]["maxs"][i]) / 2 for i in (0, 1)] + [room["floor_z"] + 8]
        target = f"{prefix}_nav_room_{rid}"
        if not m.entities(targetname=target):
            nav = _add_nav(p, m, refs, point, target)
            entity_ids["nav"].append(nav["id"])

    all_rooms = list(plan.get("rooms", {}).values())
    pmin = [min(r["interior"]["mins"][i] for r in all_rooms) for i in range(3)]
    pmax = [max(r["interior"]["maxs"][i] for r in all_rooms) for i in range(3)]
    centre = [(pmin[i] + pmax[i]) / 2 for i in range(3)]
    _add_entity(p, m, refs, "info_map_parameters", centre, {"buying": 0, "bombradius": 500})
    _add_entity(p, m, refs, "env_combined_light_probe_volume", centre,
                {"box_mins": [pmin[i] - centre[i] - 128 for i in range(3)],
                 "box_maxs": [pmax[i] - centre[i] + 128 for i in range(3)], "voxel_size": 48})
    lm = p._apply(m, {"op": "add_mesh", "shape": {"shape": "box", "mins": [pmin[0] - 64, pmin[1] - 64, pmin[2] - 32],
                    "maxs": [pmax[0] + 64, pmax[1] + 64, pmax[2] + 32]},
                    "material": "materials/tools/toolslightmapres.vmat", "options": {"physicsType": "none"}}, refs)

    group = m.new_group("competitive:" + name, [0, 0, 0])
    group.set(COMP_KEY, "string", json.dumps({
        "version": 1, "name": name, "mode": mode, "layout": plan["name"],
        "t_spawn": t_points, "ct_spawn": ct_points, "mid": mid_point,
        "sites": sites, "spawn_count": len(t_points), "entity_ids": entity_ids,
        "layout_id": layout_result.get("id"), "mid_id": mid_ent["id"], "lightmap_ids": lm.get("ids", []),
    }, ensure_ascii=False, separators=(",", ":")))
    m.attach(group)
    if op.get("ref"):
        refs[op["ref"]] = group["nodeID"]
    return {"op": "add_comp_plan", "id": group["nodeID"], "name": name, "mode": mode,
            "layout": plan["name"], "spawns": {"t": len(t_points), "ct": len(ct_points)},
            "sites": [{"id": s["id"], "targetname": s["targetname"], "point": s["point"]} for s in sites],
            "nav_seeds": len(entity_ids["nav"]), "lightmap_ids": lm.get("ids", [])}


def _competitive_meta(m) -> list[tuple[Any, dict]]:
    out = []
    for node in m.iter_nodes():
        raw = node.get(COMP_KEY)
        if raw:
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                raise OpError("invalid competitive metadata") from None
            if not isinstance(data, dict):
                raise OpError("invalid competitive metadata")
            out.append((node, _current_anchors(m, data)))
    return out


def _current_anchors(m, stored: dict) -> dict:
    """Checks and timings must follow actual entities after a map_edit move."""
    data = copy.deepcopy(stored)
    ids = data.get("entity_ids", {})
    for label, key in (("t_spawn", "t_spawns"), ("ct_spawn", "ct_spawns")):
        if ids.get(key):
            data[label] = [list(m.node(i).get("origin")) for i in ids[key]]
    if data.get("mid_id"):
        data["mid"] = list(m.node(data["mid_id"]).get("origin"))
    for site in data.get("sites", []):
        found = m.entities(targetname=site["targetname"])
        if not found:
            raise OpError(f"competitive objective {site['targetname']} was removed")
        entity = found[0]
        if m.classname(entity) == "hostage_entity":
            site["point"] = list(entity.get("origin"))
        else:
            lo, hi = playtest._entity_bounds(m, entity)
            site["bounds"] = {"mins": list(lo), "maxs": list(hi)}
            site["point"] = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2] + 8]
    return data


def _path(m, start: list[float], goal: list[float]) -> tuple[list[list[float]], float, bool]:
    points = [list(start)]
    waypoints = route.layout_waypoints(m, start, goal)
    points.extend(waypoints)
    points.append(list(goal))
    distance = sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))
    rooms, _ = route._layout_rooms(m)
    a, b = route._room_of(rooms, start), route._room_of(rooms, goal)
    reachable = a is not None and b is not None and (a == b or bool(waypoints))
    return points, distance, reachable


def _timing_row(m, team: str, start: list[float], objective: str, goal: list[float], speed: float) -> dict:
    points, distance, reachable = _path(m, start, goal)
    return {"team": team, "objective": objective, "distance_units": round(distance, 1),
            "distance_m": round(distance * 0.0254, 2), "seconds": round(distance / speed, 2),
            "speed_units_s": speed, "route": points, "reachable": reachable}


def _suggest(rows: list[dict], objective: str) -> str:
    t = next((r for r in rows if r["team"] == "T" and r["objective"] == objective), None)
    ct = next((r for r in rows if r["team"] == "CT" and r["objective"] == objective), None)
    if not t or not ct:
        return "add both T and CT routes"
    diff = t["seconds"] - ct["seconds"]
    if not t["reachable"] or not ct["reachable"]:
        return "add or widen a layout opening; no connected floor path was found"
    if diff < 3:
        return f"lengthen T path or shorten CT path by about {3 - diff:.1f}s (target CT advantage 3–8s)"
    if diff > 8:
        return f"shorten T path or lengthen CT path by about {diff - 8:.1f}s (target CT advantage 3–8s)"
    if diff < 0:
        return f"CT is {abs(diff):.1f}s slower; shorten its route or add a connector"
    return "within the 3–8s CT advantage target"


def comp_timings(project, map_name: str, timeout: float = 60, speed: float = 250, live: bool = True,
                 offset: int = 0) -> dict:
    speed = _number(speed, "speed", 1, 2000)
    timeout = _number(timeout, "timeout", 5, 90)
    m = project.load(map_name)
    comps = _competitive_meta(m)
    if len(comps) != 1:
        raise OpError("map must contain exactly one add_comp_plan")
    _, data = comps[0]
    starts = {"T": data["t_spawn"], "CT": data["ct_spawn"]}
    goals = [(s["id"], s["point"]) for s in data.get("sites", [])]
    goals.append(("mid", data["mid"]))
    rows = []
    for team in ("T", "CT"):
        for objective, goal in goals:
            rows.append(_timing_row(m, team, starts[team][0], objective, goal, speed))
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset < len(rows):
        raise OpError(f"offset must be 0..{len(rows)-1}")
    source_revision = project.s.map_path(map_name).stat().st_mtime_ns
    cache_path = project.s.logs / f"comp-timings-{map_name}.json"
    cache = {}
    if cache_path.exists():
        try:
            previous = json.loads(cache_path.read_text(encoding="utf-8"))
            if previous.get("source_revision") == source_revision:
                cache = previous.get("physical", {})
        except (ValueError, OSError):
            pass
    if live:
        row = rows[offset]
        item = {"team": row["team"], "objective": row["objective"], "ok": False, "seconds": None}
        if not row["reachable"]:
            item["reason"] = "no_static_path"
        else:
            try:
                deadline = time.monotonic() + 112
                with game.exclusive("C-timings", wait=20):
                    game.ensure_map(project.s, map_name, deadline=min(time.monotonic()+25, deadline-12))
                    game.command(project.s, "sv_cheats 1\nbot_quota 0\nbot_kick\nmp_warmup_end\nmp_freezetime 0\nmp_restartgame 1", wait=2)
                    # Both teams use explicit, saved spawn coordinates before
                    # measurement. Noclip remains off during physical walking.
                    route_timeout = min(timeout, deadline-time.monotonic()-5)
                    if route_timeout < 5:
                        raise OpError("queue/loading consumed the route budget; retry")
                    result = route.route_test(project, map_name,
                        [{"go": row["route"][-1], "radius": 24}], timeout=route_timeout,
                        start=list(starts[row["team"]][0]))
                item.update(_physical_measurement(result))
            except Exception as exc:
                item["reason"] = str(exc)[:180]
        cache[f"{row['team']}:{row['objective']}"] = item
    comparisons = []
    for obj, _ in goals:
        t = cache.get(f"T:{obj}", {})
        ct = cache.get(f"CT:{obj}", {})
        measured = bool(t.get("ok") and ct.get("ok"))
        pair = [{**r, "seconds": cache[f"{r['team']}:{obj}"]["seconds"]} for r in rows
                if r["objective"] == obj] if measured else rows
        diff = next(r["seconds"] for r in pair if r["team"] == "T" and r["objective"] == obj) - next(
            r["seconds"] for r in pair if r["team"] == "CT" and r["objective"] == obj)
        comparisons.append({"objective": obj, "basis": "physical" if measured else "estimate",
                            "ct_advantage_seconds": round(diff, 2),
                            "hint": _suggest(pair, obj) if obj in {"A", "B"} and data["mode"] in {"defuse", "wingman"}
                            else "Review control/rescue balance; the 3–8s CT target applies to bombsites."})
    physical = list(cache.values())
    complete = all(cache.get(f"{r['team']}:{r['objective']}", {}).get("ok") for r in rows)
    details = {"map": map_name, "source_revision": source_revision, "rows": rows, "physical": cache}
    project.s.logs.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8")
    for row in rows:
        row.pop("route")
        row["estimated_seconds"] = row.pop("seconds")
        row.pop("speed_units_s")
    return {"ok": all(r["reachable"] for r in rows) and (complete if live else True),
            "map": map_name, "mode": data["mode"], "weapon_requested": "knife (slot3)",
            "estimate_speed_units_s": speed, "rows": rows, "comparisons": comparisons,
            "physical": physical, "physical_complete": complete,
            "next_offset": offset + 1 if live and offset + 1 < len(rows) else None,
            "log": str(cache_path),
            "note": "One physical route per call; repeat offsets to finish. Seconds exclude setup and include controller pauses/turns. Metres use 0.0254 m/unit."}


def _physical_measurement(result: dict) -> dict:
    item = {k: result.get(k) for k in ("ok", "reason", "reached", "point_count", "log")}
    item.update(seconds=None, distance_m=None)
    if not result.get("ok"):
        return item
    details = json.loads(Path(result["log"]).read_text(encoding="utf-8"))
    samples, completed = details.get("samples", []), details.get("completed", [])
    if not samples or not completed:
        item.update(ok=False, reason="route has no movement samples")
        return item
    began = samples[0]["seconds"]
    ended = completed[-1]["seconds"]
    positions = [s["pos"] for s in samples if began <= s["seconds"] <= ended + .05]
    distance = sum(math.dist(a[:2], b[:2]) for a, b in zip(positions, positions[1:]))
    item.update(seconds=round(max(0, ended-began), 2), distance_m=round(distance * .0254, 2))
    return item


def _site_has_cover(m, site: dict) -> bool:
    if "bounds" not in site:
        return True  # a hostage point is not a bomb/rescue trigger
    lo, hi = site["bounds"]["mins"], site["bounds"]["maxs"]
    expanded = [(lo[0] - 96, hi[0] + 96), (lo[1] - 96, hi[1] + 96), (lo[2] - 8, min(hi[2], lo[2] + 96))]
    for node, bounds in checks._all_meshes(m):
        if not checks._is_solid_mesh(node):
            continue
        blo, bhi = bounds
        if bhi[2] < lo[2] + 40 or blo[2] > lo[2] + 72:
            continue  # floor, ceiling and overhead trim cannot shelter a player
        if all(blo[i] < expanded[i][1] and bhi[i] > expanded[i][0] for i in range(3)):
            # The objective trigger itself is not in _all_meshes as a solid
            # helper, so any hit is an authored wall/cover candidate.
            return True
    return False


def _spawn_area_samples(m, data, points, label):
    """Sample whole spawn rooms and real buy brushes, including doorway axes.

    Bounded sampling is a geometry heuristic, not wall penetration simulation.
    Keep real spawn eyes as well: custom world anchors may be outside rooms.
    """
    plan = _plan_for(m, data.get("layout"))
    areas = []
    for room in plan.get("rooms", {}).values():
        lo, hi = room["interior"]["mins"], room["interior"]["maxs"]
        if any(all(lo[i] <= p[i] <= hi[i] for i in (0, 1)) and
               abs(p[2] - room["floor_z"]) <= 32 for p in points):
            areas.append((lo, hi, room["floor_z"]))
    for entity_id in data.get("entity_ids", {}).get("buyzones", []):
        ent = m.node(entity_id)
        props = ent.get("entity_properties")
        if str(props.get("TeamNum")) == ("2" if label == "T" else "3"):
            lo, hi = playtest._entity_bounds(m, ent)
            areas.append((lo, hi, lo[2]))
    result = {tuple([p[0], p[1], p[2] + 64]) for p in points}
    solids = layout.world_solids(m)
    for lo, hi, floor in areas:
        axes = []
        for axis in (0, 1):
            low, high = lo[axis] + 20, hi[axis] - 20
            if high < low:
                continue
            count = min(9, max(2, math.ceil((high-low)/96)+1))
            values = {low + (high-low)*i/(count-1) for i in range(count)}
            # A narrow straight corridor can lie between uniform grid rows.
            for opening in plan.get("openings", []):
                b = opening.get("bounds", {})
                if b:
                    centre = (b["mins"][axis] + b["maxs"][axis])/2
                    if low <= centre <= high:
                        values.add(centre)
            axes.append(sorted(values))
        if len(axes) != 2:
            continue
        for x in axes[0]:
            for y in axes[1]:
                hull = {"mins": [x-16,y-16,floor+.2], "maxs": [x+16,y+16,floor+72]}
                if not layout.collisions(m, hull, [0,0,0], solids):
                    result.add((x,y,floor+64))
    return sorted(result)


def _area_exposure(m, data, t, ct):
    left = _spawn_area_samples(m, data, t, "T")
    right = _spawn_area_samples(m, data, ct, "CT")
    scene = playtest.CameraGeometry(m)
    # Box rejection avoids running triangle intersections on distant geometry.
    def visible(a, b):
        delta = checks._sub(b,a)
        length = math.sqrt(checks._dot(delta,delta))
        if length < 1:
            return False
        direction = tuple(v/length for v in delta)
        for tri, lo, hi, _ in scene.triangles:
            enter, leave = 0., length
            for i in range(3):
                if abs(direction[i]) < 1e-9:
                    if not lo[i]-.01 <= a[i] <= hi[i]+.01:
                        break
                else:
                    u,v = sorted(((lo[i]-a[i])/direction[i],(hi[i]-a[i])/direction[i]))
                    enter,leave = max(enter,u),min(leave,v)
                    if enter > leave:
                        break
            else:
                hit = checks._ray(a,direction,tri)
                if hit is not None and .1 < hit < length-.1:
                    return False
        return True
    for a in left:
        for b in right:
            if visible(a,b):
                return {"t_eye":list(a),"ct_eye":list(b),"samples":{"t":len(left),"ct":len(right)}}
    return None


def check_competitive(project, m):
    problems = []
    try:
        comps = _competitive_meta(m)
    except Exception as exc:
        return [checks.problem("error", "comp_anchor_missing", None, str(exc), "Restore the removed competitive entity or recreate the plan.")]
    for group, data in comps:
        t, ct = data.get("t_spawn", []), data.get("ct_spawn", [])
        solids = layout.world_solids(m)
        for label, points in (("T", t), ("CT", ct)):
            for i, a in enumerate(points):
                hull = {"mins": [a[0]-16, a[1]-16, a[2]+.1], "maxs": [a[0]+16, a[1]+16, a[2]+72]}
                if layout.collisions(m, hull, [0, 0, 0], solids):
                    problems.append(checks.problem("error", "comp_spawn_in_solid", group.get("nodeID"),
                        f"{label} spawn {i+1} intersects solid geometry", "Move the spawn or remove the blocking mesh."))
                for b in points[i + 1:]:
                    if math.dist(a, b) < 48:
                        problems.append(checks.problem("error", "comp_spawn_spacing", group.get("nodeID"),
                            f"{label} spawns are closer than 48 units", "Move generated or explicit spawns farther apart."))
        # A direct T↔CT line of sight leaves no protected opening phase.
        triangles = [tri for node, _ in checks._all_meshes(m) if checks._is_solid_mesh(node) for tri in checks._triangles(node)]
        for a in t:
            for b in ct:
                delta = checks._sub((b[0], b[1], b[2] + 64), (a[0], a[1], a[2] + 64))
                length = math.sqrt(checks._dot(delta, delta))
                if length < 1:
                    continue
                direction = tuple(v / length for v in delta)
                if not any(checks._ray((a[0], a[1], a[2] + 64), direction, tri) is not None and
                           checks._ray((a[0], a[1], a[2] + 64), direction, tri) < length - 16 for tri in triangles):
                    problems.append(checks.problem("error", "comp_spawn_line_of_sight", group.get("nodeID"),
                        "A T and CT spawn have direct line of sight", "Add a wall/turn or move the protected spawn rooms."))
                    break
            else:
                continue
            break
        exposure = _area_exposure(m, data, t, ct)
        if exposure:
            problems.append(checks.problem("error", "comp_spawn_area_line_of_sight", group.get("nodeID"),
                "T/CT spawn yards or buyzones have direct eye-height sight: " + json.dumps(exposure),
                "Offset successive openings (dog-leg) or add a solid wall between the yards."))
        for site in data.get("sites", []):
            if not _site_has_cover(m, site):
                problems.append(checks.problem("warning", "comp_site_exposed", group.get("nodeID"),
                    f"objective {site['id']} has no nearby authored solid cover", "Add at least one wall/box within 96 units of the site."))
        plan = _plan_for(m, data.get("layout"))
        for opening in plan.get("openings", []):
            if opening.get("width", 0) < 48:
                problems.append(checks.problem("error", "comp_passage_too_narrow", group.get("nodeID"),
                    f"layout opening {opening.get('index')} has insufficient player clearance", "Use width >= 128 for a competitive route."))
            elif opening.get("width", 0) < 128:
                problems.append(checks.problem("warning", "comp_passage_narrow", group.get("nodeID"),
                    f"layout opening {opening.get('index')} is only {opening.get('width')} units wide", "Prefer 128–256 units for a competitive route."))
            elif opening.get("width", 0) > 256:
                problems.append(checks.problem("warning", "comp_passage_wide", group.get("nodeID"),
                    f"layout opening {opening.get('index')} is {opening.get('width')} units wide", "Add a frame/cover or narrow the connector."))
        starts = {"T": data.get("t_spawn", [[0, 0, 0]])[0], "CT": data.get("ct_spawn", [[0, 0, 0]])[0]}
        for site in data.get("sites", []):
            for team, point in starts.items():
                _, _, reachable = _path(m, point, site["point"])
                reachable = reachable and _clear_graph_path(m, point, site["point"], solids)
                if not reachable:
                    problems.append(checks.problem("error", "comp_no_path", group.get("nodeID"),
                        f"{team} has no floor-level layout path to objective {site['id']}",
                        "Connect the rooms with an opening or provide a valid layout graph."))
    return problems


def _clear_graph_path(m, start, goal, solids) -> bool:
    """Remove authored openings obstructed by a player hull, then search again."""
    rooms, edges = route._layout_rooms(m)
    a, b = route._room_of(rooms, start), route._room_of(rooms, goal)
    if a is None or b is None:
        return False
    visited, queue = {a}, [a]
    clear = {}
    while queue:
        key = queue.pop(0)
        if key == b:
            return True
        for nxt, hole in edges.get(key, []):
            identity = tuple(hole["bounds"]["mins"] + hole["bounds"]["maxs"])
            if identity not in clear:
                lo, hi = hole["bounds"]["mins"], hole["bounds"]["maxs"]
                axis = 0 if hi[0]-lo[0] < hi[1]-lo[1] else 1
                centre = [(lo[i]+hi[i])/2 for i in range(3)]
                foot = [centre[0], centre[1], hole["bottom"]+.1]
                foot[axis] -= 40
                hull = {"mins": [foot[0]-16, foot[1]-16, foot[2]],
                        "maxs": [foot[0]+16, foot[1]+16, foot[2]+72]}
                delta = [0,0,0]; delta[axis] = 80
                clear[identity] = hole["width"] >= 48 and hole["height"] >= 72 and not layout.collisions(m, hull, delta, solids)
            if clear[identity] and nxt not in visited:
                visited.add(nxt); queue.append(nxt)
    return False


def _cvar_values(lines: list[str], keys: list[str]) -> dict:
    values = {}
    for key in keys:
        pattern = re.compile(r'\b' + re.escape(key) + r'"?\s*(?:=\s*)?"?(-?(?:\d+(?:\.\d*)?|\.\d+)|true|false)\b', re.I)
        for line in reversed(lines):
            match = pattern.search(line)
            if match:
                token = match[1].lower()
                values[key] = {"true": "1", "false": "0"}.get(token, token)
                break
        if key not in values:
            raise OpError(f"cannot read {key}; round-clock configuration refused")
    return values


def comp_playtest(project, map_name: str, bots: int = 10, seconds: int = 60,
                  round_seconds: int | None = None, spectate: bool = True) -> dict:
    """Run the shared bounded bot test and add competitive acceptance fields."""
    if isinstance(bots, bool) or not isinstance(bots, int) or not 0 <= bots <= 32:
        raise OpError("bots must be an integer 0..32")
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 90:
        raise OpError("seconds must be an integer 1..90")
    if round_seconds is not None and (isinstance(round_seconds, bool) or not isinstance(round_seconds, int) or not 20 <= round_seconds <= 120):
        raise OpError("round_seconds must be an integer 20..120 or null")
    deadline = time.monotonic() + 112
    with game.exclusive("C-playtest", wait=20):
        load_deadline = min(time.monotonic()+25, deadline-seconds-12)
        game.ensure_map(project.s, map_name, deadline=load_deadline)
        game.command(project.s, route.RELEASE + "\nnoclip 0", wait=.25)
        previous = {}
        if round_seconds is not None:
            keys = ["mp_roundtime", "mp_roundtime_defuse", "mp_roundtime_hostage", "mp_ignore_round_win_conditions"]
            lines = game.query(project.s, "\n".join(keys)+"\necho C_COMP_CVARS_READ", "C_COMP_CVARS_READ", timeout=3)
            previous = _cvar_values(lines, keys)
        try:
            if time.monotonic()+seconds+10 > deadline:
                raise OpError("queue/loading consumed the playtest budget; retry or use fewer seconds")
            if previous:
                game.command(project.s, f"mp_roundtime {round_seconds/60:g}\nmp_roundtime_defuse {round_seconds/60:g}\nmp_roundtime_hostage {round_seconds/60:g}\nmp_ignore_round_win_conditions 0", wait=.3)
            result = playtest.playtest_run(project, map_name, bots, seconds, spectate)
        finally:
            if previous:
                game.command(project.s, "\n".join(f"{key} {value}" for key, value in previous.items()), wait=.3)
    result["round_seconds_requested"] = round_seconds
    teams = result.get("teams", {})
    nav = result.get("nav_areas")
    players = result.get("players", {})
    players_seen = sum(int(v or 0) for v in players.values()) if isinstance(players, dict) else 0
    m = project.load(map_name)
    samples = json.loads(Path(result["samples_path"]).read_text(encoding="utf-8")).get("samples", [])
    observation = _bot_observation(m, samples)
    result.update(observation)
    both = bool(players_seen >= 2 and teams.get("2") and teams.get("3"))
    walked = all(observation["moving_bots_by_team"].get(t, 0) for t in ("2", "3"))
    result["competitive_ok"] = bool(both and walked and observation["rounds_completed"] > 0
                                    and nav and nav > 0 and not result.get("errors"))
    result["acceptance"] = {"both_teams": both, "bots_walked_both_teams": walked,
                            "round_finished": observation["rounds_completed"] > 0, "nav_areas": nav}
    return result


def _bot_observation(m, samples: list[dict]) -> dict:
    previous, moved, deaths = {}, {"2": set(), "3": set()}, []
    visited_rooms, names = {"2": set(), "3": set()}, {}
    rounds = []
    rooms, _ = route._layout_rooms(m)
    for sample in samples:
        for line in sample.get("status", []):
            match = re.match(r"^\s*(\d+)\s+.*'([^']+)'\s*$", line)
            if match:
                names[int(match[1])] = match[2]
        current_round = sample.get("round")
        if isinstance(current_round, int) and (not rounds or rounds[-1] != current_round):
            rounds.append(current_round)
        for player in sample.get("players", []):
            if player.get("team") not in (2, 3):
                continue
            key = (bool(player.get("bot")), player.get("slot"))
            raw = player.get("pos")
            pos = [raw.get(k, 0) for k in ("x", "y", "z")] if isinstance(raw, dict) else raw
            old = previous.get(key)
            room_key = route._room_of(rooms, pos) if pos else None
            if player.get("bot") and player.get("alive") and not player.get("noclip") and room_key:
                visited_rooms[str(player["team"])].add(room_key[-1])
            if old and old["round"] == current_round and pos:
                if player.get("bot") and old["alive"] and player.get("alive") and not player.get("noclip") and math.dist(old["pos"][:2], pos[:2]) >= 64:
                    moved[str(player["team"])].add(key)
                if old["alive"] and not player.get("alive"):
                    where = list(pos or old["pos"])
                    room_key = route._room_of(rooms, where)
                    deaths.append({"team": "T" if player["team"] == 2 else "CT", "slot": player["slot"],
                                   "name": names.get(player["slot"]),
                                   "bot": bool(player.get("bot")), "seconds": sample.get("elapsed"),
                                   "position": [round(v, 1) for v in where], "room": room_key[-1] if room_key else None})
            if pos:
                previous[key] = {"pos": pos, "alive": bool(player.get("alive")), "round": current_round}
    # The initial bot-join restart is excluded. Later round-counter increments
    # prove round turnover, not its win reason (the runner does not capture it).
    steady = rounds[1:] if len(rounds) > 1 else rounds
    completed = max(0, (steady[-1] - steady[0])) if steady else 0
    return {"moving_bots_by_team": {k: len(v) for k, v in moved.items()},
            "bot_rooms_by_team": {k: sorted(v) for k, v in visited_rooms.items()},
            "rounds_completed": completed, "observed_rounds": rounds,
            "death_locations": deaths[:12], "death_location_count": len(deaths),
            "observation_note": "Movement >=64u within one round; death locations are sampled, not kill events. Initial join restart excluded."}






