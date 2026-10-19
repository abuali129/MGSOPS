"""Arabic glyphs for the menu layouts: lay_<name>.la2 (font tables) + lay_<name>_0.txp (sheets).

How the menu fonts work (mapped 2026-10-07, see menu_font.py for the table format):
  - A font table only holds normalized glyph boxes; bytes 2-3 of an entry are the glyph
    height * 16, not a texture index.  A text object names its font and its texture by 1-based
    ids (+0x30 / +0x2C; the game maps them through arrays built at load: fonts by walking the
    tables by their size field, textures from the la2 texture list).  In practice every font
    samples the same kind of "grid sheet" in its layout's own txp:
        448x64 image, sub 448x60  bold caps     9972E9 (headings, 20 px)
        256x48 image, sub 256x48  caps + lower  8D08EA, 9CCB9D (menu items, 16 px)
        256x40 image, sub 256x36  caps          9CCB9A, 69B621 (12 px), the 8 px fonts
    v is normalized over the SUB height, which holds three cell rows (20 / 16 / 12 px).
  - We grow every grid sheet in place with Arabic cell rows under the Latin ones (same cell
    height, glyphs sized from that sheet's own Latin caps), update its image and sub records,
    then rescale v of every font drawing from it and append one entry per Arabic form keyed by
    its game code (\\x80 + slot, or the single byte 0x7F for the question mark).
  - Which sheet a font draws from: the grid sheet whose glyph boxes hold its Latin glyphs, and
    whose cell height is closest to the font's glyph height.  All sheets of one shape get the
    identical Arabic layout, so fonts sharing a shape agree whichever copy they sample.
"""
from __future__ import annotations

import math
import struct
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

import qar
from arabic_map import SLOTS
from build_font import FALLBACK, FONT

#: grid sheet shapes (width, height of the sub-texture the game normalizes v over) -> bold?
#: the images are 448x64, 256x48, 256x40; their subs say 448x60, 256x48, 256x36, so the cells
#: are 20, 16 and 12 rows -- the glyph heights of the fonts drawn from them.
SHEETS = {(448, 60): True, (256, 48): False, (256, 36): False}
#: the TTF the Arabic menu glyphs are rendered from (set_font changes it)
MENU_FONT = FONT
SS = 4                      # supersampling for rendering


# -- reading -----------------------------------------------------------------------------------

def fonts(la2: bytes):
    """[(hash, header pos, entry count, [entries])]; entry = (adv, height, u0, u1, v0, v1, key)

    table header: u32 hash, u16 entry count, u16 table size in bytes (8 + 16 * count); the game
    walks the tables by that size when it builds its font pointer array, so keep it in step."""
    offs = struct.unpack_from("<12I", la2, 4)
    fs = offs[10]
    if not fs:
        return []
    out, p = [], fs + 4
    for _ in range(struct.unpack_from("<I", la2, fs)[0]):
        h, c = struct.unpack_from("<IH", la2, p)
        ents = [struct.unpack_from("<HHHHHH4s", la2, p + 8 + 16 * i) for i in range(c)]
        out.append((h, p, c, ents))
        p += 8 + 16 * c
    return out


def ink_of(px, pal):
    return [1 if pal[i][3] > 0x40 else 0 for i in px]


def box_score(ents, w, h, ink) -> float:
    """share of printable Latin glyph boxes that hold ink and have clear edges in this image"""
    good = tot = 0
    for _adv, _hh, u0, u1, v0, v1, key in ents:
        k = key.rstrip(b"\0")
        if len(k) != 1 or not 0x21 <= k[0] < 0x7F or u1 <= u0:
            continue
        x0, x1 = round(u0 / 65536 * w), round(u1 / 65536 * w)
        y0, y1 = round(v0 / 65536 * h), round(v1 / 65536 * h)
        tot += 1
        if x1 <= x0 or y1 <= y0 or x1 > w or y1 > h:
            continue
        inside = sum(ink[y * w + x] for y in range(y0, y1) for x in range(x0, x1))
        edge = sum(ink[y * w + x] for y in range(y0, y1) for x in (x0 - 1, x1) if 0 <= x < w)
        good += inside > 0 and edge <= (y1 - y0) // 4
    return good / tot if tot else 0.0


