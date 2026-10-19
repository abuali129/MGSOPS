"""Read/write the Konami stage containers used by MGS Portable Ops.

_zar   : u32 LE uncompressed size + zlib stream; payload is a "dar" archive.
dar    : u32 count, then per entry: name\\0 (pad to 4), u32 size (pad to 16), data, 1 NUL byte.
         Used for both the _zar payload (data.cnf, cache.dar, scenerio.gcx, ...) and cache.dar.
"""
import struct, zlib


def dar_parse(data):
    n = struct.unpack_from("<I", data, 0)[0]
    p = 4
    out = []
    for _ in range(n):
        e = data.index(b"\0", p)
        name = data[p:e].decode("latin1")
        p = (e + 1 + 3) & ~3
        size = struct.unpack_from("<I", data, p)[0]
        p = (p + 4 + 15) & ~15
        out.append([name, data[p:p + size]])
        p += size + 1
    assert p == len(data), (p, len(data))
    return out


def dar_build(entries):
    out = bytearray(struct.pack("<I", len(entries)))
    for name, blob in entries:
        out += name.encode("latin1") + b"\0"
        out += b"\0" * (-len(out) % 4)
        out += struct.pack("<I", len(blob))
        out += b"\0" * (-len(out) % 16)
        out += blob + b"\0"
    return bytes(out)


def zar_read(path):
    data = open(path, "rb").read()
    size = struct.unpack_from("<I", data)[0]
    raw = zlib.decompress(data[4:])
    assert len(raw) == size
    return raw


def zar_write(path, raw, level=9):
    open(path, "wb").write(struct.pack("<I", len(raw)) + zlib.compress(raw, level))
