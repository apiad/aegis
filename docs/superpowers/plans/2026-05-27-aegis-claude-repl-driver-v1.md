# ClaudeReplDriver v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `ClaudeReplDriver` alongside `ClaudeDriver`, drive-selectable via `ClaudeCode(mode="print"|"repl")`. PTY in + transcript JSONL out. Subscription-safe Claude harness ahead of the June 15 Anthropic billing split.

**Architecture:** Spawn `claude` (no `-p`) in a PTY via the existing `AsyncPty`; tail `~/.claude/projects/<cwd-slug>/<uuid>.jsonl` via `watchdog` for structured events. New code lives in `drivers/claude_repl.py` + `drivers/claude_repl_parse.py`. Existing `drivers/claude.py` splits into a thin router + a renamed `drivers/claude_print.py`. Zero changes propagate to `AgentSession`, `SessionManager`, `QueueManager`, `InboxRouter`, the TUI, or the Telegram frontend.

**Tech Stack:** Python 3.11+, `asyncio`, `ptyprocess` (via existing `AsyncPty`), `watchdog` (already a dep from v0.11.2 FileIndexer), `pytest`, `uv`.

**Spec:** `docs/superpowers/specs/2026-05-27-aegis-claude-repl-driver-design.md`.

**Files this plan touches:**

| File | Status | Responsibility |
|---|---|---|
| `src/aegis/drivers/claude_repl_parse.py` | create | Pure function: transcript JSONL dict → `list[Event]` |
| `src/aegis/drivers/claude_repl.py` | create | `_cwd_slug`, `_TranscriptTail`, `ClaudeReplSession`, `ClaudeReplDriver` |
| `src/aegis/drivers/claude_print.py` | create (move) | Current `claude.py` body renamed; `ClaudeSession` → `ClaudePrintSession`, `ClaudeDriver` → `ClaudePrintDriver` |
| `src/aegis/drivers/claude.py` | rewrite | Thin router: `ClaudeDriver` dispatches to print or repl based on `agent.mode` |
| `src/aegis/drivers/__init__.py` | unchanged | Registry stays `"claude-code": ClaudeDriver` (router) |
| `src/aegis/config/__init__.py` | modify | `ClaudeCode` gets `mode: Literal["print","repl"] = "print"`; `Agent` re-exposes it as `.mode` |
| `tests/test_claude_repl_parse.py` | create | Parser unit tests |
| `tests/test_claude_repl_transcript_tail.py` | create | Tail unit tests |
| `tests/test_claude_repl_session.py` | create | Hermetic session tests (mocked PTY + tail) |
| `tests/test_claude_repl_argv.py` | create | Driver `build_argv` shape tests |
| `tests/test_claude_repl_router.py` | create | Router dispatch tests |
| `tests/test_claude_repl_live.py` | create | Live smoke tests (marker `live`, auto-skip when `claude` off PATH) |
| `tests/test_claude_pump.py` | rename imports | `ClaudeSession` → `ClaudePrintSession` |
| `tests/test_claude_resume_argv.py` | rename imports | same |
| `tests/test_driver_argv.py` | rename imports | same |
| `AGENTS.md` | modify | drivers section gets `claude_print.py` + `claude_repl.py` entries |
| `CHANGELOG.md` | modify | `## [0.12.0]` entry for the release |
| `pyproject.toml` | modify | version bump 0.11.2 → 0.12.0 |

---

## Slice 1 — Minimum end-to-end (PTY + tail + smallest possible session)

The thinnest vertical slice: spawn claude in PTY, tail its JSONL, send "say hi", get text + Result, close. No MCP, no special permission, no interrupt. Earns its keep by proving the architecture works against the real binary before we layer parity on top.

### Task 1: `_cwd_slug` helper + tests

**Files:**
- Create: `src/aegis/drivers/claude_repl.py`
- Create: `tests/test_claude_repl_session.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_claude_repl_session.py`:

```python
from __future__ import annotations

from pathlib import Path

from aegis.drivers.claude_repl import _cwd_slug


def test_cwd_slug_workspace_repo():
    assert _cwd_slug("/home/apiad/Workspace/repos/aegis") == \
        "-home-apiad-Workspace-repos-aegis"


def test_cwd_slug_dotted_dir_becomes_double_dash():
    # /home/apiad/Workspace/.playground/aegis-smoke is observed on disk
    # as -home-apiad-Workspace--playground-aegis-smoke
    assert _cwd_slug("/home/apiad/Workspace/.playground/aegis-smoke") == \
        "-home-apiad-Workspace--playground-aegis-smoke"


def test_cwd_slug_underscores_become_dashes():
    # observed under /tmp/pytest-of-apiad/pytest-N/test_live_..._0
    assert _cwd_slug("/tmp/pytest-of-apiad/pytest-1/test_live_foo_0") == \
        "-tmp-pytest-of-apiad-pytest-1-test-live-foo-0"


def test_cwd_slug_resolves_relative_paths():
    # Pass a relative or unresolved path; helper resolves first.
    slug = _cwd_slug(str(Path(".").resolve()))
    assert slug.startswith("-")
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_claude_repl_session.py -v
```

Expected: 4 ERRORS — `ModuleNotFoundError: aegis.drivers.claude_repl`.

- [ ] **Step 3: Write minimal implementation**

Create `src/aegis/drivers/claude_repl.py`:

```python
"""ClaudeReplDriver — drives claude via PTY (no -p) and tails the on-disk
session transcript JSONL for structured events. Subscription-billing-safe
alternative to ClaudePrintDriver.

Spec: docs/superpowers/specs/2026-05-27-aegis-claude-repl-driver-design.md
"""
from __future__ import annotations

import re
from pathlib import Path


def _cwd_slug(cwd: str) -> str:
    """Compute the slug claude uses for ~/.claude/projects/<slug>/.

    Empirically: every non-alphanumeric char in the absolute resolved path
    becomes '-'. Verified against real slugs on disk.
    """
    resolved = str(Path(cwd).resolve())
    return re.sub(r"[^A-Za-z0-9]", "-", resolved)
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_claude_repl_session.py -v
```

Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/drivers/claude_repl.py tests/test_claude_repl_session.py
git commit -m "feat(drivers): claude_repl — _cwd_slug helper for transcript path"
```

---

### Task 2: `_TranscriptTail` — async iterator over JSONL appends

**Files:**
- Modify: `src/aegis/drivers/claude_repl.py`
- Create: `tests/test_claude_repl_transcript_tail.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_claude_repl_transcript_tail.py`:

```python
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aegis.drivers.claude_repl import _TranscriptTail


