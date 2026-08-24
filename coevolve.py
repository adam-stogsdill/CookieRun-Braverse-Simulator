#!/usr/bin/env python3
"""Co-evolve the deck pool and the RL agent, alternating between them.

    python coevolve.py --rounds 60 --pilot both

Each round:
  1. evolve a deck, with the *current* agent (and optionally the heuristic)
     flying every candidate
  2. add the winner to the deck pool
  3. train the agent further on that widened pool

This fixes overfitting in both directions at once. Evolving against a fixed
pilot bakes in that pilot's blind spots; training on a fixed deck pool bakes in
that metagame. Alternating means neither side can sit still.

Built for unattended runs: state is written every round, the best agent seen is
kept separately from the latest, and the gauntlet is capped so rounds do not
get slower and noisier as the pool grows.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from braverse import STARTER_SET_IDS, default_db, starter_deck
from braverse.deckfile import DECK_DIR, run_tag, write_archetypes
from braverse.deckgen import (DeckEvolver, DeckGenConfig, describe,
                              implemented_pool, set_pool)
from braverse.rl import TrainConfig, Trainer
from braverse.console import utf8_output


def _pilot_from_net(net, db):
    from braverse.features import Encoder
    from braverse.rl import RLAgent

    encoder = Encoder(db)

    def factory(seat: int, seed: int):
        return RLAgent(net, seat, encoder=encoder, db=db,
                       training=False, seed=seed)

    return factory


def main() -> None:
    utf8_output()   # a redirected stdout on Windows is cp1252
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=60)
    parser.add_argument("--pilot", choices=("rl", "both"), default="both",
                        help="'both' scores every candidate under the heuristic "
                             "and the agent, keeping the worse score")
    parser.add_argument("--generations", type=int, default=25)
    parser.add_argument("--pop", type=int, default=24)
    parser.add_argument("--games", type=int, default=48,
                        help="gauntlet games per deck candidate")
    parser.add_argument("--train-games", type=int, default=10000,
                        help="RL games per round")
    parser.add_argument("--random-decks", type=float, default=0.5)
    parser.add_argument("--pool", choices=("starter_sets", "implemented"),
                        default="starter_sets")
    parser.add_argument("--sets", default="ST8,ST9",
                        help="card pool when --pool starter_sets: a comma list "
                             "of set ids, or 'all-starters' for ST1-ST10")
    parser.add_argument("--seed-decks", default="st9_sea_fairy,st8_wind_archer",
                        help="decks that seed the gauntlet: transcribed names "
                             "or set ids (ST4), or 'all-starters'")
    parser.add_argument("--colors", type=int, default=1,
                        help="colours a candidate deck may draw on "
                             "(0 = unrestricted; only sane on a 1-colour pool)")
    parser.add_argument("--archetypes", default="per-color",
                        help="'per-color' evolves one champion for every colour "
                             "in the pool each round; 'one' evolves a single "
                             "unpinned deck; or a list like 'RED,BLUE'")
    parser.add_argument("--gauntlet-size", type=int, default=6,
                        help="cap on gauntlet decks: every seed deck plus the "
                             "most recent evolved decks")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--checkpoint", default="rl_agent.pt",
                        help="starting weights; the latest is written back here")
    parser.add_argument("--out", default="coevolved.json")
    parser.add_argument("--deck-dir", default=DECK_DIR,
                        help="where per-archetype decklists are written, one "
                             "file per colour named for the run (v4_BLUE.txt)")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress per-generation lines during evolution")
    parser.add_argument("--hours", type=float, default=0.0,
                        help="stop cleanly after this many hours (0 = no limit)")
    args = parser.parse_args()

    def expand(spec: str) -> list[str]:
        if spec.strip() == "all-starters":
            return list(STARTER_SET_IDS)
        return [s.strip() for s in spec.split(",") if s.strip()]

    db = default_db()
    set_ids = expand(args.sets)
    pool = (set_pool(db, set_ids) if args.pool == "starter_sets"
            else implemented_pool(db))

    seed_names = expand(args.seed_decks)
    deck_pool = [starter_deck(db, name) for name in seed_names]
    n_seed = len(deck_pool)

    # Which colour each round's champions are pinned to. An empty tuple means
    # "unpinned", which is the old single-deck behaviour.
    probe = DeckEvolver(pool, deck_pool, DeckGenConfig(), db=db)
    if args.archetypes == "one":
        archetypes: list[tuple] = [()]
    elif args.archetypes == "per-color":
        archetypes = [(c,) for c in probe.available_colors()]
    else:
        archetypes = [(c,) for c in probe.resolve_colors(expand(args.archetypes))]

    checkpoint = Path(args.checkpoint)
    best_path = checkpoint.with_suffix(".best.pt")
    try:
        net = Trainer.load_net(checkpoint)
        print(f"continuing from {checkpoint} "
              f"({net.feature_dim}-wide rows)")
    except FileNotFoundError:
        net = None
        print("starting from a fresh policy")
    except Exception as exc:
        # Only a *missing* checkpoint means "start fresh". A file that exists
        # but will not load is a real problem, and swallowing it would silently
        # discard a trained policy and quietly restart an overnight run from
        # scratch — which looks like a successful run until the numbers land.
        raise SystemExit(
            f"{checkpoint} exists but could not be loaded: {exc}\n"
            f"Move it aside to start fresh, or point --checkpoint elsewhere.")

    print(f"card pool: {len(pool)} ({args.pool}"
          f"{': ' + ','.join(set_ids) if args.pool == 'starter_sets' else ''})"
          f"   pilot: {args.pilot}")
    print(f"seed decks: {len(deck_pool)} ({', '.join(seed_names)})")
    print("archetypes: " + ", ".join(
        "+".join(c.value for c in a) if a else "any" for a in archetypes)
        + f"  ({len(archetypes)} deck(s) evolved per round)")
    print(f"started {datetime.now():%Y-%m-%d %H:%M}")
    if args.hours:
        print(f"will stop by {datetime.now() + timedelta(hours=args.hours):%H:%M}")

    history: list[dict] = []
    best_score = -1.0
    started = time.time()
    tag = run_tag(args.out)
    # Best list each archetype has reached so far. Kept across rounds and
    # rewritten every round: a run that is stopped early — or killed — should
    # still leave its decks on disk rather than nothing at all.
    best_by_archetype: dict[str, dict] = {}

    for round_index in range(args.rounds):
        if args.hours and (time.time() - started) > args.hours * 3600:
            print(f"\nreached the {args.hours}h limit; stopping cleanly")
            break

        elapsed = time.time() - started
        print(f"\n=== round {round_index + 1}/{args.rounds} "
              f"({elapsed / 3600:.1f}h elapsed, {datetime.now():%H:%M}) ===",
              flush=True)

        # 1. Evolve against the current agent. A fresh evolver each round: the
        #    fitness cache was measured under a different pilot and is stale.
        scripted = DeckEvolver(pool, deck_pool, DeckGenConfig(),
                               db=db)._default_agent
        pilot, pilots = None, None
        if net is None:
            pilot = scripted
        elif args.pilot == "both":
            pilots = [scripted, _pilot_from_net(net, db)]
        else:
            pilot = _pilot_from_net(net, db)

        # Cap the gauntlet: an unbounded one makes each round slower and every
        # matchup rarer, so scores get noisier the longer the run goes.
        keep = max(0, args.gauntlet_size - n_seed)
        gauntlet = deck_pool[:n_seed] + (deck_pool[n_seed:][-keep:] if keep
                                         else [])

        # Evolution is the longer half of a round and the training bar does not
        # cover it, so echo generation lines to keep the run observable.
        def gen_log(line: str) -> None:
            if not args.quiet:
                print("    " + line.strip(), flush=True)

        # One champion per colour, so a round produces a metagame rather than a
        # single deck. Every colour is evolved against the same gauntlet and the
        # same pilot, so the holdout numbers are comparable across archetypes.
        champions: list[dict] = []
        for slot, colors in enumerate(archetypes):
            label = "+".join(c.value for c in colors) if colors else "any"
            deck_cfg = DeckGenConfig(
                population=args.pop, generations=args.generations,
                games_per_eval=args.games, color_identity=args.colors,
                seed=args.seed + round_index * 977 + slot * 31,
            )
            evolver = DeckEvolver(pool, gauntlet, deck_cfg, db=db,
                                  agent_factory=pilot, agent_factories=pilots,
                                  colors=colors or None)
            if not args.quiet:
                print(f"  [{label}]", flush=True)
            deck, score, _ = evolver.evolve(log=gen_log)
            holdout = evolver.holdout(deck, games=200)
            print(f"  deck {label:>8}:  validation {score:.1%}   "
                  f"holdout {holdout:.1%}", flush=True)
            champions.append({"archetype": label, "deck": list(deck),
                              "validation": score, "holdout": holdout})
            deck_pool.append(list(deck))

        for champ in champions:
            current = best_by_archetype.get(champ["archetype"])
            if current is None or champ["holdout"] > current["holdout"]:
                best_by_archetype[champ["archetype"]] = {
                    **champ, "round": round_index + 1}
        written = write_archetypes(args.deck_dir, tag, best_by_archetype, db)
        print(f"  decks: {len(written)} -> {args.deck_dir}/{tag}_*.txt",
              flush=True)

        best_champ = max(champions, key=lambda c: c["holdout"])
        deck, score, holdout = (best_champ["deck"], best_champ["validation"],
                                best_champ["holdout"])

        # 2. Train the agent on the widened pool.
        train_cfg = TrainConfig(
            games=args.train_games, eval_every=0, league_every=500,
            random_deck_share=args.random_decks,
            seed=args.seed + 10_000 + round_index,
        )
        trainer = Trainer(deck_pool, train_cfg, db=db, net=net)
        trainer.train(log=lambda *_: None)
        net = trainer.net

        vs_heuristic = trainer.evaluate(200)
        vs_unseen = trainer.evaluate(200, unseen_decks=True)
        combined = (vs_heuristic + vs_unseen) / 2
        print(f"  agent: vs heuristic {vs_heuristic:.1%}   "
              f"unseen decks {vs_unseen:.1%}", flush=True)

        # Always write the latest, but keep the best separately. Co-evolution
        # can regress, and an unattended run must not overwrite a good policy
        # with a worse one.
        trainer.save(checkpoint)
        if combined > best_score:
            best_score = combined
            shutil.copyfile(checkpoint, best_path)
            print(f"  new best ({combined:.1%}) -> {best_path}", flush=True)

        history.append({
            "round": round_index + 1,
            "deck_validation": score,
            "deck_holdout": holdout,
            "agent_vs_heuristic": vs_heuristic,
            "agent_vs_unseen": vs_unseen,
            "is_best": combined >= best_score,
            "deck": deck,
            "champions": champions,
        })
        # Written every round: a crash at 4am must not lose the whole night.
        Path(args.out).write_text(json.dumps(history, indent=1),
                              encoding="utf-8")

    if not history:
        print("no rounds completed")
        return

    print(f"\nfinished {len(history)} rounds in "
          f"{(time.time() - started) / 3600:.1f}h")
    print(f"{'round':>6} {'deck holdout':>14} {'vs heuristic':>14} {'unseen':>9}")
    for row in history:
        mark = " *" if row["is_best"] else ""
        print(f"{row['round']:>6} {row['deck_holdout']:>13.1%} "
              f"{row['agent_vs_heuristic']:>13.1%} {row['agent_vs_unseen']:>8.1%}{mark}")

    # One champion per archetype is the point of the per-colour search: a single
    # overall winner would throw away four fifths of what the run measured.
    print(f"\nbest deck per archetype:")
    print(f"{'archetype':>10} {'round':>6} {'holdout':>9}")
    for label, champ in sorted(best_by_archetype.items(),
                               key=lambda kv: -kv[1]["holdout"]):
        print(f"{label:>10} {champ['round']:>6} {champ['holdout']:>8.1%}")

    paths = write_archetypes(args.deck_dir, tag, best_by_archetype, db)

    for label, champ in sorted(best_by_archetype.items(),
                               key=lambda kv: -kv[1]["holdout"]):
        print(f"\n--- {label} (round {champ['round']}, "
              f"holdout {champ['holdout']:.1%}) ---")
        print(describe(champ["deck"], db))

    print(f"\nlatest agent -> {checkpoint}")
    print(f"best agent   -> {best_path}  ({best_score:.1%} combined)")
    print(f"log          -> {args.out}")
    for path in paths:
        print(f"deck         -> {path}")


if __name__ == "__main__":
    main()
