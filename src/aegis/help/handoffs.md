---
title: Handoffs and peers
kind: concept
when: reading another session, giving it work or context, or starting one to work beside you
hints:
  - An agent can read another session and hand it work without you copying text. #hand-work-over
  - Near the end of a long context, continue in a fresh session with a brief. #continue-in-a-fresh-session
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
held and arrives when its turn ends; meanwhile its next aegis tool result tells
it who waits and quotes your message's first line, so make that line say what
changes. Two flags change the timing:

- `urgent=true` reaches a busy receiver inside its turn, at its next tool step,
  the way a person's typing does. Use it for a correction that changes its work
  now: stop, a cut in scope, don't do X. Its row shows `steered in`. Claude Code
  and Codex take it; on another harness it is held like any message.
- `interrupt=true` cuts the receiver's current turn first. It cannot be combined
  with `urgent`.

The reply says what happened: `landed at <handle>` (it was idle), `held for
<handle>` (with the reason when its harness cannot take an urgent message), or
`steered into <handle>`. A handoff to an archived session is refused, and a
member's agent can hand off only to sessions its owner owns.

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
size. Clicking it starts the
same agent in the same folder with the brief and the last three turns, and
opens its tab. The old session is archived when the new one finishes its first
turn, unless you have written to it since; the new one can still `peer_read`
it. A cache-expired notice carries the same button, with the last turns alone
when no brief was written.

The new session takes the old one's place in its group of tabs: it joins the
group, and heads it when the old one did. Its first message names the sessions
the old one started that are still open, and asks it to tell each one with
`peer_handoff` that it continues the work. Once the old session is archived,
those sessions count as started by the new one: its card lists them, and it
waits on them as their parent.

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

## Close yourself when you finish

When the person tells you to close when you are done ("merge it and close"),
or the plan or workflow they approved says so, end your last turn with
`turn_end` with `attention` done and `close` true. Never pass it on your own
judgment: a person may still want to read the tab.

aegis archives your session once that turn ends, together with every session
you started and every session they started, deepest first. It refuses the
`turn_end` with what is still running when you have a live monitor, an open
background task, a queue task you wait on or an approved workflow run, or
when one of those sessions is still working, waiting, or did not end its last
turn done. Wait for it, cancel or close it, then call `turn_end` again, or
leave `close` out and the tab stays open. A message the person sends before
the turn ends starts a new turn, and only that turn's `turn_end` decides.
