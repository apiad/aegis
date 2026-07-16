# Aegis Remote TUI (v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `aegis --remote <ws://|ssh://>URL` — a TUI that runs sessions in a remote `aegis serve` daemon over WebSocket, with automatic SSH-tunnel setup for cross-machine use, so sessions survive TUI restarts and network hiccups.

**Architecture:** Introduce a `RemoteSessionManager` that satisfies enough of the `AppBridge` Protocol for the conversation loop (spawn / close / interrupt / handoff / deliver + the three streams + `session_list`). It uses a new async Python `WsClient` mirroring the shipped `web/static/js/ws.js` reference. `AegisApp` accepts an externally-built manager; when `--remote` is passed, the CLI builds a `RemoteSessionManager` (opening a `WsClient`, plus optionally an `SSHTunnel` for `ssh://`) instead of the local `SessionManager`. Auxiliary planes (queue MCP, canvas, terminal, groups, workflow) are represented by `_DisabledPlane` sentinels under `--remote v1`; their TUI surfaces show a "not available in --remote v1" banner. Classic in-process TUI remains the **default** — `--remote` is opt-in.

**Tech Stack:** Python 3.13+, `websockets` (new dep) for the client, `asyncio.subprocess` for SSH, Textual 8.x for UI. Server-side extensions ride Starlette/uvicorn already in use.

## Global Constraints

- **Zero-config guardrail (from S9 spec):** typing `aegis` without `--remote` must still just work; `--remote` (opt-in) auto-launches a co-resident `aegis serve` on localhost when the URL host is `localhost`/`127.0.0.1` and no daemon is already listening.
- **Classic path stays default.** No behavior change for users who don't pass `--remote`. `--classic` is not added yet (it belongs to the S10 flip plan).
- **Handle is the session identifier.** Never `session_id` on the wire (from WS-protocol spec §"Grounding corrections").
- **`protocol_version` must match `PROTOCOL_VERSION` in `wssession.py`.** Bump it (currently `2`) if any frame shape changes; client refuses to operate on major-version skew.
- **Auxiliary planes are stubbed under `--remote v1`.** Do not silently no-op — accessing them from a TUI dashboard must show a visible "not available in --remote v1" banner, so the deferred S9.3 slice has a real user-visible signal.
- **TDD.** Failing test first, then minimal code. Commit per logical unit.
- **`uv run pytest -q -m "not live"`** for fast suite. Add `-m live` tests separately for real subprocess round-trips (they auto-skip when the CLI is off PATH).
- **No modifications to `AegisApp`'s classic construction path** beyond adding an optional injectable `manager=` kwarg; the classic branch continues to build the local `SessionManager`-equivalent facades unchanged.
- **Add `websockets>=13.0` to `pyproject.toml` `[project].dependencies`.** Standalone library; the same wire library Starlette uses internally.
- **File layout matches spec pointers:** `src/aegis/tui/ws_client.py`, `src/aegis/tui/remote_manager.py`, `src/aegis/remote/ssh_tunnel.py`.
- **Never commit if `uv run pytest -q -m "not live" tests/<the-changed-area>/` is red.** Blast-radius subset per AGENTS.md, not the whole suite.

## File structure

**New files:**

- `src/aegis/tui/ws_client.py` — async Python WS client (auth / rpc futures / subscribe / resume / reconnect). Pure client; no Textual imports.
- `src/aegis/tui/remote_manager.py` — `RemoteSessionManager` (AppBridge impl) + `RemoteAgentSession` proxy (observer registration).
- `src/aegis/remote/ssh_tunnel.py` — `SSHTunnel` async context manager wrapping `ssh -L`.
- `tests/tui/test_ws_client_auth.py` — auth + hello + rpc.
- `tests/tui/test_ws_client_streams.py` — subscribe/resume + stream dispatch + `tail`.
- `tests/tui/test_ws_client_reconnect.py` — backoff, connection observer, tail-replay on resume.
- `tests/tui/test_remote_manager.py` — RemoteSessionManager RPC + observer wiring against fake `WsClient`.
- `tests/tui/test_remote_manager_parity.py` — drive both `SessionManager` and `RemoteSessionManager` through the same sequence, assert identical outcomes.
- `tests/remote/test_ssh_tunnel.py` — mock-subprocess unit; loopback integration behind `live` marker.
- `tests/web/test_wssession_handoff_rename.py` — new server RPCs.
- `tests/web/test_wssession_tail.py` — `tail` field on subscribe/resume.
- `tests/cli/test_token_cmd.py` — `aegis token`.
- `tests/cli/test_remote_flag.py` — URL parsing + manager wiring + auto-launch localhost serve.

**Modified files:**

- `src/aegis/web/wssession.py` — add `handoff`, `rename_handle` RPC arms; add optional `tail: int` to `subscribe`/`resume`; propagate through `_open_session`.
- `src/aegis/cli.py` — new `aegis token` subcommand; new `--remote`, `--token`, `--tail` options on the default TUI command; URL-scheme dispatcher (`ws://` vs `ssh://`); auto-launch co-resident serve when `--remote` targets localhost.
- `src/aegis/tui/app.py` — `AegisApp.__init__` accepts optional `manager=` kwarg; when supplied, use it as the AppBridge and construct disabled-plane stubs for auxiliary surfaces; add "not available in --remote v1" banner path in the terminal / canvas / group / queue dashboard actions.
- `src/aegis/tui/widgets.py` — `StatusBar` gains a connection-state indicator + "Disconnected — reconnecting…" banner slot; consumer wires it to `WsClient.on_connection`.
- `pyproject.toml` — add `websockets>=13.0` to `[project].dependencies`.
- `repos/aegis/AGENTS.md` — add index line pointing to the new `know-how/remote-tui.md`.
- `repos/aegis/know-how/remote-tui.md` (new) — how to run + debug `--remote`.

---

### Task 1: Server-side `handoff` + `rename_handle` RPCs

**Files:**
- Modify: `src/aegis/web/wssession.py:210-262` (extend `_call`)
- Test: `tests/web/test_wssession_handoff_rename.py`

**Interfaces:**
- Consumes: existing `WSSession._call` dispatcher, `AppBridge.handoff(from_handle, target_handle, context) -> str`, `AppBridge.rename_handle(old, new) -> dict`.
- Produces: two new RPC method names — `handoff` (params: `{from_handle, target_handle, context}`) returning `{result: str}`, and `rename_handle` (params: `{old, new}`) returning the bridge dict.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_wssession_handoff_rename.py
import pytest
from tests.web.helpers import ws_pair, FakeManager  # existing helper pattern
from aegis.web.wssession import WSSession, PROTOCOL_VERSION


@pytest.mark.asyncio
async def test_handoff_rpc_calls_manager():
    mgr = FakeManager()
    mgr.handoff_result = "delivered"
    client_t, server_t = ws_pair()
    sess = WSSession(server_t, mgr, registry=FakeRegistry(),
                     web_cfg=FakeCfg(token="t"), constants={})
    run = asyncio.create_task(sess.run())
    await client_t.send_json({"type": "auth", "token": "t"})
    await client_t.receive_json()  # hello
    await client_t.send_json({
        "type": "rpc", "id": 1, "method": "handoff",
        "params": {"from_handle": "a", "target_handle": "b",
                   "context": "please pick up"},
    })
    resp = await client_t.receive_json()
    assert resp == {"type": "rpc_response", "id": 1, "ok": True,
                    "result": {"result": "delivered"}}
    assert mgr.handoff_calls == [("a", "b", "please pick up")]
    run.cancel()


@pytest.mark.asyncio
async def test_rename_handle_rpc_calls_manager():
    mgr = FakeManager()
    mgr.rename_result = {"old": "swift-bohr", "new": "quiet-turing"}
    client_t, server_t = ws_pair()
    sess = WSSession(server_t, mgr, registry=FakeRegistry(),
                     web_cfg=FakeCfg(token="t"), constants={})
    run = asyncio.create_task(sess.run())
    await client_t.send_json({"type": "auth", "token": "t"})
    await client_t.receive_json()
    await client_t.send_json({
        "type": "rpc", "id": 7, "method": "rename_handle",
        "params": {"old": "swift-bohr", "new": "quiet-turing"},
    })
    resp = await client_t.receive_json()
    assert resp["ok"] is True
    assert resp["result"] == {"old": "swift-bohr", "new": "quiet-turing"}
    run.cancel()
