# Session Persistence v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `aegis` resume the prior workspace by default (tabs, profiles, order, active focus) with genuine stateful resume of each underlying driver session. `aegis --clean` starts fresh. No visual-only fallback.

**Architecture:** Two new files under `.aegis/state/`: `workspace.json` (tab roster, rewritten on every tab change) and `sessions/<handle>.jsonl` (per-tab serialized `Event` stream for local redraw). A per-driver `supports_resume: bool` + `resume(session_id)` capability gate; tabs whose drivers can't resume are silently skipped with a startup line. v1 is effectively Claude Code only — Gemini/OpenCode ship with the flag off until ACP `session/load` is wired.

**Tech Stack:** Python 3.13, Textual 8.x, `uv run pytest`, dataclasses, asyncio. Reuses the queue/inbox JSONL envelope (`v` + payload).

**Spec:** `docs/superpowers/specs/2026-05-21-session-persistence-design.md`. Read it first.

**Work-on policy:** Commit straight to `main`. Push after every task — the VPS job clones from origin. Use `uv run pytest` (never bare `pytest`). One logical change per commit.

---

## File Map

**Create:**
- `src/aegis/state/__init__.py` — empty.
- `src/aegis/state/workspace.py` — `WorkspaceTab`, `Workspace`, `load`, `save`, `state_dir`, `CorruptWorkspace`.
- `src/aegis/state/session_log.py` — `append_event`, `replay_events`, `EventReplay` (events + `interrupted` flag).
- `src/aegis/state/event_codec.py` — `encode_event(Event) -> dict`, `decode_event(dict) -> Event`. Type-tagged.
- `tests/test_state_workspace.py`
- `tests/test_state_session_log.py`
- `tests/test_state_event_codec.py`
- `tests/test_resume_classification.py`
- `tests/test_resume_flow.py`
- `tests/test_cli_resume.py`

**Modify:**
- `src/aegis/drivers/base.py` — add `supports_resume: bool` class attr and `resume(session_id)` method to `HarnessDriver`.
- `src/aegis/drivers/claude.py` — set `supports_resume = True`; implement `resume()`; latch session_id in `ClaudeSession`.
- `src/aegis/drivers/gemini.py` — set `supports_resume = False`; `resume()` raises `NotImplementedError`.
- `src/aegis/drivers/opencode.py` — same as gemini.
- `src/aegis/core/session.py` — expose latched `session_id` on `AgentSession` (forwarded from underlying `HarnessSession`).
- `src/aegis/tui/app.py` — wire workspace save on tab change; resume flow on startup; `--clean` path.
- `src/aegis/tui/pane.py` — emit events into `session_log.append_event` via an extra observer; render `EventReplay` on resume (banner + interrupted marker if applicable).
- `src/aegis/cli.py` — add `--clean` flag (boolean, default False); pass through to TUI bootstrap.
- `src/aegis/__init__.py` — re-export `Workspace`, `WorkspaceTab` if public.

**Reference (read-only):**
- `src/aegis/queue/jsonl.py` — JSONL envelope pattern to mirror.
- `src/aegis/events.py` — `Event` union; `SystemInit.session_id` already parsed.
- `src/aegis/core/session.py` — `add_event_observer` extension point.

---

## Slice 1 — Persistence substrate (no resume yet)

Goal: a normal `aegis` session writes `workspace.json` + `sessions/<handle>.jsonl` to disk with the right shape. Nothing reads them yet.

### Task 1: Event codec round-trip

**Files:**
- Create: `src/aegis/state/__init__.py`
- Create: `src/aegis/state/event_codec.py`
- Create: `tests/test_state_event_codec.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_state_event_codec.py
import pytest
from aegis.events import (
    SystemInit, AssistantText, AssistantThinking, ToolUse, ToolResult,
    Result, Unknown, TokenUsage,
)
from aegis.state.event_codec import encode_event, decode_event


def _roundtrip(ev):
    return decode_event(encode_event(ev))


def test_system_init_roundtrip():
    e = SystemInit(session_id="abc-123")
    assert _roundtrip(e) == e


def test_assistant_text_with_usage_roundtrip():
    u = TokenUsage(input=10, cache_creation=5, cache_read=80, output=42)
    e = AssistantText(text="hi", usage=u)
    assert _roundtrip(e) == e


def test_assistant_text_no_usage_roundtrip():
    e = AssistantText(text="plain", usage=None)
    assert _roundtrip(e) == e


def test_assistant_thinking_roundtrip():
    e = AssistantThinking(text="…", usage=None)
    assert _roundtrip(e) == e


def test_tool_use_roundtrip():
    e = ToolUse(name="Read", summary="src/x.py", usage=None)
    assert _roundtrip(e) == e


def test_tool_result_roundtrip():
    e = ToolResult(text="ok", is_error=False)
    assert _roundtrip(e) == e


def test_tool_result_error_roundtrip():
    e = ToolResult(text="boom", is_error=True)
    assert _roundtrip(e) == e


def test_result_roundtrip():
    u = TokenUsage(input=1, cache_creation=2, cache_read=3, output=4)
    e = Result(duration_ms=1234, is_error=False,
               input_tokens=1, output_tokens=4, usage=u)
    assert _roundtrip(e) == e


def test_unknown_roundtrip():
    e = Unknown(raw='{"weird": true}')
    assert _roundtrip(e) == e


def test_decode_rejects_missing_type():
    with pytest.raises(ValueError):
        decode_event({"text": "no type"})


def test_decode_rejects_unknown_type():
    with pytest.raises(ValueError):
        decode_event({"t": "MysteryEvent"})
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_state_event_codec.py -q`
Expected: collection error or all 11 fail (module missing).

- [ ] **Step 3: Implement the codec**

```python
# src/aegis/state/event_codec.py
"""Serialize aegis Event dataclasses to/from JSON-safe dicts.

Used by session_log to persist a tab's event stream for local
transcript redraw on resume. Type tag is the dataclass name under
``t``; field names mirror the dataclass.
"""
from __future__ import annotations

from aegis.events import (
    AssistantText, AssistantThinking, Event, Result, SystemInit,
    TokenUsage, ToolResult, ToolUse, Unknown,
)


def _encode_usage(u: TokenUsage | None) -> dict | None:
    if u is None:
        return None
    return {"input": u.input, "cache_creation": u.cache_creation,
            "cache_read": u.cache_read, "output": u.output}


def _decode_usage(d: dict | None) -> TokenUsage | None:
    if d is None:
        return None
    return TokenUsage(input=d["input"],
                      cache_creation=d["cache_creation"],
                      cache_read=d["cache_read"],
                      output=d["output"])


def encode_event(ev: Event) -> dict:
    if isinstance(ev, SystemInit):
        return {"t": "SystemInit", "session_id": ev.session_id}
    if isinstance(ev, AssistantText):
        return {"t": "AssistantText", "text": ev.text,
                "usage": _encode_usage(ev.usage)}
    if isinstance(ev, AssistantThinking):
        return {"t": "AssistantThinking", "text": ev.text,
                "usage": _encode_usage(ev.usage)}
    if isinstance(ev, ToolUse):
        return {"t": "ToolUse", "name": ev.name, "summary": ev.summary,
                "usage": _encode_usage(ev.usage)}
    if isinstance(ev, ToolResult):
        return {"t": "ToolResult", "text": ev.text,
                "is_error": ev.is_error}
    if isinstance(ev, Result):
        return {"t": "Result", "duration_ms": ev.duration_ms,
                "is_error": ev.is_error,
                "input_tokens": ev.input_tokens,
                "output_tokens": ev.output_tokens,
                "usage": _encode_usage(ev.usage)}
    if isinstance(ev, Unknown):
        return {"t": "Unknown", "raw": ev.raw}
    raise ValueError(f"unknown event type: {type(ev).__name__}")


def decode_event(d: dict) -> Event:
    t = d.get("t")
    if t is None:
        raise ValueError("event dict missing type tag 't'")
    if t == "SystemInit":
        return SystemInit(session_id=d.get("session_id"))
    if t == "AssistantText":
        return AssistantText(text=d["text"], usage=_decode_usage(d.get("usage")))
    if t == "AssistantThinking":
        return AssistantThinking(text=d["text"],
                                 usage=_decode_usage(d.get("usage")))
    if t == "ToolUse":
        return ToolUse(name=d["name"], summary=d["summary"],
                       usage=_decode_usage(d.get("usage")))
    if t == "ToolResult":
        return ToolResult(text=d["text"], is_error=d["is_error"])
    if t == "Result":
        return Result(duration_ms=d.get("duration_ms"),
                      is_error=d["is_error"],
                      input_tokens=d.get("input_tokens"),
                      output_tokens=d.get("output_tokens"),
                      usage=_decode_usage(d.get("usage")))
    if t == "Unknown":
        return Unknown(raw=d["raw"])
    raise ValueError(f"unknown event type tag: {t!r}")
```

