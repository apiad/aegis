# Aegis Remote Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** First-class aegis-to-aegis enqueue over a narrow HTTP plane on `aegis serve`, so an MCP agent can call `aegis_enqueue(..., target="vps")` and have the task land in a remote aegis's QueueManager — no SSH, no GitHub round-trip, no shell-out.

**Architecture:** New `src/aegis/remote/` module exposing one Starlette app (`POST /remote/v1/enqueue`) plus an httpx client. The local `aegis_enqueue` MCP tool grows a `target` parameter that routes to the client when set. Config lives in `.aegis.yaml` under new top-level keys `remotes` (outbound allowlist) and `remote_plane` (inbound bind + auth). Trust anchor is the Headscale tailnet; optional bearer-token + IP-allowlist for defense-in-depth.

**Tech Stack:** Python 3.13+, Starlette + uvicorn (both transitive via FastMCP; pin them as direct deps), httpx (already a direct dep), pytest, ruamel.yaml.

**Spec reference:** `docs/superpowers/specs/2026-05-25-aegis-remote-plane-design.md`.

---

## File Structure

**Created:**
- `src/aegis/remote/__init__.py` — re-exports `RemoteSpec`, `RemotePlaneSpec`, `remote_enqueue`, `build_plane`.
- `src/aegis/remote/config.py` — `RemoteSpec`, `RemotePlaneSpec` dataclasses.
- `src/aegis/remote/client.py` — `remote_enqueue(spec, queue, payload, from_)` async free function; normalized errors.
- `src/aegis/remote/plane.py` — `build_plane(queue_manager, plane_spec) -> Starlette` and `run_plane_async(app, bind) -> asyncio.Task`.
- `tests/test_remote_config.py` — YAML parse + overlay merge + validation.
- `tests/test_remote_plane.py` — endpoint happy-path + auth gates (token, IP).
- `tests/test_remote_client.py` — client wired against an in-process plane.
- `tests/test_remote_mcp_target.py` — MCP tool `target=` routing.
- `tests/test_remote_live.py` — opt-in live roundtrip to the VPS (`@pytest.mark.live`).

**Modified:**
- `src/aegis/config/yaml_loader.py` — add `remotes` to `_SECTIONS` (multi-entry overlay), add inline `remote_plane` block; extend `AegisConfig` with `remotes: dict[str, RemoteSpec]` and `remote_plane: RemotePlaneSpec | None`.
- `src/aegis/mcp/bridge.py` — add `remotes: object` to the `AppBridge` Protocol.
- `src/aegis/mcp/server.py` — extend `aegis_enqueue` with `target: str | None = None`; route to `aegis.remote.client.remote_enqueue` when set.
- `src/aegis/core/manager.py` — populate `self.remotes` from loaded config.
- `src/aegis/tui/app.py` — populate `self.remotes` from loaded config (Protocol compliance).
- `src/aegis/cli.py` — in `serve`, start the remote plane if `remote_plane` is configured.
- `pyproject.toml` — add `starlette` and `uvicorn` as explicit dependencies.

**Note for executors:** Most tasks touch ≥2 files. Before any task that writes more than one file, acquire a workspace lock per CLAUDE.md (`bin/ws-lock acquire <paths> --desc "..."`).

---

## Task 1: Config Dataclasses + YAML Loader

**Files:**
- Create: `src/aegis/remote/__init__.py`
- Create: `src/aegis/remote/config.py`
- Modify: `src/aegis/config/yaml_loader.py`
- Test: `tests/test_remote_config.py`

- [ ] **Step 1: Write the failing test for the config dataclasses**

Create `tests/test_remote_config.py`:

```python
from __future__ import annotations

import pytest

from aegis.remote.config import RemoteSpec, RemotePlaneSpec


def test_remote_spec_minimal() -> None:
    spec = RemoteSpec(url="http://vps.tail-net.ts.net:8556")
    assert spec.url == "http://vps.tail-net.ts.net:8556"
    assert spec.token is None


def test_remote_spec_with_token() -> None:
    spec = RemoteSpec(url="http://vps:8556", token="secret")
    assert spec.token == "secret"


def test_remote_spec_rejects_missing_scheme() -> None:
    with pytest.raises(ValueError, match="must include scheme"):
        RemoteSpec(url="vps:8556")


def test_remote_plane_spec_minimal() -> None:
    p = RemotePlaneSpec(bind="100.64.0.1:8556")
    assert p.bind == "100.64.0.1:8556"
    assert p.accept_tokens == []
    assert p.accept_from == []


def test_remote_plane_spec_rejects_unparseable_bind() -> None:
    with pytest.raises(ValueError, match="bind"):
        RemotePlaneSpec(bind="not-a-host-port")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_remote_config.py -v`
Expected: ImportError or ModuleNotFoundError on `aegis.remote.config`.

- [ ] **Step 3: Create the package skeleton**

Create `src/aegis/remote/__init__.py`:

```python
"""Aegis remote plane: server-to-server enqueue.

See docs/superpowers/specs/2026-05-25-aegis-remote-plane-design.md.
"""
from aegis.remote.config import RemoteSpec, RemotePlaneSpec

__all__ = ["RemoteSpec", "RemotePlaneSpec"]
```

Create `src/aegis/remote/config.py`:

```python
"""Config dataclasses for the remote plane."""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass(frozen=True)
class RemoteSpec:
    """Outbound remote target — one entry in the `remotes` mapping."""
    url: str
    token: str | None = None

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                f"remote url must include scheme + host: {self.url!r}")


@dataclass(frozen=True)
class RemotePlaneSpec:
    """Inbound plane config — single `remote_plane` block."""
    bind: str
    accept_tokens: list[str] = field(default_factory=list)
    accept_from: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        host, _, port = self.bind.rpartition(":")
        if not host or not port.isdigit():
            raise ValueError(
                f"remote_plane.bind must be host:port, got {self.bind!r}")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_remote_config.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Write a failing test for YAML loading of `remotes` + `remote_plane`**

Append to `tests/test_remote_config.py`:

```python
from pathlib import Path

from aegis.config.yaml_loader import load_config


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def test_load_remotes_inline(tmp_path: Path) -> None:
    _write(tmp_path / ".aegis.yaml", """
remotes:
  vps:
    url: http://vps.tail-net.ts.net:8556
""")
    cfg = load_config(tmp_path)
    assert "vps" in cfg.remotes
    assert cfg.remotes["vps"].url == "http://vps.tail-net.ts.net:8556"
    assert cfg.remotes["vps"].token is None


def test_load_remotes_overlay(tmp_path: Path) -> None:
    _write(tmp_path / ".aegis.yaml", "")
    _write(tmp_path / ".aegis" / "remotes" / "vps.yaml", """
url: http://vps:8556
token: secret
""")
    cfg = load_config(tmp_path)
    assert cfg.remotes["vps"].token == "secret"


def test_load_remotes_conflict_aborts(tmp_path: Path) -> None:
    from aegis.config import ConfigError
    _write(tmp_path / ".aegis.yaml", """
remotes:
  vps:
    url: http://vps:8556
""")
    _write(tmp_path / ".aegis" / "remotes" / "vps.yaml", """
url: http://vps:9999
""")
    with pytest.raises(ConfigError, match="remotes"):
        load_config(tmp_path)


def test_load_remote_plane(tmp_path: Path) -> None:
    _write(tmp_path / ".aegis.yaml", """
remote_plane:
  bind: 100.64.0.1:8556
  accept_tokens:
    - token-a
""")
    cfg = load_config(tmp_path)
    assert cfg.remote_plane is not None
    assert cfg.remote_plane.bind == "100.64.0.1:8556"
    assert cfg.remote_plane.accept_tokens == ["token-a"]


def test_load_remote_plane_absent_is_none(tmp_path: Path) -> None:
    _write(tmp_path / ".aegis.yaml", "")
    cfg = load_config(tmp_path)
    assert cfg.remote_plane is None
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `uv run pytest tests/test_remote_config.py -v`
Expected: 5 PASS, 5 FAIL (AegisConfig has no `remotes` / `remote_plane` field).

- [ ] **Step 7: Extend the YAML loader**

Edit `src/aegis/config/yaml_loader.py`:

After the existing imports, add:

```python
from aegis.remote.config import RemotePlaneSpec, RemoteSpec
```

Change `_SECTIONS` to include `remotes`:

```python
_SECTIONS = ("agents", "queues", "schedules", "remotes")
```

Extend `AegisConfig`:

```python
@dataclass
class AegisConfig:
    """Loaded YAML config (in-memory)."""
    default_agent: str | None = None
    agents: dict[str, Agent] = field(default_factory=dict)
    queues: dict[str, QueueSpec] = field(default_factory=dict)
    schedules: dict[str, dict[str, Any]] = field(default_factory=dict)
    workflows: list[str] = field(default_factory=list)
    plugin_dirs: list[Path] = field(default_factory=list)
    scheduler: dict[str, Any] = field(default_factory=dict)
    remotes: dict[str, RemoteSpec] = field(default_factory=dict)
    remote_plane: RemotePlaneSpec | None = None
    root: Path | None = None
```

In `load_config`, after the existing inline-section setup, add `remotes` parsing:

```python
    inline: dict[str, dict[str, Any]] = {
        "agents":   dict(raw.get("agents") or {}),
        "queues":   dict(raw.get("queues") or {}),
        "schedules": dict(raw.get("schedules") or {}),
        "remotes": dict(raw.get("remotes") or {}),
    }
```

And after `queues = ...`, add:

```python
    remotes = {k: RemoteSpec(**v) for k, v in merged["remotes"].items()}

    rp_raw = raw.get("remote_plane")
    remote_plane = RemotePlaneSpec(**rp_raw) if rp_raw else None
```

Return them in the `AegisConfig(...)` call:

```python
    return AegisConfig(
        default_agent=raw.get("default_agent"),
        agents=agents,
        queues=queues,
        schedules=merged["schedules"],
        workflows=list(raw.get("workflows") or []),
        plugin_dirs=plugin_dirs,
        scheduler=dict(raw.get("scheduler") or {}),
        remotes=remotes,
        remote_plane=remote_plane,
        root=root,
    )
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `uv run pytest tests/test_remote_config.py -v`
Expected: 10 PASS.

- [ ] **Step 9: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/remote/ src/aegis/config/yaml_loader.py tests/test_remote_config.py
git commit -m "feat(remote): config dataclasses + yaml loader for remotes/remote_plane"
```

---

## Task 2: Plane Endpoint — Happy Path (No Auth)

