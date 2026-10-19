"""Title-menu experiments: python exp_title.py <variant>  (writes the test copy's title/_zar)"""
import os, struct, sys, zlib
import qar, arabic_text, menu_font
from arabic_map import SLOTS
from gcx import Gcx
from archive import dar_parse, dar_build, zar_read
SRC = "../../MGSPO/PSP_GAME/USRDIR/stage/title/_zar"
DST = "../project/test_game/PSP_GAME/USRDIR/stage/title/_zar"
AR = ["لعبة جديدة", "تحميل اللعبة", "حذف", "مواجهة حرة", "مشاركة اللعبة", "إعداد الأزرار",
      "الخيارات", "سهل", "عادي", "صعب جدًا", "نعم", "لا"]


def write(txp=None, la2=None, arabic=True):
    top = dar_parse(zar_read(SRC)); topd = dict(top)
    dar = dar_parse(topd["cache.dar"]); qd = topd["cache.qar"]
    if la2 is not None:
        for e in dar:
            if e[0] == "lay_multiindex.la2": e[1] = la2
    g = Gcx(topd["minifont.fcx"])
    if arabic:
        for (i, raw), a in zip(g.texts(), AR):
            g.items[i] = arabic_text.encode(a, brk=b"\n") + b"\n"
    for e in top:
        if e[0] == "cache.dar": e[1] = dar_build(dar)
        elif e[0] == "cache.qar" and txp is not None:
            e[1] = qar.build(qar.parse(qd), {"lay_multiindex_0.txp": txp}, qd)
        elif e[0] == "minifont.fcx": e[1] = g.build()
    raw = dar_build(top)
    if os.path.exists(DST): os.remove(DST)
    open(DST, "wb").write(struct.pack("<I", len(raw)) + zlib.compress(raw, 9))


def originals():
    top = dict(dar_parse(zar_read(SRC)))
    qd = top["cache.qar"]; e = {x[0]: x for x in qar.parse(qd)}["lay_multiindex_0.txp"]
    return qd[e[1]:e[1] + e[2]], dict(dar_parse(top["cache.dar"]))["lay_multiindex.la2"]


if __name__ == "__main__":
    t, la = originals()
    v = sys.argv[1]
    if v == "relocate":          # same image, data + clut moved to the end of the file
        im = qar.images(t)[1]
        out = bytearray(t); out += bytes(-len(out) % 16)
        doff = len(out); out += t[im["data"]:im["data"] + im["w"] * im["h"] // 2]
        coff = len(out); out += t[im["clut"]:im["clut"] + 64]
        struct.pack_into("<4I", out, im["rec"], im["dims"], im["fmt"], doff, coff)
        write(bytes(out), None, arabic=False)
    elif v == "sep":              # Arabic glyphs in a new image, Arabic strings
        nt, nla, n, idx = menu_font.add_arabic_image(t, la, 1)
        print("glyphs", n, "new image index", idx, "txp", len(t), "->", len(nt))
        write(nt, nla, arabic=True)
    elif v == "arabic":           # grow image 1 with Arabic rows, only the item font uses them
        nt, nla, n = menu_font.add_arabic(t, la, 1, fonts={0x8D08EA})
        print("glyphs", n, "image 1 height ->", qar.images(nt)[1]["h"])
        write(nt, nla, arabic=True)
    elif v.startswith("grow"):   # grow<H>[nosub][novs]: taller image, blank rows, English text
        import re as _re; H = int(_re.match(r"grow(\d+)", v).group(1))
        im = qar.images(t)[1]; px, pal = qar.decode(t, im); w, h = im["w"], im["h"]
        rows = [px[r * w:(r + 1) * w] for r in range(h)] + [[0] * w for _ in range(H - h)]
        lin = bytearray()
        for r in rows:
            for c in range(0, w, 2): lin.append((r[c] & 15) | ((r[c + 1] & 15) << 4))
        data = qar.swizzle(bytes(lin), w // 2, H)
        out = bytearray(t); out += bytes(-len(out) % 16)
        doff = len(out); out += data; coff = len(out); out += t[im["clut"]:im["clut"] + 64]
        log2h = (H - 1).bit_length()
        struct.pack_into("<4I", out, im["rec"], (im["dims"] & 0xFFFF) | (H << 16) | (log2h << 28),
                         im["fmt"], doff, coff)
        if "nosub" not in v:
            nsub, subt = struct.unpack_from("<II", t, 16)
            for k in range(nsub):
                s0 = subt + 48 * k
                if struct.unpack_from("<I", t, s0 + 0x14)[0] == im["rec"]:
                    wh = struct.unpack_from("<I", t, s0 + 0xC)[0]
                    struct.pack_into("<I", out, s0 + 0xC, (wh & 0xFFFF) | (H << 16))
                    struct.pack_into("<f", out, s0 + 0x20, 0.5 / (1 << log2h))
                    struct.pack_into("<f", out, s0 + 0x28, (H - 1) / (1 << log2h))
        lb = bytearray(la)
        if "novs" not in v:
            offs = struct.unpack_from("<12I", lb, 4); fs = offs[10]
            cnt = struct.unpack_from("<I", lb, fs)[0]; p = fs + 4
            for _ in range(cnt):
                n = struct.unpack_from("<H", lb, p + 4)[0]
                fh = struct.unpack_from("<I", lb, p)[0]
                for i in range(n):
                    e = p + 8 + 16 * i
                    if "only8d" in v and fh != 0x8D08EA:
                        continue
                    if lb[e + 3] == 1:
                        v0, v1 = struct.unpack_from("<HH", lb, e + 8)
                        struct.pack_into("<HH", lb, e + 8, min(0xFFFF, round(v0 * h / H)),
                                         min(0xFFFF, round(v1 * h / H)))
                p += 8 + 16 * n
        write(bytes(out), bytes(lb), arabic=False)
    print("written", v)