```

(Reuse whatever `FakeManager` / `ws_pair` helpers `tests/web/test_wssession_*.py` already establishes — grep for one existing file to copy the fixture shape.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_wssession_handoff_rename.py -v`
Expected: FAIL — `handoff` / `rename_handle` currently fall through to `_RpcUnknown`.

- [ ] **Step 3: Add the two RPC arms**

In `src/aegis/web/wssession.py`, inside `_call()`, before the final `raise _RpcUnknown(method)`:

```python
        if method == "handoff":
            result = await self._m.handoff(
                params["from_handle"], params["target_handle"],
                params["context"])
            return {"result": result}
        if method == "rename_handle":
            return await self._m.rename_handle(
                params["old"], params["new"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/web/test_wssession_handoff_rename.py -v`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_wssession_handoff_rename.py src/aegis/web/wssession.py
git commit -m "feat(web): handoff + rename_handle RPCs on wssession"
```

---

### Task 2: Server-side `tail` field on `subscribe` and `resume`

**Files:**
- Modify: `src/aegis/web/wssession.py:266-336` (extend `_subscribe`, `_resume`, `_open_session`)
- Test: `tests/web/test_wssession_tail.py`

**Interfaces:**
- Consumes: existing `_tail_lower_seq(history, tail)` helper already in the file.
- Produces: `subscribe(target, tail?)` and `resume(subscriptions[{handle,last_seq,tail?}])` now respect an optional per-subscription `tail` int that overrides the server-side `REPLAY_TAIL` default. `tail=0` means "don't replay any old history" (only live from here on); omitted `tail` keeps the current default behavior.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_wssession_tail.py
@pytest.mark.asyncio
async def test_subscribe_tail_override_wins_over_constant():
    """subscribe(tail=2) must send only the last 2 coalesced blocks even
    when REPLAY_TAIL is 10."""
    mgr = FakeManager()
    reg = FakeRegistry(history=[(i, _fake_tool_use(i)) for i in range(1, 11)],
                       current_seq=10)
    client_t, server_t = ws_pair()
    sess = WSSession(server_t, mgr, registry=reg,
                     web_cfg=FakeCfg(token="t"),
                     constants={"REPLAY_TAIL": 10, "RESUME_GAP_CAP": 1000})
    run = asyncio.create_task(sess.run())
    await client_t.send_json({"type": "auth", "token": "t"})
    await client_t.receive_json()
    await client_t.send_json({
        "type": "subscribe", "tail": 2,
        "target": {"kind": "session", "handle": "swift-bohr"},
    })
    seqs = []
    while True:
        fr = await client_t.receive_json()
        if fr.get("kind") == "history_complete":
            break
        if fr.get("kind") == "event":
            seqs.append(fr["seq"])
    assert seqs == [9, 10]
    run.cancel()


@pytest.mark.asyncio
async def test_resume_per_subscription_tail_used_on_large_gap():
    """resume with a >gap_cap gap and tail=3 sends window_reset then last 3."""
    # ... build history 1..500, resume last_seq=1, gap_cap=100, tail=3
    #     expect: window_reset then event seq 498, 499, 500 then history_complete
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/web/test_wssession_tail.py -v`
Expected: FAIL — server ignores `tail` today.

- [ ] **Step 3: Thread `tail` through the dispatch chain**

In `src/aegis/web/wssession.py`:

```python
    async def _subscribe(self, frame: dict) -> None:
        target = frame.get("target") or {}
        tail = frame.get("tail")           # NEW
        if target.get("kind") == "session":
            await self._open_session(target["handle"], from_seq=0, tail=tail)
        elif (target.get("kind") == "global"
              and target.get("stream") == "session_list"):
            # ... unchanged
```

```python
    async def _resume(self, frame: dict) -> None:
        for sub in frame.get("subscriptions") or []:
            await self._open_session(
                sub["handle"],
                from_seq=int(sub.get("last_seq", 0)),
                resume=True,
                tail=sub.get("tail"))
        # ... unchanged
```

```python
    async def _open_session(self, handle: str, *, from_seq: int,
                            resume: bool = False,
                            tail: int | None = None) -> None:
        # ... existing setup unchanged until the branch that picks `lower` ...
        if resume and large_gap:
            self._emit({"type": "stream", "kind": "window_reset",
                        "handle": handle, "dropped_through_seq": from_seq})
            # NEW: apply tail on the fresh-history replay after window_reset
            effective_tail = (tail if tail is not None
                              else self._constants.get("REPLAY_TAIL", 0))
            lower = _tail_lower_seq(hist, effective_tail)
        elif resume:
            lower = from_seq
        else:
            effective_tail = (tail if tail is not None
                              else self._constants.get("REPLAY_TAIL", 0))
            lower = _tail_lower_seq(hist, effective_tail)
        # ... rest unchanged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web/test_wssession_tail.py tests/web/ -v`
Expected: both new tests PASS, no regressions in existing `tests/web/`.

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_wssession_tail.py src/aegis/web/wssession.py
git commit -m "feat(web): optional per-subscription tail on subscribe/resume"
```

---

### Task 3: `aegis token` subcommand

**Files:**
- Modify: `src/aegis/cli.py` (add command after `web`)
- Test: `tests/cli/test_token_cmd.py`

**Interfaces:**
- Consumes: `find_project_root()`, `aegis.cli._ensure_web_token(root)`.
- Produces: `aegis token` prints the active web token (creating one if needed) to stdout, no trailing newline formatting other than the default Typer echo. Non-zero exit only when no `.aegis.yaml` exists in this or any ancestor directory.

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_token_cmd.py
from typer.testing import CliRunner
from aegis.cli import app


def test_aegis_token_prints_and_persists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")  # minimal
    runner = CliRunner()
    r = runner.invoke(app, ["token"])
    assert r.exit_code == 0
    token = r.stdout.strip()
    assert len(token) >= 32                # secrets.token_urlsafe(32)
    # Idempotent — a second call returns the same token
    r2 = runner.invoke(app, ["token"])
    assert r2.stdout.strip() == token


def test_aegis_token_fails_without_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    r = runner.invoke(app, ["token"])
    assert r.exit_code != 0
    assert "No .aegis.yaml" in r.stdout or "No .aegis.yaml" in r.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_token_cmd.py -v`
Expected: FAIL — `token` command not defined.

- [ ] **Step 3: Add the subcommand**

In `src/aegis/cli.py`, after the `web` command:

```python
@app.command()
def token() -> None:
    """Print the aegis web token (create one if missing)."""
    root = find_project_root() or Path.cwd()
    if not (root / ".aegis.yaml").is_file():
        _console.print("[red]No .aegis.yaml found.[/red]")
        raise typer.Exit(1)
    typer.echo(_ensure_web_token(root))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cli/test_token_cmd.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/cli/test_token_cmd.py src/aegis/cli.py
git commit -m "feat(cli): aegis token subcommand for headless fetch"
```

---

### Task 4: `WsClient` — auth handshake + hello + rpc futures

**Files:**
- Create: `src/aegis/tui/ws_client.py`
- Modify: `pyproject.toml` — add `websockets>=13.0`
- Test: `tests/tui/test_ws_client_auth.py`

**Interfaces:**
- Consumes: `websockets.connect` (async).
- Produces:

```python
class WsClient:
    def __init__(self, url: str, token: str) -> None: ...
    async def connect(self) -> dict:
        """Open, auth, return hello frame. Raises AuthFailed on 4401."""
    async def rpc(self, method: str, params: dict | None = None) -> dict:
        """Send rpc frame, return result dict; raise RpcError on ok=False."""
    async def close(self) -> None: ...
    @property
    def constants(self) -> dict: ...   # from hello

class AuthFailed(Exception): ...
class RpcError(Exception): ...
```

Later tasks (5, 6) extend this same class with `subscribe`, `resume`, `on_connection`, etc.

- [ ] **Step 1: Add `websockets` to project deps and sync**

Edit `pyproject.toml`, add `"websockets>=13.0",` to the `dependencies` list (keep alphabetical if the list is sorted; otherwise append). Then:

```bash
uv sync
```

- [ ] **Step 2: Write the failing test**

