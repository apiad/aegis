---
title: Memories, skills and the knowledge hint
kind: concept
when: remembering a fact for later sessions, keeping a procedure as a skill, finding what aegis remembers, or reading the knowledge block under a message
hints:
  - Type /remember and a fact; every later session can find it. #remember-something
  - The knowledge block under your message names what may help the agent. #the-hint
  - The Knowledge view lists your memories and skills to search, edit and restore. #the-knowledge-view
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
with your memories on the server, so a deleted memory is kept there, and
the Knowledge view, below, restores it.

## Approving what an agent saves

A memory or skill an agent saves on its own initiative is proposed: it is
kept, and a card in the session's transcript shows it with Approve and
Discard. Approve makes it a memory like any other. Discard deletes it.
Until you approve it, a proposal is never named under a message, and a
proposed skill is not a command in the composer, though an agent that
knows its name can still load it with `skill_read`. A proposed skill's
card lists every file of its folder with its size, and a text longer than
4,000 characters is shown in part, marked so. If the proposal changes
after its card was drawn, a script included, Approve and Discard do
nothing and the card says it changed. A proposal you do not
answer stays proposed. When you asked for it in the message that began
the agent's turn, with /remember or in your own words, the agent saves it
approved, and its card says so and offers Undo. A request that reaches the
agent any other way (from another agent, a queue task or one of aegis's
wakes) does not count: what it saves then is proposed.

When an agent changes or deletes a memory or skill you already approved,
the change applies at once, whether or not you asked for it, and its card names the change with an Undo
button, which reverts that one commit in the history. Undo cannot revert
a change that a later change to the same lines built on; the card then
says so and nothing moves. An Undo makes no card of its own, so it cannot
itself be undone from the transcript; the history on the server keeps it.

The cards are kept in the transcript, so their buttons work after aegis
restarts. Only the person who owns the session can answer them, even on
a server where an admin can read every transcript; they act on that
person's store, and the agent is told the outcome.

## The Knowledge view

The Knowledge button in the top bar, or Alt+I, opens the Knowledge view:
your own memories and skills, never anyone else's, an admin's
included. A search box and the chips all, skills, memories and flagged
narrow the list; flagged lists proposals and expired or stale memories.
Proposals come first. Each row shows the name, the description, when an
agent last read it and how often, and its flag: proposed, expired, stale
(hover it for the missing path) or ok.

Open a row to read and edit the item's whole file, its frontmatter
included, in the editor. Save, or Ctrl+S, commits the edit
and approves the item, so editing a proposal approves the text you
saved; Approve on its card then does nothing and says it changed. If
the file changed since you opened it, by an agent or by hand, nothing is
saved and Reload is offered. The card of a proposal has an Edit
button that opens it here.

Below the editor, History lists every commit that changed the item,
newest first, with who made it. Restore puts the item back as it was at
that commit, as a new commit, so a restore can be undone the same way.
Delete removes the item after asking; its history is still shown, and
Restore on an earlier version brings it back. On a phone the rows drop
the usage column, and an open item takes the whole width, with a back
button to the list.

## Expired and stale memories

A temporary memory past its day is flagged expired and leaves the block
under messages that day; a search still finds it.

When a memory is saved, and once a day, aegis looks for each path the
memory quotes in backticks. A token counts as a path when it starts with
`/`, `~/`, `./` or `../`, or holds a `/` and ends in a file extension, such
as `repos/bot/run.py`; a bare file name, `origin/main` or `text/html` is
never looked for. A relative path is looked for under each of the memory's
paths, then in the working directory of the session that saved it (kept
as `cwd` in the memory), then under your Files root and your home. One
that is found nowhere flags the memory stale, naming the path. A stale memory
stays in the block, since a moved file does not make the fact false, but
it is worth a look. On a server with several people the files are looked
for as your Linux user, so a check sees only what you can see. The check
after a save runs in the background, so a slow disk never holds the save;
a check that takes longer than 30 seconds is dropped until the next one,
and only the first 200 paths of a memory are looked for. The flags
are computed again when aegis starts.

Search results and `knowledge_stats` carry the flags: `expired` or
`stale:<path>`.

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
notice, a monitor that ended, a page's answer, a card's outcome) gets no
block. An expired memory or a proposal is never named, but a search still
finds it, marked.

The search is BM25 over the name, the description and the start of the
body. It folds accents and cuts words to their first five letters, so
"configuración" finds "configuration", but different words in two
languages, such as "despliegue" and "deploy", do not meet.

An admin can add an embedding model for that (`knowledge.embeddings`,
help_read config search embeddings). Search then fuses the BM25 rank
with the model's rank, never the model alone. `memory_search` and
`skill_search` wait for the model; the block under a message never does,
so it uses the model only for text the model already read, and BM25
alone otherwise, while the model loads and on a message it has not seen.
The Knowledge view's search stays BM25, since it filters the list.

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
aegis skill. A skill named like an aegis command or like one of the
harness's own commands is offered as `/skill:<name>`, and the other
command keeps its name. Set `command: false` in a skill's `aegis:` block to keep it
out of the menu.

## Know-how in a repository

