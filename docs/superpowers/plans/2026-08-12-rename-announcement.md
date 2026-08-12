# Rename Announcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tell an agent when the operator renames it, so it stops passing a
dead handle as `from_handle`.

**Architecture:** The session holds a pending notice; `_run_turn` — the one
path every prompted turn goes through — prepends it to the next turn's text
and clears it. Nothing wakes an idle agent, so a rename costs nothing until
the agent next has a reason to think. Both `rename_handle` implementations
raise the notice, and the operator call sites declare themselves with a
`by="operator"` keyword.

**Tech Stack:** Python 3.13, asyncio, pytest + pytest-asyncio, Textual (TUI
tests via `app.run_test()`). Package manager is `uv` — run tests with
`uv run pytest`, never bare `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-12-rename-announcement-design.md`

## Global Constraints

- Work directly on `main`. This repo does not use feature branches.
- Code, comments, identifiers and commit messages in English.
- Never `git add -A` / `git add .`. Stage the explicit paths named in each
  step — this checkout is shared with other live sessions.
- Run the gate as its own tool call. Never pipe pytest into `tail`/`head`
  and never append `; echo rc=$?` — both hand the shell a 0 and turn a red
  gate green.
- The full suite has **6 known pre-existing failures**, all in `*_live.py`
  (4 gemini/opencode, `test_scheduler_live`, `test_skill_system_live`).
  They are unrelated to this work. A run is green at `6 failed, N passed`.
- Do not restart the running `aegis` process to test. It is Alex's live
  session set.

---

### Task 1: The notice, and how it rides the next turn

Everything session-local: a sender tag, a place to hold the notice, and the
hook that delivers it. Self-contained and testable with no rename involved.

**Files:**
- Modify: `src/aegis/queue/schema.py` (add `sender_substrate`, after
  `sender_reminder` at line 43)
- Modify: `src/aegis/queue/__init__.py:23-27` and `:53-57` (import + `__all__`)
- Modify: `src/aegis/core/session.py:116` (field), `:436` (`_run_turn` hook),
  and a new method beside `add_reminder` at `:382`
- Test: `tests/test_rename_notice.py` (create)

**Interfaces:**
- Consumes: `InboxMessage`, `now_iso`, `_render_batch`, `_emit_dispatch` —
  all already in `core/session.py`'s imports.
- Produces:
  - `sender_substrate() -> str` returning `"substrate"`
  - `AgentSession.note_rename(old: str, new: str, *, by: str) -> None`
  - `AgentSession._pending_notices: list[InboxMessage]`

- [x] **Step 1: Write the failing tests**

Create `tests/test_rename_notice.py`:

