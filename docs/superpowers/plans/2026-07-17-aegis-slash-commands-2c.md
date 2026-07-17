# Slash commands 2C — Prompt commands + plugin `@command` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new slash-command *sources* to aegis — user-authored
`.aegis/commands/*.md` prompt commands (expand → sent to the agent) and a plugin
`@command` decorator (control command, like a builtin) — plus source-coloring in
the 2D palette.

**Architecture:** Both sources produce ordinary `SlashCommand`s carrying a
`source` tag, so 2A's `dispatch()`, `/help`, and 2D's `complete()` pick them up
unchanged. Prompt commands ride the existing `CommandResult.effect` channel: a
`{"kind":"deliver","text":…}` effect tells each dispatch seam to route the
expanded text to the agent (normal `core.deliver`) instead of mounting a result
block. Plugin commands auto-register on the existing `import_plugins()` sweep.

**Tech Stack:** Python 3.13, `uv`, pytest (Textual `run_test` for TUI), ruamel
YAML (already a dep), asyncio subprocess (via the existing `run_shell_escape`).

## Global Constraints

- **Package manager:** `uv`. Run tests with `uv run python -m pytest`.
- **Test selector:** `-m "not live"` (the marker). NEVER `-k "not live"` (substring bug).
- **TDD:** failing test first → run-fail → minimal impl → run-pass → commit. One logical unit per commit.
- **Gate before every commit:** `uv run ruff check <changed files>` as its own step; check the exit code. Never pipe the gate through `tail`.
- **`find_project_root()` walks UP to the Workspace root.** Every test that loads `.aegis/commands/` or plugin files MUST use a temp dir **outside** `/home/apiad/Workspace` (use pytest `tmp_path`, which is under `/tmp`).
- **TUI flake:** TUI/`run_test` tests intermittently flake on zion (inotify). Re-run a failing TUI test ALONE before believing it: `uv run python -m pytest <path>::<test> -v`.
- **Source precedence:** `builtin (0) > user (1) > plugin (2)`. Higher priority (lower number) wins regardless of load order.
- **Commit trailer:** end each commit message with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Fast iteration gate:** the command-suite subset is `tests/test_command_*.py tests/test_slash_commands.py tests/test_pane_slash_command.py tests/test_web_slash.py tests/test_pane_palette.py tests/test_web_complete.py tests/test_plugin_import.py`. Run the full suite (`uv run python -m pytest -q -m "not live"`) once before finishing.

## File Structure

**New files:**
- `src/aegis/commands/expand.py` — pure async template expansion (`expand`, `ExpandError`).
- `src/aegis/commands/prompt_loader.py` — `.aegis/commands/*.md` → registered `SlashCommand`s (`load_prompt_commands`).
- `src/aegis/commands/decorator.py` — the `@command` plugin decorator.
- `examples/hello_command.py` — a shipped `@command` example (droppable into `.aegis/plugins/`).
- Tests: `tests/test_command_expand.py`, `tests/test_prompt_commands.py`, `tests/test_command_decorator.py`.

**Modified files:**
- `src/aegis/commands/__init__.py` — `register()` precedence; `Completion.source`; `complete()` fills `source`; re-export `command`.
- `src/aegis/tui/pane.py` — deliver-effect case in `on_growing_input_submitted`.
- `src/aegis/web/wssession.py` — deliver-effect case in `_deliver_or_command`; `source` in `_complete`.
- `src/aegis/cli.py` — call `load_prompt_commands` in the `serve` command path.
- `src/aegis/tui/app.py` — call `load_prompt_commands` in `on_mount` (local path).
- `src/aegis/tui/palette.py` — tint label by `source`.
- `src/aegis/web/static/js/app.js` — tint palette row by `source`.
- `src/aegis/web/static/css/*` — palette source-tint classes.
- Test additions: `tests/test_command_registry.py`, `tests/test_pane_slash_command.py`, `tests/test_web_slash.py`, `tests/test_command_complete.py`, `tests/test_plugin_import.py`.

---

## Grounded reference (verified against `main`)

Copy these signatures into your mental model; they are real on `main` as of this plan.

```python
# src/aegis/commands/__init__.py
@dataclass(frozen=True)
class CommandResult:
    ok: bool; title: str; body: str = ""; effect: dict | None = None
@dataclass
class CommandContext:
    bridge: object; handle: str
class CommandCollision(ValueError): ...
@dataclass(frozen=True)
class SlashCommand:
    name: str; summary: str; usage: str; run: Handler
    source: str = "builtin"; spec: ArgSpec = field(default_factory=ArgSpec)
REGISTRY: dict[str, SlashCommand] = {}
def register(cmd: SlashCommand) -> None: ...          # current: builtin-only guard
async def dispatch(text, ctx) -> CommandResult: ...
def classify_input(text) -> tuple[str, str]: ...       # ("command"|"message", payload)
@dataclass(frozen=True)
class Completion:
    insert: str; label: str; detail: str = ""          # ← add: source: str = "builtin"
@dataclass(frozen=True)
class Completions:
    items: tuple[Completion, ...] = (); hint: str = ""
def complete(text: str, bridge: object) -> Completions: ...

# src/aegis/commands/args.py
@dataclass(frozen=True)
class Arg: name: str; required: bool = True; greedy: bool = False; completer=None
@dataclass(frozen=True)
class ArgSpec: positionals: tuple[Arg, ...] = (); flags: tuple[Flag, ...] = ()
@dataclass(frozen=True)
class Args:
    positional: dict; flags: dict
    def __getitem__(self, key): ...   # positional first, then flags
    def get(self, key, default=None): ...
class ArgError(ValueError): ...

# src/aegis/tui/shell_escape.py  (NO Textual imports — safe to import anywhere)
async def run_shell_escape(command: str, cwd: Path, timeout: float = 60.0) -> str
#   returns a formatted block: "$ <command>\n<combined stdout+stderr>\n[exited N]"

# builtin registration pattern (src/aegis/commands/builtins/core.py:247)
register(SlashCommand("help", "list slash commands", "/help", _help))
register(SlashCommand("spawn", "start a new top-level agent", "/spawn <agent> [prompt]",
    _spawn, spec=ArgSpec(positionals=(Arg("agent", completer=_agent_choices),
                                      Arg("prompt", required=False, greedy=True)))))

# TUI seam: src/aegis/tui/pane.py:879-899 (the `elif text.startswith("/")` branch)
#   after dispatch(): mounts render_command_block, then `if result.effect: self._apply_command_effect(...)`
# web seam: src/aegis/web/wssession.py:170-189 (_deliver_or_command)
#   command → {"command_result": {...}}; else → core.deliver + {"delivery", "depth"}
# web complete: src/aegis/web/wssession.py:191-197 (_complete) — items carry insert/label/detail
```

