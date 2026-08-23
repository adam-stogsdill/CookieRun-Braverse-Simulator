"""Replays: a recorded game plays back as the game that was recorded.

The claim being pinned is a strong one — not "close enough to watch" but *the
same game*: the same shuffles, the same draws, the same prose log, the same
engine events in the same order. That is what re-running a deterministic engine
over the recorded decisions buys, and it is only true while the recording
stays a pass-through and both seats keep the method surface they had.
"""

from __future__ import annotations

import json
import random

import pytest

from braverse import (STARTER_DECKS, Game, HeuristicAgent, SeatedAgent,
                      default_db)
from braverse import replay as R
from braverse.rps import decide_first_player

DECKS = list(STARTER_DECKS)[:2]


def deck_lists():
    return [list(STARTER_DECKS[name]) for name in DECKS]


def bots(seed: int = 7):
    db = default_db()
    return [SeatedAgent(HeuristicAgent(db=db, seed=seed + i), i) for i in range(2)]


class FullSurface:
    """A seat with every optional controller method, the way a human seat has.

    The engine takes a *different path* for a controller that can answer these
    — the opening mulligan is only offered to one that can — so a recording
    that lost them would replay a different game. Answers are fixed rather than
    clever; what is under test is the plumbing, not the play.
    """

    name = "full"

    def __init__(self, inner, seat: int):
        self.inner = inner
        self.seat = seat
        self.rng = random.Random(100 + seat)

    def choose_action(self, state, options):
        return self.inner.choose_action(state, options)

    def choose(self, state, prompt, options, *, optional: bool):
        return self.inner.choose(state, prompt, options, optional=optional)

    def order_effects(self, state, prompt, options):
        return options[-1]

    def wants_mulligan(self, state, hand, *, free: bool = True):
        return free and self.rng.random() < 0.5

    def choose_many(self, state, prompt, options, *, count, optional, up_to=False):
        return list(options[:count])


def play(controllers, seed: int, decks=None):
    game = Game(decks or deck_lists(), controllers, db=default_db(), seed=seed)
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


def record_a_game(seed: int = 4242, controllers=None):
    log = R.DecisionLog(db=default_db())
    inner = controllers if controllers is not None else bots()
    wrapped = [R.record(c, i, log) for i, c in enumerate(inner)]
    game = play(wrapped, seed)
    recording = log.finish(
        game,
        decks=[{"name": name, "cards": cards, "extra": []}
               for name, cards in zip(DECKS, deck_lists())],
        pilots=["heuristic", "heuristic"],
        seed=seed,
        app_version="test",
    )
    return game, recording


def strip_uids(events):
    """Events minus the card identities.

    Uids come off a process-global counter, so the second run's cards are
    numbered from wherever the first run left off. Everything *else* about an
    event has to match exactly.
    """
    dropped = {"cookie", "attacker", "target", "source"}
    return [{k: v for k, v in event.items()
             if "uid" not in k and k not in dropped} for event in events]


def test_recording_does_not_change_the_game():
    """A recorded game is the same game. Nothing else here means anything."""
    plain = play(bots(), 31337)
    game, _ = record_a_game(31337)
    assert game.state.log == plain.state.log
    assert game.state.winner == plain.state.winner


def test_replay_reproduces_the_game_exactly():
    game, recording = record_a_game()
    controllers, cursor = R.scripted(recording)
    again = play(controllers, recording.seed)

    assert again.state.log == game.state.log
    assert strip_uids(again.state.events) == strip_uids(game.state.events)
    assert again.state.winner == game.state.winner
    assert again.state.turn_number == game.state.turn_number
    assert again.first_player == game.first_player
    assert cursor.at == len(recording.decisions)
    assert cursor.desynced is None


def test_replay_keeps_each_seat_to_the_methods_it_had():
    """A bot has no `wants_mulligan`; a replay of one must not grow one.

    Handing the engine a seat that can answer a question the original could not
    would replay a game nobody played — the mulligan alone re-deals a hand.
    """
    controllers = [FullSurface(bots()[0], 0), bots()[1]]
    game, recording = record_a_game(515, controllers)
    assert recording.surface[0] == list(R.SURFACE)
    assert recording.surface[1] == ["choose_action", "choose"]

    seats, _ = R.scripted(recording)
    for name in R.OPTIONAL:
        assert getattr(seats[0], name, None) is not None
        assert getattr(seats[1], name, None) is None
    assert play(seats, recording.seed).state.log == game.state.log


