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
from .state import Cookie, CardInstance, GameState, PlayerState, card_label


def _view_prompt(criterion: str, take: int = 1) -> str:
    """"Add a {P} card to your hand", or "Add up to 2 {P} cards to your hand".

    The count is in the prompt because it is the question: a strip of three
    cards with a confirm button under it does not otherwise say how many of
    them you are allowed to keep.
    """
    if take > 1:
        what = f"{criterion} cards" if criterion else "cards"
        return f"Add up to {take} {what} to your hand"
    what = f"{criterion} card" if criterion else "card"
    return f"Add a {what} to your hand"


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
# "your Cookies take no damage from your opponent" abilities. Each entry takes
# (db, owner, state) and returns True while that player's board is shielded.
OPPONENT_DAMAGE_SHIELDS: list = []


def shields_from_opponent(db, owner, state) -> bool:
    """Is this player's board immune to their opponent's damage right now?

    The state comes in because these are printed abilities with printed
    conditions, and 【Your Turn】 is one of them — a shield that reads "your
    Cookies take no damage from your opponent" is worth knowing the turn for.
    """
    return any(rule(db, owner, state) for rule in OPPONENT_DAMAGE_SHIELDS)

# 【EXTRA】 "Can be played if ..." gates. Each entry takes
# (db, player, opponent, card_def) and returns False to forbid playing that
# card right now. The opponent is passed because several gates compare zones.
PLAY_CONDITIONS: list = []


@dataclass(frozen=True)
class ExtraPlay:
    """How one 【EXTRA】 card leaves the EXTRA deck.

    Every EXTRA card prints a gate — the "Can be played if ..." line — and the
    card is simply not a legal move while that is false; there is no version of
    the move that fizzles. `hosts` is what separates the two kinds: a standalone
    EXTRA Cookie leaves it None and is deployed into a free battle slot, while
    an 【Awaken】 card returns the Cookies it may be stacked on top of, and is
    unplayable when that list is empty. `pay` is the printed `<...>` cost, run
    once the move is taken.
    """

    gate: object                     # (ctx) -> bool
    hosts: object = None             # (ctx) -> list[Cookie], 【Awaken】 only
    pay: object = None               # (ctx) -> bool, the <...> cost

    @property
    def is_awaken(self) -> bool:
        return self.hosts is not None


EXTRA_PLAYS: dict[str, ExtraPlay] = {}


def extra_play(card_id: str, **kwargs):
    """Register how an 【EXTRA】 card is played. Decorates its gate."""
    def wrap(gate):
        EXTRA_PLAYS[card_id] = ExtraPlay(gate=gate, **kwargs)
        return gate
    return wrap


def extra_play_of(db, card_id: str) -> "ExtraPlay | None":
    return EXTRA_PLAYS.get(db[card_id].base_id if card_id in db else card_id)


@dataclass(frozen=True)
class SpecialPlay:
    """How one 【Special Play】 Cookie gets onto the board.

    Comprehensive Rules 4-10-1-1: a Cookie with 【Special Play】 *cannot be
    played* while its printed condition is unmet — the condition is not a cost
    you may decline, it is the only door the card has. So the registry is
    consulted the same way `ExtraPlay` is: no entry, or a false `gate`, and the
    card is not a legal move at all rather than a free LV.3 body.

    `pay` performs the printed condition once the move is taken, and `frees`
    says how many battle slots it vacates on the way — Dark Enchantress trashes
    two Cookies to arrive, so she is playable *out of a full battle area*, which
    is what 3-5-6-1-1 allows.
    """

    gate: object                     # (ctx) -> bool
    pay: object                      # (ctx) -> bool
    frees: int = 0                   # battle slots the condition empties


SPECIAL_PLAYS: dict[str, SpecialPlay] = {}


def special_play(card_id: str, **kwargs):
    """Register how a 【Special Play】 Cookie is played. Decorates its gate."""
    def wrap(gate):
        SPECIAL_PLAYS[card_id] = SpecialPlay(gate=gate, **kwargs)
        return gate
    return wrap


def special_play_of(db, card_id: str) -> "SpecialPlay | None":
    return SPECIAL_PLAYS.get(db[card_id].base_id if card_id in db else card_id)


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

