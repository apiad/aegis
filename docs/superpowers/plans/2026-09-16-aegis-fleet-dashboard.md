# Fleet Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A full-screen view of every live session — one card each, no transcript — reachable with F10 and, later, as `aegis dash` on a second monitor.

**Architecture:** A `FleetSnapshot` dataclass assembled from the live `SessionManager`, rendered by a pure function, mounted in a Textual `ModalScreen`. A new typed `Origin` record answers who made each agent. A mid-turn recap call, gated on someone watching, supplies the "what is it doing right now" line.

**Tech Stack:** Python 3.13+, `uv`, Textual, Rich, pytest. No new dependencies.

> **Status:** implemented 2026-09-16. Tasks 1-16 landed, with 9b, 9c, 10b
> and 12a. Open: Task 10's check of F10 over the operator's own fleet waits
> for his daemon restart; Task 16's full gate and push are the controller's.

**Spec:** `docs/superpowers/specs/2026-09-16-aegis-fleet-dashboard-design.md` — read it before Task 1. Every cost number in this plan comes from there.

## Global Constraints

- Python 3.13 or newer. Use `uv`, never `pip`. `uv run pytest`, `uv run ruff`.
- **`make check` is red on `main` and not because of this work.** Measured at `da5c989`, before any task landed: `make typecheck` (`uv run ty check src/`) exits 1 with **327 errors**, spread across files this plan never touches (15 in `commands/builtins/core.py` alone, all pre-existing). Nothing in `TASKS.md` or `CHANGELOG.md` records it. The achievable gate, and the one the controller runs between tasks, is therefore: `format`, `lint`, `lint-docs` and `test` green, **and `ty` no worse than the 327-error baseline**. Workers run neither — see the testing rule below.
- **Shared checkout.** Stage and commit named paths only: `git commit -- <paths>`. Never `git add -A`, never `git add` a whole file that was already dirty, never `--amend`.
- Conventional commits, English, one logical change each.
- UI chrome is English (`did`, `now`, `9 agents · 6 yours · 3 ephemeral`) like the rest of the TUI. Session content is whatever that session is about.
- **Run `uv run ruff format <the src files you changed>` before every commit.** `make check` runs `ruff format src/` in *write* mode (`Makefile:3,17-18`), so unformatted code does not fail a gate quietly — it gets rewritten in a shared checkout, after the commit, under nobody's name. Never run `make format` or `ruff format src/` wholesale: other agents' files live there. Format the files you touched, by name.
- **Two clocks, and they must not cross.** `now` passed to `build_snapshot` is **monotonic** (`time.monotonic()`), because it feeds `SessionMetrics.session_seconds(now)` and `turn_seconds(now)`, which are monotonic, and so does `GhostBook.observe(now)`. `CardView.ghost_since` is therefore monotonic too, and is only ever used for an *age* and a TTL, never rendered as a time of day. `EventLine.at` is **wall-clock** (`time.time()`) because it renders as `HH:MM`. A monotonic value printed as a time of day, or a wall-clock value subtracted from a monotonic one, is a silent wrong answer rather than an error.
- **Cards are built from a snapshot of live sessions, never from the `added` event.** `_announce("added", s)` fires inside `_sync_spawn` (`core/manager.py:313`), and fork, workflow and group set `origin` *after* that returns, so an add-event listener reads `Origin()` — the operator — for one beat. `spawned_by` and `forked_from` already behave the same way at the same three sites. `build_snapshot` reading `manager._sessions` is what makes this a non-issue; do not switch any surface to event-driven card creation.
- **Workers run only the focused tests their own task needs.** The controller runs the full suite and the gate between tasks. This is a standing instruction from the operator, not a shortcut.
- Renderers are **pure functions over dataclasses**, following `aegis.tui.sidebar.render_sidebar`. No Textual object may appear in a renderer's signature. Tests construct the model and assert on `.plain` — see `tests/test_sidebar_render.py`.
- A daemon keeps the code it booted with. Any live check means `aegis kill` first, then attach fresh. See `know-how/the-daemon.md`.

---

## Files

**Created**

| path | responsibility |
|---|---|
| `src/aegis/fleet/__init__.py` | re-exports |
| `src/aegis/fleet/models.py` | `Origin`, `CardView`, `BandView`, `FleetSnapshot`, `EventLine` |
| `src/aegis/fleet/snapshot.py` | `build_snapshot(manager, now)` — reads live state, returns `FleetSnapshot` |
| `src/aegis/fleet/render.py` | `render_card`, `render_fleet` — pure |
| `src/aegis/recap/__init__.py`, `src/aegis/recap/gate.py` | `FleetRecap`, `recap_in_flight` beside `recap_turn`/`recap_session`; `should_fleet_recap` beside `should_recap` |
| `src/aegis/tui/fleet_screen.py` | `FleetScreen(ModalScreen)` — mounting, keys, click |
| `tests/test_fleet_origin.py` | `Origin` set correctly at each birth site |
| `tests/test_fleet_events.py` | the per-session event ring |
| `tests/test_fleet_snapshot.py` | assembly from a fake manager |
| `tests/test_fleet_render.py` | the pure renderer |
| `tests/test_fleet_screen.py` | keys, click, selection |
| `tests/test_fleet_recap.py` | the mid-turn gate |
| `tests/test_oneshot_env.py` | `MAX_THINKING_TOKENS` reaches the subprocess |

**Modified**

| path | change |
|---|---|
| `src/aegis/drivers/claude.py` | `generate_detailed` passes an explicit `env`; docstring numbers corrected |
| `src/aegis/core/session.py` | `origin` attribute, the event ring, the mid-turn recap driver |
| `src/aegis/core/manager.py` | `_sync_spawn` takes `origin=`; fork sets one |
| `src/aegis/commands/builtins/core.py` | `/spawn` sets `kind="operator"` |
| `src/aegis/mcp/server.py` | `aegis_spawn` sets `kind="agent"` |
| `src/aegis/queue/manager.py` | the worker spawn sets `kind="queue"` |
| `src/aegis/workflow/engine.py` | `spawn` sets `kind="workflow"` |
| `src/aegis/groups/wiring.py` | `spawn` sets `kind="group"` |
| `src/aegis/config/__init__.py` | `FleetConfig` |
| `src/aegis/config/yaml_loader.py` | `_build_fleet`, `AegisConfig.fleet` |
| `src/aegis/tui/app.py` | F10 binding, `action_open_fleet` |
| `src/aegis/tui/pane.py` | the `now` line into `SidebarModel` |
| `src/aegis/tui/sidebar.py` | render that line |
| `src/aegis/bench/scenarios.py` | a nine-card grid scenario |
| `src/aegis/cli.py` | `aegis dash` |
| `CHANGELOG.md`, `docs/`, `TASKS.md` | user-visible surface |

---

# Slice 1 — the thinking cut

Independent of everything below. Ship it on its own.

### Task 1: `MAX_THINKING_TOKENS=0` reaches the one-shot subprocess

**Files:**
- Modify: `src/aegis/drivers/claude.py` (`generate_detailed`, around line 399; `_oneshot_argv` docstring at line 341)
- Test: `tests/test_oneshot_env.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ClaudeDriver.generate_detailed` now spawns with an explicit `env` mapping containing `MAX_THINKING_TOKENS="0"`. No signature change.

- [x] **Step 1: Write the failing test**

```python
"""One-shot generation must not pay for reasoning it does not use.

Measured 2026-09-16 on a recap-shaped call (haiku 4.5, CLI 2.1.270):
thinking on took 27.1s and 2,508 output tokens to write two sentences,
and its latency swung 8.7s-30.4s run to run; off took 4.7s and 103
tokens, every time. `--effort` has no off switch — `low` still emitted
133 thinking tokens — so the environment variable is the only real one.
"""
import asyncio

import pytest
from pydantic import BaseModel

from aegis.config import Agent
from aegis.drivers.claude import ClaudeDriver


class _Two(BaseModel):
    line: str


@pytest.fixture
def agent():
    return Agent(harness="claude-code", model="claude-haiku-4-5-20251001")


def test_generate_passes_thinking_budget_zero(monkeypatch, agent, tmp_path):
    seen = {}

    async def fake_exec(*argv, **kw):
        seen["env"] = kw.get("env")
        raise RuntimeError("stop here — we only care about the env")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(ClaudeDriver().generate_detailed(agent, str(tmp_path), _Two, "hi"))

    assert seen["env"] is not None, "generate_detailed inherited the daemon env"
    assert seen["env"]["MAX_THINKING_TOKENS"] == "0"


def test_generate_env_keeps_the_rest_of_the_environment(monkeypatch, agent, tmp_path):
    """Replacing os.environ wholesale would drop PATH and the API key."""
    seen = {}

    async def fake_exec(*argv, **kw):
        seen["env"] = kw.get("env")
        raise RuntimeError("stop")

    monkeypatch.setenv("AEGIS_PROBE_MARKER", "present")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(ClaudeDriver().generate_detailed(agent, str(tmp_path), _Two, "hi"))

    assert seen["env"]["AEGIS_PROBE_MARKER"] == "present"
```

- [x] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_oneshot_env.py -v`
Expected: both FAIL on `seen["env"] is not None` — `generate_detailed` passes no `env` today, so the key is absent.

- [x] **Step 3: Pass the env**

In `src/aegis/drivers/claude.py`, add `import os` if absent, and in `generate_detailed` change the `create_subprocess_exec` call:

```python
        argv = self._oneshot_argv(agent, schema, list(instructions))
        # A generation call is handed its window and asked for two lines.
        # Reasoning buys nothing here and costs a lot: measured 2026-09-16
        # on a recap-shaped call, 27.1s / 2,508 output tokens on against
        # 4.7s / 103 off, with the on-arm swinging 8.7s-30.4s run to run.
        # `--effort` has no off (`low` still thinks), so this is the switch.
        env = {**os.environ, "MAX_THINKING_TOKENS": "0"}
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=_STREAM_LIMIT,
            )
```

- [x] **Step 4: Run the tests**

Run: `uv run pytest tests/test_oneshot_env.py -v`
Expected: PASS, both.

- [x] **Step 5: Break it on purpose and confirm the test fails**

A check whose healthy and broken outputs are identical is not a check.

```bash
sed -i 's/"MAX_THINKING_TOKENS": "0"/"MAX_THINKING_TOKENS": "1024"/' src/aegis/drivers/claude.py
cmp -s <(git show HEAD:src/aegis/drivers/claude.py) src/aegis/drivers/claude.py && echo "MUTATION DID NOT APPLY — stop" || echo "mutated"
uv run pytest tests/test_oneshot_env.py -v
```
Expected: `mutated`, then the first test FAILS. Then revert:
```bash
sed -i 's/"MAX_THINKING_TOKENS": "1024"/"MAX_THINKING_TOKENS": "0"/' src/aegis/drivers/claude.py
uv run pytest tests/test_oneshot_env.py -v
```
Expected: PASS again.

- [x] **Step 6: Measure the real path and correct the docstring**

The docstring on `_oneshot_argv` records 21,445 → 7,749 input tokens from 2026-08-26. A probe on 2026-09-16 measured 1,027 for the same flags. Confirm through the real aegis path rather than a reimplementation of it:

```bash
cd /home/apiad/Workspace/repos/aegis
uv run python - <<'PY'
import asyncio, time
from pydantic import BaseModel
from aegis.config import Agent
from aegis.drivers.claude import ClaudeDriver

class R(BaseModel):
    done: str
    doing: str

agent = Agent(harness="claude-code", model="claude-haiku-4-5-20251001")
w = open("docs/superpowers/specs/2026-09-16-aegis-fleet-dashboard-design.md").read()[:4000]
t0 = time.monotonic()
g = asyncio.run(ClaudeDriver().generate_detailed(agent, ".", R, w, "Two lines."))
print(f"wall={time.monotonic()-t0:.1f}s cost=${g.cost_usd:.5f} model={g.model} value={g.value}")
PY
```

Record the wall time and cost. Then replace the two stale paragraphs of the `_oneshot_argv` docstring with what you measured, keeping the 2026-08-26 numbers labelled as history, and add the `--effort` finding:

```
        **Re-measured 2026-09-16 (CLI 2.1.270) and the numbers above are
        HISTORY.** The same flags now cost ~1,027 input tokens, a further
        factor of 7.6 — the CLI shed its own prefix between 2.1.220 and
        2.1.270. The practical consequence inverts the old advice: the
        window is no longer worth squeezing (a full one costs ~546 tokens
        over the floor) and the expensive side is OUTPUT. See
        `generate_detailed` for the MAX_THINKING_TOKENS cut.

        ``--effort`` is not an alternative and never was: it runs `low` to
        `max` with no off, and `low` still emitted 133 thinking tokens to
        write two sentences (measured 2026-09-13).
```

- [x] **Step 7: Full gate, then commit**

Run `make check` as its own tool call and read its exit code directly. Never pipe it; a pipe hands `&&` the pipe's status and turns a red gate green.

```bash
git commit -- src/aegis/drivers/claude.py tests/test_oneshot_env.py -m "fix(drivers): one-shot generation stops paying for reasoning

A recap is handed its window and asked for two lines. Measured 2026-09-16
on a recap-shaped call: thinking on took 27.1s and 2,508 output tokens,
off took 4.7s and 103, and the on-arm's latency swung 8.7s-30.4s run to
run, which a refreshing screen cannot use. --effort has no off switch, so
the environment variable is the only one.

Also corrects the _oneshot_argv docstring: its 7,749-token prefix figure
is from 2026-08-26 and the same flags now cost ~1,027."
```

---

# Slice 2 — F10, end to end

### Task 2: `Origin` — who made this agent

**Files:**
- Create: `src/aegis/fleet/__init__.py`, `src/aegis/fleet/models.py`
- Test: `tests/test_fleet_origin.py`

**Interfaces:**
- Produces: `Origin(kind, by, detail, returns_to)` with `Origin.ephemeral -> bool`, and the constant `EPHEMERAL_KINDS`.

- [x] **Step 1: Write the failing test**

```python
"""Origin answers two questions spawned_by cannot: who made this agent,
and will it outlive the work it was made for."""
import pytest

