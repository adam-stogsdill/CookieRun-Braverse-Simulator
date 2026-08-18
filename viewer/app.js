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
  pendingId: null,
  filterUid: null,
  config: { decks: [], pilots: [] },
  busy: false,
  browsing: null,       // {seat, zone} while the trash/break browser is open
  animating: false,     // a scene is playing; the board is a beat behind
  queuedSnap: null,     // newest state, waiting for the scene to finish
  picked: [],           // indices chosen in a multi-card pick
  eventId: 0,           // last event batch played
  announced: false,     // win chime fired for this match
};

/* ------------------------------------------------------------------ utils */
function h(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(path, body) {
  const opts = body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  return res.json();
}

function prettyPilot(name) {
  if (!name) return "";
  if (!name.startsWith("rl:")) return name;
  const tag = name.slice(3).replace(/\.pt$/, "").replace(/^rl_agent_?/, "");
  return tag ? "RL " + tag : "RL";
}

/* ---------------------------------------------------------------- preview */
const preview = el("#preview");

function showPreview(card, event) {
  if (!card) return;
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

function hidePreview() { preview.classList.add("hidden"); }
window.addEventListener("mousemove", (e) => { if (preview.dataset.follow) movePreview(e); });

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
  node.addEventListener("mouseenter", (e) => { preview.dataset.follow = "1"; showPreview(card, e); });
  node.addEventListener("mouseleave", () => { delete preview.dataset.follow; hidePreview(); });
  if (opts.uid !== undefined && opts.uid !== null) {
    if (opts.seat === 0) {
      makeDraggable(node, opts.uid, opts.seat);
    } else {
      node.addEventListener("click", () => toggleFilter(opts.uid));
    }
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
  box.appendChild(front);
  box.dataset.cookie = cookie.uid;
  slot.appendChild(box);

  const bar = h("div", "hpbar");
  const max = Math.max(cookie.maxHp || cookie.hp, cookie.hp);
  for (let i = 0; i < max; i++) bar.appendChild(h("i", i < cookie.hp ? "on" : ""));
  slot.appendChild(bar);
  slot.appendChild(h("div", "hplabel", `${cookie.hp}/${max} HP${cookie.rested ? " · rested" : ""}`));
  return slot;
}

function zone(cls, label) {
  const node = h("div", "zone " + cls);
  node.appendChild(h("span", "zlabel", label));
  return node;
}

function stack(label, count, opts = {}) {
  const node = h("div", "stack" + (opts.onClick ? " clickable" : "") + (opts.danger ? " danger" : ""));
  const art = h("div", "stackart");
  if (count > 0) {
    for (let i = Math.min(count, 3) - 1; i >= 0; i--) {
      const back = opts.faceUp && i === 0 && opts.top ? cardNode(opts.top, { small: true }) : faceDown();
      // Offsets go through custom properties, not `transform`, so the stylesheet
      // can still turn the card around on a flipped opponent mat.
      back.style.setProperty("--ox", i * 2 + "px");
      back.style.setProperty("--oy", -i * 2 + "px");
      art.appendChild(back);
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

function renderSide(seat, snap) {
  const side = el("#side-" + seat);
  side.innerHTML = "";
  const p = snap.players[seat];
  const isHuman = (snap.humanSeats || []).includes(seat);
  const opponent = seat !== 0;

  /* -- seat bar ------------------------------------------------------- */
  const bar = h("div", "seatbar");
  bar.appendChild(h("span", "who", "Seat " + seat));
  bar.appendChild(h("span", "pill" + (isHuman ? " human" : ""), prettyPilot(snap.pilots[seat])));
  bar.appendChild(h("span", "deckname", snap.decks[seat]));

  /* -- mat ------------------------------------------------------------ */
  const mat = h("div", "mat" + (snap.turnPlayer === seat && !snap.over ? " active" : ""));

  const left = h("div", "matcol left");
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
  right.appendChild(stack("deck", p.deckCount, { title: "Face-down deck" }));
  right.appendChild(stack("trash", p.trashCount, {
    faceUp: true,
    top: p.trash[p.trash.length - 1],
    title: "Click to search the trash",
    onClick: p.trashCount ? () => openBrowser(seat, "trash") : null,
  }));

  mat.appendChild(left);
  mat.appendChild(battle);
  mat.appendChild(support);
  mat.appendChild(right);

  /* -- hand ----------------------------------------------------------- */
  const handRow = h("div", "handrow");
  handRow.appendChild(h("span", "tag", `hand ${p.handCount}`));
  const hand = h("div", "hand");
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
      : `Seat ${snap.winner} (${prettyPilot(snap.pilots[snap.winner])}) wins — ${snap.winReason}`;
    return;
  }
  if (snap.pending) {
    banner.classList.add("on");
    banner.textContent = `Seat ${snap.pending.seat}: ${snap.pending.prompt}`;
    return;
  }
  banner.textContent = snap.paused ? "paused" : "…";
}

function renderTurnline(snap) {
  if (!snap.players) { el("#turnline").textContent = "no match yet — hit New match"; return; }
  el("#turnline").innerHTML =
    `turn <b>${snap.turn}</b> · <b>seat ${snap.turnPlayer}</b> (${prettyPilot(snap.pilots[snap.turnPlayer])})` +
    ` · phase <b>${snap.phase}</b> · seed ${snap.seed}`;
}

/* ---------------------------------------------------------- the play-out */
/* One action can be a whole little scene: the attacker swings, damage turns HP
 * cards face up, a Cookie breaks. The server sends them already ordered, and
 * this plays them as a sequence rather than all at once. */
const ATTACK_MS = 900;
const REVEAL_MS = 700;    // between one HP card turning over and the next
const FLIP_MS = 2400;     // how long a revealed card stays on screen
const FAINT_MS = 300;
const SKILL_MS = 400;     // between one skill popping up and the next
const SKILL_HOLD = 1500;  // how long the confirmation stays on screen
const BREAK_MS = 1500;    // how long a breaking Cookie stays on screen

/** Play one action's scene and return how long it runs, in ms. */
function playEvents(events) {
  if (!events || !events.length) return 0;
  const skills = events.filter((e) => e.type === "skill");
  const attacks = events.filter((e) => e.type === "attack");
  const reveals = events.filter((e) => e.type === "reveal").slice(0, 6);
  const faints = events.filter((e) => e.type === "faint");

  let clock = 0;
  skills.forEach((event, i) => playSkill(event, i * SKILL_MS));
  if (skills.length) clock += (skills.length - 1) * SKILL_MS + SKILL_HOLD;
  attacks.forEach((event) => { playAttack(event, clock); clock += ATTACK_MS; });
  if (reveals.length) {
    playReveals(reveals, clock);
    // Wait for the last card to *leave*, not just to start turning: the board
    // must not change under a reveal that is still being read.
    clock += REVEAL_MS * (reveals.length - 1) + FLIP_MS;
  }
  faints.forEach((event, i) => playFaint(event, clock + i * FAINT_MS));
  return clock + (faints.length ? (faints.length - 1) * FAINT_MS + BREAK_MS : 0);
}

/* An HP card turning face up is the swing moment of a battle — a FLIP can
 * bounce its own host mid-attack — so it gets a real beat rather than a number
 * quietly ticking down. */
function playReveals(events, delay = 0) {
  if (!events || !events.length) return;
  // Several cards can come off the same Cookie in one attack; fan them out so a
  // three-damage hit reads as three cards rather than one.
  const perCookie = new Map();
  events.slice(0, 6).forEach((event, i) => {
    const rank = perCookie.get(event.cookie) || 0;
    perCookie.set(event.cookie, rank + 1);
    setTimeout(() => {
      flipCard(event, rank, i);
      Sfx.play(event.flip ? "flipBig" : "flip");
    }, delay + i * 700);
  });
  renderReveals(events);
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

    // The defender takes the hit as the lunge lands.
    setTimeout(() => {
      const hit = document.querySelector(`[data-cookie="${event.target}"]`);
      if (!hit) return;
      hit.classList.add("struck");
      setTimeout(() => hit.classList.remove("struck"), 420);
    }, ATTACK_MS * 0.3);

    // Remove it when the animation actually ends. A background tab throttles
    // timers but not the compositor, so a timer alone can leave a card floating
    // over the board; the timer stays as a backstop, and `sweepFx` catches
    // anything that still slips through.
    flight.finished.then(() => ghost.remove()).catch(() => ghost.remove());
    setTimeout(() => ghost.remove(), ATTACK_MS + 400);
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

    const node = h("div", "skillpop");
    node.style.left = at.left + at.width / 2 - bounds.left - 46 + "px";
    node.style.top = at.top + at.height / 2 - bounds.top - 64 + "px";
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

    const node = h("div", "breaker");
    node.style.left = at.left + at.width / 2 - bounds.left - 46 + "px";
    node.style.top = at.top + at.height / 2 - bounds.top - 64 + "px";
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
    item.appendChild(cardNode(event.card, { small: true }));
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
  node.style.width = "76px";
  node.style.height = "106px";
  const left = at ? (host ? at.left : at.left + at.width / 2 - 38) - bounds.left - 4
                  : bounds.width / 2 - 38;
  const top = at ? (host ? at.top : at.top + at.height / 2 - 53) - bounds.top - 4
                 : bounds.height / 2 - 53;
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
function hostPreview(node) {
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
  const cards = zoneName === "trash" ? player.trash : player.break;
  const query = el("#browser-search").value.trim().toLowerCase();

  el("#browser-title").textContent =
    (zoneName === "trash" ? "Trash" : "Break area") + ` · seat ${seat}`;

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
  hostPreview(document.body);
});

/* ---------------------------------------------------------------- options */
/* Turn actions arrive in engine order, which buries the interesting moves
 * under a wall of "place this card as support". Group them the way a player
 * thinks about a turn instead. */
const GROUPS = [
  ["Attack", "Attack"],
  ["PlayCookie", "Play a Cookie"],
  ["PlaySupportCard", "Items & Stages"],
  ["ActivateSkill", "Skills"],
  ["PlayTrap", "Traps"],
  ["Block", "Block"],
  ["PlaceSupport", "Place as support"],
  ["Pass", ""],
  ["EndTurn", ""],
];

/* The opening toss reads better as a hand than as a word. */
const THROW_ICONS = { rock: "\u270a", paper: "\u270b", scissors: "\u270c\ufe0f" };

function optionLabelClass(opt) {
  if (opt.kind === "EndTurn" || opt.kind === "Pass") return "opt end";
  if (opt.kind === "Attack") return "opt attack";
  if (opt.kind === "PlaceSupport") return "opt support";
  return "opt";
}

function groupOptions(options) {
  const order = new Map(GROUPS.map(([kind], i) => [kind, i]));
  const groups = [];
  const byKind = new Map();
  options.forEach((opt) => {
    const kind = opt.kind || "";
    if (!byKind.has(kind)) {
      const entry = { kind, title: (GROUPS.find(([k]) => k === kind) || [, ""])[1], items: [] };
      byKind.set(kind, entry);
      groups.push(entry);
    }
    byKind.get(kind).items.push(opt);
  });
  groups.sort((a, b) => (order.has(a.kind) ? order.get(a.kind) : 50) -
                        (order.has(b.kind) ? order.get(b.kind) : 50));
  return groups;
}

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
  head.appendChild(h("span", "picker-count", `${state.picked.length} / ${count}`));
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
      else if (count === 1) state.picked = [opt.index];
      else if (state.picked.length < count) state.picked.push(opt.index);
      renderPicker(snap);
    };
    row.appendChild(wrap);
  });
  bar.appendChild(row);

  const foot = h("div", "picker-foot");
  const verb = pick.verb || "Choose";
  const confirm = h("button", "primary",
    count > 1 ? `${verb} ${state.picked.length || ""}`.trim() : verb);
  confirm.disabled = state.picked.length !== count;
  confirm.onclick = () => {
    const picks = state.picked.slice();
    state.picked = [];
    bar.classList.add("hidden");
    // A single pick is still a single answer; only a batch sends a list.
    answer(count > 1 ? picks : picks[0]);
  };
  foot.appendChild(h("span", "hint", state.picked.length === count
    ? "ready"
    : `choose ${count - state.picked.length} more`));
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
  const row = h("div", "centre-row" + (style === "throw" ? " throws" : " choices"));
  pending.options.forEach((opt) => {
    const btn = h("button", "centre-btn");
    const icon = THROW_ICONS[opt.label];
    if (icon) btn.appendChild(h("span", "big", icon));
    btn.appendChild(h("span", "label", opt.label));
    btn.onclick = () => answer(opt.index);
    row.appendChild(btn);
  });
  bar.appendChild(row);
  return true;
}

