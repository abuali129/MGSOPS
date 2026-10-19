"""Make a face's glyphs from another face's hand-made ones, scaled to fit -- kept sharp.

The menu heading face (20-row cells, glyphs drawn at 14 px with the baseline on row 14) is the
largest; the menu items (16 rows), menu labels (12) and small font (10) use the same design
scaled by their own glyph size and lined up on their own baseline -- the numbers the generator
uses for each face (menu_patch.generated_glyphs, build_font.render_mini).

    derive(src_levels, src_width, SRC, DST) -> (width, levels)
"""
from __future__ import annotations

from PIL import Image

from glyph_png import GREYS

#: face -> (cell rows, glyph size in px, baseline row) as the generator draws it
METRICS = {
    "menu_heading": (20, 14, 14),
    "menu_items": (16, 11, 11),
    "menu_labels": (12, 10, 10),      # 10 px like the small font: 8 px blurs the letters
    "small": (10, 10, 8),
}


#: share of a pixel the scaled-down design must cover to become ink
THRESHOLD = 0.4


def derive(width: int, levels, src: str, dst: str, threshold: float = THRESHOLD) -> tuple[int, tuple]:
    """the glyph scaled to the destination face and snapped to full ink or nothing (a sharp
    pixel design stays sharp; smooth resampling would blur it into grey levels)"""
    sh, ssize, sbase = METRICS[src]
    dh, dsize, dbase = METRICS[dst]
    f = dsize / ssize
    img = Image.new("L", (width, sh))
    img.putdata([GREYS[v] for v in levels])
    nw, nh = max(1, round(width * f)), max(1, round(sh * f))
    small = img.resize((nw, nh), Image.BOX)          # area coverage
    canvas = Image.new("L", (nw, dh), 0)
    canvas.paste(small, (0, dbase - round(sbase * f)))
    return nw, tuple(3 if v >= threshold * 255 else 0 for v in canvas.tobytes())


def fit(width: int, levels, src_rows: int, ink_top: int, ink_bottom: int, dst_rows: int,
        threshold: float = THRESHOLD) -> tuple[int, tuple]:
    """the glyph scaled so the source's whole ink band (rows ink_top..ink_bottom of every glyph)
    fills the destination cell -- nothing clipped -- and snapped sharp"""
    f = dst_rows / (ink_bottom - ink_top + 1)
    img = Image.new("L", (width, src_rows))
    img.putdata([GREYS[v] for v in levels])
    nw, nh = max(1, round(width * f)), max(1, round(src_rows * f))
    small = img.resize((nw, nh), Image.BOX)
    canvas = Image.new("L", (nw, dst_rows), 0)
    canvas.paste(small, (0, -round(ink_top * f)))
    out = [3 if v >= threshold * 255 else 0 for v in canvas.tobytes()]
    # dots and other small marks would fall under the threshold: put each one back as a pixel
    for cx, cy in small_marks(width, src_rows, levels):
        x = min(nw - 1, int(cx * f))
        y = int((cy - ink_top) * f)
        if 0 <= y < dst_rows:
            out[y * nw + x] = 3
    return nw, tuple(out)


def from_ttf(font: str, face: str, bold: bool | None = None) -> dict[str, tuple[int, tuple]]:
    """every Arabic form of a menu face drawn from a TTF at the face's size and baseline, at
    the font's own width: {character: (width, levels 0..3 over the face's cell)}.  The
    hi-res pack (hires_pack.redraw) draws the same font at 4x into these widths."""
    from PIL import ImageDraw, ImageFont
    from arabic_map import SLOTS
    from build_font import FALLBACK
    from menu_patch import level
    cell, size, baseline = METRICS[face]
    bold = face == "menu_heading" if bold is None else bold
    ss = 4                                             # supersampling
    f = ImageFont.truetype(font, size * ss)
    stroke = ss // 2 if bold else 0
    out = {}
    for ch in SLOTS:
        draw_ch = FALLBACK.get(ch, ch)
        w = max(1, round((f.getlength(draw_ch) + 2 * stroke) / ss))
        img = Image.new("L", ((w + 16) * ss, cell * ss), 0)
        ImageDraw.Draw(img).text((8 * ss + stroke, baseline * ss), draw_ch, font=f, fill=255,
                                 anchor="ls", stroke_width=stroke, stroke_fill=255)
        small = img.resize((img.width // ss, cell), Image.LANCZOS).crop((8, 0, 8 + w, cell))
        out[ch] = (w, tuple(level(v) for v in small.tobytes()))
    return out


def small_marks(width: int, rows: int, levels, most: int = 4) -> list[tuple[float, float]]:
    """centres of the separate marks of at most `most` pixels (dots, small hamza)"""
    ink = {(x, y) for y in range(rows) for x in range(width) if levels[y * width + x]}
    seen, marks = set(), []
    for start in ink:
        if start in seen:
            continue
        part, todo = [], [start]
        seen.add(start)
        while todo:
            x, y = todo.pop()
            part.append((x, y))
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if n in ink and n not in seen:
                    seen.add(n)
                    todo.append(n)
        if len(part) <= most:
            marks.append((sum(p[0] for p in part) / len(part) + 0.5,
                          sum(p[1] for p in part) / len(part) + 0.5))
    return marks