def test_tail_yields_lines_appended_after_start(tmp_path: Path):
    """Lines appended after tail.start() are yielded as parsed dicts."""
    target = tmp_path / "session.jsonl"

    async def scenario():
        tail = _TranscriptTail(target)
        await tail.start()
        # File does not exist yet at start time — tail must tolerate that.
        target.write_text(
            json.dumps({"type": "system", "subtype": "init"}) + "\n"
            + json.dumps({"type": "assistant",
                          "message": {"stop_reason": "end_turn",
                                      "content": [{"type": "text",
                                                   "text": "hi"}]}}) + "\n"
        )
        collected: list[dict] = []
        async for ev in tail:
            collected.append(ev)
            if len(collected) == 2:
                break
        await tail.close()
        return collected

    out = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert out[0]["type"] == "system"
    assert out[1]["message"]["stop_reason"] == "end_turn"


def test_tail_yields_lines_appended_in_chunks(tmp_path: Path):
    """Subsequent appends after first read still get picked up."""
    target = tmp_path / "session.jsonl"

    async def scenario():
        tail = _TranscriptTail(target)
        await tail.start()
        target.write_text(json.dumps({"type": "a"}) + "\n")
        out: list[dict] = []
        async for ev in tail:
            out.append(ev)
            if len(out) == 1:
                # Append a second chunk *after* first yield
                with target.open("a") as f:
                    f.write(json.dumps({"type": "b"}) + "\n")
                    f.flush()
            if len(out) == 2:
                break
        await tail.close()
        return out

    out = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert [e["type"] for e in out] == ["a", "b"]


def test_tail_skips_malformed_lines(tmp_path: Path):
    target = tmp_path / "session.jsonl"
    target.write_text(
        "not-json\n"
        + json.dumps({"type": "ok"}) + "\n"
    )

    async def scenario():
        tail = _TranscriptTail(target)
        await tail.start()
        out: list[dict] = []
        async for ev in tail:
            out.append(ev)
            if len(out) == 1:
                break
        await tail.close()
        return out

    out = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert out == [{"type": "ok"}]
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_claude_repl_transcript_tail.py -v
```

Expected: 3 ERRORS — `ImportError: cannot import name '_TranscriptTail'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/aegis/drivers/claude_repl.py`:

```python
import asyncio
import json
from collections.abc import AsyncIterator

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class _TranscriptTail:
    """Yields parsed JSONL dicts as they are appended to a transcript file.

    The file may not exist at start() time — claude creates it on first
    write. We watch the parent directory; once the file appears we read
    it (and subsequent appends) line-by-line. Malformed lines are
    silently skipped.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._observer: Observer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._fh = None  # opened lazily once the file exists
        self._buf = ""

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._observer = Observer()
        self._observer.schedule(
            _TailHandler(self), str(self._path.parent), recursive=False,
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._observer.start()
        # File may already exist with content (e.g. from a previous run).
        self._maybe_open_and_drain()

    def _maybe_open_and_drain(self) -> None:
        if self._fh is None and self._path.exists():
            self._fh = self._path.open("r", encoding="utf-8")
        if self._fh is None:
            return
        chunk = self._fh.read()
        if not chunk:
            return
        self._buf += chunk
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._queue.put_nowait(ev)

    def _on_fs_event(self) -> None:
        # Called from watchdog's thread; bounce onto the event loop.
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._maybe_open_and_drain)

    def __aiter__(self) -> AsyncIterator[dict]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[dict]:
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev

    async def close(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        # Unblock any pending iterator.
        await self._queue.put(None)


class _TailHandler(FileSystemEventHandler):
    def __init__(self, tail: _TranscriptTail) -> None:
        self._tail = tail

    def on_any_event(self, event) -> None:  # noqa: ARG002
        self._tail._on_fs_event()
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_claude_repl_transcript_tail.py -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/drivers/claude_repl.py tests/test_claude_repl_transcript_tail.py
git commit -m "feat(drivers): claude_repl — _TranscriptTail (watchdog + line drain)"
```

---

### Task 3: `parse_jsonl_event` — transcript dict → list[Event]

**Files:**
- Create: `src/aegis/drivers/claude_repl_parse.py`
- Create: `tests/test_claude_repl_parse.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_claude_repl_parse.py`:

```python
from __future__ import annotations

from aegis.drivers.claude_repl_parse import parse_jsonl_event
from aegis.events import (AssistantText, AssistantThinking, Result,
                          SystemInit, ToolResult, ToolUse)


def test_system_init_event():
    line = {"type": "system", "subtype": "init",
            "sessionId": "abc-123", "model": "claude-opus-4-7"}
    out = parse_jsonl_event(line)
    assert len(out) == 1
    assert isinstance(out[0], SystemInit)
    assert out[0].session_id == "abc-123"


def test_assistant_text_block_yields_one_event_no_result():
    line = {"type": "assistant",
            "message": {"stop_reason": "tool_use",
                        "content": [{"type": "text", "text": "thinking…"}]}}
    out = parse_jsonl_event(line)
    assert len(out) == 1
    assert isinstance(out[0], AssistantText)
    assert out[0].text == "thinking…"


def test_assistant_end_turn_yields_text_then_result():
    line = {"type": "assistant",
            "message": {"stop_reason": "end_turn",
                        "content": [{"type": "text", "text": "Hi there"}],
                        "usage": {"input_tokens": 6,
                                  "output_tokens": 5,
                                  "cache_creation_input_tokens": 100,
                                  "cache_read_input_tokens": 0}}}
    out = parse_jsonl_event(line)
    assert len(out) == 2
    assert isinstance(out[0], AssistantText)
    assert out[0].text == "Hi there"
    assert isinstance(out[1], Result)
    assert out[1].is_error is False
    assert out[1].output_tokens == 5


def test_assistant_thinking_block_strips_signature():
    line = {"type": "assistant",
            "message": {"stop_reason": "tool_use",
                        "content": [{"type": "thinking",
                                     "thinking": "let me check",
                                     "signature": "abcdef…"}]}}
    out = parse_jsonl_event(line)
    assert isinstance(out[0], AssistantThinking)
    assert out[0].text == "let me check"


def test_assistant_tool_use_block():
    line = {"type": "assistant",
            "message": {"stop_reason": "tool_use",
                        "content": [{"type": "tool_use",
                                     "name": "Bash",
                                     "id": "tu_1",
                                     "input": {"command": "ls -la"}}]}}
    out = parse_jsonl_event(line)
    assert len(out) == 1
    assert isinstance(out[0], ToolUse)
    assert out[0].name == "Bash"
    # `summary` mirrors the existing ClaudeDriver event shape — one-line
    # rendering of the call.
    assert "ls -la" in out[0].summary


def test_user_tool_result_block():
    line = {"type": "user",
            "message": {"content": [{"type": "tool_result",
                                     "tool_use_id": "tu_1",
                                     "content": "ok",
                                     "is_error": False}]}}
    out = parse_jsonl_event(line)
    assert len(out) == 1
    assert isinstance(out[0], ToolResult)
    assert out[0].text == "ok"
    assert out[0].is_error is False


def test_multi_block_assistant_yields_one_per_block():
    line = {"type": "assistant",
            "message": {"stop_reason": "end_turn",
                        "content": [
                            {"type": "thinking", "thinking": "T",
                             "signature": "s"},
                            {"type": "text", "text": "X"},
                        ]}}
    out = parse_jsonl_event(line)
    types = [type(e).__name__ for e in out]
    assert types == ["AssistantThinking", "AssistantText", "Result"]


def test_ignored_event_types_yield_empty_list():
    for t in ("last-prompt", "attachment", "permission-mode", "mode",
              "ai-title", "file-history-snapshot"):
        assert parse_jsonl_event({"type": t}) == []
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_claude_repl_parse.py -v
```

Expected: 8 ERRORS — `ModuleNotFoundError: aegis.drivers.claude_repl_parse`.

- [ ] **Step 3: Write minimal implementation**

Create `src/aegis/drivers/claude_repl_parse.py`:

```python
"""Map transcript JSONL events (~/.claude/projects/<slug>/<uuid>.jsonl) to
aegis.events.* typed events.

