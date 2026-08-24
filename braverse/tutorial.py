"""The guided first game: a stacked deal and an opponent that plays to script.

The viewer's **Learn** button teaches by playing a real game — same engine, same
rules, same legal-action list. What it cannot afford is a random one. A tutorial
step says "click a Cookie in hand and play it into the empty slot", and a
shuffle that deals no second Cookie turns that instruction into a lie; a bot
that opens by killing the Cookie the next lesson is about does the same. So the
tutorial keeps the engine and replaces the two things that make a game
unpredictable: the shuffle, and the opponent's judgement.

Both halves are ordinary engine features rather than special cases inside it:
:class:`~braverse.engine.Game` takes ``shuffle=False`` and deals off the top of
the list it was given, and the opponent is a controller like any other. Nothing
in ``engine.py`` knows that a tutorial exists.

The decks are *permutations of the starter lists*, not new lists — built by
removing the scripted opening from the real 60 and putting it back on top. That
is what keeps them legal without a second legality argument: a permutation of a
legal deck is a legal deck, and ``tests/test_tutorial.py`` pins it anyway.
"""

from __future__ import annotations

from collections import Counter
from typing import Sequence

from . import actions as A
from .cards import CardDB, default_db
from .decks import STARTER_DECKS
from .enums import CardType
from .rps import CHOICES, GO_SECOND, ROCK, THROWS
from .state import Cookie, GameState

# ---------------------------------------------------------------------------
# the deal
# ---------------------------------------------------------------------------
# The player's opening six, in the order they are drawn. Every step of the
# course that asks for a card is answerable out of this hand:
#
#   ST9-006  Sea Fairy Cookie      LV3 HP6              the opening Cookie
#   ST9-013  Shimmering Moonlit Coral   ITEM            turn 1's support
#   ST9-015  Essence of the Ocean       ITEM            turn 2's support
#   ST9-005  Mystic Opal Cookie    LV1 HP3 【Blocker】   the second slot, and
#                                                       the Cookie that can
#                                                       block; its attack is
#                                                       {B}{N}, so two supports
#                                                       pay for it on turn 2
#   ST9-018  Tower of Frozen Waves      TRAP            the trap response
#   ST9-009  Wave Drop             LV1 HP2              a spare Cookie, and a
#                                                       {B} attack a single
#                                                       support can pay for
#
# The two Items come *before* the Cookies on purpose. The hand is drawn in this
# order and drawn cards land in this order, so the leftmost card is the one a
# hand reaches for first — and the first instruction in the course is "place
# any card as support". Left with the 【Blocker】 in front, the obvious answer
# to that instruction spends the Cookie two lessons are about. An Item there
# also makes the lesson itself land: the card you give up is one you would
# rather have kept.
PLAYER_OPENING = ("ST9-006", "ST9-013", "ST9-015", "ST9-005", "ST9-018", "ST9-009")

# The six behind it, which have to be a *second* teachable hand.
#
# The mulligan is a real question, offered before anything else, and the
# tutorial deliberately does not rig it away — "you need a Cookie in hand" is
# one of the lessons. But a stacked deck redraws off the top, so whatever sits
# behind the opening hand is the hand a player who takes the free mulligan
# keeps. Left as sorted filler that was four Gold Citrine Cookies and no
# support, no Trap and no attack anyone could pay for before turn 3.
#
# It costs nothing to make them good: if the hand is kept instead, these are the
# cards dealt face down as the opening Cookie's HP pile, where their faces never
# matter. So they are the same opening in different cards, position for position
# — a Cookie sturdier than the 【Blocker】, two Items, the 【Blocker】, a Trap, a
# cheap attacker:
#
#   ST9-007  Peppermint Cookie     LV2 HP5              the opening Cookie
#   ST9-013  Shimmering Moonlit Coral   ITEM
#   ST9-015  Essence of the Ocean       ITEM
#   ST9-005  Mystic Opal Cookie    LV1 HP3 【Blocker】
#   ST9-018  Tower of Frozen Waves      TRAP
#   ST9-002  Aquamarine Cookie     LV1 HP2  atk {N}
#
# The order matters as much as the contents, and both for the same reason: the
# bot attacks the sturdiest Cookie, so the 【Blocker】 must never be the biggest
# body on the mat or it becomes the target and can never step in front of
# anything. A first draft had a 2 HP Cookie in front and Mystic Opal at the
# back: the redraw opened with the 【Blocker】 as its Cookie, the bot attacked
# it every turn, and the block never once came up.
PLAYER_SPARE = ("ST9-007", "ST9-013", "ST9-015", "ST9-005", "ST9-018", "ST9-002")

# The opponent's, chosen for what it lets the bot *show* you rather than for
# strength. Cookiemals attacks for {G} — one support pays for it — so the very
# first bot turn contains an attack, which is what opens the block and trap
# windows the course wants to talk about.
#
#   ST8-010  Cookiemals            LV1 HP2  atk {G}     its opening Cookie
#   ST8-013  Windgrass                  ITEM            its first support
#   ST8-011  Kiwi Cookie           LV1 HP3 【Blocker】   its second body
#   ST8-015  Essence of the Tempest     ITEM            its second support
#   ST8-001  Greenbell Cookie      LV1 HP2  atk {N}
#   ST8-018  Piercing Arrow of Purity   TRAP
OPPONENT_OPENING = ("ST8-010", "ST8-013", "ST8-011", "ST8-015", "ST8-001", "ST8-018")

