/* A guided first match.
 *
 * The tutorial is not a scripted board. It starts a real game — same engine,
 * same rules, same seat 0 the setup dialog would give you — and then watches
 * the snapshot the rest of the viewer is already polling. Every step names a
 * moment ("it is your main phase and you have not placed a support yet") and
 * advances when the game reaches the next one, so a player who ignores the
 * instruction and does something else is never stuck against a script that
 * expected otherwise: they are stuck against the game, which is the thing they
 * came to learn.
 *
 * What the game *is* comes from `braverse/tutorial.py`, through the one flag
 * this file sends: both decks are dealt stacked instead of shuffled, and the
 * other seat is a scripted opponent that pulls its punches. That is not
 * decoration. "Play a Cookie into the empty slot" is a lie if the shuffle dealt
 * no second Cookie, and a bot that opens on a three-damage attack takes the
 * board away while the reader is still on step six. `tests/test_tutorial.py`
 * plays this course's own path against a real game and asserts every moment
 * below actually arrives, in order.
 *
 * Nothing here can make a move. The coach dims the board around one zone and
 * says a sentence about it; the moves still come off the cards, through the
 * same handlers as always. It also never blocks a click — the veil is
 * `pointer-events: none` all the way through, because the card menu is parked
 * on <body> outside whatever rectangle is lit up, and a tutorial that ate the
 * click it just asked for would be worse than no tutorial. */

const Tut = {
  on: false,
  i: 0,
  seen: false,          // the current step's moment has arrived at least once
  tip: null,            // a contextual aside, showing over the current step
  lit: null,            // the selector the hole is following
  fired: new Set(),     // tips already given
  attacked: false,      // this browser has answered an Attack
  played: false,        // ...and a Cookie out of hand
  waitFrom: null,       // the turn this step started waiting on
  frame: 0,
  lastKey: "",
};

const KEY = "braverse.tutorial.done";

/* --------------------------------------------------------- board reading */
/* One place that knows how to ask the snapshot a question, so a step is a
 * sentence and a condition rather than a paragraph of indexing. */
const T = {
  mine: (s) => s.players[state.mySeat],
  theirs: (s) => s.players[1 - state.mySeat],
  myTurn: (s) => s.turnPlayer === state.mySeat && !s.over,
  /* A question addressed to this seat. `waiting` is the projection the *other*
   * seat gets, and it carries no options at all. */
  asked: (s) => !!(s.pending && s.pending.seat === state.mySeat && !s.pending.waiting),
  opts: (s) => (T.asked(s) && s.pending.options) || [],
  has: (s, kind) => T.opts(s).some((o) => o.kind === kind),
  /* The main-phase action list, told apart from every mid-effect question by
   * the one move only it ever carries. */
  myMove: (s) => T.asked(s) && T.has(s, "EndTurn"),
  prompt: (s) => (s.pending && s.pending.prompt) || "",
  centre: (s) => (T.asked(s) && s.pending.centre) || null,
};

/** A step that lives exactly as long as one question is on screen.
 *
 * `past` is what makes it safe to hang a step off a question that may never be
 * asked. Losing the toss means the "you won the toss" step's moment simply
 * never comes, and without a way to recognise that the whole course would sit
 * behind it explaining a choice the bot is making. So a question-shaped step
 * carries the evidence that its moment has been and gone. */
function whileAsked(test, past) {
  const here = (s) => T.asked(s) && test(s);
  return {
    ready: here,
    /* Done when a *different* question is up, or when the evidence says the
     * moment has passed — not merely when this one blinks out. Two things make
     * the question come and go without being answered: a tied toss throws
     * again, and the snapshot between one question and the next carries no
     * pending at all. Advancing on either put the coach on "you won the toss"
     * while the board was still asking for a throw. */
    done: (s) => !here(s) && (T.asked(s) || (!!past && past(s))),
    skip: (s) => !here(s) && !!past && past(s),
  };
}

/** The toss has been settled — by us or, having lost it, for us. */
const tossOver = (s) => (s.log || []).some((line) => /goes first/i.test(line));

/* Misclick protection changes the verb this tutorial has to teach: with it on,
 * a move that cannot be taken back is held rather than clicked. Asked at the
 * moment a step is drawn — the setting is a dropdown in the header and can
 * change between one game and the next. */
