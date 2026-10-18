"""``aegis map``: a tree-sitter map of a git repository, for agents.

It answers what grep cannot say it answered completely: the repository's
layout with each module's first doc line, a file's outline, and every caller
of a symbol and everything that depends on it.

The rules, with their reasons:

- **Heuristics, no language server.** tree-sitter parses any of the pack's
  languages in milliseconds and needs nothing installed per language. The
  pack's tags queries give definitions and calls for the languages that have
  them; any other language falls back to nodes whose type reads like a
  definition (parse.py).
- **Every edge says how it was found.** An edge is *sure* when the name is
  defined in the same file, or when the using file's imports name both the
  target's module and the name. Anything else is a name match, printed with
  ``?``. Measured against jedi on aegis itself, sure edges were right 96% of
  the time and both kinds together found 97% of real references; the ``?``
  edges were mostly common method names, so ``impact`` expands through sure
  edges only (index.py).
- **One index per repository, shared, refreshed by mtime.** Every session of
  the user reads the same cache file, and each query re-parses only the files
  whose mtime or size changed, so a warm query costs a stat per file. The file
  is replaced atomically, so two sessions writing it at once lose a refresh,
  never the index.
- **A CLI, not MCP tools.** Every MCP tool's schema rides on every request of
  every session; a subcommand costs nothing until an agent runs it.
"""
