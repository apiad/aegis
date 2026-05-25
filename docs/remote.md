# Remote plane

A **remote plane** lets one `aegis serve` enqueue work into another
`aegis serve` over HTTP. One agent on one machine can hand a task off
to another machine — typically because the work is long-running, needs
different hardware, or should run under a different agent profile —
while the calling agent keeps moving.

By default the call is fire-and-forget: the receiver runs the worker
on its own queue under its own config and the calling agent is not
notified over the wire. As of v0.8.0 the caller can **opt in to a
wire callback** that delivers the worker's final message back to the
originating agent's inbox once the remote task terminates. See
[Callbacks](#callbacks) below.

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
    callback:    bool | None  = None,   # default depends on target (see below)
    target:      str | None   = None,
) -> dict
```

- `target=None` (default): the existing local-enqueue path. `callback`
  defaults to **True** — worker's final result is routed into the
  caller's inbox.
- `target="<name>"`: the substrate looks `<name>` up in the local
  `remotes` config and POSTs the body to that remote's
  `/remote/v1/enqueue`. `callback` defaults to **False** (v0.7
  fire-and-forget compatibility). Pass `callback=True` explicitly to
  request a wire-level callback — see [Callbacks](#callbacks).

`aegis_enqueue` with `target` set and explicit `callback=True` will
loudly reject the call when any of the following is missing:

- `remote_plane.peer_name` on the caller's serve;
- `remotes[<target>].peer_name` on the caller's serve;
- `remote_plane` block on the caller's serve at all.

There is no silent fallback to fire-and-forget; if you ask for a
callback and the symmetric-peer config isn't complete, the substrate
tells you so.

## Configuration

Two new top-level sections in `.aegis.yaml`. Both follow the same
inline-plus-overlay pattern as `agents`, `queues`, and `schedules`.

### Outbound — `remotes`

The list of remotes this serve is willing to call.

Inline in `.aegis.yaml`:

```yaml
remotes:
  vps:
    url: http://100.64.0.5:8556
    # optional bearer token (otherwise tailnet trust only)
    token: "<secret>"
```

Or split into overlay files at `.aegis/remotes/<name>.yaml`:

```yaml
# .aegis/remotes/vps.yaml
url: http://100.64.0.5:8556
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
  peer_name: zion                # this serve's identity on outbound callbacks
                                  # — required when `remotes` is also configured
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

The default completion model is *fire-and-forget*: the calling aegis
gets a `task_id` back from the POST and is not notified when the
remote task finishes. What happens on completion is then entirely up
to the *receiving* serve's own configuration:

- If the receiver has a Telegram bridge configured, the worker's
  final message will land in Telegram on its way out.
- If the receiver runs in a repo and the worker commits and pushes,
  the work shows up in git.
- If the worker writes into a shared filesystem (a vault, a synced
  folder), it shows up there.
- If the receiver does nothing on completion, nothing happens on
  completion.

For the opt-in **wire callback** path — final-message delivery back
to the originating agent's inbox — see [Callbacks](#callbacks).

## Callbacks

When `aegis_enqueue(target="<peer>", callback=True)` is invoked, the
substrate attaches `callback_to` + `callback_handle` hints to the
wire enqueue body. On worker termination the receiving serve POSTs
the worker's final message to `POST /remote/v1/callback` on the
caller's plane; the caller's inbox router delivers it to the
originating agent as a normal `✉ from queue:<peer>:<name>` envelope.

```python
# zion: agent dispatches and keeps working
aegis_enqueue(
    queue="implementation",
    payload="Implement the design at docs/specs/foo.md…",
    from_handle="lucid-knuth",
    target="vps",
    callback=True,
)
# → {"task_id": "01J…", "target": "vps", …}

# …later, in lucid-knuth's transcript on zion:
#   ✉ from queue:vps:impl · task#01J… · ok · 17:46:11Z
#     Done. Implemented in branch feat/foo, pushed, CHANGELOG updated.
```

