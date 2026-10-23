---
title: Memories and the knowledge hint
kind: concept
when: remembering a fact for later sessions, finding what aegis remembers, or reading the knowledge block under a message
hints:
  - Type /remember and a fact; every later session can find it. #remember-something
  - The knowledge block under your message names what may help the agent. #the-hint
---

aegis keeps your memories on the server, one Markdown file per fact, and
every session you start, in any harness, can find them. Nothing is loaded
up front: each message carries a short block naming the few memories that
may matter, and the agent reads the ones it needs. Each person on a server
has their own memories, and nobody else's agents see them.

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

When a message reaches a session, aegis searches your memories with its
text and adds a short block under it, of up to five lines, folded into its
own row in the transcript:

```text
> from aegis:notice · knowledge · may be relevant
memories: deploy-host: Where the bot runs; check before any ssh · ...

read with memory_read · search with memory_search
```

It names the three best memories, with no threshold, and the tools that
read and search them: the agent decides whether any matters. A message
that matches nothing, or a slash command, gets no block. An expired memory is never
named, but a search still finds it.

The search is BM25 over the name, the description and the start of the
body. It folds accents and cuts words to their first five letters, so
"configuración" finds "configuration", but different words in two
languages, such as "despliegue" and "deploy", do not meet.

## Tools for agents

- `memory_search` ranks your memories against a query in the agent's own
  words, and marks expired ones.
- `memory_read` returns one or more memories in full, by name.
- `memory_save` creates or replaces a memory. An agent passes `asked=true`
  when you asked it to remember something.
- `memory_delete` deletes one; the history keeps it.
- `knowledge_stats` lists each item with how often it was hinted, read,
  invoked or run, and when it was last used; `unused_days=60` keeps the
  ones nobody used in that time.

A session with read permission can search, read and see stats, but not
save or delete.

## Remember something

Type `/remember` and the fact, such as `/remember the staging host for
quill is ben-staging-2`. The agent saves it with `memory_save`, choosing
a name, a description and a type, and makes it temporary when the fact
names a date that will pass. It tells you what it saved.

You can also ask in your own words ("remember that we never deploy on
Fridays"); the agent saves it the same way.
