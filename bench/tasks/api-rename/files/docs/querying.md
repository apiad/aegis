# Querying

Every read in the package goes through `store.query.fetch_rows`:

```python
from store.db import connect
from store.query import fetch_rows

conn = connect("library.db")
fetch_rows(conn, "books", where={"author": "Ana Ruiz"}, order_by="-year", limit=3)
```

`fetch_rows(conn, table, where=None, order_by=None, limit=None)` returns a list
of dicts. `table` is one of `books`, `members`, `loans`.

## where

A dict from column to value means equality: `{"author": "Ana Ruiz"}`. A pair
picks the operator: `{"year": (">=", 2000)}`, `{"title": ("LIKE", "%sea%")}`.
`None` compares with SQL NULL: `{"returned": None}` is every loan still out.

## order_by and limit

`order_by="year"` sorts ascending, `order_by="-year"` descending. `limit` caps
the number of rows.

## Counting

`count_rows(conn, table, where=None)` takes the same `where` as `fetch_rows`
and returns an int without building the rows.

## Errors

A table outside the three, a column that is not an identifier or an unknown
operator raises `store.query.QueryError`, whose message starts with
`fetch_rows:`.
