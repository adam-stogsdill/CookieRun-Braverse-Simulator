#!/usr/bin/env python3
"""Run bulk self-play games and report the results.

    python selfplay.py -n 200                 # heuristic vs heuristic
    python selfplay.py -n 200 --p1 random     # baseline check
    python selfplay.py -n 1 --log             # replay one game's log
"""

from __future__ import annotations

import argparse
import collections
import time

from braverse import (STARTER_DECKS, Game, HeuristicAgent, RandomAgent,
                      SeatedAgent, default_db, validate)
from braverse.console import utf8_output

AGENTS = {"heuristic": HeuristicAgent, "random": RandomAgent, "rl": None}

_RL_NET = None


def make_agent(kind: str, seat: int, seed: int, checkpoint: str | None = None):
    if kind == "rl":
        # Imported lazily: torch costs about a second of start-up that a plain
        # heuristic run should not pay.
        global _RL_NET
        from braverse.agentfile import find_checkpoint
        from braverse.rl import RLAgent, Trainer
        if _RL_NET is None:
            _RL_NET = Trainer.load_net(find_checkpoint(checkpoint or "rl_agent.pt"))
        return RLAgent(_RL_NET, seat, training=False, seed=seed)
    return SeatedAgent(AGENTS[kind](seed=seed), seat)


def main() -> None:
    utf8_output()   # a redirected stdout on Windows is cp1252
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--games", type=int, default=100)
    parser.add_argument("--p0", choices=AGENTS, default="heuristic")
    parser.add_argument("--p1", choices=AGENTS, default="heuristic")
    parser.add_argument("--deck0", choices=STARTER_DECKS, default="st9_sea_fairy")
    parser.add_argument("--deck1", choices=STARTER_DECKS, default="st8_wind_archer")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--checkpoint", default="rl_agent.pt",
                        help="policy weights, used when an agent is 'rl'")
    parser.add_argument("--log", action="store_true", help="print the last game's log")
    args = parser.parse_args()

    db = default_db()
    decks = [STARTER_DECKS[args.deck0], STARTER_DECKS[args.deck1]]
    for name, deck in zip((args.deck0, args.deck1), decks):
        report = validate(deck, db)
        if not report.ok:
            print(f"! {name}: {'; '.join(report.problems)}")

    wins: collections.Counter[str] = collections.Counter()
    reasons: collections.Counter[str] = collections.Counter()
    turns: list[int] = []
    state = None
    started = time.time()

    for i in range(args.games):
        agents = [make_agent(args.p0, 0, args.seed + i, args.checkpoint),
                  make_agent(args.p1, 1, args.seed + 10_000 + i, args.checkpoint)]
        game = Game(decks, agents, db=db, seed=args.seed + i)
        game.setup()
        state = game.play_out()
        wins["draw" if state.winner == -1 else f"P{state.winner}"] += 1
        reasons[state.win_reason.split(": ", 1)[-1]] += 1
        turns.append(state.turn_number)

    elapsed = time.time() - started
    print(f"\n{args.games} games in {elapsed:.1f}s "
          f"({args.games / max(elapsed, 1e-9):.0f} games/s)")
    print(f"P0 {args.p0} ({args.deck0}) vs P1 {args.p1} ({args.deck1})")
    for key, count in wins.most_common():
        print(f"  {key:6} {count:5}  {count / args.games:6.1%}")
    print(f"  mean turns {sum(turns) / len(turns):.1f}  max {max(turns)}")
    print("\n  outcomes:")
    for reason, count in reasons.most_common():
        print(f"    {count:5}  {reason}")

    if args.log and state is not None:
        print("\n--- last game ---")
        for line in state.log:
            print("   ", line)


if __name__ == "__main__":
    main()
