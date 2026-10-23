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

A side note you asked with `/btw` is a block with the question on top and the
answer below, with the model, how long it took, what it cost and how much
of the transcript it read. It never folds into those lines. Asking another folds the
earlier ones to a line each; click a block's question to fold or open it in
this browser. While it runs, its cancel link stops it.

## Replies

Replies and your own messages are Markdown. A code block shows its language
and is coloured in the theme's own palette. A wide table scrolls sideways with
its first column pinned, and in a narrow transcript each row becomes a small
card. Task lists show checkboxes.

Each code block and each table has a copy button, shown when you hover it or
select its row. A table pastes as Markdown into a chat and as cells into a
spreadsheet. The c key copies the selected reply's Markdown.

## Read replies aloud

Every agent reply has a speaker button in its right gutter, under the read
mark, shown when you hover the reply and always on a touch screen. Press it to
hear that reply and the replies after it in the same turn, up to your next
message; press it again to stop. Tool calls, thinking and your own messages are
not read. A code block is read as "code block" and a table as "table", a file
path as its file name, and a link as its text. Each reply is read in the
language it is written in, when this device has a voice for it.

The speaker button in the message box, beside the microphone, reads each new
reply of the open session aloud as it arrives, until you press it again. Replies
already on screen are not read, and switching to another session stops the
voice. Which sessions it is on for is kept in this browser tab.

Speech is made in your browser, so no reply text leaves the device. It uses the
browser's own voice unless you choose Phonon in Settings, under This browser.
Phonon is a neural voice that sounds much better, for English replies only:
choosing it downloads 65 MB once and measures how fast this device speaks with
it. Below 1.1 times real time it would stutter, so the browser's voice stays,
and the row offers to use Phonon anyway. Other languages always use the
browser's voice.

## Quoting and row menus

Select text inside one row, a reply, your own message or a tool's output,
and a bar appears over it (under it on a touch screen, icons only):

| Item | What it does |
|---|---|
| Ask | Puts the selection in the message box as a quote, each line starting with `> `, above what you had typed. |
| Ask aside | The same quote after `/btw`, so it goes as a side question. |
| New session | Opens the New session form with this session's agent, model and folder, and `From <handle>:` and the quote in its message. |
| Copy | Copies the text without the interface around it (a code block's language, copy buttons). With Shift, as a quote. |

On a touch screen, a long press on a word selects it and shows the bar in
place of the row's menu. A selection that runs into a second row gets no bar. A scroll of your own
closes the bar; the transcript's own scroll, as a reply streams in, moves it
along.

A right-click on a row, a long press on a touch screen, or the ContextMenu key
or Shift+F10 on the selected row opens its menu:

- a reply: Reply (quotes the whole reply), New session, Copy, Copy link;
- your message: Edit and resend (puts it back in the message box), New
  session, Copy, Copy link;
- a tool row: Open file (when it read or wrote one), Copy output, Reply, Copy
  link.

A right-click inside a selection puts the bar's items on top. Copy link copies
an address that opens the session at that row, selected and in view.

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
- When the canvas draws as tabs, a right-click on a tab other than the
  transcript's, or a long press, offers Undock and Move to window…, which
  lists the windows as the pane bar's button does.
- A right-click on a sent card's bar, or a long press, opens its menu: Dock
  right, Dock left, Open in new window and Download, each where the card's
  own buttons allow it. A docked card offers only the new window, which
  moves its pane there.

A column whose share of the window is under its minimum is drawn at the
minimum, and the wider columns give up the difference; the shares kept with
the session do not change. When the minimums do not fit side by side, the
canvas draws as tabs, as on a phone: a strip of tabs over the open tab's pane.
The columns are kept, and widening the window brings them back as they were.
While another tab is open the transcript stays as it was: a reply that lands
behind it stays unread and dots the transcript's tab, and the transcript's
keys wait, except Ctrl+F, which opens its tab with the find bar.

A canvas holds 4 columns and 3 panes in a column. Once there are four
columns, Dock stacks under the last pane, and on a full canvas it says so and
does nothing. A docked file shows its newest version: when the agent sends
the same file again, the pane updates where it is and the older card says
replaced by v2. A page leaves the canvas when it is answered or closed.

A workflow's card is a canvas item too: it opens full screen, docks and
moves to another window as a page and a file do.

The canvas is kept with the session. It survives a reload and a restart of
aegis, and every browser on the session shows the same one. Only someone who
may change the session can rearrange it. The agent can dock pages, files and
workflows and arrange the canvas too.

## A canvas across browser windows

A canvas can spread over more than one browser window, for a second screen.
Window 1 is the session's tab, with the transcript. Each other window is a
browser tab of its own that shows only its panes, with no tabs, panel or
transcript. Its title is the session's name and the window's number, as in
report-layout · window 2, so the browser's tab list tells the windows apart.
A page in it is as live as a docked one: what you do in it reaches the
agent, and the agent's updates reach it.

- Pop out, on a live page's card, its pane's bar or the viewer's bar, moves
  the page to a new window and opens that window's tab. It takes the place of
  Open in a new tab, which showed the page with nothing behind it; an answered
  or closed page, and a file, keep Open in a new tab.
- Move to window, the button with two arrows on a pane's bar, lists the
  other windows in use and New window. Choosing one moves the pane there and
  brings that window's tab to the front, or opens it.
- The card of an item in another window says in window 2 · open. Open
  brings that window's tab to the front, or opens it if this browser has
  none.
- Window 1 shows a chip at the top of its canvas for each other window,
  such as window 2 · 1 pane, with Open and Bring back. Bring back moves the
  window's panes into window 1, each of its columns as a new column on the
  right, and the window goes. Once window 1 has four columns the rest stack
  under its last pane; a window that does not fit is not moved at all, and
  a message says why.

Closing a window's tab changes nothing: its panes stay where they were, and
the chip in window 1 is how you get them back. The chip shows even when
another browser has that window open, since this one cannot tell. A window
tab whose panes have all gone, brought back, undocked or answered, says This
window is empty. A canvas has at most 4 windows. A window tab stays on its
window: the keys that open another tab, the Fleet or a new session do nothing
in it, and a push for its session still notifies you.

## Paths in replies

A file path an agent quotes in backticks in a reply, such as
src/aegis/app.py:142, opens the file: click it and the viewer shows that file scrolled to line 142, with the
line marked. Agents are asked to quote files relative to the folder aegis
serves from, so the link finds them. A path that is not there says where it
looked, and one that names two different files, one in that folder and one in
the session's own, asks which to open.

Each path shows what it names when its reply comes into view: a file icon
when the file is there, a folder icon for a folder, and a struck-out red name
when nothing is there. A missing file still answers a click, in case it has
appeared since. A GitHub repo in backticks, such as `octo/aegis` or
`octo/aegis#358`, links to GitHub with its mark, unless a local folder has that
name. A bare link shows its site's icon and a short name: a GitHub pull
request reads `octo/aegis#358`, a PyPI page the package's name.
