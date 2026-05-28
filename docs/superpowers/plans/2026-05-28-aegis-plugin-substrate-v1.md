# Aegis plugin substrate — v1 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the **hook** and **tool** substrate primitives to aegis, the plugin install/update/uninstall machinery, the registry resolver, and the canonical `skill-system` plugin demonstrating it all end-to-end on Claude harness.

**Architecture:** Five vertical slices, each landing as testable software on its own. Slice 1 (hooks) extends the existing plugin loader and wires hook firing into `_run_turn`. Slice 2 (tools) plugs new tools into the FastMCP server. Slice 3 (plugin lifecycle) adds the install/uninstall machinery + lockfile against a local-path source. Slice 4 (registry) adds `gh:` resolution + `git archive` fetch. Slice 5 (skill-system) is the canonical plugin landing under `plugins/skill-system/` at the aegis repo root.

**Tech Stack:** Python 3.13+, FastMCP (already in stack), Typer (CLI), ruamel.yaml (comment-preserving config edits — already used by `aegis.config.edit`), rich (terminal output — already in stack), httpx (for `git archive` HTTPS fetch — already in stack via FastMCP transport).

**Spec:** `docs/superpowers/specs/2026-05-28-aegis-plugin-substrate-design.md` (commit 98b15bb).

**Conventions:**
- TDD: failing test → run-to-fail → implement → run-to-pass → commit. One logical change per commit.
- `uv run pytest -q -m "not live"` for the fast suite. Live tests use `@pytest.mark.live` and an `if shutil.which("claude") is None: pytest.skip(...)` guard.
- All new files live under `src/aegis/{hooks,tools,plugins}/` and `tests/`.
- Canonical plugin lives under `plugins/skill-system/` at the **aegis repo root** (not inside `src/`), to mirror the registry layout (`gh:apiad/aegis#plugins/`).

---

## File structure

### New files

```
src/aegis/hooks/
  __init__.py              # public API surface
  decorator.py             # @hook decorator + _REGISTRY
  contexts.py              # PreTurnContext, PreTurnResult, observer event dataclasses
  composer.py              # composition rules for pre_turn results
  runner.py                # invocation: timeout, exception handling, JSONL logging

src/aegis/tools/
  __init__.py              # public API surface
  decorator.py             # @tool decorator + _REGISTRY
  runner.py                # invocation wrapper: timeout, JSONL logging
  schema.py                # FastMCP registration helper

src/aegis/plugins/
  __init__.py              # public API: InstallContext, install_plugin, uninstall_plugin, etc.
  manifest.py              # PluginManifest dataclass + plugin.toml parser
  install_context.py       # InstallContext dataclass
  install.py               # install orchestration (local path source)
  uninstall.py             # uninstall orchestration
  lockfile.py              # .aegis/plugins.lock read/write
  registry.py              # registry URL parser + fetch + walk (slice 4)

src/aegis/cli_plugin.py    # `aegis plugin` typer subapp

plugins/skill-system/      # canonical plugin in aegis repo root
  plugin.toml
  skill_system.py
  _install.py

tests/test_hooks.py
tests/test_hook_loader.py  # tests for the plugin loader recursion changes
tests/test_tools.py
tests/test_plugin_manifest.py
tests/test_plugin_install.py
tests/test_plugin_uninstall.py
tests/test_plugin_lockfile.py
tests/test_plugin_registry.py
tests/test_plugin_cli.py
tests/test_skill_system.py
tests/test_skill_system_live.py     # marked @pytest.mark.live
```

### Modified files

- `src/aegis/config/yaml_loader.py::import_plugins` — recurse + skip underscores
- `src/aegis/core/session.py::_run_turn` — fire `pre_turn`, `post_turn`, `session_start`
- `src/aegis/core/session.py::close` — fire `session_end`
- `src/aegis/mcp/server.py::build_server` — register all `@tool`s after built-ins
- `src/aegis/cli.py` — mount the `aegis plugin` subapp

---

# Slice 1 — Hook substrate

Land hooks end-to-end: a fixture plugin defining `@hook("pre_turn")` modifies the message that reaches the harness, and all four Tier A events fire at the correct points.

## Task 1.1: Extend the plugin loader for full recursion + underscore-skip

**Files:**
- Modify: `src/aegis/config/yaml_loader.py:265-282` (the `import_plugins` function)
- Test: `tests/test_hook_loader.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hook_loader.py`:

```python
"""Tests for the plugin auto-import recursion + underscore-skip rules."""
from __future__ import annotations

import textwrap
from pathlib import Path

from aegis.config.yaml_loader import AegisConfig, import_plugins


def _make_cfg(plugin_dir: Path) -> AegisConfig:
    return AegisConfig(plugin_dirs=[plugin_dir])


def test_recurses_into_subfolders(tmp_path: Path) -> None:
    plug = tmp_path / "plugins"
    sub = plug / "skill-system" / "nested"
    sub.mkdir(parents=True)
    marker = tmp_path / "marker.txt"
    (sub / "deep.py").write_text(
        textwrap.dedent(f"""
        from pathlib import Path
        Path({str(marker)!r}).write_text("loaded")
        """)
    )
    import_plugins(_make_cfg(plug))
    assert marker.read_text() == "loaded"


def test_skips_underscore_prefixed_files(tmp_path: Path) -> None:
    plug = tmp_path / "plugins" / "my-plugin"
    plug.mkdir(parents=True)
    marker = tmp_path / "marker.txt"
    (plug / "_install.py").write_text(
        f"from pathlib import Path; Path({str(marker)!r}).write_text('x')"
    )
    import_plugins(_make_cfg(plug.parent))
    assert not marker.exists(), "_install.py must not be auto-imported"


def test_skips_underscore_prefixed_dirs(tmp_path: Path) -> None:
    plug = tmp_path / "plugins" / "my-plugin"
    cache = plug / "_cache"
    cache.mkdir(parents=True)
    marker = tmp_path / "marker.txt"
    (cache / "junk.py").write_text(
        f"from pathlib import Path; Path({str(marker)!r}).write_text('x')"
    )
    import_plugins(_make_cfg(plug.parent))
    assert not marker.exists(), "_cache/ must not be walked"


def test_existing_top_level_plugins_still_work(tmp_path: Path) -> None:
    plug = tmp_path / "plugins"
    plug.mkdir()
    marker = tmp_path / "marker.txt"
    (plug / "single_file.py").write_text(
        f"from pathlib import Path; Path({str(marker)!r}).write_text('ok')"
    )
    import_plugins(_make_cfg(plug))
    assert marker.read_text() == "ok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_hook_loader.py -v`
