"""Text rendering of a UsageReport into monospace ASCII lines.

Pure functions returning ``list[str]`` so both the CLI (``aegis usage``,
via typer.echo) and the ``/usage`` slash command (via CommandResult.body)
share one renderer. No Textual import — safe on the web path.
"""
from __future__ import annotations

import statistics
from decimal import Decimal
from zoneinfo import ZoneInfo

from aegis.tui.metrics import _fmt_cost, _fmt_time

_BLOCKS = " ▁▂▃▄▅▆▇█"


def _money(v) -> str:
    return _fmt_cost(v if isinstance(v, Decimal) else Decimal(str(v)))


def _bar(v: float, mx: float, width: int = 34) -> str:
    return "█" * (int(v / mx * width) if mx else 0)


def _avg_turn_secs(r) -> float:
    durs = [tr.duration_ms for tr in r.turns if tr.duration_ms]
    return (sum(durs) / len(durs) / 1000) if durs else 0.0


def dashboard_lines(r, zone: ZoneInfo | None = None) -> list[str]:
    n = len(r.sessions)
    turns = r.total_turns()
    billed, gen, rep = r.total_billed(), r.total_gen(), r.total_replay()
    out = ["═" * 60,
           f"  AEGIS USAGE   {n} sessions · {turns:,} turns",
           f"  window: {(r.first_ts or '?')[:10]} → {(r.last_ts or '?')[:10]}",
           "═" * 60,
           "", "COST",
           f"  billed (authoritative)   {_money(billed):>12}"]
    split = gen + rep
    pct = float(rep / split * 100) if split else 0
    out.append(f"    ├ generation           {_money(gen):>12}")
    out.append(f"    └ context replay       {_money(rep):>12}   ({pct:.0f}% of split)")
    est = [s.handle for s in r.sessions if s.est]
    if est:
        out.append(f"  ~est (no cost_usd): {len(est)} session(s)")
    out += ["", "AVERAGES",
            f"  per session   {_money(billed / n)}",
            f"  per turn      {(_money(billed / turns) if turns else '$0')}"
            f" · {_fmt_time(_avg_turn_secs(r))}",
            f"  error rate    {r.total_errors()}/{turns}",
            "", "BY MODEL"]
    for mdl, a in r.by_model():
        out.append(f"  {mdl:22} {_money(a['billed']):>10} · "
                   f"{a['turns']:>5} turns · {a['sessions']} sess")
    out += ["", "TOOLS (top 12)"]
    tc = r.total_tools()
    mx = max(tc.values()) if tc else 1
    for name, c in tc.most_common(12):
        out.append(f"  {name:16} {c:>6}  {_bar(c, mx)}")
    out += ["", "TOP 5 SESSIONS (billed)"]
    for s in sorted(r.sessions, key=lambda s: -s.billed_usd)[:5]:
        out.append(f"  {s.handle:28} {_money(s.billed_usd):>10} · "
                   f"{s.turns} turns")
    out += _sparkline_lines(r, zone)
    return out


def _sparkline_lines(r, zone) -> list[str]:
    days = r.by_day(zone)
    if not days:
        return []
    mx = max(v for _, v in days) or 1
    line = "".join(_BLOCKS[min(8, int(v / mx * 8))] for _, v in days[-28:])
    return ["", "DAILY BILLED (last 28d)",
            f"  {line}   peak {_money(mx)}/day"]


def temporal_lines(r, kind: str, zone: ZoneInfo | None = None) -> list[str]:
    if kind == "hour":
        data = r.by_hour(zone)
    elif kind == "month":
        data = r.by_month(zone)
    elif kind == "dow":
        data = r.by_dow(zone)
    else:
        return ["--by must be one of: month, dow, hour"]
    mx = max((v for _, v in data), default=1)
    out = [f"TURNS BY {kind.upper()}"]
    for lab, v in data:
        out.append(f"  {lab:9} {_bar(v, mx, 40):<40} {v}")
    return out


def sessions_lines(r) -> list[str]:
    d = r.distribution()
    out = ["COST-PER-SESSION DISTRIBUTION (billed)",
           f"  n={d['n']}  min={_money(d['min'])}  "
           f"p50={_money(d['p50'])}  p90={_money(d['p90'])}  "
           f"p99={_money(d['p99'])}  max={_money(d['max'])}",
           f"  mean={_money(d['mean'])}",
           "", "TOP 15 SESSIONS"]
    for s in sorted(r.sessions, key=lambda s: -s.billed_usd)[:15]:
        flag = " ~est" if s.est else ""
        out.append(f"  {s.handle:28} {_money(s.billed_usd):>10} · "
                   f"{s.turns:>4} turns · {sum(s.tools.values()):>4} tools{flag}")
    return out


def tools_lines(r) -> list[str]:
    base_turns = [float(tr.gen_usd + tr.replay_usd) for tr in r.turns]
    base = statistics.mean(base_turns) if base_turns else 0.0
    out = [f"TOOL → COST CORRELATION   baseline turn {_money(base)}"]
    for name, avg, cnt in r.tool_correlation(min_turns=1)[:15]:
        mult = (avg / base) if base else 0
        out.append(f"  {name:16} {_money(avg):>10} ({mult:>4.1f}x) · "
                   f"{cnt} turns")
    return out
