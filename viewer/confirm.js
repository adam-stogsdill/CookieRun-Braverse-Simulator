/* Misclick protection: a beat of friction on the moves you cannot take back.
 *
 * The server owns the game and there is no undo — an answer sent is a move
 * made. That makes a stray click on the wrong Cookie or on End turn expensive
 * in a way a click in most web pages is not. But a confirmation on *every*
 * move is worse than the problem: a turn is a dozen answers, and a dialog on
 * each of them turns a game into paperwork.
 *
 * So this file does two things, both cheap:
 *
 *   1. A settle guard. The board redraws under the pointer every time a
 *      question is answered, and a click already on its way lands on whatever
 *      moved into that spot. Any answer sent within `SETTLE_MS` of a new
 *      question appearing is dropped with a hint instead. Costs nothing when
 *      you are not misclicking, because you cannot deliberately answer a
 *      question you have not read yet in a quarter of a second.
 *
 *   2. Hold to commit, on the irreversible moves only. Press and hold; a ring
 *      fills and the move goes. A quick click does nothing at all — which is
 *      the point, since a misclick is exactly a quick click. No dialog, no
 *      focus stolen, no second thing to aim at, and the number of clicks in a
 *      turn is unchanged.
 *
 * What holds is decided by `needsHold` and nothing else: `app.js` asks, it
 * never hard-codes a kind. Drags are exempt at every level — pressing a card,
 * moving it across the table and letting go over a zone is already a held
 * gesture, and asking someone to then hold the thing they just dragged is the
 * paperwork this file exists to avoid.
 */
