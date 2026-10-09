- **Agents see other sessions and wait on them.** `session_list` now carries
  each session's attention and its line, what it waits on, its plan with what
  it is doing now, its model and its spend. The new `monitor_sessions` tool
  waits until a list of sessions has finished and wakes the agent `ok`, or
  `blocked` as soon as one needs you, so "when the une-tools sessions finish,
  cut a release" works without polling. Its card in the sidebar reads
  "1 of 2" and lists each session (#234).
