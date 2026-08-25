"""An intermediate representation for card effects, and its interpreter.

Card text is highly templated, so rather than hand-writing 1200 cards the
compiler in :mod:`braverse.compiler` translates rules text into these ops and
the interpreter runs them against a :class:`~braverse.effects.Ctx`. Every op
maps onto a primitive that a hand-written card would have called directly, so
compiled and hand-written cards behave identically and can coexist.
"""

from __future__ import annotations

from dataclasses import dataclass, field


from .cost import Cost
from .enums import CardType, Color, Keyword

# ---------------------------------------------------------------------------
# target selectors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Filter:
    """Restricts which Cookies a selector may pick."""

    max_level: int | None = None
    min_level: int | None = None
    exact_level: int | None = None
    max_hp: int | None = None
    min_hp: int | None = None
    color: Color | None = None
    keyword: Keyword | None = None
    name: str | None = None
    exclude_self: bool = False
    has_flip: bool | None = None

    def matches(self, cookie, ctx) -> bool:
        db = ctx.db
        defn = cookie.defn(db)
        level = defn.level or 0
        if self.exact_level is not None and level != self.exact_level:
            return False
        if self.max_level is not None and level > self.max_level:
            return False
        if self.min_level is not None and level < self.min_level:
            return False
        if self.max_hp is not None and cookie.remaining_hp > self.max_hp:
            return False
        if self.min_hp is not None and cookie.remaining_hp < self.min_hp:
            return False
        if self.color is not None and defn.color is not self.color:
            return False
        if self.keyword is not None and self.keyword not in defn.keywords:
            return False
        if self.name is not None and defn.name != self.name:
            return False
        if self.has_flip is not None and defn.is_flip != self.has_flip:
            return False
        if self.exclude_self and cookie is ctx.source_cookie:
            return False
        return True


@dataclass(frozen=True)
class CardFilter:
    """Restricts cards in a pile, matched on the printed card rather than on a
    Cookie in play — trash, break and deck piles hold cards, not Cookies."""

    color: Color | None = None
    exact_level: int | None = None
    max_level: int | None = None
    min_level: int | None = None
    keyword: Keyword | None = None
    name: str | None = None
    card_type: CardType | None = None
    is_cookie: bool | None = None
    is_flip: bool | None = None
    hp: int | None = None

    def matches(self, defn) -> bool:
        level = defn.level or 0
        if self.exact_level is not None and level != self.exact_level:
            return False
        if self.max_level is not None and level > self.max_level:
            return False
        if self.min_level is not None and level < self.min_level:
            return False
        if self.color is not None and defn.color is not self.color:
            return False
        if self.keyword is not None and self.keyword not in defn.keywords:
            return False
        if self.name is not None and defn.name != self.name:
            return False
        if self.card_type is not None and defn.type is not self.card_type:
            return False
        if self.is_cookie is not None and defn.is_cookie != self.is_cookie:
            return False
        if self.is_flip is not None and defn.is_flip != self.is_flip:
            return False
        if self.hp is not None and (defn.hp or 0) != self.hp:
            return False
        return True


# Zones a MoveCards op can read from or write to.
ZONE_TRASH = "trash"
ZONE_BREAK = "break"
ZONE_HAND = "hand"
ZONE_DECK_TOP = "deck_top"
ZONE_DECK_BOTTOM = "deck_bottom"
ZONE_SUPPORT = "support"
ZONE_BATTLE = "battle"


# Where a selector looks, and who owns it.
SCOPE_OPPONENT = "opponent"
SCOPE_OWN = "own"
SCOPE_ALL = "all"

# Named registers an op can read instead of selecting fresh.
REF_IT = "it"            # whatever the last Select bound
REF_SELF = "self"        # the Cookie whose effect this is
REF_HOST = "host"        # for FLIP: the Cookie this card was HP for


