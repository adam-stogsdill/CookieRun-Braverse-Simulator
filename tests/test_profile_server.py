"""The profile over HTTP, and the game that gets banked into it.

The rules that matter here are the ones a unit test of `braverse.profile`
cannot see: that a finished match lands in the open profile by itself, that a
bot pays nothing, that a game watched back is not a game played, and that none
of these routes answers a browser that is not on this machine.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

import play_server as PS
from braverse import default_db
from braverse import profile as PR


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the server's profile folder at a temporary one."""
    made = PR.ProfileStore(tmp_path / "profiles")
    monkeypatch.setattr(PS, "profile_store", lambda: made)
    monkeypatch.setattr(PS, "PROFILES", PS.Profiles())
    return made


@pytest.fixture
def base(store):
    PS.Handler.app = PS.Server(default_db())
    httpd = PS.Viewer(("127.0.0.1", 0), PS.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    PS.Handler.app.close()
    httpd.shutdown()
    httpd.server_close()


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


# ---------------------------------------------------------------------------
# the routes
# ---------------------------------------------------------------------------
def test_nobody_is_signed_in_to_begin_with(base):
    code, res = call(base, "/api/profiles")
    assert code == 200
    assert res["profiles"] == [] and res["active"] is None


def test_make_one_open_it_and_close_it(base):
    code, res = call(base, "/api/profiles/new", {"name": "Ada L"})
    assert code == 200
    assert res["active"]["name"] == "Ada L"
    assert res["active"]["level"] == 1

    # Every route answers with the new list and the open profile, so the
    # browser never has to go back and ask.
    _, res = call(base, "/api/profiles/close", {})
    assert res["active"] is None
    _, res = call(base, "/api/profiles/open", {"slug": "ada-l"})
    assert res["active"]["name"] == "Ada L"


def test_a_locked_profile_asks_before_it_opens(base):
    call(base, "/api/profiles/new", {"name": "Ada L", "passphrase": "hunter2"})
    call(base, "/api/profiles/close", {})

    code, res = call(base, "/api/profiles/open", {"slug": "ada-l"})
    assert code == 401 and res["locked"] is True

    code, res = call(base, "/api/profiles/open",
                     {"slug": "ada-l", "passphrase": "hunter3"})
    assert code == 403 and res["wrong"] is True

    code, res = call(base, "/api/profiles/open",
                     {"slug": "ada-l", "passphrase": "hunter2"})
    assert code == 200 and res["active"]["name"] == "Ada L"


def test_one_name_one_profile(base):
    call(base, "/api/profiles/new", {"name": "Ada L"})
    code, res = call(base, "/api/profiles/new", {"name": "Ada L"})
    assert code == 400 and "already" in res["error"]


def test_a_profile_needs_a_name(base):
    code, _ = call(base, "/api/profiles/new", {"name": "   "})
    assert code == 400


def test_the_picture_can_be_changed_and_is_checked(base):
    call(base, "/api/profiles/new", {"name": "Ada L"})
    _, res = call(base, "/api/profile/avatar", {"avatar": "card:ST9-001"})
    assert res["active"]["avatar"] == "card:ST9-001"

    code, _ = call(base, "/api/profile/avatar", {"avatar": "javascript:x"})
    assert code == 400
    # And it survives being read back off the disk.
    call(base, "/api/profiles/close", {})
    _, res = call(base, "/api/profiles/open", {"slug": "ada-l"})
    assert res["active"]["avatar"] == "card:ST9-001"


def test_the_pane_needs_a_profile_open(base):
    code, res = call(base, "/api/profile/games/keep", {"id": "x", "kept": True})
    assert code == 409 and "no profile" in res["error"]


def test_an_unknown_profile_route_is_not_found(base):
    code, _ = call(base, "/api/profile/nonsense", {})
    assert code == 404


def test_none_of_this_is_offered_to_a_stranger():
    """A joiner's browser is on someone else's machine. It sees no profiles."""
    for route in ("/api/profiles", "/api/profiles/new", "/api/profiles/open",
                  "/api/profiles/delete", "/api/profile/avatar",
                  "/api/profile/games/keep", "/api/profile/games/delete"):
        assert route not in PS.PUBLIC_ROUTES


# ---------------------------------------------------------------------------
# banking a finished game
# ---------------------------------------------------------------------------
def run(pilots=("random", "random"), *, profile_seat=0, **kw) -> PS.Match:
    """Play a real game out and hand back the match that played it."""
    config = PS.MatchConfig(
        decks=["st9_sea_fairy", "st8_wind_archer"], pilots=list(pilots),
        seed=7, delay=0.0, record=False, profile_seat=profile_seat, **kw)
    match = PS.Match(config, default_db())
    match.start()
    match.thread.join(timeout=120)
    assert match.game.state.over, "the fixture game did not finish"
    return match


def finished(*args, **kw) -> PS.Match:
    """A played-out match that has *not* been banked yet.

    The match banks itself as it ends, which is the behaviour under test in
    `test_a_finished_game_lands_in_the_open_profile` — so every test that wants
    to bank a doctored config by hand plays its game with nobody signed in.
    """
    session = PS.PROFILES.active()
    PS.PROFILES.close()
    try:
        return run(*args, **kw)
    finally:
        PS.PROFILES.session = session


def test_a_finished_game_lands_in_the_open_profile(store):
    session = PS.PROFILES.create("Ada L", "", "")
    match = run()                       # banks itself as the game ends

    profile = store.open(session.slug).profile      # from the disk, not memory
    assert profile.games == 1
    entry = profile.history[0]
    assert entry.deck == "st9_sea_fairy"
    assert entry.opponent_deck == "st8_wind_archer"
    assert entry.turns == match.game.state.turn_number
    assert entry.result == ("win" if match.game.state.winner == 0 else "loss")


def test_a_bot_game_pays_no_xp(store):
    PS.PROFILES.create("Ada L", "", "")
    run()
    profile = PS.PROFILES.active().profile
    assert profile.games == 1
    assert profile.xp == 0 and profile.level == 1
    assert profile.history[0].xp == 0


def test_two_people_do_pay_xp(store):
    PS.PROFILES.create("Ada L", "", "")
    match = finished()
    # The seats were bots so the game could play itself out; what is being
    # tested is the rule the *config* states, which is what banking reads.
    match.config.pilots = ["human", "human"]
    match.config.names = ["Ada", "Bo"]
    PS.PROFILES.bank(match)
    profile = PS.PROFILES.active().profile
    assert profile.xp in (1, 4)                   # a loss or a win
    assert profile.history[0].opponent_name == "Bo"


def test_a_game_with_no_seat_of_ours_is_not_a_game_of_ours(store):
    PS.PROFILES.create("Ada L", "", "")
    PS.PROFILES.bank(finished(profile_seat=None))
    assert PS.PROFILES.active().profile.games == 0


def test_the_guided_first_game_is_a_lesson_not_a_result(store):
    PS.PROFILES.create("Ada L", "", "")
    match = finished()
    match.config.tutorial = True
    PS.PROFILES.bank(match)
    assert PS.PROFILES.active().profile.games == 0


def test_banking_with_nobody_signed_in_is_quiet(store):
    run()                                         # must not raise


def test_a_write_that_fails_does_not_take_the_match_down(store, monkeypatch):
    PS.PROFILES.create("Ada L", "", "")
    monkeypatch.setattr(PR.Session, "save",
                        lambda self: (_ for _ in ()).throw(OSError("read-only")))
    run()                                         # must not raise


def test_the_thirtyfirst_game_takes_its_replay_with_it(store, tmp_path,
                                                       monkeypatch):
    replays = tmp_path / "replays"
    replays.mkdir()
    monkeypatch.setattr(PS, "replay_store", lambda: replays)

    session = PS.PROFILES.create("Ada L", "", "")
    for i in range(PR.HISTORY_LIMIT + 1):
        name = f"game{i:03d}.json"
        (replays / name).write_text("{}", encoding="utf-8")
        session.profile.record(deck="mine", opponent_deck="theirs",
                               opponent="human", result="win",
                               when=1000 + i, replay=name)
    dropped = session.profile.prune()
    session.save()
    for gone in dropped:
        PS.drop_replay(gone.replay)

    assert not (replays / "game000.json").exists()
    assert (replays / "game001.json").exists()
    assert len(list(replays.glob("*.json"))) == PR.HISTORY_LIMIT


def test_deleting_a_game_deletes_its_log(base, store, tmp_path, monkeypatch):
    replays = tmp_path / "replays"
    replays.mkdir()
    monkeypatch.setattr(PS, "replay_store", lambda: replays)
    (replays / "one.json").write_text("{}", encoding="utf-8")

    call(base, "/api/profiles/new", {"name": "Ada L"})
    session = PS.PROFILES.active()
    entry = session.profile.record(deck="mine", opponent_deck="theirs",
                                   opponent="human", result="win",
                                   replay="one.json")
    session.save()

    _, res = call(base, "/api/profile/games/delete", {"id": entry.id})
    assert res["active"]["history"] == []
    assert not (replays / "one.json").exists()
    # The game still happened; only its log went.
    assert res["active"]["games"] == 1


def test_a_starred_game_stays_and_unstarring_lets_it_go(base, store):
    call(base, "/api/profiles/new", {"name": "Ada L"})
    session = PS.PROFILES.active()
    old = session.profile.record(deck="mine", opponent_deck="theirs",
                                 opponent="human", result="win", when=1)
    for i in range(PR.HISTORY_LIMIT + 5):
        session.profile.record(deck="mine", opponent_deck="theirs",
                               opponent="human", result="win", when=1000 + i)
    session.profile.prune()
    session.save()
    assert session.profile.find(old.id) is None

    kept = session.profile.history[0]
    _, res = call(base, "/api/profile/games/keep", {"id": kept.id, "kept": True})
    assert [g["id"] for g in res["active"]["history"] if g["kept"]] == [kept.id]
    _, res = call(base, "/api/profile/games/keep", {"id": kept.id, "kept": False})
    assert [g for g in res["active"]["history"] if g["kept"]] == []


def test_deleting_a_profile_can_leave_the_logs_alone(base, store, tmp_path,
                                                     monkeypatch):
    replays = tmp_path / "replays"
    replays.mkdir()
    monkeypatch.setattr(PS, "replay_store", lambda: replays)
    (replays / "one.json").write_text("{}", encoding="utf-8")

    call(base, "/api/profiles/new", {"name": "Ada L"})
    session = PS.PROFILES.active()
    session.profile.record(deck="mine", opponent_deck="theirs",
                           opponent="human", result="win", replay="one.json")
    session.save()

    _, res = call(base, "/api/profiles/delete", {"slug": "ada-l"})
    assert res["profiles"] == [] and res["active"] is None
    assert (replays / "one.json").exists()


def test_deleting_a_profile_can_take_the_logs_too(base, store, tmp_path,
                                                  monkeypatch):
    replays = tmp_path / "replays"
    replays.mkdir()
    monkeypatch.setattr(PS, "replay_store", lambda: replays)
    (replays / "one.json").write_text("{}", encoding="utf-8")

    call(base, "/api/profiles/new", {"name": "Ada L"})
    session = PS.PROFILES.active()
    session.profile.record(deck="mine", opponent_deck="theirs",
                           opponent="human", result="win", replay="one.json")
    session.save()

    call(base, "/api/profiles/delete", {"slug": "ada-l", "logs": True})
    assert not (replays / "one.json").exists()


def test_a_locked_profile_is_not_deleted_without_its_passphrase(base, store):
    call(base, "/api/profiles/new", {"name": "Ada L", "passphrase": "hunter2"})
    code, _ = call(base, "/api/profiles/delete", {"slug": "ada-l"})
    assert code == 401
    code, res = call(base, "/api/profiles/delete",
                     {"slug": "ada-l", "passphrase": "hunter2"})
    assert code == 200 and res["profiles"] == []
