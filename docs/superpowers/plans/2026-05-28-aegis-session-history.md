# Aegis Session History (Ctrl+H) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `Ctrl+H` modal that lists every user-initiated agent session (open or closed, current process or previous) and reopens the selected row — either by jumping to the live tab, resuming via the existing `drv.resume()` protocol, or spawning a fresh session with the recorded profile + cwd.

**Architecture:** Two new event variants (`SessionMeta`, `SessionClosed`) are written into the existing `.aegis/state/sessions/<handle>.jsonl` per-session event log. A new `aegis.state.history` module reads them back as `SessionHistoryRow`s. A new `aegis.tui.history.HistoryModal` renders them, and `AegisApp.action_open_history()` dispatches outcomes to existing spawn / resume / focus paths. No driver changes; no new persistence files; no changes to `AgentSession`.

**Tech Stack:** Python 3.13, Textual 8.x, pytest, `uv` for package management.

**Spec:** `docs/superpowers/specs/2026-05-28-aegis-session-history-design.md`

---

## Vertical slice plan

1. **Slice 1 — Backend foundation + thinnest end-to-end read path.** Add the two events, extend the codec, add `list_history()`, write meta eagerly on `_spawn()`, mount a minimal modal, bind `Ctrl+H` for *open-fresh only*. After this slice, Ctrl+H produces a working list and a fresh re-spawn — no resume, no preview, no close marker yet.
2. **Slice 2 — Resume path + session_id capture.** Latch `session_id` from `SystemInit` events at read time; route the modal's "resume" outcome through `drv.resume()` + `ConversationPane(replay=…)`. After this slice, Claude rows resume with full conversation continuity.
3. **Slice 3 — Polish: close marker, preview, Telegram parity.** Add `SessionClosed` emission, defer meta-write until the first user message (so `preview` is populated), wire the same writes into `SessionManager.spawn()` for the headless / Telegram path.

Each slice is independently testable and committable.

---

## File map

**Create:**
- `src/aegis/state/history.py` — history reader + `SessionHistoryRow`
- `src/aegis/tui/history.py` — `HistoryModal`
- `tests/test_session_meta_event.py`
- `tests/test_history_reader.py`
- `tests/test_history_modal.py`
- `tests/test_app_history_integration.py`
- `tests/test_history_live.py` (marker `live`)

**Modify:**
- `src/aegis/events.py` — add `SessionMeta` + `SessionClosed` to the `Event` sum
- `src/aegis/state/event_codec.py` — encode/decode for the new variants
- `src/aegis/state/session_log.py` — add `append_meta(state_dir, meta)` helper
- `src/aegis/tui/app.py` — bind `Ctrl+H`, emit meta on `_spawn`, emit closed on `_close_pane` / `action_quit`, route modal outcomes
- `src/aegis/tui/pane.py` — first-user-message hook to emit `SessionMeta` (slice 3)
- `src/aegis/core/manager.py` — emit meta/closed via `SessionManager` (slice 3)

---

# SLICE 1 — Backend foundation + thinnest end-to-end

## Task 1: Add `SessionMeta` and `SessionClosed` event variants

**Files:**
- Modify: `src/aegis/events.py`
- Test: `tests/test_session_meta_event.py`

- [ ] **Step 1.1: Write the failing test**

Create `tests/test_session_meta_event.py`:

```python
from aegis.events import Event, SessionClosed, SessionMeta


def test_session_meta_is_in_event_sum():
    m = SessionMeta(
        handle="lucid-knuth",
        profile="claude-sonnet",
        provider="claude-code",
        cwd="/tmp/proj",
        created_at="2026-05-28T14:00:00Z",
        origin="tui",
        preview="",
    )
    assert isinstance(m, Event.__args__)


def test_session_closed_is_in_event_sum():
    c = SessionClosed(
        closed_at="2026-05-28T15:00:00Z", reason="user")
    assert isinstance(c, Event.__args__)


def test_session_closed_reason_must_be_one_of_known():
    # Frozen dataclass — no validation in v1; document the expectation
    # via test that the known reasons round-trip.
    for r in ("user", "interrupt", "crash"):
        c = SessionClosed(closed_at="2026-05-28T15:00:00Z", reason=r)
        assert c.reason == r
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `uv run pytest tests/test_session_meta_event.py -v`
Expected: FAIL with `ImportError: cannot import name 'SessionMeta'`.

- [ ] **Step 1.3: Implement the new variants**

In `src/aegis/events.py`, add after the `Unknown` dataclass:

```python
@dataclass(frozen=True)
class SessionMeta:
    handle: str
    profile: str
    provider: str
    cwd: str
    created_at: str
    origin: str
    preview: str = ""


@dataclass(frozen=True)
class SessionClosed:
    closed_at: str
    reason: str
```

Update the `Event` union:

```python
Event = (
    SystemInit | AssistantText | AssistantThinking
    | ToolUse | ToolResult | Result | Unknown
    | SessionMeta | SessionClosed
)
```

- [ ] **Step 1.4: Run test to verify it passes**

Run: `uv run pytest tests/test_session_meta_event.py -v`
Expected: PASS (3 tests).

- [ ] **Step 1.5: Commit**

```bash
cd repos/aegis
git add src/aegis/events.py tests/test_session_meta_event.py
git commit -m "feat(events): add SessionMeta and SessionClosed event types"
```

---

## Task 2: Extend `event_codec` round-trip for the new variants

**Files:**
- Modify: `src/aegis/state/event_codec.py`
- Test: `tests/test_session_meta_event.py` (extend)

- [ ] **Step 2.1: Add the failing codec test**

Append to `tests/test_session_meta_event.py`:

```python
from aegis.state.event_codec import decode_event, encode_event


def test_session_meta_codec_roundtrip():
    m = SessionMeta(
        handle="lucid-knuth", profile="claude-sonnet",
        provider="claude-code", cwd="/tmp/proj",
        created_at="2026-05-28T14:00:00Z", origin="tui",
        preview="hello world",
    )
    assert decode_event(encode_event(m)) == m


def test_session_closed_codec_roundtrip():
    c = SessionClosed(closed_at="2026-05-28T15:00:00Z", reason="user")
    assert decode_event(encode_event(c)) == c
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `uv run pytest tests/test_session_meta_event.py::test_session_meta_codec_roundtrip -v`
Expected: FAIL with `ValueError: unknown event type: SessionMeta`.

- [ ] **Step 2.3: Extend the codec**

In `src/aegis/state/event_codec.py`:

Update the import:

```python
from aegis.events import (
    AssistantText, AssistantThinking, Event, Result, SessionClosed,
    SessionMeta, SystemInit, TokenUsage, ToolResult, ToolUse, Unknown,
)
```

Add encode branches after the `Unknown` branch in `encode_event`:

```python
    if isinstance(ev, SessionMeta):
        return {"t": "SessionMeta",
                "handle": ev.handle, "profile": ev.profile,
                "provider": ev.provider, "cwd": ev.cwd,
                "created_at": ev.created_at, "origin": ev.origin,
                "preview": ev.preview}
    if isinstance(ev, SessionClosed):
        return {"t": "SessionClosed",
                "closed_at": ev.closed_at, "reason": ev.reason}
```

Add decode branches before the final `raise` in `decode_event`:

