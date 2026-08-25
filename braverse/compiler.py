"""Compile card rules text into :mod:`braverse.effect_ir` programs.

The text is templated enough to parse compositionally: a clause is an optional
run of ``<...>`` costs, an optional ``If ...,`` guard, and a verb phrase drawn
from a small vocabulary. That beats whole-sentence templates because the same
atoms recombine endlessly across the pool.

The hard rule is **all or nothing**: a card is only compiled if *every* clause
in its text is understood. A card that half-resolves is worse than a card that
stays vanilla, because it silently misreports what the game does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .cards import CardDB, CardDef
from .cost import Cost
from .effect_ir import (AnyOf, Modal, REF_HOST, REF_IT, REF_SELF, SCOPE_ALL,
                        SCOPE_OPPONENT,
                        SCOPE_OWN, ZONE_BATTLE, ZONE_BREAK, ZONE_DECK_BOTTOM,
                        ZONE_DECK_TOP, ZONE_HAND, ZONE_SUPPORT, ZONE_TRASH,
                        CardFilter,
                        Clause, Condition, Damage, MoveCards,
                        DamageEqualToRested, Discard, Draw, Faint, Filter,
                        GainHP, Guard, MillDeck, MillToSupport, ModifyAttack,
                        Op, PayCost, Program, RestSupport, ReturnSupportToHand,
                        ReturnToHand, Select, SetSupportActive, TrashHP,
                        TrashHPUntil, TrashStage)
from .effects import Trigger
from .enums import SYMBOL_TO_COLOR, CardType, Color, Keyword

# ---------------------------------------------------------------------------
# text preparation
# ---------------------------------------------------------------------------

_MARKER = re.compile(r"【([^】]*)】")
# 【Arena】/【Ancient】/... are card subtypes used as *filters* inside effect
# text ("Return 5 【Arena】 cards..."). Deleting them silently widened those
# filters to "any card", so they are kept as plain words; only the ability
# markers (【Activate】, 【On Play】, ...) are stripped.
_TYPE_MARKERS = {"arena", "ancient", "beast", "dragon"}


def _strip_markers(text: str) -> str:
    return _MARKER.sub(
        lambda m: f" {m.group(1)} " if m.group(1).strip().lower() in _TYPE_MARKERS
        else " ", text)
_BLOCKER_REMINDER = re.compile(r"\([^)]*redirect the attack[^)]*\)", re.I)
_PAREN = re.compile(r"\([^)]*\)")
_COST_TOKEN = re.compile(r"<([^>]*)>")
_ENERGY_ONLY = re.compile(r"^(?:\{[A-Za-z]+\})+$")

# "LV.2" would otherwise be split as a sentence end.
_LV_GUARD = "\x00LV\x00"


def _protect(text: str) -> str:
    # "LV.3" and a bare "LV." ("your break area LV. is higher than ...") both
    # end in a period that is not a sentence break.
    return re.sub(r"LV\.", _LV_GUARD, text)


def _restore(text: str) -> str:
    return text.replace(_LV_GUARD, "LV.")


def split_clauses(text: str) -> list[str]:
    """Break rules text into individually-resolvable sentences."""
    text = _BLOCKER_REMINDER.sub(" ", text or "")
    text = _strip_markers(text)
    text = _PAREN.sub(" ", text)
    text = _protect(text.replace("\n", " "))
    parts = re.split(r"(?<=\.)\s+", text)
    out = []
    for part in parts:
        part = _restore(re.sub(r"\s+", " ", part)).strip()
        if part:
            out.append(part)
    return out


NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3}


def _number(token: str | None, default: int = 1) -> int:
    if token is None:
        return default
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return NUMBER_WORDS.get(token, default)


def _color(symbol: str) -> Color | None:
    return SYMBOL_TO_COLOR.get(symbol.upper())


class CompileError(Exception):
    """Raised when a clause is not in the supported grammar."""


# ---------------------------------------------------------------------------
# filters and conditions
# ---------------------------------------------------------------------------

_LEVEL_AT_MOST = re.compile(r"LV\.(\d+) or lower", re.I)
_LEVEL_AT_LEAST = re.compile(r"LV\.(\d+) or higher", re.I)
_LEVEL_EXACT = re.compile(r"LV\.(\d+)(?! or)", re.I)
_HP_AT_MOST = re.compile(r"(\d+) or less HP remaining", re.I)
_HP_AT_LEAST = re.compile(r"(\d+) or more HP remaining", re.I)
_NAMED = re.compile(r"\[([^\]]+)\]")
_KEYWORD = re.compile(r"\b(Arena|Ancient|Beast|Dragon)\b", re.I)
_COLOR_SYMBOL = re.compile(r"\{([A-Za-z])\}")


def parse_filter(phrase: str, *, exclude_self: bool = False) -> Filter:
    kw = _KEYWORD.search(phrase)
    named = _NAMED.search(phrase)
    color = _COLOR_SYMBOL.search(phrase)
    at_most = _LEVEL_AT_MOST.search(phrase)
    at_least = _LEVEL_AT_LEAST.search(phrase)
    exact = None
    if not at_most and not at_least:
        found = _LEVEL_EXACT.search(phrase)
        exact = int(found.group(1)) if found else None
    hp_max = _HP_AT_MOST.search(phrase)
    hp_min = _HP_AT_LEAST.search(phrase)
    return Filter(
        max_level=int(at_most.group(1)) if at_most else None,
        min_level=int(at_least.group(1)) if at_least else None,
        exact_level=exact,
        max_hp=int(hp_max.group(1)) if hp_max else None,
        min_hp=int(hp_min.group(1)) if hp_min else None,
        color=_color(color.group(1)) if color else None,
        keyword=Keyword[kw.group(1).upper()] if kw else None,
        name=named.group(1) if named else None,
        exclude_self=exclude_self or "other" in phrase.lower(),
    )


_CONDITION_RULES = [
    (re.compile(r"there (?:are|is) (\d+) cards? or less in your hand", re.I),
     lambda m: Condition("hand_size", "<=", int(m.group(1)))),
    (re.compile(r"there (?:are|is) (\d+) cards? or more in your hand", re.I),
     lambda m: Condition("hand_size", ">=", int(m.group(1)))),
    (re.compile(r"there (?:are|is) (\d+) cards? or more in your support area", re.I),
     lambda m: Condition("support_count", ">=", int(m.group(1)))),
    (re.compile(r"there (?:are|is) (\d+) cards? or less in your support area", re.I),
     lambda m: Condition("support_count", "<=", int(m.group(1)))),
    (re.compile(r"the number of cards in both support areas match", re.I),
     lambda m: Condition("supports_equal")),
    (re.compile(r"there (?:are|is) (\d+) (?:【)?(Arena|Ancient|Beast|Dragon)(?:】)?"
                r" cards? or more in your trash", re.I),
     lambda m: Condition("trash_count", ">=", int(m.group(1)),
                         keyword=Keyword[m.group(2).upper()])),
    (re.compile(r"there (?:are|is) (\d+) cards? or more in your trash", re.I),
     lambda m: Condition("trash_count", ">=", int(m.group(1)))),
    (re.compile(r"\[([^\]]+)\] is in your battle area", re.I),
     lambda m: Condition("name_in_battle", name=m.group(1))),
    (re.compile(r"\[([^\]]+)\] is in your support area", re.I),
     lambda m: Condition("name_in_support", name=m.group(1))),
    (re.compile(r"there is a \[([^\]]+)\] in your support area", re.I),
     lambda m: Condition("name_in_support", name=m.group(1))),
    (re.compile(r"there is a \[([^\]]+)\] in your battle area", re.I),
     lambda m: Condition("name_in_battle", name=m.group(1))),
    (re.compile(r"an item card was activated", re.I),
     lambda m: Condition("item_played")),
    (re.compile(r"your (?:arena )?cookie has dealt effect damage", re.I),
     lambda m: Condition("arena_effect_damage")),
    (re.compile(r"any of your cookies gained hp", re.I),
     lambda m: Condition("hp_gained")),
    (re.compile(r"this cookie's hp is less than (\d+)", re.I),
     lambda m: Condition("self_hp", "<=", int(m.group(1)) - 1)),
    (re.compile(r"this cookie's remaining hp is (\d+) or less", re.I),
     lambda m: Condition("self_hp", "<=", int(m.group(1)))),
    (re.compile(r"this cookie's remaining hp is (\d+) or more", re.I),
     lambda m: Condition("self_hp", ">=", int(m.group(1)))),
    (re.compile(r"this cookie's remaining hp is (\d+)", re.I),
     lambda m: Condition("self_hp", "==", int(m.group(1)))),
    (re.compile(r"your break area is LV\.(\d+) or higher", re.I),
     lambda m: Condition("break_level", ">=", int(m.group(1)))),
    (re.compile(r"there (?:are|is) (\d+) cards? or more in your opponent's support area", re.I),
     lambda m: Condition("support_count", ">=", int(m.group(1)), who=SCOPE_OPPONENT)),
    (re.compile(r"there (?:are|is) (\d+) cards? or less in your opponent's support area", re.I),
     lambda m: Condition("support_count", "<=", int(m.group(1)), who=SCOPE_OPPONENT)),
    (re.compile(r"there are less cards in your support area than your opponent", re.I),
     lambda m: Condition("support_diff", ">=", 1)),
    (re.compile(r"your support area has (\d+) or more cards less than your opponent", re.I),
     lambda m: Condition("support_diff", ">=", int(m.group(1)))),
    (re.compile(r"the number of cards in your support area is the same or less "
                r"than your opponent", re.I),
     lambda m: Condition("support_diff", ">=", 0)),
    (re.compile(r"there is another cookie in your battle area", re.I),
     lambda m: Condition("cookie_count", ">=", 2)),
    (re.compile(r"you refreshed during this game", re.I),
     lambda m: Condition("refreshed")),
    (re.compile(r"your hand contains (\d+) cards? or less", re.I),
     lambda m: Condition("hand_size", "<=", int(m.group(1)))),
    (re.compile(r"your hand contains (\d+) cards? or more", re.I),
     lambda m: Condition("hand_size", ">=", int(m.group(1)))),
    (re.compile(r"your support area contains (\d+) cards? or more", re.I),
     lambda m: Condition("support_count", ">=", int(m.group(1)))),
    (re.compile(r"your support area contains (\d+) cards? or less", re.I),
     lambda m: Condition("support_count", "<=", int(m.group(1)))),
    (re.compile(r"there (?:are|is) (\d+) cards? or more in your opponent's trash", re.I),
     lambda m: Condition("trash_count", ">=", int(m.group(1)), who=SCOPE_OPPONENT)),
    (re.compile(r"one of your opponent's cookies is LV\.(\d+)", re.I),
     lambda m: Condition("opponent_has_level", "==", int(m.group(1)))),
    (re.compile(r"there (?:are|is) (\d+) or more \[([^\]]+)\] in your battle area", re.I),
     lambda m: Condition("name_in_battle", name=m.group(2))),
    # "During this turn, if ..." event guards. These read the per-turn counters
    # the engine keeps for both players.
    (re.compile(r"(\d+) or more of your cookies fainted", re.I),
     lambda m: Condition("cookies_fainted", ">=", int(m.group(1)))),
    (re.compile(r"your opponent's cookie fainted", re.I),
     lambda m: Condition("cookies_fainted", ">=", 1, who=SCOPE_OPPONENT)),
    (re.compile(r"your cookie fainted", re.I),
     lambda m: Condition("cookies_fainted", ">=", 1)),
    (re.compile(r"an? (?:【)?(Arena|Ancient|Beast|Dragon)(?:】)? cookie has been "
                r"placed in your break area", re.I),
     lambda m: Condition("arena_break_additions", ">=", 1)),
    (re.compile(r"an? cookie has been placed in your break area", re.I),
     lambda m: Condition("break_additions", ">=", 1)),
    (re.compile(r"there (?:are|is) (\d+) (?:【)?(Arena|Ancient|Beast|Dragon)(?:】)?"
                r" cookies? or more in your break area", re.I),
     lambda m: Condition("zone_count", ">=", int(m.group(1)), zone="break",
                         card_filter=CardFilter(
                             keyword=Keyword[m.group(2).upper()]))),
    (re.compile(r"(\d+) or more cards? in your support area were placed in "
                r"your trash", re.I),
     lambda m: Condition("support_trashed", ">=", int(m.group(1)))),
    (re.compile(r"(\d+) cards? or more have been placed from your support area "
                r"into your trash", re.I),
     lambda m: Condition("support_trashed", ">=", int(m.group(1)))),
    (re.compile(r"an? cookie from your battle area was placed on the top or "
                r"bottom of your deck", re.I),
     lambda m: Condition("cookies_to_deck", ">=", 1)),
    (re.compile(r"an? cookie was placed from your battle area on the bottom of "
                r"your deck", re.I),
     lambda m: Condition("cookies_to_deck_bottom", ">=", 1)),
    (re.compile(r"an? cookie was played from your trash", re.I),
     lambda m: Condition("played_from_trash", ">=", 1)),
    (re.compile(r"this cookie'?s hp was reduced", re.I),
     lambda m: Condition("self_hp_reduced")),
    (re.compile(r"your break area is LV\.(\d+) or (?:higher|above)", re.I),
     lambda m: Condition("break_level", ">=", int(m.group(1)))),
    (re.compile(r"your break area is LV\.(\d+) or lower", re.I),
     lambda m: Condition("break_level", "<=", int(m.group(1)))),
    (re.compile(r"your opponent's break area is LV\.(\d+) or (?:higher|above)", re.I),
     lambda m: Condition("break_level", ">=", int(m.group(1)), who=SCOPE_OPPONENT)),
    (re.compile(r"your opponent's break area is LV\.(\d+) or lower", re.I),
     lambda m: Condition("break_level", "<=", int(m.group(1)), who=SCOPE_OPPONENT)),
    (re.compile(r"there (?:are|is) (\d+) cookies? or more in your break area", re.I),
     lambda m: Condition("break_count", ">=", int(m.group(1)))),
    (re.compile(r"there is a \{([A-Za-z])\} cookie in your battle area", re.I),
     lambda m: Condition("color_in_battle", color=_color(m.group(1)))),
    (re.compile(r"your (?P<zone>support area|hand|trash|break area) contains "
                r"(\d+) or more cards", re.I),
     lambda m: Condition("zone_count", ">=", int(m.group(2)),
                         zone=_ZONE_WORDS[m.group("zone").lower()])),
    (re.compile(r"this cookie has (\d+) or more hp remaining", re.I),
     lambda m: Condition("self_hp", ">=", int(m.group(1)))),
    (re.compile(r"this cookie has (\d+) hp remaining", re.I),
     lambda m: Condition("self_hp", "==", int(m.group(1)))),
    (re.compile(r"your opponent has (\d+) cards? or more in their trash", re.I),
     lambda m: Condition("trash_count", ">=", int(m.group(1)), who=SCOPE_OPPONENT)),
    (re.compile(r"your opponent's cookie faints from this cookie's attack", re.I),
     lambda m: Condition("attack_killed")),
    (re.compile(r"the attacked cookie's remaining hp is (\d+) or more", re.I),
     lambda m: Condition("target_hp", ">=", int(m.group(1)))),
    (re.compile(r"the attacked cookie's remaining hp is (\d+) or less", re.I),
     lambda m: Condition("target_hp", "<=", int(m.group(1)))),
    (re.compile(r"the attacked cookie is LV\.(\d+)", re.I),
     lambda m: Condition("target_level", "==", int(m.group(1)))),
    (re.compile(r"your break area LV\.? is higher than your opponent's break area",
                re.I),
     lambda m: Condition("break_level_higher")),
    (re.compile(r"your break area LV\.? is lower than your opponent's break area",
                re.I),
     lambda m: Condition("break_level_lower")),
    (re.compile(r"your opponent's (?P<zone>support area|trash|hand) contains "
                r"(\d+) cards? or more", re.I),
     lambda m: Condition("zone_count", ">=", int(m.group(2)), who=SCOPE_OPPONENT,
                         zone=_ZONE_WORDS[m.group("zone").lower()])),
    (re.compile(r"your trash contains (\d+) cards? or more that have flip", re.I),
     lambda m: Condition("zone_count", ">=", int(m.group(1)), zone="trash",
                         card_filter=CardFilter(is_flip=True))),
    (re.compile(r"all cards in your opponent's support area are rested", re.I),
     lambda m: Condition("opponent_support_all_rested")),
    (re.compile(r"there is (\d+) less cards? in your support area than your "
                r"opponent's support area", re.I),
     lambda m: Condition("support_diff", ">=", int(m.group(1)))),
    (re.compile(r"your break area'?s? LV\.? is higher than your opponent's break area",
                re.I),
     lambda m: Condition("break_level_higher")),
    (re.compile(r"hp has been added to your cookie", re.I),
     lambda m: Condition("hp_gained")),
    (re.compile(r"there (?:are|is) (\d+) rested cards? or more in your support area",
                re.I),
     lambda m: Condition("rested_support_count", ">=", int(m.group(1)))),
    (re.compile(r"(?:that|the revealed) card is an? (.*?)$", re.I),
     lambda m: Condition("revealed_is", card_filter=parse_card_filter(m.group(1)))),
    (re.compile(r"your support area contains (\d+) or more active cards", re.I),
     lambda m: Condition("active_support_count", ">=", int(m.group(1)))),
    # "2 active cards or more in your support area". Without this the generic
    # zone rule below matches it, reads "active" as a card filter it cannot
    # express, and counts the whole support area — Hero Cookie BS5-063 drew
    # every turn regardless of how much of its support was rested.
    (re.compile(r"there (?:are|is) (\d+) active cards? or more in your support area",
                re.I),
     lambda m: Condition("active_support_count", ">=", int(m.group(1)))),
    (re.compile(r"there (?:are|is) (\d+) active cards? or less in your support area",
                re.I),
     lambda m: Condition("active_support_count", "<=", int(m.group(1)))),
    (re.compile(r"(\d+) or more of your \{([A-Za-z])\} LV\.(\d+) cookies fainted "
                r"during your opponent's previous turn", re.I),
     lambda m: Condition("faints_prev_turn", ">=", int(m.group(1)),
                         color=_color(m.group(2)), value2=int(m.group(3)))),
    (re.compile(r"your \{([A-Za-z])\} LV\.(\d+) cookie fainted during your "
                r"opponent's previous turn", re.I),
     lambda m: Condition("faints_prev_turn", ">=", 1,
                         color=_color(m.group(1)), value2=int(m.group(2)))),
    (re.compile(r"(\d+) or more of your \{([A-Za-z])\} LV\.(\d+) cookies fainted",
                re.I),
     lambda m: Condition("faints_this_turn", ">=", int(m.group(1)),
                         color=_color(m.group(2)), value2=int(m.group(3)))),
    (re.compile(r"(\d+) cards? or more were placed from your support area into "
                r"your trash", re.I),
     lambda m: Condition("support_trashed", ">=", int(m.group(1)))),
    (re.compile(r"your cookie has an opponent's card as hp", re.I),
     lambda m: Condition("foreign_hp")),
    (re.compile(r"there (?:are|is) (\d+) cards? or more in both players'? trash",
                re.I),
     lambda m: Condition("both_trash_count", ">=", int(m.group(1)))),
    (re.compile(r"one of your (.*?)cookies has (\d+) hp remaining", re.I),
     lambda m: Condition("any_own_hp_equals", "==", int(m.group(2)))),
    (re.compile(r"you refreshed (\d+) or more times during this game", re.I),
     lambda m: Condition("refresh_count", ">=", int(m.group(1)))),
    (re.compile(r"the total LV\.? sum of the cookies in your battle area is "
                r"(\d+) or higher", re.I),
     lambda m: Condition("own_level_sum", ">=", int(m.group(1)))),
    (re.compile(r"the remaining hp of one of your cookies is (\d+)", re.I),
     lambda m: Condition("any_own_hp_equals", "==", int(m.group(1)))),
    (re.compile(r"there (?:are|is) (\d+) cards? or more in your trash", re.I),
     lambda m: Condition("trash_count", ">=", int(m.group(1)))),
]


_ZONE_WORDS = {
    "battle area": "battle", "break area": "break", "support area": "support",
    "trash": "trash", "hand": "hand", "deck": "deck",
}

# "there is a {Y} LV.3 Cookie in your break area"
# "there is a {R} Cookie in your battle area", and its negation. `no` has to be
# matched here rather than left in `what`: `parse_card_filter` has no way to
# express it, so it read the word as noise and the card came out meaning the
# exact opposite of what it prints.
_ZONE_HAS = re.compile(
    r"there (?:is|are) (?P<none>no )?(?:an?|another|\d+)?\s*(?P<what>.*?)\s*"
    r"(?:cards?|cookies?)?\s+"
    r"in (?P<who>your opponent's|your|their|both players'?)\s*"
    r"(?P<zone>battle area|break area|support area|trash|hand|deck)$", re.I)
# "there are 3 {R} cards or more in your support area"
_ZONE_COUNT = re.compile(
    r"there (?:is|are) (?P<n>\d+) (?P<what>.*?)\s*(?:cards?|cookies?)?\s*"
    r"or (?P<dir>more|less|fewer) in (?P<who>your opponent's|your|their)\s*"
    r"(?P<zone>battle area|break area|support area|trash|hand|deck)$", re.I)
# "your trash contains 5 {P} cards or more"
_ZONE_CONTAINS = re.compile(
    r"your (?P<zone>battle area|break area|support area|trash|hand|deck) contains "
    r"(?P<n>\d+) (?P<what>.*?)\s*(?:cards?|cookies?)?\s*or (?P<dir>more|less|fewer)$",
    re.I)


def _zone_condition(phrase: str) -> Condition | None:
    """One rule shape for "<filter> in <zone>" and its counted variants."""
    match = _ZONE_COUNT.search(phrase) or _ZONE_CONTAINS.search(phrase)
    if match:
        groups = match.groupdict()
        who = SCOPE_OPPONENT if "opponent" in (groups.get("who") or "") else SCOPE_OWN
        op = ">=" if groups["dir"].lower() == "more" else "<="
        return Condition("zone_count", op, int(groups["n"]), who=who,
                         card_filter=parse_card_filter(groups["what"]),
                         zone=_ZONE_WORDS[groups["zone"].lower()])

    match = _ZONE_HAS.search(phrase)
    if match:
        groups = match.groupdict()
        if "both" in (groups["who"] or "").lower():
            return None
        who = SCOPE_OPPONENT if "opponent" in groups["who"] else SCOPE_OWN
        op, value = ("==", 0) if groups.get("none") else (">=", 1)
        return Condition("zone_has", op, value, who=who,
                         card_filter=parse_card_filter(groups["what"]),
                         zone=_ZONE_WORDS[groups["zone"].lower()])
    return None


def parse_condition(phrase: str) -> Condition:
    for pattern, build in _CONDITION_RULES:
        match = pattern.search(phrase)
        if match:
            return build(match)
    generic = _zone_condition(phrase.strip())
    if generic is not None:
        return generic
    raise CompileError(f"condition: {phrase!r}")


# ---------------------------------------------------------------------------
# costs
# ---------------------------------------------------------------------------

_DISCARD_COST = re.compile(
    r"discard (\d+|a|an|one|two|three) (?:\{([A-Za-z])\} )?cards?", re.I)
_REST_SELF_COST = re.compile(r"rest this card", re.I)
_TRASH_SELF_COST = re.compile(r"place this (?:cookie|card) in(?:to)? (?:the|your) trash", re.I)
_REST_SUPPORT_COST = re.compile(r"rest up to (\d+) cards? in your support area", re.I)
_RETURN_COOKIE_COST = re.compile(
    r"return (?:up to )?(\d+|a|an|one) (.*?)from your battle area to your hand", re.I)


def parse_cost(token: str) -> list[Op]:
    """Translate one ``<...>`` token into ops that abort if unpayable."""
    token = token.strip()
    if _ENERGY_ONLY.match(token):
        return [PayCost(Cost.parse(token))]
    # A few rows print `<R>` instead of `<{R}>`.
    if re.fullmatch(r"[RBGYPKN]+", token):
        return [PayCost(Cost.parse("".join("{%s}" % ch for ch in token)))]

    discard = _DISCARD_COST.search(token)
    if discard:
        color = _color(discard.group(2)) if discard.group(2) else None
        return [Discard(_number(discard.group(1)), color=color, optional=True)]

    rest_support = _REST_SUPPORT_COST.search(token)
    if rest_support:
        return [RestSupport(int(rest_support.group(1)))]

    if _REST_SELF_COST.search(token):
        return [RestSelf()]
    if _TRASH_SELF_COST.search(token):
        return [TrashSelf()]

    bounce = _RETURN_COOKIE_COST.search(token)
    if bounce:
        return [
            Select(SCOPE_OWN, count=_number(bounce.group(1)),
                   filter=parse_filter(bounce.group(2), exclude_self=True),
                   optional=True, ref="cost_target"),
            RequireSelected("cost_target"),
            ReturnToHand(ref="cost_target"),
        ]

    lowered = token.lower()

    # "<can be used as {B}.>" is the rider's cost: one energy, of the colour it
    # names. It was read as free, which handed 72 cards a rider that fired on
    # every attack for nothing — Pitaya Dragon Cookie (ST6-004) pinged an extra
    # damage after every swing it made. The colour substitution the phrase
    # describes is not modelled, so this is if anything a shade stricter than
    # the card; being stricter about a cost is the safe direction.
    substitute = re.search(r"can be used as \{([A-Za-z])\}", lowered, re.I)
    if substitute:
        return [PayCost(Cost.parse("{%s}" % substitute.group(1).upper()))]
    if "can be used as" in lowered:
        raise CompileError(f"cost: {token!r}")

    match = re.search(r"place (\d+) (.*?)cards? from your support area in(?:to)? "
                      r"(?:the|your) trash", lowered)
    if match:
        return [TrashOwnSupport(int(match.group(1)))]

    match = re.search(r"place (\d+) cards? from the top of (?:1 of )?your cookies'? hp "
                      r"into the trash", lowered)
    if match:
        return [Select(SCOPE_OWN, count=1, optional=False, ref="hp_victim"),
                RequireSelected("hp_victim"),
                TrashHP(int(match.group(1)), ref="hp_victim")]

    match = re.search(r"return (\d+) \{([a-z])\} cards? from your support area to your hand",
                      lowered)
    if match:
        return [ReturnSupportToHand()]

    if re.search(r"place this cookie from the break area into the trash", lowered):
        return [SelfBreakToTrash()]

    if re.search(r"place this cookie in your break area", lowered):
        return [SelfToBreak()]

    match = re.search(r"place (\d+) cards? from the top of this cookie's hp "
                      r"into the trash", lowered)
    if match:
        return [TrashHP(int(match.group(1)), ref=REF_SELF)]

    match = re.search(r"place (\d+) cookies? from your hand into your break area", lowered)
    if match:
        return [BreakCookieFromHand(int(match.group(1)))]

    if re.search(r"return \d+ cards? from your support area to your hand", lowered):
        return [ReturnSupportToHand()]

    match = re.search(r"discard (\d+) cookies? that has flip from your hand", lowered)
    if match:
        return [DiscardFlip(int(match.group(1)))]

    if re.search(r"place this cookie on the bottom of (?:your|the) deck", lowered):
        return [SelfToDeckBottom()]

    match = re.search(r"(?:place|select) (\d+) (.*?)cookie.*? from your battle area "
                      r"(?:and return them )?(?:on|to) the bottom of (?:your|the) deck",
                      lowered)
    if match:
        return [Select(SCOPE_OWN, count=int(match.group(1)), optional=False,
                       filter=parse_filter(match.group(2)), ref="deck_cost"),
                RequireSelected("deck_cost"),
                MoveSelectedToDeck(ref="deck_cost")]

    match = re.search(r"place (\d+) (.*?)cookie.*? from your battle area in(?:to)? "
                      r"your break area", lowered)
    if match:
        return [Select(SCOPE_OWN, count=int(match.group(1)), optional=False,
                       filter=parse_filter(match.group(2)), ref="break_cost"),
                RequireSelected("break_cost"), Faint(ref="break_cost")]

    if re.search(r"place this cookie on the top of (?:your|the) deck", lowered):
        return [SelfToDeck(ZONE_DECK_TOP)]

    if re.search(r"make this cookie faint", lowered):
        return [SelfFaint()]

    match = re.search(r"make (\d+) of your cookies faint", lowered)
    if match:
        return [Select(SCOPE_OWN, count=int(match.group(1)), optional=False,
                       ref="cost_faint"),
                RequireSelected("cost_faint"), Faint(ref="cost_faint")]

    if re.search(r"discard your entire hand", lowered):
        return [DiscardHand()]

    if re.search(r"select \d+ cookie from each player", lowered):
        return [Select(SCOPE_OWN, count=1, optional=False, ref="mine_each"),
                Select(SCOPE_OPPONENT, count=1, optional=False, ref="theirs_each"),
                RequireSelected("mine_each"), RequireSelected("theirs_each")]

    match = re.search(r"reveal (\d+) (.*?)(?:cards?|cookies?) from your hand", lowered)
    if match:
        return [RevealFromHand(int(match.group(1)),
                               parse_card_filter(match.group(0)))]

    match = re.search(r"place (\d+) cards? from the top of your deck into "
                      r"(?:the|your) trash", lowered)
    if match:
        return [MillDeck(int(match.group(1)))]

    # "<discard 1 {G} item card from your hand.>" — one filtered-discard rule
    # covers colour, type and FLIP variants alike.
    match = re.search(r"discard (?:a |an |(\d+) )(.*?)(?:cards?|cookies?)"
                      r"(?: from your hand)?", lowered)
    if match:
        return [FilteredDiscard(_number(match.group(1)),
                                parse_card_filter(match.group(0)))]

    # A Cookie's own HP as the price. Three shapes, and the drain must be tried
    # before the flat one — its text starts with the flat one's, so the flat
    # pattern would match the prefix and quietly charge a single card for a
    # cost that is meant to take the Cookie down to its last.
    match = re.search(r"place (?:\d+ )?(?:of )?(?:your|this|that) cookies?'?s?'? hp "
                      r"cards? in(?:to)? (?:the|your) trash until "
                      r"(?:the|that|this) cookie'?s?'? hp reaches (\d+)", lowered)
    if match:
        return [Select(SCOPE_OWN, count=1, optional=False, ref="hp_cost"),
                RequireSelected("hp_cost"),
                TrashHPUntil(int(match.group(1)), ref="hp_cost")]

    match = re.search(r"place (\d+) of (?:your|this|that) cookies?'?s?'? hp cards? "
                      r"in(?:to)? (?:the|your) trash", lowered)
    if match:
        return [Select(SCOPE_OWN, count=1, optional=False, ref="hp_cost"),
                RequireSelected("hp_cost"),
                TrashHP(int(match.group(1)), ref="hp_cost")]

    match = re.search(r"return (\d+) cards? from the top of (?:one of )?"
                      r"(?:your|this|that) cookie'?s?'? hp to your hand", lowered)
    if match:
        return [Select(SCOPE_OWN, count=1, optional=False, ref="hp_cost"),
                RequireSelected("hp_cost"),
                HPToHand(int(match.group(1)), ref="hp_cost")]

    match = re.search(r"place (\d+) cards? from the top of (?:one of )?(?:your|this|that) "
                      r"(.*?)cookie'?s?'? hp(?: card| in your battle area)? "
                      r"into (?:the|your) trash", lowered)
    if match:
        return [Select(SCOPE_OWN, count=1, optional=False,
                       filter=parse_filter(match.group(2)), ref="hp_cost"),
                RequireSelected("hp_cost"),
                TrashHP(int(match.group(1)), ref="hp_cost")]

    match = re.search(r"return (\d+) (.*?)from your support area to your hand", lowered)
    if match:
        return [ReturnSupportToHand()]

    match = re.search(r"place (\d+) (.*?)cookie.*? from your hand into (?:the|your) "
                      r"break area", lowered)
    if match:
        return [BreakCookieFromHand(int(match.group(1)))]

    match = re.search(r"(?:return|select) (?:up to )?(\d+) (.*?)from your trash"
                      r".*?to your deck.*?and shuffle", lowered)
    if match:
        return [TrashToDeck(int(match.group(1)), parse_card_filter(match.group(2)))]

    match = re.search(r"place (\d+) (.*?)cookie.*? from your battle area into "
                      r"(?:the|your) trash", lowered)
    if match:
        return [Select(SCOPE_OWN, count=int(match.group(1)),
                       filter=parse_filter(match.group(2)), optional=False,
                       ref="cost_trash"),
                RequireSelected("cost_trash"),
                TrashCookies(ref="cost_trash")]

    match = re.search(r"place (\d+) (.*?)cookie.*? from your trash into "
                      r"(?:the|your) break area", lowered)
    if match:
        return [MoveCards(ZONE_TRASH, ZONE_BREAK, int(match.group(1)),
                          parse_card_filter(match.group(2)), optional=False)]

    raise CompileError(f"cost: {token!r}")


@dataclass
class RestSelf(Op):
    def run(self, ctx, env) -> bool:
        cookie = ctx.source_cookie
        if cookie is not None:
            if cookie.rested:
                return False
            cookie.rested = True
            return True
        card = ctx.source_card
        if card is None or card.rested:
            return False
        card.rested = True
        return True


@dataclass
class TrashSelf(Op):
    def run(self, ctx, env) -> bool:
        cookie = ctx.source_cookie
        if cookie is not None:
            # "Place this Cookie in the trash" is not fainting: it never
            # reaches the break area, so the opponent banks no Level for it
            # (Crunchy Chip Cookie BS8-119 pays itself as a cost). A movement
            # lock stops it, and this is a cost, so say so rather than letting
            # it be paid for free.
            if not ctx._may_move(cookie):
                return False
            ctx.trash_cookie(cookie)
            return cookie not in ctx.me.battle
        card = ctx.source_card
        if card is not None and card in ctx.me.stage:
            ctx.me.stage.remove(card)
            ctx.me.trash.append(card)
        return True


@dataclass
class RequireSelected(Op):
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        return bool(env.get(self.ref))


@dataclass
class SetSelfActive(Op):
    def run(self, ctx, env) -> bool:
        if ctx.source_cookie is not None:
            ctx.source_cookie.rested = False
        return True


@dataclass
class TrashOwnSupport(Op):
    amount: int = 1

    def run(self, ctx, env) -> bool:
        if len(ctx.me.support) < self.amount:
            return False
        for _ in range(self.amount):
            card = ctx.choose("Trash a support card", list(ctx.me.support),
                              optional=False) or ctx.me.support[0]
            ctx.me.support.remove(card)
            ctx.me.trash.append(card)
            ctx.me.support_trashed_this_turn += 1
        return True


@dataclass
class BreakCookieFromHand(Op):
    """A self-inflicted cost: it advances the opponent's win condition."""

    amount: int = 1

    def run(self, ctx, env) -> bool:
        pool = [c for c in ctx.me.hand if ctx.db[c.card_id].is_cookie]
        if len(pool) < self.amount:
            return False
        for _ in range(self.amount):
            card = ctx.choose("Send a Cookie from hand to your break area",
                              pool, optional=False) or pool[0]
            pool.remove(card)
            ctx.me.hand.remove(card)
            ctx.me.break_area.append(card)
        ctx.game._check_win()
        return not ctx.state.over


