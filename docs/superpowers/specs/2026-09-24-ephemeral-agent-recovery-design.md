# Ephemeral agent recovery — stall, resume, park

**Status:** implemented 2026-09-25, verified against a real `claude` harness killed mid-turn by PID (stall, rebuild, park, resume and the restart replay). Supersedes the fix section of
[2026-09-24-queue-worker-recovery-design.md](2026-09-24-queue-worker-recovery-design.md),
whose analysis of the three causes still stands and is not repeated here.
**Scope:** one new module (`src/aegis/core/recovery.py`), one extracted module
(`src/aegis/queue/replay.py`), changes to `src/aegis/queue/manager.py`,
`src/aegis/queue/inbox.py`, one new keyword on `SessionManager.reconnect`, one
new field on `AgentSession`, the two brain-boot paths, one new `Origin.kind`, two
new task states, two per-queue config keys, two MCP tools, two TUI commands and a
read-only `aegis queue` CLI. No driver changes: every driver already advertises
resume, and `AgentSession.adopt` already does the rebuilding.

## What this buys

Today an ephemeral agent that ends a turn badly is closed, and its conversation
stops being reachable by any path aegis offers. After this change, the worst case
is a session sitting in the tab bar with its whole conversation, labelled with the
task it was working on, waiting for someone to say continue.

Three things have to be true for that, and none of them is true now: the record
has to survive the process, the record has to contain what resume needs, and the
session must not be destroyed on the way.

## Scope: all three ephemeral kinds

`EPHEMERAL_KINDS = {"queue", "workflow", "group"}` (`fleet/models.py:17`), and
all three are closed by the code that owns them: the queue at `manager.py:762`,
workflows at `engine.py:414` and `runner.py:540`, groups through the runtime. The
loss is identical in each, and a workflow has more invested in it than a queue
task because it is several agents into a scripted procedure.

So the classifier and the resume path live in a shared module and each owner
keeps its own notion of what a task is. The alternative, implementing this in the
queue and again in the workflow engine later, puts two copies of the classifier in
the tree, and the classifier is the part that will need tuning. Two copies of a
rule that needs tuning is how one of them ends up wrong and nothing says so. This
mirrors `core/close_guard.py`, which is shared between `aegis_close` and the
queue's `_still_working` for the same reason.

This spec implements the queue caller fully. The *intention* is that the workflow
and group callers follow, and the plane is shaped with them in mind — but the
interface as built does not yet support them, and calling that a later, smaller
change would be claiming a property it does not have. `rebuild` takes no
`Resumable`, so a caller with a record and no live session cannot use it;
`restore` duck-types four fields off "a task" (handle, rebuild record, queue, id)
that a workflow node does not have; and `classify`'s `attempts` convention reads
correctly under either counting convention from inside `recovery.py`, so the
second caller has to establish which one it means rather than inherit it. Those
are three real pieces of design work, not plumbing.

## The shared plane: `core/recovery.py`

Three things, no state of its own.

```python
@dataclass(frozen=True)
class Resumable:
    """Everything needed to rebuild a worker's conversation."""
    session_id: str      # the harness's own conversation id
    agent_profile: str
    provider: str
    cwd: str
    host: str            # "local" or a configured execution host

class Outcome(StrEnum):
    done = "done"            # clean end; the answer is the answer
    transient = "transient"  # the turn broke; the conversation is intact
    terminal = "terminal"    # the task is over

def classify(state, *, attempts, max_attempts,
             cancelled=False, over_budget=False) -> Outcome

def resumable_from(session) -> Resumable | None
    """None until the harness has reported a session id."""

async def rebuild(sm, handle, *, nudge) -> bool
    """Replace the dead harness under `handle` in place, resuming its
    conversation, then hand it one turn saying what happened.
    False when it cannot be done."""

async def restore(sm, task, *, nudge) -> str | None
    """After a restart: adopt the session already standing under the task's
    worker handle, or rebuild it from the recorded Resumable. Returns the
    handle, or None when neither is possible."""
```

