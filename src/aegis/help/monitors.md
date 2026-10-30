---
title: Monitors
kind: howto
when: waiting on a build, a test run, CI or other sessions without polling
hints:
  - Agents wait on builds and CI with a monitor that wakes them, never by polling. #wait-on-a-process
  - A monitor can wait until other sessions finish. #wait-on-other-sessions
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
- `source`: text from your tool call that started what you wait on, or null
  when no call of yours started it (see
  [Link the call you wait on](#link-the-call-you-wait-on));
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

## Link the call you wait on

The person reading a monitor usually wants the command behind it: what ran and
what it printed. Name that call in `source`, with any text you wrote in it,
such as the comment line that opens a Bash command. You never see a row's id,
so aegis finds the row: the latest tool call in your session holding that text,
leaving out `monitor_start` rows.

The reply's `source` says which row it found. When no call holds the text, the
monitor still arms, `source` is null and `note` says so. For a pull
request's CI, name the push or the `gh pr create` that started it.

In the sidebar, a click on the monitor goes to that call in the transcript and
opens it. Hovering still shows the monitor's card, with the same link under
Waits on, and on a phone a tap opens the card and the link is there. A monitor
without a linked call opens its card on a click.

## Wait on CI

Wait until a pull request's CI checks finish, GitHub Actions or any other, with
the GitHub CLI:

```json
{
  "description": "CI on pull request 42",
  "source": "gh pr create",
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

## Wait on CI again after a re-run or a push

For a few seconds after you push or re-run a job, `gh pr checks` still
reports the last attempt. A monitor armed then reads that old result: `fail`
passes on the failure you just re-ran, or `done` on the green run of the
commit before. Make each condition see only the new attempt.

After a push, require the pull request's head to be the commit you pushed.
Until GitHub moves the head, the conditions fail and the monitor keeps
waiting:

```json
{
  "description": "CI on pull request 42 after the push",
  "source": "git push",
  "done": "test \"$(gh pr view 42 --json headRefOid --jq .headRefOid)\" = <sha> && gh pr checks 42 --json bucket --jq 'length > 0 and all(.[]; .bucket == \"pass\" or .bucket == \"skipping\")' | grep -qx true",
  "fail": "test \"$(gh pr view 42 --json headRefOid --jq .headRefOid)\" = <sha> && gh pr checks 42 --json bucket --jq 'any(.[]; .bucket == \"fail\" or .bucket == \"cancel\")' | grep -qx true",
  "progress": "gh pr checks 42 --json bucket --jq '(map(select(.bucket != \"pending\")) | length) * 100 / ([length, 1] | max) | floor'"
}
```

`<sha>` is `git rev-parse HEAD` after the push. A re-run keeps the commit, so
note the time instead (`date -u +%FT%TZ`) and count only the checks you re-ran
that started after it:

```json
{
  "description": "the re-run of slow (2/4) on pull request 42",
  "source": "gh run rerun",
  "done": "gh pr checks 42 --json name,bucket,startedAt --jq 'map(select(.name == \"slow (2/4)\" and .startedAt > \"<time>\")) | length > 0 and all(.[]; .bucket == \"pass\")' | grep -qx true",
  "fail": "gh pr checks 42 --json name,bucket,startedAt --jq 'any(.[]; .name == \"slow (2/4)\" and .startedAt > \"<time>\" and (.bucket == \"fail\" or .bucket == \"cancel\"))' | grep -qx true",
  "progress": "gh pr checks 42 --json name,bucket,startedAt --jq 'if any(.[]; .name == \"slow (2/4)\" and .startedAt > \"<time>\" and .bucket != \"pending\") then 100 else 10 end'"
}
```

Watch the pull request, never a job id: a re-run is a new job with a new id,
so a monitor on `gh api .../actions/jobs/<id>` waits on the old one.

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
through a rename and a close. Sessions on a linked server cannot be watched,
unless the link is [trusted](links#trusted-links): then `handle@server` is
read from that server's session list at each interval, and a link that drops
keeps the last reading rather than ending the wait.

## The wake

A monitor's end reaches you as a user turn whose first line is a header:

```text
> from monitor:<id> · ok · 2026-10-11T14:02:11Z
```

The outcome is `ok`, `fail` or `timeout` (and `blocked` for
`monitor_sessions`). The body says why it ended, how long it ran, and which of
your monitors are still live. A wake that arrives while you are working is
held and delivered when your turn ends.

## A monitor that stalls

A monitor that never finishes would otherwise wait out its timeout. aegis
warns you, while it keeps running, when:

- its progress has not moved for 15 minutes (and at least three intervals):
  stalled;
- one of its checks cannot run, for three intervals: broken, for example a
  command not found;
- it is past its ETA by half the time it estimated, and by 10 minutes at
  least: overdue.

```text
> from aegis:notice · monitors:mon-5e1f:stalled · warn · 2026-10-20T07:41:12
monitor mon-5e1f "tests" at 40% for 16 min · look, or monitor_cancel it
```

If you are waiting, the warning wakes you; if you are working, it rides on
your next aegis tool result. Each warning comes once, and once more only if
the monitor moved and stalled again. Look at what it watches, cancel it with
`monitor_cancel`, or end your turn again to keep waiting. The `notices:` block
in `.aegis.yaml` changes the thresholds or turns the wake off (see the config
page).

## List and cancel

`monitor_list` shows your live monitors. `monitor_cancel` with
`monitor_id=<id>` stops one; no wake is sent. You can cancel only monitors
you started. Monitors are kept in the server's state directory and armed again
when the server restarts, and a wake for an asleep session starts it again.
