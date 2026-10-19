"""Arabic text -> game bytes for the keepfont renderer.

Pipeline per visual line: logical text -> arabic_reshaper (presentation forms, lam-alef ligatures)
-> python-bidi (visual order, left-to-right) -> bytes (ASCII as-is, Arabic forms as b"\\x80"+slot).
Lines are wrapped in logical order by pixel width before reordering.
"""
import arabic_reshaper
from bidi.algorithm import get_display
from arabic_map import SLOTS

LINE_BREAK = b"\x80|"

_reshaper = arabic_reshaper.ArabicReshaper(configuration={
    "delete_harakat": True,
    "support_ligatures": True,
    "RIAL SIGN": False,
})

# characters we normalise before shaping
_NORMALISE = {
    "ـ": "",        # tatweel
    "‏": "", "‎": "",
    "…": "...", "“": '"', "”": '"', "‘": "'", "’": "'",
    "–": "-", "—": "-", " ": " ",
    "ی": "ي", "ک": "ك",      # Persian yeh / keheh
    "؛": ";",                # Arabic semicolon (no glyph slot left)
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
}


def normalise(s):
    return "".join(_NORMALISE.get(c, c) for c in s)


def visual(line):
    """logical Arabic line -> visual-order string of presentation forms"""
    return get_display(_reshaper.reshape(normalise(line)))


# -- protected tokens: <TAG> and {XXXX} (raw two-byte codes such as button icons) ------------
import re as _re

TOKEN = _re.compile(r"\{[0-9A-Fa-f]{4}\}|<[^<>\n]*>")
_PUA = 0xE000
_tokens: dict[str, bytes] = {}          # placeholder char -> game bytes
_token_px: dict[str, int] = {}
ICON_PX = 16


def protect(text):
    """replace tokens with private-use placeholders (bidi class L, untouched by shaping)"""
    def sub(m):
        t = m.group(0)
        raw = bytes.fromhex(t[1:-1]) if t.startswith("{") else t.encode("ascii")
        ch = chr(_PUA + (list(_tokens.values()).index(raw) if raw in _tokens.values()
                         else len(_tokens)))
        _tokens[ch] = raw
        _token_px[ch] = ICON_PX if t.startswith("{") else 0
        return ch
    return TOKEN.sub(sub, text)


def to_bytes(vis):
    out = bytearray()
    for ch in vis:
        o = ord(ch)
        if ch in _tokens:
            out += _tokens[ch]
        elif ch in SLOTS:
            slot = SLOTS[ch]
            out += bytes((slot,)) if slot < 0x80 else bytes((0x80, slot))
        elif 0x20 <= o < 0x7F and ch != "|":
            out.append(o)
        else:
            raise ValueError(f"unsupported character {ch!r} U+{o:04X}")
    return bytes(out)


def codes_of(vis):
    return [SLOTS[ch] if ch in SLOTS else ord(ch) for ch in vis if ch not in _tokens]


def vis_width(vis, widths):
    return sum(widths.get(c, 6) for c in codes_of(vis)) + sum(_token_px.get(ch, 0) for ch in vis)


def pixel_width(line, widths):
    """widths: {code: advance px}"""
    return vis_width(visual(line), widths)


def wrap(text, widths, max_px):
    """greedy word wrap in logical order; explicit '\\n' forces a break"""
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            cand = word if not cur else cur + " " + word
            if cur and pixel_width(cand, widths) > max_px:
                lines.append(cur)
                cur = word
            else:
                cur = cand
        lines.append(cur)
    return lines


def encode(text, widths=None, max_px=None, align_right_to=None, brk=LINE_BREAK):
    """Return game bytes. Lines joined with the caption line-break code.
    align_right_to: pad shorter lines on the left with spaces so the block reads right-aligned."""
    text = protect(text)
    lines = wrap(text, widths, max_px) if (widths and max_px) else text.split("\n")
    vis = [visual(l) for l in lines]
    if align_right_to is not None and widths:
        space = widths.get(0x20, 5)
        w = [vis_width(v, widths) for v in vis]
        target = max(w) if align_right_to == "block" else align_right_to
        vis = [" " * max(0, round((target - wi) / space)) + v for v, wi in zip(vis, w)]
    return brk.join(to_bytes(v) for v in vis)


def encode_sysfont(text, brk=b"\n"):
    """Bytes for texts drawn with the PSP system font (UTF-8 strings: footers, descriptions).
    Arabic forms go out as the 2-byte UTF-8 of their game slot (U+0080..U+00FD, U+007F for
    the question mark); the remapped ltn0.pgf (tools/pgf_remap.py) has the Arabic glyphs there."""
    out = []
    for line in text.split("\n"):
        out.append(b"".join(chr(SLOTS[ch]).encode("utf-8") if ch in SLOTS else ch.encode("utf-8")
                            for ch in visual(line)))
    return brk.join(out)
