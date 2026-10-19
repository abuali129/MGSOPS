"""Replace caption files inside stage/flashdemo/_zar (and stage/ending/_zar).

usage (python API): patch_caps(game_usrdir, {"s01_01_2_e.cap": entries_or_bytes, ...})
"""
import os, struct, zlib
from archive import dar_parse, dar_build, zar_read
from safe_write import safe_write
import cap


def patch_caps(usrdir, replacements, stages=("flashdemo", "ending")):
    left = dict(replacements)
    for st in stages:
        path = os.path.join(usrdir, "stage", st, "_zar")
        ents = dar_parse(zar_read(path))
        changed = False
        for e in ents:
            if e[0] != "cache.dar":
                continue
            dar = dar_parse(e[1])
            for c in dar:
                if c[0] in replacements:
                    new = replacements[c[0]]
                    c[1] = new if isinstance(new, bytes) else cap.build(new)
                    left.pop(c[0], None)
                    changed = True
            e[1] = dar_build(dar)
        if changed:
            raw = dar_build(ents)
            safe_write(path, struct.pack("<I", len(raw)) + zlib.compress(raw, 9))
    return left