Also write `src/aegis/state/__init__.py` as an empty file.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_state_event_codec.py -q`
Expected: 11 passed.

- [ ] **Step 5: Commit + push**

```bash
git add src/aegis/state/__init__.py src/aegis/state/event_codec.py \
        tests/test_state_event_codec.py
git commit -m "feat(state): event codec for session log persistence"
git push origin main
```

---

### Task 2: `workspace.json` load/save

**Files:**
- Create: `src/aegis/state/workspace.py`
- Create: `tests/test_state_workspace.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_state_workspace.py
import json
import pytest

from aegis.state.workspace import (
    CorruptWorkspace, Workspace, WorkspaceTab,
    load, save, state_dir,
)


def test_state_dir_is_project_rooted(tmp_path):
    assert state_dir(tmp_path) == tmp_path / ".aegis" / "state"


def test_load_missing_returns_none(tmp_path):
    assert load(state_dir(tmp_path)) is None


def test_save_then_load_roundtrip(tmp_path):
    sd = state_dir(tmp_path)
    ws = Workspace(
        active_handle="lucid-knuth",
        tabs=[
            WorkspaceTab(handle="lucid-knuth", profile="default",
                         order=0, provider="claude-code",
                         session_id="abc-123",
                         created_at="2026-05-21T14:00:00Z"),
            WorkspaceTab(handle="wry-hopper", profile="fast",
                         order=1, provider="gemini",
                         session_id=None,
                         created_at="2026-05-21T15:30:00Z"),
        ],
    )
    save(sd, ws)
    out = load(sd)
    assert out == ws


def test_save_creates_parent_dirs(tmp_path):
    sd = state_dir(tmp_path)
    assert not sd.exists()
    ws = Workspace(active_handle=None, tabs=[])
    save(sd, ws)
    assert (sd / "workspace.json").exists()


def test_save_is_atomic_no_partial_file_on_crash(tmp_path, monkeypatch):
    """A partway-through save must not leave a half-written workspace.json."""
    sd = state_dir(tmp_path)
    sd.mkdir(parents=True)
    (sd / "workspace.json").write_text(
        json.dumps({"version": 1, "saved_at": "old",
                    "active_handle": None, "tabs": []}))
    # Simulate a write that fails partway: monkeypatch os.replace to raise.
    import os
    orig = os.replace
    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    ws = Workspace(active_handle="x", tabs=[])
    with pytest.raises(OSError):
        save(sd, ws)
    monkeypatch.setattr(os, "replace", orig)
    # Original file untouched.
    on_disk = json.loads((sd / "workspace.json").read_text())
    assert on_disk["saved_at"] == "old"


def test_load_corrupt_raises(tmp_path):
    sd = state_dir(tmp_path)
    sd.mkdir(parents=True)
    (sd / "workspace.json").write_text("{not json")
    with pytest.raises(CorruptWorkspace):
        load(sd)


