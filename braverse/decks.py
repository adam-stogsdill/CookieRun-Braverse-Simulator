"""Starter decklists and deck validation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from . import config as cfg
from .cards import CardDB


def _expand(entries: dict[str, int]) -> list[str]:
    return [card_id for card_id, n in entries.items() for _ in range(n)]


# ST9 "Seas of Fate" — Blue, Sea Fairy Cookie.
ST9_SEA_FAIRY = _expand({
    "ST9-002": 4, "ST9-005": 4, "ST9-009": 4, "ST9-012": 4, "ST9-010": 4,  # LV1
    "ST9-001": 4, "ST9-007": 4, "ST9-003": 4, "ST9-004": 4,                # LV2
    "ST9-006": 4, "ST9-008": 4, "ST9-011": 4,                              # LV3
    "ST9-013": 3, "ST9-015": 3,                                            # items
    "ST9-018": 2, "ST9-019": 2,                                            # traps
    "ST9-020": 2,                                                          # stage
})

# ST8 — Green, Wind Archer Cookie.
ST8_WIND_ARCHER = _expand({
    "ST8-001": 4, "ST8-010": 4, "ST8-011": 4, "ST8-012": 4, "ST8-007": 4,  # LV1
    "ST8-003": 4, "ST8-008": 4, "ST8-002": 4, "ST8-009": 4,                # LV2
    "ST8-004": 4, "ST8-005": 4, "ST8-006": 4,                              # LV3
    "ST8-013": 3, "ST8-015": 3,                                            # items
    "ST8-018": 2, "ST8-019": 2,                                            # traps
    "ST8-020": 2,                                                          # stage
})

STARTER_DECKS = {
    "st9_sea_fairy": ST9_SEA_FAIRY,
    "st8_wind_archer": ST8_WIND_ARCHER,
}


@dataclass
class DeckReport:
    ok: bool
    problems: list[str]
    size: int
    flip_count: int
    level_counts: dict[int, int]


def validate(deck: list[str], db: CardDB, rules: cfg.RulesConfig = cfg.DEFAULT) -> DeckReport:
    problems: list[str] = []
    unknown = [c for c in deck if c not in db]
    if unknown:
        problems.append(f"unknown card ids: {sorted(set(unknown))[:5]}")

    known = [db[c] for c in deck if c in db]
    if len(deck) != rules.deck_size:
        problems.append(f"deck has {len(deck)} cards, expected {rules.deck_size}")

    # "You can include up to 4 cards with the same card number" — per number,
    # so alt-art reprints of one number share the cap.
    by_number = Counter(c.base_id for c in known)
    over = {n: k for n, k in by_number.items() if k > rules.max_copies_by_number}
    if over:
        problems.append(f"more than {rules.max_copies_by_number} copies: {over}")

    flips = sum(1 for c in known if c.is_flip)
    if flips > rules.max_flip_cards:
        problems.append(f"{flips} FLIP cards, max {rules.max_flip_cards}")

    if any(c.is_ban for c in known):
        problems.append("deck contains banned cards")

    levels = Counter(c.level for c in known if c.is_cookie)
    if rules.require_cookie_card and not any(c.is_cookie for c in known):
        problems.append("deck must include at least one Cookie card")

    return DeckReport(
        ok=not problems,
        problems=problems,
        size=len(deck),
        flip_count=flips,
        level_counts=dict(sorted(levels.items(), key=lambda kv: kv[0] or 0)),
    )
