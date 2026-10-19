"""The translation window.

One tab per kind of text, a table of lines, and an entry box that takes Arabic
the way it is typed -- logical order, ordinary keyboard, Enter for a forced
line break.  The shaping, reordering, wrapping and font slots happen on build,
never in the project files, so a line can always be re-opened and edited.

The preview above the entry draws the line with the game's own caption font
(with the Arabic glyphs the build installs), centred on the 480-pixel screen,
so what you see there is what the PSP will show.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QImage, QKeySequence, QPixmap, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QFileDialog, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QSizePolicy, QSplitter, QTabBar, QTableView,
    QVBoxLayout, QWidget,
)

from mgspotr import render
from mgspotr.project import CATEGORIES, EDITIONS, LABELS, Project, ProjectError
from mgspotr.qt import theme
from mgspotr.qt.findreplace import FindReplace, find_in, replace_all

ROOT = Path(__file__).resolve().parents[2]          # MGSPO_Arabic
PSP = ROOT.parent                                   # Downloads/psp
DEFAULT_GAME = PSP / "MGSPO"
DEFAULT_ISO = PSP / "Metal Gear Solid - Portable Ops (USA) (PSP) (PSN).iso"
DEFAULT_PPSSPP = PSP / "PPSSPP" / "PPSSPPWindows64.exe"
DEFAULT_PROJECT = ROOT / "project"

SCREEN_W = render.SCREEN_W
KEPT = "#3e2424"          # rows of codec calls that stay English


def one_line(text: str) -> str:
    return text.replace("\n", " / ").strip()


def where_of(row: dict, full: bool = False) -> str:
    """where a line lives, for the label over the English and the key tooltip"""
    if "pinned" in row:                                   # codec
        calls = sorted({w.split("#")[0] for w in row["where"]})
        note = "  ·  fixed position" if row["pinned"] else ""
        if full:
            return "codec call(s): " + ", ".join(calls) + (
                "\nIn a call the game places itself: its translation must fit the call's "
                "current space." if row["pinned"] else "")
        return f"call {calls[0]}" + (f" (+{len(calls) - 1} more)" if len(calls) > 1 else "") + note
    if row.get("menu"):                                    # menus
        places = row["where"]
        if full:
            return "menu table (minifont.fcx)\n" + "\n".join(places[:20]) + (
                f"\n... {len(places) - 20} more" if len(places) > 20 else "")
        return f"menu  ·  {places[0]}" + (f"  (+{len(places) - 1} more)" if len(places) > 1 else "")
    if "where" in row:                                    # dialogue
        places = row["where"]
        speaker = row.get("speaker") or "no speaker"
        if full:
            return f"{speaker}\n" + "\n".join(places[:20]) + (
                f"\n... {len(places) - 20} more" if len(places) > 20 else "")
        return f"{speaker}  ·  {places[0]}" + (
            f"  (+{len(places) - 1} more)" if len(places) > 1 else "")
    text = f"{row['file']}  ·  {', '.join(row['stages'])}  ·  #{row['index']}"
    return text + (f"\ntime {row['start']}-{row['end']}" if full else "")


class LineModel(QAbstractTableModel):
    HEADERS = ("Line", "English", "Arabic")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []
        #: codec calls that stay English in the current build -> (size, room); tinted red
        self.kept: dict[int, tuple[int, int]] = {}

    def in_kept(self, row: dict) -> bool:
        return bool(self.kept) and "pinned" in row and any(
            int(w.split("#")[0], 16) in self.kept for w in row["where"])

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def row_at(self, index: int) -> dict | None:
        return self._rows[index] if 0 <= index < len(self._rows) else None

    def touched(self, index: int) -> None:
        if 0 <= index < len(self._rows):
            self.dataChanged.emit(self.index(index, 0), self.index(index, 2))

    def rowCount(self, _parent=QModelIndex()) -> int:      # noqa: N802
        return len(self._rows)

    def columnCount(self, _parent=QModelIndex()) -> int:   # noqa: N802
        return 3

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        row = self._rows[index.row()]
        column = index.column()
        if role == Qt.DisplayRole:
            if column == 0:
                if self.in_kept(row):
                    calls = [int(w.split("#")[0], 16) for w in row["where"]]
                    call = min((c for c in calls if c in self.kept),
                               key=lambda c: self.kept[c][0] - self.kept[c][1])
                    size, room = self.kept[call]
                    return f"{call:05X}  +{size - room}"
                return row["key"]
            if column == 1:
                return one_line(row["source"])
            return one_line(row.get("target", ""))
        if role == Qt.TextAlignmentRole and column == 2:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.BackgroundRole and self.in_kept(row):
            return QColor(KEPT)
        if role == Qt.BackgroundRole and row.get("target"):
            return QColor(theme.DONE)
        if role == Qt.ToolTipRole and column == 0:
            return where_of(row, full=True)
        return None


class Preview(QLabel):
    """The 480-px game render, scaled by whole steps to fill the pane (never blurred)."""

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._pixmap: QPixmap | None = None

    def set_image(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        self._fit()

    def setText(self, text: str) -> None:          # noqa: N802
        self._pixmap = None
        super().setText(text)

    def resizeEvent(self, event) -> None:          # noqa: N802
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        if self._pixmap is None:
            self.clear()
            return
        w = max(1, self.width())
        step = w // self._pixmap.width()
        if step >= 1:
            scaled = self._pixmap.scaled(self._pixmap.width() * step,
                                         self._pixmap.height() * step,
                                         Qt.KeepAspectRatio, Qt.FastTransformation)
        else:
            scaled = self._pixmap.scaledToWidth(w, Qt.SmoothTransformation)
        self.setFixedHeight(max(90, scaled.height()))
        self.setPixmap(scaled)


class Workbench(QMainWindow):
    def __init__(self, project: Project | None = None):
        super().__init__()
        self.setWindowTitle("MGSPO Arabic Translator")
        self.resize(1320, 800)

        self.project: Project | None = None
        self._data: dict[str, list[dict]] = {}
        self._dirty: set[str] = set()
        self._map: list[int] = []
        self._loading = False
        self._finder: FindReplace | None = None

        self._build()
        if project is not None:
            self.use(project)
        else:
            self._sync()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("&File")
        self._act(file_menu, "New project...", self.new_project, QKeySequence.New)
        self._act(file_menu, "Open project...", self.open_project, QKeySequence.Open)
        file_menu.addSeparator()
        self._act(file_menu, "Save translations", self.save, QKeySequence.Save)
        file_menu.addSeparator()
        self._act(file_menu, "Import translations from .txt folder...", self.import_txt)
        self._act(file_menu, "Export translations to .txt folder...", self.export_txt)
        file_menu.addSeparator()
        self._act(file_menu, "Export codec calls that stay English (for shortening)...",
                  self.export_kept)
        self._act(file_menu, "Import shortened codec lines...", self.import_shortened)
        file_menu.addSeparator()
        self._act(file_menu, "Help pages (export to redraw)...", self.help_pages)
        file_menu.addSeparator()
        self._act(file_menu, "Quit", self.close, QKeySequence.Quit)

        edit_menu = menu.addMenu("&Edit")
        self._act(edit_menu, "Find / Replace...", self.find_replace, QKeySequence.Find)
        self._act(edit_menu, "Next untranslated line", self.next_blank, QKeySequence("Ctrl+J"))
        self._act(edit_menu, "Filter the list", self.focus_search, QKeySequence("Ctrl+L"))
        self._act(edit_menu, "Recheck codec calls that stay English", self.recheck_codec,
                  QKeySequence("Ctrl+R"))
        edit_menu.addSeparator()
        self._act(edit_menu, "Copy English into Arabic", self.copy_source)
        self._act(edit_menu, "Clear this line", self.clear_translation)

        build_menu = menu.addMenu("&Build")
        self._act(build_menu, "Re-extract text from the game", self.re_extract)
        build_menu.addSeparator()
        self._act(build_menu, "Build game files", self.build_files, QKeySequence("Ctrl+B"))
        self._act(build_menu, "Build test folder", self.build_test, QKeySequence("Ctrl+Shift+B"))
        self._act(build_menu, "Build patched ISO...", self.build_iso)
        build_menu.addSeparator()
        self._act(build_menu, "Build test folder and run in PPSSPP", self.run_test,
                  QKeySequence("F5"))
        self._act(build_menu, "Run the patched ISO in PPSSPP", self.run_iso)
        build_menu.addSeparator()
        self._act(build_menu, "Open the output folder", self.open_output)

        settings = menu.addMenu("&Settings")
        self._act(settings, "Extracted game folder...", self.set_game)
        self._act(settings, "Original ISO...", self.set_iso)
        self._act(settings, "PPSSPP executable...", self.set_ppsspp)
        self._act(settings, "Arabic font (TTF)...", self.set_font)
        self._act(settings, "Menu font (TTF)...", self.set_menu_font)
        self._act(settings, "Font editor...", self.font_editor)
        editions = settings.addMenu("Edition")
        self._edition_group = QActionGroup(self)
        self._edition_acts = {}
        for key, (label, _fonts, _pack) in EDITIONS.items():
            act = QAction(label, self, checkable=True)
            act.triggered.connect(lambda _on=False, k=key: self.set_edition(k))
            self._edition_group.addAction(act)
            editions.addAction(act)
            self._edition_acts[key] = act
        self._act(settings, "Wrap width...", self.set_wrap)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 8, 10, 4)

        header = QHBoxLayout()
        self._project_label = QLabel("No project open")
        self._project_label.setProperty("heading", True)
        self._state_label = QLabel("")
        self._state_label.setProperty("muted", True)
        header.addWidget(self._project_label)
        header.addStretch(1)
        header.addWidget(self._state_label)
        outer.addLayout(header)

        self._tabs = QTabBar()
        self._tabs.setExpanding(False)
        for category in CATEGORIES:
            self._tabs.addTab(LABELS[category])
        self._tabs.currentChanged.connect(self._on_tab)
        outer.addWidget(self._tabs)

        panes = QSplitter(Qt.Horizontal)
        outer.addWidget(panes, 1)

        # -- left: the lines
        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 6, 6, 0)
        tools = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search English, Arabic or scene name...")
        self._search.textChanged.connect(self._refilter)
        tools.addWidget(self._search, 1)
        self._only_blank = QCheckBox("Untranslated only")
        self._only_blank.toggled.connect(self._refilter)
        tools.addWidget(self._only_blank)
        self._only_kept = QCheckBox("Stays English")
        self._only_kept.setToolTip(
            "Codec only: show every line of the calls that do not fit and so stay English\n"
            "in the build.  Shorten any line of a call (they all share its space) until it\n"
            "fits.  Lines are grouped by call, the worst first.")
        self._only_kept.toggled.connect(self._on_only_kept)
        tools.addWidget(self._only_kept)
        self._recheck = QPushButton("Recheck")
        self._recheck.setToolTip("Work out again which codec calls stay English (Ctrl+R)")
        self._recheck.clicked.connect(self.recheck_codec)
        tools.addWidget(self._recheck)
        left_box.addLayout(tools)
        self._kept_timer = QTimer(self)
        self._kept_timer.setSingleShot(True)
        self._kept_timer.setInterval(800)
        self._kept_timer.timeout.connect(self._quiet_recheck)
        self._kept_summary = QLabel("")
        self._kept_summary.setProperty("warn", True)
        self._kept_summary.setWordWrap(True)
        left_box.addWidget(self._kept_summary)

        self._model = LineModel(self)
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setWordWrap(False)
        head = self._table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.Interactive)
        head.setSectionResizeMode(1, QHeaderView.Stretch)
        head.setSectionResizeMode(2, QHeaderView.Stretch)
        self._table.setColumnWidth(0, 150)
        self._table.selectionModel().currentRowChanged.connect(self._on_line)
        left_box.addWidget(self._table)
        panes.addWidget(left)

        # -- right: the line being worked on
        right = QWidget()
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(6, 6, 0, 0)

        row = QHBoxLayout()
        row.addWidget(QLabel("As the game draws it"))
        row.addStretch(1)
        self._fit_label = QLabel("")
        self._fit_label.setProperty("muted", True)
        row.addWidget(self._fit_label)
        right_box.addLayout(row)

        self._preview = Preview()
        right_box.addWidget(self._preview)

        row = QHBoxLayout()
        row.addWidget(QLabel("English"))
        row.addStretch(1)
        self._where_label = QLabel("")
        self._where_label.setProperty("muted", True)
        row.addWidget(self._where_label)
        right_box.addLayout(row)
        self._source = QPlainTextEdit()
        self._source.setReadOnly(True)
        self._source.setMaximumHeight(90)
        right_box.addWidget(self._source)

        row = QHBoxLayout()
        row.addWidget(QLabel("Arabic"))
        row.addStretch(1)
        self._warn_label = QLabel("")
        self._warn_label.setProperty("warn", True)
        row.addWidget(self._warn_label)
        right_box.addLayout(row)

        self._entry = QPlainTextEdit()
        self._entry.setLayoutDirection(Qt.RightToLeft)
        option = QTextOption(Qt.AlignRight)
        option.setTextDirection(Qt.RightToLeft)
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self._entry.document().setDefaultTextOption(option)
        self._entry.setStyleSheet("font-size: 15pt;")       # the theme sets 10pt everywhere
        self._entry.textChanged.connect(self._on_edit)
        right_box.addWidget(self._entry, 1)

        hint = QLabel("Lines wrap automatically on build; press Enter only to force a break.")
        hint.setProperty("muted", True)
        right_box.addWidget(hint)

        buttons = QHBoxLayout()
        for label, slot, tip in (
            ("Next blank", self.next_blank, "Ctrl+J"),
            ("Copy English", self.copy_source, ""),
            ("Clear", self.clear_translation, ""),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            if tip:
                button.setToolTip(tip)
            buttons.addWidget(button)
        buttons.addStretch(1)
        run = QPushButton("Test in PPSSPP  (F5)")
        run.clicked.connect(self.run_test)
        buttons.addWidget(run)
        right_box.addLayout(buttons)

        panes.addWidget(right)
        panes.setStretchFactor(0, 3)
        panes.setStretchFactor(1, 2)
        self._status = self.statusBar()

    def _act(self, menu, label: str, slot, shortcut=None) -> QAction:
        action = QAction(label, self)
        action.triggered.connect(slot)
        if shortcut is not None:
            action.setShortcut(shortcut)
        menu.addAction(action)
        return action

    # -- project -----------------------------------------------------------

    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        folder = QFileDialog.getExistingDirectory(self, "New project folder", str(ROOT))
        if not folder:
            return
        game = QFileDialog.getExistingDirectory(
            self, "The extracted game folder (the one holding PSP_GAME)",
            str(DEFAULT_GAME if DEFAULT_GAME.is_dir() else PSP))
        if not game:
            return
        iso = str(DEFAULT_ISO) if DEFAULT_ISO.exists() else None
        ppsspp = str(DEFAULT_PPSSPP) if DEFAULT_PPSSPP.exists() else None
        try:
            project = Project.create(folder, game, iso, ppsspp)
            self._busy("Reading the captions...")
            count = project.extract(self._status.showMessage)
            count += project.extract_menus(self._status.showMessage)
            count += project.extract_dialogue(self._status.showMessage)
            legacy = ROOT / "translation"
            if any(legacy.glob("*.txt")):
                project.import_txt(legacy)
        except (ProjectError, OSError, ValueError) as error:
            self._idle()
            QMessageBox.critical(self, "Could not create project", str(error))
            return
        self._idle()
        self.use(project)
        self.say(f"{count} caption lines read")

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        folder = QFileDialog.getExistingDirectory(self, "Open project folder", str(ROOT))
        if folder:
            self._open(folder)

    def _open(self, folder) -> None:
        try:
            self.use(Project.load(folder))
        except (ProjectError, OSError, ValueError) as error:
            QMessageBox.critical(self, "Could not open project", str(error))

    def use(self, project: Project) -> None:
        self.project = project
        self._edition_acts[project.edition].setChecked(True)
        self._data.clear()
        self._dirty.clear()
        self._kept = None
        self._model.kept = {}
        self._show_kept_summary()
        self._project_label.setText(f"{project.name}  --  {project.root}")
        self._busy("Preparing the Arabic font...")
        try:
            for category in CATEGORIES:                   # tabs added after the project was made
                if not project.has(category) and category == "dialogue":
                    self._status.showMessage("Reading the stage scripts...")
                    QApplication.processEvents()
                    project.extract_dialogue(self._status.showMessage)
                if not project.has(category) and category == "codec":
                    self._status.showMessage("Reading codec.dat...")
                    QApplication.processEvents()
                    project.extract_codec(self._status.showMessage)
                if not project.has(category) and category == "menus":
                    self._status.showMessage("Reading the menu tables...")
                    QApplication.processEvents()
                    project.extract_menus(self._status.showMessage)
                    # menu labels in the scripts (and their translations) moved to Menus
                    project.extract_dialogue(self._status.showMessage)
            project.font()
        except (ProjectError, OSError) as error:
            QMessageBox.warning(self, "Font", f"The preview font could not be built:\n\n{error}")
        finally:
            self._idle()
        self._tabs.setCurrentIndex(0)
        self._on_tab()
        self._sync()

    # -- current tab -------------------------------------------------------

    @property
    def category(self) -> str:
        return CATEGORIES[max(0, self._tabs.currentIndex())]

    def entries_of(self, category: str) -> list[dict]:
        if self.project is None:
            return []
        if category not in self._data:
            if not self.project.has(category):
                return []
            try:
                self._data[category] = self.project.read(category)["entries"]
            except (OSError, ValueError) as error:
                QMessageBox.critical(self, "Could not read text", str(error))
                return []
        return self._data[category]

    def entries(self) -> list[dict]:
        return self.entries_of(self.category)

    def _on_tab(self, _index: int = -1) -> None:
        codec = self.category == "codec"
        for widget in (self._only_kept, self._recheck, self._kept_summary):
            widget.setVisible(codec)
        self._refilter()
        self._sync()

    # -- codec calls that stay English ---------------------------------------

    def _quiet_recheck(self) -> None:
        """after an edit: refresh the red rows and the summary, but leave the list as it is"""
        if self.project is None or self.category != "codec":
            return
        try:
            _bytes, _moved, over, _compact = self.project.codec_plan(self.entries_of("codec"))
        except Exception:
            return
        self._kept = over
        self._model.kept = dict(over)
        self._model.layoutChanged.emit()
        self._show_kept_summary()

    def recheck_codec(self) -> None:
        """Run the build's codec layout on the current (unsaved) lines."""
        if self.project is None or not self.project.has("codec"):
            return
        first = getattr(self.project, "_enc_cache", None) is None
        self._busy("Checking which codec calls fit..." +
                   ("  (the first check takes about half a minute)" if first else ""))
        try:
            _bytes, _moved, over, _compact = self.project.codec_plan(self.entries_of("codec"))
        except Exception as error:                      # a bad line must not kill the window
            self._idle()
            QMessageBox.warning(self, "Codec", f"Could not check the codec calls:\n\n{error}")
            return
        self._idle()
        self._kept = over
        self._model.kept = dict(over)
        self._show_kept_summary()
        if self._only_kept.isChecked():
            self._refilter()
        else:
            self._model.layoutChanged.emit()

    def _show_kept_summary(self) -> None:
        over = getattr(self, "_kept", None)
        if over is None:
            self._kept_summary.setText("")
            return
        if not over:
            self._kept_summary.setText("Every codec call fits -- nothing stays English.")
            return
        worst = sorted(over.items(), key=lambda kv: kv[1][0] - kv[1][1])
        parts = [f"{o:05X} ({s - r} bytes over)" for o, (s, r) in worst]
        self._kept_summary.setText(
            f"{len(over)} codec call(s) stay English: " + ", ".join(parts))

    def _on_only_kept(self, checked: bool) -> None:
        if checked and getattr(self, "_kept", None) is None:
            self.recheck_codec()
            return
        self._refilter()

    def _refilter(self) -> None:
        needle = self._search.text().strip().casefold()
        blanks_only = self._only_blank.isChecked()
        kept_only = self.category == "codec" and self._only_kept.isChecked()
        over = getattr(self, "_kept", None) or {}
        rows, mapping = [], []
        for index, entry in enumerate(self.entries()):
            if blanks_only and entry.get("target"):
                continue
            if kept_only and not self._model.in_kept(entry):
                continue
            if needle and needle not in entry["source"].casefold() \
                    and needle not in entry.get("target", "").casefold() \
                    and needle not in entry["key"].casefold():
                continue
            rows.append(entry)
            mapping.append(index)
        if kept_only:
            # group by the call that stays English, the call closest to fitting first
            def call_of(entry):
                calls = [int(w.split("#")[0], 16) for w in entry["where"]]
                calls = [c for c in calls if c in over]
                best = min(calls, key=lambda c: over[c][0] - over[c][1])
                return (over[best][0] - over[best][1], best)
            order = sorted(range(len(rows)), key=lambda k: (call_of(rows[k]), mapping[k]))
            rows = [rows[k] for k in order]
            mapping = [mapping[k] for k in order]
        self._map = mapping
        self._model.set_rows(rows)
        if rows:
            self._table.selectRow(0)
        else:
            self._show(None)
        self._sync()

    # -- editing -----------------------------------------------------------

    def _current(self) -> dict | None:
        index = self._table.currentIndex()
        return self._model.row_at(index.row()) if index.isValid() else None

    def _on_line(self, index, _previous=None) -> None:
        self._show(self._model.row_at(index.row()) if index.isValid() else None)

    def _show(self, entry: dict | None) -> None:
        self._loading = True
        try:
            self._source.setPlainText(entry["source"] if entry else "")
            self._entry.setPlainText(entry.get("target", "") if entry else "")
            self._where_label.setText(where_of(entry) if entry else "")
        finally:
            self._loading = False
        self._entry.setEnabled(entry is not None)
        self._repreview()

    def _on_edit(self) -> None:
        if self._loading:
            return
        entry = self._current()
        if entry is None:
            return
        entry["target"] = self._entry.toPlainText()
        self._dirty.add(self.category)
        self._model.touched(self._table.currentIndex().row())
        self._repreview()
        self._sync()
        if self.category == "codec" and getattr(self, "_kept", None) is not None:
            self._kept_timer.start()            # quietly re-run the check after a pause

    def _repreview(self) -> None:
        entry = self._current()
        self._warn_label.setText("")
        self._fit_label.setText("")
        if entry is None or self.project is None:
            self._preview.set_image(None)
            return
        target = entry.get("target", "")
        try:
            kf, _ = self.project.font()
            dialogue = self.category in ("dialogue", "codec", "menus")
            if self.category == "menus":                # drawn with the small 10-row font
                kf, _ = self.project.mini_font()
            if target.strip():
                data = self.project.encode(target, self.category)
            elif dialogue:
                data = bytes.fromhex(entry["raw"])
            else:
                data = entry["source"].encode("cp1252", errors="replace").replace(b"\n", b"\x80|")
            if dialogue:
                icons = self.project.icons(self.category, entry.get("where", []))
                image, widths = render.render(kf, data, scale=1, brk=b"\n", align="left",
                                              bg=(8, 8, 8), icons=icons)
            else:
                image, widths = render.render(kf, data, scale=1)
        except ValueError as error:
            self._preview.setText(f"[cannot encode: {error}]")
            self._warn_label.setText("unsupported character")
            return
        except Exception as error:                        # preview is best effort
            self._preview.setText(f"[preview failed: {error}]")
            return

        raw = image.tobytes("raw", "RGB")
        qimage = QImage(raw, image.width, image.height, 3 * image.width, QImage.Format_RGB888)
        self._preview.set_image(QPixmap.fromImage(qimage.copy()))

        widest = max(widths) if widths else 0
        fit = f"{len(widths)} line(s), widest {widest} px of {SCREEN_W}"
        if target.strip():
            fit += f", {len(data)} bytes (English {len(entry['source'].encode('cp1252', 'replace'))})"
        warnings = []
        tip = ""
        if self.category == "codec" and target.strip():
            blocks = sorted({int(w.split("#")[0], 16) for w in entry["where"]})
            fits = self.project.codec_fit(self.entries(), blocks)
            bad = [(o, s, r, p) for o, s, r, p in fits if s > r]
            tip = "\n".join(
                f"call {o:05X}  {'fixed' if p else 'movable'}  {s}/{r} bytes"
                + ("  -- over, stays English" if s > r and p else
                   "  -- over the 16 KB a call can load" if s > r else "")
                for o, s, r, p in fits)
            ok = len(fits) - len(bad)
            if not bad:
                fit += f"  ·  fits in all {len(fits)} call(s)" if len(fits) > 1 else "  ·  fits"
            else:
                worst = ", ".join(f"{o:05X} ({s - r} bytes over)" for o, s, r, _p in bad)
                stays = all(p for _o, _s, _r, p in bad)
                warnings.append(f"{ok} of {len(fits)} calls OK -- {worst} "
                                + ("stays English" if stays else "too big"))
        self._fit_label.setText(fit)
        self._fit_label.setToolTip(tip)
        self._warn_label.setToolTip(tip)
        if widest > SCREEN_W:
            warnings.append("wider than the screen")
        if len(widths) > 3:
            warnings.append(f"{len(widths)} lines")
        if target.strip() and not entry["source"].strip():
            warnings.append("English is empty")
        if self.category == "menus":
            over = self.project.menu_limits(self.entries()).get(entry["key"])
            if over:
                size, limit, why = over
                warnings.append(f"{size} bytes, max {limit}")
                self._warn_label.setToolTip(why)
        self._warn_label.setText(", ".join(warnings))

    def next_blank(self) -> None:
        start = self._table.currentIndex().row() + 1
        for row in list(range(start, self._model.rowCount())) + list(range(0, max(0, start))):
            entry = self._model.row_at(row)
            if entry is not None and not entry.get("target"):
                self._table.selectRow(row)
                self._entry.setFocus()
                return
        self.say("No untranslated lines left here")

    def copy_source(self) -> None:
        entry = self._current()
        if entry is not None:
            self._entry.setPlainText(entry["source"])

    def clear_translation(self) -> None:
        if self._current() is not None:
            self._entry.setPlainText("")

    def focus_search(self) -> None:
        self._search.setFocus()
        self._search.selectAll()

    # -- find and replace (the FindReplace box calls these) -----------------

    def find_replace(self) -> None:
        if self.project is None:
            return
        if self._finder is None:
            self._finder = FindReplace(self, self)
        self._finder.show()
        self._finder.raise_()
        self._finder.activateWindow()
        self._finder.find_field.setFocus()
        self._finder.find_field.selectAll()

    def say(self, message: str) -> None:
        self._status.showMessage(message, 5000)

    def _positions(self) -> list[tuple[str, int]]:
        return [(c, i) for c in CATEGORIES for i in range(len(self.entries_of(c)))]

    def _here(self) -> tuple[str, int] | None:
        row = self._table.currentIndex().row()
        if 0 <= row < len(self._map):
            return self.category, self._map[row]
        return None

    def _goto(self, category: str, index: int) -> None:
        if category != self.category:
            self._tabs.setCurrentIndex(CATEGORIES.index(category))
        if index not in self._map:
            for widget, clear in ((self._search, lambda w: w.clear()),
                                  (self._only_blank, lambda w: w.setChecked(False))):
                blocked = widget.blockSignals(True)
                clear(widget)
                widget.blockSignals(blocked)
            self._refilter()
            self.say("Filter cleared to show the match")
        if index in self._map:
            row = self._map.index(index)
            self._table.selectRow(row)
            self._table.scrollTo(self._model.index(row, 0))

    def _hit(self, entry, needle, match_case, search_english) -> bool:
        if find_in(entry.get("target", ""), needle, match_case) >= 0:
            return True
        return search_english and find_in(entry["source"], needle, match_case) >= 0

    def find_next(self, needle, up=False, match_case=False, search_english=False) -> bool:
        if self.project is None:
            return False
        positions = self._positions()
        here = self._here()
        start = positions.index(here) if here in positions else -1
        order = (positions[:start][::-1] + positions[start + 1:][::-1] if up
                 else positions[start + 1:] + positions[:max(0, start)])
        for category, index in order:
            if self._hit(self.entries_of(category)[index], needle, match_case, search_english):
                self._goto(category, index)
                return True
        return False

    def replace_one(self, needle, repl, up=False, match_case=False, search_english=False) -> None:
        entry = self._current()
        if entry is not None:
            target = entry.get("target", "")
            at = find_in(target, needle, match_case)
            if at >= 0:
                entry["target"] = target[:at] + repl + target[at + len(needle):]
                self._dirty.add(self.category)
                self._model.touched(self._table.currentIndex().row())
                self._show(entry)
                self._sync()
                self.say("Replaced")
        self.find_next(needle, up=up, match_case=match_case, search_english=search_english)

    def _replace_in(self, category, needle, repl, match_case) -> int:
        total = 0
        for entry in self.entries_of(category):
            if entry.get("target"):
                new, count = replace_all(entry["target"], needle, repl, match_case)
                if count:
                    entry["target"] = new
                    total += count
        if total:
            self._dirty.add(category)
        return total

    def replace_all_here(self, needle, repl, match_case) -> int:
        here = self._here()
        count = self._replace_in(self.category, needle, repl, match_case)
        if count:
            self._refilter()
            if here is not None:
                self._goto(*here)
            self._sync()
        return count

    def count_everywhere(self, needle, match_case):
        matches, tabs = 0, 0
        for category in CATEGORIES:
            found = sum(replace_all(e["target"], needle, needle, match_case)[1]
                        for e in self.entries_of(category) if e.get("target"))
            if found:
                matches += found
                tabs += 1
        return (matches, tabs) if matches else None

    def replace_all_everywhere(self, needle, repl, match_case):
        here = self._here()
        matches, tabs = 0, 0
        for category in CATEGORIES:
            count = self._replace_in(category, needle, repl, match_case)
            if count:
                matches += count
                tabs += 1
        if matches:
            self._refilter()
            if here is not None:
                self._goto(*here)
            self._sync()
        return matches, tabs

    # -- saving and building -----------------------------------------------

    def save(self) -> None:
        if self.project is None or not self._dirty:
            self.say("Nothing to save")
            return
        try:
            for category in sorted(self._dirty):
                doc = self.project.read(category)
                doc["entries"] = self._data[category]
                self.project.write(category, doc)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Could not save", str(error))
            return
        self._dirty.clear()
        self.say("Saved")
        self._sync()

    def re_extract(self) -> None:
        if self.project is None:
            return
        if QMessageBox.question(
                self, "Re-extract",
                "Read the English captions again from the game folder.\n\n"
                "Translations are kept where the English line is unchanged.") != QMessageBox.Yes:
            return
        self.save()
        try:
            self._busy("Re-extracting...")
            count = self.project.extract(self._status.showMessage)
            count += self.project.extract_menus(self._status.showMessage)   # before dialogue:
            count += self.project.extract_dialogue(self._status.showMessage)  # labels move over
        except (ProjectError, OSError, ValueError) as error:
            self._idle()
            QMessageBox.critical(self, "Could not re-extract", str(error))
            return
        self._idle()
        self._data.clear()
        self._refilter()
        self.say(f"{count} lines re-extracted")

    def import_txt(self) -> None:
        if self.project is None:
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Folder of <scene>.txt translations", str(ROOT / "translation"))
        if not folder:
            return
        self.save()
        notes: list[str] = []
        try:
            filled = self.project.import_txt(folder, notes.append)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Import", str(error))
            return
        self._data.clear()
        self._refilter()
        QMessageBox.information(self, "Import", f"{filled} line(s) filled in."
                                + ("\n\n" + "\n".join(notes) if notes else ""))

    def export_txt(self) -> None:
        if self.project is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Export to folder", str(ROOT))
        if not folder:
            return
        self.save()
        count = self.project.export_txt(folder)
        self.say(f"{count} scene file(s) written to {folder}")

    def export_kept(self) -> None:
        if self.project is None or not self.project.has("codec"):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save the work sheet", str(ROOT / "handover" / "codec_stays_english.txt"),
            "Text (*.txt)")
        if not path:
            return
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._busy("Checking which codec calls fit...")
        try:
            calls, lines = self.project.export_kept(self.entries_of("codec"), path)
        except Exception as error:
            self._idle()
            QMessageBox.critical(self, "Export", str(error))
            return
        self._idle()
        QMessageBox.information(self, "Export",
                                f"{calls} call(s), {lines} line(s) written to:\n\n{path}")

    def import_shortened(self) -> None:
        """Read `[KEY] ||| Arabic` lines (a chat reply saved as text) into the codec tab."""
        if self.project is None or not self.project.has("codec"):
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Shortened lines", str(ROOT / "handover"), "Text (*.txt *.md);;All files (*)")
        if not path:
            return
        reply = self.project.parse_reply(Path(path).read_text(encoding="utf-8"))
        rows = {r["key"].upper(): r for r in self.entries_of("codec")}
        changed = unknown = 0
        for key, text in reply.items():
            row = rows.get(key)
            if row is None:
                unknown += 1
            elif row.get("target") != text:
                row["target"] = text
                changed += 1
        if changed:
            self._dirty.add("codec")
        if self.category == "codec":
            self._show(self._current())
        self.recheck_codec()
        self._sync()
        still = len(getattr(self, "_kept", None) or {})
        QMessageBox.information(
            self, "Import",
            f"{len(reply)} line(s) read, {changed} changed"
            + (f", {unknown} key(s) not found" if unknown else "") + ".\n\n"
            + (f"{still} call(s) still stay English -- export again for another round."
               if still else "Every codec call fits now.")
            + "\n\nNothing is saved until you press Ctrl+S.")

    def _build_files(self) -> dict | None:
        if self.project is None:
            return None
        self.save()
        try:
            self._busy("Building...")
            changed = self.project.build(self._status.showMessage)
        except (ProjectError, OSError, ValueError) as error:
            self._idle()
            QMessageBox.critical(self, "Build failed", str(error))
            return None
        self._idle()
        return changed

    def build_files(self) -> None:
        changed = self._build_files()
        if changed is not None:
            QMessageBox.information(
                self, "Built",
                "\n".join(sorted(changed)) + f"\n\nWritten to:\n{self.project.output}"
                + self._kept_note())

    def _kept_note(self) -> str:
        """after a build: which codec calls stayed English (also refreshes the red rows)"""
        over = getattr(self.project, "codec_kept", None) or {}
        self._kept = dict(over)
        self._model.kept = dict(over)
        self._model.layoutChanged.emit()
        self._show_kept_summary()
        note = self._limit_note()
        if not over:
            return note
        return (f"\n\n{len(over)} codec call(s) stayed English (no room): "
                + ", ".join(f"{o:05X}" for o in sorted(over))
                + "\nUse 'Stays English' on the Codec tab to find their lines." + note)

    def _limit_note(self) -> str:
        """after a build: menu strings longer than the game's buffer for them"""
        try:
            over = self.project.menu_limits(self.entries_of("menus"))
        except (ProjectError, ValueError):
            return ""
        if not over:
            return ""
        entries = {e["key"]: e for e in self.entries_of("menus")}
        lines = [f"  {entries[k]['source']!r}: {n} bytes, max {limit}"
                 for k, (n, limit, _why) in sorted(over.items())]
        return ("\n\nMenu strings too long (they overflow the game's buffer and freeze the screen):\n"
                + "\n".join(lines))

    def build_test(self, quiet: bool = False) -> Path | None:
        changed = self._build_files()
        if changed is None:
            return None
        try:
            self._busy("Updating the test folder...")
            folder = self.project.make_test_game(changed, self._status.showMessage)
            cheat = self.project.install_cheat()     # right-to-left dialogue reveal
            self.project.install_texture_pack()      # hi-res menu fonts
        except (ProjectError, OSError) as error:
            self._idle()
            QMessageBox.critical(self, "Test folder", str(error))
            return None
        self._idle()
        if not quiet:
            note = (f"\n\nThe right-to-left dialogue cheat is in {cheat} "
                    "(PPSSPP: Settings > System > Enable cheats)." if cheat else "")
            QMessageBox.information(
                self, "Test folder",
                f"Ready to boot as a folder in PPSSPP:\n\n{folder}\n\n"
                "The caption font loads when the game boots, so restart the game "
                "after rebuilding -- a save state keeps the old font." + note + self._kept_note())
        return folder

    def build_iso(self) -> None:
        if self.project is None:
            return
        changed = self._build_files()
        if changed is None:
            return
        try:
            self._busy("Writing the ISO (copying ~1.4 GB)...")
            notes = self.project.make_iso(changed, self._status.showMessage)
        except (ProjectError, OSError) as error:
            self._idle()
            QMessageBox.critical(self, "ISO", str(error))
            return
        self._idle()
        QMessageBox.information(self, "ISO", "\n".join(notes) + f"\n\n{self.project.out_iso}"
                                + self._kept_note())

    def run_test(self) -> None:
        folder = self.build_test(quiet=True)
        if folder is None:
            return
        try:
            self.project.run(folder)
            self.say("PPSSPP started on the test folder")
        except ProjectError as error:
            QMessageBox.warning(self, "PPSSPP", str(error))

    def run_iso(self) -> None:
        if self.project is None:
            return
        if not self.project.out_iso.exists():
            QMessageBox.warning(self, "PPSSPP", "Build the patched ISO first.")
            return
        try:
            self.project.run(self.project.out_iso)
        except ProjectError as error:
            QMessageBox.warning(self, "PPSSPP", str(error))

    def help_pages(self) -> None:
        """export the briefing help pages (pictures) and open the folder to redraw them"""
        if self.project is None:
            return
        try:
            originals = self.project.export_help_pages()
        except (ProjectError, OSError, KeyError) as error:
            QMessageBox.critical(self, "Help pages", str(error))
            return
        done = sorted(self.project.help_page_files())
        QMessageBox.information(
            self, "Help pages",
            f"The six briefing help pages are in\n{originals}\n\n"
            f"Save your Arabic versions in\n{self.project.images}\n"
            "as brfhelp_0.png ... brfhelp_5.png (480 x 272, keep the transparency).\n"
            "Pages 0, 1, 3, 4, 5 are reduced to 16 colours on build, page 2 to 256.\n\n"
            + (f"Redrawn so far: {', '.join(map(str, done))}" if done else "None redrawn yet."))
        import os
        os.startfile(self.project.images)                 # noqa: S606 - Windows only tool

    def open_output(self) -> None:
        if self.project is not None:
            self.project.output.mkdir(parents=True, exist_ok=True)
            import os
            os.startfile(self.project.output)            # noqa: S606 - Windows only tool

    # -- settings ----------------------------------------------------------

    def _set(self, key: str, value: str | int) -> None:
        self.project.manifest[key] = value
        self.project.save()
        self.say(f"{key} set")

    def set_game(self) -> None:
        if self.project is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Extracted game folder",
                                                  str(self.project.game))
        if folder:
            self._set("game", folder)

    def set_iso(self) -> None:
        if self.project is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Original ISO", str(PSP), "ISO (*.iso)")
        if path:
            self._set("iso", path)

    def set_ppsspp(self) -> None:
        if self.project is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "PPSSPP", str(PSP), "Programs (*.exe)")
        if path:
            self._set("ppsspp", path)

    def set_font(self) -> None:
        if self.project is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Arabic TTF/OTF", str(ROOT / "fonts"),
                                              "Fonts (*.ttf *.otf)")
        if path:
            self._set("font", path)
            self.project.forget_fonts()
            self._repreview()

    def font_editor(self) -> None:
        """every glyph of every face, editable by hand (saved in the project's fonts/ folder)"""
        if self.project is None:
            return
        from mgspotr.qt.fonteditor import FontEditor
        self._busy("Preparing the fonts...")
        try:
            dialog = FontEditor(self.project, self)
        except (ProjectError, OSError) as error:
            self._idle()
            QMessageBox.critical(self, "Font editor", str(error))
            return
        self._idle()
        dialog.exec()
        self.project.forget_fonts()
        self._repreview()

    def set_menu_font(self) -> None:
        """TTF for the menu glyphs (layout sheets + small font); empty = the caption font"""
        if self.project is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Arabic TTF/OTF for the menus",
                                              str(ROOT / "fonts"), "Fonts (*.ttf *.otf)")
        if path:
            self._set("menu_font", path)
            self.project.forget_fonts()
            self._repreview()

    def set_edition(self, key: str) -> None:
        """which release edition the builds and the test folder make (menu glyph set + hi-res
        pack); the font editor edits that edition's glyphs"""
        if self.project is None:
            return
        self._set("edition", key)
        self.project.forget_fonts()
        self._repreview()
        self.say(f"Edition: {EDITIONS[key][0]} -- rebuild to apply")

    def set_wrap(self) -> None:
        if self.project is None:
            return
        value, ok = QInputDialog.getInt(self, "Wrap width",
                                        "Wrap Arabic captions at (pixels, screen is 480):",
                                        self.project.wrap_px, 120, SCREEN_W)
        if ok:
            self._set("wrap_px", value)
            self._repreview()

    # -- chrome ------------------------------------------------------------

    def _sync(self) -> None:
        if self.project is None:
            self._state_label.setText("")
            self._tabs.setEnabled(False)
            return
        self._tabs.setEnabled(True)
        for index, category in enumerate(CATEGORIES):
            rows = self._data.get(category)
            if rows is not None:
                done, total = sum(1 for r in rows if r.get("target")), len(rows)
            else:
                done, total = self.project.stats(category)
            label = LABELS[category]
            self._tabs.setTabText(index, f"{label}  {done}/{total}" if total else f"{label}  --")
        rows = self.entries()
        done = sum(1 for r in rows if r.get("target"))
        shown = self._model.rowCount()
        state = f"{done} of {len(rows)} translated"
        if shown != len(rows):
            state += f"   ({shown} shown)"
        if self._dirty:
            state += "   * unsaved"
        self._state_label.setText(state)
        star = "*" if self._dirty else ""
        self.setWindowTitle(f"{star}MGSPO Arabic Translator -- {self.project.name}")

    def _busy(self, message: str) -> None:
        self._status.showMessage(message)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()

    def _idle(self) -> None:
        QApplication.restoreOverrideCursor()
        self._status.clearMessage()

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(self, "Unsaved translations",
                                      "Save the current translations first?",
                                      QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Yes:
            self.save()
        return True

    def closeEvent(self, event) -> None:      # noqa: N802
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    app = QApplication(sys.argv[:1])
    theme.apply(app)

    project = None
    target = argv[0] if argv else (DEFAULT_PROJECT if (DEFAULT_PROJECT / "project.json").exists()
                                   else None)
    if target:
        try:
            project = Project.load(target)
        except (ProjectError, OSError, ValueError) as error:
            print(f"Could not open {target}: {error}", file=sys.stderr)

    window = Workbench(project)
    window.show()
    return app.exec()
