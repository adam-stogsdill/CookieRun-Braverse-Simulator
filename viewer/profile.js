/* The player: who is signed in here, and what they have played.
 *
 * Runs after app.js and borrows its helpers (`el`, `h`, `api`), and after
 * replays.js so a game in the history can be watched back through the same
 * `watchReplay` the shelf uses — there is one way to start a replay, not two.
 *
 * Nothing in here decides anything. The server owns the profile file, the XP
 * and the thirty-game window; the browser draws what `/api/profiles` says and
 * posts what the player clicked. That matters more than it looks: a game is
 * banked on the *match thread* as it ends, whether or not this pane is open,
 * whether or not a browser is even connected — so the record is what was
 * played, rather than what somebody had on screen at the time.
 *
 * Three dialogs, in the order they are usually met: `#profiles` chooses or
 * makes one, `#profile` is the record itself, `#avatar` picks a picture.
 */

const Profile = {
  rows: [],          // profiles on this machine (name and picture only)
  me: null,          // the open one, in full — null when nobody is signed in
  path: "",
  off: false,        // this browser is not the machine holding the files
  note: "",
  picking: "",       // slug whose passphrase box is open in the chooser
  busy: false,

  /* ------------------------------------------------------------ loading */
  async refresh() {
    try {
      const data = await api("/api/profiles");
      if (data.error) { Profile.off = true; Profile.rows = []; Profile.me = null; }
      else {
        Profile.off = false;
        Profile.rows = data.profiles || [];
        Profile.me = data.active || null;
        Profile.path = data.path || "";
      }
    } catch (err) {
      Profile.off = true;
    }
    Profile.draw();
  },

  /** Apply what a profile route answered with — every one of them replies
   *  with the new list and the open profile, so nothing has to re-fetch. */
  take(res) {
    if (res.error) { Profile.note = res.error; Profile.draw(); return false; }
    Profile.note = "";
    Profile.rows = res.profiles || Profile.rows;
    Profile.me = res.active || null;
    Profile.draw();
    return true;
  },

  draw() {
    Profile.drawChip();
    if (el("#profile").open) Profile.drawPane();
    if (el("#profiles").open) Profile.drawChooser();
  },

  /* --------------------------------------------------------------- chip */
  /* The way in, on the title screen. It carries the level rather than only the
   * name, because the level is the thing that changed since last time. */
  drawChip() {
    const chip = el("#title-profile");
    if (!chip) return;
    chip.classList.toggle("hidden", Profile.off);
    chip.innerHTML = "";
    if (Profile.off) return;
    const me = Profile.me;
    chip.appendChild(avatarNode(me ? me.avatar : "", me ? me.name : "?"));
    const text = h("div", "chip-text");
    text.appendChild(h("b", null, me ? me.name : "Sign in"));
    text.appendChild(h("i", null, me ? `Level ${me.level}` : "keep a record of your games"));
    if (me) text.appendChild(xpBar(me));
    chip.appendChild(text);
  },

  /* ------------------------------------------------------------ chooser */
  openChooser() {
    Profile.picking = "";
    Profile.note = "";
    Profile.drawChooser();
    if (!el("#profiles").open) el("#profiles").showModal();
  },

  drawChooser() {
    const list = el("#profile-list");
    list.innerHTML = "";
    if (!Profile.rows.length) {
      list.appendChild(h("p", "profile-empty",
        "No profiles on this machine yet. Make one below — it is a file here, "
        + "encrypted, and it never goes anywhere."));
    }
    Profile.rows.forEach((row) => list.appendChild(chooserRow(row)));
    setText(el("#profile-note"), Profile.note);
    el("#profile-note").classList.toggle("hidden", !Profile.note);
    setText(el("#profile-where"), Profile.path ? `kept in ${Profile.path}` : "");
  },

  async open(slug, passphrase) {
    if (Profile.busy) return;
    Profile.busy = true;
    try {
      const res = await api("/api/profiles/open", { slug, passphrase });
      if (res.locked) {
        // Not an error to show in red: it is the box asking to be filled in.
        Profile.picking = slug;
        Profile.note = "";
        Profile.drawChooser();
        const box = el(`#pass-${cssId(slug)}`);
        if (box) box.focus();
        return;
      }
      if (!Profile.take(res)) return;
      el("#profiles").close();
      Profile.openPane();
    } finally {
      Profile.busy = false;
    }
  },

  async create() {
    const name = el("#new-name").value.trim();
    const pass = el("#new-pass").value;
    const again = el("#new-pass2").value;
    if (!name) { Profile.note = "give the profile a name"; Profile.drawChooser(); return; }
    if (pass !== again) {
      Profile.note = "those two passphrases are not the same";
      Profile.drawChooser();
      return;
    }
    const res = await api("/api/profiles/new",
                          { name, passphrase: pass, avatar: newAvatar });
    if (!Profile.take(res)) return;
    el("#new-name").value = el("#new-pass").value = el("#new-pass2").value = "";
    setNewAvatar("");
    el("#profiles").close();
    Profile.openPane();
  },

  /* --------------------------------------------------------------- pane */
  openPane() {
    if (!Profile.me) { Profile.openChooser(); return; }
    Profile.drawPane();
    if (!el("#profile").open) el("#profile").showModal();
  },

  drawPane() {
    const me = Profile.me;
    if (!me) { el("#profile").close(); return; }
    const head = el("#profile-head");
    head.innerHTML = "";
    const face = avatarNode(me.avatar, me.name, "big");
    face.title = "Change the picture";
    face.onclick = () => Avatar.open();
    head.appendChild(face);

    const who = h("div", "profile-who");
    who.appendChild(h("h2", null, me.name));
    who.appendChild(h("div", "profile-level", `Level ${me.level}`));
    who.appendChild(xpBar(me));
    who.appendChild(h("div", "profile-xp",
      `${me.into} / ${me.need} XP towards level ${me.level + 1}`
      + (me.locked ? " · passphrase-protected" : "")));
    head.appendChild(who);

    const rate = me.games ? Math.round((me.wins / me.games) * 100) : 0;
    const tally = el("#profile-tally");
    tally.innerHTML = "";
    [["games", me.games], ["won", me.wins], ["lost", me.losses],
     ["drawn", me.draws], ["win rate", me.games ? `${rate}%` : "—"]]
      .forEach(([label, value]) => {
        const box = h("div", "stat");
        box.appendChild(h("b", null, String(value)));
        box.appendChild(h("i", null, label));
        tally.appendChild(box);
      });

    Profile.drawDecks(me);
    Profile.drawGames(me);
    setText(el("#profile-pane-note"), Profile.note);
    el("#profile-pane-note").classList.toggle("hidden", !Profile.note);
  },

  drawDecks(me) {
    const body = el("#profile-decks");
    body.innerHTML = "";
    if (!me.decks.length) {
      body.appendChild(h("p", "profile-empty",
        "Play a game and the deck you played it with shows up here."));
      return;
    }
    const table = h("table", "profile-table");
    const head = h("tr");
    ["deck", "games", "W", "L", "D", "win rate"]
      .forEach((label) => head.appendChild(h("th", null, label)));
    table.appendChild(head);
    me.decks.forEach((deck) => {
      const row = h("tr");
      row.appendChild(h("td", "deckname", deck.name));
      row.appendChild(h("td", null, String(deck.games)));
      row.appendChild(h("td", "good", String(deck.wins)));
      row.appendChild(h("td", "bad", String(deck.losses)));
      row.appendChild(h("td", null, String(deck.draws)));
      row.appendChild(h("td", null, deck.games
        ? `${Math.round((deck.wins / deck.games) * 100)}%` : "—"));
      table.appendChild(row);
    });
    body.appendChild(table);
  },

  drawGames(me) {
    const list = el("#profile-games");
    list.innerHTML = "";
    const kept = me.history.filter((g) => g.kept).length;
    setText(el("#profile-games-note"),
      `The last ${me.limit} games are kept, plus every game you have starred — `
      + `${kept} starred right now. A game that falls out of the list takes its `
      + `replay with it; starring one keeps both for good.`);
    if (!me.history.length) {
      list.appendChild(h("p", "profile-empty", "No games yet."));
      return;
    }
    me.history.forEach((game) => list.appendChild(gameRow(game)));
  },

  async keep(game, kept) {
    Profile.take(await api("/api/profile/games/keep", { id: game.id, kept }));
  },

  async forget(game) {
    if (!confirm("Delete this game and its replay? The win still counts; "
                 + "only the log goes.")) return;
    Profile.take(await api("/api/profile/games/delete", { id: game.id }));
    if (typeof refreshReplays === "function") refreshReplays();
  },

  /** A match that has just ended may have banked a game. Called from the poll
   *  loop rather than from the pane, because the pane is usually shut when it
   *  happens and the chip on the title screen is what shows it. */
  seen(version) {
    if (Profile.off || !Profile.me || Profile.lastOver === version) return;
    Profile.lastOver = version;
    setNewAvatar("");     // the empty circle on the new-profile form
setNewAvatar("");     // the empty circle on the new-profile form
Profile.refresh();
  },

  async close() {
    Profile.take(await api("/api/profiles/close", {}));
    el("#profile").close();
  },

  async remove() {
    const me = Profile.me;
    if (!me) return;
    if (!confirm(`Delete the profile "${me.name}"? Its games, decks and level `
                 + "go with it, and there is no way back.")) return;
    const logs = confirm("Delete the replays of its games too?\n\n"
                         + "OK deletes them. Cancel keeps the files.");
    let passphrase = "";
    if (me.locked) {
      // A locked profile is not deleted without its passphrase: "delete" must
      // not be the way around a profile you cannot open.
      passphrase = prompt("Passphrase for this profile:") || "";
      if (!passphrase) return;
    }
    const res = await api("/api/profiles/delete",
                          { slug: me.slug, passphrase, logs });
    if (!Profile.take(res)) { alert(res.error); return; }
    el("#profile").close();
    if (typeof refreshReplays === "function") refreshReplays();
  },
};

