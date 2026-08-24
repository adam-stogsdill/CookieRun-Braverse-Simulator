"""The guided first game, played end to end.

The browser course in `viewer/tutorial.js` is a list of moments it waits for:
"you have not placed a support yet", "you have a Cookie you can play", "there
is an attack in this turn's options". None of those are things a step can make
happen — the player makes them happen, out of the hand the deal gave them. So
what these tests do is play the course's own path against a real `Game` and
assert every one of those moments actually arrives, in order, with the tutorial
deal and the scripted opponent.

A step stranded here is a step that would strand a person learning the game
with an instruction they cannot follow, which is the one failure a tutorial
must not have.
"""

import pytest

from braverse import Game, default_db, validate
from braverse import actions as A
from braverse import tutorial as T
from braverse.decks import STARTER_DECKS
from braverse.enums import CardType, Marker
from braverse.rps import GO_SECOND, THROWS, decide_first_player


@pytest.fixture(scope="module")
def db():
    return default_db()


# ---------------------------------------------------------------------------
# the deal
# ---------------------------------------------------------------------------
def test_the_stacked_decks_are_the_starter_decks_reordered(db):
    """Legality by construction: a permutation of a legal deck is legal.

    Both lists are the shipped starter decks with the scripted opening moved to
    the top, so neither can drift out of the 60-card / 4-per-number / 16-FLIP
    rules by being edited here — only by the starter list itself changing,
    which this would also catch.
    """
    for stacked, name in ((T.player_deck(db), T.PLAYER_LIST),
                          (T.opponent_deck(db), T.OPPONENT_LIST)):
        assert sorted(stacked) == sorted(STARTER_DECKS[name])
        report = validate(stacked, db)
        assert report.ok, report.problems


def test_the_scripted_openings_are_dealt_in_order(db):
    """`shuffle=False` means the top of the list is the top of the deck."""
    game = tutorial_game(db)
    game.setup()
    # The opening Cookie has already been taken out of each hand by setup, so
    # compare against the deal minus that one card.
    for seat, opening in ((0, T.PLAYER_OPENING), (1, T.OPPONENT_OPENING)):
        hand = [c.card_id for c in game.state.players[seat].hand]
        played = [c.card.card_id for c in game.state.players[seat].battle]
        assert sorted(hand + played) == sorted(opening)


def test_the_hand_behind_the_opening_hand_is_also_teachable(db):
    """Someone who takes the free mulligan must not be left with junk.

    A stacked deck redraws off the top, so the six cards behind the opening
    hand *are* the mulligan. Both hands have to carry the same lessons: a
    Cookie to open with, a second Cookie for the empty slot, something to spend
    as support, and a Trap for the response window.
    """
    for hand in (T.PLAYER_OPENING, T.PLAYER_SPARE):
        kinds = [db[cid] for cid in hand]
        cookies = [d for d in kinds if d.is_cookie]
        assert len(cookies) >= 2, hand
        assert any(d.type is CardType.TRAP for d in kinds), hand
        assert any(d.type is CardType.ITEM for d in kinds), hand
        # An attack a single support can pay for, so the attack step lands on
        # the first turn the player can afford anything at all.
        assert any(d.attack and sum(n for _, n in d.attack.cost.colored)
                   + d.attack.cost.generic <= 2 for d in cookies), hand


def test_the_shuffle_is_still_on_for_everyone_else(db):
    """The stacked deal is opt-in; nothing else may become deterministic."""
    ordinary = Game([T.player_deck(db), T.opponent_deck(db)],
                    [T.make_opponent(db, 0), T.make_opponent(db, 1)],
                    db=db, seed=7)
    ordinary.setup()
    dealt = [c.card_id for c in ordinary.state.players[0].hand]
    assert dealt != list(T.PLAYER_OPENING[:len(dealt)])


