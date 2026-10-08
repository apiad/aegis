// Dictation: the mic button's whole client side. The browser captures the mic,
// resamples it to 16 kHz, cuts it into chunks at pauses and transcribes each in
// one of two Whistle workers, so no audio leaves the page (DESIGN.md, "Audio
// never leaves the browser"). The numbers below were measured on Alex's own
// dictations and phone (docs/superpowers/specs/2026-10-08-dictation-design.md).

export const SR = 16000;
// Chunks stay long while you talk: 20 s gave 27.7% WER against 30.9% at 10 s.
const MIN_CUT = 20 * SR;
const MAX_CUT = 30 * SR; // Whistle's longest single pass
const PAUSE = 0.3 * SR;
const QUIET = 0.01;
// At stop, a longer tail is split across both workers: the wait after stop is
// the one you feel, and two halves finish 1.9 times sooner than the whole.
const SPLIT_OVER = 10 * SR;
const SILENT = 0.001;
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

export class Chunker {
  constructor(emit) {
    this.emit = emit;
    this.buf = new Float32Array(0);
  }

  push(samples) {
    const m = new Float32Array(this.buf.length + samples.length);
    m.set(this.buf);
    m.set(samples, this.buf.length);
    this.buf = m;
    for (;;) {
      const n = this.buf.length;
      if (n >= MIN_CUT && rms(this.buf, n - PAUSE, n) < QUIET) this.cut(n - PAUSE / 2);
      else if (n >= MAX_CUT) this.cut(quietest(this.buf, MIN_CUT, MAX_CUT));
      else return;
    }
  }

  cut(at) {
    const chunk = this.buf.slice(0, at);
    this.buf = this.buf.slice(at);
    this.emit(chunk);
  }

  // What is left at stop, as the pieces to transcribe: none if it is silence,
  // two halves cut at the quiet point of its middle third if it is long.
  finish() {
    const t = this.buf;
    this.buf = new Float32Array(0);
    if (!t.length || rms(t) < SILENT) return [];
    if (t.length <= SPLIT_OVER) return [t];
    const third = Math.floor(t.length / 3);
    const at = quietest(t, third, 2 * third);
    return [t.slice(0, at), t.slice(at)];
  }
}

// The microphone, mono at 16 kHz. Resolves to the function that stops it.
export async function micSource(onSamples) {
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
  return async () => {
    node.port.onmessage = null;
    stream.getTracks().forEach((t) => t.stop());
    await ctx.close();
  };
}

// A piece of text into the recording's target: at the place the last piece
// ended, or at the end of the session's draft if the textarea now shows
// another session.
function insert(rec, text) {
  if (!text) return;
  const t = rec.target;
  if (t.key === null || t.current() === t.key) {
    const v = t.el.value;
    const at = Math.min(rec.at, v.length);
    const before = v.slice(0, at);
    const piece = (before && !/\s$/.test(before) ? " " : "") + text;
    t.el.value = before + piece + v.slice(at);
    rec.at = at + piece.length;
    t.el.dispatchEvent(new Event("input", { bubbles: true }));
  } else {
    const v = localStorage.getItem(t.key) || "";
    localStorage.setItem(t.key, v + (v && !/\s$/.test(v) ? " " : "") + text);
  }
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
    await this.stop();
    if (this.state !== "idle") await new Promise((r) => this.waiters.push(r));
  }

  async toggle(target) {
    if (this.rec) return this.stop();
    return this.start(target);
  }

  async start(target, source = micSource) {
    if (this.rec) await this.stop();
    const rec = {
      target,
      at: target.el.selectionStart ?? target.el.value.length,
      order: [],
      done: new Map(),
      keywords: "",
      stopped: false,
      stopCapture: null,
    };
    rec.chunker = new Chunker((a) => this.enqueue(rec, a));
    this.rec = rec;
    this.target = target;
    this.recs.add(rec);
    this.setState(this.up ? "listening" : "loading");
    try {
      rec.stopCapture = await source((s) => {
        this.onLevel(rms(s));
        rec.chunker.push(s);
      });
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

  async stop() {
    const rec = this.rec;
    if (!rec) return;
    this.rec = null;
    rec.stopped = true;
    if (rec.stopCapture) await rec.stopCapture();
    this.onLevel(0);
    for (const piece of rec.chunker.finish()) this.enqueue(rec, piece);
    this.settle();
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

  enqueue(rec, audio) {
    const id = this.next++;
    rec.order.push(id);
    this.queue.push({ id, audio, rec, secs: audio.length / SR }); // the buffer is transferred away
    this.dispatch();
  }

  dispatch() {
    if (!this.up) return;
    for (const w of this.workers) {
      if (w.job || !this.queue.length) continue;
      const job = this.queue.shift();
      w.job = job;
      w.w.postMessage({ type: "transcribe", id: job.id, audio: job.audio, keywords: job.rec.keywords }, [job.audio.buffer]);
    }
  }

  answer(w, data) {
    const job = w.job;
    w.job = null;
    if (!job) return;
    const rec = job.rec;
    if (data.type === "text") rec.done.set(job.id, data.text);
    else {
      rec.done.set(job.id, "");
      this.onError(`A piece of ${job.secs.toFixed(1)} s could not be transcribed: ${data.message}`);
    }
    while (rec.order.length && rec.done.has(rec.order[0])) {
      const id = rec.order.shift();
      insert(rec, rec.done.get(id));
      rec.done.delete(id);
    }
    this.dispatch();
    this.settle();
  }

  settle() {
    for (const r of this.recs) if (r !== this.rec && !r.order.length) this.recs.delete(r);
    if (!this.rec) this.setState(this.recs.size ? "finishing" : "idle");
  }

  abort(rec, message) {
    rec.stopped = true;
    if (this.rec === rec) this.rec = null;
    if (rec.stopCapture) rec.stopCapture();
    this.onLevel(0);
    this.queue = this.queue.filter((j) => j.rec !== rec);
    rec.order = [];
    this.recs.delete(rec);
    this.onError(message);
    this.settle();
  }
}
