"""Recording a game so it can be watched again, exactly as it happened.

A replay here is *not* a film of the board. It is the short list of decisions
that could not have been worked out from the rules — every answer both seats
gave, in the order the engine asked for them — plus the two decklists and the
seed. Everything else is re-derived by running the same engine over again.

That choice is the whole design. The engine is already deterministic: all
randomness goes through ``state.rng``, which is seeded, and ``game.clone()``
being a real deep copy is the same property stated another way. So given the
decks, the seed and the answers, a second run reproduces the first *bit for
bit* — the same shuffles, the same draws, the same prose log, the same
``state.events`` in the same order. A replay is therefore perfect by
construction rather than by how thorough the logging was, and it costs a few
kilobytes: a long game is a few hundred small integers.

The alternative — logging board states — would have to keep pace with every
mechanic anyone adds, and would be wrong in exactly the places the log is
thinnest. This cannot drift out of step with the engine, only fail loudly:
each decision carries a fingerprint of the options it was picked from, and a
replay whose options no longer match raises :class:`ReplayDesync` naming the
decision that diverged. A rules change that alters an old game says so instead
of quietly showing a different game.

Recording wraps the controllers, so it is UI-agnostic — ``play_server`` uses it
for played games, and a test or a harness can use it around bots:

    log = DecisionLog()
    controllers = [record(c, i, log) for i, c in enumerate(controllers)]
    ...
    recording = log.finish(game, decks=..., pilots=...)
    recording.save(path)

and watching one back is the same run with scripted seats:

    controllers = scripted(recording)
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

FORMAT = "braverse-replay"
FORMAT_VERSION = 1

# The controller protocol, in full. Only the first two are required; the engine
# asks `getattr(controller, name, None)` for the rest and takes a different
# path when they are missing — a bot has no `wants_mulligan`, and so is never
# offered the opening redraw. A wrapper that quietly added one would change the
# game it is meant to be recording, so both the recorder and the scripted seat
# mirror the surface they stand in for, method for method.
REQUIRED = ("choose_action", "choose")
OPTIONAL = ("order_effects", "wants_mulligan", "choose_many")
SURFACE = REQUIRED + OPTIONAL

# Decision kinds, short because there are hundreds of them in a file.
ACTION, CHOOSE, ORDER, MULLIGAN, MANY = "a", "c", "o", "m", "n"
DECLINED = -1        # an optional question answered with "no"


class ReplayError(Exception):
    """Base for anything that goes wrong watching a recording back."""


class ReplayDesync(ReplayError):
    """The engine asked something the recording does not answer.

    Always a real disagreement between this build and the one that recorded the
    game — a rules change, an edited decklist, a card whose text was fixed.
    """


class ReplayFinished(ReplayError):
    """The recording ran out of answers.

    Not a fault: a game saved while it was still being played replays up to the
    point it was saved and stops there.
    """


# ---------------------------------------------------------------------------
# fingerprints
# ---------------------------------------------------------------------------
def _uid_index(state) -> dict:
    """``{uid: card id}`` for every card either player can see or hold.

    Card uids come off a process-global counter, so they are *not* stable
    between the recording run and the replay — the second game's cards are
    numbered from wherever the first left off. Anything that has to match
    across the two runs therefore has to be said in card ids.
    """
    index: dict = {}
    for player in getattr(state, "players", []) or []:
        for zone in ("hand", "deck", "trash", "break_area", "support",
                     "stage", "extra_deck"):
            for card in getattr(player, zone, ()) or ():
                index[card.uid] = card.card_id
        for cookie in getattr(player, "battle", ()) or ():
            index[cookie.uid] = cookie.card.card_id
            for card in getattr(cookie, "spent_cards", ()) or ():
                index[card.uid] = card.card_id
    return index


def _describe_option(option: Any, index: dict) -> str:
    """One option, said in terms that survive into another run.

    An action is its type plus the cards it names; a card is its printed id; a
    throw or a yes/no is itself. Deliberately coarse: this is a tripwire for
    "the engine offered a different set of moves this time", not a serialised
    move list.
    """
    uid = getattr(option, "uid", None)
    if uid is not None:                       # a CardInstance or a Cookie
        card = getattr(option, "card", None)
        return index.get(uid) or getattr(card, "card_id", "") or "?"
    if isinstance(option, (str, bool, int, float)) or option is None:
        return repr(option)
    parts = [type(option).__name__]
    for name, value in sorted(vars(option).items()):
        parts.append(f"{name}={index.get(value, value) if isinstance(value, int) else value}")
    return "(" + " ".join(parts) + ")"


def fingerprint(state, options: Sequence) -> str:
    """A short digest of the whole option list, for detecting divergence."""
    index = _uid_index(state)
    blob = "|".join(_describe_option(o, index) for o in options)
    return hashlib.blake2b(blob.encode("utf-8"), digest_size=6).hexdigest()


def _label(state, option: Any, db=None) -> str:
    """The chosen option in words, for the replay browser's summary."""
    describe = getattr(option, "describe", None)
    if callable(describe) and db is not None:
        try:
            return str(describe(db, state))
        except Exception:       # display only; never break a game over a label
            pass
    return _describe_option(option, _uid_index(state))


