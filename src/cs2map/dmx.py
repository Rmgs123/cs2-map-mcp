"""Pure-Python reader/writer for Valve DMX binary encoding version 9.

VMAP files in CS2 are DMX documents.  The codec keeps everything it does not
understand: element order, attribute order and types, the prefix block, stub
references and orphan elements.  An unmodified document re-encodes to the
original bytes (checked by tests against Valve's own maps).

Values are stored as plain Python objects together with an explicit type tag,
so nothing is lost when a float32/int32/uint64 passes through Python.
"""
from __future__ import annotations

import struct
import uuid
from itertools import chain
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

TYPES = ('element', 'int', 'float', 'bool', 'string', 'binary', 'time', 'color',
         'vector2', 'vector3', 'vector4', 'qangle', 'quaternion', 'matrix',
         'uint64', 'uint8')
_TYPE_ID = {name: i + 1 for i, name in enumerate(TYPES)}
_ARRAY_FLAG = 32
_FLOATS = {'vector2': 2, 'vector3': 3, 'vector4': 4, 'qangle': 3, 'quaternion': 4, 'matrix': 16}


class DmxError(ValueError):
    pass


@dataclass(frozen=True)
class Stub:
    """Reference to an element that is not stored in this file (by GUID)."""
    guid: str


@dataclass
class Attr:
    type: str          # e.g. 'int', 'vector3', 'element_array'
    value: Any

    @property
    def is_array(self) -> bool:
        return self.type.endswith('_array')

    @property
    def base(self) -> str:
        return self.type[:-6] if self.is_array else self.type


class Element:
    """A DMX element: class name, element name, GUID and ordered attributes."""
    __slots__ = ('type', 'name', 'id', 'attrs')

    def __init__(self, type: str, name: str = '', id: uuid.UUID | None = None,
                 attrs: dict[str, Attr] | None = None):
        self.type = type
        self.name = name
        self.id = id or uuid.uuid4()
        self.attrs: dict[str, Attr] = attrs if attrs is not None else {}

    # -- convenience -----------------------------------------------------
    def __getitem__(self, key: str) -> Any:
        return self.attrs[key].value

    def get(self, key: str, default: Any = None) -> Any:
        a = self.attrs.get(key)
        return default if a is None else a.value

    def __contains__(self, key: str) -> bool:
        return key in self.attrs

    def set(self, key: str, type: str, value: Any) -> None:
        """Set an attribute with an explicit type (keeps position if present)."""
        if type.removesuffix('_array') not in _TYPE_ID:
            raise DmxError(f'unknown DMX type {type!r}')
        self.attrs[key] = Attr(type, value)

    def __setitem__(self, key: str, value: Any) -> None:
        """Replace the value of an existing attribute, keeping its type."""
        if key not in self.attrs:
            raise KeyError(f'{key!r} not present on {self.type}; use set(key, type, value)')
        self.attrs[key].value = value

    def children(self) -> Iterator['Element']:
        """Directly referenced (non-stub) elements, in attribute order."""
        for a in self.attrs.values():
            if a.base != 'element':
                continue
            for v in (a.value if a.is_array else (a.value,)):
                if isinstance(v, Element):
                    yield v

    def __repr__(self) -> str:
        return f'<Element {self.type} {self.name!r} {self.id}>'


@dataclass
class Document:
    root: Element
    format: str = 'vmap'
    format_version: int = 40
    encoding_version: int = 9
    prefix: list[dict[str, Attr]] = field(default_factory=list)
    # Everything below is bookkeeping for faithful re-encoding.
    element_order: list[Element] = field(default_factory=list)
    string_order: list[str] = field(default_factory=list)
    # ids of elements reachable from the root in the source file (set by loads):
    # only unreachable ones are Valve orphans to keep; a node deleted later is not.
    source_reachable: set[int] = field(default_factory=set)

    def elements(self) -> list[Element]:
        """All elements reachable from the root (depth-first, stable)."""
        seen: dict[int, Element] = {}
        stack = [self.root]
        while stack:
            e = stack.pop()
            if id(e) in seen:
                continue
            seen[id(e)] = e
            stack.extend(reversed(list(e.children())))
        return list(seen.values())


# ---------------------------------------------------------------------------
# decoding

