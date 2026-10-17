`logs/` holds a web server's rotated access logs: `access.log` is the newest, and some of the older files are gzipped. Write `report.json` at the root of this folder, a JSON object with these keys:

- `requests`: the number of well-formed lines across all the files.
- `malformed`: the number of non-blank lines that are not well-formed. A line counts as well-formed only when every field parses, the latency included.
- `per_day`: well-formed requests per UTC calendar day, as `{"YYYY-MM-DD": count}`.
- `endpoints`: for each endpoint, `{"requests": n, "p95_ms": p}`. An endpoint is the method, a space, and the path without its query string, with every path segment made only of digits replaced by `:id`, for example `GET /api/orders/:id`. The latency is the `rt=` field, in seconds. `p95_ms` is the nearest-rank 95th percentile in milliseconds, rounded to an integer: sort the endpoint's latencies ascending and take the one at 1-based position ceil(0.95 × n).
- `top_5xx`: the five most frequent status codes from 500 to 599, as `[{"status": 503, "count": 120}, ...]`, most frequent first, ties broken by the lower status code.