**Files:**
- Create: `src/aegis/remote/plane.py`
- Test: `tests/test_remote_plane.py`
- Modify: `pyproject.toml` (add starlette, uvicorn as direct deps)

- [ ] **Step 1: Add starlette + uvicorn as explicit deps**

Edit `pyproject.toml` to add them inside the existing `dependencies = [...]` block (keep alphabetical or follow existing convention):

```toml
    "starlette>=0.46",
    "uvicorn>=0.32",
```

Run: `uv sync`
Expected: succeeds (both are already transitive via fastmcp; this just pins them).

- [ ] **Step 2: Write a failing test for the endpoint happy path**

Create `tests/test_remote_plane.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from aegis.remote.config import RemotePlaneSpec
from aegis.remote.plane import build_plane


@dataclass
class _FakeQueueManager:
    """Records enqueue calls for assertion."""
    calls: list[dict[str, Any]]

    def enqueue(self, queue: str, payload: str, *,
                enqueued_by: str, callback: bool) -> tuple[str, int]:
        self.calls.append({
            "queue": queue,
            "payload": payload,
            "enqueued_by": enqueued_by,
            "callback": callback,
        })
        return ("task-01J", 0)


@pytest.mark.anyio
async def test_enqueue_happy_path() -> None:
    qm = _FakeQueueManager(calls=[])
    spec = RemotePlaneSpec(bind="127.0.0.1:0")
    app = build_plane(qm, spec)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue",
            json={"queue": "implementation",
                  "payload": "do the thing",
                  "from": "zion"})

    assert resp.status_code == 200
    assert resp.json() == {"task_id": "task-01J", "queued_position": 0}
    assert qm.calls == [{
        "queue": "implementation",
        "payload": "do the thing",
        "enqueued_by": "remote:zion",
        "callback": False,
    }]


@pytest.mark.anyio
async def test_enqueue_unknown_queue_returns_404() -> None:
    class _Raising:
        def enqueue(self, *a, **k):
            raise KeyError("nope")
    spec = RemotePlaneSpec(bind="127.0.0.1:0")
    app = build_plane(_Raising(), spec)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue",
            json={"queue": "nope", "payload": "x", "from": "zion"})
    assert resp.status_code == 404
    assert "unknown queue" in resp.json()["error"]


@pytest.mark.anyio
async def test_enqueue_bad_body_returns_400() -> None:
    qm = _FakeQueueManager(calls=[])
    spec = RemotePlaneSpec(bind="127.0.0.1:0")
    app = build_plane(qm, spec)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue", json={"queue": "x"})  # missing fields
    assert resp.status_code == 400
    assert "missing" in resp.json()["error"].lower()
```

Ensure `tests/conftest.py` exposes the `anyio_backend` fixture if not already. If `pytest-anyio` is not yet a dep, add `anyio` (likely already transitive via httpx/starlette). Check by running:

```bash
uv run python -c "import anyio; print(anyio.__version__)"
```

If missing, add `"anyio>=4"` to deps and `uv sync`.

If `pytest-anyio`'s `@pytest.mark.anyio` is unavailable, use the existing aegis convention (browse `tests/` for examples) — likely `asyncio.run(...)` inside the test body or an existing event-loop fixture. Adapt accordingly.

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_remote_plane.py -v`
Expected: ImportError on `aegis.remote.plane`.

- [ ] **Step 4: Implement the plane**

Create `src/aegis/remote/plane.py`:

```python
"""Receiver-side HTTP plane for the aegis remote API.

Exposes a single endpoint, ``POST /remote/v1/enqueue``, that other
aegis instances call to enqueue work into this aegis's QueueManager.

The app is a Starlette app; it is mounted onto a uvicorn server by
``aegis serve`` when ``.aegis.yaml`` has a ``remote_plane`` block.
"""
from __future__ import annotations

import json
from typing import Any, Protocol

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from aegis.remote.config import RemotePlaneSpec


class _QueueManagerLike(Protocol):
    def enqueue(self, queue: str, payload: str, *,
                enqueued_by: str, callback: bool) -> tuple[str, int]: ...


def build_plane(queue_manager: _QueueManagerLike,
                spec: RemotePlaneSpec) -> Starlette:
    """Build the Starlette app bound to ``queue_manager`` + ``spec``."""

    async def enqueue(request: Request) -> JSONResponse:
        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        missing = [k for k in ("queue", "payload", "from") if k not in body]
        if missing:
            return JSONResponse(
                {"error": f"missing required fields: {missing}"},
                status_code=400)
        try:
            tid, pos = queue_manager.enqueue(
                body["queue"], body["payload"],
                enqueued_by=f"remote:{body['from']}",
                callback=False)
        except KeyError as e:
            return JSONResponse(
                {"error": f"unknown queue {e.args[0]!r}"},
                status_code=404)
        return JSONResponse({"task_id": tid, "queued_position": pos})

    return Starlette(routes=[
        Route("/remote/v1/enqueue", enqueue, methods=["POST"]),
    ])
```

Update `src/aegis/remote/__init__.py`:

```python
from aegis.remote.config import RemotePlaneSpec, RemoteSpec
from aegis.remote.plane import build_plane

__all__ = ["RemoteSpec", "RemotePlaneSpec", "build_plane"]
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_remote_plane.py -v`
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/remote/plane.py src/aegis/remote/__init__.py tests/test_remote_plane.py pyproject.toml uv.lock
git commit -m "feat(remote): plane endpoint — POST /remote/v1/enqueue happy path"
```

