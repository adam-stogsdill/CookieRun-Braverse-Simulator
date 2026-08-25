"""One short code instead of two walls of pasted text.

A code says *where to collect a session description*, not what one is, so
almost everything worth testing here is about what comes back out of a code —
because whatever does is about to be turned into an address this machine will
go and fetch from.
"""

from __future__ import annotations

import threading
import time

import pytest

import rendezvous as R

# A key of the real length; codes are checked for it, so a short one is not a
# code any more.
KEY = "K7QP9XABCDEFGHJKLMNP"


# ---------------------------------------------------------------------------
# codes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "https://neither-founded-marks-suse.trycloudflare.com",
    "https://abc-123.ngrok-free.app",
    "https://thing.ngrok.app",
    "https://thing.ngrok.io",
    "https://thing.ngrok.dev",
    "https://games.example.com",           # anything else, carried whole
])
def test_every_address_survives_the_round_trip(url):
    code = R.format_code(url, KEY)
    assert R.parse_code(code) == (url, KEY)


def test_a_code_is_short_enough_to_send_someone():
    """The whole point. A session description is kilobytes; this is a line."""
    code = R.format_code(
        "https://neither-founded-marks-suse.trycloudflare.com", R.mint_key())
    # Longer than it was, because the key now encrypts as well as names — but
    # still one line somebody can send and somebody else can type.
    assert len(code) < 64, code
    assert "\n" not in code and " " not in code


def test_the_key_comes_first_so_the_address_can_have_dots():
    """Address-then-key cannot be parsed for a host nobody enumerated."""
    code = R.format_code("https://a.b.c.example.com", KEY)
    url, key = R.parse_code(code)
    assert url == "https://a.b.c.example.com" and key == KEY


@pytest.mark.parametrize("typed", [
    f" c.{KEY}.neither-founded-marks-suse ",
    f"c.{KEY}.neither-founded-marks-suse\n",
    f"c.{KEY.lower()}.NEITHER-FOUNDED-MARKS-SUSE",
    f"c.{KEY}.neither-founded\n-marks-suse",     # wrapped by a chat client
])
def test_a_code_survives_being_retyped_or_wrapped(typed):
    assert R.parse_code(typed) == (
        "https://neither-founded-marks-suse.trycloudflare.com", KEY)


@pytest.mark.parametrize("code", [
    "", "   ", "hello", f"c.{KEY}", "c..sub", f"c.{KEY}.",
    f"z.{KEY}.sub",                       # a tag from a future version
    "c.NOTAKEY!.sub",
    "c.K7QP9X.sub",                       # a key from before they got longer
])
def test_nonsense_is_refused_rather_than_guessed_at(code):
    with pytest.raises(R.RendezvousError):
        R.parse_code(code)


@pytest.mark.parametrize("evil", [
    f"u.{KEY}.evil.com/../../etc",
    f"u.{KEY}.evil.com:9999",
    f"u.{KEY}.user@evil.com",
    f"u.{KEY}.evil.com/path",
    f"u.{KEY}.evil.com?x=1",
    f"u.{KEY}.evil.com#frag",
    f"u.{KEY}.evil.com\\share",
])
def test_a_code_cannot_smuggle_anything_but_a_hostname(evil):
    """What comes out of here is fetched, so it had better be only a host."""
    with pytest.raises(R.RendezvousError):
        R.parse_code(evil)


def test_keys_are_not_guessable_by_hand():
    keys = {R.mint_key() for _ in range(200)}
    assert len(keys) == 200
    assert all(len(k) == R.KEY_LENGTH for k in keys)
    # No O/0 or I/1: these get read aloud and retyped.
    assert not ({"O", "0", "I", "1"} & set("".join(keys)))


# ---------------------------------------------------------------------------
# the board of published offers
# ---------------------------------------------------------------------------
def test_an_offer_comes_back_to_whoever_has_the_key():
    board = R.Board()
    key = board.publish("v=0 the offer")
    found = board.offer_for(R.lookup_id(key))
    assert found is not None
    assert R.open_signal(key, found.offer, role="offer") == "v=0 the offer"


def test_what_is_stored_is_already_sealed():
    """So nothing downstream has to remember to encrypt it on the way out."""
    board = R.Board()
    key = board.publish("v=0 the offer")
    assert "v=0" not in board.offer_for(R.lookup_id(key)).offer


def test_a_wrong_id_finds_nothing():
    board = R.Board()
    board.publish("v=0")
    assert board.offer_for("z" * 24) is None


def test_the_answer_finds_its_way_back():
    board = R.Board()
    key = board.publish("v=0 offer")
    assert board.answer(R.lookup_id(key),
                        R.seal_signal(key, "v=0 answer", role="answer"))
    assert board.take_answer(key) == "v=0 answer"