from aegis.fleet.models import Origin


def test_default_origin_is_the_operator():
    assert Origin().kind == "operator"


@pytest.mark.parametrize("kind", ["queue", "workflow", "group"])
def test_substrate_born_agents_are_ephemeral(kind):
    assert Origin(kind=kind).ephemeral is True


@pytest.mark.parametrize("kind", ["operator", "agent", "fork", "schedule"])
def test_agents_that_outlive_their_task_are_not(kind):
    assert Origin(kind=kind).ephemeral is False


def test_an_unknown_kind_is_not_ephemeral():
    """Ephemerality means the substrate closes it. An unrecognised kind
    has nobody to do that, so guessing 'yes' would ghost a live agent."""
    assert Origin(kind="something-new").ephemeral is False


def test_a_queue_worker_carries_where_its_answer_goes():
    o = Origin(kind="queue", by="general", detail="a3f2", returns_to="rosy-rivest")
    assert (o.by, o.detail, o.returns_to) == ("general", "a3f2", "rosy-rivest")
```

- [x] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_fleet_origin.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.fleet'`.

- [x] **Step 3: Write the model**

`src/aegis/fleet/__init__.py`:

```python
"""The fleet dashboard: one card per live session, no transcript.

Assembly (`snapshot`) reads the live SessionManager; rendering (`render`)
is pure functions over the dataclasses in `models`, so the grid is tested
without a Textual app. Same split as `aegis.tui.sidebar`.
"""

from aegis.fleet.models import BandView, CardView, EventLine, FleetSnapshot, Origin

__all__ = ["BandView", "CardView", "EventLine", "FleetSnapshot", "Origin"]
```

`src/aegis/fleet/models.py`:

```python
"""What the dashboard knows about one session, and about all of them."""

from __future__ import annotations

from dataclasses import dataclass, field

# Kinds whose sessions the substrate closes when their unit of work ends:
# a queue worker at queue/manager.py:719, a workflow subagent by the
# engine, a group member with its group. Derived, never stored — a boolean
# on the session would drift from the behaviour it names.
EPHEMERAL_KINDS = frozenset({"queue", "workflow", "group"})


@dataclass(frozen=True)
class Origin:
    """Who made this agent.

    `spawned_by` records one nullable handle and cannot answer either half
    of that question: four of the seven birth sites write nothing to it,
    and an operator typing `/spawn` in tab A writes exactly what agent A
    calling `aegis_spawn` writes. This sits beside it rather than
    replacing it — `close_guard`, `aegis_close` and the fork guard all
    read `spawned_by` and none of them wants a new type.
    """

    kind: str = "operator"  # operator|agent|queue|workflow|group|schedule|fork
    by: str = ""            # pane handle, agent handle, queue name, workflow name
    detail: str = ""        # task id, workflow run id, group name
    returns_to: str = ""    # where the result goes, for queue callbacks

    @property
    def ephemeral(self) -> bool:
        return self.kind in EPHEMERAL_KINDS
```

- [x] **Step 4: Run the tests**

Run: `uv run pytest tests/test_fleet_origin.py -v`
Expected: PASS, all five.

- [x] **Step 5: Commit**

```bash
git commit -- src/aegis/fleet/__init__.py src/aegis/fleet/models.py tests/test_fleet_origin.py -m "feat(fleet): Origin, a typed record of who made an agent"
```

### Task 3: set `Origin` at every birth site

**Files:**
- Modify: `src/aegis/core/session.py` (`AgentSession.__init__`, near the `spawned_by` neighbourhood around line 96)
- Modify: `src/aegis/core/manager.py` (`_sync_spawn` signature line 219, the assignment near line 299, the fork at line 402)
- Modify: `src/aegis/commands/builtins/core.py:280`
- Modify: `src/aegis/mcp/server.py:1136`
- Modify: `src/aegis/queue/manager.py:516`
- Modify: `src/aegis/workflow/engine.py:385-387`
- Modify: `src/aegis/groups/wiring.py:30`
- Test: `tests/test_fleet_origin.py` (append)

**Interfaces:**
- Consumes: `Origin` from Task 2.
- Produces: `AgentSession.origin: Origin`, always set, defaulting to `Origin()`. `SessionManager._sync_spawn(..., origin: Origin | None = None)`.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_fleet_origin.py`. These assert against the **real** call sites, not a synthetic stand-in: a test that hardcodes the value it branches on restates my model and passes for no reason.

```python
import inspect

from aegis.fleet.models import Origin


def test_every_session_has_an_origin(session_manager):
    """A tab nobody explicitly attributed is the operator's."""
    s = session_manager._sync_spawn("opus")
    assert s.origin == Origin()


def test_spawn_records_the_origin_it_is_given(session_manager):
    o = Origin(kind="queue", by="general", detail="a3f2")
    s = session_manager._sync_spawn("opus", origin=o)
    assert s.origin == o


def test_a_fork_is_marked_as_one(session_manager):
    parent = session_manager._sync_spawn("opus")
    child = session_manager._sync_spawn("opus", origin=Origin(kind="fork", by=parent.handle))
    assert child.origin.kind == "fork"
    assert child.origin.by == parent.handle


def test_the_queue_spawns_its_worker_with_a_queue_origin():
    """Read the real call site rather than trusting a synthetic one: the
    queue is the site that records nothing today, so the test that matters
    is whether THAT line changed."""
    from aegis.queue import manager as qm

    src = inspect.getsource(qm.QueueManager)
    assert "origin=" in src, "the queue worker spawn still records no origin"
    assert 'kind="queue"' in src


def test_the_mcp_spawn_tool_marks_an_agent_origin():
    from aegis.mcp import server

    src = inspect.getsource(server)
    assert 'kind="agent"' in src


def test_the_slash_command_marks_an_operator_origin():
    from aegis.commands.builtins import core

    src = inspect.getsource(core)
    assert 'kind="operator"' in src
```

> The three `inspect.getsource` tests are deliberate seams, not laziness.
> Driving a real queue worker, a real MCP tool call and a real slash
> command through a live manager belongs in the live-exercise step of
> Task 10; these stop the wiring silently reverting in between.

- [x] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_fleet_origin.py -v`
Expected: the six new tests FAIL — `AgentSession` has no `origin`, `_sync_spawn` takes no `origin=`, and none of the three source strings is present.

- [x] **Step 3: Thread `origin` through the session and the manager**

In `src/aegis/core/session.py`, `AgentSession.__init__`, add `origin: "Origin | None" = None` to the keyword-only parameters and, next to where `self.title` is set:

```python
        # Who made this agent, and whether the substrate will close it when
        # its work ends. `spawned_by` (below) stays: three guards read it.
        from aegis.fleet.models import Origin

        self.origin = origin or Origin()
```

In `src/aegis/core/manager.py`, add `origin: "Origin | None" = None` to `_sync_spawn`'s keyword-only parameters and pass it into the `AgentSession(...)` construction. At the fork site (line ~402), beside `child.spawned_by = forked_by`:

```python
        child.spawned_by = forked_by
        child.origin = Origin(kind="fork", by=forked_by or "")
```

- [x] **Step 4: Set it at the five remaining sites**

`src/aegis/commands/builtins/core.py` — the `/spawn` call, beside `spawned_by=ctx.handle`:

```python
            spawned_by=ctx.handle,
            # The operator typed this while standing in ctx.handle. That is
            # a different event from agent ctx.handle calling aegis_spawn,
            # and spawned_by records them identically.
            origin=Origin(kind="operator", by=ctx.handle),
```

`src/aegis/mcp/server.py` — beside `spawned_by=from_handle`:

```python
                spawned_by=from_handle,
                origin=Origin(kind="agent", by=from_handle),
```

`src/aegis/queue/manager.py` — the worker spawn:

```python
            session = sync_spawn(
                q.agent_profile,
                opening_prompt=task.payload,
                handle=worker_handle,
                origin=Origin(
                    kind="queue",
                    by=queue,
                    detail=task.id[-4:],
                    returns_to=task.callback_to or "",
                ),
            )
```

`src/aegis/workflow/engine.py` — in `spawn`, after the handle comes back:

```python
        self._spawned_handles.add(h)
        sess = self._bridge.get(h) if hasattr(self._bridge, "get") else None
        if sess is not None:
            sess.origin = Origin(kind="workflow", by=self.name, detail=self.run_id)
```

Use the engine's own attribute names for the workflow name and run id; if they differ from `self.name` / `self.run_id`, use what is actually there and leave `detail` empty rather than inventing a field.

`src/aegis/groups/wiring.py` — in `spawn`, the same shape with `kind="group"`, `by=group`.

Add `from aegis.fleet.models import Origin` at the top of each file.

- [x] **Step 5: Run the tests**

Run: `uv run pytest tests/test_fleet_origin.py -v`
Expected: PASS, all eleven.

- [x] **Step 6: Run the suite that touches spawning**

Run: `uv run pytest tests/test_spawn_provenance.py tests/core tests/test_queue_manager.py -v` (drop any path that does not exist)
Expected: PASS. A new keyword-only parameter with a default breaks no caller; if something fails, it is a positional-argument call site that needs fixing, not the default.

- [x] **Step 7: Commit**

```bash
git commit -- src/aegis/core/session.py src/aegis/core/manager.py src/aegis/commands/builtins/core.py src/aegis/mcp/server.py src/aegis/queue/manager.py src/aegis/workflow/engine.py src/aegis/groups/wiring.py tests/test_fleet_origin.py -m "feat(core): every session records where it came from

Six birth sites, one typed record. Four of them wrote nothing before, so
a queue worker, a workflow subagent, a group member and an operator's own
tab were indistinguishable."
```

### Task 4: the event ring — what a session did in the last minute

**Files:**
- Modify: `src/aegis/fleet/models.py` (add `EventLine`)
- Modify: `src/aegis/core/session.py`
- Test: `tests/test_fleet_events.py`

**Interfaces:**
- Consumes: `aegis.events.ToolUse` (fields `name`, `summary`, `parent_tool_use_id`).
- Produces: `EventLine(at: float, tool: str, summary: str)` and `AgentSession.recent_events: tuple[EventLine, ...]`, newest last, at most 5.

- [x] **Step 1: Write the failing test**

```python
"""The card's three middle rows. Today the only way to know what a session
did a minute ago is to read its transcript, which is exactly what a
dashboard exists to avoid."""
from aegis.events import ToolUse
from aegis.fleet.models import EventLine


def test_a_fresh_session_has_no_events(session):
    assert session.recent_events == ()


def test_a_tool_call_lands_in_the_ring(session):
    session.note_event(ToolUse(name="Edit", summary="apps/sigere/pusher.py"), at=100.0)
    assert session.recent_events == (
        EventLine(at=100.0, tool="Edit", summary="apps/sigere/pusher.py"),
    )


def test_the_ring_keeps_the_five_newest(session):
    for i in range(8):
        session.note_event(ToolUse(name="Bash", summary=f"cmd {i}"), at=float(i))
    assert len(session.recent_events) == 5
    assert [e.summary for e in session.recent_events] == [
        "cmd 3", "cmd 4", "cmd 5", "cmd 6", "cmd 7"
    ]


def test_a_subagents_tool_call_is_not_the_sessions_own(session):
    """A Task subagent's tools are not what THIS agent is doing, and the
    queue already applies this rule to assistant text for the same reason
    (queue/manager.py, `_attach_observers`)."""
    session.note_event(
        ToolUse(name="Read", summary="x.py", parent_tool_use_id="toolu_01"), at=1.0
    )
    assert session.recent_events == ()
```

**`tests/conftest.py` has no `session` fixture** — checked. Build one in `tests/test_fleet_events.py` from `tests/brain.py::make_brain`, which is how the suite builds a manager wired the way `cli.py::_serve` wires one:

```python
@pytest.fixture
def session(tmp_path):
    from tests.brain import make_brain

    mgr = make_brain({"opus": Agent(harness="claude-code", model="opus")},
                     default_agent="opus", ...)
    return mgr._sync_spawn("opus")
```

Fill the constructor arguments from an existing caller — `tests/test_session_titles.py` builds one. The same applies to `session_manager` in Task 3 and `fake_agent` in Task 11: neither exists in `conftest.py`, so define them locally in the test file that needs them.

- [x] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_fleet_events.py -v`
Expected: FAIL — `AgentSession` has no `note_event` and no `recent_events`.

- [x] **Step 3: Add `EventLine` to the models**

```python
@dataclass(frozen=True)
class EventLine:
    """One line of the card's activity tail."""

    at: float          # wall-clock epoch seconds, for the HH:MM stamp
    tool: str
    summary: str
```

- [x] **Step 4: Add the ring to the session**

In `AgentSession.__init__`, beside the digest:

```python
        # The last few tool calls, for the fleet dashboard's activity tail.
        # Bounded and in memory: a dashboard must never read a transcript.
        self._events: deque = deque(maxlen=5)
```

with `from collections import deque` at the top, and:

```python
    @property
    def recent_events(self) -> tuple["EventLine", ...]:
        return tuple(self._events)

    def note_event(self, ev, at: float | None = None) -> None:
        """Record a tool call for the dashboard's activity tail.

        A subagent's tool calls are skipped for the reason the queue skips
        its assistant text: they are the subagent working, not this agent.
        """
        from aegis.events import ToolUse
        from aegis.fleet.models import EventLine

        if not isinstance(ev, ToolUse):
            return
        if getattr(ev, "parent_tool_use_id", None) is not None:
            return
        self._events.append(
            EventLine(at=at if at is not None else time.time(),
                      tool=ev.name, summary=ev.summary or "")
        )
