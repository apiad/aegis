- **`aegis usage`, `aegis usage repo` and `aegis usage repos` are back.** 2.0
  dropped them with the legacy tree, so `/cost-report` ran a pinned 0.42.0 that
  cannot read aegis 2's store. They now read aegis's own transcripts, imported
  legacy sessions included, plus `~/.claude/projects` and any `--extra-root`
  (gzipped or not), with the old attribution bands, coverage check and JSON.
  Prices come from a new table copied from Anthropic's pricing page: the old one
  charged 1-hour cache writes a quarter too much and had no Opus 5.5 or Fable
  rates. OpenCode work is reported as unpriced rather than left out.
