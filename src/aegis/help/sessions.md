---
title: Sessions
kind: concept
when: starting, steering, renaming, sleeping, waking, restarting, parking, archiving and reopening agent sessions
---

A session is one agent's conversation, run by its harness (Claude Code,
OpenCode or Codex) on this server, with a tab in every browser. It outlives
its process: a session is live while its harness runs, asleep when it has
no process and keeps its conversation, and archived once you archive it.
Closing the browser leaves every session running.

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

## An untested harness version

Each aegis release is tested with a range of versions of each harness. When
the installed one is outside it, a session you start pauses on a dialog that
names the version, the range, the binary aegis found (its real path, past any
symlink) and the command that installs the newest tested version with the
installer that put it there, with a copy button. Start once starts it, and a
later message to that session does not ask again while the version stays the
same. Always allow adds that exact version to `harness_versions.accepted` in
`.aegis.yaml`, so it never asks for it again; the next update asks. Cancel
starts nothing and keeps what you typed. Only an admin sees Always allow.

A session started by an agent, a queue, a schedule or a workflow, or a parked
session that wakes, starts without asking. Its transcript gets one notice with
the same version, path and command, and an agent's `session_spawn` result
carries it too. aegis does not install anything: run the command, and the next
session starts on the tested version. The configuration page says how to turn
the question or the notice off.

## From the Help tab

Every help page has a Guided tour button. On a tutorial it starts the
tutorial's guide; on any other page it starts a guide that turns the page into
steps you try. To ask an agent a question about aegis, type the question in
the Help tab's search box: an Ask an agent row appears above the results, and
it starts a session whose first message is your question and, when a page
is open, the page you were reading. Both open a new tab from the default agent and
spend quota; Enter in the box still opens the best result.

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
turn, and an asleep session's when it wakes.

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
quiet with a crossed-out eye, in the last group of "Needs you first". It comes back to the tab bar by
itself when its turn ends done, review, needs_you or error, and stays there
until it is quieted again; a queue worker comes back only on an error.

- The Quiet button in the session panel, beside Sleep, quiets a session that
  is working or waiting.
- An agent calls `session_quiet` with a handle, its own included.
- Clicking a quiet card in the Fleet brings the session back at once.

Quiet is about the tab, not the process: a quiet session keeps working, and
sleeping one keeps it quiet. Wake, Continue and Restart leave it quiet too;
Reopen clears it.

## Menus

A session's card in the Fleet and its tab carry the same menu. Open it with a
right-click, a long press on a touch screen, or the ContextMenu key or
Shift+F10 on a selected card; Shift with a right-click keeps the browser's own
menu. Arrows move through it, Enter runs an item, Esc closes it.

It shows only what applies to that session now: Open, Open in new tab, and
Quiet or Unquiet; then Interrupt while it works, Wake and Continue while it
is asleep, Sleep while it is not, Restart, and Hand off; then Rename…, which
opens the session with its handle ready to edit; then Archive. Each acts on
that session, not on the one shown. Another member's session offers Open and
Open in new tab only, and a step of a running workflow shows the rest
disabled, with the reason as the item's tooltip. A terminal's card has Open,
Open in new tab and Archive.

The tree in the Files view has a menu opened the same way.

## Sleep, wake, restart, archive and reopen

| verb | where | what it does |
|---|---|---|
| Interrupt | the menu, Esc, the button beside Send | ends the turn the agent is working on |
| Sleep | the menu, the panel, `/sleep` (`/stop` is an alias) | ends the process and keeps the conversation; the session is asleep until a message wakes it. When ending it would also end a background task, a harness timer or a process it left running (a `nohup` command, a dev server), the menu and the panel ask first and name them |
| Wake | the menu, on an asleep session | starts the process and sends nothing. Like a message, it is refused while the quota gate is closed, and it asks before starting a harness version outside the tested range |
| Continue | the menu, on an asleep session; the panel | sends "Continue" to the agent |
| Restart | the menu, the panel, `/restart` | ends the process and starts a fresh one on the same conversation; nothing is sent. On a working session it asks first, typed or clicked, since it cuts the turn. Use it after a change to the agent's MCP config or when the harness hangs |
| Archive | the menu, the panel, `/archive` (`/close` is an alias), a middle click on the tab or card | archives the session, and its tab goes away in every browser. A session that is not done asks first |
| Reopen | the archive, an archived session's panel | gives the session a tab again, asleep, ready for the next message |

