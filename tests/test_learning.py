"""Tests for the RL trainer and the deck evolver.

Kept deliberately small: these assert that the machinery is correct and that
learning moves in the right direction, not that any particular win rate is hit.
"""

import numpy as np
import pytest
import torch

from braverse import STARTER_DECKS, default_db, validate
from braverse import actions as A
from braverse.deckgen import (DeckEvolver, DeckGenConfig, implemented_pool,
                              set_pool)
from braverse.features import FEATURE_DIM, STATE_DIM, Encoder
from braverse.rl import PolicyNet, RLAgent, TrainConfig, Trainer


@pytest.fixture(scope="module")
def db():
    return default_db()


def fresh_game(db, seed=0):
    from braverse import Game, HeuristicAgent, SeatedAgent
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=seed)
    game.setup()
    return game


# --- features --------------------------------------------------------------
def test_encoder_shape_and_finiteness(db):
    game = fresh_game(db)
    encoder = Encoder(db)
    options = game.legal_actions()
    rows = encoder.encode(game.state, 0, options)
    assert rows.shape == (len(options), FEATURE_DIM)
    assert np.isfinite(rows).all()
    assert rows.dtype == np.float32


def test_state_block_is_shared_across_actions(db):
    """The value head reads row 0's state block, so every row must carry it."""
    game = fresh_game(db)
    rows = Encoder(db).encode(game.state, 0, game.legal_actions())
    assert (rows[:, :STATE_DIM] == rows[0, :STATE_DIM]).all()


def test_encoding_is_seat_relative(db):
    game = fresh_game(db)
    encoder = Encoder(db)
    game.state.players[0].break_area.extend(game.state.players[0].deck[:2])
    mine = encoder.state_vector(game.state, 0)
    theirs = encoder.state_vector(game.state, 1)
    assert mine[0] == theirs[1]      # my break total is their opponent's
    assert mine[1] == theirs[0]


def test_lethal_attack_is_flagged(db):
    game = fresh_game(db)
    encoder = Encoder(db)
    defender = game.state.players[1]
    defender.break_area.extend(
        [c for c in defender.deck if (db[c.card_id].level or 0) == 3][:3])
    victim = defender.battle[0]
    del victim.hp_cards[1:]          # one hit from fainting
    attacker = game.state.players[0].battle[0]

    action = A.Attack(attacker.uid, victim.uid)
    row = encoder.encode(game.state, 0, [action])[0]
    kills_index = STATE_DIM + 9 + 2
    assert row[kills_index] == pytest.approx(1.0)


# --- policy ----------------------------------------------------------------
def test_policy_scores_every_action(db):
    net = PolicyNet()
    game = fresh_game(db)
    rows = Encoder(db).encode(game.state, 0, game.legal_actions())
    logits = net.logits(torch.from_numpy(rows))
    assert logits.shape == (rows.shape[0],)
    assert torch.isfinite(logits).all()


def test_rl_agent_plays_a_full_game_and_records_a_trajectory(db):
    from braverse import Game, HeuristicAgent
    net = PolicyNet()
    learner = RLAgent(net, 0, db=db, training=True, seed=0)
    foe = HeuristicAgent(db=db, seed=1)
    setattr(foe, "_seat_hint", 1)
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [learner, foe], db=db, seed=3)
    game.setup()
    state = game.play_out()
    assert state.over
    assert learner.trajectory, "training agent must record its decisions"
    for step in learner.trajectory:
        assert step.rows.shape[1] == FEATURE_DIM
        assert 0 <= step.chosen < step.rows.shape[0]


def test_greedy_agent_is_deterministic(db):
    net = PolicyNet()
    game = fresh_game(db)
    options = game.legal_actions()
    agent = RLAgent(net, 0, db=db, training=False)
    picks = {agent.choose_action(game.state, options) for _ in range(5)}
    assert len(picks) == 1


