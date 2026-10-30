---
title: Links
kind: concept
when: working with sessions on another aegis server, from this one
hints:
  - Link another aegis server and start sessions on it from this one. #add-a-link
---

A link makes this server a client of another aegis server. The people on this
server then see and drive that server's sessions in the same browser, and the
agents here can start sessions there and hand them work. A link goes one way:
the far server can never reach this one, and nothing on it can reach a session
here, unless the link is [trusted](#trusted-links).

## Add a link

From a shell on this machine, pipe the far server's token into
`aegis link add <name> <url>`. The far server's admin token is in its state
directory, in `.aegis/state/token` under its root; to give the link less, mint
a member token there with `aegis token add <name>` and pipe that instead:

```bash
ssh far-host cat work/.aegis/state/token | aegis link add far http://far-host:8742
```

aegis connects once and checks that the far server calls itself by that
name, which is its `aegis serve --name` (the host name by default). Pass
`--as` with this server's own `--name` when it runs with one, since the check
introduces this server by the host name otherwise. A running server picks the
link up within a second, with no restart.

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
link; a member sees the far sessions but every call to them is refused.

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

It cannot read a session there, enqueue there, close one there, wait on one
with `monitor_sessions` or touch a schedule there; those calls are refused and
say so. Over a
[trusted link](#trusted-links) it can do all of them. Text a far server
wrote, such as titles, messages or error text, never reaches an agent here.
Only an admin's agents act through a link.

## Trusted links

When one person alone uses both servers, such as a laptop and a host they
own, a trusted link joins them both ways over the one socket the laptop
opens. The host then reaches the laptop without a connection of its own, so
it works behind NAT or a VPN.

```bash
ssh host cat work/.aegis/state/token | aegis link add host http://host:8742 --trusted
```

`aegis link trust <name>` asks an existing link for trust, and
`aegis link trust <name> --off` stops asking. In Settings, Servers, the
"Trusted (both ways)" box does the same when adding a link. The far server
grants trust only when:

- the link holds its admin token, the one in `.aegis/state/token`;
- no other token exists there, a member's or a second admin's;
- it does not itself link a server of this server's name.

A refused link stays a plain link, and Settings shows why after its state
(`trust refused: host has tokens besides its admin's`). Minting a token on
the far server later ends the trust at its next call back.

Over a trusted link, an agent on the far server reaches this one as an agent
here reaches it: `session_spawn` with `agent@<this server>`, `peer_handoff`
to `handle@<this server>`, and `session_list` lists this server's sessions.
Agents on both servers also read across it as at home: `session_list` gives
the other server's sessions in full (title, attention, plan, cwd) with
`server`, `peer_read` reads `handle@server`, and an error the other server
returns reaches the agent in its own words. They act across it as at home
too: `queue_enqueue` with `queue@server` (the result comes back to the
inbox), `task_status`, `task_cancel` and `task_resume` with `task-id@server`,
`monitor_sessions` on `handle@server`, and `session_close` on
`handle@server`, which closes a session the agent started there at once when
it is done and any other on a second call with the token, as at home. Every
`schedule_*` tool takes `name@server` too: the schedule lives and ticks on that
server, and it [waits for a person](schedules#an-agent-s-schedule-waits-for-you) as at home.
A person in the far server's browser can call this server too, with
`/spawn agent@<this server>`, but its Fleet and tabs do not show this
server's sessions: live views still go one way. Nothing is relayed: a call
that came over a link never goes on to a third server. The far server's
Settings lists this server under Links here.

## Exit through a trusted link

A server can send what it starts out to the internet through a trusted link,
from the far server's address, with no VPN and no change to the machine's
routes. Name the link in `.aegis.yaml`:

```yaml
exit_via: host
```

or press Exit here on its row in Settings, Servers. Sessions, queue workers,
workflow steps, monitors, skill tools, terminals and browsers started after that
go through a listener on loopback: each gets the proxy variables, Chromium its
launch setting, and `git` over SSH a `GIT_SSH_COMMAND`, unless you set one. A running process keeps the
environment it started with, so a change applies to the next one started, and a
session's sidebar says which exit it has. The Host rows gain a Via row with the
address they exit from.

One agent can differ from the rest, with `exit:` naming a link or `off`:

```yaml
agents:
  opus: {harness: claude-code, model: opus}
  opus-local: {harness: claude-code, model: opus, exit: "off"}
```

A terminal profile whose `env:` names `HTTPS_PROXY` or `HTTP_PROXY` keeps it.
The server's own work stays on its own route: `git` and `systemctl` it runs,
its doctor, its version checks and its network probes.

What it does not cover:

- A program that ignores the proxy variables goes out directly: a bare `ssh`, a
  raw socket, UDP. `curl`, `git` over HTTPS, `gh`, `npm`, `uv`, Python, Node's
  `fetch` and Claude Code all honour them. It is no guarantee for every packet.
- Only a trusted link can be an exit, and a multi-user server cannot use one,
  so the far server has only its admin's token.
- The relay connects to public addresses only, so an agent cannot reach the far
  server's private network through it.

When the link is down, a connection through the exit fails with a 502 that says
why, and nothing falls back to this machine's address. `aegis doctor` flags a
name that is not a trusted link.

The near server must reach the far server's aegis URL without the VPN: a far
server only on an overlay network cannot replace that overlay. The far server
sees the host and port of each HTTPS connection and all of any plain HTTP, and
Settings on it shows the bytes each link took.

An exit changes the address and the country a provider sees, and does not change
what the provider's terms allow. It is not a way around a provider's rules about
where its service may be used.

## When a link is down

A link is linked, connecting, offline, unauthorized (the far server refused
the token; it does not retry, and a new token needs a new link) or mismatch
(a different name or an incompatible version). An offline link reconnects on
its own, backing off up to 30 seconds, a mismatched one tries again every 30
seconds, and a dropped link fails every call in flight. The Fleet and the
Settings page show a server that is down as offline since the time it went
down.
