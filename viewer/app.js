/* Visual player for the Braverse engine.
 *
 * The server owns the game; this file is a renderer plus a question-answerer.
 * Everything the browser can do is: poll /api/state, POST an option index back,
 * and nudge the pacing controls. Hidden information is filtered server-side, so
 * nothing secret is ever in this page's memory to leak. */

const el = (sel) => document.querySelector(sel);
const state = {
  snap: null,
  version: -1,
  build: "",            // the server build this page first heard from
  staleShown: false,    // the "you are on an older page" bar, shown once
  pendingId: null,
  config: { decks: [], pilots: [] },
  busy: false,
  browsing: null,       // {seat, zone} while the trash/break browser is open
  animating: false,     // a scene is playing; the board is a beat behind
  queuedSnap: null,     // newest state, waiting for the scene to finish
  restState: new Map(), // uid -> rested, as the board was last drawn
  restSeen: new Map(),  // the same, being rebuilt by the render in progress
  turning: 0,           // cards turning in this render, for the sweep stagger
  picked: [],           // indices chosen in a multi-card pick
  eventId: 0,           // last event batch played
  announced: false,     // win chime fired for this match
  lastMatch: null,      // the body of the last local /api/new, for a rematch
  /* Online play. `mySeat` is which side of the table this browser sits on —
   * 0 for a local match, and whichever seat the room handed out otherwise. It
   * is the only thing that decides which mat is drawn at the bottom and which
   * cards can be picked up, so nothing below should compare a seat to 0. */
  mySeat: 0,
  room: null,           // room code, when playing someone over the network
  token: null,          // this seat's key to it
  key: null,            // the room's own key, carried by the invite link
  lobby: null,          // room status while waiting for an opponent
  /* A peer-to-peer game: no room, no host, and the other seat's every answer
   * arriving over a data channel this browser is only relaying. `netplay.js`
   * owns the connection; all this decides is which seat the server is asked
   * about, since a peer server plays exactly one. */
  peer: false,
};

/* The room outlives the tab: a refresh, or a laptop lid, must come back to the
 * same seat rather than to a spectator's view of your own game. */
const Seat = {
  key: (room) => "braverse.seat." + room,
  save(room, seat, token, pass) {
    try {
      localStorage.setItem(Seat.key(room), JSON.stringify({ seat, token, pass }));
    } catch (err) { /* private browsing: the seat just will not survive a refresh */ }
  },
  load(room) {
    try {
      return JSON.parse(localStorage.getItem(Seat.key(room)) || "null");
    } catch (err) { return null; }
  },
  forget(room) {
    try { localStorage.removeItem(Seat.key(room)); } catch (err) { /* as above */ }
  },
};

/** Is this browser the one that answers for `state.mySeat`? */
function playableSeat() {
  if (!state.snap) return false;
  if (state.room) return state.snap.seat === state.mySeat;
  return (state.snap.humanSeats || []).includes(state.mySeat);
}

/** How to name a seat: the person in it online, the pilot driving it locally. */
function seatLabel(seat, snap) {
  const room = (snap && snap.room) || state.lobby;
  if (room) {
    const name = (room.seats[seat] || {}).name || `seat ${seat}`;
    return seat === state.mySeat && snap && snap.seat !== null ? `${name} (you)` : name;
  }
  // In a replay every seat's pilot is "replay", which says nothing about the
  // game being watched — so name whoever actually played it.
  const pilots = (snap && snap.replay && snap.replay.pilots)
    || (snap && snap.pilots) || [];
  return `seat ${seat} · ${prettyPilot(pilots[seat] || "")}`;
}

/* Seat the viewer at the bottom of the table.
 *
 * The board is two fixed sections, and everything about how a mat is drawn —
 * the flip, the hand along the bottom edge, which cards can be picked up —
 * hangs off `.me` and `.opponent`. Rather than teach all of that about seat
 * numbers, the two sections trade places and classes, so seat 1 sees exactly
 * the layout seat 0 does, from the other side. */
function seatPerspective(seat) {
  state.mySeat = seat;
  const table = el("#table");
  const mine = el("#side-" + seat);
  const theirs = el("#side-" + (1 - seat));
  mine.className = "side me";
  theirs.className = "side opponent";
  table.insertBefore(theirs, table.firstChild);
  table.insertBefore(mine, el(".middle").nextSibling);
}

/* ------------------------------------------------------------------ utils */
function h(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) setText(node, text);
  return node;
}

/* ------------------------------------------------------------- energy pips */
/* Rules text writes energy as `{G}`, and so does every prompt and option label
 * built out of it. Six braces in a row is not a cost anyone can read at a
 * glance, so every one of them is drawn as the coloured gem the card prints.
 *
 * It goes in `h` rather than at each call site: the tokens turn up in card
 * text, attack costs, action labels, the card menu, the deck builder and the
 * prompt line, and a substitution that covers only some of those is worse than
 * none — the reader stops trusting which is which. */
const ENERGY_NAMES = {
  R: "red", B: "blue", G: "green", Y: "yellow",
  P: "purple", K: "black", N: "any colour",
};
// Split rather than exec: `energyPip` builds nodes through `h`, which comes
// back through `setText`, and a shared /g/ regex has a `lastIndex` that the
// nested call resets — which sends the outer loop back to the start of the
// string, forever. A split has no state to trample.
const ENERGY_TOKEN = /(\{[RBGYPKN]\})/;

function energyPip(symbol) {
  const pip = document.createElement("span");
  pip.className = "energy e-" + symbol;
  pip.title = `{${symbol}} — ${ENERGY_NAMES[symbol]} energy`;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 20 20");
  svg.setAttribute("aria-hidden", "true");
  const gem = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
  gem.setAttribute("points", "10,0.8 18.9,5.9 18.9,14.1 10,19.2 1.1,14.1 1.1,5.9");
  svg.appendChild(gem);
  const shine = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
  shine.setAttribute("class", "shine");
  shine.setAttribute("points", "10,3.4 16.3,7 10,10.6 3.7,7");
  svg.appendChild(shine);
  pip.appendChild(svg);
  // The letter as well as the colour: six shades of gem is a lot to tell apart
  // at 14px, and some of the people reading this cannot tell two of them apart
  // at any size.
  const letter = document.createElement("b");
  letter.textContent = symbol;
  pip.appendChild(letter);
  return pip;
}

/** Put `text` into `node`, drawing any `{X}` energy tokens as pips. */
function setText(node, text) {
  const value = text === null || text === undefined ? "" : String(text);
  if (!ENERGY_TOKEN.test(value)) {
    node.textContent = value;
    return node;
  }
  node.textContent = "";
  // The capture group keeps the separators, so the pieces alternate text,
  // token, text, token, …
  value.split(new RegExp(ENERGY_TOKEN.source, "g")).forEach((piece) => {
    if (!piece) return;
    if (ENERGY_TOKEN.test(piece) && piece.length === 3) {
      node.appendChild(energyPip(piece[1]));
    } else {
      node.appendChild(document.createTextNode(piece));
    }
  });
  return node;
}

async function api(path, body) {
  const opts = body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  const data = await res.json();
  if (data && data.build) checkBuild(data.build);
  return data;
}

/* Is this page still the build the server is running?
 *
 * Every JSON answer names the build that sent it, so this is checked on
 * whatever call the page was already making rather than on a call of its own.
 *
 * It matters most when someone joins a room: a room is two browsers on *one*
 * engine — the joiner plays on the host's server — so the two seats cannot be
 * running different rules the way two peers can. What they can be running is
 * different *pages*, when a tab was left open across an upgrade and is now
 * asking a newer server questions in an older dialect. That is not something
 * to guess at fixing: say so, and offer the reload. */
function checkBuild(build) {
  if (!state.build) { state.build = build; return; }
  if (build === state.build || state.staleShown) return;
  state.staleShown = true;
  const bar = el("#stale");
  setText(el("#stale-text"),
          `This page is from version ${state.build}; the game is now running `
          + `${build}. Reload before playing so both sides are the same build.`);
  bar.classList.remove("hidden");
}

/* Turning a card, rather than snapping it.
 *
 * The board is rebuilt from scratch on every commit, so the node is new and a
 * CSS transition has nothing to move *from*. This remembers how each card sat
 * last time it was drawn, starts the new node at that angle, and lets it turn
 * to the angle the stylesheet asks for — covering both directions at once:
 * resting to attack or to pay a cost, and the sweep back to active at the start
 * of your turn. The reset is on a timer rather than the animation's end,
 * because a background tab never fires the frame that would start it. */
const TURN_MS = 320;

function animateTurn(node, card, opts) {
  if (card.uid === undefined || opts.seat === undefined) return;
  const now = !!card.rested;
  state.restSeen.set(card.uid, now);
  const was = state.restState.get(card.uid);
  if (was === undefined || was === now) return;

  const flipped = document.body.classList.contains("flip-opponent") && opts.seat !== state.mySeat;
  const base = flipped ? 180 : 0;
  const from = base + (was ? 90 : 0);
  const to = base + (now ? 90 : 0);

  node.style.transition = "none";
  node.style.transform = `rotate(${from}deg)`;
  const stagger = Math.min(state.turning++, 8) * 25;
  requestAnimationFrame(() => {
    node.style.transition = `transform ${TURN_MS}ms cubic-bezier(.2,.75,.3,1) ${stagger}ms`;
    node.style.transform = `rotate(${to}deg)`;
  });
  // Whatever happens to the frame callback, the card must end up where the
  // stylesheet puts it.
  setTimeout(() => {
    node.style.transition = "";
    node.style.transform = "";
  }, TURN_MS + stagger + 120);
}

/** The battle-area card box, read from CSS so the two cannot drift apart. */
function cardBox() {
  const css = getComputedStyle(document.documentElement);
  const w = parseFloat(css.getPropertyValue("--battle-card-w")) || 116;
  const h = parseFloat(css.getPropertyValue("--battle-card-h")) || 162;
  return { w, h };
}

function prettyPilot(name) {
  if (!name) return "";
  if (!name.startsWith("rl:")) return name;
  const tag = name.slice(3).replace(/\.pt$/, "").replace(/^rl_agent_?/, "");
  return tag ? "RL " + tag : "RL";
}

/* ---------------------------------------------------------------- preview */
/* Hovering a card shows it big. It used to do that under the cursor, which put
 * the enlargement on top of the thing you were looking at — hover a Cookie in
 * your battle area and the preview covers the row it sits in. So the play view
 * has a fixed dock at the top of the right-hand panel instead, directly above
 * the move list: the card lands in the one place your eyes already go for the
 * options, and the board is never covered.
 *
 * The floating behaviour is still there for the views that have no panel — the
 * deck builder, the deck showcase, and the trash/break <dialog>, which is in
 * the browser's top layer and can only be drawn over from inside itself. */
const preview = el("#preview");
const previewDock = el("#preview-dock");

/** Is the preview currently parked in the panel rather than chasing the mouse? */
function previewDocked() { return preview.parentElement === previewDock; }

function showPreview(card, event) {
  if (!card) return;
  previewDock.classList.remove("empty");
  preview.innerHTML = "";
  if (card.img) {
    const img = h("img");
    img.src = card.img;
    img.onerror = () => img.remove();
    preview.appendChild(img);
  }
  preview.appendChild(h("h4", null, card.name || "?"));
  const bits = [card.id, card.type, card.color].filter(Boolean);
  if (card.level) bits.push("LV" + card.level);
  if (card.hp) bits.push("HP" + card.hp);
  preview.appendChild(h("div", "meta", bits.join(" · ")));
  if (card.attack) {
    preview.appendChild(h("div", "atkline",
      `${card.attack.name || "Attack"} ${card.attack.cost} → ${card.attack.damage} dmg`));
  }
  if (card.cost) preview.appendChild(h("div", "meta", "cost " + card.cost));
  const text = [card.text, card.attack && card.attack.text, card.flipText]
    .filter((t) => t && t.trim()).join("\n\n");
  if (text) preview.appendChild(h("div", "text", text));
  preview.classList.remove("hidden");
  movePreview(event);
}

function movePreview(event) {
  if (previewDocked()) return;
  if (!event || preview.classList.contains("hidden")) return;
  const pad = 14;
  const width = preview.offsetWidth || 260;
  const height = preview.offsetHeight || 320;
  let x = event.clientX + pad;
  let y = event.clientY + pad;
  if (x + width > window.innerWidth) x = event.clientX - width - pad;
  if (y + height > window.innerHeight) y = Math.max(8, window.innerHeight - height - 8);
  preview.style.left = x + "px";
  preview.style.top = y + "px";
}

function hidePreview() {
  preview.classList.add("hidden");
  previewDock.classList.add("empty");
}
window.addEventListener("mousemove", (e) => { if (preview.dataset.follow) movePreview(e); });

/** Park the preview in the panel: no cursor tracking, nothing over the board. */
function dockPreview() {
  preview.classList.add("indock");
  preview.style.left = "";
  preview.style.top = "";
  if (preview.parentElement !== previewDock) previewDock.appendChild(preview);
  hidePreview();
}
dockPreview();

/* ------------------------------------------------------------- the card back */
/* The real Braverse card back is not among the assets this project can fetch —
 * the CDN behind `fetch_images.py` serves card *fronts*, keyed on card id, and
 * has nothing for the reverse. So: drop the image in as
 * `card_images/card_back.webp` and every face-down card in the viewer uses it,
 * with the drawn sleeve underneath as the fallback for anyone who has not. It
 * is probed once, not per card, and a miss costs one 404 for the session.
 */
const CARD_BACK_ART = "/card_images/card_back.webp";

function findCardBack() {
  const probe = new Image();
  probe.onload = () => {
    document.body.style.setProperty("--card-back-art", `url("${CARD_BACK_ART}")`);
    document.body.classList.add("has-cardback");
  };
  probe.src = CARD_BACK_ART;
}
findCardBack();

/* ------------------------------------------------------------ card markup */
function faceDown(size) {
  const node = h("div", "card back" + (size ? " " + size : ""));
  return node;
}

function cardNode(card, opts = {}) {
  const size = opts.small ? " small" : opts.mid ? " mid" : "";
  if (!card) return faceDown(size.trim());
  const node = h("div", "card" + size + (card.rested ? " rested" : ""));
  const img = h("img");
  img.src = card.img;
  img.alt = card.name;
  img.onerror = () => {
    img.remove();
    const fb = h("div", "fallback");
    fb.appendChild(h("b", null, card.name));
    fb.appendChild(h("div", null, [card.type, card.level && "LV" + card.level, card.hp && "HP" + card.hp]
      .filter(Boolean).join(" ")));
    node.appendChild(fb);
  };
  node.appendChild(img);

  if (card.uid !== undefined) node.dataset.uid = card.uid;
  animateTurn(node, card, opts);
  node.addEventListener("mouseenter", (e) => { preview.dataset.follow = "1"; showPreview(card, e); });
  node.addEventListener("mouseleave", () => { delete preview.dataset.follow; hidePreview(); });
  if (opts.uid !== undefined && opts.uid !== null) {
    if (opts.seat === state.mySeat) makeDraggable(node, opts.uid, opts.seat);
    // The same click on either side of the table. Your own cards are also
    // dragged to play them; a drag that goes nowhere lands here too.
    node.addEventListener("click", () => cardClick(opts.uid, node));
    /* A question that names this card is answered by pointing at it — the
     * cheapest click on the table, and the one a misclick hurts most. When
     * `Confirm` wants that one held, the press starts here and the click above
     * finds the answer already spent. */
    node.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      const direct = directOption(opts.uid);
      if (direct) Confirm.press(event, node, direct, () => answer(direct.index));
    });
    /* The marker `renderTray` reads to decide whether a move has somewhere to
     * be clicked. It goes on here, next to the handler, because the two have to
     * agree: a card drawn face-up on top of a pile carries a uid and answers
     * `[data-uid]` searches, but the pile's layers are inert, so a move that
     * named it would have been silently unreachable. Same condition, same
     * line — they cannot drift apart. */
    node.classList.add("pickable");
  }
  return node;
}

