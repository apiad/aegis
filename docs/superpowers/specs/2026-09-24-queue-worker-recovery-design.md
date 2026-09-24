# A dead queue worker should be resumable, not a condolence note

**Status:** analysed 2026-09-24, not implemented. Every claim below was checked
against `main` at `0e403a9` and against the live state dir on zion.
**Scope:** `src/aegis/queue/manager.py`, `src/aegis/queue/inbox.py`, the two
brain-boot paths in `src/aegis/cli.py` and `src/aegis/tui/app.py`, and one new
config key per queue. Nothing about `AgentSession`, the drivers or the MCP
surface changes — the resume machinery this needs already exists and is already
used for every ordinary tab.

## The symptom

A queue worker dies and its work is gone. The producer gets an error string, the
tab disappears, and nothing anywhere can pick the task back up.

## Three causes, stacked

They stack: fixing the top one alone buys a log file and nothing else.

### 1. The persistence plane is not wired in either brain path

`QueueManager` and `InboxRouter` both take an optional `state_dir` and both
degrade to memory-only when it is `None`:

- `manager.py:182` — `_log()` returns immediately, so no `queues/<name>.jsonl`
  is ever written.
- `manager.py:774` — `start()` returns immediately, so the replay never runs.
- `inbox.py:47` — `deliver()` skips the JSONL writethrough, so even the failure
  callback is not durable.

Both long-lived constructions pass no `state_dir`:

| call site | what it is | `state_dir` |
|---|---|---|
| `cli.py:698`, `cli.py:708` | `_serve` — the daemon brain | no |
| `tui/app.py:650`, `tui/app.py:651` | the standalone TUI's own brain | no |
| `cli.py:1395`, `cli.py:1405` | `aegis workflow run` — one-shot, exits | yes |

The only path that persists is the one that cannot restart.

In `_serve` this is visibly an oversight rather than a decision: seventeen lines
below the two that forgot, `roots.state_dir` is handed to session persistence,
the claims registry, the canvas manager and the terminal manager in a row. Same
in `AegisApp._build_planes` — `CanvasManager(state_dir=self._state_dir)` and
`TerminalManager(state_dir=…)` sit directly under the two lines that pass
nothing.

Measured on zion, 2026-09-24: `.aegis/state/sessions/` holds 1144 transcripts,
queue traffic appears in sessions written today, and neither
`.aegis/state/queues/` nor `.aegis/state/inboxes/` exists. The persistence plane
has never written a byte in the workspace aegis is developed in.

The eight tests in `tests/test_queue_persistence.py` and
`tests/test_queue_e2e.py` pass because every one of them constructs
`QueueManager(…, state_dir=tmp_path)` by hand. The replay machinery is correct.
Nothing asserts that a brain ever hands it a directory.

### 2. Recovery is an apology, and resume was made impossible

Suppose the wiring is fixed. `start()` replay finds a `dispatched` task, calls
`_mark_interrupted`, and the whole recovery is: mark the task `failed`, log
`failed`, deliver the producer an error carrying whatever `last_text` the
`deferred` record happened to hold. The payload is on disk; nothing re-runs it.
There is no retry anywhere — `attempts`, `max_attempts` and `retry` appear
nowhere in `src/aegis/queue/` or `src/aegis/config/`.

Resuming rather than re-running is the obvious move and is one field away from
possible. `ClaudeDriver.supports_resume = True`, `ClaudeDriver.resume()` builds
`claude --resume <session_id>`, `SessionManager.spawn(resume_from=…)` threads it
through, and `plan_resume` uses exactly this to restore every ordinary tab at
boot. The queue cannot: its `dispatched` record carries `task_id` and
`worker_handle` and nothing else. The harness conversation id — latched on the
driver's first `SystemInit` and available as `session.session_id` for the whole
worker's life — is never written down.

There is an asymmetry worth naming. On a daemon restart the TUI's `plan_resume`
does not filter by origin, so the worker's *tab* comes back with its full
conversation while the queue has already declared its task failed and never
reattaches observers. The context is alive on screen and orphaned from the task.

### 3. A transient turn error is terminal, and the remedy is irreversible

`_finalize` keys the whole outcome off one comparison:

```python
ok = st is AgentState.ready
```

