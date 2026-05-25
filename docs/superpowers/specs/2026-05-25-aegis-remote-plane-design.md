---
title: Aegis Remote Plane — Server-to-Server Enqueue
date: 2026-05-25
status: draft
---

# Aegis Remote Plane — Server-to-Server Enqueue

## Motivation

Brainstorming, design, and short exploratory work happen on Alex's laptop
(`zion`). Expensive work — multi-hour research, implementation, testing —
should run on the VPS (`vps`). Today there is no first-class path between
the two aegis instances: handoffs go through GitHub round-trips or the
legacy `vault/+/jobs/` substrate (which is being retired in favor of the
aegis scheduler).

The use case in Alex's words: *"agent opus, perfect, handoff research to
`vps:implementation` queue and ping me when done, and that just works."*

Concretely: a local agent calls `aegis_enqueue` with a remote target; the
local `aegis serve` sends one HTTP request to the remote `aegis serve`;
the remote enqueues into its own `QueueManager` and runs the worker on
its own filesystem; when the worker completes, it pings Alex via
Telegram. No shell SSH, no GitHub round-trip, no `vault/+/jobs/` file.

## Non-goals (explicit)

These were considered and dropped:

- **Federated TUI / multi-attach.** A TUI that attaches to multiple
  aegis servers and shows `vps:planner` next to `local:coder` in one
  pane is a separate, much larger feature (requires splitting TUI from
  server, defining an IPC protocol, and rebuilding reactive state as
  proxies). The use case above does not need it — Telegram is the
  return channel; no need to *watch* the remote agent work live.
- **SSH transport / shell-out.** Earlier drafts proposed
  `ssh vps aegis enqueue …`. Rejected: the contract becomes "what string
  did you pipe to which shell" instead of a wire protocol; subprocess
  per call; no clean error model; no path to future operations like
  status query or wire-level callbacks.
- **Wire-level completion callbacks (v1).** In v1, the remote pings
  Alex via Telegram when work completes. There is no return channel
  from remote aegis to local aegis. v2 may add a symmetric
  `POST /remote/v1/callback` endpoint so completion lands in the
  originating agent's inbox — explicitly out of scope here.
- **Cross-host `aegis_handoff` / live inbox routing.** Only
  `aegis_enqueue` crosses the boundary in v1. Handoff stays
  within a single server.
- **Streaming / persistent connections.** One HTTP request per
  enqueue. No WebSocket, no SSE.
- **A new CLI `aegis enqueue` command.** The MCP tool is the
  interface. No new shell entrypoint.

## Architecture

Both ends run `aegis serve`. Each `aegis serve` already runs an HTTP
server for the local MCP plane (`src/aegis/mcp/runtime.py`, bound to
loopback for local claude workers). This design adds a **second, distinct
HTTP plane** — the *remote plane* — that other aegis instances talk to.

```
   ┌──────────────────────┐                ┌────────────────────────┐
   │ zion: aegis serve    │                │ vps: aegis serve       │
   │                      │                │                        │
   │  ┌────────────────┐  │                │  ┌─────────────────┐   │
   │  │ MCP plane      │  │                │  │ MCP plane       │   │
   │  │ (loopback)     │  │                │  │ (loopback)      │   │
   │  └────────────────┘  │                │  └─────────────────┘   │
   │                      │   HTTP POST    │                        │
   │  ┌────────────────┐  │ ──────────────▶│  ┌─────────────────┐   │
   │  │ Remote plane   │  │  tailnet IP    │  │ Remote plane    │   │
   │  │ (tailnet IP)   │◀ │ ────────────── │  │ (tailnet IP)    │   │
   │  └────────────────┘  │   (v2 only)    │  └─────────────────┘   │
   │                      │                │                        │
   │  QueueManager        │                │  QueueManager          │
   │  InboxRouter         │                │  InboxRouter           │
   └──────────────────────┘                └────────────────────────┘
```

The two planes are kept distinct:

- **MCP plane:** loopback, no auth, FastMCP HTTP, consumed by claude
  workers that this serve has spawned. Unchanged by this design.
- **Remote plane:** tailnet-bound, separate FastAPI/Starlette app,
  narrow surface, consumed by *other* aegis serves.

### The single endpoint (v1)

```
POST /remote/v1/enqueue
Content-Type: application/json
Body:
  {
    "queue":   "implementation",
    "payload": "<full prompt for the worker>",
    "from":    "zion"             // free-form sender label, for audit
  }
Response 200:
  {
    "task_id":         "01J...",
    "queued_position": 0
  }
Response 4xx/5xx:
  {
    "error": "<human-readable reason>"
  }
```

