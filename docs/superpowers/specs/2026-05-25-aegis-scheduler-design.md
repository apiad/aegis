---
title: Aegis Scheduler — design
date: 2026-05-25
status: draft
---

# Aegis Scheduler — design

The final substrate rung. Aegis already has **queues** (inter-agent
delegation, capped concurrency, prompt + agent currency) and
**workflows** (`@workflow` decorator + engine primitives). This spec adds
**schedules** — a third substrate that fires workflows on a cron-like
cadence — and rides on a wider migration of aegis configuration from
`.aegis.py` (Python) to `.aegis.yaml` (declarative) + `.aegis/plugins/`
(auto-imported Python side effects).

Shipping the scheduler completes the move of Alex's autonomous job
substrate (`vault/+/jobs/` + `job-crawler.timer`) into aegis itself.
After this lands, the external substrate is retired.

## Goals

1. **Scheduled workflow execution.** Cron-style and one-shot fires that
   invoke any registered `@workflow` with declared kwargs. The currency
   of a schedule is `(workflow, args)`; the currency of a queue stays
   `(prompt, agent)`. The two substrates compose through built-in
   workflows — schedules never duplicate queue concerns.
2. **Declarative config.** Move all static configuration (agents,
   queues, schedules, workflow registration) into `.aegis.yaml`. Allow
   user-space Python side effects via `.aegis/plugins/*.py`
   auto-import.
3. **Hot-reload for data.** Edits to `.aegis.yaml` (and only
   `.aegis.yaml`) take effect live; Python code changes
   (`.aegis/plugins/`, built-in workflow source) require an `aegis
   serve` restart.
4. **First-class observability.** The `Ctrl+D` ops console becomes
   tabbed (`Queues | Schedules`). The schedules tab mirrors the queue
   dashboard shape so muscle memory transfers.
5. **Retire `vault/+/jobs/`.** Once aegis schedules carry Alex's
   recurring routines for ~3 days without incident on the VPS, the
   external substrate is disarmed and `job-crawler.timer` is stopped.

## Non-goals

- Migration tooling. `.aegis.py` is removed without a back-compat shim;
  Alex's only consumer rewrites its config once.
- Cross-process schedule federation. v1 assumes a single `aegis serve`
  per host; multi-host federation is left for later.
- Concurrency caps at the scheduler level. If a use case wants a cap,
  it dispatches the cap through the queue substrate via the `enqueue`
  built-in workflow. No new knob in the scheduler.

## Architecture

A new `Scheduler` substrate sits inside `aegis serve` as a peer to the
existing `QueueManager` and `InboxRouter`. It owns:

- An `asyncio.Task` tick loop (default 60s cadence, configurable via
  `scheduler.tick_seconds`).
- A fire-eligibility resolver that walks the loaded schedule table on
  each tick.
- A JSONL lifecycle log per schedule at
  `.aegis/state/schedules/<name>.jsonl`, plus a derived runtime
  snapshot at `.aegis/state/schedules.snapshot.json` (rebuildable on
  boot).

A fire dispatches the named workflow via the existing
`runner.run_workflow`. Schedules are workflow producers — they do not
duplicate the spawn / send / drain plane. **Queue substrate stays
untouched.**

### Substrate composition

| Substrate | Cadence       | Currency           | Concurrency model         |
|-----------|---------------|--------------------|---------------------------|
| Queue     | producer-driven | prompt + agent   | `max_parallel` cap, FIFO |
| Schedule  | cron / fire_at | workflow + args    | per-entry `on_overlap`    |

Cross-substrate collaboration is expressed through two pre-made
workflows that ship with aegis:

- `prompt(agent, text)` — direct spawn (`engine.spawn → send → drain →
  return final text → close`). Uncapped.
- `enqueue(queue, payload, callback=False)` — wraps `aegis_enqueue` as
  a workflow. Inherits the queue's `max_parallel`.

That gives:

- **Uncapped scheduled fire**: `schedule → workflow: prompt`.
- **Capped scheduled fire**: `schedule → workflow: enqueue → queue →
  prompt-via-worker`.

Schedules know nothing about queues; queues know nothing about
schedules; the workflow plane is the only place they meet.

## File layout

```
.aegis.yaml                          # all declarative config (single source of truth)
.aegis/plugins/*.py                  # auto-imported at boot; do whatever
                                     #   (today: register @workflow; later: hooks,
                                     #   MCP tools, theme overrides, …)
.aegis/state/schedules/<name>.jsonl  # per-schedule lifecycle log
.aegis/state/schedules.snapshot.json # derived runtime view (rebuildable from JSONL)
.aegis.py                            # ← removed, no back-compat shim
```

