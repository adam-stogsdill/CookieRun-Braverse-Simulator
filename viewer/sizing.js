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

/* One per region people actually run out of room in — each something you can
 * point at, rather than a global zoom that makes the small things bigger along
 * with the ones you were trying to read. */
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
    id: "builder", prop: "builder-scale", name: "deck builder cards",
    hint: "the card pool and the decklist's thumbnails",
    // Lives in the deck builder's own toolbar rather than in this dialog. It
    // is the one size you cannot see while the dialog is open — the builder is
    // a different tab, not the board behind — so it belongs next to the thing
    // it resizes. Still one of `SIZES`, so it is clamped, saved and reset with
    // the rest; only where its control is drawn differs.
    at: "#pool-size",
    stepper: true,
    // Far wider than the sliders: a pool of 1401 cards is two different jobs —
    // skimming a set at a glance, and reading rules text without a magnifier —
    // and the old 60–175 did neither end properly.
    min: 40, max: 400,
  },
  {
    id: "tilt", prop: "board-tilt", name: "board tilt",
    hint: "how far the two playmats lean away from you",
    // The odd one out, and marked as such: every other row here is a
    // percentage multiplying a printed measurement, this one is an angle in
    // degrees written onto the board as-is. `raw` is what says so — it keeps
    // the number off the /100 in `applySizes`, puts a ° on the readout, and
    // lets the range start at 0, which for a tilt is simply "off" rather than
    // the vanished board the scales' floor exists to prevent.
    raw: true, unit: "\u00b0", step: 1,
    min: 0, max: 24, def: 0,
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

/* A spec may name its own bounds, wider or narrower than the defaults.
 *
 * Widening used to be forbidden, on the grounds that a size you cannot undo is
 * a trap. That still holds for anything whose *own control* is inside what it
 * scales — shrink the side panel far enough and the slider goes with it. The
 * deck builder's zoom is the case it does not hold for: the buttons live in the
 * filter bar, which `--builder-scale` does not touch, so they stay exactly where
 * they were at any zoom. Hence a floor rather than a fixed ceiling — nothing may
 * reach zero, which is the only size there is genuinely no way back from. */
const FLOOR = 10;

/* `raw` rows are exempt from the floor: it guards against a *scale* of zero,
 * and a tilt of zero is the flat board rather than a board that is not there. */
const lowest = (spec) => spec.raw ? (spec.min || 0)
                                  : Math.max(FLOOR, spec.min || MIN);
const highest = (spec) => Math.max(lowest(spec), spec.max || MAX);
const stepOf = (spec) => spec.step || STEP;
const unitOf = (spec) => spec.unit || "%";
const defaultOf = (spec) => (spec.def === undefined ? 100 : spec.def);

/* Built from the specs rather than written out again, so a row added above
 * cannot be left out of what Reset puts back. */
const DEFAULT_SIZES = Object.fromEntries(
  SIZES.map((spec) => [spec.id, defaultOf(spec)]));


function loadSizes() {
  try {
    const saved = JSON.parse(Prefs.get(SIZE_KEY) || "null");
    // Spread over the defaults so a set saved by an older build — one that had
    // fewer sliders, or one written before a slider was renamed — still opens.
    return saved ? { ...DEFAULT_SIZES, ...saved } : { ...DEFAULT_SIZES };
  } catch (err) {
    return { ...DEFAULT_SIZES };
  }
}

function saveSizes() {
  Prefs.set(SIZE_KEY, JSON.stringify(sizes));
}

/* A number from anywhere — an older build, a hand-edited localStorage — is
 * clamped rather than trusted: a scale of 0 is a board that is not there, and
 * nothing in the interface could undo it. */
function clamp(spec, value) {
  const n = Number(value);
  if (!isFinite(n)) return defaultOf(spec);
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
    // Percentages are written as the multiplier the stylesheet multiplies by;
    // a `raw` row is written as itself, and the stylesheet supplies the unit.
    const value = s.raw ? sizes[s.id] : sizes[s.id] / 100;
    document.documentElement.style.setProperty("--" + s.prop, String(value));
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
  input.step = String(stepOf(spec));
  input.value = String(sizes[spec.id]);
  input.id = "size-" + spec.id;
  input.title = spec.hint;
  label.htmlFor = input.id;
  const read = h("span", "size-read", sizes[spec.id] + unitOf(spec));

  input.oninput = () => {
    sizes[spec.id] = clamp(spec, input.value);
    read.textContent = sizes[spec.id] + unitOf(spec);
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
  SIZES.filter((spec) => !spec.at)
       .forEach((spec) => wrap.appendChild(sizeRow(spec)));
}

/* Holding a zoom button should get you across the range without wearing your
 * finger out, and without shooting past the size you wanted in the first
 * quarter second. So a press does one step at once, waits long enough that a
 * click is only ever one step, and then repeats — faster the longer it is
 * held, and in bigger jumps once it is clear this is a journey rather than a
 * nudge. */
const HOLD_DELAY = 350;     // before a press becomes a repeat at all
const REPEAT_FROM = 200;    // first repeat interval
const REPEAT_TO = 40;       // as fast as it ever goes
const RAMP = 1400;          // milliseconds to reach full speed
const LEAP_AFTER = 1200;    // after this long, move in bigger steps

function stepFor(held) {
  return held > LEAP_AFTER ? STEP * 4 : STEP;
}

function intervalFor(held) {
  const eased = Math.min(1, held / RAMP);
  return REPEAT_FROM + (REPEAT_TO - REPEAT_FROM) * eased;
}

/** Wire one +/- button so press-and-hold keeps going. Returns a teardown. */
function holdToRepeat(button, apply) {
  let timer = null;
  let started = 0;

  const tick = () => {
    const held = Date.now() - started;
    apply(stepFor(held));
    timer = setTimeout(tick, intervalFor(held));
  };

  const stop = () => {
    if (timer === null) return;
    clearTimeout(timer);
    timer = null;
    // Saved once, on release: a hold is one decision, and it ends where the
    // finger comes off.
    saveSizes();
  };

  button.addEventListener("pointerdown", (event) => {
    // Left button only, and never the browser's own drag-the-button behaviour.
    if (event.button !== 0) return;
    event.preventDefault();
    // Keeps the hold alive if the finger slides off the button. Guarded
    // because it throws on a pointer id the browser does not know about, and
    // losing the press to an exception would be worse than losing the capture.
    try { button.setPointerCapture(event.pointerId); } catch (err) { /* no capture */ }
    started = Date.now();
    apply(STEP);                       // a click is exactly one step
    timer = setTimeout(tick, HOLD_DELAY);
  });
  // Every way a press can end, including the pointer being taken away by a
  // scroll or the window losing focus mid-hold — a stuck timer here would zoom
  // for ever with nothing pressed.
  for (const done of ["pointerup", "pointercancel", "pointerleave", "blur"]) {
    button.addEventListener(done, stop);
  }
  window.addEventListener("blur", stop);
  return stop;
}

/* Controls that live somewhere else in the page.
 *
 * The container is already in the markup, next to whatever it sizes; its
 * contents and behaviour are built here, so the range and the stepping live in
 * one place rather than being repeated in the HTML where they could drift. */
function wireElsewhere() {
  SIZES.filter((spec) => spec.at).forEach((spec) => {
    const host = el(spec.at);
    if (!host) return;                 // that part of the page is not built
    host.innerHTML = "";

    const step = stepOf(spec);
    const nudge = (by) => {
      const before = sizes[spec.id];
      sizes[spec.id] = clamp(spec, before + by);
      if (sizes[spec.id] === before) return;   // already at the end
      applySizes();
      syncSizeControls();
    };

    const out = h("button", "zoom", "−");
    out.type = "button";
    out.title = "Zoom out — hold to keep going";
    holdToRepeat(out, (by) => nudge(-by * step / STEP));

    const read = h("span", "zoom-read", sizes[spec.id] + unitOf(spec));
    read.id = spec.at.slice(1) + "-read";

    const into = h("button", "zoom", "+");
    into.type = "button";
    into.title = "Zoom in — hold to keep going";
    holdToRepeat(into, (by) => nudge(by * step / STEP));

    host.appendChild(h("span", "zoom-label", "size"));
    host.appendChild(out);
    host.appendChild(read);
    host.appendChild(into);
  });
}

/** Put every control back in step with `sizes`, wherever it is drawn.
 *
 * Deliberately does not rebuild the controls outside the dialog: they are being
 * held down while this runs, and replacing a button mid-press would drop the
 * pointer capture and the hold with it. Only the readout changes. */
function syncSizeControls() {
  renderSizes();
  SIZES.filter((spec) => spec.at).forEach((spec) => {
    const read = el(spec.at + "-read");
    if (read) read.textContent = sizes[spec.id] + unitOf(spec);
    const input = el(spec.at);
    if (input && input.tagName === "INPUT") input.value = String(sizes[spec.id]);
  });
}

renderSizes();
wireElsewhere();

/* Somebody else signed in: their sizes, on the board and on the sliders. */
Prefs.watch(() => {
  const saved = loadSizes();
  SIZES.forEach((spec) => { sizes[spec.id] = clamp(spec, saved[spec.id]); });
  applySizes();
  syncSizeControls();
});

el("#size-reset").onclick = () => {
  Object.assign(sizes, DEFAULT_SIZES);
  saveSizes();
  applySizes();
  // Including the ones outside the dialog: Reset sizes means all of them, and
  // a slider left showing an old number is a slider that lies.
  syncSizeControls();
};
