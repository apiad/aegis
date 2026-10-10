- **aegis keeps a journal of what was done, and agents and people can search it.**
  Commits, pull requests, plan items turning done and turns that changed files are
  recorded from the transcripts without anyone writing them down. Agents ask with
  `journal_search` (by day, words, a path, a session) and add decisions and
  blockers with `journal_note`; people use the Journal view (Alt+L), the sidebar's
  Journal row, or `aegis journal search` and `aegis journal rebuild` in a shell. A
  closed session's transcript now ends with a "closed" row.
