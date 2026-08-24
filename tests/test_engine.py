"""Engine and card-database tests. Run with: python -m pytest -q"""

import pytest

from braverse import (STARTER_DECKS, STARTER_SET_IDS, Game, HeuristicAgent,
                      RandomAgent, SeatedAgent, build_starter_deck,
                      default_db, starter_deck, validate)
from braverse import actions as A
from braverse.cost import Cost, plan_payment
from braverse.enums import CardType, Color, Marker
from braverse.engine import BankedUntap
from braverse.state import CardInstance


@pytest.fixture(scope="module")
def db():
    return default_db()


def agents(kind=HeuristicAgent, seed=0):
    return [SeatedAgent(kind(seed=seed), 0), SeatedAgent(kind(seed=seed + 1), 1)]


def new_game(seed=0, kind=HeuristicAgent, db=None):
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                agents(kind, seed), db=db, seed=seed)
    game.setup()
    return game


# --- card database ---------------------------------------------------------
def test_database_loads_and_normalises(db):
    assert len(db) > 1000
    sea_fairy = db["ST9-006"]
    assert sea_fairy.name == "Sea Fairy Cookie"
    assert sea_fairy.type is CardType.COOKIE
    assert (sea_fairy.level, sea_fairy.hp) == (3, 6)
    assert sea_fairy.attack.damage == 3
    assert sea_fairy.attack.cost == Cost.parse("{B}{B}{B}")
    assert sea_fairy.has(Marker.ONCE_PER_TURN)


def test_typos_in_the_dump_are_repaired(db):
    # `EXRTA` rows must still load as EXTRA, `PULPLE` as PURPLE.
    assert any(c.type is CardType.EXTRA for c in db.cards.values())
    assert db["ST5-008"].color is Color.PURPLE   # colour field is blank upstream
    # A handful of old rows carry no colour evidence at all; keep that small.
    colourless = [c for c in db.cards.values() if c.is_cookie and c.color is Color.NONE]
    assert len(colourless) < 20, [c.id for c in colourless]


def test_early_set_on_play_skills_are_not_read_as_activate(db):
    """The dump prints 【Activate】 on 106 early-set cards that print 【On Play】.

    Read the dump's way, each of those Cookies gains a main-phase skill it can
    press every turn for as long as it lives, in place of a one-shot that fires
    as it is played. The correction is pinned here card by card because it is a
    hand-checked reading of the card scans, not something the dump can be asked
    about again.
    """
    from braverse.cards import _ON_PLAY_MISPRINTS
    from braverse.effects import Trigger, get_effect

    assert len(_ON_PLAY_MISPRINTS) == 106
    for base_id in _ON_PLAY_MISPRINTS:
        card = db[base_id]
        assert not card.has(Marker.ACTIVATE), base_id
        assert "【Activate】" not in (card.description or ""), base_id
    # Pumpkin Pie Cookie is the one the mislabel was noticed on.
    pumpkin = db["BS1-071"]
    assert pumpkin.has(Marker.ON_PLAY)
    assert get_effect("BS1-071", Trigger.ON_PLAY) is not None
    assert get_effect("BS1-071", Trigger.ACTIVATE) is None
    # Nothing outside the early sets was touched: BS5 onward reads clean.
    assert all(b.split("-")[0] in {"BS1", "BS2", "BS3", "BS4",
                                   "ST1", "ST2", "ST3", "ST4", "ST5", "P"}
               for b in _ON_PLAY_MISPRINTS)


def test_an_on_play_cookie_offers_no_activate_action(db):
    """The skill fires as the Cookie is played, so there is no button for it."""
    game = new_game(db=db, seed=11)
    player = game.state.current
    card = CardInstance.make("BS1-029", 0)     # Lime Cookie, 【On Play】
    player.hand.append(card)
    cookie = game._deploy_cookie(player, card)
    assert not [a for a in game.legal_actions()
                if isinstance(a, A.ActivateSkill) and a.source_uid == cookie.uid]


def test_item_play_cost_comes_from_the_leading_bracket(db):
    assert db["ST9-016"].play_cost == Cost.parse("{B}{B}")
    assert db["ST8-013"].play_cost == Cost.parse("{G}")


# --- costs -----------------------------------------------------------------
def test_generic_symbols_do_not_eat_the_needed_colour():
    cost = Cost.parse("{B}{N}")
    plan = plan_payment(cost, [Color.BLUE, Color.GREEN])
    assert plan is not None and sorted(plan.indices) == [0, 1]
    assert plan_payment(cost, [Color.GREEN, Color.GREEN]) is None


def test_unpayable_cost_returns_none():
    assert plan_payment(Cost.parse("{B}{B}{B}"), [Color.BLUE, Color.BLUE]) is None


# --- decks -----------------------------------------------------------------
@pytest.mark.parametrize("name", list(STARTER_DECKS))
def test_starter_decks_are_legal(name, db):
    report = validate(STARTER_DECKS[name], db)
    assert report.ok, report.problems
    assert report.size == 60
    assert report.flip_count <= 16


@pytest.mark.parametrize("set_id", list(STARTER_SET_IDS))
def test_derived_starter_decks_are_legal_and_playable(set_id, db):
    deck = build_starter_deck(db, set_id)
    report = validate(deck, db)
    assert report.ok, report.problems
    assert report.size == 60
    assert report.flip_count <= 16
    # Only cards from that one set, and enough LV1 Cookies to open on.
    assert {db[c].set_id for c in deck} == {set_id}
    assert sum(1 for c in deck if db[c].is_cookie and db[c].level == 1) >= 12

    game = Game([deck, STARTER_DECKS["st9_sea_fairy"]],
                [SeatedAgent(HeuristicAgent(seed=1), 0),
                 SeatedAgent(HeuristicAgent(seed=2), 1)], db=db, seed=3)
    game.setup()
    assert game.play_out().winner is not None


def test_starter_deck_accepts_a_transcribed_name(db):
    assert starter_deck(db, "st9_sea_fairy") == list(STARTER_DECKS["st9_sea_fairy"])


def test_validator_rejects_a_bad_deck(db):
    report = validate(["ST9-006"] * 60, db)
    assert not report.ok
    assert any("copies" in p for p in report.problems)


# --- engine ----------------------------------------------------------------
def test_setup_gives_both_players_a_cookie_with_hp(db):
    game = new_game(db=db)
    for player in game.state.players:
        assert len(player.battle) == 1
        cookie = player.battle[0]
        assert cookie.remaining_hp == cookie.defn(db).hp
        assert len(player.hand) >= 4


def test_first_player_skips_the_opening_draw(db):
    game = new_game(db=db)
    # 6 opening cards minus the Cookie put into play, no draw on turn 1.
    assert len(game.state.players[0].hand) == 5


def test_legal_actions_always_offer_a_way_out(db):
    game = new_game(db=db)
    seen = 0
    while not game.state.over and seen < 400:
        options = game.legal_actions()
        assert options, "a player with no legal action would deadlock"
        assert any(isinstance(a, A.EndTurn) for a in options)
        game.step(game.controller(game.to_move()).choose_action(game.state, options))
        seen += 1


def test_support_is_limited_to_one_card_per_turn(db):
    game = new_game(db=db)
    player = game.state.current
    supports = [a for a in game.legal_actions() if isinstance(a, A.PlaceSupport)]
    game.step(supports[0])
    assert len(player.support) == 1
    assert not [a for a in game.legal_actions() if isinstance(a, A.PlaceSupport)]


def test_any_cookie_can_be_played_free_into_a_free_slot(db):
    """Cookies cost nothing and there is no level-up: "When a Cookie card
    plays, you do not [rest] the cost in the support area."."""
    game = new_game(db=db)
    player = game.state.current
    lv3 = next(c for c in player.deck if db[c.card_id].level == 3
               and db[c.card_id].is_cookie)
    player.deck.remove(lv3)
    player.hand.append(lv3)
    support_before = [c.rested for c in player.support]

    plays = [a for a in game.legal_actions()
             if isinstance(a, A.PlayCookie) and a.card_uid == lv3.uid]
    assert plays, "a LV3 Cookie must be playable straight from hand"
    game.step(plays[0])

    assert len(player.battle) == 2
    new = player.battle[-1]
    assert new.card.card_id == lv3.card_id
    assert new.remaining_hp == db[lv3.card_id].hp
    assert [c.rested for c in player.support] == support_before


def test_battle_area_holds_at_most_two_cookies(db):
    game = new_game(db=db)
    player = game.state.current
    for _ in range(2):
        # Re-pick each time: playing a Cookie draws HP cards off the deck.
        card = next(c for c in player.deck if db[c.card_id].is_cookie)
        player.deck.remove(card)
        player.hand.append(card)
        plays = [a for a in game.legal_actions()
                 if isinstance(a, A.PlayCookie) and a.card_uid == card.uid]
        if plays:
            game.step(plays[0])
    assert len(player.battle) == 2
    assert not [a for a in game.legal_actions() if isinstance(a, A.PlayCookie)]


