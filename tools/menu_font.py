"""Arabic glyphs for the menu fonts (layout .la2 font tables + the layout's .txp image).

A layout (lay_*.la2) has a font section (header u32 #10 = offset, #11 = end):
    u32 font count, then per font:  u32 hash, u16 entry count, u16 table size (8 + 16 * count),
    entries of 16 bytes: u16 advance*16, u8 0, u8 image index (in the layout's own
    lay_*_0.txp), u16 u0, u16 u1, u16 v0, u16 v1 (0..0xFFFF across the image),
    char key[4] (the text renderer matches keys by string prefix, up to 3 bytes).
The glyph image is a 4bpp CLUT image, 16-pixel rows; palette 0 = clear, then three
white ink levels.  We add rows of Arabic glyphs under the existing ones, rescale the
existing v coordinates, and append one entry per Arabic form keyed by its game code.
"""
from __future__ import annotations

import struct

from PIL import Image, ImageDraw, ImageFont

import qar
from arabic_map import SLOTS
from build_font import FALLBACK, FONT

CELL = 16          # glyph cell height in the image
BASELINE = 11      # baseline row inside a cell (Latin caps sit on rows 5-10)
SIZE = 11          # px em size for the Arabic glyphs
SS = 4


def ink_levels(palette):
    """palette indices ordered by alpha: [clear, light, mid, full]"""
    alpha = {i: p[3] for i, p in enumerate(palette[:16])}
    used = sorted((a, i) for i, a in alpha.items() if a > 0 and palette[i][:3] == (255, 255, 255))
    clear = min(alpha, key=lambda i: alpha[i])
    ink = [i for _a, i in used][-3:]
    while len(ink) < 3:
        ink.insert(0, ink[0])
    return [clear] + ink


