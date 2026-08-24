"""Two machines playing one game, with no server holding the board.

The engine is already deterministic — every random draw goes through
``state.rng``, which is seeded — and :mod:`braverse.replay` leans on that to
store a whole game as nothing but the decisions both seats took. That is also
the design of a peer-to-peer match, arrived at from the other end: if a game is
recoverable from its decisions, then two machines that start from the same
decks and the same seed and exchange nothing *but* decisions are playing the
same game, move for move, without either of them being in charge of it.

So a peer here does not send board states, and there is no authoritative copy
of one. Each side runs its own engine. Each answers its own seat's questions
from its own browser, puts that answer on the wire, and blocks on the wire when
the question belongs to the other seat. Both engines then step forward over
identical inputs and stay bit-identical for the length of the match. A turn
costs a few dozen bytes.

**This does not stop a cheat, and cannot.** Lockstep means both machines hold
the entire ``GameState``, the opponent's hand included, because both are
running the rules — that is what removes the server. The hidden zones are
hidden by the *viewer*, not by the protocol, so a patched client can read them.
The server-hosted room in ``play_server.py`` is the mode that is safe against
your opponent; this one is safe against the network, and is the honest trade
for needing no host and no open port. Say so in the UI rather than implying a
guarantee that is not there.

What this *does* catch is divergence. Every decision travels with the
fingerprint of the option list it came from — the same
:func:`braverse.replay.fingerprint` a replay uses, said in card ids because
uids differ between runs — so if the two engines ever stop agreeing about what
was on offer, the next message raises :class:`Desync` naming the decision that
diverged. A mismatched build, an edited card, a decklist that differs by one
card: all of them stop the match immediately instead of letting the two screens
drift quietly apart, which is the failure that would otherwise be impossible to
debug from either end.

The transport is deliberately not specified here. :class:`Link` is two methods,
and the browser satisfies it over an ``RTCDataChannel`` while the tests satisfy
it with a pair of queues; nothing in this module knows which it is talking to.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any, Optional, Sequence

from .replay import (ACTION, CHOOSE, DECLINED, MANY, MULLIGAN, ORDER, SURFACE,
                     _index_of, _mirror, _surface_of, fingerprint)

# Bumped when the wire shape changes in a way an older peer would misread.
# Checked in the handshake, so a version gap is refused at the door with
# something a player can act on rather than desyncing on turn 6.
PROTOCOL = 1

# How long a seat waits on the other machine before giving up. Generous on
# purpose, and then some: the person on the other end is reading their hand,
# not answering an RPC, and a lockstep game has nowhere to resume from — a
# timeout does not drop a frame, it ends the match permanently. Someone who
# steps away to make tea should still have a game when they come back.
TURN_TIMEOUT = 30 * 60.0

# How long the *handshake* waits, which is a different kind of wait entirely.
# Signalling here is done by hand: one player copies a code into a chat window,
# the other pastes it back, and both of those are paced by people rather than
# by machines. Timing that out on an RPC's clock is the bug this constant
# exists to not have — the exchange completing in under a minute is the
# exception, not the rule. The ceiling that actually matters is
# `play_server.PeerLobby.IDLE_LIMIT`, which reaps a lobby nobody is using.
SIGNAL_TIMEOUT = 30 * 60.0


def surface_of(controller) -> tuple:
    """Which of the controller protocol's methods this seat actually answers.

    Send *this* in the handshake rather than assuming a full seat. The engine
    branches on whether a controller has a method at all — only a seat with
    ``wants_mulligan`` is offered the opening redraw — so the surface is part
    of what the two sides have to agree on, exactly as it is part of what a
    replay has to store. Two humans both have all five; a bot standing in for
    an absent player does not, and a table that claimed otherwise would have
    one engine asking a question the other never will.
    """
    return tuple(_surface_of(controller))


class NetplayError(Exception):
    """Base for every way a peer-to-peer match can fail."""


class Desync(NetplayError):
    """The two engines stopped agreeing about the game they are playing.

    Always a real disagreement rather than a network fault: the peer answered a
    question we did not ask, or answered ours from a different list of options.
    A build mismatch, an edited card, or a decklist that is not the one we were
    handed.
    """


class PeerGone(NetplayError):
    """The other side stopped answering, or said goodbye."""


class Handshake(NetplayError):
    """The two sides could not agree to start a game at all."""


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------
class Link:
    """A two-way message pipe to the other peer.

    Messages are plain JSON-able dicts and arrive in the order they were sent;
    that is the whole contract. Ordering is not a nicety — the decision stream
    *is* the game, so a transport that reordered it would be handing the engine
    a different match. An ``RTCDataChannel`` is ordered and reliable by
    default, which is the only reason this can be so thin.
    """

    def send(self, message: dict) -> None:
        raise NotImplementedError

    def recv(self, timeout: Optional[float] = None) -> Optional[dict]:
        """The next message, or None if ``timeout`` passed with nothing."""
        raise NotImplementedError

    def close(self) -> None:
        pass


class QueueLink(Link):
    """A `Link` over two queues, for tests and for the in-process bridge.

    :func:`loopback` pairs two of these, which is how the whole protocol is
    exercised without a browser or a network anywhere in the picture.
    """

    def __init__(self, inbox: "Queue[dict]", outbox: "Queue[dict]"):
        self.inbox = inbox
        self.outbox = outbox
        self.closed = False

    def send(self, message: dict) -> None:
        if self.closed:
            raise PeerGone("the connection is closed")
        self.outbox.put(dict(message))

    def recv(self, timeout: Optional[float] = None) -> Optional[dict]:
        try:
            return self.inbox.get(timeout=timeout)
        except Empty:
            return None

    def close(self) -> None:
        self.closed = True


def loopback() -> tuple[QueueLink, QueueLink]:
    """Two links wired to each other, one per peer."""
    a: "Queue[dict]" = Queue()
    b: "Queue[dict]" = Queue()
    return QueueLink(a, b), QueueLink(b, a)


# ---------------------------------------------------------------------------
# agreeing on a game
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Table:
    """What both peers must agree on before a card is dealt.

    Everything the engine needs to be reproducible on two machines at once: the
    two decklists in seat order, the EXTRA piles beside them, and the seed. The
    host settles all of it — someone has to, and the alternative is two peers
    negotiating a seed, which is more moving parts for a value nobody benefits
    from choosing.
    """

    seed: int
    decks: tuple            # (seat 0 card ids, seat 1 card ids)
    extra: tuple            # (seat 0 EXTRA ids, seat 1 EXTRA ids)
    surface: tuple          # controller surface per seat, mirrored like a replay
    app_version: str = ""
    names: tuple = ("", "")

    def as_json(self) -> dict:
        return {"seed": self.seed,
                "decks": [list(d) for d in self.decks],
                "extra": [list(e) for e in self.extra],
                "surface": [list(s) for s in self.surface],
                "app": self.app_version,
                "names": list(self.names)}

    @staticmethod
    def from_json(blob: dict) -> "Table":
        try:
            return Table(seed=int(blob["seed"]),
                         decks=tuple(tuple(d) for d in blob["decks"]),
                         extra=tuple(tuple(e) for e in blob["extra"]),
                         surface=tuple(tuple(s) for s in blob["surface"]),
                         app_version=str(blob.get("app", "")),
                         names=tuple(blob.get("names", ("", ""))))
        except (KeyError, TypeError, ValueError) as exc:
            raise Handshake(f"the other side sent a table we cannot read: {exc}")


def host_handshake(link: Link, *, deck: Sequence[str], extra: Sequence[str],
                   seed: int, name: str = "", app_version: str = "",
                   surface: Sequence[str] = SURFACE,
                   timeout: float = SIGNAL_TIMEOUT) -> Table:
    """Wait for a joiner, then settle the table and send it back.

    The host holds seat 0. It cannot build the table until the joiner's deck
    arrives, so the order is fixed: the joiner speaks first with what it is
    bringing, the host answers with the whole agreed table, and both sides
    start from that one object rather than from two views of it.

    The joiner's `hello` is also where a version gap is caught on this side —
    before the table exists, so a mismatched peer is turned away at the door
    rather than dealt into a game that cannot stay in step.
    """
    hello = _expect(link, "hello", timeout)
    _check_protocol(hello)
    _check_app(hello.get("app", ""), app_version)
    table = Table(seed=int(seed),
                  decks=(tuple(deck), tuple(hello.get("deck", ()))),
                  extra=(tuple(extra), tuple(hello.get("extra", ()))),
                  surface=(tuple(surface), tuple(hello.get("surface", SURFACE))),
                  app_version=app_version,
                  names=(name, str(hello.get("name", ""))))
    link.send({"t": "table", "protocol": PROTOCOL, "table": table.as_json()})
    return table


def join_handshake(link: Link, *, deck: Sequence[str], extra: Sequence[str],
                   name: str = "", app_version: str = "",
                   surface: Sequence[str] = SURFACE,
                   timeout: float = SIGNAL_TIMEOUT) -> Table:
    """Offer a deck to the host and take back the table it settles on.

    The joiner holds seat 1. It checks that the table it is handed actually
    contains the deck it offered: a host that sent back something else is not
    playing the game this side agreed to, and finding that out here is far
    cheaper than finding it out as a desync once the cards are down.
    """
    link.send({"t": "hello", "protocol": PROTOCOL, "app": app_version,
               "deck": list(deck), "extra": list(extra),
               "surface": list(surface), "name": name})
    reply = _expect(link, "table", timeout)
    _check_protocol(reply)
    table = Table.from_json(reply.get("table") or {})
    # The table is where the host's version is recorded, so it is what this
    # side checks — the same field that goes on to name the build in a replay.
    _check_app(table.app_version, app_version)
    if table.decks[1] != tuple(deck) or table.extra[1] != tuple(extra):
        raise Handshake("the host dealt us a deck we did not bring")
    return table


def _expect(link: Link, kind: str, timeout: float) -> dict:
    """The next message, which must be of ``kind``.

    Anything else is fatal rather than skipped. There is nothing else either
    side could legitimately be saying yet, so an unexpected message means the
    peer is at a different point in the protocol than we are.
    """
    message = link.recv(timeout=timeout)
    if message is None:
        raise Handshake("the other side never answered")
    if message.get("t") == "bye":
        raise PeerGone(str(message.get("why") or "the other side left"))
    if message.get("t") != kind:
        raise Handshake(f"expected {kind!r}, got {message.get('t')!r}")
    return message


# Two things have to agree before a card is dealt, and they are different
# questions asked of different fields — so they are two functions, each called
# from both sides of the handshake. Between them they are the only place that
# decides whether two machines may play each other.
def _check_protocol(message: dict) -> None:
    """The wire: a mismatch means the messages themselves would be misread."""
    theirs = message.get("protocol")
    if theirs != PROTOCOL:
        raise Handshake(
            f"the other side speaks netplay protocol {theirs}, this build "
            f"speaks {PROTOCOL} — one of you is on an older version")


def _check_app(theirs: str, ours: str) -> None:
    """The rules: both machines must be running the same build.

    Lockstep works because two engines fed the same decisions from the same
    seed produce the same game. That holds only while they *are* the same
    engine over the same cards — a build that differs at all can disagree about
    what a card does, and the disagreement surfaces several turns later as a
    desync rather than as the version gap it actually is.

    So any difference is refused, with both versions in the message: "we are on
    different builds" is something two people can act on, and "fingerprint
    mismatch at decision 41" is not. A peer that names no version at all is let
    through — that is a build old enough to predate the field, and its protocol
    number has already had its say.
    """
    theirs, ours = str(theirs or ""), str(ours or "")
    if theirs and ours and theirs != ours:
        raise Handshake(
            f"the other player is running {theirs} and you are running {ours} — "
            f"the same build on both sides is what keeps the two games "
            f"identical, so whoever is behind should update")


# ---------------------------------------------------------------------------
# the decision stream
# ---------------------------------------------------------------------------
@dataclass
class Session:
    """The shared decision counter, and the rules for what may cross the wire.

    Both engines ask the same questions in the same order, so both count them
    the same way; the counter is therefore not sequencing (the transport
    already guarantees order) but a *check* — the cheapest possible statement
    of "we are on the same decision", which together with the fingerprint is
    what turns a divergence into an error message instead of a mystery.
    """

    link: Link
    seat: int                       # which seat this machine is playing
    timeout: float = TURN_TIMEOUT
    count: int = 0
    lock: Any = field(default_factory=threading.Lock)

    # -- sending -----------------------------------------------------------
    def publish(self, kind: str, state, options: Sequence, answer: Any) -> Any:
        """Put this seat's answer on the wire and hand it straight back.

        Returns ``answer`` untouched so this can wrap a controller without
        changing what it decided — the same pass-through discipline
        ``replay.record`` keeps, and for the same reason.
        """
        with self.lock:
            index = _encode(kind, options, answer)
            self.link.send({"t": "d", "n": self.count, "s": self.seat,
                            "k": kind, "g": fingerprint(state, options),
                            "i": index})
            self.count += 1
        return answer

    # -- receiving ---------------------------------------------------------
    def consume(self, kind: str, state, options: Sequence) -> Any:
        """Block until the other seat answers *this* question, and decode it.

        Everything that can be checked is checked before the answer is let into
        the engine: that it is a decision at all, that it is the decision we are
        waiting for, that it came from the seat we expect, that it is the same
        kind of question, that the option list it was picked from was ours, and
        that the index lands inside it. A message that fails any of those is a
        desync, and stopping here is the whole point of the exercise.
        """
        with self.lock:
            expected, mine = self.count, fingerprint(state, options)
            message = self._await_decision()
            self.count += 1
        got = message.get("n")
        if got != expected:
            raise Desync(f"peer is on decision {got}, we are on {expected}")
        if message.get("s") == self.seat:
            raise Desync(f"decision {expected}: the peer answered our own seat")
        if message.get("k") != kind:
            raise Desync(f"decision {expected}: peer answered a "
                         f"{message.get('k')!r} question, we asked {kind!r}")
        if message.get("g") != mine:
            raise Desync(
                f"decision {expected}: the peer was offered a different set of "
                f"moves than we were — the two builds disagree about the rules")
        return _decode(kind, options, message.get("i"), expected)

    def _await_decision(self) -> dict:
        """The next ``d`` message, or an exception saying why there isn't one."""
        message = self.link.recv(timeout=self.timeout)
        if message is None:
            raise PeerGone(f"no answer from the other side in {self.timeout:.0f}s")
        if message.get("t") == "bye":
            raise PeerGone(str(message.get("why") or "the other side left"))
        if message.get("t") != "d":
            raise Desync(f"expected a decision, got {message.get('t')!r}")
        return message

    def goodbye(self, why: str = "left the match") -> None:
        """Tell the peer we are going, so it stops waiting on a dead seat."""
        try:
            self.link.send({"t": "bye", "why": why})
        except Exception:
            pass    # leaving is best-effort; the timeout covers a link already gone


