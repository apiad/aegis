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
  file's language. A file it wrote shows the same way. Show the file, on a
  row that read, wrote or edited a file, adds a card with the whole file as it
  is now; Open on the card shows it.
- A shell command is coloured.
- An edit shows the old and new lines side by side when the transcript is at
  least 680 px wide, and as one compact column when it is narrower, with the
  changed words marked. For Claude Code the line numbers are the file's own.

## Folding

The fold button in the bar above the message box, z in a session, or Alt+Z
steps through three levels. The first shows everything. The second folds each
stretch of tool calls, thinking and notes between two messages into one line
that says what it holds; click the line to open it. The third leaves only your
messages and the agent's replies. The latest recap stays shown at every level;
older recaps fold at the third. The browser remembers the level.

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

## The canvas

A page or a file can stay on screen beside the transcript instead of
scrolling away with its card. Dock, on its card or on the viewer's bar, opens
it in a pane to the right of the transcript. The card keeps one line,
docked · show, and Show outlines the pane; Open on a docked card outlines it
too. The panes and the transcript make up the session's canvas, columns of
stacked panes:

- Drag a pane by its bar, or the transcript by the thin strip along its top
  edge. Over another pane, its left or right fifth makes a new column on that
  side, its top or bottom third stacks above or below it, and its middle
  swaps the two.
- Drag the grip on the line between two columns, or two stacked panes, to
  resize them. A column stays at least 280 px wide, the transcript's at least
  360 px, and a pane at least 120 px high.
- × on a pane's bar undocks it, and the card shows it again.

When the window is too narrow for its columns, the canvas draws as tabs, as on
a phone: a strip of tabs over the open tab's pane. That happens when the
columns' minimums do not fit side by side, or when the transcript's column
would be under its 360 px. The columns are kept, and widening the window
brings them back as they were.

A canvas holds 4 columns and 3 panes in a column. Once there are four
columns, Dock stacks under the last pane, and on a full canvas it says so and
does nothing. A docked file shows its newest version: when the agent sends
the same file again, the pane updates where it is and the older card says
replaced by v2. A page leaves the canvas when it is answered or closed.

The canvas is kept with the session. It survives a reload and a restart of
aegis, and every browser on the session shows the same one. Only someone who
may change the session can rearrange it. The agent can dock pages and files
and arrange the canvas too.

## Paths in replies

A file path an agent quotes in backticks in a reply, such as
src/aegis/app.py:142, opens the file: click it and the viewer shows that file scrolled to line 142, with the
line marked. Agents are asked to quote files relative to the folder aegis
serves from, so the link finds them. A path that is not there says where it
looked, and one that names two different files, one in that folder and one in
the session's own, asks which to open.
