"""Turning the peer-to-peer handshake into one short code.

WebRTC needs the two machines to trade a session description before they can
talk directly, and those are kilobytes. Pasting them by hand works and needs
nothing at all, which is why it was the first thing built — but "copy this
wall of text, send it over, wait for a wall of text back, paste that" is a
miserable way to start a game with a friend.

A short code cannot contain a session description. What it can contain is
*where to go and get one*, and this project already runs something that gives a
machine a public address: the tunnel in `tunnel.py`. So the host publishes its
offer behind its own tunnel, and the code is the address plus a key. The joiner
types it, its machine goes and fetches the offer, answers back the same way,
and the two browsers connect without either player seeing a session description
at all.

The exchange is deliberately done **machine to machine, not browser to
browser**. A browser fetching another machine's tunnel is a cross-origin
request and would need CORS opened up on the host — which would mean any web
page anyone visits could talk to it. The joiner's own server has no such
problem, is already trusted by its own browser, and is where the game lives
anyway.

What a code is worth stating plainly: anyone holding one can answer the offer
and become your opponent, exactly as anyone holding a room code can take the
free seat. It is an invitation, so send it the way you would send an
invitation. The key in it is what stops someone who merely knows the tunnel
address — a room invite from the same server carries that address in the open —
from walking into a peer game as well.

The tunnel is also the *only* thing published. Gameplay never touches it: once
the two browsers are connected the decisions go directly between them, and the
tunnel could be shut down without the game noticing.
"""

from __future__ import annotations

import base64
import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

from braverse import secretbox as SB

# Codes are read aloud, retyped and sent through chat clients, so the alphabet
# is the same unambiguous one room codes use — no O/0, no I/1.
KEY_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
# Twenty characters of a 32-letter alphabet is a hundred bits. It was six when
# the key only had to be unguessable enough to stop someone stumbling into a
# game; it now also *encrypts* the exchange, so it has to be a real secret.
# That is the price of not needing the tunnel to provide TLS — and it is what
# lets a plain-HTTP tunnel like playit.gg's free tier be used safely.
KEY_LENGTH = 20

# The hosts a tunnel hands back, shortened to one character each. Writing the
# suffix out in the code would double its length for no information: there are
# only so many things `tunnel.py` can produce.
SUFFIXES = {
    "c": ".trycloudflare.com",
    "n": ".ngrok-free.app",
    "m": ".ngrok.app",
    "i": ".ngrok.io",
    "d": ".ngrok.dev",
}

# A plain host:port, spoken over http. playit.gg's free tunnels are TCP with an
# address like `foo.gl.at.ply.gg:12345` and no TLS of any kind — HTTPS there is
# a paid feature — so this tag exists to carry one, and the scheme is written
# into the tag rather than assumed. Nothing is lost by it: what crosses this
# tunnel is sealed before it is handed over (see `seal_signal`), so the
# transport is a pipe rather than something that has to be trusted.
PLAIN = "t"
# Anything else — a named tunnel, a reverse proxy someone put in front — is
# carried whole under this tag rather than refused. Longer, but it works.
OTHER = "u"

# How long an unanswered offer is kept. Long enough to send a code to someone
# and have them go and find the app; short enough that a code left in a chat
# log a week ago is not still live.
OFFER_TTL = 30 * 60.0

# A ceiling on how much of someone else's machine one HTTP call can spend.
FETCH_TIMEOUT = 20.0
MAX_SIGNAL = 256 * 1024      # an SDP is a few KB; this is only a sanity bound


class RendezvousError(Exception):
    """A code that cannot be used, or a host that will not answer."""


# ---------------------------------------------------------------------------
# sealing what crosses the tunnel
# ---------------------------------------------------------------------------
# The key in a code does two jobs, and it must do them with two different
# values. It *encrypts* the exchange, and it *names* which exchange to fetch —
# and the name travels in a URL over a tunnel that may have no TLS at all, so
# the name cannot be the key. Both are derived from it instead, by HMAC, and
# knowing one tells you nothing about the other.
#
# `subkey` rather than `derive`: `derive` is scrypt, which is memory-hard
# because a passphrase someone chose is guessable. A code key is a hundred bits
# out of `os.urandom`, so there is nothing to slow an attacker down *to*, and
# paying scrypt twice a game would only make starting one slower.
SIGNAL_LABEL = b"braverse-rendezvous-v1"


def _master(key: str) -> bytes:
    return SB.subkey((key or "").strip().upper().encode("utf-8"), SIGNAL_LABEL)


def lookup_id(key: str) -> str:
    """The public name of an exchange: derived from the key, never the key.

    This is what goes in the URL. Someone who watches the tunnel — or runs it —
    learns which exchange is being fetched and nothing whatever about how to
    read it.
    """
    return SB.subkey(_master(key), b"id").hex()[:24]