/* --------------------------------------------------------------- the board */
function cookieSlot(cookie) {
  const slot = h("div", "slot");
  const box = h("div", "cookiebox");

  // The HP pile is face down, physically under the Cookie card. Offsetting the
  // backs up and to the left is what makes the pile readable at a glance.
  const stack = h("div", "hpstack");
  const depth = Math.min(cookie.hp, 6);
  for (let i = depth - 1; i >= 0; i--) {
    const back = faceDown();
    back.style.left = -(i + 1) * 3 + "px";
    back.style.top = -(i + 1) * 3 + "px";
    stack.appendChild(back);
  }
  box.appendChild(stack);

  const front = h("div", "cookie-front");
  const card = cardNode(cookie.card, { uid: cookie.uid, seat: cookie.owner });
  card.dataset.uid = cookie.uid;
  front.appendChild(card);
  front.appendChild(h("span", "badge", "LV" + cookie.level));
  if (cookie.attackDamage) {
    const atk = h("span", "atk", String(cookie.attackDamage));
    atk.title = "attack damage";
    front.appendChild(atk);
  }
  if (cookie.blocker) front.appendChild(h("span", "blocker", "BLK"));
  /* An 【Awaken】ed Cookie is a stack: the EXTRA card on top of the Cookie it
   * was played onto. Show the card underneath peeking out, so the board reads
   * as two cards rather than as a Cookie that silently changed its name. */
  if (cookie.under && cookie.under.length) {
    const tag = h("span", "awoken", "AWAKENED");
    tag.title = "Awakened from " + cookie.under.map((c) => c.name).join(", ");
    front.appendChild(tag);
  }
  box.appendChild(front);
  box.dataset.cookie = cookie.uid;
  slot.appendChild(box);

  /* HP is the pile, and healing can push it past the printed value — the card
   * does not get bigger, it is carrying spare cards. Printed HP stays the
   * denominator and every card above it is a green tick on the end of the bar. */
  const bar = h("div", "hpbar");
  const max = cookie.maxHp || cookie.hp;
  const over = Math.max(0, cookie.hp - max);
  for (let i = 0; i < max; i++) bar.appendChild(h("i", i < cookie.hp ? "on" : ""));
  for (let i = 0; i < over; i++) bar.appendChild(h("i", "on over"));
  slot.appendChild(bar);
  slot.appendChild(h("div", "hplabel",
    `${cookie.hp}/${max} HP${over ? ` (+${over})` : ""}${cookie.rested ? " · rested" : ""}`));
  return slot;
}

function zone(cls, label) {
  const node = h("div", "zone " + cls);
  node.appendChild(h("span", "zlabel", label));
  return node;
}

function stack(label, count, opts = {}) {
  const node = h("div", "stack" + (opts.onClick ? " clickable" : "") + (opts.danger ? " danger" : ""));
  /* A pile is drawn three cards deep so it reads as a pile. On a face-up one —
   * trash, break area, the EXTRA deck — the two underneath used to be drawn as
   * full card *backs*, offset up and to the right of the top card, so the right
   * edge of a face-up pile was a face-down sleeve: point at that edge and the
   * pile looked face down. Underneath a face-up top card they are paper edges
   * now, with no sleeve on them, and the whole pile previews the top card
   * wherever you point at it rather than only over the card itself. */
  const showsFront = Boolean(opts.faceUp && opts.top);
  const art = h("div", "stackart" + (showsFront ? " faceup" : ""));
  if (count > 0) {
    for (let i = Math.min(count, 3) - 1; i >= 0; i--) {
      const layer = !showsFront ? faceDown()
        : i === 0 ? cardNode(opts.top, { small: true })
        : h("div", "card edge");
      // Offsets go through custom properties, not `transform`, so the stylesheet
      // can still turn the card around on a flipped opponent mat.
      layer.style.setProperty("--ox", i * 2 + "px");
      layer.style.setProperty("--oy", -i * 2 + "px");
      art.appendChild(layer);
    }
    if (showsFront) {
      // The layers are inert (see the stylesheet), so this is the one thing
      // the pointer can land on and it always means the top card.
      art.addEventListener("mouseenter", (event) => {
        preview.dataset.follow = "1";
        showPreview(opts.top, event);
      });
      art.addEventListener("mouseleave", () => {
        delete preview.dataset.follow;
        hidePreview();
      });
    }
  } else {
    const ghost = h("div", "card back");
    ghost.style.opacity = ".18";
    art.appendChild(ghost);
  }
  node.appendChild(art);
  const meta = h("div", "meta");
  meta.appendChild(h("div", "zlabel2", label));
  meta.appendChild(h("div", "num", opts.numText !== undefined ? opts.numText : String(count)));
  if (opts.sub) meta.appendChild(h("div", "sub", opts.sub));
  node.appendChild(meta);
  if (opts.title) node.title = opts.title;
  if (opts.onClick) node.onclick = opts.onClick;
  return node;
}

/* ------------------------------------------------------------ support step */
/* The one phase the player is asked to leave deliberately.
 *
 * The engine has no `support` phase to stop in — placing a support card is a
 * main-phase action capped at one per turn (see `phaseTrack` below). That is
 * fine for a bot and wrong for a person: the free support card is the easiest
 * thing in the game to forget, and a pulsing dot in the round track was only
 * ever a hint you could play straight past. So the viewer puts the step back on
 * top of the same action list. While it is open the turn's moves are narrowed
 * to "place a card as support", and the way out is to place one or to say you
 * are not going to — after which the full move list returns for the rest of the
 * turn. Nothing about it reaches the engine: the actions offered are the ones
 * the engine already offered, in a different order of asking, so a bot seat, a
 * replay and self-play all see exactly the game they saw before.
 *
 * Passing is per turn and per browser. `key` is the turn it was answered for,
 * so the next turn re-opens the step without anything having to reset it.
 */
const supportStep = { key: null, passed: false };

const SUPPORT_PROMPT = "Support phase — place 1 card from your hand as support, "
                     + "or pass to your main phase.";

function supportStepSync(snap) {
  const key = snap && snap.players ? snap.turn + ":" + snap.turnPlayer : null;
  if (supportStep.key !== key) { supportStep.key = key; supportStep.passed = false; }
}

/** Is the support step still open on the question currently on screen? */
function inSupportStep(snap) {
  const pending = snap && snap.pending;
  if (!pending || !pending.turnAction || pending.waiting || state.animating) return false;
  if (supportStep.passed) return false;
  const player = (snap.players || [])[pending.seat];
  if (!player || player.supportedThisTurn) return false;
  // A hand that cannot support — empty, or every card already spoken for by
  // the engine — has no step to stop in.
  return (pending.options || []).some((opt) => opt.kind === "PlaceSupport");
}

/** The moves the board should offer right now, narrowed by the support step. */
function narrow(snap, options) {
  return inSupportStep(snap)
    ? (options || []).filter((opt) => opt.kind === "PlaceSupport")
    : (options || []);
}

/** Give up this turn's support card and open the rest of the turn. */
function passSupportStep() {
  supportStep.passed = true;
  closeCardMenu();
  if (state.snap) render(state.snap);
}

/* The round, as a track above the break area.
 *
 * Driven by more than `snap.phase`, because the engine only ever *reports*
 * `main` to a player: it untaps and draws for you inside the turn machinery,
 * and support placement is a main-phase action capped at one per turn rather
 * than a phase you stop in. So Active and Draw are shown as already resolved,
 * End as still to come, and Support carries the one piece of state a player can
 * actually act on — whether this turn's support card has been placed yet. */
const ROUND_PHASES = [
  ["active", "Active", "your cards untap"],
  ["draw", "Draw", "you draw for the turn"],
  ["support", "Support", "place 1 card as support"],
  ["main", "Main", "play, activate, attack"],
  ["end", "End", "turn passes"],
];

function phaseTrack(seat, snap) {
  const mine = snap.turnPlayer === seat;
  const player = snap.players[seat];
  const track = h("div", "phases" + (mine ? " mine" : ""));
  track.appendChild(h("div", "zlabel2", mine ? "this turn" : "opponent's turn"));

  const openerSkips = snap.turn === 1 && seat === snap.firstPlayer;
  ROUND_PHASES.forEach(([key, label, hint]) => {
    let cls = "phase";
    let note = "";
    if (!mine) {
      cls += " idle";
    } else if (key === "active" || key === "draw") {
      cls += " done";
      if (key === "draw" && openerSkips) { cls = "phase skipped"; note = "skipped"; }
    } else if (key === "support") {
      if (player.supportedThisTurn) { cls += " used"; note = "done"; }
      else if (inSupportStep(snap)) { cls += " now"; note = "now"; }
      else if (supportStep.passed) { cls = "phase skipped"; note = "passed"; }
      else { cls += " available"; note = "ready"; }
    } else if (key === "main") {
      if (inSupportStep(snap)) { cls += " todo"; }
      else cls += snap.phase === "main" ? " now" : " done";
    } else {
      cls += " todo";
    }
    const row = h("div", cls);
    row.appendChild(h("span", "dot"));
    row.appendChild(h("span", "name", label));
    if (note) row.appendChild(h("span", "note", note));
    row.title = hint;
    track.appendChild(row);
  });
  return track;
}

function renderSide(seat, snap) {
  const side = el("#side-" + seat);
  side.innerHTML = "";
  const p = snap.players[seat];
  const isHuman = (snap.humanSeats || []).includes(seat);
  const opponent = seat !== state.mySeat;

  /* -- seat bar ------------------------------------------------------- */
  const bar = h("div", "seatbar");
  if (snap.online) {
    bar.appendChild(h("span", "who", seatLabel(seat, snap)));
  } else {
    bar.appendChild(h("span", "who", "Seat " + seat));
    // A replayed seat is labelled with whoever played it, not with the fact
    // that it is being read off a file — the header already says that.
    const pilot = (snap.replay && snap.replay.pilots ? snap.replay.pilots : snap.pilots)[seat];
    bar.appendChild(h("span", "pill" + (isHuman ? " human" : ""), prettyPilot(pilot)));
  }
  bar.appendChild(h("span", "deckname", snap.decks[seat]));

  /* -- mat ------------------------------------------------------------ */
  const mat = h("div", "mat" + (snap.turnPlayer === seat && !snap.over ? " active" : ""));

  const left = h("div", "matcol left");
  left.appendChild(phaseTrack(seat, snap));
  left.appendChild(stack("break area", p.break.length, {
    numText: `${p.breakLevel} / 10`,
    sub: `${p.break.length} card${p.break.length === 1 ? "" : "s"}`,
    danger: p.breakLevel >= 7,
    faceUp: true,
    top: p.break[p.break.length - 1],
    title: "Level your opponent has banked here. 10 loses the game. Click to look through it.",
    onClick: p.break.length ? () => openBrowser(seat, "break") : null,
  }));

  const battle = zone("battle", "battle area");
  p.battle.forEach((c) => battle.appendChild(cookieSlot(c)));
  for (let i = p.battle.length; i < 2; i++) battle.appendChild(h("div", "slot empty", "empty"));

  const support = zone("support", `support area · ${p.supportActive}/${p.support.length} active`);
  const cards = h("div", "cards");
  p.support.forEach((c) => cards.appendChild(cardNode(c, { small: true, uid: c.uid, seat })));
  support.appendChild(cards);

  const right = h("div", "matcol right");
  const stageZone = zone("stage", "stage");
  const stageCards = h("div", "cards");
  p.stage.forEach((c) => stageCards.appendChild(cardNode(c, { small: true, uid: c.uid, seat })));
  stageZone.appendChild(stageCards);
  right.appendChild(stageZone);
  const deckStack = stack("deck", p.deckCount, { title: "Face-down deck" });
  deckStack.dataset.zone = "deck";
  right.appendChild(deckStack);
  /* The EXTRA deck is public: both players may read it whenever they like, so
   * it shows its top card face up and opens like the trash rather than sitting
   * face down as a count. Hidden entirely when a deck does not play one. */
  if (p.extraCount) {
    right.appendChild(stack("extra deck", p.extraCount, {
      faceUp: true,
      top: p.extra[p.extra.length - 1],
      title: "EXTRA deck — played from here when its condition is met. "
           + "Click to look through it.",
      onClick: () => openBrowser(seat, "extra"),
    }));
  }
  const trashStack = stack("trash", p.trashCount, {
    faceUp: true,
    top: p.trash[p.trash.length - 1],
    title: "Click to search the trash",
    onClick: p.trashCount ? () => openBrowser(seat, "trash") : null,
  });
  // Named so a discarded card knows where to fly, the same way the deck is.
  trashStack.dataset.zone = "trash";
  right.appendChild(trashStack);

  mat.appendChild(left);
  mat.appendChild(battle);
  mat.appendChild(support);
  mat.appendChild(right);

  /* -- hand ----------------------------------------------------------- */
  const handRow = h("div", "handrow");
  handRow.appendChild(h("span", "tag", `hand ${p.handCount}`));
  const hand = h("div", "hand");
  hand.dataset.zone = "hand";
  if (p.hand.length) {
    p.hand.forEach((c) => hand.appendChild(cardNode(c, { mid: true, uid: c.uid, seat })));
  } else {
    for (let i = 0; i < p.handCount; i++) hand.appendChild(faceDown("mid"));
  }
  handRow.appendChild(hand);

  if (opponent) {
    side.appendChild(handRow);
    side.appendChild(mat);
    side.appendChild(bar);
  } else {
    side.appendChild(bar);
    side.appendChild(mat);
    side.appendChild(handRow);
  }
  markActionable(side, !opponent);
}

/* The middle of the table: End turn, and every move no card can carry.
 *
 * With the move list gone the board has to be able to reach every option, and
 * almost all of them name a card you can click. The rest have nothing to point
 * at. End turn and Pass name no card at all — End turn is the move you reach
 * for constantly and the far corner of the screen was always the worst place
 * for it. An 【EXTRA】 played standalone names a card inside a pile. A
 * mid-effect question can offer a banked rider that was never a card on the
 * board.
 *
 * Which ones are homeless is worked out from the finished markup rather than
 * from a list of kinds: an option is homeless when nothing on screen carries
 * its uid. So a move nobody thought about lands here instead of quietly
 * becoming unplayable, which is the failure this whole layout could otherwise
 * have — and `tests/test_viewer.py` pins the server side of it.
 */
function homelessOptions(snap) {
  const pending = snap.pending;
  if (!pending || state.animating || pending.waiting) return [];
  // These two have already claimed their own place on screen, decline included.
  if (pending.centre || pending.pick) return [];
  return narrow(snap, pending.options).filter((opt) => {
    if (opt.subject === undefined) return true;
    return !document.querySelector(`.card.pickable[data-uid="${opt.subject}"]`);
  });
}

