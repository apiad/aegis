"""The usage page's panels (spec 12): Python decides, the browser draws.

``queries`` names the questions one server answers for the page; ``panels``
turns each server's answers, in usage.read's shape, into what the page
shows: labels, numbers as text, widths and shades as fractions and levels,
and a colour index per series. The browser computes nothing.

The window is whole weeks in the viewer's zone, Monday first, ending now;
the previous period is the same span a window earlier, cut at the same point
of its last week, so a change compares like with like.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .context import BANDS, THRESHOLDS, band, band_label
from .query import UsageQuery

PURPOSES = ("build", "fix", "refactor", "review", "docs", "writing", "planning",
            "research", "ops", "coordinate", "chat")  # fmt: skip
UNATTENDED = ("agent", "queue", "workflow", "schedule", "monitor")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
# What the page's measure control offers, and the column each reads.
MEASURES = {"cost": "cost", "sub": "sub", "tokens": "tokens", "hours": "secs"}
TITLES = {"cost": "Spend", "sub": "Subscription $", "tokens": "Tokens",
          "hours": "Agent hours"}  # fmt: skip
SERIES = 8  # colours in the client's palette; the rest is "other"
LEVELS = 4


def window(weeks: int, now: float, tz: str) -> tuple[float, float, float, list[str]]:
    """The window's start, the previous period's start and end, and the
    Monday of each week in it."""
    zone = ZoneInfo(tz)
    today = datetime.fromtimestamp(now, zone)
    monday = (today - timedelta(days=today.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    first = monday - timedelta(weeks=weeks - 1)
    start = first.timestamp()
    span = timedelta(weeks=weeks)
    prev = (first - span).timestamp()
    prev_end = (today - span).timestamp()
    mondays = [(first + timedelta(weeks=i)).date().isoformat() for i in range(weeks)]
    return start, prev, prev_end, mondays


def queries(weeks: int, split: str, now: float, tz: str) -> dict[str, UsageQuery]:
    """What one server answers for the page, by panel."""
    start, prev, prev_end, _ = window(weeks, now, tz)

    def q(**kw) -> UsageQuery:
        return UsageQuery(start, now + 1, tz=tz, **kw)

    return {
        "total": q(),
        "prev": UsageQuery(prev, prev_end, tz=tz),
        "trend": q(by="week", split=(split,)),
        "owners": q(split=("owner",)),
        "heat": q(split=("repo", "purpose")),
        "when": q(by="dow_hour"),
        "mode": q(split=("mode",)),
        "model": q(split=("harness", "model")),
        "outcomes": q(split=("repo",)),
        "subs": q(split=("billing", "owner")),
        "context": q(split=("context",)),
    }


def fmt(measure: str, v: float) -> str:
    if measure in ("cost", "sub"):
        if abs(v) < 100:
            return f"${v:,.2f}"
        return f"${v:,.0f}" if abs(v) < 10_000 else f"${v / 1000:,.1f}k"
    if measure == "tokens":
        return _count(v)
    return f"{v:,.1f} h"


def _count(v: float) -> str:
    for unit, n in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(v) >= n:
            return f"{v / n:.1f}{unit}"
    return f"{v:.0f}"


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _value(row: dict, measure: str) -> float:
    v = row.get(MEASURES[measure], 0) or 0
    return v / 3600 if measure == "hours" else v


def _level(x: float) -> int:
    return 0 if x <= 0 else min(LEVELS, 1 + int(x * LEVELS))


def _repo_label(repo: str | None) -> str:
    return repo.rstrip("/").rsplit("/", 1)[-1] if repo else "(no repo)"


def _bars(totals: dict, measure: str, label=str) -> list[dict]:
    """Rows of one bar each, largest first, as fractions of the largest."""
    top = max(totals.values(), default=0) or 1
    whole = sum(totals.values()) or 1
    return [
        {
            "key": k,
            "label": label(k),
            "value": v,
            "text": fmt(measure, v),
            "share": _pct(v / whole),
            "frac": v / top,
        }  # fmt: skip
        for k, v in sorted(totals.items(), key=lambda kv: -kv[1])
        if v
    ]


def panels(
    answers: dict[str, dict[str, dict]],
    window_weeks: int,
    split: str,
    measure: str,
    names: dict[str, str],
    mondays: list[str] | None = None,
    handoff_at: int = 300_000,
) -> dict:
    """``answers``: per server, its answer to each of ``queries``. ``names``
    relabels owners (``usage.owner_names``). ``mondays`` are the window's
    weeks, from ``window``; without them, the weeks the trend has rows for."""
    servers = list(answers)

    def rows(panel: str):
        for s in servers:
            for r in answers[s][panel]["rows"]:
                yield s, r

    def total(panel: str, m: str = measure) -> float:
        return sum(_value(answers[s][panel]["totals"], m) for s in servers)

    return {
        "tiles": _tiles(answers, servers, measure, total, rows),
        "trend": _trend(rows("trend"), window_weeks, split, measure, mondays),
        "owners": _owners(rows("owners"), servers, measure, names),
        "heat": _heat(rows("heat"), measure),
        "when": _when(rows("when"), measure),
        "mode": _bars(_by(rows("mode"), "mode", measure), measure),
        "model": _model(rows("model"), measure),
        "outcomes": _outcomes(answers, servers, measure),
        "subscriptions": _subscriptions(answers, servers, names),
        "context": _context(answers, servers, handoff_at),
        "foot": _foot(answers, servers, total),
    }


def _by(rows, key: str, measure: str) -> dict:
    out: dict = {}
    for _, r in rows:
        out[r[key]] = out.get(r[key], 0) + _value(r, measure)
    return out


def _tiles(answers, servers, measure, total, rows) -> list[dict]:
    now, before = total("total"), total("prev")
    change = None
    if before:
        d = (now - before) / before
        change = {"text": f"{'+' if d >= 0 else '−'}{abs(d) * 100:.0f}%",
                  "dir": "up" if d >= 0 else "down"}  # fmt: skip
    hours = total("total", "hours")
    waits = sum(r.get("waits", 0) for _, r in rows("mode") if r["mode"] == "person")
    by_mode = _by(rows("mode"), "mode", measure)
    alone = sum(v for k, v in by_mode.items() if k in UNATTENDED)
    cost = total("total", "cost")
    merged = sum(o.get("prs_merged", 0) for s in servers
                 for o in answers[s]["total"]["outcomes"])  # fmt: skip
    sub = total("total", "sub")
    return [
        {"key": "total", "label": f"{TITLES[measure]}, this window",
         "value": fmt(measure, now), "change": change,
         "sub": f"against {fmt(measure, before)} the period before"},
        {"key": "hours", "label": "Agent hours", "value": f"{hours:,.1f} h",
         "sub": f"{waits / 3600:,.1f} h waiting on the person"},
        {"key": "alone", "label": "Without a person",
         "value": _pct(alone / now) if now else "—",
         "sub": "agent, queue, workflow, schedule, monitor"},
        {"key": "per_pr", "label": "Cost per merged PR",
         "value": fmt("cost", cost / merged) if merged else "—",
         "sub": f"{merged} merged"},
        {"key": "subs", "label": "Subscriptions at API prices",
         "value": fmt("cost", sub) if sub else "—",
         "sub": "share of each fee" if sub else "no subscription set"},
    ]  # fmt: skip


def _trend(rows, weeks: int, split: str, measure: str, mondays) -> dict:
    cells: dict[tuple[str, str], float] = {}
    keys: dict[str, float] = {}
    for _, r in rows:
        k = str(r.get(split) if r.get(split) is not None else "(none)")
        v = _value(r, measure)
        cells[(r["bucket"], k)] = cells.get((r["bucket"], k), 0) + v
        keys[k] = keys.get(k, 0) + v
    ranked = [k for k, v in sorted(keys.items(), key=lambda kv: -kv[1]) if v]
    shown = ranked[: SERIES - 1] if len(ranked) > SERIES else ranked
    colour = {k: i for i, k in enumerate(shown)}
    if len(shown) < len(ranked):
        colour["other"] = SERIES - 1
    weeks_shown = mondays or sorted({b for b, _ in cells})
    stacks = []
    for w in weeks_shown:
        stack: dict[str, float] = {}
        for k in ranked:
            v = cells.get((w, k), 0)
            if v:
                key = k if k in colour else "other"
                stack[key] = stack.get(key, 0) + v
        stacks.append((w, stack))
    top = max((sum(s.values()) for _, s in stacks), default=0) or 1
    label = split if split != "repo" else None
    return {
        "title": f"{TITLES[measure]} per week",
        "split": split,
        "weeks": [
            {"label": datetime.fromisoformat(w).strftime("%b %d"), "monday": w,
             "text": fmt(measure, sum(s.values())), "frac": sum(s.values()) / top,
             "stack": [{"key": k, "colour": colour[k], "text": fmt(measure, v),
                        "frac": v / top} for k, v in s.items()]}
            for w, s in stacks
        ],
        "series": [
            {"key": k, "label": k if label else _repo_label(k), "colour": c,
             "text": fmt(measure, keys.get(k) or sum(v for kk, v in keys.items()
                                                      if kk not in colour))}
            for k, c in colour.items()
        ],
    }  # fmt: skip


def _owners(rows, servers, measure, names) -> list[dict]:
    per: dict[str, dict[str, float]] = {}
    for s, r in rows:
        o = r["owner"] or ""
        per.setdefault(o, {})[s] = per.setdefault(o, {}).get(s, 0) + _value(r, measure)
    top = max((sum(v.values()) for v in per.values()), default=0) or 1
    out = [
        {"owner": o, "label": names.get(o, o) or "(no repo)", "text": fmt(measure, sum(v.values())),
         "frac": sum(v.values()) / top,
         "parts": [{"server": s, "colour": servers.index(s), "text": fmt(measure, x),
                    "frac": x / top} for s, x in v.items() if x],
         "where": {"owner": o}}
        for o, v in per.items() if sum(v.values())
    ]  # fmt: skip
    return sorted(out, key=lambda x: -x["frac"])


def _heat(rows, measure) -> dict:
    per: dict[str, dict[str, float]] = {}
    for _, r in rows:
        if r["repo"]:
            p = per.setdefault(r["repo"], {})
            p[r["purpose"]] = p.get(r["purpose"], 0) + _value(r, measure)
    used = {k for p in per.values() for k, v in p.items() if v}
    cols = [p for p in PURPOSES if p in used and p != "chat"]
    cols += sorted(used - set(PURPOSES))
    out = []
    for repo, p in sorted(per.items(), key=lambda kv: -sum(kv[1].values())):
        whole = sum(p.values())
        if not whole:
            continue
        out.append({
            "repo": repo, "label": _repo_label(repo), "text": fmt(measure, whole),
            "cells": [{"purpose": c, "share": p.get(c, 0) / whole,
                       "text": _pct(p.get(c, 0) / whole) if p.get(c) else "",
                       "level": _level(p.get(c, 0) / whole),
                       "where": {"repo": repo, "purpose": c}} for c in cols],
        })  # fmt: skip
    return {"cols": cols, "rows": out}


def _when(rows, measure) -> dict:
    grid = {(d, h): 0.0 for d in DAYS for h in range(24)}
    for _, r in rows:
        d, h = r["bucket"].split()
        grid[(d, int(h))] += _value(r, measure)
    top = max(grid.values()) or 1
    return {
        "days": list(DAYS),
        "hours": [f"{h:02d}" for h in range(24)],
        "cells": [[{"text": fmt(measure, grid[(d, h)]) if grid[(d, h)] else "",
                    "level": _level(grid[(d, h)] / top)} for h in range(24)]
                  for d in DAYS],
    }  # fmt: skip


def _model(rows, measure) -> list[dict]:
    per: dict[tuple, dict] = {}
    for _, r in rows:
        k = (r["harness"], r["model"])
        acc = per.setdefault(k, {"v": 0.0, "tokens": 0})
        acc["v"] += _value(r, measure)
        acc["tokens"] += r.get("tokens", 0)
    out = []
    unpriced = measure == "cost"
    for (h, m), acc in per.items():
        if not acc["v"] and not (unpriced and acc["tokens"]):
            continue
        out.append({"key": f"{h} · {m}", "label": f"{h} · {m}", "value": acc["v"],
                    "text": fmt(measure, acc["v"]) if acc["v"] or not unpriced
                    else f"{_count(acc['tokens'])} tokens, unpriced",
                    "where": {"harness": h, "model": m}})  # fmt: skip
    out.sort(key=lambda x: -x["value"])
    top = max((x["value"] for x in out), default=0) or 1
    for x in out:
        x["frac"] = x["value"] / top
    return out


def _outcomes(answers, servers, measure) -> dict:
    per: dict[str, dict] = {}
    blank = {"commits": 0, "prs_opened": 0, "prs_merged": 0, "issues": 0,
             "v": 0.0, "cost": 0.0}  # fmt: skip
    for s in servers:
        a = answers[s]["outcomes"]
        for o in a["outcomes"]:
            acc = per.setdefault(o.get("repo"), dict(blank))
            for k in ("commits", "prs_opened", "prs_merged", "issues"):
                acc[k] += o.get(k, 0)
        for r in a["rows"]:
            acc = per.setdefault(r["repo"], dict(blank))
            acc["v"] += _value(r, measure)
            acc["cost"] += r.get("cost", 0)
    out = []
    for repo, acc in sorted(per.items(), key=lambda kv: -kv[1]["v"]):
        if not repo or not (
            acc["v"] or acc["commits"] or acc["prs_opened"] or acc["issues"]
        ):
            continue
        out.append({
            "repo": repo, "label": _repo_label(repo),
            **{k: acc[k] for k in ("commits", "prs_opened", "prs_merged", "issues")},
            "text": fmt(measure, acc["v"]),
            "per_pr": fmt("cost", acc["cost"] / acc["prs_merged"]) if acc["prs_merged"] else "—",
            "where": {"repo": repo},
        })  # fmt: skip
    return {"rows": out}


def _subscriptions(answers, servers, names) -> list[dict]:
    """Per subscription: its fee, split over owners, elsewhere and unused,
    and what it ran at API prices against the fee. One server's readings
    serve every server on the account: the first that has the summary."""
    summary: dict[str, dict] = {}
    owners: dict[str, dict[str, float]] = {}
    for s in servers:
        a = answers[s].get("subs")
        if not a:
            continue
        for b, v in (a.get("subs") or {}).items():
            summary.setdefault(b, v)
        for r in a["rows"]:
            o = owners.setdefault(r["billing"], {})
            o[r["owner"] or ""] = o.get(r["owner"] or "", 0) + (r.get("sub") or 0)
    out = []
    for b, v in sorted(summary.items(), key=lambda kv: -kv[1]["value"]):
        parts = [(names.get(o, o) or "(no repo)", x) for o, x in
                 sorted(owners.get(b, {}).items(), key=lambda kv: -kv[1]) if x]  # fmt: skip
        parts += [("elsewhere", v["elsewhere_usd"]), ("unused", v["unused_usd"])]
        whole = max(sum(x for _, x in parts), v["fee"]) or 1
        fee = v["fee"]
        note = (
            f"{_pct(v['apportioned'] / v['sub'])} of it apportioned by cost, without readings"
            if v["sub"] and v["apportioned"]
            else ("metered by the weekly limit" if fee else "no fee set: limit only")
        )
        out.append({
            "billing": b, "name": v["name"], "kind": v["kind"],
            "fee": fmt("cost", fee) if fee else "—",
            "value": fmt("cost", v["value"]),
            "ratio": f"{v['value'] / fee:.1f}×" if fee else "—",
            "parts": [{"label": k, "text": fmt("cost", x), "frac": x / whole,
                       "colour": "rest" if k in ("elsewhere", "unused") else i}
                      for i, (k, x) in enumerate(parts) if x],
            "note": note,
        })  # fmt: skip
    return out


def _context(answers, servers, handoff_at: int) -> dict:
    """Share of calls against share of cost per band, with a call's mean
    cost; session peaks for handed-off and other sessions, the band of
    ``handoff_at`` marked; the replay per threshold, net of rereading."""
    calls: dict[int, int] = {}
    cost: dict[int, float] = {}
    peaks: list = []
    replay: dict[int, dict] = {}
    for s in servers:
        c = (answers[s].get("context") or {}).get("context")
        if not c:
            continue
        for b in c["bands"]:
            calls[b["band"]] = calls.get(b["band"], 0) + b["calls"]
            cost[b["band"]] = cost.get(b["band"], 0) + b["cost"]
        peaks += c["peaks"]
        for r in c["replay"]:
            acc = replay.setdefault(r["at"], {"handoffs": 0, "saved": 0.0,
                                              "rewrite": 0.0, "reread": r["reread"]})  # fmt: skip
            for k in ("handoffs", "saved", "rewrite"):
                acc[k] += r[k]
    n, usd = sum(calls.values()) or 1, sum(cost.values()) or 1
    bands = [
        {"label": band_label(k), "calls": _pct(calls[k] / n), "cost": _pct(cost.get(k, 0) / usd),
         "calls_frac": calls[k] / n, "cost_frac": cost.get(k, 0) / usd,
         "per_call": fmt("cost", cost.get(k, 0) / calls[k]) if calls[k] else "—"}
        for k in sorted(calls)
    ]  # fmt: skip
    counts = {k: [0, 0] for k in range(len(BANDS))}
    for peak, handed in peaks:
        counts[band(peak)][0 if handed else 1] += 1
    top = max((sum(v) for v in counts.values()), default=0) or 1
    rows = [
        {"label": band_label(k), "handed": h, "other": o, "handed_frac": h / top,
         "other_frac": o / top, "mark": k == band(handoff_at)}
        for k, (h, o) in counts.items()
    ]  # fmt: skip
    table = []
    for at in THRESHOLDS:
        r = replay.get(at, {"handoffs": 0, "saved": 0.0, "rewrite": 0.0, "reread": 0.0})
        net = r["saved"] - r["rewrite"] - r["handoffs"] * r["reread"]
        table.append({"at": f"{at // 1000}k", "handoffs": r["handoffs"],
                      "saved": fmt("cost", r["saved"]), "rewrite": fmt("cost", r["rewrite"]),
                      "reread": fmt("cost", r["handoffs"] * r["reread"]),
                      "net": fmt("cost", net)})  # fmt: skip
    return {
        "bands": bands,
        "peaks": {"rows": rows, "handoff_at": f"{handoff_at // 1000}k",
                  "sessions": len(peaks)},
        "replay": table,
    }  # fmt: skip


def _foot(answers, servers, total) -> dict:
    cost = total("total", "cost")
    priced = sum(answers[s]["total"]["estimated"] * answers[s]["total"]["totals"]["cost"]
                 for s in servers)  # fmt: skip
    unpriced = sum(answers[s]["total"]["unpriced_tokens"] for s in servers)
    estimated = priced / cost if cost else 0.0
    lines = [
        f"{_pct(estimated)} of cost estimated from tokens"
        f" · {_count(unpriced)} tokens unpriced"
    ]
    for s in servers:
        b = answers[s]["total"].get("backfill")
        if b:
            lines.append(f"{s}: still counting past sessions, this answer is partial")
    return {"estimated": _pct(estimated), "unpriced": _count(unpriced),
            "servers": servers, "lines": lines}  # fmt: skip
