# Harness Registry + Per-Session Model/Effort Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make OpenCode first-class (model selection incl. free models), add a top-level `harnesses:` registry (driver + credentials), per-session model/effort selection at spawn, and optional per-agent persona system prompts.

**Architecture:** Split the monolithic `Agent` into a harness registry (resolved at YAML load time into the driver string + credentials the existing driver code already reads) plus per-agent/per-session `model`/`effort`/`prompt`. Driver selection stays `get_driver(agent.harness)` — resolution rewrites `agent.harness` to the driver string, so every existing call site keeps working. OpenCode model selection rides the existing `AcpDriver.extra_env` seam via `OPENCODE_CONFIG_CONTENT`.

**Tech Stack:** Python 3.13+, pydantic, ruamel.yaml, Textual 8.x, FastMCP, the `agent-client-protocol` SDK.

## Global Constraints

- Python 3.13+; package manager is `uv` (`uv run pytest`, `uv pip install -e .`).
- Commit straight to `main` (aegis convention). TDD: failing test first, minimal impl, commit per logical unit.
- Fast hermetic suite: `uv run python -m pytest -q -m "not live"`. Never `-k "not live"` (substring-matches `deliver` etc.). Live tests auto-skip when the CLI is off PATH.
- Code, identifiers, comments, error strings in English.
- Back-compat is mandatory: existing `.aegis.yaml` files using `provider: claude-code` (+ `model`/`effort`) MUST load unchanged; the four driver strings auto-register as implicit harnesses.
- Do not touch queues/schedules/groups config surfaces — they reference named agent profiles only.
- Spec: `docs/superpowers/specs/2026-07-28-aegis-harness-registry-per-session-models-design.md`.

## File Structure

- `src/aegis/drivers/opencode.py` — add `extra_env` (model injection). **[VS1]**
- `src/aegis/config/__init__.py` — `HarnessRegistration` dataclass; `Agent.prompt` field. **[VS2, VS3]**
- `src/aegis/config/harnesses.py` (new) — implicit-registration builder + agent→driver resolution helper. **[VS2]**
- `src/aegis/config/yaml_loader.py` — parse `harnesses:` + overlays; resolve agents against the registry. **[VS2]**
- `src/aegis/drivers/claude.py` — persona as a 2nd `--append-system-prompt`. **[VS3]**
- `src/aegis/drivers/acp.py` — persona: opencode `instructions`, lovelaice env, first-turn prepend fallback. **[VS3]**
- `src/aegis/models/__init__.py` — opencode live catalog for `models_for`. **[VS4]**
- `src/aegis/tui/picker.py` — two-tier harness→model→effort picker. **[VS4]**
- `src/aegis/mcp/server.py` + `src/aegis/mcp/bridge.py` + `src/aegis/core/manager.py` — `aegis_spawn` gains `model`/`effort`/`prompt`. **[VS4]**
- `src/aegis/config/edit.py` + `src/aegis/cli_config.py` — `aegis config harness {add,list,remove}`. **[VS5]**
- `src/aegis/tui/config_panel.py` — `AddAgentModal` harness-aware. **[VS5]**

---

## VS1 — OpenCode model selection (the headline fix; no new config)

This slice alone makes OpenCode first-class using today's `provider: opencode` + `model:` shape. It is the thinnest end-to-end win and ships before any registry work.

### Task 1: OpenCode model injection via `extra_env`

**Files:**
- Modify: `src/aegis/drivers/opencode.py`
- Test: `tests/test_opencode_driver.py` (create)

**Interfaces:**
- Consumes: `AcpDriver.extra_env(agent) -> dict[str,str]` seam (base returns `{}`), `Agent.model`.
- Produces: `OpenCodeDriver.extra_env(agent)` returns `{"OPENCODE_CONFIG_CONTENT": '{"model": "<agent.model>"}'}` when `agent.model` is truthy, else `{}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_opencode_driver.py
import json
from aegis.config import Agent, OpenCode
from aegis.drivers.opencode import OpenCodeDriver


def test_extra_env_injects_model():
    agent = Agent(provider=OpenCode(model="opencode/deepseek-v4-flash-free"))
    env = OpenCodeDriver().extra_env(agent)
    payload = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert payload["model"] == "opencode/deepseek-v4-flash-free"


def test_extra_env_empty_when_no_model():
    # A bare opencode agent with empty model injects nothing → opencode
    # keeps its own config default.
    agent = Agent(harness="opencode", model="")
    assert OpenCodeDriver().extra_env(agent) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_opencode_driver.py -v`
Expected: FAIL — `OpenCodeDriver` has no `extra_env` override, so the key is missing (KeyError).

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/drivers/opencode.py
from __future__ import annotations

import json

from aegis.config import Agent
from aegis.drivers.acp import AcpDriver


class OpenCodeDriver(AcpDriver):
    BASE_CMD = ["opencode", "acp"]

    def extra_env(self, agent: Agent) -> dict[str, str]:
        model = getattr(agent, "model", "") or ""
        if not model:
            return {}
        # opencode acp has no -m flag; it reads its model from config.
        # OPENCODE_CONFIG_CONTENT is inline JSON merged over the discovered
        # config, so the repo opencode.json MCP block is preserved.
        return {"OPENCODE_CONFIG_CONTENT": json.dumps({"model": model})}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_opencode_driver.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aegis/drivers/opencode.py tests/test_opencode_driver.py