# ---------------------------------------------------------------------------
# the toss
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("throw", THROWS)
def test_the_player_takes_the_first_turn_whichever_way_the_toss_goes(db, throw):
    """The course is written from the opening player's seat.

    Turn 1 teaches support, a Cookie and ending the turn — and that the opener
    cannot attack — and only then does a turn with an attack in it come round.
    Losing the toss must not reorder that, so the scripted opponent hands the
    first turn back when it wins. The toss itself is still real: the player
    throws, and is told what happened.
    """
    game = tutorial_game(db, student=Student(db, throw=throw))
    toss = decide_first_player(game._controllers, game.state, game.state.rng)
    assert toss.first_player == 0
    if toss.chooser == 1:
        assert toss.choice == GO_SECOND


# ---------------------------------------------------------------------------
# the course
# ---------------------------------------------------------------------------
# One entry per moment the browser course waits for, in the order it waits for
# them. `want` is the move the person following that step would make; the
# student below takes it as soon as it is offered and not before.
COURSE = (
    ("place your first support", lambda db, s, o: isinstance(o, A.PlaceSupport)),
    # Any Cookie: the step says "click a Cookie card in hand and play it", and
    # the deal is arranged so the leftmost one is the 【Blocker】 the block
    # aside later needs. Matching the step rather than the card is the point —
    # the test must fail when the *course* is unfollowable, not when a player
    # would have played a different Cookie.
    ("fill the second slot", lambda db, s, o: isinstance(o, A.PlayCookie)),
    ("pass the turn", lambda db, s, o: isinstance(o, A.EndTurn)),
    # Their turn. The bot attacks, which opens the two response windows the
    # course has asides for. One response per attack, so the trap goes here and
    # the block waits for the next swing.
    ("spring a trap", lambda db, s, o: isinstance(o, A.PlayTrap)),
    ("place a second support", lambda db, s, o: isinstance(o, A.PlaceSupport)),
    ("swing", lambda db, s, o: isinstance(o, A.Attack)),
    ("pass again", lambda db, s, o: isinstance(o, A.EndTurn)),
    ("block", lambda db, s, o: isinstance(o, A.Block)),
)


def is_play_of(db, state, option, marker):
    """A PlayCookie whose card carries `marker` — the 【Blocker】, here."""
    if not isinstance(option, A.PlayCookie):
        return False
    found = state.find_card(option.card_uid)
    return bool(found and db[found[2].card_id].has(marker))


class Student:
    """A controller that plays the course, and records where it got to.

    It only ever takes the move the current step is asking for. Anything else
    on offer is declined — pass out of a response window, end the turn — so a
    step that never becomes available shows up as a short `reached` list rather
    than as a game that wandered off and happened to look fine.
    """

    name = "student"

    def __init__(self, db, throw="rock"):
        self.db = db
        self.throw = throw
        self.todo = list(COURSE)
        self.reached = []
        self.offered = []          # every action list it was shown

    def choose_action(self, state, options):
        self.offered.append(list(options))
        if self.todo:
            label, want = self.todo[0]
            hit = next((o for o in options if want(self.db, state, o)), None)
            if hit is not None:
                self.reached.append(label)
                self.todo.pop(0)
                return hit
        # Not this window. Get out of it without doing anything the course has
        # not asked for: pass a response window, otherwise end the turn.
        return (next((o for o in options if isinstance(o, A.Pass)), None)
                or next((o for o in options if isinstance(o, A.EndTurn)), None)
                or options[0])

    def choose(self, state, prompt, options, *, optional):
        if not options:
            return None
        if list(options) == list(THROWS):
            return self.throw
        # The opening Cookie: the sturdiest, which is what the step suggests.
        if "Opening Cookie" in prompt:
            return max(options, key=lambda c: (self.db[c.card_id].hp or 0, c.card_id))
        if all(isinstance(o, bool) for o in options):
            return options[0]
        return options[0]

    # Whether this student takes the free redraw. The course tells them to keep
    # a hand with a Cookie in it, but the button is right there, and a stacked
    # deck means the redraw is a *known* hand — so both branches are testable.
    mulligans = False

    def wants_mulligan(self, state, hand, *, free=True):
        """Keep, unless this student was built to take the redraw."""
        if self.mulligans and free:
            self.mulligans = False      # the free one only
            return True
        return False


def tutorial_game(db, student=None) -> Game:
    """Exactly the game `/api/new {"tutorial": true}` builds."""
    return Game([T.player_deck(db), T.opponent_deck(db)],
                [student or Student(db), T.make_opponent(db, 1)],
                db=db, seed=0, shuffle=False, first_player=0)


