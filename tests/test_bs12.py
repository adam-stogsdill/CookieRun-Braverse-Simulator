"""BS12 — BOOSTER PACK [Festival Arena].

The set's two entrances (six 【EXTRA】 gates, five 【Special Play】 bodies) and
its two continuous abilities. The gates are conditions rather than costs, so
what each test asserts is that the move is *not offered* until the board says
it may be, and that the printed cost is actually charged on the way in.
"""

import pytest

from braverse import Game, default_db
from braverse import actions as A
from braverse import config as cfg
from braverse.cards import blocker_price
from braverse.cost import Cost
from braverse.effects import Trigger
from braverse.enums import Color, Keyword, Marker
from braverse.state import CardInstance


@pytest.fixture(scope="module")
def db():
    return default_db()


class _Auto:
    def choose_action(self, state, options):
        return options[0]

    def choose(self, state, prompt, options, *, optional=False):
        return options[0] if options else None

    def choose_many(self, state, prompt, options, *, count, optional, up_to=False):
        return options[:count]

    def confirm(self, state, prompt):
        return True


def _game(db, extra=(), seed=4):
    deck = ["BS5-062"] * 60
    game = Game([deck, deck], [_Auto(), _Auto()],
                extra_decks=[list(extra), []], db=db, seed=seed)
    game.setup()
    return game


def _extras(game):
    return [a for a in game.legal_actions() if isinstance(a, A.PlayExtra)]


def _put(game, player, card_id, zone):
    card = CardInstance.make(card_id, player.index)
    getattr(player, zone).append(card)
    return card


# --- 【EXTRA】 gates ---------------------------------------------------------
def test_shining_glitter_needs_the_break_clock_and_an_arena_card_to_pitch(db):
    """"If your break area is LV.4 or higher, <discard 1 【Arena】 card from your
    hand.> Play this Cookie." Both halves are the gate: a hand with nothing to
    pitch cannot make the move, because the discard is part of it."""
    game = _game(db, ["BS12-018"])
    me = game.state.players[0]
    me.hand = [CardInstance.make("BS5-062", 0)]          # {G}, not 【Arena】
    assert not _extras(game)

    for _ in range(2):                                    # two LV.2s -> LV.4
        _put(game, me, "BS7-023", "break_area")
    assert me.break_level_total(db) >= 4
    assert not _extras(game)                              # still nothing to pitch

    _put(game, me, "BS7-001", "hand")                     # an 【Arena】 card
    assert len(_extras(game)) == 1
    game.step(_extras(game)[0])
    assert me.battle[-1].name(db) == "Shining Glitter Cookie"
    assert not any(Keyword.ARENA in db[c.card_id].keywords for c in me.hand)


def test_clotted_cream_counts_only_yellow_arena_cookies_in_the_break_area(db):
    """"Can be played if there are 4 {Y} 【Arena】 Cookies or more in your break
    area." Both adjectives count."""
    game = _game(db, ["BS12-036"])
    me = game.state.players[0]
    for _ in range(4):
        _put(game, me, "BS5-062", "break_area")           # green, no 【Arena】
    assert not _extras(game)

    me.break_area.clear()
    for _ in range(3):
        _put(game, me, "BS7-023", "break_area")
    assert not _extras(game)                              # three is not four
    _put(game, me, "BS7-024", "break_area")
    assert len(_extras(game)) == 1


def test_apple_faerie_opens_on_either_half_of_its_or(db):
    """"if there is a [Candy Apple Cookie] that has 【Arena】 in your battle
    area, **or** if there are 7 {G} cards or more in your support area"."""
    game = _game(db, ["BS12-056"])
    me = game.state.players[0]
    assert not _extras(game)

    # The named half, and the qualifier on it: BS9-014 is also a Candy Apple
    # Cookie and does not have 【Arena】, so it must not open the gate.
    plain = game._deploy_cookie(me, CardInstance.make("BS9-014", 0))
    assert plain.name(db) == "Candy Apple Cookie"
    assert not _extras(game)
    me.battle.remove(plain)

    game._deploy_cookie(me, CardInstance.make("BS12-017", 0))
    assert len(_extras(game)) == 1


def test_apple_faerie_also_opens_on_seven_green_support_cards(db):
    game = _game(db, ["BS12-056"])
    me = game.state.players[0]
    me.support = [CardInstance.make("BS1-007", 0) for _ in range(6)]
    assert not _extras(game)
    _put(game, me, "BS1-007", "support")
    assert len(_extras(game)) == 1


def test_popping_candy_asks_for_an_arena_cookie_specifically(db):
    """"if, during this turn, an 【Arena】 Cookie was placed from your battle
    area on the bottom of your deck". The keyword is the whole question, so the
    plain counter the engine already kept is not the one to read."""
    game = _game(db, ["BS12-074"])
    me = game.state.players[0]
    assert not _extras(game)

    plain = game._deploy_cookie(me, CardInstance.make("BS5-062", 0))
    game.cookie_to_deck(plain, bottom=True)
    assert me.cookies_to_deck_bottom_this_turn == 1
    assert not _extras(game)                              # not an 【Arena】 one

    arena = game._deploy_cookie(me, CardInstance.make("BS7-001", 0))
    game.cookie_to_deck(arena, bottom=True)
    assert len(_extras(game)) == 1


def test_popping_candy_does_not_count_a_cookie_sent_to_the_top(db):
    """The card says the bottom, and the engine counts the two ends apart."""
    game = _game(db, ["BS12-074"])
    me = game.state.players[0]
    arena = game._deploy_cookie(me, CardInstance.make("BS7-001", 0))
    game.cookie_to_deck(arena, bottom=False)
    assert me.cookies_to_deck_this_turn == 1
    assert not _extras(game)