def _index_of(options: Sequence, value: Any) -> int:
    """Where in ``options`` the controller's answer came from.

    Identity first: two legal moves can compare equal — a frozen dataclass with
    the same fields is the same move — and taking the first equal one would
    record a different index than the seat actually chose. Falls back to
    equality for a controller that rebuilt its answer rather than returning the
    object it was handed.
    """
    for i, option in enumerate(options):
        if option is value:
            return i
    for i, option in enumerate(options):
        try:
            if option == value:
                return i
        except Exception:
            continue
    return DECLINED


# ---------------------------------------------------------------------------
# recording
# ---------------------------------------------------------------------------
@dataclass
class DecisionLog:
    """Every answer both seats gave, in the order the engine asked."""

    decisions: list = field(default_factory=list)
    db: Any = None

    def note(self, seat: int, kind: str, state, options: Sequence,
             answer: Any) -> None:
        entry = {"s": seat, "k": kind, "n": len(options),
                 "g": fingerprint(state, options)}
        if kind == MULLIGAN:
            entry["i"] = 0 if answer else 1
            entry["d"] = "mulligan" if answer else "keep"
        elif kind == MANY:
            picks = [_index_of(options, a) for a in (answer or [])]
            entry["i"] = [i for i in picks if i != DECLINED]
            entry["d"] = ", ".join(_label(state, options[i], self.db)
                                   for i in entry["i"]) or "none"
        else:
            entry["i"] = DECLINED if answer is None else _index_of(options, answer)
            entry["d"] = ("decline" if entry["i"] == DECLINED
                          else _label(state, options[entry["i"]], self.db))
        self.decisions.append(entry)

    def finish(self, game, *, decks: Sequence[dict], pilots: Sequence[str],
               seed: Optional[int], app_version: str = "") -> "Recording":
        state = game.state
        return Recording(
            seed=seed,
            first_player=getattr(game, "first_player", 0),
            decks=[dict(d) for d in decks],
            pilots=list(pilots),
            surface=list(self.surface),
            decisions=list(self.decisions),
            app_version=app_version,
            log=list(getattr(state, "log", []) or []),
            result={
                "over": bool(state.over),
                "winner": state.winner,
                "reason": getattr(state, "win_reason", "") or "",
                "turns": getattr(state, "turn_number", 0),
            },
        )

    # Which methods each seat's controller actually had, filled in by `record`.
    surface: list = field(default_factory=lambda: [list(SURFACE), list(SURFACE)])


class _Recorder:
    """Wraps one controller and writes down what it answers.

    Pure pass-through: the inner controller sees the same questions in the same
    order and its answers are returned untouched, so recording a game cannot
    change it. Instances are built by :func:`record`, which gives each one only
    the methods its inner controller has.
    """

    def __init__(self, inner, seat: int, log: DecisionLog):
        self.inner = inner
        self.seat = seat
        self.log = log
        self.name = getattr(inner, "name", "bot")

    def choose_action(self, state, options):
        answer = self.inner.choose_action(state, options)
        self.log.note(self.seat, ACTION, state, options, answer)
        return answer

    def choose(self, state, prompt, options, *, optional: bool):
        answer = self.inner.choose(state, prompt, options, optional=optional)
        self.log.note(self.seat, CHOOSE, state, options, answer)
        return answer

    def order_effects(self, state, prompt, options):
        answer = self.inner.order_effects(state, prompt, options)
        self.log.note(self.seat, ORDER, state, options, answer)
        return answer

    def wants_mulligan(self, state, hand, *, free: bool = True):
        answer = self.inner.wants_mulligan(state, hand, free=free)
        self.log.note(self.seat, MULLIGAN, state, [True, False], answer)
        return answer

    def choose_many(self, state, prompt, options, *, count: int,
                    optional: bool, up_to: bool = False):
        answer = self.inner.choose_many(state, prompt, options, count=count,
                                        optional=optional, up_to=up_to)
        self.log.note(self.seat, MANY, state, options, answer)
        return answer


