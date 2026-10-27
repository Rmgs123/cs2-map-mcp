"""Hollow bus / van body for add_controllable kind car: a floor people stand on, walls with window and door
openings, a windshield, a rear window and a roof. Front is +X, the floor top is at `floor`. Every piece is a box, so
the collision is the same pieces as convex hulls: players walk in through the doors and stand inside.
Wheels are not part of it (wheel_model of the car recipe)."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stock_base, box, finish


def _spans(lo, hi, holes):
    """[lo, hi] without the hole intervals, as (a, b) pieces at least 1 unit long."""
    out, at = [], lo
    for a, b in sorted(holes):
        if a - at >= 1:
            out.append((at, a))
        at = max(at, b)
    if hi - at >= 1:
        out.append((at, hi))
    return out


def build(params):
    body = stock_base(params, "metal", key="body", default=(0.80, 0.62, 0.20, 1))
    trim = stock_base(params, "metal", key="trim", default=(0.12, 0.12, 0.13, 1))
    floor_mat = stock_base(params, "rubber", key="floor", default=(0.25, 0.25, 0.26, 1))   # a ribbed bus floor
    length = float(params.get("length", 590))
    width = float(params.get("width", 140))
    height = float(params.get("height", 150))
    floor = float(params.get("floor", 24))
    clearance = float(params.get("clearance", 0))      # the skirt's bottom (the model's base at z 0)
    wall = float(params.get("wall", 4))
    roof = float(params.get("roof", 4))
    sill = float(params.get("window_bottom", 44))       # over the floor
    head = float(params.get("window_top", 104))         # over the floor
    door_top = float(params.get("door_top", head))
    pillar = float(params.get("pillar", 8))
    windows = int(params.get("windows", max(2, round(length / 96))))
    # two doors on the right (-y; the driver sits on the left, +y), as the bus example of docs/tools/vehicles.md
    doors = params.get("doors") or [{"x": length / 2 - 70, "width": 48, "side": "-y"},
                                     {"x": -(length / 2 - 77), "width": 48, "side": "-y"}]
    windshield = params.get("windshield", True)
    top = height - roof
    if not (clearance < floor < floor + sill < floor + head <= top and sill < door_top <= head):
        raise ValueError("bus_body: need clearance < floor < floor+window_bottom < floor+window_top <= height-roof "
                         "and window_bottom < door_top <= window_top")
    if width < 48 or length < 96 or floor + 72 > top:
        raise ValueError("bus_body: a player (72 tall) must fit between the floor and the roof")
    x0, x1 = -length / 2, length / 2
    y_in = width / 2 - wall
    holes = {"+y": [], "-y": []}
    for d in doors:
        side, w, x = d.get("side", "-y"), float(d.get("width", 48)), float(d["x"])
        if side not in holes:
            raise ValueError("door side is +y or -y")
        if w < 40 or x - w / 2 < x0 + wall + pillar or x + w / 2 > x1 - wall - pillar:
            raise ValueError("a door is 40+ units wide and inside the side wall")
        holes[side].append((x - w / 2, x + w / 2))
    # A player steps up 18 units at most: with the floor higher than that a doorway gets a step well inside (a
    # threshold and a well at the middle height), so players walk in (26.10: a 24-unit floor needed a jump).
    rise = floor - clearance
    well_z = clearance + rise / 2 if rise > 18 else floor
    well = float(params.get("step_depth", 16)) if rise > 18 else 0.0
    if rise > 36:
        raise ValueError("bus_body: floor - clearance must be 36 or less (two steps of 18)")
    pieces = [box("bus_roof", (length, width, roof), (0, 0, height - roof / 2), body, bevel=1),
              box("bus_floor", (length - 2 * wall, 2 * (y_in - well), rise), (0, 0, (floor + clearance) / 2),
                  floor_mat, bevel=0)]
    for side, sign in (("+y", 1), ("-y", -1)):
        if not well:
            break
        yw = sign * (y_in - well / 2)
        for k, (a, b) in enumerate(_spans(x0 + wall, x1 - wall, holes[side])):     # the floor's edge strip
            pieces.append(box(f"bus_floor{side}_{k}", (b - a, well, rise), ((a + b) / 2, yw, (floor + clearance) / 2),
                              floor_mat, bevel=0))
        for k, (a, b) in enumerate(holes[side]):                                   # the step well of a door
            pieces.append(box(f"bus_well{side}_{k}", (b - a, well, well_z - clearance),
                              ((a + b) / 2, yw, (clearance + well_z) / 2), floor_mat, bevel=0))
    # window pillars, evenly between the end posts, skipping doorways
    step = (length - 2 * wall) / windows
    posts = [x0 + wall + i * step for i in range(windows + 1)]
    for side, sign in (("+y", 1), ("-y", -1)):
        y = sign * (width / 2 - wall / 2)
        doorways = holes[side]
        # below the windows: the side from the skirt up to the sill, open at doors (from the floor up)
        for k, (a, b) in enumerate(_spans(x0, x1, doorways)):
            pieces.append(box(f"bus_side{side}_low_{k}", (b - a, wall, floor + sill - clearance),
                              ((a + b) / 2, y, (clearance + floor + sill) / 2), body, bevel=0))
        for k, (a, b) in enumerate(doorways):     # the threshold: the skirt under a doorway up to the step
            pieces.append(box(f"bus_side{side}_step_{k}", (b - a, wall, well_z - clearance),
                              ((a + b) / 2, y, (clearance + well_z) / 2), trim, bevel=0))
        # above a doorway between door_top and the window head, if they differ
        for k, (a, b) in enumerate(doorways):
            if door_top < head:
                pieces.append(box(f"bus_side{side}_lintel_{k}", (b - a, wall, head - door_top),
                                  ((a + b) / 2, y, floor + (door_top + head) / 2), body, bevel=0))
        for k, p in enumerate(posts):
            a, b = max(x0, p - pillar / 2), min(x1, p + pillar / 2)
            if any(a < d1 + pillar and b > d0 - pillar for d0, d1 in doorways):
                continue                          # the doorway's own posts stand there
            pieces.append(box(f"bus_side{side}_pillar_{k}", (b - a, wall, head - sill),
                              ((a + b) / 2, y, floor + (sill + head) / 2), trim, bevel=0))
        # door frames stand where a door cuts the window band
        for k, (a, b) in enumerate(doorways):
            for e, x in enumerate((a - pillar / 2, b + pillar / 2)):
                pieces.append(box(f"bus_side{side}_doorpost_{k}_{e}", (pillar, wall, door_top - sill),
                                  (x, y, floor + (sill + door_top) / 2), trim, bevel=0))
        pieces.append(box(f"bus_side{side}_high", (length, wall, top - floor - head),
                          (0, y, (floor + head + top) / 2), body, bevel=0))
    # front: a dashboard wall, the windshield (open) between corner posts, a header; rear: a window over a wall
    for end, x, low in (("front", x1 - wall / 2, (sill * 0.6) if windshield else head),
                        ("rear", x0 + wall / 2, sill)):
        pieces.append(box(f"bus_{end}_low", (wall, 2 * y_in, floor + low - clearance),
                          (x, 0, (clearance + floor + low) / 2), body, bevel=0))
        for e, y in enumerate((-y_in + pillar / 2, y_in - pillar / 2)):
            pieces.append(box(f"bus_{end}_post_{e}", (wall, pillar, head - low), (x, y, floor + (low + head) / 2),
                              trim, bevel=0))
        pieces.append(box(f"bus_{end}_high", (wall, 2 * y_in, top - floor - head),
                          (x, 0, (floor + head + top) / 2), body, bevel=0))
    return finish(pieces, params)
