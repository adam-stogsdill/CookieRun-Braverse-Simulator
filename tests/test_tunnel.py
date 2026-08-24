"""Reading a public address out of a tunnel client's logs.

Parsing logs is the only interface either client offers for "what did I just
get?", which makes it the part most likely to rot when one of them changes its
output. So the patterns are pinned against real banner lines, and the failure
path — a client that starts and never says anything useful — is pinned too: an
invite link that leads nowhere is worse than being told no.

Nothing here installs or runs cloudflared. `Tunnel` is handed a subprocess that
prints a canned banner, which exercises the same draining thread, the same
regex and the same `wait` as the real thing.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

import tunnel as TUN


def speaking(lines: str) -> subprocess.Popen:
    """A process that prints `lines` the way a tunnel client would, then waits."""
    return subprocess.Popen(
        [sys.executable, "-c",
         "import sys,time;sys.stdout.write(sys.argv[1]);sys.stdout.flush();"
         "time.sleep(30)", lines],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)


CLOUDFLARED = """\
2026-08-24T12:00:00Z INF Thank you for trying Cloudflare Tunnel.
2026-08-24T12:00:00Z INF +----------------------------------------------------+
2026-08-24T12:00:00Z INF |  Your quick Tunnel has been created! Visit it at:   |
2026-08-24T12:00:00Z INF |  https://brave-cookie-runs-fast.trycloudflare.com   |
2026-08-24T12:00:00Z INF +----------------------------------------------------+
"""

NGROK = """\
t=2026-08-24T12:00:00+0000 lvl=info msg="starting web service"
t=2026-08-24T12:00:00+0000 lvl=info msg="started tunnel" name=command_line \
addr=http://localhost:8080 url=https://a1b2c3d4.ngrok-free.app
"""


@pytest.mark.parametrize("name,logs,expected", [
    ("cloudflared", CLOUDFLARED, "https://brave-cookie-runs-fast.trycloudflare.com"),
    ("ngrok", NGROK, "https://a1b2c3d4.ngrok-free.app"),
])
def test_the_address_is_found_in_the_clients_own_banner(name, logs, expected):
    backend = next(b for b in TUN.BACKENDS if b.name == name)
    link = TUN.Tunnel(backend, speaking(logs))
    try:
        assert link.wait(timeout=10) == expected
        # The hostname is what the public listener will be asked by, and so what
        # its Host check has to be told to answer to.
        assert link.host == expected.split("//")[1]
    finally:
        link.close()


def test_a_client_that_says_nothing_useful_fails_loudly():
    backend = TUN.BACKENDS[0]
    link = TUN.Tunnel(backend, speaking("ERR failed to connect to the edge\n"))
    try:
        with pytest.raises(TUN.TunnelError) as raised:
            link.wait(timeout=1.0)
        # The client's own last words go in the message: "it didn't work" is not
        # something anyone can act on.
        assert "failed to connect to the edge" in str(raised.value)
    finally:
        link.close()


def test_a_client_that_exits_is_not_waited_out():
    """No point sitting out the full timeout for a process that is already gone."""
    link = TUN.Tunnel(TUN.BACKENDS[0],
                      subprocess.Popen([sys.executable, "-c", "pass"],
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True))
    with pytest.raises(TUN.TunnelError, match="exited immediately"):
        link.wait(timeout=30.0)     # returns long before this


def test_close_is_safe_twice_and_after_the_client_has_gone():
    link = TUN.Tunnel(TUN.BACKENDS[0], speaking(CLOUDFLARED))
    link.wait(timeout=10)
    link.close()
    assert not link.alive
    link.close()


def test_a_lookalike_hostname_is_not_a_tunnel_address():
    """The pattern is the only thing standing between us and a log line's URL."""
    backend = TUN.BACKENDS[0]
    assert not backend.url_re.search("https://trycloudflare.com.evil.example")
    assert not backend.url_re.search("http://plain.trycloudflare.com")


def test_the_client_is_asked_to_log_where_we_can_read_it():
    """ngrok's default is a full-screen UI that logs nowhere we can see."""
    ngrok = next(b for b in TUN.BACKENDS if b.name == "ngrok")
    assert "--log" in ngrok.argv(8080)
    # And cloudflared must be pointed at loopback, never at every interface.
    cloudflared = next(b for b in TUN.BACKENDS if b.name == "cloudflared")
    assert "http://127.0.0.1:8080" in cloudflared.argv(8080)
