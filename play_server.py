#!/usr/bin/env python3
"""A browser front end for the engine: play against a bot, a person, or watch.

    python play_server.py                 # http://localhost:8080
    python play_server.py --lan           # also reachable from the network

Two people on one network play through a *room*: one hosts, the other opens the
link and joins. The server owns the game either way — the browser is only ever
shown a seat's own view and offered a list of moves the server built, so it can
neither see the other hand nor name a card that was not on offer.

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
import os
import random
import secrets
import socket
import sys
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional, Sequence

from braverse.deckfile import DECK_DIR, read_decklist
from braverse import (DEFAULT_RULES as RULES, STARTER_DECKS, CardDB, Game,
                      HeuristicAgent, RandomAgent, SeatedAgent, default_db,
                      implemented_pool, validate)
from braverse import actions as A
from braverse.enums import CardType, Marker
from braverse.rps import CHOICES, THROWS, decide_first_player
from braverse.state import CardInstance, Cookie, GameState

ROOT = Path(__file__).resolve().parent
VIEWER = ROOT / "viewer"
IMAGES = ROOT / "card_images"

# Frozen by PyInstaller (see braverse.spec), ROOT is the throwaway directory the
# bundle unpacks into — fine for the assets baked in, useless for the decklists
# and trained pilots someone drops next to the binary. Look there too.
SIDE = (Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False) else ROOT)

# How long the browser spends playing one action out, per event. A bot seat
# waits this out before deciding again, so an attack, the HP cards it turned
# face up, and the Cookie it broke all finish on screen before the next move
# starts. Kept in step with the timings in viewer/app.js.
EVENT_SECONDS = {"attack": 0.9, "reveal": 0.7, "faint": 0.3, "skill": 0.4,
                 "draw": 0.22, "damage": 0.25, "heal": 0.25, "trap": 1.0}
# What is still on screen after an event of its kind *starts*.
# A revealed card is held face up to be read, and a broken Cookie falls apart.
TAIL_SECONDS = {"attack": 0.9, "reveal": 2.4, "faint": 1.5, "skill": 1.5,
                "draw": 0.7, "damage": 0.9, "heal": 0.9, "trap": 2.2}
MAX_REVEALS = 6          # the browser animates no more than this many
MAX_SCENE_PAUSE = 9.0

# How long a polling browser is held before being answered with "nothing new".
# Long enough that an idle match costs no traffic at all, short enough that a
# proxy or a sleeping laptop never sits on a dead connection.
POLL_HOLD = 25.0

# Filled in by `main` when the server is told to listen off this machine.
LAN_URLS: list[str] = []


def scene_seconds(events: list) -> float:
    """How long the browser spends playing one batch of events.

    Mirrors `playEvents` in viewer/app.js: the events are walked in order, each
    starting a fixed gap after the one before, and the scene ends when the last
    thing on screen finishes rather than when the last one starts — a bot must
    not move again while a reveal is still being read.
    """
    clock = 0.0
    end = 0.0
    reveals_clear = 0.0
    reveals = 0
    for event in events:
        kind = event.get("type", "")
        if kind == "reveal":
            if reveals >= MAX_REVEALS:
                continue
            reveals += 1
        # One draw event is several cards, each of which flies separately.
        n = max(1, event.get("count", 1) if kind == "draw" else 1)
        per = EVENT_SECONDS.get(kind, 0.0)
        tail = TAIL_SECONDS.get(kind, 0.0)
        # A Cookie leaving the board waits for any revealed card to clear.
        start = max(clock, reveals_clear) if kind == "faint" else clock
        end = max(end, start + (n - 1) * per + tail)
        if kind == "reveal":
            reveals_clear = max(reveals_clear, start + tail)
        clock = start + n * per
    return min(end, MAX_SCENE_PAUSE)


# ---------------------------------------------------------------------------
# decks and pilots
# ---------------------------------------------------------------------------
def scan(pattern: str) -> list[Path]:
    """Files matching `pattern` beside the script, and beside a frozen binary.

    A name found in both wins from `SIDE`, so someone can override a bundled
    decklist by dropping their own next to the executable.
    """
    found = {p.name: p for p in ROOT.glob(pattern)}
    found.update({p.name: p for p in SIDE.glob(pattern)})
    return sorted(found.values(), key=lambda p: p.name)


def deck_files() -> dict[str, Path]:
    """Decklist files by name: loose beside the script, then in `decks/`.

    `decks/` is scanned second so the curated folder wins a name clash with a
    loose file — co-evolution writes there, and those lists are the ones a run
    actually stands behind.
    """
    found = {p.stem: p for p in scan("*.txt")}
    found.update({p.stem: p for p in scan(f"{DECK_DIR}/*.txt")})
    return found


def available_decks() -> dict[str, list[str]]:
    """Starter lists, every decklist file on disk, then saved decks.

    Saved decks come last, so a deck built in the browser wins a name clash
    with a starter list — the user made that one on purpose.
    """
    return {name: deck for name, (deck, _) in available_decklists().items()}


def available_extra_decks() -> dict[str, list[str]]:
    """The EXTRA deck that goes with each name in ``available_decks()``.

    Empty for every list that does not play them, which is most of them — a
    deck without an EXTRA deck is a legal deck.
    """
    return {name: extra for name, (_, extra) in available_decklists().items()}


def available_decklists() -> dict[str, tuple[list[str], list[str]]]:
    """Every playable list as ``(deck, extra)``."""
    lists: dict[str, tuple[list[str], list[str]]] = {
        name: (list(cards), []) for name, cards in STARTER_DECKS.items()}
    for name, path in sorted(deck_files().items()):
        try:
            deck, extra = read_decklist(path)
        except Exception:
            continue        # not one of ours, or half-written by a live run
        if len(deck) >= 10:
            lists[name] = (deck, extra)
    lists.update(load_saved_decks())
    return lists


def deck_source(name: str) -> str:
    """Where a deck in `available_decks()` came from, for the UI."""
    if name in load_saved_decks():
        return "saved"
    if name in STARTER_DECKS:
        return "starter"
    path = deck_files().get(name)
    if path is not None and path.parent.name == DECK_DIR:
        return "evolved"
    return "file"


# ---------------------------------------------------------------------------
# saved decks
# ---------------------------------------------------------------------------
# Decks built in the browser live in one JSON file, `{name: [card ids]}`. It
# sits beside the script (or beside a frozen binary) so a deck survives a
# restart and can be edited by hand; if that directory is read-only — a bundle
# dropped in /Applications, say — fall back to the user's home.
DECK_STORE_NAME = "saved_decks.json"
MAX_DECK_NAME = 60
MAX_DECK_CARDS = 400       # a 60-card deck with room to be mid-edit
_store_lock = threading.Lock()


def deck_store() -> Path:
    if os.access(SIDE, os.W_OK):
        return SIDE / DECK_STORE_NAME
    home = Path.home() / ".braverse"
    home.mkdir(parents=True, exist_ok=True)
    return home / DECK_STORE_NAME


def load_saved_decks() -> dict[str, tuple[list[str], list[str]]]:
    """Browser-built decks, as ``{name: (deck, extra)}``.

    Two stored shapes: a bare list, which is every deck saved before EXTRA
    decks existed, and ``{"deck": [...], "extra": [...]}``. Both are read, so
    an existing store keeps working untouched.
    """
    path = deck_store()
    if not path.is_file():
        return {}
    try:
        blob = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(blob, dict):
        return {}
    out: dict[str, tuple[list[str], list[str]]] = {}
    for name, saved in blob.items():
        if isinstance(saved, list):
            out[str(name)] = ([str(c) for c in saved], [])
        elif isinstance(saved, dict) and isinstance(saved.get("deck"), list):
            out[str(name)] = ([str(c) for c in saved["deck"]],
                              [str(c) for c in saved.get("extra") or []])
    return out


def write_saved_decks(decks: dict) -> None:
    """Store every deck in the ``{"deck": ..., "extra": ...}`` shape.

    A bare list of card ids is accepted as a deck with no EXTRA deck, which is
    the shape every caller used before EXTRA decks existed.
    """
    path = deck_store()
    tmp = path.with_suffix(".tmp")
    blob = {}
    for name, saved in decks.items():
        deck, extra = (saved, []) if isinstance(saved, list) else saved
        blob[name] = {"deck": list(deck), "extra": list(extra)}
    tmp.write_text(json.dumps(blob, indent=2, sort_keys=True))
    tmp.replace(path)          # never leave a half-written store behind


def clean_deck_name(raw: Any) -> str:
    """A name that is safe as a dict key, a dropdown label and a file stem."""
    name = " ".join(str(raw or "").split())[:MAX_DECK_NAME]
    return "".join(ch for ch in name if ch.isprintable() and ch not in '/\\:')


def clean_card_list(raw: Any) -> list[str]:
    """Card ids out of a request body, capped so one POST cannot be a decklist
    of a million cards."""
    if not isinstance(raw, list):
        return []
    return [str(c) for c in raw[:MAX_DECK_CARDS]]


def available_pilots() -> list[str]:
    pilots = ["human", "heuristic", "random"]
    pilots += [f"rl:{p.name}" for p in scan("*.pt")]
    return pilots


def make_pilot(kind: str, seat: int, db: CardDB, seed: int, runner: "Match"):
    if kind == "human":
        return HumanController(runner, seat)
    if kind == "random":
        return Paced(SeatedAgent(RandomAgent(seed=seed), seat), runner)
    if kind.startswith("rl:"):
        from braverse.rl import RLAgent, Trainer
        net = Trainer.load_net(next(p for p in scan("*.pt") if p.name == kind[3:]))
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
        # The 4-copy rule counts card *numbers*, so the deck builder groups
        # alt arts by this rather than by id.
        "baseId": defn.base_id,
        "set": defn.set_id,
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


# ---------------------------------------------------------------------------
# the card pool, for the deck builder
# ---------------------------------------------------------------------------
POOL_LIMIT = 120          # cards returned per search; the browser pages through


def _collapse(db: CardDB, deck: Sequence[str]) -> list[dict]:
    counts: dict[str, int] = {}
    for card_id in deck:
        counts[card_id] = counts.get(card_id, 0) + 1
    cards = [dict(card_json(db, cid), count=n)
             for cid, n in counts.items() if cid in db]
    cards.sort(key=lambda c: (c["type"], -(c["level"] or 0), c["name"]))
    return cards


def deck_payload(db: CardDB, deck: Sequence[str], name: str = "",
                 extra: Sequence[str] | None = None) -> dict:
    """One decklist, collapsed to distinct cards, with its legality report.

    The EXTRA deck rides along as its own list: it is a separate pile with its
    own size cap, but the two are validated together because the copy limit
    counts card numbers across both.
    """
    extra = list(extra or [])
    report = validate(list(deck), db, extra=extra)
    return {"name": name, "cards": _collapse(db, deck), "size": len(deck),
            "extra": _collapse(db, extra), "extraSize": len(extra),
            "extraMax": RULES.extra_deck_size,
            "legal": report.ok, "problems": report.problems,
            "flipCount": report.flip_count, "levels": report.level_counts}


@lru_cache(maxsize=1)
def pool_index() -> list[tuple[str, str]]:
    """(card id, haystack) for every deck-legal card, in set/number order.

    Built once: searching ~2000 cards on each keystroke otherwise re-lowercases
    the whole pool's rules text.
    """
    db = default_db()
    rows = []
    for card in db.cards.values():
        if card.is_ban or card.type is CardType.NPC:
            continue
        haystack = " ".join([card.id, card.name, card.type.value,
                             card.color.value, card.description,
                             card.flip_text,
                             card.attack.text if card.attack else "",
                             card.attack.name if card.attack else ""]).lower()
        rows.append((card.id, haystack))
    rows.sort(key=lambda row: (db[row[0]].set_id, db[row[0]].number, row[0]))
    return rows


def pool_meta(db: CardDB) -> dict:
    """The filter choices the builder offers, plus the deck-building rules."""
    sets = sorted({db[cid].set_id for cid, _ in pool_index() if db[cid].set_id})
    types = sorted({db[cid].type.value for cid, _ in pool_index()})
    colors = sorted({db[cid].color.value for cid, _ in pool_index()
                     if db[cid].color.value})
    return {
        "sets": sets,
        "types": types,
        "colors": colors,
        "rules": {"deckSize": RULES.deck_size,
                  "maxCopies": RULES.max_copies_by_number,
                  "maxFlip": RULES.max_flip_cards,
                  "extraSize": RULES.extra_deck_size},
    }


def search_pool(db: CardDB, query: dict) -> dict:
    """Filter the pool. Returns one page of cards and the total match count."""
    text = " ".join(query.get("q", "").lower().split())
    want_type = query.get("type", "")
    want_color = query.get("color", "")
    want_set = query.get("set", "")
    playable = query.get("playable") == "1"
    coded = implemented_ids() if playable else None

    matches = []
    for card_id, haystack in pool_index():
        card = db[card_id]
        if text and not all(word in haystack for word in text.split()):
            continue
        if want_type and card.type.value != want_type:
            continue
        if want_color and card.color.value != want_color:
            continue
        if want_set and card.set_id != want_set:
            continue
        if coded is not None and card_id not in coded:
            continue
        matches.append(card_id)

    try:
        offset = max(0, int(query.get("offset", 0)))
    except ValueError:
        offset = 0
    page = matches[offset:offset + POOL_LIMIT]
    return {"total": len(matches), "offset": offset, "limit": POOL_LIMIT,
            "cards": [card_json(db, cid) for cid in page]}


@lru_cache(maxsize=1)
def implemented_ids() -> frozenset[str]:
    """Ids the engine plays correctly — everything else is a vanilla body."""
    return frozenset(c.id for c in implemented_pool(default_db()))


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
        # 【Awaken】: the cards this one was stacked on top of, so the board can
        # show that a Cookie is two cards deep.
        "under": [instance_json(db, c) for c in cookie.under],
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
        # The EXTRA deck is not a hidden zone: both players may read it at any
        # time, so it is projected in full for either seat.
        "extra": [instance_json(db, c) for c in player.extra_deck],
        "extraCount": len(player.extra_deck),
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
    if isinstance(action, A.PlayExtra):
        return "Awaken" if action.onto is not None else "EXTRA"
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
    host = getattr(action, "onto", None)
    if host is not None:
        # An 【Awaken】 is offered on the Cookie it lands on, not on the card in
        # the EXTRA deck: that is the piece of the board you are pointing at.
        out["uids"].append(host)
        out["target"] = host
        out["subject"] = host
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


# The mulligan question, as the browser sees it. Answered in the middle of the
# table like the opening toss: it is a setup decision with the whole hand on
# screen behind it, not one item in a list of moves.
MULLIGAN_CHOICES = ("Mulligan", "Keep this hand")
MULLIGAN_PROMPT = ("Mulligan? Your whole hand goes back into the deck and you "
                   "draw 6 new cards.")


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
    if set(labels) == set(MULLIGAN_CHOICES):
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
    elif "break area" in lowered:
        # The refresh cost sends a Cookie to your break area — one step closer
        # to losing. Naming it "Play Cookie" because the word "Cookie" appears
        # in the prompt reads as a reward for the click it actually punishes.
        verb = "To Break Area"
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

    def wants_mulligan(self, state: GameState, hand: Sequence) -> bool:
        """The one optional redraw of the opening hand.

        Asked with the hand already on screen — the browser polls the same
        state — so the question is just the two buttons.
        """
        payload = [option_json(self.match.db, i, o)
                   for i, o in enumerate(MULLIGAN_CHOICES)]
        index = self.match.ask(self.seat, MULLIGAN_PROMPT, payload,
                               optional=False, centre="choice")
        return index == 0

    def choose_many(self, state: GameState, prompt: str, options: Sequence, *,
                    count: int, optional: bool, up_to: bool = False):
        """Ask for the whole selection at once: pick N, then confirm.

        ``up_to`` is the "up to N" form — the confirm button is live from zero
        picks, so declining and picking fewer are both real answers."""
        if not options:
            return []
        db = self.match.db
        payload = [option_json(db, i, o) for i, o in enumerate(options)]
        pick = hand_pick(prompt, options, state.players[self.seat]) or {"verb": "Confirm"}
        picked = self.match.ask(self.seat, prompt, payload,
                                optional=optional, count=count, pick=pick,
                                up_to=up_to)
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
    online: bool = False   # two browsers, one seat each: hide per viewer


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
        self.online = bool(config.online)
        self._view_cache: dict = {}   # (version, reveal, viewer) -> rendered view
        self._prev: Optional[dict] = None   # last snapshot, for reveal diffing
        self._event_id = 0
        self._gated_event = 0
        self._queued: list = []   # events taken from the action, not a diff
        self._scene_pause = 0.0   # how long the browser needs for the last batch
        self._log_mark = 0        # log lines already turned into events
        self._event_mark = 0      # structured engine records already consumed

        decks = available_decklists()
        self.deck_lists = [list(decks[name][0]) for name in config.decks]
        self.extra_lists = [list(decks[name][1]) for name in config.decks]
        seed = config.seed if config.seed is not None else random.randrange(1 << 30)
        self.seed = seed
        self.controllers = [
            make_pilot(config.pilots[i], i, db, seed + 100 * i, self) for i in range(2)
        ]
        self.game = Game(self.deck_lists, self.controllers,
                         extra_decks=self.extra_lists, db=db, seed=seed)
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

    def _engine_events(self) -> list:
        """Damage, healing and reveals, straight from the engine's record.

        Not a diff, and the order is the point. A swing and a "Then, ..." rider
        both take HP off the same Cookie in the same step, and only the engine
        knows which was which. A heal is the same story in reverse. And a
        reveal has to be reported *as the card turns*, before the FLIP resolves
        — read off a zone diff it can only ever be reported afterwards, which
        is what made flip effects play out before the card was shown.
        """
        db = self.db
        events = []
        for record in self.game.state.events[self._event_mark:]:
            kind = record.get("kind")
            if kind == "damage":
                events.append({
                    "type": "damage",
                    "cookie": record["cookie"],
                    "owner": record["owner"],
                    "amount": record["amount"],
                    "source": record["source"],
                    "left": record["left"],
                })
            elif kind == "heal":
                events.append({
                    "type": "heal",
                    "cookie": record["cookie"],
                    "owner": record["owner"],
                    "amount": record["amount"],
                    "left": record["left"],
                })
            elif kind == "reveal":
                card = card_json(db, record["card_id"])
                card["uid"] = record["card_uid"]
                events.append({
                    "type": "reveal",
                    "cookie": record["cookie"],
                    "owner": record["owner"],
                    "card": card,
                    "flip": record["flip"],
                })
        self._event_mark = len(self.game.state.events)
        return events

    def _trap_events(self, snap: dict) -> list:
        """Traps sprung inside the defender's response window.

        Those never pass through the match loop — the engine asks the defender
        mid-attack — so they are read off the one thing that does record them.
        They get their own event type rather than riding on `skill`: a trap is
        the one card that fires on someone else's turn, in the middle of their
        attack, so the board plays it big and in the middle rather than as a
        small pop over on its owner's half.
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
                "type": "trap",
                "owner": int(match.group(1)),
                "card": card_json(self.db, matches[0].id),
                "name": matches[0].name,
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
            # Ordered the way the browser plays them: what the player did, then
            # the engine's own record of what that did — damage, reveals and
            # heals interleaved exactly as they happened — then the two things
            # still read off a diff, which have no ordering of their own.
            snap["events"] = (self._queued
                              + self._trap_events(snap)
                              + self._engine_events()
                              + self._draw_events(self._prev, snap)
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
            centre: Optional[str] = None, up_to: bool = False):
        """Block the match thread until the browser answers.

        Returns an index, or a list of them when the question takes more than
        one — the browser shows those as a pick-and-confirm over the hand. An
        "up to N" question always answers with a list, even when N is 1: "none"
        is one of its legal answers and a bare index cannot say that.
        """
        multi = count > 1 or up_to
        pending = {
            "seat": seat,
            "prompt": prompt,
            "options": options,
            "optional": optional,
            "count": count,
            "upTo": up_to,
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
            return [] if multi else None
        if isinstance(answer, list):
            picks = [i for i in answer if isinstance(i, int) and 0 <= i < len(options)]
            return picks[:count] if multi else (picks[0] if picks else None)
        if not 0 <= answer < len(options):
            return [] if multi else None
        return [answer] if multi else answer

    def answer(self, index, *, seat: Optional[int] = None,
               pending_id: Optional[int] = None) -> bool:
        """Answer the open question. Returns False if it was not yours to answer.

        ``seat`` is checked against the seat the engine is actually asking, so
        an online opponent cannot play your turn for you, and ``pending_id``
        drops an answer to a question that has already moved on — a double
        click either side of a resolution would otherwise land on whatever
        came next.
        """
        with self.cond:
            if self.pending is None:
                return False
            if seat is not None and self.pending["seat"] != seat:
                return False
            if pending_id is not None and self.pending["id"] != pending_id:
                return False
            self._answer = index
            self._answered = True
            # Retire the question here rather than waiting for the match thread
            # to wake: a held poll is released by the version bump below, and it
            # must not be released onto a prompt that has already been answered.
            self.pending = None
            self.version += 1
            self.cond.notify_all()
        return True

    def wait_for(self, version: int, timeout: float = POLL_HOLD) -> None:
        """Hold a polling browser until the match moves past ``version``."""
        deadline = time.time() + timeout
        with self.cond:
            while not self.stopped and self.version <= version:
                left = deadline - time.time()
                if left <= 0:
                    return
                self.cond.wait(min(0.5, left))

    # -- what the browser is allowed to see -------------------------------
    def view(self, viewer: Optional[int] = None) -> dict:
        """The match as one seat is allowed to see it.

        ``viewer`` is the seat asking, or None for a spectator — and in an
        online match that is the *only* thing that decides what comes out, so
        two browsers polling the same match get two different answers.
        """
        with self.cond:
            version = self.version
            key = (version, self.config.reveal, viewer)
            if key in self._view_cache:
                cached = dict(self._view_cache[key])
                cached["paused"] = self.config.paused
                cached["delay"] = self.config.delay
                return cached
            snap = json.loads(json.dumps(self.snapshot)) if self.snapshot else {}
            pending = self.pending
            error = self.error
        pending = self._hide_pending(pending, viewer)
        if snap:
            self._hide(snap, viewer)
            snap["version"] = version
            snap["pending"] = pending
            snap["error"] = error
            snap["paused"] = self.config.paused
            snap["delay"] = self.config.delay
            snap["reveal"] = self.config.reveal
            snap["online"] = self.online
            snap["viewerSeat"] = viewer
        result = snap or {"version": version, "error": error, "pending": pending,
                          "online": self.online, "viewerSeat": viewer}
        with self.cond:
            # One entry per (version, viewer); versions turn over constantly, so
            # drop the lot rather than grow a cache nobody reads twice.
            if len(self._view_cache) > 16:
                self._view_cache.clear()
            self._view_cache[key] = result
        return result

    def _hide_pending(self, pending: Optional[dict], viewer: Optional[int]) -> Optional[dict]:
        """What the seat *not* being asked is told about the question.

        Its options are the asking player's hand as often as not, so nothing
        but the fact that a question is out goes to anyone else. The prompt
        text stays: "Sea Fairy Cookie — choose a card to discard" is public
        information, and without it the wait reads as the game having hung.
        """
        if pending is None or not self.online or pending["seat"] == viewer:
            return pending
        return {
            "seat": pending["seat"],
            "prompt": pending["prompt"],
            "id": pending["id"],
            "options": [],
            "optional": False,
            "count": 1,
            "pick": None,
            "centre": None,
            "waiting": True,
        }

    def _hide(self, snap: dict, viewer: Optional[int] = None) -> None:
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
            if self.online:
                # Two browsers, two seats: the hot-seat rule below would hand
                # each player the other's hand, and `reveal` is not offered at
                # all. A viewer holds one seat's cards and nobody else's; a
                # spectator (seat None) holds none.
                if player["index"] != viewer:
                    player["hand"] = []
            # Hot seat sees both hands — two people sharing one screen have no
            # secrets from each other, and the setup dialog says so.
            elif not reveal and player["index"] not in self.human_seats:
                player["hand"] = []
            for cookie in player["battle"]:
                # The HP pile stays face down for everyone, including its owner
                # and including under `reveal` — the whole tension of a battle
                # is not knowing which card the next point of damage turns up.
                cookie["hpPileCards"] = []


# ---------------------------------------------------------------------------
# rooms: one match, two browsers
# ---------------------------------------------------------------------------
# No I, O, 0 or 1 — the code gets read down a phone or typed off a screen.
CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def room_code() -> str:
    return "".join(secrets.choice(CODE_LETTERS) for _ in range(4))


class Room:
    """A two-seat match, addressed by a code and entered with a token.

    The code is public — it is in the link you send — so it grants nothing but
    the right to watch. The token is the seat: it is minted once when you take
    the seat, never appears in another player's view, and is what lets the
    server answer "is this yours to play?" on every move.
    """

    # A seat polling normally sits *inside* a held request for most of its life,
    # so the last time it was heard from is no use on its own — it would read as
    # away seconds after taking its turn. Presence is "holding a poll right now,
    # or heard from since before the longest one could have started".
    GONE = POLL_HOLD + 10
    IDLE_LIMIT = 30 * 60    # a room nobody has polled for this long is reaped

    def __init__(self, code: str, db: CardDB, deck: str, name: str):
        self.code = code
        self.db = db
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.decks: list[Optional[str]] = [deck, None]
        self.names = [name or "Player 1", ""]
        self.tokens: list[Optional[str]] = [secrets.token_urlsafe(16), None]
        self.seen = [time.time(), 0.0]
        self.holding = [0, 0]   # polls this seat has in flight right now
        self.created = time.time()
        self.match: Optional[Match] = None
        self.version = 0        # lobby version, so a waiting host is woken

    # -- seats -----------------------------------------------------------
    def seat_of(self, token: Optional[str]) -> Optional[int]:
        if not token:
            return None
        for seat, held in enumerate(self.tokens):
            # Constant-time compare: the token is the only thing standing
            # between a spectator and playing someone else's turn.
            if held is not None and secrets.compare_digest(held, token):
                return seat
        return None

    def join(self, deck: str, name: str) -> tuple[int, str]:
        """Take the first free seat — which is not always seat 1.

        The host can walk away from their own room, and someone else should be
        able to sit down in the empty chair rather than find the room wedged
        with one player in it and no way in.
        """
        with self.cond:
            free = next((i for i, t in enumerate(self.tokens) if t is None), None)
            if free is None:
                raise ValueError("that room is full")
            token = secrets.token_urlsafe(16)
            self.decks[free] = deck
            self.names[free] = name or f"Player {free + 1}"
            self.tokens[free] = token
            self.seen[free] = time.time()
            ready = all(t is not None for t in self.tokens)
            self.version += 1
            self.cond.notify_all()
        if ready:
            self._start()
        return free, token

    def _start(self) -> None:
        config = MatchConfig(
            decks=[self.decks[0], self.decks[1]],
            pilots=["human", "human"],
            # `reveal` is a spectator's toggle over two bots; with two people
            # playing it would be a cheat, and the control route refuses it.
            reveal=False,
            delay=0.0,
            online=True,
        )
        match = Match(config, self.db)
        with self.cond:
            old, self.match = self.match, match
            if old is not None:
                # A rematch has to keep counting from where the last game left
                # off. A browser holding a poll is asking to be told when the
                # state passes the version it already has; restarting at zero
                # would leave it waiting out the full hold before it noticed
                # that the game it is waiting on is not the game any more.
                match.version = old.version + 1
            self.version += 1
            self.cond.notify_all()
        if old is not None:
            old.stop()
        match.start()

    def rematch(self) -> bool:
        """Deal again with the same decks. Only once the last game is over."""
        with self.cond:
            match = self.match
            if any(t is None for t in self.tokens):
                return False
            if match is not None and not (match.stopped or match.error
                                          or match.snapshot.get("over")):
                return False
        self._start()
        return True

    def leave(self, seat: int) -> None:
        with self.cond:
            match, self.match = self.match, None
            self.tokens[seat] = None
            self.decks[seat] = None
            self.names[seat] = ""
            self.seen[seat] = 0.0
            self.version += 1
            self.cond.notify_all()
        if match is not None:
            # Releases the match thread, which may be blocked forever on a
            # question the seat that just walked away was being asked.
            match.stop()

    # -- presence --------------------------------------------------------
    def touch(self, seat: Optional[int]) -> None:
        if seat is not None:
            self.seen[seat] = time.time()

    def holds(self, seat: Optional[int], delta: int) -> None:
        """Count a poll into or out of flight, so a held one reads as present."""
        if seat is not None:
            with self.cond:
                self.holding[seat] = max(0, self.holding[seat] + delta)

    def here(self, seat: Optional[int]) -> bool:
        if seat is None or self.tokens[seat] is None:
            return False
        if self.holding[seat] > 0:
            return True
        return time.time() - self.seen[seat] < self.GONE

    def idle(self) -> bool:
        return time.time() - max(self.seen + [self.created]) > self.IDLE_LIMIT

    def wait_for_start(self, timeout: float = POLL_HOLD) -> None:
        """Hold the host's poll in the lobby until someone joins."""
        deadline = time.time() + timeout
        with self.cond:
            while self.match is None:
                left = deadline - time.time()
                if left <= 0:
                    return
                self.cond.wait(min(0.5, left))

    def lobby(self) -> dict:
        with self.cond:
            return {
                "code": self.code,
                "seats": [
                    {"taken": t is not None, "name": n, "deck": d, "here": self.here(i)}
                    for i, (t, n, d) in enumerate(zip(self.tokens, self.names, self.decks))
                ],
                "started": self.match is not None,
                "version": self.version,
            }


def lan_urls(port: int) -> list[str]:
    """Addresses this machine can be reached on, for the host to share."""
    urls = []
    try:
        # No packet is sent; this just asks the routing table which interface
        # would carry traffic out, which is the one a phone on the wifi can see.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))   # TEST-NET-1: routable, never live
            urls.append(f"http://{probe.getsockname()[0]}:{port}/")
        finally:
            probe.close()
    except OSError:
        pass
    return urls


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Server:
    def __init__(self, db: CardDB):
        self.db = db
        self.match: Optional[Match] = None
        self.rooms: dict[str, Room] = {}
        self.lock = threading.Lock()

    def new_match(self, config: MatchConfig) -> Match:
        with self.lock:
            if self.match is not None:
                self.match.stop()
            match = Match(config, self.db)
            self.match = match
        match.start()
        return match

    # -- rooms -----------------------------------------------------------
    def new_room(self, deck: str, name: str) -> Room:
        with self.lock:
            self._reap()
            for _ in range(50):
                code = room_code()
                if code not in self.rooms:
                    break
            else:
                raise ValueError("too many rooms open")
            room = Room(code, self.db, deck, name)
            self.rooms[code] = room
        return room

    def room(self, code: Optional[str]) -> Optional[Room]:
        if not code:
            return None
        with self.lock:
            return self.rooms.get(code.strip().upper())

    def _reap(self) -> None:
        """Drop rooms nobody has polled in a long while. Caller holds the lock."""
        for code, room in [(c, r) for c, r in self.rooms.items() if r.idle()]:
            del self.rooms[code]
            if room.match is not None:
                room.match.stop()

    def close(self) -> None:
        with self.lock:
            rooms = list(self.rooms.values())
            self.rooms.clear()
        for room in rooms:
            if room.match is not None:
                room.match.stop()


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

    def _is_local(self) -> bool:
        """Did this request come from the machine running the server?

        With `--lan` the port is open to the network, and the deck store is a
        file on the host's disk. Someone who joined a game has no business
        saving over or deleting the decks of whoever invited them, so the
        routes that write go no further than the keyboard they belong to.
        """
        host = (self.client_address[0] or "").split("%")[0]
        return host in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _deck_problem(self, name: str) -> str:
        """Why this deck cannot be taken into an online match, or "".

        A local match will happily start on a half-built list — you are only
        playing yourself — but someone else is waiting on the other end of this
        one, so it is checked at the door.
        """
        decks = available_decklists()
        if name not in decks:
            return "unknown deck"
        deck, extra = decks[name]
        report = validate(deck, self.app.db, extra=extra)
        return "" if report.ok else "; ".join(report.problems)

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
        elif path in ("/app.js", "/sfx.js", "/style.css",
                      "/builder.js", "/builder.css",
                      "/table.js", "/table.css"):
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
                # Empty unless the server was started with --lan: without it
                # nothing off this machine can reach the port, and offering a
                # link that cannot work is worse than offering none.
                "lan": LAN_URLS,
            })
        elif path == "/api/state":
            self._state(self._query())
        elif path == "/api/room":
            room = self.app.room(self._query().get("room"))
            if room is None:
                self._json({"error": "no room with that code", "gone": True}, 404)
                return
            seat = room.seat_of(self._query().get("token"))
            room.touch(seat)
            self._json({"room": room.lobby(), "seat": seat})
        elif path == "/api/deck":
            name = self._query().get("name", "")
            decks = available_decklists()
            if name not in decks:
                self._json({"error": "unknown deck"}, 404)
                return
            deck, extra = decks[name]
            payload = deck_payload(self.app.db, deck, name, extra)
            payload["source"] = deck_source(name)
            payload["list"] = deck
            payload["extraList"] = extra
            self._json(payload)
        elif path == "/api/pool":
            self._json({**pool_meta(self.app.db),
                        **search_pool(self.app.db, self._query())})
        elif path == "/api/decks":
            decks = available_decklists()
            self._json({"decks": [
                {"name": name, "size": len(cards), "source": deck_source(name),
                 "extraSize": len(extra),
                 "legal": validate(cards, self.app.db, extra=extra).ok}
                for name, (cards, extra) in decks.items()]})
        else:
            self._send(404, b"not found", "text/plain")

    def _query(self) -> dict:
        from urllib.parse import parse_qs, urlparse
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    @staticmethod
    def _since(query: dict) -> Optional[int]:
        """The version the browser already has, if it asked to be held."""
        try:
            return int(query["since"])
        except (KeyError, TypeError, ValueError):
            return None

    def _state(self, query: dict) -> None:
        """The board, for a local match or a room.

        With `since`, the response is held until something actually changes —
        an idle match costs one open connection instead of three polls a
        second, and a move reaches the other player as fast as the network
        carries it rather than on the next tick.
        """
        since = self._since(query)
        code = query.get("room")
        if not code:
            match = self.app.match
            if match is None:
                self._json({"version": 0, "idle": True})
                return
            if since is not None:
                match.wait_for(since)
            self._json(match.view())
            return

        room = self.app.room(code)
        if room is None:
            self._json({"error": "no room with that code", "gone": True}, 404)
            return
        seat = room.seat_of(query.get("token"))
        room.touch(seat)
        match = room.match
        room.holds(seat, +1)
        try:
            if match is None:
                # Sitting in the lobby: hold the host's poll until someone joins.
                if since is not None:
                    room.wait_for_start()
                match = room.match
            if match is None:
                self._json({"version": 0, "idle": True, "lobby": True,
                            "room": room.lobby(), "seat": seat})
                return
            if since is not None:
                match.wait_for(since)
        finally:
            room.touch(seat)
            room.holds(seat, -1)
        view = match.view(seat)
        view["room"] = room.lobby()
        view["seat"] = seat
        view["opponentHere"] = room.here(1 - seat) if seat is not None else None
        self._json(view)

    def _seated(self, body: dict) -> tuple[Optional[Room], Optional[int]]:
        """The room and seat this request is entitled to act as, if any."""
        room = self.app.room(body.get("room"))
        if room is None:
            return None, None
        return room, room.seat_of(body.get("token"))

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
        elif path == "/api/deck/validate":
            cards = clean_card_list(body.get("cards"))
            self._json(deck_payload(self.app.db, cards,
                                    clean_deck_name(body.get("name")),
                                    clean_card_list(body.get("extra"))))
        elif path == "/api/decks/save":
            if not self._is_local():
                self._json({"error": "decks can only be changed on the machine "
                                     "running the server"}, 403)
                return
            name = clean_deck_name(body.get("name"))
            cards = clean_card_list(body.get("cards"))
            extra = clean_card_list(body.get("extra"))
            if not name:
                self._json({"error": "give the deck a name"}, 400)
                return
            unknown = sorted({c for c in (*cards, *extra) if c not in self.app.db})
            if unknown:
                self._json({"error": f"unknown card ids: {unknown[:5]}"}, 400)
                return
            # An illegal deck still saves — half-built lists are the normal
            # state of a deck you come back to — it just cannot be played.
            try:
                with _store_lock:
                    decks = load_saved_decks()
                    decks[name] = (cards, extra)
                    write_saved_decks(decks)
            except OSError as exc:
                self._json({"error": f"could not write {deck_store()}: {exc}"}, 500)
                return
            payload = deck_payload(self.app.db, cards, name, extra)
            payload["saved"] = True
            payload["path"] = str(deck_store())
            self._json(payload)
        elif path == "/api/decks/delete":
            if not self._is_local():
                self._json({"error": "decks can only be changed on the machine "
                                     "running the server"}, 403)
                return
            name = clean_deck_name(body.get("name"))
            try:
                with _store_lock:
                    decks = load_saved_decks()
                    if name not in decks:
                        self._json({"error": "no saved deck by that name"}, 404)
                        return
                    del decks[name]
                    write_saved_decks(decks)
            except OSError as exc:
                self._json({"error": f"could not write {deck_store()}: {exc}"}, 500)
                return
            self._json({"ok": True})
        elif path == "/api/room/new":
            deck = str(body.get("deck") or "")
            problem = self._deck_problem(deck)
            if problem:
                self._json({"error": problem}, 400)
                return
            try:
                room = self.app.new_room(deck, clean_deck_name(body.get("name")))
            except ValueError as exc:
                self._json({"error": str(exc)}, 503)
                return
            self._json({"room": room.code, "seat": 0, "token": room.tokens[0]})
        elif path == "/api/room/join":
            room = self.app.room(body.get("room"))
            if room is None:
                self._json({"error": "no room with that code", "gone": True}, 404)
                return
            deck = str(body.get("deck") or "")
            problem = self._deck_problem(deck)
            if problem:
                self._json({"error": problem}, 400)
                return
            try:
                seat, token = room.join(deck, clean_deck_name(body.get("name")))
            except ValueError as exc:
                self._json({"error": str(exc)}, 409)
                return
            self._json({"room": room.code, "seat": seat, "token": token})
        elif path == "/api/room/leave":
            room, seat = self._seated(body)
            if room is None or seat is None:
                self._json({"error": "not your seat"}, 403)
                return
            room.leave(seat)
            self._json({"ok": True})
        elif path == "/api/room/rematch":
            room, seat = self._seated(body)
            if room is None or seat is None:
                self._json({"error": "not your seat"}, 403)
                return
            if not room.rematch():
                self._json({"error": "that game is still going"}, 409)
                return
            self._json({"ok": True})
        elif path == "/api/choose":
            room, seat = self._seated(body)
            if body.get("room"):
                if room is None:
                    self._json({"error": "no room with that code", "gone": True}, 404)
                    return
                if seat is None:
                    # A spectator has the room code — everyone with the link
                    # does — but no token, and so no move to make.
                    self._json({"error": "not your seat"}, 403)
                    return
                match = room.match
            else:
                match = self.app.match
            if match is None:
                self._json({"error": "no match"}, 400)
                return
            index = body.get("index")
            try:
                if isinstance(index, list):
                    picked = [int(i) for i in index]
                else:
                    picked = None if index is None else int(index)
            except (TypeError, ValueError):
                self._json({"error": "bad index"}, 400)
                return
            pending_id = body.get("pendingId")
            ok = match.answer(picked, seat=seat,
                              pending_id=int(pending_id) if pending_id is not None else None)
            self._json({"ok": ok})
        elif path == "/api/control":
            if body.get("room"):
                # Pause, step, speed and reveal are a spectator's controls over
                # a bot game. In a match against a person, pausing would freeze
                # the opponent and reveal would be a cheat.
                self._json({"error": "not available in an online match"}, 403)
                return
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
    # $PORT lets a supervisor (or a preview harness juggling several sessions)
    # hand the server a free port without rewriting the command line.
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PORT") or 8080))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--lan", action="store_true",
                        help="listen on every interface so someone else on this "
                             "network can join a room")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.lan and args.host == "127.0.0.1":
        args.host = "0.0.0.0"

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

    local = f"http://127.0.0.1:{args.port}/"
    url = local if args.host in ("0.0.0.0", "::") else f"http://{args.host}:{args.port}/"
    print(f"CookieRun: Braverse — visual player on {url}   (ctrl-c to stop)")
    if args.host not in ("127.0.0.1", "localhost"):
        LAN_URLS[:] = lan_urls(args.port)
        for shared in LAN_URLS:
            print(f"  others on this network can join at {shared}")
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
        Handler.app.close()            # and the same for every open room
        httpd.server_close()
        print("bye")


if __name__ == "__main__":
    main()
