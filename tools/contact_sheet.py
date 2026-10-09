"""Put several screenshots on one labelled sheet (for reviewing frames with fewer image reads).

    python tools/contact_sheet.py out.jpg [--cols 3] [--width 520] img1.png img2.png ...
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def sheet(out: Path, images: list[Path], cols: int = 3, width: int = 520) -> Path:
    tiles = []
    for path in images:
        with Image.open(path) as im:
            im = im.convert('RGB')
            im.thumbnail((width, width))
            tiles.append((path.stem.split('-2026')[0][:48], im.copy()))
    height = max(t.height for _, t in tiles) + 22
    rows = (len(tiles) + cols - 1) // cols
    canvas = Image.new('RGB', (cols * width, rows * height), (20, 22, 26))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype('arial.ttf', 15)
    except OSError:
        font = ImageFont.load_default()
    for i, (label, tile) in enumerate(tiles):
        x, y = (i % cols) * width, (i // cols) * height
        canvas.paste(tile, (x, y + 22))
        draw.text((x + 6, y + 3), f'{i + 1}. {label}', fill=(255, 255, 255), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, quality=86)
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('images', nargs='+')
    ap.add_argument('--cols', type=int, default=3)
    ap.add_argument('--width', type=int, default=520)
    a = ap.parse_args()
    print(sheet(Path(a.out), [Path(p) for p in a.images], a.cols, a.width))
