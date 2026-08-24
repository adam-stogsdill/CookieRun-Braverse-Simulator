#!/usr/bin/env python3
"""Put the visual player on a public URL, so a room can be played over the net.

`play_server.py --online` serves the game exactly as it always did and this
module only decides how someone *off this network* reaches it: it runs a tunnel
client, which dials **out** to a provider and gets handed back an
`https://…` address that forwards to a loopback port here. That is the whole
reason to do it this way — nothing has to be forwarded on the router, no port is
opened on the machine, and TLS is the provider's problem rather than ours, so
the seat token never crosses the internet in the clear.

Two clients, best first:

- **cloudflared** (`brew install cloudflared`, or Cloudflare's download page) —
  its *quick tunnel* mode needs no account, no login and no config file, which
  is the only reason a feature like this can be a flag rather than a signup.
  The address it hands back lasts as long as the process.
- **ngrok** (`brew install ngrok`) — needs a free account and one
  `ngrok config add-authtoken …`, so it is the fallback rather than the default.

Nothing here raises merely because neither is installed: `available()` answers
that question up front and `main` can print `INSTALL_HINT` and keep serving
locally, the same shape `desktop.py` uses for a machine that cannot draw a
window.

The address is parsed out of the client's own logs, which is unlovely but is the
only interface either one offers for "what did I just get?". A run that prints
something we cannot recognise fails loudly with its last lines attached, rather
than handing back a URL that does not work.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Pattern
from urllib.parse import urlsplit

# How long to wait for the client to report its address. A cold cloudflared
# takes a couple of seconds to pick an edge; ngrok is usually quicker.
STARTUP_TIMEOUT = 30.0

# The same requirement, said for the other thing that needs a tunnel. Playing
# someone directly needs a client on *one* machine only — whoever starts the
# game — which is worth saying, because "you both need to install something"
# would be a much bigger ask and is not the ask.
PEER_HINT = (
    "playing someone directly needs a tunnel client on this computer, so the "
    "other player has somewhere to collect the invitation from. Only you need "
    "it; they need nothing.\n"
    "    cloudflared   brew install cloudflared      (no account needed)\n"
    "    ngrok         brew install ngrok            (free account, one-time "
    "`ngrok config add-authtoken …`)"
)

INSTALL_HINT = (
    "no tunnel client found — --online needs one of:\n"
    "    cloudflared   brew install cloudflared      (no account needed)\n"
    "    ngrok         brew install ngrok            (free account, one-time "
    "`ngrok config add-authtoken …`)\n"
    "Without one, --lan still lets someone on this network join."
)


class TunnelError(RuntimeError):
    """The client was there but never produced a usable address."""


@dataclass(frozen=True)
class Backend:
    name: str
    # The address turns up in a log line among many; this is how we know it.
    url_re: Pattern[str]

    def argv(self, port: int) -> list[str]:
        if self.name == "cloudflared":
            return [self.name, "tunnel", "--no-autoupdate",
                    "--url", f"http://127.0.0.1:{port}"]
        # `--log stdout` is what makes the address readable at all; by default
        # ngrok draws a full-screen terminal UI and logs nowhere we can see.
        return [self.name, "http", str(port), "--log", "stdout"]


BACKENDS = (
    Backend("cloudflared", re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")),
    Backend("ngrok", re.compile(r"https://[A-Za-z0-9-]+\.ngrok(?:-free)?\.(?:app|io|dev)")),
)


def find_backend() -> Optional[Backend]:
    """The first tunnel client on PATH, in preference order."""
    for backend in BACKENDS:
        if shutil.which(backend.name):
            return backend
    return None


def available() -> bool:
    """Whether this machine can open a tunnel at all."""
    return find_backend() is not None


class Tunnel:
    """A running tunnel client and the address it is forwarding.

    The child's output is drained on a thread whether or not anyone is reading
    it: both clients keep logging for the life of the tunnel, and a pipe nobody
    empties fills up and blocks the process that is carrying our traffic.
    """

    def __init__(self, backend: Backend, proc: subprocess.Popen):
        self.backend = backend
        self.proc = proc
        self.url: str = ""
        self._lines: deque[str] = deque(maxlen=40)
        self._found = threading.Event()
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._lines.append(line.rstrip())
            if not self.url:
                found = self.backend.url_re.search(line)
                if found:
                    self.url = found.group(0)
                    self._found.set()
        # The client exited. Anyone still waiting on an address is waiting for
        # something that is never coming.
        self._found.set()

    @property
    def host(self) -> str:
        """The bare hostname, for the Host header the public port will see."""
        return urlsplit(self.url).hostname or ""

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def wait(self, timeout: float = STARTUP_TIMEOUT) -> str:
        """Block until the address is known. Raises `TunnelError` if it isn't."""
        deadline = time.time() + timeout
        while not self.url and time.time() < deadline:
            self._found.wait(min(0.25, max(0.0, deadline - time.time())))
            if not self.alive:
                break
        if self.url:
            return self.url
        why = ("exited immediately" if not self.alive
               else f"printed no address in {timeout:.0f}s")
        tail = "\n".join(f"    {line}" for line in list(self._lines)[-8:])
        raise TunnelError(
            f"{self.backend.name} {why}."
            + (f" Its last output was:\n{tail}" if tail else ""))

    def close(self) -> None:
        """Take the tunnel down. Safe to call twice, and never raises."""
        if self.proc.poll() is not None:
            return
        try:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        except OSError:
            pass


def open_tunnel(port: int, timeout: float = STARTUP_TIMEOUT) -> Tunnel:
    """Start a tunnel to loopback ``port`` and return it once it has an address.

    Raises `TunnelError` if no client is installed, or if the one that is never
    reports an address — the caller can then go back to serving locally instead
    of printing an invite link that leads nowhere.
    """
    backend = find_backend()
    if backend is None:
        raise TunnelError(INSTALL_HINT)
    try:
        proc = subprocess.Popen(
            backend.argv(port),
            stdout=subprocess.PIPE,
            # Both clients log to stderr as readily as to stdout; folding them
            # together means one regex over one stream finds the address
            # wherever this version decided to print it.
            stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    except OSError as exc:
        raise TunnelError(f"could not run {backend.name}: {exc}") from exc
    tunnel = Tunnel(backend, proc)
    try:
        tunnel.wait(timeout)
    except TunnelError:
        tunnel.close()
        raise
    return tunnel
