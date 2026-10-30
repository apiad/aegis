---
title: Usage
kind: howto
when: asking what the agents' work cost, on which repos and owners, for what kind of work, by whom and when
hints:
  - aegis usage --since 4w: four weeks' cost by model, purpose and repo; --split any. #the-dashboard
  - The usage page: the bar chart in the top bar, or Usage in the command palette. #the-usage-page
  - usage_read answers an agent's cost question in one call. #ask-from-an-agent-usage-read
---

aegis counts every turn as it ends. The journal's writer thread reads each
record a session stores and turns each finished turn into usage rows in
`journal.db`: the turn's cost, split over the tool calls it made, each with an
activity, a purpose and a repo. `aegis usage` reads those rows, so it answers
in well under a second and never rereads a transcript.

## The dashboard

```bash
aegis usage --since 4w
```

prints the window's cost at API prices, how much of it is estimated from
tokens, agent hours and hours waiting between turns, tool calls and errors
(non-zero shell exits apart), and the cost by model, harness, what woke each
turn, purpose, activity, owner and repo, the costliest sessions, the outcomes
the journal recorded (commits, pull requests opened and merged, issues closed)
with the cost per merged pull request, and a sparkline of the daily cost.

`--since` and `--until` take a date (`2026-10-01`) or a span back from now
(`6h`, `30d`, `4w`, `3m`). `--tz` buckets days and hours in another zone than
the system's.

## Split by any dimension

```bash
aegis usage --since 12w --by week --split owner --measure cost
aegis usage --split repo,purpose --json
aegis usage --split mode --where harness=claude-code --where person=ana
```

`--split` takes one or more of `person`, `owner`, `repo`, `harness`, `model`,
`mode`, `kind`, `purpose`, `activity`, `billing`, `context` and `session`.
`--by` buckets by `hour`, `day`, `week`, `month`, `dow` (weekday) or
`--by dow_hour`; `--by hour` alone shows the hour of the day. `--measure` is one of
`cost`, `tokens`, `secs`, `turns`, `sessions`, `calls`, `errors`, `exits`,
`waits`, `subscription` (or `sub`), `limit` or `whatif`. `--where
dimension=value` filters, and may repeat. `--json` prints the rows, the totals,
the outcomes, each subscription's split and, split by context, the context
facts.

## Subscriptions and context

```bash
aegis usage --since 4w --measure subscription --split owner
aegis usage --since 4w --split context --whatif 200k
```

A turn run on a subscription (Claude Code signed in, OpenCode Go, Codex's
plan) bills it; a turn on an API key bills `api`. `--split billing` names each
subscription, as `claude:<hash>` for Claude Code's account. Name it and give
it a fee in the config's `usage.subscriptions` ([config](config.md)), and two
measures appear:

- **subscription** (`sub`): the fee's share. A week of the fee is the monthly
  fee times 12 / 52, and each rise of the weekly limit between two quota
  readings goes to what ran on the subscription in those hours, by its cost at
  API prices. A rise with nothing behind it is *elsewhere* (the provider's own
  apps, a machine aegis does not run on); what a week left at its reset is
  *unused*. The slices, elsewhere and unused add up to the fee. Before the
  earliest reading, or with no quota provider, the fee is *apportioned* by cost.
- **limit**: the points of the weekly limit used.

`--split context` groups by the context a turn peaked at, in bands of 0, 50k,
100k, 200k, 300k, 400k and 600k tokens. `--whatif` (100k, 150k, 200k, 300k or
400k) replays every session as if it had handed off whenever its context
passed that size: later calls carry less context, priced at cache read; each
handoff pays one cache write of a successor's start and the measured cost of
rereading, the median reading cost of real successors' opening five turns. The
`whatif` measure is that net saving, and the last line sums it.

The older views still work: `--sessions` lists the costliest sessions,
`--tools` the cost and calls by activity, `--session <handle>` one session and
`--model <id>` one model.

## Where a quota gauge says it will land

