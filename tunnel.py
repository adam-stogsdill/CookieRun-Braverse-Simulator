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
- **ngrok** (`brew install ngrok`) — needs a free account and an authtoken, so
  it is the fallback rather than the default. The token can come from ngrok's
  own store (`ngrok config add-authtoken …`), from `$NGROK_AUTHTOKEN`, or from
  `--ngrok-authtoken`; see `resolve_token`. It is passed to the client in its
  environment and never on its command line, because arguments are readable by
  anyone with `ps` and end up in shell history.

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

import os
import re
import shutil
import sys
import stat
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
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


# ---------------------------------------------------------------------------
# the ngrok authtoken
# ---------------------------------------------------------------------------
# ngrok will not open a tunnel for an account it cannot identify, and unlike
# cloudflared's quick tunnels there is no anonymous mode to fall back on. The
# token is what identifies the account.
#
# There are three places one can come from, and they are tried in this order:
# an explicit argument, this environment variable, and a file this app wrote.
# The fourth — `ngrok config add-authtoken`, ngrok's own store — needs nothing
# from us at all, because the client reads it by itself; a machine already set
# up that way keeps working and never touches any of this.
AUTHTOKEN_ENV = "NGROK_AUTHTOKEN"

# Where the app keeps a token it was asked to remember. In the *home*
# directory, deliberately, and never beside the game the way decklists are: a
# build gets zipped up and handed to people, and a credential that travels with
# it is a credential that has been given away.
TOKEN_FILE = "ngrok.token"

AUTH_HINT = (
    "ngrok refused the connection because it has no usable authtoken. Get one "
    "free from https://dashboard.ngrok.com/get-started/your-authtoken, then "
    "either:\n"
    "    ngrok config add-authtoken <token>       (once, ngrok's own store)\n"
    "    python play_server.py --ngrok-authtoken <token> --save-ngrok-authtoken\n"
    "cloudflared needs no account at all, if you would rather not have one."
)

# What ngrok says when the token is missing, wrong or for a dead account. The
# code is the reliable half — the prose around it changes between versions.
AUTH_FAILURE = re.compile(
    r"ERR_NGROK_(?:105|107|108|4018)|authentication failed|"
    r"authtoken.{0,40}(?:invalid|required|missing)",
    re.I)


def token_path() -> Path:
    return Path.home() / ".braverse" / TOKEN_FILE


def read_token() -> str:
    """The remembered token, or "" — never raises, whatever the file is."""
    try:
        return token_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def resolve_token(explicit: str = "") -> str:
    """The token to use, from the first place that has one."""
    return (explicit or "").strip() or os.environ.get(AUTHTOKEN_ENV, "").strip() \
        or read_token()


def save_token(token: str) -> Path:
    """Remember a token for later runs, readable only by this user.

    The mode is set on the *handle* rather than after writing, so there is no
    window in which the file exists and is world-readable. Windows ignores the
    mode and gets its protection from the profile directory instead, which is
    the same trade every other dotfile on that platform makes.
    """
    token = (token or "").strip()
    if not token:
        raise ValueError("no token to save")
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                     stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(token + "\n")
    return path


def forget_token() -> bool:
    """Drop the remembered token. True if there was one."""
    try:
        token_path().unlink()
        return True
    except OSError:
        return False


# The same requirement again, for a settings screen rather than a terminal.
# `INSTALL_HINT` is written for someone who typed `--online` and can be told
# about `--lan`; neither word means anything to somebody who opened the game by
# double-clicking it, so this says the thing itself instead of the flag.
SETUP_HINT = (
    "Playing someone over the internet needs one small program on this "
    "computer. Either will do:\n"
    "cloudflared — brew install cloudflared (no account needed)\n"
    "ngrok — brew install ngrok (free account)\n"
    "Already installed one? Close and reopen this screen — it is checked "
    "afresh each time.\n"
    "Someone on your own network can already join without it."
)


# ---------------------------------------------------------------------------
# doing the setup, rather than describing it
# ---------------------------------------------------------------------------
# What an ngrok authtoken is allowed to look like. Checked before the value is
# ever put in an argument list — the token comes from a text field in a
# browser, and `ngrok config add-authtoken <value>` would otherwise be a way to
# hand this machine an extra argument of somebody's choosing. Nothing is run
# through a shell either, so this is a second lock on a door that is already
# shut, which is the right number of locks for a credential field.
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]{8,256}$")

