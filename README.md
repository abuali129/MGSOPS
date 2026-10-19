# mgspotr — Arabic translation workbench for Metal Gear Solid: Portable Ops (PSP)

```bash
pip install -r requirements.txt
python mgspotr.py              # opens ./project if it exists
python mgspotr.py <project>    # or a given project folder
```

## What it does

**Cutscene subtitles.** The 98 caption files (1,643 lines) in `stage/flashdemo` and
`stage/ending` are read into a project: English on the left, Arabic typed on the right.
Arabic is stored as ordinary text; shaping, right-to-left ordering, wrapping and the
font slots happen on build.

**The font.** Captions are drawn from `keepfont.row`, a 2-bit bitmap font with
Windows-1252 slots. The build renders the Arabic presentation forms from the chosen TTF
(SF Arabic Rounded by default, `Settings > Arabic font`) into slots 0x80–0xFD (؟ in 0x7F)
and installs it in all four copies the game carries (`init`, `r_main01`, `r_main02`,
`r_net01`).

**The preview** draws each line with that font, centred on the 480-pixel screen, and
warns when a line is wider than the screen or runs past three lines.

**Menus.** The Menus tab holds the game's UI text: every string of the stages' menu
tables (`minifont.fcx`: menu items, footers, item and skill descriptions) and the menu
labels in the stage scripts (headings such as `MISSION:`, briefing reports — script
strings that no character speaks and that are all capitals). Lines are not wrapped
(Enter breaks a line) and a multi-line string is aligned right as a block. The build gives
every menu font its Arabic glyphs, from `Settings > Menu font` (the caption font by default):

* the layout sheets (`lay_*.la2` + `lay_*_0.txp`, all stages) — bold headings, menu items,
  small labels (`tools/menu_patch.py`);
* `keepfont_mini.row`, the small 10-row font for footers and descriptions, in the same four
  copies as the caption font.

Some menu strings are copied into fixed buffers by the game. MANAGE joins "ALL/<unit>" in
32 bytes (each Arabic letter takes 2), so a unit name may take at most 30 minus the size of
ALL — 22 bytes with الكل. Longer names freeze the soldier list. The Menus tab warns
("N bytes, max M") and the build lists any such string.

The first build after opening the project patches every layout (about a minute); later
builds reuse it.

**Right-to-left dialogue reveal.** Briefing talk and in-game dialogue appear one letter at
a time in the order the bytes are stored. Dialogue lines are therefore built in reading
order, each starting with the marker `\x80\xFE`, and a small patch to the game's glyph
drawer draws marked lines from the right edge (`tools/rtl_patch.py`), so the text grows
from the right. The patch ships as a **cheat**: the build writes
`output/cheats/ULUS10202.ini`, and *Build test folder* adds it to PPSSPP's
`memstick/PSP/Cheats/ULUS10202.ini` (cheats must be enabled). Players copy that file into
their PPSSPP cheats folder (or use it with CWCheat on a PSP). Without the cheat, dialogue
lines show reversed. The cheat also moves button icons ({8C02} …) with the
text. The file also lists **60FPS v3 [Ingame only]** — a community cheat from the PPSSPP forum
(`tools/fps60_ULUS10202.txt`). It brings gameplay to 60 fps with the game's speed, wall-sneak
and grenade timers re-timed; menus, the codec and cutscenes keep their own rate. Off by
default for players; it needs buffered rendering on.
`"rtl_mode": "eboot"` in `project.json` builds it into a decrypted
`EBOOT.BIN` instead (needs PPSSPP's dump of the executable).

**Font editor.** `Settings > Font editor` shows every glyph of the five faces the mod draws:
the caption font, the small font and the three menu sheets (headings, items, labels). Click
a glyph to see it large; export it as a PNG (4 greys on black, the face's full height),
redraw it, import it back; change its width or (caption/small) its vertical offset; *Back to
generated* drops the hand-made one. Hand-made glyphs are saved in `project/fonts/<face>/`
and replace the generated ones on every build, whatever TTF is chosen.

**Editions.** `Settings > Edition` picks what a build makes (one at a time):

| Edition | Menu glyphs in the game | Hi-res pack (`output/textures/ULUS10202/`) |
|---|---|---|
| PSP / native resolution | hand-made pixel glyphs (`project/fonts/`) | none |
| PPSSPP hi-res: pixel glyphs | the same | the menu sheets at 4x, sharp pixels |
| PPSSPP hi-res: SF Arabic Rounded menus | `project/fonts_sf/` (headings, items, labels) | Arabic redrawn at 4x from `hires_font` |

The small font and the caption font come from `project/fonts/` in every edition. The font
editor edits the chosen edition's glyphs. Rebuild after switching.

**Hi-res menu fonts.** Every build also writes `output/textures/ULUS10202/`, a PPSSPP texture
replacement pack: the grown menu font sheets (headings, items, labels with your hand-made
Arabic glyphs) at 4x with sharp pixels, named by PPSSPP's hash. With `"hires_font": "<ttf>"` in `project.json` the Arabic cells
are drawn again from that TTF at full resolution (smooth) instead (`tools/hires_pack.py`;
`textures.ini` uses `hash = xxh64`, `ignoreAddress = true`, so five PNGs serve every stage).
*Build test folder* copies it into PPSSPP's `memstick/PSP/TEXTURES/ULUS10202/` (Settings >
Tools > Developer: *Replace textures* on). Players copy the folder to the same place. Text drawn
on the fly — captions, dialogue, codec, the small font — can't be replaced this way (a new
picture every line); PPSSPP's *Texture upscaling* (xBRZ) is what smooths those.

**Help pages.** The briefing help screens are pictures (six 480 × 272 images in
`briefing/brfhelp.txp`). `File > Help pages` exports them to `project/images/originals/`;
redraw them in Arabic and save them as `project/images/brfhelp_0.png` … `brfhelp_5.png`
(same size, keep the transparency). The build reduces each to its palette size (16 colours
for pages 0, 1, 3, 4, 5; 256 for page 2) and puts it back in place. Pages you have not
redrawn stay English.

## Building

| Build menu | Result |
|---|---|
| Build game files (Ctrl+B) | `project/output/PSP_GAME/...` — only the changed `_zar` files |
| Build test folder (Ctrl+Shift+B) | `project/test_game` — hard-linked copy of the game with the build on top; boot it as a folder in PPSSPP |
| Build test folder and run (F5) | the above, then starts PPSSPP on it |
| Build patched ISO | copies the original ISO and swaps in the changed files (in place when they fit, appended otherwise) |

The extracted game folder and the original ISO are only ever read.

The font is loaded when the game boots: after a rebuild restart the game — a PPSSPP
save state still holds the old font.

## Text rules

* Enter in the Arabic box forces a line break; otherwise lines wrap at the
  project's wrap width (`Settings > Wrap width`, default 400 px).
* Latin words (FOX, CIA, Mk22) and digits can be mixed in.
* Tashkeel is dropped (no font slots for it); ؛ is written as `;`.
* `File > Import/Export translations` reads/writes one `<scene>.txt` per caption
  file, one line per caption, `\n` for a forced break.

## Layout

```
mgspotr.py            launcher
mgspotr/              workbench (project model, ISO patcher, preview renderer, Qt UI)
tools/                format code: archive (zar/dar), cap, keepfont, arabic_text, build_font
fonts/                SFArabicRounded-Regular.ttf
project/              the translation project (project.json, text/, output/, test_game/)
```
