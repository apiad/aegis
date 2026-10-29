- **A `session_spawn` prompt that starts with an aegis command runs it.**
  `prompt: "/feedback <words>"` reached the harness as text, and Claude Code
  answered with its own `/feedback`. A `/` first message now runs as if typed
  in the new session's box, as the new tab already did.
