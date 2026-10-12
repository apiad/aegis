---
title: Reading a transcript
kind: concept
when: reading what an agent did, its tool rows and replies, the files it sent and the paths it named
---

A session's transcript is everything that happened in it, one row per thing:
your messages, the agent's replies and thinking, a row for each tool call with
its result, the messages other sessions and monitors sent it, and the files
and pages it sent you. Select a row with j and k, or the arrow keys.

## Tool rows

A tool row starts folded. Click its line, or select it and press Enter, to
open it, and nothing more is downloaded until you do. Open, it shows code the
way an editor does:

- A file the agent read shows its last 40 lines, numbered and coloured by the
  file's language. A file it wrote shows the same way, and Show the file opens
  the whole file as it is now.
- A shell command is coloured.
- An edit shows the old and new lines side by side when the transcript is at
  least 680 px wide, and as one compact column when it is narrower, with the
  changed words marked. For Claude Code the line numbers are the file's own.

## Replies

Replies and your own messages are Markdown. A code block shows its language
and is coloured in the theme's own palette. A wide table scrolls sideways with
its first column pinned, and in a narrow transcript each row becomes a small
card. Task lists show checkboxes.

Each code block and each table has a copy button, shown when you hover it or
select its row. A table pastes as Markdown into a chat and as cells into a
spreadsheet. The c key copies the selected reply's Markdown.

## Files and the viewer

A file an agent sends with `file_send`, and a page it sends with
`artifact_send`, arrive as a card in the transcript. Open shows it inside
aegis, over the transcript and the message box, and leaves the tabs and the
session panel usable. Markdown renders in the theme, source shows with line
numbers and syntax colours, PDFs, HTML and images fill the view, and media
plays. A live page runs there and answers the agent from there.

Esc or the close button returns to the transcript, and the viewer's bar opens
the file in a new browser tab. A file card's Open, new tab and Download are
icon buttons, with Open natively beside them in a browser on the server's own
desktop.

## Paths in replies

A file path an agent quotes in backticks in a reply, such as
src/aegis/app.py:142, opens the file: click it and the viewer shows that file scrolled to line 142, with the
line marked. Agents are asked to quote files relative to the folder aegis
serves from, so the link finds them. A path that is not there says where it
looked, and one that names two different files, one in that folder and one in
the session's own, asks which to open.
