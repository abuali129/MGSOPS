"""Right-to-left reveal for the dialogue engine (briefing talk, in-game dialogue).

The engine (0x38460) redraws a line every frame from its first character, one character more
each time, calling font->draw(font, x, y, code) through the pointer at font+0x14 (0x37820).
Arabic used to be stored reversed (display order) and padded, so the reveal ran left to right.

With this patch an Arabic line is stored in reading order and starts with the marker
\\x80\\xFE (a code the glyph drawer skips: no ink, no width).  A small wrapper replaces the
font's draw pointer; for a marked line it draws each glyph at  RIGHT - x - width  instead of
x, so the line grows from the right edge.  Lines without the marker are drawn as before.

Mirror edge: the dialogue window (text box 480 wide) mirrors on RIGHT, its right edge; any
other box (floating chatter over the soldiers, centred on its own width) mirrors inside the
line's own width, measured at the marker from the engine's string pointer (4(sp)), so the
glyphs land exactly where left-to-right text would.

Line state (one word kept after the code):
    marker drawn                       -> 2
    x == 0, any other code             -> 1 if the state was 2 (first glyph of a marked line), else 0
    x != 0 and state 2                 -> 1
    mirror while the state is not 0

EBOOT changes (relocatable PRX, offsets relative to the module):
    0x380E8  lui   v0, 3        -> lui   v0, hi(CAVE)      (relocated HI16)
    0x380F8  addiu v0, v0, 0x7820 -> addiu v0, v0, lo(CAVE) (relocated LO16)
    CAVE = 0x18C8A8: an unreferenced VFPU helper (no calls, pointers or branches into it).
The wrapper finds itself with BAL and reaches 0x37820 by a PC-relative offset, so it needs no
relocation of its own.
"""
from __future__ import annotations

import struct
from pathlib import Path

DRAW = 0x37820            # the font's glyph drawer
CAVE = 0x18C8A8           # unused function, 0x138 bytes
HOOK_HI, HOOK_LO = 0x380E8, 0x380F8
ICON_DRAW = 0x375FC       # the font's icon drawer (codes above 0x80FF: button marks)
ICON_CAVE = 0x18C9E0      # the next unused function, 0xA8 bytes
ICON_HI, ICON_LO = 0x380F4, 0x380FC
RIGHT = 440               # dialogue lines end here (project.DIALOGUE_RIGHT)
MARKER = b"\x80\xfe"

R = {n: i for i, n in enumerate(
    "zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 s0 s1 s2 s3 s4 s5 s6 s7 t8 t9".split())}
R["ra"] = 31
R["sp"] = 29


def _r(op, rs, rt, rd, sh, fn):
    return (op << 26) | (R[rs] << 21) | (R[rt] << 16) | (R[rd] << 11) | (sh << 6) | fn


def _i(op, rs, rt, imm):
    return (op << 26) | (R[rs] << 21) | (R[rt] << 16) | (imm & 0xFFFF)


def addu(d, s, t): return _r(0, s, t, d, 0, 0x21)
def subu(d, s, t): return _r(0, s, t, d, 0, 0x23)
def sll(d, t, n): return _r(0, "zero", t, d, n, 0)
def jr(s): return _r(0, s, "zero", "zero", 0, 8)
def addiu(t, s, imm): return _i(9, s, t, imm)
def andi(t, s, imm): return _i(0xC, s, t, imm)
def ori(t, s, imm): return _i(0xD, s, t, imm)
def xori(t, s, imm): return _i(0xE, s, t, imm)
def sltiu(t, s, imm): return _i(0xB, s, t, imm)
def lui(t, imm): return _i(0xF, "zero", t, imm)
def lw(t, off, s): return _i(0x23, s, t, off)
def sw(t, off, s): return _i(0x2B, s, t, off)
def ext(t, s, pos, size): return (0x1F << 26) | (R[s] << 21) | (R[t] << 16) | ((size - 1) << 11) | (pos << 6)
NOP = 0


def lh(t, off, s): return _i(0x21, s, t, off)
def lbu(t, off, s): return _i(0x24, s, t, off)


def wrapper(right: int = RIGHT, entry: bool = False) -> bytes:
    return _wrapper(right, entry)[0]


