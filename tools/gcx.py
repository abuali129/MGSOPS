"""scenerio.gcx -- per-stage script with its in-game dialogue (and codec.dat blocks).

Layout (offsets in bytes):
  0x00  u32 magic 0x4531E19C, u32 0, u32 ?
  0x0C  u32 proc[n]            (type << 24 | code offset), ends at 0xFFFFFFFF
        -- data section, base = after the 0xFFFFFFFF --
  +0    u32 section size       (bytes from base to the end of the extra block)
  +4    u32 table offset       (0x14)
  +8    u32 pool offset
  +12   u32 extra offset       (block after the pool: embedded glyphs etc.)
  +16   u32 0, u32 0 ...       (up to the table)
  table u32 item[(pool-table)/4]   0x80000000 | offset = text string, else binary blob
  pool  items; text strings are NUL terminated, blobs run to the next item
  extra ... up to base+size
        -- code section (bytecode), unchanged; it refers to items by index --

Rebuilding only re-lays the pool: items keep their order, text items get new
bytes, the extra block moves by the size change, size/extra offsets are updated.
"""

from __future__ import annotations

import re
import struct

MAGIC = 0x4531E19C
TEXT = 0x80000000
#: button icons in the extra block: square, this many pixels; code of the first one
ICON, ICON_FIRST = 18, 0x8C01


class Gcx:
    def __init__(self, data: bytes):
        if struct.unpack_from("<I", data, 0)[0] != MAGIC:
            raise ValueError("not a GCX file")
        self.data = data
        end = data.index(b"\xff\xff\xff\xff", 4)      # text tables (minifont.fcx) have no procs
        while end % 4:                                # table entries are aligned
            end = data.index(b"\xff\xff\xff\xff", end + 1)
        self.base = base = end + 4
        self.size, self.toff, self.poff, self.eoff = struct.unpack_from("<4I", data, base)
        n = (self.poff - self.toff) // 4
        self.table = list(struct.unpack_from(f"<{n}I", data, base + self.toff))
        pool = base + self.poff
        extra = base + self.eoff
        # item bytes, in table order; shared offsets keep sharing
        starts = sorted({t & 0x7FFFFFFF for t in self.table})
        bounds = dict(zip(starts, starts[1:] + [self.eoff - self.poff]))
        self.items: list[bytes] = []
        for t in self.table:
            off = t & 0x7FFFFFFF
            blob = data[pool + off:pool + bounds[off]]
            if t & TEXT:
                blob = blob[:blob.index(b"\0")] if b"\0" in blob else blob
            self.items.append(blob)
        self._tail_of = {}                            # padding after each text item
        for t in self.table:
            if t & TEXT:
                off = t & 0x7FFFFFFF
                raw = data[pool + off:pool + bounds[off]]
                self._tail_of[off] = raw[raw.index(b"\0"):] if b"\0" in raw else b""
        self.extra = data[extra:base + self.size]
        self.code = data[base + self.size:]

    def icons(self) -> dict[int, tuple[int, list[int]]]:
        """the button icons this script's text draws: {code: (width, 2-bit levels row by row)}.
        The extra block is u32 size, then ICON x ICON icons, 2 bits a pixel, MSB first;
        {8C01} is the first.  Each stage script and codec block carries its own set."""
        x = self.extra
        if len(x) < 4:
            return {}
        size = struct.unpack_from("<I", x, 0)[0]
        step = ICON * ICON // 4
        out = {}
        for i in range(min(size, len(x) - 4) // step):
            d = x[4 + step * i:4 + step * (i + 1)]
            out[ICON_FIRST + i] = (ICON, [(d[k // 4] >> (2 * (3 - k % 4))) & 3
                                          for k in range(ICON * ICON)])
        return out

    def texts(self):
        """(index, bytes) of every text item"""
        return [(i, b) for i, (t, b) in enumerate(zip(self.table, self.items)) if t & TEXT]

    def build(self) -> bytes:
        pool = bytearray()
        placed: dict[int, int] = {}                   # old offset -> new offset
        table = []
        for t, blob in zip(self.table, self.items):
            old = t & 0x7FFFFFFF
            if old not in placed:
                placed[old] = len(pool)
                if t & TEXT:
                    tail = self._tail_of.get(old, b"\0")
                    pool += blob + (tail if tail else b"\0")
                else:
                    pool += blob
            table.append((t & TEXT) | placed[old])
        head = bytearray(self.data[self.base:self.base + self.toff])
        poff, eoff = self.poff, self.poff + len(pool)
        pad = (-eoff) % 4                            # keep the extra block aligned
        pool += bytes(pad)
        eoff += pad
        size = eoff + len(self.extra)
        struct.pack_into("<4I", head, 0, size, self.toff, poff, eoff)
        out = bytearray(self.data[:self.base]) + head
        out += b"".join(struct.pack("<I", t) for t in table)
        out += bytes(poff - (self.toff + 4 * len(table)))
        out += pool + self.extra + self.code
        return bytes(out)


# -- splitting a dialogue string into what the translator edits ----------------

TAG = re.compile(rb"<[^<>\n]*>")
#: codes the text renderer reads as two bytes (button icons etc.), kept as {XXXX}
WIDE = re.compile(rb"[\x81-\xff][\x00-\xff]|\x1f[\x00-\xff]")


def split(raw: bytes) -> tuple[bytes, bytes, bytes] | None:
    """(prefix, body, suffix) or None when the string has no words in it.

    prefix = the leading run of tags and newlines (window control, speaker, <S=1>...)
    body   = what is said; tags inside it stay inline
    suffix = the trailing newline(s)
    """
    pos = 0
    while True:
        m = TAG.match(raw, pos)
        if m:
            pos = m.end()
        elif raw[pos:pos + 1] == b"\n":
            pos += 1
        else:
            break
    body = raw[pos:]
    stripped = body.rstrip(b"\n")
    suffix = body[len(stripped):]
    if not TAG.sub(b"", stripped).strip():
        return None
    return raw[:pos], stripped, suffix


def to_text(body: bytes) -> str:
    """game bytes -> editable text: wide codes as {XXXX}, 0x80 xx as the cp1252 char"""
    out, i = [], 0
    while i < len(body):
        b = body[i]
        if b == 0x80 and i + 1 < len(body):
            out.append(bytes([body[i + 1]]).decode("cp1252", "replace")); i += 2
        elif (b >= 0x81 or b == 0x1F) and i + 1 < len(body):
            out.append("{%02X%02X}" % (b, body[i + 1])); i += 2
        else:
            out.append(bytes([b]).decode("cp1252", "replace")); i += 1
    return "".join(out)


TOKEN = re.compile(r"\{[0-9A-Fa-f]{4}\}|<[^<>\n]*>")
