# Telegram Substrate Command Surface (v0.10) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship nine new chat commands (queue, schedule, budget, peers, help) + refactor the existing five verbs into a command registry, exposing the substrate over Telegram for the first time since v0.2.

**Architecture:** One new module (`src/aegis/telegram/commands.py`) holds the `Command` registry + handler functions. `frontend.py::_command` collapses from an elif chain to a 15-line dispatcher that parses `@<peer>` suffixes and routes to the registry. The `TelegramFrontend` constructor grows `bridge` (for `queue_manager`/`scheduler`) and `cfg` (for `cfg.remotes`) parameters; `cli.py` threads them through. Zero substrate-side changes.

**Tech Stack:** Python 3.13, async httpx, Typer-untouched (CLI not affected), pytest with `uv run pytest -q -m "not live" -x`.

**Spec:** `docs/superpowers/specs/2026-05-26-aegis-telegram-substrate-commands-design.md` (canonical). Read it once before starting Task 1.

**Verified symbols (against `main` as of `019bc2c`):**
- `_PlaneBridge` at `src/aegis/cli.py:138` — has `queue_manager`, `scheduler`, `inbox_router`, `workflow_registry`, `state_root` fields.
- `Scheduler.snapshot() -> list[SimpleNamespace]` at `src/aegis/scheduler/scheduler.py:318`, `Scheduler.get(name) -> SimpleNamespace | None` at `:322`, `Scheduler.fire_now(name)` at `:199`.
- `QueueManager._queues`, `_pending`, `_inflight`, `_all`, `_load_recent_jsonl` at `src/aegis/queue/manager.py:72-81, 161`.
- `AegisConfig.remotes: dict[str, RemoteSpec]` at `src/aegis/config/yaml_loader.py:56`.
- `remote_budget_list`/`remote_budget_show`/`remote_schedule_list`/`remote_schedule_show` at `src/aegis/remote/client.py:119, 127, 142, 147`.
- `evaluate_budgets`, `BudgetCheck`, `Decision` at `src/aegis/budget/evaluator.py:15, 27, 97`.
- `format.chunk(text, *, label, ...)`, `format.escape_md` at `src/aegis/telegram/format.py`.

**Conventions:**
- Hermetic gate before every commit: `uv run pytest -q -m "not live" -x`.
- Commit straight to `main` (aegis convention; see workspace memory `feedback_aegis_work_on_main`).
- Use uv: `uv run pytest`, `uv pip install -e .`.

**Release-workflow note for Task 11:** The release workflow's CI path was unstable when v0.9.0 was tagged (four `fix(ci)` commits were pushed by the v0.9 dispatched Claude, and the latest run still failed with HTTP 403 on `actions/checkout@v4`). When you reach Task 11, **don't try to debug the workflow** — push the tag normally; if it fails to publish, file a blocker note at `vault/+/Inbox/for_claude/2026-05-26-aegis-v0.10-publish-blocker.md` and exit. Alex will deal with the workflow separately.

---

## Task 1: Command registry scaffold

**Files:**
- Create: `src/aegis/telegram/commands.py`
- Test: `tests/test_telegram_commands.py`

The registry primitives: `Command` dataclass, `CmdContext`, `COMMANDS` dict, `register()` function. No handlers registered yet.

- [ ] **Step 1: Write failing test**

Create `tests/test_telegram_commands.py`:

```python
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest

from aegis.telegram.commands import (
    Command, CmdContext, COMMANDS, register,
)


async def _noop(ctx, args): return


def test_register_adds_to_registry():
    cmd = Command(name="test_one", summary="first test command",
                  detail="more detail", handler=_noop)
    try:
        register(cmd)
        assert COMMANDS["test_one"] is cmd
    finally:
        COMMANDS.pop("test_one", None)


def test_register_rejects_duplicate():
    cmd1 = Command(name="test_dup", summary="x", detail="x", handler=_noop)
    cmd2 = Command(name="test_dup", summary="y", detail="y", handler=_noop)
    try:
        register(cmd1)
        with pytest.raises(ValueError, match="duplicate"):
            register(cmd2)
    finally:
        COMMANDS.pop("test_dup", None)


def test_cmdcontext_carries_required_fields():
    replies: list[str] = []
    async def reply(text: str) -> None: replies.append(text)
    ctx = CmdContext(bridge=object(), cfg=object(), manager=object(),
                      target=None, reply=reply, frontend=object())
    assert ctx.target is None
    assert ctx.frontend is not None
    asyncio.run(ctx.reply("hello"))
    assert replies == ["hello"]


def test_cmdcontext_with_target():
    async def reply(text: str) -> None: return
    ctx = CmdContext(bridge=object(), cfg=object(), manager=object(),
                      target="vps", reply=reply, frontend=object())
    assert ctx.target == "vps"
```

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/test_telegram_commands.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.telegram.commands'`.

- [ ] **Step 3: Implement the registry**

Create `src/aegis/telegram/commands.py`:

```python
"""Command registry for the Telegram frontend.

Each chat command is a `Command` registered at import time. The
frontend's dispatcher looks up the verb in `COMMANDS` and calls the
handler with a `CmdContext` carrying the bridge, config, session
manager, optional @peer target, and a reply callable.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CmdContext:
    """Context passed to every command handler.

    bridge:   the _PlaneBridge from cli.py (queue_manager, scheduler, ...)
    cfg:      the AegisConfig (cfg.remotes for @peer routing)
    manager:  the SessionManager (live session lookup, spawn, close)
    target:   the @peer name parsed from the user's command, or None
    reply:    async callable to send text back to the chat
    frontend: the TelegramFrontend instance — only used by the five
              migrated verbs (/new, /close, /interrupt, etc.) that
              mutate the active-session pointer. New commands should
              not touch this.
    """
    bridge:   Any
    cfg:      Any
    manager:  Any
    target:   str | None
    reply:    Callable[[str], Awaitable[None]]
    frontend: Any


@dataclass(frozen=True)
class Command:
    """One registered chat command.

    name:    full verb-plus-subcommand string, e.g. "queue list"
             or just "new" for bare-verb commands.
    summary: one-line description shown in `/help`.
    detail:  multi-line description shown in `/help <name>`.
    handler: async function called by the dispatcher.
    """
    name:    str
    summary: str
    detail:  str
    handler: Callable[[CmdContext, list[str]], Awaitable[None]]


COMMANDS: dict[str, Command] = {}


def register(cmd: Command) -> Command:
    """Register a command at import time. Duplicates fail loud."""
    if cmd.name in COMMANDS:
        raise ValueError(f"duplicate Telegram command {cmd.name!r}")
    COMMANDS[cmd.name] = cmd
    return cmd


def resolve_remote(ctx: CmdContext) -> tuple[str, Any] | None:
    """Look up ctx.target in cfg.remotes. Returns (target_name, spec)
    on success, None when ctx.target is None (local execution), and
    raises a custom marker if ctx.target is set but unknown — handler
    should reply with an error.
    """
    if ctx.target is None:
        return None
    remotes = getattr(ctx.cfg, "remotes", {}) or {}
    if ctx.target not in remotes:
        return None
    return ctx.target, remotes[ctx.target]
```

- [ ] **Step 4: Run test to verify pass**

```
uv run pytest tests/test_telegram_commands.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Run full hermetic suite + commit**

```bash
uv run pytest -q -m "not live" -x
git add src/aegis/telegram/commands.py tests/test_telegram_commands.py
git commit -m "feat(telegram): command registry scaffold (Command + CmdContext + COMMANDS)"
```

---

## Task 2: TelegramFrontend constructor + cli.py plumbing

**Files:**
- Modify: `src/aegis/telegram/frontend.py` (constructor)
- Modify: `src/aegis/cli.py` (callsite + minimal-bridge construction)
- Test: `tests/test_telegram_frontend.py` (update fixture)

Extend the constructor to accept `bridge` and `cfg`. Update the one cli.py callsite. When `remote_plane` is not configured (no `_PlaneBridge` exists), build a minimal bridge with just `queue_manager` + `scheduler` populated.

- [ ] **Step 1: Read the current frontend constructor**

```
grep -nA5 "def __init__" src/aegis/telegram/frontend.py
```
Note the exact existing parameter order so the patch is clean.

- [ ] **Step 2: Write failing test**

Add to `tests/test_telegram_frontend.py`:

```python
def test_frontend_ctor_accepts_bridge_and_cfg():
    """v0.10: TelegramFrontend gains bridge + cfg constructor params."""
    from aegis.telegram.frontend import TelegramFrontend

    class _FakeBot:
        async def send_message(self, *a, **k): return 1
        async def edit_message(self, *a, **k): return None

    class _FakeBridge:
        queue_manager = None
        scheduler = None

    class _FakeCfg:
        remotes = {}

    class _FakeMgr:
        def list_sessions(self): return []
        def list_agents(self): return []

    fe = TelegramFrontend(
        _FakeBot(), _FakeMgr(), _FakeBridge(), _FakeCfg(),
        chat_id=12345, auto_prompt="")
    assert fe._bridge is not None
    assert fe._cfg is not None
```

- [ ] **Step 3: Run test to verify failure**

```
uv run pytest tests/test_telegram_frontend.py::test_frontend_ctor_accepts_bridge_and_cfg -v
```
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'bridge'` or positional-arg mismatch.

- [ ] **Step 4: Extend the constructor**

In `src/aegis/telegram/frontend.py`, the existing `__init__` is at line 15. Replace:

```python
def __init__(self, bot, manager: SessionManager, *, chat_id: int,
             auto_prompt: str,
             refresh_interval: float = 2.0) -> None:
    self._bot = bot
    self._m = manager
    self._chat = chat_id
    self._auto = auto_prompt
    self._refresh = refresh_interval
    self._active: str | None = None
```

with:

```python
def __init__(self, bot, manager: SessionManager,
             bridge, cfg, *, chat_id: int,
             auto_prompt: str,
             refresh_interval: float = 2.0) -> None:
    self._bot = bot
    self._m = manager
    self._bridge = bridge
    self._cfg = cfg
    self._chat = chat_id
    self._auto = auto_prompt
    self._refresh = refresh_interval
    self._active: str | None = None
```