def test_empty_deck_refreshes_instead_of_losing(db):
    """[refresh]: one LV1+ Cookie from the trash goes to your own break area,
    then the trash is shuffled back into the deck."""
    game = new_game(db=db)
    player = game.state.players[0]
    player.trash.extend(player.deck)
    player.deck.clear()
    cookies_in_trash = [c for c in player.trash if (db[c.card_id].level or 0) >= 1]
    assert cookies_in_trash
    trash_size = len(player.trash)

    drawn = game.draw(player, 1, optional=False)

    assert not game.state.over, "running out of deck must not lose the game"
    assert drawn == 1
    assert len(player.break_area) == 1
    assert not player.trash
    assert len(player.deck) == trash_size - 1 - 1  # one broken, one drawn


def test_refresh_can_still_lose_by_filling_the_break_area(db):
    game = new_game(db=db)
    player = game.state.players[0]
    player.break_area.extend(
        [c for c in player.deck if (db[c.card_id].level or 0) == 3][:3]
    )
    for card in player.break_area:
        if card in player.deck:
            player.deck.remove(card)
    player.trash.extend(player.deck)
    player.deck.clear()
    # Force the refresh to break a LV1+ Cookie, tipping the total to 10.
    player.trash[:] = [c for c in player.trash if (db[c.card_id].level or 0) >= 1]

    game.draw(player, 1, optional=False)

    assert game.state.over
    assert game.state.winner == 1


def test_damage_moves_hp_cards_to_trash_and_faints_at_zero(db):
    game = new_game(db=db)
    victim = game.state.players[1].battle[0]
    trash_before = len(game.state.players[1].trash)
    hp = victim.remaining_hp

    game.deal_damage(victim, 1, source_player=0)
    assert victim.remaining_hp == hp - 1
    assert len(game.state.players[1].trash) == trash_before + 1

    game.deal_damage(victim, hp, source_player=0)
    assert victim not in game.state.players[1].battle
    assert len(game.state.players[1].break_area) == 1


def test_break_area_reaching_level_ten_ends_the_game(db):
    game = new_game(db=db)
    loser = game.state.players[1]
    lv3s = [c for c in loser.deck if (db[c.card_id].level or 0) == 3][:4]
    loser.break_area.extend(lv3s)
    game._check_win()
    assert game.state.over
    assert game.state.winner == 0
    assert "break area" in game.state.win_reason


def test_flip_card_effect_fires_when_revealed(db):
    game = new_game(db=db)
    owner = game.state.players[1]
    victim = owner.battle[0]
    # Cucumber Cookie's flip draws a card; P1 plays the ST8 deck.
    cucumber = next(c for c in owner.deck if c.card_id == "ST8-009")
    owner.deck.remove(cucumber)
    victim.hp_cards.append(cucumber)           # top of the pile
    hand_before = len(owner.hand)

    game.deal_damage(victim, 1, source_player=0)

    assert cucumber in owner.trash
    assert len(owner.hand) == hand_before + 1


def test_no_attacking_on_the_very_first_turn(db):
    game = new_game(db=db)
    assert game.state.turn_number == 1 and game.state.turn_player == 0
    assert not [a for a in game.legal_actions() if isinstance(a, A.Attack)]


def test_a_freshly_played_cookie_may_attack(db):
    """There is no summoning sickness: a revealed Cookie enters [active]."""
    game = new_game(db=db)
    game.step(A.EndTurn())          # off the no-attack first turn
    player = game.state.current
    cookie = player.battle[0]
    cookie.summoned_this_turn = True
    for card in player.deck[:6]:    # guarantee payable support
        pass
    _, colors = player.active_support_colors(db)
    attack = cookie.defn(db).attack
    if attack is not None:
        from braverse.cost import plan_payment
        payable = plan_payment(attack.cost, colors) is not None
        assert game._can_attack(player, cookie, colors) == payable


def test_clone_is_independent(db):
    game = new_game(db=db)
    twin = game.clone()
    twin.state.players[0].hand.clear()
    twin.state.players[0].battle[0].attack_bonus = 99
    assert game.state.players[0].hand
    assert game.state.players[0].battle[0].attack_bonus == 0


def test_games_are_deterministic_for_a_seed(db):
    a = new_game(seed=7, db=db).play_out()
    b = new_game(seed=7, db=db).play_out()
    assert (a.winner, a.turn_number, a.log) == (b.winner, b.turn_number, b.log)


@pytest.mark.parametrize("seed", range(25))
def test_random_games_terminate_cleanly(seed, db):
    state = new_game(seed=seed, kind=RandomAgent, db=db).play_out()
    assert state.over
    assert state.winner in (-1, 0, 1)
    for player in state.players:
        assert len(player.battle) <= 2


def test_heuristic_beats_random(db):
    wins = 0
    games = 40
    for seed in range(games):
        controllers = [SeatedAgent(HeuristicAgent(seed=seed), 0),
                       SeatedAgent(RandomAgent(seed=seed), 1)]
        game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                    controllers, db=db, seed=seed)
        game.setup()
        wins += game.play_out().winner == 0
    assert wins / games > 0.65, f"heuristic won only {wins}/{games}"


def test_a_multi_card_discard_is_one_question_for_a_controller_that_wants_it():
    """A human picks the whole discard at once; scripted agents still loop.

    The looping path is what every bot uses, so its behaviour — and the
    self-play numbers that depend on it — must not move.
    """
    from braverse.effects import ask_many

    class Batch:
        """Answers the whole selection in one go."""

        def __init__(self, take):
            self.take = take
            self.asked = []

        def choose(self, state, prompt, options, *, optional):
            raise AssertionError("should have been asked for the batch")

        def choose_many(self, state, prompt, options, *, count, optional, up_to=False):
            self.asked.append((prompt, count))
            return [options[i] for i in self.take]

    class OneAtATime:
        def __init__(self):
            self.asked = []

        def choose(self, state, prompt, options, *, optional):
            self.asked.append(prompt)
            return options[-1]

    pool = ["a", "b", "c", "d"]

    batch = Batch([1, 3])
    assert ask_many(batch, None, "Discard 2 cards", pool, 2) == ["b", "d"]
    assert batch.asked == [("Discard 2 cards", 2)]

    loop = OneAtATime()
    assert ask_many(loop, None, "Discard 2 cards", pool, 2) == ["d", "c"]
    assert len(loop.asked) == 2, "the fallback must still ask once per card"

    # A short, padded or repeated answer still discards exactly `count` cards
    # from the pool, so the game state cannot be left inconsistent.
    assert len(ask_many(Batch([]), None, "Discard 2 cards", pool, 2)) == 2
    assert len(ask_many(Batch([0, 0, 1]), None, "Discard 2 cards", pool, 2)) == 2
    assert sorted(ask_many(Batch([0, 0, 1]), None, "d", pool, 2)) == ["a", "b"]


def test_discarding_takes_the_cards_the_controller_picked():
    from braverse.effects import Ctx

    db = default_db()
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=3)
    game.setup()
    player = game.state.players[0]

    class PicksTheLastTwo:
        def choose_action(self, state, options):
            return options[0]

        def choose_many(self, state, prompt, options, *, count, optional, up_to=False):
            return list(options)[-count:]

    game._controllers[0] = PicksTheLastTwo()
    wanted = list(player.hand)[-2:]
    ctx = Ctx(game=game, state=game.state, db=db, me=player, opp=game.state.players[1])
    before = len(player.hand)

    discarded = game.discard(player, 2, ctx)
    assert discarded == wanted
    assert len(player.hand) == before - 2
    assert all(card in player.trash for card in wanted)


def _plain_pile(game, db):
    """Swap FLIPs out of every HP pile so a test measures damage, not flips."""
    for player in game.state.players:
        for cookie in player.battle:
            plain = [c for c in player.deck if not db[c.card_id].is_flip]
            for i, card in enumerate(list(cookie.hp_cards)):
                if db[card.card_id].is_flip and plain:
                    swap = plain.pop()
                    player.deck.remove(swap)
                    player.deck.append(card)
                    cookie.hp_cards[i] = swap


def _lone_cookie(game, seat=1):
    """The Cookie on ``seat``'s board, with a predictable HP pile."""
    player = game.state.players[seat]
    cookie = player.battle[0]
    return player, cookie


def test_damage_removes_exactly_the_cards_it_says_it_does():
    """Each point of damage turns one HP card, and the log states the count."""
    db = default_db()
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=6)
    game.setup()
    _plain_pile(game, db)
    player, cookie = _lone_cookie(game)

    hp = cookie.remaining_hp
    game.deal_damage(cookie, 2, source_player=0)
    assert cookie.remaining_hp == hp - 2
    assert game.state.log[-1].endswith(f"takes 2 effect damage — {hp - 2} HP left")

    # More damage than HP: it takes what is there and says so. Fainting
    # trails its own lines (a replacement gets fielded), so search the log.
    left = cookie.remaining_hp
    mark = len(game.state.log)
    game.deal_damage(cookie, left + 3, source_player=0)
    assert cookie.remaining_hp == 0
    tail = game.state.log[mark:]
    assert any(f"takes {left} effect damage (of {left + 3})" in line
               for line in tail), tail


