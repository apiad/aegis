# Budgets

Per-queue budgets let you declare cost ceilings on any queue. The
substrate enforces them at enqueue time: when admitting a new task
would push the queue over any configured ceiling, the enqueue is
rejected with a structured error that names the blocking constraint
and gives an ETA for when the queue will unblock itself.

## Why budgets

Aegis spawns workers autonomously. An agent in a retry loop, a
runaway schedule, or a workflow whose stop condition never trips can
quietly burn through API credits. `max_parallel` limits concurrency
but not aggregate cost over time — a queue with `max_parallel: 1`
can still run continuously and spend without bound.

Per-queue budgets close this gap. Three motivating patterns:

- **Cap an expensive queue.** Set `"$1/hour AND $10/day AND $50/week"`
  on your `impl` queue. A runaway loop burns one hour of budget, not
  a weekend.
- **Output-token belt.** Add `output_tokens: 500000` per hour on any
  queue. When a model drifts toward runaway verbosity, the token
  count trips before the USD ceiling has to — and the signal is
  provider-agnostic.
- **Leave cheap queues uncapped.** Queues used for quick routing
  (`fast`, `router`) carry no `budgets:` key and behave exactly as
  before. Per-queue granularity means you only pay the policy
  overhead where it matters.

## The model

### Multi-window, all-must-allow

Each queue can carry any number of `(constraint, window)` pairs.
A task is admitted iff **every** budget entry allows it — all-must-allow.
That means you can layer windows freely:

```python
"budgets": [
    {"usd": 1.00,  "window": "1h"},    # hourly guard
    {"usd": 10.00, "window": "24h"},   # daily ceiling
    {"usd": 50.00, "window": "7d"},    # weekly max
]
```

Each budget looks at the queue's recorded spend over its own rolling
window independently. If the 1h budget is blocking but the 24h budget
still has room, the 1h constraint wins and the enqueue is rejected.

### Constraint types

| Key             | Type    | Meaning                              |
|---|---|---|
| `usd`           | `float` | Rolling USD spend ceiling            |
| `output_tokens` | `int`   | Rolling output-token ceiling         |

Exactly one must appear per entry. Using both on the same entry, or
neither, is a boot-time config error.

### Window strings

`30m`, `1h`, `5h`, `24h`, `7d`, `1w`, `30d`.

Suffixes: `m` minutes, `h` hours, `d` days, `w` weeks.

### Cost accounting

Cost is recorded when a worker completes — whether it succeeded,
failed, or was interrupted. A failed worker still burned tokens.
Each `completed` and `failed` record in the queue's JSONL audit gains
a `cost` field:

```json
{
  "event": "completed",
  "task_id": "01HK…",
  "cost": {
    "usd": "0.0421",
    "input_tokens": 4200,
    "output_tokens": 1800,
    "cache_hit_tokens": 12000,
    "cache_write_tokens": 0,
    "thinking_tokens": 0
  }
}
```

`usd` is stored as a string to avoid floating-point drift across
thousands of records. The budget evaluator parses it back to `Decimal`
when summing a window.

The price table lives at `src/aegis/data/models.yaml` — a YAML document
keyed by `(provider, model)` with per-million-token rates. aegis ships
a bundled copy and, at boot, fires a best-effort background fetch of
`https://raw.githubusercontent.com/apiad/aegis/main/src/aegis/data/models.yaml`
into `~/.cache/aegis/models.yaml` (24h TTL), so new prices propagate
without a release. The cache wins over the bundled copy when present.

Update the YAML and push to `main`; every aegis installation picks it
up within 24h. `aegis.budget.prices.lookup(...)` is a thin shim over
the YAML registry for backward compatibility.

## Configuration

Add a `budgets:` list to any queue in `.aegis.yaml`:

```yaml
queues:
  impl:
    agent: opus
    max_parallel: 2
    budgets:
      - usd: 1.00
        window: 1h
      - usd: 10.00
        window: 24h
      - output_tokens: 500000     # runaway belt
        window: 1h
      - usd: 50.00
        window: 7d
  fast:
    agent: haiku-fast
    max_parallel: 4
    # no budgets: key → no caps; behaves as before
```

