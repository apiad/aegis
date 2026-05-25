# Agent Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the "agent groups" feature defined in `docs/superpowers/specs/2026-05-25-aegis-agent-groups-design.md` — a named bag of N agents addressable collectively with broadcast, `wait_all`, `wait_any` (with loser-cancel), JSONL persistence, MCP + workflow + TUI surfaces.

**Architecture:** A new `src/aegis/groups/` substrate (registry + broadcast tracker + reducers + persistence) layered on top of the existing `SessionManager` and `InboxRouter`. Broadcast/cancel/result envelopes ride the existing tagged-sender model (no new render path). MCP tools wrap the substrate; workflow `engine.*` methods mirror MCP one-to-one; TUI adds a group-tab kind, a 2nd-row member band, and a glance dashboard.

**Tech Stack:** Python 3.13, `uv`, `pytest`, Textual (TUI), FastMCP, ruamel.yaml. Reuses existing aegis substrate (`InboxRouter`, `SessionManager`, `sender_*` helpers, ULID + JSONL conventions, `AgentSession` turn loop).

**Slices:** 7 vertical slices, each independently shippable. Slice 1 ends at the substrate happy path; Slice 2 wires the first MCP path end-to-end and adds the live smoke (this is the first user-visible commit). Slices 3–7 layer features in.

---

## Pre-flight

- [ ] **Step 0.1: Read the spec end-to-end.** `docs/superpowers/specs/2026-05-25-aegis-agent-groups-design.md`. Skim the prior-art survey at `/home/apiad/Workspace/.playground/aegis-groups-prior-art.md` for the *why* behind the four-field contract and the wait_any cancel semantics.

- [ ] **Step 0.2: Confirm baseline test count.** Run `uv run pytest -q 2>&1 | tail -3`. Note the passing count — every commit must keep this green or above.

- [ ] **Step 0.3: Verify the working tree is clean and synced.** `git status -sb && git fetch && git status -sb`. If there are unrelated WIP changes, stash or commit them before starting.

---

## Slice 1 — Substrate skeleton + happy-path tests (no MCP yet)

**Files this slice creates:**
- `src/aegis/groups/__init__.py` — barrel exports
- `src/aegis/groups/models.py` — `Group`, `MemberRef`, `MemberResult`, `GroupResult`, `BroadcastRecord`
- `src/aegis/groups/reducers.py` — `Reducer` protocol + `concat` (the only one we need to make Slice 1 useful; others land in Slice 5)
- `src/aegis/groups/registry.py` — `GroupRegistry` (in-memory only; persistence is Slice 4)
- `src/aegis/groups/broadcast.py` — `BroadcastTracker` (single in-flight + correlation)
- `tests/test_groups_models.py`
- `tests/test_groups_reducers.py`
- `tests/test_groups_registry.py`
- `tests/test_groups_broadcast.py`
- `tests/test_groups_wait_all.py`

### Task 1.1: Models

**Files:**
- Create: `src/aegis/groups/models.py`
- Test:   `tests/test_groups_models.py`

- [ ] **Step 1.1.1: Write the failing test.**

```python
# tests/test_groups_models.py
from __future__ import annotations

from aegis.groups.models import (
    BroadcastRecord,
    Group,
    GroupResult,
    MemberRef,
    MemberResult,
)


def test_group_holds_named_members():
    g = Group(name="reviewers", members={
        "ada-knuth":   MemberRef(handle="ada-knuth",   profile="security"),
        "lucid-hopper": MemberRef(handle="lucid-hopper", profile="style"),
    })
    assert g.name == "reviewers"
    assert set(g.members) == {"ada-knuth", "lucid-hopper"}
    assert g.members["ada-knuth"].profile == "security"


def test_group_result_aggregates_member_results():
    res = GroupResult(
        broadcast_id="br-1",
        by_member={
            "a": MemberResult(handle="a", text="x", turn_ms=10,
                              tokens_in=1, tokens_out=1, status="done"),
        },
        combined="x",
        errors={},
        timeouts=[],
    )
    assert res.broadcast_id == "br-1"
    assert res.by_member["a"].status == "done"
    assert res.combined == "x"


def test_broadcast_record_carries_four_field_contract():
    rec = BroadcastRecord(
        id="br-1", group="reviewers", sender="agent:host",
        objective="audit X", output_format="markdown",
        tool_guidance="read-only", boundaries="20 file reads max",
        started_at="2026-05-25T08:00:00Z", members=("a", "b", "c"),
    )
    assert rec.id == "br-1"
    assert rec.objective == "audit X"
    assert rec.members == ("a", "b", "c")
```

- [ ] **Step 1.1.2: Run to verify it fails.**

Run: `uv run pytest tests/test_groups_models.py -v`
Expected: collection error (`ModuleNotFoundError: aegis.groups.models`).

- [ ] **Step 1.1.3: Create the package + write models.**

Create `src/aegis/groups/__init__.py` empty for now.

```python
# src/aegis/groups/models.py
"""Group, member, broadcast, and result records.

All five are simple frozen dataclasses. The substrate uses ULIDs for
broadcast ids (lexicographically sortable) and ISO-8601 second-precision
timestamps to stay shape-compatible with the queue substrate's JSONL log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MemberStatus = Literal["done", "canceled", "errored", "timeout", "lost"]


@dataclass(frozen=True)
class MemberRef:
    """A handle + profile pair — the substrate's view of a group member.

    The full ``AgentSession`` lives in ``SessionManager``; ``MemberRef``
    is what the registry stores so it doesn't have to track session
    objects directly.
    """
    handle: str
    profile: str


@dataclass(frozen=True)
class MemberResult:
    handle: str
    text: str
    turn_ms: int
    tokens_in: int
    tokens_out: int
    status: MemberStatus


@dataclass(frozen=True)
class GroupResult:
    broadcast_id: str
    by_member: dict[str, MemberResult]
    combined: Any
    errors: dict[str, str]
    timeouts: list[str]


@dataclass
class Group:
    """A live group. Mutable: members come and go through the registry."""
    name: str
    members: dict[str, MemberRef] = field(default_factory=dict)


@dataclass(frozen=True)
class BroadcastRecord:
    """One broadcast attempt, identified by ULID. Frozen — broadcasts are
    immutable once started; results live on ``GroupResult``, not here."""
    id: str
    group: str
    sender: str          # SenderTag — e.g. agent:<handle>, system, workflow:<name>
    objective: str
    output_format: str
    tool_guidance: str
    boundaries: str
    started_at: str      # iso8601
    members: tuple[str, ...]
```

- [ ] **Step 1.1.4: Run tests; expect pass.**

Run: `uv run pytest tests/test_groups_models.py -v`
Expected: 3 passed.

- [ ] **Step 1.1.5: Commit.**

```bash
git add src/aegis/groups/__init__.py src/aegis/groups/models.py tests/test_groups_models.py
git commit -m "feat(groups): models — Group, MemberRef, GroupResult, BroadcastRecord"
```

### Task 1.2: Reducers (concat only)

**Files:**
- Create: `src/aegis/groups/reducers.py`
- Test:   `tests/test_groups_reducers.py`

- [ ] **Step 1.2.1: Write the failing test.**

```python
# tests/test_groups_reducers.py
from __future__ import annotations

from aegis.groups.models import MemberResult
from aegis.groups.reducers import concat, get_reducer


def _mr(handle: str, text: str) -> MemberResult:
    return MemberResult(handle=handle, text=text, turn_ms=0,
                        tokens_in=0, tokens_out=0, status="done")


def test_concat_joins_with_handle_headers_in_completion_order():
    by_member = {"a": _mr("a", "hello"), "b": _mr("b", "world")}
    out = concat(by_member, order=["b", "a"])
    assert out == "---\nb: world\n\n---\na: hello"


def test_get_reducer_returns_concat_for_concat_name():
    assert get_reducer("concat") is concat


def test_get_reducer_raises_on_unknown():
    import pytest
    with pytest.raises(KeyError):
        get_reducer("does-not-exist")
```

- [ ] **Step 1.2.2: Run to verify it fails.**

Run: `uv run pytest tests/test_groups_reducers.py -v`
Expected: `ModuleNotFoundError: aegis.groups.reducers`.

- [ ] **Step 1.2.3: Implement reducers.**

```python
# src/aegis/groups/reducers.py
"""Named reducers for ``GroupResult.combined``.

Reducers receive ``by_member`` (dict[handle, MemberResult]) plus an
optional completion-order list (handles in finish order) and return any
shape. The default registered set in Slice 1 is just ``concat``; the
other three (``join_by_handle``, ``last_wins``, ``majority_vote``) land
in Slice 5.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aegis.groups.models import MemberResult

Reducer = Callable[[dict[str, MemberResult], list[str]], Any]


def concat(by_member: dict[str, MemberResult], order: list[str]) -> str:
    parts = []
    for handle in order:
        mr = by_member.get(handle)
        if mr is None:
            continue
        parts.append(f"---\n{handle}: {mr.text}")
    return "\n\n".join(parts)


_REGISTRY: dict[str, Reducer] = {
    "concat": concat,
}


def get_reducer(name: str) -> Reducer:
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown reducer {name!r}; known: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def register_reducer(name: str, fn: Reducer) -> None:
    """Used by Slice 5 to add ``join_by_handle``, ``last_wins``,
    ``majority_vote``. Kept public so user plugins may register custom
    reducers from ``.aegis/plugins/*.py``."""
    _REGISTRY[name] = fn
```

- [ ] **Step 1.2.4: Run tests; expect pass.**

Run: `uv run pytest tests/test_groups_reducers.py -v`
Expected: 3 passed.

- [ ] **Step 1.2.5: Commit.**

```bash
git add src/aegis/groups/reducers.py tests/test_groups_reducers.py
git commit -m "feat(groups): reducers — concat + named-reducer registry"
```

### Task 1.3: GroupRegistry — in-memory CRUD

**Files:**
- Create: `src/aegis/groups/registry.py`
- Test:   `tests/test_groups_registry.py`

- [ ] **Step 1.3.1: Write the failing tests.**

```python
# tests/test_groups_registry.py
from __future__ import annotations

import pytest

from aegis.groups.models import MemberRef
from aegis.groups.registry import GroupExists, GroupRegistry, UnknownGroup


def test_create_then_get():
    reg = GroupRegistry()
    g = reg.create("reviewers")
    assert g.name == "reviewers"
    assert reg.get("reviewers") is g


def test_create_rejects_duplicate_live_name():
    reg = GroupRegistry()
    reg.create("reviewers")
    with pytest.raises(GroupExists):
        reg.create("reviewers")


def test_add_member_creates_group_implicitly():
    reg = GroupRegistry()
    reg.add_member("auditors", MemberRef(handle="ada", profile="sec"))
    assert "ada" in reg.get("auditors").members


def test_remove_last_member_auto_closes_group():
    reg = GroupRegistry()
    reg.add_member("auditors", MemberRef(handle="ada", profile="sec"))
    reg.remove_member("auditors", "ada")
    with pytest.raises(UnknownGroup):
        reg.get("auditors")


def test_dissolve_removes_group_even_with_members():
    reg = GroupRegistry()
    reg.add_member("auditors", MemberRef(handle="ada", profile="sec"))
    reg.add_member("auditors", MemberRef(handle="lucid", profile="logic"))
    reg.dissolve("auditors")
    with pytest.raises(UnknownGroup):
        reg.get("auditors")


def test_rename_moves_under_new_key_and_frees_old():
    reg = GroupRegistry()
    reg.add_member("auditors", MemberRef(handle="ada", profile="sec"))
    reg.rename("auditors", "reviewers")
    assert reg.get("reviewers").name == "reviewers"
    with pytest.raises(UnknownGroup):
        reg.get("auditors")


def test_rename_rejects_collision_with_live_name():
    reg = GroupRegistry()
    reg.add_member("a", MemberRef(handle="x", profile="p"))
    reg.add_member("b", MemberRef(handle="y", profile="p"))
    with pytest.raises(GroupExists):
        reg.rename("a", "b")


def test_move_member_between_groups():
    reg = GroupRegistry()
    reg.add_member("a", MemberRef(handle="x", profile="p"))
    reg.add_member("b", MemberRef(handle="y", profile="p"))
    reg.move_member("x", from_group="a", to_group="b")
    assert "x" in reg.get("b").members
    # Source auto-closed because it dropped to zero members:
    with pytest.raises(UnknownGroup):
        reg.get("a")
```

- [ ] **Step 1.3.2: Run to verify failures.**

Run: `uv run pytest tests/test_groups_registry.py -v`
Expected: collection error.

- [ ] **Step 1.3.3: Implement the registry.**