PLAYER_LIST = "st9_sea_fairy"
OPPONENT_LIST = "st8_wind_archer"


def attack_price(card_id: str, db: CardDB) -> int:
    """How much energy this card's attack costs, or 0 for a card with none."""
    defn = db[card_id]
    attack = defn.attack
    if attack is None:
        return 0
    return sum(n for _, n in attack.cost.colored) + attack.cost.generic


def stack(deck: Sequence[str], opening: Sequence[str],
          db: CardDB | None = None) -> list[str]:
    """Reorder ``deck`` so ``opening`` is on top, and put the cheap cards next.

    Three orderings, for three reasons. ``opening`` is the scripted hand, so it
    goes first, in order.

    Then the FLIP cards go *last*, because the cards immediately behind the hand
    are not drawn — they are dealt face down as the opening Cookie's HP pile,
    and a FLIP turning over in the tutorial's first exchange fires an effect
    three lessons before the course explains what a FLIP is.

    Everything in between is ordered by what its attack costs, cheapest first.
    That is what keeps the game moving. Both sides are held to two supports
    here — the opponent by its own policy, the player by having placed two — so
    a deck that deals its three-energy Cookies first deals bodies that cannot
    swing at all. Ordered by id, the opponent's list did exactly that: kill its
    one cheap attacker on turn 2 and it drew Leek Cookie, Red Panna Cotta
    Cookie and Wind Archer Cookie, could pay for none of them, and the game
    stalled into two players passing at each other until the decks ran out. Ties
    break by id, so the list is still a *fixed* permutation.
    """
    db = db or default_db()
    left = Counter(deck)
    for card_id in opening:
        if not left[card_id]:
            raise ValueError(f"{card_id} is not in this deck")
        left[card_id] -= 1
    rest = sorted(left.elements(),
                  key=lambda cid: (db[cid].is_flip, attack_price(cid, db), cid))
    return [*opening, *rest]


def player_deck(db: CardDB | None = None) -> list[str]:
    """The stacked ST9 list the person taking the tutorial plays."""
    return stack(STARTER_DECKS[PLAYER_LIST], PLAYER_OPENING + PLAYER_SPARE, db)


def opponent_deck(db: CardDB | None = None) -> list[str]:
    """The stacked ST8 list the scripted opponent plays."""
    return stack(STARTER_DECKS[OPPONENT_LIST], OPPONENT_OPENING, db)


