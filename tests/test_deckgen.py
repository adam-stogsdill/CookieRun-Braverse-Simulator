"""Deck-search tests. Run with: python -m pytest -q"""

from __future__ import annotations

from collections import Counter

import pytest

from braverse import STARTER_SET_IDS, default_db, starter_deck, validate
from braverse.deckgen import (DeckEvolver, DeckGenConfig, implemented_pool,
                              set_pool, thin_share)
from braverse.enums import CardType


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


def test_the_pool_offers_one_archetype_per_colour(db, wide):
    pool, gauntlet = wide
    evolver = DeckEvolver(pool, gauntlet, DeckGenConfig(seed=1), db=db)
    assert [c.value for c in evolver.available_colors()] == [
        "BLUE", "GREEN", "PURPLE", "RED", "YELLOW"]


@pytest.mark.parametrize("color", ["RED", "BLUE", "GREEN", "YELLOW", "PURPLE"])
def test_a_pinned_evolver_only_ever_builds_its_own_colour(db, wide, color):
    pool, gauntlet = wide
    evolver = DeckEvolver(pool, gauntlet, DeckGenConfig(seed=2), db=db,
                          colors=[color])
    deck = evolver.random_deck()
    for _ in range(5):
        deck = evolver.mutate(deck)
    child = evolver.crossover(deck, evolver.random_deck())
    for candidate in (deck, child):
        assert validate(candidate, db).ok
        assert {db[c].color.value for c in candidate} == {color}


def test_pinning_accepts_colour_members_as_well_as_names(db, wide):
    pool, gauntlet = wide
    by_name = DeckEvolver(pool, gauntlet, DeckGenConfig(seed=3), db=db,
                          colors=["red"])
    member = by_name.fixed_colors[0]
    by_member = DeckEvolver(pool, gauntlet, DeckGenConfig(seed=3), db=db,
                            colors=[member])
    assert by_member.fixed_colors == by_name.fixed_colors
    assert by_member.random_deck() == by_name.random_deck()


def test_pinning_to_a_colour_the_pool_lacks_is_an_error(db):
    pool = set_pool(db, ["ST9"])          # mono-blue
    with pytest.raises(ValueError, match="RED"):
        DeckEvolver(pool, [starter_deck(db, "ST9")], DeckGenConfig(), db=db,
                    colors=["RED"])


def test_crossover_of_two_mono_parents_stays_legal(db, wide):
    evolver = _evolver(db, wide, 1)
    a, b = evolver.random_deck(), evolver.random_deck()
    child = evolver.crossover(a, b)
    assert validate(child, db).ok
    # Repair keeps the child inside the colours it actually leans on.
    assert len(Counter(db[c].color for c in child)) <= 2


def test_priced_blockers_are_in_the_deckbuilder_pool(db):
    """【Blocker】 <{G}> is a card the engine plays in full, effect registry or
    not — the marker and the price are read straight off the printed line. The
    pool used to ask only "is there an effect, or is the card blank?", so every
    energy-priced Blocker was filed as unimplemented and vanished from the deck
    builder and from deck evolution."""
    from braverse.cards import blocker_price
    from braverse.deckgen import implemented_pool
    from braverse.enums import Marker

    pool = {c.id for c in implemented_pool(db)}
    priced = [c for c in db.cards.values()
              if c.is_cookie and blocker_price(c) is not None]
    assert len(priced) >= 20
    assert not [c.id for c in priced if c.id not in pool]
    assert "ST8-011" in pool          # Kiwi Cookie, 【Blocker】 <{G}>
    assert "BS4-047" in pool          # Blue Lily Cookie, <Rest this card.>

    # An *item* that merely mentions 【Blocker】 carries the marker without
    # being a Blocker — only a Cookie can block, which is why `priced` above
    # filters on that. The pool tracks whether its text is coded, and
    # BS3-018's now is: the marker survives normalisation as the filter it is
    # in that sentence.
    assert db["BS3-018"].has(Marker.BLOCKER)
    assert not db["BS3-018"].is_cookie
    assert "BS3-018" in pool


def test_an_unreadable_blocker_price_keeps_the_card_out_of_the_pool(db):
    """A price the engine cannot charge means the Cookie cannot block at all,
    so the card is mis-played and does not belong in the pool."""
    import dataclasses

    from braverse.cards import blocker_price, strip_blocker_text

    kiwi = db["ST8-011"]
    odd = dataclasses.replace(
        kiwi, description="【Blocker】 <Sacrifice your firstborn.>")
    assert blocker_price(odd) is None
    assert strip_blocker_text(odd, odd.description) == odd.description


