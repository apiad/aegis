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
at the reset stays at or under 100%. Codex reports no quota, so its rule is only
closed or open.

- Over a launch line, the member's spawns are refused with the reason and
  when they reopen, and its queue tasks wait. A session already running keeps
  working. A message to one of its stopped sessions is refused when a person
  typed it, and held until the line clears when another session sent it.
- At a stop line, aegis interrupts the member's running turns and continues
  them when the gauge is back under every launch line, across a restart too.
  Until then a person's message to any of its sessions, running or not, is
  refused, and one from another session is held.

A member sees its own launch and stop lines on the quota gauges in its
session panel. An admin's agents can spawn on a linked server, where that
server's policy for the link's token decides.

## Linux users

With `multi_user:` in `.aegis.yaml`, each token also names the Linux user its
sessions run as. A person's harness, terminals, monitors, workflow steps and
the git calls aegis makes for them all run as that user, with no special
permissions, so their commits and pull requests are their own and the kernel
checks every file they touch. A member's session starts in their home.

`aegis token add ben` names the Linux user `ben`; `--linux-user` names
another. The user must exist, must be a regular account, and may not be root
or the server's own user. `--no-linux-user` is for an admin only, such as a
link from another of your servers, and runs as the server's user. Settings
shows each token's user and asks for it when you add one.

The server needs three capabilities from its systemd unit for this, which
`aegis service install --multi-user` writes, and refuses to start without
them. `aegis doctor` checks the unit, the tokens, the state directory and the
pool file. A guided setup comes in a later release; until then the mode is
for trying out.
