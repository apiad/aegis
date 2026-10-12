---
title: Tokens, owners and quota lines
kind: concept
when: letting other people use your server, or capping how much of the subscription their agents spend
---

A server holds many tokens. Each token is a principal, with a name and a
role, admin or member; the server's own token is an admin. Every session and
every queue task has an owner, the principal that started it, and an agent
acts as its session's owner.

## Members and admins

A member reads every session on the server and changes only the ones it
owns. Server settings, adding and removing links, tokens, quota policies and
spawning on a linked server are for admins.

Admins manage tokens in Settings, or in a shell with `aegis token add`,
`aegis token list` and `aegis token revoke`. A new token is a member's unless
it is added with `--role admin`. A link to another server acts there as the
token it presents.

## Quota lines

Admins are never held back. A member's agents spend each subscription only as
far as the member's policy allows. An admin sets a policy per token, or one
for every member by default, in Settings, and it has one rule per provider:
closed, open, or lines. A provider the policy does not name is closed, and a
member with no policy and no default starts nothing.

Lines are a launch line and a stop line on each quota window you tick. Drag
the two handles on the window's gauge, or move one with the arrow keys. Tick
pace instead of a launch line to let new work start while the spend projected
at the reset stays under 100%. Codex reports no quota, so its rule is only
closed or open.

- Over a launch line, the member's spawns are refused with the reason and
  when they reopen, and its queue tasks wait. A session already running keeps
  working. A message to one of its stopped sessions is refused when a person
  typed it, and held until the line clears when another session sent it.
- At a stop line, aegis interrupts the member's running turns and continues
  them when the gauge is back under every launch line, across a restart too.

A member sees its own launch and stop lines on the quota gauges in its
session panel. An admin's agents can spawn on a linked server, where that
server's policy for the link's token decides.