def _wrapper(right: int, entry: bool) -> tuple[bytes, dict]:
    """the wrapper's machine code, followed by its two data words (line state, mirror edge).
    entry=False: called through the font's draw pointer; True: jumped to from the drawer's
    first instruction (the cheat)"""
    code: list = []
    labels: dict[str, int] = {}

    def at(name):
        labels[name] = len(code)

    def op(*items):
        code.extend(items)

    B = lambda kind, rs, rt, label: ("BR", kind, rs, rt, label)      # beq / bne to a label
    GO = lambda label: ("BR", "beq", "zero", "zero", label)          # unconditional
    D = lambda kind, reg, name: ("DATA", kind, reg, name)            # lw/sw reg, name(t8)

    op(addu("t7", "ra", "zero"),                 # keep the caller's return address
       (1 << 26) | (0x11 << 16) | 1,             # bal L0  (bgezal zero, +1)
       NOP)
    at("L0")
    op(addu("t8", "ra", "zero"),                 # t8 = address of L0 (data words are t8-relative)
       addu("ra", "t7", "zero"),
       andi("t1", "a3", 0x9FFF),                 # code without the colour bits
       xori("t1", "t1", 0x80FE),                 # 0 for the marker
       D("lw", "t2", "STATE"),
       B("bne", "a1", "zero", "XNZ"),
       xori("t3", "t2", 2),                      # (delay) 0 if the state is 2
       # x == 0: a line starts
       B("bne", "t1", "zero", "STORE"),
       sltiu("t3", "t3", 1),                     # (delay) 1 if the marker came just before
       # the marker: pick the edge to mirror on
       addiu("t3", "zero", 2),
       lh("t5", 0xA, "a0"),                      # text box width; 480 = the dialogue window
       xori("t5", "t5", 480),
       B("beq", "t5", "zero", "SETR"),
       addiu("t9", "zero", right),               # (delay) dialogue window: its right edge
       # any other box (floating chatter): mirror inside the line's own width
       lw("t4", 0x20, "a0"),                     # font data
       lw("v1", 8, "t4"),                        # glyph table
       lw("t5", 4, "sp"),                        # the rest of the line (the engine's pointer)
       addu("t9", "zero", "zero"))
    at("LOOP")
    op(lbu("t6", 0, "t5"),
       B("beq", "t6", "zero", "SETR"),
       xori("t7", "t6", 0x0A),                   # (delay)
       B("beq", "t7", "zero", "SETR"),
       sltiu("t7", "t6", 0x80),                  # (delay)
       B("bne", "t7", "zero", "ONE"),
       addiu("t5", "t5", 1),                     # (delay)
       xori("t7", "t6", 0x80),
       B("bne", "t7", "zero", "ICON"),
       lbu("t6", 0, "t5"),                       # (delay) the glyph after 0x80
       GO("GLYPH"),
       addiu("t5", "t5", 1))                     # (delay)
    at("ICON")                                   # an icon is as wide as the font's cell
    op(lbu("t7", 4, "t4"), addu("t9", "t9", "t7"), addiu("t9", "t9", 1),
       GO("LOOP"), addiu("t5", "t5", 1))
    at("ONE")
    op(sltiu("t7", "t6", 0x20),                  # control bytes take no room
       B("bne", "t7", "zero", "LOOP"), NOP)
    at("GLYPH")
    op(sll("t7", "t6", 2), addu("t7", "t7", "v1"),
       lw("t7", -0x80, "t7"), ext("t7", "t7", 20, 6),
       addu("t9", "t9", "t7"),
       GO("LOOP"), addiu("t9", "t9", 1))         # (delay) width = field + 1
    at("SETR")
    op(D("sw", "t9", "EDGE"))
    at("STORE")
    op(D("sw", "t3", "STATE"),
       GO("MIRROR"), addu("t2", "t3", "zero"))   # (delay)
    at("XNZ")                                    # x != 0: a marked line keeps going (t3 = state ^ 2)
    op(B("bne", "t3", "zero", "MIRROR"), NOP,
       addiu("t2", "zero", 1), D("sw", "t2", "STATE"))
    at("MIRROR")
    op(B("beq", "t2", "zero", "CALL"),
       lw("t4", 0x20, "a0"),                     # (delay) font data
       andi("t5", "a3", 0xFF),                   # glyph table entry -> width
       lw("t6", 8, "t4"),
       sll("t5", "t5", 2),
       addu("t5", "t5", "t6"),
       lw("t5", -0x80, "t5"),                    # entries start at code 0x20
       ext("t5", "t5", 20, 6),                   # width - 1
       addu("a1", "a1", "t5"),
       D("lw", "t6", "EDGE"),
       addiu("t6", "t6", -1),
       subu("a1", "t6", "a1"))                   # x = EDGE - x - width
    at("CALL")
    if entry:
        # reached from a jump at the drawer's first instruction: do its first two
        # instructions here and continue at the third
        diff = (DRAW + 8 - (CAVE + 4 * labels["L0"])) & 0xFFFFFFFF
        op(lui("t9", diff >> 16), ori("t9", "t9", diff & 0xFFFF),
           addu("t8", "t8", "t9"), addiu("sp", "sp", -0x50), jr("t8"),
           andi("v0", "a3", 0xFF))
    else:
        diff = (DRAW - (CAVE + 4 * labels["L0"])) & 0xFFFFFFFF
        op(lui("t9", diff >> 16), ori("t9", "t9", diff & 0xFFFF),
           addu("t8", "t8", "t9"), jr("t8"), NOP)
    at("STATE")
    op(0)                                        # line state
    at("EDGE")
    op(right)                                    # mirror edge of the current line
    out = []
    for n, c in enumerate(code):
        if isinstance(c, tuple) and c[0] == "BR":
            _t, kind, rs, rt, label = c
            c = _i(4 if kind == "beq" else 5, rs, rt, labels[label] - (n + 1))
        elif isinstance(c, tuple) and c[0] == "DATA":
            _t, kind, reg, name = c
            off = 4 * (labels[name] - labels["L0"])
            c = lw(reg, off, "t8") if kind == "lw" else sw(reg, off, "t8")
        out.append(c)
    blob = b"".join(struct.pack("<I", w) for w in out)
    assert len(blob) <= 0x138, len(blob)
    return blob, labels


