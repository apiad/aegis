# Live Task List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status: complete on the TUI** (2026-08-06). Tasks 1–10 implemented (`fff6f17`..`20ee0bd`); Task 13 in `7c4fec3`..`acfdbc4`. Two halves deliberately not built, both recorded in `TASKS.md` rather than left silent:

- **Task 11, group-dashboard roll-up — skipped.** The tab-bar suffix shipped in `20ee0bd`; `member_detail` did not, because `DashboardSnapshot` has no live data path and the helper would be dead code. Wiring that path is its own slice.
- **Task 12, the web strip and slide-over — deferred** at Alex's direction (2026-08-06), to finish the TUI end to end first. The TUI and PWA are co-equal per `AGENTS.md`, so this is a real debt, not a closed item.

Two defects that only appeared when the feature was driven through a real pane, both fixed with mutation-checked tests (`e056127`, `acfdbc4`): the surfaces did not fit their width (the one-line strip wrapped; every dock row was width+1), and the plan did not survive a restart (the tracker is per-process and nothing replayed it).

**Goal:** Make agent plan state first-class session state — parsed from every harness, timed in real time, and rendered as a live task list in the TUI, the web client, and the MCP coordination plane.

**Architecture:** Two layers. The parser (`events.py`) folds the `TaskCreate`/`TaskUpdate` delta family into the cumulative `AgentPlan` event it already emits for `TodoWrite`, so all three sources (claude legacy, claude current, ACP) produce one shape. A tracker (`src/aegis/plan/`) owned by `AgentSession` folds those events plus turn boundaries into a `PlanState` carrying per-task working time. Strip, dock, tab bar, `SessionInfo.plan` and `aegis_peer_plan` are all readers of that one tracker.

**Tech Stack:** Python 3.13+, Textual 8.x, Rich, FastMCP, pytest. `uv run` for everything.

**Spec:** `docs/superpowers/specs/2026-08-05-aegis-live-task-list-design.md`

## Global Constraints

- **Circle glyphs come from the existing maps**, never redefined: `render_shared.PLAN_STATUS_GLYPH` (Python) and `renderEvent.js:PLAN_GLYPH` (JS), both `{completed: "●", in_progress: "◐", pending: "○"}`.
- **No two circle glyphs may ever be adjacent in a rendered line.** These are East Asian Ambiguous width; Rich measures one cell, terminals draw wider, and neighbours visibly overlap. Always separate with a space.
- **Time is folded from supplied timestamps, never from a live clock read** inside the tracker. Every tracker method takes an explicit `ts: float`. This is what makes replay reproduce live numbers.
- **Working time only** — elapsed accrues solely while the session is mid-turn.
- Tests: `uv run python -m pytest`. Gate on a blast-radius subset while iterating; a failing test is a real failure, not flake.
- Commit per task, conventional commits, `git add` explicit paths only.
- English for all code, comments, identifiers, and commit messages.

---

## File Structure

**Create:**
- `src/aegis/plan/__init__.py` — re-exports `PlanTask`, `PlanState`, `PlanTracker`, `PlanSnapshot`.
- `src/aegis/plan/models.py` — `PlanTask`, `PlanState`, `PlanSnapshot` dataclasses. No behaviour beyond derived properties.
- `src/aegis/plan/tracker.py` — `PlanTracker`: folds `AgentPlan` + turn boundaries into `PlanState`.
- `src/aegis/plan/render.py` — pure `render_plan_strip()` / `render_plan_dock()` producing Rich `Text`. Pure so both TUI widgets and tests consume them.
- `src/aegis/tui/plan_strip.py` — `PlanStrip(Static)`, mirroring `tui/monitor_strip.py`.
- `src/aegis/tui/plan_dock.py` — `PlanDock(Widget)`, the toggled right panel.
- `tests/test_plan_parser.py`, `tests/test_plan_tracker.py`, `tests/test_plan_render.py`, `tests/test_plan_coordination.py`.

**Modify:**
- `src/aegis/events.py` — `PlanEntry` fields; `ParserState` plan accumulator; `Task*` branch; tool_result swallow.
- `src/aegis/state/event_codec.py:115,212` — encode/decode the new `PlanEntry` fields.
- `src/aegis/core/session.py` — `AgentSession` owns a `PlanTracker`.
- `src/aegis/core/manager.py:428` — populate `SessionInfo.plan`.
- `src/aegis/mcp/bridge.py` — `SessionInfo.plan` field.
- `src/aegis/mcp/server.py` — `aegis_peer_plan` tool + BRIEFING line.
- `src/aegis/tui/pane.py:921` — mount `PlanStrip`, wrap transcript for `PlanDock`, AgentPlan in-place replacement.
- `src/aegis/tui/app.py` — `F3` binding; `SessionInfo.plan` at its three construction sites.
- `src/aegis/tui/widgets.py` — TabBar `n/total`.
- `src/aegis/tui/groups/dashboard.py` — roll-up into member `detail`.
- `src/aegis/commands/builtins/session_ctl.py` — `/tasks` command.
- `src/aegis/web/static/js/renderEvent.js`, `app.js`, `index.html` — web strip + slide-over.

---

### Task 1: PlanEntry carries id and active_form

The `Task*` family has stable ids and a present-continuous label; `TodoWrite` and ACP have neither. Both new fields are optional so snapshot sources are unaffected.

**Files:**
- Modify: `src/aegis/events.py:127-134` (`PlanEntry`)
- Modify: `src/aegis/state/event_codec.py:115-120`, `212-218`
- Test: `tests/test_plan_parser.py` (create)

**Interfaces:**
- Produces: `PlanEntry(content: str, status: str, priority: str = "medium", id: str | None = None, active_form: str | None = None)`

- [x] **Step 1: Write the failing test**

```python
# tests/test_plan_parser.py
from aegis.events import AgentPlan, PlanEntry
from aegis.state.event_codec import decode_event, encode_event


def test_plan_entry_carries_id_and_active_form():
    e = PlanEntry(content="Write the spec", status="in_progress",
                  id="7", active_form="Writing the spec")
    assert e.id == "7"
    assert e.active_form == "Writing the spec"


def test_plan_entry_defaults_are_none_for_snapshot_sources():
    e = PlanEntry(content="x", status="pending")
    assert e.id is None and e.active_form is None


def test_codec_round_trips_new_plan_entry_fields():
    ev = AgentPlan(entries=(
        PlanEntry(content="a", status="completed", id="1",
                  active_form="Doing a"),
        PlanEntry(content="b", status="pending"),
    ))
    assert decode_event(encode_event(ev)) == ev
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_plan_parser.py -v`
Expected: FAIL — `TypeError: PlanEntry.__init__() got an unexpected keyword argument 'id'`

- [x] **Step 3: Add the fields**

In `src/aegis/events.py`, extend `PlanEntry`:

```python
@dataclass(frozen=True)
class PlanEntry:
    """One row of an AgentPlan. Status vocabulary follows ACP's
    PlanEntry.status enum (pending / in_progress / completed) so the
    same renderer can handle both ACP and claude TodoWrite sources."""
    content: str
    status: str            # pending / in_progress / completed
    priority: str = "medium"   # high / medium / low (default for claude)
    # Stable identifier, present only for claude's Task* family. Snapshot
    # sources (TodoWrite, ACP) resend a full ordered list each time and
    # have no identity to carry, so this stays None for them.
    id: str | None = None
    # Present-continuous label ("Writing the spec") the Task* tools supply
    # alongside the imperative subject. Used by the strip while in progress.
    active_form: str | None = None
```

In `src/aegis/state/event_codec.py`, the encoder at line 115 currently writes `content`/`status`/`priority` per entry. Add both fields, and read them back in the decoder at line 212:

```python
# encode (inside the AgentPlan branch)
"entries": [
    {"content": e.content, "status": e.status, "priority": e.priority,
     "id": e.id, "active_form": e.active_form}
    for e in ev.entries
],

# decode
entries = tuple(
    PlanEntry(content=d.get("content", ""),
              status=d.get("status", "pending"),
              priority=d.get("priority", "medium"),
              id=d.get("id"),
              active_form=d.get("active_form"))
    for d in (rec.get("entries") or [])
)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_plan_parser.py tests/test_agent_plan.py tests/test_state_event_codec.py -v`
Expected: PASS, including the pre-existing tests untouched.

- [x] **Step 5: Commit**

```bash
git add src/aegis/events.py src/aegis/state/event_codec.py tests/test_plan_parser.py
git commit -m "feat(plan): PlanEntry carries id and active_form"
```

---

### Task 2: The parser folds the Task* family into AgentPlan

The whole rendering complaint lives here. `events.py:424` special-cases only `TodoWrite`; `TaskCreate`/`TaskUpdate` fall through to a generic `ToolUse` whose summary is the first stringy argument — the literal string `"1"` for a `TaskUpdate`.

`TaskCreate` does not carry its own id: the id arrives in the tool *result* text as `Task #7 created successfully: <subject>`. The parser stashes the pending create by `tool_call_id` and backfills on the matching result, exactly as `state.tool_diffs` already pairs `Edit` results with their inputs.

`TaskList` and `TaskGet` are reads — they must NOT touch plan state and keep rendering as ordinary tool calls.