def test_a_full_surface_seat_replays_exactly():
    controllers = [FullSurface(bots()[0], 0), FullSurface(bots(9)[1], 1)]
    game, recording = record_a_game(2024, controllers)
    seats, _ = R.scripted(recording)
    assert play(seats, recording.seed).state.log == game.state.log


def test_round_trip_through_a_file(tmp_path):
    game, recording = record_a_game()
    path = recording.save(tmp_path / "game.json")
    loaded = R.Recording.load(path)

    assert loaded.decisions == recording.decisions
    assert loaded.deck_lists == recording.deck_lists
    assert loaded.surface == recording.surface
    seats, _ = R.scripted(loaded)
    assert play(seats, loaded.seed).state.log == game.state.log


def test_a_game_saved_mid_play_stops_where_it_was_saved():
    """Not a fault: it replays what it has and says the recording ran out."""
    _, recording = record_a_game()
    recording.decisions = recording.decisions[:20]
    seats, cursor = R.scripted(recording)
    with pytest.raises(R.ReplayFinished):
        play(seats, recording.seed)
    assert cursor.at == 20


def test_a_diverged_replay_stops_rather_than_showing_a_different_game():
    _, recording = record_a_game()
    recording.decisions[12]["g"] = "0000deadbeef"
    seats, _ = R.scripted(recording)
    with pytest.raises(R.ReplayDesync) as caught:
        play(seats, recording.seed)
    assert "decision 13" in str(caught.value)


def test_a_shorter_option_list_is_a_desync_too():
    """The fingerprint is the strong check; the count is the cheap one."""
    _, recording = record_a_game()
    recording.decisions[12]["n"] += 3
    seats, _ = R.scripted(recording)
    with pytest.raises(R.ReplayDesync):
        play(seats, recording.seed)


def test_a_desync_can_be_watched_rather_than_stopped_at():
    _, recording = record_a_game()
    recording.decisions[12]["g"] = "0000deadbeef"
    seats, cursor = R.scripted(recording, strict=False)
    play(seats, recording.seed)
    assert cursor.desynced and "decision 13" in cursor.desynced


def test_the_fingerprint_is_not_made_of_uids():
    """Two runs of the same position number their cards differently.

    If the fingerprint were built from uids, every replay would be a desync —
    which is exactly the bug this pins.
    """
    first = play(bots(), 88)
    second = play(bots(), 88)
    options_a = first.legal_actions()
    options_b = second.legal_actions()
    assert {c.uid for c in first.state.players[0].hand} != \
           {c.uid for c in second.state.players[0].hand}
    assert R.fingerprint(first.state, options_a) == \
           R.fingerprint(second.state, options_b)


def test_junk_is_refused_by_name():
    with pytest.raises(R.ReplayError):
        R.Recording.from_json({"hello": "world"})
    with pytest.raises(R.ReplayError):
        R.Recording.from_json({"format": R.FORMAT, "version": R.FORMAT_VERSION + 1,
                               "decks": [{}, {}], "decisions": []})
    with pytest.raises(R.ReplayError):
        R.Recording.from_json({"format": R.FORMAT, "version": 1,
                               "decks": [{}], "decisions": []})


def test_a_replay_is_small():
    """A whole game is a few hundred small integers, not a film of the board."""
    _, recording = record_a_game()
    assert len(json.dumps(recording.to_json())) < 60_000


# ---------------------------------------------------------------------------
# the server side
# ---------------------------------------------------------------------------
import time                                                    # noqa: E402

import play_server as PS                                       # noqa: E402


def wait_for_match(match, timeout: float = 60.0):
    match.thread.join(timeout)
    assert not match.thread.is_alive(), "the match thread never finished"


@pytest.fixture
def replay_dir(tmp_path, monkeypatch):
    """Keep the tests' replays out of the folder the server actually writes."""
    monkeypatch.setattr(PS, "replay_store", lambda: tmp_path)
    return tmp_path


