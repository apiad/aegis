- **`aegis map` maps a repository's code for agents.** `aegis map repo` shows
  the folders and files with each module's first doc line, `outline` a file's
  definitions, and `callers`, `callees` and `impact` every use of a
  definition, marking with `?` those matched by name only. It parses with
  tree-sitter, so it works in Python, TypeScript, Go, Rust, Java, C and the
  other languages the grammar pack has, with no language server. The first run
  in a repository indexes it in seconds; later ones re-parse only changed
  files, and every session of the user shares the index. The agent primer
  points agents at it before they grep.

help: code-map
