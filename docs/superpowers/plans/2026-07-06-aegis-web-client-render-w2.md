# Aegis Web Client-Side Rendering (W2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the browser render each transcript block from the compact
`event` payload (mirroring `render_html.py`), add tap-to-expand for truncated
blocks via `get_event`, then drop the server-rendered `html` field from the
wire — the commit where the diet actually pays off.

**Architecture:** A new pure `renderEvent.js` returns an HTML string per event
kind (JS port of `render_html.py` + `render_shared`), wrapped by the existing
`nodeFromHtml`. The coalesce block record carries the compact `event` dict and
its `truncated` flag; `app.js` renders from those instead of `rec.html`.
Truncated blocks show an expand control that fetches the full event on demand
(`get_event`) and reveals the full body in a `<pre>`. Finally `event_frame`
stops sending `html`.

**Tech Stack:** Vanilla ES modules (no build step), dependency-free node
`.mjs` unit tests, Python/Starlette backend, `uv`.

## Global Constraints

- Python **3.13+**, `uv`. Python tests: `uv run python -m pytest -q -m "not live"`
  (use `python -m pytest`, not bare `uv run pytest`). Never `-k "not live"`.
  Never pipe pytest straight into `tail` — check the real exit code.
- JS tests are **dependency-free node scripts**, run `node tests/web/<name>.test.mjs`
  (exit non-zero on failure); mirror the style of `tests/web/coalesce.test.mjs`.
  No jsdom, no framework — `renderEvent` returns **strings**, asserted by substring.
- No build step, no SPA framework. Plain ES modules under
  `src/aegis/web/static/js/`.
- Commit straight to **`main`** (aegis convention). TDD: red → green → commit,
  one logical change per commit.
- This plan **depends on W0+W1** (already merged): compact `event` frames with a
  `truncated` flag and the `get_event` RPC exist.
- **Breaking cut is isolated to Task 4.** Through Tasks 1–3 the client stops
  *using* `html` but the server still sends it. Task 4 removes it from the frame
  and updates the tests that assert it — in one commit, so no half-broken state.

## File Structure

- `src/aegis/web/static/js/renderEvent.js` — **create**: `renderEvent(rec) ->
  string` + ported helpers (`escapeHtml`, `pathhint`, `diffWindow`,
  `resultParts`, `fmtCost`, `KIND_ICON`, `PLAN_GLYPH`, `expandControl`).
- `src/aegis/web/static/js/coalesce.js` — **modify**: block record carries
  `event`, `truncated`, `handle` (drops `html` in Task 4).
- `src/aegis/web/static/js/app.js` — **modify**: `blockEl` renders via
  `renderEvent`; expand-click delegation; per-tab detail cache.
- `src/aegis/web/static/js/ws.js` — **modify**: `getEvent(handle, seq)`.
- `src/aegis/web/static/css/base.css` — **modify**: `.expand` / `pre.expanded`.
- `src/aegis/web/subscriptions.py` — **modify** (Task 4): `event_frame` drops
  `html`; drop the `render_event_html` import.
- Tests: `tests/web/renderEvent.test.mjs` (**create**),
  `tests/web/coalesce.test.mjs` (**modify**), `tests/test_web_protocol.py`
  (**modify**, Task 4).

---

## Task 1: `renderEvent.js` — per-kind HTML-string renderer

Pure JS port of `render_html.py` + `render_shared`, reading the compact `event`.

**Files:**
- Create: `src/aegis/web/static/js/renderEvent.js`
- Test: `tests/web/renderEvent.test.mjs`

**Interfaces:**
- Produces: `renderEvent(rec) -> string`, where `rec = {event_type, event, truncated, seq, handle}` and `event` is an `encode_event()` dict. Returns `""` for kinds with no visible representation (`SystemInit`, `Unknown`). `AssistantText` uses `renderMarkdown`; other kinds mirror `render_html.py`. Truncated `ToolUse`/`ToolResult`/`AssistantThinking` include an `<span class="expand" data-handle data-seq>` control.
- Produces: `expandControl(rec, label) -> string`.

- [ ] **Step 1: Write the failing test**

Create `tests/web/renderEvent.test.mjs`:

```js
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

console.log("renderEvent.test.mjs OK");
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/web/renderEvent.test.mjs`
Expected: FAIL — `Cannot find module '.../renderEvent.js'`.

- [ ] **Step 3: Implement `renderEvent.js`**

Create `src/aegis/web/static/js/renderEvent.js`:

```js
// Per-kind event → HTML-string renderer. Browser mirror of
// aegis.render_html.render_event_html (+ aegis.render_shared helpers),
// reading the compact `event` dict. Returns "" for kinds with no visible
// block. Wrapped by app.js's nodeFromHtml.
import { renderMarkdown } from "./markdown.js";

const KIND_ICON = {
  read: "📖", edit: "✏️", execute: "⌬", search: "🔎", think: "✻",
  fetch: "🌐", move: "➡️", delete: "🗑", switch_mode: "🔄", other: "⏺",
};
const PLAN_GLYPH = { completed: "●", in_progress: "◐", pending: "○" };

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function pathhint(ev) {
  const locs = ev.locations || [];
  if (locs.length) {
    const [path, line] = locs[0];
    const tail = path ? path.split("/").pop() : "";
    return line != null ? `${tail}:${line}` : tail;
  }
  return ev.summary || "";
}

function diffWindow(oldText, newText, maxLines = 6) {
  const o = oldText ? oldText.split("\n") : [];
  const n = newText ? newText.split("\n") : [];
  let head = 0;
  while (head < o.length && head < n.length && o[head] === n[head]) head++;
  let tail = 0;
  while (tail < o.length - head && tail < n.length - head
         && o[o.length - 1 - tail] === n[n.length - 1 - tail]) tail++;
  const removed = o.slice(head, o.length - tail);
  const added = n.slice(head, n.length - tail);
  const shownRemoved = [], shownAdded = [];
  let budget = maxLines;
  for (const l of removed) { if (budget <= 0) break; shownRemoved.push(l); budget--; }
  for (const l of added) { if (budget <= 0) break; shownAdded.push(l); budget--; }
  const elided = (removed.length + added.length)
    - (shownRemoved.length + shownAdded.length);
  return { shownRemoved, shownAdded, elided };
}

function fmtCost(usd) {
  const cents = usd * 100;
  if (cents < 1) return `${Math.round(cents * 10) / 10}¢`;
  if (usd < 1) return `${Math.floor(cents)}¢`;
  return `$${usd.toFixed(2)}`;
}

function resultParts(ev) {
  const secs = (ev.duration_ms || 0) / 1000;
  const parts = [`done in ${secs.toFixed(1)}s`];
  if (ev.cost_usd != null && ev.cost_usd > 0) parts.push(fmtCost(ev.cost_usd));
  if (ev.stop_reason && ev.stop_reason !== "end_turn") parts.push(ev.stop_reason);
  return parts;
}

export function expandControl(rec, label) {
  return `<span class="expand" data-handle="${esc(rec.handle)}" `
    + `data-seq="${rec.seq}">${esc(label)}</span>`;
}

function diffHtml(diff) {
  const { shownRemoved, shownAdded, elided } = diffWindow(diff.old, diff.new);
  const rows = [`<div class="diff-head">┌ ${esc(diff.path)}</div>`];
  for (const l of shownRemoved) rows.push(`<div class="diff-row removed">- ${esc(l)}</div>`);
  for (const l of shownAdded) rows.push(`<div class="diff-row added">+ ${esc(l)}</div>`);
  if (elided > 0) {
    const s = elided !== 1 ? "s" : "";
    rows.push(`<div class="diff-more">… ${elided} more line${s}</div>`);
  }
  return `<div class="tool-result diff">${rows.join("")}</div>`;
}

function planHtml(ev) {
  const entries = ev.entries || [];
  if (!entries.length) return '<div class="agent-plan muted">📋 (no plan)</div>';
  const done = entries.filter((e) => e.status === "completed").length;
  const rows = [`<div class="plan-head">📋 Plan — ${done}/${entries.length} done</div>`];
  for (const e of entries) {
    const glyph = PLAN_GLYPH[e.status] || "○";
    const prio = (e.priority === "high" || e.priority === "low") ? ` ${e.priority}` : "";
    rows.push(`<div class="plan-row ${e.status}${prio}">`
      + `<span class="glyph">${glyph}</span> ${esc(e.content)}</div>`);
  }
  return `<div class="agent-plan">${rows.join("")}</div>`;
}

export function renderEvent(rec) {
  const ev = rec.event || {};
  const t = rec.event_type;

  if (t === "AssistantText") {
    const text = (ev.text || "").trim();
    if (!text) return "";
    return `<div class="assistant-text">${renderMarkdown(ev.text)}</div>`;
  }
  if (t === "AssistantThinking") {
    const body = (ev.text || "").trim();
    if (!body) {
      const ctl = rec.truncated ? " " + expandControl(rec, "expand") : "";
      return `<div class="thinking muted">✻ Thinking…${ctl}</div>`;
    }
    return `<div class="thinking muted"><em>✻ ${esc(body)}</em></div>`;
  }
  if (t === "ToolUse") {
    const icon = KIND_ICON[ev.kind || ""] || "⏺";
    const hint = pathhint(ev);
    const arg = (hint && hint !== ev.name)
      ? `<span class="tool-hint">(${esc(hint)})</span>` : "";
    const ctl = rec.truncated ? " " + expandControl(rec, "⋯") : "";
    return `<div class="tool-use"><span class="icon">${icon}</span> `
      + `<span class="tool-name">${esc(ev.name)}</span>${arg}${ctl}</div>`;
  }
  if (t === "ToolResult") {
    if (ev.diff && !ev.is_error) return diffHtml(ev.diff);
    const raw = ev.text || "";
    let first = raw.trim() ? raw.split("\n")[0] : "";
    if (first.length > 100) first = first.slice(0, 100) + "…";
    const cls = ev.is_error ? "error" : "ok";
    const ctl = rec.truncated ? " " + expandControl(rec, "⋯") : "";
    return `<div class="tool-result ${cls}">└ `
      + `<span class="status">${cls}</span> ${esc(first)}${ctl}</div>`;
  }
  if (t === "AgentPlan") return planHtml(ev);
  if (t === "Result") {
    return `<div class="result-sep">── ${esc(resultParts(ev).join(" · "))} ──</div>`;
  }
  return "";
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node tests/web/renderEvent.test.mjs`
Expected: `renderEvent.test.mjs OK`.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/web/static/js/renderEvent.js tests/web/renderEvent.test.mjs
git commit -m "feat(web): renderEvent.js — client-side per-kind renderer (mirrors render_html)"
```

---

## Task 2: Block record carries `event`+`truncated`; `blockEl` renders via `renderEvent`

Switch the client's render source from `rec.html` to the compact event — while
the server still sends `html` (unused now).

**Files:**
- Modify: `src/aegis/web/static/js/coalesce.js`
- Modify: `src/aegis/web/static/js/app.js` (`blockEl`, imports)
- Test: `tests/web/coalesce.test.mjs`

**Interfaces:**
- Consumes: `renderEvent` (Task 1).
- Produces: coalesce block record `{ seq, event_type, message_id, text, event, truncated, handle, html }` (html retained until Task 4). `blockEl(rec)` returns a DOM node built from `renderEvent(rec)` (falling back to `textBlock`), except `AssistantText` which keeps its markdown+streaming path.

- [ ] **Step 1: Update the coalesce test to expect the new fields**

In `tests/web/coalesce.test.mjs`, extend the append assertions to check the
record carries the compact event. Add after the existing "append" block (adapt
to the file's existing `evt()` helper — it already sets `event`):

```js
// record carries the compact event dict + truncated + handle
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/web/coalesce.test.mjs`
Expected: FAIL — `rec.event` / `rec.truncated` / `rec.handle` undefined.

- [ ] **Step 3: Carry the new fields in `coalesce.js`**

In `src/aegis/web/static/js/coalesce.js`, update the `history.push({...})` object
(and the block-record doc comment) to include `event`, `truncated`, `handle`:

```js
  history.push({
    seq: frame.seq,
    event_type: eventType,
    message_id: messageId,
    text,
    event: ev,
    truncated: frame.truncated ?? false,
    handle: frame.handle,
    html: frame.html ?? null,
  });
