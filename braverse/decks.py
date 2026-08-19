"""Starter decklists and deck validation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from . import config as cfg
from .cards import CardDB
from .enums import CardType


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

# Every ST set is a monocolour preconstructed product, but only ST8 and ST9 are
# transcribed above. The rest are derived on demand by build_starter_deck.
STARTER_SET_IDS = tuple(f"ST{i}" for i in range(1, 11))


def build_starter_deck(db: CardDB, set_id: str,
                       rules: cfg.RulesConfig = cfg.DEFAULT) -> list[str]:
    """Derive a legal 60-card list using only cards from one starter set.

    The shape follows the transcribed ST8/ST9 lists: four copies each of a
    5/4/3 LV1/LV2/LV3 Cookie line, then 3+3 items, 2+2 traps and 2 stages.
    Sets are not uniformly shaped — ST2, ST3 and ST10 are short a Cookie at
    some level — so anything still missing at the end is topped up from the
    rest of the set, Cookies first.
    """
    cards = sorted((c for c in db.cards.values()
                    if c.set_id == set_id and not c.is_ban),
                   key=lambda c: c.number)
    if not cards:
        raise KeyError(f"no cards for set {set_id!r}")

    cookies = [c for c in cards if c.is_cookie]
    items = [c for c in cards if c.type.value == "ITEM"]
    traps = [c for c in cards if c.type.value == "TRAP"]
    stages = [c for c in cards if c.type.value == "STAGE"]

    deck: list[str] = []
    copies: Counter = Counter()
    flips = 0

    def add(card, n: int) -> None:
        nonlocal flips
        room = rules.max_copies_by_number - copies[card.base_id]
        if card.is_flip:
            room = min(room, rules.max_flip_cards - flips)
        n = max(0, min(n, room, rules.deck_size - len(deck)))
        deck.extend([card.id] * n)
        copies[card.base_id] += n
        flips += n * card.is_flip

    for level, want in ((1, 5), (2, 4), (3, 3)):
        tier = [c for c in cookies if c.level == level]
        # Prefer non-FLIP Cookies once four copies would breach the FLIP cap.
        tier.sort(key=lambda c: (c.is_flip and flips + 4 > rules.max_flip_cards,
                                 c.number))
        for card in tier[:want]:
            add(card, 4)
    for card in items[:2]:
        add(card, 3)
    for card in traps[:2]:
        add(card, 2)
    for card in stages[:1]:
        add(card, 2)

    for card in cookies + items + traps + stages:
        if len(deck) >= rules.deck_size:
            break
        add(card, rules.deck_size - len(deck))

    report = validate(deck, db, rules)
    if not report.ok:
        raise ValueError(f"{set_id}: {report.problems}")
    return deck


def starter_deck(db: CardDB, name: str,
                 rules: cfg.RulesConfig = cfg.DEFAULT) -> list[str]:
    """A starter list by name: a transcribed one, or a set id like ``ST4``."""
    if name in STARTER_DECKS:
        return list(STARTER_DECKS[name])
    return build_starter_deck(db, name.upper(), rules)


@dataclass
class DeckReport:
    ok: bool
    problems: list[str]
    size: int
    flip_count: int
    level_counts: dict[int, int]
    extra_size: int = 0


def validate(deck: list[str], db: CardDB, rules: cfg.RulesConfig = cfg.DEFAULT,
             extra: list[str] | None = None) -> DeckReport:
    """Check a 60-card deck, and the EXTRA deck beside it.

    The two piles are built separately and checked together: the 4-per-number
    cap spans both, because it counts card numbers you own, not zones.
    """
    problems: list[str] = []
    extra = list(extra or [])
    unknown = [c for c in (*deck, *extra) if c not in db]
    if unknown:
        problems.append(f"unknown card ids: {sorted(set(unknown))[:5]}")

    known = [db[c] for c in deck if c in db]
    known_extra = [db[c] for c in extra if c in db]
    if len(deck) != rules.deck_size:
        problems.append(f"deck has {len(deck)} cards, expected {rules.deck_size}")

    # EXTRA cards are played out of their own pile and are never drawn, so one
    # sitting in the main 60 is a dead card that also breaks the count.
    if not rules.extra_cards_in_main_deck:
        misfiled = sorted({c.id for c in known if c.type is CardType.EXTRA})
        if misfiled:
            problems.append(f"EXTRA cards belong in the EXTRA deck: {misfiled[:5]}")
    not_extra = sorted({c.id for c in known_extra if c.type is not CardType.EXTRA})
    if not_extra:
        problems.append(f"EXTRA deck holds non-EXTRA cards: {not_extra[:5]}")
    if len(extra) > rules.extra_deck_size:
        problems.append(f"EXTRA deck has {len(extra)} cards, "
                        f"max {rules.extra_deck_size}")

    # "You can include up to 4 cards with the same card number" — per number,
    # so alt-art reprints of one number share the cap, and so do the two piles.
    by_number = Counter(c.base_id for c in (*known, *known_extra))
    over = {n: k for n, k in by_number.items() if k > rules.max_copies_by_number}
    if over:
        problems.append(f"more than {rules.max_copies_by_number} copies: {over}")

    flips = sum(1 for c in known if c.is_flip)
    if flips > rules.max_flip_cards:
        problems.append(f"{flips} FLIP cards, max {rules.max_flip_cards}")

    if any(c.is_ban for c in (*known, *known_extra)):
        problems.append("deck contains banned cards")

    levels = Counter(c.level for c in known if c.is_cookie)
    if rules.require_cookie_card and not any(c.is_cookie for c in known):
        problems.append("deck must include at least one Cookie card")

    return DeckReport(
        ok=not problems,
        problems=problems,
        size=len(deck),
        extra_size=len(extra),
        flip_count=flips,
        level_counts=dict(sorted(levels.items(), key=lambda kv: kv[0] or 0)),
    )
