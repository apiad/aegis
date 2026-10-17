# The CLI

`python3 -m store.cli DB COMMAND`, where `DB` is the SQLite file.

| command | prints |
|---|---|
| `books [--author A]` | every book, by title |
| `search TERM` | books whose title, then author, contains TERM |
| `overdue` | loans past due today |
| `popular [--top N]` | the most borrowed titles |
| `stats` | rows per table, copies, loans out |
| `check` | integrity problems; exits 1 when there are any |
| `export TABLE` | the table as CSV |
| `rows TABLE` | raw rows, as `fetch_rows` returns them |
