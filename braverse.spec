# PyInstaller spec: bundle the visual player into one standalone executable.
#
#     pip install pyinstaller
#     pyinstaller braverse.spec        # -> dist/braverse
#
# The binary carries the engine, the browser front end, the card database and
# the full ~2000-card art library, so it runs on a machine with no Python and
# no `card_images/` checkout, and any deck of any cards renders.
#
# RL pilots are deliberately left out: they need torch, which would add ~1 GB.
# `available_pilots()` only offers `rl:*` when a `.pt` file sits next to the
# binary, and none is bundled, so the menu degrades to human/heuristic/random.

import os
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

decklists = [(str(p), ".") for p in sorted(ROOT.glob("*.txt"))]

# Optional: if pywebview is installed at build time, carry it so the binary
# opens a native window. Without it the binary still runs — `desktop.py` falls
# back to a chromeless Chrome/Edge window, then to a browser tab.
try:
    from PyInstaller.utils.hooks import collect_all
    web_datas, web_binaries, web_hidden = collect_all("webview")
except Exception:
    web_datas, web_binaries, web_hidden = [], [], []

a = Analysis(
    ["play_server.py"],
    pathex=[str(ROOT)],
    datas=[
        (str(ROOT / "viewer"), "viewer"),
        (str(ROOT / "braverse_cards.csv"), "."),
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
)

# The installer, built beside the game as its own small binary: it copies the
# game into a chosen folder and makes the folders a player drops decks and card
# art into. It ships next to the game so installing needs no Python either — a
# script would not help someone who has none, which is the whole audience for a
# frozen build. It carries no data and needs no numpy, so it costs a few MB.
#
# `BRAVERSE_INSTALLER=0` skips it, for a build of only the game.
if os.environ.get("BRAVERSE_INSTALLER", "1") != "0":
    inst = Analysis(
        ["install.py"],
        pathex=[str(ROOT)],
        datas=[],
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
    )
