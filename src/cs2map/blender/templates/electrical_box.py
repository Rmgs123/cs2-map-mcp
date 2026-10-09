"""Wall-mounted electrical cabinet with a readable door and vents."""
import math
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stock_base, box, cylinder, finish


def build(params):
    metal = stock_base(params, "metal", key="body", default=(0.22, 0.25, 0.25, 1))
    plastic = stock_base(params, "plastic", key="label", default=(0.85, 0.65, 0.12, 1))
    width = float(params.get("width", 32))
    depth = float(params.get("depth", 20))
    height = float(params.get("height", 48))
    wall = float(params.get("wall", 2))
    pieces = [box("electrical_box_body", (width, depth, height), (0, 0, height/2), metal, bevel=1.5, uv_scale=.5)]
    # A shallow contrasting door and three raised ventilation strips keep the
    # silhouette legible even at a distance.
    pieces.append(box("electrical_box_door", (width-wall, wall, height-wall),
                      (0, -depth/2-wall*.25, height/2), metal, bevel=1.0, uv_scale=.5))
    for i in range(3):
        z = height*.35 + i*5
        pieces.append(box(f"electrical_vent_{i}", (width*.55, 1.5, 1.5),
                          (0, -depth/2-1.3, z), plastic, bevel=.3, uv_scale=1))
    handle = cylinder("electrical_handle", 1.5, 5, (width*.25, -depth/2-2, height*.52), plastic, 12)
    handle.rotation_euler[0] = math.pi / 2
    pieces.append(handle)
    return finish(pieces, params, forward_degrees=90)
