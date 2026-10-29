---
title: Sessions
kind: concept
when: starting, steering, renaming, sleeping, waking, restarting, parking, archiving and reopening agent sessions
hints:
  - Put a session to sleep to free its process; a message wakes it. #sleep-wake-restart-archive-and-reopen
  - Steer a running agent by typing while it works. #steer-a-running-session
  - A quiet session works without marking its tab for your attention. #quiet-sessions
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
`/spawn <agent> <prompt>` in any session's box (see the commands page). A
first message that starts with `/` runs as if typed in the new session's box,
so `/feedback <words>` runs aegis's command instead of reaching the harness as
text; start it with `//` to send a leading `/`.

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

While a session runs on an untested version, the harness row of its panel names
that version in the warning colour; hovering it shows the tested range.

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
ends, unless an agent sent a handoff as urgent: that one reaches the turn the
same way yours does, and its row shows `steered in`. Esc interrupts the turn.
`/model`, `/effort` and `/permission` switch those for this session; clicking
the chips under the box does the same.

## Dictate a message

The mic button in a session's box and in the new-tab box, or Alt+M, records
you and writes what you say into the box. Speech is transcribed in your
browser, and nothing is sent until you press Enter, which first finishes the
words still being transcribed.

Dictation mishears words, names and terms. A message whose box got dictated
text since your last send tells the agent so: after your text it reads a line
saying that part of the message was dictated and may contain transcription
errors, so it reads by context and asks about what it cannot make sense of.
Your row in the transcript shows "🎙 dictated" in place of that line. Emptying
the box forgets the mark. A message to a session on a linked server, or one
queued while offline, goes without the line.

## Rename it

A session has a handle, two or three lowercase words joined by hyphens, unique
among the open sessions on this server, and an optional title of one line. An
archived session's handle is free to take: reopening that session then gives it
the handle with a number added (`docs-sweep-2`) when an open session holds it. Click either at the top of
the session panel (Alt+B) to edit it, or type `/rename <handle>` or
`/title <text>`. An agent renames its own session with `session_rename`, setting
both a handle and a title in one call; until it has set both, `turn_end`
reminds it.

Under the title the panel shows the handle as peers address it,
`handle@server`, so a session on a linked server says where it runs. A queue
worker also carries the `worker · <queue>` badge its Fleet card has.

## The repos a session works in