---

## Task 3: Plane Endpoint — Auth Gates

**Files:**
- Modify: `src/aegis/remote/plane.py`
- Modify: `tests/test_remote_plane.py`

- [ ] **Step 1: Write failing tests for token + IP gates**

Append to `tests/test_remote_plane.py`:

```python
@pytest.mark.anyio
async def test_token_required_when_configured() -> None:
    qm = _FakeQueueManager(calls=[])
    spec = RemotePlaneSpec(bind="127.0.0.1:0", accept_tokens=["good"])
    app = build_plane(qm, spec)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue",
            json={"queue": "q", "payload": "p", "from": "zion"})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_token_accepted_when_matching() -> None:
    qm = _FakeQueueManager(calls=[])
    spec = RemotePlaneSpec(bind="127.0.0.1:0", accept_tokens=["good"])
    app = build_plane(qm, spec)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue",
            headers={"Authorization": "Bearer good"},
            json={"queue": "q", "payload": "p", "from": "zion"})
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_ip_allowlist_rejects_unlisted() -> None:
    qm = _FakeQueueManager(calls=[])
    spec = RemotePlaneSpec(bind="127.0.0.1:0", accept_from=["10.0.0.1"])
    app = build_plane(qm, spec)
    transport = ASGITransport(
        app=app, client=("192.168.1.1", 12345))
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue",
            json={"queue": "q", "payload": "p", "from": "zion"})
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_ip_allowlist_accepts_listed() -> None:
    qm = _FakeQueueManager(calls=[])
    spec = RemotePlaneSpec(bind="127.0.0.1:0", accept_from=["10.0.0.1"])
    app = build_plane(qm, spec)
    transport = ASGITransport(app=app, client=("10.0.0.1", 12345))
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue",
            json={"queue": "q", "payload": "p", "from": "zion"})
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_both_gates_must_pass() -> None:
    qm = _FakeQueueManager(calls=[])
    spec = RemotePlaneSpec(
        bind="127.0.0.1:0",
        accept_tokens=["good"],
        accept_from=["10.0.0.1"])
    app = build_plane(qm, spec)

    # Right IP, wrong token: 401
    transport = ASGITransport(app=app, client=("10.0.0.1", 12345))
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue",
            json={"queue": "q", "payload": "p", "from": "zion"})
        assert resp.status_code == 401

    # Wrong IP, right token: 403
    transport = ASGITransport(app=app, client=("192.168.1.1", 12345))
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue",
            headers={"Authorization": "Bearer good"},
            json={"queue": "q", "payload": "p", "from": "zion"})
        assert resp.status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_remote_plane.py -v`
Expected: 5 new FAILs — all currently return 200 because no gates exist.

- [ ] **Step 3: Implement the auth gates**

Edit `src/aegis/remote/plane.py`. Replace the `enqueue` body to add gates before the parse:

```python
def build_plane(queue_manager: _QueueManagerLike,
                spec: RemotePlaneSpec) -> Starlette:
    """Build the Starlette app bound to ``queue_manager`` + ``spec``."""

    async def enqueue(request: Request) -> JSONResponse:
        # IP gate
        if spec.accept_from:
            peer = request.client.host if request.client else None
            if peer not in spec.accept_from:
                return JSONResponse(
                    {"error": f"source ip {peer!r} not in accept_from"},
                    status_code=403)
        # Token gate
        if spec.accept_tokens:
            auth = request.headers.get("authorization", "")
            token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
            if token not in spec.accept_tokens:
                return JSONResponse(
                    {"error": "missing or invalid bearer token"},
                    status_code=401)

        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        missing = [k for k in ("queue", "payload", "from") if k not in body]
        if missing:
            return JSONResponse(
                {"error": f"missing required fields: {missing}"},
                status_code=400)
        try:
            tid, pos = queue_manager.enqueue(
                body["queue"], body["payload"],
                enqueued_by=f"remote:{body['from']}",
                callback=False)
        except KeyError as e:
            return JSONResponse(
                {"error": f"unknown queue {e.args[0]!r}"},
                status_code=404)
        return JSONResponse({"task_id": tid, "queued_position": pos})

    return Starlette(routes=[
        Route("/remote/v1/enqueue", enqueue, methods=["POST"]),
    ])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_remote_plane.py -v`
Expected: 8 PASS (3 from Task 2 + 5 from this task).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/remote/plane.py tests/test_remote_plane.py
git commit -m "feat(remote): plane auth gates — bearer tokens + IP allowlist"
```

---

## Task 4: Client

**Files:**
- Create: `src/aegis/remote/client.py`
- Test: `tests/test_remote_client.py`

- [ ] **Step 1: Write the failing client test**

Create `tests/test_remote_client.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from aegis.remote.client import remote_enqueue
from aegis.remote.config import RemotePlaneSpec, RemoteSpec
from aegis.remote.plane import build_plane


@dataclass
class _FakeQM:
    raise_on: str | None = None
    def enqueue(self, queue, payload, *, enqueued_by, callback):
        if self.raise_on and queue == self.raise_on:
            raise KeyError(queue)
        return ("tid-01J", 0)


