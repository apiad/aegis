---
when: a queue worker's harness died, a task is stuck in dispatched or recoverable, or you have to decide between resuming a parked conversation and re-running the task from its payload
---

# Recovering a dead queue worker

A worker whose turn ends in anything but `ready` is no longer closed. It
stalls, gets its harness rebuilt underneath it, and is told to carry on;
when the retry budget runs out it is parked, which means it sits in the
roster with its whole conversation, waiting for somebody to say continue.
Nothing is ever re-run from its payload on its own, because a worker that
got halfway may already have committed, pushed, deployed or sent mail.

## Stalled or parked

Both leave the session alive, and they need different things from you.

| | stalled | parked |
|---|---|---|
| task status | `dispatched` | `recoverable` |
| `max_parallel` slot | held | freed; the queue moved on |
| the session's origin | `queue` | `parked` |
| the producer heard | nothing | one message naming the task and the handle |
| what to do | wait a few seconds | read it, then resume or retry |

A stall is not news: the rebuild takes about three seconds and the worker
comes back mid-sentence. `max_attempts` (default 2) is what keeps a stall
from being "hold the slot until resolved" — the second bad turn end parks.

## Read the log

`<root>/.aegis/state/queues/<queue>.jsonl`, one record per transition, and
the same fold the manager replays at boot:

```
aegis queue ls              # unfinished tasks
aegis queue ls --all        # history too
aegis queue show <task_id>  # every field the records merged into one task
```

Read-only and standalone — no daemon has to be running, which is the point
when you are looking at a root whose daemon died. `show` is where the
diagnosis is: `attempt` and `stop_reason` from the stall, `parked_at`, the
`session_id` the rebuild would resume from, and `last_text`, whatever the
worker had said when it went quiet.

## Read the conversation before you decide

The parked session is the whole reason the conversation survived, so use
it. From an agent, `aegis_read_peer("<handle>")`; in the TUI, `/queues
tasks` lists the tasks and their states and the worker's tab is still
there with its history. The producer's park message names both the handle
and the task id, so neither has to be hunted for.

What you are looking for is how far it got and what it touched — a worker
that had already pushed needs different handling from one that died
thinking.

## Resume or retry

- **`aegis_task_resume(task_id)`** (MCP), or **`/resume <task_id>`** (TUI).
  Puts the same conversation back to work: with a live parked session it
  rebuilds the harness in place, keeping handle, transcript and observers;
  otherwise it spawns the recorded conversation back onto the handle.
  Either way `attempts` resets to 0 and the worker is nudged to continue.
  It needs a free slot in the queue and a recorded conversation id; it
  refuses, with a reason, otherwise.
- **`aegis_task_retry(task_id)`** enqueues the payload again as a NEW task,
  closes the parked session and fails the old one. This is a second
  execution of everything the first worker did. Ask for it only when you
  have read the conversation and decided it is not worth continuing.

There is no `aegis queue resume`: the daemon's socket carries view frames,
not RPC, so a short-lived CLI process cannot ask the live brain to rebuild
a session. Acting happens over MCP or in the TUI.

## Restarts

A task that was `dispatched` when the process died is restored, headless
included: the replay spawns the harness again with the recorded
conversation id and nudges it, and the worker picks up where it was.

A **parked** session is rebuilt LATER rather than at boot. The replay
deliberately does not spawn it — that would be one subprocess per parked
task at startup, unbounded by `max_parallel` — and it adopts one only if
something else already restored the tab, which is the TUI's `plan_resume`.
Under a headless `aegis serve` with no view attached, nothing does.

That is not the end of the conversation any more. `aegis_task_resume`
falls back to the recorded `Resumable`: with nothing standing under the
handle it spawns the harness again with `--resume <session_id>` and the
recorded `log_id`, re-origins it to `queue`, attaches the queue's
observers and nudges it. So a parked worker is resumable after a restart,
which matters because `recoverable_ttl_s` defaults to a day and a restart
inside that day is routine — an idle reap, an upgrade, a reboot.

The same fallback covers a second case that has nothing to do with
restarts. A worker that stalled, got rebuilt, and then died at `start()`
parks holding a session whose `session_id` is `None`: `AgentSession.adopt`
installed a fresh driver, and both drivers latch the id on their first
turn. A resume there closes the empty session and spawns the recorded
conversation instead.

One dead end remains: a task whose harness never reported a conversation
id at all. `aegis queue show <task_id>` is where you check — no
`session_id` means `aegis_task_resume` will refuse and `aegis_task_retry`
is the only door.

## The deadline

`recoverable_ttl_s` per queue, a day by default, driven by a reaper every
five minutes. Past it the parked session is closed, the task fails with
"parked conversation discarded after Nh unread", and the producer is told.
Set it to `0` to keep parked sessions forever — at the cost of a daemon
that never idles out, since the idle reaper will not reap one while any
session stands.

## When a rebuild reports success and the worker never speaks

Check that a second harness process actually exists (`pgrep -af claude`,
matching on the session's cwd — never `pkill -f` a pattern that is also in
your own command line). Every driver spawns lazily inside `start()`, so a
session that skips `start()` sends into a driver with no process and the
turn dies silently: nothing on stdout, no `Result`, and the queue reads it
as a second bad turn end about four milliseconds after the first. That was
a real bug in `AgentSession.adopt` until 2026-09-25, and the symptom to
recognise is a task that parks with `attempts: 2` from a single failure.
