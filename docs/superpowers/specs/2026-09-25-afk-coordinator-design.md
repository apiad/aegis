# A board full of prompts should empty itself while you sleep

**Status:** slices 1–3 plus the quota gate **implemented** 2026-09-25
(`601c605`..`d1daa0c`, 118 tests), from
`docs/superpowers/plans/2026-09-25-afk-coordinator-slices-1-3.md`. The quota
gate moved forward from slice 4 so that plan's deliverable was something you
could switch on. Not yet built: the dispatch rails, the coordinator agent,
`worktree` isolation, the reviewer stage, stall *notification* (stall
*detection* is in), and `notify_cmd` — slices 4–7, which get their own plan.

Three things the build corrected in this design. They are folded into the
sections below, and recorded here because each was a defect the spec's own
prose would have reproduced:

- **Marker values are shell-quoted.** The marker is a `key=value` list, so
  the `gate=make check` the coordinator writes for itself read back as
  `gate=make`, and it would have re-run a different command from the one it
  gave the worker — the one comparison this design exists to make.
- **`PlanSnapshot.updated_at` is an ISO string**, not an epoch float
  (`plan/tracker.py:115`). Stall detection has to parse it, and an
  unparseable clock reads as fresh rather than putting a false stall on a
  card.
- **Archived items come back from `items()`.** A card archived to get it off
  the board was read back as work to do.
**Scope:** one new built-in workflow package
(`src/aegis/workflows/builtins/afk/`) registering two workflows, two new methods
on `WorkflowEngine` (`task_status`, `plan_state`), one new field on the dict
`QueueManager.status` returns (`worker_handle`), one new docs page, one
CHANGELOG entry. Nothing about the drivers, the session manager, the scheduler
or the MCP surface changes — every other piece this needs already ships.

## What it is for

You write tasks onto a GitHub Project as issues, each issue body being a
self-contained prompt. You close the laptop. A coordinator running inside
`aegis serve` reads the board every ten minutes, decides what should happen now,
checks there is subscription quota to spend, hands each selected card to a fresh
worker, verifies what came back, and moves the card. When you sit down again the
board tells you what landed, what needs your eyes, and what broke.

The board is the only interface. It is the input queue, the progress display and
the audit trail, and it is the one thing that survives a daemon restart, a
network partition and a rewritten config.

## What it is not

It never creates cards. Humans and interactive agents fill `Todo`; the
coordinator takes, orders, defers and moves. It never blocks on a person — `engine.ask_human` is
forbidden inside it, because a coordinator waiting for an answer at 3am is a
coordinator that did nothing all night. It never marks work `Done`; the furthest it
moves a successful card is `Needs review`. An `allow_auto_done` knob was
designed and deliberately not built: it was a config key read by nothing, and
by this package's own rule about `notify_cmd`, a key that is documented and
does nothing is worse than an absent one.

## The agent decides, the code refuses

The coordinator is an agent, not a decision table. Each tick it reads the whole
board, everything in flight and how far along it is, and decides: what to start
now, in what order, how many at once, what to defer and why, what is waiting on
what, and what to write on each card so you understand the state without asking.
It also writes each worker's brief itself, so a card can be dispatched with the
context the agent knows and the card's author did not spell out — the related
pull request, the sibling card that just landed, the file the last attempt broke.

That only works if the agent cannot do damage while being clever, so the whole
design rests on one rule:

> **The agent may only move in the conservative direction.** It can decline to
> start a card the rails would permit. It cannot start one the rails refuse. It
> can ask for a review the triggers did not require. It cannot waive one they
> did.

Everything the agent produces is a *proposal*. Deterministic code validates it
and performs it. The split:

| The agent decides | The code enforces, and the agent cannot override |
|---|---|
| which cards run now, and in what order | the quota gate |
| how many run at once, up to capacity | `max_in_flight` and queue headroom |
| whether card B must wait for card A | `Repo` resolves inside `repo_root` |
| what each worker's brief says | the clean-tree refusal |
| what each card's narration says | the report contract in every payload |
| that a card needs review | that a triggered card gets one |
| that a card should be held for a human | that a red gate is `Failed` |