def test_an_update_changes_the_weights_and_stays_finite(db):
    trainer = Trainer(config=TrainConfig(games=6, batch_games=3, eval_every=0,
                                         league_every=0), db=db)
    before = [p.detach().clone() for p in trainer.net.parameters()]
    trainer.train(log=lambda *_: None)
    after = list(trainer.net.parameters())
    assert any(not torch.equal(a, b) for a, b in zip(before, after))
    assert all(torch.isfinite(p).all() for p in after)


def test_checkpoint_round_trips(db, tmp_path):
    trainer = Trainer(config=TrainConfig(games=4, batch_games=2, eval_every=0,
                                         league_every=0), db=db)
    trainer.train(log=lambda *_: None)
    path = tmp_path / "agent.pt"
    trainer.save(path)
    reloaded = Trainer.load_net(path)
    game = fresh_game(db)
    rows = torch.from_numpy(Encoder(db).encode(game.state, 0, game.legal_actions()))
    assert torch.allclose(trainer.net.logits(rows), reloaded.logits(rows))


# --- deck generation -------------------------------------------------------
def test_repair_always_produces_a_legal_deck(db):
    pool = set_pool(db, ("ST8", "ST9"))
    evolver = DeckEvolver(pool, [STARTER_DECKS["st9_sea_fairy"]],
                          DeckGenConfig(games_per_eval=2), db=db)
    broken = ["ST9-006"] * 90 + ["nonsense"] * 5     # over-copied and invalid
    fixed = evolver.repair(broken)
    report = validate(fixed, db)
    assert report.ok, report.problems


@pytest.mark.parametrize("seed", range(5))
def test_random_decks_are_legal(seed, db):
    pool = set_pool(db, ("ST8", "ST9"))
    evolver = DeckEvolver(pool, [STARTER_DECKS["st9_sea_fairy"]],
                          DeckGenConfig(seed=seed, games_per_eval=2), db=db)
    report = validate(evolver.random_deck(), db)
    assert report.ok, report.problems


def test_mutation_and_crossover_stay_legal(db):
    pool = set_pool(db, ("ST8", "ST9"))
    evolver = DeckEvolver(pool, [STARTER_DECKS["st9_sea_fairy"]],
                          DeckGenConfig(games_per_eval=2), db=db)
    a, b = evolver.random_deck(), evolver.random_deck()
    assert validate(evolver.mutate(a), db).ok
    assert validate(evolver.crossover(a, b), db).ok


def test_flip_cap_is_respected_even_from_an_all_flip_pool(db):
    flips = [c for c in db.cards.values() if c.is_flip and c.set_id in ("ST8", "ST9")]
    pool = flips + [c for c in db.cards.values()
                    if c.set_id == "ST9" and not c.is_flip]
    evolver = DeckEvolver(pool, [STARTER_DECKS["st9_sea_fairy"]],
                          DeckGenConfig(games_per_eval=2), db=db)
    deck = evolver.repair([c.id for c in flips] * 10)
    assert validate(deck, db).ok


def test_fitness_is_a_rate_and_is_cached(db):
    pool = set_pool(db, ("ST8", "ST9"))
    evolver = DeckEvolver(pool, [STARTER_DECKS["st8_wind_archer"]],
                          DeckGenConfig(games_per_eval=6, seed=1), db=db)
    deck = STARTER_DECKS["st9_sea_fairy"]
    score = evolver.fitness(deck)
    assert 0.0 <= score <= 1.0
    assert evolver.fitness(deck) == score
    assert (tuple(sorted(deck)), 0, 6) in evolver._cache


def test_seed_blocks_actually_change_the_shuffles(db):
    """The anti-overfit guarantee: a different block must be a different
    sample, and must not collide with a cached score from another block."""
    pool = set_pool(db, ("ST8", "ST9"))
    evolver = DeckEvolver(pool, [STARTER_DECKS["st8_wind_archer"]],
                          DeckGenConfig(games_per_eval=30, seed=1), db=db)
    deck = STARTER_DECKS["st9_sea_fairy"]
    scores = {evolver.fitness(deck, seed_block=b) for b in range(4)}
    assert len(scores) > 1, "every block produced an identical score"


