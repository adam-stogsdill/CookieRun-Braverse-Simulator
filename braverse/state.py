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


def card_label(defn: CardDef) -> str:
    """`Name (ST9-007)` — how a card is named in the log.

    271 of 813 names are printed on more than one card, so the name alone does
    not say *which* card acted. The viewer turns every name in the log into a
    hover target and looks it up by name, which meant a line about one printing
    previewed a different card with the same name. The id is what tells them
    apart, so the log carries it everywhere it names a card.
    """
    return f"{defn.name} ({defn.id})"


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

    def label(self, db: CardDB) -> str:
        return card_label(self.defn(db))


@dataclass
class Cookie:
    """A Cookie in the battle area: one card plus its face-down HP pile.

    Cookies do not stack in normal play — there is no level-up in Braverse. A
    Cookie is played from hand for free and its Level only feeds the break-area
    clock. The one exception is 【Awaken】, which places an EXTRA card on top of
    a Cookie already in the battle area: the pile underneath is kept in
    `under`, and the Cookie takes the EXTRA card's name, Level and attack.
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
    # "During this turn, if this Cookie's HP was reduced" — set by damage and
    # by any effect that takes cards off the pile, cleared with the rest of the
    # per-turn Cookie flags.
    hp_reduced_this_turn: bool = False
    effect_damage_reduction: int = 0    # "-N damage from effects"
    equipment: list = field(default_factory=list)  # 【Equip】 attachments
    # 【Awaken】: the cards this Cookie was stacked on top of, oldest first.
    # The Cookie *is* `card`; `under` is only the paper underneath it, which
    # travels with the stack and joins it wherever the stack ends up.
    under: list = field(default_factory=list)
    flip_disabled: bool = False         # its HP FLIPs cannot activate
    attack_bonus: int = 0                                     # cleared each turn
    attack_bonus_next_turn: int = 0   # applied at the owner's next Active Phase
    incoming_damage_reduction: int = 0   # "receives -N attack damage", per battle
    # "receives -N from all damage" — attack and effect alike, and unlike
    # `incoming_damage_reduction` it lasts until the owner's next Active Phase
    # rather than being cleared between battles.
    all_damage_reduction: int = 0

    def defn(self, db: CardDB) -> CardDef:
        return db[self.card.card_id]

    @property
    def remaining_hp(self) -> int:
        return len(self.hp_cards)

    @property
    def spent_cards(self) -> list:
        """What is left behind when this Cookie leaves the battle area.

        Its HP pile, plus anything it was 【Awaken】ed on top of. Every caller
        that moves a Cookie out of play sheds this to the trash — the Cookie's
        own card is the only part that follows it to wherever it is going.
        """
        return [*self.hp_cards, *self.under]

    def name(self, db: CardDB) -> str:
        return self.defn(db).name

    def label(self, db: CardDB) -> str:
        """The Cookie's name for the log, with the id that disambiguates it."""
        return card_label(self.defn(db))

    def level(self, db: CardDB) -> int:
        if self.level_override is not None:
            return self.level_override
        return self.defn(db).level or 1

    def max_hp(self, db: CardDB) -> int:
        """The printed HP. Healing refills the pile, it never raises this — a
        Cookie above its printed HP is shown as an overheal rather than as a
        bigger Cookie."""
        return self.defn(db).hp or 0

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
    # Banked "when your turn ends, set N cards as active" riders, one entry
    # per rider as (card id that banked it, how many). A list rather than a
    # running total so each one is its own item in the end-of-turn queue and
    # can be named and ordered like any other end-of-turn effect.
    end_turn_untaps: list = field(default_factory=list)
    used_once_per_game: set = field(default_factory=set)  # card uids
    support_skip_untap: set = field(default_factory=set)  # support card uids
    played_from_break_this_turn: set = field(default_factory=set)
    # 【Awaken】 gates ask which Cookie was replayed out of a graveyard zone
    # this turn, so the trash case is tracked the same way as the break one.
    played_from_trash_this_turn: set = field(default_factory=set)
    support_trashed_this_turn: int = 0
    # Cookies that left your battle area for the bottom of your deck this turn.
    # BS9-088's 【Awaken】 gate is the only card that asks, and it asks because
    # the two BS9 Cookies that bury themselves are what set it up.
    cookies_to_deck_bottom_this_turn: int = 0
    # The same, counting the top of the deck as well. BS9-083 asks "top or
    # bottom" and BS9-088 asks only about the bottom, so the two are counted
    # separately rather than one being derived from the other.
    cookies_to_deck_this_turn: int = 0
    # 【Arena】 Cookies added to your break area this turn. A subset of
    # `break_additions_this_turn`, kept apart because the break area holds
    # cards and this asks about the keyword on them as they arrive.
    arena_break_additions_this_turn: int = 0
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
    # Structured counterpart to the prose log, for anything that needs to read
    # back *what* happened rather than parse a sentence — the viewer animates
    # from it, and it keeps knowledge like "this was attack damage, not a
    # rider" out of string matching. Append-only; consumers track their own
    # read position.
    events: list = field(default_factory=list)
    # Names of the cards whose effects are resolving right now, outermost
    # first. `record` stamps the innermost onto every line it writes, so the
    # log says *what* caused a draw, a heal or a point of damage — a FLIP, a
    # trap and an 【Activate】 skill all read identically without it.
    effect_sources: list = field(default_factory=list)
    # Cards the player being asked is currently *looking at* — the top three
    # off their own deck, say. They are not in any zone the board draws, and
    # they are the asking player's secret, so they travel on the question
    # rather than in the snapshot: `_hide_pending` already strips a question
    # the other seat is not being asked, which is exactly the right rule for
    # these. Set only for the duration of one `choose`.
    viewing: list = field(default_factory=list)

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
        self.log.append(f"T{self.turn_number} P{self.turn_player} "
                        f"{self.source_stamp()}{message}")

    def source_stamp(self) -> str:
        """`[Card Name]`, or `[Card Name \u00b7 trap]` when the kind is known.

        The name alone left "effect damage" doing far too much work: the same
        card can hit you as a trap, as an 【Activate】 skill or as a FLIP that
        turned over mid-swing, and the record read identically for all three.
        Entries are a bare name (older callers), a `(name, kind)` pair or a
        `(name, kind, label)` triple; all three are accepted so nothing has to
        be updated in lockstep. The stamp uses the *label* — the name with the
        card's id — because a name can be printed on more than one card.
        """
        name, kind = self.source_label(), self.source_kind()
        if not name:
            return ""
        return f"[{name} \u00b7 {kind}] " if kind else f"[{name}] "

    def source_name(self) -> str:
        """The card whose effect is resolving right now, or "" for none.

        The bare name: this is what the viewer floats over the board next to a
        damage number, where there is no room for an id and no ambiguity to
        resolve. The log wants :meth:`source_label` instead.
        """
        if not self.effect_sources:
            return ""
        top = self.effect_sources[-1]
        return top[0] if isinstance(top, tuple) else top

    def source_label(self) -> str:
        """That card as the log names it — `Name (ST9-007)`."""
        if not self.effect_sources:
            return ""
        top = self.effect_sources[-1]
        if isinstance(top, tuple) and len(top) > 2 and top[2]:
            return top[2]
        return self.source_name()

    def source_kind(self) -> str:
        """One word for what sort of thing that is — trap, FLIP, 【Activate】."""
        if not self.effect_sources:
            return ""
        top = self.effect_sources[-1]
        return top[1] if isinstance(top, tuple) else ""

    def all_cookies(self) -> list[tuple[int, Cookie]]:
        return [(p.index, c) for p in self.players for c in p.battle]

    def find_cookie(self, uid: int) -> tuple[PlayerState, Cookie] | None:
        for player in self.players:
            cookie = player.find_cookie(uid)
            if cookie is not None:
                return player, cookie
        return None

    def is_attached(self, uid: int) -> bool:
        """Whether this card is 【Equip】ped to a Cookie in the battle area.

        Equipment is not a `Zone` — it hangs off a Cookie and leaves play with
        it — so `find_card` cannot see it. An item that equipped itself has
        chosen where it lives just as surely as one that placed itself in the
        support area, and filing it in the trash as well would leave one
        CardInstance in two places at once.
        """
        return any(any(card.uid == uid for card in cookie.equipment)
                   for player in self.players for cookie in player.battle)

    def find_card(self, uid: int) -> tuple[PlayerState, Zone, CardInstance] | None:
        for player in self.players:
            for zone in (Zone.HAND, Zone.SUPPORT, Zone.STAGE, Zone.TRASH,
                         Zone.DECK, Zone.BREAK, Zone.EXTRA_DECK):
                for card in player.zone(zone):
                    if card.uid == uid:
                        return player, zone, card
        return None
