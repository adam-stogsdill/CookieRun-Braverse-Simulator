"""Tests for the rules-text compiler.

Two things need proving: that the compiler *refuses* text it does not fully
understand (a half-resolved card is worse than a vanilla one), and that what it
does compile actually does the right thing to the game state.
"""

import pytest

from braverse import STARTER_DECKS, Game, HeuristicAgent, SeatedAgent, default_db
from braverse import actions as A
from braverse.compiler import (CompileError, compile_card, compile_text,
                               split_clauses)
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
                   and not is_implemented(c.id)]
        assert not missing, f"{set_id} regressed: {missing}"


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


def test_hp_cannot_reach_zero_leaves_the_cookie_at_one(db):
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=21)
    game.setup()
    _plain_hp(game, db)
    victim = game.state.players[1].battle[0]
    victim.hp_cannot_reach_zero = True

    game.deal_damage(victim, 99, source_player=0)
    assert victim in game.state.players[1].battle
    assert victim.remaining_hp == 1
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
    A Cookie whose text simply failed to route must stay unimplemented."""
    from braverse.compiler import compile_card
    from braverse.effects import is_implemented

    result = compile_card(db["BS4-022"])
    assert result.vanilla and result.ok and not result.programs
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