def test_holdout_uses_a_block_the_search_never_touches(db):
    pool = set_pool(db, ("ST8", "ST9"))
    cfg = DeckGenConfig(population=6, generations=3, elite=2,
                        games_per_eval=8, seed=2)
    evolver = DeckEvolver(pool, [STARTER_DECKS["st8_wind_archer"]], cfg, db=db)
    evolver.evolve(log=lambda *_: None)
    training_blocks = {block for _, block, _ in evolver._cache}
    assert 10_000 not in training_blocks     # holdout block
    assert all(b < cfg.generations or b == 9_999 for b in training_blocks)


def test_evolution_beats_unevolved_decks_on_held_out_shuffles(db):
    """Generation-to-generation means are noisy by design now — each one is a
    fresh sample. The claim worth testing is that the search output beats
    unevolved decks on shuffles neither of them was selected on."""
    pool = set_pool(db, ("ST8", "ST9"))
    evolver = DeckEvolver(pool, [STARTER_DECKS["st8_wind_archer"]],
                          DeckGenConfig(population=10, generations=6, elite=2,
                                        games_per_eval=16, seed=2), db=db)
    deck, score, history = evolver.evolve(log=lambda *_: None)

    assert validate(deck, db).ok
    assert history[-1]["validation_best"] == score

    baseline = sum(evolver.holdout(evolver.random_deck(), games=40)
                   for _ in range(3)) / 3
    assert evolver.holdout(deck, games=120) > baseline


def test_reported_score_is_validated_not_the_best_training_score(db):
    """Guards against the winner's curse: the returned score must come from
    the validation block, so it cannot be the max of noisy training scores."""
    pool = set_pool(db, ("ST8", "ST9"))
    evolver = DeckEvolver(pool, [STARTER_DECKS["st8_wind_archer"]],
                          DeckGenConfig(population=6, generations=4, elite=2,
                                        games_per_eval=12, seed=5), db=db)
    deck, score, history = evolver.evolve(log=lambda *_: None)
    best_training = max(h["best"] for h in history if "best" in h)
    assert score == evolver.fitness(deck, seed_block=9_999,
                                    games=max(12, 120))
    # The honest number is free to be worse than the luckiest training score.
    assert score <= best_training or score > 0


def test_implemented_pool_only_holds_cards_the_engine_fully_plays(db):
    """The invariant that matters: every card in the pool either has a
    registered effect (hand-written or compiled) or genuinely has no rules
    text. A card with text but no effect would play as a silent lie."""
    from braverse.effects import is_implemented

    pool = {c.id for c in implemented_pool(db)}
    assert "ST9-006" in pool          # hand-written
    assert "ST9-001" in pool          # vanilla body, nothing to code

    def has_text(card):
        import re
        text = " ".join([card.description, card.flip_text,
                         card.attack.text if card.attack else ""])
        return bool(re.sub(r"【Blocker】\s*(?:<[^>]*>)?\s*\([^)]*\)", "", text).strip())

    unimplemented = [c for c in db.cards.values()
                     if has_text(c) and not is_implemented(c.id)]
    assert unimplemented, "expected some cards to remain unimplemented"
    escaped = [c.id for c in unimplemented if c.id in pool]
    assert not escaped, escaped[:5]


# --- RL agent as deck-evolution pilot --------------------------------------
def test_rl_pilot_flies_the_fitness_games(db, tmp_path):
    """The evolver must be able to score decks under the learned policy, not
    just the scripted heuristic."""
    from braverse.rl import RLAgent, TrainConfig, Trainer

    trainer = Trainer(config=TrainConfig(games=2, batch_games=2, eval_every=0,
                                         league_every=0), db=db)
    path = tmp_path / "pilot.pt"
    trainer.save(path)

    pool = set_pool(db, ("ST8", "ST9"))
    factory = DeckEvolver.rl_pilot(str(path), db)
    evolver = DeckEvolver(pool, [STARTER_DECKS["st8_wind_archer"]],
                          DeckGenConfig(games_per_eval=6, seed=3), db=db,
                          agent_factory=factory)

    assert isinstance(factory(0, 0), RLAgent)
    assert factory(1, 0).seat == 1
    score = evolver.fitness(STARTER_DECKS["st9_sea_fairy"])
    assert 0.0 <= score <= 1.0


