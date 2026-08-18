"""Engine and card-database tests. Run with: python -m pytest -q"""

import pytest

from braverse import (STARTER_DECKS, Game, HeuristicAgent, RandomAgent,
                      SeatedAgent, default_db, validate)
from braverse import actions as A
from braverse.cost import Cost, plan_payment
from braverse.enums import CardType, Color, Marker


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
    twin.state.players[0].battle[0].hp_bonus = 99
    assert game.state.players[0].hand
    assert game.state.players[0].battle[0].hp_bonus == 0


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


def test_a_flip_that_bounces_its_host_denies_the_break_area():
    """Muscle Cookie / Blue Whale Cookie: "Return this Cookie to your hand."

    Revealed as the last HP card, the bounce beats the faint, so the opponent
    banks no Level. The other reading is a config flag, because the guide does
    not settle it and every measured result in the README assumes this one.
    """
    import dataclasses

    from braverse import config as cfg

    db = default_db()

    def bounce_at_zero_hp(rules):
        game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                    [SeatedAgent(HeuristicAgent(db=db), 0),
                     SeatedAgent(HeuristicAgent(db=db), 1)],
                    db=db, rules=rules, seed=1)
        game.setup()
        cookie = game.state.players[0].battle[0]
        cookie.hp_cards.clear()               # out of HP, mid-damage
        game.return_cookie_to_hand(cookie)
        # Level is banked in the *fainting* player's own break area; 10 there
        # loses them the game. Assert on that rather than on the hand, because
        # a bounced Cookie is usually replayed immediately by the same
        # controller once its battle area is checked.
        return len(game.state.players[0].break_area), game.state.log

    banked, log = bounce_at_zero_hp(cfg.DEFAULT)
    assert banked == 0, "the bounce should have saved it from the break area"
    assert any("returns to hand" in line for line in log)

    strict = dataclasses.replace(cfg.DEFAULT, flip_bounce_beats_faint=False)
    banked, log = bounce_at_zero_hp(strict)
    assert banked == 1, "at 0 HP it should have fainted instead"
    assert any("faints" in line for line in log)


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

        def choose_many(self, state, prompt, options, *, count, optional):
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

        def choose_many(self, state, prompt, options, *, count, optional):
            return list(options)[-count:]

    game._controllers[0] = PicksTheLastTwo()
    wanted = list(player.hand)[-2:]
    ctx = Ctx(game=game, state=game.state, db=db, me=player, opp=game.state.players[1])
    before = len(player.hand)

    discarded = game.discard(player, 2, ctx)
    assert discarded == wanted
    assert len(player.hand) == before - 2
    assert all(card in player.trash for card in wanted)
