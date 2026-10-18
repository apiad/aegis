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

help: files