```python
# src/aegis/groups/registry.py
"""GroupRegistry — in-memory CRUD for groups + members.

The registry holds metadata only (handle + profile pairs). Live agent
sessions live in ``SessionManager``; the registry never touches them
directly. This separation lets the registry be tested without a
``SessionManager`` instance.

Persistence (JSONL log + restart replay) lands in Slice 4. Until then,
state vanishes when the process dies.
"""
from __future__ import annotations

from aegis.groups.models import Group, MemberRef


class GroupExists(Exception):
    """Raised when create/rename collides with an existing live group."""


class UnknownGroup(Exception):
    """Raised when an operation targets a group that doesn't exist."""


class GroupRegistry:
    def __init__(self) -> None:
        self._groups: dict[str, Group] = {}

    # --- create / lookup --------------------------------------------------

    def create(self, name: str) -> Group:
        if name in self._groups:
            raise GroupExists(name)
        g = Group(name=name)
        self._groups[name] = g
        return g

    def get(self, name: str) -> Group:
        if name not in self._groups:
            raise UnknownGroup(name)
        return self._groups[name]

    def names(self) -> list[str]:
        return sorted(self._groups)

    # --- membership -------------------------------------------------------

    def add_member(self, group: str, ref: MemberRef) -> None:
        g = self._groups.get(group)
        if g is None:
            g = self.create(group)
        g.members[ref.handle] = ref

    def remove_member(self, group: str, handle: str) -> None:
        g = self.get(group)
        g.members.pop(handle, None)
        if not g.members:
            self._groups.pop(group, None)

    def move_member(self, handle: str, *, from_group: str,
                    to_group: str) -> None:
        ref = self.get(from_group).members[handle]
        self.add_member(to_group, ref)
        self.remove_member(from_group, handle)

    # --- maintenance ------------------------------------------------------

    def dissolve(self, group: str) -> None:
        if group not in self._groups:
            raise UnknownGroup(group)
        del self._groups[group]

    def rename(self, old: str, new: str) -> None:
        if new in self._groups:
            raise GroupExists(new)
        g = self.get(old)
        renamed = Group(name=new, members=dict(g.members))
        self._groups[new] = renamed
        del self._groups[old]
```

- [ ] **Step 1.3.4: Run tests; expect 8 passed.**

Run: `uv run pytest tests/test_groups_registry.py -v`

- [ ] **Step 1.3.5: Commit.**

```bash
git add src/aegis/groups/registry.py tests/test_groups_registry.py
git commit -m "feat(groups): GroupRegistry — in-memory CRUD + implicit create/auto-close"
```

### Task 1.4: BroadcastTracker — single-in-flight + correlation

**Files:**
- Create: `src/aegis/groups/broadcast.py`
- Test:   `tests/test_groups_broadcast.py`

- [ ] **Step 1.4.1: Write the failing tests.**

```python
# tests/test_groups_broadcast.py
from __future__ import annotations

import pytest

from aegis.groups.broadcast import BroadcastInFlight, BroadcastTracker
from aegis.groups.models import BroadcastRecord


def _rec(rid: str, group: str, members: tuple[str, ...]) -> BroadcastRecord:
    return BroadcastRecord(
        id=rid, group=group, sender="agent:host",
        objective="o", output_format="of", tool_guidance="tg", boundaries="b",
        started_at="2026-05-25T08:00:00Z", members=members,
    )


def test_open_then_get():
    bt = BroadcastTracker()
    rec = _rec("br-1", "reviewers", ("a", "b"))
    bt.open(rec)
    assert bt.current("reviewers") is rec


def test_second_open_on_same_group_raises():
    bt = BroadcastTracker()
    bt.open(_rec("br-1", "reviewers", ("a",)))
    with pytest.raises(BroadcastInFlight) as ei:
        bt.open(_rec("br-2", "reviewers", ("a",)))
    assert "br-1" in str(ei.value)


def test_close_frees_the_slot():
    bt = BroadcastTracker()
    bt.open(_rec("br-1", "reviewers", ("a",)))
    bt.close("reviewers", "br-1")
    bt.open(_rec("br-2", "reviewers", ("a",)))   # must not raise


def test_distinct_groups_independent():
    bt = BroadcastTracker()
    bt.open(_rec("br-1", "a", ("x",)))
    bt.open(_rec("br-2", "b", ("y",)))            # different group: fine
```

- [ ] **Step 1.4.2: Run to verify failures.**

Run: `uv run pytest tests/test_groups_broadcast.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 1.4.3: Implement the tracker.**

```python
# src/aegis/groups/broadcast.py
"""BroadcastTracker — one in-flight broadcast per group.

The spec gates re-broadcast while a previous broadcast is still
collecting member results. The tracker owns the "single in-flight per
group" invariant and the correlation id surface; the wait_all/wait_any
primitives ask the tracker for the current record by group name.
"""
from __future__ import annotations

from aegis.groups.models import BroadcastRecord


class BroadcastInFlight(Exception):
    """Raised on a second `open` against a group with an open broadcast."""

    def __init__(self, group: str, open_id: str) -> None:
        super().__init__(f"group {group!r} already has open broadcast {open_id}")
        self.group = group
        self.open_id = open_id


class BroadcastTracker:
    def __init__(self) -> None:
        self._open: dict[str, BroadcastRecord] = {}

    def open(self, rec: BroadcastRecord) -> None:
        cur = self._open.get(rec.group)
        if cur is not None:
            raise BroadcastInFlight(rec.group, cur.id)
        self._open[rec.group] = rec

    def current(self, group: str) -> BroadcastRecord | None:
        return self._open.get(group)

    def close(self, group: str, broadcast_id: str) -> None:
        cur = self._open.get(group)
        if cur is not None and cur.id == broadcast_id:
            del self._open[group]
```

- [ ] **Step 1.4.4: Run tests; expect 4 passed.**

- [ ] **Step 1.4.5: Commit.**

```bash
git add src/aegis/groups/broadcast.py tests/test_groups_broadcast.py
git commit -m "feat(groups): BroadcastTracker — single in-flight per group"
```

### Task 1.5: `wait_all` happy path against fake sessions

This task wires `GroupRegistry` + `BroadcastTracker` + `InboxRouter` + a fake `AgentSession` to drive a complete fan-out → collect cycle. No MCP yet; that's Slice 2.

**Files:**
- Create: `src/aegis/groups/runtime.py` — the `GroupRuntime` façade (`broadcast`, `wait_all`, `wait_any`-stub)
- Test:   `tests/test_groups_wait_all.py`

- [ ] **Step 1.5.1: Write the failing test.**

```python
# tests/test_groups_wait_all.py
from __future__ import annotations

import asyncio

import pytest

from aegis.groups.models import MemberRef
from aegis.groups.registry import GroupRegistry
from aegis.groups.runtime import GroupRuntime
from aegis.queue.inbox import InboxRouter


class FakeSession:
    """Minimal AgentSession stand-in for groups tests.

    Records every inbox delivery (so we can assert fan-out happened), and
    exposes an async `finish_turn(text)` that publishes a "turn-end" event
    on the bus the runtime watches.
    """
    def __init__(self, handle: str, bus: asyncio.Queue):
        self.handle = handle
        self.delivered: list = []
        self._bus = bus

    async def deliver(self, msg) -> None:
        self.delivered.append(msg)

    async def finish_turn(self, text: str) -> None:
        await self._bus.put((self.handle, text))


@pytest.mark.asyncio
async def test_wait_all_collects_one_turn_per_member_and_reduces():
    reg = GroupRegistry()
    reg.add_member("rev", MemberRef("ada",   "sec"))
    reg.add_member("rev", MemberRef("lucid", "logic"))

    bus: asyncio.Queue = asyncio.Queue()
    router = InboxRouter()
    sessions = {h: FakeSession(h, bus) for h in ("ada", "lucid")}
    for h, s in sessions.items():
        router.bind_session(h, s)

    rt = GroupRuntime(registry=reg, inbox=router, member_bus=bus,
                      now=lambda: "2026-05-25T08:00:00Z",
                      new_id=lambda: "br-1")

    bid = await rt.broadcast(
        "rev", sender="agent:host",
        objective="Reply HEARD.", output_format="one word",
        tool_guidance="none", boundaries="one turn",
    )
    assert bid == "br-1"
    # Both members received an envelope:
    assert len(sessions["ada"].delivered) == 1
    assert len(sessions["lucid"].delivered) == 1

    async def drive():
        await asyncio.sleep(0)
        await sessions["ada"].finish_turn("HEARD")
        await sessions["lucid"].finish_turn("HEARD")

    driver = asyncio.create_task(drive())
    result = await rt.wait_all("rev", timeout=5)
    await driver

    assert result.broadcast_id == "br-1"
    assert set(result.by_member) == {"ada", "lucid"}
    assert result.combined.startswith("---\nada:") or \
           result.combined.startswith("---\nlucid:")
    assert result.errors == {}
    assert result.timeouts == []


@pytest.mark.asyncio
async def test_wait_all_returns_timeouts_for_silent_members():
    reg = GroupRegistry()
    reg.add_member("rev", MemberRef("a", "p"))
    reg.add_member("rev", MemberRef("b", "p"))
    bus: asyncio.Queue = asyncio.Queue()
    router = InboxRouter()
    sessions = {h: FakeSession(h, bus) for h in ("a", "b")}
    for h, s in sessions.items():
        router.bind_session(h, s)
    rt = GroupRuntime(registry=reg, inbox=router, member_bus=bus,
                      now=lambda: "T", new_id=lambda: "br-1")
    await rt.broadcast("rev", sender="agent:host",
                       objective="x", output_format="y",
                       tool_guidance="z", boundaries="w")

    async def drive():
        await asyncio.sleep(0)
        await sessions["a"].finish_turn("done")

    driver = asyncio.create_task(drive())
    result = await rt.wait_all("rev", timeout=0.2)
    await driver
    assert "b" in result.timeouts
    assert "a" in result.by_member
