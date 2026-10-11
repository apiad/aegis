---
title: Getting started
kind: tutorial
when: the first hour with aegis, from a first session to a handoff and the archive
---

This tour walks through aegis in one sitting: a session and its transcript,
the Fleet, the plan and the marks that say what needs you, a monitor, a second
session and a handoff, and the archive. Each step says what to do and what you
should see. Steps 1, 5 and 6 start agents and spend quota.

## 1. Start a session

This step starts an agent and spends quota.

Open a new tab with the + at the end of the tab bar, or press Alt+N. Check the
chips under the box: they name the agent and its harness, model, effort and
permission. Type "List the files in this directory and tell me what this
project is" and press Enter.

You should see a new tab named after the session's handle, and the agent's
work arriving in it.

## 2. Read the transcript

Each row is one thing the agent did. A tool call is one line with its result;
click it, or select it with j and k and press Enter, to see the details. The
agent's messages are the rows in prose. Press Alt+Z to fold the tool calls
and thinking, again to keep only the messages, and a third time to bring
everything back.

You should see the rows fold and unfold, and an unread dot on each agent
message until you have read it.

## 3. Open the Fleet

Click Fleet, the first tab, or press Alt+0. Every open session is a card: its
handle and title, the mark of what it needs, its agent and model, its plan and
what it is doing now, its context and its cost.

You should see your session's card. When its turn ends, its mark turns to
done, review or needs you. With the Fleet ordered "Needs you first", a session
that needs you rises to the top.

## 4. Watch the plan and the marks

Ask the agent in its tab: "Make a three-step plan to add a short README
section, keep it with plan_update, and ask me before you write anything."

You should see the plan appear in the session panel (Alt+B opens it) and on
the card, one item marked doing at a time. When the agent stops to ask, its
mark becomes needs you and up to three reply buttons appear under its last
message. Click one, or type your own answer.

## 5. Wait with a monitor

This step spends quota.

Ask the agent: "Use an aegis monitor to wait until the file ready.txt exists
in this directory, with a progress command, then end your turn and tell me
when you are woken." When its turn has ended, create the file from a shell
with `touch ready.txt` in that directory.

You should see a monitor row with a bar in the session panel while it waits,
and a few seconds after you create the file, a wake row headed
"from monitor" followed by the agent's answer.

## 6. Start a second session and hand off

This step starts a second agent and spends quota.

Ask the first agent: "Start a second session from the default agent with
session_spawn, ask it to count the lines of the README, and wait for it with
monitor_sessions. Then tell me what it found." Watch the Fleet.

You should see a second card appear, work and finish, and the first session
wake and report the count. Open the second session's tab: its first message is
the prompt the first agent wrote. To pass work between two sessions yourself,
an agent's `peer_handoff` delivers a message headed with the sender's handle.

## 7. Close and reopen

Close the second session: type `/close` in its box. Its tab goes away. Open
the Fleet and scroll to the archive under the cards; filter it by the
session's handle.

You should see the session in the archive. Read opens its transcript without
starting anything; Reopen gives it a tab again, stopped until your next
message.

## Next

The concept pages say more about each part: sessions, monitors, queues,
handoffs, artifacts, links, configuration and commands. An agent finds the
right one with `help_search` and reads it with `help_read`.
