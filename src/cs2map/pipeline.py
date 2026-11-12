"""A map built from packages: apply only what changed, in one load and one save; run only the stale generators.

A package is a JSON file of map_edit operations (one generator's part of a map: the terrain, a street, a yard).
The map remembers every package it holds (its content hash, what it needs, whose nodes it used), so the next call
replaces only the packages whose content changed and those that depend on them. A manifest (pipeline.json) adds
the generators that write the packages: a generator runs again only when its script, its arguments or a file it
read has changed. Everything runs as one background job (python -m cs2map.pipeline). Specification:
docs/specs/pipeline.md.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from . import __version__
from .config import PROJECT_ROOT

LOCKS = PROJECT_ROOT / '.local' / 'locks'
RESULT_MARK = 'PIPELINE_RESULT '
GENERATOR_TIMEOUT = 900
_TAG = re.compile(r'\w{1,64}')


class PipelineError(ValueError):
    pass


# -- packages -----------------------------------------------------------------------------------------------------
@dataclass
class Package:
    tag: str
    hash: str
    needs: list[str] | None            # None = not declared: it may depend on every package before it
    operations: list = field(repr=False, default_factory=list)
    file: str = ''


def project_path(path: str | Path, base: Path | None = None) -> Path:
    """A file inside the project folder (packages and manifests are the user's own files, usually under my/)."""
    p = Path(path)
    if not p.is_absolute():
        p = (base or PROJECT_ROOT) / p
    real = Path(os.path.realpath(p))
    root = Path(os.path.realpath(PROJECT_ROOT))
    if real != root and root not in real.parents:
        raise PipelineError(f'{path}: packages and manifests must be inside the project folder {PROJECT_ROOT}')
    return real


def read_package(path: Path, needs: list[str] | None = None) -> Package:
    """A package file: {"package": name, "needs": [...], "operations": [...]}, or the arguments of a map_edit
    call {"name": map, "operations": [...]}, or a bare list of operations. The tag is "package", else the tag of a
    leading replace_batch, else the file name without "args_" and ".json"."""
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode('utf-8-sig'))
    except OSError as e:
        raise PipelineError(f'package {path.name}: {e}') from None
    except ValueError as e:
        raise PipelineError(f'package {path.name} is not JSON: {e}') from None
    declared = None
    if isinstance(data, dict):
        ops, tag, declared = data.get('operations'), data.get('package'), data.get('needs')
    else:
        ops, tag = data, None
    if not isinstance(ops, list):
        raise PipelineError(f'package {path.name}: no "operations" list')
    if ops and isinstance(ops[0], dict) and ops[0].get('op') == 'replace_batch':
        tag = tag or ops[0].get('tag')
        ops = ops[1:]
    if any(isinstance(o, dict) and o.get('op') == 'replace_batch' for o in ops):
        raise PipelineError(f'package {path.name}: replace_batch is only allowed as its first operation')
    tag = tag or re.sub(r'^args_', '', path.stem)
    if not isinstance(tag, str) or not _TAG.fullmatch(tag):
        raise PipelineError(f'package {path.name}: the package name {tag!r} must be letters, digits, _ (<= 64)')
    if declared is None:
        declared = needs
    if declared is not None and not (isinstance(declared, list) and all(isinstance(x, str) for x in declared)):
        raise PipelineError(f'package {path.name}: "needs" must be a list of package names')
    body = json.dumps(ops, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    digest = hashlib.sha1(f'{__version__}\n{json.dumps(declared)}\n{body}'.encode('utf-8')).hexdigest()[:16]
    return Package(tag, digest, declared, ops, str(path))


def read_packages(files: list, base: Path | None = None, needs: dict | None = None) -> list[Package]:
    out, seen = [], {}
    for f in files:
        path = project_path(f, base)
        pkg = read_package(path, (needs or {}).get(str(f)))
        if pkg.tag in seen:
            raise PipelineError(f'two packages are named {pkg.tag}: {Path(seen[pkg.tag]).name} and {path.name}')
        seen[pkg.tag] = pkg.file
        out.append(pkg)
    names = set(seen)
    for pkg in out:
        unknown = [n for n in pkg.needs or [] if n not in names]
        if unknown:
            raise PipelineError(f'package {pkg.tag} needs {unknown}: no such package in this list ({sorted(names)})')
    return out


GAME_UPDATED = 'the game was updated: its entity definitions (FGD) changed'
LOOSE_NODES = 40          # a fresh template map holds fewer nodes than this (sun, sky, fog, a spawn)
MAX_PASSES = 3            # over a manifest's generators, while one that ran leaves an earlier one stale


def plan(state: dict, present: set, packages: list[Package], game: str = '') -> dict:
    """What to replay: {replay: [tags in order], remove: [tags], skip: [tags], why: {tag: reason}}.

    state: Project.packages_state of the map; present: {tag: how many tagged nodes the map holds now}; game:
    fgd.signature of the game now (a package applied under other entity definitions is replayed: the same
    operations write other keyvalues).
    A package is replayed when its content changed (or is new, or its nodes are gone); when a package it needs,
    or one whose nodes it used last time, is replayed; when a replayed package used its nodes last time (they
    carry that package's changes); and, if its needs are not declared, when any package before it is replayed."""
    old = state.get('packages', {})
    tags = [p.tag for p in packages]
    # Packages the pipeline put into the map earlier (they have a hash) and that are no longer listed.
    remove = [t for t, e in old.items() if t not in tags and e.get('hash')]
    why: dict[str, str] = {}
    for p in packages:
        e = old.get(p.tag)
        if e is None:
            why[p.tag] = 'new'
        elif e.get('hash') != p.hash:
            why[p.tag] = 'changed'
        elif e.get('nodes') != present.get(p.tag, 0):
            why[p.tag] = ('its nodes are missing from the map' if not present.get(p.tag)
                          else 'its nodes were added or removed outside the packages')
        elif game and e.get('game') and e['game'] != game:
            why[p.tag] = GAME_UPDATED
    kept_old = [t for t in state.get('order', []) if t in tags and t not in why]
    kept_new = [t for t in tags if t in kept_old]
    moved = next((i for i, (a, b) in enumerate(zip(kept_old, kept_new)) if a != b), None)
    moved_tags = set(kept_new[moved:]) if moved is not None else set()
    gone = set(remove)
    while True:
        size = len(why)
        for i, p in enumerate(packages):
            if p.tag in why:
                continue
            e = old[p.tag]
            touches = set(e.get('touches') or [])
            dirty = set(why) | gone
            if p.needs is None or '' in touches:
                first = next((q.tag for q in packages[:i] if q.tag in why), None)
                if first or gone:
                    why[p.tag] = (f'no "needs" declared and {first or sorted(gone)[0]} before it '
                                  f'{"is replayed" if first else "was removed"}')
                elif p.tag in moved_tags:
                    why[p.tag] = 'no "needs" declared and the order of packages changed'
            else:
                hit = sorted((set(p.needs) | touches) & dirty)
                if hit:
                    why[p.tag] = f'needs {hit[0]}'
        for t in list(why) + remove:
            for other in (old.get(t) or {}).get('touches') or []:
                if other and other in tags and other not in why:
                    why[other] = f'{t} used its nodes'
        if len(why) == size:
            break
    return {'replay': [t for t in tags if t in why], 'remove': remove,
            'skip': [t for t in tags if t not in why], 'why': why}


def apply(project, name: str, packages: list[Package], dry_run: bool = False, force: bool = False,
          say=lambda text: None, complete: bool = False, source: str = '', template: str | None = None) -> dict:
    """Bring the map to the given packages: one load, one map_edit call for everything that must be replayed,
    one save. Returns {ok, replayed, skipped, removed, why, seconds, saved}.

    complete: the list is the whole map (a manifest), so packages it no longer names are removed; a plain list of
    files that leaves out a package the map holds is refused instead (a forgotten file must not delete a zone).
    dry_run: nothing is written; every package is replayed into a fresh map in memory and compared with the map
    (verify), which also proves that the map equals a clean replay."""
    from .project import PACKAGE_CALL
    started = time.monotonic()
    m = project.load(name)
    state = project.packages_state(m)
    present = project.package_nodes(m)
    from . import fgd
    game = fgd.signature(str(project.s.cs2_path))
    todo = plan(state, present, packages, game)
    if todo['remove'] and not complete:
        raise PipelineError(
            f'map {name} also holds package(s) {", ".join(todo["remove"])} that this list leaves out: give the '
            'full ordered list of package files (or a manifest); to drop a package on purpose use map_edit '
            '[{"op": "replace_batch", "remove": ["<package>"]}]')
    out = {'ok': True, 'map': name, 'replayed': todo['replay'], 'skipped': len(todo['skip']), 'removed': todo['remove'],
           'why': {t: todo['why'][t] for t in todo['replay'][:12]}}
    if not state['packages'] and todo['replay']:
        # 12.11: a 24 500-node map built earlier from these same files by plain map_edit calls carried no package
        # record: the packages would have been added on top and doubled it. More than a template's nodes: refuse.
        loose = sum(1 for n in m.iter_nodes()) - sum(present.values())
        if loose > LOOSE_NODES:
            text = (f'map {name} holds {loose} nodes that belong to no package and has no package record: the '
                    'packages would be added on top of them (a map built earlier from these same files would be '
                    f'doubled). Rebuild it from the packages: map_create(name="{name}", overwrite=true), then this '
                    'call again. To keep those nodes under the packages on purpose, mark them first: '
                    f'map_edit(name="{name}", operations=[{{"op": "replace_batch", "tag": "base"}}])')
            if not dry_run:
                raise PipelineError(text)
            out['warning'] = text
    try:                                      # hand the parsed map to the next load: one read of a big file
        st = m.path.stat()
        project._saved_map = (m.path, st.st_mtime_ns, st.st_size, m.doc)
    except (OSError, AttributeError):
        pass
    if dry_run:
        say(f'dry run: replaying all {len(packages)} packages in memory to compare with the map')
        check = verify(project, name, packages, template)
        out.update(dry_run=True, would_replay=out.pop('replayed'), failed=check['failed'],
                   seconds=round(time.monotonic() - started, 1))
        changed = set(todo['replay']) | set(todo['remove'])
        stale = [t for t in check['differ'] if t not in changed]
        out['ok'] = not check['failed'] and not stale
        if stale:
            out['differs_from_clean_replay'] = stale
            # A map from before packages remembered the game: its difference may be nothing but a game update.
            unknown = any(not (state['packages'].get(t) or {}).get('game') for t in stale)
            out['hint'] = ('these unchanged packages differ from a clean replay (edited by hand, or changed by a '
                           'package that does not declare it'
                           + (', or the game was updated since and gives fresh entities other keyvalues' if unknown
                              else '') + '): rebuild the map clean - map_create overwrite=true, then apply the '
                           'packages')
        elif not check['failed'] and not changed:
            _remember(project.s, name, partial=0, source=source, verified=True)
            out['note'] = 'the map equals a clean replay of its packages'
        return out
    if not todo['replay'] and not todo['remove']:
        out['seconds'] = round(time.monotonic() - started, 1)
        out['note'] = 'every package is up to date: nothing was written'
        return out
    say(f'replaying {len(todo["replay"])} of {len(packages)} packages'
        + (f', removing {len(todo["remove"])}' if todo['remove'] else ''))
    operations: list = [{'op': 'replace_batch', 'remove': todo['replay'] + todo['remove']}]
    by_tag = {p.tag: p for p in packages}
    for tag in todo['replay']:
        p = by_tag[tag]
        marker = {'op': 'replace_batch', 'tag': tag, 'hash': p.hash}
        if p.needs is not None:
            marker['needs'] = p.needs
        operations.append(marker)
        operations.extend(p.operations)
    token = PACKAGE_CALL.set({'order': [p.tag for p in packages]})
    try:
        result = project.map_edit(name, operations, False, force)
    finally:
        PACKAGE_CALL.reset(token)
    notes = [f'{r["tag"]}: {r["outside"]}' for r in result['results'] if isinstance(r, dict) and r.get('outside')]
    warnings = [f'{r.get("op")}: {r["warning"]}' for r in result['results'] if isinstance(r, dict) and r.get('warning')]
    out.update(operations=sum(len(by_tag[t].operations) for t in todo['replay']),
               seconds=round(time.monotonic() - started, 1), saved=result.get('saved'))
    _remember(project.s, name, partial=None if todo['skip'] else 0, source=source)
    if notes:
        out['notes'] = notes[:8]
    if warnings:
        out['warnings'] = warnings[:8]
        out['warning_count'] = len(warnings)
    return out


# -- what a release wants to know: was the map replayed whole since its last partial update? --------------------------
STATE = PROJECT_ROOT / '.local' / 'state' / 'pipeline.json'


def _states() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember(s, name: str, partial: int | None, source: str = '', verified: bool = False) -> None:
    """partial=0: the map is a clean replay (replayed whole, or verified); None: one more partial update."""
    data = _states()
    key = f'{s.addon}/{name}'
    entry = data.get(key) or {'partial': 0}
    entry['partial'] = 0 if partial == 0 else int(entry.get('partial') or 0) + 1
    entry['time'] = time.time()
    if source:
        entry['source'] = source
    if verified:
        entry['verified'] = time.time()
    data[key] = entry
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, indent=1), encoding='utf-8')
        os.replace(tmp, STATE)
    except OSError:
        pass