```python
"""A renamed agent has to be told, and it must cost nothing at rest."""
from __future__ import annotations

import asyncio

import pytest

from aegis.core.session import AgentSession
from aegis.events import Result
from aegis.tui.state import AgentState


class FakeHarness:
    """Records what text each turn actually sent to the harness — the
    substrate this feature is about, rather than the session's internals."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def start(self) -> None: ...
    async def close(self) -> None: ...

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def events(self):
        yield Result(duration_ms=1, is_error=False, usage=None)


def _session() -> tuple[AgentSession, FakeHarness]:
    h = FakeHarness()
    return AgentSession(h, None, "default", "old-name"), h


@pytest.mark.asyncio
async def test_notice_rides_the_next_turn():
    s, h = _session()
    s.note_rename("old-name", "new-name", by="operator")

    await s.send("hello")
    await s._task

    assert len(h.sent) == 1
    text = h.sent[0]
    assert "old-name" in text and "new-name" in text
    assert "hello" in text, "the operator's own message must survive"
    assert text.index("new-name") < text.index("hello"), (
        "the notice must arrive BEFORE the agent acts, not after")


@pytest.mark.asyncio
async def test_notice_does_not_start_a_turn():
    """The property that matters most. Renaming an idle agent must not
    wake it — this is what a later 'simplification' back into deliver()
    would break."""
    s, h = _session()
    s.note_rename("old-name", "new-name", by="operator")

    assert s.state is AgentState.ready
    assert h.sent == [], "a rename must not bill an LLM turn"


@pytest.mark.asyncio
async def test_notice_fires_exactly_once():
    s, h = _session()
    s.note_rename("old-name", "new-name", by="operator")

    await s.send("first")
    await s._task
    await s.send("second")
    await s._task

    assert "new-name" in h.sent[0]
    assert "new-name" not in h.sent[1], "the notice must not repeat"


@pytest.mark.asyncio
async def test_notice_names_the_consequence():
    """'You were renamed' invites a shrug. The text has to say the old
    handle no longer routes, or an agent notes it and moves on."""
    s, h = _session()
    s.note_rename("old-name", "new-name", by="operator")
    await s.send("go")
    await s._task

    text = h.sent[0]
    assert "from_handle" in text
    assert "operator" in text


@pytest.mark.asyncio
async def test_rename_mid_turn_lands_on_the_next_turn():
    """A rename while a turn is in flight must not alter that turn's text.
    The harness blocks so the ordering is deterministic rather than a race
    against how fast the fake yields its Result."""
    released = asyncio.Event()

    class BlockingHarness(FakeHarness):
        async def send(self, text: str) -> None:
            self.sent.append(text)
            await released.wait()

    h = BlockingHarness()
    s = AgentSession(h, None, "default", "old-name")

    await s.send("first")
    for _ in range(1000):          # let the turn reach the harness
        if h.sent:
            break
        await asyncio.sleep(0)
    assert h.sent == ["first"], "the turn's text is fixed before the rename"

    s.note_rename("old-name", "new-name", by="operator")
    released.set()
    await s._task
    assert "new-name" not in h.sent[0], "a running turn must not be rewritten"

    await s.send("second")
    await s._task
    assert "new-name" in h.sent[1], "the notice rides the turn after"


@pytest.mark.asyncio
async def test_notice_is_visible_to_the_operator():
    """It has to reach the transcript too, or 'why did it use the old
    name' is undebuggable."""
    s, h = _session()
    seen: list[str] = []
    s.add_dispatch_observer(lambda _s, batch: seen.extend(
        m.sender for m in batch))

    s.note_rename("old-name", "new-name", by="operator")
    await s.send("go")
    await s._task

    assert "substrate" in seen
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_rename_notice.py -p no:randomly -q`
Expected: FAIL — `AttributeError: 'AgentSession' object has no attribute 'note_rename'`

(`add_dispatch_observer` is real — `core/session.py:204` — so the only
missing name should be `note_rename`.)

- [x] **Step 3: Add the sender tag**

In `src/aegis/queue/schema.py`, after `sender_reminder` (line 43):

```python
def sender_substrate() -> str:
    """Sender tag for a notice the substrate itself raises about a session
    — a change to the session's own identity or wiring that it could not
    otherwise observe. Distinct from `reminder` (the agent left it for
    itself) and from `agent:<handle>` (a peer sent it)."""
    return "substrate"
```

In `src/aegis/queue/__init__.py`, add `sender_substrate` to both the import
block (lines 23-27, alphabetical: after `sender_reminder`) and `__all__`
(lines 53-57, same position).

- [x] **Step 4: Hold the notice on the session**

In `src/aegis/core/session.py`, beside `self._reminders` (line 116):

```python
        # Notices the substrate raised about this session itself (a rename
        # it could not otherwise observe). Unlike _reminders these never
        # justify a turn of their own — they ride the next one. See
        # docs/superpowers/specs/2026-08-12-rename-announcement-design.md.
        self._pending_notices: list[InboxMessage] = []
```

Add the method next to `add_reminder` (line 382), importing
`sender_substrate` alongside the existing `sender_*` imports at the top:

```python
    def note_rename(self, old: str, new: str, *, by: str) -> None:
        """Tell this session it was renamed by someone else.

        Deliberately does NOT wake it. An idle agent cannot act on a stale
        handle, so the notice waits for the next turn — which is both free
        and early enough. `by == "agent"` is silent: an agent that renamed
        itself already has the return value, and the shared MCP port cannot
        tell a self-rename from a peer rename anyway.
        """
        if by != "operator":
            return
        self._pending_notices.append(InboxMessage(
            sender=sender_substrate(),
            timestamp=now_iso(),
            body=(
                f"You were renamed by the operator: `{old}` → `{new}`.\n\n"
                f"Use `{new}` as your handle from now on — as `from_handle` "
                f"on aegis_monitor / aegis_enqueue / aegis_remind / "
                f"aegis_handoff, and when you tell a peer where to reach "
                f"you. The old handle no longer routes: anything addressed "
                f"to it is delivered to nobody."
            ),
        ))
```

- [x] **Step 5: Deliver it at the top of `_run_turn`**