def ink_levels(palette):
    """palette indices [clear, light, mid, full] (white ink by alpha)"""
    alpha = {i: p[3] for i, p in enumerate(palette[:16])}
    used = sorted((a, i) for i, a in alpha.items() if a > 0)
    clear = min(alpha, key=lambda i: alpha[i])
    ink = [i for _a, i in used][-3:]
    while len(ink) < 3:
        ink.insert(0, ink[0])
    return (clear, *ink)


def caps_metrics(ink, w, h, ents):
    """(top, baseline) of the capitals inside a cell, measured on the font's 'H'"""
    H = [e for e in ents if e[6].rstrip(b"\0") == b"H"][0]
    x0, x1 = round(H[2] / 65536 * w), round(H[3] / 65536 * w)
    y0, y1 = round(H[4] / 65536 * h), round(H[5] / 65536 * h)
    rows = [y for y in range(y0, y1) if any(ink[y * w + x0:y * w + x1])]
    return rows[0] - y0, rows[-1] + 1 - y0


# -- the Arabic glyph area of one sheet shape ---------------------------------------------------

#: hand-made glyphs (the workbench's font editor): sheet shape (width, sub height) ->
#: {character: (width, level per pixel 0..3, row by row over the cell)}
OVERRIDES: dict[tuple[int, int], dict[str, tuple[int, tuple]]] = {}
#: rendering settings seen per sheet shape: (cell, caps top, baseline, bold, palette levels)
PARAMS: dict[tuple[int, int], tuple] = {}


def set_font(path) -> None:
    """render the Arabic menu glyphs from another TTF from now on"""
    global MENU_FONT
    if str(path) != MENU_FONT:
        MENU_FONT = str(path)
        generated_glyphs.cache_clear()
        arabic_area.cache_clear()


def set_overrides(shape: tuple[int, int], glyphs: dict) -> None:
    """use these hand-made glyphs for every sheet of this shape (replaces earlier ones)"""
    OVERRIDES[shape] = dict(glyphs)
    arabic_area.cache_clear()


