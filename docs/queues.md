# Queues

**Queues** are aegis's inter-agent delegation primitive. Any agent can
say "do this work and tell me when you're done"; the substrate spawns
a worker, runs the payload, and delivers the result back as a normal
inbox message. Producer keeps working in between.

## The model

A queue is statically configured in `.aegis.yaml`:

```yaml
queues:
  review:
    agent: reviewer
    max_parallel: 2
  research:
    agent: default
    max_parallel: 1
```

Each queue binds to one agent profile and a max-parallel cap. At
runtime there are three lists per queue: **pending** (FIFO), **inflight**
(currently running, up to the cap), and the **all-tasks** index for
status lookup.

Dispatch is **deterministic and substrate-driven**: every enqueue and
every worker completion synchronously re-checks the cap and may start
the next pending task. There's no background loop — when nothing is
happening, nothing runs.

## Lifecycle of a task

1. **Enqueue.** An agent calls `aegis_enqueue(queue, payload,
   from_handle, callback=true)`. The substrate creates a `Task` with a
   ULID, appends it to the queue's pending list, and synchronously
   tries to dispatch.
2. **Spawn.** If the cap allows, a fresh worker is spawned with the
   queue's configured agent profile. The worker's first turn is the
   `payload`. It runs to completion.
3. **Result capture.** The worker's final assistant text is captured
   verbatim by the substrate as the task result.

   A turn boundary is *not* by itself completion. Ending a turn is how an
   agent waits — that is exactly what `aegis_monitor` tells it to do — so
   before finalizing, the substrate checks whether the worker is still
   waiting on something it armed: a live monitor, a pending reminder, an
   unconsumed inbox message, an armed loop. If it is, the task stays in
   flight, no callback is sent, the worker stays alive, and a `deferred`
   record goes in the queue log. The waker fires, the worker takes its
   reporting turn, and *that* boundary finalizes.

   Every deferring condition is self-terminating, so this cannot hang a
   slot: monitors have timeouts, reminders have fire times, inbox
   messages resolve at the next turn boundary. A held **file claim is
   deliberately not** one of them — only the holder releases a claim, so
   a worker that forgot would pin its `max_parallel` slot forever.
4. **Callback.** If the producer asked for `callback=true`, the result
   is delivered to their inbox as a normal user-message turn, prefixed
   with a header:

       > from queue:review · task#01HK… · ok · 2026-05-21T14:30:00Z

5. **Status.** Throughout, the producer (or anyone) can call
   `aegis_task_status(task_id)` to inspect the task.

If the worker errors, the callback header reports `error` instead of
`ok`, and the body carries the error reason.

**Every ending carries the worker's last message.** A cancelled task
(`aegis_cancel`) and a task interrupted by a restart both report the
outcome *and* whatever the worker had already said, rather than the bare
word `cancelled` or a canned restart notice — a worker that did twenty
minutes of work and said so should not reach its producer as one word.
The same text lands on the task's `result`, so `aegis_task_status` shows
it too. Nothing is invented: a worker that had said nothing yet gets a
callback that says exactly that.

## Restart safety

On startup the substrate replays each queue's JSONL log
(`.aegis/state/queues/<queue>.jsonl`). A task that was in flight when
the process died has its worker put back: the conversation is resumed
from the id the harness reported, the worker is told that aegis
restarted and that it is still on the same task, and the task stays
dispatched. When there is nothing to resume from, or the task had
already spent its `max_attempts`, it is parked as `recoverable`
instead, and the producer's inbox receives a notice saying where the
conversation is rather than waiting forever.

A task is **never re-run from its payload**. A worker that got halfway
may already have committed, pushed or deployed, so replaying its prompt
would be a second execution rather than a recovery.

## When a worker stalls

A turn that ends in anything but `ready` — a dropped SSH link to an
execution host, a harness exception, a stream that stops with no result
— used to close the worker, and closing is irreversible. The session
left the roster, its MCP token was revoked, its pane was dropped, and
an hour of context went with it.

It now **stalls and rebuilds**: the harness under the session is
replaced, the conversation is resumed from the id the harness reported,
and the worker is told it was interrupted and is still on the same
task. The task stays `dispatched` and keeps its slot for the rebuild
window and no longer — `max_attempts` is what bounds that.

When the attempts run out the worker is **parked**, not closed:

- its task goes to `recoverable`, which is a third status beside
  `completed` and `failed`, not a flavour of failure;
- its session stays **alive** holding the whole conversation, and stops
  being disposable — the ghost book no longer fades it and `aegis_close`
  protects it like any other session;
- its `max_parallel` slot is freed immediately, so parking never blocks
  the queue;
- the producer's callback says where the conversation is, and carries
  whatever the worker had already said.

Parked tasks survive a restart: the replay rehydrates them so they can
still be resumed or reaped, and says nothing to the producer, which was
told once already. A parked session that nobody acts on is closed after
its queue's `recoverable_ttl_s` (a day by default) and its task failed —
a bounded, announced loss, because a parked session is a real session
and one forgotten worker pins the daemon open forever.

