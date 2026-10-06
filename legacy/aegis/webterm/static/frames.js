// The daemon's frame codec, in the browser. Mirrors
// src/aegis/daemon/protocol.py: one byte of type ("D" or "M"), four bytes of
// big-endian length, then the payload. The page speaks exactly what
// `aegis attach` speaks, so `aegis web` relays without translating.

const enc = new TextEncoder();

function frame(type, payload) {
  const out = new Uint8Array(5 + payload.length);
  out[0] = type.charCodeAt(0);
  new DataView(out.buffer).setUint32(1, payload.length, false);
  out.set(payload, 5);
  return out;
}

export function encodeData(bytes) {
  return frame("D", typeof bytes === "string" ? enc.encode(bytes) : bytes);
}

export function encodeMeta(obj) {
  return frame("M", enc.encode(JSON.stringify(obj)));
}

export function hello(viewId, width, height) {
  return encodeMeta({ type: "hello", view_id: viewId, width, height });
}

export function resize(width, height) {
  return encodeMeta({ type: "resize", width, height });
}

export class FrameDecoder {
  constructor() {
    this._buf = new Uint8Array(0);
  }

  // Every whole frame in what has arrived so far, as [type, payload].
  feed(chunk) {
    const merged = new Uint8Array(this._buf.length + chunk.length);
    merged.set(this._buf, 0);
    merged.set(chunk, this._buf.length);
    const view = new DataView(merged.buffer);
    const frames = [];
    let at = 0;
    while (merged.length - at >= 5) {
      const size = view.getUint32(at + 1, false);
      if (merged.length - at < 5 + size) break;
      frames.push([String.fromCharCode(merged[at]),
                   merged.slice(at + 5, at + 5 + size)]);
      at += 5 + size;
    }
    this._buf = merged.slice(at);
    return frames;
  }

  reset() {
    this._buf = new Uint8Array(0);
  }
}
