#!/usr/bin/env python3
"""Build the standalone player for the machine you run this on.

    python3 build_release.py                 # -> release/braverse-0.2.33-macos-arm64.zip
    python build_release.py --no-images      # same, ~190 MB smaller, cards as text

What comes out is only what a *player* needs: the engine, the browser front end,
the card database, the card art, and (optionally) the native-window backend.
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
    --webview       also bundle pywebview, for a native window over a browser tab
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


def pyinstaller(py: Path, args, work: Path, dist: Path) -> Path:
    env = dict(os.environ)
    env["BRAVERSE_BUNDLE_IMAGES"] = "0" if args.no_images else "1"
    say("running PyInstaller — this takes a few minutes")
    started = time.time()
    run([py, "-m", "PyInstaller", "braverse.spec", "--noconfirm",
         "--distpath", str(dist), "--workpath", str(work),
         "--log-level", "WARN"], env=env)
    exe = dist / ("braverse.exe" if WINDOWS else "braverse")
    if not exe.exists():
        die(f"PyInstaller reported success but {exe} is not there")
    size = exe.stat().st_size / 1e6
    say(f"built {exe.name} — {size:.0f} MB in {time.time() - started:.0f}s")
    return exe


def smoke_test(exe: Path) -> None:
    """`--help` exits before the server starts but after every import, so it
    proves the bundle can load numpy, the card database and the engine — the
    failures a frozen build actually has."""
    try:
        proc = subprocess.run([str(exe), "--help"], capture_output=True,
                              text=True, timeout=180)
    except subprocess.TimeoutExpired:
        die("the binary hung on --help")
        return
    if proc.returncode != 0:
        die(f"the binary failed on --help:\n{proc.stdout}\n{proc.stderr}")
    say("smoke test passed (binary starts and imports the engine)")


NOTES = """\
CookieRun: Braverse Simulator {ver} — {tag}

Run it
------
{run_line}

It starts a local server and opens the game in a window (or a browser tab at
http://127.0.0.1:8080). Everything runs on this machine; nothing is uploaded.

  --port N      serve on another port
  --lan         let other machines on your network join (off by default)
  --no-browser  just serve, do not open anything

macOS: the binary is unsigned, so the first launch is refused. Right-click it
and choose Open, or run:  xattr -dr com.apple.quarantine ./braverse
Windows: SmartScreen shows "unknown publisher" — More info -> Run anyway.

Decks, replays and profiles
---------------------------
Decklists (.txt) placed next to this binary show up in the deck menu. Games you
keep, saved decks and player profiles are written next to it too, so keep it
somewhere you can write to (not /Applications, not Program Files).

What is in here
---------------
The rules engine, the browser front end, the card database{art}. The RL pilots
are not included — they need PyTorch — so the opponents on offer are the
built-in heuristic and random bots, and another person.

Unofficial fan project, not affiliated with Devsisters.
"""


def package(exe: Path, args, tag: str, ver: str) -> Path:
    out = (ROOT / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"braverse-{ver}-{tag}"
    final = out / (stem + (".exe" if WINDOWS else ""))
    if final.exists():
        final.unlink()
    shutil.copy2(exe, final)
    final.chmod(final.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    notes = NOTES.format(
        ver=ver, tag=tag,
        run_line=(f"  {final.name}" if WINDOWS else f"  ./{final.name}"),
        art=" and the full card art library" if not args.no_images else
            " (no card art in this build — cards render as text)")
    readme = out / f"{stem}-README.txt"
    # newline="\n" explicitly: this file is read on Windows too, and
    # `write_text`'s newline argument does not exist before Python 3.10.
    with open(readme, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(notes)

    if args.no_zip:
        say(f"binary: {final}")
        return final

    archive = out / f"{stem}.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        # Python's zipfile drops the executable bit unless it is written by
        # hand, and a binary that unzips without +x is a bug report.
        info = zipfile.ZipInfo.from_file(final, final.name)
        info.external_attr = (0o755 << 16)
        info.compress_type = zipfile.ZIP_DEFLATED
        with open(final, "rb") as fh:
            z.writestr(info, fh.read())
        z.write(readme, "README.txt")
    final.unlink()
    readme.unlink()
    say(f"release: {archive} ({archive.stat().st_size / 1e6:.0f} MB)")
    return archive


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-venv", action="store_true",
                    help="build with the current interpreter instead of a clean venv")
    ap.add_argument("--no-images", action="store_true",
                    help="leave out card_images/ (~190 MB smaller, cards as text)")
    ap.add_argument("--webview", action="store_true",
                    help="bundle pywebview so the game opens in a native window")
    ap.add_argument("--no-zip", action="store_true",
                    help="leave the bare binary in the output directory")
    ap.add_argument("--keep-build", action="store_true",
                    help="keep PyInstaller's work directory")
    ap.add_argument("--out", default="release", help="output directory (default: release)")
    args = ap.parse_args()
    utf8_output()

    ver, tag = version(), platform_tag()
    say(f"braverse {ver} for {tag}")
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

    work = ROOT / "build"
    dist = ROOT / "dist"
    exe = pyinstaller(py, args, work, dist)
    smoke_test(exe)
    result = package(exe, args, tag, ver)

    if not args.keep_build and work.exists():
        shutil.rmtree(work, ignore_errors=True)
    say(f"done: {result}")
    say("PyInstaller does not cross-compile — run this on Windows for the .exe, "
        "on macOS for the macOS build.")


if __name__ == "__main__":
    main()