# "this Cookie cannot be selected by your opponent's effects" and "this Cookie
# cannot be trashed". Two registries rather than one, because cards print the
# two sentences separately and a Cookie can have either without the other; both
# take (db, owner, cookie) like MOVEMENT_PROTECTORS and read the owner's zones.
# They are consulted only when the *opponent* is acting: a Cookie's own
# controller may still pick it up and may still trash it.
SELECTION_PROTECTORS: list = []
TRASH_PROTECTORS: list = []


def is_select_protected(db, owner, cookie) -> bool:
    return any(rule(db, owner, cookie) for rule in SELECTION_PROTECTORS)


def is_trash_protected(db, owner, cookie) -> bool:
    return any(rule(db, owner, cookie) for rule in TRASH_PROTECTORS)


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

# Rewrites of how many Cookies a [refresh] sends to the break area. Each entry
# takes (db, player, opponent) and returns a replacement count, or None. Two
# Cookies print this and they pull in opposite directions — one waives the cost
# for its controller, one raises it for the other seat — so the rule has to be
# asked about the player who is refreshing, from both sides of the table.
REFRESH_COST_MODIFIERS: list = []


def refresh_break_cost(db, player, opponent, printed: int) -> int:
    for modifier in REFRESH_COST_MODIFIERS:
        replacement = modifier(db, player, opponent)
        if replacement is not None:
            return replacement
    return printed


# Continuous "this Cookie receives -N damage" abilities. Each entry takes
# (db, state, cookie) and returns how much to shave off incoming damage.
# A registry rather than a field on the Cookie because these are conditional
# on the board — the condition has to be re-read every time damage lands, not
# banked at the moment the ability was granted.
DAMAGE_REDUCERS: list = []


def continuous_damage_reduction(db, state, cookie) -> int:
    return sum(rule(db, state, cookie) for rule in DAMAGE_REDUCERS)

def modified_attack_cost(db, player, cookie, cost):
    for modifier in ATTACK_COST_MODIFIERS:
        replacement = modifier(db, player, cookie, cost)
        if replacement is not None:
            cost = replacement
            break
    if getattr(cookie, "attack_cost_all_generic", False):
        # "all changed to {N}": the same number of symbols, none of them
        # coloured, so any support card can pay for any of them.
        from .cost import Cost
        cost = Cost((), cost.total)
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


def playable_if(predicate: Callable[["Ctx"], bool]) -> Callable[[EffectFn], EffectFn]:
    """Declare when a hand-written effect has something to do.

    A compiled card carries its conditions as ``Guard`` ops that the engine can
    read, so it knows for itself when it would fizzle. A hand-written body is
    opaque Python, so it says so here instead — the predicate is the ``if`` at
    the top of the function, hoisted where the action list can see it.

    The predicate must only read the board. Leave it off and the card is always
    offered, which is the old behaviour.
    """

    def wrap(fn: EffectFn) -> EffectFn:
        fn.playable = predicate       # type: ignore[attr-defined]
        return fn

    return wrap


def effect_is_live(fn: EffectFn | None, ctx: "Ctx") -> bool:
    """Whether running ``fn`` right now would accomplish anything.

    Three kinds of answer, in order of how much the engine can see: a compiled
    program reads its own guards and targets; a hand-written body answers only
    if it was given a `playable_if`; anything else is assumed live, so an
    unannotated card behaves exactly as it did before.
    """
    if fn is None:
        return False
    probe = getattr(fn, "is_live", None)          # a compiled Program
    if probe is not None:
        return bool(probe(ctx))
    predicate = getattr(fn, "playable", None)     # an annotated hand-written body
    if predicate is not None:
        return bool(predicate(ctx))
    return True


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
    # An 【EXTRA】 card's gate is a clause like any other, and the one that
    # decides whether the card can be in the game at all. A registered gate
    # with no body is a vanilla Cookie that enters on a real condition, which
    # is a playable card; hiding it from the deck builder was treating the most
    # important half of its text as if it were not there.
    if base in EXTRA_PLAYS:
        return True
    return any((base, trigger) in _REGISTRY for trigger in Trigger)


