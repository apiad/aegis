"""Run scenarios × repeats against a target and write the run directory."""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import platform
import socket
import subprocess
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from aegis.bench import BenchError, ScenarioSkipped
from aegis.bench.launcher import Target, resolve_target
from aegis.bench.metrics import repeat_metrics, summarize
from aegis.bench.scenarios import SCENARIOS, ScenarioContext


@dataclass
class RunOptions:
    scenarios: list[str] = field(default_factory=list)
    repeat: int = 3
    cols: int = 120
    rows: int = 40
    target: str | None = None
    speed: float = 1.0
    profile: bool = False
    sabotage_ms: int = 0
    save: bool = False
    out: Path | None = None
    keep: bool = False


def runs_dir() -> Path:
    home = os.environ.get("AEGIS_BENCH_HOME")
    base = Path(home) if home else Path.home() / ".aegis" / "bench"
    return base / "runs"


def history_dir() -> Path:
    """``bench/history/<host>`` in the checkout this bench is imported from."""
    import aegis
    root = Path(aegis.__file__).resolve().parents[2]
    pyproject = root / "pyproject.toml"
    if not (pyproject.exists()
            and 'name = "aegis-harness"' in pyproject.read_text()):
        raise BenchError("--save needs aegis installed editable from its "
                         "checkout; bench/history lives in the repo")
    return root / "bench" / "history" / socket.gethostname()


def _read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def _git(aegis_file: str) -> tuple[str, bool]:
    root = Path(aegis_file).resolve().parents[2]
    if not (root / ".git").exists():
        return "", False
    sha = subprocess.run(["git", "-C", str(root), "rev-parse", "--short",
                          "HEAD"], capture_output=True, text=True)
    dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain",
                            "--", "src"], capture_output=True, text=True)
    return sha.stdout.strip(), bool(dirty.stdout.strip())


def fingerprint(target: Target, opts: RunOptions) -> dict:
    import aegis.version
    cpu = next((line.split(":", 1)[1].strip()
                for line in _read("/proc/cpuinfo").splitlines()
                if line.startswith("model name")), platform.processor())
    sha, dirty = _git(target.aegis_file)
    return {
        "host": socket.gethostname(), "cpu": cpu, "cores": os.cpu_count(),
        "governor": _read(
            "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        "size": f"{opts.cols}x{opts.rows}", "speed": opts.speed,
        "sabotage_ms": opts.sabotage_ms, "target": target.label,
        "topology": target.topology, "aegis_version": target.version,
        "aegis_build": target.build, "aegis_file": target.aegis_file,
        "git_sha": sha, "git_dirty": dirty, "python": target.python_version,
        "textual": target.textual, "rich": target.rich,
        "bench_build": aegis.version.BUILD,
        "created": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }


def failed(summary: dict) -> bool:
    for sc in summary["scenarios"].values():
        if sc["status"] == "failed":
            return True
        if any(not g["ok"] for rep in sc.get("gates", []) for g in rep):
            return True
    return False


def _one_line(text: str) -> str:
    return (text.strip().splitlines() or [""])[0]


def run(opts: RunOptions, console: Console) -> tuple[dict, Path]:
    target = resolve_target(opts.target)
    fp = fingerprint(target, opts)
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    run_id = f"{stamp}-{target.label.replace('/', '_')}"
    run_dir = (opts.out or runs_dir()) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"[bold]aegis bench[/] {run_id}  target {target.build} "
                  f"({target.topology})  -> {run_dir}")
    results: dict[str, dict] = {}
    for name in opts.scenarios:
        sc = SCENARIOS[name]
        res: dict = {"status": "ok", "reason": "", "repeats": [], "gates": []}
        results[name] = res
        if target.topology not in sc.topologies:
            res.update(status="skipped",
                       reason=f"not applicable to {target.topology}")
            console.print(f"  {name}: skipped ({res['reason']})")
            continue
        for r in range(opts.repeat):
            rep_dir = run_dir / name / f"r{r}"
            ctx = ScenarioContext(rep_dir, target, cols=opts.cols,
                                  rows=opts.rows, speed=opts.speed,
                                  sabotage_ms=opts.sabotage_ms,
                                  profile=opts.profile, keep=opts.keep)
            try:
                sc.fn(ctx)
            except ScenarioSkipped as skip:
                res.update(status="skipped", reason=str(skip))
            except Exception as exc:  # noqa: BLE001 — a run reports, never crashes
                res.update(status="failed",
                           reason=f"{type(exc).__name__}: {exc}")
                (rep_dir / "error.txt").write_text(traceback.format_exc())
                with contextlib.suppress(Exception):
                    ctx.dump_screens()
            finally:
                try:
                    ctx.close()
                except Exception as exc:  # noqa: BLE001
                    if res["status"] == "ok":
                        res.update(status="failed",
                                   reason=f"teardown: {exc}")
            if res["status"] != "ok":
                console.print(f"  {name} r{r}: {res['status']} "
                              f"({_one_line(res['reason'])})")
                break
            metrics, gates = repeat_metrics(rep_dir)
            res["repeats"].append(metrics)
            res["gates"].append(gates)
            bad = [g["name"] for g in gates if not g["ok"]]
            note = f", [red]gates failed: {bad}[/]" if bad else ""
            console.print(f"  {name} r{r}: {len(metrics)} metrics{note}")
    summary = summarize(run_id, fp, results)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    if opts.save:
        console.print(f"saved {save_history(summary)}")
    return summary, run_dir


def save_history(summary: dict) -> Path:
    fp = summary["fingerprint"]
    base = history_dir()
    base.mkdir(parents=True, exist_ok=True)
    name = fp["aegis_version"]
    if fp["target"] == "current" and fp["git_sha"]:
        name += f"-{fp['git_sha']}" + ("-dirty" if fp["git_dirty"] else "")
    path = base / f"{name}.json"
    path.write_text(json.dumps(summary, indent=2))
    return path
