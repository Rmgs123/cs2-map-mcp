from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stock_base, box, finish

def build(params):
    m = stock_base(params, "wood", default=(.30, .16, .07, 1))
    w, d, h = float(params.get("width", 128)), float(params.get("depth", 32)), float(params.get("height", 160))
    levels = max(2, int(params.get("levels", 4))); t = float(params.get("thickness", 5))
    pieces = [box("shelf_left", (t, d, h), (-w/2+t/2, 0, h/2), m), box("shelf_right", (t, d, h), (w/2-t/2, 0, h/2), m)]
    for i in range(levels):
        z = t/2 + i*(h-t)/(levels-1)
        pieces.append(box("shelf_board_%d" % i, (w-2*t, d, t), (0, 0, z), m))
    return finish(pieces, params)
