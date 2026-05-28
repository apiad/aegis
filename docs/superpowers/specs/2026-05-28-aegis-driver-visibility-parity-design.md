---
date: 2026-05-28
type: design
status: draft
related:
  - .playground/acp-visibility/FINDINGS.md
  - .playground/acp-visibility/PARITY.md
  - docs/superpowers/specs/2026-05-20-aegis-acp-drivers-design.html
---

# Driver visibility parity — design

## Motivation

aegis ships three drivers (claude stream-json, gemini --acp, opencode
acp). Each underlying CLI streams a rich, real-time view of what the
model is doing: thinking, tool selection, file edits, plan revisions,
mid-turn token usage. Today's canonical event surface
(`src/aegis/events.py:7-67`) carries roughly **2 fields per event**;
the underlying streams expose **10–15 each**. The mapping today drops
most of it on the floor.

Two probes in `.playground/acp-visibility/` characterized the gap:

- `FINDINGS.md` — what ACP exposes that aegis ignores.
- `PARITY.md` — the same audit for claude, with both sides lined up to
  identify what every substrate gives us in common (the "LCD") and
  what each one gives extra.

The headline:

- **2 bugs.** ACP's `is_error=False` is hardcoded
  (`drivers/acp.py:151`); failed tools render as ok. Gemini token
  counts read from `PromptResponse.usage`, but gemini puts them in
  `field_meta.quota.token_count` instead — every gemini turn shows
  0/0 tokens.
- **1 perceived-quality cliff.** OpenCode streams chain-of-thought
  one token per `AgentThoughtChunk` (~116 events per turn). aegis
  renders each as a separate italic line.
- **11 LCD signals both substrates populate but aegis discards** —
  tool kind, locations, structured input, structured output, file
  diffs, plan/todos, message_id, stop_reason, cost_usd, per-model
  attribution, available commands. See PARITY.md §"The genuine common
  denominator".

This spec sets the canonical event shape we want post-parity, defines
the driver mapping rules that get us there, and orders the work into
shippable vertical slices.

## Goals

1. Every event in `aegis.events` carries the union of signals both
   substrates populate. Substrate-specific extras ride as optional
   fields on the same events; the renderer treats absence as "render
   the LCD view."
2. Real-time visibility while a turn is running, not just at the end:
   what tool is the model invoking right now, on which file, with
   what args, is it succeeding or failing, what's the running plan,
   how much context has been consumed.
3. No regression in claude rendering. The current TUI/Telegram look
   for claude sessions stays at least as good; the same renderer
   produces equivalent output for the ACP drivers.
4. Persistence (`state/event_codec.py`) remains backwards-compatible:
   old session logs still decode; new fields are optional with
   sensible defaults.

## Non-goals

- Rewriting the driver seam. `HarnessDriver` / `HarnessSession`
  (`drivers/base.py:1-53`) stays. All changes are inside
  `events.py`, `render.py`, the two driver files, and
  `state/event_codec.py`.
- Adding new CLI tools or harness types. Codex and Copilot ACP land
  on the same canonical events.
- Re-flowing the TUI. Pane layout
  (`tui/pane.py:42,506`) stays; only `render_event`'s output grows
  richer.
- Building permission-prompt UI for ACP `request_permission` (queue
  workers + auto-allow stays; richer permission UX is a separate
  spec).

## Canonical event surface (target)

Every additive field is **optional with a defaulting fallback**, so
the existing claude paths and the existing tests continue to pass
unchanged while drivers populate fields opportunistically.