Expected: `test_recurses_into_subfolders` FAILS (deep.py not auto-imported); `test_skips_underscore_prefixed_files` likely PASSES already (top-level glob doesn't see it but the new rule must hold at any depth); `test_skips_underscore_prefixed_dirs` FAILS or N/A; `test_existing_top_level_plugins_still_work` PASSES.

- [ ] **Step 3: Replace `import_plugins` body**

Edit `src/aegis/config/yaml_loader.py`, replace the `import_plugins` function (lines ~265-282) with:

```python
def import_plugins(cfg: AegisConfig) -> None:
    """Auto-import every non-underscore-prefixed `*.py` under each
    configured plugin dir, recursively. Underscore-prefixed files and
    directories are skipped at any depth.

    Side effects: any `@workflow`, `@hook`, or `@tool` decorated
    function is registered. Import errors fail loud.
    """
    for d in cfg.plugin_dirs:
        if not d.is_dir():
            continue
        for path in _iter_plugin_files(d):
            mod_name = (
                "aegis_plugin_"
                + str(path.relative_to(d)).replace("/", "_").replace(".py", "")
            )
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                raise ConfigError(f"could not load plugin {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)


def _iter_plugin_files(root: Path):
    """Yield every `*.py` under `root`, recursively, skipping any path
    component whose basename starts with `_` or `.`.
    Order is deterministic (lexical by relative path)."""
    out: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part.startswith(("_", ".")) for part in path.relative_to(root).parts):
            continue
        out.append(path)
    out.sort(key=lambda p: str(p.relative_to(root)))
    yield from out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_hook_loader.py -v`
Expected: all four PASS.

- [ ] **Step 5: Run existing test suite to confirm no regression**

Run: `uv run pytest -q -m "not live"`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add tests/test_hook_loader.py src/aegis/config/yaml_loader.py
git commit -m "feat(plugins): plugin loader recurses; skip _-prefixed paths"
```

---

## Task 1.2: Hook contexts and event payloads

**Files:**
- Create: `src/aegis/hooks/contexts.py`
- Test: `tests/test_hook_contexts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_hook_contexts.py`:

```python
"""Frozen-dataclass behavior + field shape for hook context types."""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.hooks.contexts import (
    PostTurnEvent, PreTurnContext, PreTurnResult,
    SessionEndEvent, SessionHandle, SessionStartEvent, Turn,
)


def test_preturn_context_is_frozen() -> None:
    ctx = PreTurnContext(
        session=SessionHandle(handle="lucid-knuth", agent_profile="claude-sonnet", harness="claude"),
        user_message="hello",
        history=(),
        project_root=Path("/tmp"),
        prior_results=(),
    )
    with pytest.raises((AttributeError, Exception)):
        ctx.user_message = "no"


def test_preturn_result_all_fields_default_none() -> None:
    r = PreTurnResult()
    assert r.prepend_system is None
    assert r.rewrite_user is None
    assert r.block is None
    assert r.extend_history is None


def test_session_handle_carries_harness() -> None:
    h = SessionHandle(handle="x", agent_profile="p", harness="claude")
    assert h.harness == "claude"


def test_turn_carries_role_and_content() -> None:
    t = Turn(role="user", content="hi")
    assert t.role == "user"
    assert t.content == "hi"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_hook_contexts.py -v`
Expected: ImportError / ModuleNotFoundError on `aegis.hooks.contexts`.

- [ ] **Step 3: Create the contexts module**

Create `src/aegis/hooks/__init__.py` with:
```python
"""Aegis hook substrate."""
from aegis.hooks.contexts import (
    PostTurnEvent, PreTurnContext, PreTurnResult,
    SessionEndEvent, SessionHandle, SessionStartEvent, Turn,
)
from aegis.hooks.decorator import _REGISTRY, hook, list_hooks

__all__ = [
    "PostTurnEvent", "PreTurnContext", "PreTurnResult",
    "SessionEndEvent", "SessionHandle", "SessionStartEvent", "Turn",
    "_REGISTRY", "hook", "list_hooks",
]
```

Create `src/aegis/hooks/contexts.py`:

```python
"""Typed event payloads + result for the hook substrate."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SessionHandle:
    """Read-only view of a session's identity for hook consumption."""
    handle:        str
    agent_profile: str
    harness:       str


@dataclass(frozen=True)
class Turn:
    """One historical turn surfaced to hooks."""
    role:    str   # "user" or "assistant"
    content: str


@dataclass(frozen=True)
class PreTurnResult:
    """Optional return from a pre_turn hook. All fields optional."""
    prepend_system: str | None = None
    rewrite_user:   str | None = None
    block:          str | None = None
    extend_history: tuple[Turn, ...] | None = None


@dataclass(frozen=True)
class PreTurnContext:
    """Payload for pre_turn hooks. Read-only."""
    session:       SessionHandle
    user_message:  str
    history:       tuple[Turn, ...]
    project_root:  Path
    prior_results: tuple[PreTurnResult, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PostTurnEvent:
    """Payload for post_turn observers."""
    session:           SessionHandle
    user_message:      str
    assistant_message: str
    project_root:      Path


@dataclass(frozen=True)
class SessionStartEvent:
    session:      SessionHandle
    project_root: Path


@dataclass(frozen=True)
class SessionEndEvent:
    session:      SessionHandle
    project_root: Path
    reason:       str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_hook_contexts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/hooks/ tests/test_hook_contexts.py
git commit -m "feat(hooks): typed event payloads + PreTurnResult dataclasses"
```

---

## Task 1.3: `@hook` decorator + registry

**Files:**
- Create: `src/aegis/hooks/decorator.py`
- Test: `tests/test_hook_decorator.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_hook_decorator.py`:

```python
"""Hook registration: decorator, registry, name collisions, strict flag."""
from __future__ import annotations

import pytest

from aegis.hooks import _REGISTRY, hook
from aegis.hooks.decorator import HookEntry, _reset_registry_for_tests


@pytest.fixture(autouse=True)
def _clean() -> None:
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def test_hook_registers() -> None:
    @hook("pre_turn")
    async def my_hook(ctx):
        return None

    entries = _REGISTRY["pre_turn"]
    assert len(entries) == 1
    assert entries[0].func is my_hook
    assert entries[0].strict is False


def test_strict_flag_is_recorded() -> None:
    @hook("pre_turn", strict=True)
    async def my_hook(ctx):
        return None

    assert _REGISTRY["pre_turn"][0].strict is True


def test_unknown_event_fails_loud() -> None:
    with pytest.raises(ValueError, match="unknown hook event"):
        @hook("not_a_real_event")
        async def my_hook(ctx):
            return None


def test_duplicate_qualified_name_fails_loud() -> None:
    @hook("pre_turn")
    async def my_hook(ctx):
        return None

    with pytest.raises(ValueError, match="duplicate hook"):
        @hook("pre_turn")
        async def my_hook(ctx):  # noqa: F811
            return None


def test_observer_events_register() -> None:
    @hook("post_turn")
    async def a(ev): return None

    @hook("session_start")
    async def b(ev): return None

    @hook("session_end")
    async def c(ev): return None

    assert len(_REGISTRY["post_turn"]) == 1
    assert len(_REGISTRY["session_start"]) == 1
    assert len(_REGISTRY["session_end"]) == 1


def test_entries_preserve_declaration_order() -> None:
    @hook("pre_turn")
    async def first(ctx): return None

    @hook("pre_turn")
    async def second(ctx): return None

    @hook("pre_turn")
    async def third(ctx): return None

    funcs = [e.func.__name__ for e in _REGISTRY["pre_turn"]]
    assert funcs == ["first", "second", "third"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_hook_decorator.py -v`
Expected: ImportError on `aegis.hooks.decorator`.

- [ ] **Step 3: Create the decorator module**

Create `src/aegis/hooks/decorator.py`:

```python
"""@hook decorator + per-event registry."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

VALID_EVENTS = ("pre_turn", "post_turn", "session_start", "session_end")


@dataclass(frozen=True)
class HookEntry:
    event:    str
    func:     Callable[..., Any]
    strict:   bool
    qualname: str   # for duplicate detection + log messages


_REGISTRY: dict[str, list[HookEntry]] = {ev: [] for ev in VALID_EVENTS}


def hook(event: str, *, strict: bool = False) -> Callable[[Callable], Callable]:
    """Register an async function as a hook for `event`.

    Args:
        event: one of "pre_turn", "post_turn", "session_start", "session_end".
        strict: if True, an exception raised inside this hook blocks the turn
                with the exception's string in PreTurnResult.block. Default
                False (log-and-skip; turn proceeds).
    """
    if event not in VALID_EVENTS:
        raise ValueError(
            f"unknown hook event {event!r}; valid: {VALID_EVENTS}"
        )

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        qualname = f"{fn.__module__}.{fn.__qualname__}"
        if any(e.qualname == qualname for e in _REGISTRY[event]):
            raise ValueError(f"duplicate hook {qualname!r} for {event!r}")
        _REGISTRY[event].append(
            HookEntry(event=event, func=fn, strict=strict, qualname=qualname)
        )
        return fn

    return decorate


def list_hooks(event: str | None = None) -> list[HookEntry]:
    """Return registered hooks for `event` (or all if None)."""
    if event is None:
        return [e for evs in _REGISTRY.values() for e in evs]
    return list(_REGISTRY.get(event, ()))


def _reset_registry_for_tests() -> None:
    for ev in VALID_EVENTS:
        _REGISTRY[ev].clear()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_hook_decorator.py -v`
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/hooks/decorator.py tests/test_hook_decorator.py src/aegis/hooks/__init__.py
git commit -m "feat(hooks): @hook decorator + per-event registry"
```

---

## Task 1.4: Composer — pre_turn result composition rules

**Files:**
- Create: `src/aegis/hooks/composer.py`
- Test: `tests/test_hook_composer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_hook_composer.py`:

```python
"""PreTurnResult composition: block short-circuit, rewrite conflicts,
prepend concatenation, history extension."""
from __future__ import annotations

import pytest

from aegis.hooks.composer import ComposerError, compose_pre_turn
from aegis.hooks.contexts import PreTurnResult, Turn


def test_empty_composes_to_no_op() -> None:
    composed = compose_pre_turn([])
    assert composed == PreTurnResult()


def test_single_result_passes_through() -> None:
    r = PreTurnResult(prepend_system="hello")
    composed = compose_pre_turn([r])
    assert composed.prepend_system == "hello"


def test_prepend_system_concatenates_in_order() -> None:
    results = [
        PreTurnResult(prepend_system="A"),
        PreTurnResult(prepend_system="B"),
        PreTurnResult(prepend_system="C"),
    ]
    composed = compose_pre_turn(results)
    assert composed.prepend_system == "A\n\nB\n\nC"


def test_prepend_system_skips_none() -> None:
    results = [
        PreTurnResult(prepend_system="A"),
        PreTurnResult(),
        PreTurnResult(prepend_system="C"),
    ]
    assert compose_pre_turn(results).prepend_system == "A\n\nC"


def test_block_short_circuits() -> None:
    results = [
        PreTurnResult(prepend_system="ignored after block"),
        PreTurnResult(block="reason"),
    ]
    composed = compose_pre_turn(results)
    assert composed.block == "reason"
    assert composed.prepend_system == "ignored after block"


def test_first_block_wins() -> None:
    results = [
        PreTurnResult(block="first"),
        PreTurnResult(block="second"),
    ]
    assert compose_pre_turn(results).block == "first"


def test_rewrite_user_conflict_fails_loud() -> None:
    results = [
        PreTurnResult(rewrite_user="a"),
        PreTurnResult(rewrite_user="b"),
    ]
    with pytest.raises(ComposerError, match="rewrite_user"):
        compose_pre_turn(results)


def test_rewrite_user_single_passes() -> None:
    results = [PreTurnResult(rewrite_user="new")]
    assert compose_pre_turn(results).rewrite_user == "new"


def test_extend_history_concatenates_in_order() -> None:
    a = (Turn(role="user", content="x"),)
    b = (Turn(role="assistant", content="y"),)
    composed = compose_pre_turn([
        PreTurnResult(extend_history=a),
        PreTurnResult(extend_history=b),
    ])
    assert composed.extend_history == (
        Turn(role="user", content="x"),
        Turn(role="assistant", content="y"),
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_hook_composer.py -v`
Expected: ImportError on `aegis.hooks.composer`.

- [ ] **Step 3: Create the composer module**

Create `src/aegis/hooks/composer.py`:

```python
"""Compose multiple PreTurnResults from a single pre_turn fire."""
from __future__ import annotations

from aegis.hooks.contexts import PreTurnResult


class ComposerError(Exception):
    """Two or more hooks returned conflicting mutations this turn."""


def compose_pre_turn(results: list[PreTurnResult]) -> PreTurnResult:
    """Apply the composition rules from the design spec.

    Rules:
    - prepend_system strings concatenate in declaration order, separated by "\\n\\n".
    - rewrite_user: at most one non-None across all results, else fail-loud.
    - block: first non-None wins; later block values are ignored (but their
      sibling fields are still recorded so users can introspect).
    - extend_history: tuples concatenate in declaration order.
    """
    prepends:  list[str] = []
    rewrite:   str | None = None
    block:     str | None = None
    history:   list = []

    for r in results:
        if r.prepend_system is not None:
            prepends.append(r.prepend_system)
        if r.rewrite_user is not None:
            if rewrite is not None:
                raise ComposerError(
                    "two hooks returned rewrite_user; only one allowed"
                )
            rewrite = r.rewrite_user
        if r.block is not None and block is None:
            block = r.block
        if r.extend_history:
            history.extend(r.extend_history)

    return PreTurnResult(
        prepend_system="\n\n".join(prepends) if prepends else None,
        rewrite_user=rewrite,
        block=block,
        extend_history=tuple(history) if history else None,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_hook_composer.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/hooks/composer.py tests/test_hook_composer.py
git commit -m "feat(hooks): composer for pre_turn result merging"
```

---

## Task 1.5: Runner — invocation with timeout + logging

**Files:**
- Create: `src/aegis/hooks/runner.py`
- Test: `tests/test_hook_runner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_hook_runner.py`:

```python
"""Hook invocation: timeout, log-and-skip vs strict, JSONL logging."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from aegis.hooks.contexts import (
    PreTurnContext, PreTurnResult, SessionHandle,
)
from aegis.hooks.decorator import HookEntry, _reset_registry_for_tests
from aegis.hooks.runner import run_pre_turn_hooks


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _ctx(tmp_path: Path) -> PreTurnContext:
    return PreTurnContext(
        session=SessionHandle(handle="x", agent_profile="p", harness="claude"),
        user_message="hi",
        history=(),
        project_root=tmp_path,
        prior_results=(),
    )


@pytest.mark.asyncio
async def test_runs_a_hook_and_returns_result(tmp_path: Path) -> None:
    async def my_hook(ctx):
        return PreTurnResult(prepend_system="hello")
    entries = [HookEntry(event="pre_turn", func=my_hook, strict=False,
                         qualname="t.my_hook")]
    composed = await run_pre_turn_hooks(_ctx(tmp_path), entries, state_dir=tmp_path / "state")
    assert composed.prepend_system == "hello"


@pytest.mark.asyncio
async def test_hook_exception_is_logged_and_skipped(tmp_path: Path) -> None:
    async def bad(ctx):
        raise RuntimeError("boom")
    async def good(ctx):
        return PreTurnResult(prepend_system="ok")
    entries = [
        HookEntry(event="pre_turn", func=bad, strict=False, qualname="t.bad"),
        HookEntry(event="pre_turn", func=good, strict=False, qualname="t.good"),
    ]
    state = tmp_path / "state"
    composed = await run_pre_turn_hooks(_ctx(tmp_path), entries, state_dir=state)
    assert composed.prepend_system == "ok"
    log = state / "hooks" / "t.bad.jsonl"
    assert log.exists()
    rec = json.loads(log.read_text().strip().splitlines()[-1])
    assert rec["status"] == "exception"
    assert "boom" in rec["error"]


@pytest.mark.asyncio
async def test_strict_hook_exception_blocks_turn(tmp_path: Path) -> None:
    async def bad(ctx):
        raise RuntimeError("boom")
    entries = [HookEntry(event="pre_turn", func=bad, strict=True,
                         qualname="t.bad")]
    composed = await run_pre_turn_hooks(_ctx(tmp_path), entries, state_dir=tmp_path / "state")
    assert composed.block is not None
    assert "boom" in composed.block


@pytest.mark.asyncio
async def test_hook_timeout_logs_and_skips(tmp_path: Path) -> None:
    async def slow(ctx):
        await asyncio.sleep(10)
        return PreTurnResult(prepend_system="never")
    entries = [HookEntry(event="pre_turn", func=slow, strict=False,
                         qualname="t.slow")]
    state = tmp_path / "state"
    composed = await run_pre_turn_hooks(
        _ctx(tmp_path), entries, state_dir=state, timeout=0.05
    )
    assert composed.prepend_system is None
    log = state / "hooks" / "t.slow.jsonl"
    rec = json.loads(log.read_text().strip().splitlines()[-1])
    assert rec["status"] == "timeout"


@pytest.mark.asyncio
async def test_success_logged(tmp_path: Path) -> None:
    async def ok(ctx):
        return PreTurnResult(prepend_system="x")
    entries = [HookEntry(event="pre_turn", func=ok, strict=False,
                         qualname="t.ok")]
    state = tmp_path / "state"
    await run_pre_turn_hooks(_ctx(tmp_path), entries, state_dir=state)
    log = state / "hooks" / "t.ok.jsonl"
    rec = json.loads(log.read_text().strip().splitlines()[-1])
    assert rec["status"] == "ok"
```

Add `pytest-asyncio` is already in the project (used by existing async tests); the `@pytest.mark.asyncio` is standard.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_hook_runner.py -v`
Expected: ImportError on `aegis.hooks.runner`.

- [ ] **Step 3: Create the runner module**

Create `src/aegis/hooks/runner.py`:

```python
"""Hook invocation with timeout, exception handling, and JSONL logging."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from aegis.hooks.composer import compose_pre_turn
from aegis.hooks.contexts import (
    PostTurnEvent, PreTurnContext, PreTurnResult,
    SessionEndEvent, SessionStartEvent,
)
from aegis.hooks.decorator import HookEntry

DEFAULT_TIMEOUT_S = 5.0


async def run_pre_turn_hooks(
    ctx: PreTurnContext,
    entries: list[HookEntry],
    *,
    state_dir: Path,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> PreTurnResult:
    """Run all pre_turn hooks in declaration order, composing the result.

    Each hook sees `ctx.prior_results` populated with results from earlier
    hooks of this turn. Per-hook exceptions/timeouts are logged to
    `state_dir/hooks/<qualname>.jsonl`. Strict hooks turn an exception
    into a `block` result; non-strict hooks log-and-skip.
    """
    results: list[PreTurnResult] = []
    for entry in entries:
        ctx_for_hook = PreTurnContext(
            session=ctx.session,
            user_message=ctx.user_message,
            history=ctx.history,
            project_root=ctx.project_root,
            prior_results=tuple(results),
        )
        result = await _invoke_with_timeout(
            entry, ctx_for_hook, state_dir=state_dir, timeout=timeout,
        )
        if result is not None:
            results.append(result)
    return compose_pre_turn(results)


async def run_observer_hooks(
    event: PostTurnEvent | SessionStartEvent | SessionEndEvent,
    entries: list[HookEntry],
    *,
    state_dir: Path,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> None:
    """Fire every observer hook for an event. Return value ignored."""
    for entry in entries:
        await _invoke_with_timeout(
            entry, event, state_dir=state_dir, timeout=timeout,
        )


async def _invoke_with_timeout(
    entry: HookEntry,
    payload: Any,
    *,
    state_dir: Path,
    timeout: float,
) -> PreTurnResult | None:
    """Invoke `entry.func(payload)` with timeout + JSONL logging.

    Returns the function's return value on success; None on timeout/exception.
    For strict pre_turn hooks, exceptions are converted to
    PreTurnResult(block=str(exc)) and returned.
    """
    log_path = state_dir / "hooks" / f"{entry.qualname}.jsonl"
    started = time.time()
    try:
        result = await asyncio.wait_for(entry.func(payload), timeout=timeout)
        _log(log_path, status="ok", entry=entry, started=started)
        return result
    except asyncio.TimeoutError:
        _log(log_path, status="timeout", entry=entry, started=started)
        return None
    except Exception as exc:  # noqa: BLE001 — log + skip semantics
        _log(log_path, status="exception", entry=entry, started=started,
             error=f"{type(exc).__name__}: {exc}")
        if entry.strict and entry.event == "pre_turn":
            return PreTurnResult(
                block=f"strict hook {entry.qualname} raised: {exc}"
            )
        return None


def _log(
    path: Path,
    *,
    status: str,
    entry: HookEntry,
    started: float,
    error: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts":       time.time(),
        "duration": time.time() - started,
        "event":    entry.event,
        "qualname": entry.qualname,
        "strict":   entry.strict,
        "status":   status,
    }
    if error is not None:
        rec["error"] = error
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_hook_runner.py -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/hooks/runner.py tests/test_hook_runner.py
git commit -m "feat(hooks): runner with timeout, log-and-skip, JSONL logging"
```

---

## Task 1.6: Wire `pre_turn` and `post_turn` into the session turn loop

**Files:**
- Modify: `src/aegis/core/session.py` (`_run_turn`)
- Test: `tests/test_session_hook_wiring.py`

The wiring concept: at the start of `_run_turn`, fire pre_turn hooks. If composed result has `block`, surface it as a synthetic assistant message and return. Otherwise, apply `rewrite_user` if present, then prepend the composed `prepend_system` to the user message inside a clear `<aegis_context>...</aegis_context>` delimiter. After the harness emits its final `Result`, fire `post_turn` observers with the final assistant text.

Per-turn system context lives on the user message because all aegis-driven harnesses (Claude `-p` stream-json, Gemini, OpenCode, ACP) accept user-message text as the per-turn input channel; the system prompt is set at spawn time and cannot be cheaply mutated per turn.

- [ ] **Step 1: Write the failing test**

Create `tests/test_session_hook_wiring.py`:

```python
"""Pre_turn / post_turn hooks fire at the right point in _run_turn.

Uses a fake harness session that captures the text sent to it, so we can
assert the message reaching the harness has the composed prepend_system
inlined as a <aegis_context>...</aegis_context> block.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aegis.hooks import hook
from aegis.hooks.contexts import PreTurnResult
from aegis.hooks.decorator import _reset_registry_for_tests


class FakeResult:
    def __init__(self, text: str = "ok"):
        self.usage = None
        self.is_error = False
        self.text = text


class FakeHarnessSession:
    """Minimal stand-in for HarnessSession. Captures sends."""
    def __init__(self):
        self.sent: list[str] = []
        self._events_q: asyncio.Queue = asyncio.Queue()
        self.started = False

    async def start(self): self.started = True
    async def send(self, text: str):
        self.sent.append(text)
        await self._events_q.put(FakeResult("response text"))

    async def events(self):
        while True:
            ev = await self._events_q.get()
            yield ev
            return  # one event per turn

    async def close(self, reason: str = ""): pass


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


@pytest.mark.asyncio
async def test_pre_turn_prepend_system_reaches_harness(tmp_path: Path) -> None:
    @hook("pre_turn")
    async def inject(ctx):
        return PreTurnResult(prepend_system="LOAD-X")

    from aegis.core.session import AgentSession  # late import after hook reg
    harness = FakeHarnessSession()
    session = AgentSession(
        handle="t",
        agent_profile="p",
        harness="claude",
        harness_session=harness,
        project_root=tmp_path,
    )
    await session.send_and_wait("hello user")
    assert len(harness.sent) == 1
    sent = harness.sent[0]
    assert "<aegis_context>" in sent
    assert "LOAD-X" in sent
    assert "hello user" in sent


@pytest.mark.asyncio
async def test_block_short_circuits_no_send(tmp_path: Path) -> None:
    @hook("pre_turn")
    async def blocker(ctx):
        return PreTurnResult(block="not allowed")

    from aegis.core.session import AgentSession
    harness = FakeHarnessSession()
    session = AgentSession(
        handle="t", agent_profile="p", harness="claude",
        harness_session=harness, project_root=tmp_path,
    )
    result = await session.send_and_wait("hi")
    assert harness.sent == []
    assert result.blocked_reason == "not allowed"


@pytest.mark.asyncio
async def test_post_turn_fires_with_assistant_text(tmp_path: Path) -> None:
    captured = {}

    @hook("post_turn")
    async def record(ev):
        captured["text"] = ev.assistant_message
        captured["user"] = ev.user_message

    from aegis.core.session import AgentSession
    harness = FakeHarnessSession()
    session = AgentSession(
        handle="t", agent_profile="p", harness="claude",
        harness_session=harness, project_root=tmp_path,
    )
    await session.send_and_wait("hi")
    # Allow observer hooks to run
    await asyncio.sleep(0.05)
    assert captured.get("user") == "hi"
    assert "response" in captured.get("text", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session_hook_wiring.py -v`
Expected: AttributeError on `send_and_wait` (or missing arg `project_root` in `AgentSession.__init__`).

- [ ] **Step 3: Add a hook-aware `send_and_wait` to `AgentSession`**

Modify `src/aegis/core/session.py`. Add at module top imports:

```python
from aegis.hooks import (
    PostTurnEvent, PreTurnContext, PreTurnResult, SessionHandle, Turn,
)
from aegis.hooks.decorator import _REGISTRY as _HOOK_REG
from aegis.hooks.runner import run_observer_hooks, run_pre_turn_hooks
```

Extend `AgentSession.__init__` signature to accept `project_root: Path | None = None`, defaulting to `Path.cwd()`. Store as `self.project_root`.

Add the following method to `AgentSession`:

```python
async def send_and_wait(self, user_message: str):
    """Hook-aware send. Fires pre_turn before delivery; post_turn after.

    Returns a small TurnResult dataclass with .blocked_reason set if a
    pre_turn hook blocked the turn (in which case the harness was not
    contacted), else .assistant_message with the harness's final text.
    """
    history = self._collect_history()
    ctx = PreTurnContext(
        session=SessionHandle(
            handle=self.handle, agent_profile=self.agent_profile,
            harness=self.harness,
        ),
        user_message=user_message,
        history=tuple(history),
        project_root=self.project_root,
    )
    state_dir = self.project_root / ".aegis" / "state"
    composed = await run_pre_turn_hooks(
        ctx, list(_HOOK_REG.get("pre_turn", [])), state_dir=state_dir,
    )
    if composed.block is not None:
        return _TurnResult(blocked_reason=composed.block, assistant_message=None)

    effective_user = composed.rewrite_user or user_message
    if composed.prepend_system:
        effective_user = (
            f"<aegis_context>\n{composed.prepend_system}\n</aegis_context>\n\n"
            f"{effective_user}"
        )

    if not self._started:
        await self.harness_session.start()
        self._started = True
        await run_observer_hooks(
            _SessionStartEvent_for(self), list(_HOOK_REG.get("session_start", [])),
            state_dir=state_dir,
        )

    await self.harness_session.send(effective_user)
    final_text = ""
    async for ev in self.harness_session.events():
        if hasattr(ev, "text"):
            final_text = ev.text
    await run_observer_hooks(
        PostTurnEvent(
            session=ctx.session, user_message=user_message,
            assistant_message=final_text, project_root=self.project_root,
        ),
        list(_HOOK_REG.get("post_turn", [])),
        state_dir=state_dir,
    )
    return _TurnResult(blocked_reason=None, assistant_message=final_text)


def _collect_history(self) -> list:
    # Stub for v1: hooks see an empty history. Threading the real
    # session history into hooks lives in a follow-up.
    return []
```

Add the auxiliary dataclass at module bottom:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class _TurnResult:
    blocked_reason:    str | None
    assistant_message: str | None


def _SessionStartEvent_for(s):
    from aegis.hooks.contexts import SessionStartEvent
    return SessionStartEvent(
        session=SessionHandle(
            handle=s.handle, agent_profile=s.agent_profile, harness=s.harness,
        ),
        project_root=s.project_root,
    )
```

Note: this introduces `send_and_wait` as a new hook-aware path *alongside* the existing `_run_turn`/`deliver` path. The existing path remains unchanged so production TUI/Telegram flows are untouched in this slice. Slice 1 ends with the new path verified; integrating into `_run_turn` is Task 1.7.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_session_hook_wiring.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/core/session.py tests/test_session_hook_wiring.py
git commit -m "feat(hooks): send_and_wait — pre_turn + post_turn firing path"
```

---

## Task 1.7: Integrate pre_turn / post_turn into the production `_run_turn`

**Files:**
- Modify: `src/aegis/core/session.py:143-190` (`_run_turn` body)
- Test: `tests/test_session_hook_wiring.py` (extend)

The goal: production sessions delivered via `deliver()`/`_run_turn()` honor pre_turn + post_turn hooks. The standalone `send_and_wait` method from Task 1.6 was useful as a TDD scaffold; this task threads the same logic into the live turn loop.

- [ ] **Step 1: Extend the test to cover the live path**

Append to `tests/test_session_hook_wiring.py`:

```python
@pytest.mark.asyncio
async def test_run_turn_honors_pre_turn(tmp_path: Path) -> None:
    @hook("pre_turn")
    async def inject(ctx):
        return PreTurnResult(prepend_system="LIVE")

    from aegis.core.session import AgentSession
    harness = FakeHarnessSession()
    session = AgentSession(
        handle="t", agent_profile="p", harness="claude",
        harness_session=harness, project_root=tmp_path,
    )
    # use the production deliver/_run_turn path
    await session._run_turn("user-msg")
    assert "LIVE" in harness.sent[0]
    assert "user-msg" in harness.sent[0]
```

- [ ] **Step 2: Run to verify the new test fails**

Run: `uv run pytest tests/test_session_hook_wiring.py::test_run_turn_honors_pre_turn -v`
Expected: FAIL — `harness.sent[0]` lacks "LIVE".

- [ ] **Step 3: Modify `_run_turn` to call the hook-aware send path**

In `src/aegis/core/session.py`, change `_run_turn(self, text: str)` body. Before `await self._session.send(text)` (currently line ~151), insert the pre_turn hook firing logic and replace the literal `text` argument with the composed effective message:

```python
async def _run_turn(self, text: str) -> None:
    saw_result = False
    try:
        if not self._started:
            await self._session.start()
            self._started = True
            self.metrics.begin_session(self._now())
            await run_observer_hooks(
                _SessionStartEvent_for(self),
                list(_HOOK_REG.get("session_start", [])),
                state_dir=self.project_root / ".aegis" / "state",
            )

        composed = await run_pre_turn_hooks(
            PreTurnContext(
                session=SessionHandle(
                    handle=self.handle, agent_profile=self.agent_profile,
                    harness=self.harness,
                ),
                user_message=text,
                history=(),
                project_root=self.project_root,
            ),
            list(_HOOK_REG.get("pre_turn", [])),
            state_dir=self.project_root / ".aegis" / "state",
        )
        if composed.block is not None:
            self._inject_synthetic_assistant(
                f"[turn blocked by aegis hook] {composed.block}"
            )
            return

        effective = composed.rewrite_user or text
        if composed.prepend_system:
            effective = (
                f"<aegis_context>\n{composed.prepend_system}\n"
                f"</aegis_context>\n\n{effective}"
            )

        await self._session.send(effective)
        final_text = ""
        async for ev in self._session.events():
            ...  # existing event-handling body, unchanged
            if isinstance(ev, Result):
                # capture final text for post_turn observer
                final_text = getattr(ev, "text", "") or ""

        await run_observer_hooks(
            PostTurnEvent(
                session=SessionHandle(
                    handle=self.handle, agent_profile=self.agent_profile,
                    harness=self.harness,
                ),
                user_message=text, assistant_message=final_text,
                project_root=self.project_root,
            ),
            list(_HOOK_REG.get("post_turn", [])),
            state_dir=self.project_root / ".aegis" / "state",
        )
    ...
```

(Preserve the existing except / final blocks unchanged. Use `_inject_synthetic_assistant` — a small new helper that emits a synthetic `Result` event through `self.on_event` so the TUI sees the block message.)

Add the helper:

```python
def _inject_synthetic_assistant(self, body: str) -> None:
    from aegis.events import Result
    fake = Result(usage=None, is_error=False, text=body)
    if self.on_event is not None:
        self.on_event(self, fake)
    self.metrics.commit(None, self._now())
    self._emit_state(AgentState.ready, finished=True)
```

If the existing `Result` event class doesn't carry `text`, swap the helper to emit whatever event shape carries assistant content in the live driver. Verify shape with `grep -n "class Result" src/aegis/events.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_session_hook_wiring.py -v`
Expected: 4 tests PASS (old 3 + new one).

- [ ] **Step 5: Run the full hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: green. If existing core/session tests break, the most likely culprit is the synthetic assistant injection — verify the `Result` event shape and adjust the helper.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/core/session.py tests/test_session_hook_wiring.py
git commit -m "feat(hooks): _run_turn fires pre_turn + post_turn + session_start"
```

---

## Task 1.8: Wire `session_end` into `AgentSession.close`

**Files:**
- Modify: `src/aegis/core/session.py::close` (line ~214)
- Test: `tests/test_session_hook_wiring.py` (extend)

- [ ] **Step 1: Add the failing test**

Append to `tests/test_session_hook_wiring.py`:

```python
@pytest.mark.asyncio
async def test_session_end_fires_on_close(tmp_path: Path) -> None:
    captured = {}

    @hook("session_end")
    async def record(ev):
        captured["reason"] = ev.reason
        captured["handle"] = ev.session.handle

    from aegis.core.session import AgentSession
    harness = FakeHarnessSession()
    session = AgentSession(
        handle="t", agent_profile="p", harness="claude",
        harness_session=harness, project_root=tmp_path,
    )
    await session._run_turn("hi")
    await session.close(reason="test-close")
    await asyncio.sleep(0.05)
    assert captured.get("reason") == "test-close"
    assert captured.get("handle") == "t"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_session_hook_wiring.py::test_session_end_fires_on_close -v`
Expected: FAIL — captured dict empty.

- [ ] **Step 3: Modify `AgentSession.close`**

Edit `close` body at line ~214. After the existing close logic (before `_emit_close` returns), add:

```python
from aegis.hooks.contexts import SessionEndEvent
await run_observer_hooks(
    SessionEndEvent(
        session=SessionHandle(
            handle=self.handle, agent_profile=self.agent_profile,
            harness=self.harness,
        ),
        project_root=self.project_root,
        reason=reason,
    ),
    list(_HOOK_REG.get("session_end", [])),
    state_dir=self.project_root / ".aegis" / "state",
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_session_hook_wiring.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/core/session.py tests/test_session_hook_wiring.py
git commit -m "feat(hooks): session_end fires on AgentSession.close"
```

---

## Task 1.9: End-to-end fixture-plugin integration test

**Files:**
- Create: `tests/test_hooks_e2e.py`

Verifies that a real plugin folder loaded through `import_plugins` registers all four hook event types, and that firing each event invokes the right registered hook.

- [ ] **Step 1: Write the integration test**

Create `tests/test_hooks_e2e.py`:

```python
"""End-to-end: plugin folder with hooks at all four events loads, runs."""
from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest

from aegis.config.yaml_loader import AegisConfig, import_plugins
from aegis.hooks.decorator import _REGISTRY, _reset_registry_for_tests


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _drop_plugin(plug_root: Path, marker: Path) -> None:
    p = plug_root / "test-plugin"
    p.mkdir(parents=True)
    (p / "plugin.toml").write_text('[plugin]\nname = "test-plugin"\nversion = "0.0.1"\n')
    (p / "hooks.py").write_text(textwrap.dedent(f"""
        from aegis.hooks import hook, PreTurnResult
        from pathlib import Path

        MARKER = Path({str(marker)!r})

        @hook("pre_turn")
        async def pre(ctx):
            MARKER.write_text((MARKER.read_text() if MARKER.exists() else "") + "pre\\n")
            return PreTurnResult(prepend_system="HELLO")

        @hook("post_turn")
        async def post(ev):
            MARKER.write_text((MARKER.read_text() if MARKER.exists() else "") + "post\\n")

        @hook("session_start")
        async def s_start(ev):
            MARKER.write_text((MARKER.read_text() if MARKER.exists() else "") + "start\\n")

        @hook("session_end")
        async def s_end(ev):
            MARKER.write_text((MARKER.read_text() if MARKER.exists() else "") + "end\\n")
    """))


@pytest.mark.asyncio
async def test_full_lifecycle_fires_all_events(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    plug = tmp_path / "plugins"
    _drop_plugin(plug, marker)

    cfg = AegisConfig(plugin_dirs=[plug])
    import_plugins(cfg)
    assert len(_REGISTRY["pre_turn"]) == 1
    assert len(_REGISTRY["post_turn"]) == 1
    assert len(_REGISTRY["session_start"]) == 1
    assert len(_REGISTRY["session_end"]) == 1

    # Now drive a session through the full lifecycle.
    from tests.test_session_hook_wiring import FakeHarnessSession
    from aegis.core.session import AgentSession
    harness = FakeHarnessSession()
    session = AgentSession(
        handle="t", agent_profile="p", harness="claude",
        harness_session=harness, project_root=tmp_path,
    )
    await session._run_turn("hi")
    await session.close(reason="done")
    await asyncio.sleep(0.05)

    events = [line for line in marker.read_text().splitlines() if line]
    assert events == ["start", "pre", "post", "end"]
    assert "HELLO" in harness.sent[0]
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/test_hooks_e2e.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_hooks_e2e.py
git commit -m "test(hooks): end-to-end lifecycle through a fixture plugin"
```

**Slice 1 complete.** Hook substrate live; pre_turn + post_turn + session_start + session_end fire from `_run_turn`/`close` via the production path; fixture plugin proves end-to-end loading and execution.

---

# Slice 2 — Tool substrate

Land tools end-to-end: a `@tool`-decorated async function becomes a first-class MCP tool visible in spawned Claude sessions, with timeout and JSONL logging.

## Task 2.1: `@tool` decorator + registry

**Files:**
- Create: `src/aegis/tools/__init__.py`, `src/aegis/tools/decorator.py`
- Test: `tests/test_tool_decorator.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tool_decorator.py`:

```python
"""Tool registration: decorator, registry, name collisions."""
from __future__ import annotations

import pytest

from aegis.tools import _REGISTRY, tool
from aegis.tools.decorator import _reset_registry_for_tests


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def test_tool_registers_under_function_name() -> None:
    @tool
    async def load_skill(name: str) -> str:
        """Load a skill body."""
        return ""
    assert "load_skill" in _REGISTRY
    assert _REGISTRY["load_skill"].func.__name__ == "load_skill"


def test_explicit_name_override() -> None:
    @tool(name="custom_name")
    async def foo() -> str:
        return ""
    assert "custom_name" in _REGISTRY
    assert "foo" not in _REGISTRY


def test_duplicate_name_fails_loud() -> None:
    @tool
    async def x() -> str: return ""
    with pytest.raises(ValueError, match="duplicate tool"):
        @tool
        async def x() -> str:  # noqa: F811
            return ""


def test_collision_with_aegis_builtin_fails_loud() -> None:
    with pytest.raises(ValueError, match="reserved"):
        @tool(name="aegis_enqueue")
        async def x() -> str:
            return ""


def test_default_timeout_30s_explicit_override() -> None:
    @tool
    async def a() -> str: return ""
    @tool(timeout=10.0)
    async def b() -> str: return ""
    assert _REGISTRY["a"].timeout == 30.0
    assert _REGISTRY["b"].timeout == 10.0


def test_sync_function_also_supported() -> None:
    @tool
    def plain() -> str:
        return "hi"
    assert "plain" in _REGISTRY
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tool_decorator.py -v`
Expected: ImportError on `aegis.tools`.

- [ ] **Step 3: Create the modules**

Create `src/aegis/tools/__init__.py`:

```python
"""Aegis tool substrate."""
from aegis.tools.decorator import _REGISTRY, ToolEntry, list_tools, tool

__all__ = ["_REGISTRY", "ToolEntry", "list_tools", "tool"]
```

Create `src/aegis/tools/decorator.py`:

```python
"""@tool decorator + global registry."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, overload

# Names that conflict with built-in aegis MCP tools.
RESERVED_NAMES = frozenset({
    "aegis_meta", "aegis_list_sessions", "aegis_list_agents",
    "aegis_handoff", "aegis_enqueue", "aegis_task_status",
    "aegis_run_workflow",
    "aegis_group_spawn", "aegis_group_broadcast", "aegis_group_wait_all",
    "aegis_group_wait_any", "aegis_group_cancel", "aegis_group_close",
    "aegis_group_list", "aegis_group_status", "aegis_group_spawn_mixed",
})

DEFAULT_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class ToolEntry:
    name:     str
    func:     Callable[..., Any]
    timeout:  float
    qualname: str


_REGISTRY: dict[str, ToolEntry] = {}


@overload
def tool(fn: Callable) -> Callable: ...
@overload
def tool(*, name: str | None = None, timeout: float = DEFAULT_TIMEOUT_S) -> Callable: ...
def tool(fn=None, *, name: str | None = None, timeout: float = DEFAULT_TIMEOUT_S):
    """Register a function as a first-class MCP tool.

    Usage:
        @tool
        async def my_tool(x: int) -> str: ...

        @tool(name="explicit", timeout=10.0)
        def sync_tool() -> str: ...
    """
    def decorate(f: Callable) -> Callable:
        n = name or f.__name__
        if n in RESERVED_NAMES:
            raise ValueError(f"tool name {n!r} is reserved by aegis")
        if n in _REGISTRY:
            raise ValueError(f"duplicate tool {n!r}")
        _REGISTRY[n] = ToolEntry(
            name=n, func=f, timeout=timeout,
            qualname=f"{f.__module__}.{f.__qualname__}",
        )
        return f
    if fn is not None:
        return decorate(fn)
    return decorate


def list_tools() -> list[ToolEntry]:
    return list(_REGISTRY.values())


def _reset_registry_for_tests() -> None:
    _REGISTRY.clear()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tool_decorator.py -v`
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tools/ tests/test_tool_decorator.py
git commit -m "feat(tools): @tool decorator + registry with reserved-name guard"
```

---

## Task 2.2: Tool invocation wrapper — timeout + JSONL logging

**Files:**
- Create: `src/aegis/tools/runner.py`
- Test: `tests/test_tool_runner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tool_runner.py`:

```python
"""Tool invocation: timeout, exception handling, JSONL logging."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from aegis.tools.decorator import ToolEntry
from aegis.tools.runner import ToolTimeout, invoke_tool


@pytest.mark.asyncio
async def test_invoke_returns_result(tmp_path: Path) -> None:
    async def my_tool(x: int) -> str: return f"got {x}"
    entry = ToolEntry(name="my_tool", func=my_tool, timeout=5.0, qualname="t.my")
    out = await invoke_tool(entry, kwargs={"x": 7}, state_dir=tmp_path)
    assert out == "got 7"
    log = tmp_path / "tools" / "my_tool.jsonl"
    rec = json.loads(log.read_text().strip().splitlines()[-1])
    assert rec["status"] == "ok"


@pytest.mark.asyncio
async def test_timeout_raises_typed_error(tmp_path: Path) -> None:
    async def slow() -> str:
        await asyncio.sleep(10); return "no"
    entry = ToolEntry(name="slow", func=slow, timeout=0.05, qualname="t.slow")
    with pytest.raises(ToolTimeout):
        await invoke_tool(entry, kwargs={}, state_dir=tmp_path)
    log = tmp_path / "tools" / "slow.jsonl"
    rec = json.loads(log.read_text().strip().splitlines()[-1])
    assert rec["status"] == "timeout"


@pytest.mark.asyncio
async def test_sync_function_invoked(tmp_path: Path) -> None:
    def plain() -> str: return "sync"
    entry = ToolEntry(name="plain", func=plain, timeout=5.0, qualname="t.plain")
    assert await invoke_tool(entry, kwargs={}, state_dir=tmp_path) == "sync"


@pytest.mark.asyncio
async def test_exception_logged_and_reraised(tmp_path: Path) -> None:
    async def boom() -> str: raise RuntimeError("nope")
    entry = ToolEntry(name="boom", func=boom, timeout=5.0, qualname="t.boom")
    with pytest.raises(RuntimeError, match="nope"):
        await invoke_tool(entry, kwargs={}, state_dir=tmp_path)
    rec = json.loads(
        (tmp_path / "tools" / "boom.jsonl").read_text().strip().splitlines()[-1]
    )
    assert rec["status"] == "exception"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tool_runner.py -v`
Expected: ImportError on `aegis.tools.runner`.

- [ ] **Step 3: Create the runner**

Create `src/aegis/tools/runner.py`:

```python
"""Tool invocation wrapper with timeout + JSONL logging."""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from pathlib import Path
from typing import Any

from aegis.tools.decorator import ToolEntry


class ToolTimeout(TimeoutError):
    """Raised when a tool exceeds its declared timeout."""


async def invoke_tool(
    entry: ToolEntry,
    *,
    kwargs: dict[str, Any],
    state_dir: Path,
) -> Any:
    """Invoke a tool, enforcing timeout and writing a JSONL log line.

    Sync functions are wrapped in a default executor so the timeout
    semantics still apply.
    """
    log_path = state_dir / "tools" / f"{entry.name}.jsonl"
    started = time.time()

    async def _call():
        if inspect.iscoroutinefunction(entry.func):
            return await entry.func(**kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: entry.func(**kwargs))

    try:
        result = await asyncio.wait_for(_call(), timeout=entry.timeout)
        _log(log_path, status="ok", entry=entry, started=started, kwargs=kwargs)
        return result
    except asyncio.TimeoutError:
        _log(log_path, status="timeout", entry=entry, started=started, kwargs=kwargs)
        raise ToolTimeout(f"tool {entry.name!r} exceeded {entry.timeout}s")
    except Exception as exc:
        _log(log_path, status="exception", entry=entry, started=started,
             kwargs=kwargs, error=f"{type(exc).__name__}: {exc}")
        raise


def _log(
    path: Path, *, status: str, entry: ToolEntry, started: float,
    kwargs: dict[str, Any], error: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": time.time(), "duration": time.time() - started,
        "tool": entry.name, "qualname": entry.qualname,
        "status": status, "kwargs": _safe_repr(kwargs),
    }
    if error is not None:
        rec["error"] = error
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def _safe_repr(kwargs: dict[str, Any]) -> dict[str, str]:
    return {k: repr(v)[:200] for k, v in kwargs.items()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tool_runner.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tools/runner.py tests/test_tool_runner.py
git commit -m "feat(tools): runner with timeout, JSONL logging, sync+async"
```

---

## Task 2.3: Register `@tool`s into the FastMCP server

**Files:**
- Modify: `src/aegis/mcp/server.py::build_server` (line ~378)
- Test: `tests/test_tool_mcp_register.py`

The FastMCP server is built once per process via `build_server(bridge)`. Goal: after the existing built-in tool registrations, walk `aegis.tools._REGISTRY` and register each tool with FastMCP.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tool_mcp_register.py`:

```python
"""Registered @tool functions appear in the FastMCP server's tool list."""
from __future__ import annotations

import pytest

from aegis.tools import tool
from aegis.tools.decorator import _reset_registry_for_tests


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _fake_bridge():
    class B:
        remotes = {}
        groups = None
    return B()


@pytest.mark.asyncio
async def test_user_tool_appears_in_server(monkeypatch) -> None:
    @tool
    async def load_skill(name: str) -> str:
        """Load a skill body."""
        return f"body of {name}"

    from aegis.mcp.server import build_server
    server = build_server(_fake_bridge())
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert "load_skill" in names


@pytest.mark.asyncio
async def test_user_tool_callable_through_server(monkeypatch) -> None:
    @tool
    async def echo(text: str) -> str:
        """Echo input."""
        return text + "!"

    from aegis.mcp.server import build_server
    server = build_server(_fake_bridge())
    result = await server.call_tool("echo", {"text": "hi"})
    assert "hi!" in str(result)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tool_mcp_register.py -v`
Expected: FAIL — `load_skill` not in server's tool list.

- [ ] **Step 3: Modify `build_server` to register user tools**

Edit `src/aegis/mcp/server.py`. Locate `def build_server(bridge: AppBridge) -> FastMCP:` (line ~378). After the existing tool registrations (after all `@server.tool` decorations inside the function body), append:

```python
    # Register user-declared @tool functions.
    from aegis.tools import _REGISTRY as _TOOL_REG
    from aegis.tools.runner import invoke_tool
    from pathlib import Path as _Path

    for _entry in _TOOL_REG.values():
        _register_user_tool(server, _entry)

    return server
```

And add the helper near the top of the module:

```python
def _register_user_tool(server: "FastMCP", entry) -> None:
    """Wrap a ToolEntry as a FastMCP tool with auto-derived schema."""
    from aegis.tools.runner import invoke_tool
    from pathlib import Path

    # FastMCP's @server.tool walks the function signature itself; we
    # pass the original function as the underlying impl, wrapped to
    # route through invoke_tool() for timeout + logging.
    async def _wrapper(**kwargs):
        state_dir = Path.cwd() / ".aegis" / "state"
        return await invoke_tool(entry, kwargs=kwargs, state_dir=state_dir)

    _wrapper.__name__ = entry.name
    _wrapper.__doc__ = entry.func.__doc__
    # Reuse the original signature so FastMCP derives the right schema.
    import inspect
    _wrapper.__signature__ = inspect.signature(entry.func)
    server.tool(_wrapper)
```

(The exact FastMCP API name may be `server.tool(...)` as decorator or `server.add_tool(...)` depending on FastMCP version. Check `from fastmcp import FastMCP; help(FastMCP.tool)` if the test fails on the registration API.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tool_mcp_register.py -v`
Expected: PASS.

- [ ] **Step 5: Run existing MCP suite to confirm no regression**

Run: `uv run pytest -q -m "not live" tests/test_mcp* tests/test_server* 2>/dev/null || uv run pytest -q -m "not live"`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_tool_mcp_register.py
git commit -m "feat(tools): register @tools into the FastMCP server at build time"
```

---

## Task 2.4: Cross-primitive smoke — one file with `@workflow + @hook + @tool`

**Files:**
- Test: `tests/test_cross_primitive.py`

Verifies the three substrate decorators coexist cleanly in a single plugin file with no registration interference, and all three become reachable through their respective entry points.

- [ ] **Step 1: Write the integration test**

Create `tests/test_cross_primitive.py`:

```python
"""A fixture plugin with @workflow + @hook + @tool in one file —
all three primitives must register and be reachable."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aegis.config.yaml_loader import AegisConfig, import_plugins
from aegis.hooks.decorator import _REGISTRY as _HOOK_REG, _reset_registry_for_tests as _reset_hooks
from aegis.tools.decorator import _REGISTRY as _TOOL_REG, _reset_registry_for_tests as _reset_tools
from aegis.workflow import REGISTRY as _WORKFLOW_REG


@pytest.fixture(autouse=True)
def _clean():
    _reset_hooks()
    _reset_tools()
    snapshot = dict(_WORKFLOW_REG)
    yield
    _reset_hooks()
    _reset_tools()
    _WORKFLOW_REG.clear()
    _WORKFLOW_REG.update(snapshot)


def test_one_file_registers_all_three(tmp_path: Path) -> None:
    plug = tmp_path / "plugins" / "kitchen-sink"
    plug.mkdir(parents=True)
    (plug / "plugin.toml").write_text(
        '[plugin]\nname = "kitchen-sink"\nversion = "0.0.1"\n'
    )
    (plug / "mod.py").write_text(textwrap.dedent("""
        from aegis.hooks import hook, PreTurnResult
        from aegis.tools import tool
        from aegis.workflow import workflow

        @hook("pre_turn")
        async def my_hook(ctx):
            return PreTurnResult(prepend_system="hi")

        @tool
        async def my_tool(x: int) -> int:
            \"\"\"Doubles x.\"\"\"
            return x * 2

        @workflow
        async def my_wf(engine):
            return "ok"
    """))

    import_plugins(AegisConfig(plugin_dirs=[plug.parent]))

    assert len(_HOOK_REG["pre_turn"]) == 1
    assert "my_tool" in _TOOL_REG
    assert "my_wf" in _WORKFLOW_REG
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/test_cross_primitive.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_cross_primitive.py
git commit -m "test(plugins): @workflow + @hook + @tool coexist in one file"
```

**Slice 2 complete.** Tool substrate live; a `@tool`-decorated function is now visible to spawned Claude sessions through the existing aegis MCP plane, and the three primitives coexist cleanly in a single plugin file.

---

# Slice 3 — Plugin manifest + local install lifecycle

Land the install/uninstall machinery against a local path source. Registry resolution is slice 4; this slice's install command takes `--from <local-folder>` to source the plugin.

## Task 3.1: Manifest parser

**Files:**
- Create: `src/aegis/plugins/__init__.py`, `src/aegis/plugins/manifest.py`
- Test: `tests/test_plugin_manifest.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_plugin_manifest.py`:

```python
"""plugin.toml parsing → PluginManifest."""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.plugins.manifest import ManifestError, PluginManifest, load_manifest


def _write(p: Path, body: str) -> Path:
    p.write_text(body)
    return p


def test_minimal(tmp_path: Path) -> None:
    f = _write(tmp_path / "plugin.toml", '[plugin]\nname = "x"\nversion = "0.1"\n')
    m = load_manifest(f)
    assert m.name == "x"
    assert m.version == "0.1"
    assert m.description == ""
    assert m.requires_aegis is None
    assert m.default_config == {}


def test_full(tmp_path: Path) -> None:
    body = """
[plugin]
name = "skill-system"
version = "0.1.0"
description = "Inject skills pre-turn."
requires_aegis = ">=0.15"

[default_config]
folder = ".aegis/skills/"
top_k = 3
"""
    f = _write(tmp_path / "plugin.toml", body)
    m = load_manifest(f)
    assert m.name == "skill-system"
    assert m.requires_aegis == ">=0.15"
    assert m.default_config["folder"] == ".aegis/skills/"
    assert m.default_config["top_k"] == 3


def test_missing_plugin_table(tmp_path: Path) -> None:
    f = _write(tmp_path / "plugin.toml", "[other]\nname = 'x'\n")
    with pytest.raises(ManifestError, match="\\[plugin\\]"):
        load_manifest(f)


def test_missing_required_field(tmp_path: Path) -> None:
    f = _write(tmp_path / "plugin.toml", '[plugin]\nname = "x"\n')
    with pytest.raises(ManifestError, match="version"):
        load_manifest(f)


def test_unknown_keys_preserved_in_raw(tmp_path: Path) -> None:
    body = """
[plugin]
name = "x"
version = "0.1"
future_field = "still here"
"""
    f = _write(tmp_path / "plugin.toml", body)
    m = load_manifest(f)
    assert m.raw["plugin"]["future_field"] == "still here"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_plugin_manifest.py -v`
Expected: ImportError on `aegis.plugins.manifest`.

- [ ] **Step 3: Create the manifest module**

Create `src/aegis/plugins/__init__.py`:

```python
"""Aegis plugin substrate — manifest, install, uninstall, lockfile."""
from aegis.plugins.install_context import InstallContext
from aegis.plugins.manifest import ManifestError, PluginManifest, load_manifest

__all__ = ["InstallContext", "ManifestError", "PluginManifest", "load_manifest"]
```

Create `src/aegis/plugins/manifest.py`:

```python
"""plugin.toml parsing."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ManifestError(ValueError):
    """plugin.toml malformed."""


@dataclass(frozen=True)
class PluginManifest:
    name:           str
    version:        str
    description:    str = ""
    requires_aegis: str | None = None
    default_config: dict[str, Any] = field(default_factory=dict)
    raw:            dict[str, Any] = field(default_factory=dict)


def load_manifest(path: Path) -> PluginManifest:
    """Parse plugin.toml; fail loud on missing required fields."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{path}: invalid TOML: {exc}") from exc
    if "plugin" not in raw or not isinstance(raw["plugin"], dict):
        raise ManifestError(f"{path}: missing [plugin] table")
    plug = raw["plugin"]
    for required in ("name", "version"):
        if required not in plug:
            raise ManifestError(f"{path}: [plugin].{required} required")
    return PluginManifest(
        name=plug["name"],
        version=plug["version"],
        description=plug.get("description", ""),
        requires_aegis=plug.get("requires_aegis"),
        default_config=raw.get("default_config", {}),
        raw=raw,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_plugin_manifest.py -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/plugins/ tests/test_plugin_manifest.py
git commit -m "feat(plugins): plugin.toml manifest parser"
```

---

## Task 3.2: InstallContext dataclass

**Files:**
- Create: `src/aegis/plugins/install_context.py`
- Test: `tests/test_install_context.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_install_context.py`:

```python
"""InstallContext field shape + helpers."""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.plugins.install_context import InstallContext


def _ctx(tmp_path: Path, **overrides) -> InstallContext:
    defaults = dict(
        project_root=tmp_path,
        aegis_dir=tmp_path / ".aegis",
        plugin_dir=tmp_path / ".aegis/plugins/foo",
        plugin_name="foo",
        manifest={"plugin": {"name": "foo", "version": "0.1"}},
        config=None,
        console=None,
        _confirm_default=True,
        _yes=False,
    )
    defaults.update(overrides)
    return InstallContext(**defaults)


def test_paths_exposed(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    assert ctx.project_root == tmp_path
    assert ctx.aegis_dir == tmp_path / ".aegis"
    assert ctx.plugin_dir == tmp_path / ".aegis/plugins/foo"
    assert ctx.plugin_name == "foo"


def test_confirm_default_when_yes_flag(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, _yes=True)
    assert ctx.confirm("anything?", default=True) is True
    assert ctx.confirm("anything?", default=False) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_install_context.py -v`
Expected: ImportError.

- [ ] **Step 3: Create the module**

Create `src/aegis/plugins/install_context.py`:

```python
"""InstallContext: handed to _install.py::install(ctx) and _uninstall.py."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class InstallContext:
    project_root: Path
    aegis_dir:    Path
    plugin_dir:   Path
    plugin_name:  str
    manifest:     dict[str, Any]
    config:       Any                  # AegisConfig; opaque here to avoid cycles
    console:      Any                  # rich.Console | None for headless tests
    _confirm_default: bool = True
    _yes:         bool = False

    def confirm(self, question: str, *, default: bool) -> bool:
        """Prompt the user. Returns `default` automatically in --yes mode."""
        if self._yes:
            return default
        if self.console is None:
            return default
        from rich.prompt import Confirm
        return Confirm.ask(question, default=default, console=self.console)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_install_context.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/plugins/install_context.py tests/test_install_context.py
git commit -m "feat(plugins): InstallContext dataclass for _install.py hooks"
```

---

## Task 3.3: Local-path install — copy + config merge + lockfile write

**Files:**
- Create: `src/aegis/plugins/install.py`, `src/aegis/plugins/lockfile.py`
- Test: `tests/test_plugin_install_local.py`

The `aegis plugin install --from <local-path> <name>` flow:
1. Verify `<local-path>/plugin.toml` exists; parse it.
2. Refuse if `<project>/.aegis/plugins/<name>/` exists (unless `--force`).
3. Copy `<local-path>/` → `<project>/.aegis/plugins/<name>/`.
4. Merge `[default_config]` into `.aegis.yaml`'s `plugins.<name>` namespace.
5. Run `_install.py::install(ctx)` if present.
6. Append lockfile entry.

- [ ] **Step 1: Write the failing test**

Create `tests/test_plugin_install_local.py`:

```python
"""install_plugin against a local-path source."""
from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from aegis.plugins.install import InstallError, install_plugin


def _make_source(src: Path, *, name: str, with_install: bool = False) -> Path:
    src.mkdir(parents=True)
    (src / "plugin.toml").write_text(textwrap.dedent(f"""
        [plugin]
        name = "{name}"
        version = "0.0.1"

        [default_config]
        folder = ".aegis/things/"
        k = 1
    """))
    (src / "code.py").write_text("# stub\n")
    if with_install:
        (src / "_install.py").write_text(textwrap.dedent("""
            from pathlib import Path
            def install(ctx):
                (ctx.aegis_dir / "things").mkdir(parents=True, exist_ok=True)
        """))
    return src


def _make_project(root: Path) -> Path:
    (root / ".aegis").mkdir()
    (root / ".aegis.yaml").write_text("agents: {}\n")
    return root


def test_copy_and_lockfile(tmp_path: Path) -> None:
    src = _make_source(tmp_path / "src" / "skill-system", name="skill-system")
    proj = _make_project(tmp_path / "proj")
    install_plugin(name="skill-system", source=src, project_root=proj, yes=True)
    installed = proj / ".aegis" / "plugins" / "skill-system"
    assert installed.is_dir()
    assert (installed / "plugin.toml").exists()
    assert (installed / "code.py").exists()
    lock = proj / ".aegis" / "plugins.lock"
    assert lock.exists()
    text = lock.read_text()
    assert "skill-system" in text
    assert "0.0.1" in text


def test_refuses_if_already_installed(tmp_path: Path) -> None:
    src = _make_source(tmp_path / "src" / "x", name="x")
    proj = _make_project(tmp_path / "proj")
    install_plugin(name="x", source=src, project_root=proj, yes=True)
    with pytest.raises(InstallError, match="already installed"):
        install_plugin(name="x", source=src, project_root=proj, yes=True)


def test_force_reinstalls(tmp_path: Path) -> None:
    src = _make_source(tmp_path / "src" / "x", name="x")
    proj = _make_project(tmp_path / "proj")
    install_plugin(name="x", source=src, project_root=proj, yes=True)
    install_plugin(name="x", source=src, project_root=proj, yes=True, force=True)


def test_install_py_runs(tmp_path: Path) -> None:
    src = _make_source(tmp_path / "src" / "x", name="x", with_install=True)
    proj = _make_project(tmp_path / "proj")
    install_plugin(name="x", source=src, project_root=proj, yes=True)
    assert (proj / ".aegis" / "things").is_dir()


def test_config_merged(tmp_path: Path) -> None:
    src = _make_source(tmp_path / "src" / "x", name="x")
    proj = _make_project(tmp_path / "proj")
    install_plugin(name="x", source=src, project_root=proj, yes=True)
    yaml_text = (proj / ".aegis.yaml").read_text()
    assert "plugins:" in yaml_text
    assert "x:" in yaml_text
    assert ".aegis/things/" in yaml_text


def test_rollback_on_install_py_failure(tmp_path: Path) -> None:
    src = tmp_path / "src" / "bad"
    src.mkdir(parents=True)
    (src / "plugin.toml").write_text(
        '[plugin]\nname = "bad"\nversion = "0.0.1"\n'
    )
    (src / "_install.py").write_text("def install(ctx):\n    raise RuntimeError('nope')\n")
    proj = _make_project(tmp_path / "proj")
    with pytest.raises(RuntimeError, match="nope"):
        install_plugin(name="bad", source=src, project_root=proj, yes=True)
    assert not (proj / ".aegis" / "plugins" / "bad").exists()
    lock = proj / ".aegis" / "plugins.lock"
    assert not lock.exists() or "bad" not in lock.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_plugin_install_local.py -v`
Expected: ImportError on `aegis.plugins.install`.

- [ ] **Step 3: Create the install module**

Create `src/aegis/plugins/lockfile.py`:

```python
"""Read/write .aegis/plugins.lock."""
from __future__ import annotations

import hashlib
import time
import tomllib
from pathlib import Path
from typing import Any

try:
    import tomli_w as _tomli_w
except ImportError:  # pragma: no cover — tomli_w is in pyproject
    _tomli_w = None


def lockfile_path(project_root: Path) -> Path:
    return project_root / ".aegis" / "plugins.lock"


def read_lock(project_root: Path) -> dict[str, Any]:
    p = lockfile_path(project_root)
    if not p.exists():
        return {"plugins": []}
    return tomllib.loads(p.read_text(encoding="utf-8"))


def write_lock(project_root: Path, data: dict[str, Any]) -> None:
    p = lockfile_path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    if _tomli_w is None:
        p.write_text(_naive_toml_dump(data), encoding="utf-8")
    else:
        p.write_bytes(_tomli_w.dumps(data).encode("utf-8"))


def upsert(project_root: Path, entry: dict[str, Any]) -> None:
    data = read_lock(project_root)
    plugins = [p for p in data.get("plugins", []) if p.get("name") != entry["name"]]
    plugins.append(entry)
    plugins.sort(key=lambda p: p["name"])
    data["plugins"] = plugins
    write_lock(project_root, data)


def remove(project_root: Path, name: str) -> None:
    data = read_lock(project_root)
    data["plugins"] = [p for p in data.get("plugins", []) if p.get("name") != name]
    write_lock(project_root, data)


def hash_dir(path: Path) -> dict[str, str]:
    """Return {relpath: sha256} for every file under path."""
    out = {}
    for p in path.rglob("*"):
        if p.is_file():
            rel = str(p.relative_to(path))
            out[rel] = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _naive_toml_dump(data: dict[str, Any]) -> str:
    """Last-resort TOML emitter for tests. tomli_w is the production path."""
    lines: list[str] = []
    for plug in data.get("plugins", []):
        lines.append("[[plugins]]")
        for k, v in plug.items():
            if isinstance(v, dict):
                lines.append(f"{k} = {v!r}")
            else:
                lines.append(f"{k} = {v!r}")
        lines.append("")
    return "\n".join(lines)
```

Create `src/aegis/plugins/install.py`:

```python
"""install_plugin — local-path source. Registry resolution lives in slice 4."""
from __future__ import annotations

import importlib.util
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from aegis.plugins import lockfile
from aegis.plugins.install_context import InstallContext
from aegis.plugins.manifest import load_manifest


class InstallError(RuntimeError):
    """Install failed in a way the caller should surface to the user."""


def install_plugin(
    *,
    name: str,
    source: Path,
    project_root: Path,
    yes: bool = False,
    force: bool = False,
    console: Any = None,
) -> None:
    """Install a plugin from a local-path source.

    Steps: parse manifest → copy → merge config → run _install.py → record lock.
    Rolls back the copy on _install.py failure; lockfile is only written on
    full success. Config merges are not rolled back (user-visible state).
    """
    src_manifest = source / "plugin.toml"
    if not src_manifest.exists():
        raise InstallError(f"no plugin.toml at {source}")
    manifest = load_manifest(src_manifest)
    if manifest.name != name:
        raise InstallError(
            f"manifest name {manifest.name!r} does not match requested {name!r}"
        )

    dest = project_root / ".aegis" / "plugins" / name
    if dest.exists() and not force:
        raise InstallError(f"plugin {name!r} already installed at {dest}")
    if dest.exists():
        shutil.rmtree(dest)

    # 1. Copy
    shutil.copytree(source, dest)

    # 2. Config merge
    try:
        _merge_default_config(project_root, name, manifest.default_config)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise

    # 3. _install.py
    install_py = dest / "_install.py"
    if install_py.exists():
        ctx = InstallContext(
            project_root=project_root,
            aegis_dir=project_root / ".aegis",
            plugin_dir=dest,
            plugin_name=name,
            manifest=manifest.raw,
            config=None,
            console=console,
            _yes=yes,
        )
        try:
            _invoke_install_py(install_py, ctx)
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise

    # 4. Lockfile
    lockfile.upsert(project_root, {
        "name":      name,
        "version":   manifest.version,
        "source":    str(source),
        "installed": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "file_hashes": lockfile.hash_dir(dest),
    })


def _invoke_install_py(path: Path, ctx: InstallContext) -> None:
    spec = importlib.util.spec_from_file_location(
        f"_aegis_install_{ctx.plugin_name}", path,
    )
    if spec is None or spec.loader is None:
        raise InstallError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    fn = getattr(module, "install", None)
    if fn is None:
        return  # no install fn, fine
    fn(ctx)


def _merge_default_config(
    project_root: Path, name: str, default_config: dict[str, Any],
) -> None:
    """Merge default_config into .aegis.yaml under plugins.<name>.

    Uses the existing aegis.config.edit helpers (ruamel-backed, comment-
    preserving).
    """
    if not default_config:
        return
    from aegis.config.edit import _load, _dump  # type: ignore[attr-defined]
    yaml_path = project_root / ".aegis.yaml"
    data = _load(yaml_path)
    plugins_section = data.setdefault("plugins", {})
    plugin_section = plugins_section.setdefault(name, {})
    for k, v in default_config.items():
        plugin_section.setdefault(k, v)  # never overwrite user edits
    _dump(yaml_path, data)
```

If `aegis.config.edit` doesn't expose `_load`/`_dump`, inline a minimal ruamel-backed read/modify/write directly:

```python
from ruamel.yaml import YAML
_yaml = YAML(typ="rt")  # round-trip preserves comments
data = _yaml.load(yaml_path) if yaml_path.exists() else {}
# ... modify ...
with yaml_path.open("w") as f:
    _yaml.dump(data, f)
```

Check shape with `grep -n "ruamel\|YAML(" src/aegis/config/edit.py` before deciding.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_plugin_install_local.py -v`
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/plugins/install.py src/aegis/plugins/lockfile.py tests/test_plugin_install_local.py
git commit -m "feat(plugins): local-path install with rollback + lockfile"
```

---

## Task 3.4: Uninstall — call `_uninstall.py`, delete folder, strip config

**Files:**
- Create: `src/aegis/plugins/uninstall.py`
- Test: `tests/test_plugin_uninstall.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_plugin_uninstall.py`:

```python
"""uninstall_plugin: run _uninstall.py, delete folder, strip config,
leave user data alone."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aegis.plugins.install import install_plugin
from aegis.plugins.uninstall import UninstallError, uninstall_plugin


def _setup_installed(tmp_path: Path, *, with_uninstall: bool = False) -> Path:
    src = tmp_path / "src" / "x"
    src.mkdir(parents=True)
    (src / "plugin.toml").write_text(textwrap.dedent("""
        [plugin]
        name = "x"
        version = "0.1"
        [default_config]
        k = 1
    """))
    (src / "code.py").write_text("# stub\n")
    if with_uninstall:
        (src / "_uninstall.py").write_text(textwrap.dedent("""
            def uninstall(ctx):
                (ctx.aegis_dir / "x-teardown").write_text("done")
        """))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".aegis").mkdir()
    (proj / ".aegis.yaml").write_text("plugins: {}\n")
    install_plugin(name="x", source=src, project_root=proj, yes=True)
    return proj


def test_deletes_folder(tmp_path: Path) -> None:
    proj = _setup_installed(tmp_path)
    uninstall_plugin(name="x", project_root=proj, yes=True)
    assert not (proj / ".aegis" / "plugins" / "x").exists()


def test_strips_config_section(tmp_path: Path) -> None:
    proj = _setup_installed(tmp_path)
    uninstall_plugin(name="x", project_root=proj, yes=True)
    yaml_text = (proj / ".aegis.yaml").read_text()
    assert "x:" not in yaml_text or "plugins" not in yaml_text


def test_uninstall_py_runs(tmp_path: Path) -> None:
    proj = _setup_installed(tmp_path, with_uninstall=True)
    uninstall_plugin(name="x", project_root=proj, yes=True)
    assert (proj / ".aegis" / "x-teardown").exists()


def test_leaves_user_data_alone(tmp_path: Path) -> None:
    proj = _setup_installed(tmp_path)
    user_data = proj / ".aegis" / "user-thing"
    user_data.mkdir()
    (user_data / "important.txt").write_text("keep me")
    uninstall_plugin(name="x", project_root=proj, yes=True)
    assert (user_data / "important.txt").read_text() == "keep me"


def test_lockfile_entry_removed(tmp_path: Path) -> None:
    proj = _setup_installed(tmp_path)
    uninstall_plugin(name="x", project_root=proj, yes=True)
    lock = (proj / ".aegis" / "plugins.lock").read_text() \
        if (proj / ".aegis" / "plugins.lock").exists() else ""
    assert "name = \"x\"" not in lock and "name = 'x'" not in lock


def test_uninstall_py_exception_logged_and_continued(tmp_path: Path) -> None:
    src = tmp_path / "src" / "y"
    src.mkdir(parents=True)
    (src / "plugin.toml").write_text('[plugin]\nname = "y"\nversion = "0.1"\n')
    (src / "_uninstall.py").write_text(
        "def uninstall(ctx):\n    raise RuntimeError('boom')\n"
    )
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / ".aegis").mkdir(); (proj / ".aegis.yaml").write_text("plugins: {}\n")
    install_plugin(name="y", source=src, project_root=proj, yes=True)
    # Should not raise — uninstall log-and-continues.
    uninstall_plugin(name="y", project_root=proj, yes=True)
    assert not (proj / ".aegis" / "plugins" / "y").exists()


def test_uninstalling_unknown_plugin_errors(tmp_path: Path) -> None:
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / ".aegis").mkdir(); (proj / ".aegis.yaml").write_text("plugins: {}\n")
    with pytest.raises(UninstallError, match="not installed"):
        uninstall_plugin(name="never", project_root=proj, yes=True)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_plugin_uninstall.py -v`
Expected: ImportError on `aegis.plugins.uninstall`.

- [ ] **Step 3: Create the uninstall module**

Create `src/aegis/plugins/uninstall.py`:

```python
"""uninstall_plugin — run _uninstall.py, delete folder, strip config."""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from typing import Any

from aegis.plugins import lockfile
from aegis.plugins.install_context import InstallContext


class UninstallError(RuntimeError):
    """Uninstall failed in a way the caller should surface."""


def uninstall_plugin(
    *,
    name: str,
    project_root: Path,
    yes: bool = False,
    console: Any = None,
) -> None:
    """Reverse the install:

    1. Run `_uninstall.py::uninstall(ctx)` if present. Exceptions logged + continue.
    2. Delete `.aegis/plugins/<name>/`.
    3. Strip `plugins.<name>` from `.aegis.yaml` keys that were declared in
       the manifest's [default_config] (user hand-edits preserved).
    4. Remove the lockfile entry.
    """
    plugin_dir = project_root / ".aegis" / "plugins" / name
    if not plugin_dir.exists():
        raise UninstallError(f"plugin {name!r} not installed")

    # 1. Read manifest (still inside plugin_dir before we delete) so we know
    # which config keys we wrote at install time.
    from aegis.plugins.manifest import load_manifest
    manifest_path = plugin_dir / "plugin.toml"
    default_config: dict[str, Any] = {}
    if manifest_path.exists():
        manifest = load_manifest(manifest_path)
        default_config = manifest.default_config

    # 2. _uninstall.py
    uninstall_py = plugin_dir / "_uninstall.py"
    if uninstall_py.exists():
        ctx = InstallContext(
            project_root=project_root,
            aegis_dir=project_root / ".aegis",
            plugin_dir=plugin_dir,
            plugin_name=name,
            manifest=manifest.raw if manifest_path.exists() else {},
            config=None,
            console=console,
            _yes=yes,
        )
        try:
            _invoke_uninstall(uninstall_py, ctx)
        except Exception as exc:  # noqa: BLE001 — log + continue
            import logging
            logging.getLogger("aegis.plugins").exception(
                "uninstall hook for %s raised: %s", name, exc,
            )

    # 3. Delete folder
    shutil.rmtree(plugin_dir)

    # 4. Strip config keys
    _strip_config(project_root, name, default_config)

    # 5. Remove lockfile entry
    lockfile.remove(project_root, name)


def _invoke_uninstall(path: Path, ctx: InstallContext) -> None:
    spec = importlib.util.spec_from_file_location(
        f"_aegis_uninstall_{ctx.plugin_name}", path,
    )
    if spec is None or spec.loader is None:
        raise UninstallError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    fn = getattr(module, "uninstall", None)
    if fn is not None:
        fn(ctx)


def _strip_config(
    project_root: Path, name: str, default_config: dict[str, Any],
) -> None:
    yaml_path = project_root / ".aegis.yaml"
    if not yaml_path.exists():
        return
    from ruamel.yaml import YAML
    yaml = YAML(typ="rt")
    data = yaml.load(yaml_path)
    if not isinstance(data, dict):
        return
    plugins = data.get("plugins") or {}
    plugin_section = plugins.get(name) or {}
    for k in default_config:
        plugin_section.pop(k, None)
    if not plugin_section:
        plugins.pop(name, None)
    if not plugins:
        data.pop("plugins", None)
    with yaml_path.open("w") as f:
        yaml.dump(data, f)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_plugin_uninstall.py -v`
Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/plugins/uninstall.py tests/test_plugin_uninstall.py
git commit -m "feat(plugins): uninstall with _uninstall.py + config strip"
```

---

## Task 3.5: `aegis plugin` CLI subapp (install/uninstall/list/show)

**Files:**
- Create: `src/aegis/cli_plugin.py`
- Modify: `src/aegis/cli.py` to mount the subapp
- Test: `tests/test_plugin_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_plugin_cli.py`:

```python
"""`aegis plugin` CLI surface — install/uninstall/list/show against local source."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aegis.cli import app

runner = CliRunner()


def _make_src(tmp_path: Path) -> Path:
    src = tmp_path / "skill-system"
    src.mkdir()
    (src / "plugin.toml").write_text(
        '[plugin]\nname = "skill-system"\nversion = "0.1.0"\n'
        'description = "test"\n'
    )
    (src / "code.py").write_text("# stub\n")
    return src


def test_install_then_list(tmp_path: Path, monkeypatch) -> None:
    src = _make_src(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".aegis").mkdir()
    (proj / ".aegis.yaml").write_text("agents: {}\n")
    monkeypatch.chdir(proj)
    r = runner.invoke(app, [
        "plugin", "install", "skill-system", "--from", str(src), "--yes",
    ])
    assert r.exit_code == 0, r.output
    assert "skill-system" in r.output

    r = runner.invoke(app, ["plugin", "list"])
    assert r.exit_code == 0
    assert "skill-system" in r.output
    assert "0.1.0" in r.output


def test_show(tmp_path: Path, monkeypatch) -> None:
    src = _make_src(tmp_path)
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / ".aegis").mkdir(); (proj / ".aegis.yaml").write_text("agents: {}\n")
    monkeypatch.chdir(proj)
    runner.invoke(app, ["plugin", "install", "skill-system", "--from", str(src), "--yes"])
    r = runner.invoke(app, ["plugin", "show", "skill-system"])
    assert r.exit_code == 0
    assert "skill-system" in r.output
    assert "0.1.0" in r.output


def test_uninstall(tmp_path: Path, monkeypatch) -> None:
    src = _make_src(tmp_path)
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / ".aegis").mkdir(); (proj / ".aegis.yaml").write_text("agents: {}\n")
    monkeypatch.chdir(proj)
    runner.invoke(app, ["plugin", "install", "skill-system", "--from", str(src), "--yes"])
    r = runner.invoke(app, ["plugin", "uninstall", "skill-system", "--yes"])
    assert r.exit_code == 0
    r = runner.invoke(app, ["plugin", "list"])
    assert "skill-system" not in r.output
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_plugin_cli.py -v`
Expected: FAIL — `plugin` subcommand not registered on `app`.

- [ ] **Step 3: Create the subapp**

Create `src/aegis/cli_plugin.py`:

```python
"""`aegis plugin ...` Typer subapp."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from aegis.plugins import lockfile
from aegis.plugins.install import InstallError, install_plugin
from aegis.plugins.uninstall import UninstallError, uninstall_plugin

app = typer.Typer(name="plugin", help="Manage aegis plugins.")
console = Console()


@app.command("install")
def cmd_install(
    name: str,
    from_: Path | None = typer.Option(
        None, "--from",
        help="Install from a local path instead of a registry (slice 4 unblocks registries).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't prompt; accept defaults."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing installation."),
) -> None:
    """Install a plugin."""
    if from_ is None:
        console.print(
            "[yellow]Registry resolution lands in slice 4. "
            "Pass --from <local-path> for now.[/]"
        )
        raise typer.Exit(2)
    try:
        install_plugin(
            name=name, source=from_, project_root=Path.cwd(),
            yes=yes, force=force, console=console,
        )
    except InstallError as exc:
        console.print(f"[red]install failed:[/] {exc}")
        raise typer.Exit(1)
    console.print(f"[green]installed[/] {name}")


@app.command("uninstall")
def cmd_uninstall(
    name: str,
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Uninstall a plugin."""
    try:
        uninstall_plugin(name=name, project_root=Path.cwd(), yes=yes, console=console)
    except UninstallError as exc:
        console.print(f"[red]uninstall failed:[/] {exc}")
        raise typer.Exit(1)
    console.print(f"[green]uninstalled[/] {name}")


@app.command("list")
def cmd_list() -> None:
    """List installed plugins."""
    data = lockfile.read_lock(Path.cwd())
    plugs = data.get("plugins") or []
    if not plugs:
        console.print("[dim]no plugins installed[/]")
        return
    table = Table(title="Installed plugins")
    table.add_column("Name"); table.add_column("Version"); table.add_column("Installed")
    for p in plugs:
        table.add_row(p.get("name", ""), p.get("version", ""), p.get("installed", ""))
    console.print(table)


@app.command("show")
def cmd_show(name: str) -> None:
    """Show details of an installed plugin."""
    data = lockfile.read_lock(Path.cwd())
    for p in data.get("plugins", []):
        if p.get("name") == name:
            for k, v in p.items():
                if k == "file_hashes":
                    console.print(f"file_hashes: <{len(v)} files>")
                else:
                    console.print(f"{k}: {v}")
            return
    console.print(f"[red]not installed:[/] {name}")
    raise typer.Exit(1)
```

Modify `src/aegis/cli.py`. Find the `app = typer.Typer(...)` declaration and add (after other subapp `app.add_typer(...)` lines):

```python
from aegis.cli_plugin import app as _plugin_app
app.add_typer(_plugin_app, name="plugin")
```

If `cli.py` doesn't have an `app` Typer object at module level, grep for `typer.Typer` in the file to find the right object. Wire the import + add_typer at module top after existing subapps.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_plugin_cli.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Run full suite**

Run: `uv run pytest -q -m "not live"`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/cli_plugin.py src/aegis/cli.py tests/test_plugin_cli.py
git commit -m "feat(cli): aegis plugin install/uninstall/list/show subapp"
```

**Slice 3 complete.** Local-path plugin install/uninstall/list/show work end-to-end through the CLI.

---

# Slice 4 — Registry resolution

`aegis plugin install <name>` without `--from` now resolves the name against configured `plugin_registries` (default `gh:apiad/aegis#plugins/`), fetches the folder via `git archive`, and feeds the temp path into the existing local-path install.

## Task 4.1: Registry URL parser

**Files:**
- Modify: `src/aegis/plugins/registry.py` (new file)
- Test: `tests/test_plugin_registry_url.py`

Supported URL shapes:
- `gh:<owner>/<repo>` — root of repo. Path defaults to `plugins/`.
- `gh:<owner>/<repo>#<path>` — explicit subpath.
- `gh:<owner>/<repo>@<ref>` — pinned ref (branch/tag/sha). Default `main`.
- `gh:<owner>/<repo>@<ref>#<path>` — combined.
- `file:///abs/path/to/registry` — local registry folder.

- [ ] **Step 1: Write the failing test**

Create `tests/test_plugin_registry_url.py`:

```python
"""Registry URL parsing."""
from __future__ import annotations

import pytest

from aegis.plugins.registry import RegistryURL, parse_registry_url


def test_minimal_gh() -> None:
    u = parse_registry_url("gh:apiad/aegis")
    assert u.scheme == "gh"
    assert u.owner == "apiad"
    assert u.repo == "aegis"
    assert u.ref == "main"
    assert u.path == "plugins/"


def test_gh_with_path() -> None:
    u = parse_registry_url("gh:apiad/aegis#plugins/")
    assert u.path == "plugins/"


def test_gh_with_ref() -> None:
    u = parse_registry_url("gh:apiad/aegis@v0.1.0")
    assert u.ref == "v0.1.0"
    assert u.path == "plugins/"


def test_gh_full() -> None:
    u = parse_registry_url("gh:apiad/aegis@v0.1.0#custom/path")
    assert u.ref == "v0.1.0"
    assert u.path == "custom/path"


def test_file_url() -> None:
    u = parse_registry_url("file:///home/me/plugins")
    assert u.scheme == "file"
    assert u.path == "/home/me/plugins"


def test_bad_url_fails_loud() -> None:
    with pytest.raises(ValueError, match="unsupported registry URL"):
        parse_registry_url("https://example.com/x")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_plugin_registry_url.py -v`
Expected: ImportError.

- [ ] **Step 3: Create the registry module**

Create `src/aegis/plugins/registry.py`:

```python
"""Plugin registry URL parsing + resolution + fetch."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RegistryURL:
    scheme: str               # "gh" or "file"
    owner:  str = ""          # only for gh:
    repo:   str = ""          # only for gh:
    ref:    str = "main"      # only for gh:
    path:   str = ""          # subpath (gh:) or filesystem path (file:)


_GH_RE = re.compile(
    r"^gh:(?P<owner>[^/@#]+)/(?P<repo>[^@#]+)"
    r"(@(?P<ref>[^#]+))?(#(?P<path>.*))?$"
)


def parse_registry_url(url: str) -> RegistryURL:
    if url.startswith("gh:"):
        m = _GH_RE.match(url)
        if not m:
            raise ValueError(f"malformed gh URL: {url}")
        return RegistryURL(
            scheme="gh",
            owner=m["owner"],
            repo=m["repo"],
            ref=m["ref"] or "main",
            path=(m["path"] or "plugins/").rstrip("/") + "/",
        )
    if url.startswith("file://"):
        return RegistryURL(scheme="file", path=url[len("file://"):])
    raise ValueError(
        f"unsupported registry URL {url!r}: "
        "must start with gh: or file://"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_plugin_registry_url.py -v`
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/plugins/registry.py tests/test_plugin_registry_url.py
git commit -m "feat(plugins): registry URL parser (gh:, file://)"
```

---

## Task 4.2: Fetch a plugin folder from a registry URL

**Files:**
- Modify: `src/aegis/plugins/registry.py`
- Test: `tests/test_plugin_registry_fetch.py`

Fetch strategy:
- `file://` → straight filesystem copy.
- `gh:` → use `git archive --remote=https://github.com/<owner>/<repo>.git <ref> <path>/<plugin-name>/` and pipe through `tar`, into a temp dir. `git archive` over HTTPS works for public GitHub repos via the `git` CLI.

Pre-flight: confirm `git` is available with `shutil.which("git")`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_plugin_registry_fetch.py`:

```python
"""Fetch plugin folder from a registry URL."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aegis.plugins.registry import (
    RegistryURL, fetch_plugin, parse_registry_url,
)


def test_file_registry_fetch(tmp_path: Path) -> None:
    reg = tmp_path / "registry"
    plug = reg / "skill-system"
    plug.mkdir(parents=True)
    (plug / "plugin.toml").write_text('[plugin]\nname = "skill-system"\nversion = "0.1"\n')
    (plug / "code.py").write_text("# stub\n")
    url = parse_registry_url(f"file://{reg}")

    with fetch_plugin(url, plugin_name="skill-system") as fetched:
        assert (fetched / "plugin.toml").exists()
        assert (fetched / "code.py").exists()


def test_file_registry_missing_plugin(tmp_path: Path) -> None:
    reg = tmp_path / "registry"
    reg.mkdir()
    url = parse_registry_url(f"file://{reg}")
    with pytest.raises(FileNotFoundError, match="never"):
        with fetch_plugin(url, plugin_name="never"):
            pass


@pytest.mark.live
def test_gh_fetch_via_git_archive(tmp_path: Path) -> None:
    """Live: requires `git` on PATH and HTTPS access to github.com."""
    import shutil
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    url = parse_registry_url("gh:apiad/aegis#plugins/")
    try:
        with fetch_plugin(url, plugin_name="skill-system") as fetched:
            assert (fetched / "plugin.toml").exists()
    except RuntimeError as exc:
        if "not found" in str(exc) or "exit" in str(exc):
            pytest.skip(f"skill-system not yet pushed to apiad/aegis: {exc}")
        raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_plugin_registry_fetch.py -v -m "not live"`
Expected: FAIL — `fetch_plugin` undefined.

- [ ] **Step 3: Add `fetch_plugin` to the registry module**

Append to `src/aegis/plugins/registry.py`:

```python
import contextlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from collections.abc import Iterator


@contextlib.contextmanager
def fetch_plugin(url: RegistryURL, *, plugin_name: str) -> Iterator[Path]:
    """Yield a path to a temporary copy of `plugin_name`'s folder from `url`.

    Cleans up the temp dir on context exit.
    """
    tmp = Path(tempfile.mkdtemp(prefix="aegis-plugin-"))
    try:
        if url.scheme == "file":
            src = Path(url.path) / plugin_name
            if not src.exists():
                raise FileNotFoundError(
                    f"plugin {plugin_name!r} not found at {Path(url.path)}"
                )
            dest = tmp / plugin_name
            shutil.copytree(src, dest)
            yield dest
        elif url.scheme == "gh":
            dest = _fetch_gh(url, plugin_name=plugin_name, into=tmp)
            yield dest
        else:
            raise ValueError(f"unsupported scheme {url.scheme!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fetch_gh(url: RegistryURL, *, plugin_name: str, into: Path) -> Path:
    """git archive --remote=<repo> <ref> <path>/<plugin>/ | tar -x -C <tmp>."""
    if shutil.which("git") is None:
        raise RuntimeError(
            "git not available on PATH; needed for gh: registry fetch"
        )
    repo_url = f"https://github.com/{url.owner}/{url.repo}.git"
    subpath = f"{url.path.rstrip('/')}/{plugin_name}"
    archive_cmd = ["git", "archive", f"--remote={repo_url}",
                   url.ref, subpath]
    archive = subprocess.run(archive_cmd, capture_output=True, check=False)
    if archive.returncode != 0 or not archive.stdout:
        # Fallback: shallow clone (works for repos that don't allow
        # remote archives, which is the GitHub default for HTTPS).
        return _fetch_gh_clone(url, plugin_name=plugin_name, into=into)
    extract = subprocess.run(
        ["tar", "-x", "-C", str(into)], input=archive.stdout, check=True,
    )
    final = into / subpath
    if not final.exists():
        raise RuntimeError(
            f"git archive succeeded but {subpath} not in archive"
        )
    return final


def _fetch_gh_clone(url: RegistryURL, *, plugin_name: str, into: Path) -> Path:
    """Fallback: shallow clone the repo, then read the plugin folder out."""
    clone_dir = into / "_clone"
    subprocess.run(
        ["git", "clone", "--depth=1", f"--branch={url.ref}",
         f"https://github.com/{url.owner}/{url.repo}.git", str(clone_dir)],
        check=True, capture_output=True,
    )
    src = clone_dir / url.path.rstrip("/") / plugin_name
    if not src.exists():
        raise RuntimeError(
            f"plugin {plugin_name!r} not found in {url.owner}/{url.repo}"
            f" at {url.path}"
        )
    dest = into / plugin_name
    shutil.copytree(src, dest)
    return dest
```

- [ ] **Step 4: Run test to verify hermetic tests pass**

Run: `uv run pytest tests/test_plugin_registry_fetch.py -v -m "not live"`
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/plugins/registry.py tests/test_plugin_registry_fetch.py
git commit -m "feat(plugins): fetch plugin from gh: / file:// registries"
```

---

## Task 4.3: Wire registry resolution into `aegis plugin install`

**Files:**
- Modify: `src/aegis/plugins/install.py` — add `resolve_and_install`
- Modify: `src/aegis/cli_plugin.py::cmd_install` — call the resolver when `--from` not passed
- Test: extend `tests/test_plugin_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_plugin_cli.py`:

```python
def test_install_resolves_against_file_registry(tmp_path: Path, monkeypatch) -> None:
    reg = tmp_path / "registry"
    plug = reg / "skill-system"
    plug.mkdir(parents=True)
    (plug / "plugin.toml").write_text(
        '[plugin]\nname = "skill-system"\nversion = "0.1.0"\n'
    )
    (plug / "code.py").write_text("# stub\n")

    proj = tmp_path / "proj"; proj.mkdir()
    (proj / ".aegis").mkdir()
    (proj / ".aegis.yaml").write_text(textwrap.dedent(f"""
        plugin_registries:
          - file://{reg}
        agents: {{}}
    """))
    monkeypatch.chdir(proj)

    r = runner.invoke(app, ["plugin", "install", "skill-system", "--yes"])
    assert r.exit_code == 0, r.output
    assert (proj / ".aegis" / "plugins" / "skill-system" / "plugin.toml").exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_plugin_cli.py::test_install_resolves_against_file_registry -v`
Expected: FAIL — registry resolution path not wired.

- [ ] **Step 3: Add `resolve_and_install`**

Append to `src/aegis/plugins/install.py`:

```python
from aegis.plugins.registry import fetch_plugin, parse_registry_url


DEFAULT_REGISTRY = "gh:apiad/aegis#plugins/"


def resolve_and_install(
    *,
    name: str,
    project_root: Path,
    yes: bool = False,
    force: bool = False,
    console: Any = None,
) -> None:
    """Walk configured registries; install from the first hit."""
    registries = _load_registries(project_root)
    if not registries:
        registries = [DEFAULT_REGISTRY]
    errors: list[str] = []
    for url_str in registries:
        url = parse_registry_url(url_str)
        try:
            with fetch_plugin(url, plugin_name=name) as fetched:
                install_plugin(
                    name=name, source=fetched, project_root=project_root,
                    yes=yes, force=force, console=console,
                )
                return
        except FileNotFoundError as exc:
            errors.append(f"{url_str}: {exc}")
            continue
        except Exception as exc:
            errors.append(f"{url_str}: {type(exc).__name__}: {exc}")
            continue
    raise InstallError(
        f"could not resolve {name!r} in any registry:\n  "
        + "\n  ".join(errors)
    )


def _load_registries(project_root: Path) -> list[str]:
    yaml_path = project_root / ".aegis.yaml"
    if not yaml_path.exists():
        return []
    from ruamel.yaml import YAML
    yaml = YAML(typ="safe")
    data = yaml.load(yaml_path) or {}
    return list(data.get("plugin_registries") or [])
```

Modify `cmd_install` in `src/aegis/cli_plugin.py`:

```python
@app.command("install")
def cmd_install(
    name: str,
    from_: Path | None = typer.Option(
        None, "--from",
        help="Install from a local path instead of the registry.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Install a plugin."""
    from aegis.plugins.install import resolve_and_install
    try:
        if from_ is not None:
            install_plugin(
                name=name, source=from_, project_root=Path.cwd(),
                yes=yes, force=force, console=console,
            )
        else:
            resolve_and_install(
                name=name, project_root=Path.cwd(),
                yes=yes, force=force, console=console,
            )
    except InstallError as exc:
        console.print(f"[red]install failed:[/] {exc}")
        raise typer.Exit(1)
    console.print(f"[green]installed[/] {name}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_plugin_cli.py -v`
Expected: 4 tests PASS (old 3 + new one).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/plugins/install.py src/aegis/cli_plugin.py tests/test_plugin_cli.py
git commit -m "feat(plugins): registry resolution wired into install command"
```

---

## Task 4.4: `aegis plugin update` + `aegis plugin search`

**Files:**
- Modify: `src/aegis/cli_plugin.py`
- Modify: `src/aegis/plugins/install.py` — add an edit-detection helper
- Test: `tests/test_plugin_update_search.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_plugin_update_search.py`:

```python
"""`aegis plugin update` + `aegis plugin search`."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aegis.cli import app

runner = CliRunner()


def _setup_registry_with_plugin(reg: Path, *, version: str) -> None:
    plug = reg / "skill-system"
    if plug.exists():
        import shutil; shutil.rmtree(plug)
    plug.mkdir(parents=True)
    (plug / "plugin.toml").write_text(textwrap.dedent(f"""
        [plugin]
        name = "skill-system"
        version = "{version}"
        description = "A test plugin."
    """))
    (plug / "code.py").write_text(f"# version {version}\n")


def _setup_project(proj: Path, reg: Path) -> None:
    proj.mkdir()
    (proj / ".aegis").mkdir()
    (proj / ".aegis.yaml").write_text(textwrap.dedent(f"""
        plugin_registries:
          - file://{reg}
        agents: {{}}
    """))


def test_update_picks_up_new_version(tmp_path: Path, monkeypatch) -> None:
    reg = tmp_path / "registry"
    _setup_registry_with_plugin(reg, version="0.1.0")
    proj = tmp_path / "proj"
    _setup_project(proj, reg)
    monkeypatch.chdir(proj)

    runner.invoke(app, ["plugin", "install", "skill-system", "--yes"])
    _setup_registry_with_plugin(reg, version="0.2.0")
    r = runner.invoke(app, ["plugin", "update", "skill-system", "--yes"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["plugin", "list"])
    assert "0.2.0" in r.output


def test_update_refuses_on_local_edit(tmp_path: Path, monkeypatch) -> None:
    reg = tmp_path / "registry"
    _setup_registry_with_plugin(reg, version="0.1.0")
    proj = tmp_path / "proj"
    _setup_project(proj, reg)
    monkeypatch.chdir(proj)
    runner.invoke(app, ["plugin", "install", "skill-system", "--yes"])
    # Locally edit the installed code.py
    edited = proj / ".aegis/plugins/skill-system/code.py"
    edited.write_text("# locally edited\n")
    _setup_registry_with_plugin(reg, version="0.2.0")
    r = runner.invoke(app, ["plugin", "update", "skill-system", "--yes"])
    assert r.exit_code != 0
    assert "edited" in r.output.lower() or "diverged" in r.output.lower()


def test_search(tmp_path: Path, monkeypatch) -> None:
    reg = tmp_path / "registry"
    _setup_registry_with_plugin(reg, version="0.1.0")
    proj = tmp_path / "proj"
    _setup_project(proj, reg)
    monkeypatch.chdir(proj)
    r = runner.invoke(app, ["plugin", "search", "skill"])
    assert r.exit_code == 0
    assert "skill-system" in r.output
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_plugin_update_search.py -v`
Expected: FAIL — `update` / `search` not registered.

- [ ] **Step 3: Add `update_plugin` + `search_plugins`**

Append to `src/aegis/plugins/install.py`:

```python
def update_plugin(
    *, name: str, project_root: Path,
    yes: bool = False, force: bool = False, console: Any = None,
) -> None:
    """Re-fetch and replace. Refuses on local edits unless force=True."""
    installed_dir = project_root / ".aegis" / "plugins" / name
    if not installed_dir.exists():
        raise InstallError(f"{name!r} is not installed")
    if not force:
        # Detect local edits by hashing the installed dir + diffing the
        # lockfile's recorded file_hashes.
        from aegis.plugins import lockfile
        data = lockfile.read_lock(project_root)
        entry = next(
            (p for p in data.get("plugins", []) if p.get("name") == name),
            None,
        )
        if entry is not None:
            recorded = entry.get("file_hashes") or {}
            current = lockfile.hash_dir(installed_dir)
            edited = [
                k for k in current
                if recorded.get(k) and recorded[k] != current[k]
            ]
            if edited:
                raise InstallError(
                    f"local edits detected in: {', '.join(sorted(edited))} "
                    "(use --force to clobber)"
                )
    resolve_and_install(
        name=name, project_root=project_root,
        yes=yes, force=True, console=console,
    )


def search_plugins(*, query: str, project_root: Path) -> list[dict]:
    """Walk every configured registry; collect (name, description) hits."""
    registries = _load_registries(project_root) or [DEFAULT_REGISTRY]
    hits: list[dict] = []
    for url_str in registries:
        url = parse_registry_url(url_str)
        for name, manifest in _list_plugins_in_registry(url):
            if (
                query.lower() in name.lower()
                or query.lower() in (manifest.get("description") or "").lower()
            ):
                hits.append({
                    "name": name,
                    "version": manifest.get("version", ""),
                    "description": manifest.get("description", ""),
                    "registry": url_str,
                })
    return hits


def _list_plugins_in_registry(url) -> list[tuple[str, dict]]:
    """Enumerate every plugin folder in a registry. Returns (name, manifest)."""
    if url.scheme == "file":
        root = Path(url.path)
        if not root.exists():
            return []
        out = []
        for sub in sorted(root.iterdir()):
            if not sub.is_dir():
                continue
            manifest_path = sub / "plugin.toml"
            if not manifest_path.exists():
                continue
            try:
                manifest = load_manifest(manifest_path)
            except Exception:
                continue
            out.append((manifest.name, manifest.raw.get("plugin", {})))
        return out
    if url.scheme == "gh":
        # Listing gh: registry requires cloning to inspect; defer to a
        # follow-up. For v1, search treats gh: registries as opaque.
        return []
    return []
```

Also reference the missing `from aegis.plugins.manifest import load_manifest` import at the top of `install.py` if not already present.

Append to `src/aegis/cli_plugin.py`:

```python
@app.command("update")
def cmd_update(
    name: str | None = typer.Argument(None),
    yes: bool = typer.Option(False, "--yes", "-y"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Update an installed plugin (re-fetch + replace)."""
    from aegis.plugins.install import update_plugin
    from aegis.plugins import lockfile

    targets: list[str]
    if name is not None:
        targets = [name]
    else:
        targets = [p["name"] for p in (lockfile.read_lock(Path.cwd()).get("plugins") or [])]
    if not targets:
        console.print("[dim]no plugins installed[/]"); return

    for t in targets:
        try:
            update_plugin(
                name=t, project_root=Path.cwd(),
                yes=yes, force=force, console=console,
            )
        except InstallError as exc:
            console.print(f"[red]{t} failed:[/] {exc}")
            raise typer.Exit(1)
        console.print(f"[green]updated[/] {t}")


@app.command("search")
def cmd_search(query: str) -> None:
    """Search registries for plugins matching `query`."""
    from aegis.plugins.install import search_plugins
    hits = search_plugins(query=query, project_root=Path.cwd())
    if not hits:
        console.print(f"[dim]no plugins match {query!r}[/]"); return
    for h in hits:
        console.print(
            f"[bold]{h['name']}[/] {h['version']}  "
            f"[dim]from {h['registry']}[/]"
        )
        if h.get("description"):
            console.print(f"  {h['description']}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_plugin_update_search.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/plugins/install.py src/aegis/cli_plugin.py tests/test_plugin_update_search.py
git commit -m "feat(plugins): aegis plugin update + search"
```

**Slice 4 complete.** Registry resolution works for `gh:` and `file://` schemes; install/update/search flow against configured registries with `gh:apiad/aegis#plugins/` as the default.

---

# Slice 5 — Canonical `skill-system` plugin

Author the canonical plugin at `plugins/skill-system/` in the aegis repo root so it lives at the registry default location (`gh:apiad/aegis#plugins/`).

## Task 5.1: Create the plugin folder and manifest

**Files:**
- Create: `plugins/skill-system/plugin.toml`

- [ ] **Step 1: Create the manifest**

Create `plugins/skill-system/plugin.toml`:

```toml
[plugin]
name           = "skill-system"
version        = "0.1.0"
description    = "Inject relevant skill descriptions pre-turn; agent calls load_skill on demand."
requires_aegis = ">=0.15"

[default_config]
folder = ".aegis/skills/"
top_k  = 3
```

- [ ] **Step 2: Commit**

```bash
git add plugins/skill-system/plugin.toml
git commit -m "feat(skill-system): manifest"
```

---

## Task 5.2: Implement `skill_system.py` — pre_turn hook + load_skill tool

**Files:**
- Create: `plugins/skill-system/skill_system.py`
- Test: `tests/test_skill_system.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_skill_system.py`:

```python
"""skill-system plugin: pre_turn injects menu; load_skill returns body."""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

from aegis.hooks.contexts import PreTurnContext, SessionHandle
from aegis.hooks.decorator import _REGISTRY as _HOOK_REG, _reset_registry_for_tests
from aegis.tools.decorator import _REGISTRY as _TOOL_REG, _reset_registry_for_tests as _reset_tools


def _load_skill_system():
    """Manually import the plugin module so its decorators fire."""
    repo_root = Path(__file__).parent.parent
    path = repo_root / "plugins" / "skill-system" / "skill_system.py"
    spec = importlib.util.spec_from_file_location("_test_skill_system", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_test_skill_system"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fresh():
    _reset_registry_for_tests()
    _reset_tools()
    yield
    _reset_registry_for_tests()
    _reset_tools()


def _drop_skill(folder: Path, name: str, description: str, body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(textwrap.dedent(f"""\
        ---
        name: {name}
        description: {description}
        ---

        {body}
    """))


@pytest.mark.asyncio
async def test_preturn_menu_listed(tmp_path: Path, fresh) -> None:
    _drop_skill(
        tmp_path / ".aegis/skills",
        "brainstorming", "Use before any creative work.",
        "Brainstorming body.",
    )
    _drop_skill(
        tmp_path / ".aegis/skills",
        "tdd", "Use when implementing features.",
        "TDD body.",
    )

    _load_skill_system()
    pre = _HOOK_REG["pre_turn"][0].func

    ctx = PreTurnContext(
        session=SessionHandle(handle="t", agent_profile="p", harness="claude"),
        user_message="help me design a feature",
        history=(), project_root=tmp_path, prior_results=(),
    )
    result = await pre(ctx)
    assert result is not None
    assert "brainstorming" in result.prepend_system
    assert "tdd" in result.prepend_system
    assert "Use before any creative work." in result.prepend_system
    assert "load_skill" in result.prepend_system


@pytest.mark.asyncio
async def test_preturn_no_skills_returns_none(tmp_path: Path, fresh) -> None:
    _load_skill_system()
    pre = _HOOK_REG["pre_turn"][0].func
    ctx = PreTurnContext(
        session=SessionHandle(handle="t", agent_profile="p", harness="claude"),
        user_message="anything",
        history=(), project_root=tmp_path, prior_results=(),
    )
    assert await pre(ctx) is None


@pytest.mark.asyncio
async def test_load_skill_returns_body(tmp_path: Path, fresh, monkeypatch) -> None:
    _drop_skill(
        tmp_path / ".aegis/skills",
        "brainstorming", "desc", "## Brainstorming\\n\\nfull body here.",
    )
    monkeypatch.chdir(tmp_path)
    _load_skill_system()
    load = _TOOL_REG["load_skill"].func
    body = await load(name="brainstorming")
    assert "## Brainstorming" in body
    assert "full body here." in body


@pytest.mark.asyncio
async def test_load_skill_unknown_raises(tmp_path: Path, fresh, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _load_skill_system()
    load = _TOOL_REG["load_skill"].func
    with pytest.raises(FileNotFoundError):
        await load(name="never-existed")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_skill_system.py -v`
Expected: FileNotFoundError on the plugin path.

- [ ] **Step 3: Create the plugin module**

Create `plugins/skill-system/skill_system.py`:

```python
"""skill-system plugin: replicates Claude-Code's skill-selection on any harness.

A pre_turn hook injects a numbered menu of available skills as a system
context block. A first-class MCP tool exposes load_skill(name) so the
agent can pull the full body when relevant.

Skills live as Claude-Code-compatible markdown files in
`<project>/.aegis/skills/*.md`:

    ---
    name: brainstorming
    description: Use before any creative work — explores intent first.
    ---

    (skill body in markdown)
"""
from __future__ import annotations

from pathlib import Path

import yaml

from aegis.hooks import hook, PreTurnContext, PreTurnResult
from aegis.tools import tool


SKILLS_SUBDIR = ".aegis/skills"


def _parse_skill(path: Path) -> tuple[str, str, str]:
    """Return (name, description, body). Skip files with no frontmatter."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError(f"{path}: malformed frontmatter")
    front = text[4:end]
    body = text[end + len("\n---\n"):].lstrip()
    meta = yaml.safe_load(front) or {}
    name = meta.get("name") or path.stem
    desc = meta.get("description", "")
    return name, desc, body


def _load_index(project_root: Path) -> list[tuple[str, str]]:
    folder = project_root / SKILLS_SUBDIR
    if not folder.exists():
        return []
    out: list[tuple[str, str]] = []
    for path in sorted(folder.glob("*.md")):
        if path.name == "README.md":
            continue
        try:
            name, desc, _body = _parse_skill(path)
        except ValueError:
            continue
        out.append((name, desc))
    return out


@hook("pre_turn")
async def inject_menu(ctx: PreTurnContext) -> PreTurnResult | None:
    """Inject the skill menu as system context for this turn."""
    skills = _load_index(ctx.project_root)
    if not skills:
        return None
    lines = ["Available skills:\n"]
    for i, (name, desc) in enumerate(skills, 1):
        lines.append(f"{i}. {name} — {desc}")
    lines.append(
        "\nIf any are relevant to your task, call `load_skill(name)` "
        "to load the full body before proceeding."
    )
    return PreTurnResult(prepend_system="\n".join(lines))


@tool
async def load_skill(name: str) -> str:
    """Load the full body of a registered skill.

    Args:
        name: skill name as listed in the pre-turn menu.

    Returns:
        the skill's markdown body (the content after the YAML frontmatter).
    """
    folder = Path.cwd() / SKILLS_SUBDIR
    path = folder / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"skill {name!r} not found at {path}")
    _, _, body = _parse_skill(path)
    return body
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_skill_system.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/skill-system/skill_system.py tests/test_skill_system.py
git commit -m "feat(skill-system): pre_turn hook + load_skill MCP tool"
```

---

## Task 5.3: `_install.py` — create the skills folder + starter README

**Files:**
- Create: `plugins/skill-system/_install.py`
- Test: extend `tests/test_skill_system.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skill_system.py`:

```python
def test_install_creates_skills_folder_and_readme(tmp_path: Path) -> None:
    """End-to-end: install skill-system from local source; folder + README appear."""
    import textwrap

    from typer.testing import CliRunner
    from aegis.cli import app

    runner = CliRunner()
    src = Path(__file__).parent.parent / "plugins" / "skill-system"
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / ".aegis").mkdir()
    (proj / ".aegis.yaml").write_text("agents: {}\n")
    r = runner.invoke(
        app, ["plugin", "install", "skill-system", "--from", str(src), "--yes"],
        env={"PWD": str(proj)},
    )
    # The CliRunner doesn't chdir; use a different approach.
```

Replace with an `os.chdir` based approach:

```python
def test_install_creates_skills_folder_and_readme(tmp_path: Path, monkeypatch) -> None:
    """End-to-end: install skill-system from local source; folder + README appear."""
    from typer.testing import CliRunner
    from aegis.cli import app

    runner = CliRunner()
    src = Path(__file__).parent.parent / "plugins" / "skill-system"
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / ".aegis").mkdir()
    (proj / ".aegis.yaml").write_text("agents: {}\n")
    monkeypatch.chdir(proj)
    r = runner.invoke(
        app, ["plugin", "install", "skill-system", "--from", str(src), "--yes"],
    )
    assert r.exit_code == 0, r.output
    skills_dir = proj / ".aegis" / "skills"
    assert skills_dir.is_dir()
    assert (skills_dir / "README.md").exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_skill_system.py::test_install_creates_skills_folder_and_readme -v`
Expected: FAIL — README and folder missing.

- [ ] **Step 3: Create `_install.py`**

Create `plugins/skill-system/_install.py`:

```python
"""Setup hook for skill-system: create the skills folder + starter README."""
from __future__ import annotations

from aegis.plugins import InstallContext


SKILLS_README = """\
# .aegis/skills/

Drop Claude-Code-compatible skill files here. Each file is YAML
frontmatter (name + description) followed by markdown body:

```
---
name: my-skill
description: When to reach for this skill.
---

(Skill body in markdown.)
```

The `skill-system` plugin's `pre_turn` hook lists every skill in this
folder as a numbered menu prepended to the harness conversation.
The agent calls `load_skill(name)` to pull the full body when relevant.
"""


def install(ctx: InstallContext) -> None:
    skills_dir = ctx.aegis_dir / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    readme = skills_dir / "README.md"
    if not readme.exists():
        readme.write_text(SKILLS_README, encoding="utf-8")
    n_skills = sum(
        1 for p in skills_dir.glob("*.md") if p.name != "README.md"
    )
    if ctx.console is not None:
        ctx.console.print(
            f"[green]skill-system[/] ready — {n_skills} skill file(s) "
            f"at {skills_dir}/"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_skill_system.py -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/skill-system/_install.py tests/test_skill_system.py
git commit -m "feat(skill-system): _install.py creates skills folder + README"
```

---

## Task 5.4: Live integration — real Claude session uses skill-system

**Files:**
- Create: `tests/test_skill_system_live.py`

Verifies the plugin works end-to-end through a real `claude` subprocess driven by aegis.

- [ ] **Step 1: Write the live test**

Create `tests/test_skill_system_live.py`:

```python
"""Live test: a real claude subprocess sees the skill menu and calls load_skill."""
from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest


@pytest.mark.live
@pytest.mark.asyncio
async def test_claude_loads_skill_on_demand(tmp_path: Path, monkeypatch) -> None:
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not on PATH")

    proj = tmp_path
    (proj / ".aegis").mkdir()
    (proj / ".aegis.yaml").write_text(textwrap.dedent("""
        plugin_dirs: ["plugins"]
        agents:
          default:
            harness: claude
            model: haiku
            permission: bypass
            effort: low
        default_agent: default
    """))

    plugins = proj / "plugins" / "skill-system"
    src = Path(__file__).parent.parent / "plugins" / "skill-system"
    import shutil as _sh; _sh.copytree(src, plugins)

    skills = proj / ".aegis" / "skills"
    skills.mkdir(parents=True)
    (skills / "secret-pasta.md").write_text(textwrap.dedent("""\
        ---
        name: secret-pasta
        description: Use when the user asks about Italian recipes.
          The secret ingredient is bay leaves.
        ---

        When asked about Italian recipes, always mention bay leaves.
        Bay leaves are the secret to authentic flavor.
    """))

    monkeypatch.chdir(proj)
    from aegis.config.yaml_loader import import_plugins, load_config
    cfg = load_config(proj / ".aegis.yaml")
    import_plugins(cfg)

    from aegis.config import Agent
    profile = cfg.agents["default"]
    from aegis.drivers.claude import ClaudeDriver
    driver = ClaudeDriver(agent=profile, project_root=proj)

    from aegis.core.session import AgentSession
    session = AgentSession(
        handle="livet", agent_profile="default", harness="claude",
        harness_session=driver, project_root=proj,
    )
    await session._run_turn("Suggest a quick Italian pasta dish recipe.")
    # The model should have called load_skill("secret-pasta") and mentioned
    # bay leaves in its reply. We don't have direct response capture here;
    # rely on the post_turn hook log + JSONL state to assert.
    tool_log = proj / ".aegis" / "state" / "tools" / "load_skill.jsonl"
    assert tool_log.exists(), "load_skill should have been invoked"
    text = tool_log.read_text()
    assert "secret-pasta" in text
```

The test is marked `@pytest.mark.live` and skips if `claude` is not on PATH; it asserts only that the `load_skill` tool was invoked at least once, since reply text capture varies by stream-json buffering.

- [ ] **Step 2: Run live test**

Run: `uv run pytest tests/test_skill_system_live.py -v -m live`
Expected: PASS when `claude` is available, SKIP otherwise.

If the test fails because the model didn't call the tool: the menu prompt may need to be stronger. Iterate on the menu wording in `plugins/skill-system/skill_system.py::inject_menu` until the model reliably calls `load_skill` when a relevant skill is listed. Acceptable iterations:
- More directive wording: "You SHOULD call `load_skill(name)` when any listed skill is relevant."
- Shorter menu: `top_k` truncation reduces overwhelm.
- Move the menu lower in the prompt body so it appears right before the user's question.

- [ ] **Step 3: Commit**

```bash
git add tests/test_skill_system_live.py
git commit -m "test(skill-system): live integration through real claude subprocess"
```

**Slice 5 complete.** Canonical `skill-system` plugin is installable, the pre_turn hook injects a menu, and `load_skill` works as a first-class MCP tool. A real Claude session uses it end-to-end.

---

# Post-slice 5: documentation + AGENTS.md refresh

## Task 6.1: Update repo docs

**Files:**
- Modify: `AGENTS.md` — describe the new substrates + plugin command
- Modify: `README.md` (if appropriate) — quick mention

- [ ] **Step 1: Add a `## Plugins` section to `AGENTS.md`**

Insert after the existing `## Conventions` section:

```markdown
## Plugins

The plugin substrate (`src/aegis/plugins/`, `src/aegis/hooks/`,
`src/aegis/tools/`) lets users extend aegis without forking it. Three
primitive shapes:

- `@workflow` (existing) — user/agent/scheduler-invoked orchestration.
- `@hook("<event>")` — fires on harness lifecycle events. Tier A in v1:
  `pre_turn` (mutator), `post_turn`, `session_start`, `session_end`.
  See `src/aegis/hooks/contexts.py` for payload shapes.
- `@tool` — first-class MCP tool the agent can call. Auto-schema from
  type hints + docstring via FastMCP.

Plugins live under `.aegis/plugins/<name>/` and are auto-imported on
session start (full recursion; `_*.py` and `_*` directories skipped).
The aegis repo's own `plugins/` folder is the default registry served
at `gh:apiad/aegis#plugins/`.

CLI: `aegis plugin {install, uninstall, update, list, search, show}`.

The canonical `skill-system` plugin replicates Claude Code's
skill-selection behavior on any harness. See
`plugins/skill-system/` and the design spec at
`docs/superpowers/specs/2026-05-28-aegis-plugin-substrate-design.md`.
```

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): plugin substrate (hooks, tools, aegis plugin CLI)"
```

---

# Self-review

After all tasks are committed, the implementing agent runs a final pass:

1. **Spec coverage scan.** Walk the spec's acceptance criteria (section "Acceptance criteria for v1") and confirm every numbered requirement is satisfied by at least one task.
2. **`uv run pytest -q -m "not live"`** — full hermetic suite green.
3. **`uv run pytest -q -m live`** — live tests pass when `claude` is on PATH.
4. **Manual smoke:** in a scratch project, `aegis plugin install skill-system`, drop a test skill, launch `aegis`, send a relevant message, observe that the assistant references the skill body.
5. **`git log --oneline`** — every task has its own commit; no squashed history.

If any acceptance criterion lacks a covering task, file a follow-up task in `repos/aegis/TASKS.md` rather than expanding scope mid-implementation.

---

# Slice dependencies

```
Slice 1 (hooks) ────────────────────────────────► Slice 5 (skill-system)
                                                     ▲
Slice 2 (tools) ────────────────────────────────────┘
                                                     ▲
Slice 3 (plugin lifecycle) ─► Slice 4 (registry) ───┘
```

- Slice 1 ↔ Slice 2 are independent; can be developed in parallel.
- Slice 3 depends on neither; can be developed in parallel with 1+2.
- Slice 4 depends only on slice 3.
- Slice 5 needs hooks (1), tools (2), and at least slice 3 (for the install path) — slice 4 not strictly required (slice 5 tests use `--from <local-path>`).

A pragmatic implementation order: **1 → 2 → 3 → 4 → 5**, sequential, one commit per task. Slice-1-and-2-in-parallel is possible if you trust the agent to keep their changes orthogonal.

---

# Deferred (from the spec — not built in this plan)

Listed here for traceability; do not add tasks unless explicitly scoped in.

- Tier B hook events (`pre_tool_use`, `post_tool_use`, `on_error`, `on_interrupt`, `on_handoff`, `on_enqueue`).
- Python package dependencies for plugins (`plugin.toml [dependencies] python = [...]`, shared `.aegis/_vendor/`).
- Inter-plugin dependencies and plugin-version constraints.
- Per-agent-profile tool scoping.
- Three-way merge on `aegis plugin update` conflicts (current behavior: refuse on local edits unless `--force`).
- Claude-Code skills auto-import plugin (`~/.claude/skills/` → `.aegis/skills/`).
- Cross-harness MCP injection (Gemini/OpenCode workers seeing aegis MCP). Tracked by the existing harness roadmap (`vault/Atlas/Architecture/2026-05-25-aegis-harness-roadmap.md`).