def _encode(kind: str, options: Sequence, answer: Any) -> Any:
    """An answer as an index into ``options`` — the only part worth sending.

    Both sides hold the same option list, built by the same engine from the
    same state, so the index identifies the move completely.
    """
    if kind == MULLIGAN:
        return 0 if answer else 1
    if kind == MANY:
        picks = [_index_of(options, a) for a in (answer or [])]
        return [i for i in picks if i != DECLINED]
    return DECLINED if answer is None else _index_of(options, answer)


def _decode(kind: str, options: Sequence, index: Any, n: int) -> Any:
    if kind == MULLIGAN:
        return index == 0
    if kind == MANY:
        if not isinstance(index, list):
            raise Desync(f"decision {n}: expected a list of picks, got {index!r}")
        for i in index:
            if not isinstance(i, int) or not 0 <= i < len(options):
                raise Desync(f"decision {n}: pick {i!r} is not one of the "
                             f"{len(options)} options offered")
        return [options[i] for i in index]
    if index == DECLINED:
        return None
    if not isinstance(index, int) or not 0 <= index < len(options):
        raise Desync(f"decision {n}: pick {index!r} is not one of the "
                     f"{len(options)} options offered")
    return options[index]


# ---------------------------------------------------------------------------
# the two seats
# ---------------------------------------------------------------------------
class _Local:
    """This machine's own seat: it decides, and the decision goes on the wire.

    A pure pass-through around the real controller, exactly like
    ``replay._Recorder`` — the inner seat sees the same questions and its
    answers are returned untouched, so being in a network game cannot change
    what a player is allowed to do.
    """

    def __init__(self, inner, session: Session):
        self.inner = inner
        self.session = session
        self.name = getattr(inner, "name", "human")

    def choose_action(self, state, options):
        return self.session.publish(
            ACTION, state, options, self.inner.choose_action(state, options))

    def choose(self, state, prompt, options, *, optional: bool):
        return self.session.publish(
            CHOOSE, state, options,
            self.inner.choose(state, prompt, options, optional=optional))

    def order_effects(self, state, prompt, options):
        return self.session.publish(
            ORDER, state, options,
            self.inner.order_effects(state, prompt, options))

    def wants_mulligan(self, state, hand, *, free: bool = True):
        return self.session.publish(
            MULLIGAN, state, [True, False],
            self.inner.wants_mulligan(state, hand, free=free))

    def choose_many(self, state, prompt, options, *, count: int,
                    optional: bool, up_to: bool = False):
        return self.session.publish(
            MANY, state, options,
            self.inner.choose_many(state, prompt, options, count=count,
                                   optional=optional, up_to=up_to))