A gauge's arrow is where the window should end at its reset. What is spent is
known, so only what remains of the window is estimated, at a rate between two:
the window's own so far, and what your last finished windows of that kind
usually spent (the last 20 for a 5-hour window, the last 4 for a week or a
month, from this server's own readings).

```text
landing = spent + remaining × (0.5 × usual + spent) / (0.5 + elapsed)
```

Spent and usual are percents of the quota; remaining and elapsed are fractions
of the window. A new window therefore reads what your windows usually spend, or
100% until one has finished, and the window's own rate counts for more as it
goes on. Halfway through, the two weigh the same. An early burst moves the
number without sending it to several hundred percent, and a quiet stretch brings
it back down.

A number near 100% at the start of a week means the week is going as your weeks
usually go. A window warns when it lands over 100% with half of it spent, and a
`pace` line holds new work while it lands over 100%.

## The usage page

Open it with the Usage button in the top bar (a small bar chart), with
**Usage** in the command palette (Ctrl+K), or by clicking the **Quota** label
above the quota gauges, on the Fleet or beside a session. One
row of controls changes every panel: the window (1, 4, 12 or 26 weeks, ending
this week, in your browser's time zone), what the trend is split by, and the
measure (spend at API prices, subscription dollars, tokens or agent hours).

- **Tiles**: the window's total and its change on the same span a window
  earlier, agent hours against hours waiting on the person, the share that ran
  without a person (agent, queue, workflow, scheduled run, monitor), the cost per
  merged pull request, and what subscriptions bought at API prices.
- **Trend**: the measure per week, stacked by the split.
- **Who the money goes to**: the measure by repo owner, split by server.
- **Repo by purpose**: each repo's measure as shares of its purposes.
- **When**: the measure by weekday and hour.
- **Driven by** and **Harness and model**: what woke each turn and what ran
  it; a model with no price shows its tokens.
- **Outcomes**: commits, pull requests and closed issues per repo, with the
  repo's measure and its cost per merged pull request.
- **Subscriptions**: per subscription, its fee for the window split over
  owners, elsewhere and unused, what it ran at API prices against the fee, and
  how much of it was apportioned without readings.
- **Context length**: the share of calls against the share of cost per
  context band with a call's mean cost, the peaks of sessions that handed off
  and of those that did not with `context.handoff_at` marked, and the replay's
  saving at every threshold, net of rewriting and rereading.

Any cell, bar or row lists the sessions behind it, costliest on top, and a
session opens its transcript. The page refreshes within 2 seconds of a turn
ending anywhere on the server. The footer says how much of the cost is
estimated from tokens, how many tokens have no price, and whether the
background count of older sessions is still running.

## Across linked servers

With linked servers (see Links), the page counts every server at once: each
panel adds up their rows, and a **servers** chip per server takes it out of
the panels or puts it back. A subscription that two servers run on counts its
fee once: its weekly limit is split over both servers' costs with this
server's quota readings. The footer says when each linked server last
answered ("beta answered 40 s ago"); the page asks them again every minute,
so their numbers are at most a minute old. A server that stops answering
keeps its last numbers, and one never reached, or running an aegis without
the usage page, is named as not reached. A session list behind a cell lists
this server's sessions. A member's page counts only this server.

## Ask from an agent: usage_read

An agent asks the same question with one call to `usage_read`. It takes the
CLI's parameters: `since` and `until` (`2026-10-01`, `today`, `yesterday`,
`7d`, `4w`), `by`, `split`, `measure` (rows are sorted by it), `where`, and
`tz`, and `whatif`. It returns the rows, their totals, the journal's outcomes,
the share of cost estimated from tokens, the count of tokens with no price,
each subscription's split (`subs`) and, split by context, the bands, peaks and
replay (`context`).

What did background work cost this week, per repo? Background work is every
turn no person woke: ask once per mode, or split by mode and repo at once.

```json
{"since": "7d", "split": ["mode", "repo"]}
```

and add the rows whose `mode` is `queue`, `workflow`, `schedule`, `monitor`
or `agent`. For one mode alone:

```json
{"since": "7d", "split": ["repo"], "where": {"mode": "queue"}}
```

A member's call counts only the sessions it owns; an admin's counts all.
A person's call counts every linked server and merges their rows, each
naming its `server`, with `servers` saying when each answered and whether it
is `live`, `cached` (its last answer) or `missing`. An agent's call, and one
with `server` naming this server, counts this server only; `server: "all"`
asks every linked server from an agent too.

## Import older work

aegis counts from the day it was upgraded. To count earlier work too, import
it once, on each server:

```bash
aegis usage import --claude-projects
aegis usage import --legacy ~/old-aegis/state
```

`--claude-projects` reads Claude Code's own session files in
`~/.claude/projects`, sessions run with or without aegis. Each file becomes a
session named `cc-<id>` whose turns have mode `outside`, with its repo from
its working directory; the mode filter on the page takes them out in one
click. A message aegis already counted is skipped, and so is a whole file
whose session aegis ran itself. Claude Code deletes these files after 30
days, so run the import soon after upgrading.

`--legacy` imports the logs of aegis before 2.0, as `aegis import-legacy`
does, then derives the journal and the usage tables again from every store.

Running either again adds nothing: each file imported is recorded with its
size and time, and one that grew since is read again in full. A rebuild of
the journal keeps imported sessions. The page shows them once it next
refreshes.

On a server several people use, the command reads only its own user's files.
An admin's agent calls `usage_import` instead (or `aegis call usage_import`
from its shell): it reads each person's files as their Linux user, gives the
sessions to their token, and lists any file it could not read. `people`
limits it to some tokens.

## What the numbers mean

- **Cost** is at API list prices: Claude's own running total where it reports
  one (on a subscription too), else the turn's tokens priced for its model.
  OpenCode's and Cursor's turns have no price per token: their tokens count as
  unpriced, never as zero dollars.
- **Activity** is what a tool call's hands did: reading, testing, editing,
  committing, bookkeeping, server, web, other agents, showing; a turn with no
  tool call is talking.
- **Purpose** is what the work served: build, fix, refactor, review, docs,
  writing, planning, research, ops, coordinate or chat. A code edit takes the
  type of the commit that lands it (`fix:` is fix, `feat:` build); reading,
  testing and bookkeeping take the purpose of the next call that has one.
- **Repo** is the repository a call touched, named by its main checkout, so a
  worktree counts as its repo. Its **owner** is the account or organisation in
  its `origin` URL, or `local`; the config page says how to name owners
  ([config](config.md)).
- **Mode** is what woke the turn: a person, an agent, a queue task's result, a
  workflow, a scheduled run, a monitor, a page's answer, or other.

After an upgrade the server derives the rows of every older session once, in
the background. Until that finishes, `aegis usage` says how far it has come
and its answer is partial.

`aegis usage repo <path>` and `aegis usage repos <dir>` answer another
question, the cost of one repository against every transcript store, sessions
run outside aegis included; see [commands](commands.md).