const Confirm = (function () {
  const KEY = "braverse.confirm";
  const HOLD_KEY = "braverse.confirm.ms";
  /* How long a held move takes to commit. A parameter rather than a constant
   * because the right number is a property of the hand on the mouse, not of
   * the game: long enough that a stray click never reaches it, short enough
   * that a whole turn of them is not a wait. 350ms is roughly twice a fast
   * double-click gap, which is the accident it is there to absorb. */
  const HOLD_DEFAULT = 350;
  const HOLD_MIN = 120;
  const HOLD_MAX = 1500;
  const SETTLE_MS = 250;
  // How long after a hold completes a click is still the tail of that hold —
  // the pointerup that ends the press fires one, whenever the press ended.
  const TAIL_MS = 400;

  /* off  — nothing holds; the old one-click behaviour.
   * key  — the moves you cannot take back (the default).
   * all  — every move that reaches the server. */
  const LEVELS = ["off", "key", "all"];

  // The moves "key" protects. An attack and a block spend a rest and land
  // damage; End turn and Pass give up the rest of the turn; a decline throws
  // an effect away. Everything here is a move whose cost is paid the moment it
  // is sent, and none of it can be walked back.
  const KEY_KINDS = new Set(["Attack", "Block", "EndTurn", "Pass"]);
  // A mid-effect question — "which Cookie takes 3 damage?" — is answered by
  // pointing at a card, one click, no menu in between. It is the cheapest
  // click on the table and the one that hurts most when it goes astray.
  const PICK_KINDS = new Set(["cookie", "card"]);
  /* Moves that hold at every level, `off` included. Leaving a match is not a
   * move inside the game — it throws the whole game away — and it is reached
   * by clicking the title in the corner of the header, which is where a mouse
   * rests between turns. `off` promises the old one-click viewer back, and
   * that viewer had no way out of a match at all, so there is nothing here to
   * keep identical. */
  const ALWAYS_HOLD = new Set(["Quit"]);

  let level = read();
  let holdMs = readHold();
  let questionId = null;
  let changedAt = 0;
  let committedAt = 0;
  let live = null;        // the hold in progress, if any

  function read() {
    const saved = Prefs.get(KEY);
    return LEVELS.includes(saved) ? saved : "key";
  }

  function readHold() {
    return clampHold(Number(Prefs.get(HOLD_KEY)));
  }

  /** Anything unusable — absent, not a number, silly — is the default. */
  function clampHold(ms) {
    if (!Number.isFinite(ms) || ms <= 0) return HOLD_DEFAULT;
    return Math.min(HOLD_MAX, Math.max(HOLD_MIN, Math.round(ms)));
  }

  function setHoldMs(ms) {
    holdMs = clampHold(Number(ms));
    cancel(true);   // a hold in flight was timed against the old length
    Prefs.set(HOLD_KEY, String(holdMs));
  }

  function setLevel(next) {
    if (!LEVELS.includes(next)) return;
    level = next;
    cancel();
    Prefs.set(KEY, next);
  }

  /* ------------------------------------------------------- the settle guard */
  /** Called on every render: which question is on screen, and since when. */
  function question(id) {
    if (id === questionId) return;
    questionId = id;
    changedAt = Date.now();
    cancel();      // a hold belongs to the question it started on
  }

  /** False while the board is too freshly redrawn to trust a click on it. */
  function settled() {
    return level === "off" || Date.now() - changedAt > SETTLE_MS;
  }

  /* ------------------------------------------------------ what needs a hold */
  /** `opt` is a pending option, or the string "decline" for a null answer. */
  function needsHold(opt) {
    if (!opt) return false;
    if (opt.kind && ALWAYS_HOLD.has(opt.kind)) return true;
    if (level === "off") return false;
    if (level === "all") return true;
    if (opt === "decline") return true;
    return KEY_KINDS.has(opt.kind) || PICK_KINDS.has(opt.kind);
  }

  /** A short name for the move, so the hint can say what is being refused. */
  function name(opt) {
    if (opt === "decline") return "decline";
    if (!opt) return "confirm";
    if (opt.hold) return opt.hold;                 // a caller with its own words
    if (opt.kind === "EndTurn") return "end your turn";
    if (opt.kind === "Pass") return "pass";
    if (opt.kind === "Attack") return opt.skill && opt.skill !== "Attack"
      ? opt.skill : "attack";
    // Pointing at a card is a choice of that card, and reads as one.
    if (PICK_KINDS.has(opt.kind)) return "choose " + (opt.label || "this card");
    return (opt.skill && opt.skill !== "Play" ? opt.skill : null)
      || opt.label || "confirm";
  }

  /* --------------------------------------------------------------- the hold */
  function ring(node, x, y) {
    const box = document.createElement("div");
    box.className = "holdring";
    const R = 22;
    const C = 2 * Math.PI * R;
    box.innerHTML =
      `<svg viewBox="0 0 52 52" width="52" height="52">
         <circle class="track" cx="26" cy="26" r="${R}"></circle>
         <circle class="fill" cx="26" cy="26" r="${R}"
                 stroke-dasharray="${C}" stroke-dashoffset="${C}"></circle>
       </svg>`;
    document.body.appendChild(box);
    // Over the pointer where there is one, over the middle of the control
    // when the hold came from the keyboard.
    if (x === null) {
      const r = node.getBoundingClientRect();
      x = r.left + r.width / 2;
      y = r.top + r.height / 2;
    }
    box.style.left = x + "px";
    box.style.top = y + "px";
    const fill = box.querySelector(".fill");
    fill.animate([{ strokeDashoffset: C }, { strokeDashoffset: 0 }],
                 { duration: holdMs, easing: "linear", fill: "forwards" });
    return box;
  }

  function cancel(quiet) {
    if (!live) return;
    const held = live;
    live = null;
    clearTimeout(held.timer);
    held.box.remove();
    held.node.classList.remove("holding");
    window.removeEventListener("pointerup", onRelease, true);
    window.removeEventListener("pointercancel", onRelease, true);
    window.removeEventListener("pointermove", onMove, true);
    window.removeEventListener("keyup", onKeyUp, true);
    if (!quiet) hint("hold to " + held.name);
  }

  function onMove(event) {
    // Sliding off the control is a change of mind, and on a touch screen it is
    // usually the start of a scroll.
    if (!live || live.x === null) return;
    if (Math.abs(event.clientX - live.x) > 14
        || Math.abs(event.clientY - live.y) > 14) cancel();
  }

  function onRelease() { cancel(); }
  function onKeyUp(event) {
    // A hold that started on a key ends when that key comes up.
    if (live && live.key && event.key === live.key) cancel();
  }

  /** Start a hold on `node`; `run` fires only if it is held long enough. */
  function start(node, x, y, opt, run, key) {
    cancel(true);
    const box = ring(node, x, y);
    node.classList.add("holding");
    live = {
      node, box, x, y, run, key: key || null, name: name(opt),
      timer: setTimeout(done, holdMs),
    };
    window.addEventListener("pointerup", onRelease, true);
    window.addEventListener("pointercancel", onRelease, true);
    window.addEventListener("pointermove", onMove, true);
    window.addEventListener("keyup", onKeyUp, true);
  }

  function done() {
    if (!live) return;
    const run = live.run;
    live.box.classList.add("full");
    const box = live.box;
    setTimeout(() => box.remove(), 180);
    live.node.classList.remove("holding");
    live.timer = null;
    live = null;
    window.removeEventListener("pointerup", onRelease, true);
    window.removeEventListener("pointercancel", onRelease, true);
    window.removeEventListener("pointermove", onMove, true);
    window.removeEventListener("keyup", onKeyUp, true);
    committedAt = Date.now();
    if (typeof Sfx !== "undefined") Sfx.play("place");
    run();
  }

  /** The click that follows a completed hold is that hold, not a new move. */
  function consumed() { return Date.now() - committedAt < TAIL_MS; }

  /* ------------------------------------------------------------ wiring it up */
  /* One entry point for a control that is a button: it either gets a plain
   * click or it gets the hold, and the caller does not have to know which. */
  function wire(node, opt, run) {
    if (!needsHold(opt)) {
      node.onclick = () => run();
      return node;
    }
    node.classList.add("needhold");
    node.dataset.hold = name(opt);
    // Anything that reaches this control some other way — the number-key
    // shortcuts — goes through `tap`, which finds the hold here.
    node.__hold = { opt, run };
    node.title = (node.title ? node.title + "\n" : "") + "Hold to " + name(opt);
    node.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      start(node, event.clientX, event.clientY, opt, run);
    });
    // Keyboard and screen readers get the same deal: hold the key down.
    node.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      if (live && live.node === node) return;    // key repeat, not a new press
      start(node, null, null, opt, run, event.key);
    });
    node.onclick = (event) => { event.preventDefault(); };
    return node;
  }

  /* The keyboard shortcuts point at a button rather than press it, and a
   * button that wants to be held cannot be clicked. Hold the number key down
   * instead — the same deal the mouse gets, and the ring says so. */
  function tap(node, key) {
    if (!node) return;
    const held = node.__hold;
    if (!held || !needsHold(held.opt)) { node.click(); return; }
    if (live && live.node === node) return;      // key repeat
    start(node, null, null, held.opt, held.run, key || null);
  }

  /* A card is not a button — it already answers to a click, a drag and a menu
   * — so it presses rather than wires: `app.js` decides there is a direct
   * answer on this card and hands the press over. */
  function press(event, node, opt, run) {
    if (!needsHold(opt)) return false;
    event.preventDefault();
    start(node, event.clientX, event.clientY, opt, run);
    return true;
  }

  /* ------------------------------------------------------------------- hint */
  /* Why nothing happened, said once and quietly. A control that refuses a
   * click without saying so reads as a broken control. */
  let hintTimer = null;
  function hint(text) {
    let box = document.getElementById("hintline");
    if (!box) {
      box = document.createElement("div");
      box.id = "hintline";
      document.body.appendChild(box);
    }
    box.textContent = text;
    box.classList.add("on");
    clearTimeout(hintTimer);
    hintTimer = setTimeout(() => box.classList.remove("on"), 1600);
  }

  return {
    get level() { return level; },
    set level(next) { setLevel(next); },
    get holdMs() { return holdMs; },
    set holdMs(ms) { setHoldMs(ms); },
    /** Re-read both settings without writing them back — for `Prefs.watch`,
     *  which has just swapped one player's preferences for another's. */
    reload() { level = read(); holdMs = readHold(); cancel(true); },
    LEVELS, HOLD_DEFAULT, HOLD_MIN, HOLD_MAX,
    question, settled, needsHold, name, wire, press, tap, consumed, cancel, hint,
  };
})();