```python
# src/aegis/events.py

@dataclass(frozen=True)
class TokenUsage:
    input: int
    cache_creation: int
    cache_read: int
    output: int
    # NEW — optional, defaults to 0; ACP-gemini/opencode populate.
    thinking: int = 0

@dataclass(frozen=True)
class CostUsage:                  # NEW dataclass
    amount_usd: float | None
    context_used: int | None
    context_size: int | None

@dataclass
class SystemInit:
    session_id: str | None
    # NEW — all optional, claude populates from system.init,
    # ACP from initialize() + AvailableCommandsUpdate.
    model: str | None = None
    permission_mode: str | None = None
    version: str | None = None
    available_commands: tuple[str, ...] = ()

@dataclass
class AssistantText:
    text: str
    usage: TokenUsage | None = None
    # NEW — chunk aggregation key.
    message_id: str | None = None

@dataclass
class AssistantThinking:
    text: str
    usage: TokenUsage | None = None
    # NEW — chunk aggregation key.
    message_id: str | None = None

@dataclass
class ToolUse:
    name: str
    summary: str
    usage: TokenUsage | None = None
    # NEW — substrate-aligned semantics.
    kind: str | None = None
    raw_input: dict | None = None
    tool_call_id: str | None = None
    locations: tuple[tuple[str, int | None], ...] = ()
    status: str | None = None

@dataclass
class ToolResult:
    text: str
    is_error: bool
    # NEW — substrate-aligned semantics.
    tool_call_id: str | None = None
    kind: str | None = None
    diff: tuple[str, str, str] | None = None   # (path, old_text, new_text)
    raw_output: dict | None = None
    is_image: bool = False

@dataclass
class AgentPlan:                  # NEW event type
    entries: tuple["PlanEntry", ...]

@dataclass(frozen=True)
class PlanEntry:
    content: str
    status: str                   # pending / in_progress / completed
    priority: str = "medium"      # high / medium / low

@dataclass
class ContextUpdate:              # NEW event type — mid-turn telemetry
    cost: CostUsage | None = None
    mode: str | None = None
    title: str | None = None

@dataclass
class Result:
    duration_ms: int | None
    is_error: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    usage: TokenUsage | None = None
    # NEW — all optional.
    stop_reason: str | None = None
    ttft_ms: int | None = None
    num_turns: int | None = None
    cost_usd: float | None = None
    model_usage: tuple[tuple[str, TokenUsage], ...] = ()
    permission_denials: tuple[str, ...] = ()

@dataclass
class Unknown:
    raw: str

Event = (
    SystemInit | AssistantText | AssistantThinking
    | ToolUse | ToolResult | AgentPlan | ContextUpdate
    | Result | Unknown
)
```

Wherever the current dataclasses use mutable defaults (e.g. lists),
the new fields use tuples so the frozen / hashable invariants stay
consistent across the codebase. `state/event_codec.py` round-trips
each new field as the natural JSON shape; missing keys decode to the
default.

## Per-driver mapping

### Claude (`src/aegis/events.py:120` `parse`)