def test_load_wrong_version_raises(tmp_path):
    sd = state_dir(tmp_path)
    sd.mkdir(parents=True)
    (sd / "workspace.json").write_text(
        json.dumps({"version": 99, "saved_at": "x",
                    "active_handle": None, "tabs": []}))
    with pytest.raises(CorruptWorkspace):
        load(sd)
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_state_workspace.py -q`
Expected: collection error.

- [ ] **Step 3: Implement workspace.py**

```python
# src/aegis/state/workspace.py
"""Workspace persistence: the tab roster on disk.

Single file at ``.aegis/state/workspace.json`` rewritten atomically on
every tab change. Crash-survivable single source of truth.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_VERSION = 1


class CorruptWorkspace(Exception):
    """workspace.json exists but is unparseable or schema-mismatched."""


@dataclass(frozen=True)
class WorkspaceTab:
    handle: str
    profile: str
    order: int
    provider: str
    session_id: str | None
    created_at: str


@dataclass(frozen=True)
class Workspace:
    active_handle: str | None
    tabs: list[WorkspaceTab] = field(default_factory=list)


def state_dir(cwd: Path) -> Path:
    return cwd / ".aegis" / "state"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save(state_dir_path: Path, ws: Workspace) -> None:
    state_dir_path.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": WORKSPACE_VERSION,
        "saved_at": _now_iso(),
        "active_handle": ws.active_handle,
        "tabs": [asdict(t) for t in ws.tabs],
    }
    target = state_dir_path / "workspace.json"
    # Atomic write: tmp file + rename, so a crash mid-write never leaves
    # a half-written workspace.json behind.
    fd, tmp = tempfile.mkstemp(prefix=".workspace.", suffix=".tmp",
                               dir=str(state_dir_path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        os.replace(tmp, target)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load(state_dir_path: Path) -> Workspace | None:
    p = state_dir_path / "workspace.json"
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError) as e:
        raise CorruptWorkspace(f"unparseable workspace.json: {e}") from e
    if not isinstance(raw, dict) or raw.get("version") != WORKSPACE_VERSION:
        raise CorruptWorkspace(
            f"workspace.json version mismatch (expected {WORKSPACE_VERSION}, "
            f"got {raw.get('version') if isinstance(raw, dict) else '?'})")
    try:
        tabs = [
            WorkspaceTab(
                handle=t["handle"],
                profile=t["profile"],
                order=t["order"],
                provider=t["provider"],
                session_id=t.get("session_id"),
                created_at=t["created_at"],
            )
            for t in raw["tabs"]
        ]
    except (KeyError, TypeError) as e:
        raise CorruptWorkspace(f"malformed tab record: {e}") from e
    return Workspace(active_handle=raw.get("active_handle"), tabs=tabs)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_state_workspace.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit + push**

```bash
git add src/aegis/state/workspace.py tests/test_state_workspace.py
git commit -m "feat(state): workspace.json atomic load/save with corruption detection"
git push origin main
```

---

### Task 3: `sessions/<handle>.jsonl` append + replay (with interrupted detection)

**Files:**
- Create: `src/aegis/state/session_log.py`
- Create: `tests/test_state_session_log.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_state_session_log.py
from aegis.events import (
    AssistantText, Result, SystemInit, TokenUsage, ToolResult, ToolUse,
)
from aegis.state.session_log import (
    EventReplay, append_event, replay_events, session_log_path,
)


def test_path_is_handle_scoped(tmp_path):
    assert session_log_path(tmp_path, "lucid-knuth") == \
        tmp_path / "sessions" / "lucid-knuth.jsonl"


def test_append_then_replay_returns_events(tmp_path):
    h = "lucid-knuth"
    append_event(tmp_path, h, SystemInit(session_id="abc"))
    append_event(tmp_path, h, AssistantText(text="hi", usage=None))
    append_event(tmp_path, h, Result(duration_ms=1, is_error=False))
    r = replay_events(tmp_path, h)
    assert isinstance(r, EventReplay)
    assert [type(e).__name__ for e in r.events] == [
        "SystemInit", "AssistantText", "Result"]
    assert r.interrupted is False


def test_replay_missing_returns_empty(tmp_path):
    r = replay_events(tmp_path, "ghost")
    assert r.events == []
    assert r.interrupted is False


def test_replay_marks_interrupted_when_no_result_after_assistant(tmp_path):
    h = "wry-hopper"
    append_event(tmp_path, h, SystemInit(session_id="xyz"))
    append_event(tmp_path, h, AssistantText(text="started…", usage=None))
    # No Result — process died mid-turn.
    r = replay_events(tmp_path, h)
    assert r.interrupted is True
    # Events still returned in full; renderer decides how to mark.
    assert [type(e).__name__ for e in r.events] == [
        "SystemInit", "AssistantText"]


def test_replay_not_interrupted_if_last_was_result(tmp_path):
    h = "h"
    append_event(tmp_path, h, AssistantText(text="x", usage=None))
    append_event(tmp_path, h, Result(duration_ms=1, is_error=False))
    assert replay_events(tmp_path, h).interrupted is False


def test_replay_not_interrupted_for_idle_session(tmp_path):
    """A session that only saw SystemInit (no turns yet) is not 'interrupted'."""
    h = "h"
    append_event(tmp_path, h, SystemInit(session_id="abc"))
    assert replay_events(tmp_path, h).interrupted is False


def test_replay_skips_blank_lines(tmp_path):
    h = "h"
    append_event(tmp_path, h, SystemInit(session_id="abc"))
    p = session_log_path(tmp_path, h)
    p.write_text(p.read_text() + "\n\n")
    assert len(replay_events(tmp_path, h).events) == 1


def test_envelope_carries_version_and_timestamp(tmp_path):
    import json
    h = "h"
    append_event(tmp_path, h, SystemInit(session_id="x"))
    line = session_log_path(tmp_path, h).read_text().strip()
    rec = json.loads(line)
    assert rec["v"] == 1
    assert "aegis_ts" in rec
    assert rec["event"]["t"] == "SystemInit"
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_state_session_log.py -q`
Expected: collection error.

- [ ] **Step 3: Implement session_log.py**

```python
# src/aegis/state/session_log.py
"""Per-tab event-stream persistence for transcript replay on resume.

Mirrors the queue/inbox JSONL envelope: each line is
``{"v": 1, "aegis_ts": <iso>, "event": <encoded-event>}``. Replay
returns the decoded ``Event`` list plus an ``interrupted`` flag set
when the file ends after an assistant turn with no terminating
``Result`` — used by the renderer to mark the last turn ⚠ interrupted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from aegis.events import (
    AssistantText, AssistantThinking, Event, Result, ToolUse,
)
from aegis.state.event_codec import decode_event, encode_event

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class EventReplay:
    events: list[Event]
    interrupted: bool


def session_log_path(state_dir_path: Path, handle: str) -> Path:
    return state_dir_path / "sessions" / f"{handle}.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def append_event(state_dir_path: Path, handle: str, ev: Event) -> None:
    p = session_log_path(state_dir_path, handle)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"v": SCHEMA_VERSION, "aegis_ts": _now_iso(),
           "event": encode_event(ev)}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


# Event types that indicate an in-progress turn (must be followed by a
# Result to be considered complete).
_TURN_EVENTS = (AssistantText, AssistantThinking, ToolUse)


def replay_events(state_dir_path: Path, handle: str) -> EventReplay:
    p = session_log_path(state_dir_path, handle)
    if not p.exists():
        return EventReplay(events=[], interrupted=False)
    events: list[Event] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            events.append(decode_event(rec["event"]))
    interrupted = False
    if events:
        # Scan backwards: was the last "non-Result" event part of a turn?
        last_turn_evt = None
        for e in reversed(events):
            if isinstance(e, Result):
                break
            if isinstance(e, _TURN_EVENTS):
                last_turn_evt = e
                break
        interrupted = last_turn_evt is not None
    return EventReplay(events=events, interrupted=interrupted)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_state_session_log.py -q`
Expected: 8 passed.

- [ ] **Step 5: Commit + push**

```bash
git add src/aegis/state/session_log.py tests/test_state_session_log.py
git commit -m "feat(state): per-tab session_log append + replay with interrupted detection"
git push origin main
```

---

### Task 4: Wire writes into the running TUI

**Files:**
- Modify: `src/aegis/tui/app.py` — call `save_workspace()` on tab change; emit `session_log.append_event` via per-pane observer.
- Modify: `src/aegis/tui/pane.py` — add an event observer that writes to session_log.
- Create: `tests/test_workspace_writes_on_tab_change.py` — integration-flavored unit test using a synthetic spawn/close/activate cycle.

- [ ] **Step 1: Write the failing test**

Read `src/aegis/tui/app.py` first to understand the spawn/close/activate paths. The test simulates those state mutations and asserts the workspace.json reflects them.

```python
# tests/test_workspace_writes_on_tab_change.py
"""Workspace.json reflects the live tab roster.

Uses a stub AegisApp surface: we don't run Textual, we just exercise the
state-mutation hooks (open, close, activate, reorder) that the real app
also calls, and assert the on-disk workspace.json matches after each.
"""
from pathlib import Path

from aegis.state.workspace import Workspace, WorkspaceTab, load, state_dir
from aegis.tui.app import write_workspace_snapshot  # to be added


def _tab(handle, profile, order, provider, sid="sid-" + "x"):
    return WorkspaceTab(handle=handle, profile=profile, order=order,
                        provider=provider, session_id=sid,
                        created_at="2026-05-21T00:00:00Z")


def test_snapshot_reflects_single_tab(tmp_path):
    sd = state_dir(tmp_path)
    tabs = [_tab("lucid-knuth", "default", 0, "claude-code")]
    write_workspace_snapshot(sd, tabs=tabs, active_handle="lucid-knuth")
    ws = load(sd)
    assert ws == Workspace(active_handle="lucid-knuth", tabs=tabs)


def test_snapshot_after_close_drops_tab(tmp_path):
    sd = state_dir(tmp_path)
    tabs = [_tab("a", "p", 0, "claude-code"),
            _tab("b", "p", 1, "claude-code")]
    write_workspace_snapshot(sd, tabs=tabs, active_handle="b")
    write_workspace_snapshot(sd, tabs=[tabs[1]], active_handle="b")
    assert load(sd).tabs == [tabs[1]]


