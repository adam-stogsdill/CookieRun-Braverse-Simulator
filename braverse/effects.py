"""Card effects: the trigger registry and the primitives effects are written in.

An effect is a plain function taking a :class:`Ctx`. Card text maps onto the
primitives almost sentence-for-sentence, which keeps hand-written cards short
and gives a future text→effect compiler an obvious target to emit against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol, Sequence

from .cards import CardDB
from .cost import Cost, plan_payment
from .enums import Color
from .state import Cookie, CardInstance, GameState, PlayerState


class Trigger(str, Enum):
    ON_PLAY = "on_play"          # 【On Play】
    ACTIVATE = "activate"        # 【Activate】 skill, main phase
    ATTACK = "attack"            # the "Then, ..." rider on an attack line
    FLIP = "flip"                # revealed from an HP pile
    FAINT = "faint"              # "When this Cookie faints, ..."
    ITEM = "item"                # ITEM / TRAP body
    STAGE_ACTIVATE = "stage_activate"
    END_TURN = "end_turn"
    # "When this Cookie is placed from your battle area into your trash" —
    # distinct from FAINT, which only fires on reaching 0 HP.
    TRASHED = "trashed"
    # Static 【Your Turn】 buffs, evaluated as an attack is declared.
    ATTACK_START = "attack_start"
    # Fired on the defending Cookie once an attack has been declared at it.
    WHEN_ATTACKED = "when_attacked"
    # "If this Cookie remains in the battle area after receiving damage, ..."
    SURVIVED_DAMAGE = "survived_damage"
    # "When this Cookie is played from the trash, ..." — recursion payoffs.
    PLAYED_FROM_TRASH = "played_from_trash"
    # "When this Cookie is played from the support area, ..."
    PLAYED_FROM_SUPPORT = "played_from_support"
    # "When this Cookie is played from the break area, ..."
    PLAYED_FROM_BREAK = "played_from_break"


# Cards whose mere presence on the field forbids the *opponent's* effects from
# moving Cookies out of a battle area. The engine has no general static-ability
# layer, so continuous locks register their card id here and the primitives
# that move Cookies consult it.
# Cards whose whole ability is continuous — a lock, a stat aura, a cost
# rewrite — and so has no trigger to register. They are implemented, just not
# as events, and `is_implemented` has to know that.
STATIC_ABILITY_CARDS: set[str] = set()

MOVEMENT_LOCK_CARDS: set[str] = set()

# "your opponent's Cookies can only attack this Cookie". Each entry takes
# (db, defender) and returns the Cookie that must be attacked, or None. The
# engine consults these when it builds the list of legal attacks.
TAUNT_PROVIDERS: list = []

# "this Cookie cannot attack" prohibitions. Each entry takes (db, cookie) and
# returns True to forbid that Cookie from declaring an attack.
ATTACK_PROHIBITIONS: list = []

# "your Cookies take no damage from your opponent" — blanket protection while a
# card is on the field. Each entry takes (db, owner) and returns True to shield
# that player's Cookies from all opposing damage.
OPPONENT_DAMAGE_SHIELDS: list = []


def shields_from_opponent(db, owner) -> bool:
    return any(rule(db, owner) for rule in OPPONENT_DAMAGE_SHIELDS)

# 【EXTRA】 "Can be played if ..." gates. Each entry takes
# (db, player, opponent, card_def) and returns False to forbid playing that
# card right now. The opponent is passed because several gates compare zones.
PLAY_CONDITIONS: list = []


def may_play(db, player, opponent, defn) -> bool:
    return all(rule(db, player, opponent, defn) for rule in PLAY_CONDITIONS)

# "this Cookie cannot be moved from the battle area by your opponent's
# effects". Each entry takes (db, owner, cookie) and returns True while that
# Cookie is protected — narrower than MOVEMENT_LOCK_CARDS, which locks a whole
# player. The owner is passed because these conditions read its zones.
MOVEMENT_PROTECTORS: list = []


def cannot_attack(db, cookie) -> bool:
    return any(rule(db, cookie) for rule in ATTACK_PROHIBITIONS)


def is_move_protected(db, owner, cookie) -> bool:
    return any(rule(db, owner, cookie) for rule in MOVEMENT_PROTECTORS)


def forced_attack_target(db, defender):
    for provider in TAUNT_PROVIDERS:
        forced = provider(db, defender)
        if forced is not None:
            return forced
    return None

# Continuous "+N effect damage" abilities. Each entry is called with the Ctx and
# the Cookie dealing the damage, and returns the bonus it grants. Registering a
# callable keeps the card-specific condition inside the card module instead of
# leaking into the engine.
EFFECT_DAMAGE_BONUSES: list = []

# Continuous attack-cost rewrites, e.g. "each cost required for this Cookie's
# attack becomes {N}". Each entry takes (db, player, cookie, cost) and returns
# a replacement Cost, or None to leave it alone.
ATTACK_COST_MODIFIERS: list = []


def modified_attack_cost(db, player, cookie, cost):
    for modifier in ATTACK_COST_MODIFIERS:
        replacement = modifier(db, player, cookie, cost)
        if replacement is not None:
            cost = replacement
            break
    surcharge = getattr(cookie, "attack_cost_surcharge", 0)
    if surcharge:
        from .cost import Cost
        cost = Cost(cost.colored, cost.generic + surcharge)
    return cost

EffectFn = Callable[["Ctx"], None]
_REGISTRY: dict[tuple[str, Trigger], EffectFn] = {}


def effect(card_id: str, trigger: Trigger) -> Callable[[EffectFn], EffectFn]:
    """Register the implementation of one card's trigger."""

    def wrap(fn: EffectFn) -> EffectFn:
        key = (card_id, trigger)
        if key in _REGISTRY:
            raise ValueError(f"duplicate effect for {key}")
        _REGISTRY[key] = fn
        return fn

    return wrap