def render_glyph(ch, font_path=FONT, size=SIZE):
    f = ImageFont.truetype(font_path, size * SS)
    draw_ch = FALLBACK.get(ch, ch)
    w = max(1, round(f.getlength(draw_ch) / SS))
    img = Image.new("L", (w * SS + 16 * SS, CELL * SS), 0)
    ImageDraw.Draw(img).text((8 * SS, BASELINE * SS), draw_ch, font=f, fill=255, anchor="ls")
    small = img.resize((img.width // SS, CELL), Image.LANCZOS)
    small = small.crop((8, 0, 8 + w, CELL))
    return w, list(small.tobytes())


def quantize(cov, levels):
    c = cov / 255
    return levels[0] if c < 0.18 else levels[1] if c < 0.42 else levels[2] if c < 0.68 else levels[3]


def add_arabic(txp: bytes, la2: bytes, image_index: int, font_path=FONT, size=SIZE, fonts=None):
    """Return (new txp, new la2, glyph count).  Fonts in la2 using image_index get the glyphs
    (only the font hashes in `fonts` when given: some fonts, like the 2x title heading,
    break when their v coordinates are rescaled)."""
    ims = qar.images(txp)
    im = ims[image_index]
    assert im["bpp"] == 4, "only 4bpp font images are handled"
    px, pal = qar.decode(txp, im)
    w, h = im["w"], im["h"]
    levels = ink_levels(pal)

    # lay the glyphs out on new 16-px rows under the existing ones
    chars = list(SLOTS)                       # 126 forms in 0x80..0xFD + ؟ at 0x7F
    glyphs = [(ch,) + render_glyph(ch, font_path, size) for ch in chars]
    placed, x, y = [], 0, h + (-h % CELL)
    for ch, gw, cov in glyphs:
        if x + gw + 1 > w:
            x, y = 0, y + CELL
        placed.append((ch, x, y, gw, cov))
        x += gw + 1
    new_h = y + CELL
    new_h += -new_h % 8
    assert new_h <= 256, "font image would exceed 256 rows"
    rows = [px[r * w:(r + 1) * w] for r in range(h)]
    rows += [[levels[0]] * w for _ in range(new_h - h)]
    for ch, gx, gy, gw, cov in placed:
        for r in range(CELL):
            for c in range(gw):
                v = cov[r * gw + c]
                if v:
                    rows[gy + r][gx + c] = quantize(v, levels)
    lin = bytearray()
    for r in rows:
        for c in range(0, w, 2):
            lin.append((r[c] & 15) | ((r[c + 1] & 15) << 4))
    data = qar.swizzle(bytes(lin), w // 2, new_h)

    # txp: new pixel data + palette appended, image + sub-texture records updated
    out = bytearray(txp)
    out += bytes(-len(out) % 16)
    doff = len(out)
    out += data + bytes(-len(data) % 16)
    coff = len(out)
    out += txp[im["clut"]:im["clut"] + 64]
    log2h = (new_h - 1).bit_length()
    dims = (im["dims"] & 0x0000FFFF) | (new_h << 16) | (log2h << 28)
    struct.pack_into("<4I", out, im["rec"], dims, im["fmt"], doff, coff)
    nsub, subt = struct.unpack_from("<II", txp, 16)
    for k in range(nsub):
        s = subt + 48 * k
        if struct.unpack_from("<I", txp, s + 0x14)[0] == im["rec"]:
            wh = struct.unpack_from("<I", txp, s + 0xC)[0]
            struct.pack_into("<I", out, s + 0xC, (wh & 0xFFFF) | (new_h << 16))
            p2 = 1 << log2h
            struct.pack_into("<f", out, s + 0x20, 0.5 / p2)
            struct.pack_into("<f", out, s + 0x28, (new_h - 1) / p2)

    # la2: rescale v of fonts on this image, append Arabic entries
    la = bytearray(la2)
    offs = list(struct.unpack_from("<12I", la, 4))
    fs = offs[10]
    count = struct.unpack_from("<I", la, fs)[0]
    p = fs + 4
    inserts = []                                   # (position, bytes, table header pos)
    for _ in range(count):
        n = struct.unpack_from("<H", la, p + 4)[0]
        ents = p + 8
        uses = any(la[ents + 16 * i + 3] == image_index for i in range(n))
        if fonts is not None and struct.unpack_from("<I", la, p)[0] not in fonts:
            uses = False
        if uses:
            for i in range(n):
                e = ents + 16 * i
                if la[e + 3] != image_index:
                    continue
                v0, v1 = struct.unpack_from("<HH", la, e + 8)
                struct.pack_into("<HH", la, e + 8,
                                 min(0xFFFF, round(v0 * h / new_h)), min(0xFFFF, round(v1 * h / new_h)))
            extra = bytearray()
            for ch, gx, gy, gw, _cov in placed:
                slot = SLOTS[ch]
                key = bytes((slot,)) if slot < 0x80 else bytes((0x80, slot))
                entry = struct.pack("<HBBHHHH", gw * 16, 0, image_index,
                                    min(0xFFFF, gx * 256), min(0xFFFF, (gx + gw) * 256),
                                    min(0xFFFF, round(gy * 65536 / new_h)),
                                    min(0xFFFF, round((gy + CELL) * 65536 / new_h))) + key.ljust(4, b"\0")
                if slot < 0x80:                    # ؟ replaces the existing 0x7F entry
                    for i in range(n):
                        if la[ents + 16 * i + 12:ents + 16 * i + 16].rstrip(b"\0") == key:
                            la[ents + 16 * i:ents + 16 * i + 16] = entry
                            break
                    continue
                extra += entry
            inserts.append((ents + 16 * n, bytes(extra), p))
        p = ents + 16 * n
    for pos, blob, head in sorted(inserts, reverse=True):
        n = struct.unpack_from("<H", la, head + 4)[0]
        struct.pack_into("<H", la, head + 4, n + len(blob) // 16)
        la[pos:pos] = blob
        for k, o in enumerate(offs):
            if o >= pos and o != 0:
                offs[k] = o + len(blob)
        struct.pack_into("<12I", la, 4, *offs)
    return bytes(out), bytes(la), len(placed)


# -- second approach: Arabic glyphs in their own image (existing glyphs untouched) ------------

ARABIC_SUB_HASH = 0xA2AB1C          # name hash for the added sub-texture (any unused value)


def glyph_sheet(image_width, levels, font_path=FONT, size=SIZE):
    """render every Arabic form; returns (rows of palette indices, placements, height)"""
    chars = list(SLOTS)
    glyphs = [(ch,) + render_glyph(ch, font_path, size) for ch in chars]
    placed, x, y = [], 0, 0
    for ch, gw, cov in glyphs:
        if x + gw + 1 > image_width:
            x, y = 0, y + CELL
        placed.append((ch, x, y, gw, cov))
        x += gw + 1
    h = y + CELL
    h += -h % 8
    rows = [[levels[0]] * image_width for _ in range(h)]
    for ch, gx, gy, gw, cov in placed:
        for r in range(CELL):
            for c in range(gw):
                v = cov[r * gw + c]
                if v:
                    rows[gy + r][gx + c] = quantize(v, levels)
    return rows, placed, h


def add_arabic_image(txp: bytes, la2: bytes, base_index: int, font_path=FONT, size=SIZE,
                     width: int = 256):
    """Append an Arabic glyph image to the txp (palette copied from image base_index) and
    add entries for it to every font in la2 that draws from base_index."""
    n, imgt = struct.unpack_from("<II", txp, 8)
    nsub, subt = struct.unpack_from("<II", txp, 16)
    assert imgt == 0x20 and subt == 0x20 + 16 * n, "unexpected txp layout"
    ims = qar.images(txp)
    base = ims[base_index]
    _px, pal = qar.decode(txp, base)
    levels = ink_levels(pal)
    rows, placed, h = glyph_sheet(width, levels, font_path, size)
    lin = bytearray()
    for r in rows:
        for c in range(0, width, 2):
            lin.append((r[c] & 15) | ((r[c + 1] & 15) << 4))
    data = qar.swizzle(bytes(lin), width // 2, h)

    head_old = subt + 48 * nsub
    delta = 16 + 48                                     # one image record + one sub record
    out = bytearray(txp[:0x20])
    struct.pack_into("<II", out, 8, n + 1, 0x20)
    struct.pack_into("<II", out, 16, nsub + 1, 0x20 + 16 * (n + 1))
    for k in range(n):                                  # image records, data moved by delta
        dims, fmt, doff, coff = struct.unpack_from("<4I", txp, imgt + 16 * k)
        out += struct.pack("<4I", dims, fmt, doff + delta, coff + delta)
    new_rec = len(out)
    out += bytes(16)                                     # filled in below
    for k in range(nsub):                               # sub records; image pointers shift by 16
        rec = bytearray(txp[subt + 48 * k:subt + 48 * k + 48])
        ptr = struct.unpack_from("<I", rec, 0x14)[0]
        struct.pack_into("<I", rec, 0x14, ptr)          # image table stays at 0x20
        out += rec
    sub = bytearray(txp[subt + 48 * base_index:subt + 48 * base_index + 48])         if base_index < nsub else bytearray(48)
    log2w = (width - 1).bit_length()
    log2h = (h - 1).bit_length()
    struct.pack_into("<IIII", sub, 0, 0x10, ARABIC_SUB_HASH, 0, width | (h << 16))
    struct.pack_into("<I", sub, 0x14, new_rec)
    struct.pack_into("<4f", sub, 0x1C, 0.5 / (1 << log2w), 0.5 / (1 << log2h),
                     (width - 1) / (1 << log2w), (h - 1) / (1 << log2h))
    out += sub
    assert len(out) == head_old + delta
    out += txp[head_old:]
    out += bytes(-len(out) % 16)
    doff = len(out)
    out += data + bytes(-len(data) % 16)
    coff = len(out)
    out += txp[base["clut"]:base["clut"] + 64]
    dims = (width & 0xFF) | (((width >> 8) & 0xF) << 8) | (log2w << 12) | (h << 16) | (log2h << 28)
    struct.pack_into("<4I", out, new_rec, dims, 4, doff, coff)
    new_index = n

    la = bytearray(la2)
    offs = list(struct.unpack_from("<12I", la, 4))
    # register the new sub-texture in the layout's texture list (header field #6) so it loads
    tl = offs[6]
    ntl = struct.unpack_from("<I", la, tl)[0]
    txp_hash = struct.unpack_from("<I", la, tl + 8)[0]
    pos = tl + 4 + 8 * ntl
    la[pos:pos] = struct.pack("<II", ARABIC_SUB_HASH, txp_hash)
    struct.pack_into("<I", la, tl, ntl + 1)
    offs = [o + 8 if o >= pos and o else o for o in offs]
    struct.pack_into("<12I", la, 4, *offs)
    fs = offs[10]
    count = struct.unpack_from("<I", la, fs)[0]
    p = fs + 4
    inserts = []
    for _ in range(count):
        cnt = struct.unpack_from("<H", la, p + 4)[0]
        ents = p + 8
        texs = [la[ents + 16 * i + 3] for i in range(cnt)]
        if base_index in texs:
            # scale the Arabic advance like this font scales its Latin glyphs
            e = ents + 16 * 33                          # 'A'
            adv, _z, _t, u0, u1 = struct.unpack_from("<HBBHH", la, e)
            gw_latin = (u1 - u0) / 256 * (base["w"] / 256)
            scale = (adv / 16) / max(1, round(gw_latin + 0.5)) if gw_latin else 1
            scale = 2 if scale > 1.5 else 1
            extra = bytearray()
            for ch, gx, gy, gw, _cov in placed:
                slot = SLOTS[ch]
                key = bytes((slot,)) if slot < 0x80 else bytes((0x80, slot))
                entry = struct.pack("<HBBHHHH", gw * 16 * scale, 0, new_index,
                                    min(0xFFFF, gx * 65536 // width),
                                    min(0xFFFF, (gx + gw) * 65536 // width),
                                    min(0xFFFF, gy * 65536 // h),
                                    min(0xFFFF, (gy + CELL) * 65536 // h)) + key.ljust(4, b"\0")
                if slot < 0x80:
                    for i in range(cnt):
                        if la[ents + 16 * i + 12:ents + 16 * i + 16].rstrip(b"\0") == key:
                            la[ents + 16 * i:ents + 16 * i + 16] = entry
                    continue
                extra += entry
            inserts.append((ents + 16 * cnt, bytes(extra), p))
        p = ents + 16 * cnt
    for pos, blob, head in sorted(inserts, reverse=True):
        cnt = struct.unpack_from("<H", la, head + 4)[0]
        struct.pack_into("<H", la, head + 4, cnt + len(blob) // 16)
        la[pos:pos] = blob
        offs = [o + len(blob) if o >= pos and o else o for o in offs]
        struct.pack_into("<12I", la, 4, *offs)
    return bytes(out), bytes(la), len(placed), new_index
