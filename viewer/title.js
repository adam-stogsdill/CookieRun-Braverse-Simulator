/* The front door, and the door out of a finished match.
 *
 * This file owns two overlays and nothing else. It starts no games itself:
 * every route out of the title screen ends in the set-up dialog that already
 * knows how to start one, prefilled for the mode that was picked, so there is
 * still exactly one place that POSTs /api/new. The dialog is a <dialog>, so it
 * opens in the browser's top layer and draws over the title without either of
 * them having to know about the other.
 *
 * `sync` is driven from `poll`, not from `render`: the interesting states here
 * are the ones the renderer never reaches — no match at all, and a room's
 * lobby, both of which return early. */

const Title = {
  on: false,

  show() {
    /* Set first, then switch tabs: `showTab` syncs the title back when it
     * lands on the play view, and a sync that still saw `on === false` would
     * call straight back in here. */
    Title.on = true;
    if (typeof showTab === "function") showTab("play");
    el("#title").classList.remove("hidden");
    Title.hideOver();
  },

  hide() {
    el("#title").classList.add("hidden");
    Title.on = false;
  },

  hideOver() { el("#gameover").classList.add("hidden"); },

  /* Open the set-up dialog with the decisions this menu entry has already
   * taken. Deck choices are left alone: they are whatever was last picked, and
   * the point of going through the dialog is that they can still be changed. */
  openSetup(pilots) {
    if (pilots) {
      el("#pilot0").value = pilots[0];
      el("#pilot1").value = pilots[1];
      if (typeof updateHint === "function") updateHint();
    }
    setMode(!pilots);           // no pilots means the LAN pane
    el("#setup").showModal();
  },

  /** The snapshot decides which overlay, if either, belongs on screen.
   *
   * The two questions are asked separately on purpose. The title screen goes
   * *up* when there is no match at all, but it comes *down* only for a match
   * still being played — a finished game left on the board is exactly what is
   * underneath the title when someone chooses "Title screen" from the
   * end-of-match card, and testing one condition for both would pull the
   * menu straight back down again on the next poll. */
  sync(snap) {
    if (!snap) return;
    const waiting = !!snap.lobby || !!state.room;
    /* Not while another tab has the window. Every route off this menu that
     * does not start a game — the deck builder, the replay shelf — leaves the
     * server just as idle as it was, so a title screen that only asked about
     * the *match* would raise itself on the very next poll and drag the play
     * view back up with it, which is the deck builder closing the instant it
     * is opened. */
    const elsewhere = typeof onPlayTab === "function" && !onPlayTab();
    if (snap.idle && !waiting && !Title.on && !elsewhere) Title.show();
    if ((waiting || (!snap.idle && !snap.over)) && Title.on) Title.hide();
    // A finished match asks its question once the last scene has played out;
    // `dismissed` is the person having said they want to look at the board.
    if (!snap.over) { Title.dismissed = null; Title.hideOver(); return; }
    // A finished game is banked against the open profile on the match thread;
    // this is only the browser going back for the new numbers. profile.js is
    // loaded after this file, so it is checked for rather than assumed.
    if (typeof Profile !== "undefined") Profile.seen(snap.version);
    if (state.animating || Title.on || Title.dismissed === snap.version) return;
    // Another tab has the window: the deck builder and the replay shelf are
    // not places to be asked what to do about a game that has ended.
    if (typeof onPlayTab === "function" && !onPlayTab()) return;
    Title.renderOver(snap);
  },

  renderOver(snap) {
    const box = el("#gameover");
    if (!box.classList.contains("hidden")) return;   // already up, and drawn
    const head = el("#over-title");
    const mine = state.room ? state.mySeat : (snap.humanSeats || [])[0];
    const drew = snap.winner === -1 || snap.winner === null;
    head.className = drew ? "drawn" : "";
    if (drew) head.textContent = "Draw";
    else if (mine === undefined || mine === null) {
      head.textContent = seatLabel(snap.winner, snap) + " wins";
    } else if (snap.winner === mine) head.textContent = "You win";
    else { head.textContent = "You lose"; head.className = "lost"; }
    setText(el("#over-why"), snap.winReason || "");
    box.classList.remove("hidden");
  },

  /* Same two seats, same decks, fresh shuffle. A room has its own rematch —
   * both players have to agree to it — so that button is deferred to. */
  async again() {
    Title.hideOver();
    if (state.room) { el("#btn-rematch").click(); return; }
    const last = state.lastMatch;
    if (!last) { Title.openSetup(["human", "heuristic"]); return; }
    const res = await api("/api/new", { ...last, seed: null });
    if (res.error) { alert(res.error); return; }
    state.version = -1;
    state.pendingId = null;
    state.eventId = 0;
    state.announced = false;
    poll();
  },
};

el("#title-bot").onclick = () => Title.openSetup(["human", "heuristic"]);
el("#title-watch").onclick = () => Title.openSetup(["heuristic", "heuristic"]);
el("#title-lan").onclick = () => Title.openSetup(null);
el("#title-learn").onclick = () => { Title.hide(); el("#btn-learn").click(); };
el("#title-build").onclick = () => { Title.hide(); el("#tab-build").click(); };
el("#title-replays").onclick = () => { Title.hide(); el("#tab-replays").click(); };

el("#over-again").onclick = () => Title.again();
el("#over-menu").onclick = () => {
  Title.hideOver();
  if (state.room) { leaveRoom(); return; }   // which comes back here itself
  Title.show();
};
/* Not a close box: the board is still worth reading, and the prompt comes back
 * on the next match rather than on the next poll. */
el("#over-look").onclick = () => {
  Title.hideOver();
  Title.dismissed = state.snap ? state.snap.version : null;
};

/* At boot the title belongs up only if there is nothing to come back to — a
 * refresh in the middle of a game, or a `?room=` link, should land on the
 * board. Asking the server costs one request and saves a flash of the menu. */
if (!new URLSearchParams(location.search).get("room")) {
  api("/api/state").then((snap) => Title.sync(snap)).catch(() => Title.show());
}
