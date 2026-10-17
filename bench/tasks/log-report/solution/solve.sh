#!/bin/bash
# Oracle: one pass over every file, gzipped or not, with its own parser.
set -euo pipefail
cd "$1"
python3 - <<'PY'
import gzip, json, math, re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

pat = re.compile(r'\[(?P<ts>[^\]]+)\] "(?P<method>[A-Z]+) (?P<target>[^ "]+) HTTP/[0-9.]+" (?P<status>[0-9]{3}) (?:[0-9]+|-) "[^"]*" "[^"]*" rt=(?P<rt>[0-9]+\.[0-9]+)$')

def utc_day(ts):
    if "T" in ts:
        t = datetime.fromisoformat(ts)
    else:
        t = datetime.strptime(ts, "%d/%b/%Y:%H:%M:%S %z")
    return t.astimezone(timezone.utc).date().isoformat()

ok = bad = 0
days, codes = Counter(), Counter()
lat = defaultdict(list)
for f in Path("logs").glob("access.log*"):
    data = gzip.decompress(f.read_bytes()) if f.suffix == ".gz" else f.read_bytes()
    for line in data.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        m = pat.search(line)
        if not m or not re.match(r"^\S+ \S+ \S+ \[", line):
            bad += 1
            continue
        ok += 1
        days[utc_day(m["ts"])] += 1
        path = m["target"].partition("?")[0]
        name = m["method"] + " " + re.sub(r"(?<=/)[0-9]+(?=/|$)", ":id", path)
        lat[name].append(round(float(m["rt"]) * 1000))
        if m["status"].startswith("5"):
            codes[int(m["status"])] += 1

eps = {}
for name, xs in lat.items():
    xs.sort()
    eps[name] = {"requests": len(xs), "p95_ms": xs[math.ceil(len(xs) * 0.95) - 1]}
top = sorted(codes.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
json.dump({"requests": ok, "malformed": bad, "per_day": days, "endpoints": eps,
           "top_5xx": [{"status": s, "count": c} for s, c in top]}, open("report.json", "w"), indent=2)
PY
