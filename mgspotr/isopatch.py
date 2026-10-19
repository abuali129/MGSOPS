"""Patch files inside a copy of a PSP ISO (ISO 9660, 2048-byte sectors).

A replacement that fits in the sectors the old file occupied is written in place;
a larger one is appended at the end of the image and its directory record is
pointed at the new extent.  Nothing else on the disc moves, so the result is the
original image plus the changed files -- no re-mastering, no tool in the middle.
"""

from __future__ import annotations

import os
import shutil
import struct
from pathlib import Path
from typing import Callable

SECTOR = 2048


class IsoError(Exception):
    pass


def _both32(value: int) -> bytes:
    return struct.pack("<I", value) + struct.pack(">I", value)


class Iso:
    def __init__(self, path: str | Path, writable: bool = False):
        self.path = Path(path)
        self.f = open(self.path, "r+b" if writable else "rb")
        pvd = self._read(16 * SECTOR, SECTOR)
        if pvd[1:6] != b"CD001" or pvd[0] != 1:
            raise IsoError(f"{self.path} is not an ISO 9660 image")
        self.volume_sectors = struct.unpack_from("<I", pvd, 80)[0]
        root = pvd[156:156 + 34]
        self.root = (struct.unpack_from("<I", root, 2)[0],
                     struct.unpack_from("<I", root, 10)[0])

    def close(self) -> None:
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _read(self, offset: int, size: int) -> bytes:
        self.f.seek(offset)
        return self.f.read(size)

    def _records(self, extent: int, size: int):
        """(record offset in image, name, extent, size, is_dir) for a directory"""
        data = self._read(extent * SECTOR, size)
        pos = 0
        while pos < len(data):
            length = data[pos]
            if length == 0:                      # records never cross a sector
                pos = (pos // SECTOR + 1) * SECTOR
                continue
            rec = data[pos:pos + length]
            name_len = rec[32]
            name = rec[33:33 + name_len]
            if name not in (b"\x00", b"\x01"):
                text = name.decode("latin1").split(";")[0]
                yield (extent * SECTOR + pos, text,
                       struct.unpack_from("<I", rec, 2)[0],
                       struct.unpack_from("<I", rec, 10)[0],
                       bool(rec[25] & 2))
            pos += length

    def find(self, rel_path: str):
        """record offset, extent, size of a file given as 'PSP_GAME/USRDIR/...'"""
        extent, size = self.root
        parts = [p for p in rel_path.replace("\\", "/").split("/") if p]
        for i, part in enumerate(parts):
            for off, name, ext, sz, is_dir in self._records(extent, size):
                if name.upper() == part.upper():
                    if i == len(parts) - 1:
                        return off, ext, sz
                    extent, size = ext, sz
                    break
            else:
                raise IsoError(f"{rel_path}: '{part}' not found in the image")
        raise IsoError(f"{rel_path}: not a file")

    def replace(self, rel_path: str, data: bytes) -> str:
        rec_off, extent, size = self.find(rel_path)
        have = (size + SECTOR - 1) // SECTOR
        need = (len(data) + SECTOR - 1) // SECTOR
        if need <= have:
            where, how = extent, "in place"
        else:
            self.f.seek(0, os.SEEK_END)
            end = self.f.tell()
            where = (end + SECTOR - 1) // SECTOR
            how = "appended"
        self.f.seek(where * SECTOR)
        self.f.write(data + bytes(need * SECTOR - len(data)))
        if how == "in place" and have > need:          # clear what the old file left
            self.f.write(bytes((have - need) * SECTOR))
        self.f.seek(rec_off + 2)
        self.f.write(_both32(where) + _both32(len(data)))
        if how == "appended":
            total = where + need
            if total > self.volume_sectors:
                self.volume_sectors = total
                self.f.seek(16 * SECTOR + 80)
                self.f.write(_both32(total))
        return how


def patch_copy(original: str | Path, out: str | Path, files: dict[str, bytes],
               progress: Callable[[str], None] = lambda _m: None) -> list[str]:
    """Copy ``original`` to ``out`` and replace ``files`` (iso path -> bytes)."""
    progress(f"Copying {Path(original).name}...")
    shutil.copyfile(original, out)
    notes = []
    with Iso(out, writable=True) as iso:
        for rel, data in sorted(files.items()):
            how = iso.replace(rel, data)
            notes.append(f"{rel}  ({len(data):,} bytes, {how})")
            progress(notes[-1])
    return notes