_I32 = struct.Struct('<i')
_U8 = struct.Struct('<B')
_I32x2 = struct.Struct('<ii')


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise DmxError('unexpected end of file')
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        v = struct.unpack_from('<' + fmt, self.data, self.pos)
        self.pos += size
        if self.pos > len(self.data):
            raise DmxError('unexpected end of file')
        return v

    def i32(self) -> int:
        pos = self.pos
        if pos + 4 > len(self.data):
            raise DmxError('unexpected end of file')
        self.pos = pos + 4
        return _I32.unpack_from(self.data, pos)[0]

    def cstring(self) -> str:
        end = self.data.find(b'\0', self.pos)
        if end < 0:
            raise DmxError('unterminated string')
        s = self.data[self.pos:end].decode('utf-8')
        self.pos = end + 1
        return s


def _parse_header(r: _Reader) -> tuple[str, int, str, int]:
    header = r.cstring()
    parts = header.strip().split()
    # <!-- dmx encoding binary 9 format vmap 40 -->
    if (len(parts) != 9 or parts[:3] != ['<!--', 'dmx', 'encoding'] or parts[5] != 'format'
            or parts[8] != '-->'):
        raise DmxError(f'not a DMX header: {header!r}')
    return parts[3], int(parts[4]), parts[6], int(parts[7])


def _read_scalar(r: _Reader, tag: str, strings: list[str] | None, elements: list[Element] | None,
                 inline_string: bool):
    if tag == 'element':
        idx = r.i32()
        if idx == -1:
            return None
        if idx == -2:
            return Stub(r.cstring())
        if elements is None:
            raise DmxError('element reference in prefix block')
        return elements[idx]
    if tag == 'int':
        return r.i32()
    if tag == 'float':
        return r.unpack('f')[0]
    if tag == 'bool':
        return r.take(1) != b'\0'
    if tag == 'string':
        if inline_string or strings is None:
            return r.cstring()
        return strings[r.i32()]
    if tag == 'binary':
        return r.take(r.i32())
    if tag == 'time':
        return r.i32()
    if tag == 'color':
        return tuple(r.take(4))
    if tag in _FLOATS:
        return r.unpack(f'{_FLOATS[tag]}f')
    if tag == 'uint64':
        return r.unpack('Q')[0]
    if tag == 'uint8':
        return r.take(1)[0]
    raise DmxError(f'unsupported type {tag}')


# Fixed-size array items that can be read/written with one struct call.
_BULK = {'int': ('i', 1), 'time': ('i', 1), 'float': ('f', 1), 'bool': ('?', 1), 'uint64': ('Q', 1),
         'uint8': ('B', 1), 'color': ('B', 4), **{k: ('f', n) for k, n in _FLOATS.items()}}


def _read_array(r: _Reader, tag: str, strings, elements) -> list:
    n = r.i32()
    if n < 0:
        raise DmxError('negative array length')
    bulk = _BULK.get(tag)
    if bulk:
        code, width = bulk
        flat = r.unpack(f'{n * width}{code}')
        if width == 1:
            return list(flat)
        return [flat[i:i + width] for i in range(0, len(flat), width)]
    return [_read_scalar(r, tag, strings, elements, True) for _ in range(n)]


def _read_attrs(r: _Reader, strings, elements, prefix: bool) -> dict[str, Attr]:
    attrs: dict[str, Attr] = {}
    for _ in range(r.i32()):
        name = r.cstring() if prefix else strings[r.i32()]
        type_id = r.take(1)[0]
        array = type_id >= _ARRAY_FLAG
        base_id = type_id - _ARRAY_FLAG if array else type_id
        if not 1 <= base_id <= len(TYPES):
            raise DmxError(f'unknown attribute type id {type_id} for {name!r}')
        tag = TYPES[base_id - 1]
        if array:
            value = _read_array(r, tag, strings, elements)
        else:
            value = _read_scalar(r, tag, strings, elements, prefix)
        attrs[name] = Attr(tag + '_array' if array else tag, value)
    return attrs


