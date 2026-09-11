# Aegis Roots and `embed()` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make aegis bootable in-process at an arbitrary root, many instances per process, by replacing process-global `Path.cwd()` resolution with three explicitly threaded roots — then expose that as `aegis.embed()`.

**Architecture:** Introduce one `AegisRoots` value object naming the three roots that are currently conflated (config root, state root, harness cwd). Thread it through `SessionManager`, `AgentSession`, the MCP tool surface and `_serve`, deleting every `or Path.cwd()` fallback as it goes. Then collapse the two boot paths into one `_serve()` that takes an optional UI attachment, and publish the no-UI attachment as `aegis.embed()`.

**Tech Stack:** Python 3.13, `uv`, pytest, Textual 8.2.x, typer, ruamel.yaml.

**Spec:** `docs/superpowers/specs/2026-09-07-retire-web-ui-tui-over-web-design.md`

This plan covers **stages 1–3 only** of that spec's six. It carries no deletion: the web client, the WS plane and `--remote` are all untouched and keep working. Stages 4–6 (the view seam, transports, deletion) are a separate plan.

## Global Constraints

- **Python ≥ 3.13**, `uv` only — `uv run pytest`, never bare `pip`/`python`.
- **English** for all code, comments, identifiers, test names and commit messages.
- **Conventional commits**; commit after every task.
- **No behaviour change is acceptable in stages 1–2** except one, which is a bug fix: `aegis` starts firing schedules. Everything else must be observably identical.
- **Never `git add -A`.** Stage the explicit paths named in each task.
- **The full suite flakes on 1–2 inotify tests** (known, unrelated). Gate on the blast-radius subset each task names, and run the full suite only at the end of a stage.
- **`find_project_root(start: Path | None = None)`** already accepts a start directory (`config/__init__.py:141`). Thread a root into it; do not rewrite it.
- **`asyncio_mode = "auto"`** (`pyproject.toml:92`) — async tests need no `@pytest.mark.asyncio`. Some existing tests carry it anyway; new tests in this plan omit it.
- **Signatures to match exactly** (verified against `main` on 2026-09-09):
  - `config.load_config(root: Path | None = None) -> tuple[dict[str, Agent], str]` — a **tuple**, not an object.
  - `config.load_queues(root: Path | None = None) -> dict[str, Queue]`
  - `config.edit.add_agent(root: Path, slug: str, *, provider=None, harness=None, model: str, effort=None, permission=None)` — `slug` is positional.
  - There is **no** `AegisConfig.to_dict()`; `aegis_config_show` builds its dict by hand (`mcp/server.py:637-660`).

---

## The defect this plan fixes

`SessionManager.state_root` is assigned in exactly one place — `attach_scheduler_context` (`core/manager.py:136`) — called from exactly one place (`cli.py:454`), inside `_serve`, and only when `schedules:` is configured.

So for the TUI, and for `aegis serve` without schedules, `state_root` is **always `None`**, and every `self.state_root or Path.cwd()` fallback (`manager.py:97,117,167`) is the *normal* path. `attach_persistence` is likewise handed `_state_dir(Path.cwd())` directly (`cli.py:401`).

The autouse fixture `isolated_project_dir` (`tests/conftest.py:126`) exists solely to contain this; its docstring is a description of the bug.

## File Structure

| File | Responsibility |
|---|---|
| `src/aegis/config/roots.py` *(new)* | `AegisRoots` — the three named roots and their derivation. Nothing else. |
| `src/aegis/core/manager.py` | Takes `AegisRoots`; loses three `or Path.cwd()` fallbacks and the `state_root`-via-scheduler side effect. |
| `src/aegis/core/session.py` | Takes an explicit project root; loses two `or Path.cwd()` fallbacks. |
| `src/aegis/mcp/server.py` | Config/schedule tools resolve the root from the server instance, not from 26 late-bound `find_project_root()` calls. |
| `src/aegis/terminal/manager.py`, `workflow/engine.py`, `usage/env.py` | Remaining cwd sites. |
| `src/aegis/cli.py` | `_serve(roots=…, ui=…)` as the single boot path; config guards raise; loop/signals move to the CLI wrappers. |
| `src/aegis/embed.py` *(new)* | `aegis.embed()` — the public library seam. |
| `tests/test_roots.py`, `tests/test_multi_instance.py` *(new)* | The gate: two instances, one process, no cross-talk. |

---

### Task 1: `AegisRoots` — name the three roots

**Files:**
- Create: `src/aegis/config/roots.py`
- Test: `tests/test_roots.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `AegisRoots(config_root: Path, state_root: Path, harness_cwd: Path)`, frozen dataclass; classmethod `AegisRoots.for_project(root: Path, harness_cwd: Path | None = None) -> AegisRoots`; property `state_dir -> Path` returning `state_root / ".aegis" / "state"`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_roots.py
from pathlib import Path

from aegis.config.roots import AegisRoots


def test_for_project_derives_all_three_from_one_path(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    assert roots.config_root == tmp_path
    assert roots.state_root == tmp_path
    assert roots.harness_cwd == tmp_path


def test_harness_cwd_can_differ_from_config_root(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    roots = AegisRoots.for_project(tmp_path, harness_cwd=worktree)
    assert roots.config_root == tmp_path
    assert roots.harness_cwd == worktree


def test_state_dir_is_under_state_root(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    assert roots.state_dir == tmp_path / ".aegis" / "state"


def test_roots_are_absolute_even_when_given_a_relative_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    roots = AegisRoots.for_project(Path("."))
    assert roots.config_root.is_absolute()


def test_roots_are_frozen(tmp_path):
    import dataclasses
    import pytest
    roots = AegisRoots.for_project(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        roots.config_root = tmp_path / "other"
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_roots.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.config.roots'`

- [x] **Step 3: Write minimal implementation**

```python
# src/aegis/config/roots.py
"""The three roots aegis resolves paths against.

They coincide when aegis runs from a project directory on a CLI, which is
why they were conflated as ``Path.cwd()`` for so long. They do not coincide
when aegis is embedded: one process holds several instances, each rooted at
a different worktree.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AegisRoots:
    """Where this aegis instance resolves things.

    config_root: holds .aegis.yaml, its overlays, plugin dirs, persona files.
    state_root:  parent of .aegis/state — persistence, locks, canvas,
                 terminals, views.
    harness_cwd: the directory an agent subprocess actually runs in.
    """

    config_root: Path
    state_root: Path
    harness_cwd: Path

    @classmethod
    def for_project(cls, root: Path,
                    harness_cwd: Path | None = None) -> "AegisRoots":
        """All three from one project directory, the CLI case."""
        resolved = Path(root).resolve()
        return cls(
            config_root=resolved,
            state_root=resolved,
            harness_cwd=Path(harness_cwd).resolve() if harness_cwd
            else resolved,
        )

    @property
    def state_dir(self) -> Path:
        """Mirrors ``state.workspace.state_dir`` so the two never drift."""
        return self.state_root / ".aegis" / "state"
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_roots.py -v`
Expected: PASS (5 tests)