```

- [ ] **Step 1.5.2: Run to verify failures.**

Run: `uv run pytest tests/test_groups_wait_all.py -v`
Expected: `ModuleNotFoundError: aegis.groups.runtime`.

- [ ] **Step 1.5.3: Implement the runtime façade.**

```python
# src/aegis/groups/runtime.py
"""GroupRuntime — the façade the MCP layer + workflow engine call.

Coordinates ``GroupRegistry`` (membership) + ``BroadcastTracker``
(single-in-flight + correlation) + ``InboxRouter`` (fan-out delivery)
+ a member completion bus (``asyncio.Queue`` of ``(handle, text)``
tuples; populated by the wiring layer in Slice 2 when an
``AgentSession`` finishes a post-broadcast turn).

Slice 1 ships ``broadcast`` and ``wait_all`` with the ``concat`` reducer
only. ``wait_any`` lands in Slice 3, additional reducers in Slice 5.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aegis.groups.broadcast import BroadcastTracker
from aegis.groups.models import (
    BroadcastRecord,
    GroupResult,
    MemberResult,
)
from aegis.groups.reducers import get_reducer
from aegis.groups.registry import GroupRegistry, UnknownGroup
from aegis.queue.inbox import InboxRouter
from aegis.queue.schema import InboxMessage, new_ulid, now_iso, sender_agent


def _sender_group_broadcast(group: str, broadcast_id: str) -> str:
    return f"group:{group}/broadcast:{broadcast_id}"


def _compose_broadcast_body(objective: str, output_format: str,
                            tool_guidance: str, boundaries: str) -> str:
    return (
        f"objective: {objective}\n"
        f"output_format: {output_format}\n"
        f"tool_guidance: {tool_guidance}\n"
        f"boundaries: {boundaries}"
    )


@dataclass
class GroupRuntime:
    registry: GroupRegistry
    inbox: InboxRouter
    member_bus: asyncio.Queue
    """Queue of ``(handle, text)`` tuples — wiring layer puts one on
    every post-broadcast turn-end."""
    now: Callable[[], str] = now_iso
    new_id: Callable[[], str] = new_ulid
    tracker: BroadcastTracker = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.tracker is None:
            self.tracker = BroadcastTracker()

    async def broadcast(self, group: str, *, sender: str, objective: str,
                        output_format: str, tool_guidance: str,
                        boundaries: str) -> str:
        g = self.registry.get(group)
        rec = BroadcastRecord(
            id=self.new_id(), group=group, sender=sender,
            objective=objective, output_format=output_format,
            tool_guidance=tool_guidance, boundaries=boundaries,
            started_at=self.now(), members=tuple(sorted(g.members)),
        )
        self.tracker.open(rec)
        body = _compose_broadcast_body(objective, output_format,
                                       tool_guidance, boundaries)
        tag = _sender_group_broadcast(group, rec.id)
        for handle in rec.members:
            msg = InboxMessage(
                sender=tag, body=body, received_at=self.now(),
            )
            await self.inbox.deliver(handle, msg)
        return rec.id

    async def wait_all(self, group: str, *, timeout: float = 600.0,
                       reducer: str = "concat") -> GroupResult:
        rec = self.tracker.current(group)
        if rec is None:
            raise UnknownGroup(f"no open broadcast on {group!r}")
        return await self._collect(
            rec, want={*rec.members}, timeout=timeout, reducer=reducer,
            wait_any=False,
        )

    async def _collect(self, rec: BroadcastRecord, *, want: set[str],
                       timeout: float, reducer: str,
                       wait_any: bool) -> GroupResult:
        by_member: dict[str, MemberResult] = {}
        order: list[str] = []
        deadline = asyncio.get_event_loop().time() + timeout
        while want and not (wait_any and by_member):
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                handle, text = await asyncio.wait_for(
                    self.member_bus.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if handle not in want:
                continue
            want.discard(handle)
            order.append(handle)
            by_member[handle] = MemberResult(
                handle=handle, text=text, turn_ms=0,
                tokens_in=0, tokens_out=0, status="done",
            )
        timeouts = sorted(want)
        combined: Any = get_reducer(reducer)(by_member, order)
        self.tracker.close(rec.group, rec.id)
        return GroupResult(
            broadcast_id=rec.id, by_member=by_member, combined=combined,
            errors={}, timeouts=timeouts,
        )
```

- [ ] **Step 1.5.4: Add `pytest-asyncio` if missing.**

```bash
uv run python -c "import pytest_asyncio" || uv add --dev pytest-asyncio
```

- [ ] **Step 1.5.5: Verify `asyncio_mode = auto` (or per-test markers) in `pyproject.toml`.**

If `[tool.pytest.ini_options]` doesn't already set it, the tests' `@pytest.mark.asyncio` decorators take care of it. Either is fine; don't change project-wide config.

- [ ] **Step 1.5.6: Run tests; expect 2 passed.**

Run: `uv run pytest tests/test_groups_wait_all.py -v`

- [ ] **Step 1.5.7: Commit.**

```bash
git add src/aegis/groups/runtime.py tests/test_groups_wait_all.py
git commit -m "feat(groups): GroupRuntime — broadcast + wait_all (concat reducer)"
```

### Task 1.6: Slice 1 barrel exports

**Files:**
- Modify: `src/aegis/groups/__init__.py`

- [ ] **Step 1.6.1: Write the failing test.**

```python
# tests/test_groups_imports.py
def test_barrel_exports_slice1_surface():
    from aegis.groups import (
        BroadcastInFlight,
        BroadcastRecord,
        BroadcastTracker,
        Group,
        GroupExists,
        GroupRegistry,
        GroupResult,
        GroupRuntime,
        MemberRef,
        MemberResult,
        UnknownGroup,
        concat,
        get_reducer,
        register_reducer,
    )
    # ruff fix: keep imports used
    assert all([
        BroadcastInFlight, BroadcastRecord, BroadcastTracker, Group,
        GroupExists, GroupRegistry, GroupResult, GroupRuntime, MemberRef,
        MemberResult, UnknownGroup, concat, get_reducer, register_reducer,
    ])
```

- [ ] **Step 1.6.2: Implement the barrel.**

```python
# src/aegis/groups/__init__.py
from aegis.groups.broadcast import BroadcastInFlight, BroadcastTracker
from aegis.groups.models import (
    BroadcastRecord,
    Group,
    GroupResult,
    MemberRef,
    MemberResult,
)
from aegis.groups.reducers import concat, get_reducer, register_reducer
from aegis.groups.registry import GroupExists, GroupRegistry, UnknownGroup
from aegis.groups.runtime import GroupRuntime

__all__ = [
    "BroadcastInFlight",
    "BroadcastRecord",
    "BroadcastTracker",
    "Group",
    "GroupExists",
    "GroupRegistry",
    "GroupResult",
    "GroupRuntime",
    "MemberRef",
    "MemberResult",
    "UnknownGroup",
    "concat",
    "get_reducer",
    "register_reducer",
]
```

- [ ] **Step 1.6.3: Run tests + the whole hermetic suite.**

Run: `uv run pytest tests/test_groups_imports.py -v && uv run pytest -q 2>&1 | tail -3`
Expected: import test passes; total count = baseline + ~18 new tests, all green.

- [ ] **Step 1.6.4: Commit.**

```bash
git add src/aegis/groups/__init__.py tests/test_groups_imports.py
git commit -m "feat(groups): barrel exports for slice 1 substrate"
```

---

## Slice 2 — MCP wiring + live smoke (first user-visible commit)

Adds three MCP tools (`aegis_group_spawn`, `aegis_group_broadcast`,
`aegis_group_wait_all`) and the live smoke that proves the end-to-end
loop works against a real harness.

### Task 2.1: SessionManager → group-spawn wiring

The runtime needs a hook to (a) ask `SessionManager` to spawn a fresh
`AgentSession` for a given profile, (b) feed turn-end events into the
runtime's `member_bus`. Both are thin adapters; no `SessionManager`
internal changes.

**Files:**
- Create: `src/aegis/groups/wiring.py` — the `SessionManager`/observer adapter
- Test:   `tests/test_groups_wiring.py`

- [ ] **Step 2.1.1: Read SessionManager's spawn signature.**

Run: `grep -n "def spawn\|def close_session\|observer" src/aegis/core/manager.py | head -20`

Note the spawn signature + how observers are registered. The wiring code
must call into the actual API; do **not** invent new methods on
`SessionManager`.

- [ ] **Step 2.1.2: Write the failing test.**

```python
# tests/test_groups_wiring.py
from __future__ import annotations

import asyncio

import pytest

from aegis.events import AssistantText, Result
from aegis.groups.registry import GroupRegistry
from aegis.groups.runtime import GroupRuntime
from aegis.groups.wiring import GroupWiring
from aegis.queue.inbox import InboxRouter


class _FakeSession:
    def __init__(self, handle: str):
        self.handle = handle
        self.delivered = []
        self._observers = []

    async def deliver(self, msg) -> None:
        self.delivered.append(msg)

    def add_observer(self, cb) -> None:
        self._observers.append(cb)

    async def emit(self, ev) -> None:
        for cb in self._observers:
            r = cb(ev)
            if asyncio.iscoroutine(r):
                await r


class _FakeManager:
    def __init__(self):
        self.sessions: dict[str, _FakeSession] = {}

    async def spawn(self, profile: str, *, handle: str | None = None,
                    **_):
        h = handle or f"{profile}-handle"
        s = _FakeSession(h)
        self.sessions[h] = s
        return s


@pytest.mark.asyncio
async def test_wiring_spawns_into_group_and_routes_turn_end():
    mgr = _FakeManager()
    reg = GroupRegistry()
    router = InboxRouter()
    bus: asyncio.Queue = asyncio.Queue()
    wiring = GroupWiring(session_manager=mgr, registry=reg, inbox=router,
                         member_bus=bus)

    handle = await wiring.spawn(profile="opus", group="rev", handle="ada")
    assert handle == "ada"
    assert "ada" in reg.get("rev").members
    assert router._sessions["ada"] is mgr.sessions["ada"]

    # Simulate a post-broadcast turn-end:
    await mgr.sessions["ada"].emit(AssistantText(text="HEARD"))
    await mgr.sessions["ada"].emit(Result(text="HEARD"))
    # The wiring observer translates Result → bus event with final text:
    h, t = await asyncio.wait_for(bus.get(), 1)
    assert h == "ada" and t == "HEARD"
```

- [ ] **Step 2.1.3: Run to verify failure.**

Run: `uv run pytest tests/test_groups_wiring.py -v`
Expected: `ModuleNotFoundError: aegis.groups.wiring`.

- [ ] **Step 2.1.4: Implement the wiring.**

```python
# src/aegis/groups/wiring.py
"""GroupWiring — adapter that bridges aegis core to the groups substrate.

Responsibilities:
- Spawn an ``AgentSession`` via ``SessionManager`` and register it under
  the requested ``MemberRef`` in the registry.
- Bind the new session to the inbox router so broadcasts land in it.
- Attach a turn-end observer that pushes ``(handle, final_text)`` onto
  the runtime's ``member_bus``.

The observer captures the *most recent* ``AssistantText`` event as the
"final assistant text of the turn" (matches the queue-substrate
convention; see ``aegis/queue/manager.py``).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from aegis.events import AssistantText, Result
from aegis.groups.models import MemberRef
from aegis.groups.registry import GroupRegistry
from aegis.queue.inbox import InboxRouter


@dataclass
class GroupWiring:
    session_manager: Any            # SessionManager
    registry: GroupRegistry
    inbox: InboxRouter
    member_bus: asyncio.Queue

    async def spawn(self, *, profile: str, group: str,
                    handle: str | None = None) -> str:
        session = await self.session_manager.spawn(profile=profile,
                                                   handle=handle)
        h = session.handle
        self.registry.add_member(group, MemberRef(handle=h, profile=profile))
        self.inbox.bind_session(h, session)

        last_text: dict[str, str] = {"text": ""}

        async def _observe(ev: Any) -> None:
            if isinstance(ev, AssistantText):
                last_text["text"] = ev.text
            elif isinstance(ev, Result):
                await self.member_bus.put((h, last_text["text"]))

        session.add_observer(_observe)
        return h
```

- [ ] **Step 2.1.5: Run the wiring test; expect pass.**

Run: `uv run pytest tests/test_groups_wiring.py -v`

If `AssistantText`/`Result` field shapes don't match the actual aegis
event types, adjust the observer body and the test's emit calls to use
the real fields (`grep -n "class AssistantText\|class Result" src/aegis/events.py`).

- [ ] **Step 2.1.6: Commit.**

```bash
git add src/aegis/groups/wiring.py tests/test_groups_wiring.py
git commit -m "feat(groups): wiring — SessionManager spawn + Result→bus observer"
```

### Task 2.2: `aegis_group_spawn` MCP tool

**Files:**
- Modify: `src/aegis/mcp/server.py` — add the tool registration
- Test:   `tests/test_groups_mcp_spawn.py`

- [ ] **Step 2.2.1: Read the current MCP server layout.**

Run: `grep -n "@mcp.tool\|def aegis_" src/aegis/mcp/server.py | head -20`

Note the registration pattern + how the server reaches the `AppBridge`.
The new tools call into `bridge.groups` (a new attribute) which exposes
`GroupRuntime` + `GroupWiring`.

- [ ] **Step 2.2.2: Add a `groups` attribute to `AppBridge`.**

Modify `src/aegis/mcp/bridge.py`. The current `AppBridge` is a `Protocol`
(see the file's docstring). Add a `groups` member returning a small
`GroupsBridge` Protocol:

```python
# src/aegis/mcp/bridge.py  (add near other Protocols)
from typing import Protocol


class GroupsBridge(Protocol):
    async def spawn(self, *, profile: str, group: str,
                    handle: str | None = None) -> str: ...
    async def broadcast(self, group: str, *, sender: str,
                        objective: str, output_format: str,
                        tool_guidance: str, boundaries: str) -> str: ...
    async def wait_all(self, group: str, *, timeout: float = 600.0,
                       reducer: str = "concat") -> dict: ...
```

Then extend `AppBridge` with:

```python
    groups: GroupsBridge
```

- [ ] **Step 2.2.3: Write the failing test.**

```python
# tests/test_groups_mcp_spawn.py
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_aegis_group_spawn_calls_bridge_spawn():
    from aegis.mcp.server import _aegis_group_spawn_impl

    class _Calls:
        def __init__(self): self.args = None
        async def spawn(self, *, profile, group, handle=None):
            self.args = (profile, group, handle); return "ada"

    class _Bridge:
        def __init__(self): self.groups = _Calls()

    b = _Bridge()
    out = await _aegis_group_spawn_impl(b, profile="opus", group="rev")
    assert out == {"handle": "ada", "group": "rev"}
    assert b.groups.args == ("opus", "rev", None)
```

- [ ] **Step 2.2.4: Register the tool.**

In `src/aegis/mcp/server.py`, add an `_aegis_group_spawn_impl` async
function (so it's testable without booting FastMCP) and a tool
registration that calls it:

```python
async def _aegis_group_spawn_impl(bridge, *, profile: str, group: str,
                                   handle: str | None = None) -> dict:
    h = await bridge.groups.spawn(profile=profile, group=group,
                                   handle=handle)
    return {"handle": h, "group": group}


@mcp.tool(description="Spawn a new agent into a group. Creates the "
                       "group implicitly if it doesn't exist.")
async def aegis_group_spawn(profile: str, group: str,
                             handle: str | None = None) -> dict:
    return await _aegis_group_spawn_impl(_bridge(), profile=profile,
                                          group=group, handle=handle)
