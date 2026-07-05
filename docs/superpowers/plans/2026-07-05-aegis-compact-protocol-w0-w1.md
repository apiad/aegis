# Aegis Compact Protocol (W0 + W1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `aegis serve`/web persist events like the TUI does, then add a
compact-by-default representation and a `get_event` on-tap fetch to the web WS
protocol — additively, without breaking the shipped web client.

**Architecture:** W0 moves JSONL persistence out of the TUI pane into the
backend (`SessionManager`), so every frontend writes the same log and `seq` is
a real disk line index in all modes. W1 adds field-level truncation of heavy
event bodies (`compact_encoded`) to the `stream/event` frame plus a `get_event`
RPC that returns the full event from disk. The `html` field stays on the frame
throughout W1 (removed later in W2), so the current browser client keeps
working at every step.

**Tech Stack:** Python 3.13, Starlette/WebSocket, pytest-asyncio, `uv`.

## Global Constraints

- Python **3.13+**. Package manager is **`uv`** — run tests with
  `uv run pytest -q -m "not live"` (the fast hermetic suite).
- **Never** filter with `-k "not live"` — it substring-matches `live` and eats
  unrelated tests. Use the `-m "not live"` marker filter.
- **TDD:** failing test first → run it red → minimal implementation → run green
  → commit. One logical change per commit, conventional-commit messages.
- Commit straight to **`main`** (aegis convention); no feature branch.
- **No `core` → `tui` imports.** `src/aegis/core/` must not import from
  `src/aegis/tui/`.
- **Non-breaking:** the `stream/event` frame keeps its `html` field through all
  of W1. Do not remove it here — that happens in W2.
- Compaction is **field-level truncation of the `encode_event()` dict**; the
  result must stay valid input to `decode_event()` (extra keys are ignored by
  the decoder; never remove a key the decoder requires).

## File Structure

- `src/aegis/state/session_log.py` — **modify**: gains
  `make_session_log_observer` (moved here from `tui/pane.py`). This is the
  neutral home for persistence so `core` can import it without touching `tui`.
- `src/aegis/tui/pane.py` — **modify**: import `make_session_log_observer` from
  `state/session_log.py` instead of defining it locally.
- `src/aegis/core/manager.py` — **modify**: `attach_persistence(state_dir)` +
  `_sync_spawn` attaches the persist observer when a persist dir is set.
- `src/aegis/cli.py` — **modify**: `_serve` calls
  `mgr.attach_persistence(_state_dir(Path.cwd()))`.
- `src/aegis/web/compact.py` — **create**: `compact_encoded(d) -> (dict, bool)`.
- `src/aegis/transcript_constants.py` — **modify**: add
  `TOOL_RESULT_HEAD_LINES`, `TOOL_INPUT_HEAD_LINES`.
- `src/aegis/web/subscriptions.py` — **modify**: `event_frame` uses
  `compact_encoded` + adds `truncated`; `SubscriptionRegistry.get_event`.
- `src/aegis/web/server.py` — **modify**: `_constants()` includes the two new
  compaction constants.
- `src/aegis/web/wssession.py` — **modify**: `PROTOCOL_VERSION = 2`, `hello`
  gains `capabilities: ["compact"]`, `_call` handles `get_event`.
- Tests: `tests/test_manager_persistence.py` (**create**),
  `tests/test_web_compact.py` (**create**), and extend
  `tests/test_web_protocol.py` (**modify**).

---

## Task 1: Move `make_session_log_observer` to `state/session_log.py`

Persistence must live where `core` can import it without importing `tui`.

**Files:**
- Modify: `src/aegis/state/session_log.py`
- Modify: `src/aegis/tui/pane.py:68-79` (remove local def, import instead)
- Test: `tests/test_state_session_log.py`

**Interfaces:**
- Produces: `aegis.state.session_log.make_session_log_observer(state_dir_path: Path, handle: str) -> Callable[[object, Event], None]` — an event observer that appends every event to the per-handle JSONL, swallowing exceptions so persistence never breaks the live path.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_state_session_log.py`:

```python
def test_make_session_log_observer_appends(tmp_path):
    from aegis.state.session_log import (
        make_session_log_observer, replay_events)
    from aegis.events import AssistantText
    obs = make_session_log_observer(tmp_path, "obs-handle")
    obs(object(), AssistantText(text="persisted", usage=None))
    r = replay_events(tmp_path, "obs-handle")
    assert [type(e).__name__ for e in r.events] == ["AssistantText"]
    assert r.events[0].text == "persisted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_state_session_log.py::test_make_session_log_observer_appends -q`
