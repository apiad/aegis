"""Turning spawn arguments + profile defaults + config into a Place.

Pure: no I/O, no connection, no side effects. The precedence rules are
the whole content of this module and they are tested exhaustively.
"""
from __future__ import annotations

from aegis.hosts.errors import HostError
from aegis.hosts.models import HostSpec, Place


def resolve_place(*, host: str | None, cwd: str | None,
                  agent_host: str | None,
                  hosts: dict[str, HostSpec],
                  local_root: str) -> Place:
    """Resolve where a session's harness will run.

    host: explicit spawn argument > agent profile default > "local".
    cwd:  explicit spawn argument > that host's cwd > local_root.
    """
    name = host or agent_host or "local"
    if name == "local":
        return Place("local", cwd or local_root)
    spec = hosts.get(name)
    if spec is None:
        known = sorted(hosts) + ["local"]
        raise HostError(f"unknown host {name!r}; known: {known}")
    return Place(name, cwd or spec.cwd)


def parse_at_host(token: str) -> tuple[str, str | None, str | None]:
    """Split ``agent@host:/cwd`` into its three parts.

    ``agent`` alone, ``agent@host``, and ``agent@host:/path`` are all
    valid. Only the FIRST colon after the host separates the cwd, so a
    path containing colons survives intact.
    """
    agent, sep, rest = token.partition("@")
    if not sep or not rest:
        return agent, None, None
    host, csep, cwd = rest.partition(":")
    if not host:
        return agent, None, None
    return agent, host, (cwd if csep and cwd else None)
