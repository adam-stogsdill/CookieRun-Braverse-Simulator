"""The 【EXTRA】 deck.

A second pile, never shuffled and never drawn, holding cards that enter play
through the gate printed on them. Two shapes share the mechanism: a standalone
EXTRA Cookie takes a free battle slot, and an 【Awaken】 card stacks on top of a
Cookie already in the battle area.
"""

import pytest

from braverse import (STARTER_DECKS, Game, HeuristicAgent, SeatedAgent,
                      default_db, validate)
from braverse import actions as A
from braverse import config as cfg
from braverse.effects import EXTRA_PLAYS
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

    def choose_many(self, state, prompt, options, *, count, optional):
        return options[:count]

    def confirm(self, state, prompt):
        return True


def _game(db, extra, seed=4):
    deck = ["BS5-062"] * 60
    game = Game([deck, deck], [_Auto(), _Auto()],
                extra_decks=[extra, []], db=db, seed=seed)
    game.setup()
    return game


def _extras(game):
    return [a for a in game.legal_actions() if isinstance(a, A.PlayExtra)]


# --- the pile ---------------------------------------------------------------
def test_every_extra_card_in_the_pool_knows_how_it_is_played(db):
    """An EXTRA card with no registered gate can never enter the game at all,
    so an unimplemented one is invisible rather than merely weak."""
    printed = {c.base_id for c in db.cards.values() if c.type is CardType.EXTRA}
    assert printed == set(EXTRA_PLAYS), printed ^ set(EXTRA_PLAYS)


def test_the_extra_deck_is_not_shuffled_into_the_deck(db):
    game = _game(db, ["BS8-005", "BS8-090"])
    player = game.state.players[0]
    assert [c.card_id for c in player.extra_deck] == ["BS8-005", "BS8-090"]
    # It is a pile beside the deck, not part of it: nothing can draw into it.
    for zone in (player.deck, player.hand, player.trash, player.break_area):
        assert not any(db[c.card_id].type is CardType.EXTRA for c in zone)


def test_a_game_built_without_an_extra_deck_still_works(db):
    """Every caller that predates the EXTRA deck passes nothing, and a deck
    with no EXTRA cards is a legal deck."""
    game = _game(db, [])
    assert game.state.players[0].extra_deck == []
    assert not _extras(game)


# --- gates ------------------------------------------------------------------
def test_a_closed_gate_is_not_a_move(db):
    """Avatar of Ruin: "Can be played if 2 or more of your Cookies fainted this
    turn." A card whose condition is false is not offered at all — the gate is
    a condition, not a cost that can fizzle."""
    game = _game(db, ["BS8-005"])
    assert not _extras(game)
    game.state.players[0].cookies_fainted_this_turn = 2
    assert len(_extras(game)) == 1


def test_an_extra_cookie_arrives_with_its_own_hp_and_fires_on_play(db):
    game = _game(db, ["BS8-005"])
    me, opp = game.state.players
    me.cookies_fainted_this_turn = 2
    before = [c.remaining_hp for c in opp.battle]
    game.step(_extras(game)[0])

    played = me.battle[-1]
    assert played.name(db) == "Avatar of Ruin"
    assert played.remaining_hp == db["BS8-005"].hp == 5
    assert me.extra_deck == []          # it left the pile
    # 【On Play】 All of your opponent's Cookies receive 1 damage.
    assert [c.remaining_hp for c in opp.battle] == [n - 1 for n in before]


def test_an_extra_cookie_needs_a_free_battle_slot(db):
    game = _game(db, ["BS8-005"])
    me = game.state.players[0]
    me.cookies_fainted_this_turn = 2
    while len(me.battle) < cfg.DEFAULT.max_battle_cookies:
        me.battle.append(game._deploy_cookie(me, CardInstance.make("BS5-062", 0)))
    assert len(me.battle) >= cfg.DEFAULT.max_battle_cookies
    assert not _extras(game)


def test_a_cost_that_cannot_be_paid_closes_the_gate(db):
    """Jagae Cookie: "If there are 7 cards or more in your hand, <discard 2 {B}
    cards.>" A hand that cannot cover the discard cannot make the move."""
    game = _game(db, ["BS10-098"])
    me = game.state.players[0]
    me.hand = [CardInstance.make("BS5-062", 0) for _ in range(8)]   # {G}, not {B}
    assert not _extras(game)
    me.hand[:2] = [CardInstance.make("ST9-006", 0) for _ in range(2)]   # {B}
    assert len(_extras(game)) == 1
    game.step(_extras(game)[0])
    assert me.battle[-1].name(db) == "Jagae Cookie"
    assert len(me.hand) == 6            # the two {B} cards were discarded


# --- 【Awaken】 --------------------------------------------------------------
def _awaken_setup(db, extra_id, host_id, seed=4):
    game = _game(db, [extra_id], seed=seed)
    me = game.state.players[0]
    host = game._deploy_cookie(me, CardInstance.make(host_id, 0))
    return game, me, host


