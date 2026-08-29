#!/usr/bin/env python3
"""Put the game somewhere permanent, with folders to drop things into.

    ./install.py                    # asks where, then whether to make a shortcut
    ./install.py --yes              # take every default, ask nothing
    ./install.py --uninstall        # remove the program, keep decks and profiles

The binary already runs from wherever it sits — this exists because *where it
sits* decides where the game keeps things. `play_server.py` reads decks, card
art, replays and profiles from the directory the executable is in, so a binary
left in Downloads scatters a player's saved decks through Downloads, and one
moved afterwards leaves them behind. Installing is choosing that directory
deliberately and making the folders before the game needs them:

    <install dir>/
        braverse[.exe]      the game
        decks/              decklist .txt files — yours and other people's
        card_images/        art for cards the build predates, or your own scans
        profiles/           players, written by the game
        replays/            games you kept, written by the game
        saved_decks.json    decks built in the browser, written by the game

Every folder is optional to the game and empty at first; each gets a short
read-me saying what belongs in it. The last three fill themselves in as you
play. The first two are yours to put things in, either by dropping files
straight in or by using the game's deck builder, and both are read at startup,
so a deck added while the game is open shows up when it restarts.

Reinstalling over an existing install replaces the program and touches nothing
else — that is the upgrade path, and it is why the installed binary is named
`braverse` rather than after a version.

**The frozen installer carries the game inside it.** That is the file to send
someone: one download, no second file to keep next to it, nothing to unzip in
the right order. `build_release.py` embeds the built game as a payload, and
`find_binary` unpacks it from there. Running from a checkout there is no
payload, so `--binary dist/braverse` says which game to install.

Stdlib only, and it never needs the network.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path

APP_NAME = "CookieRun Braverse"
WINDOWS = os.name == "nt"
MACOS = platform.system() == "Darwin"
EXE_NAME = "braverse.exe" if WINDOWS else "braverse"
# Where the frozen installer keeps the game it carries, and the icon it dresses
# the shortcut with. `braverse.spec` puts them there.
PAYLOAD_DIR = "payload"
ROOT = Path(__file__).resolve().parent
ICON_NAME = "braverse.icns"

# The folders the layout is made of, and what each one is for. The text is
# written into the folder as a read-me, because the folder is the interface: a
# player who never opens this script still has to know that `decks/` takes
# `.txt` files and `card_images/` takes `.webp` named after a card.
FOLDERS = {
    "agents": """\
Trained practice opponents go here, as .pt files.

Every file in this folder is offered as an opponent when you start a game, by
its file name. The ones that came with the game are already here; a file
someone sends you, or one you train yourself, shows up the same way once it is
in this folder.

These are the "rl:" opponents in the list. They are not the only practice
opponents — the built-in ones need no file and are always there.

Delete a file to remove that opponent from the list.
""",
    "decks": """\
Decklists go here, as .txt files.

Anything you save in the game's deck builder, or that someone sends you, can
live in this folder — the game reads it at startup and every list shows up in
the deck menu. A file here wins over a deck of the same name that shipped with
the game, so you can replace one rather than adding a second.

You do not have to use this folder: the deck builder's Import button takes the
same files, and a deck imported there is saved for you. This folder is the way
in when you would rather work with files.

Delete a file to remove that deck from the menu.
""",
    "card_images": """\
Card art goes here, as .webp files named after the card id — ST9-007.webp.

The game already carries art for every card that existed when it was built.
Put a file here only to add a card printed since, or to use your own scan of
one: a picture in this folder is used instead of the built-in one.

A card with no art anywhere still plays — it is drawn as its printed text.
""",
    "profiles": """\
Players, written by the game. One file each.

Each file is encrypted, and one with a passphrase can only be opened by
someone who knows it. Only the name and picture are readable without it, so
the game can show you the list before you have chosen anyone.

Copy a file to another machine's profiles folder to take a player with you.
Delete one and that player is gone, including their history — the game has no
undo for this.
""",
    "replays": """\
Games you kept, written by the game. One .json file each.