```

Then hook it into **`AgentSession._fire_event`** (`core/session.py:819`), in the existing `elif isinstance(ev, ToolUse):` branch beside `_record_repo(ev)`.

> **Corrected mid-run.** This step originally said to hook beside
> `self.metrics.record_tool()`, "the one place every tool call passes
> through". That was wrong and the Task 4 implementer caught it:
> `record_tool()` has two call sites (`_run_turn:730`,
> `_drain_unsolicited_turn:1122`), while both loops call `_fire_event(ev)`
> unconditionally *before* their isinstance chains. `_fire_event` is the
> strict superset, it already holds the sibling `ToolUse` fold, and the
> replay path skips it — which is the behaviour the ring wants, since a
> replayed transcript must not refill the ring with stale events.

- [x] **Step 5: Run the tests**

Run: `uv run pytest tests/test_fleet_events.py -v`
Expected: PASS, all four.

- [x] **Step 6: Commit**

```bash
git commit -- src/aegis/fleet/models.py src/aegis/core/session.py tests/test_fleet_events.py -m "feat(fleet): a bounded ring of each session's last five tool calls"
```

### Task 5: the snapshot models

**Files:**
- Modify: `src/aegis/fleet/models.py`
- Test: `tests/test_fleet_snapshot.py`

**Interfaces:**
- Produces: `CardView`, `BandView`, `RepoCount`, `FleetSnapshot`, all frozen dataclasses with defaults so a test can build a partial one.

- [x] **Step 1: Write the model**

No test of its own — a dataclass with defaults has no behaviour worth asserting, and Task 6 tests it through assembly. Append to `src/aegis/fleet/models.py`:

```python
@dataclass(frozen=True)
class CardView:
    """One session as the dashboard sees it. Everything a card draws is
    here; the renderer reads no live object."""

    handle: str
    title: str = ""
    state: str = "ready"            # ready | working | error
    agent_slug: str = ""
    host: str = "local"
    repo: str = ""                  # "une-tools · main +3 ~2", already formatted
    origin: Origin = field(default_factory=Origin)
    uptime_s: float = 0.0
    turn_s: float = 0.0             # 0 when not in a turn
    cost_usd: float = 0.0
    ctx_pct: float = 0.0
    plan_done: int = 0
    plan_total: int = 0
    plan_current: str = ""
    did: str = ""                   # last turn's recap
    doing: str = ""                 # mid-turn recap; "" until slice 3
    events: tuple[EventLine, ...] = ()
    claims: int = 0
    monitor: str = ""               # "pytest 60%", "" when none
    spoke_with: tuple[str, ...] = ()   # comms edges, most recent first
    waiting_on: tuple[str, ...] = ()
    tab_index: int = 0              # 1-based; the card is that tab
    ghost_since: float | None = None  # MONOTONIC; age and TTL only, never a time of day


@dataclass(frozen=True)
class RepoCount:
    name: str
    agents: int
    shared: bool = False            # more than one agent in this tree


@dataclass(frozen=True)
class BandView:
    host: str = "local"
    total: int = 0
    yours: int = 0
    ephemeral: int = 0
    by_kind: tuple[tuple[str, int], ...] = ()   # (("queue", 2), ("workflow", 1))
    working: int = 0
    ready: int = 0
    waiting: int = 0
    ctx_avg: float = 0.0
    ctx_worst: tuple[str, float] | None = None  # (handle, pct)
    cost_live: float = 0.0          # sum over OPEN sessions — not a daily total
    recap_cost: float = 0.0
    recap_calls: int = 0
    queues: tuple[int, int] = (0, 0)            # (running, configured)
    monitors: int = 0
    repos: tuple[RepoCount, ...] = ()
    clock: str = ""


@dataclass(frozen=True)
class FleetSnapshot:
    band: BandView = field(default_factory=BandView)
    cards: tuple[CardView, ...] = ()
```

- [x] **Step 2: Restore the package exports**

Task 2 wrote `src/aegis/fleet/__init__.py` exporting only `Origin`, because the other four names did not exist yet. They do now. Widen it:

```python
from aegis.fleet.models import BandView, CardView, EventLine, FleetSnapshot, Origin, RepoCount

__all__ = ["BandView", "CardView", "EventLine", "FleetSnapshot", "Origin", "RepoCount"]
```

- [x] **Step 3: Import check**

Run: `uv run python -c "from aegis.fleet import FleetSnapshot, CardView, BandView, RepoCount; print(FleetSnapshot())"`
Expected: prints a `FleetSnapshot` with an empty band and no cards. Import from the package, not the module, so the step actually exercises Step 2.

- [x] **Step 4: Commit**

```bash
git commit -m "feat(fleet): the snapshot dataclasses the renderer reads" -- src/aegis/fleet/models.py src/aegis/fleet/__init__.py
```

### Task 6: assembly from the live manager

**Files:**
- Create: `src/aegis/fleet/snapshot.py`
- Test: `tests/test_fleet_snapshot.py`

**Interfaces:**
- Consumes: `SessionManager` (`list_sessions()`, `_sessions`), `AgentSession` (`.origin`, `.metrics`, `.plan`, `.recent_events`, `.place`, `.state`, `.repo_tracker`), `RepoTracker.snapshot(for_handle="")`, `manager.locks.active()`, `manager.monitor_manager.snapshot(for_handle=...)`, `manager.queue_manager`.

> **Real attribute names — the first draft of this task guessed three of them
> wrong.** Verified against `core/manager.py` before dispatch:
>
> | the draft said | what exists |
> |---|---|
> | `manager.repo_tracker` | **no such attribute.** The tracker is app-wide and every session carries it as `s.repo_tracker` (`core/session.py:163`). Read it off the first session that has one. |
> | `manager.claims` | **no such attribute.** `manager.locks` (`core/manager.py:116,156`), whose `.active()` returns `Claim`s with a `.handle`. |
> | `manager.monitors` | **no such attribute.** `manager.monitor_manager` (`:85`, set by `attach_monitor_manager`), whose `.snapshot(for_handle=h)` returns that session's `MonitorView`s. `MonitorView` has no `from_handle` field, so filter with `for_handle`, not by reading the views. |
>
> All three are `None` on a manager nothing attached them to — every headless
> caller and most of the suite — so each read is guarded.
>
> **For the `waiting` rule's queue half**, match on the callback target, not
> on the sender: `Task.enqueued_by` is a sender tag (`agent:<handle>`, see
> `queue/schema.py::sender_agent`), so comparing it to a bare handle silently
> never matches. A session is waiting on a queue task when some task in
> `queue_manager._workers.values()` or `queue_manager._pending.values()` has
> `task.callback` true and `task.callback_handle == s.handle`.
- Produces: `build_snapshot(manager, *, now: float, ghosts: dict[str, tuple[CardView, float]] | None = None) -> FleetSnapshot`.

- [x] **Step 1: Write the failing test**

```python
"""Assembly reads live state and nothing from disk.

The fake manager below is shaped like the real one on purpose: it is the
attributes `build_snapshot` actually reaches for, so a rename upstream
breaks this test instead of production."""
from dataclasses import dataclass

from aegis.config import Agent
from aegis.fleet.models import Origin
from aegis.fleet.snapshot import build_snapshot
from aegis.plan.models import PlanState, PlanTask
from aegis.tui.metrics import SessionMetrics


@dataclass
class FakePlace:
    host: str = "local"
    cwd: str = "/home/apiad/Workspace/repos/une-tools"


class FakeSession:
    def __init__(self, handle, state="ready", origin=None, **kw):
        self.handle = handle
        # Carry a real profile so _cost exercises budget.cost.compute rather
        # than only its guard.
        self.agent = Agent(harness="claude-code", model="opus")
        self.title = kw.get("title", "")
        self.agent_slug = kw.get("agent_slug", "opus")
        self.origin = origin or Origin()
        self.place = FakePlace()
        self.state = type("S", (), {"value": state})()
        self.metrics = SessionMetrics(context_window=200_000)
        self.plan = kw.get("plan")
        self.recent_events = ()
        self._last_recap_line = kw.get("did", "")


class FakeManager:
    def __init__(self, sessions):
        self._sessions = list(sessions)
        # The real names — see the table above. None, as on a bare manager.
        self.locks = None
        self.monitor_manager = None
        self.queue_manager = None

    def list_sessions(self):
        return []


def test_an_empty_fleet_is_an_empty_snapshot():
    snap = build_snapshot(FakeManager([]), now=1000.0)
    assert snap.cards == ()
    assert snap.band.total == 0


def test_one_card_per_session_in_tab_order():
    m = FakeManager([FakeSession("alpha"), FakeSession("beta"), FakeSession("gamma")])
    snap = build_snapshot(m, now=1000.0)
    assert [c.handle for c in snap.cards] == ["alpha", "beta", "gamma"]
    assert [c.tab_index for c in snap.cards] == [1, 2, 3]


def test_the_band_counts_yours_against_the_ephemeral():
    m = FakeManager([
        FakeSession("alpha"),
        FakeSession("beta"),
        FakeSession("w1", origin=Origin(kind="queue", by="general")),
        FakeSession("w2", origin=Origin(kind="queue", by="general")),
        FakeSession("w3", origin=Origin(kind="workflow", by="review")),
    ])
    band = build_snapshot(m, now=1000.0).band
    assert (band.total, band.yours, band.ephemeral) == (5, 2, 3)
    assert dict(band.by_kind) == {"queue": 2, "workflow": 1}


def test_states_are_counted_separately():
    m = FakeManager([
        FakeSession("a", state="working"),
        FakeSession("b", state="ready"),
        FakeSession("c", state="ready"),
    ])
    band = build_snapshot(m, now=1000.0).band
    assert (band.working, band.ready) == (1, 2)


def test_the_plan_reaches_the_card():
    tasks = tuple(
        PlanTask(key=str(i), subject=f"t{i}",
                 status="completed" if i < 7 else "pending")
        for i in range(10)
    )
    plan = type("P", (), {"snapshot": lambda self, ts: PlanState(tasks=tasks)})()
    m = FakeManager([FakeSession("alpha", plan=plan)])
    card = build_snapshot(m, now=1000.0).cards[0]
    assert (card.plan_done, card.plan_total) == (7, 10)


def test_the_band_states_are_disjoint_and_sum_to_the_total():
    m = FakeManager([
        FakeSession("a", state="working"),
        FakeSession("b", state="ready"),
        FakeSession("c", state="error"),
    ])
    band = build_snapshot(m, now=1000.0).band
    assert band.working + band.ready + band.waiting + band.error == band.total == 3
    assert band.error == 1


def test_assembly_never_touches_the_disk(monkeypatch):
    """A dashboard that reads transcripts is a dashboard that stutters."""
    import builtins

    def boom(*a, **kw):
        raise AssertionError("build_snapshot opened a file")

    monkeypatch.setattr(builtins, "open", boom)
    build_snapshot(FakeManager([FakeSession("alpha")]), now=1000.0)
```

- [x] **Step 1b: Close the two gaps the Task 4 review found**

Append these to **`tests/test_fleet_events.py`**, not to the snapshot tests — that file already defines the local `session` fixture, and both are properties of the ring rather than of assembly. Task 4's four tests call `note_event` directly, so deleting the `self.note_event(ev)` line from `_fire_event` (`core/session.py:826`) leaves every one of them green, and the replay-skip property has no test at all.

```python
from aegis.events import ToolUse


def test_a_tool_call_on_the_live_path_reaches_the_ring(session):
    """The seam, not the method. Delete the note_event call from
    _fire_event and this goes red; the four tests above do not."""
    session._fire_event(ToolUse(name="Edit", summary="apps/sigere/pusher.py"))
    assert [e.tool for e in session.recent_events] == ["Edit"]


def test_a_replayed_transcript_does_not_refill_the_ring(session):
    """Replay walks events through rehydrate_plan, which never calls
    _fire_event. A resumed session must start with an empty tail rather
    than one repainted from stale history."""
    session.rehydrate_plan([ToolUse(name="Edit", summary="old.py")], [1.0])
    assert session.recent_events == ()
```

`rehydrate_plan(self, events, stamps)` takes two parallel lists — verified at `core/session.py:957`, and its only caller passes `replay.events, replay.stamps` (`tui/pane.py:1004`). Then **mutation-check the first test**: delete the `self.note_event(ev)` line, confirm with `cmp` against `git show HEAD:src/aegis/core/session.py` that the file actually changed, confirm the test goes red, and restore.

- [x] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_fleet_snapshot.py -v`
Expected: FAIL — no module `aegis.fleet.snapshot`.

- [x] **Step 3: Write the assembly**

```python
"""Building a FleetSnapshot from the live manager.

Every read is in-memory. The one rule: nothing here opens a file. The
dashboard redraws on an event stream and a transcript read in that path
would stutter the whole grid.

Optional sources are optional on purpose. Headless callers and most of
the test suite hold a manager with no repo tracker, no claim registry and
no monitors, and a dashboard that raises rather than omitting a section
would take them all down.
"""

from __future__ import annotations

from aegis.fleet.models import BandView, CardView, FleetSnapshot, Origin, RepoCount


def build_snapshot(manager, *, now: float, ghosts=None) -> FleetSnapshot:
    sessions = list(getattr(manager, "_sessions", []))
    cards = tuple(
        _card(s, index=i + 1, now=now, manager=manager)
        for i, s in enumerate(sessions)
    )
    if ghosts:
        cards = cards + tuple(c for c, _died in ghosts.values())
    return FleetSnapshot(band=_band(cards, manager=manager, now=now), cards=cards)
```

**The state vocabulary, which Task 5's dataclasses left undefined.** `CardView.state` holds the session's own `AgentState` value: `ready`, `working` or `error`. `BandView` counts `working`, `ready` and `waiting`, and has no `error` counter. Resolve it this way, and add `error: int = 0` to `BandView`:

| band counter | a session counts here when |
|---|---|
| `working` | `state == "working"` |
| `waiting` | `state == "ready"` **and** it has at least one live monitor it armed, or at least one in-flight queue task it enqueued with `callback=True` |
| `ready` | `state == "ready"` and not waiting |
| `error` | `state == "error"` |

