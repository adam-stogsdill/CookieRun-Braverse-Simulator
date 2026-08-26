"""The rules engine.

Usage::

    game = Game(deck_a, deck_b, [agent_a, agent_b])
    game.setup()
    while not game.state.over:
        game.step(agent.choose_action(game.state, game.legal_actions()))

The engine is deterministic given its RNG seed, and ``GameState`` is a pure
data tree, so ``game.clone()`` is a valid rollout root for search agents.
"""

from __future__ import annotations

import contextlib
import copy
import random
from dataclasses import dataclass
from typing import Sequence

from . import actions as A
from . import config as cfg
from .cards import CardDB, CardDef, blocker_price, default_db
from .cost import Cost, plan_payment
from .effects import (Ctx, Trigger, ask_many, cannot_attack, effect_is_live,
                      extra_play_of, forced_attack_target, get_effect,
                      may_play, modified_attack_cost, special_play_of)
from .enums import CardType, Color, Keyword, Marker, Phase
from .state import (CardInstance, Cookie, GameState, PlayerState,
                    card_label)

# What sort of thing an effect is, for the log. "effect damage" covers a trap
# sprung on your turn, an 【Activate】 skill, an ITEM and the "Then, ..." rider
# on an attack line — four very different things to be hit by, and until this
# the record could not tell them apart. The trigger decides first, because the
# same card can arrive by more than one route: a FLIP card in an HP pile is a
# FLIP when it turns over, whatever its printed type says.
TRIGGER_KINDS = {
    Trigger.FLIP: "FLIP",
    Trigger.ACTIVATE: "\u3010Activate\u3011",
    Trigger.STAGE_ACTIVATE: "stage",
    Trigger.ATTACK: "attack effect",
    Trigger.ATTACK_START: "attack effect",
    Trigger.ON_PLAY: "\u3010On Play\u3011",
    Trigger.FAINT: "faint effect",
    Trigger.TRASHED: "trashed effect",
    Trigger.WHEN_ATTACKED: "when attacked",
    Trigger.SURVIVED_DAMAGE: "survived damage",
    Trigger.END_TURN: "end of turn",
    Trigger.PLAYED_FROM_TRASH: "\u3010On Play\u3011",
    Trigger.PLAYED_FROM_SUPPORT: "\u3010On Play\u3011",
    Trigger.PLAYED_FROM_BREAK: "\u3010On Play\u3011",
}

# Trigger.ITEM is the shared body of an ITEM and a TRAP, and those two are the
# pair a player most wants told apart — a trap is the one that fired on their
# own turn. So that trigger asks the card instead.
CARD_TYPE_KINDS = {
    CardType.TRAP: "trap",
    CardType.ITEM: "item",
    CardType.STAGE: "stage",
}


def source_kind(db: CardDB, card: CardInstance | None, trigger: Trigger) -> str:
    """One short word for what is resolving. Empty when there is nothing useful
    to add — an unlabelled effect reads better than one labelled "effect"."""
    named = TRIGGER_KINDS.get(trigger)
    if named:
        return named
    if card is not None:
        return CARD_TYPE_KINDS.get(db[card.card_id].type, "")
    return ""


# The setup question that puts your first Cookie on the board. Named here
# because the front ends key their presentation off it: the viewer answers it
# by raising the playable Cookies out of your hand rather than by listing them.
OPENING_COOKIE_PROMPT = "Opening Cookie"


@dataclass
class BankedUntap:
    """One "when your turn ends, set N cards as active" rider, waiting to fire.

    An attack rider does not resolve where it is written — it is banked and
    happens as the turn ends, alongside every 【End of Turn】 effect. Giving it
    an object rather than a running total is what lets it sit in that queue as
    its own item: named after the card that banked it, and orderable against
    the others.
    """

    card_id: str
    amount: int
    name: str

    def __str__(self) -> str:
        return f"{self.name}: set {self.amount} support card(s) as active"