# ---------------------------------------------------------------------------
# conditions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnyOf:
    """"If A or if B, ..." — one guard entry that either half satisfies.

    A `Guard` requires all of its entries, which is what "and" means; a card
    that offers two ways in packs both into one of these instead, so nothing
    downstream has to know the difference between a requirement and a choice.
    """

    options: tuple = ()

    def holds(self, ctx) -> bool:
        return any(option.holds(ctx) for option in self.options)


@dataclass(frozen=True)
class Condition:
    kind: str
    op: str = ">="
    value: int = 0
    who: str = SCOPE_OWN
    name: str | None = None
    keyword: Keyword | None = None
    color: Color | None = None
    card_type: CardType | None = None
    # Generic zone predicates: "there is a {Y} LV.3 Cookie in your break area",
    # "there are 3 {R} cards or more in your support area". One rule shape
    # replaces dozens of near-identical hand-written ones.
    card_filter: "CardFilter | None" = None
    zone: str = "battle"
    value2: int | None = None   # second operand, e.g. a Cookie level

    def _compare(self, actual: int) -> bool:
        if self.op == ">=":
            return actual >= self.value
        if self.op == "<=":
            return actual <= self.value
        if self.op == "==":
            return actual == self.value
        if self.op == ">":
            return actual > self.value
        if self.op == "<":
            return actual < self.value
        return False

    def _count(self, ctx, player) -> int:
        """How many cards in ``self.zone`` match ``self.card_filter``."""
        db = ctx.db
        filt = self.card_filter or CardFilter()
        if self.zone == "battle":
            items = [c.defn(db) for c in player.battle]
        elif self.zone == "hand":
            items = [db[c.card_id] for c in player.hand]
        elif self.zone == "support":
            items = [db[c.card_id] for c in player.support]
        elif self.zone == "trash":
            items = [db[c.card_id] for c in player.trash]
        elif self.zone == "break":
            items = [db[c.card_id] for c in player.break_area]
        elif self.zone == "deck":
            items = [db[c.card_id] for c in player.deck]
        else:
            items = []
        return sum(1 for d in items if filt.matches(d))

    def holds(self, ctx) -> bool:
        db = ctx.db
        mine = self.who != SCOPE_OPPONENT
        player = ctx.me if mine else ctx.opp

        if self.kind == "hand_size":
            return self._compare(len(player.hand))
        if self.kind == "support_count":
            return self._compare(len(player.support))
        if self.kind == "active_support_count":
            return self._compare(len(player.active_support()))
        if self.kind == "trash_count":
            cards = player.trash
            if self.keyword is not None:
                cards = [c for c in cards if self.keyword in db[c.card_id].keywords]
            if self.color is not None:
                cards = [c for c in cards if db[c.card_id].color is self.color]
            if self.card_type is not None:
                cards = [c for c in cards if db[c.card_id].type is self.card_type]
            return self._compare(len(cards))
        if self.kind == "deck_count":
            return self._compare(len(player.deck))
        if self.kind == "break_level":
            return self._compare(player.break_level_total(db))
        if self.kind == "supports_equal":
            return len(ctx.me.support) == len(ctx.opp.support)
        if self.kind == "support_diff":
            # How many cards behind my support area is: value 0 means "same or
            # fewer", 1 means strictly fewer, N means behind by N or more.
            behind = len(ctx.opp.support) - len(ctx.me.support)
            return behind >= self.value
        if self.kind == "refreshed":
            return player.refreshed
        if self.kind == "opponent_has_level":
            return any((c.defn(db).level or 0) == self.value for c in ctx.opp.battle)
        if self.kind == "name_in_battle":
            return any(c.name(db) == self.name for c in player.battle)
        if self.kind == "name_in_support":
            return any(db[c.card_id].name == self.name for c in player.support)
        if self.kind == "keyword_in_battle":
            return any(self.keyword in c.defn(db).keywords for c in player.battle)
        if self.kind == "cookie_count":
            return self._compare(len(player.battle))
        if self.kind == "self_hp":
            cookie = ctx.source_cookie
            return cookie is not None and self._compare(cookie.remaining_hp)
        if self.kind == "attack_killed":
            return bool(getattr(ctx.game, "_attack_killed", False))
        if self.kind == "zone_count":
            return self._compare(self._count(ctx, player))
        if self.kind == "zone_has":
            # "there are no X" is the same shape read the other way, and it is
            # the one case where `value` may legitimately be 0.
            if self.op == "==" and self.value == 0:
                return self._count(ctx, player) == 0
            return self._count(ctx, player) >= max(1, self.value)
        if self.kind == "any_own_hp_equals":
            return any(self._compare(c.remaining_hp) for c in ctx.me.battle)
        if self.kind == "break_count":
            return self._compare(len(player.break_area))
        if self.kind == "color_in_battle":
            return any(c.defn(db).color is self.color for c in player.battle)
        if self.kind == "cookies_fainted":
            return self._compare(player.cookies_fainted_this_turn)
        if self.kind == "break_additions":
            return self._compare(player.break_additions_this_turn)
        if self.kind == "opponent_support_all_rested":
            return bool(ctx.opp.support) and all(c.rested for c in ctx.opp.support)
        if self.kind == "rested_support_count":
            return self._compare(sum(1 for c in player.support if c.rested))
        if self.kind in ("faints_prev_turn", "faints_this_turn"):
            if self.kind == "faints_prev_turn":
                want = ctx.state.turn_counter - 1
            else:
                want = ctx.state.turn_counter
            hits = sum(1 for turn, color, level in player.faint_log
                       if turn == want
                       and (self.color is None or color is self.color)
                       and (self.value2 is None or level == self.value2))
            return hits >= self.value
        if self.kind == "support_trashed":
            return self._compare(player.support_trashed_this_turn)
        if self.kind == "both_trash_count":
            return self._compare(len(ctx.me.trash) + len(ctx.opp.trash))
        if self.kind == "foreign_hp":
            # "your Cookie has an opponent's card as HP" — HP piles are built
            # from your own deck, so this only happens via a steal effect.
            return any(any(c.owner != cookie.owner for c in cookie.hp_cards)
                       for cookie in player.battle)
        if self.kind == "refresh_count":
            return self._compare(player.refresh_count)
        if self.kind == "own_level_sum":
            return self._compare(sum(c.level(db) for c in player.battle))
        if self.kind == "revealed_is":
            filt = self.card_filter or CardFilter()
            return any(filt.matches(db[c.card_id]) for c in (ctx.revealed or []))
        if self.kind == "target_hp":
            target = ctx.attack_target
            return target is not None and self._compare(target.remaining_hp)
        if self.kind == "target_level":
            target = ctx.attack_target
            return target is not None and self._compare(target.level(db))
        if self.kind == "break_level_higher":
            return ctx.me.break_level_total(db) > ctx.opp.break_level_total(db)
        if self.kind == "break_level_lower":
            return ctx.me.break_level_total(db) < ctx.opp.break_level_total(db)
        if self.kind == "arena_effect_damage":
            return ctx.me.arena_effect_damage_this_turn
        if self.kind == "item_played":
            return ctx.me.items_played_this_turn > 0
        if self.kind == "hp_gained":
            return ctx.me.hp_gained_this_turn
        if self.kind == "arena_break_additions":
            return self._compare(player.arena_break_additions_this_turn)
        if self.kind == "cookies_to_deck":
            return self._compare(player.cookies_to_deck_this_turn)
        if self.kind == "cookies_to_deck_bottom":
            return self._compare(player.cookies_to_deck_bottom_this_turn)
        if self.kind == "played_from_trash":
            return len(player.played_from_trash_this_turn) >= self.value
        if self.kind == "self_hp_reduced":
            # "During this turn, if this Cookie's HP was reduced" — the Cookie
            # asking is the one whose effect is resolving.
            cookie = ctx.source_cookie
            return cookie is not None and cookie.hp_reduced_this_turn
        return False


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------