function renderOptions(snap) {
  const box = el("#options");
  box.innerHTML = "";
  box.scrollTop = 0;
  const pending = snap.pending;
  const lastLine = (snap.log || []).slice(-1)[0] || "";
  el("#prompt").textContent = pending ? pending.prompt : (snap.over ? "Game over" : "Bots playing…");
  el("#prompt-who").textContent = pending
    ? `seat ${pending.seat} · ${prettyPilot(snap.pilots ? snap.pilots[pending.seat] : "")}`
    : lastLine;

  if (!pending) { state.filterUid = null; return; }
  if (pending.centre) {
    box.appendChild(h("div", "filterbar", "answer in the middle of the table"));
    return;
  }
  if (pending.pick) {
    box.appendChild(h("div", "filterbar",
      pending.count > 1 ? "pick the cards on your hand below"
                        : "pick the card on your hand below"));
    return;
  }

  if (state.filterUid !== null) {
    const bar = h("div", "filterbar");
    bar.appendChild(h("span", null, "filtered to the card you clicked"));
    const clear = h("button", "ghost tiny", "clear");
    clear.onclick = () => { state.filterUid = null; renderOptions(snap); highlight(null); };
    bar.appendChild(clear);
    box.appendChild(bar);
  }

  const options = pending.options.filter((o) =>
    state.filterUid === null || (o.uids || []).includes(state.filterUid) || o.subject === state.filterUid);
  const shown = options.length ? options : pending.options;

  let n = 0;
  groupOptions(shown).forEach((group) => {
    if (group.title && group.items.length) box.appendChild(h("div", "optgroup", group.title));
    group.items.forEach((opt) => {
      const btn = h("button", optionLabelClass(opt));
      n += 1;
      btn.appendChild(h("span", "k", n <= 9 ? String(n) : "·"));
      if (opt.img) {
        const img = h("img");
        img.src = opt.img;
        img.onerror = () => img.remove();
        btn.appendChild(img);
      }
      const icon = THROW_ICONS[opt.label];
      if (icon) btn.appendChild(h("span", "throw", icon));
      // Name the move the way the card does — "Tracker's Arrow", not "Attack".
      if (opt.skill && (opt.kind === "Attack" || opt.kind === "ActivateSkill")) {
        btn.appendChild(h("span", "skilltag", opt.skill));
      }
      btn.appendChild(h("span", null, opt.label));
      btn.onclick = () => answer(opt.index);
      btn.onmouseenter = () => highlight(opt);
      btn.onmouseleave = () => highlight(null);
      box.appendChild(btn);
    });
  });

  if (pending.optional) {
    const btn = h("button", "opt end");
    btn.appendChild(h("span", "k", "0"));
    btn.appendChild(h("span", null, "Decline"));
    btn.onclick = () => answer(null);
    box.appendChild(btn);
  }
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

function toggleFilter(uid) {
  const snap = state.snap;
  if (!snap || !snap.pending || state.animating) return;
  // Mid-effect questions ("Damage which Cookie?") name a card, so clicking that
  // card on the board *is* the answer rather than a filter.
  const direct = snap.pending.options.find(
    (o) => (o.kind === "cookie" || o.kind === "card") && o.subject === uid);
  if (direct) { answer(direct.index); return; }
  const node = document.querySelector(`.card[data-uid="${uid}"]`);
  if (node && movesFor(uid).length) { openCardMenu(uid, node); return; }
  state.filterUid = state.filterUid === uid ? null : uid;
  renderOptions(snap);
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

  moves.forEach((opt) => {
    const row = h("button", "cardmenu-row");
    row.appendChild(h("span", "skill", opt.skill || "Play"));
    // The engine's own description carries the target and the numbers.
    const detail = opt.label.replace(/^[^:]*:\s*/, "").replace(/^Play\s+/, "");
    if (detail && detail !== opt.skill) row.appendChild(h("span", "detail", detail));
    row.onmouseenter = () => highlight(opt);
    row.onmouseleave = () => highlight(null);
    row.onclick = (event) => {
      event.stopPropagation();
      closeCardMenu();
      answer(opt.index);
    };
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

/** Outline the cards a mid-effect question is asking you to pick between. */
function markChoosable(snap) {
  document.querySelectorAll(".choosable").forEach((n) => n.classList.remove("choosable"));
  const pending = snap.pending;
  if (!pending) return;
  pending.options.forEach((opt) => {
    if (opt.kind !== "cookie" && opt.kind !== "card") return;
    document.querySelectorAll(`.card[data-uid="${opt.subject}"]`)
      .forEach((n) => n.classList.add("choosable"));
  });
}

/* --------------------------------------------------------- drag and drop */
/* Dragging is a second way to name an option that already exists, never a new
 * kind of move: a drag resolves to (subject, target) and then looks for the one
 * legal action that matches. Anything ambiguous or unmatched falls back to the
 * list on the right, so the drag layer can never invent an illegal play. */

function pendingOptions() {
  const snap = state.snap;
  return snap && snap.pending && !state.animating ? snap.pending.options : [];
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
  if (drop) {
    Sfx.play("place");
    answer(drop.opt.index);
  } else {
    // A drag that goes nowhere is a click: ask the card what it can do.
    const node = document.querySelector(`.card[data-uid="${uid}"]`);
    if (node) openCardMenu(uid, node); else toggleFilter(uid);
  }
}

/** Make a card draggable when it has a legal move behind it. */
function makeDraggable(node, uid, seat) {
  if (seat !== 0 || !(state.snap && (state.snap.humanSeats || []).includes(0))) return;
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
  state.busy = true;
  state.filterUid = null;
  el("#options").innerHTML = "";
  try {
    await api("/api/choose", { index });
  } finally {
    state.busy = false;
  }
  poll();
}

/* -------------------------------------------------------------------- log */
function renderLog(snap) {
  const box = el("#log");
  const lines = snap.log || [];
  box.innerHTML = "";
  lines.forEach((line, i) => box.appendChild(h("li", i >= lines.length - 3 ? "new" : "", line)));
  box.scrollTop = box.scrollHeight;
}

/* ------------------------------------------------------------------- poll */
function render(snap) {
  state.snap = snap;
  if (!snap.players) {
    renderTurnline(snap);
    renderOptions(snap);
    renderBanner(snap);
    return;
  }
  renderSide(1, snap);
  renderSide(0, snap);
  renderTurnline(snap);
  renderBanner(snap);
  renderOptions(snap);
  renderLog(snap);
  renderCentre(snap);
  renderPicker(snap);
  markChoosable(snap);
  if (state.browsing) renderBrowser();
  if (snap.over && !state.announced) {
    state.announced = true;
    Sfx.play("win");
  }
  el("#btn-pause").textContent = snap.paused ? "Resume" : "Pause";
  el("#reveal").checked = !!snap.reveal;
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
    const snap = await api("/api/state");
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

document.addEventListener("keydown", (e) => {
  if (el("#setup").open || el("#browser").open) return;
  if (e.key >= "1" && e.key <= "9") {
    const btn = el("#options").querySelectorAll("button.opt")[Number(e.key) - 1];
    if (btn) btn.click();
  } else if (e.key === "0") {
    const buttons = el("#options").querySelectorAll("button.opt");
    const last = buttons[buttons.length - 1];
    if (last && last.textContent.includes("Decline")) last.click();
  } else if (e.key === "Escape") {
    state.filterUid = null;
    if (state.snap) renderOptions(state.snap);
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
  state.version = -1;
  state.pendingId = null;
  state.eventId = 0;
  state.announced = false;
  poll();
});

loadConfig();
setInterval(poll, 350);
poll();
