// Reading agent replies aloud (#637, docs/superpowers/specs/2026-10-23-read-aloud-design.html).
//
// A chip in a reply's gutter reads that reply and the later replies of its
// turn; the composer's toggle, beside the mic, reads each new reply of the open session as it
// arrives. Speech is made in this browser, so no reply text leaves it. The
// browser's own voice (speechSynthesis) is the default: no download, every
// language the device has. Phonon, a neural voice that sounds far better, is
// an opt-in per browser in Settings: it reads English replies only (the one
// model that is public and Apache 2.0), from files aegis serves pinned
// (speech.py), and only on a device that made speech at least MIN_SPEED times
// faster than real time in the speed check. The spike measured a mid-range
// phone at 0.42-0.70x on WebGPU, which stutters.
//
// One Speaker per browser: one thing speaks at a time. Rows are named by id,
// not held, since a patch replaces a row's node (transcript.js).

import { icon } from "./glyphs.js";

const KEY = "aegis.speech";
export const MIN_SPEED = 1.1;
// Phonon's worker holds the model in memory; it goes after this long without
// speech, unless the session is being followed.
const IDLE_MS = 5 * 60 * 1000;
const BENCH = "This is a short test of how fast this device can read a reply aloud, one sentence at a time.";

// Function words per language, for a reply's language. A reply too short to
// tell takes the browser's.
const WORDS = {
  en: "the of and to in is a that it for with this on are be you i should we",
  es: "el la los las de que y en un una con para por lo está están así o es del se",
  fr: "le la les des de et en un une est que pour pas dans du sur ce qui",
  de: "der die das und ist nicht ein eine zu den mit von auf für ich es",
  pt: "o a os as de que e em um uma com para não do da é se no na",
};
const SETS = Object.fromEntries(Object.entries(WORDS).map(([k, v]) => [k, new Set(v.split(" "))]));
// Said in place of what is not read, in the reply's language.
const SKIPPED = {
  en: { code: "code block", table: "table" },
  es: { code: "bloque de código", table: "tabla" },
  fr: { code: "bloc de code", table: "tableau" },
  de: { code: "Codeblock", table: "Tabelle" },
  pt: { code: "bloco de código", table: "tabela" },
};

export function guessLang(text, fallback = navigator.language || "en") {
  const words = text.toLowerCase().match(/\p{L}+/gu) || [];
  let best = null;
  let top = 1; // one shared word decides nothing
  for (const [lang, set] of Object.entries(SETS)) {
    let n = 0;
    for (const w of words) if (set.has(w)) n++;
    if (n > top) [best, top] = [lang, n];
  }
  return best || fallback.slice(0, 2).toLowerCase();
}

// What a rendered reply says aloud: code blocks and tables become a word,
// a path its file name, a link its text, and the interface parts (copy
// buttons, labels, marks: data-chrome, as quote.js drops them) nothing.
export function speakable(body) {
  const lang = guessLang(body.textContent || "");
  const said = SKIPPED[lang] || SKIPPED.en;
  const c = body.cloneNode(true);
  for (const n of c.querySelectorAll("[data-chrome]")) n.remove();
  for (const n of c.querySelectorAll(".fence, pre")) n.replaceWith(` ${said.code}. `);
  for (const n of c.querySelectorAll(".tblk, table")) n.replaceWith(` ${said.table}. `);
  for (const k of c.querySelectorAll("code")) {
    const t = k.textContent;
    k.replaceWith(/^[\w.~-]*\/[\w./-]+(:\d+(-\d+)?)?$/.test(t) ? t.replace(/:\d+(-\d+)?$/, "").split("/").pop() : t);
  }
  for (const a of c.querySelectorAll("a")) {
    const t = a.textContent;
    if (/^https?:\/\//.test(t)) {
      try {
        a.replaceWith(new URL(t).host);
      } catch {
        // not a URL after all: read as written
      }
    }
  }
  for (const n of c.querySelectorAll("li, h1, h2, h3, h4, h5, h6, p")) n.append(". ");
  const text = c.textContent
    .replace(/\s+/g, " ")
    .replace(/([.!?:;])\s*\.(\s|$)/g, "$1$2")
    .trim();
  return { text, lang };
}

// The reply rows a chip reads: its own and the replies after it, up to the
// person's next message.
export function turnRows(row) {
  const ids = [row.dataset.id];
  for (let n = row.nextElementSibling; n && !n.classList.contains("user"); n = n.nextElementSibling) {
    if (n.classList.contains("prose") && n.dataset.id) ids.push(n.dataset.id);
  }
  return ids;
}