def forget(s, name: str) -> None:
    """A map made anew (map_create) has no package history."""
    data = _states()
    if data.pop(f'{s.addon}/{name}', None) is not None:
        try:
            STATE.write_text(json.dumps(data, indent=1), encoding='utf-8')
        except OSError:
            pass


def release_gate(s, name: str) -> str | None:
    """Why a release of this map must wait, or None. A release is built from a map that equals a clean replay of
    its packages: after partial updates that is proved (dry run) or made (clean replay) first."""
    entry = _states().get(f'{s.addon}/{name}')
    if not entry or not entry.get('partial'):
        return None
    source = entry.get('source') or '<its package files or pipeline.json>'
    return (f'map {name} was updated package by package {entry["partial"]} time(s) since its last clean replay. '
            f'A release is built from a clean replay: prove it with map_edit(name="{name}", packages="{source}", '
            'dry_run=true) (replays everything in memory and compares; nothing is written), or rebuild it '
            '(map_create overwrite=true, then map_edit packages). Then call the release again')


# -- the same map from a clean slate ------------------------------------------------------------------------------
_VOLATILE = {'nodeID', 'referenceID', 'cs2map_packages', 'randomSeed'}   # randomSeed: a fresh random number per run
_FLOAT_TYPES = {'float', 'vector2', 'vector3', 'vector4', 'qangle', 'quaternion', 'matrix'}


