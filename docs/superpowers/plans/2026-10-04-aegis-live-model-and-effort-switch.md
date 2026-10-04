# Live `/model` and `/effort` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/model <name>` and `/effort <level>` switch the active session's model and reasoning effort inside the running harness process, complete from the harness's own list, show on the status bar at once, and survive a daemon restart and a reopen from history.

**Architecture:** `HarnessSession` gains `set_option(kind, value)` and `option_choices(kind)`. `ClaudeSession` implements them with stream-json control requests (`set_model`, `apply_flag_settings`, `get_settings`, `list_models`), and `AcpSession` with `session/set_config_option` over the `configOptions` the agent advertises. `AgentSession` holds the confirmed values as `model_label` / `effort_label` and overlays them onto its `Agent`, the TUI reads the labels, and two existing stores carry them across restarts: `WorkspaceTab` for the boot roster and `SessionMeta` for history. The commands reach all of this through two new `AppBridge` methods.

**Tech Stack:** Python 3.13+, asyncio, `agent-client-protocol` (`acp`) 0.10.0, Textual + Rich, pytest with `asyncio_mode = "auto"`, `uv`.

**Spec:** `docs/superpowers/specs/2026-10-04-aegis-live-model-and-effort-switch-design.md` (issue #97). Read it first: every protocol detail below was measured, and the spec has the tables.

## Global Constraints

- Work in a worktree branched from `origin/main` after PR #123 (this plan and its spec) merges: `git worktree add .claude/worktrees/97-model-effort -b feat/97-live-model-effort origin/main`, then `uv sync`. Never branch from `docs/97-live-model-effort-spec`.
- Stage named paths only and never amend. Conventional commits, English, ending with the `Co-Authored-By` line the session gives you.
- Iterate with `uv run pytest <the task's test files> -q`. Run `make check` once at the end (Task 8). The full suite has 1 or 2 known inotify flakes; re-run a failure alone before treating it as yours.
- `kind` is exactly `"model"` or `"effort"`. ACP options are found by `category` (`"model"`, `"thought_level"`), never by `id`.
- Claude control requests time out after `ClaudeSession.CONTROL_TIMEOUT_S = 15.0` seconds (a bad model name took 4.7 s to be refused).
- An unknown Claude effort level returns `success` and changes nothing. Every effort switch on Claude ends with a `get_settings` read-back.
- `Effort` gains exactly one member, `xhigh`. An effort value that is not an `Effort` member (OpenCode's `default`) is shown and stored as a label, and never written into an `Agent`.
- `WORKSPACE_VERSION` stays `1` and the history `INDEX_VERSION` stays `2`. New fields default sanely; read the comment above `INDEX_VERSION` in `src/aegis/state/history.py` before touching it.
- `/model` completion reads the live session only. `aegis.models.models_for` (the bundled price registry) is not a source.
- Error strings reaching the operator start with the harness name (`claude-code: Model 'x' not found`).

## Review Focus

1. `/model` typed in a fresh tab before its first message: the harness process does not exist yet. The switch must start it, and the first turn afterwards must not start it a second time. Pinned in Task 4.
2. A `control_response` arriving in the middle of a streaming turn must reach its waiter, and every event around it must still reach the turn, in order. Pinned in Task 2.
3. The harness exits while a control request waits. The waiter must fail at once, not after 15 s. Pinned in Task 2.
4. Resuming a tab whose stored effort is `default` (an ACP level) must not crash `Agent` validation. Pinned in Task 6.
5. A `workspace.json` and a history index written before this change must load with no fields missing and no re-fold. Pinned in Task 6.

---

### Task 1: The option contract on `HarnessSession`, and `Effort.xhigh`

**Files:**
- Modify: `src/aegis/drivers/base.py` (new dataclasses, two methods on `HarnessSession`)
- Modify: `src/aegis/config/__init__.py:82-86` (`Effort`)
- Modify: `src/aegis/drivers/claude.py:85-90` (`_EFFORT`)
- Modify: `src/aegis/tui/app.py:2058` (the spawn picker's hardcoded effort list)
- Test: `tests/test_harness_options_contract.py` (create)

**Interfaces:**
- Produces: `aegis.drivers.base.OptionChoice(value: str, name: str = "", description: str = "")`, frozen dataclass.
- Produces: `aegis.drivers.base.OptionResult(ok: bool, value: str = "", error: str = "")`, frozen dataclass.
- Produces: `HarnessSession.set_option(self, kind: str, value: str) -> OptionResult` (async) and `HarnessSession.option_choices(self, kind: str) -> list[OptionChoice]` (sync, no I/O).
- Produces: `Effort.xhigh == "xhigh"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_harness_options_contract.py
"""The default option contract: a harness that has not been taught to switch
refuses in words, and lists nothing."""
from __future__ import annotations

from aegis.config import Agent, Effort
from aegis.drivers.base import HarnessSession, OptionChoice, OptionResult
from aegis.drivers.claude import ClaudeDriver


class _Bare(HarnessSession):
    async def start(self): ...
    async def send(self, text): ...
    async def events(self):
        if False:
            yield None
    async def close(self): ...


async def test_default_set_option_refuses_with_the_kind():
    res = await _Bare().set_option("model", "sonnet")
    assert res == OptionResult(ok=False, error="cannot switch model in a live session")


def test_default_option_choices_is_empty():
    assert _Bare().option_choices("model") == []
    assert _Bare().option_choices("effort") == []


def test_option_choice_defaults():
    assert OptionChoice("sonnet") == OptionChoice("sonnet", "", "")


def test_effort_has_xhigh_and_claude_argv_carries_it():
    assert Effort("xhigh") is Effort.xhigh
    agent = Agent(harness="claude-code", model="sonnet", effort="xhigh", permission="auto")
    argv = ClaudeDriver().build_argv(agent, "/tmp", "http://127.0.0.1:0/mcp/", "h")
    assert argv[argv.index("--effort") + 1] == "xhigh"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_harness_options_contract.py -q`
Expected: FAIL with `ImportError: cannot import name 'OptionChoice'`.

- [ ] **Step 3: Implement**

In `src/aegis/drivers/base.py`, add `from dataclasses import dataclass` to the imports and, above `class HarnessSession`:

```python
@dataclass(frozen=True)
class OptionChoice:
    """One value a harness offers for a session option, e.g. a model."""

    value: str  # what to submit
    name: str = ""  # human label, e.g. "Sonnet 5"
    description: str = ""


@dataclass(frozen=True)
class OptionResult:
    """What a harness said to a model or effort switch."""

    ok: bool
    value: str = ""  # the value the harness reports as applied
    error: str = ""
```

In `HarnessSession`, after `interrupt`:

```python
    async def set_option(self, kind: str, value: str) -> OptionResult:
        """Switch ``kind`` ("model" or "effort") in the live session.

        Takes effect from the next turn. Default refuses: a driver earns
        this only by being measured against its harness.
        """
        return OptionResult(ok=False, error=f"cannot switch {kind} in a live session")

    def option_choices(self, kind: str) -> list[OptionChoice]:
        """The values ``kind`` accepts, as the harness last reported them.

        Synchronous and free of I/O, because the command palette calls it
        on every keystroke. Empty means "nothing to offer", never an error.
        """
        return []
```

In `src/aegis/config/__init__.py`, add `xhigh = "xhigh"` between `high` and `max` in `Effort`. In `src/aegis/drivers/claude.py`, add `Effort.xhigh: "xhigh",` to `_EFFORT` between `high` and `max`. In `src/aegis/tui/app.py:2058`, change `("low", "medium", "high", "max")` to `("low", "medium", "high", "xhigh", "max")`.

- [ ] **Step 4: Run the tests and the driver argv tests**

Run: `uv run pytest tests/test_harness_options_contract.py tests/test_driver_argv.py tests/test_claude_resume_argv.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/drivers/base.py src/aegis/config/__init__.py src/aegis/drivers/claude.py src/aegis/tui/app.py tests/test_harness_options_contract.py
git commit -m "feat(drivers): option contract for live model and effort switches"
```

---

### Task 2: Claude switches through control requests

**Files:**
- Modify: `src/aegis/drivers/claude.py` (`ClaudeSession`: `__init__`, `start`, `_pump_stdout`, new `_resolve_control`, `_fail_controls`, `_control`, `_refresh_models`, `_effort_levels`, `set_option`, `option_choices`; new module function `_flag_value`)
- Test: `tests/test_claude_options.py` (create)

**Interfaces:**
- Consumes: `OptionChoice`, `OptionResult` from Task 1.
- Produces: `ClaudeSession.set_option` and `ClaudeSession.option_choices` per the Task 1 contract. `ClaudeSession.CONTROL_TIMEOUT_S: float = 15.0` (class attribute, tests shrink it).

Protocol facts (from the spec): a request is `{"type":"control_request","request_id":ID,"request":{...}}`. The reply is one stdout line `{"type":"control_response","response":{"subtype":"success"|"error","request_id":ID,"response":{...}|"error":"msg"}}`. `list_models` returns `{"models":[{value, resolvedModel, displayName, description, supportedEffortLevels?, disabled?}, ...]}` and works without `initialize`. `get_settings` returns `{"applied":{"model":..., "effort":...}, ...}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_claude_options.py
"""ClaudeSession switches model and effort with stream-json control requests.

Fixture entries are trimmed from a real `list_models` reply (Claude Code
2.1.283, 2026-10-04): Sonnet 5 takes five effort levels, Haiku 4.5 none, and
Sonnet 5.5 is listed but disabled for this CLI version.
"""
from __future__ import annotations

import asyncio
import json
import types

from aegis.drivers.base import OptionChoice
from aegis.drivers.claude import ClaudeSession
from aegis.events import AssistantText, Result

MODELS = [
    {"value": "sonnet", "resolvedModel": "claude-sonnet-5", "displayName": "Sonnet 5",
     "description": "Efficient for routine tasks",
     "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
    {"value": "haiku", "resolvedModel": "claude-haiku-4-5-20251001",
     "displayName": "Haiku 4.5", "description": "Fastest for quick answers"},
    {"value": "claude-sonnet-5-5", "resolvedModel": "claude-sonnet-5-5",
     "displayName": "Sonnet 5.5", "description": "Update Claude Code to use this model",
     "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"], "disabled": True},
]


class _Harness:
    """Fake stdin that answers each control_request on the fake stdout.

    ``reply(request) -> (subtype, body) | None``; None means never answer.
    """

    def __init__(self, reader: asyncio.StreamReader, reply) -> None:
        self.reader, self.reply, self.requests = reader, reply, []

    def write(self, b: bytes) -> None:
        msg = json.loads(b)
        if msg.get("type") != "control_request":
            return
        self.requests.append(msg["request"])
        out = self.reply(msg["request"])
        if out is None:
            return
        subtype, body = out
        resp = {"subtype": subtype, "request_id": msg["request_id"]}
        resp["response" if subtype == "success" else "error"] = body
        self.reader.feed_data((json.dumps({"type": "control_response", "response": resp}) + "\n").encode())

    async def drain(self) -> None:
        pass


def _default_reply(applied_effort=None):
    state = {"effort": applied_effort}

    def reply(req):
        st = req["subtype"]
        if st == "list_models":
            return "success", {"models": MODELS}
        if st == "set_model":
            if req["model"] == "nope":
                return "error", "Model 'nope' not found"
            return "success", None
        if st == "apply_flag_settings":
            level = req["settings"]["effortLevel"]
            if level != "bogus":  # the real CLI ignores an unknown level silently
                state["effort"] = level
            return "success", None
        if st == "get_settings":
            return "success", {"applied": {"model": "x", "effort": state["effort"]}}
        return "success", None

    return reply


def _session(reply, model="sonnet"):
    reader = asyncio.StreamReader()
    sess = ClaudeSession(["claude", "-p", "--model", model], "/tmp")
    stdin = _Harness(reader, reply)
    sess._proc = types.SimpleNamespace(stdin=stdin, stdout=reader, returncode=None)
    sess._reader = asyncio.create_task(sess._pump_stdout())
    return sess, stdin, reader


async def test_model_switch_sends_set_model_and_refreshes_the_list():
    sess, stdin, _ = _session(_default_reply())
    res = await sess.set_option("model", "haiku")
    assert res.ok and res.value == "haiku"
    assert stdin.requests[0] == {"subtype": "set_model", "model": "haiku"}
    assert [r["subtype"] for r in stdin.requests] == ["set_model", "list_models"]


async def test_model_switch_error_reaches_the_caller():
    sess, _, _ = _session(_default_reply())
    res = await sess.set_option("model", "nope")
    assert not res.ok and res.error == "Model 'nope' not found"


async def test_option_choices_drop_disabled_models():
    sess, _, _ = _session(_default_reply())
    await sess._refresh_models()
    assert sess.option_choices("model") == [
        OptionChoice("sonnet", "Sonnet 5", "Efficient for routine tasks"),
        OptionChoice("haiku", "Haiku 4.5", "Fastest for quick answers"),
    ]
    assert [c.value for c in sess.option_choices("effort")] == ["low", "medium", "high", "xhigh", "max"]


async def test_effort_choices_follow_the_resolved_model_id():
    sess, _, _ = _session(_default_reply(), model="claude-haiku-4-5-20251001")
    await sess._refresh_models()
    assert sess.option_choices("effort") == []


async def test_effort_outside_the_models_levels_is_refused_before_sending():
    sess, stdin, _ = _session(_default_reply())
    await sess._refresh_models()
    stdin.requests.clear()
    res = await sess.set_option("effort", "ultra")
    assert not res.ok and "low, medium, high, xhigh, max" in res.error
    assert stdin.requests == []


async def test_effort_on_a_model_without_levels_is_refused():
    sess, stdin, _ = _session(_default_reply(), model="haiku")
    await sess._refresh_models()
    stdin.requests.clear()
    res = await sess.set_option("effort", "low")
    assert not res.ok and res.error == "haiku does not take an effort level"
    assert stdin.requests == []


async def test_effort_switch_is_confirmed_by_read_back():
    sess, stdin, _ = _session(_default_reply())
    res = await sess.set_option("effort", "xhigh")
    assert res.ok and res.value == "xhigh"
    assert [r["subtype"] for r in stdin.requests] == ["apply_flag_settings", "get_settings"]


async def test_effort_the_cli_silently_ignored_is_reported_as_failure():
    # No list yet, so the local check cannot catch "bogus"; the read-back must.
    sess, _, _ = _session(_default_reply(applied_effort="high"))
    res = await sess.set_option("effort", "bogus")
    assert not res.ok and "high" in res.error


async def test_control_response_mid_turn_never_reaches_the_turns_events():
    sess, _, reader = _session(_default_reply())
    reader.feed_data(b'{"type":"assistant","message":{"model":"claude-sonnet-5","content":[{"type":"text","text":"one"}]}}\n')
    res = await sess.set_option("model", "haiku")  # its reply lands between the two texts
    reader.feed_data(b'{"type":"assistant","message":{"model":"claude-sonnet-5","content":[{"type":"text","text":"two"}]}}\n')
    reader.feed_data(b'{"type":"result","subtype":"success","duration_ms":1,"is_error":false}\n')
    reader.feed_eof()
    events = [ev async for ev in sess.events()]
    assert res.ok
    texts = [e.text for e in events if isinstance(e, AssistantText)]
    assert texts == ["one", "two"]
    assert any(isinstance(e, Result) for e in events)
    assert not any("control_response" in str(getattr(e, "raw", "")) for e in events)


async def test_no_reply_times_out(monkeypatch):
    monkeypatch.setattr(ClaudeSession, "CONTROL_TIMEOUT_S", 0.05)
    sess, _, _ = _session(lambda req: None)
    res = await sess.set_option("model", "haiku")
    assert not res.ok and "timed out" in res.error


async def test_harness_exit_fails_a_waiting_request_at_once():
    sess, _, reader = _session(lambda req: None)  # never answers
    pending = asyncio.create_task(sess.set_option("model", "haiku"))
    await asyncio.sleep(0)
    reader.feed_eof()  # the CLI died
    res = await asyncio.wait_for(pending, timeout=1)  # well under 15 s
    assert not res.ok and "exited" in res.error


async def test_not_running_is_refused():
    sess = ClaudeSession(["claude", "--model", "sonnet"], "/tmp")
    res = await sess.set_option("model", "haiku")
    assert not res.ok and "not running" in res.error
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_claude_options.py -q`
Expected: FAIL. `set_option` returns the Task 1 default refusal, and `_refresh_models` does not exist.

- [ ] **Step 3: Implement**

In `src/aegis/drivers/claude.py`, import the new types next to the existing `from aegis.drivers.base import ...`: add `OptionChoice, OptionResult`. Add this module function near `_oneshot_cwd`:

```python
def _flag_value(argv: list[str], flag: str) -> str | None:
    """The value after ``flag`` in an argv, or None."""
    try:
        return argv[argv.index(flag) + 1]
    except (ValueError, IndexError):
        return None
```

In `ClaudeSession`, add a class attribute under `supports_idle_events = True`:

```python
    # A bad model name took 4.7 s to be refused (measured 2026-10-04).
    CONTROL_TIMEOUT_S: float = 15.0
```

At the end of `__init__`:

```python
        # Control requests awaiting their control_response, by request_id.
        self._control_waiters: dict[str, asyncio.Future] = {}
        # What `list_models` last returned, disabled entries dropped.
        self._models: list[dict] = []
        self._models_task: asyncio.Task | None = None
        # The model the CLI runs: the --model flag, then each confirmed switch.
        self._model: str | None = _flag_value(argv, "--model")
```

At the end of `start()`, after `self._reader = ...`:

```python
        # The model list feeds /model completion. `list_models` needs no
        # `initialize` and answers in ~10 ms once the CLI is up.
        self._models_task = asyncio.create_task(self._refresh_models())
```

In `_pump_stdout`, route control responses before parsing, and fail the waiters when the stream ends. The loop body becomes:

```python
                if line:
                    if self._resolve_control(line):
                        continue
                    ev = parse(line, state=self._parser_state)
                    self._latch_session_id(ev)
                    self._put(ev)
```

and the first statement of its `finally:` block becomes `self._fail_controls("harness exited")`.

Add the methods:

```python
    def _resolve_control(self, line: str) -> bool:
        """Hand a control_response line to the request awaiting it.

        True when the line was a control_response, so it never reaches
        ``parse`` or a turn's events. That includes replies nobody awaits,
        such as the one to ``interrupt``.
        """
        if '"control_response"' not in line:
            return False
        try:
            obj = json.loads(line)
        except ValueError:
            return False
        if not isinstance(obj, dict) or obj.get("type") != "control_response":
            return False
        resp = obj.get("response") or {}
        fut = self._control_waiters.pop(str(resp.get("request_id", "")), None)
        if fut is not None and not fut.done():
            fut.set_result(resp)
        return True

    def _fail_controls(self, reason: str) -> None:
        for fut in self._control_waiters.values():
            if not fut.done():
                fut.set_exception(RuntimeError(reason))
        self._control_waiters.clear()

    async def _control(self, request: dict) -> dict:
        """Send one control_request; return the body of its success reply.

        Raises RuntimeError with the CLI's message on an error reply, when
        the process is not running, or when it exits while we wait.
        """
        proc = self._proc
        if not (proc and proc.stdin and proc.returncode is None):
            raise RuntimeError("harness is not running")
        self._control_seq += 1
        rid = f"aegis_ctl_{self._control_seq}"
        fut = asyncio.get_running_loop().create_future()
        self._control_waiters[rid] = fut
        msg = {"type": "control_request", "request_id": rid, "request": request}
        try:
            proc.stdin.write((json.dumps(msg) + "\n").encode())
            await proc.stdin.drain()
            resp = await asyncio.wait_for(fut, self.CONTROL_TIMEOUT_S)
        except TimeoutError:
            raise RuntimeError(
                f"{request.get('subtype')} timed out after {self.CONTROL_TIMEOUT_S:g}s"
            ) from None
        except (BrokenPipeError, ConnectionResetError):
            raise RuntimeError("harness exited") from None
        finally:
            self._control_waiters.pop(rid, None)
        if resp.get("subtype") == "error":
            raise RuntimeError(str(resp.get("error") or "control request failed"))
        return resp.get("response") or {}

    async def _refresh_models(self) -> None:
        try:
            body = await self._control({"subtype": "list_models"})
        except RuntimeError:
            return  # completion degrades to nothing; switching still works
        self._models = [
            m
            for m in body.get("models") or []
            if isinstance(m, dict) and m.get("value") and not m.get("disabled")
        ]

    def _effort_levels(self) -> list[str] | None:
        """The current model's effort levels; None while unknown."""
        for m in self._models:
            if self._model in (m.get("value"), m.get("resolvedModel")):
                return list(m.get("supportedEffortLevels") or [])
        return None

    async def set_option(self, kind: str, value: str) -> OptionResult:
        try:
            if kind == "model":
                await self._control({"subtype": "set_model", "model": value})
                self._model = value
                await self._refresh_models()
                return OptionResult(ok=True, value=value)
            if kind == "effort":
                levels = self._effort_levels()
                if levels is not None and value not in levels:
                    if not levels:
                        return OptionResult(
                            ok=False, error=f"{self._model} does not take an effort level"
                        )
                    return OptionResult(
                        ok=False, error=f"{self._model} takes {', '.join(levels)}"
                    )
                await self._control(
                    {"subtype": "apply_flag_settings", "settings": {"effortLevel": value}}
                )
                # The CLI answers success to a level it does not know and
                # changes nothing, so only the read-back can say it applied.
                settings = await self._control({"subtype": "get_settings"})
                applied = (settings.get("applied") or {}).get("effort")
                if applied != value:
                    return OptionResult(
                        ok=False, error=f"the harness kept effort {applied!r}"
                    )
                return OptionResult(ok=True, value=value)
        except RuntimeError as e:
            return OptionResult(ok=False, error=str(e))
        return await super().set_option(kind, value)

    def option_choices(self, kind: str) -> list[OptionChoice]:
        if kind == "model":
            return [
                OptionChoice(
                    m["value"], m.get("displayName", ""), m.get("description", "")
                )
                for m in self._models
            ]
        if kind == "effort":
            return [OptionChoice(level) for level in self._effort_levels() or []]
        return []
```

Check that `json` is already imported in `claude.py` (it is used by `interrupt`).

- [ ] **Step 4: Run the new tests and the existing Claude driver tests**

Run: `uv run pytest tests/test_claude_options.py tests/test_claude_interrupt.py tests/test_claude_pump.py tests/test_claude_idle_promotion.py tests/test_interrupt_drains_pending.py -q`
Expected: all PASS. If `test_claude_interrupt.py` breaks because it expected a `control_response` as an event, the test is now wrong about the protocol. Read it before changing anything.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/drivers/claude.py tests/test_claude_options.py
git commit -m "feat(claude): switch model and effort with stream-json control requests"
```

---

### Task 3: ACP switches through `session/set_config_option`

**Files:**
- Modify: `src/aegis/events.py:161-171` (`ContextUpdate` gains `model`, `effort`)
- Modify: `src/aegis/state/event_codec.py` (encode at `ContextUpdate` branch ~line 160, decode ~line 291)
- Modify: `src/aegis/drivers/acp.py` (`_AegisAcpClient`: `config_options` + `set_config_options` + the `ConfigOptionUpdate` branch; `AcpSession.start`, `set_option`, `option_choices`; module helpers `_CATEGORY`, `_find_option`, `_flat_choices`, `_resolve_value`)
- Test: `tests/test_acp_session_options.py` (create), `tests/test_event_codec_context_update.py` (create)

**Interfaces:**
- Consumes: `OptionChoice`, `OptionResult` from Task 1.
- Produces: `ContextUpdate(model: str | None = None, effort: str | None = None)`, emitted by `AcpSession` at start and on each config change. `AcpSession.set_option` and `option_choices` per the Task 1 contract.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_event_codec_context_update.py
from aegis.events import ContextUpdate
from aegis.state.event_codec import decode_event, encode_event


def test_context_update_model_and_effort_round_trip():
    ev = ContextUpdate(model="opencode-go/qwen3.8-flash", effort="medium")
    assert decode_event(encode_event(ev)) == ev


def test_old_context_update_records_still_decode():
    ev = decode_event(encode_event(ContextUpdate(mode="build")))
    assert ev.model is None and ev.effort is None
```

```python
# tests/test_acp_session_options.py
"""AcpSession switches model and effort through the agent's configOptions.

The stub mirrors what `opencode acp` 1.18.31 did on 2026-10-04: a `model`
option and a `thought_level` option whose levels change with the model.
"""
from __future__ import annotations

import sys

from aegis.config import Agent, GeminiCLI
from aegis.drivers.acp import AcpDriver
from aegis.drivers.base import OptionChoice
from aegis.events import ContextUpdate

_STUB_CONFIG = r'''
import asyncio
import acp
from acp.schema import AgentMessageChunk, ConfigOptionUpdate, TextContentBlock

LEVELS = {
    "vendor/fast": ["low", "high", "max", "default"],
    "vendor/slow-flash": ["low", "medium", "xhigh", "default"],
    "other/slow-flash": ["low", "default"],
    "vendor/broken": ["low"],
}
state = {"model": "vendor/fast", "effort": "low"}


def opts():
    return [
        {"id": "model", "name": "Model", "category": "model", "type": "select",
         "currentValue": state["model"],
         "options": [{"value": m, "name": m.upper()} for m in LEVELS]},
        {"id": "effort", "name": "Effort", "category": "thought_level", "type": "select",
         "currentValue": state["effort"],
         "options": [{"value": l, "name": l} for l in LEVELS[state["model"]]]},
    ]


class Stub(acp.Agent):
    def on_connect(self, conn):
        self._conn = conn

    async def initialize(self, protocol_version, client_capabilities=None, client_info=None, **kw):
        return acp.InitializeResponse(protocolVersion=1, agentCapabilities={"loadSession": True},
                                      agentInfo={"name": "stub", "version": "0"})

    async def new_session(self, cwd, mcp_servers=None, additional_directories=None, **kw):
        return acp.NewSessionResponse(sessionId="sess-1", configOptions=opts())

    async def set_config_option(self, config_id, session_id, value, **kw):
        if value == "vendor/broken":
            raise acp.RequestError.invalid_params({"model": value})
        if config_id == "model":
            state["model"] = value
            if state["effort"] not in LEVELS[value]:
                state["effort"] = "low"
        else:
            state["effort"] = value
        await self._conn.session_update(session_id=session_id, update=ConfigOptionUpdate(
            sessionUpdate="config_option_update", configOptions=opts()))
        return acp.SetSessionConfigOptionResponse(configOptions=opts())

    async def prompt(self, session_id, prompt, message_id=None, **kw):
        await self._conn.session_update(session_id=session_id, update=AgentMessageChunk(
            content=TextContentBlock(text="OK", type="text"), sessionUpdate="agent_message_chunk"))
        return acp.PromptResponse(stopReason="end_turn")

    async def cancel(self, session_id, **kw):
        return None


asyncio.run(acp.run_agent(Stub()))
'''

_STUB_NO_CONFIG = _STUB_CONFIG.replace(
    'acp.NewSessionResponse(sessionId="sess-1", configOptions=opts())',
    'acp.NewSessionResponse(sessionId="sess-1")',
)


def _session(script, tmp_path):
    class _D(AcpDriver):
        BASE_CMD = [sys.executable, "-c", script]

        def build_argv(self, *a, **kw):
            return list(self.BASE_CMD)

    return _D().session(Agent(provider=GeminiCLI(model="")), str(tmp_path), mcp_url="", handle="h")


async def test_start_reports_the_running_model_and_effort(tmp_path):
    sess = _session(_STUB_CONFIG, tmp_path)
    await sess.start()
    await sess.send("hi")
    events = [ev async for ev in sess.events()]
    await sess.close()
    ups = [e for e in events if isinstance(e, ContextUpdate) and e.model]
    assert ups[0] == ContextUpdate(model="vendor/fast", effort="low")


async def test_choices_come_from_the_config_options(tmp_path):
    sess = _session(_STUB_CONFIG, tmp_path)
    await sess.start()
    try:
        assert [c.value for c in sess.option_choices("model")] == [
            "vendor/fast", "vendor/slow-flash", "other/slow-flash", "vendor/broken"]
        assert sess.option_choices("model")[0] == OptionChoice("vendor/fast", "VENDOR/FAST", "")
        assert [c.value for c in sess.option_choices("effort")] == ["low", "high", "max", "default"]
    finally:
        await sess.close()


async def test_model_switch_and_effort_levels_follow_it(tmp_path):
    sess = _session(_STUB_CONFIG, tmp_path)
    await sess.start()
    try:
        res = await sess.set_option("model", "vendor/slow-flash")
        assert res.ok and res.value == "vendor/slow-flash"
        assert [c.value for c in sess.option_choices("effort")] == ["low", "medium", "xhigh", "default"]
        refused = await sess.set_option("effort", "high")  # deepseek had it, qwen does not
        assert not refused.ok and "medium" in refused.error
        ok = await sess.set_option("effort", "medium")
        assert ok.ok and ok.value == "medium"
    finally:
        await sess.close()


async def test_unique_suffix_resolves_and_ambiguous_is_refused(tmp_path):
    sess = _session(_STUB_CONFIG, tmp_path)
    await sess.start()
    try:
        assert (await sess.set_option("model", "fast")).value == "vendor/fast"
        res = await sess.set_option("model", "slow-flash")
        assert not res.ok
        assert "vendor/slow-flash" in res.error and "other/slow-flash" in res.error
    finally:
        await sess.close()


async def test_agent_rejection_reaches_the_caller(tmp_path):
    sess = _session(_STUB_CONFIG, tmp_path)
    await sess.start()
    try:
        res = await sess.set_option("model", "vendor/broken")
        assert not res.ok and res.error
    finally:
        await sess.close()


async def test_agent_without_config_options_refuses_and_lists_nothing(tmp_path):
    # The Gemini case: gemini --acp could not be probed (#116), so this stands in.
    sess = _session(_STUB_NO_CONFIG, tmp_path)
    await sess.start()
    try:
        res = await sess.set_option("model", "anything")
        assert not res.ok and res.error == "does not offer a model setting"
        assert sess.option_choices("model") == []
    finally:
        await sess.close()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_event_codec_context_update.py tests/test_acp_session_options.py -q`
Expected: FAIL with `TypeError: ContextUpdate.__init__() got an unexpected keyword argument 'model'`.

- [ ] **Step 3: Implement `ContextUpdate` and its codec**

In `src/aegis/events.py`, add to `ContextUpdate` after `title`:

```python
    # The model and effort an ACP harness reports it runs, at start and on
    # every config_option_update. Claude does not send these.
    model: str | None = None
    effort: str | None = None
```

In `src/aegis/state/event_codec.py`, in the `ContextUpdate` encode branch, before `return out`:

```python
        if ev.model is not None:
            out["model"] = ev.model
        if ev.effort is not None:
            out["effort"] = ev.effort
```

and change the decode line to:

```python
        return ContextUpdate(
            cost=cost,
            mode=d.get("mode"),
            title=d.get("title"),
            model=d.get("model"),
            effort=d.get("effort"),
        )
```

- [ ] **Step 4: Implement the ACP side**

In `src/aegis/drivers/acp.py`, import `OptionChoice, OptionResult` alongside the existing `aegis.drivers.base` import, and add module helpers above `class _AegisAcpClient`:

```python
# ACP names the purpose of a config option by category; the id is the agent's.
_CATEGORY = {"model": "model", "effort": "thought_level"}


def _find_option(options: list, kind: str):
    cat = _CATEGORY.get(kind)
    return next(
        (o for o in options if getattr(o, "category", None) == cat and getattr(o, "type", "") == "select"),
        None,
    )


def _flat_choices(option) -> list:
    """The option's values, with ACP's optional groups flattened."""
    out: list = []
    for item in getattr(option, "options", None) or []:
        if getattr(item, "group", None) is not None:
            out.extend(getattr(item, "options", None) or [])
        else:
            out.append(item)
    return out


def _resolve_value(value: str, values: list[str]) -> tuple[str | None, list[str]]:
    """Exact match, else the one value whose part after the last '/' is
    ``value``. Returns (match, near misses to show when there is none)."""
    if value in values:
        return value, []
    by_suffix = [v for v in values if v.rsplit("/", 1)[-1] == value]
    if len(by_suffix) == 1:
        return by_suffix[0], []
    near = by_suffix or [v for v in values if value.lower() in v.lower()]
    return None, near[:8]
```

In `_AegisAcpClient.__init__`, add:

```python
        # The agent's session config options (model, thought_level, mode), as
        # last reported by new/load_session, set_config_option or an update.
        self.config_options: list = []
```

Add a method to `_AegisAcpClient`:

```python
    def set_config_options(self, options: list) -> None:
        """Store the agent's config options and report model and effort."""
        self.config_options = list(options)
        model = _find_option(self.config_options, "model")
        effort = _find_option(self.config_options, "effort")
        if model is not None or effort is not None:
            self._queue.put_nowait(
                ContextUpdate(
                    model=getattr(model, "current_value", None),
                    effort=getattr(effort, "current_value", None),
                )
            )
```

In `session_update`, add a branch before the trailing comment about dropped update classes:

```python
        elif kind == "ConfigOptionUpdate":
            self.set_config_options(getattr(update, "config_options", None) or [])
```

In `AcpSession.start`, right after `self._session_id = (...)` is assigned:

```python
            self._client.set_config_options(getattr(sess, "config_options", None) or [])
```

Add to `AcpSession`:

```python
    async def set_option(self, kind: str, value: str) -> OptionResult:
        if kind not in _CATEGORY:
            return await super().set_option(kind, value)
        if not self._conn or not self._session_id:
            return OptionResult(ok=False, error="harness is not running")
        option = _find_option(self._client.config_options, kind)
        if option is None:
            return OptionResult(ok=False, error=f"does not offer a {kind} setting")
        values = [c.value for c in _flat_choices(option)]
        match, near = _resolve_value(value, values)
        if match is None:
            listed = near or values[:12]
            label = "did you mean" if near else "choices"
            return OptionResult(
                ok=False, error=f"{value!r} is not a {kind} here; {label}: {', '.join(listed)}"
            )
        try:
            resp = await self._conn.set_config_option(
                config_id=option.id, session_id=self._session_id, value=match
            )
        except Exception as e:  # noqa: BLE001 — a JSON-RPC error must reach the operator
            return OptionResult(ok=False, error=str(e) or type(e).__name__)
        self._client.set_config_options(
            getattr(resp, "config_options", None) or self._client.config_options
        )
        applied = _find_option(self._client.config_options, kind)
        return OptionResult(ok=True, value=getattr(applied, "current_value", match))

    def option_choices(self, kind: str) -> list[OptionChoice]:
        option = _find_option(self._client.config_options, kind)
        if option is None:
            return []
        return [
            OptionChoice(c.value, getattr(c, "name", "") or "", getattr(c, "description", "") or "")
            for c in _flat_choices(option)
        ]
```

Check that `ContextUpdate` is already imported in `acp.py` (the `UsageUpdate` branch uses it).

- [ ] **Step 5: Run the new tests and the existing ACP tests**

Run: `uv run pytest tests/test_event_codec_context_update.py tests/test_acp_session_options.py tests/test_drivers_acp.py tests/test_opencode_driver.py tests/test_identity_acp.py tests/test_lovelaice_driver.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/events.py src/aegis/state/event_codec.py src/aegis/drivers/acp.py tests/test_event_codec_context_update.py tests/test_acp_session_options.py
git commit -m "feat(acp): switch model and effort through session config options"
```

---

### Task 4: `AgentSession` owns the labels and starts the harness once

**Files:**
- Modify: `src/aegis/core/session.py` (`__init__` near line 256; `adopt` near line 364; the turn's start at lines 862-865; `_fire_event` near line 987; new `ensure_started`, `_kick_start`, `_on_start_done`, `set_option`, `option_choices`, `_apply_option`)
- Test: `tests/core/test_session_options.py` (create)

**Interfaces:**
- Consumes: `HarnessSession.set_option` / `option_choices` (Task 1), `ContextUpdate.model` / `.effort` (Task 3).
- Produces: `AgentSession.model_label: str`, `AgentSession.effort_label: str`, `async AgentSession.set_option(kind, value) -> OptionResult` (errors prefixed with the harness name), `AgentSession.option_choices(kind) -> list[OptionChoice]`, `async AgentSession.ensure_started() -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/core/test_session_options.py
from __future__ import annotations

import asyncio
from pathlib import Path

from aegis.config import Agent, Effort
from aegis.core.session import AgentSession
from aegis.drivers.base import OptionChoice, OptionResult
from aegis.events import AssistantText, ContextUpdate, Result


class Harness:
    def __init__(self, events=(), reply=None):
        self.starts = 0
        self.sent: list[str] = []
        self.calls: list[tuple[str, str]] = []
        self._events = list(events)
        self.session_id = "sid"
        self._reply = reply or (lambda k, v: OptionResult(ok=True, value=v))

    async def start(self):
        await asyncio.sleep(0.01)  # long enough for a second caller to race
        self.starts += 1

    async def send(self, text):
        assert self.starts, "send() before start()"
        self.sent.append(text)

    async def events(self):
        for e in self._events:
            yield e

    async def close(self):
        pass

    async def set_option(self, kind, value):
        assert self.starts, "set_option() before start()"
        self.calls.append((kind, value))
        return self._reply(kind, value)

    def option_choices(self, kind):
        return [OptionChoice("sonnet")] if kind == "model" else []


def _agent():
    return Agent(harness="claude-code", model="opus", effort="high", permission="auto")


def _done():
    return [AssistantText(text="ok"), Result(duration_ms=1, is_error=False, usage=None)]


async def test_labels_start_from_the_agent(tmp_path: Path):
    s = AgentSession(Harness(), _agent(), "default", "h1", project_root=tmp_path)
    assert (s.model_label, s.effort_label) == ("opus", "high")


async def test_switch_before_the_first_turn_starts_the_harness_once(tmp_path: Path):
    h = Harness(_done())
    s = AgentSession(h, _agent(), "default", "h1", project_root=tmp_path)
    res = await s.set_option("model", "sonnet")
    assert res.ok and h.starts == 1
    await s.send("go")
    await s._task
    assert h.starts == 1 and h.sent == ["go"]


async def test_a_racing_switch_and_turn_share_one_start(tmp_path: Path):
    h = Harness(_done())
    s = AgentSession(h, _agent(), "default", "h1", project_root=tmp_path)
    await asyncio.gather(s.set_option("model", "sonnet"), s.ensure_started())
    assert h.starts == 1


async def test_confirmed_switch_updates_label_and_agent(tmp_path: Path):
    s = AgentSession(Harness(), _agent(), "default", "h1", project_root=tmp_path)
    await s.set_option("model", "sonnet")
    await s.set_option("effort", "xhigh")
    assert (s.model_label, s.effort_label) == ("sonnet", "xhigh")
    assert s.agent.model == "sonnet" and s.agent.effort is Effort.xhigh


async def test_effort_the_agent_cannot_hold_stays_a_label(tmp_path: Path):
    s = AgentSession(Harness(), _agent(), "default", "h1", project_root=tmp_path)
    await s.set_option("effort", "default")  # an OpenCode level
    assert s.effort_label == "default" and s.agent.effort is Effort.high


async def test_refusal_is_prefixed_with_the_harness_and_changes_nothing(tmp_path: Path):
    h = Harness(reply=lambda k, v: OptionResult(ok=False, error="Model 'x' not found"))
    s = AgentSession(h, _agent(), "default", "h1", project_root=tmp_path)
    res = await s.set_option("model", "x")
    assert res.error == "claude-code: Model 'x' not found"
    assert s.model_label == "opus" and s.agent.model == "opus"


async def test_harness_reported_values_fold_into_the_labels(tmp_path: Path):
    events = [ContextUpdate(model="opencode-go/qwen3.8-flash", effort="medium"), *_done()]
    s = AgentSession(Harness(events), _agent(), "default", "h1", project_root=tmp_path)
    await s.send("go")
    await s._task
    assert (s.model_label, s.effort_label) == ("opencode-go/qwen3.8-flash", "medium")


async def test_option_choices_kicks_a_lazy_start(tmp_path: Path):
    h = Harness()
    s = AgentSession(h, _agent(), "default", "h1", project_root=tmp_path)
    assert s.option_choices("model") == [OptionChoice("sonnet")]
    await asyncio.sleep(0.05)
    assert h.starts == 1


async def test_harness_without_the_methods_is_refused(tmp_path: Path):
    class Old:
        session_id = None
        async def start(self): ...
    s = AgentSession(Old(), _agent(), "default", "h1", project_root=tmp_path)
    res = await s.set_option("model", "sonnet")
    assert not res.ok and res.error.startswith("claude-code: ")
    assert s.option_choices("model") == []
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/core/test_session_options.py -q`
Expected: FAIL with `AttributeError: 'AgentSession' object has no attribute 'model_label'`.

- [ ] **Step 3: Implement**

In `AgentSession.__init__`, next to `self._started = False` (line 256):

```python
        self._start_task: asyncio.Task | None = None
        # The model and effort this session runs, as the harness confirmed
        # them. They start from the agent; a /model, /effort or a harness
        # report changes them. The TUI reads these, never the Agent.
        _eff = getattr(agent, "effort", "")
        self.model_label: str = str(getattr(agent, "model", "") or "")
        self.effort_label: str = str(getattr(_eff, "value", _eff) or "")
```

In `adopt` (line ~364), next to `self._started = False`, add `self._start_task = None`.

Replace the turn's start (lines 862-865):

```python
            if not self._started:
                await self._session.start()
                self._started = True
                self.metrics.begin_session(self._now())
```

with:

```python
            await self.ensure_started()
```

Add the methods:

```python
    def _kick_start(self) -> None:
        """Begin starting the harness unless it is started or starting."""
        if self._started or self._start_task is not None:
            return
        try:
            self._start_task = asyncio.ensure_future(self._session.start())
        except RuntimeError:  # no running loop
            return
        self._start_task.add_done_callback(self._on_start_done)

    def _on_start_done(self, task: asyncio.Task) -> None:
        # A failed start must be retryable by the next caller.
        if task.cancelled() or task.exception() is not None:
            self._start_task = None

    async def ensure_started(self) -> None:
        """Start the harness process once, whoever asks first: the first
        turn, or a /model that arrives before it."""
        if self._started:
            return
        self._kick_start()
        task = self._start_task
        if task is None:
            await self._session.start()
        else:
            await asyncio.shield(task)
        if not self._started:
            self._started = True
            self.metrics.begin_session(self._now())

    async def set_option(self, kind: str, value: str):
        """Switch the harness's model or effort; record it when confirmed."""
        from aegis.drivers.base import OptionResult

        harness = getattr(self.agent, "harness", "") or "harness"
        setter = getattr(self._session, "set_option", None)
        if setter is None:
            return OptionResult(
                ok=False, error=f"{harness}: cannot switch {kind} in a live session"
            )
        try:
            await self.ensure_started()
        except Exception as e:  # noqa: BLE001
            return OptionResult(ok=False, error=f"{harness}: did not start: {e}")
        res = await setter(kind, value)
        if not res.ok:
            return OptionResult(ok=False, value=res.value, error=f"{harness}: {res.error}")
        self._apply_option(kind, res.value or value)
        return res

    def option_choices(self, kind: str) -> list:
        """The harness's current list for ``kind``. Starts a lazy harness in
        the background, so a fresh tab completes once it is up."""
        lister = getattr(self._session, "option_choices", None)
        if lister is None:
            return []
        self._kick_start()
        return lister(kind)

    def _apply_option(self, kind: str, value: str) -> None:
        """Record a confirmed model or effort, and carry it into the Agent
        wherever the Agent can hold it, so a resume rebuilds it."""
        from aegis.config import Agent, Effort
        from aegis.core.manager import _overlay_agent

        if kind == "model" and value != self.model_label:
            self.model_label = value
            if isinstance(self.agent, Agent):
                self.agent = _overlay_agent(self.agent, model=value, effort=None, prompt=None)
        elif kind == "effort" and value != self.effort_label:
            self.effort_label = value
            if isinstance(self.agent, Agent) and value in {e.value for e in Effort}:
                self.agent = _overlay_agent(self.agent, model=None, effort=value, prompt=None)
```

In `_fire_event`, add a branch to the fold before the observers run:

```python
        elif isinstance(ev, ContextUpdate):
            if ev.model:
                self._apply_option("model", ev.model)
            if ev.effort:
                self._apply_option("effort", ev.effort)
```

`ContextUpdate` is already imported in `session.py`.

- [ ] **Step 4: Run the new tests and the session tests that cover starting and adopting**

Run: `uv run pytest tests/core/ tests/test_core_session_close.py tests/test_claude_idle_promotion.py tests/test_resume_flow.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/core/session.py tests/core/test_session_options.py
git commit -m "feat(session): own the live model and effort, start the harness once"
```

---

### Task 5: The status bar and sidebar show the session's labels

**Files:**
- Modify: `src/aegis/tui/widgets.py:423-441` (`StatusBar.__init__`, new `set_identity`, `_identity_tiers`)
- Modify: `src/aegis/tui/pane.py:1193-1197` (compose), `:1577` (next to `refresh_title`), `:2680` (`_on_core_event`), `:3259-3261` (`_sidebar_model`)
- Test: `tests/test_statusbar_segments.py` (extend), `tests/test_pane_identity.py` (create)

**Interfaces:**
- Consumes: `AgentSession.model_label` / `effort_label` (Task 4), `ContextUpdate.model` / `.effort` (Task 3).
- Produces: `StatusBar.set_identity(model: str, effort: str) -> None`; `ConversationPane.refresh_identity() -> None`; `ConversationPane._identity() -> tuple[str, str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_statusbar_segments.py`:

```python
def test_set_identity_replaces_model_and_effort():
    bar = _bar()
    bar.set_identity("claude-sonnet-5", "xhigh")
    text = bar.render_plain()
    assert "claude-sonnet-5" in text and "xhigh" in text
    assert "claude-opus-4-8" not in text
```

```python
# tests/test_pane_identity.py
"""The pane shows what the session runs, not what the profile said."""
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.events import ContextUpdate, Result
from aegis.tui.app import AegisApp
from aegis.tui.widgets import StatusBar


class Quiet:
    session_id = None

    def __init__(self, events=()):
        self._events = list(events)

    async def start(self): ...
    async def send(self, text): ...

    async def events(self):
        for e in self._events:
            yield e

    async def close(self): ...


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"
    def bind(self, bridge): ...
    async def start(self): ...
    async def stop(self): ...


def _agent():
    return Agent(harness="opencode", model="opencode-go/deepseek-v4-flash", effort="high", permission="auto")


@pytest.mark.asyncio
async def test_harness_report_reaches_the_status_bar():
    sess = Quiet([ContextUpdate(model="opencode-go/qwen3.8-flash", effort="medium"),
                  Result(duration_ms=1, is_error=False, usage=None)])
    app = AegisApp({"default": _agent()}, "default", lambda a, u, h: sess, FakeMCP())
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await pane._core.send("go")
        await pane._core._task
        await pilot.pause()
        text = pane.query_one(StatusBar).render_plain()
        assert "qwen3.8-flash" in text and "medium" in text
        assert pane._identity() == ("opencode-go/qwen3.8-flash", "medium")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_statusbar_segments.py tests/test_pane_identity.py -q`
Expected: FAIL with `AttributeError: 'StatusBar' object has no attribute 'set_identity'`.

- [ ] **Step 3: Implement**

In `src/aegis/tui/widgets.py`, `StatusBar.__init__`: keep `from aegis.version import BUILD` out of the constructor. Store the accent with `self._accent = colors.accent`, and replace the `eff = ...` and `self._identity = (...)` lines with `self._identity: tuple[str, ...] = self._identity_tiers(model, effort)`. Add:

```python
    def _identity_tiers(self, model: str, effort: str) -> tuple[str, ...]:
        from aegis.version import BUILD

        eff = f"[{self._accent}]{effort}[/]"
        return (
            f"[dim]aegis {BUILD}[/]  {model}  {eff}",
            f"{short_model(model)} {eff}",
            short_model(model),
        )

    def set_identity(self, model: str, effort: str) -> None:
        """Model and effort the session runs now, after a switch."""
        self._identity = self._identity_tiers(model, effort)
        self._refresh()
```

In `src/aegis/tui/pane.py`, add next to `refresh_title`:

```python
    def _identity(self) -> tuple[str, str]:
        """(model, effort) as the session runs them; the profile otherwise."""
        core = self._core
        if core is not None and hasattr(core, "model_label"):
            return core.model_label, core.effort_label
        model = getattr(self._agent, "model", "") if self._agent else ""
        eff = getattr(self._agent, "effort", "") if self._agent else ""
        return model, getattr(eff, "value", eff)

    def refresh_identity(self) -> None:
        """Push the session's model and effort to the StatusBar and sidebar."""
        bar = self._bar()
        if bar is not None:
            bar.set_identity(*self._identity())
        self._refresh_sidebar()
```

In compose (lines 1193-1197), replace the three `_model` / `_eff_raw` / `_eff` lines with `_model, _eff = self._identity()`. In `_sidebar_model` (lines 3259-3261), replace the same three lines with `_model, _eff = self._identity()`. At the top of `_on_core_event`, after the `AgentPlan` check:

```python
        if isinstance(ev, ContextUpdate) and (ev.model or ev.effort):
            self.refresh_identity()
```

Add `ContextUpdate` to the `from aegis.events import (...)` block at line 27 if it is not there.

- [ ] **Step 4: Run the tests and the status bar tests**

Run: `uv run pytest tests/test_statusbar_segments.py tests/test_statusbar_fit.py tests/test_pane_identity.py tests/test_pane_slash_command.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/widgets.py src/aegis/tui/pane.py tests/test_statusbar_segments.py tests/test_pane_identity.py
git commit -m "feat(tui): status bar shows the model and effort the session runs"
```

---

### Task 6: A switch survives a restart and a reopen from history

**Files:**
- Modify: `src/aegis/state/workspace.py:35-44` (`WorkspaceTab`), `:129-137` (`load`)
- Modify: `src/aegis/events.py:233-250` (`SessionMeta`)
- Modify: `src/aegis/state/event_codec.py:177-189, 318-328` (`SessionMeta`)
- Modify: `src/aegis/state/history.py` (`SessionHistoryRow`, `_Fold`, `_fold_file`, `_fold_to_entry`, `_entry_to_fold`, the row build at ~321)
- Modify: `src/aegis/tui/app.py` (`_pane_to_tab` at 339; new `_resume_overrides`, `_resume_agent`; `bootstrap_resume` at 204; `_resume_agent_tabs` at ~917 and ~931; `_resume_from_history` at ~1857, ~1900 and ~1918; `_append_meta` at 3250)
- Test: `tests/test_switch_persistence.py` (create)

**Interfaces:**
- Consumes: `AgentSession.model_label` / `effort_label` (Task 4).
- Produces: `WorkspaceTab.model: str | None = None`, `WorkspaceTab.effort: str | None = None`; `SessionMeta.model: str = ""`, `SessionMeta.effort: str = ""`; `SessionHistoryRow.model: str = ""`, `SessionHistoryRow.effort: str = ""`; `aegis.tui.app._resume_overrides(tab: WorkspaceTab) -> dict`; `aegis.tui.app._resume_agent(base: Agent, tab: WorkspaceTab) -> Agent`; `AegisApp._record_options(pane) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_switch_persistence.py
from __future__ import annotations

import json
from pathlib import Path

from aegis.config import Agent, Effort
from aegis.events import SessionMeta
from aegis.state import workspace as ws_mod
from aegis.state.event_codec import decode_event, encode_event
from aegis.state.history import list_history
from aegis.state.session_log import append_meta, new_log_id
from aegis.state.workspace import Workspace, WorkspaceTab
from aegis.tui.app import _resume_agent, _resume_overrides


def _tab(**kw):
    base = dict(handle="h1", profile="default", order=0, provider="claude-code",
                session_id="sid", created_at="2026-10-04T00:00:00Z", log_id="h1-x")
    return WorkspaceTab(**{**base, **kw})


def test_workspace_tab_round_trips_model_and_effort(tmp_path: Path):
    ws_mod.save(tmp_path, Workspace(tabs=[_tab(model="claude-sonnet-5", effort="xhigh")]))
    tab = ws_mod.load(tmp_path).tabs[0]
    assert (tab.model, tab.effort) == ("claude-sonnet-5", "xhigh")


def test_workspace_written_before_the_fields_still_loads(tmp_path: Path):
    ws_mod.save(tmp_path, Workspace(tabs=[_tab()]))
    raw = json.loads((tmp_path / "workspace.json").read_text())
    for t in raw["tabs"]:
        t.pop("model"), t.pop("effort")
    (tmp_path / "workspace.json").write_text(json.dumps(raw))
    tab = ws_mod.load(tmp_path).tabs[0]
    assert (tab.model, tab.effort) == (None, None)


def test_resume_overrides_drop_an_effort_the_agent_cannot_hold():
    assert _resume_overrides(_tab(model="opencode-go/qwen3.8-flash", effort="default")) == {
        "model": "opencode-go/qwen3.8-flash"}
    assert _resume_overrides(_tab(effort="xhigh")) == {"effort": "xhigh"}
    assert _resume_overrides(_tab()) == {}


def test_resume_agent_overlays_the_stored_switch():
    base = Agent(harness="claude-code", model="opus", effort="high", permission="auto")
    agent = _resume_agent(base, _tab(model="claude-sonnet-5", effort="default"))
    assert agent.model == "claude-sonnet-5" and agent.effort is Effort.high
    assert _resume_agent(base, _tab()) is base


def _meta(**kw):
    base = dict(handle="h1", profile="default", provider="claude-code", cwd="/tmp",
                created_at="2026-10-04T00:00:00Z", origin="tui")
    return SessionMeta(**{**base, **kw})


def test_session_meta_codec_carries_model_and_effort():
    ev = _meta(model="claude-sonnet-5", effort="xhigh")
    assert decode_event(encode_event(ev)) == ev
    old = encode_event(_meta())
    old.pop("model", None), old.pop("effort", None)
    assert decode_event(old).model == ""


def test_history_row_takes_the_last_non_empty_switch(tmp_path: Path):
    log_id = new_log_id("h1")
    append_meta(tmp_path, log_id, _meta(preview="hi"))
    append_meta(tmp_path, log_id, _meta(model="claude-sonnet-5", effort="xhigh"))
    append_meta(tmp_path, log_id, _meta(handle="h2"))  # a rename re-derives fields
    row = next(r for r in list_history(tmp_path, live_handles=set()) if r.log_id == log_id)
    assert (row.model, row.effort) == ("claude-sonnet-5", "xhigh")
```

The first header's `preview="hi"` is what makes the fold count the log as having content (`_fold_file` sets `has_content` from a meta preview), so the row is listed.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_switch_persistence.py -q`
Expected: FAIL with `ImportError: cannot import name '_resume_agent'`.

- [ ] **Step 3: Implement the two stores**

`WorkspaceTab` (`src/aegis/state/workspace.py`), after `log_id`:

```python
    # The model and effort the session was switched to with /model or
    # /effort; None when it runs what its profile says.
    model: str | None = None
    effort: str | None = None
```

In `load`, add `model=t.get("model"), effort=t.get("effort"),` to the `WorkspaceTab(...)` call. `save` uses `asdict`, so it needs nothing.

`SessionMeta` (`src/aegis/events.py`), after `title_source`:

```python
    # The last /model or /effort switch; "" when never switched. Last
    # non-empty wins, as for the title.
    model: str = ""
    effort: str = ""
```

In `event_codec.py`, add `"model": ev.model, "effort": ev.effort,` to the `SessionMeta` encode dict and `model=d.get("model", ""), effort=d.get("effort", ""),` to the decode call.

In `src/aegis/state/history.py`:
- `SessionHistoryRow`: add `model: str = ""` and `effort: str = ""` after `title_source`.
- `_Fold`: add `model: str = ""` and `effort: str = ""` after `title_source`.
- `_fold_file`: initialise `model = ""` and `effort = ""` next to `title = ""`. In the `SessionMeta` branch, after the title block, add `if ev.model: model = ev.model` and `if ev.effort: effort = ev.effort`. Pass `model=model, effort=effort` to the returned `_Fold`.
- `_fold_to_entry`: add `"model": fold.model, "effort": fold.effort,`.
- `_entry_to_fold`: add `model=entry.get("model", ""), effort=entry.get("effort", ""),`.
- The `SessionHistoryRow(...)` build: add `model=fold.model, effort=fold.effort,`.
- Leave `INDEX_VERSION = 2`.

- [ ] **Step 4: Implement the writers and the five resume sites in `src/aegis/tui/app.py`**

Module level, after `_pane_to_tab`:

```python
def _resume_overrides(tab: WorkspaceTab) -> dict:
    """The model and effort a tab was switched to, as keyword overrides for
    ``_overlay_agent`` and ``SessionManager._sync_spawn``.

    An effort the Agent cannot hold (an ACP level such as ``default``) is
    left out; the harness restores it from its own session store.
    """
    from aegis.config import Effort

    out: dict = {}
    if tab.model:
        out["model"] = tab.model
    if tab.effort in {e.value for e in Effort}:
        out["effort"] = tab.effort
    return out


def _resume_agent(base, tab: WorkspaceTab):
    """``base`` with the tab's stored switch applied; ``base`` when none."""
    from aegis.core.manager import _overlay_agent

    o = _resume_overrides(tab)
    return _overlay_agent(base, model=o.get("model"), effort=o.get("effort"), prompt=None)
```

In `_pane_to_tab`, add:

```python
        model=getattr(pane._core, "model_label", "") or None,
        effort=getattr(pane._core, "effort_label", "") or None,
```

Then change each resume site:
- `bootstrap_resume` (~line 204): `agent = _resume_agent(agents[tab.profile], tab)`.
- `_resume_agent_tabs`, brain branch (~line 917): add `**_resume_overrides(tab),` to the `mgr._sync_spawn(...)` call.
- `_resume_agent_tabs`, local branch (~line 929): `agent = _resume_agent(self._agents[tab.profile], tab)`.
- `_resume_from_history`: build the `WorkspaceTab(...)` at ~line 1857 with `model=row.model or None, effort=row.effort or None,`. Add `**_resume_overrides(tab),` to its `mgr._sync_spawn(...)` (~line 1900), and set `agent = _resume_agent(self._agents[tab.profile], tab)` in the local branch (~line 1916).

In `_append_meta` (line 3250), add to the `SessionMeta(...)` call:

```python
                    model=getattr(pane._core, "model_label", ""),
                    effort=getattr(pane._core, "effort_label", ""),
```

and add the writer next to `_record_title`:

```python
    def _record_options(self, pane) -> None:
        """Append a SessionMeta carrying the session's new model and effort."""
        self._append_meta(pane, handle=pane.handle)
```

`_append_meta` writes the *current* labels on every header. Before this change a rename wrote no model, and the fold's "last non-empty wins" is what keeps that safe. Do not change it to plain last-wins.

- [ ] **Step 5: Run the new tests and every resume and history test**

Run: `uv run pytest tests/test_switch_persistence.py tests/test_state_workspace.py tests/test_resume_flow.py tests/test_resume_on_boot.py tests/test_resume_classification.py tests/test_resume_failure_contained.py tests/test_app_history_integration.py tests/test_workspace_snapshot_includes_session_id.py tests/test_session_titles.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/state/workspace.py src/aegis/events.py src/aegis/state/event_codec.py src/aegis/state/history.py src/aegis/tui/app.py tests/test_switch_persistence.py
git commit -m "feat(state): a model or effort switch survives restart and reopen"
```

---

### Task 7: `/model` and `/effort`, through the bridge, with completion

**Files:**
- Modify: `src/aegis/mcp/bridge.py:209-212` (`AppBridge` protocol: two methods)
- Modify: `src/aegis/core/manager.py` (after `set_title` at 731: two methods)
- Modify: `src/aegis/tui/app.py` (after `set_title` at 3134: two methods)
- Modify: `src/aegis/commands/__init__.py:234-305` (`complete` gains `handle`; new `_call_completer`)
- Modify: `src/aegis/commands/args.py:21-26` (the completer comment)
- Modify: `src/aegis/tui/pane.py:2303, 2339` (pass the handle to `complete`)
- Modify: `src/aegis/commands/builtins/session_ctl.py` (two handlers, one completer factory, two `SlashCommand`s)
- Test: `tests/test_model_effort_commands.py` (create), `tests/test_command_complete.py` (extend)

**Interfaces:**
- Consumes: `AgentSession.set_option` / `option_choices` / `model_label` / `effort_label` (Task 4); `ConversationPane.refresh_identity` (Task 5); `AegisApp._record_options`, `AegisApp._write_snapshot` (Task 6).
- Produces: `AppBridge.set_session_option(handle: str, kind: str, value: str) -> dict` (async), returning `{"ok": True, "handle", "model", "effort"}` or `{"error": str}`. `AppBridge.session_options(handle: str, kind: str) -> dict` (sync), returning `{"value": str, "choices": list[OptionChoice]}` or `{"error": str}`. `complete(text, bridge, handle: str = "")`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_model_effort_commands.py
from __future__ import annotations

from aegis.commands import CommandContext, complete, dispatch
from aegis.drivers.base import OptionChoice

CHOICES = {
    "model": [OptionChoice("sonnet", "Sonnet 5"), OptionChoice("haiku", "Haiku 4.5"),
              OptionChoice("opencode-go/qwen3.8-flash", "Qwen")],
    "effort": [OptionChoice("low"), OptionChoice("xhigh")],
}


class Bridge:
    def __init__(self):
        self.calls = []
        self.state = {"model": "opus", "effort": "high"}

    async def set_session_option(self, handle, kind, value):
        self.calls.append((handle, kind, value))
        if value == "nope":
            return {"error": "claude-code: Model 'nope' not found"}
        self.state[kind] = value
        return {"ok": True, "handle": handle, **self.state}

    def session_options(self, handle, kind):
        self.calls.append((handle, kind))
        return {"value": self.state[kind], "choices": CHOICES[kind]}


async def test_model_switch_goes_to_the_calling_session():
    b = Bridge()
    res = await dispatch("/model sonnet", CommandContext(bridge=b, handle="h1"))
    assert res.ok and res.title == "model → sonnet (from the next turn)"
    assert b.calls == [("h1", "model", "sonnet")]


async def test_refusal_is_an_error_block_with_the_harness_reason():
    res = await dispatch("/model nope", CommandContext(bridge=Bridge(), handle="h1"))
    assert not res.ok and res.title == "cannot switch model"
    assert "Model 'nope' not found" in res.body


async def test_bare_effort_shows_the_current_value_and_choices():
    res = await dispatch("/effort", CommandContext(bridge=Bridge(), handle="h1"))
    assert res.ok and res.title == "effort: high"
    assert "xhigh" in res.body


async def test_frontend_without_the_bridge_methods_says_so():
    res = await dispatch("/model sonnet", CommandContext(bridge=object(), handle="h1"))
    assert not res.ok and "not available" in res.title


def test_model_completion_reads_the_calling_sessions_list():
    b = Bridge()
    res = complete("/model so", b, "h7")
    assert [c.label for c in res.items][0] == "sonnet"
    assert ("h7", "model") in b.calls


def test_completion_fuzzy_matches_inside_provider_ids():
    res = complete("/model qwen", Bridge(), "h1")
    assert [c.label for c in res.items] == ["opencode-go/qwen3.8-flash"]


def test_completion_without_a_session_list_is_empty():
    class Empty(Bridge):
        def session_options(self, handle, kind):
            return {"value": "", "choices": []}
    assert complete("/model ", Empty(), "h1").items == ()
```

Append to `tests/test_command_complete.py`:

```python
def test_one_argument_completers_still_get_only_the_bridge():
    _register_probe()
    try:
        res = complete("/probe2d alpha op", FakeBridge(), "some-handle")
        assert [c.label for c in res.items] == ["opus"]
    finally:
        REGISTRY.pop("probe2d", None)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_model_effort_commands.py tests/test_command_complete.py -q`
Expected: FAIL with `unknown command: /model`, and `TypeError` from `complete` taking a third argument.

- [ ] **Step 3: Implement completion with a handle**

In `src/aegis/commands/__init__.py`, change the signature to `def complete(text: str, bridge: "AppBridge", handle: str = "") -> Completions:`, pass `handle` through in the `@` delegation (`complete("/peer " + text[1:], bridge, handle)`), and in the positional branch replace `arg.completer(bridge)` with `_call_completer(arg.completer, bridge, handle)`. Add above `complete`:

```python
def _call_completer(fn, bridge, handle: str):
    """A completer takes the bridge, or the bridge and the calling pane's
    handle when its list belongs to one session (/model)."""
    import inspect

    try:
        n = len(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        n = 1
    return fn(bridge, handle) if n >= 2 else fn(bridge)
```

In `src/aegis/commands/args.py`, extend the completer comment: "...or a callable of the bridge, or of the bridge and the calling pane's handle (typed ``object``...)" and set `Completer = "tuple[Choice, ...] | Callable[..., list]"`. In `src/aegis/tui/pane.py`, lines 2303 and 2339 become `complete(value, self.app, self.handle)` and `complete(new, self.app, self.handle)`.

- [ ] **Step 4: Implement the commands**

In `src/aegis/commands/builtins/session_ctl.py`, above the `for _cmd in (` list:

```python
def _choices_of(kind: str):
    """Completer: the calling session's live list for ``kind``."""

    def completer(bridge, handle):
        read = getattr(bridge, "session_options", None)
        if read is None:
            return []
        res = read(handle, kind)
        return [
            (c.value, c.name if c.name and c.name != c.value else "")
            for c in res.get("choices") or []
        ]

    return completer


async def _switch(ctx: CommandContext, kind: str, value: str | None) -> CommandResult:
    if not value:
        read = getattr(ctx.bridge, "session_options", None)
        if read is None:
            return CommandResult(False, f"/{kind} is not available in this frontend")
        res = read(ctx.handle, kind)
        if res.get("error"):
            return CommandResult(False, f"cannot read {kind}", res["error"])
        lines = [
            f"  {c.value}" + (f"  {c.name}" if c.name and c.name != c.value else "")
            for c in res.get("choices") or []
        ]
        body = "\n".join(lines) or "the harness lists no choices"
        return CommandResult(True, f"{kind}: {res.get('value') or '?'}", body)
    setter = getattr(ctx.bridge, "set_session_option", None)
    if setter is None:
        return CommandResult(False, f"/{kind} is not available in this frontend")
    res = await setter(ctx.handle, kind, value)
    if res.get("error"):
        return CommandResult(False, f"cannot switch {kind}", res["error"])
    return CommandResult(True, f"{kind} → {res[kind]} (from the next turn)")


async def _model(ctx: CommandContext, args) -> CommandResult:
    return await _switch(ctx, "model", args.get("name"))


async def _effort(ctx: CommandContext, args) -> CommandResult:
    return await _switch(ctx, "effort", args.get("level"))
```

Add to the `for _cmd in (` tuple:

```python
    SlashCommand(
        "model",
        "switch this session's model (bare: show it and the choices)",
        "/model [name]",
        _model,
        spec=ArgSpec(positionals=(Arg("name", required=False, completer=_choices_of("model")),)),
    ),
    SlashCommand(
        "effort",
        "switch this session's reasoning effort (bare: show it and the choices)",
        "/effort [level]",
        _effort,
        spec=ArgSpec(positionals=(Arg("level", required=False, completer=_choices_of("effort")),)),
    ),
```

- [ ] **Step 5: Implement the bridge methods**

In `src/aegis/mcp/bridge.py`, after `set_title` in `AppBridge`:

```python
    async def set_session_option(self, handle: str, kind: str, value: str) -> dict:
        """Switch a session's "model" or "effort" in its live harness."""
        ...

    def session_options(self, handle: str, kind: str) -> dict:
        """{"value": current, "choices": [OptionChoice]} for a session."""
        ...
```

In `src/aegis/core/manager.py`, after `set_title`:

```python
    async def set_session_option(self, handle: str, kind: str, value: str) -> dict:
        session = self.get(handle)
        if session is None:
            return {"error": f"no session {handle!r} (use aegis_list_sessions)"}
        res = await session.set_option(kind, value)
        if not res.ok:
            return {"error": res.error}
        return {
            "ok": True,
            "handle": handle,
            "model": session.model_label,
            "effort": session.effort_label,
        }

    def session_options(self, handle: str, kind: str) -> dict:
        session = self.get(handle)
        if session is None:
            return {"error": f"no session {handle!r} (use aegis_list_sessions)"}
        value = session.model_label if kind == "model" else session.effort_label
        return {"value": value, "choices": session.option_choices(kind)}
```

In `src/aegis/tui/app.py`, after `set_title`, using the same pane lookup `set_title` uses:

```python
    def _conversation_pane(self, handle: str):
        return next(
            (
                p
                for p in self._panes
                if isinstance(p, ConversationPane) and p.handle == handle
            ),
            None,
        )

    async def set_session_option(self, handle: str, kind: str, value: str) -> dict:
        """AppBridge-shaped: switch a pane's model or effort in its harness,
        then show it, write it to the session log and snapshot the roster."""
        pane = self._conversation_pane(handle)
        if pane is None:
            return {"error": f"no session {handle!r} (use aegis_list_sessions)"}
        res = await pane._core.set_option(kind, value)
        if not res.ok:
            return {"error": res.error}
        pane.refresh_identity()
        self._record_options(pane)
        self._write_snapshot()
        return {
            "ok": True,
            "handle": handle,
            "model": pane._core.model_label,
            "effort": pane._core.effort_label,
        }

    def session_options(self, handle: str, kind: str) -> dict:
        pane = self._conversation_pane(handle)
        if pane is None:
            return {"error": f"no session {handle!r} (use aegis_list_sessions)"}
        core = pane._core
        value = core.model_label if kind == "model" else core.effort_label
        return {"value": value, "choices": core.option_choices(kind)}
```

Check for any other `AppBridge` implementer or fake in the tests that a type checker or a `Protocol` conformance test would flag: `grep -rn "def set_title" src tests`. Give each one the two methods only if a test asserts conformance.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_model_effort_commands.py tests/test_command_complete.py tests/test_slash_commands.py tests/test_pane_palette.py tests/test_pane_slash_command.py tests/test_command_registry.py -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/mcp/bridge.py src/aegis/core/manager.py src/aegis/tui/app.py src/aegis/commands/__init__.py src/aegis/commands/args.py src/aegis/tui/pane.py src/aegis/commands/builtins/session_ctl.py tests/test_model_effort_commands.py tests/test_command_complete.py
git commit -m "feat(commands): /model and /effort switch the live session, with completion"
```

---

### Task 8: Docs, release note, and the run a user would do

**Files:**
- Modify: `docs/commands.md` (two rows next to `/title`, line ~237)
- Modify: `docs/superpowers/specs/2026-10-04-aegis-live-model-and-effort-switch-design.md` (status header)
- Create: `changelog.d/97-live-model-and-effort.added.md`

- [ ] **Step 1: Document the commands**

Add after the `/title` row in `docs/commands.md`:

```markdown
| `/model [name]` | Switch the current session's model inside its running harness, from the next turn; the conversation is kept. Completes from the harness's own list. Bare `/model` shows the current model and the choices. Claude Code and OpenCode support it; a harness that cannot switch says so. |
| `/effort [level]` | Switch the current session's reasoning effort, from the next turn. The levels depend on the model (Haiku takes none; OpenCode models differ), and completion offers the current model's. Bare `/effort` shows the current level. |
```

- [ ] **Step 2: Write the release note**

```markdown
<!-- changelog.d/97-live-model-and-effort.added.md -->
`/model` and `/effort` switch a running session's model and reasoning effort without restarting it, on Claude Code (stream-json control requests) and OpenCode (ACP config options). Both complete from the harness's live list, show on the status bar at once, and survive a daemon restart and a reopen from history. `xhigh` is now a valid effort. An OpenCode tab's status bar shows the effort OpenCode actually runs instead of the profile's. (#97)
```

Run: `make changelog-check lint-docs`
Expected: `1 fragment(s), all valid` and `0 errors`. If rift's "every slash command is documented" fails, the row's `/model` spelling does not match its regex.

- [ ] **Step 3: Run the gate**

Run: `make check`
Expected: it exits 0. Read the exit status directly, never through a pipe. On a failure, re-run that test file alone before treating it as yours.

- [ ] **Step 4: Exercise it the way a user reaches it**

The aegis on zion's PATH is a uv tool install, so edits here never reach it, and a daemon keeps the code it booted with. Run this tree's aegis in a throwaway root, so nothing touches the Workspace daemon or `.aegis.yaml`:

```bash
mkdir -p /tmp/aegis-97 && cd /tmp/aegis-97 && git init -q
cat > .aegis.yaml <<'EOF'
agents:
  haiku:
    provider: claude-code
    model: claude-haiku-4-5-20251001
    effort: low
    permission: auto
  oc:
    provider: opencode
    model: opencode-go/deepseek-v4-flash
    permission: auto
default_agent: haiku
EOF
uv run --project /home/apiad/Workspace/repos/aegis/.claude/worktrees/97-model-effort aegis
```

The keys follow `docs/configuration.md` (`provider:`, `default_agent:`). Drive the TUI in an `aegis_term_spawn` terminal, or ask Alex to, and verify each spec "Done" item from the session log rather than from the screen:

1. Haiku tab: type `/model so`. The palette offers `sonnet` and Sonnet ids, and no "Sonnet 5.5". Choose `sonnet`. The status bar changes before you send anything. Send `Reply with one word: hello.` Then `grep '"SystemInit"' .aegis/state/sessions/*.jsonl | tail -1` shows `"model": "claude-sonnet-5"`.
2. `/effort xhigh` answers `effort → xhigh`, and `/effort bogus` answers with Sonnet's five levels.
3. `/spawn oc`, then `/model qwen3.8-flash` in it answers `model → opencode-go/qwen3.8-flash`, and `/effort high` is refused with `low, medium, xhigh, default`.
4. `uv run --project <worktree> aegis kill`, then start it again. The Claude tab's status bar still reads Sonnet, and its next turn's `SystemInit` says `claude-sonnet-5`.
5. Ctrl+R, reopen a closed switched session: same check as 4.

Write what you saw, including any item you could not run, into the PR body. "Tests pass" does not stand in for any of these five.

- [ ] **Step 5: Flip the spec status and commit**

Change the spec's status line to `> **Status:** implemented, <date>. Plan: docs/superpowers/plans/2026-10-04-aegis-live-model-and-effort-switch.md.`

```bash
git add docs/commands.md changelog.d/97-live-model-and-effort.added.md docs/superpowers/specs/2026-10-04-aegis-live-model-and-effort-switch-design.md
git commit -m "docs: /model and /effort, release note, spec status"
```

- [ ] **Step 6: Open the PR**

Push the branch and open a PR that closes #97. In the body, put the live results from Step 4, the two harness questions settled in the spec (mid-turn on both, Gemini unprobed because of #116), and what was left out (an agent-facing MCP tool, `/spawn --model` completion, OpenCode's `mode`). Do not merge: Alex reviews.