### Rebuild in place, never respawn

The naive version spawns a fresh session under the old worker's handle. That is
a crash. Pane removal is asynchronous — `app.py:1429` drops a closed session's
pane through `run_worker(self._drop_brain_pane(...))` — so after `close()`
returns, `#pane-<handle>` may still be mounted, and mounting a second one is
`DuplicateIds`, which takes the whole app down. `_resume_from_history` carries a
long comment about precisely this hazard (`app.py:1856`).

`AgentSession.adopt` (`session.py:339`) is the way through, and it already exists
for a neighbouring reason: `SessionManager.reconnect` uses it to rebuild a
dropped *remote* harness. It replaces the subprocess underneath a live session,
and its docstring lists what survives — "handle, log_id, inbox binding, metrics,
observers, transcript. Only the process at the bottom is new."

Observers surviving is the part that matters here. The queue's `on_event` and
`on_state` stay attached across a rebuild, so there is nothing to re-wire and no
window in which a worker is running unobserved.

One change is needed: `SessionManager.reconnect` refuses a local session
(`manager.py:454`, "reconnect is for remote sessions"). That guard belongs to the
manual `/reconnect` command, not to the mechanism — `resume_from` is how every
boot resume works, locally included. `reconnect` gains `allow_local: bool =
False`, and recovery passes `True`. The manual command's behaviour does not
change.

### The classifier is a budget, not a taxonomy

A refinement on what we discussed, and worth flagging because it inverts the
default arm. `classify` returns `done` for `AgentState.ready`, `terminal` when
attempts are exhausted or the task was cancelled or the queue is over budget, and
**`transient` for everything else**.

Two consequences of that default worth stating plainly. A cancelled or
over-budget task reaches `terminal` without ever having been transient, and keeps
today's behaviour exactly: the worker is closed, the task is `cancelled` or
`failed`. Parking applies only to a task that stalled at least once. And in the
other direction, the `failed` task state becomes nearly unreachable — a worker
that genuinely cannot do the job now costs one retry and then leaves a parked
session behind. That is the intended trade and it has a cost, handled under
"Parked sessions accumulate" below.

The tempting version enumerates the transient reasons — `stop_reason ==
"link_lost"`, a harness exception, a stream that ended with no `Result`, a
provider rate limit — and calls the rest terminal. I do not trust that
enumeration. We have four drivers, two protocols, and no stable cross-harness
vocabulary for why a turn failed; a list like that is wrong on the day a provider
changes a string, and wrong silently, because the failure mode is "went terminal
when it should have retried" and that looks exactly like the bug we are fixing.

So the retry budget does the limiting and `max_attempts` is the knob. The
diagnostic fields (`stop_reason`, `repr(last_error)`) are written into the log
record for reading afterwards, not branched on. If a taxonomy turns out to be
worth having, the log will be the evidence for building it.

`AgentSession` needs one new field for this: `last_stop_reason`, latched beside
the existing `last_error` (`session.py:328`) when a `Result` arrives.
`Result.stop_reason` already exists (`events.py:114`) and is currently dropped.

## Change 1 — hand both brain paths their state dir

`_serve`: `InboxRouter(state_dir=roots.state_dir)` and `QueueManager(queues or
{}, mgr, inbox, state_dir=roots.state_dir)`. `AegisApp._build_planes`: the same
with `self._state_dir`. Four lines.

Gate it structurally, because one assertion against one call site is how this got
here. `src/aegis/core/planes.py` already forces every new `attach_*` on
`SessionManager` into an inventory; the parallel rule is that a plane whose
constructor takes a `state_dir` must be handed one in every brain path, checked
by walking the AST of `cli.py` and `tui/app.py` for calls to the inventoried
plane constructors. The one-shot `aegis workflow run` path is in scope for the
rule too — it already complies.

Then break the wiring on purpose and confirm the gate goes red. The eight
existing persistence tests are the cautionary case: green for months against a
plane production never built, because every one of them passes `state_dir`
by hand.

## Change 2 — record what resume needs

