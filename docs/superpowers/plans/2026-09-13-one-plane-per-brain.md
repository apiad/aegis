# One plane per brain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status: shipped 2026-09-14** (`7deb8a0`, `cffaa86`, `f1822bb`). Written
2026-09-13 against `bc78e3b`. Suite `-m "not live"`: 3866 passed, 1 skipped.
Verified on a real `aegis serve` with a pty client: a monitor armed over MCP
is drawn on the tab's strip, and is not with the pre-change `app.py`.

Departures from the steps below:

- `CONSTRUCTED_PLANES` in `planes.py` names `inbox_router`, `locks`,
  `groups` and `loop_service`, instead of four hand-written assignments in
  `app.py`.
- The local constructions moved into `AegisApp._build_planes()`; the digest
  and quota services are built on both paths, so Tasks 2 and 3 landed as one
  commit.
- Sharing the inbox made two view-side calls unsafe that this plan had not
  seen: `on_mount` started the brain's queue manager a second time, and
  `_close_pane` unbound a session the brain still runs. Both are skipped when
  bridged, with a test for the second.
- Adopt-or-raise broke twelve test files that opened views over a bare
  `SessionManager`; they now build their brain with `tests/brain.py`.
- AGENTS.md no longer has a Layout section, so Task 5's pointer became a
  rule in DESIGN.md's *Rules that span modules*.
- Task 1's `assert ... or True` could never fail and was dropped.

**Goal:** A view renders the brain's queues, monitors, reminders, loops,
canvas, terminals and locks, instead of its own private copies of them.

**Architecture:** `AegisApp` builds every plane in its constructor because
it *is* the AppBridge in the interactive path. Under the daemon the brain
is the bridge, and building them again gives two of everything: agents
reach the brain's through MCP, the UI renders the view's, and they never
meet. The bridged branch adopts instead of constructs, which the
`--remote` branch beside it already does. One declared inventory drives
both the adoption and a coverage test, so a plane added to the brain later
cannot quietly skip the views.

**Tech Stack:** Python 3.13, Textual, pytest.

**Spec:** `docs/superpowers/specs/2026-09-07-retire-web-ui-tui-over-web-design.md`,
section *Brain state versus view state*. Its table is the authority and it
is unambiguous: **"queues, monitors, canvas, terminals, hosts"** are brain
state, "one copy, all views".

## The evidence

Measured on 2026-09-13 by comparing object identity between a real
`SessionManager` and a real view opened over it:

```
plano                brain      vista    same object?
inbox_router         77344      28048      False
queue_manager        83056      88176      False
monitor_manager      83392      88496      False   <- Alex's monitors
reminder_service     83728      88816      False
locks                82720      38544      False
```

The symptom Alex reported: an agent arms a monitor through MCP, the tool
succeeds, and `MonitorStrip` shows nothing, because the strip is
subscribed to the view's `MonitorManager` and the monitor was armed on the
brain's.

The same shape produced the rename bug fixed in `bc78e3b`. That one was
fixed by adding a third announcement kind. This is the general case and
adding announcements one at a time is how it keeps happening.

## Global Constraints

- **The local path does not change.** With no bridge, `AegisApp` is the
  AppBridge and builds its own planes exactly as today. Every assertion in
  this plan about adoption is conditioned on `bridge is not None`.
- **`--remote` does not change.** The `manager is not None` branch
  (`app.py:447-475`) already adopts from its manager and keeps its
  `_DisabledPlaneStub` fallbacks. Do not touch it.
- **Adopt, never fall back silently.** A bridged app that cannot find a
  plane on the brain must raise at construction, not substitute an empty
  one. A silent fallback is what this plan exists to remove.
- **The digest is not a plane.** `QueueDigest` is derived display state
  over a queue manager. Each view builds its own over the **adopted**
  manager. See Task 3.
- **Suite baseline is 3810 passed, 3 skipped** (`-m "not live"`, at
  `bc78e3b`). No regression.

---

## File structure

| File | Responsibility |
|---|---|
| `src/aegis/core/planes.py` | **New.** The declared inventory: which manager attributes a view adopts, and which `attach_*` methods are deliberately not view-facing. No logic, no imports from `tui`. |
| `src/aegis/tui/app.py` | The bridged branch adopts from `bridge` instead of constructing. |
| `tests/core/test_plane_inventory.py` | **New.** The coverage guard: every `attach_*` on `SessionManager` is classified. |
| `tests/views/test_view_uses_the_brains_planes.py` | **New.** Identity per plane, and the end-to-end monitor. |

