"""Feature encoding for learning agents.

Every decision is scored as a (state, action) pair, because the action set is
variable-length and heterogeneous — there is no fixed action index to softmax
over. The encoder emits one vector per legal action, always from the acting
seat's point of view, with the state block first so a value head can read it
without re-encoding.
"""

from __future__ import annotations

import numpy as np

from . import actions as A
from .cards import CardDB
from .enums import CardType, Marker
from .state import Cookie, GameState

STATE_DIM = 22
ACTION_DIM = 24
TYPE_DIM = 9
FEATURE_DIM = STATE_DIM + TYPE_DIM + ACTION_DIM

_ACTION_TYPES = (
    A.PlaceSupport, A.PlayCookie, A.PlaySupportCard, A.ActivateSkill,
    A.Attack, A.PlayTrap, A.Block, A.Pass, A.EndTurn,
)
_TYPE_INDEX = {cls: i for i, cls in enumerate(_ACTION_TYPES)}
# An 【EXTRA】 play *is* a Cookie play — a body arriving in the battle area —
# so it shares that one-hot slot rather than claiming a tenth. Giving it its own
# would widen FEATURE_DIM, and every agent checkpoint on disk was trained at the
# current width; what actually distinguishes the move (Level, HP, board state)
# is in the action vector either way.
_TYPE_INDEX[A.PlayExtra] = _TYPE_INDEX[A.PlayCookie]


class Encoder:
    """Turns legal actions into a float32 matrix of shape (n_actions, FEATURE_DIM)."""

    def __init__(self, db: CardDB):
        self.db = db

    # -- state ----------------------------------------------------------
    def state_vector(self, state: GameState, seat: int) -> np.ndarray:
        db = self.db
        me = state.players[seat]
        opp = state.players[1 - seat]

        my_hp = sum(c.remaining_hp for c in me.battle)
        opp_hp = sum(c.remaining_hp for c in opp.battle)
        my_break = me.break_level_total(db)
        opp_break = opp.break_level_total(db)
        cookies_in_hand = sum(1 for c in me.hand if db[c.card_id].is_cookie)

        # How close the opponent is to winning if they clear my board: the
        # single most decision-relevant quantity in the game.
        exposed = my_break + sum(c.level(db) for c in me.battle)

        return np.array([
            my_break / 10.0,
            opp_break / 10.0,
            len(me.battle) / 2.0,
            len(opp.battle) / 2.0,
            my_hp / 12.0,
            opp_hp / 12.0,
            len(me.hand) / 10.0,
            len(me.support) / 12.0,
            len(me.active_support()) / 12.0,
            len(opp.support) / 12.0,
            len(opp.active_support()) / 12.0,
            len(me.deck) / 60.0,
            len(opp.deck) / 60.0,
            len(me.trash) / 60.0,
            state.turn_number / 15.0,
            1.0 if me.stage else 0.0,
            1.0 if opp.stage else 0.0,
            self._best_attack(me.battle) / 5.0,
            self._best_attack(opp.battle) / 5.0,
            cookies_in_hand / 6.0,
            1.0 if exposed >= 10 else 0.0,
            1.0,  # bias
        ], dtype=np.float32)

    def _best_attack(self, cookies: list[Cookie]) -> float:
        return max((c.attack_damage(self.db) for c in cookies), default=0)

    def _has_effect(self, defn) -> bool:
        """Whether this card actually does something beyond its stat line."""
        from .effects import Trigger, get_effect
        return any(get_effect(defn.id, t) for t in Trigger)

    # -- actions --------------------------------------------------------
    def encode(self, state: GameState, seat: int,
               options) -> np.ndarray:
        base = self.state_vector(state, seat)
        rows = np.zeros((len(options), FEATURE_DIM), dtype=np.float32)
        rows[:, :STATE_DIM] = base
        for i, action in enumerate(options):
            offset = STATE_DIM
            type_index = _TYPE_INDEX.get(type(action))
            if type_index is not None:
                rows[i, offset + type_index] = 1.0
            rows[i, offset + TYPE_DIM:] = self._action_vector(state, seat, action)
        return rows

    def _action_vector(self, state: GameState, seat: int,
                       action: A.Action) -> np.ndarray:
        db = self.db
        me = state.players[seat]
        opp = state.players[1 - seat]
        v = np.zeros(ACTION_DIM, dtype=np.float32)

        card = self._action_card(state, action)
        if card is not None:
            defn = db[card.card_id]
            v[6] = (defn.level or 0) / 3.0
            v[7] = (defn.hp or 0) / 6.0
            v[8] = (defn.attack.damage if defn.attack else 0) / 5.0
            v[9] = float(defn.is_cookie)
            v[10] = float(defn.is_flip)
            v[11] = float(defn.type is CardType.ITEM)
            v[12] = float(defn.type is CardType.TRAP)
            v[13] = float(defn.type is CardType.STAGE)
            v[14] = defn.play_cost.total / 4.0
            v[15] = max(0, len(me.active_support()) - defn.play_cost.total) / 6.0
            v[16] = sum(1 for c in me.hand
                        if db[c.card_id].base_id == defn.base_id) / 4.0
            v[17] = float(isinstance(action, A.PlayCookie) and not me.battle)
            # Ability shape, not card identity. Nothing here is a per-card
            # one-hot, so a policy trained on one set can still read a card it
            # has never seen: it generalises over stats and abilities.
            v[19] = float(defn.has(Marker.ON_PLAY))
            v[20] = float(defn.has(Marker.ACTIVATE))
            v[21] = float(defn.has(Marker.BLOCKER))
            v[22] = float(defn.has(Marker.ONCE_PER_TURN))
            v[23] = float(self._has_effect(defn))

        if isinstance(action, A.Attack):
            attacker = me.find_cookie(action.attacker_uid)
            target = opp.find_cookie(action.target_uid)
            if attacker and target:
                damage = attacker.attack_damage(db)
                hp = target.remaining_hp
                kills = damage >= hp
                v[0] = damage / 5.0
                v[1] = hp / 6.0
                v[2] = float(kills)
                v[3] = float(kills and
                             opp.break_level_total(db) + target.level(db) >= 10)
                v[4] = target.level(db) / 3.0
                v[5] = max(0, damage - hp) / 5.0
                v[8] = damage / 5.0
                v[18] = attacker.remaining_hp / 6.0

        elif isinstance(action, A.Block):
            blocker = me.find_cookie(action.blocker_uid)
            if blocker:
                v[1] = blocker.remaining_hp / 6.0
                v[4] = blocker.level(db) / 3.0
                v[18] = blocker.remaining_hp / 6.0

        elif isinstance(action, A.ActivateSkill):
            cookie = me.find_cookie(action.source_uid)
            if cookie:
                defn = cookie.defn(db)
                v[4] = cookie.level(db) / 3.0
                v[6] = (defn.level or 0) / 3.0
                v[18] = cookie.remaining_hp / 6.0
                v[9] = 1.0
                v[2] = float(defn.has(Marker.ONCE_PER_TURN))

        return v

    def _action_card(self, state: GameState, action: A.Action):
        uid = getattr(action, "card_uid", None)
        if uid is None:
            return None
        found = state.find_card(uid)
        return found[2] if found else None
