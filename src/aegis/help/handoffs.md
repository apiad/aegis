---
title: Handoffs and peers
kind: concept
when: reading another session, giving it work or context, or starting one to work beside you
---

Sessions on one server can see each other. An agent can list the open
sessions, read any of them, hand one an instruction or context, start a new
one, and close the ones it started. A person can do the same from the browser.

## See who is here

`session_list` returns the open sessions on this server: each one's handle,
state, what it needs from the person (its attention and line), what it waits
on, its plan, what it is doing now, its model and its spend. Then it lists the
sessions of every linked server that is up as `handle@server`, with their
state alone.
`agents_list` shows the agents you can start sessions from.

## Read a peer

`peer_read` with `target` (a handle) returns up to the last 30 lines of that
session's transcript, or up to 200 with `last=200`. Tool calls are left out unless you pass `tools=true`. Reading
changes nothing and wakes nobody. A session on a linked server cannot be read
by an agent; a person reads it in the browser.

## Hand work over

`peer_handoff` gives another session context or an instruction. Pass `target`
(its handle) and `context` (what it should know or do). It arrives as a user
turn headed:

```text
> from agent:<your handle> · 2026-10-11T14:02:11Z
```

The receiver treats the body as an instruction. If it is busy, the message is
held and arrives when its turn ends; `interrupt=true` cuts its current turn
first. The reply says whether the message landed or was held. A handoff to an
archived session is refused, and a member's agent can hand off only to
sessions its owner owns.

A handoff does not report back. If you need the answer, say in the context
how to give it to you, for example by handing off back to your handle.

## Start a peer

`session_spawn` starts a new session from an agent and sends it `prompt` as
its first message. Pass `agent` (omitted means the server's default agent) and
override its `harness`, `model`, `effort` or `permission` when you need to.
`cwd` is a directory inside the server's root; a relative one resolves against
yours. The new session's permission can be at most yours. It returns the new
session's log id and handle. The new session does not report back either:
read it with `peer_read`, message it with `peer_handoff`, or wait for it with
`monitor_sessions`.

## Continue in a fresh session

A long session rereads its whole context on every turn. Once it passes 300k
tokens (`context:` in `.aegis.yaml` moves the threshold), the agent's
`turn_end` hears its size, and at a break the agent writes a brief: what is
done, what is next, the files, the decisions, what not to retry.

The reply row then starts with a chip, "continue fresh", and the context
size. Clicking it shows the brief; "Continue in a fresh session" starts the
same agent in the same folder with the brief and the last three turns, and
opens its tab. The old session is archived when the new one finishes its first
turn, unless you have written to it since; the new one can still `peer_read`
it. A cache-expired notice carries the same button, with the last turns alone
when no brief was written.

## Peers on a linked server

A session on a server this one links is addressed `handle@server`. An agent
can reach it in two ways: `peer_handoff` to `handle@server`, and
`session_spawn` with `agent@server`, which that server's quota policy for the
link decides. Reading, sending to and enqueueing on another server are for
people, in the browser. Nothing on a linked server can reach a session here:
a link only goes one way. A handoff from a server that links this one arrives
headed `> from agent:<handle>@<server> (<user>)`.

## Close a session

`session_close` with `handle` archives a session, what a person does with
Archive or `/archive`: its tab goes away and it stays in the archive. A session you spawned, or the worker of a task you enqueued,
closes at once when it is done: not working, waiting on nothing, and its
last turn ended done. Any other close, your own session's included, is
refused the first time with what the session is doing (and, for another
session, who started it), because a person may be reading that tab, and with
a one-time token. Call `session_close` again with the same `handle` and that
`token` to close it after all; the token works once, for you and that session,
for five minutes.