Anything else is `failed`, delivers an error callback, and then
`await self._sm.close(session.handle)`. `close()` removes the session from the
roster, revokes its MCP token and announces the removal, which drops the pane
and rewrites `workspace.json` without it. The conversation is no longer
reachable by any path aegis offers.

`AgentState.error` is not reserved for genuine failure. `session.py:876` sets it
for any `Result(is_error=True)`, `session.py:901` for a harness exception, and
`session.py:908` for a stream that ended with no `Result` at all. The first of
those includes `claude.py:233`, where a dropped SSH link to an execution host is
deliberately reported as `Result(is_error=True, stop_reason="link_lost")` —
chosen because it was the existing path to a red tab. It is also, unintentionally,
the path that destroys a queue worker's context when the tunnel to `vps` blips.

The codebase already accepts that a turn boundary is not a verdict on the task.
`_still_working` says so at length, and defers finalization while a worker waits
on a monitor. The same split has simply never been applied to *how* the turn
ended, only to whether the agent was waiting.

## The fix

Four changes, in dependency order. The first is prerequisite to the rest.

### 1. Hand both brain paths their state dir

`_serve`: `InboxRouter(state_dir=roots.state_dir)` and
`QueueManager(queues or {}, mgr, inbox, state_dir=roots.state_dir)`.
`AegisApp._build_planes`: the same with `self._state_dir`.

Gate it structurally rather than by one assertion. `src/aegis/core/planes.py`
already forces every new `attach_*` on `SessionManager` into an inventory; the
parallel rule is that a plane which takes a `state_dir` must receive one in
every brain path. Whatever the gate ends up being, break the wiring on purpose
and confirm it goes red before believing it — the existing tests are the
cautionary case, green for months against a plane production never built.

### 2. Write down what resume needs

Extend the `dispatched` record, or append a `worker_session` record when the id
first appears, carrying `session_id`, `agent_profile`, `cwd` and `host`. The
queue's `on_event` observer already sees every event and can latch `session_id`
off `SystemInit`; `_finalize` and the `deferred` path can read
`session.session_id` directly. Nothing below is possible without this, and it is
the cheapest of the four.

### 3. Split "the turn failed" from "the task failed"

Classify the end state in `_finalize` instead of comparing it to `ready`:

- **transient** — `link_lost`, a harness exception, a stream with no `Result`,
  a provider rate limit. Do not close the session. Log `stalled` with
  `last_text` and `session_id`, leave the task `dispatched`, and tell the
  producer the worker stalled and is recoverable rather than that it failed.
  Bound this by a per-queue `max_attempts` (default 2) so a wedged worker
  cannot hold its `max_parallel` slot forever.
- **terminal** — attempts exhausted, cancelled, over budget. Today's behaviour,
  unchanged.

Model it on `_still_working`: same shape, same reason, different question.

### 4. Make "continue it" a thing that exists

- **Automatic, on restart.** For a `dispatched` task with a recorded
  `session_id` and a resume-capable provider, `start()` re-spawns the worker
  with `resume_from=<session_id>` plus one nudge turn ("your aegis session was
  interrupted mid-task; continue"), and re-attaches observers. Fall back to
  `failed: interrupted` only when resume is genuinely impossible — no session
  id, driver without `supports_resume`, or attempts exhausted.
- **Manual, always.** `aegis queue resume <task_id>` and a matching
  `aegis_task_resume(task_id)` MCP tool, for the case where the automatic path
  gave up or the operator wants the worker back after a terminal failure. This
  is what makes "no way to continue its work" false even in the worst case, and
  it is worth having independently of whether change 3 lands.
- **Keep the thread findable.** A `failed` record that carries `session_id`
  means the Ctrl+R history can reopen the dead worker's conversation even after
  the session is closed.

## What this does not fix

A worker whose harness process died without ever reaching a `SystemInit` has no
session id and nothing to resume — it gets re-dispatched from the payload, which
is the correct outcome. A worker that ran for an hour and produced uncommitted
work in a shared checkout is still only as recoverable as its conversation;
resume brings back the context, not the filesystem. Neither is a reason to leave
the three causes above in place.

## Sizing

Change 1 is minutes plus its gate. Change 2 is about an hour with tests. Changes
3 and 4 are the real work and share a test rig: roughly half a day together,
with `max_attempts` config, the classifier, the resume path in `start()`, and
the CLI/MCP surface.
