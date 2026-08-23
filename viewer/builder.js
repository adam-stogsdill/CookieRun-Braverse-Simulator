/* Deck builder tab.
 *
 * Runs after app.js and borrows its helpers (`el`, `h`, `api`, `cardNode`), so
 * a card in the builder previews and renders exactly as it does on the table.
 *
 * The list is held here as one entry per copy — the same shape the engine and
 * `/api/decks/save` take — and the server stays the authority on legality: the
 * pane shows counts as you click, then `POST /api/deck/validate` says whether
 * the thing is playable. */

const build = {
  list: [],             // card ids, one per copy
  extra: [],            // the EXTRA deck, a separate pile of its own
  cards: new Map(),     // id -> card json, so a row can render without a fetch
  meta: null,           // sets/types/colours and the deck-building rules
  offset: 0,
  total: 0,
  limit: 120,
  page: [],             // the cards on screen, so copy counts redraw for free
  loaded: null,         // name this list came from, if any
  dirty: false,
  note: "",             // transient message under the meter
};

const TYPE_ORDER = ["COOKIE", "FLIP", "EXTRA", "ITEM", "TRAP", "STAGE", "NPC"];
const DECK_SIZE = () => (build.meta && build.meta.rules.deckSize) || 60;
const MAX_COPIES = () => (build.meta && build.meta.rules.maxCopies) || 4;
const EXTRA_SIZE = () => (build.meta && build.meta.rules.extraSize) || 10;
/* An EXTRA card is played out of its own pile and is never drawn, so clicking
 * one in the pool has to add it there rather than to the 60. */
const isExtra = (card) => !!card && card.type === "EXTRA";

/* ------------------------------------------------------------------- tabs */
/* One list, so a new tab is one entry rather than a new pair of toggles to
 * keep in step. "play" owns no body class: it is what is left when no other
 * view has claimed the second row. */
const TABS = [
  { name: "play", button: "#tab-play" },
  { name: "build", button: "#tab-build", body: "view-build" },
  { name: "table", button: "#tab-table", body: "view-table" },
  { name: "replays", button: "#tab-replays", body: "view-replays" },
];

/** Is the board itself what is on screen, rather than one of the other tabs?
 *
 * The overlays that belong to the play view — the title screen and the
 * end-of-match card — are drawn over the whole window, so they have to know
 * to stay out of a tab that has taken it over. */
function onPlayTab() {
  return !TABS.some((tab) => tab.body && document.body.classList.contains(tab.body));
}

function showTab(name) {
  TABS.forEach((tab) => {
    if (tab.body) document.body.classList.toggle(tab.body, tab.name === name);
    el(tab.button).classList.toggle("on", tab.name === name);
  });
  // The hover preview is docked in the play panel, and the other two tabs hide
  // that panel — so it goes back to following the cursor there. app.js owns it.
  if (typeof restorePreview === "function") restorePreview();
  // A finished match's card is not this tab's business. title.js is loaded
  // after this file, so it is checked for rather than assumed.
  if (typeof Title !== "undefined" && !onPlayTab()) Title.hideOver();
  if (name === "build" && !build.meta) openBuilder();
  // table.js defines this; it is loaded after this file, so it is checked for
  // rather than assumed.
  if (name === "table" && typeof renderTableKit === "function") renderTableKit();
  // replays.js, loaded after this file, so it is checked for rather than
  // assumed — the same way the sleeves tab is.
  if (name === "replays" && typeof refreshReplays === "function") refreshReplays();
}

TABS.forEach((tab) => { el(tab.button).onclick = () => showTab(tab.name); });

async function openBuilder() {
  await Promise.all([searchPool(), refreshDeckList()]);
  renderDeck();
}

/* ------------------------------------------------------------- the pool */
function poolQuery() {
  const params = new URLSearchParams();
  const q = el("#pool-q").value.trim();
  if (q) params.set("q", q);
  if (el("#pool-type").value) params.set("type", el("#pool-type").value);
  if (el("#pool-color").value) params.set("color", el("#pool-color").value);
  if (el("#pool-set").value) params.set("set", el("#pool-set").value);
  if (el("#pool-playable").checked) params.set("playable", "1");
  params.set("offset", String(build.offset));
  return params.toString();
}

async function searchPool() {
  const data = await api("/api/pool?" + poolQuery());
  if (!build.meta) {
    build.meta = { sets: data.sets, types: data.types, colors: data.colors, rules: data.rules };
    fillFilter("#pool-type", data.types);
    fillFilter("#pool-color", data.colors);
    fillFilter("#pool-set", data.sets);
  }
  build.total = data.total;
  build.limit = data.limit || build.limit;
  build.offset = data.offset;
  build.page = data.cards;
  data.cards.forEach((card) => build.cards.set(card.id, card));
  renderPool();
}