@pytest.mark.anyio
async def test_remote_enqueue_happy_path(monkeypatch) -> None:
    qm = _FakeQM()
    plane_spec = RemotePlaneSpec(bind="127.0.0.1:0")
    app = build_plane(qm, plane_spec)
    spec = RemoteSpec(url="http://test")

    transport = ASGITransport(app=app)
    async def _client_factory(_: RemoteSpec) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, base_url=spec.url)
    monkeypatch.setattr(
        "aegis.remote.client._build_client", _client_factory)

    result = await remote_enqueue(spec, "implementation", "do it", "zion")
    assert result == {
        "task_id": "tid-01J",
        "queued_position": 0,
        "target_url": "http://test",
    }


@pytest.mark.anyio
async def test_remote_enqueue_unknown_queue_returns_error(monkeypatch) -> None:
    qm = _FakeQM(raise_on="nope")
    plane_spec = RemotePlaneSpec(bind="127.0.0.1:0")
    app = build_plane(qm, plane_spec)
    spec = RemoteSpec(url="http://test")

    transport = ASGITransport(app=app)
    async def _client_factory(_: RemoteSpec) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, base_url=spec.url)
    monkeypatch.setattr(
        "aegis.remote.client._build_client", _client_factory)

    result = await remote_enqueue(spec, "nope", "x", "zion")
    assert "error" in result
    assert "unknown queue" in result["error"]


@pytest.mark.anyio
async def test_remote_enqueue_connection_refused(monkeypatch) -> None:
    spec = RemoteSpec(url="http://127.0.0.1:1")  # nothing listens here

    # Use a real httpx client so the connection refusal is real.
    async def _client_factory(s: RemoteSpec) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=s.url, timeout=httpx.Timeout(
            connect=1.0, read=1.0, write=1.0, pool=1.0))
    monkeypatch.setattr(
        "aegis.remote.client._build_client", _client_factory)

    result = await remote_enqueue(spec, "q", "p", "zion")
    assert "error" in result
    assert "unreachable" in result["error"] or "refused" in result["error"]


@pytest.mark.anyio
async def test_remote_enqueue_sends_bearer_token(monkeypatch) -> None:
    qm = _FakeQM()
    plane_spec = RemotePlaneSpec(bind="127.0.0.1:0", accept_tokens=["good"])
    app = build_plane(qm, plane_spec)
    spec = RemoteSpec(url="http://test", token="good")

    transport = ASGITransport(app=app)
    async def _client_factory(s: RemoteSpec) -> httpx.AsyncClient:
        headers = {}
        if s.token:
            headers["Authorization"] = f"Bearer {s.token}"
        return httpx.AsyncClient(
            transport=transport, base_url=s.url, headers=headers)
    monkeypatch.setattr(
        "aegis.remote.client._build_client", _client_factory)

    result = await remote_enqueue(spec, "q", "p", "zion")
    assert "task_id" in result
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_remote_client.py -v`
Expected: ImportError on `aegis.remote.client`.

- [ ] **Step 3: Implement the client**

Create `src/aegis/remote/client.py`:

```python
"""Caller-side httpx client for the remote plane."""
from __future__ import annotations

import httpx

from aegis.remote.config import RemoteSpec

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


async def _build_client(spec: RemoteSpec) -> httpx.AsyncClient:
    """Construct the httpx client for ``spec``. Separated so tests can
    monkeypatch this to inject an ASGI transport against an in-process
    plane app.
    """
    headers: dict[str, str] = {}
    if spec.token:
        headers["Authorization"] = f"Bearer {spec.token}"
    return httpx.AsyncClient(
        base_url=spec.url, headers=headers, timeout=_DEFAULT_TIMEOUT)


async def remote_enqueue(spec: RemoteSpec, queue: str, payload: str,
                         from_: str) -> dict:
    """POST one enqueue to the remote plane at ``spec.url``.

    Returns the parsed response dict on success, augmented with
    ``target_url`` for caller debugging. On any failure, returns
    ``{"error": "..."}`` with a normalized, human-readable message —
    never raises.
    """
    client = await _build_client(spec)
    try:
        try:
            resp = await client.post(
                "/remote/v1/enqueue",
                json={"queue": queue, "payload": payload, "from": from_})
        except httpx.ConnectError as e:
            return {"error": f"remote unreachable (connection refused): {e}"}
        except httpx.TimeoutException as e:
            return {"error": f"remote timed out: {e}"}
        except httpx.HTTPError as e:
            return {"error": f"remote http error: {e}"}

        if resp.status_code == 200:
            body = resp.json()
            return {**body, "target_url": spec.url}
        # Try to surface the server's error message
        try:
            err = resp.json().get("error", resp.text)
        except ValueError:
            err = resp.text
        return {"error": f"remote returned {resp.status_code}: {err}"}
    finally:
        await client.aclose()
```

Update `src/aegis/remote/__init__.py`:

```python
from aegis.remote.client import remote_enqueue
from aegis.remote.config import RemotePlaneSpec, RemoteSpec
from aegis.remote.plane import build_plane

