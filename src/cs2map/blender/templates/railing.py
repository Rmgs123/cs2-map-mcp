"""Low metal safety railing, 40 units high by default."""
import math
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stock_base, cylinder, finish


def horizontal(name, radius, length, location, mat, axis="x"):
    obj = cylinder(name, radius, length, location, mat, 16, uv_scale=0.5)
    if axis == "x":
        obj.rotation_euler[1] = math.pi / 2
    else:
        obj.rotation_euler[0] = math.pi / 2
    return obj


def build(params):
    metal = stock_base(params, "railing", default=(0.16, 0.17, 0.16, 1))
    width = float(params.get("width", 128))
    height = float(params.get("height", 40))
    radius = float(params.get("radius", 2.5))
    posts = max(2, int(params.get("posts", 4)))
    pieces = [horizontal("railing_top", radius, width, (0, 0, height-radius), metal)]
    for i in range(posts):
        x = -width/2 + i * width/(posts-1)
        pieces.append(cylinder(f"railing_post_{i}", radius, height-radius,
                               (x, 0, (height-radius)/2), metal, 16, uv_scale=.5))
    if params.get("mid_rail", True):
        pieces.append(horizontal("railing_mid", radius * .8, width, (0, 0, height*.52), metal))
    return finish(pieces, params)
