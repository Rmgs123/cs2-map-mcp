"""HUD look for every mechanic and story, and own layouts.

CS2 custom HUDs are panorama layouts in panorama/layout/custom_game (Panel, Label, Image, Button; CSS; no client
scripts). Each mechanic keeps its XML and base CSS; the look comes from a theme plus options and is appended as an
override block, so the same keys style a drone, a car, a turret, a mini-game, a weapon, a story and abilities.

    "hud": {"theme": "night", "position": "bottom", "size": "m", "width": 640, "offset": [0, 0],
            "animation": "fade", "accent": [233,163,47], "background": [11,24,38,0.93], "text": [220,238,245],
            "font": "default", "title": "DRONE"}

A map default (`set_ui_theme`) fills the keys an operation does not give. Own layouts: map_edit `add_hud`.
"""
from __future__ import annotations

import json
import re

KEY = 'cs2map_ui'
STYLE_KEYS = {'theme', 'position', 'size', 'width', 'offset', 'animation', 'accent', 'background', 'text', 'font'}
THEMES = {
    # bg, edge (accent line), title, text, dim (hints), warn, radius, edge side, shadow
    'night': {'bg': '#0d1622eb', 'edge': '#e2b866', 'title': '#e2b866', 'text': '#f2efe6', 'dim': '#9fb4c2',
              'warn': '#ff6a4d', 'radius': 6, 'side': 'left', 'shadow': True},
    'glass': {'bg': '#ffffff24', 'edge': '#ffffff66', 'title': '#ffffff', 'text': '#ffffff', 'dim': '#dfe9f0',
              'warn': '#ffb199', 'radius': 14, 'side': 'all', 'shadow': True},
    'neon': {'bg': '#05060ae0', 'edge': '#36f2ff', 'title': '#36f2ff', 'text': '#eafcff', 'dim': '#ff4fe5',
             'warn': '#ff4f6d', 'radius': 2, 'side': 'all', 'shadow': True, 'glow': True},
    'paper': {'bg': '#efe3c8f2', 'edge': '#8a4b1c', 'title': '#7a3b12', 'text': '#2d2418', 'dim': '#6b5a43',
              'warn': '#b3261e', 'radius': 3, 'side': 'top', 'shadow': True},
    'military': {'bg': '#1d2316e8', 'edge': '#b9c46a', 'title': '#c9d47a', 'text': '#e4e7d3', 'dim': '#a5ad86',
                 'warn': '#ff8a3d', 'radius': 0, 'side': 'left', 'shadow': False},
    'minimal': {'bg': '#00000000', 'edge': '#00000000', 'title': '#ffd36b', 'text': '#ffffff', 'dim': '#d8d8d8',
                'warn': '#ff6a4d', 'radius': 0, 'side': 'none', 'shadow': True},
}
POSITIONS = {
    'top': ('center', 'top'), 'bottom': ('center', 'bottom'), 'left': ('left', 'center'), 'right': ('right', 'center'),
    'center': ('center', 'center'), 'top-left': ('left', 'top'), 'top-right': ('right', 'top'),
    'bottom-left': ('left', 'bottom'), 'bottom-right': ('right', 'bottom'),
}
SIZES = {'s': 0.82, 'm': 1.0, 'l': 1.25, 'xl': 1.55}
FONTS = {'default': None, 'stratum': 'Stratum2'}     # CS2 ignores other family names in custom HUDs (tested 08.10)
ANIMATIONS = ('none', 'fade', 'slide')


class UIError(ValueError):
    pass


def _color(v, label, alpha=False):
    if isinstance(v, str) and re.fullmatch(r'#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?', v):
        return v.lower() if len(v) == 9 else v.lower() + 'ff'
    if isinstance(v, list) and len(v) in (3, 4) and all(isinstance(x, (int, float)) for x in v):
        r, g, b = (int(max(0, min(255, x))) for x in v[:3])
        a = v[3] if len(v) == 4 else 1.0
        if not 0 <= a <= 1:
            raise UIError(f'hud.{label}: alpha is 0..1')
        return '#%02x%02x%02x%02x' % (r, g, b, int(round(a * 255)))
    raise UIError(f'hud.{label} is [r,g,b] or [r,g,b,alpha 0..1] or "#rrggbb(aa)"')