const held = (opt) => typeof Confirm !== "undefined" && Confirm.needsHold(opt);

/* ------------------------------------------------------------- the course */
/* Order is the order of a real first game: the toss, the hand, a tour of the
 * mat while nothing is waiting on you, then one instruction per new verb. */
const STEPS = [
  {
    id: "welcome",
    title: "A guided first game",
    body: "You are Player 1 in a real match against the bot — nothing here is a "
        + "mock-up. I will stop you at each new idea and get out of the way in "
        + "between. Close this at any point with <b>End tutorial</b>.",
    next: "Deal me in",
  },
  {
    id: "toss",
    anchor: "#centre",
    title: "Rock, paper, scissors",
    body: "Turn order is a toss, and the winner chooses. Going first is worth "
        + "having, but it is a trade: the opener <b>skips their first draw</b> "
        + "and <b>cannot attack on turn 1</b>. Throw something.",
    ...whileAsked((s) => T.centre(s) === "throw", tossOver),
  },
  {
    id: "toss-pick",
    anchor: "#centre",
    title: "You won the toss",
    body: "Take the first turn to set up a Cookie before they can swing, or "
        + "hand it over to keep your draw and the turn-2 attack. Either answer "
        + "is a fine way to start.",
    ...whileAsked((s) => T.centre(s) === "choice" && /toss/i.test(T.prompt(s)),
                   tossOver),
  },
  {
    id: "mulligan",
    anchor: ".side.me .hand",
    title: "Your opening six",
    body: "You need a <b>Cookie card</b> — without one on the mat you have "
        + "nothing to attack or defend with, and a game with no Cookie "
        + "anywhere is a game you have already lost. This redraw is free. If "
        + "you can see a Cookie, keep.",
    // Past it once a turn is actually being played: the opening hand is
    // settled before anyone has a main phase.
    ...whileAsked((s) => /mulligan|no cookie in hand/i.test(T.prompt(s)),
                  (s) => T.myMove(s) || T.mine(s).battle.length > 0),
  },
  {
    id: "opening",
    anchor: ".side.me .hand",
    title: "Your opening Cookie",
    body: () => "Before turn 1 both players put <b>one Cookie</b> out, face "
        + "down, and then turn it over. " + (held({ kind: "card" })
          ? "<b>Press and hold</b> one of the raised cards in your hand — a "
            + "move you cannot take back is held rather than clicked, so a "
            + "stray click costs you nothing. "
          : "Click one of the raised cards in your hand. ")
        + "A higher <b>Level</b> usually means more HP — and more Level banked "
        + "for your opponent when it eventually faints. Setup does not fire "
        + "【On Play】 effects, so a Cookie played for its arrival is better "
        + "kept for later.",
    ...whileAsked((s) => /^opening cookie/i.test(T.prompt(s)),
                  (s) => T.mine(s).battle.length > 0 || T.myMove(s)),
    hint: () => held({ kind: "card" }) ? "hold a Cookie in your hand"
                                       : "click a Cookie in your hand",
  },
  {
    id: "goal",
    anchor: ".side.opponent .matcol.left .stack",
    title: "How you win",
    body: "That is your opponent's <b>break area</b>. Every Cookie of theirs "
        + "that faints lands there and banks its Level. Reach <b>10 Level</b> "
        + "in it and you win — and the same pile on your own side is how you "
        + "lose, so read both.",
    ready: (s) => T.myMove(s),
    wait: "Your opponent is taking their turn — this carries on when it "
        + "comes back to you.",
    next: "Got it",
  },
  {
    id: "battle",
    anchor: ".side.me .zone.battle",
    title: "The battle area",
    body: "Room for <b>two Cookies</b>. They attack, they block, they take the "
        + "damage. One of yours is already standing there from setup; the "
        + "other slot is worth filling, because Cookies cost <b>nothing</b> to "
        + "play from hand — the two slots are the only limit.",
    ready: (s) => T.myMove(s),
    next: "Next",
  },
  {
    id: "support",
    anchor: ".side.me .zone.support",
    title: "The support area is your energy",
    body: "Once per turn you may lay <b>one card from hand face up</b> here. "
        + "Its colour is then energy: paying a <b>{G}{G}</b> cost turns two "
        + "matching cards sideways (<b>rests</b> them). Everything untaps at "
        + "the start of your turn, so a support is a card you keep spending.",
    ready: (s) => T.myMove(s),
    next: "Next",
  },
  {
    id: "phases",
    anchor: ".side.me .phases",
    title: "The shape of a turn",
    body: "Untap, draw <b>2</b>, place <b>1</b> support, then do as much as you "
        + "can pay for: play Cookies and Items, activate skills, attack. Your "
        + "turn stops on <b>Support</b> first — place a card, or <b>Pass to "
        + "main phase</b> — so the free support never goes forgotten. This "
        + "track tells you which of those you still have in hand this turn.",
    ready: (s) => T.myMove(s),
    next: "Next",
  },
  {
    id: "hand",
    anchor: ".side.me .hand",
    title: "Moves live on the cards",
    body: "There is no list of moves to hunt through. <b>Click a card</b> and "
        + "it offers what it can do right now; drag it onto a zone if you "
        + "prefer. Anything greyed out is a move the game will not let you "
        + "make yet.",
    ready: (s) => T.myMove(s),
    next: "Let me try",
  },
  {
    id: "do-support",
    anchor: ".side.me .hand",
    title: "Place your first support",
    body: "Click any card in hand and choose <b>Place as support</b>. Pick a "
        + "colour you will actually need — a card spent here is a card you are "
        + "not playing, and that is the real cost of every effect in the game.",
    ready: (s) => T.myMove(s) && !T.mine(s).supportedThisTurn,
    skip: (s) => T.mine(s).supportedThisTurn,
    done: (s) => T.mine(s).supportedThisTurn,
    hint: "waiting for the support",
  },
  {
    id: "hp",
    anchor: ".side.me .zone.battle",
    title: "HP is a stack of cards",
    body: "The backs behind your Cookie are its HP, one card per point, dealt "
        + "off your deck when it arrived. <b>Each point of damage turns one of "
        + "them face up</b>; when the last one goes the Cookie faints, its "
        + "cards go to the trash and it banks its Level in your break area. "
        + "Healing hands the cards back.",
    ready: (s) => T.myTurn(s) && T.mine(s).battle.length > 0,
    next: "Next",
  },
  {
    id: "do-cookie",
    anchor: ".side.me .hand",
    title: "Fill the second slot",
    body: "Setup gave you one Cookie. Click a Cookie card in hand and play it "
        + "into the empty slot — Cookies are <b>free</b>, they arrive ready to "
        + "attack, and a second body is both a second attacker and the thing "
        + "that keeps you in the game when the first one faints.",
    ready: (s) => T.myMove(s) && T.has(s, "PlayCookie") && T.mine(s).battle.length < 2,
    skip: (s) => T.mine(s).battle.length >= 2,
    done: () => Tut.played,
    // Not every turn has a Cookie to spare. Wait a couple of turns for one and
    // then move on rather than holding the course against the shuffle.
    patience: 2,
    wait: "Waiting for a Cookie you can play.",
    hint: "waiting for the Cookie",
  },
  {
    id: "end-turn",
    anchor: "#endturn",
    title: "Pass the turn",
    body: (s) => "Spend what is worth spending, then "
        + (held({ kind: "EndTurn" }) ? "<b>hold End turn</b>" : "<b>End turn</b>")
        + " in the middle of the table."
        + (s.turn === 1 ? " Turn 1 has no attack in it for the opener — that "
           + "is the price of going first." : ""),
    ready: (s) => T.myMove(s),
    done: (s) => !T.myTurn(s) || s.over,
    hint: () => held({ kind: "EndTurn" }) ? "hold End turn when you are ready"
                                          : "end your turn when you are ready",
  },
  {
    id: "their-turn",
    anchor: ".side.opponent .mat",
    title: "Their turn — and yours to answer",
    body: "Watch the log on the right; it says what hit what and for how much. "
        + "You are not idle: when they attack you may be offered a <b>block</b> "
        + "or a <b>trap</b> from your hand — one or the other, never both. I "
        + "will speak up if either comes round.",
    ready: (s) => !T.myTurn(s) && !s.over,
    done: (s) => T.myTurn(s) || s.over,
    hint: "the bot is thinking",
  },
  {
    id: "attack",
    anchor: ".side.me .zone.battle",
    title: "Swing",
    body: () => "Click your Cookie, then "
        + (held({ kind: "Attack" }) ? "<b>hold</b> its attack" : "pick its attack")
        + ". The cost in "
        + "<b>&lt;angle brackets&gt;</b> is optional — the game will ask — and "
        + "the rest is paid by resting supports for you. If they have a "
        + "<b>Blocker</b>, expect the hit to be taken by it instead.",
    ready: (s) => T.myMove(s) && T.has(s, "Attack"),
    skip: () => Tut.attacked,
    done: () => Tut.attacked,
    patience: 3,
    /* "Waiting…" is the wrong thing to say here, because the thing being
     * waited for is usually the player's own next move. An attack has a cost,
     * turn 1 spent one card on support, and one support does not pay for
     * anything with two symbols on it — so the honest wait text is the
     * instruction: place another one. */
    wait: (s) => (T.mine(s).supportActive < 2
      ? "An attack has a cost, and your Cookie's is more than one support can "
        + "pay. <b>Place another card as support</b> — you may place one every "
        + "turn — and the swing will be here."
      : "Waiting for a turn where you can attack."),
    hint: "waiting for the swing",
  },
  {
    id: "aftermath",
    anchor: ".side.opponent .matcol.left .stack",
    title: "That is the whole loop",
    body: "Damage turns their HP cards over; the last one faints the Cookie "
        + "and drops its Level into that pile. Build support, hold two Cookies "
        + "on the mat, and keep the pile climbing to 10. Two smaller things "
        + "worth knowing: running your <b>deck</b> out does not lose the game "
        + "(you bank one Level and shuffle your trash back), and being left "
        + "with <b>no Cookie in play and none in hand</b> does.",
    ready: () => true,
    next: "Finish",
  },
];

