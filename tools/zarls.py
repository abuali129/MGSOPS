import struct, sys
def parse(data, align=16, verbose=False):
    n = struct.unpack_from("<I", data, 0)[0]
    p = 4; out = []
    for i in range(n):
        e = data.index(b"\0", p); name = data[p:e].decode("latin1")
        p = (e + 1 + 3) & ~3
        size = struct.unpack_from("<I", data, p)[0]; p += 4
        p = (p + align - 1) & ~(align - 1)
        out.append((name, p, size))
        p += size + 1
    return out, p
if __name__ == "__main__":
    for f in sys.argv[1:]:
        d = open(f, "rb").read()
        try:
            ents, end = parse(d)
            print(f, "end", end, "len", len(d))
            for e in ents: print("   %-20s off=%8x size=%8x" % e)
        except Exception as ex:
            print(f, "ERR", ex)