class Op:
    """Base class. ``run`` returns False to abort the rest of the sequence."""

    def run(self, ctx, env: dict) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def is_live(self, ctx, env: dict) -> bool:
        """Would running this op accomplish anything, right now?

        A probe, not a rehearsal: it must never touch the game state. Anything
        it cannot decide is live, so an unknown op keeps its card on offer —
        wrongly hiding a playable card is a worse failure than wrongly showing
        one, because a hidden card cannot be argued with.

        The default answers for every op that reads a selection: once a
        ``Select`` finds nothing, the ops downstream of it are dead too.
        """
        ref = getattr(self, "ref", None)
        return ref is None or env.get(ref, None) != []


@dataclass
class Select(Op):
    scope: str = SCOPE_OPPONENT
    count: int = 1
    filter: Filter = field(default_factory=Filter)
    optional: bool = True
    all_matching: bool = False
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        pool = self._pool(ctx)
        if not pool:
            env[self.ref] = []
            # "Select up to 1" with no legal target simply does nothing; a
            # mandatory selection with no target aborts the rest of the clause.
            return self.optional
        if self.all_matching:
            env[self.ref] = list(pool)
            return True

        chosen = []
        remaining = list(pool)
        for _ in range(self.count):
            if not remaining:
                break
            side = "opponent's" if self.scope == SCOPE_OPPONENT else "your"
            pick = ctx.choose(f"Select up to {self.count} of {side} Cookies",
                              remaining, optional=self.optional)
            if pick is None:
                break
            remaining.remove(pick)
            chosen.append(pick)
        env[self.ref] = chosen
        return True

    def _pool(self, ctx) -> list:
        # `ctx.enemy_cookies` rather than `ctx.opp.battle`: it is what drops
        # the Cookies an opposing effect is forbidden to select, and a
        # compiled selection is an effect like any hand-written one.
        pool = []
        if self.scope in (SCOPE_OPPONENT, SCOPE_ALL):
            pool += [c for c in ctx.enemy_cookies() if self.filter.matches(c, ctx)]
        if self.scope in (SCOPE_OWN, SCOPE_ALL):
            pool += [c for c in ctx.me.battle if self.filter.matches(c, ctx)]
        return pool

    def is_live(self, ctx, env) -> bool:
        """Nothing to select means nothing downstream can happen.

        True even for an optional select, whose ``run`` returns True on an empty
        pool so the clause carries on — it carries on doing nothing, because
        every op after it reads the selection this one failed to make.
        """
        if self._pool(ctx):
            return True
        env[self.ref] = []
        return False


