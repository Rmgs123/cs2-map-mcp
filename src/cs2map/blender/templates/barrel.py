from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stock_base, cylinder, finish

def build(params):
    m = stock_base(params, "barrel", key="body", default=(0.12, 0.17, 0.20, 1))
    ring_m = stock_base(params, "metal", key="ring", default=(0.08, 0.09, 0.10, 1))
    r, h = float(params.get("radius", 24)), float(params.get("height", 64))
    body = cylinder("barrel_body", r, h, (0, 0, h/2), m,
                    int(params.get("sides", 32)), uv_mode="face")
    rings = [cylinder("barrel_ring_%d" % i, r*1.04, 3, (0, 0, z), ring_m, int(params.get("sides", 32)))
             for i, z in enumerate((h*.15, h*.5, h*.85))]
    return finish([body, *rings], params)
