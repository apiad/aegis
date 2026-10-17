"""Write five rotated access logs, about 50,000 lines, from a fixed seed.

usage: gen_logs.py OUT_DIR

access.log is the newest day. access.log.1 was written while the server's clock
was at +02:00, so some of its lines fall on the previous UTC day. access.log.3.gz
and access.log.4.gz are gzipped, and access.log.4.gz predates a config change:
its timestamps are ISO 8601 instead of the common log format's. About 1% of the
lines are malformed: truncated before the latency, junk bytes, or a latency
written as rt=-.

usage: gen_logs.py OUT_DIR other writes another server's logs instead, from
another seed: three files, a clock at -05:00, and the ISO file uncompressed.
The hidden check runs the agent's make_report.py on them.
"""

import gzip
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 20261017
LINES_PER_FILE = 10_000
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

ENDPOINTS = [
    ("GET", "/api/orders/{id}", 0.040, 18),
    ("GET", "/api/orders", 0.080, 10),
    ("POST", "/api/orders", 0.150, 6),
    ("PUT", "/api/orders/{id}", 0.120, 3),
    ("DELETE", "/api/orders/{id}", 0.060, 1),
    ("GET", "/api/users/{id}", 0.030, 12),
    ("GET", "/api/users/{id}/orders", 0.110, 7),
    ("GET", "/api/search", 0.300, 9),
    ("GET", "/static/app.js", 0.005, 14),
    ("GET", "/static/app.css", 0.004, 8),
    ("GET", "/health", 0.002, 10),
    ("POST", "/api/login", 0.200, 2),
]
STATUSES = [(200, 820), (201, 30), (304, 60), (401, 15), (404, 40), (500, 9),
            (502, 7), (503, 12), (504, 5), (501, 2), (507, 1)]
AGENTS = ["Mozilla/5.0 (X11; Linux x86_64)", "curl/8.5.0", "okhttp/4.12", "Mozilla/5.0 (iPhone)"]
QUERIES = ["", "", "", "?page=2", "?q=lamp&sort=price", "?utm_source=mail"]


def clf_time(t: datetime) -> str:
    off = t.utcoffset() or timedelta()
    sign = "+" if off >= timedelta() else "-"
    mins = abs(int(off.total_seconds())) // 60
    return f"{t.day:02d}/{MONTHS[t.month - 1]}/{t.year}:{t:%H:%M:%S} {sign}{mins // 60:02d}{mins % 60:02d}"


def line(rng: random.Random, t: datetime, iso: bool) -> str:
    method, path, base, _ = rng.choices(ENDPOINTS, weights=[e[3] for e in ENDPOINTS])[0]
    path = path.replace("{id}", str(rng.randint(1, 99_999)))
    status = rng.choices([s for s, _ in STATUSES], weights=[w for _, w in STATUSES])[0]
    latency = round(base * rng.lognormvariate(0, 0.6) * (4 if status >= 500 else 1), 3)
    size = "-" if status == 304 else str(rng.randint(80, 60_000))
    ip = f"198.51.100.{rng.randint(1, 254)}" if rng.random() < 0.5 else f"203.0.113.{rng.randint(1, 254)}"
    stamp = t.replace(microsecond=0).isoformat() if iso else clf_time(t)
    query = rng.choice(QUERIES) if method == "GET" else ""
    agent = rng.choice(AGENTS)
    return f'{ip} - - [{stamp}] "{method} {path}{query} HTTP/1.1" {status} {size} "-" "{agent}" rt={latency:.3f}'


def malformed(rng: random.Random, good: str) -> str:
    kind = rng.randrange(3)
    if kind == 0:
        return good[: rng.randint(10, good.index(" rt=") - 1)]
    if kind == 1:
        return "".join(chr(rng.randint(33, 126)) for _ in range(rng.randint(5, 60)))
    return good[: good.index(" rt=")] + " rt=-"


def write(path: Path, lines: list[str]) -> None:
    data = ("\n".join(lines) + "\n").encode()
    if path.suffix == ".gz":
        with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as f:
            f.write(data)
    else:
        path.write_bytes(data)


def main(out: Path, other: bool = False) -> None:
    rng = random.Random(SEED + 1 if other else SEED)
    out.mkdir(parents=True, exist_ok=True)
    utc = timezone.utc
    plus2 = timezone(timedelta(hours=2))
    minus5 = timezone(timedelta(hours=-5))
    newest = datetime(2026, 10, 16, 0, 0, 0, tzinfo=utc)
    files = [("access.log", utc, False), ("access.log.1", plus2, False), ("access.log.2", utc, False),
             ("access.log.3.gz", utc, False), ("access.log.4.gz", utc, True)]
    if other:
        files = [("access.log", minus5, False), ("access.log.1.gz", utc, False), ("access.log.2", utc, True)]
    for age, (name, tz, iso) in enumerate(files):
        t = (newest - timedelta(days=age)).astimezone(tz)
        step = 86_400 / LINES_PER_FILE
        lines = []
        for i in range(LINES_PER_FILE):
            t += timedelta(seconds=rng.expovariate(1 / step))
            good = line(rng, t, iso)
            lines.append(malformed(rng, good) if rng.random() < 0.01 else good)
            if rng.random() < 0.002:
                lines.append("")
        write(out / name, lines)


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2:] == ["other"])