git commit -m "feat(opencode): select model via OPENCODE_CONFIG_CONTENT extra_env"
```

### Task 2: Live probe — model selection is honored + MCP preserved

**Files:**
- Modify: `tests/test_drivers_multiprovider_live.py`

**Interfaces:**
- Consumes: real `opencode` CLI on PATH; the aegis MCP runtime + `OpenCodeDriver.session(...)`.
- Produces: a `@pytest.mark.live` test asserting an OpenCode session on a **free** model answers a trivial prompt without error.

- [ ] **Step 1: Write the failing (skip-if-absent) test**

```python
# tests/test_drivers_multiprovider_live.py — append
import shutil
import pytest


@pytest.mark.live
@pytest.mark.skipif(shutil.which("opencode") is None,
                    reason="opencode not on PATH")
@pytest.mark.asyncio
async def test_opencode_free_model_roundtrip():
    from aegis.config import Agent, OpenCode
    from aegis.drivers.opencode import OpenCodeDriver

    agent = Agent(provider=OpenCode(model="opencode/deepseek-v4-flash-free"))
    sess = OpenCodeDriver().session(
        agent, cwd=".", mcp_url="", handle="probe")
    await sess.start()
    try:
        await sess.send("Reply with the single word: ok")
        texts = []
        async for ev in sess.events():
            t = getattr(ev, "text", None)
            if t:
                texts.append(t)
        assert "".join(texts).strip() != ""
    finally:
        await sess.close()