def get_effect(card_id: str, trigger: Trigger) -> EffectFn | None:
    return _REGISTRY.get((card_id.split("@")[0], trigger))


def implemented_cards() -> set[str]:
    return ({card_id for card_id, _ in _REGISTRY}
            | set(MOVEMENT_LOCK_CARDS) | set(STATIC_ABILITY_CARDS))


def is_implemented(card_id: str) -> bool:
    """Whether the engine plays this card's text in full.

    A purely continuous ability has no trigger to register, so the static
    registries count too — those cards are implemented, just not as events.
    """
    base = card_id.split("@")[0]
    if base in MOVEMENT_LOCK_CARDS or base in STATIC_ABILITY_CARDS:
        return True
    return any((base, trigger) in _REGISTRY for trigger in Trigger)


def ask_many(controller, state, prompt: str, options: Sequence, count: int,
             *, optional: bool = False) -> list:
    """Pick ``count`` of ``options``, in one question if the controller allows.

    Scripted agents answer one card at a time, which is the natural shape for a
    greedy heuristic and keeps their behaviour bit-identical. A human wants the
    opposite: see the whole hand, toggle the cards, confirm once. A controller
    that implements ``choose_many`` gets asked that way; everyone else is looped
    over exactly as before.
    """
    picker = getattr(controller, "choose_many", None)
    pool = list(options)
    if picker is not None:
        picked = picker(state, prompt, pool, count=count, optional=optional) or []
        # Take each pick *out* of the pool as it is accepted, so the same card
        # named twice is only discarded once. A short, padded or repeated answer
        # still has to leave exactly `count` cards discarded and a legal state.
        chosen: list = []
        for card in picked:
            if len(chosen) >= count:
                break
            if card in pool:
                pool.remove(card)
                chosen.append(card)
        while len(chosen) < count and pool:
            chosen.append(pool.pop(0))
        return chosen

    chosen = []
    for _ in range(count):
        if not pool:
            break
        card = controller.choose(state, prompt, pool, optional=False) or pool[0]
        pool.remove(card)
        chosen.append(card)
    return chosen


class Controller(Protocol):
    """A player brain. Used both for turn actions and mid-effect decisions."""

    def choose_action(self, state: GameState, actions: Sequence) -> object: ...

    def choose(
        self, state: GameState, prompt: str, options: Sequence, *, optional: bool
    ) -> object | None:
        """Pick one option (or ``None`` when ``optional``)."""