Positional: `bot`, `manager`, `bridge`, `cfg`. Keyword-only: the rest.

- [ ] **Step 5: Update the cli.py callsite**

In `src/aegis/cli.py`, the existing `TelegramFrontend(...)` construction is around line 284. The local context already has `cfg` (the AegisConfig). The `bridge` is either the one returned by `_maybe_start_remote_plane()` (if `remote_plane` was configured) or needs to be constructed as a minimal bridge.

Find the block that looks like:

```python
fe = TelegramFrontend(bot, mgr, chat_id=tg.chat_id,
                      auto_prompt=tg.auto_prompt)
```

(or similar — the actual line is `cli.py:284`). Replace with:

```python
# Telegram needs read access to queue_manager + scheduler + cfg.remotes.
# If remote_plane is not configured, the regular _PlaneBridge wasn't
# built — construct a minimal one with just the fields Telegram needs.
if bridge is None:
    from types import SimpleNamespace
    tg_bridge = SimpleNamespace(
        queue_manager=qm, scheduler=scheduler,
        inbox_router=None, workflow_registry=None,
        state_root=None,
    )
else:
    tg_bridge = bridge
fe = TelegramFrontend(bot, mgr, tg_bridge, cfg,
                       chat_id=tg.chat_id,
                       auto_prompt=tg.auto_prompt)
```

The `bridge` variable comes from `_maybe_start_remote_plane(cfg, qm)` earlier in `_serve()` (returns `_PlaneBridge | None`). `qm` is the local `QueueManager` and `scheduler` is the local Scheduler — both already in scope. If you grep `cli.py` for `scheduler =` and `bridge =`, you'll find their construction; adjust the placement of the Telegram wire-up so both are in scope.

- [ ] **Step 6: Run tests**

```
uv run pytest tests/test_telegram_frontend.py -v
```
Expected: the new test passes; existing frontend tests may need their ctor calls updated to pass `bridge=None, cfg=None` or stubs.

- [ ] **Step 7: Update any failing existing frontend tests**

For each `TelegramFrontend(...)` construction in the test file, add `_FakeBridge()` and `_FakeCfg()` (or `None, None`) as positional args. Common pattern:

```python
# Before
fe = TelegramFrontend(bot, mgr, chat_id=12345, auto_prompt="")
# After
fe = TelegramFrontend(bot, mgr, _FakeBridge(), _FakeCfg(),
                       chat_id=12345, auto_prompt="")
```

- [ ] **Step 8: Run full suite + commit**

```bash
uv run pytest -q -m "not live" -x
git add src/aegis/telegram/frontend.py src/aegis/cli.py tests/test_telegram_frontend.py
git commit -m "feat(telegram): TelegramFrontend ctor grows bridge + cfg params"
```

---

## Task 3: Dispatcher refactor + @peer parsing

**Files:**
- Modify: `src/aegis/telegram/frontend.py` (`_command` method)
- Test: `tests/test_telegram_dispatcher.py` (new)

Refactor `frontend._command(text)` to parse `@<peer>` tokens and look up the verb in `COMMANDS`. The five existing verbs stay in the elif chain *for now* (Task 4 migrates them); the registry path activates only when a verb matches a registered `Command`. Fall-through to `_legacy_handle_alias` for the `/<handle>` pattern when nothing matches.

- [ ] **Step 1: Write failing tests**

Create `tests/test_telegram_dispatcher.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from aegis.telegram.commands import (
    COMMANDS, Command, CmdContext, register,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Don't leak test commands between tests."""
    snap = dict(COMMANDS)
    yield
    COMMANDS.clear()
    COMMANDS.update(snap)


def _make_frontend(commands_to_register=None):
    """Build a TelegramFrontend with stub bot/manager/bridge/cfg."""
    from aegis.telegram.frontend import TelegramFrontend

    class _Bot:
        sent: list[str] = []
        async def send_message(self, chat, text, markdown=False):
            self.sent.append(text)
            return 1
        async def edit_message(self, *a, **k): return None

    class _Mgr:
        def list_sessions(self): return []
        def list_agents(self): return ["default"]
        def get(self, handle): return None
        async def close(self, handle): return None
        async def interrupt(self, handle): return None
        def _sync_spawn(self, slug): raise KeyError(slug)

    class _Bridge:
        queue_manager = None
        scheduler = None

    class _Cfg:
        remotes: dict = {}

    for cmd in (commands_to_register or []):
        register(cmd)

    bot = _Bot()
    fe = TelegramFrontend(bot, _Mgr(), _Bridge(), _Cfg(),
                          chat_id=42, auto_prompt="")
    return fe, bot


@pytest.mark.asyncio
async def test_dispatcher_routes_registered_verb():
    """A registered command is dispatched via the registry."""
    called: list[tuple] = []

    async def _h(ctx, args):
        called.append((args, ctx.target))
        await ctx.reply("ok")

    cmd = Command(name="ping", summary="x", detail="x", handler=_h)
    fe, bot = _make_frontend([cmd])
    await fe._command("/ping foo bar")
    assert called == [(["foo", "bar"], None)]
    assert "ok" in bot.sent[-1]


@pytest.mark.asyncio
async def test_dispatcher_parses_at_peer():
    """An @<peer> token is pulled out of args and exposed as ctx.target."""
    called: list[tuple] = []

    async def _h(ctx, args):
        called.append((args, ctx.target))
        await ctx.reply("ok")

    cmd = Command(name="ping", summary="x", detail="x", handler=_h)
    fe, bot = _make_frontend([cmd])
    await fe._command("/ping foo @vps bar")
    assert called == [(["foo", "bar"], "vps")]


@pytest.mark.asyncio
async def test_dispatcher_only_first_at_token_taken():
    called: list[tuple] = []
    async def _h(ctx, args):
        called.append((args, ctx.target))

    cmd = Command(name="ping", summary="x", detail="x", handler=_h)
    fe, bot = _make_frontend([cmd])
    await fe._command("/ping @vps @desktop")
    assert called == [([], "vps")]
    # "@desktop" is dropped — first wins.


@pytest.mark.asyncio
async def test_dispatcher_longest_prefix_match():
    """`/queue list` resolves before `/queue` when both registered."""
    async def _list(ctx, args): await ctx.reply("LIST")
    async def _bare(ctx, args): await ctx.reply("BARE")

    fe, bot = _make_frontend([
        Command(name="queue list", summary="x", detail="x", handler=_list),
        Command(name="queue",      summary="x", detail="x", handler=_bare),
    ])
    await fe._command("/queue list")
    assert "LIST" in bot.sent[-1]
    await fe._command("/queue")
    assert "BARE" in bot.sent[-1]


@pytest.mark.asyncio
async def test_dispatcher_unknown_verb_falls_through_to_legacy_alias():
    """Unknown verb tries /<handle> alias-routing."""
    fe, bot = _make_frontend()
    await fe._command("/no-such-command")
    # The fallback emits 'no session ...' since there's no such handle.
    assert "no session" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_dispatcher_empty_at_is_args_only():
    """Bare '@' is not a target — stays in args (handler can validate)."""
    called: list[tuple] = []
    async def _h(ctx, args):
        called.append((args, ctx.target))

    cmd = Command(name="ping", summary="x", detail="x", handler=_h)
    fe, bot = _make_frontend([cmd])
    await fe._command("/ping @")
    assert called == [(["@"], None)]
```

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/test_telegram_dispatcher.py -v
```
Expected: FAIL — `_command` doesn't do registry lookup yet; tests probably hit the legacy elif chain and get "no session" replies for all of them.

- [ ] **Step 3: Refactor `frontend._command`**

In `src/aegis/telegram/frontend.py`, find the existing `async def _command(self, text: str) -> None:` (around line 120). The five existing verbs (`/new`, `/close`, `/interrupt`, `/agents`, `/sessions`, `/help`) stay in the elif chain for this task — but ADD the registry path *before* the elif chain so registered commands win:

```python
async def _command(self, text: str) -> None:
    from aegis.telegram.commands import COMMANDS, CmdContext

    head, _, rest = text.partition(" ")
    verb = head.lstrip("/")
    tokens = rest.split()

    # Pull out @<peer>; only @<name> where name is non-empty counts.
    target: str | None = None
    args: list[str] = []
    for t in tokens:
        if t.startswith("@") and len(t) > 1 and target is None:
            target = t[1:]
        else:
            args.append(t)

    # Longest-prefix match: try "<verb> <args[0]>" before "<verb>".
    key2 = f"{verb} {args[0]}" if args else None
    cmd = COMMANDS.get(key2) if key2 else None
    if cmd is not None:
        args = args[1:]
    else:
        cmd = COMMANDS.get(verb)

    if cmd is not None:
        ctx = CmdContext(
            bridge=self._bridge, cfg=self._cfg, manager=self._m,
            target=target, reply=self._reply, frontend=self)
        await cmd.handler(ctx, args)
        return

    # Fall through to the legacy elif chain (existing verbs migrate
    # in Task 4) and the /<handle> alias-routing at the bottom.
    rest = rest.strip()
    if head == "/new":
        # ... existing body unchanged ...
    elif head == "/close":
        # ... etc, existing elif chain unchanged ...
```

Keep the existing elif chain intact below the registry check. The elif chain will be drained in Task 4.

- [ ] **Step 4: Extract the `/<handle>` alias-routing**

The existing `else:` branch at the bottom of `_command` does the `/<handle>` lookup with underscore-to-hyphen normalization. Lift it into a method so Task 4 can move the rest of the elif chain out:

```python
async def _legacy_handle_alias(self, head: str, rest: str) -> None:
    """The /<handle> alias-routing pattern: send `rest` to the named
    session, or set it active if no rest is given. Underscore→hyphen
    normalization because Telegram only auto-links [A-Za-z0-9_]+ but
    aegis handles are hyphenated.
    """
    raw = head[1:]
    core = self._m.get(raw) or self._m.get(raw.replace("_", "-"))
    if core is None:
        await self._reply(f"no session {raw!r} — /sessions")
        return
    if rest:
        await self._send_to(core, rest)
    else:
        self._active = core.handle
        await self._reply(f"▸ talking to {core.handle}")