- [x] **Step 5: Assert it matches the existing state-dir convention**

The new `state_dir` property must agree with `state.workspace.state_dir`
(`src/aegis/state/workspace.py:67`), or state silently splits in two.

```python
# append to tests/test_roots.py
def test_state_dir_agrees_with_workspace_helper(tmp_path):
    from aegis.state.workspace import state_dir as workspace_state_dir
    roots = AegisRoots.for_project(tmp_path)
    assert roots.state_dir == workspace_state_dir(tmp_path)
```

Run: `uv run pytest tests/test_roots.py -v`
Expected: PASS (6 tests)

- [x] **Step 6: Commit**

```bash
git add src/aegis/config/roots.py tests/test_roots.py
git commit -m "feat(config): AegisRoots — name the three roots explicitly"
```

---

### Task 2: `SessionManager` takes roots; delete its cwd fallbacks

**Files:**
- Modify: `src/aegis/core/manager.py:56-81` (constructor), `:97`, `:117`, `:132-136` (`attach_scheduler_context`), `:167`
- Modify: `src/aegis/cli.py:401,404` (call sites)
- Test: `tests/test_manager_roots.py`

**Interfaces:**
- Consumes: `AegisRoots` from Task 1.
- Produces: `SessionManager(..., roots: AegisRoots)` — keyword-only, **required**. `manager.roots` is a public attribute. `manager.state_root` remains a `Path` (never `None`) derived from `roots.state_root`. `attach_scheduler_context(scheduler=…, state_root=…, …)` keeps its signature for now but **no longer assigns** `self.state_root`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_manager_roots.py
from pathlib import Path

from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager


def _mgr(root: Path) -> SessionManager:
    return SessionManager(
        agents={}, default_agent="", make_session=lambda *a, **k: None,
        mcp=None, roots=AegisRoots.for_project(root))


def test_state_root_is_set_without_a_scheduler(tmp_path):
    """Regression: state_root was only ever assigned by
    attach_scheduler_context, so every non-scheduler boot fell back to cwd."""
    mgr = _mgr(tmp_path)
    assert mgr.state_root == tmp_path