def test_black_lemonade_pays_a_cookie_and_may_do_it_from_a_full_board(db):
    """"If there are 3 【Arena】 Cookies that have 【Blocker】 or more in your
    break area, <place 1 {P} LV.2 or lower Cookie from your battle area into
    your trash.> Play this Cookie."

    The cost empties a battle slot, so 3-5-6-1-1 lets the card be played out of
    a full battle area — the Cookie it trashes is the room it arrives in."""
    game = _game(db, ["BS12-092"])
    me = game.state.players[0]
    for card_id in ("BS12-081", "BS12-088", "BS12-089"):
        _put(game, me, card_id, "break_area")

    me.battle.clear()
    while len(me.battle) < cfg.DEFAULT.max_battle_cookies:
        game._deploy_cookie(me, CardInstance.make("BS2-056", 0))   # {P} LV.1
    assert len(me.battle) == cfg.DEFAULT.max_battle_cookies

    assert len(_extras(game)) == 1                        # a full board is no bar
    trash_before = len(me.trash)
    game.step(_extras(game)[0])
    assert me.battle[-1].name(db) == "Black Lemonade Cookie"
    assert len(me.battle) == cfg.DEFAULT.max_battle_cookies
    # Trashing is not fainting: the break area does not grow.
    assert len(me.trash) > trash_before
    assert len(me.break_area) == 3


def test_black_lemonade_needs_a_cookie_its_cost_can_actually_name(db):
    game = _game(db, ["BS12-092"])
    me = game.state.players[0]
    for card_id in ("BS12-081", "BS12-088", "BS12-089"):
        _put(game, me, card_id, "break_area")
    me.battle.clear()
    game._deploy_cookie(me, CardInstance.make("BS5-062", 0))       # {G}, not {P}
    assert not _extras(game)


def test_poison_mushroom_needs_both_halves_of_its_gate(db):
    """"if there are 4 cards or more in your opponent's support area **and**
    there is a Cookie that has Special Play in your battle area"."""
    game = _game(db, ["BS12-111"])
    me, opp = game.state.players
    opp.support = [CardInstance.make("BS5-062", 1) for _ in range(4)]
    assert not _extras(game)                              # no Special Play body

    me.battle.clear()
    game._deploy_cookie(me, CardInstance.make("BS12-095", 0))
    assert db["BS12-095"].has(Marker.SPECIAL_PLAY)
    assert len(_extras(game)) == 1

    opp.support.pop()
    assert not _extras(game)


# --- 【Special Play】 --------------------------------------------------------
@pytest.mark.parametrize("card_id", ["BS12-095", "BS12-096", "BS12-098",
                                     "BS12-100", "BS12-112"])
def test_a_special_play_body_is_not_free(db, card_id):
    """4-10-1-1: the printed line is the card's only door. With nothing to
    trash it is not a legal move — not a free body."""
    game = _game(db)
    me = game.state.players[0]
    me.battle.clear()
    card = _put(game, me, card_id, "hand")
    plays = [a for a in game.legal_actions()
             if isinstance(a, A.PlayCookie) and a.card_uid == card.uid]
    assert not plays

    game._deploy_cookie(me, CardInstance.make("BS12-095", 0))   # {K} LV.1
    plays = [a for a in game.legal_actions()
             if isinstance(a, A.PlayCookie) and a.card_uid == card.uid]
    assert len(plays) == 1
    game.step(plays[0])
    assert me.battle[-1].card.card_id == card_id
    # Trashed, not fainted: the Cookie itself lands in the trash (with the HP
    # pile it sheds on the way) and the break area stays empty, so the
    # opponent banks no Level for it.
    assert any(c.card_id == "BS12-095" for c in me.trash)
    assert not me.break_area


def test_red_velvet_asks_for_a_special_play_cookie_specifically(db):
    """Red Velvet's line adds "that has Special Play" to the sentence the four
    Cake Hounds print, so an ordinary {K} LV.1 Cookie is not enough."""
    game = _game(db)
    me = game.state.players[0]
    me.battle.clear()
    card = _put(game, me, "BS12-112", "hand")
    plain = game._deploy_cookie(me, CardInstance.make("BS11-093", 0))
    assert plain.defn(db).color is Color.BLACK
    assert not plain.defn(db).has(Marker.SPECIAL_PLAY)
    assert not [a for a in game.legal_actions()
                if isinstance(a, A.PlayCookie) and a.card_uid == card.uid]

    game._deploy_cookie(me, CardInstance.make("BS12-095", 0))
    assert [a for a in game.legal_actions()
            if isinstance(a, A.PlayCookie) and a.card_uid == card.uid]


# --- continuous abilities ----------------------------------------------------
def test_poison_mushroom_buffs_the_others_and_not_itself(db):
    """"Other {K} 【Arena】 Cookies in your battle area gain +1 attack damage."

    An aura over the other Cookies, which is why it cannot be an ATTACK_START
    trigger — that fires for the attacker alone."""
    from braverse.impl.bs12 import _poison_mushroom_aura

    game = _game(db)
    me = game.state.players[0]
    me.battle.clear()
    ally = game._deploy_cookie(me, CardInstance.make("BS12-094", 0))
    assert _poison_mushroom_aura(db, me, ally) == 0        # no Mushroom yet

    mushroom = game._deploy_cookie(me, CardInstance.make("BS12-111", 0))
    assert _poison_mushroom_aura(db, me, ally) == 1
    assert _poison_mushroom_aura(db, me, mushroom) == 0    # "Other"

    green = game._deploy_cookie(me, CardInstance.make("BS5-062", 0))
    assert _poison_mushroom_aura(db, me, green) == 0       # not {K} 【Arena】


