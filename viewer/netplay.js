/* Peer-to-peer: the other player's machine, reached directly.
 *
 * A room (see `Room` in play_server.py) puts one machine in charge of the game
 * and has the other talk to it over HTTP. This is the other arrangement
 * entirely: both people run the engine, an RTCDataChannel carries the
 * decisions between them, and nobody hosts anything. No port is opened on the
 * router, and the game itself never travels through anyone else's machine.
 *
 * This file is only the wire. It never reads a decision and never decides
 * anything about the game — it moves opaque messages between the data channel
 * and the local engine, which is doing the actual playing. That split is why
 * the interesting half is testable without a browser: everything that could be
 * wrong about the protocol lives in `braverse/netplay.py`, and everything here
 * is plumbing that either connects or visibly does not.
 *
 * **A peer game is not private from your opponent.** Both machines run the
 * rules, so both hold the whole game state, your hand included; the hiding is
 * done by the renderer, which is on their computer too. A modified client can
 * read it. The room mode is the one that is safe against the person you are
 * playing — this one is safe against the network, which is a different and
 * smaller claim, and the UI says so rather than letting anyone assume more.
 *
 * Arranging the game is the one part that cannot be peer-to-peer, because two
 * browsers that have never met cannot describe themselves to each other out of
 * nothing. The host therefore publishes its offer behind its own tunnel and
 * the code says where to collect it — see `rendezvous.py`. That lasts only the
 * seconds it takes the two browsers to find each other; from then on the
 * tunnel is not in the picture, and could be shut down without the game
 * noticing. The player never sees a session description at all. */

/* Public STUN, needed only to discover how this machine looks from outside a
 * NAT. It carries no game data and learns nothing but that an address asked.
 * Two people on the same network do not need it at all, which is why a failure
 * to reach it is not fatal here. */
const PEER_ICE = [{ urls: "stun:stun.l.google.com:19302" }];