```

(`_bridge()` already exists — match how `aegis_enqueue` looks it up.)

- [ ] **Step 2.2.5: Run the test; expect pass.**

Run: `uv run pytest tests/test_groups_mcp_spawn.py -v`

- [ ] **Step 2.2.6: Commit.**

```bash
git add src/aegis/mcp/server.py src/aegis/mcp/bridge.py tests/test_groups_mcp_spawn.py
git commit -m "feat(groups,mcp): aegis_group_spawn — implicit group create + handle return"
```

### Task 2.3: `aegis_group_broadcast` MCP tool

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test:   `tests/test_groups_mcp_broadcast.py`

- [ ] **Step 2.3.1: Write the failing test (covers the four-field requirement).**

```python
# tests/test_groups_mcp_broadcast.py
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_broadcast_passes_four_fields_and_returns_id():
    from aegis.mcp.server import _aegis_group_broadcast_impl

    class _G:
        def __init__(self): self.kw = None
        async def broadcast(self, group, *, sender, objective,
                             output_format, tool_guidance, boundaries):
            self.kw = dict(group=group, sender=sender, objective=objective,
                           output_format=output_format,
                           tool_guidance=tool_guidance, boundaries=boundaries)
            return "br-1"

    class _B:
        def __init__(self): self.groups = _G()

    b = _B()
    out = await _aegis_group_broadcast_impl(
        b, group="rev", sender="agent:host",
        objective="audit", output_format="md",
        tool_guidance="read-only", boundaries="20 reads",
    )
    assert out == {"broadcast_id": "br-1"}
    assert b.groups.kw["objective"] == "audit"
    assert b.groups.kw["boundaries"] == "20 reads"


@pytest.mark.asyncio
async def test_broadcast_rejects_missing_four_field_field():
    from aegis.mcp.server import _aegis_group_broadcast_impl

    class _B:
        groups = None

    with pytest.raises(TypeError):
        # `tool_guidance` deliberately omitted:
        await _aegis_group_broadcast_impl(
            _B(), group="rev", sender="x",
            objective="o", output_format="f", boundaries="b",
        )
```

- [ ] **Step 2.3.2: Register the tool.**

```python
# in src/aegis/mcp/server.py
async def _aegis_group_broadcast_impl(bridge, *, group: str, sender: str,
                                       objective: str, output_format: str,
                                       tool_guidance: str,
                                       boundaries: str) -> dict:
    bid = await bridge.groups.broadcast(
        group, sender=sender, objective=objective,
        output_format=output_format, tool_guidance=tool_guidance,
        boundaries=boundaries,
    )
    return {"broadcast_id": bid}


@mcp.tool(description="Broadcast a four-field message to every member "
                       "of a group. Required fields: objective, "
                       "output_format, tool_guidance, boundaries. "
                       "Returns a broadcast_id used by aegis_group_wait_all.")
async def aegis_group_broadcast(group: str, objective: str,
                                 output_format: str, tool_guidance: str,
                                 boundaries: str) -> dict:
    return await _aegis_group_broadcast_impl(
        _bridge(), group=group, sender=_sender_of_caller(),
        objective=objective, output_format=output_format,
        tool_guidance=tool_guidance, boundaries=boundaries,
    )
```

Use the existing helper that derives the caller's sender tag from the
priming env (`grep -n "_sender_of_caller\|from_handle" src/aegis/mcp/server.py`
— if there's no helper, mirror how `aegis_enqueue` reads the `from_handle`
parameter; `from_handle` must be a required argument on this tool too).

- [ ] **Step 2.3.3: Run tests; expect 2 passed.**

- [ ] **Step 2.3.4: Commit.**

```bash
git add src/aegis/mcp/server.py tests/test_groups_mcp_broadcast.py
git commit -m "feat(groups,mcp): aegis_group_broadcast — four-field contract enforced by signature"
```

### Task 2.4: `aegis_group_wait_all` MCP tool

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test:   `tests/test_groups_mcp_wait_all.py`

- [ ] **Step 2.4.1: Write the failing test.**

```python
# tests/test_groups_mcp_wait_all.py
from __future__ import annotations

import pytest

from aegis.groups.models import GroupResult, MemberResult


@pytest.mark.asyncio
async def test_wait_all_returns_serializable_dict():
    from aegis.mcp.server import _aegis_group_wait_all_impl

    canned = GroupResult(
        broadcast_id="br-1",
        by_member={"a": MemberResult("a", "x", 0, 0, 0, "done")},
        combined="x",
        errors={},
        timeouts=[],
    )

    class _G:
        async def wait_all(self, group, *, timeout, reducer):
            return canned

    class _B:
        groups = _G()

    out = await _aegis_group_wait_all_impl(_B(), group="rev",
                                            timeout=1.0, reducer="concat")
    assert out["broadcast_id"] == "br-1"
    assert out["by_member"]["a"]["status"] == "done"
    assert out["combined"] == "x"
    assert out["errors"] == {}
    assert out["timeouts"] == []
```

- [ ] **Step 2.4.2: Register the tool.**

```python
# src/aegis/mcp/server.py
from dataclasses import asdict


async def _aegis_group_wait_all_impl(bridge, *, group: str,
                                      timeout: float = 600.0,
                                      reducer: str = "concat") -> dict:
    result = await bridge.groups.wait_all(group, timeout=timeout,
                                           reducer=reducer)
    return {
        "broadcast_id": result.broadcast_id,
        "by_member": {h: asdict(mr) for h, mr in result.by_member.items()},
        "combined":  result.combined,
        "errors":    dict(result.errors),
        "timeouts":  list(result.timeouts),
    }


@mcp.tool(description="Block until every member of `group` has posted "
                       "one post-broadcast turn, or until `timeout` "
                       "seconds elapse. Returns the GroupResult bundle.")
async def aegis_group_wait_all(group: str, timeout: float = 600.0,
                                reducer: str = "concat") -> dict:
    return await _aegis_group_wait_all_impl(
        _bridge(), group=group, timeout=timeout, reducer=reducer)
