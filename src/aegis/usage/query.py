"""One query over the usage tables: a window, a split, a period (spec 12).

A row is one group of parts: the split's dimensions, a ``bucket`` when ``by``
is set, and every measure. Parts are grouped per turn first and then per
group, so a turn and a session count once in a group however many parts they
have there. Time buckets are computed in SQL in the asked zone: the zone's
UTC offsets over the window are a few ranges, passed as one CASE, so a
summer-time change inside the window still buckets right.

Outcomes are the journal's own rows (commit, pr opened, pr merged, issue),
counted by the repo and bucket of the entry. ``server`` is not on disk: the
caller that merges servers' answers fills it, and here it is "".
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from .subscription import WEEK, WEEKLY, Reading, split as split_fee, weekly_fee

BYS = ("hour", "day", "week", "month", "dow", "dow_hour")
MEASURES = (
    "cost", "tokens", "secs", "turns", "sessions", "calls", "errors", "exits",
    "waits", "sub", "limit",
)  # fmt: skip
BANDS = (0, 50_000, 100_000, 200_000, 300_000, 400_000, 600_000)
DOW = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")  # strftime %w order

_CONTEXT = (
    "CASE "
    + " ".join(f"WHEN t.ctx_peak >= {b} THEN {b}" for b in reversed(BANDS[1:]))
    + " ELSE 0 END"
)
DIMS = {
    "server": "''",
    "person": "p.person",
    "owner": "COALESCE(ow.owner, o.owner,"
    " CASE WHEN p.repo IS NULL THEN '' ELSE 'local' END)",
    "repo": "p.repo",
    "harness": "p.harness",
    "model": "p.model",
    "mode": "p.mode",
    "kind": "p.kind",
    "purpose": "p.purpose",
    "activity": "p.activity",
    "billing": "p.billing",
    "context": _CONTEXT,
    "session": "p.log_id",
}
# Turn-level dimensions, which usage_turns answers alone.
TURN_DIMS = {"person", "harness", "model", "mode", "kind", "billing"}


@dataclass
class UsageQuery:
    since: float
    until: float
    by: str | None = None
    split: tuple[str, ...] = ()
    measure: str = "cost"
    where: dict[str, str] = field(default_factory=dict)
    tz: str = "UTC"
    people: set[str] | None = None


@dataclass
class Answer:
    rows: list[dict]
    totals: dict
    outcomes: list[dict]
    estimated: float
    unpriced_tokens: int
    # Per subscription (billing): its fee for the window, elsewhere, unused,
    # apportioned dollars and API-priced value; and its API-priced cost per
    # [hour, group, cost], which a merge across servers shares again.
    subs: dict[str, dict] = field(default_factory=dict)
    sub_hours: dict[str, list] = field(default_factory=dict)


def _offsets(since: float, until: float, tz: str) -> str:
    """The SQL for ``{ts}``'s offset from UTC in ``tz``, in seconds, over the
    window: one CASE over the ranges between the zone's changes, found by
    day and then by hour."""
    zone = ZoneInfo(tz)

    def off(t: float) -> int:
        u = datetime.fromtimestamp(t, timezone.utc).astimezone(zone).utcoffset()
        return int(u.total_seconds()) if u else 0

    day = 86400
    t = since - since % 3600
    ranges: list[tuple[float, int]] = []
    cur = off(t)
    while t < until:
        nxt = t + day
        if off(nxt) != cur:
            h = t
            while h < nxt:
                h += 3600
                if (o := off(h)) != cur:
                    ranges.append((h, cur))
                    cur = o
        t = nxt
    if not ranges:
        return str(cur)
    whens = " ".join(f"WHEN {{ts}} < {at} THEN {o}" for at, o in ranges)
    return f"CASE {whens} ELSE {cur} END"


def _bucket(by: str, ts: str, offsets: str) -> str:
    local = f"({ts} + {offsets.replace('{ts}', ts)})"
    return {
        "hour": f"strftime('%Y-%m-%d %H', {local}, 'unixepoch')",
        "day": f"date({local}, 'unixepoch')",
        "week": f"date({local}, 'unixepoch', '-6 days', 'weekday 1')",
        "month": f"strftime('%Y-%m', {local}, 'unixepoch')",
        "dow": f"CAST(strftime('%w', {local}, 'unixepoch') AS INTEGER)",
        "dow_hour": f"strftime('%w %H', {local}, 'unixepoch')",
    }[by]


def _label(by: str | None, value):
    if by == "dow":
        return DOW[value]
    if by == "dow_hour":
        d, h = value.split()
        return f"{DOW[int(d)]} {h}"
    return value


def _owners_cte(con: sqlite3.Connection, owners: dict | None) -> tuple[str, list]:
    """The config's owners of local repos, by directory name, as a table."""
    repos = (
        [
            r
            for (r,) in con.execute(
                "SELECT DISTINCT repo FROM usage_parts WHERE repo IS NOT NULL"
            )
        ]
        if owners
        else []
    )
    pairs = [(r, owners[r.rstrip("/").rsplit("/", 1)[-1]]) for r in repos
             if r.rstrip("/").rsplit("/", 1)[-1] in (owners or {})]  # fmt: skip
    if not pairs:
        return "ow(repo, owner) AS (SELECT NULL, NULL WHERE 0)", []
    values = ",".join("(?, ?)" for _ in pairs)
    return f"ow(repo, owner) AS (VALUES {values})", [x for pr in pairs for x in pr]