**Files:**
- Modify: `src/aegis/events.py:246-262` (`ParserState`), `418-436` (tool_use branch), `473-490` (tool_result branch)
- Test: `tests/test_plan_parser.py`

**Interfaces:**
- Consumes: `PlanEntry(..., id=, active_form=)` from Task 1.
- Produces: `parse()` returns a cumulative `AgentPlan` for `TaskCreate`/`TaskUpdate` tool_use blocks, and `None`-rendering `ContextUpdate()` for their confirmation results.

- [x] **Step 1: Write the failing test**

```python
# append to tests/test_plan_parser.py
import json
from aegis.events import AgentPlan, ContextUpdate, ParserState, ToolUse, parse


def _use(name, tool_input, tid):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": name, "input": tool_input}]}})


def _result(text, tid):
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text}]}})


def test_task_create_becomes_a_cumulative_plan():
    st = ParserState()
    ev = parse(_use("TaskCreate", {"subject": "Explore", "description": "d",
                                   "activeForm": "Exploring"}, "t1"), st)
    assert isinstance(ev, AgentPlan)
    assert [e.content for e in ev.entries] == ["Explore"]
    assert ev.entries[0].status == "pending"
    assert ev.entries[0].active_form == "Exploring"

    ev = parse(_use("TaskCreate", {"subject": "Write", "description": "d"},
                    "t2"), st)
    assert [e.content for e in ev.entries] == ["Explore", "Write"]


def test_task_create_id_is_backfilled_from_the_result():
    st = ParserState()
    parse(_use("TaskCreate", {"subject": "Explore", "description": "d"}, "t1"),
          st)
    parse(_result("Task #7 created successfully: Explore", "t1"), st)
    ev = parse(_use("TaskUpdate", {"taskId": "7",
                                   "status": "in_progress"}, "t2"), st)
    assert isinstance(ev, AgentPlan)
    assert ev.entries[0].status == "in_progress"
    assert ev.entries[0].id == "7"


def test_task_confirmation_result_is_swallowed_not_orphaned():
    """The parser returns AgentPlan instead of a ToolUse, so the matching
    result has nothing to fold into and would mount standalone — one noise
    block per task. ContextUpdate renders as None."""
    st = ParserState()
    parse(_use("TaskCreate", {"subject": "Explore", "description": "d"}, "t1"),
          st)
    ev = parse(_result("Task #7 created successfully: Explore", "t1"), st)
    assert isinstance(ev, ContextUpdate)


def test_task_update_can_delete():
    st = ParserState()
    parse(_use("TaskCreate", {"subject": "A", "description": "d"}, "t1"), st)
    parse(_result("Task #1 created successfully: A", "t1"), st)
    ev = parse(_use("TaskUpdate", {"taskId": "1", "status": "deleted"}, "t2"),
               st)
    assert ev.entries == ()


def test_task_list_and_get_are_reads_and_stay_tool_calls():
    st = ParserState()
    parse(_use("TaskCreate", {"subject": "A", "description": "d"}, "t1"), st)
    ev = parse(_use("TaskList", {}, "t2"), st)
    assert isinstance(ev, ToolUse)
    ev = parse(_use("TaskGet", {"taskId": "1"}, "t3"), st)
    assert isinstance(ev, ToolUse)


def test_todowrite_snapshot_path_is_unchanged():
    st = ParserState()
    ev = parse(_use("TodoWrite", {"todos": [
        {"content": "a", "status": "completed"},
        {"content": "b", "status": "pending"}]}, "t1"), st)
    assert isinstance(ev, AgentPlan)
    assert [e.status for e in ev.entries] == ["completed", "pending"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_plan_parser.py -v`
Expected: FAIL — `test_task_create_becomes_a_cumulative_plan` asserts `isinstance(ev, AgentPlan)` but gets `ToolUse`.

- [x] **Step 3: Implement the accumulator**

In `src/aegis/events.py`, add to `ParserState` (after `tool_diffs`):

```python
    # Accumulated Task* plan state. claude's current task tools speak in
    # deltas with ids, unlike TodoWrite/ACP which resend a full snapshot,
    # so the parser folds them here and emits a cumulative AgentPlan.
    # Ordered by insertion: dict preserves creation order, which is the
    # order the agent means.
    plan_tasks: dict[str, dict] = field(default_factory=dict)
    # tool_call_id → plan_tasks key, for a TaskCreate whose real id has not
    # arrived yet (it comes in the tool_result, never the tool_use).
    plan_pending: dict[str, str] = field(default_factory=dict)
    # Monotonic counter for provisional keys before the real id lands.
    plan_seq: int = 0
```

Add a module-level helper near `_summarize_tool`:

```python
_TASK_CREATED_RE = re.compile(r"Task #(\S+) created successfully")


def _plan_entries(state: ParserState) -> tuple[PlanEntry, ...]:
    """Snapshot the accumulated Task* plan as canonical PlanEntry rows."""
    return tuple(
        PlanEntry(content=t["subject"], status=t["status"],
                  id=t.get("id"), active_form=t.get("active_form"))
        for t in state.plan_tasks.values()
    )
```

`re` is already imported in `events.py`; confirm before adding.

In the `tool_use` branch, immediately after the existing `TodoWrite` block (`events.py:436`), add:

```python
            if name == "TaskCreate":
                state.plan_seq += 1
                key = f"pending:{state.plan_seq}"
                state.plan_tasks[key] = {
                    "id": None,
                    "subject": str(tool_input.get("subject", "")),
                    "status": "pending",
                    "active_form": tool_input.get("activeForm")
                        if isinstance(tool_input.get("activeForm"), str)
                        else None,
                }
                if tool_call_id := block.get("id"):
                    state.plan_pending[tool_call_id] = key
                    # Remember so the confirmation result can be swallowed
                    # rather than mounting as an orphan block.
                    state.tool_kinds[tool_call_id] = "plan"
                return AgentPlan(entries=_plan_entries(state))

            if name == "TaskUpdate":
                task_id = str(tool_input.get("taskId", ""))
                key = next(
                    (k for k, t in state.plan_tasks.items()
                     if t.get("id") == task_id), None)
                if key is not None:
                    status = tool_input.get("status")
                    if status == "deleted":
                        state.plan_tasks.pop(key, None)
                    else:
                        t = state.plan_tasks[key]
                        if isinstance(status, str):
                            t["status"] = status
                        if isinstance(tool_input.get("subject"), str):
                            t["subject"] = tool_input["subject"]
                        if isinstance(tool_input.get("activeForm"), str):
                            t["active_form"] = tool_input["activeForm"]
                if tool_call_id := block.get("id"):
                    state.tool_kinds[tool_call_id] = "plan"
                return AgentPlan(entries=_plan_entries(state))
```

In the `tool_result` branch (`events.py:477`), before building the `ToolResult`, add:

```python
                    if tcid and state.tool_kinds.get(tcid) == "plan":
                        key = state.plan_pending.pop(tcid, None)
                        if key is not None and key in state.plan_tasks:
                            if m := _TASK_CREATED_RE.search(text):
                                state.plan_tasks[key]["id"] = m.group(1)
                        # The matching tool_use became an AgentPlan, so this
                        # result has nothing to fold into and would mount as
                        # a standalone block (pane.py:1878). ContextUpdate
                        # renders as None — the documented no-op event.
                        return ContextUpdate()
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_plan_parser.py tests/test_agent_plan.py tests/test_events.py -v`
Expected: PASS.

- [x] **Step 5: Verify against a real recorded log**

The repo's own state dir carries real `Task*` traffic. Confirm the fold produces a sane plan end to end:

```bash
uv run python -c "
import json, glob
from aegis.events import AgentPlan, ParserState, parse
st = ParserState()
last = None
for p in sorted(glob.glob('.aegis/state/sessions/*.jsonl'))[-1:]:
    print(p)
print('parser wired; run against a live session to eyeball')
"
```

Then run `aegis`, ask an agent for a multi-step task, and confirm the transcript shows plan blocks rather than `⏺ TaskUpdate(1)`.

- [x] **Step 6: Commit**

```bash
git add src/aegis/events.py tests/test_plan_parser.py
git commit -m "feat(plan): fold the Task* family into cumulative AgentPlan events"
```

---

### Task 3: PlanTask, PlanState and the working-time tracker

Pure domain layer, no Textual and no I/O. Every method takes an explicit `ts` so replay reproduces live numbers exactly.

**Files:**
- Create: `src/aegis/plan/__init__.py`, `src/aegis/plan/models.py`, `src/aegis/plan/tracker.py`
- Test: `tests/test_plan_tracker.py`

**Interfaces:**
- Consumes: `AgentPlan`, `PlanEntry` from Task 1/2.
- Produces:
  - `PlanTask(key: str, subject: str, status: str, active_form: str | None, working_s: float | None)` — `working_s is None` means never started.
  - `PlanState(tasks: tuple[PlanTask, ...])` with properties `done: int`, `total: int`, `current: PlanTask | None`.
  - `PlanSnapshot(done: int, total: int, current: str | None, current_working_s: float | None, updated_at: str | None)`.
  - `PlanTracker()` with `apply_plan(plan: AgentPlan, ts: float) -> None`, `set_working(working: bool, ts: float) -> None`, `snapshot(ts: float) -> PlanState`, `roll_up(ts: float) -> PlanSnapshot`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_plan_tracker.py
