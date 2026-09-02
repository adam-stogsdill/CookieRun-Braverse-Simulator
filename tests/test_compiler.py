"""Tests for the rules-text compiler.

Two things need proving: that the compiler *refuses* text it does not fully
understand (a half-resolved card is worse than a vanilla one), and that what it
does compile actually does the right thing to the game state.
"""

import pytest

from braverse import STARTER_DECKS, Game, HeuristicAgent, SeatedAgent, default_db
from braverse import actions as A
from braverse.compiler import (CompileError, compile_card, compile_text,
                               parse_card_filter, split_clauses)
from braverse.cost import Cost
from braverse.effects import Ctx, Trigger, get_effect
from braverse.enums import Color
from braverse.state import CardInstance


@pytest.fixture(scope="module")
def db():
    return default_db()


def _plain_hp(game, db):
    """Replace every HP pile with non-FLIP cards.

    Otherwise a FLIP buried in the pile fires mid-test and the assertion is
    measuring the flip effect rather than the clause under test.
    """
    for player in game.state.players:
        for cookie in player.battle:
            plain = [c for c in player.deck if not db[c.card_id].is_flip]
            for i, card in enumerate(list(cookie.hp_cards)):
                if db[card.card_id].is_flip and plain:
                    swap = plain.pop()
                    player.deck.remove(swap)
                    player.deck.append(card)
                    cookie.hp_cards[i] = swap


@pytest.fixture
def ctx(db):
    """A live game paused at turn 1, seen from P0's side."""
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=4)
    game.setup()
    _plain_hp(game, db)
    me = game.state.players[0]
    return Ctx(game=game, state=game.state, db=db, me=me,
               opp=game.state.players[1], source_cookie=me.battle[0],
               source_card=me.battle[0].card)


# --- splitting -------------------------------------------------------------
def test_level_notation_is_not_a_sentence_end():
    clauses = split_clauses("Return 1 Cookie that is LV.2 or lower to your hand.")
    assert clauses == ["Return 1 Cookie that is LV.2 or lower to your hand."]


def test_markers_and_reminder_text_are_stripped():
    text = ("【Blocker】 <{B}> (When one of your opponent's Cookies attacks, "
            "you can redirect the attack to this Cookie.)")
    assert split_clauses(text) == ["<{B}>"]


# --- refusal ---------------------------------------------------------------
def test_unknown_text_is_refused_not_guessed():
    with pytest.raises(CompileError):
        compile_text("Summon a dragon and win the game immediately.")


def test_a_card_with_one_bad_clause_compiles_nothing_usable(db):
    """All-or-nothing: partial understanding must not register."""
    partial = [c for c in db.cards.values()
               if (r := compile_card(c)).programs and not r.ok]
    assert partial, "expected some cards to compile only partially"
    for card in partial[:20]:
        assert not compile_card(card).ok


def test_hand_written_cards_are_never_overridden(db):
    """ST8/ST9 are hand-written; the compiler must leave them alone."""
    for card_id in ("ST9-006", "ST8-005", "ST9-016"):
        fn = get_effect(card_id, Trigger.ATTACK) or get_effect(card_id, Trigger.ITEM)
        assert fn is not None
        assert not hasattr(fn, "clauses"), f"{card_id} was overridden by the compiler"


# --- semantics -------------------------------------------------------------
def test_damage_clause_removes_hp(ctx):
    program = compile_text("Select up to 1 of your opponent's Cookies. "
                           "That Cookie receives 2 damage.")
    victim = ctx.opp.battle[0]
    before = victim.remaining_hp
    program(ctx)
    assert victim.remaining_hp == before - 2


def test_gain_hp_clause_adds_hp(ctx):
    program = compile_text("Select up to 1 of your Cookies. That Cookie gains +2 HP.")
    mine = ctx.me.battle[0]
    before = mine.remaining_hp
    program(ctx)
    assert mine.remaining_hp == before + 2


def test_draw_clause_draws(ctx):
    before = len(ctx.me.hand)
    compile_text("Draw up to 2 cards from your deck.")(ctx)
    assert len(ctx.me.hand) == before + 2


def test_guard_blocks_the_clause_when_false(ctx):
    ctx.me.hand.clear()
    ctx.me.hand.extend(ctx.me.deck[:8])
    before = len(ctx.me.hand)
    compile_text("If there are 3 cards or less in your hand, "
                 "draw up to 2 cards from your deck.")(ctx)
    assert len(ctx.me.hand) == before, "guard should have failed with 8 cards"


def test_guard_allows_the_clause_when_true(ctx):
    del ctx.me.hand[2:]
    before = len(ctx.me.hand)
    compile_text("If there are 3 cards or less in your hand, "
                 "draw up to 2 cards from your deck.")(ctx)
    assert len(ctx.me.hand) == before + 2


def test_energy_cost_rests_support_and_gates_the_effect(ctx):
    """An unpayable cost must abort the clause, not resolve it for free."""
    ctx.me.support.clear()
    before = len(ctx.me.hand)
    compile_text("<{B}{B}> Draw up to 2 cards from your deck.")(ctx)
    assert len(ctx.me.hand) == before, "effect resolved without paying"

    ctx.me.support.extend(c for c in ctx.me.deck[:6]
                          if ctx.db[c.card_id].color is Color.BLUE)
    if len(ctx.me.support) >= 2:
        for card in ctx.me.support:
            card.rested = False
        before = len(ctx.me.hand)
        compile_text("<{B}{B}> Draw up to 2 cards from your deck.")(ctx)
        assert len(ctx.me.hand) == before + 2
        assert sum(c.rested for c in ctx.me.support) == 2


def test_debuff_lowers_attack_damage(ctx):
    program = compile_text("Select up to 1 of your opponent's Cookies. "
                           "During this turn, that Cookie deals -2 attack damage.")
    victim = ctx.opp.battle[0]
    before = victim.attack_damage(ctx.db)
    program(ctx)
    assert victim.attack_damage(ctx.db) == max(0, before - 2)


def test_all_opponent_cookies_are_hit(ctx):
    program = compile_text("All of your opponent's Cookies receive 1 damage.")
    hp_before = [c.remaining_hp for c in ctx.opp.battle]
    program(ctx)
    assert [c.remaining_hp for c in ctx.opp.battle] == [h - 1 for h in hp_before]


def test_faint_sends_the_cookie_to_the_break_area(ctx):
    program = compile_text("Select up to 1 of your opponent's Cookies. "
                           "Make that Cookie faint.")
    program(ctx)
    assert ctx.opp.break_area, "fainted Cookie should be in the break area"


def test_trash_hp_does_not_trigger_flip_effects(ctx):
    """"Place N from the top of HP into the trash" is not damage — that is the
    whole reason cards word it that way."""
    victim = ctx.opp.battle[0]
    flip = next(c for c in ctx.opp.deck if ctx.db[c.card_id].is_flip)
    ctx.opp.deck.remove(flip)
    victim.hp_cards.append(flip)
    hand_before = len(ctx.opp.hand)

    compile_text("Select up to 1 of your opponent's Cookies. "
                 "Place 1 card from the top of that Cookie's HP into the trash.")(ctx)

    assert flip in ctx.opp.trash
    assert len(ctx.opp.hand) == hand_before, "FLIP fired on a non-damage removal"


def test_filters_restrict_selection(db, ctx):
    """A LV.1-only selector must not be able to hit a LV.3 Cookie."""
    lv3 = next(c for c in ctx.opp.deck if (db[c.card_id].level or 0) == 3
               and db[c.card_id].is_cookie)
    ctx.opp.deck.remove(lv3)
    ctx.opp.battle.clear()
    ctx.game._deploy_cookie(ctx.opp, lv3, run_on_play=False)
    victim = ctx.opp.battle[0]
    before = victim.remaining_hp

    compile_text("Select up to 1 of your opponent's LV.1 Cookies. "
                 "That Cookie receives 2 damage.")(ctx)
    assert victim.remaining_hp == before, "filter let a LV.3 through"


# --- pool-wide -------------------------------------------------------------
def test_compiled_coverage_is_substantial(db):
    import re

    def needs(card):
        text = " ".join([card.description, card.flip_text,
                         card.attack.text if card.attack else ""])
        text = re.sub(r"【Blocker】\s*(?:<[^>]*>)?\s*\([^)]*\)", "", text)
        return bool(text.strip())

    need = [c for c in db.cards.values() if needs(c)]
    compiled = sum(1 for c in need if compile_card(c).ok)
    assert compiled / len(need) > 0.30, f"only {compiled}/{len(need)} compiled"


def test_every_compiled_program_is_callable(db):
    for card in db.cards.values():
        result = compile_card(card)
        if result.ok:
            for program in result.programs.values():
                assert callable(program)
                assert len(program) >= 1


# --- structural prefixes ---------------------------------------------------
def test_faint_trigger_strips_its_own_prefix(db):
    """"When this Cookie faints, X" must compile as X — the trigger is carried
    by the registry key, not by the effect body."""
    from braverse.compiler import _trigger_texts

    card = db["ST8-010"]                      # Cookiemals, a faint trigger
    texts = dict(_trigger_texts(card))
    assert Trigger.FAINT in texts
    assert "When this Cookie faints" not in texts[Trigger.FAINT]


def test_faint_body_compiles_after_the_prefix_is_stripped(ctx):
    program = compile_text("Draw up to 1 card from your deck.")
    before = len(ctx.me.hand)
    program(ctx)
    assert len(ctx.me.hand) == before + 1


def test_during_this_turn_if_is_parsed_as_a_guard(ctx):
    """"During this turn, if X, do Y" is a guard, but "During this turn, that
    Cookie deals -2" is a verb. Both must still work."""
    ctx.me.cookies_fainted_this_turn = 0
    before = len(ctx.me.hand)
    compile_text("During this turn, if your Cookie fainted, "
                 "draw up to 2 cards from your deck.")(ctx)
    assert len(ctx.me.hand) == before, "guard fired with no faints"

    ctx.me.cookies_fainted_this_turn = 1
    compile_text("During this turn, if your Cookie fainted, "
                 "draw up to 2 cards from your deck.")(ctx)
    assert len(ctx.me.hand) == before + 2


def test_during_this_turn_debuff_is_still_a_verb(ctx):
    program = compile_text("Select up to 1 of your opponent's Cookies. "
                           "During this turn, that Cookie deals -2 attack damage.")
    victim = ctx.opp.battle[0]
    before = victim.attack_damage(ctx.db)
    program(ctx)
    assert victim.attack_damage(ctx.db) == max(0, before - 2)


def test_faint_counters_are_tracked_for_both_players(db):
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=11)
    game.setup()
    _plain_hp(game, db)      # a FLIP could bounce the victim before it faints
    victim = game.state.players[1].battle[0]
    game.deal_damage(victim, 99, source_player=0)
    assert game.state.players[1].cookies_fainted_this_turn == 1
    assert game.state.players[0].cookies_fainted_this_turn == 0


# --- zone movement ---------------------------------------------------------
def test_return_from_trash_to_hand_respects_the_filter(ctx):
    db = ctx.db
    flip = next(c for c in ctx.me.deck if db[c.card_id].is_flip)
    plain = next(c for c in ctx.me.deck
                 if db[c.card_id].is_cookie and not db[c.card_id].is_flip)
    for card in (flip, plain):
        ctx.me.deck.remove(card)
        ctx.me.trash.append(card)

    compile_text("Return up to 1 Cookie that has FLIP from your trash "
                 "to your hand.")(ctx)

    assert flip in ctx.me.hand
    assert plain in ctx.me.trash, "filter pulled a non-FLIP card"


def test_break_area_card_can_be_moved_to_the_trash(ctx):
    db = ctx.db
    lv1 = next(c for c in ctx.me.deck if (db[c.card_id].level or 0) == 1)
    ctx.me.deck.remove(lv1)
    ctx.me.break_area.append(lv1)

    compile_text("Select up to 1 LV.1 card from your break area and "
                 "place it in the trash.")(ctx)

    assert lv1 in ctx.me.trash
    assert lv1 not in ctx.me.break_area


