---
title: Schedules
kind: howto
when: firing a prompt on a clock, every few minutes, on a cron line or once on a set date, into a new session or into one already open
hints:
  - Schedule a prompt to fire every morning, on a cron line or once on a date. #create-a-schedule
  - A schedule can fire into a session that is already open. #where-it-fires
---

A schedule sends a prompt to an agent when a clock says so. Each tick goes
through four layers, and only a tick that passes all of them fires: the
clock, the overlap rule, an optional bash check, and the fire itself.
Schedules live in the server's state, not in `.aegis.yaml`, and people and
agents make them the same way.

## The view

The Schedules button in the top bar, or Alt+K, opens the view. The button
counts the schedules, in red while one is paused by errors. The list is on
the left; on the right are the selected schedule's four layers and its recent
runs, with each stretch of quiet ticks folded into one row until "show quiet
ticks" is checked. A fire's row links to its session and shows how its last
turn ended, the stdout that fired it, how long it took and what it cost.

Edit and + New schedule open the editor in the same panel, laid out as the
same four layers, with the quota lines between the check and the fire: per
pool, each window can hold a line on pace or under a percent, and "no limit"
sets none. It checks the clock as you type and shows the next ticks, Test
check runs the check once without saving, and Save stays off until the name,
the clock, the prompt and every percent are valid. Clocks read the server's
zone, and the view names it.

With a linked server, a chip beside the title picks whose schedules the view
shows, and everything in it, creating, editing and running included, acts on
that server. The button's count covers every server. An agent's card in a
linked session's transcript acts on that session's server.

## Create a schedule

Call `schedule_create` with:

- `name`: two or three lowercase words joined by hyphens, unique on the
  server;
- `clock`: `{"every": "5m"}` (m, h or d, at least 1m), `{"cron": "0 6 * * 1-5"}`
  (five fields, with names like `mon` and `jan`, or `@hourly`, `@daily` and
  `@weekly`), or `{"at": "2026-10-15 09:00"}`. Cron and at read the server's
  local time;
- `overlap` (default `wait`): what a tick does while the last run is still
  working. `wait` holds it until the run finishes, and at most one tick
  waits; `skip` drops it; `parallel` fires anyway, and needs a new session
  per fire;
- `check` (optional): `{"bash": "...", "timeout_s": 30}`, described below;
- `fire`: where the prompt goes, described below;
- `prompt`: what the agent is asked on each fire;
- `quota` (default: the weekly window on pace): the launch lines a fire
  waits under, described below.

An every counts from when the schedule was created or last edited. A cron
line with no tick in the next year is refused. An at fires once and then
shows as finished, with its runs, for seven days.

`schedule_test` tries a clock and a check without saving anything. It answers
with the next three ticks and whether the check would fire now, and why.

## An agent's schedule waits for you

A schedule an agent creates is born paused, and an agent's edit pauses it
again. Under the agent's call, the transcript shows a card with the schedule
in full: its clock, overlap, check, where it fires and the prompt. Unpause on
that card, or Resume in the Schedules view, starts it. An agent cannot
unpause or fire a schedule that is waiting for you. A schedule you create
yourself starts at once.

### On another server