/* Asides that fire the first time the game asks something a new player has not
 * met. They interrupt whatever step is up, and hand it back when the question
 * is answered — the step's own condition is untouched, so an aside can never
 * cost you your place. */
const TIPS = [
  {
    id: "block",
    when: (s) => T.has(s, "Block"),
    anchor: ".side.me .zone.battle",
    title: "You can block this",
    // The price is printed per card and it is not one thing: most 【Blocker】s
    // are energy — Mystic Opal Cookie, the one the tutorial deals you, blocks
    // for {B} — and five in the pool rest themselves instead. Saying "it rests
    // the Cookie" would be wrong about the very card this fires on.
    body: () => "A <b>Blocker</b> can step in front of the attack and take it "
        + "instead. It is never free: the price is printed on the Cookie — "
        + "usually energy, so a support rests to pay for it, and on a few "
        + "Cookies the <b>Blocker itself</b> rests and is not attacking next "
        + "turn. Blocking also uses up your one response for this attack: no "
        + "trap after it."
        + (held({ kind: "Block" }) ? " Hold it to commit." : ""),
  },
  {
    id: "trap",
    when: (s) => T.has(s, "PlayTrap"),
    anchor: ".side.me .hand",
    title: "A trap window",
    body: "Traps are played from your hand in the middle of an attack, and you "
        + "get <b>one response per attack</b> — this or a block. Springing it "
        + "now can change what the attack does; declining keeps the card.",
  },
  {
    id: "pick",
    when: (s) => T.asked(s) && !!s.pending.pick,
    anchor: "#picker",
    title: "Pick from the strip",
    body: "An effect is asking you to choose cards. Click to select, then "
        + "confirm at the end of the row. Dimmed cards are ones you are allowed "
        + "to <i>see</i> but not to take.",
  },
  {
    id: "yesno",
    when: (s) => T.centre(s) === "yesno",
    anchor: "#centre",
    title: "An optional cost",
    body: "Costs printed in <b>&lt;angle brackets&gt;</b> are a decision, never "
        + "an automatic charge. Pay it for the bigger effect, or decline and "
        + "take what the card does for free.",
  },
];

