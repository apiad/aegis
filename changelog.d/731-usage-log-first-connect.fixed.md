- **The knowledge usage log no longer drops a row when its file is busy at the
  start.** Its writer switches the file to WAL on its first connect, and SQLite
  answers that switch "locked" at once instead of waiting out the busy timeout.
  A hint, read or run logged while another connection held the file was lost.
  The first connect now waits for the lock, up to the same 1 s as any write.