---

## Task 1: The declared inventory and its coverage guard

This task first, because it is what makes the fix hold. The reason two of
everything existed is that adding a plane to the brain obliges nobody to
wire it to the views. A list that a test forces you to update turns that
omission into a red run.

**Files:**
- Create: `src/aegis/core/planes.py`
- Test: `tests/core/test_plane_inventory.py`

**Interfaces:**
- Produces: `BRAIN_PLANES: tuple[str, ...]` — manager attribute names a
  bridged view adopts. `NOT_VIEW_FACING: dict[str, str]` — `attach_*`
  method names that are not view-facing, mapped to the reason.

- [x] **Step 1: Write the failing test**

Create `tests/core/test_plane_inventory.py`:

```python
"""Every plane the brain grows is classified, or this fails.

Two of every plane existed because adding one to the brain obliged nobody
to wire it to the views: `attach_monitor_manager` landed, the UI kept
rendering its own MonitorManager, and nothing anywhere said so. This test
is the thing that says so.

It does not check that a plane is adopted. It checks that somebody DECIDED,
which is the step that was missing.
"""
from __future__ import annotations

from aegis.core.manager import SessionManager
from aegis.core.planes import BRAIN_PLANES, NOT_VIEW_FACING


def _attach_methods() -> set[str]:
    return {name for name in dir(SessionManager)
            if name.startswith("attach_")}


def test_every_attach_is_classified():
    """A new `attach_foo` must be named in one list or the other. Erring
    toward the loud side: the cost of classifying is one line, the cost of
    forgetting is a UI that lies about what is running."""
    classified = set(NOT_VIEW_FACING)
    for plane in BRAIN_PLANES:
        classified.add(f"attach_{plane}")
    unclassified = _attach_methods() - classified
    assert not unclassified, (
        f"unclassified planes: {sorted(unclassified)}. Add each to "
        "BRAIN_PLANES (a view renders it) or to NOT_VIEW_FACING (with the "
        "reason it does not).")


def test_nothing_is_in_both_lists():
    both = {f"attach_{p}" for p in BRAIN_PLANES} & set(NOT_VIEW_FACING)
    assert not both, f"claimed twice: {sorted(both)}"


def test_every_brain_plane_is_a_real_manager_attribute():
    """Guards a typo in the inventory, which would otherwise surface as a
    view silently falling back to nothing."""
    mgr = SessionManager.__new__(SessionManager)
    for plane in BRAIN_PLANES:
        assert hasattr(SessionManager, f"attach_{plane}") or True, plane
    # The attach_ methods set an attribute of the same name; assert the
    # naming convention holds, since the inventory relies on it.
    for plane in BRAIN_PLANES:
        assert f"attach_{plane}" in _attach_methods(), (
            f"{plane!r} has no attach_{plane} on SessionManager")
```

- [x] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/test_plane_inventory.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.core.planes'`

- [x] **Step 3: Write the inventory**

Create `src/aegis/core/planes.py`:

```python
"""Which of the brain's planes a view renders, declared once.

`AegisApp` builds every plane in its constructor because it IS the
AppBridge in the interactive path. Under the daemon the brain is the
bridge, and building them again gives two of everything: agents reach the
brain's through MCP, the UI renders the view's, and they never meet. An
agent arms a monitor, the tool succeeds, and the strip stays empty.

The spec settles which is which. `2026-09-07-retire-web-ui-tui-over-web-design.md`,
*Brain state versus view state*: "queues, monitors, canvas, terminals,
hosts" are brain state, one copy, all views.

This module is data. It imports nothing from `aegis.tui` so that the
coverage test can hold both sides at once without dragging Textual in.
"""
from __future__ import annotations

#: Manager attributes a bridged view adopts rather than constructs. Each
#: name N has a matching ``SessionManager.attach_N``; the coverage test
#: asserts that, because the adoption code derives one from the other.
BRAIN_PLANES: tuple[str, ...] = (
    "queue_manager",
    "monitor_manager",
    "reminder_service",
    "canvas_manager",
    "terminal_manager",
)

#: `attach_*` methods that are deliberately not rendered by a view, and
#: why. Listed so that a new one cannot be added without somebody deciding
#: which side it belongs on.
NOT_VIEW_FACING: dict[str, str] = {
    "attach_persistence": (
        "a state directory, not an object; the view reads the same "
        "directory through its own roots"),
    "attach_locks_state": (
        "takes a state dir and builds the brain's locks bridge; the view "
        "adopts the resulting `locks` object, which has no attach_locks"),
    "attach_remotes": (
        "peer configuration consumed by the remote plane, not rendered"),
    "attach_remote_plane": (
        "the inbound HTTP plane; has no UI surface"),
    "attach_scheduler_context": (
        "wiring for the scheduler's own loop; the view shows schedules "
        "through MCP tools, not through this object"),
}
```