```

And replace the existing `else:` branch at the bottom of `_command` with:

```python
    else:
        await self._legacy_handle_alias(head, rest)
```

- [ ] **Step 5: Run dispatcher tests**

```
uv run pytest tests/test_telegram_dispatcher.py -v
```
Expected: PASS (6 tests).

- [ ] **Step 6: Run full suite + commit**

```bash
uv run pytest -q -m "not live" -x
git add src/aegis/telegram/frontend.py tests/test_telegram_dispatcher.py
git commit -m "feat(telegram): dispatcher refactor — registry lookup with @peer parsing"
```

---

## Task 4: Migrate /new, /close, /interrupt, /agents, /sessions, /help into the registry

**Files:**
- Modify: `src/aegis/telegram/commands.py` (add 5 handlers)
- Modify: `src/aegis/telegram/frontend.py` (remove elif chain — keep only the legacy-alias fallthrough)
- Test: `tests/test_telegram_frontend.py` (existing tests confirm behavior parity)

Lift each existing verb's body into a `Command` handler. The handlers need access to `frontend._active` (mutated by `/new`, `/close`, `/<handle>` activation) and `frontend._send_to` (used by message routing) — these come through `ctx.frontend`. After migration, `_command`'s elif chain is gone; only the registry dispatch + `_legacy_handle_alias` fallthrough remain.

- [ ] **Step 1: Write the migrated handlers**

Add to `src/aegis/telegram/commands.py`:

```python
# ── existing verbs migrated into the registry ──────────────────


async def _cmd_new(ctx: CmdContext, args: list[str]) -> None:
    slug = args[0] if args else None
    try:
        core = ctx.manager._sync_spawn(slug)
    except KeyError:
        agent_list = ", ".join(ctx.manager.list_agents())
        await ctx.reply(f"unknown agent. agents: {agent_list}")
        return
    ctx.frontend._active = core.handle
    await ctx.reply(f"▸ spawned {core.handle} ({core.agent_slug})")


register(Command(
    name="new",
    summary="/new [slug] — spawn a new agent session",
    detail=(
        "/new [agent-slug]\n\n"
        "Spawn a new agent session. With no arg, uses the default "
        "agent profile. The new session becomes the active session "
        "for bare-text routing. Use /agents to list available profiles."
    ),
    handler=_cmd_new,
))


async def _cmd_close(ctx: CmdContext, args: list[str]) -> None:
    fe = ctx.frontend
    if fe._active is None:
        await ctx.reply("no active agent")
        return
    closed = fe._active
    await ctx.manager.close(closed)
    rest_sessions = ctx.manager.list_sessions()
    fe._active = rest_sessions[0].handle if rest_sessions else None
    tail = f"active: {fe._active}" if fe._active else "no active agent"
    await ctx.reply(f"▸ closed {closed} · {tail}")


register(Command(
    name="close",
    summary="/close — close the active session",
    detail=(
        "/close\n\n"
        "Close the currently-active agent session. If other sessions "
        "exist, the first one becomes active. Otherwise the active "
        "pointer clears."
    ),
    handler=_cmd_close,
))


async def _cmd_interrupt(ctx: CmdContext, args: list[str]) -> None:
    fe = ctx.frontend
    if fe._active is None:
        await ctx.reply("no active agent")
        return
    await ctx.manager.interrupt(fe._active)
    await ctx.reply(f"▸ interrupted {fe._active}")


register(Command(
    name="interrupt",
    summary="/interrupt — interrupt the active session's current turn",
    detail=(
        "/interrupt\n\n"
        "Stop the active session's in-progress turn. Equivalent to "
        "pressing Escape in the TUI. The session stays open; you can "
        "send another message immediately."
    ),
    handler=_cmd_interrupt,
))


async def _cmd_agents(ctx: CmdContext, args: list[str]) -> None:
    agent_list = ", ".join(ctx.manager.list_agents())
    await ctx.reply(f"agents: {agent_list}")


register(Command(
    name="agents",
    summary="/agents — list available agent profiles",
    detail=(
        "/agents\n\n"
        "List the agent profiles declared in .aegis.py. Use one of "
        "these names as the slug argument to /new."
    ),
    handler=_cmd_agents,
))


async def _cmd_sessions(ctx: CmdContext, args: list[str]) -> None:
    sessions = ctx.manager.list_sessions()
    if not sessions:
        await ctx.reply("no sessions")
        return
    # One per line; /underscore_alias is tappable in Telegram (which
    # only auto-links [A-Za-z0-9_]+) and routes back via the _ -> -
    # normalization in _legacy_handle_alias.
    lines = [
        f"{'●' if s.state == 'working' else '○'} "
        f"/{s.handle.replace('-', '_')} {s.state}"
        for s in sessions
    ]
    await ctx.reply("\n".join(lines))


register(Command(
    name="sessions",
    summary="/sessions — list active sessions",
    detail=(
        "/sessions\n\n"
        "List all active agent sessions with their state (working / "
        "ready). Each handle is rendered as /handle_with_underscores "
        "so Telegram makes it tappable; the dispatcher normalizes back "
        "to the real hyphenated handle."
    ),
    handler=_cmd_sessions,
))
```

- [ ] **Step 2: Remove the elif chain from `_command`**

In `src/aegis/telegram/frontend.py`, the `_command` method's body simplifies to: registry lookup → if found, dispatch; else, legacy handle alias. The full method becomes:

```python
async def _command(self, text: str) -> None:
    from aegis.telegram.commands import COMMANDS, CmdContext

    head, _, rest = text.partition(" ")
    verb = head.lstrip("/")
    tokens = rest.split()

    target: str | None = None
    args: list[str] = []
    for t in tokens:
        if t.startswith("@") and len(t) > 1 and target is None:
            target = t[1:]
        else:
            args.append(t)

    key2 = f"{verb} {args[0]}" if args else None
    cmd = COMMANDS.get(key2) if key2 else None
    if cmd is not None:
        args = args[1:]
    else:
        cmd = COMMANDS.get(verb)

    if cmd is not None:
        ctx = CmdContext(
            bridge=self._bridge, cfg=self._cfg, manager=self._m,
            target=target, reply=self._reply, frontend=self)
        await cmd.handler(ctx, args)
        return

    await self._legacy_handle_alias(head, rest.strip())
```

The old elif-branch bodies live in `commands.py` now. The `_agents_line` and `_sessions_line` helpers in `frontend.py` can be deleted (their bodies moved into `_cmd_agents` and `_cmd_sessions`).

- [ ] **Step 3: Run existing telegram tests**

```
uv run pytest tests/test_telegram_frontend.py tests/test_telegram_dispatcher.py -v
```
Expected: PASS (existing tests for /new, /close, /interrupt, /agents, /sessions hit the new registry path; behavior parity).

- [ ] **Step 4: Run full suite + commit**

```bash
uv run pytest -q -m "not live" -x
git add src/aegis/telegram/commands.py src/aegis/telegram/frontend.py
git commit -m "feat(telegram): migrate /new /close /interrupt /agents /sessions to registry"
```

---

## Task 5: /help and /help <name>

**Files:**
- Modify: `src/aegis/telegram/commands.py` (add /help handler)
- Test: `tests/test_telegram_help.py` (new)

Registry-driven help. `/help` lists every registered command's `summary` grouped by resource (first whitespace-token of `name`); `/help <name>` looks up by exact name and prints the `detail`; `/help <resource>` filters by prefix.

- [ ] **Step 1: Write failing tests**

Create `tests/test_telegram_help.py`:

```python
from __future__ import annotations

import pytest

from aegis.telegram.commands import COMMANDS, Command, register


@pytest.fixture(autouse=True)
def _clean_registry():
    snap = dict(COMMANDS)
    yield
    COMMANDS.clear()
    COMMANDS.update(snap)


def _make_frontend():
    from aegis.telegram.frontend import TelegramFrontend

    class _Bot:
        sent: list[str] = []
        async def send_message(self, chat, text, markdown=False):
            self.sent.append(text)
            return 1
        async def edit_message(self, *a, **k): return None

    class _Mgr:
        def list_sessions(self): return []
        def list_agents(self): return []

    class _Bridge: queue_manager = scheduler = None
    class _Cfg: remotes: dict = {}

    bot = _Bot()
    fe = TelegramFrontend(bot, _Mgr(), _Bridge(), _Cfg(),
                          chat_id=42, auto_prompt="")
    return fe, bot


@pytest.mark.asyncio
async def test_help_lists_all_registered_commands():
    fe, bot = _make_frontend()
    await fe._command("/help")
    out = bot.sent[-1]
    # Every registered command should appear in the bare /help listing.
    for cmd_name in COMMANDS:
        # Multi-word names like "queue list" should show up as is.
        assert cmd_name in out or cmd_name.replace(" ", " ") in out


@pytest.mark.asyncio
async def test_help_for_named_command_prints_detail():
    fe, bot = _make_frontend()
    await fe._command("/help new")
    out = bot.sent[-1]
    # The /new command's detail mentions "spawn a new agent".
    assert "spawn a new agent" in out.lower()


@pytest.mark.asyncio
async def test_help_for_unknown_command_errors():
    fe, bot = _make_frontend()
    await fe._command("/help ghost-command")
    out = bot.sent[-1]
    assert "no such command" in out.lower() or "unknown" in out.lower()


@pytest.mark.asyncio
async def test_help_for_resource_filters_by_prefix():
    """`/help queue` lists every command whose name starts with `queue `."""
    register(Command(name="queue list",  summary="list queues",
                      detail="queue list detail", handler=_noop_handler()))
    register(Command(name="queue show",  summary="show queue",
                      detail="queue show detail", handler=_noop_handler()))
    fe, bot = _make_frontend()
    await fe._command("/help queue")
    out = bot.sent[-1]
    assert "queue list" in out
    assert "queue show" in out


def _noop_handler():
    async def _h(ctx, args):
        await ctx.reply("noop")
    return _h
```

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/test_telegram_help.py -v
```
Expected: FAIL — `/help` currently emits the cryptic one-line existing message.

- [ ] **Step 3: Implement /help and /help <name>**

Add to `src/aegis/telegram/commands.py`:

```python
async def _cmd_help(ctx: CmdContext, args: list[str]) -> None:
    if not args:
        # Bare /help: group by resource (first whitespace token).
        groups: dict[str, list[Command]] = {}
        for cmd in COMMANDS.values():
            resource = cmd.name.split(" ", 1)[0]
            groups.setdefault(resource, []).append(cmd)
        lines = ["Aegis Telegram commands (/help <name> for detail):", ""]
        for resource in sorted(groups):
            cmds = sorted(groups[resource], key=lambda c: c.name)
            for cmd in cmds:
                lines.append(f"  /{cmd.name} — {cmd.summary}")
            lines.append("")
        # Drop trailing blank
        if lines and lines[-1] == "":
            lines.pop()
        await ctx.reply("\n".join(lines))
        return

    # /help <name> — try exact match first, then prefix match.
    needle = " ".join(args)
    if needle in COMMANDS:
        cmd = COMMANDS[needle]
        await ctx.reply(f"/{cmd.name}\n\n{cmd.detail}")
        return

    matching = [c for c in COMMANDS.values()
                if c.name == needle or c.name.startswith(needle + " ")]
    if matching:
        lines = [f"commands matching {needle!r}:", ""]
        for cmd in sorted(matching, key=lambda c: c.name):
            lines.append(f"  /{cmd.name} — {cmd.summary}")
        await ctx.reply("\n".join(lines))
        return

    await ctx.reply(f"no such command {needle!r}; /help to list all")


register(Command(
    name="help",
    summary="/help [name] — list commands, or show detail for one",
    detail=(
        "/help [name]\n\n"
        "With no argument, lists every registered command grouped by "
        "resource. With a command name (`/help new`), prints the "
        "command's full detail. With a resource prefix "
        "(`/help queue`), lists every subcommand under that resource."
    ),
    handler=_cmd_help,
))
```

- [ ] **Step 4: Run help tests**

```
uv run pytest tests/test_telegram_help.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Run full suite + commit**

```bash
uv run pytest -q -m "not live" -x
git add src/aegis/telegram/commands.py tests/test_telegram_help.py
git commit -m "feat(telegram): /help and /help <name> driven by the command registry"
```

---

## Task 6: /peers

**Files:**
- Modify: `src/aegis/telegram/commands.py` (add /peers handler)
- Test: `tests/test_telegram_peers.py` (new)

Iterate `cfg.remotes`; emit name + URL + auth + reachable check. Reachability via `httpx.AsyncClient.get(url + "/remote/v1/", timeout=3.0)` — any HTTP response = reachable; connection error = unreachable.

- [ ] **Step 1: Write failing test**

Create `tests/test_telegram_peers.py`:

```python
from __future__ import annotations

import httpx
import pytest

from aegis.remote.config import RemoteSpec
from aegis.telegram.commands import COMMANDS


@pytest.fixture(autouse=True)
def _clean_registry():
    snap = dict(COMMANDS)
    yield
    COMMANDS.clear()
    COMMANDS.update(snap)


def _make_frontend(remotes: dict | None = None):
    from aegis.telegram.frontend import TelegramFrontend

    class _Bot:
        sent: list[str] = []
        async def send_message(self, chat, text, markdown=False):
            self.sent.append(text); return 1
        async def edit_message(self, *a, **k): return None

    class _Mgr:
        def list_sessions(self): return []
        def list_agents(self): return []

    class _Bridge: queue_manager = scheduler = None
    class _Cfg:
        def __init__(self): self.remotes = remotes or {}

    bot = _Bot()
    fe = TelegramFrontend(bot, _Mgr(), _Bridge(), _Cfg(),
                          chat_id=42, auto_prompt="")
    return fe, bot


@pytest.mark.asyncio
async def test_peers_empty():
    fe, bot = _make_frontend(remotes={})
    await fe._command("/peers")
    assert "no peers" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_peers_shows_url_and_auth(httpx_mock):
    httpx_mock.add_response(
        method="GET", url="http://1.2.3.4:8556/remote/v1/",
        status_code=404)        # any response = reachable
    fe, bot = _make_frontend(remotes={
        "vps": RemoteSpec(url="http://1.2.3.4:8556", token="secret"),
    })
    await fe._command("/peers")
    out = bot.sent[-1]
    assert "vps" in out
    assert "1.2.3.4" in out
    assert "token" in out.lower()


@pytest.mark.asyncio
async def test_peers_unreachable(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("nope"))
    fe, bot = _make_frontend(remotes={
        "down": RemoteSpec(url="http://5.6.7.8:8556"),
    })
    await fe._command("/peers")
    out = bot.sent[-1]
    assert "unreachable" in out.lower() or "✗" in out
```

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/test_telegram_peers.py -v
```
Expected: FAIL — `/peers` not registered.

- [ ] **Step 3: Implement /peers**

Add to `src/aegis/telegram/commands.py`:

```python
async def _peer_reachable(url: str) -> bool:
    """Quick reachability probe — any HTTP response counts as reachable.
    3s timeout for mobile-fast feedback."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
            await client.get(url.rstrip("/") + "/remote/v1/")
        return True
    except httpx.HTTPError:
        return False


async def _cmd_peers(ctx: CmdContext, args: list[str]) -> None:
    remotes = getattr(ctx.cfg, "remotes", {}) or {}
    if not remotes:
        await ctx.reply("no peers configured")
        return
    lines = ["```",
             f"{'NAME':<12} {'URL':<32} {'AUTH':<8} {'REACHABLE'}"]
    for name in sorted(remotes):
        spec = remotes[name]
        url = getattr(spec, "url", "?")
        auth = "token" if getattr(spec, "token", None) else "—"
        ok = await _peer_reachable(url)
        reach = "✓" if ok else "✗ unreachable"
        lines.append(f"{name:<12} {url:<32} {auth:<8} {reach}")
    lines.append("```")
    await ctx.reply("\n".join(lines))


register(Command(
    name="peers",
    summary="/peers — list configured remotes and their reachability",
    detail=(
        "/peers\n\n"
        "List every peer in .aegis.yaml's `remotes:` block, with URL, "
        "auth status (token configured or not), and a 3-second "
        "reachability probe. No @<peer> argument (the command is "
        "about peers themselves)."
    ),
    handler=_cmd_peers,
))
```

- [ ] **Step 4: Run tests + commit**

```bash
uv run pytest tests/test_telegram_peers.py -v
uv run pytest -q -m "not live" -x
git add src/aegis/telegram/commands.py tests/test_telegram_peers.py
git commit -m "feat(telegram): /peers — list remotes + reachability check"
```

---

## Task 7: /schedule list + /schedule show

**Files:**
- Modify: `src/aegis/telegram/commands.py` (add two handlers)
- Test: `tests/test_telegram_schedule.py` (new)

Local: `bridge.scheduler.snapshot()` for list, `bridge.scheduler.get(name)` for show. Remote: `remote_schedule_list(spec)` / `remote_schedule_show(spec, name)`. Both honor `@peer`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_telegram_schedule.py`:

```python
from __future__ import annotations

from types import SimpleNamespace

import pytest

from aegis.remote.config import RemoteSpec
from aegis.telegram.commands import COMMANDS


@pytest.fixture(autouse=True)
def _clean_registry():
    snap = dict(COMMANDS)
    yield
    COMMANDS.clear()
    COMMANDS.update(snap)


def _make_frontend(scheduler=None, remotes=None):
    from aegis.telegram.frontend import TelegramFrontend

    class _Bot:
        sent: list[str] = []
        async def send_message(self, chat, text, markdown=False):
            self.sent.append(text); return 1
        async def edit_message(self, *a, **k): return None

    class _Mgr:
        def list_sessions(self): return []
        def list_agents(self): return []

    class _Bridge: pass
    _Bridge.queue_manager = None
    _Bridge.scheduler = scheduler

    class _Cfg:
        def __init__(self, remotes=None):
            self.remotes = remotes or {}

    bot = _Bot()
    fe = TelegramFrontend(bot, _Mgr(), _Bridge(), _Cfg(remotes),
                          chat_id=42, auto_prompt="")
    return fe, bot


@pytest.mark.asyncio
async def test_schedule_list_empty_local():
    class _Sched:
        def snapshot(self): return []
    fe, bot = _make_frontend(scheduler=_Sched())
    await fe._command("/schedule list")
    assert "no schedules" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_schedule_list_local_shows_entries():
    entries = [
        SimpleNamespace(name="nightly-build", source="pushed",
                         next_fire="2026-05-27T02:00:00Z",
                         fire_count=47, enabled=True),
        SimpleNamespace(name="weekly-report", source="inline",
                         next_fire="2026-05-31T08:00:00Z",
                         fire_count=12, enabled=True),
    ]
    class _Sched:
        def snapshot(self): return entries
    fe, bot = _make_frontend(scheduler=_Sched())
    await fe._command("/schedule list")
    out = bot.sent[-1]
    assert "nightly-build" in out
    assert "weekly-report" in out


@pytest.mark.asyncio
async def test_schedule_list_remote_routes_through_client(monkeypatch):
    captured = {}
    async def fake_list(spec):
        captured["spec"] = spec
        return {"schedules": [
            {"name": "remote-job", "source": "inline",
             "next_fire": "2026-05-27T05:00:00Z",
             "fire_count": 5, "enabled": True},
        ]}
    monkeypatch.setattr("aegis.remote.client.remote_schedule_list",
                        fake_list)

    fe, bot = _make_frontend(scheduler=None,
                              remotes={"vps": RemoteSpec(
                                  url="http://1.2.3.4:8556")})
    await fe._command("/schedule list @vps")
    assert captured["spec"].url == "http://1.2.3.4:8556"
    assert "remote-job" in bot.sent[-1]


@pytest.mark.asyncio
async def test_schedule_show_local_known():
    entry = SimpleNamespace(
        name="nb", source="pushed",
        spec={"workflow": "enqueue", "cron": "0 2 * * *"},
        next_fire="2026-05-27T02:00:00Z", fire_count=10, enabled=True)
    class _Sched:
        def get(self, name): return entry if name == "nb" else None
    fe, bot = _make_frontend(scheduler=_Sched())
    await fe._command("/schedule show nb")
    out = bot.sent[-1]
    assert "nb" in out
    assert "0 2 * * *" in out


@pytest.mark.asyncio
async def test_schedule_show_local_unknown():
    class _Sched:
        def get(self, name): return None
    fe, bot = _make_frontend(scheduler=_Sched())
    await fe._command("/schedule show ghost")
    assert "no such schedule" in bot.sent[-1].lower() or \
           "unknown" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_schedule_show_missing_arg():
    class _Sched:
        def get(self, name): return None
    fe, bot = _make_frontend(scheduler=_Sched())
    await fe._command("/schedule show")
    assert "usage" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_schedule_remote_unknown_peer():
    fe, bot = _make_frontend(remotes={"vps": RemoteSpec(
        url="http://1.2.3.4:8556")})
    await fe._command("/schedule list @nope")
    assert "unknown peer" in bot.sent[-1].lower()
```

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/test_telegram_schedule.py -v
```
Expected: FAIL — `/schedule list` and `/schedule show` not registered.

- [ ] **Step 3: Implement /schedule list and /schedule show**

Add to `src/aegis/telegram/commands.py`:

```python
def _fmt_schedule_table(entries: list[Any]) -> str:
    """Format a list of schedule snapshots as a monospace table.
    Each entry is either a SimpleNamespace (local) or dict (remote)."""
    if not entries:
        return "no schedules"
    lines = ["```",
             f"{'NAME':<22} {'SOURCE':<8} {'NEXT FIRE':<22} "
             f"{'ENABLED':<8} FIRES"]
    for e in entries:
        if isinstance(e, dict):
            name = e.get("name", "?")
            source = e.get("source", "?")
            next_fire = e.get("next_fire") or "—"
            enabled = "✓" if e.get("enabled", True) else "✗"
            fires = e.get("fire_count", 0)
        else:
            name = getattr(e, "name", "?")
            source = getattr(e, "source", "?")
            next_fire = getattr(e, "next_fire", None) or "—"
            enabled = "✓" if getattr(e, "enabled", True) else "✗"
            fires = getattr(e, "fire_count", 0)
        lines.append(f"{name:<22} {source:<8} {next_fire:<22} "
                     f"{enabled:<8} {fires}")
    lines.append("```")
    return "\n".join(lines)


