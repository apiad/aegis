// Dependency-free node unit test for the per-kind event renderer.
// Run: node tests/web/renderEvent.test.mjs   (exits non-zero on failure)
import assert from "node:assert";
import { renderEvent } from "../../src/aegis/web/static/js/renderEvent.js";

const rec = (event_type, event, extra = {}) => ({
  event_type, event, truncated: false, seq: 1, handle: "h", ...extra });

// ToolUse: icon + name + path hint from locations
{
  const html = renderEvent(rec("ToolUse",
    { t: "ToolUse", name: "Read", kind: "read", summary: "read x",
      locations: [["/a/b/file.py", 12]] }));
  assert.ok(html.includes("tool-use"));
  assert.ok(html.includes("Read"));
  assert.ok(html.includes("file.py:12"));
  assert.ok(html.includes("📖"));
}

// ToolResult (no diff): first line, status class, expand when truncated
{
  const html = renderEvent(rec("ToolResult",
    { t: "ToolResult", text: "first line\nsecond", is_error: false },
    { truncated: true }));
  assert.ok(html.includes("tool-result ok"));
  assert.ok(html.includes("first line"));
  assert.ok(!html.includes("second"));            // only first line
  assert.ok(html.includes('class="expand"'));
  assert.ok(html.includes('data-seq="1"'));
}

// ToolResult diff → diff rows
{
  const html = renderEvent(rec("ToolResult",
    { t: "ToolResult", text: "", is_error: false,
      diff: { path: "f.py", old: "a\nB\nc", new: "a\nX\nc" } }));
  assert.ok(html.includes("tool-result diff"));
  assert.ok(html.includes("- B"));
  assert.ok(html.includes("+ X"));
}

// Result terminator: duration + cost
{
  const html = renderEvent(rec("Result",
    { t: "Result", duration_ms: 2500, is_error: false, cost_usd: 0.0123 }));
  assert.ok(html.includes("result-sep"));
  assert.ok(html.includes("done in 2.5s"));
  assert.ok(html.includes("¢") || html.includes("$"));
}

// AgentPlan: header + rows + glyphs
{
  const html = renderEvent(rec("AgentPlan",
    { t: "AgentPlan", entries: [
      { content: "do a", status: "completed", priority: "medium" },
      { content: "do b", status: "pending", priority: "high" }] }));
  assert.ok(html.includes("Plan — 1/2 done"));
  assert.ok(html.includes("do a") && html.includes("do b"));
  assert.ok(html.includes("●") && html.includes("○"));
}

// Compact thinking (emptied body) → placeholder + expand
{
  const html = renderEvent(rec("AssistantThinking",
    { t: "AssistantThinking", text: "" }, { truncated: true }));
  assert.ok(html.includes("thinking"));
  assert.ok(html.includes('class="expand"'));
}

// SystemInit → empty (no visible block)
assert.equal(renderEvent(rec("SystemInit", { t: "SystemInit" })), "");

// ToolUse with a folded result → one .tool-call wrapper containing both,
// the result carrying its own seq for expand.
{
  const html = renderEvent(rec("ToolUse",
    { t: "ToolUse", name: "Read", kind: "read", summary: "x.py",
      tool_call_id: "A" },
    { result: { t: "ToolResult", text: "the answer", is_error: false },
      resultSeq: 7, resultTruncated: true }));
  assert.ok(html.includes("tool-call"));
  assert.ok(html.includes("tool-use") && html.includes("Read"));
  assert.ok(html.includes("tool-result ok") && html.includes("the answer"));
  assert.ok(html.includes('data-seq="7"'));   // expand points at the result
}

// ToolUse without a result → bare tool-use, no wrapper.
{
  const html = renderEvent(rec("ToolUse",
    { t: "ToolUse", name: "Bash", kind: "execute", summary: "ls",
      tool_call_id: "B" }));
  assert.ok(html.includes("tool-use") && html.includes("Bash"));
  assert.ok(!html.includes("tool-call"));
  assert.ok(!html.includes("tool-result"));
}

console.log("renderEvent.test.mjs OK");
