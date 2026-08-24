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

import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

# Codes are read aloud, retyped and sent through chat clients, so the alphabet
# is the same unambiguous one room codes use — no O/0, no I/1.
KEY_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
KEY_LENGTH = 6

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
    host = urlsplit(url if "//" in url else "//" + url).hostname or ""
    if not host:
        raise RendezvousError(f"no host in {url!r}")
    for tag, suffix in SUFFIXES.items():
        if host.endswith(suffix):
            return f"{tag}.{key}.{host[:-len(suffix)]}"
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
    if not key or any(c not in KEY_LETTERS for c in key):
        raise RendezvousError("that does not look like a game code")
    if not rest:
        raise RendezvousError("that code is missing its address")
    if tag in SUFFIXES:
        host = rest + SUFFIXES[tag]
    elif tag == OTHER:
        host = rest
    else:
        raise RendezvousError("that code is for a kind of address this "
                              "version does not know about")
    # A host is about to be fetched from, so nothing but a hostname may come
    # out: no path, no port, no credentials, no other scheme.
    if any(c in host for c in "/\\@:?#") or ".." in host or " " in host:
        raise RendezvousError("that code's address is not a plain hostname")
    return f"https://{host}", key


# ---------------------------------------------------------------------------
# the host's side: one offer, waiting to be collected
# ---------------------------------------------------------------------------
@dataclass
class Offer:
    """A published offer and the answer that will come back to it."""

    key: str
    offer: str
    answer: str = ""
    made: float = field(default_factory=time.time)

    def stale(self) -> bool:
        return time.time() - self.made > OFFER_TTL


class Board:
    """Every offer this machine is currently waiting to have answered.

    At most one matters at a time — a person hosts one game — but keeping a
    dict rather than a single slot means starting a second game cannot silently
    invalidate a code already sent to somebody, and lets a stale one expire on
    its own clock instead of being overwritten.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.offers: dict[str, Offer] = {}

    def publish(self, offer: str) -> str:
        """Store an offer under a fresh key, and return the key."""
        key = mint_key()
        with self.lock:
            self._reap()
            self.offers[key] = Offer(key=key, offer=offer)
        return key

    def offer_for(self, key: str) -> Optional[Offer]:
        with self.lock:
            self._reap()
            return self.offers.get((key or "").upper())

    def answer(self, key: str, answer: str) -> bool:
        """Record the joiner's answer. False if there is no such offer."""
        with self.lock:
            self._reap()
            found = self.offers.get((key or "").upper())
            if found is None or found.answer:
                # An offer already answered is not answered twice: the second
                # caller would be replacing a connection that is being made.
                return False
            found.answer = answer
            return True

    def take_answer(self, key: str) -> str:
        """The answer to our own offer, if it has arrived yet."""
        found = self.offer_for(key)
        return found.answer if found is not None else ""

    def drop(self, key: str) -> None:
        with self.lock:
            self.offers.pop((key or "").upper(), None)

    def _reap(self) -> None:
        for key in [k for k, o in self.offers.items() if o.stale()]:
            del self.offers[key]


# ---------------------------------------------------------------------------
# the joiner's side: go and get it
# ---------------------------------------------------------------------------
def fetch_offer(base: str, key: str, *, timeout: float = FETCH_TIMEOUT) -> str:
    """Collect the offer a code points at."""
    got = _call(f"{base}/api/signal/offer?key={key}", None, timeout)
    offer = str(got.get("offer") or "")
    if not offer:
        raise RendezvousError("that game is not there any more — the code may "
                              "have expired, or the other player closed it")
    return offer


def send_answer(base: str, key: str, answer: str, *,
                timeout: float = FETCH_TIMEOUT) -> None:
    """Hand our answer back to the machine that published the offer."""
    got = _call(f"{base}/api/signal/answer", {"key": key, "answer": answer},
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
