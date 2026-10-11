---
title: Sessions
kind: concept
when: starting, steering, renaming, stopping, closing and reopening agent sessions
---

A session is one agent's conversation, run by its harness (Claude Code,
OpenCode or Codex) on this server, with a tab in every browser. It outlives
its process: a session is live while its harness runs, stopped when it does
not, and archived once closed. Closing the browser leaves every session
running.

## Start a session

Open a new tab with the + at the end of the tab bar, or Alt+N. Type what the
agent should do and press Enter; Shift+Enter adds a line. The session starts
and its tab opens. The line above the box is the working directory, inside the
server's root; click it to change it.

The chips under the box say what runs the session: the agent, then its
harness, model, effort and permission. Picking an agent fills the other four
from its preset. Change any chip to override it for this session only; a
reset chip appears and puts the preset back. An agent is a named preset from
`.aegis.yaml` (see the configuration page). Files dropped, pasted or attached
with the paperclip go out with the first message.

An agent starts a session with `session_spawn`, and a person can type
`/spawn <agent> <prompt>` in any session's box (see the commands page).

## Permission

Permission says what the agent may do without asking, from least to most:

| permission | Claude Code runs in | in short |
|---|---|---|
| read | plan mode | reads and plans; changes nothing |
| write | accept-edits mode | edits files; asks before running commands |
| auto | auto mode | edits, and runs the commands Claude Code's checks allow |
| full | bypass-permissions mode | runs anything without asking |

OpenCode and Codex map the same four levels onto their own rules. A session
with read permission can still call aegis's reading tools (its plan, its
card, `session_list`, `peer_read`, `help_search`) and nothing that acts. An
agent can start sessions only at its own permission or below. `/permission`
changes a live session's permission, and the change applies at once.

## Steer a running session

Type in the session's box and press Enter. A message sent while the agent
works is held and delivered when its turn ends. Esc interrupts the turn.
`/model`, `/effort` and `/permission` switch those for this session; clicking
the chips under the box does the same.

## Rename it

A session has a handle, two or three lowercase words joined by hyphens, unique
on this server, and an optional title of one line. Click either at the top of
the session panel (Alt+B) to edit it, or type `/rename <handle>` or
`/title <text>`. An agent renames its own session with `session_rename`.

## What the agent reports

Two calls keep the person informed without reading the transcript:

- `plan_update` sends the whole plan each time, each item pending, doing or
  done. It shows in the session panel and on the session's card in the
  Fleet, with the time each item took.
- `turn_end` labels the turn the agent hands back: needs_you when the
  message asks something, review when it gives something to read, done when
  it reports finished work, with a one-line summary and up to three replies
  the person can click to answer.

The mark on the tab and the card comes from these. A session that drifts gets
one nudge from aegis: when its plan has gone stale after a stretch of work, or
when a turn ended without `turn_end` and nothing explains the silence.

## Stop, close and reopen

- Stop (in the session panel, or `/stop`) ends the process and keeps the
  session and its tab; the next message starts it again with its context.
- Close (`/close`, the panel's Close, or a middle click on the tab or card)
  archives the session, and its tab goes away in every browser. A session
  that is not done asks first.
- The archive is the list at the bottom of the Fleet. Filter it by title,
  handle or directory; Read opens a transcript without starting anything, and
  Reopen gives the session a tab again, stopped, ready for the next message.

## The journal, quota and the rest

- `journal_search` asks what was done on this server, by day, words, path,
  session, repo or kind; commits, pull requests, finished plan items and turns
  that changed files are recorded without anyone writing them down.
  `journal_note` records a decision, a blocker or a milestone aegis cannot
  see. People open the Journal with Alt+L.
- `quota_read` returns how much of each subscription window is left, from the
  last reading, at no cost.
- `meta` returns the primer every agent gets at spawn and the list of aegis's
  tools.