The session panel's Repos row lists each repository the session has written
a file in or committed to, newest first, one line per repo: the repo's name,
its branch (or `⑂` and the directory of a linked worktree, or `n checkouts`
when the session worked in several of the repo's checkouts), then `~n` for
files the session wrote and has not committed, `↑n` for its commits no remote
has yet, its open pull requests as links to GitHub, or `✓` when nothing is
pending. A dot marks a repo another open session also wrote in. A repo stays
listed once clean, so the row also records where the session worked.

Hover the row, or tap it on a phone, for its card: one section per repo, and
in it one block per checkout (the main checkout first, then the worktrees)
with the branch and upstream, the uncommitted files, the unpushed commits, the commits the session
left there and its PRs with their state and CI, files other sessions left
dirty in the same checkout. Below them each other session working in the repo
is named once, with the checkouts it wrote in. A commit
counts as pushed once a remote has it or an equivalent patch, so a PR merged
by rebase with its branch deleted reads as clean. A write shows within
seconds, mid-turn; git is asked at most every 10 seconds per worktree, and
only for sessions a browser shows. PR state and CI come from `gh`; without it
the card lists the PRs the journal saw and says their state is unknown.

## What the agent reports

Two calls keep the person informed without reading the transcript:

- `plan_update` sends the whole plan each time, each item pending, doing or
  done. It shows in the session panel, with the time each item took, and on
  the session's card in the Fleet.
- `turn_end` labels the turn the agent hands back: needs_you when the
  message asks something, review when it gives something to read or reports
  a step while work remains, done only when the whole task is over, with a
  one-line summary and up to three replies
  the person can click to answer. Its answer says what the person has
  seen from the agent this turn: nothing yet, and the agent writes its whole
  message after the call; or how many messages and how the last begins, so
  the agent closes in a line when its answer is already there. Past 300k tokens of context its answer
  names the size, and at a break the agent calls it again with `handoff`: a
  brief that lets a fresh session continue the work. A done card offers an
  Archive button, so aegis refuses done while the plan has items not done,
  or while a monitor, queue task, background task or workflow will still
  wake the agent: it finishes or trims the plan, cancels what it no longer
  waits on, or passes review.

The mark on the tab and the card comes from `turn_end`, except working,
waiting and error, which aegis sees for itself; a turn with no `turn_end`
reads as done. A session that drifts gets one nudge from aegis: when its plan
has gone stale after a stretch of work, or two seconds after a turn ended
without `turn_end` when nothing explains the silence (a live monitor or queue
task does). The `nudges:` block in `.aegis.yaml` sets both (see the config
page).

## When a background task ends

Claude Code wakes the agent with a message of its own when a background
command or a subagent it started ends. The transcript draws it as an inbox
row, not as your message: a header such as `background · failed · exit 1`
(red when the task failed or was stopped), the command's
opening comment or the subagent's name under it, and the command, or the
subagent's report, and the file its output went to, folded below. Click the
fold to open it.

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

## Notices

aegis tells every live session when the host runs short: the disk is full,
memory is short, the CPU is saturated or the quota runs ahead of pace. A
notice never starts a turn. It rides on the session's next message or on the
result of its next aegis tool call, whichever comes first, and never goes
into the system prompt. A call made with `aegis call` carries none, so its
output still parses. An agent reads a block headed like an inbox message:

```text
> from aegis:notice · disk · warn · 2026-10-18T09:12:04
The disk holding aegis is 94% full: 4.8 GB free of 75.0 GB, measured just now.
Free what this session created and no longer needs (your merged worktrees, ...
```

The transcript shows it as an aegis row under the message or the tool call
that carried it; your own row shows only what you typed.

When the host has another disk that is below the disk's `clear:` level, that
aegis's user can write to and that has more room, the disk notice names it
(`/mnt/data has 65.0 GB free`) and asks the agent to put new large files
there, in place of starting no large download or build. The disks are the
ones the host meters list, so a mount the `host:` block hides is never named.

Each session is told once per episode. An episode starts when a reading
reaches `on:`, is told again when it reaches `critical:`, and ends only when
the reading falls below `clear:`, so a disk hovering at the line is not
announced twice. When it ends, every session that was told gets a one-line
clear. A notice at critical also shows you a toast, once per episode.

| kind | reading | on, critical, clear |
|---|---|---|
| disk | the fullest filesystem holding the config root or the temporary directory | 90, 97, 85 |
| memory | percent of RAM not available, or memory pressure (PSI full avg60) at 5 or more | 90, 95, 85 |
| cpu | CPU pressure (PSI some avg60), or the load over the CPU count times 25 | 50, none, 30 |
| quota | the account's quota windows, for sessions that draw on that account: warning when a window is on pace to run out before its reset with half of it spent, critical at 90% spent | warning, critical, normal |
| announce | what a person or an admin's agent wrote | as written |

The memory and cpu notices also say what the reading session itself holds,
and which session holds the most (see [Load](#load)). The heaviest session is
told to slow or free its own work first; the others read the usual advice.
The text tells an agent to free or cut back only what its own session
created and never to touch shared caches or another session's work. Only
one session asks you about the rest: per kind and per owner, the first
session you opened that the notice reaches. Every other session you opened
is told that another one is asking; a spawned session reports to the agent
that started it, and a queue worker, a workflow node or a schedule's run
never asks anyone. When the asker closes, the next session you opened takes
over. cpu and announce have no asker.

The `notices:` block in `.aegis.yaml` moves the lines or turns a kind off
(see the config page).

### Notices about the agent's own work

The same channel tells an agent about its own work, so it does not have to
ask. Its next message carries a status block when what it tracks changed:

```text
> from aegis:notice · status · info · 2026-10-20T07:41:12
plan 3/7 · doing: write the stall check
monitor mon-5e1f tests 40%
queue tasks out: 2 running (review-a, review-b)
```

The status block rides only on messages, never on a tool result. Warnings
ride on both, once, and say when they end: a plan untouched for ten minutes
of work, a monitor that has stalled, cannot run its check or has overrun
its ETA (see the monitors page), and each message held for the turn's end:

```text
> from aegis:notice · held:m1792684879882 · warn · 2026-10-22T12:04:11
agent:director waits for your turn to end: "Scope changed: do #47 only, then end with done." End your turn early if it changes your work.
```

`notices: {held: false}` turns the held warnings off. After the context is
compacted, the next message tells it all again.

## Announcements

`/announce aegis restarts at 14:00 --until 14:10` tells every agent on the
server something, once, the same way. `--until HH:MM` ends it by itself
(today, or tomorrow when that minute has passed) and `--critical` also shows
a toast. An agent calls `notice_announce` with `text=`, `level=` and
`until=`; `clear=` with an id ends one early, and a call with neither lists
those in force. Each agent gets the text and one line more, "This was
written by ops-agent, not measured by aegis.", naming the agent's handle or
your name as the author; the agent that wrote it is not told its own. Announcing is for an admin and an admin's agents; a member gets
not_for_members.

## Order the tabs

The tab bar's order is this browser's own, and a reload keeps it. A session
started from another one sits in that one's group, a block headed by the
session that started it. Drag a tab to move it: a group's head moves the whole
group, and any other member moves only inside its group. Alt+Shift+[ and
Alt+Shift+] move the tab in front one place left or right by the same rules,
from the message box too.

## Switch between sessions

Alt+P lists every open session: this server's at the top, then each linked
server's in the Fleet's order, then the quiet ones from all of them, each
named with its server. A row shows the session's mark, handle and title, its
state and when it last changed, and the line it is on: what it asks you, its
plan item, or what it is doing.

- Within a server, the sessions that need you lead, then errors, then
  results to review, the longest waiting on top, as Alt+J walks them. The
  rest follow, the most recent on top.
- Type to narrow the list by handle, title, that line or the server's name.
  Arrows, Tab or Alt+P move; Alt+Shift+P moves back; Page Down and Page Up go
  to the next or previous server. Enter opens the session; Esc closes the list.
- Opening a quiet session brings its tab back, as a click on its Fleet card
  does.
- The "N ▾" button at the end of the tab bar opens the same list. It shows
  while the tabs overflow, and always on a phone, where it is the way in.

## Quiet sessions

A quiet session has no tab. Its card stays in the Fleet, dimmed and marked
quiet with a crossed-out eye, in the last group of "Needs you first". It comes back to the tab bar by
itself when its turn ends done, review, needs_you or error, and stays there
until it is quieted again; a queue worker comes back only on an error.

- The Quiet button in the session panel, beside Sleep, quiets a session that
  is working or waiting. Alt+Q does the same for the session in front, from
  the message box too.
- `/quiet` in a session's box quiets that session, as Alt+Q does.
- An agent calls `session_quiet` with a handle, its own included.
- Clicking a quiet card in the Fleet brings the session back at once.
- Opening it from the Alt+P list does the same (see
  [Switch between sessions](#switch-between-sessions)).

Quiet is about the tab, not the process: a quiet session keeps working, and
sleeping one keeps it quiet. Wake, Continue and Restart leave it quiet too;
Reopen clears it.

## Autoclose

Autoclose is your word, given ahead of time, that a session should close
once its agent finishes. While it is on, the agent can end a turn in three
ways only: done with `close`, which archives the session as soon as nothing
it started still runs; review; or needs_you. A `turn_end` with done and no
`close` is refused with a message that says so, and the agent picks one of
the three. You no longer have to tell it "close when you are done".

- The Autoclose button in the session panel turns it on and off; it shows
  in the accent colour while on.
- The menu of a session's card or tab has Autoclose on and Autoclose off.
- `/autoclose` in a session's box toggles it; `/autoclose on` and
  `/autoclose off` set it.

Only a person turns it on or off: no agent tool does. It stays on across a
sleep, a restart and a reopen. Everything that refuses `close` still
refuses it: a live monitor, a queue task, a workflow run, or a session it
started that has not ended done. The agent waits for those, or cancels
them, before it ends done.

## Menus

A session's card in the Fleet and its tab carry the same menu. Open it with a
right-click, a long press on a touch screen, or the ContextMenu key or
Shift+F10 on a selected card; Shift with a right-click keeps the browser's own
menu. Arrows move through it, Enter runs an item, Esc closes it.

It shows only what applies to that session now: Open, Open in new tab, and
Quiet or Unquiet; then Interrupt while it works, Wake while it is asleep,
Continue while it is not working, Sleep while it is not asleep, Restart, and
Hand off; then Rename…, which
opens the session with its handle ready to edit; then Archive. Each acts on
that session, not on the one shown. Another member's session offers Open and
Open in new tab only, and a step of a running workflow shows the rest
disabled, with the reason as the item's tooltip. A terminal's card has Open,
Open in new tab and Archive.

The tree in the Files view has a menu opened the same way, and so do the
Journal's entries (Open commit or Open PR, when the entry has one on GitHub,
and Go to session) and the archive's rows (Read and Reopen).

## Sleep, wake, restart, archive and reopen

| verb | where | what it does |
|---|---|---|
| Interrupt | the menu, Esc, the button beside Send | ends the turn the agent is working on |
| Sleep | the menu, the panel, `/sleep` (`/stop` is an alias) | ends the process and keeps the conversation; the session is asleep until a message wakes it. When ending it would also end a background task, a harness timer or a process it left running (a `nohup` command, a dev server), the menu and the panel ask first and name them; when aegis cannot check, they ask anyway |
| Wake | the menu, on an asleep session | starts the process and sends nothing. Like a message, it is refused while the quota gate is closed, and it asks before starting a harness version outside the tested range |
| Continue | the menu and the panel, on a session that is not working | sends "Continue" to the agent, which wakes an asleep one |
| Restart | the menu, the panel, `/restart` | ends the process and starts a fresh one on the same conversation; nothing is sent. On a working session it asks first, typed or clicked, since it cuts the turn. Use it after a change to the agent's MCP config or when the harness hangs |
| Archive | the menu, the panel, `/archive` (`/close` is an alias), a middle click on the tab or card, Alt+A in a session | archives the session, and its tab goes away in every browser. A session that is not done asks first |
| Reopen | the archive, an archived session's panel | gives the session a tab again, asleep, ready for the next message |

An asleep session's card is marked `zz` with the reason it has no process:

- `slept`: someone slept it (Sleep, `/sleep`, or an agent through
  `session.stop`);
- `idle`: aegis parked it (see below);
- `exited`: the harness ended on its own. Mid-turn or with a non-zero code
  the card also shows the error; between turns with code 0 it does not;
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

## Pause every session

When the sessions together overload the host, a restart alone brings the
load back: it continues every turn it cut. Pause every session instead.

- **Settings, Sessions, Pause all**, on a running server. Every session's
  process ends, and with it whatever its agent left running: builds, test
  suites, browsers. A turn this cuts shows `paused: the person paused every
  session` in its transcript.
- **`aegis serve --paused`** starts or restarts with every session paused:
  the turns the stop cut stay cut, in a one-off run or a systemd drop-in.

While the pause lasts, nothing new starts. Messages from agents and monitors
wait in each session's held list, queued tasks stay pending, and spawns
wait, each saying why: `the person paused every session`. The pause lasts
across a restart, until you lift it:

- **Resume all**, in the same place, continues each cut turn with a note
  that the pause cut it, sends what each session held, and starts what
  waited.
- **A message you write to one session** resumes that session alone, with
  its cut turn. The others stay paused. An agent's message cannot resume one.

## Load

aegis measures every session's CPU and RAM every 5 seconds: what its agent
and everything the agent started use, test runs and builds included. The
CPU is in cores over the last minute, so 2.0 means two cores busy; the RAM
is the resident memory, in GB.

- The sidebar's Usage row shows a `load` line, `2.0 cores · 3.1 GB`.
- A Fleet card shows the same in its footer while the session holds at least
  one core or 1 GB, so an idle session's card stays quiet.
- `session_list` gives each session here its `load`, `{"cpu": 2.0,
  "rss_gb": 3.1}`, or null while it has no process, so an agent can see who
  loads the host before it starts a build.
- The memory and cpu notices name the reading session's own figure and the
  session that holds the most, when that one holds at least a core, or a
  tenth of the RAM.

With a cgroup per session (see the config page's Memory per session), the
kernel counts each session's processes. Without one, aegis follows each
process up to the agent that started it, and a process that left that tree,
such as a background test run whose shell ended, is counted by the
`AEGIS_SESSION` it inherited. Memory shared between processes is counted
once per process, so the figure ranks sessions; it does not add up to the
host's.

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
  session, repo or kind; commits, pull requests, closed issues, finished plan
  items and turns that changed files are recorded without anyone writing them
  down. A pull request merged with `gh pr merge --repo owner/name` is filed
  under the local checkout whose `origin` is that repo, and a merge closes the
  issues its `gh pr create --body` named with Closes, Fixes or Resolves #N.
  `journal_note` records a decision, a blocker or a milestone aegis cannot
  see. People open the Journal with Alt+L; its search box matches an entry's
  text, session, kind, paths and repo.
- `quota_read` returns how much of each subscription window is left, from the
  last reading, at no cost.
- `meta` returns the primer every agent gets at spawn and the list of aegis's
  tools.
