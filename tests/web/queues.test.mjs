// node test for the queue-strip formatter. Run: node tests/web/queues.test.mjs
import assert from "node:assert";
import { formatStrip } from "../../src/aegis/web/static/js/queues.js";

assert.deepEqual(formatStrip([]), [], "empty");
assert.deepEqual(
  formatStrip([{ name: "build", running: 2, queued: 1, ok: 5, err: 0 }]),
  ["build ▸2 ⏳1 ✓5 ✗0"], "one queue");
assert.deepEqual(formatStrip(null), [], "null → empty");
assert.equal(
  formatStrip([{ name: "a", running: 0, queued: 0, ok: 0, err: 3 }])[0],
  "a ▸0 ⏳0 ✓0 ✗3", "err count");

console.log("queues.test.mjs: all assertions passed");
