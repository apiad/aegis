- **The F3 sidebar shows `⚡ N tok/s` again, along with four other figures it
  had been discarding.** Drawing its own CTX gauge made the CONTEXT section
  fall back to the narrowest metrics tier, which carries neither the
  generation speed nor the cached share, the reasoning share, the tool count
  or the compaction counter — so the panel that exists to spend the vertical
  axis on detail was showing less of it than the one-line status bar. It now
  reads the numbers off the metrics model instead of scavenging a rendered
  string, and puts them on two rows of their own: the per-turn measurements
  on one, the accumulating counters on the other. A narrow column sheds the
  shares and keeps the speed, which is the figure with no other surface here.