@pytest.mark.parametrize("raw", [
    "../../etc/passwd", "..%2fsecrets.json", "sub/dir.json", "", "notes.txt",
    ".hidden.json", None, 12,
])
def test_a_replay_name_can_only_ever_be_a_file_in_the_folder(raw):
    assert PS.safe_replay_name(raw) == ""


def test_a_normal_replay_name_survives():
    name = "20260823-014500-st9_sea_fairy-vs-st8_wind_archer.json"
    assert PS.safe_replay_name(name) == name


def test_a_played_match_keeps_itself(replay_dir):
    match = PS.Server(default_db()).new_match(PS.MatchConfig(
        decks=DECKS, pilots=["heuristic", "heuristic"], seed=99, delay=0.0))
    wait_for_match(match)
    assert match.game.state.over
    assert match.saved_as, "a finished game was not written to the replay folder"

    saved = R.Recording.load(replay_dir / match.saved_as)
    assert saved.deck_names == DECKS
    assert saved.result["winner"] == match.game.state.winner
    assert saved.app_version


def test_the_server_watches_one_back_as_an_ordinary_match(replay_dir):
    app = PS.Server(default_db())
    played = app.new_match(PS.MatchConfig(
        decks=DECKS, pilots=["heuristic", "heuristic"], seed=7, delay=0.0))
    wait_for_match(played)
    recording = R.Recording.load(replay_dir / played.saved_as)

    watching = app.new_match(PS.MatchConfig(
        decks=recording.deck_names, pilots=["replay", "replay"],
        seed=recording.seed, delay=0.0, record=False, replay=recording))
    wait_for_match(watching)

    assert watching.error is None
    assert watching.replay_note == ""
    assert watching.game.state.log == played.game.state.log
    # Nobody is playing it, so the spectator's tools are all available and
    # nothing on the board is waiting for an answer.
    assert watching.human_seats == []
    assert watching.snapshot["replay"]["at"] == len(recording.decisions)
    assert watching.pending is None
    # And a replay is not itself recorded — watching a game does not breed
    # copies of it.
    assert watching.recorder is None
    assert not watching.saved_as


def test_a_match_can_be_saved_while_it_is_still_being_played(replay_dir):
    app = PS.Server(default_db())
    match = app.new_match(PS.MatchConfig(
        decks=DECKS, pilots=["heuristic", "heuristic"], seed=11, delay=0.0,
        paused=True))
    deadline = time.time() + 20
    while match.recorder is not None and not match.recorder.decisions and time.time() < deadline:
        time.sleep(0.01)
    saved = match.save_replay()
    match.stop()

    partial = R.Recording.load(saved)
    assert partial.decisions
    assert partial.result["over"] is False


def test_a_replay_that_runs_out_says_so_instead_of_erroring(replay_dir):
    app = PS.Server(default_db())
    played = app.new_match(PS.MatchConfig(
        decks=DECKS, pilots=["heuristic", "heuristic"], seed=5, delay=0.0))
    wait_for_match(played)
    recording = R.Recording.load(replay_dir / played.saved_as)
    recording.decisions = recording.decisions[:15]

    watching = app.new_match(PS.MatchConfig(
        decks=recording.deck_names, pilots=["replay", "replay"],
        seed=recording.seed, delay=0.0, record=False, replay=recording))
    wait_for_match(watching)
    assert watching.error is None       # not an engine fault, and not reported as one
    assert not watching.replay_desync
    assert "still going" in watching.snapshot["replay"]["note"]


def test_a_diverged_replay_is_reported_on_the_board(replay_dir):
    app = PS.Server(default_db())
    played = app.new_match(PS.MatchConfig(
        decks=DECKS, pilots=["heuristic", "heuristic"], seed=3, delay=0.0))
    wait_for_match(played)
    recording = R.Recording.load(replay_dir / played.saved_as)
    recording.decisions[10]["g"] = "0000deadbeef"

    watching = app.new_match(PS.MatchConfig(
        decks=recording.deck_names, pilots=["replay", "replay"],
        seed=recording.seed, delay=0.0, record=False, replay=recording))
    wait_for_match(watching)
    assert watching.snapshot["replay"]["desync"] is True
    assert "decision 11" in watching.snapshot["replay"]["note"]
