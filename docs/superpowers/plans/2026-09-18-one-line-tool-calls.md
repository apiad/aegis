# One-Line Tool Calls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render every transcript tool call as exactly one row — label,
verdict digest, right-aligned elapsed — and open its full input and output
in a scrollable modal on click.

**Architecture:** Two pure functions in `render_shared.py` compute the
digest; `render.render_tool_use` lays out the three columns; the pane stops
folding a rendered result into the block and instead keeps the `ToolResult`
*event* on the `BlockRecord`, which is what the new
`ToolDetailScreen` reads. The record is the single source for both the live
and the replay path, which is what makes a scrolled-back call clickable.

**Tech Stack:** Python 3.13+, Rich, Textual, pytest. `uv` for everything;
never pip.

**Spec:** `docs/superpowers/specs/2026-09-18-one-line-tool-calls-design.md`

## Global Constraints

- Scope is the Textual TUI. **Do not touch `src/aegis/web/`** — it is
  superseded dead code (`2026-09-07-retire-web-ui-tui-over-web-design.md`,
  stage 6). No wire change, no `get_event`, no JS.
- `render_shared.py` is pure: no Rich, no HTML, no I/O. New helpers there
  must keep that.
- Every gate is `make check`. Iterate with `make test`. Run the repo's own
  target verbatim — bare `pytest` skips flags the Makefile passes.
- Shared checkout: stage named paths, `git commit -- <paths>`, never
  `git add -A`, never `--amend`.
- Conventional commits, English, one logical change each.
- Per `AGENTS.md`, a user-visible change needs a CHANGELOG entry and a
  `docs/` update, and must be exercised in a real TUI against a daemon
  started *after* the change. Task 6 does both.

---

### Task 1: The digest and the diff counts

**Files:**
- Modify: `src/aegis/render_shared.py` (add beside `describe_tool`, ~line 110)
- Test: `tests/test_result_digest.py` (create)

