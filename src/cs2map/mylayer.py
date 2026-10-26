"""The local layer my/: what models learn on this computer, kept between chats.

    my/notes/<name>.md      lessons and rules of thumb ("lamps need a bracket", "the user likes warm light")
    my/recipes/<name>.md    what worked: batches, scenarios, prefab notes (+ optional <name>.json data)
    my/styles/<name>.json   own looks: materials, tints, models, sizes, light, rules (validated; used by map_edit)
    my/inbox/               the user's own files: photos, models, sounds (tools only read it)
    my/maps/<map>/          what builds a map: generator scripts, batches, plans (backups.py snapshots my/)

The folder is local: never in git, never touched by updates. Models write only here (system guides stay as
shipped). Every overwrite keeps the previous version in my/.history/ (at most HISTORY_KEEP per file).
The location is settings "my_dir" or the CS2MAP_MY variable; default <project>/my.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, SETTINGS_FILE

KINDS = {'note': 'notes', 'recipe': 'recipes', 'style': 'styles'}
NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')
TEXT_LIMIT = 32_000          # characters per note or recipe: notes are short lessons, not logs
DATA_LIMIT = 200_000         # characters of JSON per recipe or style
HISTORY_KEEP = 10
INDEX_LIMIT = 40             # entries per kind listed in the guide index
FEEDBACK = 'feedback.md'      # problems with this MCP for its author, one file the user can send
FEEDBACK_ENTRY = 1500         # characters per feedback entry
FEEDBACK_LIMIT = 200_000      # characters of the whole file
INBOX_KINDS = {
    'image': ('.png', '.jpg', '.jpeg', '.tga', '.bmp', '.webp', '.psd', '.exr'),
    'model': ('.fbx', '.obj', '.glb', '.gltf', '.blend'),
    'sound': ('.wav', '.mp3', '.ogg', '.flac'),
    'text': ('.txt', '.md', '.json'),
}
INBOX_USE = {
    'image': 'material_from_reference(name, image=<path>) for a tileable surface; decal_create(name, image=<path>) '
             'for a decal or poster; reference_facade(image=<path>) for a facade photo',
    'model': 'model_build(name, file=<path>, scale="auto") -> map_edit add_model',
    'sound': 'sound_create(name, file=<path>) -> sound_event -> map_edit add_sound',
    'text': 'read it: the user may describe the map or the style',
}
README = """# my/ — your local layer

This folder belongs to this computer and its user. It is never committed and never replaced by an update.
AI models that build maps with cs2map keep what they learn here and read it in every new chat.

| Folder | What goes there | Tool |
| --- | --- | --- |
| `notes/` | lessons and rules of thumb, the user's taste | `my_notes(action="write", kind="note", ...)` |
| `recipes/` | batches, scenarios and tricks that worked | `my_notes(kind="recipe")` |
| `styles/` | own looks: materials, tints, models, sizes, light | `my_notes(kind="style", data={...})`, `style_info(kind="mine")` |
| `inbox/` | your photos, models and sounds for the AI to use | `asset_search(kind="mine")` |
| `maps/<map>/` | what builds a map: generator scripts, batches, plans, data (keep them here, not in temp folders) | backed up with every map save |