def play_the_course(db, throw="rock", limit=400, mulligan=False):
    student = Student(db, throw=throw)
    student.mulligans = mulligan
    game = tutorial_game(db, student)
    toss = decide_first_player(game._controllers, game.state, game.state.rng)
    game.first_player = toss.first_player
    game.setup()
    for _ in range(limit):
        if game.state.over or not student.todo:
            break
        options = game.legal_actions()
        if not options:
            break
        action = game.controller(game.to_move()).choose_action(game.state, options)
        if action is None:
            break
        game.step(action)
    return game, student


def test_the_whole_course_can_be_completed(db):
    """Every step's moment arrives, in order, and the game is still alive."""
    game, student = play_the_course(db)
    assert student.reached == [label for label, _ in COURSE], student.reached
    assert not game.state.over, game.state.win_reason
    # And it happens promptly: a course that only completes on turn 20 is one
    # nobody would still be reading by the time it did.
    assert game.state.turn_number <= 6, game.state.turn_number


@pytest.mark.parametrize("throw", THROWS)
def test_the_course_completes_from_any_throw(db, throw):
    """The one free choice before the deal cannot change the lesson order."""
    _, student = play_the_course(db, throw=throw)
    assert student.reached == [label for label, _ in COURSE], student.reached


def test_turn_one_offers_a_support_and_a_cookie_and_no_attack(db):
    """The three things the turn-1 steps say, read off the real action list."""
    game, student = play_the_course(db)
    first = student.offered[0]
    kinds = {type(o) for o in first}
    assert A.PlaceSupport in kinds
    assert A.PlayCookie in kinds
    assert A.EndTurn in kinds
    # "Turn 1 has no attack in it for the opener" — the step says so, so it had
    # better be true.
    assert A.Attack not in kinds


def test_the_opponent_attacks_into_a_block_and_a_trap(db):
    """Both asides need a window to appear in; the bot has to provide it.

    It attacks on its first turn — one support pays for Cookiemals — and it
    attacks the sturdiest Cookie, so the 【Blocker】 is never itself the target
    and blocking is always a move that exists.
    """
    game, student = play_the_course(db)
    windows = [o for o in student.offered
               if any(isinstance(a, A.Pass) for a in o)]
    assert windows, "the bot never attacked"
    assert any(any(isinstance(a, A.PlayTrap) for a in w) for w in windows)
    assert any(any(isinstance(a, A.Block) for a in w) for w in windows)


def test_the_player_is_never_close_to_losing_during_the_course(db):
    game, _ = play_the_course(db)
    me = game.state.players[0]
    assert me.break_level_total(db) < 4
    assert me.battle, "left with no Cookie in the battle area"


def test_the_course_completes_after_a_mulligan(db):
    """The free redraw is a real button, so the hand behind the deal is real too.

    A stacked deck redraws off the top: taking the mulligan hands you
    `PLAYER_SPARE` instead of `PLAYER_OPENING`. Every step still has to land —
    a Cookie to open with, a second for the empty slot, a card to spend, a Trap
    for the window, and an attack cheap enough to pay for early.
    """
    game, student = play_the_course(db, mulligan=True)
    assert student.reached == [label for label, _ in COURSE], student.reached
    assert not game.state.over, game.state.win_reason


def test_a_mulligan_deals_the_hand_behind_the_deal(db):
    student = Student(db)
    student.mulligans = True
    game = tutorial_game(db, student)
    game.setup()
    hand = [c.card_id for c in game.state.players[0].hand]
    played = [c.card.card_id for c in game.state.players[0].battle]
    assert sorted(hand + played) == sorted(T.PLAYER_SPARE)


def test_the_tutorial_is_reproducible(db):
    """No RNG anywhere on either side: the same course, twice, card for card."""
    runs = []
    for _ in range(2):
        game, student = play_the_course(db)
        runs.append((student.reached, list(game.state.log),
                     [c.card_id for c in game.state.players[0].hand],
                     [c.card_id for c in game.state.players[1].hand]))
    assert runs[0] == runs[1]