# Triggers the controller opted into by choosing an action; a <...> cost on
# one of these is paid without asking, because taking the action was the choice.
PLAYER_CHOSEN_TRIGGERS = frozenset({"activate", "stage_activate", "item"})


@dataclass
class Ctx:
    """Everything an effect needs, plus the verbs it is written in."""

    game: "object"                 # braverse.engine.Game — avoids a circular import
    state: GameState
    db: CardDB
    me: PlayerState
    opp: PlayerState
    source_cookie: Cookie | None = None
    source_card: CardInstance | None = None
    # The Cookie this attack is aimed at, so riders can ask about it.
    attack_target: Cookie | None = None
    # The Cookie declaring the attack, for defensive reactions.
    attacker: Cookie | None = None
    # Cards a Reveal exposed, for "if that card is ..." conditions.
    revealed: list = field(default_factory=list)
    # Which trigger is running, so an effect can tell whether its controller
    # asked for it (an 【Activate】 skill) or it simply happened to them (a FLIP
    # turning over mid-attack). Only the latter needs to be asked about costs.
    trigger: str = ""
    # Set while a clause whose cost has already been agreed to is running, so
    # the ops inside it do not ask about the same cost twice.
    cost_approved: bool = False

    fizzled: bool = False          # a mandatory sub-cost could not be paid
    notes: list[str] = field(default_factory=list)

    # -- queries ---------------------------------------------------------
    @property
    def hand_size(self) -> int:
        return len(self.me.hand)

    def name_in_battle(self, name: str, *, mine: bool = True) -> bool:
        player = self.me if mine else self.opp
        return any(c.name(self.db) == name for c in player.battle)

    def name_in_support(self, name: str, *, mine: bool = True) -> bool:
        player = self.me if mine else self.opp
        return any(self.db[c.card_id].name == name for c in player.support)

    def count_in_trash(self, predicate) -> int:
        return sum(1 for c in self.me.trash if predicate(self.db[c.card_id]))

    def active_support_count(self, *, mine: bool = True) -> int:
        player = self.me if mine else self.opp
        return len(player.active_support())

    def support_count(self, *, mine: bool = True) -> int:
        return len((self.me if mine else self.opp).support)

    # -- card movement ---------------------------------------------------
    def draw(self, n: int, *, up_to: bool = True) -> int:
        """Draw ``n``. "draw up to N" never loses the game on an empty deck."""
        return self.game.draw(self.me, n, optional=up_to)

    def discard(self, n: int, *, optional: bool = False) -> list[CardInstance]:
        """Discard ``n`` cards of the controller's choosing.

        Returns the discarded cards, so a caller can react to *what* went to
        the trash. Empty (falsy) when the cost could not be paid — or, for the
        ``optional=True`` form that spells a printed ``<Discard N cards.>``
        cost, when the controller declined to pay it.
        """
        if optional and not self.wants_to_pay(f"Discard {n} card{'' if n == 1 else 's'}."):
            return []
        return self.game.discard(self.me, n, self, optional=optional)

    def discard_colored(self, n: int, color: Color) -> list[CardInstance]:
        return self.game.discard(self.me, n, self, color=color)

    def mill_to_support(self, n: int, *, rested: bool = True) -> int:
        moved = 0
        for _ in range(n):
            if not self.me.deck:
                break
            card = self.me.deck.pop(0)
            card.rested = rested
            self.me.support.append(card)
            moved += 1
        return moved

    def return_support_to_hand(self, *, predicate=None) -> bool:
        options = [c for c in self.me.support
                   if predicate is None or predicate(self.db[c.card_id])]
        if not options:
            return False
        card = self.choose("Return a support card to hand", options, optional=True)
        if card is None:
            return False
        self.me.support.remove(card)
        card.rested = False
        self.me.hand.append(card)
        return True

    # -- support area ----------------------------------------------------
    def rest_support(self, n: int, *, mine: bool = True) -> int:
        player = self.me if mine else self.opp
        rested = 0
        for index in player.active_support():
            if rested >= n:
                break
            player.support[index].rested = True
            rested += 1
        return rested

    def set_support_active(self, n: int, *, mine: bool = True) -> int:
        player = self.me if mine else self.opp
        count = 0
        for card in player.support:
            if count >= n:
                break
            if card.rested:
                card.rested = False
                count += 1
        return count

    # -- combat ----------------------------------------------------------
    def enemy_cookies(self, predicate=None) -> list[Cookie]:
        return [c for c in self.opp.battle
                if predicate is None or predicate(c)]

    def own_cookies(self, predicate=None) -> list[Cookie]:
        return [c for c in self.me.battle
                if predicate is None or predicate(c)]

    def select_enemy(self, predicate=None, prompt="Select an opponent's Cookie") -> Cookie | None:
        options = self.enemy_cookies(predicate)
        return self.choose(prompt, options, optional=True) if options else None

    def select_own(self, predicate=None, prompt="Select one of your Cookies") -> Cookie | None:
        options = self.own_cookies(predicate)
        return self.choose(prompt, options, optional=True) if options else None

    def deal_damage(self, cookie: Cookie, amount: int) -> None:
        """Damage from a card effect, as opposed to an attack.

        Some Cookies are immune to effect damage specifically, so this is the
        one place that distinction can be enforced.
        """
        if getattr(cookie, "effect_damage_immune", False):
            return
        for bonus in EFFECT_DAMAGE_BONUSES:
            amount += bonus(self, self.source_cookie)
        amount -= getattr(cookie, "effect_damage_reduction", 0)
        if amount <= 0:
            return
        # "if your 【Arena】 Cookie has dealt effect damage" reads this.
        self.me.effect_damage_dealt_this_turn = True
        if self.source_cookie is not None:
            from .enums import Keyword
            if Keyword.ARENA in self.source_cookie.defn(self.db).keywords:
                self.me.arena_effect_damage_this_turn = True
        self.game.deal_damage(cookie, amount, source_player=self.me.index)

    def gain_hp(self, cookie: Cookie, amount: int) -> None:
        owner = self.state.players[cookie.owner]
        if owner.hp_gain_locked:
            return
        self.game.gain_hp(cookie, amount)

    def modify_attack(self, cookie: Cookie, delta: int) -> None:
        cookie.attack_bonus += delta

    @property
    def movement_locked(self) -> bool:
        """True when an opposing card forbids my effects from moving Cookies."""
        return any(c.defn(self.db).base_id in MOVEMENT_LOCK_CARDS
                   for c in self.opp.battle)

    def _may_move(self, cookie: Cookie) -> bool:
        """Whether my effect is allowed to move this Cookie off the field."""
        if self.movement_locked:
            return False
        # A Cookie can always be moved by its own controller's effects.
        if cookie.owner == self.me.index:
            return True
        owner = self.state.players[cookie.owner]
        return not is_move_protected(self.db, owner, cookie)

    def return_self_to_hand(self) -> bool:
        """A FLIP returning *itself* to hand.

        "Return this Cookie to your hand" on a FLIP means the card that was
        just revealed, not the Cookie it was serving as HP for: whenever the
        pool means the host it says so at length — "the Cookie with this card
        attached for HP" — on all 92 cards that do. Damage has already put the
        revealed card in the trash by the time this runs, so it comes back out.
        """
        card = self.source_card
        if card is None or card not in self.me.trash:
            return False
        self.me.trash.remove(card)
        self.me.hand.append(card)
        self.state.record(f"{self.db[card.card_id].name} returns to hand")
        return True

    def return_to_hand(self, cookie: Cookie) -> None:
        if not self._may_move(cookie):
            return
        self.game.return_cookie_to_hand(cookie)

    def trash_cookie(self, cookie: Cookie) -> None:
        """Remove a Cookie to the trash *without* it fainting.

        Cards word this as "place into the trash" rather than "make faint", and
        the difference is the whole game: a trashed Cookie never reaches the
        break area, so its owner's opponent banks no Level for it.
        """
        if not self._may_move(cookie):
            return
        self.game.trash_cookie(cookie)

    def opponent_discards(self, n: int = 1) -> int:
        """"Your opponent must place N cards from their hand into the trash."""
        discarded = 0
        for _ in range(n):
            if not self.opp.hand:
                break
            card = self.game.controller(self.opp.index).choose(
                self.state, "Discard a card", list(self.opp.hand), optional=False
            ) or self.opp.hand[0]
            self.opp.hand.remove(card)
            self.opp.trash.append(card)
            discarded += 1
        return discarded

    def skip_next_active(self, cookie: Cookie) -> None:
        """"not set as active during your opponent's next Active Phase"."""
        cookie.skip_next_active = True

    def play_cookie_from_trash(self, predicate=None) -> bool:
        """"Play up to 1 Cookie ... from your trash."""
        if len(self.me.battle) >= self.game.rules.max_battle_cookies:
            return False
        options = [c for c in self.me.trash
                   if self.db[c.card_id].is_cookie
                   and (predicate is None or predicate(self.db[c.card_id]))]
        if not options:
            return False
        card = self.choose("Play a Cookie from your trash", options, optional=True)
        if card is None:
            return False
        self.me.trash.remove(card)
        self.game._deploy_cookie(self.me, card, from_zone="trash")
        return True

    def faint(self, cookie: Cookie) -> None:
        """"Make that Cookie faint." — bypasses damage entirely."""
        cookie.hp_cards.clear()
        self.game.faint(cookie)

    def trash_hp(self, cookie: Cookie, amount: int, *,
                 opponent_trash: bool = False) -> None:
        """Place cards from the top of a Cookie's HP into a trash.

        This is not damage: FLIP effects do not fire, which is exactly why
        cards word it this way.
        """
        owner = self.state.players[cookie.owner]
        destination = self.state.opponent_of(cookie.owner) if opponent_trash else owner
        for _ in range(amount):
            if not cookie.hp_cards:
                break
            card = cookie.hp_cards.pop()
            card.face_up = True
            destination.trash.append(card)
        if not cookie.hp_cards:
            self.game.faint(cookie)

    def trash_stage(self, amount: int = 1, *, mine: bool = False) -> None:
        player = self.me if mine else self.opp
        for _ in range(amount):
            if not player.stage:
                break
            player.trash.append(player.stage.pop())

    def mill_deck(self, amount: int) -> None:
        for _ in range(amount):
            if not self.me.deck:
                break
            self.me.trash.append(self.me.deck.pop(0))

    # -- costs -----------------------------------------------------------
    def pay(self, cost: Cost) -> bool:
        """Pay an additional energy cost inside an effect."""
        return self.game.pay_cost(self.me, cost)

    def can_pay(self, cost: Cost) -> bool:
        _, colors = self.me.active_support_colors(self.db)
        return plan_payment(cost, colors) is not None

    # -- decisions -------------------------------------------------------
    def choose(self, prompt: str, options: Sequence, *, optional: bool = True):
        if not options:
            return None
        if len(options) == 1 and not optional:
            return options[0]
        return self.game.controller(self.me.index).choose(
            self.state, prompt, options, optional=optional
        )

    def confirm(self, prompt: str) -> bool:
        return bool(self.choose(prompt, [True], optional=True))

    def wants_to_pay(self, cost_text: str) -> bool:
        """Decide an optional ``<...>`` cost.

        Everything between angle brackets is a cost you *may* pay: pay it and
        the effect happens, decline and nothing does. That is only a real
        decision when the effect happened *to* the controller — a FLIP turning
        over mid-attack must not quietly rest their support or bin a card from
        their hand. When they asked for the effect themselves (an 【Activate】
        skill, an Item or Trap they played) taking the action *was* the
        decision, so the cost is paid without a second prompt.
        """
        if self.cost_approved or self.trigger in PLAYER_CHOSEN_TRIGGERS:
            return True
        return self.confirm(f"Pay <{cost_text}>?")

    def note(self, message: str) -> None:
        self.notes.append(message)
        self.state.record(message)