function renderTray(snap) {
  const host = el("#endturn");
  host.innerHTML = "";
  const pending = snap.pending;
  const homeless = homelessOptions(snap);
  const declines = !!(pending && pending.optional && !pending.centre && !pending.pick
                      && !state.animating && !pending.waiting);
  const supporting = inSupportStep(snap);
  if (!homeless.length && !declines && !supporting) { host.classList.add("hidden"); return; }
  host.classList.remove("hidden");

  // The support step's own way out. It sits where End turn sits, because it is
  // the same shape of move — the button that says "I am done with this part of
  // the turn" — and while the step is open End turn is not on offer.
  if (supporting) {
    const skip = h("button", "tray-btn support", "Pass to main phase");
    skip.title = "Keep this turn's support card in hand and move on.";
    skip.onclick = passSupportStep;
    host.appendChild(skip);
  }

  // End turn goes last and looks different: it is the one that gives up the
  // rest of your turn, and it should not sit first among the things you can
  // still do.
  const ordered = [...homeless].sort(byKindOrder);
  ordered.forEach((opt) => {
    const end = opt.kind === "EndTurn" || opt.kind === "Pass";
    const btn = h("button", "tray-btn" + (end ? " end" : ""),
                  opt.kind === "EndTurn" ? "End turn" : opt.label);
    btn.onmouseenter = () => { highlight(opt); showPreview(optionCard(opt)); };
    btn.onmouseleave = () => { highlight(null); hidePreview(); };
    // End turn and Pass are the two moves in the tray that cannot be walked
    // back, so they are the two that ask to be held. `Confirm` decides.
    Confirm.wire(btn, opt, () => answer(opt.index));
    host.appendChild(btn);
  });
  if (declines) {
    const no = h("button", "tray-btn end", "Decline");
    Confirm.wire(no, "decline", () => answer(null));
    host.appendChild(no);
  }
}

function renderBanner(snap) {
  const banner = el("#banner");
  banner.className = "banner";
  if (snap.error) {
    banner.textContent = "engine error: " + snap.error;
    banner.classList.add("win");
    return;
  }
  if (snap.over) {
    banner.classList.add("win");
    banner.textContent = snap.winner === -1 || snap.winner === null
      ? `Draw — ${snap.winReason}`
      : `${seatLabel(snap.winner, snap)} wins — ${snap.winReason}`;
    return;
  }
  // A replay ends either because the recording ran out — a game saved while it
  // was still being played — or because this build no longer plays the game
  // that was recorded. Both are the end of the show and both say which.
  if (snap.replay && snap.replay.note) {
    banner.classList.add("win");
    banner.textContent = (snap.replay.desync
      ? "replay stopped — this build has diverged from it: "
      : "end of the recording — ") + snap.replay.note;
    return;
  }
  if (snap.pending) {
    banner.classList.add("on");
    const line = inSupportStep(snap) ? SUPPORT_PROMPT : snap.pending.prompt;
    setText(banner, `${seatLabel(snap.pending.seat, snap)}: ${line}`);
    return;
  }
  banner.textContent = snap.paused ? "paused" : "…";
}

function renderTurnline(snap) {
  if (!snap.players) { el("#turnline").textContent = "no match yet — hit New match"; return; }
  // In a replay both pilots read "replay", which the line already says at the
  // end — naming it twice per turn is noise.
  const who = snap.online || snap.replay
    ? `<b>${snap.replay ? "seat " + snap.turnPlayer : seatLabel(snap.turnPlayer, snap)}</b>`
    : `<b>seat ${snap.turnPlayer}</b> (${prettyPilot(snap.pilots[snap.turnPlayer])})`;
  // Watching one back rather than playing it: say so, and say how far in.
  // The seed belongs to a game you might want to play again; a replay is
  // already exactly the game it is, and the line is tight enough as it is.
  const tail = snap.replay
    ? ` · <b>replay</b> ${snap.replay.at}/${snap.replay.total}`
    : ` · seed ${snap.seed}`;
  el("#turnline").innerHTML =
    `turn <b>${snap.turn}</b> · ${who} · phase <b>${snap.phase}</b>${tail}`;
}

/* ---------------------------------------------------------- the play-out */
/* One action can be a whole little scene: the attacker swings, damage turns HP
 * cards face up, a Cookie breaks. The server sends them already ordered, and
 * this plays them as a sequence rather than all at once. */
const ATTACK_MS = 900;
const REVEAL_MS = 700;    // between one HP card turning over and the next
const FLIP_MS = 2400;     // how long a revealed card stays on screen
const FAINT_MS = 300;
const DRAW_MS = 220;      // between one card leaving the deck and the next
const DRAW_FLIGHT = 700;  // how long a card takes to reach the hand
const DISCARD_MS = 260;      // between one discarded card and the next
const DISCARD_FLIGHT = 1000; // hand -> trash, face up the whole way
const DAMAGE_MS = 250;    // between one hit registering and the next
const DAMAGE_HOLD = 1000; // how long the number floats
const SKILL_MS = 400;     // between one skill popping up and the next
const SKILL_HOLD = 1500;  // how long the confirmation stays on screen
const BREAK_MS = 1500;    // how long a breaking Cookie stays on screen
const TRAP_MS = 1000;     // before whatever the card did starts happening
const TRAP_HOLD = 2200;   // how long a sprung trap or item owns the middle
const SUMMON_RISE = 620;   // the arriving Cookie owns the middle of the board
const SUMMON_DIVE = 460;  // and then falls into its slot
const SUMMON_MS = SUMMON_RISE + SUMMON_DIVE;  // between one arrival and the next
const SUMMON_HOLD = SUMMON_MS + 900;          // how long the dust hangs in the air

const MAX_REVEALS = 6;    // the most cards one scene turns over on screen

/** Play one action's scene and return how long it runs, in ms.
 *
 * The events arrive in the order they happened, and that order is the whole
 * point: a FLIP heals its host *after* the card turns over, and grouping the
 * events by type — every hit, then every heal, then every reveal — played the
 * consequence before the cause. So this walks the list once and lays each
 * event on a clock, rather than sorting them into piles first.
 */
function playEvents(events) {
  if (!events || !events.length) return 0;
  let clock = 0;      // when the next event starts
  let end = 0;        // when the last thing on screen finishes
  let reveals = 0;    // how many cards this scene has already turned
  let swingLand = null;   // when the current attack connects
  let hitStart = null;    // when the first card of the current hit turned
  let revealsClear = 0;   // when the last revealed card leaves the screen
  const summons = [0, 0];  // arrivals per seat, to tell their slots apart
  let discards = 0;   // cards already on their way to a trash, to fan them out
  const holds = (start, hold) => { end = Math.max(end, start + hold); };
  const shown = events.filter((e) => e.type === "reveal").slice(0, MAX_REVEALS);
  if (shown.length) renderReveals(shown);

  events.forEach((event) => {
    switch (event.type) {
      case "draw": {
        const n = Math.max(1, event.count || 1);
        for (let i = 0; i < n; i++) playDraw(event, clock + i * DRAW_MS);
        holds(clock, (n - 1) * DRAW_MS + DRAW_FLIGHT);
        clock += n * DRAW_MS;
        break;
      }
      case "discard": {
        playDiscard(event, clock, discards++);
        holds(clock, DISCARD_FLIGHT);
        clock += DISCARD_MS;
        break;
      }
      case "skill":
        playSkill(event, clock);
        holds(clock, SKILL_HOLD);
        clock += SKILL_MS;
        break;
      case "trap":
      case "item":
        playSpotlight(event, clock);
        holds(clock, TRAP_HOLD);
        // Everything the card does waits for it to land, so it is on screen
        // before the damage or the debuff it caused.
        clock += TRAP_MS;
        break;
      case "summon":
        playSummon(event, clock, summons[event.owner]++);
        holds(clock, SUMMON_HOLD);
        clock += SUMMON_MS;
        break;
      case "attack":
        playAttack(event, clock);
        holds(clock, ATTACK_MS);
        // The swing connects part-way through its flight; that is when the
        // number it deals should land, not when the card gets home.
        swingLand = clock + ATTACK_MS * 0.4;
        clock += ATTACK_MS;
        break;
      case "reveal": {
        if (reveals >= MAX_REVEALS) break;
        if (hitStart === null) hitStart = clock;
        playReveal(event, clock, reveals++);
        holds(clock, FLIP_MS);
        // What the card *did* plays while it is still up, so the clock only
        // moves on by the gap between cards. The board changing underneath it
        // is different — see `faint`.
        revealsClear = Math.max(revealsClear, clock + FLIP_MS);
        clock += REVEAL_MS;
        break;
      }
      case "damage": {
        // Damage is recorded once the whole hit has resolved, because only
        // then is it known how much landed — but it *reads* as the moment the
        // first card turned, so that is where it is played.
        const at = event.source === "attack" && swingLand !== null
          ? swingLand
          : (hitStart !== null ? hitStart : clock);
        playDamage(event, at);
        holds(at, DAMAGE_HOLD);
        hitStart = null;
        clock = Math.max(clock, at + DAMAGE_MS);
        break;
      }
      case "heal":
        playHeal(event, clock);
        holds(clock, DAMAGE_HOLD);
        clock += DAMAGE_MS;
        break;
      case "faint": {
        // A Cookie leaving the board is the one thing that must not happen
        // under a revealed card still being read.
        const at = Math.max(clock, revealsClear);
        playFaint(event, at);
        holds(at, BREAK_MS);
        clock = at + FAINT_MS;
        break;
      }
      default:
        break;
    }
  });
  return end;
}

/* An HP card turning face up is the swing moment of a battle — a FLIP can
 * bounce its own host mid-attack — so it gets a real beat rather than a number
 * quietly ticking down. */
function playReveal(event, delay = 0, seq = 0) {
  // Several cards can come off the same Cookie in one attack; `seq` fans them
  // out so a three-damage hit reads as three cards rather than one.
  setTimeout(() => {
    flipCard(event, seq, seq);
    Sfx.play(event.flip ? "flipBig" : "flip");
  }, delay);
}

/* The attacker steps out of its slot, turns side-on into the defender, and
 * settles back — a real card being pushed forward to declare an attack. */
function playAttack(event, delay = 0) {
  setTimeout(() => {
    const from = document.querySelector(`[data-cookie="${event.attacker}"]`);
    const to = document.querySelector(`[data-cookie="${event.target}"]`)
      || document.querySelector(`#side-${event.targetOwner} .zone.battle`);
    Sfx.play("attack");
    Sfx.play("impact", ATTACK_MS * 0.3);
    if (!from || !to) return;

    const bounds = el("#table").getBoundingClientRect();
    const a = from.getBoundingClientRect();
    const b = to.getBoundingClientRect();
    // Stop alongside the defender rather than on top of it, so both stay readable.
    const side = a.left <= b.left ? -1 : 1;
    const dx = (b.left + b.width / 2 + side * (b.width * 0.62)) - (a.left + a.width / 2);
    const dy = (b.top + b.height / 2) - (a.top + a.height / 2);
    // Keep the stand-in facing the way the real card does, flipped mat included.
    const base = document.body.classList.contains("flip-opponent")
      && event.attackerOwner !== 0 ? 180 : 0;
    const spin = base + (event.attackerOwner === 0 ? -12 : 12);

    const ghost = h("div", "attacker");
    ghost.style.left = a.left - bounds.left + "px";
    ghost.style.top = a.top - bounds.top + "px";
    ghost.style.width = a.width + "px";
    ghost.style.height = a.height + "px";
    const card = from.querySelector("img");
    if (card) {
      const img = h("img");
      img.src = card.src;
      ghost.appendChild(img);
    }
    ghost.dataset.born = performance.now();
    el("#fx").appendChild(ghost);

    const mid = base + (spin - base) * 0.5;
    // Timing is per keyframe, not one curve over the whole run: a single global
    // easing squeezes the travel *and* the hold into the first fraction of the
    // animation and spends the rest drifting home, so the card only touches the
    // defender for a blink. Out (30%), hold alongside it (30%), back (40%).
    const flight = ghost.animate([
      { transform: `translate(0,0) rotate(${base}deg) scale(1)`, easing: "ease-in" },
      { transform: `translate(${dx * 0.45}px, ${dy * 0.45}px) rotate(${mid}deg) scale(1.05)`,
        offset: 0.16, easing: "ease-out" },
      { transform: `translate(${dx}px, ${dy}px) rotate(${spin}deg) scale(1.08)`,
        offset: 0.3, easing: "linear" },
      { transform: `translate(${dx}px, ${dy}px) rotate(${spin}deg) scale(1.08)`,
        offset: 0.6, easing: "ease-in-out" },
      { transform: `translate(0,0) rotate(${base}deg) scale(1)`, offset: 1 },
    ], { duration: ATTACK_MS, easing: "linear" });

    // Remove it when the animation actually ends. A background tab throttles
    // timers but not the compositor, so a timer alone can leave a card floating
    // over the board; the timer stays as a backstop, and `sweepFx` catches
    // anything that still slips through.
    flight.finished.then(() => ghost.remove()).catch(() => ghost.remove());
    setTimeout(() => ghost.remove(), ATTACK_MS + 400);
  }, delay);
}

/* A hit landing. The two sources are deliberately unalike: a swing is a heavy
 * red shove with the number thrown out to the side, while a rider, a skill or a
 * trap is a cool blue pulse that rises off the card — so you can tell, without
 * reading a word, whether you were hit or chipped. */
function playDamage(event, delay = 0) {
  setTimeout(() => {
    const attack = event.source === "attack";
    const host = document.querySelector(`[data-cookie="${event.cookie}"]`);
    Sfx.play(attack ? "impact" : "zap");
    const anchor = host || document.querySelector(`#side-${event.owner} .zone.battle`);
    if (!anchor) return;
    const bounds = el("#table").getBoundingClientRect();
    const at = anchor.getBoundingClientRect();

    if (host) {
      host.classList.add(attack ? "struck" : "zapped");
      setTimeout(() => host.classList.remove(attack ? "struck" : "zapped"), 460);
    }

    const node = h("div", "hitnum " + (attack ? "attack" : "effect"));
    node.appendChild(h("span", "n", "-" + event.amount));
    /* What hit, under the number. A red -2 floating off a Cookie says how much
     * but never what, and "what" is the whole question when three different
     * things can be hitting you in the same beat — the swing, the "Then, ..."
     * rider on it, and a trap sprung in between. The engine names the source
     * on the event; this is that name, with one word for what sort of thing it
     * was where the two differ. */
    if (event.sourceName) {
      const kind = event.sourceKind && event.sourceKind !== "attack"
        && event.sourceKind !== "effect" ? ` · ${event.sourceKind}` : "";
      node.appendChild(h("span", "src", event.sourceName + kind));
    }
    node.style.left = at.left + at.width / 2 - bounds.left + "px";
    node.style.top = at.top + at.height / 2 - bounds.top + "px";
    node.dataset.born = performance.now();
    el("#fx").appendChild(node);
    setTimeout(() => node.remove(), DAMAGE_HOLD + 200);
  }, delay);
}

/* HP handed back — a heal, or "this Cookie's HP cannot reach 0" pulling a
 * replacement off the deck. Green where damage is red, and it glows around the
 * card rather than shoving it, because nothing is being done *to* the Cookie. */