async def _cmd_schedule_list(ctx: CmdContext, args: list[str]) -> None:
    if ctx.target is not None:
        remotes = getattr(ctx.cfg, "remotes", {}) or {}
        if ctx.target not in remotes:
            await ctx.reply(f"unknown peer {ctx.target!r}; "
                             f"known: {sorted(remotes)}")
            return
        from aegis.remote.client import remote_schedule_list
        result = await remote_schedule_list(remotes[ctx.target])
        if "error" in result:
            await ctx.reply(f"▸ remote error: {result['error']}")
            return
        entries = result.get("schedules", [])
        await ctx.reply(_fmt_schedule_table(entries))
        return

    scheduler = getattr(ctx.bridge, "scheduler", None)
    if scheduler is None:
        await ctx.reply("no scheduler configured on this serve")
        return
    entries = scheduler.snapshot()
    await ctx.reply(_fmt_schedule_table(entries))


register(Command(
    name="schedule list",
    summary="/schedule list [@peer] — list schedules with next-fire",
    detail=(
        "/schedule list [@<peer>]\n\n"
        "List every schedule on this serve (or @<peer>) with source "
        "(inline / overlay / pushed), next fire time, enabled state, "
        "and total fire count."
    ),
    handler=_cmd_schedule_list,
))


def _fmt_schedule_show(entry) -> str:
    """Format a single schedule's spec + runtime as a multi-line block."""
    lines = ["```"]
    if isinstance(entry, dict):
        # Remote: full Decision shape from remote_schedule_show.
        name = entry.get("name", "?")
        source = entry.get("source", "?")
        lines.append(f"schedule: {name}  (source: {source})")
        lines.append("")
        spec = entry.get("spec", {})
        for k, v in spec.items():
            lines.append(f"  {k}: {v}")
        runtime = entry.get("runtime") or {}
        if runtime:
            lines.append("")
            for k, v in runtime.items():
                lines.append(f"  {k}: {v}")
    else:
        name = getattr(entry, "name", "?")
        source = getattr(entry, "source", "?")
        lines.append(f"schedule: {name}  (source: {source})")
        lines.append("")
        spec = getattr(entry, "spec", {}) or {}
        for k, v in spec.items():
            lines.append(f"  {k}: {v}")
        for fld in ("next_fire", "last_fire", "fire_count",
                    "in_flight", "enabled"):
            val = getattr(entry, fld, None)
            if val is not None:
                lines.append(f"  {fld}: {val}")
    lines.append("```")
    return "\n".join(lines)


async def _cmd_schedule_show(ctx: CmdContext, args: list[str]) -> None:
    if not args:
        await ctx.reply("usage: /schedule show <name> [@peer]")
        return
    name = args[0]

    if ctx.target is not None:
        remotes = getattr(ctx.cfg, "remotes", {}) or {}
        if ctx.target not in remotes:
            await ctx.reply(f"unknown peer {ctx.target!r}; "
                             f"known: {sorted(remotes)}")
            return
        from aegis.remote.client import remote_schedule_show
        result = await remote_schedule_show(remotes[ctx.target], name)
        if "error" in result:
            await ctx.reply(f"▸ remote error: {result['error']}")
            return
        await ctx.reply(_fmt_schedule_show(result))
        return

    scheduler = getattr(ctx.bridge, "scheduler", None)
    if scheduler is None:
        await ctx.reply("no scheduler configured on this serve")
        return
    entry = scheduler.get(name)
    if entry is None:
        await ctx.reply(f"no such schedule {name!r}")
        return
    await ctx.reply(_fmt_schedule_show(entry))


register(Command(
    name="schedule show",
    summary="/schedule show <name> [@peer] — full spec + runtime",
    detail=(
        "/schedule show <name> [@<peer>]\n\n"
        "Print the full schedule spec (workflow, cron, args, "
        "lifecycle, ...) plus runtime fields (next_fire, last_fire, "
        "fire_count, in_flight, enabled) for one schedule."
    ),
    handler=_cmd_schedule_show,
))
```

- [ ] **Step 4: Run tests**

```
uv run pytest tests/test_telegram_schedule.py -v
```
Expected: PASS (7 tests).

- [ ] **Step 5: Run full suite + commit**

```bash
uv run pytest -q -m "not live" -x
git add src/aegis/telegram/commands.py tests/test_telegram_schedule.py
git commit -m "feat(telegram): /schedule list + /schedule show with @peer routing"
```

---

## Task 8: /schedule run

**Files:**
- Modify: `src/aegis/telegram/commands.py` (add /schedule run handler)
- Test: extend `tests/test_telegram_schedule.py`

Local-only mutation. `bridge.scheduler.fire_now(name)`. `@peer` returns a clear "not yet supported cross-host" error.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_telegram_schedule.py`:

```python
@pytest.mark.asyncio
async def test_schedule_run_local():
    fired: list[str] = []
    class _Sched:
        def fire_now(self, name): fired.append(name)
        def get(self, name):
            return SimpleNamespace(name=name) if name == "nb" else None
    fe, bot = _make_frontend(scheduler=_Sched())
    await fe._command("/schedule run nb")
    assert fired == ["nb"]
    assert "fired" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_schedule_run_unknown_schedule_errors():
    class _Sched:
        def fire_now(self, name): raise KeyError(name)
        def get(self, name): return None
    fe, bot = _make_frontend(scheduler=_Sched())
    await fe._command("/schedule run ghost")
    assert "no such schedule" in bot.sent[-1].lower() \
        or "unknown" in bot.sent[-1].lower() \
        or "error" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_schedule_run_missing_arg():
    fe, bot = _make_frontend(scheduler=SimpleNamespace())
    await fe._command("/schedule run")
    assert "usage" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_schedule_run_rejects_at_peer():
    fe, bot = _make_frontend(scheduler=SimpleNamespace(),
                              remotes={"vps": RemoteSpec(
                                  url="http://1.2.3.4:8556")})
    await fe._command("/schedule run nb @vps")
    out = bot.sent[-1].lower()
    assert "cross-host" in out or "local only" in out
```

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/test_telegram_schedule.py::test_schedule_run_local -v
```
Expected: FAIL — `/schedule run` not registered.

- [ ] **Step 3: Implement /schedule run**

Add to `src/aegis/telegram/commands.py`:

```python
async def _cmd_schedule_run(ctx: CmdContext, args: list[str]) -> None:
    if ctx.target is not None:
        await ctx.reply(
            "▸ /schedule run not yet supported cross-host "
            "(this serve only). Drop @<peer>.")
        return
    if not args:
        await ctx.reply("usage: /schedule run <name>")
        return
    name = args[0]
    scheduler = getattr(ctx.bridge, "scheduler", None)
    if scheduler is None:
        await ctx.reply("no scheduler configured on this serve")
        return
    entry_before = scheduler.get(name)
    if entry_before is None:
        await ctx.reply(f"no such schedule {name!r}")
        return
    try:
        scheduler.fire_now(name)
    except Exception as e:
        await ctx.reply(f"▸ error firing {name!r}: {e}")
        return
    next_fire = getattr(entry_before, "next_fire", None) or "—"
    await ctx.reply(
        f"▸ fired schedule {name!r}\n"
        f"  next regular fire still at {next_fire}")


register(Command(
    name="schedule run",
    summary="/schedule run <name> — fire a schedule now (this serve only)",
    detail=(
        "/schedule run <name>\n\n"
        "Fire-now a schedule on this serve. The next regular fire "
        "tick is unaffected. Local only — cross-host fire-now is not "
        "yet a substrate feature (deferred from v0.8); use @<peer> "
        "and the substrate will reject with a clear error."
    ),
    handler=_cmd_schedule_run,
))
```

- [ ] **Step 4: Run tests + commit**

```bash
uv run pytest tests/test_telegram_schedule.py -v
uv run pytest -q -m "not live" -x
git add src/aegis/telegram/commands.py tests/test_telegram_schedule.py
git commit -m "feat(telegram): /schedule run <name> — local fire-now"
```

---

## Task 9: /budget list + /budget show

**Files:**
- Modify: `src/aegis/telegram/commands.py` (add two handlers)
- Test: `tests/test_telegram_budget.py` (new)

Local: walk `bridge.queue_manager._queues`, run `evaluate_budgets` per queue with budgets. Remote: `remote_budget_list(spec)` / `remote_budget_show(spec, queue)`. Both honor `@peer`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_telegram_budget.py`:

```python
from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from aegis.budget.budgets import Budget
from aegis.budget.windows import parse_window
from aegis.remote.config import RemoteSpec
from aegis.telegram.commands import COMMANDS


@pytest.fixture(autouse=True)
def _clean_registry():
    snap = dict(COMMANDS)
    yield
    COMMANDS.clear()
    COMMANDS.update(snap)


def _make_frontend(queues=None, remotes=None):
    from aegis.telegram.frontend import TelegramFrontend

    class _Bot:
        sent: list[str] = []
        async def send_message(self, chat, text, markdown=False):
            self.sent.append(text); return 1
        async def edit_message(self, *a, **k): return None

    class _Mgr:
        def list_sessions(self): return []
        def list_agents(self): return []

    class _QM:
        def __init__(self, queues): self._queues = queues or {}
        def _load_recent_jsonl(self, queue, max_age): return []

    class _Bridge:
        def __init__(self, qm): self.queue_manager = qm
        scheduler = None

    class _Cfg:
        def __init__(self, remotes=None): self.remotes = remotes or {}

    bot = _Bot()
    fe = TelegramFrontend(
        bot, _Mgr(), _Bridge(_QM(queues)), _Cfg(remotes),
        chat_id=42, auto_prompt="")
    return fe, bot


def _q(name, budgets=None):
    """Make a Queue dataclass instance with optional budgets list."""
    from aegis.queue.schema import Queue
    return Queue(name=name, agent_profile="opus", max_parallel=1,
                 provider="claude-code", model="opus",
                 budgets=budgets or [])


@pytest.mark.asyncio
async def test_budget_list_no_queues():
    fe, bot = _make_frontend(queues={})
    await fe._command("/budget list")
    assert "no queues" in bot.sent[-1].lower() \
        or bot.sent[-1].strip().endswith("```")


@pytest.mark.asyncio
async def test_budget_list_local_summarizes_per_queue():
    fe, bot = _make_frontend(queues={
        "impl": _q("impl", budgets=[
            Budget("usd", Decimal("1.00"), "1h", parse_window("1h"))
        ]),
        "fast": _q("fast"),  # no budget
    })
    await fe._command("/budget list")
    out = bot.sent[-1]
    assert "impl" in out
    assert "fast" in out


@pytest.mark.asyncio
async def test_budget_show_local_no_budget():
    fe, bot = _make_frontend(queues={"fast": _q("fast")})
    await fe._command("/budget show fast")
    out = bot.sent[-1].lower()
    assert "no budget" in out or "no budgets" in out


@pytest.mark.asyncio
async def test_budget_show_unknown_queue():
    fe, bot = _make_frontend(queues={})
    await fe._command("/budget show ghost")
    assert "unknown queue" in bot.sent[-1].lower() \
        or "no such queue" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_budget_show_missing_arg():
    fe, bot = _make_frontend(queues={})
    await fe._command("/budget show")
    assert "usage" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_budget_list_remote_routes(monkeypatch):
    captured = {}
    async def fake_list(spec):
        captured["spec"] = spec
        return {"queues": [
            {"name": "impl", "budgets_count": 1, "status": "ok",
             "binding": "$0.30/$1.00 1h", "unblock_at": None},
        ]}
    monkeypatch.setattr("aegis.remote.client.remote_budget_list",
                        fake_list)
    fe, bot = _make_frontend(remotes={
        "vps": RemoteSpec(url="http://1.2.3.4:8556"),
    })
    await fe._command("/budget list @vps")
    assert captured["spec"].url == "http://1.2.3.4:8556"
    assert "impl" in bot.sent[-1]


@pytest.mark.asyncio
async def test_budget_show_remote_routes(monkeypatch):
    captured = {}
    async def fake_show(spec, queue):
        captured["queue"] = queue
        return {"name": "impl", "allowed": True, "checks": [
            {"constraint": "usd", "limit": "1.00", "spent": "0.30",
             "window": "1h", "allowed": True, "headroom": "0.70"},
        ], "blocked_by": [], "unblock_at": None}
    monkeypatch.setattr("aegis.remote.client.remote_budget_show",
                        fake_show)
    fe, bot = _make_frontend(remotes={
        "vps": RemoteSpec(url="http://1.2.3.4:8556"),
    })
    await fe._command("/budget show impl @vps")
    assert captured["queue"] == "impl"
    assert "0.30" in bot.sent[-1]
```

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/test_telegram_budget.py -v
```
Expected: FAIL — `/budget list` and `/budget show` not registered.

- [ ] **Step 3: Implement /budget list and /budget show**

Add to `src/aegis/telegram/commands.py`:

```python
async def _cmd_budget_list(ctx: CmdContext, args: list[str]) -> None:
    if ctx.target is not None:
        remotes = getattr(ctx.cfg, "remotes", {}) or {}
        if ctx.target not in remotes:
            await ctx.reply(f"unknown peer {ctx.target!r}; "
                             f"known: {sorted(remotes)}")
            return
        from aegis.remote.client import remote_budget_list
        result = await remote_budget_list(remotes[ctx.target])
        if "error" in result:
            await ctx.reply(f"▸ remote error: {result['error']}")
            return
        rows = result.get("queues", [])
        await ctx.reply(_fmt_budget_list(rows))
        return

    qm = getattr(ctx.bridge, "queue_manager", None)
    if qm is None:
        await ctx.reply("no queue manager on this serve")
        return
    queues = getattr(qm, "_queues", {})
    if not queues:
        await ctx.reply("no queues configured")
        return
    from datetime import datetime, timezone
    from aegis.budget.evaluator import evaluate_budgets
    now = datetime.now(timezone.utc)
    rows = []
    for name, q in queues.items():
        budgets = getattr(q, "budgets", []) or []
        if not budgets:
            rows.append({"name": name, "budgets_count": 0,
                          "status": "no-budget", "binding": None})
            continue
        tail = qm._load_recent_jsonl(
            name, max_age=max(b.window for b in budgets))
        d = evaluate_budgets(tail, budgets, now)
        if d.allowed:
            rows.append({"name": name, "budgets_count": len(budgets),
                          "status": "ok", "binding": None})
        else:
            c = d.blocked_by[0]
            binding = (f"${c.spent} of ${c.limit} / {c.window_str}"
                        if c.constraint == "usd"
                        else f"{c.spent}/{c.limit} {c.constraint}/{c.window_str}")
            rows.append({"name": name, "budgets_count": len(budgets),
                          "status": "blocked", "binding": binding,
                          "unblock_at": d.unblock_at.isoformat().replace(
                              "+00:00", "Z") if d.unblock_at else None})
    await ctx.reply(_fmt_budget_list(rows))


def _fmt_budget_list(rows: list[dict]) -> str:
    if not rows:
        return "no queues"
    lines = ["```",
             f"{'QUEUE':<14} {'BUDGETS':<8} {'STATUS':<28} UNBLOCKS"]
    for r in rows:
        name = r.get("name", "?")
        count = r.get("budgets_count", 0)
        status_raw = r.get("status", "?")
        if status_raw == "blocked":
            status = f"⛔ {r.get('binding') or 'over'}"
            unblock = r.get("unblock_at") or "—"
        elif status_raw == "ok":
            status = f"✓ {r.get('binding') or 'within budget'}"
            unblock = "—"
        elif status_raw == "no-budget":
            status = "— no budget"
            unblock = "—"
        else:
            status = status_raw
            unblock = r.get("unblock_at") or "—"
        lines.append(f"{name:<14} {count:<8} {status:<28} {unblock}")
    lines.append("```")
    return "\n".join(lines)


register(Command(
    name="budget list",
    summary="/budget list [@peer] — per-queue budget status",
    detail=(
        "/budget list [@<peer>]\n\n"
        "Summarize each queue's budget headroom. Shows the binding "
        "(tightest) constraint per queue, status (ok / blocked / "
        "no-budget), and unblock ETA for blocked queues."
    ),
    handler=_cmd_budget_list,
))


async def _cmd_budget_show(ctx: CmdContext, args: list[str]) -> None:
    if not args:
        await ctx.reply("usage: /budget show <queue> [@peer]")
        return
    queue = args[0]

    if ctx.target is not None:
        remotes = getattr(ctx.cfg, "remotes", {}) or {}
        if ctx.target not in remotes:
            await ctx.reply(f"unknown peer {ctx.target!r}; "
                             f"known: {sorted(remotes)}")
            return
        from aegis.remote.client import remote_budget_show
        result = await remote_budget_show(remotes[ctx.target], queue)
        if "error" in result:
            await ctx.reply(f"▸ remote error: {result['error']}")
            return
        await ctx.reply(_fmt_budget_show(result))
        return

    qm = getattr(ctx.bridge, "queue_manager", None)
    if qm is None:
        await ctx.reply("no queue manager on this serve")
        return
    queues = getattr(qm, "_queues", {})
    if queue not in queues:
        await ctx.reply(f"unknown queue {queue!r}")
        return
    q = queues[queue]
    budgets = getattr(q, "budgets", []) or []
    if not budgets:
        await ctx.reply(f"queue {queue!r} has no budgets configured")
        return
    from datetime import datetime, timezone
    from aegis.budget.evaluator import evaluate_budgets
    tail = qm._load_recent_jsonl(
        queue, max_age=max(b.window for b in budgets))
    d = evaluate_budgets(tail, budgets, datetime.now(timezone.utc))
    payload = {
        "name": queue, "allowed": d.allowed,
        "checks": [{"constraint": c.constraint, "limit": str(c.limit),
                      "spent": str(c.spent), "window": c.window_str,
                      "allowed": c.allowed, "headroom": str(c.headroom)}
                     for c in d.checks],
        "blocked_by": [{"constraint": c.constraint, "window": c.window_str}
                        for c in d.blocked_by],
        "unblock_at": (d.unblock_at.isoformat().replace("+00:00", "Z")
                        if d.unblock_at else None),
    }
    await ctx.reply(_fmt_budget_show(payload))