Expected: FAIL — `ImportError: cannot import name 'make_session_log_observer'`.

- [ ] **Step 3: Add the function to `session_log.py`**

Append to `src/aegis/state/session_log.py`:

```python
def make_session_log_observer(state_dir_path, handle: str):
    """Returns an EventCb that appends every event to the per-handle JSONL.
    Persistence must never break the live render, so it swallows errors."""
    def _obs(_sess, ev) -> None:
        try:
            append_event(state_dir_path, handle, ev)
        except Exception:
            pass
    return _obs
```

- [ ] **Step 4: Point `pane.py` at the moved function**

In `src/aegis/tui/pane.py`, delete the local `def make_session_log_observer(...)`
(lines ~68-79) and its inner `from aegis.state.session_log import append_event`.
At the call site (`pane.py:361-362`) it is currently referenced by bare name;
add an import near the other pane imports:

```python
from aegis.state.session_log import make_session_log_observer
```

Verify the pane still references `make_session_log_observer(state_dir_path, handle)`
exactly as before at line ~362.

- [ ] **Step 5: Run tests to verify green**

Run: `uv run pytest tests/test_state_session_log.py tests/test_tui_pane*.py -q`
Expected: PASS (new test + any existing pane tests).

- [ ] **Step 6: Commit**

```bash
git add src/aegis/state/session_log.py src/aegis/tui/pane.py tests/test_state_session_log.py
git commit -m "refactor(state): move make_session_log_observer out of tui/pane"
```

---

## Task 2: `SessionManager.attach_persistence` + spawn-time attach + serve wiring

Make the backend persist every session it spawns, so serve/web sessions land
on disk and `seq` is disk-aligned.

**Files:**
- Modify: `src/aegis/core/manager.py` (constructor attr, `attach_persistence`, `_sync_spawn`)
- Modify: `src/aegis/cli.py:_serve` (wire the call)
- Test: `tests/test_manager_persistence.py` (create)

**Interfaces:**
- Consumes: `make_session_log_observer` (Task 1).
- Produces: `SessionManager.attach_persistence(state_dir) -> None` (stores the dir); after it is called, `spawn`/`_sync_spawn` attaches a persist observer to each new `AgentSession`. No-op when never called (the TUI path, which does not call it).

- [ ] **Step 1: Write the failing test**

Create `tests/test_manager_persistence.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from aegis.core.manager import SessionManager
from aegis.events import AssistantText, Result
from aegis.state.session_log import replay_events


class FakeSession:
    def __init__(self, events):
        self._events = list(events)
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...
    async def events(self):
        for e in self._events:
            await asyncio.sleep(0)
            yield e


@pytest.mark.asyncio
async def test_serve_spawn_persists_events(tmp_path):
    evs = [AssistantText(text="from-serve", usage=None),
           Result(duration_ms=1, is_error=False, usage=None)]
    mgr = SessionManager(
        agents={"default": object()}, default_agent="default",
        make_session=lambda profile, url, handle: FakeSession(evs),
        mcp=None)
    mgr.attach_persistence(tmp_path)
    handle = await mgr.spawn("default")
    await mgr.get(handle).send("go")
    await mgr.get(handle)._task
    r = replay_events(tmp_path, handle)
    assert [type(e).__name__ for e in r.events] == ["AssistantText", "Result"]


@pytest.mark.asyncio
async def test_no_persistence_when_not_attached(tmp_path):
    mgr = SessionManager(
        agents={"default": object()}, default_agent="default",
        make_session=lambda profile, url, handle: FakeSession(
            [AssistantText(text="x", usage=None)]),
        mcp=None)
    handle = await mgr.spawn("default")
    await mgr.get(handle).send("go")
    await mgr.get(handle)._task
    assert replay_events(tmp_path, handle).events == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_manager_persistence.py -q`
