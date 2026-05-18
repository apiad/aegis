# Aegis Phase-1 CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an interactive `aegis` CLI that drives Claude Code via its `stream-json` subprocess protocol, re-renders output cleanly, and selects behavior through a named agent profile loaded from `.aegis.py`.

**Architecture:** Single asyncio event loop. One persistent `claude -p --input-format stream-json --output-format stream-json --replay-user-messages` subprocess per session. A reader task parses stdout lines into typed pydantic events onto a queue; the REPL writes user-message JSON to stdin and drains the queue to a rich renderer until each turn's `result`. An `Agent` profile (harness/model/effort/permission) loaded from a Python config file is translated to claude flags by a per-harness driver — the seam for future harnesses.

**Tech Stack:** Python 3.13, asyncio, pydantic 2, typer, rich, pytest (uv-managed). Spec: `docs/superpowers/specs/2026-05-18-aegis-phase1-cli-design.md`.

---

## File Structure

| Path | Responsibility |
|---|---|
| `legacy/` | Untouched prototype (`server.py`, `demo.py`) + its tests. Sidelined, not deleted. |
| `src/aegis/__init__.py` | Public exports: `Agent`, `Permission`, `Effort`. |
| `src/aegis/events.py` | Pydantic event models + `parse(line) -> Event`. |
| `src/aegis/config.py` | `Agent`/`Permission`/`Effort`, `load_config()`, `write_init_scaffold()`, `INIT_TEMPLATE`. |
| `src/aegis/drivers/__init__.py` | `DRIVERS` registry. |
| `src/aegis/drivers/base.py` | `HarnessDriver` ABC (the multi-harness seam). |
| `src/aegis/drivers/claude.py` | `ClaudeDriver.build_argv()` + `ClaudeSession` (subprocess). |
| `src/aegis/render.py` | `Renderer` — typed event → rich output. |
| `src/aegis/repl.py` | The async REPL loop. |
| `src/aegis/cli.py` | Typer app: `aegis init` + default run command. |
| `tests/` | New hermetic test suite + recorded fixtures. |
| `tests/fixtures/` | Captured real `stream-json` lines. |

---

### Task 0: Repo prep — sideline prototype, repoint entrypoint

**Files:**
- Move: `src/aegis/server.py`, `src/aegis/demo.py` → `legacy/`
- Move: `tests/test_attempt.py`, `tests/test_demo.py`, `tests/test_integration.py`, `tests/test_server.py`, `tests/test_step.py` → `legacy/tests/`
- Modify: `src/aegis/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Move prototype out of the build path**

```bash
mkdir -p legacy/tests
git mv src/aegis/server.py legacy/server.py
git mv src/aegis/demo.py legacy/demo.py
git mv tests/test_attempt.py tests/test_demo.py tests/test_integration.py tests/test_server.py tests/test_step.py legacy/tests/
```

- [ ] **Step 2: Stop pytest from collecting legacy tests**

Modify `pyproject.toml` `[tool.pytest.ini_options]` — keep `testpaths = ["tests"]` (legacy now lives outside `tests/`, so this already excludes it). Add `norecursedirs = ["legacy"]` under the same section as a guard:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
norecursedirs = ["legacy"]
```

- [ ] **Step 3: Repoint the console script**

Modify `pyproject.toml` `[project.scripts]`:

```toml
[project.scripts]
aegis = "aegis.cli:main"
```

- [ ] **Step 4: Reset package exports**

Replace `src/aegis/__init__.py` entirely:

```python
from aegis.config import Agent, Effort, Permission

__all__ = ["Agent", "Effort", "Permission"]
```

(This import will not resolve until Task 2 creates `config.py`. That is expected; do not run anything yet.)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: sideline workflow-engine prototype into legacy/, repoint entrypoint"
```

---

### Task 1: Capture real stream-json fixtures (de-risk spike)

**Files:**
- Create: `tests/fixtures/stream_text.jsonl` (a no-tool turn)
- Create: `tests/fixtures/stream_tool.jsonl` (a turn that uses a tool)
- Create: `scripts/capture_fixtures.sh`

- [ ] **Step 1: Write the capture script**

Create `scripts/capture_fixtures.sh`:

```bash
#!/usr/bin/env bash
# Captures real claude stream-json output for parser fixtures.
# Run from the repo root. Requires `claude` on PATH.
set -euo pipefail
mkdir -p tests/fixtures

echo '{"type":"user","message":{"role":"user","content":"Reply with exactly: hello from aegis"}}' \
  | claude -p --input-format stream-json --output-format stream-json --replay-user-messages \
      --permission-mode plan \
  > tests/fixtures/stream_text.jsonl

