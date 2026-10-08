// One Whistle engine, Cactus's browser build, loaded from the server's pinned
// files (dictation.py). A classic worker, because needle.js is an Emscripten
// script that defines a global createNeedle.
const OUT = 16384;
let engine, out;

function cstr(s) {
  if (!s) return 0;
  const b = new TextEncoder().encode(s);
  const p = engine._malloc(b.length + 1);
  engine.HEAPU8.set(b, p);
  engine.HEAPU8[p + b.length] = 0;
  return p;
}

async function load(base) {
  const get = async (name) => {
    const r = await fetch(base + name);
    if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
    return r;
  };
  const [js, wasm, model] = await Promise.all([
    get("needle.js").then((r) => r.text()),
    get("needle.wasm").then((r) => r.arrayBuffer()),
    get("whistle.cact").then((r) => r.arrayBuffer()),
  ]);
  importScripts(URL.createObjectURL(new Blob([js], { type: "text/javascript" })));
  engine = await createNeedle({ wasmBinary: wasm });
  const bytes = new Uint8Array(model);
  const p = engine._malloc(bytes.length);
  engine.HEAPU8.set(bytes, p);
  if (engine._needle_load(p, BigInt(bytes.length)) !== 0) throw new Error("the model did not load");
  out = engine._malloc(OUT);
}

onmessage = async ({ data }) => {
  try {
    if (data.type === "load") {
      await load(data.base);
      postMessage({ type: "ready" });
      return;
    }
    const a = data.audio;
    const pcm = engine._malloc(a.byteLength);
    const kw = cstr(data.keywords);
    const t = performance.now();
    let rc;
    try {
      engine.HEAPU8.set(new Uint8Array(a.buffer, a.byteOffset, a.byteLength), pcm);
      rc = engine._needle_transcribe(pcm, a.length, 0, kw, 0, out, OUT);
    } finally {
      engine._free(pcm);
      if (kw) engine._free(kw);
    }
    if (rc < 0) throw new Error("transcription failed");
    const r = JSON.parse(engine.UTF8ToString(out));
    postMessage({ type: "text", id: data.id, text: r.text.trim(), language: r.language, ms: performance.now() - t });
  } catch (e) {
    postMessage({ type: "error", id: data.id, message: String(e?.message ?? e) });
  }
};