def test_state_root_ignores_the_process_cwd(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    mgr = _mgr(project)
    monkeypatch.chdir(elsewhere)
    assert mgr.state_root == project


def test_two_managers_have_disjoint_state_roots(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    assert _mgr(a).state_root != _mgr(b).state_root
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_manager_roots.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'roots'`

- [x] **Step 3: Add `roots` to the constructor**

In `src/aegis/core/manager.py`, change the signature at line 56:

```python
    def __init__(self, agents: dict, default_agent: str,
                 make_session: SessionFactory, mcp,
                 *, inbox=None, hosts: dict | None = None,
                 local_root: str | None = None,
                 roots: "AegisRoots") -> None:
```

Add the import at the top of the file:

```python
from aegis.config.roots import AegisRoots
```

Replace line 81 (`self.state_root: Path | None = None`) with:

```python
        self.roots = roots
        # Was None until attach_scheduler_context happened to set it, which
        # only occurred under `aegis serve` with schedules configured. Every
        # other boot fell through to Path.cwd().
        self.state_root: Path = roots.state_root
```

- [x] **Step 4: Delete the three cwd fallbacks**

Line 97 and line 117 — replace `root_fn=lambda: self.state_root or Path.cwd(),` with:

```python
            root_fn=lambda: self.state_root,
```

Line 167 is inside `reload_plugins`, which feeds
`yaml_loader.load_config(root)` — that is a **config** root, not a state
root. It only read `state_root` because the two were conflated. Replace
`root = self.state_root or Path.cwd()` with:

```python
        root = self.roots.config_root
```

This is the one site in this task where the three roots actually diverge;
copying the `state_root` pattern here would carry the conflation forward
into the new API.

- [x] **Step 5: Stop `attach_scheduler_context` reassigning `state_root`**

At `manager.py:136`, delete the line `self.state_root = state_root` and
replace it with an assertion that the caller agrees with the constructor —
a silent disagreement here would reintroduce the split:

```python
        if Path(state_root) != self.state_root:
            raise ValueError(
                f"scheduler state_root {state_root} disagrees with "
                f"manager roots {self.state_root}")
```

- [x] **Step 6: Update the two `cli.py` call sites**

At `cli.py:401` and `:404`, replace the `Path.cwd()` arguments:

```python
    mgr.attach_persistence(roots.state_dir)
    ...
    mgr.attach_locks_state(roots.state_dir)
```

`roots` is not yet in scope in `_serve` — Task 6 threads it. For this task,
construct it locally at the top of `_serve` so the file stays importable:

```python
    from aegis.config.roots import AegisRoots
    roots = AegisRoots.for_project(Path(local_root or "."))
```

Task 6 replaces this local with the threaded parameter.

- [x] **Step 7: Run the new tests**

Run: `uv run pytest tests/test_manager_roots.py -v`
Expected: PASS (3 tests)

- [x] **Step 8: Run the blast radius**

Run: `uv run pytest tests/ -k "manager or session or serve or persist or lock" -q`
Expected: PASS. Any failure here is a real call site needing `roots=`; fix it rather than restoring the default.

- [x] **Step 9: Commit**

```bash
git add src/aegis/core/manager.py src/aegis/cli.py tests/test_manager_roots.py
git commit -m "fix(core): root SessionManager state explicitly, not via the scheduler

state_root was assigned only by attach_scheduler_context, called only from
_serve, and only when schedules were configured — so the TUI and every
schedule-less serve fell back to Path.cwd(). It is now a constructor input."
```

---

### Task 3: `AgentSession` takes an explicit project root

**Files:**
- Modify: `src/aegis/core/session.py:85,91`
- Test: `tests/test_session_roots.py`

**Interfaces:**
- Consumes: `AegisRoots` from Task 1.
- Produces: `AgentSession(..., project_root: Path)` — required, no `or Path.cwd()` fallback. `session.project_root` and `session.place` both derive from it.

- [x] **Step 1: Write the failing test**

```python
# tests/test_session_roots.py
def test_project_root_ignores_process_cwd(tmp_path, monkeypatch):
    """session.py:85,91 used `project_root or Path.cwd()`, so a session
    built without an explicit root silently adopted the process cwd —
    wrong the moment two instances share a process."""
    from aegis.core.session import AgentSession
    import inspect
    sig = inspect.signature(AgentSession.__init__)
    param = sig.parameters["project_root"]
    assert param.default is inspect.Parameter.empty, (
        "project_root must be required; a default reintroduces the cwd "
        "fallback this task removes")
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session_roots.py -v`
Expected: FAIL — `project_root` currently has a default of `None`

- [x] **Step 3: Make it required**

In `src/aegis/core/session.py`, change the `project_root` parameter to have
no default, and replace lines 85 and 91:

```python
        self.place = place or Place("local", str(project_root))
        ...
        self.project_root = Path(project_root)
```

- [x] **Step 4: Run and fix call sites**

Run: `uv run pytest tests/ -k "session" -q`
Expected: failures at every construction site missing `project_root`. Pass
`roots.harness_cwd` from `_session_factory` (`cli.py:47`); pass `tmp_path` in
tests.

- [x] **Step 5: Verify**

Run: `uv run pytest tests/test_session_roots.py tests/ -k "session" -q`
Expected: PASS

- [x] **Step 6: Commit**

```bash
git add src/aegis/core/session.py src/aegis/cli.py tests/test_session_roots.py
git commit -m "fix(core): require an explicit project_root on AgentSession"
```

---

### Task 4: MCP tools resolve the root from the server, not the cwd

**Files:**
- Modify: `src/aegis/mcp/server.py` — all 26 `find_project_root()` call sites
- Test: `tests/test_mcp_root_isolation.py`

**Interfaces:**
- Consumes: `AegisRoots` from Task 1; `manager.roots` from Task 2.
- Produces: MCP tool closures resolve `roots.config_root` from the bound manager. No `find_project_root()` call remains in `mcp/server.py`.

**Why this task is the sharpest one:** these 26 sites resolve the root **at call time from the process cwd**. Embedded, an agent working in worktree B that calls `aegis_config_add_agent` writes to whichever `.aegis.yaml` the process walks up to — silently, and into another instance's config.

> **Coordinator deviation, 2026-09-11 — `bridge.roots` does not exist on two of the three bridges.**
> Step 4 binds `roots = bridge.roots`, but `build_server(bridge)` is called with
> three different implementations and only one has the attribute after Task 2:
>
> | Bridge | Call site | `.roots` after Task 2 |
> |---|---|---|
> | `SessionManager` | `cli.py:416` (`serve`) | yes |
> | `AegisApp` | `tui/app.py:472` — `self._mcp.bind(self)` | **no** |
> | `_NullBridge` | `runtime.py:59` fallback | **no** |
>
> `AppBridge` (`mcp/bridge.py:58`) does not declare `roots` either. As written,
> Task 4 makes every TUI session raise `AttributeError` when `AegisMCP.start()`
> builds the server, and breaks `_NullBridge`'s stated contract that "tools
> return empty/unavailable rather than crashing".
>
> **The task's own test cannot catch this** — it sets `.roots` by hand on a
> `_StubBridge`, so it goes green while both real bridges break. That is the
> "green suite over a broken path" failure this plan's parent spec exists to
> prevent.
>
> Folded into Task 4 rather than deferred: declare `roots` on the `AppBridge`
> Protocol, and give `AegisApp` and `_NullBridge` one derived from the cwd each
> already holds. Task 7b can replace `AegisApp`'s derivation with the threaded
> roots once the TUI boots through `_serve`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_root_isolation.py
"""The gate for Task 4: config writes must land in the instance that made
them. Asserts on file contents, not on resolved paths — a path assertion
passes while the 26 late-bound lookups still read the process cwd."""
from pathlib import Path

CONFIG = """\
agents:
  opus:
    provider: claude-code
    model: opus
default_agent: opus
"""


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / ".aegis.yaml").write_text(CONFIG, encoding="utf-8")
    return root


async def test_config_write_lands_only_in_its_own_instance(tmp_path, monkeypatch):
    """Reuses the existing harness in tests/test_mcp_config_tools.py rather
    than a new one: `_StubBridge` already implements register_agent, and
    `_call` already unwraps FastMCP's ToolResult."""
    from aegis.config.roots import AegisRoots
    from aegis.mcp.server import build_server

    from tests.test_mcp_config_tools import _StubBridge, _call

    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    before_b = (b / ".aegis.yaml").read_text(encoding="utf-8")

    bridge = _StubBridge()
    bridge.roots = AegisRoots.for_project(a)   # what Task 2 threads in
    server = build_server(bridge)

    # Stand in the *other* project. A cwd-resolving implementation writes
    # to b; a roots-resolving one writes to a.
    monkeypatch.chdir(b)
    result = await _call(server, "aegis_config_add_agent",
                         slug="sonnet", harness="claude-code",
                         model="sonnet")

    assert (b / ".aegis.yaml").read_text(encoding="utf-8") == before_b, (
        "instance A's config write leaked into instance B")
    assert "sonnet" in (a / ".aegis.yaml").read_text(encoding="utf-8")
    assert result == {"ok": True, "live": True, "restart_required_for": []}
    assert bridge.registered_agents and bridge.registered_agents[0][0] == "sonnet", (
        "the hot-register (bridge.register_agent) was dropped; the tool's "
        "docstring promises it and the next spawn depends on it")


def test_no_find_project_root_calls_remain_in_mcp_server():
    """Structural guard, by AST rather than substring — a substring check is
    fooled by a line break or a mention in a comment. The fix is to stop
    calling it in this module at all, so assert on calls, not on text."""
    import ast

    import aegis.mcp.server as srv

    tree = ast.parse(Path(srv.__file__).read_text(encoding="utf-8"))
    calls = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "find_project_root"
    ]
    assert not calls, (
        f"mcp/server.py must resolve roots from bridge.roots; "
        f"find_project_root still called at lines {calls}")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_root_isolation.py -v`
Expected: FAIL — `_StubBridge` has no `roots` attribute (Task 2 adds it to the real manager; the test sets it explicitly), and the AST guard reports 26 `find_project_root` call sites.

- [ ] **Step 3: Enumerate the sites before editing**

Run: `uv run python -c "
import re, pathlib
src = pathlib.Path('src/aegis/mcp/server.py').read_text()
for i, line in enumerate(src.splitlines(), 1):
    if 'find_project_root' in line:
        print(f'{i}: {line.strip()}')
"`
Expected: 26 lines. Work through them all; the source guard in Step 1 fails until every one is gone.

- [ ] **Step 4: Take the root from the bridge — do not extract anything**

All 26 sites already live inside `build_server(bridge: AppBridge, tokens=None)`
(`mcp/server.py:592`), which holds `bridge`. After Task 2, `bridge.roots`
exists. So this task is one binding plus 26 mechanical edits — **no factory,
no extraction, no re-registration**.

Near the top of `build_server`, beside `config_write_lock` (`server.py:620`):

```python
    roots = bridge.roots
```

Then, at each of the 26 sites, delete the lookup and its guard:

```python
        root = find_project_root()            # delete
        if root is None:                       # delete
            return {"error": "no .aegis.yaml found"}   # delete
```

replacing them with:

```python
        root = roots.config_root
```

**Three things not to break while doing it:**

- **Keep `config_write_lock` and `bridge.register_agent`.** The real
  `aegis_config_add_agent` (`server.py:725-755`) holds the lock across the
  write and hot-registers the new `Agent` on the live map — that is the
  "Hot-registers" its docstring promises. Dropping either ships a tool that
  persists but stops hot-registering, and races the other twelve writers.
- **Two of the 26 are not config tools.** Lines 1625 and 1639 are inside
  `aegis_run_dynamic_workflow`. Same edit, but do not expect them under a
  config-tool heading.
- **Thirteen tools have a now-dead branch.** `roots.config_root` is never
  `None`, so every `return {"error": "no .aegis.yaml found"}` becomes
  unreachable. Delete them rather than leaving dead code that implies a
  contract the type no longer allows.

The previous draft of this task proposed a `build_config_tools(roots)`
factory. It was wrong twice over: unnecessary, because `bridge` is already in
scope; and lossy, because the extracted snippet dropped the lock and the
hot-register. It is recorded here so nobody reinvents it.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_mcp_root_isolation.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run the blast radius**

Run: `uv run pytest tests/ -k "mcp or config" -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_mcp_root_isolation.py
git commit -m "fix(mcp): bind config tools to an explicit root

All 26 find_project_root() calls in mcp/server.py resolved at invocation
time from the process cwd. Embedded, an agent in one worktree could write
another instance's .aegis.yaml."
```

---

### Task 5: The remaining cwd sites

**Files:**
- Modify: `src/aegis/terminal/manager.py:156`, `src/aegis/workflow/engine.py:199`, `src/aegis/usage/env.py:14`
- Modify: `src/aegis/mcp/server.py:616` (CommsLedger state-dir fallback), `:2344` (`_register_user_tool`)
- Test: `tests/test_no_cwd_regression.py`

**Note:** those last two are *not* `find_project_root` calls, so Task 4 does
not touch them — but they are `Path.cwd()` sites in a module the guard below
covers, so the guard cannot go green until they are gone too. Both take
`roots.state_dir`.

**Interfaces:**
- Consumes: `AegisRoots`.
- Produces: no new public API; a structural guard that stops the fallbacks coming back.

- [ ] **Step 1: Write the failing guard test**

```python
# tests/test_no_cwd_regression.py
"""Structural guard. The 66 cwd sites are being removed module by module;
this pins the ones already done so they cannot regress. Add modules to
CLEANED as later tasks finish them."""
from pathlib import Path

CLEANED = [
    "core/manager.py",
    "core/session.py",
    "mcp/server.py",
    "terminal/manager.py",
    "workflow/engine.py",
    "usage/env.py",
]


def test_cleaned_modules_do_not_resolve_from_the_process_cwd():
    src = Path("src/aegis")
    offenders = []
    for rel in CLEANED:
        text = (src / rel).read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "Path.cwd()" in line or "os.getcwd()" in line:
                offenders.append(f"{rel}:{i}: {line.strip()}")
    assert not offenders, "cwd resolution reintroduced:\n" + "\n".join(offenders)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_no_cwd_regression.py -v`
Expected: FAIL, listing five sites — `terminal/manager.py:156`,
`workflow/engine.py:199`, `usage/env.py:14`, **and `mcp/server.py:616` and
`:2344`**. If the failure lists only three, the guard is reading a stale
module list and will go green while two cwd sites remain.

- [ ] **Step 3: Thread the root into each**

For each module, add a required root parameter to the constructor or
function that currently falls back, and pass `roots.state_dir` (terminals),
`roots.config_root` (workflow engine) or `roots.config_root` (usage env)
from the caller. Example, `terminal/manager.py`:

```python
    def __init__(self, *, state_dir: Path) -> None:
        self._state_dir = Path(state_dir)   # was: state_dir or Path.cwd()/...
```

- [ ] **Step 4: Run the guard and the blast radius**

Run: `uv run pytest tests/test_no_cwd_regression.py tests/ -k "terminal or workflow or usage" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aegis/terminal/manager.py src/aegis/workflow/engine.py \
        src/aegis/usage/env.py tests/test_no_cwd_regression.py
git commit -m "fix(state): thread roots through terminals, workflows and usage"
```

---

### Task 6: `_serve` takes roots

**Files:**
- Modify: `src/aegis/cli.py:373-386` (signature), `:401,404,405,410,450,455,463,481`
- Test: `tests/cli/test_serve_roots.py`

**Interfaces:**
- Consumes: `AegisRoots`; `SessionManager(roots=…)` from Task 2.
- Produces: `_serve(*, roots: AegisRoots, agents, default_agent, make_session, mcp, stop, queues=None, schedules=None, remotes=None, remote_plane=None, web=None, hosts=None, host_registry=None, inline_schedule_names=None)`. The `local_root: str | None` parameter is **removed** — `roots.harness_cwd` replaces it.

**Also drop `local_root` from `SessionManager`.** It survives at
`manager.py:58`/`:67` (`self._local_root = local_root or "."`) and `_serve`
passes it at `cli.py:385`. Leaving both means two names for one concept — the
ambiguity this stage exists to remove. Delete the parameter and replace its
two internal uses (`manager.py:193` and `:208`, the `Place("local", …)`
comparison) with `str(self.roots.harness_cwd)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_serve_roots.py
def test_serve_takes_roots_and_not_local_root():
    import inspect
    from aegis.cli import _serve
    params = inspect.signature(_serve).parameters
    assert "roots" in params, "_serve must take AegisRoots"
    assert "local_root" not in params, (
        "local_root is superseded by roots.harness_cwd; keeping both "
        "reintroduces the ambiguity this stage removes")


def test_serve_has_no_cwd_calls():
    """_serve held eight Path.cwd() calls (cli.py:401,404,405,410,450,455,
    463,481) while accepting a local_root it used once."""
    import inspect
    from aegis.cli import _serve
    source = inspect.getsource(_serve)
    assert "Path.cwd()" not in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_serve_roots.py -v`
Expected: FAIL on both

- [ ] **Step 3: Change the signature**

```python
async def _serve(*, roots: AegisRoots,
                 agents, default_agent, make_session, mcp,
                 stop: asyncio.Event, queues: dict | None = None,
                 schedules: dict | None = None,
                 remotes: dict | None = None,
                 remote_plane=None, web=None,
                 hosts: dict | None = None, host_registry=None,
                 inline_schedule_names: set[str] | None = None) -> None:
```

- [ ] **Step 4: Replace all eight cwd calls**

Delete the local `roots = AegisRoots.for_project(...)` added in Task 2 Step 6, then:

```python
    mgr.attach_persistence(roots.state_dir)            # was _state_dir(Path.cwd())
    mgr.attach_locks_state(roots.state_dir)            # was _state_dir(Path.cwd())
    cm = CanvasManager(state_dir=roots.state_dir, ...)
    tm = TerminalManager(state_dir=roots.state_dir / "terminals")
    scheduler = Scheduler(schedules=schedules, state_dir=roots.state_dir, ...)
    mgr.attach_scheduler_context(scheduler=scheduler, state_root=roots.state_root, ...)
    root = roots.config_root                            # was Path.cwd()
    web_fe = WebFrontend(mgr, web, state_dir=roots.state_dir, ...)
```

- [ ] **Step 5: Update `_run_serve` to build and pass roots**

```python
    roots = AegisRoots.for_project(root, harness_cwd=Path(effective))
    ...
        await _serve(roots=roots, agents=agents, default_agent=default_agent,
                     make_session=make_session, mcp=AegisMCP(), stop=stop,
                     queues=queues, schedules=schedules, remotes=remotes,
                     remote_plane=remote_plane, web=web, hosts=hosts,
                     host_registry=host_registry,
                     inline_schedule_names=inline_schedule_names)
```

- [ ] **Step 6: Verify and add `cli.py` to the guard**

Run: `uv run pytest tests/cli/test_serve_roots.py -v`
Expected: PASS

- [ ] **Step 7: Run the blast radius**

Run: `uv run pytest tests/ -k "serve or cli or scheduler" -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/aegis/cli.py tests/cli/test_serve_roots.py
git commit -m "refactor(cli): thread AegisRoots through _serve, drop local_root"
```

---

### Task 7a: A real local injection seam — `AegisApp(bridge=…)`

**Files:**
- Modify: `src/aegis/tui/app.py:378-385` (the `manager=` branch), and the 9 `hasattr(self, "_remote_manager")` guards at `:539,889,918,967,1215,1318,1335,1362,1664`
- Test: `tests/tui/test_local_bridge_injection.py`

**Interfaces:**
- Consumes: `SessionManager` from Task 2.
- Produces: `AegisApp(..., bridge: SessionManager | None = None)` — a **local** injection path, distinct from `manager=`. When `bridge=` is given, the app uses it as its `SessionManager` and **keeps every local-plane feature on**. `manager=` keeps its exact current `--remote` meaning and is untouched.

**Why this is its own task.** The previous draft of this plan claimed
`AegisApp(manager=…)` was a general injection seam. It is not. At
`app.py:378` the branch reads *"Skip all local plane construction"*, sets
`self._remote_manager`, and 9 `hasattr(self, "_remote_manager")` guards then
turn local features **off** — `:918` nulls `queue_manager`, `:1318` disables
the hosts axis. Worse, that path calls three methods that exist **only** on
`RemoteSessionManager` and not on `SessionManager`: `make_pane_core`
(`app.py:1948`), `_add_session` (`app.py:1241`), `shutdown` (`app.py:1665`) —
verified `local=0 remote=1` for all three.

A `LocalTuiAttachment` built on `manager=` would therefore paint fine and
then `AttributeError` on opening a pane and on quit. **A launch-and-look
check does not catch this** — mount succeeds; the failures are on
interaction and teardown. Hence the test below spawns a pane and quits.

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_local_bridge_injection.py
"""bridge= must inject a local SessionManager WITHOUT degrading to the
--remote plane. The three assertions map to the three ways the manager=
path fails: features nulled, hosts axis disabled, and RemoteSessionManager-
only methods called on a local manager."""
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.tui.app import AegisApp


def _manager(tmp_path):
    return SessionManager(
        agents={}, default_agent="", make_session=lambda *a, **k: None,
        mcp=None, roots=AegisRoots.for_project(tmp_path))


async def test_bridge_does_not_set_the_remote_sentinel(tmp_path):
    app = AegisApp(agents={}, default_agent="", make_session=None, mcp=None,
                   queues={}, clean=True, drivers={}, cwd=str(tmp_path),
                   voice=None, bridge=_manager(tmp_path))
    async with app.run_test(size=(100, 30)):
        assert not hasattr(app, "_remote_manager"), (
            "bridge= must not take the --remote path; 9 hasattr guards "
            "switch local features off when that sentinel is present")


async def test_bridge_keeps_the_local_plane_on(tmp_path):
    mgr = _manager(tmp_path)
    app = AegisApp(agents={}, default_agent="", make_session=None, mcp=None,
                   queues={}, clean=True, drivers={}, cwd=str(tmp_path),
                   voice=None, bridge=mgr)
    async with app.run_test(size=(100, 30)):
        # app.py:918 nulls this on the remote path
        assert app.queue_manager is not None
        assert app.manager is mgr


async def test_bridge_survives_a_pane_and_a_clean_quit(tmp_path):
    """The failure mode a launch-and-look check misses: mount succeeds,
    then make_pane_core / _add_session / shutdown blow up, because those
    three exist only on RemoteSessionManager."""
    app = AegisApp(agents={}, default_agent="", make_session=None, mcp=None,
                   queues={}, clean=True, drivers={}, cwd=str(tmp_path),
                   voice=None, bridge=_manager(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("ctrl+e")      # a terminal tab: local-plane only
        await pilot.pause()
        await pilot.press("ctrl+q")      # quit path calls shutdown()
        await pilot.pause()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_local_bridge_injection.py -v`
Expected: FAIL — `AegisApp.__init__() got an unexpected keyword argument 'bridge'`

- [ ] **Step 3: Add the parameter**

Add `bridge=None` to `AegisApp.__init__` (`app.py:310`). Where the
constructor currently builds a `SessionManager`, use `bridge` when given:

```python
        if manager is not None:
            # --remote path: unchanged. Skips local plane construction.
            self._remote_manager = manager
            self._ws = getattr(manager, "_ws", None)
            ...
        else:
            # Local plane. `bridge` supplies an already-built manager (the
            # daemon case); otherwise construct one as before. Either way
            # NO _remote_manager sentinel is set, so every hasattr guard
            # below stays on its local branch.
            self.manager = bridge if bridge is not None else SessionManager(...)
            # ...the rest of local plane construction, unchanged
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/tui/test_local_bridge_injection.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Confirm `--remote` is untouched**

This plan deletes nothing, so the `--remote` path must behave exactly as before.

Run: `uv run pytest tests/ -k "remote or tui" -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/aegis/tui/app.py tests/tui/test_local_bridge_injection.py
git commit -m "feat(tui): AegisApp(bridge=) — inject a local manager without degrading

manager= is the --remote path: it sets _remote_manager, skips local plane
construction, and 9 hasattr guards then disable queues and the hosts axis.
It also calls make_pane_core/_add_session/shutdown, which exist only on
RemoteSessionManager. bridge= injects a local SessionManager with the whole
local plane intact."
```

---

### Task 7b: One boot path — `_serve(ui=…)`

**Files:**
- Modify: `src/aegis/cli.py:110-230` (the TUI branch), `:373` (`_serve`)
- Test: `tests/cli/test_boot_unification.py`

**Interfaces:**
- Consumes: `_serve(roots=…)` from Task 6; `AegisApp(bridge=…)` from Task 7a.
- Produces: `_serve(..., ui: UIAttachment | None = None)`, where `UIAttachment` is a Protocol with `async def run(self, manager) -> None`. `LocalTuiAttachment` implements it.

**The bug this fixes:** `tui/app.py:466` — *"The TUI does not run a scheduler."* After this task it does.

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_boot_unification.py
"""Fails against main: the TUI path constructs AegisApp directly
(cli.py:224) and never wires a scheduler, so `aegis` silently does not fire
schedules while `aegis serve` does."""
import asyncio


class _StubMCP:
    """_serve calls mcp.bind/start/stop and reads mcp.port (cli.py:415-418,
    :497). mcp=None crashes before reaching anything under test — which
    would fail identically against a correct and a broken implementation."""

    def __init__(self): self.port = 0; self.bound = None
    def bind(self, mgr): self.bound = mgr
    async def start(self): self.port = 12345
    async def stop(self): return None


class _RecordingUI:
    def __init__(self): self.manager = None
    async def run(self, manager): self.manager = manager


async def test_local_ui_boot_starts_a_scheduler(tmp_path):
    from aegis.cli import _serve
    from aegis.config.roots import AegisRoots

    ui = _RecordingUI()
    stop = asyncio.Event()
    stop.set()

    await _serve(
        roots=AegisRoots.for_project(tmp_path),
        agents={}, default_agent="", make_session=lambda *a, **k: None,
        mcp=_StubMCP(), stop=stop,
        schedules={"nightly": {"cron": "0 3 * * *", "workflow": "noop"}},
        ui=ui)

    assert ui.manager is not None, "the UI attachment never received a manager"
    assert ui.manager.scheduler is not None, (
        "a UI-attached boot must start the scheduler; this is the defect "
        "documented at tui/app.py:466")


async def test_headless_boot_still_works(tmp_path):
    from aegis.cli import _serve
    from aegis.config.roots import AegisRoots
    stop = asyncio.Event()
    stop.set()
    await _serve(roots=AegisRoots.for_project(tmp_path), agents={},
                 default_agent="", make_session=lambda *a, **k: None,
                 mcp=_StubMCP(), stop=stop, ui=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_boot_unification.py -v`
Expected: FAIL — `_serve() got an unexpected keyword argument 'ui'`

- [ ] **Step 3: Define the attachment**

```python
from typing import Protocol


class UIAttachment(Protocol):
    """A front end bound to an already-booted brain. The brain wires every
    subsystem before this runs; the attachment only renders."""

    async def run(self, manager) -> None: ...


class LocalTuiAttachment:
    """The Textual TUI on this process's terminal.

    Uses `bridge=` (Task 7a), NOT `manager=` — the latter is the --remote
    path and would disable the local plane. Uses `run_async()`, because
    this runs inside _serve's loop where a blocking `.run()` deadlocks.
    """

    def __init__(self, *, clean: bool, agent: str | None, queues: dict,
                 voice, hosts: dict, host_registry, drivers: dict,
                 cwd: str, agents: dict, roots) -> None:
        self._kw = dict(clean=clean, queues=queues, voice=voice,
                        hosts=hosts, host_registry=host_registry,
                        drivers=drivers, cwd=cwd, agents=agents)
        self._agent = agent
        self._roots = roots

    async def run(self, manager) -> None:
        # Workspace resume is a VIEW concern and a cwd site; it belongs
        # here, rooted, not in the shared boot path.
        from aegis.tui.app import pick_workspace_to_resume
        from aegis.state.workspace import CorruptWorkspace
        try:
            pick_workspace_to_resume(self._roots.state_dir,
                                     clean=self._kw["clean"])
        except CorruptWorkspace as e:
            raise typer.Exit(code=2) from e

        app = AegisApp(default_agent=self._agent or "", make_session=None,
                       mcp=None, bridge=manager, **self._kw)
        await app.run_async()
```

**Pass the real `agents` dict.** `_run_tui_with_manager:302` uses
`{slug: None}` because a `--remote` client has no local `Agent` objects.
Locally those objects are used at `app.py:642` (`drv.resume`), `:824`
(`_resolve_place`), `:1120`, and `:2354`/`:2402` (`_overlay_agent`, the
per-session model override). `None` breaks all four. Likewise pass `hosts`
and `host_registry`, which `cli.py:226-227` passes today.

- [ ] **Step 4: Accept and run the attachment in `_serve`**

After every subsystem is wired, and before awaiting `stop`:

```python
    if ui is not None:
        await ui.run(mgr)
        stop.set()
    else:
        await stop.wait()
```

- [ ] **Step 5: Route `aegis` through `_serve`, keeping bootstrap mode**

Replace the direct `AegisApp(...)` at `cli.py:224` with a `_run_serve`-style
call passing `ui=LocalTuiAttachment(...)`.

**Preserve bootstrap mode.** `cli.py:163-168` currently drops into the TUI
`ConfigPanel` when there is **no** `.aegis.yaml`. `load_boot_config` raises
`ConfigError`, so routing naively would make `aegis` in a fresh directory
exit 1 — a behaviour change the Global Constraints forbid. Keep the
pre-check:

```python
    root = find_project_root() or Path.cwd()
    if not (root / ".aegis.yaml").is_file():
        # Bootstrap: no config anywhere → straight to the ConfigPanel,
        # exactly as before. No brain to boot yet.
        _run_bootstrap_tui(root, clean=clean)
        return
```

Delete only the duplicated **config loading** at `cli.py:169-197`. Do not
delete `pick_workspace_to_resume` (`:198-205`) — it moves into
`LocalTuiAttachment.run` above, with its `CorruptWorkspace` → `Exit(2)`
contract intact.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/cli/test_boot_unification.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Exercise the three live paths**

Unit tests do not cover the boot path every user runs. All three, reading
the rc directly:

```bash
# 1. normal boot
cd /tmp && rm -rf bootchk && mkdir bootchk && cd bootchk
printf 'agents:\n  a:\n    provider: claude-code\n    model: opus\ndefault_agent: a\n' > .aegis.yaml
timeout 10 uv run --project /home/apiad/Workspace/repos/aegis aegis --clean; echo "normal rc=$?"

# 2. bootstrap mode: NO .aegis.yaml must still open the ConfigPanel, not exit 1
cd /tmp && rm -rf bootstrapchk && mkdir bootstrapchk && cd bootstrapchk
timeout 10 uv run --project /home/apiad/Workspace/repos/aegis aegis --clean; echo "bootstrap rc=$?"

# 3. corrupt workspace must still exit 2
cd /tmp/bootchk && mkdir -p .aegis/state && echo 'not json' > .aegis/state/workspace.json
uv run --project /home/apiad/Workspace/repos/aegis aegis; echo "corrupt rc=$?"
```

Expected: `normal rc=124` (timeout, i.e. it ran), `bootstrap rc=124` (**not**
1), `corrupt rc=2`.

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS except the 1–2 known inotify flakes.

- [ ] **Step 9: Commit**

```bash
git add src/aegis/cli.py tests/cli/test_boot_unification.py
git commit -m "refactor(cli): one boot path, with the UI as an attachment

aegis and aegis serve had separate boot sequences; the TUI never wired a
scheduler or the peer remote plane. Both now boot through _serve, which
takes an optional UIAttachment. Bootstrap mode and workspace resume move
into the attachment rather than being deleted."
```

---

### Task 8: Config errors raise instead of exiting

**Files:**
- Modify: `src/aegis/cli.py:166-175`, `:184-187`, `:713-740`
- Test: `tests/cli/test_config_errors_raise.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `load_boot_config(roots: AegisRoots) -> BootConfig` raising `ConfigError`. The CLI wrappers catch it and call `typer.Exit`; library callers see the exception.

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_config_errors_raise.py
import pytest

from aegis.config import ConfigError
from aegis.config.roots import AegisRoots


def test_bad_config_raises_rather_than_exiting(tmp_path):
    """A library caller must not have the process exited out from under it."""
    (tmp_path / ".aegis.yaml").write_text("agents: [this is not a mapping\n",
                                          encoding="utf-8")
    from aegis.cli import load_boot_config
    with pytest.raises(ConfigError):
        load_boot_config(AegisRoots.for_project(tmp_path))


def test_unknown_default_agent_raises(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  a:\n    provider: claude-code\n    model: opus\n"
        "default_agent: nonexistent\n", encoding="utf-8")
    from aegis.cli import load_boot_config
    with pytest.raises(ConfigError):
        load_boot_config(AegisRoots.for_project(tmp_path))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_config_errors_raise.py -v`
Expected: FAIL — `load_boot_config` does not exist

- [ ] **Step 3: Extract the loader**

```python
@dataclass(frozen=True)
class BootConfig:
    agents: dict
    default_agent: str
    queues: dict
    schedules: dict
    remotes: dict
    remote_plane: object | None
    hosts: dict
    voice: object | None
    web: object | None            # cli.py:737 — _serve starts WebFrontend
    inline_schedule_names: set[str]


def load_boot_config(roots: AegisRoots) -> BootConfig:
    """Every entry point's config load. Raises ConfigError; only the CLI
    wrappers turn that into an exit code."""
    from aegis.commands.prompt_loader import load_prompt_commands
    from aegis.config.yaml_loader import import_plugins, load_config as _load
    agents, default_agent = load_config(roots.config_root)
    yaml_cfg = _load(roots.config_root)
    import_plugins(yaml_cfg)
    load_prompt_commands(roots.config_root)      # cli.py:732
    return BootConfig(
        agents=agents, default_agent=default_agent,
        queues=load_queues(roots.config_root),
        schedules=yaml_cfg.schedules, remotes=yaml_cfg.remotes,
        remote_plane=yaml_cfg.remote_plane, hosts=yaml_cfg.hosts,
        voice=yaml_cfg.voice,
        # cli.py:737 — only a token-bearing block counts, or serve starts a
        # web frontend with no auth.
        web=(yaml_cfg.web if (yaml_cfg.web and yaml_cfg.web.token) else None),
        inline_schedule_names=yaml_cfg.inline_schedule_names)
```

Replace the three guard blocks with calls to it, wrapping in
`try/except ConfigError` → `_console.print` + `typer.Exit(1)` **only** in the
typer command bodies.

- [ ] **Step 4: Verify, including that the web UI still boots**

`BootConfig` must carry `web`, or this task silently kills the web frontend
in a plan whose contract is "the web client is untouched".

```python
# append to tests/cli/test_config_errors_raise.py
def test_boot_config_carries_web_and_is_token_gated(tmp_path):
    from aegis.cli import load_boot_config
    from aegis.config.roots import AegisRoots

    base = ("agents:\n  a:\n    provider: claude-code\n    model: opus\n"
            "default_agent: a\n")
    (tmp_path / ".aegis.yaml").write_text(
        base + "web:\n  bind: 127.0.0.1\n  port: 8899\n  token: secret\n",
        encoding="utf-8")
    assert load_boot_config(AegisRoots.for_project(tmp_path)).web is not None

    # A token-less block must stay None, or serve exposes an unauthed UI.
    (tmp_path / ".aegis.yaml").write_text(
        base + "web:\n  bind: 127.0.0.1\n  port: 8899\n", encoding="utf-8")
    assert load_boot_config(AegisRoots.for_project(tmp_path)).web is None
```

Run: `uv run pytest tests/cli/test_config_errors_raise.py tests/ -k "config or cli or web" -q`
Expected: PASS

- [ ] **Step 5: Confirm the CLI still exits nonzero on bad config**

The point is to move the exit, not remove it.

Run: `cd /tmp && mkdir -p badcfg && cd badcfg && printf 'agents: [broken\n' > .aegis.yaml && uv run --project /home/apiad/Workspace/repos/aegis aegis --clean; echo "rc=$?"`
Expected: a red error line and `rc=1`. **Read the rc directly — do not pipe.**

- [ ] **Step 6: Commit**

```bash
git add src/aegis/cli.py tests/cli/test_config_errors_raise.py
git commit -m "refactor(cli): raise ConfigError from the boot loader, exit only in commands"
```

---

### Task 9: `aegis.embed()` — the library seam

**Files:**
- Create: `src/aegis/embed.py`
- Modify: `src/aegis/__init__.py`
- Test: `tests/test_multi_instance.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `aegis.embed(root, *, harness_cwd=None) -> AsyncContextManager[EmbeddedAegis]`, where `EmbeddedAegis` exposes `.manager`, `.queues`, `.roots`. Does **not** call `asyncio.run` and installs **no** signal handlers.

- [ ] **Step 1: Write the failing test — the spec's gate**

```python
# tests/test_multi_instance.py
"""The gate for the whole plan. Asserts on a config *write*, not on path
disjointness: paths differ as soon as roots are threaded, while the
late-bound MCP lookups can still resolve through the process cwd."""
from pathlib import Path

CONFIG = """\
agents:
  opus:
    provider: claude-code
    model: opus
default_agent: opus
"""


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / ".aegis.yaml").write_text(CONFIG, encoding="utf-8")
    return root


async def test_two_instances_do_not_share_state(tmp_path):
    import aegis

    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    async with aegis.embed(a) as ae_a, aegis.embed(b) as ae_b:
        assert ae_a.manager is not ae_b.manager
        assert ae_a.roots.state_dir != ae_b.roots.state_dir
        assert ae_a.roots.state_dir.is_relative_to(a)
        assert ae_b.roots.state_dir.is_relative_to(b)


async def test_a_config_write_in_one_instance_does_not_touch_the_other(tmp_path):
    """End-to-end version of the Task 4 gate: the tool is reached through
    a real embedded instance's own MCP server, not a stub bridge."""
    import aegis

    from tests.test_mcp_config_tools import _call

    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    before_b = (b / ".aegis.yaml").read_text(encoding="utf-8")

    async with aegis.embed(a) as ae_a, aegis.embed(b):
        await _call(ae_a.mcp.server, "aegis_config_add_agent",
                    slug="sonnet", harness="claude-code", model="sonnet")

    assert (b / ".aegis.yaml").read_text(encoding="utf-8") == before_b
    assert "sonnet" in (a / ".aegis.yaml").read_text(encoding="utf-8")


async def test_embed_installs_no_signal_handlers(tmp_path):
    """An embedded aegis runs in the host's loop and must not touch its
    signals — sindri owns SIGINT/SIGTERM."""
    import signal
    import aegis

    before = signal.getsignal(signal.SIGINT)
    async with aegis.embed(_project(tmp_path, "a")):
        assert signal.getsignal(signal.SIGINT) is before
    assert signal.getsignal(signal.SIGINT) is before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_multi_instance.py -v`
Expected: FAIL — `module 'aegis' has no attribute 'embed'`

- [ ] **Step 3: Implement `embed`**

```python
# src/aegis/embed.py
"""Boot aegis in-process, at an explicit root, without owning the loop.

The library seam sindri drives. Unlike the CLI entry points this installs no
signal handlers and never calls asyncio.run — the host owns both.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from aegis.config.roots import AegisRoots


@dataclass
class EmbeddedAegis:
    manager: object
    queues: object
    roots: AegisRoots
    mcp: object             # the instance's AegisMCP; .server is its FastMCP


@asynccontextmanager
async def embed(root: Path | str, *, harness_cwd: Path | str | None = None):
    """Yield a booted aegis rooted at `root`. Several may coexist."""
    from aegis.cli import _serve, load_boot_config, _session_factory
    from aegis.hosts.registry import HostRegistry
    from aegis.mcp import AegisMCP

    roots = AegisRoots.for_project(Path(root), harness_cwd=harness_cwd)
    cfg = load_boot_config(roots)
    host_registry = HostRegistry(cfg.hosts, state_dir=roots.state_dir,
                                 local_root=str(roots.harness_cwd))
    stop = asyncio.Event()
    holder: dict = {}
    mcp = AegisMCP()

    class _Capture:
        async def run(self, manager) -> None:
            holder["manager"] = manager
            await stop.wait()

    task = asyncio.create_task(_serve(
        roots=roots, agents=cfg.agents, default_agent=cfg.default_agent,
        make_session=_session_factory(str(roots.harness_cwd), host_registry),
        mcp=mcp, stop=stop, queues=cfg.queues,
        schedules=cfg.schedules, remotes=cfg.remotes,
        remote_plane=cfg.remote_plane, hosts=cfg.hosts,
        host_registry=host_registry,
        inline_schedule_names=cfg.inline_schedule_names,
        ui=_Capture()))
    try:
        while "manager" not in holder and not task.done():
            await asyncio.sleep(0.01)
        if task.done():
            task.result()          # re-raise a boot failure
        mgr = holder["manager"]
        yield EmbeddedAegis(manager=mgr, queues=mgr.queue_manager,
                            roots=roots, mcp=mcp)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=10)
```

- [ ] **Step 4: Expose the FastMCP handle publicly**

`AegisMCP` builds its `FastMCP` in `bind()` and keeps it private
(`mcp/runtime.py:44,60`). Embedded callers — and the gate test — need it, and
reaching into `_server` from a test would pin a private. Add the accessor:

```python
    # src/aegis/mcp/runtime.py, on AegisMCP
    @property
    def server(self):
        """The bound FastMCP. None until bind() has run."""
        return self._server
```

- [ ] **Step 5: Export it**

```python
# src/aegis/__init__.py — add
from aegis.embed import EmbeddedAegis, embed  # noqa: F401
```

- [ ] **Step 6: Run the gate**

Run: `uv run pytest tests/test_multi_instance.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Mutation-check the gate**

A gate that cannot fail is worth less than none. Temporarily revert one
`roots.config_root` lookup in `mcp/server.py` back to `find_project_root()`,
re-run, and confirm
`test_a_config_write_in_one_instance_does_not_touch_the_other` goes **red**.
Then restore.

Run: `uv run pytest tests/test_multi_instance.py -v`
Expected: RED while mutated, PASS after restoring. If it stays green while
mutated, the test is a proxy — fix the test before continuing.

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS except the known inotify flakes.

- [ ] **Step 9: Commit**

```bash
git add src/aegis/embed.py src/aegis/__init__.py tests/test_multi_instance.py
git commit -m "feat(embed): boot aegis in-process at an explicit root

aegis.embed() yields a booted brain without owning the event loop or
installing signal handlers. Several instances coexist in one process with
disjoint state and no config cross-talk."
```

---

### Task 10: Documentation

**Files:**
- Modify: `AGENTS.md`, `docs/api.md`, `README.md`
- Create: `know-how/embedding-aegis.md`

**Interfaces:**
- Consumes: `aegis.embed()`.
- Produces: no code.

- [ ] **Step 1: Write the know-how doc**

Create `know-how/embedding-aegis.md` covering: the three roots and which
anchors what; that `embed()` owns neither the loop nor signals; the
multi-instance contract; and the one trap — resolving anything from
`Path.cwd()` inside an embedded instance is a bug, because the process cwd
belongs to the host.

- [ ] **Step 2: Add its index entry to `AGENTS.md`**

```markdown
- `know-how/embedding-aegis.md` — *reach for it when driving aegis as a
  library (`aegis.embed()`), or when touching anything that resolves a path
  — the three roots replaced `Path.cwd()` and must stay threaded.*
```

- [ ] **Step 3: Update the layout section of `AGENTS.md`**

Add `src/aegis/config/roots.py` and `src/aegis/embed.py`, and correct the
`cli.py` description to say `_serve` is the single boot path with an optional
UI attachment.

- [ ] **Step 4: Verify every path named in the docs exists**

Run: `uv run python -c "
import re, pathlib
for doc in ['AGENTS.md', 'know-how/embedding-aegis.md']:
    for m in re.findall(r'\`(src/[^\`]+|know-how/[^\`]+)\`', pathlib.Path(doc).read_text()):
        p = pathlib.Path(m.split(':')[0])
        print(('OK  ' if p.exists() else 'MISS'), m)
" | grep MISS || echo "all documented paths exist"`
Expected: `all documented paths exist`

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md docs/api.md README.md know-how/embedding-aegis.md
git commit -m "docs: the three roots and embedding aegis as a library"
```

---

## Done when

- `uv run pytest -q` passes but for the known inotify flakes.
- `tests/test_multi_instance.py` passes, and fails when mutated.
- `tests/test_no_cwd_regression.py` covers all six cleaned modules.
- `aegis` still launches, and now fires schedules.
- `aegis serve`, the web client and `--remote` are untouched — this plan deletes nothing.

## Deliberately not in this plan

Stages 4–6 of the spec: the view seam (N `AegisApp` instances over one brain), the daemon transports (unix socket + WS), `aegis attach`, the auth change, and the deletion of the web client, WS plane and `--remote`. Those need their own plan, and this one must land first.
