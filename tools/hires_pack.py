"""PPSSPP hi-res texture pack for the menu font sheets (the grid sheets menu_patch grows).

PPSSPP replaces a texture by name: <cachekey 16 hex><data hash 8 hex>.png in
memstick/PSP/TEXTURES/<game id>/, with textures.ini choosing the hash.  With
    hash = xxh64, ignoreAddress = true
the name is  00000000 | (dims ^ clut hash) | data hash  (GPU/Common/TextureCacheCommon.h
CacheKey, TextureReplacer::ComputeHash):
    dims       log2(tex height) << 8 | log2(tex width)       (power-of-two texture size)
    clut hash  XXH32(the loaded palette bytes, seed 0xC0108888) ^ the GE palette-format word
    data hash  XXH64(texture bytes in RAM, seed 0xBACD7814), low 32 bits; the bytes are
               (bufw * h + (w - bufw)) pixels from the texture address, w = power-of-two width
menu_patch makes every grown sheet a power of two tall, so the hashed bytes are the sheet's
own pixels (+ for 448-wide sheets the 64 columns' worth that follow: the palette, which the
txp stores right after the pixels).  One file serves every stage that carries the same sheet.

    grown_sheets(patched stage entries, original entries) -> {name: RGBA picture at 1x}
    write_pack(folder, sheets, scale)
"""
from __future__ import annotations

import struct

import xxhash
from PIL import Image

import menu_patch
import qar

TEX_SEED, CLUT_SEED = 0xBACD7814, 0xC0108888
CLUT_BYTES = 64                 # 16 colours, 32 bits each
#: the GE palette-format command word the game sets for these sheets (0xC5 command, mask 0xFF,
#: 32-bit colours); PPSSPP folds it into the palette hash -- found from its texture dumps
CLUT_FORMAT = 0xC500FF03
SCALE = 4
INI = """[options]
version = 1
hash = xxh64
ignoreAddress = true
"""


def _pow2(n: int) -> int:
    return 1 << (n - 1).bit_length()


def name_of(txp: bytes, im: dict) -> str:
    """PPSSPP's replacement file name (without .png) for one image of a txp"""
    w, h = im["w"], im["h"]
    tw, th = _pow2(w), _pow2(h)
    data = qar.pixels(txp, im)
    nbytes = (w * h + (tw - w)) * im["bpp"] // 8           # bufw = w (the image width)
    if len(data) < nbytes and not im["z"]:                  # read on into what follows in RAM
        data = txp[im["data"]:im["data"] + nbytes]
    clut = txp[im["clut"]:im["clut"] + CLUT_BYTES]
    dims = (th.bit_length() - 1) << 8 | (tw.bit_length() - 1)
    key = dims ^ xxhash.xxh32_intdigest(clut, CLUT_SEED) ^ CLUT_FORMAT
    return "%08x%08x%08x" % (0, key, xxhash.xxh64_intdigest(data[:nbytes], TEX_SEED) & 0xFFFFFFFF)


def picture(txp: bytes, im: dict) -> Image.Image:
    """the image as RGBA on its power-of-two texture (the part past the image is clear)"""
    px, pal = qar.decode(txp, im)
    w, h = im["w"], im["h"]
    img = Image.new("RGBA", (_pow2(w), _pow2(h)), (0, 0, 0, 0))
    body = Image.new("RGBA", (w, h))
    body.putdata([tuple(pal[i]) for i in px])
    img.paste(body, (0, 0))
    return img


def grown_sheets(entries, original) -> dict[str, tuple[Image.Image, int]]:
    """{name: (1x picture, the image's original height)} for every menu sheet menu_patch grew
    in one stage: entries are the patched stage entries, original the game's"""
    out = {}
    top = {e[0]: e for e in entries}
    old = {e[0]: e for e in original}
    for qname in ("cache.qar", "resident.qar"):
        if qname not in top or qname not in old:
            continue
        qd, od = top[qname][1], old[qname][1]
        before = {n: (o, s) for n, o, s, _t in qar.parse(od)}
        for name, off, size, _t in qar.parse(qd):
            if not (name.startswith("lay_") and name.endswith("_0.txp")) or name not in before:
                continue
            txp = qd[off:off + size]
            o, s = before[name]
            heights = {im["index"]: im["h"] for im in qar.images(od[o:o + s])}
            for im in qar.images(txp):
                if heights.get(im["index"]) not in (None, im["h"]):
                    out.setdefault(name_of(txp, im), (picture(txp, im), heights[im["index"]]))
    return out


#: original image height -> the sheet shape menu_patch keys it by (width, sub height)
SHAPES = {64: (448, 60), 48: (256, 48), 40: (256, 36)}
#: sheet shape -> font editor face (its glyph size and baseline: derive_glyphs.METRICS)
FACE_OF = {(448, 60): "menu_heading", (256, 48): "menu_items", (256, 36): "menu_labels"}


def tt_glyph(ch: str, font: str, size: int, cell: int, baseline: int, bold: bool, width: int):
    """an 'L' coverage image width x cell: the TTF's form drawn at this size, stretched to the
    box the game reserves for it (the boxes come from the hand-made glyphs, so joins line up)"""
    from PIL import ImageDraw, ImageFont
    from build_font import FALLBACK
    f = ImageFont.truetype(font, size)
    draw_ch = FALLBACK.get(ch, ch)
    stroke = max(1, size // 24) if bold else 0
    adv = max(1, round(f.getlength(draw_ch)) + 2 * stroke)
    pad = size
    img = Image.new("L", (adv + 2 * pad, cell), 0)
    ImageDraw.Draw(img).text((pad + stroke, baseline), draw_ch, font=f, fill=255, anchor="ls",
                             stroke_width=stroke, stroke_fill=255)
    return img.crop((pad, 0, pad + adv, cell)).resize((width, cell), Image.LANCZOS)


def redraw(picture: Image.Image, orig_h: int, font: str, scale: int = SCALE) -> Image.Image:
    """the sheet at `scale`: the game's Latin letters with sharp pixels, the Arabic cells drawn
    again from the TTF at full resolution (smooth)"""
    from derive_glyphs import METRICS
    big = picture.resize((picture.width * scale, picture.height * scale), Image.NEAREST)
    shape = SHAPES.get(orig_h)
    if shape is None or shape not in menu_patch.PARAMS:
        return big
    cell, top, base, bold, levels = menu_patch.PARAMS[shape]
    _rows, placed, _new_h = menu_patch.arabic_area(shape[0], orig_h, cell, top, base, levels, bold)
    _cell, size, baseline = METRICS[FACE_OF[shape]]
    white = Image.new("RGBA", (1, 1), (255, 255, 255, 255))
    for ch, gx, gy, gw in placed:
        box = (gx * scale, gy * scale, (gx + gw) * scale, (gy + cell) * scale)
        big.paste((0, 0, 0, 0), box)
        cov = tt_glyph(ch, font, size * scale, cell * scale, baseline * scale, bold, gw * scale)
        big.paste(white.resize(cov.size), box[:2], cov)
    return big


def write_pack(folder, sheets: dict, scale: int = SCALE, font: str | None = None) -> int:
    """textures.ini + one PNG per sheet ({name: (1x picture, original height)}); the Arabic
    cells are redrawn from `font` when given, else everything is scaled with sharp pixels.
    Returns the PNG count."""
    from pathlib import Path
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "textures.ini").write_text(INI, encoding="utf-8")
    for name, (img, orig_h) in sheets.items():
        if font:
            out = redraw(img, orig_h, font, scale)
        else:
            out = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
        out.save(folder / f"{name}.png")
    return len(sheets)