/* Energy in the coach's prose is the same gem it is everywhere else. The step
 * bodies carry markup, so they cannot go through `setText`; this walks what is
 * already on the page and swaps the `{G}` tokens for pips in place. */
function pipify(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const hits = [];
  while (walker.nextNode()) {
    if (ENERGY_TOKEN.test(walker.currentNode.nodeValue)) hits.push(walker.currentNode);
  }
  hits.forEach((node) => {
    const frag = document.createDocumentFragment();
    setText(frag, node.nodeValue);
    node.parentNode.replaceChild(frag, node);
  });
}

/* --------------------------------------------------------------- the card */
function tutEl(id) { return document.getElementById(id); }

function ensureChrome() {
  if (tutEl("tut-veil")) return;
  const veil = document.createElement("div");
  veil.id = "tut-veil";
  veil.className = "tut-veil hidden";
  ["top", "right", "bottom", "left"].forEach((side) => {
    const panel = document.createElement("div");
    panel.className = "tut-panel tut-" + side;
    veil.appendChild(panel);
  });
  const ring = document.createElement("div");
  ring.className = "tut-ring";
  veil.appendChild(ring);
  document.body.appendChild(veil);

  const card = document.createElement("div");
  card.id = "tut-card";
  card.className = "tut-card hidden";
  card.innerHTML = `
    <div class="tut-head">
      <span class="tut-step" id="tut-step"></span>
      <h3 id="tut-title"></h3>
    </div>
    <div class="tut-body" id="tut-body"></div>
    <div class="tut-foot">
      <button class="ghost tiny" id="tut-quit">End tutorial</button>
      <span class="tut-hint" id="tut-hint"></span>
      <button class="primary tiny hidden" id="tut-next"></button>
      <button class="ghost tiny hidden" id="tut-skip">Skip this</button>
    </div>`;
  document.body.appendChild(card);
  tutEl("tut-quit").onclick = () => Tut.finish(true);
  tutEl("tut-next").onclick = () => { Tut.advance(); Tut.sync(state.snap); };
  tutEl("tut-skip").onclick = () => { Tut.advance(); Tut.sync(state.snap); };
}