__all__ = [
    "RemoteSpec", "RemotePlaneSpec",
    "remote_enqueue", "build_plane",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_remote_client.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/remote/client.py src/aegis/remote/__init__.py tests/test_remote_client.py
git commit -m "feat(remote): httpx client with normalized error surface"
```

---

## Task 5: Bridge + MCP Tool `target=`

**Files:**
- Modify: `src/aegis/mcp/bridge.py`
- Modify: `src/aegis/mcp/server.py`
- Modify: `src/aegis/core/manager.py`
- Modify: `src/aegis/tui/app.py`
- Test: `tests/test_remote_mcp_target.py`

- [ ] **Step 1: Write a failing test for the MCP tool's `target=` path**

Create `tests/test_remote_mcp_target.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from aegis.mcp.bridge import AppBridge, SessionInfo
from aegis.mcp.server import build_server
from aegis.remote.config import RemotePlaneSpec, RemoteSpec
from aegis.remote.plane import build_plane


@dataclass
class _FakeQM:
    enqueue_calls: list[Any] = field(default_factory=list)
    def enqueue(self, queue, payload, *, enqueued_by, callback):
        self.enqueue_calls.append((queue, payload, enqueued_by, callback))
        return ("local-tid", 0)


@dataclass
class _FakeBridge:
    queue_manager: Any
    remotes: dict[str, RemoteSpec]
    inbox_router: Any = None
    canvas_manager: Any = None
    terminal_manager: Any = None
    def list_sessions(self) -> list[SessionInfo]:
        return []
    def list_agents(self) -> list[str]:
        return []
    async def handoff(self, *a, **k) -> str:
        return ""
    async def spawn(self, *a, **k) -> str:
        return ""
    async def close(self, *a, **k) -> None:
        return None


@pytest.mark.anyio
async def test_aegis_enqueue_local_path_unchanged(monkeypatch) -> None:
    qm = _FakeQM()
    bridge = _FakeBridge(queue_manager=qm, remotes={})
    server = build_server(bridge)
    tool = server._tools["aegis_enqueue"]  # FastMCP internal handle
    result = await tool.run({
        "queue": "q", "payload": "p", "from_handle": "h"})
    assert result["task_id"] == "local-tid"
    assert qm.enqueue_calls == [("q", "p", "agent:h", True)]


@pytest.mark.anyio
async def test_aegis_enqueue_unknown_target_errors() -> None:
    qm = _FakeQM()
    bridge = _FakeBridge(queue_manager=qm, remotes={})
    server = build_server(bridge)
    tool = server._tools["aegis_enqueue"]
    result = await tool.run({
        "queue": "q", "payload": "p",
        "from_handle": "h", "target": "vps"})
    assert "error" in result
    assert "unknown target" in result["error"]
    assert qm.enqueue_calls == []   # never hit local


@pytest.mark.anyio
async def test_aegis_enqueue_with_target_routes_remote(monkeypatch) -> None:
    # Spin up an in-process plane to be the "remote"
    remote_qm = _FakeQM()
    plane_app = build_plane(remote_qm, RemotePlaneSpec(bind="127.0.0.1:0"))

    transport = ASGITransport(app=plane_app)
    async def _client_factory(s: RemoteSpec) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, base_url=s.url)
    monkeypatch.setattr(
        "aegis.remote.client._build_client", _client_factory)

    local_qm = _FakeQM()
    bridge = _FakeBridge(
        queue_manager=local_qm,
        remotes={"vps": RemoteSpec(url="http://stub")})
    server = build_server(bridge)
    tool = server._tools["aegis_enqueue"]
    result = await tool.run({
        "queue": "implementation", "payload": "build it",
        "from_handle": "h", "target": "vps"})

    assert result["task_id"] == "local-tid"  # remote_qm's fake tid
    assert local_qm.enqueue_calls == []
    assert len(remote_qm.enqueue_calls) == 1
    q, p, eb, cb = remote_qm.enqueue_calls[0]
    assert (q, p, eb, cb) == ("implementation", "build it", "remote:h", False)
```

Note: this test assumes FastMCP exposes registered tools via `server._tools` — check `aegis/mcp/server.py` for the actual idiom. If FastMCP's API doesn't allow this directly, invoke the tool via its real callable: `await aegis_enqueue(queue=..., payload=..., from_handle=...)` where `aegis_enqueue` is imported from the test-helper-extracted function. Adjust to whatever pattern the existing `tests/test_mcp_bridge.py` uses.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_remote_mcp_target.py -v`
Expected: failures — bridge lacks `remotes`, tool lacks `target` param.

- [ ] **Step 3: Add `remotes` to the bridge Protocol**

Edit `src/aegis/mcp/bridge.py`:

```python
@runtime_checkable
class AppBridge(Protocol):
    """..."""

    queue_manager: object
    inbox_router: object
    canvas_manager: object
    terminal_manager: object
    remotes: object        # dict[str, RemoteSpec]; empty dict when none configured

    def list_sessions(self) -> list[SessionInfo]: ...
    def list_agents(self) -> list[str]: ...
    async def handoff(self, from_handle: str, target_handle: str,
                      context: str) -> str: ...
    async def spawn(self, profile: str, *,
                    handle: str | None = None) -> str: ...
    async def close(self, handle: str) -> None: ...
```

- [ ] **Step 4: Populate `remotes` on `SessionManager`**

Edit `src/aegis/core/manager.py` constructor. Find where existing attributes like `queue_manager` are initialized and add:

```python
        self.remotes: dict = {}  # populated by cli.serve from loaded YAML
```

Add a setter mirroring the existing `attach_queue_manager` pattern:

```python
    def attach_remotes(self, remotes: dict) -> None:
        self.remotes = remotes
```

(If the existing pattern is constructor injection instead, follow that.)

