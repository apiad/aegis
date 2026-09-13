"""Which aegis to run, and how to start it with the probe inside.

The probe goes in through ``python -c`` so production code carries no
benchmark hook, and so an old release from PyPI can be measured with
today's probe. Topology is read from the target itself: a build whose CLI
has ``attach`` runs as daemon + client, anything older runs the TUI
in-process.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from aegis.bench import BenchError

_INTROSPECT = (
    "import json,sys,importlib.metadata as m,aegis,aegis.cli as c\n"
    "try:\n from aegis.version import BUILD as b\n"
    "except Exception:\n b=m.version('aegis-harness')\n"
    "print(json.dumps({'version':m.version('aegis-harness'),'build':b,"
    "'aegis_file':aegis.__file__,'textual':m.version('textual'),"
    "'rich':m.version('rich'),'python':sys.version.split()[0],"
    "'attach':hasattr(c,'attach')}))")


@dataclass(frozen=True)
class Target:
    label: str
    python: tuple[str, ...]
    version: str
    build: str
    aegis_file: str
    textual: str
    rich: str
    python_version: str
    topology: str


def resolve_target(spec: str | None) -> Target:
    """None is this interpreter; ``X.Y.Z`` is that release from PyPI via
    uvx; anything else is a path to another python with aegis installed."""
    if spec is None:
        python, label = (sys.executable,), "current"
    elif re.fullmatch(r"\d+\.\d+\.\d+", spec):
        python = ("uvx", "--from", f"aegis-harness=={spec}", "python")
        label = spec
    else:
        path = Path(spec).expanduser()
        if not path.exists():
            raise BenchError(f"target {spec!r} is neither X.Y.Z nor a python")
        python, label = (str(path),), str(path)
    proc = subprocess.run([*python, "-c", _INTROSPECT], capture_output=True,
                          text=True, timeout=600)
    if proc.returncode != 0:
        raise BenchError(f"target {label}: cannot import aegis:\n"
                         f"{proc.stderr[-2000:]}")
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    return Target(label=label, python=python, version=info["version"],
                  build=info["build"], aegis_file=info["aegis_file"],
                  textual=info["textual"], rich=info["rich"],
                  python_version=info["python"],
                  topology="daemon" if info["attach"] else "in-process")


def stage_probe(run_dir: Path) -> Path:
    """Copy the probe beside the run under a name no aegis build uses, so
    the target imports this probe and never an ``aegis.bench`` of its own."""
    dest = Path(run_dir) / "_probe"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name("probe.py"),
                    dest / "aegis_bench_probe.py")
    return dest


def aegis_argv(target: Target, args: list[str], *,
               probe_dir: Path | None) -> list[str]:
    head = ""
    if probe_dir is not None:
        head = (f"sys.path.insert(0,{str(probe_dir)!r});"
                "import aegis_bench_probe;aegis_bench_probe.install();")
    code = (f"import sys;{head}sys.argv=['aegis',*{list(args)!r}];"
            "from aegis.cli import main;main()")
    return [*target.python, "-c", code]
