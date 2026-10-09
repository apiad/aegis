- **`aegis import-legacy` brings the sessions of aegis before 2.0 into the
  archive.** Since 2.0 the legacy state directory was moved aside and read by
  nothing, so every older conversation was out of reach; on zion that was 1,665
  logs. The command rewrites each one as an archived session that the archive
  lists and a tab reads, skips sessions with no content, and skips what it
  already imported when run again. Reopening one resumes its Claude session only
  while Claude Code still keeps it.