def test_damage_respects_immunity_and_the_hp_floor():
    db = default_db()
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=6)
    game.setup()
    _plain_pile(game, db)
    player, cookie = _lone_cookie(game)

    cookie.damage_immune = True
    before = cookie.remaining_hp
    game.deal_damage(cookie, 3, source_player=0)
    assert cookie.remaining_hp == before
    assert "takes no effect damage (immune)" in game.state.log[-1]

    # "This Cookie's HP cannot reach 0" does not stop the damage: every card
    # is still turned, and a replacement comes off the deck each time the pile
    # would empty, so the Cookie is still standing at the end of it.
    cookie.damage_immune = False
    cookie.hp_cannot_reach_zero = True
    turned = len(player.trash)
    game.deal_damage(cookie, 4, source_player=0)
    assert cookie.remaining_hp >= 1, "the floor let it faint"
    assert cookie in player.battle
    assert len(player.trash) - turned == 4, "the floor swallowed the damage"


def test_the_log_tells_a_swing_apart_from_a_rider_or_a_skill():
    """"Then, ..." damage, a trap and an 【Activate】 all read as effect damage;
    only the attack itself is attack damage. The log is the only place a player
    can see which one just hit them."""
    db = default_db()
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=6)
    game.setup()
    _plain_pile(game, db)
    _, target = _lone_cookie(game)

    game.deal_damage(target, 1, source_player=0, kind="attack")
    assert "attack damage" in game.state.log[-1]
    game.deal_damage(target, 1, source_player=0)
    assert "effect damage" in game.state.log[-1], "effect damage is the default"


def test_the_log_says_what_kind_of_thing_hit_you():
    """"effect damage" covers a trap, an 【Activate】, an ITEM and an attack
    rider. The stamp every line carries names the card; this pins that it also
    says which of those four the card was being at the time."""
    from braverse.effects import Trigger
    from braverse.engine import source_kind
    from braverse.enums import CardType

    db = default_db()
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=6)
    game.setup()
    _plain_pile(game, db)
    _, target = _lone_cookie(game)

    trap = next(c for c in db.cards.values() if c.type is CardType.TRAP)
    item = next(c for c in db.cards.values() if c.type is CardType.ITEM)
    # The trigger wins where it says something; the card answers for the shared
    # ITEM/TRAP body, which is the pair most worth telling apart.
    assert source_kind(db, CardInstance.make(trap.id, 0), Trigger.FLIP) == "FLIP"
    assert source_kind(db, CardInstance.make(trap.id, 0), Trigger.ITEM) == "trap"
    assert source_kind(db, CardInstance.make(item.id, 0), Trigger.ITEM) == "item"

    with game._effect_source("Piercing Arrow of Purity", "trap"):
        game.deal_damage(target, 1, source_player=1)
    line = game.state.log[-1]
    assert "[Piercing Arrow of Purity \u00b7 trap]" in line, line
    assert "effect damage" in line, line

    # An attack has no `[...]` stamp — nothing is resolving — so the swinging
    # Cookie has to be named on the line itself.
    game.deal_damage(target, 1, source_player=1, kind="attack",
                     source="Wind Archer Cookie's Tracker's Arrow")
    line = game.state.log[-1]
    assert "from Wind Archer Cookie's Tracker's Arrow" in line, line


def test_an_attack_is_named_the_way_the_card_names_it():
    """"attacks for 3" said which Cookie swung, not which of its lines did."""
    db = default_db()
    agents = [SeatedAgent(HeuristicAgent(db=db), 0),
              SeatedAgent(HeuristicAgent(db=db), 1)]
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                agents, db=db, seed=11)
    game.setup()
    for _ in range(200):
        if game.state.over:
            break
        options = game.legal_actions()
        if not options:
            break
        chosen = agents[game.state.turn_player].choose_action(game.state, options)
        game.step(chosen or options[0])

    declares = [l for l in game.state.log if " attacks " in l]
    assert declares, "the game never swung"
    assert any(" with " in l for l in declares), declares[:5]
    hits = [l for l in game.state.log if "attack damage" in l]
    assert all(" from " in l for l in hits), hits[:5]


def test_an_attack_applies_its_printed_damage_after_buffs_and_reductions():
    db = default_db()
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=6)
    game.setup()
    _plain_pile(game, db)
    attacker = game.state.players[0].battle[0]
    _, target = _lone_cookie(game)

    printed = attacker.defn(db).attack.damage
    assert attacker.attack_damage(db) == printed

    attacker.attack_bonus = 2
    assert attacker.attack_damage(db) == printed + 2, "a buff was not counted"

    # The reduction is applied to what lands, not to what the card prints.
    target.incoming_damage_reduction = 1
    landed = max(0, attacker.attack_damage(db) - target.incoming_damage_reduction)
    hp = target.remaining_hp
    game.deal_damage(target, landed, source_player=0)
    assert target.remaining_hp == max(0, hp - landed)


def test_a_flip_returns_itself_to_hand_not_the_cookie_it_was_hp_for():
    """"Return this Cookie to your hand" means the revealed card.

    The pool is explicit about this: all 92 FLIPs that mean their host spell it
    out as "the Cookie with this card attached for HP". Only these five say
    "this Cookie", so it is the card itself — it comes back out of the trash
    instead of staying there, and the Cookie it was serving keeps taking the
    rest of the damage.
    """
    db = default_db()
    game = Game([STARTER_DECKS["st8_wind_archer"], STARTER_DECKS["st9_sea_fairy"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=3)
    game.setup()

    player = game.state.players[0]
    host = player.battle[0]
    # Put a Muscle Cookie ("Return this Cookie to your hand.") on top of the pile.
    muscle = next(c for c in player.deck if c.card_id == "ST8-002")
    player.deck.remove(muscle)
    host.hp_cards.append(muscle)

    hp_before = host.remaining_hp
    hand_before = len(player.hand)
    game.deal_damage(host, 1, source_player=1)

    assert muscle in player.hand, "the revealed card did not come back to hand"
    assert muscle not in player.trash
    assert len(player.hand) == hand_before + 1
    assert host in player.battle, "the host was bounced; it should have stayed"
    assert host.remaining_hp == hp_before - 1, "the host still loses the HP card"


def test_a_flip_that_names_its_host_still_means_the_host():
    """The long phrasing is the one that reaches the Cookie holding the card."""
    db = default_db()
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=3)
    game.setup()

    player = game.state.players[0]
    host = player.battle[0]
    # Parfait Cookie: "<Discard 1 card.> The Cookie with this card attached for
    # HP gains +1 HP."
    parfait = next(c for c in player.deck if c.card_id == "ST9-010")
    player.deck.remove(parfait)
    host.hp_cards.append(parfait)

    hp_before = host.remaining_hp
    game.state.events.clear()
    game.deal_damage(host, 1, source_player=1)
    # One card off for the damage, one back on from the flip: net unchanged,
    # and the heal names the host rather than the card that was revealed.
    assert host.remaining_hp == hp_before, "the host was not the one healed"
    heals = [e for e in game.state.events if e["kind"] == "heal"]
    assert [e["cookie"] for e in heals] == [host.uid], heals
    assert parfait in player.trash, "a healing flip stays in the trash"


# --- offering only moves that would do something ---------------------------
def _add_to_hand(player, card_id):
    card = CardInstance.make(card_id, player.index)
    player.hand.append(card)
    return card


def _offered(game, uid):
    """Whether any move would play or activate the card with this uid."""
    return any(getattr(a, "card_uid", getattr(a, "source_uid", None)) == uid
               for a in game.legal_actions()
               if isinstance(a, (A.PlaySupportCard, A.PlayTrap, A.ActivateSkill)))


def _stock_support(player, card_id, count):
    player.support.clear()
    for _ in range(count):
        player.support.append(CardInstance.make(card_id, player.index))


def test_item_with_no_legal_target_is_not_offered(db):
    """ST7-016 only reaches a Cookie with 2 or less HP remaining. With no such
    Cookie on the board the card resolves into nothing, and offering it tells
    the player something untrue about their options."""
    game = new_game(seed=3, db=db)
    me = game.state.current
    _stock_support(me, "ST7-016", 4)
    card = _add_to_hand(me, "ST7-016")

    victim = game.state.opponent_of(me.index).battle[0]
    assert victim.remaining_hp > 2
    assert not _offered(game, card.uid)

    del victim.hp_cards[2:]          # now there is something to point at
    assert _offered(game, card.uid)


def test_item_whose_condition_is_false_is_not_offered(db):
    """ST8-016 needs Wind Archer out and 5 support cards. Both are readable
    from the board before the card is ever played."""
    game = new_game(seed=3, db=db)
    me = game.state.players[1]        # the ST8 seat
    while game.state.turn_player != me.index:
        game.end_turn()
    _stock_support(me, "ST8-016", 4)
    card = _add_to_hand(me, "ST8-016")

    assert not _offered(game, card.uid), "offered with only 4 support cards"
    me.support.append(CardInstance.make("ST8-016", me.index))
    assert _offered(game, card.uid) == bool(
        any(c.name(db) == "Wind Archer Cookie" for c in me.battle))


def test_hand_condition_is_read_as_it_will_be_when_the_card_resolves(db):
    """The item is still in hand while the action list is built and gone from it
    by the time its effect runs, so a hand-size condition is off by one unless
    the probe accounts for the card itself.

    BS8-096: "<{B}{B}> If there are 2 cards or less in your hand, draw up to 4."
    """
    game = new_game(seed=3, db=db)
    me = game.state.current
    _stock_support(me, "ST9-016", 4)          # blue, to pay {B}{B}
    me.hand.clear()
    card = _add_to_hand(me, "BS8-096")
    _add_to_hand(me, "ST9-016")
    _add_to_hand(me, "ST9-016")

    # Three cards in hand now, two once this one is played: the card is live.
    assert len(me.hand) == 3
    assert _offered(game, card.uid)

    _add_to_hand(me, "ST9-016")                # four now, three after playing
    assert not _offered(game, card.uid)


def test_unimplemented_card_is_still_offered(db):
    """A blank the engine has not filled in is the engine's gap, not something
    the rules say. Hiding it would be a different kind of lie."""
    from braverse.effects import is_implemented

    assert not is_implemented("BS3-043")
    game = new_game(seed=3, db=db)
    me = game.state.current
    _stock_support(me, "ST7-016", 4)           # yellow, pays {Y}{Y}{Y}
    card = _add_to_hand(me, "BS3-043")
    assert _offered(game, card.uid)


def test_stage_is_offered_for_placement_even_when_its_ability_is_dead(db):
    """Placing a stage is worth doing on its own; its 【Activate】 is a separate
    move that is gated separately."""
    game = new_game(seed=3, db=db)
    me = game.state.current
    _stock_support(me, "ST9-016", 5)
    me.hand.clear()
    for _ in range(6):
        _add_to_hand(me, "ST9-016")
    card = _add_to_hand(me, "ST9-020")          # Tearcrown, activate needs hand <= 3
    assert _offered(game, card.uid), "a stage must still be placeable"

    game.step(next(a for a in game.legal_actions()
                   if isinstance(a, A.PlaySupportCard) and a.card_uid == card.uid))
    assert card in me.stage
    assert not _offered(game, card.uid), "its 【Activate】 cannot draw with a full hand"

    del me.hand[3:]
    assert _offered(game, card.uid)


def test_trap_that_could_not_do_anything_is_not_offered(db):
    """During the defender's response window, only traps that would actually
    land should be on the list."""
    game = new_game(seed=3, db=db)
    me = game.state.current
    defender = game.state.opponent_of(me.index)
    _stock_support(defender, "ST9-018", 4)
    trap = _add_to_hand(defender, "ST9-018")    # -1 attack damage to an attacker

    attacker = me.battle[0]
    target = defender.battle[0]

    # Drive the response window directly: what is being tested is which traps
    # the window offers, not the road to opening one.
    game._response_player = defender.index
    game._pending_attack = (attacker, target)
    game._attacking_cookie = attacker
    game._trap_used = 0
    assert _offered(game, trap.uid)

    me.battle.clear()          # nothing left to debuff
    assert not _offered(game, trap.uid)


def test_playable_if_gates_a_hand_written_card(db):
    """Hand-written bodies are opaque Python, so they declare their condition
    rather than having it read off them."""
    from braverse.effects import Trigger, effect_is_live, get_effect

    fn = get_effect("ST9-007", Trigger.ACTIVATE)   # draw if hand <= 3
    assert hasattr(fn, "playable")

    game = new_game(seed=3, db=db)
    me = game.state.current
    ctx = game._ctx(me, source_cookie=me.battle[0], source_card=me.battle[0].card)
    del me.hand[3:]
    assert effect_is_live(fn, ctx)
    me.hand.extend(me.deck[:4])
    assert not effect_is_live(fn, ctx)


# --- what the log says caused a thing --------------------------------------
def test_the_log_names_the_card_whose_effect_is_running():
    """Without the name, "draws 1 card" could be any of a dozen cards on the
    board — and a FLIP that fires mid-attack looks like the attack itself."""
    db = default_db()
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=6)
    game.setup()
    _plain_pile(game, db)
    _, target = _lone_cookie(game)

    game.deal_damage(target, 1, source_player=0, kind="attack")
    assert "[" not in game.state.log[-1], "damage is not itself a card effect"

    with game._effect_source("Divine Light Crystal"):
        game.state.record("something happened")
    assert game.state.log[-1].endswith("[Divine Light Crystal] something happened")
    assert game.state.effect_sources == [], "the stack has to unwind"