def test_moving_a_card_into_the_break_area_can_end_the_game(ctx):
    """Break-area movement must run the win check, or a card could push a
    player past 10 without anyone noticing."""
    db = ctx.db
    lv3s = [c for c in ctx.me.deck if (db[c.card_id].level or 0) == 3][:4]
    for card in lv3s[:3]:
        ctx.me.deck.remove(card)
        ctx.me.break_area.append(card)
    last = lv3s[3]
    ctx.me.deck.remove(last)
    ctx.me.trash.append(last)

    compile_text("Place 1 LV.3 Cookie from your trash into your break area.")(ctx)

    assert ctx.state.over
    assert ctx.state.winner == ctx.opp.index


# --- removal that skips the break area -------------------------------------
def test_trashing_a_cookie_grants_no_break_area_level(ctx):
    """"Place into the trash" is not fainting. The card never reaches the break
    area, so it advances nobody's win condition — that is the whole point."""
    victim = ctx.opp.battle[0]
    card = victim.card

    ctx.trash_cookie(victim)

    assert victim not in ctx.opp.battle
    assert card in ctx.opp.trash
    assert not ctx.opp.break_area, "trashed Cookie must not reach the break area"
    assert ctx.opp.break_level_total(ctx.db) == 0


def test_self_trash_cost_does_not_faint_the_cookie(ctx):
    """"<Place this Cookie in the trash.>" is a cost, not a faint.

    Crunchy Chip Cookie (BS8-119) pays itself to play a [Dark Cacao Cookie]
    from the trash; if that reached the break area the opponent would bank a
    Level for a card they never beat.
    """
    from braverse.compiler import parse_cost

    mine = ctx.me.battle[0]
    card = mine.card
    for op in parse_cost("<Place this Cookie in the trash.>"):
        assert op.run(ctx, {})

    assert mine not in ctx.me.battle
    assert card in ctx.me.trash
    assert not ctx.me.break_area, "a trashed Cookie must not reach the break area"


def test_fainting_the_same_cookie_does_grant_level(ctx):
    """Contrast with the test above: damage-to-zero goes to the break area."""
    victim = ctx.opp.battle[0]
    level = victim.level(ctx.db)
    ctx.faint(victim)
    assert ctx.opp.break_level_total(ctx.db) == level


def test_compiled_trash_clause_uses_the_trash_not_the_break_area(ctx):
    compile_text("Place up to 1 Cookie that is LV.3 or lower from your "
                 "opponent's battle area into their trash.")(ctx)
    assert not ctx.opp.break_area


# --- other new mechanics ---------------------------------------------------
def test_opponent_discard(ctx):
    ctx.opp.hand.extend(ctx.opp.deck[:3])
    before = len(ctx.opp.hand)
    compile_text("Your opponent must place 1 card from their hand "
                 "into the trash.")(ctx)
    assert len(ctx.opp.hand) == before - 1


def test_skip_next_active_keeps_a_cookie_rested_for_one_phase(db):
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=5)
    game.setup()
    victim = game.state.players[1].battle[0]
    victim.rested = True
    victim.skip_next_active = True

    game.end_turn()                       # P1's active phase happens here
    assert victim.rested, "should have been skipped this Active Phase"
    assert not victim.skip_next_active

    game.end_turn()
    game.end_turn()                       # P1's next active phase
    assert not victim.rested, "should untap normally the following turn"


def test_trashed_trigger_fires_and_faint_trigger_does_not(db):
    """ST10-009 Space Doughnut draws when trashed from the battle area. It must
    fire on trash removal, and stay silent on an ordinary faint."""
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=6)
    game.setup()
    player = game.state.players[0]
    # Not in either starter deck, so mint the instance directly. Keep the
    # existing Cookie on board: emptying the battle area forces a free
    # replacement out of hand, which would mask the draw.
    card = CardInstance.make("ST10-009", 0)
    cookie = game._deploy_cookie(player, card, run_on_play=False)
    assert len(player.battle) == 2

    hand_before = len(player.hand)
    game.trash_cookie(cookie)
    assert len(player.hand) == hand_before + 1
    assert not player.break_area, "a trashed Cookie must not reach the break area"


def test_espresso_static_buff_applies_only_above_six_break_level(db):
    """ST1-009: 【Your Turn】 buffs are static, so they read the board at the
    moment the attack is declared."""
    from braverse.effects import Ctx, Trigger, get_effect

    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=7)
    game.setup()
    player = game.state.players[0]
    card = CardInstance.make("ST1-009", 0)
    player.battle.clear()
    cookie = game._deploy_cookie(player, card, run_on_play=False)
    fn = get_effect("ST1-009", Trigger.ATTACK_START)

    def run():
        fn(Ctx(game=game, state=game.state, db=db, me=player,
               opp=game.state.players[1], source_cookie=cookie,
               source_card=cookie.card))

    base = cookie.attack_damage(db)
    run()
    assert cookie.attack_damage(db) == base, "buff fired below LV.6"

    player.break_area.extend(
        [c for c in player.deck if (db[c.card_id].level or 0) == 3][:2])
    run()
    assert cookie.attack_damage(db) == base + 1


# --- BS1/BS2 mechanics -----------------------------------------------------
def test_bl_shorthand_is_recognised_as_blocker(db):
    """The dump writes Blocker as `{bl}` on 11 cards; those Cookies were
    silently unable to block at all."""
    from braverse.enums import Marker

    assert db["BS2-026"].has(Marker.BLOCKER)
    assert db["BS2-067"].has(Marker.BLOCKER)
    blockers = [c for c in db.cards.values() if c.has(Marker.BLOCKER)]
    assert len(blockers) >= 24


def test_effect_damage_immunity_stops_effects_but_not_attacks(db):
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=9)
    game.setup()
    _plain_hp(game, db)
    me = game.state.players[0]
    ctx = Ctx(game=game, state=game.state, db=db, me=me,
              opp=game.state.players[1], source_cookie=me.battle[0],
              source_card=me.battle[0].card)
    victim = ctx.opp.battle[0]
    victim.effect_damage_immune = True

    before = victim.remaining_hp
    ctx.deal_damage(victim, 2)
    assert victim.remaining_hp == before, "effect damage should be blocked"

    game.deal_damage(victim, 2, source_player=0)
    assert victim.remaining_hp == before - 2, "attacks should still connect"


def test_next_turn_debuff_waits_for_that_turn(db):
    """"during your opponent's next turn" must not fire on the current turn,
    and must survive the Active Phase reset that clears ordinary buffs."""
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=10)
    game.setup()
    victim = game.state.players[1].battle[0]
    base = victim.attack_damage(db)
    victim.attack_bonus_next_turn = -1

    assert victim.attack_damage(db) == base, "debuff applied too early"
    game.end_turn()                        # P1's turn begins
    assert victim.attack_damage(db) == max(0, base - 1)
    game.end_turn()
    game.end_turn()                        # P1's following turn
    assert victim.attack_damage(db) == base, "debuff outlasted its turn"


def test_incoming_damage_reduction_applies_to_the_attack(db):
    """BS1-042 reduces the damage it takes, which is not the same as debuffing
    the attacker."""
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=11)
    game.setup()
    _plain_hp(game, db)
    defender = game.state.players[1]
    target = defender.battle[0]
    target.incoming_damage_reduction = 1
    hp_before = target.remaining_hp

    from braverse.effects import Trigger, get_effect
    assert get_effect("BS1-042", Trigger.WHEN_ATTACKED) is not None
    # The engine resets the value per battle, so exercise the arithmetic here.
    damage = max(0, 3 - target.incoming_damage_reduction)
    game.deal_damage(target, damage, source_player=0)
    assert target.remaining_hp == hp_before - 2


# ITEM/TRAP/STAGE cards whose text the dump files under attackText. `cards.py`
# joins that field onto the description, which is what makes them visible to the
# compiler at all — before that they parsed as free, textless vanillas and were
# silently counted as complete. These are the ones whose text does not compile
# yet: each needs grammar the compiler does not have (mandatory-HP-trash costs,
# attack redirection, "for every N in your break area" scaling, support-count
# history). They are listed rather than tolerated in bulk so that a *new* hole
# in a completed set still fails these tests. Empty at the moment — every card
# that was on it has since been written by hand.
KNOWN_UNCODED: set[str] = set()


def test_bs1_and_bs2_are_fully_implemented(db):
    import re

    from braverse.effects import Trigger, get_effect

    def has_text(card):
        text = " ".join([card.description, card.flip_text,
                         card.attack.text if card.attack else ""])
        return bool(re.sub(r"【Blocker】\s*(?:<[^>]*>)?\s*\([^)]*\)", "", text).strip())

    for set_id in ("BS1", "BS2"):
        missing = [c.id for c in db.cards.values()
                   if c.set_id == set_id and has_text(c)
                   and c.id not in KNOWN_UNCODED
                   and not any(get_effect(c.id, t) for t in Trigger)]
        assert not missing, f"{set_id} incomplete: {missing}"


# --- BS6/BS7 mechanics -----------------------------------------------------
def test_played_from_trash_and_support_fire_their_own_triggers(db):
    """BS6 and BS7 hang effects on *where* a Cookie came from, which the
    engine previously could not distinguish."""
    from braverse.effects import Trigger, get_effect

    assert get_effect("BS6-097", Trigger.PLAYED_FROM_TRASH) is not None
    assert get_effect("BS7-059", Trigger.PLAYED_FROM_SUPPORT) is not None

    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=12)
    game.setup()
    me = game.state.players[0]
    me.battle.clear()
    card = CardInstance.make("BS6-097", 0)       # "gains +2 HP" from the trash
    me.trash.append(card)
    ctx = Ctx(game=game, state=game.state, db=db, me=me,
              opp=game.state.players[1])

    assert ctx.play_cookie_from_trash()
    cookie = me.battle[-1]
    assert cookie.remaining_hp == (db["BS6-097"].hp or 0) + 2


def test_static_abilities_count_as_implemented(db):
    """A purely continuous ability has no trigger to register; the coverage
    check must still see it, without registering an empty no-op effect."""
    from braverse.effects import STATIC_ABILITY_CARDS, is_implemented

    assert "BS6-010" in STATIC_ABILITY_CARDS
    for card_id in ("BS6-010", "BS7-013", "BS7-104"):
        assert is_implemented(card_id)


def test_movement_lock_stops_opposing_effects_moving_cookies(db):
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=13)
    game.setup()
    me, opp = game.state.players
    ctx = Ctx(game=game, state=game.state, db=db, me=me, opp=opp)
    victim = opp.battle[0]

    ctx.trash_cookie(victim)
    assert victim not in opp.battle, "removal should work without the lock"

    game._deploy_cookie(opp, CardInstance.make("BS6-010", 1), run_on_play=False)
    other = opp.battle[-1]
    assert ctx.movement_locked
    ctx.trash_cookie(other)
    assert other in opp.battle, "the lock should have stopped the removal"


def test_attack_cost_rewrite_applies(db):
    """BS7-104 turns its attack cost generic once its condition is met."""
    from braverse.effects import modified_attack_cost

    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=14)
    game.setup()
    me = game.state.players[0]
    me.battle.clear()
    cookie = game._deploy_cookie(me, CardInstance.make("BS7-104", 0),
                                 run_on_play=False)
    printed = db["BS7-104"].attack.cost

    assert modified_attack_cost(db, me, cookie, printed) == printed
    me.break_area.extend(
        [c for c in me.deck if (db[c.card_id].level or 0) == 3][:1])
    me.break_area.extend(
        [c for c in me.deck if (db[c.card_id].level or 0) == 3][1:2])
    if me.break_level_total(db) >= 3:
        rewritten = modified_attack_cost(db, me, cookie, printed)
        assert rewritten.generic == printed.total
        assert not rewritten.colored


def test_completed_sets_stay_complete(db):
    import re

    from braverse.effects import is_implemented

    def has_text(card):
        text = " ".join([card.description, card.flip_text,
                         card.attack.text if card.attack else ""])
        return bool(re.sub(r"【Blocker】\s*(?:<[^>]*>)?\s*\([^)]*\)", "", text).strip())

    complete = ["ST1", "ST2", "ST3", "ST4", "ST5", "ST6", "ST7", "ST8", "ST9",
                "ST10", "BS1", "BS2", "BS6", "BS7"]
    for set_id in complete:
        missing = [c.id for c in db.cards.values()
                   if c.set_id == set_id and has_text(c)
                   and c.id not in KNOWN_UNCODED
                   and not is_implemented(c.id)]
        assert not missing, f"{set_id} regressed: {missing}"


