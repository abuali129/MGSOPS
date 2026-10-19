"""Font editor: every glyph of every face the mod draws, editable by hand as PNGs.

One tab per face (caption font, small font, the three menu sheets).  Click a glyph to see it
large; export it, redraw it in any paint program (4 greys on black, full face height), import
it back.  Imported glyphs are saved in the project (fonts/<face>/<code>.png) and win over the
generated ones on every build; "Back to generated" drops the hand-made one.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QScrollArea, QSpinBox, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

import glyph_png
from mgspotr.project import FACES, FORMS, Project, ProjectError

COLUMNS = 16
CELL = 52            # grid button size
PREVIEW = 200        # detail preview box
SELECTED = "border: 2px solid #ffaa00;"
MADE = "border: 2px solid #44aaff;"


def glyph_qimage(width: int, height: int, levels) -> QImage:
    data = bytes(glyph_png.GREYS[v] for v in levels)
    return QImage(data, width, height, width, QImage.Format_Grayscale8).copy()


def char_of(face_kind: str, code: int) -> str:
    if code in FORMS:
        return FORMS[code]
    if face_kind == "row":
        return bytes([code]).decode("cp1252", errors="replace")
    return ""


class FontEditor(QDialog):
    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Font Editor")
        self._fit_screen(parent)
        self._face = next(iter(FACES))
        self._code = -1
        self._glyphs: dict[str, dict] = {}            # face -> {code: (w, yoff, levels)}
        self._made: dict[str, set] = {}               # face -> codes drawn by hand
        self._buttons: dict[str, dict[int, QPushButton]] = {}
        self._build()
        self._load_face(self._face)

    def _fit_screen(self, parent) -> None:
        """open at most 1180 x 760, but never bigger than the screen (display scaling at 150%
        makes 760 rows taller than a laptop screen, hiding the buttons at the bottom)"""
        screen = (parent.screen() if parent is not None else None) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        width = min(1180, int(area.width() * 0.92))
        height = min(760, int(area.height() * 0.88))
        self.setMinimumSize(min(640, width), min(420, height))
        self.resize(width, height)
        self.move(area.x() + (area.width() - width) // 2, area.y() + (area.height() - height) // 2)

    # -- layout ------------------------------------------------------------

    def _build(self) -> None:
        main = QVBoxLayout(self)
        top = QHBoxLayout()
        self._info = QLabel("")
        top.addWidget(QLabel("Hand-made glyphs (blue) are kept in the project and win over the "
                             "generated ones on every build."))
        top.addStretch()
        top.addWidget(self._info)
        main.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        self._tabs = QTabWidget()
        self._grids: dict[str, QGridLayout] = {}
        for face, (label, _kind, _size) in FACES.items():
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            inner = QWidget()
            grid = QGridLayout(inner)
            grid.setSpacing(2)
            grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            scroll.setWidget(inner)
            self._tabs.addTab(scroll, label)
            self._grids[face] = grid
        self._tabs.currentChanged.connect(lambda i: self._load_face(list(FACES)[i]))
        split.addWidget(self._tabs)

        side = QWidget()
        side.setFixedWidth(260)
        col = QVBoxLayout(side)
        self._preview = QLabel()
        self._preview.setFixedSize(PREVIEW, PREVIEW)
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setStyleSheet("border: 1px solid #555; background: black;")
        col.addWidget(self._preview, alignment=Qt.AlignCenter)
        self._slot = QLabel("Code: --")
        self._char = QLabel("Char: --")
        self._char.setStyleSheet("font-size: 18px;")
        self._state = QLabel("")
        for w in (self._slot, self._char, self._state):
            col.addWidget(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("Width:"))
        self._width = QSpinBox()
        self._width.setRange(1, 64)
        self._width.valueChanged.connect(self._on_width)
        row.addWidget(self._width)
        row.addStretch()
        col.addLayout(row)
        row = QHBoxLayout()
        self._yoff_label = QLabel("Y offset:")
        row.addWidget(self._yoff_label)
        self._yoff = QSpinBox()
        self._yoff.setRange(0, 15)
        self._yoff.valueChanged.connect(self._on_yoff)
        row.addWidget(self._yoff)
        row.addStretch()
        col.addLayout(row)
        for text, slot in (("Import PNG...", self._import_one), ("Export PNG...", self._export_one),
                           ("Back to generated", self._reset_one)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            col.addWidget(b)
        col.addStretch()
        split.addWidget(side)
        split.setStretchFactor(0, 1)
        main.addWidget(split, 1)

        bottom = QHBoxLayout()
        for text, slot in (("Export All Glyphs...", self._export_all),
                           ("Import Glyphs from Folder...", self._import_folder)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            bottom.addWidget(b)
        bottom.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        main.addLayout(bottom)

    # -- data --------------------------------------------------------------

    @property
    def kind(self) -> str:
        return FACES[self._face][1]

    def _load_face(self, face: str) -> None:
        self._face = face
        try:
            self._glyphs[face] = self.project.face_glyphs(face)
            self._made[face] = set(self.project.overrides(face))
        except (ProjectError, OSError) as error:
            QMessageBox.critical(self, "Font", str(error))
            return
        if face not in self._buttons:
            buttons = {}
            for n, code in enumerate(sorted(self._glyphs[face])):
                b = QPushButton()
                b.setFixedSize(CELL, CELL)
                b.clicked.connect(lambda _c=False, c=code: self._select(c))
                self._grids[face].addWidget(b, n // COLUMNS, n % COLUMNS)
                buttons[code] = b
            self._buttons[face] = buttons
        for code in self._buttons[face]:
            self._paint(code)
        self._info.setText(f"{len(self._glyphs[face])} glyphs  ·  {self.project.face_height(face)} px tall"
                           f"  ·  {len(self._made[face])} hand-made")
        is_row = self.kind == "row"
        self._yoff.setVisible(is_row)
        self._yoff_label.setVisible(is_row)
        codes = sorted(self._glyphs[face])
        self._select(self._code if self._code in self._glyphs[face] else codes[0])

    def _paint(self, code: int) -> None:
        w, _yo, lv = self._glyphs[self._face][code]
        h = self.project.face_height(self._face)
        img = glyph_qimage(w, h, lv)
        size = CELL - 8
        pix = QPixmap.fromImage(img.scaled(size, size, Qt.KeepAspectRatio, Qt.FastTransformation))
        b = self._buttons[self._face][code]
        b.setIcon(QIcon(pix))
        b.setIconSize(QSize(size, size))
        b.setToolTip(f"{code:02X}  {char_of(self.kind, code)}  w={w}")
        b.setStyleSheet(SELECTED if code == self._code else MADE if code in self._made[self._face] else "")

    def _select(self, code: int) -> None:
        old, self._code = self._code, code
        if old in self._buttons.get(self._face, {}):
            self._paint(old)
        self._paint(code)
        w, yo, lv = self._glyphs[self._face][code]
        h = self.project.face_height(self._face)
        img = glyph_qimage(w, h, lv)
        self._preview.setPixmap(QPixmap.fromImage(
            img.scaled(PREVIEW - 8, PREVIEW - 8, Qt.KeepAspectRatio, Qt.FastTransformation)))
        self._slot.setText(f"Code: {code:02X}" + (f"   (\\x80\\x{code:02X})" if code >= 0x80 else ""))
        self._char.setText(f"Char: {char_of(self.kind, code)}")
        self._state.setText("hand-made" if code in self._made[self._face] else "generated")
        for spin, value in ((self._width, w), (self._yoff, yo)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _store(self, code: int, width: int, levels, yoff=None) -> None:
        try:
            self.project.set_glyph(self._face, code, width, levels,
                                   yoff if self.kind == "row" else None)
            self._load_face(self._face)                # rebuilds the face (checks it still fits)
        except (ProjectError, OSError, ValueError) as error:
            QMessageBox.warning(self, "Font", str(error))
        self._select(code)

    # -- edits -------------------------------------------------------------

    def _on_width(self, width: int) -> None:
        if self._code < 0:
            return
        w, yo, lv = self._glyphs[self._face][self._code]
        if width == w:
            return
        h = self.project.face_height(self._face)
        rows = [list(lv[r * w:(r + 1) * w]) for r in range(h)]
        rows = [(r + [0] * width)[:width] for r in rows]        # crop or pad on the right
        self._store(self._code, width, [v for r in rows for v in r], yo)

    def _on_yoff(self, yoff: int) -> None:
        if self._code < 0 or self.kind != "row":
            return
        w, yo, lv = self._glyphs[self._face][self._code]
        if yoff != yo:
            self._store(self._code, w, lv, yoff)

    def _import_one(self) -> None:
        if self._code < 0:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Glyph PNG", "", "PNG (*.png)")
        if not path:
            return
        try:
            with Image.open(path) as img:
                width, levels = glyph_png.from_image(img, self.project.face_height(self._face))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Import", str(error))
            return
        self._store(self._code, width, levels, self._glyphs[self._face][self._code][1])

    def _export_one(self) -> None:
        if self._code < 0:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Glyph PNG", f"{self._code:02X}.png", "PNG (*.png)")
        if path:
            w, _yo, lv = self._glyphs[self._face][self._code]
            glyph_png.to_image(w, self.project.face_height(self._face), lv).save(path)

    def _reset_one(self) -> None:
        if self._code < 0 or self._code not in self._made[self._face]:
            return
        self.project.reset_glyph(self._face, self._code)
        self._load_face(self._face)

    def _export_all(self) -> None:
        """every face, one subfolder each (caption/, small/, menu_heading/ ...), <code>.png inside"""
        folder = QFileDialog.getExistingDirectory(self, "Export all glyphs (one subfolder per face)")
        if not folder:
            return
        total = 0
        for face in FACES:
            try:
                glyphs = self._glyphs.get(face) or self.project.face_glyphs(face)
            except (ProjectError, OSError) as error:
                QMessageBox.warning(self, "Export", f"{face}: {error}")
                continue
            h = self.project.face_height(face)
            out = Path(folder) / face
            out.mkdir(parents=True, exist_ok=True)
            for code, (w, _yo, lv) in glyphs.items():
                glyph_png.to_image(w, h, lv).save(out / f"{code:02X}.png")
            total += len(glyphs)
        QMessageBox.information(
            self, "Export", f"{total} glyphs written to\n{folder}\n\n"
            "One subfolder per face; files are named by glyph code (hex). Edit any of them and "
            "import the folder: only the glyphs you changed become hand-made.")

    def _import_face(self, face: str, folder: Path, problems: list) -> int:
        glyphs = self._glyphs.get(face) or self.project.face_glyphs(face)
        h = self.project.face_height(face)
        row = FACES[face][1] == "row"
        changed = 0
        for png in sorted(folder.glob("*.png")):
            try:
                code = int(png.stem, 16)
            except ValueError:
                continue
            if code not in glyphs:
                continue
            try:
                with Image.open(png) as img:
                    width, levels = glyph_png.from_image(img, h)
            except (OSError, ValueError) as error:
                problems.append(f"{face}/{png.name}: {error}")
                continue
            w, yo, lv = glyphs[code]
            if (width, tuple(levels)) == (w, tuple(lv)):
                continue                                   # unchanged: stays as it is
            glyph_png.save(self.project.face_dir(face), code, width, h, levels, yo if row else None)
            changed += 1
        return changed

    def _import_folder(self) -> None:
        """a folder with face subfolders (as exported) -> every face; a plain folder of
        <code>.png -> the face of the open tab"""
        folder = QFileDialog.getExistingDirectory(self, "Import glyphs from folder")
        if not folder:
            return
        folder = Path(folder)
        faces = [f for f in FACES if (folder / f).is_dir()]
        targets = [(f, folder / f) for f in faces] or [(self._face, folder)]
        counts, problems = {}, []
        for face, path in targets:
            try:
                counts[face] = self._import_face(face, path, problems)
            except (ProjectError, OSError) as error:
                problems.append(f"{face}: {error}")
        self.project.forget_fonts()
        self._glyphs.clear()
        try:
            self._load_face(self._face)
        except ProjectError as error:
            problems.append(str(error))
        lines = [f"{FACES[f][0]}: {n} glyph(s) changed" for f, n in counts.items()]
        QMessageBox.information(self, "Import", "\n".join(lines)
                                + ("\n\n" + "\n".join(problems[:20]) if problems else ""))
