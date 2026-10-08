- **A Bash row's verdict is the call's own last line again, not Claude Code's
  note.** Claude Code appends "Shell cwd was reset to …" after any call that
  changed directory and "[This command modified …]" after one that rewrote a
  file it had read. Either replaced the verdict; aegis now skips both.