**The coordinator agent writes nothing.** Not the board, not a repo, not a file.
It is handed state and returns a plan; every write in this system is performed by
the workflow. That is what makes it safe to give the agent broad read access to
the repos so it can actually look at the code a card talks about before deciding.

A proposal that violates a rail is not a crash. The offending entry is dropped,
the remainder proceeds, and **the refusal is written onto that card** — so a
coordinator repeatedly trying something the rails won't allow is visible to you
rather than buried in a log.

## Shape: a reconciler, not a loop

Three shapes were considered.

**A standalone process** (`aegis workflow run afk` under systemd, looping
internally) is the obvious one and the wrong one. `aegis workflow run` builds its
own `SessionManager` and `QueueManager` and tears them down when the workflow
returns, so it cannot use the queue the running daemon owns, and a second aegis
process on the same state root is refused by the daemon lock.

**One long workflow** that spawns workers and awaits them all works, but a tick
then lasts as long as the slowest card. Capacity is frozen at whatever that tick
picked, and the mapping from cards to in-flight workers lives only in that
workflow's local variables.

**A reconciler tick fired by the scheduler** is what this spec builds. The
scheduler already runs inside `aegis serve` and hands a scheduled workflow the
daemon's real `SessionManager` and `QueueManager` (`cli.py:779`). So a tick can
`engine.enqueue(..., callback=False)`, return in seconds, and the worker keeps
running in the daemon after the tick is gone. The next tick picks the result up
through `QueueManager.status(task_id)`, which survives a daemon restart because
the daemon's queue is constructed with `state_dir` and replays its JSONL on
`start()`.

Every tick does the same two things in the same order: reap what finished, then
start what fits. No tick holds work. A crash costs one tick.

```yaml
# .aegis/schedules/afk.yaml — per-host, because .aegis/ is gitignored, so
# the schedule does not follow the repo to a second machine and start a
# second coordinator against the same board.
afk:
  workflow: afk
  cron: "*/10 * * * *"
  timezone: America/Havana
  on_overlap: skip
```

`on_overlap: skip` is doing real work here: a tick that runs long (several agent
reviews at once) simply suppresses the next fire rather than stacking
coordinators.

## The board contract

Cards are **real GitHub issues** added to the project, not draft issues. Draft
issues carry a title and a body and nothing else — no comment thread — and the
report is the most valuable thing the coordinator produces. Real issues also
give the worker's pull request something to link and close, and give you the
whole history of a card a week later.

Four fields the human sets:

| Field | Type | Means |
|---|---|---|
| `Status` | single-select | the state machine, below |
| `Target repo` | single-select | which checkout the work happens in (**not** `Repo` — GitHub reserves that name and `Repository`) |
| `Priority` | single-select | input to ranking |
| `Deadline` | date, optional | what makes "must happen now" mechanical rather than a judgement call |

One more field, written only by the coordinator:

| Field | Type | Means |
|---|---|---|
| `Progress` | text | the running plan roll-up, e.g. `4/9 · running the gate · 12m` |
| `Waiting on` | text | why a deferred card is not running: `#12 (schema)`, `capacity`, `quota` |

`Target repo` is a single-select rather than free text on purpose: **its option list is
the whitelist.** A card naming a repo that is not an option cannot be created,
so a typo can never point a worker at a tree you did not opt in. The coordinator
refuses a card whose `Repo` does not resolve to a directory under `repo_root`,
rather than guessing from the prose.

`Status` options, and who writes them:

| Status | Written by | Means |
|---|---|---|
| `Todo` | human, or the coordinator on orphan recovery | eligible |
| `Waiting` | coordinator | eligible, deliberately deferred; will be reconsidered |
| `Running` | coordinator | a queue task is in flight |
| `Needs review` | coordinator | gate green, your turn |
| `Blocked` | coordinator | needs a person: dirty tree, bad `Repo`, or the agent held it |
| `Failed` | coordinator | gate red, or the worker reported failure |
| `Done` | human only | you closed it |