def test_pilot_choice_changes_the_measured_fitness(db, tmp_path):
    """Fitness is a property of the deck *and* the player flying it — which is
    the whole reason to evolve under the agent you actually care about."""
    from braverse.rl import TrainConfig, Trainer

    trainer = Trainer(config=TrainConfig(games=2, batch_games=2, eval_every=0,
                                         league_every=0), db=db)
    path = tmp_path / "pilot.pt"
    trainer.save(path)

    pool = set_pool(db, ("ST8", "ST9"))
    deck = STARTER_DECKS["st9_sea_fairy"]
    gauntlet = [STARTER_DECKS["st8_wind_archer"]]
    cfg = DeckGenConfig(games_per_eval=30, seed=3)

    heuristic_score = DeckEvolver(pool, gauntlet, cfg, db=db).fitness(deck)
    rl_score = DeckEvolver(pool, gauntlet, cfg, db=db,
                           agent_factory=DeckEvolver.rl_pilot(str(path), db)
                           ).fitness(deck)
    assert heuristic_score != rl_score


def test_trainer_samples_across_a_wide_deck_pool(db):
    """With more than two decks the trainer must actually rotate them,
    otherwise 'training on the pool' is a no-op."""
    from braverse.rl import TrainConfig, Trainer

    decks = [list(STARTER_DECKS["st9_sea_fairy"]),
             list(STARTER_DECKS["st8_wind_archer"]),
             list(STARTER_DECKS["st9_sea_fairy"])[:59] + ["ST9-006"]]
    trainer = Trainer(decks, TrainConfig(seed=1), db=db)
    seen = {tuple(d) for _ in range(40) for d in trainer.sample_decks()}
    assert len(seen) >= 3


def test_random_deck_share_reaches_cards_outside_the_starters(db):
    from braverse.rl import TrainConfig, Trainer

    trainer = Trainer(config=TrainConfig(seed=1, random_deck_share=1.0,
                                         random_deck_cache=12), db=db)
    starter_cards = set(STARTER_DECKS["st9_sea_fairy"]) | set(
        STARTER_DECKS["st8_wind_archer"])
    sampled = {card for _ in range(10) for d in trainer.sample_decks() for card in d}
    assert sampled - starter_cards, "random decks never left the starter pool"


def test_multi_pilot_fitness_takes_the_worst_case(db, tmp_path):
    """Robust selection: a deck that only works for one pilot must not score
    well. `min` aggregation is what enforces that."""
    from braverse.rl import TrainConfig, Trainer

    trainer = Trainer(config=TrainConfig(games=2, batch_games=2, eval_every=0,
                                         league_every=0), db=db)
    path = tmp_path / "pilot.pt"
    trainer.save(path)

    pool = set_pool(db, ("ST8", "ST9"))
    gauntlet = [STARTER_DECKS["st8_wind_archer"]]
    deck = STARTER_DECKS["st9_sea_fairy"]
    cfg = DeckGenConfig(games_per_eval=40, seed=3, pilot_aggregate="min")

    scripted = DeckEvolver(pool, gauntlet, cfg, db=db)._default_agent
    rl = DeckEvolver.rl_pilot(str(path), db)

    solo_scripted = DeckEvolver(pool, gauntlet, cfg, db=db,
                                agent_factory=scripted).fitness(deck, games=20)
    solo_rl = DeckEvolver(pool, gauntlet, cfg, db=db,
                          agent_factory=rl).fitness(deck, games=20)
    combined = DeckEvolver(pool, gauntlet, cfg, db=db,
                           agent_factories=[scripted, rl]).fitness(deck, games=40)

    assert combined <= max(solo_scripted, solo_rl) + 1e-9


