# aegis SSH Execution Hosts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an aegis session run its harness process on another machine over a persistent SSH connection, while the session, transcript, and MCP peer identity stay local.

**Architecture:** A `Launcher` seam is introduced underneath both driver families (`ClaudeSession`, `AcpSession`), which today both call `asyncio.create_subprocess_exec(*argv, cwd=…, env=…)`. `LocalLauncher` preserves that verbatim; `SshLauncher` wraps the same argv into `ssh -T <dest> 'cd <cwd> && exec <argv>'` over a shared `ControlMaster`, with a `-R` reverse tunnel carrying the local MCP port to the remote side so the remote agent is an ordinary peer. Host becomes a third orthogonal spawn axis beside agent profile and harness, resolved per-spawn into a `Place(host, cwd)` and never persisted.

**Tech Stack:** Python 3.13+, asyncio, ruamel.yaml, pydantic, Textual 8.x, FastMCP, pytest. Package management is `uv` (`uv pip install -e .`, `uv run pytest`) — never pip.

**Spec:** `docs/superpowers/specs/2026-08-04-aegis-ssh-execution-hosts-design.md`

## Global Constraints

- Python 3.13+. `from __future__ import annotations` at the top of every new module, matching the repo.
- **TDD, always**: failing test first, minimal implementation, commit per logical unit.
- **Commit to `main`.** aegis works on main — skip branch + PR unless asked.
- Run the fast suite with `uv run python -m pytest -q -m "not live"`. Never use `-k "not live"` — it matches `live` as a substring and silently eats unrelated test names.
- **A failing test is a real failure**, not flake to re-roll. The suite's historical flakiness was fixed in 0.25.0.
- No new third-party dependencies. `ssh` is invoked as a subprocess; do not add `paramiko`, `asyncssh`, or `fabric`.
- All new signatures that touch existing driver methods must be **defaulted**, so every existing call site and test keeps working untouched.
- English for all code, comments, identifiers, error strings, docstrings, and commit messages.
- Conventional commits (`feat`, `fix`, `docs`, `refactor`, `test`, `chore`), optional scope.
- Never `git add -A` / `git add .` / `git add -u`. Stage the explicit paths each task touches.
- New code lives in a new package `src/aegis/hosts/`, following the one-package-per-substrate layout the repo already uses for `locks/`, `groups/`, `queue/`.

---

## File Structure

**New package — `src/aegis/hosts/`**

| File | Responsibility |
|---|---|
| `models.py` | `HostSpec` (config entry) and `Place` (resolved host+cwd). Pure data, no I/O. |
| `resolve.py` | `resolve_place()` — the precedence rules turning spawn args + profile + config into a `Place`. Pure. |
| `launcher.py` | `Launcher` protocol, `LocalLauncher`, `SshLauncher`, and the pure argv-composition helpers. |
| `connection.py` | `HostConnection` — the SSH ControlMaster subprocess, allocated-port parse, preflight, health, teardown. |
| `registry.py` | `HostRegistry` — one `HostConnection` per host, synchronous `launcher_for`, async lazy open under a lock. |
| `errors.py` | `HostError`, `RemoteLinkLost`. |

**Modified**

| File | Change |
|---|---|
| `src/aegis/drivers/base.py` | `session`/`resume`/`fork` gain `launcher: Launcher = LOCAL`. |
| `src/aegis/drivers/claude.py` | `ClaudeSession` takes a launcher; `start()` delegates; persona resolves against the local root. |
| `src/aegis/drivers/acp.py` | Same two changes for `AcpSession` / `AcpDriver`. |
| `src/aegis/config/yaml_loader.py` | `hosts:` section, overlays, validation. |
| `src/aegis/config/persona.py` | `read_persona` gains an explicit `root` meaning. |
| `src/aegis/cli.py` | `_session_factory` takes a `HostRegistry` and a `place`. |
| `src/aegis/core/manager.py` | `_sync_spawn` / `spawn` / `fork` gain `host` + `cwd`; `AgentSession` carries `place`. |
| `src/aegis/core/session.py` | `AgentSession.place`; `RemoteLinkLost` surfacing. |
| `src/aegis/locks/models.py` | `Claim.host`; `claims_overlap` host gate. |
| `src/aegis/locks/registry.py` | `claim()` takes `host`. |
| `src/aegis/mcp/bridge.py` | `SessionInfo.host`. |
| `src/aegis/mcp/server.py` | `aegis_claim` derives host; `aegis_spawn` gains `host`/`cwd`; `aegis_list_sessions` reports host. |
| `src/aegis/render_shared.py` | `file_target` gains `host`; returns `None` off-host. |
| `src/aegis/commands/builtins/core.py` | `/spawn <agent>@<host>[:<cwd>]`; new `/reconnect`. |
| `src/aegis/tui/picker.py` | Host tier. |
| `src/aegis/cli_config.py` | `aegis config host add|remove|list`. |
| `src/aegis/config/edit.py` | Comment-preserving host add/remove. |

**New tests**

`tests/test_hosts_launcher.py`, `tests/test_hosts_config.py`, `tests/test_hosts_resolve.py`, `tests/test_hosts_connection.py`, `tests/test_hosts_spawn_wiring.py`, `tests/test_hosts_claims.py`, `tests/test_hosts_render.py`, `tests/test_hosts_commands.py`, `tests/test_ssh_hosts_live.py`.

---

## Task 1: The `Launcher` seam (local only, zero behaviour change)

This is the walking skeleton. It changes no behaviour — it proves the seam exists and that both driver families are launcher-agnostic.

**Files:**
- Create: `src/aegis/hosts/__init__.py`
- Create: `src/aegis/hosts/launcher.py`
- Create: `src/aegis/hosts/errors.py`
- Modify: `src/aegis/drivers/base.py`
- Modify: `src/aegis/drivers/claude.py` (`ClaudeSession.__init__`, `ClaudeSession.start`, `ClaudeDriver.session/resume/fork`)
- Modify: `src/aegis/drivers/acp.py` (`AcpSession.__init__`, `AcpSession.start`, `AcpDriver.session/resume`)
- Test: `tests/test_hosts_launcher.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `aegis.hosts.launcher.Launcher` — Protocol with `host_key: str`, `local_root: str | None`, `async spawn(argv: list[str], *, cwd: str, env: dict[str, str] | None) -> asyncio.subprocess.Process`, and `persona_root(cwd: str) -> str`.
  - `aegis.hosts.launcher.LocalLauncher(local_root: str | None = None)`.
  - `aegis.hosts.launcher.LOCAL` — a module-level `LocalLauncher()` used as the default argument everywhere.
  - `aegis.hosts.errors.HostError(Exception)`.
  - `HarnessDriver.session(agent, cwd, mcp_url, handle, launcher: Launcher = LOCAL)` and the same trailing parameter on `resume` and `fork`.

- [x] **Step 1: Write the failing test**

Create `tests/test_hosts_launcher.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from aegis.hosts.launcher import LOCAL, LocalLauncher


def test_local_launcher_identity():
    assert LOCAL.host_key == "local"
    assert LocalLauncher().host_key == "local"


def test_persona_root_falls_back_to_cwd():
    # With no explicit local_root, persona resolution is unchanged from
    # today: relative persona paths resolve under the session cwd.
    assert LocalLauncher().persona_root("/tmp/proj") == "/tmp/proj"


def test_persona_root_prefers_explicit_local_root():
    lau = LocalLauncher(local_root="/home/me/proj")
    assert lau.persona_root("/remote/tree") == "/home/me/proj"


def test_local_launcher_spawns_a_real_process():
    async def go():
        proc = await LocalLauncher().spawn(
            ["sh", "-c", "printf hello"], cwd="/tmp", env=None)
        out, _ = await proc.communicate()
        return out

    assert asyncio.run(go()) == b"hello"


def test_local_launcher_passes_cwd():
    async def go():
        proc = await LocalLauncher().spawn(
            ["sh", "-c", "pwd"], cwd="/tmp", env=None)
        out, _ = await proc.communicate()
        return out.decode().strip()

    assert asyncio.run(go()).endswith("/tmp")
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_hosts_launcher.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.hosts'`

- [x] **Step 3: Create the package and the local launcher**

Create `src/aegis/hosts/__init__.py`:

```python
"""SSH execution hosts — running a harness process on another machine.

The local aegis keeps the session, the transcript, and the MCP peer
identity; only the harness subprocess runs elsewhere. See
``docs/superpowers/specs/2026-08-04-aegis-ssh-execution-hosts-design.md``.
"""
from __future__ import annotations
```

Create `src/aegis/hosts/errors.py`:

```python
from __future__ import annotations


class HostError(Exception):
    """A remote host could not be prepared or reached."""
```

Create `src/aegis/hosts/launcher.py`:

```python
"""The process-launch seam shared by every harness driver.

Both driver families converge on the same shape — an argv, a cwd, an
optional env, and three pipes. ``Launcher`` is that shape as an
interface, so remoteness lives in one place instead of once per driver.
"""
from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

# claude stream-json and ACP JSON-RPC both put whole tool payloads on one
# line; asyncio's 64 KiB default is far too small. Mirrors
# ``drivers.claude._STREAM_LIMIT``.
STREAM_LIMIT = 16 * 1024 * 1024


@runtime_checkable
class Launcher(Protocol):
    """Starts a harness process somewhere and hands back its pipes."""

    host_key: str
    local_root: str | None

    async def spawn(self, argv: list[str], *, cwd: str,
                    env: dict[str, str] | None
                    ) -> asyncio.subprocess.Process: ...

    def persona_root(self, cwd: str) -> str: ...


class LocalLauncher:
    """Today's behaviour, unchanged: exec here, in this process tree."""

    host_key = "local"

    def __init__(self, local_root: str | None = None) -> None:
        self.local_root = local_root

    def persona_root(self, cwd: str) -> str:
        """Where a relative persona path resolves.

        A persona file always lives in the LOCAL project, even when the
        harness runs on another box — so drivers resolve it against this
        rather than against the (possibly remote) session cwd. Falling
        back to ``cwd`` keeps pre-existing local behaviour identical.
        """
        return self.local_root or cwd

    async def spawn(self, argv: list[str], *, cwd: str,
                    env: dict[str, str] | None
                    ) -> asyncio.subprocess.Process:
        kw: dict = dict(
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_LIMIT,
        )
        if env is not None:
            kw["env"] = env
        return await asyncio.create_subprocess_exec(*argv, **kw)


LOCAL = LocalLauncher()
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_hosts_launcher.py -q`
Expected: PASS (5 tests)

- [x] **Step 5: Thread the launcher through `HarnessDriver`**

In `src/aegis/drivers/base.py`, add the import and the defaulted parameter to the three factory methods. Replace the `build_argv`/`session`/`resume`/`fork` block:

```python
from aegis.hosts.launcher import LOCAL, Launcher


class HarnessDriver(abc.ABC):
    supports_resume: bool = False
    supports_fork: bool = False
    supports_oneshot: bool = False

    @abc.abstractmethod
    def build_argv(self, agent: Agent, cwd: str,
                   mcp_url: str, handle: str) -> list[str]: ...

    @abc.abstractmethod
    def session(self, agent: Agent, cwd: str, mcp_url: str, handle: str,
                launcher: Launcher = LOCAL) -> HarnessSession: ...

    def resume(self, agent: Agent, cwd: str, mcp_url: str, handle: str,
               session_id: str,
               launcher: Launcher = LOCAL) -> HarnessSession:
        raise NotImplementedError(
            f"{type(self).__name__} does not support session resume")

    def fork(self, agent: Agent, cwd: str, mcp_url: str, handle: str,
             session_id: str,
             launcher: Launcher = LOCAL) -> HarnessSession:
        raise NotImplementedError(
            f"{type(self).__name__} does not support session fork")
```

Leave the docstrings on `resume` and `fork` exactly as they are; only the signature line changes.

- [x] **Step 6: Make `ClaudeSession` launcher-driven**

In `src/aegis/drivers/claude.py`:

Add to the imports:

```python
from aegis.hosts.launcher import LOCAL, Launcher
```

In `ClaudeSession.__init__`, add the keyword and store it (after the `agent_profile` line):

```python
    def __init__(self, argv: list[str], cwd: str, *,
                 handle: str = "", harness: str = "claude-code",
                 agent_profile: str = "",
                 launcher: Launcher = LOCAL) -> None:
        self._argv = argv
        self._cwd = cwd
        self._handle = handle
        self._harness = harness
        self._agent_profile = agent_profile or handle
        self._launcher = launcher
        self._proc: asyncio.subprocess.Process | None = None
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._reader: asyncio.Task | None = None
        self._session_id: str | None = None
        self._control_seq: int = 0
        self._parser_state = ParserState()
```

Replace the body of `start()`:

```python
    async def start(self) -> None:
        argv, env = await self._apply_pre_spawn_hooks()
        self._proc = await self._launcher.spawn(argv, cwd=self._cwd, env=env)
        self._reader = asyncio.create_task(self._pump_stdout())
```

Note the ordering that must be preserved: `_apply_pre_spawn_hooks` still runs against the **inner** argv, before any wrapping. A hook that rewrites claude's flags must never have to know about ssh.

In `ClaudeDriver`, thread the launcher through all three factories, and resolve the persona against the launcher's local root:

```python
    def build_argv(self, agent: Agent, cwd: str, mcp_url: str, handle: str,
                   launcher: Launcher = LOCAL) -> list[str]:
        argv = [
            "claude", "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--replay-user-messages",
            "--verbose",  # required by claude with -p + stream-json output
            "--model", agent.model,
            "--effort", _EFFORT[agent.effort],
            "--permission-mode", _PERMISSION_MODE[agent.permission],
            "--mcp-config", mcp_config_json(mcp_url),
            "--strict-mcp-config",
            "--append-system-prompt", PRIMING.format(handle=handle),
        ]
        # Persona composes AFTER the primer so the agent still knows its
        # handle and can call back. It is read from the LOCAL project even
        # when the harness runs remotely.
        persona = read_persona(agent, launcher.persona_root(cwd))
        if persona:
            argv += ["--append-system-prompt", persona]
        return argv

    def session(self, agent: Agent, cwd: str, mcp_url: str, handle: str,
                launcher: Launcher = LOCAL) -> ClaudeSession:
        return ClaudeSession(
            self.build_argv(agent, cwd, mcp_url, handle, launcher), cwd,
            handle=handle, harness=agent.harness or "claude-code",
            launcher=launcher)

    def resume(self, agent: Agent, cwd: str, mcp_url: str, handle: str,
               session_id: str,
               launcher: Launcher = LOCAL) -> ClaudeSession:
        """Build a ClaudeSession that resumes an existing conversation."""
        argv = self.build_argv(agent, cwd, mcp_url, handle, launcher)
        # Insert --resume <session_id> right after the "claude -p" prefix
        resumed_argv = argv[:2] + ["--resume", session_id] + argv[2:]
        return ClaudeSession(resumed_argv, cwd,
                             handle=handle,
                             harness=agent.harness or "claude-code",
                             launcher=launcher)

    def fork(self, agent: Agent, cwd: str, mcp_url: str, handle: str,
             session_id: str,
             launcher: Launcher = LOCAL) -> ClaudeSession:
        """Build a ClaudeSession branching from an existing conversation.

        `--fork-session` is what keeps this from being a plain resume:
        "when resuming, create a new session ID". The parent's own id
        stays where it was, so the two conversations never share a log.
        """
        argv = self.build_argv(agent, cwd, mcp_url, handle, launcher)
        forked_argv = (argv[:2] + ["--fork-session", "--resume", session_id]
                       + argv[2:])
        return ClaudeSession(forked_argv, cwd,
                             handle=handle,
                             harness=agent.harness or "claude-code",
                             launcher=launcher)
```

