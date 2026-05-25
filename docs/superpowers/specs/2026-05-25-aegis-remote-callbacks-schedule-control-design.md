---
title: Aegis Remote Plane — Wire Callbacks + Schedule Control
date: 2026-05-25
status: draft
---

# Aegis Remote Plane — Wire Callbacks + Schedule Control

## Motivation

The v0.7 remote plane shipped with `aegis_enqueue(target=…)` and a
single `POST /remote/v1/enqueue` endpoint. Two limitations are
becoming the next thing in the way:

- **No wire return channel.** `callback=true` on the existing tool is
  silently ignored for remote targets; the v0.7 spec called this out
  explicitly as future work. An agent that enqueues a long task to a
  peer has no way to learn it finished short of the receiver doing
  something on its own (commits, its own bridge, a shared folder).
- **No way to arm a recurring task on a remote.** The v0.6 scheduler
  is per-serve: schedules declared in a serve's own `.aegis.yaml` tick
  on that serve. To get a scheduled task running on a peer, you have
  to SSH in and edit its config — exactly the kind of out-of-band
  step the substrate was meant to obviate.

This design ships both as **two independent extensions** on top of the
v0.7 remote plane:

- **A — Wire callbacks.** Promote `callback=true` from "ignored for
  remote targets" to "delivers an inbox message to the originating
  handle when the remote task terminates." Receiver POSTs back to the
  caller's plane; caller's plane routes the message into the
  originating handle's inbox via the existing `InboxRouter`.
- **B — Remote schedule control plane.** A handful of new endpoints
  under `/remote/v1/schedule` that let a client push a schedule
  definition into a remote serve, list what's there, read one entry,
  fetch its audit log, and remove it. Push writes the spec to
  `.aegis/schedules/<name>.yaml` on the receiver; from the receiver's
  POV it becomes an indistinguishable native schedule.

The two features are orthogonal — you can ship A without B or B
without A. They share only the existing trust model (`remotes:`
outbound, `remote_plane.{accept_tokens,accept_from}` inbound) and the
URL prefix.

## Non-goals (explicit)

These were considered and deferred:

- **HTTP-level `/run`, `/enable`, `/disable` on schedules.** The MCP
  surface and CLI on the local serve already support these for local
  schedules; cross-host toggling can be added later when there's a
  real need. v1 push/list/show/remove/logs covers the case the design
  is for ("arm it once, see what's there, take it down").
- **Cross-host `aegis_handoff` to a live remote handle.** Still
  semantically harder than enqueue (target is an alive session on
  the other end, not a queue). Not unlocked by this work.
- **Callbacks for scheduler-triggered remote enqueues.** A scheduled
  `enqueue` workflow firing with `target=` is fine; setting
  `callback=true` on it is an explicit validation error (no live
  inbox to deliver to). The local scheduler doesn't grow callback-
  consumer machinery.
- **Per-callback retry / persistent queue.** Best-effort POST, one
  attempt, log + drop on miss. Substrate-level callback durability is
  out of scope; receiver-side completion behavior (commits, bridges,
  whatever) covers the case where the calling agent isn't reachable.
- **Schema migration for pushed schedules.** A pushed spec is the
  v0.6 schedule-entry shape serialized to JSON. No version field, no
  migration path. Schema changes are coordinated upgrades across
  peers.
- **Multi-peer broadcast push.** A `push --to vps,desktop` shortcut is
  noise on top of two `push --to <peer>` calls. Out.

## Architecture overview

```
   ┌────────────────────────────────┐                ┌────────────────────────────────┐
   │ caller (e.g. zion)             │                │ receiver (e.g. vps)            │
   │                                │                │                                │
   │  remote_plane (tailnet IP)     │                │  remote_plane (tailnet IP)     │
   │    POST /remote/v1/enqueue ──▶ │ ────────────── │ ───▶ enqueue handler           │
   │    POST /remote/v1/callback ◀──│ ◀──────────────│ ◀─── QueueManager observer     │
   │    PUT  /remote/v1/schedule ──▶│ ────────────── │ ───▶ schedule push handler     │
   │    GET  /remote/v1/schedule    │                │                                │
   │                                │                │                                │
   │  QueueManager                  │                │  QueueManager                  │
   │  InboxRouter ◀── callback POST │                │  scheduler + hot reload        │
   └────────────────────────────────┘                └────────────────────────────────┘
```

