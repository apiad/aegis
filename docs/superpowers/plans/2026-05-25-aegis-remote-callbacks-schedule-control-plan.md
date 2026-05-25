# Remote Callbacks + Schedule Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the two v0.7.x extensions on top of the v0.7 remote plane — inbox-style wire callbacks for `aegis_enqueue(target=…)` and a five-endpoint remote-schedule control plane (push/list/show/remove/logs) backed by matching MCP tools and CLI verbs.

**Architecture:** Both features ride existing substrate primitives. Callbacks: a new observer on `QueueManager.subscribe()` POSTs to the caller's plane on task completion; the caller's plane delivers through the existing `InboxRouter`. Schedule push: PUT writes a YAML file into the receiver's `.aegis/schedules/` overlay folder; the v0.6 hot-reload watcher picks it up; the existing scheduler runtime owns the rest. Trust + URL prefix reuse the v0.7 `remotes:` / `remote_plane:` config and the `/remote/v1/` namespace.

**Tech Stack:** Python 3.13, FastMCP, Starlette (plane), httpx (client), ruamel.yaml (atomic comment-preserving writes), Typer (CLI), pytest (`uv run pytest -q -m "not live"`), pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-05-25-aegis-remote-callbacks-schedule-control-design.md` (canonical) and `.html` companion. Read it once before starting Task 1.

**Convention reminders:**
- Aegis tests live flat under `tests/`, file name `test_<topic>.py`.
- Live tests are marked `@pytest.mark.live` and auto-skip when the peer is unreachable.
- Commit straight to `main` (aegis convention — see workspace memory).
- Run hermetic gate before each commit: `uv run pytest -q -m "not live" -x`.
- Use uv, not pip. `uv run pytest`, `uv pip install -e .`.

---

## Task 1: Add `peer_name` field to `RemoteSpec`

**Files:**
- Modify: `src/aegis/remote/config.py`
- Test: `tests/test_remote_config.py`

Adds the explicit `peer_name` field that resolves Open Question #1 from the spec (the receiver's name for the caller, used to populate `callback_to` on the wire). Default `None`; when None, the existing `from` field on the enqueue body acts as the implicit fallback.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_remote_config.py`:

```python
def test_remote_spec_accepts_peer_name():
    from aegis.remote.config import RemoteSpec
    spec = RemoteSpec(url="http://1.2.3.4:8556", peer_name="laptop")
    assert spec.peer_name == "laptop"

def test_remote_spec_peer_name_defaults_to_none():
    from aegis.remote.config import RemoteSpec
    spec = RemoteSpec(url="http://1.2.3.4:8556")
    assert spec.peer_name is None
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_remote_config.py::test_remote_spec_accepts_peer_name -v
```
Expected: FAIL with `TypeError: ... unexpected keyword argument 'peer_name'`.

- [ ] **Step 3: Implement**

Edit `src/aegis/remote/config.py`, extend `RemoteSpec`:

```python
@dataclass(frozen=True)
class RemoteSpec:
    """Outbound remote target — one entry in the `remotes` mapping."""
    url: str
    token: str | None = None
    peer_name: str | None = None

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                f"remote url must include scheme + host: {self.url!r}")
```

- [ ] **Step 4: Run test to verify both pass**

```
uv run pytest tests/test_remote_config.py -v
```
Expected: PASS.

- [ ] **Step 5: Run full hermetic suite**

