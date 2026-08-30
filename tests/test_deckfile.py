"""Deck file naming and on-disk format. Run with: python -m pytest -q"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from braverse import default_db, starter_deck, validate
from braverse.deckfile import (META_DIR, archetype_name, deck_path, read_any,
                               read_pool, run_tag, write_deck, write_archetypes)
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


# --- the tournament pool ----------------------------------------------------
def test_a_pool_is_every_list_in_one_folder_in_a_fixed_order(db, tmp_path):
    """A pool is read as a set, and the order cannot depend on the filesystem.

    A training run seeded the same way has to see the same decks in the same
    order, or the seed does not describe the run.
    """
    write_deck(tmp_path / "b_second.txt", starter_deck(db, "ST9"), db)
    write_deck(tmp_path / "a_first.txt", starter_deck(db, "ST8"), db)
    (tmp_path / "README.txt").write_text("not a decklist", encoding="utf-8")
    (tmp_path / "half-written.txt").write_text("{", encoding="utf-8")

    pool = read_pool(tmp_path)
    assert [name for name, _, _ in pool] == ["a_first", "b_second"]
    assert all(len(deck) == 60 for _, deck, _ in pool)
    assert all(extra == [] for _, _, extra in pool)


def test_a_recursive_pool_is_the_whole_tree_read_once(db, tmp_path):
    """`--deck-pool decks --deck-pool-subfolders`: every list under a folder.

    Two things are pinned. A list in a subfolder is named by its path, because
    two evolution runs both ending in `_best.txt` are two different decks and a
    pool that calls them the same thing cannot say which one it trained on. And
    the same 60 cards saved twice are read once — a folder collected over time
    is full of copies, and left in, each one is another share of the training
    games spent on one deck.
    """
    st9, st8 = starter_deck(db, "ST9"), starter_deck(db, "ST8")
    (tmp_path / "run").mkdir()
    write_deck(tmp_path / "loose.txt", st9, db)
    write_deck(tmp_path / "run" / "gen000.txt", st8, db)
    write_deck(tmp_path / "run" / "gen001.txt", st8, db)     # unchanged copy

    assert [name for name, _, _ in read_pool(tmp_path)] == ["loose"]
    names = [name for name, _, _ in read_pool(tmp_path, recursive=True)]
    assert names == ["loose", "run/gen000"]


def test_the_tournament_pool_on_disk_is_legal_and_reachable(db):
    """The lists imported from topdeck.gg, checked as decks rather than files.

    They are what `train_rl.py --deck-pool` and `evolve_deck.py --gauntlet`
    read by default, so a list that stopped validating would be trained
    against without anything else noticing.
    """
    pool = read_pool(META_DIR)
    assert len(pool) >= 18
    for name, deck, extra in pool:
        report = validate(deck, db, extra=extra)
        assert report.ok, (name, report.problems)
        assert report.size == 60, name


def test_read_any_falls_back_to_the_importer_for_a_hand_written_list(db, tmp_path):
    """A list somebody exported or typed has no JSON blob and must still load."""
    path = tmp_path / "typed.txt"
    path.write_text("--COOKIE--\n4x Aloe Cookie ST3-010 LV2\n\n"
                    "--EXTRA--\n2x Peak of Apathy BS8-069 LV2\n",
                    encoding="utf-8")
    deck, extra = read_any(path)
    assert deck == ["ST3-010"] * 4
    assert extra == ["BS8-069"] * 2


def test_read_any_still_prefers_our_own_format(db, tmp_path):
    path = write_deck(tmp_path / "ours.txt", starter_deck(db, "ST9"), db,
                      extra=["BS8-069"])
    deck, extra = read_any(path)
    assert len(deck) == 60 and extra == ["BS8-069"]


def test_an_extra_pile_is_not_judged_by_the_main_deck_rules(db, tmp_path):
    """`describe` on a six-card EXTRA pile must not report "legal: False"."""
    path = write_deck(tmp_path / "withextra.txt", starter_deck(db, "ST9"),
                      db, extra=["BS10-073"] * 4 + ["BS8-069"] * 2)
    text = path.read_text(encoding="utf-8")
    assert text.count("legal:") == 1        # the main deck's, and only that
    assert "expected 60" not in text
    assert _parse(path)["extra"] == ["BS10-073"] * 4 + ["BS8-069"] * 2
