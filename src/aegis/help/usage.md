---
title: Usage
kind: howto
when: asking what the agents' work cost, on which repos and owners, for what kind of work, by whom and when
hints:
  - aegis usage --since 4w: four weeks' cost by model, purpose, repo and owner. #the-dashboard
  - Split any measure by any dimension, by period. #split-by-any-dimension
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
`cost`, `tokens`, `secs`, `turns`, `sessions`, `calls`, `errors`, `exits` or
`waits`. `--where dimension=value` filters, and may repeat. `--json` prints the
rows, the totals and the outcomes.

The older views still work: `--sessions` lists the costliest sessions,
`--tools` the cost and calls by activity, `--session <handle>` one session and
`--model <id>` one model.

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
  workflow, a schedule, a monitor, a page's answer, or other.

After an upgrade the server derives the rows of every older session once, in
the background. Until that finishes, `aegis usage` says how far it has come
and its answer is partial.

`aegis usage repo <path>` and `aegis usage repos <dir>` answer another
question, the cost of one repository against every transcript store, sessions
run outside aegis included; see [commands](commands.md).