from aegis.events import AgentPlan, PlanEntry
from aegis.plan import PlanTracker


def plan(*pairs):
    return AgentPlan(entries=tuple(
        PlanEntry(content=c, status=s) for c, s in pairs))


def test_total_done_and_current():
    t = PlanTracker()
    t.apply_plan(plan(("a", "completed"), ("b", "in_progress"),
                      ("c", "pending")), ts=0.0)
    st = t.snapshot(ts=0.0)
    assert (st.done, st.total) == (1, 3)
    assert st.current.subject == "b"


def test_working_time_accrues_only_while_mid_turn():
    t = PlanTracker()
    t.set_working(True, ts=0.0)
    t.apply_plan(plan(("a", "in_progress")), ts=0.0)
    t.set_working(False, ts=10.0)          # turn ends, 10s of work
    st = t.snapshot(ts=1000.0)             # long idle gap
    assert st.tasks[0].working_s == 10.0


def test_idle_gap_contributes_nothing():
    t = PlanTracker()
    t.apply_plan(plan(("a", "in_progress")), ts=0.0)   # not working
    st = t.snapshot(ts=500.0)
    assert st.tasks[0].working_s == 0.0


def test_re_entering_in_progress_resumes_rather_than_restarts():
    t = PlanTracker()
    t.set_working(True, ts=0.0)
    t.apply_plan(plan(("a", "in_progress")), ts=0.0)
    t.apply_plan(plan(("a", "completed")), ts=6.0)     # 6s banked
    t.apply_plan(plan(("a", "in_progress")), ts=6.0)   # reopened
    st = t.snapshot(ts=10.0)
    assert st.tasks[0].working_s == 10.0               # not 4.0


def test_never_started_task_reports_none_not_zero():
    t = PlanTracker()
    t.set_working(True, ts=0.0)
    t.apply_plan(plan(("a", "pending")), ts=0.0)
    assert t.snapshot(ts=9.0).tasks[0].working_s is None


def test_task_completed_without_ever_being_in_progress_reports_none():
    t = PlanTracker()
    t.set_working(True, ts=0.0)
    t.apply_plan(plan(("a", "completed")), ts=0.0)
    assert t.snapshot(ts=9.0).tasks[0].working_s is None


def test_live_snapshot_includes_time_since_last_transition():
    t = PlanTracker()
    t.set_working(True, ts=0.0)
    t.apply_plan(plan(("a", "in_progress")), ts=0.0)
    assert t.snapshot(ts=4.0).tasks[0].working_s == 4.0


def test_roll_up_carries_the_current_task():
    t = PlanTracker()
    t.set_working(True, ts=0.0)
    t.apply_plan(plan(("a", "completed"), ("b", "in_progress")), ts=0.0)
    r = t.roll_up(ts=3.0)
    assert (r.done, r.total, r.current) == (1, 2, "b")
    assert r.current_working_s == 3.0
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_plan_tracker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.plan'`

- [x] **Step 3: Write the models**

```python
# src/aegis/plan/models.py
"""Plan domain types. No Textual, no I/O, no clock reads — the tracker
is handed every timestamp so a replayed log reproduces live numbers."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanTask:
    """One task with its accumulated working time.

    ``working_s is None`` means the task never entered in_progress, which
    renders as "—". That is deliberately distinct from 0.0, which would
    claim the task ran instantly."""
    key: str
    subject: str
    status: str
    active_form: str | None = None
    working_s: float | None = None

    @property
    def label(self) -> str:
        """Present-continuous while in progress, imperative otherwise."""
        if self.status == "in_progress" and self.active_form:
            return self.active_form
        return self.subject


@dataclass(frozen=True)
class PlanState:
    tasks: tuple[PlanTask, ...] = ()

    @property
    def total(self) -> int:
        return len(self.tasks)

    @property
    def done(self) -> int:
        return sum(1 for t in self.tasks if t.status == "completed")

    @property
    def current(self) -> PlanTask | None:
        return next((t for t in self.tasks if t.status == "in_progress"), None)

    def __bool__(self) -> bool:
        return bool(self.tasks)


@dataclass(frozen=True)
class PlanSnapshot:
    """The small roll-up the coordination plane carries on SessionInfo."""
    done: int = 0
    total: int = 0
    current: str | None = None
    current_working_s: float | None = None
    updated_at: str | None = None
```

```python
# src/aegis/plan/__init__.py
from aegis.plan.models import PlanSnapshot, PlanState, PlanTask
from aegis.plan.tracker import PlanTracker

__all__ = ["PlanSnapshot", "PlanState", "PlanTask", "PlanTracker"]
```

- [x] **Step 4: Write the tracker**

```python
# src/aegis/plan/tracker.py
"""PlanTracker — folds AgentPlan events plus turn boundaries into a
PlanState carrying per-task working time.

Working time, not wall-clock: a session idles between turns, and a task
left in_progress overnight would otherwise report nine hours for one
minute of work. Elapsed accrues only while the session is mid-turn,
which is also exactly when the strip's in-progress circle spins.
"""
from __future__ import annotations

from datetime import UTC, datetime

from aegis.events import AgentPlan
from aegis.plan.models import PlanSnapshot, PlanState, PlanTask


class PlanTracker:
    def __init__(self) -> None:
        # key → mutable record. Insertion-ordered, which is plan order.
        self._tasks: dict[str, dict] = {}
        self._working = False
        # Key currently accruing time, and the ts it started accruing.
        # Non-None only while status is in_progress AND the session is
        # mid-turn — the conjunction is the whole definition of the metric.
        self._accruing: str | None = None
        self._since: float = 0.0
        self._updated_at: str | None = None

    # -- internals ---------------------------------------------------

    def _flush(self, ts: float) -> None:
        """Bank elapsed time into whichever task was accruing."""
        if self._accruing is not None:
            rec = self._tasks.get(self._accruing)
            if rec is not None:
                rec["working_s"] = (rec["working_s"] or 0.0) + (ts - self._since)
        self._accruing = None

    def _rearm(self, ts: float) -> None:
        """Start accruing again if a task is in progress and we're working."""
        if not self._working:
            return
        cur = next((k for k, r in self._tasks.items()
                    if r["status"] == "in_progress"), None)
        if cur is not None:
            self._accruing = cur
            self._since = ts

    @staticmethod
    def _key(entry, index: int) -> str:
        """Task* entries have stable ids. Snapshot sources (TodoWrite, ACP)
        have none, so fall back to position plus subject — which is what
        resending a full ordered list already implies."""
        return entry.id if entry.id else f"{index}:{entry.content}"

    # -- surface -----------------------------------------------------

    def apply_plan(self, plan: AgentPlan, ts: float) -> None:
        self._flush(ts)
        seen: dict[str, dict] = {}
        for i, entry in enumerate(plan.entries):
            key = self._key(entry, i)
            prev = self._tasks.get(key)
            started = bool(prev and prev["started"]) \
                or entry.status == "in_progress"
            seen[key] = {
                "subject": entry.content,
                "status": entry.status,
                "active_form": entry.active_form
                    or (prev or {}).get("active_form"),
                # Carried across revisions: a task that goes in_progress →
                # completed → in_progress resumes its accumulator.
                "working_s": (prev or {}).get("working_s"),
                "started": started,
            }
            if started and seen[key]["working_s"] is None:
                seen[key]["working_s"] = 0.0
        self._tasks = seen
        self._updated_at = datetime.now(UTC).isoformat()
        self._rearm(ts)

    def set_working(self, working: bool, ts: float) -> None:
        if working == self._working:
            return
        self._flush(ts)
        self._working = working
        self._rearm(ts)

    def snapshot(self, ts: float) -> PlanState:
        live = ts - self._since if self._accruing is not None else 0.0
        return PlanState(tasks=tuple(
            PlanTask(
                key=key,
                subject=rec["subject"],
                status=rec["status"],
                active_form=rec["active_form"],
                working_s=None if not rec["started"] else
                    (rec["working_s"] or 0.0) + (live if key == self._accruing
                                                 else 0.0),
            )
            for key, rec in self._tasks.items()
        ))

    def roll_up(self, ts: float) -> PlanSnapshot:
        st = self.snapshot(ts)
        cur = st.current
        return PlanSnapshot(
            done=st.done, total=st.total,
            current=cur.subject if cur else None,
            current_working_s=cur.working_s if cur else None,
            updated_at=self._updated_at,
        )

    @property
    def working(self) -> bool:
        """True while the session is mid-turn — the strip spins on this."""
        return self._working
```

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_plan_tracker.py -v`
Expected: PASS, all nine.

- [x] **Step 6: Commit**

```bash
git add src/aegis/plan/ tests/test_plan_tracker.py
git commit -m "feat(plan): PlanTracker with working-time accounting"
```

---

### Task 4: Replay equivalence

The property that matters most, and the one that catches a stray `now()` creeping into the tracker later. Folding a recorded log must produce the same `PlanState` the live session held.

**Files:**
- Test: `tests/test_plan_tracker.py` (append)

**Interfaces:**
- Consumes: `PlanTracker` from Task 3.

- [x] **Step 1: Write the failing test**

```python
# append to tests/test_plan_tracker.py
def test_replaying_the_same_event_sequence_reproduces_the_state():
    """Live and replayed folds must agree. If the tracker ever reads a
    real clock instead of the ts it is handed, this is what fails."""
    script = [
        ("working", True, 0.0),
        ("plan", plan(("a", "in_progress"), ("b", "pending")), 0.0),
        ("working", False, 12.0),
        ("working", True, 900.0),                       # long idle gap
        ("plan", plan(("a", "completed"), ("b", "in_progress")), 903.0),
        ("working", False, 910.0),
    ]

    def fold():
        t = PlanTracker()
        for kind, value, ts in script:
            if kind == "working":
                t.set_working(value, ts=ts)
            else:
                t.apply_plan(value, ts=ts)
        return t.snapshot(ts=2000.0)

    live, replayed = fold(), fold()
    assert live == replayed
    assert live.tasks[0].working_s == 15.0    # 12 + 3, idle gap excluded
    assert live.tasks[1].working_s == 7.0
