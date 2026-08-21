"""A wider state encoding for learning agents.

`features.Encoder` describes the board with aggregate counts: how many Cookies
each side has, how much HP in total, how many cards in hand. That is enough to
learn a decent curve, but a policy physically cannot learn "hold this trap for
their LV3" from it, because *which* Cookies are on the board never reaches the
network.

This encoder keeps the same contract — ``encode`` returns one row per legal
action, and the leading ``state_dim`` columns are the state block a value head
reads — so `rl.Trainer`, `rl.RLAgent` and `rl.PolicyNet` work unchanged. What
changes is what goes into the state block:

* **Per-Cookie slots.** ``max_battle_cookies`` is 2, so both boards fit in four
  fixed slots. Each carries level, remaining and printed HP, attack, whether it
  is rested or newly summoned, and its ability markers. No pooling and no
  set-encoder needed — the board is small enough to lay out flat.
* **Payable colours.** Which colours the active support can actually pay is the
  gate on every play, and the old encoder only counted supports. Five slots.
* **Hand shape.** Counts by level and by type, plus how many cards are payable
  right now and the cheapest cost, rather than one hand-size scalar.
* **Trash and break composition**, so "what has already been spent" is visible.

Only what the acting seat can legitimately see is encoded. The opponent's hand
contributes nothing beyond its size, which is public.

The action block is reused verbatim from the stock encoder, so the two differ
only in how much of the board the policy can see.
"""

from __future__ import annotations

import numpy as np

from . import config as cfg
from .cards import CardDB
from .enums import CardType, Color, Marker
from .features import ACTION_DIM, TYPE_DIM, Encoder
from .state import Cookie, GameState, PlayerState

#: Battle slots encoded per side. Sized from the rule, not hard-coded.
SLOTS = cfg.DEFAULT.max_battle_cookies
COOKIE_DIM = 14
COLORS = (Color.RED, Color.GREEN, Color.BLUE, Color.YELLOW, Color.PURPLE)

GLOBAL_DIM = 26
HAND_DIM = 12
ZONE_DIM = 10

WIDE_STATE_DIM = (GLOBAL_DIM + 2 * SLOTS * COOKIE_DIM + len(COLORS)
                  + HAND_DIM + ZONE_DIM)
WIDE_FEATURE_DIM = WIDE_STATE_DIM + TYPE_DIM + ACTION_DIM


