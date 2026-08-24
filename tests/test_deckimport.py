"""Reading a decklist somebody else wrote.

The game ships with two decks, so nearly every deck a player has is one that
arrived from outside: exported by this game, copied off a website, typed out,
or pasted through a chat window. `parse_decklist` is the way in, and the two
things worth pinning about it are that it is generous with the shapes those
lists come in, and that it is loud about anything it could not use — an
importer that silently drops four cards makes a deck that is wrong in a way
nobody notices until a game goes strangely.
"""

from __future__ import annotations

import json

import pytest

from braverse import default_db, starter_deck
from braverse.deckfile import parse_decklist, write_deck
from play_server import Handler


@pytest.fixture(scope="module")
def db():
    return default_db()


# --- the shapes a list arrives in ------------------------------------------
def test_our_own_file_comes_back_exactly(db, tmp_path):
    """A round trip through `write_deck` is the one case that must be lossless:
    it is what Export writes and what `decks/` holds."""
    deck = starter_deck(db, "ST9")
    path = write_deck(tmp_path / "d.txt", deck, db=db, name="Sea Fairy starter")

    found = parse_decklist(path.read_text(encoding="utf-8"), db)

    assert found.deck == deck
    assert found.name == "Sea Fairy starter"
    assert not found.skipped


def test_the_export_format_reads_back(db):
    """`--COOKIE--` sections with `3x Name ID LVn` — what the viewer's Export
    button writes, and the format people mail to each other."""
    found = parse_decklist(
        "--COOKIE--\n"
        "3x GingerBrave ST9-001 LV1\n"
        "1x Sea Fairy Cookie ST9-007 LV2\n",
        db)
    assert found.deck.count("ST9-001") == 3
    assert found.deck.count("ST9-007") == 1
    assert not found.skipped


def test_the_copy_button_format_reads_back(db):
    """`3 ST9-007 Sea Fairy Cookie` — count first, no `x`."""
    found = parse_decklist("3 ST9-007 Sea Fairy Cookie\n1 ST9-001 GingerBrave", db)
    assert found.deck.count("ST9-007") == 3
    assert found.deck.count("ST9-001") == 1


@pytest.mark.parametrize("line, count", [
    ("4x ST9-007", 4),
    ("4 ST9-007", 4),
    ("ST9-007 x4", 4),
    ("ST9-007 (x4)", 4),
    ("ST9-007", 1),
    ("  st9-007  ", 1),        # lower case, as someone would type it
])
def test_the_ways_people_write_a_quantity(db, line, count):
    assert parse_decklist(line, db).deck == ["ST9-007"] * count


def test_a_card_can_be_named_instead_of_numbered(db):
    """Lists copied off a website are names, not ids."""
    found = parse_decklist("2x Sea Fairy Cookie", db)
    assert len(found.deck) == 2
    assert all(db[c].name == "Sea Fairy Cookie" for c in found.deck)


def test_an_ambiguous_name_is_resolved_but_reported(db):
    """271 of the 813 names in the database are printed on more than one card.

    Picking one is the only useful thing to do — refusing the line loses the
    card — but it is the single place the importer decides something the file
    did not, so it has to say so.
    """
    found = parse_decklist("3x GingerBrave", db)
    assert len(found.deck) == 3
    assert len(set(found.deck)) == 1          # the same card all three times
    assert found.notes and "GingerBrave" in found.notes[0]
    assert "printed on" in found.notes[0]


def test_the_same_ambiguous_list_imports_the_same_way_twice(db):
    """Whichever printing is picked, it is picked deterministically — an import
    that shuffles between runs is one nobody can share."""
    assert parse_decklist("1x GingerBrave", db).deck == \
           parse_decklist("1x GingerBrave", db).deck


def test_extra_cards_land_in_the_extra_pile(db):
    """The EXTRA deck is a separate pile with its own cap, and a list that
    simply names EXTRA cards among the rest still has to end up right."""
    extra_id = next(i for i in db.cards if db[i].type.value == "EXTRA")
    found = parse_decklist(f"1x ST9-007\n1x {extra_id}", db)
    assert found.extra == [extra_id]
    assert found.deck == ["ST9-007"]


def test_an_extra_section_header_is_not_read_as_a_card(db):
    found = parse_decklist("--COOKIE--\n1x ST9-007\n--EXTRA--\n1x ST9-001", db)
    assert found.deck == ["ST9-007"]
    assert found.extra == ["ST9-001"]         # the header moved the pile
    assert not found.skipped


# --- and what it refuses to do quietly -------------------------------------
def test_lines_it_could_not_use_are_handed_back(db):
    """The report is the feature. A list that came in four cards short has to
    say which four lines it did not understand."""
    found = parse_decklist(
        "Here is my deck!\n"
        "https://example.com/decks/123\n"
        "4x ST9-007\n"
        "4x Definitely Not A Cookie\n",
        db)
    assert found.deck == ["ST9-007"] * 4
    assert any("Definitely Not A Cookie" in line for line in found.skipped)
    assert any("example.com" in line for line in found.skipped)


def test_unknown_ids_in_our_own_format_are_reported(db, tmp_path):
    """A file from a newer version of the game, naming a card this one has
    never heard of. The rest of the deck still imports."""
    blob = json.dumps({"deck": ["ST9-007", "ZZ9-999"], "name": "from the future"})
    found = parse_decklist(f"# a decklist\n\n{blob}", db)
    assert found.deck == ["ST9-007"]
    assert found.skipped == ["unknown card id: ZZ9-999"]


def test_nothing_at_all_is_not_an_exception(db):
    """Someone will drop a photo on it."""
    for junk in ("", "   \n\n  ", "\x00\x01\x02", "<html><body>nope</body></html>"):
        found = parse_decklist(junk, db)
        assert found.deck == [] and found.extra == []


