/* Table tab: sleeves and playmats.
 *
 * Runs after app.js and borrows its helpers (`el`, `h`). Every design here is
 * drawn in CSS — gradients, not image files — for the same reason the rest of
 * the front end has no assets: the viewer stays a folder of text, and the
 * one-file build does not grow by a megabyte per sleeve.
 *
 * A kit is per seat, the way it is across a real table: your sleeves are yours
 * wherever you sit. The choice is written to <body> as `-me-` / `-opp-` custom
 * properties, which style.css maps onto `.side.me` / `.side.opponent`. That is
 * what makes it survive a re-render, a seat swap and a refresh without any of
 * the board code knowing this tab exists.
 */

const KIT_KEY = "braverse.tablekit";

/* A sleeve's design is one whole `background` value, so it can change its
 * pattern and not only what it is painted in. `edge` is the card border,
 * `rim` the inset line just inside it. */
const SLEEVES = [
  {
    id: "cocoa", name: "Cocoa",
    bg: `radial-gradient(circle at 50% 42%, #7b4f2a 0 16%, transparent 17%),
         radial-gradient(circle at 50% 42%, #d8a15c 0 22%, transparent 23%),
         repeating-linear-gradient(45deg, #3c2416 0 5px, #35200f 5px 10px)`,
    edge: "#191008", rim: "rgba(216,161,92,.35)",
  },
  {
    id: "frost", name: "Frost",
    bg: `radial-gradient(circle at 50% 42%, #2b6c93 0 16%, transparent 17%),
         radial-gradient(circle at 50% 42%, #9fd8f2 0 22%, transparent 23%),
         repeating-linear-gradient(45deg, #16394f 0 5px, #102b3c 5px 10px)`,
    edge: "#08161f", rim: "rgba(159,216,242,.38)",
  },
  {
    id: "ember", name: "Ember",
    bg: `radial-gradient(circle at 50% 42%, #94331d 0 16%, transparent 17%),
         radial-gradient(circle at 50% 42%, #f0955c 0 22%, transparent 23%),
         repeating-linear-gradient(45deg, #4c1a0f 0 5px, #3a1109 5px 10px)`,
    edge: "#1d0803", rim: "rgba(240,149,92,.38)",
  },
  {
    id: "verdant", name: "Verdant",
    bg: `radial-gradient(circle at 50% 42%, #2f7141 0 16%, transparent 17%),
         radial-gradient(circle at 50% 42%, #96d9a4 0 22%, transparent 23%),
         repeating-linear-gradient(45deg, #17381f 0 5px, #102a17 5px 10px)`,
    edge: "#071409", rim: "rgba(150,217,164,.36)",
  },
  {
    id: "amethyst", name: "Amethyst",
    bg: `radial-gradient(circle at 50% 42%, #5c3a8e 0 16%, transparent 17%),
         radial-gradient(circle at 50% 42%, #c3a3ef 0 22%, transparent 23%),
         repeating-linear-gradient(45deg, #2e1c4a 0 5px, #241438 5px 10px)`,
    edge: "#120a1d", rim: "rgba(195,163,239,.38)",
  },
  {
    id: "midnight", name: "Midnight",
    bg: `radial-gradient(circle at 50% 42%, #1b1f2c 0 16%, transparent 17%),
         radial-gradient(circle at 50% 42%, #d9b34e 0 22%, transparent 23%),
         repeating-linear-gradient(45deg, #14171f 0 6px, #0d1016 6px 12px)`,
    edge: "#05070b", rim: "rgba(217,179,78,.42)",
  },
  {
    id: "candy", name: "Candy",
    bg: `radial-gradient(circle at 50% 42%, #b8447a 0 16%, transparent 17%),
         radial-gradient(circle at 50% 42%, #ffd3e6 0 22%, transparent 23%),
         repeating-linear-gradient(-45deg, #f095bd 0 6px, #e277a6 6px 12px)`,
    edge: "#6d2247", rim: "rgba(255,211,230,.55)",
  },
  {
    id: "parchment", name: "Parchment",
    bg: `radial-gradient(circle at 50% 42%, #8d7040 0 16%, transparent 17%),
         radial-gradient(circle at 50% 42%, #c8ad78 0 22%, transparent 23%),
         repeating-linear-gradient(45deg, #e8dcc0 0 6px, #ddcfae 6px 12px)`,
    edge: "#8d7c55", rim: "rgba(120,100,62,.45)",
  },
];

