---
title: Moving in with /setup
kind: howto
when: onboarding someone who used Claude Code, Codex, OpenCode or Cursor before; moving their skills, memories, habits and cron jobs into aegis; running /setup again
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
| skills and commands | `~/.claude/skills`, `~/.claude/commands`, plugins, each project's `.claude/`, Codex's and OpenCode's skill folders | procedures, and which ones sleep, poll or fan out to subagents; what to move into aegis (knowledge) |
| auto-memory | `~/.claude/projects/*/memory/` | facts Claude Code saved, to move into aegis (knowledge) |
| procedure docs | Markdown files whose frontmatter has a `when:` line, outside a repository's `know-how/` folder, which aegis already reads where it is (knowledge) | procedures to move into aegis as skills |
| instruction files | `CLAUDE.md` and `AGENTS.md` in the home and the projects, and notebooks about working with agents | rules aegis now carries out for them; settled patterns that become memories |
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
Skills, memories and procedure docs have rows of their own, in the same
artifact (knowledge).

## Knowledge

aegis keeps skills and memories itself, one store per person, and finds
the ones that matter for each message (help_read knowledge). Moving in is a
migration, not a copy: an item aegis takes leaves the harness, so there is
one source per item. The person accepts or rejects each row; what they do
not tick keeps working in the harness as before.

Look in these places, as the person's own user:

- skill folders (a folder with a `SKILL.md`): `~/.claude/skills/`, each
  project's `.claude/skills/`, the plugins' skills under
  `~/.claude/plugins/`, `~/.codex/skills/`, `~/.agents/skills/`,
  `~/.config/opencode/skills/` and a project's `.opencode/skills/`;
- Claude Code's auto-memory: `~/.claude/projects/*/memory/`, one file per
  fact (its `MEMORY.md` is an index; leave it out);
- procedure docs: Markdown files with a `when:` line in their frontmatter,
  leaving out any in a git repository's root `know-how/` folder: aegis tells a
  session about those where they are (knowledge), and importing one would lose
  its branch and its reviewers;
- instruction files (`CLAUDE.md`, `AGENTS.md`) and notebooks the person
  keeps about how they work with agents.

Propose a row per item, with its path as evidence. Each found thing becomes:

| found | becomes | key |
|---|---|---|
| a harness skill or plugin skill | an aegis skill, copied with its folder | `knowledge:skill:<name>` |
| a procedure doc | an aegis skill, its `when:` line as the description | `knowledge:skill:<name>` |
| an auto-memory file | an aegis memory, approved, permanent unless it names a date | `knowledge:memory:<name>` |
| a notebook or a rule section about working together | memories, one per settled pattern, which you draft | `knowledge:memory:<name>` |
| a rule telling agents to do by hand what aegis does (keep a journal file, poll, keep a todo list) | the rule removed, naming the aegis feature that replaces it | `remove:<path>` |
| a rule telling agents to print a repository's know-how menu by hand, when the person's sessions run on Claude Code | the rule removed, naming aegis's know-how notice; on other harnesses the notice waits for the next message, so keep the rule | `remove:<path>` |
| an original that has been imported | removed, or a one-line pointer saying it now lives in aegis | `remove:<path>` |

Every original to remove is a row of its own, ticked or not, so nothing
outside aegis is deleted unless its row is ticked. Say on the row:

- for a skill in a project's `.claude/skills/`: it stops being shared with
  the other people and agents who use that repository;
- for a plugin's skill: a plugin's folder is rewritten when it updates, so
  propose turning the plugin off (its `enabledPlugins` entry in
  `~/.claude/settings.json`) only when every skill it brings moved;
- for a file git tracks (`git -C <repo> ls-files --error-unmatch <path>`),
  such as a repository's `CLAUDE.md`, `AGENTS.md` or `.claude/skills/`: the
  change is a commit in that repository, made the way its own `AGENTS.md`
  says (a branch and a pull request, or straight to its main branch);
- for a skill folder that is a symlink, such as `~/.claude/skills/<name>`
  linking to a folder in a repository: `knowledge_import` refuses the link,
  so the import row names the folder it links to, and the removal row is
  `remove:<link>`, which deletes the link only (`rm` on the link, no
  trailing slash), never its target: the target may be a repository's,
  shared with others.

When auto-memory files move, Claude Code's `MEMORY.md` in that folder still
lists them, and it loads that index into every session. Propose one row
for it, `edit:<dir>/MEMORY.md`: it removes the lines of the files that
moved, or removes the index itself when every file in the folder moved.
Apply it after the files' own removal rows, and only for files that were
imported.

Leave out every source the turn says was already moved into aegis.

One more row, `knowledge:native`, offers the switch that stops the harness
loading its own skills and memory in this person's sessions: Claude Code's
`Skill` tool and its auto-memory, Codex's and OpenCode's skill catalogs and
Codex's memories. It is optional and separate from the moves, and the
person can turn it off again from Settings. Until it is on, what aegis
holds and what the harness still holds both run.

Apply in this order, after the rest of the page's rows:

1. Call `knowledge_import` with every ticked `knowledge:skill:` and
   `knowledge:memory:` row, at most 50 a call (split a longer list): `source` (the absolute path), `kind` (`skill`
   for a folder, `memory` for one fact's file, `doc` for a procedure doc),
   and `name` when aegis should know it by another. A memory is named by
   its frontmatter `name:` when that is a valid name (Claude Code links
   `[[name]]` by it, so the links keep working), else by its file's stem;
   a skill by its folder, a doc by its file's stem. Use that name in the
   row's key. A memory you drafted
   from a notebook is not a file: save it with `memory_save` and `asked:
   true`. The ticks are the person's approval, so in the turn their
   submit began each item is saved approved and one card lists them with
   an Undo each. Call it in that turn: in any other (a queue task, a
   handoff, a monitor's wake) each item is only proposed, with its own
   card for the person to approve. Read the answer: a refused row (a name
   aegis already holds, a link that leaves the folder, a folder over
   20 MiB, a memory or doc over 1 MiB) is not imported, so do not remove
   its original. An answer with an `error` names a step that failed
   after the import; the items it lists as imported are in aegis.
2. Remove each ticked `remove:<path>` original that was imported, with
   ordinary file operations as the person's user, or as a commit in its
   repository for a file git tracks.
3. For a ticked `knowledge:native`, call `setup_record` with
   `knowledge="native"` (below), in that same turn: in a turn the person
   did not begin it is refused (not_person_turn), and the person sets
   it in Settings instead.

`knowledge_import` writes what it imported into the setup record itself.

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
  ordinary edits, listed at the end with their paths; a file git tracks
  is changed as a commit in its repository, following its rules.
- skills, memories and procedure docs: `knowledge_import`, then each
  removal row (knowledge).

## Record

Call `setup_record` once, at the end: `applied` and `declined` as keys,
each declined key with the evidence count you showed, and
`sources_until=<ISO time>`, the newest transcript time you read. Add
`knowledge="native"` when the person ticked `knowledge:native`. The record is the person's own, so a
member's run never sees another's.

## Check

Run `config_doctor` and say what it found. List every applied row with where
it now lives, and what still waits for the person: a config card not yet
applied, a paused card not yet started.
