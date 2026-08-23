/* Replays tab: watch a finished game back.
 *
 * Runs after app.js and borrows its helpers (`el`, `h`, `api`, `showTab`).
 *
 * There is deliberately no player here — no scrubber, no frame store. A replay
 * is the list of decisions both seats took, and the server watches one back by
 * *running the game again* over the same decks, seed and answers. So what
 * arrives in the browser is an ordinary match: the same snapshots, the same
 * animations, and Pause / Step / speed in the header already work on it. This
 * file is the shelf the files sit on, not the projector.
 */

const replays = {
  rows: [],
  local: true,       // is this browser on the machine holding the files?
  path: "",
  note: "",
  busy: false,
};

function replayWhen(seconds) {
  if (!seconds) return "unknown date";
  const when = new Date(seconds * 1000);
  const today = new Date();
  const sameDay = when.toDateString() === today.toDateString();
  return sameDay
    ? "today " + when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : when.toLocaleString([], { year: "numeric", month: "short", day: "numeric",
                                hour: "2-digit", minute: "2-digit" });
}

function replayOutcome(row) {
  const result = row.result || {};
  const turns = result.turns ? `${result.turns} turns` : "";
  if (!result.over) return ["unfinished", turns].filter(Boolean).join(" · ");
  if (result.winner === null || result.winner === undefined || result.winner === -1) {
    return ["draw", turns].filter(Boolean).join(" · ");
  }
  return [`seat ${result.winner} wins`, turns].filter(Boolean).join(" · ");
}

async function refreshReplays() {
  try {
    const data = await api("/api/replays");
    replays.rows = data.replays || [];
    replays.local = !!data.local;
    replays.path = data.path || "";
  } catch (err) {
    replays.note = "could not read the replay folder";
  }
  renderReplays();
}

function renderReplays() {
  const list = el("#replay-list");
  if (!list) return;
  el("#replay-path").textContent = replays.path
    ? `kept in ${replays.path}` : "";
  el("#replay-note").textContent = replays.note;
  el("#replay-note").classList.toggle("hidden", !replays.note);
  list.innerHTML = "";
  if (!replays.rows.length) {
    list.appendChild(h("p", "replay-empty",
      "No replays yet. Every game you finish is kept here automatically — "
      + "or drop a replay file someone sent you onto this pane."));
    return;
  }
  replays.rows.forEach((row) => list.appendChild(replayRow(row)));
}

function replayRow(row) {
  const node = h("div", "replay-row");
  const main = h("div", "replay-main");
  main.appendChild(h("div", "replay-decks", (row.decks || []).join("  vs  ")));
  const meta = h("div", "replay-meta");
  meta.appendChild(h("span", null, replayWhen(row.recorded)));
  meta.appendChild(h("span", null, (row.pilots || []).map(prettyPilot).join(" / ")));
  meta.appendChild(h("span", null, replayOutcome(row)));
  meta.appendChild(h("span", "dim", `${row.decisions} decisions · seed ${row.seed}`));
  // Which build recorded it. A replay from an older one usually still plays;
  // when it does not, this is the first thing worth knowing.
  if (row.appVersion) meta.appendChild(h("span", "dim", "v" + row.appVersion));
  main.appendChild(meta);
  node.appendChild(main);

  const tools = h("div", "replay-tools");
  const watch = h("button", "primary tiny", "Watch");
  watch.onclick = () => watchReplay({ name: row.name });
  tools.appendChild(watch);

  const grab = h("a", "ghost tiny button", "Download");
  grab.href = "/api/replay?name=" + encodeURIComponent(row.name);
  grab.download = row.name;
  tools.appendChild(grab);

  if (replays.local) {
    const drop = h("button", "ghost tiny", "Delete");
    drop.onclick = async () => {
      if (!confirm(`Delete ${row.name}?`)) return;
      const res = await api("/api/replays/delete", { name: row.name });
      replays.note = res.error || "";
      refreshReplays();
    };
    tools.appendChild(drop);
  }
  node.appendChild(tools);
  return node;
}

/** Start a match that is watching `body` back, and go and look at it. */
async function watchReplay(body) {
  if (replays.busy) return;
  // A replay is a local match, and the board can only show one thing at a
  // time. Quietly swapping it in under someone who is sitting in an online
  // game would look like their opponent had done something very strange.
  if (state.room) {
    replays.note = "leave the room first — a replay plays on this machine";
    renderReplays();
    return;
  }
  replays.busy = true;
  try {
    const res = await api("/api/replays/watch", {
      ...body,
      paused: el("#replay-paused").checked,
      // The slider in the header, not whatever pace the last match happened to
      // run at: a game watched at speed 0 for a quick result should not make
      // the next replay flash past before anyone sees it.
      delay: el("#speed").value / 1000,
    });
    if (res.error) { replays.note = res.error; renderReplays(); return; }
    replays.note = "";
    // A new match arriving over the top of the old one: same reset the New
    // match button does, so nothing from the last game is left on the board.
    resetForNewMatch();
    showTab("play");
    poll();
  } finally {
    replays.busy = false;
  }
}

/* --------------------------------------------------------------- controls */
el("#replay-refresh").onclick = refreshReplays;

el("#replay-save").onclick = async () => {
  // Playing someone in a room? Save that game — the seat token says it is
  // yours to save. Otherwise it is the local match on the board.
  const res = await api("/api/replays/save",
                        state.room && typeof roomAuth === "function" ? roomAuth() : {});
  replays.note = res.error || `saved as ${res.name}`;
  refreshReplays();
};

/* A file someone sent you. It never touches this machine's replay folder —
 * it is posted straight to the server and played, so watching a stranger's
 * game leaves nothing behind. */
async function watchFile(file) {
  if (!file) return;
  try {
    const blob = JSON.parse(await file.text());
    await watchReplay({ replay: blob });
  } catch (err) {
    replays.note = `${file.name} is not a replay file`;
    renderReplays();
  }
}

el("#replay-file").onchange = (e) => {
  watchFile(e.target.files[0]);
  e.target.value = "";        // so the same file can be picked twice
};

const replayPane = el("#replays");
["dragenter", "dragover"].forEach((kind) => {
  replayPane.addEventListener(kind, (e) => {
    e.preventDefault();
    replayPane.classList.add("dropping");
  });
});
["dragleave", "drop"].forEach((kind) => {
  replayPane.addEventListener(kind, (e) => {
    e.preventDefault();
    if (kind === "drop") watchFile(e.dataTransfer.files[0]);
    replayPane.classList.remove("dropping");
  });
});