Both sides now run the remote plane (the v0.7 spec already permitted
this; this design promotes it from optional to "required if you want
callbacks"). The two HTTP planes stay distinct:

- **MCP plane:** loopback, no auth, FastMCP HTTP, consumed by local
  workers. Unchanged.
- **Remote plane:** tailnet-bound, narrow surface, consumed by other
  aegis serves. New endpoints land here; existing `/enqueue` is
  unchanged.

No new persistent state machinery on either side:

- Callbacks ride the existing `QueueManager` completion event on the
  receiver and the existing `InboxRouter` delivery on the caller.
- Schedule pushes land as files in the receiver's already-watched
  `.aegis/schedules/` folder; the v0.6 hot-reload watcher picks them
  up; the existing scheduler runtime owns the rest.

## Feature A — Wire callbacks

### Wire shape: `/enqueue` body grows callback hints

The existing `POST /remote/v1/enqueue` body grows two optional fields:

```
POST /remote/v1/enqueue
Body:
  {
    "queue":            "implementation",
    "payload":          "<full prompt>",
    "from":             "zion",                # existing — caller's identity for receiver audit
    "callback_to":      "zion",                # optional — name the receiver should look up in its `remotes`
    "callback_handle":  "lucid-knuth"          # optional — originating agent handle, round-tripped
  }
```

`callback_to` is **the receiver's name for the caller** — the key the
receiver expects to find in *its own* `remotes` map. Usually the same
string as `from`, but kept separate so peer-name asymmetries (zion
calls vps `vps`; vps calls zion `laptop`) don't break the round-trip.

When both fields are present, the receiver attaches them to the task
record at enqueue time so the completion observer can find them later.
When either is missing, callbacks are off for that task — the receiver
records `enqueued_by="remote:<from>"` and treats it like a v0.7
fire-and-forget enqueue.

### Callback endpoint

```
POST /remote/v1/callback
Authorization: Bearer <token>   # when remotes[from_peer].token is set
Content-Type: application/json
Body:
  {
    "task_id":      "01J…",
    "queue":        "implementation",
    "from_peer":    "vps",                    # peer name in caller's `remotes`
    "to_handle":    "lucid-knuth",
    "status":       "ok" | "failed" | "interrupted",
    "result_text":  "<worker's final assistant message>",
    "started_at":   "<iso>",
    "ended_at":     "<iso>"
  }
Response 204                                  # acknowledged
Response 4xx with {"error": "..."}            # auth / shape rejection
```

The caller's plane verifies auth, locates the active session for
`to_handle`, and hands the payload to its `InboxRouter` to construct
an envelope:

```
✉ from queue:vps:implementation · task#01J… · ok · 17:46:11Z
  PR pushed to feat/rate-limit, commit abc1234…
```

Same shape as today's local queue callback envelope, just with the
peer name prefixed on the queue tag.

### Round-trip walkthrough

1. Agent calls
   `aegis_enqueue(queue="impl", payload="…", from_handle="lucid-knuth", callback=True, target="vps")`.
2. Caller's MCP server resolves `target="vps"` to its `remotes.vps`
   entry, POSTs `/remote/v1/enqueue` to vps with
   `{queue, payload, from: "zion", callback_to: "zion", callback_handle: "lucid-knuth"}`.
   (`callback_to` defaults to the **receiver's own configured peer
   name for the caller**; see Configuration below.)
3. Receiver enqueues the task into its local `QueueManager` and
   records `callback_to` + `callback_handle` on the task record.
4. Worker runs. When the task terminates (any of `ok` / `failed` /
   `interrupted`), the receiver's existing completion event fires.
   A new observer subscribed to that event collects:
   - `result_text` from the worker's final assistant message;
   - `started_at` / `ended_at` from the task record;
   - `callback_to` / `callback_handle` from the task record;
   - looks up `remotes[callback_to]` on the receiver for URL + token;
   - POSTs `/remote/v1/callback` with the body above.
5. Caller's plane validates auth, hands `result_text` to `InboxRouter`
   for `to_handle`, mounts the `✉` block in the originating session's
   transcript.

### Failure model (callbacks)

All miss modes are loud on the receiver side (JSONL audit) and silent
on the caller side (because the caller never knows the callback was
attempted):

| Condition                                       | Receiver action                                                 |
|-------------------------------------------------|-----------------------------------------------------------------|
| `callback_to` not in receiver's `remotes`       | log `callback_dropped: unknown_peer`; no POST                  |
| Caller's plane unreachable / timeout            | log `callback_dropped: unreachable`; no retry                  |
| Caller responds 401 (token mismatch)            | log `callback_dropped: auth_rejected`                          |
| Caller responds 5xx                             | log `callback_dropped: 5xx`                                     |
| Caller responds 200 but `to_handle` closed      | caller-side `InboxRouter` writes to per-handle JSONL, no UI    |
| `callback_to` / `callback_handle` not set       | no callback attempted (v0.7 fire-and-forget behavior)          |

Audit lands in the receiver's `.aegis/state/queues/<queue>.jsonl` as a
`callback_attempted` record next to the existing `task_done` record.
Timeouts mirror v0.7 outbound: 5s connect, 10s read.

## Feature B — Remote schedule control plane

### Endpoint surface

Five endpoints, all under `/remote/v1/schedule`. Same auth gates as
`/enqueue` and `/callback`.

#### Push (create or replace)

```
PUT /remote/v1/schedule/<name>
Authorization: Bearer <token>
Content-Type: application/json
Body:
  {
    "workflow":      "enqueue",
    "args":          { "queue": "impl", "payload": "..." },
    "cron":          "0 2 * * *",
    "lifecycle":     "forever",
    "on_overlap":    "skip",
    "timezone":      "UTC",
    "enabled":       true,
    "notify":        false
  }
Response 200: {"name": "<name>", "written_to": ".aegis/schedules/<name>.yaml"}
Response 4xx with {"error": "..."}
```

PUT semantics — same name pushed twice writes twice; last write wins.
Receiver-side validation runs **before** the file write:

- cron parse must succeed;
- workflow must be in the receiver's workflow registry;
- args must satisfy the workflow's declared signature;
- lifecycle must be one of `forever` / `once` / `{fires: N}` /
  `{until: <iso>}`;
- if `args.callback == true` and the workflow is `enqueue`,
  this is rejected: scheduler-triggered remote enqueues with
  callback are an explicit non-goal.

On success, the receiver writes
`.aegis/schedules/<name>.yaml` atomically (tempfile + rename) using
ruamel.yaml's comment-preserving serializer, prefixed with a
provenance comment:

```yaml
# pushed_from: peer:zion at 2026-05-25T16:32:11Z
workflow: enqueue
cron: "0 2 * * *"
args:
  queue: impl
  payload: "..."
lifecycle: forever
```

The existing v0.6 hot-reload watcher picks up the new file within
~1s. The schedule joins the running table on the next reload pass.

#### List

```
GET /remote/v1/schedule
Response 200:
  {
    "schedules": [
      {"name": "nightly-build",   "source": "pushed",  ...summary fields},
      {"name": "nightly-cleanup", "source": "inline",  ...summary fields},
      {"name": "weekly-report",   "source": "overlay", ...summary fields},
      ...
    ]
  }
```

`source` distinguishes:

- `"inline"` — declared in `.aegis.yaml`'s `schedules:` section;
- `"overlay"` — written to `.aegis/schedules/<name>.yaml` by a human;
- `"pushed"` — written to `.aegis/schedules/<name>.yaml` by a PUT.

`source: "pushed"` is detected from the leading `# pushed_from:` comment.

Summary fields: `next_fire`, `fire_count`, `in_flight`, `enabled`,
`workflow`, `cron` (or `fire_at`).

#### Show

```
GET /remote/v1/schedule/<name>
Response 200:
  {
    "name":         "<name>",
    "source":       "pushed",
    "spec":         {workflow, args, cron, lifecycle, ...},
    "runtime":      {next_fire, last_fire, fire_count, in_flight, enabled},
    "pushed_from":  "peer:zion",   # or "agent:lucid-knuth", null when source != "pushed"
    "pushed_at":    "<iso>"
  }
Response 404 when no such schedule
```

#### Remove

```
DELETE /remote/v1/schedule/<name>
Response 204
Response 404 when no such schedule
Response 409 when source != "pushed" (inline + hand-edited overlays not removable here)
```

Only schedules carrying a `# pushed_from:` provenance comment can be
removed via DELETE. Inline and hand-edited overlays are out of scope —
the operator owns those locally.

DELETE removes the file; the hot-reload watcher drops the schedule on
its next pass.

#### Logs

```
GET /remote/v1/schedule/<name>/logs?tail=50
Response 200: {"records": [<jsonl line as object>, ...]}
```

Tail of `.aegis/state/schedules/<name>.jsonl`. No streaming, no
follow.

### Receiver-side mechanics

- **Atomic write.** ruamel.yaml dump → tempfile → `rename()` into
  `.aegis/schedules/`. The hot-reload watcher only sees fully-formed
  files.
- **Validation reuses the existing scheduler validator.** Same code
  path that boot-time `.aegis.yaml` parsing uses; no duplication.
- **Provenance.** First two lines of every pushed file are a
  `# pushed_from:` + `# pushed_at:` comment pair. Detected by the
  list/show endpoints; preserved by ruamel across operator hand-edits
  (operator can hand-edit other parts of the file without losing the
  provenance stamp).
- **No new state file.** All durability is the existing schedule
  JSONL audit; the only new bytes on disk are pushed YAML files in
  the already-watched overlay folder.

## MCP surface

### `aegis_enqueue` — semantics change, no signature change

The existing tool signature is unchanged. The docstring and the
returned `callback_note` are rewritten to reflect that callbacks now
work when both sides are configured:

```python
async def aegis_enqueue(
    queue:       str,
    payload:     str,
    from_handle: str,
    callback:    bool         = True,
    target:      str | None   = None,
) -> dict:
    """...
    If ``target`` is set and ``callback=true``, the worker's final
    message will be delivered to your inbox via a wire callback once
    the remote task terminates. Requires both sides to have configured
    each other in `remotes` and the calling side to have
    `remote_plane.bind` set.

    When ``callback=true`` is set on a remote target but this serve
    has no `remote_plane.bind`, the call fails loudly — there is no
    way to receive the callback. Set ``callback=false`` or configure
    the inbound plane.
    ...
    """
```

The `callback_note` string returned to the agent on a successful
remote enqueue becomes one of:

- `"callback will deliver to your inbox when the remote task terminates"` (callback=true, both sides configured)
- `"fire-and-forget — completion behavior is whatever the receiving serve is configured to do"` (callback=false)

### Five new schedule tools

```python
aegis_schedule_push(name: str, spec: dict, *,
                    target: str | None = None,
                    from_handle: str) -> dict
# Local: validate spec, write .aegis/schedules/<name>.yaml on this
# serve, stamp `# pushed_from: agent:<from_handle>`.
# Remote: PUT /remote/v1/schedule/<name> on the named peer; receiver
# stamps `# pushed_from: peer:<this-peer's-name-in-receiver's-view>`.
# Returns {"name": ..., "written_to": ..., "target": "<peer>"}
# or {"error": ...}.

aegis_schedule_list(*, target: str | None = None,
                    from_handle: str) -> dict
# Returns {"schedules": [{name, source, next_fire, fire_count,
#                          in_flight, enabled, workflow, cron}, ...]}.

aegis_schedule_show(name: str, *, target: str | None = None,
                    from_handle: str) -> dict
# Returns the full show dict, or {"error": "no such schedule"}.

aegis_schedule_remove(name: str, *, target: str | None = None,
                      from_handle: str) -> dict
# Removes only pushed schedules. {"removed": "<name>"} or
# {"error": "cannot remove inline/overlay schedule"}.

aegis_schedule_logs(name: str, *, target: str | None = None,
                    tail: int = 50,
                    from_handle: str) -> dict
# {"records": [<jsonl object>, ...]}.
```

All five accept `target=None` (operate on this serve) or
`target="<peer>"` (route through the named peer's HTTP endpoint via
the same client used by `aegis_enqueue(target=...)`).

Cross-host `/run`, `/enable`, `/disable` are explicit non-goals; the
MCP surface stays 1:1 with the HTTP surface.

### Self-scheduling

`target=None` means an agent can write a schedule into its **own**
serve's substrate. Use case: an agent decides "I should re-run this
analysis every Sunday at 6am" and calls
`aegis_schedule_push(name="weekly-analysis", spec={workflow: "prompt", cron: "0 6 * * 0", args: {...}})`.
The schedule joins the local scheduler table within ~1s via hot
reload. No human-in-the-loop required.

The operator can always `aegis schedule remove weekly-analysis`
(local CLI) to revoke an agent-pushed schedule. The provenance
stamp lets `aegis schedule list` show at a glance which schedules
were agent-written vs operator-written vs peer-pushed.

## CLI surface

Five new verbs / flags on the existing `aegis schedule` subapp.

```bash
# Push (resolve spec from local config or file).
aegis schedule push --to vps --name nightly-build
aegis schedule push --to vps --file my-schedule.yaml
aegis schedule push --to vps --file my-schedule.json --name override-name

# Read (--remote flag on existing inspection verbs).
aegis schedule list --remote vps
aegis schedule show --remote vps nightly-build
aegis schedule logs --remote vps nightly-build --tail 50

# Remove.
aegis schedule remove --remote vps nightly-build
```

`push` resolves the spec from one of:

- `--name <n>` (no `--file`): read `<n>` from the local scheduler
  config (inline `.aegis.yaml` `schedules.<n>` or
  `.aegis/schedules/<n>.yaml`), serialize to JSON, PUT.
- `--file <p>`: read the file (YAML or JSON, auto-detected by
  extension), PUT under the file's stem name unless `--name` overrides.
- both: `--file` provides the spec; `--name` provides the remote name.

No local state is retained after a successful push. "What's on the
remote" is always a live GET to that remote; never a cached view.

All five verbs print receiver-side errors verbatim (4xx body → stderr
→ non-zero exit).

## Configuration

The recommended config has *both* sides naming each other and
*both* sides carrying a token if any. Both `remotes` and
`remote_plane` are already present in v0.7; this design doesn't
change either schema. It does change the **deployment shape**:

- v0.7: one side outbound (`remotes` only), other side inbound
  (`remote_plane` only) is a valid shape.
- v0.7+callbacks: any peer that wants to **receive** callbacks must
  also have `remote_plane.bind` set. The MCP-level error message at
  `aegis_enqueue(target=…, callback=true)` time tells the operator
  what's missing.

```yaml
# .aegis.yaml on zion
remotes:
  vps:
    url: http://100.64.0.5:8556
    token: "<secret-for-zion-calling-vps>"
remote_plane:
  bind: 100.64.0.4:8556
  accept_tokens: ["<secret-for-vps-calling-zion>"]
```

```yaml
# .aegis.yaml on vps
remotes:
  zion:
    url: http://100.64.0.4:8556
    token: "<secret-for-vps-calling-zion>"
remote_plane:
  bind: 100.64.0.5:8556
  accept_tokens: ["<secret-for-zion-calling-vps>"]
```

Tokens are independent per direction (vps's outbound secret can
differ from zion's outbound secret). Same `remotes:` overlay folder
mechanism as v0.7.

## Implementation sketch

### Touched modules

- `src/aegis/remote/plane.py` — three new endpoints (`/callback`,
  `/schedule/...`); existing `/enqueue` body grows optional
  `callback_to` + `callback_handle` fields.
- `src/aegis/remote/client.py` — three new outbound paths
  (`remote_callback`, `remote_schedule_push/list/show/remove/logs`).
- `src/aegis/remote/config.py` — no schema change (existing fields
  cover both new features).
- `src/aegis/queue/manager.py` — completion observer hooks the new
  callback observer; task record grows `callback_to` /
  `callback_handle` fields.
- `src/aegis/mcp/server.py` — `aegis_enqueue` docstring + callback
  routing; five new `aegis_schedule_*` MCP tools.
- `src/aegis/scheduler/push.py` (new) — receiver-side handler for
  PUT/GET/DELETE schedule endpoints. Reuses the existing
  `cron.parse` + `lifecycle.parse` + workflow-registry validators.
- `src/aegis/cli_schedule.py` — five new verbs / `--remote` flags.

### State changes

None new on disk:

- Callbacks ride existing queue JSONL audit (new `callback_attempted`
  record alongside `task_done`).
- Pushed schedules land in `.aegis/schedules/<name>.yaml`, picked up
  by the existing hot-reload watcher; their lifecycle is the existing
  schedule JSONL at `.aegis/state/schedules/<name>.jsonl`.

## Testing

Mirrors the v0.7 shape:

- **Unit.** Plane endpoint shape validation (auth, body, error
  paths). Receiver-side schedule validator (cron, lifecycle, workflow
  registry, args type-check). Client error normalization for the
  three new outbound paths. Provenance comment round-trips through
  ruamel.
- **Plane endpoint tests.** Auth gating on `/callback` and
  `/schedule/*` (token mismatch, IP allowlist, both combined).
  PUT-then-GET symmetry. PUT-then-DELETE-then-GET → 404. DELETE of
  inline → 409. GET of non-existent → 404.
- **Hermetic integration.** Two `aegis serve` instances in the same
  test process on loopback ports, configured as each other's peers.
  - Callback round-trip: `aegis_enqueue(target=B, callback=true)` on
    A; worker on B produces a result; A's pane mounts the `✉` block
    with the right tag and body. Plus three failure modes
    (`callback_to` unknown, A's plane down, A's session closed).
  - Schedule push cycle: A pushes a schedule onto B; the file lands
    at `.aegis/schedules/<name>.yaml` on B's tempdir; B's hot reload
    picks it up; B fires it (FakeClock); A reads logs back; A
    deletes it; the file disappears; B drops the schedule.
- **Live (`@pytest.mark.live`).** Opt-in real-peer round-trip:
  one callback cycle + one schedule push-fire-logs-remove cycle.
  Auto-skip when the peer is unreachable, same convention v0.7 uses.

## Future extensions (not built)

These are noted to confirm the `/remote/v1/` namespace leaves room
for them without breaking changes:

- `POST /remote/v1/schedule/<name>/run` / `enable` / `disable`.
  Same payload-light shape as DELETE. Probably v0.9 or whenever an
  agent needs cross-host toggling.
- `GET /remote/v1/task/<id>` (status query) and
  `POST /remote/v1/task/<id>/cancel`. Useful but standalone.
- `aegis_handoff(target_handle=…, target="<peer>")` — cross-host
  handoff to a live remote handle. Semantically harder; deferred
  until the use case appears.
- `aegis_schedule_subscribe(name, target=…)` — wire-level
  notification when a remote schedule fires/completes. Would compose
  on top of the callback plumbing this round ships.

## Open questions

1. **`callback_to` default.** Spec says it defaults to "the
   receiver's own configured peer name for the caller." That requires
   the caller to know what the receiver calls it. Two options:
   (a) caller specifies `callback_to` explicitly in `remotes.vps`
   config (e.g., `remotes.vps.peer_name: "laptop"`); (b) caller passes
   its own `from` as `callback_to` and trusts the receiver to find a
   matching `remotes:` entry. Lean (a) — explicit beats implicit; the
   v0.7 spec already has the operator naming the peer in `remotes:`
   so the symmetry is natural.

2. **`pushed_from` in self-pushes.** When an agent pushes locally
   (`target=None`), the provenance stamp is
   `# pushed_from: agent:<handle>`. When the same agent pushes to a
   peer, the receiver stamps
   `# pushed_from: peer:<this-peer's-name-in-receiver's-view>`. The
   originating agent's handle is *not* round-tripped to the receiver.
   This is intentional — the receiver doesn't trust the caller's
   handle namespace — but worth flagging in case we want a richer
   audit later (e.g., `peer:zion:agent:lucid-knuth`).

3. **`callback_handle` reuse for spawned-tab callbacks.** Today a
   callback delivers to the handle that originated the enqueue. If
   that handle has spawned other tabs and the originator wants the
   callback to land on a different tab, no current mechanism. Out of
   scope for v1 (callbacks deliver to `from_handle`), but worth
   noting.