```

- [ ] **Step 4: Point `blockEl` at `renderEvent`**

In `src/aegis/web/static/js/app.js`, add the import near the top:

```js
import { renderEvent } from "./renderEvent.js";
```

Replace the body of `blockEl` (currently lines ~72-81):

```js
function blockEl(rec) {
  if (rec.event_type === "AssistantText") {
    const div = document.createElement("div");
    div.className = "assistant-text";
    div.innerHTML = renderMarkdown(rec.text);
    return div;
  }
  const html = renderEvent(rec);
  return html ? (nodeFromHtml(html) || textBlock(rec)) : textBlock(rec);
}
```

(`renderInto`'s `AssistantText` streaming-update branch at line ~96 is
unchanged — it re-renders markdown into the existing node. Non-streaming kinds
only ever `append`.)

- [ ] **Step 5: Run tests to verify green**

Run: `node tests/web/coalesce.test.mjs && node tests/web/renderEvent.test.mjs`
Expected: both print OK.

- [ ] **Step 6: Manual browser smoke (no regression)**

Run `aegis web` in a repo, spawn an agent, send a message that triggers a tool
call. Confirm the transcript still renders tool blocks, plan, and the
terminator line the same as before (now via `renderEvent`, `html` unused).

- [ ] **Step 7: Commit**

```bash
git add src/aegis/web/static/js/coalesce.js src/aegis/web/static/js/app.js tests/web/coalesce.test.mjs
git commit -m "feat(web): render transcript from compact event, not server html"
```

---

## Task 3: Tap-to-expand → `get_event` + per-tab cache

Reveal the full body of a truncated block on demand.

**Files:**
- Modify: `src/aegis/web/static/js/ws.js` (`getEvent`)
- Modify: `src/aegis/web/static/js/app.js` (click delegation, cache, `<pre>`)
- Modify: `src/aegis/web/static/css/base.css` (`.expand`, `pre.expanded`)
- Test: `tests/web/ws_getevent.test.mjs` (create)

**Interfaces:**
- Consumes: `get_event` RPC (W1), `expandControl` markup (Task 1).
- Produces: `AegisClient.getEvent(handle, seq) -> Promise<{event}>`.

- [ ] **Step 1: Write the failing test for `getEvent`**

Create `tests/web/ws_getevent.test.mjs` (mirrors how `ws.js` builds rpc frames;
stub the socket send):

```js
// Run: node tests/web/ws_getevent.test.mjs
import assert from "node:assert";
import { AegisClient } from "../../src/aegis/web/static/js/ws.js";