const Peer = {
  pc: null,
  channel: null,
  seat: null,
  on: false,
  status: "",
  key: "",            // the rendezvous key our offer was published under
  playing: false,     // the engine has a match, not just a lobby waiting
  watching: null,     // the setting-up watchdog, while there is no pump yet
  inbox: [],          // messages from the channel, waiting to be posted inward
  flushing: false,
  pumping: false,

  /* -- talking to our own engine ---------------------------------------- */

  /** `api`, with the failure modes said in words a player can act on.
   *
   * A bare "Failed to fetch" is the browser's way of saying it could not reach
   * the server *at all*, and letting that string reach the screen tells nobody
   * anything: the commonest causes are the local server having stopped and the
   * page being an old tab pointed at a port nothing is listening on any more,
   * and neither is guessable from those three words. A page that loaded but
   * cannot POST is almost always one of those two.
   *
   * The other case worth separating is a reply that is not JSON, which means
   * something answered but it was not this app — an older build without these
   * routes, or another server on the port. */
  async reach(path, body) {
    let res;
    try {
      const opts = body === undefined ? {} : {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      };
      res = await fetch(path, opts);
    } catch (err) {
      throw new Error("cannot reach the game on this computer — is the server "
                      + "still running, and is this page pointed at it? "
                      + `(${location.origin})`);
    }
    try {
      return await res.json();
    } catch (err) {
      throw new Error(`${location.origin} answered ${res.status} but not in a `
                      + "form this app understands — it may be an older build, "
                      + "or a different server on that port");
    }
  },

  /* -- the connection --------------------------------------------------- */

  /** Wait until every candidate is in the description we are about to show.
   *
   * The alternative — trickle ICE — means a second channel to carry the later
   * candidates on, and there is no second channel here: the offer is published
   * once, whole. Gathering fully up front is what makes that possible, at the
   * cost of a couple of seconds before the code appears. */
  settled(pc) {
    if (pc.iceGatheringState === "complete") return Promise.resolve();
    return new Promise((resolve) => {
      // A candidate server that never answers must not hang the dialog
      // forever; what has been gathered by then is usually enough on a LAN.
      const done = setTimeout(resolve, 4000);
      pc.addEventListener("icegatheringstatechange", () => {
        if (pc.iceGatheringState === "complete") { clearTimeout(done); resolve(); }
      });
    });
  },

  fresh() {
    Peer.teardown();
    const pc = new RTCPeerConnection({ iceServers: PEER_ICE });
    pc.addEventListener("connectionstatechange", () => {
      if (["failed", "disconnected", "closed"].includes(pc.connectionState)) {
        Peer.note("the connection to the other player dropped");
      }
    });
    Peer.pc = pc;
    return pc;
  },

  /** Wire up a channel — however far along it already is.
   *
   * The `open` event is not something to wait for, because it may already have
   * happened. The joiner takes its channel from `ondatachannel`, and that
   * fires with the channel *already open* often enough to be the normal case
   * rather than a race worth ignoring — at which point an `open` listener is
   * waiting for an event that will never come again.
   *
   * That was a real bug, and an unusually quiet one: the joiner's engine sends
   * its `hello` the moment the seat is created, so it is already sitting in
   * the outbox. Without a pump it stays there. The host's own channel opens
   * perfectly, reports "connected", and then waits forever for a message that
   * never left the other machine — so the side that *looks* broken is the side
   * that is working. Hence starting the pump on state, not on an event. */
  adopt(channel) {
    Peer.channel = channel;
    channel.addEventListener("message", (event) => {
      try {
        Peer.inbox.push(JSON.parse(event.data));
      } catch (err) {
        return;      // not ours; the engine would reject it anyway
      }
      Peer.flush();
    });
    channel.addEventListener("open", () => Peer.live());
    channel.addEventListener("close", () => Peer.note("the other player left"));
    if (channel.readyState === "open") Peer.live();
  },

  /** The channel is usable: say so once, and start carrying messages. */
  live() {
    if (Peer.channel && Peer.channel.readyState !== "open") return;
    if (!Peer.playing) Peer.note("connected");
    Peer.pump();
  },

  /* -- the pump --------------------------------------------------------- */

  /** Messages from the channel, handed to the local engine.
   *
   * Batched behind a microtask rather than posted one at a time: a single
   * decision often arrives as a burst, and order is preserved either way
   * because the array is drained whole and the server queues it in order.
   * Order is not cosmetic here — the decision stream *is* the game. */
  async flush() {
    if (Peer.flushing || !Peer.inbox.length) return;
    Peer.flushing = true;
    try {
      while (Peer.inbox.length) {
        const batch = Peer.inbox.splice(0, Peer.inbox.length);
        const res = await Peer.reach("/api/peer/in", { msgs: batch });
        if (res && res.gone) { Peer.note("this game is over"); return; }
        if (res && res.peer) Peer.report(res.peer);
      }
    } finally {
      Peer.flushing = false;
    }
  },

  /** Messages the local engine wants sent, carried out to the channel.
   *
   * A long-held GET rather than a timer: the reply comes back the instant our
   * seat answers a question, so the other player waits on the network and not
   * on a polling interval. */
  async pump() {
    if (Peer.pumping) return;
    Peer.pumping = true;
    try {
      while (Peer.on && Peer.channel && Peer.channel.readyState === "open") {
        const res = await Peer.reach("/api/peer/out");
        if (!res || res.gone) break;
        if (res.peer) Peer.report(res.peer);
        for (const message of res.msgs || []) {
          if (Peer.channel.readyState !== "open") break;
          Peer.channel.send(JSON.stringify(message));
        }
      }
    } catch (err) {
      Peer.fail(err.message || "lost contact with the game on this computer");
    } finally {
      Peer.pumping = false;
    }
  },

  /* -- starting a game --------------------------------------------------- */

  /** Host: publish an offer behind this machine's tunnel, and name the code.
   *
   * The player never sees a session description. The tunnel is opened on
   * demand — no flag to have remembered — and torn down as far as the game is
   * concerned the moment the two browsers are talking, because gameplay does
   * not go through it. All the code carries is where to collect the offer.
   */
  async host(deck, name) {
    const started = await Peer.reach("/api/peer/new", { host: true, deck, name });
    if (started.error) { Peer.fail(started.error); return null; }
    Peer.begin(0);
    const pc = Peer.fresh();
    // Ordered and reliable are the defaults and are load-bearing — `netplay`
    // counts decisions, so a dropped or reordered message is a desync.
    Peer.adopt(pc.createDataChannel("braverse", { ordered: true }));
    await pc.setLocalDescription(await pc.createOffer());
    await Peer.settled(pc);

    const put = await Peer.reach("/api/peer/publish",
                                 { offer: JSON.stringify(pc.localDescription) });
    if (put.error) { Peer.fail(put.error); return null; }
    Peer.key = put.key;
    Peer.waitForJoiner();
    return put.code;
  },

  /** Watch for the other player collecting the offer and answering it.
   *
   * A poll rather than anything cleverer: this runs for the seconds or minutes
   * between sending a code and someone acting on it, exactly once per game,
   * and stops the moment an answer lands. */
  async waitForJoiner() {
    while (Peer.on && Peer.pc && !Peer.pc.currentRemoteDescription) {
      let got;
      try {
        got = await Peer.reach(`/api/peer/answer?key=${encodeURIComponent(Peer.key)}`);
      } catch (err) {
        Peer.fail(err.message || String(err));
        return;
      }
      if (got && got.answer) {
        try {
          await Peer.pc.setRemoteDescription(JSON.parse(got.answer));
          Peer.note("connecting…");
        } catch (err) {
          Peer.fail("the other player's reply did not make sense");
        }
        return;
      }
      await new Promise((done) => setTimeout(done, 1500));
    }
  },

  /** Joiner: one code in, and the rest happens without them.
   *
   * Our own machine collects the offer and hands back the answer, so nothing
   * has to come back to the person who sent the code. */
  async join(code, deck, name) {
    const got = await Peer.reach("/api/peer/collect", { code });
    if (got.error) { Peer.fail(got.error); return false; }

    const started = await Peer.reach("/api/peer/new", { host: false, deck, name });
    if (started.error) { Peer.fail(started.error); return false; }
    Peer.begin(1);
    const pc = Peer.fresh();
    pc.addEventListener("datachannel", (event) => Peer.adopt(event.channel));
    await pc.setRemoteDescription(JSON.parse(got.offer));
    await pc.setLocalDescription(await pc.createAnswer());
    await Peer.settled(pc);

    const sent = await Peer.reach("/api/peer/reply",
                                  { code, answer: JSON.stringify(pc.localDescription) });
    if (sent.error) { Peer.fail(sent.error); return false; }
    Peer.note("connecting…");
    return true;
  },

  /* Watch the local engine while the two browsers are still finding each other.
   *
   * Until the channel opens there is no pump, so nothing is reading what the
   * engine has to say — and that is exactly when it has something worth
   * saying: a deck that will not load, a peer on another build, a handshake
   * that gave up. Without this the dialog sits on a cheerful stale line while
   * the game behind it is already dead. Stops as soon as the pump takes over. */
  watch() {
    if (Peer.watching) return;
    Peer.watching = setInterval(async () => {
      if (!Peer.on || (Peer.channel && Peer.channel.readyState === "open")) {
        clearInterval(Peer.watching);
        Peer.watching = null;
        return;
      }
      try {
        const snap = await Peer.reach("/api/state?peer=1");
        if (snap && snap.peer) Peer.report(snap.peer);
      } catch (err) { /* the poll failing is not itself news */ }
    }, 2000);
  },

  begin(seat) {
    Peer.seat = seat;
    Peer.on = true;
    Peer.playing = false;
    state.peer = true;
    // A peer game is drawn from our own seat, exactly as a room is; nothing
    // below should be comparing a seat to 0.
    if (typeof seatPerspective === "function") seatPerspective(seat);
    Peer.note("waiting for the other player");
    Peer.watch();
  },

  /** What the local engine says about the handshake, for the status line. */
  report(status) {
    if (!status) return;
    if (status.error) { Peer.fail(status.error); return; }
    else if (status.state === "playing" && !Peer.playing) {
      Peer.playing = true;
      Peer.note("playing");
      // The game is on and the board is behind this: nothing on the dialog is
      // of any further use, and leaving it up over a live match was the other
      // half of "it connected but nothing happened". Closing it here rather
      // than in the UI layer because this is the only place that learns the
      // match actually started.
      const dialog = el("#peerplay");
      if (dialog && dialog.open) dialog.close();
    }
  },

  /** The game is not going to happen, and the dialog should stop pretending. */
  fail(message) {
    Peer.note(message);
    const line = el("#peer-status");
    if (line) line.classList.add("bad");
    Peer.on = false;
    state.peer = false;
  },

  /** Whether there is a real game behind the dialog, or only a hopeful lobby.
   *
   * The difference decides what closing the dialog means: stepping away from a
   * match in progress, or abandoning a connection that never happened. */
  connected() {
    return Peer.playing && !!Peer.channel && Peer.channel.readyState === "open";
  },

  note(message) {
    Peer.status = message;
    const line = el("#peer-status");
    if (!line) return;
    line.textContent = message;
    if (Peer.on) line.classList.remove("bad");
  },

  /* -- leaving ----------------------------------------------------------- */

  teardown() {
    if (Peer.channel) { try { Peer.channel.close(); } catch (e) {} }
    if (Peer.pc) { try { Peer.pc.close(); } catch (e) {} }
    Peer.channel = null;
    Peer.pc = null;
  },

  async leave(why) {
    Peer.on = false;
    Peer.playing = false;
    state.peer = false;
    // Whatever is polled next is a different match, so the version counter this
    // one left behind means nothing to it — the same reason a rematch cannot
    // keep counting from the game before it. Reset so the next snapshot is
    // adopted as first sight rather than replayed as a scene.
    state.version = -1;
    state.eventId = 0;
    Peer.teardown();
    Peer.inbox.length = 0;
    try { await Peer.reach("/api/peer/close", { why: why || "left the match" }); } catch (e) {}
  },
};

