"""Whole images inside a .txp, to and from PNG (the briefing help pages, brfhelp.txp).

export(txp, index)          -> RGBA PIL image, exactly as the game has it (palette alpha kept)
replace(txp, index, image)  -> txp bytes with that image's pixels and palette replaced

The image keeps its size and format: a 4-bit image (16 colours) or 8-bit image (256 colours)
gets a new palette of that size, chosen from the PNG (alpha included), so the pixel data and
palette fit exactly where the old ones were and nothing else in the file moves.
"""
from __future__ import annotations

import struct

from PIL import Image

import qar


def export(txp: bytes, index: int) -> Image.Image:
    im = qar.images(txp)[index]
    px, pal = qar.decode(txp, im)
    out = Image.new("RGBA", (im["w"], im["h"]))
    out.putdata([pal[i] for i in px])
    return out


def replace(txp: bytes, index: int, image: Image.Image) -> bytes:
    im = qar.images(txp)[index]
    w, h = im["w"], im["h"]
    image = image.convert("RGBA")
    if image.size != (w, h):
        raise ValueError(f"image {index} must be {w}x{h}, the PNG is {image.size[0]}x{image.size[1]}")
    if im["z"]:
        raise ValueError(f"image {index} is stored compressed; not supported here")
    colours = 16 if im["bpp"] == 4 else 256
    # quantize with alpha: fast octree keeps the RGBA palette
    q = image.quantize(colors=colours, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    pal = q.getpalette(rawmode="RGBA")[:4 * colours]
    pal += [0] * (4 * colours - len(pal))
    idx = list(q.getdata())
    if im["bpp"] == 4:
        lin = bytes((idx[i] & 15) | ((idx[i + 1] & 15) << 4) for i in range(0, len(idx), 2))
        wb = w // 2
    else:
        lin = bytes(idx)
        wb = w
    data = qar.swizzle(lin, wb, h)
    out = bytearray(txp)
    old = len(qar.pixels(txp, im))
    assert len(data) == old, (len(data), old)
    out[im["data"]:im["data"] + len(data)] = data
    out[im["clut"]:im["clut"] + 4 * colours] = bytes(pal)
    return bytes(out)