def canonical(m, only_packages: bool = True) -> dict:
    """{package tag: digest of its nodes} with ids left out and siblings in a fixed order: two maps hold the same
    packages exactly when these are equal, whatever order the packages were applied in. Floats are compared as
    the file stores them (32 bits), so a map in memory equals the same map read back from disk."""
    import numpy as np
    from .dmx import Element
    from .project import Project

    def digest(e, seen) -> bytes:
        if id(e) in seen:
            return b'loop'
        seen = seen | {id(e)}
        h = hashlib.sha1(f'{e.type}|{e.name or ""}'.encode('utf-8', errors='replace'))
        for key in sorted(e.attrs):
            if key in _VOLATILE:
                continue
            a = e.attrs[key]
            v = a.value
            h.update(f'|{key}:{a.type}='.encode('utf-8', errors='replace'))
            if a.base == 'element':
                items = v if a.is_array else [v]
                subs = [digest(x, seen) if isinstance(x, Element) else repr(x).encode() for x in items]
                if key == 'children':
                    subs.sort()
                for sub in subs:
                    h.update(sub)
            elif a.base in _FLOAT_TYPES:
                h.update(np.asarray(v, dtype='<f4').tobytes())
            elif a.is_array and a.base in ('int', 'uint64', 'bool', 'uint8', 'time'):
                h.update(np.asarray(v).tobytes())
            else:
                h.update(repr(v).encode('utf-8', errors='replace'))
        return h.digest()

    groups: dict[str, list[bytes]] = {}

    def walk(parent, tag):
        for c in parent.get('children', []):
            if not isinstance(c, Element):
                continue
            own = c.get(Project.BATCH_KEY)
            if own:
                groups.setdefault(own, []).append(digest(c, frozenset()))
            elif not only_packages and tag is None:
                groups.setdefault('', []).append(digest(c, frozenset()))
            walk(c, own or tag)
    walk(m.world, None)
    return {tag: hashlib.sha1(b''.join(sorted(parts))).hexdigest()[:16] for tag, parts in groups.items()}


