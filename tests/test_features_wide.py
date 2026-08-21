"""The wide state encoder. Run with: python -m pytest -q"""

from __future__ import annotations

import numpy as np
import pytest

from braverse import (STARTER_DECKS, Game, HeuristicAgent, SeatedAgent,
                      default_db)
from braverse.features import Encoder
from braverse.features_wide import (SLOTS, WIDE_FEATURE_DIM, WIDE_STATE_DIM,
                                    WideEncoder)


@pytest.fixture(scope="module")
def db():
    return default_db()


def _game(db, seed=7):
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(seed=1), 0),
                 SeatedAgent(HeuristicAgent(seed=2), 1)], db=db, seed=seed)
    game.setup()
    return game


def test_the_wide_encoder_sees_more_than_the_stock_one(db):
    assert WideEncoder.state_dim > Encoder.state_dim
    assert WideEncoder.dim == WIDE_FEATURE_DIM
    # Both expose the same interface, which is what lets the trainer swap them.
    for attr in ("dim", "state_dim", "encode", "state_vector"):
        assert hasattr(WideEncoder, attr) and hasattr(Encoder, attr)


def test_every_decision_encodes_to_the_declared_shape(db):
    encoder, game, seen = WideEncoder(db), _game(db), 0
    while not game.state.over and seen < 120:
        options = game.legal_actions()
        if not options:
            game.end_turn()
            continue
        seat = game.to_move()
        rows = encoder.encode(game.state, seat, options)
        assert rows.shape == (len(options), WIDE_FEATURE_DIM)
        assert np.isfinite(rows).all()
        # Features are normalised; an unscaled count would swamp the rest.
        assert np.abs(rows).max() <= 20
        # The state block is shared by every action row, so a value head can
        # read row 0 without re-encoding.
        assert (rows[:, :WIDE_STATE_DIM] == rows[0, :WIDE_STATE_DIM]).all()
        seen += 1
        game.step(game.controller(seat).choose_action(game.state, options)
                  or options[0])
    assert seen > 10


def test_the_opponents_hand_never_reaches_the_encoding(db):
    """Only what the acting seat can legitimately see may be encoded."""
    encoder, game = WideEncoder(db), _game(db)
    before = encoder.state_vector(game.state, 0).copy()
    opp = game.state.players[1]
    opp.hand.reverse()
    assert np.array_equal(before, encoder.state_vector(game.state, 0))
    # Hand *size* is public, so removing a card legitimately does show up.
    opp.hand.pop()
    assert not np.array_equal(before, encoder.state_vector(game.state, 0))


def test_an_empty_battle_slot_encodes_as_zeros(db):
    encoder, game = WideEncoder(db), _game(db)
    me = game.state.players[0]
    me.battle.clear()
    board = encoder._board(me)
    assert board.shape[0] == SLOTS * 14
    assert not board.any()


def test_a_cookie_on_the_board_moves_its_own_slot_only(db):
    encoder, game = WideEncoder(db), _game(db)
    me = game.state.players[0]
    if not me.battle:
        pytest.skip("no Cookie in play at setup")
    before = encoder._board(me).copy()
    me.battle[0].rested = not me.battle[0].rested
    after = encoder._board(me)
    changed = np.flatnonzero(before != after)
    assert changed.size == 1 and changed[0] < 14   # first slot only


def test_encoding_is_deterministic(db):
    encoder = WideEncoder(db)
    a = encoder.state_vector(_game(db).state, 0)
    b = encoder.state_vector(_game(db).state, 0)
    assert np.array_equal(a, b)