```python
# tests/tui/test_ws_client_auth.py
import asyncio
import json
import pytest
import websockets

from aegis.tui.ws_client import WsClient, AuthFailed, RpcError


async def _echo_server_that_expects_token(token: str, port_holder: list):
    """Tiny in-process WS server that mimics the aegis hello handshake."""
    async def handler(ws):
        first = json.loads(await ws.recv())
        if first.get("type") != "auth" or first.get("token") != token:
            await ws.close(4401, "unauthorized")
            return
        await ws.send(json.dumps({
            "type": "hello", "server_version": "0.16.0",
            "protocol_version": 2,
            "constants": {"REPLAY_TAIL": 10, "RESUME_GAP_CAP": 1000},
            "supported_kinds": ["event", "state"],
        }))
        # Then honour a single rpc echo
        req = json.loads(await ws.recv())
        if req["method"] == "boom":
            await ws.send(json.dumps({
                "type": "rpc_response", "id": req["id"], "ok": False,
                "error": "kaboom"}))
            return
        await ws.send(json.dumps({
            "type": "rpc_response", "id": req["id"], "ok": True,
            "result": {"echo": req["params"]}}))
    async with websockets.serve(handler, "127.0.0.1", 0) as s:
        port_holder.append(s.sockets[0].getsockname()[1])
        await asyncio.Future()   # run forever


@pytest.mark.asyncio
async def test_auth_success_returns_hello_with_constants():
    port_holder: list = []
    server = asyncio.create_task(_echo_server_that_expects_token("goodtoken", port_holder))
    await asyncio.sleep(0.05)
    try:
        client = WsClient(f"ws://127.0.0.1:{port_holder[0]}", "goodtoken")
        hello = await client.connect()
        assert hello["protocol_version"] == 2
        assert client.constants["REPLAY_TAIL"] == 10
        result = await client.rpc("list_agents", {})
        assert result == {"echo": {}}
        await client.close()
    finally:
        server.cancel()


@pytest.mark.asyncio
async def test_auth_failure_raises():
    port_holder: list = []
    server = asyncio.create_task(_echo_server_that_expects_token("right", port_holder))
    await asyncio.sleep(0.05)
    try:
        client = WsClient(f"ws://127.0.0.1:{port_holder[0]}", "wrong")
        with pytest.raises(AuthFailed):
            await client.connect()
    finally:
        server.cancel()


@pytest.mark.asyncio
async def test_rpc_error_propagates():
    port_holder: list = []
    server = asyncio.create_task(_echo_server_that_expects_token("t", port_holder))
    await asyncio.sleep(0.05)
    try:
        client = WsClient(f"ws://127.0.0.1:{port_holder[0]}", "t")
        await client.connect()
        with pytest.raises(RpcError, match="kaboom"):
            await client.rpc("boom", {})
        await client.close()
    finally:
        server.cancel()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_ws_client_auth.py -v`
Expected: FAIL — `aegis.tui.ws_client` module doesn't exist.

- [ ] **Step 4: Write the minimal `WsClient`**

Create `src/aegis/tui/ws_client.py`:

```python
"""Async WebSocket client for aegis serve. Python mirror of
``web/static/js/ws.js`` — auth handshake, rpc-as-futures, subscribe,
resume with per-subscription tail, and reconnect with exponential backoff.
This module is pure (no Textual imports); the TUI wires callbacks through
``on_connection`` / observer registration on RemoteAgentSession.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import websockets
from websockets.exceptions import ConnectionClosed


class AuthFailed(Exception): ...
class RpcError(Exception): ...
class ProtocolMismatch(Exception): ...


PROTOCOL_MAJOR = 2   # bump in lockstep with wssession.PROTOCOL_VERSION


class WsClient:
    def __init__(self, url: str, token: str) -> None:
        self._url = url
        self._token = token
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._constants: dict = {}
        self._closed = False

    @property
    def constants(self) -> dict:
        return dict(self._constants)

    async def connect(self) -> dict:
        try:
            self._ws = await websockets.connect(self._url)
        except OSError as exc:
            raise AuthFailed(f"connect failed: {exc}") from exc
        await self._ws.send(json.dumps({"type": "auth", "token": self._token}))
        try:
            hello_raw = await self._ws.recv()
        except ConnectionClosed as exc:
            code = getattr(exc, "code", None)
            raise AuthFailed(f"closed during auth (code={code})") from exc
        hello = json.loads(hello_raw)
        if hello.get("type") != "hello":
            raise AuthFailed(f"expected hello, got {hello!r}")
        if hello.get("protocol_version", 0) != PROTOCOL_MAJOR:
            raise ProtocolMismatch(
                f"server protocol {hello.get('protocol_version')} "
                f"!= client {PROTOCOL_MAJOR}")
        self._constants = hello.get("constants", {})
        self._reader = asyncio.create_task(self._read_loop())
        return hello

    async def rpc(self, method: str, params: dict | None = None) -> dict:
        if self._ws is None:
            raise RpcError("not connected")
        rid = self._next_id = self._next_id + 1
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        await self._ws.send(json.dumps({
            "type": "rpc", "id": rid, "method": method, "params": params or {},
        }))
        return await fut

    async def close(self) -> None:
        self._closed = True
        if self._reader:
            self._reader.cancel()
        if self._ws:
            await self._ws.close()

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                self._handle(msg)
        except ConnectionClosed:
            self._fail_pending("connection closed")

    def _handle(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "rpc_response":
            fut = self._pending.pop(msg["id"], None)
            if fut is None or fut.done():
                return
            if msg.get("ok"):
                fut.set_result(msg.get("result", {}))
            else:
                fut.set_exception(RpcError(msg.get("error", "rpc failed")))
        elif t == "error":
            rid = msg.get("id")
            if rid is not None:
                fut = self._pending.pop(rid, None)
                if fut and not fut.done():
                    fut.set_exception(RpcError(
                        msg.get("message") or msg.get("code") or "error"))

    def _fail_pending(self, reason: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RpcError(reason))
        self._pending.clear()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_ws_client_auth.py -v`
Expected: all three PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/aegis/tui/ws_client.py tests/tui/test_ws_client_auth.py
git commit -m "feat(tui): WsClient core — auth, hello, rpc futures"
```

---

### Task 5: `WsClient` — subscribe / resume with `tail` + stream dispatch

**Files:**
- Modify: `src/aegis/tui/ws_client.py`
- Test: `tests/tui/test_ws_client_streams.py`

**Interfaces:**
- Consumes: existing `WsClient._handle`, `aegis.state.event_codec.decode_event`.
- Produces:

```python
class WsClient:
    def on(self, kind: str, fn: Callable[[dict], None]) -> None:
        """Register a stream-frame handler. `kind` is 'event' | 'state' |
        'inbox' | 'session_list' | 'history_complete' | 'window_reset' |
        'queue_digest'. Handlers receive the raw stream frame dict."""
    async def subscribe_session(self, handle: str, *, tail: int | None = None) -> None: ...
    async def subscribe_global(self, stream: str) -> None: ...
    async def unsubscribe_session(self, handle: str) -> None: ...
    # Internally tracks per-handle last_seq so `resume` (Task 6) can replay.
```

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_ws_client_streams.py
@pytest.mark.asyncio
async def test_subscribe_dispatches_stream_frames_and_tracks_last_seq():
    port_holder: list = []

    async def handler(ws):
        first = json.loads(await ws.recv())
        assert first["type"] == "auth"
        await ws.send(json.dumps({
            "type": "hello", "protocol_version": 2,
            "constants": {}, "supported_kinds": ["event"]}))
        sub = json.loads(await ws.recv())
        assert sub == {"type": "subscribe", "tail": 5,
                       "target": {"kind": "session", "handle": "swift-bohr"}}
        # Fire 3 event frames + history_complete
        for i in (7, 8, 9):
            await ws.send(json.dumps({
                "type": "stream", "kind": "event",
                "handle": "swift-bohr", "seq": i,
                "event_type": "AssistantText",
                "event": {"type": "AssistantText", "text": f"m{i}",
                          "message_id": None}}))
        await ws.send(json.dumps({
            "type": "stream", "kind": "history_complete",
            "handle": "swift-bohr", "current_seq": 9}))
        await asyncio.Future()

    async with websockets.serve(handler, "127.0.0.1", 0) as s:
        port_holder.append(s.sockets[0].getsockname()[1])
        c = WsClient(f"ws://127.0.0.1:{port_holder[0]}", "t")
        await c.connect()
        seen: list[dict] = []
        c.on("event", seen.append)
        done = asyncio.Event()
        c.on("history_complete", lambda fr: done.set())
        await c.subscribe_session("swift-bohr", tail=5)
        await asyncio.wait_for(done.wait(), 1.0)
        assert [fr["seq"] for fr in seen] == [7, 8, 9]
        assert c.last_seq("swift-bohr") == 9
        await c.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_ws_client_streams.py -v`
Expected: FAIL — `on`, `subscribe_session`, `last_seq` all AttributeError.

- [ ] **Step 3: Extend `WsClient`**