def test_the_attack_aura_does_not_accumulate_across_two_swings(db):
    """The aura is a property of the board, not a buff the attacker banks: it
    is taken off again when the battle ends, or a Cookie that swings twice in a
    turn hits for one more the second time."""
    game = _game(db)
    me, opp = game.state.players
    me.battle.clear()
    game._deploy_cookie(me, CardInstance.make("BS12-111", 0))
    ally = game._deploy_cookie(me, CardInstance.make("BS12-094", 0))
    printed = ally.attack_damage(db)
    game.state.turn_number = 3          # 5-2-1: nobody attacks on turn one
    # Enough energy for two swings: the attack rests three support cards each
    # time, and the point of the test is the second one.
    me.support = [CardInstance.make("BS12-094", 0) for _ in range(8)]

    for _ in range(2):
        ally.rested = False
        attacks = [a for a in game.legal_actions()
                   if isinstance(a, A.Attack) and a.attacker_uid == ally.uid]
        assert attacks
        game.step(attacks[0])
        # The swing lands at printed + 1 ...
        assert any(f"for {printed + 1}" in line for line in game.state.log[-6:])
        # ... and the Cookie is back to its printed number afterwards.
        assert ally.attack_damage(db) == printed


def test_black_lemonade_watches_your_other_cookies_faint(db):
    """"When one of your Cookies faints, if there are 3 cards or more in your
    opponent's hand, your opponent places 1 card from their hand into their
    trash." A watcher on the neighbours, not on itself."""
    game = _game(db)
    me, opp = game.state.players
    me.battle.clear()
    game._deploy_cookie(me, CardInstance.make("BS12-092", 0))
    victim = game._deploy_cookie(me, CardInstance.make("BS5-062", 0))
    opp.hand = [CardInstance.make("BS5-062", 1) for _ in range(3)]

    game.faint(victim)
    assert len(opp.hand) == 2
    assert len(opp.trash) >= 1


def test_black_lemonade_holds_off_while_the_hand_is_small(db):
    game = _game(db)
    me, opp = game.state.players
    me.battle.clear()
    game._deploy_cookie(me, CardInstance.make("BS12-092", 0))
    victim = game._deploy_cookie(me, CardInstance.make("BS5-062", 0))
    opp.hand = [CardInstance.make("BS5-062", 1) for _ in range(2)]

    game.faint(victim)
    assert len(opp.hand) == 2


# --- the 【Blocker】 misparse -------------------------------------------------
@pytest.mark.parametrize("card_id,why", [
    ("BS12-077", "cannot activate 【Blocker】"),
    ("BS12-092", "Cookies that have 【Blocker】 in your break area"),
    ("BS2-028", "cannot activate 【Blocker】"),
    ("BS7-003", "cannot activate 【Blocker】"),
])
def test_a_card_that_only_mentions_blocker_does_not_have_one(db, card_id, why):
    """An unpriced 【Blocker】 is a *free* one, so a Cookie that merely names the
    keyword was being handed the best redirect in the game — and the cards that
    name it mostly say nobody may block."""
    assert "【Blocker】" in db[card_id].description, why
    assert not db[card_id].has(Marker.BLOCKER)
    assert blocker_price(db[card_id]) is None


def test_a_printed_blocker_still_blocks(db):
    """The rule is "opens the line", so the real ones are untouched."""
    kiwi = db["ST8-011"]
    assert kiwi.has(Marker.BLOCKER)
    assert blocker_price(kiwi) is not None


# --- BS12-077 Spotlight Fan --------------------------------------------------
def _fan_board(db):
    game = _game(db)
    me = game.state.players[0]
    me.battle.clear()
    rockstar = game._deploy_cookie(me, CardInstance.make("BS12-093", 0))
    fan = game._deploy_cookie(me, CardInstance.make("BS12-077", 0))
    me.support = [CardInstance.make("BS2-056", 0) for _ in range(8)]   # {P}
    return game, me, rockstar, fan


def test_spotlight_fan_climbs_onto_rockstar_and_leaves_the_battle_area(db):
    """The set's one 【Equip】, and the only one worn by a Cookie rather than an
    ITEM: the move takes it off the field and leaves it riding on its host."""
    game, me, rockstar, fan = _fan_board(db)
    assert rockstar.name(db) == "Rockstar Cookie"

    activates = [a for a in game.legal_actions()
                 if isinstance(a, A.ActivateSkill) and a.source_uid == fan.uid]
    assert len(activates) == 1
    game.step(activates[0])

    assert fan not in me.battle
    assert [c.card_id for c in rockstar.equipment] == ["BS12-077"]
    assert me.battle == [rockstar]


def test_spotlight_fan_is_not_offered_without_a_rockstar_to_ride(db):
    game = _game(db)
    me = game.state.players[0]
    me.battle.clear()
    fan = game._deploy_cookie(me, CardInstance.make("BS12-077", 0))
    me.support = [CardInstance.make("BS2-056", 0) for _ in range(8)]
    assert not [a for a in game.legal_actions()
                if isinstance(a, A.ActivateSkill) and a.source_uid == fan.uid]


class _Recorder(_Auto):
    """A defender that writes down every response window it is shown."""

    def __init__(self):
        self.seen = []

    def choose_action(self, state, options):
        self.seen.append(list(options))
        return next((o for o in options if isinstance(o, A.Pass)), options[0])

    def blocks(self):
        return [o for batch in self.seen for o in batch if isinstance(o, A.Block)]