`build_argv` gaining a defaulted 5th parameter is compatible with the abstract signature in `base.py` (Python does not enforce arity on overrides) and with every existing caller.

- [x] **Step 7: Make `AcpSession` launcher-driven**

In `src/aegis/drivers/acp.py`, add `from aegis.hosts.launcher import LOCAL, Launcher` to the imports, add `launcher: Launcher = LOCAL` to `AcpSession.__init__`'s keyword arguments, store `self._launcher = launcher` beside `self._extra_env`, and replace the process-creation block inside `start()` (the `kw: dict = dict(...)` through `create_subprocess_exec` lines) with:

```python
    async def start(self) -> None:
        argv, env = await self._apply_pre_spawn_hooks()
        if self._extra_env:
            base = env if env is not None else dict(os.environ)
            env = {**base, **self._extra_env}
        self._proc = await self._launcher.spawn(argv, cwd=self._cwd, env=env)
```

Everything after that line in `start()` (the stderr drain task, the `_AcpFilter` logging handler, the SDK handshake) is unchanged.

In `AcpDriver.session` and `AcpDriver.resume`, pass the launcher into the session class and resolve the persona against the local root:

```python
    def session(self, agent: Agent, cwd: str, mcp_url: str, handle: str,
                launcher: Launcher = LOCAL) -> AcpSession:
        s = self.SESSION_CLS(agent, cwd, mcp_url, handle,
                             extra_env=self.extra_env(agent),
                             persona=read_persona(
                                 agent, launcher.persona_root(cwd)),
                             launcher=launcher)
        # The session reads BASE_CMD from itself; provider sessions
        # override _argv if they need per-call argv tweaks.
        s.BASE_CMD = self.build_argv(agent, cwd, mcp_url, handle)
        return s

    def resume(self, agent: Agent, cwd: str, mcp_url: str, handle: str,
               session_id: str,
               launcher: Launcher = LOCAL) -> AcpSession:
        s = self.SESSION_CLS(agent, cwd, mcp_url, handle,
                             resume_session_id=session_id,
                             extra_env=self.extra_env(agent),
                             persona=read_persona(
                                 agent, launcher.persona_root(cwd)),
                             launcher=launcher)
        s.BASE_CMD = self.build_argv(agent, cwd, mcp_url, handle)
        return s
```

- [x] **Step 8: Add the launcher-agnosticism test**

Append to `tests/test_hosts_launcher.py`:

```python
class FakeLauncher:
    """Records what it was asked to spawn, then delegates locally."""

    host_key = "fake"
    local_root = None

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str, dict | None]] = []

    def persona_root(self, cwd: str) -> str:
        return cwd

    async def spawn(self, argv, *, cwd, env):
        self.calls.append((list(argv), cwd, env))
        return await LocalLauncher().spawn(
            ["sh", "-c", "sleep 30"], cwd=cwd, env=None)


def test_claude_session_uses_its_launcher(tmp_path):
    from aegis.drivers.claude import ClaudeSession

    fake = FakeLauncher()

    async def go():
        sess = ClaudeSession(["claude", "-p"], str(tmp_path),
                             handle="test-agent", launcher=fake)
        await sess.start()
        await sess.close()

    asyncio.run(go())
    assert len(fake.calls) == 1
    argv, cwd, _env = fake.calls[0]
    # The launcher sees the INNER argv — pre-spawn hooks have run, but no
    # transport wrapping has happened at this layer.
    assert argv == ["claude", "-p"]
    assert cwd == str(tmp_path)


def test_driver_defaults_to_the_local_launcher(tmp_path):
    from aegis.config import Agent
    from aegis.drivers.claude import ClaudeDriver

    agent = Agent(harness="claude-code", model="opus")
    sess = ClaudeDriver().session(
        agent, str(tmp_path), "http://127.0.0.1:1/mcp/", "test-agent")
    assert sess._launcher is LOCAL
```

- [x] **Step 9: Run the whole fast suite**

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS, with no new failures. This is the gate that proves the refactor changed no behaviour.

- [x] **Step 10: Commit**

```bash
git add src/aegis/hosts/__init__.py src/aegis/hosts/errors.py \
        src/aegis/hosts/launcher.py src/aegis/drivers/base.py \
        src/aegis/drivers/claude.py src/aegis/drivers/acp.py \
        tests/test_hosts_launcher.py
git commit -m "refactor(drivers): a Launcher seam under both driver families"
```

---

## Task 2: `hosts:` config section

