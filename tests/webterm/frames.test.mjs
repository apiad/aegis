// Run: node tests/webterm/frames.test.mjs   (exits non-zero on failure)
import assert from "node:assert";
import { encodeData, hello, resize, FrameDecoder } from "../../src/aegis/webterm/static/frames.js";

const text = (bytes) => new TextDecoder().decode(bytes);
const pairs = (frames) => frames.map(([t, p]) => [t, text(p)]);

// 1) a data frame is "D", a big-endian length, the payload: the bytes
//    src/aegis/daemon/protocol.py::encode_data emits
assert.deepStrictEqual([...encodeData("hi")], [68, 0, 0, 0, 2, 104, 105]);

// 2) hello and resize are meta frames whose JSON parse_hello accepts
const h = hello("web-abc", 120, 40);
const hlen = new DataView(h.buffer).getUint32(1, false);
assert.strictEqual(String.fromCharCode(h[0]), "M");
assert.strictEqual(h.length, 5 + hlen);
assert.deepStrictEqual(JSON.parse(text(h.slice(5))),
  { type: "hello", view_id: "web-abc", width: 120, height: 40 });
assert.deepStrictEqual(JSON.parse(text(resize(90, 30).slice(5))),
  { type: "resize", width: 90, height: 30 });

// 3) the decoder reassembles frames split anywhere, including mid-header
const stream = new Uint8Array([...encodeData("abc"), ...encodeData("de")]);
for (let cut = 0; cut <= stream.length; cut++) {
  const d = new FrameDecoder();
  const got = [...d.feed(stream.slice(0, cut)), ...d.feed(stream.slice(cut))];
  assert.deepStrictEqual(pairs(got), [["D", "abc"], ["D", "de"]], `cut at ${cut}`);
}

// 4) reset drops a half-received frame, which is what a reconnect needs
const d = new FrameDecoder();
d.feed(encodeData("abcdef").slice(0, 7));
d.reset();
assert.deepStrictEqual(pairs(d.feed(encodeData("x"))), [["D", "x"]]);

console.log("frames.test.mjs: ok");