def _swing_into_a_blocker(db, equip):
    game, me, rockstar, fan = _fan_board(db)
    if equip:
        game.step([a for a in game.legal_actions()
                   if isinstance(a, A.ActivateSkill) and a.source_uid == fan.uid][0])
    opp = game.state.players[1]
    opp.battle.clear()
    target = game._deploy_cookie(opp, CardInstance.make("BS5-062", 1))
    game._deploy_cookie(opp, CardInstance.make("ST8-011", 1))   # 【Blocker】 <{G}>
    opp.support = [CardInstance.make("BS1-007", 1) for _ in range(6)]
    game.state.turn_number = 3
    me.support = [CardInstance.make("BS2-056", 0) for _ in range(8)]
    rockstar.rested = False

    recorder = _Recorder()
    game._controllers[1] = recorder
    attacks = [a for a in game.legal_actions()
               if isinstance(a, A.Attack) and a.attacker_uid == rockstar.uid
               and a.target_uid == target.uid]
    assert attacks
    game.step(attacks[0])
    return game, recorder


def test_the_fan_actually_shuts_the_blocker_out_of_the_window(db):
    """The point of the card. Kiwi Cookie can redirect the swing normally, and
    cannot while Rockstar is wearing the fan."""
    _, without = _swing_into_a_blocker(db, equip=False)
    assert len(without.blocks()) == 1

    _, with_fan = _swing_into_a_blocker(db, equip=True)
    assert not with_fan.blocks()


def test_the_fan_shuts_off_blockers_for_the_battle_and_not_the_turn(db):
    """"When that Cookie attacks, during this battle, your opponent cannot
    activate 【Blocker】." A turn can hold several battles, and the fan only
    covers the swing it was there for."""
    game, me, rockstar, fan = _fan_board(db)
    opp = game.state.players[1]
    game.step([a for a in game.legal_actions()
               if isinstance(a, A.ActivateSkill) and a.source_uid == fan.uid][0])

    game.state.turn_number = 3
    me.support = [CardInstance.make("BS2-056", 0) for _ in range(8)]
    rockstar.rested = False
    attacks = [a for a in game.legal_actions()
               if isinstance(a, A.Attack) and a.attacker_uid == rockstar.uid]
    assert attacks
    game.step(attacks[0])
    # The flag was raised for the battle and put back down with it.
    assert not opp.blockers_disabled


def test_stripping_the_fan_takes_the_rider_with_it(db):
    """The rider belongs to the attachment, not to the host — that is the whole
    mechanic. A Rockstar with no fan on it disables nothing."""
    from braverse.effects import Trigger, get_effect

    game, me, rockstar, fan = _fan_board(db)
    game.step([a for a in game.legal_actions()
               if isinstance(a, A.ActivateSkill) and a.source_uid == fan.uid][0])
    assert get_effect("BS12-077", Trigger.ATTACK_START) is not None

    rockstar.equipment.clear()
    game.state.turn_number = 3
    me.support = [CardInstance.make("BS2-056", 0) for _ in range(8)]
    rockstar.rested = False
    attacks = [a for a in game.legal_actions()
               if isinstance(a, A.Attack) and a.attacker_uid == rockstar.uid]
    assert attacks
    game.step(attacks[0])
    assert not game.state.players[1].blockers_disabled


# --- the discard-priced 【Blocker】 -------------------------------------------
_DISCARD_BLOCKERS = ["BS12-081", "BS12-088", "BS12-089", "BS12-090",
                     "BS12-091", "BS12-093"]


@pytest.mark.parametrize("card_id", _DISCARD_BLOCKERS)
def test_the_purple_blockers_price_reads(db, card_id):
    """BS12 prices its whole purple 【Blocker】 line in *discards*. A reader that
    knew only energy and rest left all six unable to block — and three of them
    were in the deck builder marked as fully played while they did it, which is
    the failure that does not announce itself."""
    from braverse.cards import BlockerDiscard

    price = blocker_price(db[card_id])
    assert price is not None
    assert price.discard == BlockerDiscard(1, Color.PURPLE, Keyword.ARENA)
    assert price.energy == Cost() and not price.rests


class _Blocks(_Auto):
    """A defender that blocks whenever it is offered the chance."""

    def __init__(self):
        self.seen = []

    def choose_action(self, state, options):
        self.seen.append(list(options))
        blocks = [o for o in options if isinstance(o, A.Block)]
        return blocks[0] if blocks else options[0]

    def blocks(self):
        return [o for b in self.seen for o in b if isinstance(o, A.Block)]


def _swing_at(db, defender_hand):
    game = _game(db)
    me, opp = game.state.players
    me.battle.clear()
    opp.battle.clear()
    attacker = game._deploy_cookie(me, CardInstance.make("BS12-094", 0))
    target = game._deploy_cookie(opp, CardInstance.make("BS5-062", 1))
    blocker = game._deploy_cookie(opp, CardInstance.make("BS12-081", 1))
    opp.hand = [CardInstance.make(i, 1) for i in defender_hand]
    game.state.turn_number = 3
    me.support = [CardInstance.make("BS12-094", 0) for _ in range(6)]

    recorder = _Blocks()
    game._controllers[1] = recorder
    attacks = [a for a in game.legal_actions()
               if isinstance(a, A.Attack) and a.attacker_uid == attacker.uid
               and a.target_uid == target.uid]
    assert attacks
    game.step(attacks[0])
    return game, opp, blocker, recorder


def test_a_discard_priced_blocker_blocks_and_pays(db):
    _, opp, blocker, recorder = _swing_at(db, ["BS12-081"])   # {P} 【Arena】
    assert len(recorder.blocks()) == 1
    assert not opp.hand                       # the card was discarded
    assert any(c.card_id == "BS12-081" for c in opp.trash)
    assert not blocker.rested                 # the price is not resting


