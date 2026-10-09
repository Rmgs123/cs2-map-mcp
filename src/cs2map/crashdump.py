"""Read what is needed from a CS2 minidump (.mdmp): the exception, the module it happened in, the console tail.

Minidump layout (Microsoft MINIDUMP_*): a header, a directory of streams. We use the exception stream (type 6:
thread id, code, address), the module list (type 4) and — for the console text — the raw bytes of the dump, in which
CS2's recent log lines appear as readable strings. No dbghelp, no symbols: it is a quick triage, not a debugger.
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

EXCEPTION_NAMES = {
    0xC0000005: 'ACCESS_VIOLATION', 0xC00000FD: 'STACK_OVERFLOW', 0xC0000094: 'INT_DIVIDE_BY_ZERO',
    0xC000001D: 'ILLEGAL_INSTRUCTION', 0xC0000409: 'STACK_BUFFER_OVERRUN (fast fail)', 0x80000003: 'BREAKPOINT (assert / fatal error)',
    0xC0000374: 'HEAP_CORRUPTION', 0xE06D7363: 'C++ exception', 0xC0000017: 'NO_MEMORY', 0x40000015: 'FATAL_APP_EXIT',
}
STREAM_MODULES, STREAM_EXCEPTION = 4, 6


def _utf16(data: bytes, rva: int) -> str:
    n = struct.unpack_from('<I', data, rva)[0]
    return data[rva + 4:rva + 4 + n].decode('utf-16-le', 'replace')


def parse(path) -> dict:
    """{exception:{code, name, address, thread}, module:{name, offset}, modules:int} from a minidump file."""
    data = Path(path).read_bytes()
    if data[:4] != b'MDMP':
        raise ValueError('not a minidump file')
    count, directory = struct.unpack_from('<II', data, 8)
    streams = {}
    for i in range(count):
        kind, size, rva = struct.unpack_from('<III', data, directory + i * 12)
        streams.setdefault(kind, (size, rva))
    out = {'file': Path(path).name, 'exception': None, 'module': None, 'modules': 0}
    modules = []
    if STREAM_MODULES in streams:
        rva = streams[STREAM_MODULES][1]
        n = struct.unpack_from('<I', data, rva)[0]
        for i in range(n):
            base, size, _, _, name_rva = struct.unpack_from('<QIIII', data, rva + 4 + i * 108)
            modules.append((base, size, _utf16(data, name_rva)))
        out['modules'] = n
    if STREAM_EXCEPTION in streams:
        rva = streams[STREAM_EXCEPTION][1]
        thread = struct.unpack_from('<I', data, rva)[0]
        code, flags, chain, address, nparams = struct.unpack_from('<IIQQI', data, rva + 8)
        out['exception'] = {'code': f'0x{code:08X}', 'name': EXCEPTION_NAMES.get(code, 'unknown'), 'address': f'0x{address:X}',
                            'thread': thread}
        for base, size, name in modules:
            if base <= address < base + size:
                out['module'] = {'name': Path(name.replace('\\', '/')).name, 'offset': f'0x{address - base:X}'}
                break
    return out


# Readable runs of the dump that look like console messages of our scripts or fatal errors. Everything else the
# dump holds (help texts of console commands, class names, Steam/Vulkan chatter) is noise.
_RUN = re.compile(rb'[ -~]{20,400}')
_SIGNAL = re.compile(rb'(FATAL ERROR|Error loading|Uncaught|Exception|Script error|assert|Assert|[A-Z][A-Z0-9]*_[A-Z0-9_]+ [a-z0-9_]+ [a-z_]+ \{|D_CONTROL|X8_|S_STORY|M_[A-Z]+ )')
_NOISE = re.compile(rb'(Cause the engine|Show entity|hltv memory|jpeg_screenshot|C_Network|CNetwork|client!|Annotation|FrameSync|'
                    rb'userlocal|Generator|SFUI_|AppID|cuda|mat_assert|\.mdmp|prototype)')


_STAMP = re.compile(r'^\s*(\d+)\((\d+\.\d+)\):\s*')


def console_tail(path, lines: int = 40) -> list[str]:
    """The last ~`lines` console-like messages found in the dump, oldest first.

    Lines the engine stamped "<frame>(<seconds>):" are sorted by that time; unstamped fatal errors come last."""
    data = Path(path).read_bytes()
    seen, stamped, other = set(), [], []
    for m in _RUN.finditer(data):
        raw = m.group(0)
        if _NOISE.search(raw) or not _SIGNAL.search(raw):
            continue
        text = raw.decode('ascii', 'replace').strip()
        if text in seen:
            continue
        seen.add(text)
        hit = _STAMP.match(text)
        if hit:
            stamped.append((float(hit.group(2)), text[hit.end():]))
        else:
            other.append(text)
    stamped.sort(key=lambda x: x[0])
    tail = [f'{t:.1f}s {x}' for t, x in stamped]
    return (tail[-max(lines - len(other[-6:]), 1):] + other[-6:])[-lines:]