# Installing is done by the platform's own package manager and nothing else.
# These are fixed commands: no part of them comes from the browser, which only
# ever names *which* client it wants, and that name is checked against the
# backends we know. Anything not covered here gets sent to the official
# download page instead — this will not fetch a binary from anywhere itself.
# Which clients this will install for you. playit is deliberately absent: its
# package-manager name is not something I could verify, and a wrong `brew
# install` is worse than a link — it fails in a way that looks like the app is
# broken. Its own download page runs a setup wizard anyway, which is the better
# road for the client that needs an account and a tunnel configured on it.
INSTALLABLE = ("cloudflared", "ngrok")

INSTALLERS = {
    "darwin": {
        "tool": "brew",
        "argv": lambda client: ["brew", "install", client],
    },
    "win32": {
        "tool": "winget",
        "argv": lambda client: [
            "winget", "install", "--exact", "--silent",
            "--accept-package-agreements", "--accept-source-agreements",
            "--id", {"ngrok": "ngrok.ngrok",
                     "cloudflared": "Cloudflare.cloudflared"}[client]],
    },
}

DOWNLOAD_PAGES = {
    "playit": "https://playit.gg/download",
    "ngrok": "https://ngrok.com/download",
    "cloudflared": ("https://developers.cloudflare.com/cloudflare-one/"
                    "connections/connect-networks/downloads/"),
}

TOKEN_PAGE = "https://dashboard.ngrok.com/get-started/your-authtoken"

# playit points a tunnel configured on your account at a port on this machine,
# rather than being told a port on the command line the way the other two are.
# So the port it is pointed at has to be one that does not move between runs —
# everywhere else we let the OS pick, which would leave the tunnel aimed at
# last night's port.
PLAYIT_LOCAL_PORT = 8071

PLAYIT_SETUP_HINT = (
    "playit needs two things done once, on your account rather than here:\n"
    f"1. run `playit setup` and follow the claim link it prints\n"
    f"2. add a TCP tunnel on playit.gg pointed at 127.0.0.1:{PLAYIT_LOCAL_PORT}\n"
    "After that this starts it for you and finds the address itself."
)


def installer(client: str) -> Optional[list]:
    """The command that would install ``client`` here, or None.

    None means this machine has no package manager we know how to drive, and
    the honest answer is the download page rather than fetching something
    ourselves.
    """
    if client not in INSTALLABLE:
        return None
    plan = INSTALLERS.get(sys.platform)
    if plan is None:
        return None
    tool = client_path(plan["tool"])
    if not tool:
        return None
    # Same problem, same fix: `brew` is in /opt/homebrew/bin, which a
    # double-clicked app does not have on its PATH either.
    argv = plan["argv"](client)
    return [tool] + argv[1:]