`Waiting` and `Blocked` are deliberately different. `Blocked` is your inbox: the
loop will not touch that card again until a person does something. `Waiting` is
the loop's own business: it chose to defer, it said why in `Waiting on`, and it
will reconsider next tick. Collapsing the two would mean either your inbox fills
with cards that just needed capacity, or cards that genuinely need you sit in a
queue nobody reads.

The coordinator's own bookkeeping goes in an HTML comment on the issue, which is
mechanical to parse and invisible in the UI:

```
<!-- aegis-afk task=<task_id> tick=<iso8601> attempt=<n> -->
```

That marker is the whole cross-tick state. There is no separate state file to
fall out of sync with the board.

## The tick, step by step

### 1. Preflight the run, not the card

`gh auth status` must report a token carrying the `project` scope. A missing
scope fails the whole tick loudly in the aegis log and touches no card — a
coordinator that silently cannot write the board would otherwise start workers
and lose every result.

### 2. Reap

For each card in `Running`, read `task_id` from the marker and ask
`engine.task_status(task_id)`:

- **still running** → leave it alone.
- **finished** → parse the report, verify (below), comment, move the card.
- **no such task** → the run was lost (daemon restart before persistence, or a
  cancelled task). Return the card to `Todo`, increment `attempt` in the marker,
  and comment saying the run was lost. Above `max_attempts` (default 2) the card
  goes to `Blocked` instead, so a card that kills the daemon every time cannot
  loop forever.

### 3. Gate on quota

Read the live Claude windows through `aegis.usage.quota_providers`. Three
outcomes, and the third is the one that matters:

- weekly used ≥ `weekly_stop_at` (default 60%) → start nothing.
- five-hour used ≥ `session_stop_at` (default 70%) → start nothing.
- the quota read failed (the endpoint rate-limits, and does so in practice) →
  **start nothing.** "I could not ask" is never read as "there is room".

In all three cases the reap in step 2 still runs, so work already in flight
finishes and lands on the board. The gate only ever stops *starting*.

The per-queue budget in `.aegis.yaml` is the independent backstop: it counts
dollars aegis measured itself, so it holds even if the subscription endpoint
reports nonsense. `BudgetExceeded` on enqueue returns the card to `Todo`
untouched.

### 4. Decide

The workflow assembles a briefing and hands it to the coordinator agent in one
turn. The briefing carries:

- every card in `Todo` and `Waiting`: number, title, body, `Repo`, `Priority`,
  `Deadline`, `Status`, `Waiting on`, and the coordinator's own notes from
  previous ticks;
- everything in flight: which card, how long, and its plan roll-up, so the agent
  can see that card 12 has been on "run the gate" for 25 minutes;
- what finished since the last tick and how it ended;
- the quota reading and the remaining capacity. The gate in step 3 already
  passed or the agent would not have been asked, so this is context for being
  conservative near the line, not a decision to re-make;
- the repo whitelist with each repo's isolation mode.

The board is the agent's memory. Its own prior notes come back to it every tick,
which is what lets a decision made at 02:10 still make sense at 02:20 without
any state the workflow has to keep.

It returns exactly one fenced `aegis-dispatch` block:

````
```aegis-dispatch
start:
  - card: 12
    reason: nothing else touches the parser and the deadline is tomorrow
    brief: |
      task-specific context the worker should have before it reads AGENTS.md
    review: auto | force
wait:
  - card: 14
    reason: needs the schema card 12 is writing right now
    waiting_on: "#12"
  - card: 19
    reason: no capacity this tick; next after 14
    waiting_on: capacity
hold:
  - card: 17
    reason: the body asks for a decision I am not authorised to make
notes: |
  one paragraph on the shape of this tick
```
````