Put files into `inbox/` and tell the model about them. Edit or delete notes freely: they are plain Markdown
and JSON. Earlier versions of overwritten files are kept in `.history/`.
"""

STYLE_KEYS = {'description', 'materials', 'tints', 'models', 'sizes', 'light', 'sky', 'rules', 'notes', 'based_on'}
STYLE_ROLES = re.compile(r'^[a-z][a-z0-9_]{0,31}$')


class MyError(ValueError):
    pass


def root() -> Path:
    """The my/ folder: CS2MAP_MY, settings "my_dir", or <project>/my."""
    env = os.environ.get('CS2MAP_MY')
    if env:
        return Path(env)
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding='utf-8')) if SETTINGS_FILE.is_file() else {}
    except (OSError, ValueError):
        data = {}
    return Path(data['my_dir']) if data.get('my_dir') else PROJECT_ROOT / 'my'


def ensure() -> Path:
    """Create the folders and the README once (cheap; called on every use)."""
    base = root()
    for sub in ('notes', 'recipes', 'styles', 'inbox', 'maps'):
        (base / sub).mkdir(parents=True, exist_ok=True)
    readme = base / 'README.md'
    if not readme.exists():
        readme.write_text(README, encoding='utf-8')
    return base


def _check_name(name: Any) -> str:
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise MyError('name: 1..64 chars a-z 0-9 _ - (starts with a letter or digit), e.g. "warm_light"')
    return name


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise MyError(f'kind must be one of {", ".join(KINDS)} (inbox files: asset_search kind="mine")')
    return KINDS[kind]


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.{uuid.uuid4().hex[:8]}.tmp')
    try:
        tmp.write_text(text, encoding='utf-8')
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _keep_history(base: Path, path: Path) -> None:
    if not path.exists():
        return
    hist = base / '.history' / path.parent.name
    hist.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    _write_atomic(hist / f'{path.stem}.{stamp}{path.suffix}', path.read_text(encoding='utf-8'))
    old = sorted(hist.glob(f'{path.stem}.*{path.suffix}'))
    for extra in old[:-HISTORY_KEEP]:
        extra.unlink()


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line.lstrip('#').strip()[:100] or fallback
    return fallback


def _files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if p.is_file() and not p.name.startswith('.')
                   and p.suffix in ('.md', '.json')), key=lambda p: p.name)


def _entries(kind: str) -> list[dict]:
    base = root()
    folder = base / _kind(kind)
    out: dict[str, dict] = {}
    for p in _files(folder):
        e = out.setdefault(p.stem, {'name': p.stem, 'files': []})
        e['files'].append(p.name)
        stamp = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        e['modified'] = max(e.get('modified', ''), stamp)
        if p.suffix == '.md':
            e['title'] = _title(p.read_text(encoding='utf-8', errors='replace'), p.stem)
        elif 'title' not in e:
            try:
                data = json.loads(p.read_text(encoding='utf-8'))
                e['title'] = str(data.get('description') or data.get('title') or p.stem)[:100] \
                    if isinstance(data, dict) else p.stem
            except ValueError:
                e['title'] = p.stem + ' (invalid JSON)'
    return list(out.values())


# ---- feedback for the MCP author -----------------------------------------------------------------------
FEEDBACK_HEAD = ('# Feedback for CS2 Map MCP\n\nProblems AI models met with this MCP on this computer: a tool error '
                 'they did not cause, a guide that was wrong, a feature they had to build by hand. Send this file to '
                 'the author (or paste it into a GitHub issue).\n')


def feedback_count() -> int:
    path = root() / FEEDBACK
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding='utf-8', errors='replace').splitlines() if line.startswith('## '))


def feedback_write(topic: str, text: str | None) -> dict:
    """Append one short entry to my/feedback.md; the same text for the same topic is recorded once."""
    base = ensure()
    _check_name(topic)
    if not isinstance(text, str) or not text.strip():
        raise MyError('feedback text: what you wanted, what happened (the error), how you worked around it, '
                      'what would help')
    body = text.strip()
    if len(body) > FEEDBACK_ENTRY:
        raise MyError(f'feedback entry is {len(body)} chars (limit {FEEDBACK_ENTRY}): keep it short')
    path = base / FEEDBACK
    old = path.read_text(encoding='utf-8') if path.is_file() else FEEDBACK_HEAD
    if f'· {topic}\n\n{body}\n' in old:
        return {'ok': True, 'kind': 'feedback', 'name': topic, 'note': 'already recorded', 'file': f'my/{FEEDBACK}'}
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    new = old.rstrip('\n') + f'\n\n## {stamp} · {topic}\n\n{body}\n'
    if len(new) > FEEDBACK_LIMIT:
        raise MyError(f'my/{FEEDBACK} is full ({FEEDBACK_LIMIT} chars): ask the user to send it and start a new one')
    _write_atomic(path, new)
    return {'ok': True, 'kind': 'feedback', 'name': topic, 'entries': feedback_count(), 'file': f'my/{FEEDBACK}'}


def feedback_read() -> dict:
    path = ensure() / FEEDBACK
    return {'kind': 'feedback', 'file': f'my/{FEEDBACK}', 'entries': feedback_count(),
            'text': path.read_text(encoding='utf-8') if path.is_file() else ''}


# ---- the tool ------------------------------------------------------------------------------------------
def list_all(kind: str | None = None) -> dict:
    ensure()
    kinds = [kind] if kind else list(KINDS)
    out: dict[str, Any] = {'my': str(root())}
    for k in kinds:
        out[KINDS[k] if k in KINDS else _kind(k)] = _entries(k)
    if not kind:
        out['inbox'] = len(inbox_files())
        out['feedback'] = feedback_count()
        out['hint'] = ('read one: my_notes(action="read", kind, name); the guide lists them too '
                       '(read_guide(section="my") prints every note)')
    return out


def read(kind: str, name: str) -> dict:
    ensure()
    folder = root() / _kind(kind)
    _check_name(name)
    md, js = folder / f'{name}.md', folder / f'{name}.json'
    if not md.exists() and not js.exists():
        names = [e['name'] for e in _entries(kind)]
        raise MyError(f'no {kind} {name!r}; existing: {", ".join(names) or "none"}')
    out: dict[str, Any] = {'kind': kind, 'name': name}
    if md.exists():
        out['text'] = md.read_text(encoding='utf-8', errors='replace')
    if js.exists():
        try:
            out['data'] = json.loads(js.read_text(encoding='utf-8'))
        except ValueError as e:
            out['data_error'] = f'invalid JSON in {js.name}: {e}'
    return out


def write(kind: str, name: str, text: str | None = None, data: Any = None, append: bool = False) -> dict:
    """Write a note, recipe or style. Overwrites keep the old version in .history/. append adds to a note."""
    base = ensure()
    folder = base / _kind(kind)
    _check_name(name)
    if text is None and data is None:
        raise MyError('give text (Markdown) and/or data (JSON: a recipe batch or a style)')
    written = []
    if kind == 'style':
        if data is None:
            raise MyError('a style is data={...}: description, materials, tints, models, sizes, light, rules '
                          '(read_guide(section="styles"))')
        data = validate_style(data)
    if kind == 'note' and data is not None:
        raise MyError('a note is text (Markdown); batches and other JSON go to kind="recipe"')
    if text is not None:
        if not isinstance(text, str) or not text.strip():
            raise MyError('text must be non-empty Markdown')
        path = folder / f'{name}.md'
        if append and path.exists():
            old = path.read_text(encoding='utf-8')
            text = old.rstrip('\n') + '\n\n' + text.strip('\n') + '\n'
        elif not text.endswith('\n'):
            text += '\n'
        if len(text) > TEXT_LIMIT:
            raise MyError(f'{kind} is {len(text)} chars (limit {TEXT_LIMIT}): keep notes short; split by topic')
        _keep_history(base, path)
        _write_atomic(path, text)
        written.append(path.name)
    if data is not None:
        dumped = json.dumps(data, ensure_ascii=False, indent=1)
        if len(dumped) > DATA_LIMIT:
            raise MyError(f'data is {len(dumped)} chars (limit {DATA_LIMIT})')
        path = folder / f'{name}.json'
        _keep_history(base, path)
        _write_atomic(path, dumped + '\n')
        written.append(path.name)
    out = {'ok': True, 'kind': kind, 'name': name, 'files': [f'my/{folder.name}/{w}' for w in written]}
    if kind == 'style':
        out['use'] = (f'map_edit: "style":"my:{name}" in add_facade/add_paving/add_building, '
                      f'"preset":"my:{name}" in lighting_preset, "${name}.materials.<role>" in any field')
    return out


# ---- inbox ---------------------------------------------------------------------------------------------
def _inbox_kind(p: Path) -> str:
    for kind, exts in INBOX_KINDS.items():
        if p.suffix.lower() in exts:
            return kind
    return 'other'


def inbox_files() -> list[Path]:
    folder = root() / 'inbox'
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.rglob('*') if p.is_file() and not p.name.startswith('.'))


def inbox_search(query: str = '', limit: int = 50) -> dict:
    ensure()
    words = query.lower().split()
    found = []
    for p in inbox_files():
        rel = p.relative_to(root() / 'inbox').as_posix()
        kind = _inbox_kind(p)
        if words and not all(w in (rel.lower() + ' ' + kind) for w in words):
            continue
        found.append({'file': str(p), 'name': rel, 'kind': kind, 'kb': round(p.stat().st_size / 1024, 1)})
    uses = {f['kind'] for f in found}
    return {'kind': 'mine', 'inbox': str(root() / 'inbox'), 'files': found[:limit], 'total': len(found),
            'use': {k: INBOX_USE[k] for k in sorted(uses) if k in INBOX_USE},
            **({} if found else {'hint': 'the inbox is empty: ask the user to put photos, models or sounds into '
                                         + str(root() / 'inbox')})}


# ---- styles --------------------------------------------------------------------------------------------
def _color(v: Any, where: str) -> list[int]:
    if isinstance(v, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', v):
        return [int(v[i:i + 2], 16) for i in (1, 3, 5)]
    if (isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) and 0 <= x <= 255 for x in v)):
        return [int(round(x)) for x in v]
    raise MyError(f'{where}: a colour is [r,g,b] 0..255 or "#rrggbb"')


def _asset(v: Any, ext: str, where: str) -> str:
    if not isinstance(v, str) or not v.endswith('.' + ext) or '..' in v or ':' in v or v.startswith(('/', '\\')):
        raise MyError(f'{where}: an addon or stock path ending in .{ext} (e.g. materials/.../x.{ext})')
    return v.replace('\\', '/')


def _roles(value: Any, where: str, fn) -> dict:
    if not isinstance(value, dict):
        raise MyError(f'{where} must be an object {{role: value}}')
    out = {}
    for role, v in value.items():
        if not isinstance(role, str) or not STYLE_ROLES.fullmatch(role):
            raise MyError(f'{where}: role names are a-z0-9_ (wall, roof, floor, trim, ...), got {role!r}')
        out[role] = [fn(x, f'{where}.{role}[{i}]') for i, x in enumerate(v)] if isinstance(v, list) and v \
            and not (where == 'tints' and len(v) == 3 and all(isinstance(x, (int, float)) for x in v)) \
            else fn(v, f'{where}.{role}')
    return out


def validate_style(data: Any) -> dict:
    """Check a style document; unknown keys are refused so typos surface at once."""
    if not isinstance(data, dict):
        raise MyError('a style is an object')
    unknown = set(data) - STYLE_KEYS
    if unknown:
        raise MyError(f'unknown style keys {sorted(unknown)}; allowed: {", ".join(sorted(STYLE_KEYS))}')
    if not isinstance(data.get('description'), str) or not data['description'].strip():
        raise MyError('style.description: one line about the look (it is shown in lists)')
    out = dict(data)
    if 'materials' in data:
        out['materials'] = _roles(data['materials'], 'materials', lambda v, w: _asset(v, 'vmat', w))
    if 'models' in data:
        out['models'] = _roles(data['models'], 'models', lambda v, w: _asset(v, 'vmdl', w))
    if 'tints' in data:
        out['tints'] = _roles(data['tints'], 'tints', _color)
    if 'sizes' in data:
        sizes = data['sizes']
        if not isinstance(sizes, dict) or not all(isinstance(v, (int, float)) and 0 < v <= 16384
                                                  for v in sizes.values()):
            raise MyError('style.sizes: {name: units}, e.g. {"floor_height": 128, "house_width": 256}')
    if 'light' in data:
        light = data['light']
        if not isinstance(light, dict) or 'preset' not in light:
            raise MyError('style.light: {"preset": <lighting preset>, "fog": ...} (light section)')
        allowed = {'preset', 'fog'}
        if set(light) - allowed:
            raise MyError(f'style.light keys: {", ".join(sorted(allowed))}')
    if 'rules' in data and (not isinstance(data['rules'], list) or not all(isinstance(r, str) for r in data['rules'])):
        raise MyError('style.rules: a list of short sentences ("roofs steep, 40°", "no plaster, timber only")')
    for key in ('sky', 'notes', 'based_on'):
        if key in data and not isinstance(data[key], str):
            raise MyError(f'style.{key} must be a string')
    return out


def style_load(name: str) -> dict:
    _check_name(name)
    path = root() / 'styles' / f'{name}.json'
    if not path.exists():
        names = [e['name'] for e in _entries('style')]
        raise MyError(f'no own style {name!r} in my/styles; existing: {", ".join(names) or "none"} '
                      '(save one: my_notes(action="write", kind="style", name, data))')
    try:
        return validate_style(json.loads(path.read_text(encoding='utf-8')))
    except ValueError as e:
        raise MyError(f'my/styles/{name}.json: {e}') from None


def styles_info(name: str | None = None) -> dict:
    ensure()
    if name:
        out = {'style': name, **style_load(name)}
        out['use'] = (f'"style":"my:{name}" in add_facade/add_paving/add_building, "preset":"my:{name}" in '
                      f'lighting_preset, "${name}.materials.<role>" (or .models/.tints/.sizes) in any map_edit field')
        return out
    return {'styles': [{'name': e['name'], 'description': e.get('title', '')} for e in _entries('style')],
            'hint': 'style_info(kind="mine", name=...) shows one; save with my_notes(kind="style")'}


def style_value(name: str, path: list[str]) -> Any:
    data = style_load(name)
    value: Any = data
    for part in path:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise MyError(f'style {name!r} has no {".".join(path)} (keys here: '
                          f'{", ".join(value) if isinstance(value, dict) else "-"})')
    return value


# ---- the guide -----------------------------------------------------------------------------------------
def guide_index() -> str:
    """The block appended to the core guide: what this computer's models already learned."""
    try:
        notes, recipes, styles = _entries('note'), _entries('recipe'), _entries('style')
        inbox = len(inbox_files())
    except OSError:
        return ''
    lines = ['', '## Your local layer my/ (this computer, earlier chats)', '']
    if not (notes or recipes or styles or inbox):
        lines.append('Empty so far. When you learn something that will help next time (a fix, a rule, the user\'s '
                     'taste, a batch that worked), save it: `my_notes(action="write", kind="note", name, text)`.')
        return '\n'.join(lines) + '\n'
    lines.append('Read the notes that touch your task before you start: `read_guide(section="my")` prints every '
                 'note, `my_notes(action="read", kind, name)` one entry.')
    for title, items in (('Notes', notes), ('Recipes', recipes), ('Styles', styles)):
        if items:
            shown = ', '.join(f'`{e["name"]}` ({e.get("title", "")[:60]})' for e in items[:INDEX_LIMIT])
            more = f' … +{len(items) - INDEX_LIMIT}' if len(items) > INDEX_LIMIT else ''
            lines.append(f'- {title}: {shown}{more}')
    if inbox:
        lines.append(f'- Inbox: {inbox} file(s) from the user — `asset_search(kind="mine")`')
    return '\n'.join(lines) + '\n'