# --- EXTRA cards are not cards in the 60 ------------------------------------
def test_the_search_never_puts_an_extra_card_in_the_main_deck(db):
    """`--pool implemented` offers 17 EXTRA cards, and none may be dealt.

    They are played out of their own pile; `validate` rejects a main deck
    holding one. A search that can draw them produces decks that are illegal by
    construction, which is what a 25-generation run against the tournament
    gauntlet produced before this: a champion three copies of an EXTRA Cookie
    deep, written to `decks/` looking like any other list.
    """
    pool = implemented_pool(db)
    assert any(c.type is CardType.EXTRA for c in pool), "nothing to keep out"

    evolver = DeckEvolver(pool, [], DeckGenConfig(seed=4), db=db)
    assert not any(c.type is CardType.EXTRA for c in evolver.pool)
    for _ in range(20):
        assert validate(evolver.random_deck(), db).ok

    # And a parent that carries one — a seeded list, a crossover from outside —
    # has it repaired away rather than passed on.
    smuggled = [c.id for c in pool if c.type is CardType.EXTRA][:3]
    repaired = evolver.repair(smuggled + evolver.random_deck()[:57])
    assert not any(db[c].type is CardType.EXTRA for c in repaired)
    assert validate(repaired, db).ok


def test_a_seeded_run_starts_from_the_deck_it_was_given(db, wide):
    """`--seed-deck` makes the first generation a tuning pass, not noise.

    The seed itself has to be *in* the starting population — a "seeded" run
    whose seed was only an inspiration is a search from scratch with extra
    steps — and half the population stays random so the run can still leave
    the neighbourhood it started in.
    """
    pool, gauntlet = wide
    seed_deck = starter_deck(db, "ST9")
    evolver = DeckEvolver(pool, gauntlet,
                          DeckGenConfig(seed=2, population=8, generations=1,
                                        games_per_eval=2), db=db)
    started: list[list[str]] = []
    evolver.random_deck = lambda _orig=evolver.random_deck: (
        started.append(_orig()) or started[-1])

    deck, _, _ = evolver.evolve(seeds=[seed_deck], log=lambda *a: None)
    assert validate(deck, db).ok
    assert len(started) == 4, "half the population should still be random"

    # An illegal seed is repaired rather than refused: it may be somebody
    # else's list, and a run that dies on it has helped nobody.
    junk = seed_deck[:30] + ["ST9-006"] * 30
    deck, _, _ = evolver.evolve(seeds=[junk], log=lambda *a: None)
    assert validate(deck, db).ok


def test_every_generation_is_offered_to_the_checkpoint_hook(db, wide):
    """A run must be inspectable and salvageable while it is still going."""
    pool, gauntlet = wide
    evolver = DeckEvolver(pool, gauntlet[:1],
                          DeckGenConfig(population=4, generations=3,
                                        games_per_eval=2, seed=3), db=db)
    seen = []
    evolver.evolve(log=lambda *_: None,
                   on_generation=lambda g, deck, row: seen.append((g, deck, row)))

    assert [g for g, _, _ in seen] == [0, 1, 2]
    for _, deck, row in seen:
        assert len(deck) == 60
        assert row["best"] >= row["mean"] - 1e-9


def test_the_hook_cannot_reach_back_into_the_search(db, wide):
    """It is handed a copy: a caller that mutates the list cannot steer the run."""
    pool, gauntlet = wide
    config = DeckGenConfig(population=4, generations=2, games_per_eval=2, seed=3)

    def run(vandalise):
        ev = DeckEvolver(pool, gauntlet[:1], config, db=db)
        return ev.evolve(log=lambda *_: None, on_generation=lambda g, d, r: (
            d.clear() if vandalise else None))[0]

    assert run(True) == run(False)


def test_checkpoints_are_playable_decklists_on_disk(db, tmp_path):
    """What a run drops every generation must load like any other decklist."""
    from braverse.deckfile import read_any
    from evolve_deck import Checkpointer

    deck = starter_deck(db, "ST9")
    extra = ["BS8-069"] * 2
    ckpt = Checkpointer(str(tmp_path), every=2, generations=5, db=db,
                        extra=extra, gauntlet=["st9_sea_fairy"])
    for generation, score in enumerate([0.4, 0.6, 0.5, 0.7, 0.55]):
        ckpt.save(generation, list(deck), {"best": score, "mean": score - 0.1})

    # every 2nd generation, and the last one whatever the count lands on
    assert sorted(p.name for p in tmp_path.glob("gen*.txt")) == [
        "gen000.txt", "gen002.txt", "gen004.txt"]
    again, carried = read_any(tmp_path / "gen004.txt")
    assert again == deck and carried == extra
    assert validate(again, db, extra=carried).ok


