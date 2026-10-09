"""Small industrial dumpster with separate rubber wheels."""
import math
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stock_base, box, cylinder, finish


def build(params):
    metal = stock_base(params, "dumpster", key="body", default=(0.12, 0.23, 0.18, 1))
    rubber = stock_base(params, "rubber", key="wheel", default=(0.03, 0.03, 0.03, 1))
    width = float(params.get("width", 80))
    depth = float(params.get("depth", 48))
    height = float(params.get("height", 64))
    wall = float(params.get("wall", 4))
    pieces = [
        box("dumpster_front", (width, wall, height), (0, -depth/2 + wall/2, height/2), metal, bevel=1.5, uv_scale=.5),
        box("dumpster_back", (width, wall, height), (0, depth/2 - wall/2, height/2), metal, bevel=1.5, uv_scale=.5),
        box("dumpster_left", (wall, depth-2*wall, height), (-width/2 + wall/2, 0, height/2), metal, bevel=1.5, uv_scale=.5),
        box("dumpster_right", (wall, depth-2*wall, height), (width/2 - wall/2, 0, height/2), metal, bevel=1.5, uv_scale=.5),
        box("dumpster_bottom", (width-2*wall, depth-2*wall, wall), (0, 0, wall/2), metal, bevel=1.0, uv_scale=.5),
        box("dumpster_lid", (width+2, depth+2, wall), (0, 0, height+wall/2), metal, bevel=1.5, uv_scale=.5),
    ]
    wheel_r = float(params.get("wheel_radius", 7))
    for i, x in enumerate((-width*.32, width*.32)):
        wheel = cylinder(f"dumpster_wheel_{i}", wheel_r, 5, (x, -depth/2-2, wheel_r), rubber, 16)
        wheel.rotation_euler[0] = math.pi / 2
        pieces.append(wheel)
    return finish(pieces, params, forward_degrees=90)
