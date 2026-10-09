"""Read and export compiled Source 2 textures from mounted VPK files.

The VTEX DATA block is deliberately parsed here instead of treating the payload as
an opaque blob.  Mip level zero is the largest image; the binary stores mips in
smallest-first order.  The decoder has no external dependencies beyond Pillow and
NumPy and fails closed for formats that are not implemented locally.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from . import resource


FORMAT_NAMES = {
    0: "UNKNOWN", 1: "DXT1", 2: "DXT5", 3: "I8", 4: "RGBA8888", 5: "R16",
    6: "RG1616", 7: "RGBA16161616", 8: "R16F", 9: "RG1616F", 10: "RGBA16161616F",
    11: "R32F", 12: "RG3232F", 13: "RGB323232F", 14: "RGBA32323232F",
    15: "JPEG_RGBA8888", 16: "PNG_RGBA8888", 17: "JPEG_DXT5", 18: "PNG_DXT5",
    19: "BC6H", 20: "BC7", 21: "ATI2N", 22: "IA88", 23: "ETC2",
    24: "ETC2_EAC", 25: "R11_EAC", 26: "RG11_EAC", 27: "ATI1N", 28: "BGRA8888",
    29: "WEBP_RGBA8888", 30: "WEBP_DXT5",
}
BLOCK_FORMATS = {1: (8, "BC1"), 2: (16, "BC3"), 20: (16, "BC7"), 27: (8, "BC4"), 21: (16, "BC5")}


@dataclass(frozen=True)
class VTexInfo:
    width: int
    height: int
    depth: int
    format: int
    format_name: str
    mip_levels: int
    data_offset: int
    mip_sizes: tuple[int, ...]
    compressed_mips: tuple[int, ...] | None = None


class VTexError(ValueError):
    pass


def _lz4_block(data: bytes, expected: int) -> bytes:
    """Small dependency-free LZ4 block decoder used by VTEX compressed mips."""
    src = memoryview(data); ip = 0; out = bytearray()
    while ip < len(src):
        token = int(src[ip]); ip += 1
        literal = token >> 4
        if literal == 15:
            while ip < len(src):
                n = int(src[ip]); ip += 1; literal += n
                if n != 255: break
        if ip + literal > len(src): raise VTexError('truncated LZ4 literals')
        out.extend(src[ip:ip + literal]); ip += literal
        if ip >= len(src): break
        if ip + 2 > len(src): raise VTexError('truncated LZ4 match offset')
        offset = int(src[ip]) | (int(src[ip + 1]) << 8); ip += 2
        if offset <= 0 or offset > len(out): raise VTexError('invalid LZ4 match offset')
        match = token & 15
        if match == 15:
            while ip < len(src):
                n = int(src[ip]); ip += 1; match += n
                if n != 255: break
        match += 4
        for _ in range(match):
            out.append(out[-offset])
    if len(out) != expected:
        raise VTexError(f'LZ4 decoded {len(out)} bytes, expected {expected}')
    return bytes(out)


def _mip_dim(value: int, mip: int) -> int:
    return max(1, int(value) >> int(mip))


def _size(width: int, height: int, depth: int, fmt: int) -> int:
    if fmt in BLOCK_FORMATS:
        block, _ = BLOCK_FORMATS[fmt]
        return max(1, (max(4, (width + 3) // 4 * 4) * max(4, (height + 3) // 4 * 4)) // 16) * block * max(1, depth)
    bpp = {3: 1, 4: 4, 5: 2, 6: 4, 7: 8, 8: 2, 9: 4, 10: 8, 11: 4,
           12: 8, 13: 12, 14: 16, 22: 2, 28: 4}.get(fmt)
    if bpp is None:
        raise VTexError(f"unsupported VTEX format {fmt} ({FORMAT_NAMES.get(fmt, 'unknown')})")
    return width * height * max(1, depth) * bpp


def parse(data: bytes) -> VTexInfo:
    """Parse a compiled ``.vtex_c`` resource and return its image layout."""
    try:
        start, block_size = resource.blocks(data).get("DATA", (None, None))
    except (struct.error, IndexError) as exc:
        raise VTexError("truncated resource block table") from exc
    if start is None or block_size < 40:
        raise VTexError("VTEX DATA block is missing or truncated")
    if start + block_size > len(data):
        raise VTexError("VTEX DATA block exceeds resource size")
    x = data[start:start + block_size]
    version, flags = struct.unpack_from("<HH", x, 0)
    if version != 1:
        raise VTexError(f"unsupported VTEX version {version}")
    _reflect = struct.unpack_from("<4f", x, 4)
    width, height, depth = struct.unpack_from("<HHH", x, 20)
    fmt, mips = struct.unpack_from("<BB", x, 26)
    if not width or not height or not mips:
        raise VTexError("invalid VTEX dimensions or mip count")
    if depth > 1 or flags & 0x10:
        raise VTexError("only 2D VTEX images are supported (volume/cubemap refused)")

    # Extra records are relative to the position immediately after the offset
    # field, as documented by ValveResourceFormat.  Record type 4 carries
    # optional per-mip compressed sizes.
    extra_offset, extra_count = struct.unpack_from("<II", x, 32)
    compressed: tuple[int, ...] | None = None
    if extra_count and 32 + extra_offset + 12 * extra_count <= len(x):
        pos = 32 + extra_offset
        for _ in range(extra_count):
            if pos + 12 > len(x):
                break
            kind, rel, size = struct.unpack_from("<III", x, pos)
            payload = pos + 12 + rel - 8
            if kind == 4 and payload >= 0 and payload + size <= len(x) and size >= 12:
                flag, list_rel, count = struct.unpack_from("<III", x, payload)
                values_pos = payload + 12 + list_rel - 8
                # The extra record exists for both compressed and plain mips;
                # its first word is the authoritative LZ4 flag.
                if flag == 1 and count <= mips and values_pos >= 0 and values_pos + count * 4 <= len(x):
                    compressed = tuple(struct.unpack_from(f"<{count}i", x, values_pos))
            pos += 12
    sizes = tuple(_size(_mip_dim(width, m), _mip_dim(height, m), depth, fmt) for m in range(mips))
    return VTexInfo(width, height, depth, fmt, FORMAT_NAMES.get(fmt, f"FORMAT_{fmt}"), mips,
                    start + block_size, sizes, compressed)


def _rgb565(value: int) -> tuple[int, int, int]:
    return ((value >> 11 & 31) * 255 // 31, (value >> 5 & 63) * 255 // 63, (value & 31) * 255 // 31)


def _decode_bc1(data: bytes, width: int, height: int, alpha: bool = False) -> np.ndarray:
    out = np.zeros((height, width, 4), dtype=np.uint8)
    stride = max(1, (width + 3) // 4)
    for by in range((height + 3) // 4):
        for bx in range((width + 3) // 4):
            p = (by * stride + bx) * 8
            if p + 8 > len(data):
                raise VTexError("truncated BC1 block")
            c0, c1, bits = struct.unpack_from("<HHI", data, p)
            a, b = _rgb565(c0), _rgb565(c1)
            colors = [a + (255,), b + (255,)]
            if c0 > c1 or not alpha:
                colors += [tuple((2 * a[i] + b[i]) // 3 for i in range(3)) + (255,),
                           tuple((a[i] + 2 * b[i]) // 3 for i in range(3)) + (255,)]
            else:
                colors += [tuple((a[i] + b[i]) // 2 for i in range(3)) + (255,), (0, 0, 0, 0)]
            for iy in range(4):
                for ix in range(4):
                    x, y = bx * 4 + ix, by * 4 + iy
                    if x < width and y < height:
                        out[y, x] = colors[(bits >> (2 * (iy * 4 + ix))) & 3]
    return out


def _decode_bc4(data: bytes, width: int, height: int) -> np.ndarray:
    out = np.zeros((height, width), dtype=np.uint8)
    stride = max(1, (width + 3) // 4)
    for by in range((height + 3) // 4):
        for bx in range((width + 3) // 4):
            p = (by * stride + bx) * 8
            if p + 8 > len(data):
                raise VTexError("truncated BC4 block")
            e0, e1 = data[p], data[p + 1]
            vals = [e0, e1]
            if e0 > e1:
                vals += [(6 * e0 + e1) // 7, (5 * e0 + 2 * e1) // 7, (4 * e0 + 3 * e1) // 7,
                         (3 * e0 + 4 * e1) // 7, (2 * e0 + 5 * e1) // 7, (e0 + 6 * e1) // 7]
            else:
                vals += [(4 * e0 + e1) // 5, (3 * e0 + 2 * e1) // 5, (2 * e0 + 3 * e1) // 5,
                         (e0 + 4 * e1) // 5, 0, 255]
            bits = int.from_bytes(data[p + 2:p + 8], "little")
            for iy in range(4):
                for ix in range(4):
                    x, y = bx * 4 + ix, by * 4 + iy
                    if x < width and y < height:
                        out[y, x] = vals[(bits >> (3 * (iy * 4 + ix))) & 7]
    return out


def _decode_bc3(data: bytes, width: int, height: int) -> np.ndarray:
    """Decode BC3 (DXT5), whose alpha and colour blocks are interleaved."""
    out = np.zeros((height, width, 4), dtype=np.uint8)
    stride = max(1, (width + 3) // 4)
    for by in range((height + 3) // 4):
        for bx in range((width + 3) // 4):
            p = (by * stride + bx) * 16
            if p + 16 > len(data):
                raise VTexError("truncated BC3 block")
            alpha = _decode_bc4(data[p:p + 8], 4, 4)
            rgb = _decode_bc1(data[p + 8:p + 16], 4, 4, False)
            x0, y0 = bx * 4, by * 4
            for iy in range(min(4, height - y0)):
                for ix in range(min(4, width - x0)):
                    out[y0 + iy, x0 + ix] = (*rgb[iy, ix, :3], alpha[iy, ix])
    return out


def _decode_bc5(data: bytes, width: int, height: int) -> np.ndarray:
    """Decode ATI2/BC5 as two unsigned red/green channels."""
    out = np.zeros((height, width, 4), dtype=np.uint8)
    stride = max(1, (width + 3) // 4)
    for by in range((height + 3) // 4):
        for bx in range((width + 3) // 4):
            p = (by * stride + bx) * 16
            r = _decode_bc4(data[p:p + 8], 4, 4)
            g = _decode_bc4(data[p + 8:p + 16], 4, 4)
            x0, y0 = bx * 4, by * 4
            for iy in range(min(4, height - y0)):
                for ix in range(min(4, width - x0)):
                    out[y0 + iy, x0 + ix] = (r[iy, ix], g[iy, ix], 255, 255)
    return out


def decode(raw: bytes, info: VTexInfo, mip: int = 0) -> Image.Image:
    """Decode one mip level to an RGBA Pillow image."""
    if not isinstance(mip, int) or not 0 <= mip < info.mip_levels:
        raise VTexError(f"mip must be 0..{info.mip_levels - 1}")
    width, height = _mip_dim(info.width, mip), _mip_dim(info.height, mip)
    # Mips are stored smallest first.  Compressed mip sizes, when present, are
    # also indexed by mip level and can be LZ4 compressed.
    offset = info.data_offset
    for m in range(info.mip_levels - 1, mip, -1):
        offset += (info.compressed_mips[m] if info.compressed_mips else info.mip_sizes[m])
    stored = info.compressed_mips[mip] if info.compressed_mips else info.mip_sizes[mip]
    payload = raw[offset:offset + stored]
    expected = info.mip_sizes[mip]
    if len(payload) != stored:
        raise VTexError("VTEX mip payload is truncated")
    if info.compressed_mips and stored < expected:
        try:
            payload = _lz4_block(payload, expected)
        except VTexError:
            raise
    if len(payload) < expected:
        raise VTexError(f"VTEX mip payload too small ({len(payload)} < {expected})")
    fmt = info.format
    if fmt == 1:
        arr = _decode_bc1(payload, width, height, False)
    elif fmt == 2:
        arr = _decode_bc3(payload, width, height)
    elif fmt == 27:
        c = _decode_bc4(payload, width, height); arr = np.stack([c, c, c, np.full_like(c, 255)], -1)
    elif fmt == 21:
        arr = _decode_bc5(payload, width, height)
    elif fmt == 20:
        # Pillow's bundled bcn decoder supports every BC7 mode.  No optional
        # native bridge, subprocess or downloaded dependency is necessary.
        try:
            return Image.frombytes("RGBA", (width, height), payload, "bcn", (7, "BC7"))
        except (OSError, ValueError) as exc:
            raise VTexError(f"BC7 decode failed: {exc}") from exc
    elif fmt in (4, 28):
        arr = np.frombuffer(payload[:width * height * 4], dtype=np.uint8).reshape(height, width, 4).copy()
        if fmt == 28:
            arr = arr[..., [2, 1, 0, 3]]
    elif fmt == 22:
        x = np.frombuffer(payload[:width * height * 2], dtype=np.uint8).reshape(height, width, 2)
        arr = np.stack([x[..., 0], x[..., 0], x[..., 0], x[..., 1]], -1)
    else:
        raise VTexError(f"VTEX format {info.format_name} requires the optional native BC decoder")
    return Image.fromarray(arr, "RGBA")


def material_refs(material_data: bytes) -> list[str]:
    return resource.external_references(material_data)


def texture_for_material(game_files, material: str) -> dict[str, str]:
    name = material.replace("\\", "/").removesuffix("_c")
    if not name.endswith(".vmat"):
        name += ".vmat"
    data = game_files.read(name + "_c")
    refs = [ref for ref in material_refs(data) if ref.endswith('.vtex')]
    slots: dict[str, str] = {}
    color = resource.color_texture(refs)
    if color:
        slots['color'] = color
    for ref in refs:
        if ref == color:
            continue
        base = ref.rsplit("/", 1)[-1].lower()
        key = "normal" if "normal" in base else "roughness" if "rough" in base else "metalness" if "metal" in base else "ao" if "ao" in base else "color"
        if key != 'color':
            slots.setdefault(key, ref)
    if "color" not in slots and refs:
        slots["color"] = resource.color_texture(refs) or refs[0]
    return slots


def export(game_files, material_or_vtex: str, out_dir: str | Path, mip: int = 0) -> dict[str, Any]:
    """Export a stock material or texture to PNG files under ``out_dir``."""
    target = material_or_vtex.replace("\\", "/").removesuffix("_c")
    if not isinstance(mip, int) or mip < 0:
        raise VTexError('mip must be a nonnegative integer')
    is_material = '.vmat' in target.lower()
    slots = texture_for_material(game_files, target) if is_material else {"color": target.removesuffix(".vtex")}
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    made: dict[str, str] = {}
    metadata: dict[str, Any] = {"source": material_or_vtex, "mip": int(mip), "maps": {}}
    errors: dict[str, str] = {}
    for key, tex in slots.items():
        if not tex.endswith(".vtex"):
            tex += ".vtex"
        try:
            data = game_files.read(tex + "_c")
            info = parse(data)
        except (KeyError, FileNotFoundError, VTexError) as exc:
            errors[key] = str(exc)
            metadata["maps"][key] = {"texture": tex, "exported": False, "error": str(exc)}
            continue
        try:
            selected = min(mip, info.mip_levels - 1) if is_material else mip
            image = decode(data, info, selected)
        except VTexError as exc:
            errors[key] = f"{info.format_name}: {exc}"
            metadata["maps"][key] = {"texture": tex, "format": info.format_name,
                                     "size": [_mip_dim(info.width, mip), _mip_dim(info.height, mip)],
                                     "exported": False}
            continue
        path = out / (re.sub(r"[^a-zA-Z0-9_.-]+", "_", tex.rsplit("/", 1)[-1].removesuffix(".vtex")) + f"_m{selected}.png")
        image.save(path)
        made[key] = str(path)
        metadata["maps"][key] = {"texture": tex, "format": info.format_name, "mip": selected, "size": [image.width, image.height], "exported": True}
    return {"ok": 'color' in made, "complete": bool(made) and not errors,
            "maps": made, "metadata": metadata, "errors": errors}

