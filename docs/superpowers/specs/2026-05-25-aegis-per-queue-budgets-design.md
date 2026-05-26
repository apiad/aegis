---
title: Aegis Per-Queue Token / USD Budgets
date: 2026-05-25
status: draft
---

# Aegis Per-Queue Token / USD Budgets

## Motivation

Aegis spawns workers autonomously. An agent in a retry loop, a runaway
schedule, or a workflow whose stop-condition never trips can quietly
burn through subscription credits or API spend. Today there is no
substrate-level mechanism to cap that — `max_parallel` limits
concurrency but not aggregate cost over time.

This design ships **per-queue budgets**: each queue declares one or
more `(constraint, window)` pairs (USD or output-token ceilings over a
rolling window); the substrate rejects new enqueues that would land
the queue over any of those ceilings. The rejection at enqueue time
*is* the signal — whoever called gets a structured error and decides
what to do (retry later, route to a different queue, escalate to a
human). No Telegram pings, no holding-pen, no implicit retries.

The motivating use cases:

- Cap the expensive `impl` queue at "$1/hour AND $10/day AND $50/week"
  so a runaway loop in a long-running workflow burns one hour of
  budget instead of a weekend.
- Cap a `research` queue at "500k output tokens per hour" as a
  belt for runaway-output failure modes (provider-agnostic — the
  output-token signal is cheaper to reason about than USD when
  models drift).
- Leave queues used for quick interactive routing (`fast`, `router`)
  budget-free.

## Non-goals (explicit)

- **No global / cross-queue ceiling.** Per-queue only in v1. Sum of
  per-queue caps is whatever it adds up to; if you want a total cap,
  spreadsheet it. A top-level `budget:` block would be a small
  additive change later.
- **No pre-flight cost estimation.** v1 charges only on `completed`/`failed`;
  in-flight tasks don't count toward the budget until they finish.
  The "I admitted three tasks at $0.99 and now I'm at $4.50" race
  is acceptable — the substrate is honest about what's been
  *recorded*, not what's been promised.
- **No throttle / holding-pen.** Reject loud at enqueue time; the
  caller's policy decides how to back off. The substrate has no
  opinion on retry strategy.
- **No alerts / notifications.** Telegram pings on budget exhaustion
  are explicitly out. The structured error returned at enqueue *is*
  the signal. Observability surfaces (CLI/MCP/HTTP/TUI) are pull,
  never push.
- **No write API on `/remote/v1/budget`.** Budget configuration lives
  in `.aegis.py` (Python-authored, not pushable over the wire). GET
  only; operators edit `.aegis.py` and the existing config hot-reload
  picks up the change on the next reload.
- **No mid-flight worker cancellation when budget trips.** In-flight
  workers complete; only new enqueues are blocked. Killing a worker
  mid-task to "recover" budget would cost more than letting it
  finish.
- **No retroactive cost backfill.** Pre-v0.9 `completed`/`failed` records that
  lack a `cost` field are treated as `$0` in the rolling-window sum.
  The first window's worth of post-deploy data slowly becomes
  accurate as the window rolls.
- **No per-agent-profile budgets.** Queues are the policy boundary;
  if two queues share an expensive profile, the operator can split
  them into separate queues to get separate budgets.

## Architecture overview

```
   ┌──────────────────────────────────────────────────────────────────┐
   │  .aegis.py: queues = {"impl": {agent: opus, budgets: [...]} }    │
   └──────────────────────────────────────────────────────────────────┘
                                  │ static config
                                  ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │  QueueManager.enqueue()                                           │
   │    1. evaluator.evaluate_budgets(jsonl_tail, budgets, now)        │
   │    2. if Decision.allowed: admit task                             │
   │       else: return {"error": ..., "blocked_by": [...]}            │
   │                                                                   │
   │  QueueManager._finalize()                                         │
   │    1. cost = cost.compute(metrics, provider, model)               │
   │    2. write completed/failed JSONL record with cost field         │
   └──────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ pure function over JSONL tail
                                  │
   ┌──────────────────────────────────────────────────────────────────┐
   │  src/aegis/budget/                                                │
   │    prices.py      — static (provider, model) → ProviderPrices     │
   │    cost.py        — compute(metrics, provider, model) -> Cost     │
   │    evaluator.py   — evaluate_budgets(jsonl, budgets, now)         │
   └──────────────────────────────────────────────────────────────────┘
```

