"""Tunable rules constants.

Every value here is taken from the official English PLAY GUIDE. Where the
guide is silent, the constant is marked NOT IN GUIDE and says what was assumed.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RulesConfig:
    # --- deck construction (PLAY GUIDE, "About Decks") --------------------
    deck_size: int = 60
    # "You can include up to 4 cards with the same card number" — the limit is
    # per card number, not per name, so alt arts of one number share the cap.
    max_copies_by_number: int = 4
    max_flip_cards: int = 16
    require_cookie_card: bool = True

    # --- setup (PLAY GUIDE, "How to Prepare for the Game") ----------------
    opening_hand: int = 6
    allow_mulligan: bool = True
    # If a player has no Cookie card in hand they reveal it, shuffle back and
    # redraw 6; the opponent may draw 1. Repeated until both have a Cookie.
    redraw_until_cookie: bool = True
    opponent_draws_on_redraw: int = 1

    # --- turn (PLAY GUIDE, "Gameplay") ------------------------------------
    draw_per_turn: int = 2
    # "You cannot draw a card from the deck on the first turn of the game."
    first_player_skips_first_draw: bool = True
    # "You cannot attack on the first turn of the game."
    first_turn_cannot_attack: bool = True
    supports_per_turn: int = 1
    traps_per_attack: int = 1

    # --- field ------------------------------------------------------------
    max_battle_cookies: int = 2
    # Cookies are played from hand for free: "When a Cookie card plays, you do
    # not [rest] the cost in the support area." There is no level-up mechanic —
    # Level only feeds the break-area clock and card effects.
    cookie_play_is_free: bool = True
    # "When revealing a Cookie card ... they are placed in the [active]
    # position", and nothing forbids attacking the turn it arrives.
    summoning_sickness: bool = False

    # --- losing -----------------------------------------------------------
    break_level_to_lose: int = 10
    # You lose only when BOTH are true: no Cookie in the battle area and no
    # Cookie card in hand that could be placed there.
    lose_when_no_cookie_anywhere: bool = True

    # --- refresh (PLAY GUIDE, "What is [refresh]?") -----------------------
    # Running the deck out does NOT lose the game. You put one LV.1-or-higher
    # Cookie card from your trash into your own break area, then shuffle the
    # trash back into the deck.
    refresh_on_empty_deck: bool = True
    refresh_break_cost: int = 1

    # --- NOT IN GUIDE -----------------------------------------------------
    # The guide does not cap how often a repeatable 【Activate】 skill may be
    # used within a turn. Treated as once per turn per source: the printed
    # 【Once Per Turn】 skills already are, and without a cap the legal-action
    # list does not terminate for skills the engine cannot prove paid a cost.
    activate_once_per_turn_per_source: bool = True
    # A FLIP that moves its own host ("Return this Cookie to your hand", Muscle
    # Cookie ST8-002, Blue Whale Cookie ST9-003) can be revealed by the very
    # damage that empties the HP pile. The guide does not say whether the flip
    # resolves first or the Cookie faints on reaching 0 HP first, and it decides
    # whether the opponent banks that Cookie's Level in the break area.
    #
    # Kept as "the flip wins", because a FLIP fires the instant it is revealed
    # and the Cookie is gone before anything checks its HP. That is also what
    # the deck search in `deckgen.py` found and exploited, so the measured
    # results in the README are all under this reading. Set False for the other
    # one: a Cookie already at 0 HP faints, and only a bounce with HP still on
    # the pile saves it.
    flip_bounce_beats_faint: bool = True
    # ----------------------------------------------------------------------


DEFAULT = RulesConfig()
