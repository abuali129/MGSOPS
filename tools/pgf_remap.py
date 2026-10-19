"""Point U+007F..U+00FD of a PGF font (built by ttf2pgf2/ttf2pgf_ext.py) at its Arabic glyphs.

The game draws its mixed-case texts (footers, item and skill descriptions) as UTF-8 with the
PSP system font, ltn0.pgf.  Code points >= U+0100 are sent to the Japanese font, and the
r_main01 string loader rewrites some multi-byte codes, so U+FExx presentation forms do not
survive.  Instead we give U+0080..U+00FD the Arabic forms of the game's own 127-slot map
(arabic_map.SLOTS, the same codes the menu sheets and the caption font use), and U+007F the
question mark.  Strings then carry each form as 2-byte UTF-8 (C2/C3 xx).

Only the charmap changes: each code point maps to a glyph id, so the remapped entries reuse
the glyphs already rendered for U+FExx (or the alias carriers for the punctuation).

  python pgf_remap.py <in.pgf> <out.pgf>

PGF layout as written by ttf2pgf_ext: 0x188-byte header, constant tables up to 0x1760,
shadow map (u32 @0x16C entries x u32 @0x170 bits), charmap (u32 @0x10 entries x u32 @0x18
bits, first code point u16 @0xB6), char pointers.  Tables are bit-packed LSB first.
"""
from __future__ import annotations

import struct
import sys

from arabic_map import SLOTS

#: punctuation has no Form-B glyph; the font builder parks it on these carriers
CARRIER = {"،": 0xFE75, "؛": 0xFEFD, "؟": 0xFEFE}


def unpack(data, off, n, bpe):
    vals, bit = [], off * 8
    for _ in range(n):
        v = 0
        for k in range(bpe):
            if data[(bit + k) >> 3] >> ((bit + k) & 7) & 1:
                v |= 1 << k
        vals.append(v)
        bit += bpe
    return vals


def pack(vals, bpe):
    out = bytearray(((len(vals) * bpe + 31) // 32) * 4)
    bit = 0
    for v in vals:
        for k in range(bpe):
            if v >> k & 1:
                out[(bit + k) >> 3] |= 1 << ((bit + k) & 7)
        bit += bpe
    return bytes(out)


def charmap(pgf: bytes):
    cmap_len, n_chars, cbpe = struct.unpack_from("<3I", pgf, 0x10)
    sh_len, sh_bpe = struct.unpack_from("<2I", pgf, 0x16C)
    cmin = struct.unpack_from("<H", pgf, 0xB6)[0]
    off = 0x1760 + ((sh_len * sh_bpe + 31) // 32) * 4
    return off, cmin, n_chars, cbpe, unpack(pgf, off, cmap_len, cbpe)


def remap(pgf: bytes) -> tuple[bytes, list[str]]:
    off, cmin, n_chars, cbpe, ids = charmap(pgf)
    gid = lambda cp: ids[cp - cmin] if 0 <= cp - cmin < len(ids) and ids[cp - cmin] < n_chars else None
    assert gid(ord("A")) is not None, "charmap offset looks wrong"
    missing = []
    for ch, slot in SLOTS.items():
        g = gid(CARRIER.get(ch, ord(ch)))
        if g is None:
            g = gid(ord(ch))
        if g is None:
            missing.append("%04X" % ord(ch))
            continue
        ids[slot - cmin] = g
    table = pack(ids, cbpe)
    out = pgf[:off] + table + pgf[off + len(table):]
    assert len(out) == len(pgf)
    return out, missing


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    data = open(src, "rb").read()
    new, missing = remap(data)
    open(dst, "wb").write(new)
    print("remapped %d slots, missing %s -> %s" % (len(SLOTS) - len(missing), missing or "none", dst))
