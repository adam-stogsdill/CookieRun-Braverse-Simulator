/* Where a preference is kept: this browser, and the player signed in on it.
 *
 * Every setting in the viewer — the sizes, the sleeves and playmats, the
 * sound, the confirm level, the flipped opponent, the name typed into the
 * online box — used to be a `localStorage` key read and written where it was
 * used. That makes a preference a property of the *browser*, which is wrong on
 * a machine two people share a profile chooser on: signing in as somebody else
 * left you playing with their board.
 *
 * So the owners go through here instead, and this is the only file that knows
 * a profile exists. The rules, in the order they matter:
 *
 *  - `localStorage` is still written, always. It is what the page has to read
 *    at load, before any fetch has answered, and it is the whole store for
 *    somebody who never signs in. Nothing here can make a setting slower to
 *    take effect than it was.
 *  - When a profile is open, a write is *also* posted to it, coalesced so a
 *    dragged slider is one request rather than forty.
 *  - Signing in adopts that profile's settings over this browser's and calls
 *    every `watch` hook, so the board changes under you rather than after a
 *    refresh. A profile that has never saved any adopts *this browser's*
 *    instead — a first sign-in should keep the board you just set up, not
 *    reset it.
 *
 * `KEYS` is the whole list, on purpose: it is what gets sent to a profile, and
 * a key stored but never listed would be a setting that quietly stops
 * following its owner around. `tests/test_viewer.py` pins that every key the
 * viewer reads or writes through here is in it.
 */

const Prefs = (() => {
  const KEYS = [
    "braverse.sizes",         // sizing.js  — the board's four scales and the tilt
    "braverse.tablekit",      // table.js   — sleeves and playmats
    "braverse.confirm",       // confirm.js — how much friction a hard move carries
    "braverse.confirm.ms",    // confirm.js — how long a held move takes
    "sound",                  // sfx.js
    "flipOpponent",           // app.js     — the opponent's half drawn upside down
    "braverse.name",          // app.js     — the name offered when playing online
  ];
  const known = new Set(KEYS);

  const watchers = [];
  let slug = null;            // whose settings are on this page, "" for nobody
  let applying = false;       // inside adopt: a write is not a change to push
  let dirty = {};             // keys changed since the last post
  let timer = null;

  /* --------------------------------------------------------------- store */
  function get(key) {
    try {
      return localStorage.getItem(key);
    } catch (err) { return null; }        // private mode
  }

  function set(key, value) {
    const text = String(value);
    if (get(key) === text) return;        // nothing changed, nothing to save
    try { localStorage.setItem(key, text); } catch (err) { /* as above */ }
    if (applying || !known.has(key)) return;
    dirty[key] = text;
    schedule();
  }

  function drop(key) {
    try { localStorage.removeItem(key); } catch (err) { /* private mode */ }
  }

  /** Everything this browser has an opinion about, as the wire wants it. */
  function all() {
    const out = {};
    KEYS.forEach((key) => {
      const value = get(key);
      if (value !== null) out[key] = value;
    });
    return out;
  }

  /* -------------------------------------------------------------- the wire */
  /* Coalesced rather than sent per write: a slider drag, a Reset sizes and a
   * seat's worth of sleeve clicks are each several writes and one decision. */
  function schedule() {
    if (timer !== null) return;
    timer = setTimeout(flush, 400);
  }

  async function flush() {
    timer = null;
    const changes = dirty;
    dirty = {};
    if (!slug || !Object.keys(changes).length) return;
    try {
      await api("/api/profile/settings", { settings: changes });
    } catch (err) {
      /* Kept in localStorage regardless, so the setting is not lost — only
       * its trip to the profile is, and the next write carries the rest. */
    }
  }

  /* ------------------------------------------------------------- profiles */
  /** Called with the open profile (or null) every time the chooser draws.
   *
   * Idempotent, and does nothing at all while the same player stays signed in
   * — it is the *change* of player that moves settings around. */
  function owner(me) {
    const next = me ? me.slug : "";
    if (next === slug) return;
    slug = next;
    if (!next) return;                    // signed out: this browser keeps its own
    const saved = me.settings || {};
    if (Object.keys(saved).length) adopt(saved);
    else seed();
  }

  /** Put a profile's settings on this page and tell the owners to re-read.
   *
   * A key this player has never set is *removed*, not left alone: an absent
   * setting means the module's own default, and a value left over from
   * whoever was signed in before is the whole bug this file exists to fix —
   * the one that only shows up on the settings the new player has never
   * touched, which are exactly the ones nobody thinks to check. */
  function adopt(saved) {
    applying = true;
    try {
      KEYS.forEach((key) => {
        if (Object.prototype.hasOwnProperty.call(saved, key)) set(key, saved[key]);
        else drop(key);
      });
    } finally {
      applying = false;
    }
    watchers.forEach((fn) => { try { fn(); } catch (err) { /* one bad hook */ } });
  }

  /** A profile with nothing saved takes what is set here, as it stands. */
  function seed() {
    dirty = { ...dirty, ...all() };
    schedule();
  }

  /** Register a function that re-reads this module's settings and applies
   *  them. Called after a sign-in swaps them out, never on an ordinary write:
   *  whoever made the write has already applied it. */
  function watch(fn) {
    if (typeof fn === "function") watchers.push(fn);
  }

  return { KEYS, get, set, all, watch, owner };
})();