The budget logic is a **pure function** over the existing per-queue
JSONL audit at `<state_dir>/queues/<queue>.jsonl` (where `state_dir`
is set on `QueueManager`; typical default in `aegis serve` is
`./.aegis/state`). No new state machine, no new persistence file, no
observer module, no Telegram coupling.

## Config shape

`.aegis.py` queue declarations grow an optional `budgets:` list:

```python
queues = {
    "impl": {
        "agent": "opus",
        "max_parallel": 2,
        "budgets": [
            {"usd": 1.00,             "window": "1h"},
            {"usd": 10.00,            "window": "24h"},
            {"output_tokens": 500000, "window": "1h"},   # runaway belt
            {"usd": 50.00,            "window": "7d"},
        ],
    },
    "fast": {
        "agent": "haiku-fast",
        "max_parallel": 4,
        # no `budgets:` → no caps; behaves as today
    },
}
```

**Each budget entry:**

- exactly one constraint: `usd` (`float` or `Decimal`, converted to
  `Decimal` at config-load) **xor** `output_tokens` (positive
  `int`);
- a required `window` string: `30m`, `1h`, `5h`, `24h`, `7d`, `1w`,
  `30d`. Suffixes: `m` (minutes), `h` (hours), `d` (days), `w`
  (weeks).

**All budgets must allow.** A queue admits a task iff every entry in
its `budgets:` list has `spent < limit` over its window. Empty
`budgets:` list (or omitted) means "no cap."

Validation runs at config-load:

- both `usd` and `output_tokens` set on the same entry → fail boot
- neither set → fail boot
- bad window string → fail boot
- duplicate `(constraint, window)` pair on the same queue → fail
  boot (no point declaring `$1/1h` twice)

## Cost source + price table

### Compute

When a worker terminates, `QueueManager._finalize` already collects
the final `SessionMetrics` (committed `c_in`/`c_out`/`c_cached`
counters present since v0.1). The cost-compute path looks
`(provider, model)` up off the `Queue` (resolved once at
config-load from the bound `agent_profile`'s `Agent.harness` /
`Agent.model`), so neither `Task` nor `SessionMetrics` grows new
fields:

```python
q = self._queues[task.queue]
cost = cost.compute(_metrics_adapter(session.metrics),
                    provider=q.provider, model=q.model)
status = "completed" if ok else "failed"
self._log(task.queue, {"event": status, ..., "cost": cost.as_dict()})
```

The `_metrics_adapter` maps `c_in → input_tokens`, `c_out →
output_tokens`, `c_cached → cache_hit_tokens`; `cache_write_tokens`
and `thinking_tokens` default to `0` because no current driver
surfaces them (defensive `getattr(..., 0)` in `compute()` handles
the gap). When/if a driver starts reporting them, the adapter
grows.

```python
@dataclass(frozen=True)
class Cost:
    usd:                Decimal
    input_tokens:       int
    output_tokens:      int
    cache_hit_tokens:   int
    cache_write_tokens: int
    thinking_tokens:    int

    def as_dict(self) -> dict:
        # Decimal -> str to survive JSONL round-trip without float drift
        return {"usd": str(self.usd),
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cache_hit_tokens": self.cache_hit_tokens,
                "cache_write_tokens": self.cache_write_tokens,
                "thinking_tokens": self.thinking_tokens}
```

`cost.compute(metrics, provider, model)`:

- looks up `PRICES[(provider, model)]` → `ProviderPrices` row;
- computes `Decimal(metrics.X) * row.X_rate / Decimal(1_000_000)`
  for each token class (`input`, `output`, `cache_hit`,
  `cache_write`, `thinking`);
- sums to a single `usd: Decimal`;
- returns `Cost(...)`.

Unknown `(provider, model)` raises `UnknownPriceError`. Fail-loud at
dispatch time, propagated to the calling agent identically to a
budget rejection. Operator updates the table when providers change
prices.

### Price table

Lives at `src/aegis/budget/prices.py`. Plain Python dict, keyed by
`(provider, model)`, values are per-million-token rates in USD:

```python
from decimal import Decimal
from dataclasses import dataclass

@dataclass(frozen=True)
class ProviderPrices:
    input:       Decimal   # USD per million input tokens
    output:      Decimal
    cache_hit:   Decimal
    cache_write: Decimal
    thinking:    Decimal

PRICES: dict[tuple[str, str], ProviderPrices] = {
    ("claude-code", "opus"): ProviderPrices(
        input=Decimal("15.00"), output=Decimal("75.00"),
        cache_hit=Decimal("1.50"), cache_write=Decimal("18.75"),
        thinking=Decimal("75.00")),
    ("claude-code", "sonnet"): ProviderPrices(
        input=Decimal("3.00"), output=Decimal("15.00"),
        cache_hit=Decimal("0.30"), cache_write=Decimal("3.75"),
        thinking=Decimal("15.00")),
    ("claude-code", "haiku"): ProviderPrices(
        input=Decimal("1.00"), output=Decimal("5.00"),
        cache_hit=Decimal("0.10"), cache_write=Decimal("1.25"),
        thinking=Decimal("5.00")),
    ("gemini",   "gemini-3-pro"): ProviderPrices(...),
    ("opencode", "kimi-k2.6"):    ProviderPrices(...),
    # ...
}
```