def test_snapshot_after_reorder(tmp_path):
    sd = state_dir(tmp_path)
    tabs = [_tab("a", "p", 0, "claude-code"),
            _tab("b", "p", 1, "claude-code")]
    write_workspace_snapshot(sd, tabs=tabs, active_handle="a")
    reordered = [_tab("b", "p", 0, "claude-code"),
                 _tab("a", "p", 1, "claude-code")]
    write_workspace_snapshot(sd, tabs=reordered, active_handle="a")
    assert load(sd).tabs == reordered


def test_session_log_observer_writes_events_for_handle(tmp_path):
    """Each pane subscribes an observer that appends incoming events."""
    from aegis.events import AssistantText, Result, SystemInit
    from aegis.state.session_log import replay_events
    from aegis.tui.pane import make_session_log_observer  # to be added

    sd = state_dir(tmp_path)
    obs = make_session_log_observer(sd, handle="lucid-knuth")

    class _FakeSession:
        handle = "lucid-knuth"

    sess = _FakeSession()
    obs(sess, SystemInit(session_id="xyz"))
    obs(sess, AssistantText(text="hi", usage=None))
    obs(sess, Result(duration_ms=1, is_error=False))

    rep = replay_events(sd, "lucid-knuth")
    assert [type(e).__name__ for e in rep.events] == [
        "SystemInit", "AssistantText", "Result"]
    assert rep.interrupted is False
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_workspace_writes_on_tab_change.py -q`
Expected: import errors (`write_workspace_snapshot`, `make_session_log_observer` do not exist).

- [ ] **Step 3: Add the two helpers and wire them**

In `src/aegis/tui/app.py`, add:

```python
# top-level helper (importable by tests + by app code)
def write_workspace_snapshot(state_dir_path, tabs, active_handle):
    """Persist the current tab roster to workspace.json."""
    from aegis.state.workspace import Workspace, save
    save(state_dir_path, Workspace(active_handle=active_handle, tabs=list(tabs)))
```

Then in `AegisApp`:
- On `_spawn`, `_close_pane`, `_activate`, and any reorder action, call `write_workspace_snapshot` with the current tab roster derived from live `ConversationPane` instances.
- Resolve each pane's `WorkspaceTab` from: `handle`, `agent_slug`, `order` (tab index), `provider` (`pane.session.agent.provider.__class__.name` or a similar accessor — check what's available), `session_id` (Task 5 will populate; for now `None`), and a `created_at` set at spawn time.
- Compute `state_dir_path = state_dir(Path(os.getcwd()))` once at app init; cache on `self._state_dir`.

In `src/aegis/tui/pane.py`, add:

```python
def make_session_log_observer(state_dir_path, handle):
    """Returns an EventCb that appends every event to the per-tab JSONL."""
    from aegis.state.session_log import append_event

    def _obs(_sess, ev):
        try:
            append_event(state_dir_path, handle, ev)
        except Exception:
            # Persistence must never break the live render. Drop the event
            # rather than propagating I/O failures up into the TUI loop.
            pass

    return _obs
```

When a `ConversationPane` mounts its `AgentSession`, register the observer via `session.add_event_observer(make_session_log_observer(state_dir_path, handle))`. The app passes `state_dir_path` to the pane at construction.

If the pane already accepts construction args via the app, add `state_dir_path` to that signature; otherwise expose via `self.app._state_dir` lookup.

- [ ] **Step 4: Run the full suite to verify pass + no regressions**

```bash
uv run pytest -q
```

Expected: previous 311 + new tests pass; no failures.

- [ ] **Step 5: Manual smoke (optional, recommended)**

```bash
cd /tmp && mkdir -p aegis-smoke && cd aegis-smoke
# Init a minimal .aegis.py against your installed claude
uv --directory /home/apiad/Workspace/repos/aegis run aegis init
# Skip if interactive; or write .aegis.py by hand
ls .aegis/state/  # should not exist yet
uv --directory /home/apiad/Workspace/repos/aegis run aegis
# Open one tab, send "hi", quit (Ctrl+Q)
ls .aegis/state/
cat .aegis/state/workspace.json
ls .aegis/state/sessions/
```

Expected: `workspace.json` carries the tab roster; `sessions/<handle>.jsonl` carries the event stream.

- [ ] **Step 6: Commit + push**

```bash
git add src/aegis/tui/app.py src/aegis/tui/pane.py \
        tests/test_workspace_writes_on_tab_change.py
git commit -m "feat(state): persist workspace + session events live during a run"
git push origin main
```

---

## Slice 2 — Driver resume capability

Goal: `HarnessDriver` declares `supports_resume`. `ClaudeDriver` latches its session_id and offers a `resume()` path that builds the same argv with `--resume <session_id>` prepended. ACP drivers stub off.

### Task 5: Capability flag on the driver protocol

**Files:**
- Modify: `src/aegis/drivers/base.py`
- Modify: `src/aegis/drivers/claude.py`
- Modify: `src/aegis/drivers/gemini.py`
- Modify: `src/aegis/drivers/opencode.py`
- Create: `tests/test_driver_resume_capability.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_driver_resume_capability.py
from aegis.drivers.claude import ClaudeDriver
from aegis.drivers.gemini import GeminiDriver
from aegis.drivers.opencode import OpencodeDriver


def test_claude_supports_resume():
    assert ClaudeDriver().supports_resume is True


def test_gemini_does_not_support_resume_yet():
    # Will flip once ACP session/load is wired to gemini-cli.
    assert GeminiDriver().supports_resume is False


def test_opencode_does_not_support_resume_yet():
    assert OpencodeDriver().supports_resume is False
```

(Adjust class names to whatever the drivers actually export — peek at `src/aegis/drivers/__init__.py`.)

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_driver_resume_capability.py -q`

- [ ] **Step 3: Add `supports_resume` to each driver**

In `src/aegis/drivers/base.py`:

```python
class HarnessDriver(abc.ABC):
    """Translates a harness-agnostic Agent into a concrete session."""

    # Per-driver capability flag. True iff this driver can rebuild a
    # session that the underlying CLI considers a continuation of a
    # prior conversation — model memory intact, not a fresh start.
    supports_resume: bool = False

    @abc.abstractmethod
    def build_argv(self, agent: Agent, cwd: str,
                   mcp_url: str, handle: str) -> list[str]: ...

    @abc.abstractmethod
    def session(self, agent: Agent, cwd: str,
                mcp_url: str, handle: str) -> HarnessSession: ...

    def resume(self, agent: Agent, cwd: str, mcp_url: str, handle: str,
               session_id: str) -> HarnessSession:
        """Build a session bound to an existing driver-side conversation.
        Default implementation raises — only resume-capable drivers
        override."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support session resume")
```

In `claude.py`, set `supports_resume = True` and implement `resume()` (see Task 6). In `gemini.py` and `opencode.py`, the default `False` is fine; explicitly setting `supports_resume = False` on the class for clarity is encouraged.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_driver_resume_capability.py -q`

- [ ] **Step 5: Commit + push**

```bash
git add src/aegis/drivers/base.py src/aegis/drivers/claude.py \
        src/aegis/drivers/gemini.py src/aegis/drivers/opencode.py \
        tests/test_driver_resume_capability.py