```python
    if t == "SessionMeta":
        return SessionMeta(
            handle=d["handle"], profile=d["profile"],
            provider=d["provider"], cwd=d["cwd"],
            created_at=d["created_at"], origin=d["origin"],
            preview=d.get("preview", ""))
    if t == "SessionClosed":
        return SessionClosed(closed_at=d["closed_at"],
                             reason=d["reason"])
```

- [ ] **Step 2.4: Run test to verify it passes**

Run: `uv run pytest tests/test_session_meta_event.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 2.5: Commit**

```bash
git add src/aegis/state/event_codec.py tests/test_session_meta_event.py
git commit -m "feat(state): encode/decode SessionMeta and SessionClosed"
```

---

## Task 3: Add `session_log.append_meta` helper

**Files:**
- Modify: `src/aegis/state/session_log.py`
- Test: `tests/test_history_reader.py` (new — placeholder for now)

- [ ] **Step 3.1: Write the failing test**

Create `tests/test_history_reader.py`:

```python
from pathlib import Path

from aegis.events import SessionMeta
from aegis.state.session_log import (
    append_meta, replay_events, session_log_path,
)


def test_append_meta_writes_meta_as_first_record(tmp_path: Path):
    sd = tmp_path / "state"
    m = SessionMeta(
        handle="h1", profile="p1", provider="claude-code",
        cwd="/tmp", created_at="2026-05-28T14:00:00Z",
        origin="tui", preview="",
    )
    append_meta(sd, m)
    replay = replay_events(sd, "h1")
    assert len(replay.events) == 1
    assert replay.events[0] == m


def test_append_meta_creates_sessions_directory(tmp_path: Path):
    sd = tmp_path / "state"
    m = SessionMeta(
        handle="h1", profile="p1", provider="claude-code",
        cwd="/tmp", created_at="2026-05-28T14:00:00Z",
        origin="tui", preview="",
    )
    append_meta(sd, m)
    assert session_log_path(sd, "h1").exists()
```

- [ ] **Step 3.2: Run test to verify it fails**

Run: `uv run pytest tests/test_history_reader.py -v`
Expected: FAIL with `ImportError: cannot import name 'append_meta'`.

- [ ] **Step 3.3: Implement `append_meta`**

In `src/aegis/state/session_log.py`, after `append_event`:

```python
def append_meta(state_dir_path: Path, meta) -> None:
    """Write a SessionMeta as a regular event record. Caller is
    responsible for invariant: this is the first record in the file."""
    append_event(state_dir_path, meta.handle, meta)
```

(`append_event` already does the JSON write, the mkdir, and the envelope wrapping; `append_meta` is just an intent-revealing alias.)

- [ ] **Step 3.4: Run test to verify it passes**

Run: `uv run pytest tests/test_history_reader.py -v`
Expected: PASS (2 tests).

- [ ] **Step 3.5: Commit**

```bash
git add src/aegis/state/session_log.py tests/test_history_reader.py
git commit -m "feat(state): add session_log.append_meta helper"
```

---

## Task 4: Implement `list_history()` reader (meta-only baseline)

**Files:**
- Create: `src/aegis/state/history.py`
- Test: `tests/test_history_reader.py` (extend)

- [ ] **Step 4.1: Write the failing test**

Append to `tests/test_history_reader.py`:

```python
from aegis.state.history import SessionHistoryRow, list_history


def _meta(handle: str, profile: str = "claude-sonnet",
          provider: str = "claude-code",
          created_at: str = "2026-05-28T14:00:00Z") -> SessionMeta:
    return SessionMeta(
        handle=handle, profile=profile, provider=provider,
        cwd="/tmp", created_at=created_at, origin="tui", preview="")


def test_list_history_returns_one_row_per_meta_file(tmp_path: Path):
    sd = tmp_path / "state"
    append_meta(sd, _meta("h1"))
    append_meta(sd, _meta("h2"))
    rows = list_history(sd, live_handles=set())
    assert {r.handle for r in rows} == {"h1", "h2"}
    assert all(isinstance(r, SessionHistoryRow) for r in rows)


def test_list_history_skips_files_without_meta_header(tmp_path: Path):
    """Worker logs (no SessionMeta first record) are excluded."""
    from aegis.events import AssistantText
    from aegis.state.session_log import append_event
    sd = tmp_path / "state"
    # Worker-shaped log: no meta, just events
    append_event(sd, "worker-handle", AssistantText(text="hi"))
    # User-shaped log: meta + events
    append_meta(sd, _meta("user-handle"))
    rows = list_history(sd, live_handles=set())
    assert {r.handle for r in rows} == {"user-handle"}


def test_list_history_marks_open_rows(tmp_path: Path):
    sd = tmp_path / "state"
    append_meta(sd, _meta("h1"))
    append_meta(sd, _meta("h2"))
    rows = list_history(sd, live_handles={"h1"})
    by_handle = {r.handle: r for r in rows}
    assert by_handle["h1"].is_open is True
    assert by_handle["h2"].is_open is False


def test_list_history_sorted_most_recent_first(tmp_path: Path):
    sd = tmp_path / "state"
    append_meta(sd, _meta("old", created_at="2026-05-28T10:00:00Z"))
    append_meta(sd, _meta("new", created_at="2026-05-28T14:00:00Z"))
    rows = list_history(sd, live_handles=set())
    assert [r.handle for r in rows] == ["new", "old"]


def test_list_history_returns_empty_when_no_sessions_dir(tmp_path: Path):
    sd = tmp_path / "state"
    rows = list_history(sd, live_handles=set())
    assert rows == []


def test_list_history_caps_at_limit(tmp_path: Path):
    sd = tmp_path / "state"
    for i in range(5):
        append_meta(sd, _meta(
            f"h{i}",
            created_at=f"2026-05-28T1{i}:00:00Z"))
    rows = list_history(sd, live_handles=set(), limit=2)
    assert len(rows) == 2
    assert [r.handle for r in rows] == ["h4", "h3"]