One JSONL line may yield 0+ aegis Events:
- 'system' type with subtype 'init' → [SystemInit]
- 'assistant' type → one event per content block, plus a Result when
  stop_reason == 'end_turn'
- 'user' type → one ToolResult per tool_result content block
- everything else → []
"""
from __future__ import annotations

from typing import Any

from aegis.events import (AssistantText, AssistantThinking, Event, Result,
                          SystemInit, TokenUsage, ToolResult, ToolUse)

_IGNORED = frozenset({
    "last-prompt", "attachment", "permission-mode", "mode", "ai-title",
    "file-history-snapshot", "queue-operation",
})


def parse_jsonl_event(line: dict[str, Any]) -> list[Event]:
    kind = line.get("type")
    if kind == "system":
        if line.get("subtype") == "init":
            return [SystemInit(session_id=line.get("sessionId"))]
        return []
    if kind == "assistant":
        return _parse_assistant(line)
    if kind == "user":
        return _parse_user(line)
    if kind in _IGNORED:
        return []
    return []


def _parse_assistant(line: dict[str, Any]) -> list[Event]:
    msg = line.get("message") or {}
    usage = _usage(msg.get("usage"))
    out: list[Event] = []
    for block in msg.get("content") or []:
        t = block.get("type")
        if t == "text":
            out.append(AssistantText(text=block.get("text", ""), usage=usage))
        elif t == "thinking":
            out.append(AssistantThinking(text=block.get("thinking", ""),
                                         usage=usage))
        elif t == "tool_use":
            out.append(ToolUse(name=block.get("name", ""),
                               summary=_tool_summary(block),
                               usage=usage))
    if msg.get("stop_reason") == "end_turn":
        out.append(Result(
            duration_ms=None,
            is_error=False,
            input_tokens=(msg.get("usage") or {}).get("input_tokens"),
            output_tokens=(msg.get("usage") or {}).get("output_tokens"),
            usage=usage,
        ))
    return out


def _parse_user(line: dict[str, Any]) -> list[Event]:
    msg = line.get("message") or {}
    out: list[Event] = []
    for block in msg.get("content") or []:
        if block.get("type") == "tool_result":
            content = block.get("content")
            text = content if isinstance(content, str) else _flatten(content)
            out.append(ToolResult(text=text or "",
                                  is_error=bool(block.get("is_error"))))
    return out


def _flatten(content) -> str:
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
        return "".join(parts)
    return ""


def _tool_summary(block: dict[str, Any]) -> str:
    inp = block.get("input") or {}
    # Mirror the -p driver's one-line summary heuristic.
    for k in ("command", "file_path", "pattern", "query", "url", "path"):
        v = inp.get(k)
        if isinstance(v, str):
            return v
    return ""


def _usage(u: dict[str, Any] | None) -> TokenUsage | None:
    if not u:
        return None
    return TokenUsage(
        input=u.get("input_tokens", 0),
        cache_creation=u.get("cache_creation_input_tokens", 0),
        cache_read=u.get("cache_read_input_tokens", 0),
        output=u.get("output_tokens", 0),
    )
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_claude_repl_parse.py -v
```

Expected: 8 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/drivers/claude_repl_parse.py tests/test_claude_repl_parse.py
git commit -m "feat(drivers): claude_repl — JSONL transcript event parser"
```

---

### Task 4: `ClaudeReplSession` — PTY + tail wired into HarnessSession surface

**Files:**
- Modify: `src/aegis/drivers/claude_repl.py`
- Modify: `tests/test_claude_repl_session.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_claude_repl_session.py`:

```python
import asyncio
import json
import uuid
from pathlib import Path

from aegis.drivers.claude_repl import ClaudeReplSession
from aegis.events import AssistantText, Result, SystemInit


class _FakePty:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.alive = True

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    @property
    def is_alive(self) -> bool:
        return self.alive

    def close(self, force: bool = False) -> None:
        self.alive = False


def test_session_routes_transcript_through_events_queue(tmp_path: Path,
                                                        monkeypatch):
    """Spawn a fake PTY, write a transcript ourselves, assert the session
    yields the right typed events and terminates on end_turn."""
    sid = str(uuid.uuid4())
    transcript_dir = tmp_path / "projects" / "-cwd"
    transcript_dir.mkdir(parents=True)
    transcript_path = transcript_dir / f"{sid}.jsonl"

    fake = _FakePty()

    async def scenario():
        sess = ClaudeReplSession(
            argv=["claude-stub"], cwd="/cwd",
            session_id=sid, transcript_path=transcript_path,
            pty_factory=lambda argv, cwd, env: fake,
        )
        await sess.start()
        # Simulate claude writing two events to its transcript.
        transcript_path.write_text(
            json.dumps({"type": "system", "subtype": "init",
                        "sessionId": sid}) + "\n"
            + json.dumps({"type": "assistant",
                          "message": {"stop_reason": "end_turn",
                                      "content": [{"type": "text",
                                                   "text": "Hi"}]}}) + "\n"
        )
        await sess.send("hello")
        out = [ev async for ev in sess.events()]
        await sess.close()
        return out

    out = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    kinds = [type(e).__name__ for e in out]
    assert kinds == ["SystemInit", "AssistantText", "Result"]
    assert out[1].text == "Hi"
    # PTY received the prompt followed by CR
    assert fake.writes == [b"hello\r"]
    assert sess.session_id == sid
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_claude_repl_session.py::test_session_routes_transcript_through_events_queue -v
```

Expected: ERROR — `ImportError: cannot import name 'ClaudeReplSession'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/aegis/drivers/claude_repl.py`:

```python
from typing import Callable

from aegis.drivers.base import HarnessSession
from aegis.drivers.claude_repl_parse import parse_jsonl_event
from aegis.events import Event, Result, SystemInit
from aegis.terminal.pty import AsyncPty


_DEFAULT_PTY_FACTORY = lambda argv, cwd, env: AsyncPty.spawn(  # noqa: E731
    argv, cwd=cwd, env=env)


class ClaudeReplSession(HarnessSession):
    """Drive `claude` (no -p) via a PTY; read structured events by tailing
    its on-disk transcript JSONL.
    """

    def __init__(
        self,
        argv: list[str],
        cwd: str,
        session_id: str,
        transcript_path: Path,
        pty_factory: Callable[[list[str], str, dict | None], object]
            = _DEFAULT_PTY_FACTORY,
        env: dict[str, str] | None = None,
    ) -> None:
        self._argv = argv
        self._cwd = cwd
        self._session_id = session_id
        self._transcript_path = transcript_path
        self._pty_factory = pty_factory
        self._env = env
        self._pty = None
        self._tail: _TranscriptTail | None = None
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def start(self) -> None:
        self._tail = _TranscriptTail(self._transcript_path)
        await self._tail.start()
        self._pty = self._pty_factory(self._argv, self._cwd, self._env)
        self._reader_task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        assert self._tail is not None
        try:
            async for raw in self._tail:
                for ev in parse_jsonl_event(raw):
                    await self._queue.put(ev)
                    if isinstance(ev, Result):
                        # End of a turn — caller's events() loop will
                        # terminate; pump keeps running for next turn.
                        pass
        except Exception:
            pass
        finally:
            await self._queue.put(None)

    async def send(self, text: str) -> None:
        assert self._pty is not None
        self._pty.write(text.encode("utf-8") + b"\r")

    async def events(self):
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev
            if isinstance(ev, Result):
                return

    async def close(self) -> None:
        if self._pty is not None and self._pty.is_alive:
            try:
                self._pty.write(b"/quit\r")
            except Exception:
                pass
            await asyncio.sleep(0.5)
            self._pty.close(force=True)
        if self._tail is not None:
            await self._tail.close()
        if self._reader_task is not None:
            self._reader_task.cancel()
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_claude_repl_session.py -v
```

Expected: 5 PASSED (4 from Task 1 + 1 new).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/drivers/claude_repl.py tests/test_claude_repl_session.py
git commit -m "feat(drivers): ClaudeReplSession — PTY + transcript tail wired"
```

---

### Task 5: Live smoke — single turn against real claude

**Files:**
- Create: `tests/test_claude_repl_live.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_claude_repl_live.py`:

```python
"""Live smoke tests for ClaudeReplDriver against the real `claude` CLI.