The four are disjoint and sum to `total`. Assert that in a test. `waiting` is what Alex asked for in the brainstorm — *"si están esperando por otros"* — and an idle session with a monitor armed or a callback pending is exactly an agent that ended its turn to wait. `waiting_on` stays empty until the comms ledger is held in memory (see Deferred); `waiting` does not depend on it.

Monitors carry `from_handle`; queue tasks carry `enqueued_by`. Both are live state, so the no-disk rule holds.

Write `_card` and `_band` to fill the fields Task 5 declared, reading:

- `s.metrics.session_seconds(now)` → `uptime_s`; `s.metrics.turn_seconds(now)` → `turn_s` (0 when `turn_start` is None); `s.metrics.last_true_input / s.metrics.context_window` → `ctx_pct`.
- `s.plan.snapshot(now)` → `plan_done`/`plan_total`/`plan_current`, guarded with `getattr(s, "plan", None)`.
- the first session's `s.repo_tracker.snapshot()` → a `{handle: RepoView}` index for the `repo` string and the band's `RepoCount` rows, where `RepoCount.shared` is `RepoView.shared`.
- `manager.locks.active()` → count per handle.
- `manager.monitor_manager.snapshot(for_handle=s.handle)` → the live-monitor string, and the monitor half of `waiting`.
- the day's `CommsLedger.read(day)` → `spoke_with` / `waiting_on` per handle. If the ledger is not already held in memory by the manager, **leave both tuples empty and note it**: reading the JSONL here would violate the no-disk rule. Wiring an in-memory tail of the ledger is its own task if the edges turn out to matter.

`_card` in full, so the optional-source guards are not left to taste:

```python
def _card(s, *, index: int, now: float, manager) -> CardView:
    m = s.metrics
    plan = getattr(s, "plan", None)
    ps = plan.snapshot(now) if plan is not None else None
    repos = _repo_index(manager)
    view = repos.get(s.handle)
    return CardView(
        handle=s.handle,
        title=getattr(s, "title", ""),
        state=s.state.value,
        agent_slug=getattr(s, "agent_slug", ""),
        host=getattr(getattr(s, "place", None), "host", "local"),
        repo=_repo_label(view),
        origin=getattr(s, "origin", None) or Origin(),
        uptime_s=m.session_seconds(now),
        # turn_start is None between turns; session_seconds would otherwise
        # read as a turn that has been running since the session began.
        turn_s=m.turn_seconds(now) if m.turn_start is not None else 0.0,
        cost_usd=_cost(s),
        ctx_pct=(100.0 * m.last_true_input / m.context_window)
        if m.context_window
        else 0.0,
        plan_done=ps.done if ps else 0,
        plan_total=ps.total if ps else 0,
        plan_current=(ps.current.subject if ps and ps.current else ""),
        did=getattr(s, "_last_recap_line", ""),
        doing=getattr(getattr(s, "fleet_recap", None), "doing", ""),
        events=getattr(s, "recent_events", ()),
        claims=_claims_for(manager, s.handle),
        monitor=_monitor_label(manager, s.handle),
        tab_index=index,
    )
```

```python
def _cost(s) -> float:
    """List-price cost so far. A session with no resolved agent profile —
    every headless caller and most of the suite — costs 0.0 rather than
    raising, for the same reason the other sources are optional."""
    agent = getattr(s, "agent", None)
    if agent is None:
        return 0.0
    return float(compute(s.metrics, agent.harness, agent.model).usd)
```

`compute` is `aegis.budget.cost.compute(metrics, provider, model) -> Cost`; take `.usd`, which is a `Decimal`.

**`SessionMetrics` has no turn counter**, so the card carries no turn count and the spec's mockup row reads `1h47m · $2.14` alone. Adding a counter to `SessionMetrics` for a decoration the uptime and the cost already imply is not worth a field that every other consumer has to keep correct.

Each `_`-prefixed helper returns the empty value when its source is absent:
`_repo_index` returns `{}` when no session carries a `repo_tracker`,
`_claims_for` returns 0 when `manager.locks` is None, `_monitor_label` returns
`""` when `manager.monitor_manager` is None. Headless callers and most of the test suite hold a
manager with none of the three, and a dashboard that raises rather than
omitting a section takes them all down.

- [x] **Step 4: Run the tests**

Run: `uv run pytest tests/test_fleet_snapshot.py -v`
Expected: PASS, all six.

- [x] **Step 5: Commit**

```bash
git commit -- src/aegis/fleet/snapshot.py tests/test_fleet_snapshot.py -m "feat(fleet): assemble a snapshot from live state, never from disk"
```

### Task 7: the card renderer

**Files:**
- Create: `src/aegis/fleet/render.py`
- Test: `tests/test_fleet_render.py`

**Interfaces:**
- Consumes: `CardView`, `aegis.tui.themes.aegis_colors`, `aegis.tui.fit` helpers.
- Produces: `render_card(card: CardView, palette, width: int) -> Text`.

- [x] **Step 1: Write the failing test**

```python
"""The pure card renderer. Chrome is English; content is the session's own."""
from aegis.fleet.models import CardView, EventLine, Origin
from aegis.tui.themes import INK, aegis_colors

C = aegis_colors(INK)
W = 42


def as_text(renderable) -> str:
    return renderable.plain


def test_a_card_leads_with_the_handle_and_the_title():
    out = as_text(render_card(CardView(handle="une-tools-tasks",
                                       title="ordenar tareas"), C, W))
    assert "une-tools-tasks" in out
    assert "ordenar tareas" in out


def test_the_plan_renders_as_a_bar_with_its_counts():
    out = as_text(render_card(CardView(handle="a", plan_done=7, plan_total=10), C, W))
    assert "7/10" in out


def test_a_session_with_no_plan_draws_no_plan_row():
    out = as_text(render_card(CardView(handle="a"), C, W))
    assert "plan" not in out


def test_the_two_recap_lines_are_labelled_in_english():
    out = as_text(render_card(
        CardView(handle="a", did="3 new tests", doing="closing the loop"), C, W))
    assert "did" in out and "3 new tests" in out
    assert "now" in out and "closing the loop" in out


def test_an_empty_doing_line_is_omitted_not_blank():
    out = as_text(render_card(CardView(handle="a", did="landed x"), C, W))
    assert "did" in out
    assert "now" not in out


def test_an_ephemeral_card_leads_with_its_origin_and_destination():
    card = CardView(handle="brisk-babbage",
                    origin=Origin(kind="queue", by="general", detail="a3f2",
                                  returns_to="rosy-rivest"))
    out = as_text(render_card(card, C, W))
    assert "queue general #a3f2" in out
    assert "rosy-rivest" in out


def test_no_row_exceeds_the_width():
    """Textual clips an over-long line silently — the reason aegis.tui.fit
    exists. A card that overflows corrupts the whole grid's columns."""
    from rich.cells import cell_len

    card = CardView(handle="a-very-long-handle-indeed",
                    title="un titulo larguisimo que no cabe de ninguna manera",
                    did="x" * 200, doing="y" * 200,
                    events=tuple(EventLine(at=0, tool="Bash", summary="z" * 120)
                                 for _ in range(5)))
    for row in as_text(render_card(card, C, W)).split("\n"):
        assert cell_len(row) <= W, f"row overflows: {row!r}"
```

- [x] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_fleet_render.py -v`
Expected: FAIL — `render_card` is not importable.

- [x] **Step 3: Write the renderer**

Add `from aegis.fleet.render import render_card` to the test, then write the renderer. Follow `aegis/tui/sidebar.py` for style: a `rich.text.Text`, measured with `rich.cells.cell_len`, truncated through `aegis.tui.fit`, and a row omitted entirely when its content is empty rather than drawn blank.

```python
from rich.cells import cell_len
from rich.text import Text

from aegis.fleet.models import CardView
from aegis.tui.fit import truncate_cells

CARD_WIDTH = 46
GUTTER = 2
_BAR = "█"
_EMPTY = "░"


def _bar(done: int, total: int, cells: int = 10) -> str:
    """A progress bar that never lies about zero: 0/10 draws no full cell,
    and 10/10 draws no empty one."""
    if total <= 0:
        return ""
    filled = round(cells * done / total)
    return _BAR * filled + _EMPTY * (cells - filled)


def _row(t: Text, body: Text | str, *, width: int, pal, edge: str) -> None:
    """One bordered row, truncated to fit. Textual clips silently, so the
    truncation is ours or the grid's columns break."""
    inner = width - 4
    # aegis.tui.fit.truncate_cells — there is no `fit_one`; `fit` and
    # `fit_rows` take Segments and are for status rows, not card bodies.
    if isinstance(body, str):
        body = Text(truncate_cells(body, inner))
    else:
        body = Text(truncate_cells(body.plain, inner), style=body.style)
    pad = inner - cell_len(body.plain)
    t.append(f"{edge} ", style=pal.muted)
    t.append_text(body)
    t.append(" " * max(0, pad) + f" {edge}\n", style=pal.muted)


def render_card(card: CardView, pal, width: int) -> Text:
    eph = card.origin.ephemeral or card.ghost_since is not None
    edge, fill = ("┆", "┄") if eph else ("│", "─")
    style = pal.muted if eph else _state_style(card.state, pal)
    t = Text()
    _cap(t, card, width, fill=fill, pal=pal, style=style)     # top border + handle + age
    if eph:
        _row(t, _origin_line(card.origin), width=width, pal=pal, edge=edge)
    if card.title:
        _row(t, card.title, width=width, pal=pal, edge=edge)
    _row(t, _identity(card), width=width, pal=pal, edge=edge)
    if card.plan_total:
        _row(t, f"plan {_bar(card.plan_done, card.plan_total)} "
                f"{card.plan_done}/{card.plan_total}",
             width=width, pal=pal, edge=edge)
    ...
    return t
```

`_cap`, `_origin_line`, `_identity`, `_state_style` and the `did` / `now` /
event rows follow the same shape. `_origin_line` renders
`queue general #a3f2 → rosy-rivest`, dropping the arrow when `returns_to`
is empty. Every row goes through `_row`, which is what makes the width test
in Step 1 a real gate rather than a coincidence.

- [x] **Step 4: Run the tests**

Run: `uv run pytest tests/test_fleet_render.py -v`
Expected: PASS, all seven. The width test is the one that matters; if it fails, the fix is in the truncation, never in the test's budget.

- [x] **Step 5: Commit**

```bash
git commit -- src/aegis/fleet/render.py tests/test_fleet_render.py -m "feat(fleet): the pure card renderer"
```

### Task 8: the grid and the band

**Files:**
- Modify: `src/aegis/fleet/render.py`
- Test: `tests/test_fleet_render.py` (append)

**Interfaces:**
- Produces: `render_fleet(snapshot: FleetSnapshot, palette, width: int) -> Text` and `columns_for(width: int) -> int`.

> **Carried from the Task 6 review.** Three facts about the band the
> renderer must respect:
> - `total` counts **live** sessions only. Ghost cards are drawn but not
>   counted, so while a ghost is on screen there is one more card than
>   `total`. Do not derive the headline count from `len(snapshot.cards)`.
> - The cost field is **`cost_live`**, the sum over open sessions. Label it
>   `live`, never `today` — it drops when a tab closes.
> - `band.host` is the local machine's hostname, not the first card's host.

- [x] **Step 1: Write the failing tests**

```python
from aegis.fleet.models import BandView, FleetSnapshot, RepoCount
from aegis.fleet.render import columns_for, render_fleet


def test_an_empty_fleet_says_so_rather_than_drawing_nothing():
    out = as_text(render_fleet(FleetSnapshot(), C, 120))
    assert "no sessions" in out.lower()


def test_columns_follow_the_width():
    assert columns_for(40) == 1
    assert columns_for(120) == 2
    assert columns_for(200) == 4


def test_a_narrow_terminal_still_gets_one_column():
    """Never zero: `width // 50` is 0 below 50 cells and would divide by it."""
    assert columns_for(10) == 1


def test_the_band_names_the_mix():
    band = BandView(host="zion", total=9, yours=6, ephemeral=3,
                    by_kind=(("queue", 2), ("workflow", 1)))
    out = as_text(render_fleet(FleetSnapshot(band=band), C, 160))
    assert "9 agents" in out
    assert "6 yours" in out
    assert "3 ephemeral" in out
    assert "2 queue" in out


def test_a_shared_repo_is_marked():
    """Two agents in one working tree is the condition that costs an
    afternoon, and no other surface in aegis shows it."""
    band = BandView(repos=(RepoCount(name="une-tools", agents=2, shared=True),
                           RepoCount(name="aegis", agents=1)))
    out = as_text(render_fleet(FleetSnapshot(band=band), C, 160))
    assert "une-tools ×2" in out
    assert "⚠" in out


def test_the_recap_spend_rides_in_the_band():
    """A paid call whose bill is not on screen is a paid call nobody audits."""
    out = as_text(render_fleet(
        FleetSnapshot(band=BandView(recap_cost=1.20, recap_calls=340)), C, 160))
    assert "1.20" in out
    assert "340" in out


def test_the_grid_never_exceeds_the_terminal_width():
    from rich.cells import cell_len

    cards = tuple(CardView(handle=f"session-{i}", title="x" * 60) for i in range(9))
    out = as_text(render_fleet(FleetSnapshot(cards=cards), C, 160))
    for row in out.split("\n"):
        assert cell_len(row) <= 160
```

- [x] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_fleet_render.py -v`
Expected: the seven new tests FAIL on the import of `render_fleet` / `columns_for`.

- [x] **Step 3: Write the grid**

`CARD_WIDTH` and `GUTTER` already exist at the top of `render.py` from Task 7. Do **not** re-declare them; import nothing and add only:

```python
def columns_for(width: int) -> int:
    """Never zero — a narrow terminal gets one column, not a ZeroDivisionError."""
    return max(1, width // (CARD_WIDTH + GUTTER))
```

