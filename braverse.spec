# PyInstaller spec: bundle the visual player into one standalone executable.
#
#     pip install pyinstaller
#     pyinstaller braverse.spec        # -> dist/braverse
#
# It builds one of two things, because the second one contains the first and a
# spec cannot depend on its own output. `build_release.py` runs it twice:
#
#     (no env)                         the game            -> dist/braverse
#     BRAVERSE_STAGE=installer         the installer       -> dist/install-braverse
#         BRAVERSE_PAYLOAD=dist/braverse   the game to carry inside it
#         BRAVERSE_ICON=...icns            the icon it dresses the shortcut with
#
# An installer built with a payload is the only file a player needs, which is
# the point: one download, nothing to keep next to it.
#
# The binary carries the engine, the browser front end, the card database and
# the full ~2000-card art library, so it runs on a machine with no Python and
# no `card_images/` checkout, and any deck of any cards renders.
#
# RL pilots are deliberately left out: they need torch, which would add ~1 GB.
# `available_pilots()` only offers `rl:*` when a `.pt` file sits next to the
# binary, and none is bundled, so the menu degrades to human/heuristic/random.

import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH)

# The whole library, so a decklist dropped next to the binary can name any card
# and still render as art. Run `python3 fetch_images.py` before building — a
# thin `card_images/` silently yields a binary with holes in it.
#
# `BRAVERSE_BUNDLE_IMAGES=0` builds without it: a ~190 MB smaller binary that
# draws every card as text instead of art. `build_release.py --no-images` sets
# it; nothing else does, so a plain `pyinstaller braverse.spec` is unchanged.
if os.environ.get("BRAVERSE_BUNDLE_IMAGES", "1") == "0":
    images = []
else:
    images = [(str(p), "card_images") for p in sorted((ROOT / "card_images").glob("*.webp"))]
    if len(images) < 2000:
        raise SystemExit(
            f"card_images/ has only {len(images)} files — run `python3 fetch_images.py` "
            f"first, or edit this check if a partial library is what you want."
        )

# The game's face. Windows compiles an icon into the .exe and the Start Menu
# shortcut inherits it; macOS draws a plain one-file binary with a generic icon
# whatever is inside it, and rejects a .ico outright — there the icon travels
# in the installer as an .icns and lands on the .app bundle it creates. The
# game also serves this file as its favicon (`/icon.ico`).
ICON = ROOT / "ginger_brave_icon.ico"
# The bitmap twin of it. A native window (`desktop.py`) draws its Dock tile and
# taskbar button from a file, not from the favicon and not from the icon
# compiled into the .exe, and only WinForms can read a .ico — so both travel.
ICON_PNG = ROOT / "ginger_brave_icon.png"
exe_icon = str(ICON) if (ICON.exists() and sys.platform == "win32") else None

decklists = [(str(p), ".") for p in sorted(ROOT.glob("*.txt"))]

# Optional: if pywebview is installed at build time, carry it so the binary
# opens a native window. Without it the binary still runs — `desktop.py` falls
# back to a chromeless Chrome/Edge window, then to a browser tab.
try:
    from PyInstaller.utils.hooks import collect_all
    web_datas, web_binaries, web_hidden = collect_all("webview")
except Exception:
    web_datas, web_binaries, web_hidden = [], [], []

STAGE = os.environ.get("BRAVERSE_STAGE", "game")

if STAGE == "game":
    a = Analysis(
        ["play_server.py"],
        pathex=[str(ROOT)],
        datas=[
            (str(ROOT / "viewer"), "viewer"),
            (str(ROOT / "braverse_cards.csv"), "."),
            *([(str(ICON), ".")] if ICON.exists() else []),
            *([(str(ICON_PNG), ".")] if ICON_PNG.exists() else []),
            *decklists,
            *images,
            *web_datas,
        ],
        binaries=web_binaries,
        hiddenimports=web_hidden,
        excludes=["torch", "tqdm", "pytest", "PIL", "tkinter", "matplotlib"],
        noarchive=False,
    )
    pyz = PYZ(a.pure)

    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        name="braverse",
        console=True,
        onefile=True,
        upx=False,
        target_arch=None,
        icon=exe_icon,
    )

else:
    # The installer. It copies the game into a chosen folder and makes the
    # folders a player drops decks and card art into — and it carries the game
    # itself, so the file someone is sent is the only file they need. Without a
    # payload it is a few MB and installs whatever sits beside it, which is what
    # a build run straight from a checkout produces.
    payload = os.environ.get("BRAVERSE_PAYLOAD", "")
    if payload and not Path(payload).is_file():
        raise SystemExit(f"BRAVERSE_PAYLOAD is set to {payload}, which is not a file")
    icon_data = os.environ.get("BRAVERSE_ICON", "")

    inst = Analysis(
        ["install.py"],
        pathex=[str(ROOT)],
        datas=[
            # "payload" is `install.PAYLOAD_DIR`; the game is unpacked from
            # there at install time. Named rather than guessed, because the
            # installer looks it up by that name.
            *([(payload, "payload")] if payload else []),
            *([(icon_data, ".")] if icon_data and Path(icon_data).is_file() else []),
        ],
        # No numpy: the installer copies a file and writes some text, and
        # dragging the game's dependencies into it would double a binary that
        # already carries the game.
        excludes=["numpy", "torch", "tqdm", "pytest", "PIL", "tkinter", "matplotlib"],
        noarchive=False,
    )
    inst_pyz = PYZ(inst.pure)
    inst_exe = EXE(
        inst_pyz,
        inst.scripts,
        inst.binaries,
        inst.datas,
        name="install-braverse",
        console=True,
        onefile=True,
        upx=False,
        target_arch=None,
        icon=exe_icon,
    )
