// Dictation: the mic button's whole client side. The browser captures the mic,
// resamples it to 16 kHz, cuts it into provisional pieces at pauses and, every
// 20 s or so, re-transcribes the stretch as one final piece whose text replaces
// them, in one of two Whistle workers, so no audio leaves the page (DESIGN.md,
// "Audio never leaves the browser"). The numbers below were measured on Alex's
// own dictations and phone (docs/superpowers/specs/2026-10-08-dictation-design.md
// and 2026-10-09-dictation-live-text-design.md).

export const SR = 16000;
// Two lanes (docs/superpowers/specs/2026-10-09-dictation-live-text-design.md):
// provisional pieces of 4 to 8 s that show words a second after a pause, and
// final pieces of 20 to 28 s made of them, whose text replaces the provisional
// text. Joined-up finals measured 29.4% WER against 30.4% for a plain 20 s cut.
const PREVIEW_MIN = 4 * SR;
const PREVIEW_MAX = 8 * SR;
const FINAL_MIN = 20 * SR;
const PAUSE = 0.3 * SR;
const QUIET = 0.01;
// A piece with under a second of voice is what hallucinates ("Thank you."): it
// is never transcribed alone. Silence itself comes back empty from the engine.
const VOICE = 1.0;
// At stop, a longer tail is split across both workers: the wait after stop is
// the one you feel, and two halves finish 1.9 times sooner than the whole.
const SPLIT_OVER = 10 * SR;
const FRAME = 0.2 * SR;
const HOP = 0.05 * SR;

export function rms(a, from = 0, to = a.length) {
  let e = 0;
  for (let i = from; i < to; i++) e += a[i] * a[i];
  return Math.sqrt(e / Math.max(1, to - from));
}

// The middle of the quietest 200 ms frame in [from, to).
export function quietest(a, from, to) {
  let best = Infinity;
  let at = Math.floor((from + to) / 2);
  for (let s = from; s + FRAME <= to; s += HOP) {
    const e = rms(a, s, s + FRAME);
    if (e < best) {
      best = e;
      at = s + FRAME / 2;
    }
  }
  return at;
}

// Seconds of 200 ms frames, 50 ms apart, whose RMS is over the pause threshold.
export function voiceSecs(a) {
  let n = 0;
  for (let s = 0; s + FRAME <= a.length; s += HOP) if (rms(a, s, s + FRAME) > QUIET) n++;
  return n * (HOP / SR);
}