const sent = [];
const c = new AegisClient("ws://x", "tok");
c.ws = { readyState: 1, send: (s) => sent.push(JSON.parse(s)) };

const p = c.getEvent("h", 7);
const frame = sent[sent.length - 1];
assert.equal(frame.type, "rpc");
assert.equal(frame.method, "get_event");
assert.deepEqual(frame.params, { handle: "h", seq: 7 });

// resolve the pending rpc as the client would on rpc_response
c._onMessage({ data: JSON.stringify({
  type: "rpc_response", id: frame.id, ok: true,
  result: { event: { t: "ToolResult", text: "FULL" } } }) });
const res = await p;
assert.equal(res.event.text, "FULL");
console.log("ws_getevent.test.mjs OK");
```

Note: adapt `c.ws` / `c._onMessage` to the real property + handler names in
`ws.js` (the message handler is the method assigned to `ws.onmessage`; the
socket field is `this.ws`). Read `ws.js` first and match them exactly.

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/web/ws_getevent.test.mjs`
Expected: FAIL — `c.getEvent is not a function`.

- [ ] **Step 3: Add `getEvent` to `ws.js`**

In `src/aegis/web/static/js/ws.js`, next to `subscribe`, add:

```js
  getEvent(handle, seq) {
    return this.rpc("get_event", { handle, seq });
  }
```

- [ ] **Step 4: Run the getEvent test green**

Run: `node tests/web/ws_getevent.test.mjs`
Expected: `ws_getevent.test.mjs OK`.

- [ ] **Step 5: Wire expand-click delegation in `app.js`**