def seal_signal(key: str, text: str, *, role: str) -> str:
    """A session description, sealed for the far side and nobody else.

    ``role`` is bound in as associated data so an offer can never be replayed
    back as an answer: the two are the same shape and would otherwise be
    interchangeable to anyone who could move bytes around.
    """
    blob = SB.seal(_master(key), text.encode("utf-8"),
                   aad=role.encode("ascii"))
    return base64.urlsafe_b64encode(blob).decode("ascii")


def open_signal(key: str, blob: str, *, role: str) -> str:
    """The other side's description, or `RendezvousError`.

    A failure here is not a decoding hiccup to be shrugged at: the tag is
    checked before a byte is decrypted, so the only ways to reach it are a
    wrong key or something having been altered in transit. Both mean this is
    not the game the code was for.
    """
    try:
        raw = base64.urlsafe_b64decode(blob.encode("ascii"))
        return SB.unseal(_master(key), raw, aad=role.encode("ascii")).decode("utf-8")
    except (SB.BadSeal, ValueError, UnicodeDecodeError):
        raise RendezvousError(
            "that invitation could not be opened with this code — either the "
            "code is wrong, or what came back was not what was sent")


def mint_key() -> str:
    return "".join(secrets.choice(KEY_LETTERS) for _ in range(KEY_LENGTH))


# ---------------------------------------------------------------------------
# the code itself
# ---------------------------------------------------------------------------
def format_code(url: str, key: str) -> str:
    """``https://a-b-c-d.trycloudflare.com`` + a key -> ``c.KEY.a-b-c-d``.

    The key comes *before* the address, which is what makes this parse
    unambiguously: a host contains dots and a key never does, so splitting off
    exactly two fields from the left leaves the rest whole however many dots it
    has. The obvious ordering — address then key — cannot say where one ends
    and the other begins for the general case.
    """
    split = urlsplit(url if "//" in url else "//" + url)
    host = split.hostname or ""
    if not host:
        raise RendezvousError(f"no host in {url!r}")
    for tag, suffix in SUFFIXES.items():
        if host.endswith(suffix):
            return f"{tag}.{key}.{host[:-len(suffix)]}"
    # Anything without TLS has to say so, and carries its port.
    if split.scheme == "http" or split.port:
        return f"{PLAIN}.{key}.{host}" + (f":{split.port}" if split.port else "")
    return f"{OTHER}.{key}.{host}"


def parse_code(code: str) -> tuple[str, str]:
    """A code back into ``(base url, key)``. Raises `RendezvousError`.

    Deliberately forgiving about how it arrives — codes get retyped, wrapped by
    chat clients and pasted with a stray space — and strict about what it
    yields, since everything downstream is about to be pointed at whatever
    address comes out of here.
    """
    body = "".join(str(code or "").split())
    parts = body.split(".", 2)
    if len(parts) != 3:
        raise RendezvousError("that does not look like a game code")
    tag, key, rest = parts[0].lower(), parts[1].upper(), parts[2].lower()
    if len(key) != KEY_LENGTH or any(c not in KEY_LETTERS for c in key):
        raise RendezvousError("that does not look like a game code")
    if not rest:
        raise RendezvousError("that code is missing its address")
    port = ""
    if tag in SUFFIXES:
        host, scheme = rest + SUFFIXES[tag], "https"
    elif tag == OTHER:
        host, scheme = rest, "https"
    elif tag == PLAIN:
        # The only form allowed to carry a port, and the only one that is not
        # https — both stated by the tag, so neither can be smuggled in under
        # one of the others.
        host, _, port = rest.partition(":")
        scheme = "http"
        if not port.isdigit() or not 0 < int(port) <= 65535:
            raise RendezvousError("that code's address has no usable port")
    else:
        raise RendezvousError("that code is for a kind of address this "
                              "version does not know about")
    # A host is about to be fetched from, so nothing but a hostname may come
    # out: no path, no credentials, no other scheme, and no port except the one
    # `PLAIN` was allowed to bring.
    if any(c in host for c in "/\\@:?#") or ".." in host or " " in host or not host:
        raise RendezvousError("that code's address is not a plain hostname")
    return f"{scheme}://{host}" + (f":{port}" if port else ""), key


