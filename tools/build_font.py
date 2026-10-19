"""Build the Arabic keepfont.row: ASCII glyphs stay, slots 0x7F..0xFF get Arabic presentation forms
rendered from SF Arabic Rounded.

usage: python build_font.py [out.row] [preview.png]
"""
import os, sys
from PIL import Image, ImageDraw, ImageFont
from keepfont import KeepFont, H
from arabic_map import SLOTS   # {unicode char: slot code}

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FONT = os.path.join(ROOT, "fonts", "SFArabicRounded-Regular.ttf")
ORIG = os.path.join(ROOT, "work", "keepfont.row")

SIZE = 16          # px em size
SS = 4             # supersampling factor
STROKE = 1         # stroke in supersampled px (thickens the thin regular weight a little)
BASE_ROW = 13      # glyph row of the baseline for yoff=3 (matches the Latin glyphs)
LEVELS = (0.16, 0.42, 0.70)   # coverage thresholds for ink levels 1,2,3

# glyphs missing from the font -> draw this char instead
FALLBACK = {"ﻩ": "ه"}


_FONTS = {}


def render(ch, font_path=FONT):
    f = _FONTS.get(font_path)
    if f is None:
        f = _FONTS[font_path] = ImageFont.truetype(font_path, SIZE * SS)
    draw_ch = FALLBACK.get(ch, ch)
    adv = f.getlength(draw_ch) / SS
    w = max(1, round(adv))
    pad = 8 * SS
    img = Image.new("L", (w * SS + 2 * pad, (H + 8) * SS), 0)
    d = ImageDraw.Draw(img)
    # baseline placed at row 16 of a canvas that is 4 rows taller than the glyph box on top
    base_y = (BASE_ROW + 4) * SS
    d.text((pad, base_y), draw_ch, font=f, fill=255, anchor="ls", stroke_width=STROKE, stroke_fill=255)
    small = img.resize((img.width // SS, img.height // SS), Image.LANCZOS)
    # columns of the advance box
    x0 = pad // SS
    box = small.crop((x0, 0, x0 + w, small.height))
    px = list(box.tobytes())
    rows = [px[y * w:(y + 1) * w] for y in range(box.height)]
    # vertical: rows 4.. = glyph rows 0.. for yoff 3; pull glyph up into the top 4 spare rows if inked
    inked = [y for y, r in enumerate(rows) if max(r) > LEVELS[0] * 255]
    top = min(inked) if inked else 4
    shift = max(0, 4 - top)            # how many rows above glyph row 0 are inked
    shift = min(shift, 3)              # yoff can go down to 0
    start = 4 - shift
    rows = rows[start:start + H]
    yoff = 3 - shift
    out = []
    for r in rows:
        for v in r:
            c = v / 255
            out.append(0 if c < LEVELS[0] else 1 if c < LEVELS[1] else 2 if c < LEVELS[2] else 3)
    return w, yoff, out


# keepfont_mini.row: 10-row glyphs (footers, item and skill descriptions).  Latin caps sit on
# rows 1-8 with the baseline under row 8; descenders (g, y) use yoff 1 to drop a row.  Arabic
# goes on box row 8 with yoff 1, so it shares the Latin baseline and keeps 2 rows of descender.
MINI_H = 10
MINI_SIZE = 10
MINI_BASE_ROW = 8
MINI_YOFF = 1


def render_mini(ch, font_path=FONT, size=MINI_SIZE):
    f = ImageFont.truetype(font_path, size * SS)
    draw_ch = FALLBACK.get(ch, ch)
    w = max(1, round(f.getlength(draw_ch) / SS))
    pad = 8 * SS
    img = Image.new("L", (w * SS + 2 * pad, MINI_H * SS), 0)
    ImageDraw.Draw(img).text((pad, MINI_BASE_ROW * SS), draw_ch, font=f, fill=255, anchor="ls")
    small = img.resize((img.width // SS, MINI_H), Image.LANCZOS)
    px = list(small.crop((pad // SS, 0, pad // SS + w, MINI_H)).tobytes())
    out = [0 if v / 255 < LEVELS[0] else 1 if v / 255 < LEVELS[1] else 2 if v / 255 < LEVELS[2] else 3
           for v in px]
    return w, MINI_YOFF, out


def make_mini_font(orig, font_path=FONT, size=MINI_SIZE):
    """original keepfont_mini.row bytes -> (KeepFont with Arabic glyphs, file bytes)"""
    kf = KeepFont(orig, h=MINI_H)
    for ch, slot in SLOTS.items():
        kf.glyphs[slot] = render_mini(ch, font_path, size)
    return kf, kf.build(like=orig)


def make_font(orig, font_path=FONT):
    """original keepfont.row bytes -> (KeepFont with Arabic glyphs, file bytes)"""
    kf = KeepFont(orig)
    for ch, slot in SLOTS.items():
        kf.glyphs[slot] = render(ch, font_path)
    return kf, kf.build(like=orig)


def build(out_path, preview=None):
    kf, data = make_font(open(ORIG, "rb").read())
    open(out_path, "wb").write(data)
    if preview:
        save_preview(kf, preview)
    return kf


def save_preview(kf, path):
    codes = list(range(0x41, 0x5B)) + list(range(0x61, 0x7B)) + list(range(0x7F, 0x100))
    cols, cw, sc = 16, 24, 4
    rows = (len(codes) + cols - 1) // cols
    S = Image.new("RGB", (cols * cw * sc, rows * (H + 4) * sc), (35, 60, 35))
    pal = [(35, 60, 35), (110, 110, 110), (190, 190, 190), (255, 255, 255)]
    for i, c in enumerate(codes):
        w, yo, px = kf.glyphs[c]
        g = Image.new("RGB", (w, H))
        g.putdata([pal[v] for v in px])
        S.paste(g.resize((w * sc, H * sc), Image.NEAREST), ((i % cols) * cw * sc, (i // cols) * (H + 4) * sc + yo * sc))
    S.save(path)


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "work", "keepfont_ar.row"),
          sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "work", "font_preview.png"))