In `src/aegis/core/session.py`, immediately after the docstring of
`_run_turn` (line 437) and before `self._unsolicited = False`:

```python
        # Substrate notices ride the turn rather than starting one. This is
        # the unified path — a typed message, an inbox batch, a monitor
        # callback, a loop tick and a reminder all arrive here — so the
        # notice reaches the agent whatever woke it, and before it acts.
        # (_drain_unsolicited_turn bypasses this on purpose: that drain is
        # the harness talking to itself, not the agent acting on identity.)
        if self._pending_notices:
            notices = self._pending_notices
            self._pending_notices = []
            self._emit_dispatch(notices)
            text = f"{_render_batch(notices)}\n\n{text}"
```

- [x] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_rename_notice.py -p no:randomly -q`
Expected: PASS (6 passed)

- [x] **Step 7: Prove the gate can fail**

Temporarily change `if by != "operator":` to `if by != "nobody":` and re-run
Step 6. Expected: `test_notice_rides_the_next_turn` FAILS. Revert the change
and re-run to confirm green. A test that cannot fail is worth less than none.

- [x] **Step 8: Run the neighbouring suites**

Run: `uv run pytest tests/test_core_session.py tests/test_reminder.py tests/test_loop.py tests/test_monitor_manager.py -p no:randomly -q`
Expected: PASS, no failures.

- [x] **Step 9: Commit**

```bash
git add src/aegis/queue/schema.py src/aegis/queue/__init__.py src/aegis/core/session.py tests/test_rename_notice.py
git commit -m "feat(session): a substrate notice that rides the next turn

An agent cannot see the operator rename it, so the handle it believes is
its own goes stale and it keeps passing that name as from_handle.

note_rename() holds a notice; _run_turn prepends it to the next turn's
text and clears it. It never wakes an idle session: a rename costs
nothing until the agent next has a reason to think, which is the only
moment it could act on a dead handle."
```

---

### Task 2: Raise the notice from both rename paths

Task 1 built the mechanism; nothing calls it yet. This wires it, and the
work is mostly about not missing a call site — this repo shipped a rename
defect today (`fb262d7`) that lived in exactly one of two paths.

Note the asymmetry: there are **three operator call sites but only two
edits**, because the TUI and the web client share one `_rename` command
function. Verify that before trusting it:
`grep -rn "CommandContext(" src/aegis/` shows `tui/pane.py` and
`web/wssession.py` both constructing it for the same dispatcher.

**Files:**
- Modify: `src/aegis/core/manager.py:488` (signature + call)
- Modify: `src/aegis/tui/app.py:2012` (signature + call)
- Modify: `src/aegis/commands/builtins/session_ctl.py:48` (pass `by`)
- Modify: `src/aegis/web/wssession.py:306` (pass `by`)
- Modify: `tests/test_slash_commands.py:76` (fake bridge must accept `by`)
- Test: `tests/test_rename_notice.py` (extend — `SessionManager` + the
  slash command)
- Test: `tests/test_tui.py` (extend, beside line 269 — `AegisApp`, which
  needs the Textual pilot and so cannot live in the file above)

**Interfaces:**
- Consumes: `AgentSession.note_rename(old, new, *, by)` from Task 1.
- Produces: `rename_handle(old, new, title=None, *, by: str = "agent")` on
  both `SessionManager` and `AegisApp`.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_rename_notice.py`:

```python
from aegis.core.manager import SessionManager
from aegis.queue.inbox import InboxRouter


class _Harness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _mgr() -> SessionManager:
    return SessionManager(
        {"default": object()}, "default",
        make_session=lambda profile, url, handle: _Harness(),
        mcp=None, inbox=InboxRouter(),
    )


@pytest.mark.asyncio
async def test_manager_rename_by_operator_notices():
    m = _mgr()
    s = m._sync_spawn("default", handle="old-name")
    await m.rename_handle("old-name", "new-name", by="operator")
    assert len(s._pending_notices) == 1
    assert "new-name" in s._pending_notices[0].body


@pytest.mark.asyncio
async def test_manager_rename_by_agent_is_silent():
    """A self-rename already returned {ok, old, new} to the caller, and a
    peer rename is indistinguishable from it over the shared MCP port."""
    m = _mgr()
    s = m._sync_spawn("default", handle="old-name")
    await m.rename_handle("old-name", "new-name")
    assert s._pending_notices == []


@pytest.mark.asyncio
async def test_manager_rename_default_is_silent():
    """Defaulting to `agent` means a call site that forgets `by=` produces
    a missing notice, never a false one."""
    m = _mgr()
    s = m._sync_spawn("default", handle="old-name")
    await m.rename_handle("old-name", "new-name", by="agent")
    assert s._pending_notices == []


@pytest.mark.asyncio
async def test_slash_rename_declares_the_operator():
    """The /rename command is the TUI's and the web client's shared path,
    so this one assertion covers both frontends."""
    from aegis.commands.builtins.session_ctl import _rename

    seen = {}

    class Bridge:
        async def rename_handle(self, old, new, title=None, *, by="agent"):
            seen["by"] = by
            return {"ok": True, "old": old, "new": new}

    class Ctx:
        bridge = Bridge()
        handle = "old-name"

    await _rename(Ctx(), {"new": "new-name"})
    assert seen["by"] == "operator"
```

