#!/usr/bin/env python3
"""A browser front end for the engine: play against a bot, a person, or watch.

    python play_server.py                 # http://localhost:8080
    python play_server.py --lan           # also reachable from the network
    python play_server.py --online        # ...and from the internet, via a tunnel

Two people play through a *room*: one hosts, the other opens the link and joins.
The server owns the game either way — the browser is only ever shown a seat's
own view and offered a list of moves the server built, so it can neither see the
other hand nor name a card that was not on offer.

`--online` puts that room on a public address (see `tunnel.py`) and so serves it
to people who are not sitting at this machine. It therefore listens *twice*: the
private port the host's own browser uses, and a second loopback port — the only
one the tunnel can reach — which serves the game and nothing else. The split is
not decoration. A tunnel client connects to us from 127.0.0.1, so on a single
port every stranger would look like the person at the keyboard, and the routes
that read and write the host's own files are gated on exactly that. Two ports
means `_is_local` stays true, and the public one can refuse by construction
rather than by remembering to check.

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
import functools
import json
import mimetypes
import os
import queue
import random
import re
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

from braverse.console import utf8_output
from braverse.agentfile import AGENT_DIR, AGENT_GLOB
from braverse.deckfile import (DECK_DIR, META_DIR, parse_decklist,
                               read_decklist)
from braverse import (DEFAULT_RULES as RULES, STARTER_DECKS,
                      TRANSCRIBED_DECKS, CardDB, Game,
                      HeuristicAgent, RandomAgent, SeatedAgent, __version__,
                      default_db, implemented_pool, validate)
from braverse import actions as A
from braverse import profile as PR
from braverse import netplay as NP
from braverse import replay as RP
from braverse import tutorial as TUT
from braverse.engine import BankedUntap, OPENING_COOKIE_PROMPT
from braverse.enums import CardType, Marker
from braverse.rps import CHOICES, THROWS, decide_first_player
from braverse.state import CardInstance, Cookie, GameState

import desktop
import rendezvous as RZ
import tunnel as TUN

ROOT = Path(__file__).resolve().parent
VIEWER = ROOT / "viewer"
IMAGES = ROOT / "card_images"
CARD_DIR = "card_images"
ICON_NAME = "ginger_brave_icon.ico"
# The same face in the format each window backend can actually read: WinForms
# takes a .ico and nothing else, every other backend wants a bitmap and draws
# the .ico badly or not at all.
ICON_PNG = "ginger_brave_icon.png"

# Frozen by PyInstaller (see braverse.spec), ROOT is the throwaway directory the
# bundle unpacks into — fine for the assets baked in, useless for the decklists
# and trained pilots someone drops next to the binary. Look there too.
SIDE = (Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False) else ROOT)


def card_image(name: str) -> Path:
    """The art for one card: someone's own copy first, the bundled one after.

    Art dropped in `card_images/` beside the binary wins, the same way a
    decklist there does. That is the only way a card printed after the build
    gets a picture — the library is baked in, and `fetch_images.py` is not in
    the bundle — and it is how someone puts their own scans on the table.
    When running from a checkout the two directories are the same one, and this
    is a no-op.
    """
    mine = SIDE / CARD_DIR / name
    return mine if mine.is_file() else IMAGES / name

WINDOWS = os.name == "nt"


def window_icon() -> Optional[str]:
    """The icon file a native window should wear, or None if it is missing.

    A frozen build carries both next to the bundled assets (see
    `braverse.spec`); a checkout has them at the top of the tree. Either way
    this is the same picture the tab shows as its favicon.
    """
    found = ROOT / (ICON_NAME if WINDOWS else ICON_PNG)
    return str(found) if found.is_file() else None



def writable(directory: Path) -> bool:
    """Whether a file can actually be created in ``directory``.

    `os.access(..., W_OK)` is the obvious test and is wrong on Windows, where it
    reports every directory writable and the ACL only bites at open() time —
    which is exactly the case that matters, since a game installed under
    Program Files has a read-only directory beside the binary.
    """
    probe = directory / f".write-probe-{os.getpid()}"
    try:
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False

# How long the browser spends playing one action out, per event. A bot seat
# waits this out before deciding again, so an attack, the HP cards it turned
# face up, and the Cookie it broke all finish on screen before the next move
# starts. Kept in step with the timings in viewer/app.js.
EVENT_SECONDS = {"attack": 0.9, "reveal": 0.7, "faint": 0.3, "skill": 0.4,
                 "draw": 0.22, "damage": 0.25, "heal": 0.25, "discard": 0.26,
                 "trap": 1.0, "item": 1.0, "summon": 1.08}
# What is still on screen after an event of its kind *starts*.
# A revealed card is held face up to be read, and a broken Cookie falls apart.
TAIL_SECONDS = {"attack": 0.9, "reveal": 2.4, "faint": 1.5, "skill": 1.5,
                "draw": 0.7, "damage": 0.9, "heal": 0.9, "discard": 1.0,
                "trap": 2.2, "item": 2.2, "summon": 1.98}
MAX_REVEALS = 6          # the browser animates no more than this many
MAX_SCENE_PAUSE = 9.0

# How long a polling browser is held before being answered with "nothing new".
# Long enough that an idle match costs no traffic at all, short enough that a
# proxy or a sleeping laptop never sits on a dead connection.
POLL_HOLD = 25.0

# Filled in by `main` when the server is told to listen off this machine.
LAN_URLS: list[str] = []
# Filled in by `main --online`: the public address the tunnel handed back, and
# the hostname requests arriving through it will carry.
PUBLIC_URL = ""
PUBLIC_HOST = ""
# The listener the tunnel reaches and the tunnel itself, once either exists.
# Module-level because `--online` is no longer the only thing that opens them:
# hosting a peer game asks for a tunnel too (see `ensure_public`), and both
# routes have to end up with the same one rather than a second of each.
PUBLIC_SERVER = None
PUBLIC_LINK = None
# An ngrok authtoken given on the command line, for this run only. Empty means
# "look in the usual places" — `tunnel.resolve_token` handles the environment
# variable, the remembered file and ngrok's own store, in that order.
NGROK_TOKEN = ""
# A running install, started from the settings screen. One at a time: there are
# only two clients and either is enough.
INSTALL_JOB = None
PUBLIC_LOCK = threading.Lock()
# Offers waiting to be collected by whoever was sent the code (rendezvous.py).
SIGNAL_BOARD = RZ.Board()


def ensure_public() -> str:
    """The public address of this machine, opening a tunnel if there is none.

    `--online` opens one at startup because a room is meant to be joinable for
    the whole session. A peer game only needs one for the few seconds the two
    browsers take to find each other, and asking someone to have remembered a
    flag before they can play with a friend is a bad trade — so this exists,
    and either route is satisfied by whichever ran first.

    Raises `TUN.TunnelError` with something worth reading if the machine has no
    tunnel client, which is the one case the caller has to explain rather than
    retry.
    """
    global PUBLIC_URL, PUBLIC_HOST, PUBLIC_SERVER, PUBLIC_LINK
    with PUBLIC_LOCK:
        if PUBLIC_URL and PUBLIC_LINK is not None and PUBLIC_LINK.alive:
            return PUBLIC_URL
        if PUBLIC_URL and PUBLIC_LINK is None:
            return PUBLIC_URL          # opened by main; not ours to second-guess
        PublicHandler.app = Handler.app
        server = PUBLIC_SERVER
        if server is None:
            # cloudflared and ngrok are *told* which port to forward, so the OS
            # can pick one. playit is the other way round: the tunnel is
            # configured on your account against a fixed local port, so letting
            # the OS choose would leave it aimed at last night's number.
            backend = TUN.find_backend()
            want = (TUN.PLAYIT_LOCAL_PORT
                    if backend is not None and backend.name == "playit" else 0)
            # `open_tunnel` picks the client again below; it must land on the
            # same one this port was chosen for, or a playit tunnel would be
            # pointed at an ephemeral port.
            chosen = backend.name if backend is not None else ""
            try:
                server = Viewer(("127.0.0.1", want), PublicHandler)
            except OSError as exc:
                raise TUN.TunnelError(
                    f"could not listen on 127.0.0.1:{want} for playit "
                    f"({exc}) — something else is using it") from exc
            threading.Thread(target=server.serve_forever, daemon=True).start()
        link = TUN.open_tunnel(server.server_address[1], authtoken=NGROK_TOKEN,
                               prefer=chosen)
        PUBLIC_SERVER, PUBLIC_LINK = server, link
        PUBLIC_URL = link.url.rstrip("/") + "/"
        PUBLIC_HOST = link.host
        return PUBLIC_URL

# What a stranger who followed an invite link may ask for.
#
# Everything outside this set belongs to the machine running the server — the
# decklists on its disk, its replay folder, the pacing controls over its local
# bot game — and is served only to a browser on that machine. Playing a room
# needs the client itself, the board, the lobby, the moves, and enough of the
# card pool to draw them; that is all this is.
#
# Hosting is *not* in it. The invite always flows one way, from the machine
# running the game outwards, so nothing off it ever needs to open a room — and a
# route that mints server-side state on request is one a stranger should not
# have. Neither is anything that writes: a joiner saving a deck or a replay
# would be writing to someone else's disk.
PUBLIC_ROUTES = frozenset({
    "/api/config", "/api/state", "/api/room",
    "/api/decks", "/api/deck", "/api/deck/validate",
    "/api/cardnames", "/api/card", "/api/pool",
    "/api/room/join", "/api/room/leave", "/api/room/rematch",
    "/api/choose",
    # How a peer game is arranged: the joiner's *machine* collects the offer a
    # code points at and hands its answer back. Both are reachable by anyone
    # holding the code, which is the point of a code — and neither touches the
    # game, only the few kilobytes two browsers need to find each other. Once
    # they have, the tunnel is not in the picture at all.
    "/api/signal/offer", "/api/signal/answer",
})

# Hostnames a request may claim to have been sent to. A browser tricked into
# resolving some attacker's name to 127.0.0.1 arrives with that name in `Host`,
# and every same-origin check downstream would then believe it — so a name we
# have no reason to answer to is refused before any route runs. Bare IPs and
# `*.local` are how this machine is legitimately addressed on a network; the
# tunnel hostname is added when there is one.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", ""})


class Limiter:
    """A token bucket per client, for the port strangers can reach.

    Not a defence against anyone determined — it is one process serving a card
    game — but the public port is a thread per connection on someone's laptop,
    and "a stranger with the link can spend the host's machine" should not be
    the way an evening ends. Buckets are dropped once they refill, so idle
    clients cost nothing and the table cannot grow without bound.
    """

    def __init__(self, rate: float, burst: int):
        self.rate = rate            # tokens per second
        self.burst = burst
        self.lock = threading.Lock()
        self.buckets: dict[str, tuple[float, float]] = {}   # key -> (tokens, when)

    def allow(self, key: str) -> bool:
        now = time.time()
        with self.lock:
            tokens, when = self.buckets.get(key, (float(self.burst), now))
            tokens = min(self.burst, tokens + (now - when) * self.rate)
            if tokens < 1.0:
                self.buckets[key] = (tokens, now)
                return False
            tokens -= 1.0
            if tokens >= self.burst - 1e-9:
                self.buckets.pop(key, None)   # full again; forget it
            else:
                self.buckets[key] = (tokens, now)
            return True


# Answering a question is the one thing a seat does often, and a fast turn is
# several in a row — hence the loose burst. Joining is once per game, and is the
# route that costs a room, so it gets its own much tighter bucket.
MOVE_LIMIT = Limiter(rate=4.0, burst=40)
JOIN_LIMIT = Limiter(rate=0.2, burst=6)
# A ceiling on live matches, so an abandoned room cannot pile up thread by
# thread over a long session. Set loose rather than tight on purpose: hosting is
# not a route a stranger can reach (see `PUBLIC_ROUTES`), so the only thing this
# bounds is the host's own clicking, and refusing a rematch because eight games
# were opened this afternoon would be its own bug. Rooms nobody polls are reaped
# on their own clock — see `Room.IDLE_LIMIT`.
MAX_ROOMS = 64


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
    """Decklist files by name: loose beside the script, `decks/`, `decks/meta/`.

    `decks/` is scanned second so the curated folder wins a name clash with a
    loose file — co-evolution writes there, and those lists are the ones a run
    actually stands behind. `decks/meta/` is scanned last and is the one
    sub-folder that is read: the tournament lists are decks people want to
    play, not only a pool to train against, and a deck the game cannot offer
    is a deck nobody can try.
    """
    found = {p.stem: p for p in scan("*.txt")}
    found.update({p.stem: p for p in scan(f"{DECK_DIR}/*.txt")})
    found.update({p.stem: p for p in scan(f"{META_DIR}/*.txt")})
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
    if name in TRANSCRIBED_DECKS:
        return "starter"
    # The other eight starter products are built from their set rather than
    # transcribed from the box, and the menu says so rather than passing them
    # off as the printed list.
    if name in STARTER_DECKS:
        return "derived"
    path = deck_files().get(name)
    if path is not None and path.parent.name == Path(META_DIR).name:
        return "tournament"
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
# A decklist is a couple of kilobytes; this is the ceiling on a paste or a
# dropped file, well under `MAX_BODY`, so a mis-dropped image is refused as
# what it is rather than parsed line by line.
MAX_IMPORT = 256 << 10
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
        blob = json.loads(path.read_text(encoding="utf-8"))
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
    tmp.write_text(json.dumps(blob, indent=2, sort_keys=True),
               encoding="utf-8")
    tmp.replace(path)          # never leave a half-written store behind


def export_store() -> Path:
    """Where an exported decklist is written: `decks/` beside the game.

    The same directory a player drops decklists into, so a deck they exported
    and a deck they were sent live together, and falling back to the home
    directory exactly like the deck store when that one is read-only.
    """
    base = SIDE if writable(SIDE) else Path.home() / ".braverse"
    path = base / DECK_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_path(name: str) -> Path:
    """A free `.txt` path for `name` in `export_store()`.

    Never returns a path that already exists: `decks/` also holds lists the
    evolver wrote and lists somebody was sent, and an export is not worth
    overwriting one of those because the names happened to match.
    """
    stem = re.sub(r"[^\w.-]+", "_", name).strip("._") or "decklist"
    directory = export_store()
    candidate = directory / f"{stem}.txt"
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{n}.txt"
        n += 1
    return candidate


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


# ---------------------------------------------------------------------------
# saved replays
# ---------------------------------------------------------------------------
# A replay is the decisions both seats took, not a film of the board: the
# engine is deterministic, so re-running it over the same decks, seed and
# answers reproduces the game exactly (see `braverse/replay.py`). They live
# beside the script in `replays/`, one JSON file each, next to the deck store
# and falling back the same way when that directory is read-only.
REPLAY_DIR_NAME = "replays"
REPLAY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,118}\.json")
MAX_REPLAY_LIST = 400      # how many the browser is shown, newest first
MAX_BODY = 8 << 20         # an uploaded replay is a few tens of KB; cap the rest

# Every extension `viewer/` and `card_images/` actually contain.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
}


def replay_store() -> Path:
    base = SIDE if writable(SIDE) else Path.home() / ".braverse"
    path = base / REPLAY_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_replay_name(raw: Any) -> str:
    """A file name out of a request, or "" for anything that is not one.

    Refused rather than scrubbed: stripping the bad characters out of
    `sub/dir.json` leaves `subdir.json`, which is a *different, real* file, and
    a route that quietly reads or deletes some other replay than the one it was
    asked about is worse than one that says no. So the name has to already be
    the shape this server writes — plain characters, no path, no leading dot,
    ending `.json`.
    """
    name = str(raw or "")
    if not REPLAY_NAME.fullmatch(name) or ".." in name:
        return ""
    return name


def replay_files() -> list[Path]:
    """Every saved replay, newest first."""
    try:
        found = [p for p in replay_store().glob("*.json") if p.is_file()]
    except OSError:
        return []
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return found[:MAX_REPLAY_LIST]


def replay_summary(path: Path) -> Optional[dict]:
    """One row of the replay list, or None for a file that is not one of ours."""
    try:
        recording = RP.Recording.load(path)
    except RP.ReplayError:
        return None
    return {"name": path.name, "size": path.stat().st_size, **recording.summary()}


def replay_filename(decks: Sequence[str], when: float) -> str:
    """`20260823-140512-st9_sea_fairy-vs-st8_wind_archer.json`."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(when))
    tag = "-vs-".join("".join(ch for ch in str(name) if ch.isalnum() or ch in "-_")[:28]
                      or "deck" for name in list(decks)[:2])
    return f"{stamp}-{tag}.json"


