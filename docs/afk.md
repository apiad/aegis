# AFK coordinator

A board of prompt cards, emptied while you sleep.

`afk` is a built-in workflow that reads a GitHub Project of issues — each
issue body being a self-contained prompt — hands each one to a queue worker,
re-runs the repo's own gate itself, and moves the card. A companion workflow,
`afk_progress`, mirrors each worker's task list onto its card so you can see
what is happening without attaching to a session.

Both run inside `aegis serve`, fired by the scheduler. The board is the only
state either of them keeps: nothing is remembered between runs, so a crash
costs one tick.

## What one tick does

1. Check that `gh` can write the board. If it cannot, the tick touches
   nothing and says so. Starting workers you then cannot record would run the
   expensive half and lose the half you wanted.
2. Read every card on the project, paginated, with its field values.
3. Reap: for each card in `Running`, look up the task the marker names and
   see whether it finished.
4. Ask the quota gate whether there is subscription room to start anything.
5. Start: rank the eligible cards, run preflight on each repo, enqueue a
   worker, and move the card to `Running`.

Reaping happens before the quota gate, so a closed window still lets finished
work land on the board. The gate only stops new starts.

The workflow returns a line like `reaped 2, started 1 (weekly 41%, five-hour
12%)`.

## Prerequisites

- `gh` authenticated with the `project` scope. The tick runs `gh auth status`
  and aborts unless the output mentions `project`.
- A GitHub Project whose items are real issues, not draft issues. Draft
  issues are skipped: they have no comment thread, and the pinned comment is
  where the report goes.
- Every repo you want worked on, checked out under one root directory.

## The board

The coordinator writes two fields and reads the rest.

| Field | Type | Who writes it |
|---|---|---|
| `Status` | single-select | you set `Todo`; the coordinator moves it after that |
| `Repo` | single-select | you — its options are the whitelist |
| `Priority` | single-select | you |
| `Deadline` | date | you, optionally |
| `Progress` | text | `afk_progress` |

`Status` must carry every option the coordinator writes, or the write fails
and the card stays where it was: `Todo`, `Running`, `Needs review`, `Blocked`
and `Failed`. Add `Done` as well — the coordinator never sets it, but you
will want somewhere to put a card you have reviewed.

`Repo` is a single-select rather than free text on purpose. A card can only
name a repo you put on the list, and the path it resolves to is checked
against the root a second time before any worker is dispatched, so a card
cannot point a worker at a tree you never opted in to.

`Priority` is ranked by the `priority_order` list. A card whose priority is
empty, or is a name not on that list, sorts last. Ties break by nearest
`Deadline`, then by issue number, so the ordering is the same every tick.

If your board is not in English, rename any of this with `status_names` and
`field_names` (see below) rather than renaming the options.

### What each status means

| Status | Set by | Means |
|---|---|---|
| `Todo` | you, or the coordinator after a lost run | ready to start |
| `Running` | the coordinator | a worker holds this card |
| `Needs review` | the coordinator | the gate was green; a person should look |
| `Blocked` | the coordinator | the worker said it was blocked, or preflight refused, or the run was lost too many times |
| `Failed` | the coordinator | the gate was red, or the worker's report could not be read |
| `Done` | you | nothing in the code writes this |

There is no path to `Done`. The furthest a worker may claim is
`needs-review`, and the coordinator will not promote it past that.

## Configuration

Name the built-in in `workflows:` to register it. That one entry registers
both `afk` and `afk_progress`:

```yaml
workflows:
  - afk

queues:
  afk:
    agent: opus
    max_parallel: 5
    budgets:
      - {window: 1d, max_usd: 20}

schedules:
  afk:
    workflow: afk
    cron: "*/10 * * * *"
    timezone: America/Havana
    on_overlap: skip
    args:
      owner: my-org
      project: 3
      repo_root: /home/me/repos

  afk-progress:
    workflow: afk_progress
    cron: "*/2 * * * *"
    on_overlap: skip
    args:
      owner: my-org
      project: 3
```

`owner`, `project` and `repo_root` have no defaults and the workflow raises if
they are missing, so a misconfigured schedule fails at its first fire instead
of doing nothing every ten minutes forever. `afk_progress` needs `owner` and
`project` only. Everything else has a default:

| Key | Default | Means |
|---|---|---|
| `owner_type` | `org` | `org` or `user`; picks which GraphQL root the board hangs off |
| `worker_queue` | `afk` | the queue workers are enqueued on |
| `max_in_flight` | `5` | cards in `Running` at once, counted after reaping |
| `weekly_stop_at` | `60` | start nothing above this percent of the weekly window |
| `session_stop_at` | `70` | start nothing above this percent of the five-hour window |
| `max_attempts` | `2` | how often a card may lose its worker before it is parked in `Blocked` |
| `review_changed_files` | `5` | a report claiming more changed files than this is flagged |
| `vague_body_chars` | `400` | a card body shorter than this, with no checklist and no acceptance marker, is flagged |
| `acceptance_markers` | `["done when", "acceptance"]` | phrases that count as a card saying how you would know it worked |
| `gate_commands` | `["make check", "make test"]` | tried in order against the repo's Makefile; the first target that exists is the gate |
| `priority_order` | `["Urgent", "Important", "Normal"]` | ranking order for the `Priority` field |
| `stall_after_s` | `1800` | a plan untouched for this long is reported as stalled |

Two more keys take mappings and default to empty, for a board in another
language:

```yaml
args:
  status_names:
    todo: Pendiente
    running: En curso
  field_names:
    status: Estado
    repo: Repositorio
```

`status_names` accepts `todo`, `waiting`, `running`, `needs_review`,
`blocked`, `failed` and `done`. `field_names` accepts `status`, `repo`,
`priority`, `deadline`, `progress` and `waiting_on`. Whatever you leave out
keeps its English name.

Put the schedules in `.aegis/schedules/afk.yaml` rather than `.aegis.yaml` if
your config is shared between machines. `.aegis/` is per-host, and two
coordinators working one board will fight over it.

## Starting a card

A card is eligible when the issue is open, its `Status` is `Todo` or
`Waiting`, its `Repo` resolves to a directory under `repo_root`, and no other
card in that repo is already running. One worker per repo at a time: two
agents in one checkout would stage each other's half-written files.

Before anything is enqueued, preflight runs in the repo and refuses on
anything it does not like:

- `git fetch --prune` must succeed.
- `git pull --ff-only` must succeed — a repo that needs a merge needs a
  person.
- `git status --porcelain` must be empty, checked after the pull rather than
  before, because a pull can leave conflict markers in a tree that read clean
  ten seconds earlier.

A refusal moves the card to `Blocked` and puts the reason on it.

The gate command is then resolved from the repo's own Makefile — the first of
`gate_commands` whose target the Makefile actually declares, ignoring
comments and `.PHONY` lines. A repo with neither target gets `none`, and the
card is still worked; the result section then says plainly that nothing was
verified. The gate comes from the repo rather than from config because the
repo is the thing that knows, and a gate named in config drifts away from the
gate that exists.

## What a worker is told

The payload is three parts in a fixed order: a preamble the code owns, the
card body, and a reporting contract the code owns. The contract goes last so
that nothing the card says can come after it.

The preamble tells the worker to read the repo's `AGENTS.md` and its
`know-how/` docs and follow them. What a task should produce — a branch and a
pull request, a commit, a document — is the repo's call, not the
coordinator's, and is not stated in the payload. The coordinator requires
only two things: keep a harness task list from the first turn, and end with
exactly one fenced `aegis-report` block.

Fenced blocks inside the card body are neutralised before the body is
pasted in, using a zero-width space inside the backticks. A card that
documents this feature quotes the report format, and passed through verbatim
it would hand the worker two templates and the parser two blocks.

The report is YAML:

```
status: needs-review | blocked | failed
summary: one line, imperative, what changed
gate: <the command you ran> -> <its exit code>
artifacts:
  - a pull request URL, a commit sha, or a path
changed: <number of files>
judgement:
  - each call you made where the card did not say what to do
notes: |
  anything else worth knowing
```

`status`, `summary` and `gate` are required, and `status` must be one of the
three named. Anything else — no block, two blocks, invalid YAML, a status the
parser does not know — moves the card to `Failed` with the worker's raw final
message quoted underneath. A run nobody can read is a run that did not
demonstrably happen.

## What gets verified

The coordinator re-runs the gate itself, in the repo, and puts both numbers
on the card: the exit code it measured and the exit code the worker claimed.
When they disagree the card says so, and the coordinator's run is the one
that counts.

A red gate is `Failed` whatever the worker reported, and the first 2000
characters of the gate output go on the card. A green gate is `Needs review`,
never `Done`.

Three things flag a green card as wanting a closer look. They are named in
the card's coordinator note; they do not change its status, because
everything green lands in `Needs review` anyway:

- `judgement` — the worker recorded a call the card did not make for it.
- `changed` — the report claims more than `review_changed_files` files
  changed.
- `vague` — the card body has no `- [ ]` checklist, contains none of
  `acceptance_markers`, and is shorter than `vague_body_chars`. "The prose
  was vague" is the real reason you want a reviewer and is not computable, so
  it is stood in for by two things that are: a card that neither enumerates
  what to do nor says how you would know it worked.

## The quota gate

Before starting anything, the coordinator reads the live Claude subscription
windows and refuses to start new work in three cases: the weekly window is at
or above `weekly_stop_at`, the five-hour window is at or above
`session_stop_at`, or there is no usable reading at all.

The third case is the one that matters. A reading that could not be taken —
the endpoint rate-limits, and it does — is never read as permission. The
reason lands in the tick's return line and in the log, so a night where
nothing started says why.

Work already in flight always finishes. The gate stops starts, not workers.

## Progress and stalls

`afk_progress` costs no agent calls: it reads the queue and the session
roster and writes the board. That is why it can run every two minutes while
the reconciler runs every ten. It never writes `Status`, never starts
anything and never reaps anything, so the two schedules are safe to run
alongside each other — they write disjoint fields.

For each card in `Running` it finds the worker's handle through the task the
marker names, takes that session's plan roll-up, and writes one line into
`Progress`:

```
2/5 · wire the parser into the CLI · 3m40s
```

A worker that reported no plan gets `no plan reported`, which is a reading
rather than a blank: a worker ignoring the task-list instruction is something
you want to see.

When the roll-up's own timestamp is older than `stall_after_s`, the line
becomes `stalled 31m on 2/5 · …`. The age is taken from the snapshot's
`updated_at` rather than computed here, because a snapshot assembled at read
time is never stale by construction and the check would be a branch that can
never be taken. A timestamp that cannot be parsed reads as fresh: calling a
plan stalled because its clock was unreadable would put a false alarm on the
board.

Nothing notifies you about a stall. It appears on the card and waits to be
noticed.

If the computed line matches what the field already says, the tick skips the
card entirely and writes nothing. The full checklist goes into the pinned
comment's `Plan` section, replaced in place so the `Coordinator` and `Result`
sections survive — the progress schedule has no way to reconstruct those.

## When a run is lost

If the marker names a task the queue no longer knows about — usually the
daemon restarted — the card goes back to `Todo` with its attempt counter
raised, and the next tick starts it again. Once the counter reaches
`max_attempts` the card goes to `Blocked` instead. A card that keeps losing
its worker is not something an unattended loop should keep retrying.

## How it talks to GitHub

Two choices here are worth the sentence they cost.

Reads go through the GraphQL API rather than `gh project item-list --format
json`. That command lowercases custom field names into its JSON keys, turning
`Categoria` into `categoria` and mangling any field whose name contains a
space. Field names are part of this package's contract with your board, so
they are read verbatim.

The pinned comment is found by a marker — an HTML comment carrying the task
id, the tick timestamp, the attempt number and the gate command — rather than
with `gh issue comment --edit-last`. That flag targets the authenticated
user's most recent comment, and the coordinator authenticates as you. The
first time you reply to a card, `--edit-last` would overwrite your own reply.
Marker values are shell-quoted, so `gate=make check` survives the round trip;
without that it would parse back as `gate=make`, and the coordinator would
re-run a different command from the one it gave the worker.

Markers inside fenced blocks are ignored when reading, so a card that
documents this feature cannot point the reaper at a task id that never
existed.

Comment bodies are cut at GitHub's 65536-character limit, with a line saying
they were cut. A silently rejected comment would leave the card updating with
nothing to read.

## Not implemented yet

The design goes further than the code does. These are the gaps, so you do not
configure around something that is not there:

- **Notification.** Nothing tells you when a card lands in `Needs review`,
  `Failed` or `Blocked`, and nothing tells you when a worker stalls. There is
  deliberately no `notify_cmd` key: a config key that is read, documented and
  does nothing is worse than an absent one, because the first person to set
  it concludes the loop is broken.
- **`Waiting`.** The status is treated as startable alongside `Todo`, but
  nothing in the code ever sets it. If you set it by hand expecting a card to
  be held back, the next tick will start it.
- **`Waiting on`.** The field name is reserved in the code and is neither
  read nor written. Your board does not need it.