`src/aegis/workflows/builtins/` holds the shipped workflows (`prompt`,
`enqueue`, `tdd_step`). Each is opt-in via the `workflows:` list in
`.aegis.yaml` — unlisted built-ins stay dormant so the surface area
never expands without intent.

## `.aegis.yaml` shape

```yaml
default_agent: claude

agents:
  claude:
    provider: claude-code
    model: opus
    effort: high
    permission: auto
  gemini-flash:
    provider: gemini-cli
    model: gemini-3-flash-preview
    permission: full

queues:
  tasks:
    agent: gemini-flash
    max_parallel: 2

workflows: [prompt, enqueue, tdd_step]

plugin_dirs: [.aegis/plugins]

scheduler:
  tick_seconds: 60
  default_timezone: America/Havana

schedules:

  end-of-day:
    workflow: prompt
    args:
      agent: claude
      text: |
        You are running the 10 PM end-of-day routine. Three outputs:
        today's Summary, a task audit, and tomorrow's Plan...
    cron: "0 2 * * *"
    timezone: America/Havana
    lifecycle: forever
    on_overlap: skip
    timeout: 1800
    enabled: true
    notify:
      on_failure: true
      on_success: false

  one-shot-experiment:
    workflow: prompt
    args:
      agent: claude
      text: "Compile the Q2 source list and write it to vault/x/q2-sources.md"
    fire_at: "2026-05-26T14:00:00Z"
    lifecycle: once
```

### Field reference

**Top-level schedule entry:**

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `workflow` | yes | — | Name of a registered `@workflow`. Boot-validated. |
| `args` | no | `{}` | Passed as `**kwargs` to the workflow function. |
| `cron` | one of {`cron`, `fire_at`} | — | Standard 5-field cron expression. |
| `fire_at` | one of {`cron`, `fire_at`} | — | ISO-8601 timestamp for one-shot. |
| `timezone` | no | `scheduler.default_timezone` | IANA tz name. |
| `lifecycle` | no | `forever` | `forever` \| `once` \| `{fires: N}` \| `{until: <iso>}`. |
| `on_overlap` | no | `skip` | `skip` \| `queue` \| `kill`. |
| `timeout` | no | `1800` | Seconds. Past this the workflow Task is cancelled. |
| `enabled` | no | `true` | Toggleable from the dashboard. |
| `notify.on_failure` | no | `true` | Ping Telegram if configured. |
| `notify.on_success` | no | `false` | Ping Telegram if configured. |

`cron` and `fire_at` are mutually exclusive — a one-shot is just
`fire_at: <iso>` with `lifecycle: once` implied.

## Runtime behaviour

### Tick loop

A single `asyncio.Task` named `scheduler_tick`. Wakes every
`scheduler.tick_seconds`. On each wake:

1. **Snapshot fire-eligible entries.** Walk the loaded schedule table.
   An entry is fire-eligible iff:
   - `enabled: true`
   - `lifecycle` not exhausted
   - `next_fire ≤ now`
   - Not already in-flight, OR `on_overlap != skip`
2. **Dispatch each eligible entry.** For each, fork an `asyncio.Task`
   that:
   - Writes `fire_requested` to JSONL with a fresh ULID `task_id`,
     `manual: false`, `backfilled: <bool>`.
   - Calls `runner.run_workflow(workflow_name, args,
     caller_handle=None)` under an `asyncio.wait_for` deadline of
     `timeout` seconds.
   - On completion, writes `fire_completed` (status `ok`) or
     `fire_failed` (status `failed:workflow` /
     `failed:crash` / `failed:timeout`).
   - Computes the next `next_fire` from cron + timezone and updates
     the in-memory snapshot.
3. **Persist snapshot.** Flush
   `.aegis/state/schedules.snapshot.json` atomically
   (write-tmp + rename) after the tick. The dashboard reads this for
   "next fire in N minutes" without re-parsing JSONL.

### `on_overlap` policies

- `skip` — the new fire is dropped; JSONL records `skipped:overlap`,
  no workflow invocation.
- `queue` — the new fire is appended to an in-memory FIFO keyed by
  schedule name; it runs as soon as the prior fire terminates. (Not
  the queue substrate — a tiny per-schedule deferral list, no
  cross-schedule fairness.)