def guide_section(name: str = '') -> str:
    """read_guide(section="my") — every note in full; "my:<name>" — one note or recipe."""
    ensure()
    if name:
        for kind in ('note', 'recipe'):
            try:
                r = read(kind, name)
            except MyError:
                continue
            body = r.get('text', '')
            if 'data' in r:
                body += '\n```json\n' + json.dumps(r['data'], ensure_ascii=False, indent=1)[:20000] + '\n```\n'
            return body
        raise MyError(f'no note or recipe {name!r} in my/; my_notes(action="list") shows them')
    parts = ['# Your notes (my/notes)\n']
    notes = _entries('note')
    if not notes:
        parts.append('No notes yet. Save lessons with my_notes(action="write", kind="note", name, text).\n')
    for e in notes:
        parts.append((root() / 'notes' / f'{e["name"]}.md').read_text(encoding='utf-8', errors='replace')
                     if f'{e["name"]}.md' in e['files'] else '')
    recipes, styles = _entries('recipe'), _entries('style')
    if recipes or styles:
        parts.append('\n## Also in my/\n')
        if recipes:
            parts.append('Recipes: ' + ', '.join(f'`{e["name"]}`' for e in recipes) + ' — read_guide(section="my:<name>")')
        if styles:
            parts.append('\nStyles: ' + ', '.join(f'`{e["name"]}`' for e in styles) + ' — style_info(kind="mine", name)')
    return '\n'.join(parts) + '\n'
