# Slash commands in aegis 2: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `/` line in a session's composer runs an aegis command (`/model`, `/effort`, `/permission`, `/rename`, `/title`, `/stop`, `/close`) or one of Claude Code's own commands, with a completion menu opened by `/` or Alt+/.

**Architecture:** `session.send` resolves a `/` line on the server (`src/aegis/commands.py`). aegis commands call operations; `/model`, `/effort` and `/permission` go through a new `session.configure`, which talks to a live `claude` with control requests (`src/aegis/claude/control.py`) and saves the result in the spawn spec. Claude's commands pass through as text, and the fold learns the line shapes they produce. The command list comes from Claude's `initialize` answer, cached in memory by cwd. The browser menu (`client/js/commands.js`) completes from `commands.list`.

**Tech Stack:** Python 3.13, asyncio, pydantic, Starlette; plain ES modules; pytest, pytest-asyncio, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-07-aegis-2-slash-commands-design.md`. Read it before Task 1; it carries the measured protocol tables this plan relies on.

## Global Constraints

- Work in the worktree `.claude/worktrees/slash-commands`, branch `design/slash-commands`, rebased on `origin/main`. Never commit in the shared checkout.
- Stage named paths only (`git add <paths>`); conventional commits; end each message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never amend.
- Imports inside `src/aegis` are relative; nothing imports `legacy/` (`tests/test_imports.py`).
- Nothing below the CLI calls `Path.cwd()` (`tests/test_no_cwd.py`).
- `make test` is the fast lane: tests over 3 s need `@pytest.mark.slow`. Browser tests carry `pytestmark`/markers as the existing ones in `tests/test_browser.py` do.
- Control request timeout: 15 s (`CONTROL_TIMEOUT_S`).
- Catalog descriptions are cut to their first sentence and 140 characters (`DESC_MAX = 140`).
- The `//` escape sends the line with its first slash replaced by a space: `//rest` is sent as ` /rest`.
- Effort levels: `low`, `medium`, `high`, `xhigh`, `max`.
- Every user-facing string is English. Error codes are snake_case.
- Iterate on the test file each task touches; leave the full suite to CI except where a task says otherwise.

## Review Focus

