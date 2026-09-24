# Ephemeral Agent Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A queue worker that ends a turn badly is rebuilt in place and told to continue, and when that runs out of attempts it is parked as an ordinary session holding its whole conversation, instead of being closed and lost.

**Architecture:** A shared plane at `core/recovery.py` owns the retry budget, the resume record and the rebuild. `QueueManager._finalize` gains a third arm that stalls instead of closing. Parking is expressed by moving the session's `Origin.kind` out of `EPHEMERAL_KINDS`, which frees the `max_parallel` slot and hands the session to the operator with no new session state. Rebuilding goes through `AgentSession.adopt`, which swaps the subprocess under a live session and keeps its handle, transcript and observers.

**Tech Stack:** Python 3.13+, `uv`, pytest (`pytest-asyncio`, `pytest-xdist`), Typer for CLI, Textual for the TUI, JSONL for queue state.

**Spec:** `docs/superpowers/specs/2026-09-24-ephemeral-agent-recovery-design.md` — read it before Task 1. The causal analysis it supersedes is `docs/superpowers/specs/2026-09-24-queue-worker-recovery-design.md`.

## Global Constraints

- **Python 3.13 or newer. Use `uv`, never `pip`.** (`AGENTS.md`)
- **Run the repo's own gate verbatim: `make test`.** Bare `pytest` skips `--max-unmarked-duration=3`, which `make test` passes, so a bare run can be green for hours while the gate is red. `make check` (format, lint, lint-docs, typecheck, test) before declaring a task done.
- **`make test` flakes 1–2 inotify tests under `-n auto`.** A single unrelated inotify failure is not your regression; re-run that file alone to confirm before chasing it.
- **This is a shared checkout. Stage named paths only: `git commit -- <path> <path>`. Never `git add -A`, never `git add .`, never `--amend`, never `git stash`.**
- **Commit to `main`.** No branch, no PR, unless asked.
- **Conventional commits, English.** One logical change per commit.
- **English for all code, comments, identifiers, log messages, test names and docs.**
- **A user-visible change needs a `CHANGELOG.md` entry; a new command, tool or config key needs a page under `docs/`.** (`AGENTS.md`) `rift check` enforces the docs half and must pass.
- **Never reuse a handle for a new session while a pane holds it.** Mounting a second `#pane-<handle>` is `DuplicateIds` and takes the whole app down. Rebuild through `adopt`; never `spawn(handle=...)` onto an occupied handle.
- **New task states must appear in the event-to-status map AND have a replay branch.** A status matching no branch drops the task from `_all` with no callback and blocks its producer forever.

## Review Focus

Five failure modes the spec implies that no obvious task test covers. Each has its test assigned to the task that owns the code.

1. **`attempts` resets across a rebuild** → infinite retry loop holding the `max_parallel` slot forever, which is the wedged queue this design exists to avoid. Test in Task 7.
2. **`rebuild` itself fails** (the ACP `loadSession` probe fails at runtime, which the driver comment says can happen) → must park, not raise into the queue, and must not leak the slot. Test in Task 8.
3. **A late `worker_session` record for an already-terminal task** → must not resurrect a completed task or re-open its slot. Test in Task 6.
4. **`_finalize` fires twice for one handle** (state observer and a close path racing) → must stall once, not rebuild twice. Test in Task 7.
5. **`aegis_task_resume` against a parked session the operator already closed** → must return an error dict, not raise. Test in Task 11.

---

### Task 1: Structural gate for stateful plane wiring

The persistence plane has never run in production because two brain paths construct it without a `state_dir`. Fix both, and add a gate that fails if anyone does it again.

**Files:**
- Modify: `src/aegis/core/planes.py` (add `STATEFUL_PLANES`)
- Modify: `src/aegis/cli.py:698,708` (`_serve`)
- Modify: `src/aegis/tui/app.py:650-653` (`_build_planes`)
- Test: `tests/core/test_stateful_planes_wired.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `aegis.core.planes.STATEFUL_PLANES: tuple[tuple[str, str], ...]` — pairs of `(constructor_name, brain_module_path)`. Nothing later depends on it; later tasks depend on the queue log actually being written.

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_stateful_planes_wired.py`:

```python
"""A plane that takes a state_dir must be handed one in every brain path.

QueueManager and InboxRouter both degrade to memory-only when state_dir
is None, and both brain paths constructed them that way for months. The
queue's whole persistence and replay layer never ran, and the eight tests
covering it passed because every one passed state_dir by hand.

This walks the AST of the brain-boot modules instead of trusting a
call site, because a call site is what went wrong.
"""
from __future__ import annotations

import ast
from pathlib import Path

import aegis
from aegis.core.planes import STATEFUL_PLANES

SRC = Path(aegis.__file__).parent


def _calls_to(module_path: str, ctor: str) -> list[ast.Call]:
    tree = ast.parse((SRC / module_path).read_text(encoding="utf-8"))
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (getattr(n.func, "id", None) == ctor
             or getattr(n.func, "attr", None) == ctor)
    ]


def test_every_stateful_plane_is_constructed_with_a_state_dir():
    offenders = []
    for ctor, module_path in STATEFUL_PLANES:
        calls = _calls_to(module_path, ctor)
        assert calls, f"no {ctor}(...) call found in {module_path}"
        for call in calls:
            if not any(kw.arg == "state_dir" for kw in call.keywords):
                offenders.append(f"{module_path}:{call.lineno} {ctor}(...)")
    assert not offenders, (
        "stateful planes constructed without state_dir:\n  "
        + "\n  ".join(offenders)
        + "\nA plane built without it silently persists nothing, and its "
          "replay never runs."
    )
```

- [ ] **Step 2: Add the inventory entry so the test can run**

Append to `src/aegis/core/planes.py`:

```python
#: Planes whose constructor takes a `state_dir` and which persist nothing
#: without one. Each pair is (constructor name, module path relative to the
#: aegis package) of a BRAIN path that must hand it a directory.
#:
#: QueueManager and InboxRouter were built without it in both brain paths
#: from the day persistence landed, so the queue never wrote a record and
#: its restart replay never ran. Nothing said so, because every test that
#: covered the replay constructed the manager itself.
STATEFUL_PLANES: tuple[tuple[str, str], ...] = (
    ("QueueManager", "cli.py"),
    ("InboxRouter", "cli.py"),
    ("QueueManager", "tui/app.py"),
    ("InboxRouter", "tui/app.py"),
)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/core/test_stateful_planes_wired.py -v`
Expected: FAIL, listing `cli.py:698 InboxRouter(...)`, `cli.py:708 QueueManager(...)`, `tui/app.py:650 InboxRouter(...)`, `tui/app.py:651 QueueManager(...)`.

- [ ] **Step 4: Wire `_serve`**

In `src/aegis/cli.py`, in `_serve`, replace:

```python
    inbox = InboxRouter()
```

with:

```python
    inbox = InboxRouter(state_dir=roots.state_dir)
```

and replace:

```python
    qm = QueueManager(queues or {}, mgr, inbox)
```

with:

```python
    qm = QueueManager(queues or {}, mgr, inbox, state_dir=roots.state_dir)
```

- [ ] **Step 5: Wire the standalone TUI**

In `src/aegis/tui/app.py`, in the plane-building constructor, replace:

```python
        self.inbox_router = InboxRouter()
        self.queue_manager = QueueManager(
            self._queues, _SessionManagerAdapter(self), self.inbox_router
        )
```

with:

```python
        self.inbox_router = InboxRouter(state_dir=self._state_dir)
        self.queue_manager = QueueManager(
            self._queues,
            _SessionManagerAdapter(self),
            self.inbox_router,
            state_dir=self._state_dir,
        )
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/core/test_stateful_planes_wired.py -v`
Expected: PASS.

- [ ] **Step 7: Mutation-test the gate**

Temporarily remove `state_dir=roots.state_dir` from the `QueueManager(...)` call in `cli.py`. Run the test again. Expected: FAIL naming `cli.py` and that line. Then put it back and confirm PASS.

A gate that cannot fail is worth less than no gate, and the one this replaces was green for months against a plane production never built. Do not skip this step.

- [ ] **Step 8: Run the full gate**

Run: `make test`
Expected: PASS (ignore up to two inotify flakes; re-run that file alone to confirm).

- [ ] **Step 9: Commit**

```bash
git commit -- src/aegis/core/planes.py src/aegis/cli.py src/aegis/tui/app.py tests/core/test_stateful_planes_wired.py -m "fix(queue): hand both brain paths a state_dir, and gate it

QueueManager and InboxRouter degrade to memory-only without one, so the
queue log, the restart replay and the durable inbox writethrough had
never run in either long-lived path. Only the one-shot workflow CLI
passed it. The gate walks the AST of the brain modules rather than
trusting a call site, because a call site is what went wrong."
```

---

### Task 2: Per-queue `max_attempts` and `recoverable_ttl_s`

**Files:**
- Modify: `src/aegis/config/yaml_loader.py:50-53` (`QueueSpec`)
- Modify: `src/aegis/config/__init__.py:214-221` (`load_queues`)
- Modify: `src/aegis/queue/schema.py` (`Queue` dataclass)
- Modify: `src/aegis/config/edit.py:317-338` (`add_queue`)
- Modify: `docs/` config page (whichever page documents `queues:`)
- Test: `tests/test_queue_config.py` (extend)

**Interfaces:**
- Consumes: nothing.
- Produces: `Queue.max_attempts: int` (default `2`) and `Queue.recoverable_ttl_s: int` (default `86400`), both read by Tasks 7, 8 and 9.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_queue_config.py`:

```python
def test_queue_carries_recovery_defaults(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        "  impl:\n"
        "    harness: claude-code\n"
        "    model: opus\n"
        "queues:\n"
        "  work:\n"
        "    agent: impl\n"
        "    max_parallel: 1\n",
        encoding="utf-8",
    )
    from aegis.config import load_queues
    q = load_queues(tmp_path)["work"]
    assert q.max_attempts == 2
    assert q.recoverable_ttl_s == 86400


def test_queue_recovery_keys_are_overridable(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        "  impl:\n"
        "    harness: claude-code\n"
        "    model: opus\n"
        "queues:\n"
        "  work:\n"
        "    agent: impl\n"
        "    max_parallel: 1\n"
        "    max_attempts: 1\n"
        "    recoverable_ttl_s: 0\n",
        encoding="utf-8",
    )
    from aegis.config import load_queues
    q = load_queues(tmp_path)["work"]
    assert q.max_attempts == 1
    assert q.recoverable_ttl_s == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_queue_config.py -k recovery -v`
Expected: FAIL with `AttributeError: 'Queue' object has no attribute 'max_attempts'`.

- [ ] **Step 3: Add the fields**

In `src/aegis/config/yaml_loader.py`, in `QueueSpec`:

```python
    agent: str
    max_parallel: int = 1
    budgets: list[dict[str, Any]] | None = None
    #: Turn ends a worker gets before its task is parked. 1 disables the
    #: automatic rebuild and parks on the first stall.
    max_attempts: int = 2
    #: Seconds a parked session is kept before it is closed and its task
    #: is failed. 0 keeps parked sessions forever.
    recoverable_ttl_s: int = 86400