git commit -m "feat(drivers): supports_resume capability flag + resume() seam"
git push origin main
```

---

### Task 6: `ClaudeDriver.resume()` + session_id latching

**Files:**
- Modify: `src/aegis/drivers/claude.py`
- Modify: `src/aegis/drivers/base.py` — add `session_id` property to `HarnessSession`.
- Modify: `src/aegis/core/session.py` — expose `session_id` on `AgentSession`.
- Create: `tests/test_claude_resume_argv.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_claude_resume_argv.py
from aegis.config import Agent, Effort, Permission
from aegis.drivers.claude import ClaudeDriver, ClaudeSession


def _agent():
    # Use whatever the minimal Agent constructor takes; mirror the queue tests.
    from aegis.drivers.claude import ClaudeDriver
    return Agent(provider=type("P", (), {
        "model": "opus", "effort": Effort.high,
        "permission": Permission.auto, "driver": ClaudeDriver()})(),
        # adjust fields to match real Agent dataclass
    )


def test_resume_argv_has_resume_session_id_flag():
    d = ClaudeDriver()
    # build a normal argv
    base = d.build_argv(_agent(), cwd="/tmp", mcp_url="http://x", handle="h")
    # build a resume argv
    sess = d.resume(_agent(), cwd="/tmp", mcp_url="http://x",
                    handle="h", session_id="abc-123")
    assert isinstance(sess, ClaudeSession)
    # private attr ok for a focused test
    assert "--resume" in sess._argv
    idx = sess._argv.index("--resume")
    assert sess._argv[idx + 1] == "abc-123"
    # all other claude flags preserved
    for flag in ("--input-format", "--output-format", "--model",
                 "--permission-mode", "--mcp-config"):
        assert flag in sess._argv
```

(Read `tests/test_state_session_log.py` for the `_agent()` shape if needed; reuse the existing test-Agent factory if there is one.)

Also add:

```python
def test_claude_session_latches_session_id_from_systeminit():
    """The ClaudeSession exposes session_id once the first SystemInit arrives."""
    # This needs a small stub: feed a SystemInit through the parsed-event
    # queue. If easier, refactor a tiny helper on ClaudeSession that
    # accepts an event directly. The test asserts session.session_id
    # changes from None to "abc-123" after that event is processed.
    ...
```

(If wiring a synthetic ClaudeSession is too heavy in unit-test isolation, skip this sub-test and rely on the integration test in Slice 5.)

- [ ] **Step 2: Run to verify fail**

- [ ] **Step 3: Implement**

In `src/aegis/drivers/claude.py`:

```python
class ClaudeSession(HarnessSession):
    def __init__(self, argv: list[str], cwd: str) -> None:
        self._argv = argv
        self._cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._reader: asyncio.Task | None = None
        self._session_id: str | None = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def _pump_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            async for raw in self._proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                ev = parse(line)
                # Latch session_id on the first SystemInit so the workspace
                # snapshot can persist it for --resume.
                from aegis.events import SystemInit
                if isinstance(ev, SystemInit) and ev.session_id:
                    self._session_id = ev.session_id
                await self._queue.put(ev)
        except Exception:
            pass
        finally:
            await self._queue.put(None)


class ClaudeDriver(HarnessDriver):
    supports_resume = True

    # build_argv as before

    def session(self, agent, cwd, mcp_url, handle):
        return ClaudeSession(self.build_argv(agent, cwd, mcp_url, handle), cwd)

    def resume(self, agent, cwd, mcp_url, handle, session_id):
        argv = self.build_argv(agent, cwd, mcp_url, handle)
        # Insert --resume <session_id> right after the "claude -p" prefix.
        return ClaudeSession(argv[:2] + ["--resume", session_id] + argv[2:],
                             cwd)
```

In `src/aegis/drivers/base.py`, add an optional `session_id` property:

```python
class HarnessSession(abc.ABC):
    @property
    def session_id(self) -> str | None:
        """The driver-assigned session id, if known. Latched lazily as
        the upstream protocol reveals it (e.g. stream-json's first
        SystemInit). Returns None for drivers that don't expose one or
        before the first event arrives."""
        return None
    # rest unchanged
```

In `src/aegis/core/session.py`:

```python
class AgentSession:
    # ... existing fields ...

    @property
    def session_id(self) -> str | None:
        return self._session.session_id
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/test_claude_resume_argv.py -q
uv run pytest -q  # full suite for regressions
```

- [ ] **Step 5: Commit + push**

```bash
git add src/aegis/drivers/claude.py src/aegis/drivers/base.py \
        src/aegis/core/session.py tests/test_claude_resume_argv.py
git commit -m "feat(drivers): ClaudeDriver.resume() + session_id latching from SystemInit"
git push origin main
```

---

### Task 7: Workspace snapshot picks up session_id from live panes

**Files:**
- Modify: `src/aegis/tui/app.py` — `write_workspace_snapshot` callers now read `pane.session.session_id`.
- Create: `tests/test_workspace_snapshot_includes_session_id.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workspace_snapshot_includes_session_id.py
"""When a pane's underlying session has latched a session_id, the next
workspace snapshot carries it."""
from aegis.state.workspace import WorkspaceTab, load, state_dir
from aegis.tui.app import write_workspace_snapshot


def test_session_id_propagates_into_snapshot(tmp_path):
    sd = state_dir(tmp_path)
    tabs = [WorkspaceTab(
        handle="lucid-knuth", profile="default", order=0,
        provider="claude-code", session_id="abc-123",
        created_at="2026-05-21T00:00:00Z")]
    write_workspace_snapshot(sd, tabs=tabs, active_handle="lucid-knuth")
    ws = load(sd)
    assert ws.tabs[0].session_id == "abc-123"
```

Add an integration-style test that constructs a `WorkspaceTab` from a stub `AgentSession`-like object via whatever helper `app.py` uses; if there's a `_pane_to_tab(pane)` helper, exercise that directly.

- [ ] **Step 2-5: Standard TDD cycle.**

The implementation is just: in the `app.py` snapshot trigger, derive each tab from the current panes by reading `pane.session.session_id` (None if not yet latched — fine, it gets re-snapshotted on the next state change once the SystemInit lands).

Commit message: `feat(state): snapshot now carries the driver session_id per tab`. Push.

---

## Slice 3 — CLI shape (`--clean`, no-workspace, corrupt-workspace)

Goal: `aegis --clean` flag wired; clear messaging on no-workspace and corrupt-workspace paths. Resume logic still not present — that lands in Slice 4. This slice just adds the flag and the messages, with the absence of `workspace.json` being the only branch that actually changes behavior so far.

### Task 8: `--clean` flag + path classification

**Files:**
- Modify: `src/aegis/cli.py` — add `--clean: bool = typer.Option(False, "--clean", help="Ignore prior workspace state; start fresh")`. Pass through to the TUI entry point.
- Modify: `src/aegis/tui/app.py` — accept a `clean: bool` parameter; if `clean` is True, ignore `workspace.json` even if present.
- Create: `tests/test_cli_clean_flag.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_clean_flag.py
from typer.testing import CliRunner
from aegis.cli import app

runner = CliRunner()


def test_clean_flag_recognized():
    """--clean is a known flag; invoking with it on an empty cwd is fine."""
    # The wizard requires .aegis.py — so we test by exercising --help.
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--clean" in result.output