def _fmt_budget_show(payload: dict) -> str:
    name = payload.get("name", "?")
    lines = ["```", f"budget for queue {name!r}", ""]
    lines.append(f"{'CONSTRAINT':<16} {'LIMIT':<10} {'SPENT':<10} "
                  f"{'WINDOW':<8} {'HEADROOM':<10} STATUS")
    for c in payload.get("checks", []):
        status = "✓" if c.get("allowed") else "⛔"
        lines.append(f"{c['constraint']:<16} {c['limit']:<10} "
                      f"{c['spent']:<10} {c['window']:<8} "
                      f"{c['headroom']:<10} {status}")
    if not payload.get("allowed", True):
        n = len(payload.get("blocked_by", []))
        unblock = payload.get("unblock_at") or "—"
        lines.append("")
        lines.append(f"blocked by {n} budget(s); unblocks at {unblock}")
    lines.append("```")
    return "\n".join(lines)


register(Command(
    name="budget show",
    summary="/budget show <queue> [@peer] — full Decision per BudgetCheck",
    detail=(
        "/budget show <queue> [@<peer>]\n\n"
        "Print every budget on a queue with spent / limit / headroom "
        "/ window / status. Blocked queues also include the "
        "unblock_at ETA."
    ),
    handler=_cmd_budget_show,
))
```

- [ ] **Step 4: Run tests + commit**

```bash
uv run pytest tests/test_telegram_budget.py -v
uv run pytest -q -m "not live" -x
git add src/aegis/telegram/commands.py tests/test_telegram_budget.py
git commit -m "feat(telegram): /budget list + /budget show with @peer routing"
```

---

## Task 10: /queue list + /queue show

**Files:**
- Modify: `src/aegis/telegram/commands.py` (add two handlers)
- Test: `tests/test_telegram_queue.py` (new)

Local-only in v0.10 (no `GET /remote/v1/queue` endpoint exists). `@peer` returns "not yet supported cross-host" error. List walks `_queues` + per-queue counts + last task. Show walks `_pending[name]` + `_inflight[name]` + last 10 JSONL records.

- [ ] **Step 1: Write failing tests**

Create `tests/test_telegram_queue.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aegis.queue.schema import Queue, Task
from aegis.remote.config import RemoteSpec
from aegis.telegram.commands import COMMANDS


@pytest.fixture(autouse=True)
def _clean_registry():
    snap = dict(COMMANDS)
    yield
    COMMANDS.clear()
    COMMANDS.update(snap)


def _q(name, max_parallel=1):
    return Queue(name=name, agent_profile="opus",
                 max_parallel=max_parallel, provider="claude-code",
                 model="opus", budgets=[])


def _task(tid="t1", queue="impl", status="pending", payload="x"):
    return Task(id=tid, queue=queue, payload=payload,
                enqueued_by="agent:p",
                enqueued_at="2026-05-26T10:00:00Z",
                callback=False, status=status)


def _make_frontend(queues=None, pending=None, inflight=None, tmp_path=None,
                   remotes=None):
    from aegis.telegram.frontend import TelegramFrontend

    class _Bot:
        sent: list[str] = []
        async def send_message(self, chat, text, markdown=False):
            self.sent.append(text); return 1
        async def edit_message(self, *a, **k): return None

    class _Mgr:
        def list_sessions(self): return []
        def list_agents(self): return []

    class _QM:
        def __init__(self, queues, pending, inflight, tmp_path):
            self._queues = queues or {}
            self._pending = pending or {n: [] for n in self._queues}
            self._inflight = inflight or {n: [] for n in self._queues}
            self._state_dir = tmp_path
        def _load_recent_jsonl(self, queue, max_age): return []

    class _Bridge:
        def __init__(self, qm):
            self.queue_manager = qm
        scheduler = None

    class _Cfg:
        def __init__(self, remotes=None): self.remotes = remotes or {}

    bot = _Bot()
    fe = TelegramFrontend(
        bot, _Mgr(),
        _Bridge(_QM(queues, pending, inflight, tmp_path)),
        _Cfg(remotes), chat_id=42, auto_prompt="")
    return fe, bot


@pytest.mark.asyncio
async def test_queue_list_empty():
    fe, bot = _make_frontend(queues={})
    await fe._command("/queue list")
    assert "no queues" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_queue_list_shows_every_queue():
    fe, bot = _make_frontend(queues={
        "impl": _q("impl"), "fast": _q("fast"),
    })
    await fe._command("/queue list")
    out = bot.sent[-1]
    assert "impl" in out
    assert "fast" in out


@pytest.mark.asyncio
async def test_queue_list_at_peer_rejects():
    fe, bot = _make_frontend(queues={}, remotes={
        "vps": RemoteSpec(url="http://1.2.3.4:8556"),
    })
    await fe._command("/queue list @vps")
    out = bot.sent[-1].lower()
    assert "cross-host" in out or "local only" in out


@pytest.mark.asyncio
async def test_queue_show_unknown():
    fe, bot = _make_frontend(queues={})
    await fe._command("/queue show ghost")
    assert "unknown queue" in bot.sent[-1].lower() \
        or "no such queue" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_queue_show_local(tmp_path):
    fe, bot = _make_frontend(
        queues={"impl": _q("impl")},
        pending={"impl": [_task(tid="p1", payload="pending one")]},
        inflight={"impl": [_task(tid="i1", status="dispatched",
                                  payload="in flight one")]},
        tmp_path=tmp_path)
    await fe._command("/queue show impl")
    out = bot.sent[-1]
    assert "impl" in out
    assert "p1" in out or "pending" in out.lower()
    assert "i1" in out or "in flight" in out.lower()


@pytest.mark.asyncio
async def test_queue_show_missing_arg():
    fe, bot = _make_frontend(queues={"impl": _q("impl")})
    await fe._command("/queue show")
    assert "usage" in bot.sent[-1].lower()
```

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/test_telegram_queue.py -v
```
Expected: FAIL — `/queue list` and `/queue show` not registered.

- [ ] **Step 3: Implement /queue list and /queue show**

Add to `src/aegis/telegram/commands.py`:

```python
async def _cmd_queue_list(ctx: CmdContext, args: list[str]) -> None:
    if ctx.target is not None:
        await ctx.reply(
            "▸ /queue list not yet supported cross-host "
            "(local only). Drop @<peer>.")
        return
    qm = getattr(ctx.bridge, "queue_manager", None)
    if qm is None:
        await ctx.reply("no queue manager on this serve")
        return
    queues = getattr(qm, "_queues", {})
    if not queues:
        await ctx.reply("no queues configured")
        return
    lines = ["```",
             f"{'QUEUE':<14} {'AGENT':<14} {'DEPTH':<6} {'IN-FLIGHT':<10} LAST"]
    for name in sorted(queues):
        q = queues[name]
        depth = len(getattr(qm, "_pending", {}).get(name, []))
        in_flight = len(getattr(qm, "_inflight", {}).get(name, []))
        agent = getattr(q, "agent_profile", "?")
        # Find most recent task in qm._all for this queue.
        all_tasks = getattr(qm, "_all", {})
        recent = sorted(
            (t for t in all_tasks.values() if t.queue == name
             and t.status in ("completed", "failed")),
            key=lambda t: getattr(t, "completed_at", "") or "",
            reverse=True,
        )
        if recent:
            last = recent[0]
            marker = "✓" if last.status == "completed" else "✗"
            last_str = f"{marker} task#{last.id[:8]}"
        else:
            last_str = "— none"
        lines.append(
            f"{name:<14} {agent:<14} {depth:<6} {in_flight:<10} {last_str}")
    lines.append("```")
    await ctx.reply("\n".join(lines))


register(Command(
    name="queue list",
    summary="/queue list — per-queue depth + in-flight + last task",
    detail=(
        "/queue list\n\n"
        "Local-only in v0.10 (no cross-host queue endpoint yet). "
        "Shows each queue's bound agent profile, pending depth, "
        "in-flight count, and last terminal task."
    ),
    handler=_cmd_queue_list,
))


async def _cmd_queue_show(ctx: CmdContext, args: list[str]) -> None:
    if ctx.target is not None:
        await ctx.reply(
            "▸ /queue show not yet supported cross-host "
            "(local only). Drop @<peer>.")
        return
    if not args:
        await ctx.reply("usage: /queue show <name>")
        return
    name = args[0]
    qm = getattr(ctx.bridge, "queue_manager", None)
    if qm is None:
        await ctx.reply("no queue manager on this serve")
        return
    queues = getattr(qm, "_queues", {})
    if name not in queues:
        await ctx.reply(f"unknown queue {name!r}")
        return
    q = queues[name]
    pending = getattr(qm, "_pending", {}).get(name, [])
    inflight = getattr(qm, "_inflight", {}).get(name, [])
    lines = ["```",
             f"queue: {name}  (agent: {q.agent_profile}, "
             f"max_parallel: {q.max_parallel})", ""]
    if inflight:
        lines.append("IN-FLIGHT")
        for t in inflight:
            handle = getattr(t, "worker_handle", "?") or "?"
            payload = (t.payload or "")[:60]
            lines.append(f"  ⏳ task#{t.id[:8]}  worker:{handle}  "
                          f"payload={payload!r}")
        lines.append("")
    if pending:
        lines.append("PENDING")
        for t in pending:
            payload = (t.payload or "")[:60]
            lines.append(f"  ○ task#{t.id[:8]}  enqueued {t.enqueued_at}  "
                          f"by {t.enqueued_by}  payload={payload!r}")
        lines.append("")
    # Recent terminal tasks: walk qm._all filtered to this queue.
    all_tasks = getattr(qm, "_all", {})
    recent = sorted(
        (t for t in all_tasks.values() if t.queue == name
         and t.status in ("completed", "failed")),
        key=lambda t: getattr(t, "completed_at", "") or "",
        reverse=True,
    )[:10]
    if recent:
        lines.append("RECENT")
        for t in recent:
            marker = "✓" if t.status == "completed" else "✗"
            lines.append(f"  {marker} task#{t.id[:8]}  {t.status}  "
                          f"{getattr(t, 'completed_at', '?')}")
    if not (inflight or pending or recent):
        lines.append("  (no tasks)")
    lines.append("```")
    await ctx.reply("\n".join(lines))


