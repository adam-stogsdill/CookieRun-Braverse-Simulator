#!/usr/bin/env python3
"""Evolve a decklist against a gauntlet of existing decks.

    python evolve_deck.py                              # default: ST8+ST9 pool
    python evolve_deck.py --generations 30 --pop 32
    python evolve_deck.py --pool implemented --out best_deck.txt
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from braverse import STARTER_DECKS, default_db
from braverse.deckgen import (DeckEvolver, DeckGenConfig, describe,
                              implemented_pool, set_pool)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", choices=("starter_sets", "implemented", "all"),
                        default="starter_sets",
                        help="starter_sets = ST8+ST9 (fully implemented)")
    parser.add_argument("--generations", type=int, default=15)
    parser.add_argument("--pop", type=int, default=24)
    parser.add_argument("--games", type=int, default=40,
                        help="gauntlet games per candidate")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--agent", choices=("heuristic", "rl", "both"), default="heuristic",
                        help="which player flies the candidate decks")
    parser.add_argument("--checkpoint", default="rl_agent.pt")
    parser.add_argument("--holdout", type=int, default=400,
                        help="games on unseen shuffles for the final number")
    parser.add_argument("--out", default="evolved_deck.txt")
    parser.add_argument("--force", action="store_true",
                        help="allow overwriting an existing --out file")
    args = parser.parse_args()

    # A long evolution run is expensive and its output is not reproducible
    # without the exact seed and budget. Never clobber one by accident --
    # a quick smoke test with the default --out is all it takes.
    if Path(args.out).exists() and not args.force:
        raise SystemExit(f"{args.out} already exists; pass --force to overwrite "
                         f"or choose another --out")

    db = default_db()
    if args.pool == "starter_sets":
        pool = set_pool(db, ("ST8", "ST9"))
    elif args.pool == "implemented":
        pool = implemented_pool(db)
    else:
        pool = [c for c in db.cards.values() if not c.is_ban]
    print(f"pool: {len(pool)} cards ({args.pool})")

    gauntlet = [STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]]
    config = DeckGenConfig(population=args.pop, generations=args.generations,
                           games_per_eval=args.games, seed=args.seed)
    pilot, pilots = None, None
    if args.agent == "rl":
        pilot = DeckEvolver.rl_pilot(args.checkpoint, db)
    elif args.agent == "both":
        # Selecting on the worst of the two pilots rejects decks that only work
        # for whoever happens to share their quirks.
        scripted = DeckEvolver(pool, gauntlet, config, db=db)._default_agent
        pilots = [scripted, DeckEvolver.rl_pilot(args.checkpoint, db)]
    print(f"pilot: {args.agent}"
          + (f" (aggregate: {config.pilot_aggregate})" if pilots else ""))
    evolver = DeckEvolver(pool, gauntlet, config, db=db,
                          agent_factory=pilot, agent_factories=pilots)

    started = time.time()
    deck, score, history = evolver.evolve()
    elapsed = time.time() - started

    # The headline number must come from shuffles the search never saw, and
    # from a fresh evolver so no cached score can leak into it.
    fresh = DeckEvolver(pool, gauntlet, config, db=db,
                        agent_factory=pilot, agent_factories=pilots)
    holdout = fresh.holdout(deck, games=args.holdout)
    baselines = {name: fresh.holdout(STARTER_DECKS[name], games=args.holdout)
                 for name in ("st9_sea_fairy", "st8_wind_archer")}

    print(f"\nvalidation win rate: {score:.1%}   ({elapsed / 60:.1f} min)")
    print(f"HOLDOUT vs gauntlet: {holdout:.1%}  ({args.holdout} unseen games)")
    print("  for reference, the hand-built starters on the same holdout:")
    for name, rate in baselines.items():
        print(f"    {name:18} {rate:.1%}")
    print(f"\n{describe(deck, db)}")

    Path(args.out).write_text(describe(deck, db) + "\n\n" + json.dumps({
        "deck": deck, "validation_score": score, "holdout": holdout,
        "baselines": baselines, "history": history,
    }, indent=1))
    print(f"\nsaved -> {args.out}")


if __name__ == "__main__":
    main()