/** Take the dimming off the board entirely, leaving the card. */
function hideVeil() {
  const veil = tutEl("tut-veil");
  if (veil) veil.classList.add("hidden");
}

/** Cut a hole in the veil over `rect`, or dim everything when there is none. */
function spotlight(rect) {
  const veil = tutEl("tut-veil");
  veil.classList.remove("hidden");
  const W = window.innerWidth;
  const H = window.innerHeight;
  const pad = 8;
  const box = rect
    ? { top: Math.max(0, rect.top - pad), left: Math.max(0, rect.left - pad),
        right: Math.min(W, rect.right + pad), bottom: Math.min(H, rect.bottom + pad) }
    : { top: 0, left: 0, right: 0, bottom: 0 };
  const put = (sel, css) => Object.assign(veil.querySelector(sel).style, css);
  if (!rect) {
    put(".tut-top", { top: "0px", left: "0px", width: W + "px", height: H + "px" });
    ["right", "bottom", "left"].forEach((s) =>
      put(".tut-" + s, { width: "0px", height: "0px" }));
    veil.querySelector(".tut-ring").style.opacity = "0";
    return;
  }
  put(".tut-top", { top: "0px", left: "0px", width: W + "px", height: box.top + "px" });
  put(".tut-bottom", { top: box.bottom + "px", left: "0px", width: W + "px",
                       height: Math.max(0, H - box.bottom) + "px" });
  put(".tut-left", { top: box.top + "px", left: "0px", width: box.left + "px",
                     height: (box.bottom - box.top) + "px" });
  put(".tut-right", { top: box.top + "px", left: box.right + "px",
                      width: Math.max(0, W - box.right) + "px",
                      height: (box.bottom - box.top) + "px" });
  Object.assign(veil.querySelector(".tut-ring").style, {
    opacity: "1", top: box.top + "px", left: box.left + "px",
    width: (box.right - box.left) + "px", height: (box.bottom - box.top) + "px",
  });
}