Add a per-tab detail cache and a single delegated click handler on the panes
container. After the `panesEl` is defined, add:

```js
const detailCache = new Map();   // `${handle}:${seq}` -> full event dict

function fullBody(event) {
  if (event.t === "ToolUse") return JSON.stringify(event.raw_input ?? {}, null, 2);
  return event.text || "";       // ToolResult / AssistantThinking
}

panesEl.addEventListener("click", async (e) => {
  const ctl = e.target.closest(".expand");
  if (!ctl) return;
  const handle = ctl.dataset.handle;
  const seq = Number(ctl.dataset.seq);
  const block = ctl.closest(".tool-use, .tool-result, .thinking");
  if (!block) return;
  const existing = block.parentElement.querySelector(
    `pre.expanded[data-seq="${seq}"]`);
  if (existing) { existing.remove(); return; }   // toggle off
  const key = `${handle}:${seq}`;
  let ev = detailCache.get(key);
  if (!ev) {
    ctl.classList.add("loading");
    try { ev = (await client.getEvent(handle, seq)).event; }
    finally { ctl.classList.remove("loading"); }
    if (!ev) return;
    detailCache.set(key, ev);
  }
  const pre = document.createElement("pre");
  pre.className = "expanded";
  pre.dataset.seq = String(seq);
  pre.textContent = fullBody(ev);
  block.insertAdjacentElement("afterend", pre);
});
```

- [ ] **Step 6: Add expand styles to `base.css`**

Append to `src/aegis/web/static/css/base.css`:

```css
.expand { cursor: pointer; text-decoration: underline dotted;
          opacity: 0.7; user-select: none; }
.expand:hover { opacity: 1; }
.expand.loading { opacity: 0.4; }
pre.expanded { white-space: pre-wrap; word-break: break-word;
               margin: 0.25rem 0 0.5rem 1rem; padding: 0.5rem;
               border-left: 2px solid var(--muted, #888);
               font-size: 0.85em; overflow-x: auto; }
```

- [ ] **Step 7: Manual browser verification (the interaction)**

`aegis web`, trigger a long tool result and a thinking block. Confirm: a compact
line with an expand affordance; clicking it fetches and reveals the full body in
a `<pre>`; clicking again collapses it; a second expand is instant (cached).

- [ ] **Step 8: Commit**

```bash
git add src/aegis/web/static/js/ws.js src/aegis/web/static/js/app.js src/aegis/web/static/css/base.css tests/web/ws_getevent.test.mjs
git commit -m "feat(web): tap-to-expand truncated blocks via get_event (+cache)"
```

---

## Task 4: Drop `html` from the wire (the breaking cut)

The client no longer reads `html`; stop sending it and update the tests that
asserted it. One commit — no half-broken state.

**Files:**
- Modify: `src/aegis/web/subscriptions.py` (`event_frame`, imports)
- Modify: `src/aegis/web/static/js/coalesce.js` (drop the `html` field)
- Test: `tests/test_web_protocol.py` (update the two `html` assertions)

**Interfaces:**
- Produces: `event_frame(handle, seq, ev) -> {type, kind, handle, seq, event_type, event:<compact>, truncated}` — no `html` key.

- [ ] **Step 1: Update the Python protocol tests to assert compact `event`, not `html`**

In `tests/test_web_protocol.py::test_subscribe_streams_history_then_live`,
replace the two `html` assertions:

```python
    assert events[0]["event"]["text"] == "one"
```
and
```python
    assert live["seq"] == 3 and live["event"]["text"] == "three"
```

(Grep the test module for `"html"` and update any remaining occurrences the same
way — assert on `["event"]` fields instead.)

- [ ] **Step 2: Run to verify the test now fails against the current frame**

Run: `uv run python -m pytest tests/test_web_protocol.py::test_subscribe_streams_history_then_live -q`
Expected: PASS actually — the frame still has `event` too. To make this a true
red for the cut, also assert absence of `html`:

```python
    assert "html" not in events[0]
```

Re-run: FAIL — `html` still present in the frame.

- [ ] **Step 3: Drop `html` from `event_frame`**

