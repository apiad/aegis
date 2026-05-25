# Remote plane

A **remote plane** lets one `aegis serve` enqueue work into another
`aegis serve` over HTTP. Brainstorm on the laptop, hand the expensive
implementation off to the VPS, and the local agent keeps moving while
the worker runs on the right machine. Telegram is the return channel.

The use case in one line: *"opus, handoff this research to
`vps:implementation` and ping me when done"* — and that just works.

## The two HTTP planes

Both ends run `aegis serve`. Each `aegis serve` already exposes one
HTTP plane — the **MCP plane**, loopback-bound, consumed by the
workers this serve spawned. The remote plane is a **second, distinct**
plane, bound to a tailnet IP, consumed by *other* aegis serves.

```
   ┌──────────────────────┐                ┌────────────────────────┐
   │ zion: aegis serve    │                │ vps: aegis serve       │
   │                      │                │                        │
   │  MCP plane (loop.)   │                │  MCP plane (loop.)     │
   │                      │   HTTP POST    │                        │
   │  Remote plane     ───┼───────────────▶│  Remote plane          │
   │  (tailnet IP)        │  tailnet IP    │  (tailnet IP)          │
   │                      │                │                        │
   │  QueueManager        │                │  QueueManager          │
   └──────────────────────┘                └────────────────────────┘
```