def test_a_hand_that_cannot_pay_is_not_offered_the_block(db):
    """An unread price is not a free one, and a payable price the hand cannot
    cover is not one either."""
    _, opp, _, recorder = _swing_at(db, ["BS5-062"])          # {G}, no 【Arena】
    assert not recorder.blocks()
    assert len(opp.hand) == 1

    _, opp, _, recorder = _swing_at(db, [])                   # empty hand
    assert not recorder.blocks()


def test_the_colour_and_the_keyword_both_narrow_the_discard(db):
    """"Discard 1 {P} 【Arena】 card" — a purple card with no 【Arena】 does not
    pay it, and neither does an 【Arena】 card of the wrong colour."""
    purple_plain = next(c.id for c in db.cards.values()
                        if c.color is Color.PURPLE
                        and Keyword.ARENA not in c.keywords)
    green_arena = next(c.id for c in db.cards.values()
                       if c.color is Color.GREEN and Keyword.ARENA in c.keywords)
    for wrong in (purple_plain, green_arena):
        _, _, _, recorder = _swing_at(db, [wrong])
        assert not recorder.blocks(), wrong


# --- "if you started the game going second" ---------------------------------
def test_the_going_second_rider_reads_the_seat_that_opened(db):
    """The set's compensation mechanic, printed on most of its Cookies. It is a
    fact about the game rather than about the turn, so it is read off
    `first_player` and not off whose turn it is."""
    from braverse.effect_ir import Condition

    game = _game(db)
    went_second = Condition("went_second")
    me, opp = game.state.players

    game.first_player = 0
    assert not went_second.holds(game._ctx(me))
    assert went_second.holds(game._ctx(opp))

    game.first_player = 1
    assert went_second.holds(game._ctx(me))
    assert not went_second.holds(game._ctx(opp))


# --- "<Reveal 1 card from the bottom of your deck.>" -------------------------
def _stardust(db, bottom_id):
    """BS12-070: "Then, <reveal 1 card from the bottom of your deck.> If that
    card is a LV.2 【Arena】 Cookie, add it to your hand, and all of your
    opponent's Cookies receive 1 damage.\""""
    game = _game(db)
    me, opp = game.state.players
    me.battle.clear()
    opp.battle.clear()
    star = game._deploy_cookie(me, CardInstance.make("BS12-070", 0))
    for _ in range(2):
        game._deploy_cookie(opp, CardInstance.make("BS12-093", 1))
    me.deck = [CardInstance.make("BS5-062", 0) for _ in range(5)]
    me.deck.append(CardInstance.make(bottom_id, 0))      # the bottom card
    before = len(me.hand)
    game.state.turn_number = 3
    me.support = [CardInstance.make("BS12-070", 0) for _ in range(6)]
    attacks = [a for a in game.legal_actions()
               if isinstance(a, A.Attack) and a.attacker_uid == star.uid]
    assert attacks
    game.step(attacks[0])
    return me, opp, len(me.hand) - before


def test_a_reveal_that_hits_takes_the_card_and_pays_out(db):
    me, opp, gained = _stardust(db, "BS7-002")      # a LV.2 【Arena】 Cookie
    assert gained == 1
    assert me.hand[-1].card_id == "BS7-002"
    assert len(me.deck) == 5                        # it left the deck
    # 2 from the swing on the target, 1 from the rider on both.
    assert sorted(c.remaining_hp for c in opp.battle) == [1, 3]


def test_a_reveal_that_misses_takes_nothing_and_pays_nothing(db):
    """The guard has to be asked *after* the reveal. Asked before it — which is
    where a guard normally goes, in front of the cost — it reads an empty
    reveal, and all seven of these cards silently never fire."""
    me, opp, gained = _stardust(db, "BS5-062")      # not a LV.2 【Arena】 Cookie
    assert gained == 0
    assert len(me.deck) == 6                        # nothing left the deck
    assert sorted(c.remaining_hp for c in opp.battle) == [2, 4]


# --- the second wave of compiler support ------------------------------------
def test_a_cookie_set_active_by_an_effect_remembers_it(db):
    """"if this Cookie has been set as active by an effect" — and the Active
    Phase is not an effect. If the ordinary untap set the flag, the two cards
    that pay off on it would pay off every single turn."""
    game = _game(db)
    me = game.state.players[0]
    me.battle.clear()
    cookie = game._deploy_cookie(me, CardInstance.make("BS12-005", 0))
    assert not cookie.set_active_by_effect_this_turn

    cookie.rested = True
    cookie.set_active_by_effect()
    assert not cookie.rested and cookie.set_active_by_effect_this_turn

    # The Active Phase clears the flag rather than setting it.
    cookie.rested = True
    game.end_turn()
    game.end_turn()
    assert not cookie.rested
    assert not cookie.set_active_by_effect_this_turn


def test_paying_a_cost_by_resting_your_own_arena_cookies(db):
    """BS12 pays for several effects by tapping its own board rather than its
    support area — "<Set 2 【Arena】 Cookies in your battle area as rested.>"."""
    from braverse.compiler import parse_cost

    ops = parse_cost("Set 2 【Arena】 Cookies in your battle area as rested.")
    assert [type(o).__name__ for o in ops] == ["Select", "RequireSelected",
                                               "RestCookies"]
    ops = parse_cost("Set 1 【Arena】 Cookie in your battle area as active.")
    assert [type(o).__name__ for o in ops] == ["Select", "RequireSelected",
                                               "SetSelectedActive"]