`render_fleet` renders the band, then lays the cards out row by row: render each card to its own `Text`, split into lines, and zip the lines of each row of cards side by side with the gutter between. Cards in a row are padded to equal height so a short card does not pull the next row up.

- [x] **Step 4: Run the tests**

Run: `uv run pytest tests/test_fleet_render.py -v`
Expected: PASS, all fourteen.

- [x] **Step 5: Commit**

```bash
git commit -- src/aegis/fleet/render.py tests/test_fleet_render.py -m "feat(fleet): the grid, the band, and the shared-repo warning"
```

### Task 9: F10, the screen, and click-to-tab

**Files:**
- Create: `src/aegis/tui/fleet_screen.py`
- Modify: `src/aegis/tui/app.py` (BINDINGS near line 340; a new `action_open_fleet` beside `action_open_dashboard` at line 2129)
- Test: `tests/test_fleet_screen.py`

**Interfaces:**
- Consumes: `build_snapshot`, `render_fleet`, `AegisApp.action_goto(n)` (line 1807).
- Produces: `FleetScreen(ModalScreen)` with `selected: int` (1-based), `card_at(x, y) -> int | None`, and dismissal returning the chosen tab index or `None`.

- [x] **Step 1: Write the failing test**

```python
"""The screen's behaviour, not its pixels. The pixels are Task 7 and 8."""
import pytest

from aegis.fleet.models import CardView, FleetSnapshot
from aegis.tui.fleet_screen import FleetScreen

SNAP = FleetSnapshot(cards=tuple(
    CardView(handle=f"s{i}", tab_index=i + 1) for i in range(9)
))


def test_the_selection_starts_on_the_first_card():
    assert FleetScreen(lambda: SNAP).selected == 1


def test_arrows_move_the_selection_and_stop_at_the_ends():
    scr = FleetScreen(lambda: SNAP)
    scr.action_move(1)
    assert scr.selected == 2
    scr.action_move(-1)
    scr.action_move(-1)
    assert scr.selected == 1, "moving left off the first card must not wrap to the last"


def test_a_number_key_selects_that_card():
    scr = FleetScreen(lambda: SNAP)
    scr.action_pick(4)
    assert scr.chosen == 4


def test_a_number_beyond_the_fleet_is_inert():
    scr = FleetScreen(lambda: FleetSnapshot(cards=(CardView(handle="a", tab_index=1),)))
    scr.action_pick(7)
    assert scr.chosen is None


def test_a_ghost_card_cannot_be_opened():
    """An ephemeral session that died has no tab left to switch to."""
    snap = FleetSnapshot(cards=(
        CardView(handle="a", tab_index=1),
        CardView(handle="dead", tab_index=0, ghost_since=100.0),
    ))
    scr = FleetScreen(lambda: snap)
    scr.action_move(1)
    scr.action_open()
    assert scr.chosen is None
```

- [x] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_fleet_screen.py -v`
Expected: FAIL — no module `aegis.tui.fleet_screen`.

- [x] **Step 3: Write the screen**

Model it on `aegis/tui/dashboard.py`'s `QueueDashboard`: a `ModalScreen` holding one `Static`, refreshed from a callable returning the current snapshot. Redraw on the manager's event subscription **coalesced at 500 ms**, plus a 1 s `set_interval` for clocks and spinners.

The four members the tests touch hold no Textual call, which is what lets them run with no app:

```python
class FleetScreen(ModalScreen):
    def __init__(self, snap: Callable[[], FleetSnapshot]) -> None:
        super().__init__()
        self._snap = snap
        self.selected = 1          # 1-based, matches tab_index
        self.chosen: int | None = None

    def action_move(self, delta: int) -> None:
        """Clamped, never wrapped: a grid you can fall off the end of is a
        grid where the arrow key does something different each press."""
        n = len(self._snap().cards)
        if n:
            self.selected = max(1, min(n, self.selected + delta))

    def action_pick(self, n: int) -> None:
        cards = self._snap().cards
        if 1 <= n <= len(cards):
            self.selected = n
            self.action_open()

    def action_open(self) -> None:
        cards = self._snap().cards
        card = cards[self.selected - 1] if 1 <= self.selected <= len(cards) else None
        # A ghost is a dead ephemeral session: there is no tab to switch to.
        if card is None or card.ghost_since is not None or not card.tab_index:
            return
        self.chosen = card.tab_index
        self.dismiss(card.tab_index)
```

`on_click` maps `(x, y)` to a card through `card_at`, using `CARD_WIDTH + GUTTER` for the column and the rendered card height for the row, sets `selected`, and calls `action_open`.

- [x] **Step 4: Wire F10**

In `src/aegis/tui/app.py` BINDINGS, beside the F3 line:

```python
        Binding("f10", "open_fleet", "Fleet", priority=True),
```

and beside `action_open_dashboard`:

```python
    async def action_open_fleet(self) -> None:
        from aegis.fleet.snapshot import build_snapshot
        from aegis.tui.fleet_screen import FleetScreen

        def snap():
            return build_snapshot(self._manager, now=time.monotonic())

        tab = await self.push_screen_wait(FleetScreen(snap))
        if tab:
            self.action_goto(tab)
```

Use whatever attribute this app actually holds its manager under — check, do not assume `self._manager`.

`action_interrupt` (line 2134) already dismisses a `ModalScreen` on escape, so escape works with no further change. Confirm that by reading it rather than assuming.

- [x] **Step 5: Run the tests**

Run: `uv run pytest tests/test_fleet_screen.py -v`
Expected: PASS, all five.

- [x] **Step 6: Commit**

```bash
git commit -- src/aegis/tui/fleet_screen.py src/aegis/tui/app.py tests/test_fleet_screen.py -m "feat(tui): F10 opens the fleet, and a card opens its tab"
```

### Task 9b: ghosts — an ephemeral worker that nobody saw

The spec asks for a dead ephemeral card to linger 60 seconds. Nothing above
populates that: `CardView.ghost_since` exists and `build_snapshot` takes a
`ghosts` argument, but no one fills or reaps it. This task is that.

**Files:**
- Create: `src/aegis/fleet/ghosts.py`
- Modify: `src/aegis/tui/fleet_screen.py`
- Test: `tests/test_fleet_ghosts.py`

**Interfaces:**
- Consumes: `CardView`, `Origin.ephemeral`.
- Produces: `GhostBook` with `observe(cards, now) -> None`, `alive(now) -> dict[str, tuple[CardView, float]]`, and the constant `GHOST_TTL = 60.0`.

- [x] **Step 1: Write the failing test**

```python
"""A queue worker can live forty seconds. Without a ghost it appears and
vanishes between two glances, and the dashboard lies by omission."""
from aegis.fleet.ghosts import GHOST_TTL, GhostBook
from aegis.fleet.models import CardView, Origin

Q = Origin(kind="queue", by="general", detail="a3f2")


def card(handle, origin=Q, **kw):
    return CardView(handle=handle, origin=origin, **kw)


def test_a_live_fleet_leaves_no_ghosts():
    b = GhostBook()
    b.observe((card("w1"),), now=100.0)
    assert b.alive(now=100.0) == {}


def test_a_departed_ephemeral_becomes_a_ghost():
    b = GhostBook()
    b.observe((card("w1"),), now=100.0)
    b.observe((), now=140.0)
    ghosts = b.alive(now=141.0)
    assert list(ghosts) == ["w1"]
    assert ghosts["w1"][0].ghost_since == 140.0
    assert ghosts["w1"][0].tab_index == 0, "a ghost has no tab to open"


def test_a_ghost_expires_after_its_ttl():
    b = GhostBook()
    b.observe((card("w1"),), now=100.0)
    b.observe((), now=140.0)
    assert b.alive(now=140.0 + GHOST_TTL - 1) != {}
    assert b.alive(now=140.0 + GHOST_TTL + 1) == {}


def test_an_operator_tab_that_closes_leaves_no_ghost():
    """Only the substrate-born come and go on their own. A tab you closed,
    you closed — showing it back for a minute would read as a bug."""
    b = GhostBook()
    b.observe((card("mine", origin=Origin()),), now=100.0)
    b.observe((), now=140.0)
    assert b.alive(now=141.0) == {}


def test_a_worker_that_comes_back_is_not_also_a_ghost():
    """Handles are recycled out of a finite pool."""
    b = GhostBook()
    b.observe((card("w1"),), now=100.0)
    b.observe((), now=140.0)
    b.observe((card("w1"),), now=150.0)
    assert b.alive(now=151.0) == {}
```

- [x] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_fleet_ghosts.py -v`
Expected: FAIL — no module `aegis.fleet.ghosts`.

- [x] **Step 3: Write it**

```python
"""Ephemeral sessions that died, kept visible for a minute.

A queue worker is spawned, runs one task and is closed
(`queue/manager.py`). On a screen you glance at, that is invisible. The
book diffs successive snapshots and holds the departed long enough to be
read.
"""

from __future__ import annotations

from dataclasses import replace

from aegis.fleet.models import CardView

GHOST_TTL = 60.0


class GhostBook:
    def __init__(self) -> None:
        self._last: dict[str, CardView] = {}
        self._ghosts: dict[str, tuple[CardView, float]] = {}

    def observe(self, cards: tuple[CardView, ...], now: float) -> None:
        live = {c.handle: c for c in cards}
        for handle, card in self._last.items():
            if handle in live or not card.origin.ephemeral:
                continue
            self._ghosts[handle] = (
                replace(card, ghost_since=now, tab_index=0), now
            )
        # A recycled handle is a live session again, never also a ghost.
        for handle in live:
            self._ghosts.pop(handle, None)
        self._last = live

    def alive(self, now: float) -> dict[str, tuple[CardView, float]]:
        self._ghosts = {
            h: v for h, v in self._ghosts.items() if now - v[1] < GHOST_TTL
        }
        return dict(self._ghosts)
```

- [x] **Step 4: Hold one book per screen**

In `FleetScreen`, keep a `GhostBook`, call `observe` on every refresh with the manager's live cards, and pass `alive(now)` into `build_snapshot(..., ghosts=...)`. The book lives on the screen rather than on the manager because a ghost is a viewing artefact: a client that was not open has nothing to catch up on.

- [x] **Step 5: Run the tests**

Run: `uv run pytest tests/test_fleet_ghosts.py tests/test_fleet_screen.py -v`
Expected: PASS. The Task 9 test that a ghost cannot be opened now has a real producer behind it.

- [x] **Step 6: Commit**

```bash
git commit -- src/aegis/fleet/ghosts.py src/aegis/tui/fleet_screen.py tests/test_fleet_ghosts.py -m "feat(fleet): a dead ephemeral worker stays readable for a minute"
```

### Task 9c: the SYSTEM row in the band

Added mid-run at the operator's request. F10 is full screen, so while it is
open the F3 sidebar is hidden — and with it the SYSTEM meters (CPU, RAM,
disk), the provider quota and the running build. Those are app-wide facts,
not per-session ones, so the fleet band is where they belong.

**One source, two surfaces.** The app already samples `SystemStats` once per
tick and pushes the formatted tiers to the active pane
(`tui/app.py:1546-1552` → `pane.set_system`, stored as `pane._system_tiers`),
and pushes quota tiers the same way (`set_quota` → `pane._quota_tiers`). The
band reads **those same tier tuples**; it does not sample the host a second
time and does not format anything itself. F3 and F10 then show identical
numbers by construction. The sampling runs while F10 is up, because the
active pane still exists behind the modal.

**Dropped on purpose:** the working directory and the locale. `cwd` is the
active pane's directory, and in a view of the whole fleet every card already
names its own repo, so one directory in the band would describe one session
while sitting above all of them.

