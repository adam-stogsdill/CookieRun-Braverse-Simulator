"""Fully-specified player actions.

Every action carries all of its targets, so ``legal_actions`` returns a flat,
enumerable move list — the shape a search agent wants. Decisions that only
arise *during* effect resolution go through the controller instead
(see :mod:`braverse.effects`).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    def describe(self, db, state) -> str:  # pragma: no cover - display only
        return type(self).__name__


@dataclass(frozen=True)
class PlaceSupport(Action):
    card_uid: int

    def describe(self, db, state):
        card = state.find_card(self.card_uid)
        return f"Support: {db[card[2].card_id].name}" if card else "Support"


@dataclass(frozen=True)
class PlayCookie(Action):
    """``onto`` is the uid of your Cookie being levelled up, or None for a new slot."""

    card_uid: int
    onto: int | None = None

    def describe(self, db, state):
        found = state.find_card(self.card_uid)
        name = db[found[2].card_id].name if found else "?"
        if self.onto is None:
            return f"Play {name}"
        target = state.find_cookie(self.onto)
        under = target[1].name(db) if target else "?"
        return f"Level up {under} → {name}"


@dataclass(frozen=True)
class PlaySupportCard(Action):
    """Play an ITEM or STAGE from hand (pays its printed cost)."""

    card_uid: int

    def describe(self, db, state):
        found = state.find_card(self.card_uid)
        return f"Play {db[found[2].card_id].name}" if found else "Play card"


@dataclass(frozen=True)
class ActivateSkill(Action):
    """Use a Cookie's or Stage card's 【Activate】 ability."""

    source_uid: int

    def describe(self, db, state):
        cookie = state.find_cookie(self.source_uid)
        if cookie:
            return f"Activate {cookie[1].name(db)}"
        found = state.find_card(self.source_uid)
        return f"Activate {db[found[2].card_id].name}" if found else "Activate"


@dataclass(frozen=True)
class Attack(Action):
    attacker_uid: int
    target_uid: int

    def describe(self, db, state):
        a = state.find_cookie(self.attacker_uid)
        t = state.find_cookie(self.target_uid)
        if not a or not t:
            return "Attack"
        return (f"Attack: {a[1].name(db)} → {t[1].name(db)} "
                f"({a[1].attack_damage(db)} dmg)")


@dataclass(frozen=True)
class PlayTrap(Action):
    """Response during the opponent's attack."""

    card_uid: int

    def describe(self, db, state):
        found = state.find_card(self.card_uid)
        return f"Trap: {db[found[2].card_id].name}" if found else "Trap"


@dataclass(frozen=True)
class Block(Action):
    """Redirect the incoming attack to one of your 【Blocker】 Cookies."""

    blocker_uid: int

    def describe(self, db, state):
        cookie = state.find_cookie(self.blocker_uid)
        return f"Block with {cookie[1].name(db)}" if cookie else "Block"


@dataclass(frozen=True)
class Pass(Action):
    """Decline the current optional window (trap/block)."""

    def describe(self, db, state):
        return "Pass"


@dataclass(frozen=True)
class EndTurn(Action):
    def describe(self, db, state):
        return "End turn"
