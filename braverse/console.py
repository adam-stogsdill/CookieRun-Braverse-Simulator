"""Making a terminal safe to print this game's text into.

Card names, `【 】`, ★ and the em dashes every script uses are outside the
Windows default code page. The console itself copes — CPython talks to it in
UTF-16 — but a *redirected* stream does not: `python selfplay.py > runs.log` on
Windows encodes as cp1252, and the first unrepresentable character ends the run
with a UnicodeEncodeError from inside a `print`, which is a confusing way to
lose an hour of training.

Called from each script's `main`, never at import: a library that retunes the
process's stdout when someone imports it is a library that breaks its host.
"""

from __future__ import annotations

import sys


def utf8_output() -> None:
    """Make stdout and stderr encode any of this game's text, everywhere."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # Not a text stream, or already replaced by something that cannot be
            # retuned (pytest's capture, a StringIO). Nothing to do, and nothing
            # to complain about.
            pass
