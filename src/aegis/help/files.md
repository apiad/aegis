---
title: Files
kind: howto
when: opening, reading or editing any file on the server in the browser with Alt+O or the folder tree, viewing rendered Markdown, HTML, images, PDF, audio and video, creating, renaming, moving or deleting files and folders, undoing a delete, and saving a file that changed on disk, and asking a session about selected lines or elements, or commenting on them
---

Files opens any file under the config root in the browser, so reading a file,
making a small edit or fixing two lines of Python never needs another editor.
It is for people, beside Journal and Settings; sessions keep their harness's
own file tools and cannot open or write through it.

## Opening a file

Press Alt+O anywhere, the Fleet and a session included, or the folder
button at the top. On a phone the header has no room for that button:
open Journal and tap Files at its top. A picker lists every file under the config root,
the most recently modified first, so what was written a minute ago is at
the top. Typing filters the list with the same fuzzy match as the command
menu, the newer file first on a tie. Type `path:line` to open the file at that
line.

A path a session's reply names opens in the viewer over the transcript, and so
does the file a Read, Write or Edit row used once you show it. When the file is
under the config root, the viewer's Open in Files button opens it here to edit,
at the line the reply named. A row shown before the page was reloaded gets the
button once you show its file again.

Enter opens the file as a new step in the browser's history, so Back
returns where you were. Shift+Enter opens it in a new
browser tab instead. Each file has its own address, `#files=<path>`, the path
under the config root, and `:line` after it if you like; it can be
bookmarked or shared with another tab.

The picker lists what git would track: inside a repository, the files git
lists (tracked and untracked, without the ignored ones); outside, everything
except a fixed list of build and cache folders such as node_modules, .venv
and __pycache__. A repository inside a gitignored folder, as in a workspace
that gitignores its repos folder, is listed with its own rules. The list is
read when the picker opens and refreshed in the background when it is more
than 30 seconds old.

## The folder tree

The left side of Files is the folder tree, starting at the config root. Each row
has a glyph: a folder, open or closed, or the file's kind: Markdown, code,
config, image, PDF, audio, video, archive, or a plain file. The Alt+O picker
shows the same glyph in front of each path. Click
a folder to open it; its contents are read the first time, and the folders you
leave open stay open the next time. A file opened from the picker or a
link opens the tree down to it. Ignored entries are hidden, by the same
rules as the picker, except a folder that holds a repository; "Show ignored"
at the foot of the tree shows them, dimmed. A .git folder and the server's
state folder never show.

Right-click a row, or hold it on a touch screen, for its menu:

- Insert path: writes the row's path, as `` `README.md` ``, into the
  message box of the session you last had open in this browser tab, where
  its cursor was, and takes you there. With no session open it starts the
  New session form with it.
- New session here: the New session form, with the row's folder (a file's
  own folder) as the session's directory.
- Terminal here: a shell terminal started in that folder.
- New file and New folder: type the name in the tree and press Enter, or Esc
  to cancel. A new file opens in the editor at once.
- Rename: edit the name in place. A name starting with `/` is a path from the
  config root, so `/docs/notes.md` moves the file there.
- Delete: asks once, saying how many files a folder holds. The entry goes to
  the trash, and the toast's Undo puts it back for 10 seconds, unless
  something new took its place.
- Copy path: the path under the config root.

Drag a row onto a folder, or below the last row for the root, to move it
there. Nothing overwrites a file or folder that exists: a name that is taken is
refused. The open file, when renamed or moved, stays open at its new path;
renaming, moving or deleting a file with unsaved changes asks first.

Deleted entries are kept in the trash folder of the server's state for 30
days, and emptied when the server starts after that. On a phone the tree is
a drawer behind ☰ in the top bar, and picking a file closes it.

## Editing and saving

A text file opens in the editor with line numbers, highlighting for about
thirty languages, indentation with Tab, search with Ctrl+F and undo. A dot
next to the path means the buffer has changes that are not saved.

Ctrl+S (⌘S on a Mac) or the Save button saves. The file keeps its line
endings and its permissions, and the write replaces the file in one step, so
nothing reading it sees half a file. Leaving Files, opening another file
or closing the tab with unsaved changes asks first: Save, Discard or Cancel.