- [x] **Step 4: Run it to verify it passes**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/core/test_plane_inventory.py -q`
Expected: PASS, 3 tests.

- [x] **Step 5: Mutation-check the guard**

The guard is worthless if it cannot fail. Remove one entry and confirm:

```bash
cd /home/apiad/Workspace/repos/aegis
python3 -c "
from pathlib import Path
p=Path('src/aegis/core/planes.py'); s=p.read_text()
p.write_text(s.replace('    \"monitor_manager\",\n',''))"
.venv/bin/python -m pytest tests/core/test_plane_inventory.py -q
git checkout src/aegis/core/planes.py
```

Expected: FAIL naming `attach_monitor_manager` as unclassified, then green
again after the checkout.

- [x] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/core/planes.py tests/core/test_plane_inventory.py
git commit -m "feat(core): declare which planes a view renders

Two of every plane existed in daemon mode because adding one to the brain
obliged nobody to wire it to the views. The inventory is the obligation:
a new attach_* must be named as a brain plane or as deliberately not
view-facing, with the reason, or the coverage test fails."
```

---

## Task 2: A bridged view adopts the brain's planes

**Files:**
- Modify: `src/aegis/tui/app.py:512-565` (the AppBridge construction block)
- Test: `tests/views/test_view_uses_the_brains_planes.py`

**Interfaces:**
- Consumes: `BRAIN_PLANES` from Task 1.
- Produces: a bridged `AegisApp` whose `queue_manager`, `monitor_manager`,
  `reminder_service`, `canvas_manager` and `terminal_manager` are the
  brain's objects. `inbox_router`, `locks`, `groups` and `loop_service`
  are adopted the same way; they have no `attach_*` and so are named
  explicitly rather than derived.

- [x] **Step 1: Write the failing test**

Create `tests/views/test_view_uses_the_brains_planes.py`:

```python
"""A view renders the brain's planes, not its own copies.

The spec's table is the authority: queues, monitors, canvas and terminals
are brain state, "one copy, all views". They were not. An agent arming a
monitor through MCP reached the brain's MonitorManager while MonitorStrip
rendered the view's, so the tool succeeded and the strip stayed empty.

Asserted on object identity. Equality would pass for two empty managers,
which is exactly the broken state.
"""
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.core.planes import BRAIN_PLANES
from aegis.monitor import MonitorManager
from aegis.queue import InboxRouter, QueueManager, ReminderService
from aegis.views.registry import ViewRegistry

from tests.views.conftest import FakeMCP


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


def _brain(tmp_path):
    """A manager wired the way `cli.py::_serve` wires one."""
    roots = AegisRoots.for_project(tmp_path)
    roster = {"opus": _agent()}
    inbox = InboxRouter()
    mgr = SessionManager(roster, "opus",
                         make_session=lambda p, u, h, **kw: _FakeHarness(),
                         mcp=None, roots=roots, inbox=inbox)
    mgr.attach_queue_manager(QueueManager({}, mgr, inbox))
    mgr.attach_monitor_manager(MonitorManager(inbox, mgr))
    mgr.attach_reminder_service(ReminderService(inbox, mgr))
    mgr.attach_persistence(roots.state_dir)
    return mgr, roots, roster


async def test_a_bridged_view_shares_every_declared_plane(tmp_path):
    mgr, roots, roster = _brain(tmp_path)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(),
                       agents=roster, default_agent="opus",
                       make_session=lambda p, u, h, **kw: _FakeHarness())
    view = await reg.open("tty-1", (100, 30))
    try:
        for plane in BRAIN_PLANES:
            brain_side = getattr(mgr, plane, None)
            if brain_side is None:
                continue          # not wired by this fixture
            assert getattr(view.app, plane) is brain_side, (
                f"{plane}: the view built its own; an agent touching the "
                "brain's would never appear on screen")
    finally:
        await reg.close_all()


async def test_the_inbox_and_locks_come_from_the_brain_too(tmp_path):
    """Neither has an `attach_*`, so neither is derived from the
    inventory, and both are keyed by handle: two of either means a message
    delivered to one is invisible to the other."""
    mgr, roots, roster = _brain(tmp_path)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(),
                       agents=roster, default_agent="opus",
                       make_session=lambda p, u, h, **kw: _FakeHarness())
    view = await reg.open("tty-1", (100, 30))
    try:
        assert view.app.inbox_router is mgr.inbox_router
        assert view.app.locks is mgr.locks
    finally:
        await reg.close_all()


async def test_the_local_path_still_builds_its_own(tmp_path, monkeypatch):
    """The regression guard. With no bridge, AegisApp IS the AppBridge and
    must keep constructing, or `aegis --foreground`'s successor and every
    unit test that builds an app directly loses its planes."""
    from aegis.tui.app import AegisApp

    monkeypatch.chdir(tmp_path)

    class _MCP:
        url = "http://127.0.0.1:0/mcp/"

        def bind(self, b): ...
        async def start(self): ...
        async def stop(self): ...

    app = AegisApp({"opus": _agent()}, "opus",
                   lambda *a, **kw: _FakeHarness(), _MCP())
    assert app.monitor_manager is not None
    assert app.queue_manager is not None
```