class Game:
    def __init__(
        self,
        decks: Sequence[Sequence[str]],
        controllers: Sequence,
        *,
        extra_decks: Sequence[Sequence[str]] | None = None,
        db: CardDB | None = None,
        rules: cfg.RulesConfig = cfg.DEFAULT,
        seed: int | None = None,
        first_player: int = 0,
        max_turns: int = 200,
        max_actions_per_turn: int = 60,
        shuffle: bool = True,
    ):
        self.db = db or default_db()
        self.rules = rules
        # Who takes the first turn. The PLAY GUIDE settles this with rock,
        # paper, scissors before the game starts (see `braverse/rps.py`); the
        # engine just needs to be told the answer.
        self.first_player = first_player
        # A stacked deal: `shuffle=False` deals off the top of the list exactly
        # as it was given, and nothing that returns cards to the deck reorders
        # it either. Only the guided first game uses it — a tutorial step that
        # says "play a Cookie" needs a Cookie to be there — and `braverse/
        # tutorial.py` is where the stacked lists live. Every other caller gets
        # the shuffle, so no self-play or training number moves.
        self.shuffling = shuffle
        self.max_turns = max_turns
        self.max_actions_per_turn = max_actions_per_turn
        self._actions_this_turn = 0
        self._controllers = list(controllers)
        self._deck_lists = [list(d) for d in decks]
        # The EXTRA deck is a second, separate pile. Callers that predate it
        # pass nothing and play with none, which is a legal way to build.
        self._extra_lists = [list(e) for e in (extra_decks or [])]
        while len(self._extra_lists) < len(self._deck_lists):
            self._extra_lists.append([])
        self.state = GameState(
            players=[PlayerState(index=0), PlayerState(index=1)],
            rng=random.Random(seed),
        )
        # Set while an attack is being resolved, so the defender's response
        # window knows what it is responding to.
        self._pending_attack: tuple[Cookie, Cookie] | None = None
        self._attack_target: Cookie | None = None
        self._attacking_cookie: Cookie | None = None
        # Cookies mid-faint, so a faint trigger that empties its own HP
        # cannot re-enter and re-run itself.
        self._fainting: set = set()
        self._response_player: int | None = None
        self._trap_used = 0
        # "trap" or "block" once the defender has answered this attack; either
        # one rules the other out for the rest of the window.
        self._responded: str | None = None
        # Non-zero while a Cookie is mid-arrival and the "your battle area is
        # empty" check must wait for it. See `_holding_refill`.
        self._refill_held = 0

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------
    def controller(self, index: int):
        return self._controllers[index]

    def _shuffle(self, cards: list) -> None:
        """Shuffle, unless this game was dealt from a stacked deck."""
        if self.shuffling:
            self.state.rng.shuffle(cards)

    def setup(self) -> None:
        state = self.state
        for player, deck_list, extra_list in zip(state.players, self._deck_lists,
                                                 self._extra_lists):
            player.deck = [CardInstance.make(cid, player.index) for cid in deck_list]
            self._shuffle(player.deck)
            # The EXTRA deck is not shuffled and not drawn from: every card in
            # it is visible to its owner all game, and is played out of the
            # pile directly when its gate opens.
            player.extra_deck = [CardInstance.make(cid, player.index)
                                 for cid in extra_list]
            self._draw_opening_hand(player)
        # 5-2-1-5: "starting with the first player". With seat 0 opening this
        # is the order it always was, so no existing recording moves.
        seating = [state.players[self.first_player],
                   state.players[1 - self.first_player]]
        for player in seating:
            self._offer_mulligan(player)
        for player in seating:
            self._redraw_until_cookie(player)
        for player in seating:
            self._place_opening_cookie(player)
        state.phase = Phase.ACTIVE
        state.turn_number = 1
        state.turn_player = self.first_player
        self._begin_turn()

    def _draw_opening_hand(self, player: PlayerState) -> None:
        for _ in range(self.rules.opening_hand):
            if player.deck:
                player.hand.append(player.deck.pop(0))

    def _offer_mulligan(self, player: PlayerState) -> None:
        """The opening redraw: one free, then as many as a Cookie-less hand needs.

        The first one costs nothing — the whole hand goes back, the deck is
        shuffled and a fresh hand comes off the top. After that the offer stays
        open only while the hand still holds no Cookie, because a hand you
        cannot open with is not a hand; each of those later redraws hands the
        opponent one card, the same price the guide puts on the mandatory
        Cookie-less redraw. Mulliganing a *playable* hand twice is not on offer:
        the free one is the whole allowance for shopping around.

        It runs *before* `_redraw_until_cookie`, which stays as the floor —
        declining here with no Cookie in hand is not a way to keep an
        unplayable one, it just has the redraw done for you at the same price.

        Only a controller that implements `wants_mulligan` is asked. Scripted
        agents have no read on hand quality, so answering for them would replace
        every opening hand in every self-play game with a random one and move
        every number this project measures, for no gain in play strength.
        """
        if not self.rules.allow_mulligan:
            return
        ask = getattr(self.controller(player.index), "wants_mulligan", None)
        if ask is None:
            return
        opponent = self.state.opponent_of(player.index)
        free = True
        for _ in range(self.rules.max_mulligans):
            if not free and self.playable_cookies(player):
                return
            if not ask(self.state, list(player.hand), free=free):
                return
            self._shuffle(player.hand)   # the hand goes back unordered
            player.deck.extend(player.hand)
            player.hand.clear()
            self._shuffle(player.deck)
            self._draw_opening_hand(player)
            if free:
                self.state.record(f"mulligan: P{player.index} draws a new hand")
            else:
                drawn = self.draw(opponent, self.rules.opponent_draws_on_redraw)
                self.state.record(
                    f"mulligan: P{player.index} had no Cookie and draws a new "
                    f"hand; P{opponent.index} draws {drawn}")
            free = False

    def _redraw_until_cookie(self, player: PlayerState) -> None:
        """"If a player does not have a Cookie card in their hand, they must
        reveal their hand, return it to the deck, and draw 6 new cards. The
        opponent may draw 1 card from the deck." Repeated until they have one."""
        if not self.rules.redraw_until_cookie:
            return
        opponent = self.state.opponent_of(player.index)
        for _ in range(20):
            if self.playable_cookies(player):
                return
            player.deck.extend(player.hand)
            player.hand.clear()
            self._shuffle(player.deck)
            self._draw_opening_hand(player)
            self.draw(opponent, self.rules.opponent_draws_on_redraw)
        self._lose(player.index, "could not draw an opening Cookie")

    def _place_opening_cookie(self, player: PlayerState) -> None:
        """Step 5: each player places 1 Cookie card face down, then reveals it
        and builds its HP pile. [On Play] effects do not fire during setup."""
        options = self.playable_cookies(player)
        if not options:
            self._lose(player.index, "no Cookie to open with")
            return
        card = self.controller(player.index).choose(
            self.state, OPENING_COOKIE_PROMPT, options, optional=False
        ) or options[0]
        player.hand.remove(card)
        self._deploy_cookie(player, card, run_on_play=False)

    # ------------------------------------------------------------------
    # turn structure
    # ------------------------------------------------------------------
    def _begin_turn(self) -> None:
        state = self.state
        player = state.current

        # Active phase.
        state.phase = Phase.ACTIVE
        for card in player.support + player.stage:
            if card.uid in player.support_skip_untap:
                player.support_skip_untap.discard(card.uid)
            else:
                card.rested = False
        for cookie in player.battle:
            cookie.effect_damage_immune = False
            cookie.damage_immune = False
            cookie.flip_disabled = False
            cookie.hp_cannot_reach_zero = False
            cookie.damage_cap = None
            cookie.attack_cost_discount = 0
            cookie.attack_cost_surcharge = 0
            cookie.level_override = None
            cookie.activate_locked = False
            cookie.effect_damage_reduction = 0
            cookie.all_damage_reduction = 0
            if cookie.skip_next_active:
                # "That Cookie is not set as active during your opponent's next
                # Active Phase" — it stays rested for exactly one phase.
                cookie.skip_next_active = False
            else:
                cookie.rested = False
            cookie.summoned_this_turn = False
            # A debuff written "during your opponent's next turn" is banked
            # here and only takes effect when that turn actually begins.
            cookie.attack_bonus = cookie.attack_bonus_next_turn
            cookie.attack_bonus_next_turn = 0
            cookie.hp_reduced_this_turn = False
            cookie.attack_cost_all_generic = False
            cookie.used_markers.clear()
        player.supported_this_turn = False
        player.left_support_phase = False
        player.extra_played_this_turn = False
        player.activated_this_turn.clear()
        player.items_played_this_turn = 0
        self._actions_this_turn = 0
        player.hp_gained_this_turn = False
        player.played_from_break_this_turn.clear()
        player.played_from_trash_this_turn.clear()
        player.support_trashed_this_turn = 0
        player.cookies_to_deck_bottom_this_turn = 0
        player.cookies_to_deck_this_turn = 0
        player.hp_gain_locked = False
        state.opponent.blockers_disabled = False
        player.blockers_disabled = False
        state.opponent.traps_disabled = False
        player.traps_disabled = False
        for side in state.players:
            side.effect_damage_dealt_this_turn = False
            side.arena_effect_damage_this_turn = False
        for side in state.players:
            side.cookies_fainted_this_turn = 0
            side.break_additions_this_turn = 0
            side.arena_break_additions_this_turn = 0
            for cookie in side.battle:
                cookie.hp_reduced_this_turn = False
        for opp_cookie in state.opponent.battle:
            opp_cookie.attack_bonus = opp_cookie.attack_bonus_next_turn
            opp_cookie.attack_bonus_next_turn = 0

        # Draw phase.
        state.phase = Phase.DRAW
        skip = (
            self.rules.first_player_skips_first_draw
            and state.turn_number == 1
            and state.turn_player == self.first_player
        )
        if not skip:
            self.draw(player, self.rules.draw_per_turn, optional=False)

        state.phase = Phase.MAIN

    def end_turn(self) -> None:
        state = self.state
        state.phase = Phase.END
        pending = self._end_turn_sources(state.current)
        while pending:
            source = self._pick_end_turn_source(state.current, pending)
            pending.remove(source)
            if isinstance(source, BankedUntap):
                self._resolve_banked_untap(state.current, source)
            elif isinstance(source, Cookie):
                self._run_cookie_effect(source, Trigger.END_TURN, state.current)
            else:
                self._run_effect(source, Trigger.END_TURN, state.current)
        if state.over:
            return
        state.turn_player = 1 - state.turn_player
        state.turn_counter += 1
        if state.turn_player == self.first_player:
            state.turn_number += 1
        if state.turn_number > self.max_turns:
            self._draw_game("turn limit")
            return
        self._begin_turn()

    # ------------------------------------------------------------------
    # legal actions
    # ------------------------------------------------------------------
    def legal_actions(self) -> list[A.Action]:
        state = self.state
        if state.over:
            return []
        if self._response_player is not None:
            return self._response_actions(state.players[self._response_player])
        return self._main_actions(state.current)

    def _end_turn_sources(self, player: PlayerState) -> list:
        """Everything of this player's waiting to happen as the turn ends.

        Stage cards, then Cookies, then the "when your turn ends, set N cards
        as active" riders banked by attacks earlier in the turn — which is the
        order they used to resolve in when there was no choice about it. The
        riders are events of the same turn-ending step as the rest, so they
        queue up with them and can be ordered against them.
        """
        out: list = []
        for card in list(player.stage):
            if get_effect(self.db[card.card_id].id, Trigger.END_TURN) is not None:
                out.append(card)
        for cookie in list(player.battle):
            if get_effect(cookie.defn(self.db).id, Trigger.END_TURN) is not None:
                out.append(cookie)
        banked = list(player.end_turn_untaps)
        player.end_turn_untaps.clear()
        for card_id, amount in banked:
            name = self.db[card_id].name if card_id in self.db else "an attack"
            out.append(BankedUntap(card_id=card_id, amount=amount, name=name))
        return out

    def _resolve_banked_untap(self, player: PlayerState,
                              banked: "BankedUntap") -> None:
        """Set up to N of this player's rested support cards as active."""
        count = banked.amount
        for card in player.support:
            if count <= 0:
                break
            if card.rested:
                card.rested = False
                count -= 1

    def _pick_end_turn_source(self, player: PlayerState, pending: list):
        """Which end-of-turn effect resolves next.

        Several of them going off at once is the turn player's ordering
        decision, and the order can matter — one can trash the Cookie another
        was going to buff. A controller that does not care to order them
        (every bot) has no `order_effects` and keeps board order, so seeded
        self-play stays bit-identical.
        """
        if len(pending) == 1:
            return pending[0]
        hook = getattr(self.controller(player.index), "order_effects", None)
        if hook is None:
            return pending[0]
        # Only the ones that would actually do something are worth ordering;
        # the rest resolve into nothing whenever they are reached.
        live = [s for s in pending if self._end_turn_source_is_live(player, s)]
        if len(live) < 2:
            return live[0] if live else pending[0]
        picked = hook(self.state, "Resolve which end-of-turn effect first?", live)
        return picked if picked in pending else live[0]

    def _end_turn_source_is_live(self, player: PlayerState, source) -> bool:
        """Whether a queued end-of-turn event has anything left to accomplish."""
        if isinstance(source, BankedUntap):
            return any(card.rested for card in player.support)
        return self._would_do_something(
            player, Trigger.END_TURN,
            cookie=source if isinstance(source, Cookie) else None,
            card=None if isinstance(source, Cookie) else source)

    def _is_first_turn(self) -> bool:
        return (self.state.turn_number == 1
                and self.state.turn_player == self.first_player)

    def to_move(self) -> int:
        if self._response_player is not None:
            return self._response_player
        return self.state.turn_player

    def response_window(self) -> tuple[Cookie, Cookie] | None:
        """The attack the defender is being asked about, as ``(attacker,
        target)``, or None when no attack is waiting on an answer.

        Public because a question asked inside this window is not the same
        question as a turn: the seat being asked is not the seat whose turn it
        is, and the *other* player is sitting there watching an attack they
        have already declared. A UI that cannot tell the two apart shows both
        of them "your move" and neither of them why they are waiting.
        """
        if self._response_player is None:
            return None
        return self._pending_attack

    def _would_do_something(self, player: PlayerState, trigger: Trigger, *,
                            card: CardInstance | None = None,
                            cookie: Cookie | None = None) -> bool:
        """Whether a card's effect has anything to accomplish right now.

        An effect whose condition is false, whose target does not exist or whose
        bracketed cost cannot be met resolves into nothing at all, and offering
        it as a move is the engine telling the player something untrue. This is
        what keeps such a move off the list.

        Cards the engine cannot read this way stay on offer — including a card
        with no implementation at all, whose blank is the engine's gap rather
        than something the rules say. See `effects.effect_is_live`.
        """
        source = cookie.card if cookie is not None else card
        if source is None:
            return True
        fn = get_effect(self.db[source.card_id].id, trigger)
        if fn is None:
            return True
        ctx = self._ctx(player, source_cookie=cookie, source_card=source,
                        trigger=trigger.value)
        # An item or trap is still in hand while the action list is built, but
        # is gone from it by the time its effect runs. Conditions that count the
        # hand — "if there are 5 cards or less", "<Discard 3 cards.>" — would
        # otherwise be probed against a hand one card larger than the real one.
        try:
            slot = player.hand.index(source)
        except ValueError:
            return effect_is_live(fn, ctx)
        player.hand.pop(slot)
        try:
            return effect_is_live(fn, ctx)
        finally:
            player.hand.insert(slot, source)

    def _main_actions(self, player: PlayerState) -> list[A.Action]:
        out: list[A.Action] = [A.EndTurn()]
        db = self.db
        _, colors = player.active_support_colors(db)

        if not player.supported_this_turn and not (
                self.rules.support_only_before_main_actions
                and player.left_support_phase):
            for card in player.hand:
                out.append(A.PlaceSupport(card.uid))

        for card in player.hand:
            defn = db[card.card_id]
            if defn.is_cookie:
                out.extend(self._cookie_plays(player, card, defn))
            elif defn.type in (CardType.ITEM, CardType.STAGE):
                if plan_payment(defn.play_cost, colors) is None:
                    continue
                # A stage is worth placing for its own sake; its 【Activate】 is
                # a separate move, gated separately below. An item is only its
                # effect, so an item that would fizzle is not a move at all.
                if (defn.type is CardType.STAGE
                        or self._would_do_something(player, Trigger.ITEM, card=card)):
                    out.append(A.PlaySupportCard(card.uid))

        out.extend(self._extra_plays(player))

        for cookie in player.battle:
            defn = cookie.defn(db)
            # Simplification: every 【Activate】 skill is treated as once per
            # turn per source. Printed 【Once Per Turn】 skills already are, and
            # the repeatable ones all carry a cost the engine cannot yet prove
            # was paid — without this the action list never terminates.
            if (defn.has(Marker.ACTIVATE)
                    and not cookie.activate_locked
                    and get_effect(defn.id, Trigger.ACTIVATE)
                    and cookie.uid not in player.activated_this_turn
                    and not (cookie.rested and "Rest this card" in defn.description)
                    and self._would_do_something(player, Trigger.ACTIVATE,
                                                 cookie=cookie)):
                out.append(A.ActivateSkill(cookie.uid))
            if self._can_attack(player, cookie, colors):
                defender = self.state.opponent_of(player.index)
                forced = forced_attack_target(db, defender)
                targets = [forced] if forced is not None else defender.battle
                for target in targets:
                    out.append(A.Attack(cookie.uid, target.uid))

        for card in player.stage:
            defn = db[card.card_id]
            if (not card.rested
                    and card.uid not in player.activated_this_turn
                    and get_effect(defn.id, Trigger.STAGE_ACTIVATE)
                    and self._would_do_something(player, Trigger.STAGE_ACTIVATE,
                                                 card=card)):
                out.append(A.ActivateSkill(card.uid))

        return out

    def _extra_plays(self, player: PlayerState) -> list[A.Action]:
        """The 【EXTRA】 cards whose gate is open right now.

        An EXTRA card is never drawn, so this is the only way it enters the
        game. The gate is a hard condition rather than a cost — a card whose
        "Can be played if ..." is false is not a move at all — which keeps the
        rule that a move on offer is a move that does something.
        """
        out: list[A.Action] = []
        # 6-5-2-2: "The Turn Player can, ONCE PER TURN, play an 【EXTRA】 Cookie
        # card or 【Awakened】 Cookie card." The gate on each card is its own
        # condition; this is the limit on top of all of them, and without it a
        # turn that opened two gates emptied half the EXTRA deck onto the board.
        if player.extra_played_this_turn:
            return out
        for card in player.extra_deck:
            play = extra_play_of(self.db, card.card_id)
            if play is None:
                continue        # an EXTRA card the engine cannot read yet
            ctx = self._ctx(player, source_card=card)
            if not play.gate(ctx):
                continue
            if play.is_awaken:
                out.extend(A.PlayExtra(card.uid, onto=host.uid)
                           for host in play.hosts(ctx))
            elif len(player.battle) < self.rules.max_battle_cookies:
                out.append(A.PlayExtra(card.uid))
        return out

    def can_play_cookie(self, player: PlayerState, card: CardInstance) -> bool:
        """Whether this card in hand could actually be placed in the battle area.

        Three things can stop it, and every question about "a Cookie card in
        your hand to play" has to ask all three or it is asking something
        weaker. It has to be a Cookie; it must not be an 【EXTRA】 one, which
        may only ever be played out of the EXTRA deck (6-5-2-3); and a
        【Special Play】 Cookie is playable only while its printed line can be
        honoured (4-10-1-1).

        The battle-area cap is deliberately *not* part of this: the two callers
        that ask about a *free* slot check it themselves, and the defeat
        condition (1-2-1-1-2) is asked precisely when the battle area is empty.
        """
        defn = self.db[card.card_id]
        if not defn.is_cookie or defn.type is CardType.EXTRA:
            return False
        if not may_play(self.db, player,
                        self.state.opponent_of(player.index), defn):
            return False
        if defn.has(Marker.SPECIAL_PLAY):
            play = special_play_of(self.db, defn.id)
            if play is None:
                return False
            return bool(play.gate(self._ctx(player, source_card=card)))
        return True

    def playable_cookies(self, player: PlayerState) -> list[CardInstance]:
        """The Cookie cards in hand that could be put into the battle area."""
        return [c for c in player.hand if self.can_play_cookie(player, c)]

    def _cookie_plays(self, player: PlayerState, card: CardInstance,
                      defn: CardDef) -> list[A.Action]:
        """Any Cookie may be played into a free battle slot, at no cost.

        Except an 【EXTRA】 one, which is never played from hand at all: it
        lives in its own pile and comes out through `_extra_plays`, past the
        "can be played if ..." gate printed on it. `validate` keeps EXTRA cards
        out of the 60 so one should not be in a hand to begin with — but
        `CardType.EXTRA.is_cookie` is True, so without this guard anything that
        did put one there, a bounce off an 【Awaken】 among them, would hand the
        player a free Cookie with its entry condition skipped. That is what
        BS9-102 was doing before its type was fixed: "can be played if there
        are 20 cards or more in each player's trash", droppable on turn one.
        """
        if not self.can_play_cookie(player, card):
            return []
        # 3-5-6-1-1: a 【Special Play】 condition that empties battle slots
        # empties them *before* the Cookie arrives, so a full battle area is
        # not in its way — Dark Enchantress trashes the two Cookies she is
        # standing on.
        play = (special_play_of(self.db, defn.id)
                if defn.has(Marker.SPECIAL_PLAY) else None)
        frees = play.frees if play is not None else 0
        if len(player.battle) - frees >= self.rules.max_battle_cookies:
            return []
        return [A.PlayCookie(card.uid, None)]

    def _can_attack(self, player: PlayerState, cookie: Cookie,
                    colors: list[Color]) -> bool:
        if cookie.rested:
            return False
        if cannot_attack(self.db, cookie):
            return False
        if self.rules.first_turn_cannot_attack and self._is_first_turn():
            return False
        if self.rules.summoning_sickness and cookie.summoned_this_turn:
            return False
        attack = cookie.defn(self.db).attack
        if attack is None:
            return False
        if not self.state.opponent_of(player.index).battle:
            return False
        cost = modified_attack_cost(self.db, player, cookie, attack.cost)
        return plan_payment(cost, colors) is not None

    def _response_actions(self, player: PlayerState) -> list[A.Action]:
        """What the defender may do about the attack being declared at them.

        A trap and a block are alternatives, not a combination: taking either
        one closes the other off for the rest of this attack. `_responded`
        carries which was taken.
        """
        out: list[A.Action] = [A.Pass()]
        db = self.db
        _, colors = player.active_support_colors(db)
        if (self._responded is None
                and self._trap_used < self.rules.traps_per_attack
                and not player.traps_disabled):
            for card in player.hand:
                defn = db[card.card_id]
                if (defn.type is CardType.TRAP
                        and plan_payment(defn.play_cost, colors) is not None
                        and self._would_do_something(player, Trigger.ITEM, card=card)):
                    out.append(A.PlayTrap(card.uid))
        if self._pending_attack and self._responded is None:
            _, target = self._pending_attack
            for cookie in player.battle:
                # 10-1-1-1: 【Blocker】 redirects an attack aimed at "one of
                # your Cookie cards *other than* the 【Blocker】 card".
                if cookie.uid == target.uid:
                    continue
                if player.blockers_disabled:
                    break
                price = self._blocker_cost(cookie)
                if price is None or plan_payment(price[0], colors) is None:
                    continue
                # Being rested only stops the five Cookies whose price *is*
                # resting themselves. The rules ask for the activation cost and
                # nothing else, so a rested Cookie with an energy price is
                # still allowed to step in front of the swing.
                if price[1] and cookie.rested:
                    continue
                out.append(A.Block(cookie.uid))
        return out

    def _blocker_cost(self, cookie: Cookie) -> tuple[Cost, bool] | None:
        """What redirecting an attack to this Cookie costs, as printed.

        Returns (energy, rests itself), or None if the Cookie has no
        【Blocker】. Five cards price the block as `<Rest this card.>` rather
        than in energy; that half used to be dropped, so those Cookies blocked
        every attack in a turn for free and were still standing to attack on
        their own. The reading itself lives in `cards.blocker_price`, because
        the deck pool has to agree with it about which prices are payable.
        """
        return blocker_price(cookie.defn(self.db))

    # ------------------------------------------------------------------
    # applying actions
    # ------------------------------------------------------------------
    def step(self, action: A.Action) -> None:
        if self.state.over:
            return
        # Backstop against an agent (or a no-op action) spinning inside a turn.
        self._actions_this_turn = getattr(self, "_actions_this_turn", 0) + 1
        if self._actions_this_turn > self.max_actions_per_turn:
            self.end_turn()
            return
        # The Support Phase ends the moment a Main Phase action is taken; from
        # then on the support placement is no longer on offer (6-1-1, 6-5-1).
        if not isinstance(action, (A.PlaceSupport, A.EndTurn)):
            self.state.current.left_support_phase = True
        handler = {
            A.PlaceSupport: self._do_place_support,
            A.PlayCookie: self._do_play_cookie,
            A.PlayExtra: self._do_play_extra,
            A.PlaySupportCard: self._do_play_support_card,
            A.ActivateSkill: self._do_activate,
            A.Attack: self._do_attack,
            A.EndTurn: lambda _: self.end_turn(),
        }.get(type(action))
        if handler is None:
            raise ValueError(f"cannot apply {action!r} outside a response window")
        handler(action)

    def _do_place_support(self, action: A.PlaceSupport) -> None:
        player = self.state.current
        card = self._take_from_hand(player, action.card_uid)
        card.rested = False
        player.support.append(card)
        player.supported_this_turn = True
        self.state.record(f"supports {card_label(self.db[card.card_id])}")

    def _do_play_cookie(self, action: A.PlayCookie) -> None:
        player = self.state.current
        card = self._peek_hand(player, action.card_uid)
        defn = self.db[card.card_id]
        play = (special_play_of(self.db, defn.id)
                if defn.has(Marker.SPECIAL_PLAY) else None)
        if play is None:
            self._take_from_hand(player, action.card_uid)
            self._deploy_cookie(player, card, onto=action.onto)
            return
        # 【Special Play】: the printed line is paid on the way in, and paying
        # it can empty the battle area. The refill prompt is held off until
        # this Cookie is down, because this Cookie *is* the refill — asking
        # first would let a replacement take the slot the condition just made.
        with self._holding_refill(player):
            ctx = self._ctx(player, source_card=card)
            # Re-checked on the way in, the way an EXTRA gate is: the board can
            # have moved since the action list was built.
            if not play.gate(ctx) or not play.pay(self._ctx(player, source_card=card)):
                return
            if self.state.over or card not in player.hand:
                return
            self._take_from_hand(player, action.card_uid)
            self._deploy_cookie(player, card, onto=action.onto)

    @contextlib.contextmanager
    def _holding_refill(self, player: PlayerState):
        """Hold off the empty-battle-area check while a Cookie is arriving.

        A 【Special Play】 condition empties battle slots as its price, so for
        the length of one play the battle area is legitimately empty with a
        Cookie already on its way into it. Checking there and then would ask
        for a replacement that is not needed — or, with an empty hand behind
        it, end the game a player has not lost. The check runs once on the way
        out, which covers the case where the condition could not be finished.
        """
        self._refill_held += 1
        try:
            yield
        finally:
            self._refill_held -= 1
        if self._refill_held == 0 and not self.state.over:
            self._check_battle_area(player)

    def _do_play_extra(self, action: A.PlayExtra) -> None:
        player = self.state.current
        card = next((c for c in player.extra_deck if c.uid == action.card_uid), None)
        if card is None:
            return
        play = extra_play_of(self.db, card.card_id)
        if play is None or player.extra_played_this_turn:
            return
        ctx = self._ctx(player, source_card=card)
        # Re-check on the way in: the gate was true when the list was built,
        # but a response window between then and now could have closed it.
        if not play.gate(ctx):
            return
        host = player.find_cookie(action.onto) if action.onto is not None else None
        if play.is_awaken and host not in play.hosts(ctx):
            return
        if play.pay is not None and not play.pay(ctx):
            return
        player.extra_deck.remove(card)
        player.extra_played_this_turn = True
        defn = self.db[card.card_id]
        if host is not None:
            self._awaken(player, host, card)
        else:
            self.state.record(f"plays {card_label(defn)} from the EXTRA deck")
            self._deploy_cookie(player, card, from_zone="extra")

    def _awaken(self, player: PlayerState, host: Cookie,
                card: CardInstance) -> None:
        """Stack an 【EXTRA】 card on top of a Cookie already in the battle area.

        The Cookie keeps the HP it has left and gains the card's printed HP
        *modifier* on top — that is what "+1" on an EXTRA card means, and it is
        why an 【Awaken】 is worth taking on a Cookie that has been chipped down
        rather than a fresh one. Everything else about the Cookie now reads off
        the new card: name, Level, attack, skills.
        """
        defn = self.db[card.card_id]
        under = self.db[host.card.card_id]
        host.under.append(host.card)
        host.card = card
        host.used_markers.clear()          # a new card, so its 【Once Per Turn】 resets
        player.activated_this_turn.discard(host.uid)
        gain = defn.hp or 0
        if not defn.hp_is_modifier:
            # A full HP value rather than a modifier: top the pile up to it.
            gain = max(0, gain - host.remaining_hp)
        self._fill_hp(player, host, host.remaining_hp + gain)
        self.state.record(f"awakens {card_label(under)} \u2192 {card_label(defn)}")
        self._run_cookie_effect(host, Trigger.ON_PLAY, player)

    def _deploy_cookie(self, player: PlayerState, card: CardInstance,
                       onto: int | None = None, *,
                       run_on_play: bool = True,
                       from_zone: str = "hand") -> Cookie:
        """Place a Cookie from hand into a free battle slot. Always free."""
        defn = self.db[card.card_id]
        cookie = Cookie(uid=card.uid, owner=player.index, card=card)
        player.battle.append(cookie)
        self._fill_hp(player, cookie, defn.hp or 0)
        cookie.summoned_this_turn = True
        cookie.rested = False
        self.state.record(f"plays {card_label(defn)}")
        if from_zone == "extra":
            pass            # nothing extra fires: the EXTRA card's own gate was the price
        elif from_zone == "trash":
            player.played_from_trash_this_turn.add(cookie.uid)
            self._run_cookie_effect(cookie, Trigger.PLAYED_FROM_TRASH, player)
        elif from_zone == "support":
            self._run_cookie_effect(cookie, Trigger.PLAYED_FROM_SUPPORT, player)
        elif from_zone == "break":
            player.played_from_break_this_turn.add(cookie.uid)
            self._run_cookie_effect(cookie, Trigger.PLAYED_FROM_BREAK, player)
        if run_on_play:
            self._run_cookie_effect(cookie, Trigger.ON_PLAY, player)
        return cookie

    def _fill_hp(self, player: PlayerState, cookie: Cookie, target_hp: int) -> None:
        while len(cookie.hp_cards) < target_hp:
            if not player.deck and not self._refresh(player):
                return
            card = player.deck.pop(0)
            card.face_up = False
            cookie.hp_cards.append(card)

    def _do_play_support_card(self, action: A.PlaySupportCard) -> None:
        player = self.state.current
        defn = self.db[self._peek_hand(player, action.card_uid).card_id]
        if not self.pay_cost(player, defn.play_cost):
            return
        card = self._take_from_hand(player, action.card_uid)
        if defn.type is CardType.STAGE:
            for old in list(player.stage):
                player.stage.remove(old)
                player.trash.append(old)
            card.rested = False
            player.stage.append(card)
            self.state.record(f"places stage {card_label(defn)}")
        else:
            player.items_played_this_turn += 1
            self.state.record(f"activates {card_label(defn)}")
            self._run_effect(card, Trigger.ITEM, player)
            # An item that placed itself somewhere — "place this card in your
            # support area as rested" — has already chosen its zone. Filing it
            # in the trash as well would leave one CardInstance in two zones.
            if (self.state.find_card(card.uid) is None
                    and not self.state.is_attached(card.uid)):
                player.trash.append(card)

    def _do_activate(self, action: A.ActivateSkill) -> None:
        player = self.state.current
        player.activated_this_turn.add(action.source_uid)
        cookie = player.find_cookie(action.source_uid)
        if cookie is not None:
            # Logged before it resolves, so the skill is named even when what it
            # does is invisible — a draw, a buff, an effect that fizzles.
            self.state.record(f"activates {cookie.label(self.db)}")
            self._run_cookie_effect(cookie, Trigger.ACTIVATE, player)
            cookie.used_markers.add(Trigger.ACTIVATE.value)
            return
        card = next((c for c in player.stage if c.uid == action.source_uid), None)
        if card is not None:
            self.state.record(f"activates {card_label(self.db[card.card_id])}")
            self._run_effect(card, Trigger.STAGE_ACTIVATE, player)

    # ------------------------------------------------------------------
    # combat
    # ------------------------------------------------------------------
    def _do_attack(self, action: A.Attack) -> None:
        state = self.state
        player = state.current
        defender = state.opponent_of(player.index)
        attacker = player.find_cookie(action.attacker_uid)
        target = defender.find_cookie(action.target_uid)
        if attacker is None or target is None:
            return
        attack = attacker.defn(self.db).attack
        if attack is None:
            return
        cost = modified_attack_cost(self.db, player, attacker, attack.cost)
        if not self.pay_cost(player, cost):
            return
        # Static 【Your Turn】 buffs read the board as the attack is declared.
        self._run_cookie_effect(attacker, Trigger.ATTACK_START, player)
        attacker.rested = True
        # 7-1-1-3: "At the end of the Attack Step, if the attacking card or the
        # attacked card was moved to another zone, the Trap Step is skipped and
        # the players proceed to the End Battle Step." An attack rider that
        # bounces its own Cookie, or takes the target off the board, ends the
        # battle here — there is nothing left to defend against and nothing
        # left to swing.
        if (state.over or attacker not in player.battle
                or target not in defender.battle):
            return
        # Named where the card names it — 980 of the Cookies print a name for
        # their attack, and "attacks for 3" told you which Cookie swung but not
        # which of its lines did.
        swing = f" with {attack.name}" if attack.name else ""
        state.record(f"{attacker.label(self.db)} attacks {target.label(self.db)}"
                     f"{swing} for {attacker.attack_damage(self.db)}")

        target = self._response_window(defender, attacker, target)
        # 7-1-2-2: the same test at the end of the Trap Step. The target
        # leaving was already handled — a trap that bounces it returns None —
        # but a trap that removes the *attacker* used to leave a Cookie that is
        # no longer on the board dealing its printed damage.
        if state.over or target is None or attacker not in player.battle:
            return

        target.incoming_damage_reduction = 0
        self._attacking_cookie = attacker
        self._run_cookie_effect(target, Trigger.WHEN_ATTACKED, defender)
        if self.state.over or target not in defender.battle:
            return

        swing = attacker.attack_damage(self.db)
        damage = max(0, swing - target.incoming_damage_reduction)
        if target.damage_cap is not None:
            # "attack damage of N or more ... is reduced to N-1" is a ceiling,
            # not a subtraction.
            damage = min(damage, target.damage_cap)
        if damage != swing:
            # The swing was announced at its printed number a moment ago. If a
            # trap or a defensive skill has shaved it since, say so — otherwise
            # the log reads as an attack that mysteriously underperformed.
            self.state.record(f"{attacker.label(self.db)}'s attack is reduced "
                              f"to {damage} (from {swing})")
        # Riders can ask "if your opponent's Cookie faints from this Cookie's
        # attack", so record the outcome before the rider runs.
        before = len(defender.break_area)
        if damage > 0:
            swung = f"'s {attack.name}" if attack.name else ""
            self.deal_damage(target, damage, source_player=player.index,
                             kind="attack",
                             source=f"{attacker.name(self.db)}{swung}",
                             source_label=f"{attacker.label(self.db)}{swung}")
        if target in defender.battle and target.hp_cards:
            self._run_cookie_effect(target, Trigger.SURVIVED_DAMAGE, defender)
        self._attack_target = target
        self._attack_killed = len(defender.break_area) > before
        if not state.over:
            self._run_cookie_effect(attacker, Trigger.ATTACK, player)
        self._check_battle_area(defender)

    def _response_window(self, defender: PlayerState, attacker: Cookie,
                         target: Cookie) -> Cookie | None:
        """Let the defender spring a trap *or* block. Returns the final target.

        One or the other, not both, and not one after the other: springing the
        trap is the answer to the attack, and so is putting a Cookie in the
        way. The window used to allow a trap and then a block on the same
        swing, which is two answers to one question.
        """
        self._pending_attack = (attacker, target)
        self._response_player = defender.index
        self._trap_used = 0
        self._responded = None
        blocked = False
        try:
            while True:
                options = self._response_actions(defender)
                if len(options) == 1:  # only Pass
                    break
                choice = self.controller(defender.index).choose_action(self.state, options)
                if isinstance(choice, A.Pass) or choice is None:
                    break
                if isinstance(choice, A.PlayTrap):
                    card = self._peek_hand(defender, choice.card_uid)
                    defn = self.db[card.card_id]
                    if not self.pay_cost(defender, defn.play_cost):
                        break
                    self._take_from_hand(defender, choice.card_uid)
                    self.state.record(f"springs trap {card_label(defn)}")
                    self._run_effect(card, Trigger.ITEM, defender)
                    defender.trash.append(card)
                    self._trap_used += 1
                    self._responded = "trap"
                elif isinstance(choice, A.Block) and not blocked:
                    blocker = defender.find_cookie(choice.blocker_uid)
                    price = self._blocker_cost(blocker) if blocker else None
                    if blocker is None or price is None:
                        break
                    cost, rest_self = price
                    if not self.pay_cost(defender, cost):
                        break
                    if rest_self:
                        blocker.rested = True
                    target = blocker
                    blocked = True
                    self._pending_attack = (attacker, target)
                    self._responded = "block"
                    self.state.record(f"{blocker.label(self.db)} blocks"
                                      + (" and rests" if rest_self else ""))
                else:
                    break
        finally:
            self._pending_attack = None
            self._response_player = None
            self._responded = None
        # A trap may have removed the target from the field.
        return target if target in defender.battle else None

    def deal_damage(self, cookie: Cookie, amount: int, *, source_player: int,
                    kind: str = "effect", source: str = "",
                    source_label: str = "") -> None:
        """Take ``amount`` HP off this Cookie, one card at a time, firing any
        FLIP as it turns.

        One card turned is one point of damage spent, and the hit runs until
        **either** the damage is spent **or** the Cookie is at 0. Those come
        apart because of healing: a FLIP that hands its host a card back as it
        turns puts the HP straight back on — the card that turned is spent
        either way, so a 4-damage hit into a 1 HP Cookie whose pile keeps
        healing turns four cards and can leave the Cookie standing.

        The count logged is what actually came off the pile, which is not
        always ``amount``: the pile can run dry, or an effect can take the
        target off the board mid-hit.
        """
        name = cookie.label(self.db)
        # The log names the card with its id; the floating damage number over
        # the board does not, so the two spellings travel separately.
        source_label = source_label or source
        by = f" from {source_label}" if source_label else ""
        if cookie.damage_immune:
            self.state.record(f"{name} takes no {kind} damage{by} (immune)")
            return
        from .effects import shields_from_opponent
        owner = self.state.players[cookie.owner]
        if (source_player != cookie.owner
                and shields_from_opponent(self.db, owner, self.state)):
            self.state.record(f"{name} takes no {kind} damage{by} (shielded)")
            return
        amount -= cookie.all_damage_reduction
        if amount <= 0:
            self.state.record(f"{name} takes no {kind} damage{by} (reduced)")
            return
        dealt = 0
        for _ in range(amount):
            # Out of cards is out of HP: the Cookie is down, and the rest of
            # the damage has nothing to land on.
            if not cookie.hp_cards:
                break
            card = cookie.hp_cards.pop()
            card.face_up = True
            owner.trash.append(card)
            dealt += 1
            cookie.hp_reduced_this_turn = True
            defn = self.db[card.card_id]
            # Recorded *before* the FLIP runs, so the viewer can turn the card
            # over and only then play whatever it did. A diff of the HP pile
            # cannot express that order — it only knows the card ended up in
            # the trash, by which time the flip has already resolved.
            self.record_reveal(cookie, card, flip=defn.is_flip)
            if defn.is_flip and not cookie.flip_disabled:
                self.state.record(f"FLIP! {card_label(defn)}")
                self._run_effect(card, Trigger.FLIP, owner, flip_host=cookie)
            # A flip effect can bounce or otherwise remove its own host;
            # the rest of the damage has nothing left to hit.
            if self.state.over or cookie not in owner.battle:
                self._record_damage(name, dealt, amount, None, kind, cookie, source,
                                    source_label)
                return
            # "This Cookie's HP cannot reach 0": the card just spent is
            # replaced off the deck rather than the hit being stopped one
            # short. The damage keeps turning cards — every FLIP in the pile
            # still fires — and the Cookie is still standing at the end of it.
            self.hold_the_floor(cookie)
        self._record_damage(name, dealt, amount, cookie, kind, cookie, source,
                            source_label)
        if not cookie.hp_cards:
            self._faint(cookie)

    def _record_damage(self, name: str, dealt: int, asked: int,
                       cookie: Cookie | None, kind: str = "effect",
                       target: Cookie | None = None, source: str = "",
                       source_label: str = "") -> None:
        """`kind` separates a swing from everything else — a "Then, ..." rider,
        a skill, a trap — so the log says which one hit you. `source` names
        the thing itself, for a swing: an effect already stamps its own name
        onto every line it writes, but nothing is "resolving" during an attack,
        so the Cookie that swung has to be named on the line.

        `dealt` is the cards that actually came off the pile, which is `asked`
        unless the pile ran dry or the target left the board mid-hit.
        """
        landed = min(dealt, asked)
        if target is not None and dealt:
            self.state.events.append({
                "kind": "damage",
                "cookie": target.uid,
                "owner": target.owner,
                "amount": landed,
                "turned": dealt,
                "source": kind,
                # What the viewer labels the floating number with: the name of
                # whatever hit, and one word for what sort of thing it was.
                "sourceName": source or self.state.source_name(),
                "sourceKind": self.state.source_kind() or kind,
                "left": target.remaining_hp if cookie is not None else 0,
            })
        source_label = source_label or source
        by = f" from {source_label}" if source_label else ""
        if dealt == 0:
            self.state.record(f"{name} takes no {kind} damage{by}")
            return
        line = f"{name} takes {landed} {kind} damage{by}"
        if dealt < asked:
            line += f" (of {asked})"
        if cookie is not None:
            line += f" — {cookie.remaining_hp} HP left"
        self.state.record(line)

    def hold_the_floor(self, cookie: Cookie) -> bool:
        """"That Cookie's HP cannot reach 0" — top the pile back up if it just
        emptied.

        Every path that can take a Cookie's last HP card calls this: damage,
        and "place N cards from the top of that Cookie's HP into the trash",
        which empties a pile just as surely without being damage. Returns True
        if a card was put back.
        """
        if not cookie.hp_cannot_reach_zero or cookie.hp_cards:
            return False
        owner = self.state.players[cookie.owner]
        self._fill_hp(owner, cookie, 1)
        if not cookie.hp_cards:
            return False            # the deck could not supply one
        self.state.record(f"{cookie.label(self.db)}'s HP cannot reach 0"
                          f" — 1 HP restored")
        self._record_heal(cookie, len(cookie.hp_cards))
        return True

    def record_reveal(self, cookie: Cookie, card: CardInstance, *,
                      flip: bool = False) -> None:
        """An HP card turning face up, at the moment it turns.

        ``flip`` means the reveal is a FLIP going off, which the board plays as
        a bigger beat. Cards taken off a pile *without* damage — "place N cards
        from the top of that Cookie's HP into the trash" — are still revealed,
        but no FLIP fires, so they are not that.
        """
        self.state.events.append({
            "kind": "reveal",
            "cookie": cookie.uid,
            "owner": cookie.owner,
            "card_uid": card.uid,
            "card_id": card.card_id,
            "flip": flip,
        })

    def _record_heal(self, cookie: Cookie, amount: int) -> None:
        """Structured counterpart to the damage event, so the viewer can play a
        heal rather than have HP quietly appear between two snapshots."""
        self.state.events.append({
            "kind": "heal",
            "cookie": cookie.uid,
            "owner": cookie.owner,
            "amount": amount,
            "left": cookie.remaining_hp,
        })

    def gain_hp(self, cookie: Cookie, amount: int) -> None:
        """Heal: cards come off the deck onto the HP pile.

        A Cookie's printed HP is not touched — "gains +1 HP" hands back a card,
        it does not make the Cookie permanently bigger — so a heal can leave the
        pile above the printed value, which the viewer shows as an overheal.
        """
        owner = self.state.players[cookie.owner]
        before = len(cookie.hp_cards)
        self._fill_hp(owner, cookie, before + amount)
        gained = len(cookie.hp_cards) - before
        if gained:
            owner.hp_gained_this_turn = True
            self.state.record(f"{cookie.label(self.db)} gains +{gained} HP"
                              f" — {cookie.remaining_hp} HP")
            self._record_heal(cookie, gained)

    def _is_extra(self, card: CardInstance) -> bool:
        return self.db[card.card_id].type is CardType.EXTRA

    def _to_private_zone(self, owner: PlayerState, card: CardInstance,
                         place) -> None:
        """Send a Cookie card that has left the battle area to a private zone.

        9-4-1: "If a Cookie Card played from the 【Extra】 deck is moved to a
        private zone, it is placed face-down in the 【Extra】 deck of its
        owner." The hand and the deck are both private zones, so an EXTRA
        Cookie bounced or decked goes home instead — it can never be played
        from either of them (6-5-2-3), so putting it there is dealing a dead
        card and quietly shrinking the EXTRA deck for the rest of the game.
        """
        if self._is_extra(card):
            card.face_up = False
            owner.extra_deck.append(card)
            self.state.record(
                f"{card_label(self.db[card.card_id])} returns to the EXTRA deck")
            return
        place(card)

    def return_cookie_to_hand(self, cookie: Cookie) -> None:
        owner = self.state.players[cookie.owner]
        if cookie not in owner.battle:
            return
        owner.battle.remove(cookie)
        extra = self._is_extra(cookie.card)
        self._to_private_zone(owner, cookie.card, owner.hand.append)
        # Only the Cookie the effect names returns to hand; anything it was
        # 【Awaken】ed on top of is spent along with its HP pile.
        owner.trash.extend(cookie.spent_cards)
        if not extra:
            self.state.record(
                f"{card_label(self.db[cookie.card.card_id])} returns to hand")
        self._check_battle_area(owner)

    def move_cookie_to_support(self, cookie: Cookie, *, rested: bool = False) -> None:
        """"Place that Cookie in your support area" — battle area to energy.

        The Cookie stops being a body and becomes a support card: it keeps no
        HP, no damage and no riders, so the HP pile and anything 【Awaken】ed
        under it are spent exactly as they are when the Cookie is bounced. Only
        the Cookie card itself makes the trip, and it lands face-up in the
        support area, where its colour is all that is left of it.

        Not a faint and not a trash: nothing reaches the break area, so the
        opponent banks no Level for it.
        """
        owner = self.state.players[cookie.owner]
        if cookie not in owner.battle:
            return
        owner.battle.remove(cookie)
        owner.trash.extend(cookie.equipment)
        owner.trash.extend(cookie.spent_cards)
        cookie.card.rested = rested
        owner.support.append(cookie.card)
        state = "rested" if rested else "active"
        self.state.record(
            f"{card_label(self.db[cookie.card.card_id])} moves to the support area as {state}")
        self._check_battle_area(owner)

    def _count_break_addition(self, owner: PlayerState, card: CardInstance) -> None:
        """One card arriving in a break area, for the "during this turn" asks.

        Two counters rather than one: cards ask about additions in general and
        about 【Arena】 additions in particular, and the keyword can only be
        read off the card as it arrives — the break area itself is a pile of
        cards with no memory of which turn each one landed on.
        """
        owner.break_additions_this_turn += 1
        if Keyword.ARENA in self.db[card.card_id].keywords:
            owner.arena_break_additions_this_turn += 1

    def cookie_to_deck(self, cookie: Cookie, *, bottom: bool = True) -> None:
        """A Cookie leaving the battle area for its owner's deck.

        The one place that happens, because two cards count it — BS9-088 asks
        only about the bottom, BS9-083 about either end — and a second copy of
        the move would be a counter that silently stops counting.
        """
        owner = self.state.players[cookie.owner]
        if cookie not in owner.battle:
            return
        owner.battle.remove(cookie)
        if bottom:
            self._to_private_zone(owner, cookie.card, owner.deck.append)
            owner.cookies_to_deck_bottom_this_turn += 1
        else:
            self._to_private_zone(owner, cookie.card,
                                  lambda c: owner.deck.insert(0, c))
        owner.cookies_to_deck_this_turn += 1
        owner.trash.extend(cookie.spent_cards)
        self._check_battle_area(owner)

    def faint(self, cookie: Cookie) -> None:
        """Public entry point for effects that destroy a Cookie directly."""
        self._faint(cookie)

    def trash_cookie(self, cookie: Cookie) -> None:
        """"Place that Cookie into the trash" — removal without fainting.

        The card never touches the break area, so it grants no Level toward the
        opponent's win condition. That is what makes these effects different
        from damage, and why cards price them the way they do.
        """
        owner = self.state.players[cookie.owner]
        if cookie not in owner.battle:
            return
        self._run_cookie_effect(cookie, Trigger.TRASHED, owner)
        if cookie not in owner.battle:
            return
        owner.battle.remove(cookie)
        owner.trash.append(cookie.card)
        owner.trash.extend(cookie.spent_cards)
        self.state.record(f"{card_label(self.db[cookie.card.card_id])} is trashed")
        self._check_battle_area(owner)

    def _faint(self, cookie: Cookie) -> None:
        owner = self.state.players[cookie.owner]
        if cookie not in owner.battle or cookie.uid in self._fainting:
            return
        self._fainting.add(cookie.uid)
        try:
            self._run_cookie_effect(cookie, Trigger.FAINT, owner)
        finally:
            self._fainting.discard(cookie.uid)
        # A faint trigger can move its own Cookie (bounce it, deck it, trash
        # it). If it did, the faint has already been superseded.
        if cookie not in owner.battle:
            return
        owner.battle.remove(cookie)
        owner.break_area.append(cookie.card)
        owner.trash.extend(cookie.equipment)
        owner.cookies_fainted_this_turn += 1
        owner.faint_log.append((self.state.turn_counter,
                                cookie.defn(self.db).color,
                                cookie.level(self.db)))
        self._count_break_addition(owner, cookie.card)
        owner.trash.extend(cookie.spent_cards)
        self.state.record(f"{card_label(self.db[cookie.card.card_id])} faints")
        self._check_win()
        if not self.state.over:
            self._check_battle_area(owner)

    def _check_battle_area(self, player: PlayerState) -> None:
        """"If your Cookie card has fainted, you can bring one Cookie card from
        your hand to the battle area" — and you lose only when the battle area
        is empty *and* no Cookie card in hand could refill it."""
        if player.battle or self._refill_held:
            return
        # "no remaining Cookie cards in your hand to play" (1-2-1-1-2) means
        # *playable*: an 【EXTRA】 Cookie that wandered into a hand may only be
        # played from the EXTRA deck, and a 【Special Play】 one whose condition
        # cannot be honoured is not a Cookie you have to play either.
        options = self.playable_cookies(player)
        if not options:
            if self.rules.lose_when_no_cookie_anywhere:
                self._lose(player.index, "no Cookie in play or in hand")
            return
        card = self.controller(player.index).choose(
            self.state, "Field a replacement Cookie", options, optional=False
        ) or options[0]
        player.hand.remove(card)
        self._deploy_cookie(player, card)

    # ------------------------------------------------------------------
    # shared primitives (used by effects)
    # ------------------------------------------------------------------
    def draw(self, player: PlayerState, n: int, *, optional: bool = True) -> int:
        drawn = 0
        for _ in range(n):
            if not player.deck and not self._refresh(player):
                return drawn
            player.hand.append(player.deck.pop(0))
            drawn += 1
        return drawn

    def _refresh(self, player: PlayerState) -> bool:
        """[refresh]: an empty deck costs a Cookie, it does not lose the game.

        "the player selects 1 Cookie card of LV.1 or higher from their trash
        and places it in their break area. After that, shuffle all cards in the
        trash and place them in the deck." (1-3-9-1.)

        Unless there is no such Cookie: an empty deck with nothing in the trash
        to pay for it is the third defeat condition (1-2-1-1-3, 9-2-1-3), and
        it used to be the one thing here that silently did nothing. A player
        whose trash holds no Cookie kept drawing off a reshuffled pile forever.
        """
        if not self.rules.refresh_on_empty_deck or player.deck:
            return bool(player.deck)

        # 9-4-3: an 【EXTRA】 Cookie sitting in the trash goes home to the EXTRA
        # deck. First, because it is not one of the Cookies the break area may
        # be paid with either — it was never part of the 60, and a deck that
        # grew one would be dealing a card that cannot be played from hand.
        returning = [c for c in player.trash
                     if self.db[c.card_id].type is CardType.EXTRA]
        for card in returning:
            player.trash.remove(card)
            card.face_up = False
            player.extra_deck.append(card)
        if returning:
            self.state.record(
                f"refresh — {len(returning)} EXTRA card(s) return to the EXTRA deck")

        for _ in range(self.rules.refresh_break_cost):
            # A *Cookie* card of LV.1 or higher, not merely a card with a
            # Level: nothing else may be placed in a break area (3-8-3).
            options = [c for c in player.trash
                       if self.db[c.card_id].is_cookie
                       and (self.db[c.card_id].level or 0) >= 1]
            if not options:
                self._lose(player.index,
                           "refresh with no Cookie in the trash")
                return False
            card = self.controller(player.index).choose(
                self.state, "Refresh: send a Cookie to your break area",
                options, optional=False,
            ) or options[0]
            player.trash.remove(card)
            player.break_area.append(card)
            self._count_break_addition(player, card)
            self.state.record(f"refresh — {card_label(self.db[card.card_id])} to break area")
        self._check_win()
        if self.state.over:
            return False

        player.refresh_count += 1
        player.deck.extend(player.trash)
        player.trash.clear()
        self._shuffle(player.deck)
        return bool(player.deck)

    def discard(self, player: PlayerState, n: int, ctx, *,
                color: Color | None = None, predicate=None,
                optional: bool = False) -> list[CardInstance]:
        """Discard ``n`` cards. Returns what was discarded — empty if unpayable.

        `color` is the common narrowing and `predicate` the general one; a
        caller may pass either, and both are applied when both are given.
        """
        pool = [c for c in player.hand
                if (color is None or self.db[c.card_id].color is color)
                and (predicate is None or predicate(self.db[c.card_id]))]
        if len(pool) < n:
            if not optional:
                ctx.fizzled = True
            return []
        label = "Discard a card" if n == 1 else f"Discard {n} cards"
        discarded = ask_many(self.controller(player.index), self.state, label, pool, n)
        for card in discarded:
            player.hand.remove(card)
            player.trash.append(card)
        return discarded

    def pay_cost(self, player: PlayerState, cost: Cost) -> bool:
        if not cost:
            return True
        indices, colors = player.active_support_colors(self.db)
        plan = plan_payment(cost, colors)
        if plan is None:
            return False
        for local in plan.indices:
            player.support[indices[local]].rested = True
        return True

    # ------------------------------------------------------------------
    # effect dispatch
    # ------------------------------------------------------------------
    def _run_cookie_effect(self, cookie: Cookie, trigger: Trigger,
                           player: PlayerState) -> None:
        defn = cookie.defn(self.db)
        fn = get_effect(defn.id, trigger)
        if fn is not None:
            with self._effect_source(defn.name,
                                     source_kind(self.db, cookie.card, trigger),
                                     label=card_label(defn)):
                fn(self._ctx(player, source_cookie=cookie, source_card=cookie.card,
                             trigger=trigger.value))
        self._run_equipment_effects(cookie, trigger, player)

    def _run_equipment_effects(self, cookie: Cookie, trigger: Trigger,
                               player: PlayerState) -> None:
        """Triggers a Cookie has only because of what is 【Equip】ped to it.

        The Soul Jams print a rider on their host — "when that Cookie attacks,
        draw 1 card" — which belongs to the jam, not to the Cookie: strip the
        jam or move the Cookie and the rider goes with it. So it is registered
        against the *jam's* card id and looked up here, with the host as
        `source_cookie` and the jam as `source_card`, which is what the rider's
        own text means by "that Cookie" and "this card".
        """
        for card in list(cookie.equipment):
            fn = get_effect(self.db[card.card_id].id, trigger)
            if fn is None:
                continue
            defn = self.db[card.card_id]
            with self._effect_source(defn.name,
                                     source_kind(self.db, card, trigger),
                                     label=card_label(defn)):
                fn(self._ctx(player, source_cookie=cookie, source_card=card,
                             trigger=trigger.value))

    def _run_effect(self, card: CardInstance, trigger: Trigger,
                    player: PlayerState, *, flip_host: Cookie | None = None) -> None:
        fn = get_effect(self.db[card.card_id].id, trigger)
        if fn is None:
            return
        defn = self.db[card.card_id]
        with self._effect_source(defn.name, source_kind(self.db, card, trigger),
                                 label=card_label(defn)):
            fn(self._ctx(player, source_cookie=flip_host, source_card=card,
                         trigger=trigger.value))

    @contextlib.contextmanager
    def showing(self, cards):
        """Cards the player being asked is looking at, for the next question.

        A "view the top 3" effect asks you to pick out of three cards that are
        in no zone the board draws. The three ride along on the question rather
        than in the snapshot, because a question the other seat is not being
        asked is already stripped on the way out (`Match._hide_pending`) and
        that is exactly the rule these need: what you looked at and did not take
        is yours. Same shape as `_effect_source` — a stack on the state that the
        layer above reads at the moment it builds the payload.
        """
        previous = self.state.viewing
        self.state.viewing = list(cards)
        try:
            yield
        finally:
            self.state.viewing = previous

    @contextlib.contextmanager
    def _effect_source(self, name: str, kind: str = "", label: str = ""):
        """Name the card whose effect is resolving, and what kind of thing it
        is, for the duration.

        Every line `state.record` writes inside this block is stamped with
        both, so the log says *what* caused a draw, a heal or a point of
        damage. The name alone was not enough: "Piercing Arrow of Purity" is
        the same three words whether it was set as a trap, activated as a
        skill or turned over as a FLIP mid-attack, and those are very different
        things to be on the wrong end of. Nested — a FLIP that fires mid-damage
        names the FLIP, not the attack that turned it over.
        """
        self.state.effect_sources.append((name, kind, label or name))
        try:
            yield
        finally:
            if self.state.effect_sources:
                self.state.effect_sources.pop()

    def _ctx(self, player: PlayerState, **kw) -> Ctx:
        kw.setdefault("attack_target", self._attack_target)
        kw.setdefault("attacker", self._attacking_cookie)
        return Ctx(
            game=self,
            state=self.state,
            db=self.db,
            me=player,
            opp=self.state.opponent_of(player.index),
            **kw,
        )

    # ------------------------------------------------------------------
    # win conditions
    # ------------------------------------------------------------------
    def _check_win(self) -> None:
        for player in self.state.players:
            if player.break_level_total(self.db) >= self.rules.break_level_to_lose:
                self._lose(player.index, "break area reached level 10")
                return

    def _lose(self, index: int, reason: str) -> None:
        if self.state.winner is None:
            self.state.winner = 1 - index
            self.state.win_reason = f"P{index} loses: {reason}"
            self.state.record(self.state.win_reason)

    def _draw_game(self, reason: str) -> None:
        if self.state.winner is None:
            self.state.winner = -1
            self.state.win_reason = f"draw: {reason}"

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _peek_hand(self, player: PlayerState, uid: int) -> CardInstance:
        card = next((c for c in player.hand if c.uid == uid), None)
        if card is None:
            raise ValueError(f"card {uid} not in P{player.index}'s hand")
        return card

    def _take_from_hand(self, player: PlayerState, uid: int) -> CardInstance:
        card = self._peek_hand(player, uid)
        player.hand.remove(card)
        return card

    def clone(self) -> "Game":
        """Deep copy for search. Controllers are shared, not copied."""
        twin = copy.copy(self)
        twin.state = copy.deepcopy(self.state)
        twin._pending_attack = None
        twin._response_player = None
        twin._refill_held = 0
        return twin

    def play_out(self) -> GameState:
        """Drive both controllers until the game ends."""
        while not self.state.over:
            options = self.legal_actions()
            if not options:
                self.end_turn()
                continue
            choice = self.controller(self.to_move()).choose_action(self.state, options)
            self.step(choice or A.EndTurn())
        return self.state