/** Park the card beside the lit rectangle, and inside the window. */
function placeCard(rect) {
  const card = tutEl("tut-card");
  const w = card.offsetWidth;
  const h = card.offsetHeight;
  const W = window.innerWidth;
  const H = window.innerHeight;
  if (!rect) {
    card.style.left = Math.round((W - w) / 2) + "px";
    card.style.top = Math.round((H - h) / 2) + "px";
    return;
  }
  const gap = 16;
  let top = rect.bottom + gap;
  if (top + h > H - 8) top = rect.top - gap - h;        // above instead
  if (top < 8) {
    // Neither: sit beside it, vertically centred on what it points at.
    top = Math.min(Math.max(8, rect.top + rect.height / 2 - h / 2), H - h - 8);
    let left = rect.right + gap;
    if (left + w > W - 8) left = rect.left - gap - w;
    card.style.left = Math.round(Math.min(Math.max(8, left), W - w - 8)) + "px";
    card.style.top = Math.round(top) + "px";
    return;
  }
  let left = rect.left + rect.width / 2 - w / 2;
  left = Math.min(Math.max(8, left), W - w - 8);
  card.style.left = Math.round(left) + "px";
  card.style.top = Math.round(top) + "px";
}

function anchorRect(anchor) {
  if (!anchor) return null;
  const node = document.querySelector(anchor);
  if (!node) return null;
  const rect = node.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  return rect;
}

/* --------------------------------------------------------------- the loop */
Object.assign(Tut, {
  begin() {
    ensureChrome();
    Tut.on = true;
    Tut.i = 0;
    Tut.seen = false;
    Tut.tip = null;
    Tut.fired = new Set();
    Tut.attacked = false;
    Tut.played = false;
    Tut.waitFrom = null;
    document.body.classList.add("tutoring");
    const learn = tutEl("btn-learn");
    if (learn) { learn.textContent = "End tutorial"; learn.classList.remove("nudge"); }
    Tut.pump();
    Tut.sync(state.snap);
  },

  finish(byHand) {
    Tut.on = false;
    Tut.tip = null;
    document.body.classList.remove("tutoring");
    const veil = tutEl("tut-veil");
    const card = tutEl("tut-card");
    if (veil) veil.classList.add("hidden");
    if (card) card.classList.add("hidden");
    try { localStorage.setItem(KEY, byHand ? "quit" : "done"); } catch (err) { /* private mode */ }
    const learn = tutEl("btn-learn");
    if (learn) { learn.textContent = "Learn"; learn.classList.remove("nudge"); }
  },

  advance() {
    Tut.i += 1;
    Tut.seen = false;
    Tut.waitFrom = null;
    if (Tut.i >= STEPS.length) Tut.finish();
  },

  /** Told by `answer()` what this browser just chose, before it is sent. */
  answered(option) {
    if (!Tut.on || !option) return;
    if (option.kind === "Attack") Tut.attacked = true;
    if (option.kind === "PlayCookie") Tut.played = true;
  },

  /** The one contextual aside that applies right now, if it is still new. */
  pickTip(snap) {
    if (Tut.tip && Tut.tip.when(snap)) return Tut.tip;
    if (Tut.tip) { Tut.fired.add(Tut.tip.id); Tut.tip = null; }
    const tip = TIPS.find((t) => !Tut.fired.has(t.id) && t.when(snap));
    Tut.tip = tip || null;
    return Tut.tip;
  },

  sync(snap) {
    if (!Tut.on) return;
    if (!snap || !snap.players) return;
    // A decided game has its own card in the middle of the screen (`#gameover`)
    // and the board behind it is worth reading. Say the closing piece, but
    // light nothing: a spotlight here would dim the one thing being read.
    if (snap.over) {
      Tut.draw(STEPS[STEPS.length - 1], snap, { over: true });
      return;
    }

    const tip = Tut.pickTip(snap);
    if (tip) { Tut.draw(tip, snap, { aside: true }); return; }

    // Walk forward over everything the player has already outrun. The guard is
    // only there so a badly-written condition cannot spin the browser.
    for (let guard = 0; guard < STEPS.length + 2; guard++) {
      const step = STEPS[Tut.i];
      if (!step) { Tut.finish(); return; }
      if (Tut.waitFrom === null) Tut.waitFrom = snap.turn;
      if (!Tut.seen && step.skip && step.skip(snap)) { Tut.advance(); continue; }
      // A step waiting on a moment the shuffle may never deal — a Cookie in
      // hand, a turn with an attack in it — gives up after a few turns rather
      // than holding everything behind it.
      if (!Tut.seen && step.patience !== undefined
          && snap.turn - Tut.waitFrom > step.patience) { Tut.advance(); continue; }
      const ready = !step.ready || step.ready(snap);
      if (ready) Tut.seen = true;
      if (Tut.seen && step.done && step.done(snap)) { Tut.advance(); continue; }
      Tut.draw(step, snap, { waiting: !ready && !Tut.seen });
      return;
    }
  },

  draw(step, snap, opts) {
    ensureChrome();
    const card = tutEl("tut-card");
    card.classList.remove("hidden");
    card.classList.toggle("aside", !!opts.aside);
    const at = STEPS.indexOf(step);
    tutEl("tut-step").textContent = opts.aside
      ? "tip" : `${at + 1} / ${STEPS.length}`;
    tutEl("tut-title").textContent = step.title;
    const waiting = typeof step.wait === "function" ? step.wait(snap) : step.wait;
    const body = opts.waiting
      ? (waiting || "Coming up next — play on.")
      : (typeof step.body === "function" ? step.body(snap) : step.body);
    tutEl("tut-body").innerHTML = body;
    pipify(tutEl("tut-body"));

    const next = tutEl("tut-next");
    const skip = tutEl("tut-skip");
    const hint = tutEl("tut-hint");
    const manual = !opts.aside && !opts.waiting && step.next;
    next.classList.toggle("hidden", !manual);
    if (manual) next.textContent = step.next;
    // A step the game has to answer can still be walked past: someone who
    // knows the rules should not be held at "place a support".
    const skippable = !opts.aside && !opts.waiting && !step.next;
    skip.classList.toggle("hidden", !skippable);
    hint.textContent = opts.waiting ? "waiting…"
      : opts.aside ? "answer it however you like"
      : (step.next ? "" : (typeof step.hint === "function"
                           ? step.hint() : (step.hint || "")));

    // What the follower should keep lit. A waiting step points at nothing —
    // the moment it describes has not arrived — so it dims the board whole.
    Tut.lit = opts.waiting || opts.over ? null : step.anchor;
    const rect = anchorRect(Tut.lit);
    if (opts.over) hideVeil(); else spotlight(rect);
    placeCard(rect);
    Tut.lastKey = "";
  },

  /* The board is rebuilt wholesale on every render, so the node a step points
   * at is a different node a second later. Rather than re-anchor from each of
   * the half-dozen places that redraw, the hole follows its selector. */
  pump() {
    if (!Tut.on) { Tut.frame = 0; return; }
    Tut.frame = requestAnimationFrame(Tut.pump);
    const card = tutEl("tut-card");
    if (!card || card.classList.contains("hidden")) return;
    const over = !!(state.snap && state.snap.over);
    const rect = over ? null : anchorRect(Tut.lit);
    const key = rect ? [rect.top, rect.left, rect.width, rect.height]
      .map(Math.round).join(",") : "none";
    if (key === Tut.lastKey) return;
    Tut.lastKey = key;
    if (over) hideVeil(); else spotlight(rect);
    placeCard(rect);
  },
});

