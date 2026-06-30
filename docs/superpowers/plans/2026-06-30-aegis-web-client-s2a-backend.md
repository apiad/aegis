# Aegis Web Client — S2a (Backend WS Server) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. The wire contract this plan implements is fixed in `docs/superpowers/specs/2026-06-30-aegis-web-ws-protocol-design.md` — read it first.

**Goal:** Stand up the headless half of the single-tab web client — a Starlette WebSocket server (`src/aegis/web/`) that speaks the S2 protocol against the real `SessionManager`, mounted into `aegis serve`. Fully testable without a browser: Starlette's WS test client + a fake `SessionManager` drive the entire protocol.

**Architecture:** A `WSSession` runs the protocol over an abstract transport (`send_json`/`receive_json`/`close` — Starlette's `WebSocket` satisfies it directly; tests use an in-memory fake). A `SubscriptionRegistry` owns one set of per-handle `AgentSession` observers (attached lazily, reference-counted across windows) and a per-handle monotonic `seq` counter; it fans live events out to subscribed `WSSession` sinks. `read_history` replays JSONL with synthesized `seq` and torn-trailing-line tolerance (S1 audit). A `WebFrontend` (sibling to `TelegramFrontend`) owns the uvicorn lifecycle and is mounted in `cli.py`'s serve path gated on `config.web`.

**Tech Stack:** Python 3.13+, Starlette + uvicorn (already deps — the remote plane uses them), `uv`, `pytest`. **No new dependencies** (no FastAPI, no Jinja in S2a).

## Global Constraints

- Python **3.13+**; deps/tests via **`uv`**: `uv run pytest -q -m "not live"`.
- **TDD**: failing test first → minimal impl → commit per task.
- **No new dependencies.** Build on `starlette` + `uvicorn` (present). Match the house idiom in `src/aegis/remote/plane.py` (raw Starlette `Route`/`JSONResponse`, app built by a `build_*` function, uvicorn driven by the caller).
- **`handle` is the session identifier** (not `session_id`) — per the WS spec's grounding correction.
- The protocol contract — frames, RPC list, stream kinds, resume semantics, error codes — is **normative in the WS spec**; this plan implements it, it does not redefine it.
- Commit straight to **main**. Conventional commits.

## Real backend surface (verified against main)

- `SessionManager`: `async spawn(profile, *, handle=None) -> str`, `async close(handle)`, `async interrupt(handle)`, `get(handle) -> AgentSession | None`, `list_sessions() -> list[SessionInfo]`, `list_agents() -> list[str]`.
- `AgentSession`: `add_event_observer(cb)`, `add_state_observer(cb)`, `add_inbox_observer(cb)`, `add_close_observer(cb)`; `async deliver(msg: InboxMessage) -> Delivery`; `.handle`, `.state: AgentState`.
- Observer callback signatures: `on_event(core, ev)`, `on_state(core, state: AgentState, finished: bool)`, `on_inbox(core, msg: InboxMessage)`, `on_close(core, reason)`.
- `SessionInfo(handle, agent_slug, state, active, unseen)`.
- `InboxMessage(sender, timestamp, body, task_id=None, status=None)`; `sender_user() -> str`; `now_iso() -> str` (from `aegis.queue`).
- `Delivery(disposition: "landed"|"queued", depth: int)`.
- `encode_event(ev) -> dict` / `decode_event(d) -> Event` (`aegis.state.event_codec`).
- `session_log.session_log_path(state_dir, handle)`; the JSONL line shape is `{"v":1,"aegis_ts":<iso>,"event":<encoded>}`.
- `render_event_html(ev) -> str | None` (S1).
- `aegis serve` lives in `cli.py` (~L220–305); each frontend runs as `asyncio.create_task(fe.run(...))`, gated on its config block.

---

## File Structure

**New package `src/aegis/web/`:**
- `__init__.py`
- `history.py` — `read_history(state_dir, handle) -> list[tuple[int, Event]]`.
- `subscriptions.py` — `SubscriptionRegistry`.
- `wssession.py` — `WSSession`, `WSTransport` Protocol.
- `server.py` — `build_web_app(manager, web_cfg) -> Starlette` (WS route + static mount placeholder + token auth).
- `frontend.py` — `WebFrontend` (uvicorn lifecycle; mirrors `TelegramFrontend`).

**Modified:**
- `src/aegis/config/yaml_loader.py` + `src/aegis/config/__init__.py` — parse `web:` block into a `WebConfig`.
- `src/aegis/cli.py` — mount `WebFrontend` in the serve path when `config.web` is set.

**Tests:**
- `tests/test_web_history.py`, `tests/test_web_subscriptions.py`, `tests/test_web_protocol.py`, `tests/test_web_server.py`, `tests/test_web_config.py`.

---

### Task 1: `web:` config block → `WebConfig`

**Files:** Modify `src/aegis/config/__init__.py` (add `WebConfig` dataclass + expose on the parsed config), `src/aegis/config/yaml_loader.py` (parse the block). Test: `tests/test_web_config.py`.

**Interfaces — Produces:**
- `WebConfig(token: str | None = None, bind: str = "127.0.0.1", port: int | None = None)`.
- The loaded `AegisConfig` exposes `.web: WebConfig | None` (None when no `web:` block).

**Acceptance:** a `.aegis.yaml` with
```yaml
web:
  token: "abc"
  bind: "127.0.0.1"
  port: 8765
```
parses to `WebConfig(token="abc", bind="127.0.0.1", port=8765)`; absence → `.web is None`; `AEGIS_WEB_TOKEN` env overrides `web.token` when set (mirrors the Telegram token precedent).

TDD: write `tests/test_web_config.py` (parse present/absent/env-override) → fail → implement parsing modeled on the existing `telegram:` block in `yaml_loader.py` → pass → commit.

---

### Task 2: `read_history` — torn-tolerant JSONL reader

**Files:** Create `src/aegis/web/history.py`. Test: `tests/test_web_history.py`.

**Interfaces — Produces:**
- `read_history(state_dir: Path, handle: str) -> list[tuple[int, Event]]` — returns `(seq, event)` pairs with `seq` the 1-based line index. Missing file → `[]`. A torn/unparseable **trailing** line is dropped; an unparseable **interior** line raises `ValueError` (genuine corruption).

**Key logic (S1 audit-driven):**
```python
def read_history(state_dir, handle):
    p = session_log_path(state_dir, handle)
    if not p.exists():
        return []
    raw = p.read_text(encoding="utf-8").splitlines()
    out = []
    for i, line in enumerate(raw):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            ev = decode_event(rec["event"])
        except Exception:
            if i == len(raw) - 1:        # torn trailing write — tolerate
                break
            raise ValueError(f"corrupt interior line {i+1} in {p}")
        out.append((i + 1, ev))
    return out
```

**Tests:** happy path (3 events → seq 1,2,3); empty/missing file → `[]`; blank lines skipped; torn trailing line dropped (no raise); corrupt interior line raises `ValueError`. Build fixtures by writing JSONL with `append_event` then hand-appending a truncated line.

TDD per the WS spec's test contract → commit.

---

### Task 3: `SubscriptionRegistry` — per-handle observers, seq, fan-out

**Files:** Create `src/aegis/web/subscriptions.py`. Test: `tests/test_web_subscriptions.py`.

**Interfaces — Produces:**
- `SubscriptionRegistry(manager, state_dir)`.
- `async subscribe(handle: str, sink: Sink) -> int` — attach `sink` to `handle`; on the first subscriber, attach observers to the `AgentSession` and initialise the per-handle live `seq` counter. Returns `current_seq` (the history line count at attach time). **Attach-before-read** to avoid the subscribe race: register observers first (buffering live events into the seq stream), then the caller reads history via `read_history`; any live event arriving during/after attach gets `seq = history_count + k`. Because both the JSONL append and the observer fire from the same `AgentSession` turn loop, initialise the counter to `len(read_history(...))` captured **after** attach and reconcile (documented inline; single-user race window is negligible but handled by counting from the post-attach snapshot).
- `unsubscribe(handle, sink)` — drop `sink`; detach observers when the last sink leaves.
- `Sink` = a callable `(frame: dict) -> None` the `WSSession` provides; the registry calls it with fully-formed `stream/*` frames (event/state/inbox). Backpressure (queue cap) lives in the sink (Task 4/WSSession), not the registry.

**Live event → frame:** on `on_event(core, ev)` the registry increments the handle's seq, builds
`{"type":"stream","kind":"event","handle":h,"seq":n,"event_type":type(ev).__name__,"event":encode_event(ev),"html":render_event_html(ev)}`
and calls every sink. `on_state` → `{"type":"stream","kind":"state","handle":h,"state":state.value,"metrics":<serialized>}` (no seq). `on_inbox` → `{"type":"stream","kind":"inbox","handle":h,"seq":n,"msg":<serialized InboxMessage>}`.

**Tests (fake AgentSession exposing the four `add_*_observer` seams + a way to emit):** first subscribe attaches exactly one set of observers; second subscribe on same handle does **not** re-attach; emitting an event fans out to all sinks with monotonic seq; unsubscribe of last sink detaches; state/inbox frames shaped correctly.

TDD → commit.

---

### Task 4: `WSSession` — the protocol handler

**Files:** Create `src/aegis/web/wssession.py`. Test: `tests/test_web_protocol.py`.

**Interfaces — Produces:**
- `class WSTransport(Protocol)`: `async send_json(obj: dict) -> None`, `async receive_json() -> dict`, `async close(code: int = 1000, reason: str = "") -> None`.
- `WSSession(transport, manager, registry, web_cfg, constants: dict)`.
- `async run() -> None` — the full lifecycle: auth handshake → hello → frame loop (rpc / subscribe / unsubscribe / resume) until disconnect. Owns a bounded send queue (default 10k) feeding `transport.send_json`; overflow → close with reason `backpressure`.

**Behaviour (normative details in the WS spec):**
- **auth:** first frame must be `{type:"auth", token}` matching `web_cfg.token`; else `transport.close(4401)`. 5s timeout → close 4401. On success send `hello` (server_version, protocol_version=1, `constants`, `supported_kinds`).
- **rpc dispatch** → real manager calls, each wrapped to emit `rpc_response{id, ok, result|error}`:
  - `list_agents` → `{agents: manager.list_agents()}`
  - `list_sessions` → `{sessions: [asdict(si) for si in manager.list_sessions()]}`
  - `spawn_session{agent_profile}` → `{handle: await manager.spawn(agent_profile)}`
  - `close_session{handle}` → `await manager.close(handle); {ok:true}`
  - `interrupt_session{handle}` → `await manager.interrupt(handle); {ok:true}`
  - `deliver{handle, message}` → build `InboxMessage(sender=sender_user(), timestamp=now_iso(), body=message)`, `d = await manager.get(handle).deliver(msg)` → `{delivery:d.disposition, depth:d.depth}`. Unknown handle → `rpc_response{ok:false, error:"unknown handle"}`.
  - unknown method → `error{code:"unknown_method", id}`.
- **subscribe{target}:** for `kind:"session"` — register a sink with the registry (`await registry.subscribe(handle, sink)` returns `current_seq`), stream `read_history` events as `stream/event` frames (seq 1..N), then `stream/history_complete{handle, current_seq:N}`. Live frames already flow via the sink. For `kind:"global", stream:"session_list"` — register on a global sink (S2a: minimal — push a `session_list` snapshot now; full add/removed/updated deltas can stay coarse in S2a and refine in S3).
- **resume{subscriptions:[{handle,last_seq}], globals}:** per handle — if `current_seq - last_seq <= RESUME_GAP_CAP`, stream only `last_seq+1..current_seq` from `read_history`; else send `stream/window_reset{handle, dropped_through_seq:last_seq}` then full history. Then live resumes via sink.
- **bad/typeless frame** → `error{code:"bad_frame"}`.

**Tests (in-memory `FakeTransport` queue pair + a fake `SessionManager`/`AgentSession`):** the WS spec's full test contract — auth ok→hello; bad token→close 4401; the 6 rpc methods; subscribe→history(seq 1..N)→history_complete→live event at N+1; resume small gap (only tail); resume large gap→window_reset→full; deliver records the `sender_user` message and returns the `Delivery` shape; backpressure overflow→close `backpressure`; unknown method→error frame.

TDD, building the test list incrementally (one assert-group per step) → commit.

---

### Task 5: `build_web_app` + `WebFrontend` + serve wiring

**Files:** Create `src/aegis/web/server.py`, `src/aegis/web/frontend.py`. Modify `src/aegis/cli.py`. Test: `tests/test_web_server.py`.

**Interfaces — Produces:**
- `build_web_app(manager, web_cfg, *, static_dir: Path | None = None) -> Starlette` — a Starlette app with:
  - `WebSocket` route `/ws` whose handler accepts the socket, adapts it to `WSTransport` (Starlette's `WebSocket.send_json/receive_json/close` already match), constructs a `WSSession` (sharing one process-wide `SubscriptionRegistry`), and `await session.run()`. The `?t=` query token is read here only to fail fast on obviously-missing auth; the authoritative check is the `auth` frame.
  - A `StaticFiles` mount at `/static` when `static_dir` is provided (S2b supplies it; S2a passes `None` and the route is simply absent).
  - `GET /healthz` → `JSONResponse({"ok": true})` (smoke target).
- `WebFrontend(manager, web_cfg, *, state_dir)` — mirrors `TelegramFrontend`: builds the app, owns a `uvicorn.Server` bound to `web_cfg.bind`/`port` (auto-pick a free port when `port is None`, persisted to `.aegis/state/web.port`), `async run()` serves until cancelled, exposes the resolved `.url`.

**Serve wiring (`cli.py`):** after the Telegram block, add — gated on the parsed `config.web`:
```python
if web_cfg is not None:
    from aegis.web.frontend import WebFrontend
    web_fe = WebFrontend(mgr, web_cfg, state_dir=_state_dir(root))
    tasks.append(asyncio.create_task(web_fe.run()))
```

**Tests:** `tests/test_web_server.py` uses Starlette's `TestClient` —
- `GET /healthz` → 200 `{"ok": true}`.
- `with client.websocket_connect("/ws?t=secret") as ws:` → send `auth` → receive `hello` with the constants block; `spawn_session` rpc against a small real or fake `SessionManager` → `rpc_response` with a handle; subscribe → `history_complete`.
- bad token → connection closed (assert `WebSocketDisconnect` / close code).

This is the end-to-end headless gate: a WS client drives spawn → subscribe → history over the real Starlette transport.

TDD → commit.

---

## Final verification

- [ ] `uv run pytest tests/test_web_*.py -q` → all green.
- [ ] `uv run pytest -q -m "not live"` → no regressions (note the known load-sensitive `test_pane_windowing` flake; confirm in isolation if it trips).
- [ ] `uv run python -c "import aegis.web.server, aegis.web.frontend, aegis.web.wssession; print('ok')"`.
- [ ] Manual: `aegis serve` with a `web:` block logs the bound URL and `GET /healthz` returns ok.

## Self-Review

**WS-spec coverage:** auth/hello (T4), 6 RPC methods (T4), stream event/state/inbox (T3), subscribe+history+history_complete (T3/T4), resume small/large+window_reset (T4), history reader torn-tolerance (T2), backpressure (T4), config/token (T1), Starlette mount + serve wiring (T5). The JS client, `aegis web` CLI, theme stylesheet, and live browser smoke are **S2b** (next plan), not here.

**Deferred/coarse in S2a (documented, not silent):** `session_list` global stream ships a coarse snapshot in S2a and gains proper add/removed/updated deltas in S3; the subscribe-race seq reconciliation uses a post-attach history snapshot (single-user window negligible).

**No new deps; matches the remote-plane Starlette idiom.**