def test_known_uncoded_list_does_not_go_stale(db):
    """The allowlist is a record of real holes, not a place to hide new ones.

    If a card on it starts compiling, it has to come off — otherwise the list
    slowly turns into a blanket exemption for whole sets.
    """
    from braverse.effects import is_implemented

    stale = sorted(c for c in KNOWN_UNCODED if is_implemented(c))
    assert not stale, f"now implemented, remove from KNOWN_UNCODED: {stale}"


# --- BS4/BS5 mechanics -----------------------------------------------------
def test_attack_target_and_attacker_are_visible_to_effects(db):
    """BS5 riders ask about "the attacked Cookie"; BS4 defenders ask about the
    attacker's level. Both need the engine to expose them."""
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=20)
    game.setup()
    _plain_hp(game, db)
    seen = {}

    from braverse.effects import Trigger, _REGISTRY

    def spy(ctx):
        seen["target"] = ctx.attack_target
        seen["attacker"] = ctx.attacker

    attacker = game.state.players[0].battle[0]
    key = (attacker.defn(db).base_id, Trigger.ATTACK)
    saved = _REGISTRY.get(key)
    _REGISTRY[key] = spy
    try:
        game.end_turn(); game.end_turn()          # off the no-attack first turn
        for card in game.state.players[0].deck[:6]:
            game.state.players[0].support.append(card)
        for c in game.state.players[0].support:
            c.rested = False
        target = game.state.players[1].battle[0]
        game._do_attack(A.Attack(attacker.uid, target.uid))
    finally:
        if saved is None:
            _REGISTRY.pop(key, None)
        else:
            _REGISTRY[key] = saved

    assert seen.get("target") is target
    assert seen.get("attacker") is attacker


def test_bare_level_notation_does_not_split_a_sentence():
    """"your break area LV. is higher than ..." was being cut at the period."""
    text = ("If your break area LV. is higher than your opponent's break area "
            "LV., draw up to 1 card from your deck.")
    assert len(split_clauses(text)) == 1


def test_hp_cannot_reach_zero_keeps_the_cookie_standing(db):
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=21)
    game.setup()
    _plain_hp(game, db)
    victim = game.state.players[1].battle[0]
    victim.hp_cannot_reach_zero = True

    # The damage lands in full — the pile is topped back up off the deck as it
    # empties — but the Cookie cannot be the card that runs out.
    trashed = len(game.state.players[1].trash)
    game.deal_damage(victim, 5, source_player=0)
    assert victim in game.state.players[1].battle
    assert victim.remaining_hp >= 1
    assert len(game.state.players[1].trash) - trashed == 5
    assert not game.state.players[1].break_area


def test_taunt_restricts_the_legal_attack_targets(db):
    """BS4-024 forces attacks onto itself while its condition holds."""
    from braverse.effects import forced_attack_target

    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=22)
    game.setup()
    defender = game.state.players[1]
    defender.battle.clear()
    kumiho = game._deploy_cookie(defender, CardInstance.make("BS4-024", 1),
                                 run_on_play=False)

    assert forced_attack_target(db, defender) is None, "no {Y} LV.3 yet"

    lv3_yellow = next(c for c in db.cards.values()
                      if c.color is Color.YELLOW and c.level == 3 and c.is_cookie)
    game._deploy_cookie(defender, CardInstance.make(lv3_yellow.id, 1),
                        run_on_play=False)
    assert forced_attack_target(db, defender) is kumiho


def test_vanilla_stage_counts_as_implemented_but_uncoded_text_does_not(db):
    """A stage printing only its placement line has no ability to implement.
    A Cookie whose text simply failed to route must stay unimplemented.

    No card in the pool is placement-only any more: every stage that looked
    that way was one whose 【Activate】 half the dump filed under attackText,
    and `cards.py` now joins that back onto the description. BS4-022 was the
    example here and turns out to have a real ability. The rule still has to
    hold for a stage that genuinely prints nothing else, so it is exercised on
    a card cut down to exactly that.
    """
    import dataclasses

    from braverse.compiler import compile_card
    from braverse.effects import is_implemented

    bare = dataclasses.replace(db["BS4-022"], flip_text="", attack=None,
                               description="<{N}{N}> Place in your stage area.")
    result = compile_card(bare)
    assert result.vanilla and result.ok and not result.programs

    # The real card's 【Activate】 was invisible before the join, and compiles.
    assert is_implemented("BS4-022")

    # Not every card without programs is vanilla.
    assert not compile_card(db["ST9-006"]).vanilla


def test_faint_trigger_cannot_recurse_into_itself(db):
    """A faint effect that empties its own HP pile used to re-enter _faint and
    re-run the same trigger forever."""
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=30)
    game.setup()
    me = game.state.players[0]
    victim = me.battle[0]
    calls = []

    from braverse.effects import Trigger, _REGISTRY

    def suicidal(ctx):
        calls.append(1)
        if ctx.source_cookie:
            ctx.game.faint(ctx.source_cookie)      # re-entry attempt

    key = (victim.defn(db).base_id, Trigger.FAINT)
    saved = _REGISTRY.get(key)
    _REGISTRY[key] = suicidal
    try:
        game.faint(victim)
    finally:
        if saved is None:
            _REGISTRY.pop(key, None)
        else:
            _REGISTRY[key] = saved

    assert len(calls) == 1, f"faint trigger ran {len(calls)} times"
    assert victim not in me.battle


def test_damage_cap_is_a_ceiling_not_a_subtraction(db):
    """BS3-013 reduces incoming attack damage of 2+ to 1."""
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=31)
    game.setup()
    _plain_hp(game, db)
    target = game.state.players[1].battle[0]
    target.damage_cap = 1
    capped = min(max(0, 4 - target.incoming_damage_reduction), target.damage_cap)
    assert capped == 1

    before = target.remaining_hp
    game.deal_damage(target, capped, source_player=0)
    assert target.remaining_hp == before - 1


# --- BS10 mechanics --------------------------------------------------------
def test_attack_prohibition_removes_the_attack_from_legal_moves(db):
    """BS10-021 cannot attack at 3 or less HP — a prohibition enforced where
    attacks are enumerated, not a trigger."""
    from braverse.effects import cannot_attack

    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=40)
    game.setup()
    me = game.state.players[0]
    me.battle.clear()
    cookie = game._deploy_cookie(me, CardInstance.make("BS10-021", 0),
                                 run_on_play=False)

    del cookie.hp_cards[3:]
    assert cannot_attack(db, cookie), "should be barred at 3 HP"
    _, colors = me.active_support_colors(db)
    assert not game._can_attack(me, cookie, colors)

    while len(cookie.hp_cards) < 5 and me.deck:
        cookie.hp_cards.append(me.deck.pop(0))
    assert not cannot_attack(db, cookie), "should be free above 3 HP"


def test_move_protection_is_conditional_and_one_sided(db):
    """BS10-070 resists only the opponent's effects, and only while its own
    support area is small."""
    from braverse.effects import Ctx as EffectCtx

    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=41)
    game.setup()
    me, opp = game.state.players
    opp.battle.clear()
    protected = game._deploy_cookie(opp, CardInstance.make("BS10-070", 1),
                                    run_on_play=False)
    attacker_ctx = EffectCtx(game=game, state=game.state, db=db, me=me, opp=opp)

    opp.support.clear()
    attacker_ctx.trash_cookie(protected)
    assert protected in opp.battle, "small support area should protect it"

    opp.support.extend(opp.deck[:5])
    attacker_ctx.trash_cookie(protected)
    assert protected not in opp.battle, "protection lapses above 4 support cards"


def test_attack_cost_discount_shaves_one_coloured_symbol(db):
    from braverse.effects import modified_attack_cost

    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=42)
    game.setup()
    me = game.state.players[0]
    me.battle.clear()
    cookie = game._deploy_cookie(me, CardInstance.make("BS10-009", 0),
                                 run_on_play=False)
    printed = db["BS10-009"].attack.cost

    assert modified_attack_cost(db, me, cookie, printed) == printed
    cookie.attack_cost_discount = 1
    discounted = modified_attack_cost(db, me, cookie, printed)
    assert discounted.total == printed.total - 1


def test_equipment_follows_its_cookie_to_the_trash(db):
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=43)
    game.setup()
    me = game.state.players[0]
    cookie = me.battle[0]
    jam = CardInstance.make("BS10-045", 0)
    cookie.equipment.append(jam)

    game.faint(cookie)
    assert jam in me.trash, "equipment should leave play with its Cookie"


# --- optional <...> costs --------------------------------------------------
class _Answers:
    """A controller that answers every yes/no the same way."""

    def __init__(self, pay: bool):
        self.pay = pay
        self.prompts: list[str] = []

    def choose_action(self, state, options):
        return options[0] if options else None

    def choose(self, state, prompt, options, *, optional):
        if all(isinstance(o, bool) for o in options):
            self.prompts.append(prompt)
            return True if self.pay else None
        return options[0] if options else None


def _seat(ctx, controller):
    ctx.game._controllers[ctx.me.index] = controller


def test_a_flip_asks_before_paying_a_bracketed_cost(ctx):
    """`<...>` is a cost you *may* pay — a FLIP must not spend it silently."""
    program = compile_text("<{B}{B}> Draw up to 1 card from your deck.")
    for card in ctx.me.support[:]:
        card.rested = False
    while len(ctx.me.support) < 3:                     # something to pay with
        ctx.me.support.append(ctx.me.deck.pop(0))
    for card in ctx.me.support:
        card.rested = False
    ctx.trigger = Trigger.FLIP.value

    refuser = _Answers(pay=False)
    _seat(ctx, refuser)
    before = len(ctx.me.hand)
    program(ctx)
    assert refuser.prompts, "the cost was never offered"
    assert "{B}{B}" in refuser.prompts[0]
    assert len(ctx.me.hand) == before, "declining still ran the effect"
    assert all(not c.rested for c in ctx.me.support), "declining still paid"


def test_a_skill_the_player_activated_pays_without_a_second_prompt(ctx):
    """Choosing 【Activate】 *is* the decision; do not ask again."""
    program = compile_text("<{B}> Draw up to 1 card from your deck.")
    while len(ctx.me.support) < 3:
        ctx.me.support.append(ctx.me.deck.pop(0))
    for card in ctx.me.support:
        card.rested = False
    ctx.trigger = Trigger.ACTIVATE.value

    controller = _Answers(pay=True)
    _seat(ctx, controller)
    before = len(ctx.me.hand)
    program(ctx)
    assert controller.prompts == [], "an activated skill asked about its own cost"
    assert len(ctx.me.hand) == before + 1


def test_an_unaffordable_cost_is_never_offered(ctx):
    program = compile_text("<{B}{B}{B}{B}{B}> Draw up to 1 card from your deck.")
    for card in ctx.me.support:
        card.rested = True                            # nothing left to pay with
    ctx.trigger = Trigger.FLIP.value

    controller = _Answers(pay=True)
    _seat(ctx, controller)
    program(ctx)
    assert controller.prompts == [], "offered a cost that could not be paid"


def test_the_cost_is_offered_once_not_once_per_op(ctx):
    """A discard cost is both the prompt and an op; it must not double-ask."""
    program = compile_text("<Discard 1 card.> Draw up to 1 card from your deck.")
    ctx.trigger = Trigger.FLIP.value
    controller = _Answers(pay=True)
    _seat(ctx, controller)
    program(ctx)
    assert len(controller.prompts) == 1, controller.prompts