echo '{"type":"user","message":{"role":"user","content":"Run the bash command: echo hi"}}' \
  | claude -p --input-format stream-json --output-format stream-json --replay-user-messages \
      --permission-mode bypassPermissions \
  > tests/fixtures/stream_tool.jsonl

echo "Captured:"
wc -l tests/fixtures/stream_text.jsonl tests/fixtures/stream_tool.jsonl
```

- [ ] **Step 2: Run it and inspect the wire format**

Run: `chmod +x scripts/capture_fixtures.sh && ./scripts/capture_fixtures.sh`
Expected: two non-empty `.jsonl` files. Then inspect distinct event shapes:

Run: `cat tests/fixtures/stream_text.jsonl | python3 -c "import sys,json; [print(json.loads(l).get('type'), sorted(json.loads(l).keys())) for l in sys.stdin if l.strip()]"`
Expected: a sequence including a `system` (subtype `init`) line, one or more `assistant` lines, and a final `result` line. Note the exact key names — Task 3's models must match these captured shapes, not assumptions. If the observed shape differs from the models in Task 3, the captured fixture wins; adjust the models.

- [ ] **Step 3: Commit the fixtures**

```bash
git add scripts/capture_fixtures.sh tests/fixtures/stream_text.jsonl tests/fixtures/stream_tool.jsonl
git commit -m "test: capture real claude stream-json fixtures for parser"
```

---

### Task 2: Config — Agent / Permission / Effort + loader + init scaffold

**Files:**
- Create: `src/aegis/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config.py`:

```python
import textwrap
import pytest
from aegis.config import (
    Agent, Permission, Effort, INIT_TEMPLATE,
    load_config, write_init_scaffold, ConfigError,
)


def test_agent_constructs_with_enums():
    a = Agent(harness="claude-code", model="opus",
              effort="high", permission="auto")
    assert a.permission is Permission.auto
    assert a.effort is Effort.high
    assert a.harness == "claude-code"
    assert a.model == "opus"


def test_init_template_parses_to_default_agent(tmp_path):
    f = tmp_path / ".aegis.py"
    f.write_text(INIT_TEMPLATE)
    agents, default = load_config(search_paths=[f])
    assert default == "default"
    assert agents["default"].model == "opus"
    assert agents["default"].permission is Permission.auto


def test_load_config_missing_everywhere_points_to_init(tmp_path):
    with pytest.raises(ConfigError, match="aegis init"):
        load_config(search_paths=[tmp_path / "nope.py",
                                  tmp_path / "also-nope.py"])


def test_load_config_cwd_shadows_home(tmp_path):
    cwd = tmp_path / ".aegis.py"
    home = tmp_path / "home.py"
    cwd.write_text('from aegis import Agent\n'
                    'agents={"default":Agent(harness="claude-code",'
                    'model="sonnet",effort="low",permission="read")}\n'
                    'default_agent="default"\n')
    home.write_text('from aegis import Agent\n'
                     'agents={"default":Agent(harness="claude-code",'
                     'model="opus",effort="max",permission="full")}\n'
                     'default_agent="default"\n')
    agents, _ = load_config(search_paths=[cwd, home])
    assert agents["default"].model == "sonnet"


def test_load_config_default_agent_not_a_key(tmp_path):
    f = tmp_path / ".aegis.py"
    f.write_text('from aegis import Agent\n'
                  'agents={"default":Agent(harness="claude-code",'
                  'model="opus",effort="high",permission="auto")}\n'
                  'default_agent="missing"\n')
    with pytest.raises(ConfigError, match="default_agent"):
        load_config(search_paths=[f])


def test_load_config_bad_permission_names_field(tmp_path):
    f = tmp_path / ".aegis.py"
    f.write_text('from aegis import Agent\n'
                  'agents={"default":Agent(harness="claude-code",'
                  'model="opus",effort="high",permission="banana")}\n'
                  'default_agent="default"\n')
    with pytest.raises(ConfigError, match="permission"):
        load_config(search_paths=[f])


def test_write_init_scaffold_refuses_overwrite(tmp_path):
    f = tmp_path / ".aegis.py"
    f.write_text("# existing\n")
    with pytest.raises(ConfigError, match="exists"):
        write_init_scaffold(f)


def test_write_init_scaffold_writes_template(tmp_path):
    f = tmp_path / ".aegis.py"
    write_init_scaffold(f)
    assert f.read_text() == INIT_TEMPLATE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -q`
Expected: collection/import error — `aegis.config` does not exist.

- [ ] **Step 3: Implement `config.py`**

Create `src/aegis/config.py`:

```python
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ValidationError


