---
title: Links
kind: concept
when: working with sessions on another aegis server, from this one
---

A link makes this server a client of another aegis server. The people on this
server then see and drive that server's sessions in the same browser, and the
agents here can start sessions there and hand them work. A link goes one way:
the far server can never reach this one, and nothing on it can reach a session
here.

## Add a link

From a shell on this machine, pipe the far server's token into
`aegis link add <name> <url>`. The token is in the far server's state
directory, in `.aegis/state/token` under its root:

```bash
ssh far-host cat work/.aegis/state/token | aegis link add far http://far-host:8742
```

aegis connects once and checks that the far server calls itself by that
name, which is its `aegis serve --name` (the host name by default). `--as`
sets the name this server gives itself there. A running server picks the link
up within a second, with no restart.

An admin can also link from the Settings page, under Servers, with the far
server's URL and token. `aegis link list` shows the links by name and URL,
never their tokens, and `aegis link remove <name>` ends one.

Links are kept in this server's state directory with owner-only permissions,
because each holds a token. They are never in `.aegis.yaml`, which may be in
git. Both servers need aegis 2.4.0 or newer.

## What people here can do there

A linked server's sessions appear in the tab bar and the Fleet with the
server's name beside the handle, and its archive merges into this one's.
The new-tab page gains a server picker. Through the link a person here can
read, prompt, interrupt, stop and close those sessions, and spawn new ones, as
far as the far server's token allows. Only an admin here can act through a
link; a member can read.

On the far server the link is one principal, the token it was given. That
token's role and quota policy decide what the link may start there, and a
session it starts is owned by it.

## What agents here can do there

A session on a linked server is addressed `handle@server`. An agent here can:

- start a session there with `session_spawn` and `agent@server`, at most at
  its own permission, when that server's quota policy for the link allows it;
- hand off to a session there with `peer_handoff` and `handle@server`; it
  arrives headed `> from agent:<handle>@<server> (<user>)`;
- see those sessions in `session_list`, by handle and state only.

It cannot read a session there, enqueue there, close one there or wait on one
with `monitor_sessions`; those calls are refused and say so. Text a far server
wrote, such as titles, messages or error text, never reaches an agent here.
Only an admin's agents act through a link.

## When a link is down

A link is linked, connecting, offline, unauthorized (the far server refused
the token; a new token needs a new link) or mismatched (a different name or an
incompatible version). It reconnects on its own, backing off up to 30
seconds, and a dropped link fails every call in flight. The Fleet shows a
server that is down as offline, with the time since.