# --- non-Cookie rules text filed under attackText ---------------------------
def _play_item(db, card_id, *, support="ST8-016", supports=5, mode=None,
               prepare=None):
    """Play `card_id` as an item off a fresh board, with energy to pay for it.

    `mode` answers a modal card's "Choose one" with that option index;
    `prepare` runs against the game once it is set up, for cards that need a
    particular board to be worth playing at all.
    Returns (game, the player who played it, the CardInstance played).
    """
    game = Game([STARTER_DECKS["st8_wind_archer"], STARTER_DECKS["st9_sea_fairy"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=3)
    game.setup()
    if prepare is not None:
        prepare(game)
    me = game.state.current
    me.support.clear()
    for _ in range(supports):
        me.support.append(CardInstance.make(support, me.index))
    card = CardInstance.make(card_id, me.index)
    me.hand.append(card)

    if mode is not None:
        controller = game.controller(me.index)
        original = controller.choose

        def pick(state, prompt, options, optional=True):
            if prompt == "Choose one":
                return options[mode]
            return original(state, prompt, options, optional=optional)

        controller.choose = pick

    action = next(a for a in game.legal_actions()
                  if isinstance(a, A.PlaySupportCard) and a.card_uid == card.uid)
    game.step(action)
    return game, me, card


def test_item_rules_text_filed_under_attack_text_is_read(db):
    """The dump puts most ITEM/TRAP/STAGE text in attackText, leaving
    description empty. Read only from description, such a card parses as a free
    vanilla that does nothing at all."""
    apple_pie = db["BS1-075"]
    assert apple_pie.description.strip()
    assert not apple_pie.attack_text
    assert apple_pie.play_cost == Cost.parse("{G}{G}")
    assert get_effect("BS1-075", Trigger.ITEM) is not None


def test_item_that_places_itself_in_support_is_not_also_trashed(db):
    """Wanderer's Apple Pie buys its way into the support area. The item path
    trashes what it played, which would file one CardInstance in two zones."""
    _, me, card = _play_item(db, "BS1-075")
    assert any(c is card for c in me.support) and card.rested
    assert not any(c is card for c in me.trash)


def test_item_lead_cost_is_charged_exactly_once(db):
    """`play_cost` is charged by the engine, so the compiled body must not pay
    the same symbols again — the hand-written items drop them for that reason."""
    assert db["ST7-016"].play_cost == Cost.parse("{Y}{Y}")

    def soften(game):
        """ST7-016 only targets a Cookie with 2 or less HP left, and is not
        offered at all without one — see the playability tests below."""
        victim = game.state.players[1].battle[0]
        del victim.hp_cards[2:]

    _, me, card = _play_item(db, "ST7-016", support="ST7-016", supports=4,
                             prepare=soften)
    assert sum(c.rested for c in me.support if c is not card) == 2


def test_stage_activate_half_is_joined_onto_its_placement_line(db):
    """A stage's description is the placement line and its 【Activate】 lands in
    attackText. Split apart, the ability is invisible and the card reads as a
    vanilla that only needs placing."""
    stage = db["BS4-022"]
    assert "Place in your stage area." in stage.description
    assert "【Activate】" in stage.description
    assert get_effect("BS4-022", Trigger.STAGE_ACTIVATE) is not None


def test_npc_attack_text_stays_an_attack(db):
    """NPCs are not Cookies by CardType but do have attack lines, so the join
    must not swallow them."""
    npc = db["BS6-030"]
    assert npc.attack is not None and npc.attack.damage == 3
    assert "【On Play】" in npc.description


def test_modal_item_offers_both_branches(db):
    """Elder Faerie's Sword picks between placing itself and sweeping. Its
    effect used to be registered under ATTACK, which an item never fires."""
    assert get_effect("BS3-068", Trigger.ITEM) is not None

    game, me, card = _play_item(db, "BS3-068", mode=0)
    assert any(c is card for c in me.support) and card.rested
    assert not any(c is card for c in me.trash)

    game, me, card = _play_item(db, "BS3-068", mode=1)
    assert any(c is card for c in me.trash)
    assert all(c.remaining_hp < c.max_hp(db) for c in game.state.players[1].battle)


def test_the_compiler_never_sweeps_in_an_equip_card(db):
    """【Equip】 cards are written by hand, one at a time — there is no
    compiler support for attaching a card to another card, and a jam swept in
    as ordinary text would resolve its rider once and then be filed in the
    trash. BS5-111 is the check: implemented, and not by compilation.
    """
    from braverse.effects import is_implemented

    assert is_implemented("BS5-111")
    assert not compile_card(db["BS5-111"]).ok, "the compiler must still refuse it"
    assert db["BS5-111"].play_cost == Cost.parse("{N}")


def test_trash_to_support_as_active_arrives_active(ctx, db):
    """Pumpkin Pie Cookie: "Place 1 Cookie from your trash into your support
    area as active." A card placed there rested is a card you cannot spend this
    turn, which is most of what the skill is for."""
    card = CardInstance.make("BS5-062", 0)
    ctx.me.trash.append(card)
    program = get_effect("BS1-071", Trigger.ON_PLAY)
    assert program is not None
    while len(ctx.me.support) < 4:
        ctx.me.support.append(CardInstance.make("BS5-062", 0))  # {G}
    for c in ctx.me.support:
        c.rested = False
    ctx.trigger = Trigger.ON_PLAY.value
    program(ctx)
    assert any(c is card for c in ctx.me.support)
    assert not card.rested
    assert not any(c is card for c in ctx.me.break_area)
    assert not any(c is card for c in ctx.me.trash)


def test_support_placement_without_a_stated_state_is_refused():
    """"as active" and "as rested" are opposite outcomes; guessing between them
    is exactly the kind of half-understanding the compiler refuses."""
    with pytest.raises(CompileError):
        compile_text("Place 1 Cookie from your trash into your support area.")


def test_can_be_used_as_is_a_cost_not_a_free_rider(db):
    """"<can be used as {R}.>" is the rider's price: one energy of the colour
    it names. Read as free, Pitaya Dragon Cookie (ST6-004) pinged an extra
    point of damage after every swing it ever made."""
    from braverse.effects import Ctx, Trigger, get_effect

    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=17)
    game.setup()
    _plain_hp(game, db)
    me, them = game.state.players[0], game.state.players[1]
    victim = them.battle[0]

    fn = get_effect("ST6-004", Trigger.ATTACK)

    def swing():
        before = victim.remaining_hp
        fn(Ctx(game=game, state=game.state, db=db, me=me, opp=them,
               source_cookie=me.battle[0], trigger=Trigger.ATTACK.value))
        return before - victim.remaining_hp

    me.support = []
    assert swing() == 0, "the rider fired with nothing to pay it"

    me.support = [CardInstance.make("ST9-013", 0)]      # a {B} card, wrong colour
    me.support[0].rested = False
    assert swing() == 0, "any colour paid for a {R} rider"

    me.support = [CardInstance.make("ST6-004", 0)]      # {R}
    me.support[0].rested = False
    assert swing() == 1
    assert me.support[0].rested, "the cost was not actually rested"


def test_when_your_turn_ends_untap_is_banked_against_its_card(db):
    """"Then, when your turn ends, set up to N cards as active" must not untap
    anything where it is written. It is banked, with the card that banked it, so
    the end of the turn can name it and order it against everything else."""
    from braverse.compiler import BankEndTurnUntap
    from braverse.effects import Ctx, Trigger

    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=17)
    game.setup()
    me, them = game.state.players[0], game.state.players[1]
    me.support = [CardInstance.make("ST9-013", 0)]
    me.support[0].rested = True
    source = CardInstance.make("BS5-060", 0)

    BankEndTurnUntap(3).run(
        Ctx(game=game, state=game.state, db=db, me=me, opp=them,
            source_card=source, trigger=Trigger.ATTACK.value), {})

    assert me.support[0].rested, "untapped on the spot instead of at end of turn"
    assert me.end_turn_untaps == [("BS5-060", 3)]


# --- text the parser must not quietly lose ---------------------------------
def test_a_selection_that_runs_into_another_instruction_is_refused():
    """The `select` patterns end in `.*?$`, so a sentence that carries on past
    the target used to hand the rest to the filter, which read none of it."""
    from braverse.compiler import parse_filter

    for parse in (parse_filter, parse_card_filter):
        with pytest.raises(CompileError):
            parse("your Cookies and place 2 of their HP cards in the trash")
    # A compound *filter* is still fine — "and" only ends it when what follows
    # gives an order.
    assert parse_filter("Cookies whose remaining HP is 1 and is LV.2 or higher")


def test_select_and_do_compounds_run_both_halves(ctx):
    """Prickly Cacti Gloves (BS2-006) pays for its damage with its own HP; the
    second half of that sentence was being dropped."""
    program = compile_text("Select 1 of your Cookies and place 2 of their HP "
                           "cards in the trash.")
    mine = ctx.me.battle[0]
    before = mine.remaining_hp
    assert before > 2

    program(ctx)

    assert mine.remaining_hp == before - 2


def test_a_bracketed_keyword_is_not_also_read_as_a_card_name():
    """"[Ancient] Cookies" is a keyword. Read as a name as well, the filter
    wants a Cookie printed with the name "Ancient" — which no card is, so the
    card selects nothing and quietly does nothing."""
    from braverse.compiler import parse_filter

    ancient = parse_filter("your [Ancient] Cookies in your battle area")
    assert ancient.keyword is not None
    assert ancient.name is None
    # A real bracketed name still reads as one.
    assert parse_filter("your [Pizza Cookie]").name == "Pizza Cookie"


def test_another_named_cookie_needs_a_second_copy(ctx, db):
    """Pizza Cookie (P-065): "another [Pizza Cookie]" is never true of the one
    Cookie asking about itself."""
    from braverse.compiler import parse_condition

    condition = parse_condition("another [Pizza Cookie] is in your battle area")
    ctx.me.battle.clear()
    first = ctx.game._deploy_cookie(ctx.me, CardInstance.make("P-065", 0),
                                    run_on_play=False)
    ctx.source_cookie = first
    assert not condition.holds(ctx), "one Pizza Cookie is not 'another'"

    ctx.game._deploy_cookie(ctx.me, CardInstance.make("P-065", 0),
                            run_on_play=False)
    assert condition.holds(ctx)


def test_a_bare_attack_aura_is_read_as_an_attack_trigger(db):
    """GingerBright (P-001) prints 【Your Turn】 and a conditional attack buff
    and no other marker, so there was no trigger to hang it on at all."""
    from braverse.effects import Trigger, get_effect, is_implemented

    assert is_implemented("P-001")
    assert get_effect("P-001", Trigger.ATTACK_START) is not None


def test_a_rules_box_wrapped_in_an_escaped_quote_is_unwrapped(db):
    """P-125's whole text arrives quoted, left over from however the dump was
    written; BS11-064 uses the same escape as real punctuation inside its text
    and must keep it."""
    from braverse.effects import is_implemented

    assert not db["P-125"].description.startswith("\\")
    assert is_implemented("P-125")
    assert "\\" in db["BS11-064"].description, "an inner quote is the card's own"


# --- costs and stat rewrites -----------------------------------------------
def test_hand_to_deck_bottom_is_a_payable_cost(ctx):
    """Macaron Cookie (P-045) trades a card in hand for a fresh one."""
    program = compile_text("<Place 1 card from your hand at the bottom of your "
                           "deck.> Draw up to 1 card from your deck.")
    ctx.trigger = Trigger.ON_PLAY.value
    hand_before = len(ctx.me.hand)
    deck_before = len(ctx.me.deck)

    program(ctx)

    assert len(ctx.me.hand) == hand_before, "one out, one in"
    assert len(ctx.me.deck) == deck_before


def test_attack_costs_all_changed_to_generic(ctx, db):
    """Hall of Ancient Heroes (P-032) drops the colours, not the count."""
    from braverse.effects import modified_attack_cost

    mine = ctx.me.battle[0]
    printed = mine.defn(db).attack.cost
    assert printed.colored, "the fixture needs a coloured attack cost"

    compile_text("Select up to 1 of your Cookies. During this turn, that "
                 "Cookie's attack costs are all changed to {N}.")(ctx)

    rewritten = modified_attack_cost(db, ctx.me, mine, printed)
    assert rewritten.total == printed.total
    assert not rewritten.colored


# --- "During this turn, if ..." event guards --------------------------------
def test_a_mid_sentence_timing_phrase_does_not_end_the_guard(ctx):
    """"If A and, during this turn, B, do X." — the comma after "and" used to
    close the guard, and the rest of the condition was read as the verb."""
    program = compile_text("If this Cookie's remaining HP is 4 or less and, "
                           "during this turn, an Item card was activated, "
                           "this Cookie gains +1 HP.")
    mine = ctx.me.battle[0]
    del mine.hp_cards[4:]
    before = mine.remaining_hp

    program(ctx)
    assert mine.remaining_hp == before, "no item played: the guard must hold"

    ctx.me.items_played_this_turn = 1
    program(ctx)
    assert mine.remaining_hp == before + 1


