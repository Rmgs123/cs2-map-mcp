from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stock_base, box, finish

def build(params):
    wood = stock_base(params, "wood", key="wood", default=(0.28, 0.10, 0.04, 1))
    w, h, t = float(params.get("width", 72)), float(params.get("height", 112)), float(params.get("thickness", 6))
    slab = box("door_slab", (w, t, h), (0, 0, h/2), wood, float(params.get("bevel", 1.5)))
    trim = stock_base(params, "metal", key="trim", default=(0.08, .08, .07, 1))
    pieces = [slab, box("door_trim_top", (w+10, t+4, 5), (0, 0, h+2.5), trim), box("door_trim_left", (5, t+4, h), (-w/2-2.5, 0, h/2), trim), box("door_trim_right", (5, t+4, h), (w/2+2.5, 0, h/2), trim)]
    return finish(pieces, params, forward_degrees=90)