def test_best_tracks_the_best_score_not_the_last_generation(db, tmp_path):
    from evolve_deck import Checkpointer

    ckpt = Checkpointer(str(tmp_path), every=1, generations=3, db=db)
    ckpt.save(0, starter_deck(db, "ST9"), {"best": 0.9, "mean": 0.5})
    ckpt.save(1, starter_deck(db, "ST8"), {"best": 0.2, "mean": 0.1})

    text = (tmp_path / "_best.txt").read_text(encoding="utf-8")
    assert "# generation: 0" in text and "# score: 0.9" in text


def test_no_checkpoint_directory_means_nothing_is_written(db, tmp_path):
    from evolve_deck import Checkpointer

    ckpt = Checkpointer(None, every=1, generations=1, db=db)
    ckpt.save(0, starter_deck(db, "ST9"), {"best": 0.5, "mean": 0.5})
    assert ckpt.written == [] and not list(tmp_path.iterdir())


# -- consolidation -------------------------------------------------------


def test_thin_share_counts_cards_not_stacks_and_by_number(db):
    """The measure the consolidation price is charged against."""
    playset = ["ST9-007"] * 4
    singles = ["ST9-001", "ST9-002", "ST9-003", "ST9-004"]
    assert thin_share(playset, db) == 0.0
    assert thin_share(singles, db) == 1.0
    assert thin_share(playset + singles, db) == 0.5
    # A pair is thin at a floor of 4 and not at a floor of 2, which is the
    # difference between "no 1-ofs" and "playsets throughout".
    pair = ["ST9-007"] * 2
    assert thin_share(pair, db, floor=2) == 0.0
    assert thin_share(pair, db, floor=4) == 1.0


def test_the_price_is_charged_to_the_objective_and_never_to_the_win_rate(db, wide):
    """Every number reported to a person stays a win rate.

    A holdout that has quietly had a deckbuilding preference subtracted from it
    cannot be compared against any other number in the project.
    """
    pool, gauntlet = wide
    config = DeckGenConfig(seed=7, games_per_eval=2, consolidation_weight=0.1)
    evolver = DeckEvolver(pool, gauntlet[:1], config, db=db)
    deck = evolver.random_deck()
    rate = evolver.fitness(deck, games=2)
    assert evolver.objective(deck, games=2) == pytest.approx(
        rate - 0.1 * thin_share(deck, db))
    # Off by default, so a run that does not ask for it is the old search.
    plain = DeckEvolver(pool, gauntlet[:1],
                        DeckGenConfig(seed=7, games_per_eval=2), db=db)
    assert plain.consolidation_penalty(deck) == 0.0
    assert plain.objective(deck, games=2) == plain.fitness(deck, games=2)


def test_a_priced_search_can_actually_thicken_a_deck(db, wide):
    """The penalty needs a mutation that collects it, or it is unclimbable.

    A random swap makes another singleton every time; without `_replacement`
    the search could only ever pay this price, which is a constant, which is
    no selection pressure at all.
    """
    pool, gauntlet = wide
    config = DeckGenConfig(seed=11, games_per_eval=2, consolidation_weight=0.1,
                           consolidation_bias=1.0, mutations=20)
    evolver = DeckEvolver(pool, gauntlet[:1], config, db=db)
    singles = evolver.repair([c.id for c in pool][:60])
    before = thin_share(singles, db)
    thinned = min(thin_share(evolver.mutate(list(singles)), db)
                  for _ in range(5))
    assert thinned < before
    assert validate(evolver.mutate(list(singles)), db).ok


def test_seeded_candidates_start_in_stacks_when_the_price_is_on(db, wide):
    """Sixty singletons in every candidate is a constant, not a gradient."""
    pool, gauntlet = wide
    priced = DeckEvolver(pool, gauntlet[:1],
                         DeckGenConfig(seed=13, consolidation_weight=0.05),
                         db=db)
    plain = DeckEvolver(pool, gauntlet[:1], DeckGenConfig(seed=13), db=db)
    assert (thin_share(priced.random_deck(), db)
            < thin_share(plain.random_deck(), db))
    assert validate(priced.random_deck(), db).ok


def test_a_run_priced_on_consolidation_ends_up_tidier(db, wide):
    """End to end: the same budget and seed, with and without the price."""
    pool, gauntlet = wide

    def run(weight):
        config = DeckGenConfig(seed=4, population=6, generations=3,
                               games_per_eval=2, consolidation_weight=weight)
        deck, score, history = DeckEvolver(pool, gauntlet[:2], config,
                                           db=db).evolve(log=lambda *a: None)
        assert validate(deck, db).ok
        assert 0.0 <= score <= 1.0, "the reported score is still a win rate"
        return thin_share(deck, db), history

    tidy, history = run(0.08)
    loose, _ = run(0.0)
    assert tidy < loose
    assert all("thin" in row for row in history if "generation" in row)
