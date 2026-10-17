#!/bin/bash
# Oracle: one pass over every file, gzipped or not, with its own parser, as the
# make_report.py the last follow-up asks for.
set -euo pipefail
cd "$1"
cat > make_report.py <<'PY'
import gzip, json, math, re, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

pat = re.compile(r'\[(?P<ts>[^\]]+)\] "(?P<method>[A-Z]+) (?P<target>[^ "]+) HTTP/[0-9.]+" (?P<status>[0-9]{3}) (?:[0-9]+|-) "[^"]*" "[^"]*" rt=(?P<rt>[0-9]+\.[0-9]+)$')

def utc(ts):
    if "T" in ts:
        t = datetime.fromisoformat(ts)
    else:
        t = datetime.strptime(ts, "%d/%b/%Y:%H:%M:%S %z")
    return t.astimezone(timezone.utc)

logs, out = Path(sys.argv[1]), sys.argv[2]
ok = bad = 0
days, hours, codes = Counter(), Counter(), Counter()
classes = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}
lat = defaultdict(list)
for f in logs.glob("access.log*"):
    data = gzip.decompress(f.read_bytes()) if f.suffix == ".gz" else f.read_bytes()
    for line in data.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        m = pat.search(line)
        if not m or not re.match(r"^\S+ \S+ \S+ \[", line):
            bad += 1
            continue
        ok += 1
        t = utc(m["ts"])
        days[t.date().isoformat()] += 1
        hours[t.strftime("%Y-%m-%dT%H")] += 1
        classes[m["status"][0] + "xx"] += 1
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
           "top_5xx": [{"status": s, "count": c} for s, c in top], "status_classes": classes,
           "busiest_hour": sorted(hours, key=lambda h: (-hours[h], h))[0]}, open(out, "w"), indent=2)
PY
timeout 120 python3 make_report.py logs report.json