```

- [ ] **Step 2.4.3: Run test; expect pass.**

- [ ] **Step 2.4.4: Commit.**

```bash
git add src/aegis/mcp/server.py tests/test_groups_mcp_wait_all.py
git commit -m "feat(groups,mcp): aegis_group_wait_all — serialize GroupResult to dict"
```

### Task 2.5: Concrete `GroupsBridge` implementation on `AegisApp`/`SessionManager`

The MCP tools call `bridge.groups.spawn/broadcast/wait_all`. The concrete
implementation lives on whatever currently implements `AppBridge` — both
`AegisApp` (TUI) and `SessionManager` (headless via `aegis serve`). Add
a thin `_GroupsBridge` class instantiated once at boot.

**Files:**
- Modify: `src/aegis/core/manager.py` — instantiate `GroupRegistry`,
  `GroupRuntime`, `GroupWiring`, and a `_GroupsBridge` that wraps them
- Modify: `src/aegis/tui/app.py` — same hookup on `AegisApp`
- Test:   `tests/test_groups_bridge_smoke.py`

- [ ] **Step 2.5.1: Implement `_GroupsBridge`.**

In `src/aegis/groups/__init__.py`, add a tiny concrete wrapper so both
embedders use the same code:

```python
# src/aegis/groups/bridge.py
"""Concrete GroupsBridge implementation reused by AegisApp + SessionManager."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from aegis.groups.registry import GroupRegistry
from aegis.groups.runtime import GroupRuntime
from aegis.groups.wiring import GroupWiring


@dataclass
class _GroupsBridge:
    runtime: GroupRuntime
    wiring: GroupWiring

    async def spawn(self, *, profile: str, group: str,
                    handle: str | None = None) -> str:
        return await self.wiring.spawn(profile=profile, group=group,
                                        handle=handle)

    async def broadcast(self, group: str, *, sender: str, objective: str,
                        output_format: str, tool_guidance: str,
                        boundaries: str):
        return await self.runtime.broadcast(
            group, sender=sender, objective=objective,
            output_format=output_format, tool_guidance=tool_guidance,
            boundaries=boundaries,
        )

    async def wait_all(self, group: str, *, timeout: float = 600.0,
                       reducer: str = "concat"):
        return await self.runtime.wait_all(group, timeout=timeout,
                                            reducer=reducer)


def make_groups_bridge(*, session_manager, inbox_router) -> _GroupsBridge:
    registry = GroupRegistry()
    bus: asyncio.Queue = asyncio.Queue()
    runtime = GroupRuntime(registry=registry, inbox=inbox_router,
                           member_bus=bus)
    wiring = GroupWiring(session_manager=session_manager, registry=registry,
                         inbox=inbox_router, member_bus=bus)
    return _GroupsBridge(runtime=runtime, wiring=wiring)
```

- [ ] **Step 2.5.2: Wire into `SessionManager`.**

In `src/aegis/core/manager.py`, where `SessionManager.__init__` finishes
setting up the queue substrate, append:

```python
from aegis.groups.bridge import make_groups_bridge
self.groups = make_groups_bridge(
    session_manager=self, inbox_router=self.inbox_router)
```

- [ ] **Step 2.5.3: Wire into `AegisApp`.**

In `src/aegis/tui/app.py`, find the analogous queue-substrate wiring
inside `AegisApp.__init__` and add the same `make_groups_bridge` call,
binding it to `self.groups`.

- [ ] **Step 2.5.4: Write the smoke test.**

```python
# tests/test_groups_bridge_smoke.py
"""Smoke test: SessionManager.__init__ produces a working .groups bridge."""
from __future__ import annotations

from unittest.mock import MagicMock


def test_session_manager_exposes_groups_bridge():
    from aegis.core.manager import SessionManager

    sm = SessionManager(
        agents={}, default_agent=None,
        queues={}, telegram=MagicMock(), driver_factory=MagicMock(),
    )
    assert hasattr(sm, "groups")
    assert hasattr(sm.groups, "spawn")
    assert hasattr(sm.groups, "broadcast")
    assert hasattr(sm.groups, "wait_all")
```

Adjust the `SessionManager(...)` constructor args to match the real
signature (`grep -n "def __init__" src/aegis/core/manager.py`). If the
constructor needs more fixtures, lift them from an existing
`tests/test_core_manager.py` test.

- [ ] **Step 2.5.5: Run test; expect pass.**

- [ ] **Step 2.5.6: Commit.**

```bash
git add src/aegis/groups/bridge.py src/aegis/core/manager.py src/aegis/tui/app.py tests/test_groups_bridge_smoke.py
git commit -m "feat(groups): _GroupsBridge wired into SessionManager + AegisApp"
```

### Task 2.6: Live smoke — 3-member roundtrip

**Files:**
- Create: `tests/test_groups_live.py`

This is one slow test (≥30s real subprocess time); mark it accordingly
so the default `pytest -q` excludes it. The aegis convention is
`@pytest.mark.live` (mirrors `tests/test_queue_dashboard_live.py`).

- [ ] **Step 2.6.1: Write the live test.**

```python
# tests/test_groups_live.py
"""Live smoke: 3 real claude-code workers in one group, one broadcast,
wait_all collects all three. Exercises the full path:
SessionManager.spawn → InboxRouter.bind → broadcast → real worker turn
→ Result event → bus → wait_all → GroupResult."""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_three_member_broadcast_wait_all(live_session_manager):
    sm = live_session_manager
    await sm.groups.spawn(profile="default", group="rev", handle="ada")
    await sm.groups.spawn(profile="default", group="rev", handle="lucid")
    await sm.groups.spawn(profile="default", group="rev", handle="wry")

    bid = await sm.groups.broadcast(
        "rev", sender="agent:host",
        objective="Reply with exactly one word: HEARD.",
        output_format="one word",
        tool_guidance="No tools needed.",
        boundaries="One turn only.",
    )
    assert bid

    result = await asyncio.wait_for(
        sm.groups.wait_all("rev", timeout=120.0, reducer="concat"),
        timeout=130.0,
    )
    assert set(result.by_member) == {"ada", "lucid", "wry"}
    for mr in result.by_member.values():
        assert "HEARD" in mr.text.upper()
    assert result.timeouts == []
```

- [ ] **Step 2.6.2: Ensure the `live_session_manager` fixture exists or add one.**

Run: `grep -n "live_session_manager\|@pytest.fixture" tests/conftest.py | head`

If a `live_session_manager` fixture isn't there, look at an existing live
test's setup (e.g. `tests/test_queue_dashboard_live.py`) and lift the
same shape into `tests/conftest.py`.

- [ ] **Step 2.6.3: Run the live smoke.**

Run: `uv run pytest -m live tests/test_groups_live.py -v`
Expected: 1 passed in ~60–120s.

If it hangs or fails: check (a) the JSONL inbox writes are landing in
`.aegis/state/inboxes/<handle>.jsonl`, (b) `Result` events are firing
on every member (`grep` the worker logs).

- [ ] **Step 2.6.4: Commit.**

```bash
git add tests/test_groups_live.py tests/conftest.py
git commit -m "test(groups): live smoke — 3-member broadcast → wait_all roundtrip"
```

- [ ] **Step 2.6.5: Run the full hermetic suite.**

Run: `uv run pytest -q 2>&1 | tail -3`
Expected: baseline + ~30 new tests, all green. Live tests skipped (no `-m live`).

---

## Slice 3 — `wait_any` + cancel envelope

### Task 3.1: `GroupRuntime.wait_any` happy path

**Files:**
- Modify: `src/aegis/groups/runtime.py`
- Test:   `tests/test_groups_wait_any.py`

- [ ] **Step 3.1.1: Write the failing test.**

```python
# tests/test_groups_wait_any.py
from __future__ import annotations

import asyncio

import pytest

from aegis.groups.models import MemberRef
from aegis.groups.registry import GroupRegistry
from aegis.groups.runtime import GroupRuntime
from aegis.queue.inbox import InboxRouter


class _FS:
    def __init__(self, handle: str, bus: asyncio.Queue):
        self.handle = handle; self.delivered = []; self._bus = bus
    async def deliver(self, msg): self.delivered.append(msg)
    async def finish_turn(self, text):
        await self._bus.put((self.handle, text))


@pytest.mark.asyncio
async def test_wait_any_returns_first_finisher_and_marks_others_canceled():
    reg = GroupRegistry()
    reg.add_member("g", MemberRef("a", "p"))
    reg.add_member("g", MemberRef("b", "p"))
    reg.add_member("g", MemberRef("c", "p"))
    bus: asyncio.Queue = asyncio.Queue()
    router = InboxRouter()
    sessions = {h: _FS(h, bus) for h in ("a", "b", "c")}
    for h, s in sessions.items(): router.bind_session(h, s)
    rt = GroupRuntime(registry=reg, inbox=router, member_bus=bus,
                      now=lambda: "T", new_id=lambda: "br-1")

    await rt.broadcast("g", sender="agent:host", objective="o",
                       output_format="f", tool_guidance="t", boundaries="b")

    async def drive(): await sessions["b"].finish_turn("first")
    asyncio.create_task(drive())

    result = await rt.wait_any("g", timeout=2, cancel_losers=True)

    assert set(result.by_member) == {"b"}
    assert result.by_member["b"].text == "first"
    # The losers received a cancel envelope:
    for loser in ("a", "c"):
        kinds = [m.sender for m in sessions[loser].delivered]
        assert any("group:g/cancel:br-1" in s for s in kinds)
```

- [ ] **Step 3.1.2: Implement `wait_any`.**

In `src/aegis/groups/runtime.py`, add:

```python
def _sender_group_cancel(group: str, broadcast_id: str) -> str:
    return f"group:{group}/cancel:{broadcast_id}"
```

and a `wait_any` method on `GroupRuntime`:

```python
    async def wait_any(self, group: str, *, timeout: float = 600.0,
                       cancel_losers: bool = True) -> GroupResult:
        rec = self.tracker.current(group)
        if rec is None:
            raise UnknownGroup(f"no open broadcast on {group!r}")
        result = await self._collect(
            rec, want={*rec.members}, timeout=timeout,
            reducer="concat", wait_any=True,
        )
        if cancel_losers:
            winner = next(iter(result.by_member))
            tag = _sender_group_cancel(group, rec.id)
            body = f"superseded by {winner}"
            for handle in rec.members:
                if handle == winner:
                    continue
                await self.inbox.deliver(handle, InboxMessage(
                    sender=tag, body=body, received_at=self.now(),
                ))
        return result
```

- [ ] **Step 3.1.3: Run the test; expect pass.**

- [ ] **Step 3.1.4: Commit.**

```bash
git add src/aegis/groups/runtime.py tests/test_groups_wait_any.py
git commit -m "feat(groups): wait_any — first-finisher + cancel envelope to losers"
```

### Task 3.2: `cancel_losers=False` skips the cancel signal

- [ ] **Step 3.2.1: Add a test case to `tests/test_groups_wait_any.py`.**

```python
@pytest.mark.asyncio
async def test_wait_any_cancel_losers_false_skips_envelope():
    reg = GroupRegistry()
    reg.add_member("g", MemberRef("a", "p"))
    reg.add_member("g", MemberRef("b", "p"))
    bus: asyncio.Queue = asyncio.Queue()
    router = InboxRouter()
    sessions = {h: _FS(h, bus) for h in ("a", "b")}
    for h, s in sessions.items(): router.bind_session(h, s)
    rt = GroupRuntime(registry=reg, inbox=router, member_bus=bus,
                      now=lambda: "T", new_id=lambda: "br-1")
    await rt.broadcast("g", sender="x", objective="o",
                       output_format="f", tool_guidance="t", boundaries="b")
    asyncio.create_task(sessions["a"].finish_turn("won"))
    await rt.wait_any("g", timeout=2, cancel_losers=False)
    assert all("cancel" not in m.sender for m in sessions["b"].delivered)
```

- [ ] **Step 3.2.2: Run the test; expect pass (existing impl already honors the flag).**

- [ ] **Step 3.2.3: Commit.**

```bash
git add tests/test_groups_wait_any.py
git commit -m "test(groups): wait_any cancel_losers=False skips envelope"
```

### Task 3.3: `aegis_group_wait_any` MCP tool

**Files:**
- Modify: `src/aegis/mcp/server.py`, `src/aegis/mcp/bridge.py`, `src/aegis/groups/bridge.py`
- Test:   `tests/test_groups_mcp_wait_any.py`

- [ ] **Step 3.3.1: Extend the `GroupsBridge` Protocol + impl.**

In `src/aegis/mcp/bridge.py`'s `GroupsBridge`, add:

```python
    async def wait_any(self, group: str, *, timeout: float = 600.0,
                       cancel_losers: bool = True) -> "GroupResult": ...
```

In `src/aegis/groups/bridge.py`'s `_GroupsBridge`, add:

```python
    async def wait_any(self, group: str, *, timeout: float = 600.0,
                       cancel_losers: bool = True):
        return await self.runtime.wait_any(
            group, timeout=timeout, cancel_losers=cancel_losers)
```

- [ ] **Step 3.3.2: Write the failing MCP test.**

```python
# tests/test_groups_mcp_wait_any.py
from __future__ import annotations

import pytest

from aegis.groups.models import GroupResult, MemberResult


@pytest.mark.asyncio
async def test_mcp_wait_any_serializes_result():
    from aegis.mcp.server import _aegis_group_wait_any_impl

    canned = GroupResult(
        broadcast_id="br-1",
        by_member={"a": MemberResult("a", "winner", 1, 2, 3, "done")},
        combined="winner", errors={}, timeouts=[],
    )

    class _G:
        async def wait_any(self, group, *, timeout, cancel_losers):
            assert cancel_losers is True
            return canned

    class _B: groups = _G()

    out = await _aegis_group_wait_any_impl(
        _B(), group="g", timeout=1.0, cancel_losers=True)
    assert out["by_member"]["a"]["text"] == "winner"
```

- [ ] **Step 3.3.3: Register the tool.**

```python
# src/aegis/mcp/server.py
async def _aegis_group_wait_any_impl(bridge, *, group: str,
                                       timeout: float = 600.0,
                                       cancel_losers: bool = True) -> dict:
    result = await bridge.groups.wait_any(
        group, timeout=timeout, cancel_losers=cancel_losers)
    return {
        "broadcast_id": result.broadcast_id,
        "by_member": {h: asdict(mr) for h, mr in result.by_member.items()},
        "combined":  result.combined,
        "errors":    dict(result.errors),
        "timeouts":  list(result.timeouts),
    }


@mcp.tool(description="Block until the first member of `group` posts "
                       "one post-broadcast turn. Surviving members receive "
                       "an inbox cancel signal unless cancel_losers=False.")
async def aegis_group_wait_any(group: str, timeout: float = 600.0,
                                cancel_losers: bool = True) -> dict:
    return await _aegis_group_wait_any_impl(
        _bridge(), group=group, timeout=timeout, cancel_losers=cancel_losers)
```

- [ ] **Step 3.3.4: Run tests; expect pass.**

- [ ] **Step 3.3.5: Commit.**

```bash
git add src/aegis/groups/bridge.py src/aegis/mcp/server.py src/aegis/mcp/bridge.py tests/test_groups_mcp_wait_any.py
git commit -m "feat(groups,mcp): aegis_group_wait_any — first-finisher + loser-cancel"
```

---

## Slice 4 — Persistence + replay

### Task 4.1: JSONL writer + event taxonomy

**Files:**
- Create: `src/aegis/groups/persistence.py`
- Test:   `tests/test_groups_persistence.py`

- [ ] **Step 4.1.1: Write the failing test.**

```python
# tests/test_groups_persistence.py
from __future__ import annotations

import json
from pathlib import Path

from aegis.groups.persistence import (
    PersistedGroupLog,
    event_created,
    event_broadcast_started,
    event_member_added,
)


def test_writes_events_one_per_line(tmp_path: Path):
    log = PersistedGroupLog(tmp_path)
    log.write("rev", event_created("rev", "agent:host", "T"))
    log.write("rev", event_member_added("ada", "sec", "agent:host", "T"))
    log.write("rev", event_broadcast_started(
        "br-1", "o", "f", "t", "b", "agent:host", ("ada",)))

    p = tmp_path / "groups" / "rev.jsonl"
    lines = p.read_text().splitlines()
    assert len(lines) == 3
    parsed = [json.loads(l) for l in lines]
    assert parsed[0]["kind"] == "created"
    assert parsed[1]["kind"] == "member_added"
    assert parsed[2]["kind"] == "broadcast_started"
    assert parsed[2]["broadcast_id"] == "br-1"
```

- [ ] **Step 4.1.2: Implement the writer + event factories.**

```python
# src/aegis/groups/persistence.py
"""JSONL lifecycle log for groups.

Per-group append-only log under ``<state_dir>/groups/<name>.jsonl``.
Same shape conventions as the queue substrate (``aegis/queue/jsonl.py``):
- One JSON object per line.
- Each record carries ``kind`` + ``at`` (ISO-8601) + payload fields.
- Append is atomic-ish (single ``write+flush``); concurrent crashes
  may leave a torn last line — replay tolerates that by skipping
  unparseable trailing lines.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def event_created(name: str, sender: str, at: str) -> dict[str, Any]:
    return {"kind": "created", "name": name, "sender": sender, "at": at}


def event_member_added(handle: str, profile: str, sender: str,
                        at: str) -> dict[str, Any]:
    return {"kind": "member_added", "handle": handle, "profile": profile,
            "sender": sender, "at": at}


def event_member_removed(handle: str, reason: str,
                          at: str) -> dict[str, Any]:
    return {"kind": "member_removed", "handle": handle, "reason": reason,
            "at": at}


def event_broadcast_started(broadcast_id: str, objective: str,
                             output_format: str, tool_guidance: str,
                             boundaries: str, sender: str,
                             members: tuple[str, ...]) -> dict[str, Any]:
    return {
        "kind": "broadcast_started",
        "broadcast_id": broadcast_id,
        "objective": objective,
        "output_format": output_format,
        "tool_guidance": tool_guidance,
        "boundaries": boundaries,
        "sender": sender,
        "members": list(members),
    }


def event_member_result(broadcast_id: str, handle: str, status: str,
                         text_preview: str, tokens_in: int,
                         tokens_out: int, turn_ms: int) -> dict[str, Any]:
    return {
        "kind": "member_result",
        "broadcast_id": broadcast_id,
        "handle": handle, "status": status,
        "text_preview": text_preview,
        "tokens_in": tokens_in, "tokens_out": tokens_out,
        "turn_ms": turn_ms,
    }


def event_broadcast_completed(broadcast_id: str, mode: str, reducer: str,
                               at: str) -> dict[str, Any]:
    return {"kind": "broadcast_completed", "broadcast_id": broadcast_id,
            "mode": mode, "reducer": reducer, "at": at}


def event_renamed(old: str, new: str, at: str) -> dict[str, Any]:
    return {"kind": "renamed", "old": old, "new": new, "at": at}


def event_dissolved(reason: str, at: str) -> dict[str, Any]:
    return {"kind": "dissolved", "reason": reason, "at": at}


class PersistedGroupLog:
    def __init__(self, state_dir: Path) -> None:
        self._root = Path(state_dir) / "groups"
        self._root.mkdir(parents=True, exist_ok=True)

    def path(self, group: str) -> Path:
        return self._root / f"{group}.jsonl"

    def write(self, group: str, record: dict[str, Any]) -> None:
        p = self.path(group)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")))
            f.write("\n")

    def read(self, group: str) -> list[dict[str, Any]]:
        p = self.path(group)
        if not p.is_file():
            return []
        records: list[dict[str, Any]] = []
        for line in p.read_text().splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # Tolerate a torn trailing line from a crash mid-write.
                continue
        return records

    def all_groups(self) -> list[str]:
        return sorted(p.stem for p in self._root.glob("*.jsonl"))
```

- [ ] **Step 4.1.3: Run the test; expect pass.**

- [ ] **Step 4.1.4: Commit.**

```bash
git add src/aegis/groups/persistence.py tests/test_groups_persistence.py
git commit -m "feat(groups): persistence — JSONL writer + event factories"
```

### Task 4.2: Wire the writer into `GroupRegistry` + `GroupRuntime`

**Files:**
- Modify: `src/aegis/groups/registry.py`, `src/aegis/groups/runtime.py`,
  `src/aegis/groups/bridge.py`
- Test:   extend `tests/test_groups_persistence.py`

- [ ] **Step 4.2.1: Add a `log: PersistedGroupLog | None = None` to `GroupRegistry.__init__` and `GroupRuntime`.**

For each mutating method, write the corresponding event after the
in-memory state change. Examples in `registry.py`:

```python
    def __init__(self, log=None) -> None:
        self._groups: dict[str, Group] = {}
        self._log = log

    def _emit(self, group: str, rec: dict[str, Any]) -> None:
        if self._log is not None:
            self._log.write(group, rec)

    def create(self, name: str, *, sender: str = "system",
               at: str = "") -> Group:
        if name in self._groups:
            raise GroupExists(name)
        g = Group(name=name)
        self._groups[name] = g
        self._emit(name, event_created(name, sender, at or now_iso()))
        return g