def render(ch, size, cell, baseline, bold):
    f = ImageFont.truetype(MENU_FONT, size * SS)
    draw_ch = FALLBACK.get(ch, ch)
    stroke = SS // 2 if bold else 0
    w = max(1, round((f.getlength(draw_ch) + 2 * stroke) / SS))
    img = Image.new("L", ((w + 16) * SS, cell * SS), 0)
    ImageDraw.Draw(img).text((8 * SS + stroke, baseline * SS), draw_ch, font=f, fill=255,
                             anchor="ls", stroke_width=stroke, stroke_fill=255)
    small = img.resize((img.width // SS, cell), Image.LANCZOS).crop((8, 0, 8 + w, cell))
    return w, list(small.tobytes())


def level(cov) -> int:
    """coverage 0..255 -> ink level 0..3"""
    c = cov / 255
    return 0 if c < 0.18 else 1 if c < 0.42 else 2 if c < 0.68 else 3


def quantize(cov, levels):
    return levels[level(cov)]


@lru_cache(maxsize=None)
def generated_glyphs(cell: int, top: int, baseline: int, bold: bool, font: str = ""):
    """the TTF's Arabic forms for one sheet shape: {character: (width, levels 0..3)}.

    Glyph size follows the sheet's own capitals: SF Arabic at 11 px sat right next to 6-px
    caps in the 256x48 sheet, so size = cap height * 11 / 6."""
    # proven pair: 11 px glyphs, baseline 11 in a 16-px cell next to 6-px caps; scale with the
    # caps but never past what the cell holds (descenders need ~5/16 of it)
    size = max(7, min(round((baseline - top) * 11 / 6), round(cell * 11 / 16)))
    baseline = min(baseline, cell - round(size * 5 / 11))
    out = {}
    for ch in SLOTS:
        gw, cov = render(ch, size, cell, baseline, bold)
        out[ch] = (gw, tuple(level(v) for v in cov))
    return out


def face_glyphs(w: int, cell: int, top: int, baseline: int, bold: bool) -> dict:
    """the glyphs a sheet of this shape gets: generated, with the hand-made ones on top"""
    glyphs = dict(generated_glyphs(cell, top, baseline, bold, MENU_FONT))
    glyphs.update(OVERRIDES.get((w, 3 * cell), {}))
    return glyphs


@lru_cache(maxsize=None)
def arabic_area(w: int, h: int, cell: int, top: int, baseline: int, levels: tuple, bold: bool):
    """rows (palette indices) to append under a w x h image in `cell`-row glyph cells,
    glyph placements (rows from the image top), new image height."""
    glyphs = face_glyphs(w, cell, top, baseline, bold)
    placed, x, y = [], 0, 0
    for ch in SLOTS:
        gw, lv = glyphs[ch]
        if x + gw + 1 > w:
            x, y = 0, y + cell
        placed.append((ch, x, y, gw, lv))
        x += gw + 1
    rows_needed = y + cell
    # a power of two: the GPU samples (and PPSSPP hashes, for hi-res replacements) the texture
    # at its power-of-two height, so the image itself then covers every byte that is read
    new_h = 1 << (h + rows_needed - 1).bit_length()
    rows = [[levels[0]] * w for _ in range(new_h - h)]
    for _ch, gx, gy, gw, lv in placed:
        for r in range(cell):
            for c in range(gw):
                v = lv[r * gw + c]
                if v:
                    rows[gy + r][gx + c] = levels[v]
    placed = tuple((ch, gx, h + gy, gw) for ch, gx, gy, gw, _lv in placed)
    return rows, placed, new_h


# -- patching -----------------------------------------------------------------------------------

def sheets(txp: bytes):
    """grid sheet candidates: image dicts + "L" (sub height) and "subs" (sub record offsets)"""
    nsub, subt = struct.unpack_from("<II", txp, 16)
    out = []
    for im in qar.images(txp):
        if im["bpp"] != 4:
            continue
        subs = [subt + 48 * k for k in range(nsub)
                if struct.unpack_from("<I", txp, subt + 48 * k + 0x14)[0] == im["rec"]]
        whs = {struct.unpack_from("<I", txp, s + 0xC)[0] for s in subs}
        if len(whs) != 1:
            continue
        wh = whs.pop()
        sw, sh = wh & 0xFFFF, wh >> 16
        if sw == im["w"] and sh <= im["h"] and (sw, sh) in SHEETS:
            out.append(dict(im, L=sh, subs=subs))
    return out


def patch(la2: bytes, txp: bytes):
    """(new la2, new txp, report) -- unchanged inputs when the layout has no grid sheet/font"""
    ims = sheets(txp)
    table = fonts(la2)
    if not ims or not table:
        return la2, txp, []
    decoded = {}
    for im in ims:
        px, pal = qar.decode(txp, im)
        decoded[im["index"]] = (px, pal, ink_of(px, pal))

    # pair fonts with sheets (v is normalized over the sub height L)
    pairs = {}                                    # font header pos -> sheet
    for fh, pos, _c, ents in table:
        A = [e for e in ents if e[6].rstrip(b"\0") == b"A"]
        B = [e for e in ents if e[6].rstrip(b"\0") == b"B"]
        if not A or not B:
            continue
        # a grid font has its letters in their own cells, a third of the sheet tall; digit-only
        # fonts (status screen counters, item counts) point every letter at one dummy box over
        # the whole image height -- leave those alone or their digits sample the Arabic rows
        if A[0][2:4] == B[0][2:4] or abs((A[0][5] - A[0][4]) - 65536 / 3) > 65536 / 60:
            continue
        height = A[0][1] / 16
        best = None
        for im in ims:
            if box_score(ents, im["w"], im["L"], decoded[im["index"]][2]) < 0.9:
                continue
            d = abs(math.log(height / (im["L"] / 3)))
            if best is None or d < best[0]:
                best = (d, im)
        if best:
            pairs[pos] = best[1]
    if not pairs:
        return la2, txp, []

    # grow every sheet a font draws from (and every other sheet of the same shape, so a font
    # sampling a sibling copy still agrees)
    shapes = {(im["w"], im["L"]) for im in pairs.values()}
    grow = [im for im in ims if (im["w"], im["L"]) in shapes]
    out = bytearray(txp)
    layout = {}                                   # shape -> (placed, new_h, cell)
    for im in grow:
        px, pal, ink = decoded[im["index"]]
        w, h, L = im["w"], im["h"], im["L"]
        cell = L // 3
        ents = next(e for _f, pos, _c, e in table if pairs.get(pos) is not None
                    and (pairs[pos]["w"], pairs[pos]["L"]) == (w, L))
        top, base = caps_metrics(ink, w, L, ents)
        PARAMS[(w, L)] = (cell, top, base, SHEETS[(w, L)], ink_levels(pal))
        rows, placed, new_h = arabic_area(w, h, cell, top, base, ink_levels(pal), SHEETS[(w, L)])
        if (w, L) in layout and layout[(w, L)][:2] != (placed, new_h):
            raise ValueError("sheets of one shape got different Arabic layouts")
        layout[(w, L)] = (placed, new_h, cell)
        full = px + [v for r in rows for v in r]
        lin = bytes((full[i] & 15) | ((full[i + 1] & 15) << 4) for i in range(0, len(full), 2))
        data = qar.stored(qar.swizzle(lin, w // 2, new_h), im["z"])
        out += bytes(-len(out) % 16)
        doff = len(out)
        out += data + bytes(-len(data) % 16)
        coff = len(out)                         # the palette follows its pixels: the game
        out += txp[im["clut"]:im["clut"] + 64]  # sizes an image as clut - data
        log2h = (new_h - 1).bit_length()
        dims = (im["dims"] & 0x0000FFFF) | (new_h << 16) | (log2h << 28)
        struct.pack_into("<4I", out, im["rec"], dims, im["fmt"], doff, coff)
        p2 = 1 << log2h
        for s in im["subs"]:                    # the sub now spans the whole image
            struct.pack_into("<I", out, s + 0xC, w | (new_h << 16))
            if struct.unpack_from("<f", txp, s + 0x20)[0]:     # half-texel style: (h-1)/pow2
                struct.pack_into("<f", out, s + 0x20, 0.5 / p2)
                struct.pack_into("<f", out, s + 0x28, (new_h - 1) / p2)
            else:                                              # plain style: h/pow2
                struct.pack_into("<f", out, s + 0x28, new_h / p2)
    out += bytes(-len(out) % 16)

    # fonts: rescale v, add the Arabic entries
    la = bytearray(la2)
    offs = list(struct.unpack_from("<12I", la, 4))
    report, inserts = [], []
    for fh, pos, cnt, ents in table:
        im = pairs.get(pos)
        if im is None:
            continue
        w, L = im["w"], im["L"]
        placed, new_h, cell = layout[(w, L)]
        A = [e for e in ents if e[6].rstrip(b"\0") == b"A"][0]
        scale = (A[1] / 16) / cell                  # drawn height / cell height
        for i in range(cnt):                        # old v was over L rows, now over new_h
            e = pos + 8 + 16 * i
            v0, v1 = struct.unpack_from("<HH", la, e + 8)
            struct.pack_into("<HH", la, e + 8, min(0xFFFF, round(v0 * L / new_h)),
                             min(0xFFFF, round(v1 * L / new_h)))
        extra = bytearray()
        # keys stay sorted (the original tables are), so walk the forms in game-code order
        for ch, gx, gy, gw in sorted(placed, key=lambda g: SLOTS[g[0]]):
            slot = SLOTS[ch]
            key = bytes((slot,)) if slot < 0x80 else bytes((0x80, slot))
            entry = struct.pack("<HHHHHH", max(16, round(gw * 16 * scale)), A[1],
                                min(0xFFFF, gx * 65536 // w), min(0xFFFF, (gx + gw) * 65536 // w),
                                min(0xFFFF, gy * 65536 // new_h),
                                min(0xFFFF, (gy + cell) * 65536 // new_h)) + key.ljust(4, b"\0")
            hit = [i for i in range(cnt) if ents[i][6].rstrip(b"\0") == key]
            if hit:                                 # the question mark reuses 0x7F
                la[pos + 8 + 16 * hit[0]:pos + 24 + 16 * hit[0]] = entry
            else:
                extra += entry
        inserts.append((pos + 8 + 16 * cnt, bytes(extra), pos))
        report.append("%06X -> image %d (%dx%d sub %d -> %d)" % (fh, im["index"], w, im["h"], L, new_h))
    for at, blob, head in sorted(inserts, reverse=True):
        n = struct.unpack_from("<H", la, head + 4)[0] + len(blob) // 16
        struct.pack_into("<HH", la, head + 4, n, 8 + 16 * n)    # count, table size in bytes
        la[at:at] = blob
        offs = [o + len(blob) if o >= at and o else o for o in offs]
    struct.pack_into("<12I", la, 4, *offs)
    return bytes(la), bytes(out), report


# -- stage containers ----------------------------------------------------------------------------

def patch_stage(entries, only=None):
    """entries = dar_parse of a stage _zar (modified in place).  Returns report lines.
    only: set of la2 names to patch (all when None)"""
    from archive import dar_parse, dar_build
    lines = []
    top = {e[0]: e for e in entries}
    for dname, qname in (("cache.dar", "cache.qar"), ("resident.dar", "resident.qar")):
        if dname not in top or qname not in top:
            continue
        dar = dar_parse(top[dname][1])
        qd = top[qname][1]
        qents = qar.parse(qd)
        qmap = {e[0]: e for e in qents}
        new_txp = {}
        for e in dar:
            if not (e[0].startswith("lay_") and e[0].endswith(".la2")):
                continue
            if only is not None and e[0] not in only:
                continue
            tname = e[0][:-4] + "_0.txp"
            if tname not in qmap:
                continue
            q = qmap[tname]
            la, tx, rep = patch(e[1], qd[q[1]:q[1] + q[2]])
            if rep:
                e[1] = la
                new_txp[tname] = tx
                lines += ["%s/%s: %s" % (dname, e[0], r) for r in rep]
        if new_txp:
            top[dname][1] = dar_build(dar)
            top[qname][1] = qar.build(qents, new_txp, qd)
    return lines


if __name__ == "__main__":
    import os
    import sys
    import zlib
    from archive import dar_parse, dar_build, zar_read
    from safe_write import safe_write
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, "..", "..", "MGSPO", "PSP_GAME", "USRDIR", "stage")
    dst = os.path.join(here, "..", "project", "test_game", "PSP_GAME", "USRDIR", "stage")
    dry = "--dry" in sys.argv
    if "--in-place" in sys.argv:      # patch the test copy's current files (keeps other mods);
        src = dst                     # grown sheets no longer match SHEETS, so reruns are no-ops
    stages = [a for a in sys.argv[1:] if not a.startswith("--")] or sorted(os.listdir(src))
    for st in stages:
        ents = dar_parse(zar_read(os.path.join(src, st, "_zar")))
        lines = patch_stage(ents)
        print(st, len(lines), "fonts")
        for l in lines:
            print("   ", l)
        if lines and not dry:
            raw = dar_build(ents)
            safe_write(os.path.join(dst, st, "_zar"), struct.pack("<I", len(raw)) + zlib.compress(raw, 9))