def _resolve(ref: str, ctx, env) -> list:
    if ref == REF_SELF:
        return [ctx.source_cookie] if ctx.source_cookie else []
    if ref == REF_HOST:
        return [ctx.source_cookie] if ctx.source_cookie else []
    targets = env.get(ref) or []
    return [t for t in targets if t is not None]


@dataclass
class Damage(Op):
    amount: int = 1
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        for cookie in _resolve(self.ref, ctx, env):
            ctx.deal_damage(cookie, self.amount)
        return True


@dataclass
class GainHP(Op):
    amount: int = 1
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        for cookie in _resolve(self.ref, ctx, env):
            ctx.gain_hp(cookie, self.amount)
        return True


@dataclass
class ModifyAttack(Op):
    delta: int = -1
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        for cookie in _resolve(self.ref, ctx, env):
            ctx.modify_attack(cookie, self.delta)
        return True


@dataclass
class Faint(Op):
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        for cookie in _resolve(self.ref, ctx, env):
            ctx.faint(cookie)
        return True


@dataclass
class TrashHP(Op):
    """"Place N card(s) from the top of that Cookie's HP into the trash."""

    amount: int = 1
    ref: str = REF_IT
    to_opponent_trash: bool = False

    def run(self, ctx, env) -> bool:
        for cookie in _resolve(self.ref, ctx, env):
            ctx.trash_hp(cookie, self.amount,
                         opponent_trash=self.to_opponent_trash)
        return True