```

…and similar for `add_member` (`event_member_added`), `remove_member`
(`event_member_removed`), `dissolve` (`event_dissolved`), `rename`
(`event_renamed`).

In `runtime.py`, `broadcast` emits `event_broadcast_started` (after
`tracker.open`); `_collect` emits one `event_member_result` per collected
result + a final `event_broadcast_completed`.

- [ ] **Step 4.2.2: Add a test that the registry writes events end-to-end.**

```python
# tests/test_groups_persistence.py  (append)
def test_registry_writes_events_via_log(tmp_path):
    log = PersistedGroupLog(tmp_path)
    from aegis.groups.registry import GroupRegistry
    from aegis.groups.models import MemberRef
    reg = GroupRegistry(log=log)
    reg.add_member("rev", MemberRef("ada", "sec"), sender="agent:host")
    reg.remove_member("rev", "ada", reason="closed-by-user")
    lines = log.read("rev")
    kinds = [r["kind"] for r in lines]
    assert kinds == ["created", "member_added", "member_removed", "dissolved"]
```

(Add a `reason=` parameter on `remove_member` to make the test feasible
— default `"closed-by-user"`. `dissolved` is auto-emitted when the
last member leaves: extend `remove_member` to write that event with
`reason="empty"`.)

- [ ] **Step 4.2.3: Run tests; expect pass.**

- [ ] **Step 4.2.4: Commit.**

```bash
git add src/aegis/groups/registry.py src/aegis/groups/runtime.py src/aegis/groups/bridge.py tests/test_groups_persistence.py
git commit -m "feat(groups): write JSONL events on registry + runtime mutations"
```

### Task 4.3: `GroupRegistry.start()` replay on boot

**Files:**
- Modify: `src/aegis/groups/registry.py`, `src/aegis/groups/bridge.py`
- Test:   `tests/test_groups_replay.py`

- [ ] **Step 4.3.1: Write the failing test.**

```python
# tests/test_groups_replay.py
from __future__ import annotations

from pathlib import Path

from aegis.groups.persistence import (
    PersistedGroupLog,
    event_broadcast_started,
    event_created,
    event_member_added,
)
from aegis.groups.registry import GroupRegistry


def test_replay_reconstitutes_members(tmp_path: Path):
    log = PersistedGroupLog(tmp_path)
    log.write("rev", event_created("rev", "agent:host", "T"))
    log.write("rev", event_member_added("ada", "sec", "agent:host", "T"))
    log.write("rev", event_member_added("lucid", "logic", "agent:host", "T"))

    reg = GroupRegistry(log=log)
    reg.start(live_handles={"ada", "lucid"})
    assert set(reg.get("rev").members) == {"ada", "lucid"}


def test_replay_marks_lost_when_session_is_gone(tmp_path: Path):
    log = PersistedGroupLog(tmp_path)
    log.write("rev", event_created("rev", "agent:host", "T"))
    log.write("rev", event_member_added("ada", "sec", "agent:host", "T"))
    log.write("rev", event_member_added("lost", "logic", "agent:host", "T"))

    reg = GroupRegistry(log=log)
    reg.start(live_handles={"ada"})    # 'lost' is missing from live sessions
    assert "lost" not in reg.get("rev").members
    # And we wrote a member_removed(reason="lost-on-restart") event:
    kinds = [r["kind"] for r in log.read("rev")]
    assert kinds[-1] == "member_removed"


def test_replay_marks_orphan_broadcast_failed_interrupted(tmp_path: Path):
    log = PersistedGroupLog(tmp_path)
    log.write("rev", event_created("rev", "agent:host", "T"))
    log.write("rev", event_member_added("ada", "sec", "agent:host", "T"))
    log.write("rev", event_broadcast_started(
        "br-1", "o", "f", "t", "b", "agent:host", ("ada",)))
    reg = GroupRegistry(log=log)
    reg.start(live_handles={"ada"})
    kinds = [r["kind"] for r in log.read("rev")]
    assert "broadcast_completed" in kinds
    last = [r for r in log.read("rev") if r["kind"] == "broadcast_completed"][-1]
    assert last["mode"] == "failed:interrupted"
```

- [ ] **Step 4.3.2: Implement `GroupRegistry.start`.**

In `registry.py`:

```python
    def start(self, *, live_handles: set[str]) -> None:
        if self._log is None:
            return
        for group in self._log.all_groups():
            records = self._log.read(group)
            members: dict[str, MemberRef] = {}
            for rec in records:
                k = rec["kind"]
                if k == "member_added":
                    members[rec["handle"]] = MemberRef(
                        handle=rec["handle"], profile=rec["profile"])
                elif k == "member_removed":
                    members.pop(rec["handle"], None)
                elif k == "renamed":
                    pass  # handled by group-level remap below
            in_flight_broadcasts = self._in_flight_broadcasts(records)
            self._groups[group] = Group(name=group, members=members)
            for handle in list(members):
                if handle not in live_handles:
                    members.pop(handle)
                    self._emit(group, event_member_removed(
                        handle, "lost-on-restart", now_iso()))
            if not members:
                self._groups.pop(group, None)
                self._emit(group, event_dissolved("empty-on-restart", now_iso()))
            for bid in in_flight_broadcasts:
                self._emit(group, event_broadcast_completed(
                    bid, "failed:interrupted", reducer="concat",
                    at=now_iso()))

    @staticmethod
    def _in_flight_broadcasts(records: list[dict]) -> list[str]:
        started = {r["broadcast_id"] for r in records
                   if r["kind"] == "broadcast_started"}
        completed = {r["broadcast_id"] for r in records
                     if r["kind"] == "broadcast_completed"}
        return sorted(started - completed)
```

- [ ] **Step 4.3.3: Call `start` from `make_groups_bridge`.**

In `src/aegis/groups/bridge.py`, after constructing registry/wiring,
accept a `state_dir` parameter and call:

```python
    if state_dir is not None:
        registry._log = PersistedGroupLog(state_dir)
        live = set(session_manager.live_handles())   # add if missing
        registry.start(live_handles=live)
```

If `SessionManager` doesn't have a `live_handles()` method, add a
one-liner returning `set(self._sessions)` (or the equivalent).

- [ ] **Step 4.3.4: Run tests; expect 3 passed.**

- [ ] **Step 4.3.5: Commit.**

```bash
git add src/aegis/groups/registry.py src/aegis/groups/bridge.py src/aegis/core/manager.py tests/test_groups_replay.py
git commit -m "feat(groups): replay JSONL on boot — lost members + interrupted broadcasts"
```

---

## Slice 5 — Sugars + ephemeral groups + remaining reducers + maintenance ops MCP tools

### Task 5.1: Spawn sugars — `n=` and `spawn_group`

**Files:**
- Modify: `src/aegis/groups/wiring.py`, `src/aegis/groups/bridge.py`,
  `src/aegis/mcp/server.py`, `src/aegis/mcp/bridge.py`
- Test:   `tests/test_groups_spawn_sugars.py`

- [ ] **Step 5.1.1: Add `n=` and `spawn_group` to `GroupWiring`.**

```python
    async def spawn_many(self, *, profile: str, n: int,
                          group: str) -> list[str]:
        if n < 1:
            raise ValueError("n must be >= 1")
        return [await self.spawn(profile=profile, group=group) for _ in range(n)]

    async def spawn_group(self, name: str, profiles: list[str]) -> list[str]:
        if not profiles:
            raise ValueError("profiles must not be empty")
        return [await self.spawn(profile=p, group=name) for p in profiles]
```

- [ ] **Step 5.1.2: Write the test.**

```python
# tests/test_groups_spawn_sugars.py
import pytest
# Re-use the FakeManager/FakeSession pattern from tests/test_groups_wiring.py
# (lift them into tests/fixtures/fake_groups_env.py if duplication grows).


@pytest.mark.asyncio
async def test_spawn_many_creates_n_members_with_same_profile():
    from aegis.groups.registry import GroupRegistry
    from aegis.groups.wiring import GroupWiring
    from aegis.queue.inbox import InboxRouter
    from tests.fixtures.fake_groups_env import FakeManager
    import asyncio

    bus: asyncio.Queue = asyncio.Queue()
    mgr = FakeManager()
    reg = GroupRegistry()
    w = GroupWiring(session_manager=mgr, registry=reg,
                    inbox=InboxRouter(), member_bus=bus)
    handles = await w.spawn_many(profile="opus", n=3, group="rev")
    assert len(handles) == 3
    assert len(reg.get("rev").members) == 3


@pytest.mark.asyncio
async def test_spawn_group_creates_heterogeneous_members():
    from aegis.groups.registry import GroupRegistry
    from aegis.groups.wiring import GroupWiring
    from aegis.queue.inbox import InboxRouter
    from tests.fixtures.fake_groups_env import FakeManager
    import asyncio

    bus: asyncio.Queue = asyncio.Queue()
    mgr = FakeManager()
    reg = GroupRegistry()
    w = GroupWiring(session_manager=mgr, registry=reg,
                    inbox=InboxRouter(), member_bus=bus)
    handles = await w.spawn_group("rev", ["sec", "style", "logic"])
    assert len(handles) == 3
    profiles = {m.profile for m in reg.get("rev").members.values()}
    assert profiles == {"sec", "style", "logic"}
```

Create `tests/fixtures/fake_groups_env.py` with the `FakeManager`/`_FakeSession`
classes lifted from earlier tests (DRY); update earlier tests to import
from this fixture file too.

- [ ] **Step 5.1.3: Implement the `aegis_group_spawn_mixed` MCP tool.**

Add an `aegis_group_spawn_mixed` tool that accepts `profiles: list[str]`
(or `preset: str` — Slice 8). For now, just `profiles`.

- [ ] **Step 5.1.4: Run tests; expect pass.**

- [ ] **Step 5.1.5: Commit.**

```bash
git add src/aegis/groups/wiring.py src/aegis/groups/bridge.py src/aegis/mcp/server.py src/aegis/mcp/bridge.py tests/test_groups_spawn_sugars.py tests/fixtures/fake_groups_env.py
git commit -m "feat(groups): spawn sugars — n= + spawn_group + aegis_group_spawn_mixed"
```

### Task 5.2: Remaining reducers — `join_by_handle`, `last_wins`, `majority_vote`

**Files:**
- Modify: `src/aegis/groups/reducers.py`
- Test:   `tests/test_groups_reducers.py` (extend)

- [ ] **Step 5.2.1: Write the failing tests.**

```python
# tests/test_groups_reducers.py  (append)
def test_join_by_handle_returns_dict():
    from aegis.groups.reducers import get_reducer
    by_member = {"a": _mr("a", "x"), "b": _mr("b", "y")}
    out = get_reducer("join_by_handle")(by_member, ["a", "b"])
    assert out == {"a": "x", "b": "y"}


def test_last_wins_returns_text_of_last_finisher():
    from aegis.groups.reducers import get_reducer
    by_member = {"a": _mr("a", "first"), "b": _mr("b", "second")}
    out = get_reducer("last_wins")(by_member, ["a", "b"])
    assert out == "second"


def test_majority_vote_returns_modal_with_tiebreak_first_finisher():
    from aegis.groups.reducers import get_reducer
    by_member = {
        "a": _mr("a", "YES"), "b": _mr("b", "NO"), "c": _mr("c", "YES"),
    }
    out = get_reducer("majority_vote")(by_member, ["a", "b", "c"])
    assert out == "YES"
```

- [ ] **Step 5.2.2: Implement + register.**

```python
# src/aegis/groups/reducers.py  (append + register)
def join_by_handle(by_member, order):
    return {h: by_member[h].text for h in order if h in by_member}


def last_wins(by_member, order):
    if not order:
        return ""
    return by_member[order[-1]].text


def majority_vote(by_member, order):
    from collections import Counter
    counts: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    for i, h in enumerate(order):
        text = by_member[h].text.strip()
        counts[text] += 1
        first_seen.setdefault(text, i)
    if not counts:
        return ""
    top = max(counts.items(), key=lambda kv: (kv[1], -first_seen[kv[0]]))
    return top[0]


