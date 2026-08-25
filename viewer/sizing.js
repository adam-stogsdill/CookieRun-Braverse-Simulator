/* Settings → Sizes: how big the board is drawn.
 *
 * Runs after app.js and borrows its helpers (`el`, `h`). Every control here
 * comes down to one number written onto <html> as a custom property, which
 * style.css multiplies its printed measurements by — so nothing that draws the
 * table, the panel or an overlay has to know this file exists, and a size
 * survives a re-render, a seat swap and a refresh for free.
 *
 * The sliders take effect as they are dragged rather than on Done. The dialog
 * is a panel over the board, not a page away from it: you can see most of what
 * you are sizing while you size it, which is the only way to land on a number
 * that is right for a particular screen.
 */

const SIZE_KEY = "braverse.sizes";

/* Four things, because they are the four that people run out of room in — and
 * because each is a region you can point at, rather than a global zoom that
 * makes the small things bigger along with the ones you were reading. */
const SIZES = [
  {
    id: "battle", prop: "battle-scale", name: "battle area cards",
    hint: "the Cookies and their HP piles",
  },
  {
    id: "card", prop: "card-scale", name: "hand & support cards",
    hint: "your hand, the support and stage rows, the stacks",
  },
  {
    id: "mat", prop: "mat-scale", name: "playmat width",
    hint: "how far the mat may spread on a wide screen",
  },
  {
    id: "panel", prop: "panel-scale", name: "side panel",
    hint: "the card viewer, the moves and the log",
    // Narrower than the rest: the panel takes its width out of the table's,
    // and past about half again there is no table left to take it from.
    min: 80, max: 150,
  },
];

/* Percentages, because that is what the readout says. The range is wide enough
 * to matter on a laptop and on a television, and narrow enough that nothing
 * can be set to a size it cannot be found and undone at. A slider may narrow
 * it further; none may widen it. */
const MIN = 60;
const MAX = 175;
const STEP = 5;

const lowest = (spec) => Math.max(MIN, spec.min || MIN);
const highest = (spec) => Math.min(MAX, spec.max || MAX);

const DEFAULT_SIZES = { battle: 100, card: 100, mat: 100, panel: 100 };

function loadSizes() {
  try {
    const saved = JSON.parse(localStorage.getItem(SIZE_KEY) || "null");
    // Spread over the defaults so a set saved by an older build — one that had
    // fewer sliders, or one written before a slider was renamed — still opens.
    return saved ? { ...DEFAULT_SIZES, ...saved } : { ...DEFAULT_SIZES };
  } catch (err) {
    return { ...DEFAULT_SIZES };
  }
}

function saveSizes() {
  try {
    localStorage.setItem(SIZE_KEY, JSON.stringify(sizes));
  } catch (err) { /* private browsing: the sizes just will not survive a refresh */ }
}

/* A number from anywhere — an older build, a hand-edited localStorage — is
 * clamped rather than trusted: a scale of 0 is a board that is not there, and
 * nothing in the interface could undo it. */
function clamp(spec, value) {
  const n = Number(value);
  if (!isFinite(n)) return 100;
  return Math.min(highest(spec), Math.max(lowest(spec), Math.round(n)));
}

const sizes = loadSizes();
SIZES.forEach((spec) => { sizes[spec.id] = clamp(spec, sizes[spec.id]); });

/** Put the current sizes on the board. Safe to call at any time. */
function applySizes() {
  SIZES.forEach((s) => {
    // On <html>, not <body>: the sizes it feeds — `--card-w`, `--panel-w` and
    // the rest — are declared on `:root`, and a custom property is substituted
    // where it is *declared*. Set one seat lower and `:root` would go on
    // reading the 1 it declares itself, so the cards never move.
    document.documentElement.style.setProperty("--" + s.prop, String(sizes[s.id] / 100));
  });
}

applySizes();

/* ------------------------------------------------------------- the sliders */
function sizeRow(spec) {
  const row = h("div", "size-row");
  const label = h("label", "size-name", spec.name);
  const input = document.createElement("input");
  input.type = "range";
  input.min = String(lowest(spec));
  input.max = String(highest(spec));
  input.step = String(STEP);
  input.value = String(sizes[spec.id]);
  input.id = "size-" + spec.id;
  input.title = spec.hint;
  label.htmlFor = input.id;
  const read = h("span", "size-read", sizes[spec.id] + "%");

  input.oninput = () => {
    sizes[spec.id] = clamp(spec, input.value);
    read.textContent = sizes[spec.id] + "%";
    applySizes();
  };
  // Saved on release rather than on every step: a drag is one decision, and
  // one that ends where the slider is let go of.
  input.onchange = saveSizes;

  row.appendChild(label);
  row.appendChild(input);
  row.appendChild(read);
  return row;
}

function renderSizes() {
  const wrap = el("#size-rows");
  wrap.innerHTML = "";
  SIZES.forEach((spec) => wrap.appendChild(sizeRow(spec)));
}

renderSizes();

el("#size-reset").onclick = () => {
  Object.assign(sizes, DEFAULT_SIZES);
  saveSizes();
  applySizes();
  renderSizes();
};
