#!/usr/bin/env python3
"""Train a self-play RL agent.

    python train_rl.py --games 4000                 # train and report
    python train_rl.py --games 20000 --out big.pt   # longer run
    python train_rl.py --eval-only rl_agent.pt      # score a checkpoint
"""

from __future__ import annotations

import argparse
import time

from braverse import STARTER_DECKS, default_db
from braverse.agentfile import AGENT_DIR, find_checkpoint
from braverse.rl import TrainConfig, Trainer
from braverse.console import utf8_output


def main() -> None:
    utf8_output()   # a redirected stdout on Windows is cp1252
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=4000)
    parser.add_argument("--batch-games", type=int, default=32)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--entropy", type=float, default=0.01)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--eval-every", type=int, default=500)
    parser.add_argument("--eval-games", type=int, default=120)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--random-decks", type=float, default=0.0,
                        help="fraction of training games on freshly generated\n                              legal decks drawn from the whole playable pool")
    parser.add_argument("--deck0", choices=STARTER_DECKS, default="st9_sea_fairy")
    parser.add_argument("--deck1", choices=STARTER_DECKS, default="st8_wind_archer")
    parser.add_argument("--out", default=f"{AGENT_DIR}/rl_agent.pt")
    parser.add_argument("--resume", metavar="CHECKPOINT",
                        help="continue from an existing checkpoint. Pass a "
                             "fresh --seed too, or the run replays the same "
                             "games it has already learned from")
    parser.add_argument("--eval-only", help="load a checkpoint and just score it")
    args = parser.parse_args()

    db = default_db()
    decks = [STARTER_DECKS[args.deck0], STARTER_DECKS[args.deck1]]
    config = TrainConfig(
        games=args.games, batch_games=args.batch_games, lr=args.lr,
        entropy_coef=args.entropy, temperature=args.temperature,
        eval_every=args.eval_every, eval_games=args.eval_games, seed=args.seed,
        random_deck_share=args.random_decks,
    )

    if args.eval_only:
        scoring = Trainer.load_net(find_checkpoint(args.eval_only))
        trainer = Trainer(decks, config, db=db, net=scoring)
        print(f"vs heuristic, starter decks {trainer.evaluate(400):.1%}")
        print(f"vs heuristic, unseen decks  "
              f"{trainer.evaluate(400, unseen_decks=True):.1%}")
        print(f"vs random,    starter decks {trainer.evaluate(400, opponent='random'):.1%}")
        return

    resume = find_checkpoint(args.resume) if args.resume else None
    resuming = Trainer.load_net(resume) if resume else None
    trainer = Trainer(decks, config, db=db, net=resuming)
    if args.resume:
        found = trainer.restore(resume)
        print(f"resumed {resume}: {found['games_trained']} games trained, "
              f"league {found['league']}, optimizer "
              f"{'restored' if found['optimizer'] else 'reset (pre-0.2.54 file)'}")
    label = "resumed" if args.resume else "untrained"
    print(f"baseline ({label}) vs heuristic {trainer.evaluate(200):.1%}")

    started = time.time()
    trainer.train()
    elapsed = time.time() - started

    final_h = trainer.evaluate(400)
    final_u = trainer.evaluate(400, unseen_decks=True)
    final_r = trainer.evaluate(400, opponent="random")
    trainer.save(args.out)

    print(f"\ntrained {args.games} games in {elapsed / 60:.1f} min "
          f"({args.games / max(elapsed, 1e-9):.0f} games/s)")
    print(f"final vs heuristic, starter decks {final_h:.1%}")
    print(f"final vs heuristic, unseen decks  {final_u:.1%}")
    print(f"final vs random,    starter decks {final_r:.1%}")
    print(f"saved -> {args.out}")


if __name__ == "__main__":
    main()