def icon_wrapper(right: int = RIGHT, entry: bool = False) -> bytes:
    """the icon drawer's wrapper (button marks such as {8C02} in a line): mirrored like the
    glyphs, x = EDGE - x - width, with the glyph wrapper's line state and edge.  At x == 0 it
    starts a line the same way (an icon right after the marker belongs to a marked line, any
    other line start ends one).  t0 is an argument of the icon drawer: left alone."""
    main = _wrapper(right, entry)[1]
    code: list = []
    labels: dict[str, int] = {}

    def at(name):
        labels[name] = len(code)

    code.extend([addu("t7", "ra", "zero"), (1 << 26) | (0x11 << 16) | 1, NOP])     # bal L0
    at("L0")
    here = ICON_CAVE + 4 * labels["L0"]
    state = CAVE + 4 * main["STATE"] - here       # the glyph wrapper's data, t8-relative
    edge = CAVE + 4 * main["EDGE"] - here
    code.extend([addu("t8", "ra", "zero"),
                 addu("ra", "t7", "zero"),
                 lw("t2", state, "t8"),
                 ("BR", "bne", "a1", "zero", "MIRROR"),
                 xori("t3", "t2", 2),                 # (delay) 0 if the marker came just before
                 sltiu("t2", "t3", 1),                # x == 0: marked line only right after it
                 sw("t2", state, "t8")])
    at("MIRROR")
    code.extend([("BR", "beq", "t2", "zero", "CALL"),
                 lw("t4", 0x20, "a0"),                # (delay) font data
                 lbu("t5", 4, "t4"),                  # icon width
                 lw("t6", edge, "t8"),
                 addu("a1", "a1", "t5"),
                 subu("a1", "t6", "a1")])             # x = EDGE - x - width
    at("CALL")
    if entry:          # jumped to from the drawer's first instruction: do its first two here
        diff = (ICON_DRAW + 8 - here) & 0xFFFFFFFF
        code.extend([lui("t9", diff >> 16), ori("t9", "t9", diff & 0xFFFF), addu("t8", "t8", "t9"),
                     addiu("sp", "sp", -0x50), jr("t8"), sw("s4", 0x30, "sp")])
    else:
        diff = (ICON_DRAW - here) & 0xFFFFFFFF
        code.extend([lui("t9", diff >> 16), ori("t9", "t9", diff & 0xFFFF), addu("t8", "t8", "t9"),
                     jr("t8"), NOP])
    out = []
    for n, c in enumerate(code):
        if isinstance(c, tuple):
            _t, kind, rs, rt, label = c
            c = _i(4 if kind == "beq" else 5, rs, rt, labels[label] - (n + 1))
        out.append(c)
    blob = b"".join(struct.pack("<I", w) for w in out)
    assert len(blob) <= 0xA8, len(blob)
    return blob