The worker's `session_id` is latched by the driver on its first `SystemInit` and
is readable as `session.session_id` for the rest of its life. The queue never
writes it down, which is the single field that makes resume impossible.

The queue's `on_event` observer sees every event (`session.py:959` fans out to
`_extra_event_observers` unconditionally), so it latches the id the moment it
appears and appends a `worker_session` record carrying the full `Resumable`.
Writing it at first sight rather than at `_finalize` matters: a worker whose
harness dies before any turn boundary never reaches `_finalize`, and that is
precisely the case we need the record for.

`worker_session` is diagnostic, not lifecycle — it must not enter
`_LIFECYCLE_EVENTS` or it will move the task's status on replay. The existing
comment in `start()` explains what that costs.

## Change 3 — stall instead of closing

`_finalize` today keys everything off `ok = st is AgentState.ready` and closes
the session on any other outcome. It gains a third arm.

On `Outcome.transient`:

- Do not close the session.
- Log `stalled` with the attempt number, `last_text`, `stop_reason` and
  `repr(last_error)`.
- Leave the task `dispatched`, the worker alive, and the `max_parallel` slot
  held.
- Say nothing to the producer. A blip that recovers in four seconds is not news,
  and waking a producer agent for it costs a turn.
- Increment `attempts` and call `recovery.rebuild`, which swaps the harness
  underneath the same session and delivers a nudge turn: *"Your aegis session
  was interrupted mid-task (<reason>). Your conversation is intact. Continue the
  task from where you were."*

The slot is held for the duration of the retries and no longer. That window is
the whole risk in this design: `max_parallel` defaults to 1
(`yaml_loader.py:52`), so a held slot is a stopped queue, and a retry loop with
no ceiling is "hold until resolved" with extra steps. `max_attempts` defaults to
2 — one retry — and a stall with no `Resumable` skips straight to parking
because there is nothing to resume.

`_still_working` keeps precedence over all of this. A worker waiting on a monitor
has not ended anything, and that check runs first, unchanged.

## Change 4 — park, and free the slot

When `classify` returns `terminal` after a transient run, the worker is parked
rather than closed:

```python
session.origin = Origin(kind="parked", by=queue_name, detail=task_id)
```

`"parked"` is a new `Origin.kind` and is deliberately **not** in
`EPHEMERAL_KINDS`. Reassigning `session.origin` is established: `groups/wiring.py:38`,
`workflow/engine.py:398`, `core/manager.py:434` and `tui/app.py:3508` all do it.

Leaving the ephemeral set is the entire mechanism, and it buys three behaviours
for free rather than adding a session state:

- `GhostBook` stops reading the worker as a departure and stops fading it after
  `GHOST_TTL`. It is a live session, so the fleet dashboard renders it as one.
- `close_guard` starts protecting it the way it protects every non-disposable
  session, so `aegis_close` refuses to reap it while it holds claims or has
  undelivered inbox items.
- `plan_resume` restores it across a daemon restart with no special case, because
  it is an ordinary tab in `workspace.json` with a `session_id`. **Only where a
  view is attached.** Under a headless `aegis serve` nothing runs `plan_resume`,
  so the task record comes back and the session does not. The replay
  deliberately leaves it that way — rebuilding every parked session at boot is
  one subprocess per parked task, unbounded by `max_parallel` — and
  `aegis_task_resume` rebuilds the conversation on demand instead, from the
  recorded `Resumable`. So a parked worker is resumable after a headless
  restart, which it has to be: `recoverable_ttl_s` defaults to a day, and a
  restart inside that day is routine.

The origin keeps `by` and `detail`, so the parked session's card still names the
queue and task it came from. That is a label for a reader and nothing more:
`aegis_task_resume` looks the task up in `_all` by its full id, and nothing
resolves a task through `Origin.detail`.

The task moves to `recoverable`, the slot frees, and `_try_dispatch` runs. The
queue moves on.

### Parked sessions accumulate