def loads(data: bytes) -> Document:
    r = _Reader(data)
    encoding, enc_ver, fmt, fmt_ver = _parse_header(r)
    if encoding != 'binary' or enc_ver != 9:
        raise DmxError(f'only binary 9 is supported, got {encoding} {enc_ver}')
    prefix = [_read_attrs(r, None, None, True) for _ in range(r.i32())]
    strings = [r.cstring() for _ in range(r.i32())]
    count = r.i32()
    elements: list[Element] = []
    for _ in range(count):
        type_idx, name_idx = r.unpack('ii')
        guid = uuid.UUID(bytes_le=r.take(16))
        elements.append(Element(strings[type_idx], strings[name_idx], guid))
    for e in elements:
        e.attrs = _read_attrs(r, strings, elements, False)
    if r.pos != len(data):
        raise DmxError(f'{len(data) - r.pos} trailing bytes')
    if not elements:
        raise DmxError('document has no elements')
    return Document(root=elements[0], format=fmt, format_version=fmt_ver, encoding_version=enc_ver,
                    prefix=prefix, element_order=elements, string_order=strings,
                    source_reachable=_reach(elements[0]))


def _reach(root: Element) -> set[int]:
    seen: set[int] = set()
    stack = [root]
    while stack:
        e = stack.pop()
        if id(e) in seen:
            continue
        seen.add(id(e))
        stack.extend(e.children())
    return seen


def load(path) -> Document:
    with open(path, 'rb') as f:
        return loads(f.read())


# ---------------------------------------------------------------------------
# encoding

class _Writer:
    def __init__(self):
        self.buf = bytearray()

    def pack(self, fmt: str, *v):
        self.buf += struct.pack('<' + fmt, *v)

    def cstring(self, s: str):
        b = s.encode('utf-8')
        if b'\0' in b:
            raise DmxError('string contains NUL')
        self.buf += b + b'\0'


def _write_bulk(w: _Writer, tag: str, values: list) -> None:
    code, width = _BULK[tag]
    if width == 1:
        if tag == 'bool' and not all(isinstance(v, bool) for v in values):
            raise DmxError('bool array needs True/False values')
        flat = values
    else:
        if values and set(map(len, values)) != {width}:
            raise DmxError(f'{tag} items need {width} components')
        flat = list(chain.from_iterable(values))
    try:
        w.pack(f'{len(flat)}{code}', *flat)
    except struct.error as e:
        raise DmxError(f'invalid {tag} array value: {e}') from None


def _write_scalar(w: _Writer, tag: str, v, strings: dict[str, int], index: dict[int, int], inline: bool):
    if tag in _BULK:
        _write_bulk(w, tag, [v])
        return
    if tag == 'element':
        if v is None:
            w.pack('i', -1)
        elif isinstance(v, Stub):
            w.pack('i', -2)
            w.cstring(v.guid)
        elif isinstance(v, Element):
            w.pack('i', index[id(v)])
        else:
            raise DmxError(f'element reference must be Element/Stub/None, got {type(v).__name__}')
    elif tag == 'string':
        if not isinstance(v, str):
            raise DmxError(f'string attribute got {type(v).__name__}')
        w.cstring(v) if inline else w.pack('i', strings[v])
    elif tag == 'binary':
        w.pack('i', len(v))
        w.buf += bytes(v)
    else:
        raise DmxError(f'unsupported type {tag}')


def _write_attrs(w: _Writer, attrs: dict[str, Attr], strings, index, prefix: bool):
    buf = w.buf
    buf += _I32.pack(len(attrs))
    for name, a in attrs.items():
        is_array = a.type.endswith('_array')
        tag = a.type[:-6] if is_array else a.type
        if tag not in _TYPE_ID:
            raise DmxError(f'unknown type {a.type!r} on {name!r}')
        if prefix and tag == 'element':
            raise DmxError('element references are not allowed in the prefix block')
        w.cstring(name) if prefix else buf.extend(_I32.pack(strings[name]))
        buf.append(_TYPE_ID[tag] + (_ARRAY_FLAG if is_array else 0))
        if is_array:
            buf += _I32.pack(len(a.value))
            if tag in _BULK:
                _write_bulk(w, tag, a.value)
            else:
                for v in a.value:
                    _write_scalar(w, tag, v, strings, index, True)
        else:
            _write_scalar(w, tag, a.value, strings, index, prefix)