Semantics mirror the existing in-process `aegis_enqueue` MCP tool:
unknown queue → 404, callback always `false` on the remote side (v1
has no wire callback channel), `enqueued_by` recorded as
`f"remote:{from}"` for audit in the queue's JSONL lifecycle log.

## Configuration

### Outbound — `remotes` (caller side)

In `.aegis.py`:

```python
remotes = {
    "vps": {
        "url":   "http://vps.tail-net.ts.net:8556",
        # optional:
        # "token": "<bearer>",
    },
}
```

- `url` is the full base URL of the remote plane. Scheme `http` is
  fine over the tailnet (WireGuard already encrypts); `https` is
  permitted but adds no value on a tailnet.
- `token`, if present, is sent as `Authorization: Bearer <token>`.

Unknown target names referenced from `aegis_enqueue(target=…)` fail
loud at the tool-call boundary; no fallback to "try as a URL,"
no implicit SSH alias resolution.

### Inbound — `remote_plane` (callee side)

In `.aegis.py`:

```python
remote_plane = {
    "bind":          "100.64.0.x:8556",   # tailnet IP, explicit
    "accept_tokens": [],                  # optional allowlist
    "accept_from":   [],                  # optional IP allowlist
}
```

- `bind` is the address the remote plane listens on. Default off
  (key absent) — opt-in only. Binding to `0.0.0.0` is permitted but
  warned about at boot.
- `accept_tokens`: when non-empty, requests must present a matching
  bearer token. Empty list (or key absent) means tailnet-trust only.
- `accept_from`: when non-empty, only requests from listed source IPs
  are accepted; useful when the tailnet has devices you don't fully
  trust.

Gates compose with AND: if both are configured, the request must
satisfy both. Both empty = "anything that reaches the port is
trusted." That is appropriate for a personal two-device tailnet
today, and inadequate the day a third device joins.

## MCP tool change

`aegis_enqueue` grows an optional `target` parameter:

```python
async def aegis_enqueue(
    queue:       str,
    payload:     str,
    from_handle: str,
    callback:    bool         = True,
    target:      str | None   = None,
) -> dict:
```

Behavior:

- `target=None` (existing path): enqueue on local QueueManager.
- `target="vps"` (or any key in `remotes`):
  - Look up `remotes[target]`.
  - POST to `<url>/remote/v1/enqueue` with body `{queue, payload, from: <local-handle>}`.
  - If `token` configured, set `Authorization`.
  - On 200: return `{"task_id": ..., "queued_position": ..., "target": "vps"}` to the agent.
  - On any error (HTTP non-2xx, timeout, unreachable, unknown target):
    return `{"error": "..."}` with a clear, distinct reason. No
    silent fallback to local enqueue.
  - `callback` is ignored when `target` is set (v1 has no wire
    callback). The returned dict includes
    `"callback_note": "wire callbacks not yet implemented; remote
    will Telegram on completion"` so the calling agent sees it
    explicitly rather than assuming local callback semantics.

The local agent's MCP system-prompt primer (`PRIMING`) gains one
sentence describing `target=` and the Telegram-as-return-channel
contract, so opus knows when to reach for it.

## Failure modes

| Condition                              | Surface                                                       |
|----------------------------------------|---------------------------------------------------------------|
| `target` not in `remotes` config       | tool returns `{"error": "unknown target 'vps'"}`              |
| Remote serve unreachable (TCP refused) | tool returns `{"error": "remote 'vps' unreachable: ..."}`     |
| Remote serve timeout                   | tool returns `{"error": "remote 'vps' timed out"}`            |
| Remote 401 (token mismatch)            | tool returns `{"error": "remote 'vps' rejected auth"}`        |
| Remote 404 (queue unknown)             | tool returns `{"error": "remote 'vps': unknown queue 'X'"}`   |
| Remote 5xx                             | tool returns `{"error": "remote 'vps' failed: <body>"}`       |

All failure paths are loud, distinguishable, and do **not** fall back
to local enqueue. The whole point of "handoff to vps" is that the
work runs there; silently running it locally would defeat that.

## Completion / return channel

In v1, the *remote* fires Telegram on completion. This already happens
for jobs enqueued through the existing MCP `aegis_enqueue` when the
remote queue's worker concludes — the worker writes its result to the
inbox of the originating handle, and on the VPS the existing
"VPS-only progress pings" convention (`bin/notify-telegram.sh`,
documented in `Workspace/CLAUDE.md`) is what surfaces it to Alex.

The local aegis is **not** notified over the wire. It does not learn
when the remote task finishes. The originating opus session is free to
continue (or end); Alex will see a Telegram ping when the work is
done and can re-engage by opening whatever artifact the remote produced
(commit, PR, vault note).

This is "v1 / minimum viable." v2 may add `POST /remote/v1/callback`
to land completion in the originating agent's inbox; see Future
extensions.