function playHeal(event, delay = 0) {
  setTimeout(() => {
    const host = document.querySelector(`[data-cookie="${event.cookie}"]`);
    Sfx.play("heal");
    const anchor = host || document.querySelector(`#side-${event.owner} .zone.battle`);
    if (!anchor) return;
    if (host) {
      host.classList.add("healed");
      setTimeout(() => host.classList.remove("healed"), 900);
    }
    const bounds = el("#table").getBoundingClientRect();
    const at = anchor.getBoundingClientRect();
    const node = h("div", "hitnum heal");
    node.textContent = "+" + event.amount;
    node.style.left = at.left + at.width / 2 - bounds.left + "px";
    node.style.top = at.top + at.height / 2 - bounds.top + "px";
    node.dataset.born = performance.now();
    el("#fx").appendChild(node);
    setTimeout(() => node.remove(), DAMAGE_HOLD + 200);
  }, delay);
}

/* A Cookie arriving on the board. It is the only way anyone ever gets a Cookie,
 * so it gets the biggest beat on the table: the card comes up large in the
 * middle of the board, holds long enough to be read, then dives into the slot
 * it is about to fill and slams into it — spheres in the Cookie's own colour
 * thrown off its edges, and dust dragged out along the table.
 * Every zone a Cookie can arrive from — hand, trash, break area, support, the
 * EXTRA deck — gets the same beat, because from the other side of the table
 * they all mean the same thing. */
const DUST = {
  RED: "#ef5b52", BLUE: "#4aa8ff", GREEN: "#4fc275", YELLOW: "#f0c33c",
  PURPLE: "#b07bff", BLACK: "#8a90a3", PURE: "#e8eefc", "": "#cdd5e3",
};

/** Where the arriving Cookie is going to be.
 *
 * The board on screen is deliberately still the pre-summon one while the scene
 * plays (see `playThenCommit`), so the Cookie has no box yet — it is about to
 * fill the first free slot in its owner's battle area. `seq` walks those slots
 * so two Cookies arriving in one action land in the two they will occupy.
 */
function summonSlot(event, seq) {
  const side = `#side-${event.owner}`;
  const host = document.querySelector(`[data-cookie="${event.cookie}"]`);
  if (host) return host;
  const empty = document.querySelectorAll(`${side} .zone.battle .slot.empty`);
  return empty[Math.min(seq, empty.length - 1)]
    || document.querySelector(`${side} .zone.battle`);
}

function playSummon(event, delay = 0, seq = 0) {
  setTimeout(() => {
    const table = el("#table");
    const anchor = summonSlot(event, seq);
    if (!table || !anchor) return;
    const colour = DUST[(event.color || "").toUpperCase()] || DUST[""];
    Sfx.play("summon");

    // The part of the table that is actually on screen — same reasoning as the
    // trap spotlight: the window's centre can be behind the side panel.
    const box = table.getBoundingClientRect();
    const top = Math.max(box.top, 0);
    const bottom = Math.min(box.bottom, window.innerHeight);
    const to = anchor.getBoundingClientRect();
    if (!to.width || !to.height) return;

    // Sized off the card box rather than off the anchor: the anchor is a slot
    // when there is one to land in, but the fallback is the whole battle area,
    // and a card stretched to *that* shape is a letterboxed crop of the art.
    const slot = cardBox();
    // As big as the board can hold without the card running off the top.
    const height = Math.min(slot.h * 3, (bottom - top) * 0.66);
    const width = height * (slot.w / slot.h);
    const cx = box.left + box.width / 2;
    const cy = (top + bottom) / 2;

    const node = h("div", "summonpop");
    node.style.setProperty("--dust", colour);
    node.style.width = width + "px";
    node.style.height = height + "px";
    node.style.left = cx + "px";
    node.style.top = cy + "px";
    node.style.marginLeft = -width / 2 + "px";
    node.style.marginTop = -height / 2 + "px";
    const img = h("img");
    img.src = event.card.img;
    img.onerror = () => {
      img.remove();
      node.insertBefore(h("div", "fallback", event.card.name), node.firstChild);
    };
    node.appendChild(img);
    node.appendChild(h("div", "summonname", event.card.name));
    node.dataset.born = performance.now();
    el("#fx").appendChild(node);

    // Rise, hold, then an accelerating dive into the slot: the card is at
    // exactly slot size and slot position when it stops, so the real board
    // rendering underneath it at the end of the scene is a clean handoff.
    const dx = (to.left + to.width / 2) - cx;
    const dy = (to.top + to.height / 2) - cy;
    const shrink = slot.w / width;
    const total = SUMMON_RISE + SUMMON_DIVE;
    const rise = SUMMON_RISE / total;
    node.animate([
      { transform: "translate(0,0) scale(.25) rotate(-9deg)", opacity: 0,
        offset: 0, easing: "cubic-bezier(.12,.85,.25,1)" },
      { transform: "translate(0,0) scale(1.08) rotate(2deg)", opacity: 1,
        offset: rise * 0.32, easing: "ease-out" },
      { transform: "translate(0,0) scale(1) rotate(0deg)", opacity: 1,
        offset: rise * 0.5 },
      { transform: "translate(0,0) scale(1.03) rotate(0deg)", opacity: 1,
        offset: rise, easing: "cubic-bezier(.45,.05,.75,.55)" },
      { transform: `translate(${dx}px, ${dy}px) scale(${shrink}) rotate(0deg)`,
        opacity: 1, offset: 1 },
    ], { duration: total, fill: "forwards" });

    // The herald is binned by `commit`, once the real card is on the board.
    // This is only the backstop for a scene that never commits.
    setTimeout(() => node.remove(), total + 5000);
    setTimeout(() => summonImpact(event, anchor, colour), total);
  }, delay);
}

/** The moment the card hits the table. */
function summonImpact(event, anchor, colour) {
  Sfx.play("impact");
  const host = document.querySelector(`[data-cookie="${event.cookie}"]`);
  if (host) {
    host.style.setProperty("--dust", colour);
    host.classList.add("landing");
    setTimeout(() => host.classList.remove("landing"), 700);
  }
  // A card-sized box centred on wherever the Cookie landed — the same box the
  // herald stopped in, so the burst comes off its edges and not off the slot's.
  const at = anchor.getBoundingClientRect();
  const slot = cardBox();
  const mid = { x: at.left + at.width / 2, y: at.top + at.height / 2 };
  const to = {
    left: mid.x - slot.w / 2, right: mid.x + slot.w / 2,
    top: mid.y - slot.h / 2, bottom: mid.y + slot.h / 2,
    width: slot.w, height: slot.h,
  };
  const bounds = el("#table").getBoundingClientRect();
  const cx = to.left + to.width / 2 - bounds.left;
  const cy = to.top + to.height / 2 - bounds.top;

  edgeSparks(to, colour);

  // Dust from under the card: thrown out and slightly up, then dragged down.
  // Sizes and angles are jittered because a ring of identical dots reads as a
  // loading spinner rather than as something hitting a table.
  for (let i = 0; i < 26; i++) {
    const bit = h("div", "dust");
    const size = 4 + Math.random() * 7;
    const angle = Math.PI * (0.08 + Math.random() * 0.84);   // outward, mostly sideways
    const reach = to.width * (0.55 + Math.random() * 1.05);
    const dx = Math.cos(angle) * reach * (Math.random() < 0.5 ? -1 : 1);
    const dy = -Math.sin(angle) * reach * 0.42;
    bit.style.width = size + "px";
    bit.style.height = size + "px";
    bit.style.left = cx + "px";
    bit.style.top = cy + to.height * 0.34 + "px";
    bit.style.background = colour;
    bit.dataset.born = performance.now();
    el("#fx").appendChild(bit);
    bit.animate([
      { transform: "translate(-50%, -50%) scale(.6)", opacity: 0 },
      { transform: `translate(calc(-50% + ${dx * 0.55}px), calc(-50% + ${dy}px)) scale(1)`,
        opacity: 1, offset: 0.28 },
      { transform: `translate(calc(-50% + ${dx}px), calc(-50% + ${-dy * 0.5 + to.height * 0.3}px)) scale(.35)`,
        opacity: 0 },
    ], { duration: 620 + Math.random() * 340, easing: "cubic-bezier(.15,.7,.35,1)" })
      .finished.then(() => bit.remove()).catch(() => bit.remove());
    setTimeout(() => bit.remove(), 1100);
  }
}

/** Spheres knocked off the card's outline.
 *
 * Spawned at points *on* the perimeter and pushed straight out of the edge they
 * came from, rather than fired from the centre: the burst has to read as the
 * card's own edges shedding, so a sphere off the top must go up even though the
 * centre is directly below it. Coordinates are viewport ones, to match the
 * fixed-position card they are being thrown off.
 */
function edgeSparks(rect, colour, count = 34) {
  const perimeter = 2 * (rect.width + rect.height);
  for (let i = 0; i < count; i++) {
    let along = Math.random() * perimeter;
    let px, py, nx, ny;
    if (along < rect.width) {
      px = rect.left + along; py = rect.top; nx = 0; ny = -1;
    } else if ((along -= rect.width) < rect.height) {
      px = rect.right; py = rect.top + along; nx = 1; ny = 0;
    } else if ((along -= rect.height) < rect.width) {
      px = rect.right - along; py = rect.bottom; nx = 0; ny = 1;
    } else {
      along -= rect.width;
      px = rect.left; py = rect.bottom - along; nx = -1; ny = 0;
    }
    // A little sideways spread on the normal, so the edges do not all fire in
    // four straight lines.
    const spread = (Math.random() - 0.5) * 1.3;
    let vx = nx + (-ny) * spread;
    let vy = ny + nx * spread;
    const len = Math.hypot(vx, vy) || 1;
    vx /= len; vy /= len;

    const size = 5 + Math.random() * 10;
    const reach = rect.width * (0.5 + Math.random() * 1.5);
    const drop = rect.height * (0.3 + Math.random() * 0.5);   // gravity, at the end

    const bit = h("div", "spark");
    bit.style.color = colour;
    bit.style.width = size + "px";
    bit.style.height = size + "px";
    bit.style.left = px + "px";
    bit.style.top = py + "px";
    bit.dataset.born = performance.now();
    el("#fx").appendChild(bit);
    bit.animate([
      { transform: "translate(-50%, -50%) scale(.2)", opacity: 0 },
      { transform: `translate(calc(-50% + ${vx * reach * 0.5}px), calc(-50% + ${vy * reach * 0.5}px)) scale(1)`,
        opacity: 1, offset: 0.22 },
      { transform: `translate(calc(-50% + ${vx * reach}px), calc(-50% + ${vy * reach + drop}px)) scale(.25)`,
        opacity: 0 },
    ], { duration: 560 + Math.random() * 420, easing: "cubic-bezier(.12,.75,.3,1)" })
      .finished.then(() => bit.remove()).catch(() => bit.remove());
    setTimeout(() => bit.remove(), 1200);
  }
}

/* A drawn card, travelling from the deck to the hand. It stays face down the
 * whole way — a draw is secret, and the event carries only a count, so there is
 * no face to show even for your own cards until the hand redraws underneath. */
function playDraw(event, delay = 0) {
  setTimeout(() => {
    const side = `#side-${event.owner}`;
    const from = document.querySelector(`${side} .stack[data-zone="deck"] .stackart`);
    const to = document.querySelector(`${side} .hand`);
    Sfx.play("draw");
    if (!from || !to) return;
    const bounds = el("#table").getBoundingClientRect();
    const a = from.getBoundingClientRect();
    const b = to.getBoundingClientRect();

    const node = h("div", "drawn");
    node.appendChild(faceDown("mid"));
    node.style.left = a.left - bounds.left + "px";
    node.style.top = a.top - bounds.top + "px";
    node.dataset.born = performance.now();
    el("#fx").appendChild(node);

    // Land at the open edge of the hand, which is where the new card appears.
    const dx = (b.right - 30) - a.left;
    const dy = (b.top + b.height / 2 - 40) - a.top;
    const flight = node.animate([
      { transform: "translate(0,0) scale(.7) rotate(-8deg)", opacity: 0, easing: "ease-out" },
      { transform: `translate(${dx * 0.5}px, ${dy * 0.5}px) scale(1.05) rotate(4deg)`,
        opacity: 1, offset: 0.45, easing: "ease-in-out" },
      { transform: `translate(${dx}px, ${dy}px) scale(1) rotate(0deg)`, opacity: 1, offset: 0.85 },
      { transform: `translate(${dx}px, ${dy}px) scale(1)`, opacity: 0 },
    ], { duration: DRAW_FLIGHT, easing: "linear" });
    flight.finished.then(() => node.remove()).catch(() => node.remove());
    setTimeout(() => node.remove(), DRAW_FLIGHT + 400);
  }, delay);
}

/* A card being discarded — almost always a cost.
 *
 * Both players watch this one. Paying `<Discard a card>` is invisible from the
 * other side of the table: a hand gets shorter, a pile gets taller, and the
 * only person who knew a price was paid is the person who paid it. So the card
 * leaves the hand face up, crosses the mat, and lands on the trash where
 * anyone can go and read it.
 *
 * `nth` fans a multi-card cost out so three cards do not fly the same line.
 */
function playDiscard(event, delay = 0, nth = 0) {
  setTimeout(() => {
    const side = `#side-${event.owner}`;
    const uid = event.card && event.card.uid;
    // The card is already out of the hand by the time this plays, so the hand
    // row is the origin; the card's own slot only exists on a lucky frame.
    const from = (uid !== undefined
        && document.querySelector(`${side} .card[data-uid="${uid}"]`))
      || document.querySelector(`${side} .hand`);
    const to = document.querySelector(`${side} .stack[data-zone="trash"] .stackart`);
    Sfx.play("discard");
    if (!from || !to) return;
    const bounds = el("#table").getBoundingClientRect();
    const a = from.getBoundingClientRect();
    const b = to.getBoundingClientRect();
    const node = h("div", "discarded");
    node.appendChild(cardNode(event.card, { mid: true }));
    node.appendChild(h("div", "discardtag", "discarded"));
    node.dataset.born = performance.now();
    el("#fx").appendChild(node);

    // Placed after appending so the card's real size comes from the stylesheet
    // rather than being repeated here.
    const box = node.getBoundingClientRect();
    const spread = (nth % 3 - 1) * 26;
    const x0 = a.left + a.width / 2 - bounds.left - box.width / 2 + spread;
    const y0 = a.top - bounds.top;
    node.style.left = x0 + "px";
    node.style.top = y0 + "px";

    const dx = b.left + b.width / 2 - bounds.left - box.width / 2 - x0;
    const dy = b.top + b.height / 2 - bounds.top - box.height / 2 - y0;
    const flight = node.animate([
      { transform: "translate(0,0) scale(.9)", opacity: 0, easing: "ease-out" },
      // It hangs for a beat where it can be read before it is thrown away —
      // the whole point is that the *opponent* gets to see what was paid.
      { transform: `translate(${dx * 0.16}px, ${dy * 0.16}px) scale(1.12) rotate(-3deg)`,
        opacity: 1, offset: 0.16, easing: "ease-out" },
      { transform: `translate(${dx * 0.22}px, ${dy * 0.22}px) scale(1.12) rotate(-3deg)`,
        opacity: 1, offset: 0.5, easing: "ease-in" },
      { transform: `translate(${dx}px, ${dy}px) scale(.62) rotate(9deg)`,
        opacity: 1, offset: 0.92 },
      { transform: `translate(${dx}px, ${dy}px) scale(.58) rotate(9deg)`, opacity: 0 },
    ], { duration: DISCARD_FLIGHT, easing: "linear" });
    flight.finished.then(() => node.remove()).catch(() => node.remove());
    setTimeout(() => node.remove(), DISCARD_FLIGHT + 400);
  }, delay);
}

