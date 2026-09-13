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


@app.command("compare")
def compare_cmd(
    a: str = typer.Argument(..., help="Run id, run dir or summary path."),
    b: str = typer.Argument(None, help="Second run; omit with --baseline."),
    baseline: str = typer.Option(None, "--baseline",
                                 help="'latest-release' or a summary."),
    force: bool = typer.Option(False, "--force",
                               help="Compare across hosts or sizes."),
    all_rows: bool = typer.Option(False, "--all",
                                  help="Include noise and unchanged rows."),
) -> None:
    """Compare two runs metric by metric; exit 1 if anything regressed."""
    from aegis.bench import BenchError
    from aegis.bench.compare import (
        compare, latest_release, load_summary, min_repeats)
    from aegis.bench.report import print_compare
    try:
        if baseline:
            base = (latest_release() if baseline == "latest-release"
                    else load_summary(baseline))
            if base is None:
                raise BenchError("no saved release in bench/history")
            left, right = base, load_summary(a)
        else:
            if b is None:
                raise typer.BadParameter("give two runs, or one and --baseline")
            left, right = load_summary(a), load_summary(b)
        rows = compare(left, right, force=force)
    except BenchError as exc:
        _console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc
    thin = min_repeats(left, right)
    if thin < 3:
        _console.print(f"[yellow]note:[/] {thin} repeat(s) on the thinner "
                       "side; with fewer than 3, repeat ranges cannot tell a "
                       "real change from noise")
    from aegis.bench.host import BUSY_CPU_PCT
    busy = [s.get("run_id", "?") for s in (left, right)
            if any(sc.get("median", {}).get("host.cpu_busy_pct", 0)
                   > BUSY_CPU_PCT for sc in s["scenarios"].values())]
    if busy:
        _console.print(f"[yellow]note:[/] {', '.join(busy)} ran on a busy "
                       f"host (CPU above {BUSY_CPU_PCT:.0f}% busy in a "
                       "window); a timing verdict may reflect the machine, "
                       "not aegis")
    print_compare(rows, _console, a_label=left.get("run_id", "a"),
                  b_label=right.get("run_id", "b"), all_rows=all_rows)
    regressed = any(r.verdict == "regressed"
                    for rs in rows.values() for r in rs)
    raise typer.Exit(1 if regressed else 0)


_HISTORY_METRICS = ["latency.marker_ms.p50", "latency.marker_ms.p95",
                    "render.tick_ms.p95", "loop.lag_ms.p99",
                    "cpu.daemon_s_per_s", "mem.rss_peak_mb"]


@app.command("history")
def history_cmd(
    metric: list[str] = typer.Option(None, "--metric", "-m"),
    scenario: str = typer.Option("block-stream", "--scenario", "-s"),
) -> None:
    """Show metrics across the saved summaries for this host."""
    import json

    from aegis.bench.compare import version_key
    from aegis.bench.report import print_history
    from aegis.bench.runner import history_dir
    base = history_dir()
    files = sorted(base.glob("*.json"),
                   key=lambda p: (version_key(p) or (0, 0, 0), p.stem)) \
        if base.exists() else []
    if not files:
        _console.print(f"no saved summaries in {base}")
        return
    print_history([json.loads(p.read_text()) for p in files],
                  list(metric or _HISTORY_METRICS), scenario, _console)


@app.command("record")
def record_cmd(
    partial: bool = typer.Option(False, "--partial",
                                 help="Record with --include-partial-messages."),
    prompt: str = typer.Option(None, "--prompt",
                               help="Replace the default recording prompt."),
    out: Path = typer.Option(None, "--out",
                             help="Fixture path; defaults to the packaged one."),
    model: str = typer.Option("sonnet", "--model"),
) -> None:
    """Record a real claude session as a replay fixture. Costs tokens."""
    from aegis.bench import BenchError
    from aegis.bench.record import RECORD_PROMPT, record_fixture
    name = "claude-stream" if partial else "claude-blocks"
    dest = out or Path(__file__).parent / "bench" / "fixtures" / f"{name}.jsonl"
    try:
        n = record_fixture(dest, partial=partial,
                           prompt=prompt or RECORD_PROMPT, model=model)
    except BenchError as exc:
        _console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc
    _console.print(f"recorded {n} steps -> {dest}")