// The tab closing is the commonest way a peer game ends; telling the other
// side beats leaving them watching a seat that will never move again.
window.addEventListener("pagehide", () => {
  if (Peer.on && Peer.channel && Peer.channel.readyState === "open") {
    try { Peer.channel.send(JSON.stringify({ t: "bye", why: "closed the tab" })); } catch (e) {}
  }
});

/* ---------------------------------------------------------------------------
 * the dialog
 *
 * Two panes of the same shape, because the exchange is symmetrical: one side
 * produces a code and reads a reply, the other reads a code and produces a
 * reply. Neither pane starts a game — the game starts when the channel opens,
 * which is a thing that happens to both machines at once and so cannot be a
 * button on either.
 * ------------------------------------------------------------------------ */
const PeerUI = {
  open() {
    PeerUI.fillDecks();
    PeerUI.showStep(null);
    Peer.note("");
    el("#peerplay").showModal();
  },

  fillDecks() {
    const select = el("#peer-deck");
    if (!select) return;
    select.innerHTML = "";
    (state.config.decks || []).forEach((deck) => {
      const option = h("option", null, `${deck.name} (${deck.size})`);
      option.value = deck.name;
      select.appendChild(option);
    });
    select.value = ((state.config.decks || [])[0] || {}).name || "";
  },

  showStep(which) {
    el("#peer-step-host").classList.toggle("hidden", which !== "host");
    el("#peer-step-guest").classList.toggle("hidden", which !== "guest");
    el("#peer-fix").classList.toggle("hidden", which !== "fix");
    if (!which) return;
    // The dialog is taller than it can show — the warning above is worth its
    // space and is not going anywhere — so the step that just opened has to be
    // scrolled to, or its Copy button sits below the fold and the code looks
    // like something you are meant to select by hand.
    const step = el({ host: "#peer-step-host", guest: "#peer-step-guest",
                      fix: "#peer-fix" }[which]);
    requestAnimationFrame(() => step.scrollIntoView({ block: "end" }));
  },

  deck() { return el("#peer-deck").value; },
  name() { return el("#peer-name").value.trim(); },

  /* Why a step failed, said without blaming the wrong thing.
   *
   * "That code did not work" is right for a code that would not decode, and
   * actively misleading for a server that is not running — it sends someone
   * off to re-copy a code that was never the problem. `Peer.reach` already
   * phrases its own failures for a player, so those are passed through as they
   * are and only a genuine decode failure gets the blame. */
  blame(err, what) {
    const message = String((err && err.message) || err);
    return /cannot reach|answered \d|understands|look like a game code|not there any more|could not reach the other/.test(message)
      ? message : `${what} — ${message}`;
  },

  /* A textarea rather than the clipboard alone: `navigator.clipboard` needs a
   * secure context, and a plain http://localhost page is only sometimes one.
   * The code is always on screen to be selected by hand. */
  async copy(sel) {
    const box = el(sel);
    box.focus();
    box.select();
    box.setSelectionRange(0, box.value.length);   // Safari needs telling twice
    try {
      await navigator.clipboard.writeText(box.value);
      Peer.note(`copied all ${box.value.length} characters — paste the lot`);
      return;
    } catch (err) { /* needs a secure context, which localhost is only sometimes */ }
    try {
      if (document.execCommand("copy")) {
        Peer.note(`copied all ${box.value.length} characters — paste the lot`);
        return;
      }
    } catch (err) { /* fall through to doing it by hand */ }
    // The whole value is selected either way, so the manual route is one key.
    Peer.note("could not copy for you — the whole code is selected, "
              + "press " + (navigator.platform.startsWith("Mac") ? "⌘C" : "Ctrl+C"));
  },

  async beHost() {
    // Ask before trying. Hosting needs a tunnel client, and one of the two
    // needs an account token; discovering that as a 503 in the middle of
    // starting a game turns a five-minute setup into a wall. Checking first
    // costs one local request and lets the answer be a button.
    await TunnelUI.refresh();
    if (!TunnelUI.ready()) {
      PeerUI.showStep("fix");
      const s = TunnelUI.state;
      Peer.note(s && s.client
        ? "ngrok needs a free account token before it can connect."
        : "this computer needs a tunnel client installed first.");
      return;
    }
    PeerUI.showStep("host");
    Peer.note("opening a way in for them…");
    try {
      const code = await Peer.host(PeerUI.deck(), PeerUI.name());
      if (code === null) {
        // `Peer.host` has already said why. Take the step back down rather than
        // leaving "Send this code to the other player" over an empty box.
        PeerUI.showStep(null);
        return;
      }
      el("#peer-code").value = code;
      PeerUI.copy("#peer-code");
      Peer.note("send them that code — the game starts when they use it");
    } catch (err) {
      Peer.fail(PeerUI.blame(err, "could not start a game"));
    }
  },

  beGuest() {
    PeerUI.showStep("guest");
    Peer.note("type the code you were sent");
    const box = el("#peer-code-in");
    if (box) { box.focus(); box.select(); }
  },

  async go() {
    const code = el("#peer-code-in").value.trim();
    if (!code) { Peer.fail("no code was typed"); return; }
    Peer.note("finding that game…");
    try {
      await Peer.join(code, PeerUI.deck(), PeerUI.name());
    } catch (err) {
      Peer.fail(PeerUI.blame(err, "could not join that game"));
    }
  },
};

