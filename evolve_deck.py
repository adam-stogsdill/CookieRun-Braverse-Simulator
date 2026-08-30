#!/usr/bin/env python3
"""Evolve a decklist against a gauntlet of existing decks.

    python evolve_deck.py                              # default: ST8+ST9 pool
    python evolve_deck.py --generations 30 --pop 32
    python evolve_deck.py --pool implemented --out best_deck.txt

    # tune a deck you already play, against the tournament field, keeping
    # every generation's champion as a playable list you can load and try
    python evolve_deck.py --seed-deck decks/MyGreenDeck.txt \
        --gauntlet decks/meta --checkpoints decks/green_run \
        --out decks/MyGreenDeck_evolved.txt
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Sequence

from braverse import STARTER_DECKS, default_db, validate
from braverse.deckfile import META_DIR, read_any, read_pool, write_deck
from braverse.deckgen import (DeckEvolver, DeckGenConfig, describe,
                              implemented_pool, set_pool, thin_share)
from braverse.console import utf8_output


def load_lists(entries: list[str]) -> list[tuple[str, list[str], list[str]]]:
    """Resolve deck arguments into ``(name, deck, extra)``.

    An entry is a starter name, a decklist file, or a folder of them — a
    folder expands to every list in it, which is how the tournament pool is
    passed as one word. Files are read with `read_any`, so a list somebody
    exported from the deck builder or typed by hand loads here the same as one
    this script wrote.
    """
    out: list[tuple[str, list[str], list[str]]] = []
    for entry in entries:
        if entry in STARTER_DECKS:
            out.append((entry, list(STARTER_DECKS[entry]), []))
            continue
        path = Path(entry)
        if path.is_dir():
            pool = read_pool(path)
            if not pool:
                raise SystemExit(f"no decklists in {entry}")
            out.extend(pool)
            continue
        deck, extra = read_any(path)
        out.append((path.stem, deck, extra))
    return out


def load_gauntlet(entries: list[str]) -> tuple[list[list[str]], list[str]]:
    """``--gauntlet`` as decks plus display names.

    The EXTRA deck of a gauntlet member is dropped rather than half-played:
    `DeckEvolver` fields one pile per seat, and a gauntlet that quietly loses a
    deck's second pile is scoring against a deck nobody brought. The name is
    starred so the report says which members are being flown short.
    """
    lists = load_lists(entries)
    return ([deck for _, deck, _ in lists],
            [name + ("*" if extra else "") for name, _, extra in lists])


class Checkpointer:
    """Writes each generation's champion into a folder, as a playable deck.

    A checkpoint is a *decklist*, not a snapshot of the search: it goes through
    `write_deck` like everything else in `decks/`, so the run's generation 12
    can be loaded in the deck menu, handed to `compare_decks.py`, or seeded
    into the next run without anything having to understand this file. Its
    header carries the generation and the score that generation was picked on,
    which is a training score on the block that generation was scored on --
    the honest number is the holdout printed at the end of the run.

    ``_best.txt`` is rewritten only when the champion actually improves, so the
    folder always has one file that is the answer so far, whatever happens to
    the process.
    """

    def __init__(self, directory: str | None, every: int, generations: int,
                 db, extra: Sequence[str] = (), **meta):
        self.dir = Path(directory) if directory else None
        self.every = max(1, every)
        self.last = generations - 1
        self.db = db
        self.extra = list(extra)
        self.meta = {k: v for k, v in meta.items() if v}
        self.written: list[Path] = []
        self.best = float("-inf")
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, generation: int, deck: list[str], row: dict) -> None:
        if not self.dir:
            return
        score = row.get("best", 0.0)
        improved = score > self.best
        self.best = max(self.best, score)
        due = generation % self.every == 0 or generation == self.last
        for name, when in ((f"gen{generation:03}", due), ("_best", improved)):
            if not when:
                continue
            path = write_deck(self.dir / f"{name}.txt", deck, self.db,
                              extra=self.extra, generation=generation,
                              score=round(score, 4),
                              mean=round(row.get("mean", 0.0), 4), **self.meta)
            if name != "_best":
                self.written.append(path)


def main() -> None:
    utf8_output()   # a redirected stdout on Windows is cp1252
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
    parser.add_argument("--gauntlet", nargs="+", metavar="DECK",
                        default=["st9_sea_fairy", "st8_wind_archer"],
                        help=f"what a candidate is scored against: starter "
                             f"names, decklist files, or a folder of them "
                             f"(e.g. {META_DIR}). A deck is only as strong as "
                             f"the field it was measured on, so this is the "
                             f"single most important argument here")
    parser.add_argument("--seed-deck", nargs="+", metavar="DECK", default=[],
                        help="start from decks somebody already built (starter "
                             "names, files, or a folder) instead of from noise "
                             "— a tuning pass on a real list rather than a "
                             "search from scratch")
    parser.add_argument("--consolidate", type=float, default=0.05,
                        metavar="W",
                        help="price thin slots at W win rate: the search "
                             "maximises win rate minus W times the share of "
                             "the 60 in stacks below --min-copies. Small on "
                             "purpose -- it breaks ties the gauntlet cannot, "
                             "so a 1-of has to actually be earning its slot. "
                             "0 disables it and evolves on win rate alone")
    parser.add_argument("--min-copies", type=int, default=2, metavar="N",
                        help="copies a card needs before it stops counting as "
                             "a thin slot (2 = no 1-ofs; 4 asks for playsets "
                             "throughout, which usually costs more win rate "
                             "than it is worth)")
    parser.add_argument("--out", default="decks/evolved_deck.txt")
    parser.add_argument("--force", action="store_true",
                        help="allow overwriting an existing --out file")
    parser.add_argument("--checkpoints", metavar="DIR", default=None,
                        help="save each generation's champion into DIR as a "
                             "playable decklist, plus a running _best.txt. A "
                             "run against a real field takes hours; without "
                             "this, a crash or a closed lid throws all of it "
                             "away and there is nothing to look at until the "
                             "end")
    parser.add_argument("--checkpoint-every", type=int, default=1, metavar="N",
                        help="write a numbered checkpoint every N generations "
                             "(the last generation is always written, and "
                             "_best.txt is rewritten whenever the champion "
                             "improves)")
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

    gauntlet, names = load_gauntlet(args.gauntlet)
    print(f"gauntlet: {len(gauntlet)} decks ({', '.join(names)})")
    config = DeckGenConfig(population=args.pop, generations=args.generations,
                           games_per_eval=args.games, seed=args.seed,
                           consolidation_weight=max(0.0, args.consolidate),
                           consolidation_floor=max(1, args.min_copies))
    if config.consolidation_weight:
        print(f"consolidation: {config.consolidation_weight:.1%} win rate "
              f"charged against slots under {config.consolidation_floor}x")
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

    seed_lists = load_lists(args.seed_deck) if args.seed_deck else []
    seeds = [deck for _, deck, _ in seed_lists]
    seed_names = [name for name, _, _ in seed_lists]
    if seeds:
        print(f"seeded from: {', '.join(seed_names)}")

    # The search is over the 60, and only the 60: `DeckEvolver` fields one pile
    # per seat. A seed that brought an EXTRA pile keeps it in everything we
    # write, so the file stays the deck the person plays -- but it took no part
    # in any of the numbers below, and the report says so rather than letting a
    # win rate be read as the whole deck's.
    extra = next((e for _, _, e in seed_lists if e), [])
    if extra:
        print(f"carrying the seed's {len(extra)}-card EXTRA pile through "
              f"unplayed (evolution scores the main 60 only)")

    checkpoints = Checkpointer(args.checkpoints, args.checkpoint_every,
                               args.generations, db, extra,
                               gauntlet=names, seeded_from=seed_names)

    started = time.time()
    deck, score, history = evolver.evolve(seeds=seeds,
                                          on_generation=checkpoints.save)
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
    thin = thin_share(deck, db, floor=config.consolidation_floor)
    print(f"thin slots: {thin:.1%} of the deck is under "
          f"{config.consolidation_floor}x"
          + (f" (priced at {config.consolidation_weight * thin:.1%} win rate "
             f"during the search)" if config.consolidation_weight else ""))
    print(f"\n{describe(deck, db)}")

    # A champion that is not a legal deck is a bug in the search, not a result.
    # Writing it anyway puts a file in `decks/` that looks like every other
    # decklist and cannot be played -- which is how three EXTRA cards spent a
    # 25-generation run sitting in a main deck.
    report = validate(deck, db, extra=extra)
    if not report.ok:
        raise SystemExit(f"the evolved deck is not legal ({'; '.join(report.problems)})"
                         f" -- nothing written to {args.out}")

    write_deck(args.out, deck, db, extra=extra, validation_score=score,
               holdout=holdout, baselines=baselines, seeded_from=seed_names,
               history=history)
    print(f"\nsaved -> {args.out}")
    if checkpoints.written:
        print(f"{len(checkpoints.written)} checkpoints -> {args.checkpoints}")


if __name__ == "__main__":
    main()
