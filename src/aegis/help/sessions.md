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
and its tab opens. On a touch screen Enter adds a line and the send button
starts the session, so a mistap does not start one with half a message. The
line above the box is the working directory, inside the server's root; click
it to change it. The box works like a session's: attach, mic and send sit
inside it.

The chips on the line under the box say what runs the session: the agent,
then its harness, model, effort and permission. Picking an agent fills the
other four from its preset. Change any chip to override it for this session
only; the agent chip gets a star, and a reset button appears that puts the
preset back. An agent is a named preset from
`.aegis.yaml` (see the configuration page). Files dropped, pasted or attached
with the paperclip go out with the first message.

An agent starts a session with `session_spawn`, and a person can type
`/spawn <agent> <prompt>` in any session's box (see the commands page).

## Permission

Permission says what the agent may do, from least to most. aegis has no
approval prompt, so what a level does not allow is refused, not asked:

| permission | Claude Code runs in | in short |
|---|---|---|
| read | plan mode | reads and plans; changes nothing |
| write | accept-edits mode | edits files; a command that needs approval is refused |
| auto | auto mode | edits, and runs the commands Claude Code's checks allow |
| full | bypass-permissions mode | runs anything without asking |

OpenCode and Codex map the same four levels onto their own rules. A session
with read permission can still call aegis's tools that read or report (its
plan, its card, `session_list`, `peer_read`, `help_search`, `journal_note`,
`file_send`, `monitor_sessions` and the like), but none that starts, messages
or closes a session, enqueues, cancels or resumes a task, arms a monitor, or
works with artifacts. An agent can start sessions only at its own permission
or below. `/permission` changes a live session's permission from its next
turn, and a stopped session's when it resumes.

## Steer a running session

Type in the session's box and press Enter. A message you send while the
agent works reaches it during the turn: Claude Code reads it at its next tool
call. Messages from other sessions and from aegis are held until the turn
ends. Esc interrupts the turn.
`/model`, `/effort` and `/permission` switch those for this session; clicking
the chips under the box does the same.

## Rename it

A session has a handle, two or three lowercase words joined by hyphens, unique
on this server, and an optional title of one line. Click either at the top of
the session panel (Alt+B) to edit it, or type `/rename <handle>` or
`/title <text>`. An agent renames its own session with `session_rename`.

Under the title the panel shows the handle as peers address it,
`handle@server`, so a session on a linked server says where it runs. A queue
worker also carries the `worker · <queue>` badge its Fleet card has.

## What the agent reports

Two calls keep the person informed without reading the transcript:

- `plan_update` sends the whole plan each time, each item pending, doing or
  done. It shows in the session panel, with the time each item took, and on
  the session's card in the Fleet.
- `turn_end` labels the turn the agent hands back: needs_you when the
  message asks something, review when it gives something to read or reports
  a step while work remains, done only when the whole task is over, with a
  one-line summary and up to three replies
  the person can click to answer. Past 300k tokens of context its answer
  names the size, and at a break the agent calls it again with `handoff`: a
  brief that lets a fresh session continue the work. A done card offers an
  Archive button, so aegis refuses done while the plan has items not done:
  the agent finishes or trims the plan first, or passes review.

The mark on the tab and the card comes from `turn_end`, except working,
waiting and error, which aegis sees for itself; a turn with no `turn_end`
reads as done. A session that drifts gets one nudge from aegis: when its plan
has gone stale after a stretch of work, or when a turn ended without
`turn_end` and nothing explains the silence.

## Toasts

Refusals and failures, and the "Nobody needs you" note, show as cards in the
corner, above the message box in a session and across the screen on a phone.
They leave on their own after a few seconds, stack up to three and hold while
you hover them; × dismisses one.

An agent shows one line in every open browser with `toast`, without ending
its turn: a heads-up you should see while it keeps working. The card names
its session, and a click opens the session's tab. While the page is hidden
it also arrives as a desktop notification, if the browser allows them. A
session can send six a minute at most, and one with read permission can send
them too.

## Quiet sessions

A quiet session has no tab. Its card stays in the Fleet, dimmed and marked
`zz`, in the last group of "Needs you first". It comes back to the tab bar by
itself when its turn ends done, review, needs_you or error, and stays there
until it is quieted again; a queue worker comes back only on an error.

- The Quiet button in the session panel, beside Stop, quiets a session that
  is working or waiting.
- An agent calls `session_quiet` with a handle, its own included.
- Clicking a quiet card in the Fleet brings the session back at once.

## Stop, close and reopen

- Stop (in the session panel, or `/stop`) ends the process and keeps the
  session and its tab; the next message starts it again with its context.
- Close (`/close`, the panel's Close, or a middle click on the tab or card)
  archives the session, and its tab goes away in every browser. A session
  that is not done asks first.
- The archive is the list at the bottom of the Fleet. Filter it by title,
  handle, directory or agent; Read opens a transcript without starting anything, and
  Reopen gives the session a tab again, stopped, ready for the next message.
  Read and Reopen are icon buttons on each row, and in an archived session's
  panel Reopen is a tile like Stop, Restart and Close.
- The archive leaves out queue workers, because a worker's result already
  went to whoever enqueued its task. When some match, the Queue workers chip
  in the archive's header says how many, and a click adds them to the list.
- On a phone, a session marked done ends its transcript with an Archive
  button that closes it in one tap. It stays in the archive.

## When the server restarts

A restart of `aegis serve` continues the turns it cut, each with a note that
the server restarted, and then sends the messages sessions were holding,
instead of leaving each session for you to start again. A turn continues up
to twice in a row after clean restarts and once after a crash; the next cut
leaves it waiting for you, with a note saying so. After a crash, a harness
the dead server left running is ended first. A harness that failed on its own
is not restarted.

## Context and the cache

The Usage row's card shows the session's context and, under it, three
counters: tool calls, the share of the last ten calls' input read from the
prompt cache, and compactions. A healthy session reads nearly all of its input
from the cache. The share turns red below 80% while the session is warm,
which means something broke the cache: a model switch, an MCP server added
mid-session, a changed prefix.

A Claude Code session that has sat idle for an hour has lost its cache. When
its context is over 100k tokens, its tab says so at the end of the transcript: the next
message rewrites the whole context, as much as about 20 warm turns read.
`context:` in `.aegis.yaml` moves both thresholds (the config page says how).

## Aegis tools first

The primer tells every agent to use aegis's tool wherever its harness has one
for the same job, because only aegis's shows on the person's screen:

- to wait, `monitor_start`, not a background command the harness wakes it from;
- for its plan, `plan_update`, not the harness's todo list;
- for work it would hand a subagent, a queue task with `queue_enqueue`, whose
  result comes back as a message; `session_spawn` is for an agent the person
  may talk to.

It also asks for documents meant for the person (a spec, a plan, a report) as
one self-contained HTML page sent with `file_send`, and keeps Markdown for
files agents read.

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