| Stream event | Canonical event | Field source |
|---|---|---|
| `system.init` | `SystemInit` | `model` ← `system.init.model`; `permission_mode` ← `permissionMode`; `version` ← `claude_code_version`; `available_commands` ← `[c["name"] for c in slash_commands]` |
| `assistant.content[type=text]` | `AssistantText` | `message_id` ← `assistant.message.id`; `usage` ← `message.usage` |
| `assistant.content[type=thinking]` | `AssistantThinking` | `message_id` ← `assistant.message.id` |
| `assistant.content[type=tool_use]` (`TodoWrite`) | `AgentPlan` | `entries` ← `[PlanEntry(t["content"], t["status"]) for t in input["todos"]]`; priority defaults to `"medium"` (claude doesn't expose) |
| `assistant.content[type=tool_use]` (everything else) | `ToolUse` | `kind` ← derived via name→kind table below; `raw_input` ← `input`; `tool_call_id` ← `id`; `locations` ← `[(input["file_path"], None)]` when present |
| `user.content[type=tool_result]` | `ToolResult` | `tool_use_id` ← `tool_use_id`; `raw_output` ← `tool_use_result`; `kind` looked up via id in a small in-parser cache; `diff` ← `(path, old, new)` when the corresponding ToolUse was `Edit`/`Write` |
| `result` | `Result` | `stop_reason` ← `result.stop_reason`; `ttft_ms` ← `result.ttft_ms`; `num_turns` ← `result.num_turns`; `cost_usd` ← `result.total_cost_usd`; `model_usage` ← `result.modelUsage`; `permission_denials` ← `result.permission_denials` |

Claude name→kind table:

| Tool name | kind |
|---|---|
| `Read` | `read` |
| `Bash`, `BashOutput`, `KillShell` | `execute` |
| `Edit`, `Write`, `NotebookEdit` | `edit` |
| `Glob`, `Grep` | `search` |
| `WebFetch`, `WebSearch` | `fetch` |
| `Task`, `Agent` | `think` |
| `TodoWrite` | (not a ToolUse — emitted as `AgentPlan`) |
| (anything else) | `other` |

The `parse()` function becomes lightly stateful (in a per-`parse`
call? No — per-session). It needs to remember each `tool_use.id` so
the matching `tool_result` can attach `kind` and `diff`. The natural
home is a small `ParserState` object that `ClaudeSession` keeps and
threads into `parse(line, state=...)`. This change is local — only
`drivers/claude.py:65-80` (the pump) calls `parse`, plus the unit
tests.

### ACP (`src/aegis/drivers/acp.py:125-153` `_AegisAcpClient.session_update`)

| ACP notification | Canonical event | Field source |
|---|---|---|
| `AgentMessageChunk` | `AssistantText` | `text` ← `update.content.text`; `message_id` ← `update.message_id` |
| `AgentThoughtChunk` | `AssistantThinking` | same |
| `ToolCallStart` | `ToolUse` | `name` ← `title`; `kind` ← `kind`; `tool_call_id` ← `tool_call_id`; `raw_input` ← `raw_input`; `locations` ← `[(l.path, l.line) for l in locations]`; `status` ← `status`; `summary` derived from raw_input similar to current `_summarize_tool` |
| `ToolCallProgress` (`status` ∈ {completed, failed}) | `ToolResult` | `text` ← join of completion `content[].content.text`; `is_error` ← `status == "failed"`; `tool_call_id` ← `tool_call_id`; `kind` ← lookup; `raw_output` ← `raw_output`; `diff` ← `(b.path, b.old_text or "", b.new_text)` for the first `FileEditToolCallContent` in `content` |
| `ToolCallProgress` (`status` == in_progress) | (in slice 1: dropped; in slice 4: a future `ToolProgress` event) | — |
| `AgentPlanUpdate` | `AgentPlan` | `entries` ← `[PlanEntry(e.content, e.status, e.priority) for e in update.entries]` |
| `UsageUpdate` | `ContextUpdate` | `cost = CostUsage(amount_usd=update.cost.amount, context_used=update.used, context_size=update.size)` |
| `CurrentModeUpdate` | `ContextUpdate` | `mode` ← `update.current_mode_id` |
| `SessionInfoUpdate` | `ContextUpdate` | `title` ← `update.title` |
| `AvailableCommandsUpdate` | (cached on session; surfaced via a deferred `SystemInit` patch — see Open Questions) | — |
| `ConfigOptionUpdate` | (dropped) | — |

PromptResponse mapping (`drivers/acp.py:381-405`):

- `Result.stop_reason` ← `resp.stop_reason` (full enum).
- `Result.is_error` ← `resp.stop_reason not in ("end_turn", None)`.
- `Result.usage`: try `resp.usage` first; **on `None`, fall back to
  `resp.field_meta["quota"]["token_count"]`** (Gemini's home for
  this).
- `Result.model_usage` ← `resp.field_meta["quota"]["model_usage"]`
  when present (Gemini).
- `Result.cost_usd` ← last `UsageUpdate.cost.amount` observed during
  the turn.

### Substrate-conditional rendering

`render.py` becomes the union renderer. It uses the new fields when
they're present and falls back gracefully when they're not. Concrete
fallbacks:

- `ToolUse.kind` missing → no icon, current `⏺` glyph.
- `ToolUse.locations` empty → use `ToolUse.summary` for the path hint.
- `ToolResult.diff` missing → current ok / error first-line behavior.
- `AgentPlan` always rendered identically across drivers.
- `ContextUpdate` always rendered identically; status bar absorbs it.

No code path branches on driver type. Driver-specific behavior is
expressed as field presence/absence.

## Renderer changes (`src/aegis/render.py`)

Function `render_event(ev, colors)` grows:

- A `_KIND_ICON` dict (read 📖 / edit ✏️ / execute ⌬ / search 🔎 /
  think ✻ / fetch 🌐 / move ➡️ / delete 🗑 / switch_mode 🔄 /
  other ⏺).
- A `_pathhint(ev)` helper that picks the shortest unique tail from
  `ev.locations[0]` (e.g. `.../target.txt:42`), falling back to
  `ev.summary`.
- A `_render_diff(diff, colors)` helper producing a 3-line unified
  preview (max 3 added + 3 removed lines, then `… N more lines`),
  styled with `colors.ok` / `colors.err` gutters.
- A new `render_event` branch for `AgentPlan`: fenced block with
  status glyphs (○ pending, ◐ in_progress, ● completed) and
  priority colorization.
- A new `render_event` branch for `ContextUpdate`: returns `None`
  (status-bar absorbs these — they're not pane content). The pane
  observer in `tui/pane.py:42,506` already handles `None` by
  skipping.

Pane / status-bar (`tui/widgets.py`, `tui/state.py`) consume
`ContextUpdate` separately for the context-fill badge — wired in a
later slice, not slice 1.

## Persistence (`src/aegis/state/event_codec.py`)

Each new field is added to the encode / decode functions with default
fallbacks. Existing serialized records still decode (missing keys →
defaults). Roundtrip tests in `tests/test_state_event_codec.py` grow
one new case per added field; existing ones keep passing because the
field defaults match the pre-extension shape.

`AgentPlan` and `ContextUpdate` get their own `t` tags
(`"AgentPlan"`, `"ContextUpdate"`) and the matching encode/decode
branches.

## Vertical slices

Ordered so each slice is independently shippable and the most-visible
wins come first. Estimates are for a Claude-Code-with-Alex pace, not
human team-weeks.

### Slice 1 — Legible tool calls everywhere (recommended first)

Detail in the companion plan
`docs/superpowers/plans/2026-05-28-aegis-driver-visibility-slice1.md`.

Scope summary:

- Bug fix A: `drivers/acp.py:151` — derive `is_error` from `status`.
- Bug fix B: `drivers/acp.py:381-405` — Gemini `usage` fallback to
  `field_meta.quota.token_count`.
- Extend `ToolUse` with `kind`, `tool_call_id`, `raw_input`,
  `locations`, `status` (all optional, defaults preserve current
  behavior).
- Extend `ToolResult` with `tool_call_id` and `kind`.
- Claude `parse()` populates these from `tool_use` / `tool_result`
  events, using a tiny per-session `ParserState` to correlate
  `tool_use.id` → kind.
- ACP `_AegisAcpClient.session_update` populates these from
  `ToolCallStart` / `ToolCallProgress`.
- `render.py` grows `_KIND_ICON` and `_pathhint`, threads them
  through `ToolUse` and `ToolResult` rendering.
- `state/event_codec.py` encodes/decodes the new fields with
  defaults.
- Test deltas in `test_events.py`, `test_drivers_acp.py`,
  `test_render_event.py`, `test_state_event_codec.py`.

Estimated effort: **4–5 hours** TDD-style.

Out of scope for slice 1: diff rendering, plan blocks, chunk
aggregation, context-fill badge. Each gets its own follow-on slice.

### Slice 2 — Thought / text chunk aggregation by `message_id`

Adds `message_id` to `AssistantText` / `AssistantThinking`. Driver
side: claude populates from `assistant.message.id`; ACP from chunk
`message_id`. Renderer side: a small per-pane buffer in
`tui/pane.py` that coalesces consecutive same-message_id chunks into
a single growing Markdown/italic block before display, flushing on
message_id change, non-chunk event, or turn end. Cosmetic for
claude (one block per turn); decisive for opencode (116 chunks → 1
block).

Estimated effort: **3 hours**.

### Slice 3 — Plan blocks (`AgentPlan`)

Canonical `AgentPlan` event + `PlanEntry`. Claude side: intercept
`TodoWrite` tool_use in `parse()`. ACP side: map
`AgentPlanUpdate`. Renderer: fenced block with status glyphs and
priority colorization, replacing any prior `AgentPlan` from the same
turn in the pane (entries arrive cumulative, not delta). Codec
updates.

Estimated effort: **4 hours**.

### Slice 4 — Diff rendering for edits

`ToolResult.diff` field + `_render_diff` helper. Claude side: at
`ToolResult` time, the per-session `ParserState` (from slice 1) looks
up the matching `ToolUse`'s input to synthesize the diff. ACP side:
extract `FileEditToolCallContent` from `ToolCallProgress.content`.
Codec updates.

Estimated effort: **3 hours**.

### Slice 5 — Result enrichment

`Result.stop_reason` (full enum, not just is_error), `ttft_ms`,
`num_turns`, `cost_usd`, `model_usage`, `permission_denials`. Wires
into `SessionMetrics` (`tui/metrics.py`) and the status bar.

Estimated effort: **3 hours**.

### Slice 6 — Mid-turn `ContextUpdate`

ACP-only emit. Status-bar badge in `tui/widgets.py` consumes the
`cost` / `mode` / `title` fields. Per-pane observer in `tui/pane.py`
forwards to the status bar.

Estimated effort: **2 hours**.

### Slice 7 — SystemInit enrichment (commands, model, permission, version)

Claude: from `system.init`. ACP: stash `InitializeResponse` fields
plus the first `AvailableCommandsUpdate` and emit a deferred
`SystemInit` patch once both have arrived (or emit a follow-on
`ContextUpdate` carrying the commands when the ACP side surfaces
them late). Mostly book-keeping; surfaces in `/help` and future
slash-command picker.

Estimated effort: **3 hours**.

**Cumulative budget: ~22 hours / about 3 days of paired
implementation.** Slices 1–3 deliver ~80% of the perceived quality
jump in ~11 hours.

## Test strategy

`tests/test_events.py` (claude parser): one new assertion per new
field per existing test case. New cases for `TodoWrite` → `AgentPlan`,
`tool_result.tool_use_id` correlation, name→kind table.

`tests/test_drivers_acp.py` (hermetic ACP stub agents): new stub
fixtures that emit each notification type with rich payloads
(populated kind, locations, raw_input, FileEditToolCallContent in
content, AgentPlanUpdate). Assert the produced events carry the
expected fields. The existing stub-script pattern (`_STUB_OK` at
`tests/test_drivers_acp.py:30`) extends naturally.

`tests/test_render_event.py`: assertions that the kind icon appears,
the pathhint appears, the diff renders the expected gutters, the plan
renders the expected glyphs. Driver-agnostic — just calls
`render_event` with synthesized events.

`tests/test_state_event_codec.py`: roundtrip tests per new field with
both populated and default values.

`tests/test_drivers_multiprovider_live.py` (live, marker-gated): one
new assertion per provider that an end-to-end turn produces ToolUse
events with non-empty `kind` and `locations`.

The fast hermetic suite (`uv run pytest -q -m "not live"`) stays
green at every commit boundary. Live tests are exercised opportunistically.

## Open questions

1. **`AvailableCommandsUpdate` arrives after `SystemInit` in ACP.**
   Two choices: (a) buffer the `SystemInit` emission until first
   `AvailableCommandsUpdate` or a short timeout; (b) emit `SystemInit`
   immediately and follow with a `ContextUpdate` carrying the
   commands. Option (b) is mechanical and keeps `SystemInit`
   semantically "the first thing in a session." Recommendation: (b).
2. **Streaming aggregation on the driver side vs renderer side.**
   `message_id` aggregation could happen in the driver (collapse
   chunks before they hit the canonical stream) or in the renderer
   (chunks pass through, the TUI coalesces). Renderer-side keeps the
   stream finely-grained for observers / persistence; driver-side
   means fewer events on disk. Recommendation: renderer-side, because
   `session_log.py` would otherwise lose the natural debugging
   granularity.
3. **Per-tool diff derivation for Claude `Edit`/`Write`.** The
   ToolResult is the natural moment to emit `diff`, but the
   `old_string` / `new_string` come from the prior `ToolUse.input`.
   The `ParserState` cache solves this; cap the cache at the N most
   recent tool_use_ids to bound memory.
4. **`AgentPlan` event ordering.** ACP `AgentPlanUpdate` arrives
   between text chunks; claude's `TodoWrite` arrives as a tool_use
   inline with the assistant message. Both should emit `AgentPlan` at
   the same logical moment (between text blocks). Pane rendering
   should treat an `AgentPlan` mid-turn the same way it treats the
   chunk-after-it: collapse any earlier `AgentPlan` from the same
   turn so only the latest entries display.
5. **Backward-compat invariants.** The frozen `TokenUsage` gains a
   field with default 0 — this is safe for equality semantics
   because pre-existing instances reconstruct with `thinking=0`. The
   non-frozen dataclasses gain fields with defaults — safe.
6. **The hardcoded `event_codec.py` keyset.** New fields are added
   with `.get(key, default)` on decode to keep old persisted records
   loadable. The `state_event_codec` tests cover this.