@dataclass
class TrashHPUntil(Op):
    """"Place HP cards in the trash until the Cookie's HP reaches N."

    A cost priced as a drain rather than as a fixed number of cards: how much
    it takes depends on how healthy the Cookie is, which is the point — the
    same card is cheap on a Cookie that is nearly dead and expensive on a fresh
    one. A Cookie already at or below the floor pays nothing, which is what the
    text says and is why `floor` is never 0: no card written this way can
    faint its own payment.
    """

    floor: int = 1
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        for cookie in _resolve(self.ref, ctx, env):
            # One card at a time through the same primitive a fixed cost uses,
            # so each one is revealed and logged the way it would be otherwise.
            while cookie.remaining_hp > self.floor:
                before = cookie.remaining_hp
                ctx.trash_hp(cookie, 1)
                if cookie.remaining_hp >= before:
                    break          # nothing moved; do not spin
        return True


@dataclass
class ReturnToHand(Op):
    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        # Inside a FLIP, "this Cookie" is the card that was just revealed — the
        # host is only ever meant when the text spells it out, and that compiles
        # to REF_HOST.
        if self.ref == REF_SELF and getattr(ctx, "trigger", "") == "flip":
            ctx.return_self_to_hand()
            return True
        for cookie in _resolve(self.ref, ctx, env):
            ctx.return_to_hand(cookie)
        return True


@dataclass
class Draw(Op):
    amount: int = 1

    def run(self, ctx, env) -> bool:
        ctx.draw(self.amount)
        return True


@dataclass
class Discard(Op):
    amount: int = 1
    color: Color | None = None
    optional: bool = True

    def run(self, ctx, env) -> bool:
        if self.color is not None:
            return bool(ctx.discard_colored(self.amount, self.color))
        return bool(ctx.discard(self.amount, optional=self.optional))


@dataclass
class RestSupport(Op):
    amount: int = 1
    mine: bool = True

    def run(self, ctx, env) -> bool:
        env["rested"] = ctx.rest_support(self.amount, mine=self.mine)
        return True


@dataclass
class SetSupportActive(Op):
    amount: int = 1
    mine: bool = True

    def run(self, ctx, env) -> bool:
        ctx.set_support_active(self.amount, mine=self.mine)
        return True


@dataclass
class MillToSupport(Op):
    amount: int = 1
    rested: bool = True

    def run(self, ctx, env) -> bool:
        ctx.mill_to_support(self.amount, rested=self.rested)
        return True


@dataclass
class ReturnSupportToHand(Op):
    card_type: CardType | None = None

    def run(self, ctx, env) -> bool:
        predicate = None
        if self.card_type is not None:
            predicate = lambda d: d.type is self.card_type  # noqa: E731
        return bool(ctx.return_support_to_hand(predicate=predicate))


@dataclass
class TrashStage(Op):
    amount: int = 1
    mine: bool = False

    def run(self, ctx, env) -> bool:
        ctx.trash_stage(self.amount, mine=self.mine)
        return True


@dataclass
class MillDeck(Op):
    amount: int = 1

    def run(self, ctx, env) -> bool:
        ctx.mill_deck(self.amount)
        return True


@dataclass
class DamageEqualToRested(Op):
    """"receives damage equal to the number of cards rested by this effect"."""

    ref: str = REF_IT

    def run(self, ctx, env) -> bool:
        amount = int(env.get("rested", 0))
        if amount:
            for cookie in _resolve(self.ref, ctx, env):
                ctx.deal_damage(cookie, amount)
        return True


