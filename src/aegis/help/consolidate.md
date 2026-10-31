---
title: Reviewing your skills and memories with /consolidate
kind: howto
when: reviewing what aegis keeps for someone, merging duplicate memories or skills, renewing or dropping expired ones, fixing stale ones, retiring skills nobody uses, or running that review every month
hints:
  - Type /consolidate and the agent proposes what to merge, renew or retire; you tick.
  - A schedule can run /consolidate's review every month. #run-it-every-month
---

This page is for the agent that `/consolidate` starts. The person keeps
skills and memories in aegis (help_read knowledge); over months some
repeat each other, some expire, some quote files that moved, and some are
offered under every message and never read. You gather that evidence,
propose on one page what to do with each item, and apply only the rows
they tick. Nothing changes before their submit.

## Gather

Change nothing in this step. Call `knowledge_stats` once with
`duplicates=true`. From its answer, take:

- pending proposals: items with `status` proposed, waiting for the
  person's Approve on their card;
- expired memories: the flag `expired`;
- stale memories: a flag `stale:<path>`, a path the memory quotes that is
  gone;
- unused items: never used, or last used more than 60 days ago, and
  items marked never recalled, hinted 10 times or more and never read or
  invoked (`flag=never_recalled` lists only those);
- likely duplicates: the `duplicates` pairs, each with its score.

Also list the docs in the `know-how/` folders of the repositories you worked
in that have no `when:` line (`grep -L '^when:' know-how/*.md`): aegis falls
back to their heading, which says little about when to read them. Name
them in your summary, as work for those repositories; there is no row for them.

Read the items a row will be about (`memory_read`, `skill_read`) before
you propose it: the score only says two items look alike. While you read,
note every `[[name]]` link to a name `knowledge_stats` does not list: a
broken link.

## Propose

Send one artifact (`artifact_create`, then `artifact_send`) with a row per
proposal, grouped by key. Each row names the items, says what you found and
what you would do, shows the evidence (the score, the flag, the hint and
read counts, the last use) and has a checkbox, unticked. The page ends with
one submit that returns the ticked keys. End your turn needs_you.

| key | found | the row proposes |
|---|---|---|
| `merge:<a>+<b>` | a duplicate pair | one item with what both say, under `a`, and `b` deleted |
| `renew:<name>` | an expired memory that is still true | a new `expires`, or permanent |
| `drop:<name>` | an expired or stale memory that is no longer true | deleting it |
| `fix:<name>` | a stale memory whose fact still holds | its paths corrected |
| `retire:<name>` | an unused or never-recalled skill or memory | deleting it |
| `link:<name>` | an item with a broken `[[link]]` | the link pointed at the item that replaced it, or removed |

Pending proposals get no key: list them at the top of the page with their
names, since only the person approves or discards a proposal, on its card
or in the Knowledge view. Leave out an item that needs nothing. With no row
to propose, say so in one line and send no page.

## Apply

The person's submit begins a turn of theirs, so what you save in it is
saved approved, with `asked=true`. Apply only the ticked rows, each through
its tool, one commit each:

- `merge:<a>+<b>`: `memory_save` or `skill_save` of `a` with the merged
  text (a skill keeps its files: pass them all), then `memory_delete` or
  `skill_delete` of `b`;
- `renew:<name>`: `memory_save` with the new `expires`, or
  `lifetime=permanent`;
- `drop:<name>` and `retire:<name>`: `memory_delete` or `skill_delete`;
- `fix:<name>`: `memory_save` with the paths corrected;
- `link:<name>`: `memory_save` or `skill_save` with the link mended.

Each change to an approved item leaves a card in the transcript with Undo,
which reverts that one commit; say so when you report. Report what changed,
row by row, and what you left as it was. A tool's refusal is part of the
report: apply the other rows and say which one failed and why.

## Run it every month

A schedule can start the review on a clock. The person still ticks the
rows: the run ends at its proposal page and waits for their submit, which
applies them as above. Create it with `schedule_create`:

- `name`: `knowledge-review`;
- `clock`: `{"cron": "0 9 1 * *"}`, nine in the morning on the first of
  each month;
- `fire`: `{"target": "new", "close": "done"}`;
- `prompt`: `Review my skills and memories: follow help_read consolidate.`

A schedule an agent creates is born paused, and the person starts it from
its card (help_read schedules).
