/* Full-screen deck view — the deck as a picture rather than a list.
 *
 * The deck pane on the right of the builder is a working list: narrow, dense,
 * every row a pair of buttons. That is the wrong shape for showing a deck to
 * somebody else. This is the same list laid out to be looked at — one section
 * per card category, every distinct card at whatever size fits the screen,
 * copies as a badge rather than as repeated rows.
 *
 * Runs after builder.js and reads its state directly (`build`, `TYPE_ORDER`,
 * `countsById`); it never edits it, so nothing here can change a deck by
 * being looked at. Cards are built with app.js's `cardNode`, so hovering one
 * gives the same preview it does everywhere else — the preview sits above
 * this overlay on purpose. */

/* Sections in reading order. EXTRA is not here: it is a separate pile and gets
 * its own section after the 60, the way the deck pane and the export file
 * already write it. */
const SHOWCASE_ORDER = TYPE_ORDER.filter((type) => type !== "EXTRA");

const SHOWCASE_BLURB = {
  COOKIE: "the bodies you play",
  FLIP: "HP that answers back",
  ITEM: "one-shot plays",
  TRAP: "set in response",
  STAGE: "the field itself",
  NPC: "",
  EXTRA: "a second pile, never drawn",
};

const showcase = {
  open: false,
  cardWidth: 118,
  names: true,
};

/* --------------------------------------------------------------- opening */
function openShowcase() {
  if (!build.list.length && !build.extra.length) {
    flash("nothing to show yet — add some cards");
    return;
  }
  showcase.open = true;
  el("#showcase").classList.remove("hidden");
  document.body.classList.add("showcase-open");
  renderShowcase();
}

function closeShowcase() {
  showcase.open = false;
  el("#showcase").classList.add("hidden");
  document.body.classList.remove("showcase-open");
}

/* ------------------------------------------------------------- rendering */
function showcaseDeckName() {
  return el("#deck-name").value.trim() || build.loaded || "Untitled deck";
}

/* Distinct cards in a pile, biggest Level first then alphabetical — the same
 * order the deck pane and the export file use, so all three read alike. */
function showcaseRows(pile, type) {
  return [...countsById(pile).entries()]
    .map(([id, count]) => ({ card: build.cards.get(id), count }))
    .filter((row) => row.card && (!type || row.card.type === type))
    .sort((a, b) => (b.card.level || 0) - (a.card.level || 0)
                    || a.card.name.localeCompare(b.card.name));
}

function showcaseEntry({ card, count }) {
  const entry = h("div", "sc-entry");
  entry.appendChild(cardNode(card));
  if (count > 1) entry.appendChild(h("span", "sc-copies", "×" + count));
  const label = h("div", "sc-name");
  label.appendChild(h("span", "sc-cname", card.name));
  const meta = [card.level ? "LV" + card.level : "", card.id].filter(Boolean).join(" · ");
  label.appendChild(h("span", "sc-cmeta", meta));
  entry.appendChild(label);
  return entry;
}

function showcaseSection(label, rows, note) {
  const copies = rows.reduce((sum, row) => sum + row.count, 0);
  const section = h("section", "sc-group");
  const head = h("h3");
  head.appendChild(h("span", "sc-label", label));
  head.appendChild(h("span", "sc-n", `${copies} card${copies === 1 ? "" : "s"}`));
  if (note) head.appendChild(h("span", "sc-note", note));
  section.appendChild(head);
  const grid = h("div", "sc-grid");
  rows.forEach((row) => grid.appendChild(showcaseEntry(row)));
  section.appendChild(grid);
  return section;
}

/* The colour spread, as a bar. A deck's colours are the first thing anyone
 * looks for and the one thing a list of names does not show. */
function showcaseColors() {
  const counts = new Map();
  [...build.list, ...build.extra].forEach((id) => {
    const card = build.cards.get(id);
    if (!card || !card.color) return;
    counts.set(card.color, (counts.get(card.color) || 0) + 1);
  });
  const total = [...counts.values()].reduce((sum, n) => sum + n, 0);
  const wrap = el("#sc-colors");
  wrap.innerHTML = "";
  if (!total) return;
  [...counts.entries()].sort((a, b) => b[1] - a[1]).forEach(([color, n]) => {
    const pip = h("span", "sc-pip", `${color.toLowerCase()} ${n}`);
    pip.style.setProperty("--pip", DUST[color] || DUST[""]);
    wrap.appendChild(pip);
  });
}

function renderShowcase() {
  if (!showcase.open) return;

  el("#sc-name").textContent = showcaseDeckName();
  el("#showcase").style.setProperty("--sc-card", showcase.cardWidth + "px");
  el("#showcase").classList.toggle("no-names", !showcase.names);

  const size = build.list.length;
  const target = DECK_SIZE();
  const cookies = build.list.filter((id) => isCookie(build.cards.get(id))).length;
  const bits = [`${size}/${target} cards`, `${cookies} cookies`];
  if (build.extra.length) bits.push(`${build.extra.length}/${EXTRA_SIZE()} extra`);
  el("#sc-sub").textContent = bits.join(" · ");
  showcaseColors();

  const body = el("#sc-body");
  body.innerHTML = "";
  SHOWCASE_ORDER.forEach((type) => {
    const rows = showcaseRows(build.list, type);
    if (rows.length) body.appendChild(showcaseSection(type, rows, SHOWCASE_BLURB[type]));
  });
  if (build.extra.length) {
    body.appendChild(showcaseSection("EXTRA DECK", showcaseRows(build.extra),
                                     SHOWCASE_BLURB.EXTRA));
  }
}

/* ---------------------------------------------------------------- controls */
el("#deck-view").onclick = openShowcase;
el("#sc-close").onclick = closeShowcase;

/* Clicking the backdrop closes, clicking a card does not — the gap between
 * sections is a big target and the cards are what you came to look at. */
el("#showcase").addEventListener("click", (e) => {
  if (e.target === el("#showcase") || e.target === el("#sc-body")) closeShowcase();
});

el("#sc-size").addEventListener("input", (e) => {
  showcase.cardWidth = Number(e.target.value);
  el("#showcase").style.setProperty("--sc-card", showcase.cardWidth + "px");
});

el("#sc-names").addEventListener("change", (e) => {
  showcase.names = e.target.checked;
  el("#showcase").classList.toggle("no-names", !showcase.names);
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && showcase.open) closeShowcase();
});
