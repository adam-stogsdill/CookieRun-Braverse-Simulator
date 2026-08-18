"""The opening rock-paper-scissors, and the turn order it sets."""

from __future__ import annotations

import random

import pytest

from braverse import (STARTER_DECKS, Game, HeuristicAgent, SeatedAgent,
                      default_db)
from braverse.rps import (CHOICES, GO_FIRST, GO_SECOND, THROWS,
                          decide_first_player)


class Scripted:
    """Throws from a fixed script, and always makes the same choice."""

    def __init__(self, throws, choice=GO_FIRST):
        self.throws = list(throws)
        self.choice = choice
        self.asked = []

    def choose_action(self, state, options):
        return options[0] if options else None

    def choose(self, state, prompt, options, *, optional):
        self.asked.append(prompt)
        if list(options) == list(THROWS):
            return self.throws.pop(0)
        if list(options) == list(CHOICES):
            return self.choice
        return options[0] if options else None


def test_the_winner_of_the_toss_chooses():
    rng = random.Random(0)
    # paper beats rock: seat 1 wins and takes the first turn.
    toss = decide_first_player([Scripted(["rock"]), Scripted(["paper"])], None, rng)
    assert toss.chooser == 1 and toss.choice == GO_FIRST
    assert toss.first_player == 1

    # ...and may hand it over instead.
    toss = decide_first_player(
        [Scripted(["rock"]), Scripted(["paper"], choice=GO_SECOND)], None, rng)
    assert toss.chooser == 1 and toss.first_player == 0


def test_ties_are_rethrown():
    rng = random.Random(0)
    toss = decide_first_player(
        [Scripted(["rock", "rock", "scissors"]), Scripted(["rock", "rock", "paper"])],
        None, rng)
    assert toss.ties == 2
    assert toss.chooser == 0 and toss.first_player == 0
    assert len(toss.rounds) == 3


def test_an_endless_tie_cannot_hang_the_match():
    rng = random.Random(1)
    stubborn = [Scripted(["rock"] * 40), Scripted(["rock"] * 40)]
    toss = decide_first_player(stubborn, None, rng, max_rounds=5)
    assert len(toss.rounds) == 5
    assert toss.first_player in (0, 1)
    assert toss.chooser is None


def test_a_refused_throw_is_supplied_at_random():
    class Silent(Scripted):
        def choose(self, state, prompt, options, *, optional):
            return None

    rng = random.Random(3)
    toss = decide_first_player([Silent([]), Silent([])], None, rng)
    assert toss.first_player in (0, 1)
    for throw0, throw1 in toss.rounds:
        assert throw0 in THROWS and throw1 in THROWS


@pytest.mark.parametrize("first", [0, 1])
def test_the_engine_starts_with_whoever_won(first):
    db = default_db()
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)],
                db=db, seed=2, first_player=first)
    game.setup()
    assert game.state.turn_player == first
    assert game._is_first_turn()
    # "You cannot draw a card from the deck on the first turn of the game" —
    # that applies to whoever actually went first, not to seat 0.
    opener = len(game.state.players[first].hand)
    game.end_turn()
    assert game.state.turn_player == 1 - first
    assert len(game.state.players[1 - first].hand) == opener + game.rules.draw_per_turn, \
        "the second player should have drawn"
    assert len(game.state.players[first].hand) == opener, "the opener drew anyway"

    # A round is only complete when play comes back round to the starter.
    assert game.state.turn_number == 1, "the second player's turn started a new round"
    game.end_turn()
    assert game.state.turn_number == 2


def test_the_heuristic_plays_the_toss_and_takes_the_first_turn():
    """A fixed throw would be free to read, and going first is worth ~68%."""
    db = default_db()
    agent = HeuristicAgent(db=db, seed=4)
    thrown = {agent.choose(None, "Rock, paper, scissors?", list(THROWS), optional=False)
              for _ in range(40)}
    assert thrown == set(THROWS), "the heuristic is throwing predictably"
    assert agent.choose(None, "who?", list(CHOICES), optional=False) == GO_FIRST
