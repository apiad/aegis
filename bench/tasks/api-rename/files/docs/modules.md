# Modules

- `store.db`: `connect()` and the schema.
- `store.query`: `fetch_rows` and `count_rows`, the package's only readers.
- `store.models`: `Book`, `Member`, `Loan`, each built from a `fetch_rows` dict.
- `store.books`, `store.members`, `store.loans`: lookups per table.
- `store.search`: title and author search.
- `store.reports`, `store.stats`: aggregates.
- `store.export`, `store.importer`: CSV out and in.
- `store.validate`: integrity checks.
- `store.cache`: a per-connection cache over the `store.query` readers.
- `store.cli`: the command line.
