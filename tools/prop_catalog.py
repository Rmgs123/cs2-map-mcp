"""Shared prop catalog (src/cs2map/data/props/catalog.jsonl): only the integrator runs this.

    python tools/prop_catalog.py status     personal notes vs the shared catalog
    python tools/prop_catalog.py promote    validated personal notes -> catalog (sorted, one line per model)

Agents write personal notes with the MCP tool prop_note (.local/props/notes.jsonl, not in git).
After promote, review `git diff` of the catalog and commit it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from cs2map import atlas  # noqa: E402


def _latest_local() -> dict:
    out = {}
    for rec in atlas._read_jsonl(atlas.NOTES):
        out[rec['model']] = rec
    return out


def status() -> dict:
    catalog = {r['model']: r for r in atlas._read_jsonl(atlas.CATALOG)}
    local = _latest_local()
    new = [m for m in local if m not in catalog]
    changed = [m for m in local if m in catalog and local[m] != catalog[m]]
    shadowed = [m for m in changed if catalog[m].get('date', '') > local[m].get('date', '')]
    return {'catalog': len(catalog), 'local': len(local), 'new': len(new), 'changed': len(changed),
            'local_older_than_catalog': shadowed[:20]}


def promote() -> dict:
    catalog = {r['model']: r for r in atlas._read_jsonl(atlas.CATALOG)}
    added = updated = 0
    rejected = []
    for model, rec in _latest_local().items():
        try:
            atlas.validate_note(dict(rec))
        except atlas.AtlasError as exc:
            rejected.append(f'{model}: {exc}')
            continue
        old = catalog.get(model)
        if old is None:
            added += 1
        elif old != rec and rec.get('date', '') >= old.get('date', ''):
            updated += 1
        else:
            continue
        catalog[model] = {k: rec[k] for k in sorted(rec) if k != 'origin'}
    atlas.CATALOG.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(catalog[m], ensure_ascii=False, separators=(',', ':'), sort_keys=True) for m in sorted(catalog)]
    tmp = atlas.CATALOG.with_suffix('.tmp')
    tmp.write_text(''.join(line + '\n' for line in lines), encoding='utf-8')
    tmp.replace(atlas.CATALOG)
    return {'catalog': len(catalog), 'added': added, 'updated': updated, 'rejected': rejected[:20]}


if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if action not in ('status', 'promote'):
        sys.exit(__doc__)
    print(json.dumps(status() if action == 'status' else promote(), ensure_ascii=False, indent=1))
