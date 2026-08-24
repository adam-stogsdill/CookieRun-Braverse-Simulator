"""Two browsers, one match: rooms, seat tokens and what each seat may see.

Driven over real HTTP rather than by calling into `Match`, because the whole
point of the feature is what crosses the wire — a test that reached past the
handler could not tell a hidden hand from a hidden-only-by-convention one.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from braverse import default_db
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


def call(base: str, path: str, body=None, timeout: float = 30.0):
    """Returns (status, payload). A 4xx is an answer here, not an exception."""
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


def open_room(base):
    _, host = call(base, "/api/room/new", {"deck": DECKS[0], "name": "Host"})
    _, guest = call(base, "/api/room/join",
                    {"room": host["room"], "deck": DECKS[1], "name": "Guest"})
    return host, guest


def state(base, room, token=None, since=None):
    query = f"/api/state?room={room}"
    if token:
        query += f"&token={token}"
    if since is not None:
        query += f"&since={since}"
    return call(base, query)[1]


def wait_for(fn, timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.02)
    return None


def answer_open_question(base, room, host, guest) -> bool:
    """Answer whichever seat the engine is currently asking, with its own token."""
    snap = state(base, room, host["token"])
    pending = snap.get("pending")
    if not pending:
        return False
    who = guest if pending.get("waiting") else host
    return call(base, "/api/choose",
                {"room": room, "token": who["token"], "index": 0,
                 "pendingId": pending["id"]})[1].get("ok", False)


def deal(base, room, host, guest, timeout: float = 20.0):
    """Play past the opening toss, which blocks before a card is dealt."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = state(base, room, host["token"])
        if snap.get("players") and snap["players"][0]["handCount"] > 0:
            return snap
        answer_open_question(base, room, host, guest)
        time.sleep(0.02)
    return None


def pending_for(base, room, token, timeout: float = 20.0):
    """The open question, once it is one this seat is actually being asked."""
    def asked():
        snap = state(base, room, token)
        pending = snap.get("pending")
        if pending and not pending.get("waiting"):
            return snap
        return None
    return wait_for(asked, timeout)


# -- seats -------------------------------------------------------------------
def test_a_room_seats_two_and_deals(base):
    host, guest = open_room(base)
    assert (host["seat"], guest["seat"]) == (0, 1)
    assert host["token"] != guest["token"]
    snap = wait_for(lambda: state(base, host["room"], host["token"]).get("players"))
    assert snap is not None


def test_the_room_is_full_after_two(base):
    _, host = call(base, "/api/room/new", {"deck": DECKS[0], "name": "Host"})
    call(base, "/api/room/join", {"room": host["room"], "deck": DECKS[1]})
    code, payload = call(base, "/api/room/join",
                         {"room": host["room"], "deck": DECKS[1]})
    assert code == 409 and "full" in payload["error"]


def test_an_illegal_deck_is_turned_away_at_the_door(base):
    code, payload = call(base, "/api/room/new", {"deck": "nonesuch"})
    assert code == 400 and payload["error"] == "unknown deck"


# -- hidden information ------------------------------------------------------
def test_each_seat_is_shown_its_own_hand_and_no_other(base):
    host, guest = open_room(base)
    room = host["room"]
    assert deal(base, room, host, guest) is not None

    mine = state(base, room, host["token"])
    theirs = state(base, room, guest["token"])
    # Both are looking at the same match, and both have cards in hand.
    assert mine["players"][0]["handCount"] == theirs["players"][0]["handCount"] > 0
    # But each holds only their own.
    assert mine["players"][0]["hand"] and mine["players"][1]["hand"] == []
    assert theirs["players"][1]["hand"] and theirs["players"][0]["hand"] == []


def test_the_room_code_alone_buys_a_seat_at_neither_hand(base):
    """The code is in the link you send; it must grant watching and nothing more."""
    host, guest = open_room(base)
    assert deal(base, host["room"], host, guest) is not None
    watching = state(base, host["room"])
    assert watching["seat"] is None
    assert all(p["hand"] == [] for p in watching["players"])
    assert watching["players"][0]["handCount"] > 0   # the count is public


def test_a_made_up_token_is_a_spectator_not_a_seat(base):
    host, guest = open_room(base)
    assert deal(base, host["room"], host, guest) is not None
    guessed = state(base, host["room"], "not-a-real-token")
    assert guessed["seat"] is None
    assert all(p["hand"] == [] for p in guessed["players"])


def test_the_question_put_to_one_seat_is_not_shown_to_the_other(base):
    """Prompt options are routinely the asking player's own hand."""
    host, guest = open_room(base)
    room = host["room"]
    asked = pending_for(base, room, host["token"])
    assert asked is not None
    theirs = state(base, room, guest["token"])["pending"]
    assert theirs["waiting"] is True
    assert theirs["options"] == []
    assert theirs["prompt"] == asked["pending"]["prompt"]   # the wait is legible