# ---------------------------------------------------------------------------
# the player's profile
# ---------------------------------------------------------------------------
# One encrypted file per player, in `profiles/` beside the replays and falling
# back to ~/.braverse the same way. See `braverse/profile.py` for what is in
# one and what is sealed; the routes below are the only way in, and none of
# them is public — a profile belongs to the machine it was made on.
PROFILE_DIR_NAME = "profiles"
MAX_PASSPHRASE = 256


@lru_cache(maxsize=1)
def profile_store() -> PR.ProfileStore:
    base = SIDE if writable(SIDE) else Path.home() / ".braverse"
    return PR.ProfileStore(base / PROFILE_DIR_NAME)


class Profiles:
    """Who is signed in, and what to do with a game they just finished.

    Exactly one profile is open at a time, on the machine running the server —
    this is a card game on someone's laptop, not a service with sessions. The
    key that reseals the file lives in here for as long as it is open and is
    never sent anywhere; closing the profile drops it.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.session: Optional[PR.Session] = None

    # -- signing in ------------------------------------------------------
    def open(self, slug: str, passphrase: str = "") -> PR.Session:
        session = profile_store().open(slug, passphrase)
        with self.lock:
            self.session = session
        return session

    def create(self, name: str, passphrase: str, avatar: str) -> PR.Session:
        session = profile_store().create(name, passphrase=passphrase,
                                         avatar=avatar)
        with self.lock:
            self.session = session
        return session

    def close(self) -> None:
        with self.lock:
            self.session = None

    def active(self) -> Optional[PR.Session]:
        with self.lock:
            return self.session

    def view(self) -> Optional[dict]:
        """The open profile as the browser wants it, or None."""
        with self.lock:
            if self.session is None:
                return None
            return {"slug": self.session.slug, "locked": self.session.locked,
                    **self.session.profile.summary()}

    # -- finished games --------------------------------------------------
    def bank(self, match: "Match") -> None:
        """Add a finished game to the open profile, if it counts.

        Called on the match thread as the game ends, and deliberately silent:
        a profile that cannot be written must not take the game down with it,
        and there is nobody to tell at that point anyway.
        """
        try:
            self._bank(match)
        except (PR.ProfileError, OSError):
            pass

    def _bank(self, match: "Match") -> None:
        # A replay is a game that already counted, and the guided first game is
        # a lesson rather than a game — neither is a result.
        if match.replay is not None or match.config.tutorial:
            return
        seat = match.profile_seat
        state = match.game.state
        if seat is None or not state.over:
            return
        # Two people is the only thing that pays XP. A bot on the other side
        # still goes in the record — it is a game you played — worth nothing.
        versus_person = all(p == "human" for p in match.config.pilots)
        names = list(match.config.names or ["", ""])
        result = ("draw" if state.winner is None or state.winner < 0
                  else "win" if state.winner == seat else "loss")
        with self.lock:
            session = self.session
            if session is None:
                return
            session.profile.record(
                deck=str(match.config.decks[seat]),
                opponent_deck=str(match.config.decks[1 - seat]),
                opponent=str(match.config.pilots[1 - seat]),
                opponent_name=names[1 - seat] if len(names) > 1 else "",
                result=result,
                turns=int(state.turn_number),
                replay=match.saved_as,
                versus_person=versus_person,
            )
            dropped = session.profile.prune()
            session.save()
        # The entry and its log go together: a game that has fallen out of the
        # last thirty is not one there is any way left to watch.
        for name in PR.replays_of(dropped):
            drop_replay(name)


def drop_replay(name: str) -> bool:
    """Delete one replay file by name. False if it was not there to delete."""
    safe = safe_replay_name(name)
    if not safe:
        return False
    try:
        (replay_store() / safe).unlink()
        return True
    except OSError:
        return False


PROFILES = Profiles()


def agent_files() -> dict[str, Path]:
    """Trained agents by file name: loose beside the script, then in `agents/`.

    Scanned in that order for the same reason `deck_files` is — the curated
    folder wins a name clash — and both are scanned because a `.pt` dropped
    beside the executable has always been offered as an opponent, and moving
    the shipped ones into a folder must not withdraw that.
    """
    found = {p.name: p for p in scan(AGENT_GLOB)}
    found.update({p.name: p for p in scan(f"{AGENT_DIR}/{AGENT_GLOB}")})
    return found


def available_pilots() -> list[str]:
    # "tutorial" is a real pilot rather than a hidden mode: it is the gentlest
    # opponent in the box and worth being able to pick again afterwards.
    pilots = ["human", "heuristic", "random", "tutorial"]
    pilots += [f"rl:{name}" for name in sorted(agent_files())]
    return pilots


def make_pilot(kind: str, seat: int, db: CardDB, seed: int, runner: "Match"):
    if kind == "human":
        return HumanController(runner, seat)
    if kind == "random":
        return Paced(SeatedAgent(RandomAgent(seed=seed), seat), runner)
    if kind == "tutorial":
        # Already seat-aware and deliberately RNG-free; wrapping it in
        # SeatedAgent would only hand it a seat hint it does not read.
        return Paced(TUT.make_opponent(db=db, seat=seat), runner)
    if kind.startswith("rl:"):
        from braverse.rl import RLAgent, Trainer
        net = Trainer.load_net(agent_files()[kind[3:]])
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


@functools.lru_cache(maxsize=1)
def card_name_index(db_id: int) -> list:
    """Every distinct card *name*, with the card behind it.

    The log is prose — "Wind Archer Cookie takes 3 attack damage from Gold
    Citrine Cookie's Shining Sea Jewel" — and the only handle it gives on a
    card is its name. This is what lets the viewer turn those names back into
    something you can hover: one entry per name, longest first so a name that
    contains a shorter one still wins the match.

    Sent whole rather than looked up per hover. It is 813 names and about
    350 KB, it never changes while the server is up, and a card the log names
    is very often one that is not on the board — in a trash, in a deck, or
    already gone — so there is no smaller set that would be correct. `db_id` is
    only there to key the cache on the database identity.
    """
    db = _DB_BY_ID[db_id]
    first: dict[str, str] = {}
    for card in db.cards.values():
        first.setdefault(card.name, card.id)
    names = sorted(first, key=len, reverse=True)
    return [card_json(db, first[name]) for name in names]


# `functools.lru_cache` cannot hold the CardDB itself (it is unhashable), so the
# cache is keyed on `id()` and the object is pinned here to keep that id valid.
_DB_BY_ID: dict[int, CardDB] = {}


def card_names(db: CardDB) -> list:
    _DB_BY_ID[id(db)] = db
    return card_name_index(id(db))


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
        # 【Equip】: a Soul Jam riding this Cookie changes what it does and is
        # public information, so it is projected like any other card on the
        # table rather than being an invisible modifier.
        "equipment": [instance_json(db, c) for c in cookie.equipment],
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
        # The Support Phase runs before the Main Phase and closes for good on
        # the first Main Phase action (Comprehensive Rules 6-1-1). The phase
        # strip needs to say "passed" from that moment rather than "ready",
        # and only the engine knows when it happened.
        "supportOpen": not player.left_support_phase,
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
    if isinstance(option, BankedUntap):
        # A banked "when your turn ends, ..." rider. It has no card on the
        # table to point at, so it is offered as the card that banked it.
        out = {"index": index, "kind": "other", "label": str(option)}
        if option.card_id:
            out["img"] = f"/card_images/{option.card_id}.webp"
        return out
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
                   "draw 6 new cards. This one is free.")
# The repeat offer, open only to a hand with no Cookie in it. It is a different
# question — it has a price — so it says so and gets its own labels.
REDRAW_CHOICES = ("Redraw", "Keep this hand")
REDRAW_PROMPT = ("No Cookie in hand. Redraw all 6? You can keep going until "
                 "you find one, but your opponent draws 1 card each time.")


def centre_style(options: Sequence) -> Optional[str]:
    """Questions that belong in the middle of the table, not off to one side.

    The opening toss is the whole screen's business for those few seconds, and
    making someone track to the far right to throw rock is a silly way to start
    a game.
    """
    # A yes/no — `Ctx.confirm`, and every optional `<...>` cost — is one button
    # and a decline. It used to render as a one-item list in the far corner
    # while the thing it was asking about was in the middle of the board.
    if options and all(isinstance(o, bool) for o in options):
        return "yesno"
    labels = [o for o in options if isinstance(o, str)]
    if len(labels) != len(options):
        return None
    if set(labels) == set(THROWS):
        return "throw"
    if set(labels) == set(CHOICES):
        return "choice"
    if set(labels) == set(MULLIGAN_CHOICES) or set(labels) == set(REDRAW_CHOICES):
        return "choice"
    return None


def shown_cards(db: CardDB, state: GameState, options: Sequence) -> list:
    """Cards to lay out alongside the answers, which are not answers themselves.

    "View the top 3 cards of your deck, add 1 {P} card to your hand" is two
    instructions, and only the second one is the question. Showing three cards
    and letting you click one of them is the effect; showing you only the
    purple one would be a different, much worse card. So the engine parks the
    whole viewed set on `state.viewing` for the duration of the question and
    this turns the part that is *not* selectable into greyed-out context.

    Safe to send: a question the other seat is not being asked is stripped
    wholesale by `Match._hide_pending`, so what you looked at and did not take
    never leaves your own browser.
    """
    if not state.viewing:
        return []
    answers = {getattr(o, "uid", None) for o in options}
    return [{**option_json(db, -1, card), "index": -1}
            for card in state.viewing if card.uid not in answers]


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
    # The Cookie you open with is answered on the table, not on a strip: the
    # viewer stands the eligible Cookies up out of your hand the way it does an
    # armed trap, and a click on one is the answer. Listing them twice — raised
    # in the hand *and* laid out in a picker — is the duplication this avoids.
    if prompt == OPENING_COOKIE_PROMPT:
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
    elif "to your hand" in lowered:
        # "View 3 cards from the top of your deck, add 1 {P} card to your hand"
        # and its relatives. "Choose" undersold what the button does.
        verb = "Add to Hand"
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
        # A question inside the attack response window is not a turn. Both
        # seats are told which it is: the defender because "Your move" during
        # someone else's attack says nothing about what is being asked, and the
        # attacker because their browser is otherwise sitting on a bare wait
        # with no hint that the swing they declared is being answered.
        answering = self.match.attack_response()
        prompt = ("Attacked — trap, block, or take it" if answering
                  else "Your move")
        index = self.match.ask(self.seat, prompt, payload, optional=False,
                               turn_action=True, responding=answering)
        return options[index] if index is not None else None

    def choose(self, state: GameState, prompt: str, options: Sequence, *, optional: bool):
        if not options:
            return None
        db = self.match.db
        payload = [option_json(db, i, o) for i, o in enumerate(options)]
        pick = hand_pick(prompt, options, state.players[self.seat])
        index = self.match.ask(self.seat, prompt, payload, optional=optional,
                               pick=pick, centre=centre_style(options),
                               shown=shown_cards(db, state, options))
        return options[index] if index is not None else None

    def order_effects(self, state: GameState, prompt: str, options: Sequence):
        """Which of several simultaneous effects resolves first.

        Not optional: one of them is going next either way, so there is nothing
        to decline. Asked once per effect until only one is left.
        """
        return self.choose(state, prompt, options, optional=False)

    def wants_mulligan(self, state: GameState, hand: Sequence, *,
                       free: bool = True) -> bool:
        """The opening redraw, free the first time and priced after that.

        Asked with the hand already on screen — the browser polls the same
        state — so the question is just the two buttons. ``free`` is the engine
        telling us which of the two questions this is: the one-off shop-around,
        or the repeat offer a Cookie-less hand gets at the cost of a card to
        the opponent.
        """
        choices = MULLIGAN_CHOICES if free else REDRAW_CHOICES
        prompt = MULLIGAN_PROMPT if free else REDRAW_PROMPT
        payload = [option_json(self.match.db, i, o) for i, o in enumerate(choices)]
        index = self.match.ask(self.seat, prompt, payload,
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
    record: bool = True    # keep the finished game in `replays/`
    # A game every seat's answers were already written down for. Set means this
    # match is watching one back rather than playing a new one; the decks, the
    # seed and every decision come out of the recording instead of the menu.
    replay: Optional[RP.Recording] = None
    # The guided first game: a stacked deal and a scripted opponent, so every
    # step of the browser course has the board it is about. See
    # `braverse/tutorial.py`.
    tutorial: bool = False
    # Which seat belongs to the person whose profile is open here, for the
    # result to be banked against. Left unset it is the first human seat, which
    # is right for every local game; a room says so explicitly, because the
    # host's own chair is not always seat 0 (see `Room.join`).
    profile_seat: Optional[int] = None
    # A peer-to-peer game: this machine plays one seat and every answer from
    # the other arrives over a data channel. Carries the seat we sit in, the
    # agreed `netplay.Table` and the live `netplay.Session`. The decks and the
    # seed come from that table rather than from the menu — both machines have
    # to deal the same game, which is what lets there be no server holding it.
    peer: Optional[dict] = None
    # What the two seats are called, when they are people.
    names: Optional[list] = None


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

        # Watching one back and playing a new one are the same run: the same
        # engine over the same decks and seed, with seats that answer from the
        # recording instead of thinking. Everything downstream of here — the
        # snapshots, the events, pause, step and speed — cannot tell them apart,
        # which is the point.
        self.replay = config.replay
        self.cursor: Optional[RP.Cursor] = None
        self.recorder: Optional[RP.DecisionLog] = None
        self.replay_note = ""        # why a replay stopped, once it has
        self.replay_desync = False
        self.saved_as = ""           # the file this match was last written to
        if self.replay is not None:
            self.deck_lists = self.replay.deck_lists
            self.extra_lists = self.replay.extra_lists
            seed = self.replay.seed
            self.seed = seed
            # Paced through the same gate a bot goes through, so a replay can be
            # paused, stepped and slowed down with the controls already there.
            self.controllers, self.cursor = RP.scripted(self.replay, pace=self.gate)
        elif config.peer is not None:
            # Nothing here is chosen locally. Both machines were handed the same
            # `Table` by the handshake, and deal from it — that identity is the
            # whole reason two engines with no server between them stay in step.
            table = config.peer["table"]
            session = config.peer["session"]
            self.deck_lists = [list(d) for d in table.decks]
            self.extra_lists = [list(e) for e in table.extra]
            seed = table.seed
            self.seed = seed
            self.peer_session = session
            # Our own seat is the browser, exactly as in any other human game;
            # the other is a seat that only ever reads the wire.
            self.controllers = NP.seats(HumanController(self, session.seat),
                                        session, table)
            self.recorder = None
        elif config.tutorial:
            # Stacked, in order, and not from a file: the tutorial's decks are
            # a fixed permutation of the two starter lists, computed rather
            # than stored so they cannot drift out of legality.
            self.deck_lists = [TUT.player_deck(db), TUT.opponent_deck(db)]
            self.extra_lists = [[], []]
            seed = config.seed if config.seed is not None else 0
            self.seed = seed
            pilots = [make_pilot(config.pilots[i], i, db, seed + 100 * i, self)
                      for i in range(2)]
            self.recorder = RP.DecisionLog(db=db)
            self.controllers = [RP.record(c, i, self.recorder)
                                for i, c in enumerate(pilots)]
        else:
            decks = available_decklists()
            self.deck_lists = [list(decks[name][0]) for name in config.decks]
            self.extra_lists = [list(decks[name][1]) for name in config.decks]
            seed = config.seed if config.seed is not None else random.randrange(1 << 30)
            self.seed = seed
            pilots = [make_pilot(config.pilots[i], i, db, seed + 100 * i, self)
                      for i in range(2)]
            # Recording wraps the seats rather than the engine, and passes every
            # question and answer straight through: a recorded game is the same
            # game. Costs a few hundred small integers over a whole match.
            self.recorder = RP.DecisionLog(db=db)
            self.controllers = [RP.record(c, i, self.recorder)
                                for i, c in enumerate(pilots)]
        self.game = Game(self.deck_lists, self.controllers,
                         extra_decks=self.extra_lists, db=db, seed=seed,
                         shuffle=not config.tutorial)
        self.human_seats = [i for i, p in enumerate(config.pilots) if p == "human"]
        self.profile_seat = (config.profile_seat if config.profile_seat is not None
                             else (self.human_seats[0] if self.human_seats else None))
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
            if self.config.peer is not None:
                # A peer seat has a board to look at before it has anything to
                # do. Both engines open on the toss, and whichever seat is not
                # throwing first blocks on the wire inside `decide_first_player`
                # — which is correct, but without a snapshot already published
                # it means staring at an empty page with the title screen gone
                # and no indication that anything is happening. Publishing here
                # costs one version and gives that seat its table.
                self.publish()
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
            self.autosave()
            # After the autosave, so the entry can name the file it was kept in.
            PROFILES.bank(self)
        except MatchAborted:
            return
        except RP.ReplayFinished as exc:
            # A game saved while it was still being played: it runs to the point
            # it was saved at and stops there, which is not a fault.
            self._replay_stopped(str(exc))
        except RP.ReplayDesync as exc:
            # This build no longer plays the game that was recorded. Say which
            # decision diverged rather than showing a game that never happened.
            self._replay_stopped(str(exc), bad=True)
        except Exception as exc:  # surface engine errors in the UI, not the console
            import traceback
            traceback.print_exc()
            with self.cond:
                self.error = f"{type(exc).__name__}: {exc}"
                self.version += 1
                self.cond.notify_all()

    # -- replays -----------------------------------------------------------
    def _replay_stopped(self, note: str, *, bad: bool = False) -> None:
        self.replay_note = note
        self.replay_desync = bad
        self.publish()

    def recording(self) -> Optional[RP.Recording]:
        """This match so far, as a replay. None while watching one back.

        Answering mid-game is deliberate: a game saved at turn 4 replays the
        first four turns and stops, which is what you want from a "save this,
        something odd just happened" button.
        """
        if self.recorder is None:
            return None
        return self.recorder.finish(
            self.game,
            decks=[{"name": name, "cards": deck, "extra": extra}
                   for name, deck, extra in zip(self.config.decks,
                                                self.deck_lists,
                                                self.extra_lists)],
            pilots=self.config.pilots,
            seed=self.seed,
            app_version=__version__,
        )

    def save_replay(self) -> Path:
        """Write this match to `replays/`. Raises OSError if it cannot."""
        recording = self.recording()
        if recording is None:
            raise RP.ReplayError("a replay is not itself recorded")
        path = replay_store() / replay_filename(self.config.decks, self.started)
        recording.save(path)
        self.saved_as = path.name
        return path

    def autosave(self) -> None:
        """Keep every finished game, quietly.

        A replay is worth having precisely for the game nobody thought to press
        save on, and one game is a few tens of kilobytes. A game abandoned
        half-way is not kept — `stop` never reaches here.
        """
        if self.recorder is None or not self.config.record:
            return
        try:
            self.save_replay()
        except (OSError, RP.ReplayError):
            pass        # a read-only disk must not take the match down with it
        self.publish()

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

        An ITEM gets the same spotlight a trap gets: it is played from hand,
        does its thing and goes straight to the trash, so a small pop over an
        empty patch of the board is the whole of it on screen. A STAGE stays
        small — it is still sitting there afterwards — and so does a Cookie's
        own 【Activate】, which pops over the Cookie that used it.
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
        if card.get("type") == CardType.ITEM.value:
            self._queued.append({
                "type": "item",
                "owner": owner,
                "card": card,
                "name": card.get("name", ""),
            })
            return
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
    def _summon_events(prev: Optional[dict], snap: dict) -> list:
        """Cookies that arrived in a battle area — the mirror of a faint.

        Read off a diff for the same reason: a Cookie can arrive from hand, the
        trash, the break area, the support area or the EXTRA deck, and every
        one of those is worth the same beat on the board. An 【Awaken】 keeps
        the host Cookie's uid, so restacking one is correctly not an arrival.
        """
        if not prev:
            return []
        events = []
        for index, (was, now) in enumerate(zip(prev["players"], snap["players"])):
            before = {c["uid"] for c in was["battle"]}
            for cookie in now["battle"]:
                if cookie["uid"] in before:
                    continue
                events.append({
                    "type": "summon",
                    "owner": index,
                    "cookie": cookie["uid"],
                    "card": cookie["card"],
                    # The dust takes the Cookie's own colour.
                    "color": cookie["card"].get("color", ""),
                })
        return events

    @staticmethod
    def _discard_events(prev: Optional[dict], snap: dict, shown: Sequence = ()) -> list:
        """Cards that went from a hand straight to the trash.

        Paying a `<Discard a card>` cost is otherwise completely silent: the
        hand quietly gets shorter and the trash quietly gets taller, and the
        player who was *charged* is the only one who ever knew a price was
        paid. So it is animated for both seats — and identity is safe to send,
        because the trash it lands in is a public zone either player may read.

        Read off a diff rather than recorded in the engine because a hand
        empties into the trash from two dozen places — a cost, an opponent's
        "discard 1", a compiled program — and every one of them is the same
        beat on screen.

        `shown` is the events already built for this scene. An Item or a Trap
        also travels hand-to-trash, and both already own the middle of the
        board for two seconds; sending them again as a discard would play the
        cost of a card that *was* the play. They are matched off by uid where
        one is known and by card id otherwise, since a trap is recognised from
        the log and carries no uid.
        """
        if not prev:
            return []
        seen_uids = set()
        seen_ids = []
        for event in shown:
            card = event.get("card")
            if not card or event.get("type") not in ("item", "trap", "skill"):
                continue
            if card.get("uid") is not None:
                seen_uids.add(card["uid"])
            else:
                seen_ids.append(card.get("id"))
        events = []
        for index, (was, now) in enumerate(zip(prev["players"], snap["players"])):
            held = {c["uid"]: c for c in was["hand"]}
            before = {c["uid"] for c in was["trash"]}
            for card in now["trash"]:
                uid = card["uid"]
                if uid in before or uid not in held or uid in seen_uids:
                    continue
                if card.get("id") in seen_ids:
                    seen_ids.remove(card["id"])
                    continue
                events.append({
                    "type": "discard",
                    "owner": index,
                    "card": held[uid],
                })
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
            # The card that was played comes first, then the price it charged
            # — a discard cost is paid on the way in, so it plays before the
            # damage or the draw it bought.
            played = self._queued + self._trap_events(snap)
            snap["events"] = (played
                              + self._discard_events(self._prev, snap, played)
                              + self._engine_events()
                              + self._draw_events(self._prev, snap)
                              + self._summon_events(self._prev, snap)
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
        snap["savedAs"] = self.saved_as
        if self.replay is not None and self.cursor is not None:
            snap["replay"] = {
                "at": self.cursor.at,
                "total": len(self.cursor.decisions),
                "note": self.replay_note,
                "desync": self.replay_desync,
                "recorded": self.replay.recorded,
                "pilots": list(self.replay.pilots),
                "appVersion": self.replay.app_version,
            }
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
    def attack_response(self) -> Optional[dict]:
        """The attack a defender is being asked about, for the browser, or None.

        Named by uid on both ends so either seat can point at the Cookies on
        its own board — the same identity the option list uses.
        """
        game = getattr(self, "game", None)
        window = game.response_window() if game is not None else None
        if window is None:
            return None
        attacker, target = window
        return {"attacker": attacker.uid, "target": target.uid,
                "attackerName": attacker.name(self.db),
                "targetName": target.name(self.db)}

    def ask(self, seat: int, prompt: str, options: list, *, optional: bool,
            count: int = 1, pick: Optional[dict] = None,
            centre: Optional[str] = None, up_to: bool = False,
            shown: Optional[list] = None, turn_action: bool = False,
            responding: Optional[dict] = None):
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
            # The turn's own action list, as opposed to a mid-effect question.
            # The viewer needs to tell them apart to run its support step.
            "turnAction": turn_action,
            # The attack this question is a response to, or None. Public to
            # both seats: an attack is declared out loud.
            "responding": responding,
            # Drawn next to the answers but not answerable. See `shown_cards`.
            "shown": shown or [],
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
            "shown": [],
            # Kept, unlike the options: which attack is being answered is
            # something the attacker declared, and it is the one thing that
            # makes their wait legible.
            "responding": pending.get("responding"),
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
# ---------------------------------------------------------------------------
# peer-to-peer: one match, two machines, no server holding the board
# ---------------------------------------------------------------------------
# How long a browser's poll for outbound messages is held before answering
# empty. Long enough that an idle match costs almost no requests, short enough
# that a closed tab is noticed while someone is still looking at the screen.
PEER_HOLD = 20.0
# A ceiling on messages queued for a browser that has stopped collecting them.
# The engine only speaks when its seat answers, so anything approaching this is
# a browser that is gone rather than a busy game.
PEER_BACKLOG = 512


class PeerBridge(NP.Link):
    """A `netplay.Link` whose far end is this machine's own browser.

    WebRTC lives in the browser and the engine lives in Python, so the data
    channel cannot be handed to `netplay` directly. This is the seam: the
    engine sends and receives ordinary `Link` messages, and the browser drains
    one queue over HTTP and fills the other with what arrived on the channel.
    The browser is a wire here, not a participant — it never reads a decision,
    and forging one would only desync the machine doing the forging.

    Ordering is preserved end to end, which is what `netplay` requires: queues
    are FIFO, the channel is ordered, and the browser posts batches in the
    order it received them.
    """

    def __init__(self):
        self.to_peer: "queue.Queue[dict]" = queue.Queue()
        self.from_peer: "queue.Queue[dict]" = queue.Queue()
        self.cond = threading.Condition()
        self.closed = False

    # -- the engine's side -------------------------------------------------
    def send(self, message: dict) -> None:
        if self.closed:
            raise NP.PeerGone("the connection is closed")
        if self.to_peer.qsize() >= PEER_BACKLOG:
            raise NP.PeerGone("the browser stopped relaying messages")
        self.to_peer.put(dict(message))
        with self.cond:
            self.cond.notify_all()

    def recv(self, timeout: Optional[float] = None):
        try:
            return self.from_peer.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self.closed = True
        with self.cond:
            self.cond.notify_all()

    # -- the browser's side ------------------------------------------------
    def deliver(self, messages: Sequence[dict]) -> None:
        """Messages that arrived on the data channel, in the order they came."""
        for message in messages:
            if isinstance(message, dict):
                self.from_peer.put(message)

    def drain(self, hold: float = PEER_HOLD) -> list:
        """Everything waiting to go out, holding briefly rather than spinning.

        Returns as soon as there is anything at all — a decision the other
        player is waiting on must not sit here for the length of a poll.
        """
        deadline = time.time() + hold
        with self.cond:
            while self.to_peer.empty() and not self.closed:
                left = deadline - time.time()
                if left <= 0:
                    break
                self.cond.wait(min(0.5, left))
        out = []
        while True:
            try:
                out.append(self.to_peer.get_nowait())
            except queue.Empty:
                break
        return out


class PeerLobby:
    """One peer-to-peer game in the making, and then in progress.

    The handshake has to happen before there is a `Match` — it is what decides
    the decks and the seed the match will be built from — but it also has to
    happen without blocking the browser that is driving the data channel. So it
    runs on its own thread, and the browser polls `status` to find out whether
    it is still waiting, playing, or has failed and why.
    """

    IDLE_LIMIT = 30 * 60

    def __init__(self, app: "App", seat: int, deck: str, name: str):
        self.app = app
        self.seat = seat            # 0 hosts, 1 joins
        self.deck = deck
        self.name = name
        self.bridge = PeerBridge()
        self.session: Optional[NP.Session] = None
        self.match: Optional[Match] = None
        self.error = ""
        self.touched = time.time()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def idle(self) -> bool:
        return time.time() - self.touched > self.IDLE_LIMIT

    def touch(self) -> None:
        self.touched = time.time()

    def _run(self) -> None:
        try:
            decks = available_decklists()
            cards, extra = [list(x) for x in decks[self.deck]]
            # Both seats are people at browsers, so both answer the whole
            # controller protocol; it is still sent rather than assumed,
            # because the engine branches on it and a wrong claim here is a
            # desync that would not surface until the opening mulligan.
            surface = NP.surface_of(HumanController(None, self.seat))
            if self.seat == 0:
                table = NP.host_handshake(
                    self.bridge, deck=cards, extra=extra,
                    seed=random.randrange(1 << 30), name=self.name,
                    app_version=__version__, surface=surface)
            else:
                table = NP.join_handshake(
                    self.bridge, deck=cards, extra=extra, name=self.name,
                    app_version=__version__, surface=surface)
            self.session = NP.Session(link=self.bridge, seat=self.seat)
            config = MatchConfig(
                decks=[f"peer:{n or '?'}" for n in table.names],
                pilots=["human", "human"],
                online=True, record=False, profile_seat=self.seat,
                peer={"table": table, "session": self.session})
            self.match = Match(config, self.app.db)
            self.match.start()
        except NP.NetplayError as exc:
            self.error = str(exc)
            # Say why before the link goes quiet. A handshake we refused looks,
            # from the other machine, exactly like a peer that never answered —
            # and "they are on 0.2.31, you are on 0.2.37" is only useful if it
            # reaches the person who has to update.
            self._tell_peer(str(exc))
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self._tell_peer(self.error)

    def _tell_peer(self, why: str) -> None:
        """Send the reason we are stopping, best effort.

        There is no `Session` yet when a handshake fails — that is the object
        that owns `goodbye` — so this writes the same message straight onto the
        bridge. Failing to send it is not worth reporting: the other side times
        out either way, it just learns less.
        """
        try:
            self.bridge.send({"t": "bye", "why": why})
        except Exception:
            pass

    def status(self) -> dict:
        return {"seat": self.seat,
                "state": ("failed" if self.error else
                          "playing" if self.match is not None else "waiting"),
                "error": self.error}

    def stop(self, why: str = "left the match") -> None:
        if self.session is not None:
            self.session.goodbye(why)
        self.bridge.close()
        if self.match is not None:
            self.match.stop()


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

    Over the internet the code is not enough to *find* the room either. Four
    characters out of an alphabet of 32 is a million rooms, which is a wall on a
    network you can see the other end of and no wall at all against a script; so
    a room also has a `secret`, minted with it, which every request that did not
    come from the host's own machine must present. It rides in the invite link
    rather than being typed, and it is the code that stays four characters,
    because the code is the thing a person reads down a phone.
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
        # Whoever opened the room is the person whose machine this is, and so
        # the seat whose results belong in the profile on it. Held as the token
        # rather than the number: a host who leaves and sits back down takes
        # the first free chair, which is not always the one they started in.
        self.host_token = self.tokens[0]
        self.secret = secrets.token_urlsafe(16)
        self.seen = [time.time(), 0.0]
        self.holding = [0, 0]   # polls this seat has in flight right now
        self.created = time.time()
        self.match: Optional[Match] = None
        self.version = 0        # lobby version, so a waiting host is woken

    # -- seats -----------------------------------------------------------
    def admits(self, secret: Optional[str]) -> bool:
        """Whether a request from off this machine may see this room at all.

        Checked before the seat token, and separately from it: this is the
        difference between a room you were sent and a room you found.
        """
        return bool(secret) and secrets.compare_digest(self.secret, str(secret))

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
        host = next((i for i, t in enumerate(self.tokens)
                     if t is not None and t == self.host_token), None)
        config = MatchConfig(
            decks=[self.decks[0], self.decks[1]],
            pilots=["human", "human"],
            profile_seat=host,
            names=list(self.names),
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
        # At most one peer-to-peer game per server. Two would need two data
        # channels in one browser and there is no reason to want that: this is
        # the machine of one of the two people playing.
        self.peer: Optional[PeerLobby] = None
        self.lock = threading.Lock()

    def new_match(self, config: MatchConfig) -> Match:
        with self.lock:
            if self.match is not None:
                self.match.stop()
            match = Match(config, self.db)
            self.match = match
        match.start()
        return match

    def end_match(self) -> bool:
        """Abandon the machine's own match, if it has one.

        The way back to the title screen from a game in progress. Only the
        solo table: a room and a peer game each have another person sitting at
        them and their own Leave, which says so to the other seat before it
        closes. Replacing a match already went through `Match.stop`, so this is
        that half of `new_match` with no new game after it.
        """
        with self.lock:
            match, self.match = self.match, None
        if match is None:
            return False
        match.stop()
        return True

    # -- rooms -----------------------------------------------------------
    # -- peer-to-peer ------------------------------------------------------
    def new_peer(self, seat: int, deck: str, name: str) -> PeerLobby:
        """Start (or restart) the machine's peer game. Replaces any previous."""
        with self.lock:
            old, self.peer = self.peer, None
        if old is not None:
            old.stop("started a new game")
        lobby = PeerLobby(self, seat, deck, name)
        with self.lock:
            self.peer = lobby
        lobby.start()
        return lobby

    def peer_lobby(self) -> Optional[PeerLobby]:
        with self.lock:
            lobby = self.peer
        if lobby is not None and lobby.idle():
            self.close_peer("idle too long")
            return None
        return lobby

    def close_peer(self, why: str = "left the match") -> None:
        with self.lock:
            lobby, self.peer = self.peer, None
        if lobby is not None:
            lobby.stop(why)

    def new_room(self, deck: str, name: str) -> Room:
        with self.lock:
            self._reap()
            # Each room is a live match on its own thread, holding two decks and
            # a game state. The code space would allow thousands; the machine
            # would not enjoy it.
            if len(self.rooms) >= MAX_ROOMS:
                raise ValueError("too many rooms open")
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
    # Overridden by `PublicHandler`, which serves the port the tunnel reaches.
    public = False

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
        # Every JSON answer names the build that sent it. One line here rather
        # than a field added to a dozen payloads, and it means any page can ask
        # "is the server still the build I loaded from?" on whatever call it
        # was already making — which is the only version gap a room can have.
        #
        # A room is two browsers on *one* engine: the person joining plays on
        # the host's server, so the rules cannot differ between the seats the
        # way they can in a peer game, where there are two engines. What can
        # differ is a page left open across a restart onto a newer build.
        if isinstance(payload, dict):
            payload.setdefault("build", __version__)
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _file(self, path: Path, cache: bool = False):
        if not path.is_file():
            self._send(404, b"not found", "text/plain")
            return
        # Our own table, then the platform's. `mimetypes` reads the registry on
        # Windows, where .js is routinely mapped to text/plain and .css can be
        # too — a front end served under those types is a blank page, and the
        # cause is nowhere near the symptom.
        ctype = (CONTENT_TYPES.get(path.suffix.lower())
                 or mimetypes.guess_type(path.name)[0]
                 or "application/octet-stream")
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

    def _client(self) -> str:
        """Who to hold a rate limit against.

        On the public port every connection arrives from the tunnel client on
        loopback, so the peer address is the same for everybody and no use at
        all here. The forwarding headers *are* worth believing on that port, and
        only there: the one thing that can reach it is our own tunnel process,
        so it is the only place they cannot simply be typed in by the caller.
        """
        if self.public:
            for header in ("Cf-Connecting-Ip", "X-Forwarded-For"):
                value = self.headers.get(header)
                if value:
                    return value.split(",")[0].strip()[:64]
        return (self.client_address[0] or "").split("%")[0]

    def _known_host(self) -> bool:
        """Whether we have any business answering to the name we were asked by.

        This is the DNS-rebinding gate. `evil.example` resolving to 127.0.0.1
        makes a stranger's page same-origin with this server as far as the
        browser is concerned, and `_is_local` — which is what stands between a
        web page and the host's decklists — would agree with it. A name we were
        never reachable under is refused here, before any route runs.
        """
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        host = host.strip().strip("[]").lower()
        if host in LOCAL_HOSTS or host == PUBLIC_HOST.lower():
            return True
        # How this machine is legitimately addressed on a network: by an address
        # rather than a name, or by the name the network itself gave it.
        if host.endswith(".local"):
            return True
        return ":" in host or all(char in "0123456789." for char in host)

    def _same_origin(self) -> bool:
        """Whether a state-changing request came from our own page.

        A browser attaches `Origin` to every cross-origin request it makes and
        cannot be talked out of it, so comparing it to the host we were asked by
        is enough to stop another site from posting moves — or deck deletions —
        through a browser that happens to have this server open. A request with
        no `Origin` at all is not a browser doing that, and is left alone: curl,
        the test suite, and the tutorial harness all live there.
        """
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return (origin.split("://", 1)[-1].strip().lower()
                == (self.headers.get("Host") or "").strip().lower())

    def _barred(self, path: str, post: bool) -> bool:
        """Refuse this request on posture alone, before its route is reached.

        Answering here rather than at each route is the point: a new route is
        private until it is named in `PUBLIC_ROUTES`, which is the direction a
        mistake should fall.
        """
        if not self._known_host():
            self._send(403, b"unrecognised host", "text/plain")
            return True
        if post and not self._same_origin():
            self._json({"error": "cross-site request refused"}, 403)
            return True
        if not self.public:
            return False
        # The front end itself, its styles and the card art are what a joiner
        # came for; it is the API that is narrowed.
        if path.startswith("/api/") and path not in PUBLIC_ROUTES:
            self._json({"error": "not available over the internet"}, 403)
            return True
        if post:
            limiter = JOIN_LIMIT if path == "/api/room/join" else MOVE_LIMIT
            if not limiter.allow(self._client()):
                self._json({"error": "too many requests; slow down"}, 429)
                return True
        return False

    def _find_room(self, code: Optional[str], secret: Optional[str]) -> Optional[Room]:
        """The room with this code, if this request is entitled to find it.

        A public request without the room's secret is answered exactly as one
        naming a room that does not exist — a script walking the code space
        should not learn which four letters are a live game.
        """
        room = self.app.room(code)
        if room is None:
            return None
        if self.public and not room.admits(secret):
            return None
        return room

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
        if not length or length > MAX_BODY:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    # -- routes ----------------------------------------------------------
    # -- profiles ----------------------------------------------------------
    def _profile_post(self, path: str, body: dict) -> None:
        """Everything under `/api/profile…`. Local machine only.

        Kept in one method rather than eight branches in `do_POST` because
        every one of them shares the same two rules: it happens on this
        machine, and it can fail three ways — locked, wrong passphrase, or a
        file that will not open — which the browser has to tell apart.
        """
        if not self._is_local():
            self._json({"error": "profiles live on the machine running the "
                                 "server"}, 403)
            return
        passphrase = str(body.get("passphrase") or "")[:MAX_PASSPHRASE]
        try:
            if path == "/api/profiles/new":
                name = PR.clean_name(body.get("name"))
                if not name:
                    self._json({"error": "give the profile a name"}, 400)
                    return
                PROFILES.create(name, passphrase, str(body.get("avatar") or ""))
            elif path == "/api/profiles/open":
                PROFILES.open(str(body.get("slug") or ""), passphrase)
            elif path == "/api/profiles/close":
                PROFILES.close()
            elif path == "/api/profiles/delete":
                # The logs are the player's either way: taking them with the
                # profile is asked for, never assumed.
                dropped = profile_store().delete(str(body.get("slug") or ""),
                                                 passphrase)
                if body.get("logs"):
                    for name in PR.replays_of(dropped):
                        drop_replay(name)
                active = PROFILES.active()
                if active is not None and active.slug == PR.slugify(
                        str(body.get("slug") or "")):
                    PROFILES.close()
            elif path == "/api/profile/avatar":
                session = PROFILES.active()
                if session is None:
                    self._json({"error": "no profile is open"}, 409)
                    return
                picked = PR.clean_avatar(body.get("avatar"))
                if picked == "" and body.get("avatar"):
                    self._json({"error": "that is not a picture this can "
                                         "store"}, 400)
                    return
                session.profile.avatar = picked
                session.save()
            elif path == "/api/profile/settings":
                # The viewer's preferences, kept with the player rather than
                # with the browser. Merged, never replaced — see
                # `Profile.remember` — and handed straight back inside the
                # active profile, so nothing has to re-fetch.
                session = PROFILES.active()
                if session is None:
                    self._json({"error": "no profile is open"}, 409)
                    return
                session.profile.remember(body.get("settings"))
                session.save()
            elif path == "/api/profile/games/keep":
                session = PROFILES.active()
                if session is None:
                    self._json({"error": "no profile is open"}, 409)
                    return
                entry = session.profile.keep(str(body.get("id") or ""),
                                             bool(body.get("kept")))
                if entry is None:
                    self._json({"error": "no game by that id"}, 404)
                    return
                # Un-marking a game can push it straight back out of the
                # window it was being held above.
                for gone in session.profile.prune():
                    drop_replay(gone.replay)
                session.save()
            elif path == "/api/profile/games/delete":
                session = PROFILES.active()
                if session is None:
                    self._json({"error": "no profile is open"}, 409)
                    return
                entry = session.profile.forget(str(body.get("id") or ""))
                if entry is None:
                    self._json({"error": "no game by that id"}, 404)
                    return
                session.save()
                drop_replay(entry.replay)
            else:
                self._json({"error": "no such profile route"}, 404)
                return
        except PR.Locked as exc:
            self._json({"error": str(exc), "locked": True}, 401)
            return
        except PR.BadPassphrase as exc:
            self._json({"error": str(exc), "wrong": True}, 403)
            return
        except PR.ProfileError as exc:
            self._json({"error": str(exc)}, 400)
            return
        except OSError as exc:
            self._json({"error": f"could not write {profile_store().dir}: {exc}"},
                       500)
            return
        self._json({"ok": True, "profiles": profile_store().list(),
                    "active": PROFILES.view()})

    def do_GET(self):
        path = self.path.split("?")[0]
        if self._barred(path, post=False):
            return
        if path == "/":
            self._file(VIEWER / "index.html")
        elif path in ("/app.js", "/sfx.js", "/confirm.js", "/style.css",
                      "/sizing.js",
                      "/builder.js", "/builder.css",
                      "/table.js", "/table.css",
                      "/showcase.js", "/showcase.css",
                      "/tutorial.js", "/tutorial.css",
                      "/replays.js", "/replays.css",
                      "/title.js", "/title.css",
                      "/profile.js", "/profile.css", "/prefs.js",
                      "/netplay.js", "/brick.svg"):
            self._file(VIEWER / path.lstrip("/"))
        elif path == "/icon.ico":
            # The game's face: the browser tab, and the window a Chromium
            # app-mode launch draws. It sits at the top of the bundle rather
            # than in `viewer/`, because the build and the installer both want
            # the same file and neither of them is the front end.
            self._file(ROOT / ICON_NAME, cache=True)
        elif path.startswith("/card_images/"):
            name = Path(path).name
            if "/" in name or ".." in name:
                self._send(400, b"bad path", "text/plain")
                return
            self._file(card_image(name), cache=True)
        elif path == "/api/tunnel":
            # Setting this machine up is the machine's own business, and the
            # token is a credential — so this is gated like the deck store
            # rather than like the board, and is not in `PUBLIC_ROUTES`.
            if not self._is_local():
                self._json({"error": "only on the machine running the server"}, 403)
                return
            state = TUN.status(NGROK_TOKEN)
            state["open"] = bool(PUBLIC_URL)
            state["url"] = PUBLIC_URL
            # What a one-click install would actually run, so the screen can
            # show the command rather than asking anyone to trust a button.
            plan = TUN.installer("cloudflared") or TUN.installer("ngrok")
            state["canInstall"] = bool(plan)
            state["installs"] = {name: " ".join(TUN.installer(name) or [])
                                 for name in ("cloudflared", "ngrok")}
            state["pages"] = TUN.DOWNLOAD_PAGES
            state["tokenPage"] = TUN.TOKEN_PAGE
            if INSTALL_JOB is not None:
                state["job"] = INSTALL_JOB.poll()
            self._json(state)
        elif path == "/api/signal/offer":
            # Reached by the *other player's machine*, holding a code. Rate
            # limited on its own account: a key is six characters out of an
            # unambiguous alphabet, which is a billion, but a route a stranger
            # can call in a loop should cost them something regardless.
            if not JOIN_LIMIT.allow(self._client()):
                self._json({"error": "too many requests; slow down"}, 429)
                return
            found = SIGNAL_BOARD.offer_for(self._query().get("id", ""))
            if found is None:
                # A wrong key and an expired game are the same answer, so a
                # stranger cannot use this to learn which keys exist.
                self._json({"error": "no game with that code"}, 404)
                return
            self._json({"offer": found.offer})
        elif path == "/api/peer/answer":
            # The host's own browser, waiting for the reply to be collected.
            # Loopback, so the key may be named here — it is the one place it
            # never leaves the machine.
            try:
                answer = SIGNAL_BOARD.take_answer(self._query().get("key", ""))
            except RZ.RendezvousError as exc:
                # Something was posted that this key cannot open. Whoever it
                # was did not have the code, so the game is not going to
                # happen and saying so beats waiting forever.
                self._json({"error": str(exc)}, 409)
                return
            self._json({"answer": answer})
        elif path == "/api/peer/out":
            # Held rather than polled: a decision the other player is waiting
            # on must leave as soon as our seat produces it.
            lobby = self.app.peer_lobby()
            if lobby is None:
                self._json({"msgs": [], "gone": True})
                return
            lobby.touch()
            # `hold` lets the caller ask for a shorter wait than the default —
            # a browser that wants to show a connecting spinner, or a test that
            # is asserting nothing *has* been sent and would otherwise pay the
            # full hold to find out. Capped, so it cannot pin a thread open.
            try:
                hold = min(PEER_HOLD, max(0.0, float(self._query().get("hold", PEER_HOLD))))
            except (TypeError, ValueError):
                hold = PEER_HOLD
            self._json({"msgs": lobby.bridge.drain(hold), "peer": lobby.status()})
        elif path == "/api/config":
            decks = available_decks()
            self._json({
                "decks": [{"name": n, "size": len(c)} for n, c in decks.items()],
                "pilots": available_pilots(),
                # Empty unless the server was started with --lan: without it
                # nothing off this machine can reach the port, and offering a
                # link that cannot work is worse than offering none.
                "lan": LAN_URLS,
                # Empty unless started with --online. When set it is the address
                # an invite link is built from, in preference to a LAN one: the
                # person you are sending it to is not on your network.
                "public": PUBLIC_URL,
            })
        elif path == "/api/state":
            self._state(self._query())
        elif path == "/api/room":
            query = self._query()
            room = self._find_room(query.get("room"), query.get("pass"))
            if room is None:
                self._json({"error": "no room with that code", "gone": True}, 404)
                return
            seat = room.seat_of(query.get("token"))
            room.touch(seat)
            self._json({"room": room.lobby(), "seat": seat,
                        "pass": room.secret if seat is not None else None})
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
        elif path == "/api/profiles":
            # Local only, like the deck store and the replay folder: these are
            # files on this machine, and a stranger in a room has no business
            # knowing who plays here.
            if not self._is_local():
                self._json({"error": "profiles live on the machine running "
                                     "the server"}, 403)
                return
            self._json({"profiles": profile_store().list(),
                        "active": PROFILES.view(),
                        "path": str(profile_store().dir)})
        elif path == "/api/replays":
            rows = [row for row in (replay_summary(p) for p in replay_files())
                    if row is not None]
            self._json({"replays": rows, "path": str(replay_store()),
                        "local": self._is_local()})
        elif path == "/api/replay":
            name = safe_replay_name(self._query().get("name"))
            path_ = replay_store() / name if name else None
            if path_ is None or not path_.is_file():
                self._json({"error": "no replay by that name"}, 404)
                return
            # Served as a download rather than a viewer route: a replay is a
            # file someone hands to someone else with the bug report.
            self._send(200, path_.read_bytes(), "application/json")
        elif path == "/api/cardnames":
            self._json({"cards": card_names(self.app.db)})
        elif path == "/api/card":
            # One card, by id. The log names cards as "Name (ST9-007)" because
            # 271 of 813 names are printed on more than one card, and the name
            # index can only hold one card per name — so a hover on a name that
            # is not the index's copy comes back here for the right one.
            card_id = (self._query().get("id") or "").strip()
            if card_id not in self.app.db:
                self._json({"error": "no card by that id"}, 404)
                return
            self._json({"card": card_json(self.app.db, card_id)})
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
        if query.get("peer"):
            # A peer game is watched exactly like any other match, with one
            # difference that matters: the view is always built for *our* seat,
            # never a spectator's. Both machines hold the whole GameState in a
            # lockstep game, so hiding the other hand is the viewer's job here
            # and it must not be skipped. See `braverse/netplay.py`.
            lobby = self.app.peer_lobby()
            if lobby is None:
                self._json({"version": 0, "idle": True})
                return
            lobby.touch()
            if lobby.match is None:
                self._json({"version": 0, "idle": True, "lobby": True,
                            "peer": lobby.status()})
                return
            if since is not None:
                lobby.match.wait_for(since)
            view = lobby.match.view(lobby.seat)
            view["peer"] = lobby.status()
            self._json(view)
            return
        if not code:
            if self.public:
                # The host's own solo game against a bot is not on the internet.
                self._json({"version": 0, "idle": True})
                return
            match = self.app.match
            if match is None:
                self._json({"version": 0, "idle": True})
                return
            if since is not None:
                match.wait_for(since)
            self._json(match.view())
            return

        room = self._find_room(code, query.get("pass"))
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
        room = self._find_room(body.get("room"), body.get("pass"))
        if room is None:
            return None, None
        return room, room.seat_of(body.get("token"))

    def do_POST(self):
        path = self.path.split("?")[0]
        if self._barred(path, post=True):
            return
        body = self._body()
        if path == "/api/new":
            decks = available_decks()
            names = body.get("decks") or ["st9_sea_fairy", "st8_wind_archer"]
            pilots = body.get("pilots") or ["human", "heuristic"]
            # The guided first game settles all of this itself: stacked decks
            # that are not on disk, the scripted opponent, and a fixed seed.
            # Taking the names from the request would only let a caller ask for
            # a tutorial the course cannot teach.
            teaching = bool(body.get("tutorial"))
            if teaching:
                names = [f"tutorial · {TUT.PLAYER_LIST}",
                         f"tutorial · {TUT.OPPONENT_LIST}"]
                pilots = ["human", "tutorial"]
            elif any(n not in decks for n in names):
                self._json({"error": "unknown deck"}, 400)
                return
            if any(p not in available_pilots() for p in pilots):
                self._json({"error": "unknown pilot"}, 400)
                return
            seed = body.get("seed")
            config = MatchConfig(
                decks=list(names),
                pilots=list(pilots),
                tutorial=teaching,
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
        elif path == "/api/quit":
            # Leaving a game in progress. Not in PUBLIC_ROUTES, so a joiner
            # cannot end the host's game from the other side of a tunnel; a
            # room is left through /api/room/leave and a peer game through
            # /api/peer/close, both of which have someone to tell.
            self._json({"ok": True, "ended": self.app.end_match()})
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
        elif path == "/api/decks/export":
            # The file is written *here*, not handed to the browser as a
            # download: the game is often shown in a desktop window (see
            # `desktop.py`), and a web view has nowhere to put a downloaded
            # blob — it navigates to it, paints the text over the board, and
            # leaves no way back. Writing it beside the game is also where the
            # player would have had to put it anyway.
            if not self._is_local():
                self._json({"error": "a deck can only be exported on the "
                                     "machine running the server"}, 403)
                return
            text = body.get("text")
            if not isinstance(text, str) or not text.strip():
                self._json({"error": "nothing to export"}, 400)
                return
            if len(text) > MAX_IMPORT:
                self._json({"error": "that is too big to be a decklist"}, 400)
                return
            name = clean_deck_name(body.get("name")) or "decklist"
            try:
                target = export_path(name)
                # newline="\n" because a decklist is a file people send each
                # other, and Windows would otherwise write CRLF into it.
                with target.open("w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text if text.endswith("\n") else text + "\n")
            except OSError as exc:
                self._json({"error": f"could not write the file: {exc}"}, 500)
                return
            self._json({"ok": True, "file": target.name, "path": str(target)})
        elif path == "/api/decks/import":
            # Local only, like every other route that writes decks: the file
            # being read is on this machine and the store it lands in is too.
            if not self._is_local():
                self._json({"error": "decks can only be changed on the machine "
                                     "running the server"}, 403)
                return
            text = body.get("text")
            if not isinstance(text, str) or not text.strip():
                self._json({"error": "nothing to import"}, 400)
                return
            if len(text) > MAX_IMPORT:
                self._json({"error": "that file is too big to be a decklist"}, 400)
                return
            found = parse_decklist(text, self.app.db)
            if not found.deck and not found.extra:
                self._json({"error": "no cards in that — a decklist is one card "
                                     "a line, with an id like ST9-007 or the "
                                     "card's name",
                            "skipped": found.skipped[:10]}, 400)
                return
            # Named by the file it came from unless the list named itself: a
            # deck arrives as `sea_fairy_aggro.txt` far more often than it
            # arrives with a name written inside it.
            name = clean_deck_name(found.name or body.get("name"))
            payload = deck_payload(self.app.db, found.deck, name, found.extra)
            payload["notes"] = found.notes[:20]
            payload["skipped"] = found.skipped[:20]
            payload["skippedCount"] = len(found.skipped)
            # Saving is the browser's next call, not this one's: an illegal
            # import belongs in the deck builder to be fixed, and there is
            # still exactly one route that writes the deck store.
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
        elif path == "/api/signal/answer":
            # The other player's machine, handing back what it answered with.
            ident = str(body.get("id") or "")
            answer = str(body.get("answer") or "")
            if not answer or len(answer) > RZ.MAX_SIGNAL:
                self._json({"error": "no answer in that"}, 400)
                return
            if not SIGNAL_BOARD.answer(ident, answer):
                self._json({"error": "no game with that code, or somebody "
                                     "else already joined it"}, 404)
                return
            self._json({"ok": True})
        elif path in ("/api/tunnel/authtoken", "/api/tunnel/forget",
                      "/api/tunnel/test", "/api/tunnel/install",
                      "/api/tunnel/prefer"):
            if not self._is_local():
                self._json({"error": "only on the machine running the server"}, 403)
                return
            if path == "/api/tunnel/prefer":
                # Changing which client to use cannot affect a tunnel that is
                # already open — that one belongs to whatever opened it — so
                # the reply says whether a restart is what makes it take hold.
                name = TUN.save_preference(str(body.get("prefer") or ""))
                self._json({"ok": True, "prefer": name,
                            "reopen": bool(PUBLIC_URL),
                            **TUN.status(NGROK_TOKEN)})
            elif path == "/api/tunnel/install":
                global INSTALL_JOB
                if INSTALL_JOB is not None and INSTALL_JOB.running:
                    self._json({"ok": True, "job": INSTALL_JOB.poll()})
                    return
                # The browser names a client and nothing else. `installer`
                # builds the command from fixed strings and refuses a name that
                # is not one of ours, so nothing from the request reaches a
                # command line.
                plan = TUN.installer(str(body.get("client") or ""))
                if plan is None:
                    self._json({"error": "this computer has no package manager "
                                         "I know how to drive — use the "
                                         "download page instead"}, 501)
                    return
                INSTALL_JOB = TUN.Job(plan).start()
                self._json({"ok": True, "job": INSTALL_JOB.poll()})
            elif path == "/api/tunnel/authtoken":
                token = str(body.get("token") or "").strip()
                if not token:
                    self._json({"error": "paste your authtoken first"}, 400)
                    return
                # Hand it to ngrok's own store, which is what the manual
                # `ngrok config add-authtoken` does — after this ngrok finds it
                # by itself, for every tunnel, without us being involved. The
                # value is checked against `TOKEN_RE` in there before it goes
                # anywhere near an argument list.
                why = TUN.configure_token(token)
                if why:
                    self._json({"error": why}, 400)
                    return
                # And kept here as well, so a machine whose ngrok config is
                # wiped — or replaced by a fresh install — does not silently
                # stop working.
                try:
                    TUN.save_token(token)
                except OSError:
                    pass        # ngrok has it; our copy is the belt, not the braces
                # Used for this run too, so "Save" and "it works now" are the
                # same moment rather than one needing a restart.
                globals()["NGROK_TOKEN"] = token
                # The reply says *that* it worked, never what was saved.
                self._json({"ok": True, **TUN.status(token)})
            elif path == "/api/tunnel/forget":
                globals()["NGROK_TOKEN"] = ""
                had = TUN.forget_token()
                self._json({"ok": True, "had": had, **TUN.status("")})
            else:
                # Opening one for real is the only honest test: a token can be
                # well-formed and still be rejected. The tunnel is kept rather
                # than thrown away, because the next thing this player does is
                # host a game with it.
                try:
                    url = ensure_public()
                except TUN.TunnelError as exc:
                    self._json({"error": str(exc)}, 503)
                    return
                self._json({"ok": True, "url": url, **TUN.status(NGROK_TOKEN)})
        elif path == "/api/peer/publish":
            # Host: put our offer somewhere the code can point at, and hand
            # back the code. Opening the tunnel is what can actually fail here,
            # and it fails with advice rather than a stack trace.
            offer = str(body.get("offer") or "")
            if not offer or len(offer) > RZ.MAX_SIGNAL:
                self._json({"error": "no offer in that"}, 400)
                return
            try:
                public = ensure_public()
            except TUN.TunnelError as exc:
                # `INSTALL_HINT` is written for `--online` and talks about
                # rooms, which is the wrong advice in front of someone who was
                # trying to start a peer game. Same requirement, said for what
                # they were actually doing.
                missing = not TUN.available()
                self._json({"error": TUN.PEER_HINT if missing else str(exc)}, 503)
                return
            key = SIGNAL_BOARD.publish(offer)
            try:
                code = RZ.format_code(public, key)
            except RZ.RendezvousError as exc:
                self._json({"error": str(exc)}, 500)
                return
            self._json({"code": code, "key": key})
        elif path == "/api/peer/collect":
            # Joiner: go and fetch the offer the code points at. Done here
            # rather than in the browser because a browser reaching another
            # machine's tunnel is a cross-origin request, and opening CORS for
            # it would let any page anyone visits talk to this server.
            try:
                base, key = RZ.parse_code(str(body.get("code") or ""))
                offer = RZ.fetch_offer(base, key)
            except RZ.RendezvousError as exc:
                self._json({"error": str(exc)}, 400)
                return
            self._json({"offer": offer, "key": key})
        elif path == "/api/peer/reply":
            # Joiner: hand our answer back to the machine that published.
            try:
                base, key = RZ.parse_code(str(body.get("code") or ""))
                RZ.send_answer(base, key, str(body.get("answer") or ""))
            except RZ.RendezvousError as exc:
                self._json({"error": str(exc)}, 400)
                return
            self._json({"ok": True})
        elif path == "/api/peer/new":
            # Local only, and deliberately so: the whole point of a peer game
            # is that nothing is hosted here for a stranger to reach. The data
            # channel is dialled by the browser, not by us.
            deck = str(body.get("deck") or "")
            problem = self._deck_problem(deck)
            if problem:
                self._json({"error": problem}, 400)
                return
            seat = 0 if body.get("host") else 1
            lobby = self.app.new_peer(seat, deck, clean_deck_name(body.get("name")))
            self._json({"ok": True, "seat": seat, "peer": lobby.status()})
        elif path == "/api/peer/in":
            # Whatever arrived on the data channel, in the order it arrived.
            lobby = self.app.peer_lobby()
            if lobby is None:
                self._json({"error": "no peer game", "gone": True}, 404)
                return
            lobby.touch()
            messages = body.get("msgs")
            if not isinstance(messages, list):
                self._json({"error": "expected a list of messages"}, 400)
                return
            lobby.bridge.deliver(messages)
            self._json({"ok": True, "peer": lobby.status()})
        elif path == "/api/peer/close":
            self.app.close_peer(str(body.get("why") or "left the match"))
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
            self._json({"room": room.code, "seat": 0, "token": room.tokens[0],
                        "pass": room.secret})
        elif path == "/api/room/join":
            room = self._find_room(body.get("room"), body.get("pass"))
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
            self._json({"room": room.code, "seat": seat, "token": token,
                        "pass": room.secret})
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
            elif body.get("peer"):
                # Our own seat in a peer game. The seat is not taken from the
                # request: this server plays exactly one seat, the one the
                # handshake gave it, and a browser cannot ask to answer for the
                # other machine.
                lobby = self.app.peer_lobby()
                if lobby is None or lobby.match is None:
                    self._json({"error": "no peer game", "gone": True}, 404)
                    return
                match, seat = lobby.match, lobby.seat
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
        elif path == "/api/replays/save":
            # A game being played in a room is worth keeping too, and the
            # person asking is one of the two playing it — the seat token is
            # checked the same way answering a question is.
            room, seat = self._seated(body)
            match = room.match if (room is not None and seat is not None) \
                else self.app.match
            if match is None:
                self._json({"error": "no match"}, 400)
                return
            try:
                saved = match.save_replay()
            except RP.ReplayError as exc:
                self._json({"error": str(exc)}, 409)
                return
            except OSError as exc:
                self._json({"error": f"could not write {replay_store()}: {exc}"}, 500)
                return
            match.publish()
            self._json({"ok": True, "name": saved.name, "path": str(saved)})
        elif path == "/api/replays/watch":
            # Either a file on this machine or one dropped into the browser.
            # The uploaded case is what makes a replay shareable: the file is
            # the whole game, so it plays back anywhere this build runs.
            blob = body.get("replay")
            if blob is None:
                name = safe_replay_name(body.get("name"))
                path_ = replay_store() / name if name else None
                if path_ is None or not path_.is_file():
                    self._json({"error": "no replay by that name"}, 404)
                    return
                try:
                    recording = RP.Recording.load(path_)
                except RP.ReplayError as exc:
                    self._json({"error": str(exc)}, 400)
                    return
            else:
                try:
                    recording = RP.Recording.from_json(blob)
                except RP.ReplayError as exc:
                    self._json({"error": str(exc)}, 400)
                    return
            unknown = sorted({c for deck in (*recording.deck_lists,
                                             *recording.extra_lists)
                              for c in deck if c not in self.app.db})
            if unknown:
                self._json({"error": "this replay uses cards this build does "
                                     f"not have: {unknown[:5]}"}, 400)
                return
            config = MatchConfig(
                decks=recording.deck_names,
                # Neither seat is being played; both are reading from the file.
                pilots=["replay", "replay"],
                seed=recording.seed,
                delay=float(body.get("delay", 0.7)),
                paused=bool(body.get("paused", False)),
                reveal=bool(body.get("reveal", True)),
                record=False,
                replay=recording,
            )
            try:
                match = self.app.new_match(config)
            except Exception as exc:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)
                return
            self._json({"ok": True, "seed": match.seed,
                        "decisions": len(recording.decisions)})
        elif path == "/api/replays/delete":
            if not self._is_local():
                self._json({"error": "replays can only be deleted on the "
                                     "machine running the server"}, 403)
                return
            name = safe_replay_name(body.get("name"))
            path_ = replay_store() / name if name else None
            if path_ is None or not path_.is_file():
                self._json({"error": "no replay by that name"}, 404)
                return
            try:
                path_.unlink()
            except OSError as exc:
                self._json({"error": f"could not delete {path_}: {exc}"}, 500)
                return
            self._json({"ok": True})
        elif path.startswith("/api/profile"):
            self._profile_post(path, body)
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


class PublicHandler(Handler):
    """The same server, on the port the tunnel forwards to.

    Public *by construction* rather than by inspection. It cannot be talked into
    believing a request came from the host's keyboard, because it has no way to
    say so — which is the whole reason `--online` runs two listeners instead of
    hardening one. A tunnel client connects from loopback, so on a shared port
    the only question that matters here ("is this the person who owns these
    files?") would answer yes to everybody.
    """

    public = True

    def _is_local(self) -> bool:
        return False


class Viewer(ThreadingHTTPServer):
    """The HTTP server, wired so it cannot outlive the terminal that ran it."""

    daemon_threads = True       # a live request must never hold the process up
    # Restart on the same port without waiting out TIME_WAIT. Not on Windows:
    # there SO_REUSEADDR lets a second server bind a port someone is already
    # listening on, and then half the requests go to the other process — so
    # take the honest "port is busy" error instead.
    allow_reuse_address = os.name != "nt"


def port_holder(port: int) -> str:
    """PID currently listening on ``port``, for a useful error message."""
    import subprocess
    if WINDOWS:
        # netstat is the one always present; -ano keeps it numeric so nothing
        # here depends on the machine's language.
        try:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                 capture_output=True, text=True, timeout=5).stdout
        except Exception:
            return ""
        pids = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[3] == "LISTENING" \
                    and parts[1].rsplit(":", 1)[-1] == str(port):
                pids.append(parts[4])
        return " ".join(dict.fromkeys(pids))
    try:
        out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""
    return " ".join(out.split())