def verify(project, name: str, packages: list[Package], template: str | None = None) -> dict:
    """Does the map hold exactly what a clean replay of its packages gives? Replays every package into a fresh
    map in memory (nothing is written) and compares the packages' nodes. Slow on big maps: a full replay."""
    from .project import DRY_RUN, PACKAGE_CALL
    from .vmap import VMap
    started = time.monotonic()
    current = project.load(name)
    have = canonical(current)
    template = template or current.world.get('cs2map_template') or 'basic'
    del current
    fresh = VMap.from_template(project.s.cs2_path, template)
    state = {'order': [], 'packages': {}}
    owner: dict[int, str] = {}
    token, call = DRY_RUN.set(True), PACKAGE_CALL.set({'order': [p.tag for p in packages]})
    failed = []
    try:
        for p in packages:
            refs: dict[str, int] = {}
            batch = project._replace_batch(fresh, {'op': 'replace_batch', 'tag': p.tag}, owner, state)
            for i, op in enumerate(p.operations):
                try:
                    result = project._apply(fresh, op, refs)
                    project._keep_ref(fresh, op, result)
                except Exception as e:           # noqa: BLE001 - reported, the comparison then fails anyway
                    failed.append(f'{p.tag} #{i}: {str(e)[:120]}')
            project._tag_batch(fresh, p.tag, batch['before'], owner)
    finally:
        DRY_RUN.reset(token)
        PACKAGE_CALL.reset(call)
    want = canonical(fresh)
    differ = sorted(t for t in set(have) | set(want) if have.get(t) != want.get(t))
    return {'ok': not differ and not failed, 'packages': len(want), 'differ': differ[:20], 'failed': failed[:8],
            'seconds': round(time.monotonic() - started, 1)}


