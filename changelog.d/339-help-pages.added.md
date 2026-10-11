- **Agents can look up how to use aegis.** aegis now ships help pages, written
  for the installed version, on its sessions, monitors, queues, handoffs,
  artifacts, links, configuration and commands, with a getting-started tour.
  Every agent gets `help_search` and `help_read` to find and read them, and its
  primer tells it to search before answering how to do something in aegis or
  using a feature it has not used in the session. The pages are checked
  against the code in both directions, in CI too: a tool, command, CLI
  subcommand or config key no page names fails, and so does a page naming one
  that does not exist.