/* A skill, Item or Trap going off. Most of them change nothing you can see —
 * a draw, a buff, an effect that fizzles — so the card itself comes forward and
 * says what it did, which is the only confirmation the player gets. */
function playSkill(event, delay = 0) {
  setTimeout(() => {
    const anchor = (event.uid !== null && event.uid !== undefined
      && (document.querySelector(`[data-cookie="${event.uid}"]`)
        || document.querySelector(`.card[data-uid="${event.uid}"]`)))
      || document.querySelector(`#side-${event.owner} .zone.battle`);
    Sfx.play("skill");
    if (!anchor) return;
    const bounds = el("#table").getBoundingClientRect();
    const at = anchor.getBoundingClientRect();

    const box = cardBox();
    const node = h("div", "skillpop");
    node.style.left = at.left + at.width / 2 - bounds.left - box.w / 2 + "px";
    node.style.top = at.top + at.height / 2 - bounds.top - box.h / 2 + "px";
    const img = h("img");
    img.src = event.card.img;
    img.onerror = () => img.remove();
    node.appendChild(img);
    node.appendChild(h("div", "skillname", event.name));
    node.appendChild(h("div", "tagline", event.card.name));
    node.dataset.born = performance.now();
    el("#fx").appendChild(node);
    setTimeout(() => node.remove(), SKILL_HOLD);
  }, delay);
}

/* A trap or an item going off. Both are played from hand, do their thing and
 * go straight to the trash, so neither gets the small pop a skill gets: the
 * table dims, the card slams down twice the size in the middle of the board,
 * and whatever it did plays out underneath it. A trap is red and an item gold,
 * because a trap is the one that fires on someone else's turn. */
function playSpotlight(event, delay = 0) {
  const isTrap = event.type === "trap";
  setTimeout(() => {
    Sfx.play(isTrap ? "trap" : "skill");
    const table = el("#table");
    if (!table) return;

    // The part of the table that is actually on screen. Centring on the window
    // would put the card behind the side panel on a wide layout; centring on
    // the table element would put it off-screen on a scrolled one.
    const box = table.getBoundingClientRect();
    const top = Math.max(box.top, 0);
    const bottom = Math.min(box.bottom, window.innerHeight);

    // The board dims, not the whole window — the log stays readable.
    const veil = h("div", "trapveil");
    veil.style.left = box.left + "px";
    veil.style.top = top + "px";
    veil.style.width = box.width + "px";
    veil.style.height = (bottom - top) + "px";
    veil.dataset.born = performance.now();
    el("#fx").appendChild(veil);
    setTimeout(() => veil.remove(), TRAP_HOLD);

    const node = h("div", "trappop " + (isTrap ? "trap" : "item")
                   + (event.owner === state.mySeat ? " mine" : ""));
    node.style.left = box.left + box.width / 2 + "px";
    node.style.top = (top + bottom) / 2 + "px";
    const img = h("img");
    img.src = event.card.img;
    img.onerror = () => {
      img.remove();
      node.appendChild(h("div", "fallback", event.card.name));
    };
    node.appendChild(img);
    node.appendChild(h("div", "trapname", isTrap ? "TRAP" : "ITEM"));
    node.appendChild(h("div", "tagline", event.name || event.card.name));
    node.dataset.born = performance.now();
    el("#fx").appendChild(node);
    setTimeout(() => node.remove(), TRAP_HOLD);
  }, delay);
}

/* A Cookie leaving the battle area: it breaks apart and drops away. */
function playFaint(event, delay = 0) {
  setTimeout(() => {
    // Pre-commit the Cookie is still in its slot, so break it where it stood.
    const anchor = document.querySelector(`[data-cookie="${event.cookie}"]`)
      || document.querySelector(`#side-${event.owner} .zone.battle`);
    Sfx.play("break");
    if (!anchor) return;
    const bounds = el("#table").getBoundingClientRect();
    const at = anchor.getBoundingClientRect();

    const box = cardBox();
    const node = h("div", "breaker");
    node.style.left = at.left + at.width / 2 - bounds.left - box.w / 2 + "px";
    node.style.top = at.top + at.height / 2 - bounds.top - box.h / 2 + "px";
    node.style.setProperty("--face",
      document.body.classList.contains("flip-opponent") && event.owner !== 0 ? "180deg" : "0deg");

    // The card snaps in two: both halves are the whole card art, each clipped
    // to one side of the same jagged break line, so the crack lines up exactly.
    const crack = jaggedSeam();
    ["left", "right"].forEach((side) => {
      const half = h("div", "half " + side);
      half.style.clipPath = side === "left" ? crack.left : crack.right;
      const img = h("img");
      img.src = event.card.img;
      img.onerror = () => img.remove();
      half.appendChild(img);
      node.appendChild(half);
    });
    node.appendChild(h("div", "tagline",
      (event.broke ? "broken — " : "removed — ") + event.card.name));
    node.dataset.born = performance.now();
    el("#fx").appendChild(node);
    setTimeout(() => node.remove(), BREAK_MS);
  }, delay);
}

/** A ragged vertical tear, as a pair of complementary clip-path polygons. */
function jaggedSeam(teeth = 9) {
  const seam = [];
  for (let i = 0; i <= teeth; i++) {
    const y = (i / teeth) * 100;
    // Alternate either side of the centre line, with a little jitter so no two
    // breaks look alike.
    const x = 50 + (i % 2 ? 1 : -1) * (5 + Math.random() * 5);
    seam.push([x, y]);
  }
  const pt = ([x, y]) => `${x.toFixed(1)}% ${y.toFixed(1)}%`;
  return {
    left: `polygon(0% 0%, ${seam.map(pt).join(", ")}, 0% 100%)`,
    right: `polygon(100% 0%, ${seam.map(pt).join(", ")}, 100% 100%)`,
  };
}

/* The animation is the moment; this strip is the record of it, so a reveal you
 * blinked through is still there to read. */
function renderReveals(events) {
  const box = el("#reveals");
  box.innerHTML = "";
  if (!events || !events.length) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  box.appendChild(h("div", "reveal-title", events.length === 1
    ? "revealed off the HP pile"
    : `${events.length} revealed off the HP pile`));
  const row = h("div", "reveal-row");
  events.forEach((event) => {
    const item = h("div", "reveal-item" + (event.flip ? " isflip" : ""));
    // Drawn at the size a card in a hand is drawn, not as a thumbnail: this is
    // the standing record of a scene that went past in under a second, and a
    // 46px sliver of art is not something you can read a FLIP off.
    item.appendChild(cardNode(event.card, { mid: true }));
    item.appendChild(h("div", "cname", (event.flip ? "FLIP! " : "") + event.card.name));
    row.appendChild(item);
  });
  box.appendChild(row);
}

function flipCard(event, rank = 0, seq = 0) {
  // The Cookie is often gone by the time this runs — an emptied HP pile is a
  // faint — so fall back to its owner's battle area, which does not move.
  const host = document.querySelector(`[data-cookie="${event.cookie}"]`);
  const anchor = host || document.querySelector(`#side-${event.owner} .zone.battle`);
  const bounds = el("#table").getBoundingClientRect();
  const at = anchor ? anchor.getBoundingClientRect() : null;
  const offset = host ? rank : seq;   // no host: fan the whole batch instead

  const node = h("div", "flipper" + (event.flip ? " isflip" : ""));
  const box = cardBox();
  node.style.width = box.w + "px";
  node.style.height = box.h + "px";
  const left = at ? (host ? at.left : at.left + at.width / 2 - box.w / 2) - bounds.left - 4
                  : bounds.width / 2 - box.w / 2;
  const top = at ? (host ? at.top : at.top + at.height / 2 - box.h / 2) - bounds.top - 4
                 : bounds.height / 2 - box.h / 2;
  node.style.left = left + offset * 30 + "px";
  node.style.top = top - offset * 12 + "px";

  const inner = h("div", "inner");
  inner.appendChild(h("div", "face back card"));
  const front = h("div", "face front");
  const img = h("img");
  img.src = event.card.img;
  img.onerror = () => {
    img.remove();
    front.appendChild(h("div", "fallback", event.card.name));
  };
  front.appendChild(img);
  inner.appendChild(front);
  node.appendChild(inner);
  node.appendChild(h("div", "tagline",
    (event.flip ? "FLIP! " : "") + event.card.name));

  node.dataset.born = performance.now();
  el("#fx").appendChild(node);
  setTimeout(() => node.remove(), FLIP_MS + 300);
}

/* --------------------------------------------------- trash / break browser */
/* A modal <dialog> is promoted to the browser's top layer, which paints above
 * every z-index on the page — so the hover preview has to move *into* the
 * dialog to be seen over it, and back out again afterwards. */
/* Back to wherever the preview belongs for the view that is on screen. The
 * dock only exists on the play tab; the builder and the showcase hide the
 * panel, so there they go back to following the cursor. */
function restorePreview() {
  const panelHidden = document.body.classList.contains("view-build")
    || document.body.classList.contains("view-table")
    || !el("#showcase").classList.contains("hidden");
  hostPreview(panelHidden ? document.body : previewDock);
}

function hostPreview(node) {
  if (node === previewDock) { dockPreview(); return; }
  preview.classList.remove("indock");
  if (preview.parentElement !== node) node.appendChild(preview);
  hidePreview();
}

function openBrowser(seat, zoneName) {
  state.browsing = { seat, zone: zoneName };
  hostPreview(el("#browser"));
  el("#browser-search").value = "";
  renderBrowser();
  if (!el("#browser").open) el("#browser").showModal();
}

function renderBrowser() {
  if (!state.browsing || !state.snap || !state.snap.players) return;
  const { seat, zone: zoneName } = state.browsing;
  const player = state.snap.players[seat];
  const cards = { trash: player.trash, extra: player.extra }[zoneName]
    || player.break;
  const query = el("#browser-search").value.trim().toLowerCase();

  el("#browser-title").textContent =
    ({ trash: "Trash", extra: "EXTRA deck" }[zoneName] || "Break area")
    + ` · seat ${seat}`;

  // One entry per distinct card, because a trash of 30 cards is mostly copies.
  const groups = new Map();
  cards.forEach((card) => {
    const entry = groups.get(card.id) || { card, count: 0 };
    entry.count += 1;
    groups.set(card.id, entry);
  });
  const matches = [...groups.values()].filter(({ card }) => {
    if (!query) return true;
    return [card.name, card.type, card.color, card.text, card.id]
      .filter(Boolean).some((field) => field.toLowerCase().includes(query));
  });
  matches.sort((a, b) => b.count - a.count || a.card.name.localeCompare(b.card.name));

  const levels = zoneName === "break"
    ? ` · ${player.breakLevel} level banked` : "";
  el("#browser-count").textContent =
    `${cards.length} card${cards.length === 1 ? "" : "s"}, ${groups.size} distinct` +
    (query ? ` · ${matches.length} matching "${query}"` : "") + levels;

  const grid = el("#browser-grid");
  grid.innerHTML = "";
  matches.forEach(({ card, count }) => {
    const entry = h("div", "entry");
    entry.appendChild(cardNode(card));
    if (count > 1) entry.appendChild(h("span", "count", "×" + count));
    entry.appendChild(h("div", "cname", card.name));
    grid.appendChild(entry);
  });
  if (!matches.length) grid.appendChild(h("div", "hint", "nothing matches"));
}

el("#browser-search").addEventListener("input", renderBrowser);
el("#browser-close").onclick = () => { el("#browser").close(); state.browsing = null; };
el("#browser").addEventListener("close", () => {
  state.browsing = null;
  restorePreview();
});

/* ---------------------------------------------------------------- options */
/* Moves arrive in engine order, which on a Cookie in hand offers "place this
 * as support" above "play it". This is the order a player thinks in, and it
 * sorts the rows of a card's menu and the buttons in the middle of the table.
 * A kind not listed here sorts between the plays and the giving-up. */
const KIND_ORDER = ["Attack", "PlayCookie", "PlayExtra", "PlaySupportCard",
                    "ActivateSkill", "PlayTrap", "Block", "PlaceSupport",
                    "Pass", "EndTurn"];

function byKindOrder(a, b) {
  const rank = (o) => {
    const at = KIND_ORDER.indexOf(o.kind);
    return at === -1 ? KIND_ORDER.indexOf("PlaceSupport") - 0.5 : at;
  };
  return rank(a) - rank(b);
}

/* The opening toss reads better as a hand than as a word. */
const THROW_ICONS = { rock: "\u270a", paper: "\u270b", scissors: "\u270c\ufe0f" };

/* Any question that is really "point at a card in your hand" is answered with
 * the hand itself: a discard cost, the Cookie you open with, the replacement
 * you field when one faints. Toggle up to the number asked for, then confirm.
 * Choosing from a list on the far side of the screen while your hand sits at
 * the bottom is the wrong way round. */
function renderPicker(snap) {
  const bar = el("#picker");
  const pending = snap.pending;
  const pick = pending && pending.pick;
  const count = pick ? Math.max(1, pending.count || 1) : 0;
  // "Up to N" — confirming with fewer, or with none at all, is a real answer.
  const upTo = !!(pending && pending.upTo);
  if (!count) {
    bar.classList.add("hidden");
    bar.innerHTML = "";
    state.picked = [];
    return false;
  }
  if (bar.dataset.for !== String(pending.id)) {
    bar.dataset.for = String(pending.id);
    state.picked = [];
  }

  bar.classList.remove("hidden");
  bar.innerHTML = "";
  const head = h("div", "picker-head");
  head.appendChild(h("span", "picker-prompt", pending.prompt));
  head.appendChild(h("span", "picker-count",
    `${state.picked.length} / ${upTo ? "up to " : ""}${count}`));
  bar.appendChild(head);

  const row = h("div", "picker-row");
  pending.options.forEach((opt) => {
    const wrap = h("div", "picker-card" + (state.picked.includes(opt.index) ? " picked" : ""));
    const card = cardNode({ img: opt.img, name: opt.label, uid: opt.subject },
                          { mid: true });
    wrap.appendChild(card);
    wrap.appendChild(h("div", "cname", opt.label));
    wrap.onclick = () => {
      const at = state.picked.indexOf(opt.index);
      if (at >= 0) state.picked.splice(at, 1);
      else if (count === 1 && !upTo) state.picked = [opt.index];
      else if (state.picked.length < count) state.picked.push(opt.index);
      renderPicker(snap);
    };
    row.appendChild(wrap);
  });
  /* Cards you are looking at but may not take. "View the top 3 cards of your
   * deck, add 1 {P} card to your hand" is two instructions: seeing all three
   * *is* the effect, and only the second instruction is the question. They are
   * drawn dimmed and inert, after the ones you can choose, so the strip shows
   * the whole view without ever offering an illegal answer. Hover still works
   * — you are allowed to read what you cannot have. */
  (pending.shown || []).forEach((opt) => {
    const wrap = h("div", "picker-card locked");
    wrap.appendChild(cardNode({ img: opt.img, name: opt.label, uid: opt.subject },
                              { mid: true }));
    wrap.appendChild(h("div", "cname", opt.label));
    wrap.title = "Viewed, but this card does not meet the condition";
    row.appendChild(wrap);
  });
  bar.appendChild(row);

  const foot = h("div", "picker-foot");
  const verb = pick.verb || "Choose";
  const confirm = h("button", "primary",
    count > 1 || upTo ? `${verb} ${state.picked.length || ""}`.trim() : verb);
  confirm.disabled = upTo ? false : state.picked.length !== count;
  confirm.onclick = () => {
    const picks = state.picked.slice();
    state.picked = [];
    bar.classList.add("hidden");
    // A single pick is still a single answer; only a batch sends a list — and
    // "up to 1" is a batch, because "none" is one of its answers.
    answer(count > 1 || upTo ? picks : picks[0]);
  };
  foot.appendChild(h("span", "hint", upTo
    ? (state.picked.length ? `${state.picked.length} of up to ${count}` : "none is allowed")
    : (state.picked.length === count ? "ready"
       : `choose ${count - state.picked.length} more`)));
  /* An optional question is only half a question without the no. It used to
   * come off the move list on the right; that list is gone, so the decline
   * travels with the strip that took the question over. "Up to N" already has
   * one — confirming with nothing picked is its no. */
  if (pending.optional && !upTo) {
    const no = h("button", "ghost", "Decline");
    Confirm.wire(no, "decline", () => {
      state.picked = [];
      bar.classList.add("hidden");
      answer(null);
    });
    foot.appendChild(no);
  }
  foot.appendChild(confirm);
  bar.appendChild(foot);
  return true;
}