def test_an_answer_that_will_not_open_is_refused_when_it_is_read():
    """Someone who guessed an id but has no code cannot become the opponent.

    Checked on the way out rather than on the way in, deliberately: refusing at
    the moment it is posted would tell whoever posted it that the id was right.
    """
    board = R.Board()
    key = board.publish("v=0 offer")
    stranger = R.mint_key()
    assert board.answer(R.lookup_id(key),
                        R.seal_signal(stranger, "v=0 mine", role="answer"))
    with pytest.raises(R.RendezvousError):
        board.take_answer(key)


def test_an_answer_cannot_be_an_offer_played_back():
    """The two are the same shape; only the sealed role tells them apart."""
    board = R.Board()
    key = board.publish("v=0 offer")
    theirs = board.offer_for(R.lookup_id(key)).offer     # the sealed offer
    board.answer(R.lookup_id(key), theirs)
    with pytest.raises(R.RendezvousError):
        board.take_answer(key)


def test_a_tampered_answer_is_caught_rather_than_used():
    board = R.Board()
    key = board.publish("v=0 offer")
    sealed = R.seal_signal(key, "v=0 answer", role="answer")
    edited = sealed[:-6] + ("A" if sealed[-6] != "A" else "B") + sealed[-5:]
    board.answer(R.lookup_id(key), edited)
    with pytest.raises(R.RendezvousError):
        board.take_answer(key)


def test_a_second_answer_cannot_hijack_a_connection_being_made():
    """Two people with the code: the first to answer is the opponent."""
    board = R.Board()
    key = board.publish("v=0 offer")
    ident = R.lookup_id(key)
    assert board.answer(ident, R.seal_signal(key, "first", role="answer"))
    assert not board.answer(ident, R.seal_signal(key, "second", role="answer"))
    assert board.take_answer(key) == "first"


def test_answering_a_game_that_was_never_published_fails():
    assert not R.Board().answer("z" * 24, "anything")


def test_an_offer_nobody_collected_does_not_live_forever():
    board = R.Board()
    key = board.publish("v=0")
    board.offers[R.lookup_id(key)].made = time.time() - R.OFFER_TTL - 1
    assert board.offer_for(R.lookup_id(key)) is None
    assert not board.answer(R.lookup_id(key), "whatever")


def test_hosting_again_does_not_invalidate_a_code_already_sent():
    board = R.Board()
    first = board.publish("offer one")
    second = board.publish("offer two")
    assert first != second
    assert R.open_signal(first, board.offer_for(R.lookup_id(first)).offer,
                         role="offer") == "offer one"
    assert R.open_signal(second, board.offer_for(R.lookup_id(second)).offer,
                         role="offer") == "offer two"


# ---------------------------------------------------------------------------
# sealing
# ---------------------------------------------------------------------------
# The tunnel may have no TLS at all — playit.gg's free tier is plain TCP, and
# HTTPS there is a paid feature — so the exchange is sealed before it is handed
# over rather than relying on the pipe to be private.
def test_the_key_never_travels_and_the_name_reveals_nothing():
    key = R.mint_key()
    ident = R.lookup_id(key)
    assert key.lower() not in ident.lower()
    assert ident not in key
    # Different keys, different names — this is what is looked up, so a clash
    # would be two games colliding.
    assert len({R.lookup_id(R.mint_key()) for _ in range(200)}) == 200


def test_a_sealed_signal_shows_nothing_of_what_it_carries():
    key = R.mint_key()
    sealed = R.seal_signal(key, "v=0 candidate 192.168.1.42", role="offer")
    assert "192.168" not in sealed and "v=0" not in sealed


def test_only_the_code_opens_it():
    key = R.mint_key()
    sealed = R.seal_signal(key, "v=0 mine", role="offer")
    assert R.open_signal(key, sealed, role="offer") == "v=0 mine"
    with pytest.raises(R.RendezvousError):
        R.open_signal(R.mint_key(), sealed, role="offer")


def test_sealing_twice_does_not_produce_the_same_bytes():
    """A fresh nonce per seal, so two games are not visibly the same game."""
    key = R.mint_key()
    assert R.seal_signal(key, "v=0", role="offer") != R.seal_signal(key, "v=0", role="offer")


@pytest.mark.parametrize("junk", ["", "not base64!!", "AAAA", "!!!!"])
def test_rubbish_in_place_of_a_signal_is_an_error_not_a_crash(junk):
    with pytest.raises(R.RendezvousError):
        R.open_signal(R.mint_key(), junk, role="offer")


def test_the_board_survives_being_used_from_several_threads():
    board = R.Board()
    keys: list = []
    def work():
        for _ in range(40):
            keys.append(board.publish("v=0"))
    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(set(keys)) == len(keys) == 160


# ---------------------------------------------------------------------------
# reaching the other machine
# ---------------------------------------------------------------------------
def test_an_unreachable_host_is_explained_not_raised_raw():
    """The person reading this typed a code and is waiting to play."""
    with pytest.raises(R.RendezvousError, match="could not reach"):
        R.fetch_offer("https://localhost.invalid.example", KEY, timeout=2)
