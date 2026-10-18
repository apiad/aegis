---
title: Files
kind: howto
when: opening, reading or editing any file on the server in the browser with Alt+O, and saving a file that changed on disk
---

Files opens any file under the config root in the browser, so reading a file,
making a small edit or fixing two lines of Python never needs another editor.
It is for people, beside Journal and Settings; sessions keep their harness's
own file tools and cannot open or write through it.

## Opening a file

Press Alt+O anywhere, the Fleet and a session included, or the folder
button at the top. A picker lists every file under the config root,
the most recently modified first, so what was written a minute ago is at
the top. Typing filters the list with the same fuzzy match as the command
menu, the newer file first on a tie. Type `path:line` to open the file at that
line.

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

## Editing and saving

A text file opens in the editor with line numbers, highlighting for about
thirty languages, indentation with Tab, search with Ctrl+F and undo. A dot
next to the path means the buffer has changes that are not saved.

Ctrl+S (⌘S on a Mac) or the Save button saves. The file keeps its line
endings and its permissions, and the write replaces the file in one step, so
nothing reading it sees half a file. Leaving Files, opening another file
or closing the tab with unsaved changes asks first: Save, Discard or Cancel.

A text file over 2 MB opens read-only, as its first 2 MB. A file that is not
text shows its type and size; its preview is not built yet.

## When the file changed on disk

Sessions write the files you edit. A save sends the version the buffer was
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
