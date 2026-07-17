# Slash Commands 2B — Full builtin coverage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the operator-useful `AppBridge` surface as builtin slash commands — `/groups /schedules /terminals /themes /clear /rename /close`, plus agent management on `/agents` and a bare-list convention — so aegis can be driven from the keyboard, in both the TUI and the web client.

**Architecture:** Split `builtins.py` into a `builtins/` package (one module per command family), each registering on import. Every command is a thin call over an existing bridge attribute, a `config.edit`/`scheduler.push` helper, or the one new **effect channel** (`CommandResult.effect`) — a plain dict the frontend seams apply after mounting the result block (for `/themes` and `/clear`). One small new bridge method (`list_groups`) and one Textual-free `THEME_NAMES` constant are the only additions beyond thin calls.

**Tech Stack:** Python 3.13+, `dataclasses`, pytest (`-m "not live"`), Textual 8.x (TUI seam only), vanilla JS (web client).

## Global Constraints

- Python **3.13+**.
- Package manager is **`uv`** — `uv run python -m pytest`, `uv pip install -e .`. Never bare `pip`.
- Test selector is **`-m "not live"`** (marker), never `-k "not live"` (substring bug).
- TDD: failing test first, minimal implementation, commit per logical unit.
- The commands core (`src/aegis/commands/`) stays **harness-agnostic** — no Textual/web imports. `CommandResult.effect` is a plain dict; the frontends interpret it. `/themes` sources names from a Textual-free constant.
- Bare noun-command === its `list` (uniform across `/agents /sessions /groups /schedules /terminals /themes /queues`). Collection nouns are plural; action verbs singular.
- Run the gate as its own step; **never** pipe pytest/ruff through `tail` in an `&&` chain (masks the exit code).
- TUI/watchdog tests flake on zion (inotify limit) — re-run a failing TUI test alone before treating it as real.
- Fast hermetic gate during iteration: `uv run python -m pytest tests/test_slash_commands.py tests/test_command_registry.py tests/test_command_args.py -q` (add pane/web tests as they land).
- Before multi-file writing, hold a ws-lock: `cd /home/apiad/Workspace && bin/ws-lock acquire repos/aegis/src repos/aegis/tests --desc "slash commands 2B"`; `bin/ws-lock gc` at the end.

---

### Task 1: Split `builtins.py` → `builtins/` package (pure refactor)

Pure move, no behavior change. The verification is that the existing suite stays green.

**Files:**
- Delete: `src/aegis/commands/builtins.py`
- Create: `src/aegis/commands/builtins/__init__.py`
- Create: `src/aegis/commands/builtins/core.py`
- Verify: `src/aegis/commands/__init__.py` bottom import (`from aegis.commands import builtins as _builtins`) — unchanged; a package import resolves the same way.

**Interfaces:**
- Consumes: `REGISTRY`, `CommandContext`, `CommandResult`, `SlashCommand`, `register` from `aegis.commands`; `Arg`, `ArgSpec`, `Flag` from `aegis.commands.args`.
- Produces: the six 2A builtins registered on import, exactly as before (`help/sessions/agents/spawn/queue/enqueue`), now living in `builtins/core.py`.

- [ ] **Step 1: Confirm the suite is green before moving**

Run: `uv run python -m pytest tests/test_slash_commands.py tests/test_command_registry.py -q`
Expected: PASS (baseline).

- [ ] **Step 2: Move the file into the package**

```bash
cd /home/apiad/Workspace/repos/aegis
git mv src/aegis/commands/builtins.py src/aegis/commands/builtins/core.py
```

- [ ] **Step 3: Create the package `__init__.py`**

```python
# src/aegis/commands/builtins/__init__.py
"""Builtin slash commands, one module per command family. Importing this
package imports every submodule for its registration side-effects, so
``from aegis.commands import builtins`` (at the bottom of the commands
package) wires up the whole builtin set."""
from aegis.commands.builtins import core as _core  # noqa: F401
```

- [ ] **Step 4: Run the suite to verify the move is transparent**

Run: `uv run python -m pytest tests/test_slash_commands.py tests/test_command_registry.py -q`
Expected: PASS — identical behavior; the six commands still register.

- [ ] **Step 5: Commit**

```bash
git add -A src/aegis/commands/
git commit -m "refactor(commands): builtins.py -> builtins/ package (core.py)"
```

---

### Task 2: Effect channel — `CommandResult.effect` + web frame + `THEME_NAMES`

Add the one new core concept and the Textual-free theme-name constant. No command emits an effect yet (Tasks 9–10 do); this task lands the field, the web-frame plumbing, and the constant, each unit-tested.

**Files:**
- Modify: `src/aegis/commands/__init__.py` (add `effect` to `CommandResult`)
- Create: `src/aegis/theme_names.py`
- Modify: `src/aegis/web/wssession.py` (`_deliver_or_command` includes `effect`)
- Test: `tests/test_command_registry.py` (effect field), `tests/test_web_slash.py` (frame carries effect)

**Interfaces:**
- Consumes: nothing new.
- Produces: `CommandResult(ok, title, body="", effect=None)` — `effect: dict | None`; `aegis.theme_names.THEME_NAMES: tuple[str, ...]`; the web `command_result` frame gains an `"effect"` key.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_command_registry.py`:

```python
from aegis.commands import CommandResult


def test_command_result_effect_defaults_none():
    assert CommandResult(True, "t").effect is None


def test_command_result_carries_effect():
    r = CommandResult(True, "t", effect={"kind": "clear"})
    assert r.effect == {"kind": "clear"}
```

Add to `tests/test_web_slash.py` (reuses the file's `FakeCore`/`FakeManager`/`WSSession.__new__` harness):

```python
class _EffectCmdBridge(FakeManager):
    """FakeManager whose /themes-style command returns an effect."""


