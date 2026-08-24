"""Peer-to-peer: two engines, no server, and the same game on both screens.

The claim under test is the one the whole design rests on — that a match needs
no authoritative copy of the board, because two deterministic engines fed the
same decisions *are* the same board. So the central test runs two complete
games in two threads, connected by nothing but a message pipe, and asserts they
finish bit-identical: same winner, same prose log, same events in the same
order.

The rest is the tripwire. A protocol that only worked when both sides agreed
would be worthless, since silent divergence is exactly the failure that cannot
be debugged from either end; so most of these pin the ways a mismatch is made
to *stop* rather than drift.
"""

from __future__ import annotations

import threading

import pytest

from braverse import STARTER_DECKS, Game, HeuristicAgent, SeatedAgent, default_db
from braverse import netplay as N
from braverse.replay import ACTION, CHOOSE, MANY, MULLIGAN, SURFACE
from braverse.rps import decide_first_player

DECKS = list(STARTER_DECKS)[:2]
SEED = 4242


def deck_lists():
    return [list(STARTER_DECKS[name]) for name in DECKS]


def table(surface=None) -> N.Table:
    """A table for two bot seats.

    The surface is taken from the controller rather than assumed: `SeatedAgent`
    has no `wants_mulligan`, so a table claiming a full seat would have both
    engines calling a method that is not there.
    """
    decks = deck_lists()
    surface = tuple(surface or N.surface_of(bot(0)))
    return N.Table(seed=SEED, decks=(tuple(decks[0]), tuple(decks[1])),
                   extra=((), ()), surface=(surface, surface))


def bot(seat: int):
    db = default_db()
    return SeatedAgent(HeuristicAgent(db=db, seed=7 + seat), seat)


def play(controllers, decks, seed=SEED):
    game = Game(decks, controllers, db=default_db(), seed=seed)
    toss = decide_first_player(controllers, game.state, game.state.rng)
    game.first_player = toss.first_player
    game.setup()
    while not game.state.over:
        options = game.legal_actions()
        if not options:
            break
        action = game.controller(game.to_move()).choose_action(game.state, options)
        if action is None:
            break
        game.step(action)
    return game


def renumber(events):
    """Events with card uids replaced by first-appearance ordinals.

    Uids come off a process-global counter, so two games in *one* process are
    numbered from wherever the previous one stopped — the same reason
    `replay.fingerprint` talks in card ids. On two real machines the counters
    both start fresh and the raw numbers agree; here they cannot, so identity
    is compared instead of arithmetic: the same card in the same place gets the
    same ordinal, and a genuine divergence still shows up as a difference.
    """
    seen: dict = {}
    out = []
    for event in events:
        row = {}
        for key, value in sorted(event.items()):
            if isinstance(value, int) and not isinstance(value, bool) \
                    and ("uid" in key or key in ("cookie", "owner_uid")):
                value = seen.setdefault(value, len(seen))
            row[key] = value
        out.append(repr(row))
    return out


def run_peer(seat: int, link: N.Link, out: dict, tbl: N.Table):
    """One machine: its own seat is a bot, the other comes off the wire."""
    session = N.Session(link=link, seat=seat, timeout=30.0)
    controllers = N.seats(bot(seat), session, tbl)
    try:
        game = play(controllers, [list(d) for d in tbl.decks], tbl.seed)
        out["log"] = list(game.state.log)
        out["events"] = renumber(getattr(game.state, "events", []))
        out["winner"] = game.state.winner
        out["turns"] = getattr(game.state, "turn_number", 0)
    except BaseException as exc:          # carried back to the test's thread
        out["error"] = f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# the whole point