Expected: FAIL — `AttributeError: 'SessionManager' object has no attribute 'attach_persistence'`.

- [ ] **Step 3: Implement in `manager.py`**

In `SessionManager.__init__` (near the other AppBridge attrs, e.g. after
`self.state_root: Path | None = None`):

```python
        self._persist_dir = None
```

Add the method (near `attach_queue_manager`/`attach_canvas_manager`):

```python
    def attach_persistence(self, state_dir) -> None:
        """Persist every spawned session's events to JSONL under state_dir.
        Called by the serve path; the in-process TUI does not call it (it
        persists via its own pane observer), so there is no double-write."""
        self._persist_dir = state_dir
```

In `_sync_spawn`, right after `self._sessions.append(s)` (before `self._touch(h)`):

```python
        if self._persist_dir is not None:
            from aegis.state.session_log import make_session_log_observer
            s.add_event_observer(make_session_log_observer(self._persist_dir, h))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_manager_persistence.py -q`
Expected: PASS (both tests).

- [ ] **Step 5: Wire it into `_serve`**

In `src/aegis/cli.py::_serve`, immediately after `mgr = SessionManager(...)`
(currently line ~207-208) and its `attach_queue_manager`, add:

```python
    from aegis.state.workspace import state_dir as _state_dir
    mgr.attach_persistence(_state_dir(Path.cwd()))
```

(The `_state_dir` import already appears later in `_serve` at line ~216; this
adds an earlier local import — or hoist the existing one above the
`attach_persistence` call. Use the same `_state_dir(Path.cwd())` value passed to
`WebFrontend` at line ~312 so writes land where `read_history` reads.)

- [ ] **Step 6: Verify the full suite is green**

Run: `uv run pytest -q -m "not live"`
Expected: PASS. (Serve-path wiring is exercised end-to-end by the existing
`live`-marked web tests and by manual `aegis serve` + browser; the unit tests
above cover the manager behavior.)

- [ ] **Step 7: Commit**

```bash
git add src/aegis/core/manager.py src/aegis/cli.py tests/test_manager_persistence.py
git commit -m "feat(core): centralize event persistence in serve via attach_persistence"
```

---

## Task 3: Compaction constants + `compact_encoded`

The pure, testable heart of W1: turn an `encode_event()` dict into a compact
one plus a `truncated` flag.

**Files:**
- Modify: `src/aegis/transcript_constants.py`
- Create: `src/aegis/web/compact.py`
- Test: `tests/test_web_compact.py` (create)

**Interfaces:**
- Produces: `aegis.web.compact.compact_encoded(d: dict) -> tuple[dict, bool]` — given an `encode_event()` dict, returns `(compacted_dict, truncated)`. Truncates `ToolResult.text` (to `TOOL_RESULT_HEAD_LINES` lines), drops `ToolUse.raw_input`, and empties `AssistantThinking.text`; everything else passes through unchanged with `truncated=False`. Compacted dicts carry `full_len` (original char count) on clipped text fields.
- Produces constants: `TOOL_RESULT_HEAD_LINES = 8`, `TOOL_INPUT_HEAD_LINES = 1`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_compact.py`:

```python
from aegis.state.event_codec import decode_event, encode_event
from aegis.events import AssistantText, AssistantThinking, ToolResult, ToolUse
from aegis.web.compact import compact_encoded


def test_tool_result_over_head_is_clipped():
    body = "\n".join(f"line{i}" for i in range(50))
    d = encode_event(ToolResult(text=body, is_error=False))
    out, truncated = compact_encoded(d)
    assert truncated is True
    assert out["text"].count("\n") < body.count("\n")
    assert out["full_len"] == len(body)
    decode_event(out)  # still a valid event dict


def test_short_tool_result_untouched():
    d = encode_event(ToolResult(text="one\ntwo", is_error=False))
    out, truncated = compact_encoded(d)
    assert truncated is False and out == d


def test_tool_use_drops_raw_input():
    d = encode_event(ToolUse(name="Bash", summary="ls",
                             raw_input={"command": "ls -la /very/long"}))
    out, truncated = compact_encoded(d)
    assert truncated is True and "raw_input" not in out
    assert out["name"] == "Bash" and out["summary"] == "ls"