@pytest.mark.asyncio
async def test_web_command_frame_includes_effect(monkeypatch):
    import aegis.commands as commands
    from aegis.commands import CommandResult

    async def _fake_dispatch(payload, ctx):
        return CommandResult(True, "theme set",
                             effect={"kind": "theme", "name": "aegis-ink"})
    monkeypatch.setattr(commands, "dispatch", _fake_dispatch)

    session = WSSession.__new__(WSSession)
    session._m = FakeManager(FakeCore())
    res = await session._deliver_or_command("h", "/themes aegis-ink")
    assert res["command_result"]["effect"] == {"kind": "theme",
                                                "name": "aegis-ink"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_command_registry.py tests/test_web_slash.py -q`
Expected: FAIL — `CommandResult` has no `effect`; the web frame has no `effect` key.

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/commands/__init__.py`, add the field to the frozen dataclass:

```python
@dataclass(frozen=True)
class CommandResult:
    ok: bool          # False → rendered as an error block
    title: str        # one-line headline, e.g. "spawned researcher-1"
    body: str = ""    # optional multi-line detail
    effect: dict | None = None   # frontend-applied side-effect, or None
```

Create `src/aegis/theme_names.py`:

```python
"""Canonical aegis theme ids, in a Textual-free module so the harness-agnostic
commands core can list themes without importing ``aegis.tui.themes`` (which
imports Textual). Mirrors the keys of ``aegis.tui.themes.THEMES`` in their
full Textual-id form."""
from __future__ import annotations

THEME_NAMES: tuple[str, ...] = ("aegis-ink", "aegis-parchment", "aegis-slate")
```

In `src/aegis/web/wssession.py`, extend the `command_result` frame in `_deliver_or_command`:

```python
        if kind == "command":
            result = await dispatch(
                payload, CommandContext(bridge=self._m, handle=handle))
            return {"command_result": {
                "ok": result.ok, "title": result.title,
                "body": result.body, "effect": result.effect}}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_command_registry.py tests/test_web_slash.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/commands/__init__.py src/aegis/theme_names.py src/aegis/web/wssession.py tests/test_command_registry.py tests/test_web_slash.py
git commit -m "feat(commands): CommandResult.effect channel + THEME_NAMES + web frame plumbing"
```

---

### Task 3: `/queues` — rename from `/queue` + bare-list branch

**Files:**
- Modify: `src/aegis/commands/builtins/core.py` (`_queue` handler + registration)
- Test: `tests/test_slash_commands.py`

**Interfaces:**
- Consumes: `ctx.bridge.queue_manager.list_queues() -> list[str]` and `ctx.bridge.queue_manager._queues[name]` (a `Queue` with `.agent_profile`, `.max_parallel`).
- Produces: command name `queues` (was `queue`); bare `/queues` lists; `/queues new …` unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_slash_commands.py` (extend `FakeQueueManager` to serve a listing):

```python
async def test_queues_bare_lists(monkeypatch):
    bridge = FakeBridge()
    # seed one live queue on the fake manager
    from aegis.queue import Queue
    bridge.queue_manager._queues = {
        "build": Queue(name="build", agent_profile="opus", max_parallel=2)}
    bridge.queue_manager.list_queues = lambda: ["build"]
    res = await dispatch("/queues", CommandContext(bridge=bridge, handle="me"))
    assert res.ok is True
    assert "build" in res.body
    assert "opus" in res.body


async def test_queues_new_still_creates(monkeypatch):
    bridge = FakeBridge()
    res = await dispatch("/queues new q1 opus --ephemeral",
                         CommandContext(bridge=bridge, handle="me"))
    assert res.ok is True
    assert [q.name for q in bridge.registered] == ["q1"]


async def test_queue_old_name_is_gone():
    res = await dispatch("/queue", _ctx())
    assert res.ok is False
    assert "unknown command" in res.title
```

Add a minimal `list_queues`/`_queues` default to `FakeQueueManager` so unrelated tests don't break:

```python
class FakeQueueManager:
    def __init__(self):
        self.enqueued = []
        self._queues = {}
    def list_queues(self):
        return sorted(self._queues)
    def enqueue(self, queue, payload, *, enqueued_by, callback):
        # ...existing body unchanged...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_slash_commands.py -q -k "queues or queue_old"`
Expected: FAIL — command is still named `queue`; no bare-list branch.

- [ ] **Step 3: Write minimal implementation**

In `builtins/core.py`, make `_queue` subverb-optional with a list branch, and rename the command to `queues`:

```python
async def _queue(ctx: CommandContext, args) -> CommandResult:
    sub = args.get("subverb")
    if sub is None:                       # bare /queues → list
        qm = ctx.bridge.queue_manager
        names = qm.list_queues()
        if not names:
            return CommandResult(True, "no queues configured")
        lines = []
        for n in names:
            q = qm._queues.get(n)
            if q is None:
                lines.append(f"  {n}")
            else:
                lines.append(f"  {n} · {q.agent_profile} · "
                             f"max_parallel {q.max_parallel}")
        plural = "" if len(names) == 1 else "s"
        return CommandResult(True, f"{len(names)} queue{plural}",
                             "\n".join(lines))
    if sub != "new":
        return CommandResult(False,
                             "usage: /queues new <name> [agent] [--ephemeral]")
    name = args.get("name")
    if not name:
        return CommandResult(False,
                             "usage: /queues new <name> [agent] [--ephemeral]")
    # ...the rest of the existing 2A body (agent resolve, ephemeral vs
    # persist) unchanged, but replacing the usage strings' "/queue" with
    # "/queues"...
```

Update the registration (name, usage, and make `subverb`/`name` optional):

```python
    SlashCommand("queues", "list or create queues",
                 "/queues [new <name> [agent] [--ephemeral]]", _queue,
                 spec=ArgSpec(
                     positionals=(Arg("subverb", required=False),
                                  Arg("name", required=False),
                                  Arg("agent", required=False)),
                     flags=(Flag("ephemeral", takes_value=False),))),
```

(The `name` positional is now optional at the parser level; the handler enforces it for the `new` subverb, so bare `/queues` parses cleanly.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_slash_commands.py -q`
Expected: PASS. Update any 2A test that referenced `/queue` (not `/queues`) — e.g. `test_queue_new_persists_by_default` should dispatch `/queues new …`; adjust the dispatch string, keep the assertions.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/commands/builtins/core.py tests/test_slash_commands.py
git commit -m "feat(commands): rename /queue -> /queues, add bare-list branch"
```

---

### Task 4: `/agents add` / `/agents remove`

**Files:**
- Modify: `src/aegis/commands/builtins/core.py` (`_agents` handler + registration)
- Test: `tests/test_slash_commands.py`

**Interfaces:**
- Consumes: `aegis.config.find_project_root`, `aegis.config.ConfigError`, `aegis.config.Agent`, `aegis.config.edit.add_agent(root, slug, *, provider, model, effort=None, permission=None)`, `aegis.config.edit.remove_agent(root, slug)`; `ctx.bridge.register_agent(slug, agent)`.
- Produces: `/agents` bare lists (unchanged); `/agents add <slug> <harness> <model> [--effort E] [--permission P]` persists + hot-registers; `/agents remove <slug>` persists (restart to drop live).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_slash_commands.py` (extend `FakeBridge` to record `register_agent`):

```python
async def test_agents_add_persists_and_hot_registers(monkeypatch):
    import aegis.config as cfg
    import aegis.config.edit as cfg_edit
    calls = {}
    from pathlib import Path
    monkeypatch.setattr(cfg, "find_project_root", lambda: Path("/tmp/proj"))
    monkeypatch.setattr(cfg_edit, "add_agent",
                        lambda root, slug, **kw: calls.setdefault("add", (slug, kw)))
    bridge = FakeBridge()
    res = await dispatch("/agents add r claude-code sonnet --effort high",
                         CommandContext(bridge=bridge, handle="me"))
    assert res.ok is True
    assert calls["add"][0] == "r"
    assert calls["add"][1]["provider"] == "claude-code"
    assert calls["add"][1]["model"] == "sonnet"
    assert calls["add"][1]["effort"] == "high"
    assert bridge.registered_agents and bridge.registered_agents[0][0] == "r"


async def test_agents_remove_persists(monkeypatch):
    import aegis.config as cfg
    import aegis.config.edit as cfg_edit
    from pathlib import Path
    removed = {}
    monkeypatch.setattr(cfg, "find_project_root", lambda: Path("/tmp/proj"))
    monkeypatch.setattr(cfg_edit, "remove_agent",
                        lambda root, slug: removed.setdefault("slug", slug))
    res = await dispatch("/agents remove r",
                         CommandContext(bridge=FakeBridge(), handle="me"))
    assert res.ok is True
    assert removed["slug"] == "r"


async def test_agents_bare_still_lists():
    res = await dispatch("/agents", _ctx())
    assert res.ok is True
```

Extend `FakeBridge`:

```python
class FakeBridge:
    def __init__(self):
        # ...existing...
        self.registered_agents = []
    def register_agent(self, slug, agent):
        self.registered_agents.append((slug, agent))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_slash_commands.py -q -k agents`
Expected: FAIL — `_agents` has no `add`/`remove` branch; spec has no positionals.

- [ ] **Step 3: Write minimal implementation**

In `builtins/core.py`, make `_agents` subverb-aware (keep the existing list body as the default branch), mirroring the MCP `aegis_config_add_agent`/`remove_agent` tools:

```python
async def _agents(ctx: CommandContext, args) -> CommandResult:
    sub = args.get("subverb")
    if sub in (None, "list"):
        return _agents_list(ctx)          # existing 2A list body, extracted
    if sub == "add":
        return await _agents_add(ctx, args)
    if sub == "remove":
        return await _agents_remove(ctx, args)
    return CommandResult(False,
                         "usage: /agents [add <slug> <harness> <model>"
                         " [--effort E] [--permission P] | remove <slug>]")


async def _agents_add(ctx, args) -> CommandResult:
    slug = args.get("slug")
    harness = args.get("harness")
    model = args.get("model")
    if not (slug and harness and model):
        return CommandResult(False,
                             "usage: /agents add <slug> <harness> <model>"
                             " [--effort E] [--permission P]")
    import aegis.config as cfg
    import aegis.config.edit as cfg_edit
    root = cfg.find_project_root()
    if root is None:
        return CommandResult(False, "no .aegis.yaml found")
    effort = args.get("effort")
    permission = args.get("permission")
    try:
        cfg_edit.add_agent(root, slug, provider=harness, model=model,
                           effort=effort, permission=permission)
    except cfg.ConfigError as e:
        return CommandResult(False, f"agent rejected: {e}")
    kw = {"harness": harness, "model": model}
    if effort is not None:
        kw["effort"] = effort
    if permission is not None:
        kw["permission"] = permission
    try:
        ctx.bridge.register_agent(slug, cfg.Agent(**kw))
    except Exception as e:                                    # noqa: BLE001
        return CommandResult(True, f"agent {slug} saved",
                             f"persisted to .aegis.yaml; restart to activate "
                             f"(live register failed: {e})")
    return CommandResult(True, f"agent {slug} added",
                         f"{harness} · {model} · persisted + hot-registered")


async def _agents_remove(ctx, args) -> CommandResult:
    slug = args.get("slug")
    if not slug:
        return CommandResult(False, "usage: /agents remove <slug>")
    import aegis.config as cfg
    import aegis.config.edit as cfg_edit
    root = cfg.find_project_root()
    if root is None:
        return CommandResult(False, "no .aegis.yaml found")
    try:
        cfg_edit.remove_agent(root, slug)
    except cfg.ConfigError as e:
        return CommandResult(False, f"cannot remove agent: {e}")
    return CommandResult(True, f"agent {slug} removed",
                         "persisted to .aegis.yaml; restart to drop the live "
                         "profile")
```

Extract the current list body into `_agents_list(ctx)` (a plain `def` returning a `CommandResult`, the verbatim 2A logic). Update the registration's spec + usage:

```python
    SlashCommand("agents", "list or manage agents",
                 "/agents [add <slug> <harness> <model> "
                 "[--effort E] [--permission P] | remove <slug>]", _agents,
                 spec=ArgSpec(
                     positionals=(Arg("subverb", required=False),
                                  Arg("slug", required=False),
                                  Arg("harness", required=False),
                                  Arg("model", required=False)),
                     flags=(Flag("effort"), Flag("permission")))),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_slash_commands.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/commands/builtins/core.py tests/test_slash_commands.py
git commit -m "feat(commands): /agents add + /agents remove (fold config into /agents)"
```

---

### Task 5: `/groups` + `list_groups` bridge method

**Files:**
- Modify: `src/aegis/groups/bridge.py` (`_GroupsBridge.list_groups`)
- Modify: `src/aegis/mcp/bridge.py` (`GroupsBridge` Protocol gains `list_groups`)
- Create: `src/aegis/commands/builtins/coordination.py` (`/groups`, and `/schedules` in Task 6)
- Modify: `src/aegis/commands/builtins/__init__.py` (import `coordination`)
- Test: `tests/test_groups_bridge.py` (new — `list_groups`), `tests/test_slash_commands.py` (`/groups`)

**Interfaces:**
- Consumes: `ctx.bridge.groups` (`_GroupsBridge`) with `list_groups() -> list[dict]`, `status(name) -> dict`, `dissolve(name) -> dict`; `self.runtime.registry.names() -> list[str]`, `registry.get(name).members -> dict[str, MemberRef]`.
- Produces: `/groups` (bare = list), `/groups status <name>`, `/groups dissolve <name>`.

- [ ] **Step 1: Write the failing tests**

`tests/test_groups_bridge.py`:

```python
import pytest
from aegis.groups.bridge import make_groups_bridge


class _FakeSM:
    def live_handles(self):
        return set()


@pytest.mark.asyncio
async def test_list_groups_returns_name_and_member_count():
    b = make_groups_bridge(session_manager=_FakeSM(), inbox_router=None)
    b.runtime.registry.create("g1")
    from aegis.groups.models import MemberRef
    b.runtime.registry.add_member("g1", MemberRef(handle="a", profile="opus"))
    b.runtime.registry.add_member("g1", MemberRef(handle="b", profile="opus"))
    rows = b.list_groups()
    assert {"name": "g1", "members": 2} in rows
```

Add to `tests/test_slash_commands.py` (a fake groups object on `FakeBridge`):

```python
class FakeGroups:
    def __init__(self):
        self.dissolved = []
    def list_groups(self):
        return [{"name": "g1", "members": 2}]
    async def status(self, name):
        return {"name": name, "members": [{"handle": "a", "profile": "opus"}]}
    async def dissolve(self, name):
        self.dissolved.append(name)
        return {"dissolved": name}


async def test_groups_bare_lists():
    bridge = FakeBridge()
    bridge.groups = FakeGroups()
    res = await dispatch("/groups", CommandContext(bridge=bridge, handle="me"))
    assert res.ok is True
    assert "g1" in res.body


async def test_groups_dissolve():
    bridge = FakeBridge()
    bridge.groups = FakeGroups()
    res = await dispatch("/groups dissolve g1",
                         CommandContext(bridge=bridge, handle="me"))
    assert res.ok is True
    assert bridge.groups.dissolved == ["g1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_groups_bridge.py tests/test_slash_commands.py -q -k "groups or list_groups"`
Expected: FAIL — `_GroupsBridge` has no `list_groups`; `/groups` command unregistered.

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/groups/bridge.py`, add to `_GroupsBridge`:

```python
    def list_groups(self) -> list[dict]:
        reg = self.runtime.registry
        return [{"name": n, "members": len(reg.get(n).members)}
                for n in reg.names()]
```

In `src/aegis/mcp/bridge.py`, add to the `GroupsBridge` Protocol (near `status`):

```python
    def list_groups(self) -> list[dict]: ...
```

Create `src/aegis/commands/builtins/coordination.py`:

```python
"""Coordination slash commands: /groups, /schedules — thin calls over the
groups bridge and the scheduler-push helpers."""
from __future__ import annotations

from aegis.commands import (
    CommandContext, CommandResult, SlashCommand, register,
)
from aegis.commands.args import Arg, ArgSpec


async def _groups(ctx: CommandContext, args) -> CommandResult:
    g = ctx.bridge.groups
    sub = args.get("subverb")
    if sub in (None, "list"):
        rows = g.list_groups()
        if not rows:
            return CommandResult(True, "no live groups")
        lines = [f"  {r['name']} · {r['members']} member"
                 f"{'' if r['members'] == 1 else 's'}" for r in rows]
        return CommandResult(True, f"{len(rows)} group"
                             f"{'' if len(rows) == 1 else 's'}",
                             "\n".join(lines))
    name = args.get("name")
    if not name:
        return CommandResult(False, "usage: /groups status|dissolve <name>")
    if sub == "status":
        st = await g.status(name)
        members = ", ".join(f"{m['handle']}({m['profile']})"
                            for m in st.get("members", [])) or "none"
        return CommandResult(True, f"group {name}", f"members: {members}")
    if sub == "dissolve":
        await g.dissolve(name)
        return CommandResult(True, f"group {name} dissolved")
    return CommandResult(False, "usage: /groups [status|dissolve <name>]")


for _cmd in (
    SlashCommand("groups", "list groups, or status/dissolve one",
                 "/groups [status|dissolve <name>]", _groups,
                 spec=ArgSpec(positionals=(Arg("subverb", required=False),
                                           Arg("name", required=False)))),
):
    register(_cmd)
```

In `src/aegis/commands/builtins/__init__.py`, import the new module:

```python
from aegis.commands.builtins import core as _core        # noqa: F401
from aegis.commands.builtins import coordination as _coordination  # noqa: F401
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_groups_bridge.py tests/test_slash_commands.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/groups/bridge.py src/aegis/mcp/bridge.py src/aegis/commands/builtins/ tests/test_groups_bridge.py tests/test_slash_commands.py
git commit -m "feat(commands): /groups + list_groups bridge method"
```

---

### Task 6: `/schedules`

**Files:**
- Modify: `src/aegis/commands/builtins/coordination.py` (`/schedules`)
- Test: `tests/test_slash_commands.py`

**Interfaces:**
- Consumes: `aegis.scheduler.push.list_payload(scheduler, state_root, inline_names) -> dict`, `show_payload(scheduler, state_root, inline_names, name) -> dict | None`, `remove_schedule(scheduler, state_root, inline_names, name) -> Result(status,...)`, `logs_payload(state_root, name, *, tail) -> dict`; `aegis.config.edit.set_schedule_enabled(root, name, value) -> bool`; `ctx.bridge.scheduler`, `ctx.bridge.state_root`, `ctx.bridge.inline_schedule_names() -> set[str]`; `aegis.config.find_project_root`.
- Produces: `/schedules` (list), `show <name>`, `enable <name>`, `disable <name>`, `remove <name>`, `logs <name>`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_slash_commands.py` (extend `FakeBridge` with `scheduler`/`state_root`/`inline_schedule_names`; monkeypatch the push helpers):

```python
async def test_schedules_list(monkeypatch):
    import aegis.scheduler.push as push
    monkeypatch.setattr(push, "list_payload",
                        lambda sched, root, inline: {"schedules": [
                            {"name": "nightly", "enabled": True,
                             "next_fire": "2026-07-18T00:00:00Z"}]})
    bridge = FakeBridge()
    res = await dispatch("/schedules",
                         CommandContext(bridge=bridge, handle="me"))
    assert res.ok is True
    assert "nightly" in res.body


async def test_schedules_enable(monkeypatch):
    import aegis.config as cfg
    import aegis.config.edit as cfg_edit
    from pathlib import Path
    seen = {}
    monkeypatch.setattr(cfg, "find_project_root", lambda: Path("/tmp/proj"))
    monkeypatch.setattr(cfg_edit, "set_schedule_enabled",
                        lambda root, name, value: seen.setdefault("call", (name, value)) or value)
    bridge = FakeBridge()
    res = await dispatch("/schedules enable nightly",
                         CommandContext(bridge=bridge, handle="me"))
    assert res.ok is True
    assert seen["call"] == ("nightly", True)
```

Extend `FakeBridge.__init__`:

```python
        from pathlib import Path
        self.scheduler = None
        self.state_root = Path("/tmp/proj")
    def inline_schedule_names(self):
        return set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_slash_commands.py -q -k schedules`
Expected: FAIL — `/schedules` unregistered.

- [ ] **Step 3: Write minimal implementation**

Append to `builtins/coordination.py`:

```python
async def _schedules(ctx: CommandContext, args) -> CommandResult:
    from aegis.scheduler.push import (
        list_payload, logs_payload, remove_schedule, show_payload)
    b = ctx.bridge
    sub = args.get("subverb")
    if sub in (None, "list"):
        rows = list_payload(getattr(b, "scheduler", None), b.state_root,
                            b.inline_schedule_names()).get("schedules", [])
        if not rows:
            return CommandResult(True, "no schedules")
        lines = [f"  {'●' if r.get('enabled') else '○'} {r['name']} · "
                 f"next {r.get('next_fire', '?')}" for r in rows]
        return CommandResult(True, f"{len(rows)} schedule"
                             f"{'' if len(rows) == 1 else 's'}",
                             "\n".join(lines))
    name = args.get("name")
    if not name:
        return CommandResult(
            False, "usage: /schedules show|enable|disable|remove|logs <name>")
    if sub == "show":
        p = show_payload(getattr(b, "scheduler", None), b.state_root,
                         b.inline_schedule_names(), name)
        if p is None:
            return CommandResult(False, f"schedule {name} not found")
        return CommandResult(True, f"schedule {name}",
                             "\n".join(f"{k}: {v}" for k, v in p.items()))
    if sub in ("enable", "disable"):
        import aegis.config as cfg
        import aegis.config.edit as cfg_edit
        root = cfg.find_project_root()
        if root is None:
            return CommandResult(False, "no .aegis.yaml found")
        try:
            cfg_edit.set_schedule_enabled(root, name, sub == "enable")
        except (KeyError, cfg.ConfigError, FileNotFoundError) as e:
            return CommandResult(False, f"cannot {sub} {name}: {e}")
        return CommandResult(True, f"schedule {name} {sub}d")
    if sub == "remove":
        r = remove_schedule(getattr(b, "scheduler", None), b.state_root,
                            b.inline_schedule_names(), name)
        if getattr(r, "status", None) == "ok":
            return CommandResult(True, f"schedule {name} removed")
        return CommandResult(False, f"cannot remove {name}",
                             getattr(r, "status", "error"))
    if sub == "logs":
        recs = logs_payload(b.state_root, name).get("records", [])
        if not recs:
            return CommandResult(True, f"no logs for {name}")
        lines = [str(rec) for rec in recs[-20:]]
        return CommandResult(True, f"schedule {name} · {len(recs)} records",
                             "\n".join(lines))
    return CommandResult(
        False, "usage: /schedules [show|enable|disable|remove|logs <name>]")


register(SlashCommand(
    "schedules", "list schedules, or show/enable/disable/remove/logs one",
    "/schedules [show|enable|disable|remove|logs <name>]", _schedules,
    spec=ArgSpec(positionals=(Arg("subverb", required=False),
                              Arg("name", required=False)))))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_slash_commands.py -q -k schedules`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/commands/builtins/coordination.py tests/test_slash_commands.py
git commit -m "feat(commands): /schedules list/show/enable/disable/remove/logs"
```

---

### Task 7: `/terminals`

**Files:**
- Create: `src/aegis/commands/builtins/terminals.py`
- Modify: `src/aegis/commands/builtins/__init__.py` (import `terminals`)
- Test: `tests/test_slash_commands.py`

**Interfaces:**
- Consumes: `ctx.bridge.terminal_manager` with `list() -> list[TerminalInfo]` (`.name`, `.pid`, `.shell`), `spawn(*, name) -> TerminalInfo`, `run(name, cmd, *, writer) -> CommandRecord` (`.cmd`, `.exit`, `.stdout`, `.duration_s`), `close(name)`.
- Produces: `/terminals` (list), `new <name>`, `run <name> <cmd…>`, `close <name>`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_slash_commands.py`:

```python
class FakeTerm:
    def __init__(self):
        self.spawned, self.closed, self.ran = [], [], []
    def list(self):
        from types import SimpleNamespace
        return [SimpleNamespace(name="t1", pid=42, shell="/bin/bash")]
    async def spawn(self, *, name):
        self.spawned.append(name)
        from types import SimpleNamespace
        return SimpleNamespace(name=name, pid=99, shell="/bin/bash")
    async def run(self, name, cmd, *, writer):
        self.ran.append((name, cmd, writer))
        from types import SimpleNamespace
        return SimpleNamespace(cmd=cmd, exit=0, stdout="hi\n", duration_s=0.1)
    async def close(self, name):
        self.closed.append(name)


async def test_terminals_bare_lists():
    bridge = FakeBridge()
    bridge.terminal_manager = FakeTerm()
    res = await dispatch("/terminals",
                         CommandContext(bridge=bridge, handle="me"))
    assert res.ok is True
    assert "t1" in res.body


async def test_terminals_run_surfaces_output():
    bridge = FakeBridge()
    bridge.terminal_manager = FakeTerm()
    res = await dispatch("/terminals run t1 echo hi",
                         CommandContext(bridge=bridge, handle="me"))
    assert res.ok is True
    assert bridge.terminal_manager.ran == [("t1", "echo hi", "me")]
    assert "hi" in res.body


async def test_terminals_new_and_close():
    bridge = FakeBridge()
    bridge.terminal_manager = FakeTerm()
    await dispatch("/terminals new t2", CommandContext(bridge=bridge, handle="me"))
    await dispatch("/terminals close t2", CommandContext(bridge=bridge, handle="me"))
    assert bridge.terminal_manager.spawned == ["t2"]
    assert bridge.terminal_manager.closed == ["t2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_slash_commands.py -q -k terminals`
Expected: FAIL — `/terminals` unregistered.

- [ ] **Step 3: Write minimal implementation**

Create `src/aegis/commands/builtins/terminals.py`:

```python
"""/terminals — thin calls over the shared TerminalManager. `run` blocks
until the command finishes (matching aegis_term_run) and returns its output."""
from __future__ import annotations

from aegis.commands import (
    CommandContext, CommandResult, SlashCommand, register,
)
from aegis.commands.args import Arg, ArgSpec


async def _terminals(ctx: CommandContext, args) -> CommandResult:
    tm = ctx.bridge.terminal_manager
    sub = args.get("subverb")
    if sub in (None, "list"):
        infos = tm.list()
        if not infos:
            return CommandResult(True, "no terminals")
        lines = [f"  {i.name} · pid {i.pid} · {i.shell}" for i in infos]
        return CommandResult(True, f"{len(infos)} terminal"
                             f"{'' if len(infos) == 1 else 's'}",
                             "\n".join(lines))
    name = args.get("name")
    if not name:
        return CommandResult(False,
                             "usage: /terminals new|run|close <name> …")
    if sub == "new":
        info = await tm.spawn(name=name)
        return CommandResult(True, f"terminal {name} started",
                             f"pid {info.pid} · {info.shell}")
    if sub == "close":
        await tm.close(name)
        return CommandResult(True, f"terminal {name} closed")
    if sub == "run":
        cmd = args.get("cmd")
        if not cmd:
            return CommandResult(False, "usage: /terminals run <name> <cmd>")
        rec = await tm.run(name, cmd, writer=ctx.handle)
        head = f"{name}$ {rec.cmd} · exit {rec.exit}"
        return CommandResult(rec.exit == 0, head, rec.stdout.rstrip())
    return CommandResult(False, "usage: /terminals [new|run|close <name> …]")


for _cmd in (
    SlashCommand("terminals", "list terminals, or new/run/close one",
                 "/terminals [new <name> | run <name> <cmd> | close <name>]",
                 _terminals,
                 spec=ArgSpec(positionals=(
                     Arg("subverb", required=False),
                     Arg("name", required=False),
                     Arg("cmd", required=False, greedy=True)))),
):
    register(_cmd)
```

In `builtins/__init__.py`, add:

```python
from aegis.commands.builtins import terminals as _terminals  # noqa: F401
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_slash_commands.py -q -k terminals`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/commands/builtins/terminals.py src/aegis/commands/builtins/__init__.py tests/test_slash_commands.py
git commit -m "feat(commands): /terminals list/new/run/close"
```

---

### Task 8: `/rename` + `/close`

**Files:**
- Create: `src/aegis/commands/builtins/session_ctl.py`
- Modify: `src/aegis/commands/builtins/__init__.py` (import `session_ctl`)
- Test: `tests/test_slash_commands.py`

**Interfaces:**
- Consumes: `ctx.bridge.rename_handle(old, new) -> dict` (may return `{"error": ...}`), `ctx.bridge.close(handle) -> None`, `ctx.handle`.
- Produces: `/rename <new>`, `/close [handle]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_slash_commands.py` (extend `FakeBridge` to record `close`/`rename_handle`):

```python
async def test_rename_current_pane():
    bridge = FakeBridge()
    res = await dispatch("/rename newname",
                         CommandContext(bridge=bridge, handle="me"))
    assert res.ok is True
    assert bridge.renamed == [("me", "newname")]


async def test_close_defaults_to_current():
    bridge = FakeBridge()
    res = await dispatch("/close", CommandContext(bridge=bridge, handle="me"))
    assert res.ok is True
    assert bridge.closed == ["me"]


async def test_close_named_handle():
    bridge = FakeBridge()
    res = await dispatch("/close other",
                         CommandContext(bridge=bridge, handle="me"))
    assert bridge.closed == ["other"]
```

Extend `FakeBridge`:

```python
        self.closed, self.renamed = [], []
    async def close(self, handle):
        self.closed.append(handle)
    async def rename_handle(self, old, new):
        self.renamed.append((old, new))
        return {"old": old, "new": new}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_slash_commands.py -q -k "rename or close"`
Expected: FAIL — commands unregistered.

- [ ] **Step 3: Write minimal implementation**

Create `src/aegis/commands/builtins/session_ctl.py`:

```python
"""Session-control slash commands: /rename, /close, and (Tasks 9-10)
/themes, /clear. Thin calls over the bridge; /themes and /clear additionally
carry a CommandResult.effect the frontend seam applies."""
from __future__ import annotations

from aegis.commands import (
    CommandContext, CommandResult, SlashCommand, register,
)
from aegis.commands.args import Arg, ArgSpec


async def _rename(ctx: CommandContext, args) -> CommandResult:
    new = args["new"]
    res = await ctx.bridge.rename_handle(ctx.handle, new)
    if isinstance(res, dict) and res.get("error"):
        return CommandResult(False, "rename rejected", res["error"])
    return CommandResult(True, f"renamed {ctx.handle} → {new}")


async def _close(ctx: CommandContext, args) -> CommandResult:
    target = args.get("handle") or ctx.handle
    await ctx.bridge.close(target)
    return CommandResult(True, f"closed {target}")


for _cmd in (
    SlashCommand("rename", "rename the current session",
                 "/rename <new>", _rename,
                 spec=ArgSpec(positionals=(Arg("new"),))),
    SlashCommand("close", "close the current or a named session",
                 "/close [handle]", _close,
                 spec=ArgSpec(positionals=(Arg("handle", required=False),))),
):
    register(_cmd)
```

In `builtins/__init__.py`, add:

```python
from aegis.commands.builtins import session_ctl as _session_ctl  # noqa: F401
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_slash_commands.py -q -k "rename or close"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/commands/builtins/session_ctl.py src/aegis/commands/builtins/__init__.py tests/test_slash_commands.py
git commit -m "feat(commands): /rename + /close"
```

---

### Task 9: `/themes` + theme effect (TUI + web)

**Files:**
- Modify: `src/aegis/commands/builtins/session_ctl.py` (`/themes`)
- Modify: `src/aegis/tui/pane.py` (apply `effect` after mounting the command block)
- Modify: `src/aegis/web/static/js/app.js` (apply `effect` in the deliver handler)
- Test: `tests/test_slash_commands.py` (command + effect), `tests/test_pane_slash_command.py` (TUI applies theme)

**Interfaces:**
- Consumes: `aegis.theme_names.THEME_NAMES`; TUI `self.app.theme = <name>`; web `applyTheme(name)`.
- Produces: `/themes` (list), `/themes <name>` → `CommandResult(effect={"kind": "theme", "name": <full-id>})`. Both frontends apply `effect` after mounting the block.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_slash_commands.py`:

```python
from aegis.theme_names import THEME_NAMES


async def test_themes_bare_lists():
    res = await dispatch("/themes", _ctx())
    assert res.ok is True
    assert THEME_NAMES[0] in res.body


async def test_themes_set_returns_effect():
    res = await dispatch(f"/themes {THEME_NAMES[0]}", _ctx())
    assert res.ok is True
    assert res.effect == {"kind": "theme", "name": THEME_NAMES[0]}


async def test_themes_short_suffix_normalizes():
    # "ink" → "aegis-ink"
    short = THEME_NAMES[0].split("aegis-", 1)[1]
    res = await dispatch(f"/themes {short}", _ctx())
    assert res.effect == {"kind": "theme", "name": THEME_NAMES[0]}


async def test_themes_unknown_errors():
    res = await dispatch("/themes bogus", _ctx())
    assert res.ok is False
```

For the TUI seam, add to `tests/test_pane_slash_command.py` (model on the existing `test_slash_command_runs_and_is_not_sent` harness — same imports/fixtures at the top of that file):

```python
async def test_themes_command_applies_theme(...):
    # Build the pane as the existing slash test does, type f"/themes {THEME_NAMES[0]}",
    # submit, then assert app.theme == THEME_NAMES[0] and a command block mounted.
    ...
```

Fill the body by copying the existing test's pane construction + `GrowingInput` submit; assert `pane.app.theme == THEME_NAMES[0]` after submit.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_slash_commands.py tests/test_pane_slash_command.py -q -k themes`
Expected: FAIL — `/themes` unregistered; pane does not apply effect.

- [ ] **Step 3: Write minimal implementation**

Append to `builtins/session_ctl.py`:

```python
from aegis.theme_names import THEME_NAMES


def _normalize_theme(name: str) -> str | None:
    if name in THEME_NAMES:
        return name
    prefixed = f"aegis-{name}"
    return prefixed if prefixed in THEME_NAMES else None


async def _themes(ctx: CommandContext, args) -> CommandResult:
    name = args.get("name")
    if name is None or name == "list":
        return CommandResult(True, "themes", "\n".join(f"  {t}"
                             for t in THEME_NAMES))
    full = _normalize_theme(name)
    if full is None:
        return CommandResult(False, f"unknown theme: {name}",
                             "available: " + ", ".join(THEME_NAMES))
    return CommandResult(True, f"theme → {full}",
                         effect={"kind": "theme", "name": full})


register(SlashCommand("themes", "list themes, or switch to one",
                      "/themes [name]", _themes,
                      spec=ArgSpec(positionals=(Arg("name", required=False),))))
```

In `src/aegis/tui/pane.py`, after the command block is mounted in the `/`-branch (right before `return`), apply the effect:

```python
                self._mount_block(
                    render_command_block(result, self._palette, width),
                    f"{result.title}\n{result.body}".strip())
                if result.effect:
                    self._apply_command_effect(result.effect)
                return
```

Add the method to `ConversationPane`:

```python
    def _apply_command_effect(self, effect: dict) -> None:
        """Apply a slash-command frontend effect (theme switch, transcript
        clear). Unknown kinds are ignored (forward-compatible)."""
        kind = effect.get("kind")
        if kind == "theme":
            self.app.theme = effect["name"]
```

In `src/aegis/web/static/js/app.js`, in the deliver `.then` handler, apply the effect after mounting the block:

```javascript
          .then((res) => {
            if (res && res.command_result) {
              mountCommandBlock(handle, res.command_result);
              applyCommandEffect(handle, res.command_result.effect);
            }
          })
```

Add the helper near `mountCommandBlock`:

```javascript
function applyCommandEffect(handle, effect) {
  if (!effect) return;
  if (effect.kind === "theme") {
    applyTheme(effect.name);
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_slash_commands.py tests/test_pane_slash_command.py -q -k themes`
Expected: PASS. If the pane test flakes (inotify), re-run it alone before believing it.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/commands/builtins/session_ctl.py src/aegis/tui/pane.py src/aegis/web/static/js/app.js tests/test_slash_commands.py tests/test_pane_slash_command.py
git commit -m "feat(commands): /themes + theme effect channel (TUI + web)"
```

---

### Task 10: `/clear` + clear effect with context marker (TUI + web)

**Files:**
- Modify: `src/aegis/commands/builtins/session_ctl.py` (`/clear`)
- Modify: `src/aegis/tui/pane.py` (`_apply_command_effect` clear branch)
- Modify: `src/aegis/web/static/js/app.js` (`applyCommandEffect` clear branch)
- Test: `tests/test_slash_commands.py` (effect), `tests/test_pane_slash_command.py` (TUI clears + marker)

**Interfaces:**
- Consumes: `self._transcript() -> VerticalScroll` (`.remove_children()`), `self._core.metrics.last_true_input: int`, `aegis.tui.metrics._fmt_tokens`.
- Produces: `/clear` → `CommandResult(effect={"kind": "clear"})`. The TUI seam wipes the transcript and mounts a persistent "cleared · N context tokens still in play" marker; the web seam does the same in the tab DOM.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_slash_commands.py`:

```python
async def test_clear_returns_clear_effect():
    res = await dispatch("/clear", _ctx())
    assert res.ok is True
    assert res.effect == {"kind": "clear"}
```

Add to `tests/test_pane_slash_command.py` (model on the existing harness):

```python
async def test_clear_command_wipes_transcript_and_marks(...):
    # Build the pane, mount a couple of blocks (or run a /sessions command),
    # then submit "/clear". Assert the transcript's children were removed and
    # a single marker block remains whose text mentions "cleared".
    ...
```

Fill the body from the existing slash-test harness; after submit assert the transcript contains exactly the marker block and its renderable text contains "cleared".

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_slash_commands.py tests/test_pane_slash_command.py -q -k clear`
Expected: FAIL — `/clear` unregistered; pane has no clear branch.

- [ ] **Step 3: Write minimal implementation**

Append to `builtins/session_ctl.py`:

```python
async def _clear(ctx: CommandContext, args) -> CommandResult:
    return CommandResult(True, "transcript cleared",
                         effect={"kind": "clear"})


register(SlashCommand("clear", "clear the visible transcript (cosmetic)",
                      "/clear", _clear))
```

In `src/aegis/tui/pane.py`, extend `_apply_command_effect`:

```python
    def _apply_command_effect(self, effect: dict) -> None:
        kind = effect.get("kind")
        if kind == "theme":
            self.app.theme = effect["name"]
        elif kind == "clear":
            from aegis.tui.metrics import _fmt_tokens
            self._transcript().remove_children()
            ctx_tokens = self._core.metrics.last_true_input
            marker = (f"──── transcript cleared · "
                      f"{_fmt_tokens(ctx_tokens)} context tokens still in "
                      f"play ────")
            width = self._transcript().size.width or 80
            from rich.text import Text
            self._mount_block(
                Text(marker, style=self._palette.muted, justify="center"),
                marker)
```

(The command block for `/clear` mounts *before* the effect runs; the effect's `remove_children()` clears it along with the scrollback, leaving only the marker. Confirm `self._palette` exposes a muted/subtle role; if the attribute differs, use the file's existing subdued style role.)

In `src/aegis/web/static/js/app.js`, extend `applyCommandEffect`:

```javascript
function applyCommandEffect(handle, effect) {
  if (!effect) return;
  if (effect.kind === "theme") {
    applyTheme(effect.name);
  } else if (effect.kind === "clear") {
    const tab = tabs.get(handle);
    if (!tab) return;
    tab.blocks.length = 0;
    tab.nodes.length = 0;
    tab.transcriptEl.innerHTML = "";
    const div = document.createElement("div");
    div.className = "command-block cleared-marker";
    div.textContent = "──── transcript cleared ────";
    tab.transcriptEl.appendChild(div);
  }
}
```

(The web marker omits the token count in v1 unless the tab already tracks a live context-token metric; if `tab.metrics` carries it, interpolate it into the marker text. Check the tab object's fields; if absent, ship the count-free marker and note it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_slash_commands.py tests/test_pane_slash_command.py -q -k clear`
Expected: PASS. Re-run a flaky pane test alone before believing a failure.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/commands/builtins/session_ctl.py src/aegis/tui/pane.py src/aegis/web/static/js/app.js tests/test_slash_commands.py tests/test_pane_slash_command.py
git commit -m "feat(commands): /clear cosmetic wipe + context-token marker (TUI + web)"
```

---

### Task 11: Full-slice verification + docs

**Files:**
- Modify: `TASKS.md` (mark 2B done, note 2B.1/2C/2D)
- Modify: `CHANGELOG.md` (2B entry)
- Modify: `AGENTS.md` (§ commands note, if warranted)

- [ ] **Step 1: Run the hermetic suite**

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS. Re-run any flaky TUI/watchdog test alone (inotify) before treating it as real (AGENTS.md).

- [ ] **Step 2: Manual smoke (TUI)**

Run `aegis` in a project with a `.aegis.yaml`, then exercise: `/help` (all new commands grouped under builtin), `/agents` / `/agents add smoke claude-code sonnet` (check `.aegis.yaml` gained the agent) / `/agents remove smoke`, `/queues`, `/groups`, `/schedules`, `/terminals` + `/terminals new t` + `/terminals run t ls` + `/terminals close t`, `/themes` + `/themes aegis-parchment` (theme switches live), `/clear` (transcript wipes, marker shows context tokens), `/rename foo`, `/close` (on a spare tab). Note any surprise; fix before proceeding.

- [ ] **Step 3: Manual smoke (web)**

Run `aegis serve`, open the web client, and confirm `/groups` / `/schedules` render as command blocks in the input box, `/themes aegis-slate` switches the stylesheet live, and `/clear` empties the transcript with a marker.

- [ ] **Step 4: Update docs**

In `TASKS.md`, mark the 2B bullet `[x]` (full builtin coverage shipped — `/groups /schedules /terminals /themes /clear /rename /close` + `/agents` management + bare-list convention + `CommandResult.effect`), and add a **2B.1** note: *session-mutation slice (`/model`, `/effort` via resume-restart) — deferred*. Add a `CHANGELOG.md` entry summarising the 2B command set, the effect channel, and the `/queue`→`/queues` rename. If AGENTS.md's command section names the builtin set, extend it.

- [ ] **Step 5: Commit**

```bash
git add TASKS.md CHANGELOG.md AGENTS.md
git commit -m "docs: slash commands 2B shipped — update TASKS/CHANGELOG/AGENTS"
```

---

## Self-Review

**Spec coverage** — every 2B spec section maps to a task:
- Command set §1 → Tasks 3–10 (queues, agents, groups, schedules, terminals, rename/close, themes, clear).
- `/clear` semantics §2 → Task 10 (cosmetic wipe + `last_true_input` marker).
- New code §3 → Task 5 (`list_groups` on both `GroupsBridge` defs) + Task 2 (`THEME_NAMES`).
- Effect channel §4 → Task 2 (field + web frame) + Tasks 9–10 (TUI/web application).
- Module split §5 → Task 1 (`builtins/` package) + submodule adds in Tasks 5/7/8.
- Testing §Testing → unit/pane/web tests folded per task; Task 11 runs the full gate + smokes.
- Scope: `/model`/`/effort` deferred (2B.1), `/handoff` dropped, `/config` dropped — none appear as tasks. ✓

**Placeholder scan** — the two "model on the existing test" notes (pane test bodies in Tasks 9–10) point at a concrete in-repo reference (`test_slash_command_runs_and_is_not_sent`), because the pane harness details are read in-file; the web `/clear` marker's optional token count is gated on a concrete field check (`tab.metrics`) with a defined fallback. No "TBD"/"handle edge cases".

**Type consistency** — `CommandResult(ok, title, body="", effect=None)` is used identically across Tasks 2, 9, 10; `_apply_command_effect(effect: dict)` introduced in Task 9, extended in Task 10 (same name); `list_groups() -> list[dict]` defined in Task 5 and consumed by the `/groups` handler in the same task; `THEME_NAMES` (full ids) defined in Task 2, consumed in Task 9; `terminal_manager.spawn(*, name)` / `run(name, cmd, *, writer)` match the grounded signatures; `add_agent(root, slug, *, provider=…)` matches `config/edit.py`. Handler signature `(ctx, args)` and `register`/`SlashCommand`/`ArgSpec` usage match the 2A registry throughout.