---

## Slice 1 — `register()` precedence + `expand()` (pure, no UI)

### Task 1: Source-precedence in `register()`

**Files:**
- Modify: `src/aegis/commands/__init__.py` (the `register` function, lines ~56-65)
- Test: `tests/test_command_registry.py` (add cases)

**Interfaces:**
- Consumes: `SlashCommand`, `CommandCollision`, `REGISTRY` (existing).
- Produces: `register(cmd)` with full precedence rule (builtin>user>plugin, order-independent, idempotent same-location replace).

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_command_registry.py`:

```python
import pytest
from aegis.commands import (
    REGISTRY, SlashCommand, CommandCollision, register)
from aegis.commands.args import ArgSpec


async def _noop(ctx, args):
    from aegis.commands import CommandResult
    return CommandResult(True, "ok")


def _cmd(name, source):
    return SlashCommand(name, "s", f"/{name}", _noop, source=source)


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(saved)


def test_user_replaces_plugin_regardless_of_order():
    register(_cmd("dup", "plugin"))
    register(_cmd("dup", "user"))            # higher priority replaces
    assert REGISTRY["dup"].source == "user"


def test_plugin_cannot_shadow_user():
    register(_cmd("dup", "user"))
    with pytest.raises(CommandCollision):
        register(_cmd("dup", "plugin"))
    assert REGISTRY["dup"].source == "user"


def test_non_builtin_cannot_shadow_builtin():
    register(_cmd("bi", "builtin"))
    with pytest.raises(CommandCollision):
        register(_cmd("bi", "user"))


def test_same_source_second_raises():
    register(_cmd("dup", "user"))
    with pytest.raises(CommandCollision):
        register(_cmd("dup", "user"))


def test_same_object_reregistration_is_idempotent():
    c = _cmd("dup", "user")
    register(c)
    register(c)                              # same command object → no raise
    assert REGISTRY["dup"] is c
```

- [ ] **Step 2: Run to verify they fail.**

Run: `uv run python -m pytest tests/test_command_registry.py -k "replaces or shadow or same_source or reregistration" -v`
Expected: FAIL (current `register` only guards builtins; `test_user_replaces_plugin` and `test_same_source_second_raises` fail).

- [ ] **Step 3: Rewrite `register()`.** Replace the function body in `src/aegis/commands/__init__.py`:

```python
_SOURCE_RANK = {"builtin": 0, "user": 1, "plugin": 2}


def register(cmd: SlashCommand) -> None:
    """Add a command, honoring source precedence: builtin > user > plugin.
    A higher-priority source replaces a lower one regardless of load order; a
    lower-or-equal-priority source shadowing an existing command raises
    CommandCollision — except idempotent re-registration of the very same
    command object (a reloaded plugin module re-running its decorator)."""
    existing = REGISTRY.get(cmd.name)
    if existing is None or existing is cmd:
        REGISTRY[cmd.name] = cmd
        return
    new_rank = _SOURCE_RANK.get(cmd.source, 99)
    old_rank = _SOURCE_RANK.get(existing.source, 99)
    if new_rank < old_rank:
        REGISTRY[cmd.name] = cmd            # strictly higher priority wins
        return
    raise CommandCollision(
        f"/{cmd.name} is already registered by a {existing.source} command "
        f"and cannot be overridden by a {cmd.source} command")
```

Note: same-*source-location* idempotency for prompt/plugin loaders is handled at
the loader layer (Tasks 3, 7) which re-register the *same object* or catch the
collision — `register` itself only fast-paths the identical-object case.

- [ ] **Step 4: Run to verify pass.**

Run: `uv run python -m pytest tests/test_command_registry.py -v`
Expected: PASS (all, including pre-existing 2A cases).

- [ ] **Step 5: Gate + commit.**

```bash
uv run ruff check src/aegis/commands/__init__.py tests/test_command_registry.py
git add src/aegis/commands/__init__.py tests/test_command_registry.py
git commit -m "feat(commands): source-precedence in register() for 2C (builtin>user>plugin)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 2: Template expansion — `expand()`

**Files:**
- Create: `src/aegis/commands/expand.py`
- Test: `tests/test_command_expand.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure). `run_shell` is injected as `Callable[[str, Path], Awaitable[str]]`.
- Produces:
  - `class ExpandError(ValueError)`
  - `async def expand(template: str, argstr: str, root: Path, run_shell) -> str`
  - `_split_args(argstr) -> list[str]` (shlex tokens; helper, used by prompt loader too via import).

- [ ] **Step 1: Write the failing tests.** Create `tests/test_command_expand.py`:

```python
import asyncio
from pathlib import Path

import pytest

from aegis.commands.expand import expand, ExpandError


async def _fake_shell(cmd, cwd):
    return f"[ran: {cmd}]"


def _run(coro):
    return asyncio.run(coro)


def test_positional_and_arguments_substitution(tmp_path):
    out = _run(expand("hi $1 and $2 — all: $ARGUMENTS",
                      "alpha beta", tmp_path, _fake_shell))
    assert out == "hi alpha and beta — all: alpha beta"


def test_missing_positional_is_empty(tmp_path):
    out = _run(expand("[$1][$2]", "only", tmp_path, _fake_shell))
    assert out == "[only][]"


def test_arguments_is_raw_verbatim(tmp_path):
    out = _run(expand("$ARGUMENTS", 'a "b c" d', tmp_path, _fake_shell))
    assert out == 'a "b c" d'          # raw, quotes preserved
    # but $1..$3 shlex-split:
    out2 = _run(expand("$1|$2|$3", 'a "b c" d', tmp_path, _fake_shell))
    assert out2 == "a|b c|d"


def test_file_include(tmp_path):
    (tmp_path / "note.md").write_text("FILE BODY", encoding="utf-8")
    out = _run(expand("before @note.md after", "", tmp_path, _fake_shell))
    assert out == "before FILE BODY after"


def test_missing_file_raises(tmp_path):
    with pytest.raises(ExpandError):
        _run(expand("@nope.md", "", tmp_path, _fake_shell))


def test_shell_embed(tmp_path):
    out = _run(expand("log:\n!`git log`", "", tmp_path, _fake_shell))
    assert out == "log:\n[ran: git log]"


def test_args_first_reach_shell(tmp_path):
    out = _run(expand("!`echo $1`", "hello", tmp_path, _fake_shell))
    assert out == "[ran: echo hello]"   # $1 substituted before the runner sees it