## Security model

The trust anchor is Headscale / WireGuard. The remote plane binds to a
tailnet IP; reaching it requires being on the tailnet, which requires
a Headscale-issued preauth key. This is the same trust model already in
use for everything else on vps.apiad.net.

Defense-in-depth knobs available from day one (optional):

- `accept_tokens`: bearer token gate at the HTTP layer.
- `accept_from`: source-IP allowlist (specific tailnet IPs only).

Threat: if any tailnet device is compromised, it can enqueue work into
any aegis on the tailnet. Mitigations beyond v1 (not built now):

- Per-queue ACLs (only certain remotes can enqueue into certain queues).
- Per-target tokens with separate secrets per origin.
- Audit log of every remote enqueue (already covered by the queue's
  JSONL lifecycle log, with `enqueued_by="remote:<from>"`).

Operational note: tokens, if used, live in `.aegis.py`. `.aegis.py` must
remain gitignored (already the case in the workspace topology).

## Implementation sketch

New module `src/aegis/remote/`:

- `plane.py` — FastAPI/Starlette app exposing `/remote/v1/enqueue`.
  Binds per `remote_plane.bind`. Mounts on its own port, separate
  from the MCP runtime. Validates auth (tokens / IP allowlist).
  Delegates to the local `QueueManager.enqueue` after recording
  `enqueued_by="remote:<from>"`.
- `client.py` — thin httpx client used by the MCP tool when `target=`
  is set. Owns retry policy (none in v1 — fail fast), timeouts
  (fixed in v1: 5s connect, 10s read; not configurable), and error
  normalization.
- `config.py` — pydantic models for `remotes` and `remote_plane`
  blocks in `.aegis.py`. Validation: URLs well-formed, target names
  unique, bind addresses parseable.

Existing modules touched:

- `src/aegis/cli.py:serve` — start `remote.plane` alongside the
  existing MCP runtime if `remote_plane.bind` is configured.
- `src/aegis/mcp/server.py:aegis_enqueue` — grow `target` param,
  route to `remote.client` when set.
- `src/aegis/config/__init__.py` — wire `remotes` and `remote_plane`
  into the loaded config.

State changes: none beyond what the existing QueueManager already
records. The remote enqueue lands as a normal task in the receiver's
queue JSONL with `enqueued_by="remote:<from>"`.

## Testing

- **Unit:** config validation, client error normalization, plane
  auth checks (token mismatch, IP not allowlisted, both gates
  combined).
- **Integration (hermetic):** spin up two `aegis serve` instances in
  the same test process, on different loopback ports, configure one
  as the other's remote, fire an `aegis_enqueue` with `target=`,
  assert the second's QueueManager received it with correct
  `enqueued_by`.
- **Live (`@pytest.mark.live`):** an opt-in test that hits the VPS
  remote plane from zion (Alex-driven, not CI).

The `live` marker convention (registered in `pyproject.toml`,
skipped by default) is exactly the right place for the cross-host
roundtrip test.

## Future extensions (explicit, not built)

- **Wire-level completion callbacks** — `POST /remote/v1/callback`
  endpoint on the *originating* aegis; remote calls back when the
  task finishes. Symmetric design: both aegises run the remote plane,
  and `remotes` config maps both ways. Requires the local aegis to
  expose the remote plane too (off by default in v1).
- **Status query** — `GET /remote/v1/task/<id>`; lets a local agent
  poll a remote task.
- **Cancel** — `POST /remote/v1/task/<id>/cancel`.
- **Cross-host `aegis_handoff`** — semantically harder than enqueue
  because the target is a *live* handle on the remote side. Deferred
  until / unless the use case appears.
- **TUI multi-attach** — separate, much larger spec.

These are noted to confirm the v1 API namespace (`/remote/v1/`) leaves
room for them without breaking changes.

## Open questions

1. **Default port.** The example uses `8556`. Should the remote plane
   default to a fixed port, or pick like the MCP runtime does and
   write to a state file? Fixed is easier to configure; pick-based
   is friendlier for "two aegises on the same box during testing."
   Lean fixed (8556) with override.

2. **Should `target` be on the MCP tool, or a `vps:queue` syntax in
   the `queue` field?** This spec uses `target=`; `vps:queue` is
   debatable. Keeping `target=` because it forces the agent to be
   explicit about "this is remote" and keeps queue names from
   colliding with target names.

3. **Telegram ping detail on remote side.** Today's
   `notify-telegram.sh` workflow on vps fires for all queue
   completions. Do we want a per-enqueue flag (`notify=True/False`)
   so noisy enqueues can stay quiet? Probably yes, but it's a remote
   QueueManager feature, not a remote-plane feature; out of scope
   here.