def main() -> None:
    global PUBLIC_URL, PUBLIC_HOST
    import signal

    # Everything this server prints — card names, the em dashes in its own
    # status lines — is text the Windows default code page cannot encode, and a
    # redirected stdout there is cp1252, not UTF-8. See braverse.console.
    utf8_output()

    parser = argparse.ArgumentParser(description=__doc__)
    # $PORT lets a supervisor (or a preview harness juggling several sessions)
    # hand the server a free port without rewriting the command line.
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PORT") or 8080))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--lan", action="store_true",
                        help="listen on every interface so someone else on this "
                             "network can join a room")
    parser.add_argument("--online", action="store_true",
                        help="also put a room on a public https address, via a "
                             "tunnel client, so someone off this network can "
                             "join (needs cloudflared or ngrok)")
    # ngrok will not open a tunnel for an account it cannot identify, so a
    # machine that uses ngrok rather than cloudflared needs a token from
    # somewhere. Passing one here is for a one-off or a script; `--save-…` is
    # for a machine you use, and after that neither flag is needed again.
    parser.add_argument("--tunnel", default="", metavar="CLIENT",
                        choices=[""] + [b.name for b in TUN.BACKENDS],
                        help="which tunnel client to prefer; the default picks "
                             "cloudflared when it is installed, since it needs "
                             "no account. Also settable in Settings.")
    parser.add_argument("--ngrok-authtoken", default="", metavar="TOKEN",
                        help="use this ngrok authtoken (also read from "
                             f"${TUN.AUTHTOKEN_ENV}, or from --save-ngrok-authtoken)")
    parser.add_argument("--save-ngrok-authtoken", action="store_true",
                        help="remember --ngrok-authtoken for future runs, in "
                             "your home directory and readable only by you")
    parser.add_argument("--forget-ngrok-authtoken", action="store_true",
                        help="delete the remembered ngrok authtoken and exit")
    parser.add_argument("--no-browser", action="store_true",
                        help="serve, but do not open anything")
    # The game is a desktop game as far as anyone playing it is concerned, so a
    # window is what it opens when the machine can draw one; `desktop` decides
    # what "can" means. --browser forces the old tab, for debugging the front
    # end with real devtools.
    parser.add_argument("--window", action="store_true",
                        help="force the desktop window (fail if no backend)")
    parser.add_argument("--browser", action="store_true",
                        help="open a browser tab instead of a window")
    args = parser.parse_args()

    global NGROK_TOKEN
    if args.forget_ngrok_authtoken and args.save_ngrok_authtoken:
        # Both were asked for and only one can happen; doing the destructive
        # one silently and dropping the other is the wrong way to guess.
        parser.error("--save-ngrok-authtoken and --forget-ngrok-authtoken are "
                     "opposites; pick one")
    if args.forget_ngrok_authtoken:
        print("forgot the saved ngrok authtoken" if TUN.forget_token()
              else "there was no saved ngrok authtoken")
        raise SystemExit(0)
    if args.tunnel:
        # A flag is for this run and for good: the same value the screen sets,
        # so the two cannot drift into disagreeing about which client is used.
        TUN.save_preference(args.tunnel)
    NGROK_TOKEN = args.ngrok_authtoken.strip()
    if args.save_ngrok_authtoken:
        if not NGROK_TOKEN:
            parser.error("--save-ngrok-authtoken needs --ngrok-authtoken TOKEN")
        # The path, never the token: this line ends up in terminal scrollback,
        # in screenshots and in pasted bug reports.
        print(f"saved the ngrok authtoken to {TUN.save_token(NGROK_TOKEN)}")

    if args.window and args.browser:
        parser.error("--window and --browser are opposites; pick one")
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
            kill = f"taskkill /PID {pid} /F" if WINDOWS else f"kill {pid}"
            print(f"an older viewer is still running as PID {pid} — stop it with:\n"
                  f"    {kill}")
        print(f"or pick another port:\n    python play_server.py --port {args.port + 1}")
        raise SystemExit(1)

    # The public face of the server, when asked for: a second listener bound to
    # loopback on a port of the OS's choosing, serving `PublicHandler`, with the
    # tunnel pointed at it. Nothing but the tunnel client can reach it, and it
    # is the only thing the tunnel can reach.
    public: Optional[Viewer] = None
    link: Optional[TUN.Tunnel] = None
    if args.online:
        if not TUN.available():
            print(TUN.INSTALL_HINT)
            raise SystemExit(1)
        PublicHandler.app = Handler.app
        public = Viewer(("127.0.0.1", 0), PublicHandler)
        try:
            link = TUN.open_tunnel(public.server_address[1],
                                   authtoken=NGROK_TOKEN)
        except TUN.TunnelError as exc:
            public.server_close()
            print(f"could not open a tunnel: {exc}")
            raise SystemExit(1)
        PUBLIC_URL = link.url.rstrip("/") + "/"
        PUBLIC_HOST = link.host
        threading.Thread(target=public.serve_forever, daemon=True).start()

    local = f"http://127.0.0.1:{args.port}/"
    url = local if args.host in ("0.0.0.0", "::") else f"http://{args.host}:{args.port}/"
    # A window has to own the main thread (every OS web view insists on it), so
    # in that mode the server moves to a thread and the window becomes what the
    # process is waiting on: closing it is what stops the game.
    windowed = not args.no_browser and not args.browser and (
        args.window or desktop.available())

    where = "in its own window" if windowed else f"on {url}"
    print(f"CookieRun: Braverse — visual player {where}   (ctrl-c to stop)")
    if windowed:
        print(f"  serving it locally on {url} — closing the window stops the game")
    if args.host not in ("127.0.0.1", "localhost"):
        LAN_URLS[:] = lan_urls(args.port)
        for shared in LAN_URLS:
            print(f"  others on this network can join at {shared}")
    if link is not None:
        print(f"  online via {link.backend.name}: {PUBLIC_URL}")
        print("  host a room and send the invite link it gives you — the link "
              "carries the room's key,\n  so the code alone is not enough to "
              "find your game. That address only serves\n  the game: your "
              "decks, replays and local matches stay on this machine.")

    if not args.no_browser and not windowed:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    # Closing the terminal sends SIGHUP and `kill` sends SIGTERM; take both as
    # "shut down", so a stray server can never end up holding the port. The
    # shutdown has to run off the serving thread or it deadlocks. Windows has
    # no SIGHUP — asking for it by name is an AttributeError at startup, so the
    # set is whatever this platform actually defines.
    def stop(signum, _frame):
        print(f"\nstopping ({signal.Signals(signum).name})")
        threading.Thread(target=httpd.shutdown, daemon=True).start()
        if windowed:
            desktop.close_window()   # or the window outlives the server it shows

    for name in ("SIGTERM", "SIGHUP", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, stop)

    def cleanup() -> None:
        if link is not None:
            link.close()               # or the tunnel outlives the game it served
        if public is not None:
            public.shutdown()
            public.server_close()
        if Handler.app.match is not None:
            Handler.app.match.stop()   # release a match thread blocked on a human
        Handler.app.close()            # and the same for every open room
        httpd.server_close()
        print("bye")

    if windowed:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        backend = desktop.open_window(local, on_close=httpd.shutdown,
                                      icon=window_icon())
        if not backend:
            # Asked for a window on a machine that cannot draw one: say so
            # rather than serving a game nobody can see.
            httpd.shutdown()
            cleanup()
            print(desktop.INSTALL_HINT)
            raise SystemExit(1)
        cleanup()
        return

    try:
        httpd.serve_forever()
    finally:
        cleanup()


if __name__ == "__main__":
    main()