```

- [ ] **Step 4.2: Run test to verify it fails**

Run: `uv run pytest tests/test_history_reader.py -v`
Expected: 6 failing tests (`ImportError: aegis.state.history`).

- [ ] **Step 4.3: Implement `aegis.state.history`**

Create `src/aegis/state/history.py`:

```python
"""History reader: glob per-session event logs, fold into rows.

Files without a SessionMeta first record are excluded — that is the
gating mechanism that keeps queue-worker / workflow-spawn logs out of
the Ctrl+H listing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from aegis.events import (
    AssistantText, AssistantThinking, SessionClosed, SessionMeta,
    SystemInit,
)
from aegis.state.event_codec import decode_event


@dataclass(frozen=True)
class SessionHistoryRow:
    handle: str
    profile: str
    provider: str
    cwd: str
    created_at: str
    closed_at: str | None
    last_activity_at: str
    preview: str
    session_id: str | None
    is_open: bool
    crash_inferred: bool


def _fold_file(path: Path) -> tuple[SessionMeta, str, str | None,
                                    str | None, str] | None:
    """Return (meta, last_ts, closed_at, session_id, preview) or None
    if the file has no SessionMeta first record."""
    meta: SessionMeta | None = None
    last_ts: str = ""
    closed_at: str | None = None
    session_id: str | None = None
    preview: str = ""
    first_line = True
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                ev_dict = rec.get("event")
                if not isinstance(ev_dict, dict):
                    continue
                if first_line:
                    first_line = False
                    if ev_dict.get("t") != "SessionMeta":
                        return None
                try:
                    ev = decode_event(ev_dict)
                except (ValueError, KeyError):
                    continue
                ts = rec.get("aegis_ts", "")
                if ts > last_ts:
                    last_ts = ts
                if isinstance(ev, SessionMeta):
                    meta = ev
                    if ev.preview:
                        preview = ev.preview
                elif isinstance(ev, SessionClosed):
                    closed_at = ev.closed_at
                elif isinstance(ev, SystemInit):
                    if ev.session_id:
                        session_id = ev.session_id
                elif isinstance(ev, (AssistantText, AssistantThinking)):
                    if not preview and ev.text:
                        preview = ev.text[:200]
    except OSError:
        return None
    if meta is None:
        return None
    return meta, last_ts or meta.created_at, closed_at, session_id, preview


def list_history(state_dir_path: Path, *, live_handles: set[str],
                 limit: int = 500) -> list[SessionHistoryRow]:
    sessions_dir = state_dir_path / "sessions"
    if not sessions_dir.is_dir():
        return []
    rows: list[SessionHistoryRow] = []
    for p in sessions_dir.glob("*.jsonl"):
        folded = _fold_file(p)
        if folded is None:
            continue
        meta, last_ts, closed_at, session_id, preview = folded
        rows.append(SessionHistoryRow(
            handle=meta.handle,
            profile=meta.profile,
            provider=meta.provider,
            cwd=meta.cwd,
            created_at=meta.created_at,
            closed_at=closed_at,
            last_activity_at=last_ts,
            preview=preview,
            session_id=session_id,
            is_open=meta.handle in live_handles,
            crash_inferred=(closed_at is None
                            and meta.handle not in live_handles),
        ))
    rows.sort(key=lambda r: r.last_activity_at, reverse=True)
    return rows[:limit]
```

- [ ] **Step 4.4: Run test to verify it passes**

Run: `uv run pytest tests/test_history_reader.py -v`
Expected: PASS (8 tests in this file).

- [ ] **Step 4.5: Commit**

```bash
git add src/aegis/state/history.py tests/test_history_reader.py
git commit -m "feat(state): add history.list_history reader"
```

---

## Task 5: Emit `SessionMeta` from `_spawn` (eager baseline)

**Files:**
- Modify: `src/aegis/tui/app.py`
- Test: `tests/test_app_history_integration.py`

For slice 1 we write meta **eagerly at spawn time** with empty preview. Slice 3 will defer this to first-user-message so preview is populated.

- [ ] **Step 5.1: Write the failing test**

Create `tests/test_app_history_integration.py`:

```python
"""Integration tests for history wiring through AegisApp.

The hermetic test rig stubs out the make_session factory so we exercise
the persistence pipeline without spawning real harness subprocesses.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aegis.config import Agent
from aegis.events import SessionMeta
from aegis.state.session_log import replay_events


@pytest.fixture
def fake_agent():
    return Agent(harness="claude-code", model="claude-sonnet-4-5",
                 effort="medium", permission="default")


def _make_fake_session(agent, mcp_url, handle):
    s = MagicMock()
    s.handle = handle
    s.session_id = None
    s.state = MagicMock(value="idle")
    return s


@pytest.mark.asyncio
async def test_spawn_writes_session_meta(tmp_path: Path, fake_agent,
                                         monkeypatch):
    monkeypatch.chdir(tmp_path)
    from aegis.tui.app import AegisApp
    app = AegisApp(
        agents={"sonnet": fake_agent},
        default_agent="sonnet",
        make_session=_make_fake_session,
        mcp=MagicMock(url="http://localhost:0/", bind=MagicMock(),
                      start=MagicMock(), stop=MagicMock()))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Default tab was spawned during on_mount; expect one
        # SessionMeta record for it.
        sessions_dir = tmp_path / ".aegis" / "state" / "sessions"
        assert sessions_dir.is_dir()
        log_files = list(sessions_dir.glob("*.jsonl"))
        assert len(log_files) == 1
        replay = replay_events(tmp_path / ".aegis" / "state",
                               log_files[0].stem)
        assert any(isinstance(e, SessionMeta) for e in replay.events)
```

- [ ] **Step 5.2: Run test to verify it fails**

Run: `uv run pytest tests/test_app_history_integration.py::test_spawn_writes_session_meta -v`
Expected: FAIL — `sessions_dir.is_dir()` returns False, or no SessionMeta in the replay.

- [ ] **Step 5.3: Wire the write in `_spawn`**

In `src/aegis/tui/app.py`, add the import:

```python
from aegis.events import SessionMeta, SessionClosed
from aegis.state.session_log import append_meta
```

In `AegisApp._spawn(...)`, after `pane = ConversationPane(...)` but before `cs.mount(pane)`, add:

```python
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        append_meta(self._state_dir, SessionMeta(
            handle=h, profile=slug, provider=agent.harness,
            cwd=self._cwd, created_at=now_iso, origin="tui",
            preview=""))
```

(Note: do NOT add meta-writing in `_SessionManagerAdapter.spawn(...)` — that path serves queue workers, which are out of scope per the spec.)

- [ ] **Step 5.4: Run test to verify it passes**

Run: `uv run pytest tests/test_app_history_integration.py::test_spawn_writes_session_meta -v`
Expected: PASS.

- [ ] **Step 5.5: Add the negation test for queue workers**

Append to `tests/test_app_history_integration.py`:

```python
@pytest.mark.asyncio
async def test_queue_worker_spawn_writes_no_meta(tmp_path: Path,
                                                  fake_agent,
                                                  monkeypatch):
    """Queue workers (via _SessionManagerAdapter) must not pollute
    Ctrl+H with worker logs."""
    monkeypatch.chdir(tmp_path)
    from aegis.tui.app import AegisApp, _SessionManagerAdapter
    app = AegisApp(
        agents={"sonnet": fake_agent},
        default_agent="sonnet",
        make_session=_make_fake_session,
        mcp=MagicMock(url="http://localhost:0/", bind=MagicMock(),
                      start=MagicMock(), stop=MagicMock()))
    async with app.run_test() as pilot:
        await pilot.pause()
        sessions_dir = tmp_path / ".aegis" / "state" / "sessions"
        files_before = set(sessions_dir.glob("*.jsonl"))
        adapter = _SessionManagerAdapter(app)
        adapter.spawn("sonnet", handle="worker-test")
        await pilot.pause()
        files_after = set(sessions_dir.glob("*.jsonl"))
        new_files = files_after - files_before
        # The worker file may or may not exist depending on whether the
        # mocked session writes events; what matters is that if it does
        # exist it carries no SessionMeta.
        for f in new_files:
            replay = replay_events(tmp_path / ".aegis" / "state",
                                   f.stem)
            assert not any(isinstance(e, SessionMeta)
                           for e in replay.events), (
                f"queue worker {f.stem} got an unexpected SessionMeta")
```

Run: `uv run pytest tests/test_app_history_integration.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5.6: Commit**

```bash
git add src/aegis/tui/app.py tests/test_app_history_integration.py
git commit -m "feat(tui): emit SessionMeta on user-initiated spawn"
```

---

## Task 6: Implement `HistoryModal` (open-fresh only)

