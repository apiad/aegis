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
