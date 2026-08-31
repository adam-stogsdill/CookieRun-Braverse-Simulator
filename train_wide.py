#!/usr/bin/env python3
"""Train an agent on the wide state encoder, and measure it against the old one.

The stock encoder (`braverse/features.py`) describes the board with aggregate
counts — board sizes, HP totals, hand size. `braverse/features_wide.py` replaces
that state block with per-Cookie slots, payable support colours, and hand
composition, so the policy can see *which* Cookies are in play rather than only
how many. Everything else — REINFORCE, the league, the action block — is shared.

The two encoders produce different row widths (55 vs 142), so their checkpoints
are NOT interchangeable. This script keeps them apart: it writes to its own
`--out` file and never touches `rl_agent*.pt`.

Quick start
-----------
Sanity check that everything is wired up (about a minute)::

    python train_wide.py --games 300 --eval-games 40

A real training run, wide encoder against the ten starter decks::

    python train_wide.py --games 40000 --out rl_wide.pt

Compare the two encoders head to head on equal budgets. This trains one agent
per encoder from scratch and plays them against the shared heuristic baseline,
which is the only honest way to read the change — their checkpoints cannot be
loaded into each other's network::

    python train_wide.py --games 40000 --compare

Evaluate a checkpoint you already trained, without training further::

    python train_wide.py --eval-only rl_wide.pt

Use the evolved decks from a co-evolution run instead of the starters, so the
agent trains on the metagame those runs actually produced::

    python train_wide.py --games 40000 --decks decks/v5_*.txt

A folder is a pool, and `--deck-pool-subfolders` makes it the whole tree::

    python train_wide.py --games 40000 --decks decks/meta
    python train_wide.py --games 40000 --decks decks --deck-pool-subfolders

Reading the output
------------------
`vs heuristic` is win rate against the scripted agent on the training decks;
`vs unseen` is the same against decks generated from the pool that the agent
never trained on, and is the number that matters — it is what caught the
overfitting described in README's deck-generation section. A wide-encoder agent
that wins more on `vs heuristic` but not on `vs unseen` has memorised the
training decks rather than learned to read a board.

Where this fits
---------------
`train_rl.py` trains the stock encoder; `coevolve.py` alternates deck evolution
with agent training. This script is deliberately training-only: it changes one
variable (the encoder) so the effect is readable. Once a wide agent is clearly
better, the next step is teaching `coevolve.py` to use it via `--encoder wide`.
"""

from __future__ import annotations

import argparse
import glob
import json
import time
from pathlib import Path

from braverse import STARTER_SET_IDS, default_db, starter_deck
from braverse.agentfile import AGENT_DIR, find_checkpoint
from braverse.deckfile import read_deck, read_extra, read_pool
from braverse.features import Encoder
from braverse.features_wide import WideEncoder
from braverse.rl import TrainConfig, Trainer
from braverse.console import utf8_output

ENCODERS = {"wide": WideEncoder, "stock": Encoder}


def load_decks(patterns: list[str], db,
               subfolders: bool = False) -> tuple[list[list[str]], str]:
    """Decks to train on: files or folders if given, else the ten starter decks.

    A folder among the entries expands to every list in it, the way it does for
    `compare_decks.py --decks` and `evolve_deck.py --gauntlet` — that is how
    `decks/meta` is passed as one word — and `subfolders` makes that expansion
    read the folder's subfolders too. It goes through `read_pool`, so a folder
    contributes its lists name-sorted and de-duplicated; a file named
    explicitly is taken as given, because naming it is the choice.
    """
    if not patterns:
        return [starter_deck(db, s) for s in STARTER_SET_IDS], "10 starter decks"

    decks: list[list[str]] = []
    with_extra: list[str] = []
    sources: list[str] = []
    files = 0
    for entry in patterns:
        if Path(entry).is_dir():
            pool = read_pool(entry, recursive=subfolders)
            if not pool:
                raise SystemExit(f"no decklists in {entry}")
            sources.append(entry.rstrip("/") + ("/**" if subfolders else ""))
            decks.extend(deck for _, deck, _ in pool)
            with_extra.extend(name for name, _, extra in pool if extra)
            continue
        for path in sorted(glob.glob(entry)):
            try:
                deck = read_deck(path)
            except Exception as exc:        # a half-written file from a live run
                print(f"  skipping {path}: {exc}")
                continue
            files += 1
            decks.append(deck)
            if read_extra(path):
                with_extra.append(Path(path).stem)
    if not decks:
        raise SystemExit(f"no readable decklists matched {patterns}")
    if with_extra:
        # One pile per seat here, as in `train_rl.py`: say that a second pile
        # is being dropped rather than training a deck nobody registered. The
        # names are capped because a whole folder tree can put ninety on the
        # line, and the count is the part that matters.
        shown = ", ".join(with_extra[:8])
        if len(with_extra) > 8:
            shown += f", and {len(with_extra) - 8} more"
        print(f"warning: EXTRA decks are not played in training — "
              f"{len(with_extra)} list(s) will train without theirs ({shown})")
    if files:
        sources.append(f"{files} file(s)")
    return decks, f"{len(decks)} decks from {', '.join(sources)}"


