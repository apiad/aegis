---
title: Schedules
kind: howto
when: firing a prompt on a clock, every few minutes, on a cron line or once on a set date, into a new session or into one already open
---

A schedule sends a prompt to an agent when a clock says so. Each tick goes
through four layers, and only a tick that passes all of them fires: the
clock, the overlap rule, an optional bash check, and the fire itself.
Schedules live in the server's state, not in `.aegis.yaml`, and people and
agents make them the same way.

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
- `prompt`: what the agent is asked on each fire.

An every counts from when the schedule was created or last edited. A cron
line with no tick in the next year is refused. An at fires once and then
shows as finished, with its runs, for seven days.

`schedule_test` tries a clock and a check without saving anything. It answers
with the next three ticks and whether the check would fire now, and why.

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