# -- one map, one pipeline at a time -------------------------------------------------------------------------------
def _lock_file(s, name: str) -> Path:
    return LOCKS / f'pipeline-{s.addon}-{name}.lock'


def check_free(s, name: str) -> None:
    """Refuse a map_edit while a pipeline job of another process is rebuilding the same map."""
    lock = _lock_file(s, name)
    try:
        info = json.loads(lock.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return
    pid = info.get('pid')
    if pid and pid != os.getpid() and psutil.pid_exists(pid) and time.time() - info.get('time', 0) < 4 * 3600:
        from .vmap import MapError
        raise MapError(f'map {name} is being rebuilt from its packages right now (job {info.get("job", "?")}): '
                       'wait for that job (job_status) and edit afterwards')


class _Held:
    def __init__(self, s, name: str, job: str = ''):
        self.path, self.job = _lock_file(s, name), job

    def __enter__(self):
        check_free_path = self.path
        try:
            info = json.loads(check_free_path.read_text(encoding='utf-8'))
            if info.get('pid') != os.getpid() and psutil.pid_exists(info.get('pid') or 0) \
                    and time.time() - info.get('time', 0) < 4 * 3600:
                raise PipelineError(f'another pipeline job ({info.get("job", "?")}) is working on this map')
        except (OSError, ValueError):
            pass
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({'pid': os.getpid(), 'time': time.time(), 'job': self.job}), encoding='utf-8')
        return self

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass


# -- the manifest: generators, tool calls, packages ------------------------------------------------------------------
def _sha(*parts) -> str:
    h = hashlib.sha1()
    for part in parts:
        h.update(part if isinstance(part, bytes) else str(part).encode('utf-8'))
        h.update(b'\0')
    return h.hexdigest()[:16]


_HASHES: dict[tuple, str] = {}        # (path, mtime, size) -> digest: many steps read the same big package files


def _file_hash(path: Path) -> str | None:
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        if key not in _HASHES:
            _HASHES[key] = hashlib.sha1(path.read_bytes()).hexdigest()[:16]
        return _HASHES[key]
    except OSError:
        return None


def _glob(folder: Path, patterns: list[str]) -> list[Path]:
    out: list[Path] = []
    for pattern in patterns:
        found = sorted(folder.glob(pattern)) if any(c in pattern for c in '*?[') else [folder / pattern]
        out.extend(p for p in found if p not in out)
    return out