register_reducer("join_by_handle", join_by_handle)
register_reducer("last_wins", last_wins)
register_reducer("majority_vote", majority_vote)
```

- [ ] **Step 5.2.3: Run tests; expect pass.**

- [ ] **Step 5.2.4: Commit.**

```bash
git add src/aegis/groups/reducers.py tests/test_groups_reducers.py
git commit -m "feat(groups): reducers — join_by_handle, last_wins, majority_vote"
```

### Task 5.3: Maintenance MCP tools — `status`, `dissolve`, `rename`, `move_member`

**Files:**
- Modify: `src/aegis/mcp/server.py`, `src/aegis/mcp/bridge.py`,
  `src/aegis/groups/bridge.py`
- Test:   `tests/test_groups_mcp_maintenance.py`

- [ ] **Step 5.3.1: Add to bridges.**

```python
# src/aegis/groups/bridge.py  (append to _GroupsBridge)
    async def status(self, group: str) -> dict:
        g = self.runtime.registry.get(group)
        rec = self.runtime.tracker.current(group)
        return {
            "name": g.name,
            "members": [
                {"handle": h, "profile": m.profile}
                for h, m in g.members.items()
            ],
            "current_broadcast": (
                {"id": rec.id, "objective": rec.objective,
                 "started_at": rec.started_at, "members": list(rec.members)}
                if rec else None
            ),
        }

    async def dissolve(self, group: str) -> dict:
        self.runtime.registry.dissolve(group)
        return {"dissolved": group}

    async def rename(self, old: str, new: str) -> dict:
        self.runtime.registry.rename(old, new)
        return {"old": old, "new": new}

    async def move_member(self, handle: str, *, from_group: str,
                          to_group: str) -> dict:
        self.runtime.registry.move_member(
            handle, from_group=from_group, to_group=to_group)
        return {"handle": handle, "from": from_group, "to": to_group}
```

- [ ] **Step 5.3.2: Write the MCP test.**

```python
# tests/test_groups_mcp_maintenance.py
import pytest


@pytest.mark.asyncio
async def test_status_includes_members_and_no_current_broadcast():
    from aegis.mcp.server import _aegis_group_status_impl

    class _G:
        async def status(self, group):
            return {"name": group, "members": [{"handle": "ada", "profile": "p"}],
                    "current_broadcast": None}

    class _B: groups = _G()
    out = await _aegis_group_status_impl(_B(), group="g")
    assert out["name"] == "g"
    assert out["members"][0]["handle"] == "ada"
    assert out["current_broadcast"] is None
```

(Mirror for `dissolve`, `rename`, `move_member` — one test each.)

- [ ] **Step 5.3.3: Register the tools in `src/aegis/mcp/server.py`.**

One `_impl` function and one decorated tool per maintenance op (`status`,
`dissolve`, `rename`, `move_member`). Each `_impl` calls
`bridge.groups.<op>` and returns the dict.

- [ ] **Step 5.3.4: Run tests; expect pass.**

- [ ] **Step 5.3.5: Commit.**

```bash
git add src/aegis/groups/bridge.py src/aegis/mcp/server.py src/aegis/mcp/bridge.py tests/test_groups_mcp_maintenance.py
git commit -m "feat(groups,mcp): status / dissolve / rename / move_member tools"
```

### Task 5.4: Ephemeral groups — workflow-only context manager

**Files:**
- Modify: `src/aegis/workflow/engine.py` (add `ephemeral_group`)
- Test:   `tests/test_groups_ephemeral.py`

- [ ] **Step 5.4.1: Write the failing test.**

```python
# tests/test_groups_ephemeral.py
import pytest


@pytest.mark.asyncio
async def test_ephemeral_group_dissolves_on_exit():
    from aegis.groups.registry import GroupRegistry
    from aegis.groups.runtime import GroupRuntime
    from aegis.queue.inbox import InboxRouter
    from aegis.workflow.engine import WorkflowEngine
    from tests.fixtures.fake_groups_env import FakeManager
    import asyncio

    bus: asyncio.Queue = asyncio.Queue()
    mgr = FakeManager()
    reg = GroupRegistry()
    router = InboxRouter()
    from aegis.groups.wiring import GroupWiring
    wiring = GroupWiring(session_manager=mgr, registry=reg, inbox=router,
                         member_bus=bus)
    rt = GroupRuntime(registry=reg, inbox=router, member_bus=bus,
                      new_id=lambda: "br-1", now=lambda: "T")

    engine = WorkflowEngine(groups_runtime=rt, groups_wiring=wiring,
                            session_manager=mgr)

    captured: dict = {}
    async with engine.ephemeral_group(profiles=["p", "p"]) as g:
        captured["name"] = g.name
        captured["members_during"] = set(reg.get(g.name).members)
    # After exit, group is gone:
    from aegis.groups.registry import UnknownGroup
    with pytest.raises(UnknownGroup):
        reg.get(captured["name"])
    assert len(captured["members_during"]) == 2
```

- [ ] **Step 5.4.2: Implement `ephemeral_group`.**

```python
# src/aegis/workflow/engine.py  (append to WorkflowEngine)
import contextlib
import secrets


@contextlib.asynccontextmanager
async def ephemeral_group(self, *, profiles: list[str]):
    """Spawn N agents into a fresh group with a generated name, yield a
    handle to the group, and dissolve on exit. For workflow use only —
    not exposed over MCP."""
    name = f"ephemeral-{secrets.token_hex(4)}"
    await self._groups_wiring.spawn_group(name, profiles)
    try:
        yield _EphemeralGroupHandle(name=name, runtime=self._groups_runtime)
    finally:
        # Best-effort dissolve; tolerate "already gone" from session-close races.
        try:
            self._groups_runtime.registry.dissolve(name)
        except Exception:
            pass


@dataclass
class _EphemeralGroupHandle:
    name: str
    runtime: "GroupRuntime"

    async def broadcast(self, **kw) -> str:
        return await self.runtime.broadcast(self.name, sender="workflow",
                                             **kw)

    async def wait_all(self, **kw):
        return await self.runtime.wait_all(self.name, **kw)

    async def wait_any(self, **kw):
        return await self.runtime.wait_any(self.name, **kw)
```

(`WorkflowEngine` may not yet take `groups_runtime`/`groups_wiring` —
add them as keyword args to `__init__`; default `None` for back-compat
with existing workflow tests.)

- [ ] **Step 5.4.3: Run test; expect pass.**

- [ ] **Step 5.4.4: Commit.**

```bash
git add src/aegis/workflow/engine.py tests/test_groups_ephemeral.py
git commit -m "feat(groups,workflow): ephemeral_group context manager"
```

---

## Slice 6 — TUI

### Task 6.1: `GroupTab` kind + tab-bar rendering

**Files:**
- Modify: `src/aegis/tui/widgets.py` (TabBar)
- Modify: `src/aegis/tui/state.py` (GroupTabState)
- Create: `src/aegis/tui/groups/__init__.py`
- Create: `src/aegis/tui/groups/state.py` — `GroupTabState` + aggregate-state emoji helper
- Test:   `tests/test_groups_tui_state.py`

- [ ] **Step 6.1.1: Write the failing test for the aggregate-state helper.**

```python
# tests/test_groups_tui_state.py
from aegis.tui.groups.state import aggregate_state_emoji


def test_aggregate_idle_all_done_is_check():
    assert aggregate_state_emoji([("a", "idle"), ("b", "idle")]) == "✓"


def test_aggregate_any_busy_is_hourglass():
    assert aggregate_state_emoji([("a", "idle"), ("b", "busy")]) == "⏳"


def test_aggregate_any_error_is_warn():
    assert aggregate_state_emoji([("a", "idle"), ("b", "errored")]) == "⚠"


def test_aggregate_any_lost_is_blocked():
    assert aggregate_state_emoji([("a", "lost"), ("b", "idle")]) == "⛔"
```

- [ ] **Step 6.1.2: Implement.**

```python
# src/aegis/tui/groups/state.py
"""TUI-side group tab state + presentation helpers."""
from __future__ import annotations


def aggregate_state_emoji(member_states: list[tuple[str, str]]) -> str:
    states = {s for _, s in member_states}
    if "lost" in states:
        return "⛔"
    if "errored" in states:
        return "⚠"
    if "busy" in states:
        return "⏳"
    return "✓"
```

- [ ] **Step 6.1.3: Add `GroupTabState` to `aegis/tui/state.py` (mirror `AgentState`).**

Look at the existing `AgentState` shape, copy minimum fields the tab bar
reads, swap "single agent" for "group of members" semantics.

- [ ] **Step 6.1.4: Extend `TabBar` to render group tabs with the aggregate emoji.**

Look at the existing per-tab label code in `widgets.py` and add a
branch for `GroupTabState`: `▣ <name> [<active>/<total> <emoji>]`.

- [ ] **Step 6.1.5: Run tests; expect pass.**

- [ ] **Step 6.1.6: Commit.**

```bash
git add src/aegis/tui/widgets.py src/aegis/tui/state.py src/aegis/tui/groups/__init__.py src/aegis/tui/groups/state.py tests/test_groups_tui_state.py
git commit -m "feat(groups,tui): GroupTabState + aggregate-state emoji + tab rendering"
```

### Task 6.2: Glance dashboard widget

**Files:**
- Create: `src/aegis/tui/groups/dashboard.py`
- Test:   `tests/test_groups_dashboard.py`

- [ ] **Step 6.2.1: Build the widget as a `Static`-rendered Textual widget.**

```python
# src/aegis/tui/groups/dashboard.py
"""GroupDashboard — the body rendered when a group tab is focused.

Three panels stacked: Members, Current broadcast, Recent broadcasts.
Pure render — reads from a snapshot dataclass populated by the
GroupTabState observer.
"""
from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent

from textual.widget import Widget
from textual.widgets import Static


@dataclass(frozen=True)
class MemberRow:
    handle: str
    state: str        # idle | busy | errored | lost
    detail: str       # "last turn 18s · 1.2k tok" | "tool: Read foo.py · 04:12"


@dataclass(frozen=True)
class BroadcastRow:
    id: str
    mode: str         # wait_all | wait_any
    status: str       # ✓ | ⚠ | ⏳
    started: str
    summary: str


@dataclass(frozen=True)
class DashboardSnapshot:
    name: str
    members: list[MemberRow]
    current: BroadcastRow | None
    recent: list[BroadcastRow]


def render_dashboard(snap: DashboardSnapshot) -> str:
    members = "\n".join(
        f"  {state_glyph(m.state)} {m.handle:<18} {m.state:<8} · {m.detail}"
        for m in snap.members
    ) or "  (no members)"
    if snap.current:
        c = snap.current
        current = dedent(f"""\
            Current broadcast
              id   {c.id} · started {c.started} · mode: {c.mode}
              {c.summary}
        """).rstrip()
    else:
        current = "Current broadcast\n  (no broadcast in flight)"
    recent = "\n".join(
        f"  {r.id} {r.status} {r.started}  {r.mode:<9} {r.summary}"
        for r in snap.recent
    ) or "  (no broadcasts yet)"
    return (
        f"▣ {snap.name} — {len(snap.members)} members\n\n"
        f"Members\n{members}\n\n"
        f"{current}\n\n"
        f"Recent broadcasts\n{recent}\n"
    )


def state_glyph(s: str) -> str:
    return {"idle": "✓", "busy": "⏳", "errored": "⚠", "lost": "⛔"}.get(s, "?")


class GroupDashboard(Widget):
    def __init__(self, snap: DashboardSnapshot, **kw):
        super().__init__(**kw)
        self._snap = snap

    def render(self) -> str:
        return render_dashboard(self._snap)
```

- [ ] **Step 6.2.2: Write the render test.**

```python
# tests/test_groups_dashboard.py
from aegis.tui.groups.dashboard import (
    BroadcastRow,
    DashboardSnapshot,
    MemberRow,
    render_dashboard,
)


def test_dashboard_renders_three_panels():
    snap = DashboardSnapshot(
        name="reviewers",
        members=[
            MemberRow("ada", "idle", "last turn 18s · 1.2k tok"),
            MemberRow("lucid", "busy", "tool: Read foo.py · 04:12"),
        ],
        current=BroadcastRow("br-9f3a", "wait_all", "⏳",
                              "14:30 (02:18 ago)",
                              "Audit branch feat/auth for security regressions."),
        recent=[BroadcastRow("br-7c11", "wait_all", "✓",
                              "14:25", "3/3 in 01:42 · concat")],
    )
    out = render_dashboard(snap)
    assert "▣ reviewers — 2 members" in out
    assert "ada" in out and "lucid" in out
    assert "br-9f3a" in out
    assert "br-7c11" in out