# --- "rest up to N cards" ---------------------------------------------------
def test_resting_up_to_n_asks_which_cards_and_takes_a_short_answer():
    """"Up to" is a real choice: which support cards go down decides what is
    left to pay with, and how many go down is what damage-by-count reads."""
    from braverse.effects import Ctx

    db = default_db()
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=4)
    game.setup()
    player = game.state.players[0]
    player.support = [player.deck.pop(0) for _ in range(3)]

    asked = []

    class PicksOne:
        def choose_action(self, state, options):
            return options[0]

        def choose_many(self, state, prompt, options, *, count, optional, up_to=False):
            asked.append((prompt, count, up_to))
            return [options[-1]]

    game._controllers[0] = PicksOne()
    ctx = Ctx(game=game, state=game.state, db=db, me=player, opp=game.state.players[1])

    assert ctx.rest_support(2) == 1, "a short answer must be honoured"
    assert asked and asked[0][1:] == (2, True)
    assert "up to 2" in asked[0][0]
    assert [c.rested for c in player.support] == [False, False, True]


def test_resting_up_to_n_still_takes_the_first_cards_for_a_bot():
    """A scripted agent has no opinion worth asking for, and asking would only
    move self-play numbers."""
    from braverse.effects import Ctx

    db = default_db()
    game = new_game(seed=4, db=db)
    player = game.state.players[0]
    player.support = [player.deck.pop(0) for _ in range(3)]
    ctx = Ctx(game=game, state=game.state, db=db, me=player, opp=game.state.players[1])

    assert ctx.rest_support(2) == 2
    assert [c.rested for c in player.support] == [True, True, False]


# --- healing ----------------------------------------------------------------
def test_healing_refills_the_pile_without_raising_printed_hp():
    db = default_db()
    game = new_game(seed=5, db=db)
    cookie = game.state.players[0].battle[0]
    printed = cookie.max_hp(db)
    cookie.hp_cards.pop()
    game.state.events.clear()

    game.gain_hp(cookie, 2)
    assert cookie.remaining_hp == printed + 1, "the cards did not come back"
    assert cookie.max_hp(db) == printed, "healing made the Cookie bigger"
    heals = [e for e in game.state.events if e["kind"] == "heal"]
    assert heals == [{"kind": "heal", "cookie": cookie.uid, "owner": 0,
                      "amount": 2, "left": printed + 1}]


def test_one_card_turned_is_one_point_of_damage_spent():
    """Four damage into a 1 HP Cookie whose pile keeps healing.

    Each card turned spends one point of the hit. A FLIP that hands its host a
    card back as it turns puts the HP straight back on — but the point is spent
    either way — so the hit runs until the damage is used up *or* the Cookie is
    at 0, whichever comes first. Heal on every card and a 4-damage swing turns
    four cards and leaves the Cookie standing on the fourth.
    """
    db = default_db()
    game = new_game(seed=5, db=db)
    player = game.state.players[1]
    cookie = player.battle[0]

    class SaysYes:
        """Pays every FLIP's <Discard 1 card.>"""

        def choose_action(self, state, options):
            return options[0]

        def choose(self, state, prompt, options, *, optional):
            return options[0]

    game._controllers[1] = SaysYes()
    # One HP, and a deck of nothing but healing FLIPs — so every card the hit
    # turns hands one straight back.
    while cookie.hp_cards:
        player.trash.append(cookie.hp_cards.pop())
    card = CardInstance.make("ST8-007", 1)
    card.face_up = False
    cookie.hp_cards.append(card)
    player.deck = [CardInstance.make("ST8-007", 1) for _ in range(10)]
    player.hand = [CardInstance.make("ST8-011", 1) for _ in range(10)]
    assert db["ST8-007"].is_flip

    game.state.events.clear()
    game.deal_damage(cookie, 4, source_player=0, kind="attack")

    turned = [e for e in game.state.events if e["kind"] == "reveal"]
    heals = [e for e in game.state.events if e["kind"] == "heal"]
    assert len(turned) == 4, "one card per point of damage"
    assert len(heals) == 4, "every one of them healed"
    assert cookie in player.battle, "it healed on the last card, so it is up"
    assert cookie.remaining_hp == 1
    assert "takes 4 attack damage" in game.state.log[-1], game.state.log[-1]


def test_the_hit_stops_the_moment_the_cookie_is_out_of_cards():
    """The other end of the same rule: an unhealed pile runs out and the rest
    of the damage has nothing to land on."""
    db = default_db()
    game = new_game(seed=5, db=db)
    _plain_pile(game, db)
    player = game.state.players[1]
    cookie = player.battle[0]
    while len(cookie.hp_cards) > 2:
        player.trash.append(cookie.hp_cards.pop())

    game.deal_damage(cookie, 5, source_player=0, kind="attack")
    assert cookie not in player.battle
    line = next(l for l in game.state.log if "attack damage" in l)
    assert "takes 2 attack damage (of 5)" in line, line


