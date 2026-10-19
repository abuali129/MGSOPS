"""keepfont.row - the 2bpp bitmap font used for cutscene captions (and other story text).

File layout (lives in stage/init/_zar -> cache.dar -> keepfont.row):
  u16 bitmap_offset (0x388), u8 0, u8 12, u32 bitmap_size
  u32 glyph[224]                for codes 0x20..0xFF (index = code - 0x20)
        bits  0-19  byte offset of the glyph inside the bitmap area
        bits 20-25  width - 1
        bits 26-31  y offset (3 for every original glyph)
  bitmap: per glyph width*18 pixels, 2 bits each, MSB first, continuous across rows
          (0 = transparent, 1..3 = increasing ink). Height (18) comes from the runtime object.

Text codes: byte < 0x80 -> glyph[byte]; "\\x80" + byte -> glyph[byte] (so 0x80..0xFF are reachable).
"""
import struct

H = 18
FIRST = 0x20
COUNT = 224


class KeepFont:
    """h = glyph rows: 18 for keepfont.row, 10 for keepfont_mini.row (small UI text: footers,
    item descriptions); both files share this layout."""
    def __init__(self, data, h=H):
        self.h = h
        bmoff, _, hb, bmsize = struct.unpack_from("<HBBI", data, 0)
        self.hdr_byte3 = hb
        tab = [struct.unpack_from("<I", data, 8 + 4 * i)[0] for i in range(COUNT)]
        bm = data[bmoff:bmoff + bmsize]
        self.glyphs = {}
        for i, e in enumerate(tab):
            off, w, yo = e & 0xFFFFF, ((e >> 20) & 63) + 1, e >> 26
            px = [(bm[off + k // 4] >> (2 * (3 - k % 4))) & 3 if off + k // 4 < len(bm) else 0
                  for k in range(w * h)]
            self.glyphs[FIRST + i] = (w, yo, px)

    def build(self, like=None):
        """like: original file bytes -> keep its bitmap size (pad) and the trailing data after it"""
        bm = bytearray()
        tab = []
        for code in range(FIRST, FIRST + COUNT):
            w, yo, px = self.glyphs[code]
            assert 1 <= w <= 64 and len(px) == w * self.h
            off = len(bm)
            tab.append(off | ((w - 1) << 20) | (yo << 26))
            packed = bytearray((len(px) + 3) // 4)
            for k, v in enumerate(px):
                packed[k // 4] |= (v & 3) << (2 * (3 - k % 4))
            bm += packed
        bmoff = 8 + 4 * COUNT
        trailer = b""
        if like is not None:
            obm = struct.unpack_from("<I", like, 4)[0]
            if len(bm) > obm:
                raise ValueError(f"glyph bitmap {len(bm)} bytes > original {obm}")
            bm += bytes(obm - len(bm))
            trailer = like[bmoff + obm:]
        out = struct.pack("<HBBI", bmoff, 0, self.hdr_byte3, len(bm))
        out += b"".join(struct.pack("<I", t) for t in tab) + bm + trailer
        return out

    def width(self, code):
        return self.glyphs[code][0]