# Prompts name both ends of the move: "a card from your trash" alone does not
# say whether answering it puts the card somewhere good or somewhere fatal.
_ZONE_LABELS = {
    ZONE_TRASH: "trash",
    ZONE_BREAK: "break area",
    ZONE_HAND: "hand",
    ZONE_SUPPORT: "support area",
    ZONE_BATTLE: "battle area",
    ZONE_DECK_TOP: "deck (top)",
    ZONE_DECK_BOTTOM: "deck (bottom)",
}


@dataclass
class MoveCards(Op):
    """Move up to ``count`` filtered cards from one zone to another.

    Covers the large family of "return X from your trash to your hand",
    "select X from your break area and place it in the trash", "place X on the
    bottom of the deck" clauses with a single op.
    """

    source: str = ZONE_TRASH
    destination: str = ZONE_HAND
    count: int = 1
    filter: CardFilter = field(default_factory=CardFilter)
    from_opponent: bool = False
    optional: bool = True
    # Only read when the destination is the support area: a card placed there
    # arrives rested unless the text says "as active".
    rested: bool = True

    def run(self, ctx, env) -> bool:
        owner = ctx.opp if self.from_opponent else ctx.me
        moved = 0
        for _ in range(self.count):
            card = self._take(ctx, owner)
            if card is None:
                break
            self._put(ctx, owner, card)
            moved += 1
        return self.optional or moved > 0

    def _pool(self, ctx, owner):
        db = ctx.db
        if self.source == ZONE_BATTLE:
            return [c for c in owner.battle if self.filter.matches(c.defn(db))]
        pile = {
            ZONE_TRASH: owner.trash,
            ZONE_BREAK: owner.break_area,
            ZONE_HAND: owner.hand,
            ZONE_SUPPORT: owner.support,
        }.get(self.source, [])
        return [c for c in pile if self.filter.matches(db[c.card_id])]

    def _take(self, ctx, owner):
        pool = self._pool(ctx, owner)
        if not pool:
            return None
        picked = ctx.choose(
            f"Move a card from your {_ZONE_LABELS.get(self.source, self.source)}"
            f" to your {_ZONE_LABELS.get(self.destination, self.destination)}",
            pool, optional=self.optional)
        if picked is None:
            return None
        if self.source == ZONE_BATTLE:
            # A Cookie leaving the field sheds its HP pile — and anything it
            # was 【Awaken】ed on top of — to the trash.
            owner.battle.remove(picked)
            owner.trash.extend(picked.spent_cards)
            card = picked.card
            ctx.game._check_battle_area(owner)
            return card
        self._pile_of(owner, self.source).remove(picked)
        return picked

    @staticmethod
    def _pile_of(player, zone):
        return {
            ZONE_TRASH: player.trash,
            ZONE_BREAK: player.break_area,
            ZONE_HAND: player.hand,
            ZONE_SUPPORT: player.support,
        }[zone]

    def _put(self, ctx, owner, card) -> None:
        if self.destination == ZONE_DECK_TOP:
            owner.deck.insert(0, card)
        elif self.destination == ZONE_DECK_BOTTOM:
            owner.deck.append(card)
        elif self.destination == ZONE_SUPPORT:
            card.rested = self.rested
            owner.support.append(card)
        else:
            self._pile_of(owner, self.destination).append(card)
        if self.destination == ZONE_BREAK:
            ctx.game._check_win()


@dataclass
class PayCost(Op):
    """A ``<...>`` cost. Aborts the clause when it cannot be paid."""

    cost: Cost = field(default_factory=Cost)

    def run(self, ctx, env) -> bool:
        if not self.cost:
            return True
        return ctx.pay(self.cost)

    def is_live(self, ctx, env) -> bool:
        return not self.cost or ctx.can_pay(self.cost)


@dataclass
class Guard(Op):
    """An ``If ...,`` prefix. Aborts the clause when the condition fails."""

    conditions: tuple = ()

    def run(self, ctx, env) -> bool:
        return all(c.holds(ctx) for c in self.conditions)

    def is_live(self, ctx, env) -> bool:
        # `holds` is a pure read of the board, so the probe is the real answer
        # rather than an approximation of it.
        return all(c.holds(ctx) for c in self.conditions)