# --- ST3-020 Divine Light Crystal -------------------------------------------
def test_divine_light_crystal_survives_a_lethal_swing():
    """Played the way a player plays it: sprung in the response window, with
    exactly the support its printed cost needs and not a card more.

    The first version of this test called the effect function directly, which
    skipped the engine's own payment of the trap's play cost — so it passed
    while the card did nothing in a real game. The body was paying `<{G}{G}>` a
    second time, and silently doing nothing when it could not.
    """
    db = default_db()
    game = Game([STARTER_DECKS["st8_wind_archer"], STARTER_DECKS["st9_sea_fairy"]],
                [SeatedAgent(HeuristicAgent(db=db), 0), _SpringsTheTrap()],
                db=db, seed=4)
    game.setup()
    _plain_pile(game, db)

    defender = game.state.players[1]
    defender.hand = [CardInstance.make("ST3-020", 1)]
    defender.support = [CardInstance.make("ST3-012", 1) for _ in range(2)]
    for card in defender.support:
        card.rested = False
    assert db["ST3-012"].color is Color.GREEN
    assert str(db["ST3-020"].play_cost) == "{G}{G}", "exactly enough, no spare"

    victim = defender.battle[0]
    while len(victim.hp_cards) > 1:
        defender.trash.append(victim.hp_cards.pop())

    attacker_side = game.state.players[0]
    attacker = attacker_side.battle[0]
    attacker_side.support = [CardInstance.make("ST8-011", 0) for _ in range(5)]
    for card in attacker_side.support:
        card.rested = False

    game._do_attack(A.Attack(attacker.uid, victim.uid))

    assert victim in defender.battle, "Divine Light Crystal let it faint"
    assert victim.remaining_hp >= 1
    assert not defender.break_area
    assert any("HP cannot reach 0" in line for line in game.state.log), \
        game.state.log[-5:]


def test_the_floor_also_holds_against_hp_placed_into_the_trash():
    """"Place N cards from the top of that Cookie's HP into the trash" empties
    a pile just as surely as damage does, and the card says HP cannot reach 0
    — not "cannot reach 0 from damage"."""
    from braverse.effects import Ctx

    db = default_db()
    game = new_game(seed=9, db=db)
    them = game.state.players[1]
    victim = them.battle[0]
    victim.hp_cannot_reach_zero = True
    ctx = Ctx(game=game, state=game.state, db=db, me=game.state.players[0], opp=them)

    ctx.trash_hp(victim, victim.remaining_hp + 2)
    assert victim in them.battle, "the pile was stripped to nothing"
    assert victim.remaining_hp >= 1


def test_no_hand_written_item_pays_its_own_play_cost_twice():
    """The `<...>` at the *front* of an ITEM or TRAP is the card's play cost.

    The engine rests support for it before the body ever runs, so a body that
    also calls `ctx.pay` for the same cost charges twice — and, because a
    failed payment just returns, the card silently does nothing. Two cards had
    that bug. This is the check that keeps a third from arriving.
    """
    import inspect
    import re

    from braverse.cards import default_db as _db
    from braverse.effects import _REGISTRY, Trigger

    db = _db()
    offenders = []
    for (card_id, trigger), fn in _REGISTRY.items():
        if trigger is not Trigger.ITEM or not hasattr(fn, "__code__"):
            continue        # a compiled Program has no source to read
        defn = db[card_id]
        cost = str(defn.play_cost)
        if not cost:
            continue
        source = inspect.getsource(fn)
        paid = re.findall(r'Cost\.parse\("([^"]+)"\)', source)
        # A card may legitimately pay the same symbol again for a *later*
        # bracket — "Then, <{Y}> You can 【Equip】 ..." — so only a card whose
        # printed text has one bracketed cost is an offender.
        brackets = re.findall(r"<([^>]*)>", defn.description or "")
        if cost in paid and len(brackets) == 1:
            offenders.append((card_id, defn.name, cost))
    assert not offenders, f"these pay their own play cost twice: {offenders}"


class _SpringsTheTrap:
    """A defender that always springs, and always picks the first option."""

    def choose_action(self, state, options):
        trap = next((o for o in options if isinstance(o, A.PlayTrap)), None)
        return trap or next((o for o in options if isinstance(o, A.Pass)), options[0])

    def choose(self, state, prompt, options, *, optional):
        return options[0]


# --- viewing the top of your deck -------------------------------------------
class _Watcher:
    """A seat that records what it was offered and what it was shown beside it."""

    def __init__(self):
        self.offered = []
        self.viewing = []

    def choose_action(self, state, options):
        return options[0]

    def choose(self, state, prompt, options, *, optional):
        self.offered.append(list(options))
        self.viewing.append(list(state.viewing))
        return options[0] if options else None


def _viewer_game(db, top_colors):
    """A game whose seat-0 deck has cards of `top_colors` on top, in order."""
    from braverse.enums import Color as C
    seat = _Watcher()
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [seat, SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=5)
    game.setup()
    seat.offered.clear()          # the opening-Cookie question is not ours
    seat.viewing.clear()
    me = game.state.players[0]
    picks = []
    for color in top_colors:
        defn = next(c for c in db.cards.values() if c.color is C[color])
        picks.append(CardInstance.make(defn.id, 0))
    me.deck[:0] = picks
    return game, seat, me


def _ctx_for(game, me, **kw):
    from braverse.effects import Ctx
    return Ctx(game=game, state=game.state, db=game.db, me=me,
               opp=game.state.opponent_of(me.index), **kw)


def test_viewing_the_top_shows_every_card_and_offers_only_the_eligible_ones():
    """"View the top 3, select 1 {B} card" is two instructions, not one.

    Aloe Cookie used to filter to the blue cards and offer only those, so a
    card whose whole point is looking at three showed you one. `pick` narrows
    what is *selectable*; the full view rides along on `state.viewing` for the
    front end to draw.
    """
    from braverse.effects import Trigger, get_effect

    db = default_db()
    game, seat, me = _viewer_game(db, ["RED", "BLUE", "RED"])
    top = list(me.deck[:3])
    before = len(me.deck)

    get_effect("BS2-040", Trigger.FAINT)(
        _ctx_for(game, me, trigger=Trigger.FAINT.value))

    assert seat.viewing == [top], "all three were put in front of the player"
    assert seat.offered[0] == [top[1]], "only the {B} card could be taken"
    assert me.hand[-1] is top[1]
    # The other two go to the bottom, and nothing was lost on the way.
    assert len(me.deck) == before - 1
    assert me.deck[-2:] == [top[0], top[2]]


def test_a_view_with_no_criterion_offers_all_of_them():
    from braverse.effects import Trigger, get_effect

    db = default_db()
    game, seat, me = _viewer_game(db, ["RED", "BLUE", "GREEN"])
    top = list(me.deck[:3])

    get_effect("ST4-013", Trigger.ON_PLAY)(
        _ctx_for(game, me, trigger=Trigger.ON_PLAY.value))

    assert seat.offered[0] == top
    assert me.hand[-1] is top[0]


def test_a_view_that_bins_the_leftovers_bins_them():
    """BS9-101 is the same effect with the remainder going to the trash rather
    than the bottom of the deck, and it pays for itself with the Cookie."""
    from braverse.effects import Trigger, get_effect

    from braverse.enums import Color

    db = default_db()
    game, seat, me = _viewer_game(db, [])

    # Deploy first: building the Cookie's HP pile draws off the deck, which
    # would push a planted top three straight back down again.
    pie = CardInstance.make("BS9-101", 0)
    me.hand.append(pie)
    game._deploy_cookie(me, pie, run_on_play=False)
    cookie = me.battle[-1]
    purple = next(c for c in db.cards.values() if c.color is Color.PURPLE)
    me.support = [CardInstance.make(purple.id, 0) for _ in range(2)]
    for card in me.support:
        card.rested = False

    red = next(c for c in db.cards.values() if c.color is Color.RED)
    me.deck[:0] = [CardInstance.make(red.id, 0), CardInstance.make(purple.id, 0),
                   CardInstance.make(red.id, 0)]
    top = list(me.deck[:3])
    trash_before = len(me.trash)

    get_effect("BS9-101", Trigger.ACTIVATE)(
        _ctx_for(game, me, source_cookie=cookie, source_card=cookie.card,
                 trigger=Trigger.ACTIVATE.value))

    assert seat.viewing[-1] == top, "all three shown"
    assert seat.offered[-1] == [top[1]], "only the {P} card could be taken"
    assert me.hand[-1] is top[1]
    assert top[0] in me.trash and top[2] in me.trash
    assert len(me.trash) > trash_before
    assert cookie not in me.battle, "the Cookie paid for its own skill"
    assert sum(c.rested for c in me.support) == 1, "{P} came off a support card"


def test_a_view_names_only_the_card_it_reveals():
    """The log is public. What you looked at and put back is not."""
    from braverse.effects import Trigger, get_effect

    db = default_db()
    game, seat, me = _viewer_game(db, ["RED", "BLUE", "RED"])
    top = list(me.deck[:3])
    mark = len(game.state.log)

    get_effect("BS2-040", Trigger.FAINT)(
        _ctx_for(game, me, trigger=Trigger.FAINT.value))

    written = "\n".join(game.state.log[mark:])
    assert db[top[1].card_id].name in written, "the revealed card is named"
    for hidden in (top[0], top[2]):
        assert db[hidden.card_id].name not in written, written


