from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stock_base, box, finish

def build(params):
    m = stock_base(params, "wood", default=(.28, .14, .06, 1))
    w, d, h = float(params.get("width", 128)), float(params.get("depth", 64)), float(params.get("height", 72))
    top = float(params.get("top", 6)); leg = float(params.get("leg", 7))
    pieces = [box("tabletop", (w, d, top), (0, 0, h-top/2), m)]
    for i, x in enumerate((-w/2+leg, w/2-leg)):
        for j, y in enumerate((-d/2+leg, d/2-leg)):
            pieces.append(box("table_leg_%d_%d" % (i, j), (leg, leg, h-top), (x, y, (h-top)/2), m))
    return finish(pieces, params)
