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

// 4) a unit block with no message_id → appended verbatim, carrying its event
{
  const history = [];
  const r = coalesceInto(history, evt("ToolResult",
    { text: "ok", seq: 1 }));
  assert.equal(r.action, "append");
  assert.equal(history[0].event_type, "ToolResult");
  assert.equal(history[0].event.text, "ok");
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

// 7) parallel tool results fold into their own use block by tool_call_id
{
  const use = (id, seq) => ({
    type: "stream", kind: "event", handle: "h", seq,
    event_type: "ToolUse",
    event: { t: "ToolUse", name: "Read", tool_call_id: id },
  });
  const res = (id, text, seq) => ({
    type: "stream", kind: "event", handle: "h", seq,
    event_type: "ToolResult",
    event: { t: "ToolResult", text, is_error: false, tool_call_id: id },
  });
  const history = [];
  coalesceInto(history, use("A", 1));
  coalesceInto(history, use("B", 2));
  // results arrive out of order (B before A) — each must fold into its own use
  const rb = coalesceInto(history, res("B", "res-B", 3));
  const ra = coalesceInto(history, res("A", "res-A", 4));
  assert.equal(history.length, 2);               // no trailing result blocks
  assert.equal(rb.action, "update");
  assert.equal(rb.index, 1);
  assert.equal(ra.action, "update");
  assert.equal(ra.index, 0);
  assert.equal(history[0].result.text, "res-A");
  assert.equal(history[0].resultSeq, 4);
  assert.equal(history[1].result.text, "res-B");
}

// 8) a result whose use isn't present appends as a standalone block
{
  const history = [];
  const r = coalesceInto(history, {
    type: "stream", kind: "event", handle: "h", seq: 1,
    event_type: "ToolResult",
    event: { t: "ToolResult", text: "orphan", is_error: false,
             tool_call_id: "ZZZ" },
  });
  assert.equal(r.action, "append");
  assert.equal(history.length, 1);
  assert.equal(history[0].event_type, "ToolResult");
}

// 9) subagent children route onto their Task record's .children
{
  const task = (id, seq) => ({
    type: "stream", kind: "event", handle: "h", seq,
    event_type: "ToolUse",
    event: { t: "ToolUse", name: "Task", summary: "explore", tool_call_id: id },
  });
  const child = (parent, type, ev, seq) => ({
    type: "stream", kind: "event", handle: "h", seq,
    event_type: type, event: { t: type, ...ev, parent_tool_use_id: parent },
  });
  const history = [];
  coalesceInto(history, task("T1", 1));
  const r = coalesceInto(history, child("T1", "ToolUse",
    { name: "Read", tool_call_id: "c1" }, 2));
  assert.equal(r.action, "update");
  assert.equal(r.index, 0);
  assert.equal(history.length, 1);              // no top-level child block
  assert.equal(history[0].children.length, 1);
  assert.equal(history[0].children[0].event.name, "Read");
}

// 10) parallel Tasks route children to their own boxes; in-box tool pairing
{
  const frame = (type, ev, seq) => ({
    type: "stream", kind: "event", handle: "h", seq,
    event_type: type, event: { t: type, ...ev },
  });
  const history = [];
  coalesceInto(history, frame("ToolUse",
    { name: "Task", summary: "A", tool_call_id: "TA" }, 1));
  coalesceInto(history, frame("ToolUse",
    { name: "Task", summary: "B", tool_call_id: "TB" }, 2));
  coalesceInto(history, frame("ToolUse",
    { name: "Read", tool_call_id: "c1", parent_tool_use_id: "TA" }, 3));
  coalesceInto(history, frame("ToolResult",
    { text: "body", is_error: false, tool_call_id: "c1",
      parent_tool_use_id: "TA" }, 4));
  assert.equal(history.length, 2);
  const boxA = history.find((b) => b.event.tool_call_id === "TA");
  const boxB = history.find((b) => b.event.tool_call_id === "TB");
  assert.equal(boxA.children.length, 1);        // the Read, with folded result
  assert.equal(boxA.children[0].result.text, "body");
  assert.ok(!boxB.children || boxB.children.length === 0);
}

// 11) the "Agent"-named dispatcher (Claude Code 2.1.x) also groups children
{
  const frame = (type, ev, seq) => ({
    type: "stream", kind: "event", handle: "h", seq,
    event_type: type, event: { t: type, ...ev },
  });
  const history = [];
  coalesceInto(history, frame("ToolUse",
    { name: "Agent", summary: "explore", tool_call_id: "A1" }, 1));
  const r = coalesceInto(history, frame("AssistantText",
    { text: "child", parent_tool_use_id: "A1" }, 2));
  assert.equal(r.action, "update");
  assert.equal(history.length, 1);
  assert.equal(history[0].children.length, 1);
}

console.log("coalesce.test.mjs: all assertions passed");
