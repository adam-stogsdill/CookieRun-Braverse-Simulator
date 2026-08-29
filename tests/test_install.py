"""The installer, and the folders it exists to create.

What is worth pinning here is not that `shutil.copy2` works. It is the three
promises the folders make, each of which is silent when broken:

- the game *reads* the folders the installer creates, from where the installer
  puts them — a `card_images/` nobody looks in is a folder of dead files;
- installing again keeps what the player put there, because that is the upgrade
  path, and uninstalling keeps it too;
- a shortcut runs the game *from the install folder*, because the game keeps
  decks and profiles beside its executable — a shortcut with the wrong working
  directory plays fine and saves into the wrong place.

The platform-specific halves are checked by building what they would write and
reading it, rather than by being on that platform, the same way
`test_windows.py` does.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import install
import play_server


@pytest.fixture()
def fake_game(tmp_path: Path) -> Path:
    """Something executable to install, that is not 200 MB."""
    binary = tmp_path / "src" / install.EXE_NAME
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\necho game\n", encoding="utf-8")
    binary.chmod(0o755)
    return binary


# --- the layout ------------------------------------------------------------
def test_install_makes_the_folders_and_explains_each(fake_game: Path, tmp_path: Path):
    target = tmp_path / "install"
    installed = install.install(fake_game, target)

    assert installed.is_file() and os.access(installed, os.X_OK)
    for name in install.FOLDERS:
        folder = target / name
        assert folder.is_dir(), f"{name} was not created"
        # The folder is the interface. A player who never reads a command line
        # finds out what goes in it from the file sitting in it.
        assert (folder / "README.txt").read_text(encoding="utf-8").strip()
    assert "decks" in (target / "README.txt").read_text(encoding="utf-8")


def test_the_folders_are_the_ones_the_game_reads(fake_game: Path, tmp_path: Path,
                                                 monkeypatch):
    """Every folder the installer makes is one `play_server` looks in.

    This is the join between the two files, and the one that rots: renaming a
    directory on either side leaves an installer that makes a folder the game
    ignores, and nothing fails until someone's deck does not appear.
    """
    target = tmp_path / "install"
    install.install(fake_game, target)
    monkeypatch.setattr(play_server, "SIDE", target)

    read_by_the_game = {
        "agents": play_server.AGENT_DIR,
        "decks": play_server.DECK_DIR,
        "card_images": play_server.CARD_DIR,
        "profiles": play_server.PROFILE_DIR_NAME,
        "replays": play_server.REPLAY_DIR_NAME,
    }
    assert set(read_by_the_game) == set(install.FOLDERS)
    for made, expected in read_by_the_game.items():
        assert made == expected, f"the game reads {expected!r}, the installer makes {made!r}"


def test_a_deck_dropped_in_the_folder_is_offered(fake_game: Path, tmp_path: Path,
                                                 monkeypatch):
    """The end of the trip: a file in `decks/` reaches the deck menu."""
    from braverse import default_db, starter_deck
    from braverse.deckfile import write_deck

    target = tmp_path / "install"
    install.install(fake_game, target)
    monkeypatch.setattr(play_server, "SIDE", target)
    monkeypatch.setattr(play_server, "ROOT", target)   # no repo decks bleeding in
    play_server.load_saved_decks.cache_clear() if hasattr(
        play_server.load_saved_decks, "cache_clear") else None

    db = default_db()
    write_deck(target / "decks" / "handed_to_me.txt", starter_deck(db, "ST9"), db=db)
    assert "handed_to_me" in play_server.available_decks()


def test_card_art_in_the_folder_beats_the_bundled_library(tmp_path: Path, monkeypatch):
    """A `.webp` a player drops in is served instead of the built-in one.

    Without this the folder is decoration: the art is baked into the binary and
    `fetch_images.py` is not in the bundle, so this override is the only way a
    card printed after the build ever gets a picture.
    """
    bundled, side = tmp_path / "bundled", tmp_path / "beside"
    (side / play_server.CARD_DIR).mkdir(parents=True)
    bundled.mkdir()
    (bundled / "ST9-007.webp").write_bytes(b"bundled")
    (bundled / "ST9-008.webp").write_bytes(b"bundled")
    (side / play_server.CARD_DIR / "ST9-007.webp").write_bytes(b"mine")

    monkeypatch.setattr(play_server, "IMAGES", bundled)
    monkeypatch.setattr(play_server, "SIDE", side)

    assert play_server.card_image("ST9-007.webp").read_bytes() == b"mine"
    # Not an all-or-nothing switch: one replaced card leaves the rest bundled.
    assert play_server.card_image("ST9-008.webp").read_bytes() == b"bundled"
    # A card in neither place still resolves to a path, and 404s as a missing
    # file rather than raising out of the handler.
    assert not play_server.card_image("nope.webp").is_file()


# --- upgrading and removing ------------------------------------------------
def test_installing_again_keeps_what_the_player_put_there(fake_game: Path,
                                                          tmp_path: Path):
    target = tmp_path / "install"
    install.install(fake_game, target)
    mine = target / "decks" / "mine.txt"
    mine.write_text("my deck", encoding="utf-8")
    (target / "profiles" / "me.profile").write_text("sealed", encoding="utf-8")

    newer = fake_game.parent / "newer"
    newer.write_text("#!/bin/sh\necho newer\n", encoding="utf-8")
    install.install(newer, target)

    assert mine.read_text(encoding="utf-8") == "my deck"
    assert (target / "profiles" / "me.profile").is_file()
    assert "newer" in (target / install.EXE_NAME).read_text(encoding="utf-8")


def test_installing_over_itself_is_refused(fake_game: Path, tmp_path: Path):
    """Copying a file onto itself truncates it. The install that follows an
    install run from inside the install folder must not eat the game."""
    target = tmp_path / "install"
    installed = install.install(fake_game, target)
    with pytest.raises(SystemExit):
        install.install(installed, target)
    assert installed.read_text(encoding="utf-8").strip()


def test_uninstall_leaves_decks_and_profiles(fake_game: Path, tmp_path: Path,
                                             monkeypatch):
    target = tmp_path / "install"
    install.install(fake_game, target)
    (target / "decks" / "mine.txt").write_text("my deck", encoding="utf-8")
    monkeypatch.setattr(install, "shortcut_paths", list)

    install.uninstall(target)

    assert not (target / install.EXE_NAME).exists()
    assert (target / "decks" / "mine.txt").read_text(encoding="utf-8") == "my deck"


def test_purge_says_so_and_removes_everything(fake_game: Path, tmp_path: Path,
                                              monkeypatch):
    target = tmp_path / "install"
    install.install(fake_game, target)
    (target / "decks" / "mine.txt").write_text("my deck", encoding="utf-8")
    monkeypatch.setattr(install, "shortcut_paths", list)

    install.uninstall(target, keep_data=False)
    assert not target.exists()


def test_uninstall_only_claims_to_keep_real_files(fake_game: Path, tmp_path: Path,
                                                  monkeypatch, capsys):
    """The read-me the installer wrote is not the player's — a folder holding
    only that one is empty, and must not be reported as something kept."""
    target = tmp_path / "install"
    install.install(fake_game, target)
    (target / "decks" / "mine.txt").write_text("my deck", encoding="utf-8")
    monkeypatch.setattr(install, "shortcut_paths", list)

    install.uninstall(target)
    said = capsys.readouterr().out
    assert "decks" in said
    assert "profiles" not in said


# --- shortcuts -------------------------------------------------------------
def test_windows_shortcut_runs_from_the_install_folder(tmp_path: Path):
    """The .lnk's working directory is where the game keeps things.

    Built as text and read back, because `WScript.Shell` only exists on
    Windows and this has to be checked from any machine.
    """
    installed = tmp_path / "Braverse" / "braverse.exe"
    script = install.windows_shortcut_script(installed, tmp_path / "game.lnk")
    assert f"$s.TargetPath = '{installed}'" in script
    assert f"$s.WorkingDirectory = '{installed.parent}'" in script


def test_macos_app_points_at_the_binary_rather_than_copying_it(tmp_path: Path):
    """Upgrading replaces the binary; the shortcut has to survive that, so the
    bundle holds a launcher and not a copy of a 200 MB game."""
    installed = tmp_path / "Braverse" / "braverse"
    installed.parent.mkdir()
    installed.write_text("game", encoding="utf-8")

    (app,) = install.macos_app(installed, tmp_path / "Applications")
    launcher = app / "Contents" / "MacOS" / "launch"

    assert (app / "Contents" / "Info.plist").is_file()
    assert os.access(launcher, os.X_OK)
    body = launcher.read_text(encoding="utf-8")
    assert str(installed) in body
    # Launched from the Dock a bundle starts at /, and the game would keep its
    # profiles there. The cd is what makes the install folder mean anything.
    assert f'cd "{installed.parent}"' in body
    assert app.stat().st_size < 1_000_000


def test_linux_desktop_entry_sets_its_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    installed = tmp_path / "braverse" / "braverse"
    installed.parent.mkdir(parents=True)
    installed.write_text("game", encoding="utf-8")

    (entry,) = install.linux_desktop(installed)
    body = entry.read_text(encoding="utf-8")
    assert f"Exec={installed}" in body
    assert f"Path={installed.parent}" in body


# --- the platform edges ----------------------------------------------------
def test_the_default_is_somewhere_the_player_can_write():
    """Not /Applications, not Program Files. The game writes profiles and
    replays beside itself; a read-only install directory sends them to
    ~/.braverse instead, and the folders the installer made stay empty."""
    where = str(install.default_dir())
    assert "Program Files" not in where
    assert not where.startswith("/Applications")


def test_every_file_it_writes_is_utf8_with_lf(tmp_path: Path, fake_game: Path):
    """These read-mes are opened in Notepad on a machine whose default encoding
    is cp1252, and every one of them is full of em dashes."""
    target = tmp_path / "install"
    install.install(fake_game, target)
    for readme in target.rglob("README.txt"):
        raw = readme.read_bytes()
        assert b"\r\n" not in raw
        raw.decode("utf-8")     # raises if it was written in the local encoding


def test_it_retunes_its_own_output():
    """It is frozen alone, with no `braverse` package beside it, so it cannot
    import `braverse.console` — it carries its own copy of `utf8_output`."""
    assert hasattr(install, "utf8_output")
    source = (Path(install.__file__).read_text(encoding="utf-8"))
    assert "utf8_output()" in source.split("def main(")[1]


def test_asking_answers_itself_when_nobody_is_there(monkeypatch):
    """A build machine, a CI job, or a double-clicked binary whose stdin is not
    a console must not block forever on a question no one can see."""
    monkeypatch.setattr(sys, "stdin", None)
    assert install.ask("where", "/somewhere", assume_yes=False) == "/somewhere"
    assert install.ask_yes("shortcut", True, assume_yes=False) is True
    assert install.ask_yes("shortcut", False, assume_yes=False) is False


def test_it_finds_the_game_next_to_itself(tmp_path: Path, monkeypatch):
    """Unzip and double-click: the installer is beside the game, under whatever
    name the release used."""
    monkeypatch.setattr(install, "__file__", str(tmp_path / "install.py"))
    named = tmp_path / f"braverse-9.9.9-macos-arm64{'.exe' if install.WINDOWS else ''}"
    named.write_text("game", encoding="utf-8")
    named.chmod(0o755)
    (tmp_path / "install-braverse").write_text("not the game", encoding="utf-8")

    assert install.find_binary(None) == named

    plain = tmp_path / install.EXE_NAME
    plain.write_text("game", encoding="utf-8")
    plain.chmod(0o755)
    assert install.find_binary(None) == plain     # the plain name wins


# --- the game it carries ---------------------------------------------------
def test_no_payload_when_running_from_a_checkout():
    """`sys._MEIPASS` only exists inside a frozen build. From source there is
    nothing carried, and `--binary` is how the build says what to install."""
    assert install.payload() is None


def test_the_carried_game_wins_over_one_lying_beside_it(tmp_path, monkeypatch):
    """The whole point of the payload: the installer is the only file someone
    needs, so it must not depend on — or be confused by — its surroundings."""
    meipass = tmp_path / "unpacked"
    (meipass / install.PAYLOAD_DIR).mkdir(parents=True)
    carried = meipass / install.PAYLOAD_DIR / install.EXE_NAME
    carried.write_text("the carried game", encoding="utf-8")

    beside = tmp_path / "beside"
    beside.mkdir()
    (beside / install.EXE_NAME).write_text("some other build", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(sys, "executable", str(beside / "install-braverse"))

    found = install.find_binary(None)
    assert found == carried
    # Unpacking does not preserve the executable bit; installing a game that
    # cannot be run is a bug report about the game, not about the installer.
    assert os.access(found, os.X_OK)


def test_an_explicit_binary_still_wins(tmp_path, monkeypatch):
    """`--binary` is what a checkout uses, and it has to beat a stale payload."""
    meipass = tmp_path / "unpacked"
    (meipass / install.PAYLOAD_DIR).mkdir(parents=True)
    (meipass / install.PAYLOAD_DIR / install.EXE_NAME).write_text("old", encoding="utf-8")
    asked = tmp_path / "fresh"
    asked.write_text("new", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

    assert install.find_binary(str(asked)) == asked


def test_the_shortcut_wears_the_icon_when_there_is_one(tmp_path, monkeypatch):
    """macOS draws a one-file binary with a generic icon whatever is inside it,
    so this bundle is the only thing that can carry the game's face."""
    icon = tmp_path / install.ICON_NAME
    icon.write_bytes(b"icns" + b"\x00" * 32)
    monkeypatch.setattr(install, "icon_file", lambda: icon)

    installed = tmp_path / "Braverse" / "braverse"
    installed.parent.mkdir()
    installed.write_text("game", encoding="utf-8")

    (app,) = install.macos_app(installed, tmp_path / "Applications")
    plist = (app / "Contents" / "Info.plist").read_text(encoding="utf-8")
    assert (app / "Contents" / "Resources" / install.ICON_NAME).is_file()
    assert f"<string>{install.ICON_NAME}</string>" in plist


