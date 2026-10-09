"""The project's CS2 addon (content + game folders), created like Workshop Tools does."""
from __future__ import annotations

import shutil
from pathlib import Path

from .config import Settings, ensure_inside, writable_roots

ADDONINFO = ('<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} '
             'format:generic:version{7412167c-06e9-4698-aff2-e63eb59037e7} -->\n{\n}\n')
TEMPLATE = 'addon_template'
PLACEHOLDER = 'xxx_mapname_xxx'


def status(s: Settings) -> dict:
    return {'addon': s.addon, 'content': str(s.content_addon), 'game': str(s.game_addon),
            'exists': s.content_addon.is_dir() and s.game_addon.is_dir(),
            'maps': sorted(p.stem for p in (s.content_addon / 'maps').glob('*.vmap')) if s.content_addon.is_dir() else []}


def ensure(s: Settings) -> dict:
    """Create the addon from Valve's addon_template if it does not exist yet."""
    created = []
    for kind, dst in (('content', s.content_addon), ('game', s.game_addon)):
        if dst.exists():
            continue
        src = s.cs2_path / kind / 'csgo_addons' / TEMPLATE
        if not src.is_dir():
            raise FileNotFoundError(f'Valve addon template missing: {src}')
        ensure_inside(dst, *writable_roots(s))
        shutil.copytree(src, dst)
        for p in sorted(dst.rglob(f'*{PLACEHOLDER}*'), key=lambda p: len(p.parts), reverse=True):
            p.rename(p.with_name(p.name.replace(PLACEHOLDER, s.addon)))
        if kind == 'game':
            (dst / 'addoninfo.txt').write_text(ADDONINFO, encoding='utf-8')
            ro = dst / 'readonly_tools_asset_info.bin'
            if ro.exists():
                ro.rename(dst / 'tools_asset_info.bin')
        created.append(str(dst))
    for sub in ('maps', 'materials', 'models', 'scripts', 'sounds'):
        (s.content_addon / sub).mkdir(parents=True, exist_ok=True)
    return {**status(s), 'created': created}


def asset_path(s: Settings, path: str | Path) -> Path:
    """Absolute path of an addon-relative content asset (e.g. 'materials/x.vmat'), checked to stay inside."""
    p = Path(path)
    full = p if p.is_absolute() else s.content_addon / p
    return ensure_inside(full, s.content_addon)
