# Slash Commands 2A — Parser + Resolution Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give aegis slash commands a shared, declarative typed-argument layer, a protected-builtin resolution model, `//` escaping, `/queue new` persistence, and full web-input parity.

**Architecture:** A new pure `args.py` parses a declarative `ArgSpec` into an `Args` object; the registry gains a `source` tag and a collision guard so builtins can't be overridden; `dispatch()` parses before calling handlers, which now receive `Args`. A pure `classify_input()` helper centralises the `/` vs `//` vs message decision, wired into both the TUI pane seam and the web `deliver` RPC so the slash surface works identically in both frontends.

**Tech Stack:** Python 3.13+, `dataclasses`, `shlex` (stdlib), pytest (`-m "not live"`), Textual (TUI seam only), vanilla JS (web client).

## Global Constraints

- Python **3.13+**.
- Package manager is **`uv`** — `uv run pytest`, `uv pip install -e .`. Never bare `pip`.
- Test selector is **`-m "not live"`** (marker), never `-k "not live"` (substring bug).
- TDD: failing test first, minimal implementation, commit per logical unit.
- The commands core (`src/aegis/commands/`) stays **harness-agnostic** — no Textual/web imports — so both frontends reuse it verbatim.
- Builtins are **immutable/protected** in 2A: no override path.
- Fast hermetic gate for iteration: `uv run python -m pytest tests/test_command_args.py tests/test_slash_commands.py tests/test_command_registry.py -q` (add web/pane tests as they land). Run as its own step; never pipe through `tail`.

---

### Task 1: Arg parser (`args.py`) — pure

**Files:**
- Create: `src/aegis/commands/args.py`
- Test: `tests/test_command_args.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces: `Arg(name, required=True, greedy=False)`, `Flag(name, takes_value=True, default=None)`, `ArgSpec(positionals=(), flags=())`, `Args(positional: dict, flags: dict)` with `.get(k, default)` and `__getitem__`, `ArgError(ValueError)`, and `parse(spec: ArgSpec, argstr: str) -> Args`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_command_args.py
import pytest
from aegis.commands.args import Arg, Flag, ArgSpec, parse, ArgError


def test_required_positional_binds():
    args = parse(ArgSpec(positionals=(Arg("name"),)), "reviewers")
    assert args["name"] == "reviewers"


def test_missing_required_raises():
    with pytest.raises(ArgError):
        parse(ArgSpec(positionals=(Arg("name"),)), "")


def test_optional_positional_absent():
    spec = ArgSpec(positionals=(Arg("name"), Arg("agent", required=False)))
    args = parse(spec, "reviewers")
    assert args["name"] == "reviewers"
    assert args.get("agent") is None


def test_greedy_takes_raw_verbatim_remainder():
    spec = ArgSpec(positionals=(Arg("agent"),
                                Arg("prompt", required=False, greedy=True)))
    args = parse(spec, 'researcher write a poem "keep quotes"')
    assert args["agent"] == "researcher"
    assert args["prompt"] == 'write a poem "keep quotes"'


def test_quoting_on_nongreedy_token():
    args = parse(ArgSpec(positionals=(Arg("name"),)), '"two words"')
    assert args["name"] == "two words"


def test_leading_valued_flag_space_form():
    spec = ArgSpec(positionals=(Arg("agent"),), flags=(Flag("effort"),))
    args = parse(spec, "--effort high researcher")
    assert args.flags["effort"] == "high"
    assert args["agent"] == "researcher"


def test_leading_valued_flag_equals_form():
    spec = ArgSpec(positionals=(Arg("agent"),), flags=(Flag("effort"),))
    args = parse(spec, "--effort=high researcher")
    assert args.flags["effort"] == "high"


def test_boolean_flag_presence_and_default():
    spec = ArgSpec(positionals=(Arg("name"),),
                   flags=(Flag("ephemeral", takes_value=False),))
    assert parse(spec, "--ephemeral q1").flags["ephemeral"] is True
    assert parse(spec, "q1").flags["ephemeral"] is False


def test_unknown_flag_raises():
    with pytest.raises(ArgError):
        parse(ArgSpec(positionals=(Arg("name"),)), "--bogus q1")


def test_valued_flag_missing_value_raises():
    spec = ArgSpec(positionals=(Arg("agent"),), flags=(Flag("effort"),))
    with pytest.raises(ArgError):
        parse(spec, "--effort")


def test_excess_positional_without_greedy_raises():
    with pytest.raises(ArgError):
        parse(ArgSpec(positionals=(Arg("name"),)), "one two")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_command_args.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.commands.args'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/commands/args.py
"""Declarative argument parsing for slash commands.

A command declares an ``ArgSpec`` (positionals + flags); ``parse`` turns the
raw argument string into a validated ``Args``. Parsing rule: *flags lead* —
``--flag`` tokens are consumed from the front while they name a declared
flag, then positionals bind in order. A trailing ``greedy`` positional takes
the raw, un-tokenized remainder so free-text (prompts) survives verbatim.
Pure: no registry, no UI.
"""
from __future__ import annotations

from dataclasses import dataclass


class ArgError(ValueError):
    """Human-facing argument parse error (message is shown to the user)."""


@dataclass(frozen=True)
class Arg:
    name: str
    required: bool = True
    greedy: bool = False          # last positional only; takes raw remainder


@dataclass(frozen=True)
class Flag:
    name: str                     # "effort" matches --effort
    takes_value: bool = True      # False → boolean presence flag
    default: "str | bool | None" = None


@dataclass(frozen=True)
class ArgSpec:
    positionals: tuple[Arg, ...] = ()
    flags: tuple[Flag, ...] = ()


@dataclass(frozen=True)
class Args:
    positional: dict
    flags: dict

    def __getitem__(self, key):
        if key in self.positional:
            return self.positional[key]
        return self.flags[key]

    def get(self, key, default=None):
        if key in self.positional:
            return self.positional[key]
        return self.flags.get(key, default)


def _pop_token(s: str) -> "tuple[str | None, str]":
    """Pop one whitespace-delimited token from the front of ``s``, honoring
    single/double quotes. Returns ``(token, remainder)``; ``token`` is None
    when ``s`` is blank. Raises ArgError on an unterminated quote."""
    s = s.lstrip()
    if not s:
        return None, ""
    out: list[str] = []
    quote: str | None = None
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if quote:
            if c == quote:
                quote = None
            else:
                out.append(c)
        elif c in ('"', "'"):
            quote = c
        elif c.isspace():
            break
        else:
            out.append(c)
        i += 1
    if quote:
        raise ArgError("unterminated quote")
    return "".join(out), s[i:].lstrip()


def parse(spec: ArgSpec, argstr: str) -> Args:
    flags_by_name = {f.name: f for f in spec.flags}
    flag_values: dict = {}
    for f in spec.flags:
        flag_values[f.name] = (
            f.default if f.default is not None
            else (False if not f.takes_value else None))

    s = argstr
    # --- flag run (flags lead) ---
    while True:
        token, rest = _pop_token(s)
        if token is None or not token.startswith("--"):
            break
        name = token[2:]
        inline = None
        if "=" in name:
            name, inline = name.split("=", 1)
        f = flags_by_name.get(name)
        if f is None:
            raise ArgError(f"unknown flag: --{name}")
        if not f.takes_value:
            flag_values[name] = True
            s = rest
            continue
        if inline is not None:
            flag_values[name] = inline
            s = rest
            continue
        value, rest2 = _pop_token(rest)
        if value is None:
            raise ArgError(f"flag --{name} needs a value")
        flag_values[name] = value
        s = rest2

    # --- positionals ---
    positional: dict = {}
    for p in spec.positionals:
        if p.greedy:
            value = s.strip()
            if value:
                positional[p.name] = value
            elif p.required:
                raise ArgError(f"missing required argument: {p.name}")
            s = ""
            continue
        token, rest = _pop_token(s)
        if token is None:
            if p.required:
                raise ArgError(f"missing required argument: {p.name}")
            continue
        positional[p.name] = token
        s = rest

    if s.strip():
        raise ArgError(f"unexpected extra arguments: {s.strip()}")

    return Args(positional=positional, flags=flag_values)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_command_args.py -q`