**Files:**
- Create: `src/aegis/tui/history.py`
- Test: `tests/test_history_modal.py`

- [ ] **Step 6.1: Write the failing test**

Create `tests/test_history_modal.py`:

```python
"""Hermetic tests for HistoryModal — exercises layout, filter, and
dismiss outcomes without depending on the full AegisApp."""
import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

from aegis.state.history import SessionHistoryRow
from aegis.tui.history import HistoryModal


def _row(handle: str, *, is_open: bool = False,
         session_id: str | None = None,
         profile: str = "claude-sonnet",
         provider: str = "claude-code",
         last: str = "2026-05-28T14:00:00Z") -> SessionHistoryRow:
    return SessionHistoryRow(
        handle=handle, profile=profile, provider=provider,
        cwd="/tmp", created_at=last, closed_at=None,
        last_activity_at=last, preview="hello",
        session_id=session_id, is_open=is_open,
        crash_inferred=False)


class _Harness(App):
    def __init__(self, rows, agents) -> None:
        super().__init__()
        self.rows = rows
        self.agents = agents
        self.dismissed = None

    def compose(self) -> ComposeResult:
        yield Label("host")

    async def on_mount(self) -> None:
        self.dismissed = await self.push_screen_wait(
            HistoryModal(self.rows, agents=self.agents,
                         resume_capable_providers={"claude-code"}))


@pytest.mark.asyncio
async def test_history_modal_renders_rows():
    rows = [_row("h1"), _row("h2", is_open=True)]
    app = _Harness(rows, agents={"claude-sonnet"})
    async with app.run_test() as pilot:
        await pilot.pause()
        # Both handles should appear somewhere in the screen text.
        text = pilot.app.screen.render().__str__()
        assert "h1" in text
        assert "h2" in text


@pytest.mark.asyncio
async def test_history_modal_empty_state():
    app = _Harness([], agents=set())
    async with app.run_test() as pilot:
        await pilot.pause()
        text = pilot.app.screen.render().__str__()
        assert "no history" in text.lower()


@pytest.mark.asyncio
async def test_history_modal_filter_narrows_rows():
    rows = [_row("apple"), _row("banana")]
    app = _Harness(rows, agents={"claude-sonnet"})
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a", "p", "p")
        await pilot.pause()
        text = pilot.app.screen.render().__str__()
        assert "apple" in text
        assert "banana" not in text


@pytest.mark.asyncio
async def test_history_modal_enter_dismisses_with_open_fresh_for_closed():
    rows = [_row("h1")]
    app = _Harness(rows, agents={"claude-sonnet"})
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.dismissed is not None
        kind, payload = app.dismissed
        assert kind == "open_fresh"
        assert payload.handle == "h1"


@pytest.mark.asyncio
async def test_history_modal_enter_dismisses_with_jump_for_open_row():
    rows = [_row("h1", is_open=True)]
    app = _Harness(rows, agents={"claude-sonnet"})
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        kind, payload = app.dismissed
        assert kind == "jump"
        assert payload == "h1"


@pytest.mark.asyncio
async def test_history_modal_escape_dismisses_none():
    rows = [_row("h1")]
    app = _Harness(rows, agents={"claude-sonnet"})
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.dismissed is None
```

- [ ] **Step 6.2: Run test to verify it fails**

Run: `uv run pytest tests/test_history_modal.py -v`
Expected: FAIL — `ImportError: aegis.tui.history`.

- [ ] **Step 6.3: Implement `HistoryModal`**

Create `src/aegis/tui/history.py`:

```python
"""Ctrl+H history modal — pick a prior session and open it.

Dismiss payloads:
    ("jump", handle)         — switch focus to live tab
    ("resume", row)          — drv.resume() via existing protocol
    ("open_fresh", row)      — _spawn(profile, cwd=row.cwd)
    None                     — Escape, no action.
"""
from __future__ import annotations

from typing import Iterable

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option

from aegis.state.history import SessionHistoryRow


def _relative_time(iso_ts: str, now_iso: str | None = None) -> str:
    """Best-effort relative-time formatter ('2m ago', '3h ago')."""
    from datetime import datetime, timezone
    try:
        ts = datetime.fromisoformat(iso_ts.rstrip("Z")).replace(
            tzinfo=timezone.utc)
    except ValueError:
        return iso_ts
    now = (datetime.fromisoformat(now_iso.rstrip("Z")).replace(
        tzinfo=timezone.utc) if now_iso
           else datetime.now(timezone.utc))
    delta = (now - ts).total_seconds()
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def _glyph(row: SessionHistoryRow, agents: set[str],
           resume_capable_providers: set[str]) -> str:
    if row.profile not in agents:
        return "⊘"
    if row.is_open:
        return "●"
    if (row.session_id is not None
            and row.provider in resume_capable_providers):
        return "↻"
    return "○"


def _row_label(row: SessionHistoryRow, agents: set[str],
               resume_capable_providers: set[str]) -> str:
    glyph = _glyph(row, agents, resume_capable_providers)
    rel = _relative_time(row.last_activity_at)
    preview = (row.preview or "").replace("\n", " ")[:40]
    profile = row.profile if row.profile in agents else (
        f"<{row.profile} missing>")
    return f"{glyph} {row.handle:<18} {profile:<18} {rel:<10} {preview}"


class HistoryModal(ModalScreen):
    """Pick a prior session to reopen."""

    DEFAULT_CSS = """
    HistoryModal { align: center middle; }
    HistoryModal #hist-box {
        width: 78; max-height: 22;
        border: round $panel; background: $surface; padding: 1 2;
    }
    HistoryModal Input { width: 100%; margin-bottom: 1; border: none;
                          background: $background; }
    HistoryModal OptionList { width: 100%; max-height: 16;
                              border: none; background: $surface; }
    HistoryModal #hist-empty { width: 100%; color: $text-muted;
                                content-align: center middle; }
    """

    def __init__(self, rows: Iterable[SessionHistoryRow], *,
                 agents: set[str],
                 resume_capable_providers: set[str]) -> None:
        super().__init__()
        self._rows = list(rows)
        self._agents = agents
        self._resume_capable = resume_capable_providers

    def compose(self) -> ComposeResult:
        with Vertical(id="hist-box"):
            if not self._rows:
                yield Label("no history yet", id="hist-empty")
                return
            yield Input(placeholder="filter…", id="hist-input")
            yield OptionList(id="hist-list")

    def on_mount(self) -> None:
        if not self._rows:
            return
        self._refresh("")
        self.query_one("#hist-input", Input).focus()

    def _matches(self, row: SessionHistoryRow, needle: str) -> bool:
        if not needle:
            return True
        needle = needle.lower()
        haystack = " ".join([
            row.handle, row.profile, row.cwd, row.preview]).lower()
        return needle in haystack

    def _refresh(self, needle: str) -> None:
        ol = self.query_one("#hist-list", OptionList)
        ol.clear_options()
        for r in self._rows:
            if not self._matches(r, needle):
                continue
            label = _row_label(r, self._agents, self._resume_capable)
            ol.add_option(Option(label, id=r.handle))
        if ol.option_count > 0:
            ol.highlighted = 0

    def _row_for(self, handle: str) -> SessionHistoryRow | None:
        for r in self._rows:
            if r.handle == handle:
                return r
        return None

    def _select(self, handle: str) -> None:
        row = self._row_for(handle)
        if row is None:
            return
        if row.profile not in self._agents:
            return  # dimmed, non-actionable
        if row.is_open:
            self.dismiss(("jump", row.handle))
            return
        if (row.session_id is not None
                and row.provider in self._resume_capable):
            self.dismiss(("resume", row))
            return
        self.dismiss(("open_fresh", row))

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh(event.value)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        ol = self.query_one("#hist-list", OptionList)
        if ol.highlighted is None or ol.option_count == 0:
            return
        opt = ol.get_option_at_index(ol.highlighted)
        if opt.id:
            self._select(opt.id)

    def on_option_list_option_selected(
            self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self._select(event.option.id)

    def key_f(self) -> None:
        """Force open-fresh on the highlighted row."""
        ol = self.query_one("#hist-list", OptionList)
        if ol.highlighted is None or ol.option_count == 0:
            return
        opt = ol.get_option_at_index(ol.highlighted)
        row = self._row_for(opt.id) if opt.id else None
        if row is None or row.profile not in self._agents:
            return
        self.dismiss(("open_fresh", row))

    def key_r(self) -> None:
        """Force resume on the highlighted row (when capable)."""
        ol = self.query_one("#hist-list", OptionList)
        if ol.highlighted is None or ol.option_count == 0:
            return
        opt = ol.get_option_at_index(ol.highlighted)
        row = self._row_for(opt.id) if opt.id else None
        if row is None or row.profile not in self._agents:
            return
        if (row.session_id is not None
                and row.provider in self._resume_capable):
            self.dismiss(("resume", row))

    def key_escape(self) -> None:
        self.dismiss(None)
```