class Job:
    """One long command, run on a thread and pollable while it runs.

    Installing takes minutes, which is far too long to hold an HTTP request
    open for — so the browser starts one of these and then asks how it is
    going. The output is kept because an install that fails says why, and
    "it did not work" is not something anybody can act on.

    Never given anything from the browser: the argv comes from `installer`,
    which builds it from fixed strings.
    """

    LIMIT = 200                     # lines kept; an install is chattier than this

    def __init__(self, argv: list):
        self.argv = list(argv)
        self.lines: deque[str] = deque(maxlen=self.LIMIT)
        self.code: Optional[int] = None
        self.error = ""
        self.lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "Job":
        self._thread.start()
        return self

    def _run(self) -> None:
        try:
            proc = subprocess.Popen(
                self.argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
        except OSError as exc:
            with self.lock:
                self.error, self.code = f"could not run {self.argv[0]}: {exc}", -1
            return
        assert proc.stdout is not None
        for line in proc.stdout:
            with self.lock:
                self.lines.append(line.rstrip())
        with self.lock:
            self.code = proc.wait()

    @property
    def running(self) -> bool:
        with self.lock:
            return self.code is None

    def poll(self) -> dict:
        with self.lock:
            return {
                "running": self.code is None,
                "ok": self.code == 0,
                "code": self.code,
                "error": self.error,
                "command": " ".join(self.argv),
                "output": "\n".join(self.lines),
            }


def configure_token(token: str, timeout: float = 30.0) -> str:
    """Hand the token to ngrok's own store. "" on success, else why not.

    This is what `ngrok config add-authtoken` does, run for the player instead
    of asked of them. Afterwards ngrok finds the token by itself, for every
    tunnel, forever — including ones opened by anything else on this machine —
    which is why it is worth doing rather than only keeping our own copy.

    The token does spend a moment as a command-line argument, which is the one
    place this file otherwise refuses to put it: ngrok offers no way to pass it
    on stdin. It is a second or two rather than the life of the process, and it
    is the same exposure the documented manual command has.
    """
    token = (token or "").strip()
    if not TOKEN_RE.match(token):
        return ("that does not look like an ngrok authtoken — it should be one "
                "unbroken string of letters, digits, dashes and underscores")
    exe = client_path("ngrok")
    if not exe:
        return "ngrok is not installed on this computer yet"
    try:
        done = subprocess.run([exe, "config", "add-authtoken", token],
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not run ngrok: {exc}"
    if done.returncode == 0:
        return ""
    # ngrok's own complaint is the useful part, but it may quote the token back.
    why = (done.stderr or done.stdout or "").strip().replace(token, "<authtoken>")
    return why or f"ngrok refused it (exit {done.returncode})"


def configured() -> bool:
    """Whether ngrok already has a token of its own.

    Read rather than guessed: `ngrok config check` is the client telling us
    about its own store, which is the only thing that actually knows.
    """
    exe = client_path("ngrok")
    if not exe:
        return False
    try:
        done = subprocess.run([exe, "config", "check"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and "valid" in (done.stdout or "").lower()


def status(explicit: str = "") -> dict:
    """What this machine can do about tunnels, for the settings screen.

    Everything the UI needs to decide what to show and nothing it does not:
    **the token itself is never in here.** A browser has no use for it — it can
    only ever set one — and a value that is never sent cannot be read out of a
    page, logged by a proxy, or left in a screenshot.

    ``needsToken`` is about the client that would actually be *used*, not about
    ngrok being installed: a machine with cloudflared reaches for that first and
    needs no account at all, so asking it for a token would be asking for
    something that will never be looked at.
    """
    backend = find_backend()
    name = backend.name if backend else ""
    needs = name == "ngrok"
    wanted = read_preference()
    return {
        "client": name,
        # What was asked for, what is actually installed, and — when those
        # disagree — the fact that they do. Silently using something other than
        # the thing someone picked is the failure this reports rather than has.
        "prefer": wanted,
        "installed": [b.name for b in BACKENDS if client_path(b.name)],
        "choices": [b.name for b in BACKENDS],
        "preferMissing": bool(wanted and wanted != name),
        "scheme": backend.scheme if backend else "",
        # playit cannot be set up from here at all: the claim and the tunnel
        # both live on your account. So the screen says what to go and do
        # rather than offering a button that cannot do it.
        "setup": PLAYIT_SETUP_HINT if name == "playit" else "",
        "needsToken": needs,
        # Whether a token would be found *right now*, from anywhere — including
        # ngrok's own store, which we cannot see and do not need to.
        "haveToken": bool(resolve_token(explicit)) if needs else False,
        "savedToken": bool(read_token()),
        "fromEnv": bool(os.environ.get(AUTHTOKEN_ENV, "").strip()),
        "install": "" if name else SETUP_HINT,
    }


class TunnelError(RuntimeError):
    """The client was there but never produced a usable address."""


@dataclass(frozen=True)
class Backend:
    name: str
    # The address turns up in a log line among many; this is how we know it.
    url_re: Pattern[str]
    # What the address it hands back speaks. Only playit is not https, and it
    # says so here rather than anywhere having to special-case a name.
    scheme: str = "https"

    def argv(self, port: int) -> list[str]:
        # The resolved path, not the bare name: the whole reason `client_path`
        # exists is that `PATH` may not mention this, and launching by name
        # would then fail for exactly the reason finding it did.
        exe = client_path(self.name) or self.name
        if self.name == "cloudflared":
            return [exe, "tunnel", "--no-autoupdate",
                    "--url", f"http://127.0.0.1:{port}"]
        if self.name == "playit":
            # `--stdout` is what makes the address readable at all: by default
            # the agent draws a full-screen TUI and logs nowhere we can see,
            # the same trap ngrok sets. The tunnel itself is configured on the
            # account rather than on the command line, which is why there is no
            # port here — see `PLAYIT_SETUP_HINT`.
            return [exe, "--stdout"]
        # `--log stdout` is what makes the address readable at all; by default
        # ngrok draws a full-screen terminal UI and logs nowhere we can see.
        return [exe, "http", str(port), "--log", "stdout"]

    def environ(self, token: str) -> Optional[dict]:
        """The child's environment, with the authtoken in it if there is one.

        ngrok takes `--authtoken` on the command line too, and this
        deliberately does not use it: arguments are world-readable in `ps` on
        every machine with other people on it, and they land in shell history
        the moment anyone copies the command. An environment variable is not
        secret either, but it is only visible to this user, which is the
        difference that matters. `NGROK_AUTHTOKEN` is ngrok's own variable, so
        nothing has to be taught to read it.
        """
        if not token or self.name != "ngrok":
            return None            # inherit ours unchanged
        return {**os.environ, AUTHTOKEN_ENV: token}


BACKENDS = (
    Backend("cloudflared", re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")),
    Backend("ngrok", re.compile(r"https://[A-Za-z0-9-]+\.ngrok(?:-free)?\.(?:app|io|dev)")),
    # playit.gg. Free tunnels there are TCP with a `host:port` address and no
    # TLS — HTTPS is a paid feature — so this is the one backend whose address
    # is not `https://`, and it is only usable because what crosses a signalling
    # tunnel is sealed before it is handed over (`rendezvous.seal_signal`).
    # Worth having anyway: it is built for people behind CGNAT, where the other
    # two are exactly the situation that fails.
    Backend("playit", re.compile(r"\b((?:[a-z0-9-]+\.)+(?:ply\.gg|playit\.gg)"
                                 r"|[a-z0-9-]+\.gl\.at\.ply\.gg):(\d{2,5})\b"),
            scheme="http"),
)


# Which client to reach for when more than one is installed. Empty means the
# order in `BACKENDS`, which puts cloudflared first because it is the only one
# that needs no account, no token and nothing configured anywhere. A stored
# value overrides that — someone behind CGNAT may have installed playit
# precisely because cloudflared is what fails for them, and before this there
# was no way to say so.
PREFERENCE_FILE = "tunnel.pref"


def preference_path() -> Path:
    return Path.home() / ".braverse" / PREFERENCE_FILE


def read_preference() -> str:
    """The chosen client, or "" for automatic. Never raises."""
    try:
        name = preference_path().read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""
    return name if name in {b.name for b in BACKENDS} else ""


def save_preference(name: str) -> str:
    """Remember which client to prefer. "" (or anything unknown) means auto."""
    name = (name or "").strip().lower()
    if name not in {b.name for b in BACKENDS}:
        name = ""
    path = preference_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not name:
        try:
            path.unlink()
        except OSError:
            pass
        return ""
    path.write_text(name + "\n", encoding="utf-8")
    return name


# Where a client can be even though `PATH` does not mention it.
#
# This is not defensiveness, it is the common case. A process launched from
# Finder, the Dock or a double-clicked build gets `PATH=/usr/bin:/bin:...` and
# nothing else — no `/opt/homebrew/bin`, which is where Homebrew puts things on
# every Apple Silicon Mac. So the game, which is *meant* to be double-clicked,
# would report "no tunnel client found" to somebody who had just installed one
# and watched it work in their terminal. Windows has the same shape: winget
# drops shims in a per-user directory that a fresh process may not have picked
# up yet.
EXTRA_BINS = {
    "darwin": ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin",
               "~/.local/bin"],
    "linux": ["/usr/local/bin", "/usr/bin", "/snap/bin", "~/.local/bin"],
    "win32": [r"~\AppData\Local\Microsoft\WinGet\Links",
              r"~\AppData\Local\Programs",
              r"~\AppData\Local\playit_gg\bin",
              r"C:\Program Files\cloudflared",
              r"C:\Program Files (x86)\cloudflared"],
}


def client_path(name: str) -> Optional[str]:
    """The full path to ``name``, looking past `PATH` when it comes up empty.

    Returns a path rather than a boolean because the answer is then used to
    *run* the thing: launching by bare name would fail for exactly the same
    reason finding it did.
    """
    found = shutil.which(name)
    if found:
        return found
    exts = [".exe", ".cmd", ".bat", ""] if sys.platform == "win32" else [""]
    for folder in EXTRA_BINS.get(sys.platform, []):
        base = Path(folder).expanduser()
        for ext in exts:
            candidate = base / (name + ext)
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def find_backend(prefer: str = "") -> Optional[Backend]:
    """The tunnel client to use: the preferred one if it is here, else the
    first on PATH in `BACKENDS` order.

    A preference that is set but not installed does **not** stop everything —
    it falls back and `status` reports the gap, so the screen can say "you
    asked for ngrok and it is not installed" rather than the game simply
    refusing with no explanation.
    """
    prefer = (prefer or "").strip().lower() or read_preference()
    if prefer:
        for backend in BACKENDS:
            if backend.name == prefer and client_path(backend.name):
                return backend
    for backend in BACKENDS:
        if client_path(backend.name):
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

    def __init__(self, backend: Backend, proc: subprocess.Popen,
                 secret: str = ""):
        self.backend = backend
        self.proc = proc
        # Never printed, only compared against: a failing run quotes the
        # client's last lines back to the user, and a client that ever echoed
        # its own token would otherwise put it in an error message, a bug
        # report and a screenshot in one go.
        self._secret = (secret or "").strip()
        self.url: str = ""
        self._lines: deque[str] = deque(maxlen=40)
        self._found = threading.Event()
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._lines.append(self._scrub(line.rstrip()))
            if not self.url:
                found = self.backend.url_re.search(line)
                if found:
                    # cloudflared and ngrok print a whole `https://…`; playit
                    # prints a bare `host:port`, so the scheme comes from the
                    # backend rather than from whatever the client felt like
                    # writing.
                    got = found.group(0)
                    self.url = (got if "://" in got
                                else f"{self.backend.scheme}://{got}")
                    self._found.set()
        # The client exited. Anyone still waiting on an address is waiting for
        # something that is never coming.
        self._found.set()

    def _scrub(self, line: str) -> str:
        return line.replace(self._secret, "<authtoken>") if self._secret else line

    @property
    def host(self) -> str:
        """The bare hostname, for the Host header the public port will see."""
        return urlsplit(self.url).hostname or ""

    @property
    def authority(self) -> str:
        """Host and port together, which is how a `host:port` tunnel is asked."""
        split = urlsplit(self.url)
        return split.netloc or ""

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
        # An unauthenticated ngrok exits at once and says so, which is a
        # different problem with a different fix than "it went quiet" — and the
        # one a first-time user is overwhelmingly likely to hit.
        if self.backend.name == "ngrok" \
                and any(AUTH_FAILURE.search(line) for line in self._lines):
            raise TunnelError(AUTH_HINT)
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


def open_tunnel(port: int, timeout: float = STARTUP_TIMEOUT,
                authtoken: str = "", prefer: str = "") -> Tunnel:
    """Start a tunnel to loopback ``port`` and return it once it has an address.

    Raises `TunnelError` if no client is installed, or if the one that is never
    reports an address — the caller can then go back to serving locally instead
    of printing an invite link that leads nowhere.
    """
    backend = find_backend(prefer)
    if backend is None:
        raise TunnelError(INSTALL_HINT)
    # Only ngrok has anything to authenticate with; cloudflared quick tunnels
    # are anonymous, which is why they are still the default.
    token = resolve_token(authtoken) if backend.name == "ngrok" else ""
    try:
        proc = subprocess.Popen(
            backend.argv(port),
            env=backend.environ(token),
            stdout=subprocess.PIPE,
            # Both clients log to stderr as readily as to stdout; folding them
            # together means one regex over one stream finds the address
            # wherever this version decided to print it.
            stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    except OSError as exc:
        raise TunnelError(f"could not run {backend.name}: {exc}") from exc
    tunnel = Tunnel(backend, proc, secret=token)
    try:
        tunnel.wait(timeout)
    except TunnelError:
        tunnel.close()
        raise
    return tunnel