`start` is a proposal. `wait` moves the card to `Waiting` and writes its reason
into `Waiting on`; deferring is always permitted, and a `Waiting` card is
reconsidered on every later tick. `hold` sends the card to `Blocked`, which is
your inbox. A card the agent mentions in none of the three keeps its current
status untouched.

Then the rails run, in code, over that block:

- a card that was not in the briefing, or is in neither `Todo` nor `Waiting`,
  is dropped. `Waiting` must be eligible: it is the agent's own deferral, and a
  rail that only admitted `Todo` would strand every card the agent deferred;
- `start` is truncated to `min(max_in_flight, queue headroom)` minus what is
  already `Running`;
- two starts naming the same `Repo` are refused unless that repo's isolation is
  `worktree` (below); the later one is demoted to `wait`;
- `review: force` is honoured; `review: auto` still gets a review if the
  mechanical triggers fire. The agent can add caution, not remove it;
- `brief` is wrapped, never trusted as the whole payload: the workflow composes
  `fixed preamble + agent brief + fixed contract`, so the report format, the
  task-list requirement and the gate command cannot be dropped by an agent
  having an off night.

Every drop is recorded on the affected card with the rail that refused it.

### 4b. Same-repo parallelism, and what it unlocks

"Does card B block card A" is only a real question if two cards in one repo
could ever run together. In a shared checkout they cannot — two agents in one
working tree produce a diff neither meant — so the rule would be mechanical and
the agent would have nothing to decide.

So isolation becomes a per-repo setting:

```yaml
repos:
  aegis:      checkout     # default: one card at a time, in the checkout
  enciclopedia: worktree   # each card gets its own git worktree
```

Under `worktree` the workflow creates `<worktree_root>/<repo>-card-<n>` off the
default branch before dispatch and removes it after the card reaches a terminal
state, leaving it in place when the card ends `Failed` so there is something to
look at. Under `checkout`, unchanged: one card at a time, in the tree.

Default is `checkout` for every repo, so v1 behaves exactly as designed before
this and the knob is opt-in per repo. It is in the spec because without it, half
of what the agent was asked to reason about is decided by physics.

### 5. Preflight the card

In the resolved checkout: `git fetch --prune`, then `git pull --ff-only` on the
default branch. Then `git status --porcelain`: a dirty tree sends the card to
`Blocked` with the dirty paths in the comment. A worker building on somebody
else's half-landed change produces something nobody can review, and on a shared
checkout that somebody is often you.

The gate command is resolved here too, by asking the repo's own Makefile which
of `gate_commands` (default `["make check", "make test"]`) it actually declares.
If none is declared the card still runs, but the report records `gate: none` and
the card lands in `Needs review` with that stated. It is never rounded up to
green.

### 6. Start

`engine.enqueue(worker_queue, payload, callback=False)`, set `Status: Running`,
write the marker. The payload is built in the next section.

## What the coordinator tells a worker

A queue worker starts with no context, so the payload is self-contained: the
card's body verbatim, the issue URL, the absolute checkout path, the default
branch, the resolved gate command, and the instruction to read that repo's
`AGENTS.md` and its know-how docs and do what they say.

That last part is deliberate. **The coordinator does not decide what artifact
the task produces.** A branch and a pull request, a commit on `main`, a rendered
PDF, a new doc — the repo's own conventions decide, and the worker is the thing
that read them. The coordinator's demand is narrower: whatever you did, report it
in a form I can put on the card.

Two requirements sit on the worker, not one.

**Keep a task list, from the first turn.** Before doing anything the worker
writes out its plan through whatever task-list its harness exposes — `TodoWrite`
under Claude Code, the plan update under ACP — one item per step, and keeps it
current as it goes. This is not bookkeeping for its own sake: aegis's plan plane
already reads that list (`PlanTracker` consumes exactly these snapshot sources),
so the coordinator can mirror it onto the card without the worker spending a
token on progress reports. The payload says so plainly, because a worker told
*why* it must keep the list current keeps it current.

