"""Minimal reader for compiled Source 2 resources (*_c): block table, external
references (RERL) and texture headers.  Enough to find a material's colour
texture and its size."""
from __future__ import annotations

import struct


def blocks(data: bytes) -> dict[str, tuple[int, int]]:
    """{block type: (absolute offset, size)}"""
    _size, _hv, _ver, block_offset, count = struct.unpack_from('<IHHII', data, 0)
    pos = 8 + block_offset
    out = {}
    for _ in range(count):
        kind = data[pos:pos + 4].decode('ascii', 'replace')
        rel, size = struct.unpack_from('<II', data, pos + 4)
        out[kind] = (pos + 4 + rel, size)
        pos += 12
    return out


def _cstr(data: bytes, pos: int) -> str:
    end = data.index(b'\0', pos)
    return data[pos:end].decode('utf-8', 'replace')


def external_references(data: bytes) -> list[str]:
    b = blocks(data).get('RERL')
    if not b:
        return []
    start, _ = b
    rel, count = struct.unpack_from('<II', data, start)
    pos = start + rel
    names = []
    for _ in range(count):
        str_rel, = struct.unpack_from('<q', data, pos + 8)
        names.append(_cstr(data, pos + 8 + str_rel))
        pos += 16
    return names


def texture_size(data: bytes) -> tuple[int, int]:
    start, _ = blocks(data)['DATA']
    version, = struct.unpack_from('<H', data, start)
    if version != 1:
        raise ValueError(f'unknown texture version {version}')
    width, height = struct.unpack_from('<HH', data, start + 4 + 16)
    return width, height


def color_texture(refs: list[str]) -> str | None:
    textures = [r for r in refs if r.endswith('.vtex')]
    for key in ('_color', 'color', 'albedo', 'basecolor', 'diffuse'):
        for t in textures:
            if key in t.rsplit('/', 1)[-1].lower():
                return t
    return textures[0] if textures else None