@dataclass
class SelfToDeckBottom(Op):
    def run(self, ctx, env) -> bool:
        cookie = ctx.source_cookie
        if cookie is None or cookie not in ctx.me.battle:
            return False
        # BS9-088 【Awaken】s off the back of exactly this happening, and
        # BS9-083 off either end of the deck; `cookie_to_deck` is where both
        # are counted.
        ctx.game.cookie_to_deck(cookie, bottom=True)
        return True


def _zone_cards(ctx, zone: str, opponent: bool):
    player = ctx.opp if opponent else ctx.me
    return {"break": player.break_area, "trash": player.trash,
            "support": player.support, "hand": player.hand}.get(zone, [])


def _zone_count(ctx, filt, zone: str, opponent: bool) -> int:
    if zone == "battle_both":
        # "in either battle area" — the whole table, not one side of it.
        return sum(1 for player in ctx.state.players for c in player.battle
                   if filt.matches(c.defn(ctx.db)))
    if zone == "fainted":
        # "for each of your opponent's Cookies that fainted during this turn".
        # Not a zone at all: the engine keeps a running count rather than the
        # cards, so a filter cannot be applied here and none is printed on the
        # cards that ask.
        player = ctx.opp if opponent else ctx.me
        return player.cookies_fainted_this_turn
    if zone == "battle":
        player = ctx.opp if opponent else ctx.me
        return sum(1 for c in player.battle if filt.matches(c.defn(ctx.db)))
    return sum(1 for c in _zone_cards(ctx, zone, opponent)
               if filt.matches(ctx.db[c.card_id]))


