"""Writing and naming evolved decklists on disk.

One format, shared by every producer: the human-readable ``describe`` block,
a blank line, then a JSON blob. ``compare_decks.py`` and ``build_tts_sheets.py``
both locate that blob as the last ``{`` after the final blank line, so the
readable half can grow without breaking them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Sequence

from .cards import CardDB
from .deckgen import describe

DECK_DIR = "decks"


def _blob(path: str | Path) -> dict:
    """The JSON half of one of our decklist files.

    Located as the last ``{`` after the final blank line, so the readable half
    above it can change freely. Raises if the file is not one of ours — callers
    scanning a directory should catch and skip.
    """
    text = Path(path).read_text()
    return json.loads(text[text.index("{", text.rindex("\n\n")):])


def read_deck(path: str | Path) -> list[str]:
    """The 60-card decklist out of a file written by ``write_deck``."""
    return list(_blob(path)["deck"])


def read_extra(path: str | Path) -> list[str]:
    """The EXTRA deck out of the same file.

    Absent in every list written before EXTRA decks existed, and absent in any
    deck that simply does not play them, so a missing key is empty rather than
    an error.
    """
    return list(_blob(path).get("extra") or [])


def read_decklist(path: str | Path) -> tuple[list[str], list[str]]:
    """Both piles at once: ``(deck, extra)``."""
    blob = _blob(path)
    return list(blob["deck"]), list(blob.get("extra") or [])


def deck_colors(deck: Sequence[str], db: CardDB) -> list[str]:
    """Colours present in a decklist, most-played first."""
    from collections import Counter
    counts = Counter(db[c].color.value for c in deck if c in db)
    return [color for color, _ in counts.most_common()]


def archetype_name(deck: Sequence[str], db: CardDB) -> str:
    """'BLUE' for a mono deck, 'BLUE+RED' for a splash, by card count."""
    return "+".join(deck_colors(deck, db)) or "colorless"


def run_tag(path: str | Path) -> str:
    """The version tag of a run, from its output filename.

    ``coevolved_v4.json`` -> ``v4``; anything unrecognised falls back to the
    stem, so a run named ``--out nightly.json`` still gets sensible filenames.
    """
    stem = Path(path).stem
    match = re.search(r"(v\d+)$", stem)
    return match.group(1) if match else stem.replace("coevolved_", "") or "run"


def deck_path(directory: str | Path, tag: str, archetype: str) -> Path:
    """``decks/v4_BLUE.txt`` — version first so a listing groups by run."""
    safe = re.sub(r"[^A-Za-z0-9+_-]", "_", archetype)
    return Path(directory) / f"{tag}_{safe}.txt"


def write_deck(path: str | Path, deck: Sequence[str], db: CardDB,
               extra: Sequence[str] | None = None, **meta) -> Path:
    """Write one decklist in the shared readable-plus-JSON format.

    ``extra`` is the EXTRA deck. It is written only when there is one, so a
    deck that does not play them produces the same file it always did.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = [f"# {key}: {value}" for key, value in meta.items()
              if not isinstance(value, (list, dict))]
    body = "\n".join(header) + ("\n\n" if header else "")
    extra = list(extra or [])
    readable = describe(deck, db)
    if extra:
        readable += "\n\nEXTRA deck\n" + describe(extra, db)
    blob = json.dumps({"deck": list(deck), **({"extra": extra} if extra else {}),
                       **meta}, indent=1)
    path.write_text(body + readable + "\n\n" + blob)
    return path


def write_archetypes(directory: str | Path, tag: str,
                     champions: dict[str, dict], db: CardDB) -> list[Path]:
    """Write one file per archetype: ``decks/<tag>_<ARCHETYPE>.txt``."""
    written = []
    for archetype, champ in sorted(champions.items()):
        written.append(write_deck(
            deck_path(directory, tag, archetype), champ["deck"], db,
            run=tag, archetype=archetype, round=champ.get("round"),
            holdout=champ.get("holdout"), validation=champ.get("validation"),
        ))
    return written
