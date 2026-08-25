"""The bridge between a peer game's engine and the browser relaying for it.

`tests/test_netplay.py` already pins the protocol itself, by running two
engines against each other with no HTTP anywhere. What is left to check is the
seam: that the messages the engine wants sent actually reach the browser's
poll, that what the browser posts back reaches the engine in order, and that a
handshake driven entirely over those two routes really does produce a match.

So the test plays the part the browser and the far machine play together — it
drains `/api/peer/out`, answers on `/api/peer/in`, and speaks `netplay`'s wire
format by hand. Driven over real HTTP for the same reason the room tests are:
what is under test is what crosses the wire.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from braverse import __version__, default_db
from braverse import netplay as NP
from play_server import Handler, Server, Viewer


@pytest.fixture(scope="module")
def base():
    Handler.app = Server(default_db())
    httpd = Viewer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    Handler.app.close()
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(autouse=True)
def no_leftover_game(base):
    """Each test starts with no peer game, and leaves none behind."""
    call(base, "/api/peer/close", {})
    yield
    call(base, "/api/peer/close", {})


def call(base: str, path: str, body=None, timeout: float = 30.0):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        base + path, data=data,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


DECKS = ("st9_sea_fairy", "st8_wind_archer")


def outbox(base, want: str = "", timeout: float = 20.0, hold: float = 5.0) -> list:
    """Everything the engine has put on the wire, optionally waiting for a kind.

    `hold` is passed through so a test asserting nothing *has* been sent does
    not pay the server's full poll to find that out.
    """
    deadline = time.time() + timeout
    seen: list = []
    while time.time() < deadline:
        _, payload = call(base, f"/api/peer/out?hold={min(hold, 5.0)}")
        seen.extend(payload.get("msgs") or [])
        if not want or any(m.get("t") == want for m in seen):
            return seen
    return seen


def inbox(base, *messages) -> dict:
    return call(base, "/api/peer/in", {"msgs": list(messages)})[1]


def peer_state(base, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    snap: dict = {}
    while time.time() < deadline:
        _, snap = call(base, "/api/state?peer=1")
        if snap.get("players") or (snap.get("peer") or {}).get("error"):
            return snap
        time.sleep(0.02)
    return snap


# ---------------------------------------------------------------------------
# the handshake, driven entirely through the two bridge routes
# ---------------------------------------------------------------------------
def test_the_joiner_offers_its_deck_over_the_wire(base):
    """Everything the far machine needs to deal our seat, and nothing else."""
    status, started = call(base, "/api/peer/new",
                           {"host": False, "deck": DECKS[1], "name": "Guest"})
    assert status == 200 and started["seat"] == 1

    hello = next(m for m in outbox(base, "hello") if m["t"] == "hello")
    assert hello["protocol"] == NP.PROTOCOL
    assert len(hello["deck"]) == 60
    assert hello["name"] == "Guest"
    # Both seats are people at browsers, so the full controller protocol is
    # offered — a seat that under-claimed would never be asked for a mulligan.
    assert "wants_mulligan" in hello["surface"]


def test_a_table_sent_back_starts_the_match(base):
    call(base, "/api/peer/new", {"host": False, "deck": DECKS[1], "name": "Guest"})
    hello = next(m for m in outbox(base, "hello") if m["t"] == "hello")

    db = default_db()
    from play_server import available_decklists
    host_deck, host_extra = available_decklists()[DECKS[0]]
    table = NP.Table(seed=99,
                     decks=(tuple(host_deck), tuple(hello["deck"])),
                     extra=(tuple(host_extra), tuple(hello["extra"])),
                     surface=(tuple(hello["surface"]), tuple(hello["surface"])),
                     app_version=__version__, names=("Host", "Guest"))
    inbox(base, {"t": "table", "protocol": NP.PROTOCOL, "table": table.as_json()})

    snap = peer_state(base)
    assert not (snap.get("peer") or {}).get("error"), snap.get("peer")
    assert snap["peer"]["state"] == "playing"
    assert snap["peer"]["seat"] == 1


def test_the_host_waits_for_a_hello_and_answers_with_the_whole_table(base):
    status, started = call(base, "/api/peer/new",
                           {"host": True, "deck": DECKS[0], "name": "Host"})
    assert status == 200 and started["seat"] == 0
    # Nothing goes out until the other side speaks: the host cannot settle a
    # table before it knows what deck it is being brought.
    assert not [m for m in outbox(base, timeout=1.0, hold=0.5) if m.get("t") == "table"]

    guest_deck = list(available_deck(DECKS[1]))
    inbox(base, {"t": "hello", "protocol": NP.PROTOCOL, "app": __version__,
                 "deck": guest_deck, "extra": [],
                 "surface": list(NP.SURFACE), "name": "Guest"})

    sent = next(m for m in outbox(base, "table") if m["t"] == "table")
    table = NP.Table.from_json(sent["table"])
    assert table.decks[1] == tuple(guest_deck)
    assert table.names == ("Host", "Guest")
    assert peer_state(base)["peer"]["state"] == "playing"


def available_deck(name):
    from play_server import available_decklists
    return available_decklists()[name][0]


# ---------------------------------------------------------------------------
# refusing to start a game that would go wrong
# ---------------------------------------------------------------------------
def test_a_peer_on_another_protocol_is_reported_not_played(base):
    call(base, "/api/peer/new", {"host": True, "deck": DECKS[0]})
    inbox(base, {"t": "hello", "protocol": NP.PROTOCOL + 7, "deck": [], "extra": []})
    status = peer_state(base).get("peer") or {}
    assert status.get("state") == "failed"
    assert "protocol" in status.get("error", "")


def test_a_peer_on_another_build_is_refused_before_a_card_is_dealt(base):
    """Two builds cannot be relied on to play the same game from the same seed."""
    call(base, "/api/peer/new", {"host": False, "deck": DECKS[1]})
    hello = next(m for m in outbox(base, "hello") if m["t"] == "hello")
    table = NP.Table(seed=1, decks=(tuple(hello["deck"]), tuple(hello["deck"])),
                     extra=((), ()), surface=(NP.SURFACE, NP.SURFACE),
                     app_version="0.0.1-ancient")
    inbox(base, {"t": "table", "protocol": NP.PROTOCOL, "table": table.as_json()})
    status = peer_state(base).get("peer") or {}
    assert status.get("state") == "failed"
    assert "0.0.1-ancient" in status.get("error", "")


def test_the_host_refuses_a_joiner_on_another_build_too(base):
    """Both seats check, not just the one that happens to receive a table.

    The host settles the table, so for a long time it compared its own version
    against its own — always equal — and only the joiner ever noticed a gap.
    The host would then see a peer that simply stopped talking.
    """
    call(base, "/api/peer/new", {"host": True, "deck": DECKS[0]})
    inbox(base, {"t": "hello", "protocol": NP.PROTOCOL, "app": "0.0.1-ancient",
                 "deck": available_deck(DECKS[0]), "extra": [],
                 "surface": list(NP.SURFACE), "name": "Guest"})

    status = peer_state(base).get("peer") or {}
    assert status.get("state") == "failed"
    assert "0.0.1-ancient" in status.get("error", "")


def test_the_refused_side_is_told_why(base):
    """A handshake we turned down looks, from the other machine, exactly like a
    peer that never answered — so the reason is sent before the link goes
    quiet. It is the only way the person who has to update hears about it."""
    call(base, "/api/peer/new", {"host": True, "deck": DECKS[0]})
    inbox(base, {"t": "hello", "protocol": NP.PROTOCOL, "app": "0.0.1-ancient",
                 "deck": available_deck(DECKS[0]), "extra": [],
                 "surface": list(NP.SURFACE), "name": "Guest"})

    byes = [m for m in outbox(base, "bye") if m.get("t") == "bye"]
    assert byes, "the peer was left waiting with no reason"
    assert "0.0.1-ancient" in byes[0].get("why", "")


def test_a_peer_that_names_no_version_is_still_played(base):
    """The field is not ancient history yet: a build from before it existed
    sends no `app` at all, and its protocol number has already had its say.
    Refusing it as well would turn one check into two failure modes."""
    call(base, "/api/peer/new", {"host": True, "deck": DECKS[0]})
    inbox(base, {"t": "hello", "protocol": NP.PROTOCOL,
                 "deck": available_deck(DECKS[0]), "extra": [],
                 "surface": list(NP.SURFACE), "name": "Guest"})

    status = peer_state(base).get("peer") or {}
    assert status.get("state") != "failed", status.get("error")


def test_a_deck_that_is_not_legal_never_opens_a_connection(base):
    status, payload = call(base, "/api/peer/new", {"host": True, "deck": "nonsense"})
    assert status == 400 and payload.get("error")


def test_a_table_that_deals_us_a_deck_we_did_not_bring_is_refused(base):
    call(base, "/api/peer/new", {"host": False, "deck": DECKS[1]})
    hello = next(m for m in outbox(base, "hello") if m["t"] == "hello")
    other = list(available_deck(DECKS[0]))
    table = NP.Table(seed=1, decks=(tuple(other), tuple(other)),
                     extra=((), ()), surface=(NP.SURFACE, NP.SURFACE),
                     app_version=__version__)
    inbox(base, {"t": "table", "protocol": NP.PROTOCOL, "table": table.as_json()})
    status = peer_state(base).get("peer") or {}
    assert status.get("state") == "failed"
    assert "did not bring" in status.get("error", "")


# ---------------------------------------------------------------------------
# the bridge itself
# ---------------------------------------------------------------------------
def test_messages_reach_the_engine_in_the_order_they_were_posted(base):
    """Order is the game. A reordered decision stream is a different match."""
    from play_server import PeerBridge
    bridge = PeerBridge()
    bridge.deliver([{"t": "d", "n": i} for i in range(20)])
    assert [bridge.recv(timeout=1)["n"] for _ in range(20)] == list(range(20))


def test_a_browser_that_stopped_relaying_is_noticed(base):
    """Rather than growing a queue for a tab that closed an hour ago."""
    from play_server import PEER_BACKLOG, PeerBridge
    bridge = PeerBridge()
    for i in range(PEER_BACKLOG):
        bridge.send({"t": "d", "n": i})
    with pytest.raises(NP.PeerGone, match="stopped relaying"):
        bridge.send({"t": "d", "n": PEER_BACKLOG})


def test_the_poll_returns_the_moment_there_is_something_to_send(base):
    """A decision must not wait out a poll while the other player watches."""
    from play_server import PeerBridge
    bridge = PeerBridge()
    threading.Timer(0.1, lambda: bridge.send({"t": "d", "n": 0})).start()
    started = time.time()
    got = bridge.drain(hold=10.0)
    assert got and got[0]["n"] == 0
    assert time.time() - started < 5.0


def test_posting_to_a_game_that_is_not_there_says_so(base):
    status, payload = call(base, "/api/peer/in", {"msgs": []})
    assert status == 404 and payload.get("gone")


def test_a_body_that_is_not_a_list_of_messages_is_refused(base):
    call(base, "/api/peer/new", {"host": True, "deck": DECKS[0]})
    status, payload = call(base, "/api/peer/in", {"msgs": "not a list"})
    assert status == 400 and payload.get("error")


def test_leaving_takes_the_game_down(base):
    call(base, "/api/peer/new", {"host": True, "deck": DECKS[0]})
    call(base, "/api/peer/close", {"why": "done"})
    assert call(base, "/api/state?peer=1")[1].get("idle")


def test_starting_a_second_game_replaces_the_first(base):
    """One machine, one person, one data channel."""
    call(base, "/api/peer/new", {"host": True, "deck": DECKS[0], "name": "First"})
    _, second = call(base, "/api/peer/new",
                     {"host": False, "deck": DECKS[1], "name": "Second"})
    assert second["seat"] == 1
    hello = next(m for m in outbox(base, "hello") if m["t"] == "hello")
    assert hello["name"] == "Second"


# ---------------------------------------------------------------------------
# what a peer game is not
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/api/peer/new", "/api/peer/in",
                                  "/api/peer/close", "/api/peer/out"])
def test_no_peer_route_is_reachable_from_the_internet(path):
    """A peer game is dialled *out* from this browser and hosts nothing.

    Nothing about it should ever be reachable through the `--online` tunnel,
    which is a different feature for a different mode of play.
    """
    from play_server import PUBLIC_ROUTES
    assert path not in PUBLIC_ROUTES


# ---------------------------------------------------------------------------
# arranging a game from one short code
# ---------------------------------------------------------------------------
# `rendezvous.py` covers the code format; what is left is the exchange those
# codes arrange — the routes the *other player's machine* calls, which are the
# only ones on this server a stranger is meant to reach.
import rendezvous as RZ                                   # noqa: E402
import play_server as PS                                  # noqa: E402


@pytest.fixture
def board():
    """A clean board of published offers around each test."""
    PS.SIGNAL_BOARD = RZ.Board()
    yield PS.SIGNAL_BOARD
    PS.SIGNAL_BOARD = RZ.Board()


def test_the_offer_a_code_points_at_can_be_collected(base, board):
    key = board.publish("v=0 the offer")
    status, got = call(base, f"/api/signal/offer?id={RZ.lookup_id(key)}")
    assert status == 200
    # Sealed on the wire, and only the code opens it: the tunnel carries a name
    # and a blob, which is what lets one without TLS be used at all.
    assert "v=0" not in got["offer"]
    assert RZ.open_signal(key, got["offer"], role="offer") == "v=0 the offer"


def test_the_answer_finds_its_way_back_over_http(base, board):
    key = board.publish("v=0 offer")
    sealed = RZ.seal_signal(key, "v=0 answer", role="answer")
    status, got = call(base, "/api/signal/answer",
                       {"id": RZ.lookup_id(key), "answer": sealed})
    assert status == 200 and got.get("ok")
    assert board.take_answer(key) == "v=0 answer"


def test_the_host_polls_for_the_answer_on_its_own_route(base, board):
    key = board.publish("v=0 offer")
    assert call(base, f"/api/peer/answer?key={key}")[1]["answer"] == ""
    board.answer(RZ.lookup_id(key), RZ.seal_signal(key, "v=0 answer", role="answer"))
    assert call(base, f"/api/peer/answer?key={key}")[1]["answer"] == "v=0 answer"


def test_a_wrong_key_is_indistinguishable_from_an_expired_game(base, board):
    board.publish("v=0 offer")
    status, got = call(base, "/api/signal/offer?id=" + "z" * 24)
    assert status == 404 and "no game with that code" in got["error"]


def test_a_second_joiner_cannot_take_a_seat_already_answered(base, board):
    key = board.publish("v=0 offer")
    ident = RZ.lookup_id(key)
    first = RZ.seal_signal(key, "first", role="answer")
    second = RZ.seal_signal(key, "second", role="answer")
    assert call(base, "/api/signal/answer", {"id": ident, "answer": first})[1].get("ok")
    status, got = call(base, "/api/signal/answer", {"id": ident, "answer": second})
    assert status == 404 and got.get("error")
    assert board.take_answer(key) == "first"


def test_an_empty_answer_is_not_an_answer(base, board):
    key = board.publish("v=0 offer")
    assert call(base, "/api/signal/answer",
                {"id": RZ.lookup_id(key), "answer": ""})[0] == 400


def test_an_absurdly_large_signal_is_refused(base, board):
    key = board.publish("v=0 offer")
    huge = "x" * (RZ.MAX_SIGNAL + 1)
    assert call(base, "/api/signal/answer",
                {"id": RZ.lookup_id(key), "answer": huge})[0] == 400
    assert call(base, "/api/peer/publish", {"offer": huge})[0] == 400


def test_publishing_without_a_tunnel_client_says_how_to_get_one(base, board, monkeypatch):
    """The one failure a player has to act on, so it cannot be a stack trace."""
    monkeypatch.setattr(PS, "ensure_public",
                        lambda: (_ for _ in ()).throw(TUN_ERROR))
    status, got = call(base, "/api/peer/publish", {"offer": "v=0"})
    assert status == 503
    assert "cloudflared" in got["error"]


TUN_ERROR = __import__("tunnel").TunnelError(__import__("tunnel").INSTALL_HINT)


def test_a_joiner_collects_and_replies_through_its_own_machine(base, board, monkeypatch):
    """The joiner's server is the one that talks to the host, never the browser.

    A browser reaching another machine's tunnel is a cross-origin request, and
    opening CORS wide enough for it would let any page anyone visits talk to
    this server. So `collect` and `reply` are server-side, and this drives them
    with the far machine's HTTP standing in for a tunnel.
    """
    key = board.publish("v=0 the offer")
    monkeypatch.setattr(RZ, "parse_code", lambda code: (base, key))
    # `collect` and `reply` seal and open for themselves, so this drives the
    # real thing end to end with only the address stubbed.

    status, got = call(base, "/api/peer/collect", {"code": "c.KEY.whatever"})
    assert status == 200 and got["offer"] == "v=0 the offer"

    status, got = call(base, "/api/peer/reply",
                       {"code": "c.KEY.whatever", "answer": "v=0 the answer"})
    assert status == 200 and got.get("ok")
    assert board.take_answer(key) == "v=0 the answer"


def test_a_mistyped_code_is_explained_rather_than_fetched(base, board):
    status, got = call(base, "/api/peer/collect", {"code": "not a code"})
    assert status == 400 and "game code" in got["error"]


@pytest.mark.parametrize("path", ["/api/signal/offer", "/api/signal/answer"])
def test_the_signalling_routes_are_the_ones_a_stranger_may_reach(path):
    """Everything else about a peer game stays off the internet."""
    assert path in PS.PUBLIC_ROUTES


@pytest.mark.parametrize("path", ["/api/peer/publish", "/api/peer/collect",
                                  "/api/peer/reply", "/api/peer/answer"])
def test_our_own_half_of_the_arrangement_is_not_public(path):
    """These spend this machine: they open tunnels and dial out to others."""
    assert path not in PS.PUBLIC_ROUTES


# ---------------------------------------------------------------------------
# setting the machine up from the machine
# ---------------------------------------------------------------------------
# Configuring a tunnel used to be a command-line flag. It is now a screen, and
# the screen talks to these — so what matters is that they are reachable only
# from this machine, and that a token goes *in* and never comes back out.
import tunnel as TUN                                          # noqa: E402


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """A throwaway home, and an ngrok that accepts whatever it is given.

    Saving a token now runs `ngrok config add-authtoken`, so without this the
    routes correctly refuse on any machine that has no ngrok — which is most of
    them, this one included. `tests/test_tunnel.py` is where the real command
    building and its refusals are pinned; here the subject is the route.
    """
    from pathlib import Path
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv(TUN.AUTHTOKEN_ENV, raising=False)
    monkeypatch.setattr(PS, "NGROK_TOKEN", "")
    monkeypatch.setattr(TUN, "configure_token", lambda token, **kw: "")
    yield tmp_path


def test_a_token_is_refused_when_ngrok_will_not_take_it(base, fake_home, monkeypatch):
    """Whatever ngrok says about it is what the player is told."""
    monkeypatch.setattr(TUN, "configure_token",
                        lambda token, **kw: "ngrok refused it: bad token")
    status, got = call(base, "/api/tunnel/authtoken", {"token": "SUPER-SECRET"})
    assert status == 400 and "refused" in got["error"]
    assert TUN.read_token() == ""       # nothing kept from a token that failed


def test_the_status_never_carries_the_token_itself(base, fake_home):
    """A browser can set one and has no use for reading one.

    A value that is never sent cannot be read out of the page, logged by
    something in between, or left behind in a screenshot of this screen.
    """
    TUN.save_token("SUPER-SECRET-TOKEN")
    status, got = call(base, "/api/tunnel")
    assert status == 200
    assert "SUPER-SECRET-TOKEN" not in json.dumps(got)
    # It still says one exists, which is all the screen needs to know.
    assert got["savedToken"] is True


def test_saving_a_token_does_not_echo_it_back(base, fake_home):
    status, got = call(base, "/api/tunnel/authtoken", {"token": "SUPER-SECRET"})
    assert status == 200 and got.get("ok")
    assert "SUPER-SECRET" not in json.dumps(got)
    assert TUN.read_token() == "SUPER-SECRET"


def test_a_saved_token_is_used_for_this_run_too(base, fake_home):
    """Save and "it works now" should be the same moment, not a restart apart."""
    call(base, "/api/tunnel/authtoken", {"token": "SUPER-SECRET"})
    assert PS.NGROK_TOKEN == "SUPER-SECRET"


def test_an_empty_token_is_refused_rather_than_saved(base, fake_home):
    TUN.save_token("real")
    for blank in ("", "   "):
        assert call(base, "/api/tunnel/authtoken", {"token": blank})[0] == 400
    assert TUN.read_token() == "real"


def test_forgetting_clears_the_run_as_well_as_the_file(base, fake_home):
    call(base, "/api/tunnel/authtoken", {"token": "SUPER-SECRET"})
    status, got = call(base, "/api/tunnel/forget", {})
    assert status == 200 and got["had"] is True
    assert TUN.read_token() == "" and PS.NGROK_TOKEN == ""
    assert got["savedToken"] is False


def test_forgetting_nothing_is_not_an_error(base, fake_home):
    status, got = call(base, "/api/tunnel/forget", {})
    assert status == 200 and got["had"] is False


def test_a_machine_with_no_client_is_told_what_to_install(base, fake_home, monkeypatch):
    monkeypatch.setattr(TUN, "find_backend", lambda: None)
    got = call(base, "/api/tunnel")[1]
    assert got["client"] == "" and "cloudflared" in got["install"]
    # Written for a settings screen, so it must not talk in command-line flags
    # at somebody who opened the game by double-clicking it.
    assert "--online" not in got["install"] and "--lan" not in got["install"]


def test_a_machine_with_cloudflared_is_never_asked_for_a_token(base, fake_home, monkeypatch):
    """It has no account to have a token for; asking would be asking for nothing."""
    monkeypatch.setattr(TUN, "find_backend",
                        lambda: next(b for b in TUN.BACKENDS if b.name == "cloudflared"))
    got = call(base, "/api/tunnel")[1]
    assert got["client"] == "cloudflared" and got["needsToken"] is False


def test_ngrok_without_a_token_reports_that_it_needs_one(base, fake_home, monkeypatch):
    monkeypatch.setattr(TUN, "find_backend",
                        lambda: next(b for b in TUN.BACKENDS if b.name == "ngrok"))
    got = call(base, "/api/tunnel")[1]
    assert got["needsToken"] is True and got["haveToken"] is False
    call(base, "/api/tunnel/authtoken", {"token": "SUPER-SECRET"})
    assert call(base, "/api/tunnel")[1]["haveToken"] is True


def test_a_failed_test_reports_why_rather_than_claiming_success(base, fake_home, monkeypatch):
    monkeypatch.setattr(PS, "ensure_public",
                        lambda: (_ for _ in ()).throw(TUN.TunnelError(TUN.AUTH_HINT)))
    status, got = call(base, "/api/tunnel/test", {})
    assert status == 503 and "authtoken" in got["error"]


@pytest.mark.parametrize("path", ["/api/tunnel", "/api/tunnel/authtoken",
                                  "/api/tunnel/forget", "/api/tunnel/test"])
def test_no_stranger_can_configure_this_machine(path):
    """Setting up the host is the host's business, and one route writes a
    credential — neither belongs on a port the internet can reach."""
    assert path not in PS.PUBLIC_ROUTES