1. **A prompt that starts with a slash but is not a command** (`/etc/hosts is broken`): refused with `unknown_command` and the `//` hint, never sent. Pinned in Task 6 (`test_an_unknown_command_is_refused_and_nothing_is_stored`).
2. **`/model` or `/effort` while a turn runs**: the change applies, the turn is not interrupted, the transcript says "from the next turn". Pinned in Task 6 (`test_effort_mid_turn_says_from_the_next_turn`).
3. **A server restart, then `/` in a stopped session**: the menu still lists Claude's commands (probe), and no session process is left running. Pinned in Task 6 (`test_a_stopped_session_after_a_restart_gets_its_catalog_from_a_probe`).
4. **A half-written draft and Alt+/**: the draft survives running a command from the menu. Pinned in Task 7 (`test_alt_slash_with_a_draft_runs_a_command_and_keeps_the_draft`).
5. **Esc with the menu open**: closes the menu, does not interrupt a working agent. Pinned in Task 7 (`test_the_menu_completes_a_model_and_esc_closes_it_without_interrupting`).

---

### Task 0: File the issue

- [x] **Step 1: Open the issue**

```bash
cd /home/apiad/Workspace/repos/aegis/.claude/worktrees/slash-commands
gh issue create -R apiad/aegis --title "aegis 2 has no slash commands: no /model, /effort, /compact, no completion" --body-file - <<'EOF'
aegis 2's composer sends every line to `claude` as a prompt. Measured on Claude Code 2.1.283 (probes in the Workspace's `.playground/aegis-slash/`):

- `/effort high`, `/context`, `/cost` typed today run in Claude, but are never echoed, so the fold pairs pending prompts with the wrong echoes and leaves some pending forever (replayed through `transcript/entries.py`).
- `/model sonnet` typed today switches the live process, but `SpawnSpec` keeps the old model, so the chips lie and the next `--resume` switches back.
- `/bogus-thing x` is answered by the model: a paid turn ($0.16) for a typo.
- `/hello world` (a `.claude/commands` file) shows as raw `<command-message>` tags.

Spec: `docs/superpowers/specs/2026-10-07-aegis-2-slash-commands-design.md` on branch `design/slash-commands`. Covers the live half of #97.
EOF
```

Note the issue number; every commit message on this branch ends its subject with `(#<n>)`.

---

### Task 1: Stream events for Claude's local commands

**Files:**
- Modify: `src/aegis/claude/stream.py`
- Create: `tests/fixtures/slash-commands.jsonl`, `scripts/trim_probe.py`
- Test: `tests/test_stream.py`

**Interfaces:**
- Produces: dataclasses `LocalCommand(command: str, args: str, text: str)`, `CommandEcho(name: str, args: str)`, `CommandOutput(text: str)`, `Reset(trigger: str)` in `aegis.claude.stream`, members of `Event`; `Result` gains `turns: int | None`. None is in `TURN_BEARING`.
- Produces: `tests/fixtures/slash-commands.jsonl`, an aegis store (records with `i`, `ts`, `src`, and `kind`/`text` or `line`) of five sends and Claude's answers: `/effort high`, `/hello world`, `/compact`, `/clear`, `what did I say before? one line`.

- [x] **Step 1: Build the fixture from the recorded probe**

`scripts/trim_probe.py` turns a probe recording into a store, keeping only the fields aegis parses, so no local path or skill list lands in the repo:

```python
"""Turn a raw claude stream-json recording into a trimmed aegis store fixture.

    uv run python scripts/trim_probe.py OUT.jsonl SEND [SEND ...] > fixture.jsonl

Each SEND becomes a ``send`` record placed before the Claude lines up to and
including the next ``result``. Hook notices and rate-limit events are dropped,
and each object keeps only the keys aegis reads."""

import json
import sys

KEEP = {
    "type", "subtype", "isReplay", "local_command_run", "trigger", "session_id",
    "model", "claude_code_version", "total_cost_usd", "duration_ms", "is_error",
    "stop_reason", "num_turns", "parent_tool_use_id",
}


def trim(obj: dict) -> dict:
    out = {k: v for k, v in obj.items() if k in KEEP}
    if isinstance(obj.get("message"), dict):
        m = obj["message"]
        out["message"] = {k: m[k] for k in ("role", "content", "model") if k in m}
    if isinstance(obj.get("compact_metadata"), dict):
        c = obj["compact_metadata"]
        out["compact_metadata"] = {k: c.get(k) for k in ("pre_tokens", "post_tokens")}
    return out


def main() -> None:
    path, sends = sys.argv[1], sys.argv[2:]
    lines = [json.loads(x) for x in open(path)]
    lines = [
        x for x in lines
        if x.get("type") != "rate_limit_event"
        and not (x.get("type") == "system" and str(x.get("subtype", "")).startswith("hook_"))
    ]
    i, out = 0, []
    def rec(r):
        nonlocal i
        out.append({"i": i, "ts": 1000.0 + i, **r})
        i += 1
    for text in sends:
        rec({"src": "aegis", "kind": "send", "text": text})
        while lines:
            x = lines.pop(0)
            rec({"src": "claude", "line": json.dumps(trim(x))})
            if x.get("type") == "result":
                break
    for r in out:
        print(json.dumps(r))


if __name__ == "__main__":
    main()
```

Run:

```bash
uv run python scripts/trim_probe.py /home/apiad/Workspace/.playground/aegis-slash/out-a.jsonl \
  "/effort high" "/hello world" "/compact" "/clear" "what did I say before? one line" \
  > tests/fixtures/slash-commands.jsonl
grep -c '"kind": "send"' tests/fixtures/slash-commands.jsonl   # expect 5
grep -c apiad tests/fixtures/slash-commands.jsonl               # expect 0
```

If the second count is not 0, the compaction summary carries a local path; replace the summary's content string with `"Summary: (trimmed)"` in `trim()` for user lines without `isReplay` and rerun.

- [x] **Step 2: Write the failing tests** (append to `tests/test_stream.py`, and add `CommandEcho, CommandOutput, LocalCommand, Reset` to its import list)

```python
def test_a_local_command_is_a_synthetic_assistant_line():
    (ev,) = parse(
        line(
            {
                "type": "assistant",
                "local_command_run": {"command": "effort", "args": "high"},
                "message": {
                    "model": "<synthetic>",
                    "content": [{"type": "text", "text": "Set effort level to high"}],
                },
            }
        )
    )
    assert ev == LocalCommand(command="effort", args="high", text="Set effort level to high")


def test_a_prompt_command_echo_is_its_name_and_args():
    text = "<command-message>hello</command-message>\n<command-name>/hello</command-name>\n<command-args>world</command-args>"
    (ev,) = parse(line({"type": "user", "isReplay": True, "message": {"content": text}}))
    assert ev == CommandEcho(name="hello", args="world")


def test_local_command_output_is_unwrapped_and_never_an_echo():
    obj = {"type": "user", "isReplay": True, "message": {"content": "<local-command-stdout>Compacted </local-command-stdout>"}}
    assert parse(line(obj)) == [CommandOutput(text="Compacted")]


def test_a_conversation_reset():
    assert parse(line({"type": "conversation_reset", "trigger": "clear"})) == [Reset(trigger="clear")]


def test_result_reads_its_turn_count():
    (ev,) = parse(line({"type": "result", "subtype": "success", "num_turns": 0, "total_cost_usd": 0}))
    assert ev.turns == 0


def test_the_recorded_probe_parses_into_the_new_events():
    kinds = []
    for raw in open("tests/fixtures/slash-commands.jsonl"):
        r = json.loads(raw)
        if r["src"] == "claude":
            kinds += [type(e).__name__ for e in parse(r["line"])]
    for want in ("LocalCommand", "CommandEcho", "CommandOutput", "Reset", "Compact", "Echo"):
        assert want in kinds, want
    assert "Garbled" not in kinds
```

- [x] **Step 3: Run them, expect ImportError**

Run: `uv run pytest tests/test_stream.py -q`
Expected: collection error, `cannot import name 'CommandEcho'`.

- [x] **Step 4: Implement**

In `src/aegis/claude/stream.py`, add `import re` and, after `Echo`:

```python
@dataclass(frozen=True)
class LocalCommand:
    """A command Claude Code ran itself (``/effort``, ``/context``): a synthetic
    assistant line carrying ``local_command_run``. Claude never echoes it."""

    command: str
    args: str
    text: str


@dataclass(frozen=True)
class CommandEcho:
    """The echo of a prompt command or skill (``/hello world``)."""

    name: str
    args: str


@dataclass(frozen=True)
class CommandOutput:
    """A local command's output replayed as a user line: ``/compact``'s
    ``Compacted``, or the note a ``set_model`` control request leaves."""

    text: str


@dataclass(frozen=True)
class Reset:
    """``/clear`` started a new conversation; the next ``init`` has a new id."""

    trigger: str
```

Add `turns: int | None = None` as the last field of `Result`. Extend `Event` with the four new classes. Add helpers after `_str`:

```python
_LOCAL_OUT = re.compile(r"^<local-command-(stdout|stderr)>(.*?)</local-command-\1>$", re.S)
_COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)


def _unwrap(text: str) -> str:
    m = _LOCAL_OUT.match(text.strip())
    return m.group(2).strip() if m else text.strip()
```

In `parse`, before `if etype == "system":`:

```python
    if etype == "conversation_reset":
        return [Reset(trigger=str(obj.get("trigger") or ""))]
```

In the `result` branch, pass `turns=obj.get("num_turns") if isinstance(obj.get("num_turns"), int) else None`.

Before `if etype == "assistant" and isinstance(content, list):`:

```python
    run = obj.get("local_command_run")
    if etype == "assistant" and isinstance(run, dict):
        return [
            LocalCommand(
                command=str(run.get("command") or ""),
                args=str(run.get("args") or ""),
                text=_unwrap(_text_of(content)),
            )
        ]
```

Replace the replay block in the `user` branch with:

```python
        if obj.get("isReplay") is True:
            text = _text_of(content).strip()
            if text.startswith("<command-") and (m := _COMMAND_NAME.search(text)):
                a = _COMMAND_ARGS.search(text)
                return [
                    CommandEcho(
                        name=m.group(1).strip().lstrip("/"),
                        args=a.group(1).strip() if a else "",
                    )
                ]
            if _LOCAL_OUT.match(text):
                return [CommandOutput(text=_unwrap(text))]
            if text:
                return [Echo(text=text)]
```

Add the four names to the module docstring's list of what is parsed.

- [x] **Step 5: Run the tests, expect PASS**

Run: `uv run pytest tests/test_stream.py -q`

- [x] **Step 6: Commit**

```bash
git add src/aegis/claude/stream.py tests/test_stream.py tests/fixtures/slash-commands.jsonl scripts/trim_probe.py
git commit -m "feat(stream): Claude's local commands, command echoes, their output and resets as events (#<n>)"
```

---

### Task 2: The fold answers each send by its text

**Files:**
- Modify: `src/aegis/transcript/entries.py`, `src/aegis/transcript/describe.py`
- Test: `tests/test_fold.py`

**Interfaces:**
- Consumes: Task 1's events.
- Produces: entry kind `"command"` (`title` the command line, `md` its output, glyph `describe.COMMAND_GLYPH = "/"`); the store record `{"kind": "configure", "model"?, "effort"?, "permission"?, "when"?: "next_turn" | "on_resume"}` folds to a system entry `model → sonnet · effort → max (from the next turn)`; a `Result` with `turns == 0` and no cost makes no entry.

- [x] **Step 1: Write the failing tests** (append to `tests/test_fold.py`)

```python
from aegis.transcript.store import read_store

FIXTURE = "tests/fixtures/slash-commands.jsonl"


def test_the_recorded_slash_commands_fold_with_nothing_left_pending():
    records, damaged = read_store(Path(FIXTURE))
    assert damaged == 0
    f = fold_records(records)
    es = f.entries()
    assert not [e for e in es if e["status"] == "pending"]
    cmds = [(e["title"], e["md"]) for e in es if e["kind"] == "command"]
    assert cmds[0][0] == "/effort high" and cmds[0][1].startswith("Set effort level to high")
    assert ("/compact", "Compacted") in cmds
    users = [e["md"] for e in es if e["kind"] == "user"]
    assert users == ["/hello world", "what did I say before? one line"]
    assert not [e for e in es if "<command-" in (e["md"] or "") or "<local-command" in (e["md"] or "")]
    assert any(e["summary"].startswith("context cleared") for e in es)


def test_a_command_waiting_for_the_turn_end_does_not_take_a_prompts_echo():
    r = Rec()
    r.own("send", text="/sleep 2")
    r.call("t1", "Bash", {"command": "sleep 2"})
    r.own("send", text="/effort low")
    r.own("send", text="steer")
    r.echo("steer")  # injected at the tool boundary
    r.output("t1", "slept")
    r.result()
    r.claude(
        {
            "type": "assistant",
            "local_command_run": {"command": "effort", "args": "low"},
            "message": {"model": "<synthetic>", "content": [{"type": "text", "text": "Set effort level to low"}]},
        }
    )
    r.claude({"type": "result", "subtype": "success", "num_turns": 0, "total_cost_usd": 0.01})
    f, _ = run(r)
    es = f.entries()
    assert [e["md"] for e in es if e["kind"] == "user"] == ["steer"]
    assert [e["title"] for e in es if e["kind"] == "command"] == ["/effort low"]
    pending = [e["md"] for e in es if e["status"] == "pending"]
    assert pending == ["/sleep 2"], "the fake echoes /sleep; real Claude answers a script's own way"


def test_the_escape_leading_space_matches_the_stripped_echo():
    r = Rec()
    r.own("send", text=" /compact now")
    r.echo("/compact now")
    f, ops = run(r)
    assert ops[1][0] == {"remove": "pending:0"}
    assert [e["md"] for e in f.entries()] == ["/compact now"]


def test_output_with_no_command_waiting_makes_no_entry():
    r = Rec()
    r.claude({"type": "user", "isReplay": True, "message": {"content": "<local-command-stdout>Set model to Sonnet 5</local-command-stdout>"}})
    f, _ = run(r)
    assert f.entries() == []


def test_a_local_only_turn_leaves_no_done_row_but_compact_shows_its_cost():
    r = Rec()
    r.claude({"type": "result", "subtype": "success", "num_turns": 0, "total_cost_usd": 0.0, "duration_ms": 0})
    r.claude({"type": "result", "subtype": "success", "num_turns": 0, "total_cost_usd": 0.04, "duration_ms": 3000})
    f, _ = run(r)
    assert [e["summary"] for e in f.entries()] == ["done in 3.0s · $0.04"]


def test_configure_records_read_as_one_line():
    r = Rec()
    r.own("configure", model="sonnet", effort="max", when="next_turn")
    r.own("configure", permission="read", when="on_resume")
    r.own("configure", effort="low")
    f, _ = run(r)
    assert [e["summary"] for e in f.entries()] == [
        "model → sonnet · effort → max (from the next turn)",
        "permission → read (when it resumes)",
        "effort → low",
    ]
```

Add `from pathlib import Path` to the file's imports.

- [x] **Step 2: Run, expect failures**

Run: `uv run pytest tests/test_fold.py -q`
Expected: the six new tests fail (pending left, raw tags as user entries, no configure entry).

- [x] **Step 3: Implement**

`src/aegis/transcript/describe.py`: add `COMMAND_GLYPH = "/"` after `FILE_GLYPH`.

`src/aegis/transcript/entries.py`:

1. Import `CommandEcho, CommandOutput, LocalCommand, Reset` from `..claude.stream`.
2. Rewrite the module docstring's first rule:

```
- The echo creates the user entry, never the send. An answer takes the pending
  send whose text it answers, else the oldest of its kind: a prompt sent
  mid-turn is read at the next tool boundary, while a slash command waits for
  the turn to end, so the two kinds are answered out of order.
```

3. Add to `Fold`:

```python
    def _take(self, want: str | None, command: bool, strict: bool = False) -> str | None:
        """Remove and return the pending send an answer belongs to: the oldest
        whose text is ``want`` (compared stripped), else the oldest command line
        or prompt as ``command`` says, else, unless ``strict``, the oldest."""
        texts = [(p, (self._entries.get(p) or {}).get("md") or "") for p in self._pending]
        pick = None
        if want is not None:
            pick = next((p for p, t in texts if t.strip() == want.strip()), None)
        if pick is None:
            pick = next((p for p, t in texts if t.startswith("/") == command), None)
        if pick is None and not strict and texts:
            pick = texts[0][0]
        if pick is not None:
            self._pending.remove(pick)
        return pick
```

4. In `_event`, the `Echo` branch: replace `if self._pending: ops += self._remove(self._pending.popleft())` with

```python
            pid = self._take(ev.text, command=False)
            if pid is not None:
                ops += self._remove(pid)
```

5. After the `Echo` branch:

```python
        if isinstance(ev, LocalCommand):
            line = f"/{ev.command} {ev.args}".strip()
            pid = self._take(line, command=True, strict=True)
            ops = self._remove(pid) if pid is not None else []
            return ops + self._upsert(
                _entry(id, "command", "ok", ts, d.COMMAND_GLYPH, title=line, md=ev.text or None)
            )

        if isinstance(ev, CommandEcho):
            line = f"/{ev.name} {ev.args}".strip()
            pid = self._take(line, command=True)
            ops = self._remove(pid) if pid is not None else []
            return ops + self._upsert(_entry(id, "user", "ok", ts, d.USER_GLYPH, md=line))

        if isinstance(ev, CommandOutput):
            pid = self._take(None, command=True, strict=True)
            if pid is None:
                return []  # a control request's note; its configure record already shows
            title = (self._entries[pid].get("md") or "").strip()
            return self._remove(pid) + self._upsert(
                _entry(id, "command", "ok", ts, d.COMMAND_GLYPH, title=title, md=ev.text or None)
            )

        if isinstance(ev, Reset):
            pid = self._take("/clear", command=True, strict=True)
            ops = self._remove(pid) if pid is not None else []
            return ops + self._upsert(
                _entry(id, "system", "ok", ts, d.SYSTEM_GLYPH,
                       summary="context cleared; Claude started a new conversation")
            )
```

6. In the `Result` branch, right after `ops = self._end_calls(...)`:

```python
            if ev.turns == 0 and len(parts) == 1 and not ev.is_error:
                return ops  # a local command that cost nothing: its own entry says it all
```

7. In `_own`, before the final `return []`:

```python
        if kind == "configure":
            parts = [f"{k} → {rec[k]}" for k in ("model", "effort", "permission") if rec.get(k)]
            when = {"next_turn": "from the next turn", "on_resume": "when it resumes"}.get(
                str(rec.get("when") or "")
            )
            line = " · ".join(parts) + (f" ({when})" if when else "")
            return self._upsert(_entry(f"e{i}", "system", "ok", ts, d.SYSTEM_GLYPH, summary=line))
```

- [x] **Step 4: Run the fold and session tests, expect PASS**

Run: `uv run pytest tests/test_fold.py tests/test_session.py -q`
Expected: all pass, the existing echo-order tests included.

- [x] **Step 5: Commit**

```bash
git add src/aegis/transcript/entries.py src/aegis/transcript/describe.py tests/test_fold.py
git commit -m "feat(fold): answers take the send they answer; command entries, resets and configure lines (#<n>)"
```

---

### Task 3: Control requests with answers, and a fake that gives them

**Files:**
- Modify: `src/aegis/claude/process.py`, `tests/fake_claude.py`
- Test: `tests/test_control.py` (create)

**Interfaces:**
- Produces: `ClaudeProcess.request(subtype: str, timeout: float = CONTROL_TIMEOUT_S, **fields) -> dict` (the `response` body of a success), raising `ControlError(message)` on an error answer, `TimeoutError`, or `BrokenPipeError` when claude is not running or exits first. `CONTROL_TIMEOUT_S = 15.0` and `class ControlError(Exception)` in `aegis.claude.process`. Answered responses never reach `on_line`.
- Produces (fake): answers `initialize` with `{"commands": COMMANDS, "models": MODELS}`; `set_model` (error `Model 'x' not found` for an unknown or disabled one); `apply_flag_settings` (applies `effortLevel` only if the current model lists it, always answers success); `get_settings` → `{"applied": {"model", "effort"}}`; `set_permission_mode` → `{"mode"}`; `interrupt` only interrupts. Scripts `/context`, `/model`, `/effort`, `/rename` (run locally), `/compact`, `/clear`, `/hello <name>`.

- [x] **Step 1: Write the failing tests** (`tests/test_control.py`)

```python
import asyncio
from pathlib import Path

import pytest

from aegis.claude.process import ClaudeProcess, ControlError, build_argv


async def start(fake: str, tmp_path: Path, lines: list[str], *extra: str) -> ClaudeProcess:
    argv = build_argv(fake, "opus", "high", "full")
    p = ClaudeProcess(argv + list(extra), tmp_path, tmp_path / "err.log", lines.append, lambda c, t: None)
    await p.start()
    return p


async def test_initialize_answers_with_commands_and_models_and_is_not_a_line(tmp_path, fake_claude):
    lines: list[str] = []
    p = await start(fake_claude, tmp_path, lines)
    try:
        r = await p.request("initialize")
        assert {"compact", "hello", "sleep"} <= {c["name"] for c in r["commands"]}
        assert any(m["value"] == "haiku" for m in r["models"])
        assert not [x for x in lines if "control_response" in x]
    finally:
        await p.terminate()


async def test_an_error_answer_raises_with_its_message(tmp_path, fake_claude):
    p = await start(fake_claude, tmp_path, [])
    try:
        with pytest.raises(ControlError, match="not found"):
            await p.request("set_model", model="gpt-5")
    finally:
        await p.terminate()


async def test_a_request_to_a_dead_process_fails_fast(tmp_path, fake_claude):
    p = await start(fake_claude, tmp_path, [])
    await p.terminate()
    with pytest.raises(BrokenPipeError):
        await asyncio.wait_for(p.request("initialize"), 2)


async def test_effort_applies_only_where_the_model_lists_it(tmp_path, fake_claude):
    p = await start(fake_claude, tmp_path, [])
    try:
        await p.request("apply_flag_settings", settings={"effortLevel": "max"})
        assert (await p.request("get_settings"))["applied"]["effort"] == "max"
        await p.request("set_model", model="haiku")
        await p.request("apply_flag_settings", settings={"effortLevel": "low"})
        assert (await p.request("get_settings"))["applied"]["effort"] == "max"
    finally:
        await p.terminate()
```

- [x] **Step 2: Run, expect ImportError**

Run: `uv run pytest tests/test_control.py -q`
Expected: `cannot import name 'ControlError'`.

- [x] **Step 3: Implement `request` in `src/aegis/claude/process.py`**

Add `import time`, then:

```python
CONTROL_TIMEOUT_S = 15.0


class ControlError(Exception):
    """claude answered a control request with an error."""
```

In `ClaudeProcess.__init__`: `self._waiting: dict[str, asyncio.Future[dict]] = {}`.

```python
    async def request(self, subtype: str, timeout: float = CONTROL_TIMEOUT_S, **fields: object) -> dict:
        """Send a control request and wait for its answer's body. A bad model
        took 4.7 s to be refused (#97), hence the generous timeout."""
        rid = f"aegis_{subtype}_{time.monotonic_ns()}"
        fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._waiting[rid] = fut
        try:
            await self.write(
                {"type": "control_request", "request_id": rid, "request": {"subtype": subtype, **fields}}
            )
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._waiting.pop(rid, None)

    def _answer(self, line: str) -> bool:
        """Hand a control response to the request waiting for it. True when one
        was waiting: that line is protocol, not transcript, and is not stored."""
        if not self._waiting or '"control_response"' not in line[:40]:
            return False
        try:
            obj = json.loads(line)
        except ValueError:
            return False
        resp = obj.get("response") if isinstance(obj, dict) else None
        fut = self._waiting.get(str(resp.get("request_id"))) if isinstance(resp, dict) else None
        if fut is None:
            return False
        if not fut.done():
            if resp.get("subtype") == "error":
                fut.set_exception(ControlError(str(resp.get("error") or "error")))
            else:
                body = resp.get("response")
                fut.set_result(body if isinstance(body, dict) else {})
        return True
```

In `_pump_stdout`, replace `if line.strip(): self._on_line(line)` with `if line.strip() and not self._answer(line): self._on_line(line)`, and right after `code = await proc.wait()`:

```python
        for fut in self._waiting.values():
            if not fut.done():
                fut.set_exception(BrokenPipeError("claude exited"))
```

Add to the module docstring: "A control request that wants its answer goes through ``request``; the answer is routed to it and never stored."

- [x] **Step 4: Extend the fake** (`tests/fake_claude.py`)

Add to the docstring's script list:

```
    /context, /model, /effort, /rename
                   run locally, as Claude does: no echo, a <synthetic>
                   assistant line with local_command_run, a zero-cost result.
    /compact       a compact_boundary, the replayed "Compacted" note, a result.
    /clear         a conversation_reset; the next turn has a new session id.
    /hello NAME    a prompt command: a <command-message> echo, then text.
```

and: "Control requests: ``interrupt``, ``initialize`` (COMMANDS and MODELS below), ``set_model``, ``apply_flag_settings``, ``get_settings`` and ``set_permission_mode`` answer as Claude Code 2.1.283 does."

Code:

```python
SCRIPTS = ("sleep", "deafsleep", "fail", "notice", "big", "exit", "recall", "mcp", "bgtask")
COMMANDS = [{"name": w, "description": f"Fake script {w}.", "argumentHint": ""} for w in SCRIPTS] + [
    {"name": n, "description": f"Claude's own {n}.", "argumentHint": "", "builtin": True}
    for n in ("compact", "clear", "context", "model", "effort", "rename")
] + [{"name": "hello", "description": "Say hello to someone. (project)", "argumentHint": "<name>"}]
LEVELS = ["low", "medium", "high", "xhigh", "max"]
MODELS = [
    {"value": "opus", "resolvedModel": "fake-opus", "displayName": "Opus", "description": "The big one.", "supportedEffortLevels": LEVELS},
    {"value": "sonnet", "resolvedModel": "fake-sonnet", "displayName": "Sonnet", "description": "The middle one.", "supportedEffortLevels": LEVELS},
    {"value": "haiku", "resolvedModel": "fake-haiku", "displayName": "Haiku", "description": "Takes no effort level."},
    {"value": "retired", "resolvedModel": "fake-retired", "displayName": "Retired", "description": "Update to use it.", "disabled": True},
]


def _arg(flag: str) -> str | None:
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else None
```

`state` gains `"model": None` (a `set_model` sets the resolved id) and `"effort": _arg("--effort")`. In `run`, the `init` emit uses `"model": state["model"] or "fake-model"`. `result` becomes:

```python
def result(is_error: bool = False, subtype: str = "success", cost: float = 0.01, turns: int = 1) -> None:
    state["cost"] += cost
    emit({...existing keys..., "num_turns": turns})
```

`SESSION_ID` stays a module global; `/clear` rebinds it, so `run` starts with `global SESSION_ID` as its first statement (Python refuses a `global` after the name is read in the same function). Define `_arg` above `state`, since `state` reads it.

```python
def _model(name: str | None) -> dict | None:
    return next((m for m in MODELS if name in (m["value"], m["resolvedModel"]) and not m.get("disabled")), None)


def control(msg: dict) -> None:
    req = msg.get("request") or {}
    sub, rid = req.get("subtype"), msg.get("request_id")
    body: dict = {}
    error = None
    if sub == "interrupt":
        interrupted.set()
    elif sub == "initialize":
        body = {"commands": COMMANDS, "models": MODELS}
    elif sub == "set_model":
        m = _model(req.get("model"))
        if m is None:
            error = f"Model '{req.get('model')}' not found"
        else:
            state["model"], state["inited"] = m["resolvedModel"], False
            emit({"type": "user", "isReplay": True, "message": {"role": "user", "content": f"<local-command-stdout>Set model to {m['displayName']}</local-command-stdout>"}})
    elif sub == "apply_flag_settings":
        level = (req.get("settings") or {}).get("effortLevel")
        current = _model(state["model"] or _arg("--model")) or MODELS[0]
        if level in current.get("supportedEffortLevels", []):
            state["effort"] = level
    elif sub == "get_settings":
        body = {"applied": {"model": state["model"], "effort": state["effort"]}}
    elif sub == "set_permission_mode":
        body = {"mode": req.get("mode")}
    resp = (
        {"request_id": rid, "subtype": "error", "error": error}
        if error
        else {"request_id": rid, "subtype": "success", "response": body}
    )
    emit({"type": "control_response", "response": resp})
```

`main()` calls `control(msg)` for every `control_request` instead of the inline success-and-interrupt.

At the top of `run`, after the `init` emit and before `echo(text)`:

```python
    word, _, arg = text.partition(" ")
    if word in ("/context", "/model", "/effort", "/rename"):
        emit({"type": "assistant", "local_command_run": {"command": word[1:], "args": arg},
              "message": {"model": "<synthetic>", "content": [{"type": "text", "text": f"ran {text[1:]}"}]}})
        result(cost=0.0, turns=0)
        return
    if word == "/compact":
        emit({"type": "system", "subtype": "compact_boundary", "compact_metadata": {"pre_tokens": 50000, "post_tokens": 4000}})
        emit({"type": "user", "isReplay": True, "message": {"role": "user", "content": "<local-command-stdout>Compacted </local-command-stdout>"}})
        result(turns=0)
        return
    if word == "/clear":
        SESSION_ID, state["inited"] = str(uuid.uuid4()), False
        emit({"type": "conversation_reset", "trigger": "clear"})
        result(cost=0.0, turns=0)
        return
    if word == "/hello":
        echo(f"<command-message>hello</command-message>\n<command-name>/hello</command-name>\n<command-args>{arg}</command-args>")
        assistant({"type": "text", "text": f"HELLO {arg}"})
        result()
        return
```

The later `word, _, arg = text.partition(" ")` line in `run` is now redundant; delete it.

- [x] **Step 5: Run control, session and agents tests, expect PASS**

Run: `uv run pytest tests/test_control.py tests/test_session.py tests/test_agents.py -q`

- [x] **Step 6: Commit**

```bash
git add src/aegis/claude/process.py tests/fake_claude.py tests/test_control.py
git commit -m "feat(claude): control requests that wait for their answer; the fake answers them (#<n>)"
```

---

### Task 4: `claude/control.py`: the catalog and the three setters

**Files:**
- Create: `src/aegis/claude/control.py`
- Test: `tests/test_control.py`

**Interfaces:**
- Consumes: `ClaudeProcess.request`, `ControlError`, `build_argv` (Task 3); `PERMISSION_MODE` from `aegis.profiles`.
- Produces:
  - `Model(value: str, resolved: str, label: str, doc: str, efforts: tuple[str, ...])` with `wire() -> dict` (`value`, `resolved`, `label`, `doc`, `efforts`; the menu matches a card's model against either id).
  - `Catalog(commands: tuple[dict, ...], models: tuple[Model, ...])`, each command `{"name", "hint", "doc", "source"}`; methods `has(name) -> bool`, `model(name) -> Model | None` (by `value` or `resolved`), `wire_commands(shadowed: Iterable[str]) -> list[dict]`, `wire_models() -> list[dict]`.
  - `from_initialize(body: dict) -> Catalog` (drops disabled models).
  - `async catalog(proc) -> Catalog`; `async probe(claude_bin, model, effort, permission, cwd, stderr_path) -> Catalog` (starts `claude`, asks, ends it).
  - `async set_model(proc, value)`, `async set_effort(proc, level)` (raises `ControlError` when `get_settings` does not read back `level`), `async set_permission(proc, permission)` (aegis vocabulary).

- [x] **Step 1: Write the failing tests** (append to `tests/test_control.py`)

```python
from aegis.claude import control


def test_the_catalog_reads_sources_cuts_docs_and_drops_disabled_models():
    cat = control.from_initialize(
        {
            "commands": [
                {"name": "compact", "description": "Clear history but keep a summary. Extra.", "argumentHint": "", "builtin": True},
                {"name": "draft", "description": "Generate a draft. (project)", "argumentHint": "<outline>"},
                {"name": "ingest", "description": "x" * 300},
                {"name": "sync", "description": "Sync it. (claude.ai sync)"},
            ],
            "models": [
                {"value": "sonnet", "resolvedModel": "claude-sonnet-5", "displayName": "Sonnet 5", "description": "d", "supportedEffortLevels": ["low"]},
                {"value": "old", "resolvedModel": "x", "displayName": "Old", "description": "d", "disabled": True},
            ],
        }
    )
    by = {c["name"]: c for c in cat.wire_commands(shadowed=())}
    assert by["compact"] == {"name": "compact", "hint": "", "doc": "Clear history but keep a summary.", "source": "claude"}
    assert (by["draft"]["source"], by["draft"]["doc"], by["draft"]["hint"]) == ("project", "Generate a draft.", "<outline>")
    assert by["ingest"]["source"] == "skill" and len(by["ingest"]["doc"]) == 140
    assert by["sync"]["source"] == "claude.ai sync"
    assert cat.model("claude-sonnet-5").value == "sonnet" and cat.model("old") is None
    assert [m["value"] for m in cat.wire_models()] == ["sonnet"]
    assert "compact" not in {c["name"] for c in cat.wire_commands(shadowed=("compact",))}


async def test_the_setters_switch_a_live_fake_and_effort_is_read_back(tmp_path, fake_claude):
    p = await start(fake_claude, tmp_path, [])
    try:
        cat = await control.catalog(p)
        assert cat.has("hello") and not cat.has("bogus")
        await control.set_model(p, "sonnet")
        await control.set_effort(p, "xhigh")
        await control.set_permission(p, "read")
        await control.set_model(p, "haiku")
        with pytest.raises(ControlError, match="did not apply effort low"):
            await control.set_effort(p, "low")
    finally:
        await p.terminate()


async def test_a_probe_answers_and_leaves_no_process(tmp_path, fake_claude):
    cat = await control.probe(fake_claude, "opus", "high", "full", tmp_path, tmp_path / "probe.log")
    assert cat.has("compact")
```

- [x] **Step 2: Run, expect ImportError**

Run: `uv run pytest tests/test_control.py -q`

- [x] **Step 3: Implement `src/aegis/claude/control.py`**

```python
"""What aegis asks of a live ``claude`` through control requests, and the
catalog its ``initialize`` answer carries.

Measured on Claude Code 2.1.283 (spec 2026-10-07): ``set_model`` and
``set_permission_mode`` answer success or an error. ``apply_flag_settings``
answers success even for a level it ignores, so ``set_effort`` reads the effort
back with ``get_settings`` and fails unless it applied. ``initialize`` makes no
API call and lists every command the cwd has: Claude's own, skills, plugins and
``.claude/commands``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..profiles import PERMISSION_MODE
from .process import ClaudeProcess, ControlError, build_argv

DESC_MAX = 140
_SOURCE = re.compile(r"\s*\(([^()]+)\)\s*$")


@dataclass(frozen=True)
class Model:
    value: str
    resolved: str
    label: str
    doc: str
    efforts: tuple[str, ...]

    def wire(self) -> dict:
        return {
            "value": self.value,
            "resolved": self.resolved,
            "label": self.label,
            "doc": self.doc,
            "efforts": list(self.efforts),
        }


def _doc(text: str) -> str:
    """The first sentence, without the trailing ``(source)``, at most DESC_MAX."""
    text = _SOURCE.sub("", text or "").strip()
    first = re.split(r"(?<=\.)\s|\n", text, maxsplit=1)[0].strip()
    return first if len(first) <= DESC_MAX else first[: DESC_MAX - 1] + "…"


def _source(c: dict) -> str:
    if c.get("builtin"):
        return "claude"
    m = _SOURCE.search(c.get("description") or "")
    return m.group(1) if m else "skill"


@dataclass(frozen=True)
class Catalog:
    commands: tuple[dict, ...]
    models: tuple[Model, ...]

    def has(self, name: str) -> bool:
        return any(c["name"] == name for c in self.commands)

    def model(self, name: str) -> Model | None:
        return next((m for m in self.models if name in (m.value, m.resolved)), None)

    def wire_commands(self, shadowed: Iterable[str]) -> list[dict]:
        skip = set(shadowed)
        return [c for c in self.commands if c["name"] not in skip]

    def wire_models(self) -> list[dict]:
        return [m.wire() for m in self.models]


def from_initialize(body: dict) -> Catalog:
    commands = tuple(
        {
            "name": str(c["name"]),
            "hint": str(c.get("argumentHint") or ""),
            "doc": _doc(str(c.get("description") or "")),
            "source": _source(c),
        }
        for c in body.get("commands") or []
        if isinstance(c, dict) and c.get("name")
    )
    models = tuple(
        Model(
            value=str(m["value"]),
            resolved=str(m.get("resolvedModel") or m["value"]),
            label=str(m.get("displayName") or m["value"]),
            doc=_doc(str(m.get("description") or "")),
            efforts=tuple(m.get("supportedEffortLevels") or ()),
        )
        for m in body.get("models") or []
        if isinstance(m, dict) and m.get("value") and not m.get("disabled")
    )
    return Catalog(commands, models)


async def catalog(proc: ClaudeProcess) -> Catalog:
    return from_initialize(await proc.request("initialize"))


async def probe(
    claude_bin: str, model: str, effort: str, permission: str, cwd: Path, stderr_path: Path
) -> Catalog:
    """A catalog for a cwd with no live process: start ``claude``, ask, end it.
    About 0.5 s and no tokens; ``initialize`` makes no API call."""
    proc = ClaudeProcess(
        build_argv(claude_bin, model, effort, permission), cwd, stderr_path,
        lambda line: None, lambda code, tail: None,
    )
    await proc.start()
    try:
        return await catalog(proc)
    finally:
        await proc.terminate()


async def set_model(proc: ClaudeProcess, value: str) -> None:
    await proc.request("set_model", model=value)


async def set_effort(proc: ClaudeProcess, level: str) -> None:
    await proc.request("apply_flag_settings", settings={"effortLevel": level})
    applied = (await proc.request("get_settings")).get("applied") or {}
    if applied.get("effort") != level:
        raise ControlError(f"claude did not apply effort {level}; it reports {applied.get('effort')}")


async def set_permission(proc: ClaudeProcess, permission: str) -> None:
    await proc.request("set_permission_mode", mode=PERMISSION_MODE.get(permission, permission))
```

- [x] **Step 4: Run, expect PASS**

Run: `uv run pytest tests/test_control.py -q`

- [x] **Step 5: Commit**

```bash
git add src/aegis/claude/control.py tests/test_control.py
git commit -m "feat(claude): the command catalog from initialize, and live model, effort and permission setters (#<n>)"
```

---

### Task 5: The session fetches its catalog and changes its spec

**Files:**
- Modify: `src/aegis/session.py`, `src/aegis/meta.py`, `src/aegis/registry.py`
- Test: `tests/test_session.py`, `tests/test_meta.py`

**Interfaces:**
- Consumes: `control.catalog`, `control.set_model`, `control.set_effort`, `control.set_permission`, `Catalog` (Task 4).
- Produces:
  - `Session.catalog_task: asyncio.Task[Catalog | None] | None`, started on every process start; `None` result when `initialize` failed.
  - `Host.catalog_ready(session, catalog) -> None` (default no-op); `Registry.catalog_ready` calls `self.catalogs.put(session.spec.cwd, catalog)` when `self.catalogs` is set (new attribute, default `None`).
  - `async Session.configure(model: str | None = None, effort: str | None = None, permission: str | None = None) -> None`: applies to a live process first, in that order; records one `configure` record of what applied (with `when`), replaces `spec`, clears `model_id` on a model change, publishes and writes the meta; re-raises the setter's error after recording what did apply.
  - `meta.rebuild` applies `configure` records over the spawn's spec and takes the last `init`'s session id.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_session.py`:

```python
async def test_a_process_start_fetches_the_catalog_and_tells_the_host(tmp_path, fake_claude):
    seen = []

    class Spy(Host):
        def catalog_ready(self, session, catalog):
            seen.append(catalog)

    h = Harness(tmp_path, fake_claude, host=Spy())
    await h.session.start()
    try:
        cat = await h.session.catalog_task
        assert cat is not None and cat.has("hello") and seen == [cat]
    finally:
        await h.session.stop()


async def test_configure_live_switches_the_process_and_the_spec(h):
    await h.session.send("hi")
    await until(lambda: h.session.status == "idle" and h.session.cost_usd, what="the turn")
    await h.session.configure(model="sonnet", effort="max", permission="read")
    s = h.session
    assert (s.spec.model, s.spec.effort, s.spec.permission) == ("sonnet", "max", "read")
    assert s.wire()["model"] == "sonnet"
    await s.send("again")
    await until(lambda: s.model_id == "fake-sonnet", what="the new model's init")
    assert [e["summary"] for e in s.entries() if "→" in e["summary"]] == [
        "model → sonnet · effort → max · permission → read"
    ]


async def test_configure_on_a_stopped_session_starts_nothing_and_the_next_start_uses_it(h):
    await h.session.stop()
    await h.session.configure(model="haiku")
    assert h.session.pid is None
    assert h.session.entries()[-1]["summary"] == "model → haiku (when it resumes)"
    await h.session.send("hello")
    argv = Path(f"/proc/{h.session.pid}/cmdline").read_bytes().split(b"\0")
    assert argv[argv.index(b"--model") + 1] == b"haiku"


async def test_a_refused_change_keeps_what_applied_before_it(h):
    from aegis.claude.process import ControlError

    with pytest.raises(ControlError):
        await h.session.configure(model="haiku", effort="low")  # haiku takes no effort
    assert (h.session.spec.model, h.session.spec.effort) == ("haiku", "high")
```

Add `from aegis.session import Host` to the imports.

Append to `tests/test_meta.py`:

```python
def test_rebuild_applies_configure_records_and_the_last_session_id(tmp_path: Path):
    path = tmp_path / "log-x.jsonl"
    rows = [
        {"i": 0, "ts": 1.0, "src": "aegis", "kind": "spawn", "profile": "opus", "model": "opus", "effort": "high", "permission": "full", "cwd": "/w"},
        {"i": 1, "ts": 2.0, "src": "claude", "line": json.dumps({"type": "system", "subtype": "init", "session_id": "first"})},
        {"i": 2, "ts": 3.0, "src": "aegis", "kind": "configure", "model": "sonnet", "when": "next_turn"},
        {"i": 3, "ts": 4.0, "src": "aegis", "kind": "configure", "effort": "max"},
        {"i": 4, "ts": 5.0, "src": "claude", "line": json.dumps({"type": "system", "subtype": "init", "session_id": "after-clear"})},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    m = rebuild(path)
    assert (m["model"], m["effort"], m["permission"]) == ("sonnet", "max", "full")
    assert m["claude_session_id"] == "after-clear"
```

(Check `tests/test_meta.py`'s imports for `json` and `rebuild`; add them if missing.)

- [x] **Step 2: Run, expect failures**

Run: `uv run pytest tests/test_session.py tests/test_meta.py -q`

- [x] **Step 3: Implement**

`src/aegis/session.py`:

- `import dataclasses`; `from .claude import control`; `from .claude.control import Catalog`.
- `Host` gains:

```python
    def catalog_ready(self, session: "Session", catalog: "Catalog") -> None:
        """A process answered ``initialize``: its cwd's commands and models."""
```

- `__init__`: `self.catalog_task: asyncio.Task | None = None`.
- In `ensure_running`, after `self._proc = proc`:

```python
        self.catalog_task = asyncio.create_task(self._fetch_catalog(proc))
```

and the method:

```python
    async def _fetch_catalog(self, proc: ClaudeProcess) -> "Catalog | None":
        try:
            cat = await control.catalog(proc)
        except (control.ControlError, TimeoutError, BrokenPipeError, ConnectionResetError):
            return None
        self._host.catalog_ready(self, cat)
        return cat
```

(`ControlError` is re-exported by `control` through its import from `.process`.)

- In `_end_process`, before terminating: `if self.catalog_task is not None and not self.catalog_task.done(): self.catalog_task.cancel()`.
- The method:

```python
    async def configure(
        self, model: str | None = None, effort: str | None = None, permission: str | None = None
    ) -> None:
        """Change the model, effort or permission. A live process gets each
        change through a control request first; the spec, which the next
        ``--resume`` is built from, takes only what applied, and a refusal is
        raised after that is recorded."""
        proc = self._proc if self.running else None
        when = "on_resume" if proc is None else "next_turn" if self.status == "working" else ""
        steps = (
            ("model", model, control.set_model),
            ("effort", effort, control.set_effort),
            ("permission", permission, control.set_permission),
        )
        applied: dict[str, str] = {}
        try:
            for kind, value, setter in steps:
                if not value:
                    continue
                if proc is not None:
                    await setter(proc, value)
                applied[kind] = value
        finally:
            if applied:
                self.spec = dataclasses.replace(self.spec, **applied)
                if "model" in applied:
                    self.model_id = None
                self._record({"kind": "configure", **applied, **({"when": when} if when else {})})
                self._publish_now()
                self._metas.write(self.meta())
```

- Module docstring: add a paragraph: "The spec is the session's model, effort and permission. ``configure`` changes them, live through control requests when there is a process, and the next ``--resume`` is built from the spec, so a change outlives the process."

`src/aegis/meta.py` `rebuild`: replace the session-id loop so it keeps the last `Init` with a session id (drop the `break`), and before the `return`:

```python
    spec = {k: spawn.get(k) for k in ("model", "effort", "permission")}
    for r in records:
        if r.get("kind") == "configure":
            spec.update({k: r[k] for k in spec if r.get(k)})
```

then use `spec["model"]`, `spec["effort"]`, `spec["permission"]` in the returned dict.

`src/aegis/registry.py`: in `__init__`, `self.catalogs = None` next to `self.quota = None`; add

```python
    def catalog_ready(self, session: Session, catalog) -> None:
        if self.catalogs is not None:
            self.catalogs.put(session.spec.cwd, catalog)
```

- [x] **Step 4: Run, expect PASS**

Run: `uv run pytest tests/test_session.py tests/test_meta.py tests/test_registry.py -q`

- [x] **Step 5: Commit**

```bash
git add src/aegis/session.py src/aegis/meta.py src/aegis/registry.py tests/test_session.py tests/test_meta.py
git commit -m "feat(session): fetch the command catalog on start; configure model, effort and permission live (#<n>)"
```

---

### Task 6: Resolving `/` lines, `session.configure` and `commands.list`

**Files:**
- Create: `src/aegis/commands.py`
- Modify: `src/aegis/app.py`, `src/aegis/profiles.py`
- Test: `tests/test_commands.py` (create)

**Interfaces:**
- Consumes: `Catalog`, `control.probe` (Task 4); `Session.catalog_task`, `Session.configure`, `Registry.catalogs` (Task 5).
- Produces:
  - `commands.Command(name, hint, doc, args)`; `commands.AEGIS: dict[str, Command]` (`model`, `effort`, `permission`, `rename`, `title`, `stop`, `close`); `commands.split(text) -> tuple[str, str] | None`; `commands.escape(text) -> str`; `commands.aegis_wire() -> list[dict]` (adds `"source": "aegis"` and `"args"`).
  - `commands.Catalogs(claude_bin: str, stderr_path: Path)` with `put(cwd, catalog)` and `async get(session) -> Catalog` (raises `OpError("no_catalog", …)`).
  - Operations: `session.configure {log_id, model?, effort?, permission?}` → the session's wire; `commands.list {log_id}` → `{"commands": [...aegis, ...claude], "models": [...], "permissions": [...]}`; `session.send` resolves `/` lines and returns the aegis command's result (or `None` for text).
  - `App.catalogs`; `profiles.EFFORTS` and `app.Effort` include `xhigh`.

- [x] **Step 1: Write the failing tests** (`tests/test_commands.py`)

```python
import pytest

from aegis.app import App
from aegis.commands import escape, split
from aegis.ops import OpError
from aegis.roots import make_roots
from aegis.transcript.store import read_store

from .conftest import until

CONFIG = "default_agent: opus\nagents:\n  opus: {model: opus, effort: high, permission: full}\n"


@pytest.fixture
async def app(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    a = App(make_roots(tmp_path, None), claude_bin=fake_claude, interrupt_timeout=0.5)
    await a.boot()
    yield a
    await a.shutdown()


async def spawn(app) -> str:
    r = await app.registry.call("session.spawn", {"profile": "opus"})
    return r["log_id"]


async def send(app, log_id, text):
    return await app.registry.call("session.send", {"log_id": log_id, "text": text})


def idle(app, log_id):
    s = app.sessions.sessions[log_id]
    return lambda: s.status == "idle"


def test_split_and_escape():
    assert split("/model sonnet") == ("model", "sonnet")
    assert split("/compact") == ("compact", "")
    assert split("//compact") is None and split("hello") is None and split("/") is None
    assert escape("//compact now") == " /compact now" and escape("/x") == "/x"


async def test_model_switches_live_and_survives_a_stop_and_resume(app):
    lid = await spawn(app)
    await send(app, lid, "hi")
    await until(idle(app, lid), what="the first turn")
    wire = await send(app, lid, "/model sonnet")
    assert wire["model"] == "sonnet"
    s = app.sessions.sessions[lid]
    await s.stop()
    await send(app, lid, "back")
    argv = open(f"/proc/{s.pid}/cmdline", "rb").read().split(b"\0")
    assert argv[argv.index(b"--model") + 1] == b"sonnet"


async def test_bad_models_and_efforts_are_refused_before_anything_is_sent(app):
    lid = await spawn(app)
    for text, code in (
        ("/model gpt-5", "unknown_model"),
        ("/model retired", "unknown_model"),
        ("/effort extreme", "bad_effort"),
        ("/permission god", "bad_permission"),
        ("/model", "missing_argument"),
    ):
        with pytest.raises(OpError) as e:
            await send(app, lid, text)
        assert e.value.code == code, text
    await send(app, lid, "/model haiku")
    with pytest.raises(OpError, match="Haiku takes no effort level"):
        await send(app, lid, "/effort high")


async def test_an_unknown_command_is_refused_and_nothing_is_stored(app):
    lid = await spawn(app)
    with pytest.raises(OpError, match="// to send it as text") as e:
        await send(app, lid, "/etc/hosts is broken")
    assert e.value.code == "unknown_command"
    records, _ = read_store(app.sessions.store_path(lid))
    assert not [r for r in records if r.get("kind") == "send"]


async def test_the_escape_sends_a_prompt_that_claude_does_not_run(app):
    lid = await spawn(app)
    await send(app, lid, "//compact now")
    await until(idle(app, lid), what="the turn")
    es = app.sessions.sessions[lid].entries()
    assert [e["md"] for e in es if e["kind"] == "user"] == ["/compact now"]
    assert not [e for e in es if "compacted" in e["summary"]]


async def test_claudes_commands_pass_through_and_fold_cleanly(app):
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    for text in ("/context", "/hello world", "/compact"):
        await send(app, lid, text)
        await until(lambda: s.status == "idle", what=text)
    first_id = s.claude_session_id
    await send(app, lid, "/clear")
    await until(lambda: s.status == "idle", what="/clear")
    await send(app, lid, "after")
    await until(lambda: s.status == "idle" and s.claude_session_id != first_id, what="the new conversation")
    es = s.entries()
    assert not [e for e in es if e["status"] == "pending"]
    assert [e["title"] for e in es if e["kind"] == "command"] == ["/context", "/compact"]
    assert [e["md"] for e in es if e["kind"] == "user"] == ["/hello world", "after"]
    assert any(e["summary"].startswith("context cleared") for e in es)


async def test_effort_mid_turn_says_from_the_next_turn(app):
    lid = await spawn(app)
    s = app.sessions.sessions[lid]
    await send(app, lid, "/sleep 1")
    await until(lambda: s.status == "working", what="the turn")
    await send(app, lid, "/effort low")
    assert s.status == "working" and s.spec.effort == "low"
    assert any(e["summary"] == "effort → low (from the next turn)" for e in s.entries())


async def test_aegis_commands_rename_title_stop_and_close(app):
    lid = await spawn(app)
    await send(app, lid, "/rename brave-otter")
    await send(app, lid, "/title Fix the parser")
    s = app.sessions.sessions[lid]
    assert (s.handle, s.title) == ("brave-otter", "Fix the parser")
    await send(app, lid, "/stop")
    assert s.pid is None
    await send(app, lid, "/close")
    assert lid in app.sessions.archived


async def test_commands_list_puts_aegis_first_and_hides_what_it_shadows(app):
    lid = await spawn(app)
    r = await app.registry.call("commands.list", {"log_id": lid})
    names = [c["name"] for c in r["commands"]]
    assert names[:7] == ["model", "effort", "permission", "rename", "title", "stop", "close"]
    assert names.count("model") == 1 and names.count("rename") == 1
    src = {c["name"]: c["source"] for c in r["commands"]}
    assert (src["model"], src["compact"], src["hello"], src["sleep"]) == ("aegis", "claude", "project", "skill")
    assert [m["value"] for m in r["models"]] == ["opus", "sonnet", "haiku"]
    assert r["permissions"] == ["read", "write", "full", "auto"]


@pytest.mark.slow
async def test_a_stopped_session_after_a_restart_gets_its_catalog_from_a_probe(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    a = App(make_roots(tmp_path, None), claude_bin=fake_claude, interrupt_timeout=0.5)
    await a.boot()
    lid = (await a.registry.call("session.spawn", {"profile": "opus"}))["log_id"]
    await a.shutdown()
    b = App(make_roots(tmp_path, None), claude_bin=fake_claude, interrupt_timeout=0.5)
    await b.boot()
    try:
        r = await b.registry.call("commands.list", {"log_id": lid})
        assert "compact" in {c["name"] for c in r["commands"]}
        assert b.sessions.sessions[lid].pid is None
    finally:
        await b.shutdown()
```

- [x] **Step 2: Run, expect ImportError**

Run: `uv run pytest tests/test_commands.py -q`

- [x] **Step 3: Implement `src/aegis/commands.py`**

```python
"""The composer's slash lines, resolved on the server.

A line that starts with ``/`` names an aegis command, which runs an operation
and sends nothing to ``claude``, or one of the session's harness commands, which
goes to ``claude`` as typed. ``//rest`` is a prompt: it is sent as `` /rest``,
because Claude Code runs a line as a command only when the slash comes first.
Any other ``/`` line is refused before it costs a turn: Claude answers an
unknown command with the model.

The harness part of the list comes from ``initialize`` (``claude/control.py``)
and is kept in memory by cwd: commands come from the cwd's ``.claude/`` and the
user's config. A cwd with no live process is probed once. Nothing goes to disk,
so an upgraded CLI never meets a stale list.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from .claude import control
from .claude.control import Catalog
from .ops import OpError
from .session import Session


@dataclass(frozen=True)
class Command:
    name: str
    hint: str
    doc: str
    args: str = ""  # what the menu completes: "models", "efforts" or "permissions"


AEGIS: dict[str, Command] = {
    c.name: c
    for c in (
        Command("model", "<name>", "Switch this session's model", "models"),
        Command("effort", "<level>", "Switch this session's reasoning effort", "efforts"),
        Command("permission", "<mode>", "Switch what the agent may do without asking", "permissions"),
        Command("rename", "<handle>", "Rename this session's handle"),
        Command("title", "<text>", "Set this session's title"),
        Command("stop", "", "Stop the process and keep the session"),
        Command("close", "", "Close the session; it stays in the archive"),
    )
}


def split(text: str) -> tuple[str, str] | None:
    """``/name rest`` as ``(name, rest)``; None for a prompt."""
    if not text.startswith("/") or text.startswith("//"):
        return None
    parts = text[1:].split(maxsplit=1)
    if not parts:
        return None
    return parts[0], parts[1].strip() if len(parts) > 1 else ""


def escape(text: str) -> str:
    return " " + text[1:] if text.startswith("//") else text


def aegis_wire() -> list[dict]:
    return [
        {"name": c.name, "hint": c.hint, "doc": c.doc, "source": "aegis", "args": c.args}
        for c in AEGIS.values()
    ]


class Catalogs:
    def __init__(self, claude_bin: str, stderr_path: Path) -> None:
        self._claude_bin = claude_bin
        self._stderr = stderr_path
        self._by_cwd: dict[str, Catalog] = {}

    def put(self, cwd: Path, catalog: Catalog) -> None:
        self._by_cwd[str(cwd)] = catalog

    async def get(self, s: Session) -> Catalog:
        t = s.catalog_task
        if t is not None:
            await asyncio.wait([t])
            if not t.cancelled() and t.result() is not None:
                return t.result()
        hit = self._by_cwd.get(str(s.spec.cwd))
        if hit is not None:
            return hit
        sp = s.spec
        try:
            cat = await control.probe(
                self._claude_bin, sp.model, sp.effort, sp.permission, sp.cwd, self._stderr
            )
        except (control.ControlError, TimeoutError, OSError) as e:
            raise OpError(
                "no_catalog",
                f"cannot list claude's commands: {e}; start the line with // to send it as text",
            ) from e
        self.put(sp.cwd, cat)
        return cat
```

(`BrokenPipeError` and `ConnectionResetError` are `OSError`s.)

- [x] **Step 4: Wire the app** (`src/aegis/app.py`, `src/aegis/profiles.py`)

`profiles.py`: `EFFORTS = ("low", "medium", "high", "xhigh", "max")`.

`app.py`:

- Imports: `from . import commands`; `from .claude.process import ControlError`; `from .profiles import EFFORTS, PERMISSION_MODE, ...` (keep the existing names).
- `Effort = Literal["low", "medium", "high", "xhigh", "max"]`.
- Params:

```python
class ConfigureParams(_Strict):
    log_id: str
    model: str | None = None
    effort: Effort | None = None
    permission: Permission | None = None
```

- In `__init__`, after the registry is built: `self.catalogs = commands.Catalogs(claude_bin, roots.state_root / "stderr" / "catalog-probe.log")` and `reg.catalogs = self.catalogs`.
- Methods on `App`:

```python
    async def _configure(self, s, model: str | None, effort: str | None, permission: str | None) -> dict:
        if not (model or effort or permission):
            raise OpError("bad_params", "nothing to change")
        if model or effort:
            cat = await self.catalogs.get(s)
            if model:
                m = cat.model(model)
                if m is None:
                    raise OpError("unknown_model", f"no model {model!r}; /model lists them")
                model = m.value
            if effort:
                cur = cat.model(model or s.spec.model or "default")
                if cur is not None and effort not in cur.efforts:
                    raise OpError(
                        "bad_effort",
                        f"{cur.label} takes no effort level"
                        if not cur.efforts
                        else f"{cur.label} takes {', '.join(cur.efforts)}",
                    )
        try:
            await s.configure(model=model, effort=effort, permission=permission)
        except ControlError as e:
            raise OpError("refused", f"claude refused: {e}") from e
        except TimeoutError as e:
            raise OpError("timeout", "claude did not answer within 15 s") from e
        except (BrokenPipeError, ConnectionResetError) as e:
            raise _dead(e) from e
        return s.wire()

    async def _command(self, s, name: str, arg: str):
        """Run an aegis command typed in the composer."""
        cmd = commands.AEGIS[name]
        if cmd.hint.startswith("<") and not arg:
            raise OpError("missing_argument", f"usage: /{name} {cmd.hint}")
        reg = self.sessions
        if name == "model":
            return await self._configure(s, arg, None, None)
        if name == "effort":
            if arg not in EFFORTS:
                raise OpError("bad_effort", f"an effort is one of {', '.join(EFFORTS)}")
            return await self._configure(s, None, arg, None)
        if name == "permission":
            if arg not in PERMISSION_MODE:
                raise OpError("bad_permission", f"a permission is one of {', '.join(PERMISSION_MODE)}")
            return await self._configure(s, None, None, arg)
        if name == "rename":
            return reg.rename(s.log_id, arg, None)
        if name == "title":
            return reg.rename(s.log_id, None, arg)
        if name == "stop":
            await s.stop()
            return s.wire()
        await reg.close(s.log_id)  # close
        return None
```

- In `_register`, the `send` handler becomes:

```python
        @r.op("session.send", SendParams)
        async def send(p: SendParams, caller):
            s = reg.open(p.log_id)
            text = p.text
            cmd = commands.split(text)
            if cmd is not None:
                name, arg = cmd
                if name in commands.AEGIS:
                    return await self._command(s, name, arg)
                if not (await self.catalogs.get(s)).has(name):
                    raise OpError(
                        "unknown_command",
                        f"no command /{name} in this session; start the line with // to send it as text",
                    )
            try:
                await s.send(commands.escape(text))
            except FileNotFoundError as e:
                ...unchanged...
```

- New operations, next to `session.send`:

```python
        @r.op("session.configure", ConfigureParams)
        async def configure(p: ConfigureParams, caller):
            return await self._configure(reg.open(p.log_id), p.model, p.effort, p.permission)

        @r.op("commands.list", LogParams)
        async def commands_list(p: LogParams, caller):
            """What the composer's menu offers this session."""
            cat = await self.catalogs.get(reg.open(p.log_id))
            return {
                "commands": commands.aegis_wire() + cat.wire_commands(shadowed=commands.AEGIS),
                "models": cat.wire_models(),
                "permissions": list(PERMISSION_MODE),
            }
```

- Update the module docstring's operation list with `session.configure` and `commands.list`, and say `session.send` resolves `/` lines (`commands.py`).

- [x] **Step 5: Run, expect PASS**

Run: `uv run pytest tests/test_commands.py tests/test_web.py tests/test_agents.py -q`

- [x] **Step 6: Commit**

```bash
git add src/aegis/commands.py src/aegis/app.py src/aegis/profiles.py tests/test_commands.py
git commit -m "feat: / lines resolved on the server; session.configure and commands.list (#<n>)"
```

---

### Task 7: The menu in the browser

**Files:**
- Create: `src/aegis/client/js/commands.js`
- Modify: `src/aegis/client/js/app.js`, `src/aegis/client/js/keys.js`, `src/aegis/client/js/entries.js`, `src/aegis/client/css/base.css`, `src/aegis/client/index.html`
- Test: `tests/test_browser.py`

**Interfaces:**
- Consumes: `commands.list`, `session.send` (Task 6); entry kind `command` (Task 2).
- Produces (JS module `commands.js`): `fuzzy(query, text) -> {score, positions} | null`; `complete(line, catalog, meta) -> {items: [{insert, label, hint, doc, source}], run: bool}`; `class CommandMenu(box, {load, run, focusInput})` with `openInline()`, `openOverlay()`, `close()`, `isOpen`, `onInput(line)`, `onKey(ev) -> boolean` (true when the menu took the key).

- [x] **Step 1: Write the failing browser tests** (append to `tests/test_browser.py`, same markers as the tests around them)

```python
def menu_rows(pg) -> list[str]:
    return pg.evaluate("[...document.querySelectorAll('#cmd-menu .cmd-row .nm')].map(n => n.textContent)")


def test_the_menu_completes_a_model_and_esc_closes_it_without_interrupting(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    page.fill("#input", "/sleep 3")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    page.click("#input")
    page.keyboard.type("/mo")
    page.wait_for_selector("#cmd-menu:not([hidden])")
    assert menu_rows(page)[0] == "/model"
    page.keyboard.press("Tab")
    assert page.input_value("#input") == "/model "
    page.keyboard.type("son")
    page.keyboard.press("Enter")  # accepts the highlighted model
    assert page.input_value("#input") == "/model sonnet "
    page.keyboard.press("Escape")
    assert page.is_hidden("#cmd-menu")
    assert page.is_visible(".row.tool.running"), "Esc on the menu does not interrupt"
    page.press("#input", "Enter")
    page.wait_for_function("() => document.getElementById('chip-model').textContent === 'sonnet'")
    assert page.input_value("#input") == ""
    assert page.errors == []


def test_alt_slash_with_a_draft_runs_a_command_and_keeps_the_draft(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    page.fill("#input", "half a thought")
    page.keyboard.press("Alt+/")
    page.wait_for_selector("#cmd-filter")  # prefilled with "/"
    page.keyboard.type("effort lo")
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    page.wait_for_function("() => document.getElementById('chip-effort').textContent === 'low effort'")
    assert page.input_value("#input") == "half a thought"
    assert page.errors == []


def test_an_unknown_command_is_flagged_and_claudes_commands_render(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    page.fill("#input", "/bogus x")
    page.wait_for_selector(".composer.bad")
    page.press("#input", "Enter")
    page.wait_for_function("() => /\\/\\/ to send it as text/.test(document.getElementById('send-error').textContent)")
    page.fill("#input", "/context")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.command .cmd")
    assert page.inner_text(".row.command .cmd") == "/context"
    assert page.errors == []


def test_clicking_the_model_chip_opens_the_menu_on_models(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    page.click("#chip-model")
    page.wait_for_selector("#cmd-menu:not([hidden])")
    assert page.input_value("#input") == "/model "
    assert menu_rows(page)[:3] == ["opus", "sonnet", "haiku"]
```

- [x] **Step 2: Run, expect failures**

Run: `uv run pytest tests/test_browser.py -q -m browser -k "menu or alt_slash or unknown_command or chip"`

- [x] **Step 3: Markup and styles**

`index.html`: inside `.composer`, before the textarea, add

```html
<div class="cmd-menu" id="cmd-menu" hidden><input id="cmd-filter" hidden spellcheck="false" placeholder="/command"><div id="cmd-rows"></div></div>
```

and change the textarea placeholder to `Message the agent. Enter sends, Shift+Enter adds a line, / for commands, Esc interrupts.` (also in `renderMeta` in `app.js`).

`base.css`, after the composer block:

```css
#a2 .composer{position:relative}
#a2 .cmd-menu{position:absolute;left:22px;right:22px;bottom:calc(100% - 6px);background:var(--surface);border:1px solid var(--rule);border-radius:var(--r-lg);padding:4px;max-height:280px;overflow:auto;font-family:var(--font-chrome);font-size:12.5px;z-index:5}
#a2 .cmd-menu input{width:100%;margin-bottom:4px;padding:6px 8px;background:var(--bg);border:1px solid var(--accent);border-radius:4px;color:var(--strong);font:inherit;outline:none}
#a2 .cmd-row{display:grid;grid-template-columns:auto auto 1fr auto;gap:10px;padding:4px 8px;border-radius:4px;cursor:pointer;align-items:baseline}
#a2 .cmd-row.on{background:var(--accent-soft)}
#a2 .cmd-row .nm{color:var(--strong)}#a2 .cmd-row .nm b{color:var(--accent)}
#a2 .cmd-row .hn,#a2 .cmd-row .dc{color:var(--muted);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
#a2 .cmd-row .src{color:var(--faint);font-size:11px}
#a2 .composer.bad textarea{border-color:var(--err)}
#a2 .chip.click{cursor:pointer}#a2 .chip.click:hover{border-color:var(--accent);color:var(--strong)}
#a2 .row.command .cmd{font-family:var(--font-chrome);color:var(--strong)}
```

- [x] **Step 4: `commands.js`**

```js
// The composer's command menu: one list for typing "/" and for Alt+/.
//
// Everything it shows comes from `commands.list`: names, hints, descriptions,
// sources, models, efforts. It decides only which rows match what is typed;
// the server resolves and runs the line (commands.py).

// The legacy TUI's scorer (legacy/aegis/commands/fuzzy.py): a case-insensitive
// subsequence, +2 per character that follows the previous match, +3 at a word
// start, and -0.01 per character of length so shorter names win ties.
export function fuzzy(query, text) {
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  let score = 0;
  let last = -2;
  const positions = [];
  let j = 0;
  for (let i = 0; i < t.length && j < q.length; i++) {
    if (t[i] !== q[j]) continue;
    if (i === last + 1) score += 2;
    if (i === 0 || /[\s:_\-/.]/.test(t[i - 1])) score += 3;
    positions.push(i);
    last = i;
    j++;
  }
  if (j < q.length) return null;
  return { score: score - 0.01 * t.length, positions };
}

const RANK = { aegis: 0, claude: 1 };

function ranked(query, rows, key) {
  if (!query) return rows.map((r) => ({ ...r, positions: [] })); // the list's own order: aegis first
  return rows
    .map((r, i) => ({ r, i, m: fuzzy(query, key(r)) }))
    .filter((x) => x.m)
    .sort((a, b) => b.m.score - a.m.score || (RANK[a.r.source] ?? 2) - (RANK[b.r.source] ?? 2) || a.i - b.i)
    .map((x) => ({ ...x.r, positions: x.m.positions }));
}

// What a line can become. `meta` is the session's card: its model, for /effort.
export function complete(line, catalog, meta) {
  if (!line.startsWith("/") || line.startsWith("//")) return { items: [] };
  const sp = line.indexOf(" ");
  if (sp < 0) {
    const q = line.slice(1);
    const items = ranked(q, catalog.commands, (c) => c.name).map((c) => ({
      insert: `/${c.name} `,
      label: `/${c.name}`,
      positions: c.positions.map((p) => p + 1),
      hint: c.hint,
      doc: c.doc,
      source: c.source,
    }));
    return { items };
  }
  const name = line.slice(1, sp);
  const arg = line.slice(sp + 1).trim();
  const cmd = catalog.commands.find((c) => c.name === name);
  if (!cmd || !cmd.args || arg.includes(" ")) return { items: [] };
  let choices = [];
  if (cmd.args === "models") choices = catalog.models.map((m) => ({ v: m.value, doc: `${m.label}. ${m.doc}`, src: m.efforts.length ? "effort" : "" }));
  if (cmd.args === "permissions") choices = catalog.permissions.map((p) => ({ v: p, doc: "", src: "" }));
  if (cmd.args === "efforts") {
    const m = catalog.models.find((x) => meta && (x.value === meta.model || x.resolved === meta.model)) || null;
    choices = (m ? m.efforts : ["low", "medium", "high", "xhigh", "max"]).map((e) => ({ v: e, doc: "", src: "" }));
  }
  const items = ranked(arg, choices.map((c) => ({ ...c, source: c.src })), (c) => c.v).map((c) => ({
    insert: `/${name} ${c.v} `,
    label: c.v,
    positions: c.positions,
    hint: "",
    doc: c.doc,
    source: c.source,
  }));
  return { items };
}

function rowNode(item, on) {
  const r = document.createElement("div");
  r.className = `cmd-row${on ? " on" : ""}`;
  const nm = document.createElement("span");
  nm.className = "nm";
  const hit = new Set(item.positions || []);
  [...item.label].forEach((ch, i) => {
    if (hit.has(i)) {
      const b = document.createElement("b");
      b.textContent = ch;
      nm.append(b);
    } else nm.append(ch);
  });
  const hn = document.createElement("span");
  hn.className = "hn";
  hn.textContent = item.hint || "";
  const dc = document.createElement("span");
  dc.className = "dc";
  dc.textContent = item.doc || "";
  const src = document.createElement("span");
  src.className = "src";
  src.textContent = item.source || "";
  r.append(nm, hn, dc, src);
  return r;
}

export class CommandMenu {
  // load(): Promise<catalog> for the focused session, or null when there is none.
  // run(line): send a line from the overlay. getLine/setLine: the composer.
  constructor({ box, rows, filter, load, run, getLine, setLine, meta }) {
    Object.assign(this, { box, rows, filter, load, run, getLine, setLine, meta });
    this.items = [];
    this.at = 0;
    this.catalog = null;
    this.overlay = false;
    rows.addEventListener("mousedown", (ev) => {
      const r = ev.target.closest(".cmd-row");
      if (!r) return;
      ev.preventDefault();
      this.at = [...rows.children].indexOf(r);
      this.accept();
    });
    filter.addEventListener("input", () => this.refresh());
    filter.addEventListener("keydown", (ev) => {
      if (this.onKey(ev)) return;
      if (ev.key === "Enter") {
        ev.preventDefault();
        const line = filter.value.trim();
        this.close();
        if (line) this.run(line);
      }
    });
  }

  get isOpen() {
    return !this.box.hidden;
  }

  line() {
    return this.overlay ? this.filter.value : this.getLine();
  }

  setText(v) {
    if (this.overlay) this.filter.value = v;
    else this.setLine(v);
  }

  async show() {
    this.catalog = await this.load();
    if (!this.catalog) return;
    this.box.hidden = false;
    this.refresh();
  }

  openInline() {
    this.overlay = false;
    this.filter.hidden = true;
    return this.show();
  }

  openOverlay() {
    this.overlay = true;
    this.filter.hidden = false;
    this.filter.value = "/";
    return this.show().then(() => this.filter.focus());
  }

  close() {
    this.box.hidden = true;
    this.overlay = false;
    this.filter.hidden = true;
  }

  refresh() {
    if (!this.catalog) return;
    const line = this.line();
    if (!line.startsWith("/") || line.startsWith("//")) {
      if (!this.overlay) this.close();
      return;
    }
    this.items = complete(line, this.catalog, this.meta()).items.slice(0, 50);
    this.at = 0;
    this.rows.replaceChildren(...this.items.map((it, i) => rowNode(it, i === this.at)));
  }

  // Whether the line is a known command, for the composer's outline.
  known(line) {
    if (!this.catalog) return true;
    const name = line.slice(1).split(/\s/)[0];
    return !name || this.catalog.commands.some((c) => c.name === name);
  }

  move(d) {
    if (!this.items.length) return;
    this.at = (this.at + d + this.items.length) % this.items.length;
    [...this.rows.children].forEach((r, i) => r.classList.toggle("on", i === this.at));
    this.rows.children[this.at]?.scrollIntoView({ block: "nearest" });
  }

  accept() {
    const it = this.items[this.at];
    if (!it) return;
    this.setText(it.insert);
    this.refresh();
  }

  // Tab accepts; Enter accepts while that would change the line, and is left
  // to the caller (send) once the line is a whole command. An aegis command
  // whose argument is missing never sends: Enter opens its arguments.
  onKey(ev) {
    if (!this.isOpen) return false;
    const line = this.line();
    const it = this.items[this.at];
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      this.move(ev.key === "ArrowDown" ? 1 : -1);
      return true;
    }
    if (ev.key === "Escape") {
      ev.preventDefault();
      ev.stopPropagation();
      this.close();
      return true;
    }
    if (ev.key === "Tab" && it) {
      ev.preventDefault();
      this.accept();
      return true;
    }
    if (ev.key === "Enter" && !ev.shiftKey && it && it.insert.trimEnd() !== line.trimEnd()) {
      ev.preventDefault();
      this.accept();
      return true;
    }
    if (ev.key === "Enter" && !ev.shiftKey) {
      const name = line.slice(1).split(/\s/)[0];
      const cmd = this.catalog?.commands.find((c) => c.name === name);
      if (cmd?.args && !line.trim().includes(" ")) {
        ev.preventDefault();
        this.setText(`/${name} `);
        this.refresh();
        return true;
      }
    }
    return false;
  }
}
```

- [x] **Step 5: Wire it** (`keys.js`, `app.js`, `entries.js`)

`keys.js`, a global row after `Alt+.`:

```js
  { scope: "global", label: "Alt+/", desc: "Commands for this session", action: "commands", match: alt("Slash") },
```

`app.js`:

```js
import { CommandMenu } from "./commands.js";

// Catalogs per session, fetched when the menu first opens there and again
// after a command that changes the model.
const catalogs = new Map();
async function loadCatalog() {
  const s = focused();
  if (!s) return null;
  if (!catalogs.has(s.log_id)) {
    try {
      catalogs.set(s.log_id, await conn.call("commands.list", { log_id: s.log_id }));
    } catch (e) {
      $("send-error").textContent = e.message;
      return null;
    }
  }
  return catalogs.get(s.log_id);
}

const menu = new CommandMenu({
  box: $("cmd-menu"),
  rows: $("cmd-rows"),
  filter: $("cmd-filter"),
  load: loadCatalog,
  run: (line) => sendLine(line, false),
  getLine: () => input.value,
  setLine: (v) => {
    input.value = v;
    autosize();
    if (shown) localStorage.setItem(`aegis.draft.${shown}`, v);
    input.focus();
  },
  meta: () => focused(),
});
```

Split `send()` into `sendLine(text, fromComposer)`: the body of today's `send` with `text` as a parameter; it clears the composer and its draft only when `fromComposer` is true; on success for a line starting with `/model` it deletes `catalogs` entry for the session; a line equal to `/close` asks `confirm(...)` with the close button's wording first. `send()` becomes `sendLine(input.value.trim(), true)`. When `sendLine` clears the composer it also calls `menu.close()` and removes the composer's `bad` class: clearing `input.value` fires no `input` event, and a menu left open would take the next Esc instead of letting it interrupt.

The composer listeners:

```js
input.addEventListener("input", async () => {
  autosize();
  if (shown) localStorage.setItem(`aegis.draft.${shown}`, input.value);
  const v = input.value;
  const slash = v.startsWith("/") && !v.startsWith("//");
  if (slash) {
    if (menu.isOpen) menu.refresh();
    else await menu.openInline(); // the outline below needs the catalog it loads
  } else if (menu.isOpen) menu.close();
  const now = input.value;
  $("composer").classList.toggle("bad", now.startsWith("/") && !now.startsWith("//") && !menu.known(now));
});
input.addEventListener("keydown", (ev) => {
  if (menu.onKey(ev)) return;
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
    ev.preventDefault();
    send();
  }
});
```

Give the `.composer` div `id="composer"` in `index.html`. In `follow()`, close the menu when the focused session changes. The `installKeys` actions gain:

```js
    commands() {
      if (route().view !== "session") return;
      const v = input.value;
      if (!v || v.startsWith("/")) {
        if (!v) input.value = "/";
        input.focus();
        menu.openInline();
      } else menu.openOverlay();
    },
```

Chips: in the composer setup,

```js
for (const [id, cmd] of [["chip-model", "model"], ["chip-effort", "effort"], ["chip-perm", "permission"]]) {
  $(id).classList.add("click");
  $(id).addEventListener("click", () => {
    input.value = `/${cmd} `;
    input.focus();
    menu.openInline();
  });
}
```

`entries.js`, a renderer:

```js
  command(e) {
    const body = el("div", "body");
    body.append(el("div", "cmd", e.title));
    if (e.md) body.append(markdown(e.md));
    return row(e, `command ${e.status}`, body);
  },
```

- [x] **Step 6: Run the browser tests, expect PASS**

Run: `uv run pytest tests/test_browser.py -q -m browser`
Expected: all pass, the existing ones included (they send `/sleep`, `/fail` and the menu must not swallow their Enter: those lines are whole commands, so Enter sends).

- [x] **Step 7: Exercise it by hand in a browser**

Start a server from this worktree on a free port against a throwaway root, with the real `claude`:

```bash
mkdir -p /tmp/aegis-slash-try && printf 'default_agent: s\nagents:\n  s: {model: sonnet, effort: low, permission: read}\n' > /tmp/aegis-slash-try/.aegis.yaml
uv run aegis serve --root /tmp/aegis-slash-try --port 8931
```

Open the printed URL, spawn, and check: `/` lists commands with sources; `/mo` Tab `son` Enter Enter switches the chip; `/effort` lists levels; `/context` renders a command row; Alt+/ over a draft keeps the draft; `/bogus` is flagged. Use the `saidkick` skill or a person's browser. Stop the server with Ctrl+C. Never point it at the Workspace root (it would read the live state).

- [x] **Step 8: Commit**

```bash
git add src/aegis/client/js/commands.js src/aegis/client/js/app.js src/aegis/client/js/keys.js src/aegis/client/js/entries.js src/aegis/client/css/base.css src/aegis/client/index.html tests/test_browser.py
git commit -m "feat(client): the command menu, Alt+/, clickable chips and command rows (#<n>)"
```

---

### Task 8: A real Claude, the docs and the release note

**Files:**
- Modify: `tests/test_live.py`, `DESIGN.md`, `docs/superpowers/specs/2026-10-07-aegis-2-slash-commands-design.md`, `src/aegis/client/index.html` (spawn form)
- Create: `changelog.d/<n>-slash-commands.added.md`

- [x] **Step 1: A live test** (append to `tests/test_live.py`)

```python
async def test_real_claude_switches_model_and_effort_and_keeps_them_across_resume(tmp_path: Path):
    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    from aegis.claude import control

    path = tmp_path / "log.jsonl"
    s = Session(
        log_id="live-cmd",
        spec=SpawnSpec("haiku", HAIKU, "low", "full", tmp_path),
        handle="live-cmd",
        store=Store(path),
        stderr_path=tmp_path / "stderr.log",
        claude_bin=claude,
        publish=lambda ch, ops: None,
        metas=MetaStore(tmp_path / "sessions"),
    )
    await s.start()
    try:
        cat = await s.catalog_task
        assert cat and cat.has("compact") and cat.model("sonnet")
        await s.configure(model="sonnet", effort="low")
        await s.send("Reply with the single word OK.")
        await until(lambda: s.status == "idle" and s.cost_usd, timeout=90, what="the turn")
        assert s.model_id and "sonnet" in s.model_id
        await s.send("/context")
        await until(lambda: s.status == "idle" and any(e["kind"] == "command" for e in s.entries()), timeout=60, what="/context")
        await s.stop()
        await s.send("Reply with the single word OK.")
        await until(lambda: s.status == "idle", timeout=90, what="the resumed turn")
        assert "sonnet" in (s.model_id or "")
        assert not [e for e in s.entries() if e["status"] == "pending"]
    finally:
        await s.stop()
    records, damaged = read_store(path)
    assert damaged == 0 and fold_records(records).entries() == s.entries()
```

Run: `make test-live` (spends a few cents of Sonnet; run it once).

- [x] **Step 2: The spawn form offers xhigh**

`index.html`: the `#sp-effort` select becomes `<option>low</option><option>medium</option><option>high</option><option>xhigh</option><option>max</option>`.

- [x] **Step 3: DESIGN.md**

Add after "**Agents call the same operations, as MCP tools named after them.**":

```markdown
**A `/` line is resolved on the server, and a typo costs nothing.** `session.send`
runs an aegis command (`commands.py`) as the operation it stands for, passes a name
in the session's catalog to `claude` as typed, and refuses anything else: Claude
answers an unknown command with the model. The catalog is Claude's own
`initialize` answer, in memory by cwd, never on disk, so an upgraded CLI never
meets a stale list. `/model`, `/effort` and `/permission` are aegis's, because the
spec the next `--resume` is built from has to change with the process; they reach
a live `claude` as control requests whose answers `ClaudeProcess.request` routes
and does not store.
```

and extend "**The echo creates the user entry, never the send.**" with: "An answer takes the pending send whose text it answers, else the oldest of its kind, because a slash command waits for the turn to end while a prompt is read at the next tool boundary."

- [x] **Step 4: The release note** (`changelog.d/<n>-slash-commands.added.md`)

```markdown
- **Slash commands in the composer.** `/model`, `/effort` and `/permission` switch a
  running session through Claude's control requests and survive a resume; `/rename`,
  `/title`, `/stop` and `/close` act on the session; Claude's own commands, skills
  and `.claude/commands` pass through. Typing `/` or pressing Alt+/ opens a menu that
  completes names, models and effort levels. An unknown command is refused instead
  of being sent to the model as a paid prompt.
```

Run: `make changelog-check`

- [x] **Step 5: Spec status**

Change the spec's status line to `> **Status:** implemented, 2026-10-<dd>. Plan: docs/superpowers/plans/2026-10-08-aegis-2-slash-commands.md.` and tick this plan's boxes.

- [x] **Step 6: Gates, bench, PR**

```bash
make format lint lint-docs typecheck changelog-check
make bench
git add tests/test_live.py DESIGN.md docs/superpowers/specs/2026-10-07-aegis-2-slash-commands-design.md docs/superpowers/plans/2026-10-08-aegis-2-slash-commands.md src/aegis/client/index.html changelog.d/<n>-slash-commands.added.md
git commit -m "docs: slash commands in DESIGN.md, the release note, the spec implemented (#<n>)"
git push -u origin design/slash-commands
gh pr create -R apiad/aegis --title "Slash commands: /model, /effort, Claude's commands and the menu (#<n>)" --body-file <body>
```

The PR body carries the bench table, the `make test-live` result, what was exercised by hand in Step 7 of Task 7, and what was left out (Fleet commands, legacy commands, agent access to `session.configure`, OpenCode). End it with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Stop there: Alex merges.
