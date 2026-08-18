#!/usr/bin/env python3
"""A browser front end for the engine: play against a bot, or watch two play.

    python play_server.py                 # http://localhost:8080

The engine calls its controllers *re-entrantly* — a trap window and every
mid-effect decision happen inside ``game.step`` — so a human seat cannot be
driven by returning from an HTTP handler. Instead the match runs on its own
thread and a human controller blocks on a queue until the browser answers the
question the engine is currently asking. Bot seats go through the same gate,
which is what makes pause / step / speed work for spectating.

The state the browser sees is always built on the match thread (right before it
blocks), so nothing ever reads a half-mutated GameState.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import random
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional, Sequence

from braverse import (STARTER_DECKS, CardDB, Game, HeuristicAgent, RandomAgent,
                      SeatedAgent, default_db, validate)
from braverse import actions as A
from braverse.enums import CardType, Marker
from braverse.rps import CHOICES, THROWS, decide_first_player
from braverse.state import CardInstance, Cookie, GameState

ROOT = Path(__file__).resolve().parent
VIEWER = ROOT / "viewer"
IMAGES = ROOT / "card_images"

# How long the browser spends playing one action out, per event. A bot seat
# waits this out before deciding again, so an attack, the HP cards it turned
# face up, and the Cookie it broke all finish on screen before the next move
# starts. Kept in step with the timings in viewer/app.js.
EVENT_SECONDS = {"attack": 0.9, "reveal": 0.7, "faint": 0.3, "skill": 0.4,
                 "draw": 0.22, "damage": 0.25}
# What is still on screen after the last event of its kind *starts*: a revealed
# card is held face up to be read, and a broken Cookie falls apart.
TAIL_SECONDS = {"reveal": 2.4, "faint": 1.5, "skill": 1.5, "draw": 0.7,
                "damage": 0.9}
MAX_REVEALS = 6          # the browser animates no more than this many
MAX_SCENE_PAUSE = 9.0


def scene_seconds(events: list) -> float:
    """How long the browser spends playing one batch of events.

    Counts each animation through to its *end*, not its start — the point is
    that a bot does not move again while a reveal is still being read.
    """
    counts: dict = {}
    for event in events:
        kind = event.get("type", "")
        # One draw event can be several cards, each of which flies separately.
        counts[kind] = counts.get(kind, 0) + (event.get("count", 1)
                                              if kind == "draw" else 1)
    counts["reveal"] = min(counts.get("reveal", 0), MAX_REVEALS)

    total = 0.0
    for kind, n in counts.items():
        if not n:
            continue
        gap = EVENT_SECONDS.get(kind, 0.0)
        tail = TAIL_SECONDS.get(kind)
        total += (n - 1) * gap + tail if tail else n * gap
    return min(total, MAX_SCENE_PAUSE)


# ---------------------------------------------------------------------------
# decks and pilots
# ---------------------------------------------------------------------------
def available_decks() -> dict[str, list[str]]:
    """Starter lists plus any decklist file `evolve_deck.py` wrote."""
    decks = {name: list(cards) for name, cards in STARTER_DECKS.items()}
    for path in sorted(ROOT.glob("*.txt")):
        try:
            text = path.read_text()
            blob = json.loads(text[text.index("{", text.rindex("\n\n")):])
            deck = list(blob["deck"])
        except Exception:
            continue
        if len(deck) >= 10:
            decks[path.stem] = deck
    return decks


def available_pilots() -> list[str]:
    pilots = ["human", "heuristic", "random"]
    pilots += [f"rl:{p.name}" for p in sorted(ROOT.glob("*.pt"))]
    return pilots


def make_pilot(kind: str, seat: int, db: CardDB, seed: int, runner: "Match"):
    if kind == "human":
        return HumanController(runner, seat)
    if kind == "random":
        return Paced(SeatedAgent(RandomAgent(seed=seed), seat), runner)
    if kind.startswith("rl:"):
        from braverse.rl import RLAgent, Trainer
        net = Trainer.load_net(ROOT / kind[3:])
        return Paced(RLAgent(net, seat, db=db, seed=seed), runner)
    return Paced(SeatedAgent(HeuristicAgent(db=db, seed=seed), seat), runner)


# ---------------------------------------------------------------------------
# serialising the state for the browser
# ---------------------------------------------------------------------------
def card_json(db: CardDB, card_id: str) -> dict:
    defn = db[card_id]
    attack = defn.attack
    return {
        "id": defn.id,
        "name": defn.name,
        "type": defn.type.value,
        "color": defn.color.value,
        "level": defn.level,
        "hp": defn.hp,
        "text": defn.description,
        "flipText": defn.flip_text,
        "cost": str(defn.play_cost) if not defn.is_cookie else "",
        "attack": ({"name": attack.name, "cost": str(attack.cost),
                    "damage": attack.damage, "text": attack.text}
                   if attack else None),
        "markers": sorted(m.value for m in defn.markers),
        "img": f"/card_images/{defn.id}.webp",
    }


def instance_json(db: CardDB, card: CardInstance) -> dict:
    out = card_json(db, card.card_id)
    out["uid"] = card.uid
    out["rested"] = card.rested
    return out


def cookie_json(db: CardDB, cookie: Cookie) -> dict:
    defn = cookie.defn(db)
    card = instance_json(db, cookie.card)
    # A Cookie in the battle area rests as a *Cookie* — attacking, paying a
    # Blocker cost — while `CardInstance.rested` only ever tracks cards resting
    # in the support area. Reading the card's flag here left an attacker sitting
    # upright all through the opponent's turn, with nothing to show it had swung.
    card["rested"] = cookie.rested
    out = {
        "uid": cookie.uid,
        "owner": cookie.owner,
        "card": card,
        "hp": cookie.remaining_hp,
        "maxHp": cookie.max_hp(db),
        "rested": cookie.rested,
        "level": cookie.level(db),
        "attackDamage": cookie.attack_damage(db),
        "blocker": defn.has(Marker.BLOCKER),
        "summonedThisTurn": cookie.summoned_this_turn,
        # The HP pile is face down. Only its size is public.
        "hpPile": len(cookie.hp_cards),
        "hpPileCards": [instance_json(db, c) for c in cookie.hp_cards],
    }
    return out


def player_json(db: CardDB, player, state: GameState) -> dict:
    return {
        "index": player.index,
        "deckCount": len(player.deck),
        "handCount": len(player.hand),
        "hand": [instance_json(db, c) for c in player.hand],
        "battle": [cookie_json(db, c) for c in player.battle],
        "support": [instance_json(db, c) for c in player.support],
        "supportActive": len(player.active_support()),
        "stage": [instance_json(db, c) for c in player.stage],
        "trash": [instance_json(db, c) for c in player.trash],
        "trashCount": len(player.trash),
        "break": [instance_json(db, c) for c in player.break_area],
        "breakLevel": player.break_level_total(db),
        "supportedThisTurn": player.supported_this_turn,
    }


def state_json(db: CardDB, state: GameState) -> dict:
    return {
        "turn": state.turn_number,
        "turnPlayer": state.turn_player,
        "phase": state.phase.value,
        "over": state.over,
        "winner": state.winner,
        "winReason": state.win_reason,
        "players": [player_json(db, p, state) for p in state.players],
        "log": state.log[-400:],
    }


def skill_label(db: CardDB, state: GameState, action: A.Action) -> str:
    """What to call this action on the card that offers it.

    Attacks are printed with a name — "Tracker's Arrow" — on 980 of the 1200
    Cookies; the older `<{P}{P}> Deals 2 damage.` printing has none, and falls
    back to "Attack". No 【Activate】 skill in the pool prints a name at all, so
    those are always just "Activate".
    """
    if isinstance(action, A.Attack):
        found = state.find_cookie(action.attacker_uid)
        attack = found[1].defn(db).attack if found else None
        return (attack.name if attack and attack.name else "Attack")
    if isinstance(action, A.ActivateSkill):
        return "Activate"
    if isinstance(action, A.PlayCookie):
        return "Play"
    if isinstance(action, A.PlaySupportCard):
        found = state.find_card(action.card_uid)
        defn = db[found[2].card_id] if found else None
        return "Set stage" if defn and defn.type is CardType.STAGE else "Play"
    if isinstance(action, A.PlaceSupport):
        return "Place as support"
    if isinstance(action, A.PlayTrap):
        return "Spring trap"
    if isinstance(action, A.Block):
        return "Block"
    if isinstance(action, A.Pass):
        return "Pass"
    return "End turn" if isinstance(action, A.EndTurn) else "Play"


def action_json(db: CardDB, state: GameState, index: int, action: A.Action) -> dict:
    """One option, tagged with the uids it touches so the board can light up."""
    out: dict[str, Any] = {
        "index": index,
        "kind": type(action).__name__,
        "label": action.describe(db, state),
        "skill": skill_label(db, state, action),
        "uids": [],
    }
    for attr in ("card_uid", "source_uid", "attacker_uid", "blocker_uid"):
        uid = getattr(action, attr, None)
        if uid is not None:
            out["uids"].append(uid)
            out["subject"] = uid
    target = getattr(action, "target_uid", None)
    if target is not None:
        out["uids"].append(target)
        out["target"] = target
    return out


def option_json(db: CardDB, index: int, option: Any) -> dict:
    """A mid-effect choice: a Cookie, a card, or a yes/no."""
    if isinstance(option, Cookie):
        defn = option.defn(db)
        return {"index": index, "kind": "cookie", "uid": option.uid,
                "label": f"{defn.name} ({option.remaining_hp} HP)",
                "img": f"/card_images/{defn.id}.webp", "subject": option.uid}
    if isinstance(option, CardInstance):
        defn = db[option.card_id]
        return {"index": index, "kind": "card", "uid": option.uid,
                "label": defn.name,
                "img": f"/card_images/{defn.id}.webp", "subject": option.uid}
    if isinstance(option, bool):
        return {"index": index, "kind": "bool", "label": "Yes" if option else "No"}
    return {"index": index, "kind": "other", "label": str(option)}


# ---------------------------------------------------------------------------
# controllers
# ---------------------------------------------------------------------------
class MatchAborted(Exception):
    """Raised inside the match thread when the match is replaced or stopped."""


def centre_style(options: Sequence) -> Optional[str]:
    """Questions that belong in the middle of the table, not off to one side.

    The opening toss is the whole screen's business for those few seconds, and
    making someone track to the far right to throw rock is a silly way to start
    a game.
    """
    labels = [o for o in options if isinstance(o, str)]
    if len(labels) != len(options):
        return None
    if set(labels) == set(THROWS):
        return "throw"
    if set(labels) == set(CHOICES):
        return "choice"
    return None


def hand_pick(prompt: str, options: Sequence, player) -> Optional[dict]:
    """Should this question be answered by pointing at the cards themselves?

    Decided structurally rather than by prompt string. Cookies in a battle area
    and cards in a support area are already on the table, so those are answered
    by clicking them where they sit. A card in your hand, trash, break area or
    deck is not something you can reach on the board — the hand is a fan and the
    rest are face-down piles — so those come up as a strip to pick from. Only
    the verb on the confirm button reads from the prompt.
    """
    if not options or not all(isinstance(o, CardInstance) for o in options):
        return None
    on_the_table = {c.uid for c in player.support} | {c.uid for c in player.stage}
    if any(o.uid in on_the_table for o in options):
        return None
    reachable = ({c.uid for c in player.hand} | {c.uid for c in player.trash}
                 | {c.uid for c in player.break_area} | {c.uid for c in player.deck})
    if not all(o.uid in reachable for o in options):
        return None
    lowered = prompt.lower()
    if "discard" in lowered:
        verb = "Discard"
    elif "cookie" in lowered:
        verb = "Play Cookie"
    else:
        verb = "Choose"
    return {"verb": verb}


class HumanController:
    """Hands every decision to the browser and blocks until it answers."""

    name = "human"

    def __init__(self, match: "Match", seat: int):
        self.match = match
        self.seat = seat

    def choose_action(self, state: GameState, options: Sequence[A.Action]):
        if not options:
            return None
        db = self.match.db
        payload = [action_json(db, state, i, a) for i, a in enumerate(options)]
        index = self.match.ask(self.seat, "Your move", payload, optional=False)
        return options[index] if index is not None else None

    def choose(self, state: GameState, prompt: str, options: Sequence, *, optional: bool):
        if not options:
            return None
        db = self.match.db
        payload = [option_json(db, i, o) for i, o in enumerate(options)]
        pick = hand_pick(prompt, options, state.players[self.seat])
        index = self.match.ask(self.seat, prompt, payload, optional=optional,
                               pick=pick, centre=centre_style(options))
        return options[index] if index is not None else None

    def choose_many(self, state: GameState, prompt: str, options: Sequence, *,
                    count: int, optional: bool):
        """Ask for the whole selection at once: pick N, then confirm."""
        if not options:
            return []
        db = self.match.db
        payload = [option_json(db, i, o) for i, o in enumerate(options)]
        pick = hand_pick(prompt, options, state.players[self.seat]) or {"verb": "Confirm"}
        picked = self.match.ask(self.seat, prompt, payload,
                                optional=optional, count=count, pick=pick)
        if not isinstance(picked, list):
            picked = [] if picked is None else [picked]
        return [options[i] for i in picked if 0 <= i < len(options)]


class Paced:
    """A bot seat, slowed to the speed the viewer asked for.

    Only the turn-level action is paced. Decisions taken inside effect
    resolution resolve instantly, so an attack and everything it triggers reads
    as one beat.
    """

    def __init__(self, agent, match: "Match"):
        self.agent = agent
        self.match = match
        self.name = getattr(agent, "name", "bot")

    def choose_action(self, state: GameState, options: Sequence[A.Action]):
        self.match.gate()
        return self.agent.choose_action(state, options)

    def choose(self, state: GameState, prompt: str, options: Sequence, *, optional: bool):
        return self.agent.choose(state, prompt, options, optional=optional)


# ---------------------------------------------------------------------------
# the match thread
# ---------------------------------------------------------------------------
@dataclass
class MatchConfig:
    decks: list  # two deck names
    pilots: list  # two pilot names
    seed: Optional[int] = None
    delay: float = 0.7
    paused: bool = False
    reveal: bool = False   # show both hands even in a human game


class Match:
    """One game, running on its own thread, observable over HTTP."""

    def __init__(self, config: MatchConfig, db: CardDB):
        self.config = config
        self.db = db
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.version = 0
        self.snapshot: dict = {}
        self.pending: Optional[dict] = None
        self._answer: Optional[int] = None
        self._answered = False
        self.stopped = False
        self.step_once = False
        self.error: Optional[str] = None
        self.started = time.time()
        self._view_cache: tuple = (-1, None)
        self._prev: Optional[dict] = None   # last snapshot, for reveal diffing
        self._event_id = 0
        self._gated_event = 0
        self._queued: list = []   # events taken from the action, not a diff
        self._scene_pause = 0.0   # how long the browser needs for the last batch
        self._log_mark = 0        # log lines already turned into events
        self._event_mark = 0      # structured engine records already consumed

        decks = available_decks()
        self.deck_lists = [list(decks[name]) for name in config.decks]
        seed = config.seed if config.seed is not None else random.randrange(1 << 30)
        self.seed = seed
        self.controllers = [
            make_pilot(config.pilots[i], i, db, seed + 100 * i, self) for i in range(2)
        ]
        self.game = Game(self.deck_lists, self.controllers, db=db, seed=seed)
        self.human_seats = [i for i, p in enumerate(config.pilots) if p == "human"]
        self.toss = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        with self.cond:
            self.stopped = True
            self.cond.notify_all()

    def _run(self) -> None:
        game = self.game
        try:
            # The guide opens with rock-paper-scissors and the winner chooses
            # who starts. It runs here rather than in `Game.setup` so bulk
            # self-play is not made to play it a million times.
            toss = decide_first_player(self.controllers, game.state, game.state.rng)
            game.first_player = toss.first_player
            self.toss = toss     # `decide_first_player` logs each round live
            self.publish()
            game.setup()
            self.publish()
            while not game.state.over:
                options = game.legal_actions()
                if not options:
                    break
                seat = game.to_move()
                self.publish()
                action = game.controller(seat).choose_action(game.state, options)
                if action is None:
                    break
                self._note_action(action)
                game.step(action)
            self.publish()
        except MatchAborted:
            return
        except Exception as exc:  # surface engine errors in the UI, not the console
            import traceback
            traceback.print_exc()
            with self.cond:
                self.error = f"{type(exc).__name__}: {exc}"
                self.version += 1
                self.cond.notify_all()

    # -- events ----------------------------------------------------------
    def _note_action(self, action) -> None:
        """Queue what the player just did, for the browser to play out.

        Taken from the action rather than a state diff: it is a *thing the
        player did*, and by the time it resolves the board may look nothing
        like it did when they chose it.
        """
        if isinstance(action, (A.ActivateSkill, A.PlaySupportCard)):
            self._note_skill(action)
            return
        if not isinstance(action, A.Attack):
            return
        found_a = self.game.state.find_cookie(action.attacker_uid)
        found_t = self.game.state.find_cookie(action.target_uid)
        if not found_a or not found_t:
            return
        attacker, target = found_a[1], found_t[1]
        self._queued.append({
            "type": "attack",
            "attacker": attacker.uid,
            "attackerOwner": attacker.owner,
            "attackerName": attacker.name(self.db),
            "target": target.uid,
            "targetOwner": target.owner,
            "targetName": target.name(self.db),
            "damage": attacker.attack_damage(self.db),
        })

    def _note_skill(self, action) -> None:
        """A skill, Item or Stage the player set off.

        Most of these have no visible consequence at all — a draw, a buff, a
        cost that could not be met — so without this the card just sat there
        and nothing happened on screen.
        """
        state = self.game.state
        uid = getattr(action, "source_uid", None) or getattr(action, "card_uid", None)
        found = state.find_cookie(uid)
        if found is not None:
            owner, card = found[0].index, instance_json(self.db, found[1].card)
        else:
            located = state.find_card(uid)
            if located is None:
                return
            owner, card = located[0].index, instance_json(self.db, located[2])
        self._queued.append({
            "type": "skill",
            "owner": owner,
            "uid": uid,
            "card": card,
            "name": skill_label(self.db, state, action),
        })

    _TRAP_LINE = None

    def _damage_events(self) -> list:
        """Damage, straight from the engine's structured record.

        Not a diff: a swing and a "Then, ..." rider both take HP off the same
        Cookie in the same step, and only the engine knows which was which.
        """
        events = []
        for record in self.game.state.events[self._event_mark:]:
            if record.get("kind") != "damage":
                continue
            events.append({
                "type": "damage",
                "cookie": record["cookie"],
                "owner": record["owner"],
                "amount": record["amount"],
                "source": record["source"],
                "left": record["left"],
            })
        self._event_mark = len(self.game.state.events)
        return events

    def _trap_events(self, snap: dict) -> list:
        """Traps sprung inside the defender's response window.

        Those never pass through the match loop — the engine asks the defender
        mid-attack — so they are read off the one thing that does record them.
        """
        import re
        if self._TRAP_LINE is None:
            type(self)._TRAP_LINE = re.compile(r"^T\d+ P(\d) springs trap (.+)$")
        events = []
        log = self.game.state.log
        for line in log[self._log_mark:]:
            match = self._TRAP_LINE.match(line)
            if not match:
                continue
            name = match.group(2)
            matches = self.db.by_name(name)
            if not matches:
                continue
            events.append({
                "type": "skill",
                "owner": int(match.group(1)),
                "uid": None,
                "card": card_json(self.db, matches[0].id),
                "name": "Trap",
            })
        self._log_mark = len(log)
        return events

    @staticmethod
    def _draw_events(prev: Optional[dict], snap: dict) -> list:
        """Cards that came off a deck into a hand.

        Carries a *count* and nothing else: a drawn card is secret, and the
        animation is a face-down card travelling from the deck to the hand, so
        there is no identity to send and nothing to leak. Cards that arrive in
        hand some other way — a Cookie bounced off the board — are excluded by
        pairing the arrivals against how far the deck actually fell.
        """
        if not prev:
            return []
        events = []
        for index, (was, now) in enumerate(zip(prev["players"], snap["players"])):
            held = {c["uid"] for c in was["hand"]}
            arrived = sum(1 for c in now["hand"] if c["uid"] not in held)
            off_deck = was["deckCount"] - now["deckCount"]
            drawn = min(arrived, off_deck)
            if drawn > 0:
                events.append({"type": "draw", "owner": index, "count": drawn})
        return events

    @staticmethod
    def _faint_events(prev: Optional[dict], snap: dict) -> list:
        """Cookies that left the battle area — fainted, trashed or bounced."""
        if not prev:
            return []
        events = []
        for index, (was, now) in enumerate(zip(prev["players"], snap["players"])):
            still_there = {c["uid"] for c in now["battle"]}
            broke = {c["uid"] for c in now["break"]}
            for cookie in was["battle"]:
                if cookie["uid"] in still_there:
                    continue
                events.append({
                    "type": "faint",
                    "owner": index,
                    "cookie": cookie["uid"],
                    "card": cookie["card"],
                    # A Cookie placed in the trash never reaches the break area,
                    # so its owner's opponent banks no Level for it.
                    "broke": cookie["card"]["uid"] in broke,
                })
        return events

    @staticmethod
    def _reveal_events(prev: Optional[dict], snap: dict) -> list:
        """HP cards that turned face up since the last snapshot.

        Damage moves an HP card straight from the face-down pile to the trash,
        so the diff of those two zones *is* the reveal — no engine callback
        needed, and a card bounced out of the pile by an effect correctly does
        not count as revealed.
        """
        if not prev:
            return []
        events = []
        for index, (was, now) in enumerate(zip(prev["players"], snap["players"])):
            pile = {}
            for cookie in was["battle"]:
                for card in cookie["hpPileCards"]:
                    pile[card["uid"]] = cookie["uid"]
            seen = {c["uid"] for c in was["trash"]}
            for card in now["trash"]:
                if card["uid"] in seen or card["uid"] not in pile:
                    continue
                events.append({
                    "type": "reveal",
                    "owner": index,
                    "cookie": pile[card["uid"]],
                    "card": card,
                    "flip": card["type"] == "FLIP",
                })
        return events

    def publish(self, pending: Optional[dict] = None) -> None:
        """Snapshot the state. Always called from the match thread."""
        snap = state_json(self.db, self.game.state)
        # Several publishes can share one game state — the loop publishes before
        # a decision, then the pacing gate or a human prompt publishes again.
        # Diffing those against each other would erase the reveal before the
        # browser ever polled it, so an unchanged state carries its events
        # forward and `eventId` tells the browser it has already animated them.
        if self._prev is not None and snap["players"] == self._prev["players"]:
            snap["events"] = self._prev.get("events", [])
        else:
            # Ordered the way the browser should play them: the attack, then
            # whatever damage turned face up, then anything that fainted.
            snap["events"] = (self._queued
                              + self._trap_events(snap)
                              + self._damage_events()
                              + self._draw_events(self._prev, snap)
                              + self._reveal_events(self._prev, snap)
                              + self._faint_events(self._prev, snap))
            self._queued = []
            if snap["events"]:
                self._event_id += 1
                self._scene_pause = scene_seconds(snap["events"])
        snap["eventId"] = self._event_id
        self._prev = snap
        snap["seed"] = self.seed
        snap["pilots"] = list(self.config.pilots)
        snap["decks"] = list(self.config.decks)
        snap["humanSeats"] = self.human_seats
        snap["firstPlayer"] = self.game.first_player
        with self.cond:
            self.snapshot = snap
            self.pending = pending
            self.version += 1
            self.cond.notify_all()

    # -- pacing ----------------------------------------------------------
    def gate(self) -> None:
        """Bot seats pass through here once per turn-level decision."""
        self.publish()
        with self.cond:
            while not self.stopped and self.config.paused and not self.step_once:
                self.cond.wait(0.2)
            if self.stopped:
                raise MatchAborted()
            stepping = self.step_once
            self.step_once = False
            delay = 0.0 if stepping else self.config.delay
            # Wait out the scene: the browser is still lunging the attacker in,
            # turning HP cards face up and breaking a Cookie, and there is no
            # point playing on underneath it. Skipped at speed 0, where the
            # viewer has asked for no pacing at all.
            if delay and self._event_id != self._gated_event:
                delay += self._scene_pause
            self._gated_event = self._event_id
        if delay:
            deadline = time.time() + delay
            with self.cond:
                while not self.stopped and time.time() < deadline:
                    self.cond.wait(min(0.1, max(0.0, deadline - time.time())))
                if self.stopped:
                    raise MatchAborted()

    # -- questions -------------------------------------------------------
    def ask(self, seat: int, prompt: str, options: list, *, optional: bool,
            count: int = 1, pick: Optional[dict] = None,
            centre: Optional[str] = None):
        """Block the match thread until the browser answers.

        Returns an index, or a list of them when ``count`` is more than one —
        the browser shows those as a pick-and-confirm over the hand.
        """
        pending = {
            "seat": seat,
            "prompt": prompt,
            "options": options,
            "optional": optional,
            "count": count,
            "pick": pick,
            "centre": centre,
            "id": self.version + 1,
        }
        self.publish(pending)
        with self.cond:
            while not self._answered and not self.stopped:
                self.cond.wait(0.2)
            if self.stopped:
                raise MatchAborted()
            answer = self._answer
            self._answered = False
            self._answer = None
            self.pending = None
        if answer is None:
            return [] if count > 1 else None
        if isinstance(answer, list):
            picks = [i for i in answer if isinstance(i, int) and 0 <= i < len(options)]
            return picks[:count] if count > 1 else (picks[0] if picks else None)
        if not 0 <= answer < len(options):
            return [] if count > 1 else None
        return [answer] if count > 1 else answer

    def answer(self, index) -> bool:
        with self.cond:
            if self.pending is None:
                return False
            self._answer = index
            self._answered = True
            self.cond.notify_all()
        return True

    # -- what the browser is allowed to see -------------------------------
    def view(self) -> dict:
        with self.cond:
            version = self.version
            key = (version, self.config.reveal)
            if self._view_cache[0] == key:
                cached = dict(self._view_cache[1])
                cached["paused"] = self.config.paused
                cached["delay"] = self.config.delay
                return cached
            snap = json.loads(json.dumps(self.snapshot)) if self.snapshot else {}
            pending = self.pending
            error = self.error
        if snap:
            self._hide(snap)
            snap["version"] = version
            snap["pending"] = pending
            snap["error"] = error
            snap["paused"] = self.config.paused
            snap["delay"] = self.config.delay
            snap["reveal"] = self.config.reveal
        result = snap or {"version": version, "error": error, "pending": None}
        with self.cond:
            self._view_cache = (key, result)
        return result

    def _hide(self, snap: dict) -> None:
        """Strip hidden information on the way out to the browser.

        Filtering here rather than at snapshot time keeps one true state on the
        match thread and lets the reveal toggle work without replaying anything.
        """
        # `reveal` is a spectator's tool and nothing else. It used to read
        # `config.reveal or not human_seats`, which had it exactly backwards:
        # in a match you were *playing* the toggle handed you the bot's hand,
        # and while watching two bots it did nothing because the second clause
        # was already true. Nobody at the table means it may be honoured; a
        # human seat means it never is.
        reveal = self.config.reveal and not self.human_seats
        for player in snap["players"]:
            # Hot seat sees both hands — two people sharing one screen have no
            # secrets from each other, and the setup dialog says so.
            if not reveal and player["index"] not in self.human_seats:
                player["hand"] = []
            for cookie in player["battle"]:
                # The HP pile stays face down for everyone, including its owner
                # and including under `reveal` — the whole tension of a battle
                # is not knowing which card the next point of damage turns up.
                cookie["hpPileCards"] = []


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Server:
    def __init__(self, db: CardDB):
        self.db = db
        self.match: Optional[Match] = None
        self.lock = threading.Lock()

    def new_match(self, config: MatchConfig) -> Match:
        with self.lock:
            if self.match is not None:
                self.match.stop()
            match = Match(config, self.db)
            self.match = match
        match.start()
        return match


class Handler(BaseHTTPRequestHandler):
    server_version = "BraverseViewer/1.0"
    app: Server = None  # type: ignore[assignment]

    def log_message(self, fmt, *args):  # quiet; the UI is the output
        pass

    # -- helpers ---------------------------------------------------------
    def _send(self, code: int, body: bytes, content_type: str, cache: bool = False):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload: Any, code: int = 200):
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _file(self, path: Path, cache: bool = False):
        if not path.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(200, path.read_bytes(), ctype, cache=cache)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    # -- routes ----------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._file(VIEWER / "index.html")
        elif path in ("/app.js", "/sfx.js", "/style.css"):
            self._file(VIEWER / path.lstrip("/"))
        elif path.startswith("/card_images/"):
            name = Path(path).name
            if "/" in name or ".." in name:
                self._send(400, b"bad path", "text/plain")
                return
            self._file(IMAGES / name, cache=True)
        elif path == "/api/config":
            decks = available_decks()
            self._json({
                "decks": [{"name": n, "size": len(c)} for n, c in decks.items()],
                "pilots": available_pilots(),
            })
        elif path == "/api/state":
            match = self.app.match
            self._json(match.view() if match else {"version": 0, "idle": True})
        elif path == "/api/deck":
            name = self._query().get("name", "")
            decks = available_decks()
            if name not in decks:
                self._json({"error": "unknown deck"}, 404)
                return
            counts: dict[str, int] = {}
            for card_id in decks[name]:
                counts[card_id] = counts.get(card_id, 0) + 1
            cards = [dict(card_json(self.app.db, cid), count=n)
                     for cid, n in counts.items() if cid in self.app.db]
            cards.sort(key=lambda c: (c["type"], -(c["level"] or 0), c["name"]))
            report = validate(decks[name], self.app.db)
            self._json({"name": name, "cards": cards,
                        "legal": report.ok, "problems": report.problems})
        else:
            self._send(404, b"not found", "text/plain")

    def _query(self) -> dict:
        from urllib.parse import parse_qs, urlparse
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()
        if path == "/api/new":
            decks = available_decks()
            names = body.get("decks") or ["st9_sea_fairy", "st8_wind_archer"]
            pilots = body.get("pilots") or ["human", "heuristic"]
            if any(n not in decks for n in names):
                self._json({"error": "unknown deck"}, 400)
                return
            if any(p not in available_pilots() for p in pilots):
                self._json({"error": "unknown pilot"}, 400)
                return
            seed = body.get("seed")
            config = MatchConfig(
                decks=list(names),
                pilots=list(pilots),
                seed=int(seed) if seed not in (None, "") else None,
                delay=float(body.get("delay", 0.7)),
                paused=bool(body.get("paused", False)),
                reveal=bool(body.get("reveal", False)),
            )
            try:
                match = self.app.new_match(config)
            except Exception as exc:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)
                return
            self._json({"ok": True, "seed": match.seed})
        elif path == "/api/choose":
            match = self.app.match
            if match is None:
                self._json({"error": "no match"}, 400)
                return
            index = body.get("index")
            if isinstance(index, list):
                picked = [int(i) for i in index]
            else:
                picked = None if index is None else int(index)
            ok = match.answer(picked)
            self._json({"ok": ok})
        elif path == "/api/control":
            match = self.app.match
            if match is None:
                self._json({"error": "no match"}, 400)
                return
            with match.cond:
                if "paused" in body:
                    match.config.paused = bool(body["paused"])
                if "delay" in body:
                    match.config.delay = max(0.0, min(5.0, float(body["delay"])))
                if "reveal" in body:
                    match.config.reveal = bool(body["reveal"])
                if body.get("step"):
                    match.step_once = True
                    match.config.paused = True
                match.cond.notify_all()
            self._json({"ok": True})
        else:
            self._send(404, b"not found", "text/plain")


class Viewer(ThreadingHTTPServer):
    """The HTTP server, wired so it cannot outlive the terminal that ran it."""

    daemon_threads = True       # a live request must never hold the process up
    allow_reuse_address = True  # restart on the same port without a TIME_WAIT wait


def port_holder(port: int) -> str:
    """PID currently listening on ``port``, for a useful error message."""
    import subprocess
    try:
        out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""
    return " ".join(out.split())


def main() -> None:
    import signal

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    db = default_db()
    Handler.app = Server(db)
    try:
        httpd = Viewer((args.host, args.port), Handler)
    except OSError as exc:
        pid = port_holder(args.port)
        print(f"cannot listen on port {args.port}: {exc}")
        if pid:
            print(f"an older viewer is still running as PID {pid} — stop it with:\n"
                  f"    kill {pid}")
        print(f"or pick another port:\n    python play_server.py --port {args.port + 1}")
        raise SystemExit(1)

    url = f"http://{args.host}:{args.port}/"
    print(f"CookieRun: Braverse — visual player on {url}   (ctrl-c to stop)")
    if not args.no_browser:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    # Closing the terminal sends SIGHUP and `kill` sends SIGTERM; take both as
    # "shut down", so a stray server can never end up holding the port. The
    # shutdown has to run off the serving thread or it deadlocks.
    def stop(signum, _frame):
        print(f"\nstopping ({signal.Signals(signum).name})")
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, stop)

    try:
        httpd.serve_forever()
    finally:
        if Handler.app.match is not None:
            Handler.app.match.stop()   # release a match thread blocked on a human
        httpd.server_close()
        print("bye")


if __name__ == "__main__":
    main()