def test_thinking_body_emptied_with_len():
    d = encode_event(AssistantThinking(text="a long private thought",
                                       usage=None))
    out, truncated = compact_encoded(d)
    assert truncated is True and out["text"] == ""
    assert out["full_len"] == len("a long private thought")


def test_assistant_text_passes_through():
    d = encode_event(AssistantText(text="the answer", usage=None))
    out, truncated = compact_encoded(d)
    assert truncated is False and out == d
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_web_compact.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.web.compact'`.

- [ ] **Step 3: Add the constants**

Append to `src/aegis/transcript_constants.py`:

```python
TOOL_RESULT_HEAD_LINES = 8   # lines of a tool result kept in the compact wire
TOOL_INPUT_HEAD_LINES = 1    # lines of tool input kept in the compact wire
```

- [ ] **Step 4: Implement `compact.py`**

Create `src/aegis/web/compact.py`:

```python
"""Field-level truncation of an ``encode_event()`` dict for the compact WS
wire. The result stays valid input to ``decode_event`` (extra keys ignored);
the full event is fetched on demand via the ``get_event`` RPC."""
from __future__ import annotations

from aegis.transcript_constants import TOOL_RESULT_HEAD_LINES


def _clip_lines(text: str, n: int) -> tuple[str, bool]:
    lines = text.splitlines()
    if len(lines) <= n:
        return text, False
    return "\n".join(lines[:n]), True


def compact_encoded(d: dict) -> tuple[dict, bool]:
    t = d.get("t")
    if t == "ToolResult":
        text = d.get("text") or ""
        clipped, was = _clip_lines(text, TOOL_RESULT_HEAD_LINES)
        if not was:
            return d, False
        out = dict(d)
        out["text"] = clipped
        out["full_len"] = len(text)
        return out, True
    if t == "ToolUse":
        if d.get("raw_input") is None:
            return d, False
        out = dict(d)
        out.pop("raw_input", None)
        return out, True
    if t == "AssistantThinking":
        text = d.get("text") or ""
        if not text:
            return d, False
        out = dict(d)
        out["text"] = ""
        out["full_len"] = len(text)
        return out, True
    return d, False
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_web_compact.py -q`
Expected: PASS (all five).

- [ ] **Step 6: Commit**

```bash
git add src/aegis/web/compact.py src/aegis/transcript_constants.py tests/test_web_compact.py
git commit -m "feat(web): compact_encoded field-truncation + compaction constants"
```

---

## Task 4: `event_frame` emits compact event + `truncated` (html kept)

Wire compaction into the live/history frame builder — additively.

**Files:**
- Modify: `src/aegis/web/subscriptions.py:24-36` (`event_frame`)
- Test: `tests/test_web_compact.py`

**Interfaces:**
- Consumes: `compact_encoded` (Task 3).
- Produces: `event_frame(handle, seq, ev)` returns `{type, kind:"event", handle, seq, event_type, event:<compacted>, truncated:<bool>, html:<render_event_html(ev)>}`. The `html` field (full render) is retained for the current client; `event` carries the compacted dict.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_compact.py`:

```python
def test_event_frame_is_compact_but_keeps_html():
    from aegis.web.subscriptions import event_frame
    body = "\n".join(f"row{i}" for i in range(40))
    fr = event_frame("h", 5, ToolResult(text=body, is_error=False))
    assert fr["kind"] == "event" and fr["seq"] == 5
    assert fr["truncated"] is True
    assert fr["event"]["text"].count("\n") < body.count("\n")   # compacted
    assert "row0" in fr["html"]                                  # full in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_web_compact.py::test_event_frame_is_compact_but_keeps_html -q`
Expected: FAIL — `KeyError: 'truncated'`.

- [ ] **Step 3: Update `event_frame`**

Replace the body of `event_frame` in `src/aegis/web/subscriptions.py`:

```python
def event_frame(handle: str, seq: int, ev) -> dict:
    """The canonical ``stream/event`` frame shape, shared by history replay
    (WSSession) and live fan-out (the per-handle observer). The ``event`` field
    is compacted (heavy bodies truncated); ``html`` keeps the full render for
    the current client (removed in W2)."""
    compact, truncated = compact_encoded(encode_event(ev))
    return {
        "type": "stream", "kind": "event",
        "handle": handle, "seq": seq,
        "event_type": type(ev).__name__,
        "event": compact,
        "truncated": truncated,
        "html": render_event_html(ev),
    }
```