`Decimal` throughout — no float drift after thousands of round-trips.
Operator updates the table when providers change prices; this is the
one piece of maintained data the feature depends on, and it lives in
one file.

## Enforcement — evaluator + rejection shape

### `evaluator.evaluate_budgets(jsonl_tail, budgets, now) -> Decision`

A pure function. Inputs:

- `jsonl_tail`: list of parsed `completed`/`failed` records from
  `.aegis/state/queues/<queue>.jsonl`, most recent first; the caller
  only needs to pass entries newer than the longest configured
  window (the evaluator further filters per-budget).
- `budgets`: the queue's parsed `Budget` list.
- `now`: a `datetime`, injected for testability (FakeClock).

Outputs:

```python
@dataclass(frozen=True)
class BudgetCheck:
    constraint:    str              # "usd" or "output_tokens"
    limit:         Decimal
    spent:         Decimal
    window:        str              # verbatim from config: "1h" / "24h" / ...
    window_start:  datetime         # now - parsed_window
    allowed:       bool             # spent < limit
    headroom:      Decimal          # limit - spent (negative when over)
    unblock_at:    datetime | None  # earliest ETA at which spent will drop
                                    # below limit; None when allowed

@dataclass(frozen=True)
class Decision:
    allowed:    bool                       # True iff every check.allowed is True
    checks:     list[BudgetCheck]          # one per budget, declaration order
    blocked_by: list[BudgetCheck]          # filtered: not allowed
    unblock_at: datetime | None            # max of blocking checks' unblock_at
```

**`unblock_at` semantics.** When a budget is blocking, walk the
contributing records in age order (oldest first). Each record will
"age out" of the window at `record.completed_at + parsed_window`.
Find the earliest such time at which the cumulative-remaining sum
drops below `limit`. Approximate but useful — gives the rejected
caller a concrete ETA. For the queue-level `Decision.unblock_at`,
take the `max` across blocking checks.

### Rejection error shape

Returned by `QueueManager.enqueue` when any budget is blocking:

```json
{
  "error":   "queue 'impl' over budget",
  "queue":   "impl",
  "blocked_by": [
    {"constraint": "usd",
     "limit": "1.00", "spent": "1.23",
     "window": "1h",
     "unblock_at": "2026-05-25T18:42:00Z"},
    {"constraint": "output_tokens",
     "limit": "500000", "spent": "612340",
     "window": "1h",
     "unblock_at": "2026-05-25T19:10:00Z"}
  ],
  "unblock_at": "2026-05-25T19:10:00Z"
}
```

Same shape across **MCP** (`aegis_enqueue` returns the dict),
**HTTP** (`POST /remote/v1/enqueue` returns status `429` with the
body), **workflow engine** (`engine.enqueue(...)` raises
`BudgetExceeded(decision)` so workflow Python can `try/except`),
**scheduler** (the workflow tick fails; `fire_failed` JSONL record
carries the reason; next cron tick retries — self-correcting as
costs age out of the window), **CLI** (printed as a table on stderr,
non-zero exit).

One error contract, five caller surfaces.

### In-flight cost

A worker that's currently running has no `completed`/`failed` record yet, so
its cost isn't counted in the budget until completion. v1 ignores
in-flight: if a queue is capped at `$1/hour` with `max_parallel: 3`,
three opus workers can start at `$0.99 spent`, finish at `$4.50
total`, and the next dispatch is blocked for one hour.

