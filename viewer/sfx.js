/* Sound effects, synthesised.
 *
 * Everything here is generated with WebAudio at play time — no audio files, so
 * the viewer stays a page and three text files with nothing to download. Each
 * sound is a shaped noise burst and/or a pitched body, which is plenty for
 * card-flick, swing, impact and crunch.
 *
 * Browsers refuse to start audio without a user gesture, so the context is
 * created lazily on the first click or key press and unlocked there. */

const Sfx = (() => {
  let ctx = null;
  let master = null;
  let enabled = localStorage.getItem("sound") !== "0";
  let noiseBuffer = null;

  function context() {
    if (ctx) return ctx;
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return null;
    ctx = new Ctor();
    master = ctx.createGain();
    master.gain.value = 0.5;
    master.connect(ctx.destination);
    return ctx;
  }

  /** One second of white noise, reused by every noise-based voice. */
  function noise() {
    const c = context();
    if (!noiseBuffer) {
      noiseBuffer = c.createBuffer(1, c.sampleRate, c.sampleRate);
      const data = noiseBuffer.getChannelData(0);
      for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
    }
    const src = c.createBufferSource();
    src.buffer = noiseBuffer;
    return src;
  }

  /** Noise through a band-pass whose frequency sweeps, with an AD envelope. */
  function burst({ at = 0, duration = 0.12, from = 1200, to = 600, q = 1.2,
                   gain = 0.5, type = "bandpass" }) {
    const c = context();
    const t = c.currentTime + at;
    const src = noise();
    const filter = c.createBiquadFilter();
    filter.type = type;
    filter.Q.value = q;
    filter.frequency.setValueAtTime(from, t);
    filter.frequency.exponentialRampToValueAtTime(Math.max(40, to), t + duration);
    const env = c.createGain();
    env.gain.setValueAtTime(0.0001, t);
    env.gain.exponentialRampToValueAtTime(gain, t + Math.min(0.012, duration / 3));
    env.gain.exponentialRampToValueAtTime(0.0001, t + duration);
    src.connect(filter).connect(env).connect(master);
    src.start(t);
    src.stop(t + duration + 0.02);
  }

  /** A pitched body: sine/triangle with a falling frequency. */
  function tone({ at = 0, duration = 0.2, from = 220, to = 80, gain = 0.3,
                  type = "sine" }) {
    const c = context();
    const t = c.currentTime + at;
    const osc = c.createOscillator();
    osc.type = type;
    osc.frequency.setValueAtTime(from, t);
    osc.frequency.exponentialRampToValueAtTime(Math.max(20, to), t + duration);
    const env = c.createGain();
    env.gain.setValueAtTime(0.0001, t);
    env.gain.exponentialRampToValueAtTime(gain, t + 0.01);
    env.gain.exponentialRampToValueAtTime(0.0001, t + duration);
    osc.connect(env).connect(master);
    osc.start(t);
    osc.stop(t + duration + 0.02);
  }

  const voices = {
    // a card flicked over: bright, short, with a little click of the corner
    flip() {
      burst({ duration: 0.09, from: 2600, to: 900, q: 0.8, gain: 0.35 });
      burst({ at: 0.05, duration: 0.06, from: 5200, to: 2600, q: 2, gain: 0.18 });
    },
    // the same, but harder, for a FLIP card actually going off
    flipBig() {
      burst({ duration: 0.11, from: 3200, to: 700, q: 0.7, gain: 0.45 });
      tone({ at: 0.02, duration: 0.4, from: 640, to: 240, gain: 0.16, type: "triangle" });
      tone({ at: 0.06, duration: 0.5, from: 960, to: 320, gain: 0.1, type: "sine" });
    },
    // the swing: air first, then the hit
    attack() {
      burst({ duration: 0.26, from: 700, to: 2600, q: 0.7, gain: 0.22, type: "bandpass" });
    },
    impact() {
      burst({ duration: 0.13, from: 1800, to: 300, q: 0.6, gain: 0.5 });
      tone({ duration: 0.22, from: 180, to: 55, gain: 0.5, type: "sine" });
    },
    // a Cookie breaking: three crunch grains over a low thump
    break() {
      for (let i = 0; i < 3; i++) {
        burst({ at: i * 0.045, duration: 0.1 - i * 0.02, from: 2400 - i * 700,
                to: 500 - i * 120, q: 1.6, gain: 0.42 - i * 0.08 });
      }
      tone({ duration: 0.34, from: 150, to: 42, gain: 0.45, type: "sine" });
      burst({ at: 0.1, duration: 0.3, from: 900, to: 160, q: 0.5, gain: 0.2 });
    },
    // your Cookie hitting the board
    place() {
      burst({ duration: 0.07, from: 1400, to: 500, q: 0.9, gain: 0.28 });
      tone({ duration: 0.12, from: 240, to: 90, gain: 0.22, type: "triangle" });
    },
    // a card sliding off the deck
    draw() {
      burst({ duration: 0.13, from: 900, to: 3000, q: 0.6, gain: 0.16 });
      burst({ at: 0.1, duration: 0.05, from: 4200, to: 2000, q: 2.2, gain: 0.1 });
    },
    // effect damage: a thin electric tick, nothing like the impact thud
    zap() {
      burst({ duration: 0.09, from: 3400, to: 1200, q: 3.2, gain: 0.22 });
      tone({ duration: 0.18, from: 1200, to: 420, gain: 0.14, type: "square" });
    },
    // a skill going off: two quick rising blips
    skill() {
      burst({ duration: 0.07, from: 1800, to: 3200, q: 1.4, gain: 0.2 });
      tone({ duration: 0.16, from: 520, to: 780, gain: 0.2, type: "triangle" });
      tone({ at: 0.09, duration: 0.2, from: 780, to: 1040, gain: 0.16, type: "triangle" });
    },
    win() {
      [523.25, 659.25, 783.99].forEach((f, i) =>
        tone({ at: i * 0.11, duration: 0.5, from: f, to: f, gain: 0.22, type: "triangle" }));
    },
  };

  function play(name, delay = 0) {
    if (!enabled) return;
    const c = context();
    if (!c) return;
    if (c.state === "suspended") c.resume();
    const voice = voices[name];
    if (!voice) return;
    if (delay) setTimeout(() => { if (enabled) voice(); }, delay);
    else voice();
  }

  // Any gesture is enough to unlock the context; after that, sound just works.
  const unlock = () => {
    const c = context();
    if (c && c.state === "suspended") c.resume();
  };
  window.addEventListener("pointerdown", unlock, { once: false });
  window.addEventListener("keydown", unlock, { once: false });

  return {
    play,
    get enabled() { return enabled; },
    set enabled(value) {
      enabled = !!value;
      localStorage.setItem("sound", enabled ? "1" : "0");
      if (enabled) unlock();
    },
  };
})();
