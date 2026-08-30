"""Evolutionary deck generation.

Fitness is the only honest measure available — a decklist is good if it wins
games — so this is a straight genetic algorithm over legal 60-card lists, with
every candidate scored by playing a gauntlet in the engine.

The card pool defaults to the sets whose effects are implemented. Widening it
to the whole database is one flag, but every unimplemented card plays as a
vanilla body, so the search would be optimising against a fiction.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Sequence

from . import config as cfg
from .agents import HeuristicAgent, SeatedAgent
from .cards import CardDB, CardDef, default_db, strip_blocker_text
from .decks import validate
from .effects import Trigger, is_implemented
from .engine import Game
from .enums import CardType

TRIGGERS = list(Trigger)


def implemented_pool(db: CardDB) -> list[CardDef]:
    """Cards the engine plays correctly: vanilla bodies plus coded effects.

    "Vanilla" is not the same as "blank". 【Blocker】 has no entry in the effect
    registry because it needs none — the engine reads the marker and its price
    off the printed line — so a Cookie whose only text is a readable 【Blocker】
    line is played in full. Counting that line as unimplemented text kept every
    energy-priced Blocker out of the deck builder and out of deck evolution,
    which is most of them: the rest-priced ones only got in because they happen
    to carry a second, hand-written ability.
    """
    pool = []
    for card in db.cards.values():
        if card.is_ban or card.type.value == "NPC":
            continue
        text = " ".join([card.description, card.flip_text,
                         card.attack.text if card.attack else ""])
        text = strip_blocker_text(card, text).strip()
        coded = is_implemented(card.id)
        if coded or not text:
            pool.append(card)
    return pool


def set_pool(db: CardDB, set_ids: Sequence[str]) -> list[CardDef]:
    return [c for c in db.cards.values()
            if c.set_id in set_ids and not c.is_ban]


@dataclass
class DeckGenConfig:
    population: int = 24
    generations: int = 15
    elite: int = 4
    tournament: int = 3
    mutations: int = 6           # cards swapped per mutation
    mutation_rate: float = 0.8
    games_per_eval: int = 40
    seed: int = 0
    # How to combine scores when several pilots fly the candidate. "min" selects
    # for robustness — a deck must work for every pilot, not just the one that
    # happens to share its quirks. "mean" is more permissive.
    pilot_aggregate: str = "min"
    # Every generation is scored on a fresh block of shuffles. Without this the
    # search overfits the seeds instead of the game: a deck evolved on fixed
    # seeds scored 78% during the run and 46-52% on unseen shuffles.
    reseed_each_generation: bool = True
    # Colours a candidate may draw on. A deck spanning a wide multi-set pool
    # uniformly cannot pay its own costs, so the search wastes its whole budget
    # rediscovering "pick a colour". 0 disables the constraint (uniform sampling
    # over the pool), which is the right setting for a single-colour pool.
    #
    # Measured on the ST1-ST10 pool against the ten starter decks, mean fitness
    # of six freshly seeded random decks: 7.6% at 0, 63.2% at 1, 31.2% at 2.
    # Mono is where the search should start; crossover between two differently
    # coloured parents still reaches two-colour lists from there.
    color_identity: int = 1
    # Nothing in a win rate rewards consistency, so a GA left alone builds a
    # list of 1-ofs: at the margin many slots are interchangeable, and a
    # singleton is as good as a playset *on average over many games*. It is not
    # the same deck to play. `consolidation_weight` prices that difference —
    # the search maximises win rate minus this weight times the share of the 60
    # sitting in stacks below `consolidation_floor`, so a thin slot has to earn
    # its keep. It is deliberately small: a tiebreaker between candidates the
    # gauntlet cannot separate, not a term that outvotes winning games. 0.05
    # costs a wholly singleton deck five points of win rate against a fully
    # consolidated one; the champion in `decks/green_run/` was 23/60 singles,
    # which that prices at ~1.9 points.
    consolidation_weight: float = 0.0
    # How many copies a card number needs before it stops costing anything. 2
    # is "no 1-ofs"; 4 asks for playsets throughout and is usually too strict
    # to be worth the win rate it spends.
    consolidation_floor: int = 2
    # Chance that a mutation, instead of rolling a fresh card, duplicates a
    # card the deck already runs thin. Without it the penalty is unclimbable:
    # every random swap makes another singleton, so the search can only pay the
    # cost and never collect. Only consulted when the weight is non-zero.
    consolidation_bias: float = 0.5
    rules: cfg.RulesConfig = field(default_factory=lambda: cfg.DEFAULT)


def copies_by_number(deck: Sequence[str], db: CardDB) -> Counter:
    """How many of each card *number* the deck runs.

    Counted by `base_id`, the same key the 4-per-number rule uses, so two
    printings of one card are one stack here as well as in `validate`.
    """
    counts: Counter = Counter()
    for card_id in deck:
        if card_id in db:
            counts[db[card_id].base_id] += 1
    return counts


def thin_share(deck: Sequence[str], db: CardDB, *, floor: int = 2) -> float:
    """Share of the deck sitting in stacks of fewer than ``floor`` copies.

    The quantity `consolidation_weight` is charged against: 0.0 is a deck of
    playsets, 1.0 a deck of nothing but singletons. Measured in *cards*, not in
    stacks, so it says how much of a draw is unrepeatable rather than how long
    the list is.
    """
    if not deck:
        return 0.0
    counts = copies_by_number(deck, db)
    thin = sum(n for n in counts.values() if n < floor)
    return thin / sum(counts.values()) if counts else 0.0


class DeckEvolver:
    def __init__(self, pool: Sequence[CardDef], gauntlet: Sequence[Sequence[str]],
                 config: DeckGenConfig = DeckGenConfig(),
                 db: CardDB | None = None,
                 agent_factory: Callable[[int, int], object] | None = None,
                 agent_factories: Sequence[Callable[[int, int], object]] | None = None,
                 colors: Sequence | None = None):
        self.db = db or default_db()
        # An EXTRA card is never a card in the 60. It is played out of its own
        # pile and `validate` rejects a main deck holding one, so a search that
        # can draw one builds decks that are illegal by construction — which is
        # exactly what happened the first time this was pointed at
        # `--pool implemented`: the champion of a 25-generation run was three
        # copies of an EXTRA Cookie deep and could not be played.
        self.pool = [c for c in pool
                     if config.rules.extra_cards_in_main_deck
                     or c.type is not CardType.EXTRA]
        self.gauntlet = [list(d) for d in gauntlet]
        self.cfg = config
        self.rng = random.Random(config.seed)
        self.agent_factory = agent_factory or self._default_agent
        # Scoring under several pilots at once selects for decks that are
        # actually good rather than decks tuned to one pilot's blind spots.
        self.agent_factories = list(agent_factories) if agent_factories else None
        self.cookies = [c for c in self.pool if c.is_cookie]
        if not self.cookies:
            raise ValueError("card pool contains no Cookie cards")
        self._cache: dict[tuple, float] = {}
        # Colours that can actually carry a deck on their own: a handful of
        # BLACK/PURE cards exist pool-wide but never enough Cookies to open on.
        self._by_color: dict = {}
        for card in self.pool:
            self._by_color.setdefault(card.color, []).append(card)
        self._colors = [c for c, cards in self._by_color.items()
                        if sum(1 for x in cards if x.is_cookie) >= 8]
        if not self._colors:
            self._colors = list(self._by_color)
        # Pinning the colours turns the search into "the best deck this colour
        # can build" rather than "the best deck", which is what you want when
        # the goal is one champion per archetype rather than one overall.
        self.fixed_colors = self.resolve_colors(colors) if colors else None

    def _default_agent(self, seat: int, seed: int):
        return SeatedAgent(HeuristicAgent(db=self.db, seed=seed), seat)

    @staticmethod
    def rl_pilot(checkpoint: str = "rl_agent.pt", db: CardDB | None = None):
        """An ``agent_factory`` that flies candidate decks with a trained policy.

        Fitness measured under the heuristic answers "is this a good deck for a
        greedy one-ply script?". Measured under the RL agent it answers a
        question closer to the one you care about. Slower — roughly half the
        games per second — so budget accordingly.
        """
        from .agentfile import find_checkpoint
        from .rl import RLAgent, Trainer, encoder_for

        db = db or default_db()
        net = Trainer.load_net(find_checkpoint(checkpoint))
        # Built once and shared across candidates, but read off the policy
        # rather than assumed — a wide checkpoint wants different rows.
        encoder = encoder_for(net, db)

        def factory(seat: int, seed: int):
            return RLAgent(net, seat, encoder=encoder, db=db,
                           training=False, seed=seed)

        return factory

    # -- colour identity --------------------------------------------------
    def resolve_colors(self, colors: Sequence) -> list:
        """Accept Color members or their names ('BLUE', 'blue')."""
        known = {c.value.upper(): c for c in self._by_color}
        out = []
        for color in colors:
            resolved = known.get(str(getattr(color, "value", color)).upper())
            if resolved is None:
                raise ValueError(f"no {color!r} cards in this pool; "
                                 f"have {sorted(known)}")
            out.append(resolved)
        return out

    def available_colors(self) -> list:
        """Colours this pool can build a standalone deck in, name-ordered."""
        return sorted(self._colors, key=lambda c: c.value)

    def _subpool(self, colors) -> tuple[list, list]:
        """The pool restricted to ``colors``, as (cards, cookies)."""
        if not colors:
            return self.pool, self.cookies
        cards = [c for color in colors for c in self._by_color.get(color, ())]
        cookies = [c for c in cards if c.is_cookie]
        # Never hand back a subpool that cannot fill a legal deck.
        if len(cookies) < 8:
            return self.pool, self.cookies
        return cards, cookies

    def _pick_colors(self) -> list:
        if self.fixed_colors:
            return list(self.fixed_colors)
        n = min(self.cfg.color_identity, len(self._colors))
        return self.rng.sample(self._colors, n) if n else []

    def _deck_colors(self, deck: Sequence[str]) -> list:
        """The colours a deck already commits to, most-played first."""
        if self.fixed_colors:
            return list(self.fixed_colors)
        if not self.cfg.color_identity:
            return []
        counts = Counter(self.db[c].color for c in deck if c in self.db)
        return [color for color, _ in counts.most_common(self.cfg.color_identity)]

    # -- legality --------------------------------------------------------
    def repair(self, deck: list[str], colors=None) -> list[str]:
        """Force a candidate back inside the deck-construction rules.

        Topping up is drawn from ``colors`` when given, so a repair cannot
        quietly splash a deck back out of its own colour identity.
        """
        rules = self.cfg.rules
        db = self.db
        fill, fill_cookies = self._subpool(colors)
        out: list[str] = []
        by_number: Counter[str] = Counter()
        flips = 0

        for card_id in deck:
            if card_id not in db:
                continue
            card = db[card_id]
            if (card.type is CardType.EXTRA
                    and not rules.extra_cards_in_main_deck):
                continue        # never in the 60, whatever a parent carried
            if by_number[card.base_id] >= rules.max_copies_by_number:
                continue
            if card.is_flip and flips >= rules.max_flip_cards:
                continue
            out.append(card_id)
            by_number[card.base_id] += 1
            flips += card.is_flip
            if len(out) == rules.deck_size:
                break

        # Top up with legal random picks.
        guard = 0
        while len(out) < rules.deck_size and guard < 5000:
            guard += 1
            card = self.rng.choice(fill)
            if by_number[card.base_id] >= rules.max_copies_by_number:
                continue
            if card.is_flip and flips >= rules.max_flip_cards:
                continue
            out.append(card.id)
            by_number[card.base_id] += 1
            flips += card.is_flip

        if rules.require_cookie_card and not any(db[c].is_cookie for c in out):
            out[-1] = self.rng.choice(fill_cookies).id
        return out

    def random_deck(self) -> list[str]:
        # Commit to a colour identity up front. Sampled uniformly over a wide
        # multi-set pool, a candidate draws ten colours' worth of cards and can
        # pay for almost none of them, so every candidate is equally dead and
        # the search gets no gradient to climb.
        colors = self._pick_colors()
        cards, cookies = self._subpool(colors)
        # Seed with a healthy Cookie count; a deck that cannot refill its
        # battle area loses on the spot and teaches the search nothing.
        seed_cookies = self._draw(cookies, 30)
        rest = self._draw(cards, 40)
        deck = seed_cookies + rest
        self.rng.shuffle(deck)
        return self.repair(deck, colors)

    def _draw(self, cards, n: int) -> list[str]:
        """``n`` card ids sampled from ``cards``.

        In stacks rather than one at a time when the run is priced on
        consolidation: starting every candidate as sixty singletons hands the
        search a population that is uniformly bad on the term it is being
        judged on, which is no gradient at all.
        """
        if not self.cfg.consolidation_weight:
            return [self.rng.choice(cards).id for _ in range(n)]
        out: list[str] = []
        floor = max(1, self.cfg.consolidation_floor)
        while len(out) < n:
            card = self.rng.choice(cards)
            copies = min(n - len(out),
                         self.rng.randint(floor, self.cfg.rules.max_copies_by_number))
            out.extend([card.id] * copies)
        return out

    # -- genetic operators ----------------------------------------------
    def mutate(self, deck: list[str]) -> list[str]:
        # Mutating within the deck's own colours keeps a working list working.
        # Splashing is still reachable: a colour that grows dominant through
        # repeated mutation becomes part of the identity on the next pass.
        colors = self._deck_colors(deck)
        cards, _ = self._subpool(colors)
        child = list(deck)
        for _ in range(self.rng.randint(1, self.cfg.mutations)):
            index = self.rng.randrange(len(child))
            child[index] = self._replacement(child, index, cards)
        return self.repair(child, colors)

    def _replacement(self, deck: list[str], index: int, cards) -> str:
        """What a mutation puts in a slot: a fresh card, or a second copy.

        A random swap almost always creates another singleton, so a search
        priced on consolidation could only ever pay that price -- it needs a
        move that *thickens* the list to have anywhere to climb. Duplicating a
        card the deck already runs is that move, and it costs the slot it took,
        so it is still selection that decides whether it was worth it.
        """
        cfg_ = self.cfg
        if cfg_.consolidation_weight and self.rng.random() < cfg_.consolidation_bias:
            counts = copies_by_number(deck, self.db)
            thin = [c for c in set(deck)
                    if c in self.db
                    and counts[self.db[c].base_id] < cfg_.consolidation_floor]
            # Never duplicate the card being replaced -- that is a no-op that
            # spends a mutation.
            thin = [c for c in thin if c != deck[index]]
            if thin:
                return self.rng.choice(sorted(thin))
        return self.rng.choice(cards).id

    def crossover(self, a: list[str], b: list[str]) -> list[str]:
        cut = self.rng.randrange(10, self.cfg.rules.deck_size - 10)
        child = a[:cut] + b[cut:]
        return self.repair(child, self._deck_colors(child))

    # -- fitness ---------------------------------------------------------
    def fitness(self, deck: list[str], seed_block: int = 0, *,
                games: int | None = None) -> float:
        """Win rate against the gauntlet, seats alternated.

        ``seed_block`` selects which shuffles are played. All candidates in a
        generation share a block — that keeps the comparison between them fair
        — but the block changes each generation, so a deck has to keep winning
        on shuffles it has never seen to survive.
        """
        games = games or self.cfg.games_per_eval
        key = (tuple(sorted(deck)), seed_block, games)
        if key in self._cache:
            return self._cache[key]

        if self.agent_factories:
            share = max(1, games // len(self.agent_factories))
            scores = [self._score(deck, seed_block, share, factory)
                      for factory in self.agent_factories]
            score = (min(scores) if self.cfg.pilot_aggregate == "min"
                     else sum(scores) / len(scores))
            self._cache[key] = score
            return score

        score = self._score(deck, seed_block, games, self.agent_factory)
        self._cache[key] = score
        return score

    def consolidation_penalty(self, deck: Sequence[str]) -> float:
        """What this deck's thin slots cost it, in win rate."""
        cfg_ = self.cfg
        if not cfg_.consolidation_weight:
            return 0.0
        return cfg_.consolidation_weight * thin_share(
            deck, self.db, floor=cfg_.consolidation_floor)

    def objective(self, deck: list[str], seed_block: int = 0, *,
                  games: int | None = None) -> float:
        """What the search maximises: `fitness` minus the consolidation price.

        Kept separate from `fitness` on purpose. Every number this module
        reports to a person -- the holdout, the validation score, a checkpoint
        header -- is a *win rate*, and a win rate that has quietly had a
        deckbuilding preference subtracted from it is a number nobody can
        compare against anything. The penalty exists to break ties inside the
        search; it does not belong in the result.
        """
        return self.fitness(deck, seed_block, games=games) - self.consolidation_penalty(deck)

    def _score(self, deck: list[str], seed_block: int, games: int,
               factory) -> float:
        base = self.cfg.seed + seed_block * 1_000_003
        wins = 0.0
        for i in range(games):
            foe = self.gauntlet[i % len(self.gauntlet)]
            seat = (i // len(self.gauntlet)) % 2
            decks = [deck, foe] if seat == 0 else [foe, deck]
            controllers = [factory(0, base + i), factory(1, base + 7_000 + i)]
            game = Game(decks, controllers, db=self.db, seed=base + i)
            game.setup()
            state = game.play_out()
            if state.winner == seat:
                wins += 1.0
            elif state.winner == -1:
                wins += 0.5
        return wins / games

    def holdout(self, deck: list[str], games: int = 400) -> float:
        """Score on a seed block the search never touched."""
        return self.fitness(deck, seed_block=10_000, games=games)

    # -- search ----------------------------------------------------------
    def _select(self, scored: list[tuple[float, list[str]]]) -> list[str]:
        contenders = [self.rng.choice(scored) for _ in range(self.cfg.tournament)]
        return max(contenders, key=lambda t: t[0])[1]

    def evolve(self, *, log=print,
               seeds: Sequence[Sequence[str]] | None = None,
               on_generation: Callable[[int, list[str], dict], None] | None = None
               ) -> tuple[list[str], float, list[dict]]:
        """Run the search. ``seeds`` starts it from decks somebody already built.

        Without seeds the first generation is noise, and most of the budget
        goes on rediscovering that a deck should pick a colour and curve. Given
        a real list — a tournament deck, a previous champion — the population
        starts as that list plus mutations of it, and the run is a *tuning*
        pass rather than a search from scratch. Half the population is still
        random, because a seeded population that is nothing but one deck's
        children cannot leave the neighbourhood it started in.

        A seed is repaired first: it may be somebody else's format, a deck with
        an EXTRA pile, or simply illegal here.

        ``on_generation(generation, champion, row)`` is called once a
        generation with that generation's best deck and its history row. A run
        against a real field is measured in hours, and until this existed the
        only way to see any of it was to wait for the end — a crash, a laptop
        lid, or simply a number that stopped climbing threw the whole budget
        away. The callback is where a caller writes a checkpoint; it is handed
        a copy, so what it does with the list cannot reach the search.
        """
        cfg_ = self.cfg
        population: list[list[str]] = []
        for seed in (seeds or ()):
            repaired = self.repair(list(seed), self._deck_colors(seed))
            population.append(repaired)
            while len(population) < cfg_.population // 2 and seeds:
                population.append(self.mutate(list(repaired)))
        population = population[:cfg_.population]
        population += [self.random_deck()
                       for _ in range(cfg_.population - len(population))]
        history: list[dict] = []
        finalists: list[list[str]] = []

        for generation in range(cfg_.generations):
            block = generation if cfg_.reseed_each_generation else 0
            # Elites are re-scored on the new block too, so a deck cannot ride
            # one lucky evaluation to the end of the run.
            # (objective, win rate, deck): selection reads the first, every
            # number a person is shown comes from the second.
            ranked = sorted(((self.objective(d, block), self.fitness(d, block), d)
                             for d in population), key=lambda t: -t[0])
            scored = [(obj, deck) for obj, _, deck in ranked]
            champion = ranked[0]
            finalists.append(list(champion[2]))

            mean = sum(rate for _, rate, _ in ranked) / len(ranked)
            thin = thin_share(champion[2], self.db,
                              floor=cfg_.consolidation_floor)
            row = {"generation": generation, "best": champion[1],
                   "mean": mean, "objective": champion[0], "thin": thin}
            history.append(row)
            log(f"  gen {generation:3}  best {champion[1]:6.1%}  "
                f"mean {mean:6.1%}"
                + (f"  thin {thin:5.1%}" if cfg_.consolidation_weight else ""))
            if on_generation is not None:
                on_generation(generation, list(scored[0][1]), dict(row))

            nxt = [list(d) for _, d in scored[:cfg_.elite]]
            while len(nxt) < cfg_.population:
                parent_a = self._select(scored)
                parent_b = self._select(scored)
                child = self.crossover(parent_a, parent_b)
                if self.rng.random() < cfg_.mutation_rate:
                    child = self.mutate(child)
                nxt.append(child)
            population = nxt

        return self._final_selection(finalists, history, log=log)

    def _final_selection(self, finalists: list[list[str]], history: list[dict],
                         *, log=print) -> tuple[list[str], float, list[dict]]:
        """Pick the winner on a validation block, not on training scores.

        Taking the best score seen during the run is the winner's curse: across
        many noisy estimates the maximum is biased high. Re-scoring the per-
        generation champions on shuffles none of them were selected on gives a
        number that survives contact with fresh seeds.
        """
        unique: list[list[str]] = []
        seen = set()
        for deck in reversed(finalists):        # prefer later generations
            key = tuple(sorted(deck))
            if key not in seen:
                seen.add(key)
                unique.append(deck)
        shortlist = unique[:8]

        log(f"  validating {len(shortlist)} champions on unseen shuffles")
        games = max(self.cfg.games_per_eval, 120)
        scored = [(self.objective(d, seed_block=9_999, games=games),
                   self.fitness(d, seed_block=9_999, games=games), d)
                  for d in shortlist]
        _, score, deck = max(scored, key=lambda t: t[0])
        history.append({"validation_best": score,
                        "validated": len(shortlist)})
        return deck, score, history


def describe(deck: Sequence[str], db: CardDB, *, legality: bool = True) -> str:
    """Human-readable decklist, grouped and sorted.

    ``legality`` off drops the footer. An EXTRA pile is six cards and holds
    nothing but EXTRA cards, so running the main-deck rules over it prints two
    problems that are not problems -- and a reader who sees "legal: False" on a
    file believes it.
    """
    counts = Counter(deck)
    lines = []
    by_type: dict[str, list[tuple[int, CardDef]]] = {}
    for card_id, n in counts.items():
        card = db[card_id]
        by_type.setdefault(card.type.value, []).append((n, card))
    for type_name in ("COOKIE", "FLIP", "ITEM", "TRAP", "STAGE", "EXTRA"):
        entries = by_type.get(type_name)
        if not entries:
            continue
        total = sum(n for n, _ in entries)
        lines.append(f"{type_name} ({total})")
        for n, card in sorted(entries, key=lambda t: (-t[0], t[1].id)):
            extra = (f"LV{card.level} HP{card.hp}" if card.is_cookie else "")
            lines.append(f"  {n}x {card.id:10} {card.name:32} {extra}")
    if not legality:
        return "\n".join(lines)
    report = validate(list(deck), db)
    lines.append(f"\nlegal: {report.ok}  size {report.size}  flips {report.flip_count}")
    if report.problems:
        lines.append("  " + "; ".join(report.problems))
    return "\n".join(lines)