/* The opening toss, played in the middle of the table. For those few seconds it
 * is the only thing happening, so it should not be a list in the far corner. */
function renderCentre(snap) {
  const bar = el("#centre");
  const pending = snap.pending;
  const style = pending && pending.centre;
  if (!style) {
    bar.classList.add("hidden");
    bar.innerHTML = "";
    return false;
  }
  bar.classList.remove("hidden");
  bar.innerHTML = "";
  bar.appendChild(h("div", "centre-prompt", pending.prompt));

  /* Cards attached to the question itself. "View the top 3 of your deck, add
   * 1 {P} card to your hand" still shows you three cards when none of them is
   * purple — that is the case where knowing what went past matters most, and
   * skipping it would have made the commonest miss the one time the card
   * appeared to do nothing. There is nothing to choose, so it renders as a
   * look and an acknowledgement rather than as a question. */
  const shown = pending.shown || [];
  const lookOnly = shown.length && style === "yesno" && pending.options.length === 1;
  if (shown.length) {
    const strip = h("div", "centre-cards");
    shown.forEach((opt) => {
      const item = h("div", "centre-card");
      item.appendChild(cardNode({ img: opt.img, name: opt.label, uid: opt.subject },
                                { mid: true }));
      item.appendChild(h("div", "cname", opt.label));
      strip.appendChild(item);
    });
    bar.appendChild(strip);
  }

  const row = h("div", "centre-row" + (style === "throw" ? " throws" : " choices"));
  pending.options.forEach((opt) => {
    const btn = h("button", "centre-btn" + (style === "yesno" ? " yes" : ""));
    const icon = THROW_ICONS[opt.label];
    if (icon) btn.appendChild(h("span", "big", icon));
    btn.appendChild(h("span", "label", lookOnly ? "OK" : opt.label));
    // A toss, a mulligan and a paid <...> cost are all one button in the
    // middle of the table. None of them is a move you can take back, but they
    // are read before they are pressed, so only "every move" holds them.
    Confirm.wire(btn, opt, () => answer(opt.index));
    row.appendChild(btn);
  });
  /* A yes/no is only half a question without the no. Declining lives on the
   * option list for everything else, but this question has left that list, so
   * the answer has to come with it. A look-and-acknowledge has no no: both
   * buttons would do the same thing. */
  if (style === "yesno" && pending.optional && !lookOnly) {
    const no = h("button", "centre-btn no");
    no.appendChild(h("span", "label", "No"));
    Confirm.wire(no, "decline", () => answer(null));
    row.appendChild(no);
  }
  bar.appendChild(row);
  return true;
}

/* The panel's one line about what the game is waiting for.
 *
 * This used to be the move list — every legal action as a button, stacked down
 * the right-hand side. It was the biggest thing on screen and the least
 * pleasant to use: it named cards you were already looking at, it grew to
 * twenty rows on a busy turn, and it took the space the card you were hovering
 * should have had. The moves live on the cards now, so all that is left here is
 * a sentence saying where to click. It is deliberately one line: everything
 * below it is the card viewer and the log.
 */
function renderOptions(snap) {
  const box = el("#options");
  box.innerHTML = "";
  const pending = snap.pending;
  const lastLine = (snap.log || []).slice(-1)[0] || "";
  const prompt = inSupportStep(snap) ? SUPPORT_PROMPT : (pending ? pending.prompt : null);
  // Nobody is being asked anything during a replay — both seats are reading
  // from the file — so "Bots playing" would be a lie about a game that has
  // already been played.
  const idle = snap.replay
    ? (snap.replay.note ? "Replay ended" : "Replaying…")
    : "Bots playing…";
  setText(el("#prompt"), prompt || (snap.over ? "Game over" : idle));
  el("#prompt-who").textContent = pending ? seatLabel(pending.seat, snap) : lastLine;

  const say = (text, cls) => box.appendChild(h("div", "filterbar" + (cls ? " " + cls : ""), text));
  if (!pending) return;
  if (pending.waiting) {
    // The question is the other seat's, and its options are that seat's hand,
    // so this browser was never sent them. Show what they are being asked.
    say("waiting for your opponent…", "waiting");
    return;
  }
  if (pending.centre) { say("answer in the middle of the table"); return; }
  if (pending.pick) {
    say(pending.count > 1 ? "pick the cards in the strip below"
                          : "pick the card in the strip below");
    return;
  }

  const { asked, actionable } = actionSets();
  if (asked.size) {
    say(asked.size === 1 ? "click the highlighted card"
                         : `click one of the ${asked.size} highlighted cards`);
    return;
  }
  if (actionable.size) {
    say(`click a card for its moves — ${actionable.size} can act`);
    return;
  }
  if (homelessOptions(snap).length) { say("answer in the middle of the table"); return; }
  say("nothing to do");
}

/* What an option looks like in the preview dock. An option is not a card — it
 * is a move — so this is the card it is about, found on the board or in the
 * hand by uid, and falling back to the thumbnail and label the option carries
 * when the move names nothing you can see (a banked rider, an EXTRA play). */
function optionCard(opt) {
  if (!opt) return null;
  // Target before subject: hovering "Attack \u2192 Wind Archer Cookie" should show
  // you what you are about to hit, not the Cookie you already clicked on.
  const uid = opt.target !== undefined ? opt.target
    : opt.subject !== undefined ? opt.subject : (opt.uids || [])[0];
  const known = uid !== undefined ? cardByUid(uid) : null;
  if (known) return known;
  if (!opt.img && !opt.label) return null;
  return { img: opt.img, name: opt.label };
}

/** Every card this browser can see, by uid — the board, both hands, the piles. */
function cardByUid(uid) {
  const snap = state.snap;
  if (!snap || !snap.players) return null;
  for (const p of snap.players) {
    for (const cookie of p.battle || []) {
      if (cookie.card && cookie.card.uid === uid) return cookie.card;
    }
    for (const zone of [p.hand, p.support, p.stage, p.trash, p.extra, p["break"]]) {
      const hit = (zone || []).find((c) => c && c.uid === uid);
      if (hit) return hit;
    }
  }
  return null;
}

function highlight(opt) {
  document.querySelectorAll(".card.hl, .card.hl-target")
    .forEach((n) => n.classList.remove("hl", "hl-target"));
  if (!opt) return;
  (opt.uids || (opt.subject !== undefined ? [opt.subject] : [])).forEach((uid) => {
    document.querySelectorAll(`.card[data-uid="${uid}"]`).forEach((n) => {
      n.classList.add(uid === opt.target ? "hl-target" : "hl");
    });
  });
}

/* One click, one card, and one place every move comes from.
 *
 * There is no move list on the right any more, so this is the whole vocabulary
 * for acting on a card. A question that names the card answers with it on the
 * first click — "damage which Cookie?" should not make you open a menu to say
 * "that one". Anything else opens the card's own menu of what it can do, which
 * is where attacks, skills, plays and supports live. It runs the same on both
 * sides of the table: your Cookie's menu holds its attacks, your opponent's
 * Cookie answers the question that is pointing at it.
 */
function cardClick(uid, node) {
  // A drag that ends on top of its own card fires a click too. That was a
  // drag, and it has already been answered or cancelled.
  if (Date.now() - lastDragEnd < 250) return;
  // The tail of a completed hold, which has already answered.
  if (Confirm.consumed()) return;
  const direct = directOption(uid);
  if (direct) {
    // Held rather than clicked: the press handler on the card owns this one,
    // and a click that got here is a click that let go too early. Saying so
    // beats opening a menu nobody asked for.
    if (Confirm.needsHold(direct)) { Confirm.hint("hold to " + Confirm.name(direct)); return; }
    closeCardMenu();
    answer(direct.index);
    return;
  }
  // No moves is not "nothing happens": a menu left open over the last card has
  // to shut, which `openCardMenu` does before it decides there is nothing to
  // show. Hovering the card has already put it in the panel, so a dead card
  // clicked is a card looked at.
  openCardMenu(uid, node);
}

/** The pending option that names this card, if the question is ours to answer. */
function directOption(uid) {
  const pending = state.snap && state.snap.pending;
  if (!pending || state.animating || pending.waiting) return null;
  // A "pick N of these" question is answered as a batch on the picker bar. One
  // index sent to a question expecting a list is padded out by the engine with
  // cards nobody chose, so pointing at a single card must not answer it.
  if ((pending.count || 1) > 1 || pending.upTo) return null;
  return (pending.options || []).find(
    (o) => (o.kind === "cookie" || o.kind === "card") && o.subject === uid) || null;
}

/* --------------------------------------------------------- the card menu */
/* Clicking a card asks it what it can do. Each move is listed by the name the
 * card prints for it — an attack's name, "Tracker's Arrow" — falling back to
 * "Attack" for the older unnamed printings and to "Activate" for skills, none
 * of which are named anywhere in the pool. */
function closeCardMenu() {
  const open = el("#cardmenu");
  if (open) open.remove();
}

function openCardMenu(uid, node) {
  closeCardMenu();
  const moves = movesFor(uid);
  if (!moves.length) return;

  moves.sort(byKindOrder);
  const menu = h("div", "cardmenu");
  menu.id = "cardmenu";
  const card = (state.snap.players || [])
    .flatMap((p) => [...p.hand, ...p.support, ...p.stage, ...p.battle.map((c) => c.card)])
    .find((c) => c && c.uid === uid);
  const owner = node.closest(".cookiebox");
  const title = (owner && state.snap.players.flatMap((p) => p.battle)
    .find((c) => String(c.uid) === owner.dataset.cookie));
  menu.appendChild(h("div", "cardmenu-title",
    (title && title.card.name) || (card && card.name) || "Card"));

  moves.forEach((opt, i) => {
    const row = h("button", "cardmenu-row");
    if (i < 9) row.appendChild(h("span", "k", String(i + 1)));
    row.appendChild(h("span", "skill", opt.skill || "Play"));
    // The engine's own description carries the target and the numbers.
    const detail = opt.label.replace(/^[^:]*:\s*/, "").replace(/^Play\s+/, "");
    if (detail && detail !== opt.skill) row.appendChild(h("span", "detail", detail));
    // Same hover contract as everything else: the board lights up and the card
    // the move is about lands in the panel. For an attack that is the Cookie
    // you are about to hit, which is the one you want to read before swinging.
    row.onmouseenter = () => { highlight(opt); showPreview(optionCard(opt)); };
    row.onmouseleave = () => { highlight(null); hidePreview(); };
    Confirm.wire(row, opt, () => { closeCardMenu(); answer(opt.index); });
    menu.appendChild(row);
  });

  document.body.appendChild(menu);
  const rect = node.getBoundingClientRect();
  const width = menu.offsetWidth;
  const height = menu.offsetHeight;
  let left = rect.right + 8;
  if (left + width > window.innerWidth) left = Math.max(8, rect.left - width - 8);
  let top = rect.top;
  if (top + height > window.innerHeight) top = Math.max(8, window.innerHeight - height - 8);
  menu.style.left = left + "px";
  menu.style.top = top + "px";
}

window.addEventListener("pointerdown", (event) => {
  const menu = el("#cardmenu");
  if (menu && !menu.contains(event.target)) closeCardMenu();
}, true);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeCardMenu();
});

/* --------------------------------------------------------- drag and drop */
/* Dragging is a second way to name an option that already exists, never a new
 * kind of move: a drag resolves to (subject, target) and then looks for the one
 * legal action that matches. Anything ambiguous or unmatched falls back to the
 * list on the right, so the drag layer can never invent an illegal play. */

function pendingOptions() {
  const snap = state.snap;
  if (!snap || !snap.pending || state.animating) return [];
  return narrow(snap, snap.pending.options);
}

/* What the cards on screen can do right now, at two different volumes.
 *
 * `asked` is a question the game has stopped for: a trap you can spring in a
 * response window, a Cookie you can block with, the Cookie you open with, the
 * Cookie an effect wants you to point at. There are two or three of them among
 * a dozen dead cards and nothing proceeds until you choose, so they get the
 * loud treatment — raised out of the hand, ringed and pulsing.
 *
 * `actionable` is the quiet one: every card with something in its menu. On a
 * good turn that is most of your hand and both your Cookies, which is exactly
 * why it cannot share the loud signal — a glow on everything means nothing. It
 * is a soft ring that says "there is a menu behind this click", and it is the
 * thing that replaced reading the move list to find out what was live.
 */
function actionSets() {
  const asked = new Set();
  const actionable = new Set();
  const pending = state.snap && state.snap.pending;
  if (!pending) return { asked, actionable };
  const options = pendingOptions();
  options.forEach((o) => {
    if (o.subject !== undefined) actionable.add(o.subject);
  });
  // A response window is the one moment your cards act on someone else's turn,
  // and a trap or a blocker is the only thing in it that can. The engine has
  // already dropped the ones whose effect would land on nothing.
  options.forEach((o) => {
    if (o.kind === "PlayTrap" || o.kind === "Block") asked.add(o.subject);
  });
  // Everything else only counts as asked if pointing at one card *is* a whole
  // answer: a batch goes to the picker strip, and a `pick` or a `centre`
  // question has already claimed its own place on screen. `directOption`'s rule.
  if (!pending.pick && !pending.centre
      && (pending.count || 1) === 1 && !pending.upTo) {
    options.forEach((o) => {
      if ((o.kind === "card" || o.kind === "cookie") && o.subject !== undefined) {
        asked.add(o.subject);
      }
    });
  }
  return { asked, actionable };
}

/* Paint those two volumes onto the finished markup for one side of the table.
 *
 * A pass over the built nodes rather than a flag threaded through each builder:
 * the cards come from four of them — hand, battle area, support, stage — and a
 * rule that has to be remembered in four places is a rule that gets forgotten
 * in one of them. `quietToo` is off for your opponent's half, where a card
 * having a menu is none of your business; being *asked* about one still is. */
function markActionable(root, quietToo) {
  const { asked, actionable } = actionSets();
  if (!asked.size && !(quietToo && actionable.size)) return;
  root.querySelectorAll(".card.pickable[data-uid]").forEach((node) => {
    const uid = Number(node.dataset.uid);
    if (asked.has(uid)) node.classList.add("armed");
    else if (quietToo && actionable.has(uid)) node.classList.add("actionable");
  });
}

