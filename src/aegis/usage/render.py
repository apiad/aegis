"""Text rendering of usage answers (query.py) into monospace lines.

Pure functions returning ``list[str]``, which ``aegis usage`` echoes. Python
decides every number; these only lay them out.
"""

from __future__ import annotations

from decimal import Decimal

from .context import BANDS, band_label
from .query import Answer

_BLOCKS = " ▁▂▃▄▅▆▇█"


def _money(v) -> str:
    """``$X.XX``, or cents below a dollar so a small cost stays visible."""
    usd = v if isinstance(v, Decimal) else Decimal(str(v))
    cents = usd * 100
    if cents < 1:
        return f"{cents.quantize(Decimal('0.1'))}¢"
    if usd < 1:
        return f"{int(cents)}¢"
    return f"${usd.quantize(Decimal('0.01'))}"


def _num(v) -> str:
    return f"{int(v):,}"


def _hours(secs: float) -> str:
    return f"{secs / 3600:.1f} h"


def _bar(v: float, mx: float, width: int = 34) -> str:
    return "█" * (int(v / mx * width) if mx else 0)


def value(measure: str, v) -> str:
    if measure in ("cost", "sub", "whatif"):
        return _money(v)
    if measure in ("secs", "waits"):
        return _hours(v)
    return _num(v)


def _section(title: str, rows: list[dict], key: str, names: dict | None = None,
             top: int = 8) -> list[str]:  # fmt: skip
    rows = sorted(rows, key=lambda r: -r["cost"])[:top]
    if not rows:
        return []
    out = ["", title]
    for r in rows:
        label = (names or {}).get(r[key], r[key]) or "(none)"
        out.append(
            f"  {str(label)[:30]:30} {_money(r['cost']):>10} · "
            f"{r['turns']:>5} turns · {r['sessions']} sess"
        )
    return out


def dashboard_lines(
    total: Answer,
    by: dict[str, Answer],
    daily: Answer,
    window: str,
    handles: dict[str, str],
) -> list[str]:
    """The dashboard: totals, then the cost by each dimension in ``by``."""
    t = total.totals
    out = [
        "═" * 60,
        f"  AEGIS USAGE   {t['sessions']} sessions · {t['turns']:,} turns"
        f" · {_num(t['tokens'])} tokens",
        f"  window: {window}",
        "═" * 60,
        "",
        "COST",
        f"  at API prices            {_money(t['cost']):>12}",
        f"  estimated from tokens    {total.estimated * 100:>11.0f}%",
    ]
    if total.unpriced_tokens:
        out.append(f"  unpriced tokens          {_num(total.unpriced_tokens):>12}")
    out += [
        "",
        "TIME",
        f"  agents working           {_hours(t['secs']):>12}",
        f"  waiting between turns    {_hours(t['waits']):>12}",
        f"  tool calls               {_num(t['calls']):>12}",
        f"  tool errors              {_num(t['errors']):>12}"
        f"   ({_num(t['exits'])} were shell exits)",
    ]
    for dim, title in (
        ("model", "BY MODEL"),
        ("harness", "BY HARNESS"),
        ("mode", "DRIVEN BY"),
        ("purpose", "BY PURPOSE"),
        ("activity", "BY ACTIVITY"),
        ("owner", "BY OWNER"),
        ("repo", "BY REPO"),
    ):
        if dim in by:
            out += _section(title, by[dim].rows, dim)
    if "session" in by:
        out += _section("TOP 5 SESSIONS", by["session"].rows, "session", handles, 5)
    out += outcome_lines(total)
    days = [r["cost"] for r in sorted(daily.rows, key=lambda r: r["bucket"])][-28:]
    if days:
        mx = max(days) or 1
        line = "".join(_BLOCKS[min(8, int(v / mx * 8))] for v in days)
        out += [
            "",
            "DAILY COST (last 28 days with work)",
            f"  {line}   peak {_money(mx)}/day",
        ]
    return out


def outcome_lines(a: Answer) -> list[str]:
    if not a.outcomes:
        return []
    keys = ("commits", "prs_opened", "prs_merged", "issues")
    sums = {k: sum(o[k] for o in a.outcomes) for k in keys}
    out = [
        "",
        "OUTCOMES (from the journal)",
        f"  {sums['commits']} commits · {sums['prs_opened']} PRs opened ·"
        f" {sums['prs_merged']} merged · {sums['issues']} issues closed",
    ]
    if sums["prs_merged"]:
        out.append(
            f"  cost per merged PR       {_money(a.totals['cost'] / sums['prs_merged']):>12}"
        )
    return out


def table_lines(a: Answer, split: tuple[str, ...], by: str | None, measure: str,
                names: dict | None = None) -> list[str]:  # fmt: skip
    """One line per row: the split's values, the bucket, the measure and a bar."""
    cols = [*split, *(["bucket"] if by else [])]
    rows = a.rows
    if by:
        rows = sorted(rows, key=lambda r: (r["bucket"], -r[measure]))
    else:
        rows = sorted(rows, key=lambda r: -r[measure])
    mx = max((r[measure] for r in rows), default=0)

    def label(r, c):
        v = r.get(c)
        if c == "context":
            return band_label(BANDS.index(v))
        return str((names or {}).get(v, v) if c == "session" else v or "(none)")

    widths = {c: max([len(c), *(len(label(r, c)) for r in rows)]) for c in cols}
    out = ["  ".join(c.upper().ljust(widths[c]) for c in cols) + f"  {measure.upper()}"]
    for r in rows:
        cells = "  ".join(label(r, c)[:40].ljust(min(widths[c], 40)) for c in cols)
        out.append(
            f"{cells}  {value(measure, r[measure]):>10}  {_bar(r[measure], mx, 30)}"
        )
    out.append(f"total  {value(measure, a.totals[measure])}")
    return out