Watch them back from the Replays tab. A replay is the decisions both players
made, not a video, so it is small and can be sent to someone else who has the
game — put a file in this folder and it appears in the list.

The game prunes its own recent games as they age out; anything you marked as
kept stays until you delete it.
""",
}

NOTES = """\
{app}

The game
--------
Run `{exe}` in this folder. It opens the game in a window, or in your browser
at http://127.0.0.1:8080. Everything runs on this computer.

Folders
-------
decks/         decklists you add — .txt files, read at startup
card_images/   card art you add — .webp named after the card id
profiles/      players, written by the game
replays/       games you kept, written by the game

Each folder has a read-me of its own saying what goes in it. They are yours to
copy, back up, or move to another computer.

Uninstalling
------------
Delete this folder. Nothing was installed anywhere else{shortcut_note}.
"""


def say(msg: str) -> None:
    print(msg, flush=True)


def die(msg: str) -> None:
    raise SystemExit(f"error: {msg}")


# ---------------------------------------------------------------------------
# where things go
# ---------------------------------------------------------------------------
def default_dir() -> Path:
    """Somewhere the player owns and can write to without being an admin.

    Deliberately not `/Applications` or `Program Files`: the game writes its
    profiles and replays next to itself, and those directories are read-only to
    the person playing. The game copes — it falls back to `~/.braverse` — but
    then the folders this script makes are not the folders it uses, which is a
    confusing way to lose a deck.
    """
    if WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "Braverse"
    if MACOS:
        return Path.home() / "Applications" / "Braverse"
    return Path.home() / ".local" / "share" / "braverse"


def payload() -> Path | None:
    """The game carried inside this installer, if it was built with one.

    A one-file build unpacks itself into a temporary directory and points
    `sys._MEIPASS` at it, so the game is an ordinary file by the time anyone
    asks — it just has to be marked executable again, since the unpacking does
    not preserve the bit.
    """
    if not getattr(sys, "frozen", False):
        return None
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    for name in (EXE_NAME, "braverse", "braverse.exe"):
        found = Path(base) / PAYLOAD_DIR / name
        if found.is_file():
            found.chmod(found.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            return found
    return None


def find_binary(explicit: str | None) -> Path:
    """The game to install: what was pointed at, what we carry, or what sits
    beside us.

    The carried copy is the normal case and the reason this file is worth
    sending on its own. Beside-us is the fallback for a release built without a
    payload; `--binary` is how a checkout says which build to install.
    """
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            die(f"no such file: {path}")
        return path

    carried = payload()
    if carried is not None:
        return carried

    here = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    names = [EXE_NAME, "braverse", "braverse.exe"]
    for name in names:
        candidate = here / name
        if candidate.is_file():
            return candidate
    # The zips are named braverse-<version>-<platform>[.exe]; accept that too,
    # so unzipping and double-clicking works without renaming anything. Matched
    # on the whole name rather than the extension: `braverse-0.2.34-macos-arm64`
    # has a "suffix" of `.34-macos-arm64`, which is nobody's idea of one.
    skip = {".txt", ".zip", ".json", ".md", ".py"}
    loose = sorted(p for p in here.glob("braverse-*")
                   if p.is_file() and p.suffix.lower() not in skip
                   and "install" not in p.name.lower()
                   and (p.suffix.lower() == ".exe" if WINDOWS
                        else os.access(p, os.X_OK)))
    if loose:
        return loose[0]
    die(f"this installer carries no game and there is none next to {here} — "
        f"pass --binary /path/to/{EXE_NAME}")
    raise AssertionError("unreachable")


def ask(question: str, default: str, assume_yes: bool) -> str:
    """A prompt with a default, that answers itself when nobody is there.

    A frozen installer double-clicked on Windows has a console; one run from a
    script or a CI job has no stdin at all, and blocking forever on a question
    nobody can see is the worst of the options.
    """
    if assume_yes or not sys.stdin or not sys.stdin.isatty():
        return default
    try:
        reply = input(f"{question} [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return reply or default


def ask_yes(question: str, default: bool, assume_yes: bool) -> bool:
    reply = ask(f"{question} (y/n)", "y" if default else "n", assume_yes)
    return reply.strip().lower().startswith("y")


# ---------------------------------------------------------------------------
# installing
# ---------------------------------------------------------------------------
def write_text(path: Path, text: str) -> None:
    """UTF-8 with LF endings, everywhere. These files are read on Windows,
    where the default encoding is cp1252 and every dash here is not in it."""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def make_layout(target: Path) -> list[Path]:
    """The folders, each with its read-me. Idempotent: an existing folder is
    left exactly as it is, because it is full of the player's things."""
    made = []
    for name, blurb in FOLDERS.items():
        folder = target / name
        folder.mkdir(parents=True, exist_ok=True)
        write_text(folder / "README.txt", blurb)
        made.append(folder)
    return made


