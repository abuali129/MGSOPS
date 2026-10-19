"""Slot assignment: Arabic presentation forms -> keepfont codes.

Text bytes: code < 0x80 is written as one byte, codes 0x80..0xFF as b"\x80" + byte.
Codes 0x80FE/0x80FF are swallowed by the renderer (seen in-game), so they stay unused.
"""
_chars = [chr(c) for c in range(0xFE80, 0xFEFD)]          # 125 letter forms incl. lam-alef ligatures
_chars += ["،"]                                         # Arabic comma
assert len(_chars) == 126

SLOTS = {ch: 0x80 + i for i, ch in enumerate(_chars)}       # 0x80..0xFD
SLOTS["؟"] = 0x7F                                       # Arabic question mark (single byte 0x7F)
