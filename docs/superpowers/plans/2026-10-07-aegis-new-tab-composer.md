# New-tab composer and agents as presets: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the `#new` view into an OpenCode-style composer whose Enter spawns and sends in one call, make an agent a preset that any spawn can override (people and agents alike), and make every field of `.aegis.yaml` explicit.

**Architecture:** A new `agents.py` replaces `profiles.py`: it reads agents strictly and resolves a spawn (agent plus overrides) into a `SpawnSpec`. `SpawnSpec` gains harness, priming, overridden fields and the spawning agent; the session records them in its spawn record and meta, so the priming survives a resume without reading the YAML again. `session.spawn` and the new `agents.list` are registry operations marked for agents, so the browser and MCP share them. The client replaces the form with a composer of native `<select>` and `<input list>` chips.

**Tech Stack:** Python 3.13, pydantic, ruamel.yaml, Starlette/uvicorn, fastmcp; plain ES modules client; pytest, pytest-asyncio, Playwright (headless Chromium); the fake claude in `tests/fake_claude.py`.

**Spec:** `docs/superpowers/specs/2026-10-07-aegis-new-tab-composer-design.md` (issue #155). Read it before Task 1.

## Global Constraints

- Work in the worktree `.claude/worktrees/new-tab-composer` on branch `feat/new-tab-composer`; never commit to `main`. Stage named paths only.
- Conventional commits in English, each ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Use `git commit -F -` with a heredoc when the message has backticks.
- Imports inside `src/aegis` are relative; nothing imports `legacy/` (`tests/test_imports.py`). Nothing calls `Path.cwd()` (`tests/test_no_cwd.py`).
- Nothing in `.aegis.yaml` is a default. An agent names `harness` (flat, `provider:` string or nested `provider:` mapping), `model`, `effort`, `permission`; `priming:` is the one optional key. A queue names `agent` and a positive integer `max_parallel`.
- Error codes, exactly: `no_agent`, `unknown_agent`, `bad_agent`, `harness_unsupported`, `bad_cwd`, `bad_config`, `bad_params`, `claude_not_found`, `send_failed`.
- Harnesses: `HARNESSES = ("claude-code", "opencode")`, `SUPPORTED_HARNESSES = ("claude-code",)`. Model aliases for `claude-code`: `("opus", "sonnet", "haiku", "fable")` (checked against `claude --help`).
- The priming text never crosses the wire: `agents.list` sends `has_priming`, and `Session.wire()` drops `priming`.
- Iterate with the narrowest test file named in each step (`uv run pytest -q tests/<file>.py`). The full gates (`make check`, browser, live, bench) run once, in Task 6.

## Review Focus

- A `model: ""` (or any field set to an empty string) in `.aegis.yaml` must count as missing, not as "use the CLI's model". Pinned in Task 2.
- Shift+Enter in the composer must add a line and never spawn; Enter during IME composition must not spawn. Pinned in Task 5.
- A relative `cwd` passed by an agent resolves against the calling session's cwd, not the server root. Pinned in Task 3.
- A session stored before this change (meta and spawn record with `profile`, no `harness`) must boot, show its agent name and resume. Pinned in Task 1.
- A queue whose YAML lost a field after tasks were logged must not crash the dispatcher with a `KeyError`. Pinned in Task 4.

---

### Task 1: The spec carries agent, harness and priming, and the priming reaches every start

**Files:**
- Modify: `src/aegis/session.py` (`SpawnSpec` at line 75; `Session.meta` at ~147; `Session.wire` at ~173; `Session.start` at ~209)
- Modify: `src/aegis/registry.py` (`spawn_args` at 61; `_session` at 112; `spawn` at 204; archive search at ~306)
- Modify: `src/aegis/meta.py` (`rebuild`, the returned dict at ~113)
- Modify: `src/aegis/transcript/entries.py:156-161` (the `spawn` summary)
- Modify: `tests/fake_claude.py` (new `/argv` script)
- Modify: `tests/conftest.py` (new `argv_of` helper)
- Test: `tests/test_registry.py`, `tests/test_meta.py`

**Interfaces:**
- Produces: `SpawnSpec(agent, model, effort, permission, cwd, harness="claude-code", priming=None, overridden=(), spawned_by=None)`; `SpawnSpec.record() -> dict`; `SpawnSpec.from_record(d: dict, cwd_default: Path) -> SpawnSpec`; `Registry.spawn_args` returns `(mcp_config | None, system_prompt | None)` where the system prompt is aegis's primer, then `"\n\n"`, then the priming. Meta and wire keys `agent`, `harness`, and when set `overridden`, `spawned_by`; meta (not wire) key `priming`. Fake script `/argv`; `tests.conftest.argv_of(session) -> list[str]`.

- [x] **Step 1: Add `/argv` to the fake claude**

In `tests/fake_claude.py`, add a line to the docstring's script list after `/recall`:

```
    /argv          text "argv: <JSON of the process's argv after the binary>".
```

and a branch in `run()` before `elif word == "/recall":`:

```python
    elif word == "/argv":
        assistant({"type": "text", "text": "argv: " + json.dumps(sys.argv[1:])})
        result()
```

- [x] **Step 2: Add `argv_of` to `tests/conftest.py`**

Add `import json` to the imports and, after `until`:

```python
async def argv_of(s) -> list[str]:
    """The argv the session's current claude process was started with, as the
    fake reports it for ``/argv``."""

    def prose() -> list[str]:
        return [e["md"] for e in s.entries() if e["kind"] == "prose"]

    before = len(prose())
    await s.send("/argv")
    await until(
        lambda: s.status == "idle" and len(prose()) > before,
        timeout=8,
        what="the /argv turn",
    )
    return json.loads(prose()[-1].removeprefix("argv: "))
```

- [x] **Step 3: Write the failing tests in `tests/test_registry.py`**

Add `argv_of` to the import from `.conftest`, then:

```python
async def test_the_priming_reaches_every_start_from_the_record_not_the_config(world):
    # The World's .aegis.yaml has no agents at all: a resume that read the
    # priming from the config would lose it.
    r = world.registry()
    spec = SpawnSpec(
        "rev", "opus", "max", "read", world.roots.config_root, priming="You review."
    )
    s = await r.spawn(spec)
    argv = await argv_of(s)
    assert argv[argv.index("--append-system-prompt") + 1] == "You review."
    await r.shutdown()
    r2 = world.registry()
    (s2,) = r2.open_sessions()
    assert s2.spec.priming == "You review."
    argv = await argv_of(s2)
    assert "--resume" in argv
    assert argv[argv.index("--append-system-prompt") + 1] == "You review."
    await r2.shutdown()


async def test_no_priming_and_no_mcp_means_no_system_prompt(world):
    r = world.registry()
    s = await r.spawn(world.spec())
    assert "--append-system-prompt" not in await argv_of(s)
    await r.shutdown()


async def test_a_spawn_records_its_agent_overrides_and_spawner(world):
    r = world.registry()
    spec = SpawnSpec(
        "opus",
        "sonnet",
        "high",
        "full",
        world.roots.config_root,
        overridden=("model",),
        spawned_by="parent-log",
        priming="secret text",
    )
    s = await r.spawn(spec)
    w = s.wire()
    assert (w["agent"], w["harness"], w["overridden"], w["spawned_by"]) == (
        "opus",
        "claude-code",
        ["model"],
        "parent-log",
    )
    assert "priming" not in w and s.meta()["priming"] == "secret text"
    assert s.entries()[0]["summary"].startswith("spawned opus* · sonnet")
    await r.shutdown()


async def test_a_meta_from_before_agents_boots_with_its_agent_name(world):
    sessions = world.roots.state_root / "sessions"
    sessions.mkdir(parents=True)
    old = {
        "log_id": "old",
        "handle": "old-one",
        "created_at": 1,
        "last_activity": 1,
        "profile": "opus",
        "model": "opus",
        "effort": "high",
        "permission": "full",
        "cwd": str(world.roots.config_root),
    }
    (sessions / "old.json").write_text(json.dumps(old))
    r = world.registry()
    (s,) = r.open_sessions()
    assert (s.spec.agent, s.spec.harness, s.spec.priming) == (
        "opus",
        "claude-code",
        None,
    )
    assert s.wire()["agent"] == "opus"
    # Closed, it is found in the archive by its agent's name (Step 9).
    await r.close("old")
    assert [m["log_id"] for m in r.archive("opus", 10, None)] == ["old"]
```

- [x] **Step 4: Update `tests/test_meta.py` for the renamed key**

In the rebuild test (~line 74) the store still writes `"profile": "opus"`, which is what an old store holds. Change the assertion to read the new key:

```python
    assert (m["agent"], m["cwd"], m["claude_session_id"], m["title"]) == (
        "opus",
        "/w",
        "cs-1",
        "fix the flaky test",
    )
    assert m["harness"] is None
```

- [x] **Step 5: Run the tests to see them fail**

Run: `uv run pytest -q tests/test_registry.py tests/test_meta.py`
Expected: FAIL (`SpawnSpec` has no `priming`; `KeyError: 'agent'`).

- [x] **Step 6: Extend `SpawnSpec` in `src/aegis/session.py`**

Replace the dataclass at line 74-80 with:

```python
@dataclass(frozen=True)
class SpawnSpec:
    """What a session's process runs with, fixed at spawn and recorded in the
    spawn record and the meta. A resume reads it from there, never from
    ``.aegis.yaml``: Claude Code does not keep the system prompt in its own
    session file, so editing an agent must not change its old sessions."""

    agent: str
    model: str
    effort: str
    permission: str
    cwd: Path
    harness: str = "claude-code"
    priming: str | None = None
    # The fields this spawn changed from its agent, such as ("model",).
    overridden: tuple[str, ...] = ()
    # The log id of the agent session that spawned this one.
    spawned_by: str | None = None

    def record(self) -> dict:
        """The fields a spawn record and a meta carry."""
        d: dict = {
            "agent": self.agent,
            "harness": self.harness,
            "model": self.model,
            "effort": self.effort,
            "permission": self.permission,
            "cwd": str(self.cwd),
        }
        if self.priming:
            d["priming"] = self.priming
        if self.overridden:
            d["overridden"] = list(self.overridden)
        if self.spawned_by:
            d["spawned_by"] = self.spawned_by
        return d

    @classmethod
    def from_record(cls, d: dict, cwd_default: Path) -> "SpawnSpec":
        # Records from before #155 carry `profile` and no harness; every one of
        # them ran Claude Code.
        return cls(
            agent=str(d.get("agent") or d.get("profile") or ""),
            model=str(d.get("model") or ""),
            effort=str(d.get("effort") or "high"),
            permission=str(d.get("permission") or "auto"),
            cwd=Path(d.get("cwd") or cwd_default),
            harness=str(d.get("harness") or "claude-code"),
            priming=d.get("priming") or None,
            overridden=tuple(d.get("overridden") or ()),
            spawned_by=d.get("spawned_by"),
        )
```

- [x] **Step 7: Record the spec in `Session.start`, `Session.meta` and `Session.wire`**

In `start()`, replace the dict passed to `self._record` with:

```python
        self._record({"kind": "spawn", **self.spec.record()})
```

and drop the now-unused `s = self.spec` line in `start()`.

In `meta()`, replace the five lines `"profile": s.profile,` … `"cwd": str(s.cwd),` (keeping `"model_id": self.model_id,`) so the dict reads:

```python
        return {
            "log_id": self.log_id,
            "handle": self.handle,
            "title": self.title,
            **s.record(),
            "model_id": self.model_id,
            "claude_session_id": self.claude_session_id,
            "archived": self.archived,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "last_status": self.status if self._proc else self.last_status,
            "cost_usd": self.cost_usd,
            "context_tokens": self.context_tokens,
            "context_window": self.context_window,
            "activity": self.activity,
            "held": self.held,
            "worker": self.worker,
        }
```

In `wire()`, after `m.pop("held")`, add:

```python
        m.pop("priming", None)  # the agent's text stays on the server
```

- [x] **Step 8: Pass the priming in `Registry.spawn_args`**

Replace `spawn_args` in `src/aegis/registry.py`:

```python
    def spawn_args(self, session: Session) -> tuple[str | None, str | None]:
        priming = session.spec.priming
        if self.mcp_url is None or self.tokens is None:
            return None, priming
        from .mcp import mcp_config, primer

        prompt = primer(session, self.server_name)
        if priming:
            prompt += "\n\n" + priming
        return mcp_config(self.mcp_url, self.tokens.mint(session.log_id)), prompt
```

- [x] **Step 9: Build and spawn sessions from the record in the registry**

In `_session`, replace the `spec=SpawnSpec(...)` argument with:

```python
            spec=SpawnSpec.from_record(meta, self.roots.harness_cwd),
```

In `spawn`, replace the dict merged into the new meta with:

```python
            {"log_id": log_id, "handle": mint_handle(self._handles())}
            | spec.record()
            | {"created_at": time.time(), "worker": worker, "title": title}
```

In the archive search, change the key tuple to `("title", "handle", "cwd", "agent", "profile")` so old metas stay searchable by name.

- [x] **Step 10: Rebuild metas and summaries with the new keys**

In `src/aegis/meta.py` `rebuild`, replace `"profile": spawn.get("profile"),` with:

```python
        "agent": spawn.get("agent") or spawn.get("profile"),
        "harness": spawn.get("harness"),
        "priming": spawn.get("priming"),
        "overridden": spawn.get("overridden"),
        "spawned_by": spawn.get("spawned_by"),
```

In `src/aegis/transcript/entries.py`, replace the `spawn` summary line with:

```python
            agent = rec.get("agent") or rec.get("profile")
            star = "*" if rec.get("overridden") else ""
            line = f"spawned {agent}{star} · {rec.get('model')} · {rec.get('cwd')}"
```

- [x] **Step 11: Run the tests to see them pass**

Run: `uv run pytest -q tests/test_registry.py tests/test_meta.py tests/test_session.py tests/test_fold.py`
Expected: PASS.

- [x] **Step 12: Commit**

```bash
git add src/aegis/session.py src/aegis/registry.py src/aegis/meta.py src/aegis/transcript/entries.py tests/fake_claude.py tests/conftest.py tests/test_registry.py tests/test_meta.py
git commit -F - <<'EOF'
feat(session): a spawn records its agent, harness and priming (#155)

The priming is appended to aegis's primer on every start, read from the
spawn record so a resume never depends on the current .aegis.yaml.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 2: `agents.py`, strict presets and spawn resolution

**Files:**
- Create: `src/aegis/agents.py`
- Modify: `src/aegis/claude/process.py` (receives `PERMISSION_MODE`)
- Modify: `src/aegis/profiles.py` (drop `PERMISSION_MODE`; the module is deleted in Task 4)
- Create: `tests/test_agent_config.py`

**Interfaces:**
- Consumes: `SpawnSpec` from Task 1.
- Produces: `aegis.agents.Agent` (fields `name, harness, model, effort, permission, priming, error`; property `enabled`; `as_dict()` with keys `name, harness, model, effort, permission, enabled, error, has_priming`); `ConfigError`; `read_config(config_root) -> dict`; `load_agents(config_root) -> list[Agent]`; `default_agent(config_root) -> str | None`; `model_suggestions(agents) -> dict[str, list[str]]`; `resolve(agents, default, name, overrides, cwd, spawned_by=None) -> SpawnSpec` raising `OpError` with codes `no_agent`, `unknown_agent`, `bad_agent`, `harness_unsupported`; constants `EFFORTS`, `HARNESSES`, `SUPPORTED_HARNESSES`, `MODEL_ALIASES`, `FIELDS`.

- [x] **Step 1: Move `PERMISSION_MODE` into `claude/process.py`**

In `src/aegis/claude/process.py`, replace `from ..profiles import PERMISSION_MODE` with the definition:

```python
# aegis's permission vocabulary, mapped to Claude Code's --permission-mode.
PERMISSION_MODE = {
    "read": "plan",
    "write": "acceptEdits",
    "full": "bypassPermissions",
    "auto": "auto",
}
```

In `src/aegis/profiles.py`, delete the `PERMISSION_MODE` dict and its comment (nothing in that module uses it).

- [x] **Step 2: Write the failing tests in `tests/test_agent_config.py`**

```python
from pathlib import Path

import pytest

from aegis.agents import ConfigError, load_agents, model_suggestions, resolve
from aegis.ops import OpError


def write(tmp_path: Path, text: str) -> Path:
    (tmp_path / ".aegis.yaml").write_text(text)
    return tmp_path


def by_name(root: Path) -> dict:
    return {a.name: a for a in load_agents(root)}


def test_the_three_harness_forms_parse(tmp_path):
    agents = by_name(
        write(
            tmp_path,
            "agents:\n"
            "  flat: {harness: claude-code, model: opus, effort: high, permission: full}\n"
            "  str: {provider: claude-code, model: opus, effort: high, permission: full}\n"
            "  nested:\n"
            "    provider: {name: claude-code, model: haiku, effort: low, permission: read}\n",
        )
    )
    assert all(a.enabled and a.error is None for a in agents.values())
    n = agents["nested"]
    assert (n.harness, n.model, n.effort, n.permission) == (
        "claude-code",
        "haiku",
        "low",
        "read",
    )


def test_a_missing_field_disables_only_that_agent(tmp_path):
    agents = by_name(
        write(
            tmp_path,
            "agents:\n"
            "  ok: {harness: claude-code, model: opus, effort: high, permission: full}\n"
            "  noeffort: {harness: claude-code, model: opus, permission: full}\n"
            "  bare: {}\n",
        )
    )
    assert agents["ok"].enabled
    assert agents["noeffort"].error == "effort is missing"
    assert not agents["noeffort"].enabled
    assert agents["bare"].error == "harness, model, effort, permission are missing"


def test_an_empty_string_counts_as_missing(tmp_path):
    (a,) = load_agents(
        write(
            tmp_path,
            'agents:\n  x: {harness: claude-code, model: "", effort: high, permission: full}\n',
        )
    )
    assert a.error == "model is missing"


def test_a_value_outside_the_vocabulary_is_an_error(tmp_path):
    (a,) = load_agents(
        write(
            tmp_path,
            "agents:\n  x: {harness: claude-code, model: opus, effort: huge, permission: full}\n",
        )
    )
    assert a.error == "effort 'huge' is not one of low, medium, high, max"


def test_an_unsupported_harness_is_disabled_but_not_an_error(tmp_path):
    (a,) = load_agents(
        write(
            tmp_path,
            "agents:\n  d: {provider: opencode, model: x, effort: high, permission: full}\n",
        )
    )
    assert a.error is None and not a.enabled


def test_priming_is_optional_and_never_listed(tmp_path):
    agents = by_name(
        write(
            tmp_path,
            "agents:\n"
            "  plain: {harness: claude-code, model: opus, effort: high, permission: full}\n"
            "  rev:\n"
            "    harness: claude-code\n    model: opus\n    effort: max\n    permission: read\n"
            "    priming: |\n      You review.\n      Rank by severity.\n",
        )
    )
    assert agents["plain"].priming is None
    assert agents["rev"].priming == "You review.\nRank by severity.\n"
    d = agents["rev"].as_dict()
    assert d["has_priming"] is True and "priming" not in d


def test_no_config_means_no_agents(tmp_path):
    assert load_agents(tmp_path) == []


def test_malformed_config_names_the_file(tmp_path):
    with pytest.raises(ConfigError, match=r"\.aegis\.yaml"):
        load_agents(write(tmp_path, "agents: [1, 2\n"))


def test_model_suggestions_put_aliases_first_without_duplicates(tmp_path):
    agents = load_agents(
        write(
            tmp_path,
            "agents:\n"
            "  a: {harness: claude-code, model: opus, effort: high, permission: full}\n"
            "  b: {harness: claude-code, model: claude-sonnet-5, effort: high, permission: full}\n"
            "  c: {harness: opencode, model: opencode-go/x, effort: high, permission: full}\n",
        )
    )
    assert model_suggestions(agents) == {
        "claude-code": ["opus", "sonnet", "haiku", "fable", "claude-sonnet-5"],
        "opencode": ["opencode-go/x"],
    }


@pytest.fixture
def agents(tmp_path):
    return load_agents(
        write(
            tmp_path,
            "agents:\n"
            "  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
            "  rev: {harness: claude-code, model: opus, effort: max, permission: read, priming: You review.}\n"
            "  broken: {harness: claude-code, model: opus, permission: full}\n"
            "  deep: {harness: opencode, model: x, effort: high, permission: full}\n",
        )
    )


def none() -> dict:
    return {"harness": None, "model": None, "effort": None, "permission": None}


def test_resolve_takes_the_agents_fields_and_its_priming(agents, tmp_path):
    spec = resolve(agents, None, "rev", none(), tmp_path)
    assert (spec.agent, spec.model, spec.effort, spec.permission) == (
        "rev",
        "opus",
        "max",
        "read",
    )
    assert (spec.harness, spec.priming, spec.overridden) == (
        "claude-code",
        "You review.",
        (),
    )


def test_an_override_replaces_a_field_and_is_recorded(agents, tmp_path):
    spec = resolve(
        agents,
        None,
        "opus",
        none() | {"model": "sonnet", "effort": "high"},
        tmp_path,
        spawned_by="p",
    )
    # effort equals the agent's value, so it is not an override.
    assert (spec.model, spec.overridden, spec.spawned_by) == ("sonnet", ("model",), "p")


def test_no_name_falls_back_to_default_agent(agents, tmp_path):
    assert resolve(agents, "rev", None, none(), tmp_path).agent == "rev"


@pytest.mark.parametrize(
    ("default", "name", "overrides", "code"),
    [
        (None, None, {}, "no_agent"),
        (None, "nope", {}, "unknown_agent"),
        (None, "broken", {}, "bad_agent"),
        ("broken", None, {}, "bad_agent"),
        (None, "deep", {}, "harness_unsupported"),
        (None, "opus", {"harness": "opencode"}, "harness_unsupported"),
    ],
)
def test_resolve_errors(agents, tmp_path, default, name, overrides, code):
    with pytest.raises(OpError) as e:
        resolve(agents, default, name, none() | overrides, tmp_path)
    assert e.value.code == code


def test_bad_agent_says_what_is_wrong(agents, tmp_path):
    with pytest.raises(OpError, match="agent 'broken': effort is missing"):
        resolve(agents, None, "broken", none(), tmp_path)
```

- [x] **Step 3: Run the tests to see them fail**

Run: `uv run pytest -q tests/test_agent_config.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.agents'`.

- [x] **Step 4: Write `src/aegis/agents.py`**

```python
"""Agents: named presets read from the ``agents:`` map of ``.aegis.yaml``.

An agent fixes a harness, a model, an effort, a permission and, optionally, a
priming prompt. A spawn starts from one agent and may override every field
but the priming (``resolve``); one-off instructions belong in the first
message.

Nothing in the file is a default. An agent that omits a field, or sets one to
an empty string, is listed with an ``error`` and cannot be spawned, and the
other agents in the file are unaffected. A field the loader filled in would be
a setting nobody chose, with nothing to show it was filled.

Three forms name the harness: flat ``harness:``, ``provider: <harness>`` as a
string (the Workspace's own form), and a nested ``provider:`` mapping whose
``name`` is the harness and whose other keys are fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML, YAMLError

from .claude.process import PERMISSION_MODE
from .ops import OpError
from .roots import CONFIG_FILE
from .session import SpawnSpec

EFFORTS = ("low", "medium", "high", "max")
HARNESSES = ("claude-code", "opencode")
SUPPORTED_HARNESSES = ("claude-code",)
# Claude Code's --model aliases (`claude --help`), offered before the models
# the agents name.
MODEL_ALIASES = {"claude-code": ("opus", "sonnet", "haiku", "fable")}
FIELDS = ("harness", "model", "effort", "permission")


class ConfigError(Exception):
    """``.aegis.yaml`` exists but cannot be parsed."""


@dataclass(frozen=True)
class Agent:
    name: str
    harness: str
    model: str
    effort: str
    permission: str
    priming: str | None = None
    # Why this agent cannot be spawned as written; None when it can.
    error: str | None = None

    @property
    def enabled(self) -> bool:
        return self.error is None and self.harness in SUPPORTED_HARNESSES

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "harness": self.harness,
            "model": self.model,
            "effort": self.effort,
            "permission": self.permission,
            "enabled": self.enabled,
            "error": self.error,
            "has_priming": bool(self.priming),
        }


def _invalid(field: str, value: str) -> str | None:
    if field == "effort" and value not in EFFORTS:
        return f"effort {value!r} is not one of {', '.join(EFFORTS)}"
    if field == "permission" and value not in PERMISSION_MODE:
        return f"permission {value!r} is not one of {', '.join(PERMISSION_MODE)}"
    return None


def _agent(name: str, raw: Any) -> Agent:
    d: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    provider = d.pop("provider", None)
    if isinstance(provider, str):
        d["harness"] = provider
    elif isinstance(provider, dict):
        d |= {k: v for k, v in provider.items() if k != "name"}
        if provider.get("name"):
            d["harness"] = provider["name"]
    values = {f: "" if d.get(f) in (None, "") else str(d[f]) for f in FIELDS}
    missing = [f for f in FIELDS if not values[f]]
    if missing:
        verb = "is" if len(missing) == 1 else "are"
        error: str | None = f"{', '.join(missing)} {verb} missing"
    else:
        error = next((e for f in FIELDS if (e := _invalid(f, values[f]))), None)
    priming = d.get("priming")
    return Agent(
        name=str(name),
        **values,
        priming=str(priming) if priming else None,
        error=error,
    )


def read_config(config_root: Path) -> dict:
    path = config_root / CONFIG_FILE
    if not path.is_file():
        return {}
    try:
        data = YAML(typ="safe").load(path.read_text())
    except YAMLError as e:
        raise ConfigError(f"{path}: {e}") from e
    return data if isinstance(data, dict) else {}


def load_agents(config_root: Path) -> list[Agent]:
    agents = read_config(config_root).get("agents")
    if not isinstance(agents, dict):
        return []
    return [_agent(name, raw) for name, raw in agents.items()]


def default_agent(config_root: Path) -> str | None:
    name = read_config(config_root).get("default_agent")
    return str(name) if name else None


def model_suggestions(agents: list[Agent]) -> dict[str, list[str]]:
    """Per harness, what the model chip offers: the CLI's aliases, then every
    model an agent of that harness names."""
    out: dict[str, list[str]] = {}
    for h in HARNESSES:
        models = list(MODEL_ALIASES.get(h, ()))
        for a in agents:
            if a.harness == h and a.model and a.model not in models:
                models.append(a.model)
        out[h] = models
    return out


def resolve(
    agents: list[Agent],
    default: str | None,
    name: str | None,
    overrides: dict[str, str | None],
    cwd: Path,
    spawned_by: str | None = None,
) -> SpawnSpec:
    """The spec a spawn runs: the agent's fields, each replaced by its override
    when one is given. The priming is always the agent's."""
    name = name or default
    if not name:
        raise OpError(
            "no_agent", "no agent given and no default_agent in .aegis.yaml"
        )
    agent = next((a for a in agents if a.name == name), None)
    if agent is None:
        raise OpError("unknown_agent", f"no agent named {name!r}")
    if agent.error:
        raise OpError("bad_agent", f"agent {name!r}: {agent.error}")
    fields = {f: getattr(agent, f) for f in FIELDS}
    overridden = tuple(
        f
        for f in FIELDS
        if overrides.get(f) not in (None, "") and overrides[f] != fields[f]
    )
    fields |= {f: str(overrides[f]) for f in overridden}
    if fields["harness"] not in SUPPORTED_HARNESSES:
        raise OpError(
            "harness_unsupported", f"{fields['harness']} is not supported yet"
        )
    return SpawnSpec(
        agent=agent.name,
        model=fields["model"],
        effort=fields["effort"],
        permission=fields["permission"],
        cwd=cwd,
        harness=fields["harness"],
        priming=agent.priming,
        overridden=overridden,
        spawned_by=spawned_by,
    )
```

- [x] **Step 5: Run the tests to see them pass**

Run: `uv run pytest -q tests/test_agent_config.py tests/test_imports.py tests/test_stream.py`
Expected: PASS. If `tests/test_imports.py` reports an import cycle, check that `claude/process.py` no longer imports `profiles`.

- [x] **Step 6: Commit**

```bash
git add src/aegis/agents.py src/aegis/claude/process.py src/aegis/profiles.py tests/test_agent_config.py
git commit -F - <<'EOF'
feat(agents): agents are strict presets, resolved with overrides (#155)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 3: `agents.list` and `session.spawn` for people and agents

**Files:**
- Modify: `src/aegis/app.py` (docstring; imports; `SpawnParams` at 37; `_resolve_cwd` at 125; `profiles.list` and `session.spawn` at 148-185)
- Test: `tests/test_web.py`, `tests/test_agents.py`, `tests/test_live.py`
- Modify (config fixtures only): `tests/test_browser.py` (the `server` fixture's YAML)

**Interfaces:**
- Consumes: `load_agents`, `default_agent`, `model_suggestions`, `resolve`, `ConfigError`, `HARNESSES`, `SUPPORTED_HARNESSES` from Task 2; `SpawnSpec.spawned_by`, `argv_of` from Task 1.
- Produces: operation `agents.list` (no params, open to agents) returning `{"agents": [Agent.as_dict()], "default": str | None, "harnesses": [{"name", "supported"}], "models": {harness: [str]}, "cwd": str}`; operation `session.spawn` (open to agents) with params `agent, harness, model, effort, permission, cwd, prompt` (all optional) returning `{"log_id", "handle"}`. MCP tools `agents_list` and `session_spawn`. `profiles.list` is gone.

Note: the browser tests stay red from this task until Task 5, because the old client still calls `profiles.list`. Do not run `tests/test_browser.py` here.

- [x] **Step 1: Make every test config name its harness**

Each YAML below gains `harness: claude-code` (or keeps its `harness:`/`provider:`), because a missing harness is now `bad_agent`:

- `tests/test_browser.py`, `server` fixture:
  `"default_agent: opus\nagents:\n  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"`
- `tests/test_live.py`, both fixtures (~lines 88 and 158):
  `f"agents:\n  haiku: {{harness: claude-code, model: {HAIKU}, effort: low, permission: full}}\n"`
- `tests/test_web.py`, the `project` fixture YAML becomes:

```python
        "default_agent: opus\n"
        "agents:\n"
        "  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
        "  reviewer: {harness: claude-code, model: claude-sonnet-5, effort: max, permission: read, priming: You review.}\n"
        "  deepseek: {harness: opencode, model: x, effort: high, permission: full}\n"
        "  broken: {harness: claude-code, model: opus, permission: full}\n"
```

- `tests/test_agents.py`, `CONFIG` becomes:

```python
CONFIG = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  reviewer: {harness: claude-code, model: opus, effort: high, permission: read, priming: You review.}
queues:
  general: {agent: opus, max_parallel: 2}
  solo: {agent: opus, max_parallel: 1}
"""
```

Then rename the spawn parameter everywhere in the tests: every `profile="opus"` and `{"profile": "opus"}` / `{"profile": "haiku"}` in `tests/test_web.py`, `tests/test_agents.py` and `tests/test_live.py` becomes `agent=...` / `{"agent": ...}`. Find them with `grep -n 'profile' tests/test_web.py tests/test_agents.py tests/test_live.py`; after the edit that grep prints nothing.

- [x] **Step 2: Write the failing web tests in `tests/test_web.py`**

Replace `test_profiles_list` with:

```python
def test_agents_list(project, fake_claude):
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        r = Conn(ws).hello().call("agents.list")["result"]
    assert r["default"] == "opus"
    assert [(a["name"], a["enabled"], a["error"]) for a in r["agents"]] == [
        ("opus", True, None),
        ("reviewer", True, None),
        ("deepseek", False, None),
        ("broken", False, "effort is missing"),
    ]
    reviewer = r["agents"][1]
    assert reviewer["has_priming"] is True and "priming" not in reviewer
    assert r["harnesses"] == [
        {"name": "claude-code", "supported": True},
        {"name": "opencode", "supported": False},
    ]
    assert r["models"] == {
        "claude-code": ["opus", "sonnet", "haiku", "fable", "claude-sonnet-5"],
        "opencode": ["x"],
    }
    assert r["cwd"] == str(project)
```

Replace the `test_spawn_errors` parameter list with:

```python
    [
        ({"agent": "nope"}, "unknown_agent"),
        ({"agent": "broken"}, "bad_agent"),
        ({"agent": "deepseek"}, "harness_unsupported"),
        ({"agent": "opus", "harness": "opencode"}, "harness_unsupported"),
        ({"agent": "opus", "cwd": "/"}, "bad_cwd"),
        ({"agent": "opus", "cwd": "missing"}, "bad_cwd"),
        ({"agent": "opus", "effort": "huge"}, "bad_params"),
        ({"agent": "opus", "prompt": ""}, "bad_params"),
        ({"profile": "opus"}, "bad_params"),
    ],
```

Add:

```python
def test_spawn_without_an_agent_or_a_default_is_refused(project, fake_claude):
    (project / ".aegis.yaml").write_text(
        "agents:\n  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    )
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        assert Conn(ws).hello().call("session.spawn")["error"]["code"] == "no_agent"


def test_spawn_sends_the_prompt_and_marks_the_override(project, fake_claude):
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        conn = Conn(ws).hello()
        r = conn.call("session.spawn", model="sonnet", prompt="hello there")["result"]
        ws.send_json({"t": "sub", "channel": "sessions"})
        (meta,) = conn.until(lambda m: m["t"] == "snapshot")["data"]
        assert (meta["agent"], meta["model"], meta["overridden"]) == (
            "opus",
            "sonnet",
            ["model"],
        )
        ws.send_json({"t": "sub", "channel": f"transcript:{r['log_id']}"})
        snap = conn.until(
            lambda m: m["t"] == "snapshot" and m["channel"].startswith("transcript")
        )
        assert snap["data"][0]["summary"].startswith("spawned opus* · sonnet")

        def has_prompt(rows):
            return any(
                e.get("kind") == "user" and e.get("md") == "hello there" for e in rows
            )

        if not has_prompt(snap["data"]):
            conn.until(
                lambda m: (
                    m["t"] == "patch"
                    and m["channel"] == f"transcript:{r['log_id']}"
                    and has_prompt([op["upsert"] for op in m["ops"] if "upsert" in op])
                )
            )
        conn.call("session.close", log_id=r["log_id"])
```

In `test_spawn_send_and_watch_a_turn`, the existing check `startswith("spawned opus")` stays.

- [x] **Step 3: Write the failing MCP tests in `tests/test_agents.py`**

Change the tool-list assertion (~line 135) to:

```python
    assert {"session_spawn", "agents_list"} <= set(tools)
    assert "session_close" not in tools
```

Add:

```python
async def test_an_agent_spawns_with_overrides_its_cwd_and_the_agents_priming(
    world, tmp_path
):
    (tmp_path / "sub").mkdir()
    r = await world.app.registry.call("session.spawn", {"agent": "opus", "cwd": "sub"})
    a = world.session(r["log_id"])
    said = await turn(
        a,
        mcp("session_spawn", agent="reviewer", effort="max", cwd=".", prompt="/argv"),
    )
    child = world.session(json.loads(said.removeprefix("mcp ok: "))["log_id"])
    assert child.spec.spawned_by == a.log_id
    assert child.spec.cwd == (tmp_path / "sub").resolve()
    assert child.spec.overridden == ("effort",)
    await until(
        lambda: any(e["kind"] == "prose" for e in child.entries()),
        timeout=8,
        what="the child's first turn",
    )
    md = next(e["md"] for e in child.entries() if e["kind"] == "prose")
    argv = json.loads(md.removeprefix("argv: "))
    assert argv[argv.index("--effort") + 1] == "max"
    system = argv[argv.index("--append-system-prompt") + 1]
    assert system.startswith("You are running inside aegis")
    assert system.endswith("\n\nYou review.")


async def test_an_agent_lists_the_agents(world):
    a = await world.spawn()
    said = await turn(a, mcp("agents_list"))
    listed = json.loads(said.removeprefix("mcp ok: "))
    assert [x["name"] for x in listed["agents"]] == ["opus", "reviewer"]
```

- [x] **Step 4: Add the live test to `tests/test_live.py`**

Copy the structure of `test_real_claude_arms_a_monitor_through_the_endpoint_and_is_woken` (line 71, the first App-based test: its imports, `.aegis.yaml`, `App`, uvicorn server and `finally` block), and change the body inside `try:` to:

```python
        r = await app.registry.call("session.spawn", {"agent": "haiku"})
        s = app.sessions.sessions[r["log_id"]]
        await s.send(
            "Use the aegis session_spawn tool with agent haiku and prompt "
            "'Reply with the single word PONG.' Then reply with the single word DONE."
        )

        def child():
            return next(
                (
                    x
                    for x in app.sessions.sessions.values()
                    if x.spec.spawned_by == s.log_id
                ),
                None,
            )

        await until(lambda: child() is not None, timeout=120, what="the spawned session")
        await until(
            lambda: any(
                "PONG" in (e.get("md") or "")
                for e in child().entries()
                if e["kind"] == "prose"
            ),
            timeout=120,
            what="the spawned session's answer",
        )
```

Name it `test_real_claude_spawns_a_peer_through_session_spawn`, with the docstring `"""The real binary finds session_spawn from its description and the new session runs its prompt."""`.

- [x] **Step 5: Run the tests to see them fail**

Run: `uv run pytest -q tests/test_web.py tests/test_agents.py`
Expected: FAIL (`unknown_op` for `agents.list`; `bad_params` for `agent`).

- [x] **Step 6: Rewrite the spawn operations in `src/aegis/app.py`**

Imports: replace `from .profiles import ProfileError, default_profile, load_profiles` with

```python
from .agents import (
    HARNESSES,
    SUPPORTED_HARNESSES,
    ConfigError,
    default_agent,
    load_agents,
    model_suggestions,
    resolve,
)
```

and drop `from .session import SpawnSpec` if nothing else in `app.py` uses it.

Replace `SpawnParams`:

```python
class SpawnParams(_Strict):
    agent: str | None = Field(
        None,
        description="The agent to start from (agents_list shows them); omitted means default_agent.",
    )
    harness: str | None = Field(None, description="Overrides the agent's harness.")
    model: str | None = Field(None, description="Overrides the agent's model.")
    effort: Effort | None = Field(None, description="Overrides the agent's effort.")
    permission: Permission | None = Field(
        None, description="Overrides the agent's permission."
    )
    cwd: str | None = Field(
        None,
        description="Working directory inside the server's root; a relative one resolves against yours.",
    )
    prompt: str | None = Field(
        None, min_length=1, description="The new session's first message."
    )
```

Give `_resolve_cwd` a base:

```python
    def _resolve_cwd(self, raw: str | None, base: Path | None = None) -> Path:
        base = base or self.roots.harness_cwd
        p = Path(raw).expanduser() if raw else base
        if not p.is_absolute():
            p = base / p
        p = p.resolve()
        root = self.roots.config_root.resolve()
        if not (p == root or p.is_relative_to(root)):
            raise OpError("bad_cwd", f"{p} is outside {root}")
        if not p.is_dir():
            raise OpError("bad_cwd", f"{p} is not a directory")
        return p
```

Add a loader next to it:

```python
    def _agents(self):
        try:
            root = self.roots.config_root
            return load_agents(root), default_agent(root)
        except ConfigError as e:
            raise OpError("bad_config", str(e)) from e
```

Replace the `profiles.list` and `session.spawn` handlers with:

```python
        @r.op("agents.list", agent=True)
        async def agents_list(_, caller):
            """The agents you can spawn, each a preset of harness, model, effort
            and permission. session_spawn starts one and can override those."""
            agents, default = self._agents()
            return {
                "agents": [a.as_dict() for a in agents],
                "default": default,
                "harnesses": [
                    {"name": h, "supported": h in SUPPORTED_HARNESSES}
                    for h in HARNESSES
                ],
                "models": model_suggestions(agents),
                "cwd": str(self.roots.harness_cwd),
            }

        @r.op("session.spawn", SpawnParams, agent=True)
        async def spawn(p: SpawnParams, caller):
            """Start a new session from an agent, overriding its harness, model,
            effort or permission if you need to, and send it `prompt` as its
            first message. Returns its log id and handle. It does not report
            back: read it with peer_read, message it with peer_handoff."""
            agents, default = self._agents()
            parent = reg.sessions.get(caller.log_id) if caller.is_agent else None
            spec = resolve(
                agents,
                default,
                p.agent,
                {
                    "harness": p.harness,
                    "model": p.model,
                    "effort": p.effort,
                    "permission": p.permission,
                },
                self._resolve_cwd(p.cwd, parent.spec.cwd if parent else None),
                spawned_by=parent.log_id if parent else None,
            )
            try:
                s = await reg.spawn(spec)
            except FileNotFoundError as e:
                raise OpError(
                    "claude_not_found", f"cannot run {self.claude_bin!r}: {e}"
                ) from e
            if p.prompt:
                try:
                    await s.send(p.prompt)
                except (BrokenPipeError, ConnectionResetError, FileNotFoundError) as e:
                    raise OpError(
                        "send_failed",
                        f"{s.handle} ({s.log_id}) started, but its first message failed: {e}",
                    ) from e
            return {"log_id": s.log_id, "handle": s.handle}
```

In the module docstring, replace ``profiles.list`` with ``agents.list``.

- [x] **Step 7: Run the tests to see them pass**

Run: `uv run pytest -q tests/test_web.py tests/test_agents.py tests/test_ops.py`
Expected: PASS.

- [x] **Step 8: Commit**

```bash
git add src/aegis/app.py tests/test_web.py tests/test_agents.py tests/test_live.py tests/test_browser.py
git commit -F - <<'EOF'
feat(spawn): session.spawn takes overrides and a first prompt, and agents can call it (#155)

profiles.list becomes agents.list. Both are open to agents as
session_spawn and agents_list.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4: Queues name every field and start workers through `resolve`

**Files:**
- Modify: `src/aegis/queues.py` (module docstring line 4; imports; `load_queues` at 70-87; `dispatch` at ~175; `_start` at 201-218)
- Modify: `src/aegis/agent_ops.py` (`queue_enqueue` at 179)
- Delete: `src/aegis/profiles.py`, `tests/test_profiles.py`
- Test: `tests/test_agent_config.py`, `tests/test_agents.py`

**Interfaces:**
- Consumes: `read_config`, `ConfigError`, `load_agents`, `resolve` from Task 2.
- Produces: `load_queues(config_root) -> dict[str, dict]` where each value is either `{"agent": str, "max_parallel": int}` or `{"error": str}`; `queue_enqueue` refuses an errored queue with `bad_config`.

- [x] **Step 1: Write the failing tests**

In `tests/test_agent_config.py`:

```python
from aegis.queues import load_queues


def test_a_queue_names_its_agent_and_max_parallel(tmp_path):
    root = write(
        tmp_path,
        "queues:\n"
        "  ok: {agent: opus, max_parallel: 3}\n"
        "  nolimit: {agent: opus}\n"
        "  bare: {}\n"
        "  zero: {agent: opus, max_parallel: 0}\n",
    )
    assert load_queues(root) == {
        "ok": {"agent": "opus", "max_parallel": 3},
        "nolimit": {"error": "max_parallel is missing"},
        "bare": {"error": "agent, max_parallel are missing"},
        "zero": {"error": "max_parallel 0 is not a positive integer"},
    }
```

In `tests/test_agents.py`, extend `CONFIG`'s `queues:` with

```
  reviewing: {agent: reviewer, max_parallel: 1}
  broken: {agent: opus}
```

and add:

```python
async def test_a_worker_gets_its_agents_priming(world):
    a = await world.spawn()
    await turn(a, mcp("queue_enqueue", queue="reviewing", payload="/argv"))
    await until(lambda: inbox(a), timeout=12, what="the callback")
    (cb,) = inbox(a)
    assert "You review." in cb["md"]


async def test_a_queue_missing_a_field_says_which(world):
    a = await world.spawn()
    said = await turn(a, mcp("queue_enqueue", queue="broken", payload="x"))
    assert said.startswith("mcp error: bad_config")
    assert "max_parallel is missing" in said


async def test_a_logged_task_on_a_queue_that_broke_does_not_stop_dispatch(world):
    # A task logged while `broken` was valid must not crash the dispatcher
    # with a KeyError on max_parallel; the other queues keep working.
    from aegis.queues import Task

    t = Task(id="task-old", queue="broken", payload="x", callback=False,
             enqueuer=None, cwd=str(world.root))
    world.app.queues.tasks[t.id] = t
    a = await world.spawn()
    said = await turn(
        a, mcp("queue_enqueue", queue="general", payload="hi", callback=False)
    )
    assert said.startswith("mcp ok")
```

Check `Task`'s required fields in `src/aegis/queues.py` (the dataclass above `load_queues`) and pass exactly those; the call above matches the fields `Queues.enqueue` sets.

- [x] **Step 2: Run the tests to see them fail**

Run: `uv run pytest -q tests/test_agent_config.py tests/test_agents.py -k "queue or worker"`
Expected: FAIL (`nolimit` parses as `max_parallel: 1`; `broken` is unknown; `KeyError: 'max_parallel'` or a worker without the priming).

- [x] **Step 3: Rewrite `load_queues`**

```python
def load_queues(config_root: Path) -> dict[str, dict]:
    """Every queue in ``queues:``. A queue that does not name its agent and a
    positive ``max_parallel`` is kept with an ``error``, so enqueueing on it
    says what is wrong; nothing in .aegis.yaml is a default (agents.py)."""
    try:
        raw = read_config(config_root).get("queues")
    except ConfigError:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for name, q in raw.items():
        q = q if isinstance(q, dict) else {}
        agent, limit = q.get("agent"), q.get("max_parallel")
        missing = [
            k for k, v in (("agent", agent), ("max_parallel", limit)) if v in (None, "")
        ]
        if missing:
            verb = "is" if len(missing) == 1 else "are"
            out[str(name)] = {"error": f"{', '.join(missing)} {verb} missing"}
        elif isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            out[str(name)] = {
                "error": f"max_parallel {limit!r} is not a positive integer"
            }
        else:
            out[str(name)] = {"agent": str(agent), "max_parallel": limit}
    return out
```

Imports: replace `from .profiles import load_profiles` with `from .agents import ConfigError, load_agents, read_config, resolve` and add `from .ops import OpError` if `queues.py` does not import it yet. Remove the now-unused `YAML`, `YAMLError` and `CONFIG_FILE` imports if nothing else in the module uses them (check with `grep -n "YAML\|CONFIG_FILE" src/aegis/queues.py`).

- [x] **Step 4: Skip errored queues in `dispatch`, start workers through `resolve`**

In `dispatch`, make the loop's first statement:

```python
            for name, q in self.queues.items():
                if "error" in q:
                    continue
```

Replace the body of `_start` up to `try: s = await self._registry.spawn(` with:

```python
    async def _start(self, t: Task, q: dict) -> None:
        try:
            agents = load_agents(self._registry.roots.config_root)
            spec = resolve(agents, None, q["agent"], {}, Path(t.cwd))
        except (ConfigError, OpError) as e:
            reason = e.message if isinstance(e, OpError) else str(e)
            self._fail(t, f"the queue's agent {q['agent']!r} cannot start: {reason}")
            return
```

keeping the rest of `_start` (spawn, `dispatched`, `send`) as it is. In the module docstring, line 4, change "the queue's profile" to "the queue's agent".

- [x] **Step 5: Refuse an errored queue in `queue_enqueue`**

In `src/aegis/agent_ops.py`, after the `unknown_queue` check:

```python
        if err := app.queues.queues[p.queue].get("error"):
            raise OpError("bad_config", f"queue {p.queue!r} in .aegis.yaml: {err}")
```

- [x] **Step 6: Delete `profiles.py` and its test**

```bash
git rm src/aegis/profiles.py tests/test_profiles.py
grep -rn "profiles" src/aegis tests scripts --include=*.py
```

Expected: the grep prints nothing.

- [x] **Step 7: Run the tests to see them pass**

Run: `uv run pytest -q tests/test_agent_config.py tests/test_agents.py tests/test_imports.py`
Expected: PASS.

- [x] **Step 8: Commit**

```bash
git add src/aegis/queues.py src/aegis/agent_ops.py tests/test_agent_config.py tests/test_agents.py
git commit -F - <<'EOF'
feat(queues): a queue names its agent and max_parallel; workers get their agent's priming (#155)

profiles.py is gone; agents.py replaces it.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 5: The composer in the browser

**Files:**
- Modify: `src/aegis/client/index.html:41-56` (the `v-spawn` view)
- Modify: `src/aegis/client/css/base.css:47`, `:54-60`, `:153`
- Modify: `src/aegis/client/js/app.js` (`loadProfiles()` call at ~78; the spawn view in `render()` at ~138; the `// -- spawn` section at ~346-400)
- Modify: `src/aegis/client/js/fleet.js:48` (the card's agent name)
- Test: `tests/test_browser.py`

**Interfaces:**
- Consumes: `agents.list` and `session.spawn` from Task 3; wire keys `agent`, `overridden` from Task 1.
- Produces: element ids `sp-agent`, `sp-harness`, `sp-model` (with `datalist#sp-models`), `sp-effort`, `sp-permission`, `sp-cwd`, `sp-text`, `sp-reset`, `sp-go`, `sp-error`; class `diff` on a changed chip; localStorage key `aegis.lastAgent`.

- [x] **Step 1: Write the failing browser tests**

In `tests/test_browser.py`, replace the `spawn` helper:

```python
def spawn(pg, prompt: str | None = None) -> str:
    pg.click("#tab-add")
    pg.wait_for_selector("#a2[data-view=spawn]")
    pg.wait_for_function("document.querySelector('#sp-agent').value !== ''")
    if prompt:
        pg.fill("#sp-text", prompt)
        pg.press("#sp-text", "Enter")
    else:
        pg.click("#sp-go")
    pg.wait_for_selector("#a2[data-view=session]")
    if prompt:
        turns_done(pg, 1)
    return pg.evaluate("location.hash.slice(3)")
```

Add:

```python
def test_the_composer_overrides_a_chip_resets_it_and_spawns_with_the_first_message(
    server, page
):
    page.goto(server.url)
    page.click("#tab-add")
    page.wait_for_function("document.querySelector('#sp-agent').value === 'opus'")
    assert page.input_value("#sp-model") == "opus"
    assert page.is_hidden("#sp-reset")

    page.fill("#sp-model", "sonnet")
    assert page.inner_text("#sp-agent option:checked") == "opus*"
    assert "diff" in page.get_attribute("#sp-model", "class")
    page.click("#sp-reset")
    assert page.input_value("#sp-model") == "opus"
    assert page.inner_text("#sp-agent option:checked") == "opus"
    assert page.is_hidden("#sp-reset")

    page.fill("#sp-text", "/argv")
    page.press("#sp-text", "Shift+Enter")
    assert page.evaluate("document.querySelector('#a2').dataset.view") == "spawn"
    page.fill("#sp-text", "/argv")
    page.select_option("#sp-effort", "max")
    page.press("#sp-text", "Enter")
    page.wait_for_selector("#a2[data-view=session]")
    turns_done(page, 1)
    text = page.inner_text("#entries")
    assert "/argv" in text and '"--effort", "max"' in text

    page.click("#tab-fleet")
    page.wait_for_selector("#a2[data-view=fleet]")
    assert page.inner_text(".card .ln b") == "opus*"
    page.click("#tab-add")
    page.wait_for_function("document.querySelector('#sp-agent').value === 'opus'")
    assert page.input_value("#sp-effort") == "high", "a spawn clears the overrides"
    assert page.input_value("#sp-text") == ""
    assert page.errors == []


def test_a_failed_spawn_keeps_the_text_and_says_why(server, page):
    page.goto(server.url)
    page.click("#tab-add")
    page.wait_for_function("document.querySelector('#sp-agent').value === 'opus'")
    page.fill("#sp-cwd", "/")
    page.fill("#sp-text", "keep me")
    page.press("#sp-text", "Enter")
    page.wait_for_function("document.querySelector('#sp-error').textContent !== ''")
    assert "outside" in page.inner_text("#sp-error")
    assert page.input_value("#sp-text") == "keep me"
    assert page.evaluate("document.querySelector('#a2').dataset.view") == "spawn"
```

- [x] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_browser.py -k "composer or failed_spawn"`
Expected: FAIL (no `#sp-agent`).

- [x] **Step 3: Replace the view's markup in `src/aegis/client/index.html`**

Replace the whole `<form class="spawn" id="spawn">…</form>` with:

```html
    <form class="spawn" id="spawn">
      <div class="mark">aegis</div>
      <input class="cwd" id="sp-cwd" spellcheck="false" aria-label="Working directory" title="Working directory: click to change">
      <div class="box">
        <textarea id="sp-text" rows="3" aria-label="First message"
          placeholder="What should the agent do? Enter starts the session and sends this; Shift+Enter adds a line."></textarea>
        <div class="picks">
          <select class="pick agent" id="sp-agent" aria-label="Agent"></select>
          <select class="pick" id="sp-harness" aria-label="Harness"></select>
          <input class="pick" id="sp-model" list="sp-models" spellcheck="false" aria-label="Model">
          <datalist id="sp-models"></datalist>
          <select class="pick" id="sp-effort" aria-label="Effort">
            <option value="low">effort low</option><option value="medium">effort medium</option>
            <option value="high">effort high</option><option value="max">effort max</option></select>
          <select class="pick" id="sp-permission" aria-label="Permission">
            <option value="read">perm read</option><option value="write">perm write</option>
            <option value="auto">perm auto</option><option value="full">perm full</option></select>
          <button type="button" class="reset" id="sp-reset" hidden>reset</button>
          <button class="send" type="submit" id="sp-go" title="Start the session">↵</button>
        </div>
      </div>
      <p class="err-text" id="sp-error"></p>
    </form>
```

- [x] **Step 4: Replace the styles in `src/aegis/client/css/base.css`**

Line 47 becomes:

```css
#a2[data-view=spawn] .v-spawn{display:grid;place-items:center;overflow:auto;padding:48px 20px}
```

Replace lines 54-60 (`/* spawn form */` through `.three`) with:

```css
/* the new-tab composer */
#a2 .spawn{width:min(640px,100%);display:grid;gap:10px;justify-items:center}
#a2 .spawn .mark{font-family:var(--font-head);font-weight:600;font-size:24px;letter-spacing:.04em;color:var(--strong)}
#a2 .spawn .cwd{width:100%;background:none;border:none;border-bottom:1px dashed transparent;outline:none;text-align:center;font-family:var(--font-mono);font-size:12px;color:var(--muted)}
#a2 .spawn .cwd:hover,#a2 .spawn .cwd:focus{border-bottom-color:var(--faint);color:var(--ink)}
#a2 .spawn .box{width:100%;display:grid;gap:10px;background:var(--surface);border:1px solid var(--rule);border-radius:var(--r-lg);padding:12px 14px}
#a2 .spawn .box:focus-within{border-color:var(--accent)}
#a2 .spawn textarea{width:100%;resize:none;background:none;border:none;outline:none;color:var(--strong);font-family:var(--font-ui);font-size:14px;line-height:1.45;max-height:40vh}
#a2 .spawn .picks{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
#a2 .spawn .pick{background:var(--bg);border:1px solid var(--rule);border-radius:5px;padding:3px 8px;font-family:var(--font-chrome);font-size:11.5px;color:var(--ink);cursor:pointer}
#a2 .spawn input.pick{width:24ch;cursor:text}
#a2 .spawn .pick.agent{border-color:var(--accent);color:var(--accent)}
#a2 .spawn .pick.diff{color:var(--accent)}
#a2 .spawn .reset{background:none;border:none;padding:0;color:var(--muted);font-size:11px;text-decoration:underline;cursor:pointer}
#a2 .spawn .send{margin-left:auto;background:var(--accent);color:var(--on-accent);border:none;border-radius:6px;padding:4px 12px;font-weight:600;cursor:pointer}
#a2 .spawn .err-text{margin:0}
#a2 .spawn .err-text:empty{display:none}
```

Keep line 61 (`#a2 .pa`) and line 62 (`#a2 .err-text`); other views use them. Delete line 153 (`#a2 .spawn .three{grid-template-columns:1fr}`) inside the media query.

- [x] **Step 5: Replace the spawn section of `src/aegis/client/js/app.js`**

Replace everything from `// -- spawn ---…` (the `let profiles = [];` line) through the end of the `$("spawn").addEventListener("submit", …)` handler with:

```js
// -- the new-tab composer -------------------------------------------------------
// An agent is a preset: picking one fills the other chips, and changing a chip
// marks the agent `name*` until reset. Enter spawns and sends in one call.
let roster = { agents: [], harnesses: [], models: {}, default: null, cwd: "" };
const LAST_AGENT = "aegis.lastAgent";
const PICKS = ["harness", "model", "effort", "permission"];

async function loadAgents() {
  try {
    roster = await conn.call("agents.list");
  } catch (e) {
    $("sp-error").textContent = e.message;
    return;
  }
  // A reconnect rebuilds the options; the chips keep what the person set.
  const before = Object.fromEntries(PICKS.map((k) => [k, $(`sp-${k}`).value]));
  $("sp-harness").replaceChildren(
    ...roster.harnesses.map((h) => {
      const o = new Option(h.supported ? h.name : `${h.name} (not supported yet)`, h.name);
      o.disabled = !h.supported;
      return o;
    }),
  );
  $("sp-agent").replaceChildren(
    ...roster.agents.map((a) => {
      const why = a.error || (a.enabled ? "" : `${a.harness} is not supported yet`);
      const o = new Option(why ? `${a.name} (${why})` : a.name, a.name);
      o.disabled = !a.enabled;
      return o;
    }),
  );
  const usable = roster.agents.filter((a) => a.enabled).map((a) => a.name);
  const keep = $("sp-agent").dataset.picked;
  const start = [keep, localStorage.getItem(LAST_AGENT), roster.default].find((n) => usable.includes(n)) || usable[0];
  if (!$("sp-cwd").value) $("sp-cwd").value = roster.cwd;
  $("sp-error").textContent = roster.agents.length ? "" : "No agents: add an agents: map to .aegis.yaml.";
  if (keep && usable.includes(keep)) {
    $("sp-agent").value = keep;
    for (const k of PICKS) $(`sp-${k}`).value = before[k];
    fillModels($("sp-harness").value);
    markDiffs();
  } else if (start) pickAgent(start);
}

function current() {
  return roster.agents.find((a) => a.name === $("sp-agent").value);
}

function fillModels(harness) {
  $("sp-models").replaceChildren(...(roster.models[harness] || []).map((m) => new Option(m, m)));
}

function pickAgent(name) {
  const a = roster.agents.find((x) => x.name === name);
  if (!a) return;
  $("sp-agent").value = a.name;
  $("sp-agent").dataset.picked = a.name;
  fillModels(a.harness);
  for (const k of PICKS) $(`sp-${k}`).value = a[k];
  markDiffs();
}

function overrides() {
  const a = current();
  const out = {};
  if (!a) return out;
  for (const k of PICKS) {
    const v = $(`sp-${k}`).value.trim();
    if (v && v !== a[k]) out[k] = v;
  }
  return out;
}

function markDiffs() {
  const a = current();
  const diff = overrides();
  for (const k of PICKS) $(`sp-${k}`).classList.toggle("diff", k in diff);
  const changed = Object.keys(diff).length > 0;
  $("sp-reset").hidden = !changed;
  const opt = $("sp-agent").selectedOptions[0];
  if (a && opt) opt.textContent = changed ? `${a.name}*` : a.name;
}

async function spawnFromComposer() {
  const a = current();
  if (!a || $("sp-go").disabled) return;
  $("sp-go").disabled = true;
  $("sp-error").textContent = "";
  const text = $("sp-text").value.trim();
  const params = { agent: a.name, cwd: $("sp-cwd").value.trim() || null, ...overrides() };
  if (text) params.prompt = text;
  try {
    const r = await conn.call("session.spawn", params);
    localStorage.setItem(LAST_AGENT, a.name);
    $("sp-text").value = "";
    pickAgent(a.name);
    go(`#s=${r.log_id}`);
  } catch (e) {
    $("sp-error").textContent = e.message;
  } finally {
    $("sp-go").disabled = false;
  }
}

$("sp-agent").addEventListener("change", () => pickAgent($("sp-agent").value));
$("sp-harness").addEventListener("change", () => {
  fillModels($("sp-harness").value);
  markDiffs();
});
for (const k of ["model", "effort", "permission"]) $(`sp-${k}`).addEventListener("input", markDiffs);
$("sp-reset").addEventListener("click", () => pickAgent($("sp-agent").value));
$("spawn").addEventListener("submit", (ev) => {
  ev.preventDefault();
  spawnFromComposer();
});
$("sp-text").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
    ev.preventDefault();
    spawnFromComposer();
  }
});
```

In the connection's `onState` handler, replace `loadProfiles();` with `loadAgents();`. In `render()`, in the `r.view === "spawn"` branch, add `$("sp-text").focus();` after `show("spawn");`.

`markDiffs` rewrites the selected option's text, and `loadAgents` rebuilds every option from `roster` and then calls `markDiffs`, so a reconnect never shows a stale `*`.

- [x] **Step 6: Show the agent and its star on the fleet card**

In `src/aegis/client/js/fleet.js:48`, replace `m.profile` with:

```js
`${m.agent}${(m.overridden || []).length ? "*" : ""}`
```

- [x] **Step 7: Run the browser tests to see them pass**

Run: `uv run playwright install chromium` (once per worktree), then `uv run pytest -q tests/test_browser.py`
Expected: PASS, every browser test, since they all spawn through the new helper.

- [x] **Step 8: Commit**

```bash
git add src/aegis/client/index.html src/aegis/client/css/base.css src/aegis/client/js/app.js src/aegis/client/js/fleet.js tests/test_browser.py
git commit -F - <<'EOF'
feat(client): the new tab is a composer; Enter spawns and sends (#155)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 6: Docs, release note, every gate, and the PR

**Files:**
- Modify: `README.md` (the Configuration section, ~lines 42-56)
- Modify: `DESIGN.md` (the paragraph "Its own state, never mixed with the legacy tree's", last sentence about `.aegis.yaml`)
- Create: `changelog.d/155-new-tab-composer.changed.md`
- Modify: `docs/superpowers/specs/2026-10-07-aegis-new-tab-composer-design.md` (status line)
- Modify: this plan (check the boxes)

- [x] **Step 1: README**

Replace the YAML block and the paragraph after it in "Configuration" with:

````markdown
```yaml
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  reviewer:
    harness: claude-code
    model: opus
    effort: max
    permission: read
    priming: |
      You review changes for correctness and report findings by severity.
queues:
  general: {agent: opus, max_parallel: 5}
```

An agent is a preset. The new tab starts from one, and any of its harness,
model, effort and permission can be changed for that session; its `priming` is
appended to aegis's own system prompt. Nothing has a default: an agent names
all four fields and a queue names its agent and `max_parallel`, and one that
does not is shown with what is missing. `permission` is `read` (plan mode),
`write` (accept edits), `auto` or `full` (bypass permissions). Only Claude Code
agents run today.
````

- [x] **Step 2: DESIGN.md**

Replace the sentence "Of `.aegis.yaml`, aegis reads the `agents:` and `queues:` maps." with:

```markdown
Of `.aegis.yaml`, aegis reads the `agents:` and `queues:` maps, and nothing in
them is a default: an agent names its harness, model, effort and permission, a
queue its agent and `max_parallel`, and one that does not is reported by name
rather than filled in or dropped. A setting the loader filled in would be one
nobody chose, with nothing to show it.
```

- [x] **Step 3: Changelog fragment**

`changelog.d/155-new-tab-composer.changed.md`:

```markdown
- **The new tab is a composer: type the first message, pick the agent and its
  settings, press Enter.** An agent is now a preset of harness, model, effort,
  permission and an optional `priming:` prompt, and every field but the priming
  can be changed per session. Agents get the same power as
  `mcp__aegis__session_spawn` and `mcp__aegis__agents_list`. `.aegis.yaml` has
  no defaults any more: an agent missing a field, or a queue missing
  `max_parallel`, is shown with what is missing instead of being filled in.
```

Run: `make changelog-check`
Expected: exit 0.

- [x] **Step 4: Run every gate, reading each exit code directly**

```bash
make check; echo "check rc=$?"
rift check; echo "rift rc=$?"
make test-browser; echo "browser rc=$?"
make test-live; echo "live rc=$?"
make bench
```

Expected: `check`, `rift`, `browser` and `live` print `rc=0`. Never pipe a gate (`make check | tail` reports `tail`'s status). Keep the bench table for the PR body.

- [x] **Step 5: Exercise it in a browser against a server started after the change**

```bash
uv run aegis serve --root /tmp/aegis-composer --port 8766 --detach
```

with `/tmp/aegis-composer/.aegis.yaml` holding the README's example. Open the printed URL, press `+`, change the model chip to `sonnet`, type a prompt and press Enter. Check that: the tab opens with the message as its first entry; the agent chip read `opus*` before Enter and the fleet card reads `opus*`; `reset` restored the chips; an agent with a missing field shows disabled with its reason. Take a screenshot of the composer for the PR. Stop that server with the `kill <pid>` line `aegis serve --detach` printed (never `pkill -f`).

- [x] **Step 6: Flip the spec's status and check this plan's boxes**

In the spec, the status line becomes `**Status: implemented, 2026-10-07** (issue #155), following this plan.` Check every box above.

- [x] **Step 7: Commit, push, open the PR**

```bash
git add README.md DESIGN.md changelog.d/155-new-tab-composer.changed.md docs/superpowers/specs/2026-10-07-aegis-new-tab-composer-design.md docs/superpowers/plans/2026-10-07-aegis-new-tab-composer.md
git commit -F - <<'EOF'
docs: agents as presets, the composer, and no defaults in .aegis.yaml (#155)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git push
gh pr create --title "The new tab is a composer, and agents are presets (#155)" --body-file -
```

The PR body carries: what changed (one paragraph per task); the bench table; the gates and their exit codes; the browser screenshot; what was left out (#153 extra MCP servers, agents closing what they spawn, overrides on `queue_enqueue`); and that the Workspace's `.aegis.yaml` needs `effort:` on its `deepseek` agent before an aegis built from this branch reads it. End it with the line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Do not merge: Alex reviews.