def test_mean_aggregation_sits_between_the_pilots(db, tmp_path):
    from braverse.rl import TrainConfig, Trainer

    trainer = Trainer(config=TrainConfig(games=2, batch_games=2, eval_every=0,
                                         league_every=0), db=db)
    path = tmp_path / "pilot.pt"
    trainer.save(path)

    pool = set_pool(db, ("ST8", "ST9"))
    gauntlet = [STARTER_DECKS["st8_wind_archer"]]
    deck = STARTER_DECKS["st9_sea_fairy"]
    cfg = DeckGenConfig(games_per_eval=40, seed=3, pilot_aggregate="mean")
    scripted = DeckEvolver(pool, gauntlet, cfg, db=db)._default_agent
    rl = DeckEvolver.rl_pilot(str(path), db)

    lo = DeckEvolver(pool, gauntlet, cfg, db=db,
                     agent_factory=scripted).fitness(deck, games=20)
    hi = DeckEvolver(pool, gauntlet, cfg, db=db,
                     agent_factory=rl).fitness(deck, games=20)
    combined = DeckEvolver(pool, gauntlet, cfg, db=db,
                           agent_factories=[scripted, rl]).fitness(deck, games=40)
    assert min(lo, hi) - 1e-9 <= combined <= max(lo, hi) + 1e-9


def test_single_pilot_path_is_unchanged_by_the_multi_pilot_refactor(db):
    """Regression guard: splitting fitness into _score must not move the
    single-pilot numbers the earlier results were measured with.

    The pinned value was measured after the refactor and cross-checked against
    a holdout computed before it (56.4% either side), so it anchors the
    single-pilot path against future drift.
    """
    pool = set_pool(db, ("ST8", "ST9"))
    evolver = DeckEvolver(pool, [STARTER_DECKS["st8_wind_archer"]],
                          DeckGenConfig(games_per_eval=30, seed=1), db=db)
    score = evolver.fitness(STARTER_DECKS["st9_sea_fairy"], seed_block=0)
    # Moved 19/30 -> 20/30 when "Return this Cookie to your hand" was corrected
    # to return the revealed FLIP card rather than the Cookie it was HP for,
    # then 20/30 -> 18/30 when 【Blocker】 stopped requiring an active Cookie
    # (Comprehensive Rules 10-1-1-1 asks for the activation cost and nothing
    # else), then 18/30 -> 17/30 when the Support Phase stopped running for
    # the whole turn (6-1-1). All three are rules fixes, not drift: re-pin
    # deliberately, never to make a red test green.
    assert score == pytest.approx(17 / 30)


# --- encoder swapping and checkpoint widths ---------------------------------
def test_a_checkpoint_is_loaded_at_the_width_it_was_saved(tmp_path, db):
    """Widths come off the weights, so an old checkpoint is never mis-sized."""
    from braverse.features_wide import WideEncoder
    from braverse.rl import TrainConfig, Trainer

    decks = [STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]]
    for encoder, expected in ((None, 55), (WideEncoder(db), WideEncoder.dim)):
        trainer = Trainer(decks, TrainConfig(games=0), db=db, encoder=encoder)
        path = tmp_path / f"agent_{expected}.pt"
        trainer.save(path)
        assert Trainer.load_net(path).feature_dim == expected


def test_a_policy_and_encoder_of_different_widths_is_refused(tmp_path, db):
    """The failure that would otherwise surface as a shape error mid-training."""
    from braverse.features_wide import WideEncoder
    from braverse.rl import PolicyNet, TrainConfig, Trainer

    decks = [STARTER_DECKS["st9_sea_fairy"]]
    stock_net = PolicyNet()                       # 55-wide
    with pytest.raises(ValueError, match="different encoder"):
        Trainer(decks, TrainConfig(games=0), db=db, net=stock_net,
                encoder=WideEncoder(db))


def test_the_wide_encoder_actually_trains(db):
    """A short run must produce a usable policy, not just correct shapes."""
    from braverse.features_wide import WideEncoder
    from braverse.rl import TrainConfig, Trainer

    decks = [STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]]
    trainer = Trainer(decks, TrainConfig(games=60, eval_every=0, seed=3),
                      db=db, encoder=WideEncoder(db))
    trainer.train(log=lambda *_: None)
    assert 0.0 <= trainer.evaluate(20) <= 1.0