function concat(parts) {
  const out = new Float32Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

// A streaming box-filter resampler from the context's rate to 16 kHz. Asking
// the AudioContext for 16 kHz instead fails in Firefox, which refuses to
// connect a microphone to a context at another rate.
export function resampler(inRate) {
  const ratio = inRate / SR;
  let buf = new Float32Array(0);
  let pos = 0;
  return (block) => {
    const merged = new Float32Array(buf.length + block.length);
    merged.set(buf);
    merged.set(block, buf.length);
    const out = [];
    while (pos + ratio <= merged.length) {
      const a = Math.floor(pos);
      const b = Math.max(a + 1, Math.floor(pos + ratio));
      let s = 0;
      for (let i = a; i < b; i++) s += merged[i];
      out.push(s / (b - a));
      pos += ratio;
    }
    const keep = Math.floor(pos);
    buf = merged.slice(keep);
    pos -= keep;
    return Float32Array.from(out);
  };
}

// Cuts the 16 kHz stream into pieces for the two lanes. `emit` gets
// { parts, final }: a provisional piece has one part; a final piece is the
// stretch since the previous final cut (one part), or at stop its two halves.
export class Chunker {
  constructor(emit) {
    this.emit = emit;
    this.buf = new Float32Array(0); // not yet cut
    this.stretch = []; // pieces cut since the last final, voiced or not
  }

  push(samples) {
    this.buf = concat([this.buf, samples]);
    for (;;) {
      const n = this.buf.length;
      if (n >= PREVIEW_MIN && rms(this.buf, n - PAUSE, n) < QUIET) this.cut(n - PAUSE / 2);
      else if (n >= PREVIEW_MAX) this.cut(quietest(this.buf, PREVIEW_MIN, PREVIEW_MAX));
      else return;
    }
  }

  cut(at) {
    const piece = this.buf.slice(0, at);
    this.buf = this.buf.slice(at);
    this.stretch.push(piece);
    // A copy: the worker takes the emitted buffer away, and the stretch keeps this one.
    if (voiceSecs(piece) >= VOICE) this.emit({ parts: [piece.slice()], final: false });
    if (this.stretch.reduce((n, p) => n + p.length, 0) >= FINAL_MIN) {
      const audio = concat(this.stretch);
      this.stretch = [];
      if (voiceSecs(audio) >= VOICE) this.emit({ parts: [audio], final: true });
    }
  }

  // The pieces at stop, in order: what was pending as a provisional piece, then
  // the stretch as a final, in two halves cut at the quiet point of its middle
  // third when it is long. A stretch with under a second of voice is dropped,
  // unless it is all the recording has (`onlyPiece`).
  finish(onlyPiece = false) {
    const out = [];
    const t = this.buf;
    this.buf = new Float32Array(0);
    if (t.length) {
      this.stretch.push(t);
      if (voiceSecs(t) >= VOICE) out.push({ parts: [t.slice()], final: false });
    }
    const audio = concat(this.stretch);
    this.stretch = [];
    if (!audio.length || (voiceSecs(audio) < VOICE && !onlyPiece)) return out;
    if (audio.length <= SPLIT_OVER) out.push({ parts: [audio], final: true });
    else {
      const third = Math.floor(audio.length / 3);
      const at = quietest(audio, third, 2 * third);
      out.push({ parts: [audio.slice(0, at), audio.slice(at)], final: true });
    }
    return out;
  }
}

// A stop the person did not ask for says why; the four they ask for say nothing.
const STOPPED = {
  track: "Microphone stopped: the browser ended the microphone",
  suspended: "Microphone stopped: the browser suspended the audio",
};

// The microphone, mono at 16 kHz. Resolves to the function that stops it.
// `onEnd` fires once with "track" or "suspended" if the browser ends the capture.
export async function micSource(onSamples, onEnd = () => {}) {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  const ctx = new AudioContext();
  try {
    await ctx.audioWorklet.addModule(new URL("./capture.worklet.js", import.meta.url));
    await ctx.resume();
  } catch (e) {
    stream.getTracks().forEach((t) => t.stop());
    await ctx.close();
    throw e;
  }
  const node = new AudioWorkletNode(ctx, "capture");
  const mute = ctx.createGain();
  mute.gain.value = 0;
  ctx.createMediaStreamSource(stream).connect(node).connect(mute).connect(ctx.destination);
  const rs = resampler(ctx.sampleRate);
  node.port.onmessage = ({ data }) => onSamples(rs(data));
  let closing = false;
  stream.getAudioTracks()[0].addEventListener("ended", () => !closing && onEnd("track"));
  ctx.addEventListener("statechange", () => !closing && ctx.state !== "running" && onEnd("suspended"));
  return async () => {
    closing = true;
    node.port.onmessage = null;
    stream.getTracks().forEach((t) => t.stop());
    await ctx.close();
  };
}

// Where a recording's text goes: the textarea while it still shows the draft
// the recording started in, else that draft in localStorage.
function doc(rec) {
  const t = rec.target;
  if (t.key === null || t.current() === t.key) {
    return {
      kind: "box",
      get: () => t.el.value,
      set: (v) => {
        t.el.value = v;
        t.el.dispatchEvent(new Event("input", { bubbles: true }));
      },
    };
  }
  return { kind: "draft", get: () => localStorage.getItem(t.key) || "", set: (v) => localStorage.setItem(t.key, v) };
}

// The text as it is now, with the recording's offsets moved past whatever the
// person typed or deleted since the last write: the first differing character
// is where their edit sits, and everything at or after it shifts by its length.
// When the text moved to the session's draft, dictation goes on at its end.
function sync(rec, d) {
  const v = d.get();
  if (rec.knownKind !== d.kind) rec.at = d.kind === "draft" ? v.length : Math.min(rec.at, v.length);
  else if (rec.known !== v) {
    const n = Math.min(v.length, rec.known.length);
    let i = 0;
    while (i < n && v[i] === rec.known[i]) i++;
    const delta = v.length - rec.known.length;
    for (const it of rec.placed) if (it.from >= i) Object.assign(it, { from: it.from + delta, to: it.to + delta });
    if (rec.at >= i) rec.at += delta;
  }
  return v;
}

function write(rec, d, v) {
  d.set(v);
  rec.known = v;
  rec.knownKind = d.kind;
}

// A separating space when the text before the insertion point does not end in one.
const lead = (before, text) => (before && !/\s$/.test(before) ? " " : "") + text;

// A provisional piece: at the place the last piece ended, remembered as the
// exact string inserted and its range.
function insert(rec, item, text) {
  if (!text) return;
  const d = doc(rec);
  const v = sync(rec, d);
  const at = Math.min(rec.at, v.length);
  const piece = lead(v.slice(0, at), text);
  write(rec, d, v.slice(0, at) + piece + v.slice(at));
  Object.assign(item, { piece, from: at, to: at + piece.length });
  rec.placed.push(item);
  rec.at = at + piece.length;
}

// A final: replaces the provisional text it covers if that text is still there,
// at its recorded range or wherever it moved to; else the person edited it and
// the final is dropped. With nothing to cover it inserts like a piece.
function replace(rec, item, text) {
  const covered = item.covers.filter((c) => c.piece);
  if (!covered.length) return insert(rec, item, text);
  const d = doc(rec);
  const v = sync(rec, d);
  const expected = covered.map((c) => c.piece).join("");
  let from = covered[0].from;
  if (v.slice(from, from + expected.length) !== expected) {
    from = v.indexOf(expected);
    if (from < 0 || v.indexOf(expected, from + 1) >= 0) return;
  }
  const to = from + expected.length;
  const piece = text ? lead(v.slice(0, from), text) : "";
  write(rec, d, v.slice(0, from) + piece + v.slice(to));
  Object.assign(item, { piece, from, to: from + piece.length });
  rec.placed = rec.placed.filter((it) => !covered.includes(it));
  rec.placed.push(item);
  if (rec.at >= to) rec.at += piece.length - expected.length;
}

// The job a free worker takes: the first provisional one, else the oldest.
export function pick(queue) {
  const i = queue.findIndex((j) => !j.final);
  return i < 0 ? 0 : i;
}

// One recording listens at a time; earlier ones may still have pieces in
// flight. A target is { el, key, current }: the textarea, the localStorage key
// of the draft it holds (null for one that holds no session's draft), and a
// function naming the draft the textarea shows now.
export class Dictation {
  constructor({ prepare, onState = () => {}, onLevel = () => {}, onError = () => {} }) {
    Object.assign(this, { prepare, onState, onLevel, onError });
    this.state = "idle";
    this.target = null;
    this.rec = null; // the recording that is listening
    this.recs = new Set(); // recordings with pieces not yet inserted
    this.workers = null;
    this.ready = null;
    this.up = false;
    this.queue = [];
    this.next = 0;
    this.waiters = []; // finish() calls waiting for idle
  }

  setState(s) {
    this.state = s;
    this.onState(s);
    if (s === "idle") for (const r of this.waiters.splice(0)) r();
  }

  // Stop a recording into `el` and wait until its last piece is in, so a send
  // carries everything that was said.
  async finish(el) {
    if (this.target?.el !== el || this.state === "idle") return;
    await this.stop("send");
    if (this.state !== "idle") await new Promise((r) => this.waiters.push(r));
  }

  async toggle(target, reason = "button") {
    if (this.rec) return this.stop(reason);
    return this.start(target);
  }

  async start(target, source = micSource) {
    if (this.rec) await this.stop();
    const rec = {
      target,
      at: target.el.selectionStart ?? target.el.value.length,
      items: [], // in order, awaiting insertion
      pending: [], // provisional items since the last final
      placed: [], // inserted items whose ranges a final may still replace
      known: "", // the text as this recording last wrote it, and where
      knownKind: null,
      emitted: 0,
      keywords: "",
      stopped: false,
      stopCapture: null,
    };
    rec.chunker = new Chunker((p) => this.enqueue(rec, p));
    this.rec = rec;
    this.target = target;
    this.recs.add(rec);
    this.setState(this.up ? "listening" : "loading");
    try {
      rec.stopCapture = await source(
        (s) => {
          this.onLevel(rms(s));
          rec.chunker.push(s);
        },
        (why) => this.rec === rec && this.stop(why),
      );
    } catch (e) {
      return this.abort(rec, `Microphone unavailable: ${e?.message ?? e}`);
    }
    if (rec.stopped) await rec.stopCapture(); // stopped while asking for the mic
    try {
      const p = await this.prepare();
      rec.keywords = (p.keywords || []).join("\n");
      await this.boot(p.base);
    } catch (e) {
      return this.abort(rec, `Dictation model unavailable: ${e?.message ?? e}`);
    }
    if (this.rec === rec) this.setState("listening");
    this.dispatch();
  }

  async stop(reason = "button") {
    const rec = this.rec;
    if (!rec) return;
    this.rec = null;
    rec.stopped = true;
    if (rec.stopCapture) await rec.stopCapture();
    this.onLevel(0);
    for (const p of rec.chunker.finish(rec.emitted === 0)) this.enqueue(rec, p);
    this.settle();
    if (STOPPED[reason]) this.onError(STOPPED[reason]);
  }

  boot(base) {
    if (this.ready) return this.ready;
    this.ready = new Promise((resolve, reject) => {
      let ready = 0;
      this.workers = [0, 1].map(() => {
        const w = { w: new Worker(new URL("./dictation.worker.js", import.meta.url)), job: null };
        w.w.onmessage = ({ data }) => {
          if (data.type === "ready") {
            if (++ready === 2) {
              this.up = true;
              resolve();
            }
          } else if (data.id === undefined) reject(new Error(data.message));
          else this.answer(w, data);
        };
        w.w.onerror = (e) => reject(new Error(e.message || "the worker did not start"));
        w.w.postMessage({ type: "load", base });
        return w;
      });
    }).catch((e) => {
      for (const w of this.workers || []) w.w.terminate();
      this.workers = null;
      this.ready = null;
      throw e;
    });
    return this.ready;
  }

  enqueue(rec, { parts, final }) {
    const item = { final, jobs: parts.length, texts: [], covers: final ? rec.pending.splice(0) : [] };
    rec.items.push(item);
    rec.emitted++;
    if (!final) rec.pending.push(item);
    parts.forEach((audio, i) => {
      this.queue.push({ id: this.next++, i, audio, item, rec, final, secs: audio.length / SR }); // the buffer is transferred away
    });
    this.dispatch();
  }

  dispatch() {
    if (!this.up) return;
    for (const w of this.workers) {
      if (w.job || !this.queue.length) continue;
      const job = this.queue.splice(pick(this.queue), 1)[0];
      w.job = job;
      w.w.postMessage({ type: "transcribe", id: job.id, audio: job.audio, keywords: job.rec.keywords }, [job.audio.buffer]);
    }
  }

  answer(w, data) {
    const job = w.job;
    w.job = null;
    if (!job) return;
    const { rec, item } = job;
    if (data.type === "text") item.texts[job.i] = data.text;
    else {
      item.texts[job.i] = "";
      this.onError(`A piece of ${job.secs.toFixed(1)} s could not be transcribed: ${data.message}`);
    }
    item.jobs--;
    while (rec.items.length && rec.items[0].jobs === 0) {
      const it = rec.items.shift();
      const text = it.texts.filter(Boolean).join(" ");
      if (it.final) replace(rec, it, text);
      else insert(rec, it, text);
    }
    this.dispatch();
    this.settle();
  }

  settle() {
    for (const r of this.recs) if (r !== this.rec && !r.items.length) this.recs.delete(r);
    if (!this.rec) this.setState(this.recs.size ? "finishing" : "idle");
  }

  abort(rec, message) {
    rec.stopped = true;
    if (this.rec === rec) this.rec = null;
    if (rec.stopCapture) rec.stopCapture();
    this.onLevel(0);
    this.queue = this.queue.filter((j) => j.rec !== rec);
    rec.items = [];
    this.recs.delete(rec);
    this.onError(message);
    this.settle();
  }
}