document.addEventListener("DOMContentLoaded", () => {
  const bind = (sel, fn) => { const node = el(sel); if (node) node.onclick = fn; };
  bind("#title-peer", () => PeerUI.open());
  bind("#peer-be-host", () => PeerUI.beHost());
  bind("#peer-be-guest", () => PeerUI.beGuest());
  bind("#peer-go", () => PeerUI.go());
  bind("#peer-copy", () => PeerUI.copy("#peer-code"));
  // Enter is what someone does after typing a code into a single box.
  const typed = el("#peer-code-in");
  if (typed) {
    typed.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); PeerUI.go(); }
    });
  }
  /* Closing the dialog on a game that never connected has to put the player
   * back somewhere they can play from.
   *
   * Starting a peer game hides the title screen — correct while a match is
   * coming up, wrong the moment it turns out not to be. Left alone, someone
   * whose opponent never pasted a code is looking at an empty board with a
   * lobby still open behind it and no way back to the menu. So an unconnected
   * game is torn down on the way out and the title screen comes back up.
   *
   * Hung off the dialog's own `close` rather than the button, because Escape
   * closes a <dialog> too and would otherwise slip past this.
   *
   * A game actually in progress is left alone: the board behind the dialog is
   * a real match, and closing the dialog is how you get back to looking at it. */
  const dialog = el("#peerplay");
  if (dialog) {
    dialog.addEventListener("close", async () => {
      if (Peer.connected()) return;
      await Peer.leave("closed before connecting");
      // `poll` would get there on its own once the lobby is gone; doing it here
      // as well means the menu is back the instant the dialog is, rather than a
      // poll later.
      if (typeof Title !== "undefined") Title.show();
    });
  }
});

