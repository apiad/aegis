"""Tables for a run, in the terminal and as Markdown."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

from aegis.bench.metrics import METRICS


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.3f}".rstrip("0").rstrip(".")
    return str(v)


def _unit(metric: str) -> str:
    spec = METRICS.get(metric)
    return spec.unit if spec else "?"


def _first_line(text: str) -> str:
    return (text.strip().splitlines() or [""])[0]


def print_run(summary: dict, console: Console) -> None:
    fp = summary["fingerprint"]
    console.print(f"[bold]{summary['run_id']}[/]  {fp['aegis_build']} "
                  f"{fp['topology']}  {fp['host']}  {fp['size']}")
    for name, sc in summary["scenarios"].items():
        title = f"{name} [{sc['status']}]"
        if sc["reason"]:
            title += f" {_first_line(sc['reason'])}"
        t = Table("metric", "median", "unit", "repeats", title=title)
        for metric, value in sc.get("median", {}).items():
            reps = [rep.get(metric) for rep in sc["repeats"]]
            t.add_row(metric, _fmt(value), _unit(metric),
                      " ".join(_fmt(r) for r in reps))
        console.print(t)
        bad = sorted({g["name"] for rep in sc.get("gates", []) for g in rep
                      if not g["ok"]})
        if bad:
            console.print(f"  [red]failed gates: {', '.join(bad)}[/]")


def render_markdown(summary: dict) -> str:
    fp = summary["fingerprint"]
    lines = [f"# aegis bench {summary['run_id']}", "",
             f"{fp['aegis_build']} ({fp['topology']}) on {fp['host']}, "
             f"{fp['cpu']}, {fp['size']}, Textual {fp['textual']}", ""]
    for name, sc in summary["scenarios"].items():
        lines += [f"## {name}: {sc['status']}", ""]
        if sc["reason"]:
            lines += [_first_line(sc["reason"]), ""]
        if sc.get("median"):
            lines += ["| metric | median | unit |", "|---|---|---|"]
            lines += [f"| {m} | {_fmt(v)} | {_unit(m)} |"
                      for m, v in sc["median"].items()]
            lines.append("")
    return "\n".join(lines)


_COLOR = {"regressed": "red", "improved": "green", "changed": "yellow",
          "new": "cyan", "gone": "cyan"}


def print_compare(rows_by_scenario: dict, console: Console, *,
                  a_label: str, b_label: str,
                  all_rows: bool = False) -> None:
    """One table per scenario; noise and unchanged rows are hidden unless
    ``all_rows``, so what is printed is what moved."""
    for name, rows in rows_by_scenario.items():
        shown = [r for r in rows
                 if all_rows or r.verdict not in ("noise", "same")]
        t = Table("metric", a_label, b_label, "Δ%", "verdict", title=name)
        for r in shown:
            color = _COLOR.get(r.verdict)
            delta = "-" if r.delta_pct is None else f"{r.delta_pct:+.1f}"
            label = f"[{color}]{r.verdict}[/]" if color else r.verdict
            t.add_row(r.metric, _fmt(r.a), _fmt(r.b), delta, label)
        if shown:
            console.print(t)
        else:
            console.print(f"{name}: no change beyond noise "
                          f"({len(rows)} metrics)")


def print_history(summaries: list[dict], metrics: list[str], scenario: str,
                  console: Console) -> None:
    t = Table("build", "topology", *metrics, title=scenario)
    for s in summaries:
        med = s["scenarios"].get(scenario, {}).get("median", {})
        t.add_row(s["fingerprint"]["aegis_build"],
                  s["fingerprint"]["topology"],
                  *(_fmt(med.get(m)) for m in metrics))
    console.print(t)