**Symmetric config required.** Both sides must define each other in
their `remotes:` block (see [Configuration](#configuration)). The
caller's `peer_name` for the target is what gets sent as
`callback_to`; the receiver looks that name up in its own
`remotes:` to find the URL to POST the callback to.

**Best-effort, no retry.** A callback that can't reach the caller
(network drop, caller restarted, auth rejected) is logged and
dropped — every callback attempt is recorded in the receiver's queue
JSONL with its outcome. Pass `callback=False` if you want the
fire-and-forget behavior explicitly.

## Remote schedules

A small control plane lets one serve **push** a schedule into a peer
and then inspect or remove it remotely. The substrate of v0.6's
schedule hot-reload watcher picks the pushed file up the moment it
lands; once seeded it behaves identically to an inline or overlay
schedule.

### HTTP endpoints

```
PUT    /remote/v1/schedule/<name>          # push a schedule body
GET    /remote/v1/schedule                 # list (with source classification)
GET    /remote/v1/schedule/<name>          # show one
DELETE /remote/v1/schedule/<name>          # remove a pushed/overlay schedule
GET    /remote/v1/schedule/<name>/logs     # JSONL tail
```

The PUT body is the same schedule shape used in `.aegis.yaml`. The
receiver writes it to `.aegis/schedules/<name>.yaml` with a
`# pushed_from: <caller>` provenance comment on the first line.

### MCP tools

```
aegis_schedule_push   (name, spec_body,        target=None)
aegis_schedule_list   (                        target=None)
aegis_schedule_show   (name,                   target=None)
aegis_schedule_remove (name,                   target=None)
aegis_schedule_logs   (name, tail=50,          target=None)
```

`target=None` operates on the local scheduler; `target="<peer>"`
routes through the remote-plane client. Same dict shapes either way.

### CLI verbs

```bash
aegis schedule push <name> <file.yaml> --to vps     # push to a peer
aegis schedule list   --remote vps                  # inspect a peer
aegis schedule show   morning-briefing --remote vps
aegis schedule remove ci-watch --remote vps
aegis schedule logs   nightly --remote vps -n 100
```

`--to` is push-only and required for `schedule push` when targeting
a peer; `--remote` is the inspection flag on the read/remove verbs.

### Source classification

`list` and `show` responses tag each schedule with a `source` field:

| source    | meaning                                                    |
|-----------|------------------------------------------------------------|
| `inline`  | declared in the receiver's `.aegis.yaml` `schedules:` table |
| `overlay` | hand-written file under `.aegis/schedules/<name>.yaml`     |
| `pushed`  | overlay file with a `# pushed_from:` provenance comment     |

`DELETE` refuses to remove `inline` schedules (the receiver's own
config owns those); `overlay` and `pushed` are removable.

### Self-scheduling

An agent can push its *own* future task into the local serve (or a
peer) — useful when finishing the current turn requires kicking
something off later. The standard pattern: build a `fire_at` body
~N seconds ahead, push, exit. The substrate's hot-reload picks it
up and fires it without further intervention.

## Security model

The remote plane has no opinion about your network — bind it where it
should be reachable from, and only from. A common deployment is a
private overlay network (Tailscale, Headscale, WireGuard,
plain VPN); the plane binds to its interface address and only nodes
on the overlay can reach the port. That makes the network itself the
trust anchor and keeps the HTTP surface narrow.

Two HTTP-layer gates compose on top:

- **Bearer tokens** (`accept_tokens` on the receiver, `token` on the
  caller). Set this when your network has callers you don't fully
  trust, or when you want different callers to use different
  secrets.
- **Source-IP allowlists** (`accept_from`). Set this when you want
  only specific peer IPs to be able to enqueue.

Both gates compose with **AND** when set. Both empty means "anything
that reaches the port is trusted" — appropriate when the network
itself is the trust anchor (small tailnet, plain VPN), inadequate the
day untrusted devices share the same network.

Tokens, if used, live in `.aegis.yaml` or its overlay files. Treat
them as secrets — keep them out of version control if the repo is
shared, or load them from env at startup.

## Patterns

### Local brainstorm → remote implementation

A long research / implementation task gets handed from an interactive
session on one machine to an `aegis serve` on another, where it can
run for hours under whatever profile and quota live there.

```python
aegis_enqueue(
    queue="implementation",
    payload=(
        "Implement the design at docs/specs/foo.md in this repo. "
        "Use TDD. Commit and push as you go. Update CHANGELOG."
    ),
    from_handle="lucid-knuth",
    target="builder",
)
# → {"task_id": "01J…", "queued_position": 0, "target": "builder",
#    "callback_note": "no wire return channel in v1; completion
#                      behavior is whatever the receiving serve is
#                      configured to do"}
```

The calling agent can wrap up the conversation. How you learn the
work is done depends on what the receiver does on completion — commits
landing in git, a notification through whatever bridge the receiver
runs, a file appearing in a synced folder, or simply checking back
later.

### Cheap local → expensive remote

A small fast model on one machine handles routing and clarification
and hands hard subproblems off to a bigger model running on another.
The remote's `.aegis.py` configures `implementation` against the
heavier model with full permissions; the calling serve doesn't even
need to know that model exists.

```python
# .aegis.py on the cheap side
agents = {
    "router": Agent(provider=ClaudeCode(model="haiku",
                                         permission="auto")),
}
default_agent = "router"
```

```yaml
# .aegis.yaml on the cheap side
remotes:
  builder:
    url: http://100.64.0.5:8556
```

### Several machines on one overlay

A handful of machines on the same overlay network — laptop, desktop,
a beefy box — each running `aegis serve`. Any agent can hand off to
any other peer it has declared as a remote.

```yaml
# .aegis.yaml on the laptop
remotes:
  desktop: { url: http://100.64.0.3:8556 }
  builder: { url: http://100.64.0.5:8556 }
remote_plane:
  bind: 100.64.0.4:8556
```

When the network itself is trusted (e.g. a personal tailnet with only
your own devices on it), no per-peer tokens are needed. Add bearer
tokens or `accept_from` allowlists the moment that stops being true.

## File layout

```
src/aegis/remote/
  config.py             # RemoteSpec, RemotePlaneSpec dataclasses
  plane.py              # Starlette app + build_plane / run_plane_async
  client.py             # httpx client: remote_enqueue, remote_callback,
                        # remote_schedule_push/list/show/remove/logs
  callback_observer.py  # receiver-side worker-completion observer
src/aegis/scheduler/
  push.py               # PUT/GET/DELETE handlers + source classification
```

The `aegis_enqueue` MCP tool routes to `remote.client.remote_enqueue`
when `target=` is set; `cli.serve()` mounts the plane via
`build_plane(queue_manager, cfg.remote_plane)` when `remote_plane` is
configured. The `aegis_schedule_*` tools and `aegis schedule` CLI
verbs use the same client module when given a `target=`/`--to`/
`--remote` peer.

## Future extensions (not built)

These are noted to confirm the `/remote/v1/` namespace leaves room
for them without breaking changes:

- **Status query** — `GET /remote/v1/task/<id>`.
- **Cancel** — `POST /remote/v1/task/<id>/cancel`.
- **Cross-host `aegis_handoff`** — handoff to a live remote handle.
  Semantically harder than enqueue; deferred until the use case
  appears.