**Interfaces:**
- Produces: `diff_counts(old_text: str, new_text: str) -> tuple[int, int]`
  returning `(added, removed)`; `result_digest(name: str, result: ToolResult
  | None) -> str` returning the short verdict text (empty string while a
  call is in flight, i.e. `result is None`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_result_digest.py`:

```python
from aegis.events import ToolResult
from aegis.render_shared import diff_counts, result_digest


def test_diff_counts_ignores_common_prefix_and_suffix():
    # diff_window caps at six rows and elides context; a COUNT must see
    # every changed line, so this pair has more changes than that budget.
    old = "\n".join(["same"] + [f"old{i}" for i in range(9)] + ["tail"])
    new = "\n".join(["same"] + [f"new{i}" for i in range(9)] + ["tail"])
    assert diff_counts(old, new) == (9, 9)


def test_edit_digest_reads_plus_minus():
    r = ToolResult(text="ok", is_error=False,
                   diff=("pane.py", "a\nb\nc", "a\nB\nc\nd"))
    assert result_digest("Edit", r) == "+2 −1"


def test_write_digest_has_no_removed_side():
    r = ToolResult(text="ok", is_error=False,
                   diff=("new.py", "", "one\ntwo\nthree"))
    assert result_digest("Write", r) == "+3"


def test_read_digest_counts_lines():
    r = ToolResult(text="\n".join(f"line{i}" for i in range(501)),
                   is_error=False)
    assert result_digest("Read", r) == "501 lines"


def test_grep_digest_says_no_matches_when_empty():
    assert result_digest("Grep", ToolResult(text="", is_error=False)) == "no matches"


def test_grep_digest_counts_matches():
    r = ToolResult(text="a.py:1:x\nb.py:2:y", is_error=False)
    assert result_digest("Grep", r) == "2 matches"


def test_bash_digest_takes_the_last_non_empty_line():
    # The verdict of a command lives at the end: "3629 passed", not the
    # progress bar the first line carries.
    r = ToolResult(text="....... [ 1%]\n....... [99%]\n\n3629 passed\n\n",
                   is_error=False)
    assert result_digest("Bash", r) == "3629 passed"


def test_unknown_tool_digest_takes_the_first_non_empty_line():
    r = ToolResult(text="\n\nmonitor_id: mon_4f2a\nmore\n", is_error=False)
    assert result_digest("mcp__aegis__aegis_monitor", r) == "monitor_id: mon_4f2a"


def test_error_digest_ignores_the_tool_shape():
    # An error result does not have the shape the success digest reads:
    # a failed Read is a message, not a line count.
    r = ToolResult(text="File does not exist: /nope.py\nand more", is_error=True)
    assert result_digest("Read", r) == "File does not exist: /nope.py"


def test_digest_is_empty_while_the_call_is_in_flight():
    assert result_digest("Bash", None) == ""


def test_digest_of_a_silent_result_is_ok():
    assert result_digest("Bash", ToolResult(text="   \n\n", is_error=False)) == "ok"


def test_digest_clips_a_long_line():
    r = ToolResult(text="x" * 200, is_error=False)
    out = result_digest("Bash", r)
    assert len(out) <= 60 and out.endswith("…")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_result_digest.py -q`
Expected: FAIL — `ImportError: cannot import name 'diff_counts'`.

- [ ] **Step 3: Implement both functions**

In `src/aegis/render_shared.py`, after `describe_tool` (the `tool_label`
definition follows it — insert between them):

```python
def diff_counts(old_text: str, new_text: str) -> tuple[int, int]:
    """(added, removed) line counts for an Edit/Write diff.

    Counted over the whole pair, not through ``diff_window``: that helper
    elides the common prefix and caps the visible rows at six, which is
    right for a preview and wrong for a count.
    """
    import difflib

    old_lines = old_text.splitlines() if old_text else []
    new_lines = new_text.splitlines() if new_text else []
    added = removed = 0
    for line in difflib.ndiff(old_lines, new_lines):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


def result_digest(name: str, result) -> str:
    """The short verdict a collapsed tool line carries after its glyph.

    One line, never more: the transcript gives a tool call exactly one row
    and the full output is a click away. ``result`` is the folded
    ``ToolResult``, or None while the call is still running — in flight
    there is no verdict, only a spinner.
    """
    if result is None:
        return ""
    text = result.text or ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # An error does not have the shape the tool's success digest reads: a
    # failed Read is a message, not a line count.
    if result.is_error:
        return _trunc(lines[0], 60) if lines else "error"
    if result.diff is not None:
        _path, old, new = result.diff
        added, removed = diff_counts(old, new)
        return f"+{added} −{removed}" if removed else f"+{added}"
    if name == "Read":
        n = len(text.splitlines())
        return f"{n} line{'s' if n != 1 else ''}"
    if name in ("Grep", "Glob"):
        n = len(lines)
        return f"{n} match{'es' if n != 1 else ''}" if n else "no matches"
    if not lines:
        return "ok"
    # Bash's verdict lives on the last line — "3629 passed",
    # "Successfully installed", "error: cannot find" — and first-line-wins
    # would show the progress bar instead. The cost is that `ls` shows its
    # last filename, which is noise and not a lie.
    return _trunc(lines[-1] if name == "Bash" else lines[0], 60)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_result_digest.py -q`
Expected: PASS, 12 tests.

- [ ] **Step 5: Commit**

```bash
git add tests/test_result_digest.py src/aegis/render_shared.py
git commit -- tests/test_result_digest.py src/aegis/render_shared.py
```

Message: `feat(render): the one-line verdict digest for a tool result`

---

### Task 2: The three-column tool line

**Files:**
- Modify: `src/aegis/render.py:121-152` (`render_tool_use`)
- Test: `tests/test_render_event.py` (append; it already imports
  `render_tool_use` and has an `as_text` helper at width 80)

**Interfaces:**
- Consumes: `result_digest` from Task 1.
- Produces: `render_tool_use(ev, colors, *, elapsed=None, running=False,
  frame=0, result=None, width=None) -> RenderableType` — always exactly
  one row. The `expanded` parameter is **removed**; Task 3 removes its
  last caller.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_render_event.py`:

```python
def test_tool_line_carries_digest_and_elapsed_on_one_row():
    ev = ToolUse(name="Bash", summary="", kind="execute",
                 raw_input={"description": "run the test suite",
                            "command": "uv run pytest -q"})
    res = ToolResult(text="3629 passed", is_error=False)
    out = as_text(render_tool_use(ev, C, elapsed=12.4, result=res, width=80))
    rows = [r for r in out.splitlines() if r.strip()]
    assert len(rows) == 1
    assert "run the test suite" in rows[0]
    assert "3629 passed" in rows[0]
    assert rows[0].rstrip().endswith("12.4s")


def test_tool_line_marks_an_error():
    ev = ToolUse(name="Read", summary="", kind="read",
                 raw_input={"file_path": "/nope.py"})
    res = ToolResult(text="File does not exist", is_error=True)
    out = as_text(render_tool_use(ev, C, elapsed=0.1, result=res, width=80))
    assert "✗" in out and "✓" not in out
    assert "File does not exist" in out


def test_running_tool_line_has_a_spinner_and_no_digest():
    ev = ToolUse(name="Bash", summary="", kind="execute",
                 raw_input={"description": "sleep", "command": "sleep 9"})
    out = as_text(render_tool_use(ev, C, elapsed=3.2, running=True, width=80))
    rows = [r for r in out.splitlines() if r.strip()]
    assert len(rows) == 1
    assert "3.2s" in rows[0]
    assert "✓" not in rows[0] and "✗" not in rows[0]


def test_sub_second_elapsed_is_shown():
    # The old line hid anything under 1s. A right-hand column that appears
    # and disappears jitters, and 0.3s is information.
    ev = ToolUse(name="Read", summary="", kind="read",
                 raw_input={"file_path": "/a/render.py"})
    res = ToolResult(text="x\ny", is_error=False)
    out = as_text(render_tool_use(ev, C, elapsed=0.3, result=res, width=80))
    assert "0.3s" in out


def test_tool_line_stays_one_row_at_a_narrow_width():
    # A line that wraps is the whole feature failing.
    ev = ToolUse(name="Bash", summary="", kind="execute",
                 raw_input={"description": "a very long description " * 6,
                            "command": "echo " + "x" * 200})
    res = ToolResult(text="y" * 200, is_error=False)
    con = Console(record=True, width=40)
    con.print(render_tool_use(ev, C, elapsed=1.5, result=res, width=40))
    rows = [r for r in con.export_text().splitlines() if r.strip()]
    assert len(rows) == 1
    assert rows[0].rstrip().endswith("1.5s")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_render_event.py -q -k "tool_line or sub_second"`
Expected: FAIL — `render_tool_use() got an unexpected keyword argument 'result'`.

- [ ] **Step 3: Rewrite `render_tool_use`**

Replace the body of `render_tool_use` in `src/aegis/render.py` (keep the
imports; add `result_digest` to the `from aegis.render_shared import`
block at the top of the file):

```python
_ELAPSED_W = 7  # "  12.4s" / " 3m04s" — the widest _fmt_dur output


def render_tool_use(
    ev,
    colors,
    *,
    elapsed: float | None = None,
    running: bool = False,
    frame: int = 0,
    result=None,
    width: int | None = None,
) -> RenderableType:
    """One tool call, one row: glyph + label, then the verdict and its
    digest, then the elapsed time in a right-hand column.

    Exactly one row is the contract. The full input and output live behind
    the click, in ``ToolDetailScreen`` — a transcript that grew a dozen
    rows per call could not be skimmed, and the rows it grew carried the
    first 100 characters of the output, which is where a Read shows its
    imports and a Bash shows a progress bar.
    """
    icon = tool_glyph(ev.name, ev.kind, ev.raw_input)
    desc = describe_tool(ev.name, ev.raw_input, ev.summary, ev.locations)
    # A call into the aegis layer wears the layer's own colour, so a
    # transcript shows at a glance where agents were talking to each other.
    style = colors.comms if aegis_glyph(ev.name, ev.raw_input or {}) else colors.accent

    line = Text(no_wrap=True, overflow="ellipsis", end="")
    line.append(f"{icon} ", style=style)
    line.append(desc)
    if running:
        verdict, vstyle = _TOOL_SPINNER[frame % len(_TOOL_SPINNER)], colors.working
    elif result is None:
        verdict, vstyle = "", colors.muted
    elif result.is_error:
        verdict, vstyle = "✗", colors.err
    else:
        verdict, vstyle = "✓", colors.ok
    digest = "" if running else result_digest(ev.name, result)
    if verdict or digest:
        line.append("  ")
        if verdict:
            line.append(verdict, style=vstyle)
        if digest:
            line.append(f" {digest}", style=colors.muted)

    if elapsed is None:
        return line
    stamp = _fmt_dur(elapsed)
    if width is None:
        line.append(f"  {stamp}", style=colors.muted)
        return line
    # Right-align the stamp in its own column, clipping the label side to
    # make room. Truncate before padding: Text.pad_right on an
    # already-too-long line would not shorten it.
    body_w = max(1, width - _ELAPSED_W)
    line.truncate(body_w, overflow="ellipsis")
    line.pad_right(body_w - line.cell_len)
    line.append(stamp.rjust(_ELAPSED_W), style=colors.muted)
    return line
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_render_event.py -q`
Expected: PASS. Any pre-existing test asserting the old `· 12.4s` inline
form or the `expanded=` parameter fails here — update it to the new
contract rather than restoring the old shape.

- [ ] **Step 5: Break the check on purpose**

Temporarily change `line.truncate(body_w, ...)` to `line.truncate(width,
...)` and re-run `test_tool_line_stays_one_row_at_a_narrow_width`. It must
FAIL (two rows). Restore the line. A one-row assertion that cannot fail is
worth less than none.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/render.py tests/test_render_event.py
git commit -- src/aegis/render.py tests/test_render_event.py
```

Message: `feat(render): one row per tool call, with the elapsed in a column`

---

### Task 3: The record becomes the source of truth

**Files:**
- Modify: `src/aegis/tui/pane.py` — `_ToolTrack` (~148), `_mount_replay`
  (~1349-1400), the live `ToolUse` branch (~2548-2568), `_fold_tool_result`
  (~2631), `_render_tool_block` (~2783), `on_copyable_block_tool_expand_toggle`
  (~2839)
- Test: `tests/test_pane_tool_records.py` (create)

**Interfaces:**
- Consumes: `render_tool_use(..., result=, width=)` from Task 2.
- Produces: `ConversationPane.tool_record(tool_call_id: str) ->
  BlockRecord | None`, resolving a call to the record holding
  `events == [ToolUse]` or `[ToolUse, ToolResult]`. Task 5's modal reads
  through it. `_ToolTrack.result_ev` holds the same `ToolResult` for the
  live path; `_ToolTrack.expanded` and `_ToolTrack.result_r` are gone.

- [ ] **Step 1: Write the failing tests**

First add the shared fixture to `tests/conftest.py` — every later task
uses it, and there is no equivalent today:

```python
# tests/conftest.py
from contextlib import asynccontextmanager

import pytest


@pytest.fixture
def pane_app():
    """A booted AegisApp and its first ConversationPane.

    `app._panes[0]` under `app.run_test()` is how the other pane tests
    reach one (see tests/test_background_costs.py). Events go in through
    `_on_core_event(_core, ev)`, the same entry the SessionManager uses.
    Passing `events` replays them off a fake log instead, which is the
    resume path.
    """
    from aegis.config import Agent
    from aegis.events import Result
    from aegis.state.session_log import EventReplay
    from aegis.tui.app import AegisApp

    class FakeSession:
        async def start(self): pass
        async def send(self, text): pass
        async def events(self):
            yield Result(duration_ms=1, is_error=False)
        async def close(self): pass

    class FakeMCP:
        url = "http://127.0.0.1:0/mcp/"
        def bind(self, bridge): pass
        async def start(self): pass
        async def stop(self): pass

    @asynccontextmanager
    async def _make(events=()):
        agent = Agent(harness="claude-code", model="opus", effort="high",
                      permission="auto")
        app = AegisApp({"default": agent}, "default",
                       lambda *a, **kw: FakeSession(), FakeMCP())
        async with app.run_test() as pilot:
            pane = app._panes[0]
            if events:
                pane._replay = EventReplay(events=list(events),
                                           interrupted=False)
                pane._replayed = False
                pane._mount_replay()
            await pilot.pause()
            yield pane, pilot

    return _make
```

Then create `tests/test_pane_tool_records.py`:

```python
"""The transcript record — not the live track — is what the detail window
reads. A call that scrolled out of the window, or one replayed from disk
on resume, must resolve exactly like one that just landed."""

import pytest
from aegis.events import ToolResult, ToolUse


def _use(tid="t1"):
    return ToolUse(name="Bash", summary="", kind="execute",
                   raw_input={"description": "run tests",
                              "command": "pytest -q"},
                   tool_call_id=tid)


def _res(tid="t1", text="3629 passed"):
    return ToolResult(text=text, is_error=False, tool_call_id=tid)


@pytest.mark.asyncio
async def test_a_live_call_resolves_to_a_record_holding_both_events(pane_app):
    async with pane_app() as (pane, pilot):
        pane._on_core_event(None, _use())
        pane._on_core_event(None, _res())
        await pilot.pause()
        rec = pane.tool_record("t1")
        assert rec is not None
        assert [type(e).__name__ for e in rec.events] == ["ToolUse", "ToolResult"]
        assert rec.events[1].text == "3629 passed"


@pytest.mark.asyncio
async def test_a_replayed_call_resolves_the_same_way(pane_app):
    # On resume the pane rebuilds _history off the log. Before this change
    # the replayed record carried no tool_call_id, so its block fell
    # through to copy-on-click and the call could not be opened at all.
    async with pane_app(events=[_use("t9"), _res("t9", "ok from disk")]) as (pane, _):
        rec = pane.tool_record("t9")
        assert rec is not None
        assert rec.tool_call_id == "t9"
        assert rec.events[1].text == "ok from disk"


@pytest.mark.asyncio
async def test_an_evicted_call_still_resolves(pane_app):
    # Eviction moves _window_start and unmounts widgets; it never drops a
    # record. A call from 400 blocks ago must still open.
    async with pane_app() as (pane, pilot):
        for i in range(400):
            pane._on_core_event(None, _use(f"t{i}"))
            pane._on_core_event(None, _res(f"t{i}"))
        await pilot.pause()
        assert pane._window_start > 0
        assert pane.tool_record("t0") is not None


@pytest.mark.asyncio
async def test_the_result_does_not_get_its_own_block(pane_app):
    async with pane_app() as (pane, pilot):
        before = len(pane._history)
        pane._on_core_event(None, _use())
        pane._on_core_event(None, _res())
        await pilot.pause()
        assert len(pane._history) == before + 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pane_tool_records.py -q`
Expected: FAIL — `AttributeError: 'ConversationPane' object has no
attribute 'tool_record'`.

- [ ] **Step 3: Make the record carry the events**

Four edits in `src/aegis/tui/pane.py`:

1. `_ToolTrack` — drop `result_r` and `expanded`, add the event:

```python
@dataclass(slots=True)
class _ToolTrack:
    """Live state for one tool call: enough to re-render its single row
    with a ticking timer while running and a frozen duration once done.

    The full input and output are NOT here — they are the events on the
    BlockRecord, which is the one source the detail window reads, so a
    replayed call and a live one open identically."""

    ev: object  # the ToolUse event
    idx: int  # history index of its block
    start: float  # time.monotonic() at dispatch
    done: bool = False
    elapsed: float | None = None  # frozen duration once done
    result_ev: object | None = None  # the folded ToolResult
```

2. The live `ToolUse` branch (~2548) — pass the events onto the record.
   `_mount_block` grows an `events` keyword that it forwards to
   `BlockRecord(...)`:

```python
    def _mount_block(
        self,
        renderable: RenderableType,
        text_payload: str,
        *,
        tight: bool = False,
        tool_call_id: str | None = None,
        file_target: FileTarget | None = None,
        remote_path: str | None = None,
        events: list | None = None,
    ) -> CopyableBlock:
        self._history.append(
            BlockRecord(
                renderable, text_payload, tight, tool_call_id,
                events=events, file_target=file_target,
            )
        )
```

   and the call site passes `events=[ev]`. Careful: `BlockRecord.materialize`
   re-renders from `events` when `renderable is None`; here the renderable
   is set, so nothing changes about painting.

3. `_fold_tool_result` — keep the event, not the rendered result:

```python
        track.done = True
        track.elapsed = time.monotonic() - track.start
        track.result_ev = ev
        rec = self._history[track.idx]
        rec.events = (rec.events or []) + [ev]
        rec.payload = f"{rec.payload}\n{_payload_for_event(ev)}"
        self._render_tool_block(track, scroll=True)
```

   Delete the two lines above it that built `result_r` via `render_event`.

4. `_mount_replay` — give the replayed record its id, and index it:

```python
            records.append(
                BlockRecord(
                    None,
                    _payload_for_event(ev),
                    False,
                    ev.tool_call_id if isinstance(ev, ToolUse) else None,
                    events=[ev],
                    file_target=(...unchanged...),
                )
            )
```

   and after the loop, before `self._history = records + self._history`,
   seed the index with what the replay already built:

```python
        # The same map the live path keeps, so a replayed call resolves
        # through one lookup rather than a second, divergent one.
        self._tool_use_idx.update(use_idx)
```

- [ ] **Step 4: Add the lookup and drop the inline expansion**

```python
    def tool_record(self, tool_call_id: str) -> "BlockRecord | None":
        """The transcript record for a tool call, live or replayed.

        Indexes are stable: eviction moves _window_start and unmounts
        widgets (`_evict_top`) but never drops a record."""
        idx = self._tool_use_idx.get(tool_call_id)
        if idx is None or not (0 <= idx < len(self._history)):
            return None
        rec = self._history[idx]
        return rec if rec.events else None
```

In `_render_tool_block`, drop the `expanded=` argument and the
`Group(line, track.result_r)`, and pass the result and the width:

```python
        running = not track.done
        elapsed = (time.monotonic() - track.start) if running else track.elapsed
        rend = render_tool_use(
            track.ev,
            self._palette,
            elapsed=elapsed,
            running=running,
            frame=self._spin_frame,
            result=track.result_ev,
            width=self._transcript().size.width or 80,
        )
        rec = self._history[track.idx]
        rec.renderable = rend
```

`on_copyable_block_tool_expand_toggle` keeps its name and its message but
loses the toggle — Task 5 gives it the modal. For this task, make it a
no-op returning early after `event.stop()`, so the click does nothing
rather than doing the old thing.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_pane_tool_records.py tests/test_render_event.py -q`
Expected: PASS.

Then `uv run pytest tests/ -q -x -k "pane or transcript or replay"` and fix
what the removed `result_r` / `expanded` broke.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/tui/pane.py tests/test_pane_tool_records.py tests/conftest.py
git commit -- src/aegis/tui/pane.py tests/test_pane_tool_records.py tests/conftest.py
```

Message: `refactor(pane): the block record carries the tool events`

---

### Task 4: The detail window

**Files:**
- Create: `src/aegis/tui/tool_detail.py`
- Modify: `src/aegis/render_shared.py:213` (`format_tool_args` grows `cap`)
- Test: `tests/test_tool_detail.py` (create)

**Interfaces:**
- Consumes: `BlockRecord` from Task 3; `_render_diff` from
  `aegis.render`; `format_tool_args`.
- Produces: `ToolDetailScreen(use: ToolUse, result: ToolResult | None,
  elapsed: float | None, colors, *, on_step=None)` and the pure
  `detail_body(use, result, colors, *, max_rows=OUTPUT_MAX_ROWS) ->
  tuple[RenderableType, int]` returning the renderable and how many output
  rows were dropped.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tool_detail.py`:

```python
from rich.console import Console
from aegis.events import ToolResult, ToolUse
from aegis.tui.themes import aegis_colors, INK
from aegis.tui.tool_detail import OUTPUT_MAX_ROWS, detail_body

C = aegis_colors(INK)


def as_text(renderable, width=100) -> str:
    con = Console(record=True, width=width)
    con.print(renderable)
    return con.export_text()


def test_body_shows_the_full_command_not_the_500_char_cap():
    cmd = "echo " + "x" * 900
    use = ToolUse(name="Bash", summary="", kind="execute",
                  raw_input={"description": "long", "command": cmd})
    body, dropped = detail_body(use, ToolResult(text="ok", is_error=False), C)
    assert "x" * 900 in as_text(body, width=1200)
    assert dropped == 0


def test_body_shows_the_full_output():
    out = "\n".join(f"line{i}" for i in range(50))
    use = ToolUse(name="Bash", summary="", kind="execute",
                  raw_input={"command": "seq"})
    body, dropped = detail_body(use, ToolResult(text=out, is_error=False), C)
    rendered = as_text(body)
    assert "line0" in rendered and "line49" in rendered
    assert dropped == 0


def test_a_huge_output_is_capped_and_says_how_much_it_dropped():
    out = "\n".join(f"line{i}" for i in range(OUTPUT_MAX_ROWS + 3000))
    use = ToolUse(name="Bash", summary="", kind="execute",
                  raw_input={"command": "seq"})
    body, dropped = detail_body(use, ToolResult(text=out, is_error=False), C)
    assert dropped == 3000
    rendered = as_text(body)
    assert "3,000 more lines" in rendered
    assert f"line{OUTPUT_MAX_ROWS + 2999}" not in rendered


def test_an_edit_shows_its_whole_diff_not_a_six_row_preview():
    old = "\n".join(f"old{i}" for i in range(20))
    new = "\n".join(f"new{i}" for i in range(20))
    use = ToolUse(name="Edit", summary="", kind="edit",
                  raw_input={"file_path": "/a/pane.py",
                             "old_string": old, "new_string": new})
    res = ToolResult(text="ok", is_error=False, diff=("/a/pane.py", old, new))
    body, _ = detail_body(use, res, C)
    rendered = as_text(body)
    assert "old19" in rendered and "new19" in rendered
    assert "more line" not in rendered


def test_a_running_call_has_no_output_section():
    use = ToolUse(name="Bash", summary="", kind="execute",
                  raw_input={"command": "sleep 9"})
    body, dropped = detail_body(use, None, C)
    rendered = as_text(body)
    assert "sleep 9" in rendered
    assert "still running" in rendered
    assert dropped == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tool_detail.py -q`
Expected: FAIL — `ModuleNotFoundError: aegis.tui.tool_detail`.

- [ ] **Step 3: Give `format_tool_args` an uncapped mode**

In `src/aegis/render_shared.py`, change the signature and the one capping
branch:

```python
def format_tool_args(
    name: str, raw_input: dict | None, summary: str = "", cap: int | None = 500
) -> str:
    """The full-args view of a tool call. Bash shows its command verbatim
    (with the description as a leading comment); other tools show
    ``key: value`` lines, with long values capped at ``cap`` characters —
    ``cap=None`` for the detail window, which is the one place that wants
    every character.
    Pure — no Rich, no HTML."""
```

and inside the loop:

```python
        if cap is not None and len(val) > cap:
            val = val[:cap] + "…"
```

- [ ] **Step 4: Write the module**

Create `src/aegis/tui/tool_detail.py`:

```python
"""The window behind a tool call.

The transcript gives a call exactly one row (`render.render_tool_use`).
Everything that row cannot hold — the whole command, the whole diff, the
whole output — is here, one click away.

One scroll region, not two panes: the input is a few lines you read before
you start scrolling, a pinned pane would cost those rows on every call,
and two panes raise a question — which one does PgDn move? — that one
region never asks. The command stays legible while you read a failure
because it is also in the title bar.
"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from aegis.render import _fmt_dur, _render_diff
from aegis.render_shared import format_tool_args, tool_glyph

# Textual lays out every mounted row, so a 50,000-line result would cost a
# full-screen reflow to show a screenful. The pane holds the whole string
# either way — `f` writes it out and opens it in a file tab, which is a
# real viewer with a search box.
OUTPUT_MAX_ROWS = 2000


def _rule(title: str, colors, note: str = "") -> Text:
    line = Text()
    line.append(f"{title}\n", style=f"bold {colors.accent}")
    if note:
        line.append(f"{note}\n", style=colors.muted)
    return line


def detail_body(use, result, colors, *, max_rows: int = OUTPUT_MAX_ROWS):
    """The scrollable contents: the call's full input, then its full
    output. Returns (renderable, dropped_row_count). Pure."""
    parts: list[RenderableType] = [_rule("input", colors)]
    if use.name in ("Edit", "Write") and result is not None and result.diff:
        # The diff IS the call; an old_string/new_string dump is a worse
        # rendering of the same thing. No line budget here.
        parts.append(_render_diff(result.diff, colors, max_lines=10**9))
    else:
        args = format_tool_args(use.name, use.raw_input, use.summary, cap=None)
        parts.append(Text(args or "(no arguments)", style=colors.muted))

    dropped = 0
    if result is None:
        parts.append(Text("\nstill running…", style=colors.working))
        return Group(*parts), 0

    lines = (result.text or "").splitlines()
    dropped = max(0, len(lines) - max_rows)
    note = f"{len(lines):,} lines" if lines else "no output"
    parts.append(Text("\n"))
    parts.append(_rule("output", colors, note))
    style = colors.err if result.is_error else ""
    parts.append(Text("\n".join(lines[:max_rows]), style=style))
    if dropped:
        parts.append(
            Text(
                f"\n… {dropped:,} more lines — c copies all, f opens the whole thing",
                style=colors.muted,
            )
        )
    return Group(*parts), dropped


class ToolDetailScreen(ModalScreen):
    """The full input and output of one tool call."""

    DEFAULT_CSS = """
    ToolDetailScreen { align: center middle; }
    ToolDetailScreen #td-box {
        width: 90%; height: 85%;
        border: round $panel; background: $surface; padding: 0 2;
    }
    ToolDetailScreen #td-title { width: 100%; height: 1; }
    ToolDetailScreen #td-scroll { width: 100%; height: 1fr; }
    ToolDetailScreen #td-help { dock: bottom; width: 100%; height: 1;
                                color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close", priority=True),
        Binding("c", "copy", "Copy output", show=False),
        Binding("f", "open_as_file", "Open as file", show=False),
        Binding("n", "step(1)", "Next call", show=False),
        Binding("p", "step(-1)", "Previous call", show=False),
    ]

    def __init__(self, use, result, elapsed, colors, *, on_step=None) -> None:
        super().__init__()
        self._use, self._result, self._elapsed = use, result, elapsed
        self._colors = colors
        # Called with +1/-1 to swap this screen's contents for the next or
        # previous call. Scanning a turn one open-and-close at a time is
        # what would stop anyone using this window.
        self._on_step = on_step

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="td-box"):
            yield Label(self._title(), id="td-title")
            with VerticalScroll(id="td-scroll"):
                yield Static(detail_body(
                    self._use, self._result, self._colors)[0], id="td-body")
            yield Label(
                "↑↓ PgDn scroll · c copy · f open as file · n/p next call · esc close",
                id="td-help",
            )

    def _title(self) -> Text:
        from aegis.render_shared import describe_tool

        glyph = tool_glyph(self._use.name, self._use.kind, self._use.raw_input)
        t = Text()
        t.append(f"{glyph} ", style=self._colors.accent)
        t.append(describe_tool(
            self._use.name, self._use.raw_input, self._use.summary,
            self._use.locations))
        if self._result is not None:
            t.append("  ")
            t.append("✗" if self._result.is_error else "✓",
                     style=self._colors.err if self._result.is_error
                     else self._colors.ok)
        if self._elapsed is not None:
            t.append(f"  {_fmt_dur(self._elapsed)}", style=self._colors.muted)
        return t

    def show(self, use, result, elapsed) -> None:
        """Swap in another call without closing — what n/p drive."""
        self._use, self._result, self._elapsed = use, result, elapsed
        self.query_one("#td-title", Label).update(self._title())
        self.query_one("#td-body", Static).update(
            detail_body(use, result, self._colors)[0])
        self.query_one("#td-scroll", VerticalScroll).scroll_home(animate=False)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    def action_step(self, delta: int) -> None:
        if self._on_step is not None:
            self._on_step(delta)

    def action_copy(self) -> None:
        text = (self._result.text if self._result else "") or ""
        self.app.copy_to_clipboard(text)
        self.app.notify(f"copied {len(text)} chars", timeout=1.5)

    async def action_open_as_file(self) -> None:
        import tempfile
        from pathlib import Path

        opener = getattr(self.app, "_open_file_tab", None)
        if opener is None or self._result is None:
            return
        fd = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix=f"{self._use.name}-", delete=False
        )
        with fd:
            fd.write(self._result.text or "")
        self.dismiss(None)
        await opener(Path(fd.name))
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_tool_detail.py tests/test_render_event.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/tui/tool_detail.py tests/test_tool_detail.py src/aegis/render_shared.py
git commit -- src/aegis/tui/tool_detail.py tests/test_tool_detail.py src/aegis/render_shared.py
```

Message: `feat(tui): a scrollable window for one tool call's input and output`

---

### Task 5: The click opens it

**Files:**
- Modify: `src/aegis/tui/pane.py` — `on_copyable_block_tool_expand_toggle`
  (~2839), plus a `_step_tool` helper beside `tool_record`
- Modify: `src/aegis/tui/pane.py:466` (`CopyableBlock` tooltip text)
- Test: `tests/test_tool_detail_open.py` (create)

**Interfaces:**
- Consumes: `tool_record` from Task 3, `ToolDetailScreen` from Task 4.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tool_detail_open.py`, using the `pane_app` fixture
from Task 3:

```python
import pytest
from aegis.events import ToolResult, ToolUse
from aegis.tui.pane import CopyableBlock
from aegis.tui.tool_detail import ToolDetailScreen


def _use(tid, cmd):
    return ToolUse(name="Bash", summary="", kind="execute",
                   raw_input={"command": cmd}, tool_call_id=tid)


def _land(pane, tid, cmd, out="ok"):
    pane._on_core_event(None, _use(tid, cmd))
    pane._on_core_event(None, ToolResult(text=out, is_error=False,
                                         tool_call_id=tid))


@pytest.mark.asyncio
async def test_clicking_a_tool_block_opens_the_window(pane_app):
    async with pane_app() as (pane, pilot):
        _land(pane, "t1", "pytest -q", "3629 passed")
        pane.post_message(CopyableBlock.ToolExpandToggle("t1"))
        await pilot.pause()
        assert isinstance(pane.app.screen, ToolDetailScreen)


@pytest.mark.asyncio
async def test_escape_closes_it(pane_app):
    async with pane_app() as (pane, pilot):
        _land(pane, "t1", "pytest -q")
        pane.post_message(CopyableBlock.ToolExpandToggle("t1"))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(pane.app.screen, ToolDetailScreen)


@pytest.mark.asyncio
async def test_n_steps_to_the_next_call_without_closing(pane_app):
    async with pane_app() as (pane, pilot):
        _land(pane, "t0", "first cmd")
        _land(pane, "t1", "second cmd")
        pane.post_message(CopyableBlock.ToolExpandToggle("t0"))
        await pilot.pause()
        screen = pane.app.screen
        assert screen._use.raw_input["command"] == "first cmd"
        await pilot.press("n")
        await pilot.pause()
        assert pane.app.screen is screen
        assert screen._use.raw_input["command"] == "second cmd"


@pytest.mark.asyncio
async def test_n_at_the_last_call_stays_put(pane_app):
    # The ends are walls, not a wrap — a wrap reads as a glitch.
    async with pane_app() as (pane, pilot):
        _land(pane, "t0", "only cmd")
        pane.post_message(CopyableBlock.ToolExpandToggle("t0"))
        await pilot.pause()
        screen = pane.app.screen
        await pilot.press("n")
        await pilot.pause()
        assert pane.app.screen is screen
        assert screen._use.raw_input["command"] == "only cmd"


@pytest.mark.asyncio
async def test_a_replayed_call_opens_too(pane_app):
    use = _use("t7", "from the log")
    res = ToolResult(text="ok", is_error=False, tool_call_id="t7")
    async with pane_app(events=[use, res]) as (pane, pilot):
        pane.post_message(CopyableBlock.ToolExpandToggle("t7"))
        await pilot.pause()
        assert isinstance(pane.app.screen, ToolDetailScreen)
        assert pane.app.screen._use.raw_input["command"] == "from the log"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tool_detail_open.py -q`
Expected: FAIL — the screen is never pushed (Task 3 left the handler a
no-op).

- [ ] **Step 3: Wire the handler**

Replace the no-op in `src/aegis/tui/pane.py`:

```python
    def on_copyable_block_tool_expand_toggle(
        self, event: "CopyableBlock.ToolExpandToggle"
    ) -> None:
        event.stop()
        self._open_tool_detail(event.tool_call_id)

    def _open_tool_detail(self, tool_call_id: str) -> None:
        from aegis.tui.tool_detail import ToolDetailScreen

        rec = self.tool_record(tool_call_id)
        if rec is None:
            return
        use, result = rec.events[0], (rec.events[1] if len(rec.events) > 1 else None)
        track = self._tools.get(tool_call_id)
        elapsed = track.elapsed if track is not None else None
        screen = ToolDetailScreen(use, result, elapsed, self._palette)
        screen._on_step = lambda delta: self._step_tool_detail(screen, delta)
        self.app.push_screen(screen)

    def _step_tool_detail(self, screen, delta: int) -> None:
        """n / p: swap the window's contents for the next or previous tool
        call in the transcript, without closing it."""
        ids = [
            rec.tool_call_id
            for rec in self._history
            if rec.tool_call_id and rec.events
        ]
        current = getattr(screen._use, "tool_call_id", None)
        if current not in ids:
            return
        nxt = ids.index(current) + delta
        if not (0 <= nxt < len(ids)):
            return  # the ends are walls, not a wrap: a wrap reads as a glitch
        rec = self.tool_record(ids[nxt])
        if rec is None:
            return
        track = self._tools.get(ids[nxt])
        screen.show(
            rec.events[0],
            rec.events[1] if len(rec.events) > 1 else None,
            track.elapsed if track is not None else None,
        )
```

- [ ] **Step 4: Fix the tooltip**

`pane.py:466` still says `"click to expand args"`. It is now:

```python
        tip = "click to open the call" if tool_call_id is not None else "click to copy"
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_tool_detail_open.py -q`
Expected: PASS.

- [ ] **Step 6: Run the whole suite**

Run: `make test`
Expected: rc=0. Read the exit code directly — do not pipe it through
`tail`, which hands `&&` its own status. Anything red here is this
change's, not the suite's: `aegis` has a known inotify flake (1-2 tests)
documented in `know-how/`, and nothing else.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/tui/pane.py tests/test_tool_detail_open.py
git commit -- src/aegis/tui/pane.py tests/test_tool_detail_open.py
```

Message: `feat(tui): clicking a tool call opens its input and output`

---

### Task 6: Docs, and driving it for real

**Files:**
- Modify: `CHANGELOG.md` (top, under the unreleased heading)
- Modify: `docs/usage.md:239-248` (the block-gesture paragraph)
- Modify: `docs/superpowers/specs/2026-09-18-one-line-tool-calls-design.md`
  (status line)

- [ ] **Step 1: Update `docs/usage.md`**

The paragraph at `docs/usage.md:243` says "A tool-call block clicks to
expand its full arguments instead." Replace it with:

```markdown
A tool-call block is one row — what ran, how it went, how long it took.
**Click it** to open the call in a window with its whole input and whole
output: `↑↓`/`PgUp`/`PgDn` scroll, `c` copies the output, `f` writes it to
a file and opens it in a tab, `n` and `p` step to the next and previous
call without closing, `Esc` closes. On a `Read`, `Write`, or `Edit` block,
**`Ctrl+click` opens the file** that call named, at its line.
```

Keep the `Ctrl+click` sentence that follows it in the original if it is
not already folded into the replacement above — do not lose it.

- [ ] **Step 2: Add the CHANGELOG entry**

```markdown
- **One row per tool call.** The transcript gives a tool call a single
  line — what ran, a verdict digest (`3629 passed`, `+12 −3`,
  `no matches`), and the elapsed time in a right-hand column. Clicking it
  opens the full input and output in a scrollable window (`c` copy, `f`
  open as a file tab, `n`/`p` to step through the turn's calls). Replaces
  the folded result line, the six-row diff preview, and the inline
  argument expansion, which together cost up to a dozen rows per call.
```

- [ ] **Step 3: Flip the spec status**

In `docs/superpowers/specs/2026-09-18-one-line-tool-calls-design.md`,
change `*Status: not implemented.*` to `*Status: implemented <date>,
`<first-commit>`..`<last-commit>`.*` with the real short hashes from
Tasks 1-5.

- [ ] **Step 4: Run the full gate**

Run: `make check`
Expected: rc=0. Read the exit code directly, in its own tool call, with
nothing piped.

- [ ] **Step 5: Drive it in a real TUI**

Green tests against a daemon that booted before the change prove nothing
about the change (`AGENTS.md`). Start a fresh `aegis` attached to a daemon
started after these commits, give an agent a turn that makes it read a
file, run a command that fails, and edit something, then check by eye:

- every tool call is exactly one row, at a narrow terminal width too;
- the elapsed column lines up down the transcript;
- a failing command shows `✗` and its error text, not a progress bar;
- clicking opens the window; the output scrolls; `Esc` closes it;
- `n` walks the turn's calls; `c` copies; `f` opens a file tab;
- scroll up to a call from an earlier session (a replayed block) and
  click it — that path had no click at all before this change.

- [ ] **Step 6: Commit**

```bash
git add CHANGELOG.md docs/usage.md docs/superpowers/specs/2026-09-18-one-line-tool-calls-design.md
git commit -- CHANGELOG.md docs/usage.md docs/superpowers/specs/2026-09-18-one-line-tool-calls-design.md
```

Message: `docs(transcript): one-line tool calls in the changelog and usage`