Acceptable for v1 (consistent with "gate at enqueue" — the gate runs
on what's *recorded* at that moment). If the miss rate matters in
practice, v2 adds a pre-flight estimate.

## MCP surface

One new tool, follows the v0.8 `target=None` local / `target="<peer>"`
cross-host pattern:

```python
aegis_budget_status(*,
                    queue: str | None = None,
                    target: str | None = None,
                    from_handle: str) -> dict
# queue=None: all queues on the targeted serve, summary shape:
#   {"queues": [{name, budgets_count, spent_summary, status}, ...]}
# queue="<name>": full Decision for that queue (same shape as a
#   rejection's blocked_by, but with checks for ALL budgets, not
#   just blocking ones).
# target=None local; target="<peer>" routes through /remote/v1/budget.
```

Agents call this *before* an expensive enqueue to decide whether to
wait or route elsewhere. The workflow engine grows a mirror method:

```python
engine.budget_status(queue, *, target=None) -> Decision
```

So workflow Python can branch on budget headroom without bouncing
through the MCP layer.

## HTTP surface

Two new read-only endpoints on the remote plane, gated by the same
`accept_tokens` / `accept_from` already used for `/enqueue`,
`/callback`, `/schedule`:

```
GET /remote/v1/budget
Response 200:
  {"queues": [
     {"name": "impl",   "budgets_count": 4, "status": "blocked",
      "binding": "usd $1.23 of $1.00 / 1h",
      "unblock_at": "2026-05-25T19:10:00Z"},
     {"name": "review", "budgets_count": 2, "status": "ok",
      "binding": "$4.10 of $10.00 / 24h"},
     {"name": "fast",   "budgets_count": 0, "status": "no-budget"}
   ]}

GET /remote/v1/budget/<queue>
Response 200:                            # full Decision shape
  {"name": "impl", "allowed": false,
   "checks": [<BudgetCheck>, <BudgetCheck>, ...],
   "blocked_by": [<BudgetCheck>, ...],
   "unblock_at": "2026-05-25T19:10:00Z"}
Response 404 when no such queue
```

Read-only. No PUT/DELETE — budget configuration is Python-authored
in `.aegis.py`, not pushable over the wire.

## CLI surface

New `aegis budget` subapp, mirroring `aegis schedule`:

```bash
aegis budget list                          # one-line summary per queue
aegis budget show impl                     # full Decision: every check
                                           # with spent/limit/headroom/unblock_at
aegis budget list --remote vps             # peer's summary
aegis budget show --remote vps impl        # peer's full Decision
```

`list` prints a table:

```
QUEUE   BUDGETS  BINDING                       STATUS
impl    4        $1.23 of $1.00 / 1h           ⛔ over (unblocks 18:42Z)
review  2        $4.10 of $10.00 / 24h         ✓ ok
fast    0        —                             — no budget
```

`show` prints every check (allowed and blocking) so the operator
sees the full headroom picture.

## TUI surface

Extends the existing v0.4 queue dashboard:

- **Always-on strip** above the status bar grows a budget cell when
  *any* configured budget for any queue drops below 20% headroom:
  `impl: ⚠ $0.88/$1.00 1h`. When a constraint trips:
  `impl: ⛔ $1.23/$1.00 1h · unblocks 18:42Z`.
- **`Ctrl+D` modal** gets a `BUDGETS` band (between `IN-FLIGHT` and
  `QUEUED`) listing every queue's binding check (or the
  most-pressured one if all are healthy).

No alerts, no notifications, no audible signal. Pure read surfaces;
the dispatch-time error remains the only "loud" signal.

## File layout

```
src/aegis/budget/                            (new package)
  __init__.py
  prices.py        # PRICES dict + ProviderPrices dataclass
  cost.py          # Cost dataclass + compute(metrics, provider, model)
  evaluator.py     # Budget, BudgetCheck, Decision dataclasses;
                   # evaluate_budgets(jsonl_tail, budgets, now)
  windows.py       # parse_window("1h") -> timedelta; validator
src/aegis/cli_budget.py                      (new — aegis budget subapp)
```

Modified:

- `src/aegis/queue/manager.py` — `enqueue()` calls
  `evaluator.evaluate_budgets()`; `_finalize()` adds `cost` to the
  `completed`/`failed` JSONL record.
- `src/aegis/queue/schema.py` (or wherever the queue config dataclass
  lives) — `Queue` grows `budgets: list[Budget]`.
- `src/aegis/config.py` — parse `budgets:` from queue dicts in
  `.aegis.py`; validate via `windows.parse_window` + the
  one-constraint-per-entry rule.
- `src/aegis/mcp/server.py` — new `aegis_budget_status` tool;
  `aegis_enqueue`'s docstring notes the budget-rejection shape.
- `src/aegis/remote/plane.py` — `GET /remote/v1/budget` and
  `GET /remote/v1/budget/<queue>` endpoints.
- `src/aegis/remote/client.py` — `remote_budget_list()`,
  `remote_budget_show()`.