DATA_WORDS = 2      # state + edge, after the code (left out of the cheat)


def hook_words(target: int = CAVE, reg: str = "v0") -> tuple[int, int]:
    """(lui, addiu) that make the font setup store `target` (module offset) at font+0x14
    (font+0x18 with reg "v1")"""
    hi = ((target + 0x8000) >> 16) & 0xFFFF
    return lui(reg, hi), addiu(reg, reg, target & 0xFFFF)


# -- text side ----------------------------------------------------------------------------

def _tokens(line: bytes) -> list[bytes]:
    out, i = [], 0
    while i < len(line):
        b = line[i]
        if b == 0x3C and b">" in line[i:]:                      # <tag>
            j = line.index(b">", i) + 1
            out.append(line[i:j]); i = j
        elif (b >= 0x80 or b == 0x1F) and i + 1 < len(line):   # 2-byte codes
            out.append(line[i:i + 2]); i += 2
        else:
            out.append(line[i:i + 1]); i += 1
    return out


def to_rtl(encoded: bytes, brk: bytes = b"\n") -> bytes:
    """display-order game bytes (as arabic_text.encode makes them, padded to the right edge)
    -> reading order with the marker on every line, no padding"""
    lines = []
    for line in encoded.split(brk):
        toks = _tokens(line.lstrip(b" "))
        lines.append(MARKER + b"".join(reversed(toks)))
    return brk.join(lines)


# -- the executable ---------------------------------------------------------------------------

#: segment 0 (code) starts at this file offset in the decrypted EBOOT (PPSSPP's dump)
SEG0 = 0xC0
#: words the patch expects before it changes them
ORIGINAL = {HOOK_HI: 0x3C020003, HOOK_LO: 0x24427820, CAVE: 0x27BDFFE0,
            ICON_HI: 0x3C030003, ICON_LO: 0x246375FC, ICON_CAVE: 0x27BDFFE0}


def patch_eboot(elf: bytes, right: int = RIGHT) -> bytes:
    """decrypted EBOOT.BIN (ELF/PRX) -> the same with the right-to-left wrapper installed"""
    if elf[:4] != b"\x7fELF":
        raise ValueError("EBOOT.BIN is not decrypted: use the PPSSPP dump "
                         "(Debug > Dump decrypted EBOOT.BIN)")
    out = bytearray(elf)
    for addr, word in ORIGINAL.items():
        have = struct.unpack_from("<I", out, SEG0 + addr)[0]
        if have != word:
            raise ValueError(f"EBOOT.BIN differs at {addr:#x} ({have:08x}, expected {word:08x}): "
                             "not the ULUS10202 executable this patch was made for")
    hi, lo = hook_words()
    struct.pack_into("<I", out, SEG0 + HOOK_HI, hi)
    struct.pack_into("<I", out, SEG0 + HOOK_LO, lo)
    blob = wrapper(right)
    out[SEG0 + CAVE:SEG0 + CAVE + len(blob)] = blob
    hi, lo = hook_words(ICON_CAVE, "v1")
    struct.pack_into("<I", out, SEG0 + ICON_HI, hi)
    struct.pack_into("<I", out, SEG0 + ICON_LO, lo)
    blob = icon_wrapper(right)
    out[SEG0 + ICON_CAVE:SEG0 + ICON_CAVE + len(blob)] = blob
    return bytes(out)


# -- the cheat (no executable change) ---------------------------------------------------------