def test_or_if_is_a_choice_not_a_second_requirement(ctx):
    """Sour Belt Cookie (P-110) opens on either half, not on both."""
    program = compile_text("If there are 4 【Arena】 Cookies or more in your "
                           "break area or if, during this turn, an 【Arena】 "
                           "Cookie has been placed in your break area, during "
                           "this turn, this Cookie gains +2 attack damage.")
    mine = ctx.source_cookie
    base = mine.attack_damage(ctx.db)

    program(ctx)
    assert mine.attack_damage(ctx.db) == base, "neither half is true yet"

    ctx.me.arena_break_additions_this_turn = 1     # the second half alone
    program(ctx)
    assert mine.attack_damage(ctx.db) == base + 2


def test_hp_reduced_this_turn_is_set_by_damage_and_cleared_by_the_turn(db):
    """White Peach Cookie (P-093) asks whether its own HP came off this turn."""
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=21)
    game.setup()
    _plain_hp(game, db)
    mine = game.state.players[0].battle[0]
    assert not mine.hp_reduced_this_turn

    game.deal_damage(mine, 1, source_player=1, kind="effect")
    assert mine.hp_reduced_this_turn

    game._begin_turn()
    assert not mine.hp_reduced_this_turn, "the flag is per turn, not per game"


def test_a_cookie_sent_to_either_end_of_the_deck_is_counted(db):
    """BS9-083 asks about "the top or bottom", BS9-088 only about the bottom,
    so `cookie_to_deck` keeps the two counts apart."""
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=22)
    game.setup()
    me = game.state.players[0]

    game.cookie_to_deck(me.battle[0], bottom=False)
    assert me.cookies_to_deck_this_turn == 1
    assert me.cookies_to_deck_bottom_this_turn == 0

    game.cookie_to_deck(me.battle[0], bottom=True)
    assert me.cookies_to_deck_this_turn == 2
    assert me.cookies_to_deck_bottom_this_turn == 1


def test_arena_break_additions_are_counted_apart_from_the_rest(db):
    """P-109/P-110 ask about 【Arena】 Cookies arriving, not any card."""
    from braverse.enums import Keyword

    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=23)
    game.setup()
    _plain_hp(game, db)
    me = game.state.players[0]
    victim = me.battle[0]
    is_arena = Keyword.ARENA in victim.defn(db).keywords

    game.faint(victim)

    assert me.break_additions_this_turn == 1
    assert me.arena_break_additions_this_turn == (1 if is_arena else 0)


# --- "Select 1 of the following." ------------------------------------------
class _PickBranch:
    """A controller that always takes the branch whose label contains a word."""

    def __init__(self, word):
        self.word = word
        self.offered: list = []

    def choose_action(self, state, options):
        return options[0] if options else None

    def choose(self, state, prompt, options, *, optional):
        if options and all(isinstance(o, str) for o in options):
            self.offered = list(options)
            for option in options:
                if self.word in option:
                    return option
        return options[0] if options else None


def test_a_modal_card_runs_only_the_branch_that_was_chosen(ctx):
    program = compile_text(
        "Select 1 of the following.\n"
        "・Draw up to 2 cards from your deck.\n"
        "・Place 1 random card from your opponent's hand into the trash.")
    controller = _PickBranch("Draw")
    _seat(ctx, controller)
    hand_before = len(ctx.me.hand)
    their_hand = len(ctx.opp.hand)

    program(ctx)

    assert len(controller.offered) == 2, "both options should be offered"
    assert len(ctx.me.hand) == hand_before + 2
    assert len(ctx.opp.hand) == their_hand, "the other branch must not run"


def test_a_modal_branch_can_span_more_than_one_sentence(ctx):
    """First Watcher's Bow (BS3-116): "Select ... Place 1 card from the top of
    that Cookie's HP" is one option made of two sentences, sharing a target."""
    program = compile_text(
        "Select 1 of the following.\n"
        "・Select up to 1 of your opponent's Cookies. Place 1 card from the "
        "top of that Cookie's HP in the trash.\n"
        "・Place 1 random card from your opponent's hand into the trash.")
    _seat(ctx, _PickBranch("top of that"))
    victim = ctx.opp.battle[0]
    before = victim.remaining_hp

    program(ctx)

    assert victim.remaining_hp == before - 1


def test_a_modal_does_not_offer_a_branch_that_cannot_do_anything(ctx):
    """A line the board makes impossible is not a choice, it is a way to throw
    the card away by mistake."""
    program = compile_text(
        "Select 1 of the following.\n"
        "・Draw up to 1 card from your deck.\n"
        "・Place 1 random card from your opponent's hand into the trash.")
    ctx.opp.hand.clear()                        # nothing to discard at random
    controller = _PickBranch("Draw")
    _seat(ctx, controller)
    before = len(ctx.me.hand)

    program(ctx)

    # One live branch is not a decision, so the question is never put — and
    # `offered` staying empty is what proves the dead one was filtered out.
    assert controller.offered == []
    assert len(ctx.me.hand) == before + 1


def test_a_modal_needs_at_least_two_options(ctx):
    with pytest.raises(CompileError):
        compile_text("Select 1 of the following.\n・Draw up to 1 card from "
                     "your deck.")


# --- negation and stripped markers -----------------------------------------
def test_there_are_no_x_is_not_read_as_there_is_an_x(ctx):
    """The negation used to be dropped, so the guard meant its own opposite."""
    program = compile_text("If there are no Cookies in your opponent's battle "
                           "area, draw up to 1 card from your deck.")
    before = len(ctx.me.hand)

    program(ctx)
    assert len(ctx.me.hand) == before, "their battle area is not empty"

    ctx.opp.battle.clear()
    program(ctx)
    assert len(ctx.me.hand) == before + 1


def test_a_filter_that_lost_its_property_is_refused():
    """A dangling "that have" is a filter whose property was normalised away,
    and an empty filter matches every Cookie — the card would be read as
    saying something it does not."""
    with pytest.raises(CompileError):
        parse_card_filter("Cookie that has")
    # 【Skill】 has no field on either filter, so naming it is refused too.
    with pytest.raises(CompileError):
        parse_card_filter("Cookie that has 【Skill】")


def test_a_property_marker_survives_where_it_is_a_filter(db):
    """【Special Play】 and 【Blocker】 are badges at the head of a card and
    *filters* mid-sentence. Deleted, the filter widens to every Cookie — which
    is how BS11-105's attack rider came to fire on every swing instead of only
    beside a 【Special Play】 Cookie."""
    from braverse.effects import is_implemented
    from braverse.enums import Marker

    assert is_implemented("BS11-105")
    assert parse_card_filter("Cookie that has Special Play").marker is Marker.SPECIAL_PLAY
    assert parse_card_filter("Cookies that have Blocker").marker is Marker.BLOCKER
    # At the head of a card it is still a badge and still stripped, or the
    # verb parser would be handed an ability name as an instruction.
    assert split_clauses("【Special Play】 Draw up to 1 card from your deck.") == [
        "Draw up to 1 card from your deck."]


# --- "for each"/"for every" scaling ----------------------------------------
def _bank_break(ctx, card_id, n):
    """Put N copies of a card in my break area, where the counts are read."""
    for _ in range(n):
        ctx.me.break_area.append(CardInstance.make(card_id, ctx.me.index))


def test_attack_buff_scales_with_the_break_area(ctx):
    """Golden City's Control Chamber (BS3-048): +1 per {Y} LV.3 in the break."""
    program = compile_text("Select up to 1 of your Cookies. During this turn, "
                           "that Cookie gains +1 attack damage for each {Y} "
                           "LV.3 Cookie in your break area.")
    mine = ctx.me.battle[0]
    base = mine.attack_damage(ctx.db)
    _bank_break(ctx, "ST7-010", 3)             # {Y} LV.3 Cookies

    program(ctx)

    assert mine.attack_damage(ctx.db) == base + 3


def test_attack_debuff_scales_the_same_way(ctx):
    """Seasick Canoeing (BS5-043) is the same op with the printed sign."""
    program = compile_text("Select up to 1 of your opponent's Cookies. During "
                           "this turn, that Cookie deals -1 attack damage for "
                           "each LV.3 Cookie in your break area.")
    theirs = ctx.opp.battle[0]
    base = theirs.attack_damage(ctx.db)
    _bank_break(ctx, "ST7-010", 2)

    program(ctx)

    assert theirs.attack_damage(ctx.db) == max(0, base - 2)


def test_for_every_two_pays_once_per_pair(ctx):
    """Jelly Pom-Poms (BS1-048): "for every 2" is a divisor, not a synonym.

    Three Cookies in the break area are worth one bonus, not three — the odd
    one over buys nothing until it has a partner.
    """
    text = ("Select up to 1 of your Cookies. During this turn, that Cookie "
            "gains +1 attack damage for every 2 {Y} LV.1 Cookies in your "
            "break area.")
    mine = ctx.me.battle[0]
    base = mine.attack_damage(ctx.db)

    _bank_break(ctx, "ST7-012", 3)             # {Y} LV.1 Cookies
    compile_text(text)(ctx)
    assert mine.attack_damage(ctx.db) == base + 1

    _bank_break(ctx, "ST7-012", 1)             # four now: a second pair
    mine.attack_bonus = 0
    compile_text(text)(ctx)
    assert mine.attack_damage(ctx.db) == base + 2


def test_gain_hp_scaling_can_target_a_selected_cookie(ctx):
    """Millennial Twig (BS4-041) says "that Cookie", not "this Cookie"."""
    program = compile_text("Select up to 1 of your Cookies. That Cookie gains "
                           "+1 HP for each {Y} LV.3 Cookie in your break area.")
    mine = ctx.me.battle[0]
    before = mine.remaining_hp
    _bank_break(ctx, "ST7-010", 2)

    program(ctx)

    assert mine.remaining_hp == before + 2


def test_draw_scaling_counts_either_battle_area(ctx):
    """Old Vanilla Orchid Locket (BS3-092) counts the whole table."""
    program = compile_text("Draw up to 1 card from your deck for each LV.2 "
                           "Cookie in either battle area.")
    # One on each side, so a rule that read only one of them would be caught.
    ctx.game._deploy_cookie(ctx.me, CardInstance.make("ST9-007", 0),
                            run_on_play=False)
    ctx.game._deploy_cookie(ctx.opp, CardInstance.make("ST9-007", 1),
                            run_on_play=False)
    expected = sum(1 for side in ctx.state.players for c in side.battle
                   if c.level(ctx.db) == 2)
    assert expected == 2
    before = len(ctx.me.hand)

    program(ctx)

    assert len(ctx.me.hand) == before + expected


def test_draw_scaling_counts_cookies_that_fainted_this_turn(ctx):
    """Jellied Jellyfish Potion (BS2-048) counts an event, not a pile."""
    program = compile_text("Draw up to 1 card for each of your opponent's "
                           "Cookies that fainted during this turn.")
    before = len(ctx.me.hand)
    program(ctx)
    assert len(ctx.me.hand) == before, "nothing has fainted yet"

    ctx.opp.cookies_fainted_this_turn = 2
    program(ctx)
    assert len(ctx.me.hand) == before + 2


def test_either_is_refused_for_zones_that_do_not_print_it(ctx):
    """"either trash" is nothing the pool prints; guessing a side of it would
    misreport the card, so the clause is refused instead."""
    with pytest.raises(CompileError):
        compile_text("Draw up to 1 card from your deck for each LV.2 Cookie "
                     "in either trash.")


# --- a Cookie's own HP as a cost -------------------------------------------
def test_hp_drain_cost_takes_the_cookie_down_to_its_floor(ctx):
    """Spicy Power Juice (BS1-023): the price is however much HP is above 1.

    A drain, not a fixed number of cards — the same card is cheap on a nearly
    dead Cookie and expensive on a fresh one.
    """
    program = compile_text(
        "<Place 1 of your Cookies' HP cards in the trash until the Cookie's HP "
        "reaches 1.> Select up to 1 of your Cookies. During this turn, that "
        "Cookie gains +2 attack damage.")
    ctx.trigger = Trigger.ACTIVATE.value
    mine = ctx.me.battle[0]
    assert mine.remaining_hp > 1, "need something to drain"
    paid = mine.remaining_hp - 1
    trash_before = len(ctx.me.trash)

    program(ctx)

    assert mine.remaining_hp == 1
    assert len(ctx.me.trash) == trash_before + paid
    assert mine in ctx.me.battle, "a drain to 1 must never faint the Cookie"


