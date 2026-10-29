---
title: Claude sign-in
kind: howto
when: Claude Code sessions keep getting logged out, fail with a 401 or "Not logged in", or need a long-lived token
hints:
  - Give every Claude session a token that lasts a year, with no logouts. #sign-in-for-a-year
  - A failed Claude sign-in shows a card that takes a new token. #when-sign-in-fails
---

Claude Code sessions in aegis sign in with your Claude subscription. This page
says how to keep them signed in, what aegis does when a session fails to sign
in, and how the quota gauges read your usage.

## Why sessions get logged out

Claude Code's stored sign-in (from Claude Code's /login) holds an access
token that lasts about eight hours and a refresh token that works once. When
the access token expires, every running Claude Code process tries to refresh
it. The first one succeeds, the old refresh token stops working, and the
others fail with a 401. The stored sign-in can end up revoked, so every
session fails until someone signs in again. An aegis server runs many
sessions at once, so it meets this often.

## Sign in for a year

A token from Claude Code's setup-token command lasts a year and is never
refreshed, so no two sessions race over it. aegis gives it to every Claude
Code process it starts as the server's own user, as `CLAUDE_CODE_OAUTH_TOKEN`:
sessions, recaps and side notes.

1. On any machine with a browser, run `claude setup-token`.
2. Approve access in the browser. If the browser shows a code (two parts
   joined by `#`), paste it back into that terminal; it is not the token.
3. The terminal prints a line starting with `sk-ant-oat01-`. That is the
   token. Paste it into the sign-in card (below), or write it to
   `~/.config/aegis/claude-oauth-token` on the server, mode 0600.

aegis reads the file each time a process starts, so a new token applies to
the next session without a restart. A `CLAUDE_CODE_OAUTH_TOKEN` already in the
server's environment, or in the multi-user pool file, wins over the file. In
multi-user mode a member's sessions keep their own sign-in.

The token can only make model requests. Claude Code's Remote Control,
claude.ai connectors and /schedule do not work with it; MCP servers you
configure yourself do. Without the file, sessions use the stored sign-in as
before.

`aegis doctor` reports the file as `claude.token`: an error when it is not
mode 0600 or owned by the server's user, a warning in the fourteen days
before the token expires, about a year after the file was written.

## When sign-in fails

When a turn fails to sign in (a 401, or "Not logged in"), the session's card
reads "Claude sign-in failed" and its transcript shows a card with the three
steps above and a field for the token. Every browser gets a toast. Sessions
that fail while the card is open join its list instead of opening another.

Paste the token and press Save and check. aegis refuses the browser's code
with what to do instead, then sends one short request on haiku with the new
token to make sure Claude Code accepts it. When it does, the token is written
to the file, every session on the list restarts and is told to continue, and
the card turns to renewed. The token never enters a transcript, a log or an
agent's context, and no agent can set or read it.

## Quota with a long-lived token

The Claude gauges read your usage from an endpoint that needs a scope the
setup-token lacks, so it refuses it. Each turn of a Claude session also
reports the five-hour and weekly windows, and the gauges take those readings
when the endpoint refuses. They update as sessions run and keep their last
reading while none does; the weekly Opus window shows only while the
endpoint works.
