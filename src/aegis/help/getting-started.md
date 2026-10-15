---
title: Getting started
kind: tutorial
when: the first hour with aegis, from one session to plans, files and pages, monitors, helpers and a queue of workers
---

This tutorial takes you from your first session to a queue of workers, in
five levels. Each level builds on the one before and opens by saying what it
starts and whether it spends quota. Every step says what to do and what you
should see. The examples use whatever project your session runs in.

You can take it with a guide: run `/tutorial` in any session, click the hint
under the new tab's chips, or press the button at the top of this page in
the Help tab. The guide opens in its own tab, asks before any step that starts
agents or spends quota, and answers questions on the way.

## Level 1. Talk to one agent

This level starts one agent and spends quota.

1. Start a session. Open the new-session page with the + at the end of the
   tab bar, or press Alt+N. The chips under the box name the agent, its
   harness, model, effort and permission. Type "List the files in this
   directory and tell me what this project is" and press Enter. You should
   see a new tab named after your message, the session's handle beside it,
   and the agent's work arriving in it.
2. Read the transcript. Each row is one thing the agent did. Click a tool
   row, or select it with j and k and press Enter, to see its details. Press
   Alt+Z to fold the tool calls and thinking, again to keep only the
   messages, and a third time to bring everything back. You should see the
   rows fold and unfold, and an unread dot on each agent message until you
   have read it.
3. Steer it while it works. Ask "Read the README and summarize each section",
   and while it works send "Also count the Markdown files". Claude Code reads
   a message sent during a turn at its next tool call. Esc interrupts a turn.
   You should see one answer that covers both requests.
4. Stop and continue. Open the session panel with Alt+B and press Stop, or
   type `/stop`. Then send "What did you find?". You should see the session
   start again and answer from what it already did.

## Level 2. Know what it needs

This level spends quota in the session from level 1.

1. Ask for a plan: "Make a three-step plan to add a short README section,
   keep it with plan_update, and ask me before you write anything." You should
   see the plan in the session panel and on its card, one item marked doing at
   a time.
2. Answer it. When the agent stops to ask, its mark becomes needs you and up
   to three reply buttons appear under its last message. Click one or type
   your own answer; say no if the project should stay untouched. You should
   see the agent continue from your answer.
3. Open the Fleet with Alt+0, or the first tab. Every open session is a card:
   its handle and title, the mark of what it needs, its agent and model, its
   plan, its context and its cost. Order it "Needs you first". You should see
   a session that needs you rise to the top, and above the cards the band with
   this server's sessions and the Gorgoneion, aegis's mark.
4. Rename the session. Click its handle at the top of the session panel, or
   type `/rename readme-plan`. You should see the tab and the card take the
   new handle.

## Level 3. Files and pages

This level spends quota in the same session.

1. Ask "Send me this project's README with file_send." You should see a card
   in the transcript. Open shows the file over the transcript, rendered;
   Esc returns.
2. Ask "Use an artifact to ask me which of three titles I prefer for the new
   README section." You should see a small page in the transcript. Pick a
   title: the agent wakes on a row headed artifact: with your choice.
3. Ask "Send the same page again, docked on the right." You should see the
   page stay beside the transcript while the transcript scrolls. Drag the
   line between them to resize it, and press × on the pane's bar to undock
   it back to its card.

## Level 4. Waiting and helpers

This level starts a second agent and spends quota.

1. Wait with a monitor. Ask "Use an aegis monitor to wait until the file
   ready.txt exists in this directory, with a progress command, then end your
   turn and tell me when you are woken." When its turn has ended, run
   `touch ready.txt` in that directory. You should see a monitor row with a
   bar in the session panel, and within ten seconds a wake row headed
   monitor: followed by the agent's answer.
2. Start a helper. Ask "Start a second session from the default agent with
   session_spawn, ask it to count the lines of the README, and wait for it
   with monitor_sessions. Then tell me what it found." You should see a second
   card appear on the Fleet, work and finish, and the first session wake and
   report the count. The helper's first message is the prompt the first agent
   wrote.
3. Hand it a note. Ask "Use peer_handoff to ask the helper which file here is
   the longest." You should see a message in the helper's tab headed
   agent: with the first session's handle, and the helper answer it.
4. Close and reopen. Type `/close` in the helper's box and confirm if it
   asks. Its tab goes away. In the Fleet, scroll to the archive under the
   cards and filter it by the helper's handle. You should see it there: Read
   opens its transcript without starting anything, and Reopen gives it a tab
   again, stopped until your next message.

## Level 5. Queues

This level starts up to three workers and spends quota.

1. Enqueue. Ask "Enqueue three tasks on the general queue with
   queue_enqueue: each counts the lines of one file in this directory and
   ends on the number." You should see worker cards appear in the Fleet's
   last group, dimmed, marked zz and marked as workers of their queue: a
   worker starts quiet, with no tab. If the agent reports that there is no
   such queue, this server has none configured: whoever runs it adds one as
   the configuration page shows, and you run this step again.
2. Wait for the results. You should see the first session wake once per task
   on a row headed queue:general with the task's id and ok. The workers
   are archived as they finish; the archive's Queue workers chip shows them.
3. Check a task. Ask "Show me the status of the first task with
   task_status." You should see its status and its result.

## Find help

Click `?` at the right end of the tab bar. The Help tab lists every page,
tutorials first, then how-to pages, then concepts, each with a line saying
when it helps. Type in its search box: the results are the pages an agent
gets from `help_search` for the same words, best first, and Enter opens the
first one. In a session, `/help` followed by a few words, such as
`/help wait for CI`, opens the tab with those words searched.

On a phone the list and the page are two screens. Tap a page to read it; the
back arrow, or the phone's own Back, returns to the list.

## Next

Each of these is a tutorial of its own, for after this one:

- [Layouts and the canvas](tutorial-layouts): several panes beside the
  transcript, and a second browser window.
- [Linked servers](tutorial-links): sessions on another aegis server, from
  this one.
- [Phone: install, offline, push](tutorial-phone): aegis as an app on a
  phone.
- [Running a server for others](tutorial-server): tokens, quota lines and
  running aegis as a service.