A text file over 2 MB opens read-only, as its first 2 MB.

## Markdown, HTML, images and other formats

A file opens the way its kind is drawn when a session sends it:

- Markdown opens rendered, as replies are, with its tables and highlighted
  code. Its images with a relative path, such as `img/plot.png`, load from
  beside the file; a path that climbs out of the config root draws nothing.
  Its relative links open the linked file here.
- HTML opens in a sandboxed frame: its scripts run, but the page has no origin
  of its own, so it cannot reach aegis or your sign-in. Files it links to
  beside it (stylesheets, images) do not load.
- Images open fitted to the pane. A PDF opens in the browser's own viewer;
  audio and video in their players.
- Anything else shows its type and size, Download, and, when the browser runs
  on the server's desktop, a button that opens it in the desktop's app for it.

Markdown, HTML and SVG are text too: Rendered and Source at the top of the
pane switch between the page and the editor, and a save redraws the page.
Rendered Markdown shows the buffer as it is; HTML pages and SVGs show the
file as saved. Opened at a line, as `#files=<path>:<line>` does, such a file
starts in Source.

The bytes of images, pages and downloads come from a short-lived address
that reads the file again each time. It lapses after ten minutes without a use,
or when the server restarts; Download asks for a fresh one on each click.

## Pointing at lines and comments

Selecting text in a file shows a bar over it, in the Files editor, rendered
Markdown and HTML, and over a sent file opened from its card. A
right-click (not with Shift) or a long press on rendered Markdown, a line of a
sent file or an element of a page opens the same items for what is under it.
In the editor a right-click is the browser's own, with its paste, and so is
one on a field, a link or an image inside a page. Scrolling a page or
pressing anywhere in it closes its menu, and the menu takes no click in its
first half second.

- Ask writes the place and the quote into the message box, above your draft:
  `` `src/park.py:40-52` `` and the lines for code, the path and the nearest
  heading (`` `notes.md` § Setup ``) for Markdown, and the heading and the
  element's id or a short CSS path (`` `report.html` § Setup › #p1 ``) for a
  page. Ask aside does the same after `/btw`.
- New session opens the New session form with it as the prompt.
- Copy copies the text; with Shift, as a quote.
- Comment pins a number on the place and adds a line for your note to a tray
  at the bottom: `3 comments · Send · Discard`. Each line can be edited or
  removed. Send writes them all as one message into the message box, in the
  order you made them, and forgets them; nothing is sent until you send that
  message.

A sent file's quotes go to the session that sent it. A file opened in Files
goes to the session last shown in this browser tab; with none, New session
opens with it. Comments not sent yet stay in this browser's storage, so a
reload does not lose them; no other browser or the server sees them. They belong to
that version of the file: a sent file sent again, or a Files file changed on
disk, starts with none.

A page whose own Content-Security-Policy blocks scripts from the server works
as before, without the menu or the bar inside it.

## When the file changed on disk

Sessions write the files you edit. While Files is on screen it checks the
open file every two seconds. A file you have not edited takes the new version
by itself, keeping your line and scroll. If you have edits, the banner below
appears without waiting for a save. A file deleted on disk shows a banner with
Save, which writes your buffer back as a new file, and Close.

A save sends the version the buffer was
based on, and when the file on disk is no longer that version, nothing is written
and a banner says the file changed on disk. It offers three answers:

- Show diff: the disk's version against your buffer.
- Reload: drop your edits and take the disk's version.
- Keep mine: the next save overwrites the change on disk, on purpose.

There is no merge. The check is what keeps a session's edit from being lost
without anyone seeing it.

## Who may open what

Every person with a token here, admin or member, may read and write files
here. Each save is logged with the name of whoever made it, in
fs-log.jsonl in the server's state folder.

Three things are always refused. A session cannot call Files at all: it has
its harness's tools. A person acting through a link from another server gets
not_across_links, because a far server never reads or writes files here. And
no path may leave the config root, through `..` or a symlink (outside_root),
or reach the server's own state folder or a .git directory (forbidden): the
first holds the server's tokens and live stores, the second a repository's
internals.