def _filter(q: UsageQuery, dims: set[str]) -> tuple[str, list]:
    """The FROM and WHERE of a query over parts; the owners and the turns are
    joined only when a dimension asks for them."""
    where = ["p.ts >= ?", "p.ts < ?"]
    args: list = [q.since, q.until]
    for d, v in q.where.items():
        where.append(f"{DIMS[d]} = ?")
        args.append(v)
    if q.people is not None:
        where.append(f"p.person IN ({','.join('?' * len(q.people))})")
        args += sorted(q.people)
    joins = ""
    if "context" in dims:
        joins += " JOIN usage_turns t ON t.log_id = p.log_id AND t.rec = p.rec"
    if "owner" in dims:
        joins += " LEFT JOIN repo_owners o ON o.repo = p.repo LEFT JOIN ow ON ow.repo = p.repo"
    return f"FROM usage_parts p{joins} WHERE {' AND '.join(where)}", args


def _grouped(con, q: UsageQuery, cte: str, cte_args: list, offsets: str) -> list[dict]:
    """The parts of the window grouped by the split and the bucket, in one
    statement. A turn and a session count once in a group."""
    base, args = _filter(q, {*q.split, *q.where})
    keys = [DIMS[d] for d in q.split]
    if q.by:
        keys.append(_bucket(q.by, "p.ts", offsets))
    names = [*q.split, *(["bucket"] if q.by else [])]
    select = "".join(f"{k}, " for k in keys)
    group = (
        f" GROUP BY {', '.join(str(n + 1) for n in range(len(keys)))}" if keys else ""
    )
    sql = (
        f"WITH {cte} SELECT {select}SUM(p.cost), SUM(p.tokens), SUM(p.secs),"
        " COUNT(DISTINCT p.turn), COUNT(DISTINCT p.log_id),"
        " SUM(p.calls), SUM(p.errors), SUM(p.exits),"
        " SUM(CASE WHEN p.cost_kind = 'priced' THEN p.cost ELSE 0 END),"
        f" SUM(CASE WHEN p.cost_kind = 'unpriced' THEN p.tokens ELSE 0 END) {base}{group}"
    )
    rows = []
    for r in con.execute(sql, cte_args + args):
        k = len(keys)
        if not r[k + 3]:
            continue  # the empty aggregate of an empty window
        row = dict(zip(names, r[:k]))
        if q.by:
            row["bucket"] = _label(q.by, row["bucket"])
        cost, *rest = r[k : k + 8]
        row |= dict(zip(MEASURES, (round(cost or 0, 9), *(v or 0 for v in rest))))
        row |= {"waits": 0.0, "sub": 0.0, "limit": 0.0}
        row["_priced"], row["_unpriced"] = r[k + 8] or 0, r[k + 9] or 0
        rows.append(row)
    return rows


def _distinct(con, q: UsageQuery, cte: str, cte_args: list) -> tuple[int, int]:
    """Turns and sessions in the whole window: from the turns alone when no
    part-level dimension filters it."""
    if set(q.where) <= TURN_DIMS:
        where, args = ["ts >= ?", "ts < ?"], [q.since, q.until]
        for d, v in q.where.items():
            where.append(f"{d} = ?")
            args.append(v)
        if q.people is not None:
            where.append(f"person IN ({','.join('?' * len(q.people))})")
            args += sorted(q.people)
        sql = f"SELECT COUNT(*), COUNT(DISTINCT log_id) FROM usage_turns WHERE {' AND '.join(where)}"
        return con.execute(sql, args).fetchone()
    base, args = _filter(q, set(q.where))
    return con.execute(
        f"WITH {cte} SELECT COUNT(DISTINCT p.turn), COUNT(DISTINCT p.log_id) {base}",
        cte_args + args,
    ).fetchone()