/* --------------------------------------------------------------- starting */
/* The same POST the setup dialog makes, with one flag instead of a menu.
 *
 * `tutorial: true` is the server's business, not the browser's: it deals both
 * decks stacked from `braverse/tutorial.py`, seats the scripted opponent, and
 * turns the shuffle off, so the hand this course talks about is the hand you
 * get. Sending deck names from here would only invite a tutorial dealt out of
 * a deck the steps know nothing about. */
async function startTutorial() {
  if (typeof showTab === "function") showTab("play");
  const res = await api("/api/new", { tutorial: true, delay: 0.6 });
  if (res.error) { alert(res.error); return; }
  // Same reset the setup dialog does: a new match is a new event stream.
  state.version = -1;
  state.pendingId = null;
  state.eventId = 0;
  state.announced = false;
  Tut.begin();
  poll();
}

window.addEventListener("resize", () => { Tut.lastKey = ""; });

const learnBtn = tutEl("btn-learn");
if (learnBtn) {
  learnBtn.onclick = () => {
    if (Tut.on) { Tut.finish(true); return; }
    startTutorial();
  };
  // First visit: say so once, quietly, on the button itself.
  let done = null;
  try { done = localStorage.getItem(KEY); } catch (err) { /* private mode */ }
  if (!done) learnBtn.classList.add("nudge");
}
