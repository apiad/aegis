---
date: 2026-05-28
type: plan
status: shipped
spec: docs/superpowers/specs/2026-05-28-aegis-driver-visibility-parity-design.md
slice: 1 — Legible tool calls everywhere
landed: 3f6772b → 763e1b6
---

# Slice 1 plan — Legible tool calls everywhere

The thinnest vertical slice that makes every tool call in every driver
informative: semantic kind icon, path hint, structured input retained,
proper success/failure styling. Two bug fixes ride along because they
share the touch zone.

After this slice:

- A `Bash(command="echo hi", description="Print hi")` call on Claude
  renders as `⌬ Bash(echo hi)`, not `⏺ Bash(echo hi)` (icon by kind).
- A `Read(file_path=".../target.txt")` on opencode/gemini renders as
  `📖 read(.../target.txt)`, not `⏺ read(read)`.
- A failed gemini `kind=edit status=failed` lands as a red
  `└ error <reason>` line, not green `└ ok`.
- Gemini turn metrics show real token counts instead of 0/0.

No new event types, no chunk aggregation, no plan blocks, no diff
rendering. Those each get their own slice.

## Vertical slice 1 = the thinnest end-to-end path

The minimum end-to-end testable change is: a synthesized
`ToolUse(name="Read", kind="read", locations=(("foo.py", None),))`
plus a synthesized `ToolResult(is_error=True, kind="read")` render
through `render_event` with the expected icon and red styling, AND
the same flows from a real claude stream-json fixture line and from a
hermetic ACP stub agent. Each commit boundary keeps
`uv run pytest -q -m "not live"` green.

## Pre-flight: pin real symbols

Before any code change, grep current `main` to confirm everything the
plan references hasn't moved since this doc was written. Each task
below names a file:line — re-grep at task start if the head moves.

```bash
cd repos/aegis
uv run pytest -q -m "not live"        # baseline green
grep -n "^class\|^def parse" src/aegis/events.py
grep -n "session_update\|_AegisAcpClient" src/aegis/drivers/acp.py
grep -n "render_event\|_TOOL_SUMMARY_KEY" src/aegis/render.py src/aegis/events.py
grep -n "^def encode_event\|^def decode_event" src/aegis/state/event_codec.py
```

## Tasks

Each task is one logical unit. Commit at every task boundary, with
the test added before the implementation per the repo TDD convention
(`AGENTS.md` §Conventions). Work on `main` per the personal-workspace
rule for aegis.

### T1 — Bug fix: ACP `is_error` derived from status

**Touches:** `src/aegis/drivers/acp.py:140-152`.

**Test first:** add `test_acp_failed_tool_marks_error` to
`tests/test_drivers_acp.py`. Use a new stub script (modeled on
`_STUB_OK` at line ~30) that emits `ToolCallStart` followed by
`ToolCallProgress` with `status="failed"` and a single
`ContentToolCallContent` carrying the error text. Assert the produced
event is `ToolResult` with `is_error=True` and the text payload.

**Implementation:** in `_AegisAcpClient.session_update`, change the
`elif kind == "ToolCallProgress":` branch to:

```python
elif kind == "ToolCallProgress":
    status = getattr(update, "status", "")
    if status in ("completed", "failed"):
        is_error = status == "failed"
        text = ""
        for block in (update.content or []):
            inner = getattr(block, "content", None)
            if inner is not None:
                candidate = getattr(inner, "text", "")
                if candidate:
                    text = candidate
        self._queue.put_nowait(
            ToolResult(text=text, is_error=is_error))
```

**Verify:** new test passes; existing
`test_acp_completed_tool_yields_tool_result` (or whatever its current
name is — grep) still passes.

**Commit:** `fix(drivers/acp): derive ToolResult.is_error from status==failed`

### T2 — Bug fix: Gemini PromptResponse usage fallback

**Touches:** `src/aegis/drivers/acp.py:381-405` (the usage extraction
block in `AcpSession.send`).

