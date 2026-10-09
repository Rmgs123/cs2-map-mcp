"""KeyValues3 text format (.vmdl, .vpost, .vsmart, .vdata, animgraphs...): parse and write.

Values map to Python as: dict (object), list (array), str, int, float, bool, None.
Typed/flagged values such as  resource:"models/x.vmdl"  are returned as Flagged(flag, value)
and written back the same way.  Multi-line strings (\"\"\"...\"\"\") are supported.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

HEADER_GENERIC = ('<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} '
                  'format:generic:version{7412167c-06e9-4698-aff2-e63eb59037e7} -->')
HEADER_MODELDOC = ('<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} '
                   'format:modeldoc41:version{12fc9d44-453a-4ae4-b4d9-7e2ac0bbd4e0} -->')


class KV3Error(ValueError):
    pass


@dataclass(frozen=True)
class Flagged:
    flag: str      # e.g. 'resource', 'resource_name', 'soundevent', 'panorama', 'subclass'
    value: Any


_TOKEN = re.compile(r'''
    (?P<ws>\s+) | (?P<comment>//[^\n]*|/\*.*?\*/) |
    (?P<mstr>"""\n?(?:.|\n)*?""") |
    (?P<str>"(?:[^"\\]|\\.)*") |
    (?P<punct>[{}\[\]=,:]) |
    (?P<word>[^\s{}\[\]=,:"]+)
''', re.VERBOSE | re.DOTALL)


def _tokens(text: str):
    pos = 0
    out = []
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise KV3Error(f'unexpected character {text[pos]!r} at {pos}')
        pos = m.end()
        kind = m.lastgroup
        if kind in ('ws', 'comment'):
            continue
        out.append((kind, m.group(kind)))
    return out


def loads(text: str) -> tuple[str, Any]:
    """Return (header, value).  The header is the <!-- kv3 ... --> line ('' if absent)."""
    header = ''
    stripped = text.lstrip('﻿')
    if stripped.startswith('<!--'):
        end = stripped.index('-->') + 3
        header, stripped = stripped[:end], stripped[end:]
    toks = _tokens(stripped)
    i = 0

    def value():
        nonlocal i
        if i >= len(toks):
            raise KV3Error('unexpected end of file')
        kind, tok = toks[i]
        # flagged value: word ':' value
        if kind == 'word' and i + 1 < len(toks) and toks[i + 1] == ('punct', ':'):
            i += 2
            return Flagged(tok, value())
        i += 1
        if kind == 'punct' and tok == '{':
            obj = {}
            while toks[i] != ('punct', '}'):
                k_kind, key = toks[i]
                if k_kind == 'str':
                    key = _unquote(key)
                elif k_kind != 'word':
                    raise KV3Error(f'bad key {key!r}')
                if toks[i + 1] != ('punct', '='):
                    raise KV3Error(f'expected = after {key!r}')
                i += 2
                obj[key] = value()
                if i < len(toks) and toks[i] == ('punct', ','):
                    i += 1
            i += 1
            return obj
        if kind == 'punct' and tok == '[':
            arr = []
            while toks[i] != ('punct', ']'):
                arr.append(value())
                if toks[i] == ('punct', ','):
                    i += 1
            i += 1
            return arr
        if kind == 'str':
            return _unquote(tok)
        if kind == 'mstr':
            body = tok[3:-3]
            return body[1:] if body.startswith('\n') else body
        if kind == 'word':
            if tok == 'true':
                return True
            if tok == 'false':
                return False
            if tok == 'null':
                return None
            try:
                return int(tok)
            except ValueError:
                try:
                    return float(tok)
                except ValueError:
                    return tok
        raise KV3Error(f'unexpected token {tok!r}')

    result = value()
    if i != len(toks):
        raise KV3Error('trailing data after the root value')
    return header, result


def _unquote(tok: str) -> str:
    return tok[1:-1].replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')


def _key(k: str) -> str:
    return k if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]*', k) else '"' + k.replace('"', '\\"') + '"'


def _scalar(v) -> str:
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        r = repr(v)
        return r if ('.' in r or 'e' in r or 'n' in r) else r + '.0'
    if isinstance(v, str):
        if '\n' in v:
            return '"""\n' + v + '"""'
        return '"' + v.replace('\\', '\\\\').replace('"', '\\"') + '"'
    if isinstance(v, Flagged):
        return f'{v.flag}:{_scalar(v.value)}'
    raise KV3Error(f'cannot write {type(v).__name__}')


def _write(v, ind: int) -> str:
    pad = '\t' * ind
    if isinstance(v, dict):
        if not v:
            return '{}'
        body = ''.join(f'{pad}\t{_key(k)} = {_write(x, ind + 1)}\n' for k, x in v.items())
        return f'\n{pad}{{\n{body}{pad}}}'
    if isinstance(v, list):
        if not v:
            return '[  ]'
        if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v) and len(v) <= 16:
            return '[ ' + ', '.join(_scalar(x) for x in v) + ' ]'
        items = ''.join(f'{pad}\t{_write(x, ind + 1).lstrip(chr(10))},\n' for x in v)
        return f'\n{pad}[\n{items}{pad}]'
    return _scalar(v)


def dumps(value: dict, header: str = HEADER_GENERIC) -> str:
    if not isinstance(value, dict):
        raise KV3Error('root must be an object')
    body = ''.join(f'\t{_key(k)} = {_write(x, 1)}\n' for k, x in value.items())
    return f'{header}\n{{\n{body}}}\n'
