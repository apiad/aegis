// A stand-in for Cactus's needle.js in tests: the six members the worker uses.
// Each chunk is answered with its length and how many keywords came with it;
// a chunk of 12 s or more takes 400 ms and a shorter one 50 ms, so a split
// tail's short second half finishes first.
self.createNeedle = async () => {
  const heap = new Uint8Array(64 << 20);
  let top = 8;
  const e = {
    HEAPU8: heap,
    _malloc: (n) => {
      const p = top;
      top += (n + 7) & ~7;
      return p;
    },
    _free: () => {},
    UTF8ToString: (p) => {
      let q = p;
      while (heap[q]) q++;
      return new TextDecoder().decode(heap.subarray(p, q));
    },
    _needle_load: () => 0,
    _needle_transcribe: (pcm, n, lang, kw, _z, out) => {
      const words = kw ? e.UTF8ToString(kw).split("\n").length : 0;
      const until = performance.now() + (n < 12 * 16000 ? 50 : 400);
      while (performance.now() < until);
      const json = JSON.stringify({ text: ` [${(n / 16000).toFixed(1)}s kw=${words}] `, language: "en" });
      heap.set(new TextEncoder().encode(json + "\0"), out);
      return 0;
    },
  };
  return e;
};