```

In `src/aegis/queue/schema.py`, in `Queue`:

```python
    budgets: list[Budget] = field(default_factory=list)
    max_attempts: int = 2
    recoverable_ttl_s: int = 86400
```

In `src/aegis/config/__init__.py`, in `load_queues`, extend the `Queue(...)` construction:

```python
        out[name] = Queue(
            name=name,
            agent_profile=qspec.agent,
            max_parallel=qspec.max_parallel,
            provider=agent.harness,
            model=agent.model,
            budgets=budgets,
            max_attempts=qspec.max_attempts,
            recoverable_ttl_s=qspec.recoverable_ttl_s,
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_queue_config.py -v`
Expected: PASS.

- [ ] **Step 5: Document the keys**

Find the docs page that documents the `queues:` config section (`grep -rl "max_parallel" docs/`). Add both keys to its table or list, with the defaults and the one-line meaning from the spec. Then run `rift check`; the "every config section is documented" rule must not gain a new miss.

- [ ] **Step 6: Commit**

```bash
git commit -- src/aegis/config/yaml_loader.py src/aegis/config/__init__.py src/aegis/queue/schema.py docs tests/test_queue_config.py -m "feat(queue): per-queue max_attempts and recoverable_ttl_s

max_attempts bounds how long a stalled worker may hold its max_parallel
slot; recoverable_ttl_s bounds how long a parked session lives before it
is closed and its task failed."
```

---

### Task 3: `core/recovery.py` — `Resumable`, `Outcome`, `classify`

Pure data and one pure function. No I/O, no session, no queue.

**Files:**
- Create: `src/aegis/core/recovery.py`
- Test: `tests/core/test_recovery_classify.py` (create)

**Interfaces:**
- Consumes: `aegis.tui.state.AgentState`.
- Produces:
  - `Resumable(session_id: str, agent_profile: str, provider: str, cwd: str, host: str)` — frozen dataclass, used by Tasks 4, 6, 8.
  - `Outcome` — `StrEnum` with members `done`, `transient`, `terminal`.
  - `classify(state, *, attempts: int, max_attempts: int, cancelled: bool = False, over_budget: bool = False) -> Outcome`, used by Task 7.

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_recovery_classify.py`:

```python
"""The retry budget is the control, not a taxonomy of failure reasons.

We have four drivers and two protocols and no stable cross-harness
vocabulary for why a turn ended badly. A hand-written list of transient
reasons is wrong the day a provider changes a string, and wrong silently:
it goes terminal where it should have retried, which looks exactly like
the bug this plane exists to fix. So everything that is not a clean end
is transient until the budget runs out.
"""
from __future__ import annotations

import pytest

from aegis.core.recovery import Outcome, classify
from aegis.tui.state import AgentState


def test_ready_is_done():
    assert classify(AgentState.ready, attempts=0, max_attempts=2) is Outcome.done


def test_ready_is_done_even_on_the_last_attempt():
    """A clean end is a clean end; the budget never turns an answer into a
    failure."""
    assert classify(AgentState.ready, attempts=9, max_attempts=2) is Outcome.done


def test_error_with_budget_left_is_transient():
    assert classify(AgentState.error, attempts=0, max_attempts=2) is Outcome.transient


def test_error_with_budget_exhausted_is_terminal():
    assert classify(AgentState.error, attempts=2, max_attempts=2) is Outcome.terminal


def test_max_attempts_of_one_is_terminal_on_the_first_bad_end():
    """The park-immediately model, for anyone who wants it."""
    assert classify(AgentState.error, attempts=1, max_attempts=1) is Outcome.terminal


def test_cancelled_is_terminal_with_budget_left():
    """Cancelling is a decision, not a failure to retry around."""
    assert classify(
        AgentState.error, attempts=0, max_attempts=5, cancelled=True
    ) is Outcome.terminal


def test_over_budget_is_terminal_with_attempts_left():
    assert classify(
        AgentState.error, attempts=0, max_attempts=5, over_budget=True
    ) is Outcome.terminal


@pytest.mark.parametrize("state", [AgentState.working, AgentState.error])
def test_every_non_ready_state_is_transient_while_budget_remains(state):
    """The default arm. Nothing enumerates stop_reason."""
    assert classify(state, attempts=0, max_attempts=2) is Outcome.transient
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/core/test_recovery_classify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.core.recovery'`.

- [ ] **Step 3: Write the implementation**

Create `src/aegis/core/recovery.py`:

```python
"""Recovering an ephemeral agent whose turn ended badly.

An ephemeral agent (`fleet.models.EPHEMERAL_KINDS`: queue, workflow,
group) used to be closed the moment a turn ended in anything but
`ready`, and closing is irreversible — the session leaves the roster,
its MCP token is revoked, its pane is dropped, and its conversation stops
being reachable by any path aegis offers. A dropped SSH link to an
execution host arrives here as `Result(is_error=True)`, so a tunnel blip
destroyed an hour of context.

This module holds the three things every caller needs and none of the
state: the retry budget, the record that makes a conversation
rebuildable, and the rebuild itself. `QueueManager` is the first caller;
`WorkflowEngine` and the group runtime are meant to be the next, which is
why nothing here knows what a task is.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aegis.tui.state import AgentState


@dataclass(frozen=True)
class Resumable:
    """Everything needed to rebuild a worker's conversation.

    `session_id` is the harness's own conversation id, latched by the
    driver on its first SystemInit. Without it there is nothing to
    resume, which is why it is recorded the moment it appears rather
    than at the end of a turn: a worker whose harness dies before any
    turn boundary is exactly the case the record exists for.
    """

    session_id: str
    agent_profile: str
    provider: str
    cwd: str
    host: str


class Outcome(StrEnum):
    done = "done"
    transient = "transient"
    terminal = "terminal"


def classify(
    state: AgentState,
    *,
    attempts: int,
    max_attempts: int,
    cancelled: bool = False,
    over_budget: bool = False,
) -> Outcome:
    """What a turn ending means for the task behind it.

    `transient` is the DEFAULT arm, deliberately. The tempting version
    enumerates the recoverable reasons — link_lost, a harness exception,
    a stream with no Result, a rate limit — and calls the rest terminal.
    That list needs a cross-harness vocabulary for failure that does not
    exist, and being wrong about it fails closed: it goes terminal where
    it should have retried, which is indistinguishable from the bug this
    plane exists to fix. The budget does the limiting instead, and the
    diagnostic fields go in the log to be read rather than branched on.
    """
    if state is AgentState.ready:
        return Outcome.done
    if cancelled or over_budget:
        return Outcome.terminal
    if attempts >= max_attempts:
        return Outcome.terminal
    return Outcome.transient
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/core/test_recovery_classify.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git commit -- src/aegis/core/recovery.py tests/core/test_recovery_classify.py -m "feat(core): recovery plane — Resumable, Outcome, classify

Shared by queue, workflow and group so one retry budget and one rebuild
serve all three. classify defaults to transient and lets max_attempts do
the limiting, rather than maintaining a cross-harness taxonomy of failure
reasons that fails closed when it is wrong."
```

---

### Task 4: `AgentSession.last_stop_reason` and `resumable_from`

`Result.stop_reason` exists (`events.py:114`) and is dropped on the floor. It is diagnostic, not a branch, but the log needs it.

**Files:**
- Modify: `src/aegis/core/session.py:328` (field), `:860-880` and `:1670-1690` (latch, both turn loops)
- Modify: `src/aegis/core/recovery.py` (add `resumable_from`)
- Test: `tests/core/test_recovery_resumable.py` (create)

**Interfaces:**
- Consumes: `Resumable` from Task 3.
- Produces:
  - `AgentSession.last_stop_reason: str | None` — `None` until a `Result` carries one.
  - `recovery.resumable_from(session) -> Resumable | None`, used by Tasks 6 and 8.

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_recovery_resumable.py`:

```python
"""Without a session id there is nothing to resume, so the record that
carries it is the whole of what makes recovery possible."""
from __future__ import annotations

from types import SimpleNamespace

from aegis.core.recovery import Resumable, resumable_from


def _session(session_id, *, slug="impl", harness="claude-code",
             cwd="/srv/app", host="local"):
    return SimpleNamespace(
        session_id=session_id,
        agent_slug=slug,
        agent=SimpleNamespace(harness=harness),
        place=SimpleNamespace(cwd=cwd, host=host),
    )


def test_returns_none_before_the_harness_reports_an_id():
    assert resumable_from(_session(None)) is None


def test_returns_none_for_an_empty_id():
    """An empty string is not an id; resuming on it would build a new
    conversation and call it a recovery."""
    assert resumable_from(_session("")) is None


def test_captures_everything_the_rebuild_needs():
    r = resumable_from(_session("sess-abc"))
    assert r == Resumable(
        session_id="sess-abc",
        agent_profile="impl",
        provider="claude-code",
        cwd="/srv/app",
        host="local",
    )


def test_missing_place_falls_back_to_local():
    """A session built before placement existed, and every test double."""
    s = _session("sess-abc")
    s.place = None
    r = resumable_from(s)
    assert r is not None and r.host == "local" and r.cwd == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/core/test_recovery_resumable.py -v`
Expected: FAIL with `ImportError: cannot import name 'resumable_from'`.

- [ ] **Step 3: Add `resumable_from`**

Append to `src/aegis/core/recovery.py`:

```python
def resumable_from(session) -> Resumable | None:
    """The rebuild record for a live session, or None when the harness has
    not reported a conversation id yet.

    Read defensively: this runs against real AgentSessions and against
    every test double that stands in for one, and a probe that raises
    here would strand the task it was trying to save.
    """
    sid = getattr(session, "session_id", None)
    if not sid:
        return None
    place = getattr(session, "place", None)
    agent = getattr(session, "agent", None)
    return Resumable(
        session_id=sid,
        agent_profile=getattr(session, "agent_slug", "") or "",
        provider=getattr(agent, "harness", "") or "",
        cwd=getattr(place, "cwd", "") or "",
        host=getattr(place, "host", "") or "local",
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/core/test_recovery_resumable.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Write the failing test for `last_stop_reason`**

First find how this repo already builds an `AgentSession` in a test: `grep -rn "AgentSession(" tests/ | head`. Use that helper rather than a new double. Then append to `tests/core/test_recovery_resumable.py`, substituting that helper for `make_session()`:

```python
async def test_last_stop_reason_is_none_on_a_fresh_session():
    """The queue reads this in the stall log. An AttributeError there
    loses the diagnostic for the very failure it is recording."""
    s = make_session()
    assert s.last_stop_reason is None


async def test_last_stop_reason_latches_from_the_result():
    from aegis.events import Result

    s = make_session()
    await s.run_turn_emitting([
        Result(duration_ms=None, is_error=True, stop_reason="link_lost"),
    ])
    assert s.last_stop_reason == "link_lost"


async def test_last_stop_reason_latches_in_both_turn_loops():
    """There are two _run_turn implementations in session.py (grep for
    `saw_result = True`; one is the ACP path). A recovery that records the
    reason on one path and not the other is worse than neither, because
    the log looks complete."""
    import ast
    import inspect

    import aegis.core.session as mod

    src = inspect.getsource(mod)
    assert src.count("self.last_stop_reason = ev.stop_reason") == 2, (
        "latch it in BOTH turn loops"
    )
```

`run_turn_emitting` stands for whatever the repo's existing helper is for driving a fake event stream through a session — reuse it, do not write a new one.

- [ ] **Step 6: Latch the field**

In `src/aegis/core/session.py`, beside `self.last_error` (around line 328):

```python
        # Captured by _run_turn's except clause for postmortem inspection.
        # None until a harness error occurs; replaced on each new error.
        self.last_error: Exception | None = None
        #: stop_reason of the most recent Result, or None. Diagnostic only:
        #: the recovery plane records it and does not branch on it, because
        #: the four drivers share no vocabulary for why a turn ended.
        self.last_stop_reason: str | None = None
```

In BOTH turn loops (around `:860-880` and `:1670-1690`), inside the `if isinstance(ev, Result):` branch and before `_emit_state`:

```python
                    self.last_stop_reason = ev.stop_reason
```

Both loops, not one. Grep for `saw_result = True` to find them; there are two, and a recovery that works in one code path and not the other is worse than neither.

- [ ] **Step 7: Run to verify it passes**

Run: `uv run pytest tests/core/test_recovery_resumable.py -v`
Expected: PASS.

- [ ] **Step 8: Run the full gate**

Run: `make test`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git commit -- src/aegis/core/session.py src/aegis/core/recovery.py tests/core/test_recovery_resumable.py -m "feat(core): latch last_stop_reason, add resumable_from

Result.stop_reason existed and was dropped. The recovery plane records it
for reading rather than branching on it. resumable_from turns a live
session into the record a rebuild needs, or None when the harness has not
reported a conversation id yet."
```

---

### Task 5: `SessionManager.reconnect(allow_local=True)`

The rebuild mechanism already exists for dropped remote links. Recovery needs it locally too.

**Files:**
- Modify: `src/aegis/core/manager.py:437-474` (`reconnect`)
- Test: `tests/core/test_reconnect_allow_local.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `SessionManager.reconnect(handle: str, *, allow_local: bool = False) -> str`, used by Task 6.

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_reconnect_allow_local.py`:

```python
"""reconnect refuses local sessions because the manual /reconnect command
is documented as a remote-link repair. The MECHANISM is provider-level
resume_from, which is how every boot resume works, locally included. The
recovery plane needs it for local workers, so the guard becomes a
parameter rather than a property of the code.
"""
from __future__ import annotations

import pytest


async def test_local_session_is_refused_by_default(brain_with_local_session):
    mgr, handle = brain_with_local_session
    with pytest.raises(ValueError, match="reconnect is for remote sessions"):
        await mgr.reconnect(handle)


async def test_local_session_is_allowed_when_asked(brain_with_local_session):
    mgr, handle = brain_with_local_session
    result = await mgr.reconnect(handle, allow_local=True)
    assert handle in result


async def test_a_session_with_no_id_is_refused_even_with_allow_local(
    brain_with_local_session_no_id,
):
    """allow_local lifts one guard, not both. Resuming on no id builds a
    fresh conversation and calls it a recovery."""
    mgr, handle = brain_with_local_session_no_id
    with pytest.raises(ValueError, match="no session id"):
        await mgr.reconnect(handle, allow_local=True)
```

Build the two fixtures from the existing brain test double. `tests/brain.py` is the shared helper; read it and follow its pattern rather than inventing a new double. The fixtures need a `SessionManager` holding one local session with `session_id` set (and one with it unset) and a `_make_session` factory that returns a fresh stub.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/core/test_reconnect_allow_local.py -v`
Expected: FAIL — `test_local_session_is_allowed_when_asked` raises `ValueError` because `allow_local` is not a parameter.

- [ ] **Step 3: Add the parameter**

In `src/aegis/core/manager.py`, change the signature and the guard:

```python
    async def reconnect(self, handle: str, *, allow_local: bool = False) -> str:
        """Rebuild a session's harness in place, resuming its conversation.

        The harness keeps its own conversation store, so a dropped link
        costs only the in-flight turn: this re-runs the harness on the
        same host and resumes the same conversation id, in the same tab,
        under the same handle.

        `allow_local` is for the recovery plane. The local refusal below
        belongs to the manual /reconnect command, which is documented as a
        remote-link repair; the mechanism underneath is provider-level
        resume_from, which is how every boot resume works, locally
        included. Rebuilding is also the ONLY safe way to put a worker
        back: spawning onto its handle races an async pane drop and mounts
        a second `#pane-<handle>`, which is DuplicateIds and the app.

        Raises ValueError listing every refusal reason at once.
        """
        import contextlib

        s = self.get(handle)
        if s is None:
            raise ValueError(f"unknown session {handle!r}")
        reasons: list[str] = []
        if s.place.is_local and not allow_local:
            reasons.append(f"{handle} runs local — reconnect is for remote sessions")
```

Leave the rest of the method unchanged.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/core/test_reconnect_allow_local.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Confirm the manual command is unchanged**

Run: `uv run pytest tests/ -k reconnect -v`
Expected: PASS — every pre-existing reconnect test still passes, because the default is `False`.

- [ ] **Step 6: Commit**

```bash
git commit -- src/aegis/core/manager.py tests/core/test_reconnect_allow_local.py -m "feat(core): reconnect(allow_local=True) for the recovery plane

The local refusal belongs to the manual /reconnect command, not to the
mechanism: resume_from is how every boot resume works. Default unchanged,
so /reconnect still refuses a local session."
```

---

### Task 6: Record the `Resumable`, and `recovery.rebuild`

**Files:**
- Modify: `src/aegis/core/recovery.py` (add `rebuild`)
- Modify: `src/aegis/queue/schema.py` (`Task.attempts`, `Task.resumable`)
- Modify: `src/aegis/queue/manager.py:570-618` (`_attach_observers.on_event`)
- Test: `tests/test_queue_worker_session_record.py` (create)
- Test: `tests/core/test_recovery_rebuild.py` (create)

**Interfaces:**
- Consumes: `Resumable`, `resumable_from` (Tasks 3, 4); `reconnect(allow_local=...)` (Task 5).
- Produces:
  - `Task.attempts: int = 0` and `Task.resumable: Resumable | None = None`.
  - A `worker_session` JSONL record: `{"event": "worker_session", "task_id", "worker_handle", "session_id", "agent_profile", "provider", "cwd", "host"}`.
  - `recovery.rebuild(sm, handle, *, nudge) -> bool`, used by Task 7.
  - The three nudge strings, in `core/recovery.py`, used by Tasks 7, 9, 11.
  - `queue_rig` and the extended `StubSM`, used by Tasks 7, 8, 9, 10, 11.

- [ ] **Step 0: Build the shared test rig first**

Tasks 6 through 11 all drive a `QueueManager` through a stubbed session
manager. Build it once. `tests/test_queue_e2e.py` already has `StubSM` — read
it, then extend it in place rather than writing a second double.

Add to `tests/conftest.py`:

```python
import pytest

from aegis.queue import InboxRouter, QueueManager
from aegis.queue.schema import Queue


def _make_rig(tmp_path, *, max_attempts=2, recoverable_ttl_s=86400,
              rebuild_fails=False):
    from tests.test_queue_e2e import StubSM

    sm = StubSM()
    sm.rebuild_fails = rebuild_fails
    inbox = InboxRouter(state_dir=tmp_path)
    qm = QueueManager(
        {"impl": Queue(name="impl", agent_profile="claude-impl",
                       max_parallel=1, max_attempts=max_attempts,
                       recoverable_ttl_s=recoverable_ttl_s)},
        sm, inbox, state_dir=tmp_path,
    )
    return qm, sm


@pytest.fixture
def queue_rig(tmp_path):
    return _make_rig(tmp_path)


@pytest.fixture
def queue_rig_max_attempts_1(tmp_path):
    return _make_rig(tmp_path, max_attempts=1)


@pytest.fixture
def queue_rig_rebuild_always_fails(tmp_path):
    return _make_rig(tmp_path, max_attempts=1, rebuild_fails=True)
```

Extend `StubSM` in `tests/test_queue_e2e.py` with the five helpers every test
below uses. Follow its existing style; these are sketches of behaviour, not
code to paste blind:

- `emit_system_init(handle, session_id)` — set the stub session's
  `session_id`, then fire a real `SystemInit` event through the session's
  event observers, so the queue's own `on_event` runs.
- `fail(handle, text, stop_reason=None, emit_twice=False)` — push `text`
  through the observers as `AssistantText`, set `last_stop_reason`, then fire
  the state observers with `AgentState.error, finished=True`. With
  `emit_twice=True`, fire the state callback a second time.
- `finish(handle, text)` — the same with `AgentState.ready`.
- `inbox_for(handle)` — the `InboxMessage`s delivered to that handle.
- `reconnect(handle, *, allow_local=False)` — raise `ValueError` when
  `self.rebuild_fails`, otherwise record the call and return a string. This is
  what `recovery.rebuild` calls, so it is the seam that makes
  `queue_rig_rebuild_always_fails` work.

The remaining fixtures (`parked_rig`, `parked_rig_ttl_zero`,
`parked_rig_completed`, `replay_rig*`) build on these; define each in the test
file that first uses it, driving `_make_rig` to the state its name describes.

- [ ] **Step 1: Write the failing test for the record**

Create `tests/test_queue_worker_session_record.py`:

```python
"""The session id is latched at first sight, not at the end of a turn.

A worker whose harness dies before any turn boundary never reaches
_finalize, and that is precisely the case the record exists for. So the
queue's event observer writes it the moment SystemInit carries one.
"""
from __future__ import annotations

from aegis.queue.jsonl import read_records


async def test_worker_session_is_recorded_when_the_id_first_appears(
    queue_rig, tmp_path,
):
    qm, sm = queue_rig
    tid = qm.enqueue("impl", "go", enqueued_by="agent:producer", callback=True)["task_id"]
    sm.emit_system_init(qm.status(tid)["worker_handle"], session_id="sess-1")

    recs = read_records(tmp_path / "queues" / "impl.jsonl")
    ws = [r for r in recs if r["event"] == "worker_session"]
    assert len(ws) == 1
    assert ws[0]["task_id"] == tid
    assert ws[0]["session_id"] == "sess-1"


async def test_worker_session_is_recorded_once(queue_rig, tmp_path):
    """A second SystemInit (a rebuild reports one too) must not append a
    duplicate that replay would have to de-duplicate."""
    qm, sm = queue_rig
    tid = qm.enqueue("impl", "go", enqueued_by="agent:producer", callback=True)["task_id"]
    h = qm.status(tid)["worker_handle"]
    sm.emit_system_init(h, session_id="sess-1")
    sm.emit_system_init(h, session_id="sess-1")

    recs = read_records(tmp_path / "queues" / "impl.jsonl")
    assert len([r for r in recs if r["event"] == "worker_session"]) == 1


async def test_worker_session_is_not_a_lifecycle_event():
    """Membership in _LIFECYCLE_EVENTS sets the task's status to the event
    name on replay. A status of 'worker_session' matches no branch, which
    drops the task from _all with no callback and blocks its producer
    forever — the exact failure the comment in start() was written about."""
    from aegis.queue.manager import _LIFECYCLE_EVENTS
    assert "worker_session" not in _LIFECYCLE_EVENTS


async def test_a_late_record_does_not_resurrect_a_terminal_task(
    queue_rig, tmp_path,
):
    """Review Focus 3. The observer fires off a session the manager has
    already finalized and popped; it must find nothing and do nothing."""
    qm, sm = queue_rig
    tid = qm.enqueue("impl", "go", enqueued_by="agent:producer", callback=True)["task_id"]
    h = qm.status(tid)["worker_handle"]
    await sm.finish(h, text="DONE")
    assert qm.status(tid)["status"] == "completed"

    sm.emit_system_init(h, session_id="sess-late")

    assert qm.status(tid)["status"] == "completed"
    recs = read_records(tmp_path / "queues" / "impl.jsonl")
    assert not [r for r in recs if r["event"] == "worker_session"
                and r.get("session_id") == "sess-late"]
```

`queue_rig` is a fixture you add to `tests/conftest.py` (or a local one): a `QueueManager` with `state_dir=tmp_path`, one queue `impl` with `max_parallel=1`, and the `StubSM` double already used by `tests/test_queue_e2e.py`. Extend that stub with `emit_system_init(handle, session_id)` and `finish(handle, text)` helpers rather than writing a second double — read `tests/test_queue_e2e.py` first and reuse `StubSM`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_queue_worker_session_record.py -v`
Expected: FAIL — no `worker_session` records are written.

- [ ] **Step 3: Add the Task fields**

In `src/aegis/queue/schema.py`, in `Task`:

```python
    callback_to: str | None = None
    callback_handle: str | None = None
    #: Turn ends this task's worker has had that were not clean. Carried
    #: across a rebuild, deliberately: resetting it on respawn is an
    #: unbounded retry loop holding the queue's max_parallel slot.
    attempts: int = 0
    #: What a rebuild needs, once the harness has reported a session id.
    resumable: "Resumable | None" = None
```

Add `from aegis.core.recovery import Resumable` at the top of `schema.py`.

- [ ] **Step 4: Latch and record in the observer**

In `src/aegis/queue/manager.py`, inside `_attach_observers`'s `on_event`, before the `isinstance(ev, AssistantText)` branch:

```python
        def on_event(_s, ev):
            h = session.handle
            from aegis.events import SystemInit

            if isinstance(ev, SystemInit):
                # First sight, not turn end. A worker whose harness dies
                # before any turn boundary never reaches _finalize, and
                # that is the case this record exists for.
                if h not in self._workers:
                    return
                t, said = self._workers[h]
                if t.resumable is not None:
                    return  # a rebuild reports one too; record it once
                from aegis.core.recovery import resumable_from

                r = resumable_from(session)
                if r is None:
                    return
                t = replace(t, resumable=r)
                self._workers[h] = (t, said)
                self._all[t.id] = t
                self._inflight[t.queue] = [
                    (t if x.id == t.id else x) for x in self._inflight[t.queue]
                ]
                self._log(
                    t.queue,
                    {
                        "event": "worker_session",
                        "task_id": t.id,
                        "worker_handle": h,
                        "session_id": r.session_id,
                        "agent_profile": r.agent_profile,
                        "provider": r.provider,
                        "cwd": r.cwd,
                        "host": r.host,
                        "at": self._now(),
                    },
                )
                return
```

The `if h not in self._workers: return` guard is what makes Review Focus 3 pass: a finalized task has been popped from `_workers`, so a late record finds nothing.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_queue_worker_session_record.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 6: Write the failing test for `rebuild`**

Create `tests/core/test_recovery_rebuild.py`:

```python
"""Rebuild swaps the subprocess under a live session. It never spawns.

Spawning onto a closed worker's handle races the async pane drop
(app.py:1429 runs it through run_worker), so `#pane-<handle>` may still be
mounted and a second mount is DuplicateIds, which takes the whole app
down. AgentSession.adopt is the way through, and reconnect already uses
it for a dropped remote link.
"""
from __future__ import annotations

from aegis.core.recovery import rebuild

NUDGE = "Your aegis session was interrupted mid-task. Continue."


async def test_rebuild_reconnects_in_place_and_nudges(brain_with_local_session):
    mgr, handle = brain_with_local_session
    before = len(mgr.list_sessions())

    assert await rebuild(mgr, handle, nudge=NUDGE) is True

    assert len(mgr.list_sessions()) == before, "rebuild must not add a session"
    assert mgr.get(handle) is not None, "the handle must still be live"
    assert NUDGE in mgr.get(handle).delivered_bodies[-1]


async def test_rebuild_passes_allow_local(brain_with_local_session):
    """Without it, every local worker is unrecoverable — which is most of
    them."""
    mgr, handle = brain_with_local_session
    assert await rebuild(mgr, handle, nudge=NUDGE) is True


async def test_rebuild_returns_false_for_an_unknown_handle():
    from tests.brain import make_brain
    mgr = make_brain()
    assert await rebuild(mgr, "ghost-handle", nudge=NUDGE) is False


async def test_rebuild_returns_false_when_reconnect_raises(
    brain_with_local_session_no_id,
):
    """Review Focus 2, half one: the ACP loadSession probe can fail at
    runtime, and a raise here would take out the queue's finalizer."""
    mgr, handle = brain_with_local_session_no_id
    assert await rebuild(mgr, handle, nudge=NUDGE) is False


async def test_rebuild_does_not_nudge_when_the_rebuild_failed(
    brain_with_local_session_no_id,
):
    """A nudge delivered to a dead harness is a message nobody reads and a
    turn the task is charged for."""
    mgr, handle = brain_with_local_session_no_id
    await rebuild(mgr, handle, nudge=NUDGE)
    assert not mgr.get(handle).delivered_bodies
```

- [ ] **Step 7: Run to verify it fails**

Run: `uv run pytest tests/core/test_recovery_rebuild.py -v`
Expected: FAIL with `ImportError: cannot import name 'rebuild'`.

- [ ] **Step 8: Implement `rebuild`**

Append to `src/aegis/core/recovery.py`:

```python
async def rebuild(sm, handle: str, *, nudge: str) -> bool:
    """Replace the dead harness under `handle`, then tell it what happened.

    Returns False rather than raising when the rebuild is impossible: a
    driver whose loadSession probe fails at runtime, a session with no
    conversation id, a handle that is already gone. The caller is a
    finalizer, and a raise there strands the task it was trying to save.

    `AgentSession.adopt`, which `reconnect` calls, keeps the handle,
    log_id, inbox binding, metrics, observers and transcript. Observers
    surviving is what matters here: the queue's on_event and on_state stay
    attached across the rebuild, so there is no window in which a worker
    runs unobserved and nothing to re-wire.
    """
    from aegis.queue.schema import InboxMessage, now_iso, sender_substrate

    try:
        await sm.reconnect(handle, allow_local=True)
    except Exception:  # noqa: BLE001 — a failed rebuild parks, never raises
        return False
    s = sm.get(handle)
    if s is None:
        return False
    try:
        await s.deliver(
            InboxMessage(
                sender=sender_substrate(),
                timestamp=now_iso(),
                body=nudge,
            )
        )
    except Exception:  # noqa: BLE001
        return False
    return True
```

- [ ] **Step 8b: Add the three nudge strings**

Also in `src/aegis/core/recovery.py`, so every caller says the same thing:

```python
#: What a rebuilt worker is told. It has no memory of the interruption —
#: from inside, the turn simply never ended — so the message has to say
#: that its conversation survived, or it re-derives what it already knows.
NUDGE_STALL = (
    "Your aegis session was interrupted mid-task ({reason}). Your "
    "conversation is intact and you are still working the same task. "
    "Continue from where you were; do not start over."
)
NUDGE_RESTART = (
    "aegis restarted while you were mid-task. Your conversation is "
    "intact and the task is still yours. Continue from where you were; "
    "do not start over. Re-arm any monitor you were waiting on, because "
    "the process that held it is gone."
)
NUDGE_OPERATOR = (
    "The operator has put you back to work on the task you were parked "
    "on. Your conversation is intact. Continue from where you were; if "
    "you are not sure what state you left things in, check the working "
    "tree before you act."
)
```

`NUDGE_RESTART` names the monitor problem deliberately: a monitor lives in the
process that died, so a worker resumed after a restart is waiting on a wake
that will never come.

- [ ] **Step 9: Run to verify it passes**

Run: `uv run pytest tests/core/test_recovery_rebuild.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 10: Run the full gate and commit**

Run: `make test`

```bash
git commit -- src/aegis/core/recovery.py src/aegis/queue/schema.py src/aegis/queue/manager.py tests/test_queue_worker_session_record.py tests/core/test_recovery_rebuild.py -m "feat(queue): record the resume record, add recovery.rebuild

The session id is latched at first SystemInit and written to the queue log
straight away, because a worker whose harness dies before a turn boundary
never reaches _finalize. rebuild goes through reconnect/adopt so the
handle, transcript and observers survive; spawning onto the handle would
race the async pane drop and crash on DuplicateIds."
```

---

### Task 7: The stall arm in `_finalize`

**Files:**
- Modify: `src/aegis/queue/manager.py:665-765` (`_finalize`)
- Test: `tests/test_queue_stall.py` (create)

**Interfaces:**
- Consumes: `classify`, `Outcome` (Task 3); `rebuild` (Task 6); `Queue.max_attempts` (Task 2).
- Produces: a `stalled` JSONL record; `Task.attempts` incremented in place; task stays `dispatched`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_queue_stall.py`:

```python
"""A transient turn end stalls and rebuilds; it does not close.

Closing is irreversible — the session leaves the roster, its token is
revoked, its pane is dropped. AgentState.error covers a dropped SSH link
(claude.py:233 reports link_lost as Result(is_error=True)), a harness
exception, and a stream that ended with no Result, so the old code
destroyed an hour of context on a tunnel blip.
"""
from __future__ import annotations

from aegis.queue.jsonl import read_records


async def test_a_bad_turn_end_stalls_instead_of_closing(queue_rig, tmp_path):
    qm, sm = queue_rig
    tid = qm.enqueue("impl", "go", enqueued_by="agent:producer", callback=True)["task_id"]
    h = qm.status(tid)["worker_handle"]
    sm.emit_system_init(h, session_id="sess-1")

    await sm.fail(h, text="halfway through")

    assert h not in sm.closed, "the worker must not be closed"
    assert qm.status(tid)["status"] == "dispatched"
    recs = read_records(tmp_path / "queues" / "impl.jsonl")
    assert [r for r in recs if r["event"] == "stalled"]


async def test_a_stall_says_nothing_to_the_producer(queue_rig):
    """A blip that recovers in four seconds is not news, and waking a
    producer agent for it costs it a turn."""
    qm, sm = queue_rig
    tid = qm.enqueue("impl", "go", enqueued_by="agent:producer", callback=True)["task_id"]
    h = qm.status(tid)["worker_handle"]
    sm.emit_system_init(h, session_id="sess-1")

    await sm.fail(h, text="halfway")

    assert not sm.inbox_for("producer")


async def test_a_stall_holds_the_max_parallel_slot(queue_rig):
    """Held only for the rebuild window. Releasing it here would run two
    workers for one queue with max_parallel=1."""
    qm, sm = queue_rig
    first = qm.enqueue("impl", "one", enqueued_by="agent:producer", callback=True)["task_id"]
    second = qm.enqueue("impl", "two", enqueued_by="agent:producer", callback=True)["task_id"]
    h = qm.status(first)["worker_handle"]
    sm.emit_system_init(h, session_id="sess-1")

    await sm.fail(h, text="halfway")

    assert qm.status(second)["status"] == "pending"


async def test_the_stall_log_carries_the_diagnostics(queue_rig, tmp_path):
    """Recorded to be read, not branched on. This is the evidence for ever
    building a real taxonomy of failure reasons."""
    qm, sm = queue_rig
    tid = qm.enqueue("impl", "go", enqueued_by="agent:producer", callback=True)["task_id"]
    h = qm.status(tid)["worker_handle"]
    sm.emit_system_init(h, session_id="sess-1")

    await sm.fail(h, text="halfway", stop_reason="link_lost")

    rec = [r for r in read_records(tmp_path / "queues" / "impl.jsonl")
           if r["event"] == "stalled"][0]
    assert rec["stop_reason"] == "link_lost"
    assert rec["attempt"] == 1
    assert rec["last_text"] == "halfway"


async def test_attempts_accumulate_across_rebuilds(queue_rig):
    """Review Focus 1. Resetting attempts on rebuild is an unbounded retry
    loop that holds the slot forever — the wedged queue this design exists
    to avoid, arriving by the door marked recovery."""
    qm, sm = queue_rig
    tid = qm.enqueue("impl", "go", enqueued_by="agent:producer", callback=True)["task_id"]
    h = qm.status(tid)["worker_handle"]
    sm.emit_system_init(h, session_id="sess-1")

    await sm.fail(h, text="one")
    assert qm._all[tid].attempts == 1

    await sm.fail(h, text="two")
    assert qm._all[tid].attempts == 2


async def test_a_double_finalize_stalls_once(queue_rig, tmp_path):
    """Review Focus 4. Two finished-state callbacks for one handle must
    not rebuild twice; the second finds nothing in _workers."""
    qm, sm = queue_rig
    tid = qm.enqueue("impl", "go", enqueued_by="agent:producer", callback=True)["task_id"]
    h = qm.status(tid)["worker_handle"]
    sm.emit_system_init(h, session_id="sess-1")

    await sm.fail(h, text="halfway", emit_twice=True)

    recs = [r for r in read_records(tmp_path / "queues" / "impl.jsonl")
            if r["event"] == "stalled"]
    assert len(recs) == 1


async def test_a_clean_end_still_completes(queue_rig):
    """The done arm is untouched; this is the regression guard for it."""
    qm, sm = queue_rig
    tid = qm.enqueue("impl", "go", enqueued_by="agent:producer", callback=True)["task_id"]
    h = qm.status(tid)["worker_handle"]
    await sm.finish(h, text="DONE")
    assert qm.status(tid)["status"] == "completed"
    assert h in sm.closed
```

Extend `StubSM` with `fail(handle, text, stop_reason=None, emit_twice=False)` and `inbox_for(handle)`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_queue_stall.py -v`
Expected: FAIL — the worker is closed and the task is `failed`.

- [ ] **Step 3: Add the stall arm**

In `src/aegis/queue/manager.py`, in `_finalize`, after the `_still_working` block and before `task, last_text = self._workers.pop(...)`:

```python
        from aegis.core.recovery import Outcome, classify, rebuild

        task, said = self._workers[session.handle]
        q = self._queues[task.queue]
        # INCREMENT FIRST, then classify. `attempts` means "bad turn-ends
        # INCLUDING this one", which is the convention Task 3's tests pin
        # (`classify(attempts=1, max_attempts=1) is terminal`). Classifying
        # on the pre-increment count gives every queue one extra rebuild and
        # makes `max_attempts: 1` retry once instead of parking on the first
        # stall, which is the opposite of what the spec documents it to mean.
        bumped = replace(task, attempts=task.attempts + 1)
        outcome = classify(
            st,
            attempts=bumped.attempts,
            max_attempts=q.max_attempts,
        )
        if outcome is Outcome.transient:
            self._workers[session.handle] = (bumped, said)
            self._all[task.id] = bumped
            self._inflight[task.queue] = [
                (bumped if x.id == task.id else x)
                for x in self._inflight[task.queue]
            ]
            self._log(
                task.queue,
                {
                    "event": "stalled",
                    "task_id": task.id,
                    "worker_handle": session.handle,
                    "attempt": bumped.attempts,
                    "last_text": said,
                    "stop_reason": getattr(session, "last_stop_reason", None),
                    "error": repr(getattr(session, "last_error", None) or None),
                    "at": self._now(),
                },
            )
            # The slot stays held for the rebuild window and no longer.
            # max_attempts is what keeps that from being "hold until
            # resolved" with extra steps.
            reason = (
                getattr(session, "last_stop_reason", None)
                or repr(getattr(session, "last_error", None))
                or "the turn ended without a result"
            )
            ok = await rebuild(
                self._sm,
                session.handle,
                nudge=NUDGE_STALL.format(reason=reason),
            )
            if not ok:
                await self._park(session, bumped, reason="rebuild failed")
            return
        if outcome is Outcome.terminal:
            # Attempts exhausted. Because the increment happens above, every
            # bad turn end reaching here has attempts >= max_attempts >= 1,
            # so this arm IS the park path — without it the method falls
            # through to the old pop/close/fail flow and the worker is
            # destroyed, which is the whole behaviour being removed.
            await self._park(
                session,
                bumped,
                reason=f"stalled {bumped.attempts} time(s); attempts exhausted",
            )
            return
        # Outcome.done falls through to the existing completion path below,
        # unchanged.
```

A consequence worth stating: with both arms in place, `_finalize` can no longer
produce the `failed` status. Every bad turn end either stalls or parks, and the
spec says so outright — "the `failed` task state becomes nearly unreachable."
`failed` still arrives from `cancel()`, from the TTL reaper in Task 10, and from
the replay in Task 9.

Import `NUDGE_STALL` alongside `classify` and `rebuild`. Note the argument order — `rebuild(sm, handle, *, nudge)`, not `(session, sm, task)` — and note that the park call passes `bumped`, not `task`, so the parked record carries the attempt that just failed.

`_park` lands in Task 8. For this task, stub it as a method raising `NotImplementedError`; every test in this file has budget left and a working rebuild, so none reaches it.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_queue_stall.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Confirm nothing else regressed**

Run: `uv run pytest tests/ -k queue -v`
Expected: PASS. If `test_failed_worker_delivers_error_callback` (`tests/test_queue_manager.py:166`) now fails, that is correct and expected: a failed worker no longer delivers an error callback on the first bad turn. Update it to set `max_attempts=1` on its queue so it still pins the terminal path, and leave a one-line comment saying why.

- [ ] **Step 6: Commit**

```bash
git commit -- src/aegis/queue/manager.py tests/test_queue_stall.py tests/test_queue_manager.py -m "feat(queue): stall and rebuild instead of closing on a bad turn end

A transient turn end now rebuilds the worker in place and nudges it,
holding the max_parallel slot only for the rebuild window. attempts is
carried across the rebuild, because resetting it is an unbounded retry
loop that wedges the queue."
```

---

### Task 8: Park — free the slot and hand the session over

**Files:**
- Modify: `src/aegis/fleet/models.py:17` (leave `EPHEMERAL_KINDS` alone; add the `parked` constant nearby)
- Modify: `src/aegis/queue/manager.py` (add `_park`)
- Test: `tests/test_queue_park.py` (create)

**Interfaces:**
- Consumes: Task 7's stall arm.
- Produces: `QueueManager._park(session, task, *, reason) -> None`; a `recoverable` JSONL record; `Origin(kind="parked", by=<queue>, detail=<task_id>)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_queue_park.py`:

```python
"""Parking promotes a worker out of being disposable.

Leaving EPHEMERAL_KINDS is the whole mechanism, and it buys three
behaviours rather than adding a session state: GhostBook stops reading the
session as a departure and stops fading it after GHOST_TTL, close_guard
starts protecting it like any other session, and plan_resume restores it
across a restart with no special case.
"""
from __future__ import annotations

from aegis.fleet.models import EPHEMERAL_KINDS
from aegis.queue.jsonl import read_records


def test_parked_is_not_an_ephemeral_kind():
    """If it were, the GhostBook would fade the session after 60 seconds
    and the operator would watch their recovery disappear."""
    assert "parked" not in EPHEMERAL_KINDS


async def test_exhausting_attempts_parks_the_worker(queue_rig_max_attempts_1, tmp_path):
    qm, sm = queue_rig_max_attempts_1
    tid = qm.enqueue("impl", "go", enqueued_by="agent:producer", callback=True)["task_id"]
    h = qm.status(tid)["worker_handle"]
    sm.emit_system_init(h, session_id="sess-1")

    await sm.fail(h, text="halfway")

    assert qm.status(tid)["status"] == "recoverable"
    assert h not in sm.closed, "a parked worker is alive, not closed"
    assert sm.get(h).origin.kind == "parked"


async def test_parking_keeps_the_provenance(queue_rig_max_attempts_1):
    """aegis_task_resume looks the session up by this, and the tab label
    shows it."""
    qm, sm = queue_rig_max_attempts_1
    tid = qm.enqueue("impl", "go", enqueued_by="agent:producer", callback=True)["task_id"]
    h = qm.status(tid)["worker_handle"]
    sm.emit_system_init(h, session_id="sess-1")

    await sm.fail(h, text="halfway")

    origin = sm.get(h).origin
    assert origin.by == "impl"
    assert origin.detail == tid


async def test_parking_frees_the_slot(queue_rig_max_attempts_1):
    qm, sm = queue_rig_max_attempts_1
    first = qm.enqueue("impl", "one", enqueued_by="agent:producer", callback=True)["task_id"]
    second = qm.enqueue("impl", "two", enqueued_by="agent:producer", callback=True)["task_id"]
    h = qm.status(first)["worker_handle"]
    sm.emit_system_init(h, session_id="sess-1")

    await sm.fail(h, text="halfway")

    assert qm.status(second)["status"] == "dispatched"


async def test_parking_tells_the_producer_once_and_actionably(
    queue_rig_max_attempts_1,
):
    """The one message the producer gets that it would not have before,
    and only when somebody has to act."""
    qm, sm = queue_rig_max_attempts_1
    tid = qm.enqueue("impl", "go", enqueued_by="agent:producer", callback=True)["task_id"]
    h = qm.status(tid)["worker_handle"]
    sm.emit_system_init(h, session_id="sess-1")

    await sm.fail(h, text="halfway")

    msgs = sm.inbox_for("producer")
    assert len(msgs) == 1
    body = msgs[0].body
    assert h in body, "names the parked session so it can be read"
    assert tid in body, "names the task so it can be resumed"
    assert "aegis_task_resume" in body


async def test_a_failed_rebuild_parks_and_frees_the_slot(
    queue_rig_rebuild_always_fails,
):
    """Review Focus 2, half two. The ACP loadSession probe can fail at
    runtime; that must park, not raise into the finalizer, and must not
    leak the slot."""
    qm, sm = queue_rig_rebuild_always_fails
    first = qm.enqueue("impl", "one", enqueued_by="agent:producer", callback=True)["task_id"]
    second = qm.enqueue("impl", "two", enqueued_by="agent:producer", callback=True)["task_id"]
    h = qm.status(first)["worker_handle"]
    sm.emit_system_init(h, session_id="sess-1")

    await sm.fail(h, text="halfway")

    assert qm.status(first)["status"] == "recoverable"
    assert qm.status(second)["status"] == "dispatched"


async def test_a_worker_with_no_session_id_parks_immediately(queue_rig):
    """Nothing to rebuild, so the retry budget is irrelevant. It parks with
    an honest message rather than burning attempts on an impossibility."""
    qm, sm = queue_rig
    tid = qm.enqueue("impl", "go", enqueued_by="agent:producer", callback=True)["task_id"]
    h = qm.status(tid)["worker_handle"]

    await sm.fail(h, text="died before SystemInit")

    assert qm.status(tid)["status"] == "recoverable"
    assert "no conversation to resume" in sm.inbox_for("producer")[0].body
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_queue_park.py -v`
Expected: FAIL — `_park` raises `NotImplementedError` from Task 7's stub.

- [ ] **Step 3: Implement `_park`**

Replace the Task 7 stub in `src/aegis/queue/manager.py`:

```python
    async def _park(self, session, task: Task, *, reason: str) -> None:
        """Promote a worker out of being disposable and free its slot.

        Leaving EPHEMERAL_KINDS is the whole mechanism. GhostBook stops
        reading the session as a departure and stops fading it after
        GHOST_TTL; close_guard starts protecting it the way it protects
        every non-disposable session; plan_resume restores it across a
        restart with no special case. No new session state, because the
        session is now an ordinary one the operator owns.
        """
        from aegis.fleet.models import Origin

        self._workers.pop(session.handle, None)
        self._chunk_run.pop(session.handle, None)
        parked = replace(
            task,
            status="recoverable",
            worker_handle=session.handle,
            error=reason,
            completed_at=self._now(),
            # Epoch seconds, read by reap_parked in Task 10. Kept apart
            # from completed_at, which is an ISO string for humans and
            # would cost a parse on every reaper tick.
            parked_at=time.time(),
        )
        self._all[task.id] = parked
        self._inflight[task.queue] = [
            t for t in self._inflight[task.queue] if t.id != task.id
        ]
        with contextlib.suppress(Exception):
            session.origin = Origin(
                kind="parked", by=task.queue, detail=task.id
            )
        self._log(
            task.queue,
            {
                "event": "recoverable",
                "task_id": task.id,
                "worker_handle": session.handle,
                "attempts": task.attempts,
                "error": reason,
                "completed_at": parked.completed_at,
            },
        )
        self._emit(
            QueueCompleted(
                task_id=task.id,
                queue=task.queue,
                outcome="recoverable",
                result=None,
                error=reason,
                completed_at=parked.completed_at,
            )
        )
        if task.callback:
            msg = InboxMessage(
                sender=sender_queue(task.queue),
                timestamp=self._now(),
                body=(
                    f"worker stalled and could not be recovered after "
                    f"{task.attempts} attempt(s): {reason}. Its session is "
                    f"parked as {session.handle!r} with the conversation "
                    f"intact. Read it with "
                    f"aegis_read_peer('{session.handle}'), or put it back "
                    f"to work with aegis_task_resume('{task.id}')."
                ),
                task_id=task.id,
                status="error",
            )
            await self._inbox.deliver(_handle_of(task.enqueued_by), msg)
        self._try_dispatch(task.queue)
```

Add `"recoverable"` to `QueueCompleted.outcome`'s `Literal` in `src/aegis/queue/events.py`, add `import time` to `manager.py`, and add the field to `Task` in `src/aegis/queue/schema.py`:

```python
    #: Epoch seconds at which this task was parked, or None. Read by
    #: reap_parked; kept apart from completed_at, which is ISO for humans.
    parked_at: float | None = None
```

- [ ] **Step 4: Park immediately when there is nothing to rebuild**

In the stall arm from Task 7, before incrementing attempts:

```python
        if outcome is Outcome.transient and task.resumable is None:
            await self._park(
                session,
                task,
                reason=(
                    "the worker never reached a turn boundary; "
                    "no conversation to resume"
                ),
            )
            return
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_queue_park.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 6: Run the full gate and commit**

Run: `make test`

```bash
git commit -- src/aegis/queue/manager.py src/aegis/queue/events.py tests/test_queue_park.py -m "feat(queue): park an unrecoverable worker instead of closing it

Parking moves the session's Origin.kind out of EPHEMERAL_KINDS, which
frees the max_parallel slot and hands the operator an ordinary session
holding the whole conversation. GhostBook stops fading it, close_guard
starts protecting it, plan_resume restores it — three behaviours for one
field, rather than a new session state."
```

---

### Task 9: Replay — extract, map, and branch

`manager.py` is 895 lines and this adds four branches. `start()`, `_mark_interrupted` and the new branches are one cohesive unit; move them out.

**Files:**
- Create: `src/aegis/queue/replay.py` (move `start`'s body, `_mark_interrupted`, add `EVENT_STATUS`)
- Modify: `src/aegis/queue/manager.py` (`start` delegates)
- Modify: `src/aegis/core/recovery.py` (add `restore`)
- Test: `tests/test_queue_replay.py` (create)

**Interfaces:**
- Consumes: `Resumable` (Task 3), `rebuild` (Task 6), `_park` (Task 8).
- Produces:
  - `aegis.queue.replay.EVENT_STATUS: dict[str, str]` — lifecycle event name → task status.
  - `aegis.queue.replay.replay(qm) -> None` — what `QueueManager.start` now calls.
  - `recovery.restore(sm, task, *, nudge) -> str | None`.

- [ ] **Step 1: Write the failing structural test**

Create `tests/test_queue_replay.py`:

```python
"""Every lifecycle event maps to a status, and every status has a branch.

Membership in _LIFECYCLE_EVENTS used to mean status = event name. A
`resumed` record under that rule sets a status of "resumed", which matches
no branch, which drops the task out of _all with no callback and blocks
its producer forever. That is a fix for one hang introducing another, and
this file exists so the next person cannot do it by adding one line.
"""
from __future__ import annotations

from aegis.queue.manager import _LIFECYCLE_EVENTS
from aegis.queue.replay import EVENT_STATUS, REPLAY_BRANCHES


def test_every_lifecycle_event_has_a_status():
    missing = _LIFECYCLE_EVENTS - set(EVENT_STATUS)
    assert not missing, (
        f"lifecycle events with no status mapping: {sorted(missing)}. "
        "Without one, replay sets the status to the event name and the "
        "task matches no branch."
    )


def test_every_mapped_status_has_a_replay_branch():
    missing = set(EVENT_STATUS.values()) - REPLAY_BRANCHES
    assert not missing, (
        f"statuses with no replay branch: {sorted(missing)}. A task in one "
        "of these vanishes on restart and its producer waits forever."
    )


def test_resumed_maps_back_to_dispatched():
    assert EVENT_STATUS["resumed"] == "dispatched"
```

- [ ] **Step 2: Write the failing behavioural tests**

Append to `tests/test_queue_replay.py`:

```python
async def test_restart_adopts_a_session_plan_resume_already_restored(
    replay_rig,
):
    """A queue worker is an ordinary tab in workspace.json and plan_resume
    does not filter by origin, so at boot the front end restores the
    worker AND the replay wants it. Whichever runs second would mount a
    second pane under a held handle: DuplicateIds, the whole app. So the
    replay looks before it builds."""
    qm, sm, tmp_path = replay_rig
    sm.preload_session("vivid-laplace", session_id="sess-1")
    before = len(sm.list_sessions())

    await qm.start()

    assert len(sm.list_sessions()) == before, "must adopt, not spawn"
    assert qm.status("01TID1")["status"] == "dispatched"


async def test_restart_rebuilds_when_the_handle_is_free(replay_rig):
    qm, sm, tmp_path = replay_rig

    await qm.start()

    assert sm.get("vivid-laplace") is not None
    assert qm.status("01TID1")["status"] == "dispatched"


async def test_restart_parks_a_task_with_no_session_record(
    replay_rig_no_worker_session,
):
    """Never re-run from the payload: a worker that got halfway may have
    committed, pushed or deployed, and re-running its prompt is a second
    execution, not a recovery."""
    qm, sm, tmp_path = replay_rig_no_worker_session

    await qm.start()

    assert qm.status("01TID1")["status"] == "recoverable"
    assert not sm.spawned, "the payload must not be re-run"


async def test_restart_parks_a_task_whose_attempts_are_exhausted(
    replay_rig_exhausted,
):
    qm, sm, tmp_path = replay_rig_exhausted
    await qm.start()
    assert qm.status("01TID1")["status"] == "recoverable"


async def test_a_recoverable_task_stays_put_and_says_nothing(
    replay_rig_recoverable,
):
    """The producer was told once already; a second message on every
    daemon restart is noise it has to spend a turn reading."""
    qm, sm, tmp_path = replay_rig_recoverable

    await qm.start()

    assert qm.status("01TID1")["status"] == "recoverable"
    assert not sm.inbox_for("lucid-knuth")


async def test_pending_is_still_requeued_at_head_of_fifo(replay_rig_pending):
    qm, sm, tmp_path = replay_rig_pending
    await qm.start()
    assert qm.status("01TID1")["status"] in ("pending", "dispatched")
```

Build each `replay_rig*` fixture by hand-writing a queue JSONL, the way `tests/test_queue_e2e.py:149` already does. Read that test first and copy its shape.

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/test_queue_replay.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.queue.replay'`.

- [ ] **Step 4: Create `replay.py`**

Create `src/aegis/queue/replay.py`. Move `QueueManager.start`'s body and `_mark_interrupted` into module-level functions taking `qm`. Add at the top:

```python
"""Rebuilding the queue's state from its log after a restart.

Split out of manager.py because start(), _mark_interrupted and the branch
per task status are one job and manager.py was already 895 lines of a
different one — the dispatch state machine.

The event-to-status map is the important part. Membership in
_LIFECYCLE_EVENTS used to mean "status = event name", and under that rule a
`resumed` record sets a status no branch below matches, which drops the
task out of _all with no callback and blocks its producer forever.
"""

#: Lifecycle event name -> the task status it implies. NOT the identity
#: map: a `resumed` record means the task is dispatched again.
EVENT_STATUS: dict[str, str] = {
    "enqueued": "pending",
    "dispatched": "dispatched",
    "stalled": "dispatched",
    "resumed": "dispatched",
    "recoverable": "recoverable",
    "completed": "completed",
    "failed": "failed",
}

#: Statuses this module knows how to rebuild. The structural test asserts
#: every value of EVENT_STATUS appears here.
REPLAY_BRANCHES: frozenset[str] = frozenset(
    {"pending", "dispatched", "recoverable", "completed", "failed"}
)
```

Add `"stalled"`, `"resumed"` and `"recoverable"` to `_LIFECYCLE_EVENTS` in `manager.py`. Change the status assignment in the record loop from `tasks[tid]["status"] = rec["event"]` to `tasks[tid]["status"] = EVENT_STATUS[rec["event"]]`.

- [ ] **Step 5: Implement `restore`**

Append to `src/aegis/core/recovery.py`:

```python
async def restore(sm, task, *, nudge: str) -> str | None:
    """Put a worker back after a restart: adopt the session already
    standing under its handle, or rebuild it from the recorded Resumable.

    Looking before building is not an optimisation. A queue worker is an
    ordinary tab in workspace.json with a session_id, and plan_resume does
    not filter by origin, so at boot the front end restores the worker and
    this wants to as well. Whichever ran second would mount a second
    `#pane-<handle>`, which is DuplicateIds and the whole app.

    Adopting a session the front end restored also repairs the orphan the
    old code left: the tab came back with its full conversation while the
    queue had declared its task failed and never looked at it again.
    """
    handle = task.worker_handle
    if not handle:
        return None
    if sm.get(handle) is not None:
        return handle if await rebuild(sm, handle, nudge=nudge) else None
    r = task.resumable
    if r is None:
        return None
    try:
        sm.spawn(
            r.agent_profile,
            handle=handle,
            resume_from=r.session_id,
            host=None if r.host == "local" else r.host,
            cwd=r.cwd or None,
            origin=Origin(kind="queue", by=task.queue, detail=task.id[-4:]),
        )
    except Exception:  # noqa: BLE001 — an unrecoverable task parks
        return None
    s = sm.get(handle)
    if s is None:
        return None
    from aegis.queue.schema import InboxMessage, now_iso, sender_substrate

    await s.deliver(
        InboxMessage(sender=sender_substrate(), timestamp=now_iso(), body=nudge)
    )
    return handle
```

Import `Origin` from `aegis.fleet.models` at the top of `recovery.py`.

- [ ] **Step 6: Wire the replay branches**

In `replay.py`, in the per-task loop, replace the single `dispatched` branch:

```python
            for tid, r in tasks.items():
                status = r["status"]
                if status == "dispatched":
                    task = _task_from_record(queue_name, tid, r)
                    q = qm._queues[queue_name]
                    if task.resumable is None or task.attempts >= q.max_attempts:
                        await _park_from_replay(qm, task, reason=(
                            "the worker never reached a turn boundary; "
                            "no conversation to resume"
                            if task.resumable is None
                            else f"stalled {task.attempts} time(s) before the restart"
                        ))
                        continue
                    handle = await restore(qm._sm, task, nudge=NUDGE_RESTART)
                    if handle is None:
                        await _park_from_replay(
                            qm, task, reason="could not rebuild after the restart"
                        )
                        continue
                    qm._all[tid] = task
                    qm._inflight[queue_name].append(task)
                    qm._workers[handle] = (task, r.get("last_text", ""))
                    qm._attach_observers(qm._sm.get(handle), task)
                    qm._log(queue_name, {
                        "event": "resumed", "task_id": tid,
                        "worker_handle": handle, "at": qm._now(),
                    })
                elif status == "recoverable":
                    qm._all[tid] = _task_from_record(queue_name, tid, r)
                elif status in ("completed", "failed"):
                    ...  # unchanged
                elif status == "pending":
                    ...  # unchanged
```

Write `_task_from_record` and `_park_from_replay` as module-level helpers in `replay.py`; `_park_from_replay` does what `QueueManager._park` does minus the session (there is none to re-origin when the rebuild failed).

- [ ] **Step 7: Run to verify it passes**

Run: `uv run pytest tests/test_queue_replay.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 8: Confirm the pre-existing replay tests still pass**

Run: `uv run pytest tests/test_queue_e2e.py tests/test_queue_persistence.py tests/test_queue_worker_waiting.py -v`
Expected: PASS. `test_restart_replays_handwritten_log_into_failed_interrupted` now has a `dispatched` record with no `worker_session`, so it should park rather than fail-interrupt. Update its assertion to `recoverable` and add a comment saying the old behaviour was the loss this change removes.

- [ ] **Step 9: Run the full gate and commit**

Run: `make check`

```bash
git commit -- src/aegis/queue/replay.py src/aegis/queue/manager.py src/aegis/core/recovery.py tests/test_queue_replay.py tests/test_queue_e2e.py -m "feat(queue): restart replay restores workers instead of failing them

Extracts start()/_mark_interrupted into queue/replay.py and adds an
explicit event-to-status map, because the identity map it replaces would
set a status of 'resumed' that matches no branch — dropping the task and
hanging its producer. restore() adopts a session plan_resume already
restored rather than mounting a second pane under a held handle."
```

---

### Task 10: The `recoverable_ttl_s` reaper

**Files:**
- Modify: `src/aegis/queue/manager.py` (add `reap_parked`)
- Modify: `src/aegis/cli.py` (`_serve`: run it on the existing periodic tick)
- Test: `tests/test_queue_park_ttl.py` (create)

**Interfaces:**
- Consumes: `Queue.recoverable_ttl_s` (Task 2), `_park` (Task 8).
- Produces: `QueueManager.reap_parked(now_epoch: float) -> list[str]` — the task ids it failed.

- [ ] **Step 1: Write the failing test**

Create `tests/test_queue_park_ttl.py`:

```python
"""A parked session is a real session, and IdleReaper reaps the daemon
only after a contiguous run of zero views AND zero sessions
(daemon/lifecycle.py:170). So one forgotten parked worker pins the daemon
open forever and sits in the tab bar. The deadline is what keeps a week of
queue work from becoming a row of dead tabs.
"""
from __future__ import annotations


async def test_a_parked_session_past_its_ttl_is_closed_and_failed(parked_rig):
    qm, sm, tid, handle, parked_at = parked_rig
    reaped = await qm.reap_parked(parked_at + 86401)
    assert reaped == [tid]
    assert qm.status(tid)["status"] == "failed"
    assert handle in sm.closed


async def test_a_parked_session_inside_its_ttl_is_left_alone(parked_rig):
    qm, sm, tid, handle, parked_at = parked_rig
    assert await qm.reap_parked(parked_at + 3600) == []
    assert qm.status(tid)["status"] == "recoverable"
    assert handle not in sm.closed


async def test_ttl_zero_never_reaps(parked_rig_ttl_zero):
    """For a host where keeping them forever is what you want."""
    qm, sm, tid, handle, parked_at = parked_rig_ttl_zero
    assert await qm.reap_parked(parked_at + 999_999) == []
    assert qm.status(tid)["status"] == "recoverable"


async def test_the_discard_is_announced_to_the_producer(parked_rig):
    """Bounded, announced loss after a full day is a different thing from
    the silent loss four seconds after a dropped link that this whole
    change exists to remove."""
    qm, sm, tid, handle, parked_at = parked_rig
    await qm.reap_parked(parked_at + 86401)
    body = sm.inbox_for("producer")[-1].body
    assert "discarded" in body and tid in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_queue_park_ttl.py -v`
Expected: FAIL with `AttributeError: 'QueueManager' object has no attribute 'reap_parked'`.

- [ ] **Step 3: Implement `reap_parked`**

`Task.parked_at` and its write in `_park` both landed in Task 8. Add to `QueueManager`:

```python
    async def reap_parked(self, now_epoch: float) -> list[str]:
        """Close parked sessions past their queue's recoverable_ttl_s and
        fail their tasks. Returns the task ids reaped.

        Takes `now` rather than reading the clock so the test does not
        have to sleep for a day.
        """
        reaped: list[str] = []
        for tid, t in list(self._all.items()):
            if t.status != "recoverable" or t.parked_at is None:
                continue
            ttl = self._queues[t.queue].recoverable_ttl_s
            if ttl <= 0 or now_epoch - t.parked_at < ttl:
                continue
            hours = int((now_epoch - t.parked_at) // 3600)
            failed = replace(
                t,
                status="failed",
                error=f"parked conversation discarded after {hours}h unread",
                completed_at=self._now(),
            )
            self._all[tid] = failed
            self._log(t.queue, {
                "event": "failed", "task_id": tid, "result": None,
                "error": failed.error, "completed_at": failed.completed_at,
                "cost": {},
            })
            if t.callback:
                await self._inbox.deliver(
                    _handle_of(t.enqueued_by),
                    InboxMessage(
                        sender=sender_queue(t.queue),
                        timestamp=self._now(),
                        body=(
                            f"the parked session for task {tid} was "
                            f"discarded after {hours}h unread; its "
                            f"conversation is gone"
                        ),
                        task_id=tid,
                        status="error",
                    ),
                )
            if t.worker_handle:
                with contextlib.suppress(Exception):
                    await self._sm.close(t.worker_handle)
            reaped.append(tid)
        return reaped
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_queue_park_ttl.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Call it periodically**

In `_serve`, find the existing periodic task list (near the `IdleReaper` construction around `cli.py:872`). Add a task that calls `qm.reap_parked(time.time())` every 300 seconds. Follow the `IdleReaper` shape: a small class or a coroutine with a `stop` event, so shutdown is clean.

- [ ] **Step 6: Run the gate and commit**

Run: `make test`

```bash
git commit -- src/aegis/queue/manager.py src/aegis/queue/schema.py src/aegis/cli.py tests/test_queue_park_ttl.py -m "feat(queue): reap parked sessions past recoverable_ttl_s

A parked session keeps the daemon alive — IdleReaper needs zero sessions
— and sits in the tab bar. The deadline closes it after a day and says so,
which is a bounded announced loss rather than the silent one this change
removes."
```

---

### Task 11: Surfaces — MCP tools, TUI commands, read-only CLI

**Files:**
- Modify: `src/aegis/mcp/server.py` (two tools, near `aegis_task_status` at `:2480`)
- Modify: `src/aegis/mcp/server.py` (the briefing string near `:212`)
- Modify: `src/aegis/commands/builtins/core.py` (two `SlashCommand`s, near `/enqueue`)
- Create: `src/aegis/cli_queue.py`
- Modify: `src/aegis/cli.py` (register the sub-app)
- Test: `tests/test_queue_resume_surface.py` (create)

**Interfaces:**
- Consumes: everything above.
- Produces: `QueueManager.resume_task(task_id) -> dict`, `QueueManager.retry_task(task_id) -> dict`; MCP `aegis_task_resume` / `aegis_task_retry`; `/queue`, `/resume`; `aegis queue ls|show`.

**Note on the CLI:** the daemon's unix socket is a view-attachment stream (`daemon/server.py`), not request/response RPC, so a standalone CLI cannot ask a live brain to rebuild anything. `aegis queue` is **read-only**, over the JSONL log. Do not add an acting subcommand; that needs a daemon protocol and is out of scope.

- [ ] **Step 1: Write the failing test**

Create `tests/test_queue_resume_surface.py`:

```python
from __future__ import annotations


async def test_resume_puts_a_parked_worker_back_to_work(parked_rig):
    qm, sm, tid, handle, _ = parked_rig
    res = await qm.resume_task(tid)
    assert res["ok"] is True
    assert qm.status(tid)["status"] == "dispatched"
    assert sm.get(handle).origin.kind == "queue"


async def test_resume_resets_the_attempt_budget(parked_rig):
    """The operator looked at it and said go. That is new information, and
    charging the new run for the old run's failures would park it again
    immediately."""
    qm, sm, tid, handle, _ = parked_rig
    await qm.resume_task(tid)
    assert qm._all[tid].attempts == 0


async def test_resume_on_an_unknown_task_returns_an_error(parked_rig):
    qm, sm, tid, handle, _ = parked_rig
    assert (await qm.resume_task("nope"))["ok"] is False


async def test_resume_on_a_closed_parked_session_fails_gracefully(parked_rig):
    """Review Focus 5. The operator closed the tab; the task still points
    at a dead handle. That is an error dict, not a traceback out of an MCP
    tool."""
    qm, sm, tid, handle, _ = parked_rig
    await sm.close(handle)
    res = await qm.resume_task(tid)
    assert res["ok"] is False
    assert "no longer live" in res["error"]


async def test_resume_on_a_completed_task_is_refused(parked_rig_completed):
    qm, sm, tid, handle, _ = parked_rig_completed
    res = await qm.resume_task(tid)
    assert res["ok"] is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_queue_resume_surface.py -v`
Expected: FAIL with `AttributeError: 'QueueManager' object has no attribute 'resume_task'`.

- [ ] **Step 3: Implement `resume_task` and `retry_task`**

```python
    async def resume_task(self, task_id: str) -> dict:
        """Put a parked worker back to work on its task.

        Resets `attempts`: the operator looked at it and said go, which is
        new information, and charging the new run for the old run's
        failures would park it again on the first stall.
        """
        from aegis.core.recovery import rebuild
        from aegis.fleet.models import Origin

        t = self._all.get(task_id)
        if t is None:
            return {"ok": False, "error": f"unknown task {task_id!r}"}
        if t.status != "recoverable":
            return {"ok": False, "error":
                    f"task {task_id} is {t.status}, not recoverable"}
        handle = t.worker_handle
        s = self._sm.get(handle) if handle else None
        if s is None:
            return {"ok": False, "error":
                    f"the parked session {handle!r} is no longer live; "
                    f"use aegis_task_retry to re-run the task from its payload"}
        q = self._queues[t.queue]
        if len(self._inflight[t.queue]) >= q.max_parallel:
            return {"ok": False, "error":
                    f"queue {t.queue!r} is at max_parallel; try again when a "
                    f"slot frees"}
        resumed = replace(t, status="dispatched", attempts=0,
                          error=None, completed_at=None, parked_at=None)
        self._all[task_id] = resumed
        self._inflight[t.queue].append(resumed)
        self._workers[handle] = (resumed, "")
        s.origin = Origin(kind="queue", by=t.queue, detail=task_id[-4:],
                          returns_to=local_waiter(resumed) or "")
        self._log(t.queue, {"event": "resumed", "task_id": task_id,
                            "worker_handle": handle, "at": self._now()})
        if not await rebuild(self._sm, handle, nudge=NUDGE_OPERATOR):
            await self._park(s, resumed, reason="rebuild failed on resume")
            return {"ok": False, "error": "could not rebuild the harness"}
        return {"ok": True, "status": "dispatched", "worker_handle": handle}
```

`retry_task` is the explicit re-run: refuse unless the task is terminal or recoverable, close any parked session, then `enqueue` the original payload as a new task and return its id. Keep the two separate; never make `resume` fall back to re-running, because a worker that got halfway may already have committed and pushed.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_queue_resume_surface.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Add the MCP tools**

Beside `aegis_task_status` in `src/aegis/mcp/server.py`:

```python
    @server.tool
    async def aegis_task_resume(task_id: str) -> dict:
        """Put a parked queue worker back to work on its task.

        A worker whose turn ended badly too many times is PARKED rather
        than closed: its session stays alive holding the whole
        conversation, and its task sits in `recoverable`. This rebuilds
        the harness under that same session and tells it to continue, so
        nothing it had worked out is lost.

        Read it first with aegis_read_peer(<worker_handle>) if you want to
        know what state it got into. Use aegis_task_retry instead when the
        conversation is not worth resuming.
        """
        return await bridge.queue_manager.resume_task(task_id)

    @server.tool
    async def aegis_task_retry(task_id: str) -> dict:
        """Re-run a task from its original payload as a NEW task.

        Deliberately distinct from aegis_task_resume, and never automatic:
        a worker that got halfway may already have committed, pushed,
        deployed or sent mail, so re-running its prompt is a second
        execution rather than a recovery. Returns the new task_id.
        """
        return await bridge.queue_manager.retry_task(task_id)
```

Add both to the briefing string near `:212`, in the same voice as `aegis_task_status` and `aegis_cancel`.

- [ ] **Step 6: Add the TUI commands**

In `src/aegis/commands/builtins/core.py`, beside the `/enqueue` `SlashCommand`:

```python
    SlashCommand(
        "queue",
        "list queue tasks and their states",
        "/queue [<queue>]",
        _queue_ls,
        spec=ArgSpec(positionals=(
            Arg("queue", required=False,
                completer=lambda b: b.queue_manager.list_queues()),
        )),
    ),
    SlashCommand(
        "resume",
        "put a parked queue worker back to work",
        "/resume <task_id>",
        _queue_resume,
        spec=ArgSpec(positionals=(Arg("task_id"),)),
    ),
```

Write `_queue_ls` and `_queue_resume` as `async def (ctx, args) -> CommandResult`, following the shape of `_enqueue` in the same file.

- [ ] **Step 7: Add the read-only CLI**

Create `src/aegis/cli_queue.py` with `ls` and `show`, reading `.aegis/state/queues/*.jsonl` through `aegis.queue.jsonl.read_records` and folding per task with `aegis.queue.replay.EVENT_STATUS`. Follow `cli_schedule.py` for the Typer and table shape. Register in `cli.py`:

```python
from aegis.cli_queue import app as _queue_app  # noqa: E402

app.add_typer(_queue_app, name="queue")
```

Add a module docstring saying why acting subcommands are absent (the socket is a view stream, not RPC), so nobody adds `aegis queue resume` without reading that first.

- [ ] **Step 8: Document everything new**

`rift check` enforces "every slash command is documented", "every CLI command is documented" and "every aegis_ tool named in a doc is a real tool". Add `aegis_task_resume`, `aegis_task_retry`, `/queue`, `/resume` and `aegis queue ls|show` to the right pages under `docs/`. Add a `CHANGELOG.md` entry covering the whole feature.

Run: `rift check`
Expected: 0 errors, and no new warnings beyond the pre-existing `preview` / `scheduler` config misses.

- [ ] **Step 9: Run the full gate and commit**

Run: `make check`

```bash
git commit -- src/aegis/queue/manager.py src/aegis/mcp/server.py src/aegis/commands/builtins/core.py src/aegis/cli_queue.py src/aegis/cli.py docs CHANGELOG.md tests/test_queue_resume_surface.py -m "feat(queue): resume and retry surfaces for parked workers

aegis_task_resume rebuilds a parked worker's harness and tells it to
continue; aegis_task_retry re-runs the payload as a new task and is kept
separate because a half-finished worker may already have committed and
pushed. The CLI stays read-only: the daemon socket is a view stream, not
RPC, so it cannot ask a live brain to rebuild anything."
```

---

### Task 12: Verify it the way a user reaches it

Nothing above proves the feature works. Every test so far runs against a double, and this whole change is about what survives a process boundary.

**Files:**
- Create: `know-how/recovering-a-dead-worker.md`

- [ ] **Step 1: Boot a real daemon started AFTER the change**

```bash
cd /home/apiad/Workspace/repos/aegis
uv run aegis serve --views &
```

`AGENTS.md`: green tests against a daemon that booted before the change prove nothing about the change.

- [ ] **Step 2: Attach and enqueue a long task**

Attach a TUI, then `/enqueue <queue> Count slowly from 1 to 300, one number per line, pausing a second between each.`

- [ ] **Step 3: Kill the worker's harness process mid-turn**

Find the worker's `claude` subprocess by PID (`pgrep -af claude | grep <something specific to it>`) and `kill <PID>`. **Kill by PID, never `pkill -f` a pattern that matches your own command line.**

- [ ] **Step 4: Observe, and write down what you see**

Confirm all four, by looking at the screen:

1. The worker's tab is still there and still holds its conversation.
2. It picked the count back up rather than restarting from 1.
3. `.aegis/state/queues/<queue>.jsonl` contains `worker_session` and `stalled` records.
4. A second enqueued task did not start while the first was stalled.

- [ ] **Step 5: Exhaust the budget and confirm parking**

Kill the harness twice more. Confirm the tab survives, its label shows it is parked, the queue dispatched the next task, and the producer got exactly one message naming the task id and `aegis_task_resume`.

- [ ] **Step 6: Resume it**

Run `/resume <task_id>` in the TUI. Confirm the worker starts working again in the same tab.

- [ ] **Step 7: Restart the daemon mid-task**

Enqueue another long task, let it run, stop the daemon, start it again, attach. Confirm the worker comes back in one tab (not two — two means the `plan_resume` race is live and Task 9 is wrong) and continues.

- [ ] **Step 8: Write the know-how doc**

Create `know-how/recovering-a-dead-worker.md` with a single-line `when:` in the frontmatter, covering: how to tell a stalled worker from a parked one, how to read a parked worker's conversation before deciding, when to `resume` versus `retry`, and where the queue log lives. `rift check` enforces the `when:` line, and `AGENTS.md` must not name the doc.

- [ ] **Step 9: Final gate and commit**

Run: `make check`
Expected: all green.

```bash
git commit -- know-how/recovering-a-dead-worker.md -m "docs(know-how): recovering a dead queue worker

Verified against a daemon booted after the change: harness killed
mid-turn three times, tab and conversation survived each one, parked on
the fourth, resumed from the TUI, and survived a daemon restart in one
tab rather than two."
```

Report what you actually observed in step 4 and step 7, including anything that did not match. If the worker came back in two tabs, say so — that is the `DuplicateIds` race and it is a blocker, not a detail.