- `kill` — the prior fire's task is cancelled, its spawned agent is
  force-closed, JSONL records `failed:killed`, and the new fire
  starts immediately.

### `lifecycle` exhaustion

- `forever` — never exhausts.
- `once` — after the first terminal event (`fire_completed` or
  `fire_failed`), runtime state flips to `completed`; entry stays in
  YAML; dashboard greys it; no further fires.
- `{fires: N}` — after `fire_count == N` terminal events, same as
  `once`.
- `{until: <iso>}` — tick refuses to dispatch if `now > <iso>`,
  regardless of `fire_count`.

Aegis **never mutates `.aegis.yaml`** to mark exhaustion. Lifecycle
state lives in `.aegis/state/schedules.snapshot.json`. The one narrow
exception is `Space` (pause/resume) in the TUI, which edits
`enabled:` in place using `ruamel.yaml` to preserve comments.

### On-boot replay

When `aegis serve` starts:

1. Read every `.aegis/state/schedules/<name>.jsonl` to find each
   schedule's most recent terminal event and `fire_count`.
2. Any `fire_requested` without a matching terminal event → write a
   synthetic `fire_failed` with `status: failed:interrupted` (same
   posture as the queue substrate).
3. Compute `next_fire` for every loaded schedule from
   `cron`/`fire_at` + timezone.
4. Any `next_fire ≤ now` is fire-eligible on the next tick — the
   first tick after boot does the backfill, **once per past-due
   schedule regardless of how many windows were missed**. This is
   Q5/ii: "exactly once".

The JSONL record on a backfill carries `backfilled: true` so the
dashboard can surface "missed-fire-recovered" events distinctly from
normal cron fires.

### JSONL record shape

```json
{"ts":"2026-05-25T02:00:00Z","schedule":"end-of-day","event":"fire_requested",
 "task_id":"01HXY…","manual":false,"backfilled":false}
{"ts":"2026-05-25T02:14:33Z","schedule":"end-of-day","event":"fire_completed",
 "task_id":"01HXY…","status":"ok","duration_s":873,
 "result_excerpt":"Summary written to vault/Calendar/Summaries/summary-2026-05-25.md"}
```

`event` ∈ `{fire_requested, fire_completed, fire_failed,
skipped:overlap}`.
`status` (only present on terminal events) ∈
`{ok, failed:workflow, failed:crash, failed:timeout,
failed:interrupted, failed:killed}`.

## Hot reload

A filesystem watcher on `.aegis.yaml`. On change:

1. Parse the new YAML in isolation.
2. Validate: agent / queue / workflow references resolve; cron strings
   parse; lifecycle / on_overlap / notify values are in their enums.
3. **Atomic swap.** Either the whole new config replaces the in-memory
   snapshot, or the reload is rejected entirely. Never a partial
   reload.
4. On reject: write `reload_failed` to a top-level
   `.aegis/state/aegis_events.jsonl` with the validation error, beep
   the dashboard, keep the prior in-memory snapshot.

For each schedule that exists in both old and new:
- If `cron`/`fire_at`/`timezone` changed, recompute `next_fire`.
- If `enabled` flipped to `false` mid-flight, the in-flight fire
  completes; no new fire is queued.
- If `enabled` flipped to `true`, schedule re-enters the eligibility
  loop on the next tick.

For schedules added: register, compute `next_fire`, ready for next
tick.

For schedules removed: any in-flight fire completes normally; runtime
state is retained in JSONL but the schedule no longer appears in the
table or the dashboard.

**Code does not hot-reload.** Edits to `.aegis/plugins/*.py` or
`src/aegis/workflows/builtins/*.py` require `aegis serve` restart. The
dashboard surfaces a "plugin file modified since boot" warning so
Alex remembers to cycle the service.

## TUI surface

### `Ctrl+D` becomes tabbed

