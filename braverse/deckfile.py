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
    text = Path(path).read_text(encoding="utf-8")
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


def read_any(path: str | Path, db: CardDB | None = None
             ) -> tuple[list[str], list[str]]:
    """``(deck, extra)`` from *any* decklist file a person might point at.

    ``read_decklist`` reads the format we write, and raises on anything else —
    which is what a person gets for pointing `--seed-deck` at the list they
    exported from the deck builder, or typed by hand, or was mailed by a
    friend. Those are the same lists `parse_decklist` already reads, so fall
    back to it rather than failing: a file that names 60 cards is a deck
    whatever shape it names them in.

    Cards it cannot place are dropped the way the importer drops them, so a
    caller that cares about the size should `validate` what comes back.
    """
    path = Path(path)
    try:
        return read_decklist(path)
    except (ValueError, KeyError, json.JSONDecodeError):
        pass
    from .cards import default_db
    imported = parse_decklist(path.read_text(encoding="utf-8"), db or default_db())
    return imported.deck, imported.extra


META_DIR = "decks/meta"       # tournament lists, one folder, read as a set


def read_pool(directory: str | Path = META_DIR,
              recursive: bool = False) -> list[tuple[str, list[str], list[str]]]:
    """Every decklist in one folder, as ``(name, deck, extra)``, name-sorted.

    A folder of lists is a different thing from a folder of decks you might
    pick one of: it is a *pool*, meant to be trained or evolved against as a
    set, which is why it is read in one call and why the order is fixed. Sorted
    by name, because a training run seeded the same way must see the same decks
    in the same order or it is not the run it says it is.

    ``recursive`` reads the subfolders too — ``decks/`` as a whole rather than
    one folder inside it — and names those lists by their path relative to the
    folder (``green_run/gen012``), because two runs both ending in a
    ``_best.txt`` are two different decks and a pool that calls them the same
    thing cannot report which one it trained on.

    Identical lists are read once. A pool is a set, and a folder collected over
    time holds the same 60 cards under several names — an evolution run whose
    last ten generations never changed, a deck saved again under a tidier name.
    Left in, each copy is another share of the training games spent on one
    deck, which is a weighting nobody chose. The first name in sorted order
    keeps its place, so the order stays a function of the filenames alone.

    Files that are not ours are skipped rather than raising, the way
    ``play_server.available_decklists`` skips them: a folder people drop lists
    into will eventually have a README or a half-written file in it.
    """
    root = Path(directory)
    paths = sorted(root.rglob("*.txt") if recursive else root.glob("*.txt"))
    pool: list[tuple[str, list[str], list[str]]] = []
    seen: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    for path in paths:
        try:
            deck, extra = read_decklist(path)
        except Exception:
            continue
        if len(deck) < 10:
            continue
        key = (tuple(sorted(deck)), tuple(sorted(extra)))
        if key in seen:
            continue
        seen.add(key)
        name = path.relative_to(root).with_suffix("").as_posix() if recursive else path.stem
        pool.append((name, deck, extra))
    return pool


# ---------------------------------------------------------------------------
# reading a decklist somebody else wrote
# ---------------------------------------------------------------------------
# The format above is ours, and anything this project wrote can be read back
# exactly. A decklist that arrives from outside is a different problem: it was
# copied off a website, typed out, exported by another tool, or pasted into a
# chat window and pasted back out. `parse_decklist` is deliberately generous
# about all of that, and deliberately loud about what it could not use — an
# importer that silently drops four cards produces a deck that is wrong in a
# way nobody notices until a game goes strangely.
#
# Understood, in order of preference:
#
#     {"deck": [...], "extra": [...]}     our own files, exactly
#     --COOKIE--                          the viewer's Export sections
#     3x Sea Fairy Cookie ST9-001 LV2
#     3 ST9-001 Sea Fairy Cookie          the viewer's Copy button
#     ST9-001 x3
#     3x Sea Fairy Cookie                 no id at all — resolved by name
#     ST9-001                             one copy
#
# An id is authoritative when there is one; a name is a guess, because 271 of
# the 813 card names in the database are printed on more than one card, and a
# guess is reported as one.

_ID = re.compile(r"\b([A-Za-z]{1,6}\d*-\d+[A-Za-z]?)\b")
_COUNT_LEAD = re.compile(r"^\s*(\d{1,3})\s*[x*]?\s+", re.I)
_COUNT_TRAIL = re.compile(r"[\s(]*[x*]\s*(\d{1,3})\s*\)?\s*$", re.I)
_SECTION = re.compile(r"^\s*[-=*#\s]*([A-Za-z ]{3,20}?)[-=*\s:]*$")
_LEVEL = re.compile(r"\b(?:LV|LEVEL)\s*\d+\b", re.I)
_MAX_CARDS = 400            # a 60-card deck with room to be mid-edit


