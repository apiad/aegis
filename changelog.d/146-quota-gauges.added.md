- **The quota and host gauges are back.** The Fleet view opens with a band:
  this server's sessions by state; CPU, RAM, disk and average context; and one
  gauge per window of every subscription aegis holds credentials for (Claude's
  5 hours and week, OpenCode Go's 5 hours, week and month). A tick on each bar
  marks how much of the window is gone, so fill past it means spending faster
  than the window refills; the projection at reset prints from 80%. The session
  sidebar gets a Quota section with Claude's two windows. Agents read the same
  numbers with `quota_read`, which never asks the vendor. Readings are shared
  with every aegis on the machine through `~/.cache/aegis/quota/`.