#: where the game's module is loaded (CWCheat addresses are absolute, offset from 0x08800000)
BASE = 0x08804000
GAME_ID = "ULUS-10202"
CHEAT_NAME = "Arabic: right-to-left dialogue"


def cheat_words(right: int = RIGHT) -> list[tuple[int, int]]:
    """(module offset, word) writes: a jump at the drawer's entry + the wrapper code.
    The wrapper's state word is left out (the cheat is re-applied all the time; the code
    sets the state itself at the start of every line)."""
    blob = wrapper(right, entry=True)
    words = [(CAVE + 4 * k, struct.unpack_from("<I", blob, 4 * k)[0])
             for k in range(len(blob) // 4 - DATA_WORDS)]
    jump = (2 << 26) | (((BASE + CAVE) >> 2) & 0x3FFFFFF)        # j CAVE
    icon = icon_wrapper(right, entry=True)
    words += [(ICON_CAVE + 4 * k, struct.unpack_from("<I", icon, 4 * k)[0])
              for k in range(len(icon) // 4)]
    icon_jump = (2 << 26) | (((BASE + ICON_CAVE) >> 2) & 0x3FFFFFF)
    return [(DRAW, jump), (DRAW + 4, NOP), (ICON_DRAW, icon_jump), (ICON_DRAW + 4, NOP)] + words


def cheat_text(right: int = RIGHT, enabled: bool = True) -> str:
    """one CWCheat entry (for <memstick>/PSP/Cheats/ULUS10202.ini in PPSSPP)"""
    lines = [f"_C{1 if enabled else 0} {CHEAT_NAME}"]
    lines += ["_L 0x2%07X 0x%08X" % (BASE + off - 0x08800000, word)
              for off, word in cheat_words(right)]
    return "\n".join(lines) + "\n"


#: 60 fps in gameplay: the community cheat "60FPS v3 [Ingame only]" (tools/fps60_ULUS10202.txt),
#: the user's pick.  Holding the frame limiter alone (data 0x089F5210 at 1) made the game run
#: 2-3x fast -- its logic steps once a frame; v3 re-times gameplay too and only acts while the
#: limiter is at its in-game setting, so menus, codec and cutscenes keep their rate.  It shares
#: no address with the right-to-left cheat.  Earlier entries of ours are replaced.
FPS_NAME = "60FPS v3 [Ingame only]"
OLD_FPS_NAMES = ("60 FPS (experimental)", "60 FPS (community v4)", "20/30 FPS (undo 60 FPS)")
FPS_FILE = Path(__file__).with_name("fps60_ULUS10202.txt")


def fps_cheat_text(enabled: bool = False) -> str:
    """the 60 fps entry as a CWCheat entry"""
    lines = [l for l in FPS_FILE.read_text(encoding="utf-8").splitlines() if l.startswith("_L")]
    return f"_C{1 if enabled else 0} {FPS_NAME}\n" + "\n".join(lines) + "\n"


def install_cheat(path, right: int = RIGHT) -> None:
    """add (or refresh) our entries in a PPSSPP cheat file, keeping the user's other cheats
    (and whether 60 fps is switched on)"""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    blocks, cur = [], []
    for line in text.splitlines():
        if line.startswith("_C") and cur:
            blocks.append(cur); cur = []
        cur.append(line)
    if cur:
        blocks.append(cur)
    head = [b for b in blocks if not b[0].startswith("_C")]
    ours = (CHEAT_NAME, FPS_NAME) + OLD_FPS_NAMES
    fps_on = any(b[0].startswith("_C1") and b[0][3:].strip() == FPS_NAME for b in blocks)
    cheats = [b for b in blocks if b[0].startswith("_C") and b[0][3:].strip() not in ours]
    head = [[l for l in b if l.strip("﻿ 	")] for b in head]
    if not any(l.startswith("_S") for b in head for l in b):
        head = [[f"_S {GAME_ID}", "_G Metal Gear Solid: Portable Ops"]] + [b for b in head if b]
    out = "\n".join("\n".join(b) for b in head + cheats)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((out + "\n" if out else "") + cheat_text(right) + fps_cheat_text(fps_on),
                    encoding="utf-8")