class WideEncoder(Encoder):
    """Same row contract as :class:`~braverse.features.Encoder`, richer state."""

    dim = WIDE_FEATURE_DIM
    state_dim = WIDE_STATE_DIM

    # -- state ----------------------------------------------------------
    def state_vector(self, state: GameState, seat: int) -> np.ndarray:
        db = self.db
        me = state.players[seat]
        opp = state.players[1 - seat]

        parts = [
            self._globals(state, me, opp),
            self._board(me), self._board(opp),
            self._payable(me),
            self._hand(me),
            self._zones(me, opp),
        ]
        return np.concatenate(parts).astype(np.float32)

    def _globals(self, state: GameState, me: PlayerState,
                 opp: PlayerState) -> np.ndarray:
        db = self.db
        my_break = me.break_level_total(db)
        opp_break = opp.break_level_total(db)
        my_hp = sum(c.remaining_hp for c in me.battle)
        opp_hp = sum(c.remaining_hp for c in opp.battle)
        # How close each side is to losing if their board is cleared — the
        # single most decision-relevant quantity in the game.
        exposed = my_break + sum(c.level(db) for c in me.battle)
        threat = opp_break + sum(c.level(db) for c in opp.battle)
        return np.array([
            my_break / 10.0, opp_break / 10.0,
            (my_break - opp_break) / 10.0,
            exposed / 10.0, threat / 10.0,
            1.0 if exposed >= 10 else 0.0,
            1.0 if threat >= 10 else 0.0,
            len(me.battle) / SLOTS, len(opp.battle) / SLOTS,
            my_hp / 12.0, opp_hp / 12.0, (my_hp - opp_hp) / 12.0,
            len(me.hand) / 10.0, len(opp.hand) / 10.0,   # size only: public
            len(me.deck) / 60.0, len(opp.deck) / 60.0,
            1.0 if not me.deck else 0.0,
            1.0 if not opp.deck else 0.0,
            state.turn_number / 15.0,
            1.0 if me.stage else 0.0, 1.0 if opp.stage else 0.0,
            float(me.supported_this_turn),
            float(me.traps_disabled), float(opp.traps_disabled),
            float(me.blockers_disabled),
            1.0,  # bias
        ], dtype=np.float32)

    def _board(self, player: PlayerState) -> np.ndarray:
        """One fixed slot per battle position, empty slots left at zero."""
        rows = np.zeros((SLOTS, COOKIE_DIM), dtype=np.float32)
        for i, cookie in enumerate(player.battle[:SLOTS]):
            rows[i] = self._cookie(cookie)
        return rows.reshape(-1)

    def _cookie(self, cookie: Cookie) -> np.ndarray:
        db = self.db
        defn = db[cookie.card.card_id]
        printed = defn.hp or 1
        return np.array([
            1.0,                                     # slot occupied
            cookie.level(db) / 3.0,
            cookie.remaining_hp / 6.0,
            printed / 6.0,
            cookie.remaining_hp / printed,           # how chewed up it is
            cookie.attack_damage(db) / 5.0,
            float(cookie.rested),
            float(cookie.summoned_this_turn),
            float(defn.is_flip),
            float(defn.has(Marker.BLOCKER)),
            float(defn.has(Marker.ACTIVATE)) * (0.0 if cookie.activate_locked
                                                else 1.0),
            float(defn.has(Marker.ONCE_PER_TURN)),
            float(cookie.damage_immune or cookie.hp_cannot_reach_zero),
            float(self._has_effect(defn)),
        ], dtype=np.float32)

    def _payable(self, player: PlayerState) -> np.ndarray:
        """Active support by colour — the gate on whether a play is legal."""
        _, colors = player.active_support_colors(self.db)
        counts = np.zeros(len(COLORS), dtype=np.float32)
        for color in colors:
            if color in COLORS:
                counts[COLORS.index(color)] += 1.0
        return counts / 4.0

    def _hand(self, player: PlayerState) -> np.ndarray:
        db = self.db
        defns = [db[c.card_id] for c in player.hand]
        available = len(player.active_support())
        costs = [d.play_cost.total for d in defns]
        playable = [d for d, cost in zip(defns, costs) if cost <= available]
        levels = [d.level for d in defns if d.is_cookie]
        return np.array([
            sum(1 for lv in levels if lv == 1) / 6.0,
            sum(1 for lv in levels if lv == 2) / 6.0,
            sum(1 for lv in levels if lv == 3) / 6.0,
            sum(1 for d in defns if d.type is CardType.ITEM) / 6.0,
            sum(1 for d in defns if d.type is CardType.TRAP) / 6.0,
            sum(1 for d in defns if d.type is CardType.STAGE) / 6.0,
            sum(1 for d in defns if d.is_flip) / 6.0,
            len(playable) / 6.0,
            (min(costs) / 4.0) if costs else 1.0,
            (sum(costs) / len(costs) / 4.0) if costs else 0.0,
            sum(1 for d in playable if d.is_cookie) / 6.0,
            sum(1 for d in defns if d.has(Marker.BLOCKER)) / 6.0,
        ], dtype=np.float32)

    def _zones(self, me: PlayerState, opp: PlayerState) -> np.ndarray:
        db = self.db
        return np.array([
            len(me.support) / 12.0, len(opp.support) / 12.0,
            len(me.active_support()) / 12.0, len(opp.active_support()) / 12.0,
            len(me.trash) / 60.0, len(opp.trash) / 60.0,
            sum(1 for c in me.trash if db[c.card_id].is_cookie) / 20.0,
            len(me.break_area) / 10.0, len(opp.break_area) / 10.0,
            me.refresh_count / 3.0,
        ], dtype=np.float32)