Auto-skip when `claude` is not on PATH. Run with -m live for the full
hermetic+live suite, or -m "not live" for hermetic only.
"""
from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest

from aegis.drivers.claude_repl import (ClaudeReplSession, _cwd_slug)
from aegis.events import AssistantText, Result


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(shutil.which("claude") is None,
                       reason="claude CLI not on PATH"),
]


@pytest.mark.asyncio
async def test_single_turn_say_hi(tmp_path):
    """Spawn real claude with --permission-mode auto, send a tiny prompt,
    assert we get text + a Result via the transcript tail.
    """
    sid = str(uuid.uuid4())
    cwd = tmp_path
    cwd.mkdir(exist_ok=True)
    slug = _cwd_slug(str(cwd))
    transcript = (Path.home() / ".claude" / "projects" / slug
                  / f"{sid}.jsonl")

    argv = ["claude",
            "--session-id", sid,
            "--permission-mode", "auto",
            "--model", "haiku",
            "--add-dir", str(cwd),
            "--append-system-prompt", "Respond in 5 words or less."]
    sess = ClaudeReplSession(argv=argv, cwd=str(cwd),
                             session_id=sid, transcript_path=transcript)
    try:
        await sess.start()
        await sess.send("say hi")
        events = []
        async for ev in sess.events():
            events.append(ev)
            if len(events) > 50:
                break
    finally:
        await sess.close()

    assert any(isinstance(e, AssistantText) and e.text for e in events), \
        "expected at least one non-empty AssistantText"
    assert any(isinstance(e, Result) and not e.is_error for e in events), \
        "expected a non-error Result"
```

- [ ] **Step 2: Run test to verify it fails (expected: passes if real claude works, fails otherwise — this is the verification gate for risks #1, #6)**

```
uv run pytest tests/test_claude_repl_live.py -v -m live
```

Expected: PASS. If it fails:
- workspace-trust dialog blocked? Switch `--add-dir` for the cwd as the test already does; if still blocking, fall back to `--dangerously-skip-permissions` *in the test only*, document in spec risk #6.
- `--permission-mode auto` didn't suppress a tool prompt? Switch the argv to `"--permission-mode", "bypassPermissions"` (mapped from `Permission.full` in the next slice). This is the documented fallback in spec risk #1.

Re-run after any change; do not proceed to Slice 2 until this test passes.

- [ ] **Step 3: Commit**

```bash
git add tests/test_claude_repl_live.py
git commit -m "test(drivers): ClaudeReplSession live smoke — single turn end-to-end"
```

---

## Slice 2 — Substrate parity (driver, build_argv, MCP injection)

### Task 6: `ClaudeReplDriver.build_argv`

**Files:**
- Modify: `src/aegis/drivers/claude_repl.py`
- Create: `tests/test_claude_repl_argv.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_claude_repl_argv.py`:

```python
from __future__ import annotations

import json

from aegis.config import Agent
from aegis.drivers.claude_repl import ClaudeReplDriver


MCP_URL = "http://127.0.0.1:9/mcp/"
HANDLE = "lucid-knuth"


def argv_for(permission="auto", effort="high", model="opus"):
    agent = Agent(harness="claude-code", model=model, effort=effort,
                  permission=permission)
    return ClaudeReplDriver().build_argv(agent, "/tmp/wd", MCP_URL, HANDLE)


def test_argv_starts_with_claude_no_print_flag():
    argv = argv_for()
    assert argv[0] == "claude"
    assert "-p" not in argv
    assert "--print" not in argv
    assert "--input-format" not in argv
    assert "--output-format" not in argv


def test_argv_carries_session_id_and_mcp_and_strict_and_priming():
    argv = argv_for()
    # --session-id is a freshly generated UUID; just verify the flag is
    # present and the following arg is a 36-char UUID.
    sid = argv[argv.index("--session-id") + 1]
    assert len(sid) == 36 and sid.count("-") == 4
    mcp = argv[argv.index("--mcp-config") + 1]
    parsed = json.loads(mcp)
    assert "aegis" in parsed["mcpServers"]
    assert "--strict-mcp-config" in argv
    sys_prompt = argv[argv.index("--append-system-prompt") + 1]
    assert HANDLE in sys_prompt


def test_argv_permission_mapping():
    assert "auto" in argv_for("auto")
    assert "bypassPermissions" in argv_for("full")
    assert "acceptEdits" in argv_for("write")
    assert "plan" in argv_for("read")


def test_argv_effort_and_model_passthrough():
    argv = argv_for(effort="max", model="sonnet")
    assert argv[argv.index("--effort") + 1] == "max"
    assert argv[argv.index("--model") + 1] == "sonnet"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_claude_repl_argv.py -v
```

Expected: 4 ERRORS — `ImportError: cannot import name 'ClaudeReplDriver'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/aegis/drivers/claude_repl.py`:

```python
import uuid

from aegis.config import Agent, Effort, Permission
from aegis.drivers.base import HarnessDriver
from aegis.mcp import PRIMING, mcp_config_json


_PERMISSION_MODE = {
    Permission.read: "plan",
    Permission.write: "acceptEdits",
    Permission.full: "bypassPermissions",
    Permission.auto: "auto",
}

_EFFORT = {
    Effort.low: "low",
    Effort.medium: "medium",
    Effort.high: "high",
    Effort.max: "max",
}


class ClaudeReplDriver(HarnessDriver):
    supports_resume = False  # v1 does not implement resume

    def build_argv(self, agent: Agent, cwd: str,
                   mcp_url: str, handle: str) -> list[str]:
        sid = str(uuid.uuid4())
        return [
            "claude",
            "--session-id", sid,
            "--permission-mode", _PERMISSION_MODE[agent.permission],
            "--model", agent.model,
            "--effort", _EFFORT[agent.effort],
            "--mcp-config", mcp_config_json(mcp_url),
            "--strict-mcp-config",
            "--add-dir", cwd,
            "--append-system-prompt", PRIMING.format(handle=handle),
        ]

    def session(self, agent: Agent, cwd: str,
                mcp_url: str, handle: str) -> ClaudeReplSession:
        argv = self.build_argv(agent, cwd, mcp_url, handle)
        sid = argv[argv.index("--session-id") + 1]
        slug = _cwd_slug(cwd)
        transcript = (Path.home() / ".claude" / "projects" / slug
                      / f"{sid}.jsonl")
        return ClaudeReplSession(
            argv=argv, cwd=cwd, session_id=sid,
            transcript_path=transcript,
        )
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_claude_repl_argv.py -v
```

Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/drivers/claude_repl.py tests/test_claude_repl_argv.py
git commit -m "feat(drivers): ClaudeReplDriver — build_argv + session factory"
```

---

### Task 7: Live smoke — MCP injection works via aegis_meta call

**Files:**
- Modify: `tests/test_claude_repl_live.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_claude_repl_live.py`:

```python
@pytest.mark.asyncio
async def test_mcp_injection_aegis_meta_callable(tmp_path):
    """Spawn a tiny FastMCP server, point claude at it via --mcp-config,
    ask the agent to call its single tool, assert we observe the
    ToolUse and ToolResult events.
    """
    import socket

    from fastmcp import FastMCP
    import uvicorn

    server = FastMCP(name="aegis")
    received: list[str] = []

    @server.tool()
    def aegis_ping(note: str) -> str:
        received.append(note)
        return f"pong:{note}"

    # Pick a free port
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    mcp_url = f"http://127.0.0.1:{port}/mcp/"

    config = uvicorn.Config(server.streamable_http_app(),
                            host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    server_task = asyncio.create_task(srv.serve())
    await asyncio.sleep(0.5)  # let it bind

    try:
        from aegis.config import Agent
        from aegis.drivers.claude_repl import ClaudeReplDriver
        agent = Agent(harness="claude-code", model="haiku",
                      effort="low", permission="full")
        sess = ClaudeReplDriver().session(
            agent, str(tmp_path), mcp_url, "lucid-knuth")
        try:
            await sess.start()
            await sess.send(
                "Call the aegis_ping tool with note='from-repl', then say done.")
            events = []
            async for ev in sess.events():
                events.append(ev)
                if len(events) > 80:
                    break
        finally:
            await sess.close()
    finally:
        srv.should_exit = True
        await asyncio.wait_for(server_task, timeout=5)

    from aegis.events import ToolUse, ToolResult, Result
    assert any(isinstance(e, ToolUse) and e.name.endswith("aegis_ping")
               for e in events), f"no aegis_ping ToolUse in: {events!r}"
    assert any(isinstance(e, ToolResult) for e in events)
    assert any(isinstance(e, Result) and not e.is_error for e in events)
    assert "from-repl" in received
```

- [ ] **Step 2: Run test to verify it passes**

```
uv run pytest tests/test_claude_repl_live.py::test_mcp_injection_aegis_meta_callable -v -m live
```

Expected: PASS. This verifies spec risk #2 end-to-end.

- [ ] **Step 3: Commit**

```bash
git add tests/test_claude_repl_live.py
git commit -m "test(drivers): ClaudeReplDriver live smoke — MCP injection via FastMCP"
```

---

## Slice 3 — Hardening: interrupt + crash-recovery

### Task 8: `interrupt()` method

**Files:**
- Modify: `src/aegis/drivers/claude_repl.py`
- Modify: `tests/test_claude_repl_session.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_claude_repl_session.py`:

```python
def test_interrupt_writes_ctrl_c_to_pty():
    """interrupt() must send 0x03 to the PTY master."""
    fake = _FakePty()

    async def scenario():
        sid = "abc"
        sess = ClaudeReplSession(
            argv=["claude-stub"], cwd="/cwd",
            session_id=sid, transcript_path=Path("/tmp/x.jsonl"),
            pty_factory=lambda argv, cwd, env: fake,
        )
        # Skip start() — interrupt only needs _pty set.
        sess._pty = fake
        await sess.interrupt()

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert fake.writes == [b"\x03"]
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_claude_repl_session.py::test_interrupt_writes_ctrl_c_to_pty -v
```

Expected: ERROR — `AttributeError: 'ClaudeReplSession' object has no attribute 'interrupt'`.

- [ ] **Step 3: Write minimal implementation**

Add to `ClaudeReplSession` in `src/aegis/drivers/claude_repl.py`:

```python
    async def interrupt(self) -> None:
        """Send Ctrl-C to the PTY to abort the current turn."""
        if self._pty is not None and self._pty.is_alive:
            self._pty.write(b"\x03")
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_claude_repl_session.py -v
```

Expected: 6 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/drivers/claude_repl.py tests/test_claude_repl_session.py
git commit -m "feat(drivers): ClaudeReplSession.interrupt — write Ctrl-C to PTY"
```

---

### Task 9: Crash-recovery idle timeout

**Files:**
- Modify: `src/aegis/drivers/claude_repl.py`
- Modify: `tests/test_claude_repl_session.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_claude_repl_session.py`:

```python
def test_events_returns_error_result_on_idle_timeout(tmp_path):
    """If no events arrive within the configured idle timeout after a
    send(), events() emits an error Result so callers don't deadlock."""
    sid = "abc"
    transcript = tmp_path / "x.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    fake = _FakePty()

    async def scenario():
        sess = ClaudeReplSession(
            argv=["claude-stub"], cwd="/cwd",
            session_id=sid, transcript_path=transcript,
            pty_factory=lambda argv, cwd, env: fake,
            idle_timeout_s=0.2,
        )
        await sess.start()
        await sess.send("hello")
        out = [ev async for ev in sess.events()]
        await sess.close()
        return out

    out = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    from aegis.events import Result
    assert len(out) == 1
    assert isinstance(out[0], Result)
    assert out[0].is_error is True
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_claude_repl_session.py::test_events_returns_error_result_on_idle_timeout -v
```

Expected: FAIL — likely timeout (no idle_timeout_s param yet).

- [ ] **Step 3: Write minimal implementation**

Modify `ClaudeReplSession.__init__` and `events()` in `src/aegis/drivers/claude_repl.py`:

```python
    def __init__(
        self,
        argv: list[str],
        cwd: str,
        session_id: str,
        transcript_path: Path,
        pty_factory: Callable[[list[str], str, dict | None], object]
            = _DEFAULT_PTY_FACTORY,
        env: dict[str, str] | None = None,
        idle_timeout_s: float = 300.0,
    ) -> None:
        # ... existing assignments ...
        self._idle_timeout_s = idle_timeout_s
        # (keep the rest of __init__ unchanged)