- [x] **Step 2: Run it to verify it fails**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/views/test_view_uses_the_brains_planes.py -q`
Expected: FAIL on `test_a_bridged_view_shares_every_declared_plane`, naming
`queue_manager` or `monitor_manager`.

- [x] **Step 3: Adopt in the bridged branch**

In `src/aegis/tui/app.py`, the block that begins
`# AppBridge surface. AegisApp is the bridge in the interactive (TUI) path.`
currently constructs every plane unconditionally. Wrap it:

```python
        # AppBridge surface. Who owns these depends on who the bridge is.
        #
        # With no bridge, AegisApp IS the AppBridge and builds them, which
        # is the interactive path and is unchanged.
        #
        # With a bridge, the BRAIN is the AppBridge, and building a second
        # set is how a view ends up rendering planes nothing else writes
        # to. An agent arms a monitor through MCP, it lands on the brain's
        # MonitorManager, and MonitorStrip is subscribed to this app's.
        # The spec settles it: queues, monitors, canvas and terminals are
        # brain state, one copy, all views.
        if bridge is not None:
            from aegis.core.planes import BRAIN_PLANES

            for _plane in BRAIN_PLANES:
                _owned = getattr(bridge, _plane, None)
                if _owned is None:
                    raise RuntimeError(
                        f"the brain has no {_plane!r}; a view must not "
                        "substitute its own, because nothing else would "
                        "ever write to it")
                setattr(self, _plane, _owned)
            # No `attach_*` of their own, so they are not derived from the
            # inventory. Both are keyed by handle, so two of either means a
            # message delivered to one is invisible to the other.
            self.inbox_router = bridge.inbox_router
            self.locks = bridge.locks
            self.groups = bridge.groups
            self.loop_service = bridge.loop_service
        else:
            self.inbox_router = InboxRouter()
            self.queue_manager = QueueManager(
                self._queues, _SessionManagerAdapter(self), self.inbox_router)
            self.monitor_manager = MonitorManager(self.inbox_router, self)
            from aegis.queue import ReminderService
            self.reminder_service = ReminderService(self.inbox_router, self)
            self.loop_service = LoopService(self)
```

Keep the remaining local-only constructions (`canvas_manager`,
`terminal_manager`, `groups`, `locks`) inside the same `else:` branch,
unchanged from what they are today. The digest is Task 3.

- [x] **Step 4: Run the test to verify it passes**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/views/test_view_uses_the_brains_planes.py -q`
Expected: PASS, 3 tests.

- [x] **Step 5: Run the views and daemon suites**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/views/ tests/daemon/ -q`
Expected: PASS. If `tests/views/test_session_propagation.py` fails, the
adoption changed queue-worker spawning: the brain's `QueueManager` spawns
through the manager rather than through `_SessionManagerAdapter`, so
workers appear via the session observer. That is the intended behaviour;
fix the test's expectation, not the code.

- [x] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/app.py tests/views/test_view_uses_the_brains_planes.py
git commit -m "fix(tui): a bridged view renders the brain's planes

With a bridge the brain is the AppBridge, and building a second set of
planes is how a view ends up rendering objects nothing else writes to: an
agent arms a monitor through MCP, it lands on the brain's MonitorManager,
and the strip subscribed to this app's shows nothing.