**Files:**
- Modify: `src/aegis/fleet/models.py` (`BandView`)
- Modify: `src/aegis/fleet/render.py` (`render_fleet`'s band)
- Modify: `src/aegis/tui/fleet_screen.py` (fill the fields at refresh)
- Test: `tests/test_fleet_render.py`, `tests/test_fleet_screen.py`

**Interfaces:**
- Consumes: `pane._system_tiers`, `pane._quota_tiers` (tuples of Rich-markup strings, widest first), `aegis.tui.sysmeter.format_build(palette)`, `aegis.tui.fit.Segment` and `aegis.tui.fit.fit(segments, width, sep)`.
- Produces: `BandView.system: tuple[str, ...] = ()`, `BandView.quota: tuple[str, ...] = ()`, `BandView.build: tuple[str, ...] = ()`.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_fleet_render.py`:

```python
def test_the_band_carries_the_system_row():
    band = BandView(system=("CPU 57% · RAM 48% · DSK 16%",),
                    quota=("cc 26/49%",), build=("aegis 0.37.0",))
    out = as_text(render_fleet(FleetSnapshot(band=band), C, 160))
    assert "CPU 57%" in out
    assert "cc 26/49%" in out
    assert "aegis 0.37.0" in out


def test_no_system_data_draws_no_system_row():
    """An empty section renders nothing — not a blank line in the band."""
    bare = as_text(render_fleet(FleetSnapshot(band=BandView()), C, 160))
    assert "CPU" not in bare
    assert "\n\n\n" not in bare


def test_a_narrow_band_sheds_the_build_before_the_meters():
    """The meters move every tick and the build never does, so on a narrow
    terminal the build is what goes."""
    band = BandView(system=("CPU 57% · RAM 48% · DSK 16%", "CPU 57%"),
                    quota=("cc 26/49%",), build=("aegis 0.37.0+12485c2",))
    out = as_text(render_fleet(FleetSnapshot(band=band), C, 40))
    assert "CPU 57%" in out
    assert "aegis 0.37.0" not in out
```

Append to `tests/test_fleet_screen.py` a test that the screen fills
`system`, `quota` and `build` from the app's active pane: a fake app whose
active pane carries `_system_tiers=("CPU 1%",)` and `_quota_tiers=("cc 1%",)`,
asserting the snapshot the screen renders has those tuples on its band, and
that with no active pane all three are empty.

- [x] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_fleet_render.py tests/test_fleet_screen.py -v`
Expected: the new tests FAIL on the unknown `BandView` keywords.

- [x] **Step 3: Add the fields**

```python
    # The SYSTEM row, as the same widest-first tier tuples the F3 sidebar
    # renders — filled by the screen from the active pane, never sampled or
    # formatted here, so F3 and F10 cannot disagree.
    system: tuple[str, ...] = ()
    quota: tuple[str, ...] = ()
    build: tuple[str, ...] = ()
```

- [x] **Step 4: Render the row**

In `render_fleet`, after the band's existing lines, add one line built with
the horizontal fitter the status bar already uses, meters first:

```python
    row = fit(
        [
            Segment("system", band.system, 3),
            Segment("quota", band.quota, 2),
            Segment("build", band.build, 1),
        ],
        width,
        sep=" · ",
    )
    if row:
        t.append("\n")
        t.append_text(Text.from_markup(row))
```

Check `fit`'s real behaviour for an empty `tiers` tuple before relying on it:
a segment with no tiers must be skipped, not rendered as an empty cell with a
separator beside it. If it is not skipped, filter empty segments out first.

- [x] **Step 5: Fill it in the screen**

At refresh, read the app's active pane with `getattr(pane, "_system_tiers",
())` and `getattr(pane, "_quota_tiers", ())`, and `format_build(palette)`, and
put them on the band with `dataclasses.replace` — the same place the `clock`
string is set.

- [x] **Step 6: Run the tests**

Run: `uv run pytest tests/test_fleet_render.py tests/test_fleet_screen.py -v`
Expected: PASS.

- [x] **Step 7: See it**

Re-run `.playground/fleet-render/shoot_f10.py` (written in Task 9) and open
the PNG: the band carries the CPU/RAM/DSK meters, the quota and the build,
and they match what F3 shows for the same app.

- [x] **Step 8: Commit**

```bash
git commit -m "feat(fleet): the band carries F3's SYSTEM row, since F10 hides F3" -- src/aegis/fleet/models.py src/aegis/fleet/render.py src/aegis/tui/fleet_screen.py tests/test_fleet_render.py tests/test_fleet_screen.py
```

### Task 10: exercise it the way a user reaches it

Green tests against a daemon that booted before the change prove nothing about the change.

> **Rewritten mid-run.** The first draft began with `aegis kill` and `aegis`.
> On the operator's machine that kills the live daemon and every agent in it,
> including the controller running this plan. It is replaced by the
> `aegis bench` rig, which builds a throwaway world under `/tmp/aegis-bench-*`
> with **its own daemon**, a fake `claude` on `PATH` and a real client in a
> pty, and "never touches the daemon you are using"
> (`know-how/benchmarking.md`). That is a daemon started after the change,
> reached through a real terminal client — what `AGENTS.md` asks for — at no
> risk to the live fleet. The last check, F10 over the operator's own fleet,
> needs his daemon restarted and is his to schedule.

**Files:**
- Modify: `src/aegis/bench/scenarios.py`
- Test: whatever the bench's own tests use to register a scenario (read `tests/bench/`)
- Modify: `CHANGELOG.md`, `TASKS.md`

- [x] **Step 1: A `fleet` scenario in the bench rig**

Model it on `many_tabs` (`bench/scenarios.py:471`): boot, open several tabs
with background Claude-shaped streams so events flow, then **inside the
measurement window press F10** and keep pumping while the streams run, so the
window measures frames with the grid up and redrawing. Register it in the
scenario list the CLI reads, not in `DEFAULT` or `QUICK`.

- [x] **Step 2: Assert on what the pty client actually drew**

The rig reconstructs each client's screen (`Rig.screen()`, `rig.py:147`).
After F10, assert the screen shows the band (`agents`) and one card per tab
by handle; after pressing `2`, assert the fleet is gone and tab 2 is the
active tab in the tab bar. Fail the scenario with `BenchError` otherwise. This
is the step that proves F10 works in a daemon started after the change,
through a real terminal client.

- [x] **Step 3: A fork and a queue worker, if the rig can make them**

`tests/test_fleet_origin.py` covers the fork's *shape* through `_sync_spawn`,
not `SessionManager.fork()`, and workflow and group births have no test at
all. If the bench world can configure a queue (its `.aegis.yaml` is written in
`bench/world.py:69`) and the fake claude can serve a worker, enqueue one task
with F10 open and assert an ephemeral card appears and then becomes a ghost.
If the rig cannot do that without new infrastructure, **stop at Steps 1-2**,
and instead add one in-process test over `tests/brain.py::make_brain` that
calls the real `SessionManager.fork()` and asserts the child's
`origin.kind == "fork"`. Report which you did and why.

- [x] **Step 4: Run it and record the numbers**

```bash
AEGIS_BENCH_HOME=/tmp/fleet-bench uv run aegis bench run -s fleet --repeat 3
```

Record the frame-span and latency medians in `CHANGELOG.md`. Then run the
existing `many-tabs` scenario the same way, so the fleet's cost reads against
the same streams without the grid. `AEGIS_BENCH_HOME` keeps the throwaway runs
out of `~/.aegis/bench`.

- [x] **Step 5: No real accounts**

The bench world uses a fake `claude`, so it does not poll real providers.
Confirm the quota poll is also stubbed in that world; if it is not, stub it.
Screenshot and bench tooling must never poll the operator's real quota
accounts (a Task 9c screenshot run came back `cc rate limited`).

- [x] **Step 6: Commit**

```bash
git commit -m "feat(bench): a fleet scenario that opens F10 in a fresh daemon" -- src/aegis/bench/scenarios.py CHANGELOG.md TASKS.md <tests you added>
```

---

### Task 10b: the fleet screen repaints at 60 fps

Found by Task 10's bench run and characterised in its review. With F10 open
over six working sessions, the pty client receives **40 frames/s inside the
measurement window and 56–60 frames/s after it**, every frame a full-screen
repaint of ~14.7 KB — about 850 KB/s of terminal output. The same streams
without the modal draw 4–5 frames/s. The fleet's own coalescing is correct
(`fleet_screen.py:255-265`); something outside it is driving the repaints.
Until this is fixed the dashboard is not fit to sit open all day, which is its
whole purpose.

**Hypothesis, from the review — confirm it before fixing anything.**
`FleetScreen` is a `ModalScreen` (`fleet_screen.py:115`). Textual keeps the
screen below a modal in `app._background_screens` regardless of opacity
(`textual/app.py:1646-1652`), and `Screen._compositor_refresh` forwards that
background screen's dirty regions into the top screen
(`textual/screen.py:1226-1231`). The main screen stays dirty while sessions
work (tab-bar spinners, `✻ working…`, the elapsed clock), and each dirty
region becomes a full repaint of the modal. Read those Textual lines in the
installed package before relying on the line numbers.

**Files:**
- Modify: `src/aegis/tui/fleet_screen.py`, `src/aegis/tui/app.py`
- Test: `tests/test_fleet_screen.py`, the `fleet` bench scenario
- Modify: `CHANGELOG.md`, `TASKS.md`

- [x] **Step 1: Confirm the redraws are not the fleet's own**

In a scratch copy or behind a temporary env flag, count `FleetScreen._draw`
calls and emit the count where the bench can read it. Run
`AEGIS_BENCH_HOME=/tmp/fleet-bench uv run aegis bench run -s fleet` once. If
`_draw` runs ~2–3 times/s while the rig counts 40+ frames/s, the cause is
outside the fleet screen. Record both numbers. Remove the counter.

- [x] **Step 2: Test the hypothesis directly**

Change `class FleetScreen(ModalScreen)` to `class FleetScreen(Screen)` and run
the scenario again. If frames fall to ≤ 5/s with each frame still ~full size
or smaller, the background-screen forwarding is confirmed. If they do not,
**stop**: report the numbers and what you ruled out, and do not guess at a
second fix.

- [x] **Step 3: Make the fix real**

If Step 2 confirmed it, keep `Screen`, and repair what `ModalScreen` gave for
free:
- **Escape.** `AegisApp.action_interrupt` dismisses only a `ModalScreen`
  (`app.py:2229`). Escape over the fleet must still close it and must never
  interrupt a running turn in the pane behind. Prefer a priority `escape`
  binding on `FleetScreen` itself over widening the app's `isinstance` check.
- **Dismissal and the `push_screen(callback=)` path** must behave the same.
- Anything else that checks for `ModalScreen` — grep `src/aegis` for it.

Write a test that pins the cause: with the fleet open over a pane whose status
bar keeps changing, the fleet's screen must not be repainted on each change
(for example, count `refresh` calls or compositor updates on the fleet screen
in a `run_test` app while poking the background pane's spinner). It must fail
on `ModalScreen` and pass on `Screen`.

- [x] **Step 4: Measure again and correct the record**

Re-run `fleet --repeat 3` and `many-tabs --repeat 3`. In `CHANGELOG.md`, lead
the fleet entry with **frames/s and bytes/s**, then frame-tick times — the
first entry led with cheaper ticks and hid the 2.5× frame count. Update the
`TASKS.md` entry Task 10 filed: fixed, with before and after numbers.

- [x] **Step 5: Commit**

```bash
git commit -m "fix(tui): the fleet screen no longer repaints with the tabs behind it" -- src/aegis/tui/fleet_screen.py src/aegis/tui/app.py tests/test_fleet_screen.py CHANGELOG.md TASKS.md
```

---

# Slice 3 — the mid-turn recap

### Task 11: `{done, doing}`, and a recap of the turn in flight

**Files:**
- Modify: `src/aegis/recap/__init__.py` (beside `recap_turn` and `recap_session`)
- Test: `tests/test_fleet_recap.py`

**Interfaces:**
- Consumes: `aegis.btw.window.assemble`, `aegis.digest.render.render_facts`, the driver's `generate_detailed`, `aegis.btw.generation_agent`.
- Produces: `FleetRecap(BaseModel)` with `done: str` and `doing: str`; `async recap_in_flight(*, replay, facts, driver, agent, cwd) -> Recap`.

- [x] **Step 1: Write the failing test**

```python
"""The mid-turn recap: what this session is doing INSIDE a turn that has
not closed. The existing recap fires when a turn ends, which is exactly
the moment the answer stops being useful to a dashboard."""
import asyncio

from aegis.drivers.oneshot import Generation
from aegis.digest.models import TurnFacts
from aegis.recap import FleetRecap, recap_in_flight


class FakeDriver:
    supports_oneshot = True

    def __init__(self, value=None, fail=False):
        self.value = value
        self.fail = fail
        self.calls = []

    async def generate_detailed(self, agent, cwd, schema, *instructions):
        self.calls.append((schema, instructions))
        if self.fail:
            return Generation()
        return Generation(value=self.value, model="haiku", duration_ms=4700,
                          cost_usd=0.00363)


def test_a_good_call_returns_both_lines(fake_agent):
    d = FakeDriver(FleetRecap(done="landed 3 tests", doing="closing the loop"))
    r = asyncio.run(recap_in_flight(replay=[], facts=TurnFacts(), driver=d,
                                    agent=fake_agent, cwd="."))
    assert r.ok is True
    assert r.done == "landed 3 tests"
    assert r.doing == "closing the loop"


def test_a_failed_call_is_a_missing_answer_not_an_exception(fake_agent):
    """Best-effort by contract, like titlegen: a dashboard must not break
    because a $0.0036 call did not come back."""
    d = FakeDriver(fail=True)
    r = asyncio.run(recap_in_flight(replay=[], facts=TurnFacts(), driver=d,
                                    agent=fake_agent, cwd="."))
    assert r.ok is False
    assert r.done == "" and r.doing == ""


def test_the_cost_rides_back_with_the_answer(fake_agent):
    """The band shows the running recap spend; it can only do that if each
    call reports what it cost."""
    d = FakeDriver(FleetRecap(done="a", doing="b"))
    r = asyncio.run(recap_in_flight(replay=[], facts=TurnFacts(), driver=d,
                                    agent=fake_agent, cwd="."))
    assert r.cost_usd == 0.00363


def test_the_window_is_the_generous_one(fake_agent):
    """Measured 2026-09-16: a full window costs ~546 tokens over the 1,027
    floor, $0.0012, and produces lines that name files and counts. Squeezing
    it saves nothing and measurably degrades the answer."""
    from aegis.recap import IN_FLIGHT_WINDOW

    assert IN_FLIGHT_WINDOW["budget_tokens"] >= 2_000
```

- [x] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_fleet_recap.py -v`
Expected: FAIL — `FleetRecap` / `recap_in_flight` not importable from `aegis.recap`.

- [x] **Step 3: Write it**

> **Placement and three corrections, verified before dispatch.**
> - The in-flight recap lives **in `aegis/recap/__init__.py`, beside
>   `recap_turn` and `recap_session`**, and reuses the module's `_one` helper
>   rather than a parallel `aegis/fleet/recap.py`. One module owns recap
>   generation; a fleet module importing `_one` across packages would be the
>   private reach the Task 6 review flagged.
> - **`Recap` has no `doing` field** (it has `line`, `building`, `done`,
>   `remaining`). Add `doing: str = ""`, and map it in `_one` with
>   `doing=getattr(v, "doing", "")` beside the others.
> - **`render_facts(None)` raises** — it reads `facts.error`. Mid-turn facts
>   come from the session's live `digest`; the tests pass `TurnFacts()`.
> - **`TURN_WINDOW` already exists** in that module (`max_turns=1,
>   budget_tokens=2_000`). The new window is **`IN_FLIGHT_WINDOW`**.

Model it on `src/aegis/recap/__init__.py`: same `Recap` dataclass for the result, same "every failure comes back as `ok=False`, never as an exception" contract, same `generation_agent` resolution so it bills to `text_generation:`. The schema:

```python
class FleetRecap(BaseModel):
    done: str = Field(description="ONE line, past tense: the last thing that "
                                  "actually landed. Name files and counts. No preamble.")
    doing: str = Field(description="ONE line, present tense: what the turn "
                                   "currently running is working on.")
```

```python
# Measured 2026-09-16: the prefix floor is ~1,027 input tokens and a full
# window costs ~546 more, so the window is a minor share of the cost and gets sized for
# relevance rather than thrift. Squeezing it to ~1,135 total produced
# terser lines that named no files.
IN_FLIGHT_WINDOW = dict(max_turns=2, budget_tokens=2_500, item_chars=240)
```

- [x] **Step 4: Run the tests**

Run: `uv run pytest tests/test_fleet_recap.py -v`
Expected: PASS, all four.

- [x] **Step 5: Commit**

```bash
git commit -m "feat(recap): a mid-turn recap, {done, doing}, best-effort by contract" -- src/aegis/recap/__init__.py tests/test_fleet_recap.py
```

### Task 12a: a session reads its generation config

Added mid-run from the Task 13 review, which traced the config and found
**no path from `.aegis.yaml` to a session**. `load_config` parses `recap:`
and `loop_judge:`, but `BootConfig` does not carry them, `_session_factory`
receives no config, and `AgentSession` hard-codes `recap_enabled = True` and
`loop_judge_enabled = True` (`core/session.py:181-182`). **Setting
`recap: false` or `loop_judge: false` does nothing today** — both keep
spending. Task 12's gate needs `cfg.fleet` in the session and would hit the
same wall, with tests that build `FleetConfig` by hand and pass anyway.

**Follow the precedent already in the repo.** `text_generation:` is not
threaded through constructors; `btw.generation_agent` reads it **at call
time** and says why: a config read is nothing next to the API call it
precedes. Do the same for `recap`, `loop_judge` and `fleet`, with one fix to
the precedent: read from an **explicit config root**, not from
`find_project_root()` and the cwd — `know-how/embedding-aegis.md` explains
that `Path.cwd()` was replaced by three roots that must stay threaded.

**Files:**
- Modify: `src/aegis/core/session.py` (accept `config_root`; replace the two hard-coded booleans)
- Modify: `src/aegis/core/manager.py` (`_sync_spawn` passes `self.roots.config_root`)
- Test: `tests/test_session_generation_config.py`

**Interfaces:**
- Produces: `AgentSession(..., config_root: Path | None = None)`; properties `AgentSession.recap_enabled -> bool`, `AgentSession.loop_judge_enabled -> bool`, `AgentSession.fleet_config -> FleetConfig`. Each reads `yaml_loader.load_config(config_root)`, cached on the file's `(mtime_ns, size)`, so an edited `.aegis.yaml` takes effect on the next check without a restart. No `config_root`, or any failure to load, returns the defaults (`True`, `True`, `FleetConfig()`) — a broken config must not cost a running session its turn, as in `generation_agent`.

- [x] **Step 1: The failing test, end to end**

The test that matters builds a **real** manager with `tests/brain.py::make_brain` over a temp roots whose `.aegis.yaml` says `recap: false`, `loop_judge: false` and a `fleet:` block, spawns a session through `_sync_spawn`, and asserts the session reports all three. It must fail today. Add:
- editing the file (a new mtime) changes the answer on the next read without respawning;
- a malformed `.aegis.yaml` yields the defaults and does not raise;
- **`recap: false` actually stops the automatic turn recap**: drive `_maybe_recap`/the turn-end path with facts that moved and assert no recap task is created. A flag nobody reads is exactly the bug being fixed.

- [x] **Step 2: Implement** — keep the attribute names `recap_enabled` and `loop_judge_enabled` so the two readers (`session.py:843`, `session.py:1079`) are untouched, but make them read-through properties. Check whether anything *assigns* them (tests may); if so, keep a setter that overrides the file, and say so.

- [x] **Step 3: Cost check** — the fleet gate will read `fleet_config` on every check of every watched working session. With the `(mtime_ns, size)` cache a check is one `stat`. Assert in a test that two consecutive reads parse the YAML once.

- [x] **Step 4: Commit**

```bash
git commit -F - -- src/aegis/core/session.py src/aegis/core/manager.py tests/test_session_generation_config.py
```

---

### Task 12: the gate — pay only for a working session someone is watching

> **Run Task 13 first.** The tests below import `FleetConfig`, which Task 13
> creates. The plan numbers them in reading order — gate then config — but
> the execution order is 11, 13, 12, 14.

**Files:**
- Modify: `src/aegis/recap/gate.py` (beside `should_recap`)
- Modify: `src/aegis/core/session.py`
- Test: `tests/test_fleet_recap.py` (append)

**Interfaces:**
- Produces: `should_fleet_recap(*, state, turn_s, since_last_s, watchers, cfg) -> bool`; `AgentSession.add_fleet_watcher(cb)` / `remove_fleet_watcher(cb)`; `AgentSession.fleet_recap: Recap | None`.

- [x] **Step 1: Write the failing tests**

```python
from aegis.config import FleetConfig
from aegis.recap.gate import should_fleet_recap

ON = FleetConfig(recap="watched", recap_after_s=60, recap_interval_s=120)


def test_an_idle_session_is_never_worth_a_call():
    """Its last turn recap already exists and already says what landed."""
    assert should_fleet_recap(state="ready", turn_s=0, since_last_s=999,
                              watchers=1, cfg=ON) is False


def test_a_young_turn_waits():
    """Under a minute you would read the line before it refreshed."""
    assert should_fleet_recap(state="working", turn_s=30, since_last_s=999,
                              watchers=1, cfg=ON) is False


def test_a_working_watched_turn_past_the_threshold_fires():
    assert should_fleet_recap(state="working", turn_s=61, since_last_s=999,
                              watchers=1, cfg=ON) is True


def test_nobody_watching_means_nobody_pays():
    assert should_fleet_recap(state="working", turn_s=999, since_last_s=999,
                              watchers=0, cfg=ON) is False


def test_the_interval_holds_between_calls():
    assert should_fleet_recap(state="working", turn_s=999, since_last_s=30,
                              watchers=1, cfg=ON) is False
    assert should_fleet_recap(state="working", turn_s=999, since_last_s=121,
                              watchers=1, cfg=ON) is True


def test_on_ignores_the_watcher_count():
    cfg = FleetConfig(recap="on", recap_after_s=60, recap_interval_s=120)
    assert should_fleet_recap(state="working", turn_s=61, since_last_s=999,
                              watchers=0, cfg=cfg) is True


def test_off_never_fires():
    cfg = FleetConfig(recap="off")
    assert should_fleet_recap(state="working", turn_s=999, since_last_s=999,
                              watchers=9, cfg=cfg) is False
```

- [x] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_fleet_recap.py -v`
Expected: the seven new tests FAIL on `should_fleet_recap`, which does not exist. `FleetConfig` does, because Task 13 ran first.

- [x] **Step 3: Write the gate**

```python
def should_fleet_recap(*, state, turn_s, since_last_s, watchers, cfg) -> bool:
    """When a mid-turn recap is worth firing.

    Four conditions, all required. `watchers` is how many clients have this
    session's card or sidebar on screen: nobody looking, nobody pays, which
    is what makes a dashboard left open all day affordable at all.
    """
    if cfg.recap == "off":
        return False
    if state != "working":
        return False
    if cfg.recap == "watched" and watchers < 1:
        return False
    if turn_s < cfg.recap_after_s:
        return False
    return since_last_s >= cfg.recap_interval_s
```

- [x] **Step 4: Drive it from the session**

> **Two inputs Task 12 must not invent.** The config comes from
> `session.fleet_config` (Task 12a), never a hand-built `FleetConfig`. And
> billing: `recap_in_flight` takes `driver` and `agent` from its caller, so the
> caller must resolve them the way `recap_for` does — `btw.generation_agent`
> then `get_driver` — or the in-flight recap bills to the session's own agent
> (usually Opus) instead of `text_generation:`. Test that the agent handed to
> the driver is the `text_generation:` profile when one is set.

In `AgentSession`, add the watcher registry and a periodic task that is armed when `watchers` goes from 0 to 1 and cancelled when it returns to 0. It calls `should_fleet_recap` and, when true, `recap_in_flight` — **detached**, exactly like `_run_recap` at line 839, because a 4.7 s stall in a turn is not payable. Store the result on `self.fleet_recap` and notify the recap observers so the card and the sidebar refresh.

Cancel an in-flight fleet recap when the turn ends, for the reason `_cancel_recap` gives at line 832: a late answer describes a turn that has already closed.

- [x] **Step 5: Run the tests**

Run: `uv run pytest tests/test_fleet_recap.py -v`
Expected: PASS, all eleven.

- [x] **Step 6: Commit**

```bash
git commit -m "feat(recap): pay for a mid-turn recap only when someone is watching" -- src/aegis/recap/gate.py src/aegis/core/session.py tests/test_fleet_recap.py
```

### Task 13: the `fleet:` config block

**Files:**
- Modify: `src/aegis/config/__init__.py`
- Modify: `src/aegis/config/yaml_loader.py` (`AegisConfig` at line 56, `load_config` at line 269, a `_build_fleet` beside `_build_voice` at line 319)
- Test: `tests/test_fleet_config.py`

**Interfaces:**
- Produces: `FleetConfig(recap: str = "watched", recap_after_s: int = 60, recap_interval_s: int = 120)`; `AegisConfig.fleet: FleetConfig`.

- [x] **Step 1: Write the failing test**

```python
import pytest

from aegis.config import FleetConfig
from aegis.config.yaml_loader import ConfigError, load_config