function fillFilter(sel, values) {
  const node = el(sel);
  values.forEach((v) => {
    const opt = h("option", null, v);
    opt.value = v;
    node.appendChild(opt);
  });
}

function renderPool() {
  const cards = build.page;
  const have = countsByBase();
  const grid = el("#pool-grid");
  grid.innerHTML = "";
  cards.forEach((card) => {
    const entry = h("div", "entry");
    entry.dataset.id = card.id;
    entry.appendChild(cardNode(card));
    entry.appendChild(h("div", "cname", card.name));
    entry.onclick = () => addCard(card);
    grid.appendChild(entry);
  });
  markCopies(have);
  if (!cards.length) grid.appendChild(h("div", "deck-hint", "nothing matches"));

  const first = build.total ? build.offset + 1 : 0;
  const last = Math.min(build.offset + cards.length, build.total);
  el("#pool-count").textContent = build.total
    ? `${build.total} card${build.total === 1 ? "" : "s"} match`
    : "no cards match";
  el("#pool-page").textContent = build.total ? `${first}–${last} of ${build.total}` : "";
  el("#pool-prev").disabled = build.offset <= 0;
  el("#pool-next").disabled = last >= build.total;
}

/* How many copies each card on screen already has, drawn onto the page in
   place: adding a card must not rebuild a grid of 120 images under the cursor. */
function markCopies(have = countsByBase()) {
  el("#pool-grid").querySelectorAll(".entry").forEach((entry) => {
    const card = build.cards.get(entry.dataset.id);
    if (!card) return;
    const n = have.get(card.baseId) || 0;
    const full = n >= MAX_COPIES();
    entry.classList.toggle("full", full);
    entry.title = full
      ? `${card.name} — already at ${MAX_COPIES()} copies of ${card.baseId}`
      : `add ${card.name}`;
    let badge = entry.querySelector(".have");
    if (!n) { if (badge) badge.remove(); return; }
    if (!badge) {
      badge = h("span", "have");
      entry.insertBefore(badge, entry.firstChild.nextSibling);
    }
    badge.textContent = n;
  });
}

let searchTimer = null;
function searchSoon(resetPage = true) {
  if (resetPage) build.offset = 0;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(searchPool, 180);
}

el("#pool-q").addEventListener("input", () => searchSoon());
["#pool-type", "#pool-color", "#pool-set", "#pool-playable"].forEach((sel) => {
  el(sel).addEventListener("change", () => searchSoon());
});
el("#pool-prev").onclick = () => {
  build.offset = Math.max(0, build.offset - build.limit);
  searchPool();
};
el("#pool-next").onclick = () => {
  build.offset += build.limit;
  searchPool();
};

