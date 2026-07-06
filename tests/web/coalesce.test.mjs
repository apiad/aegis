// Dependency-free node unit test for the streaming-chunk coalescer.
// Run: node tests/web/coalesce.test.mjs   (exits non-zero on failure)
import assert from "node:assert";
import { coalesceInto } from "../../src/aegis/web/static/js/coalesce.js";

function evt(type, { message_id = null, text = "", html = null, seq = 0 } = {}) {
  return {
    type: "stream", kind: "event", handle: "h", seq,
    event_type: type,
    event: { t: type, text, ...(message_id ? { message_id } : {}) },
    html,
  };
}

// 1) two AssistantText chunks, same message_id → one block, concatenated
{
  const history = [];
  const a = coalesceInto(history, evt("AssistantText",
    { message_id: "m1", text: "Hel", seq: 1 }));
  assert.equal(a.action, "append");
  assert.equal(history.length, 1);
  const b = coalesceInto(history, evt("AssistantText",
    { message_id: "m1", text: "lo", seq: 2 }));
  assert.equal(b.action, "update");
  assert.equal(history.length, 1);
  assert.equal(history[0].text, "Hello");
}

// 2) different message_id → two blocks
{
  const history = [];
  coalesceInto(history, evt("AssistantText", { message_id: "m1", text: "a", seq: 1 }));
  coalesceInto(history, evt("AssistantText", { message_id: "m2", text: "b", seq: 2 }));
  assert.equal(history.length, 2);
}

// 3) a ToolUse between two text chunks → three blocks (no cross-merge)
{
  const history = [];
  coalesceInto(history, evt("AssistantText", { message_id: "m1", text: "a", seq: 1 }));
  coalesceInto(history, evt("ToolUse", { html: '<div class="tool-use">x</div>', seq: 2 }));
  coalesceInto(history, evt("AssistantText", { message_id: "m1", text: "b", seq: 3 }));
  assert.equal(history.length, 3);
}

// 4) a unit block with html and no message_id → appended verbatim
{
  const history = [];
  const r = coalesceInto(history, evt("ToolResult",
    { html: '<div class="tool-result ok">ok</div>', seq: 1 }));
  assert.equal(r.action, "append");
  assert.equal(history[0].html, '<div class="tool-result ok">ok</div>');
}

// 5) AssistantThinking coalesces independently of AssistantText
{
  const history = [];
  coalesceInto(history, evt("AssistantThinking", { message_id: "t1", text: "x", seq: 1 }));
  coalesceInto(history, evt("AssistantThinking", { message_id: "t1", text: "y", seq: 2 }));
  assert.equal(history.length, 1);
  assert.equal(history[0].text, "xy");
}

// 6) record carries the compact event dict + truncated + handle
{
  const history = [];
  coalesceInto(history, {
    type: "stream", kind: "event", handle: "h", seq: 9,
    event_type: "ToolResult",
    event: { t: "ToolResult", text: "big\noutput", is_error: false },
    truncated: true,
  });
  const rec = history[0];
  assert.equal(rec.event_type, "ToolResult");
  assert.equal(rec.event.text, "big\noutput");
  assert.equal(rec.truncated, true);
  assert.equal(rec.handle, "h");
}

console.log("coalesce.test.mjs: all assertions passed");