/* ---------------------------------------------------------------------------
 * setting the machine up, from the machine
 *
 * Playing over the internet needs a tunnel client, and one of the two needs an
 * account token. That used to be a command-line flag, which is a fine answer
 * for the person who wrote it and a wall for everybody else — the people most
 * likely to want to play a friend are the least likely to want to restart a
 * server with an argument in it.
 *
 * So the token is set here, from the settings screen, and the failure path in
 * the peer dialog points at it rather than just reporting that something is
 * missing. Two rules hold throughout: the browser can *set* a token and never
 * read one back, and nothing here ever puts a token in a status line, because
 * status lines end up in screenshots.
 * ------------------------------------------------------------------------ */
const TunnelUI = {
  state: null,
  client: "cloudflared",   // which one "Install it for me" would fetch
  FALLBACK_PAGE: "https://ngrok.com/download",

  async refresh() {
    try {
      TunnelUI.state = await Peer.reach("/api/tunnel");
    } catch (err) {
      TunnelUI.state = null;
    }
    TunnelUI.render();
    return TunnelUI.state;
  },

  /** Whether hosting a game would work right now, without trying it. */
  ready() {
    const s = TunnelUI.state;
    return !!s && !!s.client && (!s.needsToken || s.haveToken);
  },

  /* One screen, three states, and the button that moves you on from each:
   * nothing installed → install it; ngrok with no token → paste one; ready →
   * start a game. Anything the buttons cannot do (making an account, a machine
   * with no package manager) is a link rather than a dead end. */
  render() {
    const box = el("#tunnel-box");
    if (!box) return;
    const s = TunnelUI.state;
    const what = el("#tunnel-what");
    const token = el("#tunnel-token");
    const install = el("#tunnel-install");
    const ready = TunnelUI.ready();

    el("#tunnel-host").classList.toggle("hidden", !ready);
    el("#tunnel-test").classList.toggle("hidden", !s || !s.client);

    if (!s) { what.textContent = "could not ask this computer about it."; return; }
    TunnelUI.renderPicker(s);

    if (s.preferMissing) {
      // Asked for one thing and given another. Saying so beats a screen that
      // reports "ready" while quietly using something else.
      what.textContent =
        `${s.prefer} is not installed on this computer, so ${s.client || "nothing"} `
        + `is being used instead. Install ${s.prefer}, or choose again below.`;
      token.classList.add("hidden");
      install.classList.add("hidden");
      return;
    }

    if (!s.client) {
      what.textContent = s.install;
      token.classList.add("hidden");
      install.classList.remove("hidden");
      // cloudflared first: it needs no account at all, so the shortest road
      // from here to a game is the one that skips the token entirely.
      // A page can outlive the server it was loaded from — a restart onto an
      // older build, a tab left open across an update — so nothing here
      // assumes a field is present. A missing one costs a button, not a
      // screenful of "cannot read properties of undefined".
      const installs = s.installs || {};
      const pages = s.pages || {};
      const pick = installs.cloudflared ? "cloudflared" : "ngrok";
      TunnelUI.client = pick;
      el("#tunnel-cmd").textContent = installs[pick] || "";
      el("#tunnel-page").href = pages[pick] || TunnelUI.FALLBACK_PAGE;
      el("#tunnel-get").classList.toggle("hidden", !s.canInstall);
      el("#tunnel-cmd").parentElement.classList.toggle("hidden", !s.canInstall);
      return;
    }
    install.classList.add("hidden");

    if (s.setup) {
      // playit: the parts that are left happen on their account, not here, so
      // this says what they are rather than pretending a button could do them.
      what.textContent = s.setup;
      token.classList.add("hidden");
      return;
    }
    if (!s.needsToken) {
      what.textContent = `Ready — using ${s.client}, which needs no account.`;
      token.classList.add("hidden");
      return;
    }
    token.classList.remove("hidden");
    el("#tunnel-forget").classList.toggle("hidden", !s.savedToken);
    el("#tunnel-token-page").href =
      s.tokenPage || "https://dashboard.ngrok.com/get-started/your-authtoken";
    what.textContent = s.haveToken
      ? (s.fromEnv
        ? "Ready — using ngrok, with a token from this computer's environment."
        : "Ready — using ngrok, and it has your authtoken.")
      : "Using ngrok, which needs a free account token before it can connect.";
  },

  /** The service picker: every client we know, marked with what is here.
   *
   * All of them are listed rather than only the installed ones, because
   * picking one you have not got yet is a reasonable thing to do on the way to
   * installing it — and a list that hid them would look like the app did not
   * support them.
   */
  renderPicker(s) {
    const pick = el("#tunnel-prefer");
    if (!pick) return;
    const installed = s.installed || [];
    const choices = s.choices || [];
    const want = [""].concat(choices).join("|");
    if (pick.dataset.built !== want) {
      pick.innerHTML = "";
      const auto = h("option", null, "choose for me (cloudflared first)");
      auto.value = "";
      pick.appendChild(auto);
      for (const name of choices) {
        const option = h("option", null,
          name + (installed.includes(name) ? "" : " — not installed"));
        option.value = name;
        pick.appendChild(option);
      }
      pick.dataset.built = want;
    }
    pick.value = s.prefer || "";
  },

  async prefer(name) {
    try {
      const got = await Peer.reach("/api/tunnel/prefer", { prefer: name });
      if (got.error) { TunnelUI.note(got.error, true); return; }
      TunnelUI.state = got;
      TunnelUI.render();
      // The line above already explains a choice that is not installed; saying
      // "using ngrok" underneath it would contradict it in the same breath.
      const settled = got.preferMissing ? `saved, but ${got.prefer} is not here yet`
        : got.prefer ? `using ${got.prefer}`
        : "choosing automatically";
      TunnelUI.note(got.reopen
        // A tunnel already open belongs to whatever opened it; the choice
        // applies to the next one rather than retroactively.
        ? settled + " — it applies next time the game is restarted"
        : settled);
    } catch (err) {
      TunnelUI.note(err.message || String(err), true);
    }
  },

  /** Install a client, and keep saying what is happening while it runs. */
  async install() {
    const log = el("#tunnel-log");
    log.classList.remove("hidden");
    log.textContent = "";
    TunnelUI.note("installing… this takes a minute or two");
    try {
      const started = await Peer.reach("/api/tunnel/install",
                                       { client: TunnelUI.client });
      if (started.error) { TunnelUI.note(started.error, true); return; }
      // Poll rather than hold a request open for the length of a package
      // install, and show the output as it comes: an install that fails says
      // why, and hiding that leaves nobody anything to act on.
      for (;;) {
        const s = await TunnelUI.refresh();
        const job = s && s.job;
        if (!job) break;
        log.textContent = job.output;
        log.scrollTop = log.scrollHeight;
        if (!job.running) {
          TunnelUI.note(job.ok ? "installed — you can start a game now"
                               : (job.error || "that did not work; the output is above"),
                        !job.ok);
          break;
        }
        await new Promise((done) => setTimeout(done, 1200));
      }
    } catch (err) {
      TunnelUI.note(err.message || String(err), true);
    }
  },

  note(message, bad) {
    const line = el("#tunnel-status");
    if (!line) return;
    line.textContent = message;
    line.classList.toggle("bad", !!bad);
  },

  async save() {
    const box = el("#tunnel-input");
    const token = box.value.trim();
    if (!token) { TunnelUI.note("paste your authtoken first", true); return; }
    TunnelUI.note("saving…");
    try {
      const got = await Peer.reach("/api/tunnel/authtoken", { token });
      if (got.error) { TunnelUI.note(got.error, true); return; }
      // Out of the page as soon as it is out of our hands: a token left in a
      // field is a token in the next screenshot of this screen.
      box.value = "";
      TunnelUI.state = got;
      TunnelUI.render();
      TunnelUI.note("saved into ngrok — you can start a game now");
    } catch (err) {
      TunnelUI.note(err.message || String(err), true);
    }
  },

  async forget() {
    try {
      const got = await Peer.reach("/api/tunnel/forget", {});
      TunnelUI.state = got;
      TunnelUI.render();
      TunnelUI.note(got.had ? "forgotten" : "there was nothing saved");
    } catch (err) {
      TunnelUI.note(err.message || String(err), true);
    }
  },

  /** Open a real tunnel, because a token can be well-formed and still refused. */
  async test() {
    TunnelUI.note("connecting… this takes a few seconds");
    try {
      const got = await Peer.reach("/api/tunnel/test", {});
      if (got.error) { TunnelUI.note(got.error, true); return; }
      TunnelUI.state = got;
      TunnelUI.render();
      TunnelUI.note("it works — you can start a game now");
    } catch (err) {
      TunnelUI.note(err.message || String(err), true);
    }
  },
};

