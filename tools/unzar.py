"""Decompress every stage/*/_zar (u32 LE size + zlib stream) into work/zar/<stage>.bin"""
import os, struct, zlib, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME = os.path.join(ROOT, "..", "MGSPO", "PSP_GAME", "USRDIR")
OUT = os.path.join(ROOT, "work", "zar")
os.makedirs(OUT, exist_ok=True)

stage_dir = os.path.join(GAME, "stage")
for st in sorted(os.listdir(stage_dir)):
    p = os.path.join(stage_dir, st, "_zar")
    if not os.path.isfile(p):
        continue
    data = open(p, "rb").read()
    size = struct.unpack_from("<I", data)[0]
    d = zlib.decompressobj()
    raw = d.decompress(data[4:])
    tail = len(d.unused_data)
    ok = "OK" if len(raw) == size else f"SIZE MISMATCH {len(raw)}"
    print(f"{st:16} comp={len(data):9} raw={size:9} trailing={tail} {ok}")
    open(os.path.join(OUT, st + ".bin"), "wb").write(raw)