# -- who may move ------------------------------------------------------------
def test_the_opponent_cannot_answer_your_question(base):
    host, guest = open_room(base)
    room = host["room"]
    asked = pending_for(base, room, host["token"])
    assert asked is not None
    seat = asked["pending"]["seat"]
    intruder = guest if seat == 0 else host
    _, refused = call(base, "/api/choose",
                      {"room": room, "token": intruder["token"], "index": 0})
    assert refused["ok"] is False
    # And the question is still standing for the seat it belongs to.
    assert state(base, room, host["token"])["pending"]["id"] == asked["pending"]["id"]


def test_a_spectator_cannot_move(base):
    host, _ = open_room(base)
    room = host["room"]
    assert pending_for(base, room, host["token"]) is not None
    code, refused = call(base, "/api/choose", {"room": room, "index": 0})
    assert code == 403 and refused["error"] == "not your seat"


def test_the_seat_being_asked_can_answer(base):
    host, guest = open_room(base)
    room = host["room"]
    asked = pending_for(base, room, host["token"])
    seat = asked["pending"]["seat"]
    mine = host if seat == 0 else guest
    _, ok = call(base, "/api/choose",
                 {"room": room, "token": mine["token"], "index": 0,
                  "pendingId": asked["pending"]["id"]})
    assert ok["ok"] is True


def test_an_answer_to_a_question_that_has_moved_on_is_dropped(base):
    """Two clicks either side of a resolution must not land on what came next."""
    host, guest = open_room(base)
    room = host["room"]
    asked = pending_for(base, room, host["token"])
    seat = asked["pending"]["seat"]
    mine = host if seat == 0 else guest
    body = {"room": room, "token": mine["token"], "index": 0,
            "pendingId": asked["pending"]["id"]}
    assert call(base, "/api/choose", body)[1]["ok"] is True
    assert call(base, "/api/choose", body)[1]["ok"] is False


# -- the wire ----------------------------------------------------------------
def test_a_held_poll_returns_when_the_match_moves(base):
    host, guest = open_room(base)
    room = host["room"]
    asked = pending_for(base, room, host["token"])
    version = asked["version"]
    seat = asked["pending"]["seat"]
    mine = host if seat == 0 else guest

    held: list = []
    watcher = threading.Thread(
        target=lambda: held.append(state(base, room, guest["token"], since=version)))
    watcher.start()
    time.sleep(0.3)
    assert not held, "the poll returned before anything happened"
    call(base, "/api/choose", {"room": room, "token": mine["token"], "index": 0,
                               "pendingId": asked["pending"]["id"]})
    watcher.join(timeout=10)
    assert held and held[0]["version"] > version


def test_a_rematch_is_refused_while_the_game_is_still_going(base):
    host, guest = open_room(base)
    assert pending_for(base, host["room"], host["token"]) is not None
    code, refused = call(base, "/api/room/rematch",
                         {"room": host["room"], "token": host["token"]})
    assert code == 409 and "still going" in refused["error"]


def test_a_rematch_keeps_counting_from_the_last_game(base):
    """A held poll asks to hear about anything past the version it holds.

    Dealing again used to restart the count at zero, so the browser that did
    not press the button sat out the whole poll timeout before it noticed the
    board had been replaced.
    """
    host, guest = open_room(base)
    room = host["room"]
    assert deal(base, room, host, guest) is not None
    before = state(base, room, host["token"])["version"]
    room_obj = Handler.app.room(room)
    room_obj.match.stop()          # stand in for a finished game
    assert call(base, "/api/room/rematch",
                {"room": room, "token": guest["token"]})[1]["ok"] is True

    started = time.time()
    after = state(base, room, host["token"], since=before)
    assert after["version"] > before
    assert time.time() - started < 5, "the poll waited out its hold on a new game"


def test_the_pacing_controls_are_refused_in_an_online_match(base):
    host, _ = open_room(base)
    code, refused = call(base, "/api/control",
                         {"room": host["room"], "token": host["token"],
                          "reveal": True, "paused": True})
    assert code == 403


def test_leaving_releases_the_match_and_empties_the_seat(base):
    host, guest = open_room(base)
    room = host["room"]
    assert pending_for(base, room, host["token"]) is not None
    assert call(base, "/api/room/leave",
                {"room": room, "token": guest["token"]})[1]["ok"] is True
    lobby = call(base, f"/api/room?room={room}")[1]["room"]
    assert lobby["started"] is False
    assert [s["taken"] for s in lobby["seats"]] == [True, False]