**Test first:** add `test_acp_send_uses_field_meta_quota_fallback`
to `tests/test_drivers_acp.py`. Stub a `prompt` response that returns
`PromptResponse(stop_reason="end_turn", usage=None,
field_meta={"quota": {"token_count": {"input_tokens": 100,
"output_tokens": 50}, "model_usage": [...]}})`. Assert the emitted
`Result.usage` reflects 100 / 50 (not 0 / 0).

**Implementation:** after the existing `u = getattr(resp, "usage",
None)` line, add a fallback:

```python
if u is None:
    fm = getattr(resp, "field_meta", None) or {}
    tc = ((fm.get("quota") or {}).get("token_count")) or {}
    if tc:
        in_tok = int(tc.get("input_tokens") or 0)
        out_tok = int(tc.get("output_tokens") or 0)
        usage = TokenUsage(input=in_tok, cache_creation=0,
                           cache_read=0, output=out_tok)
```

(Note: `Result.model_usage` extraction is deferred to slice 5; this
task only fixes the count itself.)

**Verify:** new test passes; existing opencode happy-path test
(`PromptResponse.usage` populated) still passes.

**Commit:** `fix(drivers/acp): fall back to field_meta.quota for gemini usage`

### T3 — Extend `ToolUse` / `ToolResult` dataclasses with optional fields

**Touches:** `src/aegis/events.py:44-55`.

**Test first:** add to `tests/test_events.py`:
- `test_tool_use_optional_fields_default` — `ToolUse(name="X",
  summary="")` works as before, all new fields are None/empty.
- `test_tool_use_carries_kind_and_locations` — when constructed with
  `kind="read"`, `locations=(("foo.py", 12),)`, fields are
  preserved.

**Implementation:** add new fields per spec §"Canonical event
surface" — `kind`, `raw_input`, `tool_call_id`, `locations`,
`status` on `ToolUse`; `tool_call_id`, `kind` on `ToolResult`. Use
tuples for `locations` to keep equality/hashability sensible. Keep
existing fields (`name`, `summary`, `usage`, `text`, `is_error`)
unchanged.

**Verify:** all `test_events.py` cases pass; all
`test_render_event.py` cases still pass (renderer doesn't use the
new fields yet so output is unchanged); state codec tests pass
(codec doesn't yet handle the new fields — defaults kick in on
decode).

**Commit:** `feat(events): carry kind/tool_call_id/locations/raw_input on ToolUse and ToolResult`

### T4 — State codec roundtrip for new fields

**Touches:** `src/aegis/state/event_codec.py:31-81`.

**Test first:** add roundtrip cases to
`tests/test_state_event_codec.py`:
- `test_tool_use_with_kind_roundtrip`
- `test_tool_use_with_locations_roundtrip`
- `test_tool_use_with_raw_input_roundtrip`
- `test_tool_result_with_kind_roundtrip`
- `test_old_record_without_new_fields_decodes` — synthesize a dict in
  the legacy shape (`{"t": "ToolUse", "name": "X", "summary": "y",
  "usage": None}`) and assert `decode_event` returns the equivalent
  default-filled dataclass.

**Implementation:**

- In `encode_event`, extend the `ToolUse` branch to write `kind`,
  `tool_call_id`, `raw_input` (as the original dict), `locations` (as
  a JSON list of `[path, line]` 2-tuples), and `status` when
  non-default.
- In `encode_event`, extend the `ToolResult` branch with
  `tool_call_id` and `kind`.
- In `decode_event`, read each new key via `.get(key, default)` so
  legacy records decode cleanly. Convert the JSON list of locations
  back into a tuple of tuples on the way out.

**Verify:** new roundtrip tests pass; legacy-shape decode test
passes; existing `test_state_event_codec.py` cases pass unchanged.

**Commit:** `feat(state/event_codec): roundtrip ToolUse/ToolResult new fields`

### T5 — Claude parser populates `kind`, `tool_call_id`, `raw_input`, `locations`

**Touches:** `src/aegis/events.py:120` (`parse`) plus a tiny
`ParserState` class above it; `src/aegis/drivers/claude.py:65-80`
(the `_pump_stdout` call site).

**Test first:** extend `tests/test_events.py`:
- `test_tool_use_kind_derived_from_name` — Bash → execute, Read →
  read, Edit → edit, WebFetch → fetch, Glob → search, plus an
  unknown tool → "other".
