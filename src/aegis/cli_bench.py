"""``aegis bench``: measure rendering, latency, CPU and memory.

Imports here stay light: ``aegis.cli`` loads this module on every start,
so the bench machinery is imported inside each command.
See ``know-how/benchmarking.md``.
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(help="Benchmark the TUI against a real daemon and client.",
                  no_args_is_help=True)
_console = Console()


def _size(value: str) -> tuple[int, int]:
    try:
        cols, rows = (int(p) for p in value.lower().split("x"))
    except ValueError as exc:
        raise typer.BadParameter("expected COLSxROWS, e.g. 120x40") from exc
    return cols, rows


@app.command("run")
def run_cmd(
    scenario: list[str] = typer.Option(None, "--scenario", "-s",
                                       help="Scenario name (repeatable)."),
    quick: bool = typer.Option(False, "--quick",
                               help="A fast subset, one repeat."),
    repeat: int = typer.Option(3, "--repeat", min=1),
    size: str = typer.Option("120x40", "--size", help="COLSxROWS."),
    target: str = typer.Option(None, "--target",
                               help="X.Y.Z from PyPI, or a python path."),
    speed: float = typer.Option(1.0, "--speed", help="Replay speed factor."),
    profile: bool = typer.Option(False, "--profile",
                                 help="Record a py-spy speedscope profile."),
    sabotage: int = typer.Option(0, "--sabotage",
                                 help="Sleep MS in every streaming paint."),
    save: bool = typer.Option(False, "--save",
                              help="Copy the summary to bench/history."),
    out: Path = typer.Option(None, "--out", help="Runs directory."),
    keep: bool = typer.Option(False, "--keep",
                              help="Keep the /tmp worlds for inspection."),
) -> None:
    """Run scenarios and write a summary."""
    from aegis.bench import BenchError
    from aegis.bench.report import print_run, render_markdown
    from aegis.bench.runner import RunOptions, failed, run
    from aegis.bench.scenarios import DEFAULT, QUICK, SCENARIOS
    names = list(scenario or (QUICK if quick else DEFAULT))
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        raise typer.BadParameter(f"unknown scenario(s): {unknown}; "
                                 "see `aegis bench list`")
    cols, rows = _size(size)
    opts = RunOptions(scenarios=names, repeat=1 if quick else repeat,
                      cols=cols, rows=rows, target=target, speed=speed,
                      profile=profile, sabotage_ms=sabotage, save=save,
                      out=out, keep=keep)
    try:
        summary, run_dir = run(opts, _console)
    except BenchError as exc:
        _console.print(f"[red]bench failed:[/] {exc}")
        raise typer.Exit(2) from exc
    print_run(summary, _console)
    (run_dir / "report.md").write_text(render_markdown(summary))
    raise typer.Exit(1 if failed(summary) else 0)


@app.command("list")
def list_cmd() -> None:
    """List scenarios and recent runs."""
    from rich.table import Table

    from aegis.bench.runner import runs_dir
    from aegis.bench.scenarios import DEFAULT, QUICK, SCENARIOS
    t = Table("scenario", "default", "quick", "description")
    for s in SCENARIOS.values():
        t.add_row(s.name, "yes" if s.name in DEFAULT else "",
                  "yes" if s.name in QUICK else "", s.description)
    _console.print(t)
    base = runs_dir()
    runs = sorted(base.glob("*/summary.json"))[-10:] if base.exists() else []
    if runs:
        _console.print("recent runs:")
    for p in runs:
        _console.print(f"  {p.parent.name}")