/* A mat sets its felt, the dashed zone outlines and the zone labels together —
 * a pale felt wearing the dark felt's outlines is unreadable. `pattern` is an
 * optional extra layer painted over the gradient. */
const MATS = [
  {
    id: "harbour", name: "Harbour", a: "#1d3b4d", b: "#142a38",
    line: "rgba(150,205,235,.16)", label: "rgba(190,225,245,.38)",
  },
  {
    id: "forest", name: "Forest", a: "#1e4231", b: "#122a1f",
    line: "rgba(160,235,190,.16)", label: "rgba(198,245,215,.4)",
  },
  {
    id: "cinder", name: "Cinder", a: "#432220", b: "#2a1614",
    line: "rgba(245,180,165,.15)", label: "rgba(250,205,195,.38)",
  },
  {
    id: "plum", name: "Plum", a: "#3a2550", b: "#241634",
    line: "rgba(210,180,245,.16)", label: "rgba(225,205,250,.4)",
  },
  {
    id: "sand", name: "Sand", a: "#5a4a33", b: "#3b3021",
    line: "rgba(245,225,185,.18)", label: "rgba(250,238,210,.44)",
  },
  {
    id: "slate", name: "Slate", a: "#2f343d", b: "#1e2229",
    line: "rgba(215,225,240,.16)", label: "rgba(225,233,245,.4)",
  },
  {
    id: "checker", name: "Checkerboard", a: "#23303d", b: "#18222c",
    pattern: "repeating-conic-gradient(rgba(255,255,255,.04) 0% 25%, "
             + "transparent 0% 50%) 0 0 / 44px 44px",
    line: "rgba(180,215,240,.17)", label: "rgba(205,230,248,.4)",
  },
  {
    id: "sunburst", name: "Sunburst", a: "#4a3520", b: "#2a1d11",
    pattern: "repeating-conic-gradient(from 0deg at 50% 100%, "
             + "rgba(255,205,120,.055) 0deg 6deg, transparent 6deg 12deg)",
    line: "rgba(250,215,155,.18)", label: "rgba(252,230,190,.42)",
  },
];

/* Different sleeves for the two seats out of the box: telling your cards from
 * your opponent's at a glance is the whole point of sleeving them. */
const DEFAULT_KIT = {
  sleeveMe: "cocoa", sleeveOpp: "frost",
  matMe: "harbour", matOpp: "harbour",
};

const byId = (list, id) => list.find((x) => x.id === id) || list[0];

/* ----------------------------------------------------------- persistence */
function loadKit() {
  try {
    const saved = JSON.parse(Prefs.get(KIT_KEY) || "null");
    // Spread over the defaults so a kit saved by an older build, or one naming
    // a design that has since been renamed, still opens on something valid.
    return saved ? { ...DEFAULT_KIT, ...saved } : { ...DEFAULT_KIT };
  } catch (err) {
    return { ...DEFAULT_KIT };
  }
}

function saveKit() {
  Prefs.set(KIT_KEY, JSON.stringify(kit));
}

const kit = loadKit();

/* ------------------------------------------------------------- applying */
/** The custom properties one seat's choice comes down to. */
function kitVars(sleeveId, matId) {
  const sleeve = byId(SLEEVES, sleeveId);
  const mat = byId(MATS, matId);
  return {
    "sleeve-bg": sleeve.bg,
    "sleeve-edge": sleeve.edge,
    "sleeve-rim": sleeve.rim,
    "felt-a": mat.a,
    "felt-b": mat.b,
    "felt-line": mat.line,
    "felt-label": mat.label,
    "felt-pattern": mat.pattern || "none",
  };
}