def run(
    con: sqlite3.Connection,
    q: UsageQuery,
    owners: dict | None = None,
    subscriptions: dict | None = None,
) -> Answer:
    """The answer to ``q``; ``owners`` is the config's ``usage.owners`` and
    ``subscriptions`` its ``usage.subscriptions``."""
    if q.by is not None and q.by not in BYS:
        raise ValueError(f"by must be one of {', '.join(BYS)}")
    for d in (*q.split, *q.where):
        if d not in DIMS:
            raise ValueError(f"no dimension {d!r}; one of {', '.join(DIMS)}")
    # The zone's offsets matter only where there are rows: an open window
    # (since 0) would otherwise walk every day since 1970.
    lo, hi = con.execute("SELECT MIN(ts), MAX(ts) FROM usage_parts").fetchone()
    lo2, hi2 = con.execute("SELECT MIN(ts), MAX(ts) FROM entries").fetchone()
    lo = min(x for x in (lo, lo2, q.until) if x is not None)
    hi = max(x for x in (hi, hi2, q.since) if x is not None)
    offsets = _offsets(max(q.since, lo), min(q.until, hi + 1), q.tz)
    cte, cte_args = _owners_cte(con, owners)
    rows = _grouped(con, q, cte, cte_args, offsets)
    hours = _sub_hours(con, q, cte, cte_args, offsets)
    subs = _subscriptions(con, q, hours, rows, subscriptions or {})
    totals = {m: sum(r[m] for r in rows) for m in MEASURES}
    totals["cost"] = round(totals["cost"], 9)
    if q.split or q.by:
        totals["turns"], totals["sessions"] = _distinct(con, q, cte, cte_args)
    priced = sum(r.pop("_priced") for r in rows)
    unpriced = sum(r.pop("_unpriced") for r in rows)
    waits = _waits(con, q, offsets)
    totals["waits"] = sum(waits.values())
    for row in rows:
        if q.split == ("mode",):
            row["waits"] = waits.get((row["mode"], row.get("bucket")), 0.0)
        elif not q.split:
            row["waits"] = sum(
                v for (_, b), v in waits.items() if b == row.get("bucket")
            )
    cost = totals["cost"]
    return Answer(
        rows=rows,
        totals=totals,
        outcomes=_outcomes(con, q, offsets, owners),
        estimated=round(priced / cost, 6) if cost else 0.0,
        unpriced_tokens=int(unpriced),
        subs=subs,
        sub_hours={
            b: [[h, list(k), round(float(c), 9)] for (h, k), c in v.items()]
            for b, v in hours.items()
        },
    )


def _sub_hours(con, q: UsageQuery, cte: str, cte_args: list, offsets: str) -> dict:
    """API-priced cost on each subscription, by UTC hour and the row's group
    (the split's values and the bucket)."""
    base, args = _filter(q, {*q.split, *q.where})
    keys = [DIMS[d] for d in q.split]
    if q.by:
        keys.append(_bucket(q.by, "p.ts", offsets))
    select = "".join(f", {k}" for k in keys)
    group = ", ".join(str(n + 1) for n in range(len(keys) + 2))
    sql = (
        f"WITH {cte} SELECT p.billing, CAST(p.ts / 3600 AS INTEGER) * 3600{select},"
        f" SUM(p.cost) {base} AND p.billing <> 'api' GROUP BY {group}"
    )
    out: dict[str, dict] = {}
    for r in con.execute(sql, cte_args + args):
        key = list(r[2:-1])
        if q.by:
            key[-1] = _label(q.by, key[-1])
        out.setdefault(r[0], {})[(r[1], tuple(key))] = Decimal(str(r[-1] or 0))
    return out


def _readings(con, billing: str, since: float, until: float) -> list[Reading]:
    """The weekly window's readings in the window, and the one before it."""
    window = WEEKLY.get(billing.split(":", 1)[0])
    if window is None:
        return []
    rows = con.execute(
        "SELECT ts, percent, resets_at FROM usage_quota WHERE billing = ? AND window = ?"
        " AND ts >= (SELECT COALESCE(MAX(ts), ?) FROM usage_quota"
        " WHERE billing = ? AND window = ? AND ts < ?) AND ts < ? ORDER BY ts",
        (billing, window, since, billing, window, since, until),
    ).fetchall()
    return [Reading(*r) for r in rows]


