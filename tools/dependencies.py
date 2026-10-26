"""Known advisories for the locked Python packages, and a one-command version bump.

    python tools/dependencies.py audit                      ask OSV about every package in the lock (online)
    python tools/dependencies.py bump NAME VERSION          show what a bump would change (nothing is written)
    python tools/dependencies.py bump NAME VERSION --apply  download the wheel, check its SHA-256, update the lock
                                                            and the wheel manifest, move the old wheel aside

A bump edits the three places that must agree (config/requirements.lock.txt, config/dependency-wheels.json,
tools/wheels). Afterwards install it (setup.ps1 -Apply), run `pip check` and the tests. 24.10: Pillow, certifi,
fastmcp and opentelemetry were bumped by hand after 45 Dependabot alerts the project never noticed itself.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'config' / 'requirements.lock.txt'
MANIFEST = ROOT / 'config' / 'dependency-wheels.json'
WHEELS = ROOT / 'tools' / 'wheels'
TAGS = ('cp314-cp314-win_amd64', 'cp314-abi3-win_amd64', 'py3-none-any', 'py2.py3-none-any')
LINE = re.compile(r'^([A-Za-z0-9_.\-]+)==([^\s]+) --hash=sha256:([0-9a-f]{64})$')


def _get(url: str, data: bytes | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json',
                                                          'User-Agent': 'cs2map-dependencies'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def locked() -> list[tuple[str, str, str]]:
    return [m.groups() for m in map(LINE.match, LOCK.read_text(encoding='utf-8').splitlines()) if m]


def _norm(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


def audit() -> int:
    packages = locked()
    queries = [{'package': {'name': n, 'ecosystem': 'PyPI'}, 'version': v} for n, v, _ in packages]
    found = json.loads(_get('https://api.osv.dev/v1/querybatch', json.dumps({'queries': queries}).encode()))['results']
    bad = 0
    for (name, version, _), res in zip(packages, found):
        ids = [v['id'] for v in res.get('vulns', [])]
        if not ids:
            continue
        bad += 1
        fixed = set()
        for vid in ids[:20]:
            info = json.loads(_get(f'https://api.osv.dev/v1/vulns/{vid}'))
            for aff in info.get('affected', []):
                if _norm(aff.get('package', {}).get('name', '')) != _norm(name):
                    continue
                for rng in aff.get('ranges', []):
                    fixed |= {e['fixed'] for e in rng.get('events', []) if 'fixed' in e}
        print(f'{name} {version}: {len(ids)} advisories ({", ".join(ids[:4])}{"..." if len(ids) > 4 else ""}); '
              f'fixed in {", ".join(sorted(fixed)) or "no release yet"}')
    print(f'{bad} of {len(packages)} locked packages have known advisories' if bad
          else f'no known advisories in {len(packages)} locked packages')
    return 1 if bad else 0


def bump(name: str, version: str, apply: bool) -> int:
    rows = locked()
    old = next((r for r in rows if _norm(r[0]) == _norm(name)), None)
    if old is None:
        print(f'{name} is not in {LOCK.name}: add new packages with a full pip report instead')
        return 2
    info = json.loads(_get(f'https://pypi.org/pypi/{name}/{version}/json'))
    files = {f['filename']: f for f in info['urls'] if f['packagetype'] == 'bdist_wheel'}
    pick = next((f for tag in TAGS for fn, f in files.items() if fn.endswith(tag + '.whl')), None)
    if pick is None:
        print(f'{name} {version} has no wheel for Python 3.14 on Windows x64 ({", ".join(files) or "no wheels"})')
        return 2
    sha = pick['digests']['sha256']
    needs = info['info'].get('requires_dist') or []
    print(f'{old[0]} {old[1]} -> {version}: {pick["filename"]} sha256 {sha[:16]}...')
    print('requires:', ', '.join(needs) or 'nothing', '- check the locked versions still satisfy them')
    if not apply:
        print('dry run: nothing written (add --apply)')
        return 0
    data = _get(pick['url'])
    if hashlib.sha256(data).hexdigest() != sha:
        print('the download does not match the SHA-256 PyPI publishes: nothing written')
        return 3
    (WHEELS / pick['filename']).write_bytes(data)
    lock = LOCK.read_text(encoding='utf-8')
    lock = lock.replace(f'{old[0]}=={old[1]} --hash=sha256:{old[2]}', f'{old[0]}=={version} --hash=sha256:{sha}')
    LOCK.write_text(lock, encoding='utf-8')
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    for entry in manifest['packages']:
        if _norm(entry['name']) == _norm(name):
            old_file = entry['url'].rsplit('/', 1)[-1]
            entry.update({'version': version, 'sha256': sha, 'url': pick['url']})
            aside = ROOT / '_to_delete' / f'wheels_old_{datetime.date.today():%d%m}'
            aside.mkdir(parents=True, exist_ok=True)
            if (WHEELS / old_file).exists() and old_file != pick['filename']:
                shutil.move(str(WHEELS / old_file), str(aside / old_file))
    MANIFEST.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print('written: lock, manifest, wheel. Next: setup.ps1 -Apply, pip check, the tests.')
    return 0


if __name__ == '__main__':
    args = sys.argv[1:]
    if args[:1] == ['audit'] and len(args) == 1:
        sys.exit(audit())
    if args[:1] == ['bump'] and len(args) in (3, 4) and args[3:] in ([], ['--apply']):
        sys.exit(bump(args[1], args[2], args[3:] == ['--apply']))
    print(__doc__)
    sys.exit(2)
