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
    code = R.format_code(url, "K7QP9X")
    assert R.parse_code(code) == (url, "K7QP9X")


def test_a_code_is_short_enough_to_send_someone():
    """The whole point. A session description is kilobytes; this is a line."""
    code = R.format_code(
        "https://neither-founded-marks-suse.trycloudflare.com", R.mint_key())
    assert len(code) < 48, code
    assert "\n" not in code and " " not in code


def test_the_key_comes_first_so_the_address_can_have_dots():
    """Address-then-key cannot be parsed for a host nobody enumerated."""
    code = R.format_code("https://a.b.c.example.com", "K7QP9X")
    url, key = R.parse_code(code)
    assert url == "https://a.b.c.example.com" and key == "K7QP9X"


@pytest.mark.parametrize("typed", [
    " c.K7QP9X.neither-founded-marks-suse ",
    "c.K7QP9X.neither-founded-marks-suse\n",
    "c.k7qp9x.NEITHER-FOUNDED-MARKS-SUSE",
    "c.K7QP9X.neither-founded\n-marks-suse",     # wrapped by a chat client
])
def test_a_code_survives_being_retyped_or_wrapped(typed):
    assert R.parse_code(typed) == (
        "https://neither-founded-marks-suse.trycloudflare.com", "K7QP9X")


@pytest.mark.parametrize("code", [
    "", "   ", "hello", "c.K7QP9X", "c..sub", "c.K7QP9X.",
    "z.K7QP9X.sub",                       # a tag from a future version
    "c.NOTAKEY!.sub",
])
def test_nonsense_is_refused_rather_than_guessed_at(code):
    with pytest.raises(R.RendezvousError):
        R.parse_code(code)


@pytest.mark.parametrize("evil", [
    "u.K7QP9X.evil.com/../../etc",
    "u.K7QP9X.evil.com:9999",
    "u.K7QP9X.user@evil.com",
    "u.K7QP9X.evil.com/path",
    "u.K7QP9X.evil.com?x=1",
    "u.K7QP9X.evil.com#frag",
    "u.K7QP9X.evil.com\\share",
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
    assert board.offer_for(key).offer == "v=0 the offer"


def test_a_wrong_key_finds_nothing():
    board = R.Board()
    board.publish("v=0")
    assert board.offer_for("ZZZZZZ") is None


def test_the_answer_finds_its_way_back():
    board = R.Board()
    key = board.publish("v=0 offer")
    assert board.answer(key, "v=0 answer")
    assert board.take_answer(key) == "v=0 answer"


def test_a_second_answer_cannot_hijack_a_connection_being_made():
    """Two people with the code: the first to answer is the opponent."""
    board = R.Board()
    key = board.publish("v=0 offer")
    assert board.answer(key, "first")
    assert not board.answer(key, "second")
    assert board.take_answer(key) == "first"


def test_answering_a_game_that_was_never_published_fails():
    assert not R.Board().answer("ZZZZZZ", "v=0")


def test_an_offer_nobody_collected_does_not_live_forever():
    board = R.Board()
    key = board.publish("v=0")
    board.offers[key].made = time.time() - R.OFFER_TTL - 1
    assert board.offer_for(key) is None
    assert not board.answer(key, "v=0 answer")


def test_hosting_again_does_not_invalidate_a_code_already_sent():
    board = R.Board()
    first = board.publish("offer one")
    second = board.publish("offer two")
    assert first != second
    assert board.offer_for(first).offer == "offer one"
    assert board.offer_for(second).offer == "offer two"


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
        R.fetch_offer("https://localhost.invalid.example", "K7QP9X", timeout=2)