```

- [x] **Step 2: Run test to verify it fails or passes**

Run: `uv run python -m pytest tests/test_plan_tracker.py::test_replaying_the_same_event_sequence_reproduces_the_state -v`
Expected: PASS if Task 3 is correct. **If it fails, the tracker has a clock read or a carry-over bug — fix the tracker, not the test.**

- [x] **Step 3: Mutation-check the gate**

A test that cannot fail is worth less than none. Temporarily break the tracker to prove the test bites:

```bash
# In tracker.py _flush, change (ts - self._since) to (ts - self._since) * 2
uv run python -m pytest tests/test_plan_tracker.py -v   # MUST fail
# revert
```

- [x] **Step 4: Commit**

```bash
git add tests/test_plan_tracker.py
git commit -m "test(plan): replay equivalence for working-time folding"
```

---

### Task 5: The pure strip and dock renderers

Pure functions producing Rich `Text`, so widgets stay dumb and tests assert on strings.

**Files:**
- Create: `src/aegis/plan/render.py`
- Test: `tests/test_plan_render.py`

**Interfaces:**
- Consumes: `PlanState`, `PlanTask` from Task 3; `PLAN_STATUS_GLYPH` from `render_shared`.
- Produces:
  - `render_plan_strip(state: PlanState, palette, *, working: bool = False, frame: int = 0, cap: int = 12) -> Text`
  - `render_plan_dock(state: PlanState, palette, *, working: bool = False, frame: int = 0, width: int = 24) -> Text`
  - `fmt_working(seconds: float | None) -> str`
  - `SPINNER_FRAMES: str`

- [x] **Step 1: Write the failing test**

```python
# tests/test_plan_render.py
import re

from aegis.plan import PlanState, PlanTask
from aegis.plan.render import (
    SPINNER_FRAMES, fmt_working, render_plan_dock, render_plan_strip,
)
from aegis.tui.themes import INK, aegis_colors

C = aegis_colors(INK)          # the house pattern — see tests/test_render_event.py
CIRCLES = "●◐○◓◑◒"


def as_text(renderable) -> str:
    """`.plain` rather than a Rich Console: the adjacency assertion below
    must see the exact string the renderer produced, and a Console would
    introduce wrapping and padding of its own."""
    return renderable.plain


def state(*pairs, times=None):
    times = times or {}
    return PlanState(tasks=tuple(
        PlanTask(key=str(i), subject=s, status=st,
                 working_s=times.get(s))
        for i, (s, st) in enumerate(pairs)))


def test_no_two_circles_are_ever_adjacent():
    """These glyphs are East Asian Ambiguous width: Rich measures one cell,
    terminals draw wider, so neighbours overlap. This is the regression
    guard — a future surface cannot quietly reintroduce it."""
    s = state(*[(f"t{i}", "completed") for i in range(5)])
    for out in (as_text(render_plan_strip(s, C)),
                as_text(render_plan_dock(s, C))):
        assert not re.search(f"[{CIRCLES}][{CIRCLES}]", out), out


def test_strip_has_one_circle_per_task_in_plan_order():
    s = state(("a", "completed"), ("b", "completed"),
              ("c", "in_progress"), ("d", "pending"))
    out = as_text(render_plan_strip(s, C))
    assert "● ● ◐ ○" in out
    assert "2/4" in out


def test_strip_shows_the_current_task_label_and_clock():
    s = state(("a", "completed"), ("b", "in_progress"), times={"b": 63.0})
    out = as_text(render_plan_strip(s, C))
    assert "b" in out and "1:03" in out


def test_strip_windows_around_the_current_task_past_the_cap():
    """31 tasks, current at index 13, cap 12 → window is tasks[7:19], so
    BOTH sides elide. The count stays honest at 13/31 regardless."""
    pairs = [(f"t{i}", "completed") for i in range(13)]
    pairs.append(("cur", "in_progress"))
    pairs += [(f"u{i}", "pending") for i in range(17)]
    out = as_text(render_plan_strip(state(*pairs), C, cap=12))
    assert out.count("…") == 2
    assert len(re.findall(f"[{CIRCLES}]", out)) == 12
    assert "13/31" in out


def test_window_elides_only_the_side_that_needs_it():
    """Current task near the start: nothing to elide on the left."""
    pairs = [("cur", "in_progress")]
    pairs += [(f"u{i}", "pending") for i in range(20)]
    out = as_text(render_plan_strip(state(*pairs), C, cap=12))
    assert out.count("…") == 1
    assert out.index("…") > out.index("○")   # trailing, not leading


def test_empty_plan_renders_empty():
    assert as_text(render_plan_strip(PlanState(), C)) == ""


def test_spinner_advances_only_when_working():
    s = state(("a", "in_progress"))
    still = as_text(render_plan_strip(s, C, working=False, frame=0))
    assert "◐" in still
    frames = {as_text(render_plan_strip(s, C, working=True, frame=f))
              for f in range(len(SPINNER_FRAMES))}
    assert len(frames) == len(SPINNER_FRAMES)


def test_never_started_task_shows_a_dash_not_a_zero():
    s = state(("a", "pending"))
    assert "—" in as_text(render_plan_dock(s, C))


def test_fmt_working():
    assert fmt_working(None) == "—"
    assert fmt_working(0.0) == "0:00"
    assert fmt_working(63.0) == "1:03"
    assert fmt_working(3723.0) == "1:02:03"


def test_dock_row_per_task_with_glyph_and_time():
    s = state(("explore", "completed"), ("clarify", "in_progress"),
              times={"explore": 252.0, "clarify": 63.0})
    out = as_text(render_plan_dock(s, C))
    assert "● explore" in out and "4:12" in out
    assert "◐ clarify" in out and "1:03" in out
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_plan_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.plan.render'`

- [x] **Step 3: Write the renderers**

```python
# src/aegis/plan/render.py
"""Pure plan renderers. Widgets stay dumb; these produce Rich Text and
are asserted directly in tests.

Circle glyphs come from render_shared.PLAN_STATUS_GLYPH — never a second
vocabulary — and are ALWAYS space-separated. They are East Asian
Ambiguous width: Rich measures one cell, many terminals draw the glyph
wider, and adjacent circles visibly overlap.
"""
from __future__ import annotations

from rich.text import Text

from aegis.plan.models import PlanState
from aegis.render_shared import PLAN_STATUS_GLYPH

# Rotating half-fill, in the same idiom as the existing _TOOL_SPINNER.
# It advances only while the session is mid-turn, which is exactly when
# working time accrues — the spin IS the clock running.
SPINNER_FRAMES = "◐◓◑◒"

_STRIP_LABEL = "tasks: "


def fmt_working(seconds: float | None) -> str:
    """A task that never entered in_progress reads "—", not "0:00" — the
    two mean different things and must not look alike."""
    if seconds is None:
        return "—"
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def _glyph(task, working: bool, frame: int) -> str:
    if task.status == "in_progress" and working:
        return SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]
    return PLAN_STATUS_GLYPH.get(task.status, "○")


def _style(task, colors):
    return (colors.ok if task.status == "completed"
            else colors.accent if task.status == "in_progress"
            else colors.muted)


def _window(state: PlanState, cap: int) -> tuple[list, bool, bool]:
    """One circle per task up to `cap`, windowed around the current task.
    Returns (tasks, elided_left, elided_right)."""
    tasks = list(state.tasks)
    if len(tasks) <= cap:
        return tasks, False, False
    cur = next((i for i, t in enumerate(tasks)
                if t.status == "in_progress"), 0)
    start = max(0, min(cur - cap // 2, len(tasks) - cap))
    return tasks[start:start + cap], start > 0, start + cap < len(tasks)


def render_plan_strip(state: PlanState, colors, *, working: bool = False,
                      frame: int = 0, cap: int = 12) -> Text:
    """One line: circle strip, count, current task, working clock."""
    out = Text()
    if not state:
        return out
    out.append(_STRIP_LABEL, style=colors.muted)
    window, left, right = _window(state, cap)
    if left:
        out.append("…", style=colors.muted)
    for i, task in enumerate(window):
        if i:
            out.append(" ")          # never adjacent — see module docstring
        out.append(_glyph(task, working, frame), style=_style(task, colors))
    if right:
        out.append("…", style=colors.muted)
    out.append(f"  {state.done}/{state.total}", style=colors.ink)
    if cur := state.current:
        out.append(" · ", style=colors.muted)
        out.append(cur.label, style=colors.ink)
        out.append(f" {fmt_working(cur.working_s)}", style=colors.muted)
    return out


def render_plan_dock(state: PlanState, colors, *, working: bool = False,
                     frame: int = 0, width: int = 24) -> Text:
    """One row per task: glyph, label, working time."""
    out = Text()
    if not state:
        return Text("(no plan)", style=colors.muted)
    out.append(f"tasks {state.done}/{state.total}\n",
               style=f"bold {colors.accent}")
    label_w = max(8, width - 8)
    for task in state.tasks:
        out.append(_glyph(task, working, frame), style=_style(task, colors))
        label = task.label
        if len(label) > label_w:
            label = label[:label_w - 1] + "…"
        out.append(f" {label:<{label_w}} ", style=colors.ink)
        out.append(f"{fmt_working(task.working_s):>6}\n", style=colors.muted)
    return out
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_plan_render.py -v`
Expected: PASS, all nine.