// Sentences, so no utterance runs long: some voices stop one after about 15 s.
function sentences(text) {
  return text
    .split(/(?<=[.!?…])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function loadSettings() {
  try {
    return { engine: "builtin", ...JSON.parse(localStorage.getItem(KEY) || "{}") };
  } catch {
    return { engine: "builtin" };
  }
}

export function saveSettings(s) {
  localStorage.setItem(KEY, JSON.stringify(s));
}

// The browser's own voice.
const builtin = {
  available: () => "speechSynthesis" in globalThis,
  voice(lang) {
    const vs = speechSynthesis.getVoices().filter((v) => v.lang.toLowerCase().startsWith(lang));
    return vs.find((v) => v.localService) || vs[0] || null;
  },
  // Resolves when the text has been said or `signal` aborts.
  say(text, lang, signal) {
    return new Promise((resolve, reject) => {
      if (!this.available()) return reject(new Error("this browser has no speech voices"));
      const parts = sentences(text);
      const voice = this.voice(lang);
      let i = 0;
      const next = () => {
        if (signal.aborted || i >= parts.length) return resolve();
        const u = new SpeechSynthesisUtterance(parts[i++]);
        u.lang = voice?.lang || lang;
        if (voice) u.voice = voice;
        u.onend = next;
        u.onerror = (e) => (e.error === "interrupted" || e.error === "canceled" ? resolve() : reject(new Error(`speech failed: ${e.error}`)));
        speechSynthesis.speak(u);
      };
      signal.addEventListener("abort", () => speechSynthesis.cancel(), { once: true });
      next();
    });
  },
};

// Phonon in a worker, loaded from the files the server pinned.
class Phonon {
  constructor(prepare) {
    this.prepare = prepare;
    this.tts = null;
    this.loading = null;
    this.device = null;
  }

  async module() {
    const { base } = await this.prepare();
    return { base, mod: await import(`${base}index.js`) };
  }

  load(device, onProgress = () => {}) {
    if (this.tts && this.device === device) return Promise.resolve(this.tts);
    if (this.loading && this.device === device) return this.loading;
    this.dispose();
    this.device = device;
    this.loading = (async () => {
      const { base, mod } = await this.module();
      const tts = await mod.PhononTTS.load({
        lang: "en",
        device,
        model: {
          weights: { q8: `${base}model.q8.gguf` },
          tokenizer: `${base}tokenizer.json`,
          config: `${base}config.json`,
          voices: { default: `${base}default-voice.safetensors` },
        },
        onProgress,
      });
      if (this.device !== device) {
        tts.dispose();
        throw new Error("Phonon was turned off while it loaded");
      }
      this.tts = tts;
      return tts;
    })();
    this.loading.catch(() => {
      if (this.device === device) this.loading = null;
    });
    return this.loading;
  }

  dispose() {
    this.tts?.dispose();
    this.tts = null;
    this.loading = null;
    this.device = null;
  }

  // Audio seconds made per second of work, for BENCH on `device`.
  async speed(device, onProgress) {
    const tts = await this.load(device, onProgress);
    await tts.synth("Hello."); // the first call pays for warming up
    const t = performance.now();
    const pcm = await tts.synth(BENCH);
    return pcm.length / tts.sampleRate / ((performance.now() - t) / 1000);
  }

  async say(text, signal, device) {
    const tts = await this.load(device);
    const ctx = new AudioContext({ sampleRate: tts.sampleRate });
    signal.addEventListener("abort", () => ctx.close(), { once: true });
    let at = ctx.currentTime + 0.05;
    const speech = tts.stream(text, { signal });
    try {
      for await (const pcm of speech) {
        if (signal.aborted) break;
        const b = ctx.createBuffer(1, pcm.length, tts.sampleRate);
        b.getChannelData(0).set(pcm);
        const s = ctx.createBufferSource();
        s.buffer = b;
        s.connect(ctx.destination);
        at = Math.max(at, ctx.currentTime);
        s.start(at);
        at += b.duration;
      }
    } catch (e) {
      if (!signal.aborted) throw e;
    }
    await speech.done.catch(() => {});
    if (!signal.aborted) await new Promise((r) => setTimeout(r, Math.max(0, (at - ctx.currentTime) * 1000)));
    if (ctx.state !== "closed") ctx.close();
  }
}

export class Speaker {
  // prepare: () => Promise of {base}, the server's speech.prepare.
  // row(id): the mounted row with that id, or null. onError(message).
  constructor({ prepare, row = () => null, onError = () => {} }) {
    this.phonon = new Phonon(prepare);
    this.row = row;
    this.onError = onError;
    this.settings = loadSettings();
    this.current = null; // {ids, at, abort}
    this.queue = []; // ids waiting, for the followed session
    this.following = false;
    this.idle = 0;
  }

  speakingId() {
    return this.current ? this.current.ids[this.current.at] : null;
  }

  // Whether `id`'s chip shows as speaking: its turn is being read.
  chipOn(id) {
    return !!this.current && this.current.ids[0] === id;
  }

  // A chip's click: start reading from `row`, or stop when it is the one reading.
  toggle(row) {
    if (this.current && this.current.ids[0] === row.dataset.id) return this.stop();
    this.stop();
    this.run(turnRows(row));
  }

  stop() {
    this.queue = [];
    if (!this.current) return;
    this.current.abort.abort();
    this.finish();
  }

  // A new reply in the followed session. Rows already reading go on; at most
  // three wait, the newest kept.
  arrived(id) {
    if (!this.following) return;
    if (!this.current) return this.run([id]);
    this.queue.push(id);
    if (this.queue.length > 3) this.queue.splice(0, this.queue.length - 3);
  }

  setFollowing(on) {
    this.following = on;
    if (!on) this.stop();
    this.armIdle();
  }

  async run(ids) {
    clearTimeout(this.idle);
    const me = { ids, at: 0, abort: new AbortController() };
    this.current = me;
    this.draw();
    try {
      for (; me.at < ids.length && !me.abort.signal.aborted; me.at++) {
        this.draw();
        const body = this.row(ids[me.at])?.querySelector(":scope > .body");
        if (!body) continue;
        const { text, lang } = speakable(body);
        if (text) await this.say(text, lang, me.abort.signal);
      }
    } catch (e) {
      if (!me.abort.signal.aborted) this.onError(e.message || String(e));
    }
    if (this.current !== me) return;
    this.finish();
    if (this.queue.length) this.run([this.queue.shift()]);
  }

  async say(text, lang, signal) {
    const s = this.settings;
    if (!(s.engine === "phonon" && lang === "en" && s.device)) return builtin.say(text, lang, signal);
    if (!this.phonon.tts && this.current) {
      // The first press after a reload loads the model from the browser's cache.
      this.current.loading = true;
      this.draw();
      try {
        await this.phonon.load(s.device);
      } finally {
        if (this.current) this.current.loading = false;
        this.draw();
      }
    }
    return this.phonon.say(text, signal, s.device);
  }

  finish() {
    const was = this.current;
    this.current = null;
    if (was) this.draw(was.ids);
    this.armIdle();
  }

  armIdle() {
    clearTimeout(this.idle);
    if (this.following || this.current) return;
    this.idle = setTimeout(() => this.phonon.dispose(), IDLE_MS);
  }

  // Each mounted row of `ids` (default: the current ones) shows its state.
  draw(ids = this.current?.ids || []) {
    const on = this.speakingId();
    for (const id of ids) {
      const r = this.row(id);
      if (!r) continue;
      r.classList.toggle("speaking", id === on);
      const chip = r.querySelector(".speak");
      if (chip) drawChip(chip, this.chipOn(id), this.chipOn(id) && !!this.current?.loading);
    }
  }

  // Settings: choose an engine. Phonon is kept only after it loads and made
  // speech fast enough here, unless `anyway`. Resolves with the new settings,
  // or the measured speed when it was too slow.
  async choose(engine, { onProgress = () => {}, anyway = false } = {}) {
    this.stop();
    if (engine !== "phonon") {
      this.phonon.dispose();
      this.settings = { engine: "builtin" };
      saveSettings(this.settings);
      return { settings: this.settings };
    }
    if (anyway && this.settings.measured) {
      this.settings = { ...this.settings, engine: "phonon" };
      saveSettings(this.settings);
      return { settings: this.settings };
    }
    const tried = [];
    const devices = ["cpu", ...(globalThis.navigator?.gpu ? ["webgpu"] : [])];
    for (const device of devices) {
      try {
        tried.push({ device, speed: await this.phonon.speed(device, (p) => onProgress({ ...p, device })) });
      } catch (e) {
        if (device === "cpu") throw e;
      }
    }
    this.phonon.dispose();
    const best = tried.sort((a, b) => b.speed - a.speed)[0];
    const measured = { device: best.device, speed: Math.round(best.speed * 100) / 100 };
    if (best.speed < MIN_SPEED) {
      this.settings = { engine: "builtin", measured };
      saveSettings(this.settings);
      return { settings: this.settings, slow: measured };
    }
    this.settings = { engine: "phonon", device: best.device, measured };
    saveSettings(this.settings);
    return { settings: this.settings };
  }
}

export function speakButton(on = false) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "speak";
  b.tabIndex = -1;
  b.dataset.chrome = ""; // not text a selection quotes (quote.js)
  drawChip(b, on);
  return b;
}

function drawChip(b, on, loading = false) {
  b.classList.toggle("on", on);
  b.classList.toggle("load", loading);
  b.title = loading ? "Loading Phonon…" : on ? "Stop reading" : "Read aloud";
  b.setAttribute("aria-label", b.title);
  b.replaceChildren(icon(on ? "stop" : "speak"));
}
