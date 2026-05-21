# Queues

**Queues** are aegis's inter-agent delegation primitive. Any agent can
say "do this work and tell me when you're done"; the substrate spawns
a worker, runs the payload, and delivers the result back as a normal
inbox message. Producer keeps working in between.

## The model

A queue is statically configured in `.aegis.py`:

```python
queues = {
    "review":   {"agent": "reviewer", "max_parallel": 2},
    "research": {"agent": "default",  "max_parallel": 1},
}
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
4. **Callback.** If the producer asked for `callback=true`, the result
   is delivered to their inbox as a normal user-message turn, prefixed
   with a header:

       > from queue:review · task#01HK… · ok · 2026-05-21T14:30:00Z

5. **Status.** Throughout, the producer (or anyone) can call
   `aegis_task_status(task_id)` to inspect the task.

If the worker errors, the callback header reports `error` instead of
`ok`, and the body carries the error reason.

## Restart safety

On startup the substrate replays each queue's JSONL log
(`.aegis/state/queues/<queue>.jsonl`). Tasks that were in flight when
the process died get marked `failed:interrupted` so the producer's
inbox eventually receives a clean error rather than waiting forever.

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