/** Legal actions that this card could be the subject of. */
function movesFor(uid) {
  return pendingOptions().filter((o) => o.subject === uid);
}

/** Where a given card is allowed to be dropped, as CSS selectors. */
function dropTargetsFor(uid) {
  const targets = [];
  movesFor(uid).forEach((opt) => {
    if (opt.kind === "Attack" && opt.target !== undefined) {
      targets.push({ sel: `[data-cookie="${opt.target}"]`, opt });
    } else if (opt.kind === "PlaceSupport") {
      targets.push({ sel: "#side-0 .zone.support", opt });
    } else if (opt.kind === "PlayCookie" || opt.kind === "PlaySupportCard") {
      targets.push({ sel: "#side-0 .zone.battle", opt });
      if (opt.kind === "PlaySupportCard") targets.push({ sel: "#side-0 .zone.stage", opt });
    } else if (opt.kind === "PlayTrap" || opt.kind === "ActivateSkill") {
      targets.push({ sel: "#side-0 .zone.battle", opt });
    }
  });
  return targets;
}

let dragging = null;
// When the last drag finished. A drag that ends over its own card fires a
// click as well, and that click must not be read as pointing at the card.
let lastDragEnd = 0;

function startDrag(event, uid, node) {
  const targets = dropTargetsFor(uid);
  if (!targets.length) return;                 // nothing this card can do
  event.preventDefault();
  const rect = node.getBoundingClientRect();
  const ghost = node.cloneNode(true);
  ghost.classList.add("dragging");
  ghost.style.width = rect.width + "px";
  ghost.style.height = rect.height + "px";
  document.body.appendChild(ghost);
  hidePreview();

  dragging = {
    uid, node, ghost, targets,
    dx: event.clientX - rect.left,
    dy: event.clientY - rect.top,
    hovered: null,
  };
  node.classList.add("dragsource");
  targets.forEach(({ sel }) => {
    const drop = document.querySelector(sel);
    if (drop) drop.classList.add("droppable");
  });
  moveDrag(event);
  window.addEventListener("pointermove", moveDrag);
  window.addEventListener("pointerup", endDrag);
}

function moveDrag(event) {
  if (!dragging) return;
  dragging.ghost.style.left = event.clientX - dragging.dx + "px";
  dragging.ghost.style.top = event.clientY - dragging.dy + "px";
  const under = dropUnder(event);
  if (under !== dragging.hovered) {
    document.querySelectorAll(".dropactive").forEach((n) => n.classList.remove("dropactive"));
    if (under) under.node.classList.add("dropactive");
    dragging.hovered = under;
  }
}

function dropUnder(event) {
  for (const { sel, opt } of dragging.targets) {
    const node = document.querySelector(sel);
    if (!node) continue;
    const r = node.getBoundingClientRect();
    if (event.clientX >= r.left && event.clientX <= r.right
        && event.clientY >= r.top && event.clientY <= r.bottom) {
      return { node, opt };
    }
  }
  return null;
}

function endDrag(event) {
  window.removeEventListener("pointermove", moveDrag);
  window.removeEventListener("pointerup", endDrag);
  if (!dragging) return;
  const drop = dropUnder(event);
  dragging.ghost.remove();
  dragging.node.classList.remove("dragsource");
  document.querySelectorAll(".droppable, .dropactive")
    .forEach((n) => n.classList.remove("droppable", "dropactive"));
  const uid = dragging.uid;
  dragging = null;
  lastDragEnd = Date.now();
  if (drop) {
    Sfx.play("place");
    answer(drop.opt.index);
  } else {
    // A drag that goes nowhere is a click: ask the card what it can do.
    const node = document.querySelector(`.card[data-uid="${uid}"]`);
    if (node) openCardMenu(uid, node);
  }
}

/** Make a card draggable when it has a legal move behind it. */
function makeDraggable(node, uid, seat) {
  if (seat !== state.mySeat || !playableSeat()) return;
  node.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    if (movesFor(uid).length) node.classList.add("grabbable");
    startDrag(event, uid, node);
  });
}

async function answer(index) {
  // The list on screen belongs to the question that is being animated, not the
  // one the server is asking now; an index from it would answer the wrong thing.
  if (state.busy || state.animating) return;
  // A click already travelling when the board redrew under it lands on
  // whatever moved into that spot. Nobody reads a question and answers it in a
  // quarter of a second, so an answer this early is a misclick, not a move.
  if (!Confirm.settled()) {
    Confirm.hint("the board just changed — take that again");
    return;
  }
  // What was chosen, before it is spent: the guided game watches for the verbs
  // it is teaching, and the option list is gone by the time the answer lands.
  if (typeof Tut !== "undefined" && Tut.on && index !== null && state.snap
      && state.snap.pending) {
    Tut.answered((state.snap.pending.options || [])
      .find((o) => o.index === index));
  }
  state.busy = true;
  el("#options").innerHTML = "";
  try {
    // The pending id goes with the answer so the server can drop it if the
    // question has already moved on — over a network that is not theoretical.
    await api("/api/choose", {
      index,
      pendingId: state.snap && state.snap.pending ? state.snap.pending.id : null,
      ...roomAuth(),
    });
  } finally {
    state.busy = false;
  }
  poll();
}

/** What identifies this seat to the server, on every move it makes. */
function roomAuth() {
  // A peer game has no room and no seat token: this server plays one seat, the
  // one its handshake gave it, and the browser cannot ask to answer for the
  // other machine even if it wanted to.
  if (state.peer) return { peer: true };
  if (!state.room) return {};
  // `pass` is the room's key rather than this seat's: over the internet the
  // four-character code is not enough to find a game, so it travels with every
  // request the same way it travelled in the link. The server ignores it on a
  // LAN, where the code was never the only thing standing in the way.
  const auth = { room: state.room, token: state.token };
  if (state.key) auth.pass = state.key;
  return auth;
}

/* -------------------------------------------------------------------- log */
/* -------------------------------------------------- card names in the log */
/* The log is prose, and the only handle it gives on a card is its name:
 * "Wind Archer Cookie takes 3 attack damage from Gold Citrine Cookie's
 * Tracker's Arrow". Three cards in one line, none of them clickable, and two
 * of them quite possibly not on the board any more. So every name in the log
 * becomes hoverable and previews in the panel exactly as a card on the table
 * does — which is also the fastest way to find out what a FLIP you blinked
 * through actually said.
 *
 * The index is every distinct card name in the pool, fetched once. Not the
 * cards in this game: the log names cards from a trash, a deck, and cards that
 * have already left the board entirely, so nothing narrower would be right. */
const cardIndex = { byName: new Map(), matcher: null };

async function loadCardIndex() {
  try {
    const data = await api("/api/cardnames");
    (data.cards || []).forEach((card) => cardIndex.byName.set(card.name, card));
    // Longest first, so "Wind Archer Cookie" wins over a shorter name inside
    // it. The server already sorts that way; the alternation preserves it.
    const names = [...cardIndex.byName.keys()]
      .map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    if (names.length) cardIndex.matcher = new RegExp("(" + names.join("|") + ")", "g");
  } catch (err) {
    // A log without hover is a log; it is not worth failing the page over.
    cardIndex.matcher = null;
  }
}
loadCardIndex();

/** One log line, with every card name in it turned into a hover target. */
function logLine(line) {
  const li = h("li");
  if (!cardIndex.matcher) { li.textContent = line; return li; }
  cardIndex.matcher.lastIndex = 0;
  let at = 0;
  let match;
  while ((match = cardIndex.matcher.exec(line)) !== null) {
    if (match.index > at) li.appendChild(document.createTextNode(line.slice(at, match.index)));
    const card = cardIndex.byName.get(match[0]);
    const tag = h("span", "logcard", match[0]);
    tag.addEventListener("mouseenter", (e) => { preview.dataset.follow = "1"; showPreview(card, e); });
    tag.addEventListener("mouseleave", () => { delete preview.dataset.follow; hidePreview(); });
    li.appendChild(tag);
    at = match.index + match[0].length;
  }
  if (at < line.length) li.appendChild(document.createTextNode(line.slice(at)));
  return li;
}

function renderLog(snap) {
  const box = el("#log");
  const lines = snap.log || [];
  box.innerHTML = "";
  lines.forEach((line, i) => {
    const li = logLine(line);
    let cls = i >= lines.length - 3 ? "new" : "";
    // The engine names the source, so the log can colour a swing differently
    // from a "Then, ..." rider, a skill or a trap.
    if (line.includes(" attack damage")) cls += " hit-attack";
    else if (line.includes(" effect damage")) cls += " hit-effect";
    else if (line.includes(" attacks ")) cls += " declare";
    else if (line.includes("faints")) cls += " faint";
    if (cls.trim()) li.className = cls.trim();
    box.appendChild(li);
  });
  box.scrollTop = box.scrollHeight;
}

/* ------------------------------------------------------------------- poll */
function render(snap) {
  state.snap = snap;
  // Which question is on screen, and since when: both the settle guard and the
  // holds are scoped to one question, so a new one cancels and re-arms them.
  Confirm.question(snap.pending ? snap.pending.id : null);
  supportStepSync(snap);
  if (!snap.players) {
    renderTurnline(snap);
    renderOptions(snap);
    renderBanner(snap);
    return;
  }
  state.turning = 0;
  renderSide(1, snap);
  renderSide(0, snap);
  renderTurnline(snap);
  renderBanner(snap);
  renderTray(snap);
  renderOptions(snap);
  renderLog(snap);
  // Swap in what this render just drew, so the next one can see what moved.
  state.restState = state.restSeen;
  state.restSeen = new Map();
  renderCentre(snap);
  renderPicker(snap);
  if (state.browsing) renderBrowser();
  if (snap.over && !state.announced) {
    state.announced = true;
    Sfx.play("win");
  } else if (!snap.over) {
    // A rematch is a new game arriving over the top of a finished one, and only
    // the player who asked for it goes through the reset above.
    state.announced = false;
  }
  // The guided game reads the same snapshot everything else does; it is loaded
  // after this file, so it is checked for rather than assumed.
  if (typeof Tut !== "undefined" && Tut.on) Tut.sync(snap);
  // Asked again here as well as in `poll`: the last scene of a match plays out
  // after its snapshot arrived, and the end-of-match card waits for it.
  if (typeof Title !== "undefined") Title.sync(snap);
  el("#btn-pause").textContent = snap.paused ? "Resume" : "Pause";
  // Spectator's tool only: there is nothing to reveal in a match you are playing.
  const playing = (snap.humanSeats || []).length > 0;
  const reveal = el("#reveal");
  reveal.checked = !!snap.reveal && !playing;
  reveal.disabled = playing;
  reveal.parentElement.classList.toggle("disabled", playing);
  reveal.parentElement.title = playing
    ? "Only for watching two bots — your opponent's hand stays hidden while you play"
    : "Show both hands (HP piles stay face down until revealed)";
}

/** Bin any animation left over from an earlier scene. */
function sweepFx() {
  const now = performance.now();
  el("#fx").querySelectorAll("[data-born]").forEach((node) => {
    if (now - Number(node.dataset.born) > 6000) node.remove();
  });
}

function commit(snap) {
  sweepFx();
  closeCardMenu();
  state.version = snap.version;
  state.pendingId = snap.pending ? snap.pending.id : null;
  render(snap);
  // The arriving Cookie's card is parked on its slot on `fill: forwards`, so
  // the real one has to be under it before it goes — bin it after the render,
  // not before, or the slot flashes empty between the two.
  el("#fx").querySelectorAll(".summonpop").forEach((node) => node.remove());
}

/* The board is deliberately a beat behind the server while a scene is playing.
 *
 * The events describe what an action *did*, so they have to be animated against
 * the board as it was before it: the attacker has to lunge at a Cookie that is
 * still standing, and an HP card has to turn face up over the pile it came off.
 * Rendering the aftermath first — which is what this used to do — left the
 * attacker swinging at an empty slot whenever it killed something. So the new
 * state is held back until the scene finishes, and nothing stale stays
 * clickable in the meantime. */
function playThenCommit(snap) {
  const duration = playEvents(snap.events);
  if (!duration) { commit(snap); return; }
  state.animating = true;
  state.version = snap.version;
  state.pendingId = snap.pending ? snap.pending.id : null;
  el("#options").innerHTML = "";
  el("#prompt").textContent = "…";
  setTimeout(() => {
    state.animating = false;
    const latest = state.queuedSnap || snap;
    state.queuedSnap = null;
    commit(latest);
  }, duration);
}

let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    /* Ask to be held until something changes. The server answers the moment
     * the opponent moves — which over a network is the difference between a
     * game that feels live and one that feels like a turn-based email — and
     * otherwise hangs up quietly after its own timeout. `since` is dropped
     * while a scene is playing, so the queued state stays fresh. */
    const params = new URLSearchParams();
    if (state.peer) params.set("peer", "1");
    if (state.room) {
      params.set("room", state.room);
      if (state.token) params.set("token", state.token);
      if (state.key) params.set("pass", state.key);
    }
    if (state.version >= 0 && !state.animating) params.set("since", state.version);
    const query = params.toString();
    const snap = await api("/api/state" + (query ? "?" + query : ""));
    if (snap.gone) { roomIsGone(); return; }
    // The title screen and the end-of-match prompt are hung off the snapshot
    // here rather than off `render`, because the two states they care about —
    // no match at all, and a room's lobby — both return before any drawing.
    // It is loaded after this file, so it is checked for rather than assumed.
    if (typeof Title !== "undefined") Title.sync(snap);
    if (state.room) {
      state.lobby = snap.room || null;
      // A refresh with a stale token comes back as a spectator; say so rather
      // than silently drawing a game the person thinks they are playing.
      if (snap.seat !== undefined && snap.seat !== null && snap.seat !== state.mySeat) {
        seatPerspective(snap.seat);
      }
      renderRoomBar(snap);
      if (snap.lobby) { renderLobby(snap); return; }
      hideLobby();
    }
    if (state.animating) { state.queuedSnap = snap; return; }
    const pendingId = snap.pending ? snap.pending.id : null;
    if (snap.version === state.version && pendingId === state.pendingId) return;
    // On the first snapshot after a load there is no board to animate against —
    // the scene in it already happened — so adopt it and draw the state as is.
    const firstSight = state.version === -1;
    if (snap.eventId && snap.eventId !== state.eventId && !firstSight) {
      state.eventId = snap.eventId;
      playThenCommit(snap);
    } else {
      if (snap.eventId) state.eventId = snap.eventId;
      commit(snap);
    }
  } catch (err) {
    el("#turnline").textContent = "server unreachable";
  } finally {
    polling = false;
  }
}

/* --------------------------------------------------------------- controls */
el("#btn-pause").onclick = async () => {
  const paused = !(state.snap && state.snap.paused);
  await api("/api/control", { paused });
  poll();
};
el("#btn-step").onclick = async () => { await api("/api/control", { step: true }); poll(); };
/* Everything that is a preference rather than a move now lives behind this. */
el("#btn-settings").onclick = () => el("#settings").showModal();
el("#speed").oninput = (e) => {
  el("#speed-label").textContent = (e.target.value / 1000).toFixed(2) + "s";
};
el("#speed").onchange = async (e) => { await api("/api/control", { delay: e.target.value / 1000 }); };
el("#reveal").onchange = async (e) => { await api("/api/control", { reveal: e.target.checked }); poll(); };
const soundBox = el("#sound");
soundBox.checked = Sfx.enabled;
soundBox.onchange = () => {
  Sfx.enabled = soundBox.checked;
  if (soundBox.checked) Sfx.play("place");   // confirm it is actually audible
};

