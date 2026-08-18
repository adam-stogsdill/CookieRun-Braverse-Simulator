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

from pathlib import Path

ROOT = Path(SPECPATH)

# The whole library, so a decklist dropped next to the binary can name any card
# and still render as art. Run `python3 fetch_images.py` before building — a
# thin `card_images/` silently yields a binary with holes in it.
images = [(str(p), "card_images") for p in sorted((ROOT / "card_images").glob("*.webp"))]
if len(images) < 2000:
    raise SystemExit(
        f"card_images/ has only {len(images)} files — run `python3 fetch_images.py` "
        f"first, or edit this check if a partial library is what you want."
    )

decklists = [(str(p), ".") for p in sorted(ROOT.glob("*.txt"))]

a = Analysis(
    ["play_server.py"],
    pathex=[str(ROOT)],
    datas=[
        (str(ROOT / "viewer"), "viewer"),
        (str(ROOT / "braverse_cards.csv"), "."),
        *decklists,
        *images,
    ],
    hiddenimports=[],
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