```

- [ ] **Step 2: Run to verify they fail.**

Run: `uv run python -m pytest tests/test_command_expand.py -v`
Expected: FAIL with `ModuleNotFoundError: aegis.commands.expand`.

- [ ] **Step 3: Implement `expand()`.** Create `src/aegis/commands/expand.py`:

```python
"""Prompt-command template expansion (Claude-Code parity).

Order (args first, so `!`git log $1`` works):
  1. $ARGUMENTS → raw stripped argstr; $1..$9 → shlex-split tokens (missing → "").
  2. @<path>   → splice file contents (resolved under `root`); missing → ExpandError.
  3. !`cmd`    → run via the injected async run_shell(cmd, root); inline its output.

`.aegis/commands/*.md` is trusted local config: @file reads and !`cmd` execute
on expansion. Arg values are substituted before the include/shell scan, so they
can influence includes/shell — accepted inside the trust boundary.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

_FILE_RE = re.compile(r"(?<!\S)@(\S+)")
_SHELL_RE = re.compile(r"!`([^`]*)`")


class ExpandError(ValueError):
    """Human-facing expansion failure (missing @file, etc.)."""


def _split_args(argstr: str) -> list[str]:
    try:
        return shlex.split(argstr)
    except ValueError:
        return argstr.split()


def _sub_args(template: str, argstr: str) -> str:
    raw = argstr.strip()
    toks = _split_args(argstr)
    out = template.replace("$ARGUMENTS", raw)
    for i in range(9, 0, -1):                 # $9..$1 so $1 doesn't eat $12
        val = toks[i - 1] if i - 1 < len(toks) else ""
        out = out.replace(f"${i}", val)
    return out


def _sub_files(text: str, root: Path) -> str:
    def repl(m: re.Match) -> str:
        rel = m.group(1)
        path = (root / rel)
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            raise ExpandError(f"@{rel}: cannot read include ({e.__class__.__name__})")
    return _FILE_RE.sub(repl, text)


async def _sub_shell(text: str, root: Path, run_shell) -> str:
    out: list[str] = []
    last = 0
    for m in _SHELL_RE.finditer(text):
        out.append(text[last:m.start()])
        out.append(await run_shell(m.group(1), root))
        last = m.end()
    out.append(text[last:])
    return "".join(out)


async def expand(template: str, argstr: str, root: Path, run_shell) -> str:
    text = _sub_args(template, argstr)
    text = _sub_files(text, root)
    text = await _sub_shell(text, root, run_shell)
    return text
```

- [ ] **Step 4: Run to verify pass.**

Run: `uv run python -m pytest tests/test_command_expand.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Gate + commit.**

```bash
uv run ruff check src/aegis/commands/expand.py tests/test_command_expand.py
git add src/aegis/commands/expand.py tests/test_command_expand.py
git commit -m "feat(commands): prompt-template expand() ($args/@file/!shell)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Slice 2 — Prompt loader + boot wiring + deliver-effect seam

### Task 3: Prompt-command loader

**Files:**
- Create: `src/aegis/commands/prompt_loader.py`
- Test: `tests/test_prompt_commands.py`

**Interfaces:**
- Consumes: `expand` (Task 2), `register`/`SlashCommand`/`CommandResult`/`CommandCollision` (`aegis.commands`), `Arg`/`ArgSpec` (`aegis.commands.args`), `run_shell_escape`.
- Produces: `def load_prompt_commands(root: Path, run_shell=None) -> list[str]` — scans `<root>/.aegis/commands/*.md`, registers `source="user"` commands, returns registered names. Re-callable (same file re-registers the same fresh command via caught collision → replace).

- [ ] **Step 1: Write the failing tests.** Create `tests/test_prompt_commands.py`:

```python
import asyncio
from pathlib import Path

from aegis.commands import REGISTRY, CommandContext
from aegis.commands.prompt_loader import load_prompt_commands


async def _fake_shell(cmd, cwd):
    return f"[ran: {cmd}]"


def _mk(root: Path, name: str, text: str):
    d = root / ".aegis" / "commands"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(text, encoding="utf-8")


def _clear(names):
    for n in names:
        REGISTRY.pop(n, None)


def test_absent_dir_is_noop(tmp_path):
    assert load_prompt_commands(tmp_path, run_shell=_fake_shell) == []


def test_loads_frontmatter_and_registers(tmp_path):
    _mk(tmp_path, "greet",
        "---\ndescription: say hi\nargument-hint: <name>\n---\nHello $1!")
    names = load_prompt_commands(tmp_path, run_shell=_fake_shell)
    try:
        assert "greet" in names
        cmd = REGISTRY["greet"]
        assert cmd.source == "user"
        assert cmd.summary == "say hi"
        assert cmd.usage == "/greet <name>"
        assert cmd.spec.positionals[0].greedy is True
    finally:
        _clear(names)


def test_run_returns_deliver_effect(tmp_path):
    _mk(tmp_path, "greet", "---\ndescription: hi\n---\nHello $1!")
    names = load_prompt_commands(tmp_path, run_shell=_fake_shell)
    try:
        cmd = REGISTRY["greet"]
        from aegis.commands.args import parse
        args = parse(cmd.spec, "World")
        res = asyncio.run(cmd.run(CommandContext(bridge=None, handle="h"), args))
        assert res.ok is True
        assert res.effect == {"kind": "deliver", "text": "Hello World!"}
    finally:
        _clear(names)


def test_bad_include_returns_error_result(tmp_path):
    _mk(tmp_path, "bad", "---\ndescription: x\n---\n@missing.md")
    names = load_prompt_commands(tmp_path, run_shell=_fake_shell)
    try:
        cmd = REGISTRY["bad"]
        from aegis.commands.args import parse
        res = asyncio.run(cmd.run(CommandContext(bridge=None, handle="h"),
                                  parse(cmd.spec, "")))
        assert res.ok is False
        assert res.effect is None
    finally:
        _clear(names)


def test_reload_is_idempotent(tmp_path):
    _mk(tmp_path, "greet", "---\ndescription: hi\n---\nHello")
    a = load_prompt_commands(tmp_path, run_shell=_fake_shell)
    b = load_prompt_commands(tmp_path, run_shell=_fake_shell)   # no raise
    try:
        assert "greet" in a and "greet" in b
    finally:
        _clear(a)
```

- [ ] **Step 2: Run to verify they fail.**

Run: `uv run python -m pytest tests/test_prompt_commands.py -v`
Expected: FAIL with `ModuleNotFoundError: aegis.commands.prompt_loader`.

- [ ] **Step 3: Implement the loader.** Create `src/aegis/commands/prompt_loader.py`:

```python
"""Load user-authored prompt commands from `<root>/.aegis/commands/*.md`.