/* ---------------------------------------------------------------- pieces */
/** A button that does something, rather than one that submits.
 *
 * Every control here is built inside a `<form method="dialog">`, and a
 * <button> with no `type` is a submit button — which in a dialog form means
 * "close the dialog". Starring a game would shut the pane, and picking a
 * profile would shut the chooser before it could redraw with the passphrase
 * box. So no control here is built with a bare `h("button", …)`.
 */
function btn(cls, text) {
  const node = h("button", cls, text);
  node.type = "button";
  return node;
}

/** A profile picture: a card's art, an uploaded image, or their initial. */
function avatarNode(avatar, name, cls) {
  const node = h("div", "avatar" + (cls ? " " + cls : ""));
  const value = String(avatar || "");
  if (value.startsWith("card:")) {
    node.style.backgroundImage = `url("/card_images/${value.slice(5)}.webp")`;
    node.classList.add("art");
  } else if (value.startsWith("data:")) {
    node.style.backgroundImage = `url("${value}")`;
    node.classList.add("art");
  } else {
    node.textContent = (String(name || "?").trim()[0] || "?").toUpperCase();
  }
  return node;
}

function xpBar(me) {
  const bar = h("div", "xpbar");
  const fill = h("div", "xpfill");
  fill.style.width = `${Math.round((me.into / Math.max(1, me.need)) * 100)}%`;
  bar.appendChild(fill);
  return bar;
}