The thing I added that we did not discuss, because the design is incomplete
without it. A parked session is a real session, and that cuts both ways:
`IdleReaper` reaps the daemon only after a contiguous run of **zero views and
zero sessions** (`daemon/lifecycle.py:170`), so one forgotten parked worker pins
the daemon open indefinitely and sits in the tab bar. Over a week of queue work
with a few genuine failures, that is a row of dead tabs and a laptop daemon that
never exits.

So `recoverable` has a deadline: `recoverable_ttl_s`, default 24 hours. When it
expires the parked session is closed, the task moves to `failed`, and the
producer is told the conversation was discarded after N hours unread. That is a
bounded, announced loss after a full day in which anyone could have acted, which
is a different thing from today's silent loss four seconds after a dropped SSH
link.

Set it to `0` to disable the deadline and keep parked sessions forever, for a
host where that is what you want.

## Task states and replay

`pending | dispatched | stalled | recoverable | completed | failed | cancelled`.
`Task` gains two fields, both persisted: `attempts: int` and `resumable:
Resumable | None`.

`stalled`, `recoverable` and `resumed` all join `_LIFECYCLE_EVENTS`, but they
cannot keep the identity mapping the replay uses today. `_LIFECYCLE_EVENTS`
membership currently means `tasks[tid]["status"] = rec["event"]`, so a `resumed`
record would set a status named `resumed`, which matches no branch below, which
drops the task out of `_all` with no callback and blocks the producer forever on a
task nothing remembers. That is the exact failure the comment in `start()` was
written about, and it has happened once in this file already.

So the replay gets an explicit event-to-status map rather than an identity:
`resumed` means the task is `dispatched` again, and every other lifecycle event
maps to its own name. Adding a lifecycle event without an entry in that map should
fail a test, not a producer.

Replay, per task:

| on disk | action |
|---|---|
| `dispatched` or `stalled`, with `Resumable`, attempts left | `restore` with the nudge; stays `dispatched` |
| `dispatched` or `stalled`, no `Resumable` | `recoverable`, with "the worker never reached a turn boundary; no conversation to resume" |
| `dispatched` or `stalled`, attempts exhausted | `recoverable` |
| `recoverable` | unchanged, no callback — the producer was told once already |
| `pending` | re-queued at head of FIFO, as today |
| `completed` / `failed` / `cancelled` | rehydrated, as today |

### The restart race with `plan_resume`

A queue worker is an ordinary tab in `workspace.json` with a `session_id`, and
`plan_resume` does not filter by origin. So at boot the TUI restores the dead
worker's session *and* the queue replay wants to rebuild it, and whichever runs
second mounts a second pane under a handle the first already holds. That is the
same `DuplicateIds` crash from a different direction, and the ordering between
`qm.start()` (`cli.py:753`) and the front end's boot resume is not fixed.

`recovery.restore` resolves it by looking before it builds: if
`sm.get(worker_handle)` already returns a session, the replay adopts that one and
re-attaches its observers rather than creating anything. Only when the handle is
unoccupied does it spawn with `resume_from` and the recorded `log_id`.

That also fixes the orphan described in the analysis spec, where a restart left
the worker's tab alive with its full conversation while the queue had declared
its task failed and never looked at it again. The session it finds standing there
*is* the worker, and re-attaching is the whole of the repair.

**A task is never re-run from its payload automatically.** A worker that got
halfway may have committed, pushed, deployed or sent mail, and re-running its
prompt is not a recovery, it is a second execution. Resume or park. Re-running is
`aegis_task_retry`, which a person or an agent asks for explicitly.

`start()`, `_mark_interrupted` and these branches are a cohesive unit and
`manager.py` is already 895 lines. They move to `src/aegis/queue/replay.py` as
part of this change. The dispatch state machine stays where it is.

## What the producer hears

Exactly one message it would not have got before, and only when someone has to
act:

- `stalled` → nothing.
- `recoverable` → one message: what broke, how many attempts, the parked
  session's handle, and the two things that can be done about it
  (`aegis_task_resume("<task_id>")`, or `aegis_read_peer("<handle>")` to read the
  conversation first).
