---
title: Moving in with /setup
kind: howto
when: onboarding someone who used Claude Code, Codex, OpenCode or Cursor before; moving their skills, habits and cron jobs into aegis; running /setup again
hints:
  - Coming from Claude Code, Codex or Cursor? /setup moves your skills and jobs in.
  - Run /setup again any time; it proposes only what is new.
---

This page is for the agent that `/setup` starts. The person already has
habits in another harness: skills, instruction files, loops, jobs on a clock,
requests they make every week. You read those habits, propose on one page what
each becomes in aegis, and apply what they tick. They can run `/setup` again
at any time; a second run proposes only what is new. The turn `/setup`
delivers names each step's section below and ends with the server's facts and
the person's last run.

## Look

Change nothing in this step. Read what the sources section lists, as the
person's own user. Before you open any transcript, ask: say which harnesses
have transcripts, how many files and how much text (`ls`, `du -sh`), and
wait for a yes. Read the transcripts only to count patterns; quote at most a
line of the person's own words on the proposal page. With a record from
an earlier run, read transcripts newer than the time it read up to only.

## Sources

| source | where | what it tells you |
|---|---|---|
| harnesses | the turn's facts; `aegis doctor` | which agents and queues to propose |
| Claude Code transcripts | `~/.claude/projects/*/*.jsonl` | projects, sessions a week, the same request made again and again, long waits on CI, Claude Code's /loop |
| Codex sessions | `~/.codex/sessions/` | the same |
| OpenCode sessions | OpenCode's data directory, `~/.local/share/opencode/` | the same |
| skills and commands | `~/.claude/skills`, `~/.claude/commands`, plugins, each project's `.claude/` | procedures, and which ones sleep, poll or fan out to subagents |
| instruction files | `CLAUDE.md` and `AGENTS.md` in the home and the projects | rules aegis now carries out for them |
| clocks | `crontab -l`, `systemctl --user list-timers` | jobs that start a harness |
| aegis | `.aegis.yaml`, `workflow_list`, `schedule_list` | what exists already |

Skip a source that is missing and name it in the summary.

## Summarise

Say in a few lines what you found: the harnesses, sessions a week and in
which projects, the skills that matter, the loops and jobs. No proposals yet.

## Propose

Send one artifact (`artifact_create`, then `artifact_send`) with a row per
proposal, grouped by kind. Each row says what you found, what it becomes and
the evidence, with a checkbox, and the page ends with one submit that returns
the ticked keys. Leave out what the record says was applied, and a declined
key unless its evidence has at least doubled. End your turn needs_you.

| found | becomes |
|---|---|
| a harness installed and not configured | an agent preset, and a queue for cheap work |
| the same request on a rhythm | a schedule |
| a Claude Code /loop, or a cron line that runs a harness | a schedule, or aegis's loop workflow |
| a skill with ordered steps and checks | a saved workflow |
| a skill or rule that sleeps or polls | a line telling agents to use `monitor_start` |
| a skill that spawns subagents | a queue |
| many projects open each day | agent presets per project and the Fleet |

A key names each proposal as kind and name: `queue:tests`,
`schedule:weekly-prs`, `workflow:release`, `agent:codex`, `edit:<path>`.
Skills keep working as they are, because each harness loads its own skills
inside aegis; propose a change to one only where aegis gives it something it
lacked.

## Apply

Apply the ticked rows, each through its own door:

- `.aegis.yaml`: you cannot write it. Read it, write the whole new text with
  every ticked agent and queue, and call `config_suggest` once. The person
  applies the card; you are woken with applied, declined or stale. On stale,
  read the file again and suggest anew.
- schedules: `schedule_create`. Each is born paused with its card, and the
  person starts it from there.
- workflows: `workflow_save`, in the project the skill came from.
- files the person owns, such as a skill, `AGENTS.md` or `CLAUDE.md`:
  ordinary edits, listed at the end with their paths.

## Record

Call `setup_record` once, at the end: `applied` and `declined` as keys,
each declined key with the evidence count you showed, and
`sources_until=<ISO time>`, the newest transcript time you read. The record is the person's own, so a
member's run never sees another's.

## Check

Run `config_doctor` and say what it found. List every applied row with where
it now lives, and what still waits for the person: a config card not yet
applied, a paused card not yet started.
