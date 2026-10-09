"""Rounded, staggered cloth bags with a full-width 44-unit wall."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stock_base, box, finish


def build(params):
    mat = stock_base(params, "fabric", default=(.48, .38, .22, 1))
    width = float(params.get("width", 128))
    rows = max(1, int(params.get("rows", 3)))
    bag_width = float(params.get("bag_width", 32))
    depth = float(params.get("bag_depth", 20))
    height = float(params.get("height", rows*float(params.get("bag_height", 44/3))))
    if min(width, bag_width, depth, height) <= 0:
        raise ValueError("sandbag dimensions must be positive")
    columns = max(1, round(width/bag_width))
    step = width/columns
    bh = height/rows
    pieces = []
    for row in range(rows):
        cuts = [-width/2, *[-width/2+(i+(0.5 if row % 2 else 1))*step
                           for i in range(columns) if i+(0.5 if row % 2 else 1) < columns], width/2]
        for i, (left, right) in enumerate(zip(cuts, cuts[1:])):
            obj = box(f"sandbag_{row}_{i}", (right-left-.6, depth, bh-.25),
                      ((left+right)/2, (row % 2)*1.5, bh*row+(bh-.25)/2),
                      mat, bevel=min(4, (right-left)/4, bh*.26))
            for poly in obj.data.polygons:
                poly.use_smooth = True
            pieces.append(obj)
    return finish(pieces, params)