Add the import at the top of `subscriptions.py` (near the other `aegis.web`
imports):

```python
from aegis.web.compact import compact_encoded
```

- [ ] **Step 4: Run tests to verify green (incl. no regression)**

Run: `uv run pytest tests/test_web_compact.py tests/test_web_protocol.py tests/test_web_subscriptions.py -q`
Expected: PASS. `test_subscribe_streams_history_then_live` still passes because
`html` is retained.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/web/subscriptions.py tests/test_web_compact.py
git commit -m "feat(web): event_frame emits compacted event + truncated flag (html kept)"
```

---

## Task 5: `get_event` RPC + registry read

The on-tap full fetch — a disk read (works because W0 persists in serve mode).

**Files:**
- Modify: `src/aegis/web/subscriptions.py` (`SubscriptionRegistry.get_event`)
- Modify: `src/aegis/web/wssession.py:_call` (route `get_event`)
- Test: `tests/test_web_protocol.py`

**Interfaces:**
- Consumes: `read_history` (existing), `encode_event` (existing).
- Produces: `SubscriptionRegistry.get_event(handle: str, seq: int) -> {"event": <full encode_event dict> | None}`. RPC `get_event` with `params:{handle, seq}` returns that dict.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_protocol.py`:

```python
async def test_rpc_get_event_returns_full_body(tmp_path: Path):
    from aegis.events import ToolResult
    sd = tmp_path / "state"
    body = "\n".join(f"L{i}" for i in range(30))
    append_event(sd, "h", ToolResult(text=body, is_error=False))
    mgr = FakeManager({"h": FakeCore("h")})
    t, _, task = await _run_authed(tmp_path, mgr, cores_state_dir=sd)
    t.feed({"type": "rpc", "id": 4, "method": "get_event",
            "params": {"handle": "h", "seq": 1}})
    await _settle()
    resp = [s for s in t.sent if s.get("type") == "rpc_response"][-1]
    assert resp["ok"] is True
    assert resp["result"]["event"]["text"] == body   # full, un-truncated
    t.disconnect()
    await task
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_web_protocol.py::test_rpc_get_event_returns_full_body -q`
Expected: FAIL — error frame `unknown_method 'get_event'` (so no `rpc_response`).

- [ ] **Step 3: Add `get_event` to the registry**

In `src/aegis/web/subscriptions.py`, add a method to `SubscriptionRegistry`
(near `history`):

```python
    def get_event(self, handle: str, seq: int) -> dict:
        """Full (un-truncated) encoded event at ``seq`` for on-tap expansion.
        Reads the persisted JSONL — relies on W0 central persistence so live
        serve-mode events are on disk."""
        for s, ev in read_history(self._state_dir, handle):
            if s == seq:
                return {"event": encode_event(ev)}
        return {"event": None}
```

(`encode_event` is already imported in `subscriptions.py`; confirm the import
line `from aegis.state.event_codec import encode_event` is present.)

- [ ] **Step 4: Route the RPC in `wssession.py`**

In `src/aegis/web/wssession.py::_call`, add before the final `raise _RpcUnknown`:

```python
        if method == "get_event":
            return self._reg.get_event(params["handle"], int(params["seq"]))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_web_protocol.py::test_rpc_get_event_returns_full_body -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/web/subscriptions.py src/aegis/web/wssession.py tests/test_web_protocol.py
git commit -m "feat(web): get_event rpc — full event body on demand from JSONL"
```

---

## Task 6: Bump protocol to v2 + advertise `compact` capability + expose constants

Signal the compact protocol in the handshake and publish the new tuning knobs.

**Files:**
- Modify: `src/aegis/web/wssession.py` (`PROTOCOL_VERSION`, `_hello`)
- Modify: `src/aegis/web/server.py:28-37` (`_constants`)
- Test: `tests/test_web_protocol.py` (update existing assertion + add one)

