"""Read-only access to the game's VPK archives (asset search and file extraction)."""
from __future__ import annotations

import struct
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SIGNATURE = 0x55AA1234
ARCHIVES = ('csgo', 'csgo_core', 'core', 'csgo_imported', 'csgo_lv')


@dataclass(frozen=True)
class Entry:
    vpk: str            # the *_dir.vpk file
    archive: int        # 0x7fff = data stored in the dir file itself
    offset: int
    length: int
    preload: bytes


class VPK:
    def __init__(self, dir_path: Path):
        self.path = Path(dir_path)
        self.entries: dict[str, Entry] = {}
        with open(self.path, 'rb') as f:
            data = f.read()
        sig, version, tree_size = struct.unpack_from('<III', data, 0)
        if sig != SIGNATURE:
            raise ValueError(f'not a VPK: {dir_path}')
        header = 12 if version == 1 else 28
        self._data_start = header + tree_size
        pos = header

        def cstr():
            nonlocal pos
            end = data.index(b'\0', pos)
            s = data[pos:end].decode('utf-8', 'replace')
            pos = end + 1
            return s

        while True:
            ext = cstr()
            if not ext:
                break
            while True:
                folder = cstr()
                if not folder:
                    break
                while True:
                    name = cstr()
                    if not name:
                        break
                    _crc, preload_len, archive, offset, length, _term = struct.unpack_from('<IHHIIH', data, pos)
                    pos += 18
                    preload = data[pos:pos + preload_len]
                    pos += preload_len
                    full = (f'{folder}/' if folder.strip() else '') + f'{name}.{ext}' if ext.strip() else name
                    self.entries[full.lower()] = Entry(str(self.path), archive, offset, length, preload)

    def read(self, name: str) -> bytes:
        e = self.entries[name.lower()]
        if e.length == 0:
            return e.preload
        if e.archive == 0x7FFF:
            archive = self.path
            offset = self._data_start + e.offset
        else:
            archive = self.path.with_name(self.path.name.replace('_dir.vpk', f'_{e.archive:03d}.vpk'))
            offset = e.offset
        with open(archive, 'rb') as f:
            f.seek(offset)
            return e.preload + f.read(e.length)


_TEXTURE_REF = re.compile(rb'(?:materials|textures)/[a-z0-9_/.\-]+\.vtex')


class GameFiles:
    """All stock VPKs of the installed game."""

    def __init__(self, cs2_path: Path):
        self.cs2_path = Path(cs2_path)
        self._missing: dict[str, list[str]] = {}
        self.vpks: list[tuple[str, VPK]] = []
        for mod in ARCHIVES:
            p = self.cs2_path / 'game' / mod / 'pak01_dir.vpk'
            if p.exists():
                self.vpks.append((mod, VPK(p)))

    def exists(self, name: str) -> bool:
        n = name.lower().replace('\\', '/')
        return any(n in v.entries for _, v in self.vpks)

    def read(self, name: str) -> bytes:
        n = name.lower().replace('\\', '/')
        for _, v in self.vpks:
            if n in v.entries:
                return v.read(n)
        raise FileNotFoundError(name)

    def missing_textures(self, material: str) -> list[str]:
        """Textures a compiled stock material refers to that the game does not have (it renders as a magenta
        checker, e.g. materials/decals/oldleaves01.vmat). Empty for a sound material or one that is not stock."""
        key = material.lower().replace('\\', '/').removesuffix('_c')
        if key not in self._missing:
            try:
                data = self.read(key + '_c')
            except (FileNotFoundError, OSError):
                data = b''
            refs = {m.decode() for m in _TEXTURE_REF.findall(data)}
            self._missing[key] = sorted(r for r in refs if not self.exists(r + '_c'))
        return self._missing[key]

    def search(self, query: str = '', ext: str | None = None, limit: int = 100) -> list[str]:
        """Find compiled stock assets; returns source-style paths (e.g. 'materials/x.vmat')."""
        words = [w for w in query.lower().replace('\\', '/').split() if w]
        suffix = None
        if ext:
            suffix = '.' + ext.lower().lstrip('.')
            if not suffix.endswith('_c'):
                suffix += '_c'
        out: list[str] = []
        seen = set()
        for _, v in self.vpks:
            for name in v.entries:
                if suffix and not name.endswith(suffix):
                    continue
                if all(w in name for w in words):
                    src = name[:-2] if name.endswith('_c') else name
                    if src not in seen:
                        seen.add(src)
                        out.append(src)
        out.sort(key=lambda n: (len(n), n))
        return out[:limit]


@lru_cache(maxsize=2)
def game_files(cs2_path: str) -> GameFiles:
    return GameFiles(Path(cs2_path))
