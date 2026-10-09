from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stock_base, box, finish

def build(params):
    m = stock_base(params, "metal", default=(0.12, .13, .14, 1))
    w, h, t = float(params.get("width", 128)), float(params.get("height", 96)), float(params.get("thickness", 6))
    pieces = [box("frame_top", (w, t, t), (0, 0, h-t/2), m), box("frame_bottom", (w, t, t), (0, 0, t/2), m), box("frame_left", (t, t, h-2*t), (-w/2+t/2, 0, h/2), m), box("frame_right", (t, t, h-2*t), (w/2-t/2, 0, h/2), m)]
    for i in range(1, int(params.get("verticals", 1))+1):
        x = -w/2 + i*w/(int(params.get("verticals", 1))+1)
        pieces.append(box("frame_vertical_%d" % i, (t, t, h-2*t), (x, 0, h/2), m))
    return finish(pieces, params)