def test_awaken_stacks_on_the_host_and_keeps_its_remaining_hp(db):
    """Hollyberry Cookie 【EXTRA】 prints HP as `+1`, not a total: the Cookie it
    lands on keeps the HP it has left and gains one more. That is the whole
    point of the card — it is worth most on a Cookie already chipped down."""
    game, me, host = _awaken_setup(db, "BS10-024", "BS9-017")
    while host.remaining_hp > 3:
        me.trash.append(host.hp_cards.pop())
    me.hand.append(CardInstance.make("BS5-062", 0))     # for <Discard 1 card.>
    original = host.card

    moves = _extras(game)
    assert [m.onto for m in moves] == [host.uid]
    slots = len(me.battle)
    game.step(moves[0])

    assert host in me.battle and len(me.battle) == slots   # no new slot taken
    assert host.card.card_id == "BS10-024"
    assert host.remaining_hp == 4                       # 3 kept + 1 printed
    assert host.level(db) == 3                          # reads off the new card
    assert host.under == [original]
    # 【On Play】 Until the end of your opponent's next turn, -1 from all damage.
    assert host.all_damage_reduction == 1


def test_awaken_is_only_offered_on_a_host_the_card_names(db):
    """"You can 【Awaken】 your [Hollyberry Cookie] with 3 or less HP remaining."
    A different Cookie, or one still above 3 HP, is not a legal host."""
    game, me, host = _awaken_setup(db, "BS10-024", "BS5-062")   # Pumpkin Cookie
    me.hand.append(CardInstance.make("BS5-062", 0))
    assert not _extras(game)

    game, me, host = _awaken_setup(db, "BS10-024", "BS9-017")
    me.hand.append(CardInstance.make("BS5-062", 0))
    assert host.remaining_hp > 3
    assert not _extras(game)


def test_an_awakened_cookie_faints_as_one_card_not_two(db):
    """Only the card on top reaches the break area. Banking both would count
    two Levels toward the opponent's win for one Cookie."""
    game, me, host = _awaken_setup(db, "BS10-024", "BS9-017")
    while host.remaining_hp > 3:
        me.trash.append(host.hp_cards.pop())
    me.hand.append(CardInstance.make("BS5-062", 0))
    under = host.card
    game.step(_extras(game)[0])

    game.faint(host)
    assert [c.card_id for c in me.break_area] == ["BS10-024"]
    assert under in me.trash
    assert me.break_level_total(db) == db["BS10-024"].level


def test_awaken_resets_the_once_per_turn_of_the_card_it_replaces(db):
    """The skill on the new card has not been used this turn — it was not on
    the table until now."""
    game, me, host = _awaken_setup(db, "BS10-073", "BS10-069")   # White Lily
    me.support = [CardInstance.make("BS5-062", 0) for _ in range(8)]
    host.used_markers.add("activate")
    me.activated_this_turn.add(host.uid)
    game.step(_extras(game)[0])
    assert not host.used_markers
    assert host.uid not in me.activated_this_turn


# --- deck construction ------------------------------------------------------
def test_deck_validation_covers_both_piles(db):
    deck = list(STARTER_DECKS["st9_sea_fairy"])
    assert validate(deck, db, extra=["BS8-005", "BS8-090"]).ok

    # Four copies of one number is legal; a seventh card of any kind is not.
    over = validate(deck, db, extra=["BS8-005"] * 4 + ["BS8-069"] * 3)
    assert not over.ok and any("EXTRA deck has 7" in p for p in over.problems)

    wrong = validate(deck, db, extra=["ST9-006"])
    assert not wrong.ok and any("non-EXTRA" in p for p in wrong.problems)

    misfiled = validate(deck[:59] + ["BS8-005"], db)
    assert not misfiled.ok
    assert any("belong in the EXTRA deck" in p for p in misfiled.problems)


def test_the_copy_limit_spans_both_piles(db):
    """It counts card numbers you own, not cards in one zone."""
    deck = list(STARTER_DECKS["st9_sea_fairy"])
    report = validate(deck, db, extra=["BS8-005"] * 5)
    assert not report.ok and any("more than 4 copies" in p for p in report.problems)


def test_a_deck_with_an_extra_deck_plays_a_full_game(db):
    """The whole thing has to survive a real game, not just a set-up board."""
    deck = list(STARTER_DECKS["st8_wind_archer"])
    game = Game([deck, deck],
                [SeatedAgent(HeuristicAgent(db=db, seed=1), 0),
                 SeatedAgent(HeuristicAgent(db=db, seed=2), 1)],
                extra_decks=[["BS8-005", "BS8-069", "BS8-090"], []],
                db=db, seed=5)
    game.setup()
    assert game.play_out().winner is not None