def test_the_for_every_tail_reads_either_way_round(db):
    """"For every 3 【Arena】 Cookies in your break area, this Cookie gains
    +1 HP." Every other card in the pool prints the scale *after* the effect;
    BS12 prints it first, and it is the same sentence."""
    from braverse.compiler import parse_verb

    front = parse_verb("For every 3 【Arena】 Cookies in your break area, "
                       "this Cookie gains +1 HP.")
    back = parse_verb("This Cookie gains +1 HP for every 3 【Arena】 Cookies "
                      "in your break area.")
    assert [type(o).__name__ for o in front] == ["ScaledGainHP"]
    assert front == back


def test_playing_a_cookie_out_of_the_support_area_is_remembered(db):
    """BS12 uses the support area as a second hand and then asks about it —
    once about the board and once about the Cookie speaking."""
    from braverse.effect_ir import Condition

    game = _game(db)
    me = game.state.players[0]
    me.battle.clear()
    any_played = Condition("played_from_support")
    assert not any_played.holds(game._ctx(me))

    card = CardInstance.make("BS12-055", 0)
    me.support.append(card)
    me.support.remove(card)
    cookie = game._deploy_cookie(me, card, from_zone="support")
    assert any_played.holds(game._ctx(me))
    assert cookie.uid in me.played_from_support_this_turn

    ctx = game._ctx(me, source_cookie=cookie)
    assert Condition("self_played_from_support").holds(ctx)
    other = game._deploy_cookie(me, CardInstance.make("BS5-062", 0))
    assert not Condition("self_played_from_support").holds(
        game._ctx(me, source_cookie=other))


def test_a_buff_until_the_end_of_your_next_turn_survives_the_active_phase(db):
    """"Until the end of your next turn" is two turns of buff, not one: the
    Active Phase overwrites `attack_bonus` with what was banked for it, so a
    bonus applied only to the current turn expires at the start of the very
    turn the card says it lasts through."""
    from braverse.compiler import BuffUntilNextTurn

    game = _game(db)
    me = game.state.players[0]
    me.battle.clear()
    cookie = game._deploy_cookie(me, CardInstance.make("BS12-093", 0))
    printed = cookie.attack_damage(db)

    BuffUntilNextTurn(2, ref="it").run(
        game._ctx(me, source_cookie=cookie), {"it": [cookie]})
    assert cookie.attack_damage(db) == printed + 2

    game.end_turn()
    game.end_turn()                      # back round to this player
    assert cookie.attack_damage(db) == printed + 2

    game.end_turn()
    game.end_turn()
    assert cookie.attack_damage(db) == printed


def test_the_whole_trash_goes_back_into_the_deck_and_is_shuffled(db):
    """BS12-085 Rainbow Headphones. The shuffle goes through `state.rng`, or
    every existing replay of a game containing it would diverge."""
    from braverse.compiler import RecycleTrash

    game = _game(db)
    me = game.state.players[0]
    me.trash = [CardInstance.make("BS5-062", 0) for _ in range(7)]
    deck_before = len(me.deck)

    op = RecycleTrash()
    ctx = game._ctx(me)
    assert op.is_live(ctx, {})
    assert op.run(ctx, {})
    assert not me.trash
    assert len(me.deck) == deck_before + 7
    assert not op.is_live(game._ctx(me), {})     # nothing left to recycle


def test_an_opponents_cookie_can_be_put_on_the_bottom_of_their_deck(db):
    """BS12-057. The Cookie goes to *its owner's* deck, and being decked is not
    fainting — no break area, so no Level for the other seat."""
    game = _game(db)
    me, opp = game.state.players
    opp.battle.clear()
    victim = game._deploy_cookie(opp, CardInstance.make("BS5-062", 1))
    # A second Cookie so the battle area is not emptied: emptying it makes the
    # engine field a replacement, whose HP pile comes off the same deck and
    # would hide the one card this test is counting.
    game._deploy_cookie(opp, CardInstance.make("BS12-093", 1))
    deck_before, break_before = len(opp.deck), len(opp.break_area)

    game.cookie_to_deck(victim, bottom=True)
    assert victim not in opp.battle
    assert len(opp.deck) == deck_before + 1
    assert len(opp.break_area) == break_before
    assert opp.deck[-1].card_id == "BS5-062"        # the bottom


# --- the other two 【Equip】 Cookies -----------------------------------------
def test_producer_mic_shuts_off_flips_for_the_battle(db):
    """BS12-007: "your opponent cannot activate FLIP during this battle.\""""
    game = _game(db)
    me, opp = game.state.players
    me.battle.clear()
    opp.battle.clear()
    host = game._deploy_cookie(me, CardInstance.make("BS12-018", 0))
    mic = game._deploy_cookie(me, CardInstance.make("BS12-007", 0))
    assert host.name(db) == "Shining Glitter Cookie"
    game._deploy_cookie(opp, CardInstance.make("BS5-062", 1))
    me.support = [CardInstance.make("BS12-018", 0) for _ in range(8)]

    activates = [a for a in game.legal_actions()
                 if isinstance(a, A.ActivateSkill) and a.source_uid == mic.uid]
    assert len(activates) == 1
    game.step(activates[0])
    assert mic not in me.battle
    assert [c.card_id for c in host.equipment] == ["BS12-007"]

    game.state.turn_number = 3
    me.support = [CardInstance.make("BS12-018", 0) for _ in range(8)]
    host.rested = False
    attacks = [a for a in game.legal_actions()
               if isinstance(a, A.Attack) and a.attacker_uid == host.uid]
    assert attacks
    game.step(attacks[0])
    # Raised for the battle and put back down with it.
    assert not any(c.flip_disabled for c in opp.battle)