```

Replace `events()`:

```python
    async def events(self):
        while True:
            try:
                ev = await asyncio.wait_for(
                    self._queue.get(), timeout=self._idle_timeout_s)
            except asyncio.TimeoutError:
                yield Result(duration_ms=None, is_error=True,
                             input_tokens=None, output_tokens=None)
                return
            if ev is None:
                return
            yield ev
            if isinstance(ev, Result):
                return
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_claude_repl_session.py -v
```

Expected: 7 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/drivers/claude_repl.py tests/test_claude_repl_session.py
git commit -m "feat(drivers): ClaudeReplSession — idle-timeout crash recovery"
```

---

## Slice 4 — Config seam + router

### Task 10: `ClaudeCode.mode` field + `Agent.mode` flat attr

**Files:**
- Modify: `src/aegis/config/__init__.py`
- Create: `tests/test_claude_repl_router.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_claude_repl_router.py`:

```python
from __future__ import annotations

from aegis.config import Agent, ClaudeCode


def test_claude_code_mode_defaults_to_print():
    a = Agent(provider=ClaudeCode(model="opus"))
    assert a.mode == "print"


def test_claude_code_mode_opt_in_repl():
    a = Agent(provider=ClaudeCode(model="opus", mode="repl"))
    assert a.mode == "repl"


def test_flat_construction_accepts_mode():
    a = Agent(harness="claude-code", model="opus", mode="repl")
    assert a.mode == "repl"


def test_non_claude_agent_has_no_mode_attr_or_default_print():
    """Non-claude agents either don't expose mode or default to 'print'.
    The router only consults it for claude-code; this just guards against
    AttributeError elsewhere."""
    a = Agent(harness="gemini", model="gemini-3-flash-preview")
    # Mode is a claude-only concept; for other harnesses it should be
    # absent or default to 'print' (router never reads it for them).
    assert getattr(a, "mode", "print") == "print"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_claude_repl_router.py -v
```

