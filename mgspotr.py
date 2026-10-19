#!/usr/bin/env python3
"""mgspotr -- Arabic translation workbench for Metal Gear Solid: Portable Ops (PSP).

    python mgspotr.py                  open the workbench (on ./project if it exists)
    python mgspotr.py <project>        open it on a project folder

Translations are typed as ordinary Arabic and stored that way.  The conversion
to the game's caption font slots (presentation forms, visual order, wrapping)
happens when the game files are built.

    pip install PySide6 arabic-reshaper python-bidi pillow
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))      # archive, cap, keepfont, arabic_text, build_font


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    from mgspotr.qt import AVAILABLE
    if not AVAILABLE:
        print("The workbench needs PySide6:\n\n    pip install PySide6\n", file=sys.stderr)
        return 1

    from mgspotr.qt.workbench import main as workbench_main
    return workbench_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