In `src/aegis/tui/ws_client.py`, extend `__init__`:

```python
        self._handlers: dict[str, list[Callable[[dict], None]]] = {}
        self._subs: dict[str, int] = {}       # handle -> last_seq
        self._globals: set[str] = set()
```

Add methods:

```python
    def on(self, kind: str, fn: Callable[[dict], None]) -> None:
        self._handlers.setdefault(kind, []).append(fn)

    def last_seq(self, handle: str) -> int:
        return self._subs.get(handle, 0)

    async def subscribe_session(self, handle: str, *,
                                tail: int | None = None) -> None:
        assert self._ws is not None
        self._subs.setdefault(handle, 0)
        frame: dict = {"type": "subscribe",
                       "target": {"kind": "session", "handle": handle}}
        if tail is not None:
            frame["tail"] = tail
        await self._ws.send(json.dumps(frame))

    async def subscribe_global(self, stream: str) -> None:
        assert self._ws is not None
        self._globals.add(stream)
        await self._ws.send(json.dumps({
            "type": "subscribe",
            "target": {"kind": "global", "stream": stream}}))

    async def unsubscribe_session(self, handle: str) -> None:
        assert self._ws is not None
        self._subs.pop(handle, None)
        await self._ws.send(json.dumps({
            "type": "unsubscribe",
            "target": {"kind": "session", "handle": handle}}))
```

Extend `_handle`:

```python
        elif t == "stream":
            handle = msg.get("handle")
            seq = msg.get("seq")
            if handle and isinstance(seq, int):
                self._subs[handle] = max(self._subs.get(handle, 0), seq)
            for fn in self._handlers.get(msg.get("kind", ""), ()):
                try:
                    fn(msg)
                except Exception:
                    pass    # observer errors never break the read loop
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_ws_client_streams.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/ws_client.py tests/tui/test_ws_client_streams.py
git commit -m "feat(tui): WsClient subscribe/unsubscribe + stream dispatch"
```

---

### Task 6: `WsClient` — reconnect with backoff, connection observer, tail-replay on resume

**Files:**
- Modify: `src/aegis/tui/ws_client.py`
- Test: `tests/tui/test_ws_client_reconnect.py`

**Interfaces:**
- Consumes: existing `WsClient._read_loop`.
- Produces:

```python
class WsClient:
    def on_connection(self, fn: Callable[[bool], None]) -> None:
        """Fired with `True` on successful (re)connect, `False` on drop."""
    # Reconnect is automatic once `connect()` has succeeded once. Uses
    # exponential backoff: 1, 2, 4, 8, 16, 30, 30... capped at 30s.
    # On each reconnect: re-authenticate, then send `resume` with the
    # tracked last_seq per handle and default_tail per subscription.
    # `default_tail` is set from `WsClient.__init__(default_tail=10)`.
```

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_ws_client_reconnect.py
@pytest.mark.asyncio
async def test_reconnect_sends_resume_with_tail_and_flips_connection():
    """Kill the server mid-session, restart, confirm client re-auths and
    sends a `resume` frame with tail=10 and last_seq=9 for the tracked handle."""
    # Use a driver-server pattern that accepts one connection, then closes,
    # then accepts a second and asserts on the resume frame it receives.
    port = _pick_free_port()
    seen_resume: list[dict] = []

    async def one_shot_close(ws):
        await ws.recv()  # auth
        await ws.send(json.dumps({"type": "hello", "protocol_version": 2,
                                  "constants": {}, "supported_kinds": []}))
        await ws.recv()  # subscribe
        await ws.send(json.dumps({
            "type": "stream", "kind": "event", "handle": "h", "seq": 9,
            "event_type": "AssistantText",
            "event": {"type": "AssistantText", "text": "x", "message_id": None}}))
        await ws.close()

    async def accept_resume(ws):
        await ws.recv()  # auth
        await ws.send(json.dumps({"type": "hello", "protocol_version": 2,
                                  "constants": {}, "supported_kinds": []}))
        frame = json.loads(await ws.recv())
        seen_resume.append(frame)
        await asyncio.Future()

    connected_events: list[bool] = []

    # Server v1: close after one event
    s1 = await websockets.serve(one_shot_close, "127.0.0.1", port)
    c = WsClient(f"ws://127.0.0.1:{port}", "t", default_tail=10)
    c.on_connection(connected_events.append)
    await c.connect()
    await c.subscribe_session("h")
    await asyncio.sleep(0.3)   # let event arrive + server close
    s1.close()
    await s1.wait_closed()
    assert connected_events[-1] is False

    # Server v2: expect resume
    s2 = await websockets.serve(accept_resume, "127.0.0.1", port)
    await asyncio.wait_for(_until(lambda: bool(seen_resume)), 5.0)
    assert seen_resume[0]["type"] == "resume"
    assert seen_resume[0]["subscriptions"] == [
        {"handle": "h", "last_seq": 9, "tail": 10}]
    assert connected_events[-1] is True
    await c.close()
    s2.close()
    await s2.wait_closed()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_ws_client_reconnect.py -v`
Expected: FAIL — no reconnect logic, no `on_connection`, no `default_tail`.

- [ ] **Step 3: Add reconnect + `on_connection` + `default_tail`**

Extend `WsClient.__init__` to accept `default_tail: int = 10`, store it, initialise `self._connection_handlers: list[Callable[[bool], None]] = []` and `self._reconnect_task: asyncio.Task | None = None`, `self._authed_once = False`.

Add:

```python
    def on_connection(self, fn: Callable[[bool], None]) -> None:
        self._connection_handlers.append(fn)

    def _emit_connection(self, up: bool) -> None:
        for fn in list(self._connection_handlers):
            try:
                fn(up)
            except Exception:
                pass
```

In `connect()`, after a successful hello: `self._authed_once = True; self._emit_connection(True)`.

Replace `_read_loop` with:

```python
    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                self._handle(msg)
        except ConnectionClosed:
            pass
        self._fail_pending("connection closed")
        self._emit_connection(False)
        if not self._closed and self._authed_once:
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        delay = 1.0
        while not self._closed:
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)
            try:
                self._ws = await websockets.connect(self._url)
            except OSError:
                continue
            try:
                await self._ws.send(json.dumps({"type": "auth",
                                                "token": self._token}))
                hello = json.loads(await self._ws.recv())
                if hello.get("type") != "hello":
                    await self._ws.close()
                    continue
            except (ConnectionClosed, OSError):
                continue
            self._constants = hello.get("constants", self._constants)
            # Resend resume with current subscriptions + tail
            await self._ws.send(json.dumps({
                "type": "resume",
                "subscriptions": [
                    {"handle": h, "last_seq": s, "tail": self._default_tail}
                    for h, s in self._subs.items()],
                "globals": list(self._globals),
            }))
            self._emit_connection(True)
            self._reader = asyncio.create_task(self._read_loop())
            return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_ws_client_reconnect.py -v`
Expected: PASS. (May need small `asyncio.sleep` timing tolerance in the test — adjust once.)

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/ws_client.py tests/tui/test_ws_client_reconnect.py
git commit -m "feat(tui): WsClient reconnect + on_connection + tail-replay resume"
```

---

### Task 7: `RemoteSessionManager` — conversation-loop AppBridge

**Files:**
- Create: `src/aegis/tui/remote_manager.py`
- Test: `tests/tui/test_remote_manager.py`
- Test: `tests/tui/test_remote_manager_parity.py`

**Interfaces:**
- Consumes: `WsClient` (Tasks 4–6), `aegis.mcp.bridge.AppBridge`, `aegis.mcp.bridge.SessionInfo`, `aegis.state.event_codec.decode_event`.
- Produces:

```python
class RemoteUnsupportedError(RuntimeError):
    """Raised when a --remote v1 TUI touches an auxiliary plane
    (queues, canvas, terminals, groups, workflow, scheduler) that isn't
    yet exposed over the WS protocol."""


class _DisabledPlane:
    """Sentinel that raises RemoteUnsupportedError on any attribute or
    method access, with a stable message the TUI catches to show its
    'not available in --remote v1' banner."""


class RemoteAgentSession:
    handle: str
    def add_event_observer(self, cb): ...
    def add_state_observer(self, cb): ...
    def add_inbox_observer(self, cb): ...
    async def deliver(self, msg) -> Delivery: ...


class RemoteSessionManager:
    def __init__(self, ws: WsClient) -> None: ...
    async def start(self) -> None:
        """Subscribe to session_list; pre-populate _sessions map from the
        initial `session_list` stream frame."""

    # AppBridge methods (subset — conversation loop only)
    async def spawn(self, profile, *, handle=None,
                    opening_prompt=None, spawned_by=None) -> str: ...
    async def close(self, handle: str) -> None: ...
    async def interrupt(self, handle: str) -> None: ...
    async def handoff(self, from_handle, target_handle, context) -> str: ...
    async def rename_handle(self, old, new) -> dict: ...
    def list_sessions(self) -> list[SessionInfo]: ...
    def list_agents(self) -> list[str]: ...
    def get(self, handle: str) -> RemoteAgentSession | None: ...

    # AppBridge auxiliary attrs — all _DisabledPlane sentinels
    queue_manager: _DisabledPlane
    inbox_router: _DisabledPlane
    canvas_manager: _DisabledPlane
    terminal_manager: _DisabledPlane
    groups: _DisabledPlane
    locks: _DisabledPlane
    remotes: dict         # {}
    scheduler: None
    state_root: Path      # cwd — used for read-only path resolution only
    workflow_registry: _DisabledPlane

    def inline_schedule_names(self) -> set[str]: ...   # returns set()
    def register_agent(self, slug, agent) -> None:     # raises RemoteUnsupported
    def register_queue(self, queue) -> None:           # raises RemoteUnsupported
    def reload_plugins(self) -> None:                  # raises RemoteUnsupported
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/tui/test_remote_manager.py
@pytest.mark.asyncio
async def test_spawn_and_close_round_trip_via_rpc(fake_ws_client):
    fake_ws_client.rpc_result("spawn_session", {"handle": "quiet-turing"})
    fake_ws_client.rpc_result("close_session", {"ok": True})
    mgr = RemoteSessionManager(fake_ws_client)
    handle = await mgr.spawn("main")
    assert handle == "quiet-turing"
    assert fake_ws_client.rpc_calls[0] == ("spawn_session",
                                            {"agent_profile": "main"})
    await mgr.close("quiet-turing")
    assert fake_ws_client.rpc_calls[1] == ("close_session",
                                            {"handle": "quiet-turing"})


@pytest.mark.asyncio
async def test_deliver_returns_delivery_dataclass(fake_ws_client):
    fake_ws_client.rpc_result("deliver", {"delivery": "landed", "depth": 0})
    mgr = RemoteSessionManager(fake_ws_client)
    fake_ws_client.inject_session_list_stream(
        added=[{"handle": "h", "agent_slug": "main", "state": "ready",
                "active": True, "unseen": False}])
    await mgr.start()
    session = mgr.get("h")
    receipt = await session.deliver(_fake_inbox_message("hi"))
    assert receipt.disposition == "landed"
    assert receipt.depth == 0


@pytest.mark.asyncio
async def test_event_stream_routes_to_registered_observer(fake_ws_client):
    mgr = RemoteSessionManager(fake_ws_client)
    fake_ws_client.inject_session_list_stream(
        added=[{"handle": "h", "agent_slug": "main", "state": "ready",
                "active": True, "unseen": False}])
    await mgr.start()
    session = mgr.get("h")
    got: list = []
    session.add_event_observer(got.append)
    fake_ws_client.inject_stream("event", {
        "handle": "h", "seq": 42, "event_type": "AssistantText",
        "event": {"type": "AssistantText", "text": "hi", "message_id": None}})
    assert len(got) == 1
    assert got[0].__class__.__name__ == "AssistantText"


def test_disabled_plane_raises_on_access():
    mgr = RemoteSessionManager(_dummy_ws_client())
    with pytest.raises(RemoteUnsupportedError, match="not available in --remote v1"):
        mgr.canvas_manager.open("x")
    with pytest.raises(RemoteUnsupportedError):
        mgr.queue_manager.enqueue("q", "payload")
```

Plus a parity test:

```python
# tests/tui/test_remote_manager_parity.py
@pytest.mark.asyncio
async def test_conversation_loop_matches_local_manager():
    """Drive both a local SessionManager and a RemoteSessionManager
    (behind a real in-process WSSession + fake Manager) through spawn →
    deliver → interrupt → close, assert identical event orderings."""
    # This is the parity gate the S9 spec asks for.
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_remote_manager.py tests/tui/test_remote_manager_parity.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `RemoteSessionManager`**

Create `src/aegis/tui/remote_manager.py` — full implementation. Key shape:

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from aegis.mcp.bridge import SessionInfo
from aegis.state.event_codec import decode_event
from aegis.tui.ws_client import WsClient


class RemoteUnsupportedError(RuntimeError):
    pass


_MSG = "not available in --remote v1"


class _DisabledPlane:
    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, item: str):
        raise RemoteUnsupportedError(f"{self._name}.{item}: {_MSG}")


@dataclass
class _Delivery:
    disposition: str
    depth: int


class RemoteAgentSession:
    def __init__(self, handle: str, ws: WsClient) -> None:
        self.handle = handle
        self._ws = ws
        self._event_obs: list[Callable] = []
        self._state_obs: list[Callable] = []
        self._inbox_obs: list[Callable] = []

    def add_event_observer(self, cb): self._event_obs.append(cb)
    def add_state_observer(self, cb): self._state_obs.append(cb)
    def add_inbox_observer(self, cb): self._inbox_obs.append(cb)

    async def deliver(self, msg) -> _Delivery:
        r = await self._ws.rpc("deliver", {"handle": self.handle,
                                            "message": msg.body})
        return _Delivery(disposition=r["delivery"], depth=r["depth"])


class RemoteSessionManager:
    def __init__(self, ws: WsClient, *, cwd: Path | None = None) -> None:
        self._ws = ws
        self._sessions: dict[str, RemoteAgentSession] = {}
        self._infos: dict[str, SessionInfo] = {}
        self._agents: list[str] = []
        # AppBridge auxiliary plane stubs
        self.queue_manager = _DisabledPlane("queue_manager")
        self.inbox_router = _DisabledPlane("inbox_router")
        self.canvas_manager = _DisabledPlane("canvas_manager")
        self.terminal_manager = _DisabledPlane("terminal_manager")
        self.groups = _DisabledPlane("groups")
        self.locks = _DisabledPlane("locks")
        self.workflow_registry = _DisabledPlane("workflow_registry")
        self.remotes: dict = {}
        self.scheduler = None
        self.state_root = cwd or Path.cwd()

    async def start(self) -> None:
        self._ws.on("event", self._on_event)
        self._ws.on("state", self._on_state)
        self._ws.on("inbox", self._on_inbox)
        self._ws.on("session_list", self._on_session_list)
        r = await self._ws.rpc("list_agents", {})
        self._agents = list(r.get("agents", []))
        r = await self._ws.rpc("list_sessions", {})
        for si in r.get("sessions", []):
            self._add_session(si)
        await self._ws.subscribe_global("session_list")

    # AppBridge conversation-loop methods
    async def spawn(self, profile, *, handle=None,
                    opening_prompt=None, spawned_by=None) -> str:
        r = await self._ws.rpc("spawn_session", {"agent_profile": profile})
        h = r["handle"]
        # Subscribe to the new handle so events start flowing.
        await self._ws.subscribe_session(h,
                                          tail=self._ws.constants.get("REPLAY_TAIL", 10))
        return h

    async def close(self, handle: str) -> None:
        await self._ws.rpc("close_session", {"handle": handle})
        self._sessions.pop(handle, None)
        self._infos.pop(handle, None)

    async def interrupt(self, handle: str) -> None:
        await self._ws.rpc("interrupt_session", {"handle": handle})

    async def handoff(self, from_handle, target_handle, context) -> str:
        r = await self._ws.rpc("handoff", {
            "from_handle": from_handle, "target_handle": target_handle,
            "context": context})
        return r["result"]

    async def rename_handle(self, old, new) -> dict:
        return await self._ws.rpc("rename_handle", {"old": old, "new": new})

    def list_sessions(self) -> list[SessionInfo]:
        return list(self._infos.values())

    def list_agents(self) -> list[str]:
        return list(self._agents)

    def get(self, handle: str) -> RemoteAgentSession | None:
        return self._sessions.get(handle)

    def inline_schedule_names(self) -> set[str]:
        return set()

    def register_agent(self, slug, agent) -> None:
        raise RemoteUnsupportedError(f"register_agent: {_MSG}")

    def register_queue(self, queue) -> None:
        raise RemoteUnsupportedError(f"register_queue: {_MSG}")

    def reload_plugins(self) -> None:
        raise RemoteUnsupportedError(f"reload_plugins: {_MSG}")

    # Stream dispatch
    def _add_session(self, si: dict) -> None:
        info = SessionInfo(handle=si["handle"], agent_slug=si["agent_slug"],
                           state=si["state"], active=si["active"],
                           unseen=si["unseen"],
                           spawned_by=si.get("spawned_by"))
        self._infos[info.handle] = info
        self._sessions.setdefault(info.handle, RemoteAgentSession(info.handle, self._ws))

    def _on_event(self, fr: dict) -> None:
        sess = self._sessions.get(fr.get("handle", ""))
        if sess is None:
            return
        try:
            ev = decode_event(fr["event"])
        except Exception:
            return
        for cb in list(sess._event_obs):
            try: cb(ev)
            except Exception: pass

    def _on_state(self, fr: dict) -> None:
        sess = self._sessions.get(fr.get("handle", ""))
        if sess is None:
            return
        for cb in list(sess._state_obs):
            try: cb(fr.get("state"), fr.get("metrics"))
            except Exception: pass

    def _on_inbox(self, fr: dict) -> None:
        sess = self._sessions.get(fr.get("handle", ""))
        if sess is None:
            return
        for cb in list(sess._inbox_obs):
            try: cb(fr.get("msg"))
            except Exception: pass

    def _on_session_list(self, fr: dict) -> None:
        for si in fr.get("added", []) or []:
            self._add_session(si)
        for h in fr.get("removed", []) or []:
            self._sessions.pop(h, None)
            self._infos.pop(h, None)
        for si in fr.get("updated", []) or []:
            self._add_session(si)  # upsert
```