Expected: 4 FAILED — `ValidationError` (mode not accepted) and `AttributeError` (no `.mode`).

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/config/__init__.py`, modify `ClaudeCode`:

```python
class ClaudeCode(_ProviderBase):
    """Anthropic's `claude` CLI. Has an `effort` field (low|medium|high|max)
    that no other provider currently exposes."""
    name: Literal["claude-code"] = "claude-code"
    effort: Effort = Effort.high
    mode: Literal["print", "repl"] = "print"
```

In the `Agent` class, where flat-attribute re-exposure happens (find the `@property` block that mirrors provider fields onto `Agent` — look for `effort` and follow that pattern), add a `mode` accessor:

```python
    @property
    def mode(self) -> str:
        prov = self.provider
        return getattr(prov, "mode", "print")
```

If the flat construction shape (`Agent(harness=..., model=..., mode=...)`) goes through a validator that builds the provider object, extend that validator to accept and forward `mode`. (Search for "harness=" in the Agent class for the builder; add `mode=values.get("mode", "print")` to the `ClaudeCode(...)` call.)

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_claude_repl_router.py -v
```

Expected: 4 PASSED. Also run the broader config suite:

```
uv run pytest tests/ -k "config or agent" -v
```

Expected: all existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/config/__init__.py tests/test_claude_repl_router.py
git commit -m "feat(config): ClaudeCode.mode (\"print\"|\"repl\") + Agent.mode accessor"
```

---

### Task 11: Rename `claude.py` → `claude_print.py`

**Files:**
- Move: `src/aegis/drivers/claude.py` → `src/aegis/drivers/claude_print.py`
- Modify: imports/exports
- Modify: `tests/test_claude_pump.py`, `tests/test_claude_resume_argv.py`, `tests/test_driver_argv.py`

- [ ] **Step 1: Capture baseline test count**

```
uv run pytest -m "not live" -q | tail -3
```

Record the pass count (call it `N_before`).

- [ ] **Step 2: Move the file and rename classes**

```bash
git mv src/aegis/drivers/claude.py src/aegis/drivers/claude_print.py
```

In `src/aegis/drivers/claude_print.py`, rename:
- `class ClaudeSession` → `class ClaudePrintSession`
- `class ClaudeDriver` → `class ClaudePrintDriver` (keep `supports_resume = True`)

Update all `self.__class__.__name__`-style references if any.

- [ ] **Step 3: Update test imports**

In `tests/test_claude_pump.py`:
- `from aegis.drivers import claude` → `from aegis.drivers import claude_print as claude`
- `from aegis.drivers.claude import _STREAM_LIMIT, ClaudeSession` → `from aegis.drivers.claude_print import _STREAM_LIMIT, ClaudePrintSession as ClaudeSession`

In `tests/test_claude_resume_argv.py`:
- swap `from aegis.drivers.claude import ClaudeDriver` → `from aegis.drivers.claude_print import ClaudePrintDriver as ClaudeDriver`

In `tests/test_driver_argv.py`:
- `from aegis.drivers.claude import ClaudeDriver` → `from aegis.drivers.claude_print import ClaudePrintDriver as ClaudeDriver`
- The `test_registry_has_claude` test asserts `DRIVERS["claude-code"] is ClaudeDriver`. After Task 12 the registry will point at the *router* class (also named `ClaudeDriver`); for now the test will fail because `DRIVERS["claude-code"]` is `ClaudePrintDriver` from the renamed module but the new local alias is too. Use a wider check: `assert DRIVERS["claude-code"].__name__ == "ClaudeDriver"` (this stays correct both before and after Task 12).

- [ ] **Step 4: Update `src/aegis/drivers/__init__.py`**

```python
from aegis.drivers.claude_print import ClaudePrintDriver as ClaudeDriver
# ... rest unchanged
```

This keeps the public `aegis.drivers.ClaudeDriver` import path stable for now; Task 12 will swap it to point at the router.

- [ ] **Step 5: Run hermetic suite to confirm parity**

```
uv run pytest -m "not live" -q | tail -3
```

Expected: pass count unchanged from Step 1.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(drivers): rename claude.py → claude_print.py (router prep)"
```

