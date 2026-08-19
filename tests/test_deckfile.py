"""Deck file naming and on-disk format. Run with: python -m pytest -q"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from braverse import default_db, starter_deck, validate
from braverse.deckfile import (archetype_name, deck_path, run_tag, write_deck,
                               write_archetypes)
from export_decks import best_by_archetype


@pytest.fixture(scope="module")
def db():
    return default_db()


def _parse(path):
    """Read a deck file exactly the way compare_decks.py does."""
    text = path.read_text()
    return json.loads(text[text.index("{", text.rindex("\n\n")):])


@pytest.mark.parametrize("out, tag", [
    ("coevolved_v4.json", "v4"),
    ("coevolved_v12.json", "v12"),
    ("/tmp/runs/coevolved_v7.json", "v7"),
    ("nightly.json", "nightly"),
])
def test_the_run_tag_comes_from_the_output_filename(out, tag):
    assert run_tag(out) == tag


def test_deck_files_are_named_for_the_run_and_archetype():
    assert deck_path("decks", "v4", "BLUE").as_posix() == "decks/v4_BLUE.txt"
    # A splash archetype keeps both colours, and nothing escapes the directory.
    assert deck_path("decks", "v4", "BLUE+RED").name == "v4_BLUE+RED.txt"
    # A hostile archetype label cannot climb out of the deck directory.
    escaped = deck_path("decks", "v4", "../etc/passwd")
    assert escaped.parent == Path("decks")
    assert "/" not in escaped.name and ".." not in escaped.name


def test_archetype_is_named_for_the_colours_actually_played(db):
    assert archetype_name(starter_deck(db, "ST9"), db) == "BLUE"
    splash = starter_deck(db, "ST9")[:50] + starter_deck(db, "ST8")[:10]
    assert archetype_name(splash, db) == "BLUE+GREEN"


def test_a_written_deck_round_trips_through_the_shared_parser(db, tmp_path):
    deck = starter_deck(db, "ST9")
    path = write_deck(tmp_path / "v9_BLUE.txt", deck, db,
                      run="v9", archetype="BLUE", round=3, holdout=0.8)
    blob = _parse(path)
    assert blob["deck"] == deck
    assert blob["holdout"] == 0.8 and blob["round"] == 3
    assert validate(blob["deck"], db).ok
    # The readable half is still there, above the blob.
    assert "COOKIE" in path.read_text()


def test_writing_archetypes_creates_the_directory_and_one_file_each(db, tmp_path):
    champions = {
        "BLUE": {"deck": starter_deck(db, "ST9"), "holdout": 0.8, "round": 2},
        "GREEN": {"deck": starter_deck(db, "ST8"), "holdout": 0.7, "round": 5},
    }
    out = tmp_path / "nested" / "decks"
    written = write_archetypes(out, "v4", champions, db)
    assert [p.name for p in written] == ["v4_BLUE.txt", "v4_GREEN.txt"]
    assert _parse(out / "v4_GREEN.txt")["round"] == 5


def test_the_best_round_per_archetype_wins(db):
    blue, green = starter_deck(db, "ST9"), starter_deck(db, "ST8")
    history = [
        {"round": 1, "champions": [{"archetype": "BLUE", "deck": blue, "holdout": 0.5},
                                   {"archetype": "GREEN", "deck": green, "holdout": 0.9}]},
        {"round": 2, "champions": [{"archetype": "BLUE", "deck": blue, "holdout": 0.8},
                                   {"archetype": "GREEN", "deck": green, "holdout": 0.6}]},
    ]
    best = best_by_archetype(history, db)
    assert best["BLUE"]["round"] == 2 and best["BLUE"]["holdout"] == 0.8
    assert best["GREEN"]["round"] == 1 and best["GREEN"]["holdout"] == 0.9


def test_a_run_from_before_per_archetype_still_splits_by_colour(db):
    """Older histories recorded one deck per round, with no archetype label."""
    history = [
        {"round": 1, "deck": starter_deck(db, "ST9"), "deck_holdout": 0.5},
        {"round": 2, "deck": starter_deck(db, "ST8"), "deck_holdout": 0.7},
        {"round": 3, "deck": starter_deck(db, "ST9"), "deck_holdout": 0.6},
    ]
    best = best_by_archetype(history, db)
    assert set(best) == {"BLUE", "GREEN"}
    assert best["BLUE"]["round"] == 3      # 0.6 beats round 1's 0.5
    assert best["GREEN"]["round"] == 2