def _subscriptions(con, q: UsageQuery, hours: dict, rows: list, conf: dict) -> dict:
    """Split each subscription's fee and limit over the rows: their ``sub``
    and ``limit``, and a summary per subscription."""
    out: dict[str, dict] = {}
    by_key = {
        (*(r[d] for d in q.split), *((r["bucket"],) if q.by else ())): r for r in rows
    }
    billings = set(hours) | {
        b
        for (b,) in con.execute(
            "SELECT DISTINCT billing FROM usage_quota WHERE ts >= ? AND ts < ?",
            (q.since, q.until),
        )
    }
    for billing in sorted(billings):
        c = conf.get(billing.split(":", 1)[-1]) or {}
        fee = Decimal(str(c["fee"])) if c.get("fee") else None
        hourly = hours.get(billing, {})
        first = min((h for h, _ in hourly), default=q.since)
        span = (max(q.since, first), min(q.until, time.time()))
        s = split_fee(_readings(con, billing, q.since, q.until), hourly, fee, span)
        for key, points in s.limit.items():
            if key in by_key:
                by_key[key]["limit"] += points
        for key, dollars in s.dollars.items():
            if key in by_key:
                by_key[key]["sub"] += float(dollars)
        week = weekly_fee(fee) if fee is not None else Decimal(0)
        out[billing] = {
            "name": c.get("name") or billing,
            "fee": float(week * Decimal(str(max(span[1] - span[0], 0))) / WEEK),
            "kind": s.kind,
            "sub": float(sum(s.dollars.values(), Decimal(0))),
            "elsewhere": s.elsewhere,
            "unused": s.unused,
            "elsewhere_usd": float(week * Decimal(str(s.elsewhere)) / 100),
            "unused_usd": float(week * Decimal(str(s.unused)) / 100),
            "apportioned": float(s.apportioned),
            "value": round(float(sum(hourly.values(), Decimal(0))), 9),
        }
    return out


def _waits(con: sqlite3.Connection, q: UsageQuery, offsets: str) -> dict[tuple, float]:
    """Seconds waited, by what woke the session and bucket."""
    bucket = _bucket(q.by, "w.ts", offsets) if q.by else "NULL"
    where, args = ["w.ts >= ?", "w.ts < ?"], [q.since, q.until]
    if q.people is not None:
        where.append(
            "w.log_id IN (SELECT log_id FROM usage_turns WHERE person IN"
            f" ({','.join('?' * len(q.people))}))"
        )
        args += sorted(q.people)
    out: dict[tuple, float] = {}
    for woke, b, secs in con.execute(
        f"SELECT w.woke_by, {bucket}, SUM(w.secs) FROM usage_waits w"
        f" WHERE {' AND '.join(where)} GROUP BY 1, 2",
        args,
    ):
        out[(woke, _label(q.by, b) if q.by else None)] = secs
    return out


OUTCOMES = {
    "commits": "e.kind = 'commit'",
    "prs_opened": "e.kind = 'pr' AND e.text LIKE 'opened%'",
    "prs_merged": "e.kind = 'pr' AND e.text LIKE 'merged%'",
    "issues": "e.kind = 'issue'",
}


def _outcomes(
    con: sqlite3.Connection, q: UsageQuery, offsets: str, owners: dict | None
) -> list[dict]:
    """The journal's outcomes in the window, by the entry's repo, owner and
    bucket as far as the split asks for them."""
    cte, cte_args = _owners_cte(con, owners)
    keys, names = [], []
    if "repo" in q.split:
        keys.append("e.repo")
        names.append("repo")
    if "owner" in q.split:
        keys.append(DIMS["owner"].replace("p.repo", "e.repo"))
        names.append("owner")
    if q.by:
        keys.append(_bucket(q.by, "e.ts", offsets))
        names.append("bucket")
    where, args = (
        ["e.ts >= ?", "e.ts < ?", "e.kind IN ('commit', 'pr', 'issue')"],
        [
            q.since,
            q.until,
        ],
    )
    for d, v in q.where.items():
        if d in ("repo", "owner"):
            where.append(f"{DIMS[d].replace('p.repo', 'e.repo')} = ?")
            args.append(v)
    if q.people is not None:
        where.append(
            "e.log_id IN (SELECT log_id FROM usage_turns WHERE person IN"
            f" ({','.join('?' * len(q.people))}))"
        )
        args += sorted(q.people)
    counts = ", ".join(
        f"SUM(CASE WHEN {c} THEN 1 ELSE 0 END)" for c in OUTCOMES.values()
    )
    select = ", ".join(keys + [counts])
    sql = (
        f"WITH {cte} SELECT {select} FROM entries e"
        " LEFT JOIN repo_owners o ON o.repo = e.repo LEFT JOIN ow ON ow.repo = e.repo"
        f" WHERE {' AND '.join(where)}"
        + (
            f" GROUP BY {', '.join(str(n + 1) for n in range(len(keys)))}"
            if keys
            else ""
        )
    )
    out = []
    for r in con.execute(sql, cte_args + args):
        row = dict(zip(names, r[: len(keys)]))
        if "bucket" in row:
            row["bucket"] = _label(q.by, row["bucket"])
        nums = [int(x or 0) for x in r[len(keys) :]]
        if any(nums):
            out.append(row | dict(zip(OUTCOMES, nums)))
    return out
