"""Rare fallback of the prop atlas: look at stock models in CS2.

Only for models whose offline preview is broken (atlas.preview checks that).
Reuses the kit gallery: one shared map `a_gallery`, a full build and frames
under the game lock; repeat the identical prop_preview call while pending.
"""
from __future__ import annotations

from . import atlas, kits


def capture(project, models: list[str]) -> dict:
    facts = atlas.measure(project, models)
    notes = atlas.notes_all()
    parts = []
    for path in models:
        info = facts[path]
        if not info.get('lo'):
            raise atlas.AtlasError(f'{path} has no measurable geometry; it cannot be shown in the game either')
        rec = notes.get(path) or {}
        name = path.rsplit('/', 1)[-1][:-5].lower()
        key = ''.join(c if c.isalnum() else '_' for c in name)[:64]
        parts.append({'full_key': f'atlas/{key}', 'kit': 'atlas', 'key': key, 'model': path,
                      'bounds': [info['lo'], info['hi']], 'size': [round(b - a, 2) for a, b in zip(info['lo'], info['hi'])],
                      'front': rec.get('front') if rec.get('front') in ('+x', '-x', '+y', '-y') else '+x',
                      'mount': 'wall' if rec.get('mount') == 'wall' else 'floor', 'solid': bool(info.get('phys'))})
    requested = {'models': models, 'game': True}
    result = kits.gallery_parts(project, parts, 'atlas', requested, 0, len(parts))
    result['note'] = 'in-game frames: the recorded front (or +X) faces the camera'
    return result