The two planes never overlap. Local workers can't reach the remote
plane (it's bound to a different address); remote callers can't reach
the MCP plane (it's loopback). One JSON endpoint each direction.

## The single endpoint

```
POST /remote/v1/enqueue
Content-Type: application/json
Body:
  {
    "queue":   "implementation",
    "payload": "<full prompt for the worker>",
    "from":    "zion"
  }
Response 200:
  {
    "task_id":         "01J...",
    "queued_position": 0
  }
Response 4xx/5xx:
  { "error": "<reason>" }
```

The remote `QueueManager` accepts the task as if it were locally
enqueued and records `enqueued_by="remote:<from>"` in its JSONL
lifecycle log. The worker runs on the remote machine's filesystem,
with the remote `.aegis.py`'s agent profiles.

## MCP surface

`aegis_enqueue` grew one optional parameter:

```python
aegis_enqueue(
    queue:       str,
    payload:     str,
    from_handle: str,
    callback:    bool         = True,
    target:      str | None   = None,   # ← new
) -> dict
```

- `target=None` (default): the existing local-enqueue path; nothing
  changes.
- `target="<name>"`: the substrate looks `<name>` up in the local
  `remotes` config and POSTs the body to that remote's
  `/remote/v1/enqueue`. The returned dict includes `"target":
  "<name>"` and a `"callback_note"` explaining that wire-level
  callbacks don't exist yet — the remote will Telegram on
  completion.

`callback` is ignored when `target` is set (the local aegis has no
inbox channel to deliver into from across the network in v1).

## Configuration

Two new top-level sections in `.aegis.yaml`. Both follow the same
inline-plus-overlay pattern as `agents`, `queues`, and `schedules`.

### Outbound — `remotes`

The list of remotes this serve is willing to call.

Inline in `.aegis.yaml`:

```yaml
remotes:
  vps:
    url: http://vps.tail-net.ts.net:8556
    # optional bearer token (otherwise tailnet trust only)
    token: "<secret>"
```

Or split into overlay files at `.aegis/remotes/<name>.yaml`:

```yaml
# .aegis/remotes/vps.yaml
url: http://vps.tail-net.ts.net:8556
token: "<secret>"
```

Same fail-loud rule as queues and schedules: a name appearing in both
inline and an overlay aborts boot.

`url` must include scheme and host. `http://` over a tailnet is fine —
WireGuard already encrypts. `https://` is permitted but adds no value
on a tailnet.

### Inbound — `remote_plane`

The opt-in section that turns on the receive side. Default off
(missing key or empty block).

```yaml
remote_plane:
  bind: 100.64.0.5:8556          # tailnet IP, explicit
  accept_tokens: []              # optional bearer-token allowlist
  accept_from: []                # optional source-IP allowlist
```

- `bind` is the address the inbound plane listens on. Binding to a
  specific tailnet IP is the recommended shape; `0.0.0.0` is
  permitted but warned at boot.
- `accept_tokens`: when non-empty, requests must present a matching
  `Authorization: Bearer <token>`. Empty list = tailnet trust only.
- `accept_from`: when non-empty, only requests from listed source IPs
  pass. Empty list = any source IP that reaches the port is accepted.

Gates compose with **AND**: if both are configured, the request must
satisfy both. Both empty is "anything that reaches the port is
trusted" — appropriate for a small personal tailnet, inadequate the
day a third device joins.

There is one `remote_plane` block per serve. No overlay folder for
this section (it's not a multi-entry table).

## Error model

All failure paths are loud and distinguishable. There is **no silent
fallback** to local enqueue — the whole point of `target=vps` is that
the work runs on vps; running it locally instead would defeat the
ask.

| Condition                              | What the tool returns                                         |
|----------------------------------------|---------------------------------------------------------------|
| `target` not in `remotes` config       | `{"error": "unknown target 'vps'"}`                           |
| Remote serve unreachable (TCP refused) | `{"error": "remote 'vps' unreachable: ..."}`                  |
| Remote serve timeout                   | `{"error": "remote 'vps' timed out"}`                         |
| Remote 401 (token mismatch)            | `{"error": "remote 'vps' rejected auth"}`                     |
| Remote 404 (queue unknown)             | `{"error": "remote 'vps': unknown queue 'X'"}`                |
| Remote 5xx                             | `{"error": "remote 'vps' failed: <body>"}`                    |

Timeouts in v1 are fixed: 5s connect, 10s read. No retries — fail
fast and let the calling agent decide what to do.

## Completion / return channel

In v1, completion does not return over the wire. The remote's
`QueueManager` runs the worker, the worker writes its result to its
own inbox channel, and on the VPS the existing "VPS-only progress
pings" convention (`bin/notify-telegram.sh`) surfaces the finish to
Alex.

The local aegis is **not** notified that the remote task completed.
The originating opus session is free to keep working or wind down;
the Telegram ping is what closes the loop, and Alex re-engages by
opening whatever artifact the remote produced (a commit, a vault
note, a PR).

A v2 might add `POST /remote/v1/callback` so completion lands in the
originating agent's inbox. The `/remote/v1/` namespace was chosen so
that addition is backwards-compatible.

## Security model

The trust anchor is the tailnet (Headscale / WireGuard). The remote
plane binds to a tailnet IP; reaching it requires being on the
tailnet, which requires a Headscale-issued preauth key. Same trust
model already in use for everything else on `vps.apiad.net`.

Two defense-in-depth knobs are available from day one:

- **Bearer tokens** (`accept_tokens` on the receiver, `token` on the
  caller). Use when the tailnet has untrusted devices.
- **IP allowlists** (`accept_from`). Use when you want only specific
  tailnet IPs to call this serve.

Tokens, if used, live in `.aegis.yaml` or its overlay files. Gitignore
the project's `.aegis.yaml` if you check in your repo and the token
isn't fetched from env at startup — same discipline as any other
secret-bearing config.

## Patterns

### Laptop brainstorm → VPS implementation

The original use case. A long research/implementation task gets
handed from a local opus session to the VPS, where it can run for
hours under whatever quota lives there.

```python
# In an opus session on zion (after brainstorming):
aegis_enqueue(
    queue="implementation",
    payload=(
        "Implement the design at "
        "vault/Atlas/Architecture/2026-05-25-aegis-remote-plane-design.md "
        "in repos/aegis. Use TDD. Push commits to main as you go. "
        "Update CHANGELOG when done."
    ),
    from_handle="lucid-knuth",
    target="vps",
)
# → {"task_id": "01J…", "queued_position": 0, "target": "vps",
#    "callback_note": "wire callbacks not yet implemented; remote will
#                      Telegram on completion"}
```

The local agent can wrap up the conversation. Alex's phone buzzes when
the VPS worker finishes.

### Cheap local → expensive remote

A free-tier local model that's fine for routing and clarification
hands hard subproblems off to a paid model running on the VPS. The
remote's `.aegis.py` configures `implementation` to use opus with
`full` permission; the local serve's `.aegis.py` doesn't even know
opus exists.

```python
# .aegis.py (zion) — local agents and the remote definition
agents = {
    "router": Agent(provider=ClaudeCode(model="haiku",
                                         permission="auto")),
}
default_agent = "router"

remotes = {
    "vps": {"url": "http://vps.tail-net.ts.net:8556"},
}
```

### Multiple machines in a tailnet

Three machines on the same tailnet — laptop, desktop, VPS — each
running `aegis serve`. Any agent can hand off to any of the other two:

```yaml
# .aegis.yaml on laptop
remotes:
  desktop: { url: http://100.64.0.3:8556 }
  vps:     { url: http://vps.tail-net.ts.net:8556 }
remote_plane:
  bind: 100.64.0.4:8556
```

The tailnet handles auth via WireGuard; no per-target tokens needed
until a fourth (less-trusted) device joins.

## File layout

```
src/aegis/remote/
  config.py       # RemoteSpec, RemotePlaneSpec dataclasses
  plane.py        # Starlette app + build_plane / run_plane_async
  client.py       # httpx client + remote_enqueue() + normalized errors
```

The `aegis_enqueue` MCP tool routes to `remote.client.remote_enqueue`
when `target=` is set; `cli.serve()` mounts the plane via
`build_plane(queue_manager, cfg.remote_plane)` when `remote_plane` is
configured.

## Future extensions (not built)

These are noted to confirm the `/remote/v1/` namespace leaves room
for them without breaking changes:

- **Wire-level completion callbacks** — `POST /remote/v1/callback`;
  symmetric design where both ends run the plane.
- **Status query** — `GET /remote/v1/task/<id>`.
- **Cancel** — `POST /remote/v1/task/<id>/cancel`.
- **Cross-host `aegis_handoff`** — handoff to a live remote handle.
  Semantically harder than enqueue; deferred until the use case
  appears.