- [ ] **Step 6.4: Run test to verify it passes**

Run: `uv run pytest tests/test_history_modal.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6.5: Commit**

```bash
git add src/aegis/tui/history.py tests/test_history_modal.py
git commit -m "feat(tui): HistoryModal — list and select prior sessions"
```

---

## Task 7: Bind `Ctrl+H` and route outcomes (open-fresh only for slice 1)

**Files:**
- Modify: `src/aegis/tui/app.py`
- Test: `tests/test_app_history_integration.py` (extend)

- [ ] **Step 7.1: Write the failing integration test**

Append to `tests/test_app_history_integration.py`:

```python
@pytest.mark.asyncio
async def test_ctrl_h_opens_history_modal(tmp_path: Path, fake_agent,
                                          monkeypatch):
    monkeypatch.chdir(tmp_path)
    from aegis.tui.app import AegisApp
    from aegis.tui.history import HistoryModal
    app = AegisApp(
        agents={"sonnet": fake_agent},
        default_agent="sonnet",
        make_session=_make_fake_session,
        mcp=MagicMock(url="http://localhost:0/", bind=MagicMock(),
                      start=MagicMock(), stop=MagicMock()))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+h")
        await pilot.pause()
        assert isinstance(pilot.app.screen, HistoryModal)
```

- [ ] **Step 7.2: Run test to verify it fails**

Run: `uv run pytest tests/test_app_history_integration.py::test_ctrl_h_opens_history_modal -v`
Expected: FAIL — current screen is not a `HistoryModal`.

- [ ] **Step 7.3: Bind the key and implement the action**

In `src/aegis/tui/app.py`, add to `BINDINGS`:

```python
        Binding("ctrl+h", "open_history", "History", priority=True),
```

Add the action method (slice 1: open-fresh and jump only; resume is wired in slice 2):

```python
    _RESUME_CAPABLE_PROVIDERS: set[str] = {"claude-code"}

    @work
    async def action_open_history(self) -> None:
        from aegis.state.history import list_history
        from aegis.tui.history import HistoryModal

        live = {p.handle for p in self._panes
                if isinstance(p, ConversationPane)}
        rows = list_history(self._state_dir, live_handles=live)
        outcome = await self.push_screen_wait(
            HistoryModal(rows, agents=set(self._agents),
                         resume_capable_providers=(
                             self._RESUME_CAPABLE_PROVIDERS)))
        if outcome is None:
            return
        kind, payload = outcome
        if kind == "jump":
            self._jump_to_handle(payload)
        elif kind == "open_fresh":
            await self._spawn(payload.profile)
        # "resume" — wired in slice 2

    def _jump_to_handle(self, handle: str) -> None:
        for p in self._panes:
            if (isinstance(p, ConversationPane)
                    and p.handle == handle):
                self.query_one(ContentSwitcher).current = p.id
                p.unseen = False
                p.focus_input()
                self._refresh_tabbar()
                return