**Files:**
- Create: `src/aegis/hosts/models.py`
- Modify: `src/aegis/config/yaml_loader.py` (`AegisConfig`, `_SECTIONS`, `load_config`)
- Test: `tests/test_hosts_config.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `aegis.hosts.models.HostSpec(name: str, ssh: str, cwd: str, ssh_opts: list[str] = [], remote_mcp_port: int | None = None)` — frozen dataclass.
  - `AegisConfig.hosts: dict[str, HostSpec]`.
  - `"hosts"` added to `yaml_loader._SECTIONS`, so `.aegis/hosts/*.yaml` overlays merge fail-loud like every other section.

- [x] **Step 1: Write the failing test**

Create `tests/test_hosts_config.py`:

```python
from __future__ import annotations

import pytest

from aegis.config import ConfigError
from aegis.config.yaml_loader import load_config


def _write(root, text: str):
    (root / ".aegis.yaml").write_text(text)
    return root


def test_hosts_section_loads(tmp_path):
    _write(tmp_path, """
hosts:
  vps:
    ssh: vps.apiad.net
    cwd: /home/apiad/Workspace
  smaug:
    ssh: smaug.local
    cwd: /home/apiad/work
    ssh_opts: ["-o", "ServerAliveInterval=15"]
""")
    cfg = load_config(tmp_path)
    assert set(cfg.hosts) == {"vps", "smaug"}
    assert cfg.hosts["vps"].ssh == "vps.apiad.net"
    assert cfg.hosts["vps"].cwd == "/home/apiad/Workspace"
    assert cfg.hosts["vps"].ssh_opts == []
    assert cfg.hosts["smaug"].ssh_opts == ["-o", "ServerAliveInterval=15"]


def test_hosts_default_to_empty(tmp_path):
    _write(tmp_path, "")
    assert load_config(tmp_path).hosts == {}


def test_host_named_local_is_refused(tmp_path):
    _write(tmp_path, """
hosts:
  local:
    ssh: somewhere
    cwd: /tmp
""")
    with pytest.raises(ConfigError, match="local"):
        load_config(tmp_path)


def test_host_requires_ssh_and_cwd(tmp_path):
    _write(tmp_path, """
hosts:
  vps:
    ssh: vps.apiad.net
""")
    with pytest.raises(ConfigError, match="cwd"):
        load_config(tmp_path)


def test_agent_host_default_must_reference_a_declared_host(tmp_path):
    _write(tmp_path, """
default_agent: main
agents:
  main:
    harness: claude-code
    model: opus
    host: nowhere
""")
    with pytest.raises(ConfigError, match="nowhere"):
        load_config(tmp_path)


def test_agent_host_default_accepts_local(tmp_path):
    _write(tmp_path, """
default_agent: main
agents:
  main:
    harness: claude-code
    model: opus
    host: local
""")
    assert load_config(tmp_path).agents["main"].host == "local"


def test_hosts_overlay_merges(tmp_path):
    _write(tmp_path, "")
    d = tmp_path / ".aegis" / "hosts"
    d.mkdir(parents=True)
    (d / "vps.yaml").write_text("ssh: vps.apiad.net\ncwd: /home/apiad/Workspace\n")
    cfg = load_config(tmp_path)
    assert cfg.hosts["vps"].ssh == "vps.apiad.net"


def test_hosts_overlay_collision_is_fail_loud(tmp_path):
    _write(tmp_path, """
hosts:
  vps:
    ssh: a
    cwd: /tmp
""")
    d = tmp_path / ".aegis" / "hosts"
    d.mkdir(parents=True)
    (d / "vps.yaml").write_text("ssh: b\ncwd: /tmp\n")
    with pytest.raises(ConfigError, match="vps"):
        load_config(tmp_path)
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_hosts_config.py -q`
Expected: FAIL with `AttributeError: 'AegisConfig' object has no attribute 'hosts'`

- [x] **Step 3: Write `HostSpec`**

Create `src/aegis/hosts/models.py`:

```python
"""Config and resolved-place data for SSH execution hosts. Pure data."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HostSpec:
    """One entry in the `hosts:` mapping — a machine aegis can run a
    harness on.

    ``ssh`` is handed to the ``ssh`` binary verbatim, so it resolves
    through the user's ``~/.ssh/config``: aliases, ``ProxyCommand``,
    jump hosts and non-standard ports all work without aegis
    reimplementing any of it.
    """
    name: str
    ssh: str
    cwd: str
    ssh_opts: list[str] = field(default_factory=list)
    # Escape hatch: pin the remote forward port instead of letting sshd
    # allocate one and parsing the choice off ssh's stderr.
    remote_mcp_port: int | None = None


@dataclass(frozen=True)
class Place:
    """Where a session's harness process runs. Resolved per spawn,
    never persisted — the same shape as the model/effort overrides."""
    host: str   # "local" or a `hosts:` key
    cwd: str

    @property
    def is_local(self) -> bool:
        return self.host == "local"

    def qualify(self, path: str) -> str:
        """A path string that names its machine. ``/x`` stays ``/x``
        locally and becomes ``vps:/x`` on a remote host, so a reader —
        human or agent — can never mistake one tree for the other."""
        return path if self.is_local else f"{self.host}:{path}"
```

- [x] **Step 4: Wire the section into the loader**

In `src/aegis/config/yaml_loader.py`:

Add the import beside the other config imports:

```python
from aegis.hosts.models import HostSpec
```

Add the field to `AegisConfig`, directly after `remotes`:

```python
    hosts: dict[str, HostSpec] = field(default_factory=dict)
```

Extend `_SECTIONS` (this is what gives `hosts:` drop-in overlays and collision detection for free):

```python
_SECTIONS = ("agents", "harnesses", "queues", "schedules", "remotes", "hosts")
```

Add a builder above `load_config`:

```python
def _host_from_dict(name: str, d: dict[str, Any]) -> HostSpec:
    """Construct a HostSpec from a `hosts:` YAML entry.

    `local` is an implicit host that always exists and cannot be
    redeclared — allowing it would make the meaning of `host: local`
    depend on config, which is exactly the ambiguity the implicit host
    exists to prevent.
    """
    if name == "local":
        raise ConfigError(
            "hosts: 'local' is implicit (the machine aegis runs on) and "
            "cannot be declared.")
    for key in ("ssh", "cwd"):
        if not d.get(key):
            raise ConfigError(
                f"hosts[{name!r}]: {key!r} is required.")
    port = d.get("remote_mcp_port")
    return HostSpec(
        name=name,
        ssh=str(d["ssh"]),
        cwd=str(d["cwd"]),
        ssh_opts=[str(o) for o in (d.get("ssh_opts") or [])],
        remote_mcp_port=int(port) if port is not None else None,
    )
```

In `load_config`, add `"hosts"` to the `inline` dict:

```python
        "hosts": dict(raw.get("hosts") or {}),
```

Build the hosts after `remotes` is built:

```python
    hosts = {k: _host_from_dict(k, dict(v))
             for k, v in merged["hosts"].items()}
```

Add validation after the existing queue-agent validation block:

```python
    # An agent profile may name a default host; it must exist.
    for aname, aprofile in agents.items():
        h = getattr(aprofile, "host", None)
        if h and h != "local" and h not in hosts:
            raise ConfigError(
                f"{base}: agents[{aname!r}].host={h!r} does not reference "
                f"a declared host (known: {sorted(hosts)} + 'local').")
```

And pass it into the returned `AegisConfig`, beside `remotes=remotes`:

```python
        hosts=hosts,
```

- [x] **Step 5: Add the optional `host:` default to `Agent`**

In `src/aegis/config/__init__.py`, add one field to the `Agent` model, after `prompt`:

```python
    host: str | None = None     # optional default execution host
```

It is a plain passthrough: `_sync_provider_and_flat` needs no change, because `host` is aegis-side placement and not a provider concern.

- [x] **Step 6: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_hosts_config.py -q`
Expected: PASS (8 tests)

- [x] **Step 7: Run the full fast suite**

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS with no new failures.

- [x] **Step 8: Commit**

```bash
git add src/aegis/hosts/models.py src/aegis/config/yaml_loader.py \
        src/aegis/config/__init__.py tests/test_hosts_config.py
git commit -m "feat(hosts): a hosts: config section with overlays and fail-loud validation"
```

---

## Task 3: `Place` resolution

**Files:**
- Create: `src/aegis/hosts/resolve.py`
- Test: `tests/test_hosts_resolve.py`

**Interfaces:**
- Consumes: `HostSpec`, `Place` from Task 2.
- Produces: `aegis.hosts.resolve.resolve_place(*, host: str | None, cwd: str | None, agent_host: str | None, hosts: dict[str, HostSpec], local_root: str) -> Place`. Raises `HostError` for an unknown host name.

- [x] **Step 1: Write the failing test**

Create `tests/test_hosts_resolve.py`:

```python
from __future__ import annotations

import pytest

from aegis.hosts.errors import HostError
from aegis.hosts.models import HostSpec, Place
from aegis.hosts.resolve import resolve_place

HOSTS = {
    "vps": HostSpec(name="vps", ssh="vps.apiad.net",
                    cwd="/home/apiad/Workspace"),
}


def r(**kw):
    base = dict(host=None, cwd=None, agent_host=None,
                hosts=HOSTS, local_root="/local/proj")
    base.update(kw)
    return resolve_place(**base)


def test_defaults_to_local_at_the_project_root():
    assert r() == Place("local", "/local/proj")


def test_explicit_host_uses_that_hosts_cwd():
    assert r(host="vps") == Place("vps", "/home/apiad/Workspace")


def test_explicit_host_beats_the_profile_default():
    assert r(host="local", agent_host="vps") == Place("local", "/local/proj")


def test_profile_default_applies_when_no_explicit_host():
    assert r(agent_host="vps") == Place("vps", "/home/apiad/Workspace")


def test_explicit_cwd_beats_the_host_cwd():
    assert r(host="vps", cwd="/other/tree") == Place("vps", "/other/tree")


def test_explicit_cwd_applies_locally_too():
    assert r(cwd="/somewhere/else") == Place("local", "/somewhere/else")


def test_unknown_host_is_a_loud_error():
    with pytest.raises(HostError, match="nowhere"):
        r(host="nowhere")


def test_unknown_host_error_lists_the_known_ones():
    with pytest.raises(HostError, match="vps"):
        r(host="nowhere")
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_hosts_resolve.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.hosts.resolve'`

- [x] **Step 3: Write the resolver**

Create `src/aegis/hosts/resolve.py`:

```python
"""Turning spawn arguments + profile defaults + config into a Place.

Pure: no I/O, no connection, no side effects. The precedence rules are
the whole content of this module and they are tested exhaustively.
"""
from __future__ import annotations

from aegis.hosts.errors import HostError
from aegis.hosts.models import HostSpec, Place


def resolve_place(*, host: str | None, cwd: str | None,
                  agent_host: str | None,
                  hosts: dict[str, HostSpec],
                  local_root: str) -> Place:
    """Resolve where a session's harness will run.

    host: explicit spawn argument > agent profile default > "local".
    cwd:  explicit spawn argument > that host's cwd > local_root.
    """
    name = host or agent_host or "local"
    if name == "local":
        return Place("local", cwd or local_root)
    spec = hosts.get(name)
    if spec is None:
        known = sorted(hosts) + ["local"]
        raise HostError(
            f"unknown host {name!r}; known: {known}")
    return Place(name, cwd or spec.cwd)
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_hosts_resolve.py -q`
Expected: PASS (8 tests)

- [x] **Step 5: Commit**

```bash
git add src/aegis/hosts/resolve.py tests/test_hosts_resolve.py
git commit -m "feat(hosts): Place resolution — explicit > profile default > local"
```

---

## Task 4: `SshLauncher` argv composition

Pure string work, no `ssh` process. This is where quoting bugs are cheapest to catch.

**Files:**
- Modify: `src/aegis/hosts/launcher.py`
- Test: `tests/test_hosts_launcher.py`

**Interfaces:**
- Consumes: `Launcher` (Task 1), `HostSpec` (Task 2).
- Produces:
  - `aegis.hosts.launcher.remote_command(argv: list[str], *, cwd: str, env: dict[str, str]) -> str`
  - `aegis.hosts.launcher.env_delta(env: dict[str, str] | None, base: Mapping[str, str]) -> dict[str, str]`
  - `aegis.hosts.launcher.ssh_argv(spec: HostSpec, control_path: str, remote_cmd: str) -> list[str]`
  - `aegis.hosts.launcher.SshLauncher(conn, spec, local_root)` — implements `Launcher`, `host_key == spec.name`.

- [x] **Step 1: Write the failing test**

Append to `tests/test_hosts_launcher.py`:

```python
from aegis.hosts.launcher import env_delta, remote_command, ssh_argv
from aegis.hosts.models import HostSpec

SPEC = HostSpec(name="vps", ssh="vps.apiad.net", cwd="/home/apiad/Workspace")


def test_remote_command_cds_and_execs():
    cmd = remote_command(["claude", "-p"], cwd="/srv/app", env={})
    assert cmd == "cd /srv/app && exec claude -p"


def test_remote_command_quotes_a_cwd_with_spaces():
    cmd = remote_command(["true"], cwd="/srv/my app", env={})
    assert "'/srv/my app'" in cmd
    assert cmd.startswith("cd '/srv/my app' && exec ")


def test_remote_command_quotes_argv_with_quotes_and_newlines():
    # The claude primer is a realistic worst case: a multi-line string
    # containing apostrophes, passed as one --append-system-prompt value.
    primer = "You are 'agent-one'.\nCall aegis_meta() first."
    cmd = remote_command(
        ["claude", "--append-system-prompt", primer], cwd="/srv", env={})
    # Round-trip through the shell's own parser rather than asserting on
    # the exact escaping: what matters is that sh reconstructs the value.
    import shlex
    parsed = shlex.split(cmd)
    assert parsed[-1] == primer
    assert parsed[-2] == "--append-system-prompt"


def test_remote_command_emits_env_before_the_argv():
    cmd = remote_command(["true"], cwd="/srv", env={"FOO": "bar"})
    assert cmd == "cd /srv && exec env FOO=bar true"


def test_remote_command_quotes_env_values():
    cmd = remote_command(["true"], cwd="/srv", env={"K": "a b"})
    assert "'K=a b'" in cmd


def test_env_delta_keeps_only_what_differs_from_the_local_environment():
    # Shipping the whole local environ over ssh would clobber the remote
    # shell's own environment. Only driver-injected and hook-added keys
    # should cross.
    base = {"PATH": "/usr/bin", "HOME": "/home/me"}
    got = env_delta({"PATH": "/usr/bin", "HOME": "/home/me",
                     "OPENROUTER_API_KEY": "sk-x"}, base)
    assert got == {"OPENROUTER_API_KEY": "sk-x"}


def test_env_delta_of_none_is_empty():
    assert env_delta(None, {"PATH": "/usr/bin"}) == {}


def test_env_delta_includes_a_changed_value():
    base = {"MODEL": "old"}
    assert env_delta({"MODEL": "new"}, base) == {"MODEL": "new"}


def test_ssh_argv_shape():
    argv = ssh_argv(SPEC, "/run/x.sock", "cd /srv && exec true")
    assert argv[0] == "ssh"
    assert "-T" in argv                       # no PTY: clean byte stream
    assert "ControlPath=/run/x.sock" in argv
    assert argv[-2] == "vps.apiad.net"
    assert argv[-1] == "cd /srv && exec true"


def test_ssh_argv_appends_host_ssh_opts():
    spec = HostSpec(name="vps", ssh="h", cwd="/x",
                    ssh_opts=["-o", "ServerAliveInterval=15"])
    argv = ssh_argv(spec, "/run/x.sock", "true")
    assert "ServerAliveInterval=15" in argv
    # opts land before the destination, where ssh expects them
    assert argv.index("ServerAliveInterval=15") < argv.index("h")
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_hosts_launcher.py -q`
Expected: FAIL with `ImportError: cannot import name 'env_delta' from 'aegis.hosts.launcher'`

- [x] **Step 3: Write the composition helpers and `SshLauncher`**

Append to `src/aegis/hosts/launcher.py` (and add `import os`, `import shlex`, `from collections.abc import Mapping`, `from aegis.hosts.models import HostSpec` to the imports):

```python
def env_delta(env: dict[str, str] | None,
              base: Mapping[str, str]) -> dict[str, str]:
    """The environment keys worth sending across the wire.

    A local spawn hands the driver a full copy of ``os.environ`` (that is
    what ``_apply_pre_spawn_hooks`` returns once any hook fires). Shipping
    all of it to another machine would clobber the remote shell's own
    environment with this one's — so only keys that actually differ from
    the local baseline cross: driver-injected ``extra_env`` and whatever a
    pre-spawn hook added or changed.
    """
    if env is None:
        return {}
    return {k: v for k, v in env.items() if base.get(k) != v}


def remote_command(argv: list[str], *, cwd: str,
                   env: dict[str, str]) -> str:
    """The single shell string ssh will run on the far side.

    ``exec`` matters: it replaces the login shell with the harness, so
    signals and EOF reach the harness directly rather than a wrapper.
    """
    parts = ["cd", shlex.quote(cwd), "&&", "exec"]
    if env:
        parts.append("env")
        parts += [shlex.quote(f"{k}={v}") for k, v in sorted(env.items())]
    parts += [shlex.quote(a) for a in argv]
    return " ".join(parts)


def ssh_argv(spec: HostSpec, control_path: str,
             remote_cmd: str) -> list[str]:
    """A session-carrying ssh invocation that multiplexes over the master.

    ``-T`` disables PTY allocation: stream-json and ACP JSON-RPC need a
    clean byte stream, and a PTY would inject echo and line discipline
    into the middle of the protocol.
    """
    return [
        "ssh", "-T",
        "-o", f"ControlPath={control_path}",
        *spec.ssh_opts,
        spec.ssh,
        remote_cmd,
    ]


class SshLauncher:
    """Runs the harness on another machine, over a shared ControlMaster."""

    def __init__(self, conn, spec: HostSpec,
                 local_root: str | None = None) -> None:
        self._conn = conn
        self._spec = spec
        self.host_key = spec.name
        self.local_root = local_root
        # Bytes ssh itself wrote — the difference between "the harness
        # exited" and "the link died", which the session needs at EOF.
        self.stderr_tail: list[bytes] = []

    def persona_root(self, cwd: str) -> str:
        """A persona file lives in the LOCAL project even when the
        harness runs remotely, so it never resolves under the remote
        cwd."""
        return self.local_root or "."

    async def spawn(self, argv: list[str], *, cwd: str,
                    env: dict[str, str] | None
                    ) -> asyncio.subprocess.Process:
        await self._conn.ensure_open()
        cmd = remote_command(argv, cwd=cwd,
                             env=env_delta(env, os.environ))
        return await asyncio.create_subprocess_exec(
            *ssh_argv(self._spec, self._conn.control_path, cmd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_LIMIT,
        )
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_hosts_launcher.py -q`
Expected: PASS (all tests including the 10 new ones)

- [x] **Step 5: Commit**

```bash
git add src/aegis/hosts/launcher.py tests/test_hosts_launcher.py
git commit -m "feat(hosts): SshLauncher argv composition with shell-safe quoting"
```

---

## Task 5: `HostConnection` — ControlMaster, reverse tunnel, preflight

**Files:**
- Create: `src/aegis/hosts/connection.py`
- Test: `tests/test_hosts_connection.py`

**Interfaces:**
- Consumes: `HostSpec` (Task 2), `HostError` (Task 1).
- Produces:
  - `aegis.hosts.connection.parse_allocated_port(line: str) -> int | None`
  - `aegis.hosts.connection.master_argv(spec: HostSpec, control_path: str, mcp_port: int) -> list[str]`
  - `aegis.hosts.connection.preflight_command(binary: str, cwd: str) -> str`
  - `aegis.hosts.connection.HostConnection(spec, control_path, mcp_port)` with `async ensure_open()`, `control_path: str`, `remote_mcp_url: str`, `async close()`.

- [x] **Step 1: Write the failing test**

Create `tests/test_hosts_connection.py`:

```python
from __future__ import annotations

import pytest

from aegis.hosts.connection import (
    master_argv,
    parse_allocated_port,
    preflight_command,
)
from aegis.hosts.models import HostSpec

SPEC = HostSpec(name="vps", ssh="vps.apiad.net",
                cwd="/home/apiad/Workspace")


def test_parses_the_allocated_port_line():
    line = "Allocated port 41573 for remote forward to 127.0.0.1:8931"
    assert parse_allocated_port(line) == 41573


def test_parses_the_line_with_a_debug_prefix():
    line = ("debug1: Allocated port 41573 for remote forward to "
            "127.0.0.1:8931")
    assert parse_allocated_port(line) == 41573


def test_ignores_unrelated_stderr():
    assert parse_allocated_port("Warning: Permanently added 'vps'") is None
    assert parse_allocated_port("") is None
    assert parse_allocated_port("Allocated port for remote forward") is None


def test_master_argv_requests_a_dynamic_reverse_forward():
    argv = master_argv(SPEC, "/run/x.sock", 8931)
    assert "-R" in argv
    assert argv[argv.index("-R") + 1] == "0:127.0.0.1:8931"


def test_master_argv_pins_the_port_when_the_spec_says_so():
    spec = HostSpec(name="vps", ssh="h", cwd="/x", remote_mcp_port=9999)
    argv = master_argv(spec, "/run/x.sock", 8931)
    assert argv[argv.index("-R") + 1] == "9999:127.0.0.1:8931"


def test_master_argv_fails_loudly_on_a_broken_forward():
    # Without this, a failed forward leaves a live master and every
    # session on it gets an unreachable MCP URL.
    assert "ExitOnForwardFailure=yes" in master_argv(SPEC, "/s", 1)


def test_master_argv_forces_loglevel_info():
    # The allocated-port line is an INFO-level message. A user's
    # ~/.ssh/config setting LogLevel=QUIET would otherwise swallow it and
    # the parse would hang until it timed out.
    assert "LogLevel=INFO" in master_argv(SPEC, "/s", 1)


def test_master_argv_is_a_backgroundable_master():
    argv = master_argv(SPEC, "/run/x.sock", 8931)
    assert "-M" in argv and "-N" in argv
    assert "ControlPath=/run/x.sock" in argv
    assert "ControlPersist=60s" in argv
    assert argv[-1] == "vps.apiad.net"


def test_preflight_checks_both_the_binary_and_the_directory():
    cmd = preflight_command("claude", "/home/apiad/Workspace")
    assert "command -v claude" in cmd
    assert "/home/apiad/Workspace" in cmd
    assert "test -d" in cmd


def test_preflight_quotes_a_cwd_with_spaces():
    assert "'/srv/my app'" in preflight_command("claude", "/srv/my app")
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_hosts_connection.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.hosts.connection'`

- [x] **Step 3: Write the connection module**

Create `src/aegis/hosts/connection.py`:

```python
"""One persistent SSH connection to one execution host.

Owns the ControlMaster subprocess, the reverse tunnel that carries the
local MCP port to the remote side, and the one-time preflight that turns
"the harness isn't installed there" into a sentence instead of a
mysterious EOF.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import shlex
from pathlib import Path

from aegis.hosts.errors import HostError
from aegis.hosts.models import HostSpec

# ssh reports a dynamically-allocated reverse-forward port on stderr:
#   Allocated port 41573 for remote forward to 127.0.0.1:8931
_ALLOCATED = re.compile(r"Allocated port (\d+) for remote forward")

# How long to wait for the master to come up and report its port.
OPEN_TIMEOUT_S = 20.0


def parse_allocated_port(line: str) -> int | None:
    """The remote port sshd chose for our reverse forward, if this line
    announces one."""
    m = _ALLOCATED.search(line)
    return int(m.group(1)) if m else None


def master_argv(spec: HostSpec, control_path: str,
                mcp_port: int) -> list[str]:
    """The backgrounded ControlMaster that every session multiplexes over.

    ``-R 0:…`` asks sshd to pick a free remote port, so two aegis
    instances targeting the same host cannot collide. ``LogLevel=INFO``
    is explicit because the allocated-port announcement is an INFO
    message and a user's ssh_config may otherwise be quieter than that.
    """
    remote_port = spec.remote_mcp_port if spec.remote_mcp_port else 0
    return [
        "ssh", "-M", "-N",
        "-o", f"ControlPath={control_path}",
        "-o", "ControlMaster=yes",
        "-o", "ControlPersist=60s",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "LogLevel=INFO",
        "-R", f"{remote_port}:127.0.0.1:{mcp_port}",
        *spec.ssh_opts,
        spec.ssh,
    ]


def preflight_command(binary: str, cwd: str) -> str:
    """Confirm the harness exists and the working tree is there."""
    return (f"command -v {shlex.quote(binary)} >/dev/null 2>&1 "
            f"&& test -d {shlex.quote(cwd)}")


class HostConnection:
    """Lazily-opened, shared-by-every-session link to one host."""

    def __init__(self, spec: HostSpec, control_path: str,
                 mcp_port: int) -> None:
        self._spec = spec
        self.control_path = control_path
        self._mcp_port = mcp_port
        self._proc: asyncio.subprocess.Process | None = None
        self._remote_port: int | None = None
        self._lock = asyncio.Lock()
        self._stderr: list[str] = []

    @property
    def remote_mcp_url(self) -> str:
        """What the remote harness should be told the MCP plane is."""
        if self._remote_port is None:
            raise HostError(f"host {self._spec.name!r} is not open")
        return f"http://127.0.0.1:{self._remote_port}/mcp/"

    async def ensure_open(self) -> None:
        """Open the master if it isn't, exactly once.

        Concurrent spawns on the same host share one lock, so two tabs
        opened at the same moment share a single master rather than
        racing to create two.
        """
        async with self._lock:
            if self._proc is not None and self._proc.returncode is None:
                return
            Path(self.control_path).parent.mkdir(parents=True, exist_ok=True)
            await self._open()

    async def _open(self) -> None:
        argv = master_argv(self._spec, self.control_path, self._mcp_port)
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._remote_port = await self._await_port()

    async def _await_port(self) -> int:
        """Read ssh's stderr until it announces the forward, or fail.

        A pinned ``remote_mcp_port`` skips the parse entirely — that is
        the escape hatch for an sshd whose phrasing we don't match.
        """
        assert self._proc and self._proc.stderr
        if self._spec.remote_mcp_port:
            return self._spec.remote_mcp_port

        async def read_until_port() -> int:
            assert self._proc and self._proc.stderr
            async for raw in self._proc.stderr:
                line = raw.decode("utf-8", "replace").rstrip()
                self._stderr.append(line)
                port = parse_allocated_port(line)
                if port is not None:
                    return port
            raise HostError(
                f"host {self._spec.name!r}: ssh exited before announcing a "
                f"reverse-forward port.\n"
                f"  ssh stderr:\n{self.stderr_text() or '(empty)'}")

        try:
            return await asyncio.wait_for(read_until_port(), OPEN_TIMEOUT_S)
        except asyncio.TimeoutError:
            await self.close()
            raise HostError(
                f"host {self._spec.name!r}: timed out after "
                f"{OPEN_TIMEOUT_S:.0f}s waiting for ssh to open the "
                f"reverse forward. Set `remote_mcp_port:` on the host to "
                f"skip port auto-detection.\n"
                f"  ssh stderr:\n{self.stderr_text() or '(empty)'}") from None

    async def preflight(self, binary: str, cwd: str) -> None:
        """Confirm the harness and the working tree exist, once per host."""
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-T",
            "-o", f"ControlPath={self.control_path}",
            *self._spec.ssh_opts, self._spec.ssh,
            preflight_command(binary, cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise HostError(
                f"{self._spec.name}: preflight failed — either {binary!r} "
                f"is not on PATH there or {cwd} does not exist.\n"
                f"  ssh stderr: "
                f"{err.decode('utf-8', 'replace').strip() or '(empty)'}")

    def stderr_text(self) -> str:
        """The last of what ssh said, for error messages."""
        return "\n".join(self._stderr[-40:])

    async def close(self) -> None:
        """Tear the master down. Idempotent."""
        with contextlib.suppress(Exception):
            proc = await asyncio.create_subprocess_exec(
                "ssh", "-O", "exit",
                "-o", f"ControlPath={self.control_path}",
                self._spec.ssh,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(proc.wait(), timeout=5)
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            if self._proc.returncode is None:
                self._proc.kill()
        self._proc = None
        self._remote_port = None
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_hosts_connection.py -q`
Expected: PASS (10 tests)

- [x] **Step 5: Commit**

```bash
git add src/aegis/hosts/connection.py tests/test_hosts_connection.py
git commit -m "feat(hosts): HostConnection — ControlMaster, reverse tunnel, preflight"
```

---

## Task 6: `HostRegistry`

**Files:**
- Create: `src/aegis/hosts/registry.py`
- Test: `tests/test_hosts_connection.py` (append)

**Interfaces:**
- Consumes: `HostSpec`, `Place`, `HostConnection`, `LocalLauncher`, `SshLauncher`.
- Produces: `aegis.hosts.registry.HostRegistry(hosts: dict[str, HostSpec], state_dir: Path, local_root: str)` with `set_mcp_port(port: int)`, **synchronous** `launcher_for(place: Place, mcp_url: str) -> tuple[Launcher, str]`, and `async close_all()`.

- [x] **Step 1: Write the failing test**

Append to `tests/test_hosts_connection.py`:

```python
from pathlib import Path

from aegis.hosts.launcher import LocalLauncher, SshLauncher
from aegis.hosts.models import Place
from aegis.hosts.registry import HostRegistry


def _registry(tmp_path):
    reg = HostRegistry({"vps": SPEC}, state_dir=tmp_path / "state",
                       local_root="/local/proj")
    reg.set_mcp_port(8931)
    return reg


def test_local_place_gets_a_local_launcher_and_the_url_unchanged(tmp_path):
    lau, url = _registry(tmp_path).launcher_for(
        Place("local", "/local/proj"), "http://127.0.0.1:8931/mcp/")
    assert isinstance(lau, LocalLauncher)
    assert url == "http://127.0.0.1:8931/mcp/"
    assert lau.persona_root("/local/proj") == "/local/proj"


def test_remote_place_gets_an_ssh_launcher(tmp_path):
    lau, _url = _registry(tmp_path).launcher_for(
        Place("vps", "/home/apiad/Workspace"), "http://127.0.0.1:8931/mcp/")
    assert isinstance(lau, SshLauncher)
    assert lau.host_key == "vps"
    # Persona files live locally even though the harness runs remotely.
    assert lau.persona_root("/home/apiad/Workspace") == "/local/proj"


def test_remote_url_is_deferred_until_the_tunnel_is_up(tmp_path):
    # launcher_for is synchronous (it is called from _sync_spawn) and the
    # allocated port is not known until the master opens. The URL is
    # therefore a callable placeholder resolved inside spawn().
    _lau, url = _registry(tmp_path).launcher_for(
        Place("vps", "/x"), "http://127.0.0.1:8931/mcp/")
    assert url == ""      # sentinel: resolved at spawn time


def test_one_connection_is_reused_per_host(tmp_path):
    reg = _registry(tmp_path)
    a, _ = reg.launcher_for(Place("vps", "/x"), "u")
    b, _ = reg.launcher_for(Place("vps", "/y"), "u")
    assert a._conn is b._conn


def test_control_path_lives_under_the_state_dir(tmp_path):
    reg = _registry(tmp_path)
    lau, _ = reg.launcher_for(Place("vps", "/x"), "u")
    assert str(tmp_path / "state") in lau._conn.control_path
    assert lau._conn.control_path.endswith("vps.sock")


def test_unknown_host_is_a_loud_error(tmp_path):
    with pytest.raises(HostError, match="nowhere"):
        _registry(tmp_path).launcher_for(Place("nowhere", "/x"), "u")
```

Add `from aegis.hosts.errors import HostError` to that file's imports.

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_hosts_connection.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.hosts.registry'`

- [x] **Step 3: Write the registry**

Create `src/aegis/hosts/registry.py`:

```python
"""One HostConnection per host, and the launcher that rides it.

``launcher_for`` must stay SYNCHRONOUS: ``SessionManager._sync_spawn``
is sync, and opening an SSH master is not. So the registry hands back an
``SshLauncher`` bound to a not-yet-open connection; the connection is
established inside ``SshLauncher.spawn``, which is already async. That
also puts connection errors where they belong — failing the session that
asked for the host, in a pane that exists to show the message.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from aegis.hosts.connection import HostConnection
from aegis.hosts.errors import HostError
from aegis.hosts.launcher import Launcher, LocalLauncher, SshLauncher
from aegis.hosts.models import HostSpec, Place

# Returned as the mcp_url for a remote place. The real URL is not known
# until sshd allocates the reverse-forward port, so drivers must ask the
# connection at spawn time rather than baking a URL into argv early.
DEFERRED_URL = ""


class HostRegistry:
    def __init__(self, hosts: dict[str, HostSpec], state_dir: Path,
                 local_root: str) -> None:
        self._hosts = dict(hosts)
        self._state_dir = Path(state_dir)
        self._local_root = local_root
        self._conns: dict[str, HostConnection] = {}
        self._mcp_port: int | None = None

    def set_mcp_port(self, port: int) -> None:
        """Called once, after AegisMCP has bound its port and before the
        first spawn."""
        self._mcp_port = port

    def known(self) -> list[str]:
        return sorted(self._hosts)

    def spec(self, name: str) -> HostSpec:
        spec = self._hosts.get(name)
        if spec is None:
            raise HostError(
                f"unknown host {name!r}; known: "
                f"{sorted(self._hosts) + ['local']}")
        return spec

    def _connection(self, spec: HostSpec) -> HostConnection:
        conn = self._conns.get(spec.name)
        if conn is None:
            if self._mcp_port is None:
                raise HostError(
                    "host registry has no MCP port yet — call "
                    "set_mcp_port() before spawning a remote session.")
            conn = HostConnection(
                spec,
                control_path=str(self._state_dir / "ssh" / f"{spec.name}.sock"),
                mcp_port=self._mcp_port)
            self._conns[spec.name] = conn
        return conn

    def launcher_for(self, place: Place,
                     mcp_url: str) -> tuple[Launcher, str]:
        """The launcher for a place, plus the MCP URL its harness should
        be told about. Synchronous by contract."""
        if place.is_local:
            return LocalLauncher(local_root=self._local_root), mcp_url
        spec = self.spec(place.host)
        conn = self._connection(spec)
        return (SshLauncher(conn, spec, local_root=self._local_root),
                DEFERRED_URL)

    async def close_all(self) -> None:
        """Tear down every master. Called on aegis quit."""
        await asyncio.gather(
            *(c.close() for c in self._conns.values()),
            return_exceptions=True)
        self._conns.clear()
```

- [x] **Step 4: Resolve the deferred URL inside `SshLauncher.spawn`**

The MCP URL is baked into argv by `build_argv`, which runs before the tunnel exists. `SshLauncher.spawn` therefore rewrites the placeholder just before exec. In `src/aegis/hosts/launcher.py`, replace `SshLauncher.spawn` with:

```python
    async def spawn(self, argv: list[str], *, cwd: str,
                    env: dict[str, str] | None
                    ) -> asyncio.subprocess.Process:
        await self._conn.ensure_open()
        argv = _substitute_mcp_url(argv, self._conn.remote_mcp_url)
        cmd = remote_command(argv, cwd=cwd,
                             env=env_delta(env, os.environ))
        return await asyncio.create_subprocess_exec(
            *ssh_argv(self._spec, self._conn.control_path, cmd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_LIMIT,
        )
```

and add the substitution helper above it:

```python
def _substitute_mcp_url(argv: list[str], url: str) -> list[str]:
    """Fill in the MCP URL that wasn't known when argv was built.

    ``build_argv`` bakes ``mcp_config_json(mcp_url)`` into the argv at
    session-construction time, but a remote session's URL depends on the
    port sshd allocates when the tunnel opens — which is later. The
    registry hands drivers an empty URL as a placeholder; this rewrites
    the resulting config just before exec.
    """
    from aegis.mcp import mcp_config_json
    placeholder = mcp_config_json("")
    real = mcp_config_json(url)
    return [real if a == placeholder else a for a in argv]
```

Only the exact placeholder config blob is replaced — nothing else in argv is touched. Matching on anything looser (an empty string, a substring) would rewrite unrelated arguments.

- [x] **Step 5: Add the substitution test**

Append to `tests/test_hosts_launcher.py`:

```python
def test_deferred_mcp_url_is_substituted_before_exec():
    from aegis.hosts.launcher import _substitute_mcp_url
    from aegis.mcp import mcp_config_json

    argv = ["claude", "-p", "--mcp-config", mcp_config_json(""),
            "--strict-mcp-config"]
    out = _substitute_mcp_url(argv, "http://127.0.0.1:41573/mcp/")
    assert out[3] == mcp_config_json("http://127.0.0.1:41573/mcp/")
    assert "41573" in out[3]
    # Nothing else is touched.
    assert out[0:3] == ["claude", "-p", "--mcp-config"]
    assert out[4] == "--strict-mcp-config"
```

- [x] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_hosts_connection.py tests/test_hosts_launcher.py -q`
Expected: PASS

- [x] **Step 7: Commit**

```bash
git add src/aegis/hosts/registry.py src/aegis/hosts/launcher.py \
        tests/test_hosts_connection.py tests/test_hosts_launcher.py
git commit -m "feat(hosts): HostRegistry with a synchronous launcher_for and deferred MCP url"
```

---

## Task 7: Thread the place through spawn

**Files:**
- Modify: `src/aegis/cli.py` (`_session_factory`)
- Modify: `src/aegis/core/manager.py` (`SessionManager.__init__`, `_sync_spawn`, `spawn`, `fork`)
- Modify: `src/aegis/core/session.py` (`AgentSession.__init__`)
- Test: `tests/test_hosts_spawn_wiring.py`

**Interfaces:**
- Consumes: `HostRegistry.launcher_for`, `resolve_place`, `Place`.
- Produces:
  - `_session_factory(cwd: str, hosts: HostRegistry | None = None)` → `make_session(profile, mcp_url, handle, fork_from=None, place=None)`.
  - `SessionManager._sync_spawn(..., host: str | None = None, cwd: str | None = None)`; same two keywords on `spawn()` and `fork()`.
  - `AgentSession.place: Place`.

- [x] **Step 1: Write the failing test**

Create `tests/test_hosts_spawn_wiring.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.core.manager import SessionManager
from aegis.hosts.models import HostSpec, Place


class _StubSession:
    supports_idle_events = False

    def __init__(self):
        self.started = False

    async def start(self):
        self.started = True

    async def send(self, text):
        pass

    async def events(self):
        return
        yield

    async def close(self):
        pass


def _manager(tmp_path, **kw):
    seen: list[Place | None] = []

    def make_session(profile, mcp_url, handle, fork_from=None, place=None):
        seen.append(place)
        return _StubSession()

    mgr = SessionManager(
        agents={"main": Agent(harness="claude-code", model="opus"),
                "vpsy": Agent(harness="claude-code", model="opus",
                              host="vps")},
        default_agent="main",
        make_session=make_session,
        hosts={"vps": HostSpec(name="vps", ssh="vps.apiad.net",
                               cwd="/home/apiad/Workspace")},
        local_root=str(tmp_path),
        **kw)
    return mgr, seen


def test_default_spawn_is_local(tmp_path):
    mgr, seen = _manager(tmp_path)
    asyncio.run(mgr.spawn("main"))
    assert seen[-1] == Place("local", str(tmp_path))


def test_explicit_host_reaches_the_factory(tmp_path):
    mgr, seen = _manager(tmp_path)
    asyncio.run(mgr.spawn("main", host="vps"))
    assert seen[-1] == Place("vps", "/home/apiad/Workspace")


def test_explicit_cwd_overrides_the_host_default(tmp_path):
    mgr, seen = _manager(tmp_path)
    asyncio.run(mgr.spawn("main", host="vps", cwd="/other"))
    assert seen[-1] == Place("vps", "/other")


def test_profile_host_default_applies(tmp_path):
    mgr, seen = _manager(tmp_path)
    asyncio.run(mgr.spawn("vpsy"))
    assert seen[-1] == Place("vps", "/home/apiad/Workspace")


def test_session_carries_its_place(tmp_path):
    mgr, _ = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main", host="vps"))
    assert mgr.get(h).place == Place("vps", "/home/apiad/Workspace")


def test_unknown_host_raises_before_a_session_is_created(tmp_path):
    from aegis.hosts.errors import HostError
    mgr, _ = _manager(tmp_path)
    before = len(mgr.sessions())
    with pytest.raises(HostError, match="nowhere"):
        asyncio.run(mgr.spawn("main", host="nowhere"))
    assert len(mgr.sessions()) == before
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_hosts_spawn_wiring.py -q`
Expected: FAIL with `TypeError: SessionManager.__init__() got an unexpected keyword argument 'hosts'`

- [x] **Step 3: Give `SessionManager` the host axis**

In `src/aegis/core/manager.py`:

Add the imports:

```python
from aegis.hosts.models import HostSpec, Place
from aegis.hosts.resolve import resolve_place
```

Add two keyword arguments to `SessionManager.__init__` (defaulted, so every existing construction site keeps working), storing them beside `self._make_session`:

```python
        self._hosts: dict[str, HostSpec] = dict(hosts or {})
        self._local_root = local_root or "."
```

Add `host` and `cwd` to `_sync_spawn`'s keyword arguments, and resolve the place *before* anything is created — an unknown host must not leave a half-built session behind:

```python
    def _sync_spawn(self, slug: str | None = None, *,
                    opening_prompt: str | None = None,
                    handle: str | None = None,
                    spawned_by: str | None = None,
                    model: str | None = None,
                    effort: str | None = None,
                    prompt: str | None = None,
                    host: str | None = None,
                    cwd: str | None = None,
                    fork_from: str | None = None,
                    forked_from: dict | None = None,
                    place: Place | None = None) -> AgentSession:
        slug = slug or self._default_agent
        if slug not in self._agents:
            raise KeyError(slug)
        agent = _overlay_agent(self._agents[slug], model=model,
                              effort=effort, prompt=prompt)
        # Resolve placement BEFORE minting a handle or a session: an
        # unknown host must fail without leaving a half-built tab behind.
        place = place or resolve_place(
            host=host, cwd=cwd,
            agent_host=getattr(agent, "host", None),
            hosts=self._hosts, local_root=self._local_root)
        h = handle or generate_name({s.handle for s in self._sessions})
        url = self._mcp.url if self._mcp is not None else ""
        raw = (self._make_session(agent, url, h,
                                  fork_from=fork_from, place=place)
               if fork_from is not None
               else self._make_session(agent, url, h, place=place))
        s = AgentSession(raw, agent, slug, h,
                         inbox=self._inbox,
                         opening_prompt=opening_prompt,
                         place=place)
```

The rest of `_sync_spawn` is unchanged.

Forward the two keywords from `spawn`:

```python
    async def spawn(self, profile: str, *,
                    handle: str | None = None,
                    opening_prompt: str | None = None,
                    spawned_by: str | None = None,
                    model: str | None = None,
                    effort: str | None = None,
                    prompt: str | None = None,
                    host: str | None = None,
                    cwd: str | None = None) -> str:
        """AppBridge-shaped async spawn. Returns the new handle.

        ``model`` / ``effort`` / ``prompt`` are optional per-session
        overrides layered over the named profile (never persisted).
        ``host`` / ``cwd`` place the harness process — see
        ``aegis.hosts``."""
        sess = self._sync_spawn(profile, handle=handle,
                                opening_prompt=opening_prompt,
                                spawned_by=spawned_by,
                                model=model, effort=effort, prompt=prompt,
                                host=host, cwd=cwd)
        return sess.handle
```

In `fork`, pass the parent's place so branching a conversation never silently relocates it. In the `self._sync_spawn(...)` call inside `fork`, add:

```python
            place=s.place,
```

- [x] **Step 4: Give `AgentSession` a place**

In `src/aegis/core/session.py`, add a defaulted keyword to `AgentSession.__init__` and store it:

```python
                 place: "Place | None" = None,
```

and in the body, before the other assignments:

```python
        from aegis.hosts.models import Place
        self.place = place or Place("local", ".")
```

The local import avoids an import cycle at module load; `core.session` is imported early.

- [x] **Step 5: Update the session factory**

In `src/aegis/cli.py`, replace `_session_factory`:

```python
def _session_factory(cwd: str, hosts=None):
    """The SessionFactory every entry point hands SessionManager.

    ``fork_from`` is what SessionManager.fork passes to branch an
    existing conversation; without it this is an ordinary cold spawn.
    ``place`` says which machine and which working tree the harness runs
    in — ``None`` means here, at ``cwd``. Kept in one place because all
    three entry points (tui, serve, workflow) need the fork branch and
    three copies would drift.
    """
    from aegis.hosts.models import Place

    def make_session(profile, mcp_url, handle, fork_from=None, place=None):
        place = place or Place("local", cwd)
        if hosts is not None:
            launcher, url = hosts.launcher_for(place, mcp_url)
        else:
            from aegis.hosts.launcher import LocalLauncher
            launcher, url = LocalLauncher(local_root=cwd), mcp_url
        drv = get_driver(profile.harness)
        if fork_from is not None:
            return drv.fork(profile, place.cwd, url, handle, fork_from,
                            launcher)
        return drv.session(profile, place.cwd, url, handle, launcher)
    return make_session
```

Then, at each of the three call sites that build a `SessionManager` (the `tui`, `serve`, and `workflow` entry points), construct a `HostRegistry` from `cfg.hosts` and pass it to both `_session_factory` and `SessionManager`:

```python
    from aegis.hosts.registry import HostRegistry
    host_registry = HostRegistry(
        cfg.hosts, state_dir=root / ".aegis" / "state", local_root=str(root))
    ...
    make_session = _session_factory(str(root), host_registry)
    mgr = SessionManager(..., hosts=cfg.hosts, local_root=str(root))
```

and immediately after the MCP runtime starts (wherever `mcp.start()` is called), hand the registry the port:

```python
    host_registry.set_mcp_port(mcp.port)
```

- [x] **Step 6: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_hosts_spawn_wiring.py -q`
Expected: PASS (6 tests)

- [x] **Step 7: Run the full fast suite**

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS with no new failures.

- [x] **Step 8: Commit**

```bash
git add src/aegis/cli.py src/aegis/core/manager.py src/aegis/core/session.py \
        tests/test_hosts_spawn_wiring.py
git commit -m "feat(hosts): thread host/cwd through spawn as a third orthogonal axis"
```

---

## Task 8: Live end-to-end over `ssh localhost`

This is the task that proves the whole mechanism, without needing the VPS. It uses `localhost` as the "remote" host: a real ControlMaster, a real `-R` tunnel, a real `claude`, and an assertion that the "remote" agent's MCP call reaches the local server.

**Files:**
- Create: `tests/test_ssh_hosts_live.py`

**Interfaces:**
- Consumes: everything from Tasks 1–7.
- Produces: nothing consumed by later tasks.

- [x] **Step 1: Write the live test**

Create `tests/test_ssh_hosts_live.py`:

```python
"""End-to-end: a harness running over ssh, calling back through the
reverse tunnel.

Uses `localhost` as the remote host, so this runs anywhere sshd accepts a
key-based connection from the current user. Marked `live` and skipped
when that isn't true.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess

import pytest

from aegis.hosts.connection import HostConnection
from aegis.hosts.launcher import SshLauncher
from aegis.hosts.models import HostSpec

pytestmark = pytest.mark.live


def _ssh_localhost_works() -> bool:
    if shutil.which("ssh") is None:
        return False
    try:
        return subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "-o", "ConnectTimeout=5", "localhost", "true"],
            capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


needs_ssh = pytest.mark.skipif(
    not _ssh_localhost_works(),
    reason="ssh to localhost is not available (BatchMode key auth required)")


@needs_ssh
def test_master_opens_and_allocates_a_reverse_port(tmp_path):
    spec = HostSpec(name="localhost", ssh="localhost", cwd=str(tmp_path))
    conn = HostConnection(spec,
                          control_path=str(tmp_path / "ctl.sock"),
                          mcp_port=9)     # discard port; nothing must connect

    async def go():
        await conn.ensure_open()
        url = conn.remote_mcp_url
        await conn.close()
        return url

    url = asyncio.run(go())
    assert url.startswith("http://127.0.0.1:")
    assert url.endswith("/mcp/")


@needs_ssh
def test_launcher_runs_a_command_in_the_remote_cwd(tmp_path):
    workdir = tmp_path / "tree"
    workdir.mkdir()
    (workdir / "marker.txt").write_text("here")
    spec = HostSpec(name="localhost", ssh="localhost", cwd=str(workdir))
    conn = HostConnection(spec, control_path=str(tmp_path / "ctl.sock"),
                          mcp_port=9)
    lau = SshLauncher(conn, spec, local_root=str(tmp_path))

    async def go():
        proc = await lau.spawn(["cat", "marker.txt"],
                               cwd=str(workdir), env=None)
        out, _ = await proc.communicate()
        await conn.close()
        return out

    assert asyncio.run(go()).strip() == b"here"


@needs_ssh
def test_preflight_rejects_a_missing_binary(tmp_path):
    from aegis.hosts.errors import HostError

    spec = HostSpec(name="localhost", ssh="localhost", cwd=str(tmp_path))
    conn = HostConnection(spec, control_path=str(tmp_path / "ctl.sock"),
                          mcp_port=9)

    async def go():
        await conn.ensure_open()
        try:
            await conn.preflight("definitely-not-a-real-binary-xyz",
                                 str(tmp_path))
        finally:
            await conn.close()

    with pytest.raises(HostError, match="preflight failed"):
        asyncio.run(go())


@needs_ssh
def test_the_reverse_tunnel_actually_carries_traffic(tmp_path):
    """The assertion that matters: something on the 'remote' side can
    reach a server bound to localhost here, through the -R forward."""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"reached-the-local-server")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    local_port = srv.server_address[1]

    spec = HostSpec(name="localhost", ssh="localhost", cwd=str(tmp_path))
    conn = HostConnection(spec, control_path=str(tmp_path / "ctl.sock"),
                          mcp_port=local_port)
    lau = SshLauncher(conn, spec, local_root=str(tmp_path))

    async def go():
        await conn.ensure_open()
        remote_port = conn.remote_mcp_url.split(":")[2].split("/")[0]
        proc = await lau.spawn(
            ["sh", "-c",
             f"exec 3<>/dev/tcp/127.0.0.1/{remote_port} && "
             f"printf 'GET / HTTP/1.0\\r\\n\\r\\n' >&3 && cat <&3"],
            cwd=str(tmp_path), env=None)
        out, _ = await proc.communicate()
        await conn.close()
        return out

    try:
        assert b"reached-the-local-server" in asyncio.run(go())
    finally:
        srv.shutdown()


@needs_ssh
@pytest.mark.skipif(shutil.which("claude") is None,
                    reason="claude CLI not on PATH")
def test_claude_runs_over_ssh_and_answers(tmp_path):
    from aegis.config import Agent
    from aegis.drivers.claude import ClaudeDriver
    from aegis.events import Result

    spec = HostSpec(name="localhost", ssh="localhost", cwd=str(tmp_path))
    conn = HostConnection(spec, control_path=str(tmp_path / "ctl.sock"),
                          mcp_port=9)
    lau = SshLauncher(conn, spec, local_root=str(tmp_path))
    agent = Agent(harness="claude-code", model="haiku")

    async def go():
        sess = ClaudeDriver().session(
            agent, str(tmp_path), "", "live-remote", lau)
        await sess.start()
        await sess.send("Reply with exactly: PONG")
        texts = []
        async for ev in sess.events():
            texts.append(str(getattr(ev, "text", "")))
            if isinstance(ev, Result):
                break
        await sess.close()
        await conn.close()
        return "".join(texts)

    assert "PONG" in asyncio.run(go()).upper()
```

- [x] **Step 2: Run the live test**

Run: `uv run python -m pytest tests/test_ssh_hosts_live.py -v`
Expected: PASS, or skips with a clear reason if `ssh localhost` is not set up. If you see skips, set up key auth to localhost and re-run — a silently-skipped live test proves nothing.

- [x] **Step 3: Mutation-check the tunnel test**

A test that cannot fail licenses shipping. Temporarily break the forward — in `master_argv`, change `-R` to bind a port that cannot carry traffic (e.g. change `f"{remote_port}:127.0.0.1:{mcp_port}"` to `f"{remote_port}:127.0.0.1:1"`).

Run: `uv run python -m pytest tests/test_ssh_hosts_live.py::test_the_reverse_tunnel_actually_carries_traffic -v`
Expected: **FAIL**. Then revert the change and confirm it passes again. Do not commit the mutation.

- [x] **Step 4: Confirm the fast suite is unaffected**

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS, and the live file contributes zero tests to this run.

- [x] **Step 5: Commit**

```bash
git add tests/test_ssh_hosts_live.py
git commit -m "test(hosts): live end-to-end over ssh localhost, tunnel included"
```

---

## Task 9: `RemoteLinkLost` — a dead tab must not look idle

**Files:**
- Modify: `src/aegis/hosts/errors.py`
- Modify: `src/aegis/hosts/launcher.py` (`SshLauncher`: retain ssh stderr, expose exit state)
- Modify: `src/aegis/drivers/claude.py` (`ClaudeSession._pump_stdout`)
- Test: `tests/test_hosts_launcher.py` (append)

**Interfaces:**
- Consumes: `SshLauncher` (Task 4).
- Produces:
  - `aegis.hosts.errors.RemoteLinkLost(HostError)` with `.host: str` and `.detail: str`.
  - `SshLauncher.link_failure() -> RemoteLinkLost | None` — non-`None` only when the ssh process exited non-zero.
  - `ClaudeSession` emits an `Error` event carrying the link-failure text before its stream-end sentinel.

- [x] **Step 1: Write the failing test**

Append to `tests/test_hosts_launcher.py`:

```python
def test_link_failure_is_none_while_ssh_is_healthy(tmp_path):
    from aegis.hosts.connection import HostConnection
    from aegis.hosts.launcher import SshLauncher

    spec = HostSpec(name="vps", ssh="h", cwd="/x")
    conn = HostConnection(spec, control_path=str(tmp_path / "s.sock"),
                          mcp_port=1)
    lau = SshLauncher(conn, spec)
    assert lau.link_failure() is None


def test_link_failure_reports_the_host_and_the_stderr(tmp_path):
    from aegis.hosts.connection import HostConnection
    from aegis.hosts.errors import RemoteLinkLost
    from aegis.hosts.launcher import SshLauncher

    spec = HostSpec(name="vps", ssh="h", cwd="/x")
    conn = HostConnection(spec, control_path=str(tmp_path / "s.sock"),
                          mcp_port=1)
    lau = SshLauncher(conn, spec)

    class _Dead:
        returncode = 255

    lau._proc = _Dead()
    lau.stderr_tail = [b"ssh: connect to host vps port 22: No route to host"]

    failure = lau.link_failure()
    assert isinstance(failure, RemoteLinkLost)
    assert failure.host == "vps"
    assert "No route to host" in failure.detail


def test_clean_harness_exit_is_not_a_link_failure(tmp_path):
    # rc 0 means the harness ended normally — that is an ordinary end of
    # stream, not a dropped link, and must not be reported as one.
    from aegis.hosts.connection import HostConnection
    from aegis.hosts.launcher import SshLauncher

    spec = HostSpec(name="vps", ssh="h", cwd="/x")
    conn = HostConnection(spec, control_path=str(tmp_path / "s.sock"),
                          mcp_port=1)
    lau = SshLauncher(conn, spec)

    class _Clean:
        returncode = 0

    lau._proc = _Clean()
    assert lau.link_failure() is None
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_hosts_launcher.py -q`
Expected: FAIL with `AttributeError: 'SshLauncher' object has no attribute 'link_failure'`

- [x] **Step 3: Add the error type**

Append to `src/aegis/hosts/errors.py`:

```python
class RemoteLinkLost(HostError):
    """The SSH link carrying a session's harness died.

    Distinct from the harness exiting: to the reading side both look like
    stdout EOF, and without this distinction a dead tab sits there
    looking idle.
    """

    def __init__(self, host: str, detail: str) -> None:
        self.host = host
        self.detail = detail
        super().__init__(
            f"link to {host} lost — {detail or 'no diagnostic output'}")
```

- [x] **Step 4: Retain ssh's stderr and expose the failure**

In `src/aegis/hosts/launcher.py`, add to `SshLauncher.__init__`:

```python
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None
```

At the end of `SshLauncher.spawn`, retain the process and start draining its stderr before returning it:

```python
        proc = await asyncio.create_subprocess_exec(
            *ssh_argv(self._spec, self._conn.control_path, cmd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_LIMIT,
        )
        self._proc = proc
        self._stderr_task = asyncio.create_task(self._drain_stderr(proc))
        return proc
```

And add the two methods:

```python
    async def _drain_stderr(self, proc) -> None:
        """Keep the last of what ssh said. This is the only place the
        difference between 'harness exited' and 'link died' is written
        down."""
        if proc.stderr is None:
            return
        try:
            async for raw in proc.stderr:
                self.stderr_tail.append(raw.rstrip())
                del self.stderr_tail[:-40]
        except Exception:                                    # noqa: BLE001
            pass

    def link_failure(self):
        """A ``RemoteLinkLost`` if ssh died, else ``None``.

        rc 0 means the harness ended normally and the link was fine.
        """
        from aegis.hosts.errors import RemoteLinkLost
        proc = self._proc
        if proc is None or proc.returncode in (None, 0):
            return None
        detail = b"\n".join(self.stderr_tail).decode("utf-8", "replace")
        return RemoteLinkLost(self._spec.name, detail.strip())
```

- [x] **Step 5: Surface it from the session**

In `src/aegis/drivers/claude.py`, replace the `finally` block of `_pump_stdout`:

```python
        finally:
            # A dropped SSH link looks exactly like a clean harness exit
            # from here — stdout just ends. Ask the launcher which it was,
            # so the pane can say "link to vps lost" instead of going
            # quietly idle on a session that no longer exists.
            failure = getattr(self._launcher, "link_failure", lambda: None)()
            if failure is not None:
                await self._queue.put(AssistantText(text=str(failure)))
                await self._queue.put(
                    Result(duration_ms=None, is_error=True,
                           stop_reason="link_lost"))
            await self._queue.put(None)  # always signal stream end
```

Extend the existing events import at the top of `drivers/claude.py` to bring in `AssistantText`:

```python
from aegis.events import (
    AssistantText, Event, ParserState, Result, SystemInit, parse)
```

(keep whatever names that line already imports; only add `AssistantText`.)

There is deliberately **no new event type** here. `events.py` has no `Error` class, and inventing one would mean touching every renderer. `AssistantText` puts the diagnostic in the transcript where a reader will see it, and `Result(is_error=True)` is the terminal event `AgentSession` already keys its `error` state off — so the tab turns red through the path that already exists.

- [x] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_hosts_launcher.py -q`
Expected: PASS

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS with no new failures.

- [x] **Step 7: Commit**

```bash
git add src/aegis/hosts/errors.py src/aegis/hosts/launcher.py \
        src/aegis/drivers/claude.py tests/test_hosts_launcher.py
git commit -m "feat(hosts): a dropped ssh link reports itself instead of looking idle"
```

---

## Task 10: `/reconnect`

**Files:**
- Modify: `src/aegis/core/manager.py` (new `reconnect` method)
- Modify: `src/aegis/commands/builtins/session_ctl.py` (new `/reconnect` command)
- Test: `tests/test_hosts_commands.py`

**Interfaces:**
- Consumes: `AgentSession.place`, `HarnessDriver.supports_resume`, `HarnessSession.session_id`.
- Produces: `SessionManager.reconnect(handle: str) -> str` — rebuilds the session in place via `drv.resume(...)` on the same place, returning a status string. Raises `ValueError` listing every refusal reason at once (the same shape `fork` uses).

- [x] **Step 1: Write the failing test**

Create `tests/test_hosts_commands.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.core.manager import SessionManager
from aegis.hosts.models import HostSpec, Place


class _StubSession:
    supports_idle_events = False

    def __init__(self, session_id=None):
        self._session_id = session_id

    @property
    def session_id(self):
        return self._session_id

    async def start(self):
        pass

    async def send(self, text):
        pass

    async def events(self):
        return
        yield

    async def close(self):
        pass


def _manager(tmp_path, session_id="sid-1"):
    built: list[dict] = []

    def make_session(profile, mcp_url, handle, fork_from=None, place=None,
                     resume_from=None):
        built.append({"place": place, "resume_from": resume_from})
        return _StubSession(session_id)

    mgr = SessionManager(
        agents={"main": Agent(harness="claude-code", model="opus")},
        default_agent="main",
        make_session=make_session,
        hosts={"vps": HostSpec(name="vps", ssh="vps.apiad.net", cwd="/w")},
        local_root=str(tmp_path))
    return mgr, built


def test_reconnect_resumes_on_the_same_place(tmp_path):
    mgr, built = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main", host="vps"))
    asyncio.run(mgr.reconnect(h))
    assert built[-1]["place"] == Place("vps", "/w")
    assert built[-1]["resume_from"] == "sid-1"


def test_reconnect_keeps_the_same_handle(tmp_path):
    mgr, _ = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main", host="vps"))
    asyncio.run(mgr.reconnect(h))
    assert mgr.get(h) is not None


def test_reconnect_refuses_without_a_session_id(tmp_path):
    mgr, _ = _manager(tmp_path, session_id=None)
    h = asyncio.run(mgr.spawn("main", host="vps"))
    with pytest.raises(ValueError, match="no session id"):
        asyncio.run(mgr.reconnect(h))


def test_reconnect_refuses_on_a_local_session(tmp_path):
    mgr, _ = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main"))
    with pytest.raises(ValueError, match="local"):
        asyncio.run(mgr.reconnect(h))
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_hosts_commands.py -q`
Expected: FAIL with `AttributeError: 'SessionManager' object has no attribute 'reconnect'`

- [x] **Step 3: Implement `reconnect`**

Add to `SessionManager` in `src/aegis/core/manager.py`:

```python
    async def reconnect(self, handle: str) -> str:
        """Rebuild a remote session's harness in place, resuming its
        conversation.

        The remote harness keeps its own conversation store, so a dropped
        link costs only the in-flight turn: this re-runs the harness on
        the same host and resumes the same conversation id, in the same
        tab, under the same handle.

        Raises ValueError listing every refusal reason at once.
        """
        s = self.get(handle)
        reasons: list[str] = []
        if s.place.is_local:
            reasons.append(
                f"{handle} runs local — reconnect is for remote sessions")
        sid = s.session_id
        if not sid:
            reasons.append(
                f"{handle} has no session id to resume from")
        if reasons:
            raise ValueError("; ".join(reasons))

        with contextlib.suppress(Exception):
            await s.close()
        url = self._mcp.url if self._mcp is not None else ""
        raw = self._make_session(s.agent, url, handle,
                                 place=s.place, resume_from=sid)
        s.adopt(raw)
        await s.start()
        return f"reconnected {handle} on {s.place.host}"
```

Add `import contextlib` at the top of the module if it is not already imported.

`AgentSession.adopt(raw)` swaps the underlying harness session while keeping everything aegis owns — handle, log id, inbox binding, metrics, observers. The wrapped session is stored as `self._session` (set in `AgentSession.__init__`, `src/aegis/core/session.py:55`). Add:

```python
    def adopt(self, session: HarnessSession) -> None:
        """Replace the underlying harness session in place.

        Everything aegis owns survives: handle, log_id, inbox binding,
        metrics, observers, transcript. Only the process at the bottom
        is new. Used by reconnect after a dropped remote link — which is
        why the tab keeps its history rather than being respawned.
        """
        self._session = session
        self.state = AgentState.ready
```

- [x] **Step 4: Extend the session factory with `resume_from`**

In `src/aegis/cli.py`, add the parameter to `make_session`:

```python
    def make_session(profile, mcp_url, handle, fork_from=None, place=None,
                     resume_from=None):
        place = place or Place("local", cwd)
        if hosts is not None:
            launcher, url = hosts.launcher_for(place, mcp_url)
        else:
            from aegis.hosts.launcher import LocalLauncher
            launcher, url = LocalLauncher(local_root=cwd), mcp_url
        drv = get_driver(profile.harness)
        if fork_from is not None:
            return drv.fork(profile, place.cwd, url, handle, fork_from,
                            launcher)
        if resume_from is not None:
            return drv.resume(profile, place.cwd, url, handle, resume_from,
                              launcher)
        return drv.session(profile, place.cwd, url, handle, launcher)
```

- [x] **Step 5: Add the `/reconnect` slash command**

In `src/aegis/commands/builtins/session_ctl.py`, add the handler and register it alongside the other session-control commands:

```python
async def _reconnect(ctx: CommandContext, args) -> CommandResult:
    """Rebuild a dropped remote session's harness, in place."""
    handle = args.get("handle") or ctx.handle
    try:
        msg = await ctx.bridge.reconnect(handle)
    except ValueError as e:
        return CommandResult(False, f"cannot reconnect: {e}")
    return CommandResult(True, msg)
```

Register it in the module's command list, matching the surrounding `SlashCommand(...)` entries:

```python
    SlashCommand("reconnect", "rebuild a dropped remote session in place",
                 "/reconnect [handle]", _reconnect,
                 Args(ArgSpec("handle", required=False))),
```

Read the neighbouring registrations first and match their exact `Args`/`ArgSpec` construction — the parser API is declarative and the surrounding entries are the reference.

- [x] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_hosts_commands.py -q`
Expected: PASS (4 tests)

- [x] **Step 7: Commit**

```bash
git add src/aegis/core/manager.py src/aegis/core/session.py \
        src/aegis/cli.py src/aegis/commands/builtins/session_ctl.py \
        tests/test_hosts_commands.py
git commit -m "feat(hosts): /reconnect resumes a dropped remote session in place"
```

---

## Task 11: Host-scoped claims

**Files:**
- Modify: `src/aegis/locks/models.py` (`Claim`, `claims_overlap`)
- Modify: `src/aegis/locks/registry.py` (`ClaimRegistry.claim`)
- Modify: `src/aegis/locks/persistence.py` (round-trip the new field)
- Modify: `src/aegis/mcp/server.py` (`aegis_claim`, `aegis_claims`)
- Modify: `src/aegis/mcp/bridge.py` (`SessionInfo.host`)
- Test: `tests/test_hosts_claims.py`

**Interfaces:**
- Consumes: `Place.host` (Task 2), `AgentSession.place` (Task 7).
- Produces: `Claim.host: str = "local"`; `claims_overlap` returns `False` across hosts; `ClaimRegistry.claim(..., host: str = "local")`; `SessionInfo.host: str = "local"`.

- [x] **Step 1: Write the failing test**

Create `tests/test_hosts_claims.py`:

```python
from __future__ import annotations

from aegis.locks.models import Claim, claims_overlap


def _claim(handle, files=(), prefixes=(), intent="shared", host="local"):
    return Claim(claim_id=f"c-{handle}-{host}", handle=handle,
                 prefixes=frozenset(prefixes), files=frozenset(files),
                 intent=intent, desc="", since="2026-08-04T00:00:00Z",
                 host=host)


def test_same_path_on_different_hosts_does_not_overlap():
    a = _claim("one", files=["src/foo.py"], host="local")
    b = _claim("two", files=["src/foo.py"], host="vps")
    assert not claims_overlap(a, b)


def test_same_path_on_the_same_host_still_overlaps():
    a = _claim("one", files=["src/foo.py"], host="vps")
    b = _claim("two", files=["src/foo.py"], host="vps")
    assert claims_overlap(a, b)


def test_prefix_containment_is_host_scoped():
    a = _claim("one", prefixes=["src/aegis/"], host="local")
    b = _claim("two", files=["src/aegis/tui/app.py"], host="vps")
    assert not claims_overlap(a, b)
    c = _claim("two", files=["src/aegis/tui/app.py"], host="local")
    assert claims_overlap(a, c)


def test_host_defaults_to_local():
    assert Claim(claim_id="x", handle="h", prefixes=frozenset(),
                 files=frozenset(["a"]), intent="shared", desc="",
                 since="2026-08-04T00:00:00Z").host == "local"


def test_exclusive_on_another_host_does_not_block(tmp_path):
    from aegis.locks.registry import ClaimRegistry

    reg = ClaimRegistry()
    reg.claim("remote-one", prefixes=[], files=["src/foo.py"],
              intent="exclusive", host="vps")
    _c, granted, overlaps = reg.claim(
        "local-one", prefixes=[], files=["src/foo.py"],
        intent="exclusive", host="local")
    assert granted
    assert overlaps == []


def test_exclusive_on_the_same_host_still_blocks():
    from aegis.locks.registry import ClaimRegistry

    reg = ClaimRegistry()
    reg.claim("one", prefixes=[], files=["src/foo.py"],
              intent="exclusive", host="vps")
    _c, granted, overlaps = reg.claim(
        "two", prefixes=[], files=["src/foo.py"],
        intent="exclusive", host="vps")
    assert not granted
    assert [o.handle for o in overlaps] == ["one"]
```

`ClaimRegistry()` may require constructor arguments (a log, a live-handle filter) — read `src/aegis/locks/registry.py` and construct it the way the existing `tests/` for locks do.

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_hosts_claims.py -q`
Expected: FAIL with `TypeError: Claim.__init__() got an unexpected keyword argument 'host'`

- [x] **Step 3: Add `host` to the model and the overlap rule**

In `src/aegis/locks/models.py`, add the field to `Claim` (last, with a default, so existing positional construction is unaffected):

```python
@dataclass
class Claim:
    claim_id: str
    handle: str
    prefixes: frozenset[str]   # each ends with "/"
    files: frozenset[str]      # exact paths, no trailing "/"
    intent: str                # "shared" | "exclusive"
    desc: str
    since: str                 # ISO-8601
    host: str = "local"        # which machine these paths are on
```

And gate `claims_overlap` at the top:

```python
def claims_overlap(a: Claim, b: Claim) -> bool:
    # Paths only mean something relative to a machine. The same string
    # names a different file on a different host, so claims on different
    # hosts can never overlap — treating them as though they could would
    # both block honest work and pretend to protect files it does not.
    if a.host != b.host:
        return False
    # file ∩ file
    if a.files & b.files:
        return True
    ...
```

The rest of the function is unchanged.

- [x] **Step 4: Thread `host` through the registry**

In `src/aegis/locks/registry.py`:

```python
    def claim(self, handle: str, prefixes, files,
              intent: str = "shared",
              desc: str = "",
              host: str = "local") -> tuple[Claim, bool, list[Claim]]:
        self._prune_dead()
        candidate = Claim(claim_id=new_ulid(), handle=handle,
                          prefixes=frozenset(prefixes), files=frozenset(files),
                          intent=intent, desc=desc, since=now_iso(),
                          host=host)
```

The rest of the method is unchanged.

In `src/aegis/locks/persistence.py`, include `host` in the serialised record and read it back with a `"local"` default, so pre-existing JSONL logs replay unchanged.

- [x] **Step 5: Have `aegis_claim` derive the host from the caller**

In `src/aegis/mcp/bridge.py`, add to `SessionInfo`:

```python
    host: str = "local"     # which machine this session's harness runs on
```

In `src/aegis/mcp/server.py`, inside `aegis_claim`, look the caller up before claiming and pass its host through:

```python
        host = next((s.host for s in bridge.sessions()
                     if s.handle == from_handle), "local")
```

then add `host=host` to the `registry.claim(...)` call. In `aegis_claims`, include `"host": c.host` in each rendered dict so the board shows where each claim lives.

Wherever `SessionInfo` objects are constructed from `AgentSession`s, pass `host=s.place.host`.

- [x] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_hosts_claims.py -q`
Expected: PASS (6 tests)

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS with no new failures.

- [x] **Step 7: Commit**

```bash
git add src/aegis/locks/models.py src/aegis/locks/registry.py \
        src/aegis/locks/persistence.py src/aegis/mcp/bridge.py \
        src/aegis/mcp/server.py tests/test_hosts_claims.py
git commit -m "feat(locks): claims are host-scoped — same path, different machine, no overlap"
```

---

## Task 12: Off-host file affordances

**Files:**
- Modify: `src/aegis/render_shared.py` (`file_target`)
- Modify: `src/aegis/tui/pane.py` (pass the pane's host; render the qualified path)
- Test: `tests/test_hosts_render.py`

**Interfaces:**
- Consumes: `Place.qualify` (Task 2), `AgentSession.place` (Task 7).
- Produces: `file_target(name, raw_input, locations=(), host: str = "local") -> FileTarget | None` — returns `None` whenever `host != "local"`.

- [x] **Step 1: Write the failing test**

Create `tests/test_hosts_render.py`:

```python
from __future__ import annotations

from aegis.hosts.models import Place
from aegis.render_shared import file_target


def test_local_read_still_yields_a_target():
    t = file_target("Read", {"file_path": "/home/apiad/Workspace/x.py"})
    assert t is not None
    assert t.path == "/home/apiad/Workspace/x.py"


def test_remote_read_yields_no_local_target():
    # The same path exists on both machines and is a DIFFERENT file.
    # Opening the local one would be a silent wrong answer, which is
    # worse than not opening anything.
    t = file_target("Read", {"file_path": "/home/apiad/Workspace/x.py"},
                    host="vps")
    assert t is None


def test_remote_edit_yields_no_local_target():
    assert file_target(
        "Edit", {"file_path": "/x.py", "old_string": "a", "new_string": "b"},
        host="vps") is None


def test_place_qualifies_a_path_for_display():
    assert Place("vps", "/w").qualify("/w/x.py") == "vps:/w/x.py"
    assert Place("local", "/w").qualify("/w/x.py") == "/w/x.py"
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_hosts_render.py -q`
Expected: FAIL with `TypeError: file_target() got an unexpected keyword argument 'host'`

- [x] **Step 3: Gate `file_target` on the host**

In `src/aegis/render_shared.py`:

```python
def file_target(name: str, raw_input: dict | None,
                locations=(), host: str = "local") -> FileTarget | None:
    """Which file (and line) a tool call points at, if any. Pure.

    ``host`` is the machine the session's harness runs on. A path from a
    remote session names a file on THAT machine; the identically-named
    local file is a different file, so there is no local target to
    offer — the caller shows the qualified path instead.
    """
    if host != "local":
        return None
    inp = raw_input or {}
    ...
```

The rest of the function is unchanged.

- [x] **Step 4: Pass the pane's host at every call site**

In `src/aegis/tui/pane.py`, find each `file_target(...)` call and add `host=self._core.place.host` (use whatever accessor the pane already has for its session core). At the block-gesture handler (`CopyableBlock.on_click`), when `file_target` returns `None` **and** the pane is remote, ctrl+click copies the qualified path rather than doing nothing:

```python
        if target is None and place.host != "local":
            path = (raw_input or {}).get("file_path")
            if path:
                self.app.copy_to_clipboard(place.qualify(str(path)))
                self.notify(f"copied {place.qualify(str(path))}")
                return
```

Also disable the `@`-file picker on a remote pane: where the pane opens `FilePickerModal`, return early with a notice when `place.host != "local"` — the local index would complete local paths into a prompt that will be read on the remote.

Add an `@<host>` suffix to the pane's tab label and status bar wherever the handle is rendered, so a pane's machine is never something you have to remember.

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_hosts_render.py -q`
Expected: PASS (4 tests)

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS with no new failures.

- [x] **Step 6: Commit**

```bash
git add src/aegis/render_shared.py src/aegis/tui/pane.py \
        tests/test_hosts_render.py
git commit -m "feat(tui): a remote pane shows host-qualified paths instead of opening local ones"
```

---

## Task 13: `/spawn <agent>@<host>[:<cwd>]`

**Files:**
- Modify: `src/aegis/commands/builtins/core.py` (`_spawn`)
- Create: the parser helper in `src/aegis/hosts/resolve.py`
- Test: `tests/test_hosts_commands.py` (append)

**Interfaces:**
- Consumes: `SessionManager.spawn(host=, cwd=)` (Task 7).
- Produces: `aegis.hosts.resolve.parse_at_host(token: str) -> tuple[str, str | None, str | None]` returning `(agent, host, cwd)`.

- [x] **Step 1: Write the failing test**

Append to `tests/test_hosts_commands.py`:

```python
from aegis.hosts.resolve import parse_at_host


def test_plain_agent_has_no_host():
    assert parse_at_host("claude-code") == ("claude-code", None, None)


def test_agent_at_host():
    assert parse_at_host("claude-code@vps") == ("claude-code", "vps", None)


def test_agent_at_host_with_cwd():
    assert parse_at_host("claude-code@vps:/srv/app") == (
        "claude-code", "vps", "/srv/app")


def test_cwd_may_contain_colons_after_the_first():
    assert parse_at_host("main@vps:/srv/a:b") == ("main", "vps", "/srv/a:b")


def test_empty_host_is_treated_as_absent():
    assert parse_at_host("main@") == ("main", None, None)


def test_spawn_command_passes_host_and_cwd(tmp_path):
    mgr, built = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main", host="vps", cwd="/srv/app"))
    assert mgr.get(h).place == Place("vps", "/srv/app")
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_hosts_commands.py -q`
Expected: FAIL with `ImportError: cannot import name 'parse_at_host'`

- [x] **Step 3: Write the parser**

Append to `src/aegis/hosts/resolve.py`:

```python
def parse_at_host(token: str) -> tuple[str, str | None, str | None]:
    """Split ``agent@host:/cwd`` into its three parts.

    ``agent`` alone, ``agent@host``, and ``agent@host:/path`` are all
    valid. Only the FIRST colon after the host separates the cwd, so a
    path containing colons survives intact.
    """
    agent, sep, rest = token.partition("@")
    if not sep or not rest:
        return agent, None, None
    host, csep, cwd = rest.partition(":")
    if not host:
        return agent, None, None
    return agent, host, (cwd if csep and cwd else None)
```

- [x] **Step 4: Use it in `/spawn`**

In `src/aegis/commands/builtins/core.py`, inside `_spawn`, parse the agent token before dispatching:

```python
    from aegis.hosts.resolve import parse_at_host

    agent, host, cwd = parse_at_host(agent)
    handle = await ctx.bridge.spawn(agent, opening_prompt=prompt,
                                    spawned_by=ctx.handle,
                                    host=host, cwd=cwd,
                                    model=model, effort=effort)
```

Keep the existing `model` / `effort` arguments exactly as the current call passes them; only `host` and `cwd` are added. Update the command's usage string in its `SlashCommand(...)` registration:

```python
                 "/spawn <agent>[@host[:cwd]] [prompt] [--model M] [--effort E]",
```

Extend the `Arg.completer` for the agent argument so the palette offers `<agent>@<host>` combinations, sourced from the bridge's known hosts.

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_hosts_commands.py -q`
Expected: PASS

- [x] **Step 6: Commit**

```bash
git add src/aegis/hosts/resolve.py src/aegis/commands/builtins/core.py \
        tests/test_hosts_commands.py
git commit -m "feat(commands): /spawn agent@host[:cwd]"
```

---

## Task 14: `aegis_spawn(host=, cwd=)` and host in the peer list

**Files:**
- Modify: `src/aegis/mcp/server.py` (`aegis_spawn`, `aegis_list_sessions`, `BRIEFING`)
- Modify: `src/aegis/mcp/bridge.py` (`AppBridge.spawn` protocol signature)
- Test: `tests/test_hosts_commands.py` (append)

**Interfaces:**
- Consumes: `SessionManager.spawn(host=, cwd=)` (Task 7), `SessionInfo.host` (Task 11).
- Produces: `aegis_spawn(profile, opening_prompt, from_handle, host=None, cwd=None)`; `aegis_list_sessions` entries carry `host`.

- [x] **Step 1: Write the failing test**

Append to `tests/test_hosts_commands.py`:

```python
def test_session_info_reports_the_host(tmp_path):
    mgr, _ = _manager(tmp_path)
    asyncio.run(mgr.spawn("main", host="vps"))
    asyncio.run(mgr.spawn("main"))
    hosts = sorted(s.host for s in mgr.sessions())
    assert hosts == ["local", "vps"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_hosts_commands.py -q`
Expected: FAIL with `AttributeError: 'SessionInfo' object has no attribute 'host'` (or the sessions list not carrying it).

- [x] **Step 3: Add the parameters and the reporting**

In `src/aegis/mcp/bridge.py`, add `host: str | None = None, cwd: str | None = None` to the `AppBridge.spawn` protocol signature.

In `src/aegis/mcp/server.py`, extend `aegis_spawn`:

```python
    async def aegis_spawn(profile: str, opening_prompt: str,
                          from_handle: str,
                          host: str | None = None,
                          cwd: str | None = None) -> dict:
        """Spawn a new top-level peer agent and hand it an opening prompt.

        ``host`` places the new agent's harness on another machine — a
        key from the `hosts:` config, or "local" (the default). ``cwd``
        overrides that host's default working directory. The spawned
        agent is an ordinary peer: it appears in aegis_list_sessions,
        can be handed off to, and can call back through the same MCP
        surface you are using now.
        """
```

then pass `host=host, cwd=cwd` through to `bridge.spawn(...)`.

In `aegis_list_sessions`, include `"host": s.host` in each rendered entry.

In the `BRIEFING` text, add one line under the `aegis_list_sessions` bullet:

```
    Each entry also carries `host` — the machine that peer's harness runs on
    ("local", or a configured host like "vps"). A peer on another host reads
    and writes THAT machine's files; paths are not interchangeable between
    hosts, and file claims are scoped per host.
```

and one under `aegis_spawn`:

```
    Pass host="vps" (and optionally cwd=) to place the new agent's harness on
    another machine. It stays an ordinary peer of yours over this same MCP
    surface — only its filesystem and shell are elsewhere.
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_hosts_commands.py -q`
Expected: PASS

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS with no new failures.

- [x] **Step 5: Commit**

```bash
git add src/aegis/mcp/server.py src/aegis/mcp/bridge.py \
        tests/test_hosts_commands.py
git commit -m "feat(mcp): aegis_spawn places an agent on a host; peers report theirs"
```

---

## Task 15: Ctrl+N host tier

**Files:**
- Modify: `src/aegis/tui/picker.py` (`build_picker_rows`, a `HostPicker`)
- Modify: `src/aegis/tui/app.py` (the Ctrl+N action)
- Test: `tests/test_hosts_render.py` (append)

**Interfaces:**
- Consumes: `HostRegistry.known()` (Task 6), `Agent.host` (Task 2).
- Produces: `aegis.tui.picker.build_host_rows(hosts: list[str], local_label: str) -> list[tuple[str, str]]` — `(value, label)` pairs, `local` always first.

- [x] **Step 1: Write the failing test**

Append to `tests/test_hosts_render.py`:

```python
def test_host_rows_put_local_first():
    from aegis.tui.picker import build_host_rows

    rows = build_host_rows(["vps", "smaug"], local_label="/home/apiad/Work")
    assert rows[0][0] == "local"
    assert "/home/apiad/Work" in rows[0][1]
    assert [v for v, _ in rows] == ["local", "smaug", "vps"]


def test_host_rows_with_no_configured_hosts_offer_only_local():
    from aegis.tui.picker import build_host_rows

    assert [v for v, _ in build_host_rows([], local_label="/x")] == ["local"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_hosts_render.py -q`
Expected: FAIL with `ImportError: cannot import name 'build_host_rows'`

- [x] **Step 3: Add the row builder**

In `src/aegis/tui/picker.py`, beside `build_picker_rows`:

```python
def build_host_rows(hosts: list[str],
                    local_label: str) -> list[tuple[str, str]]:
    """(value, label) rows for the host tier of the spawn picker.

    `local` is always first and always present — it is the implicit host
    and the overwhelmingly common choice.
    """
    rows = [("local", f"local — {local_label}")]
    rows += [(h, h) for h in sorted(hosts)]
    return rows
```

- [x] **Step 4: Wire the tier into Ctrl+N**

In `src/aegis/tui/app.py`, in the new-tab action, push a `_ChoicePicker` built from `build_host_rows(...)` **before** the existing `AgentPicker`, then pass the chosen host into the spawn call. When the chosen agent profile carries a `host:` default, pre-select that row. Escape at the host tier cancels the whole spawn, matching how escape behaves at the agent tier.

The existing `_ChoicePicker(options, ...)` modal already takes `(value, label)` pairs, so no new modal class is required — read its constructor and use it directly.

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_hosts_render.py -q`
Expected: PASS

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS with no new failures.

- [x] **Step 6: Manually verify the picker**

Run `uv run aegis` in a directory with a `hosts:` entry configured, press `Ctrl+N`, and confirm the host tier appears with `local` first. This is a TUI affordance — a passing unit test on the row builder does not prove the modal renders.

- [x] **Step 7: Commit**

```bash
git add src/aegis/tui/picker.py src/aegis/tui/app.py \
        tests/test_hosts_render.py
git commit -m "feat(tui): Ctrl+N picks a host before an agent"
```

---

## Task 16: `aegis config host add|remove|list`

**Files:**
- Modify: `src/aegis/cli_config.py`
- Modify: `src/aegis/config/edit.py`
- Test: `tests/test_hosts_config.py` (append)

**Interfaces:**
- Consumes: `HostSpec` (Task 2).
- Produces: `aegis.config.edit.add_host(root, name, ssh, cwd, ssh_opts=None)` and `remove_host(root, name)` — comment-preserving ruamel edits with atomic tempfile rename, matching the existing `add_agent`/`remove_agent` helpers.

- [x] **Step 1: Write the failing test**

Append to `tests/test_hosts_config.py`:

```python
def test_add_host_writes_a_loadable_entry(tmp_path):
    from aegis.config.edit import add_host

    (tmp_path / ".aegis.yaml").write_text("")
    add_host(tmp_path, "vps", ssh="vps.apiad.net", cwd="/home/apiad/Workspace")
    cfg = load_config(tmp_path)
    assert cfg.hosts["vps"].ssh == "vps.apiad.net"
    assert cfg.hosts["vps"].cwd == "/home/apiad/Workspace"


def test_add_host_preserves_comments(tmp_path):
    from aegis.config.edit import add_host

    (tmp_path / ".aegis.yaml").write_text(
        "# keep me\ndefault_agent: main\n"
        "agents:\n  main:\n    harness: claude-code\n    model: opus\n")
    add_host(tmp_path, "vps", ssh="h", cwd="/w")
    assert "# keep me" in (tmp_path / ".aegis.yaml").read_text()


def test_remove_host(tmp_path):
    from aegis.config.edit import add_host, remove_host

    (tmp_path / ".aegis.yaml").write_text("")
    add_host(tmp_path, "vps", ssh="h", cwd="/w")
    remove_host(tmp_path, "vps")
    assert load_config(tmp_path).hosts == {}
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_hosts_config.py -q`
Expected: FAIL with `ImportError: cannot import name 'add_host'`

- [x] **Step 3: Implement the editors**

In `src/aegis/config/edit.py`, add `add_host` and `remove_host` using the module's existing private helpers — `_load(path)` (ruamel round-trip, comment-preserving), `_validate_and_dump(root, data)` (re-parses the edited document so a bad edit fails before it is written), and `_atomic_write(path, payload)`. Read `add_agent` (`src/aegis/config/edit.py:144`) and `remove_agent` (`:203`) and mirror their exact structure:

```python
def add_host(root: Path, name: str, *, ssh: str, cwd: str,
             ssh_opts: list[str] | None = None) -> None:
    """Add a `hosts:` entry, preserving comments and formatting."""
    path = root / ".aegis.yaml"
    data = _load(path)
    hosts = data.setdefault("hosts", {})
    if name in hosts:
        raise ConfigError(f"host {name!r} already exists")
    entry: dict[str, Any] = {"ssh": ssh, "cwd": cwd}
    if ssh_opts:
        entry["ssh_opts"] = list(ssh_opts)
    hosts[name] = entry
    _atomic_write(path, _validate_and_dump(root, data))


def remove_host(root: Path, name: str) -> None:
    """Drop a `hosts:` entry. Persisted; takes effect on next start."""
    path = root / ".aegis.yaml"
    data = _load(path)
    hosts = data.get("hosts") or {}
    if name not in hosts:
        raise ConfigError(f"host {name!r} is not declared")
    del hosts[name]
    if not hosts:
        data.pop("hosts", None)
    _atomic_write(path, _validate_and_dump(root, data))
```

Confirm against `add_agent` whether `_validate_and_dump` takes `(root, data)` in that order and whether `_atomic_write` takes the file path or the root — match the call shape it already uses rather than the one written above if they differ.

- [x] **Step 4: Mount the CLI subcommands**

In `src/aegis/cli_config.py`, add a `host` sub-app with `add`, `remove`, and `list` verbs, mirroring the existing `agent` sub-app's structure, options, and output formatting. `add` is hot-registering only in the sense that the next spawn picks it up from the reloaded config; `remove` needs a restart — say so in the command's help text, consistent with the other `remove` verbs.

- [x] **Step 5: Run tests and the CLI**

Run: `uv run python -m pytest tests/test_hosts_config.py -q`
Expected: PASS

Run: `uv run aegis config host list`
Expected: the configured hosts, or an empty listing without a traceback.

- [x] **Step 6: Commit**

```bash
git add src/aegis/cli_config.py src/aegis/config/edit.py \
        tests/test_hosts_config.py
git commit -m "feat(config): aegis config host add|remove|list"
```

---

## Task 17: Documentation

**Files:**
- Create: `know-how/ssh-execution-hosts.md`
- Modify: `AGENTS.md` (Know-how index + Layout)
- Modify: `CHANGELOG.md`
- Modify: `TASKS.md`
- Modify: `docs/superpowers/specs/2026-08-04-aegis-ssh-execution-hosts-design.md` (status header)

**Interfaces:**
- Consumes: everything.
- Produces: nothing.

- [x] **Step 1: Write the know-how doc**

Create `know-how/ssh-execution-hosts.md` covering, with worked commands:

- What it is and what it is not (the three-way table from the spec).
- Configuring a host: the `hosts:` block, and `aegis config host add`.
- Spawning on a host: `Ctrl+N`, `/spawn main@vps`, `/spawn main@vps:/srv/app`, `aegis_spawn(..., host="vps")`.
- What the remote box needs: the harness CLI on `PATH`, an ssh key, nothing else. No aegis install, no `.aegis.yaml`.
- Debug checklist, each item a command to run:
  - `ssh <host> true` — reachability.
  - `ssh <host> 'command -v claude'` — the preflight, by hand.
  - `ssh -O check -o ControlPath=.aegis/state/ssh/<host>.sock <host>` — is the master alive.
  - `ssh -v -N -R 0:127.0.0.1:1234 <host>` — does the forward get allocated, and what does the announcement line look like on this sshd. If the phrasing differs from `Allocated port N for remote forward`, set `remote_mcp_port:` on the host.
  - `LogLevel=QUIET` in `~/.ssh/config` suppresses the announcement — the symptom is a 20s timeout on first spawn.
- The security notes: argv is visible in the remote `ps`; the tunneled MCP port is unauthenticated on the remote loopback.
- Why paths are host-scoped: ctrl+click, claims, `@`-completion.
- When to use `aegis --remote ssh://` instead (durability).

- [x] **Step 2: Index it in `AGENTS.md`**

Add to the Know-how list:

```markdown
- `know-how/ssh-execution-hosts.md` — *reach for it when running a harness on
  another machine via the `hosts:` config (`/spawn main@vps`), or debugging
  the SSH ControlMaster / reverse MCP tunnel.*
```

Add a Layout entry after the `src/aegis/locks/` bullet:

```markdown
- `src/aegis/hosts/` - SSH execution hosts (eighth coordination
  primitive): running a harness process on another machine while the
  session, transcript and MCP peer identity stay local. `models.py`
  (`HostSpec` config entry + `Place` resolved host+cwd); `resolve.py`
  (`resolve_place` precedence — explicit > profile default > local — and
  `parse_at_host` for `agent@host:/cwd`); `launcher.py` (the `Launcher`
  seam both driver families spawn through: `LocalLauncher` is today's
  `create_subprocess_exec`, `SshLauncher` wraps the same argv into
  `ssh -T <dest> 'cd <cwd> && exec <argv>'`); `connection.py`
  (`HostConnection` — one ControlMaster per host, `-R 0` reverse tunnel
  carrying the local MCP port with the allocated port parsed off ssh's
  stderr, preflight, teardown); `registry.py` (`HostRegistry` — one
  connection per host; `launcher_for` is SYNCHRONOUS because
  `_sync_spawn` is, so the connection opens lazily inside
  `SshLauncher.spawn`). Host is a third orthogonal spawn axis beside
  agent profile and harness — any harness on any host, resolved per
  spawn and never persisted. Paths are host-scoped: `Claim.host` gates
  overlap, and `file_target` returns `None` off-host so ctrl+click
  cannot silently open the identically-named local file. NOT the same as
  `--remote` (TUI attached to a remote serve) or `remotes:` (federated
  serves) — see `know-how/ssh-execution-hosts.md`.
```

- [x] **Step 3: Update `CHANGELOG.md` and `TASKS.md`**

Add a CHANGELOG entry under the unreleased heading describing the feature in the repo's existing voice. In `TASKS.md`, add the shipped item and remove it from anything that lists it as pending.

- [x] **Step 4: Flip the spec status**

In the spec's header, change `**Status:** approved, not yet planned` to `**Status:** implemented` and add the plan path beside the spec path. A stale status header misleads the next `/workon`.

- [x] **Step 5: Final full-suite gate**

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS.

Run this as its own tool call, and read the exit code directly — do not pipe it through `tail` or append `echo "rc=$?"`, both of which hand a green result to whatever reads it regardless of what pytest did.

- [x] **Step 6: Commit**

```bash
git add know-how/ssh-execution-hosts.md AGENTS.md CHANGELOG.md TASKS.md \
        docs/superpowers/specs/2026-08-04-aegis-ssh-execution-hosts-design.md
git commit -m "docs(hosts): know-how, layout entry, changelog"
```

---

## Post-implementation verification

Not a task — the manual check that the feature actually works, done on real machines rather than against `localhost`.

- [ ] Add `vps` to `hosts:` pointing at `vps.apiad.net` / `/home/apiad/Workspace`.
- [ ] `Ctrl+N` → `vps` → an agent profile. Confirm the pane opens and the tab shows `@vps`.
- [ ] In that pane, ask the agent to run `hostname` and `pwd`. Expect `vps` and `/home/apiad/Workspace` — **not** zion's. This is the whole point of the feature; anything else means the launcher didn't take.
- [ ] In that pane, ask the agent to call `aegis_list_sessions()`. It must see the local peers — that proves the reverse tunnel carries the MCP plane.
- [ ] From the local pane, `@<remote-handle> <question>` and confirm the remote peer answers.
- [ ] Confirm one `ssh` master exists, not one per session: `ps aux | grep 'ssh -M'` should show a single process after opening two remote tabs.
- [ ] Drop the link on purpose (`ssh -O exit -o ControlPath=… vps`), confirm the pane reports the loss rather than going idle, then `/reconnect` and confirm the conversation resumes with its history.
- [ ] Ctrl+click a Read block in the remote pane; confirm it copies `vps:/…` and does **not** open zion's identically-named file.