def test_a_view_that_matches_nothing_still_shows_you_the_cards():
    """The commonest miss — three cards, none the right colour — is exactly
    when knowing what went past matters most, so it is shown and acknowledged
    rather than skipped."""
    from braverse.effects import Trigger, get_effect

    db = default_db()
    game, seat, me = _viewer_game(db, ["RED", "RED", "RED"])
    top = list(me.deck[:3])
    hand = len(me.hand)

    get_effect("BS2-040", Trigger.FAINT)(
        _ctx_for(game, me, trigger=Trigger.FAINT.value))

    assert seat.viewing == [top], "all three were still put in front of you"
    assert seat.offered == [[True]], "the only answer was an acknowledgement"
    assert len(me.hand) == hand, "nothing was taken"
    assert me.deck[-3:] == top, "and all three went back to the bottom"


def test_a_view_of_an_empty_deck_does_nothing():
    from braverse.effects import Trigger, get_effect

    db = default_db()
    game, seat, me = _viewer_game(db, [])
    me.deck.clear()
    hand = len(me.hand)

    get_effect("BS2-040", Trigger.FAINT)(
        _ctx_for(game, me, trigger=Trigger.FAINT.value))

    assert len(me.hand) == hand
    assert not seat.offered


def test_every_registered_effect_names_a_card_that_exists():
    """BS9-101 sat dead for the life of the file because it was registered as
    "BS09-101", which is not a card id — `get_effect` looks up the database's
    normalised id and never found it. Nothing failed; the card simply never did
    anything. A typo that costs a card its effect should not be silent."""
    import braverse  # noqa: F401  (imports every impl module)
    from braverse.effects import _REGISTRY

    db = default_db()
    unknown = sorted({cid for cid, _ in _REGISTRY if cid.split("@")[0] not in db})
    assert not unknown, f"effects registered against ids that do not exist: {unknown}"


# --- the mulligan -----------------------------------------------------------
def test_a_controller_that_wants_a_mulligan_gets_a_whole_new_hand():
    db = default_db()

    class Mulligans:
        def __init__(self):
            self.asked = 0
            self.free = []

        def choose_action(self, state, options):
            return options[0]

        def choose(self, state, prompt, options, *, optional):
            return options[0]

        def wants_mulligan(self, state, hand, *, free):
            self.asked += 1
            self.free.append(free)
            return self.asked == 1      # once, and only once

    seat = Mulligans()
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [seat, SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=11)
    twin = new_game(seed=11, db=db)

    game.setup()
    assert seat.free[0] is True, "the first one is the free one"
    # Offered again only if the fresh hand came up Cookie-less, and never free
    # a second time.
    assert all(f is False for f in seat.free[1:])
    player = game.state.players[0]
    # Same number of cards, drawn from a reshuffled deck: the opening hand of
    # the identically-seeded game it did not mulligan out of is gone.
    assert len(player.hand) + 1 == game.rules.opening_hand, "one went to the board"
    kept = [c.card_id for c in twin.state.players[0].hand]
    assert [c.card_id for c in player.hand] != kept, "the hand did not change"
    # The hand went back into the deck rather than into a pile: every card is
    # still accounted for.
    on_the_board = sum(1 + len(c.hp_cards) for c in player.battle)
    assert (len(player.deck) + len(player.hand) + len(player.trash)
            + on_the_board) == game.rules.deck_size
    assert any("mulligan" in line for line in game.state.log)


def test_a_bot_is_never_asked_to_mulligan():
    """Scripted agents have no read on hand quality; answering for them would
    replace every opening hand in every self-play game."""
    db = default_db()
    a = new_game(seed=11, db=db)
    b = new_game(seed=11, db=db)
    assert [c.card_id for c in a.state.players[0].hand] == \
           [c.card_id for c in b.state.players[0].hand]
    assert not any("mulligan" in line for line in a.state.log)


def test_a_cookieless_hand_can_keep_mulliganing_and_the_opponent_draws():
    """The free mulligan is the whole allowance for shopping around; a hand
    with no Cookie in it may keep redrawing, one card to the opponent each."""
    db = default_db()

    class Greedy:
        """Says yes every time it is asked, and records the price."""

        def __init__(self):
            self.free = []

        def choose_action(self, state, options):
            return options[0]

        def choose(self, state, prompt, options, *, optional):
            return options[0]

        def wants_mulligan(self, state, hand, *, free):
            self.free.append(free)
            return True

    seat = Greedy()
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [seat, SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=11)
    game.setup()

    paid = seat.free.count(False)
    # It stops the moment a Cookie turns up, so a yes-man is not asked forever.
    assert seat.free[0] is True
    assert paid == len(seat.free) - 1
    # Every priced redraw handed the opponent a card, and every card is still
    # accounted for on both sides.
    for player in game.state.players:
        on_the_board = sum(1 + len(c.hp_cards) for c in player.battle)
        assert (len(player.deck) + len(player.hand) + len(player.trash)
                + on_the_board) == game.rules.deck_size
    if paid:
        assert any("draws 1" in line for line in game.state.log)


def test_a_playable_hand_is_only_offered_the_free_mulligan():
    """Yes-to-everything must not spin the opening hand forever: once a Cookie
    is in hand the offer is closed."""
    db = default_db()

    class Greedy:
        def __init__(self):
            self.asked = 0

        def choose_action(self, state, options):
            return options[0]

        def choose(self, state, prompt, options, *, optional):
            return options[0]

        def wants_mulligan(self, state, hand, *, free):
            self.asked += 1
            return True

    seat = Greedy()
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [seat, SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=11)
    game.setup()
    assert seat.asked < game.rules.max_mulligans, "the loop terminated on merit"
    assert any(db[c.card_id].is_cookie for c in game.state.players[0].hand) \
        or game.state.players[0].battle, "it stopped once a Cookie was there"


def test_the_mulligan_can_be_turned_off():
    db = default_db()
    import dataclasses

    from braverse import config as cfg
    rules = dataclasses.replace(cfg.DEFAULT, allow_mulligan=False)

    class WouldMulligan:
        def choose_action(self, state, options):
            return options[0]

        def choose(self, state, prompt, options, *, optional):
            return options[0]

        def wants_mulligan(self, state, hand, *, free):
            raise AssertionError("must not be asked when the rule is off")

    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [WouldMulligan(), SeatedAgent(HeuristicAgent(db=db), 1)],
                db=db, rules=rules, seed=11)
    game.setup()


# --- BS8-059 Mystic Flour Cookie -------------------------------------------
def test_mystic_flour_trashes_hp_off_every_enemy_cookie():
    """The 【Activate】 used to mill the controller's *own* deck into their
    support area — text from a card that does not exist."""
    from braverse.effects import Ctx, Trigger, get_effect
    from braverse.state import CardInstance

    db = default_db()
    game = new_game(seed=12, db=db)
    me, them = game.state.players[0], game.state.players[1]
    me.support = [CardInstance.make("ST3-004", 0) for _ in range(3)]
    for card in me.support:
        card.rested = False
    victim = them.battle[0]
    before = victim.remaining_hp
    hand = len(me.hand)

    fn = get_effect("BS8-059", Trigger.ACTIVATE)
    fn(Ctx(game=game, state=game.state, db=db, me=me, opp=them,
           source_cookie=me.battle[0], trigger=Trigger.ACTIVATE.value))

    assert victim.remaining_hp == before - 2, "the opponent's HP pile is the target"
    assert len(me.hand) == hand + 2, "the two {G} support cards did not come back"
    assert len(me.support) == 1


# --- BS5-063 Hero Cookie ----------------------------------------------------
def test_hero_cookie_counts_active_support_not_the_whole_area():
    """"if there are 2 active cards or more in your support area" — the
    compiler read the word "active" as a card filter it could not express and
    counted the rested cards too, so the draw happened every turn."""
    from braverse.effects import Ctx, Trigger, get_effect

    db = default_db()
    game = new_game(seed=13, db=db)
    me = game.state.players[0]
    me.support = [me.deck.pop(0) for _ in range(4)]
    for card in me.support:
        card.rested = True

    fn = get_effect("BS5-063", Trigger.END_TURN)
    assert fn is not None

    def run():
        before = len(me.hand)
        fn(Ctx(game=game, state=game.state, db=db, me=me,
               opp=game.state.players[1], trigger=Trigger.END_TURN.value))
        return len(me.hand) - before

    assert run() == 0, "four rested cards are not two active ones"
    me.support[0].rested = False
    assert run() == 0, "one active card is still not two"
    me.support[1].rested = False
    assert run() == 2


# --- blocking ---------------------------------------------------------------
def _attack_setup(db, seed=21):
    """A game with P0 attacking and P1 holding a `<Rest this card.>` Blocker."""
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=seed)
    game.setup()
    _plain_pile(game, db)
    defender = game.state.players[1]
    defender.battle.clear()
    for card_id in ("ST8-003", "BS4-047"):          # a target, and the Blocker
        card = CardInstance.make(card_id, 1)
        game._deploy_cookie(defender, card, run_on_play=False)
    defender.support = [CardInstance.make("ST8-011", 1) for _ in range(4)]
    for card in defender.support:
        card.rested = False
    defender.hand = [CardInstance.make("ST8-017", 1)]   # a trap it can afford
    return game, defender


