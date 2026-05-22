---
title: Shared Canvas — Blackboard Coordination Primitive
date: 2026-05-21
status: draft
---

# Shared Canvas

## Goal

Give aegis agents a third coordination primitive (alongside queues
and inboxes): a **shared mutable artifact** that multiple agents can
read, write, and subscribe to. When one agent writes, every other
subscriber wakes up with a notification carrying the diff.

The target use case: a markdown report multiple agents shape
incrementally — PM owns the intro, researcher fills in the data
section, copy-editor passes over the whole thing. None of them
polls; each reacts when relevant content changes.

## The blackboard

This is the classical [blackboard
pattern](https://en.wikipedia.org/wiki/Blackboard_(design_pattern)) —
shared structure + specialists + event-driven coordination, formalized
by Hearsay-II in the 70s. Aegis already has the wake mechanism
(inboxes); the canvas is the shared structure.

Three coordination primitives, composed:

| Primitive | Verb | Wake trigger |
|---|---|---|
| Queue | "do this, tell me when done" | Worker completes |
| Inbox / handoff | "wake — message for you" | Sender posts |
| **Canvas** | "wake — shared state changed" | Subscriber writes |

The canvas reuses the inbox plumbing for notifications: a canvas write
becomes an `InboxMessage` to every subscriber with sender
`canvas:<name>`. The same `✉` block already used for handoffs and
queue callbacks renders the notification. Zero new TUI.

## Hard rules

- **File-backed.** Every canvas is a real markdown file on disk —
  greppable, version-controllable, openable in Alex's editor. The
  canvas is the file; aegis is the choreographer.
- **Markdown only.** Section structure comes from `##` headings.
  HTML / JSON / binary canvases out of scope.
- **No ownership.** Any subscriber can write any section. The ledger
  records `who_wrote: <handle>` per write but enforces nothing. Trust
  the agents; surface conflicts loudly via notifications.
- **Created on demand via MCP.** No `canvases = {...}` in
  `.aegis.py`. An agent calls `aegis_canvas_open(name, file)`; the
  substrate creates the file if missing.

## The artifact

### Section model

Sections are top-level `## headings`. Each section is the unit of
write and the unit of notification.

```markdown
# Q3 Report

Some preamble text before any heading.

## Intro
…intro body…

## Data
…data body…

## Recommendation
…recommendation body…
```

- The body between the H1 (or file start) and the first `##` is the
  implicit **`_preamble`** section.
- If the file has *no* `##` headings, the entire body is a single
  implicit **`body`** section.
- Nested `###`+ headings are content *within* their parent section.
  Don't try to be clever about deep trees in v1.

### Section naming

- Agents pass section names without the `##` prefix:
  `write_section("report-q3", "data", ...)`.
- Case-sensitive, exact match.
- Allowed chars: alphanumeric, dash, underscore, space. No `/`, `:`,
  newlines, leading/trailing whitespace.

### Canvas identity

A canvas has a **name** (logical id, used in MCP calls) and a **file
path** (where it lives on disk). Names must be unique within a
project. File paths can be anywhere the aegis process can write.

## MCP surface

| Tool | Args | Returns |
|---|---|---|
| `aegis_canvas_open` | `name`, `file` (optional on re-open) | metadata: `{name, file, sections: [{name, lines, last_writer, updated_at}], created_at}` |
| `aegis_canvas_read` | `name`, `section` (optional) | full content or one section |
| `aegis_canvas_write_section` | `name`, `section`, `content` | new section metadata |
| `aegis_canvas_append_to_section` | `name`, `section`, `text` | new section metadata |
| `aegis_canvas_subscribe` | `name`, `sections` (optional filter) | ack with current subscriber list |
| `aegis_canvas_unsubscribe` | `name` | ack |
| `aegis_canvas_list` | — | all canvases in this project + subscriber counts |

### Behaviors

- **`open`** creates the canvas if it doesn't exist (empty file). Idempotent — opening an existing canvas just returns its metadata. The first `open` requires the `file` arg; subsequent opens reuse the registered path.
- **`write_section`** replaces the section's body. If the section doesn't exist, it's appended to the end of the file. Returns the new metadata.
- **`append_to_section`** appends to existing section content (newline-joined). Cheaper for log-style growth. Creates the section if missing.
- **`subscribe`** with `sections=None` means all sections; with a list, only those sections trigger notifications. Subscription lives for the agent session's lifetime — drops automatically when the session closes. No persistence across restarts in v1.
- **`list`** lets one agent discover what canvases other agents have created.

### Concurrency

The substrate holds one async lock per canvas. Reads and writes
serialize. Two agents writing the same section race; last to acquire
the lock wins, and the loser's write is recorded too in the ledger
(`overwritten_by: <handle>` annotation on its ledger row).

## Notification model

Every write fires an `InboxMessage` to every subscriber **except the
writer themselves**. The message renders as the existing `✉` block:

```
✉ from canvas:report-q3 · section "data" · 2026-05-21T20:30:00Z
  written by researcher (+18 / -3 lines)
  ──
  ## Data
  Q3 numbers came in stronger than projected. Revenue up 14% YoY
  driven by enterprise tier expansion. Net new logos hit 47 …
  … (5 more lines)
```

### Notification payload structure

The inbox message body carries:
- **Header**: `from canvas:<name> · section "<section>" · <iso-ts>`
- **Byline**: `written by <writer-handle> (+<added>/-<removed> lines)`
- **Preview**: first ~6 lines of the new section content; if the
  section is longer, append `… (N more lines)`.
- **Operation**: `write_section` produces full preview;
  `append_to_section` shows only the appended text with byline
  `appended by <writer-handle> (+<N> lines)`.

The diff math is line-based: `added = len(new.splitlines())`,
`removed = len(old.splitlines())`. Reported as signed.

### Subscription filtering

`subscribe(name, sections=["data"])` means this agent only wakes for
writes to `data`. Filter is matched against the writer's `section`
arg verbatim — no globs, no implicit catch-all.

### Batching

Inbox delivery already buffers when a session is mid-turn (see
`AgentSession.deliver` / `_chain_if_pending`). A flurry of canvas
writes to a mid-turn subscriber becomes a single chained turn with
all `✉` blocks rendered in order. Idle subscribers get immediate
single-write turns.

## State layout

```
.aegis/state/canvases/
  <name>/
    meta.json              # {name, file, created_at, created_by}
    ledger.jsonl           # one append per write: {ts, writer, section, op, +lines, -lines}
```

Subscriber lists live **in memory only** — they're session-scoped.
On aegis restart, agents must re-subscribe. (Canvases themselves
persist; the file + meta + ledger are all on disk.)

The on-disk markdown file is the canvas content — not duplicated in
state. State is the side-channel for tracking *who did what to what*.

## Worked example

PM agent shaping a Q3 report with help from researcher and copy-editor:

```
# PM agent
aegis_canvas_open(name="report-q3", file="vault/reports/q3.md")
aegis_canvas_subscribe(name="report-q3")           # wake on any change
aegis_canvas_write_section(
    name="report-q3", section="intro",
    content="Q3 was a quarter of consolidation…")
# Hands off to researcher via aegis_handoff
aegis_handoff(to="researcher", text="fill the data section of canvas report-q3")

# researcher agent (woken by handoff)
aegis_canvas_open(name="report-q3")
aegis_canvas_subscribe(name="report-q3", sections=["data"])
aegis_canvas_write_section(
    name="report-q3", section="data",
    content="Q3 numbers came in stronger…")
# Done; returns.

# PM agent wakes with:
✉ from canvas:report-q3 · section "data" · 2026-05-21T20:30:00Z
  written by researcher (+18 / -3 lines)
  ──
  ## Data
  Q3 numbers came in stronger…
# PM reads and hands off to copy-editor for the full pass.
```

If copy-editor rewrites 5 sections quickly, PM gets one chained turn
with 5 `✉` blocks (because PM was mid-turn when they landed) or 5
separate turns (if PM was idle between each).

## Edge cases

| Situation | Behavior |
|---|---|
| `open` on missing file | Create empty file, register canvas. |
| `open` with new `file` arg on already-registered name | Error — name already bound to a different file. |
| `read` before `open` | Error — `canvas not opened`. |
| `write_section` to a section name with disallowed chars | Reject with error. |
| `write_section` to `_preamble` or `body` | Treated like any other section; `_preamble` always writes pre-first-`##`; `body` only valid when file has no `##` (else error). |
| File deleted externally between writes | Next op fails loudly; agent gets error; canvas state is not auto-cleaned. |
| Two agents write the same section concurrently | Lock serializes; both ledger rows written; the loser's row tagged `overwritten_by`. Both notifications fire. |
| Self-write notification suppressed | Writer doesn't get an `✉` for their own write — they already know. |
| Subscribed agent's session closes | Subscription drops silently. No persistence across restart. |

## Out of scope (deferred)

- **Section ownership / enforcement.** Soft warnings or hard rejects
  when non-owners write. Add when a real workflow demands it.
- **External-edit detection.** Alex editing the file in his editor
  between aegis writes won't fire notifications. Document the
  constraint; add a file watcher later if it bites.
- **Cross-format canvases.** HTML, JSON, structured data with
  schemas — different decomposition rule per format. Markdown only
  for v1.
- **Schema-first canvases.** `open(name, sections=["a", "b", "c"])`
  with fixed-set enforcement. Ad-hoc is the v1 model.
- **TUI surface.** No dashboard, no peek-modal, no canvas tab. Alex
  reads the file in his editor of choice.
- **Subscription persistence.** Across aegis restarts, agents must
  re-subscribe. Acceptable because subscriptions are session-scoped
  by nature.
- **Predicate subscriptions.** `subscribe_when(predicate)` —
  wake-on-keyword, wake-on-line-count. Filter-by-section is the only
  v1 predicate.
- **Locks beyond per-canvas serialization.** No long-held
  `lock_section` for "I'm working here." Race risk is on the agents.

## Testing

Unit tests cover:

- Section parsing: split a markdown body into `(_preamble, [(name,
  content)…])`; round-trip without loss; handle no-`##` files
  (single `body` section); handle multi-line section bodies; handle
  pre-`##` preamble.
- Write/append semantics: replace creates section if missing; append
  joins with newline; both update the ledger; both fire correct
  notifications; bad section names rejected.
- Notification math: `+added/-removed` line counts; preview truncation
  + `… (N more lines)` footer; self-writes suppressed; filtered
  subscriptions only fire for matching sections.
- Concurrency: simulate two concurrent writes to the same section;
  assert both ledger rows; assert the loser is tagged
  `overwritten_by`; assert both subscribers each get one
  notification.
- State: `meta.json` and `ledger.jsonl` shapes; missing-file
  resilience on `read`.
- MCP integration: each tool through the existing MCP server,
  request/response shape verified against the schema.

Integration test: two `FakeAgent` sessions, both subscribed to a
canvas. Agent A writes a section; assert Agent B receives the inbox
message with the right body. Agent B writes back; Agent A wakes
similarly. Cross-firing works through the real `InboxRouter`.

## Implementation order

Five vertical slices, each a working slice through the substrate.

1. **Section parser + writer (pure).** A `Canvas` module that takes
   markdown text, exposes section read/write/append over an in-memory
   model, round-trips back to markdown. No I/O, no MCP.
2. **CanvasManager + on-disk state.** `.aegis/state/canvases/<name>/`
   substrate: meta.json, ledger.jsonl. `open`, `read`, `write_section`,
   `append_to_section`, `list` operations over real files. Per-canvas
   async lock. Subscribers in-memory list. No notifications yet.
3. **Notifications via inbox.** On every write, build an
   `InboxMessage` with the documented body and deliver to each
   subscriber via `InboxRouter`. Honor self-write suppression and
   subscription filtering.
4. **MCP tools.** Wire the six operations into the existing MCP
   server. Each tool is a thin wrapper over `CanvasManager`. Validate
   args, format responses.
5. **End-to-end integration.** Two-agent test through the real
   `InboxRouter` and the real MCP server; manual smoke with a
   `claude` session driving the canvas via tool calls. Docs (README
   section + `docs/canvas.md`) + CHANGELOG.
