"""Map preview for the loading screen and the Workshop: a picture and an icon CS2 looks for by name.

CS2 loads `panorama/images/map_icons/screenshots/1080p/<map>_png.vtex_c` (the loading-screen picture) and
`panorama/images/map_icons/map_icon_<map>.vsvg_c` (the map icon) when a map loads (console, 09.10). A frame taken
with `game_screenshot` (a view with `"preview": true`) becomes the picture (1920 x 1080); the icon is a simple
SVG made from the map's initials unless the addon already has one. Both compile through a hidden layout that shows
them (resourcecompiler has no compiler for a bare image).
"""
from __future__ import annotations

from pathlib import Path

from .config import ensure_inside

ICON = '''<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
<rect x="4" y="4" width="120" height="120" rx="18" fill="#1b2633" stroke="#e2b866" stroke-width="6"/>
<text x="64" y="80" font-family="Arial" font-weight="bold" font-size="48" fill="#f2efe6" text-anchor="middle">TEXT</text>
</svg>
'''


def install(project, name: str, png: str) -> dict:
    """Make `png` (a game frame) the loading-screen picture of map `name` and add an icon if it has none."""
    from PIL import Image
    from . import mechanics
    s = project.s
    folder = s.content_addon / 'panorama' / 'images' / 'map_icons'
    shot = ensure_inside(folder / 'screenshots' / '1080p' / f'{name}.png', s.content_addon)
    shot.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(png) as im:
        im = im.convert('RGB')
        w, h = im.size
        target = 16 / 9
        if w / h > target:                       # crop to 16:9, then scale to 1920 x 1080
            nw = int(h * target)
            im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
        elif w / h < target:
            nh = int(w / target)
            im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
        im.resize((1920, 1080), Image.LANCZOS).save(shot)
    icon = ensure_inside(folder / f'map_icon_{name}.svg', s.content_addon)
    made_icon = False
    if not icon.exists():
        words = [w for w in name.replace('-', '_').split('_') if w]
        initials = ''.join(w[0] for w in words[-2:]).upper() or name[:2].upper()
        icon.write_text(ICON.replace('TEXT', initials), encoding='utf-8')
        made_icon = True
    xml = ('<root><Panel class="Hidden">'
           f'<Image src="file://{{images}}/map_icons/screenshots/1080p/{name}.png" />'
           f'<Image src="file://{{images}}/map_icons/map_icon_{name}.svg" /></Panel></root>')
    mechanics.ensure_hud(project, f'mcp_preview_{name}', xml, '.Hidden { visibility: collapse; }')
    game = s.game_addon / 'panorama' / 'images' / 'map_icons'
    return {'preview': s.addon_relative(shot), 'icon': s.addon_relative(icon), 'icon_made': made_icon,
            'compiled': (game / 'screenshots' / '1080p' / f'{name}_png.vtex_c').is_file()
            and (game / f'map_icon_{name}.vsvg_c').is_file()}