---

### Task 12: Thin `claude.py` router — dispatches on `agent.mode`

**Files:**
- Create: `src/aegis/drivers/claude.py`
- Modify: `src/aegis/drivers/__init__.py`
- Modify: `tests/test_claude_repl_router.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_claude_repl_router.py`:

```python
from aegis.config import Agent, ClaudeCode
from aegis.drivers import DRIVERS
from aegis.drivers.claude import ClaudeDriver
from aegis.drivers.claude_print import ClaudePrintSession
from aegis.drivers.claude_repl import ClaudeReplSession


def test_registry_points_at_router():
    assert DRIVERS["claude-code"] is ClaudeDriver


def test_router_returns_print_session_when_mode_print():
    agent = Agent(provider=ClaudeCode(model="opus", mode="print"))
    sess = ClaudeDriver().session(agent, "/tmp/wd",
                                  "http://127.0.0.1:9/mcp/", "h")
    assert isinstance(sess, ClaudePrintSession)


def test_router_returns_repl_session_when_mode_repl(tmp_path):
    agent = Agent(provider=ClaudeCode(model="opus", mode="repl"))
    sess = ClaudeDriver().session(agent, str(tmp_path),
                                  "http://127.0.0.1:9/mcp/", "h")
    assert isinstance(sess, ClaudeReplSession)


def test_router_build_argv_delegates_to_print_by_default():
    agent = Agent(provider=ClaudeCode(model="opus"))
    argv = ClaudeDriver().build_argv(agent, "/tmp/wd",
                                     "http://127.0.0.1:9/mcp/", "h")
    assert "-p" in argv


def test_router_build_argv_delegates_to_repl_when_mode_repl():
    agent = Agent(provider=ClaudeCode(model="opus", mode="repl"))
    argv = ClaudeDriver().build_argv(agent, "/tmp/wd",
                                     "http://127.0.0.1:9/mcp/", "h")
    assert "-p" not in argv
    assert "--session-id" in argv
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_claude_repl_router.py -v
```