def _surface_of(controller) -> list:
    return [name for name in SURFACE if getattr(controller, name, None) is not None]


def _mirror(base, template: Sequence[str], name: str):
    """A subclass of ``base`` carrying only the methods in ``template``."""
    missing = [m for m in SURFACE if m not in template]
    return type(name, (base,), {m: None for m in missing})


def record(controller, seat: int, log: DecisionLog):
    """Wrap ``controller`` so every answer it gives lands in ``log``."""
    surface = _surface_of(controller)
    log.surface[seat] = surface
    cls = _mirror(_Recorder, surface, "Recorded" + type(controller).__name__)
    return cls(controller, seat, log)


# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------
@dataclass
class Recording:
    """One game, in the only form that cannot disagree with the engine."""

    seed: Optional[int]
    first_player: int
    decks: list          # [{"name", "cards", "extra"}, ...]
    pilots: list
    surface: list
    decisions: list
    app_version: str = ""
    recorded: float = field(default_factory=time.time)
    log: list = field(default_factory=list)
    result: dict = field(default_factory=dict)

    # -- shape -----------------------------------------------------------
    @property
    def deck_lists(self) -> list:
        return [list(d.get("cards") or []) for d in self.decks]

    @property
    def extra_lists(self) -> list:
        return [list(d.get("extra") or []) for d in self.decks]

    @property
    def deck_names(self) -> list:
        return [str(d.get("name") or "deck") for d in self.decks]

    def summary(self) -> dict:
        """What the browser's replay list shows without opening the file."""
        return {
            "recorded": self.recorded,
            "decks": self.deck_names,
            "pilots": list(self.pilots),
            "seed": self.seed,
            "firstPlayer": self.first_player,
            "decisions": len(self.decisions),
            "result": dict(self.result),
            "appVersion": self.app_version,
        }

    # -- disk ------------------------------------------------------------
    def to_json(self) -> dict:
        return {
            "format": FORMAT,
            "version": FORMAT_VERSION,
            "app": self.app_version,
            "recorded": self.recorded,
            "seed": self.seed,
            "firstPlayer": self.first_player,
            "decks": self.decks,
            "pilots": self.pilots,
            "surface": self.surface,
            "decisions": self.decisions,
            "result": self.result,
            "log": self.log,
        }

    @classmethod
    def from_json(cls, blob: Any) -> "Recording":
        if not isinstance(blob, dict) or blob.get("format") != FORMAT:
            raise ReplayError("not a Braverse replay file")
        if int(blob.get("version", 0)) > FORMAT_VERSION:
            raise ReplayError(
                f"replay was written by a newer version (format "
                f"{blob.get('version')}, this build reads {FORMAT_VERSION})")
        decks = blob.get("decks")
        if not isinstance(decks, list) or len(decks) != 2:
            raise ReplayError("replay does not name two decks")
        decisions = blob.get("decisions")
        if not isinstance(decisions, list):
            raise ReplayError("replay has no decisions in it")
        surface = blob.get("surface") or [list(SURFACE), list(SURFACE)]
        return cls(
            seed=blob.get("seed"),
            first_player=int(blob.get("firstPlayer") or 0),
            decks=[dict(d) for d in decks],
            pilots=list(blob.get("pilots") or ["?", "?"]),
            surface=[list(s) for s in surface],
            decisions=decisions,
            app_version=str(blob.get("app") or ""),
            recorded=float(blob.get("recorded") or 0.0),
            log=list(blob.get("log") or []),
            result=dict(blob.get("result") or {}),
        )

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_json()))
        tmp.replace(path)      # never leave a half-written replay behind
        return path

    @classmethod
    def load(cls, path: Path) -> "Recording":
        try:
            blob = json.loads(Path(path).read_text())
        except OSError as exc:
            raise ReplayError(f"could not read {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ReplayError(f"{Path(path).name} is not valid JSON: {exc}") from exc
        return cls.from_json(blob)


# ---------------------------------------------------------------------------
# playing one back
# ---------------------------------------------------------------------------
class _Scripted:
    """A seat that answers from the recording instead of thinking.

    Both seats share one cursor: the engine asks its questions in a fixed
    order, so the recording is one interleaved list rather than two, and the
    seat a decision was recorded for is itself part of what is checked.
    """

    name = "replay"

    def __init__(self, cursor: "Cursor", seat: int):
        self.cursor = cursor
        self.seat = seat

    def _take(self, kind: str, state, options: Sequence):
        return self.cursor.take(self.seat, kind, state, options)

    def choose_action(self, state, options):
        self.cursor.pace()
        index = self._take(ACTION, state, options)
        return None if index == DECLINED else options[index]

    def choose(self, state, prompt, options, *, optional: bool):
        index = self._take(CHOOSE, state, options)
        return None if index == DECLINED else options[index]

    def order_effects(self, state, prompt, options):
        index = self._take(ORDER, state, options)
        return None if index == DECLINED else options[index]

    def wants_mulligan(self, state, hand, *, free: bool = True):
        return self._take(MULLIGAN, state, [True, False]) == 0

    def choose_many(self, state, prompt, options, *, count: int,
                    optional: bool, up_to: bool = False):
        picks = self._take(MANY, state, options)
        return [options[i] for i in picks if 0 <= i < len(options)]


class Cursor:
    """Hands out the recorded answers one at a time, and checks them.

    ``strict`` is what makes a replay trustworthy rather than merely plausible:
    a question whose options no longer fingerprint the same way is a game that
    has diverged, and showing the rest of it would be showing a game that never
    happened. Loosened only by callers that would rather see the divergence
    play out than stop at it.
    """

    def __init__(self, recording: Recording, *, pace: Optional[Callable] = None,
                 strict: bool = True):
        self.recording = recording
        self.decisions = list(recording.decisions)
        self.at = 0
        self._pace = pace
        self.strict = strict
        self.desynced: Optional[str] = None

    def pace(self) -> None:
        if self._pace is not None:
            self._pace()

    @property
    def done(self) -> bool:
        return self.at >= len(self.decisions)

    def take(self, seat: int, kind: str, state, options: Sequence):
        if self.done:
            raise ReplayFinished(
                f"it was saved {len(self.decisions)} decisions in, with the "
                f"game still going")
        entry = self.decisions[self.at]
        self.at += 1
        where = f"decision {self.at} of {len(self.decisions)}"
        if entry.get("s") != seat or entry.get("k") != kind:
            self._diverged(f"{where}: this game asks seat {seat} a "
                           f"{_KIND_NAMES.get(kind, kind)} question, the "
                           f"recording has seat {entry.get('s')} answering a "
                           f"{_KIND_NAMES.get(entry.get('k'), entry.get('k'))} one")
        if entry.get("n") != len(options):
            self._diverged(f"{where}: {len(options)} options now, "
                           f"{entry.get('n')} when it was recorded "
                           f"({entry.get('d', '?')})")
        elif entry.get("g") and entry["g"] != fingerprint(state, options):
            self._diverged(f"{where}: the same number of options, but not the "
                           f"same ones ({entry.get('d', '?')})")
        index = entry.get("i", DECLINED)
        if isinstance(index, list):
            return [i for i in index if isinstance(i, int)]
        if not isinstance(index, int) or index >= len(options):
            return DECLINED
        return index

    def _diverged(self, why: str) -> None:
        self.desynced = why
        if self.strict:
            raise ReplayDesync(why)


_KIND_NAMES = {ACTION: "move", CHOOSE: "choice", ORDER: "ordering",
               MULLIGAN: "mulligan", MANY: "multi-pick"}


def scripted(recording: Recording, *, pace: Optional[Callable] = None,
             strict: bool = True) -> list:
    """Two controllers that play ``recording`` back, and the cursor driving them.

    Each seat is given exactly the methods it had when the game was recorded —
    a bot seat gets no ``wants_mulligan``, so the replay takes the same branch
    through ``Game.setup`` the original did.
    """
    cursor = Cursor(recording, pace=pace, strict=strict)
    controllers = []
    for seat in range(2):
        surface = recording.surface[seat] if seat < len(recording.surface) else SURFACE
        cls = _mirror(_Scripted, surface, f"ReplaySeat{seat}")
        controllers.append(cls(cursor, seat))
    return controllers, cursor