**Report once at the end.** The last thing a worker says must be exactly one
fenced `aegis-report` block:

````
```aegis-report
status: needs-review | blocked | failed
summary: one line, imperative, what changed
gate: make check -> 0
artifacts:
  - https://github.com/owner/repo/pull/12
  - 3f9a1c2
changed: 7 files
judgement:
  - the card did not say which of two behaviours; chose the first
notes: |
  free text
```
````

Notes on the fields:

- `status` has no `done`. A worker cannot mark its own work finished; the
  furthest it can claim is `needs-review`.
- `gate` records the command **and** the exit code the worker saw. The
  coordinator re-runs the same command and compares. A disagreement is the
  interesting case, and it is the one the tests target.
- `judgement` is the highest-value field. The worker does not decide whether it
  gets reviewed — that is the coordinator's call — but a worker admitting where
  the card was ambiguous is the strongest signal the coordinator has.

A missing block, a malformed block, or more than one block is treated as
`failed` with the raw final message quoted on the card. A worker that cannot
report has not demonstrably done anything.

## Verification: mechanical first, and it short-circuits

The coordinator runs the resolved gate itself, in that checkout, and compares
the exit code with what the worker claimed.

**Red → `Failed`.** The output goes on the card. No reviewer is spawned. Running
the mechanical check first is the whole reason it is first: a broken build never
costs an agent.

**Green → a reviewer runs if anything asks for one.** The dispatching agent may
have marked the card `review: force`, in which case it runs regardless. Failing
that, the mechanical triggers decide: `judgement` non-empty, when `changed` exceeds `review_changed_files`
(default 5), or when the card states no acceptance criteria — no task-list item
and no line matching `acceptance_markers` (default `["done when", "acceptance"]`)
— and its body is shorter than `vague_body_chars` (default 400).

That last trigger is deliberately mechanical. "The prose was vague" is the real
reason you want a reviewer, and it is not computable, so the design substitutes
two things that are: a card that neither enumerates what to do nor says how you
would know it worked is the shape a worker has to guess at. The whole rule is a
pure function of the report plus the card, so it is table-testable, and the
inputs it fired on are recorded on the card, so a surprising decision can be read
back rather than re-derived.

The reviewer is a `delegate` onto `review_queue`, bounded by that queue's
`max_parallel`, and given `review_timeout_s` (default 600). It reads the diff
against the card's prompt and returns a verdict. On timeout the card still moves
to `Needs review` with the review marked inconclusive — a review that could not
finish must not strand a card.

Either way the card lands in `Needs review` with one comment carrying the
summary, the gate result as the coordinator measured it, the artifact links, the
judgement calls, and the reviewer's verdict when there is one.

## What the coordinator says on a card

The pinned comment is the coordinator's voice, rewritten in place each tick
rather than appended, so a card carries one current statement instead of forty
stale ones. It has three sections, each owned by a different part of the system:

```markdown
### Coordinator
Deferred this tick. The schema this card imports is being rewritten by #12,
which is 6/9 through and has been running 14 minutes. Next tick unless #12
lands in `Failed`, in which case this goes to `Waiting on: #12 (failed)`.

_Refused by a rail this tick: proposed starting alongside #12 in the same
checkout; `aegis` is `isolation: checkout`, so it was demoted to `wait`._

### Plan
- [x] read AGENTS.md and the know-how menu — 1m12s
- [x] write the failing test — 4m03s
- [ ] make it pass  ← running, 11m
- [ ] run the gate

