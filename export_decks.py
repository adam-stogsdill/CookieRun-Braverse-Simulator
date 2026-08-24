#!/usr/bin/env python3
"""Split a co-evolution run's decks into one file per colour/archetype.

    python export_decks.py coevolved_v4.json coevolved_v5.json
    python export_decks.py coevolved_v3.json --dir decks

Writes ``decks/<run>_<ARCHETYPE>.txt`` — the best list each archetype reached
across the whole run, in the same readable-plus-JSON format ``evolve_deck.py``
writes, so ``compare_decks.py`` and ``build_tts_sheets.py`` can read them.

Runs from before per-archetype evolution recorded a single champion per round
rather than one per colour. Those are grouped by the colours their decks
actually play, so an older run still exports per-colour lists.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from braverse import default_db
from braverse.deckfile import DECK_DIR, archetype_name, run_tag, write_archetypes
from braverse.console import utf8_output


def best_by_archetype(history: list[dict], db) -> dict[str, dict]:
    """Best-holdout deck per archetype across every round of a run."""
    best: dict[str, dict] = {}
    for row in history:
        # Newer runs record every colour's champion; older ones just one deck.
        champions = row.get("champions") or [{
            "archetype": archetype_name(row["deck"], db),
            "deck": row["deck"],
            "holdout": row.get("deck_holdout"),
            "validation": row.get("deck_validation"),
        }]
        for champ in champions:
            name = champ.get("archetype") or archetype_name(champ["deck"], db)
            entry = {**champ, "archetype": name, "round": row["round"]}
            if name not in best or entry["holdout"] > best[name]["holdout"]:
                best[name] = entry
    return best


def main() -> None:
    utf8_output()   # a redirected stdout on Windows is cp1252
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", help="coevolved_*.json files")
    parser.add_argument("--dir", default=DECK_DIR)
    args = parser.parse_args()

    db = default_db()
    for run in args.runs:
        path = Path(run)
        if not path.exists():
            print(f"{run}: not found, skipping")
            continue
        history = json.loads(path.read_text(encoding="utf-8"))
        if not history:
            print(f"{run}: no rounds recorded, skipping")
            continue

        tag = run_tag(path)
        champions = best_by_archetype(history, db)
        written = write_archetypes(args.dir, tag, champions, db)
        print(f"\n{run}  ({len(history)} rounds) -> {len(written)} decks")
        for deck_path in written:
            champ = champions[deck_path.stem.split("_", 1)[1]]
            print(f"  {deck_path}   round {champ['round']:>3}   "
                  f"holdout {champ['holdout']:.1%}")


if __name__ == "__main__":
    main()