- [x] **Step 5: Mutation-check the overlap guard**

The single most important assertion in this plan. Prove it bites:

```bash
# In render.py render_plan_strip, delete the `out.append(" ")` separator
uv run python -m pytest tests/test_plan_render.py::test_no_two_circles_are_ever_adjacent -v
# MUST fail. Then restore.
```

- [x] **Step 6: Commit**

```bash
git add src/aegis/plan/render.py tests/test_plan_render.py
git commit -m "feat(plan): pure strip and dock renderers with circle vocabulary"
```

---

### Task 6: AgentSession owns a PlanTracker

Plan state belongs to the session core, not a TUI widget — that is what lets the MCP plane and the web client read it.

**Files:**
- Modify: `src/aegis/core/session.py` (`AgentSession.__init__`, event and state observer paths)
- Test: `tests/test_plan_tracker.py` (append)

**Interfaces:**
- Consumes: `PlanTracker` from Task 3.
- Produces: `AgentSession.plan: PlanTracker` (top-level), `AgentSession.subplans: dict[str, PlanTracker]` (keyed by `parent_tool_use_id`), `AgentSession.plan_state() -> PlanState`, `AgentSession.plan_roll_up() -> PlanSnapshot`, `AgentSession.subplan_states() -> dict[str, PlanState]`.

**Routing matters here.** A subagent dispatched via `Task`/`Agent` keeps its own task list, and its `AgentPlan` events carry `parent_tool_use_id`. Folding those into the same tracker would let a subagent's three-item plan *overwrite* the main agent's eight-item one — the strip would show 1/3 while the real plan is 5/8. Route by `parent_tool_use_id`: `None` goes to the top-level tracker, anything else to a per-parent tracker.

- [x] **Step 1: Write the failing test**

```python
# append to tests/test_plan_tracker.py
from aegis.events import AgentPlan, PlanEntry


def _entries(*pairs):
    return tuple(PlanEntry(content=c, status=s) for c, s in pairs)


def test_agent_session_folds_plans_into_its_tracker(session):
    session._on_event(AgentPlan(entries=_entries(
        ("a", "completed"), ("b", "in_progress"))))
    st = session.plan_state()
    assert (st.done, st.total) == (1, 2)
    assert session.plan_roll_up().current == "b"


def test_a_subagent_plan_does_not_overwrite_the_top_level_plan(session):
    """The failure this guards: a 3-item subagent plan replacing the
    parent's 8-item one, so the strip reads 1/3 while the real plan is
    5/8."""
    session._on_event(AgentPlan(entries=_entries(
        ("a", "completed"), ("b", "in_progress"), ("c", "pending"))))
    session._on_event(AgentPlan(
        entries=_entries(("sub", "in_progress")),
        parent_tool_use_id="tool_1"))

    assert session.plan_state().total == 3          # unchanged
    assert session.subplan_states()["tool_1"].total == 1
```

There is no shared session fixture in `tests/conftest.py` (it carries `isolated_project_dir`, the queue mocks and `workflow_context`). Add a local `session` fixture to `tests/test_plan_tracker.py` that builds an `AgentSession` over the same fake harness `tests/test_core_session.py` already uses — read that module and copy its construction rather than inventing a second fake.

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_plan_tracker.py -k agent_session -v`
Expected: FAIL — `AttributeError: 'AgentSession' object has no attribute 'plan_state'`

- [x] **Step 3: Wire the tracker**

In `src/aegis/core/session.py`, in `AgentSession.__init__`:

```python
        from aegis.plan import PlanTracker
        # Plan state is session state, not view state: the TUI strip, the
        # web client, and the MCP coordination plane are all readers.
        self.plan = PlanTracker()
        # Subagent plans, keyed by the dispatching Task tool_use id. Kept
        # apart from the top-level plan so a subagent's short list cannot
        # overwrite its parent's.
        self.subplans: dict[str, PlanTracker] = {}
```

In the event-dispatch path (where events reach observers), fold `AgentPlan`, routing by parent:

```python
        from aegis.events import AgentPlan
        if isinstance(ev, AgentPlan):
            now = time.monotonic()
            if ev.parent_tool_use_id is None:
                self.plan.apply_plan(ev, ts=now)
            else:
                tracker = self.subplans.get(ev.parent_tool_use_id)
                if tracker is None:
                    tracker = self.subplans[ev.parent_tool_use_id] = \
                        PlanTracker()
                    tracker.set_working(self.plan.working, ts=now)
                tracker.apply_plan(ev, ts=now)
```

In `_emit_state`, mirror turn state into every tracker so working time accrues only mid-turn:

```python
        now = time.monotonic()
        working = state.value == "working"
        self.plan.set_working(working, ts=now)
        for tracker in self.subplans.values():
            tracker.set_working(working, ts=now)
```

Add the read helpers:

```python
    def plan_state(self):
        return self.plan.snapshot(ts=time.monotonic())

    def plan_roll_up(self):
        return self.plan.roll_up(ts=time.monotonic())

    def subplan_states(self) -> dict:
        now = time.monotonic()
        return {k: t.snapshot(ts=now) for k, t in self.subplans.items()}
```

`time.monotonic()` is correct here and not a violation of the no-clock rule: the *tracker* never reads a clock, the caller supplies it. Monotonic is required — wall-clock jumps would corrupt durations.

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_plan_tracker.py tests/test_core_session.py -v`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/aegis/core/session.py tests/test_plan_tracker.py
git commit -m "feat(plan): AgentSession owns a PlanTracker"
```

---

### Task 7: PlanStrip in the conversation pane

The always-on surface. Mirrors `tui/monitor_strip.py` exactly.

**Files:**
- Create: `src/aegis/tui/plan_strip.py`
- Modify: `src/aegis/tui/pane.py:921-936` (compose), plus the event/state observer methods
- Test: `tests/test_plan_render.py` (append a widget smoke test)

**Interfaces:**
- Consumes: `render_plan_strip` (Task 5), `AgentSession.plan` (Task 6).
- Produces: `PlanStrip(Static)` with `refresh_plan(state: PlanState, working: bool) -> None`.

- [x] **Step 1: Write the widget**

```python
# src/aegis/tui/plan_strip.py
"""PlanStrip — always-on, one-line plan summary.

Sits above the status bar alongside QueueStrip and MonitorStrip, and is
hidden when the session has no plan. The spinner ticks only while the
session is mid-turn, which is the same condition under which working
time accrues.
"""
from __future__ import annotations

from textual.widgets import Static

from aegis.plan.models import PlanState
from aegis.plan.render import render_plan_strip

_TICK = 0.25


class PlanStrip(Static):
    """Hidden (display:none) until the session has a plan."""

    def __init__(self, palette, **kw) -> None:
        super().__init__("", **kw)
        self._palette = palette
        self._state = PlanState()
        self._working = False
        self._frame = 0
        self.display = False

    def on_mount(self) -> None:
        self.set_interval(_TICK, self._tick)

    def _tick(self) -> None:
        # Only repaint while working: a settled plan is a static line and
        # must not burn a redraw four times a second.
        if self._working and self._state.current is not None:
            self._frame += 1
            self._paint()

    def refresh_plan(self, state: PlanState, working: bool) -> None:
        self._state, self._working = state, working
        self.display = bool(state)
        self._paint()

    def _paint(self) -> None:
        self.update(render_plan_strip(
            self._state, self._palette,
            working=self._working, frame=self._frame))
```

- [x] **Step 2: Mount it in the pane**

In `src/aegis/tui/pane.py`, import `PlanStrip` and add it to `compose()` beside the other strips:

```python
    def compose(self) -> ComposeResult:
        with Vertical():
            yield VerticalScroll(id="transcript")
            with Horizontal(id="strips"):
                yield QueueStrip(self._digest, self._palette)
                yield MonitorStrip(self._monitor_manager, self._palette,
                                   ...)
            yield PlanStrip(self._palette, id="plan-strip")
            yield StatusBar(_model, _eff, self._palette)
            ...