- `test_tool_use_carries_raw_input_and_locations` — parsing the
  fixture-shape `{"name":"Read","input":{"file_path":".../foo.py"}}`
  produces `ToolUse(kind="read", raw_input={"file_path":".../foo.py"},
  locations=((".../foo.py", None),), tool_call_id="toolu_…")`.
- `test_tool_result_kind_correlated_via_state` — feed a tool_use then
  a tool_result with matching id; assert the `ToolResult.kind` is
  populated from the prior `ToolUse`. Without the matching prior id
  (`ParserState` doesn't know it), `kind` is None.

**Implementation:**

1. Add a `_KIND_BY_NAME: dict[str, str]` table at module top of
   `events.py`, per the spec name→kind table. Bash etc. → "execute";
   Read → "read"; Edit/Write/NotebookEdit → "edit"; Glob/Grep →
   "search"; WebFetch/WebSearch → "fetch"; Task/Agent → "think".
2. Add a `ParserState` dataclass at module scope:
   ```python
   @dataclass
   class ParserState:
       tool_kinds: dict[str, str] = field(default_factory=dict)
       # tool_call_id → kind, for tool_result correlation
   ```
3. Change the `parse` signature to `parse(line: str, state:
   ParserState | None = None) -> Event`. Default `state` to a
   throwaway instance so existing callers still work.
4. In the `tool_use` branch: derive `kind` via the table (default
   `"other"`), capture `id` → `tool_call_id`, pass `input` →
   `raw_input`. If `input.file_path` is a string, build
   `locations=((input["file_path"], None),)`. Stash `tool_call_id →
   kind` in `state.tool_kinds`.
5. In the `tool_result` branch: pull `tool_use_id` →
   `tool_call_id`, look up `kind` in `state.tool_kinds`.
6. In `drivers/claude.py` `_pump_stdout`, instantiate one
   `ParserState` per session and pass it on each `parse(line,
   state=self._parser_state)`.

**Verify:** new tests pass; existing claude fixture-driven tests
pass; `tests/test_drivers_acp.py` unaffected.

**Commit:** `feat(events): populate ToolUse kind/tool_call_id/raw_input/locations from claude stream`

### T6 — ACP driver populates the same fields

**Touches:** `src/aegis/drivers/acp.py:125-153`.

**Test first:** add to `tests/test_drivers_acp.py`:
- `test_acp_tool_use_carries_kind_and_locations` — stub emits
  `ToolCallStart(kind="read", locations=[{"path":".../foo.py",
  "line":None}], raw_input={"filePath":".../foo.py"}, status="pending",
  tool_call_id="call_1", title="read")`. Assert the produced
  `ToolUse` has matching fields.
- `test_acp_tool_result_correlates_via_id` — stub emits start+progress
  with matching `tool_call_id`. Assert `ToolResult.tool_call_id` is
  populated, `ToolResult.kind` matches.

**Implementation:** in `_AegisAcpClient.session_update`:

- `ToolCallStart` branch: read `kind`, `tool_call_id`, `locations`
  (as list of `(path, line)` tuples), `raw_input`, `status`, `title`.
  Stash `tool_call_id → kind` in a `self._tool_kinds` dict (replaces
  the existing `self._tool_calls` dict which is currently
  underused). Emit a `ToolUse` with all fields populated. `summary`
  derived from `raw_input` via a helper that picks `command` /
  `filePath` / `file_path` / `pattern` / first string value.
- `ToolCallProgress` (completion branch): read `tool_call_id`,
  look up `kind` in `self._tool_kinds`. Emit `ToolResult` with
  those + the existing `text` / `is_error` derivation from T1.

**Verify:** new tests pass; all existing `test_drivers_acp.py`
hermetic stub tests pass; the live-mode test (when run with `gemini`
on PATH) still works.

**Commit:** `feat(drivers/acp): populate ToolUse/ToolResult kind/tool_call_id/locations/raw_input`

### T7 — Renderer: kind icons and path hint

**Touches:** `src/aegis/render.py:13-40`.

**Test first:** extend `tests/test_render_event.py`:
- `test_tool_use_with_kind_renders_icon` — `ToolUse(name="Read",
  kind="read", summary="foo.py")` renders to text containing
  `"📖"` and `"foo.py"`.
- `test_tool_use_with_kind_execute` — kind=`"execute"` → `"⌬"`.
- `test_tool_use_unknown_kind_falls_back_to_dot` — kind=`None` →
  `"⏺"` (current behavior).
- `test_tool_use_uses_location_pathhint` — `ToolUse(name="Read",
  summary="", locations=(("/a/b/c/foo.py", None),))` renders with
  the path tail (`foo.py` at minimum).
- `test_tool_result_error_styling_preserved` — unchanged from current
  test.

**Implementation:** in `render.py`:

```python
_KIND_ICON = {
    "read": "📖", "edit": "✏️", "execute": "⌬", "search": "🔎",
    "think": "✻", "fetch": "🌐", "move": "➡️", "delete": "🗑",
    "switch_mode": "🔄", "other": "⏺",
}

def _pathhint(ev: ToolUse) -> str:
    if ev.locations:
        path, line = ev.locations[0]
        tail = path.rsplit("/", 1)[-1]
        return f"{tail}:{line}" if line is not None else tail
    return ev.summary
```

Update the `ToolUse` branch of `render_event`:

```python
if isinstance(ev, ToolUse):
    icon = _KIND_ICON.get(ev.kind or "", "⏺")
    hint = _pathhint(ev)
    arg = f"({hint})" if hint else ""
    return Text.assemble((f"{icon} ", colors.accent), f"{ev.name}{arg}")
```

(The space after the icon is conditional — emoji width is variable;
keep current spacing intentional.)

**Verify:** all new render tests pass; pre-existing
`test_tool_use_one_liner` still passes (its assertion is `"Read"` and
`"foo.py"` in output, which both still hold).

**Commit:** `feat(render): kind icons and path hints for ToolUse`

### T8 — Smoke-test all three drivers end-to-end

**Touches:** none (verification only).

Re-run:

```bash
cd repos/aegis
uv run pytest -q -m "not live"
# Spot-check live drivers (skip silently if CLI absent):
uv run pytest tests/test_drivers_multiprovider_live.py -q
```

In a live `aegis` TUI session (when on zion), spawn one claude pane,
one gemini pane, one opencode pane; send each a prompt that triggers
multiple tool calls (e.g. "list files in this directory then read
the largest one"). Visually confirm:

- Read shows 📖 with the filename.
- Bash / shell shows ⌬ with the command.
- Edit / write shows ✏️ with the path.
- Failed tools show the red `└ error` line.

Capture a screenshot or asciinema of each, drop in
`.playground/acp-visibility/slice1-after.{png,cast}` for posterity.

**No commit** (verification only). If anything looks off, file the
discrepancy in `TASKS.md` under "Active" and decide whether it's a
slice-1 fix or a slice-2 follow-on.

## Done definition

- All hermetic tests pass: `uv run pytest -q -m "not live"`.
- The live test `tests/test_drivers_multiprovider_live.py` passes
  when the relevant CLIs are on PATH; auto-skip otherwise.
- A claude pane and an opencode/gemini pane both render tool calls
  with semantic icons + path hints. Failed tools render red.
- Gemini turn metrics show non-zero tokens.
- No regression visible in current claude transcripts.
- `CHANGELOG.md` gets one bullet under unreleased: *driver visibility
  parity slice 1 — semantic tool-call icons, path hints, ACP
  `is_error` fix, gemini token-count fix.*

## Out of scope (explicit)

Anything beyond the above is slice 2+:

- `message_id` chunk aggregation (slice 2).
- `AgentPlan` event for `TodoWrite` / `AgentPlanUpdate` (slice 3).
- File-diff rendering (slice 4).
- `Result.stop_reason`, `ttft_ms`, `cost_usd`, `model_usage` (slice 5).
- `ContextUpdate` from ACP `UsageUpdate` / `CurrentModeUpdate`
  (slice 6).
- `SystemInit` enrichment with model/commands/version (slice 7).

If anything from this list is tempting mid-slice, write it down in
the spec's open-questions or in `TASKS.md` instead.
