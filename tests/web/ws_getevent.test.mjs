// Run: node tests/web/ws_getevent.test.mjs
import assert from "node:assert";
import { WSClient } from "../../src/aegis/web/static/js/ws.js";

// node has no global WebSocket; _send() checks WebSocket.OPEN.
globalThis.WebSocket = { OPEN: 1 };

const sent = [];
const c = new WSClient("ws://x", "tok");
c.ws = { readyState: 1, send: (s) => sent.push(JSON.parse(s)) };

const p = c.getEvent("h", 7);
const frame = sent[sent.length - 1];
assert.equal(frame.type, "rpc");
assert.equal(frame.method, "get_event");
assert.deepEqual(frame.params, { handle: "h", seq: 7 });

// resolve the pending rpc as the client would on rpc_response
c._handle({ type: "rpc_response", id: frame.id, ok: true,
            result: { event: { t: "ToolResult", text: "FULL" } } });
const res = await p;
assert.equal(res.event.text, "FULL");
console.log("ws_getevent.test.mjs OK");