document.addEventListener("DOMContentLoaded", () => {
  const bind = (sel, fn) => { const node = el(sel); if (node) node.onclick = fn; };
  bind("#tunnel-save", () => TunnelUI.save());
  bind("#tunnel-forget", () => TunnelUI.forget());
  bind("#tunnel-test", () => TunnelUI.test());
  bind("#tunnel-get", () => TunnelUI.install());
  const pick = el("#tunnel-prefer");
  if (pick) pick.onchange = () => TunnelUI.prefer(pick.value);
  // The end of setting up is the thing you were trying to do in the first place.
  bind("#tunnel-host", () => {
    el("#settings").close();
    PeerUI.open();
    PeerUI.beHost();
  });
  // Enter in a single field means the one button next to it.
  const box = el("#tunnel-input");
  if (box) {
    box.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); TunnelUI.save(); }
    });
  }
  // Opening settings is the moment the answer has to be current: a token may
  // have been added in a terminal since the page loaded.
  const gear = el("#settings-open") || document.querySelector('[aria-label="Settings"]');
  if (gear) gear.addEventListener("click", () => TunnelUI.refresh());

  // The failure path: from "this needs setting up" straight to setting it up.
  bind("#peer-setup", () => {
    el("#peerplay").close();
    const dialog = el("#settings");
    if (dialog && !dialog.open) dialog.showModal();
    TunnelUI.refresh();
  });
});