### Putting a parked worker back to work

Read it first — `aegis_read_peer(<worker_handle>)`, or just switch to its
tab — and then pick one of two doors:

| | What it does |
|---|---|
| `aegis_task_resume(task_id)` | Rebuilds the harness under the **same session** and tells it to continue. Nothing it had worked out is lost, and its retry budget starts over: you looked at it and said go, which is new information. |
| `aegis_task_retry(task_id)` | Re-runs the **original payload** as a new task, closing the parked session. |

They are deliberately separate, and resume never falls back to retry. A
worker that got halfway may already have committed, pushed, deployed or
sent mail, so re-running its prompt is a second execution rather than a
recovery — something only a caller who has decided the conversation is
not worth continuing should ask for.

In the TUI, `/queue` lists tasks with their full ids and `/resume
<task_id>` is the same door as `aegis_task_resume`. Resume is refused —
with a reason, never a traceback — when the task is not parked, when its
session is gone (the tab was closed, or the TTL reaper got there first),
or when the queue has no free slot to take back.

### `aegis queue` — reading the log from a shell

```bash
aegis queue ls                # unfinished tasks across every queue
aegis queue ls impl --all     # one queue, history included
aegis queue show <task_id>    # the folded state plus every record
```

`aegis queue` is **read-only, and stays that way.** The daemon's socket
is a view-attachment stream, not request/response RPC, so a standalone
CLI process has no verb it can send to ask a live brain to rebuild a
harness; a `resume` subcommand here could only edit the JSONL log and
lie about a worker it never touched. The two surfaces that can act are
the two already bound to a brain — the MCP tools and the slash commands
above. Reading is a different matter: `ls` and `show` fold the same log
the manager replays at boot, so they answer with no daemon running at
all.

## Why callbacks, not polling

The producer doesn't have to know how long the worker will take, doesn't
have to poll, and doesn't have to keep state. Its next turn is woken by
the inbox message just like a user typing into its tab. From the
producer's perspective, `aegis_enqueue` is fire-and-forget; the answer
shows up later as a normal turn.

## Operational cap

`max_parallel` is the only flow-control knob. Set it according to:

- **Cost** — each worker is a separate model call.
- **Provider rate limits** — concurrent Claude / Gemini sessions
  consume your quota.
- **Local CPU / IO** — every worker is a subprocess.

Start with `1` and raise if you observe pending tasks piling up.

## Configuration validation

At boot, `aegis` validates the `queues` dict:

- Each queue's `agent` must reference a key in `agents`.
- `max_parallel` must be a positive int.

Errors are fail-loud — aegis aborts startup with a clear pointer at
the offending queue.

## In the TUI

Queue workers appear as **background tabs** when they spawn. They
don't steal focus. Their state dot, sticky `*`, and bell behave like
any other tab — you can switch to a worker tab mid-flight to watch
what it's doing, or just let it finish and the producer's inbox
callback handles the result.

### Always-on strip

In every conversation, a one-line strip sits just above the status
bar showing live queue state — depth, parallel cap, ok/err counts,
and the handle of the most recently started in-flight worker. The
format adapts to how many queues you have:

| Queues | Strip |
|---|---|
| 1 | `queues: tasks ●1/2 ○3 ✓14 ✗2    last: brisk-curie` |
| 2–3 | `queues: tasks ●1/2 ○3 · impl ●0/1    last: brisk-curie` |
| 4+ | `5 queues · ●3/8 ○12 ✓42 ✗3    last: brisk-curie` |

If no queues are configured in `.aegis.yaml`, the strip is hidden.

### Dashboard (`F4`)

Press `F4` from any conversation for a full-screen modal:

- **QUEUES** — config (agent profile, max-parallel) + live counts.
- **IN-FLIGHT** — running workers with elapsed time and payload
  preview.
- **QUEUED** — tasks waiting for a slot.
- **RECENT** — last 10 completed tasks in reverse time order, with
  outcome glyphs (`✓` ok, `✗` failed).

On the right, a **detail panel** for the cursor-selected task shows
identity, sender, state, payload, lifecycle timestamps, and a live
tail of the worker's assistant text (or the captured final text for
completed tasks).

| Key | Action |
|---|---|
| `↑` / `↓` | Move the cursor across IN-FLIGHT → QUEUED → RECENT |
| `Enter` | Refresh the detail panel |
| `>` | Jump to the worker's tab (when one exists) |
| `Esc` | Close the dashboard |

### Inbox arrivals

When a handoff, queue callback, or Telegram message lands on an
agent, the receiving pane mounts a distinct block in the transcript
**before** the agent reacts:

```
✉ from queue:review · task#01HK…f3 · ok · 2026-05-21T17:30:00Z
  PR looks clean. Two nits flagged in the
  diff comments; nothing blocking.
  … (5 more lines)
```

The block fires synchronously whether the agent was idle (immediate
dispatch) or mid-turn (buffered for chain), so the arrival is always
visible.