# ---------------------------------------------------------------------------
def test_two_engines_exchanging_only_decisions_play_the_same_game():
    """No board ever crosses the wire, and both sides still finish in step."""
    left, right = N.loopback()
    tbl = table()
    a, b = {}, {}
    threads = [threading.Thread(target=run_peer, args=(0, left, a, tbl)),
               threading.Thread(target=run_peer, args=(1, right, b, tbl))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
        assert not t.is_alive(), "a peer deadlocked waiting on the other"

    assert "error" not in a, a["error"]
    assert "error" not in b, b["error"]
    assert a["winner"] == b["winner"]
    assert a["turns"] == b["turns"]
    assert a["log"] == b["log"]
    assert a["events"] == b["events"]
    # A game that ended on turn 1 would satisfy all of the above and prove
    # nothing about staying in step.
    assert a["turns"] > 1
    assert a["winner"] is not None


def test_a_peer_game_is_the_game_one_machine_would_have_played_alone():
    """Being networked does not change what the engine does.

    Same decks, same seed, same bots: the local-only run and the two-machine
    run have to agree, or the wrappers are influencing play rather than
    relaying it.
    """
    solo = play([bot(0), bot(1)], deck_lists())

    left, right = N.loopback()
    tbl = table()
    a, b = {}, {}
    threads = [threading.Thread(target=run_peer, args=(0, left, a, tbl)),
               threading.Thread(target=run_peer, args=(1, right, b, tbl))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    assert "error" not in a, a.get("error")
    assert a["winner"] == solo.state.winner
    assert a["log"] == list(solo.state.log)


# ---------------------------------------------------------------------------
# the handshake
# ---------------------------------------------------------------------------
def test_the_two_sides_settle_on_one_table():
    left, right = N.loopback()
    decks = deck_lists()
    got: dict = {}
    host = threading.Thread(
        target=lambda: got.update(t=N.host_handshake(
            left, deck=decks[0], extra=[], seed=SEED, name="host")))
    host.start()
    joined = N.join_handshake(right, deck=decks[1], extra=[], name="guest")
    host.join(timeout=10)

    assert joined.seed == SEED
    assert joined.decks == got["t"].decks
    assert joined.names == ("host", "guest")


def test_a_version_gap_is_refused_at_the_door():
    """Rather than desyncing six turns in, when it is unfixable."""
    left, right = N.loopback()
    right.send({"t": "hello", "protocol": N.PROTOCOL + 1, "deck": [], "extra": []})
    with pytest.raises(N.Handshake, match="protocol"):
        N.host_handshake(left, deck=[], extra=[], seed=1, timeout=5)


def test_a_host_that_deals_us_a_different_deck_is_refused():
    """The joiner checks the table it is handed contains the deck it offered."""
    left, right = N.loopback()
    decks = deck_lists()
    swapped = N.Table(seed=1, decks=(tuple(decks[0]), tuple(decks[0])),
                      extra=((), ()), surface=(SURFACE, SURFACE))
    joiner = threading.Thread(target=lambda: None)
    del joiner
    left.recv(timeout=0)          # drain nothing; the hello arrives below

    def be_a_bad_host():
        left.recv(timeout=5)
        left.send({"t": "table", "protocol": N.PROTOCOL,
                   "table": swapped.as_json()})

    bad = threading.Thread(target=be_a_bad_host)
    bad.start()
    with pytest.raises(N.Handshake, match="did not bring"):
        N.join_handshake(right, deck=decks[1], extra=[], timeout=5)
    bad.join(timeout=5)


def test_the_handshake_outlives_a_human_pasting_a_code():
    """Signalling is paced by people, and the wait has to be paced the same.

    The exchange is: copy a code, send it over whatever you use to chat, wait
    for a reply code, paste it back. That is minutes, routinely. An earlier
    version timed this out after 60 seconds, so the codes would exchange
    perfectly, WebRTC would connect, and the game would never start — the
    handshake had already given up and nothing was reading the wire any more.
    """
    assert N.SIGNAL_TIMEOUT >= 10 * 60, "a person cannot paste a code that fast"
    import inspect
    for handshake in (N.host_handshake, N.join_handshake):
        default = inspect.signature(handshake).parameters["timeout"].default
        assert default == N.SIGNAL_TIMEOUT, (
            f"{handshake.__name__} waits on a person, not on an RPC")


def test_a_seat_is_not_lost_by_stepping_away_from_the_keyboard():
    """A lockstep game has nowhere to resume from, so a timeout is permanent."""
    assert N.TURN_TIMEOUT >= 10 * 60


def test_a_peer_that_never_speaks_does_not_hang_forever():
    left, _right = N.loopback()
    with pytest.raises(N.Handshake, match="never answered"):
        N.host_handshake(left, deck=[], extra=[], seed=1, timeout=0.2)


# ---------------------------------------------------------------------------
# divergence
# ---------------------------------------------------------------------------
class Board:
    """Enough of a state for `fingerprint` to have something to say."""
    players = ()


def session_pair():
    left, right = N.loopback()
    return (N.Session(link=left, seat=0, timeout=2.0),
            N.Session(link=right, seat=1, timeout=2.0))


def test_a_different_option_list_is_caught_rather_than_played():
    """The heart of the tripwire: same question, different moves on offer."""
    mine, theirs = session_pair()
    theirs.publish(CHOOSE, Board(), ["a", "b", "c"], "b")
    with pytest.raises(N.Desync, match="different set of moves"):
        mine.consume(CHOOSE, Board(), ["a", "b"])


def test_an_answer_to_a_different_question_is_caught():
    mine, theirs = session_pair()
    theirs.publish(CHOOSE, Board(), ["a", "b"], "a")
    with pytest.raises(N.Desync, match="question"):
        mine.consume(ACTION, Board(), ["a", "b"])


def test_the_two_sides_must_be_on_the_same_decision():
    mine, theirs = session_pair()
    theirs.count = 5
    theirs.publish(CHOOSE, Board(), ["a", "b"], "a")
    with pytest.raises(N.Desync, match="decision 5.*we are on 0|peer is on"):
        mine.consume(CHOOSE, Board(), ["a", "b"])


def test_a_pick_outside_the_options_is_refused():
    """A hostile peer cannot name a move that was never on offer."""
    mine, theirs = session_pair()
    theirs.link.send({"t": "d", "n": 0, "s": 1, "k": CHOOSE,
                      "g": N.fingerprint(Board(), ["a", "b"]), "i": 99})
    with pytest.raises(N.Desync, match="not one of the 2 options"):
        mine.consume(CHOOSE, Board(), ["a", "b"])


def test_a_peer_cannot_answer_our_own_seat():
    mine, theirs = session_pair()
    theirs.link.send({"t": "d", "n": 0, "s": 0, "k": CHOOSE,
                      "g": N.fingerprint(Board(), ["a"]), "i": 0})
    with pytest.raises(N.Desync, match="our own seat"):
        mine.consume(CHOOSE, Board(), ["a"])


def test_silence_becomes_a_dropped_peer_not_a_wedged_game():
    mine, _theirs = session_pair()
    with pytest.raises(N.PeerGone, match="no answer"):
        mine.consume(CHOOSE, Board(), ["a"])


def test_leaving_says_so_instead_of_timing_out():
    mine, theirs = session_pair()
    theirs.goodbye("closed the tab")
    with pytest.raises(N.PeerGone, match="closed the tab"):
        mine.consume(CHOOSE, Board(), ["a"])


# ---------------------------------------------------------------------------
# encoding
# ---------------------------------------------------------------------------
def test_a_declined_optional_stays_declined():
    mine, theirs = session_pair()
    theirs.publish(CHOOSE, Board(), ["a", "b"], None)
    assert mine.consume(CHOOSE, Board(), ["a", "b"]) is None


def test_a_multi_pick_survives_the_wire():
    mine, theirs = session_pair()
    theirs.publish(MANY, Board(), ["a", "b", "c"], ["a", "c"])
    assert mine.consume(MANY, Board(), ["a", "b", "c"]) == ["a", "c"]


@pytest.mark.parametrize("answer", [True, False])
def test_the_mulligan_is_a_yes_or_no_both_sides_read_alike(answer):
    mine, theirs = session_pair()
    theirs.publish(MULLIGAN, Board(), [True, False], answer)
    assert mine.consume(MULLIGAN, Board(), [True, False]) is answer


# ---------------------------------------------------------------------------
# surface
# ---------------------------------------------------------------------------
def test_a_seat_never_grows_a_method_the_other_side_does_not_have():
    """A bot has no `wants_mulligan`, and must not appear to.

    The engine offers the opening redraw only to a seat that can answer it, so
    a wrapper that added one would make this engine ask a question the other
    engine is never going to ask — a desync manufactured by our own plumbing.
    """
    bare = ("choose_action", "choose")
    session = N.Session(link=N.loopback()[0], seat=0)
    local, remote = N.seats(bot(0), session, table(surface=bare))
    for wrapper in (local, remote):
        assert wrapper.choose_action is not None
        assert getattr(wrapper, "wants_mulligan", None) is None
        assert getattr(wrapper, "choose_many", None) is None


def test_a_full_surface_is_kept_whole():
    session = N.Session(link=N.loopback()[0], seat=0)
    for wrapper in N.seats(bot(0), session, table(surface=SURFACE)):
        for method in SURFACE:
            assert getattr(wrapper, method, None) is not None