class ImportedDeck:
    """What one pasted or dropped decklist turned out to be.

    `skipped` and `notes` are the point as much as `deck` is: they are what the
    importer shows so the person can see that their 60-card list came in as 56
    and why, rather than finding out during a game.
    """

    def __init__(self, deck=None, extra=None, name="", notes=None, skipped=None):
        self.deck: list[str] = list(deck or [])
        self.extra: list[str] = list(extra or [])
        self.name: str = name
        self.notes: list[str] = list(notes or [])
        self.skipped: list[str] = list(skipped or [])

    def __repr__(self) -> str:      # pragma: no cover - debugging only
        return (f"ImportedDeck(name={self.name!r}, deck={len(self.deck)}, "
                f"extra={len(self.extra)}, skipped={len(self.skipped)})")


def _json_decklist(text: str) -> dict | None:
    """The JSON half of one of our own files, if this text has one."""
    for start in (text.rfind("{"), text.find("{")):
        if start < 0:
            continue
        try:
            blob = json.loads(text[start:])
        except ValueError:
            continue
        if isinstance(blob, dict) and isinstance(blob.get("deck"), list):
            return blob
    return None


def _count_and_rest(line: str) -> tuple[int, str]:
    """How many copies this line asks for, and the line without that part."""
    lead = _COUNT_LEAD.match(line)
    if lead:
        return int(lead.group(1)), line[lead.end():]
    trail = _COUNT_TRAIL.search(line)
    if trail:
        return int(trail.group(1)), line[:trail.start()]
    return 1, line


def _resolve(line: str, db: CardDB) -> tuple[str | None, str]:
    """One line to a card id, plus a note when the answer was a guess."""
    for match in _ID.finditer(line):
        found = match.group(1)
        if found in db:
            return found, ""
        upper = found.upper()
        if upper in db:
            return upper, ""

    # No id, or none we know: what is left ought to be a name. Strip the
    # decorations the export formats add — a Level, a trailing set in
    # brackets, the punctuation people separate columns with.
    name = _LEVEL.sub("", line)
    name = re.sub(r"[\[(][^\])]*[\])]", "", name)
    name = name.strip(" \t-·|,:;\u2014\u2013")
    if not name:
        return None, ""
    matches = db.by_name(name)
    if not matches:
        return None, ""
    if len(matches) == 1:
        return matches[0].id, ""
    # Printed more than once. Take the lowest id so the same list always
    # imports the same way, and say so — this is the one place the importer
    # decides something the file did not.
    pick = sorted(m.id for m in matches)[0]
    return pick, (f"{name} is printed on {len(matches)} cards — took {pick}; "
                  f"the deck builder can swap it")


def parse_decklist(text: str, db: CardDB) -> ImportedDeck:
    """Read a decklist somebody else wrote, as generously as is honest.

    Never raises on bad input: a line that means nothing lands in `skipped`,
    because the caller's job is to show the person what did not come through.
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    blob = _json_decklist(text)
    if blob is not None:
        deck = [str(c) for c in blob.get("deck") or []][:_MAX_CARDS]
        extra = [str(c) for c in blob.get("extra") or []][:_MAX_CARDS]
        unknown = sorted({c for c in (*deck, *extra) if c not in db})
        return ImportedDeck(
            deck=[c for c in deck if c in db],
            extra=[c for c in extra if c in db],
            name=str(blob.get("name") or blob.get("archetype") or ""),
            skipped=[f"unknown card id: {c}" for c in unknown],
        )

    result = ImportedDeck()
    in_extra = False
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("#"):
            key = re.match(r"#\s*name\s*:\s*(.+)", line, re.I)
            if key:
                result.name = key.group(1).strip()
            continue

        # A section header switches piles rather than naming a card. Matched
        # before the card line so `--EXTRA--` is never read as a card called
        # EXTRA, and loosely, because people write `EXTRA:` and `== EXTRA ==`.
        header = _SECTION.match(line)
        if header and not _ID.search(line) and not line[0].isdigit():
            word = header.group(1).strip().upper()
            if word in ("EXTRA", "EXTRA DECK"):
                in_extra = True
                continue
            if word in ("COOKIE", "FLIP", "ITEM", "TRAP", "STAGE", "NPC",
                        "MAIN", "MAIN DECK", "DECK"):
                in_extra = False
                continue

        count, rest = _count_and_rest(line)
        card_id, note = _resolve(rest, db)
        if card_id is None:
            # Prose from a `describe` block, a URL, a stray column header. Only
            # worth reporting if it looked like it was trying to be a card.
            if len(line) < 80 and not line.endswith(":"):
                result.skipped.append(line)
            continue
        if note:
            result.notes.append(note)

        count = max(1, min(count, 60))
        pile = (result.extra if in_extra or db[card_id].type.value == "EXTRA"
                else result.deck)
        room = _MAX_CARDS - (len(result.deck) + len(result.extra))
        if room <= 0:
            result.skipped.append(f"stopped at {_MAX_CARDS} cards: {line}")
            break
        pile.extend([card_id] * min(count, room))
    return result


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
        readable += "\n\nEXTRA deck\n" + describe(extra, db, legality=False)
    blob = json.dumps({"deck": list(deck), **({"extra": extra} if extra else {}),
                       **meta}, indent=1)
    # UTF-8 and LF explicitly: card names are not ASCII, and a decklist
    # written on Windows should be the same bytes as one written anywhere
    # else — these files get mailed around.
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(body + readable + "\n\n" + blob)
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
