"""A CookieRun: Braverse rules engine and practice bot.

    from braverse import Game, HeuristicAgent, SeatedAgent, STARTER_DECKS

    agents = [SeatedAgent(HeuristicAgent(), 0), SeatedAgent(HeuristicAgent(), 1)]
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                agents, seed=1)
    game.setup()
    final = game.play_out()
"""

# Kept in step with "Current Version" at the top of README.md. Written into
# every replay file, so a recording that will not play back can say which build
# made it.
__version__ = "0.2.43"

from .agents import HeuristicAgent, RandomAgent, SeatedAgent
from .cards import CardDB, CardDef, default_db, load_cards
from .config import DEFAULT as DEFAULT_RULES, RulesConfig
from .deckgen import DeckEvolver, DeckGenConfig, implemented_pool, set_pool
from .decks import (STARTER_DECKS, STARTER_SET_IDS, build_starter_deck,
                    starter_deck, validate)
from .effects import Ctx, Trigger, effect, implemented_cards
from .engine import Game
from .enums import CardType, Color, Marker, Phase, Zone
from .features import Encoder
from .state import Cookie, GameState, PlayerState

# `braverse.rl` is deliberately not imported here: it pulls in torch, which
# costs about a second of start-up that a plain simulation run should not pay.
# Import it directly — `from braverse.rl import Trainer`.

from . import impl as _impl  # registers the hand-written card effects  # noqa: F401


def _compile_pool() -> int:
    """Compile the rest of the pool from its printed text.

    Hand-written cards are skipped, so this can only ever *add* coverage —
    anything verified by hand keeps its implementation. Set
    ``BRAVERSE_NO_COMPILE=1`` to run with hand-written cards only.
    """
    import os

    if os.environ.get("BRAVERSE_NO_COMPILE"):
        return 0
    from .compiler import compile_all
    return compile_all(default_db())["__registered__"]


COMPILED_CARDS = _compile_pool()

__all__ = [
    "__version__",
    "Game", "GameState", "PlayerState", "Cookie",
    "HeuristicAgent", "RandomAgent", "SeatedAgent",
    "CardDB", "CardDef", "default_db", "load_cards",
    "STARTER_DECKS", "STARTER_SET_IDS", "build_starter_deck", "starter_deck",
    "validate",
    "DeckEvolver", "DeckGenConfig", "implemented_pool", "set_pool", "Encoder",
    "Ctx", "Trigger", "effect", "implemented_cards",
    "RulesConfig", "DEFAULT_RULES",
    "CardType", "Color", "Marker", "Phase", "Zone",
]
