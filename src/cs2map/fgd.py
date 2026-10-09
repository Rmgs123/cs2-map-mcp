"""Entity definitions from the game's FGD files (classes, keyvalues, defaults, I/O).

Hammer fills every keyvalue of a new entity from the FGD, so we do the same to
produce entities identical to hand-placed ones.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_TOKEN = re.compile(r'''
    (?P<ws>\s+) | (?P<comment>//[^\n]*) |
    (?P<string>"(?:[^"\\]|\\.)*") |
    (?P<punct>[@()\[\]{}:=,+]) |
    (?P<word>[^\s@()\[\]{}:=,+"]+)
''', re.VERBOSE)

# keys Hammer stores as node attributes instead of entity properties
NODE_KEYS = {'origin', 'angles', 'scales'}


@dataclass
class Prop:
    name: str
    type: str
    display: str = ''
    default: str = ''
    description: str = ''
    choices: list[tuple[str, str]] = field(default_factory=list)
    readonly: bool = False


@dataclass
class EntityClass:
    name: str
    kind: str                     # PointClass, SolidClass, BaseClass, ...
    description: str = ''
    bases: list[str] = field(default_factory=list)
    props: dict[str, Prop] = field(default_factory=dict)
    inputs: dict[str, str] = field(default_factory=dict)    # name -> "type: description"
    outputs: dict[str, str] = field(default_factory=dict)
    metadata: str = ''
    helper_keys: list[str] = field(default_factory=list)   # keys a Hammer helper edits, e.g. box_mins


class _Tokens:
    def __init__(self, text: str):
        self.items: list[tuple[str, str]] = []
        for m in _TOKEN.finditer(text):
            kind = m.lastgroup
            if kind in ('ws', 'comment'):
                continue
            value = m.group(kind)
            if kind == 'string':
                value = value[1:-1].replace('\\"', '"').replace('\\n', '\n')
            self.items.append((kind, value))
        self.i = 0

    def peek(self, k: int = 0):
        j = self.i + k
        return self.items[j] if j < len(self.items) else ('eof', '')

    def next(self):
        tok = self.peek()
        self.i += 1
        return tok

    def accept(self, value: str) -> bool:
        if self.peek()[1] == value and self.peek()[0] in ('punct', 'word'):
            self.i += 1
            return True
        return False

    def skip_block(self, open_: str, close: str) -> str:
        """Skip a balanced block starting at the current `open_` token; return its raw text."""
        depth, parts = 0, []
        while True:
            kind, value = self.next()
            if kind == 'eof':
                return ' '.join(parts)
            if kind == 'punct' and value == open_:
                depth += 1
                if depth == 1:
                    continue
            elif kind == 'punct' and value == close:
                depth -= 1
                if depth == 0:
                    return ' '.join(parts)
            parts.append(value)

    def string(self) -> str:
        """Read one string, joining '+' continuations."""
        kind, value = self.peek()
        if kind != 'string':
            return ''
        self.i += 1
        out = value
        while self.peek()[1] == '+' and self.peek(1)[0] == 'string':
            self.i += 1
            out += self.next()[1]
        return out


def _parse_class(t: _Tokens, kind: str) -> EntityClass | None:
    bases: list[str] = []
    helper_keys: list[str] = []
    metadata = ''
    # header: helpers like base(...), iconsprite(...), metadata {...} until '='
    while True:
        k, v = t.peek()
        if k == 'eof':
            return None
        if k == 'punct' and v == '=':
            t.next()
            break
        if k == 'punct' and v == '[':   # classes without '=' (e.g. @mapsize) - not entities
            return None
        t.next()
        if k == 'word' and t.peek()[1] == '(':
            inner = t.skip_block('(', ')')
            if v.lower() == 'base':
                bases += [b.strip() for b in inner.replace(',', ' ').split() if b.strip()]
            elif v.lower() not in ('iconsprite', 'studio', 'color', 'size', 'tags', 'model', 'sprite'):
                # volumetric_fog_controller( box_mins, box_maxs ): Hammer stores these keys on the entity.
                helper_keys += [w for w in re.findall(r'[A-Za-z_]\w*', inner) if w == w.lower()]
        elif k == 'word' and t.peek()[1] == '{':
            text = t.skip_block('{', '}')
            if v == 'metadata':
                metadata = text
    kind_tok, name = t.next()
    if kind_tok != 'word':
        return None
    desc = ''
    if t.accept(':'):
        desc = t.string()
    cls = EntityClass(name=name, kind=kind, description=desc, bases=bases, metadata=metadata,
                      helper_keys=helper_keys)
    if not t.accept('['):
        return cls
    while True:
        k, v = t.peek()
        if k == 'eof':
            break
        if k == 'punct' and v == ']':
            t.next()
            break
        if k == 'word' and v in ('input', 'output') and t.peek(2)[1] == '(':
            t.next()
            io_name = t.next()[1]
            io_type = t.skip_block('(', ')')
            io_desc = t.string() if t.accept(':') else ''
            (cls.inputs if v == 'input' else cls.outputs)[io_name] = f'{io_type}: {io_desc}'.strip()
            continue
        if k == 'word' and t.peek(1)[1] == '(':
            _parse_prop(t, cls)
            continue
        t.next()   # unknown token; keep going
    return cls


def _parse_prop(t: _Tokens, cls: EntityClass) -> None:
    name = t.next()[1]
    ptype = t.skip_block('(', ')').strip()
    prop = Prop(name=name, type=ptype.lower())
    # flags and decoration blocks before the first ':'
    while True:
        k, v = t.peek()
        if k == 'word' and v.lower() in ('readonly', 'report'):
            prop.readonly = prop.readonly or v.lower() == 'readonly'
            t.next()
        elif k == 'punct' and v == '[':
            t.skip_block('[', ']')
        elif k == 'punct' and v == '{':
            t.skip_block('{', '}')
        else:
            break
    if t.accept(':'):
        prop.display = t.string()
        if t.accept(':'):
            k, v = t.peek()
            if k in ('string', 'word'):
                prop.default = t.string() if k == 'string' else t.next()[1]
            if t.accept(':'):
                prop.description = t.string()
    if t.accept('='):
        if t.peek()[1] == '[':
            t.next()
            while True:
                k, v = t.peek()
                if k == 'eof' or (k == 'punct' and v == ']'):
                    t.next()
                    break
                value = t.next()[1]
                label = ''
                if t.accept(':'):
                    label = t.string()
                    if t.accept(':'):   # spawnflags: value : "label" : default
                        flag_default = t.next()[1]
                        if prop.type == 'flags' and flag_default == '1':
                            prop.default = str(int(prop.default or '0') + int(value))
                prop.choices.append((value, label))
    cls.props[name] = prop


def parse(text: str, classes: dict[str, EntityClass] | None = None) -> dict[str, EntityClass]:
    classes = {} if classes is None else classes
    t = _Tokens(text)
    while True:
        k, v = t.next()
        if k == 'eof':
            return classes
        if not (k == 'punct' and v == '@'):
            continue
        kind = t.next()[1]
        if not kind.lower().endswith('class'):
            continue
        cls = _parse_class(t, kind)
        if cls is None:
            continue
        old = classes.get(cls.name)
        if old is not None:   # later files extend/override earlier definitions
            if kind != 'OverrideClass':
                old.kind = cls.kind
            old.description = cls.description or old.description
            old.bases += [b for b in cls.bases if b not in old.bases]
            old.props.update(cls.props)
            old.inputs.update(cls.inputs)
            old.outputs.update(cls.outputs)
            old.metadata = old.metadata or cls.metadata
            old.helper_keys += [k for k in cls.helper_keys if k not in old.helper_keys]
        else:
            classes[cls.name] = cls


class Catalog:
    """All entity classes of the installed game."""

    def __init__(self, classes: dict[str, EntityClass]):
        self.classes = classes

    @classmethod
    def load(cls, cs2_path: Path) -> 'Catalog':
        return _load(str(cs2_path))

    def get(self, name: str) -> EntityClass:
        c = self.classes.get(name)
        if c is None or c.kind == 'BaseClass':
            close = [n for n in self.classes if name.lower() in n.lower() and self.classes[n].kind != 'BaseClass']
            raise KeyError(f'unknown entity class {name!r}' + (f'; similar: {", ".join(sorted(close)[:8])}' if close else ''))
        return c

    def _chain(self, name: str, seen: set[str]) -> list[EntityClass]:
        c = self.classes.get(name)
        if c is None or name in seen:
            return []
        seen.add(name)
        out: list[EntityClass] = []
        for b in c.bases:
            out += self._chain(b, seen)
        out.append(c)
        return out

    def props(self, name: str) -> dict[str, Prop]:
        merged: dict[str, Prop] = {}
        for c in self._chain(name, set()):
            for k, p in c.props.items():
                if p.type == 'remove_key':      # CS2 FGDs delete inherited keys this way
                    merged.pop(k, None)
                else:
                    merged[k] = p
        return merged

    def helper_keys(self, name: str) -> set[str]:
        return {k for c in self._chain(name, set()) for k in c.helper_keys}

    def io(self, name: str) -> tuple[dict[str, str], dict[str, str]]:
        ins: dict[str, str] = {}
        outs: dict[str, str] = {}
        for c in self._chain(name, set()):
            ins.update(c.inputs)
            outs.update(c.outputs)
        return ins, outs

    def defaults(self, name: str) -> dict[str, str]:
        """Keyvalues Hammer writes for a freshly placed entity (without node keys)."""
        return {k: p.default for k, p in self.props(name).items() if k not in NODE_KEYS}

    def search(self, text: str, limit: int = 50) -> list[tuple[str, str, str]]:
        text = text.lower()
        hits = [(c.name, c.kind, c.description) for c in self.classes.values()
                if c.kind != 'BaseClass' and (text in c.name.lower() or text in c.description.lower())]
        hits.sort(key=lambda h: (not h[0].lower().startswith(text), h[0]))
        return hits[:limit]

    def describe(self, name: str, with_props: bool = True) -> dict:
        c = self.get(name)
        ins, outs = self.io(name)
        info = {'classname': c.name, 'kind': c.kind, 'description': c.description,
                'brush_entity': c.kind == 'SolidClass'}
        if with_props:
            info['keyvalues'] = [
                {'key': p.name, 'type': p.type, 'default': p.default, 'label': p.display,
                 **({'help': p.description} if p.description else {}),
                 **({'choices': {v: lbl for v, lbl in p.choices}} if p.choices else {})}
                for p in self.props(name).values() if p.name not in NODE_KEYS]
            info['inputs'] = sorted(ins)
            info['outputs'] = sorted(outs)
        return info


@lru_cache(maxsize=2)
def _load(cs2_path: str) -> Catalog:
    game = Path(cs2_path) / 'game'
    classes: dict[str, EntityClass] = {}
    files = sorted((game / 'core').glob('*.fgd')) + sorted((game / 'csgo').glob('*.fgd'))
    if not files:
        raise FileNotFoundError(f'no FGD files under {game}')
    # core definitions first, then csgo (which extends/overrides them)
    for f in files:
        parse(f.read_text(encoding='utf-8', errors='replace'), classes)
    return Catalog(classes)
