#!/usr/bin/env python3
"""Round-robin decks against each other, under a chosen pilot.

    python compare_decks.py --decks st9_sea_fairy st8_wind_archer decks/evolved_deck.txt
    python compare_decks.py --agent rl --decks decks/evolved_deck.txt decks/evolved_deck_rl.txt

A deck's strength is not a property of the deck alone — it is a property of the
deck *and* the player flying it. Running the same round robin under both pilots
tells you whether an evolved deck is genuinely strong or merely tuned to the
quirks of whoever scored it during evolution.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from braverse import (STARTER_DECKS, Game, HeuristicAgent, SeatedAgent,
                      default_db, validate)


def load_deck(name: str) -> tuple[str, list[str], list[str]]:
    """``(name, deck, extra)``. The EXTRA deck is empty when a list has none."""
    if name in STARTER_DECKS:
        return name, list(STARTER_DECKS[name]), []
    path = Path(name)
    text = path.read_text()
    # evolve_deck.py writes a human-readable block, then a JSON blob.
    blob = json.loads(text[text.index("{", text.rindex("\n\n")):])
    return path.stem, list(blob["deck"]), list(blob.get("extra") or [])


def pilot_factory(kind: str, checkpoint: str, db):
    if kind == "rl":
        from braverse.deckgen import DeckEvolver
        return DeckEvolver.rl_pilot(checkpoint, db)

    def factory(seat: int, seed: int):
        return SeatedAgent(HeuristicAgent(db=db, seed=seed), seat)

    return factory


def match(a: tuple[list[str], list[str]], b: tuple[list[str], list[str]],
          factory, db, games: int, seed0: int) -> float:
    """Win rate of ``a`` against ``b``, seats alternated.

    Each side is ``(deck, extra)``: a deck that plays an EXTRA deck has to take
    it into the match, or the comparison is measuring a different deck.
    """
    wins = 0.0
    for i in range(games):
        seat = i % 2
        pair = [a, b] if seat == 0 else [b, a]
        decks = [d for d, _ in pair]
        extras = [e for _, e in pair]
        controllers = [factory(0, seed0 + i), factory(1, seed0 + 7000 + i)]
        game = Game(decks, controllers, extra_decks=extras, db=db, seed=seed0 + i)
        game.setup()
        winner = game.play_out().winner
        wins += 1.0 if winner == seat else (0.5 if winner == -1 else 0.0)
    return wins / games


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decks", nargs="+", required=True)
    parser.add_argument("--agent", choices=("heuristic", "rl"), default="heuristic")
    parser.add_argument("--checkpoint", default="rl_agent.pt")
    parser.add_argument("--games", type=int, default=200)
    parser.add_argument("--seed", type=int, default=7_000_000,
                        help="held-out seed block, away from any evolution run")
    args = parser.parse_args()

    db = default_db()
    decks = [load_deck(name) for name in args.decks]
    for name, deck, extra in decks:
        report = validate(deck, db, extra=extra)
        if not report.ok:
            print(f"! {name}: {'; '.join(report.problems)}")

    factory = pilot_factory(args.agent, args.checkpoint, db)
    width = max(len(n) for n, _, _ in decks) + 2

    print(f"\npilot: {args.agent}   {args.games} games per pairing, "
          f"seats alternated\n")
    header = " " * width + "".join(f"{n[:11]:>13}" for n, _, _ in decks) + f"{'avg':>13}"
    print(header)

    results = {}
    for name_a, deck_a, extra_a in decks:
        row = []
        for name_b, deck_b, extra_b in decks:
            if name_a == name_b:
                row.append(None)
                continue
            rate = match((deck_a, extra_a), (deck_b, extra_b),
                         factory, db, args.games, args.seed)
            row.append(rate)
            results[(name_a, name_b)] = rate
        scored = [r for r in row if r is not None]
        average = sum(scored) / len(scored) if scored else 0.0
        cells = "".join("        --   " if r is None else f"{r:>12.1%} " for r in row)
        print(f"{name_a:<{width}}{cells}{average:>12.1%}")

    print("\nrows are the win rate of that deck against each column.")


if __name__ == "__main__":
    main()