def test_clean_flag_does_not_conflict_with_init(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--clean"])
    # init does not accept --clean; either typer rejects, or our wiring
    # ignores it. Either is fine; just don't crash hard.
    assert result.exit_code in (0, 2)
```

For the actual behavioral check, add a unit test against a `pick_workspace_to_resume(state_dir_path, clean: bool) -> Workspace | None` pure function:

```python
def test_pick_workspace_returns_none_when_clean(tmp_path):
    from aegis.state.workspace import (
        Workspace, WorkspaceTab, save, state_dir,
    )
    from aegis.tui.app import pick_workspace_to_resume

    sd = state_dir(tmp_path)
    save(sd, Workspace(active_handle="x", tabs=[]))
    assert pick_workspace_to_resume(sd, clean=True) is None
    assert pick_workspace_to_resume(sd, clean=False) is not None


def test_pick_workspace_returns_none_when_missing(tmp_path):
    from aegis.state.workspace import state_dir
    from aegis.tui.app import pick_workspace_to_resume
    assert pick_workspace_to_resume(state_dir(tmp_path), clean=False) is None
```

- [ ] **Step 2-4: Standard TDD cycle.**

Implement `pick_workspace_to_resume(state_dir_path, clean)` in `app.py`:

```python
def pick_workspace_to_resume(state_dir_path, clean):
    """Return the Workspace to resume, or None for a fresh start.

    None can mean: --clean was passed, no workspace.json exists, or the
    file was empty. CorruptWorkspace bubbles up to the caller, which is
    responsible for printing a clear error and exiting nonzero."""
    if clean:
        return None
    from aegis.state.workspace import load
    return load(state_dir_path)
```

In `cli.py`, add `--clean` to the root command and pass it down. In `AegisApp.__init__` or whatever bootstrap function the CLI calls, accept `clean: bool = False` and pass to `pick_workspace_to_resume` (used by Slice 4).

- [ ] **Step 5: Commit + push**

`feat(cli): --clean flag + pick_workspace_to_resume helper`.

---

### Task 9: Corrupt-workspace exits clean with a clear pointer

**Files:**
- Modify: `src/aegis/cli.py` — catch `CorruptWorkspace`, print the message + suggestion, exit nonzero.
- Create: `tests/test_cli_corrupt_workspace.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_corrupt_workspace.py
from typer.testing import CliRunner
from aegis.cli import app
from aegis.state.workspace import state_dir

runner = CliRunner()


def test_corrupt_workspace_exits_nonzero_with_hint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Write a minimal .aegis.py so the wizard isn't needed
    (tmp_path / ".aegis.py").write_text(
        "from aegis import Agent, ClaudeCode\n"
        "agents = {'default': Agent(provider=ClaudeCode(model='opus'))}\n"
        "default_agent = 'default'\n"
    )
    sd = state_dir(tmp_path)
    sd.mkdir(parents=True)
    (sd / "workspace.json").write_text("{not json")
    result = runner.invoke(app, [])
    assert result.exit_code != 0
    assert "workspace.json" in result.output
    assert "--clean" in result.output
```

- [ ] **Step 2-5: Standard TDD cycle.**

Implementation in the root command:

```python
try:
    ws = pick_workspace_to_resume(state_dir(Path.cwd()), clean=clean)
except CorruptWorkspace as e:
    typer.echo(f"aegis: {e}", err=True)
    typer.echo("hint: re-run with `aegis --clean` to ignore prior state.",
               err=True)
    raise typer.Exit(code=2)
```

Commit: `feat(cli): exit cleanly with --clean hint on corrupt workspace.json`. Push.

---

## Slice 4 — Resume flow (the actual restore)

Goal: `aegis` startup, given a workspace, classifies each tab as resumable / skipped, opens the resumable ones via `driver.resume()`, redraws each transcript from JSONL, and shows a one-line banner in the active pane.

### Task 10: Pure classification function

**Files:**
- Create: `src/aegis/tui/resume_plan.py` — `ResumePlan`, `TabPlan`, `plan_resume(workspace, agents, drivers)`.
- Create: `tests/test_resume_classification.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_resume_classification.py
from aegis.state.workspace import Workspace, WorkspaceTab
from aegis.tui.resume_plan import (
    SkipReason, plan_resume, TabPlan,
)


def _tab(handle, profile, provider, session_id, order=0):
    return WorkspaceTab(handle=handle, profile=profile, order=order,
                        provider=provider, session_id=session_id,
                        created_at="2026-05-21T00:00:00Z")


def _agents_with(*profiles):
    return {p: object() for p in profiles}


def _drivers_with(**flags):
    # flags: provider -> supports_resume
    return {name: type("D", (), {"supports_resume": v})() for name, v in flags.items()}


def test_resumable_when_all_conditions_met():
    ws = Workspace(active_handle="a", tabs=[_tab("a", "default", "claude-code", "sid-1")])
    agents = _agents_with("default")
    drivers = _drivers_with(**{"claude-code": True})
    plan = plan_resume(ws, agents, drivers)
    assert len(plan.resumable) == 1
    assert plan.resumable[0].tab.handle == "a"
    assert plan.skipped == []


def test_skip_when_profile_missing():
    ws = Workspace(active_handle="a", tabs=[_tab("a", "ghost", "claude-code", "sid-1")])
    plan = plan_resume(ws, _agents_with("default"),
                       _drivers_with(**{"claude-code": True}))
    assert plan.resumable == []
    assert plan.skipped[0].reason == SkipReason.profile_missing


def test_skip_when_driver_no_resume():
    ws = Workspace(active_handle="a", tabs=[_tab("a", "default", "gemini", "sid-1")])
    plan = plan_resume(ws, _agents_with("default"),
                       _drivers_with(**{"gemini": False}))
    assert plan.skipped[0].reason == SkipReason.driver_no_resume


def test_skip_when_session_id_missing():
    ws = Workspace(active_handle="a", tabs=[_tab("a", "default", "claude-code", None)])
    plan = plan_resume(ws, _agents_with("default"),
                       _drivers_with(**{"claude-code": True}))
    assert plan.skipped[0].reason == SkipReason.no_session_id


def test_mixed_workspace_partitions_correctly():
    ws = Workspace(active_handle="ok", tabs=[
        _tab("ok", "default", "claude-code", "sid", order=0),
        _tab("ghost", "missing", "claude-code", "sid", order=1),
        _tab("gem", "default", "gemini", "sid", order=2),
    ])
    plan = plan_resume(ws, _agents_with("default"),
                       _drivers_with(**{"claude-code": True, "gemini": False}))
    assert [r.tab.handle for r in plan.resumable] == ["ok"]
    assert {s.tab.handle for s in plan.skipped} == {"ghost", "gem"}
```

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement.**

```python
# src/aegis/tui/resume_plan.py
"""Pure classification: which tabs in a workspace can be resumed?

The TUI bootstrap calls plan_resume(workspace, agents, drivers), opens
the resumable ones via driver.resume(), and reports skipped ones in a
single startup-banner line.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from aegis.state.workspace import Workspace, WorkspaceTab


class SkipReason(str, Enum):
    profile_missing = "profile-missing"
    driver_no_resume = "driver-no-resume"
    no_session_id = "no-session-id"


@dataclass(frozen=True)
class TabPlan:
    tab: WorkspaceTab


@dataclass(frozen=True)
class SkippedTab:
    tab: WorkspaceTab
    reason: SkipReason


@dataclass(frozen=True)
class ResumePlan:
    resumable: list[TabPlan]
    skipped: list[SkippedTab]


def plan_resume(ws: Workspace, agents: dict, drivers: dict) -> ResumePlan:
    resumable: list[TabPlan] = []
    skipped: list[SkippedTab] = []
    for tab in sorted(ws.tabs, key=lambda t: t.order):
        if tab.profile not in agents:
            skipped.append(SkippedTab(tab=tab, reason=SkipReason.profile_missing))
            continue
        drv = drivers.get(tab.provider)
        if drv is None or not getattr(drv, "supports_resume", False):
            skipped.append(SkippedTab(tab=tab, reason=SkipReason.driver_no_resume))
            continue
        if not tab.session_id:
            skipped.append(SkippedTab(tab=tab, reason=SkipReason.no_session_id))
            continue
        resumable.append(TabPlan(tab=tab))
    return ResumePlan(resumable=resumable, skipped=skipped)
```

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Commit + push.**

`feat(resume): pure classification of workspace tabs into resumable + skipped`.

---

### Task 11: Bootstrap drives the resume plan

**Files:**
- Modify: `src/aegis/tui/app.py` — at startup, if `pick_workspace_to_resume` returned a workspace, call `plan_resume`, drive `_spawn` per resumable (using `driver.resume(session_id)` instead of `driver.session(...)` — needs a small spawn-variant), redraw transcript from `replay_events`, banner skipped tabs.
- Modify: `src/aegis/tui/pane.py` — accept a `replay: EventReplay | None` ctor kwarg; if present, paint blocks from those events before going live; mark the last turn `⚠ interrupted` if `replay.interrupted`.
- Create: `tests/test_resume_flow.py` — end-to-end harness with a stub driver that records `resume(session_id)` calls.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_resume_flow.py
"""End-to-end resume: given a workspace.json + per-tab JSONL on disk and
a stubbed driver registry, the bootstrap function should:
  - call driver.resume(session_id) for resumable tabs in order
  - skip the others
  - return a startup-banner string listing skips (or '' if none)
"""
from pathlib import Path

from aegis.events import AssistantText, Result, SystemInit
from aegis.state.session_log import append_event
from aegis.state.workspace import (
    Workspace, WorkspaceTab, save, state_dir,
)
from aegis.tui.app import bootstrap_resume  # to be added


class StubSession:
    def __init__(self): self.opened = True


class StubDriver:
    supports_resume = True
    def __init__(self): self.resume_calls = []
    def resume(self, agent, cwd, mcp_url, handle, session_id):
        self.resume_calls.append((handle, session_id))
        return StubSession()


class StubNoResumeDriver:
    supports_resume = False


def test_bootstrap_resume_opens_resumable_and_skips_others(tmp_path):
    sd = state_dir(tmp_path)
    save(sd, Workspace(active_handle="ok", tabs=[
        WorkspaceTab(handle="ok", profile="default", order=0,
                     provider="claude-code", session_id="sid-1",
                     created_at="2026-05-21T00:00:00Z"),
        WorkspaceTab(handle="gem", profile="default", order=1,
                     provider="gemini", session_id="sid-2",
                     created_at="2026-05-21T00:00:00Z"),
    ]))
    # session log for the resumable tab
    append_event(sd, "ok", SystemInit(session_id="sid-1"))
    append_event(sd, "ok", AssistantText(text="hello", usage=None))
    append_event(sd, "ok", Result(duration_ms=1, is_error=False))

    drv_c = StubDriver()
    drv_g = StubNoResumeDriver()
    opens = []
    banner = bootstrap_resume(
        state_dir_path=sd,
        ws=Workspace(active_handle="ok", tabs=[]),  # ignored if we pass loaded ws below
        agents={"default": object()},
        drivers={"claude-code": drv_c, "gemini": drv_g},
        cwd=str(tmp_path), mcp_url="http://x",
        open_tab=lambda *, handle, replay, session: opens.append(
            (handle, len(replay.events), session.opened)),
    )
    # Resumable tab opened with its 3-event replay
    assert opens == [("ok", 3, True)]
    # Resume was called against the right driver with the right id
    assert drv_c.resume_calls == [("ok", "sid-1")]
    # Banner mentions the skipped gemini tab
    assert "skipped 1" in banner
    assert "gemini" in banner.lower()


def test_bootstrap_resume_zero_resumable_returns_signal(tmp_path):
    sd = state_dir(tmp_path)
    save(sd, Workspace(active_handle="gem", tabs=[
        WorkspaceTab(handle="gem", profile="default", order=0,
                     provider="gemini", session_id="sid-2",
                     created_at="2026-05-21T00:00:00Z"),
    ]))
    opens = []
    banner = bootstrap_resume(
        state_dir_path=sd,
        ws=None,  # forces re-load inside
        agents={"default": object()},
        drivers={"gemini": StubNoResumeDriver()},
        cwd=str(tmp_path), mcp_url="http://x",
        open_tab=lambda **kw: opens.append(kw))
    assert opens == []
    # Caller can detect "no resumable" and exit clean
    assert banner.startswith("no resumable")
```

- [ ] **Step 2-4: Standard TDD cycle.**

`bootstrap_resume` is a pure orchestrator: it takes the resolved `ws`, `agents`, `drivers`, `cwd`, `mcp_url`, and an `open_tab` callback that the real app fills in with `_spawn`-equivalent logic (taking `handle`, `replay: EventReplay`, `session: HarnessSession`). It returns a banner string suitable for the active pane header.

Banner formats:
- 0 skips: `↻ resumed N tab(s)` (or empty if N==0 — but then caller exits).
- N skips: `↻ resumed N · skipped M (<comma-joined reasons or providers>)`.
- 0 resumable: returns `no resumable tabs (...)` — caller sees this prefix and exits clean without opening the TUI.

In the real `AegisApp.on_mount`:

```python
if not clean:
    try:
        ws = pick_workspace_to_resume(self._state_dir, clean=False)
    except CorruptWorkspace as e:
        # printed + exited at CLI level; we shouldn't reach here
        raise
    if ws is not None:
        banner = bootstrap_resume(...)
        if banner.startswith("no resumable"):
            # exit the app with a message — no point opening empty
            sys.stderr.write(banner + "\n")
            self.exit()
            return
        # Otherwise: show banner in the first/active pane after mount
        if banner:
            self._active.show_resume_banner(banner)
```

`Pane.show_resume_banner(text)` mounts a single styled `Static` at the top of the pane content.

- [ ] **Step 5: Commit + push.**

`feat(resume): bootstrap_resume drives plan + opens resumable tabs + banner`.

---

### Task 12: Pane replay paints prior events and marks interrupted turn

**Files:**
- Modify: `src/aegis/tui/pane.py` — accept `replay` kwarg; render its events through the existing block-mounting path before going live; if `replay.interrupted`, mount a `⚠ interrupted` marker after the last block.
- Create: `tests/test_pane_replay.py`

- [ ] **Step 1: Write the failing test**

This needs to instantiate a `ConversationPane` minimally. Reuse the test scaffolding from prior pane tests (e.g. `tests/test_pane_inbox_render.py` or whichever exists). If pane tests already mock Textual, follow the same pattern.

Assertions:
- Given a 3-event replay (`SystemInit`, `AssistantText`, `Result`), the pane mounts the same blocks the live path would mount, in order.
- Given an interrupted replay (ends with `AssistantText`, no `Result`), an extra "interrupted" marker block is mounted after.
- Live events arriving after replay also mount correctly (no interaction).

- [ ] **Step 2-5: Standard TDD cycle.**

Implementation reuses the existing block-render functions (no new rendering primitives), called from a `_replay_events(self, replay)` method invoked just after compose/mount and before subscribing live observers.

Commit: `feat(pane): replay events on resume + interrupted-turn marker`. Push.

---

## Slice 5 — Edge handling + verification

Goal: per-tab resume failure surfaces in-pane, full suite green, end-to-end manual smoke confirms model memory survives a quit/reopen.

### Task 13: Per-tab resume failure is contained

**Files:**
- Modify: `src/aegis/tui/app.py` — wrap `driver.resume(...)` call in `open_tab` lambda with try/except; on failure, open the tab with a failure-banner instead of skipping silently.
- Modify: `src/aegis/tui/pane.py` — add `show_resume_failure(reason: str)`.
- Create: `tests/test_resume_failure_contained.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_resume_failure_contained.py
"""When driver.resume raises for one tab, other tabs still open and the
failure is contained in its own pane."""
from aegis.state.session_log import append_event
from aegis.state.workspace import (
    Workspace, WorkspaceTab, save, state_dir,
)
from aegis.events import SystemInit
from aegis.tui.app import bootstrap_resume


class FlakyDriver:
    supports_resume = True
    def __init__(self, fail_handle): self.fail_handle = fail_handle
    def resume(self, agent, cwd, mcp_url, handle, session_id):
        if handle == self.fail_handle:
            raise RuntimeError("session expired")
        class S: pass
        return S()


def test_one_tab_fails_others_open(tmp_path):
    sd = state_dir(tmp_path)
    save(sd, Workspace(active_handle="a", tabs=[
        WorkspaceTab(handle="a", profile="default", order=0,
                     provider="claude-code", session_id="sid-a",
                     created_at="2026-05-21T00:00:00Z"),
        WorkspaceTab(handle="b", profile="default", order=1,
                     provider="claude-code", session_id="sid-b",
                     created_at="2026-05-21T00:00:00Z"),
    ]))
    append_event(sd, "a", SystemInit(session_id="sid-a"))
    append_event(sd, "b", SystemInit(session_id="sid-b"))

    events = []
    bootstrap_resume(
        state_dir_path=sd, ws=None,
        agents={"default": object()},
        drivers={"claude-code": FlakyDriver(fail_handle="a")},
        cwd=str(tmp_path), mcp_url="http://x",
        open_tab=lambda **kw: events.append(("ok", kw["handle"])),
        open_failed_tab=lambda **kw: events.append(("fail", kw["handle"], kw["reason"])))
    # Both handles produce an event; one is success, one is failure.
    kinds = {e[0] for e in events}
    assert kinds == {"ok", "fail"}
    fail = next(e for e in events if e[0] == "fail")
    assert fail[1] == "a"
    assert "session expired" in fail[2]
```

- [ ] **Step 2-5: Standard TDD cycle.**

Implementation: extend `bootstrap_resume` to accept an `open_failed_tab` callback; wrap the `drv.resume(...)` call in try/except; on success call `open_tab`, on `Exception` call `open_failed_tab` with the reason.

In `AegisApp`, `open_failed_tab` mounts a placeholder pane with `pane.show_resume_failure(reason)`. The pane stays for inspection; Alex closes it manually.

Commit: `feat(resume): per-tab resume failure contained, surfaces in-pane`. Push.

---

### Task 14: Full suite green + end-to-end smoke

**Files:**
- No code changes unless a regression surfaces.

- [ ] **Step 1: Run the full hermetic suite**

```bash
uv run pytest -q
```

Expected: all tests pass (311 baseline + ~30 new from this plan). If anything fails, debug before continuing.

- [ ] **Step 2: End-to-end smoke against a real Claude session**

```bash
cd /tmp && rm -rf aegis-resume-smoke && mkdir aegis-resume-smoke && cd aegis-resume-smoke

# Bootstrap a minimal .aegis.py
cat > .aegis.py <<'EOF'
from aegis import Agent, ClaudeCode
agents = {"default": Agent(provider=ClaudeCode(model="opus", effort="high", permission="auto"))}
default_agent = "default"
EOF

# First run
uv --directory /home/apiad/Workspace/repos/aegis run aegis
# In the TUI: send "remember the number 47", wait for reply, Ctrl+Q to quit

# Verify state on disk
ls .aegis/state/
cat .aegis/state/workspace.json | head -5
ls .aegis/state/sessions/

# Resume
uv --directory /home/apiad/Workspace/repos/aegis run aegis
# In the TUI: the tab from before should be back, with the prior transcript
# visible. Send "what number did I tell you to remember?"
# Expected reply: "47" — the model genuinely resumed.

# Cleanup
cd / && rm -rf /tmp/aegis-resume-smoke
```

- [ ] **Step 3: --clean smoke**

```bash
cd /tmp/aegis-resume-smoke  # recreate from Step 2 first
uv --directory /home/apiad/Workspace/repos/aegis run aegis --clean
# Expected: opens fresh, ignores existing workspace.json; opening any tab
# overwrites the workspace.
```

- [ ] **Step 4: Corrupt workspace smoke**

```bash
cd /tmp/aegis-resume-smoke
echo "{not json" > .aegis/state/workspace.json
uv --directory /home/apiad/Workspace/repos/aegis run aegis
# Expected: prints "aegis: unparseable workspace.json ..." and hint
# "re-run with `aegis --clean`". Exit code 2.
uv --directory /home/apiad/Workspace/repos/aegis run aegis --clean
# Expected: starts fresh, no error.
```

- [ ] **Step 5: Update docs**

Add a `## Persistence` section to `docs/usage.md` documenting:
- Default behavior: `aegis` resumes; `aegis --clean` starts fresh.
- What's persisted (workspace + per-tab event log under `.aegis/state/`).
- Limitations: tabs whose drivers don't support resume are skipped; cwd-bound (moving the project breaks Claude resume); workers not resumed.

Update `README.md` "What you get" to mention persistence:
> **Session persistence.** `aegis` reopens the last workspace by default — same tabs, profiles, order, with each model session genuinely resumed (memory intact, not a transcript replay). `aegis --clean` starts fresh.

Update `CHANGELOG.md` with the new entry under a new `[Unreleased]` section:

```markdown
## [Unreleased]

### Added
- **Session persistence.** `aegis` resumes the last workspace by default;
  `aegis --clean` opts out. Per-tab event logs + workspace.json live under
  `.aegis/state/`. Tabs whose drivers don't support session resume
  (currently Gemini, OpenCode) are skipped with a startup banner.
```

- [ ] **Step 6: Commit + push**

```bash
git add docs/usage.md README.md CHANGELOG.md
git commit -m "docs: session persistence — --clean, behavior, limitations"
git push origin main
```

---

## Done Conditions

- `uv run pytest -q` is green.
- Manual smoke from Task 14 confirms model memory survives quit/reopen.
- `aegis --clean` opens fresh on a populated workspace without deleting state.
- Corrupt workspace exits with code 2 and a clear hint.
- Skipped tabs (driver doesn't support resume, profile missing, no session_id) appear in the startup banner.
- Mid-stream interrupted turns are visibly marked on replay.
- `CHANGELOG.md`, `README.md`, `docs/usage.md` updated.
- All commits on `main`, pushed to `origin`.
- No bump of `pyproject.toml` version in this plan — leave for a separate release commit after the plan completes.