In `src/aegis/web/subscriptions.py`, remove the `"html": render_event_html(ev),`
line from `event_frame`, and remove the now-unused import
`from aegis.render_html import render_event_html`.

```python
def event_frame(handle: str, seq: int, ev) -> dict:
    """The canonical ``stream/event`` frame shape, shared by history replay
    and live fan-out. The ``event`` field is compacted; full detail is fetched
    on demand via the ``get_event`` RPC."""
    compact, truncated = compact_encoded(encode_event(ev))
    return {
        "type": "stream", "kind": "event",
        "handle": handle, "seq": seq,
        "event_type": type(ev).__name__,
        "event": compact,
        "truncated": truncated,
    }
```

- [ ] **Step 4: Drop the `html` field from the client record**

In `src/aegis/web/static/js/coalesce.js`, remove `html: frame.html ?? null,`
from the `history.push({...})` and the `if (frame.html != null) last.html = ...`
line in the update branch, and update the block-record doc comment.

- [ ] **Step 5: Run Python + JS suites green**

Run: `uv run python -m pytest tests/test_web_protocol.py tests/test_web_subscriptions.py tests/test_web_server.py -q`
Then: `node tests/web/coalesce.test.mjs && node tests/web/renderEvent.test.mjs`
Expected: all pass. (If `test_web_subscriptions.py` or others still assert
`html`, grep `"html"` across `tests/test_web_*.py` and update them.)

- [ ] **Step 6: Commit**

```bash
git add src/aegis/web/subscriptions.py src/aegis/web/static/js/coalesce.js tests/test_web_protocol.py
git commit -m "feat(web): drop server-rendered html from the wire — render client-side"
```

---

## Task 5: Full gate + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the JS tests**

Run: `for f in tests/web/*.test.mjs; do node "$f" || exit 1; done; echo ALL_JS_OK`
Expected: `ALL_JS_OK`.

- [ ] **Step 2: Run the Python blast radius**

Run: `uv run python -m pytest tests/test_web_*.py -q; echo "EXIT=$?"`
Expected: `EXIT=0`. (The TUI-file-watcher inotify flake is unrelated — see the
project memory; it doesn't touch web tests.)

- [ ] **Step 3: Manual end-to-end browser check**

`aegis web` on a real repo: spawn, stream a turn with tool calls + a plan +
thinking; verify full transcript fidelity from the compact wire, expand/collapse
works, reconnect (`resume`) still replays. Confirm in devtools Network that
`event` frames no longer carry an `html` field.

- [ ] **Step 4: CHANGELOG**

Under `## [Unreleased]`:

```markdown
- Web client renders transcripts **client-side** from the compact `event`
  payload (`renderEvent.js`, mirroring `render_html.py`); the server no longer
  ships a rendered `html` blob per event. Truncated blocks (tool input/output,
  thinking) expand on tap via `get_event`, cached per tab. Completes the wire
  diet: tool-heavy turns stream a fraction of the previous bytes.
```

- [ ] **Step 5: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): client-side rendering + html dropped from wire"
git push origin main
```

---

## Self-Review notes (for the executor)

- **Spec coverage (W2):** renderer = Task 1; render-from-compact = Task 2;
  tap-to-expand = Task 3; `html` removal = Task 4. All of W2's spec section is
  covered.
- **Parity approach:** `renderEvent.js` is a near-line-for-line port of
  `render_html.py` + `render_shared`, so structural parity is by construction;
  node tests assert the salient content per kind. `fmtCost` uses float math vs
  the server's `Decimal` — display-only, negligible; revisit only if a cent
  rounding visibly diverges.
- **Divergence kept intentionally:** `AssistantText` renders **markdown**
  client-side (richer than the server's escaped text) — this matches the
  current shipped client, do not "fix" it to match `render_html.py`.
- **Ordering safety:** Tasks 1–3 leave `html` on the frame (unused); Task 4 is
  the single breaking commit that removes it and updates the asserting tests
  together. Do not remove `html` earlier.
- **ws.js test adaptation:** Step 1 of Task 3 stubs the socket — read `ws.js`
  and match the real field/handler names (`this.ws`, the `onmessage` handler)
  before running; the rpc-promise plumbing already exists (`this.rpc`).