/* How much friction a move that cannot be taken back should carry. Purely a
 * preference of this browser's owner — the match neither knows nor cares. */
const confirmBox = el("#confirm-level");
const holdBox = el("#hold-ms");
confirmBox.value = Confirm.level;
confirmBox.onchange = () => {
  Confirm.level = confirmBox.value;
  syncHoldBox();
  if (state.snap) render(state.snap);   // the affordances change with it
};

/* How long a held move takes to commit. The list is presets, not the range the
 * setting accepts, so a value set another way — an older list, a console, a
 * hand-edited localStorage — is shown rather than silently rounded away. */
holdBox.onchange = () => {
  Confirm.holdMs = holdBox.value;
  syncHoldBox();
};

function syncHoldBox() {
  const ms = Confirm.holdMs;
  if (![...holdBox.options].some((o) => Number(o.value) === ms)) {
    const own = new Option((ms / 1000).toFixed(2).replace(/0$/, "") + "s", String(ms));
    holdBox.appendChild(own);
  }
  holdBox.value = String(ms);
  // Nothing is held when nothing asks to be, so the length has nothing to say.
  const idle = Confirm.level === "off";
  holdBox.disabled = idle;
  el("#hold-label").classList.toggle("disabled", idle);
}
syncHoldBox();

/* Purely a view preference, so it lives in the browser rather than the match. */
const flipOpp = el("#flipopp");
flipOpp.checked = localStorage.getItem("flipOpponent") !== "0";
document.body.classList.toggle("flip-opponent", flipOpp.checked);
flipOpp.onchange = () => {
  localStorage.setItem("flipOpponent", flipOpp.checked ? "1" : "0");
  document.body.classList.toggle("flip-opponent", flipOpp.checked);
};

el("#btn-copy-log").onclick = () => {
  navigator.clipboard.writeText(((state.snap && state.snap.log) || []).join("\n"));
};

/* Whether this key is someone typing rather than reaching for a shortcut.
 *
 * Every board shortcut is a bare character — space pauses, 1-9 take an option —
 * so inside a text field all of them are keystrokes the field wanted. Space is
 * the one that bites: it is swallowed by preventDefault and never reaches the
 * deck builder's search box at all. Asking what has focus is the fix; the old
 * guard named two dialogs, which could only ever cover the fields someone had
 * remembered to add to it. */
function isTyping(event) {
  const node = event.target;
  if (!node) return false;
  if (node.isContentEditable) return true;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(node.tagName);
}

document.addEventListener("keydown", (e) => {
  if (isTyping(e)) return;
  if (el("#setup").open || el("#browser").open || el("#settings").open) return;
  // The number keys used to index the move list. They index whatever is on
  // screen offering itself instead: the rows of an open card menu first — that
  // is the list you are looking at — and otherwise the tray in the middle of
  // the table, where End turn always sits last.
  if (e.key >= "1" && e.key <= "9") {
    const host = el("#cardmenu") || el("#endturn");
    const btn = host.querySelectorAll("button")[Number(e.key) - 1];
    // Not `btn.click()`: a move that wants to be held is held on the keyboard
    // too, by keeping the number key down.
    Confirm.tap(btn, e.key);
  } else if (e.key === "0") {
    const buttons = el("#endturn").querySelectorAll("button.tray-btn");
    const last = buttons[buttons.length - 1];
    if (last && last.textContent.includes("Decline")) Confirm.tap(last, e.key);
  } else if (e.key === " ") {
    e.preventDefault();
    el("#btn-pause").click();
  } else if (e.key === "ArrowRight") {
    el("#btn-step").click();
  }
});

/* ----------------------------------------------------------------- set-up */
async function loadConfig() {
  state.config = await api("/api/config");
  [0, 1].forEach((seat) => {
    const pilot = el("#pilot" + seat);
    pilot.innerHTML = "";
    state.config.pilots.forEach((p) => {
      const o = h("option", null, prettyPilot(p));
      o.value = p;
      pilot.appendChild(o);
    });
    pilot.value = seat === 0 ? "human" : "heuristic";
    const deck = el("#deck" + seat);
    deck.innerHTML = "";
    state.config.decks.forEach((d) => {
      const o = h("option", null, `${d.name} (${d.size})`);
      o.value = d.name;
      deck.appendChild(o);
    });
    const names = state.config.decks.map((d) => d.name);
    deck.value = seat === 0 ? (names[0] || "") : (names[1] || names[0] || "");
    deck.onchange = () => describeDeck(seat);
    pilot.onchange = updateHint;
    describeDeck(seat);
  });
  updateHint();

  const online = el("#online-deck");
  online.innerHTML = "";
  state.config.decks.forEach((d) => {
    const o = h("option", null, `${d.name} (${d.size})`);
    o.value = d.name;
    online.appendChild(o);
  });
  online.value = (state.config.decks[0] || {}).name || "";
  online.onchange = describeOnlineDeck;
  describeOnlineDeck();
  el("#online-hint").textContent = state.config.public
    ? "Anyone you send the link to can join the room you host, wherever they are."
    : (state.config.lan || []).length
      ? "Someone on this network can join the room you host. Restart with "
        + "--online to play someone further away."
      : "Only this machine can reach this server — restart it with --lan, or "
        + "--online to play someone anywhere.";
  const remembered = localStorage.getItem("braverse.name");
  if (remembered) el("#online-name").value = remembered;
  el("#online-name").onchange = (e) =>
    localStorage.setItem("braverse.name", e.target.value);
}

async function describeOnlineDeck() {
  const info = el("#online-deckinfo");
  const data = await api("/api/deck?name=" + encodeURIComponent(el("#online-deck").value));
  if (data.error) { info.textContent = data.error; return; }
  info.className = "deckinfo" + (data.legal ? "" : " bad");
  // Unlike a local game, an illegal list cannot be taken into a room at all —
  // say so here rather than at the door.
  info.textContent = data.legal
    ? `${data.cards.length} distinct · legal`
    : data.problems.join("; ") + " — not playable online";
}

async function describeDeck(seat) {
  const name = el("#deck" + seat).value;
  const info = el("#deckinfo" + seat);
  info.textContent = "…";
  const data = await api("/api/deck?name=" + encodeURIComponent(name));
  if (data.error) { info.textContent = data.error; return; }
  const cookies = data.cards.filter((c) => c.type === "COOKIE" || c.type === "FLIP")
    .reduce((n, c) => n + c.count, 0);
  info.className = "deckinfo" + (data.legal ? "" : " bad");
  info.textContent = data.legal
    ? `${data.cards.length} distinct · ${cookies} cookies · legal`
    : data.problems.join("; ");
}

function updateHint() {
  const pilots = [el("#pilot0").value, el("#pilot1").value];
  const humans = pilots.filter((p) => p === "human").length;
  el("#setup-hint").textContent = humans === 0
    ? "Both seats are bots — you can watch, pause and step through the match."
    : humans === 2
      ? "Both seats are human: you will be asked for both sides (hands stay visible to you)."
      : "You play one seat; the bot answers for the other.";
}

/* Forget the game that was on screen, so a new one is not drawn over its
 * corpse: the version goes back to "never polled", which makes the first
 * snapshot arrive as a state to adopt rather than a scene to animate. Shared
 * by the New match button and by starting a replay. */
function resetForNewMatch() {
  state.version = -1;
  state.pendingId = null;
  state.eventId = 0;
  state.announced = false;
}

el("#btn-new").onclick = () => el("#setup").showModal();
el("#setup-form").addEventListener("submit", async (e) => {
  if (e.submitter && e.submitter.value === "cancel") return;
  const seed = el("#seed").value;
  const body = {
    decks: [el("#deck0").value, el("#deck1").value],
    pilots: [el("#pilot0").value, el("#pilot1").value],
    seed: seed === "" ? null : Number(seed),
    delay: el("#speed").value / 1000,
    paused: el("#start-paused").checked,
    reveal: el("#pilot0").value !== "human" && el("#pilot1").value !== "human",
  };
  const res = await api("/api/new", body);
  if (res.error) { alert(res.error); return; }
  // What "New match" on the end-of-game card repeats: the same two seats and
  // decks, on a fresh shuffle.
  state.lastMatch = body;
  resetForNewMatch();
  poll();
});

/* ----------------------------------------------------------------- online */
/* One machine runs the server and both people point a browser at it. A room is
 * a code plus a token per seat: the code is public, because it travels in the
 * link, and the token is what the server checks before it lets this browser
 * answer anything. Neither is ever asked to hold a secret — the hand you
 * cannot see was never sent to this page. */

function setMode(online) {
  el("#mode-online").classList.toggle("on", online);
  el("#mode-local").classList.toggle("on", !online);
  el("#online-pane").classList.toggle("hidden", !online);
  el("#local-pane").classList.toggle("hidden", online);
  // "Start" belongs to the local form; a room is entered by its own buttons.
  el("#start").classList.toggle("hidden", online);
}
el("#mode-local").onclick = () => setMode(false);
el("#mode-online").onclick = () => setMode(true);

function onlineError(message) {
  const box = el("#online-error");
  box.textContent = message || "";
  box.classList.toggle("hidden", !message);
}

function joinUrl(code) {
  // Prefer the address the server says can actually be reached from where the
  // person you are inviting is sitting: the public one over a merely local one,
  // and either over http://localhost, which is useless to send to anyone.
  const reachable = state.config.public || (state.config.lan || [])[0];
  const origin = reachable ? reachable.replace(/\/$/, "") : location.origin;
  let url = `${origin}/?room=${code}`;
  // The key makes the link the invitation, rather than the code being a guess
  // anybody could make. Sent over the internet, the link is the whole secret —
  // so a link is a thing to hand to one person, not to post.
  if (state.key) url += `&pass=${encodeURIComponent(state.key)}`;
  return url;
}

/** Take a seat: remember it, point the poll at it, and redraw from its side. */
function takeSeat(code, seat, token, pass) {
  state.room = code;
  state.token = token;
  if (pass) state.key = pass;
  Seat.save(code, seat, token, state.key);
  seatPerspective(seat);
  state.version = -1;
  state.pendingId = null;
  state.eventId = 0;
  state.announced = false;
  document.body.classList.add("online");
  const url = new URL(location.href);
  url.searchParams.set("room", code);
  // Kept in the address bar so a refresh comes back to the same room even in a
  // browser that will not keep localStorage for us.
  if (state.key) url.searchParams.set("pass", state.key);
  history.replaceState(null, "", url);
  el("#setup").close();
  poll();
}

async function hostRoom() {
  onlineError("");
  const res = await api("/api/room/new", {
    deck: el("#online-deck").value,
    name: el("#online-name").value,
  });
  if (res.error) { onlineError(res.error); return; }
  takeSeat(res.room, res.seat, res.token, res.pass);
}

async function joinRoom(code) {
  onlineError("");
  const wanted = (code || el("#online-code").value || "").trim().toUpperCase();
  if (wanted.length !== 4) { onlineError("a room code is four characters"); return; }
  const res = await api("/api/room/join", {
    room: wanted,
    // Set when this browser arrived by an invite link; the server insists on it
    // for a join that did not come from the machine running the game.
    pass: state.key || undefined,
    deck: el("#online-deck").value,
    name: el("#online-name").value,
  });
  if (res.error) { onlineError(res.error); return; }
  takeSeat(res.room, res.seat, res.token, res.pass);
}

el("#stale-reload").onclick = () => location.reload();

el("#btn-host").onclick = hostRoom;
el("#btn-join").onclick = () => joinRoom();
el("#online-code").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); joinRoom(); }
});

function renderLobby(snap) {
  const box = el("#lobby");
  box.classList.remove("hidden");
  el("#lobby-code").textContent = state.room;
  const url = joinUrl(state.room);
  if (el("#lobby-url").value !== url) el("#lobby-url").value = url;
  el("#lobby-hint").textContent = state.config.public
    ? "Send that link to one person: it carries the key to this room."
    : (state.config.lan || []).length
    ? "They need to be on the same network as this machine."
    : "This server is only listening on this machine — restart it with --lan "
      + "for someone else to reach it.";
  el("#turnline").textContent = "waiting for an opponent";
  el("#prompt").textContent = "Room " + state.room;
  el("#prompt-who").textContent = "nobody has joined yet";
  el("#options").innerHTML = "";
}

function hideLobby() { el("#lobby").classList.add("hidden"); }

el("#lobby-copy").onclick = async () => {
  await navigator.clipboard.writeText(el("#lobby-url").value);
  el("#lobby-copy").textContent = "copied";
  setTimeout(() => { el("#lobby-copy").textContent = "copy"; }, 1200);
};
el("#lobby-cancel").onclick = () => leaveRoom();

function renderRoomBar(snap) {
  const bar = el("#roombar");
  bar.classList.remove("hidden");
  el("#room-code").textContent = "room " + state.room;
  const lobby = state.lobby;
  const them = lobby ? lobby.seats[1 - state.mySeat] : null;
  const who = el("#room-who");
  if (state.snap && state.snap.seat === null) {
    who.textContent = "watching";
    who.className = "who";
  } else if (!them || !them.taken) {
    who.textContent = "no opponent";
    who.className = "who away";
  } else {
    who.textContent = (them.name || "opponent") + (them.here ? "" : " — away");
    who.className = "who" + (them.here ? "" : " away");
  }
  el("#btn-rematch").classList.toggle("hidden",
    !(snap && snap.over && state.snap && state.snap.seat !== null));
}

function roomIsGone() {
  // The host stopped the server, or the room was reaped after a long silence.
  if (state.room) Seat.forget(state.room);
  el("#turnline").textContent = "that room is gone";
  onlineError("that room is gone");
  clearRoom();
}

function clearRoom() {
  state.room = null;
  state.token = null;
  state.lobby = null;
  state.snap = null;
  state.version = -1;
  hideLobby();
  document.body.classList.remove("online");
  el("#roombar").classList.add("hidden");
  seatPerspective(0);
  const url = new URL(location.href);
  url.searchParams.delete("room");
  history.replaceState(null, "", url);
}

async function leaveRoom() {
  const room = state.room;
  if (!room) return;
  await api("/api/room/leave", roomAuth());
  Seat.forget(room);
  clearRoom();
  el("#turnline").textContent = "idle";
  if (typeof Title !== "undefined") Title.show(); else el("#setup").showModal();
}
el("#btn-leave").onclick = leaveRoom;
el("#btn-rematch").onclick = async () => {
  const res = await api("/api/room/rematch", roomAuth());
  if (res.error) return;
  state.version = -1;
  state.eventId = 0;
  state.announced = false;
  poll();
};

/** A link with ?room= in it, or a seat this browser held before a refresh. */
async function resumeFromUrl() {
  const query = new URLSearchParams(location.search);
  const code = query.get("room");
  if (!code) return false;
  // Taken before anything is asked of the server: without it a request about
  // this room is answered as though the room did not exist.
  state.key = query.get("pass") || null;
  const held = Seat.load(code.toUpperCase());
  if (held) {
    takeSeat(code.toUpperCase(), held.seat, held.token, held.pass);
    return true;
  }
  // No token for this room: it is someone else's invitation. Open the dialog
  // on the online tab with the code already filled in.
  setMode(true);
  el("#online-code").value = code.toUpperCase();
  el("#setup").showModal();
  return true;
}

loadConfig().then(resumeFromUrl);
setInterval(poll, 350);
poll();
