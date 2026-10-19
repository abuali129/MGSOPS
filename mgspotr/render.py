"""Draw encoded caption bytes the way the game does: keepfont glyphs, a dark
outline, each line centred on the 480-pixel screen."""

from __future__ import annotations

import re

from PIL import Image, ImageFilter

import cap
from keepfont import H

SCREEN_W = 480
LINE_H = 20
#: a dialogue line starting with this is stored in reading order; the patched game (and the
#: preview) draw it from RTL_RIGHT leftwards -- see tools/rtl_patch.py
RTL_MARKER = bytes((0x80, 0xFE))
RTL_RIGHT = 440
#: glyph codes the game's drawer skips (no ink, no width)
SKIPPED = (0x00, 0xFE, 0xFF)


TAG = re.compile(rb"<[^<>\n]*>")


#: width of a two-byte code the preview has no picture for
ICON_W = 18


def codes(line: bytes) -> list[int]:
    """glyph codes of a line, tags dropped: 0x80 xx -> xx; 0xA0/0xC0/0xE0 xx are the same glyph
    in a colour (the game clears bits 0x6000); 0x1F xx -> glyph xx + 0x7F; any other two-byte
    code stays whole (0x8Cnn button icons, 0x81xx/0x82xx Japanese glyphs, 0x83xx marks)"""
    line = TAG.sub(b"", line)
    out, i = [], 0
    while i < len(line):
        b = line[i]
        if b >= 0x80 and i + 1 < len(line):
            code = ((b & 0x9F) << 8) | line[i + 1]
            out.append(code & 0xFF if code >> 8 == 0x80 else code); i += 2
        elif b == 0x1F and i + 1 < len(line):
            out.append((line[i + 1] + 0x7F) & 0xFF); i += 2
        else:
            out.append(b); i += 1
    return out


def glyph_width(kf, c: int, icons=None) -> int:
    if c in SKIPPED:
        return 0
    if c >= 0x100:
        return (icons or {}).get(c, (ICON_W,))[0]
    return kf.glyphs[c][0] if c in kf.glyphs else 0


def line_width(kf, line: bytes, icons=None) -> int:
    return sum(glyph_width(kf, c, icons) for c in codes(line))


def _icon(icon, h: int):
    """(width, yoff, levels) of an icon at the font's height (the small font gets it scaled)"""
    w, lv = icon
    if h == w:
        return w, 0, lv
    nw = max(1, round(w * h / w))
    return nw, 0, [lv[(y * w // h) * w + x * w // nw] for y in range(h) for x in range(nw)]


def render(kf, data: bytes, scale: int = 2, bg=(38, 34, 30), brk: bytes = cap.BR,
           align: str = "center", margin: int = 8, icons=None) -> tuple[Image.Image, list[int]]:
    """(RGB image, pixel width of every line).  align: "center" (captions) or "left"
    (dialogue window; Arabic arrives padded so it reads right-aligned).  icons: the line's
    button icons, {code: (width, levels)} (Project.icons); others show as an empty box"""
    lines = data.split(brk)
    h = getattr(kf, "h", H)                  # 18-row caption font or the 10-row small font
    top = 3 if h == H else 0                 # caption glyphs carry a 3-row y offset
    pics = {c: _icon(i, h) for c, i in (icons or {}).items()}
    widths = [sum(pics[c][0] if c in pics else glyph_width(kf, c) for c in codes(l))
              for l in lines]
    height = 8 + LINE_H * len(lines) + 4
    mask = Image.new("L", (SCREEN_W, height), 0)
    px = mask.load()
    for n, line in enumerate(lines):
        x = (SCREEN_W - widths[n]) // 2 if align == "center" else margin
        rtl = line.startswith(RTL_MARKER)
        pen = 0                                   # right-to-left lines: pixels drawn so far
        y0 = 4 + n * LINE_H
        for c in codes(line):
            if c in SKIPPED:
                continue
            if c in pics:
                w, yo, bits = pics[c]
                yo += top                         # icons sit on the cell's top row
            elif c >= 0x100:                      # no picture: an outline box
                w, yo = ICON_W, top + 2
                bits = [3 if gx in (2, w - 3) or gy in (0, h - 5) else 0
                        for gy in range(h - 4) for gx in range(w)] + [0] * (4 * w)
            elif c in kf.glyphs:
                w, yo, bits = kf.glyphs[c]
            else:
                continue
            if rtl:
                x = margin + RTL_RIGHT - pen - w
                pen += w
            for gy in range(h):
                yy = y0 + yo + gy - top
                if not 0 <= yy < height:
                    continue
                for gx in range(w):
                    v = bits[gy * w + gx]
                    if v and 0 <= x + gx < SCREEN_W:
                        px[x + gx, yy] = max(px[x + gx, yy], v * 85)
            x += w
    outline = mask.filter(ImageFilter.MaxFilter(3))
    img = Image.new("RGB", (SCREEN_W, height), bg)
    img.paste((0, 0, 0), (0, 0), outline)
    img.paste((255, 255, 255), (0, 0), mask)
    if scale != 1:
        img = img.resize((SCREEN_W * scale, height * scale), Image.NEAREST)
    return img, widths
