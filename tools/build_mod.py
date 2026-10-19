"""Build the Arabic mod into a game folder (default: ../test_game).

- stage/init/_zar     : cache.dar/keepfont.row  <- Arabic font
- stage/flashdemo/_zar, stage/ending/_zar : *.cap <- translation/<capname>.txt (one line per caption entry,
  "\\n" inside a line = forced line break; empty line = keep English)
"""
import os, struct, sys, zlib
from archive import dar_parse, dar_build, zar_read
from safe_write import safe_write
from keepfont import KeepFont
import cap, arabic_text
from build_font import build as build_font

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TR = os.path.join(ROOT, "translation")
WRAP_PX = 400


ORIG_USR = os.path.join(os.path.dirname(ROOT), "MGSPO", "PSP_GAME", "USRDIR")


def patch_zar(path, fn):
    """fn(name, blob) -> new blob or None (keep). Applied to top level and cache.dar members.
    Always starts from the untouched original game files."""
    src = os.path.join(ORIG_USR, "stage", os.path.basename(os.path.dirname(path)), "_zar")
    ents = dar_parse(zar_read(src))
    changed = False
    for e in ents:
        if e[0].endswith(".dar"):
            dar = dar_parse(e[1])
            sub = False
            for c in dar:
                nb = fn(c[0], c[1])
                if nb is not None and nb != c[1]:
                    c[1] = nb; sub = True
            if sub:
                e[1] = dar_build(dar); changed = True
        else:
            nb = fn(e[0], e[1])
            if nb is not None and nb != e[1]:
                e[1] = nb; changed = True
    if changed:
        raw = dar_build(ents)
        safe_write(path, struct.pack("<I", len(raw)) + zlib.compress(raw, 9))
    return changed


def load_translation(capname):
    p = os.path.join(TR, capname.rsplit(".", 1)[0] + ".txt")
    if not os.path.exists(p):
        return None
    return [l.rstrip("\r\n").replace("\\n", "\n") for l in open(p, encoding="utf-8")]


def main(game):
    usr = os.path.join(game, "PSP_GAME", "USRDIR")
    font_path = os.path.join(ROOT, "work", "keepfont_ar.row")
    kf = build_font(font_path)
    font = open(font_path, "rb").read()
    widths = {c: kf.width(c) for c in kf.glyphs}

    for st in ("init", "r_main01", "r_main02", "r_net01"):
        print(st, "font:", patch_zar(os.path.join(usr, "stage", st, "_zar"),
                                     lambda n, b: font if n == "keepfont.row" else None))

    def caps(n, b):
        if not n.lower().endswith(".cap") or not b:
            return None
        tr = load_translation(n)
        if tr is None:
            return None
        ents = cap.parse(b)
        if len(tr) != len(ents):
            raise SystemExit(f"{n}: {len(tr)} lines in translation, {len(ents)} captions")
        for e, t in zip(ents, tr):
            if t.strip():
                e["text"] = arabic_text.encode(t, widths, WRAP_PX, align_right_to="block")
        return cap.build(ents)

    for st in ("flashdemo", "ending"):
        print(st, patch_zar(os.path.join(usr, "stage", st, "_zar"), caps))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "test_game"))