# ---------------------------------------------------------------------------
# the opponent
# ---------------------------------------------------------------------------
class TutorialOpponent:
    """A deterministic opponent that plays like a demonstration.

    Not a weaker heuristic — a *predictable* one. It holds no RNG at all, so
    the same tutorial always plays out the same way, and its policy is written
    as an explicit order of preference rather than a scoring function, so what
    it will do next can be read off this file:

    1. place a support, while it has one to place;
    2. put out a Cookie whenever a battle slot is free;
    3. attack, if it can pay for one;
    4. end the turn.

    Two of those choices are teaching choices rather than good ones.

    It never activates a skill, plays an Item, sets a Stage or springs a trap:
    the course has enough to say without the board sprouting things it has not
    introduced, and a bot that quietly wins the game with an Item is a bad
    first opponent.

    And it attacks the Cookie with the **most** remaining HP. A real opponent
    picks off the weak one; this one goes for the wall, which is what keeps the
    lesson available — the player's 【Blocker】 is never the thing being
    attacked, so "you can block this" is always a move it is possible to make.
    """

    name = "tutorial"

    # Two supports is the whole leash, and it is the only number here that
    # matters. ST8's cheap attacks — Cookiemals {G}, Greenbell {N}, Kiwi
    # {G}{N} — cost one or two, and everything that hits for 3 costs three. So
    # a bot that never lays a third support can never swing for more than 2,
    # whatever it draws, and it cannot grind a beginner out while they are
    # still reading. Uncapped it opened a Leek Cookie and had the game by turn
    # 9 against a player who was following the course.
    MAX_SUPPORTS = 2
    # And one swing a turn, so a lesson costs you a Cookie at worst rather than
    # the board.
    ATTACKS_PER_TURN = 1

    def __init__(self, db: CardDB | None = None, seat: int = 1):
        self.db = db or default_db()
        self.seat = seat
        # The toss cycles rather than repeating: a fixed throw ties forever
        # against a player who keeps throwing the same thing, and every tie is
        # another "throw again" in the face of someone who has not been told
        # why. Cycling settles it in at most two rounds.
        self._throws = 0
        self._swings = 0
        self._turn = None

    # -- turn actions ----------------------------------------------------
    def choose_action(self, state: GameState, options: Sequence[A.Action]):
        if not options:
            return None
        self._new_turn(state)
        pick = (self._support(state, options)
                or self._cookie(state, options)
                or self._swing(state, options))
        if pick is not None:
            return pick
        end = self._first(options, A.EndTurn)
        # A response window has no End turn in it — only Pass, and whatever the
        # defender could do about the attack. This bot does neither.
        return end or self._first(options, A.Pass) or options[0]

    def _first(self, options: Sequence[A.Action], kind):
        return next((o for o in options if isinstance(o, kind)), None)

    def _new_turn(self, state: GameState) -> None:
        """Reset the per-turn leash. Keyed on the turn number and the seat to
        move, because a response window on the other player's turn comes
        through here too."""
        key = (state.turn_number, state.turn_player)
        if key != self._turn:
            self._turn = key
            self._swings = 0

    def _support(self, state: GameState, options: Sequence[A.Action]):
        """Spend the least useful card, and stop at the leash."""
        me = state.players[self.seat]
        if len(me.support) >= self.MAX_SUPPORTS:
            return None
        plays = [o for o in options if isinstance(o, A.PlaceSupport)]
        if not plays:
            return None
        # A Cookie is the last thing to burn as energy: it is the one card type
        # you can lose the game for running out of.
        return min(plays, key=lambda o: (self._is_cookie(state, o.card_uid),
                                         self._card_id(state, o.card_uid)))

    def _is_cookie(self, state: GameState, uid: int) -> bool:
        found = state.find_card(uid)
        return bool(found and self.db[found[2].card_id].is_cookie)

    def _card_id(self, state: GameState, uid: int) -> str:
        found = state.find_card(uid)
        return found[2].card_id if found else ""

    def _cookie(self, state: GameState, options: Sequence[A.Action]):
        """The biggest body it can put in a free slot, so the board is stable.

        Ordered by printed HP and then by card id: two cards with the same HP
        must not be separated by hand order, or the deal and the policy would
        have to be reasoned about together.
        """
        plays = [o for o in options if isinstance(o, A.PlayCookie)]
        if not plays:
            return None
        return max(plays, key=lambda o: self._hp_key(state, o.card_uid))

    def _hp_key(self, state: GameState, uid: int):
        found = state.find_card(uid)
        defn = self.db[found[2].card_id] if found else None
        return ((defn.hp or 0), defn.id) if defn else (0, "")

    def _swing(self, state: GameState, options: Sequence[A.Action]):
        if self._swings >= self.ATTACKS_PER_TURN:
            return None
        swings = [o for o in options if isinstance(o, A.Attack)]
        if not swings:
            return None
        self._swings += 1
        return max(swings, key=lambda o: self._target_key(state, o.target_uid))

    def _target_key(self, state: GameState, uid: int):
        """Hit the sturdiest Cookie, and break ties by uid rather than by luck."""
        found = state.find_cookie(uid)
        cookie = found[1] if found else None
        return (cookie.remaining_hp, -cookie.uid) if cookie else (-1, 0)

    # -- everything else -------------------------------------------------
    def choose(self, state: GameState, prompt: str, options: Sequence, *,
               optional: bool):
        """Answer the small questions without ever reaching for the RNG.

        The toss is the one answer that is a decision about the tutorial rather
        than about the game: a fixed throw, and — on the rounds it wins —
        handing the first turn to the player. The course is written from the
        opening player's seat (support, Cookie, end turn, then a turn with an
        attack in it), and which side of a coin the person landed on is not
        something a tutorial should reorder itself around. Losing the toss is
        still a real outcome they see and get told about; it just does not
        change who starts.
        """
        if not options:
            return None
        if list(options) == list(THROWS):
            throw = THROWS[self._throws % len(THROWS)]
            self._throws += 1
            return throw
        if list(options) == list(CHOICES):
            return GO_SECOND
        if all(isinstance(o, bool) for o in options):
            # Optional costs and "do you want to" questions: never, so the bot
            # does nothing the course has not covered.
            return False if False in options else options[0]
        if all(isinstance(o, Cookie) for o in options):
            return max(options, key=lambda c: (c.remaining_hp, -c.uid))
        return options[0]


def make_opponent(db: CardDB | None = None, seat: int = 1) -> TutorialOpponent:
    return TutorialOpponent(db=db, seat=seat)


# ---------------------------------------------------------------------------
# what the course is entitled to assume
# ---------------------------------------------------------------------------
# Every step of the browser course that waits on the board waits on one of
# these. They are asserted against a real game in `tests/test_tutorial.py`, so
# a change to a card, to the starter lists or to the engine that would strand a
# step fails there rather than in front of someone learning the game.
GUARANTEES = (
    "you always take the first turn, whichever way the toss goes",
    "the opening hand holds three Cookies, two Items and a Trap",
    "and so does the hand behind it, for anyone who takes the mulligan",
    "turn 1 offers a support to place and a Cookie to play",
    "the Cookie you play into the second slot is a 【Blocker】",
    "the opponent attacks on its first turn, into a block and a trap window",
    "turn 2 offers an attack you can pay for",
)
