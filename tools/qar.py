"""cache.qar / resident.qar -- texture archives, and the .txp textures inside them.

qar:  entries back to back (each padded to 128 bytes), then a table at the offset in the
      last u32:  u32 count, count x (u32 hash | type << 24, u32 size), NUL-separated names.
txp:  u32 0, u32 0, u32 image count, u32 image table (0x20)
      u32 sub count, u32 sub table, ...
      image: u32 dims (w = (b1 & 0xF) << 8 | b0, h = (b3 & 0xF) << 8 | b2, high nibbles = log2 sizes), u32 format
             (4 = 4bpp CLUT, 5 = 8bpp CLUT; | 0x20000 = pixels stored as u32 size + zlib),
             u32 data offset, u32 CLUT offset (absolute)
      pixels are PSP-swizzled: 16-byte x 8-row blocks.
"""
from __future__ import annotations

import struct
import zlib

ZLIB = 0x20000


def parse(data: bytes) -> list[list]:
    """[[name, offset, size, typehash], ...]"""
    tab = struct.unpack_from("<I", data, len(data) - 4)[0]
    n = struct.unpack_from("<I", data, tab)[0]
    recs = [struct.unpack_from("<II", data, tab + 4 + 8 * i) for i in range(n)]
    names = data[tab + 4 + 8 * n:len(data) - 4].split(b"\0")
    out, off = [], 0
    for i, (th, size) in enumerate(recs):
        out.append([names[i].decode("latin1"), off, size, th])
        off += size + (-size % 128)
    return out


def build(entries: list[list], blobs: dict[str, bytes], original: bytes) -> bytes:
    """rebuild with some entries replaced; blobs: name -> new bytes"""
    tab = struct.unpack_from("<I", original, len(original) - 4)[0]
    n = struct.unpack_from("<I", original, tab)[0]
    names_part = original[tab + 4 + 8 * n:len(original) - 4]
    body = bytearray()
    recs = []
    for name, off, size, th in entries:
        blob = blobs.get(name, original[off:off + size])
        recs.append((th, len(blob)))
        body += blob + bytes(-len(blob) % 128)
    out = body + struct.pack("<I", n) + b"".join(struct.pack("<II", *r) for r in recs)
    out += names_part
    return bytes(out + struct.pack("<I", len(body)))


def unswizzle(data: bytes, width_bytes: int, height: int) -> bytearray:
    out = bytearray(width_bytes * height)
    i = 0
    for by in range(0, height, 8):
        for bx in range(0, width_bytes, 16):
            for y in range(8):
                for x in range(16):
                    if by + y < height and i < len(data):
                        out[(by + y) * width_bytes + bx + x] = data[i]
                    i += 1
    return out


def swizzle(lin: bytes, width_bytes: int, height: int) -> bytes:
    hp = height + (-height % 8)
    out = bytearray(width_bytes * hp)
    i = 0
    for by in range(0, hp, 8):
        for bx in range(0, width_bytes, 16):
            for y in range(8):
                for x in range(16):
                    if by + y < height:
                        out[i] = lin[(by + y) * width_bytes + bx + x]
                    i += 1
    return bytes(out[:width_bytes * height])


def images(txp: bytes) -> list[dict]:
    n, t = struct.unpack_from("<II", txp, 8)
    out = []
    for k in range(n):
        dims, fmt, doff, coff = struct.unpack_from("<4I", txp, t + 16 * k)
        w = ((dims >> 8) & 0xF) << 8 | (dims & 0xFF)
        h = ((dims >> 24) & 0xF) << 8 | (dims >> 16) & 0xFF
        bpp = 4 if fmt & 0xFFFF == 4 else 8
        out.append({"index": k, "dims": dims, "w": w, "h": h, "fmt": fmt, "bpp": bpp,
                    "data": doff, "clut": coff, "rec": t + 16 * k, "z": bool(fmt & ZLIB)})
    return out


def pixels(txp: bytes, img: dict) -> bytes:
    """the image's swizzled pixel bytes (inflated when stored compressed)"""
    size = img["w"] * img["bpp"] // 8 * img["h"]
    if img["z"]:
        return zlib.decompressobj().decompress(txp[img["data"] + 4:])[:size]
    return txp[img["data"]:img["data"] + size]


def stored(swizzled: bytes, compress: bool) -> bytes:
    """pixel bytes as written in a txp (u32 size + zlib when compress)"""
    if not compress:
        return swizzled
    return struct.pack("<I", len(swizzled)) + zlib.compress(swizzled, 9)


def decode(txp: bytes, img: dict) -> tuple[list[int], list[tuple]]:
    """(pixel indices row-major, palette RGBA list)"""
    wb = img["w"] * img["bpp"] // 8
    raw = pixels(txp, img)
    lin = unswizzle(raw, wb, img["h"])
    px = []
    for b in lin:
        px += [b & 15, b >> 4] if img["bpp"] == 4 else [b]
    ncol = 16 if img["bpp"] == 4 else 256
    pal = txp[img["clut"]:img["clut"] + 4 * ncol]
    return px[:img["w"] * img["h"]], [tuple(pal[i * 4:i * 4 + 4]) for i in range(len(pal) // 4)]
