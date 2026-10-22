- **Cursor Agent is a fourth harness: `harness: cursor`.** aegis could run
  Claude Code, OpenCode and Codex only, so a Cursor workspace had to be driven
  in a terminal beside the rest. A cursor agent now runs through
  `cursor-agent acp` with its own model catalog, its model, effort and
  permission applied while it runs, the aegis MCP endpoint handed to each
  session through `session/new` (each with its own token), permission requests
  answered by the configured level and never "allow always", streamed text,
  tools and the harness's own generated title in the transcript, prompts typed
  mid-turn queued instead of cancelling the run, and an interrupt that closes
  the turn and resumes the conversation with `session/load`. `aegis doctor`
  probes its version and catalog like the other harnesses, and an untested
  version is named with the command that installs a tested one. Cursor reports
  no cost or token counts through ACP, so those sessions show neither.

help: config#add-an-agent
