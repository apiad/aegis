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
