---
title: Queues
kind: howto
when: handing a batch of self-contained tasks to worker sessions that run in parallel
---

A queue runs tasks in fresh worker sessions, a few at a time. You enqueue a
task with a self-contained prompt and keep working; a worker session starts
from the queue's agent, does the task, and its final message comes back to
your inbox. Queues are defined in `.aegis.yaml` (see the config page).

## Enqueue a task

Call `queue_enqueue` with:

- `queue`: the queue's name;
- `payload`: the worker's first prompt. The worker knows nothing else, so say
  what to do, where, and what its final message must contain;
- `callback` (default true): deliver the worker's result to your inbox.

It returns without waiting for the work, with the task id, its status and,
while it waits for a slot, its position in the queue. With a slot free, the
worker has already started, and the status says running.
An unknown queue is refused with the names of the queues there are, and a
queue whose entry in `.aegis.yaml` is broken is refused with what is wrong.

Tasks wait first in, first out, for a free slot. A queue runs at most
`max_parallel:` workers at once. A pending task also waits, without failing,
while its owner's quota policy keeps the harness it needs closed.

## What a worker is

A worker is an ordinary session, marked as a worker of its queue on its card,
started in the enqueuer's working directory. It starts quiet: no tab, only a
card in the Fleet's last group, unless its queue sets `quiet: false`. Its task is its first
prompt and its final message is the result, so it should end on the answer
the enqueuer needs.

A task finishes only when the worker's turn has ended and nothing is pending
for it: no live monitor, no held message, no background command still running.
A worker that ends a turn to wait on a monitor is still working.

When it finishes, the task is completed with the worker's last message as its
result, the worker is archived, and, with a callback, you are woken with:

```text
> from queue:<name> · task#<id> · ok · 2026-10-11T14:02:11Z
```

A worker whose process exits on its own fails its task and gets its tab, and
the wake says `error` with the exit code and the last ten lines of its stderr.

## While you wait

Between your turns, a session with a callback task still pending or running
reads waiting, and says on whom. Its Fleet card says `waiting · <handle>`,
naming the first two workers and counting the rest (`+2`); a task still
waiting for a slot has no worker yet and counts as `1 pending queue task`.
The session panel has a Waiting on section with a row per task: the worker's
handle, the queue and the task's status. A click on a row opens the worker,
whose tab a quiet worker otherwise lacks. A session started with
`session_spawn` that is still working or waiting shows there the same way.
`session_list` gives an agent the same names in each card's waiting line.

## Check, cancel and resume

- `task_status` with `task_id=<id>`: the task's status (pending, running,
  completed, failed or cancelled), its worker's log id, the worker's handle
  while its session is open, the error of a failed task and, once finished,
  its result.
- `task_cancel` with `task_id=<id>`: drops a pending task, or stops and
  archives a running task's worker. No callback is sent for a cancelled task.
- `task_resume` with `task_id=<id>`: puts a failed or cancelled task's worker
  back to work with its conversation intact, reopening it from the archive if
  needed. Any other task is refused as not resumable.

You can cancel and resume only tasks you enqueued.

## After a restart

Tasks are kept in the server's state directory. When the server starts, pending
tasks are dispatched again, and each running task's worker is resumed with a
message saying the server restarted. A running task whose worker is gone
fails.