@dataclass
class Clause:
    """One sentence: costs and conditions gating a sequence of ops.

    A ``<...>`` cost is *optional* — you may pay it to get the effect, or
    decline and get nothing. That only needs asking when the effect happened
    *to* the player rather than being something they chose: a FLIP turning over
    mid-attack should not quietly rest two support cards on its controller's
    behalf. When the controller asked for the effect in the first place — an
    【Activate】 skill, an Item or a Trap they played — taking the action is the
    decision, and the cost is paid without a second prompt.
    """

    ops: list = field(default_factory=list)
    cost_text: str = ""     # the printed <...> costs, for the prompt

    def run(self, ctx, env) -> bool:
        approved = False
        if self.cost_text:
            # Never offer a cost that cannot be met; the clause fails anyway.
            if not self._affordable(ctx):
                return False
            if not ctx.wants_to_pay(self.cost_text):
                return False
            # Agreed once, for every op in this clause: the ops that *are* the
            # cost must not ask about it a second time on their way through.
            approved, ctx.cost_approved = ctx.cost_approved, True
        try:
            for op in self.ops:
                if not op.run(ctx, env):
                    return False
            return True
        finally:
            if self.cost_text:
                ctx.cost_approved = approved

    def _affordable(self, ctx) -> bool:
        return all(ctx.can_pay(op.cost) for op in self.ops if isinstance(op, PayCost))

    def is_live(self, ctx, env) -> bool:
        """Would this clause do anything if it ran now?

        Same order as ``run``: an unaffordable cost kills the clause, then each
        op is asked in turn so that a dead ``Select`` takes its dependants with
        it. Ops are probed even after the first failure would have aborted, so
        that every ``Select`` gets to record its empty result in ``env`` for the
        later clauses that read it.
        """
        live = self._affordable(ctx)
        for op in self.ops:
            if not op.is_live(ctx, env):
                live = False
        return live



@dataclass
class Modal(Op):
    """"Select 1 of the following." — one card, two lines, the player picks.

    Each branch is a list of `Clause`, because a branch is as long as the card
    prints it: "View 3 cards ... Then, place the remaining cards in the trash"
    is one option made of three sentences, and they share an `env` so a Select
    in the first reaches the ops in the third.

    Only branches that would do something are offered, for the same reason
    `Game._would_do_something` filters actions: a line the board makes
    impossible is not a choice, it is a way to throw the card away by mistake.
    If none of them is live the whole op is dead and never runs.
    """

    branches: tuple = ()          # ((label, (Clause, ...)), ...)

    def _live_branches(self, ctx) -> list:
        return [b for b in self.branches
                if all(clause.is_live(ctx, {}) for clause in b[1])]

    def run(self, ctx, env) -> bool:
        options = self._live_branches(ctx) or list(self.branches)
        if not options:
            return False
        labels = [label for label, _ in options]
        pick = ctx.choose("Select 1 of the following", labels, optional=False)
        chosen = options[labels.index(pick)] if pick in labels else options[0]
        ctx.note(chosen[0])
        for clause in chosen[1]:
            if not clause.run(ctx, env):
                return False
        return True

    def is_live(self, ctx, env) -> bool:
        return bool(self._live_branches(ctx))


@dataclass
class Program:
    """A full effect: clauses run in order, each independently gated."""

    clauses: list = field(default_factory=list)
    source: str = ""

    def __call__(self, ctx) -> None:
        env: dict = {}
        for clause in self.clauses:
            clause.run(ctx, env)

    def is_live(self, ctx) -> bool:
        """Would any clause of this effect accomplish something right now?

        One live clause is enough — a card whose second sentence is dead is
        still worth playing for its first.
        """
        env: dict = {}
        return any(clause.is_live(ctx, env) for clause in self.clauses)

    def __len__(self) -> int:
        return len(self.clauses)