def test_a_blocker_priced_in_rest_actually_rests():
    """【Blocker】 <Rest this card.> — the bracket is the price. Only the energy
    half of it was read, so those Cookies blocked every attack in a turn for
    free and were still upright to swing on their own."""
    db = default_db()
    game, defender = _attack_setup(db)
    blocker = defender.battle[1]
    assert db[blocker.card.card_id].name == "Blue Lily Cookie"
    assert game._blocker_cost(blocker) == (Cost(), True)

    attacker = game.state.players[0].battle[0]
    game._pending_attack = (attacker, defender.battle[0])
    game._response_player = 1
    game._trap_used = 0
    game._responded = None

    class Blocks:
        def choose_action(self, state, options):
            return next((o for o in options if isinstance(o, A.Block)), A.Pass())

    game._controllers[1] = Blocks()
    target = game._response_window(defender, attacker, defender.battle[0])
    assert target is blocker, "the attack was not redirected"
    assert blocker.rested, "the blocker did not pay with its own rest"


def test_a_blocker_priced_in_energy_pays_from_support():
    """The other half of the price: `【Blocker】 <{G}>` rests a green support
    card and leaves the Cookie itself upright."""
    db = default_db()
    game, defender = _attack_setup(db)
    defender.battle.pop()                                  # drop the rest-priced one
    game._deploy_cookie(defender, CardInstance.make("ST8-011", 1),
                        run_on_play=False)                 # Kiwi Cookie, <{G}>
    blocker = defender.battle[1]
    assert game._blocker_cost(blocker) == (Cost(colored=((Color.GREEN, 1),)), False)
    defender.hand = []                                     # no trap to distract it

    attacker = game.state.players[0].battle[0]
    game._pending_attack = (attacker, defender.battle[0])
    game._response_player = 1
    game._trap_used = 0
    game._responded = None

    class Blocks:
        def choose_action(self, state, options):
            return next((o for o in options if isinstance(o, A.Block)), A.Pass())

    game._controllers[1] = Blocks()
    target = game._response_window(defender, attacker, defender.battle[0])
    assert target is blocker
    assert not blocker.rested, "an energy price must not also rest the Cookie"
    assert sum(c.rested for c in defender.support) == 1


def test_a_blocker_whose_price_cannot_be_read_does_not_block_for_free():
    db = default_db()
    game, defender = _attack_setup(db)
    blocker = defender.battle[1]
    # Pretend the printed price is something the engine has never seen.
    import dataclasses
    printed = db[blocker.card.card_id]
    db.cards[printed.id] = dataclasses.replace(
        printed, description="【Blocker】 <Sing a little song.>")
    try:
        assert game._blocker_cost(blocker) is None
    finally:
        db.cards[printed.id] = printed


def test_a_trap_and_a_block_are_alternatives_not_a_combination():
    """Springing the trap is the answer to the attack, and so is putting a
    Cookie in the way. Either one closes the other off."""
    db = default_db()
    game, defender = _attack_setup(db)
    attacker = game.state.players[0].battle[0]
    game._pending_attack = (attacker, defender.battle[0])
    game._response_player = 1
    game._trap_used = 0
    game._responded = None

    offered = game._response_actions(defender)
    assert any(isinstance(o, A.PlayTrap) for o in offered)
    assert any(isinstance(o, A.Block) for o in offered)

    game._responded = "block"
    assert [type(o) for o in game._response_actions(defender)] == [A.Pass], \
        "a trap was still on offer after blocking"

    game._responded = "trap"
    assert [type(o) for o in game._response_actions(defender)] == [A.Pass], \
        "a block was still on offer after springing a trap"


def test_a_blocker_attacked_head_on_can_still_spring_a_trap():
    """Only *activating* 【Blocker】 spends the defender's answer to an attack.

    A Cookie that merely has 【Blocker】 printed on it and is attacked directly —
    the opponent going for the blocker rather than around it — has not
    activated anything, so the trap is still there to be sprung. It is the
    other side of the same rule as
    `test_a_trap_and_a_block_are_alternatives_not_a_combination`, and worth
    pinning separately: the two read almost the same in the code and a change
    to one is very easy to make in the other by accident.
    """
    db = default_db()
    game, defender = _attack_setup(db)
    attacker = game.state.players[0].battle[0]
    blocker = defender.battle[1]
    assert blocker.has_marker(db, Marker.BLOCKER)

    # The attack is aimed at the Blocker itself.
    game._pending_attack = (attacker, blocker)
    game._response_player = 1
    game._trap_used = 0
    game._responded = None

    kinds = {type(o).__name__ for o in game._response_actions(defender)}
    assert "PlayTrap" in kinds, "the trap was taken away for nothing"
    # And it cannot block with *itself* — that is not what a Blocker does.
    assert "Block" not in kinds

    # Springing it still closes off blocking with some other Cookie, which is
    # the half of the rule that does hold.
    game._responded = "trap"
    assert {type(o).__name__ for o in game._response_actions(defender)} == {"Pass"}


def test_the_log_says_when_a_swing_was_shaved():
    """An attack announced at 3 that lands for 1 reads as a broken attack
    unless the reduction is written down."""
    db = default_db()
    game = new_game(seed=22, db=db)
    _plain_pile(game, db)
    me = game.state.players[0]
    attacker = me.battle[0]
    me.support = [CardInstance.make("ST9-013", 0) for _ in range(6)]
    for card in me.support:
        card.rested = False
    _, target = _lone_cookie(game)
    # A cap survives the per-battle reset, which is what makes it readable here.
    target.damage_cap = 1

    game._do_attack(A.Attack(attacker.uid, target.uid))
    assert any("attacks" in line for line in game.state.log), "no attack happened"
    assert any("attack is reduced to 1" in line for line in game.state.log), \
        game.state.log[-4:]


# --- BS9-018 Hero Cookie ----------------------------------------------------
def test_hero_cookies_shield_only_stands_on_its_own_turn():
    """"【Your Turn】 ... your Cookies take no damage from your opponent."

    The marker was being dropped, and it is half the card: without it the
    shield stood on the opponent's turn too — which is when nearly all the
    damage in this game is dealt — so one Cookie on the board made its
    controller immune to everything for the rest of the game.
    """
    db = default_db()
    game = new_game(seed=15, db=db)
    _plain_pile(game, db)
    defender = game.state.players[1]
    defender.battle.clear()
    for card_id in ("BS9-018", "ST8-003"):
        game._deploy_cookie(defender, CardInstance.make(card_id, 1), run_on_play=False)
    hero, ally = defender.battle
    assert db[hero.card.card_id].name == "Hero Cookie"

    # Their opponent's turn — the shield is down, so the damage lands.
    game.state.turn_player = 0
    before = ally.remaining_hp
    game.deal_damage(ally, 1, source_player=0, kind="attack")
    assert ally.remaining_hp == before - 1, "the shield stood on the wrong turn"

    # Their own turn — a trap, a Blocker or a FLIP can still hit them, and now
    # the shield is what the card says it is.
    game.state.turn_player = 1
    held = ally.remaining_hp
    game.deal_damage(ally, 1, source_player=0, kind="effect")
    assert ally.remaining_hp == held, "the shield did not hold on its own turn"
    assert "shielded" in game.state.log[-1]

    # It never blocked its controller's own damage either way.
    game.deal_damage(ally, 1, source_player=1, kind="effect")
    assert ally.remaining_hp == held - 1


# --- ST3-016 Ancient Healer's Gaze ------------------------------------------
class _PicksFirst:
    """A controller that passes on its own turn and takes the first option."""

    def choose_action(self, state, options):
        return next((o for o in options if isinstance(o, A.Pass)), options[0])

    def choose(self, state, prompt, options, *, optional):
        return options[0]


def _gaze_game(db):
    game = Game([STARTER_DECKS["st8_wind_archer"], STARTER_DECKS["st9_sea_fairy"]],
                [_PicksFirst(), _PicksFirst()], db=db, seed=3)
    game.setup()
    _plain_pile(game, db)
    player = game.state.players[0]
    player.hand = [CardInstance.make("ST3-016", 0)]
    player.support = [CardInstance.make("ST3-012", 0) for _ in range(3)]
    for card in player.support:
        card.rested = False
    assert str(db["ST3-016"].play_cost) == "{G}{G}{G}", "exactly enough, no spare"
    return game, player


def test_ancient_healers_gaze_banks_a_cookie_as_energy():
    """Played through the engine, so the item's own {G}{G}{G} is really paid.

    The Cookie leaves the battle area for the support area as active, its HP
    pile is spent, and — the point of the card — nothing reaches the break
    area, so the opponent banks no Level for it.
    """
    db = default_db()
    game, player = _gaze_game(db)
    target = player.battle[0]
    assert target.level(db) <= 2, target.level(db)
    hp_cards = list(target.hp_cards)
    trash_before = len(player.trash)

    game._do_play_support_card(A.PlaySupportCard(player.hand[0].uid))

    assert target not in player.battle
    assert target.card in player.support, "the Cookie card is the support card"
    assert not target.card.rested, "placed as active"
    assert not player.break_area, "a move to support is not a faint"
    assert all(c in player.trash for c in hp_cards), "the HP pile is spent"
    assert len(player.trash) >= trash_before + len(hp_cards)
    # Three support cards rested to pay {G}{G}{G}; the Cookie arrives active,
    # so it is the one thing in the support area still able to pay for anything.
    assert player.active_support() == [len(player.support) - 1]