- a resumed worker that then finishes → the ordinary `ok` callback.

So a producer that enqueued work and went back to its own turn gets one actionable
message instead of a condolence, and a producer whose worker blipped and recovered
gets what it always wanted, which is the answer.

## Surfaces, and one constraint

The daemon's unix socket (`daemon/server.py`) is a view-attachment stream, not
request/response RPC. A standalone CLI process cannot ask a live brain to spawn a
session. That rules out `aegis queue resume` as a real command without a new
daemon protocol, which is not in this change.

So the acting surfaces are the two that are already bound to the brain:

- **MCP:** `aegis_task_resume(task_id)` and `aegis_task_retry(task_id)`.
- **TUI:** `/queues tasks` to list tasks and their states, `/resume <task_id>`. Both
  follow the `/enqueue` shape in `commands/builtins/core.py`.

And the CLI stays read-only, over the JSONL log, which works standalone and is
how you find a task id after the fact:

- `aegis queue ls [--queue <name>] [--state <state>]`
- `aegis queue show <task_id>`

New module `src/aegis/cli_queue.py`, registered with `app.add_typer(_queue_app,
name="queue")`, matching `cli_schedule.py` and the six others.

## Config

One key per queue:

```yaml
queues:
  impl:
    agent: claude-impl
    max_parallel: 1
    max_attempts: 2            # new; 1 disables automatic resume
    recoverable_ttl_s: 86400   # new; 0 keeps parked sessions forever
```

`max_attempts: 1` means the first transient outcome parks immediately, which is
the "park immediately" model for anyone who wants it.

## What this does not do

- No automatic re-run from payload, by design, as above.
- No workflow or group caller. The plane's interface is built for them; wiring
  them is a later change.
- No daemon RPC, so no acting `aegis queue` subcommands from a detached shell.
- Resume restores a conversation, not a filesystem. A worker that spent an hour
  producing uncommitted work in a shared checkout comes back knowing what it
  meant to do, which is strictly more than it has now and less than everything.
- No recovery for a worker that died before its harness ever reported a session
  id. It parks with an honest message.

## Testing

The mechanical ones: classifier table, park frees the slot and the next task
dispatches, park leaves `EPHEMERAL_KINDS` and `GhostBook` stops shadowing it,
each replay branch including the two that park, producer gets exactly one message
per recoverable task, `max_attempts: 1` parks on first stall, the TTL closes a
parked session and reports the discard, `recoverable_ttl_s: 0` never does.

One structural test beyond those: every member of `_LIFECYCLE_EVENTS` has an entry
in the event-to-status map and every value of that map has a replay branch. Both
halves, because the failure is a task that silently stops existing and the
producer that waits on it forever.

And two for the crash hazards, because both take the whole app down rather than
failing a task: a rebuild mounts no second pane and the handle count does not
change, and a replay against a handle `plan_resume` has already restored adopts
it instead of spawning.

Two that matter more than the rest:

**The wiring gate must be mutation-tested.** Remove `state_dir` from `_serve`
and confirm the structural check fails. A gate that cannot fail is worth less
than no gate, and this specific gate is replacing one that was green for months
while the thing it guarded did not exist.

**Exercise it the way a user reaches it.** Boot a real daemon, enqueue a task,
kill the worker's harness process mid-turn, and confirm from the TUI that the tab
is still there with its conversation, that the queue dispatched the next task, and
that `/resume` picks it back up. Against a daemon started *after* the change —
`AGENTS.md` is explicit that green tests against a daemon that booted before the
change prove nothing about the change, and this change is entirely about what
survives a process boundary.

## Sizing

Change 1 is minutes plus its gate, and it is worth landing on its own because
until it lands nothing else is observable. Change 2 is about an hour. Changes 3
and 4 share a rig and are the bulk: the plane, the third arm of `_finalize`, the
parking, the replay extraction and its branches, the two MCP tools, the two TUI
commands and the CLI. Call the whole thing a day, with the replay branches and
the live-daemon test as the parts most likely to take longer than they look.