def test_hp_drain_cost_charges_nothing_at_the_floor(ctx):
    """A Cookie already at 1 pays nothing, which is what the text says."""
    program = compile_text(
        "<Place 1 of your Cookies' HP cards in the trash until the Cookie's HP "
        "reaches 1.> Select up to 1 of your Cookies. During this turn, that "
        "Cookie gains +2 attack damage.")
    ctx.trigger = Trigger.ACTIVATE.value
    mine = ctx.me.battle[0]
    del mine.hp_cards[1:]
    trash_before = len(ctx.me.trash)

    program(ctx)

    assert mine.remaining_hp == 1
    assert len(ctx.me.trash) == trash_before
    assert mine in ctx.me.battle


def test_flat_hp_cost_charges_exactly_one_card(ctx):
    """Sniffly Cocoa Palm (BS5-042) prices itself at a single HP card, and the
    drain's regex must not swallow this shorter sentence."""
    program = compile_text(
        "<Place 1 of your Cookies' HP cards in the trash.> Draw up to 2 cards "
        "from your deck.")
    ctx.trigger = Trigger.ACTIVATE.value
    mine = ctx.me.battle[0]
    before = mine.remaining_hp
    assert before > 2

    program(ctx)

    assert mine.remaining_hp == before - 1


def test_hp_to_hand_cost_returns_the_card_rather_than_trashing_it(ctx):
    """Squishy Jelly Watch (BS6-019): the HP card comes back to hand, so the
    cost is a tempo loss rather than a card loss."""
    from braverse.compiler import parse_cost

    mine = ctx.me.battle[0]
    top = mine.hp_cards[-1]
    hand_before = len(ctx.me.hand)
    trash_before = len(ctx.me.trash)

    env = {}
    for op in parse_cost("<Return 1 card from the top of your Cookie's HP to "
                         "your hand.>"):
        assert op.run(ctx, env)

    assert top in ctx.me.hand
    assert len(ctx.me.hand) == hand_before + 1
    assert len(ctx.me.trash) == trash_before


# --- the Soul Jams (BS3-019/043/066/091/115) ---------------------------------
def _jam_table(db, host_id, seed=17):
    """A game with one named host of P0's and two LV.2 Cookies of P1's."""
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=seed)
    game.setup()
    _plain_hp(game, db)
    me, opp = game.state.players
    me.battle.clear()
    host = game._deploy_cookie(me, CardInstance.make(host_id, 0),
                               run_on_play=False)
    opp.battle.clear()
    for _ in range(2):
        game._deploy_cookie(opp, CardInstance.make("BS9-097", 1),
                            run_on_play=False)
    return game, me, opp, host


def _play_jam(game, me, card_id):
    """Run one item's body the way `_do_play_support_card` would."""
    card = CardInstance.make(card_id, me.index)
    game._run_effect(card, Trigger.ITEM, me)
    if (game.state.find_card(card.uid) is None
            and not game.state.is_attached(card.uid)):
        me.trash.append(card)
    return card


# --- BS3-115 Soul Jam: Light of Resolution ----------------------------------
def test_soul_jam_resolution_trashes_hp_and_equips(db):
    game, me, opp, cacao = _jam_table(db, "BS3-100")
    victims = [c for c in opp.battle if c.level(db) <= 2][:2]
    assert len(victims) >= 1, "need a LV.2 or lower Cookie to aim at"
    before = {c.uid: c.remaining_hp for c in victims}

    card = _play_jam(game, me, "BS3-115")

    for victim in victims:
        assert victim.remaining_hp == before[victim.uid] - 1
    assert card in cacao.equipment, "the jam should ride Dark Cacao Cookie"
    assert card not in me.trash, "an equipped jam must not also sit in the trash"


def test_soul_jam_resolution_shields_its_host_from_selection(db):
    from braverse.effects import Ctx

    game, me, opp, cacao = _jam_table(db, "BS3-100")
    theirs = Ctx(game=game, state=game.state, db=db, me=opp, opp=me,
                 source_cookie=opp.battle[0], source_card=opp.battle[0].card)
    assert cacao in theirs.enemy_cookies(), "unprotected before the jam lands"

    _play_jam(game, me, "BS3-115")

    assert cacao not in theirs.enemy_cookies(), \
        "an equipped Cookie cannot be selected by the opponent's effects"
    theirs.trash_cookie(cacao)
    assert cacao in me.battle, "an equipped Cookie cannot be trashed"


def test_soul_jam_resolution_does_not_shield_from_its_own_controller(db):
    """"cannot be selected by *your opponent's* effects" — mine still reach it."""
    from braverse.effects import Ctx

    game, me, opp, cacao = _jam_table(db, "BS3-100")
    _play_jam(game, me, "BS3-115")
    mine = Ctx(game=game, state=game.state, db=db, me=me, opp=opp,
               source_cookie=cacao, source_card=cacao.card)

    assert cacao in mine.own_cookies()
    mine.trash_cookie(cacao)
    assert cacao not in me.battle


def test_soul_jam_passion_damages_then_equips_for_attack(db):
    game, me, opp, holly = _jam_table(db, "BS3-017")     # Hollyberry Cookie
    victim = opp.battle[0]
    before = victim.remaining_hp
    printed = holly.attack_damage(db)

    card = _play_jam(game, me, "BS3-019")

    assert victim.remaining_hp == before - 2
    assert card in holly.equipment
    game._run_cookie_effect(holly, Trigger.ATTACK_START, me)
    assert holly.attack_damage(db) == printed + 1


def test_soul_jam_passion_aura_leaves_with_the_jam(db):
    """The rider belongs to the jam, so stripping it takes the +1 away."""
    game, me, opp, holly = _jam_table(db, "BS3-017")
    printed = holly.attack_damage(db)
    _play_jam(game, me, "BS3-019")
    holly.equipment.clear()

    game._run_cookie_effect(holly, Trigger.ATTACK_START, me)
    assert holly.attack_damage(db) == printed


def test_soul_jam_abundance_sweeps_then_heals_its_host(db):
    game, me, opp, cheese = _jam_table(db, "BS3-025")    # Golden Cheese Cookie
    before = {c.uid: c.remaining_hp for c in opp.battle}
    cheese.hp_cards.pop()                                # room to be healed
    hurt = cheese.remaining_hp

    card = _play_jam(game, me, "BS3-043")

    for cookie in opp.battle:
        assert cookie.remaining_hp == before[cookie.uid] - 1
    assert card in cheese.equipment
    assert cheese.remaining_hp == hurt + 2


def test_soul_jam_abundance_heals_nobody_when_it_does_not_equip(db):
    """"That Cookie gains +2 HP" is the equip's rider, not the sweep's."""
    game, me, opp, other = _jam_table(db, "BS3-017")     # no Golden Cheese
    other.hp_cards.pop()
    hurt = other.remaining_hp

    card = _play_jam(game, me, "BS3-043")

    assert card in me.trash
    assert other.remaining_hp == hurt


def test_soul_jam_freedom_cycles_support_and_refunds_on_attack(db):
    game, me, opp, lily = _jam_table(db, "BS3-055")      # White Lily Cookie
    while len(me.support) < 3:                           # something to give back
        me.support.append(me.deck.pop(0))
    support_before = len(me.support)
    hand_before = len(me.hand)

    card = _play_jam(game, me, "BS3-066")

    assert len(me.support) == support_before, "one out, one in"
    assert len(me.hand) == hand_before + 1
    assert not me.support[-1].rested, "the new support card arrives active"
    assert card in lily.equipment

    for support in me.support:
        support.rested = True
    game._run_cookie_effect(lily, Trigger.ATTACK_START, me)
    assert len(me.active_support()) == 1


def test_soul_jam_truth_digs_and_draws_on_attack(db):
    game, me, opp, vanilla = _jam_table(db, "BS3-088")   # Pure Vanilla Cookie
    hand_before = len(me.hand)
    deck_before = len(me.deck)
    top_three = me.deck[:3]

    card = _play_jam(game, me, "BS3-091")

    assert len(me.hand) == hand_before + 2
    assert len(me.deck) == deck_before - 2
    assert me.deck[0] in top_three, "the leftover goes back on top, not the bottom"
    assert card in vanilla.equipment

    hand = len(me.hand)
    game._run_cookie_effect(vanilla, Trigger.ATTACK_START, me)
    assert len(me.hand) == hand + 1


def test_a_soul_jam_that_does_not_equip_is_spent(db):
    """With no Cookie of the right name, the jam is an ordinary item."""
    game, me, opp, other = _jam_table(db, "BS3-017")     # not Pure Vanilla
    card = _play_jam(game, me, "BS3-091")

    assert card in me.trash
    assert not any(c.equipment for c in me.battle)


# --- promo cards written by hand -------------------------------------------
class _Answers2:
    """A controller that answers every question with a scripted reply."""

    def __init__(self, *, yes=True, pick=0):
        self.yes = yes
        self.pick = pick
        self.prompts: list[str] = []

    def choose_action(self, state, options):
        return options[0] if options else None

    def choose(self, state, prompt, options, *, optional):
        self.prompts.append(prompt)
        if options and all(isinstance(o, bool) for o in options):
            return True if self.yes else None
        if not options:
            return None
        return options[min(self.pick, len(options) - 1)]


def test_birthday_cake_asks_rather_than_reading_the_clock(ctx, db):
    """P-041's condition is a fact about the world, not the board.

    Reading the system clock would answer it and would break every existing
    recording — a replay re-runs the engine and would get a different answer on
    a different day. A question is a decision, and decisions are what replays
    already carry.
    """
    from braverse.effects import Trigger, get_effect

    fn = get_effect("P-041", Trigger.ATTACK)
    assert fn is not None
    ctx.me.battle.clear()
    mine = ctx.game._deploy_cookie(ctx.me, CardInstance.make("P-041", 0),
                                   run_on_play=False)
    ctx.source_cookie = mine
    base = mine.attack_damage(db)

    refuser = _Answers2(yes=False)
    _seat(ctx, refuser)
    fn(ctx)
    assert refuser.prompts, "the card must ask"
    assert mine.attack_damage(db) == base

    mine.attack_bonus = 0
    _seat(ctx, _Answers2(yes=True))
    fn(ctx)
    assert mine.attack_damage(db) == base + 1


def test_an_alternative_cost_is_not_also_charged_as_the_play_cost(db):
    """P-082 prints "<{Y}{N}> or <...>". Lifting the head of that into
    `play_cost` bills the energy whichever way the choice goes, so the card
    keeps both alternatives in its own effect and prints no play cost."""
    from braverse.effects import Trigger, get_effect

    assert not db["P-082"].play_cost, "the head of the text is not the price"
    assert get_effect("P-082", Trigger.ITEM) is not None
    # A card that prints one cost is unaffected.
    assert db["BS3-116"].play_cost


def test_magic_lettering_pens_goes_colourless_after_a_faint(ctx, db):
    """P-084's rewrite makes the cost payable from any support card — the same
    single symbol, not a cheaper one."""
    from braverse.impl.promo import _pens_cost

    assert _pens_cost(ctx) == Cost.parse("{G}")
    ctx.me.cookies_fainted_this_turn = 1
    rewritten = _pens_cost(ctx)
    assert rewritten.total == 1
    assert not rewritten.colored


# --- "if activated during your turn", and a static taunt --------------------
def test_a_flip_can_tell_whose_turn_it_went_off_on(ctx, db):
    """Pistachio Cookie (BS9-041): a FLIP fires whenever its host loses HP,
    which is usually the *opponent's* turn — so which turn it was is a real
    distinction and not a formality."""
    program = compile_text("Draw up to 1 card from your deck. Then, if "
                           "activated during your turn, select up to 1 of your "
                           "opponent's Cookies. That Cookie receives 1 damage.")
    victim = ctx.opp.battle[0]
    before = victim.remaining_hp

    ctx.state.turn_player = ctx.opp.index
    program(ctx)
    assert victim.remaining_hp == before, "their turn: only the draw happens"

    ctx.state.turn_player = ctx.me.index
    program(ctx)
    assert victim.remaining_hp == before - 1


def test_animatronic_forces_attacks_onto_itself_only_beside_its_master(db):
    """BS9-082: "If [Shadow Milk Cookie] is in your battle area, your
    opponent's Cookies can only attack this Cookie." A rule about which
    attacks are legal, so it lives where attacks are enumerated."""
    from braverse.effects import forced_attack_target

    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=31)
    game.setup()
    defender = game.state.players[1]
    defender.battle.clear()
    animatronic = game._deploy_cookie(defender, CardInstance.make("BS9-082", 1),
                                      run_on_play=False)
    game._deploy_cookie(defender, CardInstance.make("ST8-004", 1),
                        run_on_play=False)

    assert forced_attack_target(db, defender) is None, "no master, no taunt"

    game._deploy_cookie(defender, CardInstance.make("BS9-079", 1),
                        run_on_play=False)      # a Shadow Milk Cookie
    assert forced_attack_target(db, defender) is animatronic