/* A slug is `[a-z0-9-]` from the server, so this is belt and braces — but it
 * is spliced into a selector, and a selector is not a place to trust input. */
const cssId = (slug) => String(slug).replace(/[^a-z0-9-]/gi, "");

function chooserRow(row) {
  const node = h("div", "profile-row");
  const pick = btn("profile-pick");
  pick.appendChild(avatarNode(row.avatar, row.name));
  const text = h("div", "chip-text");
  text.appendChild(h("b", null, row.name));
  text.appendChild(h("i", null, row.locked ? "needs its passphrase"
                                           : "opens on this machine"));
  pick.appendChild(text);
  if (Profile.me && Profile.me.slug === row.slug) {
    pick.appendChild(h("span", "tag", "open"));
  }
  pick.onclick = () => Profile.open(row.slug, "");
  node.appendChild(pick);

  if (Profile.picking === row.slug) {
    const form = h("div", "profile-unlock");
    const box = h("input");
    box.type = "password";
    box.id = `pass-${cssId(row.slug)}`;
    box.placeholder = "passphrase";
    box.autocomplete = "current-password";
    const go = btn("primary tiny", "Unlock");
    const send = () => Profile.open(row.slug, box.value);
    go.onclick = send;
    box.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); send(); } };
    form.appendChild(box);
    form.appendChild(go);
    node.appendChild(form);
  }
  return node;
}

function gameWhen(seconds) {
  if (!seconds) return "";
  const when = new Date(seconds * 1000);
  const today = new Date().toDateString() === when.toDateString();
  return today
    ? "today " + when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : when.toLocaleString([], { month: "short", day: "numeric",
                                hour: "2-digit", minute: "2-digit" });
}

