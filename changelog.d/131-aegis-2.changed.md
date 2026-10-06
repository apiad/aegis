- **aegis is now the web-native workplace; the terminal app is gone.** `aegis
  serve` runs Claude Code sessions and serves them to browser tabs, with monitors,
  queues and handoffs for agents over MCP (built as `aegis2` in slices 1 to 3).
  The TUI-era tree moved to `legacy/` as reference and is no longer installed;
  `pip install "aegis-harness<2"` still gets it. State lives in `.aegis2/state/`,
  next to the old `.aegis/state/`, which nothing reads.
