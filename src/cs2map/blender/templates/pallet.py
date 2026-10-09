"""Warehouse pallet made from separate wood slats and runners."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stock_base, box, finish


def build(params):
    wood = stock_base(params, "wood", default=(0.34, 0.18, 0.07, 1))
    width = float(params.get("width", 64))
    depth = float(params.get("depth", 48))
    height = float(params.get("height", 8))
    slats = max(2, int(params.get("slats", 5)))
    t = float(params.get("thickness", 2.5))
    pieces = []
    for i in range(slats):
        y = -depth/2 + (i + .5) * depth/slats
        pieces.append(box(f"pallet_top_{i}", (width, depth/slats - 1.0, t),
                          (0, y, height - t/2), wood, bevel=.5, uv_scale=.5))
    for i, x in enumerate((-width*.36, 0, width*.36)):
        pieces.append(box(f"pallet_runner_{i}", (t*2.2, depth, height-t),
                          (x, 0, (height-t)/2), wood, bevel=.5, uv_scale=.5))
    return finish(pieces, params)