function gameRow(game) {
  const node = h("div", "game-row " + game.result);
  const star = btn("star" + (game.kept ? " on" : ""), game.kept ? "★" : "☆");
  star.title = game.kept
    ? "Kept. Click to let it fall out of the list again."
    : "Keep this game past the last thirty";
  star.onclick = () => Profile.keep(game, !game.kept);
  node.appendChild(star);

  const main = h("div", "game-main");
  main.appendChild(h("div", "game-decks",
    `${game.deck || "a deck"}  vs  ${game.opponent_deck || "a deck"}`));
  const meta = h("div", "game-meta");
  meta.appendChild(h("span", "result", game.result));
  meta.appendChild(h("span", null, gameWhen(game.when)));
  meta.appendChild(h("span", null,
    (game.opponent_name || prettyOpponent(game.opponent))
    + (game.turns ? ` · ${game.turns} turns` : "")));
  // Nothing is more confusing than a game that paid no XP with no reason
  // given, so the reason is on the row rather than in the docs.
  meta.appendChild(h("span", game.xp ? "xp" : "dim",
    game.xp ? `+${game.xp} XP` : "no XP — not a person"));
  main.appendChild(meta);
  node.appendChild(main);

  const tools = h("div", "game-tools");
  if (game.replay) {
    const watch = btn("ghost tiny", "Watch");
    watch.onclick = () => {
      el("#profile").close();
      if (typeof watchReplay === "function") watchReplay({ name: game.replay });
    };
    tools.appendChild(watch);
  } else {
    tools.appendChild(h("span", "dim", "log deleted"));
  }
  const drop = btn("ghost tiny", "Delete");
  drop.title = "Delete this game's log and take it off the list";
  drop.onclick = () => Profile.forget(game);
  tools.appendChild(drop);
  node.appendChild(tools);
  return node;
}

function prettyOpponent(kind) {
  if (kind === "human") return "a person";
  if (typeof prettyPilot === "function") return prettyPilot(kind);
  return kind || "a bot";
}

/* ---------------------------------------------------------------- picture */
/* Card art or your own image. An uploaded file is drawn onto a canvas and read
 * back out at AVATAR_PX, so what is stored is a few kilobytes of PNG rather
 * than the 4MB photo that was picked — the profile is a file that gets read,
 * written and resealed on every finished game. */
const AVATAR_PX = 128;
let newAvatar = "";        // the picture chosen for a profile not made yet

const Avatar = {
  target: "profile",       // "profile" (change the open one) or "new"

  open(target) {
    Avatar.target = target || "profile";
    el("#avatar-q").value = "";
    el("#avatar").showModal();
    Avatar.search();
  },

  async search() {
    const query = new URLSearchParams({ q: el("#avatar-q").value, offset: "0" });
    const data = await api("/api/pool?" + query.toString());
    const grid = el("#avatar-grid");
    grid.innerHTML = "";
    (data.cards || []).slice(0, 60).forEach((card) => {
      const pick = btn("avatar-card");
      pick.style.backgroundImage = `url("${card.img}")`;
      pick.title = `${card.name} (${card.id})`;
      pick.onclick = () => Avatar.chose("card:" + card.id);
      grid.appendChild(pick);
    });
    if (!grid.children.length) {
      grid.appendChild(h("p", "profile-empty",
        "No card art here. Run `python fetch_images.py` to download it, or "
        + "use a picture of your own below."));
    }
  },

  /** Shrink whatever was picked to a small square PNG, in the browser. */
  file(file) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = AVATAR_PX;
        const ctx = canvas.getContext("2d");
        // Cover, not stretch: a portrait photo of a person should not come
        // out as a portrait photo of a person who has been sat on.
        const side = Math.min(img.width, img.height);
        ctx.drawImage(img, (img.width - side) / 2, (img.height - side) / 2,
                      side, side, 0, 0, AVATAR_PX, AVATAR_PX);
        Avatar.chose(canvas.toDataURL("image/png"));
      };
      img.onerror = () => alert("that file is not an image this can read");
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  },

  async chose(value) {
    if (Avatar.target === "new") {
      setNewAvatar(value);
      el("#avatar").close();
      return;
    }
    const res = await api("/api/profile/avatar", { avatar: value });
    el("#avatar").close();
    Profile.take(res);
  },
};

function setNewAvatar(value) {
  newAvatar = value;
  const slot = el("#new-face");
  slot.innerHTML = "";
  slot.appendChild(avatarNode(value, el("#new-name").value || "?"));
}

/* --------------------------------------------------------------- controls */
el("#title-profile").onclick = () =>
  (Profile.me ? Profile.openPane() : Profile.openChooser());
el("#profile-switch").onclick = () => { el("#profile").close(); Profile.openChooser(); };
el("#profile-signout").onclick = () => Profile.close();
el("#profile-delete").onclick = () => Profile.remove();
el("#new-create").onclick = () => Profile.create();
el("#new-face").onclick = () => Avatar.open("new");
el("#new-name").oninput = () => setNewAvatar(newAvatar);
el("#avatar-q").oninput = () => Avatar.search();
el("#avatar-file").onchange = (e) => {
  Avatar.file(e.target.files[0]);
  e.target.value = "";        // so the same file can be picked twice
};

setNewAvatar("");     // the empty circle on the new-profile form
Profile.refresh();