def test_the_empty_chair_is_the_one_you_get(base):
    """The host can walk away from their own room; the room is not wedged."""
    host, guest = open_room(base)
    room = host["room"]
    call(base, "/api/room/leave", {"room": room, "token": host["token"]})
    _, third = call(base, "/api/room/join", {"room": room, "deck": DECKS[0]})
    assert third["seat"] == 0
    lobby = call(base, f"/api/room?room={room}")[1]["room"]
    assert lobby["started"] is True


def test_a_lone_seat_does_not_start_a_game(base):
    _, host = call(base, "/api/room/new", {"deck": DECKS[0]})
    assert call(base, f"/api/room?room={host['room']}")[1]["room"]["started"] is False


def test_a_joiner_cannot_write_to_the_host_machines_deck_store(base):
    """`--lan` opens the port; it must not open the host's files with it."""
    import play_server

    real, play_server.Handler._is_local = play_server.Handler._is_local, lambda self: False
    try:
        code, refused = call(base, "/api/decks/save",
                             {"name": "theirs", "cards": [DECKS[0]]})
        assert code == 403
        code, refused = call(base, "/api/decks/delete", {"name": "anything"})
        assert code == 403
    finally:
        play_server.Handler._is_local = real


def test_an_unknown_room_is_reported_as_gone(base):
    code, payload = call(base, "/api/state?room=ZZZZ")
    assert code == 404 and payload["gone"] is True


# ---------------------------------------------------------------------------
# --online: the port a stranger reaches
# ---------------------------------------------------------------------------
# Driven against a real `PublicHandler` listener, because the thing under test
# is a posture and not a function: every one of these would pass if it were
# asked of the private port, which is the whole reason the second port exists.


@pytest.fixture(scope="module")
def public():
    """The public listener, wired the way `--online` wires it."""
    from play_server import PublicHandler

    PublicHandler.app = Handler.app
    httpd = Viewer(("127.0.0.1", 0), PublicHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def out(base: str, path: str, body=None, who: str = "203.0.113.9",
        headers: dict | None = None, timeout: float = 30.0):
    """A request from off this machine: it arrives with a forwarded address.

    `who` buckets the rate limiter, so a test that deliberately trips it does
    not spend the allowance of the next one.
    """
    data = None if body is None else json.dumps(body).encode()
    sent = {"X-Forwarded-For": who}
    if data:
        sent["Content-Type"] = "application/json"
    sent.update(headers or {})
    req = urllib.request.Request(base + path, data=data, headers=sent)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"body": raw.decode(errors="replace")}


def invited(base):
    """A room opened on the host's own machine, as the invite flow opens it."""
    _, host = call(base, "/api/room/new", {"deck": DECKS[0], "name": "Host"})
    assert host["pass"], "hosting must hand back the room's key"
    return host


def test_the_public_port_never_believes_a_caller_is_the_host(public):
    """The loopback trap, and the reason there are two ports at all.

    A tunnel client connects to us *from* 127.0.0.1, so on a shared port every
    stranger would satisfy the check that guards the host's own files. Here the
    connection really is from loopback and it still must not count.
    """
    code, refused = out(public, "/api/decks/save", {"name": "theirs", "cards": []})
    assert code == 403


@pytest.mark.parametrize("path,body", [
    ("/api/new", {}),                       # a match on someone else's machine
    ("/api/control", {"paused": True}),     # pausing a person, not a bot
    ("/api/room/new", {"deck": DECKS[0]}),  # minting state on request
    ("/api/decks/save", {"name": "x", "cards": []}),
    ("/api/decks/delete", {"name": "x"}),
    ("/api/replays/save", {}),
    ("/api/replays/delete", {"name": "x"}),
])
def test_a_route_that_is_not_playing_a_room_is_refused(public, path, body):
    code, refused = out(public, path, body)
    assert code == 403 and "internet" in refused["error"]


@pytest.mark.parametrize("path", ["/api/replays", "/api/replay?name=x"])
def test_the_hosts_replay_folder_is_not_browsable(public, path):
    """It answers with the store's filesystem path, among other things."""
    code, refused = out(public, path)
    assert code == 403


def test_the_room_code_alone_does_not_find_a_room_from_outside(base, public):
    """Four characters is a wall on a LAN and no wall at all against a script."""
    host = invited(base)
    code, payload = out(public, f"/api/state?room={host['room']}")
    assert code == 404 and payload["gone"] is True


def test_a_wrong_key_is_indistinguishable_from_no_such_room(base, public):
    """So walking the code space teaches nothing about which are live."""
    host = invited(base)
    real = out(public, f"/api/state?room={host['room']}&pass=nonsense")
    fake = out(public, "/api/state?room=ZZZZ&pass=nonsense")
    assert real == fake