/* --------------------------------------------------------------- the deck */
/* The copy limit counts card numbers you own, so it spans both piles. */
function countsByBase() {
  const counts = new Map();
  [...build.list, ...build.extra].forEach((id) => {
    const card = build.cards.get(id);
    const key = card ? card.baseId : id;
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  return counts;
}

function addCard(card) {
  const extra = isExtra(card);
  const pile = extra ? build.extra : build.list;
  const cap = extra ? EXTRA_SIZE() : DECK_SIZE();
  if (pile.length >= cap) {
    return flash(extra ? `EXTRA deck is full at ${cap} cards`
                       : `deck is full at ${cap} cards`);
  }
  if ((countsByBase().get(card.baseId) || 0) >= MAX_COPIES()) {
    return flash(`${MAX_COPIES()} copies of ${card.baseId} already`);
  }
  build.cards.set(card.id, card);
  pile.push(card.id);
  touched();
}

function removeCard(id) {
  const pile = isExtra(build.cards.get(id)) ? build.extra : build.list;
  const at = pile.lastIndexOf(id);
  if (at < 0) return;
  pile.splice(at, 1);
  touched();
}

function touched() {
  build.dirty = true;
  renderDeck();
  markCopies();          // the pool page on screen keeps its nodes
}

/* A short message in the status line — "deck is full", "saved" — that holds
   the line for a couple of seconds before legality takes it back. */
function flash(message) {
  build.note = message;
  const status = el("#deck-status");
  status.className = "deck-status";
  status.textContent = message;
  setTimeout(() => {
    if (build.note !== message) return;
    build.note = "";
    validateSoon();
  }, 2200);
}

function renderDeck() {
  const size = build.list.length;
  const target = DECK_SIZE();
  const fill = el("#deck-fill");
  fill.style.width = Math.min(100, (size / target) * 100) + "%";
  fill.className = size === target ? "done" : size > target ? "over" : "";

  // Group for reading: Cookies first, then the support cards.
  const groups = new Map();
  countsById(build.list).forEach((count, id) => {
    const card = build.cards.get(id);
    if (!card) return;
    const bucket = groups.get(card.type) || [];
    bucket.push({ card, count });
    groups.set(card.type, bucket);
  });

  const listNode = el("#decklist");
  listNode.innerHTML = "";
  TYPE_ORDER.filter((t) => groups.has(t)).forEach((type) => {
    const rows = groups.get(type).sort((a, b) =>
      (b.card.level || 0) - (a.card.level || 0) || a.card.name.localeCompare(b.card.name));
    const n = rows.reduce((sum, r) => sum + r.count, 0);
    listNode.appendChild(h("div", "group", `${type} · ${n}`));
    rows.forEach(({ card, count }) => listNode.appendChild(deckRow(card, count)));
  });
  if (!size) listNode.appendChild(h("div", "deck-hint", "empty — click cards on the left"));

  /* The EXTRA deck is listed under the 60 rather than mixed into it: they are
   * two piles on the table, filled and capped separately. */
  if (build.extra.length) {
    listNode.appendChild(h("div", "group",
      `EXTRA DECK · ${build.extra.length}/${EXTRA_SIZE()}`));
    const rows = [...countsById(build.extra).entries()]
      .map(([id, count]) => ({ card: build.cards.get(id), count }))
      .filter((r) => r.card)
      .sort((a, b) => a.card.name.localeCompare(b.card.name));
    rows.forEach(({ card, count }) => listNode.appendChild(deckRow(card, count)));
  }

  const cookies = build.list.filter((id) => isCookie(build.cards.get(id))).length;
  const flips = build.list.filter((id) => build.cards.get(id) &&
    build.cards.get(id).type === "FLIP").length;
  const maxFlip = (build.meta && build.meta.rules.maxFlip) || 16;
  const mix = el("#deck-mix");
  mix.innerHTML = "";
  const line = [`${size}/${target} cards`, `${cookies} cookies`,
                `${flips}/${maxFlip} flip`];
  if (build.extra.length) line.push(`${build.extra.length}/${EXTRA_SIZE()} extra`);
  line.forEach((text) => mix.appendChild(h("span", null, text)));

  // showcase.js is loaded after this file, so it is checked for rather than
  // assumed — the full-screen view keeps up with edits made behind it.
  if (typeof renderShowcase === "function") renderShowcase();

  validateSoon();
}

function isCookie(card) {
  return !!card && (card.type === "COOKIE" || card.type === "FLIP" || card.type === "EXTRA");
}

function countsById(pile = build.list) {
  const counts = new Map();
  pile.forEach((id) => counts.set(id, (counts.get(id) || 0) + 1));
  return counts;
}

function deckRow(card, count) {
  const row = h("div", "row");
  const node = cardNode(card);
  node.title = "remove one";
  node.onclick = () => removeCard(card.id);
  row.appendChild(node);
  row.appendChild(h("span", "rname", card.name));
  row.appendChild(h("span", "rmeta", card.level ? "LV" + card.level : card.id));
  const minus = h("button", "ghost tiny", "−");
  minus.onclick = () => removeCard(card.id);
  const plus = h("button", "ghost tiny", "+");
  plus.onclick = () => addCard(card);
  row.appendChild(minus);
  row.appendChild(h("span", "n", "×" + count));
  row.appendChild(plus);
  return row;
}

/* Legality is the server's call — it runs the same `validate` the engine does
   when a match starts, so the builder can never bless a deck the game refuses. */
let validateTimer = null;
function validateSoon() {
  clearTimeout(validateTimer);
  validateTimer = setTimeout(async () => {
    const data = await api("/api/deck/validate",
                           { cards: build.list, extra: build.extra });
    if (build.note) return;     // a flash message owns the line right now
    const status = el("#deck-status");
    status.className = "deck-status " + (data.legal ? "good" : "bad");
    status.textContent = data.legal
      ? "legal — ready to play"
      : data.problems.join(" · ") || "not legal yet";
  }, 220);
}

/* ------------------------------------------------------- saving & loading */
async function refreshDeckList(selected) {
  const data = await api("/api/decks");
  const node = el("#deck-load");
  node.innerHTML = "";
  const head = h("option", null, "load a deck…");
  head.value = "";
  node.appendChild(head);
  data.decks.forEach((deck) => {
    const tag = deck.source === "saved" ? "" : ` (${deck.source})`;
    const opt = h("option", null, `${deck.name} · ${deck.size}${tag}`);
    opt.value = deck.name;
    node.appendChild(opt);
  });
  node.value = selected || "";
}

el("#deck-load").addEventListener("change", async (e) => {
  const name = e.target.value;
  if (!name) return;
  if (!confirmDiscard()) { e.target.value = build.loaded || ""; return; }
  const data = await api("/api/deck?name=" + encodeURIComponent(name));
  if (data.error) { alert(data.error); return; }
  data.cards.forEach((card) => build.cards.set(card.id, card));
  (data.extra || []).forEach((card) => build.cards.set(card.id, card));
  build.list = data.list.slice();
  build.extra = (data.extraList || []).slice();
  build.loaded = data.source === "saved" ? name : null;
  build.dirty = false;
  // A starter or generated list opens as a copy, so editing one cannot
  // silently shadow the deck it came from.
  el("#deck-name").value = data.source === "saved" ? name : name + " copy";
  renderDeck();
  searchPool();
});

function confirmDiscard() {
  return !build.dirty || confirm("Discard unsaved changes to this deck?");
}

el("#deck-save").onclick = async () => {
  const name = el("#deck-name").value.trim();
  if (!name) { flash("give the deck a name first"); el("#deck-name").focus(); return; }
  const data = await api("/api/decks/save",
                        { name, cards: build.list, extra: build.extra });
  if (data.error) { alert(data.error); return; }
  build.loaded = name;
  build.dirty = false;
  await refreshDeckList(name);
  flash(data.legal ? `saved "${name}"` : `saved "${name}" — not legal yet`);
  loadConfig();          // the New match dropdowns pick the deck up at once
};

el("#deck-new").onclick = () => {
  if (!confirmDiscard()) return;
  build.list = [];
  build.extra = [];
  build.loaded = null;
  build.dirty = false;
  el("#deck-name").value = "";
  el("#deck-load").value = "";
  renderDeck();
  searchPool();
};

el("#deck-delete").onclick = async () => {
  const name = build.loaded;
  if (!name) { flash("only a saved deck can be deleted"); return; }
  if (!confirm(`Delete the saved deck "${name}"?`)) return;
  const data = await api("/api/decks/delete", { name });
  if (data.error) { alert(data.error); return; }
  build.loaded = null;
  build.dirty = true;
  await refreshDeckList();
  flash(`deleted "${name}"`);
  loadConfig();
};

el("#deck-copy").onclick = async () => {
  const lines = [...countsById().entries()].map(([id, n]) => {
    const card = build.cards.get(id);
    return `${n} ${id} ${card ? card.name : ""}`.trim();
  });
  const text = lines.join("\n");
  try {
    await navigator.clipboard.writeText(text);
    flash(`copied ${build.list.length} cards`);
  } catch {
    flash("clipboard blocked — the list is in the console");
    console.log(text);
  }
};

/* Export: the same list the Copy button puts on the clipboard, but as a file
 * laid out in sections the way a decklist is written out — a `--TYPE--` header,
 * then a line per distinct card with its copies, name, ID and Level. The EXTRA
 * deck gets its own section at the end: it is part of the deck you register,
 * and a separate pile on the table. Cards with no printed Level (items, traps,
 * stages) simply end after the ID. */
const SECTION_LABEL = {
  COOKIE: "COOKIE", FLIP: "FLIP", EXTRA: "EXTRA",
  ITEM: "ITEM", TRAP: "TRAP", STAGE: "STAGE", NPC: "NPC",
};

function exportSections() {
  const sections = [];
  const collect = (pile, order) => {
    const entries = [...countsById(pile).entries()]
      .map(([id, count]) => ({ card: build.cards.get(id), id, count }))
      .filter((r) => r.card);
    order.forEach((type) => {
      // Listed the way the pane lists it, so the file reads like the screen.
      const rows = entries.filter((r) => r.card.type === type)
        .sort((a, b) => (b.card.level || 0) - (a.card.level || 0)
                        || a.card.name.localeCompare(b.card.name))
        .map(({ card, id, count }) => [
          `${count}x`, card.name, id, card.level ? "LV" + card.level : "",
        ].filter(Boolean).join(" "));
      if (rows.length) sections.push({ label: SECTION_LABEL[type] || type, rows });
    });
  };
  collect(build.list, TYPE_ORDER.filter((t) => t !== "EXTRA"));
  collect(build.extra, ["EXTRA"]);
  return sections;
}

function deckFileName() {
  const name = el("#deck-name").value.trim() || build.loaded || "decklist";
  return name.replace(/[^\w.-]+/g, "_") + ".txt";
}

el("#deck-export").onclick = () => {
  const sections = exportSections();
  if (!sections.length) { flash("nothing to export yet"); return; }
  const text = sections
    .map((section) => `--${section.label}--\n${section.rows.join("\n")}\n`)
    .join("\n");
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const link = h("a");
  link.href = url;
  link.download = deckFileName();
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoked late: Safari reads the blob after the click returns.
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  const lines = sections.reduce((sum, s) => sum + s.rows.length, 0);
  flash(`exported ${lines} cards (${build.list.length + build.extra.length} copies)`);
};

el("#deck-name").addEventListener("input", () => { build.dirty = true; });

window.addEventListener("beforeunload", (e) => {
  if (build.dirty) { e.preventDefault(); e.returnValue = ""; }
});
