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

    def choose_many(self, state, prompt, options, *, count, optional, up_to=False):
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


def test_an_extra_card_is_typed_by_the_marker_printed_on_it(db):
    """Seven cards print 【EXTRA】 and were typed COOKIE by the scrape.

    No column in the dump is reliable: the two BS11 rows set `isExtra` to 1
    while typing them COOKIE, the five BS9 rows say Cookie in both columns, and
    BS10's rows type them `EXRTA` with no 【EXTRA】 in their text at all. The
    printed marker is the one signal that gets all 17 right, and it is a strict
    superset of the type column, so promoting on it can only add cards.
    """
    from braverse.enums import Marker

    marked = {c.base_id for c in db.cards.values() if Marker.EXTRA in c.markers}
    typed = {c.base_id for c in db.cards.values() if c.type is CardType.EXTRA}
    assert marked == typed, marked ^ typed
    for card_id in ("BS9-010", "BS9-030", "BS9-055", "BS9-088", "BS9-102",
                    "BS11-091", "BS11-116"):
        assert db[card_id].type is CardType.EXTRA, card_id


def test_an_extra_card_is_never_played_from_hand(db):
    """`CardType.EXTRA.is_cookie` is True, so an EXTRA card that reached a hand
    used to be offered as a free Cookie play with its gate skipped — which is
    what BS9-102 was doing while it was typed COOKIE. It belongs to its own
    pile and arrives only through the gate."""
    from braverse.state import CardInstance

    game = _game(db, [])
    player = game.state.players[0]
    del player.battle[1:]
    card = CardInstance.make("BS9-102", 0)
    player.hand.append(card)

    mine = [a for a in game.legal_actions()
            if getattr(a, "card_uid", None) == card.uid]
    assert not any(isinstance(a, A.PlayCookie) for a in mine), mine
    # Still a card in your hand: face down as support is any card's business.
    assert any(isinstance(a, A.PlaceSupport) for a in mine), mine


def test_a_promoted_extra_card_waits_for_its_own_gate(db):
    """BS9-102: "can be played if there are 20 cards or more in each player's
    trash". It was droppable on turn one for nothing."""
    from braverse.state import CardInstance

    game = _game(db, ["BS9-102"])
    me, them = game.state.players
    del me.battle[1:]
    assert not _extras(game), "the gate was open on an empty trash"

    me.trash.extend(CardInstance.make("ST9-001", 0) for _ in range(20))
    them.trash.extend(CardInstance.make("ST8-001", 1) for _ in range(20))
    assert _extras(game), "the gate stayed shut with both trashes at 20"


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


# --- the seven promoted cards' own text --------------------------------------
def _ctx(game, player, **kw):
    from braverse.effects import Ctx
    return Ctx(game=game, state=game.state, db=game.db, me=player,
               opp=game.state.opponent_of(player.index), **kw)


def _deploy(game, player, card_id):
    """Put one of them on the board with a free slot beside it."""
    del player.battle[1:]
    card = CardInstance.make(card_id, player.index)
    player.hand.append(card)
    game._deploy_cookie(player, card, run_on_play=False)
    return player.battle[-1]


def _fire(game, player, card_id, trigger, **kw):
    from braverse.effects import Trigger, get_effect
    fn = get_effect(card_id, Trigger[trigger])
    assert fn is not None, f"{card_id} has no {trigger}"
    fn(_ctx(game, player, trigger=Trigger[trigger].value, **kw))


def test_shadow_milk_red_takes_a_card_out_of_the_opponents_hand_as_hp(db):
    """【On Play】 Add up to 1 card from your opponent's hand face-up to the
    bottom of this Cookie's HP.

    Random, like every other card in the pool that reaches into a hand, and
    face up, because a card taken off someone else is public. The bottom of the
    pile is index 0 — damage pops off the end — so a stolen card is the *last*
    one that pile will turn over.
    """
    game = _game(db, [])
    me, them = game.state.players
    cookie = _deploy(game, me, "BS9-010")
    hand, pile = len(them.hand), len(cookie.hp_cards)

    _fire(game, me, "BS9-010", "ON_PLAY", source_cookie=cookie)

    assert len(them.hand) == hand - 1
    assert len(cookie.hp_cards) == pile + 1
    assert cookie.hp_cards[0].face_up, "a stolen card is public"
    assert cookie.hp_cards[0].owner == them.index, "it keeps its owner"


def test_shadow_milk_red_steals_hp_off_a_cookie_it_swings_at(db):
    game = _game(db, [])
    me, them = game.state.players
    cookie = _deploy(game, me, "BS9-010")
    me.support = [CardInstance.make("ST9-001", 0) for _ in range(3)]
    for card in me.support:
        card.rested = False
    victim = them.battle[0]
    theirs, mine = len(victim.hp_cards), len(cookie.hp_cards)

    _fire(game, me, "BS9-010", "ATTACK", source_cookie=cookie)

    assert len(victim.hp_cards) == theirs - 1
    assert len(cookie.hp_cards) == mine + 1


def test_shadow_milk_yellow_buys_back_room_on_the_break_clock(db):
    """【On Play】 Place up to 1 LV.1 Cookie from your break area into your
    trash. Taking a card out of your own break area lowers the Level banked
    against you, which is the whole point of it."""
    game = _game(db, [])
    me, _ = game.state.players
    cookie = _deploy(game, me, "BS9-030")
    lv1 = next(c for c in db.cards.values()
               if c.is_cookie and (c.level or 0) == 1)
    me.break_area.append(CardInstance.make(lv1.id, 0))
    banked = me.break_level_total(db)

    _fire(game, me, "BS9-030", "ON_PLAY", source_cookie=cookie)

    assert me.break_level_total(db) == banked - 1