```
┌─ Ctrl+D · ops console ────────────────────────────────────────────────────────┐
│  [ Queues ]  ▶ Schedules ◀                                                    │
│                                                                                │
│  SCHEDULES (5 enabled · 2 paused)                  ┃ DetailPanel               │
│    ▸ end-of-day      cron 0 2 * * *  next  06:14   ┃                          │
│      claude-private  cron 0 * * * *  next  in 12m  ┃ Schedule: end-of-day      │
│      briefing        cron 0 11 * * * next  09:14   ┃ Workflow: prompt          │
│      bug-hunter      cron 0 3 * * 1  paused        ┃ Cron: 0 2 * * * (Havana)  │
│      one-shot-test   fire 2026-05-26T14:00 done    ┃ Lifecycle: forever        │
│                                                    ┃ Last:  ok · 14m · 02:00   │
│  IN-FLIGHT (1)                                     ┃ Fires: 14   Failures: 0   │
│    claude-private    ▸ lucid-knuth · 00:04:12      ┃                          │
│                                                    ┃ Assistant tail:           │
│  QUEUED (0)                                        ┃   "Today I worked on…"    │
│                                                    ┃   "…end-of-day complete." │
│                                                    ┃                          │
│  RECENT (last 10)                                  ┃                          │
│    end-of-day        ok    14m   02:00:00          ┃                          │
│    claude-private    ok     3m   00:04:00          ┃                          │
│    bug-hunter        fail  ↻    yesterday 03:00    ┃                          │
└────────────────────────────────────────────────────────────────────────────────┘
```

**Keybindings:**

- `Shift+Tab` — cycle top-level tabs (`Queues` ↔ `Schedules`).
- `Tab` — cycle bands inside the active tab.
- `↑↓` — move cursor inside focused band.
- `Enter` — refresh details.
- `>` — jump to the worker's TUI tab if one exists.
- `Space` — toggle `enabled: true|false` in `.aegis.yaml` for the
  cursored schedule. `ruamel.yaml` preserves comments and key order.
- `F` — fire-now via the manual-fire path (Q8/A: same JSONL flow,
  `manual: true` tag).
- `E` — open `$EDITOR` on `.aegis.yaml` at the cursored schedule's
  line.
- `Esc` — close the modal.

The always-on strip above each conversation's status bar stays
queue-only. Schedules tick too slowly for a strip to be informative
(stale "next fire in 6h" 95% of the time).

## CLI surface

```
aegis schedule list                    # tabular: name · cron · next · status · fires
aegis schedule show <name>             # full config + last 10 fires
aegis schedule run <name>              # fire-now via manual-fire path (returns when done)
aegis schedule disable <name>          # sets enabled: false in .aegis.yaml
aegis schedule enable <name>           # sets enabled: true
aegis schedule logs <name> [--tail N]  # JSONL read, pretty-printed
```

No `add` / `rm` / `edit` subcommands — schedule authoring is "edit
the YAML, let hot-reload pick it up". This keeps the on-disk state
and the in-memory state from drifting.

## Failure modes

| Failure | Behaviour |
|---|---|
| Workflow raises `WorkflowError` | JSONL `fire_failed`, `status: failed:workflow`, notify if `on_failure`. Schedule continues. |
| Workflow raises unexpected `Exception` | `fire_failed`, `status: failed:crash`, full traceback stored in JSONL. Aegis itself doesn't crash. |
| Timeout exceeded | Workflow Task is `cancel()`-ed, spawned agent is `engine.close`-ed, `fire_failed`, `status: failed:timeout`. |
| `.aegis.yaml` reload invalid | Whole reload rejected. Prior in-memory snapshot retained. Logged to `.aegis/state/aegis_events.jsonl`. Dashboard beeps. |
| Plugin module raises at import | `aegis serve` refuses to boot. Fail loud — matches existing unknown-agent posture. |
| `aegis serve` crashes mid-fire | On next boot, dangling `fire_requested` records → marked `failed:interrupted`. Backfill-once policy fires past-due schedules on first tick. |
| JSONL corruption (incomplete tail line) | Malformed tail logged + skipped; snapshot rebuilt from valid records. No data-loss-on-startup. |
| Two `aegis serve` processes accidentally running | Systemd `Type=exec` + MCP HTTP port bind prevents second process. Belt-and-suspenders: scheduler acquires advisory file lock at `.aegis/state/scheduler.lock` before first tick. |

## Testing strategy

**Hermetic (`uv run pytest -q -m "not live"`).** A `FakeClock` injected
into `Scheduler` lets tests advance time deterministically. Coverage
targets:

- Cron parsing and `next_fire` computation across timezones.
- Each `lifecycle` form's exhaustion behaviour.
- Each `on_overlap` policy (skip / queue / kill).
- Backfill-once on boot replay.
- Plugin discovery (positive: registered; negative: import error
  blocks boot).
- YAML reload — atomic swap on success, full reject on validation
  failure.
- JSONL append + boot replay round-trip (including a corrupted tail
  line).
