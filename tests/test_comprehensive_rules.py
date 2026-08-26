"""Rules pinned to the Comprehensive Rules (Ver.1.8, 2026-07-27).

Everything here cites the section it comes from. The PLAY GUIDE the engine was
first written against is a summary; these are the clauses that document spells
out and that the engine used to read differently — one play from the EXTRA deck
a turn, a Cookie leaving the battle area mid-attack, 【Special Play】 as a gate,
and the third defeat condition.
"""

import pytest

from braverse import Game, default_db, validate
from braverse import actions as A
from braverse.effects import SPECIAL_PLAYS
from braverse.enums import CardType, Marker
from braverse.state import CardInstance


@pytest.fixture(scope="module")
def db():
    return default_db()


class _Auto:
    """A controller that takes the first thing it is offered."""

    def choose_action(self, state, options):
        return options[0]

    def choose(self, state, prompt, options, *, optional=False):
        return options[0] if options else None

    def choose_many(self, state, prompt, options, *, count, optional, up_to=False):
        return options[:count]

    def confirm(self, state, prompt):
        return True


def _filler(db) -> str:
    return next(c.id for c in db.cards.values() if c.type is CardType.ITEM)


def _game(db, deck, extra=None, *, seed=1):
    game = Game([deck, list(deck)], [_Auto(), _Auto()],
                extra_decks=[list(extra or []), []], db=db, seed=seed,
                shuffle=False)
    game.setup()
    return game


# --- 4-10 【Special Play】 ---------------------------------------------------
def test_special_play_cookies_all_know_their_condition(db):
    """4-10-1-1: a 【Special Play】 Cookie cannot be played while its condition
    is unmet, so one with no registered condition must be unplayable rather
    than free. This asserts the pool is complete instead."""
    printed = {c.base_id for c in db.cards.values()
               if c.has(Marker.SPECIAL_PLAY)}
    assert printed == set(SPECIAL_PLAYS), printed ^ set(SPECIAL_PLAYS)


def test_special_play_is_not_a_move_while_its_condition_is_unmet(db):
    """P-162 is the LV.1 {K} body BS11-111 has to trash to arrive; without one
    on the board the card is not a legal move at all."""
    white = next(c.id for c in db.cards.values()
                 if c.is_cookie and c.level == 1 and c.type is not CardType.EXTRA
                 and c.color is not None and c.color.name != "BLACK")
    game = _game(db, [white] + ["BS11-111"] * 2 + [_filler(db)] * 57)
    me = game.state.players[0]
    card = next(c for c in me.hand if c.card_id == "BS11-111")
    assert not game.can_play_cookie(me, card)
    assert not [a for a in game.legal_actions()
                if isinstance(a, A.PlayCookie) and a.card_uid == card.uid]


def test_special_play_pays_by_trashing_not_by_fainting(db):
    """"Place 1 {K} LV.1 Cookie ... into your trash" — the trash, so the
    opponent banks no Level for the body you spent arriving (3-8-1-1)."""
    game = _game(db, ["P-162"] + ["BS11-111"] * 2 + [_filler(db)] * 57)
    me = game.state.players[0]
    card = next(c for c in me.hand if c.card_id == "BS11-111")
    game.step(A.PlayCookie(card.uid, None))
    assert [c.card.card_id for c in me.battle] == ["BS11-111"]
    assert any(c.card_id == "P-162" for c in me.trash)
    assert not me.break_area


def test_special_play_can_be_played_out_of_a_full_battle_area(db):
    """3-5-6-1-1: the condition empties the slots before the Cookie needs one,
    and the emptying must not trigger the "field a replacement" prompt — the
    Cookie being played is the replacement."""
    game = _game(db, ["P-162"] + ["BS11-115"] * 2 + [_filler(db)] * 57)
    me = game.state.players[0]
    me.battle.clear()
    for cid in ("BS11-111", "BS11-112"):
        game._deploy_cookie(me, CardInstance.make(cid, 0), run_on_play=False)
    card = CardInstance.make("BS11-115", 0)
    me.hand.append(card)
    assert len(me.battle) == 2
    assert game.can_play_cookie(me, card)
    game.step(A.PlayCookie(card.uid, None))
    assert [c.card.card_id for c in me.battle] == ["BS11-115"]
    assert not game.state.over


def test_special_play_keyword_must_open_its_line(db):
    """BS11-105 only *names* 【Special Play】 in the middle of its attack text.
    Reading that as the keyword gave it a play condition it does not print."""
    assert not db["BS11-105"].has(Marker.SPECIAL_PLAY)
    assert db["BS11-115"].has(Marker.SPECIAL_PLAY)


