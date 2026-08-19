"""Card database: load `braverse_cards.csv` into typed, engine-ready defs.

The dump is a community scrape and is dirty in predictable ways (``EXRTA`` for
``EXTRA``, ``PULPLE`` for ``PURPLE``, ``level3`` for ``3``, mixed case). All of
that is normalised here so nothing downstream has to know about it.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .cost import Cost
from .enums import CardType, Color, Keyword, Marker

DEFAULT_CSV = Path(__file__).resolve().parent.parent / "braverse_cards.csv"

_TYPE_FIXES = {"EXRTA": "EXTRA", "COOKIE": "COOKIE", "NPC": "NPC"}
_COLOR_FIXES = {"PULPLE": "PURPLE"}
_KEYWORD_FIXES = {"AREMA": "ARENA"}

# The dump writes some ability icons as shorthand tokens instead of the 【...】
# markers used elsewhere. `{bl}` is Blocker on 11 cards, which meant those
# Cookies were silently unable to block at all. `{mou}` is decorative.
_SYMBOL_FIXES = {"{bl}": "【Blocker】", "{mou}": "", "{mt)": "【Your Turn】"}


# The dump prints 【Activate】 on these cards; the cards themselves print
# 【On Play】. Checked badge by badge against the scans in `card_images/` —
# the two markers are colour-coded on the card face (teal for On Play,
# magenta for Activate), so they can be read off the art directly. Every
# affected card is from BS1-BS4, ST1-ST5 or P; nothing from BS5 onward is
# wrong, which is what a dump-side defect in the early sets looks like.
#
# The difference is not cosmetic. An 【Activate】 is a main-phase skill its
# controller presses, once per turn, for as long as the Cookie lives; an
# 【On Play】 fires once, as the Cookie is played, and never again. Read the
# wrong way round, a third of BS3's Cookies gain a repeatable skill they do
# not have.
_ON_PLAY_MISPRINTS = frozenset({
    "BS1-001", "BS1-017", "BS1-028", "BS1-029", "BS1-034", "BS1-036",
    "BS1-037", "BS1-054", "BS1-056", "BS1-063", "BS1-068", "BS1-071",
    "BS1-073", "BS2-002", "BS2-003", "BS2-012", "BS2-018", "BS2-022",
    "BS2-027", "BS2-031", "BS2-036", "BS2-046", "BS2-055", "BS2-057",
    "BS2-058", "BS2-061", "BS2-062", "BS2-064", "BS2-065", "BS2-068",
    "BS2-069", "BS3-002", "BS3-009", "BS3-010", "BS3-013", "BS3-026",
    "BS3-028", "BS3-030", "BS3-031", "BS3-036", "BS3-038", "BS3-040",
    "BS3-052", "BS3-055", "BS3-057", "BS3-060", "BS3-062", "BS3-063",
    "BS3-065", "BS3-076", "BS3-078", "BS3-083", "BS3-088", "BS3-089",
    "BS3-097", "BS3-098", "BS3-100", "BS3-109", "BS3-111", "BS3-112",
    "BS3-113", "BS4-004", "BS4-015", "BS4-025", "BS4-026", "BS4-028",
    "BS4-030", "BS4-033", "BS4-038", "BS4-046", "BS4-049", "BS4-053",
    "BS4-073", "BS4-074", "BS4-081", "BS4-082", "BS4-089", "BS4-092",
    "BS4-095", "BS4-099", "P-007", "P-010", "P-016", "P-018", "P-030",
    "P-041", "P-043", "P-044", "P-045", "P-046", "P-054", "P-055",
    "ST1-002", "ST1-007", "ST2-001", "ST2-004", "ST2-008", "ST2-010",
    "ST3-004", "ST3-010", "ST4-008", "ST4-013", "ST5-001", "ST5-006",
    "ST5-010", "ST5-015",
})


# Two rows carry a different card's text altogether — not a marker slip but the
# wrong card. Both are 【EXTRA】 【Awaken】 cards, and the dump gives them the
# rules text and attack line of the ordinary Cookie they awaken, which reads as
# a plausible card and so passes every structural check. Transcribed from the
# scans in `card_images/`. Values are (description, attack_text).
_TEXT_OVERRIDES: dict[str, tuple[str, str]] = {
    "BS10-024": (
        "【EXTRA】 <Discard 1 card.> You can 【Awaken】 your [Hollyberry Cookie] "
        "with 3 or less HP remaining.\n"
        "【On Play】 Until the end of your opponent's next turn, this Cookie "
        "receives -1 from all damage.",
        "<{R}{R}> Shield of Conviction deals 2\n"
        "Then, <{R}{R}> Select up to 1 of your opponent's Cookies. "
        "That Cookie receives 2 damage.",
    ),
    "BS10-073": (
        "【EXTRA】 If there are 8 cards or more in your support area, you can "
        "【Awaken】 your [White Lily Cookie].",
        "<{G}{G}{G}{G}> Dawn Lily Protection deals 4\n"
        "Then, <return 1 Cookie from your support area to your hand.> Place up "
        "to 1 card from the top of your deck in your support area as rested.",
    ),
}


def _fix_on_play(text: str, base_id: str) -> str:
    return text.replace("【Activate】", "【On Play】") \
        if base_id in _ON_PLAY_MISPRINTS else text

def _normalise_symbols(text: str) -> str:
    for token, replacement in _SYMBOL_FIXES.items():
        text = text.replace(token, replacement)
    return text

# `<{B}{B}> Gem Mermaid Strength deals 2 Then, ...`
# Two printings exist: `<{B}{B}> Gem Mermaid Strength deals 2` (named attack)
# and the older `<{P}{P}> Deals 2 damage.` (unnamed).
_ATTACK_RE = re.compile(r"^\s*<([^>]*)>\s*(.*?)\s*\bdeals?\s+(\d+)", re.S | re.I)
# A third printing drops the verb entirely: `<{P}> Myaha! 1`. Anchored to the
# end of the first line so it cannot swallow numbers from the rider text.
_ATTACK_TERSE_RE = re.compile(r"^\s*<([^>]*)>\s*([^\n]*?)\s+(\d+)\s*$", re.M)
_LEAD_COST_RE = re.compile(r"^\s*<((?:\{[A-Za-z]+\})+)>")
_LEAD_ANY_COST_RE = re.compile(r"^\s*<[^>]*>\s*")
_MARKER_RE = re.compile(r"【([^】]*)】")


def _norm(value: str, fixes: dict[str, str]) -> str:
    key = (value or "").strip().upper()
    return fixes.get(key, key)


@dataclass(frozen=True)
class Attack:
    """The attack line of a Cookie card."""

    name: str
    cost: Cost
    damage: int
    text: str = ""

    def __str__(self) -> str:
        return f"{self.name} {self.cost} → {self.damage}"


@dataclass(frozen=True)
class CardDef:
    """Immutable printed card. One per card id (alt arts collapse to base_id)."""

    id: str
    base_id: str
    set_id: str
    number: str
    name: str
    type: CardType
    color: Color
    energy_colors: tuple[Color, ...]
    level: int | None
    hp: int | None
    hp_is_modifier: bool
    keywords: frozenset[Keyword]
    markers: frozenset[Marker]
    rarity: str
    description: str
    attack_text: str
    flip_text: str
    play_cost: Cost
    attack: Attack | None
    is_ban: bool
    is_limit: bool

    @property
    def is_cookie(self) -> bool:
        return self.type.is_cookie

    @property
    def is_flip(self) -> bool:
        return self.type is CardType.FLIP

    def has(self, marker: Marker) -> bool:
        return marker in self.markers

    def __str__(self) -> str:
        if self.is_cookie:
            return f"{self.name} (LV{self.level} HP{self.hp} {self.color.value})"
        return f"{self.name} ({self.type.value} {self.color.value})"


def _parse_int(value: str) -> tuple[int | None, bool]:
    """Return (value, is_modifier). EXTRA Cookies print HP as ``+2``."""
    text = (value or "").strip()
    if not text:
        return None, False
    modifier = text.startswith("+")
    digits = re.sub(r"[^0-9]", "", text)
    return (int(digits) if digits else None), modifier


def _parse_energy(raw: str) -> tuple[Color, ...]:
    """`"BLUE MIX"`, `"RED YELLOW GREEN BLUE PURPLE"`, `"MIX"` → colours."""
    text = _norm(raw, _COLOR_FIXES).replace("MIX", " ")
    colors = [Color[w] for w in text.split() if w in Color.__members__]
    return tuple(dict.fromkeys(colors))


def _infer_color(rules_text: str) -> Color:
    """Best-effort colour from the energy symbols a card mentions."""
    from collections import Counter

    from .enums import SYMBOL_TO_COLOR

    counts = Counter(
        SYMBOL_TO_COLOR[sym.upper()]
        for sym in re.findall(r"\{([A-Za-z]+)\}", rules_text or "")
        if sym.upper() in SYMBOL_TO_COLOR
    )
    return counts.most_common(1)[0][0] if counts else Color.NONE


def _parse_attack(text: str) -> Attack | None:
    match = _ATTACK_RE.match(text or "") or _ATTACK_TERSE_RE.match(text or "")
    if not match:
        return None
    cost_text, name, damage = match.groups()
    rider = text[match.end():]
    # `Deals 3 damage.` matches through the number, leaving a bare "damage."
    # behind. That is not rider text and must not reach the effect compiler.
    rider = re.sub(r"^\s*damage\s*\.?", "", rider, count=1, flags=re.I)
    return Attack(
        name=" ".join(name.split()),
        cost=Cost.parse(cost_text),
        damage=int(damage),
        text=rider.strip(),
    )


def _parse_markers(text: str) -> frozenset[Marker]:
    found = set()
    for token in _MARKER_RE.findall(text or ""):
        for marker in Marker:
            if token.strip().lower() == marker.value.lower():
                found.add(marker)
    return frozenset(found)


def load_cards(path: str | Path = DEFAULT_CSV, *, drop_alt_art: bool = True) -> dict[str, CardDef]:
    """Load the CSV into ``{card_id: CardDef}``."""
    cards: dict[str, CardDef] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if drop_alt_art and row.get("is_alt_art") == "1":
                continue
            card = _row_to_def(row)
            if card is not None:
                cards[card.id] = card
    _backfill_set_colors(cards)
    return cards


def _backfill_set_colors(cards: dict[str, CardDef]) -> None:
    """Give colourless rows their set's colour.

    Starter (``ST``) sets are mono-colour, and the ST1–ST5 rows in the dump
    lost their colour field entirely. Adopting the set's dominant colour is
    strictly better than leaving them unplayable.
    """
    from collections import Counter
    from dataclasses import replace

    dominant: dict[str, Color] = {}
    per_set: dict[str, Counter] = {}
    for card in cards.values():
        if card.color is not Color.NONE:
            per_set.setdefault(card.set_id, Counter())[card.color] += 1
    for set_id, counts in per_set.items():
        dominant[set_id] = counts.most_common(1)[0][0]

    for card_id, card in list(cards.items()):
        if card.color is Color.NONE and card.set_id in dominant:
            color = dominant[card.set_id]
            cards[card_id] = replace(
                card, color=color, energy_colors=card.energy_colors or (color,)
            )


def _row_to_def(row: dict[str, str]) -> CardDef | None:
    type_text = _norm(row.get("type", ""), _TYPE_FIXES)
    if type_text not in CardType.__members__:
        return None
    card_type = CardType[type_text]

    color_text = _norm(row.get("color", ""), _COLOR_FIXES)
    color = Color[color_text] if color_text in Color.__members__ else Color.NONE
    if color is Color.NONE:
        # The ST1–ST5 rows in the dump have an empty colour field. Their own
        # cost symbols are the only surviving evidence of what colour they are.
        color = _infer_color(row.get("all_rules_text", ""))

    level, _ = _parse_int(row.get("cardLevel", ""))
    hp, hp_is_mod = _parse_int(row.get("hp", ""))

    keywords = set()
    for word in re.split(r"[,\s]+", _norm(row.get("keyword", ""), _KEYWORD_FIXES)):
        word = _KEYWORD_FIXES.get(word, word)
        if word in Keyword.__members__:
            keywords.add(Keyword[word])

    base_id = row.get("base_id") or row["id"].split("@")[0]
    if base_id in _TEXT_OVERRIDES:
        description, attack_text = _TEXT_OVERRIDES[base_id]
        all_text = description + "\n" + attack_text
    else:
        description = _fix_on_play(_normalise_symbols(row.get("description", "")), base_id)
        attack_text = _fix_on_play(_normalise_symbols(row.get("attackText", "")), base_id)
        all_text = _fix_on_play(_normalise_symbols(row.get("all_rules_text", "")), base_id)

    # The dump routinely files an ITEM/TRAP/STAGE's rules text under attackText
    # instead of description — sometimes the whole card (160 of them leave
    # description empty), sometimes only the 【Activate】 half of a stage whose
    # description holds just the placement line. None of these types has an
    # attack line, so the two fields are one body of rules text and joining them
    # is what lets the lead cost and the compiler see it at all. Cookies and NPCs
    # are left alone: for them attackText really is an attack.
    if card_type in (CardType.ITEM, CardType.TRAP, CardType.STAGE) and attack_text.strip():
        description = "\n".join(
            part for part in (description.strip(), attack_text.strip()) if part)
        attack_text = ""

    # ITEM/TRAP/STAGE print their activation cost at the head of the description.
    play_cost = Cost()
    if not card_type.is_cookie:
        lead = _LEAD_COST_RE.match(description)
        if lead:
            play_cost = Cost.parse(lead.group(1))

    flip_text = _fix_on_play(_normalise_symbols(row.get("flipText", "")), base_id)
    # Some rows duplicate the attack line into the flip field, minus its damage
    # number, so the attack *name* would be parsed as a flip effect. A flip
    # field that is only a cost plus the attack's name is that defect.
    parsed_attack = _parse_attack(attack_text)
    if parsed_attack is not None and parsed_attack.name:
        stripped = _LEAD_ANY_COST_RE.sub("", flip_text).strip().rstrip(".")
        if stripped and stripped == parsed_attack.name.rstrip("."):
            flip_text = ""

    return CardDef(
        id=row["id"],
        base_id=base_id,
        set_id=row.get("setId", ""),
        number=row.get("number", ""),
        name=" ".join(row.get("name", "").split()),
        type=card_type,
        color=color,
        energy_colors=_parse_energy(row.get("energyType", "")) or ((color,) if color else ()),
        level=level,
        hp=hp,
        hp_is_modifier=hp_is_mod,
        keywords=frozenset(keywords),
        markers=_parse_markers(all_text),
        rarity=row.get("rarity", ""),
        description=description,
        attack_text=attack_text,
        flip_text=flip_text,
        play_cost=play_cost,
        attack=parsed_attack,
        is_ban=row.get("isBan") == "1",
        is_limit=row.get("isLimit") == "1",
    )


class CardDB:
    """Lookup helper over the loaded card pool."""

    def __init__(self, cards: dict[str, CardDef]):
        self.cards = cards
        self._by_name: dict[str, list[CardDef]] = {}
        for card in cards.values():
            self._by_name.setdefault(card.name.lower(), []).append(card)

    def __getitem__(self, card_id: str) -> CardDef:
        return self.cards[card_id]

    def __contains__(self, card_id: str) -> bool:
        return card_id in self.cards

    def __len__(self) -> int:
        return len(self.cards)

    def by_name(self, name: str) -> list[CardDef]:
        return list(self._by_name.get(name.lower(), []))

    def one_by_name(self, name: str) -> CardDef:
        matches = self.by_name(name)
        if not matches:
            raise KeyError(f"no card named {name!r}")
        return matches[0]

    def set(self, set_id: str) -> list[CardDef]:
        return sorted(
            (c for c in self.cards.values() if c.set_id == set_id),
            key=lambda c: c.number,
        )


@lru_cache(maxsize=1)
def default_db() -> CardDB:
    return CardDB(load_cards())
