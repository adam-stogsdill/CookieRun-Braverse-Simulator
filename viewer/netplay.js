/* Peer-to-peer: the other player's machine, reached directly.
 *
 * A room (see `Room` in play_server.py) puts one machine in charge of the game
 * and has the other talk to it over HTTP. This is the other arrangement
 * entirely: both people run the engine, an RTCDataChannel carries the
 * decisions between them, and nobody hosts anything. No port is opened, no
 * tunnel is dialled, and no address of yours is handed to a stranger.
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
 * Signalling is done by hand: the two of you swap a code over whatever you
 * already trust to talk to each other. That is not a limitation worked around,
 * it is the feature — a signalling server would be one more thing to run, to
 * trust, and to keep online, and the exchange happens exactly once per game. */

/* Public STUN, needed only to discover how this machine looks from outside a
 * NAT. It carries no game data and learns nothing but that an address asked.
 * Two people on the same network do not need it at all, which is why a failure
 * to reach it is not fatal here. */
const PEER_ICE = [{ urls: "stun:stun.l.google.com:19302" }];

/* Said the same way wherever a code turns out not to have arrived whole, since
 * the remedy is always the same and never the one the raw error suggests. */
const PEER_INCOMPLETE =
  "that code did not arrive in one piece — copy it with the Copy button and "
  + "paste the whole thing, all of it on one line";

