from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stock_base, box, finish

def build(params):
    m = stock_base(params, "crate", default=(0.42, 0.22, 0.08, 1))
    w, d, h = [float(params.get(k, v)) for k, v, in (("width", 64), ("depth", 64), ("height", 64))]
    body = box("crate", (w, d, h), (0, 0, h/2), m,
               float(params.get("bevel", .75)))
    body["template_uv_mode"] = "face"
    return finish([body], params)