def test_the_key_from_the_invite_link_gets_you_in(base, public):
    host = invited(base)
    code, payload = out(public, f"/api/state?room={host['room']}&pass={host['pass']}")
    assert code == 200 and payload.get("lobby") is True


def test_the_key_is_a_room_not_a_seat(base, public):
    """Everyone with the link has it, so it must buy no more than watching."""
    host = invited(base)
    _, guest = out(public, "/api/room/join",
                   {"room": host["room"], "pass": host["pass"],
                    "deck": DECKS[1], "name": "Guest"})
    assert guest["seat"] == 1
    # Holding the same key as the player whose turn it is, and still not them.
    code, refused = out(public, "/api/choose",
                        {"room": host["room"], "pass": host["pass"], "index": 0})
    assert code == 403 and refused["error"] == "not your seat"
    call(base, "/api/room/leave", {"room": host["room"], "token": host["token"]})


def test_joining_from_outside_needs_the_key(base, public):
    host = invited(base)
    code, refused = out(public, "/api/room/join",
                        {"room": host["room"], "deck": DECKS[1]})
    assert code == 404 and refused["gone"] is True


def test_the_hosts_solo_game_is_not_on_the_internet(public):
    """A request with no room names the local match; there is none out here."""
    code, payload = out(public, "/api/state")
    assert code == 200
    # By key rather than by whole dict: every answer also names the build that
    # sent it, and this test is about what a stranger can see of the host's own
    # game — which is nothing.
    assert payload["idle"] is True and payload["version"] == 0
    assert "players" not in payload and "log" not in payload


def test_the_front_end_itself_is_still_served(public):
    """A joiner needs the client; it is the API that is narrowed, not the page."""
    req = urllib.request.Request(public + "/app.js")
    with urllib.request.urlopen(req, timeout=10) as res:
        assert res.status == 200 and b"roomAuth" in res.read()


def test_a_flood_of_join_attempts_is_turned_away(base, public):
    host = invited(base)
    seen = set()
    for _ in range(12):
        code, _payload = out(public, "/api/room/join",
                             {"room": host["room"], "pass": host["pass"],
                              "deck": DECKS[1]}, who="198.51.100.4")
        seen.add(code)
        if code == 429:
            break
    assert 429 in seen
    # And the limit is per client, not a wall in front of everybody.
    code, _ = out(public, "/api/room/join",
                  {"room": host["room"], "pass": host["pass"], "deck": DECKS[1]},
                  who="198.51.100.5")
    assert code != 429


def test_a_post_from_another_site_is_refused(public):
    """`Origin` is the one thing a browser will not let a page lie about."""
    code, refused = out(public, "/api/choose", {"index": 0},
                        headers={"Origin": "https://evil.example"})
    assert code == 403 and "cross-site" in refused["error"]


def test_a_host_header_we_never_answered_to_is_refused(base):
    """DNS rebinding: a name pointed at 127.0.0.1 must not read as local."""
    code, refused = call_with_host(base, "/api/decks", "evil.example")
    assert code == 403
    # The ways this machine is legitimately addressed still work.
    assert call_with_host(base, "/api/decks", "localhost")[0] == 200
    assert call_with_host(base, "/api/decks", "mymac.local")[0] == 200


def call_with_host(base: str, path: str, host: str):
    req = urllib.request.Request(base + path, headers={"Host": host})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


# ---------------------------------------------------------------------------
# which build each side is running
# ---------------------------------------------------------------------------
def test_every_answer_names_the_build_that_sent_it(base):
    """One stamp, applied in `_json`, rather than a field on a dozen payloads.

    A room is two browsers on *one* engine — whoever joins plays on the host's
    server — so unlike a peer game there are no two rule sets to disagree.
    What can disagree is a page left open across an upgrade, and this is what
    lets any page notice, on whatever call it was already making.
    """
    from braverse import __version__

    for path in ("/api/config", "/api/state", "/api/decks"):
        code, payload = call(base, path)
        assert code == 200, path
        assert payload.get("build") == __version__, path


def test_joining_a_room_says_which_build_you_joined(base):
    """The moment the question is asked out loud: a joiner whose page came
    from an older build learns it here, before the first turn."""
    from braverse import __version__

    _, host = call(base, "/api/room/new", {"deck": DECKS[0], "name": "Host"})
    code, joined = call(base, "/api/room/join",
                        {"room": host["room"], "deck": DECKS[1], "name": "Guest"})
    assert code == 200
    assert joined["build"] == __version__
    call(base, "/api/room/leave", {"room": host["room"], "token": joined["token"]})