register(Command(
    name="queue show",
    summary="/queue show <name> — pending + in-flight + recent",
    detail=(
        "/queue show <name>\n\n"
        "Local-only in v0.10. Shows the queue's pending tasks "
        "(awaiting dispatch), in-flight tasks (active workers), and "
        "up to 10 most-recent terminal tasks. Payloads are truncated "
        "to 60 characters."
    ),
    handler=_cmd_queue_show,
))
```

- [ ] **Step 4: Run tests + commit**

```bash
uv run pytest tests/test_telegram_queue.py -v
uv run pytest -q -m "not live" -x
git add src/aegis/telegram/commands.py tests/test_telegram_queue.py
git commit -m "feat(telegram): /queue list + /queue show (local-only in v0.10)"
```

---

## Task 11: Docs + release (v0.10.0)

**Files:**
- Create: `docs/telegram.md`
- Modify: `docs/configuration.md` (Telegram section grows command list)
- Modify: `docs/index.md` (mention substrate commands)
- Modify: `docs/roadmap.md` (v0.10.0 entry)
- Modify: `mkdocs.yml` (Telegram entry)
- Modify: `README.md` (Telegram section)
- Modify: `CHANGELOG.md` (`[0.10.0]` entry)
- Modify: `pyproject.toml` (version bump 0.9.0 → 0.10.0)
- Modify: `uv.lock` (matching version bump)

- [ ] **Step 1: Write `docs/telegram.md`**

User-facing doc covering: setup, the bot account requirements, the existing five verbs, the nine new substrate verbs, `@<peer>` cross-host syntax, output formatting (plain + fenced code), examples per resource, FAQ ("what's not supported in v0.10"). Pull paragraphs from the spec but rewrite in second person.

- [ ] **Step 2: Sync `docs/configuration.md`**

In the existing `## Headless / Telegram` section, the command table currently shows only five verbs. Add the new ones with a sentence pointing readers to `docs/telegram.md` for detail.

- [ ] **Step 3: Sync `docs/index.md` + `mkdocs.yml`**

In `docs/index.md` "What's also in the box", add:

```markdown
- **Telegram substrate commands.** `/queue`, `/schedule`, `/budget`, `/peers`, plus session-spawn verbs — every aegis substrate is now reachable from the phone, with optional `@<peer>` for cross-host inspection. See [Telegram](telegram.md).
```

In `mkdocs.yml` `nav.Concepts`, add `- Telegram: telegram.md`.

- [ ] **Step 4: Sync `docs/roadmap.md`**

Above `### v0.9.0`:

```markdown
### v0.10.0 (current)
- **Telegram substrate commands.** Nine new chat commands —
  `/queue list/show`, `/schedule list/show/run`, `/budget list/show`,
  `/peers`, `/help` — wired through a command registry. Cross-host
  via `@<peer>` syntax where the substrate already supports it
  (schedule + budget); queue + schedule-run are local-only this
  round. Existing five verbs (`/new`, `/close`, `/interrupt`,
  `/agents`, `/sessions`) migrated into the same registry; `/help`
  is now registry-driven.
```

- [ ] **Step 5: Sync `README.md`**

Replace the existing Telegram block with the new command list. Add `- [Telegram](https://apiad.github.io/aegis/telegram/)` to the docs link list.

- [ ] **Step 6: `CHANGELOG.md` `[0.10.0]` entry**

Above `## [0.9.0]`:

```markdown
## [0.10.0] - 2026-05-26

### Added
- **Telegram substrate command surface.** Nine new chat commands
  reach every existing substrate from the phone:
  - `/queue list` + `/queue show <name>` — local-only (no cross-host
    queue endpoint yet).
  - `/schedule list [@peer]` + `/schedule show <name> [@peer]` +
    `/schedule run <name>` (local-only fire-now).
  - `/budget list [@peer]` + `/budget show <queue> [@peer]`.
  - `/peers` — list configured remotes with reachability probe.
  - `/help` + `/help <name>` — registry-driven.
- **Command registry** in `src/aegis/telegram/commands.py`. The five
  existing verbs (`/new`, `/close`, `/interrupt`, `/agents`,
  `/sessions`) migrated into the same registry; single source of
  truth for `/help`.
- **`@<peer>` cross-host syntax** parsed by the dispatcher. Each
  handler decides whether to honor it; commands that don't support
  cross-host return a clear error.
- **Plain-text output by default; tabular data in fenced code
  blocks** for proper monospace alignment on mobile. No
  MarkdownV2-escape gymnastics in any new command.

### Changed
- `TelegramFrontend.__init__` grows `bridge` and `cfg` positional
  params. Existing `aegis serve` wire-up updated; no external API
  change.

Spec: `docs/superpowers/specs/2026-05-26-aegis-telegram-substrate-commands-design.md`.
```

- [ ] **Step 7: Bump version**

```bash
sed -i 's/^version = "0\.9\.0"$/version = "0.10.0"/' pyproject.toml
sed -i '0,/^version = "0\.9\.0"$/s//version = "0.10.0"/' uv.lock
grep -nE '^name = "aegis-harness"|^version = ' uv.lock | head -4
grep '^version' pyproject.toml
```
Expected: both at `0.10.0`.

- [ ] **Step 8: Final gate**

```
uv run pytest -q -m "not live" -x
```
Expected: all green.

- [ ] **Step 9: Release commit + tag + push**

```bash
git add docs/telegram.md docs/configuration.md docs/index.md \
        docs/roadmap.md mkdocs.yml README.md CHANGELOG.md \
        pyproject.toml uv.lock
git commit -m "release: 0.10.0 — telegram substrate command surface"
git pull --rebase
git tag -a v0.10.0 -m "v0.10.0 — telegram substrate commands. See CHANGELOG.md."
git push origin main
git push origin v0.10.0
```

- [ ] **Step 10: Confirm PyPI (with caveat)**

```bash
sleep 30
curl -sS https://pypi.org/pypi/aegis-harness/json | \
  python3 -c "import sys,json;d=json.load(sys.stdin);print('latest:',d['info']['version'])"
```

**Caveat from the v0.9.0 publish situation:** the release workflow was unstable as of 2026-05-26. If PyPI still shows `0.9.0` (or older) after 60 seconds, **do not try to fix the workflow** — file a blocker note at
`vault/+/Inbox/for_claude/2026-05-26-aegis-v0.10-publish-blocker.md` summarising the workflow's failure mode (run id + error message from `gh run view <id>`) and exit cleanly. Alex will deal with the publish workflow separately.

- [ ] **Step 11: Notify Telegram**

```bash
bin/notify-telegram.sh "🎉 aegis 0.10.0 released — telegram substrate command surface" || true
```

(If PyPI didn't pick up the publish, omit this ping and let the blocker note speak for itself.)

---

## Self-review

**Spec coverage:**

| Spec section | Implementation task |
|---|---|
| Motivation | (context only) |
| Non-goals | enforced by absence — no /queue cancel, no /schedule enable, etc. |
| Architecture overview | Tasks 1–3 (registry + dispatcher + plumbing) |
| Command registry | Task 1 |
| Dispatch path + @peer parsing | Task 3 |
| Cross-host resolution helper | Task 1 (`resolve_remote`) |
| Resource verbs — queue | Task 10 |
| Resource verbs — schedule list/show | Task 7 |
| Resource verbs — schedule run | Task 8 |
| Resource verbs — budget | Task 9 |
| Resource verbs — peers | Task 6 |
| /help | Task 5 |
| Migrated verbs (new/close/interrupt/agents/sessions) | Task 4 |
| Plumbing — bridge + cfg | Task 2 |
| Output formatting (plain + fenced) | Embedded in each handler |
| Error model | Each handler emits the spec's error shapes |
| Testing | Tests in every task; no live tests |
| Implementation sketch | Tasks 1–10 collectively |
| Open questions | Q1 reachability check via `GET /remote/v1/` 3s probe (Task 6); Q2 frontend_state via `ctx.frontend` field (Task 4); Q3 /<handle> alias stays out of registry (Task 3) |

**Placeholder scan:** No "TBD" / "implement later" / "similar to Task N" in any task. Every test has runnable code; every implementation step shows the full new function body. The release task (Task 11) explicitly handles the PyPI-publish ambiguity from v0.9.0 without delegating to "future work".

**Type consistency:** `Command(name, summary, detail, handler)` consistent across Tasks 1 + 4 + 5 + 6 + 7 + 8 + 9 + 10. `CmdContext(bridge, cfg, manager, target, reply, frontend)` consistent (Task 1 defines; Tasks 3-10 read). The `frontend` field on `CmdContext` is mentioned in Task 1 and used by the migrated handlers in Task 4 — type-consistent. Test helpers `_make_frontend()` / `_q()` / `_task()` are local to each test file (no cross-test imports), so signature drift between tests doesn't matter.

**Plan-vs-reality verification:** every test or impl step uses a symbol verified against `main` (commit `019bc2c`):
- `Queue(name, agent_profile, max_parallel, provider, model, budgets)` from `src/aegis/queue/schema.py`
- `Task(id, queue, payload, enqueued_by, enqueued_at, callback, status, ...)` from the same
- `QueueManager._queues`, `_pending`, `_inflight`, `_all`, `_load_recent_jsonl` (all underscored — private API but stable)
- `Scheduler.snapshot() / get(name) / fire_now(name)` from `src/aegis/scheduler/scheduler.py`
- `evaluate_budgets`, `BudgetCheck`, `Decision` from `src/aegis/budget/evaluator.py`
- `Budget`, `parse_window` from `src/aegis/budget/budgets.py`, `windows.py`
- `RemoteSpec` from `src/aegis/remote/config.py`
- `remote_budget_list/show`, `remote_schedule_list/show` from `src/aegis/remote/client.py`

Plan complete.