A repository can keep its own procedures as Markdown files in a `know-how/`
folder at its root. They stay there: they go through review with the code,
reach everyone who clones it, and differ by branch, so aegis keeps no copy and
they are not in the Knowledge view. To be told when to read a doc, an agent
needs a line from it, tried in this order:

```yaml
---
when: bumping the version and cutting a release
---
```

a line starting `**When to reach for this:**`, or the doc's opening heading.

A session gets those lines once, up to 50 docs, as a notice, when it starts
in the repository or when a tool call names a path in it:

```text
> from aegis:notice · know-how · quill
This repository's procedure docs. Read those that match your task before you act:

- know-how/releasing.md: bumping the version and cutting a release
```

It reaches the agent as a notice does (help_read sessions notices): on
Claude Code right after that tool call, elsewhere on the next message or
aegis tool result. A worktree counts as its main checkout, and after a
compaction a session is told again. To turn it off, set `knowledge.know_how:
false` (help_read config search embeddings).

## Tools for agents

- `memory_search` ranks your memories against a query in the agent's own
  words, and marks expired ones.
- `memory_read` returns one or more memories in full, by name.
- `memory_save` creates or replaces a memory. An agent passes `asked=true`
  when your message that began its turn asked it to remember something;
  otherwise a new memory is proposed and waits for your Approve.
- `memory_delete` deletes one; the history keeps it, and the card of an
  approved one offers Undo.
- `skill_search` ranks your skills against what the agent is about to do.
- `skill_read` loads a skill: it returns SKILL.md and the folder's file
  list, and with `file` returns one of those files, up to 1 MiB.
- `skill_save` creates or replaces a skill, with its other files in
  base64, 20 MiB at most in all, and its `aegis` block, proposed unless
  `asked=true`, as a memory is.
- `skill_delete` deletes a skill, its whole folder; the history keeps
  it, and the card of an approved one offers Undo.
- `skill_run` runs one of the scripts a loaded skill declares under
  `aegis.scripts`, with arguments, and returns its exit code, stdout and
  stderr, each cut at 20,000 characters.
- `skill_tool` calls a tool of the MCP server a loaded skill brings, with
  its arguments, and cuts the answer at 20,000 characters; `skill_read`
  lists those tools.
- `knowledge_stats` lists each item with how often it was hinted, read,
  invoked or run, when it was last used and its flags; `unused_days=60`
  keeps the ones nobody used in that time, `flag=stale` or
  `flag=expired` the ones with that flag. An item hinted 10 times or more
  and never read or invoked is marked never recalled, and
  `flag=never_recalled` lists only those. `duplicates=true` adds the
  pairs of items of one kind that are likely duplicates: each one is the
  other's best match by name and description, both ways, with a high
  score. `/consolidate` reads all of this and proposes what to merge,
  renew, drop, fix or retire (help_read consolidate).

- `knowledge_import` moves skill folders, memory files and procedure
  docs the person ticked on the `/setup` page into aegis (help_read setup
  knowledge).

A session with read permission can search, read and see stats, but not
save or delete. `skill_run` and `skill_tool` need full permission.

## Remember something

Type `/remember` and the fact, such as `/remember the staging host for
quill is ben-staging-2`. The agent saves it with `memory_save`, choosing
a name, a description and a type, and makes it temporary when the fact
names a date that will pass. It tells you what it saved.

You can also ask in your own words ("remember that we never deploy on
Fridays"); the agent saves it the same way.

## Moving in what a harness kept

`/setup` finds the skills, auto-memory files and procedure docs another
harness kept, and proposes moving each into aegis on its page. The agent
moves the rows you tick with `knowledge_import`: a skill's folder is copied
whole, scripts and binaries included, each item is saved approved as one
commit when your submit of the page began the agent's turn (otherwise each
waits for your Approve on its own card), and a name aegis already holds is refused, never overwritten. One
card in the transcript lists what came in, with an Undo per item, which
takes that item out of aegis again; the next `/setup` then proposes it
again. Undo is refused for a skill that gained files since it came in,
since undoing the import would leave them behind. Removing an original from where the
harness loaded it is a row of its own, done after the import, and nothing
outside aegis is deleted unless you ticked its row.

## Turning the harness's own off

Moving items in does not stop a harness from loading what is left. That is
a separate switch, off by default: Settings, under Skills and memory, "Only
aegis's skills and memory in my sessions". It is yours alone; in multi-user
mode another member's sessions are not touched. `/setup` offers it as the
`knowledge:native` row. With it on, your sessions start:

- in Claude Code, without its `Skill` tool (aegis's `skill_read` and
  `skill_search` replace it) and with its auto-memory off, through the
  `CLAUDE_CODE_DISABLE_AUTO_MEMORY` variable;
- in Codex, without its skills catalog, its bundled skills and its
  memories;
- in OpenCode, with every skill denied, so none is listed or loaded.

A skill in your store then also takes the name of a harness command, so
`/<name>` runs yours. A session keeps the setting it was started with, a
restart of its process included, so its menu and its tools always agree;
the switch reaches the sessions you start after it. Turn the switch off in Settings to go back: until it
is on, what aegis holds and what the harness still holds both run, and the
hint names only aegis's.
