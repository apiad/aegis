- **A read or write Claude Code session can call aegis's own tools.** Claude
  Code refused every aegis tool in plan mode, which `read` maps to, and in
  acceptEdits, which `write` maps to, and on Haiku in `auto`, so those
  sessions could not end a turn with `turn_end`, keep a plan, or answer a
  nudge. aegis now lets its own tools through in every mode and decides itself
  what a read session may call: its own card (`turn_end`, `plan_update`,
  `session_rename`), reading (`session_list`, `peer_read` and the status
  tools), waiting on other sessions, and `file_send`. A read session is
  refused `monitor_start`, which runs a shell command, the artifact tools, and
  anything that acts on another session: spawn, close, handoff and enqueue.
  The same rule now holds for OpenCode and Codex, whose read sessions could
  call every aegis tool, and it follows a live `/permission` change (#305).