```

- [ ] **Step 2: Run it**

Run: `uv run python -m pytest tests/test_drivers_multiprovider_live.py::test_opencode_free_model_roundtrip -v -m live`
Expected: PASS if `opencode` is authenticated. **If it FAILS with a model-not-selected or config error**, the `OPENCODE_CONFIG_CONTENT` inline mechanism doesn't override — apply the fallback in Step 3.

- [ ] **Step 3 (only if probe fails): temp-file config fallback**

Replace `extra_env` in `opencode.py` with a temp-file writer that merges `{"model": …}` over the discovered `opencode.json` and points `OPENCODE_CONFIG` at it. Register cleanup on `AcpSession.close` (add an `_on_close` hook list on the session, or write the temp file under `{cwd}/.aegis/state/opencode/` and leave it — it is overwritten each spawn).

```python
    def extra_env(self, agent: Agent) -> dict[str, str]:
        model = getattr(agent, "model", "") or ""
        if not model:
            return {}
        import tempfile, json, os
        base = {}
        repo_cfg = os.path.join(os.getcwd(), "opencode.json")
        if os.path.isfile(repo_cfg):
            with open(repo_cfg) as fh:
                base = json.load(fh)
        base["model"] = model
        fd, path = tempfile.mkstemp(prefix="opencode-", suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(base, fh)
        return {"OPENCODE_CONFIG": path}
```

Re-run the hermetic Task 1 tests (adjust their assertion to whichever env key ships) and the live probe.

- [ ] **Step 4: Commit**

```bash
git add tests/test_drivers_multiprovider_live.py src/aegis/drivers/opencode.py
git commit -m "test(opencode): live free-model round-trip probe"
```

---

## VS2 — Harness registry + back-compat resolution

### Task 3: `HarnessRegistration` + implicit registrations

**Files:**
- Modify: `src/aegis/config/__init__.py`
- Create: `src/aegis/config/harnesses.py`
- Test: `tests/test_harness_registry.py` (create)

**Interfaces:**
- Produces:
  - `HarnessRegistration` (frozen dataclass): `name: str`, `driver: str`, `base_url: str | None = None`, `api_key_file: str | None = None`, `default_model: str | None = None`, `permission_default: Permission | None = None`.
  - `IMPLICIT_HARNESSES: dict[str, HarnessRegistration]` — one per driver name (`claude-code`, `gemini`, `opencode`, `lovelaice`), no credentials.
  - `merge_harnesses(explicit: dict[str, HarnessRegistration]) -> dict[str, HarnessRegistration]` — explicit wins over implicit on name collision.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_harness_registry.py
from aegis.config import Permission
from aegis.config.harnesses import (
    HarnessRegistration, IMPLICIT_HARNESSES, merge_harnesses)


def test_implicit_covers_four_drivers():
    assert set(IMPLICIT_HARNESSES) == {
        "claude-code", "gemini", "opencode", "lovelaice"}
    assert IMPLICIT_HARNESSES["opencode"].driver == "opencode"


def test_explicit_overrides_implicit():
    reg = HarnessRegistration(name="opencode", driver="opencode",
                              default_model="opencode/mimo-v2.5-free")
    merged = merge_harnesses({"opencode": reg})
    assert merged["opencode"].default_model == "opencode/mimo-v2.5-free"
    # implicit ones still present
    assert merged["claude-code"].driver == "claude-code"


def test_second_endpoint_same_driver():
    ovr = HarnessRegistration(name="openrouter", driver="lovelaice",
                              base_url="https://openrouter.ai/api/v1")
    merged = merge_harnesses({"openrouter": ovr})
    assert merged["openrouter"].driver == "lovelaice"
    assert merged["lovelaice"].driver == "lovelaice"  # implicit still there
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_harness_registry.py -v`
Expected: FAIL — `aegis.config.harnesses` does not exist (ImportError).

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/config/harnesses.py
from __future__ import annotations

from dataclasses import dataclass

from aegis.config import Permission

_DRIVERS = ("claude-code", "gemini", "opencode", "lovelaice")


@dataclass(frozen=True)
class HarnessRegistration:
    name: str
    driver: str
    base_url: str | None = None
    api_key_file: str | None = None
    default_model: str | None = None
    permission_default: Permission | None = None


IMPLICIT_HARNESSES: dict[str, HarnessRegistration] = {
    d: HarnessRegistration(name=d, driver=d) for d in _DRIVERS
}


def merge_harnesses(
    explicit: dict[str, HarnessRegistration],
) -> dict[str, HarnessRegistration]:
    """Implicit driver-name registrations + explicit ones; explicit wins."""
    return {**IMPLICIT_HARNESSES, **explicit}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_harness_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aegis/config/harnesses.py tests/test_harness_registry.py
git commit -m "feat(config): HarnessRegistration + implicit driver registrations"
```

### Task 4: Agent → driver resolution against the registry

**Files:**
- Modify: `src/aegis/config/__init__.py` (add `prompt` field to `Agent`)
- Modify: `src/aegis/config/harnesses.py` (add `resolve_agent_entry`)
- Test: `tests/test_harness_registry.py`

**Interfaces:**
- Consumes: `HarnessRegistration`, the provider classes `ClaudeCode`/`GeminiCLI`/`OpenCode`/`Lovelaice`, `Agent`.
- Produces: `resolve_agent_entry(body: dict, harnesses: dict[str, HarnessRegistration]) -> Agent`. Given a raw agent YAML mapping that carries **either** `harness: <registry-key>` **or** the legacy `provider: <driver>`, returns a fully-resolved `Agent` whose `.harness` is the **driver string** (so `get_driver(agent.harness)` works) with credentials injected into `.provider` and `.prompt` set from the mapping's `prompt` key.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_harness_registry.py — append
import pytest
from aegis.config import ConfigError
from aegis.config.harnesses import merge_harnesses, resolve_agent_entry, \
    HarnessRegistration


HN = merge_harnesses({
    "openrouter": HarnessRegistration(
        name="openrouter", driver="lovelaice",
        base_url="https://openrouter.ai/api/v1",
        api_key_file="~/key.txt", default_model="qwen/qwen3-32b"),
})


def test_harness_ref_resolves_to_driver_and_creds():
    a = resolve_agent_entry(
        {"harness": "openrouter", "model": "qwen/qwen3-32b"}, HN)
    assert a.harness == "lovelaice"            # driver string for get_driver
    assert a.provider.base_url == "https://openrouter.ai/api/v1"
    assert a.provider.api_key_file == "~/key.txt"


def test_harness_ref_default_model_fallback():
    a = resolve_agent_entry({"harness": "openrouter"}, HN)
    assert a.model == "qwen/qwen3-32b"


def test_legacy_provider_shape_still_resolves():
    a = resolve_agent_entry(
        {"provider": "claude-code", "model": "opus", "effort": "high"}, HN)
    assert a.harness == "claude-code"
    assert a.effort.value == "high"


def test_prompt_field_captured():
    a = resolve_agent_entry(
        {"harness": "claude-code", "model": "opus",
         "prompt": ".aegis/personas/reviewer.md"}, HN)
    assert a.prompt == ".aegis/personas/reviewer.md"


def test_unknown_harness_fails_loud():
    with pytest.raises(ConfigError):
        resolve_agent_entry({"harness": "nope", "model": "x"}, HN)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_harness_registry.py -v`
Expected: FAIL — `resolve_agent_entry` undefined; `Agent` has no `prompt` field.

- [ ] **Step 3: Implement**

Add the `prompt` field to `Agent` in `src/aegis/config/__init__.py` (after `permission`):

```python
    prompt: str | None = None   # optional persona system-prompt file path
```

Add resolution in `src/aegis/config/harnesses.py`:

```python
from aegis.config import (
    Agent, ClaudeCode, ConfigError, Effort, GeminiCLI, Lovelaice, OpenCode,
    Permission,
)

_PROVIDER_BY_DRIVER = {
    "claude-code": ClaudeCode, "gemini": GeminiCLI,
    "opencode": OpenCode, "lovelaice": Lovelaice,
}


def resolve_agent_entry(
    body: dict, harnesses: dict[str, HarnessRegistration],
) -> Agent:
    d = dict(body)
    prompt = d.pop("prompt", None)
    # Legacy provider: shape → delegate to the driver directly.
    reg_key = d.pop("harness", None)
    if reg_key is None:
        # No harness ref → must be the legacy provider: shape.
        provider_name = d.get("provider")
        if provider_name is None:
            raise ConfigError(f"agent missing `harness` or `provider`: {body!r}")
        reg_key = provider_name          # provider name IS a driver name
        d.pop("provider", None)
    reg = harnesses.get(reg_key)
    if reg is None:
        raise ConfigError(
            f"unknown harness {reg_key!r}; known: {sorted(harnesses)}")
    cls = _PROVIDER_BY_DRIVER[reg.driver]
    model = d.get("model") or reg.default_model
    if not model:
        raise ConfigError(
            f"agent on harness {reg_key!r} has no model and the harness "
            f"declares no default_model")
    kw: dict = {"model": model}
    perm = d.get("permission") or (
        reg.permission_default.value if reg.permission_default else None)
    if perm is not None:
        kw["permission"] = perm
    if cls is ClaudeCode and d.get("effort") is not None:
        kw["effort"] = d["effort"]
    if cls is Lovelaice:
        if reg.base_url is not None:
            kw["base_url"] = reg.base_url
        if reg.api_key_file is not None:
            kw["api_key_file"] = reg.api_key_file
    agent = Agent(provider=cls(**kw))
    agent.prompt = prompt
    return agent
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_harness_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aegis/config/__init__.py src/aegis/config/harnesses.py tests/test_harness_registry.py
git commit -m "feat(config): resolve agent entries against harness registry + persona field"
```

### Task 5: Load `harnesses:` in the YAML loader + wire resolution

**Files:**
- Modify: `src/aegis/config/yaml_loader.py`
- Test: `tests/test_yaml_loader_harnesses.py` (create)

**Interfaces:**
- Consumes: `merge_harnesses`, `resolve_agent_entry`, `HarnessRegistration`.
- Produces: `AegisConfig.harnesses: dict[str, HarnessRegistration]`; `_agent_from_dict` replaced by registry-aware resolution; `harnesses:` overlays under `.aegis/harnesses/*.yaml`; fail-loud on unknown driver.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_yaml_loader_harnesses.py
import pytest
from aegis.config import ConfigError
from aegis.config.yaml_loader import load_config


def _write(tmp_path, text):
    (tmp_path / ".aegis.yaml").write_text(text)
    return tmp_path


def test_harness_ref_agent_loads(tmp_path):
    _write(tmp_path, """
harnesses:
  fast:
    driver: opencode
    default_model: opencode/mimo-v2.5-free
default_agent: quick
agents:
  quick:
    harness: fast
""")
    cfg = load_config(tmp_path)
    a = cfg.agents["quick"]
    assert a.harness == "opencode"
    assert a.model == "opencode/mimo-v2.5-free"
    assert "fast" in cfg.harnesses


def test_legacy_provider_agent_still_loads(tmp_path):
    _write(tmp_path, """
default_agent: main
agents:
  main:
    provider: claude-code
    model: opus
    effort: high
""")
    cfg = load_config(tmp_path)
    assert cfg.agents["main"].harness == "claude-code"


def test_unknown_driver_fails_loud(tmp_path):
    _write(tmp_path, """
harnesses:
  bad:
    driver: not-a-driver
default_agent: x
agents:
  x: { harness: bad, model: m }
""")
    with pytest.raises(ConfigError):
        load_config(tmp_path)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_yaml_loader_harnesses.py -v`
Expected: FAIL — loader ignores `harnesses:`; `harness:`-shaped agents raise "missing provider".

- [ ] **Step 3: Implement**

In `yaml_loader.py`:

1. Add `harnesses: dict[str, HarnessRegistration] = field(default_factory=dict)` to `AegisConfig`.
2. Add `"harnesses"` to `_SECTIONS` so overlays under `.aegis/harnesses/*.yaml` are collected.
3. Parse + validate harnesses before agents, and resolve agents through the registry:

```python
from aegis.config.harnesses import (
    HarnessRegistration, merge_harnesses, resolve_agent_entry)

_VALID_DRIVERS = {"claude-code", "gemini", "opencode", "lovelaice"}


def _harness_from_dict(name: str, d: dict) -> HarnessRegistration:
    driver = d.get("driver")
    if driver not in _VALID_DRIVERS:
        raise ConfigError(
            f"harness {name!r}: unknown driver {driver!r}; "
            f"known: {sorted(_VALID_DRIVERS)}")
    return HarnessRegistration(
        name=name, driver=driver,
        base_url=d.get("base_url"), api_key_file=d.get("api_key_file"),
        default_model=d.get("default_model"),
        permission_default=(Permission(d["permission_default"])
                            if d.get("permission_default") else None))
```

In `load_config`, add `"harnesses"` to the inline dict + merged sections, then:

```python
    explicit_harnesses = {
        k: _harness_from_dict(k, dict(v))
        for k, v in merged["harnesses"].items()}
    harnesses = merge_harnesses(explicit_harnesses)
    agents = {k: resolve_agent_entry(dict(v), harnesses)
              for k, v in merged["agents"].items()}
```

Delete the old `_agent_from_dict` + `_PROVIDERS` map (superseded by `resolve_agent_entry`). Add `harnesses=harnesses` to the `AegisConfig(...)` return. Import `Permission` from `aegis.config`.

- [ ] **Step 4: Run the loader tests + the full config suite**

Run: `uv run python -m pytest tests/test_yaml_loader_harnesses.py tests/test_harness_registry.py -v`
Then regression: `uv run python -m pytest -q -m "not live" -k "config or yaml or loader"`
Expected: PASS (fix any test that constructed agents via the removed `_agent_from_dict`).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/config/yaml_loader.py tests/test_yaml_loader_harnesses.py
git commit -m "feat(config): load harnesses: registry + resolve agents through it"
```

---

## VS3 — Personas (system prompt injection)

### Task 6: Persona read helper + claude injection

**Files:**
- Create: `src/aegis/config/persona.py`
- Modify: `src/aegis/drivers/claude.py`
- Test: `tests/test_persona.py` (create)

**Interfaces:**
- Produces:
  - `read_persona(agent: Agent, cwd: str) -> str | None` — reads `agent.prompt` (project-root-relative or `~`-expanded); raises `ConfigError` when the path is set but unreadable; returns `None` when `agent.prompt` is falsy.
  - `ClaudeDriver.build_argv` appends a **second** `--append-system-prompt <persona>` after the primer append when a persona exists.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_persona.py
import pytest
from aegis.config import Agent, ClaudeCode, ConfigError
from aegis.config.persona import read_persona


def test_reads_persona_file(tmp_path):
    p = tmp_path / "reviewer.md"
    p.write_text("You are a terse reviewer.")
    agent = Agent(provider=ClaudeCode(model="opus"))
    agent.prompt = "reviewer.md"
    assert read_persona(agent, str(tmp_path)) == "You are a terse reviewer."


def test_none_when_no_prompt(tmp_path):
    agent = Agent(provider=ClaudeCode(model="opus"))
    assert read_persona(agent, str(tmp_path)) is None


def test_missing_file_fails_loud(tmp_path):
    agent = Agent(provider=ClaudeCode(model="opus"))
    agent.prompt = "nope.md"
    with pytest.raises(ConfigError):
        read_persona(agent, str(tmp_path))


def test_claude_argv_appends_persona(tmp_path):
    from aegis.drivers.claude import ClaudeDriver
    p = tmp_path / "persona.md"
    p.write_text("PERSONA-TEXT")
    agent = Agent(provider=ClaudeCode(model="opus"))
    agent.prompt = "persona.md"
    argv = ClaudeDriver().build_argv(agent, str(tmp_path), "", "handle")
    # two --append-system-prompt: primer first, persona second
    idxs = [i for i, a in enumerate(argv) if a == "--append-system-prompt"]
    assert len(idxs) == 2
    assert argv[idxs[1] + 1] == "PERSONA-TEXT"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_persona.py -v`
Expected: FAIL — `aegis.config.persona` missing; claude argv has one append only.

- [ ] **Step 3: Implement**

```python
# src/aegis/config/persona.py
from __future__ import annotations

from pathlib import Path

from aegis.config import Agent, ConfigError


def read_persona(agent: Agent, cwd: str) -> str | None:
    rel = getattr(agent, "prompt", None)
    if not rel:
        return None
    p = Path(rel).expanduser()
    if not p.is_absolute():
        p = Path(cwd) / p
    try:
        return p.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"persona prompt file {p} is unreadable: {e}") from e
```

In `claude.py` `build_argv`, after the primer `--append-system-prompt` entry, append the persona:

```python
from aegis.config.persona import read_persona
# ... inside build_argv, build the list then:
        argv = [ ... existing list ending with the primer append ... ]
        persona = read_persona(agent, cwd)
        if persona:
            argv += ["--append-system-prompt", persona]
        return argv
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_persona.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aegis/config/persona.py src/aegis/drivers/claude.py tests/test_persona.py
git commit -m "feat(persona): read persona file + claude 2nd append-system-prompt"
```

### Task 7: ACP persona injection (first-turn prepend + native seams)

**Files:**
- Modify: `src/aegis/drivers/acp.py` (`AcpSession`, `AcpDriver`)
- Modify: `src/aegis/drivers/opencode.py` (fold persona into `instructions`)
- Modify: `src/aegis/drivers/lovelaice.py` (`LOVELAICE_SYSTEM_PROMPT` env)
- Test: `tests/test_persona_acp.py` (create)

**Interfaces:**
- Consumes: `read_persona`, `AcpSession.send`, `AcpDriver.session`.
- Produces: `AcpSession` gains a `persona: str | None` ctor arg; on the **first** `send()` it prepends the persona as a leading `{"type":"text"}` block. `AcpDriver.session/resume` pass `persona=read_persona(agent, cwd)` through. `OpenCodeDriver.extra_env` additionally sets `instructions` in the config JSON when a persona exists (probe: inline text vs temp file); when native `instructions` is wired, opencode suppresses the first-turn prepend (pass `persona=None` to the session so it isn't double-applied). `LovelaiceDriver.extra_env` sets `LOVELAICE_SYSTEM_PROMPT`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_persona_acp.py
import pytest
from aegis.drivers.acp import AcpSession
from aegis.config import Agent, GeminiCLI


class _StubConn:
    def __init__(self):
        self.prompts = []
    async def prompt(self, *, session_id, prompt):
        self.prompts.append(prompt)
        class R:  # minimal PromptResponse-ish
            stop_reason = "end_turn"; usage = None; field_meta = None
        return R()


@pytest.mark.asyncio
async def test_first_send_prepends_persona():
    agent = Agent(provider=GeminiCLI(model="gemini-2.5-pro"))
    sess = AcpSession(agent, cwd=".", mcp_url="", handle="h",
                      persona="PERSONA-X")
    sess._conn = _StubConn()
    sess._session_id = "sid"
    await sess.send("hello")
    blocks = sess._conn.prompts[0]
    assert blocks[0]["text"] == "PERSONA-X"
    assert blocks[-1]["text"] == "hello"
    # second turn does NOT re-inject
    await sess.send("again")
    assert all(b["text"] != "PERSONA-X" for b in sess._conn.prompts[1])
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_persona_acp.py -v`
Expected: FAIL — `AcpSession.__init__` takes no `persona`; no prepend logic.

- [ ] **Step 3: Implement**

In `AcpSession.__init__`, add `persona: str | None = None` param → `self._persona = persona`; add `self._persona_sent = False`. In `send()`, build the prompt blocks:

```python
        blocks = [{"type": "text", "text": text}]
        if self._persona and not self._persona_sent:
            blocks = [{"type": "text", "text": self._persona}] + blocks
            self._persona_sent = True
        resp = await self._conn.prompt(session_id=self._session_id,
                                       prompt=blocks)
```

In `AcpDriver.session`/`resume`, compute `persona = read_persona(agent, cwd)` and pass `persona=persona` into `self.SESSION_CLS(...)`. For `OpenCodeDriver`: if the persona is wired via `instructions` in `extra_env`, override `session()` to pass `persona=None` (so it isn't double-applied); otherwise leave the base behavior (first-turn prepend). For `LovelaiceDriver.extra_env`, add:

```python
        persona = read_persona(agent, "")  # cwd not needed for abs/~ paths;
        # for project-relative persona paths, resolve in session() instead
        if persona:
            env["LOVELAICE_SYSTEM_PROMPT"] = persona
```

(Prefer resolving persona once in `AcpDriver.session` and threading it to both `extra_env` and the session; if that requires a signature change, keep the first-turn prepend as the guaranteed path and treat `instructions`/env as best-effort — the test above covers the guaranteed path.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_persona_acp.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aegis/drivers/acp.py src/aegis/drivers/opencode.py src/aegis/drivers/lovelaice.py tests/test_persona_acp.py
git commit -m "feat(persona): ACP first-turn prepend + native seams (opencode/lovelaice)"
```

---

## VS4 — Per-session model/effort selection

### Task 8: OpenCode model catalog for the picker

**Files:**
- Modify: `src/aegis/models/__init__.py`
- Test: `tests/test_models_opencode_catalog.py` (create)

**Interfaces:**
- Consumes: the real `opencode models` CLI (best-effort), `models_for`.
- Produces: `opencode_models(cache_ttl=86400) -> list[str]` — cached list of `opencode/...` ids from `opencode models`; empty list when the CLI is absent or errors. `models_for("opencode")` returns these as `(id, id)` tuples when the models.yaml opencode provider is empty.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_opencode_catalog.py
from aegis.models import opencode_models


def test_opencode_models_returns_list(monkeypatch):
    monkeypatch.setattr(
        "aegis.models._run_opencode_models",
        lambda: "opencode/mimo-v2.5-free\nopencode/gpt-5.1\n")
    got = opencode_models()
    assert "opencode/mimo-v2.5-free" in got


def test_opencode_models_empty_when_cli_absent(monkeypatch):
    monkeypatch.setattr("aegis.models._run_opencode_models",
                        lambda: None)
    assert opencode_models() == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_models_opencode_catalog.py -v`
Expected: FAIL — `opencode_models` / `_run_opencode_models` undefined.

- [ ] **Step 3: Implement**

```python
# src/aegis/models/__init__.py — add
import shutil
import subprocess


def _run_opencode_models() -> str | None:
    if shutil.which("opencode") is None:
        return None
    try:
        out = subprocess.run(["opencode", "models"], capture_output=True,
                             text=True, timeout=15)
        return out.stdout if out.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def opencode_models() -> list[str]:
    """Live `opencode models` ids (best-effort). Empty when CLI absent."""
    raw = _run_opencode_models()
    if not raw:
        return []
    return [ln.strip() for ln in raw.splitlines() if ln.strip()]
```

(A ~24h file cache under `cache_path().parent / "opencode-models.txt"` can be added the same way `maybe_refresh` caches `models.yaml`; the test stubs `_run_opencode_models`, so caching is an internal detail.)

Wire `models_for("opencode")` to fall back to `opencode_models()` when the models.yaml opencode provider yields nothing.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_models_opencode_catalog.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aegis/models/__init__.py tests/test_models_opencode_catalog.py
git commit -m "feat(models): live opencode models catalog for the picker"
```

### Task 9: Two-tier harness→model→effort picker

**Files:**
- Modify: `src/aegis/tui/picker.py`
- Test: `tests/test_picker_harness.py` (create)

**Interfaces:**
- Consumes: `AgentPicker` (existing, returns a profile slug), `models_for`, `opencode_models`, the loaded `AegisConfig.harnesses`.
- Produces: `build_picker_rows(presets: list[str], harnesses: dict[str, HarnessRegistration]) -> list[tuple[str,str]]` — pure function returning `(id, label)` rows: presets first, then `harness:<name>` rows. Selecting a `harness:<name>` row triggers the model→effort sub-flow, resolving to a **transient `Agent`** via `resolve_agent_entry({"harness": name, "model": chosen, "effort": chosen}, harnesses)`.

Keep the modal chain thin — the pure `build_picker_rows` + a `resolve_transient_agent(name, model, effort, harnesses) -> Agent` are the testable units; the Textual wiring calls them.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_picker_harness.py
from aegis.config.harnesses import merge_harnesses, HarnessRegistration
from aegis.tui.picker import build_picker_rows, resolve_transient_agent


HN = merge_harnesses({})


def test_rows_presets_then_harnesses():
    rows = build_picker_rows(["opus", "quick"], HN)
    ids = [r[0] for r in rows]
    assert ids[:2] == ["opus", "quick"]
    assert "harness:opencode" in ids


def test_resolve_transient_agent():
    a = resolve_transient_agent(
        "opencode", "opencode/mimo-v2.5-free", None, HN)
    assert a.harness == "opencode"
    assert a.model == "opencode/mimo-v2.5-free"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_picker_harness.py -v`
Expected: FAIL — the two helpers don't exist.

- [ ] **Step 3: Implement**

```python
# src/aegis/tui/picker.py — add
from aegis.config.harnesses import resolve_agent_entry


def build_picker_rows(presets, harnesses):
    rows = [(p, p) for p in presets]
    for name, reg in harnesses.items():
        rows.append((f"harness:{name}", f"⚙ {name} ({reg.driver})"))
    return rows


def resolve_transient_agent(harness_name, model, effort, harnesses):
    body = {"harness": harness_name, "model": model}
    if effort is not None:
        body["effort"] = effort
    return resolve_agent_entry(body, harnesses)
```

Then extend `AgentPicker.compose` to render `build_picker_rows(...)`; on selecting a `harness:` id, push a model `OptionList` (from `models_for(reg.driver)` or `opencode_models()` for opencode, always with a `<custom>` free-text row), then an effort `OptionList` **only** when `reg.driver == "claude-code"`, and `dismiss()` with the transient `Agent`. (Widget wiring is not unit-tested; the pure helpers above are.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_picker_harness.py -v`
Expected: PASS

- [ ] **Step 5: Manual smoke + commit**

Smoke: `aegis` in a project with a `harnesses:` block, open the picker, pick a harness → model → confirm a tab spawns. Then:

```bash
git add src/aegis/tui/picker.py tests/test_picker_harness.py
git commit -m "feat(tui): two-tier harness→model→effort agent picker"
```

### Task 10: `aegis_spawn` + spawn command gain model/effort/prompt

**Files:**
- Modify: `src/aegis/mcp/bridge.py`, `src/aegis/core/manager.py`, `src/aegis/mcp/server.py`
- Modify: `src/aegis/commands/builtins/core.py` (spawn slash command)
- Test: `tests/test_spawn_overrides.py` (create)

**Interfaces:**
- Consumes: `SessionManager.spawn(profile, ...)` at `manager.py:163`; `resolve_transient_agent`; the loaded harnesses.
- Produces: `spawn` accepts optional `model: str | None`, `effort: str | None`, `prompt: str | None`. When any is set, the resolved profile is overlaid (model/effort override; `prompt` overrides persona). `aegis_spawn` MCP tool exposes `model`/`effort`/`prompt` params; the spawn slash command gains `--model`/`--effort` args.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_spawn_overrides.py
import pytest
from aegis.config import Agent, ClaudeCode


def test_overlay_model_and_effort():
    from aegis.core.manager import _overlay_agent  # helper to add
    base = Agent(provider=ClaudeCode(model="opus", effort="high"))
    out = _overlay_agent(base, model="sonnet", effort="low", prompt=None)
    assert out.model == "sonnet"
    assert out.effort.value == "low"


def test_overlay_prompt_only():
    from aegis.core.manager import _overlay_agent
    base = Agent(provider=ClaudeCode(model="opus"))
    out = _overlay_agent(base, model=None, effort=None,
                         prompt="p.md")
    assert out.prompt == "p.md"
    assert out.model == "opus"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_spawn_overrides.py -v`
Expected: FAIL — `_overlay_agent` undefined.

- [ ] **Step 3: Implement**

Add `_overlay_agent` in `manager.py`:

```python
def _overlay_agent(base, *, model, effort, prompt):
    from aegis.config import Agent
    data = base.model_dump()
    if model is not None:
        data["model"] = model
        if data.get("provider"):
            data["provider"]["model"] = model
    if effort is not None:
        data["effort"] = effort
        if data.get("provider", {}).get("name") == "claude-code":
            data["provider"]["effort"] = effort
    if prompt is not None:
        data["prompt"] = prompt
    return Agent(**data)
```

Thread optional `model`/`effort`/`prompt` through `SessionManager.spawn` (apply `_overlay_agent` to the looked-up profile before building the session). Add the params to `bridge.py` `spawn` protocol signatures, to `aegis_spawn` in `server.py` (extend the docstring Args), and `--model`/`--effort` to the spawn slash command.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_spawn_overrides.py -v`
Then: `uv run python -m pytest -q -m "not live" -k "spawn or manager or mcp"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/bridge.py src/aegis/core/manager.py src/aegis/mcp/server.py src/aegis/commands/builtins/core.py tests/test_spawn_overrides.py
git commit -m "feat(spawn): optional model/effort/prompt overrides on aegis_spawn + /spawn"
```

---

## VS5 — Config authoring

### Task 11: `aegis config harness {add,list,remove}`

**Files:**
- Modify: `src/aegis/config/edit.py`, `src/aegis/cli_config.py`
- Test: `tests/test_config_edit_harness.py` (create)

**Interfaces:**
- Consumes: `_load`, `_validate_and_dump`, `_atomic_write` in `edit.py`; the ruamel round-trip machinery.
- Produces: `add_harness(root, name, *, driver, base_url=None, api_key_file=None, default_model=None, permission_default=None)`, `remove_harness(root, name)`; also extend `_VALID_PROVIDERS` in `edit.py` to include `lovelaice` and let `add_agent` accept `harness=<key>`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_edit_harness.py
import pytest
from aegis.config import ConfigError
from aegis.config.edit import add_harness, remove_harness
from aegis.config.yaml_loader import load_config


def test_add_harness_roundtrips(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: m\nagents:\n  m: { provider: claude-code, model: opus }\n")
    add_harness(tmp_path, "openrouter", driver="lovelaice",
                base_url="https://openrouter.ai/api/v1",
                default_model="qwen/qwen3-32b")
    cfg = load_config(tmp_path)
    assert cfg.harnesses["openrouter"].base_url.endswith("/v1")


def test_add_harness_unknown_driver(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: m\nagents:\n  m: { provider: claude-code, model: opus }\n")
    with pytest.raises(ConfigError):
        add_harness(tmp_path, "bad", driver="nope")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_config_edit_harness.py -v`
Expected: FAIL — `add_harness` undefined.

- [ ] **Step 3: Implement**

```python
# src/aegis/config/edit.py — add
_VALID_DRIVERS = {"claude-code", "gemini", "opencode", "lovelaice"}


def add_harness(root, name, *, driver, base_url=None, api_key_file=None,
                default_model=None, permission_default=None):
    if driver not in _VALID_DRIVERS:
        raise ConfigError(
            f"unknown driver {driver!r}; known: {sorted(_VALID_DRIVERS)}")
    base = root / ".aegis.yaml"
    data = _load(base)
    harnesses = data.setdefault("harnesses", {})
    if name in harnesses:
        raise ConfigError(f"harness {name!r} already exists in {base}")
    entry = {"driver": driver}
    for k, v in (("base_url", base_url), ("api_key_file", api_key_file),
                 ("default_model", default_model),
                 ("permission_default", permission_default)):
        if v is not None:
            entry[k] = v
    harnesses[name] = entry
    _atomic_write(base, _validate_and_dump(root, data))


def remove_harness(root, name):
    base = root / ".aegis.yaml"
    data = _load(base)
    if name not in (data.get("harnesses") or {}):
        raise ConfigError(f"harness {name!r} not found in {base}")
    del data["harnesses"][name]
    _atomic_write(base, _validate_and_dump(root, data))
```

Add a `harness_app` typer sub-app in `cli_config.py` mirroring `queue_app` (`list`/`add`/`remove`), and mount it on `app`. Update `add_agent` to accept `harness` as an alternative to `provider` (pass through to the entry as `harness:` instead of `provider:`).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_config_edit_harness.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aegis/config/edit.py src/aegis/cli_config.py tests/test_config_edit_harness.py
git commit -m "feat(cli): aegis config harness add/list/remove"
```

### Task 12: `AddAgentModal` harness-aware

**Files:**
- Modify: `src/aegis/tui/config_panel.py`
- Test: `tests/test_config_panel_harness.py` (create — pure helpers only)

**Interfaces:**
- Consumes: loaded `AegisConfig.harnesses`, `models_for`/`opencode_models`, `add_agent`.
- Produces: the modal's provider `Select` is populated from the registered harnesses (label `name (driver)`) instead of the hard-coded driver list; selecting a harness repopulates the model `Select` from that harness's driver catalog; an optional persona-path `Input` writes `prompt:`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_panel_harness.py
from aegis.config.harnesses import merge_harnesses
from aegis.tui.config_panel import _harness_options


def test_harness_options_include_registered():
    opts = _harness_options(merge_harnesses({}))
    labels = [o[0] for o in opts]
    assert any("opencode" in lbl for lbl in labels)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_config_panel_harness.py -v`
Expected: FAIL — `_harness_options` undefined.

- [ ] **Step 3: Implement**

Add `_harness_options(harnesses) -> list[tuple[str,str]]` returning `(f"{name} ({reg.driver})", name)` rows, and use it to build the provider `Select` in `AddAgentModal.compose` (replacing the hard-coded `value="claude-code"` list). On submit, resolve the selected harness → write `harness: <name>` (or keep `provider:` for the implicit driver-name entries) + optional `prompt:`. Reuse `models_for(reg.driver)`/`opencode_models()` for the model options.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_config_panel_harness.py -v`
Expected: PASS

- [ ] **Step 5: Manual smoke + commit**

Smoke: `aegis` with no `.aegis.yaml` → ConfigPanel opens → add an agent on a registered harness → confirm it writes valid YAML that reloads.

```bash
git add src/aegis/tui/config_panel.py tests/test_config_panel_harness.py
git commit -m "feat(tui): AddAgentModal is harness-aware (registry + persona path)"
```

---

## Final verification

- [ ] Full hermetic suite: `uv run python -m pytest -q -m "not live"` (re-run any TUI/watchdog flake in isolation before treating as real — inotify limit on zion).
- [ ] Live multiprovider: `uv run python -m pytest tests/test_drivers_multiprovider_live.py -v -m live` (opencode free-model round-trip honored).
- [ ] Manual end-to-end (the spec's vertical slice): register an `opencode` harness, author a `fast-free` agent on `opencode/deepseek-v4-flash-free`, spawn it, confirm the free model answers.
- [ ] Update `AGENTS.md` (drivers/config bullets) + `know-how/native-lovelaice-agent.md` if the persona env/opencode-config mechanism shifted from the plan.
- [ ] Flip the spec `Status:` header to `implemented` in the same commit as the AGENTS.md update.

## Self-review notes

- **Spec coverage:** OpenCode model selection (T1–2), harness registry + implicit/back-compat (T3–5), personas incl. per-driver injection (T6–7), per-session picker + non-interactive overrides (T8–10), config authoring CLI + ConfigPanel (T11–12). Non-goal (mid-session switch) intentionally excluded.
- **Resolution invariant:** every task keeps `Agent.harness` == a **driver string** post-resolution, so the three `get_driver(profile.harness)` sites in `cli.py` and `make_session` need no change.
- **Probe gates:** OpenCode config-injection mechanism (T2) and ACP native persona seams (T7) are confirmed against the real CLIs; the first-turn-prepend persona path is the guaranteed floor if native seams don't land.
