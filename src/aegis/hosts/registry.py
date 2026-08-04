"""One HostConnection per host, and the launcher that rides it.

``launcher_for`` must stay SYNCHRONOUS: ``SessionManager._sync_spawn``
is sync, and opening an SSH master is not. So the registry hands back an
``SshLauncher`` bound to a not-yet-open connection; the connection is
established inside ``SshLauncher.spawn``, which is already async. That
also puts connection errors where they belong — failing the session that
asked for the host, in a pane that exists to show the message.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from aegis.hosts.connection import HostConnection
from aegis.hosts.errors import HostError
from aegis.hosts.launcher import Launcher, LocalLauncher, SshLauncher
from aegis.hosts.models import HostSpec, Place

# Returned as the mcp_url for a remote place. The real URL is not known
# until sshd allocates the reverse-forward port, so drivers must ask the
# connection at spawn time rather than baking a URL into argv early.
DEFERRED_URL = ""


class HostRegistry:
    def __init__(self, hosts: dict[str, HostSpec], state_dir: Path,
                 local_root: str) -> None:
        self._hosts = dict(hosts)
        self._state_dir = Path(state_dir)
        self._local_root = local_root
        self._conns: dict[str, HostConnection] = {}
        self._mcp_port: int | None = None

    def set_mcp_port(self, port: int) -> None:
        """Called once, after AegisMCP has bound its port and before the
        first spawn."""
        self._mcp_port = port

    def known(self) -> list[str]:
        return sorted(self._hosts)

    def spec(self, name: str) -> HostSpec:
        spec = self._hosts.get(name)
        if spec is None:
            raise HostError(
                f"unknown host {name!r}; known: "
                f"{sorted(self._hosts) + ['local']}")
        return spec

    def _connection(self, spec: HostSpec) -> HostConnection:
        conn = self._conns.get(spec.name)
        if conn is None:
            if self._mcp_port is None:
                raise HostError(
                    "host registry has no MCP port yet — call "
                    "set_mcp_port() before spawning a remote session.")
            conn = HostConnection(
                spec,
                control_path=str(
                    self._state_dir / "ssh" / f"{spec.name}.sock"),
                mcp_port=self._mcp_port)
            self._conns[spec.name] = conn
        return conn

    def launcher_for(self, place: Place,
                     mcp_url: str) -> tuple[Launcher, str]:
        """The launcher for a place, plus the MCP URL its harness should
        be told about. Synchronous by contract."""
        if place.is_local:
            return LocalLauncher(local_root=self._local_root), mcp_url
        spec = self.spec(place.host)
        conn = self._connection(spec)
        return (SshLauncher(conn, spec, local_root=self._local_root),
                DEFERRED_URL)

    async def close_all(self) -> None:
        """Tear down every master. Called on aegis quit."""
        await asyncio.gather(
            *(c.close() for c in self._conns.values()),
            return_exceptions=True)
        self._conns.clear()