def ask_many(controller, state, prompt: str, options: Sequence, count: int,
             *, optional: bool = False, up_to: bool = False) -> list:
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
        picked = picker(state, prompt, pool, count=count, optional=optional,
                        up_to=up_to) or []
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
        # "Up to N" means the answer is allowed to be short — including empty.
        # A fixed "N cards" is not: a padded answer still has to leave exactly
        # `count` cards spent and a legal state.
        if not up_to:
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

    def discard_matching(self, n: int, predicate) -> list[CardInstance]:
        """Discard ``n`` cards the predicate accepts, chosen by the controller.

        `discard_colored` covers the common "{Y} cards" wording; this is for
        the costs that ask for something narrower — BS9-030 wants three yellow
        Cookies *that have FLIP*, which is a type as well as a colour.
        """
        return self.game.discard(self.me, n, self, predicate=predicate)

    def view_top(self, n: int, *, pick=None, take: int = 1,
                 prompt: str = "", reveal: bool = False,
                 rest: str = "bottom", criterion: str = "") -> list[CardInstance]:
        """"View N cards from the top of your deck, take one, put the rest back."

        Three cards in the pool are written this way and each did it by hand,
        slicing `ctx.me.deck` directly — which is the one thing a card effect is
        not supposed to do, and which got the important detail wrong: Aloe
        Cookie's "select 1 {B} card" only ever *offered* the blue ones, so a
        player looked at the top three and was shown one. Looking at all three
        is the effect. What you may take out of them is a separate restriction,
        and the two must not be collapsed into each other.

        So `pick` narrows what is selectable and nothing else. Every viewed card
        travels on the question as context (`state.viewing`) and the browser
        draws all of them, greying the ones the criterion rules out.

        The cards stay in the deck while the question is open, and move only
        once it is answered — nothing has happened yet, and leaving them there
        is also what lets the viewer recognise them as cards it can lay out in
        a strip rather than as a list of buttons.

        `reveal` is "show it to your opponent": the taken card is named in the
        log, and the ones left behind never are. The log is public, and what
        you saw and did not take is yours.

        `rest` is where the leftovers go — "bottom" of the deck, "top" of it,
        or "trash". "top" is the one where the order is a real decision: those
        cards are the next ones drawn and their controller has just seen them,
        so more than one leftover is ordered by hand rather than silently.
        """
        viewed = self.me.deck[:n]
        if not viewed:
            return []
        self.state.record(f"views {len(viewed)} card"
                          f"{'' if len(viewed) == 1 else 's'} from the top of the deck")
        eligible = [c for c in viewed if pick is None or pick(self.db[c.card_id])]
        taken: list[CardInstance] = []
        if not eligible:
            # You still looked. Skipping the prompt here would have made the
            # commonest miss — three cards, none of them the right colour —
            # the one case where the card does nothing you can see, which is
            # exactly when knowing what went past matters most.
            with self.game.showing(viewed):
                self.confirm(f"Nothing among the {len(viewed)} viewed cards "
                             f"can be taken")
        # "Add up to 2 of them to your hand" is one question about the whole
        # run, not two in a row: the viewed cards are on screen together and
        # picking one should not hide the rest behind another prompt.
        if take > 1 and eligible:
            with self.game.showing(viewed):
                batched = self.choose_many(prompt or _view_prompt(criterion, take),
                                           eligible, count=take, up_to=True)
            if batched is not None:
                taken = list(batched)
                eligible = [c for c in eligible if c not in taken]
        for _ in range(take - len(taken)):
            if not eligible:
                break
            with self.game.showing(viewed):
                chosen = self.choose(prompt or _view_prompt(criterion), eligible,
                                     optional=True)
            if chosen is None:
                break
            eligible.remove(chosen)
            taken.append(chosen)

        # Only now does anything move. Taken cards come out of the deck by
        # identity, because the top N may not be the first N any more if an
        # effect reshuffled underneath us — it cannot, today, but the identity
        # form costs nothing and cannot go wrong.
        for card in viewed:
            if card in self.me.deck:
                self.me.deck.remove(card)
        for card in taken:
            card.face_up = bool(reveal)
            self.me.hand.append(card)
            if reveal:
                self.state.record(f"reveals {card_label(self.db[card.card_id])} "
                                  f"and adds it to hand")
        leftovers = [c for c in viewed if c not in taken]
        if rest == "trash":
            self.me.trash.extend(leftovers)
        elif rest == "top":
            self.me.deck[:0] = self._ordered_for_top(leftovers)
        else:
            # "in any order" — the printed text lets the controller order them
            # and this does not ask, because the deck is face down and the only
            # thing that could read the difference is the controller's own
            # memory of what they just saw.
            self.me.deck.extend(leftovers)
        if leftovers:
            where = {"trash": "the trash",
                     "top": "the top of the deck"}.get(rest, "the bottom of the deck")
            self.state.record(f"places {len(leftovers)} viewed card"
                              f"{'' if len(leftovers) == 1 else 's'} in {where}")
        return taken

    def _ordered_for_top(self, cards: list[CardInstance]) -> list[CardInstance]:
        """Put cards back on the deck "in any order", top of the list first.

        Unlike the bottom of the deck, this order is worth asking about: these
        are the next cards drawn. One card has only one order, so that case
        never asks.
        """
        if len(cards) < 2:
            return list(cards)
        remaining = list(cards)
        ordered: list[CardInstance] = []
        while len(remaining) > 1:
            with self.game.showing(remaining):
                pick = self.choose("Put which card on top of your deck?",
                                   remaining, optional=False)
            pick = pick if pick is not None else remaining[0]
            remaining.remove(pick)
            ordered.append(pick)
        ordered.extend(remaining)
        return ordered

    def reveal_top(self, n: int = 1) -> list[CardInstance]:
        """"Reveal N cards from the top of your deck" — and put them back.

        A *reveal* is not a *view*: the cards are shown to both players and the
        card then asks a question about them ("if that card is a {B} LV.2
        Cookie, ..."). Nothing moves, so they stay exactly where they were,
        still in draw order. Nine cards in the pool open this way.

        Public, unlike `view_top`, which is why the names go in the log — that
        is the whole difference between the two verbs and the reason they are
        not one with a flag.
        """
        seen = self.me.deck[:n]
        if seen:
            names = ", ".join(card_label(self.db[c.card_id]) for c in seen)
            self.state.record(f"reveals {names} from the top of the deck")
        return list(seen)

    def run_flip(self, card: CardInstance, host: Cookie | None = None) -> None:
        """Resolve a card's FLIP as though it had just turned over.

        BS9-030 discards a FLIP Cookie out of hand and fires its effect from
        there, which is the only card in the pool that runs a FLIP anywhere but
        an HP pile. `host` is the Cookie the effect treats as its own — the one
        a "this Cookie gains +1 HP" clause lands on — and defaults to whatever
        is resolving.
        """
        from .enums import CardType
        if self.db[card.card_id].type is not CardType.FLIP:
            return
        self.game._run_effect(card, Trigger.FLIP, self.me,
                              flip_host=host or self.source_cookie)

    def steal_to_hp(self, cookie: Cookie, card: CardInstance,
                    source: list) -> None:
        """Move a card out of `source` onto the bottom of `cookie`'s HP pile.

        Face up, because every card that does this says so: HP taken off
        someone else is public where your own pile is not. The bottom is
        `index 0` — damage pops off the end — so a stolen card is the last one
        the pile will turn over, not the next.
        """
        if card not in source:
            return
        source.remove(card)
        card.face_up = True
        cookie.hp_cards.insert(0, card)

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

    def return_support_to_hand(self, *, predicate=None,
                               optional: bool = True) -> bool:
        """"Return 1 card from your support area to your hand."

        `optional` is the difference between "return up to 1" and "return 1":
        the latter is part of a bargain the controller already agreed to, and
        declining it would hand them the upside without the cost.
        """
        options = [c for c in self.me.support
                   if predicate is None or predicate(self.db[c.card_id])]
        if not options:
            return False
        card = self.choose("Return a support card to hand", options,
                           optional=optional)
        if card is None:
            if not optional:
                card = options[0]
            else:
                return False
        self.me.support.remove(card)
        card.rested = False
        self.me.hand.append(card)
        return True

    # -- support area ----------------------------------------------------
    def rest_support(self, n: int, *, mine: bool = True) -> int:
        """"Rest up to N cards in [a] support area" — the controller picks which.

        "Up to" is a real decision: which cards go down decides what is left to
        pay with, and how many go down feeds effects that scale off the count.
        So a controller that can answer a batch question (a human) is shown the
        active cards and rests as few as none. Scripted agents have no opinion
        worth asking for and would only make self-play numbers wobble, so they
        keep resting the first N, exactly as before.
        """
        player = self.me if mine else self.opp
        options = [player.support[i] for i in player.active_support()]
        if not options or n <= 0:
            return 0
        limit = min(n, len(options))
        controller = self.game.controller(self.me.index)
        if getattr(controller, "choose_many", None) is not None:
            whose = "your" if mine else "your opponent's"
            picked = ask_many(controller, self.state,
                              f"Rest up to {limit} card{'' if limit == 1 else 's'}"
                              f" in {whose} support area",
                              options, limit, optional=True, up_to=True)
        else:
            picked = options[:limit]
        for card in picked:
            card.rested = True
        return len(picked)

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
        """The opponent's Cookies my effect is allowed to pick.

        The one funnel every "select up to N of your opponent's Cookies" goes
        through, which is why the selection protections are filtered here: a
        Cookie that cannot be selected is not on offer at all, rather than
        being offered and then silently doing nothing.
        """
        return [c for c in self.opp.battle
                if not is_select_protected(self.db, self.opp, c)
                and (predicate is None or predicate(c))]

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
        # Everything routed through an effect — a skill, a trap, the "Then,"
        # rider on an attack — is effect damage, not the swing itself.
        self.game.deal_damage(cookie, amount, source_player=self.me.index,
                              kind="effect")

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
        self.state.record(f"{card_label(self.db[card.card_id])} returns to hand")
        return True

    def return_to_hand(self, cookie: Cookie) -> None:
        if not self._may_move(cookie):
            return
        self.game.return_cookie_to_hand(cookie)

    def move_to_support(self, cookie: Cookie, *, rested: bool = False) -> None:
        """"Place that Cookie in your support area" — it becomes energy.

        Removal that pays its controller back: nothing reaches the break area,
        and the card lands in the support area as a colour source. It goes
        through the same movement lock and move-protection checks as a bounce,
        because from the board's point of view it is one.
        """
        if not self._may_move(cookie):
            return
        self.game.move_cookie_to_support(cookie, rested=rested)

    def trash_cookie(self, cookie: Cookie) -> None:
        """Remove a Cookie to the trash *without* it fainting.

        Cards word this as "place into the trash" rather than "make faint", and
        the difference is the whole game: a trashed Cookie never reaches the
        break area, so its owner's opponent banks no Level for it.
        """
        if not self._may_move(cookie):
            return
        owner = self.state.players[cookie.owner]
        if owner.index != self.me.index and is_trash_protected(self.db, owner, cookie):
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

    def play_cookie_from_trash(self, predicate=None, *,
                               optional: bool = True) -> bool:
        """"Play up to 1 Cookie ... from your trash."

        ``optional`` is the difference between "play *up to* 1" and "play 1":
        the latter is mandatory once its cost is paid, so declining is not one
        of the answers.
        """
        if len(self.me.battle) >= self.game.rules.max_battle_cookies:
            return False
        options = [c for c in self.me.trash
                   if self.db[c.card_id].is_cookie
                   and (predicate is None or predicate(self.db[c.card_id]))]
        if not options:
            return False
        card = self.choose("Play a Cookie from your trash", options,
                           optional=optional)
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
            # A pile that got shorter is HP that was reduced, however it went.
            cookie.hp_reduced_this_turn = True
            # Face up on the table, so the board shows it — but not as a FLIP,
            # because no FLIP fires on this path.
            self.game.record_reveal(cookie, card)
            # Emptying a pile this way is still the pile reaching 0, so the
            # floor holds here exactly as it does against damage.
            self.game.hold_the_floor(cookie)
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
    def choose_many(self, prompt: str, options: Sequence, *, count: int,
                    up_to: bool):
        """Ask for a whole selection in one question, or say it cannot be.

        Returns ``None`` when this seat has no batched form — every scripted
        agent, which answers one card at a time. That is deliberate and load
        bearing: a bot asked N separate questions and a bot asked one question
        for N cards make different games out of the same seed, and self-play
        numbers are the regression check for the whole engine. Only a seat that
        implements ``choose_many`` (a person at a browser) takes this path.
        """
        controller = self.game.controller(self.me.index)
        if getattr(controller, "choose_many", None) is None:
            return None
        return ask_many(controller, self.state, prompt, list(options), count,
                        optional=up_to, up_to=up_to)

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