def validate(hud, extra=()):
    """Check the style keys of an operation's hud (other keys, like title, are the operation's own)."""
    if hud is None:
        return {}
    if not isinstance(hud, dict):
        raise UIError('hud must be an object')
    out = {}
    for k, v in hud.items():
        if k not in STYLE_KEYS:
            if k in extra:
                out[k] = v
                continue
            raise UIError(f'unknown hud key {k!r}; style keys: {", ".join(sorted(STYLE_KEYS))}'
                          + (f'; own keys: {", ".join(sorted(extra))}' if extra else ''))
        if k == 'theme' and v not in THEMES:
            raise UIError(f'hud.theme: one of {", ".join(THEMES)}')
        if k == 'position' and v not in POSITIONS:
            raise UIError(f'hud.position: one of {", ".join(POSITIONS)}')
        if k == 'size' and v not in SIZES:
            raise UIError('hud.size: s, m, l or xl')
        if k == 'animation' and v not in ANIMATIONS:
            raise UIError('hud.animation: none, fade or slide')
        if k == 'font' and v not in FONTS:
            raise UIError(f'hud.font: one of {", ".join(FONTS)}')
        if k == 'width' and (isinstance(v, bool) or not isinstance(v, (int, float)) or not 160 <= v <= 1600):
            raise UIError('hud.width: 160..1600 pixels')
        if k == 'offset' and (not isinstance(v, list) or len(v) != 2
                              or not all(isinstance(x, (int, float)) and -900 <= x <= 900 for x in v)):
            raise UIError('hud.offset: [x, y] pixels, -900..900 (x right, y down)')
        if k in ('accent', 'background', 'text'):
            _color(v, k)
        out[k] = v
    return out


def has_style(hud) -> bool:
    return bool(hud) and any(k in STYLE_KEYS for k in hud)


def override(roles: dict, hud: dict, default_position: str = 'bottom') -> str:
    """CSS appended to a mechanic's base CSS. roles: panel, title, text (selectors), hint, warn (extra selectors)."""
    if not has_style(hud):
        return ''
    t = dict(THEMES[hud.get('theme', 'night')])
    if 'accent' in hud:
        t['edge'] = t['title'] = _color(hud['accent'], 'accent')
    if 'background' in hud:
        t['bg'] = _color(hud['background'], 'background')
    if 'text' in hud:
        t['text'] = _color(hud['text'], 'text')
    scale = SIZES[hud.get('size', 'm')]
    h, v = POSITIONS[hud.get('position', default_position)]
    ox, oy = hud.get('offset', [0, 0])
    font = FONTS[hud.get('font', 'default')]
    p = roles['panel']
    margins = {'left': 40 + ox if h == 'left' else 0, 'right': 40 - ox if h == 'right' else 0,
               'top': (210 if (v, h) == ('top', 'left') else 120 if v == 'top' else 0) + (oy if v != 'bottom' else 0),
               'bottom': (110 if v == 'bottom' else 0) - (oy if v == 'bottom' else 0)}
    if h == 'center' and ox:
        margins['left'] = ox
    edge = {'left': f'border-left: {round(4 * scale)}px solid {t["edge"]};',
            'top': f'border-top: {round(3 * scale)}px solid {t["edge"]};',
            'all': f'border: 1px solid {t["edge"]};', 'none': 'border: 0px solid #00000000;'}[t['side']]
    shadow = 'box-shadow: #000000a0 0px 4px 16px 0px;' if t['shadow'] and t['side'] != 'none' else ''
    text_shadow = ('text-shadow: 0px 0px 6px ' + t['title'] + ';') if t.get('glow') else \
        ('text-shadow: 1px 1px 3px #000000e6;' if t['side'] == 'none' or t['bg'].endswith('24') else '')
    ff = f'font-family: {font};' if font else ''
    lines = [
        f'{p} {{ horizontal-align: {h}; vertical-align: {v}; margin: {margins["top"]}px {margins["right"]}px '
        f'{margins["bottom"]}px {margins["left"]}px; background-color: {t["bg"]}; {edge} border-radius: {t["radius"]}px; '
        f'{shadow} padding: {round(12 * scale)}px {round(22 * scale)}px; '
        + (f'width: {int(hud["width"])}px; ' if 'width' in hud else '') + '}',
        f'{p} Label {{ color: {t["text"]}; font-size: {round(21 * scale)}px; {ff} {text_shadow} }}',
    ]
    if roles.get('title'):
        lines.append(f'{roles["title"]} {{ color: {t["title"]}; font-size: {round(16 * scale)}px; font-weight: bold; '
                     f'text-transform: uppercase; letter-spacing: 1px; margin-bottom: {round(4 * scale)}px; {ff} }}')
    if roles.get('hint'):
        lines.append(f'{roles["hint"]} {{ color: {t["dim"]}; font-size: {round(16 * scale)}px; }}')
    for sel in roles.get('warn', ()):
        lines.append(f'{sel} {{ color: {t["warn"]}; }}')
    anim = hud.get('animation', 'none')
    if anim != 'none':
        lines.append(f'{p} {{ transition-property: opacity, transform; transition-duration: 0.25s; '
                     'transition-timing-function: ease-out; opacity: 1; transform: translateY(0px); }')
        move = ' transform: translateY(-18px);' if anim == 'slide' else ''
        lines.append(f'{p}.Hidden {{ visibility: visible; opacity: 0;{move} }}')
    return '\n/* cs2map ui theme */\n' + '\n'.join(lines) + '\n'