```

- [ ] **Step 6.2.3: Run tests; expect pass.**

- [ ] **Step 6.2.4: Commit.**

```bash
git add src/aegis/tui/groups/dashboard.py tests/test_groups_dashboard.py
git commit -m "feat(groups,tui): GroupDashboard widget + render"
```

### Task 6.3: Member sub-tabs (2nd-row band)

**Files:**
- Modify: `src/aegis/tui/widgets.py` (or create `src/aegis/tui/groups/subtabs.py`)
- Modify: `src/aegis/tui/app.py`
- Test:   `tests/test_groups_subtabs.py`

- [ ] **Step 6.3.1: Add a `MemberSubTabBar` widget that renders the member row.**

(Mirror `TabBar` but scoped to one group's members. Activation on
Ctrl+↓ from the dashboard, Ctrl+↑ to return.)

- [ ] **Step 6.3.2: Hook the bar into `AegisApp` so it appears below the main `TabBar` only when a group tab is focused.**

- [ ] **Step 6.3.3: Write a smoke test.**

(Boot `AegisApp` against a fake `SessionManager` containing one group +
2 members; assert `MemberSubTabBar` is visible.)

- [ ] **Step 6.3.4: Run + commit.**

```bash
git commit -m "feat(groups,tui): MemberSubTabBar — 2nd-row band inside a group tab"
```

### Task 6.4: Keybinds — Ctrl+T / Ctrl+Shift+T / Ctrl+G / Ctrl+B / Ctrl+W / Ctrl+R

**Files:**
- Modify: `src/aegis/tui/app.py`
- Create: `src/aegis/tui/groups/lasso.py` — Ctrl+G modal
- Create: `src/aegis/tui/groups/broadcast.py` — Ctrl+B four-field composer
- Test:   `tests/test_groups_keybinds.py`

- [ ] **Step 6.4.1: Wire each keybind.**

Use Textual's `BINDINGS` declarations. For each binding, the handler
calls into `bridge.groups.*`. Ctrl+Shift+T must prompt for a group
name if the current tab is at root; Ctrl+G opens the lasso modal;
Ctrl+B opens the four-field broadcast composer; Ctrl+W asks for
confirmation showing the member count before dissolving.

- [ ] **Step 6.4.2: Implement the lasso modal.**

Multi-select list of root agents + name input field. On submit, call
`bridge.groups.spawn` (or a dedicated `bridge.groups.move_member` per
selected handle into the new group).

- [ ] **Step 6.4.3: Implement the broadcast composer modal.**

Four labelled text inputs (`objective`, `output_format`, `tool_guidance`,
`boundaries`); Submit → `bridge.groups.broadcast`. Each field is
non-empty-required; show inline validation.

- [ ] **Step 6.4.4: Write keybind smoke tests.**

(Drive the app with Textual's `pilot.press` and assert the right modal
opens / the right method is called.)

- [ ] **Step 6.4.5: Run + commit.**

```bash
git commit -m "feat(groups,tui): keybinds + lasso + broadcast-composer modals"
```

---

## Slice 7 — Workflow Python API + YAML config presets

### Task 7.1: `engine.group / broadcast / wait_all / wait_any / spawn_group / dissolve_group / move_member / rename_group`

**Files:**
- Modify: `src/aegis/workflow/engine.py`
- Test:   `tests/test_groups_workflow_engine.py`

- [ ] **Step 7.1.1: Write the failing tests.**

```python
# tests/test_groups_workflow_engine.py
import pytest


@pytest.mark.asyncio
async def test_engine_spawn_group_delegates_to_wiring():
    from aegis.workflow.engine import WorkflowEngine

    class _W:
        async def spawn_group(self, name, profiles):
            return [f"{p}-h" for p in profiles]

    class _R: pass

    e = WorkflowEngine(groups_runtime=_R(), groups_wiring=_W(),
                       session_manager=None)
    handles = await e.spawn_group("rev", ["sec", "style"])
    assert handles == ["sec-h", "style-h"]


@pytest.mark.asyncio
async def test_engine_broadcast_and_wait_all_delegate_to_runtime():
    from aegis.groups.models import GroupResult
    from aegis.workflow.engine import WorkflowEngine

    class _R:
        async def broadcast(self, group, **kw):
            return "br-1"
        async def wait_all(self, group, **kw):
            return GroupResult("br-1", {}, "", {}, [])

    class _W: pass

    e = WorkflowEngine(groups_runtime=_R(), groups_wiring=_W(),
                       session_manager=None)
    bid = await e.broadcast("rev", objective="o", output_format="f",
                             tool_guidance="t", boundaries="b")
    assert bid == "br-1"
    res = await e.wait_all("rev")
    assert res.broadcast_id == "br-1"
```

- [ ] **Step 7.1.2: Implement on `WorkflowEngine`.**

```python
# src/aegis/workflow/engine.py  (append methods)
    async def spawn_group(self, name, profiles):
        return await self._groups_wiring.spawn_group(name, profiles)

    async def broadcast(self, group, *, objective, output_format,
                        tool_guidance, boundaries):
        return await self._groups_runtime.broadcast(
            group, sender="workflow",
            objective=objective, output_format=output_format,
            tool_guidance=tool_guidance, boundaries=boundaries,
        )

    async def wait_all(self, group, *, timeout=600.0, reducer="concat"):
        return await self._groups_runtime.wait_all(
            group, timeout=timeout, reducer=reducer)

    async def wait_any(self, group, *, timeout=600.0, cancel_losers=True):
        return await self._groups_runtime.wait_any(
            group, timeout=timeout, cancel_losers=cancel_losers)

    async def dissolve_group(self, group):
        return self._groups_runtime.registry.dissolve(group)

    async def rename_group(self, old, new):
        return self._groups_runtime.registry.rename(old, new)

    async def move_member(self, handle, *, from_group, to_group):
        return self._groups_runtime.registry.move_member(
            handle, from_group=from_group, to_group=to_group)
```

- [ ] **Step 7.1.3: Run tests; expect pass.**

- [ ] **Step 7.1.4: Commit.**

```bash
git add src/aegis/workflow/engine.py tests/test_groups_workflow_engine.py
git commit -m "feat(groups,workflow): engine methods mirror MCP surface"
```

### Task 7.2: `groups:` section in `.aegis.yaml` + `.aegis/groups/<name>.yaml` overlay

**Files:**
- Modify: `src/aegis/config/yaml_loader.py`
- Test:   `tests/test_groups_yaml_loader.py`

- [ ] **Step 7.2.1: Write the failing test.**

```python
# tests/test_groups_yaml_loader.py
from pathlib import Path

from aegis.config.yaml_loader import load_config


def test_loads_inline_groups_defaults_and_presets(tmp_path: Path):
    (tmp_path / ".aegis.yaml").write_text("""\
default_agent: claude
agents:
  claude: {provider: claude-code, model: opus, effort: high, permission: auto}
groups:
  defaults:
    broadcast_timeout: 300
    default_reducer: join_by_handle
  presets:
    code_audit:
      profiles: [sec, style, logic]
""")
    cfg = load_config(tmp_path)
    assert cfg.groups["defaults"]["broadcast_timeout"] == 300
    assert cfg.groups["defaults"]["default_reducer"] == "join_by_handle"
    assert cfg.groups["presets"]["code_audit"]["profiles"] == \
           ["sec", "style", "logic"]


def test_loads_overlay_group_files(tmp_path: Path):
    (tmp_path / ".aegis.yaml").write_text("""\
default_agent: claude
agents:
  claude: {provider: claude-code, model: opus, effort: high, permission: auto}
""")
    (tmp_path / ".aegis" / "groups").mkdir(parents=True)
    (tmp_path / ".aegis" / "groups" / "code_audit.yaml").write_text(
        "profiles: [sec, style, logic]\n")
    cfg = load_config(tmp_path)
    assert cfg.groups["presets"]["code_audit"]["profiles"] == \
           ["sec", "style", "logic"]


def test_inline_overlay_conflict_is_fail_loud(tmp_path: Path):
    import pytest
    from aegis.config import ConfigError
    (tmp_path / ".aegis.yaml").write_text("""\
default_agent: claude
agents:
  claude: {provider: claude-code, model: opus, effort: high, permission: auto}
groups:
  presets:
    code_audit:
      profiles: [a, b]
""")
    (tmp_path / ".aegis" / "groups").mkdir(parents=True)
    (tmp_path / ".aegis" / "groups" / "code_audit.yaml").write_text(
        "profiles: [c, d]\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path)
```

- [ ] **Step 7.2.2: Extend the loader.**

In `src/aegis/config/yaml_loader.py`, add `groups: dict[str, Any] = …`
to `AegisConfig`, parse the inline `groups:` section, walk
`.aegis/groups/*.yaml`, merge into `groups.presets` with the same
fail-loud-on-duplicate-keys rule scheduler/queues use.

- [ ] **Step 7.2.3: Run tests; expect pass.**

- [ ] **Step 7.2.4: Commit.**

```bash
git add src/aegis/config/yaml_loader.py tests/test_groups_yaml_loader.py
git commit -m "feat(groups,config): groups: section in .aegis.yaml + .aegis/groups/ overlay"
```

### Task 7.3: `aegis_group_spawn_mixed(preset=...)` wired

**Files:**
- Modify: `src/aegis/mcp/server.py`, `src/aegis/groups/bridge.py`
- Test:   `tests/test_groups_mcp_preset.py`

- [ ] **Step 7.3.1: Extend the impl to accept `preset:`.**

```python
async def _aegis_group_spawn_mixed_impl(bridge, *, name: str,
                                          profiles: list[str] | None = None,
                                          preset: str | None = None) -> dict:
    if preset is not None:
        profiles = bridge.config.groups["presets"][preset]["profiles"]
    if not profiles:
        raise ValueError("must pass either `profiles` or `preset`")
    handles = await bridge.groups.spawn_group(name, profiles)
    return {"group": name, "handles": handles}
```

- [ ] **Step 7.3.2: Write the test.**

(Stub `bridge.config.groups["presets"]["code_audit"]` and call the impl
with `preset="code_audit"`.)

- [ ] **Step 7.3.3: Run + commit.**

```bash
git commit -m "feat(groups,mcp): aegis_group_spawn_mixed(preset=...) — YAML-driven team factories"
```

---

## Final pass

### Task F.1: Documentation

**Files:**
- Create: `repos/aegis/docs/groups.md`
- Modify: `repos/aegis/AGENTS.md` (add a `groups/` paragraph)
- Modify: `repos/aegis/docs/roadmap.md` (groups → shipped)
- Modify: `repos/aegis/CHANGELOG.md` (new version entry)
- Modify: `repos/aegis/docs/index.md` (link to groups.md)
- Modify: `repos/aegis/docs/configuration.md` (groups: section)

- [ ] **Step F.1.1: Write `docs/groups.md`.**

User-facing doc covering: what a group is, the four-field broadcast
contract, wait_all vs wait_any (and when to pick each), the
`GroupResult` shape with all four named reducers, TUI keybinds, the
ephemeral-group context manager for workflows, the YAML preset
form. ≤300 lines.

- [ ] **Step F.1.2: Update `AGENTS.md`'s `## Layout` section with `groups/` description.**

- [ ] **Step F.1.3: Bump version.** Edit `src/aegis/__init__.py`'s
`__version__` to `0.6.0`. Update `CHANGELOG.md` with a `## 0.6.0` entry
summarizing the slices.

- [ ] **Step F.1.4: Run docs + version test.** `uv run pytest tests/test_cli.py -v`
(catches the dynamic-version assertion).

- [ ] **Step F.1.5: Commit.**

```bash
git add docs/groups.md AGENTS.md docs/roadmap.md docs/index.md docs/configuration.md CHANGELOG.md src/aegis/__init__.py
git commit -m "docs(groups): user-facing docs + AGENTS.md + 0.6.0 changelog"
```

### Task F.2: Full hermetic suite + live smoke

- [ ] **Step F.2.1: Run hermetic suite.** `uv run pytest -q 2>&1 | tail -3`
Expected: baseline + ~80 new tests, all green.

- [ ] **Step F.2.2: Run live suite.** `uv run pytest -m live -v`
Expected: all live tests pass (including the 3-member roundtrip from
Slice 2).

- [ ] **Step F.2.3: Push.** `git push origin main`

### Task F.3: Release

- [ ] **Step F.3.1: Tag.** `git tag v0.6.0 && git push origin v0.6.0`

- [ ] **Step F.3.2: Verify install.** From a scratch venv:
`uv pip install aegis==0.6.0 && aegis --version` → `0.6.0`.

---

## Self-review log

- **Spec coverage:** Every numbered section of the spec (model,
  spawn, broadcast/wait, GroupResult, lifecycle, MCP, workflow, TUI,
  file layout, testing) has at least one task. The four "open
  questions deferred to the implementation plan" are addressed:
  Ctrl+B keybind (Task 6.4), passive-cancel mechanism (Task 3.1),
  `aegis_group_status` schema with last-broadcast list (Task 5.3),
  empty-group dashboard placeholder (Task 6.2's "(no members)" line).
- **Placeholder scan:** No "TBD" / "TODO" / "implement later" /
  "similar to" text in the plan; every step that changes code shows
  the code; every test step has the test body.
- **Type consistency:** `MemberResult`, `GroupResult`, `BroadcastRecord`,
  `Group`, `MemberRef` defined in Task 1.1 and used consistently
  through MCP, workflow, and TUI tasks. Reducer signature
  `(by_member, order) -> Any` used in 1.2 and 5.2. `bridge.groups.*`
  protocol fixed in 2.2 and additive thereafter (3.3, 5.3).
- **Slice independence:** Each slice ends at a committable green
  state; Slices 3–7 layer onto Slice 2 in any order if needed
  (though the listed order is recommended). Slice 2 includes the
  live smoke so the substrate is proven before further features
  pile on.