def _ordered_elements(doc: Document) -> list[Element]:
    """Original order for surviving elements, original orphans kept, new ones appended."""
    reachable = doc.elements()
    reach_ids = {id(e) for e in reachable}
    original = doc.element_order or []
    orig_ids = {id(e) for e in original}
    # An original element survives if it is still reachable, or if it was never
    # reachable in the source file (a Valve orphan we must not drop).
    orig_reachable = doc.source_reachable or (_reach(original[0]) if original else set())
    out = [e for e in original if id(e) in reach_ids or id(e) not in orig_reachable]
    if not out or out[0] is not doc.root:
        out = [doc.root] + [e for e in out if e is not doc.root]
    out += [e for e in reachable if id(e) not in orig_ids and e is not doc.root]
    return out


def dumps(doc: Document) -> bytes:
    elements = _ordered_elements(doc)
    used: dict[str, None] = {}
    for e in elements:
        used[e.type] = None
        used[e.name] = None
        for name, a in e.attrs.items():
            used[name] = None
            if a.type == 'string':
                used[a.value] = None
    # Valve keeps strings nobody references (e.g. '$prefix_attribute$'); so do we.
    table = list(doc.string_order)
    known = set(table)
    table += [s for s in used if s not in known]
    strings = {s: i for i, s in enumerate(table)}
    index = {id(e): i for i, e in enumerate(elements)}
    # Stubs and element references must resolve to something we are writing.
    for e in elements:
        for a in e.attrs.values():
            if a.base == 'element':
                for v in (a.value if a.is_array else (a.value,)):
                    if isinstance(v, Element) and id(v) not in index:
                        raise DmxError(f'{e!r} references an element that is not in the document')
    w = _Writer()
    w.cstring(f'<!-- dmx encoding binary {doc.encoding_version} format {doc.format} {doc.format_version} -->\n')
    w.pack('i', len(doc.prefix))
    for p in doc.prefix:
        _write_attrs(w, p, None, None, True)
    w.pack('i', len(table))
    for s in table:
        w.cstring(s)
    w.pack('i', len(elements))
    for e in elements:
        w.pack('ii', strings[e.type], strings[e.name])
        w.buf += e.id.bytes_le
    for e in elements:
        _write_attrs(w, e.attrs, strings, index, False)
    return bytes(w.buf)


def dump(doc: Document, path) -> None:
    with open(path, 'wb') as f:
        f.write(dumps(doc))


# ---------------------------------------------------------------------------
# KeyValues2 text (debug view, same layout as dmxconvert -oe keyvalues2)

def _kv2_scalar(tag: str, v) -> str:
    if tag == 'element':
        if v is None:
            return ''
        return v.guid if isinstance(v, Stub) else str(v.id)
    if tag == 'bool':
        return '1' if v else '0'
    if tag == 'float':
        return f'{v:g}'
    if tag in _FLOATS:
        return ' '.join(f'{c:g}' for c in v)
    if tag == 'color':
        return ' '.join(str(c) for c in v)
    if tag == 'binary':
        return v.hex().upper()
    return str(v).replace('\\', '\\\\').replace('"', '\\"')


def to_kv2(doc: Document, max_array: int | None = None) -> str:
    """Readable text dump; element references are printed as GUIDs."""
    out: list[str] = []
    for e in _ordered_elements(doc):
        out.append(f'"{e.type}"\n{{\n\t"id" "elementid" "{e.id}"\n\t"name" "string" "{e.name}"')
        for name, a in e.attrs.items():
            tag = a.base
            if a.is_array:
                items = a.value if max_array is None else a.value[:max_array]
                more = '' if max_array is None or len(a.value) <= max_array else f' ... {len(a.value)} total'
                body = ', '.join(f'"{_kv2_scalar(tag, v)}"' for v in items)
                out.append(f'\t"{name}" "{tag}_array" [ {body}{more} ]')
            else:
                out.append(f'\t"{name}" "{tag}" "{_kv2_scalar(tag, a.value)}"')
        out.append('}\n')
    return '\n'.join(out)


def walk(root: Element, cls: str | Iterable[str] | None = None) -> Iterator[Element]:
    """Depth-first iteration over reachable elements, optionally filtered by class."""
    wanted = {cls} if isinstance(cls, str) else (set(cls) if cls else None)
    seen: set[int] = set()
    stack = [root]
    while stack:
        e = stack.pop()
        if id(e) in seen:
            continue
        seen.add(id(e))
        if wanted is None or e.type in wanted:
            yield e
        stack.extend(reversed(list(e.children())))
