"""Player controllers.

Both agents satisfy the :class:`braverse.effects.Controller` protocol: they
pick turn actions and answer the small decisions effects raise mid-resolution.
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Sequence

from . import actions as A
from .cards import CardDB, default_db
from .rps import CHOICES, GO_FIRST, THROWS
from .enums import CardType, Marker
from .state import CardInstance, Cookie, GameState


class RandomAgent:
    """Uniform over legal actions. The baseline every other agent must beat."""

    def __init__(self, seed: int | None = None, name: str = "random"):
        self.rng = random.Random(seed)
        self.name = name

    def choose_action(self, state: GameState, options: Sequence[A.Action]):
        # Ending the turn immediately makes for degenerate games, so only take
        # it when it is the sole option or by luck of the draw.
        return self.rng.choice(list(options)) if options else None

    def choose(self, state: GameState, prompt: str, options: Sequence, *, optional: bool):
        if optional and self.rng.random() < 0.3:
            return None
        return self.rng.choice(list(options)) if options else None


class HeuristicAgent:
    """A solid, scripted opponent: curve out, trade efficiently, take lethal.

    Deliberately greedy and one-ply. It exists to be a usable practice partner
    and a fixed baseline to measure a search agent against later.
    """

    def __init__(self, db: CardDB | None = None, seed: int | None = None,
                 name: str = "heuristic"):
        self.db = db or default_db()
        self.rng = random.Random(seed)
        self.name = name

    # -- turn actions ----------------------------------------------------
    def choose_action(self, state: GameState, options: Sequence[A.Action]):
        if not options:
            return None
        scored = [(self._score(state, a), i, a) for i, a in enumerate(options)]
        best = max(scored, key=lambda t: (t[0], -t[1]))
        return best[2]

    def _score(self, state: GameState, action: A.Action) -> float:
        db = self.db
        me = state.players[self._seat(state)]

        if isinstance(action, A.EndTurn):
            return 0.0

        if isinstance(action, A.PlaceSupport):
            # Energy is the whole game early; keep taking it, but never bin a
            # card the board is short of.
            card = self._card(state, action.card_uid)
            return 90.0 - self._hand_value(state, me, card)

        if isinstance(action, A.PlayCookie):
            defn = db[self._card(state, action.card_uid).card_id]
            level = defn.level or 1
            if not me.battle:
                return 95.0 + (defn.hp or 0)      # never sit with an empty board
            score = 65.0 + (defn.hp or 0) * 2.0
            if defn.attack:
                score += defn.attack.damage * 2.0
            # Level is a liability: it is what the opponent banks when this
            # Cookie faints, and 10 in the break area loses the game.
            score -= level * 3.0
            if me.break_level_total(db) + level >= 10:
                score -= 200.0
            return score

        if isinstance(action, A.PlayExtra):
            # The gate has already been checked — the engine only offers an
            # EXTRA card whose condition is met — and the card costs nothing
            # from hand, so this is close to free value. An 【Awaken】 is rated
            # on the HP it adds to a Cookie already holding a slot.
            defn = db[self._card(state, action.card_uid).card_id]
            if action.onto is not None:
                return 80.0 + (defn.hp or 0) * 2.0
            if not me.battle:
                return 96.0 + (defn.hp or 0)
            score = 75.0 + (defn.hp or 0) * 2.0
            if defn.attack:
                score += defn.attack.damage * 2.0
            level = defn.level or 1
            if me.break_level_total(db) + level >= 10:
                score -= 200.0
            return score

        if isinstance(action, A.Attack):
            return self._attack_score(state, action)

        if isinstance(action, A.ActivateSkill):
            cookie = state.find_cookie(action.source_uid)
            if cookie is not None:
                # Resting a Cookie for value is bad if it still wants to swing.
                if "Rest this card" in cookie[1].defn(db).description and not cookie[1].rested:
                    return 15.0
                return 40.0
            return 35.0

        if isinstance(action, A.PlaySupportCard):
            defn = db[self._card(state, action.card_uid).card_id]
            if defn.type is CardType.STAGE:
                return 30.0 if not me.stage else 5.0
            return 45.0

        if isinstance(action, A.PlayTrap):
            return 60.0

        if isinstance(action, A.Block):
            blocker = state.find_cookie(action.blocker_uid)
            return 55.0 if blocker and blocker[1].remaining_hp >= 2 else 5.0

        if isinstance(action, A.Pass):
            return 20.0

        return 1.0

    def _attack_score(self, state: GameState, action: A.Attack) -> float:
        db = self.db
        attacker = state.find_cookie(action.attacker_uid)
        target = state.find_cookie(action.target_uid)
        if not attacker or not target:
            return 0.0
        damage = attacker[1].attack_damage(db)
        hp = target[1].remaining_hp
        score = 50.0 + damage * 3
        if damage >= hp:
            # A kill also feeds the break-area clock, which is how you win.
            score += 40.0 + target[1].level(db) * 8
            if target[0].break_level_total(db) + target[1].level(db) >= 10:
                score += 500.0  # lethal
        else:
            score -= max(0, damage - hp)  # do not overkill into a small body
        return score

    # -- mid-effect decisions -------------------------------------------
    def choose(self, state: GameState, prompt: str, options: Sequence, *, optional: bool):
        if not options:
            return None
        # The opening toss. Throw at random — a fixed throw is free to read —
        # and take the first turn, which is worth about 68% in mirror matches.
        if list(options) == list(THROWS):
            return self.rng.choice(list(THROWS))
        if list(options) == list(CHOICES):
            return GO_FIRST
        seat = self._seat(state)
        me = state.players[seat]
        lowered = prompt.lower()

        if all(isinstance(o, bool) for o in options):
            return options[0]

        if all(isinstance(o, Cookie) for o in options):
            mine = [c for c in options if c.owner == seat]
            theirs = [c for c in options if c.owner != seat]
            if "opponent" in lowered or "damage which" in lowered or "attacker" in lowered:
                pool = theirs or list(options)
                if "attacker" in lowered:
                    # Debuff whatever is hitting hardest.
                    return max(pool, key=lambda c: c.attack_damage(self.db))
                # Otherwise finish the most valuable thing we can actually kill.
                return max(pool, key=lambda c: (-c.remaining_hp, c.level(self.db)))
            pool = mine or list(options)
            if "return" in lowered:
                return min(pool, key=lambda c: (c.level(self.db), c.remaining_hp))
            return min(pool, key=lambda c: c.remaining_hp)

        if all(isinstance(o, CardInstance) for o in options):
            if "discard" in lowered:
                return max(options, key=lambda c: self._discard_priority(state, me, c))
            if "opening" in lowered or "replacement" in lowered:
                return max(options, key=lambda c: (self.db[c.card_id].hp or 0))
            return max(options, key=lambda c: self._hand_value(state, me, c))

        return options[0]

    # -- helpers ---------------------------------------------------------
    def _seat(self, state: GameState) -> int:
        """Which side we are on. Set by :class:`SeatedAgent`; during our own
        turn the turn player is the same thing anyway."""
        hint = getattr(self, "_seat_hint", None)
        return state.turn_player if hint is None else hint

    def _card(self, state: GameState, uid: int) -> CardInstance:
        found = state.find_card(uid)
        if found is None:
            raise ValueError(f"card {uid} not found")
        return found[2]

    def _hand_value(self, state: GameState, me, card: CardInstance) -> float:
        """How much we want to keep this card in hand."""
        defn = self.db[card.card_id]
        counts = Counter(self.db[c.card_id].name for c in me.hand)
        value = 0.0
        if defn.is_cookie:
            # Cookies cost nothing, so the only gate is a free battle slot —
            # and you must never run out of them, or you lose outright.
            value = 12.0 if len(me.battle) < 2 else 9.0
            if len(me.battle) <= 1:
                value += 6.0
            if defn.has(Marker.BLOCKER):
                value += 2.0
        elif defn.type is CardType.TRAP:
            value = 8.0
        elif defn.type is CardType.ITEM:
            value = 7.0
        else:
            value = 5.0
        # A fourth copy in hand is worth much less than the first.
        value -= max(0, counts[defn.name] - 1) * 2.0
        return value

    def _discard_priority(self, state: GameState, me, card: CardInstance) -> float:
        return -self._hand_value(state, me, card)


class SeatedAgent:
    """Bind an agent to a seat so its heuristics know which side it is on."""

    def __init__(self, agent, seat: int):
        self.agent = agent
        self.seat = seat
        setattr(agent, "_seat_hint", seat)
        self.name = getattr(agent, "name", "agent")

    def choose_action(self, state, options):
        return self.agent.choose_action(state, options)

    def choose(self, state, prompt, options, *, optional):
        return self.agent.choose(state, prompt, options, optional=optional)