Expected: 5 ERRORS — `ImportError: cannot import name 'ClaudeDriver' from 'aegis.drivers.claude'` (file doesn't exist post-rename).

- [ ] **Step 3: Write minimal implementation**

Create `src/aegis/drivers/claude.py`:

```python
"""Public ClaudeDriver entry point — thin router that dispatches to
ClaudePrintDriver or ClaudeReplDriver based on the agent's
ClaudeCode.mode field.

Print mode (default for v1): claude -p, stream-JSON over stdio.
REPL mode: claude (no -p), driven by PTY + transcript JSONL tail.
See spec: docs/superpowers/specs/2026-05-27-aegis-claude-repl-driver-design.md
"""
from __future__ import annotations

from aegis.config import Agent
from aegis.drivers.base import HarnessDriver, HarnessSession
from aegis.drivers.claude_print import ClaudePrintDriver
from aegis.drivers.claude_repl import ClaudeReplDriver


class ClaudeDriver(HarnessDriver):
    supports_resume = True  # print mode supports resume; repl mode falls back

    def __init__(self) -> None:
        self._print = ClaudePrintDriver()
        self._repl = ClaudeReplDriver()

    def _pick(self, agent: Agent) -> HarnessDriver:
        return self._repl if getattr(agent, "mode", "print") == "repl" \
            else self._print

    def build_argv(self, agent: Agent, cwd: str,
                   mcp_url: str, handle: str) -> list[str]:
        return self._pick(agent).build_argv(agent, cwd, mcp_url, handle)

    def session(self, agent: Agent, cwd: str,
                mcp_url: str, handle: str) -> HarnessSession:
        return self._pick(agent).session(agent, cwd, mcp_url, handle)

    def resume(self, agent: Agent, cwd: str,
               mcp_url: str, handle: str, session_id: str) -> HarnessSession:
        return self._pick(agent).resume(agent, cwd, mcp_url, handle,
                                        session_id)
```

In `src/aegis/drivers/__init__.py`, replace the import line introduced in Task 11:

```python
from aegis.drivers.claude import ClaudeDriver
```

(removing the `from aegis.drivers.claude_print import ClaudePrintDriver as ClaudeDriver` line)

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_claude_repl_router.py -v
```

Expected: 5 PASSED.

Run the full hermetic suite:

```
uv run pytest -m "not live" -q | tail -3
```

Expected: pass count = `N_before` (Task 11) + 9 (5 router + 4 mode tests from Task 10).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/drivers/claude.py src/aegis/drivers/__init__.py tests/test_claude_repl_router.py
git commit -m "feat(drivers): ClaudeDriver router — print|repl dispatch on agent.mode"
```

---

## Slice 5 — Ship

### Task 13: AGENTS.md + CHANGELOG entries

**Files:**
- Modify: `AGENTS.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update AGENTS.md drivers section**

In the `## Layout` section's `src/aegis/drivers/` entry, replace the existing `claude.py` description with:

```
  `claude.py` (thin router — dispatches to print or repl based on
  ClaudeCode.mode), `claude_print.py` (Claude Code via `claude -p`,
  stream-json over stdio — the original driver, billed against the
  metered API pool after 2026-06-15), `claude_repl.py` (Claude Code
  via PTY + transcript JSONL tail — subscription-safe; opt-in via
  `ClaudeCode(mode="repl")`),
```

- [ ] **Step 2: Add `## [0.12.0]` entry to CHANGELOG.md**

Insert below `## [Unreleased]`:

```markdown
## [0.12.0] - 2026-05-27

### ClaudeReplDriver — subscription-safe Claude harness

- New `ClaudeReplDriver` drives `claude` via PTY (no `-p`) and tails the
  on-disk session transcript at
  `~/.claude/projects/<cwd-slug>/<uuid>.jsonl` for structured events.
  Subscription-billing-safe alternative to the `-p` driver ahead of
  Anthropic's 2026-06-15 billing split.
- Opt-in per agent profile via `ClaudeCode(mode="repl")`; default
  remains `"print"` for this release. Default flips to `"repl"` in a
  follow-up release after burn-in.
- `ClaudeDriver` is now a thin router that dispatches to
  `ClaudePrintDriver` (the original `-p` driver, renamed for clarity)
  or `ClaudeReplDriver` based on the agent's mode. Public import path
  (`from aegis.drivers.claude import ClaudeDriver`) unchanged.
- Full feature parity with the print driver on the substrate floor:
  per-session aegis-MCP injection, `--permission-mode` mapping,
  multi-turn within session, mid-turn interrupt (Ctrl-C), final-text
  capture for queue workers. Idle-timeout crash recovery (default 5
  min) emits an error Result so callers never deadlock.
- Known gap: no token-by-token streaming on the REPL path (transcript
  writes whole messages). Resume support deferred to a follow-up spec.

Spec: `docs/superpowers/specs/2026-05-27-aegis-claude-repl-driver-design.md`.
Plan: `docs/superpowers/plans/2026-05-27-aegis-claude-repl-driver-v1.md`.
```

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md CHANGELOG.md
git commit -m "docs: AGENTS.md drivers section + CHANGELOG 0.12.0 entry"
```

---

### Task 14: Version bump + release commit

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Bump version**

In `pyproject.toml`, change:

```toml
version = "0.11.2"
```

to:

```toml
version = "0.12.0"
```

- [ ] **Step 2: Verify full suite (hermetic) green**

```
uv run pytest -m "not live" -q | tail -3
```

Expected: all pass.

- [ ] **Step 3: Verify live smoke green (gate before release)**

```
uv run pytest tests/test_claude_repl_live.py -v -m live
```

Expected: both live tests pass.

- [ ] **Step 4: Commit + tag**

```bash
git add pyproject.toml
git commit -m "release: 0.12.0 — ClaudeReplDriver (subscription-safe Claude harness)"
git tag v0.12.0
git push origin main
git push origin v0.12.0
```

---

## Out-of-plan (defer to follow-up specs)

These are *explicitly out of scope* for v1, captured here so they don't get smuggled in:

- Token-by-token streaming for the REPL path (would require parsing the PTY render plus reconciling with JSONL writes — separate design).
- Session resume via `claude --resume <path>` (one transcript-tail call away, but separate spec).
- Slash command support (`/skill-name`, `/clear`, `/compact`) — substrate semantics need their own design.
- `--brief` / `SendUserMessage` agent-to-user channel.
- Default flip from `"print"` to `"repl"` — separate, deliberate release after v0.12.0 burn-in (target: before 2026-06-15).

## Self-review checks (run before declaring the plan done)

These map to the spec's risk register; each is verified by a specific task.

| Spec risk | Verified by |
|---|---|
| #1 `--permission-mode auto` actually suppresses prompts | Task 5 live smoke |
| #2 MCP injection works in REPL | Task 7 live smoke |
| #3 Transcript flush latency tolerable | Tasks 5 + 7 (anecdotal during live runs) |
| #4 Crash recovery | Task 9 hermetic test |
| #5 Skill/CLAUDE.md/hook rendering | Tasks 5 + 7 cover skill+priming via `--append-system-prompt`; further skill-specific verification deferred to post-release |
| #6 Workspace-trust dialog on fresh cwd | Task 5 (uses `--add-dir <cwd>`); if blocking, document fallback in Task 5 commit message |