```

Match the actual existing compose body — the snippet above elides the real
QueueStrip/MonitorStrip arguments. Insert `PlanStrip` directly before
`StatusBar` and leave every other line untouched.

Then refresh it wherever the pane already reacts to events and state. In the
event handler, after the existing dispatch:

```python
        if isinstance(ev, AgentPlan):
            self._refresh_plan_surfaces()
```

and in `_on_core_state` (which already toggles the `.working` class):

```python
        self._refresh_plan_surfaces()
```

with:

```python
    def _refresh_plan_surfaces(self) -> None:
        sess = self._session
        if sess is None:
            return
        state = sess.plan_state()
        working = sess.plan.working
        self.query_one("#plan-strip", PlanStrip).refresh_plan(state, working)
```

- [x] **Step 3: Write the smoke test**

```python
# append to tests/test_plan_render.py
import pytest

from aegis.tui.plan_strip import PlanStrip


@pytest.mark.asyncio
async def test_plan_strip_hides_when_there_is_no_plan():
    from textual.app import App, ComposeResult

    class _A(App):
        def compose(self) -> ComposeResult:
            yield PlanStrip(C, id="plan-strip")

    async with _A().run_test() as pilot:
        strip = pilot.app.query_one("#plan-strip", PlanStrip)
        assert strip.display is False
        strip.refresh_plan(state(("a", "in_progress")), working=True)
        assert strip.display is True
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_plan_render.py -v`
Expected: PASS.

- [x] **Step 5: Verify in the real TUI**

A widget test proves the widget; it does not prove the wiring. Run `aegis`, give an agent a multi-step task, and confirm the strip appears, counts up, and its circle spins while working and stills when the turn ends.

- [x] **Step 6: Commit**

```bash
git add src/aegis/tui/plan_strip.py src/aegis/tui/pane.py tests/test_plan_render.py
git commit -m "feat(tui): always-on PlanStrip in the conversation pane"
```

---

### Task 8: AgentPlan replaces in place in the transcript

Each `TaskCreate`/`TaskUpdate` now emits a cumulative `AgentPlan`, so an unchanged transcript would stack one plan block per mutation — trading N tool calls for N plan blocks. The event docstring already states the intended behaviour ("treat a new AgentPlan in the same turn as a replacement for any earlier one"); this task implements it.

**Files:**
- Modify: `src/aegis/tui/pane.py` (live mount path ~1878, replay fold ~1036-1074)
- Modify: `src/aegis/web/static/js/coalesce.js`
- Test: `tests/test_pane_plan_fold.py` (create)

**Interfaces:**
- Consumes: `AgentPlan` with `parent_tool_use_id` (Task 2).

- [x] **Step 1: Write the failing test**

```python
# tests/test_pane_plan_fold.py
"""Consecutive AgentPlan events collapse to one block: the plan is a
mutating thing, not an append-only log of its own revisions."""
from aegis.events import AgentPlan, AssistantText, PlanEntry
from aegis.tui.pane import fold_plan_events


def p(*pairs, parent=None):
    return AgentPlan(
        entries=tuple(PlanEntry(content=c, status=s) for c, s in pairs),
        parent_tool_use_id=parent)


def test_consecutive_plans_collapse_to_the_latest():
    evs = [p(("a", "pending")), p(("a", "in_progress")),
           p(("a", "completed"))]
    out = fold_plan_events(evs)
    assert len(out) == 1
    assert out[0].entries[0].status == "completed"


def test_a_plan_after_other_events_still_replaces_the_earlier_one():
    evs = [p(("a", "pending")), AssistantText(text="thinking"),
           p(("a", "completed"))]
    out = fold_plan_events(evs)
    assert [type(e).__name__ for e in out] == ["AssistantText", "AgentPlan"]
    assert out[-1].entries[0].status == "completed"


def test_subagent_plans_are_kept_separate_from_the_parent_plan():
    evs = [p(("a", "pending")), p(("x", "pending"), parent="tool_1"),
           p(("a", "completed"))]
    out = fold_plan_events(evs)
    plans = [e for e in out if isinstance(e, AgentPlan)]
    assert len(plans) == 2
    assert {pl.parent_tool_use_id for pl in plans} == {None, "tool_1"}
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_pane_plan_fold.py -v`
Expected: FAIL — `ImportError: cannot import name 'fold_plan_events'`

- [x] **Step 3: Implement the fold**

Add to `src/aegis/tui/pane.py`, next to the existing `coalesce_chunks` usage:

```python
def fold_plan_events(events: list) -> list:
    """Collapse AgentPlan revisions to the latest per plan owner.

    The plan is a mutating object; each Task* call re-emits the whole
    thing. Appending every revision would trade N tool-call rows for N
    plan blocks. Keyed by parent_tool_use_id so a subagent's plan does
    not overwrite its parent's.
    """
    from aegis.events import AgentPlan
    out: list = []
    slot: dict[str | None, int] = {}
    for ev in events:
        if isinstance(ev, AgentPlan):
            key = ev.parent_tool_use_id
            if key in slot:
                out[slot[key]] = ev
                continue
            slot[key] = len(out)
        out.append(ev)
    return out
```

Apply it in the replay path (where `coalesce_chunks` is already called), and in the live mount path replace the mounted plan block in place when one already exists for the same `parent_tool_use_id` rather than appending a new one.

For the web, mirror it in `coalesce.js` with the same keying.

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_pane_plan_fold.py tests/test_render_event.py -v`
Expected: PASS.

- [x] **Step 5: Verify in the real TUI**

Run `aegis`, give an agent a five-step task, and confirm the transcript holds exactly one plan block that mutates — not five.

- [x] **Step 6: Commit**

```bash
git add src/aegis/tui/pane.py src/aegis/web/static/js/coalesce.js tests/test_pane_plan_fold.py
git commit -m "feat(plan): AgentPlan revisions replace in place in the transcript"
```

---

### Task 9: PlanDock, F3 and /tasks

**Files:**
- Create: `src/aegis/tui/plan_dock.py`
- Modify: `src/aegis/tui/pane.py` (wrap `#transcript` in a `Horizontal`), `src/aegis/tui/app.py` (binding), `src/aegis/commands/builtins/session_ctl.py` (`/tasks`)
- Test: `tests/test_plan_render.py` (append)

**Interfaces:**
- Consumes: `render_plan_dock` (Task 5), `subplan_states()` (Task 6), `_refresh_plan_surfaces` (Task 7).
- Produces: `PlanDock(Static)` with `refresh_plan(state, subplans, working)` and `toggle()`; `render_plan_dock(..., subplans: dict[str, PlanState] | None = None)`.

The spec calls for subagent plans to **nest** under the dock — that is where a fan-out becomes legible, showing which of three parallel agents is still grinding. The strip stays flat and top-level-only; merging four agents' lists into one line is noise.

- [x] **Step 1: Write the dock widget**

```python
# src/aegis/tui/plan_dock.py
"""PlanDock — the toggled right-hand task panel.

Hidden by default: it costs ~26 columns, which is the right trade only on
a wide terminal. The strip is the always-on surface; this is the drill-down.
"""
from __future__ import annotations

from textual.widgets import Static

from aegis.plan.models import PlanState
from aegis.plan.render import render_plan_dock

_TICK = 0.25
DOCK_WIDTH = 26


class PlanDock(Static):
    DEFAULT_CSS = f"""
    PlanDock {{
        width: {DOCK_WIDTH};
        border-left: solid $panel;
        padding: 0 1;
    }}
    """

    def __init__(self, palette, **kw) -> None:
        super().__init__("", **kw)
        self._palette = palette
        self._state = PlanState()
        self._working = False
        self._frame = 0
        self.display = False
        self._open = False

    def on_mount(self) -> None:
        self.set_interval(_TICK, self._tick)

    def _tick(self) -> None:
        if self._open and self._working and self._state.current is not None:
            self._frame += 1
            self._paint()

    def toggle(self) -> bool:
        self._open = not self._open
        self.display = self._open
        if self._open:
            self._paint()
        return self._open

    def refresh_plan(self, state: PlanState, subplans: dict, working: bool
                     ) -> None:
        self._state, self._subplans, self._working = state, subplans, working
        if self._open:
            self._paint()

    def _paint(self) -> None:
        self.update(render_plan_dock(
            self._state, self._palette, working=self._working,
            frame=self._frame, width=DOCK_WIDTH - 2,
            subplans=self._subplans))
```

Initialise `self._subplans: dict = {}` in `__init__` alongside `self._state`.

- [x] **Step 1b: Extend the dock renderer to nest subagent plans**

In `src/aegis/plan/render.py`, add a `subplans` parameter to `render_plan_dock` and emit each subagent's tasks indented beneath a header. Insert the nesting block after the top-level task loop:

```python
def render_plan_dock(state: PlanState, colors, *, working: bool = False,
                     frame: int = 0, width: int = 24,
                     subplans: dict | None = None) -> Text:
    """One row per task: glyph, label, working time. Subagent plans nest
    beneath, which is what makes a fan-out legible — you can see which of
    three parallel agents is still grinding."""
    out = Text()
    if not state and not subplans:
        return Text("(no plan)", style=colors.muted)
    out.append(f"tasks {state.done}/{state.total}\n",
               style=f"bold {colors.accent}")
    label_w = max(8, width - 8)
    for task in state.tasks:
        _dock_row(out, task, colors, working, frame, label_w, indent="")
    for key, sub in (subplans or {}).items():
        if not sub:
            continue
        out.append(f"  └ subagent {sub.done}/{sub.total}\n",
                   style=colors.muted)
        for task in sub.tasks:
            _dock_row(out, task, colors, working, frame,
                      max(6, label_w - 4), indent="    ")
    return out


def _dock_row(out: Text, task, colors, working: bool, frame: int,
              label_w: int, indent: str) -> None:
    out.append(indent)
    out.append(_glyph(task, working, frame), style=_style(task, colors))
    label = task.label
    if len(label) > label_w:
        label = label[:label_w - 1] + "…"
    out.append(f" {label:<{label_w}} ", style=colors.ink)
    out.append(f"{fmt_working(task.working_s):>6}\n", style=colors.muted)
```

Replace the Task 5 body of `render_plan_dock` with this version; the Task 5 tests still pass because `subplans` defaults to `None`.

Add the nesting test to `tests/test_plan_render.py`:

```python
def test_dock_nests_subagent_plans_under_the_top_level_plan():
    top = state(("dispatch", "in_progress"))
    sub = state(("grind", "in_progress"), ("done", "completed"))
    out = as_text(render_plan_dock(top, C, subplans={"tool_1": sub}))
    assert "dispatch" in out and "subagent 1/2" in out
    assert "    ◐ grind" in out          # indented beneath
    assert not re.search(f"[{CIRCLES}][{CIRCLES}]", out)
```

- [x] **Step 2: Wrap the transcript**

In `pane.py:compose`, change:

```python
            yield VerticalScroll(id="transcript")
```

to:

```python
            with Horizontal(id="transcript-row"):
                yield VerticalScroll(id="transcript")
                yield PlanDock(self._palette, id="plan-dock")
```

Add `Horizontal` to the `textual.containers` import (line 17 currently imports `Vertical, VerticalScroll`). Add CSS so `#transcript` takes the remaining width (`width: 1fr`).

Extend `_refresh_plan_surfaces` from Task 7 to refresh the dock as well:

```python
        self.query_one("#plan-dock", PlanDock).refresh_plan(
            state, sess.subplan_states(), working)
```

- [x] **Step 3: Add the F3 binding and /tasks command**

In `src/aegis/tui/app.py`, add to `BINDINGS` (`F2` is already the ConfigPanel):

```python
        Binding("f3", "toggle_tasks", "Tasks", show=False),
```

```python
    def action_toggle_tasks(self) -> None:
        pane = self._active_pane()
        if pane is not None:
            pane.query_one("#plan-dock", PlanDock).toggle()
```

In `src/aegis/commands/builtins/session_ctl.py`, register `/tasks` following the shape of the neighbouring commands in that module, returning a `CommandResult` whose `effect` toggles the dock. This is what gives the web client the same toggle through the shared `dispatch()` seam.

- [x] **Step 4: Write the test**

```python
# append to tests/test_plan_render.py
@pytest.mark.asyncio
async def test_plan_dock_toggles_and_renders_rows():
    from textual.app import App, ComposeResult

    from aegis.tui.plan_dock import PlanDock

    class _A(App):
        def compose(self) -> ComposeResult:
            yield PlanDock(C, id="plan-dock")

    async with _A().run_test() as pilot:
        dock = pilot.app.query_one("#plan-dock", PlanDock)
        assert dock.display is False
        dock.refresh_plan(state(("a", "in_progress")), {}, working=True)
        assert dock.toggle() is True
        assert dock.display is True
        assert dock.toggle() is False
```

- [x] **Step 5: Run tests and verify in the real TUI**

Run: `uv run python -m pytest tests/test_plan_render.py -v`
Expected: PASS. Then run `aegis`, press `F3` mid-plan, confirm the dock opens with one row per task and the transcript reflows rather than being overdrawn.

- [x] **Step 6: Commit**

```bash
git add src/aegis/tui/plan_dock.py src/aegis/tui/pane.py src/aegis/tui/app.py src/aegis/commands/builtins/session_ctl.py tests/test_plan_render.py
git commit -m "feat(tui): PlanDock on F3 and /tasks"
```

---

### Task 10: Plan state on the coordination plane

The half that makes plan state worth putting in the core: a peer or coordinator can see that an agent is 3/8 through a plan and what it is currently on.

**Files:**
- Modify: `src/aegis/mcp/bridge.py` (`SessionInfo`), `src/aegis/core/manager.py:428`, `src/aegis/tui/app.py` (three `SessionInfo(` sites), `src/aegis/tui/remote_manager.py:326`, `src/aegis/mcp/server.py`
- Test: `tests/test_plan_coordination.py` (create)

**Interfaces:**
- Consumes: `PlanSnapshot`, `AgentSession.plan_roll_up()` (Tasks 3, 6).
- Produces: `SessionInfo.plan: PlanSnapshot | None = None`; MCP tool `aegis_peer_plan(handle: str) -> dict`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_plan_coordination.py
import dataclasses

from aegis.mcp.bridge import SessionInfo
from aegis.plan import PlanSnapshot


def test_session_info_defaults_plan_to_none():
    """Every existing construction site stays valid."""
    s = SessionInfo(handle="h", agent_slug="a", state="ready",
                    active=True, unseen=False)
    assert s.plan is None


def test_plan_reaches_list_sessions_through_asdict():
    """aegis_list_sessions is dataclasses.asdict over list_sessions, so a
    field added to SessionInfo needs no change to the tool body."""
    s = SessionInfo(handle="h", agent_slug="a", state="working",
                    active=True, unseen=False,
                    plan=PlanSnapshot(done=3, total=8, current="Wire it up",
                                      current_working_s=63.0))
    d = dataclasses.asdict(s)
    assert d["plan"]["done"] == 3
    assert d["plan"]["total"] == 8
    assert d["plan"]["current"] == "Wire it up"
```

Plus the tool itself. Read `tests/test_mcp_server.py` first and reuse its fake-bridge construction rather than building a second one — the fake there already implements `list_sessions`, so it only needs a `plan_state` method added:

```python
# append to tests/test_plan_coordination.py
import pytest

from aegis.plan import PlanState, PlanTask


@pytest.mark.asyncio
async def test_peer_plan_returns_the_full_annotated_list(mcp_bridge):
    """The drill-down: list_sessions says 1/2, this says which two."""
    mcp_bridge.add_session("worker")
    mcp_bridge.plans["worker"] = PlanState(tasks=(
        PlanTask(key="1", subject="Read the spec", status="completed",
                 working_s=252.0),
        PlanTask(key="2", subject="Write the code", status="in_progress",
                 working_s=63.0),
    ))
    out = await call_tool("aegis_peer_plan", handle="worker")
    assert (out["done"], out["total"]) == (1, 2)
    assert out["tasks"][1] == {"subject": "Write the code",
                               "status": "in_progress", "working_s": 63.0}


@pytest.mark.asyncio
async def test_peer_plan_refuses_an_unknown_handle(mcp_bridge):
    out = await call_tool("aegis_peer_plan", handle="ghost")
    assert "no session" in out["error"]


@pytest.mark.asyncio
async def test_peer_plan_on_a_session_with_no_plan_is_empty_not_an_error(
        mcp_bridge):
    mcp_bridge.add_session("idle")
    out = await call_tool("aegis_peer_plan", handle="idle")
    assert out["tasks"] == [] and out["total"] == 0
```

`mcp_bridge` and `call_tool` are placeholders for whatever `tests/test_mcp_server.py` already names its fixture and invocation helper — match that module exactly rather than adding new ones.

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_plan_coordination.py -v`
Expected: FAIL — `TypeError: SessionInfo.__init__() got an unexpected keyword argument 'plan'`

- [x] **Step 3: Add the field and the tool**

In `src/aegis/mcp/bridge.py`, append to `SessionInfo` (after `host`):

```python
    # Plan roll-up, so a peer deciding who to hand work to also learns how
    # far along everyone is. None when the session has no plan.
    plan: "PlanSnapshot | None" = None
```

Import `PlanSnapshot` from `aegis.plan`. Because the field defaults to `None`, all five construction sites stay valid; populate it at `core/manager.py:428` and the three `tui/app.py` sites from `session.plan_roll_up()`. `tui/remote_manager.py:326` receives it over the wire — decode it there if the remote payload carries it, else leave `None`.

In `src/aegis/mcp/server.py`, add beside `aegis_list_sessions`:

```python
    @server.tool
    async def aegis_peer_plan(handle: str) -> dict:
        """The full task list a peer session is working through.

        aegis_list_sessions gives you the roll-up (3/8, and what it is on
        right now); this is the drill-down — every task with its status
        and accumulated working time. Working time counts only the peer's
        mid-turn seconds, so an agent idle between turns does not inflate.
        """
        sessions = list(bridge.list_sessions())
        if not any(s.handle == handle for s in sessions):
            return {"error": f"no session {handle!r} (use aegis_list_sessions)"}
        state = bridge.plan_state(handle)
        if state is None:
            return {"handle": handle, "tasks": [], "done": 0, "total": 0}
        return {
            "handle": handle,
            "done": state.done,
            "total": state.total,
            "tasks": [
                {"subject": t.subject, "status": t.status,
                 "working_s": t.working_s}
                for t in state.tasks
            ],
        }
```

Add `plan_state(handle) -> PlanState | None` to the `AppBridge` Protocol and implement it on both `SessionManager` and `AegisApp`. Add a line describing `aegis_peer_plan` to the `BRIEFING` string, next to the `aegis_list_sessions` entry, and note there that each session entry now carries `plan`.

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_plan_coordination.py tests/test_mcp_server.py -v`
Expected: PASS.

- [x] **Step 5: Verify live**

From one aegis session with a plan in flight, call `aegis_list_sessions()` in another and confirm the `plan` roll-up is populated, then `aegis_peer_plan(<handle>)` and confirm the full list comes back with plausible working times.

- [x] **Step 6: Commit**

```bash
git add src/aegis/mcp/bridge.py src/aegis/mcp/server.py src/aegis/core/manager.py src/aegis/tui/app.py src/aegis/tui/remote_manager.py tests/test_plan_coordination.py
git commit -m "feat(mcp): plan roll-up on SessionInfo and aegis_peer_plan drill-down"
```

---

### Task 11: TabBar count and group dashboard roll-up

> **Partially shipped.** The tab-bar suffix landed in `20ee0bd`; the group-dashboard `member_detail` half was skipped deliberately — `DashboardSnapshot` has no live data path, so the helper would be dead code. Its steps below stay unticked on purpose.

The human coordinator's view of the same data. Both are small reads of what Task 10 already exposes.

**Files:**
- Modify: `src/aegis/tui/widgets.py` (TabBar cell), `src/aegis/tui/groups/dashboard.py:45`, `src/aegis/tui/groups/state.py`
- Test: `tests/test_plan_coordination.py` (append)

**Interfaces:**
- Consumes: `PlanSnapshot` (Task 3), `SessionInfo.plan` (Task 10).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_plan_coordination.py
from aegis.tui.groups.dashboard import member_detail


def test_group_member_detail_carries_the_plan_roll_up():
    detail = member_detail(state="working",
                           plan=PlanSnapshot(done=3, total=8,
                                             current="Wire it up"))
    assert "3/8" in detail and "Wire it up" in detail


def test_group_member_detail_without_a_plan_is_unchanged():
    assert "/" not in member_detail(state="ready", plan=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_plan_coordination.py -k member_detail -v`
Expected: FAIL — `ImportError: cannot import name 'member_detail'`

- [ ] **Step 3: Implement**

In `src/aegis/tui/groups/dashboard.py`, add:

```python
def member_detail(state: str, plan) -> str:
    """The per-member detail line. render_dashboard already renders a
    `detail` slot per member; a plan roll-up drops straight into it, so a
    fan-out becomes legible with no new widget."""
    if plan is None or not plan.total:
        return state
    cur = f" · {plan.current}" if plan.current else ""
    return f"{plan.done}/{plan.total}{cur}"
```

and feed it where `m.detail` is built. In `src/aegis/tui/widgets.py`, append a compact `n/total` to each tab cell when that session's `SessionInfo.plan` is populated — keep it short, the tab bar is width-constrained, and drop it first when the cell must truncate.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_plan_coordination.py tests/test_groups_dashboard.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/widgets.py src/aegis/tui/groups/ tests/test_plan_coordination.py
git commit -m "feat(tui): plan roll-up in the tab bar and group dashboard"
```

---

> **Deferred 2026-08-06** — Alex scoped this session to the TUI. Not started; the web surfaces remain the outstanding half of this plan.

### Task 12: The web strip and slide-over

The TUI and PWA are co-equal per `AGENTS.md`. `renderEvent.js:planHtml` already renders `AgentPlan` rows, so only the live surfaces are new.

**Files:**
- Modify: `src/aegis/web/static/js/app.js`, `src/aegis/web/static/js/renderEvent.js`, `src/aegis/web/static/index.html`, and the web CSS
- Modify: `src/aegis/web/wssession.py` (push plan state over the socket)

**Interfaces:**
- Consumes: `PlanState` / `PlanSnapshot` (Task 3), `/tasks` dispatch (Task 9).

- [ ] **Step 1: Push plan state over the websocket**

In `src/aegis/web/wssession.py`, include the plan roll-up and full task list in the per-session payload the client already receives on state change, following the shape of the existing fields in that module.

- [ ] **Step 2: Render the strip**

Export the glyph map from `renderEvent.js` (it is currently module-private at line 11) so there is still exactly one vocabulary:

```js
export const PLAN_GLYPH = { completed: "●", in_progress: "◐", pending: "○" };
export const SPINNER_FRAMES = "◐◓◑◒";
```

Add to `app.js`, mirroring `plan/render.py`:

```js
import { PLAN_GLYPH, SPINNER_FRAMES } from "./renderEvent.js";

function fmtWorking(s) {
  if (s === null || s === undefined) return "—";
  s = Math.floor(s);
  if (s >= 3600)
    return `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60))
      .padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function planGlyph(task, working, frame) {
  if (task.status === "in_progress" && working)
    return SPINNER_FRAMES[frame % SPINNER_FRAMES.length];
  return PLAN_GLYPH[task.status] || "○";
}

// One circle per task, ALWAYS space-separated: these glyphs are East Asian
// Ambiguous width and adjacent circles overlap in many renderers.
export function renderPlanStrip(plan, { working = false, frame = 0,
                                        cap = 12 } = {}) {
  const tasks = (plan && plan.tasks) || [];
  if (!tasks.length) return "";
  let start = 0;
  if (tasks.length > cap) {
    const cur = Math.max(0, tasks.findIndex((t) => t.status === "in_progress"));
    start = Math.max(0, Math.min(cur - Math.floor(cap / 2), tasks.length - cap));
  }
  const window = tasks.slice(start, start + cap);
  const left = start > 0 ? "…" : "";
  const right = start + cap < tasks.length ? "…" : "";
  const circles = window
    .map((t) => `<span class="${t.status}">${planGlyph(t, working, frame)}</span>`)
    .join(" ");
  const done = tasks.filter((t) => t.status === "completed").length;
  const cur = tasks.find((t) => t.status === "in_progress");
  const tail = cur
    ? ` · ${esc(cur.active_form || cur.subject)} ${fmtWorking(cur.working_s)}`
    : "";
  return `<span class="plan-strip">${left}${circles}${right}`
    + `  ${done}/${tasks.length}${tail}</span>`;
}
```

Mount a `<div id="plan-strip">` in the status area in `index.html`, repaint it from the websocket handler, and drive `frame` from a 250 ms interval that ticks **only while the session is working** — a settled plan must not burn a repaint four times a second.

- [ ] **Step 3: Render the slide-over**

Add a panel that slides in over the transcript (not a permanent sidebar — a permanent sidebar on a phone is not a thing), toggled by the same `/tasks` command and by a tap target in the status area.

- [ ] **Step 4: Verify in a browser**

Serve with `aegis web`, drive a multi-step task, and confirm on a desktop browser and at a phone viewport width that the strip appears, counts up, spins while working, and the slide-over lists every task with its time. **Load the page and look at it** — a passing render function is not a served UI.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/web/
git commit -m "feat(web): live plan strip and task slide-over"
```

---

### Task 13: Full suite, docs, release notes

- [x] **Step 1: Run the whole hermetic suite**

Run: `uv run python -m pytest -q -m "not live"`
Expected: green. A failing test is a real failure, not flake — the 0.25.0 fixes removed the historical flakiness, so investigate rather than re-roll.

- [x] **Step 2: Update AGENTS.md**

Add `src/aegis/plan/` to the Layout section, describing the two layers (parser normalizes shape; tracker adds time) and stating the two rules a future contributor will otherwise break: circles are always space-separated, and the tracker never reads a clock.

- [x] **Step 3: Update CHANGELOG.md**

Add a Features entry covering: the `Task*` family now renders as a live plan rather than anonymous tool calls; per-task working time; the strip, the dock, and the web surfaces; plan roll-up on `aegis_list_sessions` and the new `aegis_peer_plan`.

- [x] **Step 4: Commit**

```bash
git add AGENTS.md CHANGELOG.md
git commit -m "docs: the live task list"
```

---

## Out of scope

Carried from the spec, so a worker does not absorb them silently:

- **The broken collapsed `SubagentBox`.** Separate fix, own spec. Task 8 keys plan folding by `parent_tool_use_id` so subagent plans stay distinct, which does not depend on that fix.
- **`tool_progress` and `system/task_started` landing as `Unknown`.** Both observed live. `tool_progress` carries `elapsed_time_seconds` and would feed a per-tool progress signal. Adjacent, own follow-up.
- **Cross-tab aggregation.** Each pane's dock shows that pane's plan; the group dashboard is where fan-out aggregates.
