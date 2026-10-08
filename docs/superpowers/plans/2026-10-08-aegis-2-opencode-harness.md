# OpenCode sessions in aegis 2: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An agent whose harness is `opencode` spawns, streams, takes prompts, commands and inbox messages, interrupts, resumes, changes model, effort and permission, calls aegis's MCP tools, and shows cost, context and its own title, like a Claude session.

**Architecture:** A `Harness` interface (`src/aegis/harness.py`) goes between `Session` and its process, with Claude Code moved behind it unchanged. An OpenCode implementation (`src/aegis/opencode/`) runs one `opencode serve` per session and drives it over HTTP and server-sent events. Its parser maps OpenCode's events onto the event types the fold already draws. Token deltas are folded live and never stored.

**Tech Stack:** Python 3.13, asyncio, httpx (async client and SSE streaming), stdlib `http.server` for the fake, pytest with `pytest-asyncio`, Playwright for the browser tests.

**Spec:** `docs/superpowers/specs/2026-10-08-aegis-2-opencode-harness-design.md`. Issue #180. Work in the worktree `.claude/worktrees/opencode-harness` on branch `feat/180-opencode-harness`.

## Global Constraints

- OpenCode 1.18.31 is the version measured. The v1 routes only: `/session`, `/session/{id}`, `/session/{id}/prompt_async`, `/session/{id}/abort`, `/session/{id}/command`, `/event`, `/command`, `/config/providers`, `/global/health`. Never `/api/session/*`.
- Every request carries `?directory=<cwd>` and basic auth `opencode:<OPENCODE_SERVER_PASSWORD>`.
- `opencode serve --hostname 127.0.0.1 --port 0`; the port is read from the printed line `opencode server listening on http://127.0.0.1:<port>` (OpenCode takes 4096 when free).
- The store keeps only these event types, for the session and its children: `session.created`, `session.updated`, `session.status`, `session.idle`, `session.error`, `session.compacted`, `message.updated`, `message.part.updated`. `message.part.delta` is folded live and never stored.
- No permission rule maps to `ask`. Every mode adds `aegis_*: allow`, `question: deny`, `doom_loop: deny`.
- An OpenCode model is `provider/model`. An agent or spawn naming one without a `/` is refused with `bad_model`.
- `src/aegis/` imports nothing from `legacy/`, uses relative imports, and never calls `Path.cwd()` (`tests/test_imports.py`, `tests/test_no_cwd.py`).
- Commits: conventional, English, `git commit -- <paths>` with named paths only, never amend. A file the commit creates is unknown to git until `git add <that path>`, so stage new files by name first. End each message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; messages with backticks go through `git commit -F -`.
- Iterate on the smallest test set: after each task run only that task's tests. `make test` once at the end, then the PR; CI runs the rest.
- `make test` fails any test over 3 s that is not marked `slow`.

## Review Focus

- **Abort while a tool runs.** OpenCode sends `session.error` and `session.idle` before it completes the aborted `bash` part. The row must stay `interrupted` and never flip to `ok` when that late part arrives. Pinned in Task 3 (abort fixture and parser test) and Task 7 (session test).
- **The doubled `session.idle` after an abort.** One `Result`, one "interrupted" line. Pinned in Task 3.
- **A permission change while a turn runs.** A prompt sent mid-turn must not restart the child and kill the turn; the restart waits for the next send after the turn ends. Pinned in Task 6.
- **A resume OpenCode no longer knows.** A new conversation, a `reset` entry, a new `resume_id`, no crash. Pinned in Task 7.
- **A delta whose part never closes** (the child dies mid-answer). The live-only entry is removed at the turn's end or the exit, so the live view and a fresh fold agree. Pinned in Task 3.

---

### Task 1: `resume_id` and harness labels

The meta's `claude_session_id` becomes `resume_id` (an OpenCode id is not Claude's), `Init` and the exit line name the harness.

**Files:**
- Modify: `src/aegis/session.py`, `src/aegis/registry.py`, `src/aegis/meta.py`, `src/aegis/claude/stream.py`, `src/aegis/transcript/entries.py`
- Test: `tests/test_session.py`, `tests/test_registry.py`, `tests/test_meta.py`, `tests/test_fold.py`, `tests/test_commands.py`, `tests/test_live.py`

**Interfaces:**
- Produces: `Session(resume_id=...)`, `Session.resume_id`, meta key `"resume_id"` (a meta with only `"claude_session_id"` still resumes), `Init.harness: str = "Claude Code"`, exit record field `"harness"`.

- [ ] **Step 1: Rename in the tests and add the two new expectations**

```bash
cd /home/apiad/Workspace/repos/aegis/.claude/worktrees/opencode-harness
sed -i 's/claude_session_id/resume_id/g' tests/test_session.py tests/test_commands.py \
  tests/test_live.py tests/test_registry.py tests/test_meta.py tests/test_fold.py
sed -i 's/"claude exited with code 3"/"Claude Code exited with code 3"/' tests/test_session.py
grep -c resume_id tests/test_session.py tests/test_meta.py
```

Append to `tests/test_registry.py` (its `World` fixture boots a `Registry` over a tmp state root):

```python
def test_a_meta_from_before_the_rename_still_resumes(world):
    from aegis.meta import MetaStore

    MetaStore(world.roots.state_root / "sessions").write(
        {
            "log_id": "old-1",
            "handle": "old-owl",
            "agent": "opus",
            "harness": "claude-code",
            "model": "opus",
            "effort": "high",
            "permission": "full",
            "cwd": str(world.roots.config_root),
            "claude_session_id": "cs-old",
            "created_at": 1.0,
        }
    )
    r = world.registry()
    assert r.sessions["old-1"].resume_id == "cs-old"
    assert r.sessions["old-1"].meta()["resume_id"] == "cs-old"
```