Provide a `tests/tui/conftest.py` `fake_ws_client` fixture that satisfies the `WsClient` public surface used above (`on`, `rpc`, `subscribe_global`, `subscribe_session`, `constants`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_remote_manager.py tests/tui/test_remote_manager_parity.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/remote_manager.py tests/tui/test_remote_manager.py tests/tui/test_remote_manager_parity.py tests/tui/conftest.py
git commit -m "feat(tui): RemoteSessionManager — conversation-loop AppBridge over WS"
```

---

### Task 8: `--remote ws://URL` CLI wiring + AegisApp manager injection

**Files:**
- Modify: `src/aegis/cli.py` (extend `run` callback)
- Modify: `src/aegis/tui/app.py:182-260` (accept optional `manager=` kwarg; disable-plane branch)
- Test: `tests/cli/test_remote_flag.py`

**Interfaces:**
- Consumes: `WsClient`, `RemoteSessionManager` from prior tasks.
- Produces: `aegis --remote ws://host:port [--token TOK] [--tail N]` boots the TUI against the given daemon. `aegis --remote` (no URL) or `--remote ws://localhost:PORT` auto-launches a co-resident `aegis serve` in a subprocess if the port isn't already listening (zero-config guardrail). `AegisApp` gets a new optional `manager=` constructor parameter; when set, it is used as the AppBridge, and auxiliary planes on `AegisApp` are re-pointed at the manager's `_DisabledPlane` sentinels. TUI actions that would touch queue/canvas/terminal/group dashboards catch `RemoteUnsupportedError` and show `"<surface> not available in --remote v1"` in the pane's message area.

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_remote_flag.py
def test_remote_ws_url_parses_and_builds_remote_manager(monkeypatch):
    """--remote ws://... should construct RemoteSessionManager, not build
    a local SessionManager. AegisApp must never be constructed with a
    positional agents dict here — the remote branch supplies manager."""
    from aegis.cli import _build_remote_manager  # to be added
    monkeypatch.setattr("aegis.tui.ws_client.WsClient", _FakeWsClient)
    mgr = asyncio.run(_build_remote_manager(
        url="ws://localhost:8080", token="t", tail=10))
    assert mgr.__class__.__name__ == "RemoteSessionManager"


def test_remote_localhost_autolaunches_serve_if_port_free(monkeypatch,
                                                            tmp_path):
    """When --remote targets localhost and nothing is listening, spawn
    a background `aegis serve` subprocess before opening the WS."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    called: dict = {}
    monkeypatch.setattr("aegis.cli._maybe_autolaunch_serve",
                        lambda url: called.setdefault("url", url))
    monkeypatch.setattr("aegis.cli._build_remote_manager",
                        lambda **kw: _FakeManager())
    monkeypatch.setattr("aegis.tui.app.AegisApp.run", lambda self: None)
    from typer.testing import CliRunner
    r = CliRunner().invoke(app, ["--remote", "ws://localhost:8080"])
    assert r.exit_code == 0
    assert called["url"] == "ws://localhost:8080"


def test_remote_ws_remote_host_does_not_autolaunch(monkeypatch):
    """Non-localhost host must NOT trigger auto-launch."""
    called: dict = {}
    monkeypatch.setattr("aegis.cli._maybe_autolaunch_serve",
                        lambda url: called.setdefault("url", url))
    monkeypatch.setattr("aegis.cli._build_remote_manager",
                        lambda **kw: _FakeManager())
    monkeypatch.setattr("aegis.tui.app.AegisApp.run", lambda self: None)
    from typer.testing import CliRunner
    r = CliRunner().invoke(app, ["--remote", "ws://otherhost:8080",
                                  "--token", "t"])
    # _maybe_autolaunch_serve is called but is a no-op for non-localhost;
    # here we're just asserting the parse path routes correctly.
    assert r.exit_code == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/cli/test_remote_flag.py -v`
Expected: FAIL — `_build_remote_manager`, `_maybe_autolaunch_serve`, `--remote` option all absent.

- [ ] **Step 3: Add `--remote` / `--token` / `--tail` options and the two helpers**

In `src/aegis/cli.py`, extend the `run` callback signature:

```python
def run(
    ctx: typer.Context,
    version: bool = typer.Option(...),
    agent: str = typer.Option(None, "--agent", "-a"),
    cwd: str = typer.Option(".", "--cwd"),
    clean: bool = typer.Option(False, "--clean"),
    remote: str = typer.Option(
        None, "--remote",
        help="Run against a remote aegis serve. "
             "ws://host:port or ssh://host:port. "
             "Empty value = ws://localhost:8080."),
    token: str = typer.Option(
        None, "--token",
        help="Web token for --remote ws://. Ignored for ssh:// (fetched)."),
    tail: int = typer.Option(
        10, "--tail",
        help="On subscribe/resume, replay last N coalesced blocks."),
) -> None:
```

Add the branch (at the top of the function body, before the classic-path config loading):

```python
    if remote is not None:
        url = remote or "ws://localhost:8080"
        _maybe_autolaunch_serve(url)
        mgr = asyncio.run(_build_remote_manager(url=url, token=token,
                                                 tail=tail))
        _run_tui_with_manager(mgr, cwd=cwd, clean=clean, agent=agent)
        return
```

Add helpers:

```python
async def _build_remote_manager(*, url: str, token: str | None,
                                tail: int) -> "RemoteSessionManager":
    from aegis.tui.remote_manager import RemoteSessionManager
    from aegis.tui.ws_client import WsClient
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme != "ws":
        raise typer.BadParameter(
            f"unsupported scheme {parsed.scheme!r}; use ws://")
    if not token:
        raise typer.BadParameter(
            "--token is required for --remote ws://; "
            "for ssh:// the token is fetched automatically")
    ws = WsClient(url, token, default_tail=tail)
    await ws.connect()
    mgr = RemoteSessionManager(ws)
    await mgr.start()
    return mgr


def _maybe_autolaunch_serve(url: str) -> None:
    """If URL points at localhost and nothing is listening on the port,
    spawn a background `aegis serve` subprocess and wait until the WS port
    accepts connections (5s cap)."""
    from urllib.parse import urlparse
    import socket, subprocess, sys, time
    parsed = urlparse(url)
    if parsed.hostname not in ("localhost", "127.0.0.1"):
        return
    port = parsed.port or 8080
    with socket.socket() as probe:
        probe.settimeout(0.1)
        try:
            probe.connect(("127.0.0.1", port))
            return   # already listening
        except OSError:
            pass
    subprocess.Popen([sys.executable, "-m", "aegis", "serve"],
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,
                     start_new_session=True)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.1)
            try:
                probe.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.1)
    raise typer.Exit(f"aegis serve failed to start on port {port}")


def _run_tui_with_manager(mgr, *, cwd: str, clean: bool,
                           agent: str | None) -> None:
    """Launch AegisApp with an externally-built manager (--remote path)."""
    root = find_project_root() or Path.cwd()
    effective_cwd = str(root) if cwd == "." else cwd
    from aegis.drivers import DRIVERS
    drivers = {slug: cls() for slug, cls in DRIVERS.items()}
    agents = {slug: None for slug in mgr.list_agents()}  # names only
    AegisApp(agents=agents, default_agent=agent or "",
             make_session=None, mcp=None,
             queues={}, clean=clean, drivers=drivers,
             cwd=effective_cwd, voice=None,
             manager=mgr).run()
```

In `src/aegis/tui/app.py` `AegisApp.__init__`, add an optional `manager=None` kwarg. When provided, skip local plane construction (queue_manager/canvas_manager/terminal_manager) and instead point `self.queue_manager`, `self.canvas_manager`, `self.terminal_manager`, `self.groups`, `self.locks` at the manager's disabled-plane sentinels. Route `self.session_manager` / all spawn/close/interrupt paths through `manager` instead of the built-in adapters.

Wrap TUI actions that touch aux planes (queue dashboard `Ctrl+D`, canvas open, terminal spawn, group ops) with `try: … except RemoteUnsupportedError as e: self._show_banner(str(e))`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/cli/test_remote_flag.py tests/tui/ -v`
Expected: PASS. Classic-path tests must still pass — spot-check `tests/test_app_smoke.py` or equivalent.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/cli.py src/aegis/tui/app.py tests/cli/test_remote_flag.py
git commit -m "feat(cli,tui): --remote ws:// wiring + AegisApp manager injection"
```

---

### Task 9: `SSHTunnel` async context manager

**Files:**
- Create: `src/aegis/remote/ssh_tunnel.py`
- Test: `tests/remote/test_ssh_tunnel.py`

**Interfaces:**
- Consumes: `asyncio.create_subprocess_exec`, standard `socket` for TCP probe.
- Produces:

```python
class TunnelError(RuntimeError): ...

class SSHTunnel:
    def __init__(self, host: str, remote_port: int, *,
                 probe_timeout_s: float = 10.0) -> None: ...
    local_port: int   # populated after __aenter__
    async def __aenter__(self) -> "SSHTunnel": ...
    async def __aexit__(self, *exc) -> None: ...
```

Behaviour: opens a socket on `127.0.0.1:0` to reserve an ephemeral port, closes it, then spawns `ssh -L <local>:localhost:<remote_port> -N <host>`. Probes `127.0.0.1:<local>` every 100 ms up to `probe_timeout_s`. Raises `TunnelError` on timeout. `__aexit__` terminates the ssh subprocess.

- [ ] **Step 1: Write the failing test**

```python
# tests/remote/test_ssh_tunnel.py
@pytest.mark.asyncio
async def test_ssh_tunnel_picks_port_and_probes(monkeypatch):
    """Mock create_subprocess_exec + open a real loopback listener on the
    port that SSHTunnel picks, verify probe returns and __aexit__
    terminates."""
    calls: dict = {}

    class FakeProc:
        returncode = None
        async def wait(self): pass
        def terminate(self): calls["terminated"] = True

    async def fake_exec(*args, **kw):
        calls["argv"] = args
        # Start a real loopback listener on the picked local port
        # (extract it from `-L <local>:localhost:<remote>`).
        local = int(args[2].split(":")[0])
        srv = await asyncio.start_server(lambda r, w: None,
                                          "127.0.0.1", local)
        calls["srv"] = srv
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    from aegis.remote.ssh_tunnel import SSHTunnel
    async with SSHTunnel("vps", 8080) as t:
        assert t.local_port > 0
        assert "argv" in calls
        assert calls["argv"][0] == "ssh"
        assert "-L" in calls["argv"]
        assert "-N" in calls["argv"]
    assert calls.get("terminated") is True
    calls["srv"].close()


@pytest.mark.asyncio
async def test_ssh_tunnel_probe_timeout(monkeypatch):
    """If no listener ever appears, raise TunnelError within probe_timeout."""
    class FakeProc:
        returncode = None
        async def wait(self): pass
        def terminate(self): pass
    async def fake_exec(*a, **kw): return FakeProc()
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    from aegis.remote.ssh_tunnel import SSHTunnel, TunnelError
    with pytest.raises(TunnelError):
        async with SSHTunnel("nowhere", 9999, probe_timeout_s=0.3):
            pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/remote/test_ssh_tunnel.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `SSHTunnel`**

Create `src/aegis/remote/ssh_tunnel.py`:

```python
"""Async context manager wrapping `ssh -L` for the --remote ssh:// path.

Bind a random local port; spawn `ssh -L <local>:localhost:<remote> -N <host>`;
probe until TCP connect succeeds; teardown terminates the subprocess.
Fail fast — no retry — so bad SSH configs surface immediately.
"""
from __future__ import annotations

import asyncio
import socket


class TunnelError(RuntimeError):
    pass


class SSHTunnel:
    def __init__(self, host: str, remote_port: int, *,
                 probe_timeout_s: float = 10.0) -> None:
        self.host = host
        self.remote_port = remote_port
        self.probe_timeout_s = probe_timeout_s
        self.local_port: int = 0
        self._proc: asyncio.subprocess.Process | None = None

    async def __aenter__(self) -> "SSHTunnel":
        # Reserve an ephemeral local port then release it — ssh will re-bind.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            self.local_port = s.getsockname()[1]
        argv = ("ssh",
                "-L", f"{self.local_port}:localhost:{self.remote_port}",
                "-N", self.host)
        self._proc = await asyncio.create_subprocess_exec(*argv)
        await self._probe()
        return self

    async def _probe(self) -> None:
        deadline = asyncio.get_event_loop().time() + self.probe_timeout_s
        while asyncio.get_event_loop().time() < deadline:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", self.local_port),
                    timeout=0.5)
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(0.1)
        raise TunnelError(
            f"ssh tunnel to {self.host}:{self.remote_port} did not "
            f"become reachable on 127.0.0.1:{self.local_port} within "
            f"{self.probe_timeout_s}s")

    async def __aexit__(self, *exc) -> None:
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/remote/test_ssh_tunnel.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/remote/ssh_tunnel.py tests/remote/test_ssh_tunnel.py
git commit -m "feat(remote): SSHTunnel async context manager"
```

---

### Task 10: `ssh://host:port` URL scheme in `--remote`

**Files:**
- Modify: `src/aegis/cli.py` (extend `_build_remote_manager` + `_maybe_autolaunch_serve` bypass)
- Test: `tests/cli/test_remote_flag.py` (add cases)

**Interfaces:**
- Consumes: `SSHTunnel` (Task 9), `aegis token` subcommand (Task 3), `_build_remote_manager` (Task 8).
- Produces: `--remote ssh://host:port` (a) shells out to `ssh <host> aegis token`, captures the token, (b) opens `SSHTunnel(host, port)`, (c) hands `ws://localhost:<local_port>` + token to `WsClient`. Auto-launch is skipped (only makes sense for local `ws://`). Tunnel is kept alive for the TUI's lifetime via a caller-scoped async context; task 10 stashes it on the manager for `__aexit__` on shutdown.

- [ ] **Step 1: Write the failing test**

```python
def test_remote_ssh_fetches_token_and_opens_tunnel(monkeypatch):
    fetched: dict = {}

    class FakeTunnel:
        local_port = 41234
        async def __aenter__(self):
            fetched["opened"] = True
            return self
        async def __aexit__(self, *a): fetched["closed"] = True

    def fake_ssh_token(host):
        fetched["host"] = host
        return "server-token"

    monkeypatch.setattr("aegis.cli._ssh_fetch_token", fake_ssh_token)
    monkeypatch.setattr("aegis.remote.ssh_tunnel.SSHTunnel",
                        lambda host, port: FakeTunnel())

    async def fake_build(*, url, token, tail):
        fetched["url"] = url
        fetched["token"] = token
        return _FakeManager()

    monkeypatch.setattr("aegis.cli._build_remote_manager", fake_build)
    monkeypatch.setattr("aegis.tui.app.AegisApp.run", lambda self: None)

    from typer.testing import CliRunner
    r = CliRunner().invoke(app, ["--remote", "ssh://vps:8080"])
    assert r.exit_code == 0
    assert fetched["host"] == "vps"
    assert fetched["token"] == "server-token"
    assert fetched["url"] == "ws://localhost:41234"
    assert fetched["opened"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_remote_flag.py::test_remote_ssh_fetches_token_and_opens_tunnel -v`
Expected: FAIL.

- [ ] **Step 3: Extend the `--remote` branch**

Replace the `--remote` block from Task 8 with a scheme-aware version:

```python
    if remote is not None:
        url = remote or "ws://localhost:8080"
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme == "ssh":
            host = parsed.hostname
            port = parsed.port or 8080
            fetched_token = _ssh_fetch_token(host)
            from aegis.remote.ssh_tunnel import SSHTunnel
            tunnel = SSHTunnel(host, port)
            async def _boot():
                await tunnel.__aenter__()
                try:
                    mgr = await _build_remote_manager(
                        url=f"ws://localhost:{tunnel.local_port}",
                        token=fetched_token, tail=tail)
                    mgr._tunnel = tunnel   # keep alive for TUI lifetime
                    return mgr
                except Exception:
                    await tunnel.__aexit__(None, None, None)
                    raise
            mgr = asyncio.run(_boot())
        elif parsed.scheme == "ws":
            _maybe_autolaunch_serve(url)
            mgr = asyncio.run(_build_remote_manager(url=url, token=token,
                                                     tail=tail))
        else:
            raise typer.BadParameter(
                f"--remote: unsupported scheme {parsed.scheme!r}")
        _run_tui_with_manager(mgr, cwd=cwd, clean=clean, agent=agent)
        return


def _ssh_fetch_token(host: str) -> str:
    import subprocess
    r = subprocess.run(["ssh", host, "aegis", "token"],
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/cli/test_remote_flag.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/cli.py tests/cli/test_remote_flag.py
git commit -m "feat(cli): --remote ssh:// — fetch token + open SSHTunnel"
```

---

### Task 11: Reconnect status banner + `window_reset` pane clear

**Files:**
- Modify: `src/aegis/tui/widgets.py` (extend `StatusBar`)
- Modify: `src/aegis/tui/app.py` (wire `WsClient.on_connection` + `window_reset` handler when running under `RemoteSessionManager`)
- Test: `tests/tui/test_remote_status_banner.py`

**Interfaces:**
- Consumes: `WsClient.on_connection(bool)` (Task 6), stream `kind: "window_reset"` frames.
- Produces: `StatusBar.set_connection_state(up: bool, reason: str = "")` — when `up=False`, renders a right-aligned red "⚠ disconnected — reconnecting…"; when `up=True`, the indicator is cleared. Pane transcript reset: on `window_reset` for a subscribed handle, the corresponding `ConversationPane` clears its `_history` and re-mounts the incoming events.

- [ ] **Step 1: Write the failing test**

```python
def test_status_bar_shows_disconnected_banner_on_connection_drop():
    bar = StatusBar()
    bar.set_connection_state(False)
    assert "disconnected" in bar.render_plain().lower()
    bar.set_connection_state(True)
    assert "disconnected" not in bar.render_plain().lower()


@pytest.mark.asyncio
async def test_window_reset_clears_pane_transcript(remote_app_pilot):
    """A window_reset stream frame for handle 'h' clears the pane
    transcript before the fresh event flow arrives."""
    app = remote_app_pilot.app
    pane = app.pane_for("h")
    pane._history.append(("event", ...))   # seed
    app._ws.inject_stream("window_reset",
                          {"handle": "h", "dropped_through_seq": 10})
    await asyncio.sleep(0)
    assert pane._history == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_remote_status_banner.py -v`
Expected: FAIL — `set_connection_state` and `window_reset` handler missing.

- [ ] **Step 3: Implement banner + clear**

Extend `StatusBar` in `src/aegis/tui/widgets.py` with a `set_connection_state` method that toggles a right-aligned `Text` segment with the theme's `$warning` colour.

In `AegisApp` (only the branch that received a `manager=RemoteSessionManager`), after start:

```python
        self._ws.on_connection(
            lambda up: self.status_bar.set_connection_state(up))
        self._ws.on("window_reset", self._on_window_reset)

    def _on_window_reset(self, fr: dict) -> None:
        pane = self.pane_for(fr.get("handle", ""))
        if pane is not None:
            pane.clear_transcript()
```

Add `ConversationPane.clear_transcript()` that resets `_history` and re-mounts empty.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_remote_status_banner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/widgets.py src/aegis/tui/app.py src/aegis/tui/pane.py tests/tui/test_remote_status_banner.py
git commit -m "feat(tui): --remote disconnect banner + window_reset transcript clear"
```

---

### Task 12: Live smoke + AGENTS.md know-how entry

**Files:**
- Create: `repos/aegis/know-how/remote-tui.md`
- Modify: `repos/aegis/AGENTS.md` (add index line under `## Know-how`)
- Modify: `TASKS.md` (mark shipped, note deferred S9.3)
- Optionally: `tests/live/test_remote_tui_live.py` behind the `live` marker.

**Interfaces:**
- Consumes: everything from Tasks 1–11.
- Produces: a green loopback smoke on vps (`aegis --remote ssh://localhost:8080` targeting a co-resident serve) and a real cross-host smoke from zion (`aegis --remote ssh://vps:8080`), plus documentation.

- [ ] **Step 1: Loopback live smoke (on the working host)**

```bash
# Terminal A: start a serve
cd /home/apiad/Workspace/repos/aegis
uv run aegis serve
# Wait for "web UI on ..." (or confirm port 8080 is listening)

# Terminal B: run remote TUI against loopback SSH
uv run aegis --remote ssh://localhost:8080
```

Verify: TUI opens, `session_list` populates any existing sessions, `Ctrl+N` spawns via the daemon, transcripts render, `Escape` interrupts, closing/reopening the TUI shows the previous session in the tab list (session lived in the daemon). Kill terminal B mid-turn; reopen; verify banner appears, then clears with replayed tail.

- [ ] **Step 2: Real cross-host smoke (zion → vps)**

From zion (with corp-proxy setup active if applicable — see `CLAUDE.md`):

```bash
uv run aegis --remote ssh://vps:8080
```

Expected: the same behaviour as loopback, over the real SSH tunnel.

Record any deviations. Fix them if in-scope; file a follow-up if not.

- [ ] **Step 3: Write the know-how doc**

Create `repos/aegis/know-how/remote-tui.md` describing:
- when to reach for it (running the TUI against a remote or auto-launched daemon)
- the three schemes (`--remote` no arg, `--remote ws://…`, `--remote ssh://…`)
- auth handling (`aegis token` under the hood for `ssh://`)
- what's disabled in v1 (queue/canvas/terminal/group dashboards)
- known limitations + where to file follow-ups

- [ ] **Step 4: Add the AGENTS.md index line**

In `repos/aegis/AGENTS.md` under `## Know-how`, insert:

```
- `know-how/remote-tui.md` — *reach for it when running the TUI against a
  remote or auto-launched aegis serve (via `--remote ws://…` or
  `ssh://…`), or debugging the WS client / SSH tunnel path.*
```

- [ ] **Step 5: Update TASKS.md**

In `repos/aegis/TASKS.md`, mark **Web S9–S10** section as *"S9.0–S9.2 shipped 2026-07-XX as `aegis --remote` (conversation loop). Deferred: S9.3 (aux-surface RPCs) and S10 (default flip) — see follow-up plans."* Also strike the `2026-07-16-aegis-remote-tui-ssh-tunnel-design.md` from "Watching" / active if listed.

- [ ] **Step 6: Commit**

```bash
git add repos/aegis/know-how/remote-tui.md repos/aegis/AGENTS.md repos/aegis/TASKS.md
git commit -m "docs(aegis): --remote TUI know-how + AGENTS.md index"
```

- [ ] **Step 7 (optional): Add live smoke test**

`tests/live/test_remote_tui_live.py`, gated behind `pytest.mark.live` and auto-skipping unless a co-resident `aegis serve` is listening on 8080.

---

## Sequencing summary

Independent first wave (can run in parallel): **1, 2, 3, 4, 9**.
Second wave: **5** (needs 4), **6** (needs 4/5).
Third wave: **7** (needs 4, 5), **8** (needs 7).
Fourth wave: **10** (needs 3, 8, 9), **11** (needs 6, 8).
Fifth wave: **12** (needs 10, 11).

Under subagent-driven-development, the first-wave tasks can fan out immediately; the rest fold in as their dependencies land.

## Deferred (own follow-up plans)

- **S9.3 — auxiliary-surface RPCs** (group ops, terminals, canvas, workflow, scheduler) so the aux dashboards work under `--remote`.
- **S10 — flip `--remote` to default** (needs ≥1 week of daily use) + delete classic in-process code path.
- **MCP-plane relocation** from TUI process into `aegis serve` (structural cleanup called out in the S9 spec).