def test_no_fleet_block_means_the_defaults(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    assert load_config(tmp_path).fleet == FleetConfig()


def test_a_fleet_block_overrides_what_it_names(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        "fleet:\n  recap: on\n  recap_interval_s: 300\n")
    cfg = load_config(tmp_path).fleet
    assert cfg.recap == "on"
    assert cfg.recap_interval_s == 300
    assert cfg.recap_after_s == 60, "an unnamed key keeps its default"


def test_an_unknown_recap_mode_fails_loud(tmp_path):
    """A typo that silently means 'off' is a dashboard with no lines and no
    explanation."""
    (tmp_path / ".aegis.yaml").write_text("fleet:\n  recap: sometimes\n")
    with pytest.raises(ConfigError, match="fleet.recap"):
        load_config(tmp_path)


def test_a_non_mapping_fleet_block_fails_loud(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("fleet: true\n")
    with pytest.raises(ConfigError, match="fleet"):
        load_config(tmp_path)
```

- [x] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_fleet_config.py -v`
Expected: FAIL — `FleetConfig` is not importable.

- [x] **Step 3: Write it**

In `src/aegis/config/__init__.py`, beside `VoiceConfig`:

```python
@dataclass(frozen=True)
class FleetConfig:
    """The F10 dashboard's paid call.

    `watched` — only sessions whose card or sidebar a client has on screen.
    `on` — every working session, watched or not. `off` — never.
    """

    recap: str = "watched"
    recap_after_s: int = 60
    recap_interval_s: int = 120
```

In `yaml_loader.py`, add `fleet: FleetConfig = field(default_factory=FleetConfig)` to `AegisConfig`, a `_build_fleet` shaped like `_build_voice` that raises `ConfigError("fleet.recap: must be one of watched|on|off")` on an unknown mode, and wire it into `load_config` beside `voice = _build_voice(...)`.

- [x] **Step 4: Run the tests**

Run: `uv run pytest tests/test_fleet_config.py -v`
Expected: PASS, all four.

- [x] **Step 5: Commit**

```bash
git commit -- src/aegis/config/__init__.py src/aegis/config/yaml_loader.py tests/test_fleet_config.py -m "feat(config): a fleet: block for the dashboard's recap gate"
```

### Task 14: the line in F3, the spend in the band, and who is watching

> **Rewritten after Task 12 landed.** The first draft said "register the pane
> as a watcher on mount and unregister on unmount". That pays for every
> mounted pane, focused or not, sidebar open or not — the opposite of the
> `watched` rule. And Task 12's reviewer flagged that a client which
> disconnects without removing its watcher turns `watched` into `on` for that
> session until it closes. This task wires *who is watching* precisely.

**What Task 12 delivers, on `main`:**
- `AgentSession.add_fleet_watcher(cb)` / `remove_fleet_watcher(cb)`; the
  periodic check runs only while at least one watcher is registered.
- An ok mid-turn recap is delivered **only** to those callbacks, as
  `cb(session, recap)` — never through `_emit_recap`, so the pane's
  transcript never draws it.
- `session.fleet_recap` (cleared when the session leaves working),
  `fleet_recap_cost_usd`, `fleet_recap_calls` (paid calls only),
  `fleet_recap_cancelled`, `fleet_recap_failed`.

**Files:**
- Modify: `src/aegis/tui/app.py` (one place decides what F3 is watching)
- Modify: `src/aegis/tui/pane.py`, `src/aegis/tui/sidebar.py` (the `now` line)
- Modify: `src/aegis/tui/fleet_screen.py` (F10 watches every session while open)
- Modify: `src/aegis/fleet/snapshot.py`, `src/aegis/fleet/models.py`, `src/aegis/fleet/render.py` (band spend)
- Test: `tests/test_fleet_watching.py`, `tests/test_sidebar_render.py`, `tests/test_fleet_render.py`

**The watching rule.**
- **F3 watches exactly one session: the active pane's, and only while sidebar
  mode is on.** One method on the app, called from `set_sidebar_mode` and from
  `_activate`, reconciles it: register the active pane's callback if the
  sidebar is open, unregister every other pane's. A tab switch moves the watch;
  closing F3 drops it.
- **F10 watches every live session while it is open.** `FleetScreen` already
  hooks each session for events (`_hook_events` / `on_unmount`); register a
  fleet watcher beside each event observer and remove it beside each removal.
- **Every registration has a matching removal on unmount**, for panes and for
  the screen. Then verify the part the reviewer flagged: **when a client
  detaches from the daemon, does its app actually unmount its panes and
  screens?** Find out how a view's app is torn down (`src/aegis/views/`,
  `src/aegis/daemon/`) and prove with a test that detaching a view leaves zero
  fleet watchers on the brain's sessions. If teardown does not unmount, remove
  the watchers from the view teardown path itself.

**The callbacks.** A watcher callback refreshes its surface: the pane calls
`_refresh_sidebar()`, the fleet screen calls its coalesced `poke()`. Neither
draws anything into a transcript.

**The `now` line in F3.** `SidebarModel.now_line: str = ""`, filled in
`pane._sidebar_model` from `core.fleet_recap.doing` when set, rendered in the
SESSION section when non-empty.

**The band.** `BandView` gains `recap_cancelled: int = 0`. `_band` sums
`fleet_recap_cost_usd`, `fleet_recap_calls` and `fleet_recap_cancelled` across
live sessions. The renderer shows `recap $X / N calls`, adding
`· C cancelled` only when C > 0 — a cancelled call billed an unknowable amount,
so the band says it happened rather than pretending the total is complete.

- [x] **Step 1: Failing tests.** In `tests/test_fleet_watching.py`, over a real
  in-process app (the pattern in `tests/test_fleet_screen.py`):
  1. sidebar closed → the active session has 0 fleet watchers;
  2. F3 opened → the active session has exactly 1; every other session 0;
  3. switch tabs with F3 open → the watch moves (old 0, new 1);
  4. F3 closed → 0 everywhere;
  5. F10 opened → every session has ≥ 1; F10 closed → back to what F3 alone
     implies;
  6. a watcher callback with a recap refreshes the sidebar's `now` line;
  7. **a view detached from a brain leaves 0 fleet watchers** on the brain's
     sessions (the daemon shape — `tests/brain.py::make_brain` and the view
     tests show how a view attaches).
  Plus render tests: `now_line` shows and is omitted when empty; the band shows
  `· 2 cancelled` only when non-zero.
- [x] **Step 2: Implement** to the rules above.
- [x] **Step 3: Mutation-check** the reconcile (register on every pane instead
  of the active one → test 2 or 3 red) and the unmount removal (skip it →
  test 7 red).
- [x] **Step 4: See it.** Re-run `.playground/fleet-render/shoot_f10.py` and a
  copy that opens F3 on a pane; a fake recap delivered to the watcher should
  show as `now …` in both. Stub the quota poll in any script (do not poll real
  accounts).
- [x] **Step 5: Document** the `fleet:` block in `docs/configuration.md`
  beside `recap:` / `loop_judge:`, with the measured per-call cost and the
  30 s interval floor.
- [x] **Step 6: Commit** — the watching rule; the F3 line and band spend; the
  docs.

---

# Slice 4 — `aegis dash`

### Task 15: boot straight into the dashboard

**Files:**
- Modify: `src/aegis/cli.py`
- Modify: `src/aegis/tui/app.py`
- Test: `tests/cli/test_dash_command.py`

**Interfaces:**
- Produces: `aegis dash` — attaches like `aegis` and pushes `FleetScreen` on mount.

- [x] **Step 1: Write the failing test**

```python
"""`aegis dash` is the same client with the screen already up. Tabs are
brain state and not per view (know-how/the-daemon.md), so a second client
already holds every session — there is nothing to fetch and nothing to
sync."""
from typer.testing import CliRunner

from aegis.cli import app

runner = CliRunner()


def test_dash_is_a_registered_command():
    out = runner.invoke(app, ["--help"]).output
    assert "dash" in out


def test_dash_attaches_with_the_fleet_screen_open(monkeypatch):
    seen = {}

    def fake_attach(*a, **kw):
        seen.update(kw)

    monkeypatch.setattr("aegis.cli._attach", fake_attach)
    runner.invoke(app, ["dash"])
    assert seen.get("open_fleet") is True
```

Use whatever the attach entry point is actually called in `cli.py` — read it first; `_attach` is a placeholder here and must be replaced with the real name.

- [x] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/cli/test_dash_command.py -v`
Expected: FAIL — no `dash` command.

- [x] **Step 3: Add the command**

Add `dash` to `cli.py` as a thin alias for the attach path, passing a flag through to the app. In `AegisApp.on_mount`, when the flag is set, call `action_open_fleet()`.

- [x] **Step 4: Run the tests**

Run: `uv run pytest tests/cli/test_dash_command.py -v`
Expected: PASS.

- [x] **Step 5: Exercise it in a second terminal** — done in the daemon shape instead, never against the live daemon: `tests/test_fleet_dash.py` opens two views over one brain, picks a card in the dash view and asserts the other view's tab did not move. The brief's local boot flag could not work (the app lives in the daemon), so the client asks in its hello.

With `aegis` already running in one terminal, open another and run `aegis dash`. Expected: the grid, with the same sessions. Click a card in the dash terminal and confirm the **other** terminal does not move — focus is per view, tabs are not.

- [x] **Step 6: Commit**

```bash
git commit -- src/aegis/cli.py src/aegis/tui/app.py tests/cli/test_dash_command.py -m "feat(cli): aegis dash boots straight into the fleet grid"
```

### Task 16: the user-visible surface

**Files:**
- Modify: `CHANGELOG.md`, `docs/`, `TASKS.md`
- Modify: `docs/superpowers/specs/2026-09-16-aegis-fleet-dashboard-design.md` (status header)

- [x] **Step 1: Document the config keys and the command**

Add `fleet:` (all three keys, with the measured cost that justifies the defaults) and `aegis dash` to the user-facing reference under `docs/`. A new config key that is only in a spec is a key nobody finds.

- [x] **Step 2: CHANGELOG**

One entry for the dashboard, one for the thinking cut, each with the measured numbers rather than an adjective.

- [x] **Step 3: Flip the statuses**

Set the spec's header to `implemented <date>` and tick this plan's tasks in the same commit batch. A stale status header sends the next `/workon` down a road that is already built.

- [ ] **Step 4: Full gate** — run by the controller.

Run `make check` as its own tool call. Read the exit code directly.

- [ ] **Step 5: Commit and push**

```bash
git commit -- CHANGELOG.md TASKS.md docs -m "docs: the fleet dashboard ships"
git push origin main
```

---

## Deferred, and why

- **A restored session reads as operator-born.** `_resume_agent_tabs`
  (`tui/app.py:856`) and `_resume_from_history` (`tui/app.py:1699`) call
  `_sync_spawn` with no `origin`, so a queue worker that comes back after a
  daemon restart shows as yours. Outside every task here, and the fix is to
  persist `origin` in the session snapshot and restore it. Narrow — a worker
  only comes back if it was mid-task when the daemon went down — but the
  dashboard is where it becomes visible, so it gets filed rather than
  forgotten.

- **The comms edges** (`spoke_with` / `waiting_on`) are empty until the ledger is held in memory. Reading the day's JSONL during assembly would break the no-disk rule that keeps the grid from stuttering. Its own task if the edges turn out to matter in use.
- **A relations strip.** Cut in the spec: the nine live sessions have one real edge between them, and a graph drawn from that is decoration.
- **The loop judge's thinking budget**, pending its own measurement. It decides whether a turn satisfied an instruction, which is the one call of the four where reasoning might earn its cost, and turning it off on the strength of a writing-task measurement would be reasoning past the evidence.

---

## Rulings made during execution

Decisions the controller took on the operator's behalf while the plan ran, in
order, each with what it costs if wrong. The SDD ledger they come from is
scratch and was deleted after the final review; this is the durable copy.

1. **Task 7 owns `CARD_WIDTH`/`GUTTER`; Task 8 does not redeclare them.** Cost: nil.
2. **Execution order 11, 13, 12, 14**, because Task 12's tests import Task 13's config. Cost: one blocked dispatch.
3. **Card cost through a guarded `_cost(s)`**, 0.0 for a session with no agent profile. Cost: a card reading $0.00 for a session that spent money.
4. **Missing fixtures defined locally**, built on `tests/brain.py::make_brain`, not added to `conftest.py`. Cost: duplication across test files.
5. **`.aegis.yaml` not edited at the start to add a cheaper profile** (it held another session's uncommitted edit), so every worker ran on opus. Cost: a more expensive run. Superseded by ruling 34.
6. **`make check` is not the gate:** `ty check src/` was already red with 327 errors before the plan. The gate became format + lint + lint-docs + test, with ty held at the baseline. Cost: a new type error hidden among old ones — which happened (5 new), and was fixed before shipping.
7. **Accepted widening `origin=` to `AppBridge.spawn` and its implementations**, not only `_sync_spawn`. Cost: an implementation left behind raising `TypeError`.
8. **Accepted replacing three source-string tests with behavioural ones.** Cost: a behavioural test that passes with the wiring deleted.
9. **`waiting` = ready with a live monitor or an owed queue callback**, and `error` got its own band counter. Cost: a session waiting on a handoff reads as ready. (The queue half first matched a remote-only field; fixed in the final wave.)
10. **The controller ran `ruff format` inline** on four files instead of a fix round. Cost: an unreviewed whitespace commit.
11. **`cost_today` renamed `cost_live` and the band host set to the local hostname, inline.** Cost: an unreviewed 4-line commit.
12. **The band's cost is labelled "live", not turned into a daily total**, because a daily figure needs a disk read. Cost: the operator wants the day's spend and gets the open sessions'.
13. **No `├───┤` separators on cards.** Cost: nine cards read worse; one helper to add back.
14. **Uptime always leads the footer; the cap shows turn time only while working.** Cost: an idle card's cap loses its duration.
15. **Six card minors folded into fix round 1** (equal row heights, trailing whitespace, ghost age, hidden `local`, context over 80% in red, `⏱`). Cost: a larger fix diff.
16. **The SYSTEM row in F10's band reuses the tiers pushed to the active pane**; cwd and locale dropped. Cost: the operator wanted cwd — one segment.
17. **Tasks 9/9b minors folded:** state the click invariant, key the selection on the handle, one pass over panes. Cost: a larger fix diff.
18. **The app holds the last system and quota sample**, delivered to the screen through an injected callable, so F10 shows meters over any tab. Cost: one host sample per tick while a terminal tab is in front.
19. **The build is always shown in the band.** Cost: nil.
20. **Screenshot and bench tooling stub the quota poll** after a run hit the operator's real Claude account ("cc rate limited"). Cost: none.
21. **Task 10 rewritten onto the `aegis bench` rig** instead of `aegis kill` on the live daemon. Cost: F10 over the operator's own fleet waits for his restart.
22. **Task 10b round 2 continued with the same live worker** via handoff. Cost: one more experiment.
23. **The in-flight recap lives in `aegis.recap` beside the other recaps**, not a new `aegis.fleet.recap`. Cost: a later move.
24. **Tasks 11 and 13 batched.** Cost: nil.
25. **vital-valiant's queue-rename fix landed on `main` in parallel**, with constraints on queue shapes and `callback_handle`. Cost: a transient red suite of unclear owner.
26. **After a worker died on a network error, Task 13 re-dispatched from its RED test** and Task 11 reviewed from its diff. Cost: nil.
27. **Task 13's number validation fixed inline.** Cost: an unreviewed ~15-line commit.
28. **Recap interval floor of 30 s**, since a call takes ~4.7 s. Cost: an operator wanting 10 s gets a clear `ConfigError`.
29. **Task 12a: sessions read `recap`, `loop_judge` and `fleet` from an explicit config root at call time**, mtime-cached (following `text_generation`'s precedent). It also fixed `recap: false` / `loop_judge: false` being silently ignored. Cost: a few `stat`s per read.
30. **Option A done inline by the controller, at the operator's request:** pinned haiku profile + `text_generation`, roster threaded into real sessions, billing from the config root, one-shots from an empty directory. Cost: the automatic recap and loop judge now spend in every session (~$0.007–0.015 per call).
31. **The fleet recap gets its own channel** (the watcher callbacks), not `_emit_recap`, which the pane draws into the transcript. Cost: nil.
32. **With `text_generation` unset, the unattended in-flight recap refuses** instead of billing the session's own model. Cost: no `now` line until it is set (now logged once).
33. **Task 12 minors folded:** per-turn interval reset, a snapshot of `_tracked`, counting paid calls only, the cheap state check first. Cost: nil.
34. **Task 14 rewritten before dispatch:** F3 watches the active tab with the sidebar open; F10 watches every session while open; every registration removed on unmount; a detach test. Cost: a leaked watcher — which the review then found on a hung shutdown and which was fixed (ruling 35).
35. **The hung-shutdown watcher leak fixed inline** with a sweep in `View.stop`. Cost: an unreviewed ~30-line commit, pinned by a failing-first test.
36. **`aegis dash` asks through the hello frame**, and the view opens the fleet idempotently in the app's own context. Cost: a new client against an old daemon silently gets a plain view (documented).
37. **The loop judge keeps thinking** (`think=True`), restoring the spec's exclusion that Task 1 had lost. Cost: judge calls take longer and cost more until measured.
38. **The ty gate is "no new errors over the baseline"**, and 5 new errors were fixed. Cost: nil.
39. **`fleet.recap: on` removed rather than implemented**; modes are `watched | off`. Cost: an operator wanting recaps with no screen open.
40. **One final fix wave with every finding**, and seven user-visible minors folded in. Cost: a large final diff, re-reviewed against real objects.

## Status after the final review

All tasks landed and passed task review; the final whole-branch review's
four Important findings and its must-fix test gap were fixed and re-reviewed
against real objects (a real `QueueManager`, a real session, a fake `claude`
on `PATH`). Final gate on `40469ee`'s parent `220b838`: 4074 passed, 1
skipped; ruff check and format clean; lint-docs clean; `ty` 327 (baseline).

**Not yet exercised:** F10, `aegis dash` and the now-live turn recap and loop
judge over the operator's own fleet. The running daemon predates all of it
and needs a restart (`aegis kill`, which drops the open tabs).