def install(binary: Path, target: Path, shortcut_note: str = "") -> Path:
    target.mkdir(parents=True, exist_ok=True)
    installed = target / EXE_NAME

    if installed.exists() and installed.samefile(binary):
        die(f"{binary} is already the installed copy — nothing to do")

    # Windows will not overwrite a running executable, and an upgrade run while
    # the game is open is a plausible thing for someone to do.
    try:
        shutil.copy2(binary, installed)
    except PermissionError:
        die(f"cannot write {installed} — close the game if it is running, "
            f"then run this again")
    installed.chmod(installed.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    make_layout(target)
    write_text(target / "README.txt",
               NOTES.format(app=APP_NAME, exe=EXE_NAME, shortcut_note=shortcut_note))
    unquarantine(installed)
    return installed


def unquarantine(installed: Path) -> None:
    """Clear macOS's download flag on the copy we just made.

    A binary that came out of a downloaded zip carries
    `com.apple.quarantine`, and Gatekeeper refuses an unsigned one outright —
    the "cannot be opened because the developer cannot be verified" dialog with
    no Open button. Clearing it on a file the person explicitly chose to
    install is the difference between the game starting and the game looking
    broken. Best effort: it is not fatal if xattr is missing or refuses.
    """
    if not MACOS:
        return
    subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(installed)],
                   capture_output=True)


# ---------------------------------------------------------------------------
# shortcuts
# ---------------------------------------------------------------------------
def windows_shortcut_script(installed: Path, link: Path) -> str:
    """The PowerShell that makes a .lnk. Built as a string so it can be read in
    a test on any platform — the COM object only exists on Windows."""
    return (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
        f"'{link}')\n"
        f"$s.TargetPath = '{installed}'\n"
        # Without this the shortcut runs the game from System32, and the game
        # keeps its decks and profiles beside the *executable* — so this line
        # is not cosmetic, it is where a shortcut-launched game saves things.
        f"$s.WorkingDirectory = '{installed.parent}'\n"
        # A .lnk shows the target's icon on its own, until something in the
        # shell's cache says otherwise — naming it costs a line and takes that
        # "until" away.
        f"$s.IconLocation = '{installed},0'\n"
        f"$s.Description = '{APP_NAME}'\n"
        "$s.Save()\n"
    )


def windows_shortcut(installed: Path, desktop: bool = False) -> list[Path]:
    made = []
    appdata = os.environ.get("APPDATA")
    targets = []
    if appdata:
        start = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        start.mkdir(parents=True, exist_ok=True)
        targets.append(start / f"{APP_NAME}.lnk")
    if desktop:
        targets.append(Path.home() / "Desktop" / f"{APP_NAME}.lnk")
    for link in targets:
        script = windows_shortcut_script(installed, link)
        proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                               "-Command", script], capture_output=True, text=True)
        if proc.returncode == 0 and link.exists():
            made.append(link)
        else:
            say(f"  could not make {link.name}: {proc.stderr.strip() or 'unknown error'}")
    return made