@dataclass
class EffectDamageReduction(Op):
    amount: int = 1
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        for cookie in _resolve(self.ref, ctx, env):
            cookie.effect_damage_reduction += self.amount
        return True


@dataclass
class LockOpponentHPGain(Op):
    def run(self, ctx, env) -> bool:
        ctx.opp.hp_gain_locked = True
        return True


@dataclass
class OpponentSelects(Op):
    """The opponent chooses which of their own Cookies an effect will hit."""

    count: int = 1
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        chosen = []
        pool = list(ctx.opp.battle)
        for _ in range(self.count):
            if not pool:
                break
            pick = ctx.game.controller(ctx.opp.index).choose(
                ctx.state, "Select one of your Cookies", pool, optional=False)
            pick = pick or pool[0]
            pool.remove(pick)
            chosen.append(pick)
        env[self.ref] = chosen
        return True


@dataclass
class AttackSurcharge(Op):
    amount: int = 1
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        for cookie in _resolve(self.ref, ctx, env):
            cookie.attack_cost_surcharge += self.amount
        return True


@dataclass
class SetLevel(Op):
    level: int = 1
    ref: str = REF_SELF

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        for cookie in _resolve(self.ref, ctx, env):
            cookie.level_override = self.level
        return True


@dataclass
class HPToDeckBottom(Op):
    amount: int = 1
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        for cookie in _resolve(self.ref, ctx, env):
            owner = ctx.state.players[cookie.owner]
            for _ in range(self.amount):
                if not cookie.hp_cards:
                    break
                owner.deck.append(cookie.hp_cards.pop())
            if not cookie.hp_cards:
                ctx.game.faint(cookie)
        return True


@dataclass
class RecycleOpponentTrash(Op):
    def run(self, ctx, env) -> bool:
        ctx.opp.deck.extend(ctx.opp.trash)
        ctx.opp.trash.clear()
        ctx.state.rng.shuffle(ctx.opp.deck)
        return True


@dataclass
class DamageAllExcept(Op):
    """"All Cookies that are not [X] receive N damage"."""

    name: str = ""
    amount: int = 1

    def run(self, ctx, env) -> bool:
        for cookie in list(ctx.me.battle) + list(ctx.opp.battle):
            if cookie.name(ctx.db) != self.name:
                ctx.deal_damage(cookie, self.amount)
        return True


@dataclass
class PlayFromHand(Op):
    filter: object = None

    def run(self, ctx, env) -> bool:
        if len(ctx.me.battle) >= ctx.game.rules.max_battle_cookies:
            return False
        options = [c for c in ctx.me.hand
                   if ctx.db[c.card_id].is_cookie
                   and (self.filter is None or self.filter.matches(ctx.db[c.card_id]))]
        if not options:
            return False
        card = ctx.choose("Play a Cookie from your hand", options, optional=True)
        if card is None:
            return False
        ctx.me.hand.remove(card)
        ctx.game._deploy_cookie(ctx.me, card)
        return True


@dataclass
class HandToHP(Op):
    """Bury cards from hand under a Cookie as extra HP."""

    amount: int = 1
    ref: str = REF_SELF

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        targets = _resolve(self.ref, ctx, env)
        if not targets or not ctx.me.hand:
            return True
        cookie = targets[0]
        for _ in range(self.amount):
            if not ctx.me.hand:
                break
            card = ctx.choose("Add a card from hand to this Cookie's HP",
                              list(ctx.me.hand), optional=True)
            if card is None:
                break
            ctx.me.hand.remove(card)
            card.face_up = False
            cookie.hp_cards.append(card)
        return True


@dataclass
class SupportToHP(Op):
    amount: int = 1
    filter: object = None
    ref: str = REF_SELF

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        targets = _resolve(self.ref, ctx, env)
        if not targets:
            return True
        cookie = targets[0]
        for _ in range(self.amount):
            pool = [c for c in ctx.me.support
                    if self.filter is None or self.filter.matches(ctx.db[c.card_id])]
            if not pool:
                break
            card = ctx.choose("Move a support card onto this Cookie's HP", pool,
                              optional=True)
            if card is None:
                break
            ctx.me.support.remove(card)
            card.face_up = False
            cookie.hp_cards.append(card)
        return True


@dataclass
class TransferHP(Op):
    """Move HP cards between two of your Cookies."""

    amount: int = 1
    from_self: bool = False
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        others = _resolve(self.ref, ctx, env)
        me = ctx.source_cookie
        if me is None or not others:
            return True
        source, destination = (me, others[0]) if self.from_self else (others[0], me)
        for _ in range(self.amount):
            if not source.hp_cards:
                break
            destination.hp_cards.append(source.hp_cards.pop())
        if not source.hp_cards:
            ctx.game.faint(source)
        return True


@dataclass
class TrashSelectedSupport(Op):
    """Trash the opponent support card a preceding selector rested."""

    def run(self, ctx, env) -> bool:
        rested = [c for c in ctx.opp.support if c.rested]
        if not rested:
            return True
        ctx.opp.support.remove(rested[-1])
        ctx.opp.trash.append(rested[-1])
        return True


@dataclass
class OpponentDraws(Op):
    amount: int = 1

    def run(self, ctx, env) -> bool:
        ctx.game.draw(ctx.opp, self.amount)
        return True


@dataclass
class OpponentHandToDeck(Op):
    amount: int = 1

    def run(self, ctx, env) -> bool:
        for _ in range(self.amount):
            if not ctx.opp.hand:
                break
            card = ctx.game.controller(ctx.opp.index).choose(
                ctx.state, "Put a card on the bottom of your deck",
                list(ctx.opp.hand), optional=False) or ctx.opp.hand[0]
            ctx.opp.hand.remove(card)
            ctx.opp.deck.append(card)
        return True


@dataclass
class SelectedTrashToDeckBottom(Op):
    ref: str = "trash_sel"

    def run(self, ctx, env) -> bool:
        for card in env.get(self.ref) or []:
            if card in ctx.me.trash:
                ctx.me.trash.remove(card)
                ctx.me.deck.append(card)
        return True


@dataclass
class SelfCardToDeck(Op):
    """"place this card at the bottom of your deck" — the card resolving.

    A stage card recycling itself, so it is looked for wherever it currently
    is rather than assumed into one zone: the stage area while it is in play,
    the trash for an item already filed there.
    """

    bottom: bool = True

    def run(self, ctx, env) -> bool:
        card = ctx.source_card
        if card is None:
            return False
        for zone in (ctx.me.stage, ctx.me.trash, ctx.me.support, ctx.me.hand):
            if card in zone:
                zone.remove(card)
                break
        if self.bottom:
            ctx.me.deck.append(card)
        else:
            ctx.me.deck.insert(0, card)
        return True


@dataclass
class SelfCardToBreak(Op):
    def run(self, ctx, env) -> bool:
        card = ctx.source_card
        if card is None:
            return False
        if card in ctx.me.trash:
            ctx.me.trash.remove(card)
        ctx.me.break_area.append(card)
        ctx.game._check_win()
        return not ctx.state.over


@dataclass
class DisableFlips(Op):
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        for cookie in _resolve(self.ref, ctx, env):
            cookie.flip_disabled = True
        return True


@dataclass
class PlaySelectedFromBreak(Op):
    """"Play that Cookie" after a break-area selection bound it."""

    ref: str = "break_sel"

    def run(self, ctx, env) -> bool:
        for card in env.get(self.ref) or []:
            if card in ctx.me.break_area:
                ctx.me.break_area.remove(card)
                ctx.game._deploy_cookie(ctx.me, card)
        return True


@dataclass
class SetSelectedActive(Op):
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        for cookie in _resolve(self.ref, ctx, env):
            cookie.rested = False
        return True


@dataclass
class BankEndTurnUntap(Op):
    """"when your turn ends, set up to N cards as active" — a delayed untap."""

    amount: int = 1

    def run(self, ctx, env) -> bool:
        source = ctx.source_card
        if source is None and ctx.source_cookie is not None:
            source = ctx.source_cookie.card
        ctx.me.end_turn_untaps.append(
            (source.card_id if source is not None else "", self.amount))
        return True


@dataclass
class DisableBlockers(Op):
    def run(self, ctx, env) -> bool:
        ctx.opp.blockers_disabled = True
        return True


@dataclass
class RandomOpponentDiscard(Op):
    amount: int = 1

    def run(self, ctx, env) -> bool:
        for _ in range(self.amount):
            if not ctx.opp.hand:
                break
            card = ctx.state.rng.choice(ctx.opp.hand)
            ctx.opp.hand.remove(card)
            ctx.opp.trash.append(card)
        return True

    def is_live(self, ctx, env) -> bool:
        return bool(ctx.opp.hand)


@dataclass
class DrawToHandSize(Op):
    size: int = 4

    def run(self, ctx, env) -> bool:
        while len(ctx.me.hand) < self.size:
            if not ctx.draw(1):
                break
        return True


