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
from typing import Sequence

from . import actions as A
from . import config as cfg
from .cards import CardDB, CardDef, default_db
from .cost import Cost, plan_payment
from .effects import (Ctx, Trigger, ask_many, cannot_attack, effect_is_live,
                      extra_play_of, forced_attack_target, get_effect,
                      may_play, modified_attack_cost)
from .enums import CardType, Color, Marker, Phase
from .state import CardInstance, Cookie, GameState, PlayerState

_BLOCKER_COST_RE = None  # set lazily; see _blocker_cost


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
    ):
        self.db = db or default_db()
        self.rules = rules
        # Who takes the first turn. The PLAY GUIDE settles this with rock,
        # paper, scissors before the game starts (see `braverse/rps.py`); the
        # engine just needs to be told the answer.
        self.first_player = first_player
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

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------
    def controller(self, index: int):
        return self._controllers[index]

    def setup(self) -> None:
        state = self.state
        for player, deck_list, extra_list in zip(state.players, self._deck_lists,
                                                 self._extra_lists):
            player.deck = [CardInstance.make(cid, player.index) for cid in deck_list]
            state.rng.shuffle(player.deck)
            # The EXTRA deck is not shuffled and not drawn from: every card in
            # it is visible to its owner all game, and is played out of the
            # pile directly when its gate opens.
            player.extra_deck = [CardInstance.make(cid, player.index)
                                 for cid in extra_list]
            self._draw_opening_hand(player)
        for player in state.players:
            self._offer_mulligan(player)
        for player in state.players:
            self._redraw_until_cookie(player)
        for player in state.players:
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
        """One optional redraw of the opening hand, before anything is placed.

        The whole hand goes back, the deck is shuffled and a fresh hand is
        drawn — there is no cost and no card penalty, so it is offered once and
        the answer is simply taken. It runs *before* `_redraw_until_cookie`, so
        a mulligan into a Cookie-less hand still triggers the mandatory redraw
        the guide describes.

        Only a controller that implements `wants_mulligan` is asked. Scripted
        agents have no read on hand quality, so answering for them would replace
        every opening hand in every self-play game with a random one and move
        every number this project measures, for no gain in play strength.
        """
        if not self.rules.allow_mulligan:
            return
        ask = getattr(self.controller(player.index), "wants_mulligan", None)
        if ask is None or not ask(self.state, list(player.hand)):
            return
        self.state.rng.shuffle(player.hand)   # the hand goes back unordered
        player.deck.extend(player.hand)
        player.hand.clear()
        self.state.rng.shuffle(player.deck)
        self._draw_opening_hand(player)
        self.state.record(f"mulligan: P{player.index} draws a new hand")

    def _redraw_until_cookie(self, player: PlayerState) -> None:
        """"If a player does not have a Cookie card in their hand, they must
        reveal their hand, return it to the deck, and draw 6 new cards. The
        opponent may draw 1 card from the deck." Repeated until they have one."""
        if not self.rules.redraw_until_cookie:
            return
        opponent = self.state.opponent_of(player.index)
        for _ in range(20):
            if any(self.db[c.card_id].is_cookie for c in player.hand):
                return
            player.deck.extend(player.hand)
            player.hand.clear()
            self.state.rng.shuffle(player.deck)
            self._draw_opening_hand(player)
            self.draw(opponent, self.rules.opponent_draws_on_redraw)
        self._lose(player.index, "could not draw an opening Cookie")

    def _place_opening_cookie(self, player: PlayerState) -> None:
        """Step 5: each player places 1 Cookie card face down, then reveals it
        and builds its HP pile. [On Play] effects do not fire during setup."""
        options = [c for c in player.hand if self.db[c.card_id].is_cookie]
        if not options:
            self._lose(player.index, "no Cookie to open with")
            return
        card = self.controller(player.index).choose(
            self.state, "Opening Cookie", options, optional=False
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
            cookie.used_markers.clear()
        player.supported_this_turn = False
        player.activated_this_turn.clear()
        player.items_played_this_turn = 0
        self._actions_this_turn = 0
        player.hp_gained_this_turn = False
        player.played_from_break_this_turn.clear()
        player.played_from_trash_this_turn.clear()
        player.support_trashed_this_turn = 0
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
        for card in list(state.current.stage):
            self._run_effect(card, Trigger.END_TURN, state.current)
        for cookie in list(state.current.battle):
            self._run_cookie_effect(cookie, Trigger.END_TURN, state.current)
        if state.current.set_active_at_end_turn:
            # Banked by attack riders that read "when your turn ends, ...".
            count = state.current.set_active_at_end_turn
            state.current.set_active_at_end_turn = 0
            for card in state.current.support:
                if count <= 0:
                    break
                if card.rested:
                    card.rested = False
                    count -= 1
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

    def _is_first_turn(self) -> bool:
        return (self.state.turn_number == 1
                and self.state.turn_player == self.first_player)

    def to_move(self) -> int:
        if self._response_player is not None:
            return self._response_player
        return self.state.turn_player

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

        if not player.supported_this_turn:
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

    def _cookie_plays(self, player: PlayerState, card: CardInstance,
                      defn: CardDef) -> list[A.Action]:
        """Any Cookie may be played into a free battle slot, at no cost.

        【EXTRA】 Cookies additionally carry a "can be played if ..." gate.
        """
        if len(player.battle) >= self.rules.max_battle_cookies:
            return []
        if not may_play(self.db, player,
                        self.state.opponent_of(player.index), defn):
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
                if cookie.uid == target.uid or cookie.rested:
                    continue
                if player.blockers_disabled:
                    break
                price = self._blocker_cost(cookie)
                if price is not None and plan_payment(price[0], colors) is not None:
                    out.append(A.Block(cookie.uid))
        return out

    def _blocker_cost(self, cookie: Cookie) -> tuple[Cost, bool] | None:
        """What redirecting an attack to this Cookie costs, as printed.

        Returns (energy, rests itself), or None if the Cookie has no
        【Blocker】. Five cards price the block as `<Rest this card.>` rather
        than in energy; that half used to be dropped, so those Cookies blocked
        every attack in a turn for free and were still standing to attack on
        their own.
        """
        defn = cookie.defn(self.db)
        if not defn.has(Marker.BLOCKER):
            return None
        import re
        bracket = re.search(r"【Blocker】\s*<([^>]*)>", defn.description)
        if bracket is None:
            return Cost(), False
        token = bracket.group(1)
        if re.fullmatch(r"(?:\{[A-Za-z]+\})*", token):
            return Cost.parse(token), False
        if re.search(r"rest this card", token, re.I):
            return Cost(), True
        # An unread price is not a free one: leave the Cookie unable to block
        # rather than letting it block for nothing.
        return None

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
        self.state.record(f"supports {self.db[card.card_id].name}")

    def _do_play_cookie(self, action: A.PlayCookie) -> None:
        player = self.state.current
        card = self._take_from_hand(player, action.card_uid)
        self._deploy_cookie(player, card, onto=action.onto)

    def _do_play_extra(self, action: A.PlayExtra) -> None:
        player = self.state.current
        card = next((c for c in player.extra_deck if c.uid == action.card_uid), None)
        if card is None:
            return
        play = extra_play_of(self.db, card.card_id)
        if play is None:
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
        defn = self.db[card.card_id]
        if host is not None:
            self._awaken(player, host, card)
        else:
            self.state.record(f"plays {defn.name} from the EXTRA deck")
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
        self.state.record(f"awakens {under.name} \u2192 {defn.name}")
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
        self.state.record(f"plays {defn.name}")
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
            self.state.record(f"places stage {defn.name}")
        else:
            player.items_played_this_turn += 1
            self.state.record(f"activates {defn.name}")
            self._run_effect(card, Trigger.ITEM, player)
            # An item that placed itself somewhere — "place this card in your
            # support area as rested" — has already chosen its zone. Filing it
            # in the trash as well would leave one CardInstance in two zones.
            if self.state.find_card(card.uid) is None:
                player.trash.append(card)

    def _do_activate(self, action: A.ActivateSkill) -> None:
        player = self.state.current
        player.activated_this_turn.add(action.source_uid)
        cookie = player.find_cookie(action.source_uid)
        if cookie is not None:
            # Logged before it resolves, so the skill is named even when what it
            # does is invisible — a draw, a buff, an effect that fizzles.
            self.state.record(f"activates {cookie.name(self.db)}")
            self._run_cookie_effect(cookie, Trigger.ACTIVATE, player)
            cookie.used_markers.add(Trigger.ACTIVATE.value)
            return
        card = next((c for c in player.stage if c.uid == action.source_uid), None)
        if card is not None:
            self.state.record(f"activates {self.db[card.card_id].name}")
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
        state.record(f"{attacker.name(self.db)} attacks {target.name(self.db)} "
                     f"for {attacker.attack_damage(self.db)}")

        target = self._response_window(defender, attacker, target)
        if state.over or target is None:
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
            self.state.record(f"{attacker.name(self.db)}'s attack is reduced "
                              f"to {damage} (from {swing})")
        # Riders can ask "if your opponent's Cookie faints from this Cookie's
        # attack", so record the outcome before the rider runs.
        before = len(defender.break_area)
        if damage > 0:
            self.deal_damage(target, damage, source_player=player.index,
                             kind="attack")
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
                    self.state.record(f"springs trap {defn.name}")
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
                    self.state.record(f"{blocker.name(self.db)} blocks"
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
                    kind: str = "effect") -> None:
        """Reveal ``amount`` HP cards, one at a time, firing any FLIP as it turns.

        The count logged is what was actually taken off the pile, which is not
        always ``amount``: the pile can run dry, "HP cannot reach 0" stops one
        short, and a FLIP can hand HP *back* mid-way, in which case the loop
        keeps going and legitimately takes more cards than the Cookie started
        with.
        """
        name = cookie.name(self.db)
        if cookie.damage_immune:
            self.state.record(f"{name} takes no {kind} damage (immune)")
            return
        from .effects import shields_from_opponent
        owner = self.state.players[cookie.owner]
        if source_player != cookie.owner and shields_from_opponent(self.db, owner):
            self.state.record(f"{name} takes no {kind} damage (shielded)")
            return
        amount -= cookie.all_damage_reduction
        if amount <= 0:
            self.state.record(f"{name} takes no {kind} damage (reduced)")
            return
        dealt = 0
        for _ in range(amount):
            if not cookie.hp_cards:
                break
            card = cookie.hp_cards.pop()
            card.face_up = True
            owner.trash.append(card)
            dealt += 1
            defn = self.db[card.card_id]
            # Recorded *before* the FLIP runs, so the viewer can turn the card
            # over and only then play whatever it did. A diff of the HP pile
            # cannot express that order — it only knows the card ended up in
            # the trash, by which time the flip has already resolved.
            self.record_reveal(cookie, card, flip=defn.is_flip)
            if defn.is_flip and not cookie.flip_disabled:
                self.state.record(f"FLIP! {defn.name}")
                self._run_effect(card, Trigger.FLIP, owner, flip_host=cookie)
            # A flip effect can bounce or otherwise remove its own host;
            # the rest of the damage has nothing left to hit.
            if self.state.over or cookie not in owner.battle:
                self._record_damage(name, dealt, amount, None, kind, cookie)
                return
            # "This Cookie's HP cannot reach 0": the card just spent is
            # replaced off the deck rather than the hit being stopped one
            # short. The damage keeps turning cards — every FLIP in the pile
            # still fires — and the Cookie is still standing at the end of it.
            self.hold_the_floor(cookie)
        self._record_damage(name, dealt, amount, cookie, kind, cookie)
        if not cookie.hp_cards:
            self._faint(cookie)

    def _record_damage(self, name: str, dealt: int, asked: int,
                       cookie: Cookie | None, kind: str = "effect",
                       target: Cookie | None = None) -> None:
        """`kind` separates a swing from everything else — a "Then, ..." rider,
        a skill, a trap — so the log says which one hit you."""
        if target is not None and dealt:
            self.state.events.append({
                "kind": "damage",
                "cookie": target.uid,
                "owner": target.owner,
                "amount": dealt,
                "source": kind,
                "left": target.remaining_hp if cookie is not None else 0,
            })
        if dealt == 0:
            self.state.record(f"{name} takes no {kind} damage")
            return
        line = f"{name} takes {dealt} {kind} damage"
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
        self.state.record(f"{cookie.name(self.db)}'s HP cannot reach 0"
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
            self.state.record(f"{cookie.name(self.db)} gains +{gained} HP"
                              f" — {cookie.remaining_hp} HP")
            self._record_heal(cookie, gained)

    def return_cookie_to_hand(self, cookie: Cookie) -> None:
        owner = self.state.players[cookie.owner]
        if cookie not in owner.battle:
            return
        owner.battle.remove(cookie)
        owner.hand.append(cookie.card)
        # Only the Cookie the effect names returns to hand; anything it was
        # 【Awaken】ed on top of is spent along with its HP pile.
        owner.trash.extend(cookie.spent_cards)
        self.state.record(f"{self.db[cookie.card.card_id].name} returns to hand")
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
        self.state.record(f"{self.db[cookie.card.card_id].name} is trashed")
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
        owner.break_additions_this_turn += 1
        owner.trash.extend(cookie.spent_cards)
        self.state.record(f"{self.db[cookie.card.card_id].name} faints")
        self._check_win()
        if not self.state.over:
            self._check_battle_area(owner)

    def _check_battle_area(self, player: PlayerState) -> None:
        """"If your Cookie card has fainted, you can bring one Cookie card from
        your hand to the battle area" — and you lose only when the battle area
        is empty *and* no Cookie card in hand could refill it."""
        if player.battle:
            return
        options = [c for c in player.hand if self.db[c.card_id].is_cookie]
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
        trash and place them in the deck."
        """
        if not self.rules.refresh_on_empty_deck or player.deck:
            return bool(player.deck)

        for _ in range(self.rules.refresh_break_cost):
            options = [c for c in player.trash if (self.db[c.card_id].level or 0) >= 1]
            if not options:
                break
            card = self.controller(player.index).choose(
                self.state, "Refresh: send a Cookie to your break area",
                options, optional=False,
            ) or options[0]
            player.trash.remove(card)
            player.break_area.append(card)
            player.break_additions_this_turn += 1
            self.state.record(f"refresh — {self.db[card.card_id].name} to break area")
        self._check_win()
        if self.state.over:
            return False

        player.refresh_count += 1
        player.deck.extend(player.trash)
        player.trash.clear()
        self.state.rng.shuffle(player.deck)
        return bool(player.deck)

    def discard(self, player: PlayerState, n: int, ctx, *,
                color: Color | None = None, optional: bool = False) -> list[CardInstance]:
        """Discard ``n`` cards. Returns what was discarded — empty if unpayable."""
        pool = [c for c in player.hand
                if color is None or self.db[c.card_id].color is color]
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
        fn = get_effect(cookie.defn(self.db).id, trigger)
        if fn is None:
            return
        with self._effect_source(cookie.defn(self.db).name):
            fn(self._ctx(player, source_cookie=cookie, source_card=cookie.card,
                         trigger=trigger.value))

    def _run_effect(self, card: CardInstance, trigger: Trigger,
                    player: PlayerState, *, flip_host: Cookie | None = None) -> None:
        fn = get_effect(self.db[card.card_id].id, trigger)
        if fn is None:
            return
        with self._effect_source(self.db[card.card_id].name):
            fn(self._ctx(player, source_cookie=flip_host, source_card=card,
                         trigger=trigger.value))

    @contextlib.contextmanager
    def _effect_source(self, name: str):
        """Name the card whose effect is resolving, for the duration.

        Every line `state.record` writes inside this block is stamped with the
        card that caused it. Nested — a FLIP that fires mid-damage names the
        FLIP, not the attack that turned it over.
        """
        self.state.effect_sources.append(name)
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
