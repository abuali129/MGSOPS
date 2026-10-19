"""Cutscene caption (.cap) files.

header : u32 file_size, u32 data_size (both 16-aligned; file_size may be larger
         when the original tool left junk after the data), u32 0, u32 0
         u32 entries_bytes
entry  : u32 start, u32 end, u32 0, u16 entry_size, u16 flag(1),
         text NUL-terminated, padded to 4 (entry_size includes the 16-byte entry header)
text   : UTF-8; "\\x80|" is a line break.
"""
import struct

BR = b"\x80|"


def parse(data):
    size, dsize, a, b, nbytes = struct.unpack_from("<5I", data, 0)
    p, end = 20, 20 + nbytes
    out = []
    while p < end:
        start, stop, z, esize, flag = struct.unpack_from("<IIIHH", data, p)
        text = data[p + 16:p + esize].split(b"\0", 1)[0]
        out.append({"start": start, "end": stop, "z": z, "flag": flag, "text": text})
        p += esize
    assert p == end, (p, end)
    return out


def build(entries):
    body = bytearray()
    for e in entries:
        t = e["text"] + b"\0"
        t += b"\0" * (-len(t) % 4)
        body += struct.pack("<IIIHH", e["start"], e["end"], e["z"], 16 + len(t), e["flag"]) + t
    out = bytearray(struct.pack("<I", len(body)) + body)
    total = (16 + len(out) + 16) & ~15   # original tool always adds 1..16 bytes of padding
    out = struct.pack("<IIII", total, total, 0, 0) + out
    return bytes(out + b"\0" * (total - len(out)))
