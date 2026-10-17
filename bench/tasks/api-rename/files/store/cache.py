"""A per-connection read cache for the CLI's repeated lookups.

Wraps a reader from store.query by name, so a cached read and a direct one never
disagree about arguments.
"""

from functools import wraps

from . import query

_READERS = ("fetch_rows", "count_rows")


def cached(conn):
    """Return an object with the readers of store.query, memoized for this connection."""
    memo: dict = {}

    class Reader:
        pass

    reader = Reader()
    for name in _READERS:
        fn = getattr(query, name)

        @wraps(fn)
        def call(table, *args, _fn=fn, _name=name, **kwargs):
            key = (_name, table, repr(args), repr(sorted(kwargs.items())))
            if key not in memo:
                memo[key] = _fn(conn, table, *args, **kwargs)
            return memo[key]

        setattr(reader, name, call)
    return reader