Expected: PASS (all 12 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/commands/args.py tests/test_command_args.py
git commit -m "feat(commands): declarative typed-arg parser (args.py)"
```

---

### Task 2: Registry — `source` tag, `spec` field, collision guard

**Files:**
- Modify: `src/aegis/commands/__init__.py`
- Test: `tests/test_command_registry.py`

**Interfaces:**
- Consumes: `ArgSpec` from Task 1.
- Produces: `SlashCommand` now carries `source: str = "builtin"` and `spec: ArgSpec = ArgSpec()`; `CommandCollision(ValueError)`; `register(cmd)` raises `CommandCollision` when a non-builtin command shadows a builtin name.

This task does **not** change the handler signature or `dispatch()` yet (that is Task 3) — the new fields have defaults, so existing `SlashCommand(name, summary, usage, run)` construction in `builtins.py` keeps working and the whole suite stays green.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_command_registry.py
import pytest
from aegis.commands import (
    REGISTRY, SlashCommand, register, CommandCollision,
)


async def _noop(ctx, args):  # signature is irrelevant to this task's checks
    return None


def _restore(snapshot):
    REGISTRY.clear()
    REGISTRY.update(snapshot)


def test_builtin_registers_and_carries_source():
    snap = dict(REGISTRY)
    try:
        register(SlashCommand("t_reg_a", "s", "/t_reg_a", _noop))
        assert REGISTRY["t_reg_a"].source == "builtin"
    finally:
        _restore(snap)


def test_user_cannot_override_builtin():
    snap = dict(REGISTRY)
    try:
        register(SlashCommand("t_reg_b", "s", "/t_reg_b", _noop))  # builtin
        with pytest.raises(CommandCollision):
            register(SlashCommand("t_reg_b", "s", "/t_reg_b", _noop,
                                  source="user"))
    finally:
        _restore(snap)


def test_user_fresh_name_registers():
    snap = dict(REGISTRY)
    try:
        register(SlashCommand("t_reg_c", "s", "/t_reg_c", _noop,
                              source="user"))
        assert REGISTRY["t_reg_c"].source == "user"
    finally:
        _restore(snap)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_command_registry.py -q`
Expected: FAIL — `ImportError: cannot import name 'CommandCollision'` (and `SlashCommand` has no `source` kwarg).

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/commands/__init__.py`, add the import and edit the dataclass + `register`. Change the top imports:

```python
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from aegis.commands.args import Args, ArgError, ArgSpec, parse
```

Replace the `SlashCommand` dataclass and `register`:

```python
class CommandCollision(ValueError):
    """A non-builtin command tried to shadow a protected builtin name."""


@dataclass(frozen=True)
class SlashCommand:
    name: str
    summary: str          # one line, shown by /help
    usage: str            # e.g. "/spawn <agent> [prompt]"
    run: Handler
    source: str = "builtin"          # builtin | user | plugin
    spec: ArgSpec = field(default_factory=ArgSpec)


REGISTRY: dict[str, SlashCommand] = {}


def register(cmd: SlashCommand) -> None:
    """Add a command to the registry. Builtins are protected: a non-builtin
    command whose name already exists as a builtin is rejected."""
    existing = REGISTRY.get(cmd.name)
    if (existing is not None and existing.source == "builtin"
            and cmd.source != "builtin"):
        raise CommandCollision(
            f"/{cmd.name} is a builtin and cannot be overridden by a "
            f"{cmd.source} command")
    REGISTRY[cmd.name] = cmd
```

(`Handler`, `CommandResult`, `CommandContext`, `dispatch`, and the bottom `import builtins` line stay as they are in this task.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_command_registry.py tests/test_slash_commands.py -q`
Expected: PASS — new registry tests green, and the existing dispatcher suite still green (defaults preserve old construction).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/commands/__init__.py tests/test_command_registry.py
git commit -m "feat(commands): source tag + protected-builtin collision guard"
```

---

### Task 3: Typed dispatch + migrate all builtins to specs

**Files:**
- Modify: `src/aegis/commands/__init__.py` (dispatch)
- Modify: `src/aegis/commands/builtins.py` (all 6 handlers + registration)
- Test: `tests/test_slash_commands.py` (add cases; existing cases should still pass)

**Interfaces:**
- Consumes: `parse`, `ArgError`, `Arg`, `ArgSpec`, `Args` (Tasks 1–2).
- Produces: `Handler = Callable[[CommandContext, Args], Awaitable[CommandResult]]`; `dispatch()` parses `cmd.spec` and returns a `usage:`-titled error on `ArgError` without calling the handler. Builtin specs: `spawn` = `(Arg("agent"), Arg("prompt", required=False, greedy=True))`; `queue` = `(Arg("subverb"), Arg("name"), Arg("agent", required=False))`; `enqueue` = `(Arg("queue"), Arg("payload", greedy=True))`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_slash_commands.py`:

```python
async def test_argerror_returns_usage_and_skips_handler():
    # /spawn with no agent → parse fails on the required positional
    res = await dispatch("/spawn", _ctx())
    assert res.ok is False
    assert res.title.startswith("usage:")
    assert "/spawn" in res.title


async def test_typed_handler_receives_parsed_args():
    bridge = FakeBridge()
    ctx = CommandContext(bridge=bridge, handle="me")
    res = await dispatch("/spawn opus write the report", ctx)
    assert res.ok is True
    # FakeBridge.spawn records opening_prompt; assert the greedy prompt is verbatim
    assert bridge.spawned == ("opus", "write the report", "me")
```

Extend `FakeBridge.spawn` in that file to record its arguments:

```python
class FakeBridge:
    # ...existing...
    spawned = None
    async def spawn(self, agent, *, opening_prompt=None, spawned_by=None):
        FakeBridge.spawned = (agent, opening_prompt, spawned_by)
        return f"{agent}-1"
```

(If `FakeBridge.spawn` already exists, adjust it to record into an instance/class attribute rather than duplicating.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_slash_commands.py -q`
Expected: FAIL — dispatch does not yet parse specs (`res.title` won't start with `usage:`; `bridge.spawned` unset).

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/commands/__init__.py`, change `Handler` and `dispatch`:

```python
Handler = Callable[["CommandContext", Args], Awaitable["CommandResult"]]


async def dispatch(text: str, ctx: CommandContext) -> CommandResult:
    """Parse ``/verb rest-of-line``, parse its typed args, run the command.
    A bare ``/`` → ``/help``. Unknown verb, ArgError, or handler exception all
    come back as an error CommandResult; a bad command never kills the turn."""
    body = text[1:] if text.startswith("/") else text
    parts = body.split(None, 1)
    verb = parts[0].lower() if parts and parts[0] else "help"
    argstr = parts[1] if len(parts) > 1 else ""
    cmd = REGISTRY.get(verb)
    if cmd is None:
        return CommandResult(False, f"unknown command: /{verb}", "try /help")
    try:
        args = parse(cmd.spec, argstr)
    except ArgError as e:
        return CommandResult(False, f"usage: {cmd.usage}", str(e))
    try:
        return await cmd.run(ctx, args)
    except Exception as e:  # noqa: BLE001
        return CommandResult(False, f"/{verb} failed", f"{type(e).__name__}: {e}")
```

Rewrite `src/aegis/commands/builtins.py` handlers to take `(ctx, args)` and declare specs. Replace the file body below the module docstring:

```python
from __future__ import annotations

from aegis.commands import (
    REGISTRY, CommandContext, CommandResult, SlashCommand, register,
)
from aegis.commands.args import Arg, ArgSpec


async def _help(ctx: CommandContext, args) -> CommandResult:
    order = ["builtin", "user", "plugin"]
    by_source: dict[str, list] = {}
    for c in REGISTRY.values():
        by_source.setdefault(c.source, []).append(c)
    lines: list[str] = []
    seen = set()
    for src in order + [s for s in by_source if s not in order]:
        cmds = by_source.get(src)
        if not cmds or src in seen:
            continue
        seen.add(src)
        if len(by_source) > 1:
            lines.append(f"[{src}]")
        for c in sorted(cmds, key=lambda c: c.name):
            lines.append(f"{c.usage} — {c.summary}")
    return CommandResult(True, "commands", "\n".join(lines))


async def _sessions(ctx: CommandContext, args) -> CommandResult:
    sessions = list(ctx.bridge.list_sessions())
    if not sessions:
        return CommandResult(True, "no live sessions")
    lines = [f"{'*' if s.active else ' '} {s.handle} · {s.agent_slug} · "
             f"{s.state}" for s in sessions]
    plural = "" if len(sessions) == 1 else "s"
    return CommandResult(True, f"{len(sessions)} session{plural}",
                         "\n".join(lines))


async def _agents(ctx: CommandContext, args) -> CommandResult:
    names = ctx.bridge.list_agents()
    if not names:
        return CommandResult(True, "no agents configured")
    configs = getattr(ctx.bridge, "_agents", {}) or {}
    lines = []
    for name in names:
        a = configs.get(name)
        if a is None:
            lines.append(f"  {name}")
            continue
        harness = getattr(a, "harness", "") or "?"
        model = getattr(a, "model", "") or "?"
        perm = getattr(a, "permission", "")
        perm = getattr(perm, "value", perm) or "?"
        lines.append(f"  {name} · {harness} · {model} · {perm}")
    plural = "" if len(names) == 1 else "s"
    return CommandResult(True, f"{len(names)} agent{plural}", "\n".join(lines))


async def _spawn(ctx: CommandContext, args) -> CommandResult:
    agent = args["agent"]
    prompt = args.get("prompt")
    agents = ctx.bridge.list_agents()
    if agent not in agents:
        return CommandResult(False, f"unknown agent: {agent}",
                             "available: " + ", ".join(agents))
    handle = await ctx.bridge.spawn(agent, opening_prompt=prompt,
                                    spawned_by=ctx.handle)
    detail = f"agent {agent}" + (f" · prompt: {prompt}" if prompt else "")
    return CommandResult(True, f"spawned {handle}", detail)


async def _queue(ctx: CommandContext, args) -> CommandResult:
    if args["subverb"] != "new":
        return CommandResult(False, "usage: /queue new <name> [agent]")
    name = args["name"]
    agents = ctx.bridge.list_agents()
    agent = args.get("agent") or (agents[0] if agents else "")
    if not agent:
        return CommandResult(False, "no agent available for the queue")
    if agent not in agents:
        return CommandResult(False, f"unknown agent: {agent}",
                             "available: " + ", ".join(agents))
    from aegis.queue import Queue
    q = Queue(name=name, agent_profile=agent, max_parallel=1)
    try:
        ctx.bridge.register_queue(q)
    except ValueError as e:
        return CommandResult(False, f"queue rejected: {e}")
    return CommandResult(True, f"queue {name} created",
                         f"agent {agent} · max_parallel 1")


async def _enqueue(ctx: CommandContext, args) -> CommandResult:
    queue = args["queue"]
    payload = args["payload"]
    from aegis.queue import sender_user
    try:
        result = ctx.bridge.queue_manager.enqueue(
            queue, payload, enqueued_by=sender_user(), callback=False)
    except KeyError as e:
        return CommandResult(False, f"unknown queue: {e.args[0]!r}")
    if isinstance(result, dict):
        return CommandResult(False, "enqueue failed", str(result))
    tid, pos = result
    return CommandResult(True, f"queued task {tid}",
                         f"queue {queue} · position {pos}")


for _cmd in (
    SlashCommand("help", "list slash commands", "/help", _help),
    SlashCommand("sessions", "list live agent sessions", "/sessions",
                 _sessions),
    SlashCommand("agents", "list configured agents", "/agents", _agents),
    SlashCommand("spawn", "start a new top-level agent",
                 "/spawn <agent> [prompt]", _spawn,
                 spec=ArgSpec(positionals=(
                     Arg("agent"),
                     Arg("prompt", required=False, greedy=True)))),
    SlashCommand("queue", "create a queue", "/queue new <name> [agent]",
                 _queue,
                 spec=ArgSpec(positionals=(
                     Arg("subverb"), Arg("name"),
                     Arg("agent", required=False)))),
    SlashCommand("enqueue", "drop a task on a queue",
                 "/enqueue <queue> <payload>", _enqueue,
                 spec=ArgSpec(positionals=(
                     Arg("queue"), Arg("payload", greedy=True)))),
):
    register(_cmd)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_slash_commands.py tests/test_command_registry.py -q`
Expected: PASS. If any pre-existing assertion checked the exact `/queue` usage title (`test_queue_new_usage_on_missing_args`), confirm it still holds — the handler returns the same `usage: /queue new <name> [agent]` string and the parse-level failure also yields a `usage:`-titled result; adjust the assertion to `res.ok is False` if it was pinned to the old wording.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/commands/__init__.py src/aegis/commands/builtins.py tests/test_slash_commands.py
git commit -m "feat(commands): typed dispatch + migrate builtins to ArgSpec"
```

---

### Task 4: `/queue new` persistence + `--ephemeral`

**Files:**
- Modify: `src/aegis/commands/builtins.py` (`_queue` + its spec)
- Test: `tests/test_slash_commands.py`

**Interfaces:**
- Consumes: `Flag` from Task 1; `aegis.config.find_project_root`, `aegis.config.load_queues`, `aegis.config.ConfigError`, `aegis.config.edit.add_queue` (same path the `aegis_config_add_queue` MCP tool uses).
- Produces: `/queue new <name> [agent] [--ephemeral]`. Default persists to `.aegis.yaml` and hot-registers; `--ephemeral` hot-registers only. Spec gains `flags=(Flag("ephemeral", takes_value=False),)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_slash_commands.py`:

```python
async def test_queue_new_persists_by_default(monkeypatch):
    import aegis.config as cfg
    import aegis.config.edit as cfg_edit
    calls = {}
    monkeypatch.setattr(cfg, "find_project_root", lambda: __import__("pathlib").Path("/tmp/proj"))
    monkeypatch.setattr(cfg_edit, "add_queue",
                        lambda root, name, **kw: calls.setdefault("add", (str(root), name, kw)))
    monkeypatch.setattr(cfg, "load_queues",
                        lambda root: {"build": object()})
    bridge = FakeBridge()
    res = await dispatch("/queue new build opus", CommandContext(bridge=bridge, handle="me"))
    assert res.ok is True
    assert calls["add"][1] == "build"
    assert calls["add"][2] == {"agent": "opus", "max_parallel": 1}
    assert bridge.registered is not None            # hot-registered too


async def test_queue_new_ephemeral_skips_persistence(monkeypatch):
    import aegis.config.edit as cfg_edit
    monkeypatch.setattr(cfg_edit, "add_queue",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not persist")))
    bridge = FakeBridge()
    res = await dispatch("/queue new build opus --ephemeral",
                         CommandContext(bridge=bridge, handle="me"))
    assert res.ok is True
    assert "ephemeral" in res.title
    assert bridge.registered is not None
```

Ensure `FakeBridge` records `register_queue`:

```python
class FakeBridge:
    registered = None
    def register_queue(self, q):
        FakeBridge.registered = q
```

(Merge into the existing `FakeBridge` — do not duplicate the class. Confirm `list_agents()` returns a list containing `"opus"`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_slash_commands.py -q -k "queue_new_persists or queue_new_ephemeral"`
Expected: FAIL — `_queue` neither reads `--ephemeral` nor calls `add_queue`.

- [ ] **Step 3: Write minimal implementation**

Replace `_queue` in `builtins.py` and add the flag to its spec:

```python
async def _queue(ctx: CommandContext, args) -> CommandResult:
    if args["subverb"] != "new":
        return CommandResult(False,
                             "usage: /queue new <name> [agent] [--ephemeral]")
    name = args["name"]
    agents = ctx.bridge.list_agents()
    agent = args.get("agent") or (agents[0] if agents else "")
    if not agent:
        return CommandResult(False, "no agent available for the queue")
    if agent not in agents:
        return CommandResult(False, f"unknown agent: {agent}",
                             "available: " + ", ".join(agents))

    if args.flags.get("ephemeral"):
        from aegis.queue import Queue
        q = Queue(name=name, agent_profile=agent, max_parallel=1)
        try:
            ctx.bridge.register_queue(q)
        except ValueError as e:
            return CommandResult(False, f"queue rejected: {e}")
        return CommandResult(True, f"queue {name} created (ephemeral)",
                             f"agent {agent} · max_parallel 1")

    # persist to .aegis.yaml, then hot-register from the reloaded config
    import aegis.config as cfg
    import aegis.config.edit as cfg_edit
    root = cfg.find_project_root()
    if root is None:
        return CommandResult(False, "no .aegis.yaml found",
                             "run /queue new … --ephemeral for a session-only queue")
    try:
        cfg_edit.add_queue(root, name, agent=agent, max_parallel=1)
    except cfg.ConfigError as e:
        return CommandResult(False, f"queue rejected: {e}")
    try:
        fresh = cfg.load_queues(root)[name]
        ctx.bridge.register_queue(fresh)
    except Exception as e:                                    # noqa: BLE001
        return CommandResult(True, f"queue {name} saved",
                             f"persisted to .aegis.yaml; restart to activate "
                             f"(live register failed: {e})")
    return CommandResult(True, f"queue {name} created",
                         f"agent {agent} · persisted to .aegis.yaml")
```

Update the `SlashCommand("queue", …)` registration's spec to add the flag and reflect the usage string:

```python
    SlashCommand("queue", "create a queue",
                 "/queue new <name> [agent] [--ephemeral]", _queue,
                 spec=ArgSpec(
                     positionals=(Arg("subverb"), Arg("name"),
                                  Arg("agent", required=False)),
                     flags=(Flag("ephemeral", takes_value=False),))),
```

Add `Flag` to the args import at the top of `builtins.py`:

```python
from aegis.commands.args import Arg, ArgSpec, Flag
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_slash_commands.py -q`
Expected: PASS (persistence + ephemeral + all prior cases).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/commands/builtins.py tests/test_slash_commands.py
git commit -m "feat(commands): /queue new persists to .aegis.yaml (--ephemeral opts out)"
```

---

### Task 5: `classify_input()` helper + TUI `//` escaping

**Files:**
- Modify: `src/aegis/commands/__init__.py` (add `classify_input`)
- Modify: `src/aegis/tui/pane.py` (`on_growing_input_submitted`, ~lines 805–826)
- Test: `tests/test_command_registry.py` (helper unit) + `tests/test_pane_slash_command.py` (pane)

**Interfaces:**
- Consumes: nothing new.
- Produces: `classify_input(text: str) -> tuple[str, str]` returning `("command", text)` for a single leading `/`, `("message", <text minus one slash>)` for a leading `//`, and `("message", text)` otherwise. Both frontends route the slash family through it.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_command_registry.py`:

```python
from aegis.commands import classify_input


def test_classify_single_slash_is_command():
    assert classify_input("/sessions") == ("command", "/sessions")


def test_classify_double_slash_is_literal_message():
    assert classify_input("//not a command") == ("message", "/not a command")


def test_classify_plain_is_message():
    assert classify_input("hello there") == ("message", "hello there")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_command_registry.py -q -k classify`
Expected: FAIL — `ImportError: cannot import name 'classify_input'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/aegis/commands/__init__.py` (after `dispatch`):

```python
def classify_input(text: str) -> "tuple[str, str]":
    """Route an input line for the slash family. ``//foo`` is a literal
    message ``/foo`` (one slash stripped); a single leading ``/`` is a
    command; anything else is a plain message. The TUI's ``!`` shell escape
    is handled before this call and is not represented here."""
    if text.startswith("//"):
        return "message", text[1:]
    if text.startswith("/"):
        return "command", text
    return "message", text
```

Then in `src/aegis/tui/pane.py`, rewrite the `/`-branch of `on_growing_input_submitted`. The current block is:

```python
        elif text.startswith("/"):
            # Slash command: aegis executes it directly and renders the
            # result in the transcript — never delivered to the agent.
            from aegis.commands import CommandContext, dispatch
            from aegis.render import render_command_block
            width = self._transcript().size.width or 80
            result = await dispatch(
                text, CommandContext(bridge=self.app, handle=self.handle))
            self._flush_streaming()
            self._mount_block(
                render_command_block(result, self._palette, width),
                f"{result.title}\n{result.body}".strip())
            return
```

Replace it with (note: the `!` branch above stays unchanged; this `elif` becomes an `elif text.startswith("/"):` that now also handles `//`):

```python
        elif text.startswith("/"):
            from aegis.commands import (
                CommandContext, classify_input, dispatch)
            from aegis.render import render_command_block
            kind, payload = classify_input(text)
            if kind == "command":
                width = self._transcript().size.width or 80
                result = await dispatch(
                    payload, CommandContext(bridge=self.app,
                                            handle=self.handle))
                self._flush_streaming()
                self._mount_block(
                    render_command_block(result, self._palette, width),
                    f"{result.title}\n{result.body}".strip())
                return
            text = payload   # "//foo" → deliver "/foo" as a normal message
```

Falling out of the `elif` continues to the existing message-delivery code below, so a `//`-escaped line is delivered verbatim as `/foo`.

Add a pane test to `tests/test_pane_slash_command.py` mirroring the existing `test_slash_command_runs_and_is_not_sent` structure, asserting `//` delivers a message and does not mount a command block. Model it on the existing test's harness (same fixtures/imports at the top of that file):

```python
async def test_double_slash_delivers_literal_message(...):
    # Using the same pane harness as test_slash_command_runs_and_is_not_sent:
    # type "//hello", submit, assert the pane delivered a message whose body
    # is "/hello" and that no command block was mounted.
    ...
```

Fill the body by copying the existing test's setup (pane construction, `GrowingInput` submit) and swapping the input to `//hello` and the assertion to check message delivery (`core.deliver` / inbox received `"/hello"`) with no command block.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_command_registry.py tests/test_pane_slash_command.py -q`
Expected: PASS. If the TUI pane test flakes (inotify), re-run it alone per AGENTS.md before treating it as a real failure.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/commands/__init__.py src/aegis/tui/pane.py tests/test_command_registry.py tests/test_pane_slash_command.py
git commit -m "feat(commands): classify_input helper + // literal-slash escape in TUI"
```

---

### Task 6: Web parity — `deliver` routes slash commands

**Files:**
- Modify: `src/aegis/web/wssession.py` (the `deliver` branch of `_dispatch`, ~lines 252–259)
- Modify: `src/aegis/web/static/js/app.js` (deliver call site ~line 863 + a `mountCommandBlock` helper)
- Test: `tests/test_web_slash.py` (new)

**Interfaces:**
- Consumes: `classify_input`, `dispatch`, `CommandContext` from the commands core; `self._m` (the `SessionManager`, which implements `AppBridge`).
- Produces: the `deliver` RPC returns `{"command_result": {"ok", "title", "body"}}` for a slash command (and does **not** call `core.deliver`); `//` unescapes to a normal delivery; a plain message behaves exactly as before. The web client renders `command_result` as a transcript block.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_slash.py
from __future__ import annotations

import asyncio
import pytest

from aegis.web.wssession import WSSession
from tests.test_wssession_handoff_rename import FakeTransport  # reuse harness


class FakeCore:
    def __init__(self):
        self.delivered = []

    async def deliver(self, msg):
        self.delivered.append(msg.body)
        class R:  # minimal receipt
            disposition = "landed"
            depth = 0
        return R()


class FakeManager:
    """Implements the AppBridge subset the slash commands need for /sessions."""
    def __init__(self, core):
        self._core = core

    def get(self, handle):
        return self._core

    def list_sessions(self):
        return []

    def list_agents(self):
        return []


async def _deliver(session, handle, message):
    # call the same coroutine the RPC layer invokes; returns the result dict
    return await session._deliver_or_command(handle, message)  # see impl note


@pytest.mark.asyncio
async def test_web_slash_command_returns_command_result_and_skips_deliver():
    core = FakeCore()
    mgr = FakeManager(core)
    session = WSSession.__new__(WSSession)   # bypass full ctor
    session._m = mgr
    res = await session._deliver_or_command("h", "/sessions")
    assert "command_result" in res
    assert res["command_result"]["ok"] is True
    assert core.delivered == []              # never reached the agent


@pytest.mark.asyncio
async def test_web_double_slash_delivers_literal():
    core = FakeCore()
    session = WSSession.__new__(WSSession)
    session._m = FakeManager(core)
    res = await session._deliver_or_command("h", "//hello")
    assert core.delivered == ["/hello"]
    assert "delivery" in res
```

**Impl note:** to keep the branch unit-testable without the full transport/RPC envelope, extract the slash decision into a method `_deliver_or_command(self, handle, message) -> dict` on `WSSession` and have the `deliver` case in `_dispatch` call it. The test targets that method directly.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_web_slash.py -q`
Expected: FAIL — `AttributeError: 'WSSession' object has no attribute '_deliver_or_command'`.

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/web/wssession.py`, replace the existing `deliver` branch of `_dispatch`:

```python
        if method == "deliver":
            return await self._deliver_or_command(
                params["handle"], params["message"])
```

Add the method (near the other `_dispatch` helpers). Import lazily to keep module import light:

```python
    async def _deliver_or_command(self, handle: str, message: str) -> dict:
        """Route a web input line: a slash command runs through the shared
        dispatcher and returns a command_result frame (never reaching the
        agent); ``//`` unescapes to a literal message; anything else is
        delivered normally."""
        from aegis.commands import CommandContext, classify_input, dispatch
        kind, payload = classify_input(message)
        if kind == "command":
            result = await dispatch(
                payload, CommandContext(bridge=self._m, handle=handle))
            return {"command_result": {
                "ok": result.ok, "title": result.title, "body": result.body}}
        core = self._m.get(handle)
        if core is None:
            raise ValueError("unknown handle")
        from aegis.queue import InboxMessage, now_iso, sender_user
        msg = InboxMessage(sender=sender_user(), timestamp=now_iso(),
                           body=payload)
        receipt = await core.deliver(msg)
        return {"delivery": receipt.disposition, "depth": receipt.depth}
```

(Confirm `InboxMessage`, `now_iso`, `sender_user`, and `sender_user` are the same symbols the original `deliver` branch used — they are imported at module top today; if so, drop the local re-import and use the existing ones.)

In `src/aegis/web/static/js/app.js`, update the deliver call site (~line 863) to render a command block when the response carries one:

```javascript
      if (text && activeHandle) {
        client.rpc("deliver", { handle: activeHandle, message: text })
          .then((res) => {
            if (res && res.command_result) {
              mountCommandBlock(activeHandle, res.command_result);
            }
          })
          .catch((err) => showError("deliver failed: " + err.message));
        input.value = "";
        autogrow();
      }
```

Add the helper (near `blockEl` / `renderInto`):

```javascript
function mountCommandBlock(handle, cr) {
  const tab = tabs.get(handle);
  if (!tab) return;
  const stick = nearBottom(tab.transcriptEl);
  const div = document.createElement("div");
  div.className = "command-block" + (cr.ok ? "" : " error");
  const head = document.createElement("div");
  head.className = "command-title";
  head.textContent = "/ " + cr.title;
  div.appendChild(head);
  if (cr.body) {
    const body = document.createElement("pre");
    body.className = "command-body";
    body.textContent = cr.body;
    div.appendChild(body);
  }
  tab.transcriptEl.appendChild(div);
  if (stick) tab.transcriptEl.scrollTop = tab.transcriptEl.scrollHeight;
}
```

Add minimal styling so the block reads as distinct (append to the web stylesheet, e.g. `src/aegis/web/static/css/app.css` — match the existing block class conventions in that file):

```css
.command-block { border-left: 2px solid var(--accent, #d19a66); padding: 4px 8px; margin: 4px 0; }
.command-block.error { border-left-color: var(--error, #e06c75); }
.command-title { font-weight: 600; }
.command-body { white-space: pre-wrap; margin: 2px 0 0; }
```

(If the CSS variable names differ in the actual stylesheet, use the file's existing accent/error variables.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_web_slash.py -q`
Expected: PASS (command routing + `//` literal delivery).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/web/wssession.py src/aegis/web/static/js/app.js src/aegis/web/static/css/app.css tests/test_web_slash.py
git commit -m "feat(web): route slash commands through deliver, render command block"
```

---

### Task 7: Full-slice verification + docs

**Files:**
- Modify: `AGENTS.md` (§ commands, if a note is warranted) and `TASKS.md` (mark 2A done, note 2B/2C/2D)
- Modify: `CHANGELOG.md` (add a 2A entry under the next version)

- [ ] **Step 1: Run the hermetic suite**

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS. If a TUI/watchdog test flakes (inotify), re-run it alone before treating it as a real failure (AGENTS.md).

- [ ] **Step 2: Manual smoke (TUI)**

Run `aegis` in a project with a `.aegis.yaml`, then type: `/help` (grouped listing), `/queue new smoke <agent>` (check `.aegis.yaml` gained the queue), `/queue new tmp <agent> --ephemeral` (no file change), `//literal slash message` (delivered to agent as `/literal…`), `/spawn <agent> do a thing` (greedy prompt verbatim). Note any surprise; fix before proceeding.

- [ ] **Step 3: Update docs**

In `TASKS.md`, replace the "Slash commands — Phase 2" active block's first bullet set with a note that **2A (parser + resolution core) shipped**, and that 2B (builtin coverage), 2C (prompt + plugin commands), 2D (discovery UX) remain, referencing the two new docs. Add a `CHANGELOG.md` entry summarising the typed-arg layer, protected builtins, `//` escaping, `/queue new` persistence, and web parity.

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md TASKS.md CHANGELOG.md
git commit -m "docs: slash commands 2A shipped — update TASKS/CHANGELOG"
```

---

## Self-Review

**Spec coverage** — every 2A spec section maps to a task:
- Typed args (§1) → Task 1 (`args.py`) + Task 3 (dispatch parses, builtins declare specs).
- Registry sources + protected-builtin resolution (§2) → Task 2.
- `//` escaping (§3) → Task 5 (`classify_input` + TUI) and Task 6 (web seam).
- `/queue new` persistence (§4) → Task 4.
- Web parity (§5) → Task 6.
- Testing (§6) → tests folded into each task; Task 7 runs the full gate.

**Placeholder scan** — no "TBD/handle edge cases"; the two places that say "model on the existing test" (pane test body in Task 5, CSS variable names in Task 6) point at concrete in-repo references (`test_slash_command_runs_and_is_not_sent`, the existing stylesheet) rather than leaving logic unspecified, because those depend on harness details the implementer reads in-file.

**Type consistency** — `SlashCommand(name, summary, usage, run, source="builtin", spec=ArgSpec())` construction is consistent across Tasks 2–4; `Handler` takes `(ctx, Args)` from Task 3 onward; `classify_input -> (str, str)` used identically in Tasks 5–6; `Args.get` / `Args.flags` usage matches the Task 1 definition; `dispatch(payload, ctx)` (not raw `text`) is passed post-Task-5 so `//` is stripped before dispatch.