Validation happens at boot. Aegis refuses to start if:

- A budget entry has both `usd` and `output_tokens`.
- A budget entry has neither.
- A window string is not recognized.
- Two entries on the same queue share an identical `(constraint, window)` pair.

## Rejection shape

When a new enqueue would exceed any budget, the call returns
immediately with a structured error — across all surfaces (MCP,
HTTP, workflow engine, CLI). The error names every blocking
constraint and an ETA for when the queue will unblock:

```json
{
  "error": "queue 'impl' over budget",
  "queue": "impl",
  "blocked_by": [
    {
      "constraint": "usd",
      "limit": "1.00",
      "spent": "1.23",
      "window": "1h",
      "unblock_at": "2026-05-25T18:42:00Z"
    },
    {
      "constraint": "output_tokens",
      "limit": "500000",
      "spent": "612340",
      "window": "1h",
      "unblock_at": "2026-05-25T19:10:00Z"
    }
  ],
  "unblock_at": "2026-05-25T19:10:00Z"
}
```

`unblock_at` is the latest ETA across all blocking constraints — the
time at which the queue will next accept an enqueue if no further work
is done. It's approximate but useful: the oldest records are aging out
of the window, and the ETA marks the point at which cumulative spend
drops back below the limit.

What you do with the rejection is up to you: retry at `unblock_at`,
route to a different queue, or escalate to a human. The substrate has
no retry policy of its own.

## Observability

### CLI

```bash
aegis budget list                 # one-line summary per queue
aegis budget show impl            # full decision: every check with
                                  # spent / limit / headroom / unblock_at
aegis budget list --remote vps    # remote serve's summary
aegis budget show --remote vps impl
```

`list` output:

```
QUEUE   BUDGETS  BINDING                         STATUS
impl    4        $1.23 of $1.00 / 1h             ⛔ over (unblocks 18:42Z)
review  2        $4.10 of $10.00 / 24h           ✓ ok
fast    0        —                               — no budget
```

`show` prints every check (not just blocking ones) so you can see
full headroom across all windows at once.

### MCP

```python
aegis_budget_status(
    queue=None,       # None → summary for all queues
    target=None,      # None → local; "<peer>" → remote serve
    from_handle="…"
)
```

`queue=None` returns a summary row per queue. `queue="impl"` returns
the full `Decision` for that queue — identical in shape to the
`blocked_by` field of a rejection, but including every configured
budget (not just blocking ones).

Use this from a workflow before an expensive enqueue to check headroom
and decide whether to wait or route elsewhere:

```python
decision = await engine.budget_status("impl")
if not decision["allowed"]:
    # log and wait until unblock_at
    return {"status": "deferred", "reason": decision["blocked_by"]}
await engine.enqueue("impl", payload=…)
```

### HTTP

Two read-only endpoints on the remote plane (gated by the same token
and IP allowlists as `/enqueue`):

```
GET /remote/v1/budget
GET /remote/v1/budget/<queue>
```

The list endpoint returns a summary row per queue. The per-queue
endpoint returns the full `Decision` — or `404` if the queue doesn't
exist on that serve.

These are read-only. Budget configuration lives in `.aegis.yaml` and is
not writable over the wire.

## Patterns

### Cap an Opus queue

The classic pattern: an hourly guard, a daily ceiling, and a weekly
max. Three windows, layered:

```python
"impl": {
    "agent": "opus",
    "max_parallel": 2,
    "budgets": [
        {"usd": 1.00,  "window": "1h"},
        {"usd": 10.00, "window": "24h"},
        {"usd": 50.00, "window": "7d"},
    ],
},
```

When your workflow calls `engine.enqueue("impl", …)`, the substrate
checks all three windows. If the 1h budget is exhausted, the enqueue
raises `BudgetExceeded` in the workflow. The workflow can catch it,
log it, and schedule a retry at `decision.unblock_at`.

### Runaway-output belt

Add an output-token ceiling alongside your USD budget. USD is billed
at completion; a model drifting toward extremely long outputs may rack
up tokens faster than the dollar amount implies. The token constraint
trips first and is provider-agnostic:

```python
"research": {
    "agent": "sonnet",
    "max_parallel": 1,
    "budgets": [
        {"usd": 5.00,             "window": "1h"},
        {"output_tokens": 500000, "window": "1h"},
    ],
},
```

Whichever constraint trips first blocks the queue. The rejection names
both if both are exceeded.

### Check before enqueuing

If you want to surface budget status proactively rather than handling
a rejection, check first with `aegis_budget_status`:

```python
# in a workflow
status = await engine.budget_status("impl")
if not status["allowed"]:
    eta = status["unblock_at"]
    await engine.send(pm, f"impl queue over budget; estimated clear at {eta}")
    return
await engine.enqueue("impl", payload=…)
```

## Non-goals

The following are intentionally out of scope for v0.9:

- **No global / cross-queue ceiling.** Budgets are per-queue. If you
  want a combined ceiling across multiple queues, add up the per-queue
  caps manually. A top-level `budget:` block is a small additive
  change for a later release.
- **No pre-flight cost estimation.** In-flight workers don't count
  toward the budget until they finish and their JSONL record is
  written. If three $0.99 tasks start simultaneously against a $1/hour
  cap, all three are admitted; the $2.97+ total is recorded on
  completion and the next task is blocked. This is consistent with
  the design principle: the gate operates on *recorded* facts, not
  promises.
- **No throttle or holding pen.** When a budget is exceeded, the
  enqueue is rejected immediately. There is no queue of waiting tasks
  that auto-resume when budget becomes available. The caller decides
  when to retry.
- **No alerts or push notifications.** Budget state surfaces only
  through the pull surfaces above (CLI / MCP / HTTP). The rejection
  at enqueue time is the only "loud" signal.
- **No TUI surface in v0.9.** Budget bands in the `Ctrl+D` dashboard
  and the always-on strip are deferred to v0.9.1.
- **No per-agent-profile budgets.** The policy boundary is the queue.
  If two queues share a profile and you want separate budgets, split
  them into two distinct queues.

## FAQ

**What happens to in-flight workers when a budget trips?**

Nothing. In-flight workers complete normally. The budget check only
gates new enqueues; it doesn't cancel running work. "Recover" by
cancelling a worker mid-task would cost more tokens than letting it
finish.

**Do failed workers count against the budget?**

Yes. A worker that errored or was interrupted still consumed tokens.
The `cost` field is written on all terminal outcomes (`completed`,
`failed`).

**What if I haven't configured prices for my model?**

If the queue's `(provider, model)` pair isn't in the price table at
`src/aegis/budget/prices.py`, the finalizer records
`"cost": {"error": "unknown_model"}` instead of crashing. The record
counts as `$0` for budget purposes, so the queue stays permanently
unblocked for USD constraints. Add the model to the price table to
get accurate enforcement.

**Does budget state survive a restart?**

Yes. The budget evaluator reads from the per-queue JSONL audit at
`.aegis/state/queues/<queue>.jsonl`, which is written to disk and
replayed on every start. There is no separate budget state file.

**Can I change budgets without restarting?**

Yes. `.aegis.yaml` is re-evaluated on config reload (or `aegis` restart).
Budget changes apply to the next enqueue after reload; in-flight workers
are not affected.

**What if two entries in `budgets:` have the same constraint and window?**

That's a boot-time config error. Aegis refuses to start. Duplicate
`(constraint, window)` pairs on the same queue are meaningless — only
the stricter one would ever matter, and duplicates suggest a config
mistake.

**The `unblock_at` time passed but the queue is still blocked. Why?**

`unblock_at` is an estimate: it assumes no further work completes
between now and then. If workers finished in that interval, their
costs are now inside the window and the spend is still above the
ceiling. The ETA is a lower bound on unblock time, not a guarantee.

**Can I inspect budgets on a remote serve?**

Yes, with `--remote <peer>` on the CLI verbs, `target="<peer>"` on
`aegis_budget_status`, or `GET /remote/v1/budget` on the HTTP plane.
The remote plane's existing auth gates apply.
