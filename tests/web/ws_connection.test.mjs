// Run: node tests/web/ws_connection.test.mjs
import assert from "node:assert";
import { WSClient } from "../../src/aegis/web/static/js/ws.js";

const seen = [];
const c = new WSClient("ws://x", "tok");
c.on("connection", (f) => seen.push(f.connected));

// hello → connected true
c._handle({ type: "hello", constants: {} });
assert.deepEqual(seen, [true]);

// explicit disconnect signal → connected false
c._emitConnection(false);
assert.deepEqual(seen, [true, false]);
console.log("ws_connection.test.mjs OK");