def test_angel_lightstick_draws_only_on_a_small_hand(db):
    """BS12-062: "if there are 5 cards or less in your hand, draw up to 2.\""""
    def run(hand_size):
        game = _game(db)
        me, opp = game.state.players
        me.battle.clear()
        opp.battle.clear()
        host = game._deploy_cookie(me, CardInstance.make("BS12-074", 0))
        stick = game._deploy_cookie(me, CardInstance.make("BS12-062", 0))
        assert host.name(db) == "Popping Candy Cookie"
        game._deploy_cookie(opp, CardInstance.make("BS5-062", 1))
        me.support = [CardInstance.make("BS12-074", 0) for _ in range(8)]
        game.step([a for a in game.legal_actions()
                   if isinstance(a, A.ActivateSkill) and a.source_uid == stick.uid][0])

        me.hand = [CardInstance.make("BS5-062", 0) for _ in range(hand_size)]
        game.state.turn_number = 3
        me.support = [CardInstance.make("BS12-074", 0) for _ in range(8)]
        host.rested = False
        before = len(me.hand)
        attacks = [a for a in game.legal_actions()
                   if isinstance(a, A.Attack) and a.attacker_uid == host.uid]
        assert attacks
        game.step(attacks[0])
        return len(me.hand) - before

    assert run(3) == 2          # small hand: draws
    assert run(6) == 0          # six cards: nothing


# --- the continuous abilities -----------------------------------------------
def test_cake_pops_caps_damage_only_beside_popping_candy(db):
    """"If there is a [Popping Candy Cookie] in your battle area, any time this
    Cookie would receive 2 or more damage, the damage is reduced to 1." """
    from braverse.effects import continuous_damage_cap

    game = _game(db)
    me = game.state.players[0]
    me.battle.clear()
    pops = game._deploy_cookie(me, CardInstance.make("BS12-063", 0))
    assert continuous_damage_cap(db, game.state, pops) is None

    game._deploy_cookie(me, CardInstance.make("BS12-074", 0))   # Popping Candy
    assert continuous_damage_cap(db, game.state, pops) == 1

    # It is *this* Cookie's shield, not an aura over the board.
    other = game._deploy_cookie(me, CardInstance.make("BS12-094", 0))
    assert continuous_damage_cap(db, game.state, other) is None


def test_strawberry_crepe_stays_rested_when_she_is_alone(db):
    """"During the Active Phase, if there is no other 【Arena】 Cookie in your
    battle area, this Cookie is not set as active." Conditional, so it is
    re-read every Active Phase rather than armed once."""
    game = _game(db)
    me = game.state.players[0]
    me.battle.clear()
    crepe = game._deploy_cookie(me, CardInstance.make("BS12-014", 0))
    crepe.rested = True
    game.end_turn()
    game.end_turn()
    assert crepe.rested                       # alone: stays down

    game._deploy_cookie(me, CardInstance.make("BS12-094", 0))   # another 【Arena】
    game.end_turn()
    game.end_turn()
    assert not crepe.rested                   # company: wakes up


def test_dj_cookie_taxes_the_other_seats_items(db):
    """"your opponent cannot activate Items unless they discard 1 card" — a
    price *and* a gate: an opponent who cannot discard cannot play the Item."""
    from braverse.effects import item_surcharge

    game = _game(db)
    me, opp = game.state.players
    me.battle.clear()
    assert item_surcharge(db, game.state, opp) == 0

    game._deploy_cookie(me, CardInstance.make("BS12-082", 0))
    assert item_surcharge(db, game.state, opp) == 1
    assert item_surcharge(db, game.state, me) == 0      # not on its own side


def test_werewolf_silences_a_level_three_attack_rider(db):
    """"When this Cookie battles, your opponent cannot activate attack effects
    of LV.3 Cookies during this battle." The swing still lands; the rider on
    the attack line does not."""
    from braverse.effects import attack_effect_silenced

    game = _game(db)
    me, opp = game.state.players
    me.battle.clear()
    opp.battle.clear()
    wolf = game._deploy_cookie(opp, CardInstance.make("BS12-089", 1))
    lv3 = game._deploy_cookie(me, CardInstance.make("BS12-094", 0))
    lv2 = game._deploy_cookie(me, CardInstance.make("BS12-093", 0))
    assert lv3.level(db) == 3 and lv2.level(db) == 2

    assert attack_effect_silenced(db, game.state, lv3, wolf)
    assert not attack_effect_silenced(db, game.state, lv2, wolf)
    other = game._deploy_cookie(opp, CardInstance.make("BS5-062", 1))
    assert not attack_effect_silenced(db, game.state, lv3, other)


# --- "placed in your break area by an 【Arena】 card effect" -------------------
def test_being_thrown_away_by_an_arena_card_pays_espresso_out(db):
    """The reward for the set's own discard costs. The qualifier is about the
    card doing the placing, not the card being placed."""
    game = _game(db)
    me = game.state.players[0]
    card = CardInstance.make("BS12-033", 0)          # Espresso Cookie
    me.hand.append(card)
    before = len(me.hand)

    arena = game._ctx(me, source_card=CardInstance.make("BS12-034", 0))
    assert Keyword.ARENA in db["BS12-034"].keywords
    me.hand.remove(card)
    game.place_in_break_by_effect(me, card, arena)
    assert card in me.break_area
    assert len(me.hand) == before               # -1 for the card, +1 drawn


def test_a_non_arena_card_effect_does_not_fire_it(db):
    game = _game(db)
    me = game.state.players[0]
    card = CardInstance.make("BS12-033", 0)
    plain = game._ctx(me, source_card=CardInstance.make("BS5-062", 0))
    assert Keyword.ARENA not in db["BS5-062"].keywords
    before = len(me.hand)
    game.place_in_break_by_effect(me, card, plain)
    assert card in me.break_area
    assert len(me.hand) == before               # no draw


