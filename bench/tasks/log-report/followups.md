Add two keys to `report.json`:

- `status_classes`: well-formed requests by status class, as `{"2xx": n, "3xx": n, "4xx": n, "5xx": n}`, every class present even when its count is 0.
- `busiest_hour`: the UTC hour with the most well-formed requests, as `"YYYY-MM-DDTHH"`, ties broken by the earlier hour.
---
Turn this into a script we can rerun: `python3 make_report.py LOGS_DIR OUT_JSON` writes the same report for any folder of logs in this format. Next week it runs on another server's logs, so it must not depend on these files' names, count or clock offset: it reads every `access.log*` file in LOGS_DIR, gzipped or not. Keep `report.json` in sync with what the script writes for `logs/`.