Adoption raises rather than falling back. A silent substitute is the bug."
```

---

## Task 3: The digest is view-side, over the adopted manager

**Files:**
- Modify: `src/aegis/tui/app.py` (the `QueueDigest` construction)
- Test: `tests/views/test_view_uses_the_brains_planes.py` (append)

**Interfaces:**
- Consumes: the adopted `self.queue_manager` from Task 2.
- Produces: `self.queue_digest`, one per view, subscribed to the brain's
  queue manager.

- [x] **Step 1: Write the failing test**

Append to `tests/views/test_view_uses_the_brains_planes.py`:

```python
async def test_each_view_has_its_own_digest_over_the_brains_queues(
        tmp_path):
    """The digest is derived display state, not a plane. Two views may
    hold two digests; what they must not hold is two queue managers, or
    one view's chips would count a queue the other cannot see."""
    mgr, roots, roster = _brain(tmp_path)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(),
                       agents=roster, default_agent="opus",
                       make_session=lambda p, u, h, **kw: _FakeHarness())
    a = await reg.open("tty-a", (100, 30))
    b = await reg.open("tty-b", (100, 30))
    try:
        assert a.app.queue_digest is not b.app.queue_digest
        assert a.app.queue_manager is b.app.queue_manager is mgr.queue_manager
        assert a.app.queue_digest._manager is mgr.queue_manager
    finally:
        await reg.close_all()
```

- [x] **Step 2: Run it to verify it fails**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/views/test_view_uses_the_brains_planes.py::test_each_view_has_its_own_digest_over_the_brains_queues -q`
Expected: FAIL, because the digest is built in the `else:` branch only and
a bridged app has none.

- [x] **Step 3: Build the digest on both paths**

Move the digest construction out of the `else:` branch so it runs for both,
immediately after `self.queue_manager` is set either way:

```python
        # Derived display state, not a plane: one per view, reading the
        # queue manager this app ended up with. Two digests over one
        # manager is correct; two managers is the bug Task 2 fixed.
        self.queue_digest = QueueDigest(self.queue_manager)
        self.queue_digest.start()
```

- [x] **Step 4: Run it to verify it passes**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/views/test_view_uses_the_brains_planes.py -q`
Expected: PASS, 4 tests.

- [x] **Step 5: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/app.py tests/views/test_view_uses_the_brains_planes.py
git commit -m "fix(tui): one digest per view, over the brain's queue manager

The digest is derived display state and may be per-view. The manager
underneath it may not."
```

---

## Task 4: The monitor reaches the strip

The end-to-end assertion for the symptom Alex reported. Tasks 2 and 3
assert wiring; this asserts what he sees.

**Files:**
- Test: `tests/views/test_view_uses_the_brains_planes.py` (append)

**Interfaces:**
- Consumes: everything above.

- [x] **Step 1: Write the failing test**

Append to `tests/views/test_view_uses_the_brains_planes.py`:

```python
async def test_a_monitor_armed_on_the_brain_is_visible_to_a_view(tmp_path):
    """What Alex saw: `aegis_monitor_*` succeeded and the strip stayed
    empty. Asserted through the view's own monitor_manager, which is what
    MonitorStrip renders from."""
    mgr, roots, roster = _brain(tmp_path)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(),
                       agents=roster, default_agent="opus",
                       make_session=lambda p, u, h, **kw: _FakeHarness())
    view = await reg.open("tty-1", (100, 30))
    try:
        async with view.app.run_test(headless=False, size=(100, 30)) as p:
            handle = await mgr.spawn("opus")
            await p.pause()
            # Armed on the BRAIN, the way an MCP tool call arrives.
            # `autorun=False` keeps the poller from running a subprocess
            # during the test; the monitor is still registered, which is
            # what the strip reads.
            mgr.monitor_manager.start_monitor(
                from_handle=handle, description="build",
                done="test -f /nonexistent-on-purpose",
                interval_s=3600.0, timeout_s=3600.0, autorun=False)
            await p.pause()

            seen = view.app.monitor_manager.snapshot(for_handle=handle)
            assert seen, (
                "a monitor armed on the brain is invisible to the view; "
                "this is the strip staying empty")
    finally:
        await reg.close_all()
```

- [x] **Step 2: Run it to verify it passes**

Run: `.venv/bin/python -m pytest tests/views/test_view_uses_the_brains_planes.py -q`
Expected: PASS, 5 tests. The real signature is
`start_monitor(*, from_handle, description, done, fail=None,
progress=None, cwd=None, interval_s=2.0, timeout_s=3600.0,
interrupt=False, autorun=True) -> str`; the read is
`snapshot(for_handle=…)`. Both at `src/aegis/monitor/manager.py:76,126`.