An asleep session's card is marked `zz` with the reason it has no process:

- `slept`: someone slept it (Sleep, `/sleep`, or an agent through
  `session.stop`);
- `idle`: aegis parked it (see below);
- `exited`: the harness ended on its own;
- `server`: the server restarted under it.

- The archive is the list at the bottom of the Fleet. Filter it by title,
  handle, directory or agent; Read opens a transcript without starting anything, and
  Reopen gives the session a tab again, asleep, ready for the next message.
  Read and Reopen are icon buttons on each row, and in an archived session's
  panel Reopen is a tile like Sleep, Restart and Archive.
- The archive leaves out queue workers, because a worker's result already
  went to whoever enqueued its task. When some match, the Queue workers chip
  in the archive's header says how many, and a click adds them to the list.
- On a phone, a session marked done ends its transcript with an Archive
  button that archives it in one tap.

## Parked sessions

A Claude Code harness holds about 300 MB while it waits for the next
message. When an admin turns parking on for a harness (the configuration page
says how), aegis ends the process of an idle session of that harness that
nothing needs, and the next message resumes it with its context. aegis checks
every 30 seconds. A session parks once all of these have held for the
harness's idle time without a break, and none of the cases in the next list
applies:

- its harness is idle, with a process;
- its last turn ended with `turn_end` (done, needs_you or review), or it
  waits on a monitor, a queue task or a session. A turn that ended without
  `turn_end`, a person's slash command and no turn yet do not qualify;
- it holds no message for the harness.

These sessions do not park:

- a queue worker;
- a session whose transcript is open in a visible page, in this browser or
  in one on a linked server. A hidden tab, a transcript docked beside
  another, and the Fleet do not count;
- a session with an open background task, such as a background Bash call, a
  Monitor or a background subagent, which wakes the session when it ends;
- a session whose harness has called ScheduleWakeup or CronCreate. Those
  timers live inside the harness, so the call keeps the session up until its
  process ends for another reason, even after the timer fired or was deleted;
- a session with a process left running, such as a `nohup` command or a dev
  server. For Claude Code, a process the harness started directly that is
  not a shell, such as a stdio MCP server or a plugin's helper, does not
  count: it ends and starts again with the harness. An MCP server started
  through `sh -c` does count. For Codex and OpenCode every child process
  counts, so a session of theirs with any MCP server never parks.

A parked session is asleep with the reason `idle`. It keeps its tab, its mark
and its place in the attention counts, and leaves the live count and the
context average. Its Fleet card carries a chip such as `zz idle 20 min`, its
composer reads "Asleep after 20 min idle. Typing wakes it.", and its
transcript gets a row such as "asleep after 20 min idle". The Fleet's header counts every asleep session,
as in `3 asleep · ~0.9 GB idle`: the memory each parked harness held when it
parked, without the processes it had started.

Any message wakes a parked session: one you send, a monitor's or queue's
callback, a handoff from another session, or one of aegis's nudges. Opening
its transcript does not. The first keystroke in its composer starts the
harness while you type, and a "Waking · resuming the harness" row shows in
the transcript until it is back. A wake costs one harness start, about 3 s
for Claude Code; slow SessionStart hooks in the checkout add their own time.

Sleep on a parked session changes its reason to `slept`: typing no longer
wakes it, and it stays asleep until someone sends it a message.

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