@dataclass
class Scaled(Op):
    """Base for "N per X" effects: how much, worked out from a count.

    `divisor` is the "for every 2 ..." case — the count is floor-divided before
    it is multiplied, so a pair pays once and an odd one over pays nothing.
    """

    per: int = 1
    filter: object = None
    zone: str = "break"
    opponent: bool = False
    divisor: int = 1

    def amount(self, ctx) -> int:
        count = _zone_count(ctx, self.filter, self.zone, self.opponent)
        return self.per * (count // max(1, self.divisor))


@dataclass
class ScaledDamage(Scaled):
    """"receives 1 damage for each LV.3 Cookie in your break area"."""

    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        amount = self.amount(ctx)
        if amount:
            for cookie in _resolve(self.ref, ctx, env):
                ctx.deal_damage(cookie, amount)
        return True


@dataclass
class ScaledGainHP(Scaled):
    """"gains +1 HP for each ..." — on this Cookie, or on a selected one.

    `ref` is which: the cards that say "this Cookie" leave it at `REF_SELF`,
    the ones that say "that Cookie" have already selected their target.
    """

    ref: str = REF_SELF

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        amount = self.amount(ctx)
        if not amount:
            return True
        for cookie in _resolve(self.ref, ctx, env):
            ctx.gain_hp(cookie, amount)
        return True


@dataclass
class ScaledModifyAttack(Scaled):
    """"gains +1 attack damage for each ..." and its negative twin.

    `per` carries the sign, so the same op is the buff and the debuff.
    """

    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        amount = self.amount(ctx)
        if not amount:
            return True
        for cookie in _resolve(self.ref, ctx, env):
            ctx.modify_attack(cookie, amount)
        return True


@dataclass
class ScaledDraw(Scaled):
    def run(self, ctx, env) -> bool:
        amount = self.amount(ctx)
        if amount:
            ctx.draw(amount)
        return True


@dataclass
class ViewTop(Op):
    """"View N cards from the top of your deck ..." — `Ctx.view_top` verbatim.

    Everything the primitive already gets right lives there: the whole run is
    shown even when only some of it can be taken, the cards stay in the deck
    while the question is open, and `rest` says where the leftovers go.
    """

    amount: int = 3
    take: int = 1
    rest: str = "bottom"
    filter: object = None
    reveal: bool = False

    def run(self, ctx, env) -> bool:
        pick = None
        if self.filter is not None:
            pick = lambda defn: self.filter.matches(defn)   # noqa: E731
        ctx.view_top(self.amount, take=self.take, pick=pick, rest=self.rest,
                     reveal=self.reveal)
        return True

    def is_live(self, ctx, env) -> bool:
        return bool(ctx.me.deck)


@dataclass
class HPToHand(Op):
    """Return cards from the top of a Cookie's HP pile to hand."""

    amount: int = 1
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        targets = _resolve(self.ref, ctx, env) or (
            [ctx.source_cookie] if ctx.source_cookie else [])
        for cookie in targets:
            owner = ctx.state.players[cookie.owner]
            for _ in range(self.amount):
                if not cookie.hp_cards:
                    break
                owner.hand.append(cookie.hp_cards.pop())
            if not cookie.hp_cards:
                ctx.game.faint(cookie)
        return True


@dataclass
class SelectedCookieToSupport(Op):
    """Move a selected Cookie off the field into the support area."""

    ref: str = REF_IT
    rested: bool = False

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        for cookie in _resolve(self.ref, ctx, env):
            owner = ctx.state.players[cookie.owner]
            if cookie not in owner.battle:
                continue
            owner.battle.remove(cookie)
            cookie.card.rested = self.rested
            owner.support.append(cookie.card)
            owner.trash.extend(cookie.spent_cards)
            ctx.game._check_battle_area(owner)
        return True


@dataclass
class SelfCardToSupport(Op):
    rested: bool = True

    def run(self, ctx, env) -> bool:
        card = ctx.source_card
        if card is None:
            return False
        if card in ctx.me.trash:
            ctx.me.trash.remove(card)
        card.rested = self.rested
        ctx.me.support.append(card)
        return True


@dataclass
class DebuffNextTurn(Op):
    """"during your opponent's next turn" — banked until that turn starts."""

    amount: int = 1
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        for cookie in _resolve(self.ref, ctx, env):
            cookie.attack_bonus_next_turn -= self.amount
        return True


@dataclass
class SelectTrash(Op):
    """Bind cards from the trash for a following clause to act on."""

    count: int = 1
    filter: object = None

    def run(self, ctx, env) -> bool:
        pool = [c for c in ctx.me.trash
                if self.filter is None or self.filter.matches(ctx.db[c.card_id])]
        chosen = []
        for _ in range(self.count):
            if not pool:
                break
            card = ctx.choose("Select a card from your trash", pool, optional=True)
            if card is None:
                break
            pool.remove(card)
            chosen.append(card)
        env["trash_sel"] = chosen
        return True


@dataclass
class SelectedTrashToDeck(Op):
    def run(self, ctx, env) -> bool:
        chosen = env.get("trash_sel") or []
        for card in chosen:
            if card in ctx.me.trash:
                ctx.me.trash.remove(card)
                ctx.me.deck.append(card)
        if chosen:
            ctx.state.rng.shuffle(ctx.me.deck)
        return True


@dataclass
class MoveSelectedToDeck(Op):
    ref: str = REF_IT
    bottom: bool = True

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        for cookie in _resolve(self.ref, ctx, env):
            ctx.game.cookie_to_deck(cookie, bottom=self.bottom)
        return True


@dataclass
class PlayFromBreak(Op):
    filter: object = None

    def run(self, ctx, env) -> bool:
        if len(ctx.me.battle) >= ctx.game.rules.max_battle_cookies:
            return False
        options = [c for c in ctx.me.break_area
                   if ctx.db[c.card_id].is_cookie
                   and (self.filter is None or self.filter.matches(ctx.db[c.card_id]))]
        if not options:
            return False
        card = ctx.choose("Play a Cookie from your break area", options,
                          optional=True)
        if card is None:
            return False
        ctx.me.break_area.remove(card)
        ctx.game._deploy_cookie(ctx.me, card, from_zone="break")
        return True


@dataclass
class RecycleTrash(Op):
    def run(self, ctx, env) -> bool:
        ctx.me.deck.extend(ctx.me.trash)
        ctx.me.trash.clear()
        ctx.state.rng.shuffle(ctx.me.deck)
        return True


@dataclass
class TrashOpponentSupport(Op):
    amount: int = 1

    def run(self, ctx, env) -> bool:
        for _ in range(self.amount):
            if not ctx.opp.support:
                break
            ctx.opp.trash.append(ctx.opp.support.pop())
        return True


@dataclass
class RevealFromHand(Op):
    """A reveal costs nothing but requires you to actually hold the cards."""

    amount: int = 1
    filter: object = None

    def run(self, ctx, env) -> bool:
        pool = [c for c in ctx.me.hand
                if self.filter is None or self.filter.matches(ctx.db[c.card_id])]
        return len(pool) >= self.amount


@dataclass
class SelfFaint(Op):
    def run(self, ctx, env) -> bool:
        if ctx.source_cookie is None or ctx.source_cookie not in ctx.me.battle:
            return False
        ctx.faint(ctx.source_cookie)
        return not ctx.state.over


@dataclass
class DiscardHand(Op):
    def run(self, ctx, env) -> bool:
        ctx.me.trash.extend(ctx.me.hand)
        ctx.me.hand.clear()
        return True


@dataclass
class FilteredDiscard(Op):
    amount: int = 1
    filter: object = None

    def run(self, ctx, env) -> bool:
        pool = [c for c in ctx.me.hand
                if self.filter is None or self.filter.matches(ctx.db[c.card_id])]
        if len(pool) < self.amount:
            return False
        for _ in range(self.amount):
            card = ctx.choose("Discard a card", pool, optional=False) or pool[0]
            pool.remove(card)
            ctx.me.hand.remove(card)
            ctx.me.trash.append(card)
        return True


@dataclass
class TrashToDeck(Op):
    amount: int = 1
    filter: object = None

    def run(self, ctx, env) -> bool:
        pool = [c for c in ctx.me.trash
                if self.filter is None or self.filter.matches(ctx.db[c.card_id])]
        if len(pool) < self.amount:
            return False
        for _ in range(self.amount):
            card = ctx.choose("Shuffle a card back into your deck", pool,
                              optional=False) or pool[0]
            pool.remove(card)
            ctx.me.trash.remove(card)
            ctx.me.deck.append(card)
        ctx.state.rng.shuffle(ctx.me.deck)
        return True


@dataclass
class MillOpponentDeck(Op):
    amount: int = 1

    def run(self, ctx, env) -> bool:
        for _ in range(self.amount):
            if not ctx.opp.deck:
                break
            ctx.opp.trash.append(ctx.opp.deck.pop(0))
        return True


@dataclass
class Reveal(Op):
    """Reveal from the top of the deck. Hidden information is not modelled, so
    the reveal itself is a no-op; only cards that *act* on the reveal matter."""

    amount: int = 1
    from_bottom: bool = False

    def run(self, ctx, env) -> bool:
        cards = (ctx.me.deck[-self.amount:] if self.from_bottom
                 else ctx.me.deck[:self.amount])
        env["revealed"] = cards
        ctx.revealed = list(cards)
        return True


@dataclass
class PlayFromTrash(Op):
    filter: object = None

    def run(self, ctx, env) -> bool:
        filt = self.filter
        return ctx.play_cookie_from_trash(
            (lambda d: filt.matches(d)) if filt is not None else None)


@dataclass
class PlayFromSupport(Op):
    filter: object = None

    def run(self, ctx, env) -> bool:
        if len(ctx.me.battle) >= ctx.game.rules.max_battle_cookies:
            return False
        options = [c for c in ctx.me.support
                   if ctx.db[c.card_id].is_cookie
                   and (self.filter is None or self.filter.matches(ctx.db[c.card_id]))]
        if not options:
            return False
        card = ctx.choose("Play a Cookie from your support area", options,
                          optional=True)
        if card is None:
            return False
        ctx.me.support.remove(card)
        ctx.game._deploy_cookie(ctx.me, card, from_zone="support")
        return True


@dataclass
class TrashCookies(Op):
    """"Place that Cookie into the trash" — removal that skips the break area."""

    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        for cookie in _resolve(self.ref, ctx, env):
            ctx.trash_cookie(cookie)
        return True


@dataclass
class OpponentDiscard(Op):
    amount: int = 1

    def run(self, ctx, env) -> bool:
        ctx.opponent_discards(self.amount)
        return True


@dataclass
class SkipNextActive(Op):
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        from .effect_ir import _resolve
        for cookie in _resolve(self.ref, ctx, env):
            ctx.skip_next_active(cookie)
        return True


@dataclass
class TrashHPOfAllEnemies(Op):
    amount: int = 1

    def run(self, ctx, env) -> bool:
        for cookie in list(ctx.enemy_cookies()):
            ctx.trash_hp(cookie, self.amount)
        return True


@dataclass
class SelfToDeck(Op):
    zone: str = ZONE_DECK_BOTTOM

    def run(self, ctx, env) -> bool:
        cookie = ctx.source_cookie
        if cookie is None or cookie not in ctx.me.battle:
            return False
        ctx.game.cookie_to_deck(cookie, bottom=self.zone != ZONE_DECK_TOP)
        return True


@dataclass
class SelfBreakToTrash(Op):
    """Undo a break: recover the cost by moving a card out of the break area."""

    def run(self, ctx, env) -> bool:
        card = ctx.source_card
        if card is None or card not in ctx.me.break_area:
            return False
        ctx.me.break_area.remove(card)
        ctx.me.trash.append(card)
        return True


@dataclass
class SelfToBreak(Op):
    def run(self, ctx, env) -> bool:
        cookie = ctx.source_cookie
        if cookie is None or cookie not in ctx.me.battle:
            return False
        ctx.faint(cookie)
        return not ctx.state.over


@dataclass
class SupportFromHand(Op):
    amount: int = 1
    rested: bool = True

    def run(self, ctx, env) -> bool:
        for _ in range(self.amount):
            if not ctx.me.hand:
                break
            card = ctx.choose("Place a card into your support area",
                              list(ctx.me.hand), optional=True)
            if card is None:
                break
            ctx.me.hand.remove(card)
            card.rested = self.rested
            ctx.me.support.append(card)
        return True


@dataclass
class DiscardFlip(Op):
    amount: int = 1

    def run(self, ctx, env) -> bool:
        pool = [c for c in ctx.me.hand if ctx.db[c.card_id].is_flip]
        if len(pool) < self.amount:
            return False
        for _ in range(self.amount):
            card = ctx.choose("Discard a FLIP Cookie", pool, optional=False) or pool[0]
            pool.remove(card)
            ctx.me.hand.remove(card)
            ctx.me.trash.append(card)
        return True


# ---------------------------------------------------------------------------
# effect verbs
# ---------------------------------------------------------------------------

def _target_scope(phrase: str) -> tuple[str, bool]:
    """(scope, exclude_self) from "your opponent's Cookies" / "your Cookies"."""
    lowered = phrase.lower()
    if "opponent" in lowered:
        return SCOPE_OPPONENT, False
    if "your other" in lowered:
        return SCOPE_OWN, True
    if "your" in lowered:
        return SCOPE_OWN, False
    return SCOPE_ALL, False


_VERB_RULES: list[tuple[re.Pattern, object]] = []


def verb(pattern: str):
    compiled = re.compile(pattern, re.I)

    def wrap(fn):
        _VERB_RULES.append((compiled, fn))
        return fn

    return wrap


@verb(r"^select up to (\d+) of (.*?cookies?.*?)$")
def _v_select(m) -> list[Op]:
    scope, exclude = _target_scope(m.group(2))
    return [Select(scope, count=int(m.group(1)),
                   filter=parse_filter(m.group(2), exclude_self=exclude))]


@verb(r"^select up to (\d+) (.*?cookies?)$")
def _v_select_bare(m) -> list[Op]:
    """"Select up to 1 LV.2 or lower Cookie" — no "of your/opponent's"."""
    scope, exclude = _target_scope(m.group(2))
    return [Select(scope, count=int(m.group(1)),
                   filter=parse_filter(m.group(2), exclude_self=exclude))]


@verb(r"^select (\d+) of (.*?cookies?.*?)$")
def _v_select_required(m) -> list[Op]:
    """"Select 1 of your Cookies" — mandatory, unlike "select up to 1".

    The distinction is already in the op: an optional select with no legal
    target quietly does nothing, a mandatory one aborts the rest of the clause.
    """
    scope, exclude = _target_scope(m.group(2))
    return [Select(scope, count=int(m.group(1)), optional=False,
                   filter=parse_filter(m.group(2), exclude_self=exclude))]


@verb(r"^(?:that|those) cookies? receives? (\d+) damage(?: each)?\.?$")
def _v_damage(m) -> list[Op]:
    return [Damage(int(m.group(1)))]


@verb(r"^all of your opponent's cookies receive (\d+) damage(?: each)?\.?$")
def _v_damage_all(m) -> list[Op]:
    return [Select(SCOPE_OPPONENT, all_matching=True), Damage(int(m.group(1)))]


@verb(r"^deals? (\d+) damage to all of your opponent's cookies\.?$")
def _v_deals_damage_all(m) -> list[Op]:
    return [Select(SCOPE_OPPONENT, all_matching=True), Damage(int(m.group(1)))]


@verb(r"^all of your cookies receive (\d+) damage(?: each)?\.?$")
def _v_damage_all_mine(m) -> list[Op]:
    return [Select(SCOPE_OWN, all_matching=True), Damage(int(m.group(1)))]



@verb(r"^all of your cookies gain \+(\d+) hp\.?$")
def _v_gain_all(m) -> list[Op]:
    return [Select(SCOPE_OWN, all_matching=True), GainHP(int(m.group(1)))]


@verb(r"^(?:that|those) cookies? gains? \+(\d+) hp\.?$")
def _v_gain(m) -> list[Op]:
    return [GainHP(int(m.group(1)))]


@verb(r"^this cookie gains \+(\d+) hp\.?$")
def _v_gain_self(m) -> list[Op]:
    return [GainHP(int(m.group(1)), ref=REF_SELF)]


@verb(r"^the cookie with this card attached for hp gains \+(\d+) hp\.?$")
def _v_gain_host(m) -> list[Op]:
    return [GainHP(int(m.group(1)), ref=REF_HOST)]


@verb(r"^during this turn, (?:that|those) cookies? deals? -(\d+) attack damage\.?$")
def _v_debuff(m) -> list[Op]:
    return [ModifyAttack(-int(m.group(1)))]


@verb(r"^(?:that|those) cookies? deals? an additional -(\d+) attack damage\.?$")
def _v_debuff_more(m) -> list[Op]:
    return [ModifyAttack(-int(m.group(1)))]


@verb(r"^during this turn, (?:that|those) cookies? gains? \+(\d+) attack damage\.?$")
def _v_buff(m) -> list[Op]:
    return [ModifyAttack(int(m.group(1)))]


@verb(r"^(?:during this turn, )?this cookie gains \+(\d+) attack damage\.?$")
def _v_buff_self(m) -> list[Op]:
    return [ModifyAttack(int(m.group(1)), ref=REF_SELF)]


@verb(r"^set this cookie as active\.?$")
def _v_set_self_active(m) -> list[Op]:
    return [SetSelfActive()]


@verb(r"^draw(?: up to)? (\d+) cards? from your deck and discard (\d+) cards?\.?$")
def _v_draw_discard(m) -> list[Op]:
    return [Draw(int(m.group(1))), Discard(int(m.group(2)))]


@verb(r"^this cookie receives -(\d+) attack damage\.?$")
def _v_debuff_self(m) -> list[Op]:
    return [ModifyAttack(-int(m.group(1)), ref=REF_SELF)]


@verb(r"^draw(?: up to)? (\d+) cards? from your deck\.?$")
def _v_draw(m) -> list[Op]:
    return [Draw(int(m.group(1)))]


@verb(r"^discard (\d+) cards?\.?$")
def _v_discard(m) -> list[Op]:
    return [Discard(int(m.group(1)))]


@verb(r"^make (?:that|those) cookies? faint\.?$")
def _v_faint(m) -> list[Op]:
    return [Faint()]


@verb(r"^place (?:up to )?(\d+) cards? from the top of (?:that|this) cookie's hp "
      r"in(?:to)? (?:the|your) trash\.?$")
def _v_trash_hp(m) -> list[Op]:
    return [TrashHP(int(m.group(1)))]


@verb(r"^place (?:up to )?(\d+) cards? from the top of (?:that|this) cookie's hp "
      r"into your opponent's trash\.?$")
def _v_trash_hp_opp(m) -> list[Op]:
    return [TrashHP(int(m.group(1)), to_opponent_trash=True)]


@verb(r"^return this cookie to your hand\.?$")
def _v_bounce_self(m) -> list[Op]:
    return [ReturnToHand(ref=REF_SELF)]


@verb(r"^return (?:that|those) cookies? to your hand\.?$")
def _v_bounce(m) -> list[Op]:
    return [ReturnToHand()]


@verb(r"^rest up to (\d+) cards? in your opponent's support area\.?$")
def _v_rest_opp(m) -> list[Op]:
    return [RestSupport(int(m.group(1)), mine=False)]


@verb(r"^rest up to (\d+) cards? in your support area\.?$")
def _v_rest_mine(m) -> list[Op]:
    return [RestSupport(int(m.group(1)))]


@verb(r"^set up to (\d+) cards? (?:from|in) your support area as active\.?$")
def _v_set_active(m) -> list[Op]:
    return [SetSupportActive(int(m.group(1)))]


@verb(r"^place up to (\d+) cards? from the top of your deck (?:in|into) your "
      r"support area as rested\.?$")
def _v_mill_support(m) -> list[Op]:
    return [MillToSupport(int(m.group(1)))]


@verb(r"^place (?:up to )?(\d+) cards? from the top of your deck into (?:the|your) trash\.?$")
def _v_mill(m) -> list[Op]:
    return [MillDeck(int(m.group(1)))]


@verb(r"^return up to (\d+) item cards? from your support area to your hand\.?$")
def _v_return_item(m) -> list[Op]:
    return [ReturnSupportToHand(card_type=CardType.ITEM)]


@verb(r"^return up to (\d+) cards? from your support area to your hand\.?$")
def _v_return_support(m) -> list[Op]:
    return [ReturnSupportToHand()]


@verb(r"^place up to (\d+) of your opponent's stage cards? in the trash\.?$")
def _v_trash_stage(m) -> list[Op]:
    return [TrashStage(int(m.group(1)), mine=False)]


@verb(r"^(?:that|those) cookies? receives? damage equal to the number of cards "
      r"rested by this effect\.?$")
def _v_damage_rested(m) -> list[Op]:
    return [DamageEqualToRested()]


@verb(r"^(?:place|put) (?:up to )?(\d+) (?:of )?(?:your opponent's )?(.*?cookies?) "
      r"from (?:their|your) battle area into (?:the|their|your) trash\.?$")
def _v_trash_cookie(m) -> list[Op]:
    scope = SCOPE_OPPONENT if "opponent" in m.group(0).lower() else SCOPE_OWN
    return [Select(scope, count=int(m.group(1)),
                   filter=parse_filter(m.group(2)), ref="to_trash"),
            TrashCookies(ref="to_trash")]


@verb(r"^place (?:up to )?(\d+) of your opponent's cookies whose remaining hp "
      r"is (\d+) or less into the trash\.?$")
def _v_trash_weak_cookie(m) -> list[Op]:
    return [Select(SCOPE_OPPONENT, count=int(m.group(1)),
                   filter=Filter(max_hp=int(m.group(2))), ref="to_trash"),
            TrashCookies(ref="to_trash")]


@verb(r"^place (?:up to )?(\d+) cookies? that is LV\.(\d+) or lower from your "
      r"opponent's battle area into their trash\.?$")
def _v_trash_low_cookie(m) -> list[Op]:
    return [Select(SCOPE_OPPONENT, count=int(m.group(1)),
                   filter=Filter(max_level=int(m.group(2))), ref="to_trash"),
            TrashCookies(ref="to_trash")]


@verb(r"^place (?:that|those) cookies? in(?:to)? the trash\.?$")
def _v_trash_selected(m) -> list[Op]:
    return [TrashCookies()]


@verb(r"^your opponent must place (\d+) cards? from their hand into the trash\.?$")
def _v_opponent_discard(m) -> list[Op]:
    return [OpponentDiscard(int(m.group(1)))]


@verb(r"^(?:that|those) cookies? (?:is|are) not set as active during your "
      r"opponent's next active phase\.?$")
def _v_skip_active(m) -> list[Op]:
    return [SkipNextActive()]


@verb(r"^during this turn, (?:that|those) cookies? receives? -(\d+) attack damage\.?$")
def _v_self_debuff_target(m) -> list[Op]:
    return [ModifyAttack(-int(m.group(1)))]


@verb(r"^during this turn, all of your cookies gain \+(\d+) attack damage\.?$")
def _v_buff_all_mine(m) -> list[Op]:
    return [Select(SCOPE_OWN, all_matching=True), ModifyAttack(int(m.group(1)))]


@verb(r"^during this turn, all of your opponent's cookies deal -(\d+) attack damage\.?$")
def _v_debuff_all_theirs(m) -> list[Op]:
    return [Select(SCOPE_OPPONENT, all_matching=True), ModifyAttack(-int(m.group(1)))]


@verb(r"^place up to (\d+) cards? from the top of each of your opponent's "
      r"cookies'? hp into the trash\.?$")
def _v_trash_hp_all(m) -> list[Op]:
    return [TrashHPOfAllEnemies(int(m.group(1)))]


@verb(r"^_unused_mill_variant_(\d+)$")
def _v_mill_own(m) -> list[Op]:
    return [MillDeck(int(m.group(1)))]


@verb(r"^(?:take|place) (?:up to )?(\d+) cards? from the top (?:of )?your deck "
      r"and place it in your support area as active\.?$")
def _v_mill_support_active(m) -> list[Op]:
    return [MillToSupport(int(m.group(1)), rested=False)]


@verb(r"^select up to (\d+) (.*?) in your break area\.?$")
def _v_select_break(m) -> list[Op]:
    return [MoveCards(ZONE_BREAK, ZONE_TRASH, int(m.group(1)),
                      parse_card_filter(m.group(2)))]


@verb(r"^select up to (\d+) (.*?) in (?:your opponent's|their) battle area"
      r"(?: that does not have)?\.?$")
def _v_select_in_enemy_battle(m) -> list[Op]:
    return [Select(SCOPE_OPPONENT, count=int(m.group(1)),
                   filter=parse_filter(m.group(2)))]


@verb(r"^select up to (\d+) (.*?) in your battle area\.?$")
def _v_select_in_own_battle(m) -> list[Op]:
    return [Select(SCOPE_OWN, count=int(m.group(1)),
                   filter=parse_filter(m.group(2)))]


@verb(r"^place this cookie in(?:to)? (?:the|your) trash\.?$")
def _v_trash_self(m) -> list[Op]:
    return [TrashSelf()]


@verb(r"^all other cookies receive (\d+) damage\.?$")
def _v_damage_all_others(m) -> list[Op]:
    return [Select(SCOPE_ALL, all_matching=True,
                   filter=Filter(exclude_self=True)),
            Damage(int(m.group(1)))]


@verb(r"^all cookies receive (\d+) damage\.?$")
def _v_damage_everything(m) -> list[Op]:
    return [Select(SCOPE_ALL, all_matching=True), Damage(int(m.group(1)))]


@verb(r"^both players place (\d+) cards? from the top of their decks? into "
      r"(?:the|their) trash\.?$")
def _v_mill_both(m) -> list[Op]:
    return [MillDeck(int(m.group(1))), MillOpponentDeck(int(m.group(1)))]


@verb(r"^your opponent places? (\d+) cards? from the top of their deck into "
      r"(?:the|their) trash\.?$")
def _v_mill_opponent(m) -> list[Op]:
    return [MillOpponentDeck(int(m.group(1)))]


@verb(r"^reveal (\d+) cards? from the top of your deck\.?$")
def _v_reveal(m) -> list[Op]:
    return [Reveal(int(m.group(1)))]


@verb(r"^play up to (\d+) (.*?) from your trash\.?$")
def _v_play_from_trash(m) -> list[Op]:
    return [PlayFromTrash(parse_card_filter(m.group(2)))]


@verb(r"^play up to (\d+) (.*?) from your support area\.?$")
def _v_play_from_support(m) -> list[Op]:
    return [PlayFromSupport(parse_card_filter(m.group(2)))]


@verb(r"^place (?:up to )?(\d+) cards? from your hand (?:at|on) the bottom of "
      r"your deck\.?$")
def _v_hand_to_deck_bottom(m) -> list[Op]:
    return [MoveCards(ZONE_HAND, ZONE_DECK_BOTTOM, int(m.group(1)),
                      optional=True)]


@verb(r"^place (?:up to )?(\d+) cards? from your hand on the top of your deck\.?$")
def _v_hand_to_deck_top(m) -> list[Op]:
    return [MoveCards(ZONE_HAND, ZONE_DECK_TOP, int(m.group(1)), optional=True)]


@verb(r"^draw(?: up to)? (\d+) cards? from your deck and place (\d+) cards? "
      r"from your hand on the top of your deck\.?$")
def _v_draw_then_topdeck(m) -> list[Op]:
    return [Draw(int(m.group(1))),
            MoveCards(ZONE_HAND, ZONE_DECK_TOP, int(m.group(2)), optional=True)]


@verb(r"^return up to (\d+) (.*?) from your battle area to your hand\.?$")
def _v_bounce_own(m) -> list[Op]:
    return [Select(SCOPE_OWN, count=int(m.group(1)),
                   filter=parse_filter(m.group(2)), ref="bounce"),
            ReturnToHand(ref="bounce")]


@verb(r"^the (?:LV\.\d+ or higher )?cookie with this card attached for hp "
      r"gains \+(\d+) hp\.?$")
def _v_gain_host_leveled(m) -> list[Op]:
    return [GainHP(int(m.group(1)), ref=REF_HOST)]


@verb(r"^your opponent (?:places?|discards?) (\d+) cards?"
      r"(?: from their hand into (?:the|their) trash)?\.?$")
def _v_opp_discard_alt(m) -> list[Op]:
    return [OpponentDiscard(int(m.group(1)))]


@verb(r"^place (?:up to )?(\d+) cards? from the top of your opponent's deck "
      r"into (?:the|your opponent's|their) trash\.?$")
def _v_mill_opp_alt(m) -> list[Op]:
    return [MillOpponentDeck(int(m.group(1)))]


@verb(r"^place (?:up to )?(\d+) cards? from the top of either player's decks? "
      r"into (?:the|their) trash\.?$")
def _v_mill_either(m) -> list[Op]:
    return [MillDeck(int(m.group(1))), MillOpponentDeck(int(m.group(1)))]


@verb(r"^select up to (\d+) (.*?) from your break area and play them\.?$")
def _v_play_from_break(m) -> list[Op]:
    return [PlayFromBreak(parse_card_filter(m.group(2)))]


@verb(r"^(?:choose|select) (?:up to )?(\d+) (.*?) from your trash and place them "
      r"in your break area\.?$")
def _v_trash_to_break_alt(m) -> list[Op]:
    return [MoveCards(ZONE_TRASH, ZONE_BREAK, int(m.group(1)),
                      parse_card_filter(m.group(2)))]


@verb(r"^return (?:up to )?(\d+) cards? from your support area to your hand\.?$")
def _v_support_to_hand(m) -> list[Op]:
    return [ReturnSupportToHand()]


@verb(r"^set (?:up to )?(\d+) cards? from your support area as active\.?$")
def _v_set_support_active_alt(m) -> list[Op]:
    return [SetSupportActive(int(m.group(1)))]


@verb(r"^return all cards from your trash to your deck and shuffle it\.?$")
def _v_recycle_trash(m) -> list[Op]:
    return [RecycleTrash()]


@verb(r"^you can return this cookie to your hand\.?$")
def _v_may_bounce_self(m) -> list[Op]:
    return [ReturnToHand(ref=REF_SELF)]


@verb(r"^place this cookie in(?:to)? your break area\.?$")
def _v_self_to_break_verb(m) -> list[Op]:
    return [SelfToBreak()]


@verb(r"^(?:that|the selected) cookie deals an additional -(\d+) attack damage\.?$")
def _v_additional_debuff(m) -> list[Op]:
    return [ModifyAttack(-int(m.group(1)))]


@verb(r"^deals? (\d+) damage to all cookies other than this cookie\.?$")
def _v_damage_others(m) -> list[Op]:
    return [Select(SCOPE_ALL, all_matching=True, filter=Filter(exclude_self=True)),
            Damage(int(m.group(1)))]


@verb(r"^select up to (\d+) (?:active )?cards? from your opponent's support area\.?$")
def _v_select_opp_support(m) -> list[Op]:
    return [RestSupport(int(m.group(1)), mine=False)]


@verb(r"^your opponent rests (\d+) (?:active )?cards? in their support area\.?$")
def _v_opp_rests(m) -> list[Op]:
    return [RestSupport(int(m.group(1)), mine=False)]


@verb(r"^(?:that|those) cookies? cannot attack until the start of the next turn\.?$")
def _v_cannot_attack(m) -> list[Op]:
    return [SkipNextActive()]


@verb(r"^draw(?: up to)? (\d+) cards? from your deck and select up to (\d+) of "
      r"your opponent's cookies\.?$")
def _v_draw_then_select(m) -> list[Op]:
    return [Draw(int(m.group(1))),
            Select(SCOPE_OPPONENT, count=int(m.group(2)))]


@verb(r"^place (?:up to )?(\d+) cards? from the top of your deck into your "
      r"support area as active\.?$")
def _v_mill_support_active_alt(m) -> list[Op]:
    return [MillToSupport(int(m.group(1)), rested=False)]


@verb(r"^place (?:up to )?(\d+) cards? from each player's support area into "
      r"their trash\.?$")
def _v_trash_both_support(m) -> list[Op]:
    return [TrashOwnSupport(int(m.group(1))), TrashOpponentSupport(int(m.group(1)))]


@verb(r"^select up to (\d+) (.*?) from either player's battle area\.?$")
def _v_select_either(m) -> list[Op]:
    return [Select(SCOPE_ALL, count=int(m.group(1)),
                   filter=parse_filter(m.group(2)))]


@verb(r"^(?:take|place) (?:up to )?(\d+) cards? from the top of your deck and "
      r"place it in your support area as rested\.?$")
def _v_mill_support_rested(m) -> list[Op]:
    return [MillToSupport(int(m.group(1)))]


@verb(r"^during this turn, (?:that|those) cookies? deals? \+(\d+) attack damage\.?$")
def _v_buff_selected(m) -> list[Op]:
    return [ModifyAttack(int(m.group(1)))]


@verb(r"^deals? (\d+) damage to each of those cookies\.?$")
def _v_damage_each_selected(m) -> list[Op]:
    return [Damage(int(m.group(1)))]


@verb(r"^select up to (\d+) (.*?) from your trash that do not have flip\.?$")
def _v_select_trash_nonflip(m) -> list[Op]:
    return [SelectTrash(int(m.group(1)), CardFilter(is_flip=False))]


@verb(r"^return those cards to your deck and shuffle it\.?$")
def _v_return_selected_to_deck(m) -> list[Op]:
    return [SelectedTrashToDeck()]


@verb(r"^place (?:up to )?(\d+) of your opponent's cookies whose remaining hp is "
      r"(\d+) or less from their battle area into the trash\.?$")
def _v_trash_weak_from_battle(m) -> list[Op]:
    return [Select(SCOPE_OPPONENT, count=int(m.group(1)),
                   filter=Filter(max_hp=int(m.group(2))), ref="weak"),
            TrashCookies(ref="weak")]


@verb(r"^select up to (\d+) (.*?) from your break area and play (?:it|them)\.?$")
def _v_play_from_break_alt(m) -> list[Op]:
    return [PlayFromBreak(parse_card_filter(m.group(2)))]


@verb(r"^select up to (\d+) (.*?) from your break area\.?$")
def _v_select_from_break(m) -> list[Op]:
    return [MoveCards(ZONE_BREAK, ZONE_TRASH, int(m.group(1)),
                      parse_card_filter(m.group(2)))]


@verb(r"^place (?:that|those) cookies? in(?:to)? the break area\.?$")
def _v_break_selected(m) -> list[Op]:
    return [Faint()]


@verb(r"^deals? (\d+) damage to all cookies\.?$")
def _v_damage_all_cookies(m) -> list[Op]:
    return [Select(SCOPE_ALL, all_matching=True), Damage(int(m.group(1)))]


@verb(r"^place them in your support area as (active|rested)\.?$")
def _v_selected_to_support(m) -> list[Op]:
    return [SelectedCookieToSupport(rested=m.group(1).lower() == "rested")]


@verb(r"^place this card in your support area as (active|rested)\.?$")
def _v_self_card_to_support(m) -> list[Op]:
    return [SelfCardToSupport(rested=m.group(1).lower() == "rested")]


@verb(r"^(\d+) of your other cookies gains? \+(\d+) hp\.?$")
def _v_other_gains_hp(m) -> list[Op]:
    return [Select(SCOPE_OWN, count=int(m.group(1)),
                   filter=Filter(exclude_self=True), ref="ally"),
            GainHP(int(m.group(2)), ref="ally")]


@verb(r"^(?:those|that) cookies? deals? -(\d+) attack damage(?: each)? during "
      r"your opponent's next turn\.?$")
def _v_debuff_next_turn(m) -> list[Op]:
    return [DebuffNextTurn(int(m.group(1)))]


# "for each X in your break area", and its two variants: "for every 2 X",
# which pays out once per pair rather than once per card, and "in either
# battle area", which counts both sides of the table.
_FOR_EACH = re.compile(
    r"for (?:each|every) (?:(?P<per>\d+) )?(?P<what>.*?)\s*(?:cards?|cookies?)?\s+in "
    r"(?P<who>your opponent's|your|their|either)\s*"
    r"(?P<zone>break area|trash|support area|battle area|hand)", re.I)

# "for each of your opponent's Cookies that fainted during this turn" counts an
# event, not a pile, so it is matched separately and answered by the running
# count the engine already keeps for both players.
_FOR_EACH_FAINTED = re.compile(
    r"for each of (?P<who>your opponent's|your) cookies? that fainted "
    r"during this turn", re.I)


def _count_source(phrase: str):
    """Turn a "for each ..." tail into a (filter, zone, opponent, divisor).

    `divisor` is the "every 2" case: the count is floor-divided by it, so two
    Cookies in the break area are worth one bonus and three are still worth
    one. Everything else divides by 1 and is unchanged.
    """
    fainted = _FOR_EACH_FAINTED.search(phrase)
    if fainted:
        return (CardFilter(), "fainted",
                "opponent" in fainted.group("who").lower(), 1)

    match = _FOR_EACH.search(phrase)
    if not match:
        return None
    groups = match.groupdict()
    who = (groups["who"] or "").lower()
    zone = {"break area": "break", "trash": "trash", "support area": "support",
            "battle area": "battle", "hand": "hand"}[groups["zone"].lower()]
    if zone == "battle" and who.startswith("either"):
        zone = "battle_both"
    elif who.startswith("either"):
        # "either trash", "either hand" — nothing prints those, and guessing at
        # one side of it would misreport the card.
        return None
    return (parse_card_filter(groups["what"]), zone, "opponent" in who,
            int(groups["per"] or 1))


@verb(r"^(?:that|those) cookies? receives? (\d+) damage (for each .*)\.?$")
def _v_damage_scaling(m) -> list[Op]:
    source = _count_source(m.group(2))
    if source is None:
        raise CompileError(f"verb: {m.group(0)!r}")
    return [ScaledDamage(int(m.group(1)), *source)]


@verb(r"^this cookie gains \+(\d+) hp (for each .*)\.?$")
def _v_gain_hp_scaling(m) -> list[Op]:
    source = _count_source(m.group(2))
    if source is None:
        raise CompileError(f"verb: {m.group(0)!r}")
    return [ScaledGainHP(int(m.group(1)), *source)]


@verb(r"^draw(?: up to)? (\d+) cards?(?: from your deck)? (for each .*)\.?$")
def _v_draw_scaling(m) -> list[Op]:
    source = _count_source(m.group(2))
    if source is None:
        raise CompileError(f"verb: {m.group(0)!r}")
    return [ScaledDraw(int(m.group(1)), *source)]


@verb(r"^(?:that|those) cookies? gains? \+(\d+) hp (for each .*)\.?$")
def _v_selected_gain_hp_scaling(m) -> list[Op]:
    source = _count_source(m.group(2))
    if source is None:
        raise CompileError(f"verb: {m.group(0)!r}")
    return [ScaledGainHP(int(m.group(1)), *source, ref=REF_IT)]


@verb(r"^during this turn,? (?:that|those|this) cookies? gains? \+(\d+) "
      r"attack damage (for (?:each|every) .*)\.?$")
def _v_attack_buff_scaling(m) -> list[Op]:
    source = _count_source(m.group(2))
    if source is None:
        raise CompileError(f"verb: {m.group(0)!r}")
    return [ScaledModifyAttack(int(m.group(1)), *source)]


@verb(r"^during this turn,? (?:that|those|this) cookies? deals? -(\d+) "
      r"attack damage (for (?:each|every) .*)\.?$")
def _v_attack_debuff_scaling(m) -> list[Op]:
    """The same op as the buff, with the sign the text prints."""
    source = _count_source(m.group(2))
    if source is None:
        raise CompileError(f"verb: {m.group(0)!r}")
    return [ScaledModifyAttack(-int(m.group(1)), *source)]


@verb(r"^return (?:up to )?(\d+) cards? from the top of (?:that|this|your) "
      r"cookie'?s? hp to your hand\.?$")
def _v_hp_to_hand(m) -> list[Op]:
    """"this Cookie" on a stage card is the one just selected, not the stage —
    a stage has no HP — so all three wordings land on the same selection."""
    return [HPToHand(int(m.group(1)))]


@verb(r"^return (?:that|those) cookies? to (?:the|your) hand\.?$")
def _v_bounce_selected_alt(m) -> list[Op]:
    return [ReturnToHand()]


@verb(r"^place (?:that|those) cookies? on the bottom of (?:the|your|its owner's) deck\.?$")
def _v_deck_selected(m) -> list[Op]:
    return [MoveSelectedToDeck()]


@verb(r"^select up to (\d+) cards? in your opponent's support area\.?$")
def _v_select_opp_support_cards(m) -> list[Op]:
    return [RestSupport(int(m.group(1)), mine=False)]


@verb(r"^rest those cards\.?$")
def _v_rest_those(m) -> list[Op]:
    return []           # the selection above already rested them


@verb(r"^during this turn, your opponent cannot activate\.?$")
def _v_disable_blockers(m) -> list[Op]:
    """The 【Blocker】 in "cannot activate 【Blocker】" is stripped as a marker."""
    return [DisableBlockers()]


@verb(r"^select up to (\d+) (.*?) in either player's battle area\.?$")
def _v_select_either_battle(m) -> list[Op]:
    return [Select(SCOPE_ALL, count=int(m.group(1)),
                   filter=parse_filter(m.group(2)))]


@verb(r"^place (?:up to )?(\d+) of your opponent's stages? in(?:to)? the trash\.?$")
def _v_trash_opp_stage(m) -> list[Op]:
    return [TrashStage(int(m.group(1)), mine=False)]


@verb(r"^place (\d+) random cards? from your opponent's hand into the trash\.?$")
def _v_random_discard(m) -> list[Op]:
    return [RandomOpponentDiscard(int(m.group(1)))]


@verb(r"^this cookie receives -(\d+) attack damage until the end of your "
      r"opponent's turn\.?$")
def _v_self_debuff_next(m) -> list[Op]:
    return [ModifyAttack(-int(m.group(1)), ref=REF_SELF)]


@verb(r"^you can draw cards from your deck until there are (\d+) cards in your hand\.?$")
def _v_draw_up_to_hand(m) -> list[Op]:
    return [DrawToHandSize(int(m.group(1)))]


@verb(r"^(?:place|return) (?:that|those) cookies? to your opponent's hand\.?$")
def _v_bounce_to_owner(m) -> list[Op]:
    return [ReturnToHand()]


@verb(r"^return (?:that|those) cookies? to your opponent's hand\.?$")
def _v_bounce_owner_alt(m) -> list[Op]:
    return [ReturnToHand()]


@verb(r"^play that cookie\.?$")
def _v_play_that_cookie(m) -> list[Op]:
    return [PlaySelectedFromBreak()]


@verb(r"^set (?:that|those) cookies? as active\.?$")
def _v_set_selected_active(m) -> list[Op]:
    return [SetSelectedActive()]


@verb(r"^when your turn ends, set up to (\d+) cards? from your support area "
      r"as active\.?$")
def _v_bank_untap(m) -> list[Op]:
    return [BankEndTurnUntap(int(m.group(1)))]


@verb(r"^draw(?: up to)? (\d+) cards? from your deck and place up to (\d+) cards? "
      r"from the top of your deck into the trash\.?$")
def _v_draw_and_mill(m) -> list[Op]:
    return [Draw(int(m.group(1))), MillDeck(int(m.group(2)))]


@verb(r"^place (?:that|those) cookies? in your opponent's (support area|break area)"
      r"(?: as (active|rested))?\.?$")
def _v_selected_to_opp_zone(m) -> list[Op]:
    if m.group(1).lower() == "break area":
        return [Faint()]
    return [SelectedCookieToSupport(rested=(m.group(2) or "rested").lower() == "rested")]


@verb(r"^place (?:that|those) cookies? in your support area as (active|rested)\.?$")
def _v_selected_to_own_support(m) -> list[Op]:
    return [SelectedCookieToSupport(rested=m.group(1).lower() == "rested")]


@verb(r"^place (?:up to )?(\d+) cards? from the top of your opponent's deck in "
      r"(?:the|their) trash\.?$")
def _v_mill_opp_in(m) -> list[Op]:
    return [MillOpponentDeck(int(m.group(1)))]


@verb(r"^both players place the top (\d+) cards? from their decks? into the trash\.?$")
def _v_mill_both_alt(m) -> list[Op]:
    return [MillDeck(int(m.group(1))), MillOpponentDeck(int(m.group(1)))]


@verb(r"^your opponent must place (\d+) cards? from their hand on the bottom of "
      r"their deck\.?$")
def _v_opp_hand_to_deck(m) -> list[Op]:
    return [OpponentHandToDeck(int(m.group(1)))]


@verb(r"^place them on the bottom of your deck in any order\.?$")
def _v_selected_trash_to_deck_bottom(m) -> list[Op]:
    return [SelectedTrashToDeckBottom()]


@verb(r"^(?:you can )?place this card in your (support area|break area)"
      r"(?: as (active|rested))?\.?$")
def _v_self_card_to_zone(m) -> list[Op]:
    if m.group(1).lower() == "break area":
        return [SelfCardToBreak()]
    return [SelfCardToSupport(rested=(m.group(2) or "rested").lower() == "rested")]


@verb(r"^during this turn, (?:that|those) cookies?'?s? hp-attached flip effects "
      r"cannot be activated\.?$")
def _v_disable_flips(m) -> list[Op]:
    return [DisableFlips()]


@verb(r"^reveal (?:up to )?(\d+) cards? from the bottom of your deck\.?$")
def _v_reveal_bottom(m) -> list[Op]:
    return [Reveal(int(m.group(1)), from_bottom=True)]


@verb(r"^place (?:up to )?(\d+) cards? from your hand to the top of "
      r"(?:this|that) cookie'?s? hp\.?$")
def _v_hand_to_hp(m) -> list[Op]:
    return [HandToHP(int(m.group(1)))]


@verb(r"^place (?:up to )?(\d+) (.*?)cards? from your support area to the top of "
      r"(?:this|that) cookie'?s? hp\.?$")
def _v_support_to_hp(m) -> list[Op]:
    return [SupportToHP(int(m.group(1)), parse_card_filter(m.group(2)))]


@verb(r"^(?:take|place) (?:up to )?(\d+) cards? from the top of that cookie'?s? hp "
      r"and (?:place|add) it to the top of this cookie'?s? hp\.?$")
def _v_hp_transfer(m) -> list[Op]:
    return [TransferHP(int(m.group(1)))]


@verb(r"^(?:take|place) (?:up to )?(\d+) cards? from the top of this cookie'?s? hp "
      r"and (?:place|add) it to the top of your other cookie'?s? hp\.?$")
def _v_hp_transfer_out(m) -> list[Op]:
    return [TransferHP(int(m.group(1)), from_self=True)]


@verb(r"^place (?:up to )?(\d+) cards? from the top of each cookie'?s? hp "
      r"into (?:the|your) trash\.?$")
def _v_trash_hp_each(m) -> list[Op]:
    return [TrashHP(int(m.group(1)))]


@verb(r"^place (?:up to )?(\d+) cards? from the top of this cookie'?s? hp cards? "
      r"into (?:the|your) trash\.?$")
def _v_trash_own_hp(m) -> list[Op]:
    return [TrashHP(int(m.group(1)), ref=REF_SELF)]


@verb(r"^rest that card\.?$")
def _v_rest_that_card(m) -> list[Op]:
    return []          # the selector above already rested it


@verb(r"^view (\d+) cards? from the top of your deck[;,]? (?:and )?place them "
      r"(?:back )?(?:on|to) the top of (?:the|your) deck in any order\.?$")
def _v_view_and_reorder(m) -> list[Op]:
    """Look at the top N and put every one of them back, in an order you pick.

    `take=0` is what makes it a pure reorder: nothing is added to hand, so the
    whole viewed run is the leftover that `rest="top"` puts back.
    """
    return [ViewTop(int(m.group(1)), take=0, rest="top")]


@verb(r"^draw(?: up to)? (\d+) cards? from your deck and place this card at "
      r"the (bottom|top) of your deck\.?$")
def _v_draw_and_recycle_self(m) -> list[Op]:
    return [Draw(int(m.group(1))),
            SelfCardToDeck(bottom=m.group(2).lower() == "bottom")]


@verb(r"^place that card in(?:to)? the trash\.?$")
def _v_trash_selected_support(m) -> list[Op]:
    return [TrashSelectedSupport()]


@verb(r"^select up to (\d+) other (.*?) in your battle area and set them as active\.?$")
def _v_select_and_untap(m) -> list[Op]:
    return [Select(SCOPE_OWN, count=int(m.group(1)),
                   filter=parse_filter(m.group(2), exclude_self=True), ref="untap"),
            SetSelectedActive(ref="untap")]


@verb(r"^place (?:up to )?(\d+) random cards? from your opponent's hand into "
      r"(?:the|their) trash\.?$")
def _v_random_discard_alt(m) -> list[Op]:
    return [RandomOpponentDiscard(int(m.group(1)))]


@verb(r"^your opponent draws (\d+) cards? from their deck\.?$")
def _v_opponent_draws(m) -> list[Op]:
    return [OpponentDraws(int(m.group(1)))]


@verb(r"^place (?:up to )?(\d+) (.*?)cards? from your hand in(?:to)? your support "
      r"area as (active|rested)\.?$")
def _v_hand_to_support_filtered(m) -> list[Op]:
    return [SupportFromHand(int(m.group(1)),
                            rested=m.group(3).lower() == "rested")]


@verb(r"^place this cookie in(?:to)? your support area as (active|rested)\.?$")
def _v_self_cookie_to_support(m) -> list[Op]:
    return [SelectedCookieToSupport(ref=REF_SELF,
                                    rested=m.group(1).lower() == "rested")]


@verb(r"^rest (?:up to )?(\d+) cards? in your opponent's support area\.?$")
def _v_rest_opp_support_alt(m) -> list[Op]:
    return [RestSupport(int(m.group(1)), mine=False)]


@verb(r"^add (?:up to )?(\d+) (.*?)cards? from your support area to the top of "
      r"(?:that|this) cookie'?s? hp\.?$")
def _v_support_to_hp_target(m) -> list[Op]:
    return [SupportToHP(int(m.group(1)), parse_card_filter(m.group(2)), ref=REF_IT)]


@verb(r"^the selected cookie receives (\d+) damage\.?$")
def _v_selected_receives(m) -> list[Op]:
    return [Damage(int(m.group(1)))]


@verb(r"^draw(?: up to)? (\d+) additional cards?\.?$")
def _v_draw_additional(m) -> list[Op]:
    return [Draw(int(m.group(1)))]


@verb(r"^select up to (\d+) of your opponent's stage cards?\.?$")
def _v_select_opp_stage(m) -> list[Op]:
    return [TrashStage(int(m.group(1)), mine=False)]


@verb(r"^place that card in(?:to)? your opponent's trash\.?$")
def _v_stage_already_trashed(m) -> list[Op]:
    return []          # the selector above already trashed it


@verb(r"^place (?:up to )?(\d+) cards? from the top of all of your opponent's "
      r"cookies'? hp into (?:the|your) trash\.?$")
def _v_trash_hp_all_enemies(m) -> list[Op]:
    return [TrashHPOfAllEnemies(int(m.group(1)))]


@verb(r"^all (?:of )?your cookies that have (\d+) or less hp gain \+(\d+) hp\.?$")
def _v_heal_wounded_allies(m) -> list[Op]:
    return [Select(SCOPE_OWN, all_matching=True,
                   filter=Filter(max_hp=int(m.group(1)))),
            GainHP(int(m.group(2)))]


@verb(r"^all cookies with (\d+) or more hp remaining receive (\d+) damage\.?$")
def _v_damage_healthy(m) -> list[Op]:
    return [Select(SCOPE_ALL, all_matching=True,
                   filter=Filter(min_hp=int(m.group(1)))),
            Damage(int(m.group(2)))]


@verb(r"^all cookies that are not \[([^\]]+)\] receive (\d+) damage\.?$")
def _v_damage_except_named(m) -> list[Op]:
    return [DamageAllExcept(m.group(1), int(m.group(2)))]


@verb(r"^select (\d+) of your opponent's cookies\.?$")
def _v_select_enemy_mandatory(m) -> list[Op]:
    return [Select(SCOPE_OPPONENT, count=int(m.group(1)), optional=False)]


@verb(r"^make up to (\d+) of your cookies faint\.?$")
def _v_faint_own(m) -> list[Op]:
    return [Select(SCOPE_OWN, count=int(m.group(1)), ref="sacrifice"),
            Faint(ref="sacrifice")]


@verb(r"^play up to (\d+) (.*?) from your break area\.?$")
def _v_play_from_break_plain(m) -> list[Op]:
    return [PlayFromBreak(parse_card_filter(m.group(2)))]


@verb(r"^play up to (\d+) (.*?) from your hand\.?$")
def _v_play_from_hand(m) -> list[Op]:
    return [PlayFromHand(parse_card_filter(m.group(2)))]


@verb(r"^set up to (\d+) (.*?)cards? from your support area as active\.?$")
def _v_set_support_active_filtered(m) -> list[Op]:
    return [SetSupportActive(int(m.group(1)))]


@verb(r"^place (?:up to )?(\d+) cards? from the top of your deck in your support "
      r"area as active\.?$")
def _v_mill_support_active_in(m) -> list[Op]:
    return [MillToSupport(int(m.group(1)), rested=False)]


@verb(r"^place (?:up to )?(\d+) (.*?)cards? from your trash into your support area "
      r"as (active|rested)\.?$")
def _v_trash_to_support(m) -> list[Op]:
    return [MoveCards(ZONE_TRASH, ZONE_SUPPORT, int(m.group(1)),
                      parse_card_filter(m.group(2)),
                      rested=m.group(3).lower() == "rested")]


@verb(r"^place this cookie in(?:to)? the break area\.?$")
def _v_self_to_break_plain(m) -> list[Op]:
    return [SelfToBreak()]


@verb(r"^during this turn, the attack cost of that cookie is increased by "
      r"(\d+) \{[A-Za-z]\}\.?$")
def _v_attack_surcharge(m) -> list[Op]:
    return [AttackSurcharge(int(m.group(1)))]


@verb(r"^during this turn, the LV\.? of this cookie in your battle area becomes "
      r"(\d+)\.?$")
def _v_level_becomes(m) -> list[Op]:
    return [SetLevel(int(m.group(1)))]


@verb(r"^place (?:up to )?(\d+) cards? from the top of (?:that|this) cookie'?s? hp "
      r"on the bottom of (?:your opponent's|their|your) deck\.?$")
def _v_hp_to_deck_bottom(m) -> list[Op]:
    return [HPToDeckBottom(int(m.group(1)))]


@verb(r"^place (?:up to )?(\d+) of your (.*?cookies?) .*?in your trash\.?$")
def _v_trash_own_filtered(m) -> list[Op]:
    return [Select(SCOPE_OWN, count=int(m.group(1)),
                   filter=parse_filter(m.group(2)), ref="self_trash"),
            TrashCookies(ref="self_trash")]


@verb(r"^return all cards from both players'? trash to their decks? and shuffle "
      r"them\.?$")
def _v_recycle_both(m) -> list[Op]:
    return [RecycleTrash(), RecycleOpponentTrash()]


@verb(r"^during this turn, your opponent's cookies deal -(\d+) attack damage\.?$")
def _v_debuff_all_enemies(m) -> list[Op]:
    return [Select(SCOPE_OPPONENT, all_matching=True),
            ModifyAttack(-int(m.group(1)))]


@verb(r"^(?:you can )?place this cookie on the (?:top or )?bottom of your deck\.?$")
def _v_self_to_deck_bottom_alt(m) -> list[Op]:
    return [SelfToDeckBottom()]


@verb(r"^place (?:that|those) cookies? on the bottom of (?:their|your opponent's) "
      r"deck\.?$")
def _v_selected_to_owner_deck(m) -> list[Op]:
    return [MoveSelectedToDeck()]


@verb(r"^place (?:up to )?(\d+) (.*?) from your battle area on the (?:top or )?"
      r"bottom of your deck\.?$")
def _v_own_cookie_to_deck(m) -> list[Op]:
    return [Select(SCOPE_OWN, count=int(m.group(1)),
                   filter=parse_filter(m.group(2)), ref="decked"),
            MoveSelectedToDeck(ref="decked")]


@verb(r"^your opponent selects (\d+) of their cookies\.?$")
def _v_opponent_selects(m) -> list[Op]:
    return [OpponentSelects(int(m.group(1)))]


@verb(r"^add (?:up to )?(\d+) cards? from the top of that cookie'?s? hp face-up "
      r"to the bottom of this cookie'?s? hp\.?$")
def _v_steal_hp(m) -> list[Op]:
    return [TransferHP(int(m.group(1)))]


@verb(r"^during this turn, (?:that|this) cookie receives -(\d+) effect damage\.?$")
def _v_effect_damage_reduction(m) -> list[Op]:
    return [EffectDamageReduction(int(m.group(1)))]


@verb(r"^this cookie takes -(\d+) damage until the end of the turn\.?$")
def _v_self_damage_reduction(m) -> list[Op]:
    return [EffectDamageReduction(int(m.group(1)), ref=REF_SELF)]


@verb(r"^all (?:of )?your cookies gain \+(\d+) hp\.?$")
def _v_all_mine_gain_hp(m) -> list[Op]:
    return [Select(SCOPE_OWN, all_matching=True), GainHP(int(m.group(1)))]


@verb(r"^(?:one|1) of your (.*?) gains \+(\d+) hp\.?$")
def _v_one_of_mine_gains(m) -> list[Op]:
    return [Select(SCOPE_OWN, count=1, filter=parse_filter(m.group(1)), ref="ally"),
            GainHP(int(m.group(2)), ref="ally")]


@verb(r"^during this turn, your opponent cannot add hp to cookies via card "
      r"effects\.?$")
def _v_lock_hp_gain(m) -> list[Op]:
    return [LockOpponentHPGain()]


@verb(r"^add (?:up to )?(\d+) cards? from your hand to the top of (?:this|that) "
      r"cookie'?s? hp\.?$")
def _v_hand_to_hp_alt(m) -> list[Op]:
    return [HandToHP(int(m.group(1)))]


@verb(r"^place in your stage area\.?$")
def _v_place_stage(m) -> list[Op]:
    return []           # the engine already handles placement


# --- zone movement ---------------------------------------------------------
# Words that describe a card's *state* on the table rather than what is printed
# on it. `CardFilter` matches printed cards, so it can never honour one of
# these — and quietly dropping the word turns "2 active cards in your support
# area" into "2 cards in your support area", a condition that is true far more
# often than the card says. Refusing is the all-or-nothing rule: a card the
# compiler cannot read in full is better left unimplemented.
_STATE_WORDS = re.compile(r"\b(active|rested|face[- ]up|face[- ]down)\b", re.I)


# "Cookies that have 【Blocker】" arrives here as "Cookies that have", because
# `split_clauses` strips 【...】 markers. The property the card filtered on is
# gone, and an empty filter means *every* Cookie — so the card would be read as
# saying something it does not. Refuse instead.
_STRIPPED_PROPERTY = re.compile(r"\bthat (?:have|has|is|are)\s*$", re.I)


def parse_card_filter(phrase: str) -> CardFilter:
    """Card-level filter for pile contents: colour, level, FLIP, name, type."""
    state = _STATE_WORDS.search(phrase)
    if state:
        raise CompileError(f"card filter describes state, not print: {state.group(0)!r}"
                           f" in {phrase!r}")
    if _STRIPPED_PROPERTY.search(phrase.strip()):
        raise CompileError(f"card filter lost its property to marker "
                           f"stripping: {phrase!r}")
    lowered = phrase.lower()
    color = _COLOR_SYMBOL.search(phrase)
    at_most = _LEVEL_AT_MOST.search(phrase)
    at_least = _LEVEL_AT_LEAST.search(phrase)
    exact = None
    if not at_most and not at_least:
        found = _LEVEL_EXACT.search(phrase)
        exact = int(found.group(1)) if found else None
    kw = _KEYWORD.search(phrase)
    named = _NAMED.search(phrase)

    is_flip = None
    if "that has flip" in lowered or "that have flip" in lowered:
        is_flip = True
    elif "does not have flip" in lowered:
        is_flip = False

    is_cookie = True if "cookie" in lowered else None
    return CardFilter(
        color=_color(color.group(1)) if color else None,
        exact_level=exact,
        max_level=int(at_most.group(1)) if at_most else None,
        min_level=int(at_least.group(1)) if at_least else None,
        keyword=Keyword[kw.group(1).upper()] if kw else None,
        name=named.group(1) if named else None,
        is_cookie=is_cookie,
        is_flip=is_flip,
    )


@verb(r"^return up to (\d+) (.*?) from your trash to your hand\.?$")
def _v_trash_to_hand(m) -> list[Op]:
    return [MoveCards(ZONE_TRASH, ZONE_HAND, int(m.group(1)),
                      parse_card_filter(m.group(2)))]


@verb(r"^select up to (\d+) (.*?) from your break area and place (?:it|them) "
      r"in the trash\.?$")
def _v_break_to_trash(m) -> list[Op]:
    return [MoveCards(ZONE_BREAK, ZONE_TRASH, int(m.group(1)),
                      parse_card_filter(m.group(2)))]


@verb(r"^place (?:up to )?(\d+) (.*?) from your trash into your break area\.?$")
def _v_trash_to_break(m) -> list[Op]:
    return [MoveCards(ZONE_TRASH, ZONE_BREAK, int(m.group(1)),
                      parse_card_filter(m.group(2)))]


@verb(r"^place this cookie on the (top|bottom) of (?:your|the) deck\.?$")
def _v_self_to_deck(m) -> list[Op]:
    zone = ZONE_DECK_TOP if m.group(1).lower() == "top" else ZONE_DECK_BOTTOM
    return [SelfToDeck(zone)]


@verb(r"^place up to (\d+) of your opponent's (.*?) from their battle area on "
      r"the (top|bottom) of (?:the|their) deck\.?$")
def _v_bounce_to_deck(m) -> list[Op]:
    zone = ZONE_DECK_TOP if m.group(3).lower() == "top" else ZONE_DECK_BOTTOM
    return [MoveCards(ZONE_BATTLE, zone, int(m.group(1)),
                      parse_card_filter(m.group(2)), from_opponent=True)]


@verb(r"^deals? (\d+) damage\.?$")
def _v_deals_damage(m) -> list[Op]:
    """A second attack line's damage, applied to whatever was selected."""
    return [Damage(int(m.group(1)))]


@verb(r"^you can draw(?: up to)? (\d+) cards? from your deck\.?$")
def _v_may_draw(m) -> list[Op]:
    return [Draw(int(m.group(1)))]


@verb(r"^place up to (\d+) cards? from your hand into your support area as rested\.?$")
def _v_support_from_hand(m) -> list[Op]:
    return [SupportFromHand(int(m.group(1)))]


@verb(r"^place up to (\d+) cards? from your hand into your support area as active\.?$")
def _v_support_from_hand_active(m) -> list[Op]:
    return [SupportFromHand(int(m.group(1)), rested=False)]


# --- generic zone-to-zone movement ----------------------------------------
# Most remaining clauses share one skeleton: "<verb> N <filter> from <zone>
# to <zone>". Parsing that shape generically beats writing a rule per
# phrasing, and it keeps the filter and both zones explicit.

_ZONE_PATTERNS = [
    (re.compile(r"the top of (?P<who>[\w' ]*?)\s*deck", re.I), ZONE_DECK_TOP),
    (re.compile(r"the bottom of (?P<who>[\w' ]*?)\s*deck", re.I), ZONE_DECK_BOTTOM),
    (re.compile(r"(?P<who>[\w' ]*?)\s*battle area", re.I), ZONE_BATTLE),
    (re.compile(r"(?P<who>[\w' ]*?)\s*break area", re.I), ZONE_BREAK),
    (re.compile(r"(?P<who>[\w' ]*?)\s*support area", re.I), ZONE_SUPPORT),
    (re.compile(r"(?P<who>[\w' ]*?)\s*trash", re.I), ZONE_TRASH),
    (re.compile(r"(?P<who>[\w' ]*?)\s*hand", re.I), ZONE_HAND),
    (re.compile(r"(?P<who>[\w' ]*?)\s*deck", re.I), ZONE_DECK_TOP),
]


def _parse_zone(phrase: str):
    """"your opponent's break area" -> (ZONE_BREAK, from_opponent=True)."""
    for pattern, zone in _ZONE_PATTERNS:
        match = pattern.search(phrase or "")
        if match:
            who = (match.groupdict().get("who") or "").lower()
            return zone, ("opponent" in who or "their" in who)
    return None, False


_MOVE_CLAUSE = re.compile(
    r"^(?:place|put|return|move|add|select|choose|play)\s+(?:up to\s+)?"
    r"(?P<n>\d+|a|an|one|all)\s+(?P<what>.*?)\s+"
    r"from\s+(?P<src>.+?)\s+(?:in)?to\s+(?P<dst>.+?)\.?$", re.I)


_SUPPORT_STATE = re.compile(r"\bas (active|rested)\b", re.I)


def _generic_move(phrase: str) -> list[Op] | None:
    match = _MOVE_CLAUSE.match(phrase)
    if not match:
        return None
    source, src_opp = _parse_zone(match.group("src"))
    destination, dst_opp = _parse_zone(match.group("dst"))
    if source is None or destination is None or source == destination:
        return None
    # Cards only ever move within one player's zones in this grammar; a
    # cross-player move is a different (rarer) effect and is left uncompiled.
    if src_opp != dst_opp:
        return None
    if source in (ZONE_DECK_TOP, ZONE_DECK_BOTTOM):
        return None                      # deck-as-source needs ordering rules
    count = 4 if match.group("n").lower() == "all" else _number(match.group("n"))
    what = match.group("what")

    if source == ZONE_BATTLE:
        scope = SCOPE_OPPONENT if src_opp else SCOPE_OWN
        ops: list[Op] = [Select(scope, count=count, filter=parse_filter(what),
                                ref="moved")]
        if destination == ZONE_HAND:
            ops.append(ReturnToHand(ref="moved"))
        elif destination == ZONE_TRASH:
            ops.append(TrashCookies(ref="moved"))
        elif destination == ZONE_BREAK:
            ops.append(Faint(ref="moved"))
        elif destination in (ZONE_DECK_TOP, ZONE_DECK_BOTTOM):
            ops.append(MoveSelectedToDeck(ref="moved",
                                          bottom=destination == ZONE_DECK_BOTTOM))
        else:
            return None
        return ops

    if destination == ZONE_BATTLE:
        filt = parse_card_filter(what)
        if source == ZONE_TRASH:
            return [PlayFromTrash(filt)]
        if source == ZONE_BREAK:
            return [PlayFromBreak(filt)]
        if source == ZONE_SUPPORT:
            return [PlayFromSupport(filt)]
        return None

    # "... into your support area as active" — the trailing state rides on the
    # destination phrase, and dropping it would silently rest a card the text
    # says arrives ready to spend.
    rested = True
    if destination == ZONE_SUPPORT:
        state = _SUPPORT_STATE.search(match.group("dst"))
        if state is None:
            return None      # unstated: refuse rather than guess active/rested
        rested = state.group(1).lower() == "rested"
    return [MoveCards(source, destination, count, parse_card_filter(what),
                      from_opponent=src_opp, rested=rested)]


def parse_verb(phrase: str) -> list[Op]:
    phrase = phrase.strip()
    for pattern, build in _VERB_RULES:
        match = pattern.match(phrase)
        if match:
            return build(match)
    generic = _generic_move(phrase)
    if generic is not None:
        return generic
    raise CompileError(f"verb: {phrase!r}")


# ---------------------------------------------------------------------------
# clause assembly
# ---------------------------------------------------------------------------

_LEADING_CONNECTIVE = re.compile(r"^(?:then|also|after that)\s*,?\s*", re.I)
_IF_PREFIX = re.compile(r"^if\s+(.*?),\s*(.+)$", re.I)
# "During this turn" is a timing phrase, not a condition, and it turns up in
# the middle of a guard as often as at the front: "If A and, during this turn,
# B, do X." Left alone it ends the guard at the first comma and the rest of the
# condition is read as the verb — so the connector is normalised back to the
# plain "and if" / "or if" the guard loop already understands.
_MID_GUARD_TIMING = re.compile(
    r"\s+(and|or)(?:\s+if)?,\s*during this turn,\s*", re.I)
# "During this turn, if X, do Y." — only strip the timing phrase when a guard
# follows it. "During this turn, that Cookie deals -2 attack damage." is a verb
# in its own right and must keep its prefix.
_DURING_TURN_IF = re.compile(r"^during this turn,\s*(?=if\s)", re.I)
_FAINT_PREFIX = re.compile(r"When this Cookie faints,?\s*", re.I)
# Other trigger prefixes the text carries inline instead of as a 【marker】.
_TRIGGER_PREFIXES = [
    (re.compile(r"When this Cookie is played from (?:the|your) trash,?\s*", re.I),
     "played_from_trash"),
    (re.compile(r"When this Cookie is played from (?:the|your) support area,?\s*",
                re.I), "played_from_support"),
    (re.compile(r"When your turn ends,?\s*", re.I), "end_turn"),
    (re.compile(r"When this Cookie is placed from your hand into your trash"
                r"(?: by the effect of[^,]*)?,?\s*", re.I), "trashed"),
    (re.compile(r"When this Cookie is played from (?:the|your) break area,?\s*",
                re.I), "played_from_break"),
    (re.compile(r"When this Cookie attacks,?\s*", re.I), "attack_start"),
    (re.compile(r"When your opponent attacks this Cookie,?\s*", re.I),
     "when_attacked"),
    (re.compile(r"If this Cookie remains in the battle area after receiving "
                r"damage,?\s*", re.I), "survived_damage"),
]


def compile_clause(text: str) -> Clause:
    ops: list[Op] = []
    body = _LEADING_CONNECTIVE.sub("", text.strip())

    # Costs may appear anywhere in the clause; pull them out in order.
    cost_tokens = _COST_TOKEN.findall(body)
    for token in cost_tokens:
        ops.extend(parse_cost(token))
    body = _COST_TOKEN.sub(" ", body)
    body = _LEADING_CONNECTIVE.sub("", body.strip())

    # Guards can stack: "If A, if B, do X."
    conditions = []
    body = _MID_GUARD_TIMING.sub(lambda m: f" {m.group(1).lower()} if ", body)
    while True:
        body = _DURING_TURN_IF.sub("", body)
        match = _IF_PREFIX.match(body)
        if not match:
            break
        head, rest = match.group(1), match.group(2)
        for piece in re.split(r"\s+and if\s+|\s+and\s+(?=there )", head):
            # "A or if B" is a choice, not a second requirement: either half
            # opens the card. `AnyOf` keeps that inside one Guard entry so the
            # rest of the pipeline still sees a flat list of conditions to
            # satisfy.
            alternatives = re.split(r"\s+or if\s+", piece)
            if len(alternatives) > 1:
                conditions.append(AnyOf(tuple(parse_condition(a)
                                              for a in alternatives)))
            else:
                conditions.append(parse_condition(piece))
        body = _LEADING_CONNECTIVE.sub("", rest.strip())
    if conditions:
        ops.insert(0, Guard(tuple(conditions)))

    # Multiple verbs can share a sentence, joined by "Then,". Protect "LV.2"
    # so the level notation is not mistaken for a sentence end.
    for piece in re.split(r"\.\s*", _protect(body)):
        piece = _LEADING_CONNECTIVE.sub("", _restore(piece).strip())
        if not piece:
            continue
        ops.extend(parse_verb(piece + "."))

    if not ops:
        raise CompileError(f"empty clause: {text!r}")
    # Only costs that actually compiled to something are worth asking about;
    # `<... can be used as {B}.>` and friends parse to no ops at all.
    payable = [t.strip() for t in cost_tokens if parse_cost(t)]
    return Clause(ops, cost_text="; ".join(payable))


# "Select 1 of the following." and the bullet that starts each branch. The
# bullets survive `split_clauses` as clauses of their own, but a branch is as
# long as the card prints it — every sentence up to the next bullet belongs to
# the option above it — so the grouping happens here, over the whole effect,
# rather than one clause at a time.
_MODAL_LEAD = re.compile(r"(?:then,?\s*)?select 1 of the following\.?\s*$", re.I)
_MODAL_BULLET = "\u30fb"


def _modal_branches(chunks: list[str]) -> tuple:
    """Group the clauses after a modal lead into (label, clauses) branches."""
    branches: list = []
    for chunk in chunks:
        body = chunk.strip()
        if body.startswith(_MODAL_BULLET):
            branches.append([body.lstrip(_MODAL_BULLET).strip()])
        elif branches:
            branches[-1].append(body)
        else:
            # Text between "Select 1 of the following." and the first bullet.
            # Nothing prints that, and guessing which option it belongs to
            # would be inventing a card.
            raise CompileError(f"modal: stray clause {chunk!r}")
    if len(branches) < 2:
        raise CompileError("modal: fewer than two options")
    return tuple((" ".join(parts), tuple(compile_clause(p) for p in parts))
                 for parts in branches)


def compile_text(text: str) -> Program:
    """Compile a whole effect. Raises :class:`CompileError` on any clause."""
    clauses = []
    chunks = split_clauses(text)
    for index, chunk in enumerate(chunks):
        if _MODAL_LEAD.search(chunk):
            # Whatever came before "Select 1 of the following." on that line is
            # the card's own cost, and is charged once, before the choice.
            head = _MODAL_LEAD.sub("", chunk).strip()
            if head:
                clauses.append(compile_clause(head))
            clauses.append(Clause(ops=[Modal(_modal_branches(chunks[index + 1:]))]))
            break
        clauses.append(compile_clause(chunk))
    if not clauses:
        raise CompileError("no clauses")
    return Program(clauses, source=text)


# ---------------------------------------------------------------------------
# card-level compilation
# ---------------------------------------------------------------------------

_PLACEMENT_ONLY = re.compile(
    r"^\s*(?:<[^>]*>\s*)*place in your stage area\.?\s*$", re.I)

# The energy cost an ITEM/TRAP prints at the head of its text. `cards.py` reads
# the same run of symbols into `play_cost`, which the engine charges when the
# card is played, so the compiled body must not pay it a second time. Only an
# all-symbol cost is stripped — a leading `<Discard 1 card.>` is a real op.
_LEAD_ENERGY_COST = re.compile(r"^\s*<(?:\{[A-Za-z]+\})+>\s*")


def _is_vanilla_stage(card: CardDef) -> bool:
    """A stage card printing only its placement line has nothing to compile.

    The engine already handles placing and replacing stages, so such a card is
    fully playable with zero effects — as opposed to a card whose text simply
    failed to route anywhere, which must stay unimplemented.
    """
    return (card.type is CardType.STAGE
            and bool(_PLACEMENT_ONLY.match(card.description or ""))
            and not (card.flip_text or "").strip()
            and not (card.attack.text if card.attack else "").strip())


@dataclass
class CardCompilation:
    card_id: str
    programs: dict          # Trigger -> Program
    failures: list          # (trigger, clause, reason)
    vanilla: bool = False   # understood in full, but has no effects to run

    @property
    def ok(self) -> bool:
        if self.failures:
            return False
        return bool(self.programs) or self.vanilla


def _trigger_texts(card: CardDef) -> list[tuple[Trigger, str]]:
    """Split a card's printed text into the triggers it belongs to."""
    out: list[tuple[Trigger, str]] = []

    description = card.description or ""
    if description.strip():
        if card.type is CardType.STAGE:
            # Stage cards print placement, then an 【Activate】 block.
            head, _, tail = description.partition("【Activate】")
            if tail.strip():
                out.append((Trigger.STAGE_ACTIVATE, tail))
            elif "When your turn ends" in description:
                head, _, tail = description.partition("When your turn ends")
                out.append((Trigger.END_TURN, tail.lstrip(", ")))
        elif card.type in (CardType.ITEM, CardType.TRAP):
            # Without the strip the lead cost is charged twice: once by the
            # engine as `play_cost`, once by the body's own PayCost. The
            # hand-written items drop it from their docstrings for this reason.
            out.append((Trigger.ITEM, _LEAD_ENERGY_COST.sub("", description, count=1)))
        elif "【On Play】" in description:
            out.append((Trigger.ON_PLAY, description))
        elif "【Activate】" in description:
            out.append((Trigger.ACTIVATE, description))
        elif any(p.search(description) for p, _ in _TRIGGER_PREFIXES):
            for pattern, name in _TRIGGER_PREFIXES:
                if pattern.search(description):
                    out.append((Trigger(name), pattern.sub("", description)))
                    break
        elif _FAINT_PREFIX.search(description):
            # The trigger is carried by the registry key, so the prefix itself
            # is not part of the effect body.
            out.append((Trigger.FAINT, _FAINT_PREFIX.sub("", description)))

    if card.flip_text and card.flip_text.strip():
        out.append((Trigger.FLIP, card.flip_text))

    if card.attack and card.attack.text.strip():
        out.append((Trigger.ATTACK, card.attack.text))

    return out


def compile_card(card: CardDef) -> CardCompilation:
    programs: dict = {}
    failures: list = []
    for trigger, text in _trigger_texts(card):
        try:
            programs[trigger] = compile_text(text)
        except CompileError as exc:
            failures.append((trigger, text, str(exc)))
    return CardCompilation(card.id, programs, failures,
                           vanilla=_is_vanilla_stage(card))


def compile_all(db: CardDB, *, register: bool = True,
                skip: set | None = None) -> dict:
    """Compile the whole pool.

    Hand-written implementations always win: a card already in the effect
    registry is skipped entirely, so the compiler can never silently override
    a card someone verified by hand.
    """
    from .effects import _REGISTRY, STATIC_ABILITY_CARDS, get_effect

    skip = skip or set()
    results: dict[str, CardCompilation] = {}
    registered = 0

    for card in db.cards.values():
        if card.id in skip or card.base_id in skip:
            continue
        if any(get_effect(card.id, t) for t in Trigger):
            continue           # hand-written; leave it alone
        result = compile_card(card)
        results[card.id] = result
        if register and result.ok:
            for trigger, program in result.programs.items():
                _REGISTRY[(card.base_id, trigger)] = program
            if result.vanilla and not result.programs:
                # Nothing to register, but the card is understood in full.
                STATIC_ABILITY_CARDS.add(card.base_id)
            registered += 1

    results["__registered__"] = registered  # type: ignore[assignment]
    return results
