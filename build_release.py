#!/usr/bin/env python3
"""Build the standalone player for the machine you run this on.

    python3 build_release.py                 # -> release/braverse-0.2.33-macos-arm64.zip
    python build_release.py --no-images      # same, ~190 MB smaller, cards as text

What comes out is **one file to send someone**: an installer that carries the
game inside it, zipped with a read-me. Only what a *player* needs is in it: the
engine, the browser front end, the card database, the card art, and (on macOS)
the native-window backend. `--no-installer` ships the bare game instead.
Everything the project uses to *develop* the game — torch, the RL pilots, the
evolution and training scripts, the tests, the card scrapers — is left out.

The lean bundle is arranged for, not hoped for. By default the build runs in a
throwaway virtualenv holding `requirements-play.txt` and PyInstaller and
nothing else, so there is no torch on the path for PyInstaller to discover in
the first place; `braverse.spec` names the same libraries in `excludes` as a
second line of defence. `--no-venv` builds with the current interpreter
instead, which is faster and only as lean as that interpreter is.

**PyInstaller does not cross-compile.** A Windows .exe has to be built on
Windows and a macOS binary on macOS — this script builds for the host and names
its output accordingly. To get both from one command, run it on both machines,
or let `.github/workflows/release.yml` run it on both runners.

Options:

    --no-venv       build with the current interpreter, no throwaway venv
    --no-images     leave out card_images/ (cards render as text)
    --no-installer  ship the bare game instead of an installer carrying it
    --webview       bundle pywebview, for the game's own window (default on macOS)
    --no-webview    leave it out; the game borrows a Chromium window instead
    --no-zip        leave the bare binary in release/, do not archive it
    --keep-build    keep PyInstaller's work directory for inspection
    --out DIR       where the release goes (default: release/)
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WINDOWS = os.name == "nt"

# Everything the bundle is built from. Missing any of these is a broken build,
# not a thin one, so they are checked before PyInstaller spends five minutes.
REQUIRED = [
    Path("play_server.py"),
    Path("install.py"),
    Path("desktop.py"),
    Path("tunnel.py"),
    Path("braverse_cards.csv"),
    Path("braverse/__init__.py"),
    Path("viewer/index.html"),
    Path("viewer/app.js"),
]

# Libraries that belong to the training half of the project. If one is on the
# path when PyInstaller runs it can be pulled in by an incidental import and
# quietly add a gigabyte, so the venv build asserts they are absent.
DEV_ONLY = ["torch", "tqdm", "pytest", "PIL", "matplotlib"]


def utf8_output() -> None:
    """`braverse.console.utf8_output`, loaded from its file rather than
    imported: `import braverse.console` runs the package's `__init__`, which
    needs numpy and compiles every card — and the whole point of this script is
    that the interpreter running it need not have the game's dependencies
    installed. Everything printed here is full of em dashes, and a redirected
    stdout on Windows is cp1252."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_braverse_console", ROOT / "braverse" / "console.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.utf8_output()


