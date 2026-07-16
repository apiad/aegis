"""Async context manager wrapping `ssh -L` for the --remote ssh:// path.

Bind a random local port; spawn `ssh -L <local>:localhost:<remote> -N <host>`;
probe until TCP connect succeeds; teardown terminates the subprocess.
Fail fast — no retry — so bad SSH configs surface immediately.
"""
from __future__ import annotations

import asyncio
import socket


class TunnelError(RuntimeError):
    """Raised when the SSH tunnel fails to become reachable within the probe timeout."""


class SSHTunnel:
    """Async context manager that wraps an `ssh -L` port-forward subprocess.

    Usage::

        async with SSHTunnel("vps", 8080) as tunnel:
            # tunnel.local_port is the local port forwarded to vps:8080
            connect_to("127.0.0.1", tunnel.local_port)

    The constructor only stores parameters; the subprocess is not started until
    ``__aenter__``. ``__aexit__`` terminates (and if needed kills) the subprocess.
    """

    def __init__(
        self,
        host: str,
        remote_port: int,
        *,
        probe_timeout_s: float = 10.0,
    ) -> None:
        self.host = host
        self.remote_port = remote_port
        self.probe_timeout_s = probe_timeout_s
        self.local_port: int = 0
        self._proc: asyncio.subprocess.Process | None = None

    async def __aenter__(self) -> "SSHTunnel":
        # Reserve an ephemeral local port then release it — ssh will re-bind.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            self.local_port = s.getsockname()[1]

        argv = (
            "ssh",
            "-L", f"{self.local_port}:localhost:{self.remote_port}",
            "-N", self.host,
        )
        self._proc = await asyncio.create_subprocess_exec(*argv)
        await self._probe()
        return self

    async def _probe(self) -> None:
        """Poll 127.0.0.1:<local_port> every 100 ms until reachable or timeout."""
        deadline = asyncio.get_event_loop().time() + self.probe_timeout_s
        while asyncio.get_event_loop().time() < deadline:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", self.local_port),
                    timeout=0.5,
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(0.1)
        raise TunnelError(
            f"ssh tunnel to {self.host}:{self.remote_port} did not become "
            f"reachable on 127.0.0.1:{self.local_port} within "
            f"{self.probe_timeout_s}s"
        )

    async def __aexit__(self, *exc) -> None:
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
