#!/usr/bin/env python3
"""What the engine can and cannot play yet.

    python coverage_report.py            # overall + per-set coverage
    python coverage_report.py --phrases  # most common rules sentences, for
                                         # prioritising the next batch of work
"""

from __future__ import annotations

import argparse
import collections
import re

from braverse import default_db
from braverse.effects import Trigger, get_effect, is_implemented

TRIGGERS = list(Trigger)


def needs_effect(card) -> bool:
    """True when the card does something the vanilla rules cannot express."""
    text = " ".join([card.description, card.flip_text,
                     card.attack.text if card.attack else ""])
    # 【Blocker】 is handled structurally by the engine, so a card whose only
    # text is the Blocker line plus its reminder needs no implementation.
    text = re.sub(r"【Blocker】\s*(?:<[^>]*>)?\s*\([^)]*\)", "", text)
    return bool(text.strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phrases", action="store_true")
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args()

    db = default_db()
    cards = list(db.cards.values())

    need = [c for c in cards if needs_effect(c)]
    have = [c for c in need if is_implemented(c.id)]
    vanilla = [c for c in cards if not needs_effect(c)]

    from braverse.compiler import compile_card

    hand_written = [c for c in have if not any(
        hasattr(get_effect(c.id, t), "clauses") for t in TRIGGERS
        if get_effect(c.id, t))]
    compiled = len(have) - len(hand_written)
    partial = [c for c in need
               if (r := compile_card(c)).programs and not r.ok
               and c not in have]

    print(f"cards in pool              {len(cards)}")
    print(f"  playable as printed      {len(vanilla)}  (stats + attack only)")
    print(f"  need an effect           {len(need)}")
    print(f"  effect implemented       {len(have)}  ({len(have) / len(need):.1%})")
    print(f"    hand-written           {len(hand_written)}")
    print(f"    compiled from text     {compiled}")
    print(f"  partially understood     {len(partial)}  (held back on purpose)")
    print(f"  fully playable pool      {len(vanilla) + len(have)}")

    print("\nper set (cards needing effects / implemented):")
    per_set = collections.Counter(c.set_id for c in need)
    done_set = collections.Counter(c.set_id for c in have)
    for set_id, count in sorted(per_set.items(), key=lambda kv: -kv[1]):
        mark = "  <-- playable" if done_set[set_id] == count else ""
        print(f"  {set_id:6} {done_set[set_id]:4}/{count:<4}{mark}")

    if args.phrases:
        print("\nmost common rules sentences (targets for a text compiler):")
        counts: collections.Counter[str] = collections.Counter()
        for card in need:
            text = " ".join([card.description, card.flip_text,
                             card.attack.text if card.attack else ""])
            for sentence in re.split(r"(?<=\.)\s+", text):
                sentence = re.sub(r"\d+", "N", sentence.strip())
                sentence = re.sub(r"\[[^\]]+\]", "[CARD]", sentence)
                sentence = re.sub(r"\s+", " ", sentence)
                if 10 < len(sentence) < 120:
                    counts[sentence] += 1
        for sentence, count in counts.most_common(args.top):
            print(f"  {count:5}  {sentence}")


if __name__ == "__main__":
    main()
