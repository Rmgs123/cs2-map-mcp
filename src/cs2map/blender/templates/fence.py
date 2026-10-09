from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stock_base, box, finish

def build(params):
    m = stock_base(params, "wood", default=(0.25, 0.27, 0.24, 1))
    w, h, slats = float(params.get("width", 160)), float(params.get("height", 96)), int(params.get("slats", 9))
    t = float(params.get("thickness", 5))
    pieces = [box("fence_rail_top", (w, t, t), (0, 0, h-t/2), m), box("fence_rail_bottom", (w, t, t), (0, 0, t/2), m)]
    for i in range(slats):
        x = -w/2 + (i + .5) * w/slats
        pieces.append(box("fence_slat_%d" % i, (t, t, h-t*2), (x, 0, h/2), m))
    return finish(pieces, params)
