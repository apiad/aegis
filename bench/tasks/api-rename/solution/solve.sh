#!/bin/bash
# Oracle: move the reader and its helpers to store/records.py, leave a warning alias
# in store/query.py, then rename every other use, test and doc.
set -euo pipefail
cd "$1"
python3 - <<'PY'
import pathlib, re

q = pathlib.Path("store/query.py").read_text()
head, _, rest = q.partition("def count_rows")
records = head.replace("fetch_rows", "load_records")
records = records.replace('"""Reading rows. load_records is', '"""Reading rows. load_records is')
pathlib.Path("store/records.py").write_text(records)
pathlib.Path("store/query.py").write_text('''"""count_rows, and fetch_rows: the old name of store.records.load_records."""

import sqlite3
import warnings

from .records import OPERATORS, QueryError, _check_table, _where_clause, load_records

__all__ = ["OPERATORS", "QueryError", "count_rows", "fetch_rows", "load_records"]


def fetch_rows(*args, **kwargs) -> list[dict]:
    """Deprecated: use store.records.load_records."""
    warnings.warn(
        "store.query.fetch_rows is deprecated; use store.records.load_records",
        DeprecationWarning,
        stacklevel=2,
    )
    return load_records(*args, **kwargs)


def count_rows''' + rest)

for p in pathlib.Path("store").glob("*.py"):
    if p.name in ("query.py", "records.py"):
        continue
    s = p.read_text()
    s = s.replace("from .query import fetch_rows as _fetch", "from .records import load_records as _fetch")
    s = s.replace("from .query import count_rows, fetch_rows", "from .query import count_rows\nfrom .records import load_records")
    s = s.replace("from .query import fetch_rows", "from .records import load_records")
    s = s.replace("from . import query\n", "from . import query, records\n")
    s = s.replace("query.fetch_rows(", "records.load_records(")
    s = s.replace("fn = getattr(query, name)", "fn = getattr(records if name == \"load_records\" else query, name)")
    s = s.replace("store.query.", "store.query and store.records.").replace("fetch_rows", "load_records")
    p.write_text(s)

for p in list(pathlib.Path("tests").glob("*.py")) + list(pathlib.Path("docs").glob("*.md")) + [pathlib.Path("README.md")]:
    s = p.read_text()
    s = s.replace("from store.query import QueryError, count_rows, fetch_rows",
                  "from store.query import QueryError, count_rows\nfrom store.records import load_records")
    s = s.replace("from store.query import fetch_rows", "from store.records import load_records")
    s = s.replace("store.query.fetch_rows", "store.records.load_records").replace("fetch_rows", "load_records")
    p.write_text(s)

c = pathlib.Path("CHANGELOG.md")
c.write_text(c.read_text().replace("# Changelog\n", "# Changelog\n\n## Unreleased\n\n- `fetch_rows` is now `load_records`, in `store.records`. `store.query.fetch_rows`\n  still works and emits a `DeprecationWarning`.\n", 1))
PY