const Peer = {
  INCOMPLETE: PEER_INCOMPLETE,
  pc: null,
  channel: null,
  seat: null,
  on: false,
  status: "",
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

  /* -- signalling codes ------------------------------------------------- */

  /** A description as a code short enough to paste into a chat window.
   *
   * Raw SDP is a few kilobytes of text, which is a miserable thing to send
   * someone. Gzip takes it to a few hundred bytes where the browser has
   * `CompressionStream`, and where it does not the code is merely long rather
   * than broken — worth the branch, since being unable to start a game at all
   * would be the alternative. */
  async encode(description) {
    const json = JSON.stringify({ t: description.type, s: description.sdp });
    const bytes = new TextEncoder().encode(json);
    const packed = await Peer.gzip(bytes);
    return (packed ? "z" : "p") + Peer.base64(packed || bytes);
  },

  /** A code back into a description, saying plainly when it is not one.
   *
   * Every failure here is one of two things and they need different advice:
   * the text is not a code at all, or it is a code that did not arrive whole.
   * The second is much the commoner — these are ~700 characters in a
   * three-row box, so a drag-select takes a fragment, and chat clients
   * helpfully truncate long strings — and it used to surface as
   * `TypeError: Failed to fetch`, because a corrupt gzip stream errors the
   * body and `Response.arrayBuffer` reports that the way it reports a dead
   * network. Three words, no relation to the actual problem, and they sent
   * people looking at their server instead of their clipboard. */
  async decode(code) {
    const body = String(code || "").trim().replace(/\s+/g, "");
    if (!body) throw new Error("no code was pasted");
    if (!"zp".includes(body[0]) || body.length < 24) {
      throw new Error("that does not look like a game code — it should be one "
                      + "long unbroken line starting with z");
    }
    let bytes;
    try {
      bytes = Peer.unbase64(body.slice(1));
    } catch (err) {
      throw new Error(Peer.INCOMPLETE);
    }
    let raw;
    try {
      raw = body[0] === "z" ? await Peer.gunzip(bytes) : bytes;
    } catch (err) {
      throw new Error(Peer.INCOMPLETE);
    }
    let blob;
    try {
      blob = JSON.parse(new TextDecoder().decode(raw));
    } catch (err) {
      throw new Error(Peer.INCOMPLETE);
    }
    if (!blob || !blob.s) throw new Error(Peer.INCOMPLETE);
    return { type: blob.t, sdp: blob.s };
  },

  async gzip(bytes) {
    if (typeof CompressionStream === "undefined") return null;
    try {
      const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream("gzip"));
      return new Uint8Array(await new Response(stream).arrayBuffer());
    } catch (err) { return null; }
  },

  async gunzip(bytes) {
    if (typeof DecompressionStream === "undefined") {
      throw new Error("this browser cannot read a compressed code");
    }
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  },

  base64(bytes) {
    let out = "";
    for (const byte of bytes) out += String.fromCharCode(byte);
    return btoa(out).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  },

  unbase64(text) {
    const padded = text.replace(/-/g, "+").replace(/_/g, "/");
    const raw = atob(padded + "=".repeat((4 - padded.length % 4) % 4));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  },

  /* -- the connection --------------------------------------------------- */

  /** Wait until every candidate is in the description we are about to show.
   *
   * The alternative — trickle ICE — means a second channel to carry the later
   * candidates on, and there is no second channel here: the whole exchange is
   * one code, pasted once. Gathering fully up front is what makes that
   * possible, at the cost of a couple of seconds before the code appears. */
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

  /** Everything that makes the channel usable once it is open. */
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

  /** Host: build the offer code, and hold it out to be pasted elsewhere. */
  async host(deck, name) {
    const started = await Peer.reach("/api/peer/new", { host: true, deck, name });
    if (started.error) { Peer.fail(started.error); return null; }
    Peer.begin(0);
    const pc = Peer.fresh();
    // The host opens the channel; the joiner picks it up from `ondatachannel`.
    // Ordered and reliable are the defaults and are load-bearing — `netplay`
    // is counting decisions, so a dropped or reordered message is a desync.
    Peer.adopt(pc.createDataChannel("braverse", { ordered: true }));
    await pc.setLocalDescription(await pc.createOffer());
    await Peer.settled(pc);
    return Peer.encode(pc.localDescription);
  },

  /** Host: take the joiner's answer code and the game is on. */
  async accept(code) {
    if (!Peer.pc) throw new Error("start a game first");
    await Peer.pc.setRemoteDescription(await Peer.decode(code));
  },

  /** Joiner: take the host's offer code, and give back an answer code. */
  async join(code, deck, name) {
    const offer = await Peer.decode(code);      // before anything is committed
    const started = await Peer.reach("/api/peer/new", { host: false, deck, name });
    if (started.error) { Peer.fail(started.error); return null; }
    Peer.begin(1);
    const pc = Peer.fresh();
    pc.addEventListener("datachannel", (event) => Peer.adopt(event.channel));
    await pc.setRemoteDescription(offer);
    await pc.setLocalDescription(await pc.createAnswer());
    await Peer.settled(pc);
    return Peer.encode(pc.localDescription);
  },

  /* Watch the local engine while the codes are being swapped.
   *
   * Until the channel opens there is no pump, so nothing is reading what the
   * engine has to say — and the setting-up phase is exactly when it has
   * something worth saying: a deck that will not load, a peer on another
   * build, a handshake that gave up. Without this the dialog sits on a
   * cheerful stale line while the game behind it is already dead, which is a
   * worse failure than the failure. Stops as soon as the pump takes over. */
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
    return /cannot reach|answered \d|understands|one piece|look like a game code|no code was/.test(message)
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

  /** Tell someone their code is short *before* they click the button.
   *
   * A code that did not survive the trip is the commonest failure here, and
   * finding out at paste time beats finding out after a round trip through a
   * chat window. Cheap: decoding is local and takes no connection. */
  async check(sel) {
    const value = el(sel).value.trim();
    if (!value) { Peer.note(""); return; }
    try {
      await Peer.decode(value);
      Peer.note("that code looks complete");
    } catch (err) {
      Peer.note(String(err.message || err));
    }
  },

  async beHost() {
    PeerUI.showStep("host");
    Peer.note("building your code…");
    try {
      const code = await Peer.host(PeerUI.deck(), PeerUI.name());
      if (code === null) return;                 // `Peer.host` said why
      el("#peer-offer").value = code;
      Peer.note("send that code over, then paste their reply");
    } catch (err) {
      Peer.fail(PeerUI.blame(err, "could not start a game"));
    }
  },

  async accept() {
    try {
      await Peer.accept(el("#peer-answer").value);
      Peer.note("connecting…");
    } catch (err) {
      Peer.fail(PeerUI.blame(err, "that reply code did not work"));
    }
  },

  beGuest() {
    PeerUI.showStep("guest");
    Peer.note("paste the code you were sent");
  },

  async reply() {
    Peer.note("building your reply…");
    try {
      const code = await Peer.join(el("#peer-offer-in").value,
                                   PeerUI.deck(), PeerUI.name());
      if (code === null) return;
      el("#peer-answer-out").value = code;
      Peer.note("send that back, and the game starts on its own");
    } catch (err) {
      Peer.fail(PeerUI.blame(err, "that code did not work"));
    }
  },
};

document.addEventListener("DOMContentLoaded", () => {
  const bind = (sel, fn) => { const node = el(sel); if (node) node.onclick = fn; };
  bind("#title-peer", () => PeerUI.open());
  bind("#peer-be-host", () => PeerUI.beHost());
  bind("#peer-be-guest", () => PeerUI.beGuest());
  bind("#peer-accept", () => PeerUI.accept());
  bind("#peer-reply", () => PeerUI.reply());
  bind("#peer-copy", () => PeerUI.copy("#peer-offer"));
  for (const sel of ["#peer-answer", "#peer-offer-in"]) {
    const box = el(sel);
    if (box) box.addEventListener("input", () => PeerUI.check(sel));
  }
  bind("#peer-copy-answer", () => PeerUI.copy("#peer-answer-out"));
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
