"""The local layer my/ (mylayer.py): notes, recipes, own styles, the user's inbox — and own styles in map_edit."""
from __future__ import annotations

import re
from typing import Any

from .. import mylayer
from ..project import OpError

STYLE_REF = re.compile(r'^\$([a-z0-9][a-z0-9_-]{0,63})\.((?:' + '|'.join(sorted(mylayer.STYLE_KEYS)) +
                       r')(?:\.[A-Za-z0-9_]+)*)$')
BASE_KITS = ('building_inferno', 'building_mirage')


def _expand(value: Any) -> Any:
    """"$<style>.materials.wall" anywhere in an operation -> the value from my/styles/<style>.json."""
    if isinstance(value, str):
        hit = STYLE_REF.match(value)
        if hit:
            return mylayer.style_value(hit.group(1), hit.group(2).split('.'))
        return value
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def _own(op: dict, key: str) -> tuple[str, dict] | None:
    value = op.get(key)
    if isinstance(value, str) and value.startswith('my:'):
        name = value[3:]
        return name, mylayer.style_load(name)
    return None


def _first(style: dict, group: str, *roles: str) -> Any:
    table = style.get(group) or {}
    for role in roles:
        if role in table:
            return table[role]
    return None


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else [v]


def _facade(op: dict, name: str, style: dict) -> dict:
    """add_facade: own plaster/timber palette + tints, roof material, sizes; windows, shutters, balconies stay the
    base kit's parts (Inferno unless based_on names another kit)."""
    op = dict(op)
    op['style'] = style.get('based_on') if style.get('based_on') in BASE_KITS else 'building_inferno'
    if 'materials' not in op:
        mats = _first(style, 'materials', 'facade', 'wall', 'walls')
        if mats is not None:
            tints = _first(style, 'tints', 'facade', 'wall', 'walls')
            tints = [] if tints is None else (tints if tints and isinstance(tints[0], list) else [tints])
            mats = _as_list(mats)
            count = max(len(mats), len(tints) or 1)
            op['materials'] = [({'material': mats[i % len(mats)], 'tint': tints[i % len(tints)]} if tints
                                else mats[i % len(mats)]) for i in range(count)]
    roof = _first(style, 'materials', 'roof')
    if roof and op.get('roof') is not False:
        spec = dict(op.get('roof') or {})
        spec.setdefault('material', _as_list(roof)[0])
        op['roof'] = spec
    sizes = style.get('sizes') or {}
    for key in ('floor_height', 'house_width'):
        if key in sizes and key not in op:
            op[key] = sizes[key]
    # Italian props and shop signs would betray another look: off unless the batch asks for them
    for key in ('balconies', 'awnings', 'lamps'):
        op.setdefault(key, False)
    op.setdefault('shops', 0)
    return op


def _building(op: dict, name: str, style: dict) -> dict:
    op = dict(op)
    op['style'] = style.get('based_on') if style.get('based_on') in BASE_KITS else 'building_inferno'
    for key, roles in (('wall_material', ('wall', 'walls', 'facade')), ('floor_material', ('floor', 'interior_floor'))):
        v = _first(style, 'materials', *roles)
        if v and key not in op:
            op[key] = _as_list(v)[0]
    roof = _first(style, 'materials', 'roof')
    if roof:
        spec = dict(op.get('roof') or {})
        spec.setdefault('material', _as_list(roof)[0])
        op['roof'] = spec
    sizes = style.get('sizes') or {}
    for key in ('floor_height', 'wall_thickness'):
        if key in sizes and key not in op:
            op[key] = sizes[key]
    return op


def _paving(op: dict, name: str, style: dict) -> dict:
    """add_paving: own floors per surface (street/plaza/alley/courtyard, falling back to floor) and own sidewalk,
    curb, gutter, band, border and step materials; sizes come from the Inferno preset."""
    from .. import paving
    base = paving.STYLES['mirage' if style.get('based_on') == 'building_mirage' else 'inferno']
    mats = style.get('materials') or {}
    floor = _first(style, 'materials', 'floor', 'street', 'ground')
    surfaces = {}
    for surface, preset in base['surfaces'].items():
        chosen = mats.get(surface, floor)
        surfaces[surface] = {**preset, **({'floor': _as_list(chosen)[0]} if chosen else {})}
    walk = _first(style, 'materials', 'sidewalk', 'pavement')
    materials = dict(base['materials'])
    for role in materials:
        v = mats.get(role) or (walk if role in ('sidewalk', 'border', 'steps') else None)
        if v:
            materials[role] = _as_list(v)[0]
    if mats.get('curb'):
        materials['curb'] = _as_list(mats['curb'])[0]
    op = dict(op)
    op['style'] = {'my': name, 'surfaces': surfaces, 'materials': materials}
    return op


