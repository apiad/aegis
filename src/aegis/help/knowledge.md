---
title: Memories, skills and the knowledge hint
kind: concept
when: remembering a fact for later sessions, keeping a procedure as a skill, finding what aegis remembers, or reading the knowledge block under a message
hints:
  - Type /remember and a fact; every later session can find it. #remember-something
  - The knowledge block under your message names what may help the agent. #the-hint
---

aegis keeps your memories and skills on the server: one Markdown file per
fact, and one folder per procedure. Every session you start, in any
harness, can find them. Nothing is loaded up front: each message carries a
short block naming the few memories and skills that may matter, and the
agent reads the ones it needs. Each person on a server has their own, and
nobody else's agents see them.

## What a memory is

A memory is one fact a later session should know: who you are, how you
want agents to work, where something runs, a constraint on a project. It
has a name (lowercase letters, digits, - and _), a one-line description,
a body in Markdown, and a type: user, feedback, project or reference. A
memory is permanent, or temporary with the day it stops being true. Its
optional paths list directories where it matters most, and in a session
working there it ranks higher.

Names are unique across your memories and skills: a memory cannot take the
name of a skill. A link to another item is written `[[name]]`.

Every change goes through aegis and is one commit in a git history kept
with your memories on the server, so a deleted memory is kept there. The
Knowledge view, in a later release, will restore one.

## The hint

When a message reaches a session, aegis searches your memories and skills
with its text and adds a short block under it, of up to five lines, folded
into its own row in the transcript:

```text
> from aegis:notice · knowledge · may be relevant
skills: release: Use when cutting a release · ...

memories: deploy-host: Where the bot runs; check before any ssh · ...

read with skill_read / memory_read · search with skill_search / memory_search
```

It names the three best skills and the three best memories, with no
threshold, and the tools that read and search them: the agent decides
whether any matters. A message
that matches nothing, a slash command, or one of aegis's own wakes (a
notice, a monitor that ended, a page's answer) gets no block. An expired memory is never
named, but a search still finds it.

The search is BM25 over the name, the description and the start of the
body. It folds accents and cuts words to their first five letters, so
"configuración" finds "configuration", but different words in two
languages, such as "despliegue" and "deploy", do not meet.

## Skills

A skill is a procedure for one kind of task, in the SKILL.md format other
harnesses use: a folder named after the skill, holding `SKILL.md` with a
`name` and a `description` that says when to use it, and any files the
procedure needs. An optional `aegis:` block in its frontmatter is read by
aegis only, so a skill copied out still works elsewhere:

```yaml
aegis:
  command: true        # offer it as /release in the composer
  scripts: [bump.py]   # files skill_run may run
  mcp:                 # an MCP server whose tools skill_tool calls
    command: [python3, server.py]
```

An agent finds a skill mid-task with `skill_search`, in its own words,
and loads it with `skill_read`. A loaded skill shows on the session's card
in the Fleet, as a skills line under the plan, and in its sidebar under
Skills loaded, in the order the agent read them, with how many times it
ran each one's scripts, so you can tell which session follows which
procedure.

### Scripts a skill brings

A skill that lists files under `aegis.scripts` lets an agent run them with
`skill_run`, once the skill is loaded in its session; any other file of the
folder never runs. Each run copies the skill's folder into a fresh
temporary directory, runs `python3` on the script there with the
arguments the agent gives, and removes the directory after. On a server
with several people, it runs as your Linux user, so a script reaches what
you can reach and nothing more. A run stops after two minutes unless the
agent asks for longer, up to ten. A script runs on the server without
asking, so only a session with full permission may run one.

### An MCP server a skill brings

A skill whose `aegis:` block names `mcp.command` brings an MCP server that
speaks over standard input and output. When a session loads the skill,
aegis starts the server for that session alone, in a fresh copy of the
skill's folder and, on a server with several people, as your Linux user.
`skill_read` returns the server's tools with their arguments, and the
agent calls one with `skill_tool`, naming the skill, the tool and the
arguments. No other session reaches the server, and it stops when the
session closes. Only a session with full permission reaches the server:
any other loads the skill's text but starts no server, and `skill_read`
says why.

The tools stay behind `skill_tool` instead of joining the agent's own tool
list, because not every harness picks up tools added mid-session: Codex
does not yet by default. One tool works the same in every harness.

### Skills as commands

Each of your skills is a command in the composer. Type `/<name>` and
any arguments, such as /release v2.1 for a skill named release, and aegis
sends the session one turn holding the skill's text and your arguments,
marks the skill loaded and counts the run. aegis sends it
itself, so a skill works the same in Claude Code, Codex and OpenCode
sessions. The menu lists your skills after aegis's own commands, marked
aegis skill. A skill named like an aegis command is offered as
`/skill:<name>`, and a harness command with a skill's name is hidden
behind it. Set `command: false` in a skill's `aegis:` block to keep it
out of the menu.

## Tools for agents

- `memory_search` ranks your memories against a query in the agent's own
  words, and marks expired ones.
- `memory_read` returns one or more memories in full, by name.
- `memory_save` creates or replaces a memory. An agent passes `asked=true`
  when you asked it to remember something.
- `memory_delete` deletes one; the history keeps it.
- `skill_search` ranks your skills against what the agent is about to do.
- `skill_read` loads a skill: it returns SKILL.md and the folder's file
  list, and with `file` returns one of those files.
- `skill_save` creates or replaces a skill, with its other files in
  base64 and its `aegis` block.
- `skill_run` runs one of the scripts a loaded skill declares under
  `aegis.scripts`, with arguments, and returns its exit code, stdout and
  stderr, each cut at 20,000 characters.
- `skill_tool` calls a tool of the MCP server a loaded skill brings, with
  its arguments, and cuts the answer at 20,000 characters; `skill_read`
  lists those tools.
- `knowledge_stats` lists each item with how often it was hinted, read,
  invoked or run, and when it was last used; `unused_days=60` keeps the
  ones nobody used in that time.

A session with read permission can search, read and see stats, but not
save or delete. `skill_run` and `skill_tool` need full permission.

## Remember something

Type `/remember` and the fact, such as `/remember the staging host for
quill is ben-staging-2`. The agent saves it with `memory_save`, choosing
a name, a description and a type, and makes it temporary when the fact
names a date that will pass. It tells you what it saved.

You can also ask in your own words ("remember that we never deploy on
Fridays"); the agent saves it the same way.
