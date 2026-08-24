"""The platform-specific branches, exercised on whatever platform runs them.

Windows is not macOS in four ways that matter to this project and that a test
run on a Mac would otherwise never touch:

- signals: there is no `SIGHUP`, so naming it is an AttributeError at startup;
- `mimetypes` reads the registry, where `.js` is often `text/plain`;
- text files: the default encoding is cp1252, not UTF-8, and newlines get
  translated on write;
- `os.access(dir, W_OK)` claims every directory is writable.

Each is checked here by faking the platform rather than by being on it, so a
regression shows up in the ordinary `pytest -q` run on any machine.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
from pathlib import Path

import pytest

import desktop
import play_server
from braverse import default_db, starter_deck
from braverse.deckfile import read_decklist, write_deck


# --- signals ---------------------------------------------------------------
def test_no_signal_is_named_unconditionally():
    """`signal.SIGHUP` does not exist on Windows; it has to be looked up."""
    source = inspect.getsource(play_server.main)
    assert "signal.SIGHUP" not in source
    assert 'getattr(signal, name, None)' in source


def test_the_signal_set_is_whatever_exists():
    import signal
    for name in ("SIGTERM", "SIGHUP", "SIGINT", "SIGBREAK"):
        got = getattr(signal, name, None)
        assert got is None or isinstance(got, signal.Signals)


# --- content types ---------------------------------------------------------
def test_front_end_types_do_not_come_from_the_registry(monkeypatch):
    """A Windows registry that calls .js text/plain must not reach the browser."""
    monkeypatch.setattr(play_server.mimetypes, "guess_type",
                        lambda name: ("text/plain", None))
    served = []

    class Fake:
        _send = lambda self, code, body, ctype, cache=False: served.append(ctype)
        _file = play_server.Handler._file

    Fake()._file(play_server.VIEWER / "app.js")
    Fake()._file(play_server.VIEWER / "style.css")
    assert served == ["text/javascript; charset=utf-8", "text/css; charset=utf-8"]


def test_every_served_extension_has_a_type():
    """Nothing in `viewer/` may fall through to the platform's guess."""
    missing = {p.suffix.lower() for p in play_server.VIEWER.iterdir()
               if p.is_file() and p.suffix} - set(play_server.CONTENT_TYPES)
    assert not missing, f"add these to CONTENT_TYPES: {sorted(missing)}"


# --- text files ------------------------------------------------------------
def test_decklists_are_utf8_with_unix_newlines(tmp_path):
    """A decklist written on Windows has to be the same file as anywhere else."""
    db = default_db()
    deck = starter_deck(db, "ST8")
    path = write_deck(tmp_path / "w.txt", deck, db)
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    raw.decode("utf-8")                      # not cp1252, and not lossy
    assert read_decklist(path)[0] == deck


def test_saved_decks_store_round_trips_as_utf8(tmp_path, monkeypatch):
    monkeypatch.setattr(play_server, "deck_store", lambda: tmp_path / "d.json")
    play_server.write_saved_decks({"내 덱": ["ST8-001"]})
    raw = (tmp_path / "d.json").read_bytes()
    json.loads(raw.decode("utf-8"))
    assert play_server.load_saved_decks() == {"내 덱": (["ST8-001"], [])}


# --- writability -----------------------------------------------------------
def test_writable_probes_instead_of_asking(tmp_path):
    assert play_server.writable(tmp_path) is True
    assert play_server.writable(tmp_path / "does-not-exist") is False
    assert not list(tmp_path.iterdir())      # the probe cleans up after itself


# --- the port holder -------------------------------------------------------
NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1044
  TCP    127.0.0.1:8080         0.0.0.0:0              LISTENING       9312
  TCP    127.0.0.1:8080         127.0.0.1:52144        ESTABLISHED     4001
  TCP    [::]:8080              [::]:0                 LISTENING       9312
"""


def test_port_holder_reads_netstat_on_windows(monkeypatch):
    monkeypatch.setattr(play_server, "WINDOWS", True)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(
                            a, 0, stdout=NETSTAT, stderr=""))
    # The listener once, not once per address family, and not the connection to it.
    assert play_server.port_holder(8080) == "9312"
    assert play_server.port_holder(135) == "1044"
    assert play_server.port_holder(9999) == ""


def test_port_holder_survives_no_netstat(monkeypatch):
    monkeypatch.setattr(play_server, "WINDOWS", True)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert play_server.port_holder(8080) == ""


# --- the window ------------------------------------------------------------
def test_windows_browser_paths_cover_both_install_roots(monkeypatch):
    monkeypatch.setenv("PROGRAMFILES", r"C:\Program Files")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\a\AppData\Local")
    found = desktop.windows_candidates()
    assert all(p.endswith(".exe") and "\\" in p for p in found)
    assert any(p.startswith(r"C:\Program Files\Google\Chrome") for p in found)
    assert any(p.startswith(r"C:\Users\a\AppData\Local\Google\Chrome")
               for p in found)


def test_no_window_backend_is_not_an_error(monkeypatch):
    monkeypatch.setattr(desktop, "have_webview", lambda: False)
    monkeypatch.setattr(desktop, "run_app_mode", lambda url: None)
    closed = []
    assert desktop.open_window("http://127.0.0.1:1/", closed.append) == ""
    assert not closed          # nothing opened, so nothing to close


def test_close_window_is_safe_with_no_window():
    desktop.close_window()     # must not raise on a process with no window


# --- the command-line scripts ---------------------------------------------
def test_every_script_retunes_its_output():
    """A script that prints card text must survive a cp1252 stdout.

    Static rather than behavioural on purpose: the failure it guards against is
    a *new* script, and only reading the source can notice one that forgot.
    """
    root = Path(play_server.__file__).resolve().parent
    forgot = []
    for script in sorted(root.glob("*.py")):
        text = script.read_text(encoding="utf-8")
        if "def main(" not in text or script.name == "desktop.py":
            continue
        if "utf8_output()" not in text:
            forgot.append(script.name)
    assert not forgot, f"call braverse.console.utf8_output() in: {forgot}"


def test_utf8_output_is_not_called_at_import():
    """Retuning the process's streams on import would break the host."""
    from braverse import console
    assert "utf8_output()" not in inspect.getsource(console).split("def utf8_output")[0]
    for module in (play_server, desktop):
        top = inspect.getsource(module).split("def ")[0]
        assert "utf8_output()" not in top