- `notify.on_failure` / `on_success` plumbing (the notifier is
  stubbed; assert it was / wasn't called).

The workflow runner is stubbed in hermetic tests — no subprocess
spawns. Each fire returns a fixture string.

**Live (`uv run pytest -m live`).** One end-to-end test:

- Drop a `prompt` workflow schedule with `cron: "* * * * *"` and
  `tick_seconds: 5` into a fixture `.aegis.yaml`.
- Run `aegis serve` for ~90s in a subprocess.
- Assert ≥1 `fire_completed` record reached the JSONL and the
  worker's final text shows up in `result_excerpt`.
- Auto-skip when `claude` is off PATH (matches the existing
  `tests/test_queue_live.py` pattern).

## Rollout

1. **Land VS1-7** (see Implementation slices) — full feature set
   shipped to `main`.
2. **Deploy `aegis serve` as a `systemd --user` unit on the VPS.** The
   spec ships:
   - `scripts/aegis.service` — unit file with `Restart=on-failure`,
     `RestartSec=10s`, `Type=exec`.
   - `scripts/install-vps-service.sh` — one-shot installer that drops
     the unit, runs `loginctl enable-linger apiad`, and
     `systemctl --user enable --now aegis.service`.
3. **Author daily routines as aegis schedules.** Alex translates
   `vault/+/jobs/end-of-day.md`, `briefing.md`, `weekly.md`,
   `claude-private-tick.md`, etc. into `.aegis.yaml` entries by hand
   (no migration tool — the bodies are different enough that
   automated conversion would mis-encode lifecycle and on_overlap
   intent).
4. **Run both substrates in parallel for ~3 days.** Both fire the
   same routines; outputs are compared.
5. **Disarm the external substrate.** `vault/+/jobs/*.md` get
   `status: cancelled`; `systemctl --user disable --now
   job-crawler.timer` on the VPS. The job-crawler code stays in the
   workspace repo as historical reference but no longer runs.

On zion (the laptop), `aegis serve` is **not** installed as a
service. The scheduler only ticks while serve is running, which is
fine for a laptop — the daily routines live on the VPS.

## Implementation slices

Vertical slices — each ships to `main`, the dashboard reflects it
incrementally, each is end-to-end testable on its own.

1. **VS1: Config migration.** `.aegis.yaml` parser; plugin auto-import
   from `.aegis/plugins/*.py`; `aegis serve` boots from YAML.
   `.aegis.py` removed. No scheduler yet. End-to-end test: existing
   queue tests pass against YAML config.
2. **VS2: Built-in workflows.** `prompt(agent, text)` and
   `enqueue(queue, payload, callback)` shipped in
   `src/aegis/workflows/builtins/`. Registered via YAML `workflows:`
   list. `aegis workflow run prompt --agent=claude --text=…` works.
3. **VS3: Minimum-viable scheduler.** Tick loop, cron parsing, JSONL
   log, snapshot. Only `cron` triggers, only `lifecycle: forever`,
   only `on_overlap: skip`. Fires `prompt` workflows. Live test
   against `claude`.
4. **VS4: Full schedule semantics.** `fire_at`,
   `lifecycle: once|{fires:N}|{until:<iso>}`, `on_overlap: queue|kill`,
   `timeout`, `notify`, backfill-once, on-boot replay.
5. **VS5: TUI integration.** Tabbed `Ctrl+D` ops console; Schedules
   tab with bands + actions (`Space`, `F`, `E`, `>`,
   `Shift+Tab`).
6. **VS6: CLI surface.** `aegis schedule list/show/run/enable/disable/logs`.
7. **VS7: Hot reload.** Filesystem watcher on `.aegis.yaml`;
   atomic-swap-or-reject.
8. **VS8: Rollout polish.** Systemd unit + installer script. Docs
   page on the public roadmap.

Estimate at Claude-Code-with-Alex pace: VS1-3 each ~half-day, VS4 ~1
day, VS5 ~half-day, VS6-8 ~half-day combined. Total ~3-4 days of
focused work.

## Open questions

None blocking. Two we may revisit after VS3 lands:

- **Per-schedule plugin scoping.** Today every plugin is global. If
  Alex starts writing schedule-specific helpers, a
  `plugin: <module>` field on a schedule entry could scope imports.
  Deferred.
- **Cross-host federation.** If aegis later runs on both zion and VPS
  with shared state, deciding *which* host owns a given schedule
  needs design. Out of scope for v1.
