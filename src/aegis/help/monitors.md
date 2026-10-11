---
title: Monitors
kind: howto
when: waiting on a build, a test run, CI or other sessions without polling
---

A monitor waits so you do not have to. You give aegis a bash condition and end
your turn; aegis runs the condition on a timer and wakes you when it passes,
when a failure condition passes, or when the time runs out. A monitor costs no
tokens while it waits, and the person watches its progress bar on your card.
Never sleep or poll in a loop instead.

## Wait on a process

Call `monitor_start` with:

- `description`: what is being waited on, shown on your card and in the wake;
- `done`: a bash command; exit code 0 means finished;
- `fail` (optional): a bash command; exit code 0 means it failed;
- `progress`: a bash command that prints a number from 0 to 100 for the bar,
  or null when nothing about the process can be counted;
- `interval_s=10` by default: how often the conditions run, from 1 to 3600
  seconds;
- `timeout_s=3600` by default, and at most 7 days;
- `cwd` (optional): where the conditions run, relative to your session's
  working directory.

Then end your turn. `monitor_start` returns at once, and its reply lists your
other live monitors so you can cancel the ones you no longer need.

Each run checks `done` first, then `fail`, then reads `progress`. A condition
gets 30 seconds per run; one that takes longer counts as not passing yet. A
command that cannot run, such as a missing binary, exits 127 on every run, and
the card shows that exit code and its stderr line.

## Always give progress

The bar is what the person reads to decide whether to wait or step in. Count
what is finished over the total: CI checks done, files written, test lines
passed. When nothing can be counted, estimate from elapsed time over the
expected duration and cap it at 99. Pass null only when neither works. The
card also shows an estimate of when the bar reaches 100.

## Wait on CI

Wait for a pull request's CI checks with the GitHub CLI:

```json
{
  "description": "CI on pull request 42",
  "done": "gh pr checks 42 --json bucket --jq 'length > 0 and all(.[]; .bucket == \"pass\" or .bucket == \"skipping\")' | grep -qx true",
  "fail": "gh pr checks 42 --json bucket --jq 'any(.[]; .bucket == \"fail\" or .bucket == \"cancel\")' | grep -qx true",
  "progress": "gh pr checks 42 --json bucket --jq '(map(select(.bucket != \"pending\")) | length) * 100 / ([length, 1] | max) | floor'",
  "interval_s": 30,
  "timeout_s": 3600
}
```

`done` passes only when every check passed or was skipped, and `fail` as soon
as one failed or was cancelled, so the wake says which. Each run checks `done`
before `fail`, so write the two so that they can never both pass.

## Wait on other sessions

`monitor_sessions` waits for sessions on this server to finish. Give it a
`description`, `sessions` (1 to 20 handles of open sessions, from
`session_list`), and optionally `interval_s=10` and `timeout_s=14400` (4
hours). You are woken:

- `ok` when every session has finished: its last turn ended with `turn_end`
  done or review, or it was closed;
- `blocked` as soon as one needs the person or hit an error, naming it, so you
  can say what holds the work up;
- `timeout` when the time runs out.

A session waiting on its own monitor or queue task counts as still running,
for as long as it waits. One that went idle after a turn that ended without
`turn_end`, waiting on nothing, counts as running for two minutes, the moment
a finished task takes to reach it; past that, the wait ends `blocked`. You
cannot wait on your own session. A watched session is followed
through a rename and a close. Sessions on a linked server cannot be watched.

## The wake

A monitor's end reaches you as a user turn whose first line is a header:

```text
> from monitor:<id> · ok · 2026-10-11T14:02:11Z
```

The outcome is `ok`, `fail` or `timeout` (and `blocked` for
`monitor_sessions`). The body says why it ended, how long it ran, and which of
your monitors are still live. A wake that arrives while you are working is
held and delivered when your turn ends.

## List and cancel

`monitor_list` shows your live monitors. `monitor_cancel` with
`monitor_id=<id>` stops one; no wake is sent. You can cancel only monitors
you started. Monitors are kept in the server's state directory and armed again
when the server restarts, and a wake for a stopped session starts it again.