Each file becomes a `source="user"` SlashCommand whose handler expands the body
template (see `expand`) and returns a `deliver` effect so the seam sends the
expansion to the agent as a normal message. Frontmatter: `description` → summary,
`argument-hint` → usage suffix. Boot-load only; re-callable (idempotent).
"""
from __future__ import annotations

import logging
from pathlib import Path

from ruamel.yaml import YAML

from aegis.commands import (
    REGISTRY, CommandCollision, CommandResult, SlashCommand, register)
from aegis.commands.args import Arg, ArgSpec
from aegis.commands.expand import ExpandError, expand

logger = logging.getLogger(__name__)
_yaml = YAML(typ="safe")

_GREEDY_SPEC = ArgSpec(
    positionals=(Arg("arguments", required=False, greedy=True),))


def _split_frontmatter(raw: str) -> "tuple[dict, str]":
    """Return (frontmatter dict, body). A leading `---` fence delimits YAML."""
    if raw.startswith("---"):
        parts = raw.split("\n", 1)
        rest = parts[1] if len(parts) > 1 else ""
        end = rest.find("\n---")
        if end != -1:
            head = rest[:end]
            body = rest[end + 4:]
            if body.startswith("\n"):
                body = body[1:]
            meta = _yaml.load(head) or {}
            return (meta if isinstance(meta, dict) else {}), body
    return {}, raw


def _make_command(name: str, meta: dict, template: str, root: Path,
                  run_shell) -> SlashCommand:
    summary = str(meta.get("description", "") or "")
    hint = meta.get("argument-hint")
    usage = f"/{name} {hint}" if hint else f"/{name}"

    async def _run(ctx, args) -> CommandResult:
        argstr = args.get("arguments", "") or ""
        try:
            text = await expand(template, argstr, root, run_shell)
        except ExpandError as e:
            return CommandResult(False, f"/{name} failed", str(e))
        return CommandResult(True, f"/{name}",
                             effect={"kind": "deliver", "text": text})

    return SlashCommand(name, summary, usage, _run,
                        source="user", spec=_GREEDY_SPEC)


def load_prompt_commands(root: Path, run_shell=None) -> list[str]:
    if run_shell is None:
        from aegis.tui.shell_escape import run_shell_escape
        run_shell = run_shell_escape
    folder = Path(root) / ".aegis" / "commands"
    if not folder.is_dir():
        return []
    loaded: list[str] = []
    for path in sorted(folder.glob("*.md")):
        name = path.stem.lower()
        try:
            meta, body = _split_frontmatter(path.read_text(encoding="utf-8"))
        except OSError as e:
            logger.warning("prompt command %s unreadable: %s", path, e)
            continue
        cmd = _make_command(name, meta, body, Path(root), run_shell)
        # Idempotent reload: a user command replacing an existing user command
        # of the same name is the same file reloading — drop the old, register.
        existing = REGISTRY.get(name)
        if existing is not None and existing.source == "user":
            REGISTRY.pop(name, None)
        try:
            register(cmd)
            loaded.append(name)
        except CommandCollision as e:
            logger.warning("prompt command /%s skipped: %s", name, e)
    return loaded
```

- [ ] **Step 4: Run to verify pass.**

Run: `uv run python -m pytest tests/test_prompt_commands.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Gate + commit.**

```bash
uv run ruff check src/aegis/commands/prompt_loader.py tests/test_prompt_commands.py
git add src/aegis/commands/prompt_loader.py tests/test_prompt_commands.py
git commit -m "feat(commands): load .aegis/commands/*.md prompt commands (source=user)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 4: TUI deliver-effect seam

**Files:**
- Modify: `src/aegis/tui/pane.py` (the `elif text.startswith("/")` branch, ~879-899)
- Test: `tests/test_pane_slash_command.py` (add a case)

**Interfaces:**
- Consumes: `dispatch`, `CommandContext`, `classify_input` (existing imports in the branch); `load_prompt_commands` for the test fixture.
- Produces: when `dispatch()` returns a `deliver` effect, the pane mounts a *user line* + `core.deliver`s the expanded text (a turn), and does NOT mount a command block.

- [ ] **Step 1: Write the failing test.** Add to `tests/test_pane_slash_command.py` (follow the file's existing `run_test`/pane harness — reuse its helpers for building a pane with a fake core). Sketch:

```python
async def test_prompt_command_delivers_to_agent(monkeypatch, tmp_path):
    # Register a temp prompt command that expands to a fixed message.
    from aegis.commands.prompt_loader import load_prompt_commands
    d = tmp_path / ".aegis" / "commands"; d.mkdir(parents=True)
    (d / "poem.md").write_text("---\ndescription: p\n---\nWrite about $1",
                               encoding="utf-8")

    async def _fake_shell(cmd, cwd):  # unused here
        return ""
    names = load_prompt_commands(tmp_path, run_shell=_fake_shell)
    try:
        # ... build pane bound to a fake core that records deliver() calls
        #     (mirror the existing pane test harness in this file) ...
        # Type "/poem cats" and submit:
        await pane_type_and_submit(pane, "/poem cats")
        assert fake_core.delivered[-1].body == "Write about cats"
        assert not command_blocks_mounted(pane)   # no command-result block
    finally:
        for n in names:
            from aegis.commands import REGISTRY; REGISTRY.pop(n, None)
```

Match the actual harness already used by the neighboring tests in this file
(fake core exposing `deliver`, block-inspection helper). If the file has no
reusable helper, model the fake core on the one in `test_pane_slash_command.py`'s
existing `/sessions` test and assert on `self._core.deliver` invocation.

- [ ] **Step 2: Run to verify it fails.**

Run: `uv run python -m pytest tests/test_pane_slash_command.py -k prompt_command_delivers -v`
Expected: FAIL (currently a prompt command mounts a command block via the effect
being applied by `_apply_command_effect`, which ignores unknown `deliver` kind —
so `deliver` is never called and a block IS mounted).

- [ ] **Step 3: Implement the seam.** In `src/aegis/tui/pane.py`, edit the slash branch so a `deliver` effect routes to the normal message path. Replace the `if kind == "command":` body (lines ~887-898) with:

```python
            if kind == "command":
                width = self._transcript().size.width or 80
                result = await dispatch(
                    payload, CommandContext(bridge=self.app,
                                            handle=self.handle))
                eff = result.effect or {}
                if eff.get("kind") == "deliver":
                    # Prompt command: its expansion is delivered to the agent
                    # as a normal user message (rendered as a user line by
                    # _on_core_dispatch), not a command-result block.
                    text = eff["text"]
                    # fall through to the normal deliver path below
                else:
                    self._flush_streaming()
                    self._mount_block(
                        render_command_block(result, self._palette, width),
                        f"{result.title}\n{result.body}".strip())
                    if result.effect:
                        self._apply_command_effect(result.effect)
                    return
            else:
                text = payload   # "//foo" → deliver "/foo" as a normal message
```

(The existing `text = payload` line for the `//` case moves into the `else`
above so both non-command slash paths set `text` and fall through to the shared
`InboxMessage` + `core.deliver` code that already follows.)

- [ ] **Step 4: Run to verify pass.**

Run: `uv run python -m pytest tests/test_pane_slash_command.py -v`
Expected: PASS (new test + all existing pane slash tests). If a TUI test flakes,
re-run it alone before believing it.

- [ ] **Step 5: Gate + commit.**

```bash
uv run ruff check src/aegis/tui/pane.py tests/test_pane_slash_command.py
git add src/aegis/tui/pane.py tests/test_pane_slash_command.py
git commit -m "feat(tui): route prompt-command deliver-effect to the agent

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 5: Web deliver-effect seam

**Files:**
- Modify: `src/aegis/web/wssession.py` (`_deliver_or_command`, ~170-189)
- Test: `tests/test_web_slash.py` (add a case)

**Interfaces:**
- Consumes: `dispatch`, `classify_input`, `CommandContext`, `InboxMessage`/`sender_user`/`now_iso` (existing in this module).
- Produces: a prompt command through the `deliver` RPC calls `core.deliver` with the expanded text and returns a `{"delivery","depth"}` frame (NOT `command_result`); control commands still return `command_result`.

- [ ] **Step 1: Write the failing test.** Add to `tests/test_web_slash.py` (mirror the existing `_deliver_or_command` unit tests — they build a `WsSession` with a fake manager/core). Sketch:

```python
async def test_prompt_command_delivers_not_command_result(tmp_path, monkeypatch):
    from aegis.commands.prompt_loader import load_prompt_commands
    d = tmp_path / ".aegis" / "commands"; d.mkdir(parents=True)
    (d / "hi.md").write_text("---\ndescription: h\n---\nHi $1", encoding="utf-8")

    async def _fake_shell(cmd, cwd):
        return ""
    names = load_prompt_commands(tmp_path, run_shell=_fake_shell)
    try:
        sess, fake_core = make_ws_session_with_fake_core()   # per this file's harness
        res = await sess._deliver_or_command("h1", "/hi there")
        assert "command_result" not in res
        assert res["delivery"] == fake_core.receipt.disposition
        assert fake_core.delivered[-1].body == "Hi there"
    finally:
        for n in names:
            from aegis.commands import REGISTRY; REGISTRY.pop(n, None)


async def test_control_command_still_returns_command_result():
    sess, _ = make_ws_session_with_fake_core()
    res = await sess._deliver_or_command("h1", "/help")
    assert "command_result" in res
```

Reuse the fake-manager/fake-core builder already present in `test_web_slash.py`.

- [ ] **Step 2: Run to verify it fails.**

Run: `uv run python -m pytest tests/test_web_slash.py -k "prompt_command_delivers" -v`
Expected: FAIL (currently `/hi` returns a `command_result` frame carrying the
`deliver` effect; `core.deliver` is never called).

- [ ] **Step 3: Implement the seam.** In `src/aegis/web/wssession.py`, edit `_deliver_or_command`:

```python
    async def _deliver_or_command(self, handle: str, message: str) -> dict:
        from aegis.commands import CommandContext, classify_input, dispatch
        kind, payload = classify_input(message)
        core = self._m.get(handle)
        if kind == "command":
            result = await dispatch(
                payload, CommandContext(bridge=self._m, handle=handle))
            eff = result.effect or {}
            if eff.get("kind") == "deliver":
                # Prompt command: deliver the expansion to the agent like a
                # normal message (renders via the inbox stream frame).
                if core is None:
                    raise ValueError("unknown handle")
                msg = InboxMessage(sender=sender_user(), timestamp=now_iso(),
                                   body=eff["text"])
                receipt = await core.deliver(msg)
                return {"delivery": receipt.disposition, "depth": receipt.depth}
            return {"command_result": {
                "ok": result.ok, "title": result.title,
                "body": result.body, "effect": result.effect}}
        if core is None:
            raise ValueError("unknown handle")
        msg = InboxMessage(sender=sender_user(), timestamp=now_iso(),
                           body=payload)
        receipt = await core.deliver(msg)
        return {"delivery": receipt.disposition, "depth": receipt.depth}
```

- [ ] **Step 4: Run to verify pass.**

Run: `uv run python -m pytest tests/test_web_slash.py -v`
Expected: PASS.

- [ ] **Step 5: Gate + commit.**

```bash
uv run ruff check src/aegis/web/wssession.py tests/test_web_slash.py
git add src/aegis/web/wssession.py tests/test_web_slash.py
git commit -m "feat(web): route prompt-command deliver-effect to the agent

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 6: Boot wiring (TUI + serve)

**Files:**
- Modify: `src/aegis/tui/app.py` (`on_mount`, after `await self._mcp.start()`, in the non-remote path, ~356)
- Modify: `src/aegis/cli.py` (`serve` command, after `import_plugins(yaml_cfg)`, ~524)
- Test: `tests/test_prompt_commands.py` (add a boot-integration test for the TUI path)

**Interfaces:**
- Consumes: `load_prompt_commands` (Task 3), `self.state_root` (AegisApp, used by `reload_plugins`), `root` (cli serve).
- Produces: `.aegis/commands/*.md` are registered at TUI boot and at `serve` boot.

- [ ] **Step 1: Write the failing test.** Add to `tests/test_prompt_commands.py` a test that drives `AegisApp.on_mount` via `run_test` against a temp project, asserting a `.md` command is in `REGISTRY` after boot. Reuse the app-boot harness from `tests/test_pane_slash_command.py` / any existing `AegisApp().run_test()` test. Sketch:

```python
async def test_tui_boot_loads_prompt_commands(tmp_path):
    d = tmp_path / ".aegis" / "commands"; d.mkdir(parents=True)
    (d / "boothi.md").write_text("---\ndescription: b\n---\nHi", encoding="utf-8")
    # Build an AegisApp rooted at tmp_path (state_root=tmp_path); run_test().
    # After mount:
    from aegis.commands import REGISTRY
    try:
        assert "boothi" in REGISTRY
    finally:
        REGISTRY.pop("boothi", None)
```

If wiring a full `AegisApp` boot in a test is heavy, instead assert the wiring by
calling the exact helper the app calls — factor the two-line load into a tiny
module function `_load_project_commands(root)` and unit-test THAT — but prefer the
real boot test if the harness exists in the suite.

- [ ] **Step 2: Run to verify it fails.**

Run: `uv run python -m pytest tests/test_prompt_commands.py -k tui_boot -v`
Expected: FAIL (nothing loads prompt commands at boot yet).

- [ ] **Step 3: Wire the two boot paths.**

In `src/aegis/tui/app.py` `on_mount`, right after `await self._mcp.start()` (line ~356, non-remote path):

```python
        from aegis.commands.prompt_loader import load_prompt_commands
        with contextlib.suppress(Exception):
            load_prompt_commands(self.state_root)
```

(`contextlib` is already imported in pane.py; confirm it is imported in app.py —
if not, `import contextlib` at the top. `self.state_root` is the project root, as
used by `reload_plugins`.)

In `src/aegis/cli.py` `serve` command, right after `import_plugins(yaml_cfg)` (line ~524):

```python
        from aegis.commands.prompt_loader import load_prompt_commands
        load_prompt_commands(root)
```

(`root` is already in scope in the serve command.)

- [ ] **Step 4: Run to verify pass.**

Run: `uv run python -m pytest tests/test_prompt_commands.py -v`
Expected: PASS. If the TUI boot test flakes, re-run alone.

- [ ] **Step 5: Gate + commit.**

```bash
uv run ruff check src/aegis/tui/app.py src/aegis/cli.py tests/test_prompt_commands.py
git add src/aegis/tui/app.py src/aegis/cli.py tests/test_prompt_commands.py
git commit -m "feat(commands): load prompt commands at TUI + serve boot

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Slice 3 — Plugin `@command` decorator

### Task 7: The `@command` decorator

**Files:**
- Create: `src/aegis/commands/decorator.py`
- Modify: `src/aegis/commands/__init__.py` (re-export `command`)
- Test: `tests/test_command_decorator.py`

**Interfaces:**
- Consumes: `register`, `SlashCommand`, `CommandCollision`, `CommandResult` (`aegis.commands`); `ArgSpec` (`aegis.commands.args`).
- Produces:
  - `def command(fn=None, *, name=None, summary=None, usage=None, spec=None)` — decorator; bare or kwargs form; builds `SlashCommand(source="plugin")` and `register`s it; returns the original function.
  - `from aegis.commands import command` works.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_command_decorator.py`:

```python
import asyncio
import pytest

from aegis.commands import REGISTRY, CommandResult, CommandCollision, command
from aegis.commands.args import Arg, ArgSpec


def _clear(*names):
    for n in names:
        REGISTRY.pop(n, None)


def test_bare_decorator_defaults():
    @command
    async def ping(ctx, args):
        "ping a thing"
        return CommandResult(True, "pong")
    try:
        c = REGISTRY["ping"]
        assert c.source == "plugin"
        assert c.summary == "ping a thing"
        assert c.usage == "/ping"
        res = asyncio.run(c.run(None, None))
        assert res.title == "pong"
    finally:
        _clear("ping")


def test_kwargs_form():
    @command(name="pp", summary="s", usage="/pp <x>",
             spec=ArgSpec(positionals=(Arg("x"),)))
    async def _h(ctx, args):
        return CommandResult(True, args["x"])
    try:
        c = REGISTRY["pp"]
        assert c.usage == "/pp <x>"
        assert c.spec.positionals[0].name == "x"
    finally:
        _clear("pp")


def test_collision_with_builtin_raises():
    with pytest.raises(CommandCollision):
        @command(name="help")
        async def _h(ctx, args):
            return CommandResult(True, "x")


def test_non_coroutine_rejected():
    with pytest.raises(TypeError):
        @command
        def _sync(ctx, args):        # not async
            return None


def test_wrong_signature_rejected():
    with pytest.raises(TypeError):
        @command
        async def _bad(only_one):    # must be (ctx, args)
            return None
```

- [ ] **Step 2: Run to verify they fail.**

Run: `uv run python -m pytest tests/test_command_decorator.py -v`
Expected: FAIL with `ImportError: cannot import name 'command'`.

- [ ] **Step 3: Implement the decorator.** Create `src/aegis/commands/decorator.py`:

```python
"""The @command plugin primitive — a control command contributed by a plugin.