class Permission(str, Enum):
    read = "read"
    write = "write"
    full = "full"
    auto = "auto"


class Effort(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    max = "max"


class Agent(BaseModel):
    harness: str
    model: str
    effort: Effort
    permission: Permission


class ConfigError(Exception):
    pass


INIT_TEMPLATE = '''\
# .aegis.py - Aegis configuration (always Python)
from aegis import Agent

agents = {
    "default": Agent(
        harness="claude-code",   # only driver in v1
        model="opus",            # passthrough alias to the harness
        effort="high",           # low | medium | high | max
        permission="auto",       # read | write | full | auto
    ),
}

default_agent = "default"
'''


def default_search_paths() -> list[Path]:
    return [Path.cwd() / ".aegis.py", Path.home() / ".aegis.py"]


def load_config(
    search_paths: Sequence[Path] | None = None,
) -> tuple[dict[str, Agent], str]:
    paths = list(search_paths) if search_paths is not None else default_search_paths()
    target = next((p for p in paths if p.is_file()), None)
    if target is None:
        raise ConfigError(
            "No .aegis.py found in the current directory or home. "
            "Run `aegis init` to create one."
        )

    namespace: dict[str, object] = {}
    try:
        code = compile(target.read_text(), str(target), "exec")
        exec(code, namespace)  # noqa: S102 - config is intentionally Python
    except ValidationError as e:
        raise ConfigError(f"Invalid agent in {target}: {e}") from e
    except Exception as e:  # noqa: BLE001 - surface any config error cleanly
        raise ConfigError(f"Failed to load {target}: {e}") from e

    agents = namespace.get("agents")
    default_agent = namespace.get("default_agent")
    if not isinstance(agents, dict) or not agents:
        raise ConfigError(f"{target} must define a non-empty `agents` dict.")
    for name, agent in agents.items():
        if not isinstance(agent, Agent):
            raise ConfigError(
                f"agents[{name!r}] in {target} is not an Agent instance."
            )
    if not isinstance(default_agent, str) or default_agent not in agents:
        raise ConfigError(
            f"`default_agent` in {target} must be one of {sorted(agents)}."
        )
    return agents, default_agent


def write_init_scaffold(path: Path) -> None:
    if path.exists():
        raise ConfigError(f"{path} already exists; refusing to overwrite.")
    path.write_text(INIT_TEMPLATE)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -q`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/config.py src/aegis/__init__.py tests/test_config.py
git commit -m "feat(config): Agent profile, Python config loader, init scaffold"
```

---

### Task 3: Events — typed stream-json models + parser

**Files:**
- Create: `src/aegis/events.py`
- Test: `tests/test_events.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_events.py`. The fixture-driven test reads the real captured lines from Task 1 and asserts every line parses without raising and that the known shapes are recognized:

```python
import json
from pathlib import Path
import pytest
from aegis.events import (
    parse, SystemInit, AssistantText, AssistantThinking,
    ToolUse, ToolResult, Result, Unknown,
)

FIX = Path(__file__).parent / "fixtures"


def test_unknown_never_raises():
    assert isinstance(parse('not json at all'), Unknown)
    assert isinstance(parse('{"type":"totally_new_event"}'), Unknown)
    assert isinstance(parse(''), Unknown)


def test_parse_system_init():
    ev = parse(json.dumps({"type": "system", "subtype": "init",
                            "session_id": "abc"}))
    assert isinstance(ev, SystemInit)
    assert ev.session_id == "abc"


def test_parse_assistant_text():
    ev = parse(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "hello"}]},
    }))
    assert isinstance(ev, AssistantText)
    assert ev.text == "hello"


def test_parse_assistant_thinking():
    ev = parse(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "thinking",
                                  "thinking": "hmm"}]},
    }))
    assert isinstance(ev, AssistantThinking)


def test_parse_tool_use():
    ev = parse(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Bash",
                                  "input": {"command": "echo hi"}}]},
    }))
    assert isinstance(ev, ToolUse)
    assert ev.name == "Bash"
    assert ev.summary == "echo hi"


def test_parse_tool_result():
    ev = parse(json.dumps({
        "type": "user",
        "message": {"content": [{"type": "tool_result",
                                  "content": "ok output",
                                  "is_error": False}]},
    }))
    assert isinstance(ev, ToolResult)
    assert ev.is_error is False
    assert "ok output" in ev.text


def test_parse_result():
    ev = parse(json.dumps({"type": "result", "subtype": "success",
                            "duration_ms": 1234, "is_error": False}))
    assert isinstance(ev, Result)
    assert ev.duration_ms == 1234


@pytest.mark.parametrize("fixture",
                         ["stream_text.jsonl", "stream_tool.jsonl"])
def test_real_fixture_lines_all_parse(fixture):
    lines = [l for l in (FIX / fixture).read_text().splitlines() if l.strip()]
    assert lines, f"{fixture} is empty - rerun scripts/capture_fixtures.sh"
    events = [parse(l) for l in lines]
    # Every fixture turn must end in a Result and never produce a bare
    # Unknown for the structural events we render.
    assert any(isinstance(e, Result) for e in events)
    assert any(isinstance(e, (AssistantText, ToolUse)) for e in events)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_events.py -q`
Expected: import error — `aegis.events` does not exist.

- [ ] **Step 3: Implement `events.py`**

Create `src/aegis/events.py`. The `parse()` function maps a single raw line to exactly one typed event; multi-block assistant messages yield the first renderable block (text > thinking > tool_use precedence) — the REPL renders one event per line and claude emits one content block per assistant line in practice, but precedence keeps a defensive single-event contract.

```python
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class SystemInit:
    session_id: str | None


@dataclass
class AssistantText:
    text: str


@dataclass
class AssistantThinking:
    text: str


@dataclass
class ToolUse:
    name: str
    summary: str


@dataclass
class ToolResult:
    text: str
    is_error: bool


@dataclass
class Result:
    duration_ms: int | None
    is_error: bool


@dataclass
class Unknown:
    raw: str


Event = (
    SystemInit | AssistantText | AssistantThinking
    | ToolUse | ToolResult | Result | Unknown
)

# Tool name -> input key whose value is the one-line summary.
_TOOL_SUMMARY_KEY = {
    "Bash": "command",
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
}


def _summarize_tool(name: str, tool_input: dict) -> str:
    key = _TOOL_SUMMARY_KEY.get(name)
    if key and isinstance(tool_input.get(key), str):
        return tool_input[key]
    # Fallback: first string value, else empty.
    for v in tool_input.values():
        if isinstance(v, str):
            return v
    return ""


def _first_block(content: list) -> dict | None:
    for kind in ("text", "thinking", "tool_use"):
        for block in content:
            if isinstance(block, dict) and block.get("type") == kind:
                return block
    return content[0] if content and isinstance(content[0], dict) else None


def parse(line: str) -> Event:
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return Unknown(raw=line)
    if not isinstance(obj, dict):
        return Unknown(raw=line)

    etype = obj.get("type")

    if etype == "system" and obj.get("subtype") == "init":
        return SystemInit(session_id=obj.get("session_id"))

    if etype == "result":
        return Result(
            duration_ms=obj.get("duration_ms"),
            is_error=bool(obj.get("is_error", False)),
        )

    if etype == "assistant":
        content = obj.get("message", {}).get("content", [])
        block = _first_block(content) if isinstance(content, list) else None
        if block is None:
            return Unknown(raw=line)
        btype = block.get("type")
        if btype == "text":
            return AssistantText(text=block.get("text", ""))
        if btype == "thinking":
            return AssistantThinking(text=block.get("thinking", ""))
        if btype == "tool_use":
            return ToolUse(
                name=block.get("name", "?"),
                summary=_summarize_tool(
                    block.get("name", ""), block.get("input", {}) or {}
                ),
            )
        return Unknown(raw=line)

    if etype == "user":
        content = obj.get("message", {}).get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    raw = block.get("content", "")
                    text = raw if isinstance(raw, str) else json.dumps(raw)
                    return ToolResult(
                        text=text,
                        is_error=bool(block.get("is_error", False)),
                    )
        return Unknown(raw=line)

    return Unknown(raw=line)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_events.py -q`
Expected: all passed. If `test_real_fixture_lines_all_parse` fails, the captured wire shape differs from the models — adjust the field paths in `parse()` to match the real fixture (the fixture is ground truth), then rerun.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/events.py tests/test_events.py
git commit -m "feat(events): typed stream-json event models and parser"
```

---

### Task 4: Driver — `HarnessDriver` seam + `ClaudeDriver.build_argv`

**Files:**
- Create: `src/aegis/drivers/__init__.py`
- Create: `src/aegis/drivers/base.py`
- Create: `src/aegis/drivers/claude.py`
- Test: `tests/test_driver_argv.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_driver_argv.py`:

```python
from aegis.config import Agent
from aegis.drivers import DRIVERS
from aegis.drivers.claude import ClaudeDriver


def argv_for(permission, effort="high", model="opus"):
    agent = Agent(harness="claude-code", model=model,
                  effort=effort, permission=permission)
    return ClaudeDriver().build_argv(agent, cwd="/tmp/wd")


def test_registry_has_claude():
    assert DRIVERS["claude-code"] is ClaudeDriver


def test_fixed_stream_flags_always_present():
    argv = argv_for("auto")
    assert argv[0] == "claude"
    assert "-p" in argv
    assert "--input-format" in argv
    assert argv[argv.index("--input-format") + 1] == "stream-json"
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--replay-user-messages" in argv


def test_permission_mapping():
    assert "plan" in argv_for("read")
    assert "acceptEdits" in argv_for("write")
    assert "bypassPermissions" in argv_for("full")
    assert "auto" in argv_for("auto")


def test_effort_and_model_passthrough():
    argv = argv_for("auto", effort="max", model="sonnet")
    assert argv[argv.index("--effort") + 1] == "max"
    assert argv[argv.index("--model") + 1] == "sonnet"


def test_unknown_harness_raises():
    import pytest
    from aegis.drivers import get_driver
    with pytest.raises(KeyError):
        get_driver("opencode")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_driver_argv.py -q`
Expected: import error — `aegis.drivers` does not exist.

- [ ] **Step 3: Implement the driver package**

Create `src/aegis/drivers/base.py`:

```python
from __future__ import annotations

import abc
from collections.abc import AsyncIterator

from aegis.config import Agent
from aegis.events import Event


class HarnessSession(abc.ABC):
    """One live conversation with a harness subprocess."""

    @abc.abstractmethod
    async def start(self) -> None: ...

    @abc.abstractmethod
    async def send(self, text: str) -> None: ...

    @abc.abstractmethod
    def events(self) -> AsyncIterator[Event]: ...

    @abc.abstractmethod
    async def close(self) -> None: ...


class HarnessDriver(abc.ABC):
    """Translates a harness-agnostic Agent into a concrete session."""

    @abc.abstractmethod
    def build_argv(self, agent: Agent, cwd: str) -> list[str]: ...

    @abc.abstractmethod
    def session(self, agent: Agent, cwd: str) -> HarnessSession: ...
```

Create `src/aegis/drivers/claude.py`:

```python
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from aegis.config import Agent, Effort, Permission
from aegis.events import Event, Result, parse
from aegis.drivers.base import HarnessDriver, HarnessSession

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


class ClaudeSession(HarnessSession):
    def __init__(self, argv: list[str], cwd: str) -> None:
        self._argv = argv
        self._cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._reader: asyncio.Task | None = None

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self._argv,
            cwd=self._cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader = asyncio.create_task(self._pump_stdout())

    async def _pump_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        async for raw in self._proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if line:
                await self._queue.put(parse(line))
        await self._queue.put(None)  # stream closed sentinel

    async def send(self, text: str) -> None:
        assert self._proc and self._proc.stdin
        msg = {"type": "user",
               "message": {"role": "user", "content": text}}
        self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self._proc.stdin.drain()

    async def events(self) -> AsyncIterator[Event]:
        """Yield events until this turn's Result (or stream close)."""
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev
            if isinstance(ev, Result):
                return

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()


class ClaudeDriver(HarnessDriver):
    def build_argv(self, agent: Agent, cwd: str) -> list[str]:
        return [
            "claude", "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--replay-user-messages",
            "--model", agent.model,
            "--effort", _EFFORT[agent.effort],
            "--permission-mode", _PERMISSION_MODE[agent.permission],
        ]

    def session(self, agent: Agent, cwd: str) -> ClaudeSession:
        return ClaudeSession(self.build_argv(agent, cwd), cwd)
```

Create `src/aegis/drivers/__init__.py`:

```python
from aegis.drivers.base import HarnessDriver, HarnessSession
from aegis.drivers.claude import ClaudeDriver

DRIVERS: dict[str, type[HarnessDriver]] = {"claude-code": ClaudeDriver}


def get_driver(harness: str) -> HarnessDriver:
    return DRIVERS[harness]()


__all__ = ["DRIVERS", "get_driver", "HarnessDriver", "HarnessSession",
           "ClaudeDriver"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_driver_argv.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/drivers tests/test_driver_argv.py
git commit -m "feat(drivers): HarnessDriver seam + ClaudeDriver argv/session"
```

---

### Task 5: Renderer — typed event → rich output

**Files:**
- Create: `src/aegis/render.py`
- Test: `tests/test_render.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_render.py`:

```python
from rich.console import Console
from aegis.events import (
    AssistantText, AssistantThinking, ToolUse, ToolResult,
    Result, SystemInit, Unknown,
)
from aegis.render import Renderer


def render_one(ev) -> str:
    con = Console(record=True, width=80)
    Renderer(con).render(ev)
    return con.export_text()


def test_assistant_text_rendered():
    assert "hello world" in render_one(AssistantText("hello world"))


def test_tool_use_one_liner():
    out = render_one(ToolUse(name="Read", summary="foo.py"))
    assert "Read" in out and "foo.py" in out
    assert out.count("\n") <= 2  # one-liner, not a panel


def test_thinking_is_collapsed():
    out = render_one(AssistantThinking("a very long secret reasoning chain"))
    assert "secret reasoning" not in out
    assert "Thinking" in out


def test_tool_result_ok_collapsed():
    out = render_one(ToolResult(text="line1\nline2\nline3", is_error=False))
    assert "line1" in out
    assert "line3" not in out  # only first line shown


def test_tool_result_error_marked():
    out = render_one(ToolResult(text="boom", is_error=True))
    assert "error" in out.lower()


def test_systeminit_and_unknown_render_nothing():
    assert render_one(SystemInit(session_id="x")).strip() == ""
    assert render_one(Unknown(raw="{}")).strip() == ""


def test_result_shows_separator():
    out = render_one(Result(duration_ms=2500, is_error=False))
    assert "2.5" in out or "2500" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_render.py -q`
Expected: import error — `aegis.render` does not exist.

- [ ] **Step 3: Implement `render.py`**

Create `src/aegis/render.py`:

```python
from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown

from aegis.events import (
    AssistantText, AssistantThinking, ToolUse, ToolResult,
    Result, SystemInit, Unknown, Event,
)


class Renderer:
    def __init__(self, console: Console) -> None:
        self._c = console

    def render(self, ev: Event) -> None:
        if isinstance(ev, AssistantText):
            if ev.text.strip():
                self._c.print(Markdown(ev.text))
        elif isinstance(ev, AssistantThinking):
            self._c.print("[dim]✻ Thinking…[/dim]")
        elif isinstance(ev, ToolUse):
            arg = f"({ev.summary})" if ev.summary else ""
            self._c.print(f"[cyan]⏺[/cyan] {ev.name}{arg}")
        elif isinstance(ev, ToolResult):
            first = ev.text.splitlines()[0] if ev.text.strip() else ""
            mark = "[red]error[/red]" if ev.is_error else "[green]ok[/green]"
            self._c.print(f"  [dim]└[/dim] {mark} {first}")
        elif isinstance(ev, Result):
            secs = (ev.duration_ms or 0) / 1000
            self._c.print(f"[dim]── done in {secs:.1f}s ──[/dim]")
        elif isinstance(ev, (SystemInit, Unknown)):
            pass  # not part of the rendered view
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_render.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/render.py tests/test_render.py
git commit -m "feat(render): minimal-clean rich renderer for stream events"
```

---

### Task 6: REPL — the async loop

**Files:**
- Create: `src/aegis/repl.py`
- Test: `tests/test_repl.py`

- [ ] **Step 1: Write the failing test**

The REPL is wiring; test it against a fake session (no subprocess) to keep it hermetic. Create `tests/test_repl.py`:

```python
import asyncio
from rich.console import Console
from aegis.events import AssistantText, Result
from aegis.repl import run_repl


class FakeSession:
    def __init__(self):
        self.sent = []
        self.started = self.closed = False

    async def start(self):
        self.started = True

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        yield AssistantText(f"echo: {self.sent[-1]}")
        yield Result(duration_ms=10, is_error=False)

    async def close(self):
        self.closed = True


def test_repl_sends_inputs_and_renders_until_exit():
    sess = FakeSession()
    con = Console(record=True, width=80)
    # Two user lines then EOF (None) ends the loop.
    inputs = iter(["hello", "world"])

    def fake_input(_prompt):
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError

    asyncio.run(run_repl(sess, con, input_fn=fake_input))

    assert sess.started and sess.closed
    assert sess.sent == ["hello", "world"]
    out = con.export_text()
    assert "echo: hello" in out and "echo: world" in out


def test_repl_sends_initial_prompt_first():
    sess = FakeSession()
    con = Console(record=True, width=80)

    def fake_input(_p):
        raise EOFError

    asyncio.run(run_repl(sess, con, input_fn=fake_input,
                         initial_prompt="kickoff"))
    assert sess.sent == ["kickoff"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_repl.py -q`
Expected: import error — `aegis.repl` does not exist.

- [ ] **Step 3: Implement `repl.py`**

Create `src/aegis/repl.py`:

```python
from __future__ import annotations

import asyncio
from collections.abc import Callable

from rich.console import Console

from aegis.drivers.base import HarnessSession
from aegis.render import Renderer

_QUIT = {"exit", "quit", "/exit", "/quit"}


async def _drain_turn(session: HarnessSession, renderer: Renderer) -> None:
    async for ev in session.events():
        renderer.render(ev)


async def run_repl(
    session: HarnessSession,
    console: Console,
    input_fn: Callable[[str], str] = input,
    initial_prompt: str | None = None,
) -> None:
    renderer = Renderer(console)
    await session.start()
    try:
        if initial_prompt:
            await session.send(initial_prompt)
            await _drain_turn(session, renderer)
        while True:
            try:
                line = (await asyncio.to_thread(input_fn, "aegis> ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line.lower() in _QUIT:
                break
            await session.send(line)
            try:
                await _drain_turn(session, renderer)
            except KeyboardInterrupt:
                console.print("[dim]^C - turn interrupted[/dim]")
    finally:
        await session.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_repl.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/repl.py tests/test_repl.py
git commit -m "feat(repl): async REPL loop wiring session to renderer"
```

---

### Task 7: CLI — typer app (`aegis init` + default run)

**Files:**
- Create: `src/aegis/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli.py`:

```python
from pathlib import Path
from typer.testing import CliRunner
from aegis.cli import app
from aegis.config import INIT_TEMPLATE

runner = CliRunner()


def test_init_creates_scaffold(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert (tmp_path / ".aegis.py").read_text() == INIT_TEMPLATE


def test_init_refuses_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.py").write_text("# existing\n")
    result = runner.invoke(app, ["init"])
    assert result.exit_code != 0
    assert "exists" in result.output


def test_run_without_config_points_to_init(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.aegis.py either
    result = runner.invoke(app, [])
    assert result.exit_code != 0
    assert "aegis init" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -q`
Expected: import error — `aegis.cli` does not exist.

- [ ] **Step 3: Implement `cli.py`**

Create `src/aegis/cli.py`. A typer callback runs when no subcommand is given (the run path); `init` is an explicit subcommand.

```python
from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console

from aegis.config import ConfigError, load_config, write_init_scaffold
from aegis.drivers import get_driver
from aegis.repl import run_repl

app = typer.Typer(add_completion=False, no_args_is_help=False)
_console = Console()


@app.command()
def init() -> None:
    """Create a .aegis.py config scaffold in the current directory."""
    try:
        write_init_scaffold(Path.cwd() / ".aegis.py")
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    _console.print("[green]Created .aegis.py[/green]")


@app.callback(invoke_without_command=True)
def run(
    ctx: typer.Context,
    prompt: str = typer.Argument(None, help="Optional first turn."),
    agent: str = typer.Option(None, "--agent", "-a"),
    cwd: str = typer.Option(".", "--cwd"),
) -> None:
    """Run the interactive aegis session (default command)."""
    if ctx.invoked_subcommand is not None:
        return
    try:
        agents, default_agent = load_config()
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    name = agent or default_agent
    if name not in agents:
        _console.print(
            f"[red]Unknown agent {name!r}. Known: {sorted(agents)}[/red]"
        )
        raise typer.Exit(1)
    profile = agents[name]
    driver = get_driver(profile.harness)
    session = driver.session(profile, cwd)
    asyncio.run(run_repl(session, _console, initial_prompt=prompt))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -q`
Expected: 3 passed. (If typer's callback + subcommand interaction makes `test_run_without_config_points_to_init` resolve the subcommand instead, ensure `init` is registered before the callback and that invoking with `[]` triggers the callback — confirmed by the test; adjust only if it fails.)

- [ ] **Step 5: Commit**

```bash
git add src/aegis/cli.py tests/test_cli.py
git commit -m "feat(cli): typer app with aegis init and interactive run"
```

---

### Task 8: End-to-end integration smoke + docs

**Files:**
- Create: `tests/test_integration_live.py`
- Modify: `README.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Write the live smoke test (marked slow)**

Create `tests/test_integration_live.py`:

```python
import asyncio
import shutil
import pytest
from aegis.config import Agent
from aegis.drivers.claude import ClaudeDriver
from aegis.events import AssistantText, Result

pytestmark = pytest.mark.skipif(
    shutil.which("claude") is None, reason="claude not on PATH"
)


def test_live_claude_say_hi():
    agent = Agent(harness="claude-code", model="sonnet",
                  effort="low", permission="read")
    sess = ClaudeDriver().session(agent, cwd=".")

    async def go():
        await sess.start()
        await sess.send("Reply with exactly: HELLO_AEGIS")
        seen_text = seen_result = False
        async for ev in sess.events():
            if isinstance(ev, AssistantText):
                seen_text = True
            if isinstance(ev, Result):
                seen_result = True
        await sess.close()
        return seen_text, seen_result

    seen_text, seen_result = asyncio.run(asyncio.wait_for(go(), timeout=120))
    assert seen_text and seen_result
```

- [ ] **Step 2: Run the full fast suite, then the live test**

Run: `uv run pytest -q -k "not live"`
Expected: all fast tests pass.

Run: `uv run pytest tests/test_integration_live.py -q`
Expected: 1 passed (real claude round-trip). If it fails on the wire format, the fix belongs in `events.parse()` against the Task-1 fixtures — re-derive, do not patch around it here.

- [ ] **Step 3: Rewrite README + AGENTS for v1 reality**

Replace `README.md` with usage for the new CLI:

```markdown
# Aegis

Meta-harness for coding agents. Phase 1: an interactive `aegis` CLI that drives
Claude Code via its `stream-json` protocol and re-renders output cleanly.

## Quick start

    uv pip install -e .
    aegis init          # writes .aegis.py
    aegis               # interactive session with the default agent
    aegis "do X"        # send a first turn, then continue interactively
    aegis --agent fast  # pick a named agent profile

## Config (.aegis.py)

Config is always Python. `aegis init` scaffolds an `agents` dict of
`Agent(harness, model, effort, permission)` plus `default_agent`.
Permission levels: `read` (no mutations), `write` (edits, no shell),
`full` (edits + shell), `auto` (harness-native mode).

## Status

Phase 1 of the vision in `docs/superpowers/specs/`. Prototype preserved under
`legacy/`.
```

Replace `AGENTS.md` with:

```markdown
# Agents

## Running

    aegis init && aegis

## Package management

Use `uv` (not pip): `uv pip install -e .`, `uv run pytest`.

## Layout

- `src/aegis/cli.py` - typer entrypoint (`aegis`, `aegis init`)
- `src/aegis/config.py` - Agent profile + .aegis.py loader
- `src/aegis/drivers/` - HarnessDriver seam; ClaudeDriver in claude.py
- `src/aegis/events.py` - stream-json parser
- `src/aegis/render.py` - rich renderer
- `src/aegis/repl.py` - async loop
- `legacy/` - sidelined workflow-engine prototype (not built)

## Tests

`uv run pytest -q -k "not live"` for the fast suite; drop the filter to
include the live claude round-trip.

## Python

Requires Python 3.13+.
```

- [ ] **Step 4: Full suite green**

Run: `uv run pytest -q -k "not live"`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_integration_live.py README.md AGENTS.md
git commit -m "test: live claude smoke; docs: rewrite README/AGENTS for Phase-1 CLI"
```

---

## Self-Review

**Spec coverage:**
- Mechanism (persistent stream-json subprocess) → Task 4 `ClaudeSession`.
- Agent abstraction (harness/model/effort/permission) → Task 2.
- Permission table → Task 4 `_PERMISSION_MODE` + `test_permission_mapping`.
- Effort/model passthrough → Task 4.
- Config loading (cwd→home, no-config error, validation) → Task 2.
- `aegis init` scaffold, refuse overwrite → Task 2 + Task 7.
- Module boundaries (cli/config/drivers/events/render/repl) → Tasks 2–7 match the spec table exactly.
- DRIVERS registry seam → Task 4.
- Minimal-clean rendering rules → Task 5 (one test per rule).
- CLI surface (`PROMPT`, `--agent`, `--cwd`) → Task 7. `--debug` raw-event echo: the spec lists it; it is intentionally deferred — see Deviations.
- Interactive REPL, Ctrl-D/exit quit, Ctrl-C interrupt → Task 6.
- Prototype to `legacy/`, repoint script → Task 0.
- Testing strategy (fixtures, config, argv, render, live smoke) → Tasks 1,2,3,4,5,8.

**Deviations from spec (intentional, low-risk):**
- `--debug` stderr raw-event echo is **dropped from v1 scope** — it touches every module's plumbing for marginal value on day one and is not load-bearing. The spec's "Open items deferred" already treats debug observability as soft. Flagged here for the spec-review gate; remove `--debug` from the spec or accept the deferral.

**Placeholder scan:** none — every code step contains complete code.

**Type consistency:** `Agent`, `Permission`, `Effort`, `Event` union members, `parse()`, `build_argv()`, `session()`, `run_repl(session, console, input_fn, initial_prompt)`, `HarnessSession` method names (`start/send/events/close`) are consistent across Tasks 2–8 and match the FakeSession in Task 6.

---

## Execution notes

- Bite-sized TDD: red → green → commit per task. Eight commits.
- Tasks 2–7 are strictly ordered (each imports the prior). Task 1 must run before Task 3 (fixtures). Task 0 first.
- Tests are written and validated inline by the implementer; the verification layer is not delegated.