- [ ] **Step 5: Populate `remotes` on `AegisApp`**

Edit `src/aegis/tui/app.py`. In `__init__` near the existing `self.queue_manager = ...` line, add:

```python
        self.remotes: dict = {}  # populated later from loaded YAML
```

If the TUI startup loads YAML (lines 190-191), wire `self.remotes = cfg.remotes` immediately after the YAML config object is loaded.

- [ ] **Step 6: Extend `aegis_enqueue` MCP tool**

Edit `src/aegis/mcp/server.py`. Replace the existing `aegis_enqueue` (around line 307) with:

```python
    @server.tool
    async def aegis_enqueue(queue: str, payload: str, from_handle: str,
                            callback: bool = True,
                            target: str | None = None) -> dict:
        """Enqueue a task on a named queue. Returns task_id + queued_position.

        If ``target`` is set, the enqueue is forwarded to a configured
        remote aegis (must be a key in ``remotes`` in .aegis.yaml). The
        remote runs the task on its own filesystem and queue. ``callback``
        is ignored for remote targets in v1 — the remote pings via
        Telegram on completion.

        If ``target=None`` (default), the task runs on this aegis's local
        QueueManager. ``callback=true`` (default) routes the worker's
        final result into your inbox; ``callback=false`` drops it.

        from_handle is your own aegis handle (read from your system prompt).
        Unknown queue/target returns ``{"error": "..."}``.
        """
        from aegis.queue import sender_agent
        if target is not None:
            remotes = getattr(bridge, "remotes", {}) or {}
            if target not in remotes:
                return {"error":
                        f"unknown target {target!r}; "
                        f"known: {sorted(remotes)}"}
            from aegis.remote.client import remote_enqueue
            result = await remote_enqueue(
                remotes[target], queue, payload, from_handle)
            if "error" not in result:
                result["callback_note"] = (
                    "wire callbacks not yet implemented; "
                    "remote will Telegram on completion")
                result["target"] = target
            return result

        try:
            tid, pos = bridge.queue_manager.enqueue(
                queue, payload,
                enqueued_by=sender_agent(from_handle),
                callback=callback)
        except KeyError as e:
            return {"error": f"enqueue rejected: unknown queue {e.args[0]!r}"}
        return {"task_id": tid, "queued_position": pos}
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_remote_mcp_target.py -v`
Expected: 3 PASS.

- [ ] **Step 8: Run the full hermetic suite to catch regressions**

Run: `uv run pytest -q -m "not live"`
Expected: 0 failures. If any existing test broke (e.g. a bridge fake that no longer satisfies the Protocol), add `remotes = {}` to its fixture.

- [ ] **Step 9: Commit**

```bash
git add src/aegis/mcp/bridge.py src/aegis/mcp/server.py \
        src/aegis/core/manager.py src/aegis/tui/app.py \
        tests/test_remote_mcp_target.py
git commit -m "feat(remote): aegis_enqueue grows target= and routes to remote plane"
```

---

## Task 6: Wire the Plane into `aegis serve`

**Files:**
- Modify: `src/aegis/cli.py`
- Test: `tests/test_remote_serve_wiring.py`

- [ ] **Step 1: Write a failing test for the wiring**

Create `tests/test_remote_serve_wiring.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from aegis.config.yaml_loader import AegisConfig
from aegis.remote.config import RemotePlaneSpec


@pytest.mark.anyio
async def test_serve_starts_remote_plane_when_configured(tmp_path,
                                                          monkeypatch) -> None:
    """When `remote_plane` is configured, `serve` boots an HTTP server.

    Stubs out everything except the remote-plane startup path. Asserts
    that ``run_plane_async`` (or its equivalent) is invoked with the
    configured spec.
    """
    from aegis.remote import plane as plane_mod

    started: list[tuple] = []

    def _fake_run(app, bind):
        started.append((app, bind))
        async def _noop() -> None:
            return None
        return asyncio.create_task(_noop())

    monkeypatch.setattr(plane_mod, "run_plane_async", _fake_run)

    # Drive the boot path. The simplest fixture: call the helper that
    # cli.serve uses to start the plane, in isolation.
    from aegis.cli import _maybe_start_remote_plane  # to be added

    spec = RemotePlaneSpec(bind="127.0.0.1:0")
    cfg = AegisConfig(remote_plane=spec)
    qm = object()  # opaque; not exercised in this test

    await _maybe_start_remote_plane(cfg, qm)

    assert len(started) == 1
    app, bind = started[0]
    assert bind == "127.0.0.1:0"


@pytest.mark.anyio
async def test_serve_skips_remote_plane_when_unconfigured(
        monkeypatch) -> None:
    from aegis.remote import plane as plane_mod

    started: list = []
    def _fake_run(*a, **k):
        started.append(a)
    monkeypatch.setattr(plane_mod, "run_plane_async", _fake_run)

    from aegis.cli import _maybe_start_remote_plane
    cfg = AegisConfig(remote_plane=None)
    await _maybe_start_remote_plane(cfg, queue_manager=object())
    assert started == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_remote_serve_wiring.py -v`
Expected: ImportError on `_maybe_start_remote_plane`.

- [ ] **Step 3: Add `run_plane_async` to `plane.py`**

Append to `src/aegis/remote/plane.py`:

```python
import asyncio

import uvicorn


def run_plane_async(app: Starlette, bind: str) -> asyncio.Task:
    """Run the plane on ``bind`` (``host:port``) as an asyncio task.

    Returns the task; caller is responsible for keeping a reference
    and (optionally) cancelling on shutdown.
    """
    host, _, port_s = bind.rpartition(":")
    config = uvicorn.Config(
        app, host=host, port=int(port_s),
        log_level="info", access_log=False)
    server = uvicorn.Server(config)
    return asyncio.create_task(server.serve())
```

Add `run_plane_async` to `__all__` in `src/aegis/remote/__init__.py`.

- [ ] **Step 4: Add the wiring helper to `cli.py`**

Edit `src/aegis/cli.py`. Add (e.g., just above the `serve` command body, or in a private helper section):

```python
async def _maybe_start_remote_plane(cfg, queue_manager) -> None:
    """Start the remote plane if `.aegis.yaml` configured it.

    No-op when ``cfg.remote_plane`` is None. Otherwise builds the
    Starlette app + an asyncio task running uvicorn.
    """
    if getattr(cfg, "remote_plane", None) is None:
        return
    from aegis.remote.plane import build_plane, run_plane_async
    app = build_plane(queue_manager, cfg.remote_plane)
    run_plane_async(app, cfg.remote_plane.bind)
```

- [ ] **Step 5: Call the helper from the `serve` flow**

Locate the `serve` command body (around `cli.py:225`). After the YAML config is loaded and the QueueManager is attached to the bridge / SessionManager, add an `await _maybe_start_remote_plane(cfg, queue_manager)` call. Also wire `session_manager.attach_remotes(cfg.remotes)` (or whatever method we added in Task 5) at the same point.

The exact placement depends on the current shape of `serve` — read the surrounding 30 lines first; insert after `attach_queue_manager` and before the main event loop blocks.

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/test_remote_serve_wiring.py -v`
Expected: 2 PASS.

- [ ] **Step 7: Run the full hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: 0 failures.

- [ ] **Step 8: Commit**

```bash
git add src/aegis/cli.py src/aegis/remote/plane.py \
        src/aegis/remote/__init__.py tests/test_remote_serve_wiring.py
git commit -m "feat(remote): wire remote plane into aegis serve startup"
```

---

## Task 7: Live Roundtrip Test (Opt-In)

**Files:**
- Create: `tests/test_remote_live.py`

- [ ] **Step 1: Add the opt-in live test**

Create `tests/test_remote_live.py`:

```python
"""Live cross-host enqueue test.

Skipped by default (no `live` marker collected). Run with:

    AEGIS_REMOTE_LIVE_URL=http://vps.tail-net.ts.net:8556 \\
    AEGIS_REMOTE_LIVE_QUEUE=implementation \\
    uv run pytest -m live tests/test_remote_live.py -v

Both env vars are required. The remote queue must exist on the VPS.
"""
from __future__ import annotations

import os

import pytest

from aegis.remote.client import remote_enqueue
from aegis.remote.config import RemoteSpec


@pytest.mark.live
@pytest.mark.anyio
async def test_remote_live_roundtrip() -> None:
    url = os.environ.get("AEGIS_REMOTE_LIVE_URL")
    queue = os.environ.get("AEGIS_REMOTE_LIVE_QUEUE")
    if not (url and queue):
        pytest.skip("AEGIS_REMOTE_LIVE_URL + AEGIS_REMOTE_LIVE_QUEUE required")
    token = os.environ.get("AEGIS_REMOTE_LIVE_TOKEN")

    spec = RemoteSpec(url=url, token=token)
    result = await remote_enqueue(
        spec, queue, "live-test payload — ignore", "live-test")

    assert "error" not in result, f"remote returned error: {result}"
    assert "task_id" in result
    assert result["target_url"] == url
```

- [ ] **Step 2: Confirm the `live` marker is registered**

Run: `grep "live" pyproject.toml`
Expected: matches `"live: …"` line in `[tool.pytest.ini_options].markers`. If not present, add it.

- [ ] **Step 3: Run the test in default mode (should skip / not collect)**

Run: `uv run pytest tests/test_remote_live.py -v`
Expected: 1 SKIP or "deselected" message — the `@live` marker excludes it from the default suite.

- [ ] **Step 4: Commit**

```bash
git add tests/test_remote_live.py
git commit -m "test(remote): opt-in live roundtrip against real VPS"
```

- [ ] **Step 5: (Out-of-band, Alex-driven) actually run the live test**

This is for Alex to do when both ends are deployed. Sample command:

```bash
AEGIS_REMOTE_LIVE_URL=http://<vps-tailnet-host>:8556 \
AEGIS_REMOTE_LIVE_QUEUE=<some-queue> \
uv run pytest -m live tests/test_remote_live.py -v
```

Not part of CI; not in the plan's verification scope.

---

## Final verification

- [ ] **Run the full hermetic suite once more**

Run: `uv run pytest -q -m "not live"`
Expected: 0 failures.

- [ ] **Verify nothing leaked into the live suite by accident**

Run: `uv run pytest -q --collect-only -m live tests/test_remote_*.py`
Expected: only `test_remote_live.py::test_remote_live_roundtrip` listed.

- [ ] **Push (when Alex confirms)**

```bash
git push origin main
```

(Per CLAUDE.md: pre-authorized for the workspace shell, but `repos/aegis` is independent — confirm before pushing if uncertain.)