(If `Registry` keeps its metas somewhere other than `state_root / "sessions"`, take the path from `Registry.__init__`'s `MetaStore(...)`.)

Append to `tests/test_fold.py`:

```python
def test_init_and_exit_name_their_harness():
    r = Rec()
    r.claude({"type": "system", "subtype": "init", "session_id": "s", "model": "m",
              "claude_code_version": "2.1"})
    r.own("exit", code=1, stderr_tail=[], harness="OpenCode")
    r.own("exit", code=2, stderr_tail=[])  # a record from before the label
    summaries = [e["summary"] for e in run(r)[0].entries()]
    assert summaries[0] == "Claude Code 2.1 · m"
    assert "OpenCode exited with code 1" in summaries
    assert "claude exited with code 2" in summaries
```

(`run(r)` is the helper `tests/test_fold.py` already uses to fold `r.records`.)

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_session.py tests/test_registry.py tests/test_meta.py tests/test_fold.py tests/test_commands.py`
Expected: FAIL with `AttributeError: 'Session' object has no attribute 'resume_id'` and a `KeyError`/assert on `resume_id`.

- [ ] **Step 3: Rename in the code**

In `src/aegis/session.py`: the `__init__` parameter `claude_session_id` becomes `resume_id`, the attribute `self.claude_session_id` becomes `self.resume_id`, the meta key `"claude_session_id"` becomes `"resume_id"`, `ensure_running` records `{"kind": "resume", "resume_id": resume}`, and `_on_line` sets `changes["resume_id"] = ev.session_id`. Update the module docstring's `--resume` sentence to say "once the harness has given the session an id". `_on_exit` records the harness:

```python
        self._record(
            {
                "kind": "exit",
                "code": code,
                "stderr_tail": stderr_tail,
                "harness": "Claude Code",
            }
        )
```

In `src/aegis/registry.py`, `_session`:

```python
            resume_id=meta.get("resume_id") or meta.get("claude_session_id"),
```

In `src/aegis/meta.py`, `rebuild`: rename `claude_id` to `resume_id` and write the key `"resume_id"`.

In `src/aegis/claude/stream.py`, `Init` gains a last field:

```python
@dataclass(frozen=True)
class Init:
    session_id: str | None
    model: str | None
    version: str | None
    # Who printed it, for the transcript's first line.
    harness: str = "Claude Code"
```

In `src/aegis/transcript/entries.py`, the `Init` branch builds its line from the label:

```python
            line = " · ".join(
                x
                for x in (
                    f"{ev.harness} {ev.version}" if ev.version else ev.harness,
                    ev.model,
                )
                if x
            )
```

and the `exit` branch of `_own` names the harness, with old records keeping their old words:

```python
                        summary=f"{rec.get('harness') or 'claude'} exited with code {rec.get('code')}",
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q tests/test_session.py tests/test_registry.py tests/test_meta.py tests/test_fold.py tests/test_commands.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -q -F - -- src/aegis/session.py src/aegis/registry.py src/aegis/meta.py \
  src/aegis/claude/stream.py src/aegis/transcript/entries.py tests/ <<'EOF'
refactor: a session's resume id is the harness's, not Claude's (#180)

The meta's claude_session_id becomes resume_id, read from the old key when
that is all a meta has. Init and the exit line name their harness.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 2: the harness interface, with Claude Code behind it

**Files:**
- Create: `src/aegis/harness.py`, `src/aegis/claude/harness.py`, `tests/test_harness.py`
- Modify: `src/aegis/session.py`, `src/aegis/registry.py`, `src/aegis/commands.py`, `src/aegis/app.py`, `src/aegis/transcript/entries.py`, `src/aegis/meta.py`

**Interfaces:**
- Consumes: Task 1's `resume_id`.
- Produces:
  - `aegis.harness.Launch` (frozen dataclass: `cwd: Path, model: str, effort: str, permission: str, resume_id: str | None, mcp: tuple[str, str] | None, system_prompt: str | None, stderr_path: Path, on_line: Callable[[str], None], on_exit: Callable[[int, list[str]], None], on_error: Callable[[str, bool, str | None], None]`).
  - `aegis.harness.Process` protocol: `pid`, `running`, `session_id` properties; `async start()`, `async send(text: str)`, `async interrupt()`, `async set(kind: str, value: str)`, `async catalog() -> Catalog`, `async terminate()`.
  - `aegis.harness.Harness` protocol: `name`, `src`, `label`, `tool_prefix`, `bin`; `process(launch) -> Process`; `async probe(spec, stderr_path) -> Catalog`.
  - `aegis.harness.harness_for(name: str, claude_bin: str, opencode_bin: str) -> Harness`.
  - `Session(..., claude_bin, opencode_bin="opencode")`, `Session.harness`.
  - `Host.spawn_args(session) -> tuple[tuple[str, str] | None, str | None]` (the MCP URL and token, not Claude's JSON).
  - `Fold.parse(src: str, line: str) -> list[Event]`, `transcript.entries.PARSERS: dict[str, Callable[[], Parser]]`.
  - `commands.Catalogs(stderr_path)`, `Catalogs.put(harness: str, cwd: Path, catalog)`.

- [ ] **Step 1: Write the failing test**

`tests/test_harness.py`:

```python
import pytest

from aegis.claude.harness import ClaudeCode
from aegis.harness import harness_for
from aegis.transcript.entries import Fold


def test_harness_for_picks_by_name():
    h = harness_for("claude-code", "/bin/claude", "/bin/opencode")
    assert isinstance(h, ClaudeCode)
    assert (h.name, h.src, h.label, h.tool_prefix, h.bin) == (
        "claude-code",
        "claude",
        "Claude Code",
        "mcp__aegis__",
        "/bin/claude",
    )


def test_an_unknown_harness_is_refused():
    with pytest.raises(ValueError, match="lovelaice"):
        harness_for("lovelaice", "claude", "opencode")


def test_the_fold_parses_by_src_and_keeps_one_parser_per_src():
    f = Fold()
    line = '{"type":"system","subtype":"init","session_id":"s","model":"m"}'
    (ev,) = f.parse("claude", line)
    assert ev.session_id == "s"
    assert f.parse("claude", "not json")[0].raw == "not json"
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest -q tests/test_harness.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.claude.harness'`.

- [ ] **Step 3: Write the interface**

`src/aegis/harness.py`:

```python
"""A harness is the agent CLI a session runs: Claude Code or OpenCode.

``Session`` keeps the store, the fold, the status, the inbox and the card.
Everything it asks of the agent's process goes through ``Process``, and the
fold reads every stored line through the parser its ``src`` tag names
(``transcript.entries.PARSERS``). Adding a harness adds a module behind these
two, never a branch in ``Session``.

The host decides what a process starts with (the MCP URL and this session's
token, the system prompt) and hands it over as a ``Launch``; the harness only
uses it, because how a token reaches the CLI is the harness's business
(``--mcp-config`` for Claude, ``OPENCODE_CONFIG_CONTENT`` for OpenCode).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .claude.control import Catalog
    from .session import SpawnSpec


def _no_error(text: str, idle: bool, line: str | None) -> None:
    return None


@dataclass(frozen=True)
class Launch:
    cwd: Path
    model: str
    effort: str
    permission: str
    resume_id: str | None
    # The aegis MCP endpoint and this session's token; None runs without aegis.
    mcp: tuple[str, str] | None
    system_prompt: str | None
    stderr_path: Path
    on_line: Callable[[str], None]
    on_exit: Callable[[int, list[str]], None]
    # A request that failed outside the event stream: its text, whether it
    # means no turn is running, and the line the person typed, if any.
    on_error: Callable[[str, bool, str | None], None] = _no_error


class Process(Protocol):
    @property
    def pid(self) -> int | None: ...
    @property
    def running(self) -> bool: ...
    @property
    def session_id(self) -> str | None:
        """The harness's id for the conversation, once the process knows it."""
        ...

    async def start(self) -> None: ...
    async def send(self, text: str) -> None: ...
    async def interrupt(self) -> None: ...
    async def set(self, kind: str, value: str) -> None:
        """Change ``model``, ``effort`` or ``permission``; raises
        ``claude.process.ControlError`` when the harness refuses."""
        ...

    async def catalog(self) -> Catalog: ...
    async def terminate(self) -> None: ...


class Harness(Protocol):
    name: str
    src: str
    label: str
    tool_prefix: str
    bin: str

    def process(self, launch: Launch) -> Process: ...
    async def probe(self, spec: SpawnSpec, stderr_path: Path) -> Catalog: ...


def harness_for(name: str, claude_bin: str, opencode_bin: str) -> Harness:
    if name == "claude-code":
        from .claude.harness import ClaudeCode

        return ClaudeCode(claude_bin)
    raise ValueError(f"no harness named {name!r}")
```

`src/aegis/claude/harness.py`:

```python
"""Claude Code behind the harness interface: ``claude -p`` in stream-json.

A prompt and a slash command are both a user message: Claude runs a line as a
command when its first character is ``/``. Model, effort and permission change
live through control requests (``control.py``).
"""

from __future__ import annotations

import time
from pathlib import Path

from ..harness import Launch
from . import control
from .control import Catalog
from .process import ClaudeProcess, build_argv

_SETTERS = {
    "model": control.set_model,
    "effort": control.set_effort,
    "permission": control.set_permission,
}


class ClaudeSession:
    def __init__(self, bin: str, launch: Launch) -> None:
        mcp_config = None
        if launch.mcp is not None:
            from ..mcp import mcp_config as build

            mcp_config = build(*launch.mcp)
        argv = build_argv(
            bin,
            launch.model,
            launch.effort,
            launch.permission,
            launch.resume_id,
            **({"mcp_config": mcp_config} if mcp_config else {}),
            system_prompt=launch.system_prompt,
        )
        self._proc = ClaudeProcess(
            argv, launch.cwd, launch.stderr_path, launch.on_line, launch.on_exit
        )

    @property
    def pid(self) -> int | None:
        return self._proc.pid

    @property
    def running(self) -> bool:
        return self._proc.running

    @property
    def session_id(self) -> str | None:
        return None  # Claude names it in its first init line

    async def start(self) -> None:
        await self._proc.start()

    async def send(self, text: str) -> None:
        await self._proc.write(
            {"type": "user", "message": {"role": "user", "content": text}}
        )

    async def interrupt(self) -> None:
        await self._proc.write(
            {
                "type": "control_request",
                "request_id": f"aegis_interrupt_{time.monotonic_ns()}",
                "request": {"subtype": "interrupt"},
            }
        )

    async def set(self, kind: str, value: str) -> None:
        await _SETTERS[kind](self._proc, value)

    async def catalog(self) -> Catalog:
        return await control.catalog(self._proc)

    async def terminate(self) -> None:
        await self._proc.terminate()


class ClaudeCode:
    name = "claude-code"
    src = "claude"
    label = "Claude Code"
    tool_prefix = "mcp__aegis__"

    def __init__(self, bin: str) -> None:
        self.bin = bin

    def process(self, launch: Launch) -> ClaudeSession:
        return ClaudeSession(self.bin, launch)

    async def probe(self, spec, stderr_path: Path) -> Catalog:
        return await control.probe(
            self.bin, spec.model, spec.effort, spec.permission, spec.cwd, stderr_path
        )
```

In `src/aegis/transcript/entries.py`, give the fold its parsers. Below the imports:

```python
class _Stateless:
    """Claude's stream-json needs no memory between lines."""

    def feed(self, line: str) -> list[Event]:
        return parse(line)


# The store's src tag -> a parser factory. A fold keeps one parser per tag, so
# a harness whose events need earlier lines (OpenCode's) sees them in order.
PARSERS: dict[str, Any] = {"claude": _Stateless}
```

In `Fold.__init__` add `self._parsers: dict[str, Any] = {}`, add the method

```python
    def parse(self, src: str, line: str) -> list[Event]:
        """One stored or live line of harness ``src``, through this fold's parser."""
        p = self._parsers.get(src)
        if p is None:
            p = self._parsers[src] = PARSERS[src]()
        return p.feed(line)
```

and make `apply` dispatch on it:

```python
        i, ts = record["i"], record.get("ts")
        src = record.get("src")
        if src in PARSERS:
            evs = events if events is not None else self.parse(src, record.get("line", ""))
            ops: list[dict] = []
            for k, ev in enumerate(evs):
                ops += self._event(f"e{i}.{k}", ts, ev)
            return ops
        return self._own(i, ts, record)
```

In `src/aegis/meta.py`, `rebuild` reads the id through a fold's parsers, so any harness's store rebuilds:

```python
from .claude.stream import Init
from .transcript.entries import PARSERS, Fold
...
    resume_id = None
    fold = Fold()
    for r in records:
        if r.get("src") in PARSERS:
            for e in fold.parse(r["src"], r.get("line", "")):
                if isinstance(e, Init) and e.session_id:
                    resume_id = e.session_id  # the last one: /clear starts anew
```

(drop the now-unused `parse` import).

- [ ] **Step 4: Move `Session` onto the interface**

In `src/aegis/session.py`:

- Imports: drop `from .claude import control` and `from .claude.process import ClaudeProcess, build_argv`; add `from .harness import Launch, Process, harness_for`; keep `Catalog` from `.claude.control` and `TURN_BEARING, Init, Notice, Result` from `.claude.stream`.
- `Host.spawn_args` returns `tuple[tuple[str, str] | None, str | None]` and its docstring says "The aegis MCP URL and this session's token, and the appended system prompt, for a new process."
- `__init__` gains `opencode_bin: str = "opencode"` after `claude_bin`, stores `self.harness = harness_for(spec.harness, claude_bin, opencode_bin)`, drops `self._claude_bin`, and types `self._proc: Process | None = None`.
- `ensure_running`:

```python
    async def ensure_running(self) -> None:
        """Start the harness if there is no process. Raises FileNotFoundError
        when its binary is missing, leaving the session stopped."""
        if self.running:
            return
        resume = self.resume_id
        mcp, system_prompt = self._host.spawn_args(self)
        proc = self.harness.process(
            Launch(
                cwd=self.spec.cwd,
                model=self.spec.model,
                effort=self.spec.effort,
                permission=self.spec.permission,
                resume_id=resume,
                mcp=mcp,
                system_prompt=system_prompt,
                stderr_path=self._stderr_path,
                on_line=self._on_line,
                on_exit=self._on_exit,
                on_error=self._on_error,
            )
        )
        await proc.start()
        self._proc = proc
        self.catalog_task = asyncio.create_task(self._fetch_catalog(proc))
        self._stopping = False
        self.open_tasks.clear()
        if resume:
            self._record({"kind": "resume", "resume_id": resume})
        self._set(status="idle")
```

- `_fetch_catalog(self, proc: Process)` calls `cat = await proc.catalog()`.
- `configure`: the loop becomes

```python
        applied: dict[str, str] = {}
        try:
            for kind, value in (
                ("model", model),
                ("effort", effort),
                ("permission", permission),
            ):
                if not value:
                    continue
                if proc is not None:
                    await proc.set(kind, value)
                applied[kind] = value
```

- `send` ends with `await self._proc.send(text)` instead of writing the user message.
- `interrupt` calls `await self._proc.interrupt()` instead of writing the control request.
- `_on_line` parses through the fold and stores under the harness's tag:

```python
    def _on_line(self, line: str) -> None:
        if self._stopping:
            return
        events = self.fold().parse(self.harness.src, line)
        self._record({"src": self.harness.src, "line": line}, events)
```

  (the rest of the method is unchanged).
- `_on_exit` records `"harness": self.harness.label`.
- Add the error callback, recorded and folded in Task 3:

```python
    def _on_error(self, text: str, idle: bool, line: str | None = None) -> None:
        if self._stopping:
            return
        rec: dict = {"kind": "harness_error", "text": text}
        if line:
            rec["line"] = line
        self._record(rec)
        if idle and self.status == "working":
            self._set(status="idle")
            self._host.turn_ended(self)
```

In `src/aegis/registry.py`: `Registry.__init__` gains `opencode_bin: str = "opencode"` (stored as `self._opencode_bin`) and `_session` passes it; `spawn_args` returns the pair:

```python
    def spawn_args(
        self, session: Session
    ) -> tuple[tuple[str, str] | None, str | None]:
        priming = session.spec.priming
        if self.mcp_url is None or self.tokens is None:
            return None, priming
        from .mcp import primer

        prompt = primer(session, self.server_name)
        if priming:
            prompt += "\n\n" + priming
        return (self.mcp_url, self.tokens.mint(session.log_id)), prompt
```

and `catalog_ready` puts by harness: `self.catalogs.put(session.spec.harness, session.spec.cwd, catalog)`.

In `src/aegis/commands.py`, `Catalogs` keys by harness and cwd and probes through the session's harness:

```python
class Catalogs:
    """Each (harness, cwd)'s catalog, from a live process or a probe.
    ...(keep the existing paragraph)..."""

    def __init__(self, stderr_path: Path) -> None:
        self._stderr = stderr_path
        self._by_key: dict[str, Catalog] = {}
        self._failed: set[str] = set()
        self._probing: dict[str, asyncio.Task[Catalog | None]] = {}

    @staticmethod
    def _key(harness: str, cwd: Path) -> str:
        return f"{harness}\0{cwd}"

    def put(self, harness: str, cwd: Path, catalog: Catalog) -> None:
        key = self._key(harness, cwd)
        self._by_key[key] = catalog
        self._failed.discard(key)

    async def get(self, s: Session) -> Catalog | None:
        key = self._key(s.spec.harness, s.spec.cwd)
        ...(the existing body, with self._by_key for self._by_cwd)...

    async def _probe(self, s: Session) -> Catalog | None:
        key = self._key(s.spec.harness, s.spec.cwd)
        try:
            cat = await s.harness.probe(s.spec, self._stderr)
        except (control.ControlError, TimeoutError, OSError):
            self._failed.add(key)
            return None
        self.put(s.spec.harness, s.spec.cwd, cat)
        return cat
```

In `src/aegis/app.py`: `App.__init__` gains `opencode_bin: str = "opencode"`, stored as `self.opencode_bin`, passed to `Registry(roots, self.publish, claude_bin, interrupt_timeout, opencode_bin=opencode_bin)`; `commands.Catalogs(roots.state_root / "stderr" / "catalog-probe.log")`.

- [ ] **Step 5: Run the new test and every test the move touches**

Run: `uv run pytest -q tests/test_harness.py tests/test_session.py tests/test_commands.py tests/test_registry.py tests/test_meta.py tests/test_fold.py tests/test_web.py tests/test_imports.py tests/test_no_cwd.py`
Expected: PASS, with no other change to the Claude tests.

- [ ] **Step 6: Commit**

```bash
git commit -q -F - -- src/aegis/harness.py src/aegis/claude/harness.py src/aegis/session.py \
  src/aegis/registry.py src/aegis/commands.py src/aegis/app.py \
  src/aegis/transcript/entries.py src/aegis/meta.py tests/test_harness.py <<'EOF'
refactor: a harness interface between the session and its process (#180)

Session asks a Process to start, send, interrupt, set and end, and the fold
parses each stored line through the parser its src tag names. Claude Code is
the first harness, moved behind the interface without a behaviour change.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 3: OpenCode's events, recorded and folded

**Files:**
- Create: `src/aegis/opencode/__init__.py` (empty), `src/aegis/opencode/stream.py`, `scripts/record_opencode.py`, `tests/fixtures/opencode/*.jsonl`, `tests/test_opencode_stream.py`
- Modify: `src/aegis/claude/stream.py`, `src/aegis/transcript/entries.py`, `src/aegis/client/js/entries.js`, `tests/test_fold.py`

**Interfaces:**
- Consumes: `Fold.parse`, `PARSERS` (Task 2).
- Produces:
  - In `claude/stream.py`: `Text.key`, `Thinking.key` (`str | None = None`), `Echo.expands: bool = False`, new `Delta(key: str, kind: str, text: str)`, `Title(text: str)`, `Step(usage: Usage, parent: str | None = None)`, all in `Event`.
  - `aegis.opencode.stream`: `Parser` (with `feed`), `STORED: frozenset[str]`, `DELTA = "message.part.delta"`, `LABEL = "OpenCode"`, `session_of(props: dict) -> str | None`, `tool_name(str) -> str`, `tool_input(Any) -> dict`.
  - `Fold.live(events: list[Event]) -> list[dict]`; `PARSERS["opencode"]`; `_own` kinds `harness_error` and `reset`.

- [ ] **Step 1: Record the fixtures from a real `opencode serve`**

`scripts/record_opencode.py`:

```python
"""Record OpenCode's event stream for the parser's fixtures.

Runs a real ``opencode serve`` in a temporary directory (a few cents of the
model below) and writes one file per scenario to tests/fixtures/opencode/: the
``data:`` payloads of the scenario's session and its children, in order, the
types aegis stores plus deltas, one JSON per line. Re-run it after an OpenCode
upgrade and read what tests/test_opencode_stream.py says.

    uv run python scripts/record_opencode.py [--model opencode-go/deepseek-v4-flash]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "opencode"
KEEP = {
    "session.created",
    "session.updated",
    "session.status",
    "session.idle",
    "session.error",
    "session.compacted",
    "message.updated",
    "message.part.updated",
    "message.part.delta",
}
COMMAND = {"hello": {"template": "Say hello to $ARGUMENTS in exactly three words."}}


class Server:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        pw = secrets.token_hex(8)
        env = dict(
            os.environ,
            OPENCODE_SERVER_PASSWORD=pw,
            OPENCODE_CONFIG_CONTENT=json.dumps(
                {"command": COMMAND, "permission": {"*": "allow", "question": "deny"}}
            ),
        )
        self.proc = subprocess.Popen(
            ["opencode", "serve", "--hostname", "127.0.0.1", "--port", "0"],
            cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )  # fmt: skip
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise SystemExit("opencode serve exited before listening")
            if m := re.search(r"listening on (http://\S+)", line):
                self.base = m.group(1)
                break
        self.auth = "Basic " + base64.b64encode(f"opencode:{pw}".encode()).decode()
        self.events: list[dict] = []
        threading.Thread(target=self._pump, daemon=True).start()
        time.sleep(1)

    def req(self, method: str, path: str, body: dict | None = None, timeout: float = 30):
        r = urllib.request.Request(
            f"{self.base}{path}?directory={self.cwd}", method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"content-type": "application/json", "authorization": self.auth},
        )  # fmt: skip
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None

    def _pump(self) -> None:
        r = urllib.request.Request(
            f"{self.base}/event?directory={self.cwd}", headers={"authorization": self.auth}
        )
        for line in urllib.request.urlopen(r, timeout=3600):
            if line.startswith(b"data:"):
                self.events.append(json.loads(line[5:]))

    def idle_after(self, n: int, sid: str, limit: float = 300) -> None:
        end = time.time() + limit
        while time.time() < end:
            if any(
                e["type"] == "session.idle" and e["properties"].get("sessionID") == sid
                for e in self.events[n:]
            ):
                time.sleep(1.5)  # late parts after an abort's idle
                return
            time.sleep(0.2)
        raise SystemExit(f"no idle for {sid} in {limit}s")

    def save(self, name: str, n: int, sid: str) -> None:
        mine, out = {sid}, []
        for e in self.events[n:]:
            p = e.get("properties") or {}
            info = p.get("info") if isinstance(p.get("info"), dict) else {}
            if e["type"] == "session.created" and info.get("parentID") in mine:
                mine.add(info["id"])
            owner = p.get("sessionID") or info.get("id") or (p.get("part") or {}).get("sessionID")
            if e["type"] in KEEP and owner in mine:
                out.append(json.dumps(e))
        (OUT / f"{name}.jsonl").write_text("\n".join(out) + "\n")
        print(f"{name}: {len(out)} events")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="opencode-go/deepseek-v4-flash")
    model = ap.parse_args().model
    provider, _, model_id = model.partition("/")
    M = {"providerID": provider, "modelID": model_id}
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path(tmp)
        (cwd / "notes.txt").write_text("alpha\nbeta\n")
        srv = Server(cwd)
        try:
            def turn(name: str, *texts: str, gap: float = 0, abort_after: float = 0):
                n = len(srv.events)
                sid = srv.req("POST", "/session", {})["id"]
                for k, text in enumerate(texts):
                    if k:
                        time.sleep(gap)
                    srv.req("POST", f"/session/{sid}/prompt_async",
                            {"model": M, "parts": [{"type": "text", "text": text}]})  # fmt: skip
                if abort_after:
                    time.sleep(abort_after)
                    srv.req("POST", f"/session/{sid}/abort", {})
                srv.idle_after(n, sid)
                srv.save(name, n, sid)

            turn("plain", "Reply with the single word OK.")
            turn("tool", "Run `echo hi` in bash, then say done in one word.")
            turn("midturn", "Run `sleep 6` in bash, then say A.",
                 "Also say B.", gap=3)  # fmt: skip
            turn("abort", "Run `sleep 30` in bash, then say finished.", abort_after=8)
            turn("edit", "In notes.txt replace the word beta with gamma using the edit tool, then say done.")
            turn("task", "Use the task tool with the general subagent to count the files "
                 "in this directory, then report the count in one line.")  # fmt: skip
            n = len(srv.events)
            sid = srv.req("POST", "/session", {})["id"]
            srv.req("POST", f"/session/{sid}/command",
                    {"command": "hello", "arguments": "the okapi", "model": model}, timeout=300)  # fmt: skip
            srv.idle_after(n, sid)
            srv.save("command", n, sid)
        finally:
            srv.proc.terminate()


if __name__ == "__main__":
    main()
```

Run: `uv run python scripts/record_opencode.py`
Expected: seven lines like `plain: 23 events`, and `ls tests/fixtures/opencode/` lists `abort command edit midturn plain task tool` `.jsonl`. Check by eye that `abort.jsonl` has `session.error` before a `tool` part with "User aborted", and that `task.jsonl` has a `session.created` with a `parentID`. If the model answered something unexpected (no tool call in `tool`, no child in `task`), re-run the script; it costs cents.

- [ ] **Step 2: Write the failing tests**

`tests/test_opencode_stream.py`:

```python
import json
from pathlib import Path

import pytest

from aegis.claude.stream import (
    Delta,
    Echo,
    Init,
    Result,
    Step,
    Text,
    Title,
    ToolCall,
    ToolOutput,
)
from aegis.opencode.stream import DELTA, Parser, tool_input, tool_name
from aegis.transcript.entries import Fold

FIX = Path(__file__).parent / "fixtures" / "opencode"


def lines(name: str) -> list[str]:
    return [ln for ln in (FIX / f"{name}.jsonl").read_text().splitlines() if ln]


def first_prompt(name: str) -> str:
    for ln in lines(name):
        e = json.loads(ln)
        part = e["properties"].get("part") or {}
        if e["type"] == "message.part.updated" and part.get("type") == "text":
            return part["text"]
    raise AssertionError("no prompt")


def fold(name: str, *sends: str, interrupt_before: str | None = None) -> Fold:
    """Fold a fixture the way a session stores it: spawn, the sends, then every
    stored line; deltas go through ``live`` and are not records."""
    f, i = Fold(), 0

    def own(kind, **kw):
        nonlocal i
        f.apply({"i": i, "ts": 1.0 + i, "src": "aegis", "kind": kind, **kw})
        i += 1

    own("spawn", agent="deep", model="m", cwd="/x")
    for s in sends or (first_prompt(name),):
        own("send", text=s)
    for ln in lines(name):
        if interrupt_before and interrupt_before in ln:
            own("interrupt")
            interrupt_before = None
        evs = f.parse("opencode", ln)
        if json.loads(ln)["type"] == DELTA:
            f.live(evs)
            continue
        f.apply({"i": i, "ts": 1.0 + i, "src": "opencode", "line": ln}, evs)
        i += 1
    return f


def refold(name: str, *sends: str) -> list[dict]:
    """The same records, without the deltas: what a reload folds."""
    f, i = Fold(), 0
    recs = [{"src": "aegis", "kind": "spawn", "agent": "deep", "model": "m", "cwd": "/x"}]
    recs += [{"src": "aegis", "kind": "send", "text": s} for s in sends or (first_prompt(name),)]
    recs += [
        {"src": "opencode", "line": ln}
        for ln in lines(name)
        if json.loads(ln)["type"] != DELTA
    ]
    for r in recs:
        f.apply({"i": i, "ts": 1.0 + i, **r})
        i += 1
    return f.entries()


def kinds(f: Fold) -> list[tuple[str, str]]:
    return [(e["kind"], e["status"]) for e in f.entries()]


@pytest.mark.parametrize("name", ["plain", "tool", "midturn", "edit", "task", "command"])
def test_live_deltas_end_where_a_reload_starts(name):
    sends = ("/hello the okapi",) if name == "command" else ()
    assert fold(name, *sends).entries() == refold(name, *sends)


def test_a_plain_turn():
    f = fold("plain")
    e = f.entries()
    assert e[1]["summary"].startswith("OpenCode ") and "opencode-go/" in e[1]["summary"]
    assert [k for k in kinds(f) if k[0] == "user"] == [("user", "ok")]
    assert any(x["kind"] == "prose" and x["md"].strip() for x in e)
    assert e[-1]["summary"].startswith("done in") and "$" in e[-1]["summary"]


def test_a_tool_call_is_a_bash_row_with_its_output():
    (t,) = [e for e in fold("tool").entries() if e["kind"] == "tool"]
    assert (t["title"], t["status"]) == ("Bash", "ok")
    assert "hi" in t["detail"]["tail"]


def test_a_prompt_sent_mid_turn_is_read_and_one_result_ends_both():
    f = fold("midturn", "Run `sleep 6` in bash, then say A.", "Also say B.")
    assert [k for k in kinds(f) if k[0] == "user"] == [("user", "ok"), ("user", "ok")]
    assert sum(e["summary"].startswith("done in") for e in f.entries()) == 1


def test_an_abort_is_interrupted_once_and_its_late_tool_part_changes_nothing():
    f = fold("abort", interrupt_before='"session.error"')
    tools = [e for e in f.entries() if e["kind"] == "tool"]
    assert [(t["status"], t["detail"]["result"]) for t in tools] == [("err", "interrupted")]
    assert [e["summary"] for e in f.entries() if e["kind"] == "error"] == ["interrupted"]
    assert not any(e["summary"].startswith("done in") for e in f.entries())


def test_an_edit_has_its_diff():
    (t,) = [e for e in fold("edit").entries() if e["kind"] == "tool" and e["title"] == "Edit"]
    assert t["detail"]["diff"]["removed"] and t["detail"]["diff"]["added"]


def test_a_task_counts_its_child_session_as_steps():
    (t,) = [e for e in fold("task").entries() if e["title"] == "Task"]
    assert t["status"] == "ok" and t["detail"]["steps"] > 0


def test_a_command_shows_the_line_as_typed_with_its_template_under_it():
    f = fold("command", "/hello the okapi")
    (u,) = [e for e in f.entries() if e["kind"] == "user"]
    assert (u["status"], u["md"]) == ("ok", "/hello the okapi")
    assert "okapi" in u["detail"]["tail"] and u["detail"]["tail"] != u["md"]


def test_names_and_inputs_are_claudes():
    assert [tool_name(n) for n in ("bash", "edit", "aegis_monitor_start", "other_x")] == [
        "Bash", "Edit", "mcp__aegis__monitor_start", "other_x",
    ]  # fmt: skip
    assert tool_input({"filePath": "a", "oldString": "b", "replaceAll": True}) == {
        "file_path": "a", "old_string": "b", "replace_all": True,
    }  # fmt: skip


def ev(type_, **props) -> str:
    return json.dumps({"type": type_, "properties": props})


def test_the_parser_maps_one_turn():
    p = Parser()
    info = {"id": "ses_1", "title": "New session - x", "version": "1.18.31",
            "model": {"id": "flash", "providerID": "go"}}  # fmt: skip
    assert p.feed(ev("session.created", sessionID="ses_1", info=info)) == [
        Init(session_id="ses_1", model="go/flash", version="1.18.31", harness="OpenCode")
    ]
    user = {"id": "msg_u", "role": "user", "sessionID": "ses_1", "time": {"created": 1}}
    assert p.feed(ev("message.updated", sessionID="ses_1", info=user)) == []
    part = {"id": "prt_u", "messageID": "msg_u", "sessionID": "ses_1", "type": "text", "text": "hi"}
    assert p.feed(ev("message.part.updated", sessionID="ses_1", part=part)) == [
        Echo(text="hi", expands=True)
    ]
    assert p.feed(ev("message.part.updated", sessionID="ses_1", part=part)) == []
    synth = {**part, "id": "prt_s", "synthetic": True}
    assert p.feed(ev("message.part.updated", sessionID="ses_1", part=synth)) == []
    asst = {"id": "msg_a", "role": "assistant", "sessionID": "ses_1", "time": {"created": 10}}
    p.feed(ev("message.updated", sessionID="ses_1", info=asst))
    opened = {"id": "prt_t", "messageID": "msg_a", "sessionID": "ses_1", "type": "text", "text": ""}
    assert p.feed(ev("message.part.updated", sessionID="ses_1", part=opened)) == [
        Text(text="", parent=None, usage=None, key="prt_t")
    ]
    assert p.feed(ev(DELTA, sessionID="ses_1", messageID="msg_a", partID="prt_t",
                     field="text", delta="Hel")) == [Delta(key="prt_t", kind="prose", text="Hel")]  # fmt: skip
    assert p.feed(ev(DELTA, sessionID="ses_1", partID="prt_nope", field="text", delta="x")) == []
    running = {"id": "prt_c", "messageID": "msg_a", "sessionID": "ses_1", "type": "tool",
               "tool": "read", "callID": "c1", "state": {"status": "running", "input": {"filePath": "/a"}}}  # fmt: skip
    assert p.feed(ev("message.part.updated", sessionID="ses_1", part=running)) == [
        ToolCall(id="c1", name="Read", input={"file_path": "/a"}, parent=None, usage=None)
    ]
    assert p.feed(ev("message.part.updated", sessionID="ses_1", part=running)) == []
    done = {**running, "state": {"status": "completed", "input": {"filePath": "/a"}, "output": "x"}}
    assert p.feed(ev("message.part.updated", sessionID="ses_1", part=done)) == [
        ToolOutput(id="c1", text="x", is_error=False, parent=None)
    ]
    step = {"id": "prt_f", "messageID": "msg_a", "sessionID": "ses_1", "type": "step-finish",
            "tokens": {"total": 130, "input": 100, "output": 20, "reasoning": 5,
                       "cache": {"read": 3, "write": 2}}, "cost": 0.5}  # fmt: skip
    (st,) = p.feed(ev("message.part.updated", sessionID="ses_1", part=step))
    assert isinstance(st, Step) and st.usage.context == 130
    asst_done = {**asst, "cost": 0.5, "time": {"created": 10, "completed": 1510}}
    p.feed(ev("message.updated", sessionID="ses_1", info=asst_done))
    titled = {**info, "title": "Fix the parser"}
    assert p.feed(ev("session.updated", sessionID="ses_1", info=titled)) == [Title(text="Fix the parser")]
    (r,) = p.feed(ev("session.idle", sessionID="ses_1"))
    assert isinstance(r, Result) and (r.is_error, r.duration_ms, r.cost_usd) == (False, 1500, 0.5)
    assert p.feed(ev("session.idle", sessionID="ses_1")) == []
    assert p.feed(ev("session.idle", sessionID="ses_other")) == []
    assert p.feed("not json")[0].raw == "not json"
```

Append to `tests/test_fold.py`:

```python
def test_a_live_entry_whose_part_never_closed_is_dropped_at_the_turns_end():
    from aegis.claude.stream import Delta, Result

    f = Fold()
    f.live([Delta(key="prt_1", kind="prose", text="half an ans")])
    assert [e["md"] for e in f.entries()] == ["half an ans"]
    ops = f._event("e9.0", 9.0, Result(False, "success", 10, None, None, None))
    assert {"remove": "prt_1"} in ops
    assert all(e["id"] != "prt_1" for e in f.entries())


def test_a_harness_error_loses_the_send_it_answers_and_a_reset_is_said():
    r = Rec()
    r.own("send", text="/nope x")
    r.own("harness_error", text="/nope failed: 400", line="/nope x")
    r.own("reset", text="OpenCode no longer had this conversation; it started a new one")
    e = run(r)[0].entries()
    assert [(x["kind"], x["status"]) for x in e] == [
        ("user", "lost"), ("error", "err"), ("system", "ok"),
    ]  # fmt: skip
    assert e[1]["summary"] == "/nope failed: 400"


def test_activity_waits_for_the_model_until_the_turn_says_something():
    r = Rec()
    r.echo("do it")
    assert run(r)[0].activity() == "waiting for the model"
    r.text("On it.")
    assert run(r)[0].activity() == "On it."
```

and change the first expectation of `test_activity_prefers_a_running_call_then_the_latest_prose` from `"do it"` to `"waiting for the model"`: an echoed prompt with nothing after it is a turn waiting on the model.

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest -q tests/test_opencode_stream.py tests/test_fold.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.opencode'`.

- [ ] **Step 4: Add the events**

In `src/aegis/claude/stream.py`:

```python
@dataclass(frozen=True)
class Text:
    text: str
    parent: str | None
    usage: Usage | None
    # A harness that updates one part in place (OpenCode) names it; the entry
    # id is then the key. Claude's blocks have none.
    key: str | None = None


@dataclass(frozen=True)
class Thinking:
    text: str
    parent: str | None
    usage: Usage | None
    key: str | None = None
```

```python
@dataclass(frozen=True)
class Echo:
    """A prompt the harness has read. ``expands`` marks a harness that echoes
    a slash command as its expanded template (OpenCode), never as the line."""

    text: str
    expands: bool = False
```

```python
@dataclass(frozen=True)
class Delta:
    """A few more characters of an open text (``prose``) or reasoning
    (``thinking``) part. Folded live and never stored: the part's closing
    update carries the whole text."""

    key: str
    kind: str
    text: str


@dataclass(frozen=True)
class Title:
    """The title the harness generated for the conversation."""

    text: str


@dataclass(frozen=True)
class Step:
    """What one finished model step used (OpenCode's ``step-finish``)."""

    usage: Usage
    parent: str | None = None
```

and add `| Delta | Title | Step` to the `Event` union.

- [ ] **Step 5: Write the parser**

`src/aegis/opencode/stream.py`:

```python
"""OpenCode's event stream, one ``data:`` payload at a time, as typed events.

The events are the ones ``claude/stream.py`` defines, so the fold keeps one set
of rules for both harnesses. Measured on OpenCode 1.18.31 (spec
``2026-10-08-aegis-2-opencode-harness-design.md``; fixtures in
``tests/fixtures/opencode/``, recorded by ``scripts/record_opencode.py``):

- A message's role arrives in ``message.updated`` before its parts.
- A text or reasoning part opens with an empty ``message.part.updated``,
  streams as ``message.part.delta`` and closes with its full text; the part id
  is the entry id, so all three touch one entry.
- A tool part goes ``pending`` (no input), ``running`` (input), then
  ``completed`` or ``error``, under one ``callID``.
- ``session.idle`` ends a turn. An abort sends ``session.error`` first, then
  ``session.idle``, then completes the aborted tool part, then idles again: a
  call still open at the idle is closed, and its late part changes nothing.
- A ``task`` call runs a child session, whose events are steps of that call.

The parser keeps what one line cannot carry alone (roles, part kinds, costs,
the turn's span), so it must see every stored line in order, which is why the
fold owns it. Every value comes from the lines and none from the clock, so a
fold of the store equals the live fold.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..claude.stream import (
    Compact,
    Delta,
    Echo,
    Event,
    Garbled,
    Ignored,
    Init,
    Result,
    Step,
    Text,
    Thinking,
    Title,
    ToolCall,
    ToolOutput,
    Usage,
)

LABEL = "OpenCode"
DELTA = "message.part.delta"
# What the store keeps; everything else on the stream (heartbeats, plugins,
# diffs, deltas) carries nothing a reload needs.
STORED = frozenset(
    {
        "session.created",
        "session.updated",
        "session.status",
        "session.idle",
        "session.error",
        "session.compacted",
        "message.updated",
        "message.part.updated",
    }
)
PLACEHOLDER_TITLE = "New session - "
TOOL_NAMES = {
    "bash": "Bash",
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "multiedit": "MultiEdit",
    "glob": "Glob",
    "grep": "Grep",
    "list": "LS",
    "webfetch": "WebFetch",
    "websearch": "WebSearch",
    "todowrite": "TodoWrite",
    "task": "Task",
}
AEGIS_TOOL = "aegis_"
_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def tool_name(name: str) -> str:
    """OpenCode's tool name in the vocabulary ``describe.py`` reads."""
    if name in TOOL_NAMES:
        return TOOL_NAMES[name]
    if name.startswith(AEGIS_TOOL):
        return "mcp__aegis__" + name[len(AEGIS_TOOL) :]
    return name


def tool_input(inp: Any) -> dict:
    """camelCase keys as snake_case: ``filePath`` is ``file_path``."""
    if not isinstance(inp, dict):
        return {}
    return {_CAMEL.sub("_", k).lower(): v for k, v in inp.items()}


def session_of(props: dict) -> str | None:
    """The session an event belongs to."""
    sid = props.get("sessionID")
    if isinstance(sid, str):
        return sid
    for key in ("info", "part"):
        d = props.get(key)
        if isinstance(d, dict):
            v = d.get("sessionID") or (d.get("id") if key == "info" else None)
            if isinstance(v, str):
                return v
    return None


def _usage(tokens: Any) -> Usage | None:
    if not isinstance(tokens, dict):
        return None
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    return Usage(
        input=int(tokens.get("input") or 0),
        cache_creation=int(cache.get("write") or 0),
        cache_read=int(cache.get("read") or 0),
        output=int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0),
    )


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) else None


class Parser:
    def __init__(self) -> None:
        self.root: str | None = None
        self.children: dict[str, str | None] = {}  # child session -> its Task call
        self.roles: dict[str, str] = {}
        self.part_kinds: dict[str, str] = {}
        self.echoed: set[str] = set()
        self.open_calls: set[str] = set()
        self.closed_calls: set[str] = set()
        self.task_call: str | None = None
        self.costs: dict[str, float] = {}
        self.model: str | None = None
        self.title: str | None = None
        self.turn_open = False
        self.turn_start: int | None = None
        self.turn_end: int | None = None
        self.error: str | None = None

    def feed(self, line: str) -> list[Event]:
        try:
            obj: Any = json.loads(line)
        except ValueError:
            return [Garbled(raw=line)]
        if not isinstance(obj, dict):
            return [Garbled(raw=line)]
        kind = str(obj.get("type"))
        handler = getattr(self, "_" + kind.replace(".", "_").replace("-", "_"), None)
        if handler is None:
            return [Ignored(type=kind)]
        return handler(_dict(obj.get("properties")))

    # -- whose event ---------------------------------------------------
    def _belongs(self, sid: str | None) -> tuple[bool, str | None]:
        """Whether the event is this conversation's, and the Task call it is
        a step of (None for the conversation itself)."""
        if sid is None:
            return False, None
        if self.root is None and sid not in self.children:
            self.root = sid
        if sid == self.root:
            return True, None
        if sid in self.children:
            return True, self.children[sid]
        return False, None

    # -- sessions ------------------------------------------------------
    def _session_created(self, p: dict) -> list[Event]:
        info = _dict(p.get("info"))
        sid, parent = _str(info.get("id")), _str(info.get("parentID"))
        if sid is None:
            return []
        if parent is not None:
            if parent == self.root or parent in self.children:
                self.children[sid] = self.task_call
            return []
        self.root, self.model = sid, None  # a new conversation
        return self._info(info)

    def _session_updated(self, p: dict) -> list[Event]:
        info = _dict(p.get("info"))
        if info.get("parentID"):
            return []
        ok, parent = self._belongs(_str(info.get("id")))
        return self._info(info) if ok and parent is None else []

    def _info(self, info: dict) -> list[Event]:
        out: list[Event] = []
        m = _dict(info.get("model"))
        model = f"{m['providerID']}/{m['id']}" if m.get("providerID") and m.get("id") else None
        if model is not None and model != self.model:
            self.model = model
            out.append(
                Init(
                    session_id=_str(info.get("id")),
                    model=model,
                    version=_str(info.get("version")),
                    harness=LABEL,
                )
            )
        title = _str(info.get("title"))
        if title and not title.startswith(PLACEHOLDER_TITLE) and title != self.title:
            self.title = title
            out.append(Title(text=title))
        return out

    def _session_status(self, p: dict) -> list[Event]:
        ok, parent = self._belongs(_str(p.get("sessionID")))
        if ok and parent is None and _dict(p.get("status")).get("type") == "busy":
            self.turn_open = True
        return []

    def _session_error(self, p: dict) -> list[Event]:
        ok, parent = self._belongs(_str(p.get("sessionID")))
        if ok and parent is None:
            self.error = str(_dict(p.get("error")).get("name") or "error")
        return []

    def _session_compacted(self, p: dict) -> list[Event]:
        ok, parent = self._belongs(_str(p.get("sessionID")))
        return [Compact(pre_tokens=0, post_tokens=0)] if ok and parent is None else []

    def _session_idle(self, p: dict) -> list[Event]:
        ok, parent = self._belongs(_str(p.get("sessionID")))
        if not ok or parent is not None or not self.turn_open:
            return []
        span = (
            self.turn_end - self.turn_start
            if self.turn_start is not None and self.turn_end is not None
            else None
        )
        result = Result(
            is_error=self.error is not None,
            subtype=self.error or "success",
            duration_ms=span,
            cost_usd=sum(self.costs.values()) if self.costs else None,
            stop_reason=None,
            context_window=None,
        )
        self.closed_calls |= self.open_calls
        self.open_calls.clear()
        self.turn_open, self.turn_start, self.turn_end, self.error = False, None, None, None
        return [result]

    # -- messages and parts ------------------------------------------
    def _message_updated(self, p: dict) -> list[Event]:
        info = _dict(p.get("info"))
        ok, parent = self._belongs(_str(info.get("sessionID")))
        mid, role = _str(info.get("id")), info.get("role")
        if not ok or mid is None or role not in ("user", "assistant"):
            return []
        self.roles[mid] = str(role)
        if role != "assistant":
            return []
        cost = info.get("cost")
        if isinstance(cost, (int, float)):
            self.costs[mid] = float(cost)
        if parent is not None:
            return []
        self.turn_open = True
        t = _dict(info.get("time"))
        if isinstance(t.get("created"), int) and self.turn_start is None:
            self.turn_start = t["created"]
        if isinstance(t.get("completed"), int):
            self.turn_end = max(self.turn_end or 0, t["completed"])
        err = _dict(info.get("error"))
        if err.get("name"):
            self.error = str(err["name"])
        return []

    def _message_part_updated(self, p: dict) -> list[Event]:
        part = _dict(p.get("part"))
        ok, parent = self._belongs(_str(part.get("sessionID")))
        if not ok:
            return []
        ptype, pid = part.get("type"), str(part.get("id") or "")
        role = self.roles.get(str(part.get("messageID")))
        if ptype == "text":
            text = str(part.get("text") or "")
            if role == "user":
                if (
                    parent is not None
                    or part.get("synthetic")
                    or part.get("ignored")
                    or pid in self.echoed
                    or not text.strip()
                ):
                    return []
                self.echoed.add(pid)
                return [Echo(text=text, expands=True)]
            self.part_kinds[pid] = "prose"
            return [Text(text=text, parent=parent, usage=None, key=pid)]
        if ptype == "reasoning":
            self.part_kinds[pid] = "thinking"
            text = str(part.get("text") or "")
            return [Thinking(text=text, parent=parent, usage=None, key=pid)]
        if ptype == "tool":
            return self._tool(part, parent)
        if ptype == "step-finish":
            u = _usage(part.get("tokens"))
            return [Step(usage=u, parent=parent)] if u is not None else []
        return []

    def _tool(self, part: dict, parent: str | None) -> list[Event]:
        call = str(part.get("callID") or part.get("id") or "")
        if call in self.closed_calls:
            return []  # its turn already ended: an abort's late part
        state = _dict(part.get("state"))
        status = state.get("status")
        name = tool_name(str(part.get("tool") or "?"))
        out: list[Event] = []
        if status in ("running", "completed", "error") and call not in self.open_calls:
            self.open_calls.add(call)
            out.append(
                ToolCall(
                    id=call,
                    name=name,
                    input=tool_input(state.get("input")),
                    parent=parent,
                    usage=None,
                )
            )
            if name == "Task" and parent is None:
                self.task_call = call
        if status in ("completed", "error"):
            self.open_calls.discard(call)
            self.closed_calls.add(call)
            if call == self.task_call:
                self.task_call = None
            text = state.get("output") if status == "completed" else state.get("error")
            out.append(
                ToolOutput(
                    id=call,
                    text=str(text or ""),
                    is_error=status == "error",
                    parent=parent,
                )
            )
        return out

    def _message_part_delta(self, p: dict) -> list[Event]:
        ok, parent = self._belongs(_str(p.get("sessionID")))
        pid = str(p.get("partID") or "")
        kind = self.part_kinds.get(pid)
        if not ok or parent is not None or kind is None or p.get("field") != "text":
            return []
        return [Delta(key=pid, kind=kind, text=str(p.get("delta") or ""))]
```

- [ ] **Step 6: Teach the fold the new events**

In `src/aegis/transcript/entries.py`:

- Import `Delta`, `Step`, `Title` from `..claude.stream`, and add `from ..opencode.stream import Parser as OpenCodeParser`; `PARSERS = {"claude": _Stateless, "opencode": OpenCodeParser}`.
- `Fold.__init__` adds `self._live: set[str] = set()` (entries only deltas made) and `self._turn_open = False`.
- New method:

```python
    def live(self, events: list[Event]) -> list[dict]:
        """Deltas: grow their part's entry without a store record. The part's
        closing update replaces the text, so a reload agrees once it closes."""
        ops: list[dict] = []
        for ev in events:
            if not isinstance(ev, Delta):
                continue
            e = self._entries.get(ev.key)
            if e is None:
                thinking = ev.kind == "thinking"
                e = _entry(
                    ev.key,
                    "thinking" if thinking else "prose",
                    "ok",
                    None,
                    d.THINKING_GLYPH if thinking else d.PROSE_GLYPH,
                    title="Thinking" if thinking else "",
                    md="",
                )
                self._live.add(ev.key)
            ops += self._upsert({**e, "md": (e.get("md") or "") + ev.text})
        return ops

    def _drop_live(self) -> list[dict]:
        """Entries only deltas made, whose part never closed."""
        ops: list[dict] = []
        for key in sorted(self._live):
            ops += self._remove(key)
        self._live.clear()
        return ops
```

- `Text` branch:

```python
        if isinstance(ev, Text):
            eid = ev.key or id
            if ev.key:
                self._live.discard(ev.key)
            if not ev.text.strip():
                return []
            return self._upsert(_entry(eid, "prose", "ok", ts, d.PROSE_GLYPH, md=ev.text))
```

- `Thinking` branch: `eid = ev.key or id`; if `ev.key` discard it from `_live`, and `if ev.key and not ev.text.strip(): return []` (an opening part); then the existing entry with `eid`.
- `Echo` branch:

```python
        if isinstance(ev, Echo):
            ops: list[dict] = []
            typed = None
            if ev.expands:
                pid = self._exact(ev.text)
                if pid is None:
                    pid = self._take(None, command=True, strict=True)
                    if pid is not None:
                        typed = (self._entries.get(pid) or {}).get("md")
                if pid is None:
                    pid = self._take(None, command=False)
            else:
                pid = self._take(ev.text, command=False)
            if pid is not None:
                ops += self._remove(pid)
            self._turn_open = True
            if ev.text.startswith("> from "):
                ...(unchanged inbox entry)...
            if typed is not None:
                return ops + self._upsert(
                    _entry(
                        id, "user", "ok", ts, d.USER_GLYPH, md=typed,
                        detail={"tail": ev.text},
                    )
                )  # fmt: skip
            return ops + self._upsert(_entry(id, "user", "ok", ts, d.USER_GLYPH, md=ev.text))
```

  with the helper

```python
    def _exact(self, want: str) -> str | None:
        """Remove and return the pending send whose text is ``want``."""
        for p in self._pending:
            if ((self._entries.get(p) or {}).get("md") or "").strip() == want.strip():
                self._pending.remove(p)
                return p
        return None
```

- `Result` branch: right after computing `ops = self._end_calls(...)`, add `ops += self._drop_live()` and `self._turn_open = False`.
- `Compact` branch: `line = f"context compacted: {ev.pre_tokens // 1000}k → {ev.post_tokens // 1000}k tokens" if ev.pre_tokens else "context compacted"`.
- `_own`: `send` sets `self._turn_open = True`; `exit`, `stop` and `server_stopped` prepend `self._drop_live()` and set `self._turn_open = False`; two new kinds:

```python
        if kind == "harness_error":
            ops: list[dict] = []
            line = rec.get("line")
            if line:
                pid = self._exact(str(line))
                if pid is not None:
                    ops += self._upsert({**self._entries[pid], "status": "lost"})
            return ops + self._upsert(
                _entry(f"e{i}", "error", "err", ts, d.ERROR_GLYPH, summary=str(rec.get("text")))
            )
        if kind == "reset":
            return self._upsert(
                _entry(f"e{i}", "system", "ok", ts, d.SYSTEM_GLYPH, summary=str(rec.get("text")))
            )
```

- `activity()`: in its loop, before the `prose`/`user` case, `if e["kind"] == "user" and self._turn_open: return "waiting for the model"`.

`Title` and `Step` reach no branch and fold to nothing, which is right: the session reads them (Task 7).

In `src/aegis/client/js/entries.js`, the user row shows a command's template:

```js
  user(e) {
    const body = el("div", "body", e.md);
    if (e.detail?.tail) {
      const d = el("details");
      d.append(el("summary", null, "template"), el("pre", "out", e.detail.tail));
      body.append(d);
    }
    return row(e, `user ${e.status}`, body);
  },
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest -q tests/test_opencode_stream.py tests/test_fold.py tests/test_session.py tests/test_meta.py`
Expected: PASS. If a fixture test fails because the model behaved differently (for instance no `Edit` call), re-record with Step 1 rather than loosening the test.

- [ ] **Step 8: Commit**

```bash
git add tests/fixtures/opencode/*.jsonl
git commit -q -F - -- src/aegis/opencode/ src/aegis/claude/stream.py src/aegis/transcript/entries.py \
  src/aegis/client/js/entries.js scripts/record_opencode.py tests/fixtures/opencode \
  tests/test_opencode_stream.py tests/test_fold.py <<'EOF'
feat: fold OpenCode's event stream into transcript entries (#180)

A parser maps OpenCode 1.18.31's events onto the events the fold draws: keyed
text and reasoning parts, deltas folded live and never stored, tool parts with
Claude's names and inputs, child sessions as steps of their task call, one
result per turn even when an abort idles twice. Fixtures are recorded from a
real opencode serve by scripts/record_opencode.py.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4: what an OpenCode child starts with, and the primer

**Files:**
- Create: `src/aegis/opencode/config.py`, `tests/test_opencode_config.py`
- Modify: `src/aegis/claude/control.py` (`Model.window`), `src/aegis/mcp.py` (primer prefix)

**Interfaces:**
- Produces: `aegis.opencode.config.rules(permission: str) -> dict[str, str]`, `child_config(mcp: tuple[str, str] | None, permission: str) -> dict`, `split_model(model: str) -> dict[str, str]`, `catalog_from(commands: list, providers: dict) -> Catalog`; `Model.window: int | None = None`; `mcp.primer(session, server_name)` names tools by `session.harness.tool_prefix`.

- [ ] **Step 1: Write the failing tests**

`tests/test_opencode_config.py`:

```python
import pytest

from aegis.claude.process import PERMISSION_MODE
from aegis.agents import PERMISSION_ORDER
from aegis.mcp import HEADER
from aegis.opencode.config import catalog_from, child_config, rules, split_model


@pytest.mark.parametrize("permission", list(PERMISSION_MODE))
def test_no_permission_ever_asks(permission):
    r = rules(permission)
    assert "ask" not in r.values()
    assert (r["aegis_*"], r["question"], r["doom_loop"]) == ("allow", "deny", "deny")


def test_each_permission_denies_strictly_less_than_the_one_below_it():
    denied = [{k for k, v in rules(p).items() if v == "deny"} for p in PERMISSION_ORDER]
    for lower, higher in zip(denied, denied[1:]):
        assert higher < lower
    assert {"edit", "bash", "task", "external_directory"} <= denied[0]
    assert "edit" not in denied[1] and "bash" in denied[1]
    assert denied[3] == {"question", "doom_loop"}


def test_the_child_config_carries_the_token_and_the_rules():
    c = child_config(("http://127.0.0.1:9/mcp", "tok"), "read")
    assert c["mcp"]["aegis"] == {
        "type": "remote",
        "url": "http://127.0.0.1:9/mcp",
        "headers": {HEADER: "tok"},
        "oauth": False,
        "enabled": True,
    }
    assert c["permission"] == rules("read")
    assert "mcp" not in child_config(None, "full")


def test_a_model_splits_at_the_first_slash():
    assert split_model("opencode-go/deepseek-v4-pro") == {
        "providerID": "opencode-go",
        "modelID": "deepseek-v4-pro",
    }
    assert split_model("openrouter/qwen/qwen3-32b")["modelID"] == "qwen/qwen3-32b"


def test_the_catalog_lists_models_with_variants_and_windows_and_commands():
    cat = catalog_from(
        [
            {"name": "hello", "description": "Greet someone. More.", "source": "command"},
            {"name": "unslop", "description": "Cut AI tells.", "source": "skill"},
            {"description": "nameless"},
        ],
        {
            "providers": [
                {
                    "id": "go",
                    "models": {
                        "pro": {"name": "Pro", "limit": {"context": 1000000}, "variants": {"high": {}, "max": {}}},
                        "plain": {"name": "Plain", "limit": {"context": 200000}},
                    },
                }
            ]
        },
    )
    assert [(c["name"], c["source"], c["doc"]) for c in cat.commands] == [
        ("hello", "opencode", "Greet someone."),
        ("unslop", "skill", "Cut AI tells."),
    ]
    pro = cat.model("go/pro")
    assert (pro.label, pro.efforts, pro.window) == ("Pro", ("high", "max"), 1000000)
    assert cat.model("go/plain").efforts == ()
```

Append to `tests/test_harness.py`:

```python
def test_the_primer_names_tools_by_the_harness_prefix(tmp_path):
    from aegis.mcp import primer
    from aegis.meta import MetaStore
    from aegis.session import Session, SpawnSpec
    from aegis.transcript.store import Store

    def make(harness):
        return Session(
            log_id="p",
            spec=SpawnSpec("a", "m", "high", "full", tmp_path, harness=harness),
            handle="quiet-owl",
            store=Store(tmp_path / f"{harness}.jsonl"),
            stderr_path=tmp_path / "e",
            claude_bin="claude",
            publish=lambda ch, ops: None,
            metas=MetaStore(tmp_path / "sessions"),
        )

    assert "(mcp__aegis__*)" in primer(make("claude-code"), "zion")
```

(Task 5 adds the OpenCode case to this test, once `harness_for` knows OpenCode.)

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_opencode_config.py tests/test_harness.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.opencode.config'`.

- [ ] **Step 3: Implement**

In `src/aegis/claude/control.py`, `Model` gains a last field `window: int | None = None` (the context window, when the harness's catalog names one).

`src/aegis/opencode/config.py`:

```python
"""What an OpenCode child is started with, and its catalog. No I/O here.

OpenCode reads its config once at start, from ``OPENCODE_CONFIG_CONTENT``
merged over the user's own: the aegis MCP server with this session's token,
and the permission rules. aegis has no approval prompt, so no rule asks: the
``question`` tool and the ``doom_loop`` check both wait on a reply nobody can
send. The four permissions deny strictly less from ``read`` to ``full``, so a
session an agent spawns still has at most the agent's power.
"""

from __future__ import annotations

from typing import Any

from ..claude.control import Catalog, Model, _doc

PERMISSIONS: dict[str, dict[str, str]] = {
    "read": {
        "*": "allow",
        "edit": "deny",
        "bash": "deny",
        "task": "deny",
        "external_directory": "deny",
    },
    "write": {"*": "allow", "bash": "deny", "external_directory": "deny"},
    "auto": {"*": "allow", "external_directory": "deny"},
    "full": {"*": "allow"},
}
ALWAYS = {"aegis_*": "allow", "question": "deny", "doom_loop": "deny"}
SOURCES = {"command": "opencode", "skill": "skill", "mcp": "mcp"}


def rules(permission: str) -> dict[str, str]:
    return {**PERMISSIONS[permission], **ALWAYS}


def child_config(mcp: tuple[str, str] | None, permission: str) -> dict[str, Any]:
    cfg: dict[str, Any] = {"permission": rules(permission)}
    if mcp is not None:
        from ..mcp import HEADER

        url, token = mcp
        cfg["mcp"] = {
            "aegis": {
                "type": "remote",
                "url": url,
                "headers": {HEADER: token},
                "oauth": False,
                "enabled": True,
            }
        }
    return cfg


def split_model(model: str) -> dict[str, str]:
    provider, _, model_id = model.partition("/")
    return {"providerID": provider, "modelID": model_id}


def catalog_from(commands: list, providers: dict) -> Catalog:
    cmds = tuple(
        {
            "name": str(c["name"]),
            "hint": "",
            "doc": _doc(str(c.get("description") or "")),
            "source": SOURCES.get(str(c.get("source")), "opencode"),
        }
        for c in commands
        if isinstance(c, dict) and c.get("name")
    )
    models: list[Model] = []
    for p in providers.get("providers") or []:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        for mid, m in (p.get("models") or {}).items():
            if not isinstance(m, dict):
                continue
            value = f"{p['id']}/{mid}"
            window = (m.get("limit") or {}).get("context")
            models.append(
                Model(
                    value=value,
                    resolved=value,
                    label=str(m.get("name") or value),
                    doc="",
                    efforts=tuple((m.get("variants") or {}).keys()),
                    window=window if isinstance(window, int) else None,
                )
            )
    return Catalog(cmds, tuple(models))
```

In `src/aegis/mcp.py`, the primer's first paragraph names the prefix:

```python
def primer(session: Session, server_name: str) -> str:
    return PRIMER.format(
        handle=session.handle,
        server=server_name,
        tools=f"{session.harness.tool_prefix}*",
    )


PRIMER = """\
You are running inside aegis, a workplace for coding agents. Your handle is \
{handle}, on the server {server}. aegis's tools are the `aegis` MCP server's \
({tools}); they know who you are, so no tool takes your handle.
...(the rest unchanged)...
```

(the rest of `PRIMER` has no braces, so `format` leaves it as is; check with `grep -c '[{}]'` that only the three placeholders remain).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q tests/test_opencode_config.py tests/test_harness.py tests/test_control.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -q -F - -- src/aegis/opencode/config.py src/aegis/claude/control.py src/aegis/mcp.py \
  tests/test_opencode_config.py tests/test_harness.py <<'EOF'
feat: the config an OpenCode child starts with, and its catalog (#180)

The aegis MCP server with the session's token, permission rules in which
nothing asks, a provider/model split, and the catalog from /command and
/config/providers with each model's variants and context window. The primer
names aegis's tools by the harness's prefix.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 5: a fake `opencode serve`

**Files:**
- Create: `tests/fake_opencode.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces: the `fake_opencode` fixture (path to an executable), and the scripts listed in the fake's docstring, which Tasks 6 to 8 use.

- [ ] **Step 1: Write the fake**

`tests/fake_opencode.py`:

```python
"""A stand-in for ``opencode serve``, for tests and the bench.

It serves the v1 routes aegis uses on the port it prints, checks the basic
auth OpenCode checks, and emits events in the shapes OpenCode 1.18.31 emits
(``tests/fixtures/opencode/``). The text of a prompt picks a script:

    /sleep N        a bash call that takes N seconds, then text. Prompts sent
                    meanwhile are echoed at once and answered after the call,
                    in the same turn, as OpenCode reads them at the next step.
    /stall N        N seconds busy with no step, then text.
    /stream N       N words of text, one delta every 0.25 s.
    /fail           a bash call that fails.
    /bash D => OUT  a bash call described D whose output is OUT.
    /edit P         an edit of file P from "a" to "b" (no file is touched).
    /mcp T JSON     call tool T of the aegis MCP server in OPENCODE_CONFIG_CONTENT
                    with arguments JSON, as an aegis_T tool part.
    /task           a task call whose child session says one thing.
    /body           text "body: <JSON of the prompt body received>".
    /config         text "config: <OPENCODE_CONFIG_CONTENT>".
    /recall         text listing the prompts this session id received before.
    /exit N         a stderr line, then exit with code N.
    anything else   text "you said: <prompt>".

Text streams as an empty part, three deltas, then the full text. An assistant
message costs $0.002 and reports 1,030 tokens. After its first turn a session
is titled "Fake title: <first prompt>". ``/session/{id}/abort`` ends a running
script as OpenCode does: ``session.error`` (MessageAbortedError), idle, the
aborted tool part completed, idle again. ``/session/{id}/command`` echoes the
expanded template (``hello``: "Say hello to <args>."), runs it, and answers
when the turn ends; an unknown command is a 400.

Each session id's prompts are appended to ``$FAKE_OPENCODE_HOME/<id>.prompts``,
which is also how ``GET /session/{id}`` knows a session an earlier process
made. ``FAKE_OPENCODE_LOG=<file>`` gets one ``METHOD path`` line per request.
``FAKE_OPENCODE_DIE=1`` exits with code 1 before listening.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

VERSION = "1.18.31"
CONFIG = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT") or "{}")
PASSWORD = os.environ.get("OPENCODE_SERVER_PASSWORD")
HOME = os.environ.get("FAKE_OPENCODE_HOME") or tempfile.gettempdir()
LOG = os.environ.get("FAKE_OPENCODE_LOG")
COST = 0.002
TOKENS = {"total": 1030, "input": 1000, "output": 20, "reasoning": 10, "cache": {"read": 0, "write": 0}}
ZERO = {"total": 0, "input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}}
TEMPLATES = {"hello": "Say hello to $ARGUMENTS."}
COMMANDS = [
    {"name": "hello", "description": "Greet someone.", "source": "command"},
    {"name": "review", "description": "Review the changes.", "source": "command"},
    {"name": "unslop", "description": "Cut AI tells from any writing.", "source": "skill"},
]
PROVIDERS = {
    "providers": [
        {
            "id": "opencode-go",
            "name": "OpenCode Go",
            "models": {
                "fake-pro": {"id": "fake-pro", "name": "Fake Pro", "limit": {"context": 1000000, "output": 8192},
                             "variants": {"high": {}, "max": {}}},
                "fake-flash": {"id": "fake-flash", "name": "Fake Flash", "limit": {"context": 500000, "output": 8192},
                               "variants": {"low": {}, "high": {}, "max": {}}},
                "fake-plain": {"id": "fake-plain", "name": "Fake Plain", "limit": {"context": 200000, "output": 8192}},
            },
        }
    ],
    "default": {"opencode-go": "fake-flash"},
}  # fmt: skip

_lock = threading.Lock()
_n = [0]
subscribers: list[queue.Queue] = []
sessions: dict[str, "Sess"] = {}


def nid(prefix: str) -> str:
    with _lock:
        _n[0] += 1
        return f"{prefix}_{_n[0]:06d}{uuid.uuid4().hex[:8]}"


def now_ms() -> int:
    return int(time.time() * 1000)


def emit(type_: str, **props) -> None:
    line = json.dumps({"id": nid("evt"), "type": type_, "properties": props})
    with _lock:
        subs = list(subscribers)
    for q in subs:
        q.put(line)


class Aborted(Exception):
    pass


class Sess:
    def __init__(self, sid: str, parent: str | None = None) -> None:
        self.id, self.parent = sid, parent
        self.title = f"New session - {time.strftime('%Y-%m-%dT%H:%M:%S')}"
        self.model: dict | None = None
        self.inbox: queue.Queue = queue.Queue()
        self.abort = threading.Event()
        self.cond = threading.Condition()
        self.turns = 0
        self.first: str | None = None
        self.titled = False
        if parent is None:
            threading.Thread(target=worker, args=(self,), daemon=True).start()

    def info(self) -> dict:
        d = {"id": self.id, "slug": "fake-slug", "version": VERSION, "directory": os.getcwd(),
             "title": self.title, "time": {"created": now_ms(), "updated": now_ms()}}  # fmt: skip
        if self.parent:
            d["parentID"] = self.parent
        if self.model:
            d["model"] = self.model
        return d

    def end_turn(self) -> None:
        with self.cond:
            self.turns += 1
            self.cond.notify_all()


def prompts_file(sid: str) -> str:
    return os.path.join(HOME, f"{sid}.prompts")


def take_prompt(s: Sess, text: str, body: dict) -> None:
    with open(prompts_file(s.id), "a") as f:
        f.write(json.dumps(text) + "\n")
    m = body.get("model")
    if isinstance(m, str):
        p, _, i = m.partition("/")
        m = {"providerID": p, "modelID": i}
    if isinstance(m, dict) and m:
        s.model = {"id": m.get("modelID"), "providerID": m.get("providerID"),
                   "variant": body.get("variant") or "default"}  # fmt: skip
    mid = nid("msg")
    emit("message.updated", sessionID=s.id,
         info={"id": mid, "role": "user", "sessionID": s.id, "time": {"created": now_ms()}})  # fmt: skip
    emit("message.part.updated", sessionID=s.id, time=now_ms(),
         part={"id": nid("prt"), "messageID": mid, "sessionID": s.id, "type": "text", "text": text})  # fmt: skip
    emit("session.updated", sessionID=s.id, info=s.info())
    emit("session.diff", sessionID=s.id, diff=[])  # noise aegis drops
    s.inbox.put((text, body))


class Msg:
    def __init__(self, s: Sess) -> None:
        self.s, self.id, self.created = s, nid("msg"), now_ms()
        self.update()

    def update(self, finish: str | None = None, error: str | None = None, done: bool = False) -> None:
        m = self.s.model or {"id": "fake-flash", "providerID": "opencode-go"}
        info = {"id": self.id, "role": "assistant", "sessionID": self.s.id, "mode": "build",
                "modelID": m["id"], "providerID": m["providerID"],
                "cost": COST if done else 0, "tokens": TOKENS if done else ZERO,
                "time": {"created": self.created, **({"completed": now_ms()} if done else {})}}  # fmt: skip
        if finish:
            info["finish"] = finish
        if error:
            info["error"] = {"name": error, "data": {"message": "Aborted"}}
        emit("message.updated", sessionID=self.s.id, info=info)

    def part(self, pid: str | None = None, **p) -> str:
        pid = pid or nid("prt")
        emit("message.part.updated", sessionID=self.s.id, time=now_ms(),
             part={"id": pid, "messageID": self.id, "sessionID": self.s.id, **p})  # fmt: skip
        return pid

    def text(self, text: str, pace: float = 0.0, chunks: list[str] | None = None) -> None:
        pid = self.part(type="text", text="")
        if chunks is None:
            k = max(1, len(text) // 3)
            chunks = [text[:k], text[k : 2 * k], text[2 * k :]]
        for c in chunks:
            emit("message.part.delta", sessionID=self.s.id, messageID=self.id, partID=pid, field="text", delta=c)
            if pace and self.s.abort.wait(pace):
                raise Aborted
        self.part(pid, type="text", text=text)

    def step(self, reason: str) -> None:
        self.part(type="step-finish", reason=reason, tokens=TOKENS, cost=COST)
        self.update(reason, done=True)

    def tool_start(self, name: str, inp: dict) -> tuple[str, str]:
        call = nid("call")
        pid = self.part(type="tool", tool=name, callID=call, state={"status": "pending", "input": {}, "raw": ""})
        self.part(pid, type="tool", tool=name, callID=call,
                  state={"status": "running", "input": inp, "time": {"start": now_ms()}})  # fmt: skip
        return pid, call

    def tool_end(self, pid: str, call: str, name: str, inp: dict, output: str | None = None,
                 error: str | None = None) -> None:  # fmt: skip
        state = ({"status": "error", "input": inp, "error": error} if error is not None
                 else {"status": "completed", "input": inp, "output": output or "", "metadata": {}})  # fmt: skip
        self.part(pid, type="tool", tool=name, callID=call, state=state)

    def tool(self, name: str, inp: dict, output: str | None = None, error: str | None = None,
             wait: float = 0.0) -> None:  # fmt: skip
        pid, call = self.tool_start(name, inp)
        if wait and self.s.abort.wait(wait):
            abort_turn(self.s)
            self.tool_end(pid, call, name, inp,
                          output="(no output)\n\n<shell_metadata>\nUser aborted the command\n</shell_metadata>")  # fmt: skip
            self.update(error="MessageAbortedError", done=True)
            idle_events(self.s)
            raise Aborted
        self.tool_end(pid, call, name, inp, output=output, error=error)


def idle_events(s: Sess) -> None:
    emit("session.status", sessionID=s.id, status={"type": "idle"})
    emit("session.idle", sessionID=s.id)


def abort_turn(s: Sess) -> None:
    emit("session.error", sessionID=s.id, error={"name": "MessageAbortedError", "data": {"message": "Aborted"}})
    idle_events(s)


def drain(s: Sess) -> list[str]:
    out = []
    while True:
        try:
            out.append(s.inbox.get_nowait()[0])
        except queue.Empty:
            return out


def mcp_call(tool: str, arguments: dict) -> tuple[bool, str]:
    cfg = (CONFIG.get("mcp") or {}).get("aegis")
    if not cfg:
        return False, "no aegis MCP server configured"
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": arguments}})  # fmt: skip
    req = urllib.request.Request(cfg["url"], data=body.encode(), headers={
        "content-type": "application/json", "accept": "application/json, text/event-stream",
        **cfg.get("headers", {})})  # fmt: skip
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            reply = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    if "error" in reply:
        return False, json.dumps(reply["error"])
    res = reply.get("result") or {}
    text = "\n".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
    return not res.get("isError"), text


def after_tool(s: Sess, m: Msg, text: str) -> None:
    m.step("tool-calls")
    extra = drain(s)
    m2 = Msg(s)
    m2.part(type="step-start")
    m2.text(text + "".join(f"\nalso: {e}" for e in extra))
    m2.step("stop")


def run(s: Sess, text: str, body: dict) -> None:
    if s.first is None:
        s.first = text
    word, _, rest = text.partition(" ")
    if word == "/stall":
        if s.abort.wait(float(rest or 1)):
            abort_turn(s)
            raise Aborted
    if word == "/exit":
        print("fake opencode: exiting on request", file=sys.stderr, flush=True)
        os._exit(int(rest or 1))
    m = Msg(s)
    m.part(type="step-start")
    if word == "/sleep":
        m.tool("bash", {"command": f"sleep {rest}", "description": f"sleep {rest}"},
               output=f"slept {rest}\n", wait=float(rest or 1))  # fmt: skip
        return after_tool(s, m, f"done after {rest}")
    if word == "/fail":
        m.tool("bash", {"command": "false"}, error="exit code 1")
        return after_tool(s, m, "it failed")
    if word == "/bash":
        d, _, out = rest.partition(" => ")
        m.tool("bash", {"command": d, "description": d}, output=out)
        return after_tool(s, m, "ran it")
    if word == "/edit":
        m.tool("edit", {"filePath": rest, "oldString": "a", "newString": "b"}, output="Edit applied successfully.")
        return after_tool(s, m, "edited")
    if word == "/mcp":
        tool, _, args = rest.partition(" ")
        arguments = json.loads(args or "{}")
        ok, out = mcp_call(tool, arguments)
        m.tool(f"aegis_{tool}", arguments, output=out if ok else None, error=None if ok else out)
        return after_tool(s, m, "called it")
    if word == "/task":
        inp = {"description": "look around", "prompt": "List the files.", "subagentType": "general"}
        pid, call = m.tool_start("task", inp)
        child = Sess(nid("ses"), parent=s.id)
        sessions[child.id] = child
        emit("session.created", sessionID=child.id, info=child.info())
        cu = nid("msg")
        emit("message.updated", sessionID=child.id,
             info={"id": cu, "role": "user", "sessionID": child.id, "time": {"created": now_ms()}})  # fmt: skip
        emit("message.part.updated", sessionID=child.id, time=now_ms(),
             part={"id": nid("prt"), "messageID": cu, "sessionID": child.id, "type": "text", "text": "List the files."})  # fmt: skip
        cm = Msg(child)
        cm.part(type="step-start")
        cm.text("child looked around")
        cm.step("stop")
        idle_events(child)
        m.tool_end(pid, call, "task", inp, output="child done")
        return after_tool(s, m, "the child is done")
    if word == "/stream":
        words = [f"chunk{i} " for i in range(1, int(rest or 4) + 1)]
        m.text("".join(words), pace=0.25, chunks=words)
    elif word == "/body":
        m.text("body: " + json.dumps(body, sort_keys=True))
    elif word == "/config":
        m.text("config: " + json.dumps(CONFIG, sort_keys=True))
    elif word == "/recall":
        with open(prompts_file(s.id)) as f:
            earlier = [json.loads(x) for x in f.read().splitlines()][:-1]
        m.text("earlier: " + json.dumps(earlier))
    else:
        m.text(f"you said: {text}")
    m.step("stop")


def worker(s: Sess) -> None:
    while True:
        item = s.inbox.get()
        s.abort.clear()
        emit("session.status", sessionID=s.id, status={"type": "busy"})
        try:
            while True:
                run(s, *item)
                try:
                    item = s.inbox.get_nowait()
                except queue.Empty:
                    break
        except Aborted:
            drain(s)
            s.end_turn()
            continue
        if not s.titled and s.first:
            s.titled, s.title = True, f"Fake title: {s.first[:40]}"
            emit("session.updated", sessionID=s.id, info=s.info())
        idle_events(s)
        s.end_turn()


def new_session() -> Sess:
    s = Sess(nid("ses"))
    sessions[s.id] = s
    emit("session.created", sessionID=s.id, info=s.info())
    emit("session.updated", sessionID=s.id, info=s.info())
    emit("plugin.added", id="core/fake")  # noise aegis drops
    return s


def find(sid: str) -> Sess | None:
    s = sessions.get(sid)
    if s is None and os.path.exists(prompts_file(sid)):
        s = sessions[sid] = Sess(sid)
    return s


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def _json(self, code: int, obj) -> None:
        data = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(code)
        if data:
            self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authed(self) -> bool:
        if not PASSWORD:
            return True
        want = "Basic " + base64.b64encode(f"opencode:{PASSWORD}".encode()).decode()
        if self.headers.get("authorization") == want:
            return True
        self._json(401, {"name": "Unauthorized"})
        return False

    def _body(self) -> dict:
        n = int(self.headers.get("content-length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")

    def _route(self, method: str) -> None:
        path = urlparse(self.path).path
        if LOG:
            with open(LOG, "a") as f:
                f.write(f"{method} {path}\n")
        if not self._authed():
            return
        if method == "GET" and path == "/global/health":
            return self._json(200, {"healthy": True, "version": VERSION})
        if method == "GET" and path == "/event":
            return self._events()
        if method == "GET" and path == "/command":
            return self._json(200, COMMANDS)
        if method == "GET" and path == "/config/providers":
            return self._json(200, PROVIDERS)
        if method == "POST" and path == "/session":
            self._body()
            return self._json(200, new_session().info())
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "session":
            s = find(parts[1])
            if s is None:
                return self._json(404, {"name": "NotFoundError"})
            if method == "GET" and len(parts) == 2:
                return self._json(200, s.info())
            body = self._body()
            if parts[2:] == ["prompt_async"]:
                text = "".join(p.get("text", "") for p in body.get("parts") or [])
                take_prompt(s, text, body)
                return self._json(204, None)
            if parts[2:] == ["abort"]:
                s.abort.set()
                return self._json(200, True)
            if parts[2:] == ["command"]:
                name = body.get("command")
                if name not in {c["name"] for c in COMMANDS}:
                    return self._json(400, {"name": "CommandNotFound", "data": {"command": name}})
                text = TEMPLATES.get(name, f"Run {name}.").replace("$ARGUMENTS", body.get("arguments", ""))
                with s.cond:
                    before = s.turns
                take_prompt(s, text, body)
                with s.cond:
                    s.cond.wait_for(lambda: s.turns > before, timeout=60)
                return self._json(200, {"info": {"role": "assistant"}, "parts": []})
        return self._json(404, {"name": "NotFound"})

    def _events(self) -> None:
        q: queue.Queue = queue.Queue()
        with _lock:
            subscribers.append(q)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(b'data: {"id":"evt_0","type":"server.connected","properties":{}}\n\n')
            self.wfile.flush()
            while True:
                try:
                    line = q.get(timeout=10)
                except queue.Empty:
                    line = json.dumps({"id": nid("evt"), "type": "server.heartbeat", "properties": {}})
                self.wfile.write(f"data: {line}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _lock:
                subscribers.remove(q)


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] != "serve":
        print("fake opencode: only `serve` is faked", file=sys.stderr)
        sys.exit(2)
    if os.environ.get("FAKE_OPENCODE_DIE"):
        print("fake opencode: dying before listening", file=sys.stderr, flush=True)
        sys.exit(1)
    port = int(args[args.index("--port") + 1]) if "--port" in args else 0
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    print(f"opencode server listening on http://127.0.0.1:{srv.server_address[1]}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
```

In `tests/conftest.py`, next to `fake_claude`:

```python
FAKE_OPENCODE = Path(__file__).parent / "fake_opencode.py"


@pytest.fixture
def fake_opencode(tmp_path: Path) -> str:
    """An executable that runs the fake opencode, as a session would exec it."""
    path = tmp_path / "bin-opencode" / "opencode"
    path.parent.mkdir()
    home = tmp_path / "fake-opencode-home"
    home.mkdir()
    path.write_text(
        f'#!/bin/sh\nexport FAKE_OPENCODE_HOME="{home}"\n'
        f'exec "{sys.executable}" "{FAKE_OPENCODE}" "$@"\n'
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)
```

- [ ] **Step 2: Check it by hand**

```bash
FAKE_OPENCODE_HOME=$(mktemp -d) OPENCODE_SERVER_PASSWORD=pw uv run python tests/fake_opencode.py serve --port 0 &
sleep 1; P=$(jobs -p)
```

Read the printed port, then `curl -s -u opencode:pw http://127.0.0.1:<port>/global/health` answers `{"healthy": true, "version": "1.18.31"}` and `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:<port>/global/health` prints `401`. End it with `kill $P`. Task 6's tests drive every script.

- [ ] **Step 3: Commit**

```bash
git commit -q -F - -- tests/fake_opencode.py tests/conftest.py <<'EOF'
test: a fake opencode serve, scripted by the prompt's text (#180)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 6: the OpenCode process

**Files:**
- Create: `src/aegis/opencode/process.py`, `src/aegis/opencode/harness.py`, `tests/test_opencode_process.py`
- Modify: `src/aegis/harness.py`, `pyproject.toml`, `uv.lock`, `tests/test_harness.py`

**Interfaces:**
- Consumes: `Launch`, `Process` (Task 2); `STORED`, `DELTA`, `session_of` (Task 3); `child_config`, `split_model`, `catalog_from` (Task 4); `fake_opencode` (Task 5).
- Produces: `OpenCodeProcess(bin: str, launch: Launch)` implementing `Process`; `async probe(bin: str, cwd: Path, stderr_path: Path) -> Catalog`; `OpenCode(bin)` harness (`name="opencode"`, `src="opencode"`, `label="OpenCode"`, `tool_prefix="aegis_"`); `harness_for("opencode", ...)`.

- [ ] **Step 1: Add httpx as a direct dependency**

Run: `uv add 'httpx>=0.28'`
Expected: `pyproject.toml` lists `httpx` and `uv.lock` changes only in that entry (it was already locked through fastmcp). `uv lock --check` passes.

- [ ] **Step 2: Write the failing tests**

`tests/test_opencode_process.py`:

```python
import asyncio
import json
from pathlib import Path

import pytest

from aegis.harness import Launch
from aegis.opencode.process import OpenCodeProcess, probe

from .conftest import until


class Rig:
    def __init__(self, tmp_path: Path, bin: str, **kw):
        self.lines: list[dict] = []
        self.exits: list[tuple[int, list[str]]] = []
        self.errors: list[tuple[str, bool, str | None]] = []
        defaults = dict(
            cwd=tmp_path,
            model="opencode-go/fake-pro",
            effort="high",
            permission="full",
            resume_id=None,
            mcp=None,
            system_prompt="You are in aegis.",
            stderr_path=tmp_path / "stderr.log",
            on_line=lambda line: self.lines.append(json.loads(line)),
            on_exit=lambda code, tail: self.exits.append((code, tail)),
            on_error=lambda text, idle, line: self.errors.append((text, idle, line)),
        )
        self.launch = Launch(**(defaults | kw))
        self.proc = OpenCodeProcess(bin, self.launch)

    def types(self) -> list[str]:
        return [e["type"] for e in self.lines]

    def texts(self) -> list[str]:
        return [
            e["properties"]["part"]["text"]
            for e in self.lines
            if e["type"] == "message.part.updated"
            and e["properties"]["part"]["type"] == "text"
            and e["properties"]["part"]["text"]
        ]

    def idles(self) -> int:
        return self.types().count("session.idle")

    async def turn(self, text: str, n: int = 1) -> None:
        before = self.idles()
        await self.proc.send(text)
        await until(lambda: self.idles() >= before + n, timeout=5, what=f"{text!r} to end")


@pytest.fixture
async def rig(tmp_path, fake_opencode):
    r = Rig(tmp_path, fake_opencode)
    await r.proc.start()
    yield r
    await r.proc.terminate()


async def test_start_makes_a_session_and_only_its_kept_events_arrive(rig):
    assert rig.proc.session_id.startswith("ses_") and rig.proc.running
    await rig.turn("hello")
    assert "you said: hello" in rig.texts()
    assert "message.part.delta" in rig.types()
    assert not {"plugin.added", "session.diff", "server.heartbeat", "server.connected"} & set(rig.types())


async def test_the_prompt_carries_model_variant_and_system(rig):
    await rig.turn("/body")
    body = json.loads(rig.texts()[-1].removeprefix("body: "))
    assert body["model"] == {"providerID": "opencode-go", "modelID": "fake-pro"}
    assert (body["variant"], body["system"]) == ("high", "You are in aegis.")


async def test_an_effort_the_model_does_not_list_is_not_sent(rig):
    await rig.proc.set("model", "opencode-go/fake-plain")
    await rig.turn("/body")
    body = json.loads(rig.texts()[-1].removeprefix("body: "))
    assert body["model"]["modelID"] == "fake-plain" and "variant" not in body


async def test_a_resume_keeps_the_id_and_an_unknown_one_starts_anew(tmp_path, fake_opencode):
    first = Rig(tmp_path, fake_opencode)
    await first.proc.start()
    await first.turn("remember me")
    sid = first.proc.session_id
    await first.proc.terminate()
    again = Rig(tmp_path, fake_opencode, resume_id=sid)
    await again.proc.start()
    try:
        assert again.proc.session_id == sid
        await again.turn("/recall")
        assert "remember me" in again.texts()[-1]
    finally:
        await again.proc.terminate()
    lost = Rig(tmp_path, fake_opencode, resume_id="ses_gone")
    await lost.proc.start()
    try:
        assert lost.proc.session_id not in (None, "ses_gone")
    finally:
        await lost.proc.terminate()


async def test_a_permission_change_restarts_the_child_at_the_next_send_only(rig):
    pid = rig.proc.pid
    await rig.proc.send("/sleep 1")
    await rig.proc.set("permission", "read")
    await rig.proc.send("steer this")  # mid-turn: no restart
    assert rig.proc.pid == pid
    await until(lambda: rig.idles() >= 1, timeout=5, what="the turn")
    await rig.turn("/config")
    assert rig.proc.pid != pid
    rules = json.loads(rig.texts()[-1].removeprefix("config: "))["permission"]
    assert rules["edit"] == "deny" and rules["bash"] == "deny"


async def test_interrupt_aborts_the_turn(rig):
    await rig.proc.send("/sleep 5")
    await until(lambda: "message.part.updated" in rig.types(), what="the call")
    await asyncio.sleep(0.2)
    await rig.proc.interrupt()
    await until(lambda: "session.error" in rig.types(), what="the abort")


async def test_a_command_runs_without_blocking_and_an_unknown_one_is_an_error(rig):
    await rig.proc.send("/hello the okapi")  # returns before the turn ends
    await until(lambda: "you said: Say hello to the okapi." in rig.texts(), what="the command")
    await until(lambda: rig.idles() >= 1, what="its end")
    await rig.proc.send("/nope x")
    await until(lambda: rig.errors, what="the refusal")
    text, idle, line = rig.errors[0]
    assert "400" in text and idle and line == "/nope x"


async def test_an_exit_reports_the_code_and_the_stderr_tail(rig):
    await rig.proc.send("/exit 3")
    await until(lambda: rig.exits, what="the exit")
    code, tail = rig.exits[0]
    assert code == 3 and any("exiting on request" in t for t in tail)
    assert not rig.proc.running


async def test_a_missing_binary_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        await Rig(tmp_path, str(tmp_path / "no-opencode")).proc.start()


async def test_a_child_that_dies_before_listening_raises(tmp_path, fake_opencode, monkeypatch):
    monkeypatch.setenv("FAKE_OPENCODE_DIE", "1")
    with pytest.raises(OSError, match="dying before listening"):
        await Rig(tmp_path, fake_opencode).proc.start()


async def test_the_catalog_and_a_probe(rig, tmp_path, fake_opencode):
    cat = await rig.proc.catalog()
    assert cat.has("hello") and cat.model("opencode-go/fake-pro").window == 1000000
    probed = await probe(fake_opencode, tmp_path, tmp_path / "probe.log")
    assert [m.value for m in probed.models] == [m.value for m in cat.models]
```

Append to `tests/test_harness.py`:

```python
def test_opencode_is_a_harness(tmp_path):
    from aegis.opencode.harness import OpenCode

    h = harness_for("opencode", "claude", "/bin/opencode")
    assert isinstance(h, OpenCode)
    assert (h.name, h.src, h.label, h.tool_prefix, h.bin) == (
        "opencode", "opencode", "OpenCode", "aegis_", "/bin/opencode",
    )  # fmt: skip
```

and extend `test_the_primer_names_tools_by_the_harness_prefix` with `assert "(aegis_*)" in primer(make("opencode"), "zion")`.

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest -q tests/test_opencode_process.py tests/test_harness.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.opencode.process'`.

- [ ] **Step 4: Implement the process**

`src/aegis/opencode/process.py`:

```python
"""One ``opencode serve`` child per session, driven over HTTP.

Measured on OpenCode 1.18.31 (spec ``2026-10-08-aegis-2-opencode-harness-design.md``):
the v1 routes run a turn and the v2 ``/api/session`` routes do not;
``--port 0`` takes 4096 when it is free and another free port otherwise, so the
port is read from the line the server prints; ``POST /session/{id}/command``
blocks until the turn ends, so it runs on its own task.

The child reads its config once, at start, from ``OPENCODE_CONFIG_CONTENT``:
the aegis MCP server with this session's token, and the permission rules. A
permission change therefore restarts the child before the next prompt that
comes after the turn, and the new child resumes the same OpenCode session. A
random password guards the port, because any process or page on the machine
can reach a localhost port.

Events reach ``on_line`` as they arrive, only this session's and its child
sessions', and only the types the fold reads (``stream.STORED``) plus deltas.
If the event stream closes while the child runs, the child is ended and its
exit reported: a session that can no longer hear its harness must not look
alive.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import secrets
from collections import deque
from pathlib import Path
from typing import Any

import httpx

from ..claude.control import Catalog
from ..harness import Launch
from .config import catalog_from, child_config, split_model
from .stream import DELTA, STORED, session_of

START_S = 15.0
REQUEST_S = 30.0
TERM_GRACE_S = 5.0
STDERR_TAIL = 20
_LISTENING = re.compile(r"listening on (http://\S+)")


class OpenCodeProcess:
    def __init__(self, bin: str, launch: Launch) -> None:
        self._bin = bin
        self._launch = launch
        self.model, self.effort, self.permission = (
            launch.model,
            launch.effort,
            launch.permission,
        )
        self._session_id: str | None = None
        self._children: set[str] = set()
        self._proc: asyncio.subprocess.Process | None = None
        self._client: httpx.AsyncClient | None = None
        self._tasks: set[asyncio.Task] = set()
        self._pump_task: asyncio.Task | None = None
        self._wait_task: asyncio.Task | None = None
        self._connected = asyncio.Event()
        self._early: list[str] = []
        self._catalog: Catalog | None = None
        self._busy = False
        self._restart = False
        self._quiet = False  # ending the child on purpose: report nothing
        self._tail: deque[str] = deque(maxlen=STDERR_TAIL)

    # -- what the session reads --------------------------------------
    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    # -- lifecycle ----------------------------------------------------
    async def start(self) -> None:
        await self._spawn()
        resume = self._launch.resume_id
        if resume and await self._exists(resume):
            self._session_id = resume
        else:
            self._session_id = (await self._call("POST", "/session", json={}))["id"]
        early, self._early = self._early, []
        for raw in early:
            self._deliver(raw)
        self._catalog = await self._read_catalog()

    async def _spawn(self) -> None:
        self._quiet = False
        password = secrets.token_hex(16)
        env = {
            **os.environ,
            "OPENCODE_CONFIG_CONTENT": json.dumps(
                child_config(self._launch.mcp, self.permission)
            ),
            "OPENCODE_SERVER_PASSWORD": password,
        }
        self._launch.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        self._proc = await asyncio.create_subprocess_exec(
            self._bin, "serve", "--hostname", "127.0.0.1", "--port", "0",
            cwd=self._launch.cwd, env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        self._keep(self._drain(self._proc.stderr))
        try:
            url = await asyncio.wait_for(self._listening(), START_S)
        except (TimeoutError, OSError):
            await self._end_child()
            raise
        self._keep(self._drain(self._proc.stdout))
        self._client = httpx.AsyncClient(
            base_url=url,
            auth=("opencode", password),
            params={"directory": str(self._launch.cwd)},
            timeout=httpx.Timeout(REQUEST_S, read=None),
        )
        self._connected = asyncio.Event()
        self._pump_task = asyncio.create_task(self._pump())
        try:
            await asyncio.wait_for(self._connected.wait(), START_S)
        except TimeoutError:
            await self._end_child()
            raise
        self._wait_task = asyncio.create_task(self._wait())

    async def _listening(self) -> str:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            raw = await self._proc.stdout.readline()
            if not raw:
                await self._proc.wait()
                await asyncio.sleep(0.05)  # let the stderr drain catch up
                tail = "; ".join(self._tail) or "no output"
                raise ConnectionError(
                    f"opencode serve exited with code {self._proc.returncode} "
                    f"before listening: {tail}"
                )
            line = raw.decode(errors="replace").strip()
            if m := _LISTENING.search(line):
                return m.group(1)
            self._note(line)

    def _note(self, line: str) -> None:
        if not line:
            return
        self._tail.append(line)
        with self._launch.stderr_path.open("a") as f:
            f.write(line + "\n")

    async def _drain(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while raw := await stream.readline():
            self._note(raw.decode(errors="replace").rstrip())

    def _keep(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def _wait(self) -> None:
        assert self._proc is not None
        code = await self._proc.wait()
        if self._quiet:
            return
        self._quiet = True
        await self._close()
        self._launch.on_exit(code, list(self._tail))

    async def terminate(self) -> None:
        await self._end_child()

    async def _end_child(self) -> None:
        self._quiet = True
        await self._close()
        proc = self._proc
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), TERM_GRACE_S)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        if self._wait_task is not None:
            self._wait_task.cancel()

    async def _close(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
        for t in list(self._tasks):
            t.cancel()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- events -------------------------------------------------------
    async def _pump(self) -> None:
        assert self._client is not None
        try:
            async with self._client.stream("GET", "/event") as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        self._deliver(line[5:].strip())
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as e:
            self._note(f"the event stream failed: {e}")
        if not self._quiet and self.running:
            self._note("the event stream closed")
            assert self._proc is not None
            self._proc.terminate()  # _wait reports the exit

    def _deliver(self, raw: str) -> None:
        try:
            obj: Any = json.loads(raw)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            kind = obj.get("type")
            if kind == "server.connected":
                self._connected.set()
                return
            if kind not in STORED and kind != DELTA:
                return
            props = obj.get("properties") if isinstance(obj.get("properties"), dict) else {}
            if self._session_id is None:
                self._early.append(raw)
                return
            info = props.get("info") if isinstance(props.get("info"), dict) else {}
            if kind == "session.created" and info.get("parentID") in (
                {self._session_id} | self._children
            ):
                self._children.add(str(info.get("id")))
            sid = session_of(props)
            if sid != self._session_id and sid not in self._children:
                return
            if kind == "session.status" and sid == self._session_id:
                self._busy = (props.get("status") or {}).get("type") == "busy"
            if kind == "session.idle" and sid == self._session_id:
                self._busy = False
        self._launch.on_line(raw)

    # -- requests -----------------------------------------------------
    async def _call(
        self, method: str, path: str, *, json: Any = None, timeout: float | None = REQUEST_S
    ) -> Any:
        if self._client is None:
            raise ConnectionResetError("opencode serve is not running")
        req = self._client.request(method, path, json=json)
        try:
            r = await (asyncio.wait_for(req, timeout) if timeout else req)
        except httpx.TransportError as e:
            raise ConnectionResetError(str(e)) from e
        r.raise_for_status()
        return r.json() if r.content else None

    async def _exists(self, sid: str) -> bool:
        try:
            await self._call("GET", f"/session/{sid}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return False
            raise
        return True

    async def _read_catalog(self) -> Catalog:
        commands = await self._call("GET", "/command")
        providers = await self._call("GET", "/config/providers")
        return catalog_from(commands or [], providers or {})

    def _variant(self) -> str | None:
        m = self._catalog.model(self.model) if self._catalog else None
        return self.effort if m is not None and self.effort in m.efforts else None

    async def _maybe_restart(self) -> None:
        if not self._restart or self._busy:
            return
        self._restart = False
        await self._end_child()
        self._launch = dataclasses.replace(self._launch, resume_id=self._session_id)
        await self._spawn()

    async def send(self, text: str) -> None:
        await self._maybe_restart()
        if text.startswith("/"):
            name, _, args = text[1:].partition(" ")
            self._keep(self._command(name, args.strip(), text))
            return
        body: dict[str, Any] = {
            "parts": [{"type": "text", "text": text}],
            "model": split_model(self.model),
        }
        if variant := self._variant():
            body["variant"] = variant
        if self._launch.system_prompt:
            body["system"] = self._launch.system_prompt
        await self._call("POST", f"/session/{self._session_id}/prompt_async", json=body)

    async def _command(self, name: str, args: str, line: str) -> None:
        body: dict[str, Any] = {"command": name, "arguments": args, "model": self.model}
        if variant := self._variant():
            body["variant"] = variant
        try:
            await self._call(
                "POST", f"/session/{self._session_id}/command", json=body, timeout=None
            )
        except httpx.HTTPStatusError as e:
            r = e.response
            self._launch.on_error(
                f"/{name} failed: {r.status_code} {r.text[:200]}", not self._busy, line
            )
        except ConnectionResetError:
            pass  # the child is gone; its exit is reported

    async def interrupt(self) -> None:
        await self._call("POST", f"/session/{self._session_id}/abort", json={})

    async def set(self, kind: str, value: str) -> None:
        if kind == "model":
            self.model = value
        elif kind == "effort":
            self.effort = value
        elif kind == "permission" and value != self.permission:
            self.permission, self._restart = value, True

    async def catalog(self) -> Catalog:
        if self._catalog is None:
            self._catalog = await self._read_catalog()
        return self._catalog


async def probe(bin: str, cwd: Path, stderr_path: Path) -> Catalog:
    """A catalog for a cwd with no live process: start ``opencode serve``,
    ask, end it. About 3 s and no tokens."""
    p = OpenCodeProcess(
        bin,
        Launch(
            cwd=cwd, model="", effort="", permission="full", resume_id=None, mcp=None,
            system_prompt=None, stderr_path=stderr_path,
            on_line=lambda line: None, on_exit=lambda code, tail: None,
        ),
    )  # fmt: skip
    await p._spawn()
    try:
        return await p._read_catalog()
    except httpx.HTTPError as e:
        raise ConnectionError(f"opencode serve gave no catalog: {e}") from e
    finally:
        await p.terminate()
```

`src/aegis/opencode/harness.py`:

```python
"""OpenCode behind the harness interface: ``opencode serve`` over HTTP."""

from __future__ import annotations

from pathlib import Path

from ..claude.control import Catalog
from ..harness import Launch
from .process import OpenCodeProcess, probe
from .stream import LABEL


class OpenCode:
    name = "opencode"
    src = "opencode"
    label = LABEL
    tool_prefix = "aegis_"

    def __init__(self, bin: str) -> None:
        self.bin = bin

    def process(self, launch: Launch) -> OpenCodeProcess:
        return OpenCodeProcess(self.bin, launch)

    async def probe(self, spec, stderr_path: Path) -> Catalog:
        return await probe(self.bin, spec.cwd, stderr_path)
```

In `src/aegis/harness.py`, `harness_for` gains, before the `raise`:

```python
    if name == "opencode":
        from .opencode.harness import OpenCode

        return OpenCode(opencode_bin)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q tests/test_opencode_process.py tests/test_harness.py`
Expected: PASS, each test well under 3 s.

- [ ] **Step 6: Commit**

```bash
git commit -q -F - -- src/aegis/opencode/process.py src/aegis/opencode/harness.py \
  src/aegis/harness.py pyproject.toml uv.lock tests/test_opencode_process.py tests/test_harness.py <<'EOF'
feat: run opencode serve as a session's process (#180)

One child per session on the port it prints, behind a random password, with
the aegis MCP token and permission rules in OPENCODE_CONFIG_CONTENT. Its
events are filtered to the session and its children; prompts carry the
model, the variant and the primer; commands run on their own task; a
permission change restarts the child at the next send after the turn.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 7: OpenCode sessions

**Files:**
- Create: `tests/test_session_opencode.py`
- Modify: `src/aegis/session.py`, `src/aegis/registry.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `Session.title_set` (meta key `"title_set"`); `Session._on_line` folds `Delta` live and takes `Title` unless `title_set`; `context_window` from the catalog model's `window`; a `reset` record when a resume came back with a new id.

- [ ] **Step 1: Write the failing tests**

`tests/test_session_opencode.py`:

```python
from pathlib import Path

import pytest

from aegis.meta import MetaStore
from aegis.session import Session, SpawnSpec
from aegis.transcript.entries import fold_records
from aegis.transcript.store import Store, read_store

from .conftest import until


class OC:
    def __init__(self, tmp_path: Path, fake: str):
        self.published: list[tuple[str, list[dict]]] = []
        self.path = tmp_path / "state" / "transcripts" / "log-oc.jsonl"
        self.metas = MetaStore(tmp_path / "state" / "sessions")
        self.tmp_path, self.fake = tmp_path, fake
        self.session = self.make()

    def make(self, **meta) -> Session:
        return Session(
            log_id="log-oc",
            spec=SpawnSpec("deepseek", "opencode-go/fake-pro", "high", "full",
                           self.tmp_path, harness="opencode"),  # fmt: skip
            handle="quiet-okapi",
            store=Store(self.path),
            stderr_path=self.tmp_path / "state" / "stderr" / "log-oc.log",
            claude_bin="claude-unused",
            opencode_bin=self.fake,
            publish=lambda ch, ops: self.published.append((ch, ops)),
            metas=self.metas,
            **meta,
        )

    def statuses(self) -> list[str]:
        out: list[str] = []
        for ch, ops in self.published:
            if ch == "sessions":
                for op in ops:
                    st = op["upsert"]["state"]
                    if not out or out[-1] != st:
                        out.append(st)
        return out

    def prose(self) -> list[str]:
        return [e["md"] for e in self.session.entries() if e["kind"] == "prose"]

    def done_lines(self) -> int:
        return sum(
            e["summary"].startswith(("done in", "interrupted"))
            for e in self.session.entries()
        )

    async def turn(self, text: str) -> None:
        before = self.done_lines()
        await self.session.send(text)
        await until(
            lambda: self.done_lines() > before and self.session.status == "idle",
            timeout=5, what=f"{text!r}",
        )  # fmt: skip

    def refold_matches(self) -> bool:
        records, damaged = read_store(self.path)
        return damaged == 0 and fold_records(records).entries() == self.session.entries()

    def patches_rebuild_entries(self) -> bool:
        shown: dict[str, dict] = {}
        for ch, ops in self.published:
            if ch != self.session.channel:
                continue
            for op in ops:
                if "upsert" in op:
                    shown[op["upsert"]["id"]] = op["upsert"]
                else:
                    shown.pop(op["remove"], None)
        return list(shown.values()) == self.session.entries()


@pytest.fixture
async def oc(tmp_path, fake_opencode):
    h = OC(tmp_path, fake_opencode)
    await h.session.start()
    yield h
    await h.session.stop()
    assert h.refold_matches(), "live entries differ from a fold of the store"
    assert h.patches_rebuild_entries(), "the published patches do not add up"


async def test_a_prompt_runs_a_turn_with_cost_context_title_and_model(oc):
    await oc.turn("hello")
    s = oc.session
    assert oc.statuses() == ["idle", "working", "idle"]
    assert oc.prose() == ["you said: hello"]
    assert s.cost_usd == pytest.approx(0.002) and s.context_tokens == 1030
    assert s.context_window == 1000000 and s.model_id == "opencode-go/fake-pro"
    assert s.resume_id and s.resume_id.startswith("ses_")
    assert s.title == "Fake title: hello"
    assert s.entries()[1]["summary"] == "OpenCode 1.18.31 · opencode-go/fake-pro"


async def test_text_streams_before_the_turn_ends(oc):
    await oc.session.send("/stream 4")
    await until(lambda: any("chunk1" in m for m in oc.prose()), what="the first chunk")
    assert oc.done_lines() == 0
    await until(lambda: oc.done_lines() == 1, timeout=5, what="the end")
    assert oc.prose() == ["chunk1 chunk2 chunk3 chunk4 "]


async def test_a_prompt_sent_mid_turn_is_answered_in_the_same_turn(oc):
    await oc.session.send("/sleep 0.6")
    await until(lambda: any(e["kind"] == "tool" for e in oc.session.entries()), what="the call")
    await oc.session.send("steer this")
    await until(lambda: oc.done_lines() == 1 and oc.session.status == "idle", timeout=5, what="idle")
    assert [e["status"] for e in oc.session.entries() if e["kind"] == "user"] == ["ok", "ok"]
    assert "also: steer this" in oc.prose()[-1]


async def test_an_inbox_message_waits_for_the_turn_and_arrives_as_its_own(oc):
    await oc.session.send("/sleep 0.5")
    await until(lambda: oc.session.status == "working", what="working")
    await oc.session.deliver("> from monitor:m1 · done", "the build finished")
    assert len(oc.session.held) == 1
    await until(
        lambda: any(e["kind"] == "inbox" for e in oc.session.entries())
        and oc.session.status == "idle",
        timeout=5,
        what="the inbox turn",
    )
    assert oc.session.held == [] and oc.done_lines() == 2


async def test_interrupt_marks_the_call_interrupted_and_it_stays_so(oc):
    await oc.session.send("/sleep 5")
    await until(lambda: any(e["kind"] == "tool" for e in oc.session.entries()), what="the call")
    await oc.session.interrupt()
    await until(lambda: oc.session.status == "idle", timeout=5, what="idle")
    await oc.turn("after")  # the late tool part has long arrived
    (t,) = [e for e in oc.session.entries() if e["kind"] == "tool"]
    assert (t["status"], t["detail"]["result"]) == ("err", "interrupted")


async def test_stop_and_resume_keep_the_conversation(oc):
    await oc.turn("remember PELICAN")
    sid = oc.session.resume_id
    await oc.session.stop()
    await oc.turn("/recall")
    assert oc.session.resume_id == sid and "PELICAN" in oc.prose()[-1]


async def test_a_resume_opencode_lost_starts_anew_and_says_so(oc):
    await oc.turn("first")
    await oc.session.stop()
    oc.session.resume_id = "ses_gone"
    await oc.turn("second")
    assert oc.session.resume_id not in ("ses_gone", None)
    assert any("no longer had this conversation" in e["summary"] for e in oc.session.entries())


async def test_model_and_effort_apply_from_the_next_prompt(oc):
    await oc.session.configure(model="opencode-go/fake-flash", effort="low")
    await oc.turn("/body")
    assert '"modelID": "fake-flash"' in oc.prose()[-1] and '"variant": "low"' in oc.prose()[-1]
    assert oc.session.context_window == 500000


async def test_a_permission_change_restarts_the_child_before_the_next_prompt(oc):
    pid = oc.session.pid
    await oc.session.configure(permission="read")
    await oc.turn("/config")
    assert oc.session.pid != pid and '"bash": "deny"' in oc.prose()[-1]


async def test_a_title_someone_set_is_kept(oc):
    oc.session.title_set = True
    oc.session._set(title="mine")
    await oc.turn("hello")
    assert oc.session.title == "mine"


async def test_a_command_shows_as_typed_and_an_unknown_one_ends_the_turn(oc):
    await oc.turn("/hello the okapi")
    users = [e for e in oc.session.entries() if e["kind"] == "user"]
    assert users[-1]["md"] == "/hello the okapi"
    assert users[-1]["detail"]["tail"] == "Say hello to the okapi."
    await oc.session.send("/nope x")
    await until(lambda: oc.session.status == "idle", what="idle after the refusal")
    assert any(e["kind"] == "error" and "/nope failed" in e["summary"] for e in oc.session.entries())


async def test_an_exit_leaves_the_session_stopped(oc):
    await oc.session.send("/exit 3")
    await until(lambda: oc.session.status == "stopped", timeout=5, what="stopped")
    assert oc.session.entries()[-1]["summary"] == "OpenCode exited with code 3"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_session_opencode.py`
Expected: FAIL: `TypeError: Session.__init__() got an unexpected keyword argument 'title_set'` in the title test, the title stays `hello` in the first test, the streaming test sees no prose before the end, and the reset test finds no "no longer had" entry.

- [ ] **Step 3: Implement**

In `src/aegis/session.py`:

- Import `Delta, Title` from `.claude.stream` alongside the others.
- `__init__` gains `title_set: bool = False`, stored as `self.title_set`; `meta()` adds `"title_set": self.title_set`.
- `ensure_running`, after `await proc.start()` and `self._proc = proc` and the resume record:

```python
        sid = proc.session_id
        if resume and sid and sid != resume:
            self._record(
                {
                    "kind": "reset",
                    "text": f"{self.harness.label} no longer had this conversation; "
                    "it started a new one",
                }
            )
        if sid:
            self._set(resume_id=sid)
```

- `_fetch_catalog`, after `self._host.catalog_ready(self, cat)`: `self._window_from(cat)`, with

```python
    def _window_from(self, cat: Catalog | None) -> None:
        """A harness whose events name no context window (OpenCode) has it in
        its catalog."""
        m = cat.model(self.spec.model) if cat is not None else None
        if m is not None and m.window:
            self._set(context_window=m.window)
```

- `configure`, in the `finally` after the spec is replaced, when `"model" in applied` and `self.catalog_task` is done without error: `self._window_from(self.catalog_task.result())`.
- `_on_line`:

```python
    def _on_line(self, line: str) -> None:
        if self._stopping:
            return
        fold = self.fold()
        events = fold.parse(self.harness.src, line)
        if events and all(isinstance(ev, Delta) for ev in events):
            # Never stored: the part's closing update carries the whole text.
            self._publish(self.channel, fold.live(events))
            self._set(
                activity=fold.activity(),
                **({"status": "working"} if self.status == "idle" else {}),
            )
            return
        self._record({"src": self.harness.src, "line": line}, events)
        changes: dict[str, object] = {}
        for ev in events:
            ...(the existing loop)...
            if isinstance(ev, Title) and not self.title_set:
                changes["title"] = ev.text
```

In `src/aegis/registry.py`: `_session` passes `title_set=bool(meta.get("title_set"))`; `rename` sets it when a title is given, for a live session (`s.title_set = True` before `s._set(**changes)`) and for an archived one (`meta["title_set"] = True`).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q tests/test_session_opencode.py tests/test_session.py tests/test_registry.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -q -F - -- src/aegis/session.py src/aegis/registry.py tests/test_session_opencode.py <<'EOF'
feat: OpenCode sessions stream, resume, retitle and know their window (#180)

Deltas are folded and published live without a store record. OpenCode's
title replaces the first prompt's unless someone set one. The context window
comes from the catalog, and a resume OpenCode no longer knows starts a new
conversation and says so.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 8: spawning OpenCode agents from the browser

**Files:**
- Modify: `src/aegis/agents.py`, `src/aegis/app.py`, `src/aegis/cli.py`, `tests/test_agent_config.py`, `tests/test_web.py`, `tests/test_browser.py`

**Interfaces:**
- Consumes: Tasks 2 to 7.
- Produces: `SUPPORTED_HARNESSES == HARNESSES == ("claude-code", "opencode")`; an OpenCode agent or spawn without `provider/model` fails with `bad_model`; `aegis serve --opencode <bin>`; the spawn error code `harness_not_found`.

- [ ] **Step 1: Change the tests to the new behaviour**

`tests/test_agent_config.py`:
- `test_an_unsupported_harness_is_disabled_but_not_an_error` uses `provider: lovelaice`.
- In the `agents` fixture, `deep` gets `model: opencode-go/fake-pro`.
- In `test_resolve_errors`, replace the two `harness_unsupported` rows with `(None, "lovelaice-agent", {}, "harness_unsupported")` (add `lovelaice-agent: {harness: lovelaice, model: m, effort: high, permission: full}` to the fixture) and `(None, "opus", {"harness": "opencode"}, "bad_model")`.
- Add:

```python
def test_an_opencode_agent_names_provider_and_model(tmp_path):
    (a,) = load_agents(
        write(tmp_path, "agents:\n  d: {provider: opencode, model: x, effort: high, permission: full}\n")
    )
    assert a.error == "an OpenCode model is provider/model, such as opencode-go/deepseek-v4-pro"
    assert not a.enabled
```

`tests/test_web.py`:
- The `project` fixture's `deepseek` gets `model: opencode-go/fake-pro`.
- `test_agents_list` (the one asserting `("deepseek", False, None)`) expects `("deepseek", True, None)`, `{"name": "opencode", "supported": True}` and `"opencode": ["opencode-go/fake-pro"]`.
- `test_spawn_errors`: `({"agent": "deepseek"}, ...)` is removed (it spawns now), and `({"agent": "opus", "harness": "opencode"}, "bad_model")`.
- `test_a_missing_claude_leaves_no_session_behind` expects `"harness_not_found"`.

`tests/test_browser.py`:
- `Server.__init__(self, root, claude, opencode=None)` stores `opencode` and, when set, `start()` adds `"--opencode", self.opencode` to the `aegis serve` command.
- `CONFIG`'s `deepseek` uses `model: opencode-go/fake-pro`.
- The `server` fixture takes `fake_opencode` too and builds `Server(tmp_path, fake_claude, fake_opencode)`.
- A new test:

```python
def test_an_opencode_session_streams_and_calls_aegis(server, page):
    page.goto(server.url)
    page.click("#tab-add")
    page.wait_for_function("document.querySelector('#sp-agent').value !== ''")
    page.select_option("#sp-agent", "deepseek")
    page.fill("#sp-text", "/stream 6")
    page.press("#sp-text", "Enter")
    page.wait_for_selector("#a2[data-view=session]")
    page.wait_for_function(
        "[...document.querySelectorAll('.row.prose .body')].some(b => b.textContent.includes('chunk1'))"
    )
    done = "[...document.querySelectorAll('.row.sys .body')].filter(b => /^done in/.test(b.textContent)).length"
    assert page.evaluate(done) == 0, "the text is drawn before the turn ends"
    turns_done(page, 1)

    page.fill("#input", "/mcp meta {}")
    page.press("#input", "Enter")
    turns_done(page, 2)
    assert "meta" in page.locator(".row.tool.ok").last.inner_text()
    assert "OpenCode 1.18.31" in page.inner_text("#entries")
    assert page.errors == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_agent_config.py tests/test_web.py`
Expected: FAIL on `harness_unsupported` versus `bad_model`, `enabled` False, and `claude_not_found`.

- [ ] **Step 3: Implement**

`src/aegis/agents.py`:

```python
HARNESSES = ("claude-code", "opencode")
SUPPORTED_HARNESSES = HARNESSES
...
OPENCODE_MODEL = "an OpenCode model is provider/model, such as opencode-go/deepseek-v4-pro"


def _model_error(harness: str, model: str) -> str | None:
    if harness == "opencode" and "/" not in model:
        return OPENCODE_MODEL
    return None
```

In `_agent`, after the existing `error` is computed: `error = error or _model_error(values["harness"], values["model"])`. In `resolve`, after the `harness_unsupported` check:

```python
    if err := _model_error(fields["harness"], fields["model"]):
        raise OpError("bad_model", err)
```

`src/aegis/app.py`: both `except FileNotFoundError` handlers in `session.spawn` and `session.send` raise

```python
                raise OpError(
                    "harness_not_found", f"cannot run {self._bin(spec.harness)!r}: {e}"
                ) from e
```

(`s.spec.harness` in `session.send`), with

```python
    def _bin(self, harness: str) -> str:
        return self.opencode_bin if harness == "opencode" else self.claude_bin
```

and `_dead`'s message says "the agent stopped while being written to".

`src/aegis/cli.py`: `serve` gains `opencode: str = typer.Option("opencode", help="The opencode executable to run.")`, passes `opencode_bin=opencode` to `App`, and `_detach` takes it and adds `"--opencode", opencode` to the re-exec command.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q tests/test_agent_config.py tests/test_web.py tests/test_agents.py tests/test_commands.py`, then `uv run pytest -q -m browser tests/test_browser.py`
Expected: PASS, the new browser test included.

- [ ] **Step 5: Commit**

```bash
git commit -q -F - -- src/aegis/agents.py src/aegis/app.py src/aegis/cli.py \
  tests/test_agent_config.py tests/test_web.py tests/test_browser.py <<'EOF'
feat: spawn OpenCode agents (#180)

opencode is a supported harness. Its agents name provider/model or are
refused as bad_model; aegis serve takes --opencode; a missing binary of
either harness is harness_not_found.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 9: the live test, the bench, the docs

**Files:**
- Modify: `tests/test_live.py`, `scripts/bench.py`, `DESIGN.md`, `docs/superpowers/specs/2026-10-08-aegis-2-opencode-harness-design.md`
- Create: `changelog.d/180-opencode-sessions.added.md`

- [ ] **Step 1: The live test**

Append to `tests/test_live.py`:

```python
FLASH = "opencode-go/deepseek-v4-flash"


async def test_a_real_opencode_session(tmp_path: Path):
    """A prompt, an aegis tool call, an interrupt, a resume that keeps the
    context, and a read session refused an edit. About a cent of Go."""
    import asyncio

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    opencode = shutil.which("opencode")
    assert opencode, "opencode is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        f"  deep: {{harness: opencode, model: {FLASH}, effort: high, permission: full}}\n"
        f"  reader: {{harness: opencode, model: {FLASH}, effort: high, permission: read}}\n"
    )
    port = _free_port()
    app = App(
        make_roots(tmp_path, None),
        opencode_bin=opencode,
        base_url=f"http://127.0.0.1:{port}",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            build_web(app, "t", {f"127.0.0.1:{port}"}), port=port, log_level="warning"
        )
    )
    task = asyncio.create_task(server.serve())
    await until(lambda: server.started, timeout=10, what="uvicorn")

    def tools(s) -> list[dict]:
        return [e for e in s.entries() if e["kind"] == "tool"]

    def prose(s) -> list[str]:
        return [e["md"] for e in s.entries() if e["kind"] == "prose"]

    async def turn(s, text: str) -> None:
        await s.send(text)
        await until(lambda: s.status == "working", timeout=30, what="the turn to start")
        await until(lambda: s.status == "idle", timeout=120, what=f"the turn {text!r}")

    try:
        r = await app.registry.call("session.spawn", {"agent": "deep"})
        s = app.sessions.sessions[r["log_id"]]
        await turn(s, "Remember the word PELICAN. Reply with the single word OK.")
        assert s.cost_usd and s.context_window and s.resume_id.startswith("ses_")

        await turn(s, "Call the aegis meta tool, then tell me in one line what it returned.")
        assert any(t["title"] == "meta" and t["status"] == "ok" for t in tools(s))

        await s.send("Run exactly this bash command in the foreground: sleep 40")
        await until(
            lambda: any(t["status"] == "running" for t in tools(s)),
            timeout=120,
            what="the bash call",
        )
        await s.interrupt()
        await until(lambda: s.status == "idle", timeout=20, what="idle after the interrupt")
        assert tools(s)[-1]["status"] == "err"
        assert tools(s)[-1]["detail"]["result"] == "interrupted"

        sid = s.resume_id
        await s.stop()
        await turn(s, "What word did I ask you to remember? Reply with that one word.")
        assert "PELICAN" in prose(s)[-1].upper() and s.resume_id == sid

        r = await app.registry.call("session.spawn", {"agent": "reader"})
        reader = app.sessions.sessions[r["log_id"]]
        await turn(reader, "Use the write tool to create a file named x.txt containing hi.")
        assert not (tmp_path / "x.txt").exists()
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)
        await app.shutdown()
```

If the module does not import `asyncio`, `uvicorn` and `build_web` at the top the way the other App-backed live tests reach them, keep the local imports above.

Run: `make test-live`
Expected: the OpenCode test passes with the Claude ones. If OpenCode's model refuses the meta call or the edit on its own, re-run once before changing anything; note the result in the PR body either way.

- [ ] **Step 2: The bench**

In `scripts/bench.py`, `server_cost` takes the fixture lines and the harness, and `main` measures both:

```python
OPENCODE_FIXTURE = ROOT / "tests" / "fixtures" / "opencode" / "tool.jsonl"


def opencode_lines() -> list[str]:
    return [ln for ln in OPENCODE_FIXTURE.read_text().splitlines() if ln]
```

`server_cost(rounds=40, harness="claude-code")` builds `SpawnSpec("b", "m", "low", "full", Path(tmp), harness=harness)`, passes `opencode_bin="true"`, takes `claude_lines()` or `opencode_lines()`, makes ids unique per round by replacing `"toolu_"` (Claude) or `"call_"` and `"prt_"` (OpenCode) with an `r{r}_` prefix, and returns keys prefixed `server_line_` for Claude (as today) and `server_line_opencode_` for OpenCode. `main` does `metrics.update(asyncio.run(server_cost(harness="opencode")))` after the Claude call.

Run: `make bench`
Expected: the table prints `server_line_opencode_p50_us` and `server_line_opencode_p95_us` next to the Claude rows. Copy the table into the PR body.

- [ ] **Step 3: The docs**

`DESIGN.md`:
- "The process model": "runs each harness as its own child process" stays; the second paragraph's "a `claude` child is running" becomes "a harness child (`claude` or `opencode serve`) is running", and "A prompt to a stopped session starts `claude --resume`" becomes "A prompt to a stopped session starts the harness again on the same conversation (`claude --resume`, or `opencode serve` and the same session id)".
- A new rule under "Rules that span modules":

```markdown
**A harness is a module behind one interface.** `Session` asks a `Process`
(`harness.py`) to start, send, interrupt, set and end, and the fold reads each
stored line through the parser its `src` tag names. Claude Code
(`claude/harness.py`) speaks stream-json on stdio; OpenCode
(`opencode/process.py`) is one `opencode serve` per session over HTTP, because
its MCP token, like Claude's, is per process. Both parsers emit the same
events, so the entry rules exist once. OpenCode's token deltas are the one
thing folded and never stored: the part's closing update carries the whole
text, so the live view is ahead of a fresh fold only while a part is open.
```

- "Agents call the same operations": "the token is minted for each `claude` process and rides in its `--mcp-config` header" becomes "the token is minted for each harness process and rides in the `X-Aegis-Session` header of its MCP config".

Spec: the status line becomes `> **Status:** implemented, 2026-10-08. Plan: \`docs/superpowers/plans/2026-10-08-aegis-2-opencode-harness.md\`.`; in "A harness behind the session", `configure` becomes `set(kind, value)` returning nothing and `command` is folded into `send` (the session computes when a change applies, as it did); in "The catalog", the spawn form's model chip offers the models the agents name, and the `/model` menu in a session offers the catalog's.

`changelog.d/180-opencode-sessions.added.md`:

```markdown
- **OpenCode agents run in aegis.** An agent with `harness: opencode` and a `provider/model` spawns from the new tab and runs `opencode serve` for its session: the transcript streams as it is written, prompts and slash commands, interrupt, resume, `/model`, `/effort` and `/permission`, every aegis tool over MCP, queue work, and a card with cost, context and the title OpenCode gives it. `aegis serve --opencode` names the binary.
```

- [ ] **Step 4: Run every gate once**

Run: `make check`, then `make test-browser`, reading each rc directly (no pipes).
Expected: all pass. `make lint-docs` runs rift, which CI cannot.

- [ ] **Step 5: Commit and open the PR**

```bash
git commit -q -F - -- tests/test_live.py scripts/bench.py DESIGN.md \
  docs/superpowers/specs/2026-10-08-aegis-2-opencode-harness-design.md \
  docs/superpowers/plans/2026-10-08-aegis-2-opencode-harness.md \
  changelog.d/180-opencode-sessions.added.md <<'EOF'
docs: OpenCode sessions in DESIGN.md, the live test and the bench (#180)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git push -q
gh pr create --title "feat: OpenCode sessions in aegis 2 (#180)" --body-file - <<'EOF'
Closes #180.

...(what was measured, the bench table, the live test result, what was tried
and rejected (ACP, opencode run, the v2 routes), and what is left out: the
approval card, forking, sharing, legacy imports)...

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

Then exercise it the way Alex will: `aegis serve` from the worktree on a free port with a scratch state root, spawn the `deepseek` agent from a real browser, and check the stream, an interrupt and a resume after restarting the server (AGENTS.md "What done means", item 2).