/** Paint a kit onto a node. `which` is "me"/"opp" for the board's two-seat
 *  variables, or null to set the plain ones a preview or swatch reads. */
function paint(node, sleeveId, matId, which) {
  const vars = kitVars(sleeveId, matId);
  Object.keys(vars).forEach((name) => {
    const full = which ? name.replace("-", `-${which}-`) : name;
    node.style.setProperty("--" + full, vars[name]);
  });
}

/** Put the current kit on the board. Safe to call at any time. */
function applyKit() {
  paint(document.body, kit.sleeveMe, kit.matMe, "me");
  paint(document.body, kit.sleeveOpp, kit.matOpp, "opp");
}

applyKit();

/* ------------------------------------------------------------ the swatches */
/* Every sample is the real thing at a smaller size — a `.card.back` for a
 * sleeve, the mat's own background for a playmat — so nothing shown here can
 * drift from what the board does with the same choice. */
function sleeveSwatch(sleeve) {
  const node = h("div", "swatch");
  paint(node, sleeve.id, kit.matMe, null);
  node.appendChild(h("div", "card back"));
  node.appendChild(h("span", "swatch-name", sleeve.name));
  return node;
}

function matSwatch(mat) {
  const node = h("div", "swatch");
  paint(node, kit.sleeveMe, mat.id, null);
  node.appendChild(h("div", "swatch-felt"));
  node.appendChild(h("span", "swatch-name", mat.name));
  return node;
}

/** One seat's column: a live preview, then the two racks of choices. */
function renderSeatKit(which) {
  const wrap = el(which === "me" ? "#kit-me" : "#kit-opp");
  const sleeveKey = which === "me" ? "sleeveMe" : "sleeveOpp";
  const matKey = which === "me" ? "matMe" : "matOpp";
  wrap.innerHTML = "";

  const preview = h("div", "kit-preview");
  paint(preview, kit[sleeveKey], kit[matKey], null);
  const row = h("div", "kit-row");
  for (let i = 0; i < 5; i++) row.appendChild(h("div", "card back mid"));
  preview.appendChild(row);
  preview.appendChild(h("span", "kit-zlabel", "battle area"));
  wrap.appendChild(preview);

  const rack = (title, items, chosen, swatchOf, pick) => {
    wrap.appendChild(h("h4", "kit-h", title));
    const grid = h("div", "swatches");
    items.forEach((item) => {
      const node = swatchOf(item);
      node.classList.toggle("on", item.id === chosen);
      node.title = item.name;
      node.onclick = () => { pick(item.id); saveKit(); applyKit(); renderTableKit(); };
      grid.appendChild(node);
    });
    wrap.appendChild(grid);
  };

  rack("Sleeve", SLEEVES, kit[sleeveKey], sleeveSwatch, (id) => { kit[sleeveKey] = id; });
  rack("Playmat", MATS, kit[matKey], matSwatch, (id) => { kit[matKey] = id; });
}

function renderTableKit() {
  renderSeatKit("me");
  renderSeatKit("opp");
}

/* Somebody else signed in: their sleeves and mats, on the board and on the
 * racks. `loadKit` rather than the saved object itself, so a kit written by an
 * older build still lands on something valid. */
Prefs.watch(() => {
  Object.assign(kit, loadKit());
  applyKit();
  renderTableKit();
});

el("#kit-reset").onclick = () => {
  Object.assign(kit, DEFAULT_KIT);
  saveKit();
  applyKit();
  renderTableKit();
};

el("#kit-swap").onclick = () => {
  Object.assign(kit, {
    sleeveMe: kit.sleeveOpp, sleeveOpp: kit.sleeveMe,
    matMe: kit.matOpp, matOpp: kit.matMe,
  });
  saveKit();
  applyKit();
  renderTableKit();
};