def test_a_build_with_no_icon_still_makes_a_valid_bundle(tmp_path, monkeypatch):
    """The icon is optional; the shortcut is not."""
    monkeypatch.setattr(install, "icon_file", lambda: None)
    installed = tmp_path / "Braverse" / "braverse"
    installed.parent.mkdir()
    installed.write_text("game", encoding="utf-8")

    (app,) = install.macos_app(installed, tmp_path / "Applications")
    plist = (app / "Contents" / "Info.plist").read_text(encoding="utf-8")
    assert "CFBundleIconFile" not in plist
    assert "<key>CFBundleExecutable</key><string>launch</string>" in plist
    assert not (app / "Contents" / "Resources").exists()


# --- what actually gets shipped --------------------------------------------
def test_the_release_ships_the_installer_alone(tmp_path):
    """The installer carries the game, so shipping the game beside it would
    double a 200 MB download to say the same thing twice."""
    import zipfile
    from types import SimpleNamespace

    import build_release

    game = tmp_path / "braverse"
    game.write_text("game", encoding="utf-8")
    installer = tmp_path / "install-braverse"
    installer.write_text("installer with a game inside", encoding="utf-8")
    args = SimpleNamespace(out=str(tmp_path / "release"), no_zip=False, no_images=False)

    archive = build_release.package(game, installer, args, "macos-arm64", "9.9.9")

    names = sorted(zipfile.ZipFile(archive).namelist())
    assert names == ["braverse-9.9.9-macos-arm64/README.txt",
                     "braverse-9.9.9-macos-arm64/install-braverse"]
    notes = zipfile.ZipFile(archive).read(names[0]).decode()
    assert "Install it" in notes


def test_without_an_installer_the_bare_game_ships(tmp_path):
    import zipfile
    from types import SimpleNamespace

    import build_release

    game = tmp_path / "braverse"
    game.write_text("game", encoding="utf-8")
    args = SimpleNamespace(out=str(tmp_path / "release"), no_zip=False, no_images=True)

    archive = build_release.package(game, None, args, "macos-arm64", "9.9.9")
    names = sorted(zipfile.ZipFile(archive).namelist())
    assert names == ["braverse-9.9.9-macos-arm64/README.txt",
                     "braverse-9.9.9-macos-arm64/braverse"]
