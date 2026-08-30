"""Self-play reinforcement learning.

The policy scores each legal action independently and softmaxes over them, so
it handles the variable, heterogeneous action set the engine produces without
needing a fixed action index. Training is REINFORCE with a learned value
baseline and an entropy bonus, against a league of frozen past selves plus the
scripted heuristic so the policy cannot drift into beating only itself.

Mid-effect decisions (which card to discard, which Cookie to target inside an
effect) are delegated to :class:`~braverse.agents.HeuristicAgent`. Only the
turn-level action choice is learned — that is where nearly all the decision
weight sits, and it keeps the credit assignment clean.
"""

from __future__ import annotations

import copy
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import torch
import torch.nn as nn

from . import actions as A
from .agents import HeuristicAgent, RandomAgent
from .cards import CardDB, default_db
from .decks import STARTER_DECKS
from .engine import Game
from .features import FEATURE_DIM, STATE_DIM, Encoder
from .state import GameState

from tqdm import tqdm

#: Checkpoint layout. 1 was a bare ``state_dict``; 2 is a dict that also
#: carries Adam's moments and the league, so a run can be *continued*
#: rather than only warm-started. Both are still read — every `.pt` a
#: player already has beside the game was written at 1.
CHECKPOINT_FORMAT = 2


class PolicyNet(nn.Module):
    """Scores one (state, action) row; a softmax over rows gives the policy."""

    def __init__(self, hidden: int = 1024, feature_dim: int = FEATURE_DIM,
                 state_dim: int = STATE_DIM,
                 widths: Sequence[int] | None = None,
                 value_hidden: int | None = None):
        """``widths`` is the body's hidden layers, ``hidden`` the pyramid they
        default to. Both are here so a checkpoint can be rebuilt at the shape
        it was *saved* at rather than the shape this class happens to default
        to today — deepening the net must not orphan every `.pt` already on
        disk, which is exactly what happened once.
        """
        super().__init__()
        self.feature_dim = feature_dim
        self.state_dim = state_dim
        self.widths = tuple(widths) if widths is not None else (
            hidden, hidden // 2, hidden // 4, hidden // 8)
        self.value_hidden = value_hidden if value_hidden is not None else hidden

        layers: list[nn.Module] = []
        previous = feature_dim
        for width in self.widths:
            layers += [nn.Linear(previous, width), nn.ReLU()]
            previous = width
        layers.append(nn.Linear(previous, 1))
        self.body = nn.Sequential(*layers)

        self.value = nn.Sequential(
            nn.Linear(state_dim, self.value_hidden), nn.ReLU(),
            nn.Linear(self.value_hidden, 1),
        )

    def logits(self, rows: torch.Tensor) -> torch.Tensor:
        return self.body(rows).squeeze(-1)

    def state_value(self, state_rows: torch.Tensor) -> torch.Tensor:
        return self.value(state_rows).squeeze(-1)


def encoder_for(net: PolicyNet, db: CardDB) -> Encoder:
    """The encoder whose rows this policy was trained to read.

    A checkpoint means nothing apart from the encoding it was trained on, and
    the row width is the one unambiguous record of which that was. Callers used
    to default to the stock `Encoder` instead, so a wide checkpoint — offered
    in the pilot menu like any other `.pt` beside the game — died on the match
    thread with a bare matrix-shape error the moment it was asked for a move.
    """
    from .features_wide import WideEncoder

    for cls in (Encoder, WideEncoder):
        if cls.dim == net.feature_dim:
            return cls(db)
    raise ValueError(
        f"no encoder emits {net.feature_dim}-wide rows, so this checkpoint "
        f"cannot be played: it was trained against an encoding this build does "
        f"not have. Known widths: "
        f"{', '.join(f'{c.__name__} {c.dim}' for c in (Encoder, WideEncoder))}.")


@dataclass
class Step:
    rows: np.ndarray      # (n_actions, encoder.dim)
    chosen: int


class RLAgent:
    """Controller driven by a :class:`PolicyNet`."""

    def __init__(self, net: PolicyNet, seat: int, *, encoder: Encoder | None = None,
                 db: CardDB | None = None, training: bool = False,
                 temperature: float = 1.0, seed: int | None = None,
                 name: str = "rl"):
        self.net = net
        self.seat = seat
        self.db = db or default_db()
        # Matched to the policy, never assumed: see `encoder_for`.
        self.encoder = encoder or encoder_for(net, self.db)
        self.training = training
        self.temperature = temperature
        self.name = name
        self.rng = random.Random(seed)
        self.fallback = HeuristicAgent(db=self.db, seed=seed)
        setattr(self.fallback, "_seat_hint", seat)
        self.trajectory: list[Step] = []

    def reset(self) -> None:
        self.trajectory = []

    def choose_action(self, state: GameState, options: Sequence[A.Action]):
        if not options:
            return None
        if len(options) == 1:
            return options[0]

        rows = self.encoder.encode(state, self.seat, list(options))
        with torch.no_grad():
            logits = self.net.logits(torch.from_numpy(rows))
            if self.training:
                probs = torch.softmax(logits / self.temperature, dim=0).numpy()
                index = int(np.random.choice(len(options), p=probs / probs.sum()))
            else:
                index = int(torch.argmax(logits).item())

        if self.training:
            self.trajectory.append(Step(rows=rows, chosen=index))
        return options[index]

    def choose(self, state: GameState, prompt: str, options: Sequence, *, optional: bool):
        return self.fallback.choose(state, prompt, options, optional=optional)


@dataclass
class TrainConfig:
    games: int = 1_000_000
    batch_games: int = 32
    lr: float = 3e-4
    entropy_coef: float = 0.01
    value_coef: float = 0.5
    temperature: float = 0.90
    league_every: int = 500          # snapshot the policy into the league
    league_size: int = 6
    heuristic_share: float = 0.4     # fraction of games played against the script
    eval_every: int = 500
    eval_games: int = 120
    seed: int = 0
    # Fraction of training games played with freshly generated random legal
    # decks instead of the fixed pool. This is what exposes the policy to cards
    # the starter decks never contain.
    random_deck_share: float = 0.0
    random_deck_cache: int = 200     # distinct random decks to rotate through


class Trainer:
    def __init__(self, decks: Sequence[Sequence[str]] | None = None,
                 config: TrainConfig = TrainConfig(),
                 db: CardDB | None = None,
                 net: PolicyNet | None = None,
                 encoder: Encoder | None = None):
        self.cfg = config
        self.db = db or default_db()
        # A resumed run takes its encoding from the checkpoint; a fresh one has
        # nothing to read it off and gets the stock encoder. An encoder passed
        # explicitly still wins, and still has to agree with the net below.
        self.encoder = encoder or (Encoder(self.db) if net is None
                                   else encoder_for(net, self.db))
        self.decks = [list(d) for d in (decks or [
            STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]])]
        self.net = net or PolicyNet(feature_dim=self.encoder.dim,
                                    state_dim=self.encoder.state_dim)
        if self.net.feature_dim != self.encoder.dim:
            raise ValueError(
                f"encoder emits {self.encoder.dim}-wide rows but the policy "
                f"expects {self.net.feature_dim}. The checkpoint was trained "
                f"with a different encoder — train a new one rather than "
                f"resuming this.")
        self.opt = torch.optim.Adam(self.net.parameters(), lr=config.lr)
        self.rng = random.Random(config.seed)
        self.league: list[PolicyNet] = []
        self.history: list[dict] = []
        self.games_trained = 0
        self._random_decks: list[list[str]] = []
        torch.manual_seed(config.seed)
        np.random.seed(config.seed)

    # -- decks -----------------------------------------------------------
    def _build_random_decks(self) -> None:
        """Pre-generate a rotation of legal decks from the whole playable pool."""
        from .deckgen import DeckEvolver, DeckGenConfig, implemented_pool

        pool = implemented_pool(self.db)
        maker = DeckEvolver(pool, [], DeckGenConfig(seed=self.cfg.seed), db=self.db)
        self._random_decks = [maker.random_deck()
                              for _ in range(self.cfg.random_deck_cache)]

    def sample_decks(self) -> list[list[str]]:
        if self.cfg.random_deck_share and self.rng.random() < self.cfg.random_deck_share:
            if not self._random_decks:
                self._build_random_decks()
            return [self.rng.choice(self._random_decks),
                    self.rng.choice(self._random_decks)]
        if len(self.decks) <= 2:
            return [self.decks[0], self.decks[-1]]
        return [self.rng.choice(self.decks), self.rng.choice(self.decks)]

    # -- opponents -------------------------------------------------------
    def _opponent(self, seat: int, seed: int):
        if not self.league or self.rng.random() < self.cfg.heuristic_share:
            agent = HeuristicAgent(db=self.db, seed=seed)
            setattr(agent, "_seat_hint", seat)
            return agent
        frozen = self.rng.choice(self.league)
        return RLAgent(frozen, seat, encoder=self.encoder, db=self.db,
                       training=False, seed=seed, name="league")

    def _snapshot(self) -> None:
        frozen = copy.deepcopy(self.net)
        for p in frozen.parameters():
            p.requires_grad_(False)
        frozen.eval()
        self.league.append(frozen)
        if len(self.league) > self.cfg.league_size:
            self.league.pop(0)

    # -- rollouts --------------------------------------------------------
    def play_game(self, learner: RLAgent, opponent, seed: int,
                  decks: Sequence[Sequence[str]] | None = None) -> float:
        """Run one game. Returns the learner's reward in [-1, 1]."""
        learner.reset()
        controllers = [None, None]
        controllers[learner.seat] = learner
        controllers[1 - learner.seat] = opponent
        if decks is None:
            decks = [self.decks[0], self.decks[-1]]
        game = Game(list(decks), controllers, db=self.db, seed=seed)
        game.setup()
        state = game.play_out()
        if state.winner == learner.seat:
            return 1.0
        if state.winner == -1 or state.winner is None:
            return 0.0
        return -1.0

    # -- learning --------------------------------------------------------
    def _update(self, episodes: list[tuple[list[Step], float]]) -> dict:
        steps = [(s, r) for traj, r in episodes for s in traj]
        if not steps:
            return {}

        width = max(s.rows.shape[0] for s, _ in steps)
        batch = np.zeros((len(steps), width, self.encoder.dim), dtype=np.float32)
        mask = np.zeros((len(steps), width), dtype=bool)
        chosen = np.zeros(len(steps), dtype=np.int64)
        returns = np.zeros(len(steps), dtype=np.float32)
        for i, (step, reward) in enumerate(steps):
            n = step.rows.shape[0]
            batch[i, :n] = step.rows
            mask[i, :n] = True
            chosen[i] = step.chosen
            returns[i] = reward

        rows = torch.from_numpy(batch)
        mask_t = torch.from_numpy(mask)
        chosen_t = torch.from_numpy(chosen)
        returns_t = torch.from_numpy(returns)

        logits = self.net.logits(rows)
        logits = logits.masked_fill(~mask_t, float("-inf"))
        log_probs = torch.log_softmax(logits, dim=1)
        picked = log_probs.gather(1, chosen_t.unsqueeze(1)).squeeze(1)

        # The state block is identical across a decision's rows, so row 0 of
        # each padded group carries it.
        values = self.net.state_value(rows[:, 0, :self.encoder.state_dim])
        advantage = (returns_t - values).detach()
        if advantage.numel() > 1 and advantage.std() > 1e-6:
            advantage = (advantage - advantage.mean()) / (advantage.std() + 1e-6)

        probs = log_probs.exp()
        entropy = -(probs * log_probs.masked_fill(~mask_t, 0.0)).sum(dim=1).mean()

        policy_loss = -(picked * advantage).mean()
        value_loss = torch.nn.functional.mse_loss(values, returns_t)
        loss = (policy_loss
                + self.cfg.value_coef * value_loss
                - self.cfg.entropy_coef * entropy)

        self.opt.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(self.net.parameters(), 1.0)
        self.opt.step()

        return {
            "policy_loss": float(policy_loss),
            "value_loss": float(value_loss),
            "entropy": float(entropy),
            "decisions": len(steps),
        }

    def train(self, *, log=print) -> list[dict]:
        cfg = self.cfg
        episodes: list[tuple[list[Step], float]] = []
        wins = 0
        played = 0

        for game_index in tqdm(range(cfg.games), desc="Training RL Agent."):
            seat = game_index % 2
            learner = RLAgent(self.net, seat, encoder=self.encoder, db=self.db,
                              training=True, temperature=cfg.temperature,
                              seed=cfg.seed + game_index)
            opponent = self._opponent(1 - seat, cfg.seed + 90_000 + game_index)
            reward = self.play_game(learner, opponent, cfg.seed + game_index,
                                    decks=self.sample_decks())
            episodes.append((learner.trajectory, reward))
            wins += reward > 0
            played += 1
            self.games_trained += 1

            if len(episodes) >= cfg.batch_games:
                stats = self._update(episodes)
                episodes.clear()
                stats.update(games=game_index + 1,
                             train_winrate=wins / max(played, 1))
                self.history.append(stats)
                wins = played = 0

            if cfg.league_every and (game_index + 1) % cfg.league_every == 0:
                self._snapshot()

            if cfg.eval_every and (game_index + 1) % cfg.eval_every == 0:
                rate = self.evaluate(cfg.eval_games)
                log(f"  game {game_index + 1:6}  vs heuristic {rate:6.1%}"
                    f"  league {len(self.league)}")
                self.history.append({"games": game_index + 1, "eval": rate})

        if episodes:
            self._update(episodes)
        return self.history

    # -- evaluation ------------------------------------------------------
    def evaluate(self, games: int = 200, opponent: str = "heuristic", *,
                 unseen_decks: bool = False) -> float:
        """Greedy win rate, seats alternated so turn order cannot flatter it.

        ``unseen_decks`` scores on freshly generated legal decks built from the
        whole playable pool — a generalisation test, since the policy encodes
        card *stats and abilities* rather than card identity.

        A trainer holding a *pool* is scored across that pool rather than on
        one pair out of it. Two decks is the fixed match-up this started as and
        stays exactly that; more than two means the pool was the point, and a
        number measured on `decks[0]` vs `decks[-1]` would be a report on two
        of the eighteen decks the run actually trained against.
        """
        held_out = None
        if not unseen_decks and len(self.decks) > 2:
            held_out = self.decks
        if unseen_decks:
            from .deckgen import DeckEvolver, DeckGenConfig, implemented_pool
            maker = DeckEvolver(implemented_pool(self.db), [],
                                DeckGenConfig(seed=self.cfg.seed + 555_555),
                                db=self.db)
            held_out = [maker.random_deck() for _ in range(40)]

        wins = 0.0
        for i in range(games):
            seat = i % 2
            learner = RLAgent(self.net, seat, encoder=self.encoder, db=self.db,
                              training=False, seed=i, name="rl-eval")
            if opponent == "random":
                other = RandomAgent(seed=10_000 + i)
            else:
                other = HeuristicAgent(db=self.db, seed=10_000 + i)
            setattr(other, "_seat_hint", 1 - seat)
            decks = None
            if held_out:
                decks = [held_out[i % len(held_out)],
                         held_out[(i * 7 + 3) % len(held_out)]]
            reward = self.play_game(learner, other, 500_000 + i, decks=decks)
            wins += 1.0 if reward > 0 else (0.5 if reward == 0 else 0.0)
        return wins / games

    # -- persistence -----------------------------------------------------
    def save(self, path: str | Path) -> None:
        """Write the checkpoint, plus a readable sidecar beside it.

        The blob carries what a run needs to be *continued*, not only replayed:
        the weights, Adam's moments, and the league of frozen past selves.
        Weights alone make a resume a warm start — the optimizer rebuilds its
        moments from zero, and an empty league sends the first ``league_every``
        games back to the scripted heuristic, a weaker and much narrower
        opponent than the run was finishing against.
        """
        path = Path(path)
        # Agents live in `agents/`, which a fresh clone does not have yet: a
        # run that trains for an hour must not fail on the write.
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save({
            "format": CHECKPOINT_FORMAT,
            "net": self.net.state_dict(),
            "optimizer": self.opt.state_dict(),
            "league": [frozen.state_dict() for frozen in self.league],
            "games_trained": self.games_trained,
        }, path)
        path.with_suffix(".json").write_text(json.dumps({
            "encoder": type(self.encoder).__name__,
            "feature_dim": self.encoder.dim,
            "state_dim": self.encoder.state_dim,
            "games_trained": self.games_trained,
            "config": self.cfg.__dict__,
            "history": self.history[-50:],
        }, indent=1), encoding="utf-8")

    @staticmethod
    def _weights(blob) -> dict:
        """The policy weights out of either checkpoint layout.

        A format-1 file *is* the ``state_dict``; a format-2 file keeps it under
        ``net`` alongside the optimizer and league. Sniffing the key rather
        than the version keeps this working on a file written before the
        version was recorded.
        """
        return blob["net"] if "net" in blob else blob

    @staticmethod
    def _net_from_weights(weights: dict) -> PolicyNet:
        """Build a policy sized from the tensors themselves.

        The widths are read off the weights rather than assumed, so a
        checkpoint trained under a different encoder loads as the shape it was
        actually saved at. Guessing instead would raise a shape error that
        unattended callers catch and treat as "no checkpoint", silently
        throwing away a trained policy.
        """
        indices = sorted(int(key.split(".")[1]) for key in weights
                         if key.startswith("body.") and key.endswith(".weight"))
        # Every body Linear but the last one is a hidden layer; the last emits
        # the single score. Reading the depth as well as the widths is what
        # lets a net saved under an older architecture load at all.
        net = PolicyNet(feature_dim=weights["body.0.weight"].shape[1],
                        state_dim=weights["value.0.weight"].shape[1],
                        widths=[weights[f"body.{i}.weight"].shape[0]
                                for i in indices[:-1]],
                        value_hidden=weights["value.0.weight"].shape[0])
        net.load_state_dict(weights)
        net.eval()
        return net

    @staticmethod
    def load_net(path: str | Path) -> PolicyNet:
        """Load just the policy from a checkpoint of either format."""
        blob = torch.load(path, map_location="cpu")
        return Trainer._net_from_weights(Trainer._weights(blob))

    def restore(self, path: str | Path) -> dict:
        """Continue a saved run: Adam's moments and the league come back.

        The *weights* arrive through ``net=`` at construction instead, because
        the network has to be sized from them before there is an optimizer to
        restore into; loading them again here is idempotent and keeps the
        method honest when a caller forgets. What it returns is what it
        actually found, so a caller can say so out loud — a format-1 file
        carries weights only, and reporting that warm start as a resume is
        exactly the quiet lie this is meant to avoid.
        """
        blob = torch.load(Path(path), map_location="cpu")
        self.net.load_state_dict(self._weights(blob))
        if "net" not in blob:
            return {"optimizer": False, "league": 0, "games_trained": 0}

        self.opt.load_state_dict(blob["optimizer"])
        self.league = []
        for weights in blob.get("league", []):
            frozen = self._net_from_weights(weights)
            for param in frozen.parameters():
                param.requires_grad_(False)
            self.league.append(frozen)
        self.games_trained = int(blob.get("games_trained", 0))
        return {"optimizer": True, "league": len(self.league),
                "games_trained": self.games_trained}