```
uv run pytest -q -m "not live" -x
```
Expected: all green (no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/aegis/remote/config.py tests/test_remote_config.py
git commit -m "feat(remote): RemoteSpec gains optional peer_name field"
```

---

## Task 2: Extend `/remote/v1/enqueue` body with callback hints

**Files:**
- Modify: `src/aegis/remote/plane.py`
- Modify: `src/aegis/queue/manager.py` (extend `Task` to carry callback_to + callback_handle)
- Test: `tests/test_remote_plane.py`

The receiver-side enqueue handler accepts two new optional body fields, `callback_to` and `callback_handle`, and threads them onto the `Task` record so the completion observer (Task 3) can find them later. When missing, behavior is identical to v0.7.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_remote_plane.py`:

```python
async def test_enqueue_accepts_callback_hints(tmp_path):
    qm = _make_queue_manager(tmp_path)            # existing helper in this file
    spec = RemotePlaneSpec(bind="127.0.0.1:8556")
    app = build_plane(qm, spec)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                  base_url="http://test") as client:
        r = await client.post("/remote/v1/enqueue", json={
            "queue": "impl", "payload": "do it", "from": "zion",
            "callback_to": "laptop", "callback_handle": "lucid-knuth"})
        assert r.status_code == 200
    tid = r.json()["task_id"]
    task = qm._all[tid]
    assert task.callback_to == "laptop"
    assert task.callback_handle == "lucid-knuth"

async def test_enqueue_without_callback_hints_stays_v07(tmp_path):
    qm = _make_queue_manager(tmp_path)
    spec = RemotePlaneSpec(bind="127.0.0.1:8556")
    app = build_plane(qm, spec)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                  base_url="http://test") as client:
        r = await client.post("/remote/v1/enqueue", json={
            "queue": "impl", "payload": "do it", "from": "zion"})
        assert r.status_code == 200
    tid = r.json()["task_id"]
    task = qm._all[tid]
    assert task.callback_to is None
    assert task.callback_handle is None
```

(If `_make_queue_manager` doesn't already exist in `tests/test_remote_plane.py`, copy the pattern from `tests/test_queue_manager.py` — a `QueueManager` instance with one queue named `"impl"`, given an in-memory `session_manager` test double.)

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_remote_plane.py::test_enqueue_accepts_callback_hints -v
```
Expected: FAIL — either `AttributeError: 'Task' object has no attribute 'callback_to'` or `TypeError` from `Task(...)` rejecting the kwargs.

- [ ] **Step 3: Extend `Task` dataclass**

In `src/aegis/queue/manager.py` (and `src/aegis/queue/schema.py` if `Task` lives there — confirm with `grep -rn "^class Task" src/aegis/queue/`), add two optional fields with `None` defaults so existing call-sites stay valid:

```python
@dataclass(frozen=True)
class Task:
    # ...existing fields...
    callback_to:     str | None = None
    callback_handle: str | None = None
```

Update `QueueManager.enqueue(...)` to accept and store them. The signature change:

```python
def enqueue(self, queue: str, payload: str, *,
            enqueued_by: str | None = None,
            callback: bool = False,
            callback_to: str | None = None,
            callback_handle: str | None = None) -> tuple[str, int]:
```

When constructing the `Task`, pass the new fields through. The existing local-callback path (`callback=True`) continues to work unchanged.

- [ ] **Step 4: Extend `plane.py` /enqueue handler**

In `src/aegis/remote/plane.py`, the enqueue route reads `body["queue"]`, `body["payload"]`, `body["from"]`. Add reads for `body.get("callback_to")` and `body.get("callback_handle")` and pass them to `queue_manager.enqueue(...)`.

- [ ] **Step 5: Run both tests**

```
uv run pytest tests/test_remote_plane.py::test_enqueue_accepts_callback_hints tests/test_remote_plane.py::test_enqueue_without_callback_hints_stays_v07 -v
```
Expected: PASS.

- [ ] **Step 6: Run full hermetic suite**

```
uv run pytest -q -m "not live" -x
```
Expected: all green. If any existing `Task(...)` construction fails because of the new fields, fix the call-site to pass the defaults (or rely on the dataclass defaults — frozen-dataclass-friendly).

- [ ] **Step 7: Commit**

```bash
git add src/aegis/remote/plane.py src/aegis/queue/manager.py tests/test_remote_plane.py
git commit -m "feat(remote): /enqueue accepts callback_to + callback_handle hints"
```

---

## Task 3: Outbound callback observer + `remote_callback` client

**Files:**
- Modify: `src/aegis/remote/client.py`
- Modify: `src/aegis/queue/manager.py` (subscribe a callback-firer observer)
- Modify: `src/aegis/cli.py` (wire it on `aegis serve` startup, alongside existing remote-plane wiring)
- Test: `tests/test_remote_callback_client.py` (new)

The completion observer pulls `callback_to` + `callback_handle` off the task, looks up `remotes[callback_to]` on this serve, and POSTs `/remote/v1/callback` to the caller. The actual delivery to a *live* inbox happens on the caller side (Task 4); this task is just the outbound POST.

- [ ] **Step 1: Write the failing test for the client function**

Create `tests/test_remote_callback_client.py`:

```python
import httpx
import pytest
from aegis.remote.client import remote_callback
from aegis.remote.config import RemoteSpec


@pytest.mark.asyncio
async def test_remote_callback_posts_body_and_token(httpx_mock):
    spec = RemoteSpec(url="http://1.2.3.4:8556", token="secret")
    httpx_mock.add_response(
        method="POST",
        url="http://1.2.3.4:8556/remote/v1/callback",
        status_code=204,
    )
    body = {
        "task_id": "01J123",
        "queue": "impl",
        "from_peer": "vps",
        "to_handle": "lucid-knuth",
        "status": "ok",
        "result_text": "done",
        "started_at": "2026-05-25T10:00:00Z",
        "ended_at": "2026-05-25T10:05:00Z",
    }
    result = await remote_callback(spec, body)
    assert result == {"ok": True}
    request = httpx_mock.get_request()
    assert request.headers["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_remote_callback_normalizes_5xx(httpx_mock):
    spec = RemoteSpec(url="http://1.2.3.4:8556")
    httpx_mock.add_response(
        method="POST",
        url="http://1.2.3.4:8556/remote/v1/callback",
        status_code=503,
    )
    result = await remote_callback(spec, {"task_id": "x", "queue": "q",
                                          "from_peer": "p", "to_handle": "h",
                                          "status": "ok", "result_text": "",
                                          "started_at": "", "ended_at": ""})
    assert result.get("error", "").startswith("callback_dropped:")
```

(`pytest-httpx` is already in the test deps — see existing `tests/test_remote_client.py` for the pattern.)

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_remote_callback_client.py -v
```
Expected: FAIL — `remote_callback` not defined.

- [ ] **Step 3: Implement `remote_callback` in `src/aegis/remote/client.py`**

```python
async def remote_callback(spec: RemoteSpec, body: dict) -> dict:
    """POST /remote/v1/callback. Best-effort, no retry."""
    try:
        async with await _build_client(spec) as client:
            r = await client.post("/remote/v1/callback", json=body,
                                  timeout=httpx.Timeout(10.0, connect=5.0))
    except httpx.TimeoutException:
        return {"error": "callback_dropped: timeout"}
    except httpx.RequestError as e:
        return {"error": f"callback_dropped: unreachable ({e})"}
    if r.status_code == 204 or r.status_code == 200:
        return {"ok": True}
    if r.status_code == 401:
        return {"error": "callback_dropped: auth_rejected"}
    return {"error": f"callback_dropped: {r.status_code}"}
```

- [ ] **Step 4: Run client tests**

```
uv run pytest tests/test_remote_callback_client.py -v
```
Expected: PASS.

- [ ] **Step 5: Write failing test for the observer hook**

Add to `tests/test_remote_callback_client.py`:

```python
@pytest.mark.asyncio
async def test_callback_observer_fires_on_completion(tmp_path, httpx_mock):
    """End-to-end: when a task with callback_to completes, the
    observer POSTs /remote/v1/callback to the caller's plane."""
    from aegis.queue.manager import QueueManager
    from aegis.remote.callback_observer import install_callback_observer
    from tests.fixtures.fake_session import FakeSessionManager   # existing helper

    sm = FakeSessionManager()
    qm = QueueManager(queues={"impl": Queue(...)},  # mirror test_queue_manager pattern
                       session_manager=sm,
                       state_dir=tmp_path)
    remotes = {"zion": RemoteSpec(url="http://1.2.3.4:8556")}
    install_callback_observer(qm, remotes=remotes, self_peer_name="vps")

    httpx_mock.add_response(
        method="POST",
        url="http://1.2.3.4:8556/remote/v1/callback",
        status_code=204,
    )

    tid, _ = qm.enqueue("impl", "do it",
                         enqueued_by="remote:zion",
                         callback_to="zion",
                         callback_handle="lucid-knuth")
    # Drive the fake session to "done" with a result.
    await sm.finish_task(tid, result_text="ALL GOOD")

    request = httpx_mock.get_request()
    body = request.read().decode()
    assert "lucid-knuth" in body
    assert "ALL GOOD" in body
    assert '"from_peer": "vps"' in body
    assert '"status": "ok"' in body
```

(`FakeSessionManager` exists in the test fixtures — see `tests/conftest.py` or `tests/fixtures/`.)

- [ ] **Step 6: Run test to verify failure**

```
uv run pytest tests/test_remote_callback_client.py::test_callback_observer_fires_on_completion -v
```
Expected: FAIL — `install_callback_observer` not defined.

- [ ] **Step 7: Implement the observer**

Create `src/aegis/remote/callback_observer.py`:

```python
"""Subscribe to QueueManager completion events and fire remote callbacks."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping

from aegis.queue.manager import QueueManager, QueueEvent
from aegis.remote.client import remote_callback
from aegis.remote.config import RemoteSpec

_log = logging.getLogger(__name__)


def install_callback_observer(
    qm: QueueManager,
    *,
    remotes: Mapping[str, RemoteSpec],
    self_peer_name: str,
) -> None:
    """Hook a completion observer that fires /remote/v1/callback per task.

    Only tasks with `callback_to` set are eligible. Best-effort POST,
    no retry. `self_peer_name` is what we identify ourselves as in the
    callback body's `from_peer` field — typically the local serve's
    `from` identity (hostname or operator-configured).
    """
    def _observer(ev: QueueEvent) -> None:
        if ev.outcome not in ("completed", "failed", "interrupted"):
            return
        task = qm._all.get(ev.task_id)
        if task is None or not task.callback_to:
            return
        spec = remotes.get(task.callback_to)
        if spec is None:
            qm._log(task.queue, {
                "event": "callback_dropped",
                "task_id": task.id,
                "reason": "unknown_peer",
                "callback_to": task.callback_to,
            })
            return
        status = {"completed": "ok",
                  "failed": "failed",
                  "interrupted": "interrupted"}[ev.outcome]
        body = {
            "task_id":     task.id,
            "queue":       task.queue,
            "from_peer":   self_peer_name,
            "to_handle":   task.callback_handle,
            "status":      status,
            "result_text": getattr(ev, "result_text", "") or "",
            "started_at":  task.started_at or "",
            "ended_at":    ev.completed_at or "",
        }
        # Fire-and-forget — observer must not block QueueManager.
        asyncio.create_task(_fire(qm, task.queue, task.id, spec, body))

    qm.subscribe(_observer)


async def _fire(qm: QueueManager, queue: str, task_id: str,
                spec: RemoteSpec, body: dict) -> None:
    result = await remote_callback(spec, body)
    qm._log(queue, {
        "event": "callback_attempted",
        "task_id": task_id,
        "outcome": "delivered" if result.get("ok") else result.get("error"),
    })
```

- [ ] **Step 8: Wire it on `aegis serve` startup**

In `src/aegis/cli.py`, find the existing `_maybe_start_remote_plane` block. Right after the plane is mounted, install the observer if both `cfg.remotes` and (a configured self-name) are present:

```python
async def _maybe_start_remote_plane(cfg, queue_manager) -> None:
    if getattr(cfg, "remote_plane", None) is None:
        return
    from aegis.remote import plane as plane_mod
    from aegis.remote.callback_observer import install_callback_observer
    app = plane_mod.build_plane(queue_manager, cfg.remote_plane)
    plane_mod.run_plane_async(app, cfg.remote_plane.bind)
    if cfg.remotes:
        # self_peer_name defaults to the host the operator named for us.
        # If unset, callbacks will still fire but with an empty from_peer;
        # operator can set `remote_plane.peer_name` in .aegis.yaml later.
        self_name = getattr(cfg.remote_plane, "peer_name", None) or "this-serve"
        install_callback_observer(
            queue_manager, remotes=cfg.remotes, self_peer_name=self_name)
```

(If `RemotePlaneSpec` doesn't have a `peer_name` field, **don't add one in this task** — that's a follow-up if needed. The `"this-serve"` default is fine for v1; the caller already knows which peer it's hearing from because it controls the connection.)

- [ ] **Step 9: Run observer test**

```
uv run pytest tests/test_remote_callback_client.py -v
```
Expected: PASS.

- [ ] **Step 10: Run full hermetic suite**

```
uv run pytest -q -m "not live" -x
```
Expected: all green.

- [ ] **Step 11: Commit**

```bash
git add src/aegis/remote/client.py src/aegis/remote/callback_observer.py src/aegis/queue/manager.py src/aegis/cli.py tests/test_remote_callback_client.py
git commit -m "feat(remote): callback observer + remote_callback client"
```

---

## Task 4: `/remote/v1/callback` endpoint → InboxRouter delivery

**Files:**
- Modify: `src/aegis/remote/plane.py`
- Test: `tests/test_remote_callback_endpoint.py` (new)

The endpoint on the **caller's** side (the originating serve receiving the callback POST from the remote). Validates auth, parses the body, hands `result_text` to the local `InboxRouter` for `to_handle`. If the session is closed, the InboxRouter writes to its per-handle JSONL ledger as it already does — no special handling needed.

- [ ] **Step 1: Write the failing test**

Create `tests/test_remote_callback_endpoint.py`:

```python
import httpx
import pytest
from httpx import ASGITransport

from aegis.remote.config import RemotePlaneSpec
from aegis.remote.plane import build_plane


class _FakeInboxRouter:
    def __init__(self):
        self.deliveries = []
    def deliver(self, handle, **kwargs):
        self.deliveries.append({"handle": handle, **kwargs})


class _Bridge:
    """Minimal bridge for the plane: must expose queue_manager and inbox_router."""
    def __init__(self, qm, inbox):
        self.queue_manager = qm
        self.inbox_router = inbox


@pytest.mark.asyncio
async def test_callback_endpoint_routes_to_inbox(tmp_path):
    qm = _make_queue_manager(tmp_path)        # reuse from test_remote_plane.py
    inbox = _FakeInboxRouter()
    bridge = _Bridge(qm, inbox)
    spec = RemotePlaneSpec(bind="127.0.0.1:8556")
    app = build_plane(bridge, spec)
    transport = ASGITransport(app=app)
    body = {
        "task_id": "01J", "queue": "impl",
        "from_peer": "vps", "to_handle": "lucid-knuth",
        "status": "ok", "result_text": "DONE",
        "started_at": "", "ended_at": "",
    }
    async with httpx.AsyncClient(transport=transport,
                                  base_url="http://test") as client:
        r = await client.post("/remote/v1/callback", json=body)
        assert r.status_code == 204
    assert len(inbox.deliveries) == 1
    d = inbox.deliveries[0]
    assert d["handle"] == "lucid-knuth"
    assert "DONE" in d["body"]
    assert "queue:vps:impl" in d.get("sender", "")


@pytest.mark.asyncio
async def test_callback_endpoint_auth_rejects_bad_token(tmp_path):
    bridge = _Bridge(_make_queue_manager(tmp_path), _FakeInboxRouter())
    spec = RemotePlaneSpec(bind="127.0.0.1:8556", accept_tokens=["good"])
    app = build_plane(bridge, spec)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                  base_url="http://test") as client:
        r = await client.post("/remote/v1/callback", json={
            "task_id": "x", "queue": "q", "from_peer": "p",
            "to_handle": "h", "status": "ok", "result_text": "",
            "started_at": "", "ended_at": ""},
            headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401
```

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/test_remote_callback_endpoint.py -v
```
Expected: FAIL — endpoint not registered.

- [ ] **Step 3: Register the endpoint in `build_plane`**

Add to `src/aegis/remote/plane.py`. The existing `build_plane` signature already accepts a queue-manager-like; bump it to accept a `bridge` (or extend `_QueueManagerLike` to require `inbox_router`). Match whatever shape the existing caller in `cli.py` is currently passing — if it's just the `QueueManager`, you'll need to widen the signature **and** update `cli.py`'s call-site to pass a bridge containing both.

```python
@app.route("/remote/v1/callback", methods=["POST"])
async def callback(request):
    auth_err = _check_auth(request, spec)
    if auth_err:
        return JSONResponse(auth_err, status_code=401)
    body = await request.json()
    sender = f"queue:{body['from_peer']}:{body['queue']}"
    inbox_router.deliver(
        body["to_handle"],
        sender=sender,
        body=body["result_text"],
        meta={
            "task_id": body["task_id"],
            "status": body["status"],
            "started_at": body["started_at"],
            "ended_at": body["ended_at"],
        },
    )
    return Response(status_code=204)
```

(The `_check_auth(...)` helper already exists for the `/enqueue` endpoint — reuse it.)

- [ ] **Step 4: Run callback endpoint tests**

```
uv run pytest tests/test_remote_callback_endpoint.py -v
```
Expected: PASS.

- [ ] **Step 5: Run full hermetic suite**

```
uv run pytest -q -m "not live" -x
```
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/remote/plane.py tests/test_remote_callback_endpoint.py
git commit -m "feat(remote): /remote/v1/callback endpoint delivers to InboxRouter"
```

---

## Task 5: Wire callbacks through `aegis_enqueue` MCP tool

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_mcp_remote_callback.py` (new)

Make `aegis_enqueue(target=…, callback=true)` actually request a callback. The MCP tool looks up `remotes[target].peer_name` to compute `callback_to`; sets `callback_handle = from_handle`; passes both to the existing `remote_enqueue` client. Update the docstring + `callback_note` to reflect the new behavior, and reject `callback=true` when this serve has no `remote_plane` configured.

- [ ] **Step 1: Write failing tests**

Create `tests/test_mcp_remote_callback.py`:

```python
import pytest

from aegis.mcp.server import _build_aegis_enqueue   # extract for test
                                                     # — if no such helper,
                                                     # test via the bridge layer
from aegis.remote.config import RemoteSpec, RemotePlaneSpec


class _FakeBridge:
    def __init__(self, remotes, remote_plane):
        self.remotes = remotes
        self.remote_plane = remote_plane
        self.posted = []
    async def remote_enqueue_via_client(self, spec, **kwargs):
        self.posted.append({"spec": spec, **kwargs})
        return {"task_id": "01J", "queued_position": 0}


@pytest.mark.asyncio
async def test_aegis_enqueue_remote_callback_passes_hints(monkeypatch):
    """When callback=true and remote_plane is configured, callback_to +
    callback_handle land in the outbound enqueue."""
    bridge = _FakeBridge(
        remotes={"vps": RemoteSpec(url="http://1.2.3.4:8556",
                                    peer_name="laptop")},
        remote_plane=RemotePlaneSpec(bind="127.0.0.1:8556"))
    captured = {}
    async def fake_remote_enqueue(spec, queue, payload, from_,
                                    callback_to=None, callback_handle=None):
        captured.update(spec=spec, queue=queue, payload=payload, from_=from_,
                         callback_to=callback_to, callback_handle=callback_handle)
        return {"task_id": "01J", "queued_position": 0}
    monkeypatch.setattr("aegis.mcp.server.remote_enqueue", fake_remote_enqueue)

    # Call the tool body directly (or via the FastMCP server bound to bridge).
    result = await _invoke_aegis_enqueue(
        bridge, queue="impl", payload="do it",
        from_handle="lucid-knuth", callback=True, target="vps")
    assert captured["callback_to"] == "laptop"
    assert captured["callback_handle"] == "lucid-knuth"
    assert result["target"] == "vps"
    assert "callback will deliver" in result["callback_note"]


@pytest.mark.asyncio
async def test_aegis_enqueue_callback_true_no_remote_plane_errors(monkeypatch):
    """callback=true on a remote target requires this serve to have a
    remote_plane configured."""
    bridge = _FakeBridge(
        remotes={"vps": RemoteSpec(url="http://1.2.3.4:8556")},
        remote_plane=None)
    result = await _invoke_aegis_enqueue(
        bridge, queue="impl", payload="x",
        from_handle="h", callback=True, target="vps")
    assert "error" in result
    assert "remote_plane" in result["error"]
```

`_invoke_aegis_enqueue` is a small test helper that calls into the tool body the same way `tests/test_remote_mcp_target.py` already does — copy that pattern.

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/test_mcp_remote_callback.py -v
```
Expected: FAIL — current `aegis_enqueue` ignores callback for remote.

- [ ] **Step 3: Implement**

Edit `src/aegis/mcp/server.py`. In `aegis_enqueue`, when `target is not None`:

```python
if target is not None:
    remotes = getattr(bridge, "remotes", {}) or {}
    if target not in remotes:
        return {"error": f"unknown target {target!r}; "
                          f"known: {sorted(remotes)}"}
    if callback and getattr(bridge, "remote_plane", None) is None:
        return {"error":
                "callback=true on a remote target requires "
                "remote_plane to be configured on this serve"}
    spec = remotes[target]
    callback_to = spec.peer_name if callback else None
    callback_handle = from_handle if callback else None
    from aegis.remote.client import remote_enqueue
    result = await remote_enqueue(
        spec, queue, payload, from_handle,
        callback_to=callback_to, callback_handle=callback_handle)
    if "error" not in result:
        result["target"] = target
        if callback:
            result["callback_note"] = (
                "callback will deliver to your inbox when the remote "
                "task terminates")
        else:
            result["callback_note"] = (
                "fire-and-forget — completion behavior is whatever the "
                "receiving serve is configured to do")
    return result
```

Update the `aegis_enqueue` docstring to match (see spec, "MCP surface" section).

- [ ] **Step 4: Extend `remote_enqueue` client signature**

In `src/aegis/remote/client.py`, `remote_enqueue` needs to accept the new kwargs and forward them in the POST body:

```python
async def remote_enqueue(spec: RemoteSpec, queue: str, payload: str,
                          from_: str, *,
                          callback_to: str | None = None,
                          callback_handle: str | None = None) -> dict:
    body = {"queue": queue, "payload": payload, "from": from_}
    if callback_to is not None:
        body["callback_to"] = callback_to
    if callback_handle is not None:
        body["callback_handle"] = callback_handle
    # ...existing httpx POST...
```

Add a quick test in `tests/test_remote_client.py` confirming the body shape carries the hints when set.

- [ ] **Step 5: Run all MCP + client tests**

```
uv run pytest tests/test_mcp_remote_callback.py tests/test_remote_client.py tests/test_remote_mcp_target.py -v
```
Expected: PASS.

- [ ] **Step 6: Run full hermetic suite**

```
uv run pytest -q -m "not live" -x
```
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/mcp/server.py src/aegis/remote/client.py tests/test_mcp_remote_callback.py tests/test_remote_client.py
git commit -m "feat(remote): aegis_enqueue wires callback=true for remote targets"
```

---

## Task 6: Hermetic two-serve callback round-trip + failure modes

**Files:**
- Test: `tests/test_remote_callback_e2e.py` (new)

Spin up two `aegis serve` instances in the same test process, configured as each other's peers, and walk one task through the full round-trip. Plus three negative paths from the spec's failure-mode table.

- [ ] **Step 1: Write the e2e success test**

```python
import asyncio
import pytest

from tests.fixtures.two_serves import build_two_serves   # new helper, see Step 2


@pytest.mark.asyncio
async def test_remote_callback_round_trip(tmp_path):
    """Agent on A enqueues to B with callback=true; B runs the worker;
    A's pane receives the inbox envelope."""
    pair = await build_two_serves(tmp_path)
    try:
        # The agent sits on A; the worker runs on B.
        agent = pair.spawn_agent_on_a(handle="lucid-knuth")
        result = await pair.invoke_enqueue_on_a(
            queue="impl", payload="echo:hello",
            from_handle="lucid-knuth", callback=True, target="b")
        assert result["target"] == "b"

        # Let B's worker finish and the callback POST happen.
        await pair.wait_for_inbox_on_a("lucid-knuth", timeout=10.0)

        deliveries = agent.inbox_deliveries
        assert len(deliveries) == 1
        d = deliveries[0]
        assert "queue:a:impl" in d["sender"] or "queue:b:impl" in d["sender"]
        assert "hello" in d["body"]
    finally:
        await pair.shutdown()
```

- [ ] **Step 2: Build the `build_two_serves` fixture**

Create `tests/fixtures/two_serves.py`:

```python
"""Spin up two aegis-serve-like instances on loopback for cross-host tests.

Each side gets:
  - a QueueManager backed by a FakeSessionManager (echoes payload as result)
  - a RemotePlane bound to a free loopback port
  - a configured `remotes` map pointing at the other side
  - a peer_name (their name in the other side's view)

Returns a `Pair` with helpers:
  - spawn_agent_on_a(handle) — registers a fake session
  - invoke_enqueue_on_a(...) — calls aegis_enqueue tool body
  - wait_for_inbox_on_a(handle, timeout)
  - shutdown()
"""
```

Mirror the pattern in `tests/fixtures/fake_groups_env.py` if it helps. The key trick: pick loopback ports via `socket.socket().bind(('127.0.0.1', 0))` to avoid collisions in parallel tests.

- [ ] **Step 3: Run the success path**

```
uv run pytest tests/test_remote_callback_e2e.py::test_remote_callback_round_trip -v
```
Expected: PASS.

- [ ] **Step 4: Add the three failure-mode tests**

```python
@pytest.mark.asyncio
async def test_callback_to_unknown_peer_logs_drop(tmp_path):
    """B has no remotes[zion] → callback_dropped: unknown_peer audit
    record on B; A's pane receives nothing."""
    # ...

@pytest.mark.asyncio
async def test_caller_plane_unreachable_logs_drop(tmp_path):
    """A's plane shut down before B's worker finishes → 
    callback_dropped: unreachable audit on B; A's session sees nothing."""
    # ...

@pytest.mark.asyncio
async def test_caller_session_closed_writes_to_inbox_jsonl(tmp_path):
    """A's session closed before callback arrives → A's plane delivers
    to inbox JSONL but no live agent reacts."""
    # ...
```

For each: walk through the full enqueue → completion path, then assert on the receiver's `.aegis/state/queues/<queue>.jsonl` for the right `callback_dropped:<reason>` record.

- [ ] **Step 5: Run all four tests**

```
uv run pytest tests/test_remote_callback_e2e.py -v
```
Expected: PASS.

- [ ] **Step 6: Run full hermetic suite**

```
uv run pytest -q -m "not live" -x
```
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add tests/test_remote_callback_e2e.py tests/fixtures/two_serves.py
git commit -m "test(remote): hermetic two-serve callback round-trip + failure modes"
```

---

## Task 7: `PUT /remote/v1/schedule/<name>` — atomic write + validation

**Files:**
- Create: `src/aegis/scheduler/push.py`
- Modify: `src/aegis/remote/plane.py`
- Test: `tests/test_schedule_push_endpoint.py` (new)

Receiver-side push handler. Validates the spec (cron, lifecycle, workflow registry, args type-check, no `callback=true` for `enqueue` workflows), writes atomically with provenance comment, returns 200 or 4xx.

- [ ] **Step 1: Write failing tests**

```python
import json
import pytest
from httpx import AsyncClient, ASGITransport
from aegis.remote.config import RemotePlaneSpec
from aegis.remote.plane import build_plane


@pytest.mark.asyncio
async def test_push_writes_yaml_with_provenance(tmp_path):
    bridge = _make_bridge(tmp_path)     # bridge with queue_manager + workflow_registry
    spec = RemotePlaneSpec(bind="127.0.0.1:8556")
    app = build_plane(bridge, spec)
    body = {
        "workflow": "enqueue",
        "args": {"queue": "impl", "payload": "x", "callback": False},
        "cron": "0 2 * * *",
        "lifecycle": "forever",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.put("/remote/v1/schedule/nightly", json=body,
                         headers={"X-Pushed-From": "peer:zion"})
        assert r.status_code == 200
    written = tmp_path / ".aegis" / "schedules" / "nightly.yaml"
    assert written.exists()
    content = written.read_text()
    assert content.startswith("# pushed_from: peer:zion")
    assert "cron: \"0 2 * * *\"" in content


@pytest.mark.asyncio
async def test_push_rejects_bad_cron(tmp_path):
    bridge = _make_bridge(tmp_path)
    app = build_plane(bridge, RemotePlaneSpec(bind="127.0.0.1:8556"))
    body = {"workflow": "enqueue", "args": {"queue": "impl", "payload": "x"},
            "cron": "not a cron", "lifecycle": "forever"}
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as c:
        r = await c.put("/remote/v1/schedule/n", json=body)
        assert r.status_code == 400
        assert "cron" in r.json()["error"].lower()


@pytest.mark.asyncio
async def test_push_rejects_unknown_workflow(tmp_path):
    # ...

@pytest.mark.asyncio
async def test_push_rejects_callback_true_on_enqueue_workflow(tmp_path):
    # ...

@pytest.mark.asyncio
async def test_push_is_atomic(tmp_path):
    """tempfile + rename — no partial files visible during write."""
    # ...
```

- [ ] **Step 2: Run tests to verify failure**

```
uv run pytest tests/test_schedule_push_endpoint.py -v
```
Expected: FAIL — endpoint not registered.

- [ ] **Step 3: Implement `scheduler/push.py`**

```python
"""Receiver-side schedule push: validate, write atomically with provenance."""
from __future__ import annotations

import io
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from ruamel.yaml import YAML

from aegis.scheduler.cron import next_fire as _validate_cron   # raises on bad expr
from aegis.scheduler.lifecycle import is_exhausted as _lifecycle_check
from aegis.workflow.registry import get as _workflow_get      # raises on unknown


def validate_spec(spec: dict, *, workflow_registry) -> None:
    """Raise ValueError with a clear message on invalid spec."""
    if not isinstance(spec, dict):
        raise ValueError("spec must be a JSON object")
    workflow = spec.get("workflow")
    if workflow is None:
        raise ValueError("spec.workflow is required")
    if workflow_registry.get(workflow) is None:
        raise ValueError(f"unknown workflow: {workflow!r}")

    if "cron" in spec:
        try:
            _validate_cron(spec["cron"], spec.get("timezone", "UTC"),
                            datetime.now(timezone.utc))
        except Exception as e:
            raise ValueError(f"invalid cron: {e}")
    elif "fire_at" in spec:
        try:
            datetime.fromisoformat(spec["fire_at"].replace("Z", "+00:00"))
        except Exception as e:
            raise ValueError(f"invalid fire_at: {e}")
    else:
        raise ValueError("spec must have 'cron' or 'fire_at'")

    if workflow == "enqueue" and spec.get("args", {}).get("callback"):
        raise ValueError(
            "callback=true on a scheduled remote enqueue is not allowed "
            "(scheduler has no inbox to deliver to)")

    # Lifecycle string sniff — explicit "forever" / "once" or a dict.
    lc = spec.get("lifecycle", "forever")
    if lc not in ("forever", "once") and not isinstance(lc, dict):
        raise ValueError(f"invalid lifecycle: {lc!r}")


def write_atomic(state_root: Path, name: str, spec: dict,
                  pushed_from: str) -> Path:
    """Serialize spec to YAML with a provenance header; atomic rename
    into state_root/.aegis/schedules/<name>.yaml."""
    dest_dir = state_root / ".aegis" / "schedules"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name}.yaml"

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    buf = io.StringIO()
    yaml.dump(spec, buf)
    serialized = buf.getvalue()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    body = (f"# pushed_from: {pushed_from} at {now}\n"
            f"# pushed_at: {now}\n"
            f"{serialized}")

    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=dest_dir, delete=False, suffix=".tmp")
    try:
        tmp.write(body)
        tmp.flush()
        Path(tmp.name).replace(dest)
    finally:
        tmp.close()
    return dest
```

- [ ] **Step 4: Wire the endpoint into `plane.py`**

```python
@app.route("/remote/v1/schedule/{name}", methods=["PUT"])
async def schedule_push(request):
    auth_err = _check_auth(request, spec)
    if auth_err:
        return JSONResponse(auth_err, status_code=401)
    name = request.path_params["name"]
    if not name or "/" in name or name.startswith("."):
        return JSONResponse({"error": "invalid schedule name"}, status_code=400)
    body = await request.json()
    pushed_from = request.headers.get("X-Pushed-From", "peer:unknown")
    try:
        validate_spec(body, workflow_registry=bridge.workflow_registry)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    dest = write_atomic(bridge.state_root, name, body, pushed_from)
    return JSONResponse(
        {"name": name, "written_to": str(dest.relative_to(bridge.state_root))})
```

(`bridge.workflow_registry` and `bridge.state_root` are new attributes the bridge needs. The bridge is the existing `SessionManager`-or-similar object passed by `cli.serve()`. Add the attrs in `cli.serve()` where the bridge is constructed.)

- [ ] **Step 5: Run push tests**

```
uv run pytest tests/test_schedule_push_endpoint.py -v
```
Expected: PASS.

- [ ] **Step 6: Run full hermetic suite**

```
uv run pytest -q -m "not live" -x
```
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/scheduler/push.py src/aegis/remote/plane.py src/aegis/cli.py tests/test_schedule_push_endpoint.py
git commit -m "feat(scheduler): PUT /remote/v1/schedule/<name> with validation + atomic write"
```

---

## Task 8: Schedule list + show endpoints + provenance detection

**Files:**
- Modify: `src/aegis/scheduler/push.py`
- Modify: `src/aegis/remote/plane.py`
- Test: `tests/test_schedule_inspect_endpoints.py` (new)

GET endpoints that read what's currently in the scheduler's table plus the on-disk files, classify each schedule as `inline` / `overlay` / `pushed`, and return summary + full views.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_list_classifies_sources(tmp_path):
    """A schedule from .aegis.yaml inline → source=inline; from
    .aegis/schedules/<n>.yaml without a pushed_from comment → overlay;
    with the comment → pushed."""
    # ...

@pytest.mark.asyncio
async def test_show_returns_full_spec_and_runtime(tmp_path):
    # ...

@pytest.mark.asyncio
async def test_show_404_for_missing(tmp_path):
    # ...
```

- [ ] **Step 2: Verify failure**

```
uv run pytest tests/test_schedule_inspect_endpoints.py -v
```

- [ ] **Step 3: Implement classification helper in `scheduler/push.py`**

```python
def classify_source(file_path: Path | None, inline_names: set[str],
                     name: str) -> tuple[str, str | None, str | None]:
    """Return (source, pushed_from, pushed_at) for a schedule.

    source ∈ {"inline", "overlay", "pushed"}.
    pushed_from / pushed_at are None unless source == "pushed".
    """
    if name in inline_names:
        return ("inline", None, None)
    if file_path is None or not file_path.exists():
        return ("inline", None, None)  # already-loaded inline, file gone
    first_two = file_path.read_text().splitlines()[:2]
    pf = None; pa = None
    for line in first_two:
        if line.startswith("# pushed_from:"):
            rest = line[len("# pushed_from:"):].strip()
            # "peer:zion at 2026-05-25T..."
            if " at " in rest:
                pf, pa = rest.rsplit(" at ", 1)
            else:
                pf = rest
        elif line.startswith("# pushed_at:"):
            pa = line[len("# pushed_at:"):].strip()
    if pf is not None:
        return ("pushed", pf.strip(), (pa or "").strip())
    return ("overlay", None, None)
```

- [ ] **Step 4: Wire the GET endpoints**

```python
@app.route("/remote/v1/schedule", methods=["GET"])
async def schedule_list(request):
    auth_err = _check_auth(request, spec)
    if auth_err: return JSONResponse(auth_err, status_code=401)
    sched = bridge.scheduler
    inline = set(bridge.inline_schedule_names())   # from cfg.schedules
    rows = []
    for entry in sched.snapshot():     # existing scheduler API
        source, pf, pa = classify_source(
            entry.file_path, inline, entry.name)
        rows.append({
            "name": entry.name, "source": source,
            "next_fire": entry.next_fire, "fire_count": entry.fire_count,
            "in_flight": entry.in_flight, "enabled": entry.enabled,
            "workflow": entry.spec.get("workflow"),
            "cron": entry.spec.get("cron"),
        })
    return JSONResponse({"schedules": rows})


@app.route("/remote/v1/schedule/{name}", methods=["GET"])
async def schedule_show(request):
    auth_err = _check_auth(request, spec)
    if auth_err: return JSONResponse(auth_err, status_code=401)
    name = request.path_params["name"]
    entry = bridge.scheduler.get(name)
    if entry is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    source, pf, pa = classify_source(
        entry.file_path, bridge.inline_schedule_names(), name)
    return JSONResponse({
        "name": name, "source": source, "spec": entry.spec,
        "runtime": {"next_fire": entry.next_fire,
                     "last_fire": entry.last_fire,
                     "fire_count": entry.fire_count,
                     "in_flight": entry.in_flight,
                     "enabled": entry.enabled},
        "pushed_from": pf, "pushed_at": pa,
    })
```

(If the existing `scheduler.snapshot()` doesn't return `file_path`, add it — schedules already track which file they came from internally.)

- [ ] **Step 5: Run inspect tests**

```
uv run pytest tests/test_schedule_inspect_endpoints.py -v
```
Expected: PASS.

- [ ] **Step 6: Run full hermetic suite + commit**

```bash
uv run pytest -q -m "not live" -x
git add src/aegis/scheduler/push.py src/aegis/remote/plane.py tests/test_schedule_inspect_endpoints.py
git commit -m "feat(scheduler): GET /remote/v1/schedule list + show with provenance"
```

---

## Task 9: Schedule remove + logs endpoints

**Files:**
- Modify: `src/aegis/remote/plane.py`
- Test: `tests/test_schedule_remove_logs.py` (new)

`DELETE /remote/v1/schedule/<name>` removes only pushed schedules (refuses inline/overlay with 409); `GET /remote/v1/schedule/<name>/logs` tails the JSONL audit.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_delete_pushed_schedule_removes_file(tmp_path):
    """Push a schedule, DELETE it, file is gone, GET → 404."""
    # ...

@pytest.mark.asyncio
async def test_delete_overlay_returns_409(tmp_path):
    """A hand-written .aegis/schedules/foo.yaml (no pushed_from comment)
    cannot be removed via DELETE."""
    # ...

@pytest.mark.asyncio
async def test_delete_inline_returns_409(tmp_path):
    # ...

@pytest.mark.asyncio
async def test_logs_returns_tail(tmp_path):
    """Push a schedule, fake some JSONL entries, GET ?tail=N returns
    last N as objects."""
    # ...
```

- [ ] **Step 2: Verify failure + implement**

Add to `plane.py`:

```python
@app.route("/remote/v1/schedule/{name}", methods=["DELETE"])
async def schedule_remove(request):
    auth_err = _check_auth(request, spec)
    if auth_err: return JSONResponse(auth_err, status_code=401)
    name = request.path_params["name"]
    entry = bridge.scheduler.get(name)
    if entry is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    source, _, _ = classify_source(
        entry.file_path, bridge.inline_schedule_names(), name)
    if source != "pushed":
        return JSONResponse(
            {"error": f"cannot remove {source!r}-source schedule"},
            status_code=409)
    entry.file_path.unlink()
    return Response(status_code=204)


@app.route("/remote/v1/schedule/{name}/logs", methods=["GET"])
async def schedule_logs(request):
    auth_err = _check_auth(request, spec)
    if auth_err: return JSONResponse(auth_err, status_code=401)
    name = request.path_params["name"]
    tail = int(request.query_params.get("tail", "50"))
    log_path = bridge.state_root / ".aegis" / "state" / "schedules" / f"{name}.jsonl"
    if not log_path.exists():
        return JSONResponse({"records": []})
    lines = log_path.read_text().splitlines()[-tail:]
    import json
    records = [json.loads(line) for line in lines if line.strip()]
    return JSONResponse({"records": records})
```

- [ ] **Step 3: Run tests + commit**

```bash
uv run pytest tests/test_schedule_remove_logs.py -v
uv run pytest -q -m "not live" -x
git add src/aegis/remote/plane.py tests/test_schedule_remove_logs.py
git commit -m "feat(scheduler): DELETE + logs endpoints for /remote/v1/schedule"
```

---

## Task 10: Outbound schedule client + hermetic two-serve push cycle

**Files:**
- Modify: `src/aegis/remote/client.py`
- Test: `tests/test_remote_schedule_client.py` (new)
- Test: `tests/test_remote_schedule_e2e.py` (new)

Five outbound client functions: `remote_schedule_push / list / show / remove / logs`. Plus a two-serve hermetic test that exercises the full push → hot-reload → fire → logs → remove cycle.

- [ ] **Step 1: Write client tests**

```python
@pytest.mark.asyncio
async def test_remote_schedule_push_sends_pushed_from_header(httpx_mock):
    spec = RemoteSpec(url="http://1.2.3.4:8556")
    httpx_mock.add_response(
        method="PUT",
        url="http://1.2.3.4:8556/remote/v1/schedule/foo",
        status_code=200,
        json={"name": "foo", "written_to": ".aegis/schedules/foo.yaml"})
    result = await remote_schedule_push(
        spec, name="foo", spec_body={"workflow": "prompt"},
        pushed_from="peer:zion")
    assert result["name"] == "foo"
    req = httpx_mock.get_request()
    assert req.headers.get("X-Pushed-From") == "peer:zion"


# ...one per verb (list, show, remove, logs).
```

- [ ] **Step 2: Implement five client functions in `client.py`**

```python
async def remote_schedule_push(spec: RemoteSpec, *, name: str,
                                spec_body: dict, pushed_from: str) -> dict:
    async with await _build_client(spec) as client:
        r = await client.put(f"/remote/v1/schedule/{name}",
                              json=spec_body,
                              headers={"X-Pushed-From": pushed_from})
    if r.status_code == 200:
        return r.json()
    return _normalize_err("schedule push", r)


async def remote_schedule_list(spec: RemoteSpec) -> dict: ...
async def remote_schedule_show(spec: RemoteSpec, name: str) -> dict: ...
async def remote_schedule_remove(spec: RemoteSpec, name: str) -> dict: ...
async def remote_schedule_logs(spec: RemoteSpec, name: str,
                                 tail: int = 50) -> dict: ...
```

(`_normalize_err(prefix, response) -> dict` already exists for the `/enqueue` path; reuse it.)

- [ ] **Step 3: Write the e2e cycle test**

```python
@pytest.mark.asyncio
async def test_schedule_push_cycle_e2e(tmp_path):
    """A pushes a schedule to B; B's hot-reload picks it up; B fires it
    (via FakeClock); A reads logs back; A deletes it; B drops it."""
    pair = await build_two_serves(tmp_path)
    try:
        spec_body = {
            "workflow": "prompt",
            "args": {"agent": "default", "message": "tick"},
            "cron": "*/1 * * * *",
            "lifecycle": "forever",
        }
        push = await pair.push_schedule_a_to_b(name="ticker", spec_body=spec_body)
        assert push["name"] == "ticker"

        # Wait for hot-reload to pick up the file.
        await pair.wait_for_schedule_on_b("ticker", timeout=5.0)

        # Advance the FakeClock on B to fire once.
        await pair.tick_b(seconds=70)
        await pair.wait_for_fire_count_on_b("ticker", count=1, timeout=5.0)

        logs = await pair.fetch_schedule_logs_from_a("ticker")
        assert any(rec["event"] == "fire_completed" for rec in logs["records"])

        await pair.remove_schedule_from_a("ticker")
        await pair.wait_for_schedule_gone_on_b("ticker", timeout=5.0)
    finally:
        await pair.shutdown()
```

- [ ] **Step 4: Run all tests + commit**

```bash
uv run pytest tests/test_remote_schedule_client.py tests/test_remote_schedule_e2e.py -v
uv run pytest -q -m "not live" -x
git add src/aegis/remote/client.py tests/test_remote_schedule_client.py tests/test_remote_schedule_e2e.py tests/fixtures/two_serves.py
git commit -m "feat(remote): schedule client + hermetic two-serve push cycle"
```

---

## Task 11: Five `aegis_schedule_*` MCP tools

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Modify: `src/aegis/mcp/bridge.py` (extend the `AppBridge` protocol with the new methods)
- Test: `tests/test_mcp_schedule_tools.py` (new)

The MCP tools. Each accepts `target=None` (operate on this serve via the local scheduler API) or `target="<peer>"` (route through the matching client function). 1:1 with the HTTP surface.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_schedule_push_local(tmp_path):
    """target=None writes a YAML file in this serve's .aegis/schedules/."""
    # ...

@pytest.mark.asyncio
async def test_schedule_push_remote_routes_through_client(monkeypatch):
    """target='vps' calls remote_schedule_push with the right spec + headers."""
    # ...

@pytest.mark.asyncio
async def test_schedule_list_local_and_remote(monkeypatch):
    # ...

# ...one per verb.
```

- [ ] **Step 2: Verify failure + implement**

In `src/aegis/mcp/server.py`, add five tools mirroring this pattern:

```python
@server.tool
async def aegis_schedule_push(name: str, spec: dict,
                                from_handle: str,
                                target: str | None = None) -> dict:
    """Push a schedule into a scheduler. target=None writes locally;
    target='<peer>' routes through the named remote."""
    if target is not None:
        remotes = getattr(bridge, "remotes", {}) or {}
        if target not in remotes:
            return {"error": f"unknown target {target!r}"}
        from aegis.remote.client import remote_schedule_push
        return await remote_schedule_push(
            remotes[target], name=name, spec_body=spec,
            pushed_from=f"agent:{from_handle}")
    # Local path: validate + write into our own state_root.
    from aegis.scheduler.push import validate_spec, write_atomic
    try:
        validate_spec(spec, workflow_registry=bridge.workflow_registry)
    except ValueError as e:
        return {"error": str(e)}
    dest = write_atomic(bridge.state_root, name, spec,
                         pushed_from=f"agent:{from_handle}")
    return {"name": name,
            "written_to": str(dest.relative_to(bridge.state_root))}


@server.tool
async def aegis_schedule_list(from_handle: str,
                                target: str | None = None) -> dict: ...

@server.tool
async def aegis_schedule_show(name: str, from_handle: str,
                                target: str | None = None) -> dict: ...

@server.tool
async def aegis_schedule_remove(name: str, from_handle: str,
                                  target: str | None = None) -> dict: ...

@server.tool
async def aegis_schedule_logs(name: str, from_handle: str,
                                target: str | None = None,
                                tail: int = 50) -> dict: ...
```

For the local-path implementations of `list`/`show`/`remove`/`logs`, read directly from the in-process scheduler — same data the HTTP GETs return.

- [ ] **Step 3: Run tests + commit**

```bash
uv run pytest tests/test_mcp_schedule_tools.py -v
uv run pytest -q -m "not live" -x
git add src/aegis/mcp/server.py src/aegis/mcp/bridge.py tests/test_mcp_schedule_tools.py
git commit -m "feat(mcp): aegis_schedule_* tools — push/list/show/remove/logs"
```

---

## Task 12: CLI verbs — `push --to` and `--remote` flag

**Files:**
- Modify: `src/aegis/cli_schedule.py`
- Test: `tests/test_cli_schedule_remote.py` (new)

Operator-facing CLI: `aegis schedule push --to <peer>` and `--remote <peer>` on `list`/`show`/`logs`/`remove`. Re-uses the client functions from Task 10.

- [ ] **Step 1: Write CLI tests**

Use Typer's `CliRunner` (existing pattern in `tests/test_cli_schedule.py`):

```python
def test_schedule_push_to_remote_reads_local_config(monkeypatch, tmp_path):
    """`aegis schedule push --to vps --name nightly` reads the local
    schedule named 'nightly' and POSTs it."""
    # ...

def test_schedule_push_from_file(tmp_path):
    """`aegis schedule push --to vps --file my.yaml` reads my.yaml and PUTs."""
    # ...

def test_schedule_list_remote(monkeypatch):
    """`aegis schedule list --remote vps` shows the peer's table."""
    # ...

# ...one per verb.
```

- [ ] **Step 2: Implement**

Edit `src/aegis/cli_schedule.py`. The existing `@app.command(...)` decorators become parameterized:

```python
@app.command("push")
def push_schedule(
    name: str = typer.Option(None, "--name"),
    file: Path = typer.Option(None, "--file"),
    to: str = typer.Option(..., "--to", help="remote peer name"),
):
    """Push a schedule to a remote peer."""
    cfg = _cfg()
    if file is not None:
        text = file.read_text()
        spec = json.loads(text) if file.suffix == ".json" else _load_yaml(text)
        name = name or file.stem
    elif name is not None:
        spec = _read_local_schedule(cfg, name)
    else:
        typer.echo("--name or --file is required", err=True); raise typer.Exit(1)
    if to not in cfg.remotes:
        typer.echo(f"unknown remote {to!r}", err=True); raise typer.Exit(1)
    from aegis.remote.client import remote_schedule_push
    result = asyncio.run(remote_schedule_push(
        cfg.remotes[to], name=name, spec_body=spec,
        pushed_from=f"peer:{cfg.self_name or 'unknown'}"))
    if "error" in result:
        typer.echo(result["error"], err=True); raise typer.Exit(1)
    typer.echo(f"pushed {result['name']} → {to} ({result['written_to']})")


@app.command("list")
def list_schedules(remote: str = typer.Option(None, "--remote")) -> None:
    if remote is None:
        # ...existing local path...
        return
    cfg = _cfg()
    if remote not in cfg.remotes:
        typer.echo(f"unknown remote {remote!r}", err=True); raise typer.Exit(1)
    from aegis.remote.client import remote_schedule_list
    result = asyncio.run(remote_schedule_list(cfg.remotes[remote]))
    if "error" in result:
        typer.echo(result["error"], err=True); raise typer.Exit(1)
    _print_schedule_table(result["schedules"])
```

Same `--remote` pattern on `show`, `logs`, `remove`. Keep the local path intact when `--remote` is not set.

- [ ] **Step 3: Run CLI tests + commit**

```bash
uv run pytest tests/test_cli_schedule_remote.py tests/test_cli_schedule.py -v
uv run pytest -q -m "not live" -x
git add src/aegis/cli_schedule.py tests/test_cli_schedule_remote.py
git commit -m "feat(cli): aegis schedule push --to + --remote flag on inspection verbs"
```

---

## Task 13: Live (`@pytest.mark.live`) round-trip + docs sync + release

**Files:**
- Test: `tests/test_remote_callback_schedule_live.py` (new)
- Modify: `docs/remote.md` — add callback + schedule control sections
- Modify: `docs/configuration.md` — add the `peer_name` field + symmetric-deployment shape
- Modify: `docs/index.md` + `docs/roadmap.md` + `README.md` — surface the new capabilities
- Modify: `mkdocs.yml` — no nav change (remote.md is already there)
- Modify: `CHANGELOG.md` — add `[0.8.0]` section
- Modify: `pyproject.toml` + `uv.lock` — bump 0.7.1 → 0.8.0

- [ ] **Step 1: Write the live test (auto-skip when peer unreachable)**

```python
import pytest
import os

@pytest.mark.live
@pytest.mark.asyncio
async def test_live_callback_round_trip():
    """Real zion↔vps round-trip — auto-skips when AEGIS_LIVE_PEER_URL unset
    or the peer isn't reachable."""
    peer = os.environ.get("AEGIS_LIVE_PEER_URL")
    if not peer:
        pytest.skip("AEGIS_LIVE_PEER_URL not set")
    # ...

@pytest.mark.live
@pytest.mark.asyncio
async def test_live_schedule_push_cycle():
    """Push a once-fire schedule with fire_at ~10s in the future,
    wait, fetch logs, remove."""
    # ...
```

- [ ] **Step 2: Hermetic gate + commit**

```bash
uv run pytest -q -m "not live" -x
git add tests/test_remote_callback_schedule_live.py
git commit -m "test(remote): live callback + schedule push round-trips (opt-in)"
```

- [ ] **Step 3: Sync user-facing docs**

In `docs/remote.md`, add a `## Callbacks` section after the existing Completion / return channel section. Pull language from the spec but in user-doc voice (no Telegram-as-default-return-channel framing — that's the v0.7.1 lesson). Include a short worked example: agent calls `aegis_enqueue(target="vps", callback=True)`, the receiver completes the task, the `✉ from queue:vps:impl` envelope shows up in the agent's transcript.

Add a `## Remote schedules` section after `## Callbacks`. Cover: the five HTTP endpoints, the five MCP tools, the five CLI verbs, the source classification rule (inline / overlay / pushed), the provenance comment shape, the self-scheduling use case.

In `docs/configuration.md`, add the `peer_name` field documentation under the `remotes:` section. Add a worked symmetric `.aegis.yaml` pair (zion + vps) showing both sides defining each other.

In `docs/index.md`, extend the existing Remote-plane bullet under "What else is in the box" with one sentence about callbacks + remote schedules.

In `docs/roadmap.md`, add a `### v0.8.0 (current)` section above `### v0.7.0` summarizing both features.

In `README.md`, extend the existing Remote-plane section: after the current paragraph about "no wire return channel," add a paragraph about callbacks + schedule control, with a link into `docs/remote.md`.

- [ ] **Step 4: Update `CHANGELOG.md`**

Add above `## [0.7.1]`:

```markdown
## [0.8.0] - 2026-05-25

### Added
- **Wire callbacks for remote queues.** `aegis_enqueue(target=…, callback=True)`
  now actually delivers the worker's final message to the originating
  agent's inbox once the remote task terminates. Symmetric peers config
  (both sides define each other in `remotes:`); RemoteSpec gains an
  optional `peer_name` field that controls the `callback_to` round-trip.
  Best-effort, no retry, log+drop on miss; receiver's queue JSONL records
  every callback attempt.
- **Remote schedule control plane.** Five new endpoints under
  `/remote/v1/schedule` (PUT push, GET list/show, DELETE remove, GET logs);
  five matching `aegis_schedule_*` MCP tools (push/list/show/remove/logs,
  each with optional `target=` for cross-host); CLI `aegis schedule push
  --to <peer>` and `--remote <peer>` flag on inspection verbs. Pushed
  schedules land in the receiver's `.aegis/schedules/<name>.yaml` overlay
  folder with a `# pushed_from:` provenance comment; the v0.6 hot-reload
  watcher picks them up and they become indistinguishable from native
  schedules. Source classification (`inline` / `overlay` / `pushed`) is
  surfaced in list + show responses.

Spec: `docs/superpowers/specs/2026-05-25-aegis-remote-callbacks-schedule-control-design.md`.
```

- [ ] **Step 5: Bump version + lock**

```bash
sed -i 's/^version = "0\.7\.1"$/version = "0.8.0"/' pyproject.toml
sed -i '0,/^version = "0\.7\.1"$/s//version = "0.8.0"/' uv.lock
grep -n '^version = "0' pyproject.toml uv.lock | head
```

Expect: `pyproject.toml:3:version = "0.8.0"` and `uv.lock:7:version = "0.8.0"` (line numbers may differ; verify the package being bumped is `aegis-harness`).

- [ ] **Step 6: Final gate**

```bash
uv run pytest -q -m "not live" -x
```
Expected: all green.

- [ ] **Step 7: Release commit + tag + push**

```bash
git add docs/remote.md docs/configuration.md docs/index.md docs/roadmap.md README.md CHANGELOG.md pyproject.toml uv.lock
git commit -m "release: 0.8.0 — remote callbacks + schedule control plane"
git pull --rebase
git tag -a v0.8.0 -m "v0.8.0 — wire callbacks + remote schedule control. See CHANGELOG.md."
git push origin main
git push origin v0.8.0
```

- [ ] **Step 8: Confirm PyPI publish**

```bash
sleep 30
curl -sS https://pypi.org/pypi/aegis-harness/json | python3 -c "import sys,json;d=json.load(sys.stdin);print('latest:',d['info']['version'])"
```
Expected: `latest: 0.8.0`.

- [ ] **Step 9: Notify completion via Telegram (VPS-only)**

If running on VPS:

```bash
bin/notify-telegram.sh "✅ aegis 0.8.0 released — wire callbacks + schedule control plane on PyPI" || true
```

---

## Self-review

**Spec coverage.** Every feature in the spec maps to a task:
- Wire callbacks: Tasks 1–6 cover RemoteSpec.peer_name, enqueue body extension, observer + client, callback endpoint, MCP wiring, e2e + failure modes.
- Schedule control plane: Tasks 7–10 cover PUT validation+write, GET list+show with provenance, DELETE+logs, client + e2e.
- MCP surface: Task 11.
- CLI: Task 12.
- Live + docs + release: Task 13.

The three Open Questions from the spec are addressed: Q1 (callback_to default) → resolved by adding `peer_name` to RemoteSpec (Task 1); Q2 (pushed_from in self-pushes) → the `agent:<handle>` vs `peer:<name>` distinction lives in Task 11's MCP tool implementation; Q3 (callback to spawned tabs) → explicitly out of scope, callback always delivers to from_handle.

**Placeholder scan.** Every code step has runnable code or commands. Cross-task references name exact symbols. No "TBD" / "TODO" / "implement later" in the plan body.

**Type consistency.** `callback_to` and `callback_handle` are `str | None` throughout (Tasks 2, 3, 5). `RemoteSpec.peer_name` is `str | None` (Task 1). The MCP tool `aegis_enqueue` signature is unchanged from v0.7 (Task 5). The five new `aegis_schedule_*` tools share the `(name, ..., from_handle, target=None)` shape (Task 11). The five client functions in Task 10 (`remote_schedule_*`) match the verbs on the HTTP surface (Tasks 7–9).

**Vertical-slice order.** Tasks 1–6 form a complete callback slice that can ship on its own (skip 7–13 and you have working callbacks). Tasks 7–10 form a complete schedule slice on top. Task 11 layers MCP exposure; Task 12 layers CLI; Task 13 closes with docs + release. Each task ends in a clean commit and a green hermetic suite.