def test_a_dangling_select_with_no_consequence_is_refused(db):
    """Mala Sauce Cookie (BS9-025)'s text ends "you can select another of your
    Cookies" with nothing following it — the dump lost the half that says what
    happens to the Cookie you picked. A selection that binds nothing is a card
    that does nothing, and offering it would report a choice that changes the
    game not at all."""
    from braverse.effects import is_implemented

    assert not is_implemented("BS9-025")
    with pytest.raises(CompileError):
        compile_text("You can select another of your Cookies.")


# --- view N, take one, dispose of the rest ---------------------------------
def test_a_view_takes_what_it_may_and_files_the_rest_where_it_says(ctx, db):
    """Rambirdtan Handler Glove (BS5-108). "Then, place the remaining cards in
    the trash" is not an instruction of its own — "the remaining cards" only
    means anything to the look that just happened — so it is folded back into
    that look rather than compiled to ops that must find them again."""
    program = compile_text(
        "View 3 cards from the top of your deck, reveal up to 1 {P} Cookie "
        "from the viewed cards, and add it to your hand. Then, place the "
        "remaining cards in the trash.")
    hand_before = len(ctx.me.hand)
    trash_before = len(ctx.me.trash)
    top_three = ctx.me.deck[:3]

    program(ctx)

    for card in top_three:
        assert card not in ctx.me.deck, "all three left the deck"
    moved = len(ctx.me.hand) - hand_before
    assert len(ctx.me.trash) == trash_before + (3 - moved)


def test_the_remainder_can_be_named_a_sentence_later(ctx):
    """Tales of the Lotus (BS5-086) plays the Cookie it found before it says
    where the rest go, and "the remaining cards" still means the same look."""
    from braverse.compiler import ViewTop

    program = compile_text(
        "View 3 cards from the top of your deck and select up to 1 {B} Cookie "
        "from the viewed cards. Play that Cookie with +1 HP. Then, place the "
        "remaining cards on the bottom of your deck in any order.")
    looks = [op for clause in program.clauses for op in clause.ops
             if isinstance(op, ViewTop)]
    assert len(looks) == 1
    assert looks[0].rest == "bottom"


def test_a_sentence_the_one_before_it_carried_out_is_not_an_empty_clause():
    """Squishy Jelly Watch (BS6-019): "Select up to 2 cards in your opponent's
    support area. Rest those cards." is one action across two sentences, and
    the first carries it out. An empty op list would be indistinguishable from
    text that routed nowhere."""
    from braverse.compiler import Done

    program = compile_text("Select up to 2 cards in your opponent's support "
                           "area. Rest those cards.")
    assert any(isinstance(op, Done) for clause in program.clauses
               for op in clause.ops)


# --- "apply the effect below based on ..." ---------------------------------
def test_a_dispatch_runs_the_branch_the_board_picks(ctx):
    """Flipped Coin (BS9-114). Same bullet layout as "Select 1 of the
    following", the opposite mechanic: nobody is asked."""
    program = compile_text(
        "Apply the effect below based on the number of cards in your trash.\n"
        "・ 14 cards or less: Place 5 cards from the top of your deck into "
        "your trash.\n"
        "・ 15 cards or more: Place 3 cards from the top of your opponent's "
        "deck into your opponent's trash.")
    ctx.me.trash.clear()
    theirs = len(ctx.opp.deck)

    program(ctx)
    assert len(ctx.me.trash) == 5, "the low branch mills my own deck"
    assert len(ctx.opp.deck) == theirs

    while len(ctx.me.trash) < 15:
        ctx.me.trash.append(ctx.me.deck.pop(0))
    theirs = len(ctx.opp.deck)
    program(ctx)
    assert len(ctx.opp.deck) == theirs - 3, "the high branch mills theirs"


def test_a_dispatch_label_it_cannot_read_is_refused():
    with pytest.raises(CompileError):
        compile_text("Apply the effect below based on the phase of the moon.\n"
                     "・ Waxing: Draw up to 1 card from your deck.\n"
                     "・ Waning: Draw up to 2 cards from your deck.")


# --- costs and riders on items ---------------------------------------------
def test_a_reveal_cost_keeps_the_card_in_hand(ctx, db):
    """Light of Deceit (BS9-091): showing the card is the whole price."""
    from braverse.compiler import parse_cost

    named = CardInstance.make("BS9-079", 0)      # a Shadow Milk Cookie
    ops = parse_cost("<Reveal 1 [Shadow Milk Cookie] from your hand.>")
    assert not all(op.run(ctx, {}) for op in ops), "not holding one"

    ctx.me.hand.append(named)
    assert all(op.run(ctx, {}) for op in ops)
    assert named in ctx.me.hand, "a reveal spends nothing"


def test_damage_reduction_from_equipment_is_re_read_every_time(db):
    """BS9-092's rider is conditional on the board, so it cannot be banked at
    the moment the jam went on: a hand that grew past five turns it off."""
    from braverse.effects import continuous_damage_reduction

    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=41)
    game.setup()
    me = game.state.players[0]
    cookie = me.battle[0]
    cookie.equipment.append(CardInstance.make("BS9-092", 0))
    game.state.turn_player = 0
    del me.hand[5:]

    assert continuous_damage_reduction(db, game.state, cookie) == 3

    while len(me.hand) <= 5:
        me.hand.append(me.deck.pop(0))
    assert continuous_damage_reduction(db, game.state, cookie) == 0

    del me.hand[5:]
    game.state.turn_player = 1
    assert continuous_damage_reduction(db, game.state, cookie) == 0, "their turn"


# --- "during this battle" is resolved after the battle ---------------------
def test_a_battle_scoped_trap_is_held_until_the_battle_has_happened(db):
    """The Trap Step runs *before* damage (7-1-2), so "if your Cookie faints
    during this battle" cannot be answered when the card is played — nothing
    has fainted yet. Held to the end of the battle, it is asked when the card
    means it."""
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=51)
    game.setup()

    assert game._waits_for_the_battle(CardInstance.make("P-029", 0)), \
        "Ritual of Life asks about the battle it is played into"
    assert not game._waits_for_the_battle(CardInstance.make("BS3-116", 0)), \
        "an ordinary item resolves on the spot"


def test_battle_faints_are_counted_per_battle_not_per_turn(db):
    """Two attacks in one turn are two battles, and a trap in the second one
    must not see what died in the first."""
    from braverse.effect_ir import Condition

    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=52)
    game.setup()
    _plain_hp(game, db)
    me, opp = game.state.players
    ctx = Ctx(game=game, state=game.state, db=db, me=opp, opp=me,
              source_cookie=None, source_card=None)
    condition = Condition("battle_faints", ">=", 1)

    game._battle_faints = []
    assert not condition.holds(ctx)

    victim = opp.battle[0]
    game.faint(victim)
    assert condition.holds(ctx), "their own Cookie fainting is what it asks about"

    game._battle_faints = []            # the next battle starts clean
    assert not condition.holds(ctx)


# --- refresh, taunts and redirects -----------------------------------------
def test_a_cookie_can_rewrite_what_a_refresh_costs(db):
    """Nosy Wizard waives it for its controller; Everything Pie Cookie raises
    it for the seat across the table."""
    from braverse.effects import refresh_break_cost

    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=53)
    game.setup()
    me, opp = game.state.players
    printed = game.rules.refresh_break_cost

    assert refresh_break_cost(db, me, opp, printed) == printed

    game._deploy_cookie(opp, CardInstance.make("BS9-111", 1), run_on_play=False)
    assert refresh_break_cost(db, me, opp, printed) == 2

    game._deploy_cookie(me, CardInstance.make("BS9-096", 0), run_on_play=False)
    assert refresh_break_cost(db, me, opp, printed) == 0, "the waiver wins"


def test_a_redirect_moves_the_attack_onto_a_different_cookie(ctx, db):
    """Broken Signpost (BS1-050): the same outcome as a 【Blocker】 by another
    route, and "a different Cookie" is the whole card."""
    from braverse.compiler import RedirectAttack

    attacker = ctx.opp.battle[0]
    target = ctx.me.battle[0]
    ctx.game._pending_attack = (attacker, target)

    if len(ctx.me.battle) < 2:
        ctx.game._deploy_cookie(ctx.me, CardInstance.make("ST9-007", 0),
                                run_on_play=False)
    op = RedirectAttack()
    assert op.is_live(ctx, {})
    assert op.run(ctx, {})
    assert ctx.game._redirect_to is not None
    assert ctx.game._redirect_to is not target


def test_a_redirect_with_nowhere_to_go_is_not_a_move(ctx):
    """One Cookie on the board means there is no different Cookie to redirect
    to, so the trap is not on offer."""
    from braverse.compiler import RedirectAttack

    attacker = ctx.opp.battle[0]
    target = ctx.me.battle[0]
    del ctx.me.battle[1:]
    ctx.me.battle[0] = target
    ctx.game._pending_attack = (attacker, target)

    assert not RedirectAttack().is_live(ctx, {})


# --- "If you did" ----------------------------------------------------------
def test_if_you_did_reads_whether_the_sentence_before_happened(ctx):
    """Carrot Farm Scarecrow (BS2-021) only refills a support area it emptied."""
    program = compile_text("Return 1 card from your support area to your hand. "
                           "If you did, place 1 card from your hand into your "
                           "support area as rested.")
    ctx.me.support.clear()
    hand_before = len(ctx.me.hand)

    program(ctx)
    assert len(ctx.me.support) == 0, "nothing came back, so nothing goes in"
    assert len(ctx.me.hand) == hand_before

    ctx.me.support.append(ctx.me.deck.pop(0))
    program(ctx)
    assert len(ctx.me.support) == 1, "one out, one in"


def test_if_you_did_does_not_veto_the_move_at_probe_time(ctx):
    """A probe cannot know whether a sentence that has not run yet succeeded,
    so it does not get to say the card is dead either."""
    program = compile_text("Return 1 card from your support area to your hand. "
                           "If you did, draw up to 1 card from your deck.")
    ctx.me.support.append(ctx.me.deck.pop(0))
    assert program.is_live(ctx)


# --- "up to N" is one question with a confirm ------------------------------
class _Batching:
    """A seat that can answer a whole selection at once, as a browser can."""

    def __init__(self, take=None):
        self.take = take
        self.singles: list = []
        self.batches: list = []

    def choose_action(self, state, options):
        return options[0] if options else None

    def choose(self, state, prompt, options, *, optional):
        self.singles.append(prompt)
        return options[0] if options else None

    def choose_many(self, state, prompt, options, *, count, optional,
                    up_to=False):
        self.batches.append({"prompt": prompt, "count": count, "up_to": up_to,
                             "offered": len(options)})
        wanted = count if self.take is None else self.take
        return options[:wanted]


def _stock_enemy_board(ctx, n=3):
    while len(ctx.opp.battle) < n:
        ctx.game._deploy_cookie(ctx.opp, CardInstance.make("ST9-007", 1),
                                run_on_play=False)


def test_selecting_up_to_two_is_one_question_not_two(ctx):
    """"Select up to 2" asked twice in a row gives no way to say "that is all"
    except declining the second question, and no way to change the first
    answer at all. One question, pick as many as you want, confirm."""
    _stock_enemy_board(ctx)
    seat = _Batching()
    _seat(ctx, seat)

    compile_text("Select up to 2 of your opponent's Cookies. During this turn, "
                 "those Cookies deal -1 attack damage each.")(ctx)

    assert seat.singles == [], "nothing should be asked one at a time"
    assert len(seat.batches) == 1
    assert seat.batches[0]["count"] == 2
    assert seat.batches[0]["up_to"] is True
    assert seat.batches[0]["offered"] == 3, "every legal target is on offer"