def read_manifest(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except OSError as e:
        raise PipelineError(f'manifest {path}: {e}') from None
    except ValueError as e:
        raise PipelineError(f'manifest {path.name} is not JSON: {e}') from None
    if not isinstance(data, dict) or not isinstance(data.get('steps'), list) or not data['steps']:
        raise PipelineError(f'manifest {path.name}: {{"map": name, "steps": [{{"run": "gen.py", "packages": '
                            '["args_a.json"]}, ...]}')
    for i, step in enumerate(data['steps']):
        if not isinstance(step, dict) or not ({'run', 'tool', 'packages'} & set(step)):
            raise PipelineError(f'manifest step {i}: needs "run" (a generator), "tool" (an MCP tool call) or '
                                '"packages"')
        unknown = set(step) - {'run', 'args', 'inputs', 'packages', 'needs', 'tool', 'timeout'}
        if unknown:
            raise PipelineError(f'manifest step {i}: unknown field(s) {sorted(unknown)}')
    return data


def _step_name(step: dict) -> str:
    if 'run' in step:
        return ' '.join([str(step['run']), *map(str, step.get('args') or [])])
    if 'tool' in step:
        return f'{step["tool"]} {step.get("args", "")}'
    return ', '.join(step['packages'])


def run_generator(folder: Path, step: dict, say) -> dict:
    """Run one generator script in its folder (isolated Python, UTF-8) and return what it read: {deps: {file:
    hash}} for the next staleness check. A non-zero exit is an error with the end of its output."""
    script = project_path(step['run'], folder)
    deps_file = folder / '.pipeline' / f'deps-{os.getpid()}.json'
    deps_file.parent.mkdir(parents=True, exist_ok=True)
    runner = Path(__file__).with_name('genrun.py')
    # Isolated as -I (no user site, no script folder forced on sys.path, no PYTHON* of the user), but with a fixed
    # hash seed: a set of strings then iterates in the same order in every run. 12.11: a generator that looped over
    # set(materials) wrote its operations in another order each time; the package looked changed and was replayed.
    argv = [sys.executable, '-s', '-P', '-X', 'utf8', str(runner), str(deps_file), str(script),
            *[str(a) for a in step.get('args') or []]]
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PYTHON')}
    env.update(PYTHONUTF8='1', PYTHONHASHSEED='0')
    try:
        done = subprocess.run(argv, cwd=str(folder), env=env, capture_output=True, text=True, encoding='utf-8',
                              errors='replace', timeout=float(step.get('timeout') or GENERATOR_TIMEOUT),
                              creationflags=0x08000000)
    except subprocess.TimeoutExpired:
        raise PipelineError(f'generator {step["run"]} ran longer than {step.get("timeout") or GENERATOR_TIMEOUT} s '
                            'and was stopped') from None
    if done.returncode != 0:
        tail = '\n'.join((done.stderr or done.stdout or '').strip().splitlines()[-12:])
        raise PipelineError(f'generator {step["run"]} failed (exit {done.returncode}):\n{tail}')
    try:
        read = json.loads(deps_file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        read = []
    finally:
        try:
            deps_file.unlink()
        except OSError:
            pass
    deps = {}
    for name in read:
        path = Path(name)
        try:
            rel = path.resolve().relative_to(folder.resolve()).as_posix()
        except ValueError:
            continue
        if rel.startswith('.pipeline/') or '__pycache__' in rel:
            continue
        # A file the script looked for and did not find counts too (None): when a later step writes it, this
        # step is stale.
        deps[rel] = _file_hash(path)
    return {'deps': deps}


def _generator_key(folder: Path, step: dict) -> str:
    script = folder / step['run']
    declared = [f'{p.relative_to(folder).as_posix()}={_file_hash(p)}' for p in _glob(folder, step.get('inputs') or [])]
    return _sha(__version__, _file_hash(script), json.dumps(step.get('args') or []), *declared)


def _stale(folder: Path, step: dict, record: dict | None) -> str | None:
    """Why a generator step must run again, or None."""
    if record is None:
        return 'never run'
    if record.get('key') != _generator_key(folder, step):
        return 'its script, arguments or inputs changed'
    for rel, digest in (record.get('deps') or {}).items():
        if _file_hash(folder / rel) != digest:
            return f'{rel} changed'
    outputs = _glob(folder, step.get('packages') or [])
    if step.get('packages') and (not outputs or not all(p.is_file() for p in outputs)):
        return 'a package file is missing'
    return None


def run(project, name: str, manifest: Path, dry_run: bool = False, force: bool = False, say=print) -> dict:
    """The manifest's steps in order: stale generators, changed tool calls, then one apply of every package."""
    data = read_manifest(manifest)
    if data.get('map') not in (None, name):
        raise PipelineError(f'manifest {manifest.name} is for map {data["map"]!r}, not {name!r}')
    folder = manifest.parent
    state_file = folder / '.pipeline' / 'state.json'
    try:
        state = json.loads(state_file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    steps = data['steps']
    files: list[Path] = []
    needs: dict[str, list[str]] = {}
    ran, kept, tools = [], 0, []
    started = time.monotonic()

    def save_state():
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding='utf-8')
        os.replace(tmp, state_file)

    def stale_steps() -> list[str]:
        return [_step_name(s) for i, s in enumerate(steps)
                if 'run' in s and _stale(folder, s, state.get(f'{i}:{_step_name(s)}'))]

    # A step may read a file that a later step writes (a kerb generator that reads every other package): it saw the
    # previous run's file. So the steps are passed again while a generator that ran left an earlier one stale;
    # generators that feed each other without settling are named after MAX_PASSES.
    unsettled: list[str] = []
    for passed in range(1, MAX_PASSES + 1):
        files, needs, kept, ran_now = [], {}, 0, 0
        for i, step in enumerate(steps):
            label = _step_name(step)
            key = f'{i}:{label}'
            if 'run' in step:
                why = _stale(folder, step, state.get(key))
                if why:
                    say(f'== step {i + 1}/{len(steps)}: {label} ({why})' + (f', pass {passed}' if passed > 1 else ''))
                    t0 = time.monotonic()
                    got = run_generator(folder, step, say)
                    state[key] = {'key': _generator_key(folder, step), 'deps': got['deps'],
                                  'seconds': round(time.monotonic() - t0, 1)}
                    save_state()
                    ran.append({'run': label, 'why': why, 'seconds': state[key]['seconds'],
                                **({'pass': passed} if passed > 1 else {})})
                    ran_now += 1
                else:
                    kept += 1
            elif 'tool' in step:
                from . import surface
                args_file = project_path(step['args'], folder) if isinstance(step.get('args'), str) else None
                args = json.loads(args_file.read_text(encoding='utf-8-sig')) if args_file else (step.get('args') or {})
                digest = _sha(__version__, step['tool'], json.dumps(args, sort_keys=True))
                if (state.get(key) or {}).get('key') != digest:
                    say(f'== step {i + 1}/{len(steps)}: {label}')
                    fn = surface.PUBLIC.get(step['tool'])
                    if fn is None or step['tool'] in ('map_edit', 'map_build', 'game_launch', 'game_stop'):
                        raise PipelineError(f'manifest step {i}: tool {step["tool"]!r} cannot be a pipeline step')
                    if not dry_run:
                        result = fn(**args)
                        if isinstance(result, dict) and (result.get('ok') is False or result.get('error')):
                            raise PipelineError(f'{label} failed: {json.dumps(result, ensure_ascii=False)[:400]}')
                        state[key] = {'key': digest}
                        save_state()
                    if label not in tools:
                        tools.append(label)
                else:
                    kept += 1
            for p in _glob(folder, step.get('packages') or []):
                if not p.is_file():
                    raise PipelineError(f'manifest step {i} ({label}): package file {p.name} does not exist'
                                        + (' after the generator ran' if 'run' in step else ''))
                files.append(p)
                if step.get('needs') is not None:
                    needs[str(p)] = step['needs']
        unsettled = stale_steps() if ran_now else []
        if not unsettled:
            break
    if not files:
        raise PipelineError(f'manifest {manifest.name} lists no packages')
    packages = read_packages(files, needs=needs)
    if not project.s.map_path(name).exists():
        create = data.get('create') or {}
        say(f'creating map {name}')
        project.map_create(name, create.get('template', 'basic'), type=create.get('type'))
    say(f'== step {len(steps)}/{len(steps)}: applying {len(packages)} packages')
    try:
        source = manifest.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        source = str(manifest)
    out = apply(project, name, packages, dry_run, force, say, complete=True, source=source,
                template=(data.get('create') or {}).get('template'))
    out.update(generators_run=ran, steps_up_to_date=kept, seconds=round(time.monotonic() - started, 1))
    if tools:
        out['tools_called'] = tools
    if unsettled:
        out['unsettled'] = unsettled[:8]
        out['warning'] = (f'after {MAX_PASSES} passes these generators are stale again: each reads a file another one '
                          'writes and changes it back. The map holds the last pass; make one of them stop reading the '
                          "other's output, or write the same file for the same inputs")
    return out


# -- the background job ----------------------------------------------------------------------------------------------
def start(project, name: str, packages, dry_run: bool = False, force: bool = False) -> dict:
    """Start the job that brings map `name` to its packages. packages: a list of package files, or the path of a
    manifest (pipeline.json). Returns {job, ...}; the result comes with job_status."""
    from .config import check_name
    check_name(name, 'map name')
    if isinstance(packages, str):
        manifest = project_path(packages)
        read_manifest(manifest)                              # a broken manifest fails here, not in the job
        spec = {'manifest': str(manifest)}
        title = f'pipeline {name} ({manifest.parent.name}/{manifest.name})'
    elif isinstance(packages, list) and packages and all(isinstance(x, str) for x in packages):
        if not project.s.map_path(name).exists():
            raise PipelineError(f'map {name!r} does not exist: create it with map_create first')
        files = [str(project_path(x)) for x in packages]
        read_packages(files)
        spec = {'files': files}
        title = f'apply {name} ({len(files)} packages)'
    else:
        raise PipelineError('packages: a list of package files, or the path of a manifest (pipeline.json)')
    check_free(project.s, name)
    spec.update(map=name, dry_run=bool(dry_run), force=bool(force), addon=project.s.addon)
    spec_file = project.s.logs / f'pipeline-spec-{time.time_ns()}.json'
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(json.dumps(spec), encoding='utf-8')
    env = {'PYTHONPATH': str(Path(__file__).resolve().parents[1]), 'PYTHONUTF8': '1', 'CS2MAP_JOB': title}
    job = project.jobs.start('pipeline', title, [sys.executable, '-m', 'cs2map.pipeline', str(spec_file)],
                             PROJECT_ROOT, timeout=3 * 3600, on_finish=_result, env=env)
    return {'job': job.id, 'state': job.state, 'log': job.log,
            'hint': 'poll job_status(job, wait_seconds=25) until state is not running; result: replayed, skipped, '
                    'generators_run, errors'}


def _short_source(files: list[str]) -> str:
    """The package list as a release refusal can show it: the folder and how many files."""
    folders = {str(Path(f).parent) for f in files}
    folder = folders.pop() if len(folders) == 1 else ''
    try:
        folder = Path(folder).relative_to(PROJECT_ROOT).as_posix() if folder else ''
    except ValueError:
        pass
    return f'<the {len(files)} package files' + (f' in {folder}>' if folder else '>')


def _result(job, text: str) -> dict:
    for line in reversed(text.splitlines()):
        if line.startswith(RESULT_MARK):
            try:
                return json.loads(line[len(RESULT_MARK):])
            except ValueError:
                break
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith('$ ')]
    return {'ok': False, 'errors': lines[-6:] or ['the pipeline job ended without a result']}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print('usage: python -m cs2map.pipeline <spec.json>   (started by map_edit(packages=...))')
        return 2
    sys.stdout.reconfigure(encoding='utf-8')
    spec_file = Path(argv[0])
    spec = json.loads(spec_file.read_text(encoding='utf-8'))
    try:
        spec_file.unlink()
    except OSError:
        pass
    from . import server                                     # the project with every extension's operations
    project = server.project
    name = spec['map']

    def say(text: str) -> None:
        print(text, flush=True)

    try:
        with _Held(project.s, name, os.environ.get('CS2MAP_JOB', '')):
            if spec.get('manifest'):
                out = run(project, name, Path(spec['manifest']), spec['dry_run'], spec['force'], say)
            else:
                out = apply(project, name, read_packages(spec['files']), spec['dry_run'], spec['force'], say,
                            source=_short_source(spec['files']))
    except Exception as e:                                   # noqa: BLE001 - the job's result names it
        out = {'ok': False, 'errors': [f'{type(e).__name__}: {e}'[:3000]]}
    print(RESULT_MARK + json.dumps(out, ensure_ascii=False, default=str), flush=True)
    return 0 if out.get('ok') else 1


from .jobs import register_recovery  # noqa: E402

register_recovery('pipeline', _result)

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