def say(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def die(msg: str) -> "None":
    raise SystemExit(f"[build] {msg}")


def version() -> str:
    """`braverse.__version__`, read rather than imported — importing the package
    costs numpy, which the interpreter running this script need not have."""
    text = (ROOT / "braverse" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else "0.0.0"


def platform_tag() -> str:
    """`macos-arm64`, `windows-x86_64`, `linux-x86_64` — what the file is named
    after, and the only thing that says which machine can run it."""
    system = {"Darwin": "macos", "Windows": "windows"}.get(
        platform.system(), platform.system().lower())
    arch = platform.machine().lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64",
            "aarch64": "arm64"}.get(arch, arch)
    return f"{system}-{arch}"


def run(cmd: list[str], **kw) -> None:
    say("$ " + " ".join(str(c) for c in cmd))
    proc = subprocess.run([str(c) for c in cmd], cwd=ROOT, **kw)
    if proc.returncode != 0:
        die(f"command failed ({proc.returncode}): {' '.join(str(c) for c in cmd)}")


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts" if WINDOWS else "bin") / ("python.exe" if WINDOWS else "python")


def preflight(args) -> None:
    missing = [p for p in REQUIRED if not (ROOT / p).exists()]
    if missing:
        die("missing files: " + ", ".join(str(p) for p in missing))

    if sys.version_info < (3, 9):
        die(f"needs Python 3.9+, this is {platform.python_version()}")

    if not args.no_images:
        count = len(list((ROOT / "card_images").glob("*.webp")))
        if count < 2000:
            die(f"card_images/ has {count} files — run `python fetch_images.py` "
                f"to fetch the art, or pass --no-images to build without it")
        say(f"card art: {count} files")


def build_venv(args) -> Path:
    """A virtualenv holding the play dependencies and PyInstaller, and nothing
    else. Rebuilt from scratch each run: a stale one is how a dependency that
    was dropped from requirements-play.txt keeps shipping."""
    venv = ROOT / ".venv-build"
    if venv.exists():
        say(f"removing stale {venv.name}/")
        shutil.rmtree(venv)
    say(f"creating {venv.name}/ with {platform.python_version()}")
    run([sys.executable, "-m", "venv", str(venv)])
    py = venv_python(venv)
    run([py, "-m", "pip", "install", "--upgrade", "--quiet", "pip"])
    run([py, "-m", "pip", "install", "--quiet", "-r", "requirements-play.txt"])
    run([py, "-m", "pip", "install", "--quiet", "pyinstaller>=6.0"])
    if args.webview:
        # pywebview's macOS backend builds against pyobjc, whose wheels for 3.9
        # stop at pyobjc-core 10 — pin it first or the install fails on a
        # compile nobody asked for.
        if platform.system() == "Darwin" and sys.version_info < (3, 10):
            run([py, "-m", "pip", "install", "--quiet", "pyobjc-core<11"])
        run([py, "-m", "pip", "install", "--quiet", "pywebview>=5"])
    return venv


def check_window_backend(py: Path, args) -> None:
    """A requested native window that the build interpreter cannot import is a
    silent downgrade — the spec bundles what it finds, and the binary comes out
    launching Chrome instead. Say so at build time, where it is fixable."""
    if not args.webview:
        return
    ok = subprocess.run([str(py), "-c", "import webview"],
                        capture_output=True).returncode == 0
    if not ok:
        die("--webview was asked for but pywebview is not importable in the "
            "build environment — install it there, or pass --no-webview")


def check_lean(py: Path) -> None:
    """No development-only library should be importable in the build
    environment. Warn rather than fail: `braverse.spec` excludes them anyway,
    and a shared interpreter (`--no-venv`) legitimately has them."""
    code = ("import importlib.util as u;"
            f"print(','.join(m for m in {DEV_ONLY!r} if u.find_spec(m)))")
    out = subprocess.run([str(py), "-c", code], capture_output=True,
                         text=True).stdout.strip()
    if out:
        say(f"note: {out} present in the build environment — the spec excludes "
            f"them, but a clean venv build is the safer bundle")


ICON = "ginger_brave_icon.ico"


def macos_icns(work: Path) -> Path | None:
    """The .ico turned into the .icns macOS insists on, via tools every Mac has.

    Not a nicety: a one-file binary on macOS is drawn with a generic icon
    whatever is inside it, so the `.app` the installer creates is the only
    thing that can wear the game's face — and `iconutil` will only build one
    from a folder of *square* PNGs. The source here is 32x29, so it is padded
    square and scaled up; a bigger square source would look better and nothing
    else would have to change.
    """
    source = ROOT / ICON
    if platform.system() != "Darwin" or not source.exists():
        return None
    if not shutil.which("sips") or not shutil.which("iconutil"):
        say("note: sips/iconutil missing — the shortcut gets the generic icon")
        return None

    iconset = work / "braverse.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)
    square = work / "square.png"
    quiet = {"capture_output": True}
    # Two steps, because `sips` reads the .ico but pads and scales separately:
    # -p sets the canvas (transparent), -z resizes into it.
    subprocess.run(["sips", "-s", "format", "png", str(source),
                    "--out", str(square)], **quiet)
    subprocess.run(["sips", "-p", "32", "32", str(square),
                    "--out", str(square)], **quiet)
    for size in (16, 32, 64, 128, 256, 512, 1024):
        subprocess.run(["sips", "-z", str(size), str(size), str(square),
                        "--out", str(iconset / f"icon_{size}x{size}.png")], **quiet)
        # Retina names are what iconutil actually looks for at each size.
        half = size // 2
        if half >= 16:
            shutil.copy2(iconset / f"icon_{size}x{size}.png",
                         iconset / f"icon_{half}x{half}@2x.png")
    icns = work / "braverse.icns"
    done = subprocess.run(["iconutil", "-c", "icns", str(iconset),
                           "-o", str(icns)], capture_output=True, text=True)
    if done.returncode != 0 or not icns.exists():
        say(f"note: could not build an .icns ({done.stderr.strip()})")
        return None
    return icns


def pyinstaller(py: Path, args, work: Path, dist: Path) -> tuple[Path, Path | None]:
    """The game, then the installer that carries it.

    Two runs over the same spec rather than one, because the second contains
    the first: a spec cannot embed a file the same spec has not written yet.
    """
    env = dict(os.environ)
    env["BRAVERSE_BUNDLE_IMAGES"] = "0" if args.no_images else "1"
    suffix = ".exe" if WINDOWS else ""
    base = [str(py), "-m", "PyInstaller", "braverse.spec", "--noconfirm",
            "--distpath", str(dist), "--workpath", str(work), "--log-level", "WARN"]

    say("running PyInstaller — this takes a few minutes")
    started = time.time()
    run(base, env={**env, "BRAVERSE_STAGE": "game"})
    exe = dist / f"braverse{suffix}"
    if not exe.exists():
        die(f"PyInstaller reported success but {exe} is not there")
    say(f"built {exe.name} — {exe.stat().st_size / 1e6:.0f} MB "
        f"in {time.time() - started:.0f}s")

    if args.no_installer:
        return exe, None

    icns = macos_icns(work)
    say("packing the game inside the installer")
    run(base, env={**env,
                   "BRAVERSE_STAGE": "installer",
                   # The payload: this is what makes the installer the only
                   # file a player needs.
                   "BRAVERSE_PAYLOAD": str(exe),
                   "BRAVERSE_ICON": str(icns) if icns else ""})
    installer = dist / f"install-braverse{suffix}"
    if not installer.exists():
        die(f"PyInstaller reported success but {installer} is not there")
    say(f"built {installer.name} — {installer.stat().st_size / 1e6:.0f} MB")
    if installer.stat().st_size < exe.stat().st_size:
        die("the installer is smaller than the game it should be carrying — "
            "the payload did not go in")
    return exe, installer


def smoke_test(exe: Path, what: str) -> None:
    """`--help` exits before anything happens but after every import, so it
    proves the bundle can load numpy, the card database and the engine — the
    failures a frozen build actually has."""
    try:
        proc = subprocess.run([str(exe), "--help"], capture_output=True,
                              text=True, timeout=180)
    except subprocess.TimeoutExpired:
        die(f"the {what} hung on --help")
        return
    if proc.returncode != 0:
        die(f"the {what} failed on --help:\n{proc.stdout}\n{proc.stderr}")
    say(f"smoke test passed ({what} starts and imports what it needs)")


NOTES = """\
CookieRun: Braverse Simulator {ver} — {tag}

{headline}
{underline}
{run_line}

{body}
  --port N      serve on another port
  --lan         let other machines on your network join (off by default)
  --no-browser  just serve, do not open anything

First launch
------------
macOS: this file is unsigned, so the first launch is refused — right-click it
and choose Open. The installer clears that flag on the game it installs, so it
only happens once, to this one file.
Windows: SmartScreen shows "unknown publisher" — More info -> Run anyway.

What is in here
---------------
The rules engine, the browser front end, the card database{art}. The RL pilots
are not included — they need PyTorch — so the opponents on offer are the
built-in heuristic and random bots, and another person.

Unofficial fan project, not affiliated with Devsisters.
"""

INSTALLER_BODY = """\
It asks where to put the game, makes the folders you drop decks and card art
into, and offers to make a shortcut. Nothing is installed system-wide and it
never asks for an administrator; uninstalling is deleting the folder it made.
It can start the game when it is done.

The game keeps decks, profiles and replays in the folder it is installed to.
Once it is installed you can run it with:
"""

GAME_BODY = """\
It starts a local server and opens the game in its own window (or a browser tab
at http://127.0.0.1:8080). Everything runs on this machine; nothing is
uploaded. The game keeps decks, profiles and replays beside itself, so put it
somewhere you can write to before you start saving decks.
"""


def package(exe: Path, installer: Path | None, args, tag: str, ver: str) -> Path:
    """The one file a player needs, plus a read-me, in a folder inside a zip.

    When there is an installer it is the *only* binary shipped: it carries the
    game inside it, so shipping the game beside it would double a 200 MB
    download to say the same thing twice.
    """
    out = (ROOT / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    stem = f"braverse-{ver}-{tag}"
    suffix = ".exe" if WINDOWS else ""
    staged = out / stem
    if staged.exists():
        shutil.rmtree(staged)
    staged.mkdir(parents=True)

    if installer is not None:
        name = f"install-braverse{suffix}"
        shipped = staged / name
        shutil.copy2(installer, shipped)
        notes = dict(
            headline="Install it", underline="-" * len("Install it"),
            run_line=f"  {name}" if WINDOWS else f"  ./{name}",
            body=INSTALLER_BODY + f"\n  braverse{suffix}\n")
    else:
        name = f"braverse{suffix}"
        shipped = staged / name
        shutil.copy2(exe, shipped)
        notes = dict(
            headline="Run it", underline="------",
            run_line=f"  {name}" if WINDOWS else f"  ./{name}",
            body=GAME_BODY)
    shipped.chmod(shipped.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    readme = staged / "README.txt"
    # newline="\n" explicitly: this file is read on Windows too, and
    # `write_text`'s newline argument does not exist before Python 3.10.
    with open(readme, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(NOTES.format(
            ver=ver, tag=tag,
            art=" and the full card art library" if not args.no_images else
                " (no card art in this build — cards render as text)",
            **notes))

    if args.no_zip:
        say(f"folder: {staged}")
        return staged

    archive = out / f"{stem}.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(staged.iterdir()):
            # Python's zipfile drops the executable bit unless it is written by
            # hand, and a binary that unzips without +x is a bug report.
            info = zipfile.ZipInfo.from_file(path, f"{stem}/{path.name}")
            info.external_attr = ((0o755 if path.suffix != ".txt" else 0o644) << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, path.read_bytes())
    shutil.rmtree(staged)
    say(f"release: {archive} ({archive.stat().st_size / 1e6:.0f} MB)")
    return archive


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-venv", action="store_true",
                    help="build with the current interpreter instead of a clean venv")
    ap.add_argument("--no-images", action="store_true",
                    help="leave out card_images/ (~190 MB smaller, cards as text)")
    ap.add_argument("--no-installer", action="store_true",
                    help="ship the bare game instead of an installer carrying it")
    ap.add_argument("--webview", dest="webview", action="store_true", default=None,
                    help="bundle pywebview so the game opens in a native window "
                         "(the default on macOS)")
    ap.add_argument("--no-webview", dest="webview", action="store_false",
                    help="leave pywebview out; the game borrows a Chromium window")
    ap.add_argument("--no-zip", action="store_true",
                    help="leave the bare binary in the output directory")
    ap.add_argument("--keep-build", action="store_true",
                    help="keep PyInstaller's work directory")
    ap.add_argument("--out", default="release", help="output directory (default: release)")
    args = ap.parse_args()
    utf8_output()

    # A build with no native-window backend is not a build without a window —
    # it is a build that launches *Google Chrome*, because that is what
    # `desktop.py` falls back to. On macOS pywebview costs about 2 MB frozen
    # and gives the game its own WebKit window, so it is the default there.
    # Not on Windows, where pywebview needs pythonnet to reach WebView2 and the
    # frozen combination is untested — and where the fallback is a chromeless
    # Edge window that ships with the OS, so it always works and looks the
    # same. `--webview` opts in there, `--no-webview` opts out anywhere.
    if args.webview is None:
        args.webview = platform.system() == "Darwin"

    ver, tag = version(), platform_tag()
    say(f"braverse {ver} for {tag}"
        + ("" if args.webview else " (no native window backend — the game will "
                                  "borrow a Chromium window)"))
    preflight(args)

    py = Path(sys.executable)
    if args.no_venv:
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            die("PyInstaller is not installed here — `pip install pyinstaller`, "
                "or drop --no-venv and let this script make a clean venv")
    else:
        py = venv_python(build_venv(args))
    check_lean(py)
    check_window_backend(py, args)

    work = ROOT / "build"
    dist = ROOT / "dist"
    exe, installer = pyinstaller(py, args, work, dist)
    smoke_test(exe, "game")
    if installer is not None:
        smoke_test(installer, "installer")
    result = package(exe, installer, args, tag, ver)

    if not args.keep_build and work.exists():
        shutil.rmtree(work, ignore_errors=True)
    say(f"done: {result}")
    say("PyInstaller does not cross-compile — run this on Windows for the .exe, "
        "on macOS for the macOS build.")


if __name__ == "__main__":
    main()