### Result
_(written once, when the card reaches a terminal state)_
```

`Coordinator` is the agent's own reasoning, and the rail-refusal line is written
by the workflow rather than the agent — an agent that keeps proposing something
illegal should not be the one narrating that it did.

`Plan` is the mirror of the worker's task list, maintained by the progress
schedule below.

`Result` is the verified outcome: the summary, the gate as the coordinator
measured it, the artifact links, the judgement calls, the reviewer's verdict.

The dependency picture comes out of this for free. A card saying "waiting on
#12" produces a GitHub cross-reference, so #12 shows a backlink and you can walk
the chain from either end without the coordinator maintaining a graph.

## Progress: a second, cheaper schedule

The reconciler runs every ten minutes and costs agent calls. Mirroring a plan
costs none — it is a read off the session manager and a write to the board — so
it gets its own schedule at a much tighter cadence.

```yaml
afk-progress:
  workflow: afk_progress
  cron: "*/2 * * * *"
  on_overlap: skip
```

Each fire, for every card in `Running`:

1. Resolve the card's `task_id` to the worker's handle, and the handle to its
   plan.
2. Write the roll-up into the `Progress` field: `done/total`, the current task's
   subject, and how long it has been on it — `4/9 · running the gate · 12m`.
3. Rewrite the plan section of the pinned coordinator comment with the full
   checklist, one line per task with its status and accumulated working time.

Three rules keep this from being noise:

- **No plan is itself a reading.** A worker whose roll-up is absent gets
  `Progress: no plan reported`, not a blank. A worker ignoring the instruction
  is something you want to see on the board.
- **Unchanged means no write.** The tick compares the roll-up against what the
  card already shows and skips the API call when they match. Five cards at a
  two-minute cadence is 150 potential writes an hour; in practice it is a small
  fraction of that.
- **It never writes `Status`.** This schedule starts nothing, reaps nothing and
  moves nothing. That is what makes it safe to run alongside the reconciler: the
  two write disjoint fields, so a fire landing mid-reconcile cannot corrupt the
  state machine.

### Why two minutes, when nobody is watching

At two-minute granularity there is no human reading it live — that is the whole
premise. The cadence buys two things instead.

The first is an accurate record. `PlanSnapshot` carries `updated_at`, so the
card ends up with a per-task working time you can read the next morning to see
where the hour actually went, rather than a single "took 47 minutes".

The second is **stall detection**, which is the real reason to poll. When a
worker's roll-up has not changed for `stall_after_s` (default 1800), the card
shows `Progress: stalled 34m on "run the gate"` and `notify_cmd` fires once.
The coordinator does not kill it — `aegis_cancel` exists and cancelling a
worker that was merely slow throws away real work, so v1 surfaces and leaves the
decision to you. A stalled card is still reaped normally by the reconciler when
its task eventually ends.

## Configuration

All knobs live under one workflow config block, set as `@workflow.configure`
defaults and overridable per-schedule through `args:`, which is the mechanism
aegis already documents for workflow config.

```yaml
workflows:
  - afk                      # opt in to the built-in

schedules:
  afk:
    workflow: afk
    cron: "*/10 * * * *"
    args:
      owner: syalia-srl
      owner_type: org        # org | user
      project: 3             # project number
      repo_root: /home/apiad/Workspace/repos
      worker_queue: afk
      review_queue: afk-review
      max_in_flight: 5
      weekly_stop_at: 60
      session_stop_at: 70
      max_attempts: 2
      review_changed_files: 5
      review_timeout_s: 600
      vague_body_chars: 400
      acceptance_markers: ["done when", "acceptance"]
      gate_commands: ["make check", "make test"]
      coordinator_queue: afk-coordinator   # read-only profile; writes nothing
      worktree_root: /home/apiad/.cache/aegis-afk-worktrees
      repos:                 # isolation per repo; absent means `checkout`
        enciclopedia: worktree
      notify_cmd: ""         # e.g. bin/notify-telegram.sh; empty disables
      field_names:           # for boards that are not in English
        status: Status
        repo: Repo
        priority: Priority
        deadline: Deadline
        progress: Progress

  afk-progress:
    workflow: afk_progress
    cron: "*/2 * * * *"
    args:
      owner: syalia-srl
      owner_type: org
      project: 3
      stall_after_s: 1800
      notify_cmd: ""
      field_names:
        status: Status
        progress: Progress