# ---------------------------------------------------------------------------
# the host's side: one offer, waiting to be collected
# ---------------------------------------------------------------------------
@dataclass
class Offer:
    """A published offer and the answer that will come back to it.

    Both are stored *sealed*: this machine can read them, but they are already
    in the form that crosses the tunnel, so nothing has to remember to encrypt
    them on the way out.
    """

    key: str                  # ours, and never sent anywhere
    offer: str                # sealed
    answer: str = ""          # sealed
    made: float = field(default_factory=time.time)

    @property
    def id(self) -> str:
        return lookup_id(self.key)

    def stale(self) -> bool:
        return time.time() - self.made > OFFER_TTL


class Board:
    """Every offer this machine is currently waiting to have answered.

    Indexed by the *derived* id, which is what a caller off this machine knows,
    while the key stays inside the record. At most one matters at a time — a
    person hosts one game — but a dict rather than a single slot means starting
    a second game cannot silently invalidate a code already sent to somebody,
    and a stale one expires on its own clock instead of being overwritten.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.offers: dict[str, Offer] = {}      # id -> Offer

    def publish(self, offer: str) -> str:
        """Seal an offer under a fresh key and return the key.

        The key is the whole of what the other player needs and the only thing
        that never travels: it goes into a code, and the code goes wherever the
        two of them already talk.
        """
        key = mint_key()
        record = Offer(key=key, offer=seal_signal(key, offer, role="offer"))
        with self.lock:
            self._reap()
            self.offers[record.id] = record
        return key

    def offer_for(self, ident: str) -> Optional[Offer]:
        with self.lock:
            self._reap()
            return self.offers.get((ident or "").strip().lower())

    def answer(self, ident: str, answer: str) -> bool:
        """Record the joiner's sealed answer. False if there is no such offer.

        The answer is *not* opened here. It is checked when the host reads it,
        which is the only place that can tell a wrong key from a right one —
        and refusing at that point rather than this one keeps a stranger who
        posts nonsense from learning whether they guessed an id correctly.
        """
        with self.lock:
            self._reap()
            found = self.offers.get((ident or "").strip().lower())
            if found is None or found.answer:
                # An offer already answered is not answered twice: the second
                # caller would be replacing a connection being made.
                return False
            found.answer = answer
            return True

    def take_answer(self, key: str) -> str:
        """The answer to our own offer, opened, if it has arrived yet.

        Raises `RendezvousError` if what came back cannot be opened with the
        key we published under — which means it was altered on the way, or
        somebody answered an id they did not have the code for.
        """
        found = self.offer_for(lookup_id(key))
        if found is None or not found.answer:
            return ""
        return open_signal(key, found.answer, role="answer")

    def drop(self, key: str) -> None:
        with self.lock:
            self.offers.pop(lookup_id(key), None)

    def _reap(self) -> None:
        for ident in [i for i, o in self.offers.items() if o.stale()]:
            del self.offers[ident]


# ---------------------------------------------------------------------------
# the joiner's side: go and get it
# ---------------------------------------------------------------------------
def fetch_offer(base: str, key: str, *, timeout: float = FETCH_TIMEOUT) -> str:
    """Collect and open the offer a code points at.

    The id goes over the wire; the key stays here and does the opening. So a
    tunnel with no TLS at all — playit.gg's free tier, say — carries only a
    name and a sealed blob, and learns nothing from either.
    """
    got = _call(f"{base}/api/signal/offer?id={lookup_id(key)}", None, timeout)
    sealed = str(got.get("offer") or "")
    if not sealed:
        raise RendezvousError("that game is not there any more — the code may "
                              "have expired, or the other player closed it")
    return open_signal(key, sealed, role="offer")


def send_answer(base: str, key: str, answer: str, *,
                timeout: float = FETCH_TIMEOUT) -> None:
    """Seal our answer and hand it back to the machine that published."""
    got = _call(f"{base}/api/signal/answer",
                {"id": lookup_id(key),
                 "answer": seal_signal(key, answer, role="answer")},
                timeout)
    if not got.get("ok"):
        raise RendezvousError(str(got.get("error")
                                  or "the other machine would not take our answer"))


def _call(url: str, body: Optional[dict], timeout: float) -> dict:
    """One request to the other machine, with its failures said in English.

    Every error here is going to be read by somebody who typed a code and is
    waiting to play, so none of them are allowed to surface as a stack trace or
    an HTTP status.
    """
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as res:
            return json.loads(res.read(MAX_SIGNAL) or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RendezvousError("that game is not there any more — the code "
                                  "may have expired, or the other player "
                                  "closed it")
        raise RendezvousError(f"the other machine refused us ({exc.code})")
    except urllib.error.URLError as exc:
        raise RendezvousError(
            "could not reach the other player's machine — their game may have "
            f"stopped, or the code may be mistyped ({exc.reason})")
    except (ValueError, TimeoutError) as exc:
        raise RendezvousError(f"the other machine answered oddly: {exc}")