# ---- map default ---------------------------------------------------------------------------------------
def stored(m) -> dict:
    raw = m.world.get(KEY) if hasattr(m, 'world') else None
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def set_theme_op(project, m, op, refs):
    extra = set(op) - {'op'} - STYLE_KEYS
    if extra:
        raise UIError(f'set_ui_theme takes the hud style keys only ({", ".join(sorted(STYLE_KEYS))}); unknown {sorted(extra)}')
    style = validate({k: v for k, v in op.items() if k != 'op'})
    if 'position' in style:
        raise UIError('position differs per HUD: give it on each operation, not in set_ui_theme')
    m.world.set(KEY, 'string', json.dumps(style, sort_keys=True))
    return {'op': 'set_ui_theme', **style, 'applies_to': 'HUD operations later in this and later batches'}


UI_OPS = {'add_controllable', 'add_minigame', 'add_weapon', 'add_story', 'add_ability', 'add_hideseek'}


def preprocess(project, m, op, refs):
    """Fill hud style keys from the map default (set_ui_theme) for HUD-making operations."""
    if not isinstance(op, dict) or op.get('op') not in UI_OPS or m is None:
        return op
    default = stored(m)
    if not default:
        return op
    hud = op.get('hud')
    if hud is False:
        return op
    hud = dict(hud) if isinstance(hud, dict) else {}
    for k, v in default.items():
        hud.setdefault(k, v)
    return {**op, 'hud': hud}


# ---- own layouts ---------------------------------------------------------------------------------------
TAGS = {'root', 'styles', 'include', 'Panel', 'Label', 'Image', 'Button'}
ATTRS = {'id', 'class', 'hittest', 'text', 'src'}


def check_layout(xml: str, css: str) -> list[str]:
    """Static checks of an own layout: allowed tags/attributes, ids, a reachable css; returns the panel ids."""
    if not isinstance(xml, str) or not isinstance(css, str):
        raise UIError('xml and css are strings')
    if len(xml) > 60000 or len(css) > 60000:
        raise UIError('layout files are limited to 60 KB each')
    if re.search(r'<\s*script|javascript:|onactivate|onmouse', xml, re.I):
        raise UIError('layouts cannot run scripts (CS2 does not support client scripts here)')
    tags = re.findall(r'<\s*([A-Za-z]+)', xml)
    bad = sorted(set(tags) - TAGS)
    if bad:
        raise UIError(f'unsupported tags {bad}; CS2 custom HUDs know Panel, Label, Image, Button')
    for attrs in re.findall(r'<\s*(?:Panel|Label|Image|Button)\b([^>]*)>', xml):
        names = set(re.findall(r'([A-Za-z_:-]+)\s*=', attrs))
        if names - ATTRS:
            raise UIError(f'unsupported attributes {sorted(names - ATTRS)}; allowed: {", ".join(sorted(ATTRS))}')
    for src in re.findall(r'src\s*=\s*"([^"]+)"', xml):
        if src.startswith('file://{resources}/styles/'):
            continue
        if not re.fullmatch(r'file://\{images\}/custom_game/[A-Za-z0-9_./-]+\.(png|vtex)|s2r://panorama/images/[A-Za-z0-9_./-]+', src):
            raise UIError(f'image src {src!r}: use file://{{images}}/custom_game/<name>.png (put images with add_hud '
                          'images) or a stock s2r://panorama/images/... path')
    if not re.search(r'<\s*root', xml):
        raise UIError('the layout starts with <root>')
    ids = re.findall(r'\bid\s*=\s*"([A-Za-z0-9_-]+)"', xml)
    if len(ids) != len(set(ids)):
        raise UIError('panel ids must be unique')
    if '{' in css and css.count('{') != css.count('}'):
        raise UIError('css braces do not match')
    return ids
