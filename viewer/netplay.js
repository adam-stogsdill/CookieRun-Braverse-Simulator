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

const Peer = {
  pc: null,
  channel: null,
  seat: null,
  on: false,
  status: "",
  inbox: [],          // messages from the channel, waiting to be posted inward
  flushing: false,
  pumping: false,

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

  async decode(code) {
    const body = code.trim().replace(/\s+/g, "");
    if (!body) throw new Error("that code is empty");
    const bytes = Peer.unbase64(body.slice(1));
    const raw = body[0] === "z" ? await Peer.gunzip(bytes) : bytes;
    const blob = JSON.parse(new TextDecoder().decode(raw));
    if (!blob || !blob.s) throw new Error("that does not look like a code");
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
    channel.addEventListener("open", () => {
      Peer.note("connected");
      Peer.pump();
    });
    channel.addEventListener("close", () => Peer.note("the other player left"));
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
        const res = await api("/api/peer/in", { msgs: batch });
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
        const res = await api("/api/peer/out");
        if (!res || res.gone) break;
        if (res.peer) Peer.report(res.peer);
        for (const message of res.msgs || []) {
          if (Peer.channel.readyState !== "open") break;
          Peer.channel.send(JSON.stringify(message));
        }
      }
    } catch (err) {
      Peer.note("lost contact with the local engine");
    } finally {
      Peer.pumping = false;
    }
  },

  /* -- starting a game --------------------------------------------------- */

  /** Host: build the offer code, and hold it out to be pasted elsewhere. */
  async host(deck, name) {
    const started = await api("/api/peer/new", { host: true, deck, name });
    if (started.error) { Peer.note(started.error); return null; }
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
    const started = await api("/api/peer/new", { host: false, deck, name });
    if (started.error) { Peer.note(started.error); return null; }
    Peer.begin(1);
    const pc = Peer.fresh();
    pc.addEventListener("datachannel", (event) => Peer.adopt(event.channel));
    await pc.setRemoteDescription(offer);
    await pc.setLocalDescription(await pc.createAnswer());
    await Peer.settled(pc);
    return Peer.encode(pc.localDescription);
  },

  begin(seat) {
    Peer.seat = seat;
    Peer.on = true;
    state.peer = true;
    // A peer game is drawn from our own seat, exactly as a room is; nothing
    // below should be comparing a seat to 0.
    if (typeof seatPerspective === "function") seatPerspective(seat);
    Peer.note("waiting for the other player");
  },

  /** What the local engine says about the handshake, for the status line. */
  report(status) {
    if (!status) return;
    if (status.error) Peer.note(status.error);
    else if (status.state === "playing" && Peer.status !== "playing") {
      Peer.note("playing");
      Peer.status = "playing";
    }
  },

  note(message) {
    Peer.status = message;
    const line = el("#peer-status");
    if (line) line.textContent = message;
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
    state.peer = false;
    Peer.teardown();
    Peer.inbox.length = 0;
    try { await api("/api/peer/close", { why: why || "left the match" }); } catch (e) {}
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

  /* A textarea rather than the clipboard alone: `navigator.clipboard` needs a
   * secure context, and a plain http://localhost page is only sometimes one.
   * The code is always on screen to be selected by hand. */
  async copy(sel) {
    const box = el(sel);
    box.select();
    try { await navigator.clipboard.writeText(box.value); Peer.note("copied"); }
    catch (err) { Peer.note("select the code and copy it"); }
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
      Peer.note(String(err.message || err));
    }
  },

  async accept() {
    try {
      await Peer.accept(el("#peer-answer").value);
      Peer.note("connecting…");
    } catch (err) {
      Peer.note("that reply code did not work — " + (err.message || err));
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
      Peer.note("that code did not work — " + (err.message || err));
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
  bind("#peer-copy-answer", () => PeerUI.copy("#peer-answer-out"));
  // Closing the dialog is not leaving the game: the board is behind it, and a
  // connected match should stay connected. Only an unstarted one is torn down.
  bind("#peer-close", () => { if (!Peer.on) Peer.leave("closed the dialog"); });
});
