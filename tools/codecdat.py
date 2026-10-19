"""codec.dat -- the codec (radio) calls.

The file is 272 GCX blocks (see gcx.py) packed on 16-byte boundaries.  Stage
scripts start a call with ``01 <freq u16> 0A <u32 0x01000000 | offset/16>`` in
their bytecode; the game then reads a 16 KB window from that offset.

Blocks whose reference was found in the stage scripts are *movable*: the
rebuild may put them anywhere and patches every reference.  The others (event
calls the engine starts itself) are *pinned*: they keep their offset and may
only grow into the room before the next pinned block.
"""

from __future__ import annotations

import struct

from gcx import Gcx

MAGIC = bytes.fromhex("9ce13145")
ALIGN = 16
FILE_ID = 0x01000000
WINDOW = 0x4000          # bytes the game reads for one call


def block_offsets(data: bytes) -> list[int]:
    out, i = [], data.find(MAGIC)
    while i >= 0:
        if i % ALIGN == 0:
            out.append(i)
        i = data.find(MAGIC, i + 1)
    return out


def split_blocks(data: bytes) -> dict[int, bytes]:
    offs = block_offsets(data)
    ends = offs[1:] + [len(data)]
    return {o: data[o:e] for o, e in zip(offs, ends)}


def find_refs(script: bytes, valid: set[int]) -> list[tuple[int, int]]:
    """(position of the u32, block offset) for every codec reference in a GCX's bytecode"""
    g = Gcx(script)
    start = g.base + g.size
    out = []
    j = script.find(b"\x0a", start)
    while 0 <= j <= len(script) - 5:
        if script[j + 4] == 1 and script[j + 3] == 0:
            off = (struct.unpack_from("<I", script, j + 1)[0] & 0xFFFFFF) * ALIGN
            if off in valid:
                out.append((j + 1, off))
        j = script.find(b"\x0a", j + 1)
    return out


def patch_refs(script: bytes, valid: set[int], moved: dict[int, int]) -> tuple[bytes, int]:
    out = bytearray(script)
    n = 0
    for pos, off in find_refs(script, valid):
        if off in moved and moved[off] != off:
            struct.pack_into("<I", out, pos, FILE_ID | (moved[off] // ALIGN))
            n += 1
    return bytes(out), n


def pad(blob: bytes) -> bytes:
    return blob + bytes(-len(blob) % ALIGN)


def layout(original: dict[int, bytes], new: dict[int, bytes], pinned: set[int]):
    """Place blocks.  Returns (codec.dat bytes, {old offset: new offset}, problems).

    Everything stays at its original offset if it still fits there, so an
    untouched block never moves.  A movable block that grew goes to the end of
    the file.  A pinned block that grew may take the space of the movable blocks
    after it (they go to the end too); if it still does not fit it is reported
    in ``problems`` as (offset, size needed, room)."""
    offs = sorted(original)
    total = max(o + len(b) for o, b in original.items())
    size = {o: len(pad(new[o])) for o in offs}
    evicted: set[int] = set()
    problems = []
    for k, o in enumerate(offs):
        if o in evicted:
            continue
        nxt = k + 1
        limit = offs[nxt] if nxt < len(offs) else None
        if limit is None or o + size[o] <= limit:
            continue
        if o not in pinned:
            evicted.add(o)
            continue
        # pinned and too big: push following movable blocks out of the way
        while limit is not None and o + size[o] > limit and offs[nxt] not in pinned:
            evicted.add(offs[nxt])
            nxt += 1
            limit = offs[nxt] if nxt < len(offs) else None
        if limit is not None and o + size[o] > limit:
            problems.append((o, size[o], limit - o))
    moved: dict[int, int] = {}
    tail = total
    for o in offs:
        if o in evicted:
            continue
        moved[o] = o
        tail = max(tail, o + size[o])
    tail += -tail % ALIGN
    for o in offs:
        if o in evicted:
            moved[o] = tail
            tail += size[o]
    if any(n != o for o, n in moved.items()):
        # a call reads WINDOW bytes from its offset; a short read at the end of the
        # file hangs the game, so every block needs a full window behind it
        tail = max(tail, max(moved.values()) + WINDOW)
    out = bytearray(tail)
    for o, n in moved.items():
        blob = pad(new[o])
        out[n:n + len(blob)] = blob
    return bytes(out), moved, problems
