- **Alt+O opens any file on the server, and the Files view edits it.** From
  anywhere in aegis, Alt+O lists every file under the config root, newest
  first, filtered as you type; `path:line` opens at a line. The file opens at
  its own address, `#files=<path>`, in an editor with highlighting for about
  thirty languages, and Ctrl+S saves it, keeping its line endings and
  permissions. A save checks that the file on disk is still the version it
  was based on: when a session changed it meanwhile, nothing is written and
  a banner offers the diff, the disk's version, or your buffer on purpose.
  Admins and members may write; sessions and linked servers may not, and no
  path reaches outside the root, the server's state folder or a .git
  directory. Every save is logged with its author in `fs-log.jsonl`.
- **The Files view has a folder tree.** Folders open in place from the
  config root, ignored entries hidden unless asked for; right-click or a long
  press creates, renames, deletes or copies a path, and a row dragged onto a
  folder moves there. A delete goes to a trash in the server's state with Undo
  in a toast, kept 30 days. The open file follows the disk: an untouched
  buffer takes a session's write by itself, an edited one shows the banner.
  On a phone the tree is a drawer behind ☰.

help: files