class _Remote:
    """The other machine's seat: every question is answered off the wire.

    It never consults an agent. Its whole job is to block this engine at
    precisely the points the other engine is also stopping, so the two walk
    forward in step.
    """

    name = "peer"

    def __init__(self, session: Session):
        self.session = session

    def choose_action(self, state, options):
        return self.session.consume(ACTION, state, options)

    def choose(self, state, prompt, options, *, optional: bool):
        return self.session.consume(CHOOSE, state, options)

    def order_effects(self, state, prompt, options):
        return self.session.consume(ORDER, state, options)

    def wants_mulligan(self, state, hand, *, free: bool = True):
        return self.session.consume(MULLIGAN, state, [True, False])

    def choose_many(self, state, prompt, options, *, count: int,
                    optional: bool, up_to: bool = False):
        return self.session.consume(MANY, state, options)


def seats(local, session: Session, table: Table) -> list:
    """The two controllers to hand ``Game``, in seat order.

    Both are mirrored down to the surface the *other* side said its controller
    has, for the reason ``replay`` mirrors: the engine asks
    ``getattr(controller, name, None)`` and takes a different path when a method
    is missing, so a seat that grew a ``wants_mulligan`` it does not really have
    would make this engine ask a question the other one never will — a desync
    manufactured by the wrapper itself.
    """
    out: list = []
    for seat in range(2):
        template = table.surface[seat] if seat < len(table.surface) else SURFACE
        if seat == session.seat:
            wrapper = _mirror(_Local, template, "LocalSeat")(local, session)
        else:
            wrapper = _mirror(_Remote, template, "RemoteSeat")(session)
        out.append(wrapper)
    return out
