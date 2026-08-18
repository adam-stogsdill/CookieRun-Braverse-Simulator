"""Deck-search tests. Run with: python -m pytest -q"""

from __future__ import annotations

from collections import Counter

import pytest

from braverse import STARTER_SET_IDS, default_db, starter_deck, validate
from braverse.deckgen import DeckEvolver, DeckGenConfig, set_pool


@pytest.fixture(scope="module")
def db():
    return default_db()


@pytest.fixture(scope="module")
def wide(db):
    """The whole ten-set starter pool, evolved against the ten starter decks."""
    pool = set_pool(db, list(STARTER_SET_IDS))
    gauntlet = [starter_deck(db, s) for s in STARTER_SET_IDS]
    return pool, gauntlet


def _evolver(db, wide, colors, seed=5):
    pool, gauntlet = wide
    return DeckEvolver(pool, gauntlet,
                       DeckGenConfig(seed=seed, color_identity=colors), db=db)


def test_the_wide_pool_spans_every_starter_set(wide, db):
    pool, gauntlet = wide
    assert {c.set_id for c in pool} == set(STARTER_SET_IDS)
    assert len(gauntlet) == len(STARTER_SET_IDS)


@pytest.mark.parametrize("colors", [1, 2])
def test_seeded_decks_stay_inside_their_colour_identity(db, wide, colors):
    evolver = _evolver(db, wide, colors)
    for _ in range(5):
        deck = evolver.random_deck()
        assert validate(deck, db).ok
        assert len({db[c].color for c in deck}) <= colors


def test_mutation_does_not_splash_a_deck_out_of_its_colours(db, wide):
    evolver = _evolver(db, wide, 1)
    deck = evolver.random_deck()
    before = {db[c].color for c in deck}
    for _ in range(10):
        deck = evolver.mutate(deck)
        assert validate(deck, db).ok
        assert {db[c].color for c in deck} == before


def test_unrestricted_seeding_still_produces_legal_decks(db, wide):
    """color_identity=0 is the old uniform behaviour, and must still work."""
    evolver = _evolver(db, wide, 0)
    deck = evolver.random_deck()
    assert validate(deck, db).ok
    assert len({db[c].color for c in deck}) > 2


def test_a_single_colour_pool_is_unaffected_by_the_constraint(db):
    """ST9 is mono-blue: asking for two colours must not break the search."""
    pool = set_pool(db, ["ST9"])
    evolver = DeckEvolver(pool, [starter_deck(db, "ST9")],
                          DeckGenConfig(seed=1, color_identity=2), db=db)
    deck = evolver.random_deck()
    assert validate(deck, db).ok
    assert {db[c].set_id for c in deck} == {"ST9"}


def test_colour_seeding_beats_uniform_on_the_wide_pool(db, wide):
    """The whole point: uniform candidates cannot pay their own costs.

    Measured far apart (7.6% vs 63.2% over six decks); this asserts only the
    direction, with a small sample, so it does not turn into a flaky benchmark.
    """
    def mean_fitness(colors):
        evolver = _evolver(db, wide, colors, seed=11)
        return sum(evolver.fitness(evolver.random_deck(), games=12)
                   for _ in range(3)) / 3

    assert mean_fitness(1) > mean_fitness(0) + 0.15


def test_crossover_of_two_mono_parents_stays_legal(db, wide):
    evolver = _evolver(db, wide, 1)
    a, b = evolver.random_deck(), evolver.random_deck()
    child = evolver.crossover(a, b)
    assert validate(child, db).ok
    # Repair keeps the child inside the colours it actually leans on.
    assert len(Counter(db[c].color for c in child)) <= 2