def _district(op: dict, name: str, style: dict) -> dict:
    """add_district: walls (+ tints), roof, street, sidewalk, trim, window and door materials, sizes."""
    op = dict(op)
    op['style'] = 'inferno'
    mats = dict(op.get('materials') or {})
    walls = _first(style, 'materials', 'wall', 'walls', 'facade')
    if walls is not None and 'walls' not in mats:
        tints = _first(style, 'tints', 'wall', 'walls', 'facade')
        tints = [] if tints is None else (tints if tints and isinstance(tints[0], list) else [tints])
        walls = _as_list(walls)
        count = max(len(walls), len(tints) or 1)
        mats['walls'] = [{'material': walls[i % len(walls)], **({'tint': tints[i % len(tints)]} if tints else {})}
                         for i in range(count)]
    for key, roles in (('roof', ('roof',)), ('street', ('street', 'floor', 'ground')), ('sidewalk', ('sidewalk',)),
                       ('trim', ('trim', 'stone')), ('window', ('window',)), ('door', ('door',))):
        v = _first(style, 'materials', *roles)
        if v and key not in mats:
            mats[key] = _as_list(v)[0]
    if mats:
        op['materials'] = mats
    sizes = style.get('sizes') or {}
    for key in ('floor_height', 'house_width'):
        if key in sizes and key not in op:
            op[key] = sizes[key]
    return op


def _light(op: dict, name: str, style: dict) -> dict:
    light = style.get('light')
    if not light:
        raise OpError(f'own style {name!r} has no "light" ({{"preset": ..., "fog": ...}}); add it or name a preset')
    op = dict(op)
    op['preset'] = light['preset']
    if 'fog' in light and 'fog' not in op:
        op['fog'] = light['fog']
    return op


ADAPTERS = {'add_facade': ('style', _facade), 'add_building': ('style', _building), 'add_district': ('style', _district),
            'add_paving': ('style', _paving), 'lighting_preset': ('preset', _light)}


def preprocess(project, m, op, refs):
    if not isinstance(op, dict):
        return op
    try:
        op = _expand(op)
        adapter = ADAPTERS.get(op.get('op'))
        if adapter:
            own = _own(op, adapter[0])
            if own:
                op = adapter[1](op, *own)
        elif any(isinstance(v, str) and v.startswith('my:') for k, v in op.items() if k in ('style', 'preset')):
            raise OpError(f'{op.get("op")} does not take own styles; use "$<style>.materials.<role>" values instead '
                          '(own styles work in add_facade, add_building, add_paving, add_district, lighting_preset)')
    except mylayer.MyError as e:
        raise OpError(str(e)) from None
    return op


def register(mcp, project):
    @mcp.tool()
    def my_notes(action: str = 'list', kind: str | None = None, name: str | None = None, text: str | None = None,
                 data: Any = None, append: bool = False) -> dict:
        if kind == 'feedback':                      # one file for the MCP author: my/feedback.md
            if action == 'write':
                return mylayer.feedback_write(name or 'general', text)
            return mylayer.feedback_read()
        if action == 'list':
            return mylayer.list_all(kind)
        if not name:
            raise ValueError(f'action={action!r} needs name')
        if action == 'read':
            return mylayer.read(kind or 'note', name)
        if action == 'write':
            return mylayer.write(kind or 'note', name, text, data, append)
        raise ValueError('action: list | read | write')

    @mcp.tool()
    def my_inbox(query: str = '', limit: int = 50) -> dict:
        return mylayer.inbox_search(query, limit)

    @mcp.tool()
    def my_styles(name: str | None = None) -> dict:
        return mylayer.styles_info(name)

    project.register_preprocessor(preprocess)
    # own-style values must be in place before layout points and kit parts are resolved
    project.preprocessors.remove(preprocess)
    project.preprocessors.insert(0, preprocess)
