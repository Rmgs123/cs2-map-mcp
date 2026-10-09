from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stock_base, cylinder, finish

def build(params):
    m = stock_base(params, "metal", default=(0.32, .34, .36, 1))
    r, length = float(params.get("radius", 8)), float(params.get("length", 192))
    o = cylinder("pipe", r, length, (0, 0, r), m, int(params.get("sides", 24)))
    o.rotation_euler[1] = 1.57079632679
    return finish([o], params)