- [x] **Step 2: Write the failing test for the OTHER implementation**

`AegisApp.rename_handle` is a second, independent implementation, and it
needs the Textual pilot — so this one test goes in `tests/test_tui.py`,
beside `test_monitor_strip_follows_a_renamed_session` (line 269), reusing
that file's existing `_app()` helper. Pinning both implementations is the
point: they drifted apart today in `fb262d7`, where only one of them
migrated the monitor and reminder planes.

```python
@pytest.mark.asyncio
async def test_app_rename_by_operator_notices_the_session():
    """The TUI's rename_handle is a second implementation of the same
    contract. Both must raise the notice, or the frontends disagree."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        old = pane.handle
        await app.rename_handle(old, "brand-new-name", by="operator")
        await pilot.pause()
        bodies = [m.body for m in pane._core._pending_notices]
        assert len(bodies) == 1
        assert "brand-new-name" in bodies[0]


@pytest.mark.asyncio
async def test_app_rename_by_agent_is_silent():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await app.rename_handle(pane.handle, "brand-new-name")
        await pilot.pause()
        assert pane._core._pending_notices == []
```

- [x] **Step 3: Run both files to verify they fail**

Run: `uv run pytest tests/test_rename_notice.py tests/test_tui.py -p no:randomly -q`
Expected: the four new tests in `test_rename_notice.py` and the two in
`test_tui.py` FAIL — `rename_handle() got an unexpected keyword argument
'by'`. Every other test in `test_tui.py` still passes.

- [x] **Step 4: Thread `by` through `SessionManager.rename_handle`**

In `src/aegis/core/manager.py`, change the signature at line 488:

```python
    async def rename_handle(self, old: str, new: str,
                            title: str | None = None, *,
                            by: str = "agent") -> dict:
```

Then, immediately after the monitor/reminder plane loop added in `fb262d7`
(and before the `if title is not None:` block), add:

```python
        # Someone other than the session changed its identity. It cannot
        # observe that on its own, so the substrate says so.
        session.note_rename(old, new, by=by)
```

Add one line to the docstring's `title` paragraph:

```
        ``by`` names who renamed it: ``"operator"`` announces the change to
        the session, anything else is silent. See the spec at
        docs/superpowers/specs/2026-08-12-rename-announcement-design.md.
```

- [x] **Step 5: Thread `by` through `AegisApp.rename_handle`**

In `src/aegis/tui/app.py`, change the signature at line 2012 to match
exactly:

```python
    async def rename_handle(self, old: str, new: str,
                            title: str | None = None, *,
                            by: str = "agent") -> dict:
```

After `self.repo_tracker.rename(old, new)` (line 2056) add:

```python
        pane._core.note_rename(old, new, by=by)
```

- [x] **Step 6: Declare the operator at both call sites**

In `src/aegis/commands/builtins/session_ctl.py`, line 48:

```python
    res = await ctx.bridge.rename_handle(ctx.handle, new, by="operator")
```

In `src/aegis/web/wssession.py`, line 306:

```python
            return await self._m.rename_handle(
                params["old"], params["new"], params.get("title"),
                by="operator")
```

- [x] **Step 7: Update the slash-command test's fake bridge**

`tests/test_slash_commands.py:76` defines a fake whose signature must now
accept the keyword, or every slash-command test fails:

```python
    async def rename_handle(self, old, new, title=None, *, by="agent"):
        self.renamed.append((old, new))
        return {"old": old, "new": new}
```

- [x] **Step 8: Run to verify they pass**