```

- [ ] **Step 7.4: Run test to verify it passes**

Run: `uv run pytest tests/test_app_history_integration.py::test_ctrl_h_opens_history_modal -v`
Expected: PASS.

- [ ] **Step 7.5: Run the full suite to confirm no regression**

Run: `uv run pytest -q -m "not live"`
Expected: PASS for everything (no regressions).

- [ ] **Step 7.6: Commit**

```bash
git add src/aegis/tui/app.py tests/test_app_history_integration.py
git commit -m "feat(tui): bind Ctrl+H to history modal (open-fresh + jump)"
```

### Slice 1 done

At this point, `Ctrl+H` works end-to-end: it lists every user-initiated session (current + previous launches), and Enter either jumps to a live tab or spawns a fresh session with the recorded profile. The list reads from real disk state and reflects across process restarts. No resume, no preview, no close marker yet.

---

# SLICE 2 — Resume path

## Task 8: Wire the modal's "resume" outcome through `drv.resume()`

**Files:**
- Modify: `src/aegis/tui/app.py`
- Test: `tests/test_app_history_integration.py` (extend)

- [ ] **Step 8.1: Write the failing test**

Append to `tests/test_app_history_integration.py`:

```python
@pytest.mark.asyncio
async def test_history_resume_calls_driver_resume(tmp_path: Path,
                                                   fake_agent,
                                                   monkeypatch):
    """A row with session_id and a resume-capable provider routes the
    Enter action through drv.resume()."""
    monkeypatch.chdir(tmp_path)
    from datetime import datetime, timezone
    from aegis.events import SessionMeta, SystemInit
    from aegis.state.session_log import append_event, append_meta
    from aegis.tui.app import AegisApp

    sd = tmp_path / ".aegis" / "state"
    # Pre-seed a closed session log with meta + system init.
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    append_meta(sd, SessionMeta(
        handle="prior", profile="sonnet", provider="claude-code",
        cwd=str(tmp_path), created_at=now, origin="tui", preview=""))
    append_event(sd, "prior", SystemInit(session_id="upstream-1"))

    fake_driver = MagicMock()
    fake_driver.supports_resume = True
    fake_driver.resume = MagicMock(
        return_value=_make_fake_session(fake_agent, "", "prior"))

    app = AegisApp(
        agents={"sonnet": fake_agent},
        default_agent="sonnet",
        make_session=_make_fake_session,
        mcp=MagicMock(url="http://localhost:0/", bind=MagicMock(),
                      start=MagicMock(), stop=MagicMock()),
        drivers={"claude-code": fake_driver},
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+h")
        await pilot.pause()
        # Highlight the closed "prior" row (the default tab is the
        # current open row; "prior" should sort by recency).
        # Press 'r' to force resume.
        await pilot.press("p", "r", "i")  # filter to "prior"
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        fake_driver.resume.assert_called_once()
        args = fake_driver.resume.call_args
        # Signature: resume(agent, cwd, mcp_url, handle, session_id)
        assert args.args[3] == "prior"
        assert args.args[4] == "upstream-1"
```

- [ ] **Step 8.2: Run test to verify it fails**

Run: `uv run pytest tests/test_app_history_integration.py::test_history_resume_calls_driver_resume -v`
Expected: FAIL — `resume.assert_called_once()` fires AssertionError because the slice-1 code drops resume outcomes.

- [ ] **Step 8.3: Implement the resume branch**

In `src/aegis/tui/app.py`, extend `action_open_history` — replace the `# "resume" — wired in slice 2` line with:

```python
        elif kind == "resume":
            await self._resume_from_history(payload)
```

Add the helper method:

```python
    async def _resume_from_history(self, row) -> None:
        from aegis.state.session_log import replay_events
        from aegis.state.workspace import WorkspaceTab
        from aegis.tui.resume_plan import plan_resume

        # Build a one-tab Workspace and route through the same
        # classifier the boot path uses — keeps the skip semantics
        # identical (profile_missing / driver_no_resume / no_session_id).
        tab = WorkspaceTab(
            handle=row.handle, profile=row.profile, order=0,
            provider=row.provider, session_id=row.session_id,
            created_at=row.created_at)
        from aegis.state.workspace import Workspace
        ws = Workspace(active_handle=row.handle, tabs=[tab])
        plan = plan_resume(ws, self._agents, self._drivers)
        if not plan.resumable:
            self.notify(
                f"cannot resume {row.handle}: "
                f"{plan.skipped[0].reason.value}",
                severity="warning")
            return
        tab = plan.resumable[0].tab
        drv = self._drivers[tab.provider]
        agent = self._agents[tab.profile]
        try:
            session = drv.resume(
                agent, self._cwd, self._mcp.url,
                tab.handle, tab.session_id)
        except Exception as e:
            self.notify(f"resume failed: {e}", severity="error")
            return
        replay = replay_events(self._state_dir, tab.handle)
        pane = ConversationPane(
            session, agent, tab.profile, tab.handle, self._palette,
            digest=self.queue_digest, state_dir_path=self._state_dir,
            replay=replay)
        self._panes.append(pane)
        self.inbox_router.bind_session(tab.handle, pane._core)
        cs = self.query_one(ContentSwitcher)
        await cs.mount(pane)
        cs.current = pane.id
        pane.focus_input()
        if hasattr(pane, "show_resume_banner"):
            pane.show_resume_banner("↻ resumed from history")
        self._refresh_tabbar()
```

- [ ] **Step 8.4: Run test to verify it passes**

Run: `uv run pytest tests/test_app_history_integration.py::test_history_resume_calls_driver_resume -v`
Expected: PASS.

- [ ] **Step 8.5: Run the full suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 8.6: Commit**

```bash
git add src/aegis/tui/app.py tests/test_app_history_integration.py
git commit -m "feat(tui): wire Ctrl+H resume outcome through drv.resume()"
```

### Slice 2 done

Closed Claude sessions with a captured `session_id` now resume with full conversation continuity. Gemini and OpenCode rows automatically fall through to "open fresh" (the existing `plan_resume()` classifier handles that with the `driver_no_resume` skip reason).

---

# SLICE 3 — Polish: close marker, deferred meta with preview, Telegram parity

## Task 9: Emit `SessionClosed` on pane close + quit

**Files:**
- Modify: `src/aegis/tui/app.py`
- Test: `tests/test_app_history_integration.py` (extend)

- [ ] **Step 9.1: Write the failing test**

Append to `tests/test_app_history_integration.py`:

```python
@pytest.mark.asyncio
async def test_close_pane_writes_session_closed(tmp_path: Path,
                                                  fake_agent,
                                                  monkeypatch):
    monkeypatch.chdir(tmp_path)
    from aegis.events import SessionClosed
    from aegis.tui.app import AegisApp

    app = AegisApp(
        agents={"sonnet": fake_agent},
        default_agent="sonnet",
        make_session=_make_fake_session,
        mcp=MagicMock(url="http://localhost:0/", bind=MagicMock(),
                      start=MagicMock(), stop=MagicMock()))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Capture the default tab's handle then close it via Ctrl+W.
        handles_before = [
            p.handle for p in pilot.app._panes
            if hasattr(p, "handle")]
        assert len(handles_before) == 1
        target_handle = handles_before[0]
        # Spawn a second tab so closing the first doesn't trigger
        # app.exit() (which short-circuits the close path under test).
        await pilot.app._spawn("sonnet")
        await pilot.pause()
        # Switch back to the first tab and close it.
        pilot.app._activate(0)
        await pilot.pause()
        await pilot.press("ctrl+w")
        await pilot.pause()
        replay = replay_events(tmp_path / ".aegis" / "state",
                               target_handle)
        assert any(isinstance(e, SessionClosed)
                   for e in replay.events)
        closed = next(e for e in replay.events
                      if isinstance(e, SessionClosed))
        assert closed.reason == "user"
```

- [ ] **Step 9.2: Run test to verify it fails**

Run: `uv run pytest tests/test_app_history_integration.py::test_close_pane_writes_session_closed -v`
Expected: FAIL — no `SessionClosed` in the replayed events.

- [ ] **Step 9.3: Emit on close**

In `src/aegis/tui/app.py`, modify `_close_pane` to record the close before tearing down (add after the `isinstance(pane, ConversationPane)` check, before `await pane.close()`):

```python
    async def _close_pane(self, pane) -> None:
        if isinstance(pane, ConversationPane):
            self.inbox_router.unbind_session(pane.handle)
            self._record_session_closed(pane.handle, reason="user")
        await pane.close()
        if pane in self._panes:
            self._panes.remove(pane)
        try:
            await pane.remove()
        except Exception:
            pass

    def _record_session_closed(self, handle: str, *, reason: str) -> None:
        from datetime import datetime, timezone
        from aegis.state.session_log import (
            append_event, session_log_path,
        )
        # Only emit closed events for logs that have a meta header —
        # i.e. user-initiated sessions. Worker logs are silent.
        if not session_log_path(self._state_dir, handle).exists():
            return
        replay = replay_events(self._state_dir, handle)
        from aegis.events import SessionMeta
        has_meta = any(isinstance(e, SessionMeta) for e in replay.events)
        if not has_meta:
            return
        if any(isinstance(e, SessionClosed) for e in replay.events):
            return  # already closed
        now_iso = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        append_event(self._state_dir, handle,
                     SessionClosed(closed_at=now_iso, reason=reason))
```

Add the import at top:

```python
from aegis.state.session_log import append_meta, replay_events
```

Also call `_record_session_closed` in `action_quit` for each remaining pane:

```python
    async def action_quit(self) -> None:
        try:
            self._write_snapshot()
        except Exception:
            pass
        for pane in list(self._panes):
            if isinstance(pane, ConversationPane):
                self.inbox_router.unbind_session(pane.handle)
                self._record_session_closed(pane.handle, reason="user")
            await pane.close()
        self.queue_digest.stop()
        await self.queue_manager.stop()
        await self._mcp.stop()
        self._file_indexer.stop()
        self.exit()
```

- [ ] **Step 9.4: Run test to verify it passes**

Run: `uv run pytest tests/test_app_history_integration.py::test_close_pane_writes_session_closed -v`
Expected: PASS.

- [ ] **Step 9.5: Commit**

```bash
git add src/aegis/tui/app.py tests/test_app_history_integration.py
git commit -m "feat(tui): emit SessionClosed on pane close and quit"
```

---

## Task 10: Defer meta-write until first user message (populates `preview`)

**Files:**
- Modify: `src/aegis/tui/app.py`, `src/aegis/tui/pane.py`
- Test: `tests/test_app_history_integration.py` (modify the slice-1 test)

The slice-1 test asserts a meta record after spawn; this task changes the contract: meta is written when the first user message is submitted, with `preview` populated from that message. Lazy-spawn parity.

- [ ] **Step 10.1: Update the existing test to reflect the new contract**

In `tests/test_app_history_integration.py`, modify `test_spawn_writes_session_meta`:

```python
@pytest.mark.asyncio
async def test_first_message_writes_session_meta(tmp_path: Path,
                                                  fake_agent,
                                                  monkeypatch):
    monkeypatch.chdir(tmp_path)
    from aegis.tui.app import AegisApp
    app = AegisApp(
        agents={"sonnet": fake_agent},
        default_agent="sonnet",
        make_session=_make_fake_session,
        mcp=MagicMock(url="http://localhost:0/", bind=MagicMock(),
                      start=MagicMock(), stop=MagicMock()))
    async with app.run_test() as pilot:
        await pilot.pause()
        sessions_dir = tmp_path / ".aegis" / "state" / "sessions"
        # Pre-first-message: no meta on disk yet.
        if sessions_dir.is_dir():
            for f in sessions_dir.glob("*.jsonl"):
                replay = replay_events(
                    tmp_path / ".aegis" / "state", f.stem)
                assert not any(isinstance(e, SessionMeta)
                               for e in replay.events)
        # Send a message through the active pane.
        active = pilot.app._active
        active._submit("hello world")
        await pilot.pause()
        # Now meta should be present with preview populated.
        log_files = list(sessions_dir.glob("*.jsonl"))
        assert len(log_files) == 1
        replay = replay_events(tmp_path / ".aegis" / "state",
                               log_files[0].stem)
        metas = [e for e in replay.events
                 if isinstance(e, SessionMeta)]
        assert len(metas) == 1
        assert metas[0].preview == "hello world"
```

Delete the old `test_spawn_writes_session_meta` test (replaced by this one).

- [ ] **Step 10.2: Run test to verify it fails**

Run: `uv run pytest tests/test_app_history_integration.py::test_first_message_writes_session_meta -v`
Expected: FAIL — meta is written at spawn time, before the user message.

- [ ] **Step 10.3: Move the meta-write from `_spawn` to first-message**

In `src/aegis/tui/app.py`, remove the `append_meta(...)` call added in Task 5 from `_spawn`.

Pass the meta-write callback to `ConversationPane`. The cleanest hook: add a constructor parameter `on_first_user_message: Callable[[str], None] | None = None` to `ConversationPane`, called with the user-message text on the first `_submit` invocation.

In `src/aegis/tui/pane.py`, modify `ConversationPane.__init__` to accept the kwarg and store it:

```python
        self._on_first_user_message = on_first_user_message
        self._first_msg_recorded = False
```

In `ConversationPane._submit(text)`, at the very top of the method body:

```python
        if (text and not self._first_msg_recorded
                and self._on_first_user_message is not None):
            self._first_msg_recorded = True
            try:
                self._on_first_user_message(text)
            except Exception:
                pass  # never block a turn on history-write failure
```

Back in `src/aegis/tui/app.py` `_spawn(...)`, build the callback and pass it:

```python
        from datetime import datetime, timezone

        def _write_meta(first_msg: str, *, _h=h, _slug=slug,
                        _agent=agent) -> None:
            now_iso = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            preview = first_msg.replace("\n", " ")[:200]
            append_meta(self._state_dir, SessionMeta(
                handle=_h, profile=_slug, provider=_agent.harness,
                cwd=self._cwd, created_at=now_iso, origin="tui",
                preview=preview))

        pane = ConversationPane(
            self._make_session(agent, self._mcp.url, h), agent,
            slug, h, self._palette, digest=self.queue_digest,
            state_dir_path=self._state_dir,
            on_first_user_message=_write_meta)
```

- [ ] **Step 10.4: Run test to verify it passes**

Run: `uv run pytest tests/test_app_history_integration.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 10.5: Run the full suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 10.6: Commit**

```bash
git add src/aegis/tui/app.py src/aegis/tui/pane.py tests/test_app_history_integration.py
git commit -m "feat(history): defer SessionMeta to first user message, populate preview"
```

---

## Task 11: `SessionManager` parity for headless / Telegram path

**Files:**
- Modify: `src/aegis/core/manager.py`
- Test: `tests/test_app_history_integration.py` (extend with a manager-level test)

- [ ] **Step 11.1: Write the failing test**

Append to `tests/test_app_history_integration.py`:

```python
@pytest.mark.asyncio
async def test_session_manager_spawn_records_meta(tmp_path, fake_agent,
                                                    monkeypatch):
    """The headless aegis serve path (used by Telegram) also writes
    SessionMeta with origin=telegram."""
    monkeypatch.chdir(tmp_path)
    from aegis.core.manager import SessionManager

    mcp = MagicMock(url="http://localhost:0/")
    sm = SessionManager(
        agents={"sonnet": fake_agent}, default_agent="sonnet",
        make_session=_make_fake_session, mcp=mcp)
    sm.state_root = tmp_path
    # opening_prompt=None so we skip the asyncio.create_task branch
    # (no running loop / Textual context in this hermetic test).
    sm._sync_spawn("sonnet", handle="srv-h1", opening_prompt=None)
    sessions_dir = tmp_path / ".aegis" / "state" / "sessions"
    log_files = list(sessions_dir.glob("*.jsonl"))
    assert len(log_files) == 1
    replay = replay_events(tmp_path / ".aegis" / "state", "srv-h1")
    metas = [e for e in replay.events if isinstance(e, SessionMeta)]
    assert len(metas) == 1
    assert metas[0].origin == "telegram"
    # SessionClosed parity
    await sm.close("srv-h1")
    replay = replay_events(tmp_path / ".aegis" / "state", "srv-h1")
    closed = [e for e in replay.events
              if isinstance(e, SessionClosed)]
    assert len(closed) == 1
    assert closed[0].reason == "user"
```

Note: this requires importing `SessionClosed` at the top of the test file (already imported in Task 9's test).

- [ ] **Step 11.2: Run test to verify it fails**

Run: `uv run pytest tests/test_app_history_integration.py::test_session_manager_spawn_records_meta -v`
Expected: FAIL — no meta written.

- [ ] **Step 11.3: Implement the manager-side hook**

In `src/aegis/core/manager.py`, augment `_sync_spawn` to write `SessionMeta` eagerly with `origin="telegram"` (manager-spawned sessions always come from the headless/Telegram path; the TUI path uses `AegisApp._spawn` directly). Because `AgentSession` does not currently carry a first-user-message hook, write meta eagerly with the `opening_prompt` (always present for the headless path) as the preview.

Add at the top of the file:

```python
from datetime import datetime, timezone
from aegis.events import SessionMeta, SessionClosed
from aegis.state.session_log import append_event, append_meta
```

In `_sync_spawn`, after `self._sessions.append(s)`:

```python
        if self.state_root is not None:
            now_iso = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            preview = (opening_prompt or "").replace("\n", " ")[:200]
            try:
                append_meta(self.state_root / ".aegis" / "state",
                            SessionMeta(
                    handle=h, profile=slug, provider=agent.harness,
                    cwd=str(self.state_root),
                    created_at=now_iso, origin="telegram",
                    preview=preview))
            except Exception:
                pass
```

In `close(handle)`, before `self._sessions.remove(s)`:

```python
        if self.state_root is not None:
            now_iso = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            try:
                append_event(
                    self.state_root / ".aegis" / "state", handle,
                    SessionClosed(closed_at=now_iso, reason="user"))
            except Exception:
                pass
```

(`state_root` is None during tests that don't set it — the guard makes the hook a no-op for those.)

- [ ] **Step 11.4: Run test to verify it passes**

Run: `uv run pytest tests/test_app_history_integration.py::test_session_manager_spawn_records_meta -v`
Expected: PASS.

- [ ] **Step 11.5: Run the full suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 11.6: Commit**

```bash
git add src/aegis/core/manager.py tests/test_app_history_integration.py
git commit -m "feat(core): SessionManager writes SessionMeta and SessionClosed"
```

---

## Task 12: Live end-to-end resume test (Claude required)

**Files:**
- Create: `tests/test_history_live.py`

- [ ] **Step 12.1: Write the live test**

Create `tests/test_history_live.py`:

```python
"""End-to-end Ctrl+H resume against a real claude subprocess.

Auto-skips when `claude` is not on PATH. Marked `live`; run with
`uv run pytest -m live tests/test_history_live.py`.
"""
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _skip_if_no_claude():
    if shutil.which("claude") is None:
        pytest.skip("claude not on PATH")


@pytest.mark.asyncio
async def test_resume_from_history_retains_memory(tmp_path: Path,
                                                    monkeypatch):
    """Open, send a turn that establishes a fact, close, relaunch,
    Ctrl+H + resume, ask a follow-up that references the fact."""
    monkeypatch.chdir(tmp_path)
    from aegis.config import Agent
    from aegis.drivers.claude import ClaudeDriver
    from aegis.tui.app import AegisApp

    agent = Agent(harness="claude-code", model="claude-sonnet-4-5",
                  effort="low", permission="default")

    def _spawn_session(a, url, handle):
        drv = ClaudeDriver()
        return drv.spawn(a, str(tmp_path), url, handle)

    # First run — establish a fact.
    from unittest.mock import MagicMock
    mcp = MagicMock(url="http://localhost:0/", bind=MagicMock(),
                    start=MagicMock(), stop=MagicMock())
    app = AegisApp(
        agents={"sonnet": agent}, default_agent="sonnet",
        make_session=_spawn_session, mcp=mcp,
        drivers={"claude-code": ClaudeDriver()})
    async with app.run_test() as pilot:
        await pilot.pause(0.5)
        active = pilot.app._active
        active._submit("Remember this codeword: PURPLEFOX42. Reply OK.")
        # Wait for the turn to land.
        await pilot.pause(30)
        first_handle = active.handle
        await pilot.press("ctrl+w")
        await pilot.pause(1.0)

    # Second run — resume and probe memory.
    app2 = AegisApp(
        agents={"sonnet": agent}, default_agent="sonnet",
        make_session=_spawn_session, mcp=mcp,
        drivers={"claude-code": ClaudeDriver()})
    async with app2.run_test() as pilot:
        await pilot.pause(0.5)
        await pilot.press("ctrl+h")
        await pilot.pause(0.5)
        # Filter to the prior handle and press 'r' (force resume).
        for ch in first_handle:
            await pilot.press(ch)
        await pilot.pause(0.3)
        await pilot.press("r")
        await pilot.pause(2.0)
        active = pilot.app._active
        active._submit("What was the codeword?")
        await pilot.pause(30)
        # Pull the last assistant text from the pane's event log.
        from aegis.state.session_log import replay_events
        from aegis.events import AssistantText
        replay = replay_events(
            tmp_path / ".aegis" / "state", active.handle)
        texts = [e.text for e in replay.events
                 if isinstance(e, AssistantText)]
        assert any("PURPLEFOX42" in t for t in texts)
```

- [ ] **Step 12.2: Run the live test**

Run: `uv run pytest -m live tests/test_history_live.py -v`
Expected: PASS (when `claude` is on PATH; auto-skip otherwise).

- [ ] **Step 12.3: Run the full hermetic suite once more**

Run: `uv run pytest -q -m "not live"`
Expected: PASS — no regressions.

- [ ] **Step 12.4: Commit**

```bash
git add tests/test_history_live.py
git commit -m "test(history): live end-to-end Ctrl+H resume against claude"
```

---

## Wrap-up

- [ ] **Final verification**

Run the full hermetic suite:

```bash
cd repos/aegis
uv run pytest -q -m "not live"
```

Run the live suite if `claude` is on PATH:

```bash
uv run pytest -m live -q
```

- [ ] **Push to origin**

```bash
git push
```

- [ ] **Update `repos/aegis/TASKS.md`**

Add a line under the most recent "shipped" section noting:

```
**Session history shipped 2026-05-28:** Ctrl+H modal lists every
user-initiated session across process restarts; Enter jumps / resumes
(Claude) / opens fresh; SessionMeta + SessionClosed event variants
written into the existing per-handle event log. Spec/plan in
`docs/superpowers/{specs,plans}/2026-05-28-aegis-session-history*`.
```

Commit:

```bash
git add TASKS.md
git commit -m "docs(TASKS): note session-history shipped"
git push
```

---

# Self-review notes (for the executor)

- The `_RESUME_CAPABLE_PROVIDERS` set is intentionally hard-coded to `{"claude-code"}` in Task 7. A cleaner derivation is `{name for name, drv in self._drivers.items() if drv.supports_resume}`, but the test fixture in this plan does not always populate `self._drivers` (slice 1's test passes no drivers). If the executor populates drivers consistently, prefer the derived form.
- Task 5 writes meta eagerly at `_spawn` time; Task 10 moves it to first-user-message. Between Task 5 and Task 10 the test name changes (`test_spawn_writes_session_meta` → `test_first_message_writes_session_meta`). Task 10 explicitly deletes the old test. Do not leave both — they contradict.
- The first-message hook in Task 10 mutates `ConversationPane.__init__`'s signature. The existing call sites in `app.py` (`_spawn`, `_resume_agent_tabs`, `_SessionManagerAdapter.spawn`, the resume helper in Task 8) all need to handle the new optional kwarg. Only `_spawn` and the resume helper in Task 8 actually pass it; the queue-worker path leaves it None (correct — no meta for workers).
- `_close_pane` is called from both `action_close_tab` and `_close_pane` from inside `action_quit`-equivalent flows. `_record_session_closed` is idempotent (skips if a closed marker is already present), so duplicate calls are safe.
- The live test in Task 12 spawns real `claude` and asserts on token-level model output. It is `live`-marked and not in the default CI gate; treat its failure as a behavioural regression worth investigating, not a hard build break.
