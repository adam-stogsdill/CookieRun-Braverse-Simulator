"""The deck builder tab's server side: the pool search and the saved deck store."""

from __future__ import annotations

import json

import pytest

import play_server as ps
from braverse import STARTER_DECKS, default_db


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the saved-deck store at a scratch directory."""
    monkeypatch.setattr(ps, "SIDE", tmp_path)
    return tmp_path / ps.DECK_STORE_NAME


def test_a_saved_deck_survives_and_is_offered_to_play(store):
    deck = list(STARTER_DECKS["st9_sea_fairy"])
    ps.write_saved_decks({"my brew": deck})

    assert ps.load_saved_decks() == {"my brew": (deck, [])}
    assert ps.available_decks()["my brew"] == deck
    assert ps.deck_source("my brew") == "saved"
    assert ps.deck_source("st9_sea_fairy") == "starter"


def test_a_saved_deck_wins_a_name_clash_with_a_starter(store):
    ps.write_saved_decks({"st9_sea_fairy": ["ST9-002"] * 60})
    assert ps.available_decks()["st9_sea_fairy"] == ["ST9-002"] * 60


def test_a_corrupt_store_is_ignored_rather_than_fatal(store):
    store.write_text("{ not json")
    assert ps.load_saved_decks() == {}
    assert "st9_sea_fairy" in ps.available_decks()


def test_the_store_is_written_whole(store):
    ps.write_saved_decks({"a": ["ST9-002"]})
    ps.write_saved_decks({"a": ["ST9-002"], "b": ["ST8-001"]})
    assert json.loads(store.read_text()) == {
        "a": {"deck": ["ST9-002"], "extra": []},
        "b": {"deck": ["ST8-001"], "extra": []},
    }
    assert not list(store.parent.glob("*.tmp")), "left a half-written file behind"


def test_a_store_written_before_extra_decks_still_loads(store):
    """The old shape is a bare list of card ids, with no EXTRA deck at all.

    Rewriting every saved deck on upgrade would be a fine way to lose someone's
    decks to a bug, so the reader takes both shapes instead.
    """
    store.write_text(json.dumps({"old brew": ["ST9-002"] * 60}))
    assert ps.load_saved_decks() == {"old brew": (["ST9-002"] * 60, [])}
    assert ps.available_decks()["old brew"] == ["ST9-002"] * 60
    assert ps.available_extra_decks()["old brew"] == []


def test_an_extra_deck_round_trips_through_the_store(store):
    deck = list(STARTER_DECKS["st9_sea_fairy"])
    ps.write_saved_decks({"brew": (deck, ["BS8-005", "BS8-090"])})
    assert ps.available_extra_decks()["brew"] == ["BS8-005", "BS8-090"]


def test_deck_names_cannot_escape_the_store():
    assert ps.clean_deck_name("../../etc/passwd") == "....etcpasswd"
    assert ps.clean_deck_name("  spaced   out  ") == "spaced out"
    assert ps.clean_deck_name(None) == ""
    assert len(ps.clean_deck_name("x" * 200)) == ps.MAX_DECK_NAME


def test_a_posted_decklist_is_capped():
    assert len(ps.clean_card_list(["ST9-002"] * 10_000)) == ps.MAX_DECK_CARDS
    assert ps.clean_card_list("not a list") == []


def test_a_starter_list_reports_legal_and_its_shape():
    payload = ps.deck_payload(default_db(), STARTER_DECKS["st9_sea_fairy"], "st9")
    assert payload["legal"] and payload["problems"] == []
    assert payload["size"] == 60
    assert payload["flipCount"] == 16
    assert sum(c["count"] for c in payload["cards"]) == 60


def test_an_unfinished_deck_says_what_is_missing():
    payload = ps.deck_payload(default_db(), ["ST9-002"] * 3)
    assert not payload["legal"]
    assert any("expected 60" in problem for problem in payload["problems"])


def test_the_pool_leaves_out_what_cannot_be_played():
    db = default_db()
    ids = {c["id"] for c in ps.search_pool(db, {})["cards"]}
    assert not any(db[cid].is_ban or db[cid].type.value == "NPC" for cid in ids)


def test_pool_search_filters_narrow_the_result():
    db = default_db()
    everything = ps.search_pool(db, {})["total"]
    traps = ps.search_pool(db, {"type": "TRAP"})
    assert 0 < traps["total"] < everything
    assert all(c["type"] == "TRAP" for c in traps["cards"])

    one_set = ps.search_pool(db, {"set": "ST9"})
    assert all(c["set"] == "ST9" for c in one_set["cards"])

    # Every word has to land, so a two-word search is a narrowing search.
    wide = ps.search_pool(db, {"q": "sea"})["total"]
    narrow = ps.search_pool(db, {"q": "sea fairy"})["total"]
    assert 0 < narrow <= wide


def test_pool_search_pages_without_dropping_or_repeating_cards():
    db = default_db()
    first = ps.search_pool(db, {})
    second = ps.search_pool(db, {"offset": ps.POOL_LIMIT})
    assert len(first["cards"]) == ps.POOL_LIMIT
    assert first["total"] == second["total"]
    assert not ({c["id"] for c in first["cards"]} & {c["id"] for c in second["cards"]})


def test_a_junk_offset_does_not_break_the_search():
    assert ps.search_pool(default_db(), {"offset": "nonsense"})["offset"] == 0
    assert ps.search_pool(default_db(), {"offset": "-5"})["offset"] == 0


def test_the_builder_is_told_the_deck_building_rules():
    meta = ps.pool_meta(default_db())
    assert meta["rules"] == {"deckSize": 60, "maxCopies": 4, "maxFlip": 16,
                             "extraSize": 6}
    assert "ST9" in meta["sets"] and "COOKIE" in meta["types"]


def test_cards_carry_the_number_the_copy_limit_counts():
    # The 4-copy rule is per card number, so the builder groups by base id.
    card = ps.card_json(default_db(), "ST9-002")
    assert card["baseId"] == "ST9-002" and card["set"] == "ST9"


# --- the decks/ folder -------------------------------------------------------
@pytest.fixture
def deck_dir(tmp_path, monkeypatch):
    """Point both scan roots at a scratch directory with a decks/ folder."""
    monkeypatch.setattr(ps, "ROOT", tmp_path)
    monkeypatch.setattr(ps, "SIDE", tmp_path)
    folder = tmp_path / ps.DECK_DIR
    folder.mkdir()
    return folder


def _write(path, deck, **meta):
    from braverse.deckfile import write_deck
    return write_deck(path, deck, default_db(), **meta)


def test_a_deck_in_the_decks_folder_is_offered_to_play(deck_dir):
    _write(deck_dir / "v4_BLUE.txt", STARTER_DECKS["st9_sea_fairy"], run="v4")
    decks = ps.available_decks()
    assert "v4_BLUE" in decks
    assert len(decks["v4_BLUE"]) == 60
    assert ps.deck_source("v4_BLUE") == "evolved"


def test_a_loose_decklist_beside_the_script_still_works(deck_dir):
    _write(deck_dir.parent / "evolved_deck.txt", STARTER_DECKS["st8_wind_archer"])
    assert "evolved_deck" in ps.available_decks()
    assert ps.deck_source("evolved_deck") == "file"


def test_the_decks_folder_wins_a_name_clash_with_a_loose_file(deck_dir):
    _write(deck_dir.parent / "clash.txt", STARTER_DECKS["st8_wind_archer"])
    _write(deck_dir / "clash.txt", STARTER_DECKS["st9_sea_fairy"])
    assert ps.available_decks()["clash"] == list(STARTER_DECKS["st9_sea_fairy"])
    assert ps.deck_source("clash") == "evolved"


def test_a_half_written_deck_file_is_skipped_not_fatal(deck_dir):
    """A live co-evolution run rewrites these files while the server is up."""
    (deck_dir / "v9_RED.txt").write_text("COOKIE (30)\n\n{\"deck\": [tru")
    _write(deck_dir / "v9_BLUE.txt", STARTER_DECKS["st9_sea_fairy"])
    decks = ps.available_decks()
    assert "v9_RED" not in decks
    assert "v9_BLUE" in decks


def test_a_saved_deck_still_beats_a_decks_folder_name_clash(deck_dir):
    _write(deck_dir / "mine.txt", STARTER_DECKS["st8_wind_archer"])
    ps.write_saved_decks({"mine": list(STARTER_DECKS["st9_sea_fairy"])})
    assert ps.available_decks()["mine"] == list(STARTER_DECKS["st9_sea_fairy"])
    assert ps.deck_source("mine") == "saved"