**Interfaces:**
- Produces: `hello` frame with `protocol_version: 2` and
  `capabilities: ["compact"]`; `constants` includes `TOOL_RESULT_HEAD_LINES`
  and `TOOL_INPUT_HEAD_LINES`.

- [ ] **Step 1: Update the existing hello test + add capability assertion**

In `tests/test_web_protocol.py`, change `test_auth_success_sends_hello`'s
`assert hello["protocol_version"] == 1` to `== 2`, and append:

```python
    assert "compact" in hello["capabilities"]
```

Note: the module-level `CONSTANTS` dict in this test file is a fixed stub, so
no constants assertion is needed here — the server-built constants are covered
by `tests/test_web_server.py` (next step).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_web_protocol.py::test_auth_success_sends_hello -q`
Expected: FAIL — `assert 1 == 2`.

- [ ] **Step 3: Bump the protocol + hello in `wssession.py`**

In `src/aegis/web/wssession.py`: set `PROTOCOL_VERSION = 2`. In `_hello`, add
the capabilities key:

```python
    def _hello(self) -> dict:
        return {
            "type": "hello",
            "server_version": self._server_version,
            "protocol_version": PROTOCOL_VERSION,
            "constants": self._constants,
            "capabilities": ["compact"],
            "supported_kinds": list(SUPPORTED_KINDS),
        }
```

- [ ] **Step 4: Expose the new constants in `server.py`**

In `src/aegis/web/server.py::_constants`, add the two knobs (import them at the
top alongside the existing `_tc` usage):

```python
        "TOOL_RESULT_HEAD_LINES": _tc.TOOL_RESULT_HEAD_LINES,
        "TOOL_INPUT_HEAD_LINES": _tc.TOOL_INPUT_HEAD_LINES,
```

- [ ] **Step 5: Add a server-constants test**

Add to `tests/test_web_server.py` (mirror its existing style of building the
app / calling `_constants`; if it has no direct `_constants` test, add):

```python
def test_constants_include_compaction_knobs():
    from aegis.web.server import _constants
    c = _constants()
    assert c["TOOL_RESULT_HEAD_LINES"] == 8
    assert c["TOOL_INPUT_HEAD_LINES"] == 1
```

- [ ] **Step 6: Run the web suite green**

Run: `uv run pytest tests/test_web_protocol.py tests/test_web_server.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/web/wssession.py src/aegis/web/server.py tests/test_web_protocol.py tests/test_web_server.py
git commit -m "feat(web): protocol v2 — advertise compact capability + compaction constants"
```

---

## Task 7: Full-suite gate + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the whole hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS with no regressions. If a snapshot/golden test asserted the old
1-field frame or `protocol_version == 1` elsewhere, update it to match (grep
`protocol_version` and `event_frame` across `tests/` first).

- [ ] **Step 2: Add a CHANGELOG entry**

Under the unreleased section of `CHANGELOG.md`:

```markdown
- Web protocol v2: events stream compact-by-default (heavy bodies truncated
  with a `truncated` flag); full detail on demand via the `get_event` RPC.
  `aegis serve`/web now persists sessions to JSONL like the TUI, so `seq` is a
  real line index in every frontend. (`html` still sent; retired in the web
  client-render slice.)
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): compact protocol v2 + central serve persistence"
```

---

## Self-Review notes (for the executor)

- **Spec coverage:** W0 = Tasks 1–2; W1 compaction = Tasks 3–4; W1 `get_event`
  = Task 5; W1 protocol-version/capability/constants = Task 6. W1's
  thinking-*stream suppression* (not emitting per-chunk thinking frames) is
  **not** in this plan — Task 3 empties each thinking body instead, which
  captures most of the byte win without needing turn-completion detection. The
  "one marker on completion" optimization is deferred to W2/a follow-up, as the
  spec's §"Thinking-stream suppression" notes it can be re-tuned via the
  `THINKING_STREAM` gate without a protocol change.
- **Non-breaking invariant:** `html` stays on the frame (Task 4) so the shipped
  client is unaffected until W2. Do not remove it here.
- **Persistence disjointness:** `attach_persistence` is called only in `_serve`
  (Task 2, Step 5), never by the TUI's `AegisApp`, so the pane's own persist
  observer never double-writes.
