# Subagent view — design

**Date:** 2026-07-08
**Status:** implemented (commits `4f9146e`…`7f61a70`)
**Scope:** group `Task`-tool subagent events into collapsible inline
"ventanitas" in both the TUI and the web transcript.

> Canonical Markdown. The aegis repo's house convention renders specs as
> self-contained HTML; an `.html` companion may be generated from this file
> with the `writing-specs-as-html` skill, but this `.md` is the source of
> truth.

## Problem

When Claude runs the `Task` tool it dispatches a subagent whose events
(assistant text, thinking, tool calls, plans) stream back interleaved with
the main agent's events. Claude tags every stream message with
`parent_tool_use_id`: `null` for the main agent, and the dispatching `Task`
tool_use's id for a subagent's events. aegis currently **drops** this field,
so subagent events are flattened inline — with parallel subagents (e.g. eight
at once) their events pile up indistinguishably.

We want each `Task(...)` call to open a **ventanita**: a collapsible inline
box that groups that subagent's events under the dispatching call.

## Approach

The `Task` tool_use block is the box **header**; the subagent's events
(routed by `parent_tool_use_id`) are the **body**; the `Task` tool_result
(the subagent's final return) folds in as the **footer** — composing with the
existing tool_use/tool_result fold (`8125861`).

One visual level of nesting. Collapsed by default with a live header;
expand on demand.

## 1. Data model (parser + persistence)

- **`events.py` parser** — read `obj["parent_tool_use_id"]` from the
  top-level stream message and set it on the emitted event.
- **Dataclasses** — add `parent_tool_use_id: str | None = None` to the event
  types that can appear inside a subagent: `AssistantText`,
  `AssistantThinking`, `ToolUse`, `ToolResult`, `AgentPlan`.
- **`state/event_codec.py`** — `encode_event` emits the key only when set;
  `decode_event` reads it (defaulting to `None`). Old persisted JSONL without
  the field decodes to `None` → renders inline exactly as today
  (backward-compatible).

The field must round-trip through the JSONL so replay/resume reconstructs
the grouping on both UIs.

## 2. Grouping logic (shared concept)

- A `ToolUse` whose name is a **subagent-dispatch tool** — matched against
  `{"Task", "Agent"}` (Claude Code has used both across versions; 2.1.x emits
  `Agent`) — **opens a container**, keyed by its own `tool_call_id`.
- An event with `parent_tool_use_id == T` routes into container `T`
  (appends to its child list; updates the live header).
- The `tool_result` for `T` folds in as the container's footer.
- **Graceful fallback** — an event whose `parent_tool_use_id` has no known
  container renders inline (as today). This covers deeper nesting
  (subagents spawning subagents) without breaking; we render a single visual
  level. In practice Claude subagents do not dispatch `Task`.

## 3. Render — TUI

- New widget `SubagentBox(Widget)` with a reactive `collapsed` (default
  `True`):
  - **Collapsed** — one live line:
    `🤖 <task summary> · ⣾ N events · <last action>` while running;
    `🤖 <task summary> · ✓ · N events · 4.3s` on completion
    (`✗` on error/interrupt).
  - **Expanded** — header + child renderables (indented, faint left
    border/tint) + a closing line with the result.
  - Toggle — click the header, or `Enter`/`space` when the box is focused.
- `pane.py` — `self._subagent_boxes: dict[str, SubagentBox]` maps a `Task`
  tool_call_id → its box. In `_on_core_event`: an event carrying a known
  `parent_tool_use_id` routes into that box (in-place update, mirroring
  `_fold_tool_result`); a `Task` `ToolUse` mounts a `SubagentBox` and
  registers it. `_mount_replay` reconstructs the same grouping over the
  coalesced stream.
- **Windowing** — a `SubagentBox` counts as **one** block in
  `_history` / `_mounted_blocks` (its children live inside), so a noisy
  subagent does not blow the mounted-window bound.

## 4. Render — Web

- **`coalesce.js`** — a frame with `parent_tool_use_id` is pushed onto the
  `.children` array of the parent `Task` `ToolUse` record (found by
  `tool_call_id`); returns `{action:"update", index:<parent>}`. The `Task`
  tool_result folds in as today.
- **`renderEvent.js`** — a `Task` `ToolUse` with `.children` renders
  `<div class="subagent" data-collapsed>`: a header, the children (each via
  `renderEvent`), and a footer.
- **`app.js`** — `renderInto` re-renders the parent node when children
  arrive (also refreshes the live header); a delegated click on
  `.subagent-header` toggles the `data-collapsed` attribute.
- **`base.css`** — `.subagent` styling: faint left border, indent, clickable
  header, body hidden under `[data-collapsed]`.

## 5. Edge cases

- **Parallel Tasks** — N `Task` tool_uses in one assistant message mount N
  adjacent boxes; interleaved children route by id. Covered by the design.
- **Interrupt mid-subagent** — the box ends `✗ interrupted`, incomplete. No
  special state.
- **Expand/collapse state** — UI-only, not persisted. On replay everything
  starts collapsed.

## 6. Testing

- **Parser** — `parent_tool_use_id` captured from the stream message.
- **Codec** — round-trips (present → encoded → decoded; absent → `None`).
- **TUI** — child events group into the correct box (parallel + out of
  order); an event with no known parent falls back inline; replay
  reconstructs boxes.
- **JS** — `coalesceInto` routes children onto `.children`; `renderEvent`
  produces the collapsible box; a `Task` without children stays a plain
  block.
- **Web protocol** — grouping survives a resume round-trip.

## 7. Scope / YAGNI

One visual nesting level. No sub-tabs, no side panel, no persisted collapse
state, no animations. Touches: `events.py`, `state/event_codec.py`,
`pane.py` (+ new `SubagentBox`), `coalesce.js`, `renderEvent.js`, `app.js`,
`base.css`, and their tests.

## 8. Driver applicability (claude-only today)

The grouping key `parent_tool_use_id` is populated **only** by the Claude
stream-json parser (`events.py::parse`), which reads Claude's own
`parent_tool_use_id` stream field. Other drivers:

- **ACP drivers** (`AcpDriver`, and its `GeminiDriver` / OpenCode shims) build
  events directly from ACP `session_update` notifications
  (`ToolCallStart` / `ToolCallProgress` / message chunks). Those notifications
  carry `tool_call_id` but **no parent linkage**, so `parent_tool_use_id`
  stays `None` and every event renders **flat inline** — identical to
  pre-feature behavior. Zero regression.
- The tool-use ↔ tool-result **fold** (paired by `tool_call_id`) *does* apply
  to ACP, since ACP sets `tool_call_id` on both.

Because the grouping key lives in the shared event model (not the Claude
parser), ACP subagent grouping would light up for free if a future ACP
version exposes subagent parentage: set `parent_tool_use_id` when
constructing events in `_AegisAcpClient` — no render changes needed.
