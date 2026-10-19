"""Hand-made glyphs: one PNG per glyph, 4 grey levels (the fonts' 2-bit ink).

A glyph is (width, levels): `levels` holds one value 0..3 per pixel, row by row, over the face's
full height (18 rows for the caption font, 10 for the small font, the cell height for a menu
sheet).  PNGs are greyscale 0 / 85 / 170 / 255 on black; any grey is rounded to the nearest
level when a PNG is read back, so a drawing program's anti-aliasing is fine.

Overrides live in <project>/fonts/<face>/<code>.png (code = the game's glyph code in hex, e.g.
"AE.png"); the caption and small fonts also keep each glyph's vertical offset in yoff.json.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

GREYS = (0, 85, 170, 255)


def to_image(width: int, height: int, levels) -> Image.Image:
    img = Image.new("L", (width, height), 0)
    img.putdata([GREYS[v] for v in levels])
    return img


def from_image(img: Image.Image, height: int) -> tuple[int, tuple]:
    """(width, levels) of a glyph PNG; it must be `height` rows tall"""
    img = img.convert("L")
    if img.height != height:
        raise ValueError(f"the glyph must be {height} pixels tall (this one is {img.height})")
    if not 1 <= img.width <= 64:
        raise ValueError(f"the glyph must be 1 to 64 pixels wide (this one is {img.width})")
    levels = tuple(min(3, (v + 42) // 85) for v in img.getdata())
    return img.width, levels


def load(folder: Path, height: int) -> dict[int, tuple[int, int | None, tuple]]:
    """{code: (width, yoff or None, levels)} of the overrides in a face folder"""
    folder = Path(folder)
    if not folder.is_dir():
        return {}
    yoffs = {}
    meta = folder / "yoff.json"
    if meta.exists():
        yoffs = {int(k, 16): v for k, v in json.loads(meta.read_text(encoding="utf-8")).items()}
    out = {}
    for png in sorted(folder.glob("*.png")):
        try:
            code = int(png.stem, 16)
        except ValueError:
            continue
        with Image.open(png) as img:
            width, levels = from_image(img, height)
        out[code] = (width, yoffs.get(code), levels)
    return out


def save(folder: Path, code: int, width: int, height: int, levels, yoff: int | None = None) -> None:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    to_image(width, height, levels).save(folder / f"{code:02X}.png")
    if yoff is not None:
        meta = folder / "yoff.json"
        data = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
        data[f"{code:02X}"] = yoff
        meta.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")


def remove(folder: Path, code: int) -> None:
    folder = Path(folder)
    (folder / f"{code:02X}.png").unlink(missing_ok=True)
    meta = folder / "yoff.json"
    if meta.exists():
        data = json.loads(meta.read_text(encoding="utf-8"))
        if data.pop(f"{code:02X}", None) is not None:
            meta.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
