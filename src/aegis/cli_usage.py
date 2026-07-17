"""``aegis usage`` — session usage & cost analytics (read-only)."""
from __future__ import annotations

import statistics
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
import yaml

from aegis.tui.metrics import _fmt_cost, _fmt_time
from aegis.usage import build_report

app = typer.Typer(add_completion=False, no_args_is_help=False)

_BLOCKS = " ▁▂▃▄▅▆▇█"


def _money(v) -> str:
    """Format a cost via the TUI helper, coercing floats to Decimal
    (``_fmt_cost`` requires a Decimal)."""
    return _fmt_cost(v if isinstance(v, Decimal) else Decimal(str(v)))


def _bar(v: float, mx: float, width: int = 34) -> str:
    return "█" * (int(v / mx * width) if mx else 0)


def _resolve_default_agent() -> tuple[str, str]:
    from aegis.config import find_project_root
    root = find_project_root() or Path.cwd()
    cfg = {}
    p = root / ".aegis.yaml"
    if p.exists():
        cfg = yaml.safe_load(p.read_text()) or {}
    da = cfg.get("default_agent")
    agent = (cfg.get("agents") or {}).get(da, {}) if da else {}
    return agent.get("model", "opus"), agent.get("provider", "claude-code")


def _state_dir() -> Path:
    from aegis.config import find_project_root
    root = find_project_root() or Path.cwd()
    return root / ".aegis" / "state"


@app.callback(invoke_without_command=True)
def usage(
    by: str = typer.Option(None, "--by", help="month|dow|hour"),
    sessions: bool = typer.Option(False, "--sessions",
                                  help="cost distribution + top sessions"),
    tools: bool = typer.Option(False, "--tools",
                               help="tool→cost correlation"),
    since: str = typer.Option(None, "--since", help="ISO date lower bound"),
    session: str = typer.Option(None, "--session", help="single handle"),
    model: str = typer.Option(None, "--model", help="filter to one model"),
    tz: str = typer.Option(None, "--tz", help="IANA tz (default: system)"),
) -> None:
    zone = ZoneInfo(tz) if tz else None
    dmodel, dprovider = _resolve_default_agent()
    report = build_report(_state_dir(), default_model=dmodel,
                          default_provider=dprovider, since=since,
                          handle=session)
    if model:
        report.sessions = [s for s in report.sessions if s.model == model]
    if not report.sessions:
        typer.echo("No session logs found.")
        raise typer.Exit(0)

    if by:
        _render_temporal(report, by, zone)
    elif sessions:
        _render_sessions(report)
    elif tools:
        _render_tools(report)
    else:
        _render_dashboard(report, zone)


def _render_dashboard(r, zone) -> None:
    n = len(r.sessions)
    turns = r.total_turns()
    billed, gen, rep = r.total_billed(), r.total_gen(), r.total_replay()
    typer.echo("═" * 60)
    typer.echo(f"  AEGIS USAGE   {n} sessions · {turns:,} turns")
    typer.echo(f"  window: {(r.first_ts or '?')[:10]} → {(r.last_ts or '?')[:10]}")
    typer.echo("═" * 60)
    typer.echo("\nCOST")
    typer.echo(f"  billed (authoritative)   {_money(billed):>12}")
    split = gen + rep
    pct = float(rep / split * 100) if split else 0
    typer.echo(f"    ├ generation           {_money(gen):>12}")
    typer.echo(f"    └ context replay       {_money(rep):>12}   ({pct:.0f}% of split)")
    est = [s.handle for s in r.sessions if s.est]
    if est:
        typer.echo(f"  ~est (no cost_usd): {len(est)} session(s)")
    typer.echo("\nAVERAGES")
    typer.echo(f"  per session   {_money(billed / n)}")
    typer.echo(f"  per turn      {_money(billed / turns) if turns else '$0'}"
               f" · {_fmt_time(_avg_turn_secs(r))}")
    typer.echo(f"  error rate    {r.total_errors()}/{turns}")
    typer.echo("\nBY MODEL")
    for mdl, a in r.by_model():
        typer.echo(f"  {mdl:22} {_money(a['billed']):>10} · "
                   f"{a['turns']:>5} turns · {a['sessions']} sess")
    typer.echo("\nTOOLS (top 12)")
    tc = r.total_tools()
    mx = max(tc.values()) if tc else 1
    for name, c in tc.most_common(12):
        typer.echo(f"  {name:16} {c:>6}  {_bar(c, mx)}")
    typer.echo("\nTOP 5 SESSIONS (billed)")
    for s in sorted(r.sessions, key=lambda s: -s.billed_usd)[:5]:
        typer.echo(f"  {s.handle:28} {_money(s.billed_usd):>10} · "
                   f"{s.turns} turns")
    _render_sparkline(r, zone)


def _avg_turn_secs(r) -> float:
    durs = [tr.duration_ms for tr in r.turns if tr.duration_ms]
    return (sum(durs) / len(durs) / 1000) if durs else 0.0


def _render_sparkline(r, zone) -> None:
    days = r.by_day(zone)
    if not days:
        return
    mx = max(v for _, v in days) or 1
    typer.echo("\nDAILY BILLED (last 28d)")
    line = "".join(_BLOCKS[min(8, int(v / mx * 8))] for _, v in days[-28:])
    typer.echo(f"  {line}   peak {_money(mx)}/day")


def _render_temporal(r, kind, zone) -> None:
    if kind == "hour":
        data = r.by_hour(zone)
    elif kind == "month":
        data = r.by_month(zone)
    elif kind == "dow":
        data = r.by_dow(zone)
    else:
        typer.echo("--by must be one of: month, dow, hour")
        raise typer.Exit(1)
    mx = max((v for _, v in data), default=1)
    typer.echo(f"TURNS BY {kind.upper()}")
    for lab, v in data:
        typer.echo(f"  {lab:9} {_bar(v, mx, 40):<40} {v}")


def _render_sessions(r) -> None:
    d = r.distribution()
    typer.echo("COST-PER-SESSION DISTRIBUTION (billed)")
    typer.echo(f"  n={d['n']}  min={_money(d['min'])}  "
               f"p50={_money(d['p50'])}  p90={_money(d['p90'])}  "
               f"p99={_money(d['p99'])}  max={_money(d['max'])}")
    typer.echo(f"  mean={_money(d['mean'])}")
    typer.echo("\nTOP 15 SESSIONS")
    for s in sorted(r.sessions, key=lambda s: -s.billed_usd)[:15]:
        flag = " ~est" if s.est else ""
        typer.echo(f"  {s.handle:28} {_money(s.billed_usd):>10} · "
                   f"{s.turns:>4} turns · {sum(s.tools.values()):>4} tools{flag}")


def _render_tools(r) -> None:
    base_turns = [float(tr.gen_usd + tr.replay_usd) for tr in r.turns]
    base = statistics.mean(base_turns) if base_turns else 0.0
    typer.echo(f"TOOL → COST CORRELATION   baseline turn {_money(base)}")
    for name, avg, cnt in r.tool_correlation(min_turns=1)[:15]:
        mult = (avg / base) if base else 0
        typer.echo(f"  {name:16} {_money(avg):>10} ({mult:>4.1f}x) · "
                   f"{cnt} turns")