Fourth decorator beside @workflow / @hook / @tool; auto-registered on the plugin
import sweep (aegis.config.yaml_loader.import_plugins). Registers a
`source="plugin"` SlashCommand, so register()'s precedence guard protects
builtins and user .md commands.
"""
from __future__ import annotations

import inspect

from aegis.commands import REGISTRY, CommandCollision, SlashCommand, register
from aegis.commands.args import ArgSpec


def _usage_from_spec(name: str, spec: ArgSpec) -> str:
    parts = []
    for p in spec.positionals:
        parts.append(f"<{p.name}>" if p.required else f"[{p.name}]")
    return f"/{name}" + ("" if not parts else " " + " ".join(parts))


def _make(fn, *, name, summary, usage, spec):
    if not inspect.iscoroutinefunction(fn):
        raise TypeError(f"@command on {fn.__name__}: must be async def")
    params = list(inspect.signature(fn).parameters.values())
    if len(params) < 2 or params[0].name != "ctx" or params[1].name != "args":
        raise TypeError(
            f"@command on {fn.__name__}: signature must be (ctx, args)")
    n = name or fn.__name__
    s = spec or ArgSpec()
    summ = summary if summary is not None else (
        (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else "")
    use = usage or _usage_from_spec(n, s)
    # Idempotent reload: same-location re-registration replaces cleanly.
    existing = REGISTRY.get(n)
    if (existing is not None and existing.source == "plugin"
            and getattr(existing.run, "__code__", None) is not None
            and existing.run.__code__.co_filename == fn.__code__.co_filename
            and existing.run.__code__.co_firstlineno == fn.__code__.co_firstlineno):
        REGISTRY.pop(n, None)
    register(SlashCommand(n, summ, use, fn, source="plugin", spec=s))
    return fn


def command(fn=None, *, name=None, summary=None, usage=None, spec=None):
    """Register a plugin control command.

        @command
        async def ping(ctx, args): ...

        @command(name="pp", summary="…", usage="/pp <x>", spec=ArgSpec(...))
        async def _h(ctx, args): ...
    """
    if fn is not None:
        return _make(fn, name=name, summary=summary, usage=usage, spec=spec)

    def deco(f):
        return _make(f, name=name, summary=summary, usage=usage, spec=spec)
    return deco
```

- [ ] **Step 4: Re-export from the package.** At the bottom of
`src/aegis/commands/__init__.py`, *above* the existing
`from aegis.commands import builtins as _builtins` line, add:

```python
from aegis.commands.decorator import command  # noqa: E402
```

(Placed after the type/`register` definitions so `decorator.py`'s
`from aegis.commands import ...` resolves. `command` is now importable as
`from aegis.commands import command`.)

- [ ] **Step 5: Run to verify pass.**

Run: `uv run python -m pytest tests/test_command_decorator.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Gate + commit.**

```bash
uv run ruff check src/aegis/commands/decorator.py src/aegis/commands/__init__.py tests/test_command_decorator.py
git add src/aegis/commands/decorator.py src/aegis/commands/__init__.py tests/test_command_decorator.py
git commit -m "feat(commands): @command plugin decorator (source=plugin)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 8: `@command` loads via the plugin import sweep + example

**Files:**
- Create: `examples/hello_command.py`
- Test: `tests/test_plugin_import.py` (add a case)

**Interfaces:**
- Consumes: `import_plugins` (`aegis.config.yaml_loader`), the `@command` decorator, an `AegisConfig` with a `plugin_dirs` entry.
- Produces: dropping a `@command` file under a `plugin_dirs` entry registers it into `REGISTRY` with `source="plugin"` on `import_plugins()`.

- [ ] **Step 1: Write the failing test.** Add to `tests/test_plugin_import.py` (mirror its existing pattern that writes a `*.py` under a temp plugin dir, builds a config with `plugin_dirs`, calls `import_plugins`, and asserts registration). Sketch:

```python
def test_command_plugin_registers(tmp_path):
    from aegis.config.yaml_loader import import_plugins
    from aegis.commands import REGISTRY
    pdir = tmp_path / ".aegis" / "plugins"; pdir.mkdir(parents=True)
    (pdir / "myc.py").write_text(
        "from aegis.commands import command, CommandResult\n"
        "@command\n"
        "async def zzhi(ctx, args):\n"
        "    'plugin hi'\n"
        "    return CommandResult(True, 'hi')\n", encoding="utf-8")
    cfg = _config_with_plugin_dir(pdir)     # per this file's existing helper
    try:
        import_plugins(cfg)
        assert "zzhi" in REGISTRY
        assert REGISTRY["zzhi"].source == "plugin"
    finally:
        REGISTRY.pop("zzhi", None)
```

Use the same config/plugin-dir construction the neighboring `test_plugin_import.py`
tests use (they already exercise `@workflow`/`@hook`/`@tool` this way).

- [ ] **Step 2: Run to verify it fails.**

Run: `uv run python -m pytest tests/test_plugin_import.py -k command_plugin -v`
Expected: FAIL (import path or assertion) — confirms the sweep didn't register it before the decorator existed / example present.

- [ ] **Step 3: Add the example.** Create `examples/hello_command.py`:

```python
"""Example plugin command. Drop this file into `.aegis/plugins/` (or any
`plugin_dirs:` entry) to register `/hello` as a plugin control command."""
from aegis.commands import command, CommandResult
from aegis.commands.args import Arg, ArgSpec


@command(summary="greet someone", usage="/hello <name>",
         spec=ArgSpec(positionals=(Arg("name", required=False),)))
async def hello(ctx, args):
    who = args.get("name") or "world"
    return CommandResult(True, f"hello {who}")
```

- [ ] **Step 4: Run to verify pass.**

Run: `uv run python -m pytest tests/test_plugin_import.py -v`
Expected: PASS.

- [ ] **Step 5: Gate + commit.**

```bash
uv run ruff check examples/hello_command.py tests/test_plugin_import.py
git add examples/hello_command.py tests/test_plugin_import.py
git commit -m "feat(commands): @command loads via plugin import sweep + example

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Slice 4 — Palette source-coloring

### Task 9: `Completion.source` + `complete()` fills it

**Files:**
- Modify: `src/aegis/commands/__init__.py` (`Completion` dataclass; the verb-in-progress branch of `complete()`)
- Test: `tests/test_command_complete.py` (add a case)

**Interfaces:**
- Consumes: `REGISTRY`, `fuzzy_rank`, `Completion` (existing).
- Produces: `Completion.source: str = "builtin"`; command-name completions carry the command's `source`.

- [ ] **Step 1: Write the failing test.** Add to `tests/test_command_complete.py`:

```python
def test_command_completions_carry_source():
    from aegis.commands import REGISTRY, SlashCommand, complete

    async def _n(ctx, args):
        from aegis.commands import CommandResult
        return CommandResult(True, "x")

    REGISTRY["zzuser"] = SlashCommand("zzuser", "s", "/zzuser", _n, source="user")
    REGISTRY["zzplug"] = SlashCommand("zzplug", "s", "/zzplug", _n, source="plugin")
    try:
        got = {c.label: c.source for c in complete("/zz", bridge=None).items}
        assert got["/zzuser"] == "user"
        assert got["/zzplug"] == "plugin"
        assert complete("/help", bridge=None)  # builtin still default source
    finally:
        REGISTRY.pop("zzuser", None); REGISTRY.pop("zzplug", None)
```

- [ ] **Step 2: Run to verify it fails.**

Run: `uv run python -m pytest tests/test_command_complete.py -k carry_source -v`
Expected: FAIL with `AttributeError: 'Completion' object has no attribute 'source'`.

- [ ] **Step 3: Implement.** In `src/aegis/commands/__init__.py`:

Add the field to `Completion`:

```python
@dataclass(frozen=True)
class Completion:
    insert: str
    label: str
    detail: str = ""
    source: str = "builtin"
```

In `complete()`, the verb-in-progress branch, set `source=c.source` in the emitted `Completion`:

```python
        items = tuple(
            Completion(insert=f"/{c.name} ", label=f"/{c.name}",
                       detail=c.summary, source=c.source)
            for c in ranked)
```

(Argument-value completions keep the default `source="builtin"` — they are not source-scoped.)

- [ ] **Step 4: Run to verify pass.**

Run: `uv run python -m pytest tests/test_command_complete.py -v`
Expected: PASS.

- [ ] **Step 5: Gate + commit.**

```bash
uv run ruff check src/aegis/commands/__init__.py tests/test_command_complete.py
git add src/aegis/commands/__init__.py tests/test_command_complete.py
git commit -m "feat(commands): tag palette Completion with source

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 10: Tint the palette by source (TUI + web)

**Files:**
- Modify: `src/aegis/tui/palette.py` (`update`, ~36-40)
- Modify: `src/aegis/web/wssession.py` (`_complete`, ~191-197 — include `source` in the item dict)
- Modify: `src/aegis/web/static/js/app.js` (`renderPalette`, ~893-912 — add a source class)
- Modify: `src/aegis/web/static/css/` (add `.pl-source-user` / `.pl-source-plugin` tints — find the file holding `.palette-row`/`.pl-label`)
- Test: `tests/test_web_complete.py` (assert `source` present in the item dict); TUI tint is asserted structurally (see step)

**Interfaces:**
- Consumes: `Completion.source` (Task 9); the theme `AegisColors` object on the TUI palette (`self._palette`).
- Produces: TUI palette row label styled by source; web `_complete` items include `source`; web rows carry a `pl-source-<source>` class.

- [ ] **Step 1 (web): Write the failing test.** Add to `tests/test_web_complete.py` (mirror its existing `_complete` unit test):

```python
async def test_complete_items_include_source():
    from aegis.commands import REGISTRY, SlashCommand
    async def _n(ctx, args):
        from aegis.commands import CommandResult
        return CommandResult(True, "x")
    REGISTRY["zzuser"] = SlashCommand("zzuser", "s", "/zzuser", _n, source="user")
    try:
        sess = make_ws_session()                 # per this file's harness
        res = await sess._complete("/zzuser")
        item = next(i for i in res["items"] if i["label"] == "/zzuser")
        assert item["source"] == "user"
    finally:
        REGISTRY.pop("zzuser", None)
```

- [ ] **Step 2: Run to verify it fails.**

Run: `uv run python -m pytest tests/test_web_complete.py -k include_source -v`
Expected: FAIL (`KeyError: 'source'`).

- [ ] **Step 3 (web python): add `source` to `_complete`.** In `src/aegis/web/wssession.py`:

```python
    async def _complete(self, message: str) -> dict:
        """Palette completions for a web input line (mirrors the TUI panel)."""
        from aegis.commands import complete
        c = complete(message, self._m)
        return {"items": [{"insert": it.insert, "label": it.label,
                           "detail": it.detail, "source": it.source}
                          for it in c.items],
                "hint": c.hint}
```

- [ ] **Step 4: Run web test to verify pass.**

Run: `uv run python -m pytest tests/test_web_complete.py -v`
Expected: PASS.

- [ ] **Step 5 (web js + css): tint the row.** In `src/aegis/web/static/js/app.js` `renderPalette`, add the source class to the row:

```javascript
    row.className = "palette-row" + (i === 0 ? " current" : "")
                    + " pl-source-" + (it.source || "builtin");
```

Find the CSS file with `.palette-row` (`grep -rl "palette-row" src/aegis/web/static/css`) and add:

```css
.pl-source-user   .pl-label { color: var(--success, #7ec87e); }
.pl-source-plugin .pl-label { color: var(--secondary, #b48ead); }
/* builtin: default label color (accent) — no override */
```

(Match the existing CSS variable names in that file; if it uses concrete hex
rather than CSS vars, use the theme's accent/success/secondary hexes already
present.)

- [ ] **Step 6 (TUI): tint the label by source.** In `src/aegis/tui/palette.py` `update`, choose the style per source:

```python
        rows = []
        _src_style = {
            "user": getattr(self._palette, "success", self._palette.accent),
            "plugin": getattr(self._palette, "secondary", self._palette.accent),
        }
        for c in self._items:
            style = _src_style.get(getattr(c, "source", "builtin"),
                                   self._palette.accent)
            t = Text(c.label, style=style)
            if c.detail:
                t.append(f"   {c.detail}", style=self._palette.muted)
            rows.append(Option(t))
```

(Confirm `AegisColors` has `success`/`secondary` roles — `grep -n "success\|secondary\|accent" src/aegis/tui/themes.py`. If a role is absent, fall back to `accent` via the `getattr` default already shown, and use whichever roles exist.)

- [ ] **Step 7: TUI palette test.** Add/extend a case in `tests/test_pane_palette.py` asserting the palette renders three rows for a registry containing one of each source (structural: the `CommandPalette.update` produces one `Option` per item without error, and `_items[i].source` round-trips). If asserting Rich style is awkward, assert the `source` reached the widget:

```python
async def test_palette_rows_have_source(...):
    # register zzuser (user) + zzplug (plugin), open palette on "/zz"
    palette.update(complete("/zz", bridge=fake))
    assert {c.source for c in palette._items} >= {"user", "plugin"}
```

- [ ] **Step 8: Run tests to verify pass.**

Run: `uv run python -m pytest tests/test_web_complete.py tests/test_pane_palette.py -v`
Expected: PASS (re-run any flaky TUI test alone).

- [ ] **Step 9: Gate + commit.**

```bash
uv run ruff check src/aegis/tui/palette.py src/aegis/web/wssession.py tests/test_web_complete.py tests/test_pane_palette.py
git add src/aegis/tui/palette.py src/aegis/web/wssession.py src/aegis/web/static/js/app.js src/aegis/web/static/css tests/test_web_complete.py tests/test_pane_palette.py
git commit -m "feat(palette): color-code command completions by source

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Finalization

### Task 11: TASKS.md + full-suite gate + browser smoke

**Files:**
- Modify: `TASKS.md` (mark 2C `[x]`)

- [ ] **Step 1: Full hermetic suite.**

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS (~1745+ passing; at most 1 inotify TUI flake — re-run it alone to confirm green).

- [ ] **Step 2: Browser smoke (web palette + prompt command).** With a temp project holding a `.aegis/commands/hi.md`, run `aegis serve`, open the web UI, type `/` and confirm the drop-up color-codes builtin vs user vs plugin, and that invoking `/hi …` delivers the expansion to the agent (renders as a user/inbox line, no command-result block). Note the result in the PR/commit message. (No JS unit harness — this is the verification step per 2D's precedent.)

- [ ] **Step 3: Mark the task done.** In `TASKS.md`, change the 2C line from `[ ]` to `[x]` with a one-line "shipped" note mirroring the 2A/2B/2D entries, citing this plan and the 2C spec.

- [ ] **Step 4: Commit.**

```bash
uv run ruff check TASKS.md 2>/dev/null || true
git add TASKS.md
git commit -m "docs(tasks): mark slash-commands 2C shipped

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 5: Push.**

```bash
git push origin main
```

---

## Self-review (author's check against the spec)

- **Spec §"key design fork" (effect-based deliver):** Tasks 3 (deliver effect emitted), 4 (TUI seam), 5 (web seam). ✓
- **Spec §1 (register precedence):** Task 1. ✓
- **Spec §2 (expand: $args/@file/!shell, args-first):** Task 2. ✓
- **Spec §3 (prompt loader, greedy spec, frontmatter→summary/usage):** Task 3. ✓
- **Spec §4 (@command decorator, defaults, collision):** Task 7; sweep + example Task 8. ✓
- **Spec §5 (boot wiring, no live watch):** Task 6 (TUI on_mount + serve). ✓
- **Spec §6 (deliver-effect seam, both frontends):** Tasks 4, 5. ✓
- **Spec §7 (palette source-coloring):** Tasks 9, 10. ✓
- **Spec §Security (trust boundary):** documented in `expand.py` docstring (Task 2) and spec; no code gate (by design). ✓
- **Type consistency:** `load_prompt_commands(root, run_shell=None)`, `expand(template, argstr, root, run_shell)`, `command(fn=None, *, name, summary, usage, spec)`, `Completion(..., source="builtin")` — used consistently across tasks. ✓
- **Test-isolation constraint:** every filesystem test uses `tmp_path` (outside Workspace). ✓
- **Known soft spots for the executor:** the TUI/web test *harness* helpers (fake core, block inspection, WsSession builder) are referenced by shape, not verbatim — the executor must read the neighboring tests in each file and reuse their real fixtures. Flagged in Tasks 4, 5, 6, 10.