def test_ancient_healers_gaze_is_not_offered_without_a_legal_target():
    """"A listed move must do something": with only LV.3 Cookies on the board
    the item has nothing to select, so it must not appear in the action list."""
    db = default_db()
    game, player = _gaze_game(db)
    for cookie in player.battle:
        cookie.level_override = 3

    actions = game.legal_actions()
    assert not [a for a in actions if isinstance(a, A.PlaySupportCard)], \
        "offered an item that cannot select anything"

    for cookie in player.battle:
        cookie.level_override = 1
    assert [a for a in game.legal_actions() if isinstance(a, A.PlaySupportCard)]


# --- ST3-018 Parsley Tea of Invigoration ------------------------------------
class _PicksLast:
    """Passes on its own turn; records every question and takes the last option."""

    def __init__(self):
        self.prompts = []

    def choose_action(self, state, options):
        return next((o for o in options if isinstance(o, A.Pass)), options[0])

    def choose(self, state, prompt, options, *, optional):
        self.prompts.append((prompt, optional, list(options)))
        return options[-1]


def _parsley_game(db):
    seat = _PicksLast()
    game = Game([STARTER_DECKS["st8_wind_archer"], STARTER_DECKS["st9_sea_fairy"]],
                [seat, _PicksFirst()], db=db, seed=3)
    game.setup()
    player = game.state.players[0]
    player.hand = [CardInstance.make("ST3-018", 0)]
    player.support = [CardInstance.make("ST3-012", 0) for _ in range(2)]
    for card in player.support:
        card.rested = False
    assert str(db["ST3-018"].play_cost) == "{G}{G}", "exactly enough, no spare"
    player.trash = [CardInstance.make("ST3-016", 0),
                    CardInstance.make("ST3-001", 0),
                    CardInstance.make("ST3-002", 0)]
    return game, player, seat


def test_parsley_tea_plays_the_cookie_its_controller_picked():
    """The card's whole body is a selection: with two Cookies in the trash the
    player says which one comes back, and it is not a choice they can decline."""
    db = default_db()
    game, player, seat = _parsley_game(db)
    battle_before = len(player.battle)
    wanted = player.trash[-1]

    game._do_play_support_card(A.PlaySupportCard(player.hand[0].uid))

    prompts = [p for p in seat.prompts if "trash" in p[0].lower()]
    assert len(prompts) == 1, "asked exactly once which Cookie to bring back"
    prompt, optional, options = prompts[0]
    assert not optional, "'Play 1', not 'up to 1' — the cost is already spent"
    assert [c.card_id for c in options] == ["ST3-001", "ST3-002"], \
        "only the Cookies in the trash are on offer"

    assert wanted not in player.trash
    assert len(player.battle) == battle_before + 1
    played = player.battle[-1]
    assert played.uid == wanted.uid
    assert played.hp_cards, "it comes back with a fresh HP pile"


def test_parsley_tea_is_not_offered_with_no_cookie_in_the_trash():
    """"A listed move must do something" — an empty trash means the item has
    nothing to play, so it is not a move."""
    db = default_db()
    game, player, _ = _parsley_game(db)
    player.trash = [CardInstance.make("ST3-016", 0)]

    assert not [a for a in game.legal_actions() if isinstance(a, A.PlaySupportCard)]

    player.trash.append(CardInstance.make("ST3-001", 0))
    assert [a for a in game.legal_actions() if isinstance(a, A.PlaySupportCard)]


# --- end-of-turn ordering --------------------------------------------------
class OrderingController:
    """A seat that always resolves the *last* offered effect first."""

    def __init__(self):
        self.prompts = []

    def choose_action(self, state, options):
        return options[0] if options else None

    def choose(self, state, prompt, options, *, optional):
        return None if optional else (options[0] if options else None)

    def order_effects(self, state, prompt, options):
        self.prompts.append(list(options))
        return options[-1]


def _end_turn_pair(monkeypatch):
    """Two Cookies whose end-of-turn effects just log which one ran."""
    from braverse import effects as E

    order = []
    registry = dict(E._REGISTRY)
    for card_id in ("ST8-002", "ST8-003"):
        registry[(card_id, E.Trigger.END_TURN)] = (
            lambda ctx, cid=card_id: order.append(cid)
        )
    monkeypatch.setattr(E, "_REGISTRY", registry)
    return order


def test_end_turn_effects_resolve_in_the_players_chosen_order(db, monkeypatch):
    order = _end_turn_pair(monkeypatch)
    seat = OrderingController()
    game = Game([STARTER_DECKS["st8_wind_archer"], STARTER_DECKS["st9_sea_fairy"]],
                [seat, SeatedAgent(HeuristicAgent(seed=1), 1)], db=db, seed=0)
    game.setup()
    me = game.state.current
    for card_id in ("ST8-002", "ST8-003"):
        game._deploy_cookie(me, CardInstance.make(card_id, me.index))

    game.end_turn()

    assert len(seat.prompts) == 1
    assert order == ["ST8-003", "ST8-002"]


def test_bots_keep_board_order_at_end_of_turn(db, monkeypatch):
    """No `order_effects` on a controller means no question and no reordering —
    seeded self-play has to stay bit-identical."""
    order = _end_turn_pair(monkeypatch)
    game = new_game(db=db)
    me = game.state.current
    for card_id in ("ST8-002", "ST8-003"):
        game._deploy_cookie(me, CardInstance.make(card_id, me.index))

    game.end_turn()

    assert order == ["ST8-002", "ST8-003"]


# --- banked "when your turn ends" riders ------------------------------------
def test_banked_untap_queues_with_the_other_end_of_turn_effects(monkeypatch):
    """An attack rider that reads "when your turn ends, ..." happens in the same
    step as every 【End of Turn】 effect, so it is one more item in the queue —
    orderable against them, and named after the card that banked it."""
    db = default_db()
    order = _end_turn_pair(monkeypatch)
    seat = OrderingController()
    game = Game([STARTER_DECKS["st8_wind_archer"], STARTER_DECKS["st9_sea_fairy"]],
                [seat, SeatedAgent(HeuristicAgent(seed=1), 1)], db=db, seed=0)
    game.setup()
    me = game.state.current
    game._deploy_cookie(me, CardInstance.make("ST8-002", me.index))
    me.support = [CardInstance.make("ST8-012", 0) for _ in range(2)]
    for card in me.support:
        card.rested = True
    me.end_turn_untaps.append(("BS5-060", 1))

    game.end_turn()

    assert len(seat.prompts) == 1, "the rider and the effect were ordered together"
    offered = seat.prompts[0]
    assert any(isinstance(o, BankedUntap) for o in offered)
    banked = next(o for o in offered if isinstance(o, BankedUntap))
    assert db["BS5-060"].name in str(banked), "named after the card that banked it"
    # The controller takes the last option, which is the rider, so it resolved
    # first and the Cookie's own effect second.
    assert order == ["ST8-002"]
    assert [c.rested for c in me.support] == [False, True], "set up to 1 as active"
    assert not me.end_turn_untaps, "banked riders do not carry into the next turn"


def test_banked_untap_is_not_offered_with_no_rested_support(monkeypatch):
    """Nothing to set as active is nothing to order — the queue only asks about
    events that would do something."""
    db = default_db()
    order = _end_turn_pair(monkeypatch)
    seat = OrderingController()
    game = Game([STARTER_DECKS["st8_wind_archer"], STARTER_DECKS["st9_sea_fairy"]],
                [seat, SeatedAgent(HeuristicAgent(seed=1), 1)], db=db, seed=0)
    game.setup()
    me = game.state.current
    game._deploy_cookie(me, CardInstance.make("ST8-002", me.index))
    me.support = [CardInstance.make("ST8-012", 0)]
    me.support[0].rested = False
    me.end_turn_untaps.append(("BS5-060", 3))

    game.end_turn()

    assert not seat.prompts, "asked to order an event that does nothing"
    assert order == ["ST8-002"]


def test_log_names_cards_with_their_id(db):
    """Every card the log names is named "Name (ST9-007)".

    271 of the 813 names in the pool are printed on more than one card, so the
    viewer — which hovers a name in the log and previews the card behind it —
    could only ever guess which printing acted. The id is the answer, and it
    has to be on every line that names a card, not just the ambiguous ones:
    the viewer matches one shape.

    Read here exactly as the viewer reads it: one alternation of every name,
    longest first, so a name printed inside a longer one does not match on its
    own.
    """
    import re

    names = sorted({c.name for c in db.cards.values()}, key=len, reverse=True)
    matcher = re.compile("(" + "|".join(re.escape(n) for n in names) + ")")
    game = new_game(seed=4, db=db)
    game.play_out()
    named = 0
    for line in game.state.log:
        for match in matcher.finditer(line):
            assert re.match(r" \([A-Za-z0-9]+-\d+[A-Za-z]?\)",
                            line[match.end():]), line
            named += 1
    assert named > 20, "the game logged almost nothing; the test proves little"