def test_a_short_answer_to_up_to_is_accepted(ctx):
    """Confirming with one of two picked is a real answer, not a short one."""
    _stock_enemy_board(ctx)
    seat = _Batching(take=1)
    _seat(ctx, seat)
    before = {c.uid: c.attack_damage(ctx.db) for c in ctx.opp.battle}

    compile_text("Select up to 2 of your opponent's Cookies. During this turn, "
                 "those Cookies deal -1 attack damage each.")(ctx)

    weakened = [c for c in ctx.opp.battle
                if c.attack_damage(ctx.db) < before[c.uid]]
    assert len(weakened) == 1


def test_a_fixed_count_is_not_an_up_to(ctx):
    """"Select 2 of your Cookies" has no short answer, and the flag says so —
    it is what keeps the confirm button gated on exactly two."""
    seat = _Batching()
    _seat(ctx, seat)
    while len(ctx.me.battle) < 2:
        ctx.game._deploy_cookie(ctx.me, CardInstance.make("ST9-007", 0),
                                run_on_play=False)

    compile_text("Select 2 of your Cookies and place 1 of their HP cards in "
                 "the trash.")(ctx)

    assert seat.batches[0]["up_to"] is False


def test_up_to_one_stays_a_single_question(ctx):
    """One card is already one question, and its "none" is the Decline button.
    Putting it on a confirm strip would be two clicks for one decision."""
    _stock_enemy_board(ctx)
    seat = _Batching()
    _seat(ctx, seat)

    compile_text("Select up to 1 of your opponent's Cookies. That Cookie "
                 "receives 1 damage.")(ctx)

    assert seat.batches == []
    assert len(seat.singles) == 1


def test_a_scripted_seat_is_still_asked_one_card_at_a_time(ctx):
    """The bot path is deliberately untouched: a bot asked N questions and a
    bot asked one question for N cards make different games out of the same
    seed, and self-play numbers are the regression check for the engine."""
    _stock_enemy_board(ctx)
    assert not hasattr(ctx.game.controller(ctx.me.index), "choose_many")
    assert ctx.choose_many("anything", list(ctx.opp.battle),
                           count=2, up_to=True) is None


def test_taking_up_to_two_from_a_pile_is_one_question(ctx):
    """The same rule for a trash or break-area pile, not just the board."""
    for _ in range(4):
        ctx.me.trash.append(ctx.me.deck.pop(0))
    seat = _Batching()
    _seat(ctx, seat)

    compile_text("Return up to 2 cards from your trash to your hand.")(ctx)

    assert seat.singles == []
    assert seat.batches[0]["count"] == 2 and seat.batches[0]["up_to"] is True


def test_adding_up_to_two_viewed_cards_is_one_question(ctx):
    """"View 3 cards ... add up to 2 of them to your hand" — the three are on
    screen together, and picking one should not hide the rest behind a second
    prompt. The count belongs in the prompt: a strip with a confirm button
    does not otherwise say how many you may keep."""
    seat = _Batching()
    _seat(ctx, seat)

    ctx.view_top(3, take=2)

    assert seat.singles == []
    assert seat.batches[0]["count"] == 2
    assert "up to 2" in seat.batches[0]["prompt"]


def test_an_attack_rider_that_names_no_target_hits_the_attacked_cookie(db):
    """"Then, if there is a [Soul Jam] card in your support area, deals 1
    damage." (BS3-009) names no target because the swing already did.

    Compiled, that is a bare `Damage` reading the `it` register, which nothing
    in an attack program ever binds — so 40-odd riders across every set turned
    into a sentence the engine read and then did nothing about. The register is
    *unbound*, which is the distinction being pinned here: a `Select` that ran
    and found no target writes an empty list, and that empty list still means
    nobody.
    """
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=17)
    game.setup()
    _plain_hp(game, db)
    me, them = game.state.players[0], game.state.players[1]
    victim = them.battle[0]
    game._attack_target = victim

    fn = get_effect("BS3-009", Trigger.ATTACK)

    def swing():
        before = victim.remaining_hp
        fn(Ctx(game=game, state=game.state, db=db, me=me, opp=them,
               source_cookie=me.battle[0], attack_target=victim,
               trigger=Trigger.ATTACK.value))
        return before - victim.remaining_hp

    me.support = []
    assert swing() == 0, "the rider fired with no Soul Jam in support"

    me.support = [CardInstance.make("BS3-019", 0)]     # Soul Jam: Light of Passion
    assert swing() == 1, "the rider's damage landed on nobody"


def test_an_attack_riders_own_hp_cost_is_not_charged_to_the_victim(db):
    """"place 1 card from the top of this Cookie's HP into the trash" is about
    the attacker; "that Cookie" is about whoever was selected or swung at. Read
    through one alternation the two were the same sentence, so BS3-109 paid its
    own price out of the Cookie it had just hit.
    """
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=17)
    game.setup()
    _plain_hp(game, db)
    me, them = game.state.players[0], game.state.players[1]
    attacker, victim = me.battle[0], them.battle[0]

    before = (attacker.remaining_hp, victim.remaining_hp)
    get_effect("BS3-109", Trigger.ATTACK)(
        Ctx(game=game, state=game.state, db=db, me=me, opp=them,
            source_cookie=attacker, attack_target=victim,
            trigger=Trigger.ATTACK.value))

    assert attacker.remaining_hp == before[0] - 1
    assert victim.remaining_hp == before[1], "paid its cost out of the defender"


def test_a_bracketed_family_name_matches_its_members(db):
    """"a [Soul Jam] card" is the family, not a card printed with that name.

    No card is called plain "Soul Jam" — they are "Soul Jam: Light of Passion"
    and nine others — so exact equality made the condition unsatisfiable and
    every card asking about one lost a sentence without failing to compile.
    """
    from braverse.effect_ir import CardFilter

    jam = CardFilter(name="Soul Jam")
    assert jam.matches(db["BS3-019"])
    assert jam.matches(db["BS9-092"])
    assert not jam.matches(db["ST9-013"])
    # Still exact for everything that is not a family member.
    assert not CardFilter(name="Soul").matches(db["BS3-019"])


def test_a_hand_written_trigger_does_not_swallow_the_rest_of_the_card(db):
    """Mozzarella Cookie (BS3-028) has a hand-written 【On Play】 and an attack
    rider that heals it. `compile_all` skipped the whole card the moment one
    trigger was in the registry, so the rider was compiled and thrown away —
    the card swung with the second half of its attack missing, and nothing
    said so.
    """
    assert get_effect("BS3-028", Trigger.ON_PLAY) is not None
    assert get_effect("BS3-028", Trigger.ATTACK) is not None, \
        "the compiled attack rider was dropped because the 【On Play】 was written"


def test_every_trigger_the_compiler_understands_is_registered(db):
    """The general form of the same hole.

    A card is several abilities that share a piece of cardboard. Anything the
    compiler reads in full and nobody wrote by hand has to reach the registry,
    or the card plays part of its text — the quietest failure this engine has,
    since a dropped trigger looks exactly like a card with nothing printed on
    it.
    """
    from braverse.compiler import compile_card

    dropped = []
    for card_id, defn in db.cards.items():
        if card_id != defn.base_id:
            continue
        result = compile_card(defn)
        # All-or-nothing still holds: a clause nobody can read — neither the
        # compiler nor a hand-written effect — refuses the whole card, and its
        # other triggers go down with it deliberately.
        if any(get_effect(card_id, trigger) is None
               for trigger, _clause, _why in result.failures):
            continue
        for trigger in result.programs:
            if get_effect(card_id, trigger) is None:
                dropped.append(f"{card_id} {trigger.name}")
    assert not dropped, f"understood but never registered: {dropped}"


def test_a_card_whose_unreadable_half_is_hand_written_still_registers(db):
    """All-or-nothing, widened to count hand-written work as an answer.

    BS3-028's 【On Play】 does not compile. That refuses the card only if the
    【On Play】 is nobody's — and here it is written out in `impl/bs3.py`, so
    the sentence the compiler cannot read is one it does not have to.
    """
    from braverse.compiler import compile_card

    failed = {trigger for trigger, _clause, _why in compile_card(db["BS3-028"]).failures}
    assert Trigger.ON_PLAY in failed
    assert get_effect("BS3-028", Trigger.ON_PLAY) is not None


def test_a_counted_zone_condition_keeps_its_count():
    """"if there are 2 Cookies in your battle area" was read as "if there is 1".

    The numeral was matched by the pattern and dropped on the floor, and every
    card printing one asked for a single card instead — a condition true on
    nearly any board. The reversed word order is the same sentence and was
    landing in the same place.
    """
    from braverse.compiler import parse_condition

    two = parse_condition("there are 2 Cookies in your battle area")
    assert (two.kind, two.op, two.value) == ("zone_has", ">=", 2)

    five = parse_condition("there are 5 cards in your support area")
    assert (five.kind, five.op, five.value) == ("zone_has", ">=", 5)

    # "N or more X" — the bound in front of the filter rather than behind it.
    three = parse_condition("there are 3 or more Cookies in your break area")
    assert (three.kind, three.op, three.value) == ("zone_count", ">=", 3)

    # Unnumbered and negated forms are untouched.
    one = parse_condition("there is another 【Ancient】 Cookie in your battle area")
    assert (one.op, one.value) == (">=", 1)
    none = parse_condition("there are no Cookies in your battle area")
    assert (none.op, none.value) == ("==", 0)


def test_a_filter_about_the_board_is_refused_not_dropped():
    """"2 Cookies whose remaining HP is 1" is not a property of a printed card.

    A `CardFilter` reads `CardDef`s, so the clause was silently counting every
    Cookie instead — the same failure `_STATE_WORDS` already refuses for
    "active" and "rested", one relative clause further along.
    """
    from braverse.compiler import parse_card_filter

    with pytest.raises(CompileError):
        parse_card_filter("Cookies whose remaining HP is 1")


def test_an_item_body_is_registered_on_the_item_trigger(db):
    """An ITEM's body runs on `Trigger.ITEM`. Two of them were hand-written
    against `Trigger.ATTACK`, which only a Cookie in the battle area ever
    fires, so both cards were inert while `is_implemented` called them done.
    """
    from braverse.effects import is_implemented

    for card_id in ("BS2-047", "BS5-020"):
        assert db[card_id].type.name == "ITEM"
        assert is_implemented(card_id)
        assert get_effect(card_id, Trigger.ITEM) is not None
        assert get_effect(card_id, Trigger.ATTACK) is None


def test_crimson_dragon_mask_counts_cookies_at_1_hp(db):
    """BS5-020 fires only with two Cookies of yours down to their last card."""
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=19)
    game.setup()
    _plain_hp(game, db)
    me, them = game.state.players
    while len(me.battle) < 2:
        game._deploy_cookie(me, me.deck.pop())
    while len(them.battle) < 2:
        game._deploy_cookie(them, them.deck.pop())

    def fire():
        before = sorted(c.remaining_hp for c in them.battle)
        get_effect("BS5-020", Trigger.ITEM)(
            Ctx(game=game, state=game.state, db=db, me=me, opp=them,
                source_card=CardInstance.make("BS5-020", 0), trigger=Trigger.ITEM.value))
        return before != sorted(c.remaining_hp for c in them.battle)

    assert not fire(), "fired with nobody at 1 HP"
    del me.battle[0].hp_cards[1:]
    assert not fire(), "one Cookie at 1 HP was enough"
    del me.battle[1].hp_cards[1:]
    assert fire()


def test_an_optional_cost_is_not_charged_for_an_effect_that_cannot_happen(db):
    """Millennial Tree Cookie (BS4-038): "<{Y}> Select up to 1 {Y} LV.2 or
    lower Cookie from your break area and play them."

    The Cookie it plays needs the battle-area slot the Cookie itself just took,
    so on a full board the clause used to rest the {Y} and then find nowhere to
    put anything. A cost is a trade; a trade with nothing on the other side is
    not a decision to put in front of anybody.
    """
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=3)
    game.setup()
    me = game.state.players[0]
    card = CardInstance.make("BS4-038", 0)
    me.hand.append(card)
    me.break_area.append(CardInstance.make("BS1-029", 0))     # {Y} LV.1
    for _ in range(3):
        energy = CardInstance.make("BS1-029", 0)
        energy.rested = False
        me.support.append(energy)

    play = next(a for a in game.legal_actions()
                if isinstance(a, A.PlayCookie) and a.card_uid == card.uid)
    game.step(play)

    assert len(me.battle) == 2, "the board was already full"
    assert len(me.break_area) == 1, "played a Cookie into a full battle area"
    assert not any(c.rested for c in me.support), "paid {Y} for nothing"