def train_one(name: str, decks, db, args) -> dict:
    """Train one agent under the named encoder and report how it does."""
    encoder = ENCODERS[name](db)
    print(f"\n=== {name} encoder: {encoder.dim}-wide rows "
          f"({encoder.state_dim} state) ===", flush=True)

    cfg = TrainConfig(games=args.games, eval_every=0, league_every=args.league_every,
                      random_deck_share=args.random_decks, seed=args.seed)
    resume = find_checkpoint(args.resume) if args.resume else None
    trainer = Trainer(decks, cfg, db=db, encoder=encoder,
                      net=Trainer.load_net(resume) if resume else None)
    if resume:
        found = trainer.restore(resume)
        print(f"  resumed {resume}: {found['games_trained']} games trained, "
              f"league {found['league']}, optimizer "
              f"{'restored' if found['optimizer'] else 'reset (pre-0.2.54 file)'}",
              flush=True)

    started = time.time()
    trainer.train(log=lambda *_: None)
    elapsed = time.time() - started

    vs_heuristic = trainer.evaluate(args.eval_games)
    vs_unseen = trainer.evaluate(args.eval_games, unseen_decks=True)
    print(f"  vs heuristic {vs_heuristic:.1%}   vs unseen {vs_unseen:.1%}   "
          f"({elapsed / 60:.1f} min)", flush=True)

    out = Path(args.out if name == "wide" else
               Path(args.out).with_name(Path(args.out).stem + "_stock.pt"))
    trainer.save(out)
    print(f"  saved -> {out}", flush=True)
    return {"encoder": name, "dim": encoder.dim, "out": str(out),
            "vs_heuristic": vs_heuristic, "vs_unseen": vs_unseen,
            "minutes": elapsed / 60}


def main() -> None:
    utf8_output()   # a redirected stdout on Windows is cp1252
    parser = argparse.ArgumentParser(
        description="Train an agent on the wide state encoder.",
        epilog="See the module docstring for worked examples.")
    parser.add_argument("--games", type=int, default=40000,
                        help="self-play games of training (default: 40000)")
    parser.add_argument("--eval-games", type=int, default=200,
                        help="games per evaluation (default: 200)")
    parser.add_argument("--decks", nargs="*", default=[],
                        help="decklist files, globs or folders; a folder "
                             "expands to every list in it. Default is the ten "
                             "starter decks, e.g. --decks decks/v5_*.txt or "
                             "--decks decks/meta")
    parser.add_argument("--deck-pool-subfolders", action="store_true",
                        help="expand a folder in --decks recursively, so "
                             "--decks decks --deck-pool-subfolders trains on "
                             "every list under decks/ rather than only the "
                             "loose ones")
    parser.add_argument("--encoder", choices=list(ENCODERS), default="wide")
    parser.add_argument("--compare", action="store_true",
                        help="train both encoders on the same budget and decks")
    parser.add_argument("--eval-only", metavar="CHECKPOINT",
                        help="evaluate an existing checkpoint, no training")
    parser.add_argument("--resume", metavar="CHECKPOINT",
                        help="continue training an existing checkpoint. The "
                             "encoder is read off the file, not from "
                             "--encoder. Pass a fresh --seed too, or the run "
                             "replays the same games it already learned from")
    parser.add_argument("--random-decks", type=float, default=0.5)
    parser.add_argument("--league-every", type=int, default=500)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default=f"{AGENT_DIR}/rl_wide.pt")
    args = parser.parse_args()

    db = default_db()
    decks, described = load_decks(args.decks, db,
                                  subfolders=args.deck_pool_subfolders)
    print(f"training decks: {described}")

    if args.eval_only:
        net = Trainer.load_net(find_checkpoint(args.eval_only))
        name = "wide" if net.feature_dim == WideEncoder.dim else "stock"
        print(f"{args.eval_only}: {net.feature_dim}-wide rows -> {name} encoder")
        trainer = Trainer(decks, TrainConfig(games=0, seed=args.seed), db=db,
                          net=net, encoder=ENCODERS[name](db))
        print(f"  vs heuristic {trainer.evaluate(args.eval_games):.1%}   "
              f"vs unseen {trainer.evaluate(args.eval_games, unseen_decks=True):.1%}")
        return

    names = list(ENCODERS) if args.compare else [args.encoder]
    if args.resume:
        if args.compare:
            raise SystemExit("--resume continues one agent; it cannot be "
                             "combined with --compare, which trains two from "
                             "scratch on an equal budget")
        # Which encoder a checkpoint belongs to is a property of the file, so
        # read it rather than trusting --encoder: the mismatch would otherwise
        # surface as a shape error several minutes into a long run.
        detected = ("wide" if Trainer.load_net(find_checkpoint(args.resume)).feature_dim
                    == WideEncoder.dim else "stock")
        if detected != args.encoder:
            print(f"note: {args.resume} was trained with the {detected} "
                  f"encoder; using that rather than --encoder {args.encoder}")
        names = [detected]
    results = [train_one(name, decks, db, args) for name in names]

    if len(results) > 1:
        print(f"\n{'encoder':>8} {'rows':>6} {'vs heuristic':>14} {'vs unseen':>11}")
        for row in results:
            print(f"{row['encoder']:>8} {row['dim']:>6} "
                  f"{row['vs_heuristic']:>13.1%} {row['vs_unseen']:>10.1%}")
        wide, stock = results[0], results[1]
        delta = wide["vs_unseen"] - stock["vs_unseen"]
        print(f"\nwide encoder on unseen decks: {delta:+.1%} vs stock")
        print("Read `vs unseen`, not `vs heuristic` — see the module docstring.")

    Path(args.out).with_suffix(".summary.json").write_text(
        json.dumps(results, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
