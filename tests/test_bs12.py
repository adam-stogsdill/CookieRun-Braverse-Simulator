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