- `src/aegis/cli.py` — mount the `aegis budget` subapp.
- `src/aegis/workflow/engine.py` — `engine.budget_status()` mirror.

## Testing

- **Unit.**
  - `cost.compute()` against fixture metrics for every entry in
    `PRICES`; `Decimal` precision after 1000 round-trips; unknown
    `(provider, model)` raises `UnknownPriceError`.
  - `windows.parse_window()` accepts every documented suffix; rejects
    unknowns + negative values + zero.
  - `evaluator.evaluate_budgets()` against fixture JSONL tails:
    single-budget allow, single-budget block, multi-budget all-allow,
    multi-budget partial-block, multi-budget all-block, empty
    `budgets`, exact-edge-of-window timestamps, future timestamps
    (treat as 0 contribution), recently-aged-out costs
    (allowed-now), `unblock_at` math against a known input series.
- **Integration.**
  - `QueueManager` with a fixture session manager whose worker emits
    a configured cost. Enqueue tasks until the budget trips; assert
    the rejection shape; advance FakeClock past the window; assert
    the next enqueue is admitted.
  - Multi-budget all-must-allow: trip the 1h budget but not the 24h;
    assert `blocked_by` is just the 1h. Trip both; assert two-entry
    `blocked_by` + `unblock_at = max`.
  - `output_tokens` belt: USD budget allowing, output-tokens belt
    blocking → rejected with belt named.
- **Plane endpoint.** `GET /remote/v1/budget` and
  `GET /remote/v1/budget/<queue>` against a hermetic plane fixture;
  auth gating same as `/enqueue` and `/callback`.
- **MCP.** `aegis_budget_status` for `target=None` (local) and
  `target="<peer>"` (routed through the client). Plus
  `aegis_enqueue` rejection-shape regression: enqueue into an
  over-budget queue, assert the returned dict matches the contract.
- **CLI.** Snapshot the `aegis budget list` table format; `aegis
  budget show impl` parses round-trip.
- **Live (`@pytest.mark.live`).** Opt-in: configure a small budget
  on a real queue, fire two workers that exceed it, assert the third
  enqueue is rejected over the wire with the right shape.

## Implementation notes

- **JSONL tail loading.** `evaluator.evaluate_budgets()` is pure;
  the caller (`QueueManager.enqueue`) is responsible for loading the
  tail. For O(1) hot path, `QueueManager` keeps an in-memory deque
  of recent `(completed_at, cost)` tuples per queue, sized to the
  longest configured window. On `_finalize`, append; on `enqueue`,
  filter to per-window and pass to the evaluator. JSONL on disk is
  authoritative; the deque is rebuilt from JSONL on `start()` (the
  existing replay path).
- **Decimal precision.** Use `Decimal` end-to-end. `usd` field in
  JSONL is serialized as a string (`"0.0421"`). `evaluator` parses
  back to `Decimal` on load. No floats anywhere on the cost path.
- **Cost on failed workers.** A worker that finishes `failed` (worker
  errored) or `interrupted` (substrate killed it) still consumed
  tokens up to that point. v1 records cost for all three termination
  outcomes — failure isn't free. Operator-visible in `show`.
- **Hot reload of budget config.** `.aegis.py` is Python; reload
  re-evaluates the module. Mid-flight budget changes apply to the
  next enqueue; in-flight workers are unaffected (they're already
  past the gate).

## Open questions

1. **Budget on workflow `engine.enqueue` vs raw MCP `aegis_enqueue`.**
   Both call into `QueueManager.enqueue`, so both surface the
   rejection identically. Should `BudgetExceeded` be a *typed* Python
   exception (cleaner workflow code) or just a dict the workflow has
   to `if "error" in result:`? Lean typed exception — that's what
   `engine.enqueue` returns today on local-queue failures.
2. **Pricing for ACP-driven providers (Gemini, OpenCode).** The
   metrics those drivers expose may not split cache_hit / cache_write
   identically to Claude. The price table can carry per-provider
   defaults (e.g., Gemini lumps cache into input); the compute step
   uses whatever fields are present and zeros out the rest. Confirm
   the per-provider metrics shape against current
   `gemini_parse.py` / `opencode_parse.py` before coding.
3. **Budget for the workflow runner itself.** The scheduler triggers
   workflow runs that may or may not enqueue tasks. The runner's
   *own* token spend (the agent doing the orchestration) isn't
   queue-bound. v1 leaves the orchestrator un-budgeted — only the
   downstream worker enqueues count. v2 could introduce a
   "workflow:" pseudo-queue with its own budget if this becomes a
   leak.