def test_a_paste_cannot_be_a_million_cards(db):
    found = parse_decklist("999x ST9-007\n" * 50, db)
    assert len(found.deck) + len(found.extra) <= 400
    assert any("stopped at" in line for line in found.skipped)


# --- the route -------------------------------------------------------------
@pytest.fixture
def server(tmp_path, monkeypatch):
    """The real HTTP handler, with the deck store pointed somewhere temporary
    so a test cannot write into the decks of whoever is running it."""
    import threading

    import play_server as PS

    monkeypatch.setattr(PS, "deck_store", lambda: tmp_path / "saved_decks.json")
    PS.Handler.app = PS.Server(default_db())
    httpd = PS.Viewer(("127.0.0.1", 0), PS.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    PS.Handler.app.close()
    httpd.shutdown()
    httpd.server_close()


def post(base: str, path: str, body: dict):
    import json as _json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        base + path, data=_json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, _json.loads(res.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _json.loads(exc.read())


def test_a_pasted_list_comes_back_as_a_deck(server, db):
    deck = starter_deck(db, "ST9")
    counts: dict[str, int] = {}
    for card in deck:
        counts[card] = counts.get(card, 0) + 1
    text = "\n".join(f"{n}x {card}" for card, n in counts.items())

    code, res = post(server, "/api/decks/import", {"text": text, "name": "pasted"})

    assert code == 200
    assert res["size"] == 60 and res["legal"] is True
    assert res["name"] == "pasted"
    assert sum(card["count"] for card in res["cards"]) == 60
    assert res["skippedCount"] == 0


def test_a_list_the_parser_makes_nothing_of_is_a_400(server):
    code, res = post(server, "/api/decks/import",
                     {"text": "dear diary, today I built a deck"})
    assert code == 400
    assert "decklist" in res["error"]
    # Even here, what it could not read comes back — that is the whole report.
    assert res["skipped"]


def test_an_empty_import_is_refused(server):
    assert post(server, "/api/decks/import", {"text": "   "})[0] == 400
    assert post(server, "/api/decks/import", {})[0] == 400


def test_a_dropped_photo_is_refused_by_size_not_parsed(server):
    import play_server as PS

    code, res = post(server, "/api/decks/import",
                     {"text": "x" * (PS.MAX_IMPORT + 1)})
    assert code == 400
    assert "too big" in res["error"]


def test_importing_does_not_save_by_itself(server, tmp_path, db):
    """The route parses; the browser decides whether to save. One route writes
    the deck store, and an illegal import belongs in the builder, not in the
    deck menu."""
    code, _ = post(server, "/api/decks/import", {"text": "4x ST9-007", "name": "half"})
    assert code == 200
    assert not (tmp_path / "saved_decks.json").exists()


def test_import_is_a_local_only_route():
    """It reads a file off this machine and writes to this machine's deck
    store. Someone who merely joined a game over the LAN has no business
    doing either — the same rule the save and delete routes follow."""
    import play_server

    source = play_server.__file__ and open(play_server.__file__, encoding="utf-8").read()
    route = source[source.index('elif path == "/api/decks/import"'):]
    route = route[:route.index('elif path ==', 10)]
    assert "_is_local()" in route
    assert "/api/decks/import" not in Handler.__dict__.get("PUBLIC_ROUTES", ())
    assert "/api/decks/import" not in play_server.PUBLIC_ROUTES


# --- the way back out ------------------------------------------------------
# Export is the other half of import, and it writes the file on this machine
# rather than handing the browser a download: the game is usually shown in a
# desktop window, where a download has nowhere to go and a blob link merely
# navigates the window to the decklist, painting it over the board with no way
# back. See `viewer/builder.js`.
@pytest.fixture
def exports(tmp_path, monkeypatch):
    import play_server as PS

    directory = tmp_path / "decks"
    directory.mkdir()
    monkeypatch.setattr(PS, "export_store", lambda: directory)
    return directory


def test_export_writes_the_file_here(server, exports, db):
    code, res = post(server, "/api/decks/export",
                     {"name": "Sea Fairy aggro", "text": "--COOKIE--\n4x ST9-007\n"})

    assert code == 200 and res["ok"] is True
    written = exports / "Sea_Fairy_aggro.txt"
    assert res["path"] == str(written)
    assert written.read_text(encoding="utf-8").endswith("4x ST9-007\n")


def test_export_never_writes_over_a_decklist_already_there(server, exports):
    """`decks/` also holds lists the evolver wrote and lists someone was sent;
    an export is not worth losing one of those to a name clash."""
    (exports / "evolved_deck.txt").write_text("keep me", encoding="utf-8")

    code, res = post(server, "/api/decks/export",
                     {"name": "evolved_deck", "text": "--COOKIE--\n4x ST9-007\n"})

    assert code == 200
    assert res["file"] == "evolved_deck-2.txt"
    assert (exports / "evolved_deck.txt").read_text(encoding="utf-8") == "keep me"


def test_an_empty_export_is_refused(server, exports):
    assert post(server, "/api/decks/export", {"text": "  ", "name": "x"})[0] == 400
    assert not list(exports.iterdir())


def test_export_is_a_local_only_route():
    """It writes a file on the machine running the server. Someone who joined
    a game over the LAN does not get to do that."""
    import play_server

    source = open(play_server.__file__, encoding="utf-8").read()
    route = source[source.index('elif path == "/api/decks/export"'):]
    route = route[:route.index('elif path ==', 10)]
    assert "_is_local()" in route
    assert "/api/decks/export" not in play_server.PUBLIC_ROUTES