Run: `uv run pytest tests/test_rename_notice.py tests/test_tui.py -p no:randomly -q`
Expected: PASS — 10 in `test_rename_notice.py` (6 from Task 1 + 4 here) and
`test_tui.py` fully green including the 2 added in Step 2.

- [x] **Step 9: Prove no call site was missed**

Run this and read it — every `rename_handle` definition must carry `by`, and
every operator-initiated call must pass it:

```bash
grep -rn "rename_handle" src/aegis/ | grep -v "\.pyc"
```

Expected: `core/manager.py` and `tui/app.py` definitions both show `by`;
`commands/builtins/session_ctl.py` and `web/wssession.py` both pass
`by="operator"`; `mcp/server.py` and `mcp/bridge.py` pass nothing (correct —
an MCP rename stays silent); `tui/remote_manager.py` forwards over RPC and
needs no change.

- [x] **Step 10: Run the affected suites**

Run: `uv run pytest tests/test_rename_notice.py tests/test_rename_handle.py tests/test_rename_carries_planes.py tests/test_core_manager.py tests/test_slash_commands.py tests/test_mcp_bridge.py tests/test_wssession_handoff_rename.py tests/test_session_titles.py tests/test_tui.py tests/test_app_history_integration.py -p no:randomly -q`
Expected: PASS, no failures.

- [x] **Step 11: Run the full suite as the real gate**

This touches a signature four files depend on, so the subset is not enough.
Run it as its own tool call, unpiped:

`uv run pytest -p no:randomly -q`

Expected: `6 failed, N passed` — and the 6 are exactly the known `*_live.py`
failures listed in Global Constraints. Any other failure is yours.

- [x] **Step 12: Update the changelog**

In `CHANGELOG.md`, under `## [Unreleased]`, add an `### Added` section above
the existing `### Fixed`:

```markdown
### Added

- **An agent is now told when you rename it.** It could not see the change
  before — no message announced it, and its system prompt still carried the
  handle it was born with — so it went on passing a dead name as
  `from_handle`, addressing monitors and queue callbacks to a session that
  did not exist. The notice rides the next turn rather than starting one, so
  renaming an idle agent still costs nothing, and it lands before the agent
  acts rather than after.

  Operator renames only. An agent that renames itself already has the return
  value, and a *peer* rename is indistinguishable from a self-rename until
  `from_handle` becomes a transport fact (`TASKS.md:245`) — faking the
  attribution would be worse than the honest gap.
```

- [x] **Step 13: Commit**

```bash
git add src/aegis/core/manager.py src/aegis/tui/app.py src/aegis/commands/builtins/session_ctl.py src/aegis/web/wssession.py tests/test_rename_notice.py tests/test_tui.py tests/test_slash_commands.py CHANGELOG.md
git commit -m "feat(rename): tell the session when the operator renames it

Both rename implementations now raise the substrate notice, and the
operator call sites declare themselves with by='operator'. An MCP
rename stays silent: a self-rename already returned {ok, old, new}, and
the shared MCP port cannot tell one from a peer rename.

Three operator call sites, two edits — the TUI and the web client share
the /rename command function. The web client's direct rename_handle RPC
is the third and is easy to miss; fb262d7 shipped a rename defect today
that lived in exactly one of two paths."
```

- [x] **Step 14: Mark the spec implemented**

In `docs/superpowers/specs/2026-08-12-rename-announcement-design.md`, change
the status line to:

```markdown
**Status:** implemented 2026-08-12; plan at
`docs/superpowers/plans/2026-08-12-rename-announcement.md`.
```

Commit: `git add docs/superpowers/specs/2026-08-12-rename-announcement-design.md && git commit -m "docs(spec): mark the rename announcement implemented"`

- [x] **Step 15: Push**

```bash
git push origin main
```

---

## Not in this plan

Carried from the spec's *Out of scope*, so a reader does not think they were
forgotten:

- **Peer renames** stay silent — blocked on per-session MCP identity
  (`TASKS.md:245`). When that lands, the rule tightens to "announce unless
  the resolved caller is the target" and the `by=` keyword can be dropped.
- **Title changes** do not announce. Display only; nothing goes stale.
- **`aegis_enqueue` / `aegis_remind` / `aegis_handoff` handle validation** —
  the backstop `3686083` gave monitors would fit all three, but it is a
  separate change with its own blast radius.
- **Restarting the running aegis.** None of this reaches Alex's live
  sessions until he restarts, which is his call.