def test_deck_needs_a_cookie_that_can_open_the_game(db):
    """5-1-1-2: at least 1 non-【Special Play】 Cookie card."""
    report = validate(["BS11-111"] * 4 + [_filler(db)] * 56, db)
    assert any("Special Play" in p for p in report.problems)


# --- 6-5-2-2 one EXTRA play a turn ------------------------------------------
def test_only_one_extra_card_may_be_played_per_turn(db):
    """"The Turn Player can, once per turn, play an 【EXTRA】 Cookie card or
    【Awakened】 Cookie card." A second open gate is not a second play."""
    game = _game(db, ["BS5-062"] * 60)
    me = game.state.players[0]
    assert not me.extra_played_this_turn
    me.extra_played_this_turn = True
    assert not [a for a in game.legal_actions() if isinstance(a, A.PlayExtra)]


# --- 9-4-1 / 9-4-3 an EXTRA card goes home ----------------------------------
def test_extra_cookie_bounced_to_hand_goes_to_the_extra_deck(db):
    """9-4-1: a Cookie played from the 【EXTRA】 deck that moves to a private
    zone is placed face-down in the EXTRA deck, not in the hand it can never
    be played from (6-5-2-3)."""
    game = _game(db, ["BS5-062"] * 60)
    me = game.state.players[0]
    extra_id = next(c.id for c in db.cards.values() if c.type is CardType.EXTRA)
    cookie = game._deploy_cookie(me, CardInstance.make(extra_id, 0),
                                 run_on_play=False, from_zone="extra")
    game.return_cookie_to_hand(cookie)
    assert [c.card_id for c in me.extra_deck] == [extra_id]
    assert not any(c.card_id == extra_id for c in me.hand)


def test_refresh_returns_extra_cards_to_the_extra_deck(db):
    """9-4-3: an EXTRA card in the trash at Refresh goes back to the EXTRA
    deck; only the rest is shuffled into the new deck."""
    game = _game(db, ["BS5-062"] * 60)
    me = game.state.players[0]
    extra_id = next(c.id for c in db.cards.values() if c.type is CardType.EXTRA)
    me.deck.clear()
    me.trash.append(CardInstance.make(extra_id, 0))
    me.trash.extend(CardInstance.make("BS5-062", 0) for _ in range(3))
    game._refresh(me)
    assert [c.card_id for c in me.extra_deck] == [extra_id]
    assert not any(db[c.card_id].type is CardType.EXTRA for c in me.deck)


# --- 1-2-1-1-3 the third defeat condition -----------------------------------
def test_refresh_with_no_cookie_in_the_trash_loses_the_game(db):
    """9-2-1-3: "During a player's Refresh procedure, if there are no Cookie
    cards in the trash that can be placed in the break area, that player has
    met the condition for defeat." It used to reshuffle and carry on."""
    game = _game(db, ["BS5-062"] * 60)
    me = game.state.players[0]
    me.deck.clear()
    me.trash[:] = [CardInstance.make(_filler(db), 0) for _ in range(5)]
    assert not game._refresh(me)
    assert game.state.over and game.state.winner == 1


def test_refresh_breaks_a_cookie_and_not_merely_a_levelled_card(db):
    """1-3-9-1 says a LV.1-or-higher *Cookie* card; 3-8-3 says the break area
    generally holds nothing else."""
    game = _game(db, ["BS5-062"] * 60)
    me = game.state.players[0]
    me.deck.clear()
    me.trash[:] = [CardInstance.make("BS5-062", 0) for _ in range(3)]
    assert game._refresh(me)
    assert [c.card_id for c in me.break_area] == ["BS5-062"]


# --- 7-1-1-3 / 7-1-2-2 a Cookie that left the battle area ------------------
def test_an_attacker_that_left_the_battle_area_deals_no_damage(db):
    """7-1-2-2: at the end of the Trap Step, if the attacking card was moved to
    another zone the players proceed straight to the End Battle Step."""
    game = _game(db, ["BS5-062"] * 60)
    state = game.state
    me, them = state.players[0], state.players[1]
    attacker, target = me.battle[0], them.battle[0]
    before = target.remaining_hp

    real = game._response_window

    def bounce(defender, atk, tgt):
        # Stands in for a trap that removes the attacker from the battle area.
        game.return_cookie_to_hand(atk)
        return real(defender, atk, tgt)

    game._response_window = bounce
    state.phase = state.phase
    game.step(A.Attack(attacker.uid, target.uid))
    assert target.remaining_hp == before
