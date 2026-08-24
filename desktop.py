#!/usr/bin/env python3
"""Show the visual player in its own window instead of a browser tab.

The game itself does not change: `play_server.py` still serves the same
`viewer/` over HTTP on localhost, and this module only decides what *displays*
it. Two ways, best first:

- **pywebview** (`pip install pywebview`) — a real native window, drawn by the
  OS web view (WebKit on macOS, WebView2 on Windows, WebKitGTK on Linux). No
  address bar, no tabs, and closing it stops the server.
- **A Chromium browser in app mode** (`--app=`), if one is installed. Same
  chromeless window, but it is a browser process, so it gets its own profile
  directory under the user's cache — without one, launching Chrome while Chrome
  is already running just hands the URL to the existing process and exits,
  which would look like the window closing the moment it opened.

`open_window` returns something the caller can wait on, or None when neither
way is available; nothing here ever raises just because a desktop is missing,
so a headless machine falls back to printing the URL.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

TITLE = "CookieRun: Braverse"
SIZE = (1440, 900)
MIN_SIZE = (1024, 700)


def have_webview() -> bool:
    """Whether the native-window backend is importable."""
    try:
        import webview  # noqa: F401
    except Exception:
        return False
    return True


def available() -> bool:
    """Whether this machine can draw a window at all."""
    return have_webview() or _chromium() is not None


def run_webview(url: str, on_close: Optional[Callable[[], None]] = None) -> None:
    """Open a native window on ``url`` and block until it is closed.

    Must be called from the main thread — every OS web view insists on it —
    which is why the caller serves HTTP on a thread instead.
    """
    import webview

    window = webview.create_window(
        TITLE, url, width=SIZE[0], height=SIZE[1], min_size=MIN_SIZE,
    )
    if on_close is not None:
        window.events.closed += lambda: on_close()
    webview.start()


# Where a Chromium-family browser lives, per platform. Windows is a product of
# every install root and every browser, because Chrome installs per-machine
# under Program Files or per-user under LOCALAPPDATA depending on who ran the
# installer, and both are ordinary.
MAC_BROWSERS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
WINDOWS_BROWSERS = (
    r"Google\Chrome\Application\chrome.exe",
    r"Microsoft\Edge\Application\msedge.exe",
    r"BraveSoftware\Brave-Browser\Application\brave.exe",
    r"Chromium\Application\chrome.exe",
)
UNIX_BROWSERS = ("google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "microsoft-edge", "brave-browser")


def windows_candidates() -> list:
    """Every path a Chromium browser might be installed at on Windows."""
    roots = [os.environ.get("PROGRAMFILES", r"C:\Program Files"),
             os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
             os.environ.get("LOCALAPPDATA", "")]
    # `Path(root) / rel` is right on Windows and wrong elsewhere (a backslash is
    # a legal file-name character on POSIX, not a separator), so tests that fake
    # Windows go through PureWindowsPath explicitly.
    from pathlib import PureWindowsPath
    return [str(PureWindowsPath(root) / rel)
            for root in roots if root for rel in WINDOWS_BROWSERS]


def _chromium() -> Optional[str]:
    """Path to an installed Chromium-family browser, or None."""
    if sys.platform == "darwin":
        candidates = list(MAC_BROWSERS)
    elif sys.platform == "win32":
        candidates = windows_candidates()
    else:
        candidates = [shutil.which(name) for name in UNIX_BROWSERS]
        candidates = [c for c in candidates if c]
    for path in candidates:
        if Path(path).exists():
            return path
    # Last resort on any platform: whatever is on PATH under a known name.
    for name in UNIX_BROWSERS + ("chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _app_profile() -> Path:
    """A profile directory of our own, so the window is our own process."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    path = base / "braverse-window"
    path.mkdir(parents=True, exist_ok=True)
    return path


_proc: Optional[subprocess.Popen] = None


def run_app_mode(url: str) -> Optional[subprocess.Popen]:
    """Launch a chromeless browser window on ``url``; None if none installed."""
    global _proc
    browser = _chromium()
    if browser is None:
        return None
    try:
        _proc = subprocess.Popen(
            [browser, f"--app={url}",
             f"--user-data-dir={_app_profile()}",
             f"--window-size={SIZE[0]},{SIZE[1]}",
             "--no-first-run", "--no-default-browser-check"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return _proc
    except OSError:
        return None


def close_window() -> None:
    """Tear the window down from outside — a signal, usually. Safe if there is none."""
    global _proc
    try:
        import webview
        for window in list(getattr(webview, "windows", [])):
            window.destroy()
    except Exception:
        pass
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()


def open_window(url: str, on_close: Optional[Callable[[], None]] = None) -> str:
    """Show ``url`` in a window, blocking until it closes.

    Returns which backend was used: ``"webview"``, ``"app"``, or ``""`` when
    the machine has neither and the caller should keep serving to a browser.
    ``on_close`` runs once the window is gone, either way.
    """
    if have_webview():
        try:
            run_webview(url, on_close)
            return "webview"
        except Exception as exc:
            # pywebview *imports* on any platform but only *runs* where its
            # backend does: WebView2 plus pythonnet on Windows, WebKitGTK on
            # Linux. `have_webview` cannot tell the difference — importing is
            # all it can do without opening a window — so a bundled copy that
            # cannot draw has to fall through here rather than take the game
            # down. The browser window below looks the same to a player.
            print(f"native window unavailable ({exc}); using a browser window")

    proc = run_app_mode(url)
    if proc is not None:
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
        if on_close is not None:
            on_close()
        return "app"

    return ""


INSTALL_HINT = ("no desktop window backend found — install one with:\n"
                "    pip install pywebview\n"
                "(or install Chrome/Edge, which can host the window too)")
