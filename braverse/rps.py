"""Rock, paper, scissors for the opening turn order.

The PLAY GUIDE settles who goes first with rock-paper-scissors, and the winner
chooses. That choice is worth having: in mirror matches under the scripted
heuristic the player who goes first wins about 68% of the time, so "winner
picks" is a real advantage rather than a coin flip with extra steps.

Deliberately kept out of :meth:`Game.setup`. Bulk self-play and training run
millions of games where the ritual would only burn RNG and time, and every
harness in the repo wants seat 0 to start so its results stay comparable. A
played match calls :func:`decide_first_player` and passes the answer to
``Game(first_player=...)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

ROCK, PAPER, SCISSORS = "rock", "paper", "scissors"
THROWS = (ROCK, PAPER, SCISSORS)
# What each throw beats.
BEATS = {ROCK: SCISSORS, PAPER: ROCK, SCISSORS: PAPER}

GO_FIRST = "I go first"
GO_SECOND = "My opponent goes first"
CHOICES = (GO_FIRST, GO_SECOND)

PROMPT_THROW = "Rock, paper, scissors?"
# Going first is worth about 68% under the heuristic, but it is a real trade —
# the opener skips their first draw and cannot attack — so the prompt says so
# rather than making the winner remember it.
PROMPT_CHOICE = ("You won the toss — who goes first? "
                 "(the opener skips their first draw and cannot attack on turn 1)")


def beats(throw: str, other: str) -> bool:
    return BEATS.get(throw) == other


@dataclass
class Toss:
    """How the opening was decided, for the log and the UI."""

    first_player: int
    rounds: list = field(default_factory=list)   # [(throw0, throw1), ...]
    chooser: int | None = None                   # who won the toss
    choice: str = ""

    @property
    def ties(self) -> int:
        return max(0, len(self.rounds) - 1)

    def describe(self) -> list[str]:
        lines = []
        for throw0, throw1 in self.rounds:
            outcome = ("a tie" if throw0 == throw1
                       else f"P{0 if beats(throw0, throw1) else 1} wins")
            lines.append(f"rock-paper-scissors: P0 {throw0}, P1 {throw1} — {outcome}")
        if self.chooser is not None:
            lines.append(f"P{self.chooser} chooses: {self.choice}")
        lines.append(f"P{self.first_player} goes first")
        return lines


def decide_first_player(controllers: Sequence, state, rng, *,
                        max_rounds: int = 12) -> Toss:
    """Throw until someone wins, then let them choose who starts.

    Ties are re-thrown, as at a table. A controller that will not commit to a
    throw is given one at random so a stubborn agent cannot hang the match, and
    an unbroken run of ties falls back to the RNG rather than looping forever.
    """
    toss = Toss(first_player=0)
    for _ in range(max_rounds):
        throws = []
        for seat, controller in enumerate(controllers):
            pick = controller.choose(state, PROMPT_THROW, list(THROWS), optional=False)
            throws.append(pick if pick in THROWS else rng.choice(THROWS))
        toss.rounds.append(tuple(throws))
        if throws[0] == throws[1]:
            continue

        winner = 0 if beats(throws[0], throws[1]) else 1
        toss.chooser = winner
        choice = controllers[winner].choose(state, PROMPT_CHOICE, list(CHOICES),
                                            optional=False)
        toss.choice = choice if choice in CHOICES else GO_FIRST
        toss.first_player = winner if toss.choice == GO_FIRST else 1 - winner
        return toss

    # Nothing but ties: the guide has no answer for that, so flip a coin.
    toss.first_player = rng.randrange(2)
    toss.choice = "unresolved after %d ties" % len(toss.rounds)
    return toss
