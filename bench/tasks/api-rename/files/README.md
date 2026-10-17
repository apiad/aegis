# store

A small library catalogue on SQLite: books, members and loans, with a CLI.

```bash
python3 -m store.cli library.db books --author "Ana Ruiz"
python3 -m store.cli library.db overdue
```

Reading goes through one function, `store.query.fetch_rows`; see
[docs/querying.md](docs/querying.md). The CLI is in [docs/cli.md](docs/cli.md).
