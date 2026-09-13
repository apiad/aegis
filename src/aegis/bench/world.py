"""A throwaway aegis world: config, fake agents on PATH, its own daemon.

The root is under /tmp because aegis resolves its project by walking up to
the nearest ``.aegis.yaml``; a root inside the workspace would pick up the
operator's config. ``AEGIS_DAEMON_DIR`` keeps the daemon out of the
operator's registry, and the daemon runs in its own session so teardown
can kill its whole process group, fake agents included, by a PID the
bench started. Inherited ``AEGIS_*`` variables are dropped: the bench may
itself be running inside an aegis session.
"""
from __future__ import annotations

import contextlib
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from aegis.bench import BenchError
from aegis.bench.launcher import Target, aegis_argv, stage_probe

_CONFIG = """agents:
  bench:
    provider: claude-code
    model: sonnet
    effort: low
    permission: full
  bench-acp:
    provider: lovelaice
    model: bench
default_agent: {default}
"""


@dataclass
class World:
    root: Path
    run_dir: Path
    env: dict[str, str]
    target: Target
    probe_dir: Path
    daemon: subprocess.Popen | None = None

    @property
    def socket(self) -> Path:
        return self.root / ".aegis" / "state" / "daemon.sock"


def build_world(run_dir: Path, target: Target, *, script: dict,
                default_agent: str = "bench", sabotage_ms: int = 0) -> World:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="aegis-bench-"))
    (root / ".aegis.yaml").write_text(_CONFIG.format(default=default_agent))
    bin_dir = root / ".bench-bin"
    bin_dir.mkdir()
    for name, module in (("claude", "aegis.bench.fake_claude"),
                         ("lovelaice-acp", "aegis.bench.fake_acp")):
        shim = bin_dir / name
        shim.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} "
                        f"-m {module} \"$@\"\n")
        shim.chmod(0o755)
    script_path = run_dir / "script.json"
    script_path.write_text(json.dumps(script))
    env = {k: v for k, v in os.environ.items() if not k.startswith("AEGIS_")}
    env.update({
        "PATH": f"{bin_dir}:{env.get('PATH', '')}",
        "AEGIS_DAEMON_DIR": str(root / ".daemons"),
        "AEGIS_IDLE_TIMEOUT": "0",
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
        "AEGIS_BENCH_SCRIPT": str(script_path),
        "AEGIS_BENCH_EMIT": str(run_dir / "emit.jsonl"),
        "AEGIS_BENCH_PROBE": str(run_dir / "probe.jsonl"),
    })
    if sabotage_ms:
        env["AEGIS_BENCH_SABOTAGE_MS"] = str(sabotage_ms)
    return World(root=root, run_dir=run_dir, env=env, target=target,
                 probe_dir=stage_probe(run_dir))


def _accepts(path: Path) -> bool:
    """Liveness is "the socket accepts", not "the file exists": the file
    appears before the listener, and a killed daemon leaves it behind."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        s.close()


def start_daemon(world: World, *, wrap: list[str] | None = None,
                 timeout_s: float = 60) -> float:
    """Start ``aegis serve`` with the probe; returns boot time in ms."""
    argv = aegis_argv(world.target, ["serve", "--cwd", str(world.root)],
                      probe_dir=world.probe_dir)
    log_path = world.run_dir / "serve.log"
    t0 = time.monotonic_ns()
    with log_path.open("wb") as log:
        world.daemon = subprocess.Popen(
            [*(wrap or []), *argv], cwd=world.root, env=world.env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True)
    deadline = time.monotonic() + timeout_s
    while not (world.socket.exists() and _accepts(world.socket)):
        if world.daemon.poll() is not None or time.monotonic() > deadline:
            tail = log_path.read_text(errors="replace")
            raise BenchError(f"daemon did not come up (rc="
                             f"{world.daemon.poll()}):\n{tail[-2000:]}")
        time.sleep(0.01)
    return (time.monotonic_ns() - t0) / 1e6


def client_argv(world: World, *, view: str,
                wrap: list[str] | None = None) -> list[str]:
    if world.target.topology == "daemon":
        args = ["attach", "--cwd", str(world.root), "--view", view]
        return aegis_argv(world.target, args, probe_dir=None)
    argv = aegis_argv(world.target, ["--cwd", str(world.root)],
                      probe_dir=world.probe_dir)
    return [*(wrap or []), *argv]


def kill_group(pid: int) -> None:
    """SIGTERM the process group led by ``pid``, then SIGKILL what stays."""
    for sig, wait_s in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            try:
                os.killpg(pid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.05)


def teardown(world: World, *, keep: bool = False) -> None:
    if world.daemon is not None:
        kill_group(world.daemon.pid)
        with contextlib.suppress(subprocess.TimeoutExpired):
            world.daemon.wait(timeout=5)
    if not keep:
        shutil.rmtree(world.root, ignore_errors=True)
