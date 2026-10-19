"""Project model: a folder holding the game's text, its translation and a build.

A project is a plain folder, so it survives without the app::

    <project>/
        project.json      where the extracted game, the ISO and PPSSPP are
        text/             <category>.json -- English + Arabic per line, editable
        output/           rebuilt game files (PSP_GAME/...) and the patched ISO
        test_game/        hard-linked copy of the game with the output laid over
                          it, bootable as a folder in PPSSPP

The extracted game folder is only ever read.  Translations are stored the way
they are typed: logical Arabic, in reading order, "\\n" for a forced break.
Shaping, reordering, wrapping and the font slots all happen on build.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path
from typing import Callable

from archive import dar_build, dar_parse, zar_read
from qar import build as qar_build, parse as qar_parse
import arabic_text
import cap
import codecdat
import gcx
import hires_pack
import menu_patch
import glyph_png
import rtl_patch
import txp_image
from arabic_map import SLOTS
from build_font import FONT, MINI_H, make_font, make_mini_font
from keepfont import H as CAPTION_H

from PIL import Image

from . import isopatch

#: Tabs, in order.  Only categories with an extractor are listed.
CATEGORIES = ("cutscenes", "dialogue", "codec", "menus")
LABELS = {"cutscenes": "Cutscenes", "dialogue": "Dialogue", "codec": "Codec", "menus": "Menus"}

CODEC_PATH = "PSP_GAME/USRDIR/codec.dat"
#: The game executable (only replaced when the right-to-left reveal is built in, rtl_mode "eboot").
EBOOT_PATH = "PSP_GAME/SYSDIR/EBOOT.BIN"
#: The right-to-left reveal as a PPSSPP/CWCheat cheat (rtl_mode "cheat", the default).
CHEAT_FILE = "ULUS10202.ini"
#: PPSSPP texture replacement folder name, and the list of PNGs our last install put there
TEXTURE_GAME = "ULUS10202"
PACK_LISTING = "mgspotr_pack.txt"
#: Release editions: id -> (label, glyph folder for the menu faces, hi-res pack).
#:   pack None: no texture pack; "pixel": the menu sheets at 4x with sharp pixels;
#:   "ttf": the Arabic cells redrawn at 4x from hires_font.
#: Faces missing from an edition's folder (the small font, captions) come from fonts/.
EDITIONS = {
    "psp": ("PSP / native resolution", "fonts", None),
    "ppsspp_pixel": ("PPSSPP hi-res: pixel glyphs", "fonts", "pixel"),
    "ppsspp_sf": ("PPSSPP hi-res: SF Arabic Rounded menus", "fonts_sf", "ttf"),
}
DEFAULT_EDITION = "psp"
#: The briefing help pages are pictures: six 480x272 images in this texture.  They are
#: exported to <project>/images/originals/ and redrawn by hand as <project>/images/brfhelp_<n>.png
HELP_STAGE, HELP_QAR, HELP_TXP, HELP_PAGES = "briefing", "cache.qar", "brfhelp.txp", 6
#: PPSSPP's dump of the decrypted executable (Debug > Dump decrypted EBOOT.BIN)
DEFAULT_EBOOT_DUMP = Path(__file__).resolve().parent.parent / "work" / "dump" / "ULUS10202_EBOOT.BIN"
#: a call reads this much from its offset; blocks bigger than this (and than they were) get a warning
CODEC_WINDOW = 0x4000

#: MANAGE builds "ALL/<unit>" in a 32-byte buffer (NUL included): longer unit names overflow it
#: and freeze the soldier list (found 2026-10-13 with SPY/TECHNICAL/MEDICAL UNIT at 23-25 bytes).
MANAGE_BUFFER = 32
MANAGE_ALL = "addscvr:0102"                                        # ALL
MANAGE_UNITS = tuple(f"addscvr:{n:04d}" for n in range(8, 19))     # INDEPENDENT .. TEAM DELTA
#: REPORT > LOG rows are built as "<day> <month> 1970 / <location>"; a row that does not fit the
#: game's buffer is left showing the layout's placeholder ("PROGRAM:K.YAMAMURA", 2026-10-15).
#: 52 bytes is the longest row seen to fit, 71 bytes failed: the true limit lies in between.
REPORT_ROW_MAX = 52
REPORT_FIXED = len(b"24 ") + len(b" 1970 / ")                      # day and year parts
REPORT_MONTHS = tuple(f"addscvr:{n:04d}" for n in range(68, 80))   # JANUARY .. DECEMBER
REPORT_PLACES = tuple(f"addscvr:{n:04d}" for n in range(112, 132)) # COMM BASE .. LAUNCH CONTROL ROOM

#: In-game dialogue window: text area width and where right-aligned lines end.
DIALOGUE_WRAP = 430
DIALOGUE_RIGHT = 440
SPEAKER = re.compile(rb"WIN_NAME_SET,([A-Z0-9_]+)")

#: Stages whose cache.dar holds caption (.cap) files.
CAPTION_STAGES = ("flashdemo", "ending")
#: Stages carrying a copy of the caption font (resident copies are the live ones).
FONT_STAGES = ("init", "r_main01", "r_main02", "r_net01")
FONT_NAME = "keepfont.row"
#: The small 10-row font beside it: footers, item and skill descriptions.
MINI_NAME = "keepfont_mini.row"
#: Each stage's menu strings (title menu, pause menu, item names, descriptions, ...).
MENU_TABLE = "minifont.fcx"
#: Caption files that are development leftovers, not shown in the game.
SKIP_CAPS = re.compile(r"_j(_test)?\.cap$", re.I)

#: Fonts the font editor shows: id -> (tab label, kind, height or sheet shape).
#: "row" faces are the .row bitmap fonts (every code 0x20-0xFF); "sheet" faces are the menu
#: layout sheets, where only the Arabic forms are ours (the Latin letters are the game's art).
FACES = {
    "caption": ("Captions (keepfont)", "row", CAPTION_H),
    "small": ("Small text (keepfont_mini)", "row", MINI_H),
    "menu_heading": ("Menu headings (448x60 bold)", "sheet", (448, 60)),
    "menu_items": ("Menu items (256x48)", "sheet", (256, 48)),
    "menu_labels": ("Menu labels (256x36)", "sheet", (256, 36)),
}
FIRST_CODE = 0x20
#: inverse of SLOTS: game code -> Arabic form
FORMS = {code: ch for ch, code in SLOTS.items()}

PROJECT_NAME = "project.json"
DEFAULT_WRAP = 400            # px; the screen is 480, English peaks at 458

ProgressFn = Callable[[str], None]


def _noop(_m: str) -> None:
    pass


class ProjectError(Exception):
    pass


def natural_key(text: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def stage_zar(game: Path, stage: str) -> Path:
    return game / "PSP_GAME" / "USRDIR" / "stage" / stage / "_zar"


def iso_path_of(stage: str) -> str:
    return f"PSP_GAME/USRDIR/stage/{stage}/_zar"


def read_cache(game: Path, stage: str) -> tuple[list, list]:
    """(top-level entries, cache.dar entries) of a stage archive"""
    top = dar_parse(zar_read(stage_zar(game, stage)))
    for name, blob in top:
        if name == "cache.dar":
            return top, dar_parse(blob)
    return top, []


def pack_zar(top: list) -> bytes:
    raw = dar_build(top)
    return struct.pack("<I", len(raw)) + zlib.compress(raw, 9)


def decode_source(raw: bytes) -> str:
    return raw.replace(cap.BR, b"\n").decode("cp1252", errors="replace")


class Project:
    def __init__(self, root: Path, manifest: dict):
        self.root = Path(root)
        self.manifest = manifest
        self._font = None            # (KeepFont, bytes), built on first use
        self._mini = None            # same for keepfont_mini.row
        self._menu_fonts: dict[str, tuple] = {}  # stage -> (entries with menu glyphs, changed?)
        self._packed: dict[str, bytes] = {}      # stage -> packed _zar when only the glyphs changed

    # -- layout ------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.manifest.get("name", self.root.name)

    @property
    def game(self) -> Path:
        return Path(self.manifest["game"])

    @property
    def iso(self) -> Path | None:
        raw = self.manifest.get("iso")
        return Path(raw) if raw else None

    @property
    def ppsspp(self) -> Path | None:
        raw = self.manifest.get("ppsspp")
        return Path(raw) if raw else None

    @property
    def wrap_px(self) -> int:
        return int(self.manifest.get("wrap_px", DEFAULT_WRAP))

    @property
    def font_file(self) -> Path:
        return Path(self.manifest.get("font", FONT))

    @property
    def menu_font_file(self) -> Path:
        """TTF for the menu glyphs (layout sheets and the small font); the caption font by default"""
        return Path(self.manifest.get("menu_font") or self.font_file)

    @property
    def rtl(self) -> bool:
        """dialogue revealed right to left (needs the patched executable)"""
        return bool(self.manifest.get("rtl", True))

    @property
    def rtl_mode(self) -> str:
        """how the right-to-left reveal reaches the game: "cheat" (a PPSSPP/CWCheat cheat,
        the default) or "eboot" (a patched, decrypted EBOOT.BIN in the build)"""
        return self.manifest.get("rtl_mode", "cheat")

    @property
    def cheat_path(self) -> Path:
        return self.output / "cheats" / CHEAT_FILE

    def install_cheat(self) -> Path | None:
        """add the cheat to PPSSPP's cheat file (memstick/PSP/Cheats/ULUS10202.ini)"""
        exe = self.ppsspp
        if not (self.rtl and self.rtl_mode == "cheat" and exe):
            return None
        target = exe.parent / "memstick" / "PSP" / "Cheats" / CHEAT_FILE
        rtl_patch.install_cheat(target, DIALOGUE_RIGHT)
        return target

    @property
    def eboot_dump(self) -> Path:
        return Path(self.manifest.get("eboot") or DEFAULT_EBOOT_DUMP)

    def patched_eboot(self) -> bytes:
        dump = self.eboot_dump
        if not dump.exists():
            raise ProjectError(f"The decrypted EBOOT.BIN is missing ({dump}).\n"
                               "Dump it from PPSSPP (Debug > Dump decrypted EBOOT.BIN) or turn "
                               "off the right-to-left reveal.")
        try:
            return rtl_patch.patch_eboot(dump.read_bytes(), DIALOGUE_RIGHT)
        except ValueError as error:
            raise ProjectError(str(error)) from None

    @property
    def images(self) -> Path:
        return self.root / "images"

    def help_txp(self) -> bytes:
        top = dict(dar_parse(zar_read(stage_zar(self.game, HELP_STAGE))))
        qd = top[HELP_QAR]
        entry = {e[0]: e for e in qar_parse(qd)}[HELP_TXP]
        return qd[entry[1]:entry[1] + entry[2]]

    def export_help_pages(self) -> Path:
        """the original help pages as PNGs (with their transparency) to redraw"""
        folder = self.images / "originals"
        folder.mkdir(parents=True, exist_ok=True)
        txp = self.help_txp()
        for n in range(HELP_PAGES):
            txp_image.export(txp, n).save(folder / f"brfhelp_{n}.png")
        return folder

    def help_page_files(self) -> dict[int, Path]:
        """the redrawn pages present: page number -> PNG"""
        return {n: self.images / f"brfhelp_{n}.png" for n in range(HELP_PAGES)
                if (self.images / f"brfhelp_{n}.png").exists()}

    @property
    def text(self) -> Path:
        return self.root / "text"

    @property
    def output(self) -> Path:
        return self.root / "output"

    @property
    def test_game(self) -> Path:
        return self.root / "test_game"

    @property
    def out_iso(self) -> Path:
        stem = self.iso.stem if self.iso else "MGSPO"
        return self.output / f"{stem} (Arabic).iso"

    def text_of(self, category: str) -> Path:
        return self.text / f"{category}.json"

    def has(self, category: str) -> bool:
        return self.text_of(category).exists()

    # -- create / load -----------------------------------------------------

    @classmethod
    def create(cls, root, game, iso=None, ppsspp=None) -> "Project":
        root, game = Path(root), Path(game)
        if not stage_zar(game, "init").exists():
            raise ProjectError(f"{game} does not look like an extracted MGS Portable Ops "
                               "(no PSP_GAME/USRDIR/stage/init/_zar)")
        root.mkdir(parents=True, exist_ok=True)
        manifest = {"name": root.name, "game": str(game), "wrap_px": DEFAULT_WRAP,
                    "font": str(FONT)}
        if iso:
            manifest["iso"] = str(iso)
        if ppsspp:
            manifest["ppsspp"] = str(ppsspp)
        project = cls(root, manifest)
        project.save()
        return project

    @classmethod
    def load(cls, path) -> "Project":
        path = Path(path)
        if path.is_dir():
            path = path / PROJECT_NAME
        if not path.exists():
            raise ProjectError(f"No {PROJECT_NAME} in {path.parent}")
        return cls(path.parent, json.loads(path.read_text(encoding="utf-8")))

    def save(self) -> None:
        (self.root / PROJECT_NAME).write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # -- extraction --------------------------------------------------------

    def extract(self, progress: ProgressFn = _noop) -> int:
        """Read every caption from the game; keeps translations whose English is unchanged."""
        old = {}
        if self.has("cutscenes"):
            old = {e["key"]: e for e in self.read("cutscenes")["entries"]}

        scenes: dict[str, dict] = {}            # cap name -> {"stages": [...], "entries": [...]}
        for stage in CAPTION_STAGES:
            progress(f"Reading {stage}...")
            _, dar = read_cache(self.game, stage)
            for name, blob in dar:
                if not name.lower().endswith(".cap") or not blob or SKIP_CAPS.search(name):
                    continue
                scene = name.rsplit(".", 1)[0]
                entries = cap.parse(blob)
                if scene in scenes:
                    same = [e["text"] for e in entries] == scenes[scene]["texts"]
                    if same:
                        scenes[scene]["stages"].append(stage)
                        continue
                    scene = f"{stage}/{scene}"
                scenes[scene] = {"file": name, "stages": [stage], "entries": entries,
                                 "texts": [e["text"] for e in entries]}

        out = []
        for scene in sorted(scenes, key=natural_key):
            s = scenes[scene]
            for i, e in enumerate(s["entries"]):
                key = f"{scene}:{i:02d}"
                source = decode_source(e["text"])
                row = {"key": key, "file": s["file"], "stages": s["stages"], "index": i,
                       "start": e["start"], "end": e["end"], "source": source, "target": ""}
                prev = old.get(key)
                if prev and prev.get("source") == source and prev.get("target"):
                    row["target"] = prev["target"]
                out.append(row)
        self.write("cutscenes", {"category": "cutscenes", "entries": out})
        return len(out)

    def stages(self) -> list[str]:
        root = self.game / "PSP_GAME" / "USRDIR" / "stage"
        return sorted((p.name for p in root.iterdir() if (p / "_zar").exists()), key=natural_key)

    def script_texts(self, progress: ProgressFn = _noop) -> dict[bytes, dict]:
        """every distinct text body of the stage scripts, in order of first use:
        body -> {"speaker": first speaker or "", "speakers": any speaker seen, "where": [...]}"""
        if getattr(self, "_script_texts", None) is None:
            found: dict[bytes, dict] = {}
            for stage in self.stages():
                top = dar_parse(zar_read(stage_zar(self.game, stage)))
                blob = dict(top).get("scenerio.gcx")
                if not blob:
                    continue
                progress(f"Reading {stage}...")
                for index, raw in gcx.Gcx(blob).texts():
                    parts = gcx.split(raw)
                    if parts is None:
                        continue
                    m = SPEAKER.search(parts[0])
                    info = found.setdefault(parts[1], {"speaker": m.group(1).decode() if m else "",
                                                       "speakers": False, "where": []})
                    info["speakers"] |= bool(m)
                    info["where"].append(f"{stage}#{index}")
            self._script_texts = found
        return self._script_texts

    @staticmethod
    def is_ui_text(body: bytes, info: dict) -> bool:
        """a script string that is a menu label, not speech: never spoken by anyone and all
        capitals ("MISSION:", "TEAM ALPHA", ":WEAPONS"); it goes to the Menus tab"""
        letters = gcx.TAG.sub(b"", body)
        return (not info["speakers"] and re.search(rb"[A-Z]", letters) is not None
                and re.search(rb"[a-z]", letters) is None)

    def extract_dialogue(self, progress: ProgressFn = _noop) -> int:
        """Every distinct spoken line in the stage scripts; one translation per English text.
        Menu labels in the scripts go to the Menus tab instead (is_ui_text)."""
        old = {}
        if self.has("dialogue"):
            old = {e["source"]: e.get("target", "") for e in self.read("dialogue")["entries"]}
        found = self.script_texts(progress)
        order = [b for b, info in found.items() if not self.is_ui_text(b, info)]
        out = []
        for n, body in enumerate(order):
            source = gcx.to_text(body)
            info = found[body]
            out.append({"key": f"{info['where'][0].split('#')[0]}:{n:04d}",
                        "speaker": info["speaker"], "where": info["where"],
                        "source": source, "raw": body.hex(), "target": old.get(source, "")})
        self.write("dialogue", {"category": "dialogue", "entries": out})
        return len(out)

    def extract_menus(self, progress: ProgressFn = _noop) -> int:
        """Every distinct UI string: the stages' menu tables (minifont.fcx, where "stage#n")
        and the menu labels in their scripts (where "stage script#n"); one translation each.
        Run it before extract_dialogue: labels translated in the Dialogue tab move over here."""
        old = {}
        for category in ("dialogue", "menus"):
            if self.has(category):
                old.update({e["source"]: e["target"] for e in self.read(category)["entries"]
                            if e.get("target")})
        found: dict[bytes, list[str]] = {}
        for body, info in self.script_texts(progress).items():
            if self.is_ui_text(body, info):
                found.setdefault(body, []).extend(
                    w.replace("#", " script#") for w in info["where"])
        for stage in self.stages():
            blob = dict(dar_parse(zar_read(stage_zar(self.game, stage)))).get(MENU_TABLE)
            if not blob:
                continue
            progress(f"Reading {stage}...")
            for index, raw in gcx.Gcx(blob).texts():
                parts = gcx.split(raw)
                if parts and re.search(rb"[A-Za-z]", parts[1]):     # skip "----" and the like
                    found.setdefault(parts[1], []).append(f"{stage}#{index}")
        out = []
        for n, (body, where) in enumerate(found.items()):
            source = gcx.to_text(body)
            out.append({"key": f"{where[0].split('#')[0].split(' ')[0]}:{n:04d}", "menu": True,
                        "where": where,
                        "source": source, "raw": body.hex(), "target": old.get(source, "")})
        self.write("menus", {"category": "menus", "entries": out})
        return len(out)

    def import_txt(self, folder, progress: ProgressFn = _noop) -> int:
        """Fill targets from <scene>.txt files (one line per caption, '\\n' = break)."""
        doc = self.read("cutscenes")
        by_scene: dict[str, list[dict]] = {}
        for e in doc["entries"]:
            by_scene.setdefault(e["key"].rsplit(":", 1)[0].split("/")[-1].lower(), []).append(e)
        filled = 0
        for path in sorted(Path(folder).glob("*.txt")):
            rows = by_scene.get(path.stem.lower())
            if not rows:
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) != len(rows):
                progress(f"{path.name}: {len(lines)} lines for {len(rows)} captions -- skipped")
                continue
            for e, line in zip(rows, lines):
                if line.strip():
                    e["target"] = line.replace("\\n", "\n")
                    filled += 1
        self.write("cutscenes", doc)
        return filled

    def export_txt(self, folder) -> int:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        by_scene: dict[str, list[str]] = {}
        for e in self.read("cutscenes")["entries"]:
            by_scene.setdefault(e["key"].rsplit(":", 1)[0].replace("/", "__"), []).append(
                e.get("target", "").replace("\n", "\\n"))
        for scene, lines in by_scene.items():
            (folder / f"{scene}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return len(by_scene)

    # -- text files --------------------------------------------------------

    def read(self, category: str) -> dict:
        return json.loads(self.text_of(category).read_text(encoding="utf-8"))

    def write(self, category: str, doc: dict) -> None:
        self.text.mkdir(parents=True, exist_ok=True)
        temp = self.text_of(category).with_suffix(".json.tmp")
        temp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.text_of(category))

    def stats(self, category: str) -> tuple[int, int]:
        if not self.has(category):
            return 0, 0
        rows = self.read(category)["entries"]
        return sum(1 for r in rows if r.get("target")), len(rows)

    # -- font and encoding -------------------------------------------------

    def font(self):
        """(KeepFont with Arabic glyphs, keepfont.row bytes) -- hand-made glyphs included"""
        if self._font is None:
            _, dar = read_cache(self.game, "init")
            orig = dict(dar).get(FONT_NAME)
            if orig is None:
                raise ProjectError(f"{FONT_NAME} not found in stage/init")
            kf, _data = make_font(orig, str(self.font_file))
            self._font = self._with_overrides("caption", kf, orig)
        return self._font

    # -- hand-made glyphs (the font editor) ----------------------------------

    @property
    def edition(self) -> str:
        """which release edition builds make ("edition" in project.json, see EDITIONS)"""
        name = self.manifest.get("edition", DEFAULT_EDITION)
        return name if name in EDITIONS else DEFAULT_EDITION

    def face_dir(self, face: str) -> Path:
        """the edition's folder for this face, or fonts/ when the edition has none of its own"""
        own = self.root / EDITIONS[self.edition][1] / face
        return own if own.is_dir() else self.root / "fonts" / face

    def face_height(self, face: str) -> int:
        _label, kind, size = FACES[face]
        if kind == "row":
            return size
        return size[1] // 3                         # a sheet cell: a third of the sub height

    def overrides(self, face: str) -> dict[int, tuple]:
        """{code: (width, yoff or None, levels)} drawn by hand for this face"""
        try:
            return glyph_png.load(self.face_dir(face), self.face_height(face))
        except ValueError as error:
            raise ProjectError(f"fonts/{face}: {error}") from None

    def _with_overrides(self, face: str, kf, orig: bytes):
        for code, (width, yoff, levels) in self.overrides(face).items():
            if FIRST_CODE <= code <= 0xFF:
                old_yoff = kf.glyphs[code][1]
                kf.glyphs[code] = (width, old_yoff if yoff is None else yoff, list(levels))
        try:
            return kf, kf.build(like=orig)
        except ValueError as error:
            raise ProjectError(f"The {face} font is too big for the game's font file: {error}.\n"
                               "Make some hand-made glyphs narrower.") from None

    def face_glyphs(self, face: str) -> dict[int, tuple[int, int, tuple]]:
        """what the build uses for a face: {code: (width, yoff, levels)}"""
        _label, kind, size = FACES[face]
        if kind == "row":
            kf, _ = self.font() if face == "caption" else self.mini_font()
            return {c: (w, yo, tuple(px)) for c, (w, yo, px) in kf.glyphs.items()}
        shape = size
        if shape not in menu_patch.PARAMS:
            for stage in ("title", "briefing", "r_main01"):    # sheets of all three shapes
                self.menu_stage(stage)
                if shape in menu_patch.PARAMS:
                    break
        self._sheet_overrides()
        cell, top, base, bold, _levels = menu_patch.PARAMS[shape]
        glyphs = menu_patch.face_glyphs(shape[0], cell, top, base, bold)
        return {SLOTS[ch]: (w, 0, lv) for ch, (w, lv) in glyphs.items()}

    def _sheet_overrides(self) -> None:
        """hand the menu sheets' hand-made glyphs to the menu patcher"""
        for face, (_label, kind, shape) in FACES.items():
            if kind == "sheet":
                menu_patch.set_overrides(shape, {
                    FORMS[code]: (w, lv) for code, (w, _y, lv) in self.overrides(face).items()
                    if code in FORMS})

    def set_glyph(self, face: str, code: int, width: int, levels, yoff: int | None = None) -> None:
        glyph_png.save(self.face_dir(face), code, width, self.face_height(face), levels, yoff)
        self.forget_fonts()

    def reset_glyph(self, face: str, code: int) -> None:
        glyph_png.remove(self.face_dir(face), code)
        self.forget_fonts()

    def widths(self) -> dict[int, int]:
        kf, _ = self.font()
        return {c: kf.width(c) for c in kf.glyphs}

    def forget_fonts(self) -> None:
        """after a font setting changes: rebuild every font, encoding and menu glyph on next use"""
        self._font = None
        self._mini = None
        self._menu_fonts.clear()
        self._packed.clear()
        self.__dict__.pop("_enc_cache", None)
        self.__dict__.pop("_pack_sheets", None)

    def mini_font(self):
        """(KeepFont of keepfont_mini.row with Arabic glyphs, its bytes)"""
        if self._mini is None:
            _, dar = read_cache(self.game, "init")
            orig = dict(dar).get(MINI_NAME)
            if orig is None:
                raise ProjectError(f"{MINI_NAME} not found in stage/init")
            kf, _data = make_mini_font(orig, str(self.menu_font_file))
            self._mini = self._with_overrides("small", kf, orig)
        return self._mini

    def mini_widths(self) -> dict[int, int]:
        kf, _ = self.mini_font()
        return {c: kf.width(c) for c in kf.glyphs}

    def menu_stage(self, stage: str) -> list:
        """the stage's top-level entries with Arabic glyphs added to its menu fonts
        (layout sheets + font tables, tools/menu_patch.py); cached for the session"""
        if stage not in self._menu_fonts:
            menu_patch.set_font(self.menu_font_file)
            self._sheet_overrides()
            top = dar_parse(zar_read(stage_zar(self.game, stage)))
            self._menu_fonts[stage] = (top, bool(menu_patch.patch_stage(top)))
        top, changed = self._menu_fonts[stage]
        return [list(e) for e in top], changed

    # -- button icons ({8Cnn} in a line) -----------------------------------------

    def icons(self, category: str, where: list[str]) -> dict[int, tuple[int, list[int]]]:
        """the icons a line can draw, from the script it lives in (each stage script and codec
        block carries its own set): {code: (width, levels)}; {} when there are none"""
        if not where:
            return {}
        src = where[0].split("#")[0]
        cache = self.__dict__.setdefault("_icons", {})
        if (category, src) not in cache:
            found = {}
            try:
                if category == "codec":
                    blob = self.codec_blocks().get(int(src, 16))
                else:
                    stage, _, kind = src.partition(" ")
                    files = dict(dar_parse(zar_read(stage_zar(self.game, stage))))
                    blob = files.get("scenerio.gcx")
                    if category == "menus" and kind != "script":    # menu tables
                        blob = files.get(MENU_TABLE, blob)
                if blob:
                    found = gcx.Gcx(blob).icons()
            except (OSError, ValueError, KeyError, struct.error):
                pass
            cache[(category, src)] = found
        return cache[(category, src)]

    # -- codec ---------------------------------------------------------------

    def codec_blocks(self) -> dict[int, bytes]:
        if getattr(self, "_codec", None) is None:
            self._codec = codecdat.split_blocks(
                (self.game / CODEC_PATH).read_bytes())
        return self._codec

    def codec_refs(self) -> dict[str, list[tuple[int, int]]]:
        """stage -> [(position, block offset)] of every codec reference in its script"""
        if getattr(self, "_codec_refs", None) is None:
            valid = set(self.codec_blocks())
            refs = {}
            for stage in self.stages():
                blob = dict(dar_parse(zar_read(stage_zar(self.game, stage)))).get("scenerio.gcx")
                if blob:
                    found = codecdat.find_refs(blob, valid)
                    if found:
                        refs[stage] = found
            self._codec_refs = refs
        return self._codec_refs

    def codec_pinned(self) -> set[int]:
        referenced = {off for found in self.codec_refs().values() for _, off in found}
        return set(self.codec_blocks()) - referenced

    def extract_codec(self, progress: ProgressFn = _noop) -> int:
        """Every distinct line of every codec call; one translation per English text."""
        old = {}
        if self.has("codec"):
            old = {e["source"]: e.get("target", "") for e in self.read("codec")["entries"]}
        progress("Reading codec.dat and the stage scripts...")
        pinned = self.codec_pinned()
        found: dict[bytes, list[str]] = {}
        for off, blob in sorted(self.codec_blocks().items()):
            for index, raw in gcx.Gcx(blob).texts():
                parts = gcx.split(raw)
                if parts:
                    found.setdefault(parts[1], []).append(f"{off:05X}#{index}")
        out = []
        for n, (body, where) in enumerate(found.items()):
            source = gcx.to_text(body)
            blocks = sorted({int(w.split("#")[0], 16) for w in where})
            out.append({"key": f"{where[0].split('#')[0]}:{n:04d}", "where": where,
                        "pinned": any(b in pinned for b in blocks),
                        "source": source, "raw": body.hex(),
                        "target": old.get(source, "")})
        self.write("codec", {"category": "codec", "entries": out})
        return len(out)

    def codec_new_blocks(self, rows: list[dict] | None = None,
                         problems: list[str] | None = None,
                         keep: set[int] = frozenset(),
                         only: set[int] | None = None,
                         compact: set[int] = frozenset()) -> dict[int, bytes]:
        """Blocks with the current translations applied (unchanged blocks are returned as is)."""
        if rows is None:
            rows = self.read("codec")["entries"] if self.has("codec") else []
        trans: dict[bytes, bytes] = {}
        tight: dict[bytes, bytes] = {}
        for r in rows:
            if r.get("target", "").strip():
                try:
                    trans[bytes.fromhex(r["raw"])] = self.encode(r["target"], "codec")
                    if compact:
                        tight[bytes.fromhex(r["raw"])] = self.encode(r["target"], "codec", True)
                except ValueError as error:
                    if problems is not None:
                        problems.append(f"{r['key']}: {error}")
        new = {}
        for off, blob in self.codec_blocks().items():
            if only is not None and off not in only:
                continue
            if off in keep:
                new[off] = blob
                continue
            script = gcx.Gcx(blob)
            table = tight if off in compact else trans
            hits = 0
            for index, raw in script.texts():
                parts = gcx.split(raw)
                if parts and parts[1] in table:
                    script.items[index] = parts[0] + table[parts[1]] + parts[2]
                    hits += 1
            new[off] = script.build() if hits else blob
        return new

    def codec_fit(self, rows: list[dict], offsets: list[int]) -> list[tuple[int, int, int, bool]]:
        """(block, new size, room, pinned) for the given blocks with these translations"""
        mine = {int(w.split("#")[0], 16) for r in rows for w in r["where"]}
        rows = [r for r in rows if any(int(w.split("#")[0], 16) in offsets for w in r["where"])]
        pinned = self.codec_pinned()
        new = self.codec_new_blocks(rows, only=set(offsets))
        tight = self.codec_new_blocks(rows, only=set(offsets) & pinned,
                                      compact=set(offsets) & pinned)
        new.update({o: b for o, b in tight.items() if len(b) < len(new[o])})
        pins = sorted(pinned)
        out = []
        for off in offsets:
            size = len(codecdat.pad(new[off]))
            if off in pinned:
                k = pins.index(off)
                room = (pins[k + 1] - off) if k + 1 < len(pins) else max(CODEC_WINDOW, size)
            else:
                room = max(CODEC_WINDOW, len(self.codec_blocks()[off]))
            out.append((off, size, room, off in pinned))
        return out

    def codec_plan(self, rows: list[dict] | None = None, problems: list[str] | None = None):
        """Work out codec.dat the way the build does.

        Returns (codec bytes, {old: new offset}, {call: (size, room)} for the calls that
        stay English, set of calls built with compact alignment)."""
        if problems is None:
            problems = []
        keep: set[int] = set()
        compact: set[int] = set()
        over: dict[int, tuple[int, int]] = {}
        while True:
            new_blocks = self.codec_new_blocks(rows, problems=problems, keep=keep,
                                               compact=compact)
            if problems:
                return None, {}, {}, compact
            codec_bytes, moved, overflow = codecdat.layout(
                self.codec_blocks(), new_blocks, self.codec_pinned())
            if not overflow:
                return codec_bytes, moved, over, compact
            for off, size, room in overflow:          # first try tighter alignment
                if off in compact:
                    keep.add(off)
                    over[off] = (size, room)
                else:
                    compact.add(off)

    # -- handing the codec calls that stay English to someone to shorten -----------

    @staticmethod
    def simple_bytes(text: str) -> int:
        """Bytes a line costs in the game, countable by hand: 2 per Arabic letter
        (لا / لأ / لإ / لآ count as one letter, the comma ، too), 1 per space, digit,
        Latin letter, ؟ or other punctuation, 0 for tashkeel.  Padding for alignment is not included."""
        text = arabic_text.normalise(text)
        text = re.sub(r"[ً-ْٰ]", "", text)
        text = re.sub(r"ل[اأإآ]", "ﻻ", text)
        return sum(2 if ("؀" <= ch <= "ۿ" or ch == "ﻻ") and ch != "؟"
                   else 1 for ch in text)

    def kept_report(self, rows: list[dict]) -> list[dict]:
        """The calls that stay English, closest to fitting first, with their lines."""
        _bytes, _moved, over, _compact = self.codec_plan(rows)
        calls = []
        for off, (size, room) in sorted(over.items(), key=lambda kv: kv[1][0] - kv[1][1]):
            mine = [r for r in rows if any(int(w.split("#")[0], 16) == off for w in r["where"])]
            now = sum(self.simple_bytes(r.get("target") or "") for r in mine)
            calls.append({"call": off, "over": size - room, "now": now,
                          "limit": max(0, now - (size - room) - 16),
                          "lines": mine})
        return calls

    def export_kept(self, rows: list[dict], path) -> tuple[int, int]:
        """Write the calls that stay English as a plain-text work sheet; returns (calls, lines)."""
        calls = self.kept_report(rows)
        out = ["# MGS Portable Ops -- codec calls that do not fit (stay English)", "",
               "Each call below is over its space. Shorten its Arabic lines until the call's",
               "total is at or under its LIMIT (counted with the rule in the instructions).",
               ""]
        listed = set()
        for c in calls:
            out.append(f"## CALL {c['call']:05X}  --  now {c['now']} bytes, LIMIT {c['limit']} "
                       f"(cut at least {c['now'] - c['limit']}), {len(c['lines'])} line(s)")
            out.append("")
            for r in c["lines"]:
                others = sorted({w.split("#")[0] for w in r["where"]} - {f"{c['call']:05X}"})
                also = f"  (also used in calls {', '.join(others)})" if others else ""
                again = "  [already listed above -- same line, same text]" if r["key"] in listed else ""
                listed.add(r["key"])
                out.append(f"[{r['key']}] {self.simple_bytes(r.get('target') or '')} bytes{also}{again}")
                out.append("EN: " + r["source"].replace("\n", "\\n"))
                out.append("AR: " + (r.get("target") or "").replace("\n", "\\n"))
                out.append("")
        Path(path).write_text("\n".join(out), encoding="utf-8")
        return len(calls), len(listed)

    @staticmethod
    def parse_reply(text: str) -> dict[str, str]:
        """Lines like  `[174B0:0123] ||| Arabic text`  (brackets optional, \\n = forced break)."""
        out = {}
        for line in text.splitlines():
            m = re.match(r"\s*\[?([0-9A-Fa-f]{5}:\d{4})\]?\s*\|\|\|\s*(.*?)\s*$", line)
            if m and m.group(2):
                out[m.group(1).upper()] = m.group(2).replace("\\n", "\n")
        return out

    def menu_bytes(self, entry: dict) -> int:
        """size of a menu string as the game gets it (English when untranslated)"""
        if entry.get("target", "").strip():
            return len(self.encode(entry["target"], "menus"))
        return len(bytes.fromhex(entry["raw"]))

    def menu_limits(self, entries: list[dict]) -> dict[str, tuple[int, int, str]]:
        """{key: (bytes, limit, why)} for menu strings the game copies into fixed buffers;
        only strings over their limit are listed"""
        by_key = {e["key"]: e for e in entries}
        if MANAGE_ALL not in by_key:
            return {}
        room = MANAGE_BUFFER - 2                                   # the "/" and the NUL
        all_size = self.menu_bytes(by_key[MANAGE_ALL])
        units = {k: self.menu_bytes(by_key[k]) for k in MANAGE_UNITS if k in by_key}
        why = f'MANAGE joins "ALL/<unit>" in a {MANAGE_BUFFER}-byte buffer; longer freezes the list'
        out = {k: (n, room - all_size, why) for k, n in units.items() if n > room - all_size}
        longest = max(units.values(), default=0)
        if all_size > room - longest:
            out[MANAGE_ALL] = (all_size, room - longest, why)
        # REPORT > LOG rows: each location with the longest month
        months = {k: self.menu_bytes(by_key[k]) for k in REPORT_MONTHS if k in by_key}
        places = {k: self.menu_bytes(by_key[k]) for k in REPORT_PLACES if k in by_key}
        why = ("REPORT > LOG builds \"<day> <month> 1970 / <location>\"; a longer row shows "
               f"\"PROGRAM:K.YAMAMURA\" instead ({REPORT_ROW_MAX} bytes is the longest seen to fit)")
        room = REPORT_ROW_MAX - REPORT_FIXED
        month = max(months.values(), default=0)
        out.update({k: (n, room - month, why) for k, n in places.items() if n > room - month})
        return out

    def encode(self, target: str, category: str = "cutscenes", compact: bool = False) -> bytes:
        """compact: align lines to each other instead of padding them to the window edge
        (saves bytes where a codec call has no room to grow)"""
        cache = self.__dict__.setdefault("_enc_cache", {})
        key = (target, category, compact, self.wrap_px)
        if key in cache:
            return cache[key]
        if category == "menus":
            # no wrapping; the game draws menu lines from the left, so a multi-line string
            # is padded to read right-aligned as a block
            out = arabic_text.encode(target, self.mini_widths(), None, align_right_to="block",
                                     brk=b"\n")
        elif category in ("dialogue", "codec"):
            out = arabic_text.encode(target, self.widths(), DIALOGUE_WRAP,
                                     align_right_to="block" if compact else DIALOGUE_RIGHT,
                                     brk=b"\n")
            if category == "dialogue" and self.rtl:
                # reading order + the marker per line: the patched executable draws it from the
                # right edge, so the typewriter reveal runs right to left
                out = rtl_patch.to_rtl(out)
        else:
            out = arabic_text.encode(target, self.widths(), self.wrap_px, align_right_to="block")
        if len(cache) > 20000:
            cache.clear()
        cache[key] = out
        return out

    # -- build -------------------------------------------------------------

    def build(self, progress: ProgressFn = _noop) -> dict[str, bytes]:
        """Write output/PSP_GAME/... and return {iso path: bytes} of every changed file."""
        rows = self.read("cutscenes")["entries"]
        problems = []
        by_file: dict[tuple[str, str], dict[int, bytes]] = {}
        for r in rows:
            if not r.get("target", "").strip():
                continue
            try:
                data = self.encode(r["target"])
            except ValueError as error:
                problems.append(f"{r['key']}: {error}")
                continue
            for stage in r["stages"]:
                by_file.setdefault((stage, r["file"]), {})[r["index"]] = data

        dialogue: dict[bytes, bytes] = {}             # English body bytes -> Arabic bytes
        dlg_stages: set[str] = set()
        if self.has("dialogue"):
            for r in self.read("dialogue")["entries"]:
                if not r.get("target", "").strip():
                    continue
                try:
                    dialogue[bytes.fromhex(r["raw"])] = self.encode(r["target"], "dialogue")
                except ValueError as error:
                    problems.append(f"{r['key']}: {error}")
                    continue
                dlg_stages.update(w.split("#")[0] for w in r["where"])
        menus: dict[bytes, bytes] = {}                # English menu string -> Arabic bytes
        menu_stages: set[str] = set()                 # stages whose menu table changes
        label_stages: set[str] = set()                # stages whose script has translated labels
        if self.has("menus"):
            for r in self.read("menus")["entries"]:
                if not r.get("target", "").strip():
                    continue
                try:
                    menus[bytes.fromhex(r["raw"])] = self.encode(r["target"], "menus")
                except ValueError as error:
                    problems.append(f"{r['key']}: {error}")
                    continue
                for w in r["where"]:
                    stage = w.split("#")[0]
                    if stage.endswith(" script"):
                        label_stages.add(stage.split(" ")[0])
                    else:
                        menu_stages.add(stage)
        # codec.dat: translate the blocks, lay them out, note which moved
        moved: dict[int, int] = {}
        codec_stages: set[str] = set()
        if self.has("codec"):
            progress("Building codec.dat...")
            codec_bytes, moved, over, _compact = self.codec_plan(problems=problems)
            self.codec_kept = over
            for off, (size, room) in sorted(over.items()):
                progress(f"codec call {off:05X} needs {size} bytes, has {room} -- kept in English")
            if not problems:
                moved = {o: n for o, n in moved.items() if o != n}
                codec_stages = {st for st, found in self.codec_refs().items()
                                if any(off in moved for _, off in found)}
                orig = (self.game / CODEC_PATH).read_bytes()
                if not problems and codec_bytes != orig:
                    changed_codec = codec_bytes
                else:
                    changed_codec = None
        if problems:
            raise ProjectError("Some lines cannot be built:\n\n" + "\n".join(problems[:30]))

        _, font_bytes = self.font()
        glyphs = self.has("menus")                    # menu fonts get their Arabic glyphs
        mini_bytes = self.mini_font()[1] if glyphs else None
        changed: dict[str, bytes] = {}
        if self.has("dialogue") and self.rtl:
            if self.rtl_mode == "eboot":
                changed[EBOOT_PATH] = self.patched_eboot()
                dest = self.output / EBOOT_PATH
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(changed[EBOOT_PATH])
            else:                                   # the cheat file, for players and for PPSSPP
                self.cheat_path.parent.mkdir(parents=True, exist_ok=True)
                self.cheat_path.write_text(f"_S {rtl_patch.GAME_ID}\n_G Metal Gear Solid: "
                                           "Portable Ops\n" + rtl_patch.cheat_text(DIALOGUE_RIGHT)
                                           + rtl_patch.fps_cheat_text(),
                                           encoding="utf-8")
        if self.has("codec") and changed_codec is not None:
            changed[CODEC_PATH] = changed_codec
            dest = self.output / CODEC_PATH
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(changed_codec)
        valid = set(self.codec_blocks()) if moved else set()

        pages = self.help_page_files()
        text_stages = (set(FONT_STAGES) | set(CAPTION_STAGES) | dlg_stages | codec_stages
                       | menu_stages | label_stages | ({HELP_STAGE} if pages else set()))
        stages = set(text_stages)
        if glyphs:
            stages |= set(self.stages())
        for stage in sorted(stages, key=natural_key):
            progress(f"Building {stage}...")
            if stage not in text_stages and stage in self._packed:
                blob = self._packed[stage]          # only the menu glyphs: same as last build
                changed[iso_path_of(stage)] = blob
                dest = self.output / iso_path_of(stage)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(blob)
                continue
            if glyphs:
                top, dirty = self.menu_stage(stage)
            else:
                top, _ = read_cache(self.game, stage)
                dirty = False
            for item in top:
                if item[0] == HELP_QAR and stage == HELP_STAGE and pages:
                    qd = item[1]
                    ents = qar_parse(qd)
                    entry = {e[0]: e for e in ents}[HELP_TXP]
                    txp = qd[entry[1]:entry[1] + entry[2]]
                    for n, png in sorted(pages.items()):
                        try:
                            with Image.open(png) as image:
                                txp = txp_image.replace(txp, n, image)
                        except (OSError, ValueError) as error:
                            raise ProjectError(f"{png.name}: {error}") from None
                    item[1] = qar_build(ents, {HELP_TXP: txp}, qd)
                    dirty = True
                    continue
                if item[0] == MENU_TABLE and stage in menu_stages:
                    table = gcx.Gcx(item[1])
                    hits = 0
                    for index, raw in table.texts():
                        parts = gcx.split(raw)
                        if parts and parts[1] in menus:
                            table.items[index] = parts[0] + menus[parts[1]] + parts[2]
                            hits += 1
                    if hits:
                        item[1] = table.build()
                        dirty = True
                    continue
                if item[0] == "scenerio.gcx" and stage in (dlg_stages | codec_stages | label_stages):
                    blob = item[1]
                    if stage in dlg_stages | label_stages:
                        script = gcx.Gcx(blob)
                        hits = 0
                        for index, raw in script.texts():
                            parts = gcx.split(raw)
                            if not parts:
                                continue
                            new = dialogue.get(parts[1])
                            if new is None and stage in label_stages:
                                new = menus.get(parts[1])          # menu labels: no padding
                            if new is not None:
                                script.items[index] = parts[0] + new + parts[2]
                                hits += 1
                        if hits:
                            blob = script.build()
                    if stage in codec_stages:
                        blob, _n = codecdat.patch_refs(blob, valid, moved)
                    if blob != item[1]:
                        item[1] = blob
                        dirty = True
                    continue
                if not item[0].endswith(".dar"):
                    continue
                dar = dar_parse(item[1])
                sub = False
                for c in dar:
                    if c[0] == FONT_NAME and stage in FONT_STAGES:
                        c[1] = font_bytes
                        sub = True
                    elif c[0] == MINI_NAME and stage in FONT_STAGES and mini_bytes:
                        c[1] = mini_bytes
                        sub = True
                    elif (stage, c[0]) in by_file and c[1]:
                        ents = cap.parse(c[1])
                        for i, data in by_file[(stage, c[0])].items():
                            ents[i]["text"] = data
                        c[1] = cap.build(ents)
                        sub = True
                if sub:
                    item[1] = dar_build(dar)
                    dirty = True
            if dirty:
                blob = pack_zar(top)
                if stage not in text_stages:
                    self._packed[stage] = blob
                changed[iso_path_of(stage)] = blob
                dest = self.output / iso_path_of(stage)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(blob)
        if glyphs:
            self.build_texture_pack(progress)        # hi-res menu fonts for PPSSPP
        progress(f"Built {len(changed)} file(s)")
        return changed

    # -- hi-res menu fonts (a PPSSPP texture replacement pack) -------------------

    @property
    def texture_pack(self) -> Path:
        return self.output / "textures" / TEXTURE_GAME

    def build_texture_pack(self, progress: ProgressFn = _noop) -> int:
        """the edition's hi-res pack in output/textures/ULUS10202: the grown menu font sheets at
        4x (sharp pixels, or the Arabic cells redrawn from hires_font); returns how many sheets.
        An edition without a pack removes the folder."""
        kind = EDITIONS[self.edition][2]
        if not self.has("menus") or kind is None:
            if self.texture_pack.exists():
                shutil.rmtree(self.texture_pack)
            return 0
        sheets = self.__dict__.get("_pack_sheets")
        if sheets is None:
            sheets = {}
            for stage in self.stages():
                top, changed = self.menu_stage(stage)
                if changed:
                    progress(f"Hi-res menu fonts: {stage}...")
                    original = dar_parse(zar_read(stage_zar(self.game, stage)))
                    sheets.update(hires_pack.grown_sheets(top, original))
            self._pack_sheets = sheets
        pack = self.texture_pack
        if pack.exists():
            for old in pack.glob("*.png"):
                old.unlink()
        return hires_pack.write_pack(pack, sheets, font=self.hires_font if kind == "ttf" else None)

    @property
    def hires_font(self) -> str | None:
        """TTF the hi-res pack redraws the Arabic menu glyphs from ("hires_font" in
        project.json); None: the hand-made glyphs scaled up with sharp pixels"""
        raw = self.manifest.get("hires_font")
        return str(raw) if raw else None

    def install_texture_pack(self) -> Path | None:
        """copy the pack into PPSSPP (memstick/PSP/TEXTURES/ULUS10202), replacing the PNGs an
        earlier install put there and leaving any others alone; an edition without a pack
        just takes the earlier ones out"""
        exe = self.ppsspp
        if not exe:
            return None
        target = exe.parent / "memstick" / "PSP" / "TEXTURES" / TEXTURE_GAME
        listing = target / PACK_LISTING
        if listing.exists():
            for name in listing.read_text(encoding="utf-8").split():
                (target / name).unlink(missing_ok=True)
            listing.unlink()
        if not (self.texture_pack / "textures.ini").exists():
            return None
        target.mkdir(parents=True, exist_ok=True)
        names = []
        for src in self.texture_pack.iterdir():
            shutil.copy2(src, target / src.name)
            if src.suffix == ".png":
                names.append(src.name)
        listing.write_text("\n".join(names) + "\n", encoding="utf-8")
        return target

    def managed_stages(self) -> list[str]:
        """every stage a build may touch (the test folder resets these to the originals)"""
        out = set(FONT_STAGES) | set(CAPTION_STAGES)
        if self.has("dialogue"):
            for r in self.read("dialogue")["entries"]:
                out.update(w.split("#")[0] for w in r["where"])
        if self.has("codec"):
            out.update(self.codec_refs())
        if self.has("menus"):
            out.update(self.stages())                # every layout gets the menu glyphs
        return sorted(out, key=natural_key)

    def make_test_game(self, changed: dict[str, bytes], progress: ProgressFn = _noop) -> Path:
        """Hard-linked copy of the game with the built files laid over it."""
        dest = self.test_game
        if not (dest / "PSP_GAME").exists():
            progress("Linking a test copy of the game...")
            for src_dir, _dirs, files in os.walk(self.game):
                rel = Path(src_dir).relative_to(self.game)
                (dest / rel).mkdir(parents=True, exist_ok=True)
                for f in files:
                    target = dest / rel / f
                    if not target.exists():
                        try:
                            os.link(Path(src_dir) / f, target)
                        except OSError:
                            shutil.copy2(Path(src_dir) / f, target)
        # every managed stage goes back to the original first, then the build on top
        for stage in self.managed_stages():
            target = dest / iso_path_of(stage)
            if target.exists():
                target.unlink()                    # never write through a hard link
            blob = changed.get(iso_path_of(stage))
            if blob is None:
                os.link(stage_zar(self.game, stage), target)
            else:
                target.write_bytes(blob)
        for rel in (CODEC_PATH, EBOOT_PATH):           # whole files: built one or the original
            target = dest / rel
            if target.exists():
                target.unlink()
            if rel in changed:
                target.write_bytes(changed[rel])
            else:
                os.link(self.game / rel, target)
        return dest

    def make_iso(self, changed: dict[str, bytes], progress: ProgressFn = _noop) -> list[str]:
        if not self.iso or not self.iso.exists():
            raise ProjectError("The original ISO is not set (Settings > Original ISO...)")
        self.output.mkdir(parents=True, exist_ok=True)
        return isopatch.patch_copy(self.iso, self.out_iso, changed, progress)

    def run(self, target: Path) -> None:
        exe = self.ppsspp
        if not exe or not exe.exists():
            raise ProjectError("PPSSPP is not set (Settings > PPSSPP...)")
        subprocess.Popen([str(exe), str(target)], cwd=str(exe.parent))