def test_an_effect_placement_counts_towards_the_arena_break_asks(db):
    """"during this turn, if an 【Arena】 Cookie has been placed in your break
    area" — six cards ask it, and before this the counter only saw faints and
    refreshes, never the effect placements those cards are actually about."""
    game = _game(db)
    me = game.state.players[0]
    assert me.arena_break_additions_this_turn == 0
    ctx = game._ctx(me, source_card=CardInstance.make("BS12-034", 0))
    game.place_in_break_by_effect(me, CardInstance.make("BS12-094", 0), ctx)
    assert me.arena_break_additions_this_turn == 1
    assert me.break_additions_this_turn == 1


# --- the last three ----------------------------------------------------------
def test_herb_teapot_only_pays_out_for_the_cookie_it_played(db):
    """"If [Herb Cookie] was played by this effect" — the sentence before, not
    the board. A Herb Cookie already standing there does not count."""
    def run(support_id):
        game = _game(db)
        me = game.state.players[0]
        me.battle.clear()
        teapot = game._deploy_cookie(me, CardInstance.make("BS12-044", 0))
        me.support = [CardInstance.make(support_id, 0)]
        me.support[0].rested = True
        acts = [a for a in game.legal_actions()
                if isinstance(a, A.ActivateSkill) and a.source_uid == teapot.uid]
        assert acts
        game.step(acts[0])
        return me

    me = run("BS12-055")                     # Herb Cookie
    assert me.battle[-1].name(db) == "Herb Cookie"
    assert not me.support                    # it left the support area

    me = run("BS12-057")                     # another 【Arena】 Cookie
    assert me.battle[-1].name(db) != "Herb Cookie"


def test_licorice_buries_a_special_play_cookie_as_hp(db):
    """"Place 1 Cookie that has Special Play from your hand face-up on the top
    of that Cookie's HP." The top is the end of the pile: damage pops off the
    end, so this is the next card that Cookie turns over."""
    game = _game(db)
    me, opp = game.state.players
    me.battle.clear()
    opp.battle.clear()
    lic = game._deploy_cookie(me, CardInstance.make("BS12-109", 0))
    game._deploy_cookie(opp, CardInstance.make("BS12-093", 1))
    opp.support = [CardInstance.make("BS5-062", 1) for _ in range(3)]
    me.hand = [CardInstance.make("BS12-095", 0)]      # a 【Special Play】 Cookie
    game.state.turn_number = 3
    me.support = [CardInstance.make("BS12-109", 0) for _ in range(6)]
    depth = len(lic.hp_cards)

    attacks = [a for a in game.legal_actions()
               if isinstance(a, A.Attack) and a.attacker_uid == lic.uid]
    assert attacks
    game.step(attacks[0])
    assert not me.hand
    assert len(lic.hp_cards) == depth + 1
    assert lic.hp_cards[-1].card_id == "BS12-095"     # the top
    assert lic.hp_cards[-1].face_up


def test_licorice_holds_off_against_a_thin_support_area(db):
    game = _game(db)
    me, opp = game.state.players
    me.battle.clear()
    opp.battle.clear()
    lic = game._deploy_cookie(me, CardInstance.make("BS12-109", 0))
    game._deploy_cookie(opp, CardInstance.make("BS12-093", 1))
    opp.support = [CardInstance.make("BS5-062", 1) for _ in range(2)]   # only 2
    me.hand = [CardInstance.make("BS12-095", 0)]
    game.state.turn_number = 3
    me.support = [CardInstance.make("BS12-109", 0) for _ in range(6)]

    attacks = [a for a in game.legal_actions()
               if isinstance(a, A.Attack) and a.attacker_uid == lic.uid]
    game.step(attacks[0])
    assert len(me.hand) == 1                  # nothing buried


def test_kumiho_answers_a_swing_aimed_at_somebody_else(db):
    """"When one of your opponent's Cookies attacks" — every swing, not only
    the ones aimed at Kumiho, which is what separates this from
    【When Attacked】. Once a turn, and the cost is asked before it is paid."""
    game = _game(db)
    me, opp = game.state.players
    me.battle.clear()
    opp.battle.clear()
    attacker = game._deploy_cookie(me, CardInstance.make("BS12-094", 0))
    victim = game._deploy_cookie(opp, CardInstance.make("BS5-062", 1))
    kumiho = game._deploy_cookie(opp, CardInstance.make("BS12-053", 1))
    opp.support = [CardInstance.make("BS5-062", 1) for _ in range(3)]
    game.state.turn_number = 3
    me.support = [CardInstance.make("BS12-094", 0) for _ in range(8)]
    printed = attacker.attack_damage(db)
    support_before = len(opp.support)

    attacks = [a for a in game.legal_actions()
               if isinstance(a, A.Attack) and a.attacker_uid == attacker.uid
               and a.target_uid == victim.uid]
    assert attacks
    game.step(attacks[0])

    # Kumiho was not the target and still answered.
    assert len(opp.support) == support_before - 1
    assert attacker.attack_damage(db) == printed - 2
    assert Trigger.OPPONENT_ATTACKS.value in kumiho.used_markers

    # Once a turn: a second swing gets nothing.
    attacker.rested = False
    support_now = len(opp.support)
    attacks = [a for a in game.legal_actions()
               if isinstance(a, A.Attack) and a.attacker_uid == attacker.uid]
    if attacks:
        game.step(attacks[0])
        assert len(opp.support) == support_now