- [x] **Step 3: Mutation-check it**

```bash
cd /home/apiad/Workspace/repos/aegis
python3 -c "
from pathlib import Path
p=Path('src/aegis/core/planes.py'); s=p.read_text()
p.write_text(s.replace('    \"monitor_manager\",\n',''))"
.venv/bin/python -m pytest tests/views/test_view_uses_the_brains_planes.py -q
git checkout src/aegis/core/planes.py
```

Expected: the monitor test FAILS with the plane dropped from the
inventory, then green again.

- [x] **Step 4: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add tests/views/test_view_uses_the_brains_planes.py
git commit -m "test(views): a monitor armed on the brain reaches the view

The symptom, asserted end to end rather than as wiring."
```

---

## Task 5: The full suite, and write it down

**Files:**
- Modify: `know-how/the-daemon.md`
- Modify: `AGENTS.md` (the Layout section's `core/` entry)

- [x] **Step 1: Run the whole suite**

```bash
cd /home/apiad/Workspace/repos/aegis
nohup bash -c '.venv/bin/python -m pytest -q -m "not live" > /tmp/aegis-planes.log 2>&1; echo "DONE rc=$?" >> /tmp/aegis-planes.log' > /dev/null 2>&1 &
```

Wait on `grep -q "^DONE rc=" /tmp/aegis-planes.log`, never a fixed sleep.
Expected: no regression against 3810 passed / 3 skipped.

- [x] **Step 2: Add the rule to the daemon know-how**

Append to `know-how/the-daemon.md`:

```markdown
## One plane per brain

Queues, monitors, reminders, canvas and terminals belong to the brain, one
copy for every view. A view adopts them; it must never build its own,
because an agent reaches the brain's through MCP and the UI would render
something nothing writes to. That is what made a monitor armed by an agent
leave the strip empty.

`src/aegis/core/planes.py` declares which ones. Adding an `attach_*` to
`SessionManager` without naming it there fails
`tests/core/test_plane_inventory.py`, which is the point: the decision is
the step that kept being skipped.
```

- [x] **Step 3: Point at it from AGENTS.md**

In the `Layout` section, after the `src/aegis/config/roots.py` entry, add:

```markdown
- `src/aegis/core/planes.py` - which of the brain's planes a view renders,
  declared once. A bridged `AegisApp` adopts these rather than building
  its own; the coverage test forces every new `attach_*` to be classified.
```

- [x] **Step 4: Run rift**

Run: `cd /home/apiad/Workspace/repos/aegis && rift check`
Expected: no new errors. It lints that every path named in the docs exists.

- [x] **Step 5: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add know-how/the-daemon.md AGENTS.md
git commit -m "docs: one plane per brain

The rule, and where the inventory that enforces it lives."
```

---

## Done when

- A monitor armed on the brain appears in a view, asserted end to end.
- `tests/core/test_plane_inventory.py` goes red when a plane is dropped
  from the inventory, and when an `attach_*` is added without being
  classified.
- Every name in `BRAIN_PLANES` is the same object on the brain and in a
  bridged view, asserted by identity.
- Two views hold two digests over one queue manager.
- The local path still builds its own planes, asserted directly.
- `-m "not live"` is green with no regression against 3810.

## Already correct, checked so nobody redoes them

The spec's table names two more things as brain state. Both already are,
and this plan touches neither:

- **hosts.** `AegisApp` takes `host_registry` as a constructor argument
  (`app.py:354,393`) rather than building one, and `cli.py:734` passes the
  brain's into `ViewRegistry`, which forwards it to every view. One
  registry, all views, today.
- **pending messages.** They live on the session, not the pane: the pane
  calls `self._core.cancel_pending(...)` (`pane.py:2174`), and under a
  bridge `_core` IS the brain's `AgentSession`. A message submitted in one
  view is already pending in every view, which is what the spec asks for.

## Deliberately not in this plan

- **`repo_tracker`.** Built before the bridge branch and fed by panes. It
  is arguably brain state by the spec's logic, but it is not in the spec's
  table, it is not part of the reported symptom, and moving it touches the
  sidebar's refresh path. Decide separately.
- **The single-daemon lock.** A different defect with a different cause
  (`ensure_daemon` probes then spawns with nothing atomic between). It is
  next, and it is small.
- **`--remote`.** Its branch already adopts. Stage 6 deletes it.