def test_shadow_milk_yellow_fires_a_flip_out_of_hand(db):
    """The only card in the pool that runs a FLIP from anywhere but an HP
    pile: discard one and its effect happens from the trash."""
    from braverse.enums import CardType

    game = _game(db, [])
    me, _ = game.state.players
    cookie = _deploy(game, me, "BS9-030")
    flip = next(c for c in db.cards.values()
                if c.type is CardType.FLIP and c.flip_text)
    me.hand.append(CardInstance.make(flip.id, 0))
    trash = len(me.trash)

    _fire(game, me, "BS9-030", "ATTACK", source_cookie=cookie)

    assert len(me.trash) > trash, "the FLIP was discarded"
    assert not any(db[c.card_id].type is CardType.FLIP for c in me.hand)


def test_shadow_milk_green_fills_its_support_only_up_to_five(db):
    game = _game(db, [])
    me, _ = game.state.players
    cookie = _deploy(game, me, "BS9-055")

    me.support = []
    _fire(game, me, "BS9-055", "ACTIVATE", source_cookie=cookie)
    assert len(me.support) == 1
    assert me.support[0].rested, "placed as rested"

    me.support = [CardInstance.make("ST9-001", 0) for _ in range(6)]
    _fire(game, me, "BS9-055", "ACTIVATE", source_cookie=cookie)
    assert len(me.support) == 6, "6 is more than 5, so the condition fails"


def test_shadow_milk_green_pays_a_support_card_for_a_point_of_damage(db):
    game = _game(db, [])
    me, them = game.state.players
    cookie = _deploy(game, me, "BS9-055")
    me.support = [CardInstance.make("ST9-001", 0) for _ in range(3)]
    victim = them.battle[0]
    hp, hand = victim.remaining_hp, len(me.hand)

    _fire(game, me, "BS9-055", "ATTACK", source_cookie=cookie)

    assert len(me.support) == 2 and len(me.hand) == hand + 1, "the cost was paid"
    assert victim.remaining_hp == hp - 1


def test_pure_vanilla_reveals_the_top_card_and_only_pays_out_on_a_match(db):
    """【On Play】 Reveal 1 card from the top of your deck. If that card is a
    {B} LV.2 Cookie, this Cookie gains +2 HP, and draw up to 2 cards.

    A reveal, not a draw: the card stays on top, so the two drawn afterwards
    start with the one everybody just looked at.
    """
    from braverse.enums import Color

    blue2 = next(c for c in db.cards.values()
                 if c.color is Color.BLUE and c.is_cookie and (c.level or 0) == 2)
    red = next(c for c in db.cards.values()
               if c.color is Color.RED and c.is_cookie)

    for defn, hits in ((blue2, True), (red, False)):
        game = _game(db, [])
        me, _ = game.state.players
        cookie = _deploy(game, me, "BS9-088")
        me.deck.insert(0, CardInstance.make(defn.id, 0))
        hp, hand = cookie.remaining_hp, len(me.hand)

        _fire(game, me, "BS9-088", "ON_PLAY", source_cookie=cookie)

        if hits:
            assert cookie.remaining_hp == hp + 2
            assert len(me.hand) == hand + 2
            # In printed order the HP comes first, and in this engine healing
            # is cards off the top of the deck — so the card you just revealed
            # is the first one onto the pile, and the two you draw come from
            # under it. Worth pinning: it is the difference between drawing the
            # {B} LV.2 Cookie you found and burying it.
            assert cookie.hp_cards[-2].card_id == defn.id
            assert all(c.card_id != defn.id for c in me.hand[-2:])
        else:
            assert cookie.remaining_hp == hp and len(me.hand) == hand
            assert me.deck[0].card_id == defn.id, "a reveal puts nothing anywhere"


def test_shadow_milk_purple_trades_a_purple_card_for_one_of_theirs(db):
    from braverse.enums import Color

    game = _game(db, [])
    me, them = game.state.players
    cookie = _deploy(game, me, "BS9-102")
    purple = next(c for c in db.cards.values() if c.color is Color.PURPLE)
    me.hand.append(CardInstance.make(purple.id, 0))
    them.hand.extend(CardInstance.make("ST9-001", 1) for _ in range(4))
    mine, theirs = len(me.hand), len(them.hand)

    _fire(game, me, "BS9-102", "ACTIVATE", source_cookie=cookie)

    assert len(me.hand) == mine - 1, "the {P} card was discarded"
    assert len(them.hand) == theirs - 1


def test_shadow_milk_purple_only_chips_a_well_stocked_trash(db):
    game = _game(db, [])
    me, them = game.state.players
    cookie = _deploy(game, me, "BS9-102")
    victim = them.battle[0]

    them.trash = []
    hp = victim.remaining_hp
    _fire(game, me, "BS9-102", "ATTACK", source_cookie=cookie, attack_target=victim)
    assert victim.remaining_hp == hp, "under 20 cards, nothing happens"

    them.trash = [CardInstance.make("ST9-001", 1) for _ in range(20)]
    _fire(game, me, "BS9-102", "ATTACK", source_cookie=cookie, attack_target=victim)
    assert victim.remaining_hp == hp - 1


def test_dark_enchantress_cannot_be_moved_by_the_opponent(db):
    """The whole of the card's text below its 【EXTRA】 gate."""
    from braverse.effects import is_move_protected

    game = _game(db, [])
    me, _ = game.state.players
    warded = _deploy(game, me, "BS11-116")
    assert is_move_protected(db, me, warded)
    assert not is_move_protected(db, me, me.battle[0]), "only itself"
