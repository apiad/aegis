"""Recompute report.json from the logs at the start tag and compare it exactly.

usage: check_report.py LOGS_DIR REPORT_JSON [LABEL]
Prints PASS, or FAIL: <the first field that differs>. LABEL names the report in
the message: the check runs this once on report.json and once on what
make_report.py writes for another server's logs.
"""

import gzip
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

LINE = re.compile(
    r'^(\S+) (\S+) (\S+) \[([^\]]+)\] "(\S+) (\S+) (\S+)" (\d{3}) (\d+|-) "([^"]*)" "([^"]*)" rt=(\d+\.\d+)$'
)


def when(stamp: str) -> datetime:
    for fmt in ("%d/%b/%Y:%H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(stamp, fmt)
        except ValueError:
            pass
    raise ValueError(stamp)


def endpoint(method: str, target: str) -> str:
    path = target.split("?", 1)[0]
    return method + " " + "/".join(":id" if s.isdigit() else s for s in path.split("/"))


def expected(logs: Path) -> dict:
    requests = malformed = 0
    per_day: Counter = Counter()
    per_hour: Counter = Counter()
    classes = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}
    latencies: dict[str, list[int]] = defaultdict(list)
    errors: Counter = Counter()
    for f in sorted(logs.iterdir()):
        opener = gzip.open if f.name.endswith(".gz") else open
        with opener(f, "rt", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                text = raw.rstrip("\n")
                if not text.strip():
                    continue
                m = LINE.match(text)
                try:
                    t = when(m.group(4)) if m else None
                except ValueError:
                    t = None
                if t is None:
                    malformed += 1
                    continue
                requests += 1
                per_day[t.astimezone(timezone.utc).strftime("%Y-%m-%d")] += 1
                per_hour[t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")] += 1
                ms = round(Decimal(m.group(12)) * 1000)
                latencies[endpoint(m.group(5), m.group(6))].append(ms)
                status = int(m.group(8))
                classes[f"{status // 100}xx"] += 1
                if 500 <= status <= 599:
                    errors[status] += 1
    endpoints = {}
    for name, values in latencies.items():
        values.sort()
        endpoints[name] = {"requests": len(values), "p95_ms": values[math.ceil(0.95 * len(values)) - 1]}
    top = sorted(errors.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return {
        "requests": requests,
        "malformed": malformed,
        "per_day": dict(per_day),
        "endpoints": endpoints,
        "top_5xx": [{"status": s, "count": c} for s, c in top],
        "status_classes": classes,
        "busiest_hour": min(per_hour, key=lambda h: (-per_hour[h], h)),
    }


def first_difference(want, got, where="report") -> str | None:
    if isinstance(want, dict) and isinstance(got, dict):
        for key in sorted(set(want) | set(got)):
            if key not in got:
                return f"{where}.{key} is missing"
            if key not in want:
                return f"{where}.{key} should not be there"
            diff = first_difference(want[key], got[key], f"{where}.{key}")
            if diff:
                return diff
        return None
    if want != got:
        return f"{where} is {got!r}, expected {want!r}"
    return None


def main(logs: Path, report: Path, label: str = "report") -> int:
    if not report.exists():
        print(f"FAIL: no {label}")
        return 1
    try:
        got = json.loads(report.read_text())
    except ValueError as e:
        print(f"FAIL: {label} is not JSON: {e}")
        return 1
    diff = first_difference(expected(logs), got, label)
    print(f"FAIL: {diff}" if diff else "PASS")
    return 1 if diff else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2]), *sys.argv[3:]))
