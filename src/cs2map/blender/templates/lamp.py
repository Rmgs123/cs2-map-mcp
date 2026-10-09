from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stock_base, cylinder, finish

def build(params):
    metal = stock_base(params, "metal", key="metal", default=(0.08, .09, .10, 1))
    shade = stock_base(params, "plastic", key="shade", default=(0.8, .65, .25, 1))
    h, r = float(params.get("height", 128)), float(params.get("radius", 16))
    pieces = [cylinder("lamp_stem", r*.25, h*.75, (0, 0, h*.375), metal), cylinder("lamp_shade", r, h*.25, (0, 0, h*.875), shade)]
    return finish(pieces, params)