MAC_PLIST = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>{app}</string>
  <key>CFBundleDisplayName</key><string>{app}</string>
  <key>CFBundleIdentifier</key><string>local.braverse.launcher</string>
  <key>CFBundleExecutable</key><string>launch</string>{icon}
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>10.13</string>
</dict>
</plist>
"""


def icon_file() -> Path | None:
    """The .icns this installer carries, if it was built with one."""
    base = getattr(sys, "_MEIPASS", None) if getattr(sys, "frozen", False) else None
    found = Path(base or ROOT) / ICON_NAME
    return found if found.is_file() else None


def macos_app(installed: Path, where: Path | None = None) -> list[Path]:
    """A tiny .app that runs the installed binary.

    A bundle rather than an alias so it turns up in Spotlight and Launchpad and
    can be kept in the Dock, which is what "shortcut" means on this platform.
    It holds a two-line script, not a copy of the game: upgrading replaces the
    binary and this keeps pointing at it.

    The icon lives here rather than on the binary, because a one-file build is
    a plain executable and macOS draws those with a generic icon no matter what
    is inside them. This bundle is the only thing on the platform that can wear
    the game's face.
    """
    base = where or (Path.home() / "Applications")
    base.mkdir(parents=True, exist_ok=True)
    app = base / f"{APP_NAME}.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)

    art = icon_file()
    icon_key = ""
    if art is not None:
        resources = app / "Contents" / "Resources"
        resources.mkdir(parents=True, exist_ok=True)
        shutil.copy2(art, resources / ICON_NAME)
        # copy2 keeps the mode, and a file unpacked from a frozen installer is
        # 0600 — readable by whoever installed and by nobody else, which is a
        # bundle with no icon for the second account on the machine.
        (resources / ICON_NAME).chmod(0o644)
        icon_key = f"\n  <key>CFBundleIconFile</key><string>{ICON_NAME}</string>"
    write_text(app / "Contents" / "Info.plist",
               MAC_PLIST.format(app=APP_NAME, icon=icon_key))
    launcher = macos / "launch"
    # cd first: the game keeps decks, profiles and replays beside the
    # executable, and a bundle launched from the Dock starts at /.
    write_text(launcher, f'#!/bin/sh\ncd "{installed.parent}"\nexec "{installed}" "$@"\n')
    launcher.chmod(0o755)
    # Finder caches a bundle's icon against the bundle's own modification date,
    # and writing files *inside* it does not move that date — so re-installing
    # over an .app that once had no icon leaves the old blank one on screen
    # until something else touches it. Touching it is the whole fix.
    os.utime(app, None)
    return [app]


def linux_desktop(installed: Path) -> list[Path]:
    apps = Path.home() / ".local" / "share" / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    entry = apps / "braverse.desktop"
    write_text(entry, "\n".join([
        "[Desktop Entry]", "Type=Application", f"Name={APP_NAME}",
        "Comment=Play CookieRun: Braverse against a bot or a friend",
        f"Exec={installed}", f"Path={installed.parent}",
        "Terminal=false", "Categories=Game;", "",
    ]))
    entry.chmod(0o755)
    return [entry]


def make_shortcut(installed: Path, desktop: bool = False) -> list[Path]:
    if WINDOWS:
        return windows_shortcut(installed, desktop=desktop)
    if MACOS:
        return macos_app(installed)
    return linux_desktop(installed)


def shortcut_paths() -> list[Path]:
    """Where a shortcut would be, for uninstalling and for the read-me."""
    out = []
    if WINDOWS:
        appdata = os.environ.get("APPDATA")
        if appdata:
            out.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu"
                       / "Programs" / f"{APP_NAME}.lnk")
        out.append(Path.home() / "Desktop" / f"{APP_NAME}.lnk")
    elif MACOS:
        out.append(Path.home() / "Applications" / f"{APP_NAME}.app")
    else:
        out.append(Path.home() / ".local" / "share" / "applications" / "braverse.desktop")
    return out


# ---------------------------------------------------------------------------
# uninstalling
# ---------------------------------------------------------------------------
def uninstall(target: Path, keep_data: bool = True) -> None:
    """Take the program away and leave the player's things alone.

    Decks, profiles and replays are not ours to delete on the way out — someone
    uninstalling to reinstall a newer build would lose every player. `--purge`
    is the way to say otherwise, and it says what it is about to remove.
    """
    if not target.exists():
        die(f"nothing installed at {target}")

    for link in shortcut_paths():
        if link.is_dir():
            shutil.rmtree(link, ignore_errors=True)
            say(f"removed {link}")
        elif link.exists():
            link.unlink()
            say(f"removed {link}")

    if not keep_data:
        shutil.rmtree(target, ignore_errors=True)
        say(f"removed {target} and everything in it")
        return

    exe = target / EXE_NAME
    if exe.exists():
        exe.unlink()
        say(f"removed {exe}")
    (target / "README.txt").unlink(missing_ok=True)
    # The read-me we wrote does not count as something of theirs — a folder
    # holding only that is an empty folder, and saying "kept your profiles"
    # about one is how someone believes a player survived that did not.
    kept = [d for d in FOLDERS
            if any(f.name != "README.txt" for f in (target / d).glob("*"))]
    say(f"kept your {', '.join(sorted(kept))} in {target}" if kept
        else f"nothing of yours was in {target}")


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", help=f"where to install (default: {default_dir()})")
    ap.add_argument("--binary", help="the game to install (default: the one beside this script)")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="take every default and ask nothing")
    ap.add_argument("--shortcut", dest="shortcut", action="store_true", default=None,
                    help="make a shortcut without asking")
    ap.add_argument("--no-shortcut", dest="shortcut", action="store_false",
                    help="do not make a shortcut")
    ap.add_argument("--desktop", action="store_true",
                    help="Windows: put a shortcut on the desktop as well")
    ap.add_argument("--uninstall", action="store_true",
                    help="remove the program and any shortcut, keep decks and profiles")
    ap.add_argument("--purge", action="store_true",
                    help="with --uninstall: delete the folder and everything in it")
    args = ap.parse_args()
    utf8_output()

    target = Path(args.dir).expanduser().resolve() if args.dir else default_dir()

    try:
        if args.uninstall:
            if args.purge and not args.yes and not ask_yes(
                    f"delete {target} and every deck, profile and replay in it?",
                    False, False):
                die("cancelled")
            uninstall(target, keep_data=not args.purge)
            return

        binary = find_binary(args.binary)
        if not args.dir:
            target = Path(ask(f"install {APP_NAME} to", str(default_dir()),
                              args.yes)).expanduser().resolve()

        say(f"installing {binary.name} to {target}")
        installed = install(binary, target)
        say(f"  {installed.name}")
        for folder in sorted(FOLDERS):
            say(f"  {folder}{os.sep}")

        want = args.shortcut
        if want is None:
            want = ask_yes("make a shortcut", True, args.yes)
        links = make_shortcut(installed, desktop=args.desktop) if want else []
        # Rewritten now that we know whether there is one, so the read-me in
        # the folder is true about what else is on the machine.
        note = (" except " + " and ".join(str(p) for p in links)) if links else ""
        write_text(target / "README.txt",
                   NOTES.format(app=APP_NAME, exe=EXE_NAME, shortcut_note=note))
        for link in links:
            say(f"  shortcut: {link}")
        if links and MACOS:
            say("  (drag it to the Dock to keep it there)")

        say("")
        say(f"done. Decks go in {target / 'decks'}, card art in "
            f"{target / 'card_images'}.")
        if ask_yes("start the game now", False, False):
            launch(installed)
    finally:
        pause_if_double_clicked()


def launch(installed: Path) -> None:
    """Start the game and let go of it, so closing this window is harmless."""
    kwargs = {"cwd": str(installed.parent)}
    if WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([str(installed)], **kwargs)


def pause_if_double_clicked() -> None:
    """Hold the console open on Windows.

    A frozen installer that was double-clicked owns its console window, and
    that window closes the moment this process ends — including on the error
    that explains what went wrong.
    """
    if WINDOWS and getattr(sys, "frozen", False) and sys.stdin and sys.stdin.isatty():
        try:
            input("\nPress Enter to close.")
        except (EOFError, KeyboardInterrupt):
            pass


def utf8_output() -> None:
    """`braverse.console.utf8_output`, inlined: this script is frozen on its
    own, with no `braverse` package beside it, and everything it prints goes to
    a Windows console that defaults to cp1252 when redirected."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    main()
