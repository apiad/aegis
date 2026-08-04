"""One persistent SSH connection to one execution host.

Owns the ControlMaster subprocess, the reverse tunnel that carries the
local MCP port to the remote side, and the one-time preflight that turns
"the harness isn't installed there" into a sentence instead of a
mysterious EOF.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import shlex
from pathlib import Path

from aegis.hosts.errors import HostError
from aegis.hosts.models import HostSpec

# ssh reports a dynamically-allocated reverse-forward port on stderr:
#   Allocated port 41573 for remote forward to 127.0.0.1:8931
_ALLOCATED = re.compile(r"Allocated port (\d+) for remote forward")

# How long to wait for the master to come up and report its port.
OPEN_TIMEOUT_S = 20.0


def parse_allocated_port(line: str) -> int | None:
    """The remote port sshd chose for our reverse forward, if this line
    announces one."""
    m = _ALLOCATED.search(line)
    return int(m.group(1)) if m else None


def master_argv(spec: HostSpec, control_path: str,
                mcp_port: int) -> list[str]:
    """The backgrounded ControlMaster that every session multiplexes over.

    ``-R 0:…`` asks sshd to pick a free remote port, so two aegis
    instances targeting the same host cannot collide. ``LogLevel=INFO``
    is explicit because the allocated-port announcement is an INFO
    message and a user's ssh_config may otherwise be quieter than that.
    """
    remote_port = spec.remote_mcp_port if spec.remote_mcp_port else 0
    return [
        "ssh", "-M", "-N",
        "-o", f"ControlPath={control_path}",
        "-o", "ControlMaster=yes",
        "-o", "ControlPersist=60s",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "LogLevel=INFO",
        "-R", f"{remote_port}:127.0.0.1:{mcp_port}",
        *spec.ssh_opts,
        spec.ssh,
    ]


def preflight_command(binary: str, cwd: str) -> str:
    """Confirm the harness exists and the working tree is there."""
    return (f"command -v {shlex.quote(binary)} >/dev/null 2>&1 "
            f"&& test -d {shlex.quote(cwd)}")


class HostConnection:
    """Lazily-opened, shared-by-every-session link to one host."""

    def __init__(self, spec: HostSpec, control_path: str,
                 mcp_port: int) -> None:
        self._spec = spec
        self.control_path = control_path
        self._mcp_port = mcp_port
        self._proc: asyncio.subprocess.Process | None = None
        self._remote_port: int | None = None
        self._lock = asyncio.Lock()
        self._stderr: list[str] = []

    @property
    def remote_mcp_url(self) -> str:
        """What the remote harness should be told the MCP plane is."""
        if self._remote_port is None:
            raise HostError(f"host {self._spec.name!r} is not open")
        return f"http://127.0.0.1:{self._remote_port}/mcp/"

    async def ensure_open(self) -> None:
        """Open the master if it isn't, exactly once.

        Concurrent spawns on the same host share one lock, so two tabs
        opened at the same moment share a single master rather than
        racing to create two.
        """
        async with self._lock:
            if self._proc is not None and self._proc.returncode is None:
                return
            Path(self.control_path).parent.mkdir(parents=True, exist_ok=True)
            await self._open()

    async def _open(self) -> None:
        argv = master_argv(self._spec, self.control_path, self._mcp_port)
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._remote_port = await self._await_port()

    async def _await_port(self) -> int:
        """Read ssh's stderr until it announces the forward, or fail.

        A pinned ``remote_mcp_port`` skips the parse entirely — that is
        the escape hatch for an sshd whose phrasing we don't match.
        """
        if self._spec.remote_mcp_port:
            return self._spec.remote_mcp_port

        async def read_until_port() -> int:
            assert self._proc and self._proc.stderr
            async for raw in self._proc.stderr:
                line = raw.decode("utf-8", "replace").rstrip()
                self._stderr.append(line)
                port = parse_allocated_port(line)
                if port is not None:
                    return port
            raise HostError(
                f"host {self._spec.name!r}: ssh exited before announcing a "
                f"reverse-forward port.\n"
                f"  ssh stderr:\n{self.stderr_text() or '(empty)'}")

        try:
            return await asyncio.wait_for(read_until_port(), OPEN_TIMEOUT_S)
        except asyncio.TimeoutError:
            await self.close()
            raise HostError(
                f"host {self._spec.name!r}: timed out after "
                f"{OPEN_TIMEOUT_S:.0f}s waiting for ssh to open the "
                f"reverse forward. Set `remote_mcp_port:` on the host to "
                f"skip port auto-detection.\n"
                f"  ssh stderr:\n{self.stderr_text() or '(empty)'}") from None

    async def preflight(self, binary: str, cwd: str) -> None:
        """Confirm the harness and the working tree exist, once per host."""
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-T",
            "-o", f"ControlPath={self.control_path}",
            *self._spec.ssh_opts, self._spec.ssh,
            preflight_command(binary, cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise HostError(
                f"{self._spec.name}: preflight failed — either {binary!r} "
                f"is not on PATH there or {cwd} does not exist.\n"
                f"  ssh stderr: "
                f"{err.decode('utf-8', 'replace').strip() or '(empty)'}")

    def stderr_text(self) -> str:
        """The last of what ssh said, for error messages."""
        return "\n".join(self._stderr[-40:])

    async def close(self) -> None:
        """Tear the master down. Idempotent."""
        with contextlib.suppress(Exception):
            proc = await asyncio.create_subprocess_exec(
                "ssh", "-O", "exit",
                "-o", f"ControlPath={self.control_path}",
                self._spec.ssh,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(proc.wait(), timeout=5)
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            if self._proc.returncode is None:
                self._proc.kill()
        self._proc = None
        self._remote_port = None
