- **The TUI, the daemon, and everything only it used.** Slash commands, workflows
  and their DSL, schedules, canvases, shared terminals, execution hosts, groups,
  claims, reminders, recaps, voice input, the old web relay and the mkdocs site
  are not in 2.0 (see `legacy/`). Dropped dependencies: textual, lovelaice,
  croniter, watchdog, ptyprocess, agent-client-protocol, jsonschema,
  markdown-it-py, pathspec, tomli-w, rich, and the `voice` extra.
