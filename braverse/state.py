"""Mutable game state.

Everything here is plain data so that ``copy.deepcopy`` gives a correct,
independent clone — which is what a search-based agent will need later.
Card *definitions* are never copied; instances hold the card id and look the
printed card up through the shared :class:`CardDB`.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field

from .cards import CardDB, CardDef
from .enums import Color, Marker, Phase, Zone

_uid_counter = itertools.count(1)


@dataclass
class CardInstance:
    """One physical card."""

    uid: int
    card_id: str
    owner: int
    rested: bool = False
    face_up: bool = True

    @staticmethod
    def make(card_id: str, owner: int) -> "CardInstance":
        return CardInstance(uid=next(_uid_counter), card_id=card_id, owner=owner)

    def defn(self, db: CardDB) -> CardDef:
        return db[self.card_id]


@dataclass
class Cookie:
    """A Cookie in the battle area: one card plus its face-down HP pile.

    Cookies do not stack — there is no level-up in Braverse. A Cookie is played
    from hand for free and its Level only feeds the break-area clock.
    """

    uid: int
    owner: int
    card: CardInstance = None  # type: ignore[assignment]
    # Last element is the top of the pile: damage reveals "in order of the most
    # recently used", and setup places the first card at the very bottom.
    hp_cards: list[CardInstance] = field(default_factory=list)
    rested: bool = False
    summoned_this_turn: bool = True
    used_markers: set[str] = field(default_factory=set)       # 【Once Per Turn】 tracking
    skip_next_active: bool = False   # "not set as active next Active Phase"
    effect_damage_immune: bool = False  # "takes no damage from effects"
    damage_immune: bool = False         # "this Cookie takes no damage"
    hp_cannot_reach_zero: bool = False  # survives at 1 HP this battle
    damage_cap: int | None = None       # incoming attack damage ceiling
    attack_cost_discount: int = 0       # colour symbols shaved off the attack
    attack_cost_surcharge: int = 0      # extra {N} added to the attack
    level_override: int | None = None   # "this Cookie's LV. becomes N"
    activate_locked: bool = False       # its 【Activate】 is suppressed
    effect_damage_reduction: int = 0    # "-N damage from effects"
    equipment: list = field(default_factory=list)  # 【Equip】 attachments
    flip_disabled: bool = False         # its HP FLIPs cannot activate
    attack_bonus: int = 0                                     # cleared each turn
    attack_bonus_next_turn: int = 0   # applied at the owner's next Active Phase
    incoming_damage_reduction: int = 0   # "receives -N attack damage", per battle
    hp_bonus: int = 0                                         # +N HP effects, persistent

    def defn(self, db: CardDB) -> CardDef:
        return db[self.card.card_id]

    @property
    def remaining_hp(self) -> int:
        return len(self.hp_cards)

    def name(self, db: CardDB) -> str:
        return self.defn(db).name

    def level(self, db: CardDB) -> int:
        if self.level_override is not None:
            return self.level_override
        return self.defn(db).level or 1

    def max_hp(self, db: CardDB) -> int:
        return (self.defn(db).hp or 0) + self.hp_bonus

    def attack_damage(self, db: CardDB) -> int:
        attack = self.defn(db).attack
        base = attack.damage if attack else 0
        return max(0, base + self.attack_bonus)

    def has_marker(self, db: CardDB, marker: Marker) -> bool:
        return self.defn(db).has(marker)


@dataclass
class PlayerState:
    index: int
    deck: list[CardInstance] = field(default_factory=list)
    hand: list[CardInstance] = field(default_factory=list)
    battle: list[Cookie] = field(default_factory=list)
    support: list[CardInstance] = field(default_factory=list)
    stage: list[CardInstance] = field(default_factory=list)
    trash: list[CardInstance] = field(default_factory=list)
    break_area: list[CardInstance] = field(default_factory=list)
    extra_deck: list[CardInstance] = field(default_factory=list)

    supported_this_turn: bool = False
    refresh_count: int = 0    # how many [refresh]es this player has done
    activated_this_turn: set[int] = field(default_factory=set)   # source uids
    traps_this_attack: int = 0
    items_played_this_turn: int = 0
    hp_gained_this_turn: bool = False
    blockers_disabled: bool = False   # "your opponent cannot activate Blocker"
    traps_disabled: bool = False      # "your opponent cannot activate traps"
    set_active_at_end_turn: int = 0   # banked "when your turn ends" untaps
    used_once_per_game: set = field(default_factory=set)  # card uids
    support_skip_untap: set = field(default_factory=set)  # support card uids
    played_from_break_this_turn: set = field(default_factory=set)
    support_trashed_this_turn: int = 0
    hp_gain_locked: bool = False      # "cannot add HP via card effects"
    # (turn_counter, colour, level) for every Cookie of yours that fainted,
    # so cards can ask about "your opponent's previous turn".
    faint_log: list = field(default_factory=list)
    effect_damage_dealt_this_turn: bool = False
    arena_effect_damage_this_turn: bool = False
    # Reset for BOTH players each turn: "during this turn" clauses on one
    # player's card routinely ask about the other player's losses.
    cookies_fainted_this_turn: int = 0
    break_additions_this_turn: int = 0

    def active_support(self) -> list[int]:
        return [i for i, c in enumerate(self.support) if not c.rested]

    def support_colors(self, db: CardDB) -> list[Color]:
        """Colour of each support card; index-aligned with ``self.support``."""
        return [db[c.card_id].color for c in self.support]

    def active_support_colors(self, db: CardDB) -> tuple[list[int], list[Color]]:
        idx = self.active_support()
        return idx, [db[self.support[i].card_id].color for i in idx]

    @property
    def refreshed(self) -> bool:
        return self.refresh_count > 0

    def break_level_total(self, db: CardDB) -> int:
        return sum(db[c.card_id].level or 0 for c in self.break_area)

    def zone(self, zone: Zone) -> list:
        return {
            Zone.DECK: self.deck,
            Zone.HAND: self.hand,
            Zone.SUPPORT: self.support,
            Zone.STAGE: self.stage,
            Zone.TRASH: self.trash,
            Zone.BREAK: self.break_area,
            Zone.EXTRA_DECK: self.extra_deck,
        }[zone]

    def find_cookie(self, uid: int) -> Cookie | None:
        return next((c for c in self.battle if c.uid == uid), None)


@dataclass
class GameState:
    players: list[PlayerState]
    turn_player: int = 0
    phase: Phase = Phase.ACTIVE
    turn_number: int = 1
    turn_counter: int = 0   # increments on every turn change
    winner: int | None = None
    win_reason: str = ""
    rng: random.Random = field(default_factory=random.Random)
    log: list[str] = field(default_factory=list)

    def player(self, index: int) -> PlayerState:
        return self.players[index]

    @property
    def current(self) -> PlayerState:
        return self.players[self.turn_player]

    @property
    def opponent(self) -> PlayerState:
        return self.players[1 - self.turn_player]

    def opponent_of(self, index: int) -> PlayerState:
        return self.players[1 - index]

    @property
    def over(self) -> bool:
        return self.winner is not None

    def record(self, message: str) -> None:
        self.log.append(f"T{self.turn_number} P{self.turn_player} {message}")

    def all_cookies(self) -> list[tuple[int, Cookie]]:
        return [(p.index, c) for p in self.players for c in p.battle]

    def find_cookie(self, uid: int) -> tuple[PlayerState, Cookie] | None:
        for player in self.players:
            cookie = player.find_cookie(uid)
            if cookie is not None:
                return player, cookie
        return None

    def find_card(self, uid: int) -> tuple[PlayerState, Zone, CardInstance] | None:
        for player in self.players:
            for zone in (Zone.HAND, Zone.SUPPORT, Zone.STAGE, Zone.TRASH,
                         Zone.DECK, Zone.BREAK, Zone.EXTRA_DECK):
                for card in player.zone(zone):
                    if card.uid == uid:
                        return player, zone, card
        return None
