- **`aegis map --exclude` leaves out folders, and a cold index is twice as
  fast.** A tracked `legacy/` folder was two thirds of aegis's own index and
  joined every name match; `-x legacy` (a path or a glob, repeatable) keeps it
  out of the views without re-parsing anything. The first index parses in a
  pool of processes with tree-sitter queries instead of a walk over every
  node: on aegis's 1,370 files, measured side by side on a busy host, about
  18 s down to 7 to 13 s, and 7 s with `-x legacy`.
help: code-map#the-index