Over a [trusted link](links#trusted-links), every `schedule_*` tool takes a
schedule on the other server as `name@server`, as `queue_enqueue` does with
`queue@server`. Put one there when the clock must keep ticking while this
machine sleeps:

```json
{"name": "nightly-tests@host", "clock": {"every": "1h"}, "fire": {"agent": "opus"}, "prompt": "run the tests"}
```

The schedule lives and ticks on that server, and it fires there. A
`fire.cwd`, a `fire.session` and an agent's name are that server's; with no
`cwd`, a fire starts in that server's working directory. A reply names the
schedule `name@server` and adds `server`. `schedule_list` gives this server's
schedules and then each trusted server's, in the same form, and leaves out a
server that cannot answer now. `schedule_test` takes `name@server` to try a clock
and a check there, and that schedule need not exist yet.

The rules are the same as here, and the far server applies them. What the agent
creates waits for a person, an edit waits again, no agent can unpause or fire one
that is waiting, an agent changes only what it created, and a fire stays below the
permission of the agent that created it.

The card shows under the agent's call in this session, and Unpause acts on the
far server. The far server's Schedules view shows the schedule as
well. Two cases have no Unpause on the card, which says to use the far server's
Schedules view instead:

- The agent is on the server that accepted the link, and calls back over it.
  Its browser does not follow the other server's schedules, and the tool's reply
  has a `note` that says so. Open the Schedules view of the server that holds
  the link.
- You read that session through a link. A card there may only act on the
  session's own server, so a far session can never aim your click somewhere
  else. Pick the server in the Schedules view here.

A plain link refuses all of this, as it does `queue@server`, and a member's agent
is refused. Nothing is relayed to a third server.

## Fire a workflow

To make a schedule run a saved workflow, give it a /workflow or /loop line as its
prompt: it runs that workflow at each fire, with no click, at the version a
person approved. When
the workflow changes, the schedule pauses and its card shows the change until
you unpause it. One saved before schedules pinned a version waits for a
person from the server's start, and no agent can unpause it. No agent can
`schedule_fire` such a schedule, paused or not, across a link included:
the approval covers its clock, not runs on demand. A person's Fire now on a
paused one works only when no approval is due and the workflow is still the
approved version. See [Fire one from a schedule](workflows#fire-one-from-a-schedule).

## The check

The check runs on each tick that passes the overlap rule, in the directory
the fire runs in. Exit 0 with a stdout different from the stdout of the last
fire fires the tick, and that stdout is appended to the prompt, cut at 8 KB.
Exit 0 with the same stdout, and exit 1, are quiet ticks: nothing fires. Any
other exit code, or the timeout (30 seconds by default, 300 at most), is an
error. Three errors in a row pause the schedule and show a toast.

A check that fires once for each failed run on main, and stays quiet while
main is green:

```bash
gh run list -b main -L1 --json conclusion,databaseId \
  -q 'select(.[0].conclusion=="failure") | .[0].databaseId' | grep .
```

## Where it fires

`fire` is one of:

- `{"target": "new", "agent": "opus", "close": "done", "spawn": false}`: a
  fresh session per fire, from the agent named (the default agent when it is
  left out), in your working directory unless `cwd` names another;
- `{"target": "existing", "session": "<handle>"}`: that session's inbox. A
  session in the middle of a turn holds the message until the turn ends, and
  a stopped one is resumed to receive it. If the session is archived, the
  schedule pauses.

Either way the agent gets the prompt under a header line,
`> from schedule:<name> · run#<n> · tick <HH:MM>`.

If the owner's quota gate is closed when a new session should start, the
tick waits and fires when the gate opens.

## When a run finishes

A run is finished when its session is not working, holds no messages, has no
live monitor and no open background task, and no session it started is still
working. A run whose last turn asked the person something counts as
finished.

A new run is then closed by its `close` rule, read from its last `turn_end`.
`done` archives it when that said done, and keeps it open when it asked the
person, had something to read, failed, or never called `turn_end`. `always`
archives every run and `never` archives none. A run in an existing session is
never closed.

## Starting other work

A run with `spawn: false`, the default, is refused `session_spawn`,
`queue_enqueue` and `schedule_create`. With `spawn: true` they work as usual.
The sessions the run starts carry the same flag, the run stays live until
they stop working, and each one is closed by the run's rule, read from its
own last `turn_end`.

## Quota lines

A fire waits while the pool it draws on is over the schedule's own launch
line, and goes once, at most one tick, when it drops back under. `quota` maps
a pool to its windows and each window to a percent from 0 to 99 or to
`"pace"`, which holds while the window is projected to pass 100% by its
reset. The pool claude has the windows session and weekly_all, and the pool
opencode-go has rolling, weekly and monthly. Without `quota` a
schedule holds while either weekly window is off pace; `{}` sets no line.

```json
{"claude": {"session": 60, "weekly_all": "pace"}}
```

The lines apply to a fire into an open session too. They only hold what has
not started: a live run goes on. A pool aegis has never read sets no line, and
a token's own quota gate still applies on top of them.

## Change, pause and fire

`schedule_update` replaces a definition. It applies from the next tick and
leaves a live run alone. `schedule_pause` stops a schedule from ticking, and
`schedule_resume` starts it again from now with its error count cleared; the
ticks that fell during the pause do not fire. `schedule_fire` fires at once,
skipping the clock and the check but not the overlap rule. `schedule_delete`
removes a schedule, and a live run keeps going.

An agent changes only the schedules it created, a member only the ones it
owns, and an admin any. A schedule an agent creates never starts an agent
with more permission than its creator had.

## Read the runs

`schedule_list` gives every schedule with its next tick and its live runs.
`schedule_runs` gives one schedule's fires and ticks, the newest at the top, 50
per call; pass `quiet` to include the ticks that changed nothing.

## Restarts and sleep

The ticks missed while the server was down or the machine slept become one
tick when it is back, and that tick goes through every layer. A run that was
live is live again after a restart, and a held tick stays held.