```

Defaults are chosen so that naming `owner`, `project` and `repo_root` is enough
to get a working coordinator. `notify_cmd` defaults to empty because
notification belongs to the operator's machine, not to aegis: when set, it is
invoked once per card reaching a terminal state, with a one-line message.

## Modules

One package, each file with one job, because the coordinator is mostly a
parser, a decision table and an API client and those want separate tests.

| File | Holds |
|---|---|
| `__init__.py` | the `@workflow` entry point — the tick, in the six steps above |
| `board.py` | the GitHub Projects client: read items, set a field, comment, parse and write the marker |
| `report.py` | the `aegis-report` parser and its failure modes |
| `decide.py` | the rails: pure functions that validate a dispatch proposal, plus the quota gate and the review triggers |
| `dispatch.py` | the `aegis-dispatch` parser, and building the coordinator agent's briefing |
| `payload.py` | building the worker and reviewer prompts |
| `preflight.py` | git fetch / pull / dirty check, gate-command resolution |
| `progress.py` | the `afk_progress` workflow: plan roll-up to `Progress` field and pinned comment, plus stall detection |

`decide.py` no longer holds the judgement — the agent does — and that changes
what it is for rather than making it smaller. It now answers "is this proposal
allowed", which is the question you *can* put in a table test, where "is this
the right card to run next" never was. Every rail in the split table above is one
pure function here, and its test is an illegal proposal that must be refused.

## Changes outside the package

Three small additions, all of them passthroughs to state that already exists.

**`WorkflowEngine.task_status(task_id)`** wraps `QueueManager.status`, which
already returns `status`, `result`, `error`, `completed_at` and
`queued_position`. The engine holds the queue manager and simply does not
expose it. Without this the reconciler shape is impossible.

**`QueueManager.status` gains `worker_handle`.** The field is already on the
`Task` record and is already surfaced in other places (`manager.py:496`); it is
merely absent from the `status` dict. This is the link from a card's `task_id`
to the session whose plan the progress tick reads.

**`WorkflowEngine.plan_state(handle)`** wraps `SessionManager.plan_state`, the
same method backing `aegis_peer_plan`. The roll-up needs nothing new —
`engine.list_sessions()` already returns `SessionInfo.plan` — but the full
checklist for the pinned comment does.

Reading the subscription quota needs no change: the package imports
`aegis.usage.quota_providers` directly.

## Testing

Pure, table-driven, no network:

- the report parser: absent block, two blocks, unknown `status`, missing `gate`,
  a `gate` line that does not parse, body text after the block.
- the quota gate: under both thresholds, over each one, over both, and the
  unreadable case — which must return "start nothing", not "start everything".
- capacity and eligibility: two cards naming one repo, a card whose `Repo` is
  not in `repo_root`, a `Running` card with no marker.
- the review decision: each trigger in isolation, none of them, and
  `review: force` with none of them firing.

**The rails, tested adversarially.** These are the tests that carry the design,
because the agent is now the part nobody can unit-test, and the rails are the
reason that is acceptable. Each one is a dispatch proposal written to break a
specific rail, asserted to be refused *and* to leave the rest of the proposal
intact:

- a card number that was in no briefing;
- a card already `Running`, proposed again;
- `start` longer than capacity, and longer than queue headroom;
- two starts in one `checkout` repo — the second must be demoted to `wait`, not
  dropped, because dropping it loses the reason;
- two starts in one `worktree` repo — must be allowed, which is the test that
  proves the isolation setting is read at all;
- a `Repo` that resolves outside `repo_root`, including via `..`;
- a `brief` that tries to countermand the fixed contract ("ignore any earlier
  instruction about a report block") — the composed payload must still end with
  the contract, and this is asserted on the composed string, not on the
  intention;
- a proposal with no `aegis-dispatch` block at all, and one with two.

Every refusal is asserted to land on the card. A rail that refuses silently is
the same as no rail, because the next morning you cannot tell a coordinator that
chose well from one that kept being stopped.
- the progress formatter: a roll-up with no current task, a task that never
  entered `in_progress` (`working_s is None`, which must not render as `0m`),
  an absent plan, and a roll-up identical to what the card shows — which must
  produce no write.
- stall detection: `updated_at` just inside and just outside `stall_after_s`,
  and the same stalled card on a second fire, which must not notify twice.

Against a throwaway project in a scratch org, one end-to-end per path:

- a worker that reports `gate: make check -> 0` on a tree where `make check`
  exits 1 lands the card in `Failed`. **This is the test the design exists for.**
  It is written by making a passing card lie, confirming the card moves to
  `Failed`, and confirming the same card with an honest report moves to
  `Needs review` — so the check is shown to distinguish the two rather than
  merely to be green.
- a dirty checkout sends the card to `Blocked` and starts no worker.
- a `Running` card whose task id is unknown returns to `Todo` with `attempt`
  incremented, and to `Blocked` on the third sighting.
- a worker that keeps a task list drives the `Progress` field through at least
  two distinct values, and a worker that keeps none leaves `no plan reported`
  on the card. Asserting only the first would pass against a coordinator that
  writes any text at all.
- two cards in one `worktree` repo run together, land in separate worktrees, and
  produce two independent branches; the worktrees are gone afterwards except the
  one whose card ended `Failed`.

## Delivery, in vertical slices

1. `engine.task_status`, `board.py`, and the state machine driven by a stub
   worker that writes a fixed report. Proves cards move and the marker survives
   ticks, with no agent spend at all.
2. Real workers: `payload.py`, `preflight.py`, `report.py`, the mechanical gate.
   End of this slice the loop does useful work.
3. `progress.py`, `engine.plan_state`, `worker_handle` on the task status, and
   the two-minute schedule. Placed here rather than last because it is the
   slice that makes the loop legible: until the board shows a plan advancing,
   a run you slept through is indistinguishable from a run that hung.
4. **The rails, before the agent that needs them.** `decide.py` and
   `dispatch.py`: the quota gate, the proposal validators, the parser, and the
   composed-payload contract — all driven by hand-written proposals in tests,
   with selection still done by a trivial deterministic stand-in. At the end of
   this slice every adversarial test above passes and no agent has been asked
   anything.
5. Swap the stand-in for the coordinator agent: the briefing, the read-only
   profile, the `Coordinator` narration, `Waiting` and `Waiting on`. The rails
   are already proven, so this slice can be judged on whether the decisions are
   *good* rather than whether they are *safe*.
6. Per-repo `worktree` isolation.
7. The reviewer stage, stall detection, `notify_cmd`, `docs/afk.md` in the
   mkdocs nav, the CHANGELOG entry.

## Known limits, stated rather than hidden

A card is attempted at most `max_attempts` times and then parked in `Blocked`;
there is no backoff and no partial resume of a worker that died mid-task. Its
branch is left in the checkout for a human to look at, which is why the dirty
tree check exists in step 5 — the next card in that repo will refuse to start
until someone cleans up. That is the intended behaviour and not a bug: an
unattended coordinator should stop touching a repo it has left in an unknown
state.

### The agent is the part that cannot be tested

Every previous version of this design could be judged by reading its rules. This
one cannot: two ticks over identical boards may dispatch differently, and
neither is wrong. That is the point of the change and it is also its cost.

What the design does about it is refuse to let the untestable part be the part
that can hurt you. The rails are deterministic, adversarially tested, and sit
between every agent decision and every write. The agent's discretion is
monotone toward caution. Its reasoning is on the card, in its own words, next to
any rail that refused it — so the way you evaluate this coordinator is by
reading a morning's worth of cards and judging the decisions, which is the only
honest way to evaluate judgement.

## Out of scope for the first version

Routing triage to a cheaper provider's pool; workers on execution hosts other
than the one the coordinator runs on; letting the coordinator split a card into
follow-up cards; reviewing the reviewer.
